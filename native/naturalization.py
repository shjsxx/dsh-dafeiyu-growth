"""Small naturalness layer for the local rule-based replies.

This does not invent facts or call a model. It only avoids repeating the same
generic line and makes a generic fallback sound like it remembers the current
relationship topic. Specific factual/action/reply paths are left untouched.
"""
from __future__ import annotations

from chat import DEFAULT_REPLIES

GENTLE_FALLBACKS = (
    "嗯，我在听。你不用一次说清楚，慢慢来。",
    "我在。你想先说说刚才那件事，还是先安静一会儿？",
    "听见了。要我先接着刚才的话，还是你先缓一缓？",
)
TOPIC_FALLBACKS = (
    "你刚才提到{topic}，我还记得。想接着聊那件事，还是先说点别的？",
    "我还记着{topic}这件事。你现在想继续说说，还是先换口气？",
)


def _topic_name(topic):
    if isinstance(topic, dict):
        topic = topic.get("topic")
    return " ".join(str(topic or "").split())[:32]


def naturalize(text, *, user_text="", mood="neutral", last_reply="", topics=(), seed=0):
    """Return a varied local line; only generic/emotional lines are changed."""
    text = str(text or "")
    if not text:
        return text
    seed = int(seed or 0)
    names = [name for name in (_topic_name(row) for row in topics or ()) if name]
    generic = text in DEFAULT_REPLIES
    if generic and text == last_reply:
        if names:
            text = TOPIC_FALLBACKS[seed % len(TOPIC_FALLBACKS)].format(topic=names[-1])
        else:
            text = GENTLE_FALLBACKS[seed % len(GENTLE_FALLBACKS)]
    if generic:
        if mood in ("sad", "tired", "anxious", "anger"):
            text = GENTLE_FALLBACKS[seed % len(GENTLE_FALLBACKS)]
        elif names:
            text = TOPIC_FALLBACKS[seed % len(TOPIC_FALLBACKS)].format(topic=names[-1])
    return text
