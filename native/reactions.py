"""Deterministic reaction planning for the native companion.

Maps explicit interaction words and the local emotion label to a short visual
reaction (motion/particles/bubble). Pure: no Qt, IO, assets or network.
"""
from __future__ import annotations

REACTIONS = {
    "funny": {
        "label": "搞笑", "message": "哈哈哈，那我给你转个圈——",
        "detail": "差点把自己转晕，但气氛轻一点了", "duration": 4200,
        "effect": "sparkle", "motion": "spin",
    },
    "come": {
        "label": "快来", "message": "来啦来啦，我这就过来！",
        "detail": "小跑着来找你，别走太快", "duration": 3800,
        "effect": "speed", "motion": "come",
    },
    "sad": {
        "label": "难过", "message": "唔……我陪你安静一会儿。",
        "detail": "耳朵垂下来，不急着说话", "duration": 5200,
        "effect": "rain", "motion": "droop",
    },
    "bored": {
        "label": "无聊", "message": "好无聊呀……要不我们数云朵？",
        "detail": "尾巴晃两下，等你给点事做", "duration": 4600,
        "effect": "question", "motion": "sway",
    },
    "sleepy": {
        "label": "打瞌睡", "message": "呼……我眯一小会儿。",
        "detail": "Zzz 慢慢飘起来，别吵醒我", "duration": 6000,
        "effect": "zzz", "motion": "doze",
    },
    "cry": {
        "label": "流泪", "message": "呜……让我哭一小会儿。",
        "detail": "眼泪掉下来，但我会慢慢好起来", "duration": 5400,
        "effect": "tears", "motion": "droop",
    },
}

# Explicit words win over the classifier, which is often wrong on short input.
WORD_RULES = (
    ("cry", ("哭", "流泪", "眼泪", "想哭", "泪目")),
    ("sleepy", ("打瞌睡", "困了", "好困", "想睡", "睡觉", "晚安", "眯一会")),
    ("bored", ("无聊", "没意思", "好闲", "没事做")),
    ("funny", ("搞笑", "逗我", "笑话", "哈哈", "好笑", "整活")),
    ("come", ("快来", "过来", "来一下", "来找我", "快过来")),
    ("sad", ("难过", "伤心", "不开心", "失落", "委屈")),
)
MOOD_RULES = (("sad", "sad"), ("tired", "sleepy"), ("anger", "sad"),
              ("anxious", "sad"), ("joy", "funny"))


def detect_reaction(text, mood="neutral"):
    raw = "".join(str(text or "").split())[:120]
    for reaction, words in WORD_RULES:
        if any(word in raw for word in words):
            return reaction
    for label, reaction in MOOD_RULES:
        if mood == label:
            return reaction
    return ""


def plan(reaction):
    """Return a bounded, immutable-by-convention plan for one reaction."""
    spec = REACTIONS.get(reaction)
    if not spec:
        return None
    return {
        "key": reaction, "label": spec["label"], "message": spec["message"],
        "detail": spec["detail"], "duration": max(1200, min(8000, int(spec["duration"]))),
        "effect": spec["effect"], "motion": spec["motion"],
    }
