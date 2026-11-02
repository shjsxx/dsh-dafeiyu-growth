"""Read-only, offline growth UI for the native BigFish helper.

The JSON producer is src/growth.js. DSH_HOME, when set, is the DSH data
root (normally ~/.dsh), not its parent. Nothing in this module writes data
or opens a network connection. Constructing/updating a dialog never shows it.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QStackedWidget, QTabWidget, QMessageBox,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from training_ui import TrainingPanel
from growth_charts import BarChart, RadarChart, DonutChart, GaugeChart, recent_days, activity_days
from ability_model import DIMENSIONS as ABILITY_DIMENSIONS, derive_abilities
from data_panel import DataPanel
from online_ui import OnlinePanel
from voice_ui import VoiceDataPanel
from companion_state import MOODS as COMPANION_MOODS, REASONS as COMPANION_REASONS

MAX_LEVEL = 60
FOOTER_HEIGHT = 36
STAGES = (
    {"key": "egg", "name": "云芽", "minLevel": 1, "accent": "#a9c6da", "title": "刚凝成的一缕云气"},
    {"key": "fry", "name": "听风", "minLevel": 5, "accent": "#7fa9c9", "title": "会追着风跑的小云灵"},
    {"key": "fish", "name": "御风", "minLevel": 10, "accent": "#5c8fbf", "title": "能自己穿过云层"},
    {"key": "big", "name": "踏云", "minLevel": 20, "accent": "#3f6fa8", "title": "踏着云海走了很远"},
    {"key": "whale", "name": "岚鲸", "minLevel": 35, "accent": "#2b4e7e", "title": "云海尽头的鲸"},
)
COUNTERS = ("events", "turns", "okTurns", "errorTurns", "toolCalls", "toolErrors", "approvals", "recoveries")
TRAITS = (("stamina", "体力", "#e0894a"), ("wit", "灵性", "#7f6bd0"),
          ("affinity", "亲和", "#d0568a"), ("calm", "定力", "#3f9b74"))
LEGACY_STAGE_NAMES = (("鱼卵", "云芽"), ("幼鱼", "听风"), ("小鱼", "御风"), ("大鱼", "踏云"))


def modern_text(value):
    """Show older diary lines with the current stage names."""
    text = _text(value)
    for old_name, new_name in LEGACY_STAGE_NAMES:
        text = text.replace(old_name, new_name)
    return re.sub(r"(?<!岚)鲸", "岚鲸", text)


def _number(value, default=0):
    """Accept JSON numbers only; bool, numeric strings and NaN are not XP."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        return value if math.isfinite(value) else default
    except (OverflowError, ValueError):
        return default


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value, default="", limit=8000):
    return value[:limit] if isinstance(value, str) else default


def _single_line(value):
    return " ".join(_text(value).split())


def _count(value):
    return max(0, _number(value))


def _fmt(value):
    value = _number(value)
    return str(int(value)) if value == int(value) else f"{value:g}"


def xp_for_level(level):
    n = max(1, min(MAX_LEVEL, math.floor(_number(level, 1))))
    return 40 * (n - 1) ** 2


def level_from_xp(xp):
    return min(MAX_LEVEL, math.floor(math.sqrt(max(0, _number(xp)) / 40)) + 1)


def _histogram(value):
    return {str(key): _count(count) for key, count in _mapping(value).items()}


def _rows(value, learned=False):
    if not isinstance(value, list):
        return []
    rows = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            continue
        if learned and (not row.get("id") or not row["text"]):
            continue
        rows.append({"text": _text(row["text"]), "at": _text(row.get("at")),
                     "id": _text(row.get("id")), "kind": _text(row.get("kind")),
                     "level": _number(row.get("level"), 1)})
    return rows


def _learned_rows(profile, now, streak):
    """Recompute current habit facts exactly like src/growth.js.

    Persisted ``learned`` is only an archive for stable timestamps; expired
    predicates must disappear instead of being replayed forever.
    """
    previous = {}
    raw = profile.get("learned")
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                previous[row["id"]] = row
    stamp = now.isoformat(timespec="seconds") if isinstance(now, datetime) else datetime.now().isoformat(timespec="seconds")

    def put(rows, key, text):
        old = previous.get(key)
        when = old.get("at") if isinstance(old, dict) and old.get("text") == text and isinstance(old.get("at"), str) else stamp
        rows.append({"id": key, "text": text, "at": when})

    rows = []
    if streak >= 2:
        put(rows, "streak", f"已经连续 {streak} 天陪你工作")

    hours = profile.get("hours")
    values = []
    if isinstance(hours, list):
        for value in hours[:24]:
            values.append(max(0.0, _number(value)))
    values.extend([0.0] * (24 - len(values)))
    total = sum(values)
    if total >= 40:
        peak = values.index(max(values))
        put(rows, "hour", f"你最常在 {peak:02d} 点活跃")
        night = sum(values[22:]) + sum(values[0:4])
        if night / total >= 0.35:
            put(rows, "night", "你是夜行动物，深夜还在干活")

    tools = _histogram(profile.get("tools"))
    if tools:
        key, count = max(tools.items(), key=lambda item: item[1])
        if count >= 10:
            put(rows, "tool", f"最常用的工具是 {key}（{int(count)} 次）")
    projects = _histogram(profile.get("projects"))
    if projects:
        key, count = max(projects.items(), key=lambda item: item[1])
        if count >= 20:
            put(rows, "project", f"在 {key} 这个项目上干得最多")

    counters = _mapping(profile.get("counters"))
    errors = int(_count(counters.get("toolErrors")))
    if errors >= 5:
        put(rows, "errors", f"遇到过 {errors} 次工具报错，自己爬起来了 {int(_count(counters.get('recoveries')))} 次")

    rows.sort(key=lambda row: row["at"], reverse=True)
    return rows[:12]


def derive(profile, now=None):
    """Normalize a raw JS profile and derive the same level/stages/traits.

    Invalid roots return None. Malformed individual fields fall back safely;
    ordinary valid producer data preserves JS formulas (including rounding).
    ``now`` is an optional local datetime for deterministic streak tests.
    """
    if not isinstance(profile, dict):
        return None
    xp = _number(profile.get("xp"))
    level = level_from_xp(xp)
    if level == MAX_LEVEL:
        into, need, ratio = 1, 1, 1
    else:
        need = xp_for_level(level + 1) - xp_for_level(level)
        into = max(0, min(need, xp - xp_for_level(level)))
        ratio = max(0, min(1, into / need))
    stage = next(s for s in reversed(STAGES) if level >= s["minLevel"])
    counters = {key: _count(_mapping(profile.get("counters")).get(key)) for key in COUNTERS}
    tools, projects = _histogram(profile.get("tools")), _histogram(profile.get("projects"))
    attempts = counters["okTurns"] + counters["errorTurns"]
    calm = 50
    if attempts:
        if attempts > 1e300:
            # Finite JSON fields may still overflow when added or multiplied.
            largest = max(counters["okTurns"], counters["errorTurns"])
            ok, errors = counters["okTurns"] / largest, counters["errorTurns"] / largest
            calm = 100 * ok / (ok + errors)
        else:
            calm = 100 * counters["okTurns"] / attempts
    # JS Math.round is floor(x + .5), unlike Python's round-to-even.
    # Cap before floor so pathological finite counters cannot overflow it.
    traits = {
        "stamina": math.floor(min(100, _count(profile.get("activeMs")) / 120000) + .5),
        "wit": min(100, len(tools) * 6 + len(projects) * 6),
        "affinity": math.floor(min(100, min(125, counters["turns"]) * .8 + min(25, counters["approvals"]) * 4) + .5),
        "calm": math.floor(min(100, calm) + .5),
    }
    days = _mapping(profile.get("days"))
    today = (now if isinstance(now, datetime) else datetime.now()).date()
    streak = 0
    for i in range(400):
        if days.get((today - timedelta(days=i)).isoformat()):
            streak += 1
        elif i != 0:
            break
    return {
        "version": 1, "xp": xp, "level": level, "into": into, "need": need, "ratio": ratio,
        "stage": {key: value for key, value in stage.items() if key != "minLevel"},
        "streak": streak, "traits": traits, "counters": counters,
        "topTools": sorted(tools.items(), key=lambda item: item[1], reverse=True)[:6],
        "topProjects": sorted(projects.items(), key=lambda item: item[1], reverse=True)[:6],
        "days": {key: _count(value) for key, value in list(_mapping(profile.get("days")).items())[-400:]
                 if isinstance(key, str) and len(key) == 10 and key[4] == "-" and key[7] == "-"},
        "hours": [_count(v) for v in (profile.get("hours") if isinstance(profile.get("hours"), list) else [])][:24],
        "learned": _learned_rows(profile, now if isinstance(now, datetime) else datetime.now(), streak),
        "diary": _rows(profile.get("diary")),
        "lastReviewAt": _text(profile.get("lastReviewAt")) or None,
        "createdAt": _text(profile.get("createdAt")), "updatedAt": _text(profile.get("updatedAt")),
    }


class GrowthReader:
    """Cache the last successfully parsed local profile, never modifying it."""

    def __init__(self, path=None):
        if path is None:
            path = os.environ.get("DSH_DAFEIYU_GROWTH_FILE")
            if not path:
                root = Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")
                path = root / "dsh-dafeiyu-growth" / "profile.json"
        self.path = Path(path).expanduser()
        self.state = None
        self._signature = None

    def refresh(self):
        try:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if signature == self._signature and self.state is not None:
                return self.state
            with self.path.open("r", encoding="utf-8-sig") as stream:
                state = derive(json.load(stream))
            if state is not None:
                self.state = state
                self._signature = signature
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            # Missing, half-written or corrupt profiles must not erase last-good.
            pass
        return self.state


def _valid(state):
    if not isinstance(state, dict) or not isinstance(state.get("stage"), dict):
        return False
    level = _number(state.get("level"), -1)
    return 1 <= level <= MAX_LEVEL


def summary(state):
    if not _valid(state):
        return ""
    stage = _single_line(state["stage"].get("name")) or "云芽"
    return f"Lv.{_fmt(state['level'])} · {stage}  |  经验 {_fmt(state.get('into'))}/{_fmt(state.get('need', 1))}"


def footer_height(state):
    return FOOTER_HEIGHT if _valid(state) else 0


def _accent(state):
    # Accept only literal hex colors, never injected stylesheet expressions.
    value = _mapping(state.get("stage")).get("accent")
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value
        except ValueError:
            pass
    return STAGES[0]["accent"]


def _tint(color, amount=0.12):
    """Blend an accent with white for a soft themed surface."""
    c = QColor(color)
    if not c.isValid():
        return "#FFFFFF"
    amount = max(0.0, min(1.0, amount))
    r = round(c.red() * amount + 255 * (1 - amount))
    g = round(c.green() * amount + 255 * (1 - amount))
    b = round(c.blue() * amount + 255 * (1 - amount))
    return f"#{r:02x}{g:02x}{b:02x}"


def draw_growth_footer(painter, state, x, y, width, height, scale=1):
    """Paint at the supplied footer top; all geometry is already device-scaled.

    Height is normally 36 * scale. The caller owns painter and widget lifetime.
    Painter state is restored, and no text or bar can escape the footer rect.
    """
    if not _valid(state) or not painter.isActive():
        return
    scale = max(.1, _number(scale, 1))
    x, y, width, height = (_number(v) for v in (x, y, width, height))
    height = min(height, FOOTER_HEIGHT * scale)
    if width <= 0 or height <= 0:
        return
    painter.save()
    try:
        painter.setClipRect(QRectF(x, y, width, height), Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f5f9fc"))
        painter.drawRoundedRect(QRectF(x, y, width, height), 7 * scale, 7 * scale)
        pad = min(16 * scale, width / 4)
        font = QFont("Microsoft YaHei UI")
        font.setPointSizeF(9.0 * max(1.0, scale))
        painter.setFont(font)
        painter.setPen(QPen(QColor("#405166")))
        text_rect = QRectF(x + pad, y + 2 * scale, max(0, width - pad * 2), 22 * scale)
        text = painter.fontMetrics().elidedText(summary(state), Qt.TextElideMode.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        bar_width = max(0, width - pad * 2)
        bar = QRectF(x + pad, y + 27 * scale, bar_width, 3 * scale)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e0e9ef"))
        painter.drawRoundedRect(bar, 1.5 * scale, 1.5 * scale)
        ratio = max(0, min(1, _number(state.get("ratio"))))
        if ratio:
            painter.setBrush(QColor(_accent(state)))
            painter.drawRoundedRect(QRectF(bar.x(), bar.y(), bar_width * ratio, bar.height()), 1.5 * scale, 1.5 * scale)
    finally:
        painter.restore()


def _label(text="", name="", word_wrap=True):
    label = QLabel()
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(False)
    label.setWordWrap(word_wrap)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    label.setMinimumWidth(0)
    label.setText(text)
    return label


class _CloudSeal(QWidget):
    """Vector cloud crest: decorative only, with no external image dependency."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(72, 72)
        self.setAccessibleName("云岚的云月徽记")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 88, self.height() / 88)
        painter.setPen(QPen(QColor(210, 218, 247, 45), 1))
        painter.setBrush(QColor(255, 255, 255, 7))
        painter.drawEllipse(QRectF(5, 5, 78, 78))
        painter.setPen(QPen(QColor(210, 218, 247, 85), 1))
        painter.drawArc(QRectF(11, 11, 66, 66), 30 * 16, 200 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#E5D7B8"))
        painter.drawEllipse(QRectF(46, 20, 18, 18))
        cloud = QPainterPath()
        cloud.moveTo(23, 56)
        cloud.cubicTo(12, 53, 19, 40, 29, 42)
        cloud.cubicTo(30, 26, 53, 25, 58, 42)
        cloud.cubicTo(72, 40, 78, 57, 64, 61)
        cloud.cubicTo(52, 65, 34, 60, 23, 56)
        painter.setBrush(QColor("#E4E8FB"))
        painter.drawPath(cloud)
        painter.setPen(QPen(QColor("#AFBDDF"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(28, 67), QPointF(53, 67))
        painter.drawLine(QPointF(37, 72), QPointF(60, 72))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#C9B690"))
        painter.drawEllipse(QRectF(20, 24, 3, 3))
        painter.drawEllipse(QRectF(70, 32, 2, 2))
        painter.end()


class _MoodOrb(QFrame):
    """Small native mood visualization; numbers remain the source of truth."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("moodOrb")
        self.setMinimumSize(82, 82)
        self.setMaximumSize(82, 82)
        self.valence, self.energy = 0.2, 0.76

    def set_state(self, valence, energy):
        self.valence = max(-1.0, min(1.0, _number(valence, .2)))
        self.energy = max(0.0, min(1.0, _number(energy, .76)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(6, 6, self.width() - 12, self.height() - 12)
        base = QColor("#D4EAE3") if self.valence >= 0 else QColor("#E1E9EB")
        glow = QColor("#65AAA0") if self.energy >= .5 else QColor("#9EACB2")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(glow.red(), glow.green(), glow.blue(), 45))
        painter.drawEllipse(rect.adjusted(-4, -4, 4, 4))
        painter.setBrush(base)
        painter.drawEllipse(rect)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(26, 27, 8, 9))
        painter.drawEllipse(QRectF(48, 27, 8, 9))
        painter.setPen(QPen(QColor("#376C6B"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.valence >= 0:
            painter.drawArc(QRectF(28, 31, 27, 24), 205 * 16, 130 * 16)
        else:
            painter.drawArc(QRectF(28, 42, 27, 20), 25 * 16, 130 * 16)
        painter.end()


class _JournalCover(QFrame):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#143D47"))
        gradient.setColorAt(1, QColor("#1E686E"))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(self.rect()), 18, 18)
        painter.setClipPath(self._clip())
        painter.setPen(QPen(QColor(190, 230, 224, 25), 1))
        for offset in (0, 20, 40):
            painter.drawEllipse(QRectF(self.width() - 150 + offset, self.height() - 60 + offset, 215, 115))
        painter.end()

    def _clip(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 18, 18)
        return path


class _JournalTabs(QTabWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.tabBar().setFixedWidth(self.width())


class _DiaryTimeline(QWidget):
    """Selectable plain-text entries; retain text() for read-only consumers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("growthDiary")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(10)
        self._entries = None
        self._text = ""

    def text(self):
        return self._text

    def set_entries(self, entries):
        entries = tuple(entries)
        if entries == self._entries:
            return
        self._entries = entries
        self._text = "\n\n".join((date + "\n" if date else "") + text for date, text in entries)
        if not entries:
            self._text = "还没有成长回顾。"
        while self.box.count():
            item = self.box.takeAt(0)
            if item.widget() is not None:
                widget = item.widget()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for date, text in entries or (("第一笔，留给以后的故事", "每一次进阶，都会在这里留下一段回忆。"),):
            frame = QFrame()
            frame.setProperty("journalCard", True)
            row = QHBoxLayout(frame)
            row.setContentsMargins(14, 13, 15, 14)
            row.setSpacing(12)
            marker = _label("", "diaryMarker", False)
            marker.setPixmap(theme_icon("history", size=18).pixmap(18, 18))
            marker.setFixedWidth(18)
            row.addWidget(marker, 0, Qt.AlignmentFlag.AlignTop)
            words = QVBoxLayout()
            words.setSpacing(7)
            stamp = _label(date or "成长留影", "diaryDate")
            stamp.setStyleSheet("color: #627B84; font-size: 12px;")
            body = _label(text, "diaryBody")
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setStyleSheet("font-size: 13px; color: #284B54;")
            words.addWidget(stamp)
            words.addWidget(body)
            row.addLayout(words, 1)
            self.box.addWidget(frame)
        self.updateGeometry()


from growth_theme import DIALOG_QSS, icon as theme_icon



class GrowthDialog(QDialog):
    forget_requested = Signal()
    neural_rollback_requested = Signal()
    neural_reset_requested = Signal()
    cloud_settings_requested = Signal()
    voice_tools_requested = Signal()
    emotion_learning_requested = Signal()
    emotion_auto_changed = Signal(bool)
    knowledge_auto_changed = Signal(bool)
    online_enabled_changed = Signal(bool)
    online_refresh_requested = Signal()
    memory_clear_requested = Signal()

    """A passive nonmodal growth journal: only open_near explicitly shows it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("growthJournal")
        self.setWindowTitle("云岚 · 成长卡")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setModal(False)
        self.setMinimumSize(420, 480)
        self.resize(456, 640)
        # Sharper Windows text: full hinting + grayscale-safe strategy keeps
        # small CJK glyphs legible at fractional display scaling.
        font = QFont("Microsoft YaHei UI")
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.setFont(font)
        self.setStyleSheet(DIALOG_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(10)
        self.hero = _JournalCover()
        self.hero.setObjectName("heroCard")
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(18, 14, 18, 14)
        hero_layout.setSpacing(7)
        identity = QHBoxLayout()
        identity.setSpacing(8)
        identity_text = QVBoxLayout()
        identity_text.setSpacing(2)
        identity_text.addWidget(_label("Y U N L A N  /  成长档案", "coverEyebrow", False))
        name_row = QHBoxLayout()
        name_row.setSpacing(12)
        name_row.addWidget(_label("云岚", "coverName", False))
        self.level_label = _label(name="growthLevel", word_wrap=False)
        name_row.addWidget(self.level_label)
        name_row.addStretch()
        identity_text.addLayout(name_row)
        self.title_label = _label(name="growthTitle")
        identity_text.addWidget(self.title_label)
        identity.addLayout(identity_text, 1)
        self.cloud_seal = _CloudSeal()
        identity.addWidget(self.cloud_seal)
        hero_layout.addLayout(identity)
        self.xp_progress = self._meter(1000, 6)
        self.xp_progress.setObjectName("growthXpProgress")
        self.xp_progress.setAccessibleName("本级经验进度")
        hero_layout.addWidget(self.xp_progress)
        xp_row = QHBoxLayout()
        self.xp_label = _label(name="growthXp", word_wrap=False)
        self.progress_hint = _label("慢慢长大，也很好", "coverProgressHint", False)
        self.progress_hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        xp_row.addWidget(self.xp_label, 1)
        xp_row.addWidget(self.progress_hint)
        hero_layout.addLayout(xp_row)
        outer.addWidget(self.hero)
        self.tabs = _JournalTabs()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.setAccessibleName("成长卡内容分类")
        outer.addWidget(self.tabs, 1)
        self.page_scrolls = []
        overview = self._page("概览")
        overview.setSpacing(10)
        memory = self._page("本领")
        diary_page = self._page("回顾")
        self.scroll = self.page_scrolls[0]

        self._heading(overview, "最近 7 天", "活动节奏")
        frame, box = self._card("weekCard")
        self.week_chart = BarChart()
        box.addWidget(self.week_chart)
        self.week_caption = _label("还没有活动记录；开始使用后这里会显示每天的变化。", "memoryStatus")
        box.addWidget(self.week_caption)
        overview.addWidget(frame)

        self._heading(overview, "成长画像", "来自日常积累")
        frame, box = self._card("traitRadarCard")
        self.trait_radar = RadarChart()
        box.addWidget(self.trait_radar)
        overview.addWidget(frame)
        self._heading(overview, "四项数值", "同上的数字明细")
        frame, box = self._card("traitsPanel")
        box.setContentsMargins(10, 10, 10, 10)
        traits_grid = QGridLayout()
        traits_grid.setSpacing(8)
        self.trait_bars, self.trait_labels = {}, {}
        trait_colors = ("#268A88", "#558C9C", "#B28174", "#8E9B65")
        for index, (key, title, _) in enumerate(TRAITS):
            tile = QFrame()
            tile.setProperty("traitTile", True)
            tile_box = QVBoxLayout(tile)
            tile_box.setContentsMargins(12, 8, 12, 9)
            tile_box.setSpacing(6)
            row = QHBoxLayout()
            row.setSpacing(8)
            name = _label(title, f"trait-{key}", False)
            row.addWidget(name, 1)
            value = _label("0", f"traitValue-{key}", False)
            value.setProperty("traitNumber", True)
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(value)
            tile_box.addLayout(row)
            bar = self._meter(100, 4, trait_colors[index])
            tile_box.addWidget(bar)
            traits_grid.addWidget(tile, index // 2, index % 2)
            self.trait_bars[key], self.trait_labels[key] = bar, value
        box.addLayout(traits_grid)
        overview.addWidget(frame)
        self._heading(overview, "陪伴足迹", "每一次都算数")
        frame, box = self._card("togetherPanel")
        box.setSpacing(9)
        box.setContentsMargins(14, 10, 14, 10)
        self.stat_values = {}
        specs = (("streak", "连续陪伴 / 天"), ("turns", "聊过的轮次"), ("toolCalls", "工具调用"),
                 ("toolErrors", "工具报错"), ("recoveries", "任务自救"), ("approvals", "授权确认"))
        primary, secondary = QHBoxLayout(), QHBoxLayout()
        primary.setSpacing(8)
        secondary.setSpacing(8)
        for index, (key, caption) in enumerate(specs):
            cell = QVBoxLayout()
            cell.setSpacing(3)
            value = _label("0", f"statValue-{key}", False)
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setStyleSheet(
                "font-size: 24px; font-weight: 700; color: #17343D;" if index < 3
                else "font-size: 16px; font-weight: 600; color: #435E68;"
            )
            label = _label(caption, f"statCaption-{key}")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("font-size: 12px; color: #627B84;")
            cell.addWidget(value)
            cell.addWidget(label)
            (primary if index < 3 else secondary).addLayout(cell, 1)
            self.stat_values[key] = value
        box.addLayout(primary)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: #E5EEEE; border: none;")
        box.addWidget(divider)
        box.addLayout(secondary)
        overview.addWidget(frame)
        self.stats_label = self.stat_values["turns"]
        self.audit_note = _label("这些是日常活动记录，不是模型能力评分。", "memoryStatus")
        self.audit_note.setToolTip("历史异常计数原样保留；这些成长属性不代表动作或模型能力。")
        overview.addWidget(self.audit_note)
        self.engine_status = _label("成长引擎 · 等待同步", "engineStatus")
        self.engine_status.setToolTip("独立成长引擎持有等级、阶段、连续天数与里程碑；XP 镜像现有档案，不新增经验来源。")
        self.engine_status.setStyleSheet("font-size:11px;color:#627B84;padding:0 2px;")
        overview.insertWidget(0, self.engine_status)
        self.continuity_status = _label("长期一致性 · 等待检查点", "memoryStatus")
        self.continuity_status.setStyleSheet("font-size:11px;color:#627B84;padding:0 2px;")
        self.continuity_status.setToolTip("每天保存一次性格与记忆的数字锚点；重启或跨天后校验，不保存文本。")
        overview.insertWidget(1, self.continuity_status)
        overview.addStretch()

        self._heading(memory, "能力雷达", "本地练习指数")
        radar_card, radar_box = self._card("abilityRadarCard")
        self.ability_radar = RadarChart()
        radar_box.addWidget(self.ability_radar)
        self.ability_summary = _label("六维能力会随着本地练习与互动慢慢变化。", "memoryStatus")
        radar_box.addWidget(self.ability_summary)
        memory.addWidget(radar_card)

        self._heading(memory, "能力维度", "六维 · 不是能力认证")
        frame, box = self._card("abilityGrid")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.ability_tiles = {}
        for index, spec in enumerate(ABILITY_DIMENSIONS):
            tile = QFrame()
            tile.setProperty("traitTile", True)
            tile_box = QVBoxLayout(tile)
            tile_box.setContentsMargins(12, 8, 12, 9)
            tile_box.setSpacing(4)
            head = QHBoxLayout()
            head.setSpacing(6)
            head.addWidget(_label(spec["label"], f"ability-{spec['key']}", False), 1)
            rank = _label("初识", f"abilityRank-{spec['key']}", False)
            rank.setStyleSheet("font-size:11px;color:#187E83;font-weight:600;")
            head.addWidget(rank)
            tile_box.addLayout(head)
            value = _label("0", f"abilityValue-{spec['key']}", False)
            value.setProperty("traitNumber", True)
            tile_box.addWidget(value)
            bar = self._meter(100, 4, spec["color"])
            tile_box.addWidget(bar)
            grid.addWidget(tile, index // 2, index % 2)
            self.ability_tiles[spec["key"]] = (value, rank, bar)
        box.addLayout(grid)
        memory.addWidget(frame)

        self._heading(memory, "练习记录", "动作与陪伴")
        frame, box = self._card("practiceCard")
        practice = QGridLayout()
        practice.setSpacing(8)
        self.practice_values = {}
        for index, (key, caption) in enumerate((("runs", "桌面小跑"), ("jumps", "起跳"),
                                                ("falls", "摔倒"), ("recoveries", "自己爬起"))):
            value = _label("0", f"practice-{key}", False)
            value.setStyleSheet("font-size:20px;font-weight:700;color:#17343D;")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption_label = _label(caption, "pageCaption")
            caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            practice.addWidget(value, index // 2 * 2, index % 2)
            practice.addWidget(caption_label, index // 2 * 2 + 1, index % 2)
            self.practice_values[key] = value
        box.addLayout(practice)
        self.skill_bars, self.skill_values = {}, {}
        # Compatibility only: aggregate gauges are replaced by the six-dimension
        # radar; existing callers still read these bounded practice values.
        hidden = QWidget()
        hidden.hide()
        self._skill_compat = hidden
        for key, title, color in (("agility", "敏捷", "#278A8A"), ("balance", "平衡", "#8E9B65")):
            bar = self._meter(100, 5, color)
            bar.setParent(hidden)
            value = _label("0", f"skillValue-{key}", False)
            value.setParent(hidden)
            self.skill_bars[key], self.skill_values[key] = bar, value
        self.skills_label = _label("尚未解锁本领；完成练习后会记录在这里。", "growthSkills")
        self.bond_label = _label("", "growthBond")
        self.bond_label.setStyleSheet("font-size:12px;color:#627B84;padding:6px;background:#EFF8F5;border-radius:6px;")
        box.addWidget(self.skills_label)
        box.addWidget(self.bond_label)
        memory.addWidget(frame)

        self._heading(memory, "动作偏好模型", "本机运行")
        frame, box = self._card("card")
        self.neural_label = _label("等待本地模型初始化。", "neuralInfo")
        box.addWidget(self.neural_label)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.neural_rollback = QPushButton("回滚上一版")
        self.neural_rollback.setObjectName("neuralRollback")
        self.neural_rollback.setIcon(theme_icon("reset", size=16))
        self.neural_rollback.clicked.connect(self.neural_rollback_requested.emit)
        self.neural_reset = QPushButton("重置网络")
        self.neural_reset.setObjectName("neuralReset")
        self.neural_reset.clicked.connect(self._confirm_neural_reset)
        buttons.addWidget(self.neural_rollback)
        buttons.addWidget(self.neural_reset)
        buttons.addStretch()
        box.addLayout(buttons)
        memory.addWidget(frame)
        memory.addWidget(_label("只学习数字化的动作偏好；不读取文字、屏幕或文件，不是语言模型。", "memoryStatus"))
        self._heading(memory, "记住的日常", "使用习惯")
        frame, box = self._card("card")
        self.learned_label = _label(name="growthLearned")
        box.addWidget(self.learned_label)
        memory.addWidget(frame)
        self.memory_status = _label("仅记住你明确提供的名字、喜好和互动次数。\n本地规则回应，不是自由对话模型。", "memoryStatus")
        memory.addWidget(self.memory_status)
        self.forget_button = QPushButton("清除互动记忆")
        self.forget_button.setObjectName("forgetMemory")
        self.forget_button.clicked.connect(self._confirm_forget)
        memory.addWidget(self.forget_button, 0, Qt.AlignmentFlag.AlignLeft)
        memory.addStretch()

        self._heading(diary_page, "成长回顾", "最近 5 条")
        review_card, review_box = self._card("reviewSummary")
        review_row = QHBoxLayout(); review_row.setSpacing(8)
        self.review_total, self.review_stage, self.review_streak = (_label("0", "reviewMetric", False) for _ in range(3))
        for caption, widget in (("总回顾", self.review_total), ("进阶", self.review_stage), ("连续天数", self.review_streak)):
            cell = QVBoxLayout(); cell.setSpacing(2)
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            widget.setStyleSheet("font-size:20px;font-weight:700;color:#17343D;")
            caption_label = _label(caption, "pageCaption")
            caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(widget); cell.addWidget(caption_label)
            review_row.addLayout(cell, 1)
        review_box.addLayout(review_row)
        diary_page.addWidget(review_card)
        self._heading(diary_page, "记忆引擎", "本地 + 公开")
        memory_card, memory_box = self._card("memoryEngineCard")
        self.memory_counts = _label("本地记忆 0 条 · 公开记忆 0 条", "memoryStatus")
        memory_box.addWidget(self.memory_counts)
        self.memory_recent = _label("还没有记忆；回顾、习惯和公开知识会自动汇总。", "growthLearned")
        memory_box.addWidget(self.memory_recent)
        self.memory_clear = QPushButton("清除记忆引擎")
        self.memory_clear.setObjectName("memoryClear")
        self.memory_clear.setAutoDefault(False)
        self.memory_clear.clicked.connect(self._confirm_memory_clear)
        memory_box.addWidget(self.memory_clear, 0, Qt.AlignmentFlag.AlignLeft)
        diary_page.addWidget(memory_card)
        diary_page.addWidget(_label("本地回顾/习惯与已校验公开知识自动入记忆；不会另开爬虫，也不含聊天原文。", "memoryStatus"))
        diary_page.addWidget(_label("把走过的小小一步，留在这里。", "pageCaption"))
        self.diary_label = _DiaryTimeline()
        diary_page.addWidget(self.diary_label)
        diary_page.addWidget(_label("进阶时自动留下一条记录；其他回顾由你或 agent 编写。", "memoryStatus"))
        diary_page.addStretch()
        permissions = self._page("权限")
        self._heading(permissions, "你的选择，始终优先", "权限边界")
        frame, box = self._card("privacyCard")
        box.addWidget(_label(
            "默认不读取屏幕、剪贴板、浏览记录或其他会话。\n"
            "聊天与语音上传均需另行确认，权限不随成长扩大。\n"
            "动作模型只学习数字反馈；情感模型不保存原句。",
            "permissionText"))
        permissions.addWidget(frame)
        posture, posture_box = self._card("permissionPosture")
        posture_row = QHBoxLayout(); posture_row.setSpacing(8)
        self.permission_tiles = {}
        for key, title, detail in (("local", "本地档案", "仅存本机"), ("cloud", "云端", "逐次确认"), ("tools", "电脑操作", "默认关闭")):
            tile = QFrame(); tile.setProperty("traitTile", True)
            tile_box = QVBoxLayout(tile); tile_box.setContentsMargins(10, 8, 10, 8); tile_box.setSpacing(2)
            name = _label(title, "pageCaption", False); name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value = _label(detail, "permissionState", False); value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setStyleSheet("font-size:12px;font-weight:600;color:#187E83;")
            tile_box.addWidget(name); tile_box.addWidget(value)
            posture_row.addWidget(tile, 1)
            self.permission_tiles[key] = value
        posture_box.addLayout(posture_row)
        permissions.addWidget(posture)
        self._heading(permissions, "云端对话", "每次确认")
        frame, box = self._card("cloudCard")
        self.cloud_status = _label("云端接口：未配置", "cloudStatus")
        box.addWidget(self.cloud_status)
        self.cloud_settings_button = QPushButton("云端接口设置")
        self.cloud_settings_button.setObjectName("cloudSettings")
        self.cloud_settings_button.setProperty("primary", True)
        self.cloud_settings_button.setIcon(theme_icon("arrow", "#FFFFFF", 16))
        self.cloud_settings_button.clicked.connect(self.cloud_settings_requested.emit)
        box.addWidget(self.cloud_settings_button, 0, Qt.AlignmentFlag.AlignLeft)
        permissions.addWidget(frame)
        self._heading(permissions, "公开资料学习", "可随时关闭")
        frame, box = self._card("webPermissionCard")
        box.addWidget(_label(
            "情感页的总开关控制自动读取；训练页可选择是否包含公开知识课程。\n"
            "固定公开 JSON / 官方问答接口；知乎与短视频未连接。不发送私聊，不绕过访问限制；单项最大 768 KB。", 
            "permissionText"))
        permissions.addWidget(frame)
        self._heading(permissions, "电脑操作", "会话授权 · 逐项确认")
        tools_button = QPushButton('连续语音与安全权限…')
        tools_button.setProperty("primary", True)
        tools_button.setIcon(theme_icon("voice", "#FFFFFF", 16))
        tools_button.setAutoDefault(False)
        tools_button.clicked.connect(self.voice_tools_requested.emit)
        permissions.addWidget(tools_button)
        self._heading(permissions, "高风险能力", "未启用")
        frame, box = self._card("riskCard")
        box.addWidget(_label(
            "仅可确认打开记事本／计算器、调节音量；默认无权限。\n"
            "系统命令、管理员权限、安装、任意点击输入、删除数据均未启用。\n"
            "学习不会带来额外权限；这些边界只能由你决定。",
            "permissionText"))
        permissions.addWidget(frame)
        permissions.addStretch()
        emotion_page = self._page("情感")
        emotion_page.setSpacing(10)
        emotion_nav = QHBoxLayout()
        self.emotion_sections = QButtonGroup(self)
        self.emotion_sections.setExclusive(True)
        self.emotion_stack = QStackedWidget()
        self.emotion_stack.setObjectName("emotionWorkspace")
        self.emotion_views = []
        for index, title in enumerate(("此刻的我们", "学习设置")):
            button = QPushButton(title)
            button.setObjectName("emotionSection")
            button.setCheckable(True)
            button.setAutoDefault(False)
            self.emotion_sections.addButton(button, index)
            emotion_nav.addWidget(button)
            section = QWidget()
            section_box = QVBoxLayout(section)
            section_box.setContentsMargins(0, 0, 0, 0)
            section_box.setSpacing(10)
            self.emotion_stack.addWidget(section)
            self.emotion_views.append(section_box)
        self.emotion_sections.button(0).setChecked(True)
        self.emotion_sections.idClicked.connect(self.emotion_stack.setCurrentIndex)
        emotion_page.addLayout(emotion_nav)
        emotion_page.addWidget(self.emotion_stack)
        emotion_page.addStretch()
        emotion_now, emotion_settings = self.emotion_views
        self._heading(emotion_now, "云岚的心情天气", "陪伴状态")
        frame, box = self._card("companionFeelingCard")
        mood_row = QHBoxLayout()
        self.companion_orb = _MoodOrb()
        mood_row.addWidget(self.companion_orb)
        mood_words = QVBoxLayout()
        mood_words.setSpacing(4)
        self.companion_name = _label("安安稳稳", "emotionName")
        self.companion_reason = _label("从平静的一小片云开始", "memoryStatus")
        self.companion_metrics = _label("愉悦倾向 60 · 精神 76\n信任 35 · 好奇 45", "emotionInfo")
        mood_words.addWidget(self.companion_name)
        mood_words.addWidget(self.companion_reason)
        self.companion_metrics.hide()
        mood_words.addStretch()
        mood_row.addLayout(mood_words, 1)
        box.addLayout(mood_row)
        emotion_now.addWidget(frame)

        feeling_grid = QGridLayout()
        feeling_grid.setHorizontalSpacing(8)
        feeling_grid.setVerticalSpacing(8)
        self.feeling_values, self.feeling_bars = {}, {}
        for index, (key, caption, color) in enumerate((("valence", "愉悦倾向", "#278A8A"), ("energy", "精神", "#6C9E8C"), ("trust", "熟悉 / 信任", "#AA8A79"), ("curiosity", "好奇", "#A49560"))):
            tile, tile_box = self._card("feelingTile")
            tile_box.setContentsMargins(11, 8, 11, 8)
            tile_box.setSpacing(6)
            row = QHBoxLayout()
            row.addWidget(_label(caption, "feelingCaption", False))
            number = _label("0", "feelingValue", False)
            number.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(number)
            tile_box.addLayout(row)
            meter = self._meter(100, 4, color)
            tile_box.addWidget(meter)
            feeling_grid.addWidget(tile, index // 2, index % 2)
            self.feeling_values[key], self.feeling_bars[key] = number, meter
        self._heading(emotion_now, "人格倾向", "稳定 + 记忆")
        personality_card, personality_box = self._card("personalityCard")
        self.personality_tone = _label("现在的语气：平稳 · 主动性 0.40", "emotionName")
        self.personality_metrics = _label("温暖 50 · 好奇 50 · 俏皮 50\n稳定 50 · 表达 50", "emotionInfo")
        self.personality_note = _label("性格慢慢形成；记忆只影响话题与主动性，不改变事实。", "memoryStatus")
        personality_box.addWidget(self.personality_tone)
        personality_box.addWidget(self.personality_metrics)
        personality_box.addWidget(self.personality_note)
        emotion_now.addWidget(personality_card)
        radar_card, radar_box = self._card("emotionRadarCard")
        self.emotion_radar = RadarChart()
        radar_box.addWidget(self.emotion_radar)
        emotion_now.addWidget(radar_card)
        emotion_now.addLayout(feeling_grid)
        self._heading(emotion_now, "我听见的你", "线索推测")
        frame, box = self._card("emotionCard")
        box.setSpacing(8)
        self.emotion_name = _label("还在倾听", "heardEmotion")
        box.addWidget(self.emotion_name)
        self.emotion_label = _label("等待第一次对话。", "emotionInfo")
        self.emotion_label.hide()
        self.heard_detail = _label("还没有明确线索，你可以慢慢说。", "heardDetail")
        box.addWidget(self.heard_detail)
        self.emotion_signal = self._meter(100, 4, "#55A4A5")
        self.emotion_signal.setObjectName("emotionSignal")
        box.addWidget(self.emotion_signal)
        emotion_now.addWidget(frame)
        self.emotion_comparison = _label("情感对照：等待直接对话；不上传原句。", "learningStatus")
        self.emotion_comparison.setStyleSheet("background:#EAF2F3; padding:10px; border-radius:9px; color:#435E68; font-size:12px;")
        emotion_now.addWidget(self.emotion_comparison)
        emotion_now.addWidget(_label("表达模拟 ≠ 真实意识  ·  情绪线索 ≠ 心理诊断", "emotionFootnote"))
        emotion_now.addStretch()

        self.online_panel = OnlinePanel(self)
        self.online_panel.enabled_changed.connect(self.online_enabled_changed.emit)
        self.online_panel.refresh_requested.connect(self.online_refresh_requested.emit)
        self.online_panel.curriculum_requested.connect(self.emotion_learning_requested.emit)
        emotion_settings.addWidget(self.online_panel)
        frame, box = self._card("emotionLearningCard")
        self._heading(box, "公开资料更新", "固定来源")
        self.emotion_auto = QCheckBox("自动更新已选公开学习资料")
        self.emotion_auto.setObjectName("emotionAuto")
        self.emotion_auto.toggled.connect(self.emotion_auto_changed.emit)
        box.addWidget(self.emotion_auto)
        self.learning_status = _label("自动读取已关闭。", "learningStatus")
        box.addWidget(self.learning_status)
        self.learning_source = _label("来源：Unicode CLDR 中文表情注释 · Unicode-3.0", "memoryStatus")
        self.learning_source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.learning_source)
        self.emotion_learn = QPushButton("现在检查一次")
        self.emotion_learn.setObjectName("emotionLearn")
        self.emotion_learn.clicked.connect(self.emotion_learning_requested.emit)
        box.addWidget(self.emotion_learn, 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(_label("仅固定公开数据；不发送私聊，不抓取登录页或视频媒体。", "memoryStatus"))
        emotion_settings.addWidget(frame)
        source_frame, source_box = self._card("emotionLearningCard")
        self._heading(source_box, "怎样学，才不会学歪", "数据边界")
        source_box.addWidget(_label("明确自述才调整情感权重；公开词条仅补充线索，不把平台观点当作心理事实。", "memoryStatus"))
        source_box.addWidget(_label("不保存私人原句；学习不会获得系统或管理员权限。知乎与短视频在来源页如实标记接入状态。", "memoryStatus"))
        emotion_settings.addWidget(source_frame)
        emotion_settings.addStretch()
        training_page = self._page("训练")
        training_page.setSpacing(8)
        scope_row = QHBoxLayout()
        self.training_auto = QCheckBox("后台自动更新")
        self.training_auto.setObjectName("trainingAuto")
        self.training_auto.toggled.connect(self.emotion_auto_changed.emit)
        scope_row.addWidget(self.training_auto)
        self.knowledge_auto = QCheckBox("包含公开知识课程")
        self.knowledge_auto.setToolTip("科学、天文、地理、计算机等入门知识；仅固定公开只读数据源。")
        self.knowledge_auto.setObjectName("knowledgeAuto")
        self.knowledge_auto.toggled.connect(self.knowledge_auto_changed.emit)
        scope_row.addWidget(self.knowledge_auto)
        scope_row.addStretch()
        training_page.addLayout(scope_row)
        self.training_online_label = _label("动态公开联网未连接", "learningStatus")
        training_page.addWidget(self.training_online_label)
        self.training_panel = TrainingPanel(self)
        self.training_panel.learning_requested.connect(self.emotion_learning_requested.emit)
        training_page.addWidget(self.training_panel)
        chart_card, chart_box = self._card("trainingChartCard")
        self.training_chart_title = _label("最近 14 天训练活动", "dataSection", False)
        chart_box.addWidget(self.training_chart_title)
        self.training_activity = BarChart()
        chart_box.addWidget(self.training_activity)
        self.training_chart_caption = _label("还没有训练记录。", "memoryStatus")
        chart_box.addWidget(self.training_chart_caption)
        domain_row = QHBoxLayout()
        self.training_domains = DonutChart()
        domain_row.addWidget(self.training_domains, 1)
        chart_box.addLayout(domain_row)
        training_page.addWidget(chart_card)
        training_page.addStretch()
        data_page = self._page("数据")
        data_page.setSpacing(8)
        self.data_online_label = _label("动态公开联网未连接", "learningStatus")
        data_page.addWidget(self.data_online_label)
        voice_button = QPushButton('连续语音与安全权限…')
        voice_button.setAutoDefault(False)
        voice_button.setIcon(theme_icon("voice", size=16))
        voice_button.clicked.connect(self.voice_tools_requested.emit)
        data_page.addWidget(voice_button)
        chart_card, chart_box = self._card("dataChartCard")
        self.data_donut = DonutChart()
        chart_box.addWidget(self.data_donut)
        self.data_chart_caption = _label("知识分布会随公开资料收录更新。", "memoryStatus")
        chart_box.addWidget(self.data_chart_caption)
        data_page.addWidget(chart_card)
        self.data_panel = DataPanel(self)
        data_page.addWidget(self.data_panel)
        self.voice_data_panel = VoiceDataPanel(self)
        data_page.addWidget(self.voice_data_panel)
        data_page.addStretch()
        self._emotion_snapshot, self._companion_snapshot, self._training_snapshot = {}, {}, {}
        self._state_snapshot, self._behavior_snapshot = {}, {}
        footer = QHBoxLayout()
        hint = _label("本地记录 · 上传需确认", "footerHint", False)
        hint.setToolTip("成长卡只展示本地快照；切换页面不会联网或授权。")
        footer.addWidget(hint)
        footer.addStretch()
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("closeJournal")
        self.close_button.setMinimumWidth(82)
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        outer.addLayout(footer)
        self._fit_screen(QGuiApplication.primaryScreen())
        self.update_state(None)
        self.update_behavior({})
        self.update_neural({})
        self.update_cloud({})
        self.update_emotion({})
        self.update_companion({})
        self.update_training({})
        self.tabs.currentChanged.connect(self._set_page_layout)

    def _set_page_layout(self, index):
        # Same window size and footer; dedicate more space to training details.
        compact = index >= 4
        for widget in (self.hero.findChild(QLabel, "coverEyebrow"), self.title_label, self.cloud_seal,
                       self.xp_progress, self.xp_label, self.progress_hint):
            if widget is not None:
                widget.setVisible(not compact)
        self.hero.setMaximumHeight(82 if compact else 16777215)
        self.hero.layout().invalidate()

    @staticmethod
    def _meter(maximum, height, color=None):
        bar = QProgressBar()
        bar.setRange(0, maximum)
        bar.setTextVisible(False)
        bar.setFixedHeight(height)
        if color:
            bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}")
        return bar

    @staticmethod
    def _card(name):
        frame = QFrame()
        frame.setObjectName(name)
        frame.setProperty("journalCard", True)
        box = QVBoxLayout(frame)
        box.setContentsMargins(15, 13, 15, 13)
        box.setSpacing(10)
        return frame, box

    def _page(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName("growthContent")
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 9, 3, 6)
        box.setSpacing(12)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        self.page_scrolls.append(scroll)
        return box

    def _confirm_memory_clear(self):
        answer = QMessageBox.question(
            self, "清除记忆引擎",
            "清除记忆引擎中的本地回顾/习惯与公开知识摘要？\n成长档案、知识索引和互动记忆不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.memory_clear_requested.emit()

    def update_memory(self, snapshot):
        """Passive memory-engine summary; no file or network access here."""
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        status = snapshot.get("status") if isinstance(snapshot.get("status"), str) else "new"
        counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
        local = (_count(counts.get("preference")) + _count(counts.get("habit"))
                 + _count(counts.get("diary")))
        public = _count(counts.get("public")) + _count(counts.get("culture"))
        if status == "protected":
            self.memory_counts.setText("记忆引擎存档受保护；未覆盖原文件，也不会自动学习。")
            self.memory_recent.setText("可在备份后手动检查 memory.json。")
            self.memory_clear.setEnabled(False)
            return
        self.memory_clear.setEnabled(bool(local + public))
        self.memory_counts.setText(f"本地记忆 {local} 条 · 公开记忆 {public} 条 · 版本 r{_fmt(_count(snapshot.get('revision')))}")
        rows = snapshot.get("recent") if isinstance(snapshot.get("recent"), list) else []
        lines = []
        for row in rows[:3]:
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                continue
            label = row.get("kindLabel") if isinstance(row.get("kindLabel"), str) else "记忆"
            lines.append(f"• {label}：{row['text'][:48]}")
        self.memory_recent.setText("\n".join(lines) or "还没有记忆；回顾、习惯和公开知识会自动汇总。")

    def _confirm_forget(self):
        answer = QMessageBox.question(self, "清除互动记忆", "清除名字、喜好和互动记录？\n等级、经验与跑跳练习会保留。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.forget_requested.emit()

    def report_memory_status(self, ok):
        self.memory_status.setText("互动记忆已从本地清除。练习和等级仍然保留。" if ok else "暂时无法写入本地文件；未确认磁盘删除成功，请稍后重试。")

    def update_neural(self, info):
        """Show training health without exposing weights or user content."""
        info = info if isinstance(info, dict) else {}
        status = info.get("status") if isinstance(info.get("status"), str) else "new"
        labels = {"new": "新模型", "ready": "就绪", "read_only": "只读", "protected": "受保护（需重置）", "error": "错误"}
        samples = _fmt(_count(info.get("samples")))
        updates = _fmt(_count(info.get("updates")))
        version = _fmt(_count(info.get("version")))
        lines = [
            f"10 → 12 → 4 · 样本 {samples} · 更新 {updates} · 版本 v{version}",
            f"状态：{labels.get(status, status)}",
        ]
        error = info.get("error")
        if isinstance(error, str) and error:
            lines.append(f"提示：{error}")
        self.neural_label.setText("\n".join(lines))
        self.neural_rollback.setEnabled(status in {"new", "ready"} and _count(info.get("samples")) > 0)
        self.neural_reset.setEnabled(status != "read_only")

    def update_cloud(self, config):
        """Explain the owner-configured cloud channel; never show the key."""
        config = config if isinstance(config, dict) else {}
        endpoint = config.get("endpoint") if isinstance(config.get("endpoint"), str) else ""
        model = config.get("model") if isinstance(config.get("model"), str) else ""
        if not endpoint or not model:
            self.cloud_status.setText("云端接口：未配置\n发送方式：不会联网；在设置里配置后，每次发送仍需你确认。")
            self.permission_tiles["cloud"].setText("未配置")
            return
        self.permission_tiles["cloud"].setText("已配置")
        host = urllib.parse.urlsplit(endpoint).hostname or "已配置"
        if config.get("key_saved"):
            key_state = "已用 Windows DPAPI 加密保存"
        elif config.get("key"):
            key_state = "使用当前进程的环境变量，未写入设置文件"
        else:
            key_state = "未保存（接口可能不需要密钥）"
        lines = [
            f"云端接口：{host} · {model}",
            "发送方式：每次单独确认；不附带 DSH 会话、文件、屏幕或工具调用。",
            f"密钥：{key_state}",
        ]
        if config.get("error"):
            lines.append(f"注意：{config['error']}")
        self.cloud_status.setText("\n".join(lines))

    def _confirm_neural_reset(self):
        answer = QMessageBox.question(
            self, "重置本地神经网络",
            "清除网络权重、反馈样本和版本记录？\n互动记忆与等级不会删除；此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.neural_reset_requested.emit()

    def update_emotion(self, info):
        """Describe the local mood reader without showing raw text."""
        info = info if isinstance(info, dict) else {}
        self._emotion_snapshot = dict(info)
        label = info.get("name") if isinstance(info.get("name"), str) else "平静"
        confidence = max(0.0, min(1.0, _number(info.get("confidence"))))
        learned = _fmt(_count(info.get("learned")))
        lexicon = _fmt(_count(info.get("lexicon")))
        status = info.get("status") if isinstance(info.get("status"), str) else "new"
        source = info.get("source") if isinstance(info.get("source"), str) else ""
        lines = [
            f"现在：{label} · 启发式线索分数 {round(confidence * 100)}%" if confidence > 0 else "现在：尚无明确的情绪线索",
            f"已学线索 {learned} 条 · 公开词典词 {lexicon} 条",
        ]
        self.emotion_label.setToolTip(
            "个人情绪学习保存哈希特征和数字权重，不保存原句；公开词条会存入词典。"
            + (f"\n最近公开来源：{source}" if source else "")
        )
        if status == "protected":
            lines.append("情感文件损坏，当前使用内置词典；未覆盖原文件。")
        self.emotion_name.setText(label if confidence > 0 else "还在倾听，慢慢了解")
        self.emotion_label.setText("\n".join(lines))
        self.emotion_signal.setValue(round(confidence * 100) if confidence > 0 else 0)
        self.heard_detail.setText(f"启发式线索 {round(confidence * 100)}% · 在本地分析，不保存原句" if confidence > 0 else "还没有明确线索，你可以慢慢说。")
        self._update_data_panel()
        self._refresh_abilities()
        self._emotion_protected = status in ("read_only", "protected")
        self.update_learning(getattr(self, "_learning_info", {}))

    def update_continuity(self, snapshot):
        """Passive long-term consistency summary; no writes here."""
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        if snapshot.get("status") == "protected":
            self.continuity_status.setText("长期一致性存档受保护；未覆盖原文件。")
            return
        days = int(max(0, _number(snapshot.get("days"))))
        startups = int(max(0, _number(snapshot.get("startups"))))
        message = _text(snapshot.get("message"), "等待检查点")
        self.continuity_status.setText(f"长期一致性 · 连续 {days} 天 · {message} · 启动 {startups} 次")

    def update_personality(self, snapshot):
        """Passive personality summary; no model call or file write here."""
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        traits = snapshot.get("traits") if isinstance(snapshot.get("traits"), dict) else {}
        if snapshot.get("status") == "protected":
            self.personality_tone.setText("人格存档受保护；当前不调整语气。")
            self.personality_metrics.setText("未覆盖原文件，也不会假装已学习。")
            return
        labels = snapshot.get("labels") if isinstance(snapshot.get("labels"), dict) else {}
        def value(key):
            return int(round(max(0, min(100, _number(traits.get(key))))))
        self.personality_tone.setText(
            f"现在的语气：{_text(snapshot.get('toneLabel'), '平稳')} · 主动性 {_number(snapshot.get('initiative'), .4):.2f}")
        self.personality_metrics.setText(
            f"温暖 {value('warmth')} · 好奇 {value('curiosity')} · 俏皮 {value('playfulness')}\n"
            f"稳定 {value('steadiness')} · 表达 {value('expressiveness')}")
        self.personality_note.setText("性格慢慢形成；记忆只影响话题与主动性，不改变事实。"
                                      if snapshot.get("simulated") else "人格状态来自本地统计。")

    def update_companion(self, info):
        info = info if isinstance(info, dict) else {}
        self._companion_snapshot = dict(info)
        mood = info.get("mood")
        self.companion_name.setText(COMPANION_MOODS.get(mood, "安安稳稳"))
        reason = info.get("reason")
        self.companion_reason.setText(reason if isinstance(reason, str) and reason in COMPANION_REASONS.values() else "从平静的一小片云开始")
        valence = max(-1, min(1, _number(info.get("valence"), .2)))
        values = {key: int(round(max(0, min(1, _number(info.get(key), default))) * 100)) for key, default in (("energy", .76), ("trust", .35), ("curiosity", .45))}
        self.companion_metrics.setText(f"愉悦倾向 {int(round((valence + 1) * 50))} · 精神 {values['energy']}\n信任 {values['trust']} · 好奇 {values['curiosity']}")
        self.companion_orb.set_state(valence, values["energy"] / 100)
        values["valence"] = int(round((valence + 1) * 50))
        for key, value in values.items():
            self.feeling_values[key].setText(str(value))
            self.feeling_bars[key].setValue(value)
        self.emotion_radar.set_data(["愉悦", "精神", "信任", "好奇"],
            [values["valence"], values["energy"], values["trust"], values["curiosity"]])
        self._update_data_panel()
        self._refresh_abilities()
        self.companion_reason.setToolTip("互动状态会本地保存；不因你离开而降低信任，也不会向你索取陪伴。")

    def update_online(self, info):
        """Passive live status; updating or opening a page never connects."""
        self.online_panel.update_state(info)
        self.emotion_comparison.setText(self.online_panel.comparison.text())
        text = self.online_panel.status.text() + "\n" + self.online_panel.timing.text()
        self.training_online_label.setText(text)
        self.data_online_label.setText(text)

    def update_training(self, info):
        self._training_snapshot = dict(info) if isinstance(info, dict) else {}
        self.training_panel.update_data(info)
        journal = _mapping(info.get("journal"))
        events = journal.get("events") if isinstance(journal.get("events"), list) else []
        activity = activity_days(events, 14)
        self.training_activity.set_data(activity)
        active = sum(1 for _, value in activity if value)
        total = sum(value for _, value in activity)
        self.training_chart_caption.setText(f"14天 {_fmt(total)} 次事件 · {active} 天有训练" if total else "还没有训练记录；完成一次本地训练后会显示。")
        domains = _mapping(_mapping(info.get("knowledge")).get("domains"))
        labels = {"geography": "地理", "astronomy": "天文", "chemistry": "化学", "computing": "计算机", "science": "科学", "general": "常识"}
        self.training_domains.set_data([(labels.get(key, "其他"), _count(value)) for key, value in domains.items()], "知识")
        self.data_donut.set_data([(labels.get(key, "其他"), _count(value)) for key, value in domains.items()], "知识")
        knowledge = _mapping(info.get("knowledge"))
        self.data_chart_caption.setText(f"已收录 {_fmt(_count(knowledge.get('count')))} 条知识 · {len(domains)} 个领域" if domains else "知识分布会随公开资料收录更新。")
        self.voice_data_panel.update_data(self._training_snapshot.get('journal', {}))
        self._refresh_abilities()
        self._update_data_panel()

    def _refresh_abilities(self):
        if not hasattr(self, "ability_radar"):
            return
        result = derive_abilities(getattr(self, "_state_snapshot", {}),
                                  getattr(self, "_behavior_snapshot", {}),
                                  getattr(self, "_training_snapshot", {}),
                                  getattr(self, "_emotion_snapshot", {}))
        self.ability_radar.set_data([row["label"] for row in result["dimensions"]],
                                    [row["value"] for row in result["dimensions"]])
        for row in result["dimensions"]:
            value, rank, bar = self.ability_tiles[row["key"]]
            value.setText(str(row["value"]))
            rank.setText(row["rank"])
            bar.setValue(row["value"])
        self.ability_summary.setText(
            f"综合 {result['overall']} · 最强 {result['strongest']['label']} {result['strongest']['value']}"
            f" · 可练 {result['focus']['label']} {result['focus']['value']}")

    def _update_data_panel(self):
        if not hasattr(self, "data_panel"):
            return
        training = getattr(self, "_training_snapshot", {})
        self.data_panel.update_data({"emotion": getattr(self, "_emotion_snapshot", {}),
                                    "companion": getattr(self, "_companion_snapshot", {}),
                                    "knowledge": training.get("knowledge", {}), "training": training,
                                    "sourceCatalog": training.get("sourceCatalog", []),
                                    "platformSources": training.get("platformSources", [])})

    def update_learning(self, info):
        """Automation state only; updating the card never causes a request."""
        info = info if isinstance(info, dict) else {}
        self._learning_info = dict(info)
        enabled = info.get("enabled") is True
        busy = info.get("busy") is True
        protected = bool(info.get("protected") or info.get("modelProtected") or (not info.get("knowledgeEnabled") and getattr(self, "_emotion_protected", False)))
        previous_auto = self.training_auto.blockSignals(True)
        self.training_auto.setChecked(enabled)
        self.training_auto.blockSignals(previous_auto)
        self.training_auto.setEnabled(not info.get("protected", False))
        previous_knowledge = self.knowledge_auto.blockSignals(True)
        self.knowledge_auto.setChecked(info.get("knowledgeEnabled") is True)
        self.knowledge_auto.blockSignals(previous_knowledge)
        self.knowledge_auto.setEnabled(not info.get("protected", False))
        previous = self.emotion_auto.blockSignals(True)
        self.emotion_auto.setChecked(enabled)
        self.emotion_auto.blockSignals(previous)
        self.emotion_auto.setEnabled(not info.get("protected", False))
        self.emotion_learn.setEnabled(not busy and not protected)
        self.emotion_learn.setText("正在读取…" if busy else "现在学习一次")
        if protected:
            state = "设置或词典受保护；自动读取已暂停。"
        elif busy:
            state = "后台读取中，不发送聊天内容…"
        elif enabled:
            state = "自动读取已开启 · 无需逐次确认"
        else:
            state = "自动读取已关闭"
        last = _number(info.get("lastSuccess"))
        if last > 0:
            try:
                stamp = datetime.fromtimestamp(last).strftime("%m-%d %H:%M")
            except (OverflowError, OSError, ValueError):
                stamp = "已记录"
            state += f"\n上次更新 {stamp} · 新增 {_fmt(_count(info.get('lastAdded')))} 条"
        elif not busy:
            state += "\n尚未完成首轮公开资料更新"
        error = info.get("error")
        if isinstance(error, str) and error:
            state += "\n" + error[:160]
        self.learning_status.setText(state)
        source = info.get("sourceName")
        if not isinstance(source, str) or not source:
            source = "Unicode CLDR 中文表情注释 · Unicode-3.0"
        self.learning_source.setText("来源：" + source)
        next_check = _number(info.get("nextCheck"))
        try:
            due = datetime.fromtimestamp(next_check).strftime("%m-%d %H:%M") if next_check > 0 else "启动后检查"
        except (OverflowError, OSError, ValueError):
            due = "按后台计划"
        self.learning_status.setToolTip("下次检查：" + due if enabled else "开关只控制公开资料 GET；不会授权上传私聊或系统操作。")

    @staticmethod
    def _heading(layout, text, aside=""):
        row = QHBoxLayout()
        row.setSpacing(8)
        kind = "shield" if any(key in text for key in ("权限", "选择", "边界", "操作")) else "emotion" if any(key in text for key in ("心情", "天气", "听见")) else "history" if "回顾" in text else "training" if any(key in text for key in ("模型", "网络", "学习", "资料")) else "growth"
        glyph = _label("", "sectionIcon", False)
        glyph.setFixedSize(20, 20)
        glyph.setPixmap(theme_icon(kind, size=18).pixmap(18, 18))
        row.addWidget(glyph)
        label = _label(text)
        label.setProperty("heading", True)
        row.addWidget(label, 1)
        if aside:
            note = _label(aside, "sectionAside", False)
            note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(note)
        layout.addLayout(row)

    def update_state(self, state):
        """Refresh labels using plain text only, even for hostile local JSON."""
        valid = _valid(state)
        state = state if valid else derive({})
        stage = _mapping(state.get("stage"))
        self.level_label.setText(f"Lv.{_fmt(state.get('level', 1))} · {_text(stage.get('name'), '云芽')}")
        self.title_label.setText(_text(stage.get("title"), "刚凝成的一缕云气") if valid else "成长数据尚未就绪")
        self.xp_label.setText(f"成长经验  {_fmt(state.get('into'))} / {_fmt(state.get('need', 40))}")
        self.xp_label.setToolTip(f"累计经验 {_fmt(state.get('xp'))}")
        level = int(_number(state.get("level"), 1))
        self.progress_hint.setText(f"向 Lv.{level + 1} 迈进" if level < MAX_LEVEL else "已至云海尽头")
        ratio = max(0, min(1, _number(state.get("ratio"))))
        self.xp_progress.setValue(int(ratio * 1000))
        days = recent_days(state.get("days"), 7)
        self.week_chart.set_data(days)
        active = sum(1 for _, value in days if value)
        total = sum(value for _, value in days)
        self.week_caption.setText(f"7天共 {_fmt(total)} 次活动 · {active} 天有记录" if total else "还没有活动记录；开始使用后这里会显示每天的变化。")
        traits = _mapping(state.get("traits"))
        self.trait_radar.set_data([title for _, title, _ in TRAITS], [max(0, min(100, _number(traits.get(key)))) for key, _, _ in TRAITS])
        for key, _, _ in TRAITS:
            value = max(0, min(100, _number(traits.get(key))))
            self.trait_bars[key].setValue(int(value))
            self.trait_labels[key].setText(_fmt(value))
        learned = _rows(state.get("learned"), learned=True)[:12]
        self.learned_label.setText(
            "\n".join("•  " + row["text"] for row in learned) or "还不够了解你，多用一会儿就会出现。"
        )
        diaries = _rows(state.get("diary"))[:5]
        all_diary = _rows(state.get("diary"))
        self.review_total.setText(_fmt(len(all_diary)))
        self.review_stage.setText(_fmt(sum(1 for row in all_diary if row.get("kind") == "stage")))
        self.review_streak.setText(_fmt(_count(state.get("streak"))))
        entries = []
        for row in diaries:
            when = row["at"][:16].replace("T", " ")
            if row["kind"] == "stage" and when:
                when += " · 进化"
            entries.append((when, modern_text(row["text"])))
        self.diary_label.set_entries(entries)
        counters = _mapping(state.get("counters"))
        count = lambda key: _fmt(_count(counters.get(key)))
        self.stat_values["streak"].setText(_fmt(_count(state.get("streak"))))
        self.stat_values["turns"].setText(count("turns"))
        self.stat_values["toolCalls"].setText(count("toolCalls"))
        self.stat_values["toolErrors"].setText(count("toolErrors"))
        self.stat_values["recoveries"].setText(count("recoveries"))
        self.stat_values["approvals"].setText(count("approvals"))
        engine = _mapping(state.get("engine"))
        status = engine.get("status") if isinstance(engine.get("status"), str) else ""
        if status == "ready":
            self.engine_status.setText(
                f"成长引擎 v{_fmt(_count(engine.get('version')))} · 同步 #{_fmt(_count(engine.get('revision')))}"
                f" · 里程碑 {_fmt(_count(engine.get('milestones')))}")
        elif status == "protected":
            self.engine_status.setText("成长引擎存档受保护，当前仅显示档案数据；未覆盖原文件。")
        elif status in ("error", "new"):
            self.engine_status.setText("成长引擎尚未同步；等级与经验仍来自现有档案。")
        else:
            self.engine_status.setText("成长引擎 · 等待同步")
        self._state_snapshot = state if valid else {}
        self._refresh_abilities()
        self._fit_to_content()

    def update_behavior(self, snapshot):
        """Show skills learned from the companion's own practice."""
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        self._behavior_snapshot = dict(snapshot)
        for key, caption in (("runs", "桌面小跑"), ("jumps", "起跳"), ("falls", "摔倒"), ("recoveries", "自己爬起")):
            if key in self.practice_values:
                self.practice_values[key].setText(_fmt(_count(snapshot.get(key))))
        for key in ("agility", "balance"):
            value = max(0, min(100, _number(snapshot.get(key))))
            self.skill_bars[key].setValue(int(value))
            self.skill_values[key].setText(_fmt(value))
        self._refresh_abilities()
        rows = snapshot.get("discoveries")
        lines = []
        if isinstance(rows, list):
            for row in rows[-5:]:
                if isinstance(row, dict) and isinstance(row.get("text"), str):
                    lines.append("•  " + row["text"][:120])
        self.skills_label.setText("\n".join(lines) or "还没有练出新本领，多陪它跑一会儿。")
        bits = []
        if _count(snapshot.get("pets")):
            bits.append(f"被摸 {_fmt(_count(snapshot.get('pets')))} 次")
        if _count(snapshot.get("chats")):
            bits.append(f"聊天 {_fmt(_count(snapshot.get('chats')))} 次")
        favorite = snapshot.get("favorite")
        if isinstance(favorite, str) and favorite:
            bits.append(f"最喜欢{favorite}")
        owner = snapshot.get("userName")
        if isinstance(owner, str) and owner:
            bits.append(f"记住了：{owner}")
        likes = snapshot.get("likes")
        if isinstance(likes, list) and likes and isinstance(likes[0], str):
            bits.append(f"记得你喜欢{likes[0]}")
        self.bond_label.setText(" · ".join(bits))
        self.bond_label.setVisible(bool(bits))
        self._fit_to_content()

    def _fit_to_content(self):
        # Data updates must never grow the window or move its close button.
        # Each tab scrolls independently on small screens or long histories.
        for scroll in self.page_scrolls:
            content = scroll.widget()
            if content is not None and content.layout() is not None:
                content.layout().invalidate()
                content.updateGeometry()

    def _fit_screen(self, screen):
        if screen is None:
            return
        available = screen.availableGeometry()
        self.setMinimumSize(min(420, available.width()), min(480, available.height()))
        self.setMaximumSize(available.size())
        self.resize(min(self.width(), available.width()), min(self.height(), available.height()))

    def open_near(self, petWidget):
        """Explicitly open next to the pet, keeping the complete frame on-screen."""
        pet_rect = None
        if isinstance(petWidget, QWidget):
            pet_rect = petWidget.frameGeometry() if petWidget.isWindow() else petWidget.rect().translated(petWidget.mapToGlobal(QPoint(0, 0)))
        screen = QGuiApplication.screenAt(pet_rect.center()) if pet_rect is not None else None
        screen = screen or (petWidget.screen() if isinstance(petWidget, QWidget) else QGuiApplication.primaryScreen())
        self._fit_screen(screen)
        available = screen.availableGeometry() if screen else None
        # Showing is confined to this method; __init__ and update_state are passive.
        self.show()
        # Metrics are only final after the first show; size to the whole card now.
        self._fit_to_content()
        if available is not None:
            frame = self.frameGeometry()
            # Window decorations also count toward the available screen bounds.
            extra_w, extra_h = frame.width() - self.width(), frame.height() - self.height()
            max_width, max_height = max(1, available.width() - extra_w), max(1, available.height() - extra_h)
            self.setMinimumSize(min(420, max_width), min(480, max_height))
            self.setMaximumSize(max_width, max_height)
            self.resize(min(self.width(), max_width), min(self.height(), max_height))
            frame = self.frameGeometry()
            left = pet_rect.right() + 12 if pet_rect is not None else available.center().x() - frame.width() // 2
            top = pet_rect.top() if pet_rect is not None else available.center().y() - frame.height() // 2
            if pet_rect is not None and left + frame.width() > available.right() + 1:
                left = pet_rect.left() - frame.width() - 12
            left = max(available.left(), min(left, available.right() - frame.width() + 1))
            top = max(available.top(), min(top, available.bottom() - frame.height() + 1))
            self.move(self.pos() + QPoint(left - frame.left(), top - frame.top()))
        self.raise_()
        self.activateWindow()
