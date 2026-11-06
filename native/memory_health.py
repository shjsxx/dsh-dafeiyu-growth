"""Periodic, read-only memory health scanner.

It builds a curation plan from the real MemoryEngine records and never archives,
deletes or mutates memory. The optional config is an explicit opt-in; enabling
it only schedules scans, not curation commits.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from evolution_service import EvolutionService
from memory_engine import MemoryEngine

CONFIG_VERSION = 1
MIN_INTERVAL = 60
MAX_INTERVAL = 7 * 24 * 60 * 60
DEFAULT_INTERVAL = 6 * 60 * 60


def default_config_path():
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "memory-health.json"


def _bounded(value):
    try:
        return max(MIN_INTERVAL, min(MAX_INTERVAL, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL


class MemoryHealthConfig:
    def __init__(self, path=None, clock=None):
        self.path = Path(path).expanduser() if path is not None else default_config_path()
        self._clock = clock or (lambda: time.time())
        self.enabled = False
        self.interval = DEFAULT_INTERVAL
        self.last_scan = 0
        self.last_fingerprint = ""
        self.counts = {}
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
        self.last_fingerprint = str(raw.get("fingerprint") or "")[:32]
        counts = raw.get("counts") if isinstance(raw.get("counts"), dict) else {}
        self.counts = {str(key)[:16]: max(0, int(value)) for key, value in list(counts.items())[:8]
                       if isinstance(value, (int, float)) and not isinstance(value, bool)}
        self.status = "ready"

    def set(self, enabled, interval, latest=None):
        self.enabled = bool(enabled)
        self.interval = _bounded(interval)
        if isinstance(latest, dict):
            self.last_scan = max(0, int(latest.get("createdAt") or self._clock()))
            self.last_fingerprint = str(latest.get("fingerprint") or "")[:32]
            counts = latest.get("counts") if isinstance(latest.get("counts"), dict) else {}
            self.counts = {str(key)[:16]: max(0, int(value)) for key, value in list(counts.items())[:8]
                           if isinstance(value, (int, float)) and not isinstance(value, bool)}
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({
                "version": CONFIG_VERSION, "enabled": self.enabled, "interval": self.interval,
                "lastScan": self.last_scan, "fingerprint": self.last_fingerprint,
                "counts": self.counts, "updatedAt": int(self._clock()),
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


class MemoryHealthHost(QObject):
    status_changed = Signal(dict)

    def __init__(self, service=None, memory=None, config=None, parent=None):
        super().__init__(parent)
        self.service = service or EvolutionService()
        self.memory = memory or MemoryEngine()
        self.config = config
        self.latest = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.scan)
        self._last = "未启用；只扫描，不改记忆。"

    def state(self):
        latest = self.latest or {}
        counts = latest.get("counts") or (self.config.counts if self.config is not None else {})
        return {
            "enabled": bool(self.config.enabled) if self.config is not None else self.timer.isActive(),
            "interval": int(self.config.interval if self.config is not None else DEFAULT_INTERVAL),
            "last": self._last,
            "records": int(latest.get("records", 0)),
            "counts": dict(counts or {}),
            "fingerprint": str(latest.get("fingerprint") or ""),
            "persisted": self.config is not None,
        }

    def _emit(self):
        self.status_changed.emit(self.state())

    def configure(self, enabled, interval):
        interval = _bounded(interval)
        if self.config is not None:
            self.config.set(enabled, interval, latest=self.latest)
        if enabled:
            self.timer.start(interval * 1000)
            self._last = "已启用；按间隔只生成整理计划，不会归档或删除。"
            self.scan()
        else:
            self.timer.stop()
            self._last = "已停用；不会再自动扫描。"
            self._emit()

    def scan(self):
        """Read-only scan; the only writes are the small config/report fields."""
        try:
            # Re-read the same file so a curation/archive-restore performed in
            # another dialog is reflected without restarting the pet.
            path = getattr(self.memory, "path", None)
            records = (MemoryEngine(path) if path else MemoryEngine()).export_records()
            plan = self.service.curator.plan(records)
        except Exception as error:
            self._last = f"扫描失败：{str(error)[:80]}"
            self._emit()
            return None
        self.latest = plan
        counts = plan.get("counts") or {}
        if self.config is not None:
            self.config.set(self.config.enabled, self.config.interval, latest=plan)
        try:
            self.service.record_audit(
                "memory", "scanned", "periodic read-only health scan",
                f"{plan.get('records', 0)} records",
                {"archive": int(counts.get("archive", 0)),
                 "quarantine": int(counts.get("quarantine", 0))})
        except Exception:
            pass
        self._last = (f"最近扫描：记录 {plan.get('records', 0)} 条；"
                      f"隔离 {counts.get('quarantine', 0)}，归档候选 {counts.get('archive', 0)}；未修改记忆。")
        self._emit()
        return plan

    def shutdown(self):
        # Persist the opt-in state across restart; stopping must not disable it.
        self.timer.stop()
