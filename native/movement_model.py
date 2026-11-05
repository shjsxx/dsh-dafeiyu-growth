"""Explanatory movement/preference model for the 本领 page.

Two layers are deliberately kept separate:
  * motor execution: run speed and fall risk come from agility/balance, which
    are trained by actual runs, jumps, falls and recoveries;
  * action selection: the all-action preference table changes which animation
    is chosen and how often, but does not change motor speed or fall risk.

The module is pure and deterministic; it only formats existing counters.
"""
from __future__ import annotations

import math


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return 0.0
    return max(0.0, value) if math.isfinite(value) else 0.0


def derive_movement(behavior=None, preferences=None, total_actions=109):
    """Return current motor gauges plus an explicit preference/motor split."""
    behavior, preferences = _mapping(behavior), _mapping(preferences)
    agility = min(100.0, _count(behavior.get("agility")))
    balance = min(100.0, _count(behavior.get("balance")))
    runs, jumps = _count(behavior.get("runs")), _count(behavior.get("jumps"))
    falls, recoveries = _count(behavior.get("falls")), _count(behavior.get("recoveries"))
    distance = _count(behavior.get("distance"))
    speed = min(420.0, 240.0 + agility * 1.8)
    fall_chance = min(0.18, max(0.05, 0.18 - balance * 0.0015 - agility * 0.0005))
    stability = round((1.0 - fall_chance) * 100.0, 1)
    recovery = 100.0 if falls <= 0 else round(min(100.0, recoveries / falls * 100.0), 1)
    tracked = int(_count(preferences.get("actions")) or _count(preferences.get("tracked")))
    observations = int(_count(preferences.get("observations")))
    feedback = int(_count(preferences.get("liked")) + _count(preferences.get("disliked")))
    coverage = round(tracked / max(1, int(total_actions)) * 100.0, 1)
    return {
        "agility": round(agility, 1),
        "balance": round(balance, 1),
        "speed": int(round(speed)),
        "speed_min": 240,
        "speed_max": 420,
        "fall_chance": round(fall_chance * 100.0, 1),
        "stability": stability,
        "recovery": recovery,
        "runs": int(runs),
        "jumps": int(jumps),
        "falls": int(falls),
        "recoveries": int(recoveries),
        "distance": int(round(distance)),
        "preference_tracked": tracked,
        "preference_coverage": coverage,
        "preference_observations": observations,
        "preference_feedback": feedback,
        "at_speed_cap": agility >= 99.5,
        "at_stability_floor": fall_chance <= 0.05001,
        "layers": [
            {"key": "motor", "label": "运动执行", "inputs": "跑动 / 跳落 / 摔倒 / 爬起",
             "outputs": "跑速、摔倒率、爬起率", "effect": "真实改变动作能力"},
            {"key": "preference", "label": "动作选择", "inputs": "动作出现、喜欢、不喜欢",
             "outputs": "选哪个动作、出现频率", "effect": "改变偏好，不直接改变速度"},
            {"key": "memory", "label": "日常记忆", "inputs": "名字、喜好、回顾、知识",
             "outputs": "回应内容、选择语境", "effect": "帮助更懂你，不是肌肉记忆"},
        ],
        "notes": [
            f"跑速 = 240 + 敏捷 × 1.8，当前 {int(round(speed))} px/s"
            + ("（已达上限）" if agility >= 99.5 else ""),
            f"摔倒率 = 18% − 平衡×0.15% − 敏捷×0.05%，当前 {fall_chance * 100:.1f}%",
            f"爬起率 = {int(recoveries)} / {int(falls)} 次摔倒 = {recovery:.1f}%",
            "偏好观察决定“选哪个动作”，速度与稳定只由跑跳结果训练；"
            "日常记忆帮助回应和选择，不会直接让腿更快。",
        ],
        "kind": "motor_and_preference_are_separate",
    }
