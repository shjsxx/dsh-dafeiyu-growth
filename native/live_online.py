"""Opt-in public USGS numeric facts, not a chat/cloud/training service.

Only ``fetch_live_facts`` performs networking: one fixed, credential-free HTTPS
GET, no redirects, cookies, proxies, payload-selected URLs or executable data.
Run it in the application's background worker, never on a Qt event thread.
``LiveOnlineState`` performs local atomic persistence only, and reserves a
persisted polling slot *before* a worker starts. Call ``is_current`` before
applying a result and pass the same attempt_id to ``finish`` after audited apply.
No response prose, event identifiers, places, titles or URLs are returned/stored.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import math
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "usgs_live"
SOURCE_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_hour.geojson"
QUESTION = "最近一小时公开地震监测是什么？"
MAX_BYTES = 256 * 1024
MAX_FEATURES = 200
MAX_STATE_BYTES = 16 * 1024
MAX_STAMP = 32_503_680_000.0
MAX_MS = MAX_STAMP * 1000
INTERVAL = 300
MANUAL_INTERVAL = 60
DAY = 86_400
BACKOFF = (300, 900, 3600)
# At most one transport may outlive a deadline (e.g. host DNS stuck). Calls
# during that period fail closed instead of spawning more network workers.
_TRANSPORT_LOCK = threading.Lock()
ERRORS = {
    "network": "暂时无法连接公开地震监测源；保留上次结果。",
    "schema": "公开地震数据格式或时间校验未通过；保留上次结果。",
    "size": "公开地震数据超过 256 KB 上限；已停止读取。",
    "rate_limit": "公开接口要求冷却；到期前不会再次请求。",
    "storage": "本地审计或保存未成功；本次未记为更新成功。",
    "cancelled": "本次公开监测更新已取消。",
}


class _Failure(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _number(value, lower=0, upper=MAX_MS):
    # Check ranges *before* converting: math.isfinite(10**10000) overflows.
    if type(value) not in (int, float) or not lower <= value <= upper:
        raise ValueError("number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("number")
    return result


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("integer")
    return value


def _utc(milliseconds):
    value = _number(milliseconds)
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean_counters(raw):
    if type(raw) is not dict:
        raise ValueError("counters")
    count = _integer(raw.get("count"), 0, MAX_FEATURES)
    rejected = _integer(raw.get("rejected", 0), 0, MAX_FEATURES)
    reviewed = _integer(raw.get("reviewedCount"), 0, count)
    if count + rejected > MAX_FEATURES or (not count and rejected):
        raise ValueError("counter total")
    if count:
        magnitude = _number(raw.get("maxMagnitude"), 2.5, 10)
        latest = _number(raw.get("latestEventTimeMs"))
    else:
        if raw.get("maxMagnitude") is not None or raw.get("latestEventTimeMs") is not None:
            raise ValueError("empty counters")
        magnitude = latest = None
    return {"count": count, "maxMagnitude": magnitude, "latestEventTimeMs": latest,
            "reviewedCount": reviewed, "rejected": rejected}


def make_summary(counters, source_updated_at_ms):
    """Regenerate all visible prose from bounded numbers, never downloaded text."""
    clean = _clean_counters(counters)
    generated = _number(source_updated_at_ms)
    dated = _utc(generated)
    count = clean["count"]
    if count and not max(0, generated - 3_600_000) <= clean["latestEventTimeMs"] <= generated:
        raise ValueError("summary event time")
    prefix = f"USGS 最近一小时公开地震源，源更新时间 {dated}（UTC）。"
    if count:
        facts = (f"通过校验的 2.5 级及以上记录共 {count} 条，最大震级 {clean['maxMagnitude']:.2f}；"
                 f"最新事件时间 {_utc(clean['latestEventTimeMs'])}（UTC），已人工复核 {clean['reviewedCount']} 条。")
    else:
        facts = "通过校验的 2.5 级及以上记录为 0 条，无最大震级或最新事件时间，已人工复核 0 条。"
    rejected = f"另有 {clean['rejected']} 条未通过校验，未计入汇总。" if clean["rejected"] else ""
    return prefix + facts + rejected + "统计窗口为源更新时间前 1 小时；这是监测汇总，不是预测或实时安全警报。"


def _record(counters, generated):
    return {"id": "usgs_live:summary", "key": "summary", "question": QUESTION,
            "answer": make_summary(counters, generated), "domain": "geography",
            "intent": "concept", "topic": "公开地震监测", "aliases": ["公开地震监测", "最近一小时地震", "地震监测"],
            "reference": "", "source": SOURCE, "sourceUrl": SOURCE_URL}


def _json_integer(value):
    if len(value.lstrip("-")) > 16:
        raise ValueError("integer bound")
    return int(value)


def _json_float(value):
    number = float(value)
    if not math.isfinite(number) or abs(number) > MAX_MS:
        raise ValueError("float bound")
    return number


def _json_constant(_value):
    raise ValueError("nonfinite")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _loads(body):
    return json.loads(body.decode("utf-8"), parse_int=_json_integer, parse_float=_json_float,
                      parse_constant=_json_constant, object_pairs_hook=_unique_object)


def _validate_feed(raw, now, metrics):
    if type(raw) is not dict or raw.get("type") != "FeatureCollection":
        raise ValueError("collection")
    metadata = raw.get("metadata")
    if type(metadata) is not dict:
        raise ValueError("metadata")
    generated = _number(metadata.get("generated"))
    now_ms = _number(now, 0, MAX_STAMP) * 1000
    if not now_ms - 600_000 <= generated <= now_ms + 300_000:
        raise ValueError("source freshness")
    features = raw.get("features")
    if type(features) is not list or len(features) > MAX_FEATURES:
        raise ValueError("features")
    magnitudes, event_times = [], []
    reviewed = 0
    for feature in features:
        try:
            if type(feature) is not dict or feature.get("type") != "Feature":
                raise ValueError("feature")
            properties, geometry = feature.get("properties"), feature.get("geometry")
            if type(properties) is not dict or type(geometry) is not dict or geometry.get("type") != "Point":
                raise ValueError("point")
            magnitude = _number(properties.get("mag"), 2.5, 10)
            event_time = _number(properties.get("time"), max(0, generated - 3_600_000), min(now_ms, generated))
            status = properties.get("status")
            if "status" in properties and status not in ("reviewed", "automatic"):
                raise ValueError("status")
            coordinates = geometry.get("coordinates")
            if type(coordinates) is not list or len(coordinates) not in (2, 3):
                raise ValueError("coordinates")
            _number(coordinates[0], -180, 180)
            _number(coordinates[1], -90, 90)
            if len(coordinates) == 3:
                _number(coordinates[2], -1_000_000, 1_000_000)
            magnitudes.append(magnitude)
            event_times.append(event_time)
            reviewed += status == "reviewed"
        except (ValueError, TypeError, OverflowError):
            metrics["rejected"] += 1
    metrics["validated"] = len(magnitudes)
    # A genuinely empty feed is successful. An entirely invalid nonempty feed
    # must not be misrepresented as an official zero-event observation.
    if features and not magnitudes:
        raise ValueError("no valid features")
    counters = {"count": len(magnitudes), "maxMagnitude": max(magnitudes, default=None),
                "latestEventTimeMs": max(event_times, default=None), "reviewedCount": reviewed,
                "rejected": metrics["rejected"]}
    return counters, generated


def _header(headers, name):
    values = headers.get_all(name) if hasattr(headers, "get_all") else None
    if values is not None and len(values) != 1:
        raise _Failure("schema")
    value = values[0] if values else headers.get(name)
    if value is not None and (not isinstance(value, str) or len(value) > 256):
        raise _Failure("schema")
    return value


def _retry_after(headers):
    value = headers.get("Retry-After") if headers is not None else None
    if not isinstance(value, str):
        return INTERVAL
    value = value.strip()
    if not value or not value.isascii() or not value.isdecimal():
        return INTERVAL
    # Do not parse arbitrarily large integers, including hostile HTTP headers.
    value = value.lstrip("0") or "0"
    return DAY if len(value) > 5 else max(INTERVAL, min(DAY, int(value)))


def _download(timeout, started, metrics, cancelled=None):
    if cancelled is not None and cancelled.is_set():
        raise TimeoutError()
    request = urllib.request.Request(SOURCE_URL, method="GET", headers={
        "Accept": "application/geo+json, application/json", "Accept-Encoding": "identity",
        "User-Agent": "Yunlan/0.4 (public numeric USGS monitor; no private payload)",
    })
    # A fresh opener has no auth/cookie handlers. Ignore credential-bearing
    # environment proxies rather than transmitting local configuration.
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        if cancelled is not None and cancelled.is_set():
            raise TimeoutError()
        if response.getcode() != 200 or response.geturl() != SOURCE_URL:
            raise _Failure("network")
        headers = response.headers
        media = (_header(headers, "Content-Type") or "").split(";", 1)[0].strip().lower()
        if media not in ("application/json", "application/geo+json", "application/geojson"):
            raise _Failure("schema")
        encoding = (_header(headers, "Content-Encoding") or "identity").strip().lower()
        transfer = (_header(headers, "Transfer-Encoding") or "").strip().lower()
        if encoding != "identity" or transfer not in ("", "chunked"):
            raise _Failure("schema")
        length = _header(headers, "Content-Length")
        expected = None
        if length is not None:
            if transfer or not length.isascii() or not length.isdecimal():
                raise _Failure("schema")
            length = length.lstrip("0") or "0"
            if len(length) > 6 or int(length) > MAX_BYTES:
                raise _Failure("size")
            expected = int(length)
        chunks = []
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0 or (cancelled is not None and cancelled.is_set()):
                raise TimeoutError()
            # urllib's HTTPS response exposes the connected socket here. Tighten
            # every read to the remaining request budget, not a fresh timeout.
            stream = getattr(getattr(response, "fp", None), "raw", None)
            connection = getattr(stream, "_sock", None)
            if connection is not None:
                connection.settimeout(remaining)
            reader = getattr(response, "read1", response.read)
            chunk = reader(min(16 * 1024, MAX_BYTES + 1 - metrics["bytes"]))
            if not isinstance(chunk, bytes):
                raise _Failure("schema")
            metrics["bytes"] += len(chunk)
            if metrics["bytes"] > MAX_BYTES:
                raise _Failure("size")
            if not chunk:
                break
            chunks.append(chunk)
        if expected is not None and metrics["bytes"] != expected:
            raise _Failure("schema")
        return b"".join(chunks)


def _deadline_download(timeout, started, metrics):
    """Bound caller latency even when the OS resolver ignores socket timeout.

    Python cannot interrupt getaddrinfo safely. A single daemon transport is
    therefore allowed to wind down after cancellation; no further transport can
    start until it ends. It discards its response and never applies local state.
    """
    if not _TRANSPORT_LOCK.acquire(blocking=False):
        raise _Failure("network")
    ready, cancelled = threading.Event(), threading.Event()
    outcome, worker_metrics = {}, {"bytes": 0}

    def run():
        try:
            outcome["body"] = _download(timeout, started, worker_metrics, cancelled)
        except Exception as failure:
            # Close HTTP error bodies even if the caller already timed out;
            # code/headers remain usable without retaining a live connection.
            if isinstance(failure, urllib.error.HTTPError):
                failure.close()
            outcome["failure"] = failure
        finally:
            _TRANSPORT_LOCK.release()
            ready.set()

    worker = threading.Thread(target=run, name="usgs-public-facts", daemon=True)
    try:
        worker.start()
    except RuntimeError:
        _TRANSPORT_LOCK.release()
        raise _Failure("network") from None
    if not ready.wait(max(0, timeout - (time.monotonic() - started))):
        cancelled.set()
        metrics["bytes"] = worker_metrics["bytes"]
        raise TimeoutError()
    metrics["bytes"] = worker_metrics["bytes"]
    if "failure" in outcome:
        raise outcome["failure"]
    return outcome["body"]


def fetch_live_facts(timeout=12):
    """One bounded public GET; no local file I/O, input prose or raw error text.

    ``timeout`` is a numeric budget clamped to 0.1..15 seconds; HTTPS reads
    use its remaining deadline. A guarded daemon transport also bounds caller
    waiting if host DNS ignores socket timeouts; late results are discarded.
    ``errorCode`` on failures is exactly network/schema/size/rate_limit.
    """
    started = time.monotonic()
    metrics = {"bytes": 0, "validated": 0, "rejected": 0, "duration_ms": 0}
    result = {"ok": False, "source": SOURCE, "sourceUrl": SOURCE_URL, "records": [],
              "metrics": metrics, "fetchedAt": "", "fetchedAtMs": None,
              "sourceUpdatedAt": "", "sourceUpdatedAtMs": None, "summary": "",
              "errorCode": "", "retry_after": 0}
    try:
        budget = min(15.0, max(0.1, _number(timeout, 0, MAX_STAMP)))
        body = _deadline_download(budget, started, metrics)
        now = time.time()
        counters, generated = _validate_feed(_loads(body), now, metrics)
        if time.monotonic() - started > budget:
            raise TimeoutError()
        record = _record(counters, generated)
        result.update(ok=True, records=[record], counters=counters, sourceUpdatedAt=_utc(generated),
                      sourceUpdatedAtMs=generated, summary=record["answer"],
                      hash=hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                                   separators=(",", ":")).encode("utf-8")).hexdigest())
    except urllib.error.HTTPError as failure:
        try:
            result["errorCode"] = "rate_limit" if failure.code in (429, 503) else "network"
            if result["errorCode"] == "rate_limit":
                result["retry_after"] = _retry_after(failure.headers)
        finally:
            failure.close()
    except _Failure as failure:
        result["errorCode"] = failure.code
    except (urllib.error.URLError, TimeoutError, OSError, socket.timeout, http.client.HTTPException):
        result["errorCode"] = "network"
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        result["errorCode"] = "schema"
    finished = time.time()
    result["fetchedAtMs"] = _number(finished, 0, MAX_STAMP) * 1000
    result["fetchedAt"] = _utc(result["fetchedAtMs"])
    metrics["duration_ms"] = round(max(0, (time.monotonic() - started) * 1000), 3)
    if not result["ok"]:
        result["error"] = ERRORS[result["errorCode"]]
    return result


def default_path():
    explicit = os.environ.get("DSH_DAFEIYU_ONLINE_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "online.json"
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "online.json"


class LiveOnlineState:
    """Single-process, thread-safe scheduler; persistence failures fail closed.

    New instances are disabled. Enabling requires ``owner_action=True`` from an
    explicit owner UI action, not model text. Manual requests still require opt-in
    and obey the persisted 60-second floor and server cooldown. Automatic retries
    use 300/900/3600 seconds; successful polling uses 300 seconds.
    """
    def __init__(self, path=None, clock=None):
        self.path = Path(path) if path is not None else default_path()
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._active = None
        self._issued = {}
        self._disk_digest = None
        self.protected = False
        self.enabled = False
        self.last_attempt = self.last_success = self.next_check = self.retry_until = 0.0
        self.failures = 0
        self.counters = None
        self.source_updated_at_ms = None
        self.error_code = ""
        try:
            content = self._disk_bytes()
            if content is not None:
                self._disk_digest = hashlib.sha256(content).digest()
                raw = _loads(content)
                if type(raw) is not dict or type(raw.get("version")) is not int or raw["version"] != 1 or type(raw.get("enabled")) is not bool:
                    raise ValueError("state")
                stamps = [_number(raw.get(name), 0, MAX_STAMP) for name in
                          ("lastAttempt", "lastSuccess", "nextCheck", "retryUntil")]
                failures = _integer(raw.get("failures"), 0, 1_000_000)
                code = raw.get("errorCode", "")
                if not isinstance(code, str) or code not in ("", *ERRORS):
                    raise ValueError("error enum")
                counters, updated = raw.get("counters"), raw.get("sourceUpdatedAtMs")
                if counters is not None:
                    counters, updated = _clean_counters(counters), _number(updated)
                    make_summary(counters, updated)
                elif updated is not None:
                    raise ValueError("source stamp")
                self.enabled = raw["enabled"]
                self.last_attempt, self.last_success, self.next_check, self.retry_until = stamps
                self.failures, self.error_code = failures, code
                self.counters, self.source_updated_at_ms = counters, updated
        except (OSError, ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
            self.enabled = False
            self.protected = True

    def _disk_bytes(self):
        try:
            with self.path.open("rb") as handle:
                content = handle.read(MAX_STATE_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(content) > MAX_STATE_BYTES:
            raise ValueError("state size")
        return content

    def _raw(self):
        return {"version": 1, "enabled": self.enabled, "lastAttempt": self.last_attempt,
                "lastSuccess": self.last_success, "nextCheck": self.next_check,
                "retryUntil": self.retry_until, "failures": self.failures,
                "sourceUpdatedAtMs": self.source_updated_at_ms, "counters": self.counters,
                "summary": make_summary(self.counters, self.source_updated_at_ms) if self.counters is not None else "",
                "errorCode": self.error_code}

    def _persist(self):
        if self.protected:
            return False
        temporary = None
        try:
            current = self._disk_bytes()
            digest = hashlib.sha256(current).digest() if current is not None else None
            if digest != self._disk_digest:
                self.protected, self.enabled, self._active = True, False, None
                return False
            content = json.dumps(self._raw(), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(content) > MAX_STATE_BYTES:
                raise ValueError("state size")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self._disk_digest = hashlib.sha256(content).digest()
            return True
        except (OSError, ValueError, TypeError, OverflowError):
            self.error_code = "storage"
            # Do not permit network activity on state whose cooldown cannot be
            # durably reserved. Disable in memory even if the old disk was on.
            self.enabled, self._active = False, None
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def set_enabled(self, enabled, *, owner_action=False):
        with self._lock:
            if type(enabled) is not bool or self.protected or (enabled and owner_action is not True):
                return False
            self.enabled = enabled
            if not enabled:
                self._active = None
            return self._persist()

    def is_current(self, attempt_id):
        with self._lock:
            return (self.enabled and not self.protected and isinstance(attempt_id, str)
                    and self._active is not None and attempt_id == self._active)

    def is_due(self, now=None):
        with self._lock:
            now = _number(self._clock() if now is None else now, 0, MAX_STAMP)
            return (self.enabled and not self.protected and self._active is None
                    and now >= max(self.next_check, self.retry_until)
                    and (not (self.last_attempt or self.next_check) or now >= self.last_attempt + MANUAL_INTERVAL))

    def begin(self, manual=False, now=None):
        with self._lock:
            now = _number(self._clock() if now is None else now, 0, MAX_STAMP)
            reason = ("protected" if self.protected else "disabled" if not self.enabled else
                      "working" if self._active is not None else "cooldown" if now < self.retry_until else
                      "interval" if ((self.last_attempt or self.next_check) and now < self.last_attempt + MANUAL_INTERVAL)
                      or (not manual and now < self.next_check) else "")
            if reason:
                return {"ok": False, "reason": reason, "attempt_id": None, "snapshot": self.snapshot(now)}
            self.last_attempt, self.next_check = now, max(now + INTERVAL, self.retry_until)
            self.error_code = ""
            self._active = uuid.uuid4().hex
            if not self._persist():
                return {"ok": False, "reason": "storage", "attempt_id": None, "snapshot": self.snapshot(now)}
            self._issued[self._active] = True
            if len(self._issued) > 16:
                del self._issued[next(iter(self._issued))]
            return {"ok": True, "reason": "", "attempt_id": self._active, "snapshot": self.snapshot(now)}

    def cancel(self):
        with self._lock:
            self._active = None
            # Keep reservations and server cooldown; cancellation cannot create
            # a rapid restart/request loop.
            return self.snapshot()

    def _honor_retry(self, result, now):
        try:
            retry = _number(result.get("retry_after", INTERVAL), 0, MAX_STAMP)
        except (ValueError, TypeError, OverflowError):
            retry = INTERVAL
        self.retry_until = max(self.retry_until, now + max(INTERVAL, min(DAY, retry)))
        self.next_check = max(self.next_check, self.retry_until)

    def finish(self, result, attempt_id=None, now=None):
        """Finish every issued worker, even cancelled ones, to honor HTTP cooldown.

        Cancelled generations can only extend a known server cooldown; they can
        never apply facts, set lastSuccess, enable polling, or finish a new worker.
        """
        with self._lock:
            now = _number(self._clock() if now is None else now, 0, MAX_STAMP)
            if not self.is_current(attempt_id):
                known = isinstance(attempt_id, str) and self._issued.pop(attempt_id, None) is not None
                if (known and not self.protected and type(result) is dict
                        and result.get("ok") is False and result.get("errorCode") == "rate_limit"):
                    self._honor_retry(result, now)
                    self.error_code = "rate_limit"
                    self._persist()
                return {"ok": False, "reason": "cancelled", "snapshot": self.snapshot(now)}
            self._issued.pop(attempt_id, None)
            success = type(result) is dict and result.get("ok") is True
            counters = updated = None
            code = result.get("errorCode") if type(result) is dict else "schema"
            if success:
                try:
                    counters = _clean_counters(result.get("counters"))
                    updated = _number(result.get("sourceUpdatedAtMs"))
                    if not now * 1000 - 600_000 <= updated <= now * 1000 + 300_000:
                        raise ValueError("freshness")
                    if counters["count"]:
                        _number(counters["latestEventTimeMs"], max(0, updated - 3_600_000), min(now * 1000, updated))
                    make_summary(counters, updated)
                except (ValueError, TypeError, OverflowError):
                    success, code = False, "schema"
            self._active = None
            previous_success = (self.last_success, self.counters, self.source_updated_at_ms)
            if success:
                self.last_success, self.failures, self.error_code = now, 0, ""
                self.counters, self.source_updated_at_ms = counters, updated
                self.next_check = max(now + INTERVAL, self.retry_until)
            else:
                self.failures = min(self.failures + 1, 1_000_000)
                self.error_code = code if isinstance(code, str) and code in ERRORS else "schema"
                if self.error_code == "rate_limit":
                    self._honor_retry(result, now)
                self.next_check = max(now + BACKOFF[min(self.failures - 1, len(BACKOFF) - 1)], self.retry_until)
            saved = self._persist()
            if not saved and success:
                self.last_success, self.counters, self.source_updated_at_ms = previous_success
            return {"ok": success and saved, "reason": "" if success and saved else self.error_code,
                    "snapshot": self.snapshot(now)}

    def snapshot(self, now=None):
        with self._lock:
            now = _number(self._clock() if now is None else now, 0, MAX_STAMP)
            status = ("protected" if self.protected else "disabled" if not self.enabled else
                      "working" if self._active is not None else "cooldown" if now < self.retry_until else
                      "error" if self.error_code else "ready")
            return {"source": SOURCE, "sourceUrl": SOURCE_URL, "enabled": self.enabled,
                    "working": self._active is not None, "protected": self.protected, "status": status,
                    "attempt_id": self._active, "lastAttempt": self.last_attempt, "lastSuccess": self.last_success,
                    "sourceUpdatedAtMs": self.source_updated_at_ms,
                    "sourceUpdatedAt": _utc(self.source_updated_at_ms) if self.source_updated_at_ms is not None else "",
                    "summary": make_summary(self.counters, self.source_updated_at_ms) if self.counters is not None else "",
                    "counters": copy.deepcopy(self.counters), "failures": self.failures, "nextCheck": self.next_check,
                    "retryUntil": self.retry_until, "errorCode": self.error_code,
                    "error": "公开监测设置损坏或已被外部修改；已停用并保留原文件。" if self.protected else ERRORS.get(self.error_code, "")}
