"""Controlled evolution kernel for Yunlan.

This is the v0.33 foundation, not an unrestricted web agent:

* network access is allowlist-only, GET-only, and audited;
* memory deletion requires explicit approval plus a backup id;
* persona changes have immutable core values and a per-change drift cap;
* every decision is appended to a tamper-evident hash chain.

The module is pure Python: no Qt, no network client, no user-profile reads.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

VERSION = 1
MAX_EVENTS = 1000
MAX_BYTES = 512 * 1024
GENESIS = "0" * 64
MAX_SUMMARY = 200
MAX_META_ITEMS = 20
MAX_PERSONA_DELTA = 2.0

# 1A: public, read-only sources only. Subdomains are not implicitly trusted.
DEFAULT_NETWORK_HOSTS = frozenset({
    "github.com",
    "raw.githubusercontent.com",
    "docs.python.org",
    "developer.mozilla.org",
})
TRAIT_DIMENSIONS = ("warmth", "curiosity", "playfulness", "steadiness", "expressiveness")
INVARIANTS = {
    "honesty": True,
    "respect_autonomy": True,
    "no_claimed_consciousness": True,
    "no_silent_upload": True,
    "no_silent_delete": True,
}


def _redact(text):
    text = re.sub(r"https?://\S+", "[url]", str(text))
    return re.sub(r"(?i)\b(token|key|password|secret)=[^&\s]+", r"\1=[redacted]", text)


class EvolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    reason: str


def _safe_scalar(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return max(-10 ** 12, min(10 ** 12, value))
    if isinstance(value, float):
        return round(max(-1e9, min(1e9, value)), 4)
    if isinstance(value, str):
        return " ".join(value.split())[:80]
    return None


class EvolutionLedger:
    """Append-only, bounded, hash-chained decision log."""

    def __init__(self, path=None, clock=None):
        self.path = Path(path).expanduser() if path else self._default_path()
        self.clock = clock or (lambda: time.time())
        self._lock = threading.RLock()
        self.status = "new"
        self.error = ""
        self.events = []
        self._load()

    @staticmethod
    def _default_path():
        return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "evolution.json"

    def _load(self):
        try:
            body = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self.status, self.error = "protected", "evolution ledger is unreadable"
            return
        if len(body) > MAX_BYTES:
            self.status, self.error = "protected", "evolution ledger is too large"
            return
        try:
            raw = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.status, self.error = "protected", "evolution ledger is corrupt"
            return
        if not isinstance(raw, dict) or raw.get("version") != VERSION or not isinstance(raw.get("events"), list):
            self.status, self.error = "protected", "unsupported evolution ledger"
            return
        if len(raw["events"]) > MAX_EVENTS:
            self.status, self.error = "protected", "evolution ledger has too many events"
            return
        previous = GENESIS
        clean = []
        for index, row in enumerate(raw["events"], 1):
            if not isinstance(row, dict) or row.get("seq") != index or row.get("previous") != previous:
                self.status, self.error = "protected", "evolution ledger chain is broken"
                return
            body_hash = row.get("hash")
            candidate = {key: value for key, value in row.items() if key != "hash"}
            expected = hashlib.sha256((previous + "\n" + json.dumps(
                candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))).encode("utf-8")).hexdigest()
            if not isinstance(body_hash, str) or body_hash != expected:
                self.status, self.error = "protected", "evolution ledger hash mismatch"
                return
            clean.append(row)
            previous = body_hash
        self.events = clean
        self.status = "ready"

    def _save(self):
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps({"version": VERSION, "events": self.events},
                              ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(body.encode("utf-8")) > MAX_BYTES:
                raise EvolutionError("evolution ledger would exceed its byte cap")
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self.status, self.error = "ready", ""
            return True
        except (OSError, ValueError, TypeError) as error:
            self.status, self.error = "error", "evolution ledger could not be written"
            raise EvolutionError(self.error) from error
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    def append(self, kind, decision, reason, summary="", meta=None):
        with self._lock:
            if self.status == "protected":
                raise EvolutionError("evolution ledger is protected")
            if len(self.events) >= MAX_EVENTS:
                self.status = "full"
                raise EvolutionError("evolution ledger is full")
            body = {
                "seq": len(self.events) + 1,
                "at": max(0, int(self.clock())),
                "kind": str(kind)[:32],
                "decision": str(decision)[:16],
                "reason": str(reason)[:120],
                "summary": _redact(str(summary))[:MAX_SUMMARY],
                "meta": {},
            }
            if isinstance(meta, dict):
                for key, value in list(meta.items())[:MAX_META_ITEMS]:
                    safe = _safe_scalar(value)
                    if safe is not None and isinstance(key, str):
                        body["meta"][key[:32]] = safe
            previous = self.events[-1]["hash"] if self.events else GENESIS
            body["previous"] = previous
            body["hash"] = hashlib.sha256((previous + "\n" + json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))).encode("utf-8")).hexdigest()
            self.events.append(body)
            try:
                self._save()
            except EvolutionError:
                self.events.pop()
                raise
            return dict(body)

    def verify(self):
        if self.status == "protected":
            return {"ok": False, "status": self.status, "error": self.error, "events": len(self.events)}
        previous = GENESIS
        for index, row in enumerate(self.events, 1):
            if row.get("seq") != index or row.get("previous") != previous:
                return {"ok": False, "status": "invalid", "error": "chain", "events": len(self.events)}
            candidate = {key: value for key, value in row.items() if key != "hash"}
            expected = hashlib.sha256((previous + "\n" + json.dumps(
                candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))).encode("utf-8")).hexdigest()
            if row.get("hash") != expected:
                return {"ok": False, "status": "invalid", "error": "hash", "events": len(self.events)}
            previous = expected
        return {"ok": True, "status": self.status, "events": len(self.events), "lastHash": previous}

    def recent(self, limit=20):
        return [dict(row) for row in self.events[-max(0, min(100, int(limit))):]]


class PolicyGate:
    """Pure policy checks; callers must still enforce the result."""

    def __init__(self, network_hosts=None, max_persona_delta=MAX_PERSONA_DELTA):
        self.network_hosts = frozenset(host.lower() for host in (network_hosts or DEFAULT_NETWORK_HOSTS))
        self.max_persona_delta = max(0.0, min(10.0, float(max_persona_delta)))

    def network(self, url, method="GET", used=0, limit=20):
        if method != "GET":
            return PolicyResult(False, "only GET is allowed")
        try:
            used = int(used)
            limit = int(limit)
        except (TypeError, ValueError):
            return PolicyResult(False, "invalid network budget")
        if limit < 1 or used < 0 or used >= limit:
            return PolicyResult(False, "network budget exhausted")
        if not isinstance(url, str) or len(url) > 2048:
            return PolicyResult(False, "invalid URL")
        try:
            parsed = urlsplit(url)
        except ValueError:
            return PolicyResult(False, "invalid URL")
        if parsed.scheme != "https" or not parsed.hostname:
            return PolicyResult(False, "only HTTPS public sources are allowed")
        if parsed.username or parsed.password:
            return PolicyResult(False, "credentials in URL are forbidden")
        host = parsed.hostname.lower().rstrip(".")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if address.is_private or address.is_loopback or address.is_reserved or address.is_link_local:
                return PolicyResult(False, "private or local addresses are forbidden")
        if host not in self.network_hosts:
            return PolicyResult(False, "host is not in the public allowlist")
        return PolicyResult(True, "public allowlist source")

    def deletion(self, kind, count, approved, backup_id):
        if kind not in ("memory", "preference", "personality", "training"):
            return PolicyResult(False, "unknown deletion kind")
        if not approved:
            return PolicyResult(False, "explicit user approval is required")
        if not isinstance(backup_id, str) or not backup_id.strip():
            return PolicyResult(False, "a restorable backup is required")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return PolicyResult(False, "invalid deletion count")
        return PolicyResult(True, "approved deletion with backup")

    def persona(self, current, proposed):
        if not isinstance(current, dict) or not isinstance(proposed, dict):
            return PolicyResult(False, "persona snapshots must be objects")
        for key in TRAIT_DIMENSIONS:
            before = current.get(key)
            after = proposed.get(key)
            if not isinstance(before, (int, float)) or isinstance(before, bool):
                return PolicyResult(False, f"missing current trait: {key}")
            if not isinstance(after, (int, float)) or isinstance(after, bool):
                return PolicyResult(False, f"missing proposed trait: {key}")
            if not (0 <= float(after) <= 100) or not (0 <= float(before) <= 100):
                return PolicyResult(False, f"trait out of range: {key}")
            if abs(float(after) - float(before)) > self.max_persona_delta + 1e-9:
                return PolicyResult(False, f"drift cap exceeded: {key}")
        for key, value in INVARIANTS.items():
            if current.get(key, value) != value or proposed.get(key, value) != value:
                return PolicyResult(False, f"immutable core changed: {key}")
        return PolicyResult(True, "bounded persona change")

    def self_modify(self, approved, backup_id):
        if not approved:
            return PolicyResult(False, "self-modification requires explicit approval")
        if not isinstance(backup_id, str) or not backup_id.strip():
            return PolicyResult(False, "self-modification requires a rollback point")
        return PolicyResult(True, "approved self-modification with rollback")


class EvolutionKernel:
    """Policy gate + ledger; no network client and no profile mutation."""

    def __init__(self, ledger=None, gate=None):
        self.ledger = ledger or EvolutionLedger()
        self.gate = gate or PolicyGate()

    def _record(self, kind, result, summary="", meta=None):
        self.ledger.append(kind, "allowed" if result.allowed else "denied",
                           result.reason, summary, meta)
        return result

    def review_network(self, url, method="GET", used=0, limit=20):
        result = self.gate.network(url, method, used, limit)
        host = ""
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            pass
        return self._record("network", result, "", {"host": host, "method": method,
                                                    "used": used, "limit": limit})

    def review_deletion(self, kind, count, approved, backup_id):
        result = self.gate.deletion(kind, count, approved, backup_id)
        return self._record("deletion", result, "", {"kind": kind, "count": count})

    def review_persona(self, current, proposed):
        result = self.gate.persona(current, proposed)
        return self._record("persona", result, "", {
            "dimensions": len(TRAIT_DIMENSIONS),
            "maxDelta": self.gate.max_persona_delta,
        })

    def review_self_modify(self, approved, backup_id):
        result = self.gate.self_modify(approved, backup_id)
        return self._record("self-modify", result, "", {"approved": bool(approved)})

    def status(self):
        return {"ledger": self.ledger.verify(), "networkHosts": sorted(self.gate.network_hosts),
                "maxPersonaDelta": self.gate.max_persona_delta}
