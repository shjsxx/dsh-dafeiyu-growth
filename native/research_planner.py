"""Deterministic research planner for the public allowlist.

The planner never performs a search request. It maps a natural-language goal to
known public documentation entry points and validates every candidate through
the same PolicyGate used at execution time. The result is only an editable
proposal: approval and running remain explicit.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from evolution_kernel import PolicyGate

VERSION = 1
MAX_SOURCES = 5
MAX_GOAL = 300
DEFAULT_REQUESTS = 6
DEFAULT_BYTES = 500_000

# Fixed, human-auditable seeds. No wildcard hosts and no dynamic discovery.
SEEDS = (
    {
        "id": "python-docs",
        "host": "docs.python.org",
        "url": "https://docs.python.org/3/",
        "title": "Python 3 官方文档",
        "keywords": (
            "python", "python3", "asyncio", "typing", "标准库", "解释器",
            "venv", "pip", "gil", "cpython", "语法", "包管理",
        ),
    },
    {
        "id": "mdn-web",
        "host": "developer.mozilla.org",
        "url": "https://developer.mozilla.org/en-US/docs/Web",
        "title": "MDN Web 文档",
        "keywords": (
            "javascript", "js", "typescript", "web", "css", "html", "dom",
            "browser", "浏览器", "前端", "网页",
        ),
    },
    {
        "id": "cpython-repo",
        "host": "github.com",
        "url": "https://github.com/python/cpython",
        "title": "CPython 源码仓库",
        "keywords": (
            "cpython", "python 源码", "python 仓库", "c 实现", "开源项目",
        ),
    },
    {
        "id": "mdn-content",
        "host": "github.com",
        "url": "https://github.com/mdn/content",
        "title": "MDN 内容仓库",
        "keywords": ("mdn", "web docs", "文档仓库"),
    },
    {
        "id": "cpython-readme",
        "host": "raw.githubusercontent.com",
        "url": "https://raw.githubusercontent.com/python/cpython/main/README.rst",
        "title": "CPython README",
        "keywords": ("cpython readme", "python readme"),
    },
)

_URL_RE = re.compile(r"https?://[^\s<>\"'，。；：！？、）】」』]+", re.IGNORECASE)


def _clean(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _trim_url(value):
    return str(value or "").rstrip(".,;:!?)]}>，。；：！？、）】」』")


class ResearchPlanner:
    """Map a goal to allowlisted, editable seed URLs without network access."""

    def __init__(self, gate=None):
        self.gate = gate or PolicyGate()

    def _allowed(self, url):
        try:
            return bool(self.gate.network(url, "GET", 0, 1).allowed)
        except Exception:
            return False

    def _user_urls(self, goal):
        urls = []
        for match in _URL_RE.findall(goal):
            url = _trim_url(match)
            if self._allowed(url) and url not in urls:
                urls.append(url)
        return urls

    def _score(self, goal, seed):
        text = goal.casefold()
        hits = [keyword for keyword in seed["keywords"] if keyword.casefold() in text]
        # Prefer the more specific matching seed when several share a host.
        return (len(hits), max((len(keyword) for keyword in hits), default=0))

    def plan(self, goal, max_sources=3):
        """Return an editable plan. Never writes state and never opens a socket."""
        goal = _clean(goal, MAX_GOAL)
        limit = max(1, min(MAX_SOURCES, int(max_sources or 3)))
        user_urls = self._user_urls(goal)
        candidates = []
        for seed in SEEDS:
            if not self._allowed(seed["url"]):
                continue
            score = self._score(goal, seed)
            if score[0] > 0:
                candidates.append((score, seed))
        candidates.sort(key=lambda item: (item[0], item[1]["id"]), reverse=True)
        selected = []
        seen = set()
        for url in user_urls:
            selected.append({
                "url": url,
                "host": urlsplit(url).hostname or "",
                "title": "目标中的公开 URL",
                "reason": "从研究目标中提取，且通过白名单校验",
            })
            seen.add(url)
        for _score_value, seed in candidates:
            if len(selected) >= limit:
                break
            if seed["url"] in seen:
                continue
            selected.append({
                "url": seed["url"],
                "host": seed["host"],
                "title": seed["title"],
                "reason": "目标命中固定公开来源目录",
            })
            seen.add(seed["url"])
        selected = selected[:limit]
        if not selected:
            note = ("没有匹配到固定公开来源。请手动填写白名单 URL；"
                    "自动规划不会猜测或访问任意站点。")
        elif user_urls:
            note = "已保留目标中的公开 URL，并补充匹配的固定来源；可继续编辑。"
        else:
            note = "已按固定公开来源目录生成候选计划；尚未提出、批准或联网。"
        return {
            "version": VERSION,
            "goal": goal,
            "urls": [row["url"] for row in selected],
            "sources": selected,
            "maxRequests": DEFAULT_REQUESTS,
            "maxBytes": DEFAULT_BYTES,
            "note": note,
            "matched": bool(candidates),
        }
