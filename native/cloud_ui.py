"""Owner-facing cloud settings / confirmation dialogs (explicit, per message)."""
from __future__ import annotations

import urllib.parse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from cloud_chat import CloudConfigError, save_config

STYLE = """
    QDialog { background: #F4F6FA; }
    QLabel { color: #2B3846; font-family: "Microsoft YaHei UI"; font-size: 12px; background: transparent; }
    QLabel#cloudTitle { font-size: 16px; font-weight: 700; color: #1B2838; }
    QLabel#cloudHint { font-size: 11px; color: #7A8796; }
    QLabel#cloudError { font-size: 11px; color: #A34D58; }
    QLineEdit, QSpinBox, QPlainTextEdit { background: #FFFFFF; border: 1px solid #DCE3EB; border-radius: 8px; padding: 7px 9px; color: #22303F; }
    QPlainTextEdit { font-size: 12px; }
    QPushButton { background: #FFFFFF; color: #415066; border: 1px solid #DCE3EB; border-radius: 8px; padding: 7px 16px; }
    QPushButton:hover { background: #F1F6FB; }
    QPushButton#cloudPrimary { background: #E8F2F9; border-color: #C9DEEC; color: #2E5B78; font-weight: 600; }
    QPushButton#cloudDanger { color: #95505A; }
"""


class CloudSettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config or {})
        self.setWindowTitle("云岚 · 云端接口设置")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        title = QLabel("云端接口设置")
        title.setObjectName("cloudTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        hint = QLabel("独立接口，默认逐句确认；聊天窗可明确允许本次连续云聊。\n连续模式仅含当前窗口最近6条短消息，合计最多4000字；不含其他会话、文件、屏幕或工具调用，关闭窗口撤销。\n密钥只在本机加密保存。\n勾选“默认打开智能云端模式”后，聊天窗会优先选云端，但每条消息仍需确认。\n勾选“允许主动智能消息”后，空闲时最多每30分钟自动生成一条短消息；只发送时间/情绪/最近话题等最小上下文，可能产生调用费用。\n接口需兼容 OpenAI /chat/completions；地址可填 base URL，模型名要填接口提供方支持的名称。")
        hint.setObjectName("cloudHint")
        hint.setTextFormat(Qt.TextFormat.PlainText)
        hint.setWordWrap(True)
        form = QFormLayout()
        form.setSpacing(7)
        self.endpoint = QLineEdit(str(self.config.get("endpoint", "")))
        self.endpoint.setPlaceholderText("https://your-api.example/v1/chat/completions")
        self.model = QLineEdit(str(self.config.get("model", "")))
        self.model.setPlaceholderText("模型名，例如 deepseek-chat / deepseek-reasoner")
        self.tokens = QSpinBox()
        self.tokens.setRange(16, 1024)
        self.tokens.setValue(int(self.config.get("max_tokens", 256) or 256))
        self.tokens.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("已保存（留空保持原密钥）" if self.config.get("key_saved") else "API 密钥（可留空，取决于接口）")
        self.clear_key = QCheckBox("清除已保存的密钥")
        self.prefer_cloud = QCheckBox("默认打开智能云端模式（仍需逐条确认发送）")
        self.prefer_cloud.setChecked(self.config.get("prefer_cloud") is True)
        self.proactive_smart = QCheckBox("允许主动智能消息（空闲时自动调用云端，最多每30分钟一次）")
        self.proactive_smart.setChecked(self.config.get("proactive_smart") is True)
        form.addRow("接口地址", self.endpoint)
        form.addRow("模型", self.model)
        form.addRow("输出上限", self.tokens)
        form.addRow("密钥", self.key)
        form.addRow("", self.clear_key)
        form.addRow("", self.prefer_cloud)
        form.addRow("", self.proactive_smart)
        self.error = QLabel("")
        self.error.setObjectName("cloudError")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        if self.config.get("error"):
            self.error.setText(str(self.config["error"]))
        self.error.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存设置")
        save.setObjectName("cloudPrimary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addLayout(buttons)

    def _save(self):
        try:
            self.config = save_config(
                self.endpoint.text(), self.model.text(), int(self.tokens.value()),
                self.key.text(), path=self.config.get("path") or None,
                clear_key=self.clear_key.isChecked(),
                prefer_cloud=self.prefer_cloud.isChecked(),
                proactive_smart=self.proactive_smart.isChecked(),
            )
        except (CloudConfigError, OSError) as error:
            self.error.setText(str(error))
            return
        self.accept()


class CloudChatDialog(QDialog):
    def __init__(self, parent=None, name=""):
        super().__init__(parent)
        self.setWindowTitle("云端对话")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        title = QLabel("想云端说点什么？")
        title.setObjectName("cloudTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        hint = QLabel(f"{name}，下一屏会给你看完整发送内容，确认后才会联网。" if name else "下一屏会给你看完整发送内容，确认后才会联网。")
        hint.setObjectName("cloudHint")
        hint.setWordWrap(True)
        self.input = QLineEdit()
        self.input.setMaxLength(2000)
        self.input.setPlaceholderText("只发送这一句；不含当前对话历史")
        self.input.returnPressed.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        nxt = QPushButton("下一步")
        nxt.setObjectName("cloudPrimary")
        nxt.setDefault(True)
        nxt.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(nxt)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.input)
        layout.addLayout(buttons)
        self.input.setFocus()

    def ask(self):
        if self.exec() == QDialog.DialogCode.Accepted:
            return self.input.text().strip()
        return ""


class CloudConfirmDialog(QDialog):
    def __init__(self, endpoint, model, tokens, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认发送到云端")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(STYLE)
        host = urllib.parse.urlsplit(endpoint).hostname or endpoint
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        title = QLabel("本次将发送以下内容")
        title.setObjectName("cloudTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        meta = QLabel(f"仅此一段 · {host} · {model} · 最多 {tokens} tokens\n不包含其他会话、文件、屏幕、剪贴板或工具调用。")
        meta.setObjectName("cloudHint")
        meta.setTextFormat(Qt.TextFormat.PlainText)
        meta.setWordWrap(True)
        self.preview = QPlainTextEdit(text)
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(120)
        note = QLabel("本地只会记住这句里明确的名字/喜好，不保存原文。\n费用由该接口的提供方按实际用量计费；DSH 不提供价格或额度上限。")
        note.setObjectName("cloudHint")
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        send = QPushButton("确认发送")
        send.setObjectName("cloudPrimary")
        send.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(send)
        layout.addWidget(title)
        layout.addWidget(meta)
        layout.addWidget(self.preview)
        layout.addWidget(note)
        layout.addLayout(buttons)

    def confirm(self):
        return self.exec() == QDialog.DialogCode.Accepted
