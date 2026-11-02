"""Deterministic local dialogue policy: intent, follow-up and clarification.

This is a small state machine, not an LLM. It never invents an answer: when the
context is insufficient it asks which previous turn the owner means.
"""
from __future__ import annotations

import re

FOLLOWUP_PHRASES = ("然后呢", "后来呢", "接着呢", "具体呢", "还有呢", "再讲讲",
                    "说下去", "继续呢", "为什么呢", "怎么说", "详细点")
META_PHRASES = ("答非所问", "没回答", "没有回答我", "听不懂", "没听懂", "你在说什么",
                "为什么这么回答", "为什么这么说", "别敷衍", "敷衍我", "答不对")
GREETINGS = ("你好", "嗨", "在吗", "早上好", "晚上好", "hello", "hi")
EMOTION_WORDS = ("难过", "伤心", "不开心", "累", "困", "焦虑", "害怕", "生气", "开心", "高兴", "哭")
MEMORY_WORDS = ("记得", "还记得", "我说过", "喜好", "我喜欢什么", "了解我", "名字")
COMMAND_WORDS = ("跑来", "跳一个", "转一圈", "快来", "过来", "别动", "停下")
STOP = ("请问", "可以", "能不能", "帮我", "告诉我", "我想问", "就是", "那个", "然后", "所以")


def _raw(text):
    return " ".join(str(text or "").split())[:160]


def is_meta(text):
    raw = _raw(text)
    return any(word in raw for word in META_PHRASES)


def is_followup(text):
    raw = "".join(_raw(text).split())
    return len(raw) <= 18 and any(word in raw for word in FOLLOWUP_PHRASES)


def extract_topic(text):
    raw = _raw(text)
    for word in STOP:
        raw = raw.replace(word, "")
    raw = re.sub(r"[，。,.!！?？：:；;、\s]+", "", raw)
    return raw[-28:]


def classify(text):
    raw = _raw(text)
    if not raw:
        return "empty"
    if is_meta(raw):
        return "meta"
    if is_followup(raw):
        return "followup"
    if any(word in raw for word in MEMORY_WORDS):
        return "memory"
    if any(word in raw for word in COMMAND_WORDS):
        return "command"
    if any(word in raw for word in EMOTION_WORDS):
        return "emotion"
    if any(word in raw for word in GREETINGS):
        return "greeting"
    if any(mark in raw for mark in "？?") or any(word in raw for word in ("为什么", "怎么", "什么", "哪里", "谁")):
        return "question"
    return "smalltalk"


def followup_reply(text, last_topic="", last_reply=""):
    """Answer a short follow-up by anchoring to the actual previous topic."""
    raw = _raw(text)
    topic = extract_topic(last_topic) or "刚才那件事"
    if not last_reply:
        return "你想让我接着哪一句说？先给我一个开头，我就不乱猜。"
    if "为什么" in raw:
        return (f"你是在问「{topic}」的原因，还是问我刚才为什么那样回？"
                "你指一下，我只回答那一件，不绕开。")
    if "具体" in raw or "详细" in raw:
        return f"好，我把「{topic}」再说具体一点。你想先听哪一部分：发生了什么，还是我当时的感受？"
    if "还有" in raw:
        return f"关于「{topic}」，我目前只记得刚才那些；你还想补充哪一点？"
    return (f"你刚才讲到「{topic}」。后来发生了什么？"
            "如果你想让我接着编，我会先说明那是猜的。")
