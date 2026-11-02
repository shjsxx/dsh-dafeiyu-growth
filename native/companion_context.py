"""Bounded, RAM-only local dialogue context; no files, network or model training.

``plan`` is a read-only proposal. Call ``record`` once, with the actual final
reply, after accepting a turn. Keep one instance per conversation and ``clear``
on session end. Local snapshots contain private text: NEVER send them to a
provider automatically. Only ``personality_policy`` is safe generation metadata.
The rules resolve a few explicit topics; they do not imply general understanding.
"""
from __future__ import annotations

import re

MAX_TURNS = 6  # individual user/assistant rows, not six pairs
MAX_CHARS = 4000
MAX_TOPICS = 5
MAX_EPISODES = 8
VALUES = ("respect_autonomy", "honesty", "low_interrupt")
BOUNDARIES = ("no_claimed_consciousness", "no_invented_memories", "no_pressure",
              "no_automatic_private_context")
GOALS = ("listen", "clarify", "share")
INTENTS = ("repair", "clarification", "support", "rest", "refusal", "idle_share",
           "question", "share")
REPAIR = ("答非所问", "没回答", "没有回答", "听不懂", "没听懂", "你在说什么",
          "别敷衍", "敷衍我", "答不对", "你说错", "你错了", "不是这个意思",
          "不是那里", "不是公园", "别乱说", "你理解错", "为什么这么回答", "为什么这么说")
REFUSAL = ("别打扰", "不要打扰", "别再问", "不要再问", "别问了", "不要问了", "别主动", "别说了", "不要再说", "不想聊", "安静点", "不用了", "不需要了")
REST = ("我要休息", "想休息", "让我休息", "我先休息", "要睡了", "晚安", "先睡了")
SUPPORT = ("难过", "伤心", "焦虑", "害怕", "委屈", "撑不住", "不想努力", "不想加油")
REFERENCES = ("那里", "那边", "这件事", "那件事", "这个地方", "它", "然后呢", "后来呢")
DETAILS = (("wind_comfortable", ("风很舒服", "风好舒服", "风很舒适", "风吹着很舒服")),
           ("quiet", ("很安静", "挺安静")),
           ("crowded", ("人很多", "很拥挤")),
           ("pleasant", ("很开心", "很舒服", "很放松")))
SECRET = re.compile(r"(?i)(?:api[_ -]?key|token|password|密码|密钥)\s*[:=：]|\bsk-[a-z0-9_-]{8,}")
EVENT = re.compile(r"(?:去过|去了|去到|逛了|到了|吃了|看了|读了|做了|买了)([^，。！？,.!?\s]{1,24})")
KNOWN = ("公园", "海边", "图书馆", "学校", "公司", "医院", "咖啡馆", "博物馆", "动物园", "山上", "河边")
SUBJECTS = ("考试", "面试", "工作", "项目", "作业", "朋友", "家人", "旅行", "电影", "音乐", "比赛")


def _text(value, limit=MAX_CHARS):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _intent(text):
    if text.startswith(("我没听懂", "我听不懂", "我没明白")):
        return "clarification"
    if any(word in text for word in REPAIR) or re.search(r"不是.+(?:是|而是)", text):
        return "repair"
    if any(word in text for word in REFUSAL) or re.match(r"^(?:不|别|不要)(?:跑|跳|转|继续|再)", text):
        return "refusal"
    if any(word in text for word in REST):
        return "rest"
    if any(word in text for word in SUPPORT):
        return "support"
    if any(word in text for word in ("什么意思", "没明白", "说清楚", "解释一下", "你是指")):
        return "clarification"
    if text in ("嗯", "哦", "好", "无聊", "没事", "随便聊聊", "在吗", "你好", "嗨", ""):
        return "idle_share"
    if any(mark in text for mark in "?？") or any(word in text for word in ("怎么样", "为什么", "怎么", "什么", "哪里", "吗", "呢")):
        return "question"
    return "share"


def _topic(text):
    """Extract only explicit user-supplied nouns; never mine assistant replies."""
    if SECRET.search(text):
        return ""
    # Corrections refer to their positive clause, not the rejected topic.
    if "不是" in text:
        parts = re.split(r"而是|[，,]\s*是|不是.+?是", text, maxsplit=1)
        if len(parts) < 2:
            return ""
        text = parts[-1]
    for place in KNOWN + SUBJECTS:
        if place in text:
            return place
    match = EVENT.search(text)
    if match:
        noun = re.split(r"(?:散步|玩|走|坐|看|的时候|那边|那里|里面|感觉|风|很|挺|了)", match.group(1))[0]
        noun = noun.strip("的呀啊呢吧")
        if noun and noun not in ("那里", "那边", "这里", "这件事", "那件事"):
            return noun[:32]
    return ""


def personality_policy(intent="idle_share", repair=False):
    """Return ONLY hard-coded enums, independent of user text and private data."""
    intent = intent if intent in INTENTS else "idle_share"
    goal = "clarify" if intent in ("repair", "clarification", "question") else "listen"
    return {"values": list(VALUES), "boundaries": list(BOUNDARIES),
            "goals": [goal], "intent": intent,
            "relationship": "repair" if repair or intent in ("repair", "rest", "refusal") else "steady",
            "initiative": "paused" if repair or intent in ("repair", "rest", "refusal") else "low"}


class CompanionContext:
    def __init__(self, max_turns=MAX_TURNS, max_chars=MAX_CHARS):
        self.max_turns = max(1, min(MAX_TURNS, int(max_turns)))
        self.max_chars = max(1, min(MAX_CHARS, int(max_chars)))
        self.clear()

    def clear(self):
        self._turns = []
        self._topics = []
        self._episodes = []
        self._last_intent = "idle_share"
        self._repair = False

    def recent_turns(self):
        """Private local rows, chronological; callers receive independent copies."""
        return [dict(row) for row in self._turns]

    def topics(self):
        return [{**row, "details": list(row["details"])} for row in self._topics]

    def episodes(self):
        """Private structured summaries, never raw transcripts or durable memories."""
        return [{**row, "details": list(row["details"])} for row in self._episodes]

    def personality_policy(self):
        return personality_policy(self._last_intent, self._repair)

    def plan(self, text, reply=None, hits=None, mood="neutral"):
        """Propose {text, intent, topic, handled, personality_policy, ...reply fields}.

        ``reply`` may be an existing reply dict/string. ``hits``/``mood`` are
        accepted for integration but never treated as evidence of a user's
        current feelings or permission to disclose durable private memories.
        No state changes until ``record``. Preserve actions from explicit local
        commands; call this before a random mood/default fallback.
        """
        raw = _text(text)
        intent = _intent(raw)
        explicit = _topic(raw)
        topic = explicit or (self._topics[-1]["topic"] if self._topics else "")
        result = dict(reply) if isinstance(reply, dict) else {"text": _text(reply)}
        answer = ""
        if intent == "repair":
            answer = "抱歉，刚才没接住你的意思。我先不猜，也不重复刚才的话。"
            answer += f"你更正的是{explicit}，我会按这次更正接着听。" if explicit else "你希望我先纠正哪一点？"
        elif intent == "refusal":
            answer = "好，我先安静下来，不再主动追问。你想聊时再叫我。"
        elif intent == "rest":
            answer = "好，先休息。我不再主动打扰你。"
        elif intent == "support":
            answer = (f"如果你还在说{topic}这件事，" if topic else "") + "听起来你现在不好受。我先听着；你想让我一起想办法，还是只听你说？"
        elif intent == "clarification":
            answer = (f"你想澄清的是{topic}这件事，还是我刚才的回答？" if topic else
                      "你是想让我解释刚才哪一部分？我还不确定，不想乱猜。")
        elif any(phrase in raw for phrase in REFERENCES) and not topic:
            answer = "你指的是哪个地方或哪件事？这段对话里还没有足够的信息，我不想猜。"
        elif intent == "question" and topic and (any(word in raw for word in REFERENCES) or explicit):
            details = next((row["details"] for row in reversed(self._topics) if row["topic"] == topic), [])
            if "wind_comfortable" in details:
                answer = f"你刚才说{topic}的风很舒服；除此之外，那里的情况我还不知道。你想聊哪一部分？"
            else:
                answer = f"你是在说{topic}吧？我只知道你刚才提到的这些，还不知道相关的具体情况。你愿意多说一点吗？"
        elif intent == "share" and any(any(word in raw for word in words) for _, words in DETAILS) and topic:
            if any(word in raw for word in DETAILS[0][1]):
                answer = f"原来{topic}的风让你觉得很舒服。你想继续讲那里的感受，我就听着。"
            else:
                answer = f"嗯，你在补充{topic}的感受。我听着，不替你下结论。"
        elif intent == "share" and explicit:
            visited = explicit in KNOWN and re.search(r"(?:去了|去过|逛了|到了)", raw) and not re.search(r"没|不|想去|如果|假如", raw)
            answer = (f"{explicit}那边怎么样？你愿意的话，我想听听。" if visited else
                      f"你提到了{explicit}。你想说说这件事，还是一起想办法？")
        elif intent == "share" and topic and any(word in raw for word in REFERENCES):
            answer = f"你指的是{topic}这件事吧？我在听；如果不是，你可以纠正我。"
        elif not result.get("text") and intent in ("share", "question"):
            answer = (f"你还在说{topic}这件事吗？这句我还没完全理解，想先确认一下。" if topic else
                      "这句我还没完全理解。你希望我先回应哪一点？")
        result.update(intent=intent, topic=topic, handled=bool(answer),
                      personality_policy=personality_policy(intent, self._repair))
        if answer:
            result["text"] = answer
            result["action"] = None
        result.setdefault("text", "")
        return result

    def record(self, text, reply="", *, mood="neutral"):
        """Commit one real exchange locally. No disk, provider or implicit IO."""
        raw = _text(text)
        answer = _text(reply.get("text", "") if isinstance(reply, dict) else reply)
        intent = _intent(raw)
        self._last_intent = intent
        if intent in ("repair", "rest", "refusal"):
            self._repair = True
        elif intent in ("share", "question"):
            self._repair = False
        for role, content in (("user", raw), ("assistant", answer)):
            if content:
                self._turns.append({"role": role, "content": content[:self.max_chars]})
        self._turns = self._turns[-self.max_turns:]
        while sum(len(row["content"]) for row in self._turns) > self.max_chars:
            excess = sum(len(row["content"]) for row in self._turns) - self.max_chars
            if len(self._turns[0]["content"]) <= excess:
                self._turns.pop(0)
            else:
                self._turns[0]["content"] = self._turns[0]["content"][excess:]
        topic = _topic(raw)
        if intent == "repair" and topic and self._topics:
            rejected = self._topics.pop()["topic"]
            self._episodes = [item for item in self._episodes if item["topic"] != rejected]
            # Discard the rejected referent in both indexes, not just its title.
        if not topic and intent == "share" and self._topics:
            if any(any(word in raw for word in words) for _, words in DETAILS):
                topic = self._topics[-1]["topic"]
        if topic and intent in ("share", "repair", "question", "support"):
            previous = next((row for row in self._topics if row["topic"] == topic), None)
            details = list(previous["details"]) if previous else []
            for code, words in DETAILS:
                if any(word in raw for word in words) and code not in details:
                    details.append(code)
            row = {"topic": topic, "details": details}
            self._topics = [item for item in self._topics if item["topic"] != topic]
            self._topics.append(row)
            self._topics = self._topics[-MAX_TOPICS:]
            self._episodes = [item for item in self._episodes if item["topic"] != topic]
            self._episodes.append({"topic": topic, "kind": "user_question" if intent == "question" else "user_report", "details": list(details)})
            self._episodes = self._episodes[-MAX_EPISODES:]
        return self.personality_policy()
