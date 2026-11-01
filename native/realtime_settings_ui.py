"""Native settings only: no API test, microphone, connection or upload consent.

Connect QDialog.accepted in the parent to revoke *all* active realtime sessions
and reload realtime_config.load_config(). This dialog keeps only public config;
accepted means a local save succeeded, never permission to send audio.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from realtime_config import ERRORS, RealtimeConfigError, public_config, save_config

STYLE = """
QDialog, QWidget#realtimeBody { background: #F4F6FA; }
QWidget { font-family: "Microsoft YaHei UI"; font-size: 13px; color: #243447; }
QLabel { background: transparent; }
QLabel#realtimeTitle { font-size: 18px; font-weight: 700; }
QLabel#realtimeStatus { color: #95505A; }
QLineEdit, QSpinBox, QComboBox {
    background: white; border: 1px solid #CDD7E3; border-radius: 7px;
    padding: 7px; min-height: 20px;
}
QPushButton {
    background: white; border: 1px solid #CDD7E3; border-radius: 7px;
    padding: 8px 14px; min-height: 20px;
}
QPushButton#realtimeSave { background: #E0EEF9; font-weight: 600; }
QScrollArea { border: none; background: transparent; }
"""


def _label(text, name=None):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


class RealtimeSettingsDialog(QDialog):
    """Scrollable, screen-bounded local API settings (normally 440 x 580).

    Public controls: endpoint/model/voice/key, max_tokens/session_seconds,
    protocol (itemData ga/beta), clear_key, status, save_button/cancel_button.
    self.config remains a public copy even after a successful explicit save.
    """

    def __init__(self, config, parent=None):
        super().__init__(parent)
        try:
            self.config = public_config(config or {})
        except RealtimeConfigError:
            # Retain the intended file target even when display fields are bad;
            # never redirect a failed edit to the default configuration file.
            fallback = {"error": ERRORS["corrupt"]}
            if isinstance(config, dict):
                fallback.update(path=config.get("path", ""), _revision=config.get("_revision"))
            self.config = public_config(fallback)
        self.setWindowTitle("云岚 · 实时语音 API 设置")
        self.setModal(True)
        self.setStyleSheet(STYLE)
        font = self.font()
        font.setPixelSize(13)
        self.setFont(font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(_label("实时语音 API 设置", "realtimeTitle"))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("realtimeBody")
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 8, 0)
        content.setSpacing(12)
        content.addWidget(_label(
            "优先使用 OpenAI Realtime（默认 GA）。这是独立于文字聊天的接口设置，"
            "不会读取文字聊天的地址或密钥；普通 /chat/completions 接口不一定支持实时语音。"
        ))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setSpacing(7)
        self.endpoint = QLineEdit(self.config["endpoint"])
        self.endpoint.setMaxLength(2048)
        self.endpoint.setPlaceholderText("wss://api.openai.com/v1/realtime")
        self.model = QLineEdit(self.config["model"])
        self.model.setMaxLength(100)
        self.model.setPlaceholderText("gpt-realtime")
        self.voice = QLineEdit(self.config["voice"])
        self.voice.setMaxLength(64)
        self.voice.setPlaceholderText("marin（以服务商支持的音色为准）")
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(16, 4096)
        self.max_tokens.setValue(self.config["max_tokens"])
        self.max_tokens.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.session_seconds = QSpinBox()
        self.session_seconds.setRange(60, 1800)
        self.session_seconds.setValue(self.config["session_seconds"])
        self.session_seconds.setSuffix(" 秒")
        self.session_seconds.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.protocol = QComboBox()
        self.protocol.addItem("GA（OpenAI 官方，推荐）", "ga")
        self.protocol.addItem("Beta（仅兼容服务商明确要求时）", "beta")
        self.protocol.setCurrentIndex(self.protocol.findData(self.config["protocol"]))
        self.key = QLineEdit()
        self.key.setMaxLength(8192)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(
            "已加密保存；地址不变时留空保留" if self.config["key_saved"] else
            "输入此实时接口的 API 密钥（不会显示旧密钥）"
        )
        self.clear_key = QCheckBox("清除密钥（并禁用环境密钥）")
        self.clear_key.setToolTip("选择后可保存无密钥设置；不会授权上传。")
        for title, widget in (("接口地址", self.endpoint), ("模型", self.model),
                              ("音色", self.voice), ("单次输出 tokens 上限", self.max_tokens),
                              ("单次会话时限", self.session_seconds), ("实时协议", self.protocol),
                              ("API 密钥", self.key)):
            form.addRow(_label(title), widget)
        content.addLayout(form)
        content.addWidget(self.clear_key)
        content.addWidget(_label(
            "安全提示：自定义接口提供方会收到原始麦克风音频和该接口的 API 密钥；"
            "请只填写你信任且确实支持 Realtime 的完整地址。HTTPS/WSS 自动转为 WSS，"
            "明文仅允许 localhost、127.0.0.1 或 [::1]。地址改变须重新输入密钥或明确清除。"
        ))
        content.addWidget(_label(
            "密钥使用当前 Windows 用户 DPAPI 加密保存；密钥输入框始终不回填旧密钥。"
            "OpenAI 在中国大陆的可用性不保证，需你自己的可用服务账号、权限及额度；费用由服务商收取。"
        ))
        content.addWidget(_label(
            "保存只写入本地独立配置：不会测试 API、打开麦克风或授权上传。"
            "开始会话前仍须在父窗口单独确认目标与音频上传；更改设置会撤销已有会话授权。"
        ))
        content.addStretch()
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)
        self.status = _label(self.config.get("error") or "尚未保存；不会联网或访问麦克风。", "realtimeStatus")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("realtimeSave")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        screen = self.screen()
        available = screen.availableGeometry() if screen is not None else None
        width = min(440, max(1, available.width() - 24)) if available else 440
        height = min(580, max(1, available.height() - 40)) if available else 580
        self.setMinimumSize(min(320, width), min(280, height))
        if available:
            self.setMaximumSize(max(1, available.width() - 24), max(1, available.height() - 40))
        self.resize(width, height)

    def reject(self):
        self.key.clear()
        super().reject()

    def _save(self):
        try:
            saved = save_config(
                self.endpoint.text(), self.model.text(), self.voice.text(),
                self.max_tokens.value(), self.session_seconds.value(), self.protocol.currentData(),
                key=self.key.text(), clear_key=self.clear_key.isChecked(),
                path=self.config.get("path") or None,
                expected_revision=self.config.get("_revision"),
            )
            # A successful atomic save must emit accepted even if reloading an
            # environment key reports an error: the parent must revoke sessions.
            self.config = public_config(saved)
            self.key.clear()
            self.status.setText("已保存本地设置；未连接 API、未访问麦克风，也未授权音频上传。")
        except RealtimeConfigError as error:
            self.status.setText(str(error))
            return
        except Exception:
            # OS/provider exceptions may contain paths, URLs or secrets. Never echo.
            self.status.setText(ERRORS["write"])
            return
        self.accept()
