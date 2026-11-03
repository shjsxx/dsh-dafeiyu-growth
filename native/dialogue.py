"""Short-term dialogue repair: meta questions, follow-ups and honest fallbacks.

Pure functions with no IO. They never invent an answer: when the local rules
miss, they say so and quote the immediately previous turn instead of emitting
a random companion line.
"""
from __future__ import annotations

META_PHRASES = ("答非所问", "没回答", "没有回答我", "听不懂", "没听懂", "你在说什么",
                "为什么这么回答", "为什么这么说", "别敷衍", "敷衍我", "答不对")
QUESTION_MARKS = ("?", "？")


def _short(value, limit=42):
    return " ".join(str(value or "").split())[:limit]


def is_question(text):
    raw = "".join(str(text or "").split())
    if any(mark in raw for mark in QUESTION_MARKS):
        return True
    return any(word in raw for word in ("为什么", "怎么", "什么", "哪里", "谁", "多少", "吗", "呢"))


def is_meta_question(text):
    raw = "".join(str(text or "").split())
    return any(phrase in raw for phrase in META_PHRASES)


def meta_reply(text, last_user="", last_reply=""):
    """Explain a missed turn using the actual previous exchange, if present."""
    if not is_meta_question(text):
        return ""
    current, previous_question, previous_answer = _short(text, 32), _short(last_user), _short(last_reply)
    if previous_answer and any(marker in previous_answer for marker in
                               ("没接上", "没有正面回答", "你问「")):
        return ("我不再重复解释自己了。你直接把想让我回答的那句话发给我，"
                "我只回答那一句，不再加陪伴话术。")
    if previous_question and previous_answer:
        return (
            f"你问「{current}」，我上一句回的是「{previous_answer}」。"
            f"那句话是在接住你前面说的「{previous_question}」，没有正面回答你的“为什么”，确实算没接上。"
            "你想让我解释刚才为什么没回答，还是回到那件事继续说？"
        )
    return ("刚才那句确实没接住你的话。你愿意把问题再说一次吗？"
            "这次我先不急着给陪伴话术，先确认你在问什么。")


def fallback_reply(text, last_user=""):
    """Last-resort local reply: honest, contextual, never a random mood line."""
    previous = _short(last_user)
    if is_question(text):
        if previous:
            return f"这句我还没答上来。你问的是「{previous}」，我理解得对吗？可以再说具体一点。"
        return "这句我还没答上来。你愿意换个说法，或告诉我你最想我先回答哪一点吗？"
    if previous:
        return f"我还没完全接住。你是想说「{previous}」这件事吗？可以再给我一点上下文。"
    return "我还没完全接住。你愿意多说一点吗？我先听，不急着给结论。"
