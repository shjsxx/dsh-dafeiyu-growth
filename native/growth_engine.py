"""Independent, versioned growth engine for the native companion.

It owns level/stage/streak/milestone state in ``growth.json`` and mirrors the
XP and daily counters already produced by the DSH plugin. It never creates XP,
reads chats, runs tools or grants permissions. Corrupt or unknown files are
preserved and reported as protected instead of being replaced.

Pure Python, bounded, atomic writes. No Qt, network, model or UI imports.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

VERSION = 1
MAX_LEVEL = 60
MAX_BYTES = 256 * 1024
MAX_DAYS = 400
MAX_MILESTONES = 100
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
STAGES = (
    ("egg", "云芽", "刚凝成的一缕云气", 1),
    ("fry", "听风", "会追着风跑的小云灵", 5),
    ("fish", "御风", "能自己穿过云层", 10),
    ("big", "踏云", "踏着云海走了很远", 20),
    ("whale", "岚鲸", "云海尽头的鲸", 35),
)


def default_path() -> Path:
    explicit = os.environ.get("DSH_DAFEIYU_ENGINE_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "growth.json"
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "growth.json"


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _count(value, cap=10 ** 13):
    number = _finite(value)
    return int(max(0, min(cap, number))) if number is not None else 0


def xp_for_level(level):
    n = max(1, min(MAX_LEVEL, int(level)))
    return 40 * (n - 1) ** 2


def level_from_xp(xp):
    return min(MAX_LEVEL, int(math.sqrt(max(0.0, xp) / 40)) + 1)


def stage_for_level(level):
    chosen = STAGES[0]
    for stage in STAGES:
        if level >= stage[3]:
            chosen = stage
    return {"key": chosen[0], "name": chosen[1], "title": chosen[2]}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


class GrowthEngine:
    def __init__(self, path=None, clock=None):
        self.path = Path(path).expanduser() if path is not None else default_path()
        self._clock = clock or (lambda: time.time())
        self.status = "new"
        self.error = ""
        self.data = None
        self._load()

    # ---- persistence -------------------------------------------------
    def _load(self):
        try:
            with self.path.open("rb") as source:
                body = source.read(MAX_BYTES + 1)
        except FileNotFoundError:
            self.data = self._empty()
            return
        except OSError:
            self.status, self.error = "protected", "growth engine file is unreadable"
            return
        if len(body) > MAX_BYTES:
            self.status, self.error = "protected", "growth engine file is too large"
            return
        try:
            raw = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs)
            if not isinstance(raw, dict) or raw.get("version") != VERSION:
                raise ValueError
            days = raw.get("days")
            milestones = raw.get("milestones")
            if not isinstance(days, dict) or not isinstance(milestones, list):
                raise ValueError
            clean_days = {}
            for key, value in list(days.items())[:MAX_DAYS]:
                if isinstance(key, str) and DATE.match(key):
                    clean_days[key] = _count(value)
            clean_milestones = []
            for row in milestones[-MAX_MILESTONES:]:
                if not isinstance(row, dict) or row.get("kind") not in {"start", "level", "stage", "reset"}:
                    continue
                clean_milestones.append({"at": _count(row.get("at")), "kind": row["kind"],
                                         "level": _count(row.get("level")), "stage": str(row.get("stage") or "")[:16]})
            self.data = {
                "version": VERSION,
                "revision": _count(raw.get("revision")),
                "xp": _count(raw.get("xp")),
                "days": clean_days,
                "milestones": clean_milestones,
                "createdAt": _count(raw.get("createdAt")),
                "updatedAt": _count(raw.get("updatedAt")),
            }
            self.status = "ready"
        except (ValueError, TypeError, UnicodeDecodeError):
            self.data = None
            self.status, self.error = "protected", "growth engine file is corrupt or unsupported"

    def _empty(self):
        now = int(self._clock())
        return {"version": VERSION, "revision": 0, "xp": 0, "days": {}, "milestones": [],
                "createdAt": now, "updatedAt": now}

    def _save(self):
        if self.status == "protected" or not isinstance(self.data, dict):
            return False
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
            self.status = "ready"
            self.error = ""
            return True
        except (OSError, ValueError, TypeError):
            self.status, self.error = "error", "growth engine file could not be written"
            return False
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    # ---- sync --------------------------------------------------------
    def sync(self, state, now=None):
        """Mirror one validated profile snapshot; return a public snapshot.

        XP is mirrored, never invented. A drop is recorded as a reset milestone
        only when the incoming profile is valid and level 1, so a transient
        missing/corrupt read cannot erase progress.
        """
        if self.status == "protected":
            return None
        if not isinstance(state, dict):
            return None
        xp = _finite(state.get("xp"))
        if xp is None or xp < 0 or xp > 10 ** 13:
            return None
        xp = int(xp)
        stamp = int(self._clock() if now is None else now)
        today = datetime.fromtimestamp(stamp).date()
        incoming = state.get("days") if isinstance(state.get("days"), dict) else {}
        previous_xp = self.data["xp"]
        reset = xp == 0 and previous_xp > 0
        merged = {} if reset else dict(self.data["days"])
        for key, value in list(incoming.items())[:MAX_DAYS]:
            if isinstance(key, str) and DATE.match(key):
                merged[key] = max(_count(value), merged.get(key, 0))
        cutoff = (today - timedelta(days=MAX_DAYS)).isoformat()
        merged = {key: value for key, value in sorted(merged.items()) if key >= cutoff}
        if len(merged) > MAX_DAYS:
            merged = dict(sorted(merged.items())[-MAX_DAYS:])
        streak = 0
        for offset in range(MAX_DAYS):
            if merged.get((today - timedelta(days=offset)).isoformat()):
                streak += 1
            elif offset:
                break
        level, stage = level_from_xp(xp), stage_for_level(level_from_xp(xp))
        previous = {"xp": previous_xp, "level": level_from_xp(previous_xp),
                    "stage": stage_for_level(level_from_xp(previous_xp))["key"]}
        changed = (reset or xp != previous["xp"] or merged != self.data["days"]
                   or level != previous["level"] or stage["key"] != previous["stage"])
        milestones = list(self.data["milestones"])
        def add(kind):
            milestones.append({"at": stamp, "kind": kind, "level": level, "stage": stage["key"]})
            del milestones[:-MAX_MILESTONES]
        if not milestones:
            add("start")
            changed = True
        if reset:
            add("reset")
        elif level > previous["level"]:
            add("level")
        if not reset and stage["key"] != previous["stage"] and level > 1:
            add("stage")
        if not changed:
            return self.snapshot()
        previous_data = {**self.data, "days": dict(self.data["days"]),
                         "milestones": [dict(row) for row in self.data["milestones"]]}
        self.data.update(xp=xp, days=merged, milestones=milestones, revision=self.data["revision"] + 1,
                         updatedAt=stamp)
        if not self._save():
            self.data = previous_data
            return self.snapshot()
        return self.snapshot()

    def snapshot(self):
        data = self.data or self._empty()
        xp = data["xp"]
        level = level_from_xp(xp)
        into, need, ratio = 1, 1, 1.0
        if level < MAX_LEVEL:
            need = max(1, xp_for_level(level + 1) - xp_for_level(level))
            into = max(0, min(need, xp - xp_for_level(level)))
            ratio = into / need
        stage = stage_for_level(level)
        today = datetime.fromtimestamp(int(self._clock())).date()
        streak = 0
        for offset in range(MAX_DAYS):
            if data["days"].get((today - timedelta(days=offset)).isoformat()):
                streak += 1
            elif offset:
                break
        return {
            "version": VERSION, "status": self.status, "error": self.error,
            "revision": data["revision"], "xp": xp, "level": level,
            "into": into, "need": need, "ratio": ratio, "stage": stage,
            "streak": streak, "days": dict(data["days"]),
            "milestones": len(data["milestones"]),
            "updatedAt": data["updatedAt"],
        }
