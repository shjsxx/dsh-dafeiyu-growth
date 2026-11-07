"""Explicit research-task dialog for the native helper.

The dialog can propose a plan, approve it, pause/resume it, run it once, and
show a cited summary. It never starts research on open and never writes memory.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from evolution_service import EvolutionService
from memory_engine import MemoryEngine
from research_scheduler import ResearchScheduler
from state_backup import StateBackup


class ResearchWorker(QThread):
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


class ResearchDialog(QDialog):
    def __init__(self, parent=None, service=None, scheduler_host=None, autonomous_host=None):
        super().__init__(parent)
        self.service = service or EvolutionService()
        self.memory = MemoryEngine()
        self.worker = None
        self._scheduler_host = scheduler_host
        self._autonomous_host = autonomous_host
        self.setWindowTitle("云岚 · 研究任务")
        self.setMinimumSize(620, 560)
        self.setStyleSheet(
            "QDialog{background:#F4F7F8;}"
            "QLabel{color:#273E50;font-family:'Microsoft YaHei UI';font-size:13px;}"
            "QPlainTextEdit,QListWidget{background:#FFFFFF;border:1px solid #D7E1E5;"
            "border-radius:8px;padding:7px;color:#22303F;font-size:13px;}"
            "QPushButton{padding:8px 12px;border:1px solid #C9D8DE;border-radius:8px;"
            "background:#FFFFFF;color:#294B60;font-size:13px;}"
            "QPushButton:disabled{color:#9AAAB2;background:#F0F3F4;}"
        )
        root = QVBoxLayout(self)
        root.setSpacing(9)
        title = QLabel("研究任务")
        title.setStyleSheet("font-size:20px;font-weight:700;color:#1D3A48;")
        root.addWidget(title)
        hint = QLabel("只读取公开来源；研究任务使用固定来源目录，自选网站学习使用你明确添加的网站。"
                      "提出后必须批准，运行一次才联网；摘要只作为未受信资料。")
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addWidget(QLabel("研究目标"))
        self.goal = QPlainTextEdit()
        self.goal.setPlaceholderText("例如：查清 Python 3.13 的自由线程构建有哪些官方说明")
        self.goal.setMaximumHeight(60)
        root.addWidget(self.goal)
        root.addWidget(QLabel("URL 计划（每行一个，必须已在公开白名单）"))
        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("https://docs.python.org/3/...")
        self.urls.setMaximumHeight(90)
        root.addWidget(self.urls)

        budget_row = QHBoxLayout()
        budget_row.addWidget(QLabel("最多请求"))
        self.requests = QSpinBox()
        self.requests.setRange(1, 20)
        self.requests.setValue(6)
        budget_row.addWidget(self.requests)
        budget_row.addWidget(QLabel("最多字节"))
        self.bytes = QSpinBox()
        self.bytes.setRange(1000, 5_000_000)
        self.bytes.setValue(500_000)
        self.bytes.setSingleStep(50_000)
        budget_row.addWidget(self.bytes)
        plan_button = QPushButton("自动规划")
        plan_button.clicked.connect(self.plan)
        budget_row.addWidget(plan_button)
        propose = QPushButton("提出计划")
        propose.clicked.connect(self.propose)
        budget_row.addWidget(propose)
        web_button = QPushButton("自选网站学习")
        web_button.clicked.connect(self.open_web_learning)
        budget_row.addWidget(web_button)
        budget_row.addStretch(1)
        root.addLayout(budget_row)

        root.addWidget(QLabel("任务"))
        self.tasks = QListWidget()
        self.tasks.itemSelectionChanged.connect(self.refresh_buttons)
        root.addWidget(self.tasks, 1)

        scheduler_row = QHBoxLayout()
        self.auto_schedule = QCheckBox("自动调度已批准任务")
        self.auto_schedule.setToolTip("只取已批准任务；不自动批准，不绕过预算，关闭窗口即停止。")
        self.auto_schedule.toggled.connect(self._toggle_scheduler)
        scheduler_row.addWidget(self.auto_schedule)
        scheduler_row.addWidget(QLabel("间隔"))
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(15, 3600)
        self.auto_interval.setValue(60)
        self.auto_interval.setSuffix(" 秒")
        self.auto_interval.valueChanged.connect(self._interval_changed)
        scheduler_row.addWidget(self.auto_interval)
        self.scheduler_status = QLabel("未启用；只取已批准任务。")
        self.scheduler_status.setWordWrap(True)
        scheduler_row.addWidget(self.scheduler_status, 1)
        root.addLayout(scheduler_row)
        self.scheduler = ResearchScheduler(self.service.research, interval=60)
        self.scheduler_timer = QTimer(self)
        self.scheduler_timer.setInterval(1000)
        self.scheduler_timer.timeout.connect(self._scheduler_tick)
        if self._scheduler_host is not None:
            try:
                self._scheduler_host.status_changed.connect(self._on_scheduler_host_status)
                state = self._scheduler_host.state()
                self._apply_scheduler_state(state)
            except (AttributeError, RuntimeError):
                self._scheduler_host = None

        actions = QHBoxLayout()
        self.approve_button = QPushButton("批准")
        self.approve_button.clicked.connect(self.approve)
        self.pause_button = QPushButton("暂停/恢复")
        self.pause_button.clicked.connect(self.pause_or_resume)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel)
        self.run_button = QPushButton("运行一次")
        self.run_button.clicked.connect(self.run_selected)
        self.summary_button = QPushButton("查看来源摘要")
        self.summary_button.clicked.connect(self.show_summary)
        self.commit_button = QPushButton("写入记忆…")
        self.commit_button.clicked.connect(self.commit_summary)
        for button in (self.approve_button, self.pause_button, self.cancel_button,
                       self.run_button, self.summary_button, self.commit_button):
            actions.addWidget(button)
        actions.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        root.addLayout(actions)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("运行状态与来源摘要会显示在这里。")
        self.output.setMaximumHeight(130)
        root.addWidget(self.output)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.refresh()

    def refresh(self):
        selected = self.selected_id()
        self.tasks.clear()
        for task in self.service.research.list():
            item = QListWidgetItem(
                f"{task.get('status', '?')} · {task.get('goal', '')} · "
                f"{task.get('used', 0)} 请求")
            item.setData(Qt.ItemDataRole.UserRole, task.get("id"))
            self.tasks.addItem(item)
            if task.get("id") == selected:
                self.tasks.setCurrentItem(item)
        if self.tasks.currentItem() is None and self.tasks.count():
            self.tasks.setCurrentRow(0)
        self.refresh_buttons()

    def selected_id(self):
        item = self.tasks.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def selected_task(self):
        task_id = self.selected_id()
        return next((row for row in self.service.research.list() if row.get("id") == task_id), None)

    def refresh_buttons(self):
        task = self.selected_task()
        status = task.get("status") if task else ""
        host_busy = bool(self._scheduler_host and self._scheduler_host.state().get("busy"))
        busy = bool(self.worker and self.worker.isRunning()) or host_busy
        self.approve_button.setEnabled(not busy and status == "proposed")
        self.pause_button.setEnabled(not busy and status in ("proposed", "approved", "paused"))
        self.cancel_button.setEnabled(not busy and status not in ("done", "failed", "cancelled", ""))
        self.run_button.setEnabled(not busy and status == "approved")
        self.summary_button.setEnabled(not busy and status == "done")
        self.commit_button.setEnabled(not busy and status == "done" and not task.get("committed"))

    def open_web_learning(self):
        from web_learning_ui import WebLearningDialog
        dialog = WebLearningDialog(self, autonomous_host=self._autonomous_host)
        dialog.exec()

    def plan(self):
        """Fill an editable allowlisted plan; this never proposes or fetches."""
        goal = self.goal.toPlainText().strip()
        if not goal:
            QMessageBox.information(self, "研究规划", "先写一个研究目标。")
            return
        try:
            plan = self.service.plan_research(goal)
        except Exception as error:
            QMessageBox.warning(self, "研究规划", str(error))
            return
        urls = list(plan.get("urls") or [])
        if not urls:
            self.status.setText(str(plan.get("note") or "没有匹配的公开来源。"))
            return
        self.urls.setPlainText("\n".join(urls))
        self.requests.setValue(int(plan.get("maxRequests") or 6))
        self.bytes.setValue(int(plan.get("maxBytes") or 500_000))
        titles = "、".join(row.get("title", "") for row in plan.get("sources", [])[:3])
        note = str(plan.get("note") or "")
        self.status.setText(f"已生成 {len(urls)} 个候选来源：{titles}。{note}")

    def propose(self):
        urls = [line.strip() for line in self.urls.toPlainText().splitlines() if line.strip()]
        try:
            task = self.service.propose_research(
                self.goal.toPlainText(), urls, self.requests.value(), self.bytes.value())
        except Exception as error:
            QMessageBox.warning(self, "研究计划", str(error))
            return
        self.status.setText(f"已提出 {task['id']}；请先批准，再运行一次。")
        self.refresh()

    def approve(self):
        task_id = self.selected_id()
        if not task_id:
            return
        try:
            self.service.approve_research(task_id)
        except Exception as error:
            QMessageBox.warning(self, "批准研究", str(error))
            return
        self.status.setText("已批准；点击“运行一次”才会联网。")
        self.refresh()

    def pause_or_resume(self):
        task = self.selected_task()
        if not task:
            return
        try:
            if task.get("status") == "paused":
                self.service.resume_research(task["id"])
            else:
                self.service.pause_research(task["id"])
        except Exception as error:
            QMessageBox.warning(self, "研究任务", str(error))
        self.refresh()

    def cancel(self):
        task_id = self.selected_id()
        if not task_id:
            return
        try:
            self.service.cancel_research(task_id)
        except Exception as error:
            QMessageBox.warning(self, "研究任务", str(error))
        self.refresh()

    def run_selected(self):
        task_id = self.selected_id()
        if not task_id or self.worker is not None:
            return
        if self._scheduler_host is not None and self._scheduler_host.state().get("enabled"):
            self._scheduler_host.configure(False, self.auto_interval.value())
            self.auto_schedule.blockSignals(True)
            self.auto_schedule.setChecked(False)
            self.auto_schedule.blockSignals(False)
            self.scheduler_status.setText("已暂停常驻调度，正在手动运行。")
        self._start_worker(task_id, manual=True)

    def _start_worker(self, task_id, manual=False):
        if self.worker is not None:
            return
        if manual and self._scheduler_host is not None:
            self._scheduler_host.configure(False, self.auto_interval.value())
        self.output.setPlainText("正在运行；只访问已批准的白名单 URL。")
        self.worker = ResearchWorker(self.service, task_id, self)
        self.worker.done.connect(self.worker_done)
        self.worker.failed.connect(self.worker_failed)
        self.worker.finished.connect(self.worker_cleanup)
        self.refresh_buttons()
        self.worker.start()

    def _apply_scheduler_state(self, state):
        self.auto_schedule.blockSignals(True)
        self.auto_schedule.setChecked(bool(state.get("enabled")))
        self.auto_schedule.blockSignals(False)
        self.auto_interval.blockSignals(True)
        self.auto_interval.setValue(int(state.get("interval") or 60))
        self.auto_interval.blockSignals(False)
        self.scheduler_status.setText(str(state.get("last") or "未启用；只取已批准任务。"))

    def _on_scheduler_host_status(self, state):
        self._apply_scheduler_state(state)
        self.refresh_buttons()

    def _toggle_scheduler(self, enabled):
        if self._scheduler_host is not None:
            self._scheduler_host.configure(enabled, self.auto_interval.value())
            return
        if enabled:
            self.scheduler.set_interval(self.auto_interval.value())
            self.scheduler.enable()
            self.scheduler_timer.start()
            self.scheduler_status.setText("已启用；只取已批准任务，间隔到达后运行一个。")
        else:
            self.scheduler_timer.stop()
            self.scheduler.disable()
            self.scheduler_status.setText("已停用；不会再自动取任务。")

    def _interval_changed(self, value):
        if self._scheduler_host is not None:
            state = self._scheduler_host.state()
            self._scheduler_host.configure(bool(state.get("enabled")), value)
            return
        self.scheduler.set_interval(value)

    def _scheduler_tick(self):
        if not self.auto_schedule.isChecked() or self.worker is not None:
            return
        task = self.scheduler.tick()
        if not task:
            self.scheduler_status.setText("等待已批准任务；不会自动批准。")
            return
        self.scheduler_status.setText(f"已取任务 {task['id']}；运行中…")
        self._start_worker(task["id"])

    def worker_done(self, task):
        self.output.setPlainText(str(task.get("summary") or task.get("error") or "已完成"))
        self.status.setText("研究完成；摘要未写入记忆。")
        if self.auto_schedule.isChecked():
            self.scheduler.postpone()
            self.scheduler_status.setText("上次任务已完成；按间隔等待下一个已批准任务。")
        self.refresh()

    def worker_failed(self, message):
        self.output.setPlainText(f"研究失败：{message}")
        self.status.setText("任务失败；没有写入记忆。")
        if self.auto_schedule.isChecked():
            self.scheduler.postpone()
            self.scheduler_status.setText("上次任务失败；调度器继续等待，不会重试。")
        self.refresh()

    def worker_cleanup(self):
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        self.refresh_buttons()

    def show_summary(self):
        task = self.selected_task()
        if not task or task.get("status") != "done":
            return
        # Read the stored summary without using the memory-export confirmation.
        row = next((row for row in self.service.research.tasks if row.get("id") == task["id"]), {})
        self.output.setPlainText(str(row.get("summary") or "没有摘要"))
        self.status.setText("这是未受信网页摘要；写入记忆需要后续单独确认。")

    def commit_summary(self):
        task = self.selected_task()
        if not task or task.get("status") != "done" or task.get("committed"):
            return
        answer = QMessageBox.question(
            self, "写入长期记忆",
            "把这份带来源的研究摘要写入本地长期记忆？\n"
            "会先创建状态备份；摘要标记为公开研究资料，不包含原始网页全文。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            snapshot = StateBackup().create("before-research-memory")
            if snapshot is None:
                raise RuntimeError("没有可备份的状态文件")
            row = next((item for item in self.service.research.tasks
                        if item.get("id") == task["id"]), None)
            if row is None:
                raise RuntimeError("研究任务不存在")
            text = str(row.get("summary") or "")
            citations = row.get("citations") or []
            if citations:
                text += "\n来源：" + " ".join(
                    str(c.get("url") or "") for c in citations[:5] if c.get("url"))
            if not self.memory.remember_research(text):
                raise RuntimeError("长期记忆没有写入成功")
            self.service.research.mark_committed(task["id"])
            self.service.record_audit("memory", "written", "explicit research memory write",
                                      task["id"], {"citations": len(citations)})
            self.status.setText(f"已写入长期记忆；恢复点：{snapshot.name}")
            self.output.setPlainText(text)
        except Exception as error:
            QMessageBox.warning(self, "写入记忆", str(error))
        self.refresh()


    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "研究任务", "研究正在运行，请等待完成后再关闭。")
            event.ignore()
            return
        self.scheduler_timer.stop()
        self.scheduler.disable()
        event.accept()
