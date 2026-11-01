"""Passive native live status: explicit toggles, fixed labels and numeric data."""
from __future__ import annotations

import math
import time
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

LABELS = {"joy": "开心", "sad": "低落", "anger": "生气", "anxious": "焦虑", "tired": "疲惫", "calm": "平静", "neutral": "线索不足"}


def seconds(value):
    if type(value) not in (int, float):
        return 0
    try:
        return value if 0 < value < 32_503_680_000 and math.isfinite(value) else 0
    except (ValueError, OverflowError):
        return 0


def millis(value):
    if type(value) not in (int, float):
        return 0
    return value / 1000 if 0 < value < 32_503_680_000_000 else 0


def stamp(value):
    if type(value) not in (int, float):
        return "尚无"
    try:
        if not math.isfinite(value) or not 0 < value < 32_503_680_000:
            return "尚无"
        return time.strftime("%m-%d %H:%M:%S", time.localtime(value))
    except (ValueError, OverflowError, OSError):
        return "尚无"


class OnlinePanel(QFrame):
    enabled_changed = Signal(bool)
    refresh_requested = Signal()
    curriculum_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("liveOnlinePanel")
        self.setStyleSheet('''
            QFrame#liveOnlinePanel { background:#FFFFFF; border:1px solid #D9E6E8; border-radius:12px; }
            QFrame#liveOnlinePanel QLabel { font-family:"Microsoft YaHei UI"; font-size:13px; color:#435E68; background:transparent; border:none; }
            QFrame#liveOnlinePanel QLabel#liveHeading { font-size:14px; font-weight:700; color:#11595F; background:#EFF8F5; padding:9px; border-radius:8px; }
            QFrame#liveOnlinePanel QLabel#liveHeading[state="warning"] { color:#A77739; background:#FFF5E5; }
            QFrame#liveOnlinePanel QLabel#liveHeading[state="error"] { color:#AF4C56; background:#FFF5E5; }
            QFrame#liveOnlinePanel QLabel[caption="true"] { font-size:12px; color:#627B84; }
            QFrame#liveOnlinePanel QPushButton { font-size:13px; padding:6px 9px; color:#11595F; background:white; border:1px solid #D9E6E8; border-radius:6px; }
            QFrame#liveOnlinePanel QPushButton:focus, QFrame#liveOnlinePanel QCheckBox:focus { border:2px solid #187E83; }
            QFrame#liveOnlinePanel QPushButton:disabled { color:#627B84; background:#F3F6F7; }
            QFrame#liveOnlinePanel QCheckBox { font-family:"Microsoft YaHei UI"; font-size:13px; color:#17343D; }
        ''')
        root = QVBoxLayout(self)
        root.setContentsMargins(11, 9, 11, 9)
        root.setSpacing(6)
        row = QHBoxLayout()
        self.toggle = QCheckBox("动态公开联网")
        self.toggle.toggled.connect(self.enabled_changed.emit)
        row.addWidget(self.toggle, 1)
        self.refresh_button = QPushButton("刷新动态")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        row.addWidget(self.refresh_button)
        self.curriculum_button = QPushButton("检查词典/课程")
        self.curriculum_button.clicked.connect(self.curriculum_requested.emit)
        root.addLayout(row)
        self.status = self._label("动态来源未连接", "liveHeading")
        self.timing = self._label("")
        self.comparison = self._label("情感对照：等待你直接说话；不上传原句。")
        self.note = self._label("动态源每5分钟；词典/课程每小时复核。模型在本机适应，收录不等于训练。")
        root.addWidget(self.status)
        self.timing.hide()  # Compatibility text; the visible presentation is a time grid.
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        self.time_values = {}
        for index, (key, caption) in enumerate((('source', '来源更新'), ('checked', '本机校验'), ('next', '下次检查'))):
            title = self._label(caption)
            title.setProperty('caption', True)
            value = self._label('尚无')
            grid.addWidget(title, index, 0)
            grid.addWidget(value, index, 1)
            self.time_values[key] = value
        root.addLayout(grid)
        self.note.setProperty('caption', True)
        for item in (self.comparison, self.note):
            root.addWidget(item)
        root.addWidget(self.curriculum_button, 0, Qt.AlignmentFlag.AlignLeft)

    @staticmethod
    def _label(text, name=""):
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        return label

    def update_state(self, info):
        info = info if isinstance(info, dict) else {}
        live = info.get("live") if isinstance(info.get("live"), dict) else {}
        curriculum = info.get("curriculum") if isinstance(info.get("curriculum"), dict) else {}
        comparison = info.get("comparison") if isinstance(info.get("comparison"), dict) else {}
        enabled = live.get("enabled") is True
        blocked = self.toggle.blockSignals(True)
        self.toggle.setChecked(enabled)
        self.toggle.blockSignals(blocked)
        self.toggle.setEnabled(not live.get("protected", False))
        working = live.get("working") is True
        self.refresh_button.setEnabled(enabled and not working and not live.get("protected", False))
        self.curriculum_button.setEnabled(not curriculum.get("busy", False) and not curriculum.get("protected", False))
        if not enabled:
            state = "动态公开联网已关闭"
        elif working:
            state = "USGS 动态资料 · 正在校验"
        elif live.get("errorCode"):
            state = "动态连接暂不可用 · 保留带时间的旧资料"
        elif seconds(live.get("lastSuccess")):
            age = time.time() - seconds(live.get("lastSuccess"))
            state = "USGS 动态资料 · 已连接" if age < 600 else "USGS 动态资料 · 缓存待刷新"
        else:
            state = "动态公开联网已开启 · 等待首次更新"
        self.status.setText(state)
        self.status.setProperty('state', 'error' if enabled and live.get('errorCode') else 'warning' if not enabled or not seconds(live.get('lastSuccess')) else 'ready')
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.time_values['source'].setText(stamp(millis(live.get('sourceUpdatedAtMs'))))
        self.time_values['checked'].setText(stamp(live.get('lastSuccess')))
        self.time_values['next'].setText(stamp(live.get('nextCheck')))
        self.timing.setText("来源更新 " + stamp(millis(live.get("sourceUpdatedAtMs")))
                            + "\n本机校验 " + stamp(live.get("lastSuccess")) + " · 下次 " + stamp(live.get("nextCheck")))
        model, public = comparison.get("modelLabel"), comparison.get("publicLabel")
        if isinstance(model, str) and isinstance(public, str) and model in LABELS and public in LABELS:
            self.comparison.setText(f"情感对照：本地模型 {LABELS[model]} / 公开词典 {LABELS[public]}\n两种线索，不是诊断或准确率；原句不联网。")
        else:
            self.comparison.setText("情感对照：等待你直接说话；不上传原句。")
