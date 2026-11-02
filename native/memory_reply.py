"""Pure memory-aware reply composition; no IO, network or model imports."""
from __future__ import annotations

MEMORY_WORDS = ("记得", "还记得", "记不记得", "我说过", "我的喜好", "我喜欢什么",
                "你了解我", "我叫什么", "我的名字", "记住我", "忘了我")
QUESTION_WORDS = ("什么", "为什么", "怎么", "如何", "介绍", "知道", "讲讲", "科普")


def wants_memory(text):
    raw = "".join(str(text or "").split())
    return any(word in raw for word in MEMORY_WORDS)


def compose_memory_answer(text, name="", likes=None, hits=None):
    """Answer only from memories that actually exist; never invent details."""
    if not wants_memory(text):
        return None
    name = str(name or "").strip()[:20]
    likes = [str(item).strip()[:40] for item in (likes or []) if str(item).strip()][:4]
    hits = [row for row in (hits or []) if isinstance(row, dict) and isinstance(row.get("text"), str)]
    lines = []
    if name:
        lines.append(f"记得，你叫{name}。")
    if likes:
        lines.append("你提过喜欢：" + "、".join(likes) + "。")
    for row in hits[:3]:
        label = row.get("kindLabel") if isinstance(row.get("kindLabel"), str) else "记忆"
        lines.append(f"• {label}：{row['text'][:80]}")
    if not lines:
        return "我还没有记住足够的事情。你可以直接告诉我名字或喜好，回顾与公开知识也会自动进入记忆。"
    lines.append("这些来自本地记忆引擎；记错了可以直接纠正我。")
    return "\n".join(lines)


def compose_public_answer(text, hits):
    """Use a public memory only for an explicit knowledge-style question."""
    raw = "".join(str(text or "").split())
    if not any(word in raw for word in QUESTION_WORDS):
        return None, ""
    for row in hits or ():
        if not isinstance(row, dict) or row.get("kind") != "public":
            continue
        if not isinstance(row.get("text"), str) or int(row.get("score", 0) or 0) < 20:
            continue
        source = row.get("source") if isinstance(row.get("source"), str) else "公开来源"
        return "我记得这条公开知识：\n" + row["text"][:160], "记忆引擎 · 公开知识 · " + source[:40]
    return None, ""
