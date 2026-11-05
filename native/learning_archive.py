"""Unified, local-only learning archive for the 本领 page.

All existing records are learning evidence: desktop practice, action-preference
observations, daily memory, and training/journal events. This module only
normalizes snapshots that the caller already holds; it performs no IO, time,
Qt or network access. Missing or hostile fields degrade to zero/empty.
"""
from __future__ import annotations

import math
from datetime import datetime

MAX_TEXT = 120
MAX_TIMELINE = 10


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _number(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _count(value):
    return max(0, int(_number(value)))


def _text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _when(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = _number(value)
        if stamp > 10**12:
            stamp /= 1000.0
        if stamp > 0:
            try:
                return datetime.fromtimestamp(stamp).strftime("%m-%d %H:%M")
            except (OverflowError, OSError, ValueError):
                return ""
    if isinstance(value, str):
        raw = value.strip()
        if len(raw) >= 16 and raw[4] == "-" and raw[7] == "-":
            return raw[:16].replace("T", " ")
    return ""


def _stamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = _number(value)
        return stamp / 1000.0 if stamp > 10**12 else stamp
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _memory_summary(snapshot):
    snapshot = _mapping(snapshot)
    counts = _mapping(snapshot.get("counts"))
    total = _count(snapshot.get("total"))
    if not total:
        total = sum(_count(value) for value in counts.values())
    recent = []
    for row in _rows(snapshot.get("recent")):
        recent.append({
            "source": "日常记忆",
            "label": _text(row.get("kindLabel"), 24) or "记忆",
            "detail": _text(row.get("text")),
            "at": "",
            "stamp": 0.0,
        })
    return {"total": total, "counts": {key: _count(value) for key, value in counts.items()},
            "recent": recent, "status": snapshot.get("status") if isinstance(snapshot.get("status"), str) else "new"}


def _preference_summary(snapshot):
    snapshot = _mapping(snapshot)
    tracked = _count(snapshot.get("actions"))
    observations = _count(snapshot.get("observations"))
    liked = _count(snapshot.get("liked"))
    disliked = _count(snapshot.get("disliked"))
    if not observations:
        for row in _rows(snapshot.get("top")) + _rows(snapshot.get("bottom")):
            observations += _count(row.get("shown"))
            liked += _count(row.get("liked"))
            disliked += _count(row.get("disliked"))
    return {"tracked": tracked, "observations": observations, "liked": liked,
            "disliked": disliked, "decisions": liked + disliked,
            "updated": _when(snapshot.get("updated"))}


def _training_summary(snapshot):
    snapshot = _mapping(snapshot)
    journal = _mapping(snapshot.get("journal"))
    totals = _mapping(journal.get("totals")) or _mapping(snapshot.get("totals"))
    events = _rows(journal.get("events")) or _rows(snapshot.get("events"))
    knowledge = _mapping(snapshot.get("knowledge"))
    knowledge_count = _count(knowledge.get("count")) or _count(totals.get("knowledge_added"))
    return {"events": max(len(events), _count(totals.get("runs"))),
            "weight_steps": _count(totals.get("weight_steps")),
            "passes": _count(totals.get("emotion_adaptation_passes")),
            "knowledge": knowledge_count,
            "status": journal.get("status") if isinstance(journal.get("status"), str) else "ready",
            "timeline": events}


def build_learning_archive(state=None, behavior=None, training=None, emotion=None,
                           memory=None, preferences=None, companion=None):
    """Return a consistent, UI-ready view of every learning source."""
    state, behavior = _mapping(state), _mapping(behavior)
    training, emotion = _mapping(training), _mapping(emotion)
    companion = _mapping(companion)

    runs, jumps = _count(behavior.get("runs")), _count(behavior.get("jumps"))
    actions = _count(behavior.get("actions"))
    falls, recoveries = _count(behavior.get("falls")), _count(behavior.get("recoveries"))
    pets, chats = _count(behavior.get("pets")), _count(behavior.get("chats"))
    counters = _mapping(state.get("counters"))
    turns = _count(counters.get("turns"))
    practice_total = runs + jumps + actions + falls + recoveries + pets + chats

    preferences_view = _preference_summary(preferences)
    memory_view = _memory_summary(memory)
    training_view = _training_summary(training)
    learned = _rows(state.get("learned")) if isinstance(state.get("learned"), list) else []
    diary = _rows(state.get("diary")) if isinstance(state.get("diary"), list) else []
    emotion_learned = _count(emotion.get("learnedCount")) or _count(emotion.get("learned"))

    timeline = []
    for row in training_view["timeline"][-5:]:
        kind = _text(row.get("kind"), 40) or "训练"
        domain = _text(row.get("domain"), 30)
        status = _text(row.get("status"), 20)
        timeline.append({"source": "训练", "label": kind,
                         "detail": " · ".join(part for part in (domain, status) if part),
                         "at": _when(row.get("finished") or row.get("started")),
                         "stamp": _stamp(row.get("finished") or row.get("started"))})
    timeline.extend(memory_view["recent"])
    for row in diary[-3:]:
        timeline.append({"source": "回顾", "label": _text(row.get("kind"), 20) or "日常",
                         "detail": _text(row.get("text")), "at": _when(row.get("at")),
                         "stamp": _stamp(row.get("at"))})
    for row in learned[-3:]:
        timeline.append({"source": "画像", "label": "长期记住",
                         "detail": _text(row.get("text")), "at": _when(row.get("at")),
                         "stamp": _stamp(row.get("at"))})
    for row in _rows(behavior.get("discoveries"))[-3:]:
        timeline.append({"source": "本领", "label": "新发现",
                         "detail": _text(row.get("text")), "at": _when(row.get("at")),
                         "stamp": _stamp(row.get("at"))})
    timeline.sort(key=lambda row: row["stamp"], reverse=True)
    timeline = timeline[:MAX_TIMELINE]

    sources = [
        {"key": "practice", "label": "练习记录", "value": practice_total,
         "detail": f"跑 {runs} · 跳 {jumps} · 动作 {actions} · 互动 {pets + chats}"},
        {"key": "preferences", "label": "动作偏好", "value": preferences_view["tracked"],
         "detail": f"观察 {preferences_view['observations']} · 反馈 {preferences_view['decisions']}"},
        {"key": "memory", "label": "日常记忆", "value": memory_view["total"],
         "detail": f"画像 {len(learned)} · 回顾 {len(diary)}"},
        {"key": "training", "label": "训练与知识", "value": training_view["events"],
         "detail": f"知识 {training_view['knowledge']} · 权重步 {training_view['weight_steps']}"},
    ]
    evidence_total = practice_total + preferences_view["observations"] + memory_view["total"] + training_view["events"]
    updated = max([item["stamp"] for item in timeline] + [0.0])
    return {
        "sources": sources,
        "practice": {"runs": runs, "jumps": jumps, "actions": actions, "falls": falls,
                     "recoveries": recoveries, "pets": pets, "chats": chats,
                     "turns": turns, "total": practice_total},
        "preferences": preferences_view,
        "memory": {**memory_view, "learned": len(learned), "diary": len(diary),
                   "emotion_learned": emotion_learned},
        "training": {key: value for key, value in training_view.items() if key != "timeline"},
        "companion": {"affinity": _count(companion.get("affinity")),
                      "days": _count(companion.get("days"))},
        "evidence_total": evidence_total,
        "updated": _when(updated) or "",
        "timeline": timeline,
        "kind": "all_records_are_learning",
    }
