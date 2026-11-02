"""Long-term continuity anchors for personality and memory.

One bounded, numeric-only checkpoint per day proves that the same traits and
memory counts survive restarts and day rollover. It stores no text and never
changes personality or memory; it only records/compares revisions.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

VERSION = 1
MAX_BYTES = 128 * 1024
MAX_DAYS = 180
MAX_STARTUPS = 10 ** 9


def default_path():
    explicit = os.environ.get("DSH_DAFEIYU_CONTINUITY_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "continuity.json"
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "continuity.json"


def _label(now):
    return datetime.fromtimestamp(now).date().isoformat()


def _count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        return int(max(0, min(10 ** 13, value))) if math.isfinite(value) else 0
    except (OverflowError, ValueError):
        return 0


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


class ContinuityStore:
    def __init__(self, path=None, clock=None):
        self.path = Path(path).expanduser() if path is not None else default_path()
        self._clock = clock or (lambda: time.time())
        self.status = "new"
        self.error = ""
        self.data = None
        self._load()

    def _load(self):
        try:
            with self.path.open("rb") as source:
                body = source.read(MAX_BYTES + 1)
        except FileNotFoundError:
            self.data = self._empty()
            return
        except OSError:
            self.status, self.error = "protected", "continuity file is unreadable"
            return
        if len(body) > MAX_BYTES:
            self.status, self.error = "protected", "continuity file is too large"
            return
        try:
            raw = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs)
            if not isinstance(raw, dict) or raw.get("version") != VERSION:
                raise ValueError
            anchors = raw.get("anchors")
            if not isinstance(anchors, dict):
                raise ValueError
            clean = {}
            for day, row in sorted(anchors.items())[-MAX_DAYS:]:
                if not isinstance(day, str) or len(day) != 10 or not isinstance(row, dict):
                    continue
                counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
                clean[day] = {
                    "personality": _count(row.get("personality")),
                    "memory": _count(row.get("memory")),
                    "total": _count(row.get("total")),
                    "counts": {str(key)[:16]: _count(value) for key, value in list(counts.items())[:12]},
                    "traits": {str(key)[:16]: _count(value) for key, value in list((row.get("traits") or {}).items())[:8]},
                }
            self.data = {"version": VERSION, "revision": _count(raw.get("revision")),
                         "startups": min(MAX_STARTUPS, _count(raw.get("startups"))),
                         "lastStartup": _count(raw.get("lastStartup")), "anchors": clean}
            self.status = "ready"
        except (ValueError, TypeError, UnicodeDecodeError):
            self.data = None
            self.status, self.error = "protected", "continuity file is corrupt or unsupported"

    def _empty(self):
        return {"version": VERSION, "revision": 0, "startups": 0,
                "lastStartup": 0, "anchors": {}}

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
            self.status, self.error = "error", "continuity file could not be written"
            return False
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def record_startup(self):
        if self.status == "protected":
            return False
        before = {**self.data, "anchors": {key: dict(value) for key, value in self.data["anchors"].items()}}
        self.data["startups"] = min(MAX_STARTUPS, self.data["startups"] + 1)
        self.data["lastStartup"] = int(self._clock())
        self.data["revision"] += 1
        return self._save(before)

    def checkpoint(self, personality, memory, now=None):
        """Store one numeric anchor for today, at most once per unchanged state."""
        if self.status == "protected" or not isinstance(personality, dict) or not isinstance(memory, dict):
            return False
        stamp = int(self._clock() if now is None else now)
        day = _label(stamp)
        traits = personality.get("traits") if isinstance(personality.get("traits"), dict) else {}
        counts = memory.get("counts") if isinstance(memory.get("counts"), dict) else {}
        anchor = {"personality": _count(personality.get("revision")), "memory": _count(memory.get("revision")),
                   "total": _count(memory.get("total")),
                   "counts": {str(key)[:16]: _count(value) for key, value in list(counts.items())[:12]},
                   "traits": {str(key)[:16]: _count(value) for key, value in list(traits.items())[:8]}}
        existing = self.data["anchors"].get(day)
        if existing == anchor:
            return True
        before = {**self.data, "anchors": {key: dict(value) for key, value in self.data["anchors"].items()}}
        self.data["anchors"][day] = anchor
        if len(self.data["anchors"]) > MAX_DAYS:
            self.data["anchors"] = dict(sorted(self.data["anchors"].items())[-MAX_DAYS:])
        self.data["revision"] += 1
        return self._save(before)

    def verify(self, personality=None, memory=None, now=None):
        data = self.data or self._empty()
        days = sorted(data["anchors"])
        today = _label(int(self._clock() if now is None else now))
        anchor = data["anchors"].get(today) or (data["anchors"][days[-1]] if days else None)
        personality = personality if isinstance(personality, dict) else {}
        memory = memory if isinstance(memory, dict) else {}
        same = None
        if anchor:
            same = (anchor["personality"] == _count(personality.get("revision"))
                    and anchor["memory"] == _count(memory.get("revision"))
                    and anchor["total"] == _count(memory.get("total")))
        return {"version": VERSION, "status": self.status, "error": self.error,
                "days": len(days), "lastDate": days[-1] if days else "", "today": today,
                "stable": same, "startups": data["startups"], "revision": data["revision"],
                "message": ("今天的状态与上次一致" if same else
                            "今天已记录新的状态" if anchor else "还没有一致性锚点")}
