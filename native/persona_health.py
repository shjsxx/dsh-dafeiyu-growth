"""Periodic, read-only persona drift monitor.

It compares the latest automatic persona-history entry with the current
personality snapshot. A review signal is emitted only when the existing
reflection layer reports needs_review and the result fingerprint changed. It
never restores, rewrites or approves a personality change.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from evolution_service import EvolutionService
from persona_history import PersonaHistory
from personality_engine import PersonalityEngine

CONFIG_VERSION = 1
MIN_INTERVAL = 60
MAX_INTERVAL = 7 * 24 * 60 * 60
DEFAULT_INTERVAL = 6 * 60 * 60


def default_config_path():
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "persona-health.json"


def _bounded(value):
    try:
        return max(MIN_INTERVAL, min(MAX_INTERVAL, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL


class PersonaHealthConfig:
    def __init__(self, path=None, clock=None):
        self.path = Path(path).expanduser() if path is not None else default_config_path()
        self._clock = clock or (lambda: time.time())
        self.enabled = False
        self.interval = DEFAULT_INTERVAL
        self.last_scan = 0
        self.last_status = ""
        self.last_fingerprint = ""
        self.changes = 0
        self.status = "new"
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError):
            self.status = "protected"
            return
        if not isinstance(raw, dict) or raw.get("version") != CONFIG_VERSION:
            self.status = "protected"
            return
        self.enabled = raw.get("enabled") is True
        self.interval = _bounded(raw.get("interval"))
        self.last_scan = max(0, int(raw.get("lastScan") or 0))
        self.last_status = str(raw.get("lastStatus") or "")[:32]
        self.last_fingerprint = str(raw.get("fingerprint") or "")[:32]
        self.changes = max(0, int(raw.get("changes") or 0))
        self.status = "ready"

    def set(self, enabled, interval, latest=None):
        self.enabled = bool(enabled)
        self.interval = _bounded(interval)
        if isinstance(latest, dict):
            self.last_scan = max(0, int(latest.get("at") or self._clock()))
            self.last_status = str(latest.get("status") or "")[:32]
            self.last_fingerprint = str(latest.get("fingerprint") or "")[:32]
            self.changes = max(0, int(latest.get("changes") or 0))
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({
                "version": CONFIG_VERSION, "enabled": self.enabled,
                "interval": self.interval, "lastScan": self.last_scan,
                "lastStatus": self.last_status, "fingerprint": self.last_fingerprint,
                "changes": self.changes, "updatedAt": int(self._clock()),
            }, ensure_ascii=False, separators=(",", ":"))
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
            self.status = "ready"
            return True
        except OSError:
            self.status = "error"
            return False
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class PersonaHealthHost(QObject):
    status_changed = Signal(dict)
    review_needed = Signal(dict)

    def __init__(self, service=None, personality=None, history=None, config=None, parent=None):
        super().__init__(parent)
        self.service = service or EvolutionService()
        self.personality = personality or PersonalityEngine()
        self.history = history or PersonaHistory()
        self.config = config
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.scan)
        self._last = "未启用；只读检查，不改人格。"
        self._last_fingerprint = config.last_fingerprint if config is not None else ""
        self._last_result = None

    def state(self):
        result = self._last_result or {}
        return {
            "enabled": bool(self.config.enabled) if self.config is not None else self.timer.isActive(),
            "interval": int(self.config.interval if self.config is not None else DEFAULT_INTERVAL),
            "last": self._last,
            "status": str(result.get("status") or ""),
            "changes": len(result.get("changes") or []),
            "fingerprint": str(result.get("fingerprint") or ""),
            "persisted": self.config is not None,
        }

    def _emit(self):
        self.status_changed.emit(self.state())

    def configure(self, enabled, interval):
        interval = _bounded(interval)
        if self.config is not None:
            self.config.set(enabled, interval, latest=self._last_result)
        if enabled:
            self.timer.start(interval * 1000)
            self._last = "已启用；按间隔只读检查，不自动改人格。"
            self.scan()
        else:
            self.timer.stop()
            self._last = "已停用；不会再自动检查。"
            self._emit()

    def scan(self):
        current = self.personality.snapshot()
        entries = self.history.entries(1)
        if not entries:
            try:
                self.history.record(current, reason="monitor")
            except (OSError, ValueError, TypeError):
                pass
            self._last = "已建立首个快照；还没有可比较的历史。"
            self._emit()
            return None
        previous = entries[-1]
        try:
            result = self.service.reflection.reflect(
                previous, current, previous.get("evidence") or [])
        except Exception as error:
            self._last = f"检查失败：{str(error)[:80]}"
            self._emit()
            return None
        changes = result.changes or []
        fingerprint = hashlib.sha256(
            f"{result.status}|{previous.get('revision')}|{current.get('revision')}|"
            f"{[(row.get('trait'), row.get('from'), row.get('to')) for row in changes]}".encode("utf-8")
        ).hexdigest()[:16]
        latest = {"at": int(time.time()), "status": result.status,
                  "fingerprint": fingerprint, "changes": len(changes)}
        self._last_result = {**latest, "summary": result.summary,
                             "reasons": list(result.reasons), "changes": changes}
        if self.config is not None:
            self.config.set(self.config.enabled, self.config.interval, latest=latest)
        if result.status == "needs_review":
            self._last = f"需要复核：{result.summary}"
        elif changes:
            self._last = f"有界变化：{result.summary}"
        else:
            self._last = "人格没有可观察变化。"
        if result.status == "needs_review" and fingerprint != self._last_fingerprint:
            self._last_fingerprint = fingerprint
            try:
                self.service.record_audit(
                    "persona", "review", "periodic read-only drift check",
                    f"{len(changes)} changes", {"previous": int(previous.get("revision", 0)),
                                                "current": int(current.get("revision", 0))})
            except Exception:
                pass
            self.review_needed.emit({
                "summary": result.summary,
                "reasons": list(result.reasons),
                "changes": len(changes),
            })
        self._emit()
        return result

    def shutdown(self):
        # Keep an explicit opt-in across restart; only stop this timer.
        self.timer.stop()
