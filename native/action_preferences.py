"""All-action preference model for Yunlan's presentation actions.

This is a bounded local statistics model over the *action names the renderer
actually plays* (109 pinned animations). It is deliberately separate from the
4-action decision MLP in ``neural_brain.py``: the decision model still chooses
rest/run/hop/talk under safety gates, while this model remembers which of all
performed actions the owner likes or dislikes.

Only action names, counts and a bounded preference weight are stored. No chat,
context text, files, screenshots or network data. Corrupt files fail closed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

VERSION = 2
MAX_ACTIONS = 200
MAX_NAME = 80
MAX_COUNT = 10**9
WEIGHT_LIMIT = 1.0
STEP = 0.15
DECAY = 0.995


def default_path() -> Path:
    override = os.environ.get("DSH_DAFEIYU_ACTION_PREF_FILE")
    if override:
        return Path(override)
    growth_file = os.environ.get("DSH_DAFEIYU_GROWTH_FILE")
    if growth_file:
        return Path(growth_file).resolve().parent / "action_preferences.json"
    dsh_home = os.environ.get("DSH_HOME")
    root = Path(dsh_home) if dsh_home else Path.home() / ".dsh"
    return root / "dsh-dafeiyu-growth" / "action_preferences.json"


def _number(value, default=0.0, low=0.0, high=MAX_COUNT):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return default
    if value != value or value in (float("inf"), float("-inf")):
        return default
    return max(low, min(high, value))


def _name(value):
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())[:MAX_NAME]
    return value


class ActionPreferences:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.RLock()
        self._data = {"version": VERSION, "revision": 0, "updated": 0, "actions": {}}
        self.status = "new"
        self.error = None
        self._load()

    def _blank(self):
        return {"version": VERSION, "revision": 0, "updated": 0, "actions": {}}

    def _load(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            self.status = "new"
            return
        except OSError:
            self.status, self.error = "error", "storage unavailable"
            return
        if len(raw) > 256 * 1024:
            self.status, self.error = "protected", "file too large"
            return
        try:
            state = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            self.status, self.error = "protected", "invalid file"
            return
        if not isinstance(state, dict) or state.get("version") != VERSION:
            self.status, self.error = "protected", "unsupported version"
            return
        actions = state.get("actions")
        if not isinstance(actions, dict) or len(actions) > MAX_ACTIONS:
            self.status, self.error = "protected", "invalid action table"
            return
        clean = {}
        for name, row in actions.items():
            name = _name(name)
            if not name or not isinstance(row, dict):
                continue
            clean[name] = {
                "shown": int(_number(row.get("shown"))),
                "liked": int(_number(row.get("liked"))),
                "disliked": int(_number(row.get("disliked"))),
                "weight": round(_number(row.get("weight"), 0.0, -WEIGHT_LIMIT, WEIGHT_LIMIT), 4),
            }
        self._data = {"version": VERSION,
                      "revision": int(_number(state.get("revision"))),
                      "updated": int(_number(state.get("updated"))),
                      "actions": clean}
        self.status = "ready"

    def _save(self):
        if self.status in ("protected", "error"):
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self._data, ensure_ascii=False, sort_keys=True).encode("utf-8")
            fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                try:
                    Path(temporary).unlink()
                except FileNotFoundError:
                    pass
            return True
        except OSError:
            self.status, self.error = "error", "storage unavailable"
            return False

    def observe(self, name, signal="shown"):
        """Record a performed action. signal = shown/liked/disliked."""
        if self.status in ("protected", "error"):
            return False
        if not isinstance(name, str) or len(name) > MAX_NAME:
            return False
        name = _name(name)
        if not name or signal not in ("shown", "liked", "disliked"):
            return False
        with self._lock:
            actions = self._data["actions"]
            if name not in actions:
                if len(actions) >= MAX_ACTIONS:
                    return False
                actions[name] = {"shown": 0, "liked": 0, "disliked": 0, "weight": 0.0}
            row = actions[name]
            if signal == "shown":
                row["shown"] = min(MAX_COUNT, row["shown"] + 1)
            elif signal == "liked":
                row["liked"] = min(MAX_COUNT, row["liked"] + 1)
                row["weight"] = round(min(WEIGHT_LIMIT, row["weight"] * DECAY + STEP), 4)
            else:
                row["disliked"] = min(MAX_COUNT, row["disliked"] + 1)
                row["weight"] = round(max(-WEIGHT_LIMIT, row["weight"] * DECAY - STEP), 4)
            self._data["revision"] += 1
            self._data["updated"] = int(__import__("time").time())
            return self._save()

    def preference(self, name):
        row = self._data["actions"].get(_name(name))
        return float(row["weight"]) if row else 0.0

    def choose(self, candidates=None):
        """Weighted pick from known actions; preferences, not a safety gate."""
        with self._lock:
            names = []
            if isinstance(candidates, (list, tuple)):
                names = [_name(name) for name in candidates if _name(name) in self._data["actions"]]
                if not names:
                    return None
            else:
                names = [name for name, row in self._data["actions"].items() if row["shown"] > 0]
            if not names:
                return None
            weights = [max(0.05, 1.0 + self._data["actions"][name]["weight"]) for name in names]
            total = sum(weights)
            roll = __import__("random").random() * total
            for name, weight in zip(names, weights):
                roll -= weight
                if roll <= 0:
                    return name
            return names[-1]

    def snapshot(self, limit=5):
        rows = sorted(self._data["actions"].items(), key=lambda item: item[1]["weight"], reverse=True)
        totals = {
            "shown": sum(int(row.get("shown", 0)) for row in self._data["actions"].values()),
            "liked": sum(int(row.get("liked", 0)) for row in self._data["actions"].values()),
            "disliked": sum(int(row.get("disliked", 0)) for row in self._data["actions"].values()),
        }
        return {
            "status": self.status,
            "revision": self._data["revision"],
            "updated": self._data["updated"],
            "actions": len(self._data["actions"]),
            "observations": totals["shown"],
            "liked": totals["liked"],
            "disliked": totals["disliked"],
            "rows": [{"name": name, **row} for name, row in sorted(self._data["actions"].items())],
            "top": [{"name": name, **row} for name, row in rows[:max(1, min(20, int(limit)))]],
            "bottom": [{"name": name, **row} for name, row in rows[-max(1, min(20, int(limit))):]],
        }
