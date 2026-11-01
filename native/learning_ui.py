"""One explicit, owner-confirmed public lexicon fetch dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

STYLE = """
    QDialog { background: #F4F6FA; }
    QLabel { color: #2B3846; font-family: "Microsoft YaHei UI"; font-size: 12px; background: transparent; }
    QLabel#learnTitle { font-size: 16px; font-weight: 700; color: #1B2838; }
    QLabel#learnHint { font-size: 11px; color: #7A8796; }
    QLabel#learnError { font-size: 11px; color: #B4544A; }
    QLineEdit { background: #FFFFFF; border: 1px solid #DCE3EB; border-radius: 9px; padding: 8px 10px; font-size: 12px; }
    QLineEdit:focus { border-color: #8FBFD4; }
    QCheckBox { color: #415066; font-size: 11px; }
    QPushButton { background: #FFFFFF; color: #415066; border: 1px solid #DCE3EB; border-radius: 8px; padding: 6px 14px; }
    QPushButton:hover { background: #F1F6FB; }
    QPushButton#learnGo { background: #E8F2F9; border-color: #C9DEEC; color: #2E5B78; font-weight: 600; }
    QPushButton:disabled { color: #A6AFBA; background: #F7F9FB; }
"""


class LexiconLearningDialog(QDialog):
    """Returns a public JSON source only after an explicit confirmation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("联网学习情感词典")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(True)
        self.setMinimumWidth(430)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        title = QLabel("联网学习情感词典")
        title.setObjectName("learnTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        hint = QLabel(
            "只 GET 你填写的公开 JSON 词典；不发送聊天、名字、屏幕或本地状态。\n"
            "每次都要你确认；拒绝重定向，最大 256 KB。格式例如：\n"
            '{"joy":["开心","雀跃"],"sad":["难过","惆怅"]}'
        )
        hint.setObjectName("learnHint")
        hint.setTextFormat(Qt.TextFormat.PlainText)
        hint.setWordWrap(True)
        self.source = QLineEdit()
        self.source.setPlaceholderText("https://example.com/emotion-lexicon.json")
        self.source.setMaxLength(500)
        self.confirm = QCheckBox("我确认这是公开词典，不含个人聊天内容")
        self.error = QLabel("")
        self.error.setObjectName("learnError")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.go = QPushButton("拉取并学习")
        self.go.setObjectName("learnGo")
        self.go.setEnabled(False)
        self.go.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.go)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.source)
        layout.addWidget(self.confirm)
        layout.addWidget(self.error)
        layout.addLayout(buttons)
        self.confirm.toggled.connect(self.go.setEnabled)
        self.source.returnPressed.connect(self._accept)

    def _accept(self):
        if not self.go.isEnabled():
            return
        if not self.source.text().strip():
            self.error.setText("先填写一个公开词典地址。")
            return
        self.accept()

    def source_url(self) -> str:
        return self.source.text().strip()

    def ask(self) -> str:
        if self.exec() == QDialog.DialogCode.Accepted:
            return self.source_url()
        return ""
