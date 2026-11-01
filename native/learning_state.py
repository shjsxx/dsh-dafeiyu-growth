"""Persisted owner setting and bounded schedule for public read-only learning.

This module never performs network I/O and never reads conversation content.
New installations stay off; enabling is a persistent owner action, not a prompt
for each request. A corrupt settings file disables automation without overwrite.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

DAY = 24 * 60 * 60
BACKOFF = (60 * 60, 3 * 60 * 60, 6 * 60 * 60, DAY)


def default_path():
    explicit = os.environ.get("DSH_DAFEIYU_LEARNING_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "learning.json"
    root = Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")
    return root / "dsh-dafeiyu-growth" / "learning.json"


def _stamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        return max(0.0, min(32_503_680_000.0, value)) if math.isfinite(value) else 0.0
    except (OverflowError, ValueError):
        return 0.0


class PublicLearningState:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path()
        self.enabled = False
        self.knowledge_enabled = False
        self.realtime_mode = False
        self.last_attempt = 0.0
        self.last_success = 0.0
        self.next_check = 0.0
        self.server_retry_until = 0.0
        self.failures = 0
        self.last_added = 0
        self.sources = []
        self.error = ""
        self.protected = False
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or not isinstance(raw.get("enabled"), bool):
                    raise ValueError("invalid learning setting")
                self.enabled = raw["enabled"]
                if "knowledgeEnabled" in raw and not isinstance(raw["knowledgeEnabled"], bool):
                    raise ValueError("invalid knowledge setting")
                self.knowledge_enabled = raw.get("knowledgeEnabled", False)
                self.realtime_mode = raw.get("realtimeMode") is True
                self.last_attempt = _stamp(raw.get("lastAttempt"))
                self.last_success = _stamp(raw.get("lastSuccess"))
                self.next_check = _stamp(raw.get("nextCheck"))
                self.server_retry_until = _stamp(raw.get("serverRetryUntil"))
                self.failures = min(100, max(0, int(raw.get("failures", 0))))
                self.last_added = min(1800, max(0, int(raw.get("lastAdded", 0))))
                self.error = str(raw.get("error", ""))[:240]
                sources = raw.get("sources")
                if isinstance(sources, list):
                    self.sources = [str(source)[:200] for source in sources[:8] if isinstance(source, str)]
            except (OSError, ValueError, TypeError, OverflowError):
                self.enabled = False
                self.protected = True
                self.error = "自动学习设置损坏，已停止自动联网并保留原文件。"

    def save(self):
        if self.protected:
            return False
        raw = {
            "version": 1, "enabled": self.enabled,
            "scope": "public-safe-curriculum-read-only" if self.knowledge_enabled else "public-emotion-lexicons-read-only",
            "knowledgeEnabled": self.knowledge_enabled, "realtimeMode": self.realtime_mode,
            "lastAttempt": self.last_attempt, "lastSuccess": self.last_success,
            "nextCheck": self.next_check, "failures": self.failures,
            "serverRetryUntil": self.server_retry_until,
            "lastAdded": self.last_added, "sources": self.sources, "error": self.error,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, self.path)
            return True
        except OSError:
            self.error = "无法保存自动学习设置，请检查本地存储。"
            return False

    def set_enabled(self, enabled):
        if self.protected:
            return False
        previous = self.enabled
        self.enabled = bool(enabled)
        if not self.save():
            self.enabled = previous
            return False
        return True

    def set_knowledge_enabled(self, enabled):
        if self.protected:
            return False
        previous, previous_due = self.knowledge_enabled, self.next_check
        self.knowledge_enabled = bool(enabled)
        if self.knowledge_enabled and not previous:
            self.next_check = min(self.next_check, time.time())
        if not self.save():
            self.knowledge_enabled, self.next_check = previous, previous_due
            return False
        return True

    def set_realtime_mode(self, enabled):
        """Hourly public lexicon/curriculum verification; never override backoff."""
        if self.protected:
            return False
        before = self.realtime_mode, self.next_check
        self.realtime_mode = bool(enabled)
        if self.realtime_mode and not self.failures:
            self.next_check = max(self.server_retry_until, min(self.next_check, self.last_success + 3600))
        if not self.save():
            self.realtime_mode, self.next_check = before
            return False
        return True

    def is_due(self, now=None):
        now = time.time() if now is None else now
        return self.enabled and not self.protected and now >= max(self.next_check, self.server_retry_until)

    def begin(self, now=None, manual=False):
        now = time.time() if now is None else now
        if self.protected or (not manual and not self.is_due(now)):
            return False
        if now < self.server_retry_until:
            self.error = "公开接口正在限流冷却中，请按计划稍后再试。"
            return False
        # Prevent immediate restarts, repeated clicks and crash/restart loops.
        if self.last_attempt and now - self.last_attempt < 60:
            return False
        self.last_attempt = now
        self.next_check = now + BACKOFF[0]
        self.error = ""
        if not self.save():
            self.error += " 本次不会发起联网。"
            return False
        return True

    def finish(self, ok, added=0, sources=(), error="", now=None, retry_after=0):
        now = time.time() if now is None else now
        if ok:
            self.last_success = now
            self.last_added = max(0, min(1800, int(added)))
            self.sources = [str(source)[:200] for source in sources[:8]]
            self.failures = 0
            self.next_check = now + (3600 if self.realtime_mode else DAY)
            self.error = ""
        else:
            self.failures = min(100, self.failures + 1)
            self.next_check = now + BACKOFF[min(self.failures - 1, len(BACKOFF) - 1)]
            self.error = str(error or "暂时无法读取公开词典；稍后再试。")[:240]
        if _stamp(retry_after) > 0:
            self.server_retry_until = max(self.server_retry_until, now + _stamp(retry_after))
            self.next_check = max(self.next_check, self.server_retry_until)
        return self.save()

    def snapshot(self, busy=False):
        return {
            "enabled": self.enabled, "knowledgeEnabled": self.knowledge_enabled,
            "realtimeMode": self.realtime_mode, "intervalSeconds": 3600 if self.realtime_mode else DAY,
            "busy": bool(busy), "protected": self.protected,
            "lastAttempt": self.last_attempt, "lastSuccess": self.last_success,
            "nextCheck": self.next_check, "lastAdded": self.last_added,
            "sources": list(self.sources), "error": self.error,
        }
