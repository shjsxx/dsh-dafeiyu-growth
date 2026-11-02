"""Deterministic, local-only ability indices for the 本领 page.

These are practice indices derived from existing counters, not neural
capability scores, benchmarks, or promises about future engines. The module
is pure: no IO, time, randomness, Qt or model imports. It exposes a stable
schema that future growth/memory/personality engines can feed.
"""
from __future__ import annotations

import math

DIMENSIONS = (
    {"key": "action", "label": "行动", "color": "#187E83",
     "hint": "桌面练习与跑动"},
    {"key": "balance", "label": "平衡", "color": "#5B9A8F",
     "hint": "起落、摔倒与爬起来"},
    {"key": "expression", "label": "表达", "color": "#7C8FA6",
     "hint": "对话、问候与回顾"},
    {"key": "learning", "label": "学习", "color": "#A98A5F",
     "hint": "知识、情感与训练"},
    {"key": "companionship", "label": "陪伴", "color": "#B07C74",
     "hint": "连续相处与互动"},
    {"key": "memory", "label": "记忆", "color": "#8E9B65",
     "hint": "记住的日常与回顾"},
)
RANKS = ((80, "出色"), (60, "精通"), (40, "熟练"), (20, "入门"), (0, "初识"))
ENGINE_INPUTS = {
    "growth": ("xp", "level", "streak", "counters", "days"),
    "memory": ("learned", "diary", "knowledge", "emotion_lexicon"),
    "personality": ("traits", "companion", "emotion_mood"),
}


def _number(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        return float(value) if math.isfinite(value) else default
    except (OverflowError, ValueError):
        return default


def _count(value):
    return max(0, _number(value))


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _scaled(value, factor, cap=100.0):
    return max(0.0, min(cap, value * factor))


def _log_count(value, weight, cap=100.0):
    return min(cap, math.log10(1 + max(0.0, value)) * weight)


def _rank(value):
    for threshold, name in RANKS:
        if value >= threshold:
            return name
    return RANKS[-1][1]


def derive_abilities(state=None, behavior=None, training=None, emotion=None):
    """Return six bounded dimensions plus focus/strongest summaries.

    Missing or malformed inputs degrade to zero rather than raising; no field
    is trusted beyond finite numbers and simple mappings.
    """
    state, behavior = _mapping(state), _mapping(behavior)
    training, emotion = _mapping(training), _mapping(emotion)
    traits = _mapping(state.get("traits"))
    counters = _mapping(state.get("counters"))
    journal = _mapping(training.get("journal"))
    totals = _mapping(journal.get("totals"))
    knowledge = _mapping(training.get("knowledge"))
    learned = state.get("learned") if isinstance(state.get("learned"), list) else []
    diary = state.get("diary") if isinstance(state.get("diary"), list) else []

    agility, balance = _count(behavior.get("agility")), _count(behavior.get("balance"))
    runs, jumps = _count(behavior.get("runs")), _count(behavior.get("jumps"))
    falls, recoveries = _count(behavior.get("falls")), _count(behavior.get("recoveries"))
    pets, chats = _count(behavior.get("pets")), _count(behavior.get("chats"))
    turns = _count(counters.get("turns"))
    affinity = _count(traits.get("affinity"))
    stamina = _count(traits.get("stamina"))
    streak = _count(state.get("streak"))
    knowledge_count = _count(knowledge.get("count"))
    learned_count = _count(emotion.get("learned"))
    fit_samples = _count(emotion.get("fit_samples"))
    lexicon = _count(emotion.get("lexicon"))
    passes = _count(totals.get("emotion_adaptation_passes"))
    steps = _count(totals.get("weight_steps"))

    action = .5 * agility + .3 * _log_count(runs + jumps, 32) + .2 * stamina
    recovery_ratio = recoveries / max(1.0, falls)
    balance_score = .65 * balance + .2 * min(100.0, recovery_ratio * 100) + .15 * min(100.0, (runs + jumps) * 4)
    expression = .45 * _log_count(turns + chats, 34) + .3 * _scaled(len(diary), 8) + .25 * _log_count(pets + chats, 26)
    learning = .4 * _scaled(knowledge_count, 2) + .3 * _scaled(learned_count + fit_samples, 8) + .3 * min(100.0, steps * 3 + passes * 10)
    companionship = .5 * affinity + .3 * _scaled(streak, 12) + .2 * _scaled(pets + chats, 5)
    memory_score = .4 * _scaled(len(learned), 10) + .35 * _scaled(len(diary), 10) + .25 * _scaled(lexicon, 1.2)

    values = {"action": action, "balance": balance_score, "expression": expression,
              "learning": learning, "companionship": companionship, "memory": memory_score}
    basis = {
        "action": f"敏捷 {int(agility)} · 跑跳 {int(runs + jumps)} 次",
        "balance": f"平衡 {int(balance)} · 爬起 {int(recoveries)} 次",
        "expression": f"对话 {int(turns)} 轮 · 回顾 {len(diary)} 条",
        "learning": f"知识 {int(knowledge_count)} 条 · 适应 {int(passes)} 轮",
        "companionship": f"连续 {int(streak)} 天 · 互动 {int(pets + chats)} 次",
        "memory": f"记住 {len(learned)} 条 · 词典 {int(lexicon)} 词",
    }
    dimensions = []
    for spec in DIMENSIONS:
        value = int(round(max(0.0, min(100.0, values[spec["key"]]))))
        dimensions.append({**spec, "value": value, "rank": _rank(value), "basis": basis[spec["key"]]})
    overall = int(round(sum(row["value"] for row in dimensions) / len(dimensions)))
    strongest = max(dimensions, key=lambda row: row["value"])
    focus = min(dimensions, key=lambda row: row["value"])
    return {"dimensions": dimensions, "overall": overall, "strongest": strongest, "focus": focus,
            "engine_inputs": {key: tuple(value) for key, value in ENGINE_INPUTS.items()},
            "kind": "practice_index_not_certification"}
