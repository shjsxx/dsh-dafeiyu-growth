"""Original QPainter charts for the growth workspace. Pure display: no IO."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

INK = '#17343D'
MUTED = '#627B84'
GRID = '#E3EDEE'
TEAL = '#187E83'
TEAL_SOFT = '#D9ECEA'
ACCENTS = ('#187E83', '#5B9A8F', '#A98A5F', '#7C8FA6', '#B07C74', '#8E9B65')


class _Chart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _text(self, painter, rect, text, color=MUTED, size=9, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter):
        font = QFont(self.font())
        font.setPixelSize(size)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(rect, align, text)


class BarChart(_Chart):
    """Compact labeled bars; labels stay readable at 400px width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(112)
        self.setMaximumHeight(132)
        self.data = []

    def set_data(self, rows):
        self.data = [(str(label)[:6], max(0.0, float(value))) for label, value in rows][-14:]
        self.setAccessibleName(f'柱状图：{len(self.data)} 项')
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 4, -2, -2)
        rows = self.data
        if not rows:
            self._text(p, rect, '暂无记录', MUTED, 11, Qt.AlignmentFlag.AlignCenter)
            p.end(); return
        top, bottom = rect.top() + 14, rect.bottom() - 20
        peak = max(value for _, value in rows) or 1
        p.setPen(QPen(QColor(GRID), 1))
        for step in (0, .5, 1):
            y = round(bottom - (bottom - top) * step)
            p.drawLine(rect.left(), y, rect.right(), y)
        gap = 6
        slot = (rect.width() - gap * (len(rows) - 1)) / len(rows)
        for index, (label, value) in enumerate(rows):
            x = rect.left() + index * (slot + gap)
            height = (bottom - top) * value / peak
            bar = QRectF(x, bottom - height, slot, max(2.0, height))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(TEAL if index == len(rows) - 1 else TEAL_SOFT))
            p.drawRoundedRect(bar, 3, 3)
            if value:
                self._text(p, QRectF(x - 2, bottom - height - 15, slot + 4, 13), str(int(value)), INK, 9,
                           Qt.AlignmentFlag.AlignCenter)
            self._text(p, QRectF(x - 4, bottom + 3, slot + 8, 14), label, MUTED, 8,
                       Qt.AlignmentFlag.AlignCenter)
        p.end()


class RadarChart(_Chart):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(166)
        self.setMaximumHeight(194)
        self.labels, self.values = [], []

    def set_data(self, labels, values):
        self.labels = [str(x)[:6] for x in labels][:6]
        self.values = [max(0.0, min(100.0, float(v))) for v in values][:6]
        self.setAccessibleName('雷达图：' + '、'.join(f'{a}{int(b)}' for a, b in zip(self.labels, self.values)))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        count = len(self.labels)
        if count < 3:
            p.end(); return
        center = QRectF(self.rect()).center()
        radius = min(self.width(), self.height()) / 2 - 30
        angles = [-math.pi / 2 + 2 * math.pi * i / count for i in range(count)]
        def point(angle, ratio):
            return center.x() + math.cos(angle) * radius * ratio, center.y() + math.sin(angle) * radius * ratio
        for ring in (.33, .66, 1.0):
            path = QPainterPath()
            for index, angle in enumerate(angles):
                x, y = point(angle, ring)
                path.moveTo(x, y) if index == 0 else path.lineTo(x, y)
            path.closeSubpath()
            p.setPen(QPen(QColor(GRID), 1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
        for angle in angles:
            p.drawLine(*point(angle, 0), *point(angle, 1))
        path = QPainterPath()
        for index, (angle, value) in enumerate(zip(angles, self.values)):
            x, y = point(angle, value / 100)
            path.moveTo(x, y) if index == 0 else path.lineTo(x, y)
        path.closeSubpath()
        p.setPen(QPen(QColor(TEAL), 1.6)); p.setBrush(QColor(24, 126, 131, 42)); p.drawPath(path)
        for angle, value in zip(angles, self.values):
            x, y = point(angle, value / 100)
            p.setBrush(QColor(TEAL)); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))
        for angle, label, value in zip(angles, self.labels, self.values):
            x, y = point(angle, 1.18)
            self._text(p, QRectF(x - 36, y - 16, 72, 14), label, MUTED, 9, Qt.AlignmentFlag.AlignCenter)
            self._text(p, QRectF(x - 36, y - 2, 72, 16), str(int(value)), INK, 10,
                       Qt.AlignmentFlag.AlignCenter)
        p.end()


class DonutChart(_Chart):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(148)
        self.setMaximumHeight(172)
        self.segments, self.caption = [], ''

    def set_data(self, segments, caption=''):
        self.segments = [(str(label)[:8], max(0, int(value))) for label, value in segments if value > 0][:6]
        self.caption = str(caption)[:12]
        total = sum(v for _, v in self.segments)
        self.setAccessibleName(f'环形图：{self.caption} 共{total}')
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum(v for _, v in self.segments)
        side = min(self.height() - 12, self.width() * .48)
        box = QRectF(4, (self.height() - side) / 2, side, side)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(GRID)); p.drawEllipse(box)
        if total:
            start = 90 * 16
            for index, (_, value) in enumerate(self.segments):
                span = -round(360 * 16 * value / total)
                p.setBrush(QColor(ACCENTS[index % len(ACCENTS)]))
                p.drawPie(box, start, span)
                start += span
        inner = box.adjusted(side * .24, side * .24, -side * .24, -side * .24)
        p.setBrush(QColor('#FFFFFF')); p.drawEllipse(inner)
        self._text(p, QRectF(inner.x(), inner.center().y() - 14, inner.width(), 16), str(total), INK, 14,
                   Qt.AlignmentFlag.AlignCenter)
        self._text(p, QRectF(inner.x(), inner.center().y() + 2, inner.width(), 12), self.caption, MUTED, 8,
                   Qt.AlignmentFlag.AlignCenter)
        legend_x = box.right() + 12
        line = (self.height() - max(1, len(self.segments)) * 18) / 2
        for index, (label, value) in enumerate(self.segments or [('暂无数据', 0)]):
            y = line + index * 18
            p.setBrush(QColor(ACCENTS[index % len(ACCENTS)] if value else GRID)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(legend_x, y + 5, 7, 7))
            self._text(p, QRectF(legend_x + 13, y, self.width() - legend_x - 60, 16), label, MUTED, 9)
            if value:
                self._text(p, QRectF(self.width() - 46, y, 42, 16), str(int(value)), INK, 9,
                           Qt.AlignmentFlag.AlignRight)
        p.end()


def recent_days(days, count=7, now=None):
    """Return (label, count) for the latest calendar days, including empty days."""
    if not isinstance(days, dict):
        days = {}
    today = (now or datetime.now()).date()
    rows = []
    for offset in range(count - 1, -1, -1):
        day = today - timedelta(days=offset)
        try:
            value = int(days.get(day.isoformat(), 0) or 0)
        except (TypeError, ValueError):
            value = 0
        rows.append((f'{day.month:02d}-{day.day:02d}', max(0, min(10 ** 9, value))))
    return rows


def activity_days(events, count=14, now=None):
    """Aggregate journal events by local day from their finite `started` stamp."""
    totals = {}
    for row in events if isinstance(events, list) else []:
        stamp = row.get('started') if isinstance(row, dict) else None
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0:
            continue
        try:
            day = datetime.fromtimestamp(stamp).date().isoformat()
        except (OverflowError, OSError, ValueError):
            continue
        totals[day] = totals.get(day, 0) + 1
    return recent_days(totals, count, now)
