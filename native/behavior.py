"""Local, rule-based learning for 云岚's on-desktop behaviour.

The companion practices running, jumping and getting up on its own. Every run
and every tumble changes two skills (agility, balance), and crossing a
threshold unlocks a new "本领" line that the growth card shows. This is honest
rule-based learning: no model, no network, no tokens — just practice.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

VERSION = 1

# (id, field, threshold, text)
RULES = (
    ("run1", "runs", 1, "学会在桌面上小跑"),
    ("run5", "runs", 5, "完成了五次桌面小跑练习"),
    ("run15", "runs", 15, "跑起来像一阵小旋风"),
    ("jump1", "jumps", 1, "学会原地起跳"),
    ("jump5", "jumps", 5, "完成了五次起跳练习"),
    ("fall1", "falls", 1, "经历了第一次摔倒"),
    ("fall3", "recoveries", 3, "摔倒后会自己爬起来，不哭"),
    ("balance20", "balance", 20, "脚步开始稳了"),
    ("balance40", "balance", 40, "平衡练习累计达到40分"),
    ("agility30", "agility", 30, "灵敏练习累计达到30分，跑速提高了"),
    ("agility60", "agility", 60, "灵敏练习累计达到60分"),
    ("pet1", "pets", 1, "第一次被你摸到，记住了这个感觉"),
    ("pet10", "pets", 10, "累计被你摸了十次"),
    ("head5", "headPats", 5, "你已经摸过我五次头了"),
    ("tail5", "tailPats", 5, "你已经挠过我五次尾巴了"),
    ("chat1", "chats", 1, "学会和你搭话"),
    ("chat10", "chats", 10, "能陪你聊上一会儿"),
    ("greet5", "greetings", 5, "会主动来找你说话"),
)

FIELDS = ("runs", "jumps", "falls", "recoveries", "agility", "balance", "distance",
          "pets", "headPats", "tailPats", "pokes", "chats", "greetings")


def default_path() -> Path:
    override = os.environ.get("DSH_DAFEIYU_BEHAVIOR_FILE")
    if override:
        return Path(override)
    growth_file = os.environ.get("DSH_DAFEIYU_GROWTH_FILE")
    if growth_file:
        return Path(growth_file).resolve().parent / "behavior.json"
    dsh_home = os.environ.get("DSH_HOME")
    root = Path(dsh_home) if dsh_home else Path.home() / ".dsh"
    return root / "dsh-dafeiyu-growth" / "behavior.json"


def _number(value, default=0.0, low=0.0, high=1e9):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return default
    if value != value or value in (float("inf"), float("-inf")):
        return default
    return max(low, min(high, value))


def _personal_text(value, limit):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _reconcile_rule_discoveries(state):
    rules = {key: (field, threshold, text) for key, field, threshold, text in RULES}
    rows = []
    seen = set()
    for row in state["discoveries"]:
        key = row["id"]
        if key in rules:
            field, threshold, text = rules[key]
            if state[field] < threshold or key in seen:
                continue
            row = dict(row, text=text)
            seen.add(key)
        rows.append(row)
    state["discoveries"] = rows


def _reconcile_personal_discoveries(state):
    """Personal discoveries are a view of current memory, not a second archive."""
    texts = {
        "name1": f"记住了你的名字：{state['userName']}" if state["userName"] else "",
        "like1": f"记得你喜欢{state['likes'][0]}" if state["likes"] else "",
    }
    rows = []
    seen = set()
    for row in state["discoveries"]:
        key = row["id"]
        if key in texts:
            if not texts[key] or key in seen:
                continue
            row = dict(row, text=texts[key])
            seen.add(key)
        rows.append(row)
    state["discoveries"] = rows


def _blank():
    return {
        "version": VERSION,
        "runs": 0,
        "jumps": 0,
        "falls": 0,
        "recoveries": 0,
        "agility": 0.0,
        "balance": 0.0,
        "distance": 0.0,
        "pets": 0,
        "headPats": 0,
        "tailPats": 0,
        "pokes": 0,
        "chats": 0,
        "greetings": 0,
        "userName": "",
        "likes": [],
        "discoveries": [],
        "updatedAt": None,
    }


class BehaviorLearner:
    """Persist practice counts and derived skills; never raises on bad files."""

    def __init__(self, path=None, now=None):
        self.path = Path(path) if path is not None else default_path()
        self._now = now
        self.data = self._load()
        self._dirty = False

    # -- persistence -----------------------------------------------------
    def _timestamp(self):
        moment = self._now() if callable(self._now) else datetime.now()
        return moment.isoformat(timespec="seconds")

    def _load(self):
        state = _blank()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return state
        if not isinstance(raw, dict):
            return state
        for key in FIELDS:
            limit = 100.0 if key in ("agility", "balance") else 1e9
            state[key] = _number(raw.get(key), 0.0, 0.0, limit)
        discoveries = raw.get("discoveries")
        if isinstance(discoveries, list):
            for row in discoveries[-30:]:
                if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                    continue
                if not row.get("id"):
                    continue
                state["discoveries"].append({
                    "id": str(row["id"])[:40],
                    "text": row["text"][:160],
                    "at": str(row.get("at") or "")[:40],
                })
        if isinstance(raw.get("updatedAt"), str):
            state["updatedAt"] = raw["updatedAt"][:40]
        state["userName"] = _personal_text(raw.get("userName"), 20)
        likes = raw.get("likes")
        if isinstance(likes, list):
            for item in likes[-30:]:
                like = _personal_text(item, 30)
                if like and like not in state["likes"]:
                    state["likes"].append(like)
            state["likes"] = state["likes"][-6:]
        _reconcile_rule_discoveries(state)
        _reconcile_personal_discoveries(state)
        return state

    def save(self):
        self._dirty = True
        self.data["updatedAt"] = self._timestamp()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(self.data, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                try:
                    Path(temporary).unlink()
                except FileNotFoundError:
                    pass
        except OSError:
            return False
        self._dirty = False
        return True

    # -- practice --------------------------------------------------------
    def _unlock(self):
        for key, field, threshold, text in RULES:
            if self.data[field] < threshold:
                continue
            if any(row.get("id") == key for row in self.data["discoveries"]):
                continue
            self.data["discoveries"].append({
                "id": key,
                "text": text,
                "at": self._timestamp(),
            })
            self._dirty = True
        if len(self.data["discoveries"]) > 30:
            self.data["discoveries"] = self.data["discoveries"][-30:]

    def note_run(self, distance):
        distance = _number(distance, 0.0, 0.0, 10000.0)
        if distance <= 0:
            return
        self.data["runs"] += 1
        self.data["distance"] += distance
        self.data["agility"] = min(100.0, self.data["agility"] + 0.4 + min(1.6, distance / 900.0))
        self._unlock()
        self.save()

    def note_jump(self):
        self.data["jumps"] += 1
        self.data["agility"] = min(100.0, self.data["agility"] + 0.5)
        self._unlock()
        self.save()

    def note_fall(self):
        self.data["falls"] += 1
        self.data["balance"] = min(100.0, self.data["balance"] + 3.0)
        self._unlock()
        self.save()

    def note_getup(self):
        if self.data["recoveries"] >= self.data["falls"]:
            return
        self.data["recoveries"] += 1
        self.data["balance"] = min(100.0, self.data["balance"] + 1.5)
        self._unlock()
        self.save()

    def note_pet(self, spot):
        """Remember where the owner likes to touch: head, tail or body."""
        self.data["pets"] += 1
        if spot == "head":
            self.data["headPats"] += 1
        elif spot == "tail":
            self.data["tailPats"] += 1
        else:
            self.data["pokes"] += 1
        self._unlock()
        self.save()

    def note_chat(self):
        self.data["chats"] += 1
        self._unlock()
        self.save()

    def note_greeting(self):
        self.data["greetings"] += 1
        self._unlock()
        self.save()

    def add_like(self, text):
        like = _personal_text(text, 30)
        if not like or like in self.data["likes"]:
            return
        self.data["likes"].append(like)
        self.data["likes"] = self.data["likes"][-6:]
        if not any(row.get("id") == "like1" for row in self.data["discoveries"]):
            self.data["discoveries"].append({
                "id": "like1",
                "text": f"记得你喜欢{like}",
                "at": self._timestamp(),
            })
        _reconcile_personal_discoveries(self.data)
        self.save()

    def set_name(self, name):
        name = _personal_text(name, 20)
        if not name or name == self.data.get("userName"):
            return
        self.data["userName"] = name
        if not any(row.get("id") == "name1" for row in self.data["discoveries"]):
            self.data["discoveries"].append({
                "id": "name1",
                "text": f"记住了你的名字：{name}",
                "at": self._timestamp(),
            })
            self._dirty = True
        _reconcile_personal_discoveries(self.data)
        self.save()

    def favorite_spot(self):
        spots = (("head", "摸头", self.data["headPats"]),
                 ("tail", "挠尾巴", self.data["tailPats"]),
                 ("poke", "戳肚子", self.data["pokes"]))
        key, label, count = max(spots, key=lambda row: row[2])
        if count < 3:
            return ""
        if sorted(row[2] for row in spots)[-2] == count:
            return ""  # no clear favourite yet
        return label

    # -- derived ---------------------------------------------------------
    def fall_chance(self):
        # Practice reduces tumbles, but never removes them: a companion that
        # occasionally trips is part of the charm.
        return max(0.05, min(0.18, 0.18 - self.data["balance"] * 0.0015 - self.data["agility"] * 0.0005))

    def run_speed(self):
        return 240.0 + self.data["agility"] * 1.8

    def snapshot(self):
        return {
            "agility": round(self.data["agility"]),
            "balance": round(self.data["balance"]),
            "runs": int(self.data["runs"]),
            "jumps": int(self.data["jumps"]),
            "falls": int(self.data["falls"]),
            "recoveries": int(self.data["recoveries"]),
            "pets": int(self.data["pets"]),
            "headPats": int(self.data["headPats"]),
            "tailPats": int(self.data["tailPats"]),
            "pokes": int(self.data["pokes"]),
            "chats": int(self.data["chats"]),
            "greetings": int(self.data["greetings"]),
            "favorite": self.favorite_spot(),
            "userName": self.data.get("userName", ""),
            "likes": list(self.data["likes"][-4:]),
            "discoveries": [dict(row) for row in self.data["discoveries"][-6:]],
        }

    def forget_user(self):
        """Forget interaction/personal memory, preserving movement practice.

        Return whether the cleared state reached disk. On failure it remains
        dirty so the caller can warn the user and retry rather than claiming
        that persisted personal information has already been erased.
        """
        interaction_fields = {
            "pets", "headPats", "tailPats", "pokes", "chats", "greetings",
        }
        for field in interaction_fields:
            self.data[field] = 0
        self.data["userName"] = ""
        self.data["likes"] = []
        movement_rules = {
            key: text for key, field, threshold, text in RULES
            if field not in interaction_fields
        }
        # Keep only known movement discoveries: an unknown legacy row can also
        # contain personal text, and must not survive an explicit erasure.
        self.data["discoveries"] = [
            dict(row, text=movement_rules[row["id"]])
            for row in self.data["discoveries"] if row.get("id") in movement_rules
        ]
        return self.save()

    def reset(self):
        self.data = _blank()
        self.save()
