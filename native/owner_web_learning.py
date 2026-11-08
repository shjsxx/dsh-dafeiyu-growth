"""Owner-directed web learning for Yunlan.

This is intentionally different from the fixed research allowlist: the owner
adds public sites, and only those sites are fetched. It is not an internet-wide
crawler. Every successful fetch must be recorded in both the training journal
and the emotion store before its summary is written to memory.

Safety properties:
* HTTP(S) GET only, no credentials, cookies, uploads or browser automation;
* explicit host allowlist, re-checked on every redirect;
* localhost/private/reserved addresses rejected before connecting;
* robots.txt respected;
* bounded bytes, redirects and extracted text;
* page text is untrusted data, never instructions.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path

from web_scout import extract_page
from web_knowledge import WebKnowledgeStore

VERSION = 1
MAX_SOURCES = 100
MAX_BYTES = 512 * 1024
MAX_TEXT = 12_000
MAX_REDIRECTS = 3
TIMEOUT = 15.0
MIN_TEXT = 20
USER_AGENT = "Yunlan-Learn/0.35 (owner-selected public sites; no cookies)"


class WebLearningError(RuntimeError):
    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


def _default_source_path(data_dir=None):
    root = Path(data_dir).expanduser() if data_dir else (
        Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth")
    return root / "web-sources.json"


def normalize_url(url):
    """Validate syntax and return a stable URL without credentials/fragment."""
    raw = str(url or "").strip()
    if not raw or len(raw) > 2048:
        raise WebLearningError("invalid_url")
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as error:
        raise WebLearningError("invalid_url") from error
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebLearningError("only_http_https")
    if parsed.username or parsed.password:
        raise WebLearningError("credentials_not_allowed")
    if parsed.port not in (None, 80, 443):
        raise WebLearningError("port_not_allowed")
    host = parsed.hostname.lower().rstrip(".")
    if not host or len(host) > 253:
        raise WebLearningError("invalid_host")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") \
            or host.endswith(".internal"):
        raise WebLearningError("private_address")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise WebLearningError("private_address")
    path = parsed.path or "/"
    query = ("?" + parsed.query) if parsed.query else ""
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, path, query, ""))


def _resolve_public(host, port, resolver=None):
    resolver = resolver or socket.getaddrinfo
    try:
        rows = resolver(host, port, type=socket.SOCK_STREAM)
    except (OSError, TypeError, ValueError) as error:
        raise WebLearningError("dns_failed") from error
    if not rows:
        raise WebLearningError("dns_failed")
    for row in rows:
        try:
            address = ipaddress.ip_address(str(row[4][0]).split("%", 1)[0])
        except (ValueError, IndexError, TypeError):
            raise WebLearningError("dns_failed") from None
        if not address.is_global:
            raise WebLearningError("private_address")
    return True


@dataclass
class FetchResult:
    status: int
    headers: dict
    body: bytes
    final_url: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def default_transport(url, timeout, headers):
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        try:
            body = error.read(MAX_BYTES + 1)
        except (OSError, ValueError):
            body = b""
        return FetchResult(error.code, dict(error.headers or {}), body, url)
    with response:
        return FetchResult(response.status, dict(response.headers),
                           response.read(MAX_BYTES + 1), response.geturl())


class UserWebSources:
    """Persistent explicit host allowlist; no hard-coded public sites."""

    def __init__(self, path=None, data_dir=None, resolver=None, clock=None):
        self.path = Path(path) if path is not None else _default_source_path(data_dir)
        self.resolver = resolver or socket.getaddrinfo
        self.clock = clock or time.time
        self.sites = []
        self.status = "new"
        self.error = ""
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError):
            self.status, self.error = "protected", "web source file is unreadable"
            return
        rows = raw.get("sites") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            self.status, self.error = "protected", "web source file is invalid"
            return
        clean = []
        for row in rows[:MAX_SOURCES]:
            host = row.get("host") if isinstance(row, dict) else None
            if not isinstance(host, str) or not host:
                continue
            try:
                host = normalize_url("https://" + host).split("/")[2].split(":")[0]
            except WebLearningError:
                continue
            clean.append({"host": host, "label": str(row.get("label") or host)[:80],
                          "addedAt": max(0, int(row.get("addedAt") or 0))})
        self.sites = clean
        self.status = "ready"

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps({"version": VERSION, "sites": self.sites},
                              ensure_ascii=False, separators=(",", ":"))
            temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            temporary.write_text(body, encoding="utf-8")
            os.replace(temporary, self.path)
            self.status, self.error = "ready", ""
            return True
        except (OSError, ValueError, TypeError):
            self.status, self.error = "error", "web source file could not be written"
            return False

    def add(self, url):
        normalized = normalize_url(url)
        host = urllib.parse.urlsplit(normalized).hostname.lower()
        _resolve_public(host, 443, self.resolver)
        if self.status == "protected":
            return False
        if not any(row["host"] == host for row in self.sites):
            self.sites = (self.sites + [{
                "host": host, "label": host, "addedAt": int(self.clock())}])[-MAX_SOURCES:]
            if not self._save():
                return False
        return True

    def remove(self, host):
        host = str(host or "").lower().rstrip(".")
        before = len(self.sites)
        self.sites = [row for row in self.sites if row["host"] != host]
        return len(self.sites) != before and self._save()

    def allows(self, url):
        host = urllib.parse.urlsplit(str(url or "")).hostname
        if not host:
            return False
        return any(row["host"] == host.lower().rstrip(".") for row in self.sites)

    def snapshot(self):
        return {"status": self.status, "error": self.error,
                "sites": [dict(row) for row in self.sites]}


class WebArticleFetcher:
    def __init__(self, sources, resolver=None, transport=None, clock=None,
                 respect_robots=True):
        self.sources = sources
        self.resolver = resolver or socket.getaddrinfo
        self.transport = transport or default_transport
        self.clock = clock or time.time
        self.respect_robots = bool(respect_robots)

    def _robots_allowed(self, url):
        if not self.respect_robots:
            return True
        parsed = urllib.parse.urlsplit(url)
        robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        try:
            response = self.transport(robots_url, TIMEOUT, {"User-Agent": USER_AGENT})
        except Exception as error:
            raise WebLearningError("robots_unavailable") from error
        status = int(getattr(response, "status", 0) or 0)
        body = getattr(response, "body", b"") or b""
        if status in (404, 410):
            return True
        if status < 200 or status >= 300:
            raise WebLearningError("robots_unavailable")
        robots = urllib.robotparser.RobotFileParser()
        try:
            robots.parse(body.decode("utf-8", "replace").splitlines())
        except (ValueError, UnicodeDecodeError):
            raise WebLearningError("robots_unavailable") from None
        return robots.can_fetch(USER_AGENT, url)

    def fetch(self, url, allow_xml=False):
        current = normalize_url(url)
        if not self.sources.allows(current):
            raise WebLearningError("source_not_added")
        parsed = urllib.parse.urlsplit(current)
        _resolve_public(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                        self.resolver)
        if not self._robots_allowed(current):
            raise WebLearningError("robots_disallowed")
        used = 0
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urllib.parse.urlsplit(current)
            _resolve_public(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                            self.resolver)
            try:
                response = self.transport(current, TIMEOUT, {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html, text/plain, application/xhtml+xml",
                })
            except WebLearningError:
                raise
            except Exception as error:
                raise WebLearningError("network") from error
            used += 1
            status = int(getattr(response, "status", 0) or 0)
            headers = getattr(response, "headers", {}) or {}
            location = headers.get("Location") or headers.get("location")
            if status in (301, 302, 303, 307, 308):
                if used > MAX_REDIRECTS or not location:
                    raise WebLearningError("redirect_limit")
                target = normalize_url(urllib.parse.urljoin(current, str(location)))
                if not self.sources.allows(target):
                    raise WebLearningError("redirect_not_added")
                if not self._robots_allowed(target):
                    raise WebLearningError("robots_disallowed")
                _resolve_public(urllib.parse.urlsplit(target).hostname,
                                urllib.parse.urlsplit(target).port or 443, self.resolver)
                current = target
                continue
            if status < 200 or status >= 300:
                raise WebLearningError(f"http_{status}")
            body = getattr(response, "body", b"") or b""
            if len(body) > MAX_BYTES:
                raise WebLearningError("too_large")
            content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
            if content_type and not any(kind in content_type.lower() for kind in
                                        ("text/html", "text/plain", "application/xhtml+xml",
                                         "application/json")
                                        + (("application/rss+xml", "application/atom+xml",
                                            "application/xml", "text/xml") if allow_xml else ())):
                raise WebLearningError("unsupported_content")
            if allow_xml and any(kind in content_type.lower() for kind in
                                 ("rss+xml", "atom+xml", "xml")):
                title = ""
                text = body.decode("utf-8", "replace")[:256 * 1024]
            else:
                title, text = extract_page(body, content_type)
                text = " ".join(text.split())[:MAX_TEXT]
            if len(text) < MIN_TEXT:
                raise WebLearningError("empty_page")
            final_url = getattr(response, "final_url", "") or current
            if final_url != current:
                final_url = normalize_url(final_url)
                if not self.sources.allows(final_url):
                    raise WebLearningError("redirect_not_added")
                if not self._robots_allowed(final_url):
                    raise WebLearningError("robots_disallowed")
            return {"url": final_url, "host": parsed.hostname.lower(),
                    "title": title[:200], "text": text,
                    "bytes": len(body), "contentType": content_type[:80],
                    "fetchedAt": int(self.clock())}
        raise WebLearningError("redirect_limit")


class WebLearningService:
    """Fetch an owner-selected page, record it, then add one public summary."""

    def __init__(self, data_dir=None, sources=None, fetcher=None, memory=None,
                 emotion=None, journal=None, knowledge=None):
        from emotion_model import EmotionModel
        from memory_engine import MemoryEngine
        from training_journal import TrainingJournal
        root = Path(data_dir).expanduser() if data_dir else (
            Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth")
        self.sources = sources or UserWebSources(data_dir=root)
        self.fetcher = fetcher or WebArticleFetcher(self.sources)
        self.memory = memory or MemoryEngine(root / "memory.json")
        self.emotion = emotion or EmotionModel(root / "emotion.json")
        self.journal = journal or TrainingJournal(root / "training.json")
        self.knowledge = knowledge or WebKnowledgeStore(data_dir=root)

    def add_source(self, url):
        try:
            ok = self.sources.add(url)
        except (WebLearningError, OSError, ValueError) as error:
            return {"ok": False, "error": getattr(error, "code", str(error))}
        return {"ok": bool(ok), "sources": self.sources.snapshot(),
                "error": "" if ok else "source_not_added"}

    def learn(self, url):
        """A fetch is not learning until both records exist and memory accepts it."""
        if self.knowledge.has_url(url):
            return {"ok": False, "error": "duplicate_url"}
        try:
            article = self.fetcher.fetch(url)
        except (WebLearningError, OSError, ValueError) as error:
            return {"ok": False, "error": getattr(error, "code", str(error))}
        event_id = self.journal.begin("knowledge_index", "general", "user_web")
        if event_id is None:
            return {"ok": False, "error": "training_record_unavailable"}
        host = article.get("host") or ""
        title = article.get("title") or host
        text = article.get("text") or ""
        try:
            if not self.emotion.note_web_learning(host, title, len(text), "success"):
                self.journal.finish(event_id, "failed", {"bytes": article.get("bytes", 0),
                                                          "characters": len(text)}, error="storage")
                return {"ok": False, "error": "emotion_record_failed"}
            knowledge = self.knowledge.add(
                article.get("url") or url, title=title, text=text, summary=text[:600],
                host=host, category=article.get("category", ""),
                fetched_at=article.get("fetchedAt"))
            added = self.memory.ingest_external({
                "topic": title[:60],
                "text": text[:600],
                "sourceLabel": "你指定的网站：" + host,
                "kind": "public",
            })
            knowledge_added = 1 if knowledge.get("ok") else 0
            status = "success" if (knowledge_added or added) else "no_change"
            self.journal.finish(event_id, status, {
                "bytes": int(article.get("bytes") or 0),
                "characters": len(text),
                "added": int(bool(knowledge_added or added)),
            })
            return {"ok": True, "title": title, "host": host,
                    "chars": len(text), "added": int(bool(added)),
                    "knowledgeAdded": knowledge_added,
                    "knowledgeReason": "" if knowledge.get("ok") else knowledge.get("reason", ""),
                    "text": text[:600], "status": status}
        except (OSError, TypeError, ValueError) as error:
            self.journal.finish(event_id, "failed", {"bytes": article.get("bytes", 0),
                                                      "characters": len(text)}, error="storage")
            return {"ok": False, "error": type(error).__name__}

    def history(self):
        try:
            return list(self.emotion.snapshot().get("webLearningRecent") or [])
        except Exception:
            return []

    def is_seen(self, url):
        return self.knowledge.has_url(url)
