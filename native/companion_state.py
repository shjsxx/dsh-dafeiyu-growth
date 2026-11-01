"""Persistent expressive state for a warm companion, not subjective sentience.

Only numeric state and enumerated interaction reasons are stored. Absence never
punishes the owner: trust does not decay for being offline, and the baseline is
calm. This simulator cannot grant capabilities or initiate network requests.
"""
from __future__ import annotations

import copy
import json
import math
import os
import time
from pathlib import Path

MOODS = {"relaxed": "安安稳稳", "cheerful": "有点雀跃", "curious": "好奇心冒头", "sleepy": "想歇一小会儿", "comforting": "想安静陪着你", "shy": "有一点害羞", "startled": "刚被轻轻吓了一跳"}
REASONS = {"new": "从平静的一小片云开始", "pet_head": "刚刚被摸了摸头", "pet_cheek": "刚才被蹭了脸", "pet_tail": "尾巴被碰了一下", "poke": "刚才被轻轻戳了一下", "chat": "刚和你聊了几句", "comfort": "想让这会儿的陪伴轻一点", "run_ok": "又完成了一次小练习", "fall": "刚练习了一次，还可以慢慢来", "feedback_yes": "知道了你喜欢的动作", "feedback_no": "在调整自己的动作习惯", "learn": "新收录了一点公开知识", "rest": "正在慢慢恢复精神"}
EVENTS = set(REASONS) - {"new", "comfort"}


def default_path():
    explicit = os.environ.get("DSH_DAFEIYU_COMPANION_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "companion.json"
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "companion.json"


def _finite(value, fallback, low=0.0, high=1.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    try:
        return max(low, min(high, float(value))) if math.isfinite(value) else fallback
    except (ValueError, OverflowError):
        return fallback


class CompanionState:
    def __init__(self, path=None, read_only=False):
        self.path = Path(path) if path is not None else default_path()
        self.read_only = read_only
        self.status = "new"
        self.error = ""
        self.data = {"version": 1, "valence": .20, "energy": .76, "trust": .35, "curiosity": .45, "events": 0, "updated": time.time(), "comfortUntil": 0, "reactionUntil": 0, "reaction": "relaxed", "reason": "new"}
        if self.path.exists():
            try:
                if self.path.stat().st_size > 32 * 1024:
                    raise ValueError("oversized")
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or raw.get("version") != 1:
                    raise ValueError("invalid")
                for key in ("valence", "energy", "trust", "curiosity"):
                    self.data[key] = _finite(raw.get(key), self.data[key], -1 if key == "valence" else 0, 1)
                self.data["events"] = int(_finite(raw.get("events"), 0, 0, 1e9))
                for key in ("updated", "comfortUntil", "reactionUntil"):
                    self.data[key] = _finite(raw.get(key), self.data[key], 0, 32_503_680_000)
                self.data["reason"] = raw.get("reason") if raw.get("reason") in REASONS else "new"
                self.data["reaction"] = raw.get("reaction") if raw.get("reaction") in MOODS else "relaxed"
                self.status = "ready"
            except (OSError, ValueError, TypeError, OverflowError):
                self.status = "protected"
                self.error = "陪伴状态存档不可读，保留原文件；暂用平静状态。"

    def _sync(self, now):
        elapsed = max(0, min(6 * 3600, now - self.data["updated"]))
        if elapsed <= 0:
            return
        blend = 1 - math.exp(-elapsed / 2400)
        self.data["valence"] += (.20 - self.data["valence"]) * blend
        self.data["energy"] += (.76 - self.data["energy"]) * (1 - math.exp(-elapsed / 1800))
        self.data["curiosity"] += (.45 - self.data["curiosity"]) * blend
        # No loneliness/guilt penalty and no trust loss while the owner is away.
        self.data["updated"] = now

    def _commit(self, data):
        if self.read_only or self.status == "protected":
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            os.replace(temporary, self.path)
        except (OSError, ValueError):
            self.status = "error"
            self.error = "陪伴状态保存失败；未假装已记住变化。"
            return False
        self.data = data
        self.status = "ready"
        self.error = ""
        return True

    def apply(self, event, user_mood="neutral", now=None):
        if event not in EVENTS:
            return {"ok": False}
        now = time.time() if now is None else now
        original = copy.deepcopy(self.data)
        self._sync(now)
        before = self.snapshot(now)
        data = copy.deepcopy(self.data)
        data["events"] = min(1_000_000_000, data["events"] + 1)
        data["reason"] = event
        if event.startswith("pet_"):
            data["valence"] += .055
            data["trust"] += .012
            data["energy"] += .01
            if event == "pet_cheek":
                data.update(reaction="shy", reactionUntil=now + 24)
        elif event == "poke":
            data["valence"] -= .015
            data.update(reaction="startled", reactionUntil=now + 8)
        elif event == "chat":
            data["valence"] += .025
            data["trust"] += .008
            data["curiosity"] += .035
            if user_mood in ("sad", "anxious", "tired", "anger"):
                data["comfortUntil"] = now + 180
                data["reason"] = "comfort"
            elif user_mood == "joy":
                data["valence"] += .06
        elif event == "run_ok":
            data["valence"] += .035
            data["energy"] -= .035
        elif event == "fall":
            data["valence"] -= .025
            data["energy"] -= .04
        elif event == "feedback_yes":
            data["valence"] += .055
            data["trust"] += .008
        elif event == "feedback_no":
            data["valence"] -= .015
            data["curiosity"] += .025
        elif event == "learn":
            data["valence"] += .025
            data["curiosity"] += .12
            data["energy"] -= .015
        elif event == "rest":
            data["energy"] += .06
        for key in ("valence", "energy", "trust", "curiosity"):
            data[key] = max(-1 if key == "valence" else 0, min(1, data[key]))
        data["updated"] = now
        ok = self._commit(data)
        if not ok:
            self.data = original
        return {"ok": ok, "before": before, "after": self.snapshot(now) if ok else before}

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        self._sync(now)
        if self.data["comfortUntil"] > now:
            mood = "comforting"
        elif self.data["reactionUntil"] > now:
            mood = self.data["reaction"]
        elif self.data["energy"] < .32:
            mood = "sleepy"
        elif self.data["valence"] > .54:
            mood = "cheerful"
        elif self.data["curiosity"] > .64:
            mood = "curious"
        else:
            mood = "relaxed"
        return {key: self.data[key] for key in ("valence", "energy", "trust", "curiosity", "events")} | {"mood": mood, "name": MOODS[mood], "reason": REASONS[self.data["reason"]], "status": self.status, "error": self.error, "simulated": True}

    def line(self, situation="idle", user_mood="neutral"):
        state = self.snapshot()
        if user_mood in ("sad", "anxious", "anger") or state["mood"] == "comforting":
            options = ("我在。你可以慢慢说，也可以先不说，我陪你待一会儿。", "这会儿我轻一点，不催你。想聊的时候叫我就好。", "要我陪你理一理，还是只听你说？都可以。")
        elif user_mood == "tired" or state["mood"] == "sleepy":
            options = ("先歇一小会儿也没关系，我也把脚步放慢一点。", "不用一直绷着，今天已经走了不少路啦。")
        elif state["mood"] == "cheerful":
            options = ("刚才那点小互动，让我这片云都轻快起来了。", "嘿嘿，今天适合把一点点开心攒起来。", "我有点雀跃，想转一小圈给你看。")
        elif state["mood"] == "curious":
            options = ("我这会儿有点想学新知识。你想聊地理、星空，还是别的？", "我现在好奇心有点冒头，想和你一起弄懂一件小事。")
        elif state["mood"] == "shy":
            options = ("刚才被你蹭了脸，有一点害羞……不过不讨厌。", "嗯……我先把这点小开心藏在云里。")
        else:
            options = ("我在呢。想聊什么，就慢慢说给我听。", "你忙你的，我在旁边安安稳稳地陪着。", "这会儿很平静。有什么想分享的小事吗？", "嗯，我听着。你想让我陪你聊聊，还是一起想办法？")
        return options[int(state["events"]) % len(options)]
