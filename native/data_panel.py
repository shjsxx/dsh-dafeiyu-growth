"""Compact, passive knowledge/emotion dashboard for the native growth card.

Only fixed enums and bounded numeric metadata cross the display boundary.  The
caller owns snapshots; this module does not import a learner, construct a store,
read/write files, connect to a network, show windows, or request training.  The
only interaction switches two local, read-only views.  ``update_data(info)`` does
not change that selection and never retains arbitrary caller-owned payloads.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

MAX_COUNTER = 10 ** 13
EVENT_LIMIT = 2000
# These are display vocabularies, deliberately independent of model/IO modules.
DOMAIN_TEXT = {
    "geography": "地理", "astronomy": "天文", "science": "科学",
    "computing": "计算机", "general": "常识", "chemistry": "化学",
}
SOURCE_TEXT = {
    "cldr_zh": "表情词典", "core_curriculum": "基础课程",
    "countries": "国家资料", "cldr_units": "计量单位",
    "mime_db": "文件格式", "stackexchange": "技术问答",
}
PLATFORM_TEXT = {"zhihu_public": "知乎", "short_video_public": "短视频"}
SOURCE_STATE_TEXT = {
    "available": "可收录", "indexed": "已收录", "error": "暂不可用",
    "disabled": "已停用", "pending": "待接入", "unconnected": "未连接",
}
MODEL_STATE_TEXT = {
    "new": "新模型", "ready": "就绪", "read_only": "只读",
    "protected": "受保护", "error": "保存异常",
}
FINAL_STATE_TEXT = {
    "success": "完成", "no_change": "无新增", "observed": "仅观察",
    "skipped": "已跳过", "failed": "失败", "cancelled": "已取消",
    "interrupted": "提交未知",
}

PANEL_QSS = """
QWidget#dataPanel { background: transparent; }
QWidget#dataPanel QLabel { background: transparent; color: #435E68;
    font-family: "Microsoft YaHei UI"; font-size: 13px; }
QWidget#dataPanel QLabel#dataHeading { color: #17343D; font-size: 18px; font-weight: 700; }
QWidget#dataPanel QLabel#dataReadOnly { color: #627B84; font-size: 12px; }
QWidget#dataPanel QFrame#dataHeroCard { background: #EFF8F5;
    border: 1px solid #BFDCD5; border-radius: 12px; }
QWidget#dataPanel QFrame#dataHeroCard QLabel[dataValue="true"] {
    color: #11595F; font-size: 24px; font-weight: 700; }
QWidget#dataPanel QFrame#dataHeroCard QLabel[dataCaption="true"] { color: #435E68; font-size: 12px; }
QWidget#dataPanel QFrame[dataCard="true"] {
    background: #FFFFFF; border: 1px solid #D9E6E8; border-radius: 12px; }
QWidget#dataPanel QLabel[dataSection="true"] { color: #17343D; font-size: 13px; font-weight: 700; }
QWidget#dataPanel QLabel[dataMuted="true"] { color: #627B84; font-size: 12px; }
QWidget#dataPanel QFrame#dataSegments { background: #E7EFF0; border-radius: 9px; }
QWidget#dataPanel QPushButton { background: transparent; color: #627B84;
    border: 1px solid transparent; border-radius: 7px; padding: 2px 8px;
    font-family: "Microsoft YaHei UI"; font-size: 13px; }
QWidget#dataPanel QPushButton:hover { color: #11595F; background: #EFF8F5; }
QWidget#dataPanel QPushButton:pressed { color: #11595F; background: #DBEEEA; }
QWidget#dataPanel QPushButton:checked { background: #FFFFFF; color: #11595F;
    border-color: #D9E6E8; font-weight: 700; }
QWidget#dataPanel QPushButton:focus { border-color: #187E83; }
QWidget#dataPanel QPushButton:disabled { color: #7D9299; background: #EDF2F3; }
QWidget#dataPanel QLabel[dataMetric="true"] { color: #187E83; font-size: 24px; font-weight: 700; }
QWidget#dataPanel QLabel[dataMetricCaption="true"] { color: #627B84; font-size: 12px; }
QWidget#dataPanel QProgressBar { background: #E7EFF0; border: none; border-radius: 2px; }
QWidget#dataPanel QProgressBar::chunk { background: #187E83; border-radius: 2px; }
QWidget#dataPanel QLabel[dataSource="true"] { color: #435E68; background: #F3F6F7;
    border-radius: 5px; padding: 2px 6px; font-size: 12px; }
QWidget#dataPanel QLabel[dataSource="true"][sourceState="indexed"],
QWidget#dataPanel QLabel[dataSource="true"][sourceState="available"] { color: #11595F; background: #EFF8F5; }
QWidget#dataPanel QLabel[dataSource="true"][sourceState="error"] { color: #AF4C56; background: #FCEFF0; }
QWidget#dataPanel QLabel[dataSource="true"][sourceState="pending"],
QWidget#dataPanel QLabel[dataSource="true"][sourceState="unconnected"] { color: #A77739; background: #FFF5E5; }
QWidget#dataPanel QLabel#dataPlatforms { color: #627B84; font-size: 12px; }
QWidget#dataPanel QLabel#dataTrainingTotals { color: #627B84; font-size: 12px; }
QWidget#dataPanel QLabel#dataRecent[empty="true"] { color: #627B84; font-size: 12px; }
QWidget#dataPanel QLabel#dataRecent[empty="false"] { color: #435E68; font-size: 12px; }
QWidget#dataPanel QLabel[modelState="error"], QWidget#dataPanel QLabel[modelState="protected"] {
    color: #AF4C56; }
QWidget#dataPanel QLabel[modelState="read_only"] { color: #A77739; }
"""


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _counter(value):
    """Reject invalid counters BEFORE float conversion (including enormous ints)."""
    if type(value) is int:
        return value if 0 <= value <= MAX_COUNTER else None
    if type(value) is float and math.isfinite(value) and 0 <= value <= MAX_COUNTER and value.is_integer():
        return int(value)
    return None


def _count(value):
    result = _counter(value)
    return result if result is not None else 0


def _enum(value, choices):
    return value if isinstance(value, str) and value in choices else None


def _stamp(value):
    # Used only for ordering, never formatted as caller-controlled strings.
    if type(value) is int:
        return value if 0 < value <= MAX_COUNTER else None
    if type(value) is float and math.isfinite(value) and 0 < value <= MAX_COUNTER:
        return value
    return None


def _compact(value):
    """Keep unusually large but valid counters from widening a 400px panel."""
    for scale, suffix in ((10 ** 12, "万亿"), (10 ** 8, "亿"), (10 ** 4, "万")):
        if value >= scale:
            tenths = value * 10 // scale
            return f"{tenths // 10}.{tenths % 10}{suffix}"
    return str(value)


def _public_records(raw):
    """Detached, bounded, finalized public-ingest metadata; never raw text.

    Running/queued rows and incompatible source/kind/domain combinations are not
    completed ingests.  A failed/cancelled/interrupted row is shown honestly but
    cannot contribute newly accepted records, even if it claims an added metric.
    """
    result, seen = [], set()
    for row in raw[:EVENT_LIMIT] if isinstance(raw, list) else ():
        if not isinstance(row, dict):
            continue
        source = _enum(row.get("source"), SOURCE_TEXT)
        status = _enum(row.get("status"), FINAL_STATE_TEXT)
        event_id, finished = _counter(row.get("id")), _stamp(row.get("finished"))
        kind, domain = row.get("kind"), _enum(row.get("domain"), DOMAIN_TEXT)
        valid_kind = (source == "cldr_zh" and kind == "public_lexicon" and row.get("domain") == "emotion") or (
            source is not None and source != "cldr_zh" and kind == "knowledge_index" and domain is not None)
        if not valid_kind or status is None or not event_id or finished is None or event_id in seen:
            continue
        seen.add(event_id)
        metrics = _mapping(row.get("metrics"))
        committed = status in ("success", "no_change")
        result.append({
            "id": event_id, "finished": finished, "source": source, "status": status,
            "added": _count(metrics.get("added")) if status == "success" else 0,
            "changed": _count(metrics.get("changed")) if committed else 0,
            "validated": _count(metrics.get("validated")), "rejected": _count(metrics.get("rejected")),
        })
    return sorted(result, key=lambda row: (row["finished"], row["id"]), reverse=True)


class DataPanel(QWidget):
    """A view-only snapshot dashboard, with persistent Knowledge/Emotion tabs.

    ``info`` accepts {emotion, knowledge, training: {journal, brain}, companion,
    platformSources, sourceCatalog}.  Source catalog rows contain fixed ``id``
    and ``status`` enum codes.  Platform entries remain metadata-only/pending;
    no caller-provided claim of connectivity is presented as a connection.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dataPanel")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setStyleSheet(PANEL_QSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        heading = QHBoxLayout()
        heading.addWidget(self._label("数据档案", "dataHeading"), 1)
        heading.addWidget(self._label("只读快照 · 不保存原文", "dataReadOnly"), 1, Qt.AlignmentFlag.AlignRight)
        root.addLayout(heading)

        hero = QFrame()
        hero.setObjectName("dataHeroCard")
        hero.setFixedHeight(60)
        hero_box = QHBoxLayout(hero)
        hero_box.setContentsMargins(13, 6, 13, 6)
        hero_box.setSpacing(12)
        self.hero_values = {}
        for key, title in (("knowledge", "已收录知识"), ("emotion", "情感拟合样本"), ("training", "累计审计事件")):
            column = QVBoxLayout()
            column.setSpacing(0)
            value = self._label("0", prop="dataValue")
            column.addWidget(self._label(title, prop="dataCaption"))
            column.addWidget(value)
            hero_box.addLayout(column, 1)
            self.hero_values[key] = value
        root.addWidget(hero)

        segments = QFrame()
        segments.setObjectName("dataSegments")
        segments.setFixedHeight(30)
        segment_box = QHBoxLayout(segments)
        segment_box.setContentsMargins(2, 2, 2, 2)
        segment_box.setSpacing(2)
        self.view_buttons = QButtonGroup(self)
        self.view_buttons.setExclusive(True)
        self.knowledge_button = QPushButton("知识分布")
        self.emotion_button = QPushButton("情感模型")
        for index, button in enumerate((self.knowledge_button, self.emotion_button)):
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.view_buttons.addButton(button, index)
            segment_box.addWidget(button, 1)
        self.knowledge_button.setChecked(True)
        root.addWidget(segments)

        self.views = QStackedWidget()
        self.views.setFixedHeight(122)
        self.views.addWidget(self._knowledge_view())
        self.views.addWidget(self._emotion_view())
        self.view_buttons.idClicked.connect(self.views.setCurrentIndex)
        root.addWidget(self.views)

        recent, recent_box = self._card()
        row = QHBoxLayout()
        row.addWidget(self._label("近期公开收录", prop="dataSection"), 1)
        self.ingest_count_label = self._label(prop="dataMuted")
        row.addWidget(self.ingest_count_label, 1, Qt.AlignmentFlag.AlignRight)
        recent_box.addLayout(row)
        self.ingest_totals_label = self._label(prop="dataMuted")
        self.ingest_totals_label.setStyleSheet("font-size:11px;")
        self.recent_label = self._label(name="dataRecent", prop="dataMuted")
        self.recent_label.setMinimumHeight(26)
        recent_box.addWidget(self.ingest_totals_label)
        recent_box.addWidget(self.recent_label)
        root.addWidget(recent)

        sources, source_box = self._card()
        row = QHBoxLayout()
        row.addWidget(self._label("来源覆盖", prop="dataSection"), 1)
        self.source_count_label = self._label(prop="dataMuted")
        row.addWidget(self.source_count_label, 2, Qt.AlignmentFlag.AlignRight)
        source_box.addLayout(row)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        self.source_labels = {}
        for index, source in enumerate(SOURCE_TEXT):
            label = self._label(prop="dataSource")
            grid.addWidget(label, index // 3, index % 3)
            grid.setColumnStretch(index % 3, 1)
            self.source_labels[source] = label
        source_box.addLayout(grid)
        self.platform_label = self._label("知乎 · 待接入    短视频 · 待接入  ｜  仅元数据，未连接", "dataPlatforms")
        self.platform_label.setStyleSheet("font-size:11px;")
        self.platform_label.setToolTip("平台尚未连接；仅限官方公开 API/RSS，不采集私密内容、不下载视频。")
        source_box.addWidget(self.platform_label)
        root.addWidget(sources)

        self.training_totals_label = self._label(name="dataTrainingTotals")
        self.training_totals_label.setStyleSheet("font-size:10px;")
        root.addWidget(self.training_totals_label)
        self.update_data({})

    @staticmethod
    def _label(text="", name="", prop=""):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if name:
            label.setObjectName(name)
        if prop:
            label.setProperty(prop, True)
        return label

    @staticmethod
    def _card():
        frame = QFrame()
        frame.setProperty("dataCard", True)
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 4, 12, 4)
        box.setSpacing(3)
        return frame, box

    def _knowledge_view(self):
        card, box = self._card()
        row = QHBoxLayout()
        self.knowledge_label = self._label(prop="dataSection")
        self.knowledge_revision_label = self._label(prop="dataMuted")
        row.addWidget(self.knowledge_label, 2)
        row.addWidget(self.knowledge_revision_label, 1, Qt.AlignmentFlag.AlignRight)
        box.addLayout(row)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(5)
        self.domain_labels, self.domain_bars = {}, {}
        for index, domain in enumerate(DOMAIN_TEXT):
            column = QVBoxLayout()
            column.setSpacing(2)
            label = self._label()
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setTextVisible(False)
            bar.setFixedHeight(3)
            bar.setMinimumWidth(0)
            column.addWidget(label)
            column.addWidget(bar)
            grid.addLayout(column, index // 2, index % 2)
            grid.setColumnStretch(index % 2, 1)
            self.domain_labels[domain], self.domain_bars[domain] = label, bar
        box.addLayout(grid)
        card.setToolTip("已收录数是当前本地知识索引条数；分布只显示固定领域，不显示知识正文。收录不等于神经网络训练。")
        return card

    def _emotion_view(self):
        card, box = self._card()
        row = QHBoxLayout()
        row.addWidget(self._label("情绪分类器 · 非真实情绪", prop="dataSection"), 2)
        self.emotion_revision_label = self._label(prop="dataMuted")
        row.addWidget(self.emotion_revision_label, 1, Qt.AlignmentFlag.AlignRight)
        box.addLayout(row)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        self.emotion_values = {}
        metrics = (("fit_samples", "拟合样本"), ("adaptation_passes", "加性适应轮次"),
                   ("lexicon", "公开词条"), ("features", "哈希特征"),
                   ("events", "观察次数"), ("learned", "已学线索"))
        for index, (key, title) in enumerate(metrics):
            column = QVBoxLayout()
            column.setSpacing(0)
            value = self._label("0", prop="dataMetric")
            column.addWidget(value)
            column.addWidget(self._label(title, prop="dataMetricCaption"))
            grid.addLayout(column, index // 3, index % 3)
            grid.setColumnStretch(index % 3, 1)
            self.emotion_values[key] = value
        box.addLayout(grid)
        card.setToolTip("加性权重适应不是反向传播；公开词典收录不是训练，特征数不是准确率。这里只展示模型计数，不展示输入原文或情绪原因。")
        return card

    @staticmethod
    def _set(label, text):
        if label.text() != text:
            label.setText(text)

    @staticmethod
    def _state(label, key, value):
        """Repolish only when a fixed presentation enum actually changes."""
        if label.property(key) != value:
            label.setProperty(key, value)
            label.style().unpolish(label)
            label.style().polish(label)

    def _metric(self, label, value):
        self._set(label, _compact(value))
        label.setToolTip(str(value))

    def update_data(self, info):
        info = _mapping(info)
        emotion = _mapping(info.get("emotion"))
        knowledge = _mapping(info.get("knowledge"))
        training = _mapping(info.get("training"))
        journal = _mapping(training.get("journal"))
        totals = _mapping(journal.get("totals"))
        count = _count(knowledge.get("count"))
        self._metric(self.hero_values["knowledge"], count)
        self._metric(self.hero_values["emotion"], _count(emotion.get("fit_samples")))
        self._metric(self.hero_values["training"], _count(totals.get("runs")))
        self._set(self.knowledge_label, f"已收录 {_compact(count)} 条 · 索引非训练")
        self.knowledge_label.setToolTip(f"当前已收录知识 {count} 条")
        knowledge_state = MODEL_STATE_TEXT.get(_enum(knowledge.get("status"), MODEL_STATE_TEXT), "待快照")
        self._set(self.knowledge_revision_label, f"r{_compact(_count(knowledge.get('revision')))} · {knowledge_state}")
        self._state(self.knowledge_revision_label, "modelState", _enum(knowledge.get("status"), MODEL_STATE_TEXT) or "new")
        domains = _mapping(knowledge.get("domains"))
        clean_domains = {domain: _count(domains.get(domain)) for domain in DOMAIN_TEXT}
        total = sum(clean_domains.values())
        for domain, value in clean_domains.items():
            self._set(self.domain_labels[domain], f"{DOMAIN_TEXT[domain]}  {_compact(value)}")
            self.domain_labels[domain].setToolTip(f"{DOMAIN_TEXT[domain]}：{value} 条")
            # Integer arithmetic avoids huge-int float conversion and Qt overflow.
            progress = value * 1000 // total if total else 0
            if self.domain_bars[domain].value() != progress:
                with QSignalBlocker(self.domain_bars[domain]):
                    self.domain_bars[domain].setValue(progress)
        emotion_state = MODEL_STATE_TEXT.get(_enum(emotion.get("status"), MODEL_STATE_TEXT), "待快照")
        self._set(self.emotion_revision_label, f"r{_compact(_count(emotion.get('revision')))} · {emotion_state}")
        self._state(self.emotion_revision_label, "modelState", _enum(emotion.get("status"), MODEL_STATE_TEXT) or "new")
        for key, label in self.emotion_values.items():
            self._metric(label, _count(emotion.get(key)))

        records = _public_records(journal.get("events"))
        self._set(self.ingest_count_label, f"留存已结束 {len(records)} 次")
        added, changed, validated, rejected = (sum(row[key] for row in records) for key in ("added", "changed", "validated", "rejected"))
        self._set(self.ingest_totals_label, f"新增 {_compact(added)} · 变更 {_compact(changed)} · 校验 {_compact(validated)} · 拒绝 {_compact(rejected)}")
        self.ingest_totals_label.setToolTip(
            f"仅当前留存的公开收录记录，非终身总数。新增 {added}，变更 {changed}，校验 {validated}，拒绝 {rejected}。"
            "新增只计成功提交；失败、取消和提交未知不计为已收录。")
        lines = []
        for row in records[:2]:
            suffix = f"新增 {_compact(row['added'])}" if row["status"] in ("success", "no_change") else "不计新增"
            lines.append(f"{SOURCE_TEXT[row['source']]} · {FINAL_STATE_TEXT[row['status']]} · {suffix}")
        self._set(self.recent_label, "\n".join(lines) if lines else "暂无已结束的公开收录记录\n进行中、排队及私密输入不展示")
        self._state(self.recent_label, "empty", not lines)

        catalog = info.get("sourceCatalog")
        states = {}
        for row in catalog[:32] if isinstance(catalog, list) else ():
            if not isinstance(row, dict):
                continue
            source = _enum(row.get("id"), SOURCE_TEXT)
            state = _enum(row.get("status"), SOURCE_STATE_TEXT)
            if source is not None and source not in states and state is not None:
                states[source] = state
        indexed = sum(state == "indexed" for state in states.values())
        available = sum(state == "available" for state in states.values())
        self._set(self.source_count_label, f"已收录 {indexed}/{len(SOURCE_TEXT)} · 可收录 {available}")
        for source, label in self.source_labels.items():
            state_text = SOURCE_STATE_TEXT.get(states.get(source), "待确认")
            self._set(label, f"{SOURCE_TEXT[source]} · {state_text}")
            self._state(label, "sourceState", states.get(source, "unknown"))
        # platformSources and companion intentionally cannot assert connectivity,
        # leak arbitrary mood/reason/name, or turn metadata into an active feed.
        steps = _count(totals.get("weight_steps"))
        passes = _count(totals.get("emotion_adaptation_passes"))
        failed = _count(totals.get("failed"))
        unknown = _count(totals.get("interrupted_unknown"))
        self._set(self.training_totals_label,
                  f"动作 SGD {_compact(steps)} 步 · 情感适应 {_compact(passes)} 次 · 失败 {_compact(failed)} · 未知 {_compact(unknown)}")
        brain = _mapping(training.get("brain"))
        brain_state = MODEL_STATE_TEXT.get(_enum(brain.get("status"), MODEL_STATE_TEXT), "待快照")
        self.training_totals_label.setToolTip(
            f"累计审计 {_count(totals.get('runs'))} 次；动作 SGD {steps} 步；情感加性适应 {passes} 次；失败 {failed}；中断提交未知 {unknown}。\n"
            f"动作模型：样本 {_count(brain.get('samples'))} · 更新 {_count(brain.get('updates'))} · v{_count(brain.get('version'))} · {brain_state}。\n"
            f"累计知识新增 {_count(totals.get('knowledge_added'))} 条。审计次数、索引收录、权重更新分别统计；均不是准确率。")
