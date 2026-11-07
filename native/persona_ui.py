"""Persona reflection/rollback review dialog.

It compares the current personality with the previous continuity anchor. The
rollback button is disabled unless an anchor exists; execution requires a
confirmation, a safety snapshot, and an explicit backup id.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from continuity import ContinuityStore
from evolution_service import EvolutionService
from persona_health import PersonaHealthHost
from persona_history import PersonaHistory
from personality_engine import PersonalityEngine
from state_backup import StateBackup


class PersonaReflectionDialog(QDialog):
    def __init__(self, parent=None, service=None, health_host=None):
        super().__init__(parent)
        self.service = service or EvolutionService()
        self.personality = PersonalityEngine()
        self.continuity = ContinuityStore()
        self.history = PersonaHistory()
        self._health_shared = health_host is not None
        self.health_host = health_host or PersonaHealthHost(
            self.service, self.personality, self.history, config=None, parent=self)
        self.previous = None
        self.current = None
        self.setWindowTitle("云岚 · 人格反思")
        self.setMinimumSize(620, 520)
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
        title = QLabel("人格反思")
        title.setStyleSheet("font-size:20px;font-weight:700;color:#1D3A48;")
        root.addWidget(title)
        hint = QLabel("只比较人格快照；回滚只恢复五维学习倾向，不改变诚实、自主、"
                      "不静默上传/删除等核心边界。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.summary = QLabel("正在读取连续性锚点…")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.changes = QListWidget()
        root.addWidget(self.changes, 1)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(120)
        root.addWidget(self.output)
        health_row = QHBoxLayout()
        self.health_enabled = QCheckBox("周期检查")
        self.health_enabled.setToolTip("只读对照最近快照；超过阈值只提示复核，不自动改人格。")
        self.health_enabled.toggled.connect(self._toggle_health)
        health_row.addWidget(self.health_enabled)
        health_row.addWidget(QLabel("间隔"))
        self.health_interval = QSpinBox()
        self.health_interval.setRange(1, 10080)
        self.health_interval.setValue(360)
        self.health_interval.setSuffix(" 分钟")
        self.health_interval.valueChanged.connect(self._health_interval_changed)
        health_row.addWidget(self.health_interval)
        self.health_status = QLabel("未启用；只读检查，不改人格。")
        self.health_status.setWordWrap(True)
        health_row.addWidget(self.health_status, 1)
        root.addLayout(health_row)
        actions = QHBoxLayout()
        refresh = QPushButton("重新分析")
        refresh.clicked.connect(self.refresh)
        self.rollback_button = QPushButton("回滚到上一锚点…")
        self.rollback_button.clicked.connect(self.rollback)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(refresh)
        actions.addWidget(self.rollback_button)
        actions.addStretch(1)
        actions.addWidget(close)
        root.addLayout(actions)
        self.health_host.status_changed.connect(self._apply_health_state)
        self._apply_health_state(self.health_host.state())
        self.refresh()

    def _apply_health_state(self, state):
        self.health_enabled.blockSignals(True)
        self.health_enabled.setChecked(bool(state.get("enabled")))
        self.health_enabled.blockSignals(False)
        self.health_interval.blockSignals(True)
        self.health_interval.setValue(max(1, int(state.get("interval") or 21600) // 60))
        self.health_interval.blockSignals(False)
        self.health_status.setText(str(state.get("last") or "未启用；只读检查，不改人格。"))

    def _toggle_health(self, enabled):
        self.health_host.configure(enabled, self.health_interval.value() * 60)

    def _health_interval_changed(self, minutes):
        state = self.health_host.state()
        self.health_host.configure(bool(state.get("enabled")), minutes * 60)

    def closeEvent(self, event):
        if not self._health_shared:
            self.health_host.shutdown()
        event.accept()

    def refresh(self):
        self.changes.clear()
        self.current = self.personality.snapshot()
        # Capture a changed snapshot for automatic review, but never invent
        # evidence for a change that came only from a passive state refresh.
        try:
            self.history.record(self.current, reason="review")
        except (OSError, ValueError, TypeError):
            pass
        automatic = self.history.entries(2)
        events = automatic[-1].get("evidence", []) if len(automatic) >= 2 else []
        if len(automatic) >= 2:
            previous_entry = automatic[-2]
            self.previous = {
                "revision": int(previous_entry.get("revision", 0)),
                "traits": dict(previous_entry.get("traits") or {}),
            }
            try:
                when = datetime.fromtimestamp(int(previous_entry.get("at", 0))).strftime("%m-%d %H:%M")
            except (OSError, OverflowError, ValueError):
                when = "历史记录"
            source = f"自动记录 {when} → 当前"
        else:
            anchors = self.continuity.data.get("anchors") if isinstance(self.continuity.data, dict) else {}
            days = sorted(anchors) if isinstance(anchors, dict) else []
            if len(days) < 2:
                self.previous = None
                self.summary.setText("还没有两个连续性锚点，暂时无法比较人格漂移。")
                self.rollback_button.setEnabled(False)
                return
            previous_day = days[-2]
            anchor = anchors[previous_day]
            self.previous = {"revision": int(anchor.get("personality", 0)),
                             "traits": dict(anchor.get("traits") or {})}
            source = f"连续性锚点 {previous_day}"
        try:
            result = self.service.reflection.reflect(self.previous, self.current, events)
        except Exception as error:
            self.summary.setText(f"人格快照不可比较：{error}")
            self.rollback_button.setEnabled(False)
            return
        self.summary.setText(f"{result.status} · 对照{source}：{result.summary}")
        for change in result.changes:
            item = QListWidgetItem(
                f"{change['label']} {change['from']:.1f} → {change['to']:.1f} "
                f"({change['delta']:+.1f}) · "
                f"{'有证据' if change['linked'] else '无结构化证据'}")
            self.changes.addItem(item)
        if not result.changes:
            self.changes.addItem(QListWidgetItem("没有可观察的五维变化。"))
        self.output.setPlainText("\n".join(result.reasons) or "没有额外复核原因。")
        self.rollback_button.setEnabled(bool(self.previous.get("traits")))

    def rollback(self):
        if not self.previous or not self.previous.get("traits"):
            return
        answer = QMessageBox.question(
            self, "回滚人格",
            "把五维学习倾向恢复到上一连续性锚点？\n会先创建状态备份，核心边界不变。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            snapshot = StateBackup().create("before-persona-rollback")
            if snapshot is None:
                raise RuntimeError("没有可备份的状态文件")
            if not self.personality.restore_traits(
                    self.previous["traits"], approved=True, backup_id=str(snapshot)):
                raise RuntimeError("人格回滚没有写入成功")
            self.service.record_audit("persona", "rolled_back", "explicit approved rollback",
                                      "five learned traits", {
                                          "from": int(self.previous.get("revision", 0)),
                                          "to": int(self.current.get("revision", 0)),
                                      })
            self.rollback_button.setEnabled(False)
            self.summary.setText(f"已恢复到上一锚点；备份：{snapshot.name}")
            self.output.setPlainText("只恢复了五维学习倾向；核心不变项没有被修改。")
        except Exception as error:
            QMessageBox.warning(self, "人格回滚", str(error))
