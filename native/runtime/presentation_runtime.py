"""Focused runtime mixin extracted from the native Qt host.

No separate app/server; the installed host still owns windows, signals and permissions.
"""
from __future__ import annotations
import random
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen, QPolygonF, QRadialGradient
from reactions import plan as plan_reaction


class PresentationRuntime:
    def _draw_aura(self, painter: QPainter) -> None:
        pose = self.presentation_pose
        if not isinstance(pose, dict) or not pose.get("glow_alpha"):
            return
        pet_x, pet_y, pet_width, pet_height = self._pet_rect()
        center = QPointF(pet_x + pet_width / 2, pet_y + pet_height * .58)
        radius = max(24.0, pet_width * float(pose.get("glow_radius", .7)))
        color = pose.get("glow") or (92, 158, 150)
        gradient = QRadialGradient(center, radius)
        gradient.setColorAt(0.0, QColor(color[0], color[1], color[2], int(pose["glow_alpha"])))
        gradient.setColorAt(0.72, QColor(color[0], color[1], color[2], int(pose["glow_alpha"] * .45)))
        gradient.setColorAt(1.0, QColor(color[0], color[1], color[2], 0))
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius * 1.05)
        painter.restore()


    def _trigger_reaction(self, key: str) -> bool:
        spec = plan_reaction(key)
        if not isinstance(spec, dict):
            return False
        now_ms = self._now_ms()
        self.reaction = spec
        self.reaction_started_ms = now_ms
        self.reaction_until_ms = now_ms + spec["duration"]
        self._show_overlay(spec["message"], spec["detail"], "IDLE", spec["duration"])
        motion = spec["motion"]
        if motion == "spin":
            self._start_spin(1, allow_during_chat=True)
            self._spawn_hearts(5, gold=True)
            self.blush_until_ms = now_ms + 1600
        elif motion == "come":
            pet_x, pet_y, pet_width, _ = self._pet_rect()
            cursor = QCursor.pos()
            center = self.x() + pet_x + pet_width / 2
            distance = max(140, min(460, abs(cursor.x() - center)))
            self._start_run(distance=distance, direction=1 if cursor.x() >= center else -1,
                            force_fall=False, allow_during_chat=True)
        elif motion == "droop":
            self.pet_tilt = -6.0
            self.pet_scale_y = .94
        elif motion == "doze":
            self.pet_scale_y = .97
        self.update()
        return True


    def _draw_reaction(self, painter: QPainter, now_ms: int) -> None:
        spec = self.reaction
        if not isinstance(spec, dict) or now_ms >= self.reaction_until_ms:
            return
        pet_x, pet_y, pet_width, pet_height = self._pet_rect()
        elapsed = max(0, now_ms - self.reaction_started_ms)
        life = max(1, self.reaction_until_ms - self.reaction_started_ms)
        fade = min(1.0, min(elapsed, life - elapsed) / 380.0)
        painter.save()
        painter.setOpacity(max(0.0, fade))
        effect = spec["effect"]
        if effect in ("rain", "tears"):
            for index, ratio in enumerate((.22, .30, .38, .60, .68, .76)):
                drop = (elapsed * .07 + index * 11) % 42
                x = pet_x + pet_width * ratio
                y = pet_y + pet_height * .34 + drop
                painter.setBrush(QColor(62, 128, 198, 235))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QRectF(x - 3.6, y - 7.0, 7.2, 12.0))
            if effect == "rain":
                painter.setBrush(QColor(150, 170, 190, 130))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(pet_x + pet_width * .5, pet_y + 8), 24, 7)
        elif effect == "zzz":
            font = QFont("Microsoft YaHei UI"); font.setPointSizeF(11.0); font.setBold(True)
            painter.setFont(font); painter.setPen(QColor("#7C8FA6"))
            for index, phase in enumerate((0, 1100, 2200)):
                t = (elapsed + phase) % 3300
                alpha = int(220 * (1 - t / 3300))
                painter.setPen(QColor(124, 143, 166, max(0, alpha)))
                painter.drawText(QPointF(pet_x + pet_width * .72 + t / 180,
                                         pet_y + pet_height * .30 - t / 34), "Z")
        elif effect == "question":
            font = QFont("Microsoft YaHei UI"); font.setPointSizeF(13.0); font.setBold(True)
            painter.setFont(font); painter.setPen(QColor("#8E9B65"))
            bob = 3 * (1 + int(elapsed / 320) % 2)
            painter.drawText(QPointF(pet_x + pet_width * .72, pet_y + pet_height * .22 - bob), "?")
            painter.setPen(QColor("#A98A5F"))
            painter.drawText(QPointF(pet_x + pet_width * .82, pet_y + pet_height * .36), "…")
        elif effect == "sparkle":
            painter.setPen(QPen(QColor("#D7B56D"), 2))
            for index in range(4):
                cx = pet_x + pet_width * (.20 + .20 * index)
                cy = pet_y + pet_height * (.22 + .12 * (index % 2))
                r = 3 + 2 * (index % 2)
                painter.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
                painter.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
        elif effect == "speed":
            painter.setPen(QPen(QColor(120, 140, 160, 120), 2))
            direction = -1 if self.pet_facing >= 0 else 1
            for index in range(3):
                y = pet_y + pet_height * (.55 + .08 * index)
                x = pet_x + pet_width * .18
                painter.drawLine(QPointF(x, y), QPointF(x + 24 * direction, y))
        painter.restore()


    def _spawn_hearts(self, count: int = 3, gold: bool = False) -> None:
        now_ms = self._now_ms()
        pet_x, pet_y, pet_width, pet_height = self._pet_rect()
        for _ in range(max(1, min(8, count))):
            self.hearts.append({
                "born": now_ms + random.randint(0, 90),
                "sx": self.x() + pet_x + pet_width * random.uniform(0.28, 0.72),
                "sy": self.y() + pet_y + pet_height * random.uniform(0.20, 0.52),
                "vx": random.uniform(-14, 14),
                "vy": random.uniform(-38, -22),
                "size": random.uniform(4.0, 7.5),
                "gold": gold,
            })
        if len(self.hearts) > 24:
            self.hearts = self.hearts[-24:]


    def _draw_hearts(self, painter: QPainter, now_ms: int) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for heart in self.hearts:
            age = now_ms - int(heart["born"])
            if age < 0 or age >= 1100:
                continue
            alpha = int(210 * (1 - age / 1100))
            x = heart["sx"] - self.x() + heart["vx"] * age / 1000
            y = heart["sy"] - self.y() + heart["vy"] * age / 1000
            size = float(heart["size"]) * (1 + age / 2600)
            color = QColor(236, 154, 88, alpha) if heart.get("gold") else QColor(235, 116, 141, alpha)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(x - size, y - size, size * 1.1, size * 1.1))
            painter.drawEllipse(QRectF(x, y - size, size * 1.1, size * 1.1))
            painter.drawPolygon(QPolygonF([
                QPointF(x - size * 0.95, y + size * 0.12),
                QPointF(x + size * 2.02, y + size * 0.12),
                QPointF(x + size * 0.54, y + size * 1.60),
            ]))
        painter.restore()


    def _draw_blush(self, painter: QPainter, pet_x: int, pet_y: int, pet_width: int,
                    pet_height: int, now_ms: int) -> None:
        remaining = max(0, self.blush_until_ms - now_ms)
        alpha = int(95 * min(1.0, remaining / 500))
        if alpha <= 0:
            return
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(240, 138, 154, alpha))
        radius = max(4.0, pet_width * 0.052)
        for ratio in (0.34, 0.66):
            cx = pet_x + pet_width * ratio
            cy = pet_y + pet_height * 0.37
            painter.drawEllipse(QRectF(cx - radius, cy - radius * 0.55, radius * 1.4, radius * 0.9))
        painter.restore()

