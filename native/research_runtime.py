"""Process-lifetime research scheduler host.

The host lives with the native companion process, not with the research dialog.
It is off by default, only selects already-approved tasks, runs one task at a
time, and stops taking new work when disabled or when the process exits.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from evolution_service import EvolutionService
from research_scheduler import ResearchScheduler, ResearchSchedulerConfig


class ResearchJob(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, service, task_id, parent=None):
        super().__init__(parent)
        self.service = service
        self.task_id = task_id

    def run(self):
        try:
            self.done.emit(self.service.run_research(self.task_id))
        except Exception as error:
            self.failed.emit(str(error))


class ResearchSchedulerHost(QObject):
    status_changed = Signal(dict)

    def __init__(self, service=None, parent=None, config=None):
        super().__init__(parent)
        self.service = service or EvolutionService()
        self.config = config
        interval = config.interval if config is not None else 60
        self.scheduler = ResearchScheduler(self.service.research, interval=interval)
        self.worker = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self._last = "未启用；只取已批准任务。"
        self._last_task = ""

    def state(self):
        return {
            "enabled": bool(self.scheduler.enabled),
            "interval": int(self.scheduler.interval),
            "busy": bool(self.worker and self.worker.isRunning()),
            "last": self._last,
            "lastTask": self._last_task,
            "persisted": self.config is not None,
        }

    def _emit(self):
        self.status_changed.emit(self.state())

    def configure(self, enabled, interval):
        self.scheduler.set_interval(interval)
        if self.config is not None:
            self.config.set(enabled, interval)
        if enabled:
            self.scheduler.enable()
            self.timer.start()
            self._last = "已启用；等待间隔到达后取一个已批准任务。"
        else:
            self.scheduler.disable()
            self.timer.stop()
            self._last = "已停用；不会再自动取任务。"
        self._emit()

    def _tick(self):
        if not self.scheduler.enabled or self.worker is not None:
            return
        task = self.scheduler.tick()
        if not task:
            self._last = "等待已批准任务；不会自动批准。"
            self._emit()
            return
        self._last_task = task["id"]
        self._last = f"已取任务 {task['id']}；运行中…"
        self._emit()
        self.worker = ResearchJob(self.service, task["id"], self)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._cleanup)
        self.worker.start()

    def _done(self, task):
        self.scheduler.postpone()
        self._last = f"任务 {task.get('id', '')} 已完成；按间隔等待下一个。"
        self._emit()

    def _failed(self, message):
        self.scheduler.postpone()
        self._last = f"任务失败：{str(message)[:80]}；不会自动重试。"
        self._emit()

    def _cleanup(self):
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def shutdown(self):
        self.timer.stop()
        self.scheduler.disable()
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(1500)
