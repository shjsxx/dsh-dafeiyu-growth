"""Explicit memory-curation review dialog.

The dialog reads the local memory, asks the curator for a proposal, and only
moves selected records to a local archive after an explicit confirmation and a
state backup. It never deletes the archive or mutates unsupported files.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from evolution_service import EvolutionService
from memory_engine import MemoryEngine
from memory_health import MemoryHealthHost
from state_backup import StateBackup


class MemoryCurationDialog(QDialog):
    def __init__(self, parent=None, service=None, health_host=None):
        super().__init__(parent)
        self.service = service or EvolutionService()
        self.memory = MemoryEngine()
        self._health_shared = health_host is not None
        self.health_host = health_host or MemoryHealthHost(self.service, self.memory, config=None, parent=self)
        self.plan = None
        self.setWindowTitle("云岚 · 记忆整理")
        self.setMinimumSize(600, 520)
        self.setStyleSheet(
            "QDialog{background:#F4F7F8;}"
            "QLabel{color:#273E50;font-family:'Microsoft YaHei UI';font-size:13px;}"
            "QListWidget,QPlainTextEdit{background:#FFFFFF;border:1px solid #D7E1E5;"
            "border-radius:8px;padding:7px;color:#22303F;font-size:13px;}"
            "QPushButton{padding:8px 12px;border:1px solid #C9D8DE;border-radius:8px;"
            "background:#FFFFFF;color:#294B60;font-size:13px;}"
            "QPushButton:disabled{color:#9AAAB2;background:#F0F3F4;}"
        )
        root = QVBoxLayout(self)
        root.setSpacing(9)
        title = QLabel("记忆整理")
        title.setStyleSheet("font-size:20px;font-weight:700;color:#1D3A48;")
        root.addWidget(title)
        hint = QLabel("只生成整理计划；批准后会先做状态备份，再把重复/冲突/过期记录移到本地 "
                      "memory-archive.json。不会删除归档，也不会碰其他文件。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.summary = QLabel("点击“生成计划”查看候选。")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.rows = QListWidget()
        root.addWidget(self.rows, 1)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(90)
        root.addWidget(self.output)
        health_row = QHBoxLayout()
        self.health_enabled = QCheckBox("周期扫描")
        self.health_enabled.setToolTip("只生成整理计划；不归档、不删除、不写入记忆。")
        self.health_enabled.toggled.connect(self._toggle_health)
        health_row.addWidget(self.health_enabled)
        health_row.addWidget(QLabel("间隔"))
        self.health_interval = QSpinBox()
        self.health_interval.setRange(1, 10080)
        self.health_interval.setValue(360)
        self.health_interval.setSuffix(" 分钟")
        self.health_interval.valueChanged.connect(self._health_interval_changed)
        health_row.addWidget(self.health_interval)
        self.health_status = QLabel("未启用；只扫描，不改记忆。")
        self.health_status.setWordWrap(True)
        health_row.addWidget(self.health_status, 1)
        root.addLayout(health_row)
        actions = QHBoxLayout()
        self.plan_button = QPushButton("生成计划")
        self.plan_button.clicked.connect(self.build_plan)
        self.commit_button = QPushButton("批准并整理…")
        self.commit_button.setEnabled(False)
        self.commit_button.clicked.connect(self.commit_plan)
        self.archive_button = QPushButton("查看归档…")
        self.archive_button.clicked.connect(self.open_archive)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(self.plan_button)
        actions.addWidget(self.commit_button)
        actions.addWidget(self.archive_button)
        actions.addStretch(1)
        actions.addWidget(close)
        root.addLayout(actions)
        self.health_host.status_changed.connect(self._apply_health_state)
        self._apply_health_state(self.health_host.state())

    def _apply_health_state(self, state):
        self.health_enabled.blockSignals(True)
        self.health_enabled.setChecked(bool(state.get("enabled")))
        self.health_enabled.blockSignals(False)
        interval = max(1, int(state.get("interval") or 21600) // 60)
        self.health_interval.blockSignals(True)
        self.health_interval.setValue(interval)
        self.health_interval.blockSignals(False)
        self.health_status.setText(str(state.get("last") or "未启用；只扫描，不改记忆。"))

    def _toggle_health(self, enabled):
        self.health_host.configure(enabled, self.health_interval.value() * 60)

    def _health_interval_changed(self, minutes):
        state = self.health_host.state()
        self.health_host.configure(bool(state.get("enabled")), minutes * 60)

    def closeEvent(self, event):
        if not self._health_shared:
            self.health_host.shutdown()
        event.accept()

    def open_archive(self):
        from memory_archive_ui import MemoryArchiveDialog
        MemoryArchiveDialog(self, service=self.service).exec()
        self.memory = MemoryEngine()
        self.health_host.memory = self.memory
        self.summary.setText("归档已重新读取；如记录有变化，请重新生成计划。")
        self.commit_button.setEnabled(False)
        self.plan = None

    def build_plan(self):
        try:
            records = self.memory.export_records()
            self.plan = self.service.curator.plan(records)
        except Exception as error:
            QMessageBox.warning(self, "记忆整理", str(error))
            return
        self.rows.clear()
        by_id = {str(row.get("id")): row for row in records}
        for action in self.plan.get("actions", []):
            if action.get("action") == "keep":
                continue
            record = by_id.get(str(action.get("id")), {})
            item = QListWidgetItem(
                f"{action.get('action')} · {str(record.get('text', ''))[:90]} · "
                f"{action.get('reason', '')}")
            item.setToolTip(str(record.get("text", "")))
            self.rows.addItem(item)
        counts = self.plan.get("counts", {})
        self.summary.setText(
            f"记录 {self.plan.get('records', 0)} 条；保留 {counts.get('keep', 0)}，"
            f"隔离 {counts.get('quarantine', 0)}，归档 {counts.get('archive', 0)}。"
            "确认前不会移动任何记录。")
        self.commit_button.setEnabled(bool(self.rows.count()))

    def commit_plan(self):
        if not self.plan:
            return
        answer = QMessageBox.question(
            self, "批准记忆整理",
            "把候选记录移入本地归档？\n会先创建状态备份；记录不会真正删除，"
            "可从 memory-archive.json 恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            snapshot = StateBackup().create("before-memory-curation")
            if snapshot is None:
                raise RuntimeError("没有可备份的状态文件")
            moved = {"quarantine": 0, "archive": 0}
            actions = self.plan.get("actions", [])

            def apply(selection):
                for mode, ids in selection.items():
                    if not ids:
                        continue
                    reasons = {}
                    for row in actions:
                        if row.get("action") == mode and row.get("id") in ids:
                            reasons[row["id"]] = row.get("reason", "")
                    for record_id in ids:
                        moved[mode] += 1 if self.memory.archive_records(
                            [record_id], mode, reasons.get(record_id, "")) else 0

            self.service.curator.commit(
                self.plan, approved=True, backup_id=str(snapshot), handler=apply)
            self.output.setPlainText(
                f"已隔离 {moved['quarantine']} 条、归档 {moved['archive']} 条。\n"
                f"状态备份：{snapshot.name}")
            self.plan = None
            self.commit_button.setEnabled(False)
            self.summary.setText("整理完成；原记录保留在本地归档中。")
        except Exception as error:
            QMessageBox.warning(self, "记忆整理", str(error))
