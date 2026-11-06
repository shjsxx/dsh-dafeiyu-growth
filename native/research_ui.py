"""Explicit research-task dialog for the native helper.

The dialog can propose a plan, approve it, pause/resume it, run it once, and
show a cited summary. It never starts research on open and never writes memory.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from evolution_service import EvolutionService
from memory_engine import MemoryEngine
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = EvolutionService()
        self.memory = MemoryEngine()
        self.worker = None
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
        hint = QLabel("只读取公开白名单来源；提出后必须批准，运行一次才联网。"
                      "摘要只作为未受信资料，写入记忆仍需下一次确认。")
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
        budget_row.addStretch(1)
        root.addLayout(budget_row)

        root.addWidget(QLabel("任务"))
        self.tasks = QListWidget()
        self.tasks.itemSelectionChanged.connect(self.refresh_buttons)
        root.addWidget(self.tasks, 1)

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
        busy = bool(self.worker and self.worker.isRunning())
        self.approve_button.setEnabled(not busy and status == "proposed")
        self.pause_button.setEnabled(not busy and status in ("proposed", "approved", "paused"))
        self.cancel_button.setEnabled(not busy and status not in ("done", "failed", "cancelled", ""))
        self.run_button.setEnabled(not busy and status == "approved")
        self.summary_button.setEnabled(not busy and status == "done")
        self.commit_button.setEnabled(not busy and status == "done" and not task.get("committed"))

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
        self.output.setPlainText("正在运行；只访问已批准的白名单 URL。")
        self.worker = ResearchWorker(self.service, task_id, self)
        self.worker.done.connect(self.worker_done)
        self.worker.failed.connect(self.worker_failed)
        self.worker.finished.connect(self.worker_cleanup)
        self.refresh_buttons()
        self.worker.start()

    def worker_done(self, task):
        self.output.setPlainText(str(task.get("summary") or task.get("error") or "已完成"))
        self.status.setText("研究完成；摘要未写入记忆。")
        self.refresh()

    def worker_failed(self, message):
        self.output.setPlainText(f"研究失败：{message}")
        self.status.setText("任务失败；没有写入记忆。")
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
        event.accept()
