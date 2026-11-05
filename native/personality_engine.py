"""Stable personality tendencies plus current tone/initiative.

Personality is a bounded, deterministic simulation: it learns slowly from the
existing companion state and decides how warm/curious/playful the local voice
is. It never claims consciousness and never invents memories; memory only
selects a topic and nudges initiative.
"""
from __future__ import annotations

import copy
import json
import math
import os
import time
from pathlib import Path

from companion_context import personality_policy as fixed_policy

VERSION = 2
VALUES = ("respect_autonomy", "honesty", "low_interrupt")
GOALS = ("listen", "clarify", "share")
BOUNDARIES = ("no_claimed_consciousness", "no_invented_memories", "no_pressure",
              "no_automatic_private_context")
REPAIR_SECONDS = 300
REST_SECONDS = 1800
UPDATE_SECONDS = 300
UPDATE_TURNS = 3
MAX_BYTES = 64 * 1024
DIMENSIONS = ("warmth", "curiosity", "playfulness", "steadiness", "expressiveness")
FEEDBACK_DELTAS = {
    "liked": {"warmth": .45, "curiosity": .2, "playfulness": .55,
               "steadiness": .0, "expressiveness": .35},
    "disliked": {"warmth": .1, "curiosity": .05, "playfulness": -.25,
                  "steadiness": .3, "expressiveness": -.1},
}
LABELS = {"warmth": "温暖", "curiosity": "好奇", "playfulness": "俏皮",
          "steadiness": "稳定", "expressiveness": "表达"}
TONES = ("gentle", "warm", "playful", "curious", "steady")
TONE_LABELS = {"gentle": "轻柔", "warm": "温暖", "playful": "俏皮",
               "curious": "好奇", "steady": "平稳"}
SUFFIX = {"gentle": " 我在这儿，不催你。", "warm": " 我在。",
          "playful": " 可以轻松一点聊。", "curious": " 我还想多听你说一点。", "steady": ""}


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
        self._v1_body = None
        self._repair_guard = None
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
            if not isinstance(raw, dict) or isinstance(raw.get("version"), bool) or raw.get("version") not in (1, VERSION):
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
                         "updatedAt": max(0, int(_finite(raw.get("updatedAt")) or 0)),
                         "observedTurns": max(0, int(_finite(raw.get("observedTurns")) or 0)),
                         "values": list(VALUES), "goals": list(GOALS), "boundaries": list(BOUNDARIES),
                         "relationship": {"state": "steady", "until": 0, "acknowledged": False}}
            relationship = raw.get("relationship")
            if raw["version"] == VERSION and isinstance(relationship, dict):
                state = relationship.get("state")
                if state in ("steady", "repair", "rest", "refusal"):
                    self.data["relationship"] = {"state": state,
                        "until": max(0, int(_finite(relationship.get("until")) or 0)),
                        "acknowledged": relationship.get("acknowledged") is True}
            if raw["version"] == 1:
                self._v1_body = body  # no writes at load; preserve original before first migration save
            self.status = "ready"
        except (ValueError, TypeError, UnicodeDecodeError):
            self.data = None
            self.status, self.error = "protected", "personality file is corrupt or unsupported"

    def _empty(self):
        return {"version": VERSION, "revision": 0, "traits": {key: 50.0 for key in DIMENSIONS},
                "interactions": 0, "updatedAt": int(self._clock()), "observedTurns": 0,
                "values": list(VALUES), "goals": list(GOALS), "boundaries": list(BOUNDARIES),
                "relationship": {"state": "steady", "until": 0, "acknowledged": False}}

    def _save(self, before):
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Serialize only the fixed schema, never caller-supplied text/history/secrets.
            payload = {key: max(0, int(_finite(self.data.get(key)) or 0)) for key in
                       ("revision", "interactions", "updatedAt", "observedTurns")}
            payload["version"] = VERSION
            payload["traits"] = {key: round(_clamp(self.data["traits"][key]), 1) for key in DIMENSIONS}
            relation = self.data["relationship"]
            payload.update(values=list(VALUES), goals=list(GOALS), boundaries=list(BOUNDARIES),
                           relationship={"state": relation["state"] if relation["state"] in
                                         ("steady", "repair", "rest", "refusal") else "steady",
                                         "until": int(relation["until"]),
                                         "acknowledged": relation["acknowledged"] is True})
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if self._v1_body is not None:
                backup = self.path.with_name(self.path.name + ".v1.bak")
                try:
                    with backup.open("xb") as stream:
                        stream.write(self._v1_body)
                        stream.flush()
                        os.fsync(stream.fileno())
                except FileExistsError:
                    if backup.read_bytes() != self._v1_body:
                        raise ValueError("existing migration backup differs")
                try:
                    os.chmod(backup, 0o600)
                except OSError:
                    pass
            os.replace(temporary, self.path)
            self.data = payload
            self._v1_body = None
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
            except OSError:
                pass

    def update(self, companion=None, emotion=None, state=None, now=None, *, interaction=False):
        """Slowly adapt toward the current relationship; no text is stored."""
        if self.status == "protected":
            return self.snapshot()
        companion = companion if isinstance(companion, dict) else {}
        state = state if isinstance(state, dict) else {}
        def number(key, default, low=0.0):
            value = _finite(companion.get(key))
            return max(low, min(1.0, default if value is None else value))
        trust = number("trust", .35)
        curiosity = number("curiosity", .45)
        energy = number("energy", .76)
        valence = number("valence", .2, -1.0)
        counters = state.get("counters") if isinstance(state.get("counters"), dict) else {}
        turns = max(0, int(_finite(counters.get("turns")) or 0))
        streak = max(0, int(_finite(state.get("streak")) or 0))
        stamp = max(0, int(self._clock() if now is None else now))
        elapsed = max(0, stamp - self.data["updatedAt"])
        delta = max(0, turns - self.data["observedTurns"])
        # A UI refresh is not an interaction. Stable traits move only on a
        # meaningful turn batch, or a new interaction after the time gate.
        if self.data["interactions"] and not (delta >= UPDATE_TURNS or
                elapsed >= UPDATE_SECONDS and (delta > 0 or interaction)):
            return self.snapshot()
        target = {
            "warmth": 50 + trust * 25 + (valence + 1) * 7 + min(8, streak),
            "curiosity": 40 + curiosity * 30 + min(10, energy * 12),
            "playfulness": 30 + energy * 25 + trust * 12 + min(6, turns / 30),
            "steadiness": 55 + (1 - energy) * 18 + (1 - abs(valence - .2)) * 8,
            "expressiveness": 45 + trust * 15 + energy * 10,
        }
        before = copy.deepcopy(self.data)
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
        self.data["observedTurns"] = turns
        self.data["revision"] += 1
        self.data["interactions"] += 1
        self.data["updatedAt"] = stamp
        if not self._save(before):
            # _save restores before, including the original traits; return the
            # restored snapshot rather than the rejected candidate.
            return self.snapshot()
        return self.snapshot()

    def _relationship(self):
        return self._repair_guard or (self.data or self._empty())["relationship"]

    def _cooldown(self, now=None):
        relation = self._relationship()
        stamp = self._clock() if now is None else now
        return relation["state"] != "steady" and stamp < relation["until"]

    def feedback(self, liked, now=None):
        """Apply one explicit action feedback nudge to stable traits.

        This is deliberately small and bounded: a single click must not rewrite
        the personality. Repeated feedback accumulates slowly; a dislike is not
        allowed to collapse warmth. No action name or chat text is stored here.
        """
        if self.status == "protected" or not isinstance(liked, bool):
            return self.snapshot()
        before = copy.deepcopy(self.data)
        current = dict(self.data["traits"])
        delta = FEEDBACK_DELTAS["liked" if liked else "disliked"]
        changed = False
        for key in DIMENSIONS:
            next_value = round(_clamp(current[key] + delta.get(key, 0.0)), 1)
            if abs(next_value - current[key]) >= .1:
                current[key] = next_value
                changed = True
        if not changed:
            return self.snapshot()
        stamp = max(0, int(self._clock() if now is None else now))
        self.data["traits"] = current
        self.data["revision"] += 1
        self.data["interactions"] += 1
        self.data["updatedAt"] = stamp
        if not self._save(before):
            return self.snapshot()
        return self.snapshot()

    def restore_traits(self, traits, approved=False, backup_id=""):
        """Restore only the five learned traits from an approved snapshot.

        Core values/boundaries are not accepted from the caller. A rollback must
        have explicit approval and a restorable backup id.
        """
        if self.status == "protected" or not approved:
            return False
        if not isinstance(backup_id, str) or not backup_id.strip():
            return False
        if not isinstance(traits, dict):
            return False
        restored = {}
        for key in DIMENSIONS:
            value = traits.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
            restored[key] = round(_clamp(value), 1)
        before = copy.deepcopy(self.data)
        self.data["traits"] = restored
        self.data["revision"] += 1
        self.data["updatedAt"] = max(0, int(self._clock()))
        return self._save(before)

    def observe(self, text="", intent=None, now=None):
        """Observe an actual user turn and return a repair acknowledgment or ''.

        Text is classified and discarded, never persisted. Pass the context
        plan's fixed intent to avoid duplicate classification. The cooldown
        survives save failures in RAM, while persisted data rolls back exactly.
        A normal message does not cancel a refusal/rest cooldown.
        """
        if intent not in ("repair", "rest", "refusal"):
            from companion_context import _intent
            intent = _intent(text if isinstance(text, str) else "") if intent is None else intent
        if intent not in ("repair", "rest", "refusal"):
            return ""
        stamp = max(0, int(self._clock() if now is None else now))
        seconds = REST_SECONDS if intent in ("rest", "refusal") else REPAIR_SECONDS
        old_until = self._relationship()["until"]
        relation = {"state": intent, "until": max(old_until, stamp + seconds), "acknowledged": True}
        self._repair_guard = dict(relation)
        if self.status != "protected":
            before = copy.deepcopy(self.data)
            self.data["relationship"] = relation
            self.data["revision"] += 1
            self._save(before)
        if intent == "repair":
            return "抱歉，刚才没接住你的意思。我先听清楚，不再乱猜或主动岔开话题。"
        return "好，我先安静下来，不主动打扰。你想聊时再叫我。"

    def personality_policy(self, intent="idle_share"):
        """Fixed enum generation metadata only; no traits, memories or text."""
        return fixed_policy(intent, self._cooldown())

    def tone(self, mood="neutral", has_memory=False):
        traits = (self.data or self._empty())["traits"]
        if self._cooldown() or mood in ("sad", "tired", "anxious", "anger"):
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
        relation = self._relationship()
        if self._cooldown():
            value = 0.0
        elif relation["state"] != "steady" and self._clock() < relation["until"] + REPAIR_SECONDS:
            value *= .4
        self._last_initiative = round(max(0.0, min(.95, value)), 2)
        return self._last_initiative

    def proactive(self, base, memory_hits=None, mood="neutral"):
        """Return '' during repair/rest/refusal: callers must skip initiation.

        Explicit replies remain available. Memory hits are LOCAL only, never
        included in personality_policy or sent to a model by this engine.
        """
        if self._cooldown():
            self._last_initiative = 0.0
            return ""
        hit = next((row for row in (memory_hits or []) if isinstance(row, dict)
                    and isinstance(row.get("text"), str) and row["text"].strip()), None)
        if hit and self.initiative(mood, True) >= .55:
            text = hit["text"].strip().replace("\n", " ")[:56]
            return f"你之前提到：{text}。现在想聊聊吗？不想聊也没关系。"
        return base

    def decorate(self, text, mood="neutral", has_memory=False):
        """Add a short tonal tail only to already-emotional/default replies."""
        if self._cooldown() or not isinstance(text, str) or len(text) > 160 or text.endswith(("？", "?")):
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
                "values": list(VALUES), "goals": list(GOALS), "boundaries": list(BOUNDARIES),
                "relationship": {**self._relationship(), "cooldown": self._cooldown()},
                "personality_policy": self.personality_policy(), "simulated": True}
