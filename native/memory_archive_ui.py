"""Read-only archive review with explicit, backed-up restore."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout,
)

from memory_engine import KIND_LABEL, MemoryEngine
from state_backup import StateBackup


class MemoryArchiveDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.memory = MemoryEngine()
        self.setWindowTitle("云岚 · 记忆归档")
        self.setMinimumSize(560, 420)
        self.setStyleSheet(
            "QDialog{background:#F4F7F8;}"
            "QLabel{color:#273E50;font-family:'Microsoft YaHei UI';font-size:13px;}"
            "QListWidget{background:#FFFFFF;border:1px solid #D7E1E5;"
            "border-radius:8px;padding:7px;color:#22303F;font-size:13px;}"
            "QPushButton{padding:8px 12px;border:1px solid #C9D8DE;border-radius:8px;"
            "background:#FFFFFF;color:#294B60;font-size:13px;}"
            "QPushButton:disabled{color:#9AAAB2;background:#F0F3F4;}"
        )
        root = QVBoxLayout(self)
        root.setSpacing(9)
        title = QLabel("记忆归档")
        title.setStyleSheet("font-size:20px;font-weight:700;color:#1D3A48;")
        root.addWidget(title)
        hint = QLabel("归档记录不会进入日常召回。恢复会先创建状态备份，再写回本地记忆；"
                      "对应归档条目会被移除。所有动作都需要再次确认。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.rows = QListWidget()
        self.rows.itemSelectionChanged.connect(self._refresh_buttons)
        root.addWidget(self.rows, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.restore_button = QPushButton("恢复选中…")
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self.restore)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(self.restore_button)
        actions.addStretch(1)
        actions.addWidget(close)
        root.addLayout(actions)
        self.refresh()

    def refresh(self):
        selected = self.rows.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected is not None else None
        self.rows.clear()
        for row in self.memory.list_archive():
            label = KIND_LABEL.get(row["kind"], row["kind"])
            item = QListWidgetItem(f"{label} · {row['text'][:80]}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(f"原因：{row.get('reason') or '未记录'}\n来源：{row.get('source') or '未知'}")
            self.rows.addItem(item)
            if row["id"] == selected_id:
                self.rows.setCurrentItem(item)
        if self.rows.currentItem() is None and self.rows.count():
            self.rows.setCurrentRow(0)
        self.status.setText(f"归档 {self.rows.count()} 条；恢复需再次确认。" if self.rows.count() else
                            "本地归档为空。")
        self._refresh_buttons()

    def _refresh_buttons(self):
        self.restore_button.setEnabled(self.rows.currentItem() is not None)

    def restore(self):
        item = self.rows.currentItem()
        if item is None:
            return
        record_id = item.data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self, "恢复归档记录",
            "把选中的归档记录写回本地记忆，并从归档移除该条？\n会先创建状态备份。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            snapshot = StateBackup().create("before-archive-restore")
            if snapshot is None:
                raise RuntimeError("没有可备份的状态文件")
            restored = self.memory.restore_archive([record_id], approved=True)
            if not restored:
                raise RuntimeError("记录已存在、已被清除，或归档写入失败")
            self.refresh()
            self.status.setText(f"已恢复 1 条；状态备份：{snapshot.name}")
        except Exception as error:
            QMessageBox.warning(self, "恢复归档记录", str(error))
