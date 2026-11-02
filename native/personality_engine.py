"""Stable personality tendencies plus current tone/initiative.

Personality is a bounded, deterministic simulation: it learns slowly from the
existing companion state and decides how warm/curious/playful the local voice
is. It never claims consciousness and never invents memories; memory only
selects a topic and nudges initiative.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

VERSION = 1
MAX_BYTES = 64 * 1024
DIMENSIONS = ("warmth", "curiosity", "playfulness", "steadiness", "expressiveness")
LABELS = {"warmth": "温暖", "curiosity": "好奇", "playfulness": "俏皮",
          "steadiness": "稳定", "expressiveness": "表达"}
TONES = ("gentle", "warm", "playful", "curious", "steady")
TONE_LABELS = {"gentle": "轻柔", "warm": "温暖", "playful": "俏皮",
               "curious": "好奇", "steady": "平稳"}
SUFFIX = {"gentle": " 我在这儿，不催你。", "warm": " 我在。",
          "playful": " 嘿，别太严肃嘛。", "curious": " 我还想多听你说一点。", "steady": ""}


def default_path():
    explicit = os.environ.get("DSH_DAFEIYU_PERSONALITY_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "personality.json"
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "personality.json"


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _clamp(value):
    return max(0.0, min(100.0, float(value)))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


class PersonalityEngine:
    def __init__(self, path=None, clock=None):
        self.path = Path(path).expanduser() if path is not None else default_path()
        self._clock = clock or (lambda: time.time())
        self.status = "new"
        self.error = ""
        self.data = None
        self._last_tone = "steady"
        self._last_initiative = 0.4
        self._load()

    def _load(self):
        try:
            with self.path.open("rb") as source:
                body = source.read(MAX_BYTES + 1)
        except FileNotFoundError:
            self.data = self._empty()
            return
        except OSError:
            self.status, self.error = "protected", "personality file is unreadable"
            return
        if len(body) > MAX_BYTES:
            self.status, self.error = "protected", "personality file is too large"
            return
        try:
            raw = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs)
            if not isinstance(raw, dict) or raw.get("version") != VERSION:
                raise ValueError
            traits = raw.get("traits")
            if not isinstance(traits, dict):
                raise ValueError
            clean = {}
            for key in DIMENSIONS:
                value = _finite(traits.get(key))
                if value is None:
                    raise ValueError
                clean[key] = round(_clamp(value), 1)
            self.data = {"version": VERSION, "revision": max(0, int(_finite(raw.get("revision")) or 0)),
                         "traits": clean, "interactions": max(0, int(_finite(raw.get("interactions")) or 0)),
                         "updatedAt": max(0, int(_finite(raw.get("updatedAt")) or 0))}
            self.status = "ready"
        except (ValueError, TypeError, UnicodeDecodeError):
            self.data = None
            self.status, self.error = "protected", "personality file is corrupt or unsupported"

    def _empty(self):
        return {"version": VERSION, "revision": 0, "traits": {key: 50.0 for key in DIMENSIONS},
                "interactions": 0, "updatedAt": int(self._clock())}

    def _save(self, before):
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self.status, self.error = "ready", ""
            return True
        except (OSError, ValueError, TypeError):
            self.data = before
            self.status, self.error = "error", "personality file could not be written"
            return False
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def update(self, companion=None, emotion=None, state=None, now=None):
        """Slowly adapt toward the current relationship; no text is stored."""
        if self.status == "protected":
            return self.snapshot()
        companion = companion if isinstance(companion, dict) else {}
        state = state if isinstance(state, dict) else {}
        trust = max(0.0, min(1.0, _finite(companion.get("trust")) or 0.35))
        curiosity = max(0.0, min(1.0, _finite(companion.get("curiosity")) or 0.45))
        energy = max(0.0, min(1.0, _finite(companion.get("energy")) or 0.76))
        valence = max(-1.0, min(1.0, _finite(companion.get("valence")) or 0.2))
        counters = state.get("counters") if isinstance(state.get("counters"), dict) else {}
        turns = max(0, int(_finite(counters.get("turns")) or 0))
        streak = max(0, int(_finite(state.get("streak")) or 0))
        target = {
            "warmth": 50 + trust * 25 + (valence + 1) * 7 + min(8, streak),
            "curiosity": 40 + curiosity * 30 + min(10, energy * 12),
            "playfulness": 30 + energy * 25 + trust * 12 + min(6, turns / 30),
            "steadiness": 55 + (1 - energy) * 18 + (1 - abs(valence - .2)) * 8,
            "expressiveness": 45 + trust * 15 + energy * 10,
        }
        before = {**self.data, "traits": dict(self.data["traits"])}
        current = dict(self.data["traits"])
        changed = False
        for key in DIMENSIONS:
            next_value = round(_clamp(current[key] * .82 + _clamp(target[key]) * .18), 1)
            if abs(next_value - current[key]) >= .5:
                current[key] = next_value
                changed = True
        if not changed and self.data["interactions"]:
            return self.snapshot()
        self.data["traits"] = current
        stamp = int(self._clock() if now is None else now)
        self.data["revision"] += 1
        self.data["interactions"] += 1
        self.data["updatedAt"] = stamp
        if not self._save(before):
            # _save restores before, including the original traits; return the
            # restored snapshot rather than the rejected candidate.
            return self.snapshot()
        return self.snapshot()

    def tone(self, mood="neutral", has_memory=False):
        traits = (self.data or self._empty())["traits"]
        if mood in ("sad", "tired", "anxious", "anger"):
            chosen = "gentle"
        elif has_memory and traits["warmth"] >= 55:
            chosen = "warm"
        elif mood == "joy" and traits["playfulness"] >= 52:
            chosen = "playful"
        elif traits["curiosity"] >= 58 and mood == "neutral":
            chosen = "curious"
        else:
            chosen = "steady"
        self._last_tone = chosen
        return chosen

    def initiative(self, mood="neutral", has_memory=False):
        traits = (self.data or self._empty())["traits"]
        value = .15 + traits["expressiveness"] / 220 + traits["curiosity"] / 500
        if has_memory:
            value += .10
        if mood in ("sad", "tired", "anxious", "anger"):
            value -= .18
        if mood == "joy":
            value += .05
        self._last_initiative = round(max(.05, min(.95, value)), 2)
        return self._last_initiative

    def proactive(self, base, memory_hits=None, mood="neutral"):
        """Prefer a remembered topic when initiative is high; else base line."""
        hit = next((row for row in (memory_hits or []) if isinstance(row, dict)
                    and isinstance(row.get("text"), str) and row["text"].strip()), None)
        if hit and self.initiative(mood, True) >= .55:
            text = hit["text"].strip().replace("\n", " ")[:56]
            return f"我刚刚想起：{text}。现在还想聊聊吗？"
        return base

    def decorate(self, text, mood="neutral", has_memory=False):
        """Add a short tonal tail only to already-emotional/default replies."""
        if not isinstance(text, str) or len(text) > 160 or text.endswith(("？", "?")):
            return text
        tone = self.tone(mood, has_memory)
        suffix = SUFFIX.get(tone, "")
        return text + suffix if suffix and suffix not in text else text

    def snapshot(self, mood="neutral", has_memory=False):
        data = self.data or self._empty()
        tone = self.tone(mood, has_memory)
        initiative = self.initiative(mood, has_memory)
        return {"version": VERSION, "status": self.status, "error": self.error,
                "revision": data["revision"], "traits": dict(data["traits"]),
                "labels": dict(LABELS), "tone": tone, "toneLabel": TONE_LABELS[tone],
                "initiative": initiative, "interactions": data["interactions"],
                "simulated": True}
