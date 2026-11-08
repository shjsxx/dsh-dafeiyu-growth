"""Explicit owner-directed web learning dialog.

Sites are added by the owner. Learning is one GET at a time and is disabled
while a request is running. The dialog never crawls links or starts itself.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from owner_web_learning import WebLearningService

STYLE = """
QDialog { background: #F4F7F8; }
QLabel { color: #29404F; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QLabel#webTitle { font-size: 20px; font-weight: 700; color: #1D3A48; }
QLabel#webHint { color: #697D89; font-size: 12px; }
QLineEdit, QListWidget, QPlainTextEdit { background: #FFFFFF; border: 1px solid #D6E1E5;
    border-radius: 8px; padding: 7px; color: #22303F; font-size: 13px; }
QPushButton { padding: 8px 12px; border: 1px solid #C9D8DE; border-radius: 8px;
    background: #FFFFFF; color: #294B60; font-size: 13px; }
QPushButton:disabled { color: #9AAAB2; background: #F0F3F4; }
"""


class LearnWorker(QThread):
    done = Signal(object)

    def __init__(self, service, url, parent=None):
        super().__init__(parent)
        self.service = service
        self.url = url

    def run(self):
        try:
            self.done.emit(self.service.learn(self.url))
        except Exception as error:
            self.done.emit({"ok": False, "error": type(error).__name__})


class WebLearningDialog(QDialog):
    def __init__(self, parent=None, service=None, autonomous_host=None):
        super().__init__(parent)
        self.service = service or WebLearningService()
        self.autonomous_host = autonomous_host
        self.worker = None
        self.setWindowTitle("云岚 · 自选网站学习")
        self.setMinimumSize(600, 540)
        self.setStyleSheet(STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(9)

        title = QLabel("自选网站学习")
        title.setObjectName("webTitle")
        root.addWidget(title)
        hint = QLabel(
            "只学习你明确添加的公开网站；不扫描链接、不登录、不使用 Cookie。\n"
            "只抓一个页面，遵守 robots.txt，拒绝 localhost/内网地址和跨站重定向。\n"
            "每次成功抓取都会先写入训练日志和情感记录，再写入本地知识库。"
        )
        hint.setObjectName("webHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://example.com/article")
        row.addWidget(self.url, 1)
        self.add_button = QPushButton("添加来源")
        self.add_button.clicked.connect(self.add_source)
        row.addWidget(self.add_button)
        self.learn_button = QPushButton("开始学习")
        self.learn_button.clicked.connect(self.learn)
        row.addWidget(self.learn_button)
        root.addLayout(row)

        root.addWidget(QLabel("已添加来源"))
        self.sources = QListWidget()
        self.sources.setMaximumHeight(120)
        root.addWidget(self.sources)

        remove_row = QHBoxLayout()
        remove_row.addStretch(1)
        self.remove_button = QPushButton("移除选中来源")
        self.remove_button.clicked.connect(self.remove_source)
        remove_row.addWidget(self.remove_button)
        root.addLayout(remove_row)

        self.autonomous = QCheckBox("每15分钟自主选源最多3条（内置新闻/视频/情感/办公目录）")
        self.autonomous.setToolTip("系统从内置公开源目录自行选题；不抓取用户未指定的任意站点，不下载视频文件。")
        if self.autonomous_host is not None:
            self.autonomous.setChecked(bool(self.autonomous_host.state().get("enabled")))
        self.autonomous.toggled.connect(self._autonomous_toggled)
        root.addWidget(self.autonomous)
        self.autonomous_status = QLabel("自主模式未启用")
        self.autonomous_status.setObjectName("webHint")
        self.autonomous_status.setWordWrap(True)
        root.addWidget(self.autonomous_status)
        if self.autonomous_host is not None:
            self.autonomous_host.status_changed.connect(self._autonomous_state)
            self._autonomous_state(self.autonomous_host.state())

        root.addWidget(QLabel("记录"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("学习结果和情感/训练记录会显示在这里。")
        root.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        root.addLayout(buttons)
        self.refresh()

    def refresh(self):
        self.sources.clear()
        for row in self.service.sources.snapshot().get("sites", []):
            item = QListWidgetItem(f"{row.get('host', '')} · {row.get('label', '')}")
            item.setData(Qt.ItemDataRole.UserRole, row.get("host", ""))
            self.sources.addItem(item)

    def _set_busy(self, busy):
        self.learn_button.setEnabled(not busy)
        self.add_button.setEnabled(not busy)

    def _autonomous_toggled(self, enabled):
        if self.autonomous_host is None:
            self.autonomous_status.setText("自主主机未连接；只能手动学习。")
            return
        self.autonomous_host.configure(enabled, 15, 3)

    def _autonomous_state(self, state):
        state = state if isinstance(state, dict) else {}
        if state.get("enabled"):
            self.autonomous_status.setText(
                f"自主模式已启用 · 每 {state.get('interval', 15)} 分钟 · "
                f"最多 {state.get('batch', 3)} 条 · {state.get('last', '')}")
        else:
            self.autonomous_status.setText(f"自主模式未启用 · {state.get('last', '')}")

    def add_source(self):
        url = self.url.text().strip()
        if not url:
            return
        result = self.service.add_source(url)
        if result.get("ok"):
            self.log.appendPlainText(f"已添加来源：{url}")
            self.refresh()
        else:
            self.log.appendPlainText(f"添加失败：{result.get('error', 'unknown')}")

    def remove_source(self):
        item = self.sources.currentItem()
        if item is None:
            return
        host = item.data(Qt.ItemDataRole.UserRole)
        if self.service.sources.remove(host):
            self.log.appendPlainText(f"已移除来源：{host}")
            self.refresh()

    def learn(self):
        url = self.url.text().strip()
        if not url or self.worker is not None:
            return
        if not self.service.sources.allows(url):
            added = self.service.add_source(url)
            if not added.get("ok"):
                self.log.appendPlainText(f"来源未添加：{added.get('error', 'unknown')}")
                return
            self.refresh()
        self._set_busy(True)
        self.log.appendPlainText(f"学习中：{url}")
        self.worker = LearnWorker(self.service, url, self)
        self.worker.done.connect(self._done)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _worker_finished(self):
        self._set_busy(False)
        self.worker = None

    def _done(self, result):
        result = result if isinstance(result, dict) else {}
        if result.get("ok"):
            note = "已写入独立网页知识库" if result.get("knowledgeAdded") else "已记录，无新增网页知识"
            self.log.appendPlainText(
                f"完成：{result.get('title') or result.get('host')} · {result.get('chars', 0)} 字 · {note}")
            self.log.appendPlainText(f"预览：{str(result.get('text') or '')[:300]}")
        else:
            self.log.appendPlainText(f"失败：{result.get('error', 'unknown')}")
