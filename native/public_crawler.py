"""Bounded, robots-respecting public crawler for a single allowlisted API.

No arbitrary URL fetching, no HTML scraping, no login walls, no Zhihu and no
redirects to other hosts. Only a fixed Wikimedia REST endpoint is permitted;
failures are explicit codes and never become fabricated memories.
"""
from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
import urllib.robotparser
import time

SOURCE = {
    "id": "wikipedia_zh",
    "host": "zh.wikipedia.org",
    "license": "CC BY-SA 4.0",
    "label": "维基百科中文摘要",
}
MAX_BYTES = 256 * 1024
TIMEOUT = 12
USER_AGENT = "Yunlan/0.17 (local companion memory; contact: project repository)"


class CrawlError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CrawlError("redirect_refused")


def _open_no_redirect(request, timeout):
    opener = urllib.request.build_opener(_RejectRedirect)
    return opener.open(request, timeout=timeout)


class PublicCrawler:
    def __init__(self, fetcher=None, clock=None):
        self._fetch = fetcher or self._default_fetch
        self._clock = clock or (lambda: time.time())
        self._robots = None
        self._robots_at = 0.0

    @staticmethod
    def _valid_topic(topic):
        topic = " ".join(str(topic or "").split())
        if not 1 < len(topic) <= 40:
            return ""
        if any(ord(ch) < 32 for ch in topic):
            return ""
        return topic

    def _allowed(self):
        now = self._clock()
        if self._robots is not None and now - self._robots_at < 86400:
            return self._robots
        robots = urllib.robotparser.RobotFileParser()
        try:
            body = self._fetch("https://zh.wikipedia.org/robots.txt", MAX_BYTES)
            robots.parse(body.decode("utf-8", "replace").splitlines())
            self._robots = robots.can_fetch(USER_AGENT, "https://zh.wikipedia.org/api/rest_v1/")
        except Exception:
            self._robots = False
        self._robots_at = now
        return self._robots

    def fetch_summary(self, topic):
        topic = self._valid_topic(topic)
        if not topic:
            raise CrawlError("invalid_topic")
        if not self._allowed():
            raise CrawlError("robots_disallowed")
        url = "https://zh.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(topic, safe="")
        raw = self._fetch(url, MAX_BYTES)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise CrawlError("bad_json") from None
        text = data.get("extract") if isinstance(data, dict) else None
        if not isinstance(text, str):
            raise CrawlError("no_extract")
        text = " ".join(text.split())[:600]
        if len(text) < 20:
            raise CrawlError("too_short")
        return {"topic": topic, "text": text, "source": SOURCE["id"],
                "sourceLabel": SOURCE["label"], "license": SOURCE["license"],
                "url": "https://zh.wikipedia.org/wiki/" + urllib.parse.quote(topic.replace(" ", "_"), safe="")}

    @staticmethod
    def _default_fetch(url, limit):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in ("zh.wikipedia.org",) or parsed.username or parsed.password:
            raise CrawlError("url_not_allowed")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with _open_no_redirect(request, TIMEOUT) as response:
            if response.status != 200:
                raise CrawlError("http_status")
            if urllib.parse.urlsplit(response.geturl()).hostname not in ("zh.wikipedia.org",):
                raise CrawlError("redirect_refused")
            body = response.read(limit + 1)
        if len(body) > limit:
            raise CrawlError("too_large")
        return body


POETRY = {
    "id": "shijing",
    "label": "诗经 · chinese-poetry (MIT)",
    "license": "MIT repository / public-domain text",
    "url": "https://api.github.com/repos/chinese-poetry/chinese-poetry/contents/%E8%AF%97%E7%BB%8F/shijing.json",
}


class PoetryCrawler:
    """Fixed GitHub API file, no user URL and no arbitrary crawl.

    Only one public-domain classical Chinese poetry file is read from the
    MIT-licensed chinese-poetry repository. The API is used directly; no HTML,
    login wall, redirect to another host or user-supplied URL is accepted.
    """

    def __init__(self, fetcher=None, clock=None):
        self._fetch = fetcher or self._default_fetch
        self._clock = clock or (lambda: time.time())

    def fetch_poem(self, index=None):
        raw = self._fetch(POETRY["url"], 1024 * 1024)
        try:
            data = json.loads(raw.decode("utf-8"))
            content = base64.b64decode(data["content"]).decode("utf-8")
            poems = json.loads(content)
        except (KeyError, ValueError, TypeError, UnicodeDecodeError):
            raise CrawlError("bad_json") from None
        if not isinstance(poems, list) or not poems:
            raise CrawlError("empty")
        if index is None:
            index = int(self._clock() // 86400) % len(poems)
        poem = poems[max(0, min(len(poems) - 1, int(index)))]
        if not isinstance(poem, dict):
            raise CrawlError("bad_poem")
        title = " ".join(str(poem.get("title") or "").split())[:40]
        lines = poem.get("content") if isinstance(poem.get("content"), list) else []
        text = " ".join(" ".join(str(line).split()) for line in lines[:2])[:200]
        if not title or len(text) < 6:
            raise CrawlError("too_short")
        return {"kind": "culture", "topic": title, "text": text,
                "source": POETRY["id"], "sourceLabel": POETRY["label"],
                "license": POETRY["license"], "url": POETRY["url"]}

    @staticmethod
    def _default_fetch(url, limit):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "api.github.com"
                or not parsed.path.startswith("/repos/chinese-poetry/chinese-poetry/")
                or parsed.username or parsed.password):
            raise CrawlError("url_not_allowed")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/vnd.github+json"})
        with _open_no_redirect(request, TIMEOUT) as response:
            if response.status != 200:
                raise CrawlError("http_status")
            if urllib.parse.urlsplit(response.geturl()).hostname != "api.github.com":
                raise CrawlError("redirect_refused")
            body = response.read(limit + 1)
        if len(body) > limit:
            raise CrawlError("too_large")
        return body
