"""Autonomous selection from a small, vetted public source catalog.

This is not an open-web crawler. It rotates through public RSS/Atom sources
chosen by the project (news/science, video metadata, emotion, office/study),
selects one recent item, and delegates the actual page learning to
``owner_web_learning.WebLearningService`` so the training + emotion gates remain
mandatory. Video files are never downloaded; only public page/feed metadata is
learned.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from owner_web_learning import WebLearningService

CATALOG = (
    {"category": "news", "name": "NASA Breaking News",
     "feed": "https://www.nasa.gov/rss/dyn/breaking_news.rss"},
    {"category": "emotion", "name": "Psychology Today",
     "feed": "https://www.psychologytoday.com/us/front/feed"},
    {"category": "office", "name": "Microsoft 365 Blog",
     "feed": "https://www.microsoft.com/en-us/microsoft-365/blog/feed/"},
    {"category": "video", "name": "TED Talks",
     "feed": "https://www.ted.com/talks", "kind": "page"},
)
MAX_ITEMS = 10
DEFAULT_INTERVAL_MINUTES = 60


def _text(node, *names, ns=None):
    for name in names:
        found = node.find(name, ns) if ns else node.find(name)
        if found is not None and found.text:
            return " ".join(found.text.split())[:300]
    return ""


def parse_feed(body):
    """Extract bounded title/link rows from RSS 2.0 or Atom; no HTML execution."""
    if isinstance(body, bytes):
        text = body.decode("utf-8", "replace")
    else:
        text = str(body or "")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise ValueError("feed is not valid XML") from error
    rows = []
    if root.tag.endswith("rss") or root.find(".//item") is not None:
        for item in root.findall(".//item")[:MAX_ITEMS]:
            link = _text(item, "link") or _text(item, "guid")
            title = _text(item, "title")
            summary = _text(item, "description")
            if link and title:
                rows.append({"title": title, "link": link, "summary": summary})
    else:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//a:entry", ns)[:MAX_ITEMS]:
            title = _text(entry, "a:title", ns=ns)
            link = ""
            for node in entry.findall("a:link", ns):
                if node.get("rel") in (None, "alternate") and node.get("href"):
                    link = node.get("href")
                    break
            summary = _text(entry, "a:summary", ns=ns) or _text(entry, "a:content", ns=ns)
            if link and title:
                rows.append({"title": title, "link": link, "summary": summary})
    return rows


class AutonomousLearningConfig:
    def __init__(self, path=None, data_dir=None):
        root = Path(data_dir).expanduser() if data_dir else (
            Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth")
        self.path = Path(path) if path is not None else root / "auto-learning.json"
        self.enabled = False
        self.interval = DEFAULT_INTERVAL_MINUTES
        self.nextAt = 0
        self.index = 0
        self.last = "未启用"
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        self.enabled = raw.get("enabled") is True
        try:
            self.interval = max(15, min(24 * 60, int(raw.get("interval", DEFAULT_INTERVAL_MINUTES))))
            self.nextAt = max(0, int(raw.get("nextAt", 0)))
            self.index = max(0, int(raw.get("index", 0)))
        except (TypeError, ValueError):
            self.enabled = False
        self.last = str(raw.get("last") or "未启用")[:120]

    def set(self, enabled, interval=None):
        self.enabled = bool(enabled)
        if interval is not None:
            try:
                self.interval = max(15, min(24 * 60, int(interval)))
            except (TypeError, ValueError):
                self.interval = DEFAULT_INTERVAL_MINUTES
        if not self.enabled:
            self.nextAt = 0
        return self.save()

    def postpone(self, index):
        self.index = int(index)
        self.nextAt = int(time.time()) + self.interval * 60
        return self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps({"version": 1, "enabled": self.enabled, "interval": self.interval,
                               "nextAt": self.nextAt, "index": self.index, "last": self.last},
                              ensure_ascii=False, separators=(",", ":"))
            temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            temporary.write_text(body, encoding="utf-8")
            os.replace(temporary, self.path)
            return True
        except (OSError, ValueError, TypeError):
            return False


class AutonomousLearner:
    def __init__(self, service=None, catalog=CATALOG):
        self.service = service or WebLearningService()
        self.catalog = tuple(catalog)
        self.index = 0

    def next(self):
        if not self.catalog:
            return {"ok": False, "error": "empty_catalog"}
        start = self.index % len(self.catalog)
        errors = []
        for offset in range(len(self.catalog)):
            position = (start + offset) % len(self.catalog)
            source = self.catalog[position]
            self.index = (position + 1) % len(self.catalog)
            feed = source["feed"]
            added = self.service.add_source(feed)
            if not added.get("ok"):
                errors.append(f"{source['name']}:source")
                continue
            if source.get("kind") == "page":
                try:
                    result = self.service.learn(feed)
                    if result.get("ok"):
                        result.update({"category": source["category"], "sourceName": source["name"],
                                       "feed": feed, "position": position})
                        return result
                    errors.append(f"{source['name']}:{result.get('error', 'page')}")
                except Exception as error:
                    errors.append(f"{source['name']}:{type(error).__name__}")
                continue
            try:
                raw = self.service.fetcher.fetch(feed, allow_xml=True)
                items = parse_feed(raw.get("text", ""))
            except Exception as error:
                errors.append(f"{source['name']}:{getattr(error, 'code', type(error).__name__)}")
                continue
            for item in items[:MAX_ITEMS]:
                try:
                    link = item["link"]
                    if not self.service.sources.allows(link):
                        if not self.service.add_source(link).get("ok"):
                            continue
                    result = self.service.learn(link)
                    if result.get("ok"):
                        result.update({"category": source["category"], "sourceName": source["name"],
                                       "feed": feed, "position": position})
                        return result
                except Exception as error:
                    errors.append(f"{source['name']}:{type(error).__name__}")
            errors.append(f"{source['name']}:no_article")
        return {"ok": False, "error": "all_sources_failed", "details": errors[:4],
                "category": self.catalog[start]["category"]}


class AutonomousJob(QThread):
    done = Signal(object)

    def __init__(self, learner, parent=None):
        super().__init__(parent)
        self.learner = learner

    def run(self):
        try:
            self.done.emit(self.learner.next())
        except Exception as error:
            self.done.emit({"ok": False, "error": type(error).__name__})


class AutonomousLearningHost(QObject):
    status_changed = Signal(dict)

    def __init__(self, service=None, config=None, learner=None, parent=None):
        super().__init__(parent)
        self.service = service or WebLearningService()
        self.config = config or AutonomousLearningConfig()
        self.learner = learner or AutonomousLearner(self.service)
        self.learner.index = self.config.index
        self.worker = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self._last = self.config.last

    def state(self):
        return {"enabled": bool(self.config.enabled), "interval": self.config.interval,
                "busy": bool(self.worker and self.worker.isRunning()),
                "nextAt": self.config.nextAt, "index": self.config.index,
                "last": self._last, "catalog": [{"category": row["category"], "name": row["name"]}
                                                for row in CATALOG]}

    def configure(self, enabled, interval=None):
        self.config.set(enabled, interval)
        if enabled:
            if not self.config.nextAt:
                self.config.nextAt = int(time.time())
            self.timer.start()
            self._last = "已启用；按目录自主选题"
        else:
            self.timer.stop()
            self._last = "已停用；不会再自动联网"
        self.config.last = self._last
        self.config.save()
        self.status_changed.emit(self.state())

    def _tick(self):
        if not self.config.enabled or self.worker is not None:
            return
        if int(time.time()) < int(self.config.nextAt or 0):
            return
        self.worker = AutonomousJob(self.learner, self)
        self.worker.done.connect(self._done)
        self.worker.finished.connect(self._cleanup)
        self._last = "正在自主选题并学习…"
        self.status_changed.emit(self.state())
        self.worker.start()

    def _done(self, result):
        result = result if isinstance(result, dict) else {}
        self.config.index = self.learner.index
        if result.get("ok"):
            self._last = f"{result.get('category', '')} · {result.get('title', '')[:40]}"
        else:
            self._last = f"学习失败：{str(result.get('error', 'unknown'))[:60]}"
        self.config.last = self._last
        self.config.postpone(self.learner.index)
        self.status_changed.emit(self.state())

    def _cleanup(self):
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def shutdown(self):
        self.timer.stop()
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(1500)
