"""v0.34 controlled public-web scout.

Design constraints:

* only the EvolutionKernel allowlist can be fetched;
* HTTPS GET only, no cookies, no credentials, no user data;
* redirects are followed manually and each target is re-checked;
* request/byte budgets and a small on-disk cache;
* page text is untrusted data, never instructions, and citations are kept.

The default transport is urllib, but tests inject a fake transport so the suite
never touches the network.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from evolution_kernel import EvolutionKernel, PolicyGate

VERSION = 1
MAX_TEXT = 8000
MAX_TITLE = 200
DEFAULT_CACHE_TTL = 6 * 60 * 60
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json")
USER_AGENT = "Yunlan-Research/0.34 (public allowlist; no cookies)"


@dataclass
class ResearchTask:
    goal: str
    max_requests: int = 8
    max_bytes: int = 1_000_000
    timeout: float = 15.0
    max_redirects: int = 3
    used: int = 0
    bytes_used: int = 0


@dataclass
class FetchResult:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    final_url: str = ""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "noscript", "template", "svg"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "noscript", "template", "svg") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title + " " + data).strip()
        elif not self._skip:
            self.parts.append(data)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return the 3xx response instead of silently following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(url, timeout, max_bytes, headers):
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        if error.code in (301, 302, 303, 307, 308):
            return FetchResult(error.code, dict(error.headers), b"", url)
        raise
    with response:
        body = response.read(max_bytes + 1)
        return FetchResult(response.status, dict(response.headers), body, response.geturl())


def _clean_text(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:MAX_TEXT]


def _cache_key(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def extract_page(body, content_type=""):
    raw = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    if "html" in content_type.lower():
        parser = _TextExtractor()
        try:
            parser.feed(raw)
            parser.close()
        except Exception:
            pass
        title = _clean_text(parser.title)[:MAX_TITLE]
        text = _clean_text(" ".join(parser.parts))
        return title, text
    return "", _clean_text(raw)


class WebScout:
    def __init__(self, kernel=None, cache_dir=None, transport=None, clock=None,
                 cache_ttl=DEFAULT_CACHE_TTL):
        self.kernel = kernel or EvolutionKernel(gate=PolicyGate())
        self.transport = transport or _default_transport
        self.cache_dir = (Path(cache_dir).expanduser() if cache_dir
                          else Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")
                          / "dsh-dafeiyu-growth" / "web-cache")
        self.clock = clock or (lambda: time.time())
        self.cache_ttl = max(0, int(cache_ttl))

    # ---- cache -------------------------------------------------------
    def _cache_path(self, url):
        return self.cache_dir / (_cache_key(url) + ".json")

    def _cache_get(self, url):
        path = self._cache_path(url)
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or data.get("version") != VERSION or data.get("url") != url:
            return None
        if not isinstance(data.get("fetchedAt"), (int, float)):
            return None
        if self.clock() - float(data["fetchedAt"]) > self.cache_ttl:
            return None
        if not isinstance(data.get("text"), str) or not isinstance(data.get("title"), str):
            return None
        return data

    def _cache_put(self, url, title, text, content_type, fetched_at):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            data = {"version": VERSION, "url": url, "title": title[:MAX_TITLE],
                    "text": text[:MAX_TEXT], "contentType": content_type[:80],
                    "fetchedAt": int(fetched_at)}
            temporary = self._cache_path(url).with_name(self._cache_path(url).name + f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                 encoding="utf-8")
            os.replace(temporary, self._cache_path(url))
        except OSError:
            pass

    def clear_cache(self):
        removed = 0
        if not self.cache_dir.is_dir():
            return 0
        for path in self.cache_dir.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    # ---- fetch -------------------------------------------------------
    def _citation(self, url, title, cached):
        return {"url": url, "host": urlsplit(url).hostname or "", "title": title[:MAX_TITLE],
                "fetchedAt": int(self.clock()), "cached": bool(cached), "trust": "untrusted"}

    def fetch(self, url, task):
        if not isinstance(task, ResearchTask):
            raise TypeError("task must be a ResearchTask")
        gate = self.kernel.review_network(url, "GET", task.used, task.max_requests)
        if not gate.allowed:
            return {"ok": False, "error": gate.reason, "citation": None, "text": "", "title": ""}

        cached = self._cache_get(url)
        if cached:
            task.bytes_used += len(cached["text"].encode("utf-8"))
            return {"ok": True, "cached": True, "text": cached["text"], "title": cached["title"],
                    "citation": self._citation(url, cached["title"], True), "error": ""}

        current = url
        for redirect in range(max(0, int(task.max_redirects)) + 1):
            if task.used >= task.max_requests:
                return {"ok": False, "error": "network budget exhausted", "citation": None, "text": "", "title": ""}
            task.used += 1
            try:
                response = self.transport(current, task.timeout, max(0, task.max_bytes - task.bytes_used), {
                    "User-Agent": USER_AGENT, "Accept": "text/html, text/plain, application/json",
                })
            except Exception as error:
                return {"ok": False, "error": f"fetch failed: {type(error).__name__}",
                        "citation": None, "text": "", "title": ""}
            status = int(getattr(response, "status", 0) or 0)
            headers = getattr(response, "headers", {}) or {}
            location = headers.get("Location") or headers.get("location")
            if status in (301, 302, 303, 307, 308):
                if redirect >= task.max_redirects or not location:
                    return {"ok": False, "error": "redirect limit reached", "citation": None, "text": "", "title": ""}
                current = urljoin(current, location)
                target = self.kernel.review_network(current, "GET", task.used, task.max_requests)
                if not target.allowed:
                    return {"ok": False, "error": f"redirect denied: {target.reason}",
                            "citation": None, "text": "", "title": ""}
                continue
            if status < 200 or status >= 300:
                return {"ok": False, "error": f"HTTP {status}", "citation": None, "text": "", "title": ""}
            body = getattr(response, "body", b"") or b""
            if len(body) > max(0, task.max_bytes - task.bytes_used):
                return {"ok": False, "error": "byte budget exceeded", "citation": None, "text": "", "title": ""}
            content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
            if content_type and not any(kind in content_type.lower() for kind in ALLOWED_CONTENT_TYPES):
                return {"ok": False, "error": "unsupported content type", "citation": None, "text": "", "title": ""}
            title, text = extract_page(body, content_type)
            if not text:
                return {"ok": False, "error": "empty page text", "citation": None, "text": "", "title": ""}
            task.bytes_used += len(body)
            final_url = getattr(response, "final_url", "") or current
            if final_url != current:
                final_gate = self.kernel.review_network(final_url, "GET", task.used, task.max_requests)
                if not final_gate.allowed:
                    return {"ok": False, "error": f"final URL denied: {final_gate.reason}",
                            "citation": None, "text": "", "title": ""}
            self._cache_put(final_url, title, text, content_type, self.clock())
            return {"ok": True, "cached": False, "text": text, "title": title,
                    "citation": self._citation(final_url, title, False), "error": ""}
        return {"ok": False, "error": "redirect limit reached", "citation": None, "text": "", "title": ""}

    def research(self, urls, task):
        results = []
        for url in list(urls)[:max(0, int(task.max_requests))]:
            if task.used >= task.max_requests or task.bytes_used >= task.max_bytes:
                break
            results.append(self.fetch(url, task))
        citations = [row["citation"] for row in results if row.get("ok") and row.get("citation")]
        return {"goal": task.goal[:200], "results": results, "citations": citations,
                "used": task.used, "bytes": task.bytes_used}
