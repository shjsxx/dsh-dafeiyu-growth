"""Compact, passive training journal for the native growth card.

Only explicit clicks emit ``learning_requested``. Construction and updates never
read files, create a learner/journal, or connect to the network. All displayed
metadata comes from fixed enums and finite numbers, not logs or source content.
"""
from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from training_journal import DOMAINS, ERRORS, KINDS, LABEL_VALUES, LIMIT, METRICS, SOURCES, STATUSES, LABEL_TEXT as ACTIVITY_LABELS

PAGE_SIZE = 30

# No UI/model integration imports: this module owns only a passive child widget.
PANEL_QSS = """
QWidget#trainingPanel { background: transparent; }
QWidget#trainingPanel QLabel { color: #435E68; background: transparent;
    font-family: "Microsoft YaHei UI"; font-size: 13px; }
QWidget#trainingPanel QLabel#trainingHeading { color: #17343D; font-size: 18px; font-weight: 700; }
QWidget#trainingPanel QLabel[caption="true"] { color: #627B84; font-size: 12px; }
QWidget#trainingPanel QLabel[section="true"] { color: #17343D; font-size: 13px; font-weight: 600; }
QWidget#trainingPanel QLabel#trainingWarning { color: #A77739; background: #FFF5E5;
    border: 1px solid #E9D7B7; border-radius: 8px; padding: 2px 8px; font-size: 12px; }
QWidget#trainingPanel QFrame#trainingSegments { background: #E7EFF0; border-radius: 9px; }
QWidget#trainingPanel QComboBox, QWidget#trainingPanel QPushButton {
    color: #435E68; background: #FFFFFF; border: 1px solid #D9E6E8;
    border-radius: 7px; padding: 2px 8px;
    font-family: "Microsoft YaHei UI"; font-size: 13px; }
QWidget#trainingPanel QPushButton:hover, QWidget#trainingPanel QComboBox:hover {
    background: #EFF8F5; border-color: #8CBAB9; }
QWidget#trainingPanel QPushButton:pressed { background: #DBEEEA; color: #11595F; }
QWidget#trainingPanel QPushButton:focus, QWidget#trainingPanel QComboBox:focus {
    border: 1px solid #187E83; }
QWidget#trainingPanel QPushButton:disabled, QWidget#trainingPanel QComboBox:disabled {
    color: #7D9299; background: #EDF2F3; border-color: #D9E6E8; }
QWidget#trainingPanel QPushButton[segment="true"] { color: #627B84; background: transparent;
    border: 1px solid transparent; padding: 0 12px; }
QWidget#trainingPanel QPushButton[segment="true"]:hover { color: #11595F; background: #EFF8F5; }
QWidget#trainingPanel QPushButton[segment="true"]:checked { color: #11595F;
    background: #FFFFFF; border-color: #D9E6E8; font-weight: 700; }
QWidget#trainingPanel QPushButton[segment="true"]:focus { border-color: #187E83; }
QWidget#trainingPanel QPushButton#trainingLearn { color: #FFFFFF; background: #187E83;
    border-color: #187E83; font-weight: 600; padding: 2px 14px; }
QWidget#trainingPanel QPushButton#trainingLearn:hover { background: #176F74; }
QWidget#trainingPanel QPushButton#trainingLearn:pressed { background: #11595F; }
QWidget#trainingPanel QPushButton#trainingLearn:focus { border: 2px solid #11595F; }
QWidget#trainingPanel QPushButton#trainingLearn:disabled { color: #7D9299;
    background: #E7EFF0; border-color: #D9E6E8; }
QWidget#trainingPanel QFrame[metricTile="true"], QWidget#trainingPanel QFrame#trainingSummaryCard,
QWidget#trainingPanel QFrame#trainingCoverage, QWidget#trainingPanel QFrame#trainingPlatforms {
    background: #FFFFFF; border: 1px solid #D9E6E8; border-radius: 12px; }
QWidget#trainingPanel QFrame[metricTile="true"][accent="true"] { background: #EFF8F5; border-color: #BFDCD5; }
QWidget#trainingPanel QLabel[pulseValue="true"] { color: #17343D; font-size: 24px; font-weight: 700; }
QWidget#trainingPanel QFrame[accent="true"] QLabel[pulseValue="true"] { color: #187E83; }
QWidget#trainingPanel QLabel[pulseValue="true"][compactValue="true"] { font-size: 18px; }
QWidget#trainingPanel QLabel[pulseCaption="true"] { color: #435E68; font-size: 12px; }
QWidget#trainingPanel QLabel[sourceState="indexed"], QWidget#trainingPanel QLabel[sourceState="connected"] {
    color: #11595F; background: #EFF8F5; border-radius: 5px; padding: 2px 6px; font-size: 12px; }
QWidget#trainingPanel QLabel[sourceState="available"] { color: #187E83;
    background: #EFF8F5; border-radius: 5px; padding: 2px 6px; font-size: 12px; }
QWidget#trainingPanel QLabel[sourceState="unknown"], QWidget#trainingPanel QLabel[sourceState="disabled"],
QWidget#trainingPanel QLabel[sourceState="pending"], QWidget#trainingPanel QLabel[sourceState="unconnected"] {
    color: #A77739; background: #FFF5E5; border-radius: 5px; padding: 2px 6px; font-size: 12px; }
QWidget#trainingPanel QLabel[sourceState="error"] { color: #AF4C56;
    background: #FCEFF0; border-radius: 5px; padding: 2px 6px; font-size: 12px; }
QWidget#trainingPanel QListWidget, QWidget#trainingPanel QPlainTextEdit {
    background: #FFFFFF; color: #435E68; border: 1px solid #D9E6E8;
    border-radius: 10px; padding: 5px;
    font-family: "Microsoft YaHei UI"; font-size: 13px;
    selection-background-color: #DBEEEA; selection-color: #11595F; }
QWidget#trainingPanel QListWidget:focus, QWidget#trainingPanel QPlainTextEdit:focus { border-color: #187E83; }
QWidget#trainingPanel QListWidget::item { padding: 8px; border: none; }
QWidget#trainingPanel QListWidget::item:hover { background: #F3F6F7; border-radius: 6px; }
QWidget#trainingPanel QListWidget::item:selected { background: #DBEEEA; color: #11595F; border-radius: 6px; }
QWidget#trainingPanel QScrollBar:vertical { background: transparent; width: 6px; margin: 3px 0; }
QWidget#trainingPanel QScrollBar::handle:vertical { background: #B4CDCF; border-radius: 3px; min-height: 24px; }
QWidget#trainingPanel QScrollBar::handle:vertical:hover { background: #187E83; }
QWidget#trainingPanel QScrollBar::add-line:vertical, QWidget#trainingPanel QScrollBar::sub-line:vertical { height: 0; }
QWidget#trainingPanel QScrollBar::add-page:vertical, QWidget#trainingPanel QScrollBar::sub-page:vertical { background: transparent; }
"""

_METRIC_LABELS = {
    "samples": "样本数", "updates": "模型更新次数", "version": "模型版本",
    "features": "特征数", "confidence": "情绪分类置信度（不是质量）",
    "probability": "动作偏好概率", "loss": "本次样本目标交叉熵 loss",
    "records": "资料条目", "valence": "模拟愉悦度", "energy": "模拟精力",
    "trust": "模拟信任", "curiosity": "模拟好奇心", "public_cues": "公开情绪线索",
    "reward": "本次反馈奖励", "weight_steps": "权重更新步数", "trained": "已拟合标记",
    "adaptation_passes": "加性权重适应轮次（非反向传播）",
    "added": "新增条目", "changed": "变更条目", "removed": "移除条目",
    "validated": "校验条目", "rejected": "拒绝条目", "bytes": "资料字节数",
    "duration_ms": "用时（毫秒）", "event_count": "互动计数", "characters": "临时文字字符数（不保存原文）",
    "dataset_rows": "公开数据行数", "dataset_skipped": "过滤/跳过行数", "dataset_validation": "留出验证行数",
    "dataset_accuracy": "留出准确率",
    "dataset_accuracy_before": "更新前留出准确率", "dataset_accuracy_after": "更新后留出准确率",
    "dataset_balanced": "平衡准确率",
    "dataset_balanced_before": "更新前平衡准确率", "dataset_balanced_after": "更新后平衡准确率",
    "dataset_macro_f1": "macro F1",
    "dataset_macro_f1_before": "更新前 macro F1", "dataset_macro_f1_after": "更新后 macro F1",
    "dataset_duplicates": "重复行", "dataset_conflicts": "冲突标签行",
    "dataset_classes": "覆盖类别数", "dataset_min_class": "最小类别样本",
    "changed_weights": "变化权重项",
    "audio_input_bytes": "音频发送字节数（交给套接字）", "audio_output_bytes": "收到的音频字节数", "played_ms": "设备实际处理播报（毫秒）",
    "input_tokens": "服务报告输入 tokens", "output_tokens": "服务报告输出 tokens", "input_audio_tokens": "其中音频输入 tokens", "output_audio_tokens": "其中音频输出 tokens", "cached_tokens": "其中缓存输入 tokens",
}
_LABEL_TEXT = {
    "action": ("动作", {"rest": "休息", "run": "跑动", "hop": "跳跃", "talk": "说话", "spin": "转圈"}),
    "emotion": ("情绪标签", {"joy": "喜悦", "sad": "低落", "anger": "生气", "anxious": "焦虑", "tired": "疲惫", "calm": "平静", "neutral": "中性"}),
    "own_mood": ("模拟状态", {"relaxed": "放松", "cheerful": "愉快", "curious": "好奇", "sleepy": "困倦", "comforting": "安慰", "shy": "害羞", "startled": "惊讶"}),
}
for _key, _caption in (('voice_event', '语音事件'), ('computer_action', '固定操作'), ('decision', '安全决定'), ('rt_reason', '实时接口状态')):
    _LABEL_TEXT[_key] = (_caption, {value: ACTIVITY_LABELS[value] for value in LABEL_VALUES[_key]})

_NOTES = {
    'voice_io': '说明：本机语音或独立实时API均不是声学/合成模型训练。服务报告的tokens不等于账单；播放处理不证明本人听到。云端转写未核对，不自动拟合或触发操作；本机识别分数未校准。',
    'computer_control': '说明：仅用户直接命令可提出固定操作，原生按钮逐项确认；执行次数不等于模型训练。',
    "action_fit": "说明：loss 仅是本次样本目标交叉熵，不是测试准确率；前后概率是动作偏好，不代表实际成功率。",
    "emotion_fit": "说明：情绪置信度是分类倾向，不是情绪质量或心理诊断；在线拟合不代表泛化准确率提升。",
    "emotion_read": "说明：仅观察，未训练；情绪置信度是分类倾向，不是情绪质量或心理诊断。",
    "public_lexicon": "说明：公开词典的下载与收录，不是神经网络训练。",
    "knowledge_index": "说明：知识资料的下载、校验与索引，不是神经网络训练。",
    "companion_adapt": "说明：仅为模拟陪伴状态更新，不是真实情绪，也不是神经网络训练。",
    "control": "说明：模型管理操作，不是训练提升；重置或回滚不代表学得更好。",
}
_BRAIN_STATUSES = {"new": "新模型", "ready": "就绪", "read_only": "只读", "protected": "受保护", "error": "错误"}
# Presentation only. These IDs/statuses can neither add an endpoint nor grant access.
_SOURCE_CATALOG = {
    "cldr_zh": ("Unicode CLDR · 中文词典", "Unicode 官方公开 JSON；只收录固定词典字段。"),
    "core_curriculum": ("内置基础知识课程", "本地课程，无需联网。"),
    "countries": ("Countries · 国家资料", "Countries 项目公开数据；仅已审阅的国家/首都字段。"),
    "cldr_units": ("Unicode CLDR · 计量单位", "Unicode 官方公开 JSON；仅名称和书写格式。"),
    "mime_db": ("mime-db · 文件类型", "mime-db 项目公开数据；仅媒体类型与扩展名。"),
    "stackexchange": ("Stack Exchange · 公开问答", "Stack Exchange 官方公开 API；仅白名单元数据，不执行来源内容。"),
}
_SOURCE_STATES = {
    "unknown": "未校验", "available": "可读取", "connected": "已连接",
    "indexed": "已收录", "error": "暂不可用", "disabled": "已停用",
    "pending": "待启用", "unconnected": "未连接",
}
_PUBLIC_KNOWLEDGE = {"countries", "cldr_units", "mime_db", "stackexchange"}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and abs(value) <= 1e13 else None
    except (OverflowError, ValueError):
        return None


def _count(value):
    number = _number(value)
    return int(max(0, number)) if number is not None else 0


def _enum(value, choices):
    return value if isinstance(value, str) and value in choices else None


def _fmt(value):
    return str(int(value)) if value == int(value) else f"{value:.5g}"


def _stamp(value, short=False):
    if not value:
        return "—"
    try:
        return datetime.fromtimestamp(value).strftime("%m-%d %H:%M" if short else "%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "时间不可用"


def _events(raw):
    """Detach and whitelist even when the caller bypassed TrainingJournal."""
    result, seen = [], set()
    for row in raw[:LIMIT] if isinstance(raw, list) else ():
        if not isinstance(row, dict):
            continue
        kind, domain = _enum(row.get("kind"), KINDS), _enum(row.get("domain"), DOMAINS)
        source, status = _enum(row.get("source"), SOURCES), _enum(row.get("status"), STATUSES)
        number = _number(row.get("id"))
        if not all((kind, domain, source, status)) or number is None or number <= 0 or number != int(number):
            continue
        event_id = int(number)
        if event_id in seen:
            continue
        seen.add(event_id)
        metrics = {}
        raw_metrics = _mapping(row.get("metrics"))
        for key in sorted(METRICS):
            value = _number(raw_metrics.get(key))
            if value is not None:
                metrics[key] = value
        labels = {}
        raw_labels = _mapping(row.get("labels"))
        for key, values in LABEL_VALUES.items():
            value = _enum(raw_labels.get(key), values)
            if value is not None:
                labels[key] = value
        result.append({
            "id": event_id, "started": max(0, _number(row.get("started")) or 0),
            "finished": max(0, _number(row.get("finished")) or 0),
            "kind": kind, "domain": domain, "source": source, "status": status,
            "metrics": metrics, "labels": labels, "error": _enum(row.get("error"), ERRORS) or "",
        })
    return result


def _catalog(raw):
    """Ignore arbitrary names, descriptions, endpoints, platform states and text."""
    result = {}
    for row in raw[:32] if isinstance(raw, list) else ():
        if not isinstance(row, dict):
            continue
        source = _enum(row.get("id"), _SOURCE_CATALOG)
        state = _enum(row.get("status"), _SOURCE_STATES)
        if source and state and source not in result:
            result[source] = state
    return result


def _detail(event):
    lines = [
        f"记录 #{event['id']} · {DOMAINS[event['domain']]}",
        f"开始：{_stamp(event['started'])}    结束：{_stamp(event['finished'])}",
        f"来源：{SOURCES[event['source']]}",
        f"类型：{KINDS[event['kind']]}    状态：{STATUSES[event['status']]}",
        _NOTES[event["kind"]],
    ]
    if event["error"]:
        lines.append(f"提示：{ERRORS[event['error']]}")
    for key, value in event["labels"].items():
        title, values = _LABEL_TEXT[key]
        lines.append(f"{title}：{values[value]}")
    metrics, done = event["metrics"], set()
    for key, value in metrics.items():
        if key in done:
            continue
        if key.endswith(("_before", "_after")):
            base = key.rsplit("_", 1)[0]
            before, after = base + "_before", base + "_after"
            left = _fmt(metrics[before]) if before in metrics else "—"
            right = _fmt(metrics[after]) if after in metrics else "—"
            lines.append(f"{_METRIC_LABELS[base]}：{left} → {right}")
            done.update((before, after))
        else:
            caption = '语音引擎分数（未校准，非准确率）' if key == 'confidence' and event['kind'] == 'voice_io' else _METRIC_LABELS[key]
            lines.append(f"{caption}：{_fmt(value)}")
            done.add(key)
    if not metrics:
        lines.append("指标：暂无数值记录。")
    return "\n".join(lines)


class TrainingPanel(QWidget):
    """Read-only journal with compact overview / records / sources segments.

    ``update_data`` accepts journal.snapshot(), knowledge/brain snapshots, an
    optional activeSource journal enum, and a sourceCatalog list of fixed
    {id, status} enums. Catalog state is presentation, never permission to fetch.
    Legacy platformSources is not trusted: platform adapters remain unconnected.
    An unchanged valid journal revision never resets items, selection or scroll.
    """

    learning_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("trainingPanel")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setStyleSheet(PANEL_QSS)
        self._revision = None
        self._events = []
        self._displayed = None
        self._page = 0
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        heading = QHBoxLayout()
        title = self._label("学习手账", "trainingHeading", False)
        title.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        heading.addWidget(title)
        heading.addStretch()
        self.learning_button = QPushButton("现在学习")
        self.learning_button.setObjectName("trainingLearn")
        self.learning_button.setFixedHeight(32)
        self.learning_button.setAutoDefault(False)
        self.learning_button.setToolTip("交给主程序的学习调度器；此面板不会自行训练或联网。")
        self.learning_button.clicked.connect(self.learning_requested.emit)
        heading.addWidget(self.learning_button)
        box.addLayout(heading)

        segments = QFrame()
        segments.setObjectName("trainingSegments")
        segments.setFixedHeight(32)
        segment_box = QHBoxLayout(segments)
        segment_box.setContentsMargins(3, 2, 3, 2)
        segment_box.setSpacing(3)
        self.view_buttons = {}
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        for index, title in enumerate(("总览", "记录", "来源")):
            button = QPushButton(title)
            button.setProperty("segment", True)
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setAccessibleName("学习手账 · " + title)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.view_group.addButton(button, index)
            self.view_buttons[title] = button
            segment_box.addWidget(button)
        box.addWidget(segments)

        self.warning_label = self._label(name="trainingWarning", wrap=False)
        self.warning_label.setFixedHeight(22)
        box.addWidget(self.warning_label)
        self.view_stack = QStackedWidget()
        self.view_stack.setMinimumWidth(0)
        self.view_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.overview_page = QWidget()
        self.records_page = QWidget()
        self.sources_page = QWidget()
        for page in (self.overview_page, self.records_page, self.sources_page):
            page.setMinimumWidth(0)
            self.view_stack.addWidget(page)
        self.view_group.idClicked.connect(self.view_stack.setCurrentIndex)
        self.view_stack.currentChanged.connect(self._view_changed)
        self.view_buttons["总览"].setChecked(True)
        box.addWidget(self.view_stack)
        self._build_overview()
        self._build_records()
        self._build_sources()
        self.update_data({})

    @staticmethod
    def _label(text="", name="", wrap=True):
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(wrap)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _set_label(label, text):
        if label.text() != text:
            label.setText(text)

    @staticmethod
    def _page_box(page):
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        return box

    def _view_changed(self, index):
        button = self.view_group.button(index)
        if button is not None:
            button.setChecked(True)

    def _build_overview(self):
        box = self._page_box(self.overview_page)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.pulse_values = {}
        self.pulse_captions = {}
        for index, (key, title, hint) in enumerate((
            ("action", "动作 SGD · 已提交步数", "来自审计累计权重更新步数，不是训练事件数量。"),
            ("emotion", "情感适应 · 累计轮次", "加性权重适应轮次，非反向传播；观察不计入。"),
            ("knowledge", "知识索引 · 当前条目", "知识库当前条目数，不是训练步数或下载次数。"),
            ("companion", "近期陪伴 · 状态调整", "保留记录中已完成的模拟状态调整次数；不是真实情绪。"),
        )):
            tile = QFrame()
            tile.setProperty("metricTile", True)
            tile.setProperty("accent", index == 0)
            tile.setFixedHeight(75)
            tile.setToolTip(hint)
            tile_box = QVBoxLayout(tile)
            tile_box.setContentsMargins(12, 4, 12, 4)
            tile_box.setSpacing(0)
            value = self._label("0", f"pulse-{key}", False)
            value.setProperty("pulseValue", True)
            caption = self._label(title, f"pulseCaption-{key}")
            caption.setProperty("pulseCaption", True)
            caption.setFixedHeight(32)
            tile_box.addWidget(caption)
            tile_box.addWidget(value)
            grid.addWidget(tile, index // 2, index % 2)
            self.pulse_values[key] = value
            self.pulse_captions[key] = caption
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)

        card = QFrame()
        card.setObjectName("trainingSummaryCard")
        card_box = QVBoxLayout(card)
        card_box.setContentsMargins(12, 7, 12, 7)
        card_box.setSpacing(1)
        self.summary_label = self._label(wrap=False)
        self.summary_label.setProperty("section", True)
        self.summary_label.setFixedHeight(20)
        self.knowledge_label = self._label()
        self.knowledge_label.setProperty("caption", True)
        self.knowledge_label.setFixedHeight(26)
        self.brain_label = self._label(wrap=False)
        self.brain_label.setProperty("caption", True)
        self.brain_label.setFixedHeight(16)
        self.evaluation_label = self._label(wrap=False)
        self.evaluation_label.setProperty("caption", True)
        self.evaluation_label.setFixedHeight(16)
        self.evaluation_label.setToolTip("情感指标来自确定的分层留出集；动作多样性来自数值回放，不代表实际成功率。")
        self.public_import_label = self._label(wrap=False)
        self.public_import_label.setProperty("caption", True)
        self.public_import_label.setFixedHeight(16)
        self.source_label = self._label(wrap=False)
        self.source_label.setProperty("caption", True)
        self.source_label.setFixedHeight(16)
        for label in (self.summary_label, self.knowledge_label, self.brain_label,
                      self.evaluation_label, self.public_import_label, self.source_label):
            card_box.addWidget(label)
        box.addWidget(card)
        note = self._label("收录 ≠ 训练；情感适应不等于 SGD。\n只展示来源与数值，不保存私聊原文。")
        note.setProperty("caption", True)
        note.setFixedHeight(28)
        box.addWidget(note)
        box.addStretch()

    def _build_records(self):
        box = self._page_box(self.records_page)
        filters = QHBoxLayout()
        filters.setSpacing(7)
        self.domain_filter = QComboBox()
        self.domain_filter.setAccessibleName("按领域筛选")
        self.domain_filter.addItem("全部领域", "")
        for key, text in DOMAINS.items():
            self.domain_filter.addItem(text, key)
        self.kind_filter = QComboBox()
        self.kind_filter.setAccessibleName("按类型筛选")
        self.kind_filter.addItem("全部类型", "")
        for key, text in KINDS.items():
            self.kind_filter.addItem(text, key)
        for combo in (self.domain_filter, self.kind_filter):
            combo.setMinimumWidth(0)
            combo.setFixedHeight(28)
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            combo.currentIndexChanged.connect(self._filter_changed)
        filters.addWidget(self.domain_filter, 1)
        filters.addWidget(self.kind_filter, 2)
        box.addLayout(filters)

        self.history_list = QListWidget()
        self.history_list.setObjectName("trainingHistory")
        self.history_list.setAccessibleName("学习记录（最新在前）")
        self.history_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.history_list.setFixedHeight(150)
        self.history_list.setMinimumWidth(0)
        self.history_list.currentItemChanged.connect(self._selection_changed)
        self.history_empty_label = self._label("暂无匹配记录\n可切换领域或类型；学习后会显示审计摘要。")
        self.history_empty_label.setProperty("caption", True)
        self.history_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_box = QVBoxLayout(self.history_list.viewport())
        empty_box.setContentsMargins(16, 12, 16, 12)
        empty_box.addWidget(self.history_empty_label)
        box.addWidget(self.history_list)

        pages = QHBoxLayout()
        self.newer_button = QPushButton("较新")
        self.older_button = QPushButton("较早")
        for button in (self.newer_button, self.older_button):
            button.setAutoDefault(False)
            button.setFixedHeight(26)
        self.newer_button.clicked.connect(lambda: self._change_page(-1))
        self.older_button.clicked.connect(lambda: self._change_page(1))
        self.page_label = self._label(wrap=False)
        self.page_label.setProperty("caption", True)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pages.addWidget(self.newer_button)
        pages.addWidget(self.page_label, 1)
        pages.addWidget(self.older_button)
        box.addLayout(pages)

        self.detail_view = QPlainTextEdit()
        self.detail_view.setObjectName("trainingDetails")
        self.detail_view.setAccessibleName("所选记录详情（只读）")
        self.detail_view.setReadOnly(True)
        self.detail_view.setUndoRedoEnabled(False)
        self.detail_view.setFixedHeight(110)
        self.detail_view.setMinimumWidth(0)
        self.detail_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        box.addWidget(self.detail_view)

    def _build_sources(self):
        box = self._page_box(self.sources_page)
        self.coverage_label = self._label(name="coverageDetail", wrap=False)
        self.coverage_label.setFixedHeight(22)
        box.addWidget(self.coverage_label)
        coverage = QFrame()
        coverage.setObjectName("trainingCoverage")
        rows = QVBoxLayout(coverage)
        rows.setContentsMargins(12, 8, 12, 8)
        rows.setSpacing(4)
        self.source_status_labels = {}
        for key, (title, hint) in _SOURCE_CATALOG.items():
            row = QWidget()
            row.setFixedHeight(28)
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(5)
            name = self._label(title, wrap=False)
            name.setToolTip(hint)
            line.addWidget(name, 1)
            state = self._label("未校验", wrap=False)
            state.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            state.setToolTip(hint + "\n此处状态来自快照，不代表实时网络连通。")
            self.source_status_labels[key] = state
            line.addWidget(state)
            rows.addWidget(row)
        box.addWidget(coverage)

        platforms = QFrame()
        platforms.setObjectName("trainingPlatforms")
        platform_box = QVBoxLayout(platforms)
        platform_box.setContentsMargins(10, 7, 10, 7)
        platform_box.setSpacing(2)
        title = self._label("待接入 · 平台公开来源", wrap=False)
        title.setProperty("caption", True)
        title.setFixedHeight(16)
        platform_box.addWidget(title)
        self.platform_label = self._label("知乎公开源：未连接  ·  短视频公开元数据：未连接", "coveragePlatforms", False)
        self.platform_label.setFixedHeight(22)
        self.platform_label.setProperty("sourceState", "unconnected")
        self.platform_label.setToolTip("仅为未接入入口，没有平台读取能力；只允许经审核的官方公开 API/RSS。")
        platform_box.addWidget(self.platform_label)
        box.addWidget(platforms)
        policy = self._label("仅固定公开数据；不抓取登录页或私密内容，\n不下载视频、不绕过验证，不执行来源指令。")
        policy.setProperty("caption", True)
        policy.setFixedHeight(32)
        box.addWidget(policy)
        box.addStretch()

    def update_data(self, info):
        """Update whitelisted view state without initiating any work."""
        info = _mapping(info)
        journal = _mapping(info.get("journal"))
        totals = _mapping(journal.get("totals"))
        knowledge, brain = _mapping(info.get("knowledge")), _mapping(info.get("brain"))
        revision = _number(journal.get("revision"))
        revision = int(revision) if revision is not None and revision >= 0 and revision == int(revision) else None
        same_revision = revision is not None and revision == self._revision
        rows = self._events if same_revision else _events(journal.get("events"))
        runs = _count(totals.get("runs", journal.get("total")))
        steps = _count(totals.get("weight_steps"))
        passes = _count(totals.get("emotion_adaptation_passes"))
        count = _count(knowledge.get("count"))
        recent_companion = sum(row["kind"] == "companion_adapt" and row["status"] == "success" for row in rows)
        for key, value in (("action", steps), ("emotion", passes), ("knowledge", count), ("companion", recent_companion)):
            label = self.pulse_values[key]
            self._set_label(label, str(value))
            compact = len(str(value)) > 8
            if label.property("compactValue") != compact:
                label.setProperty("compactValue", compact)
                label.style().unpolish(label)
                label.style().polish(label)
        self._set_label(self.summary_label, f"已记录 {runs} 次事件 · 失败 {_count(totals.get('failed'))}")
        domains = _mapping(knowledge.get("domains"))
        facts = " · ".join(f"{text} {_count(domains.get(key))}" for key, text in DOMAINS.items() if _count(domains.get(key)))
        self._set_label(self.knowledge_label, f"知识库 {count} 条" + (f" · {facts}" if facts else ""))
        self.knowledge_label.setToolTip(self.knowledge_label.text())
        brain_status = _enum(brain.get("status"), _BRAIN_STATUSES)
        def number_or_zero(value):
            number = _number(value)
            return 0 if number is None else number
        self._set_label(self.brain_label,
                        f"动作模型：样本 {_count(brain.get('samples'))} · 更新 {_count(brain.get('updates'))} · "
                        f"v{_count(brain.get('version'))} · {_BRAIN_STATUSES.get(brain_status, '暂无状态')} · "
                        f"多样性 {number_or_zero(brain.get('diversity')):.2f} · 均奖励 {number_or_zero(brain.get('avg_reward')):+.2f}")
        evaluation = _mapping(info.get("evaluation"))
        if evaluation:
            value = _number(evaluation.get("accuracy"))
            accuracy = f"{int(round(value * 100))}%" if value is not None else "—"
            value = _number(evaluation.get("balanced_accuracy"))
            balanced = f"{int(round(value * 100))}%" if value is not None else "—"
            self._set_label(self.evaluation_label,
                            f"情感留出：准确率 {accuracy} · 平衡 {balanced} · "
                            f"macro F1 {number_or_zero(evaluation.get('macro_f1')):.2f} · "
                            f"验证 {_count(evaluation.get('support'))}")
            if evaluation.get("reason") == "no_improvement":
                self.evaluation_label.setToolTip("上次公开数据更新未达到采纳门槛；权重未变，这里显示的是当前模型的留出评估。")
            else:
                self.evaluation_label.setToolTip("来自确定的分层留出集；不是心理诊断或泛化保证。")
        else:
            self._set_label(self.evaluation_label, "情感留出：尚无评估；完成一次公开数据学习后显示。")
        self.summary_label.setToolTip(
            f"动作 SGD {steps} 步 · 情感适应 {passes} 次（加性适应，非反向传播）\n" + self.brain_label.text() +
            f"\n本记录期累计新增知识 {_count(totals.get('knowledge_added'))} 条。\n"
            "统计从记录功能启用起；不伪造旧训练。最近30天且最多2000条，早期累计数保留。")
        public_added = sum(_count(row["metrics"].get("added")) for row in rows
                           if row["kind"] == "knowledge_index" and row["status"] == "success" and row["source"] in _PUBLIC_KNOWLEDGE)
        lexicon_added = sum(_count(row["metrics"].get("added")) for row in rows
                            if row["kind"] == "public_lexicon" and row["status"] == "success" and row["source"] == "cldr_zh")
        self._set_label(self.public_import_label, f"近期公开收录 {public_added} 条 · 词典新增 {lexicon_added} 条")
        self.public_import_label.setToolTip("仅累计保留记录中成功导入的 added 数值；不把下载次数、内置课程或失败事件算作公开新增。")
        source = _enum(info.get("activeSource"), SOURCES)
        self._set_label(self.source_label, f"当前来源：{SOURCES[source]}" if source else "")
        self.source_label.setToolTip(self.source_label.text())
        self.source_label.setVisible(source is not None)

        catalog = _catalog(info.get("sourceCatalog"))
        indexed_sources = _mapping(knowledge.get("sources"))
        for key, label in self.source_status_labels.items():
            imported = key in indexed_sources or any(row["source"] == key and row["kind"] in ("knowledge_index", "public_lexicon")
                                                    and row["status"] in ("success", "no_change") for row in rows)
            state = catalog.get(key, "indexed" if imported else "unknown")
            self._set_label(label, _SOURCE_STATES[state])
            if label.property("sourceState") != state:
                label.setProperty("sourceState", state)
                label.style().unpolish(label)
                label.style().polish(label)
        indexed = sum(label.property("sourceState") == "indexed" for label in self.source_status_labels.values())
        self._set_label(self.coverage_label, f"固定公开 / 本地来源 · 已收录 {indexed} / {len(_SOURCE_CATALOG)} 项")
        # Never interpolate platformSources: its status/name fields may be raw text.
        self._set_label(self.platform_label, "知乎公开源：未连接  ·  短视频公开元数据：未连接")

        status = _enum(journal.get("status"), {"new", "ready", "protected", "read_only", "error"})
        read_only = status == "read_only" or journal.get("read_only") is True
        warnings = []
        if status == "protected":
            warnings.append("日志损坏或不可读：已保留原文件，新训练暂停，避免漏记。")
        elif status == "error":
            warnings.append("日志无法保存：未记录的新训练不会开始。")
        if read_only:
            warnings.append("只读预览：不会启动学习或修改日志。")
        if brain_status in ("read_only", "protected", "error"):
            warnings.append("动作模型只读或受保护；不能据此声称完成了权重更新。")
        self._set_label(self.warning_label, " · ".join(warnings))
        self.warning_label.setToolTip("\n".join(warnings))
        self.warning_label.setVisible(bool(warnings))
        busy = info.get("busy") is True
        self.learning_button.setEnabled(not busy and not read_only and status not in ("protected", "error"))
        self.learning_button.setText("学习中…" if busy else "现在学习")
        if same_revision:
            return
        self._revision = revision
        selected = self._selected_id()
        changed = rows != self._events
        self._events = rows
        if changed or self._displayed is None:
            filtered = self._filtered_events()
            if selected is not None:
                index = next((i for i, row in enumerate(filtered) if row["id"] == selected), None)
                if index is not None:
                    self._page = index // PAGE_SIZE
            self._render_page(selected, preserve_scroll=True)

    def _filtered_events(self):
        domain, kind = self.domain_filter.currentData(), self.kind_filter.currentData()
        return [row for row in self._events if (not domain or row["domain"] == domain) and (not kind or row["kind"] == kind)]

    def _selected_id(self):
        item = self.history_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _filter_changed(self, _index):
        self._page = 0
        self._render_page()

    def _change_page(self, delta):
        self._page += delta
        self._render_page()

    def _render_page(self, selected=None, preserve_scroll=False):
        rows = self._filtered_events()
        page_count = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
        self._page = max(0, min(self._page, max(0, page_count - 1)))
        start = self._page * PAGE_SIZE
        visible = rows[start:start + PAGE_SIZE]
        scroll = self.history_list.verticalScrollBar().value()
        if visible != self._displayed:
            blocked = self.history_list.blockSignals(True)
            try:
                self.history_list.clear()
                for event in visible:
                    text = (f"{_stamp(event['started'], short=True)} · {DOMAINS[event['domain']]} · {STATUSES[event['status']]}\n"
                            f"{KINDS[event['kind']]} · {SOURCES[event['source']]}")
                    item = QListWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, event["id"])
                    self.history_list.addItem(item)
                index = next((i for i, row in enumerate(visible) if row["id"] == selected), 0)
                self.history_list.setCurrentRow(index if visible else -1)
            finally:
                self.history_list.blockSignals(blocked)
            self._displayed = visible
            if preserve_scroll:
                self.history_list.verticalScrollBar().setValue(scroll)
        self.history_empty_label.setVisible(not visible)
        self.newer_button.setEnabled(self._page > 0)
        self.older_button.setEnabled(self._page + 1 < page_count)
        self._set_label(self.page_label, f"{self._page + 1 if page_count else 0} / {page_count} · {len(rows)} 条")
        self._selection_changed()

    def _selection_changed(self, *_args):
        selected = self._selected_id()
        event = next((row for row in self._events if row["id"] == selected), None)
        text = _detail(event) if event is not None else "暂无匹配记录。仅展示固定来源与数值指标，不保存或展示私聊原文。"
        if self.detail_view.toPlainText() != text:
            self.detail_view.setPlainText(text)
