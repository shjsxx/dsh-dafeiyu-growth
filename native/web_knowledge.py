"""Bounded local web knowledge separate from private companion memory.

This store exists because the small private memory engine intentionally has a
low cap. Web pages are public-source learnings, not user memories, so they get
their own capacity, URL/content deduplication and low-value eviction.

No network here. Text is local-only and never becomes an instruction.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

VERSION = 1
DEFAULT_MAX_RECORDS = 2000
DEFAULT_MAX_BYTES = 32 * 1024 * 1024
MAX_TEXT = 4000
MAX_TITLE = 160
MAX_URL = 2048

TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "spm", "from", "ref"}


def default_path(data_dir=None):
    root = Path(data_dir).expanduser() if data_dir else (
        Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth")
    return root / "web-knowledge.json"


def canonical_url(url):
    """Normalize a public URL for duplicate detection, not for display."""
    raw = " ".join(str(url or "").split())[:MAX_URL]
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    host = parsed.hostname.lower().rstrip(".")
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() not in TRACKING]
    query.sort()
    netloc = host
    if parsed.port and parsed.port not in (80, 443):
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, path, urlencode(query), ""))


def content_hash(text):
    return hashlib.sha256((" ".join(str(text or "").split())).encode("utf-8")).hexdigest()


def _plain(value, limit):
    return " ".join(str(value or "").split())[:limit]


class WebKnowledgeStore:
    def __init__(self, path=None, data_dir=None, clock=None,
                 max_records=DEFAULT_MAX_RECORDS, max_bytes=DEFAULT_MAX_BYTES):
        self.path = Path(path) if path is not None else default_path(data_dir)
        self.clock = clock or time.time
        self.max_records = max(50, min(10000, int(max_records)))
        self.max_bytes = max(256 * 1024, min(64 * 1024 * 1024, int(max_bytes)))
        self.lock = threading.RLock()
        self.status = "new"
        self.error = ""
        self.data = {"version": VERSION, "revision": 0, "records": []}
        self._load()

    def _load(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self.status, self.error = "protected", "web knowledge file is unreadable"
            return
        if len(raw) > self.max_bytes:
            self.status, self.error = "protected", "web knowledge file is too large"
            return
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.status, self.error = "protected", "web knowledge file is corrupt"
            return
        rows = parsed.get("records") if isinstance(parsed, dict) else None
        if not isinstance(parsed, dict) or parsed.get("version") != VERSION or not isinstance(rows, list):
            self.status, self.error = "protected", "unsupported web knowledge file"
            return
        clean = []
        for row in rows[: self.max_records]:
            if not isinstance(row, dict):
                continue
            url = canonical_url(row.get("url"))
            text = _plain(row.get("text"), MAX_TEXT)
            if not url or not text:
                continue
            clean.append({
                "id": str(row.get("id") or hashlib.sha256(url.encode()).hexdigest())[:64],
                "url": url,
                "host": _plain(row.get("host"), 120),
                "title": _plain(row.get("title"), MAX_TITLE),
                "text": text,
                "summary": _plain(row.get("summary") or text, 600),
                "category": _plain(row.get("category"), 32),
                "fetchedAt": max(0, int(row.get("fetchedAt") or 0)),
                "quality": max(0, min(100, int(row.get("quality") or 50))),
                "hits": max(0, int(row.get("hits") or 0)),
                "contentHash": str(row.get("contentHash") or content_hash(text))[:64],
            })
        self.data = {"version": VERSION, "revision": max(0, int(parsed.get("revision") or 0)),
                     "records": clean}
        self.status = "ready"

    def _save(self):
        temporary = None
        try:
            body = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode("utf-8")
            if len(body) > self.max_bytes:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            with temporary.open("wb") as stream:
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
        except (OSError, ValueError, TypeError):
            self.status, self.error = "error", "web knowledge file could not be written"
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except (FileNotFoundError, OSError):
                    pass

    def has_url(self, url):
        key = canonical_url(url)
        return bool(key) and any(row["url"] == key for row in self.data["records"])

    def _quality(self, title, text, category=""):
        length = min(40, len(text) // 200)
        title_bonus = 10 if title else 0
        category_bonus = 5 if category else 0
        return max(1, min(100, 40 + length + title_bonus + category_bonus))

    def add(self, url, title="", text="", summary="", host="", category="",
            fetched_at=None, quality=None):
        """Add one page; duplicate URL/content is rejected without overwriting."""
        key = canonical_url(url)
        clean_text = _plain(text, MAX_TEXT)
        if not key or len(clean_text) < 20:
            return {"ok": False, "reason": "invalid_record"}
        digest = content_hash(clean_text)
        with self.lock:
            if self.status == "protected":
                return {"ok": False, "reason": "protected"}
            if any(row["url"] == key for row in self.data["records"]):
                return {"ok": False, "reason": "duplicate_url"}
            if any(row["contentHash"] == digest for row in self.data["records"]):
                return {"ok": False, "reason": "duplicate_content"}
            row = {
                "id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:32],
                "url": key,
                "host": _plain(host or urlsplit(key).hostname, 120),
                "title": _plain(title, MAX_TITLE),
                "text": clean_text,
                "summary": _plain(summary or clean_text, 600),
                "category": _plain(category, 32),
                "fetchedAt": max(0, int(fetched_at if fetched_at is not None else self.clock())),
                "quality": max(0, min(100, int(quality if quality is not None else
                                           self._quality(title, clean_text, category)))),
                "hits": 0,
                "contentHash": digest,
            }
            before = json.loads(json.dumps(self.data, ensure_ascii=False))
            # New knowledge is admitted; only the lowest-value *old* page is evicted.
            if len(self.data["records"]) >= self.max_records:
                self.data["records"].sort(
                    key=lambda item: (item["quality"], item["hits"], item["fetchedAt"]))
                del self.data["records"][0]
            self.data["records"].append(row)
            self.data["records"].sort(key=lambda item: (item["fetchedAt"], item["quality"]),
                                      reverse=True)
            self.data["revision"] += 1
            if not self._save():
                self.data = before
                return {"ok": False, "reason": "storage"}
            return {"ok": True, "record": dict(row)}

    def search(self, query, limit=5):
        """Small lexical retrieval; no network, no model call, no write."""
        query = " ".join(str(query or "").split())[:240]
        if len(query) < 2:
            return []
        qgrams = {query[index:index + 2] for index in range(len(query) - 1)}
        scored = []
        for row in self.data["records"]:
            text = row["title"] + " " + row["summary"] + " " + row["text"]
            overlap = sum(1 for gram in qgrams if gram in text)
            if overlap:
                scored.append((overlap * 2 + row["quality"] / 100, row["hits"], row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [{"id": row["id"], "title": row["title"], "text": row["summary"],
                 "source": row["host"] or row["url"], "url": row["url"],
                 "quality": row["quality"], "score": round(score, 2)}
                for score, _, row in scored[:max(1, min(20, int(limit)))]]

    def stats(self):
        return {"status": self.status, "error": self.error,
                "revision": self.data.get("revision", 0),
                "total": len(self.data.get("records", [])),
                "maxRecords": self.max_records, "maxBytes": self.max_bytes}
