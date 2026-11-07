"""Bounded relationship memory: topics, not transcripts.

Only short topic labels, structured detail codes, the last mood label and
timestamps survive a session. No user sentence, assistant reply, URL or secret
is stored. The store is a local continuity aid, not permission to send context
to a provider.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

VERSION = 1
MAX_TOPICS = 20
MAX_EPISODES = 40
MAX_BYTES = 128 * 1024
MOODS = {"neutral", "joy", "sad", "tired", "anxious", "anger", "calm"}


def default_path():
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "relationship.json"


def _topic(value):
    return " ".join(str(value or "").split())[:32]


def _mood(value):
    return value if value in MOODS else "neutral"


class RelationshipStore:
    def __init__(self, path=None, clock=None):
        self.path = Path(path).expanduser() if path is not None else default_path()
        self._clock = clock or (lambda: time.time())
        self.status = "new"
        self.error = ""
        self.data = self._empty()
        self._load()

    def _empty(self):
        return {"version": VERSION, "revision": 0, "topics": [], "episodes": [],
                "lastMood": "neutral", "lastSeen": 0,
                "lastOpeningRevision": -1}

    def _load(self):
        try:
            body = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self.status, self.error = "protected", "relationship memory is unreadable"
            return
        if len(body) > MAX_BYTES:
            self.status, self.error = "protected", "relationship memory is too large"
            return
        try:
            raw = json.loads(body.decode("utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != VERSION:
                raise ValueError
            topics = []
            for row in raw.get("topics", [])[-MAX_TOPICS:]:
                if not isinstance(row, dict):
                    continue
                topic = _topic(row.get("topic"))
                if not topic:
                    continue
                details = [str(value)[:24] for value in (row.get("details") or [])[:8]
                           if isinstance(value, str)]
                topics.append({"topic": topic, "details": details,
                               "lastSeen": max(0, int(row.get("lastSeen") or 0))})
            episodes = []
            for row in raw.get("episodes", [])[-MAX_EPISODES:]:
                if not isinstance(row, dict):
                    continue
                topic = _topic(row.get("topic"))
                if not topic:
                    continue
                episodes.append({"topic": topic, "kind": str(row.get("kind") or "user_report")[:24],
                                 "details": [str(value)[:24] for value in (row.get("details") or [])[:8]
                                             if isinstance(value, str)]})
            self.data = {"version": VERSION, "revision": max(0, int(raw.get("revision") or 0)),
                         "topics": topics, "episodes": episodes,
                         "lastMood": _mood(raw.get("lastMood")),
                         "lastSeen": max(0, int(raw.get("lastSeen") or 0)),
                         "lastOpeningRevision": max(-1, int(raw.get("lastOpeningRevision") or -1))}
            self.status = "ready"
        except (ValueError, TypeError, UnicodeDecodeError):
            self.status, self.error = "protected", "relationship memory is corrupt or unsupported"

    def _save(self, before):
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(text.encode("utf-8")) > MAX_BYTES:
                raise ValueError("relationship memory is too large")
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self.status, self.error = "ready", ""
            return True
        except (OSError, ValueError, TypeError):
            self.data = before
            self.status, self.error = "error", "relationship memory could not be written"
            return False
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def remember(self, context, mood="neutral", now=None):
        """Store the context's structured topics; never inspect raw turns."""
        if self.status == "protected" or context is None:
            return False
        try:
            topics = context.topics()
            episodes = context.episodes()
        except (AttributeError, TypeError):
            return False
        stamp = int(self._clock() if now is None else now)
        before = json.loads(json.dumps(self.data))
        by_topic = {row["topic"]: row for row in self.data["topics"]}
        for row in topics:
            topic = _topic(row.get("topic"))
            if not topic:
                continue
            details = [str(value)[:24] for value in (row.get("details") or [])[:8]
                       if isinstance(value, str)]
            by_topic[topic] = {"topic": topic, "details": details, "lastSeen": stamp}
        self.data["topics"] = sorted(by_topic.values(), key=lambda row: row["lastSeen"],
                                      reverse=True)[:MAX_TOPICS]
        self.data["episodes"] = [
            {"topic": _topic(row.get("topic")),
             "kind": str(row.get("kind") or "user_report")[:24],
             "details": [str(value)[:24] for value in (row.get("details") or [])[:8]
                         if isinstance(value, str)]}
            for row in episodes[-MAX_EPISODES:] if _topic(row.get("topic"))
        ][-MAX_EPISODES:]
        self.data["lastMood"] = _mood(mood)
        self.data["lastSeen"] = stamp
        self.data["revision"] += 1
        return self._save(before)

    def seed_context(self, context):
        """Load only bounded structured topics into a fresh dialogue context."""
        if context is None:
            return False
        try:
            context.seed(self.data["topics"], self.data["episodes"])
            return True
        except (AttributeError, TypeError):
            return False

    def last_topic(self):
        topics = self.data.get("topics") if isinstance(self.data, dict) else []
        return topics[0]["topic"] if topics else ""

    def recall_answer(self, text):
        """Answer a direct 'what did we talk about' question from local structure."""
        if not isinstance(text, str) or not text.strip():
            return ""
        markers = ("上次", "之前", "刚才", "记得", "聊过", "说过", "提过", "接着聊")
        if not any(marker in text for marker in markers):
            return ""
        if self.status == "protected" or not isinstance(self.data, dict):
            return ""
        topics = self.data.get("topics") or []
        if not topics:
            return "我这边还没有可接上的关系话题。你可以给我一个关键词，我只会记话题，不保存原句。"
        row = topics[0]
        topic = _topic(row.get("topic"))
        if not topic:
            return ""
        details = [str(value)[:24] for value in (row.get("details") or [])[:3]
                   if isinstance(value, str) and value.strip()]
        line = f"上次我们聊到{topic}"
        if details:
            line += "，你提到过" + "、".join(details)
        line += "。"
        if self.data.get("lastMood") in ("sad", "tired", "anxious", "anger"):
            line += "后来好一点了吗？不想说也没关系。"
        return line

    def memory_context(self, limit=3):
        """Structured rows for an explicitly authorized model memory envelope."""
        if self.status == "protected" or not isinstance(self.data, dict):
            return []
        rows = []
        for row in (self.data.get("topics") or [])[:max(0, int(limit))]:
            topic = _topic(row.get("topic"))
            if not topic:
                continue
            details = [str(value)[:24] for value in (row.get("details") or [])[:3]
                       if isinstance(value, str) and value.strip()]
            text = f"关系话题：{topic}"
            if details:
                text += "；细节：" + "、".join(details)
            text += f"；最后情绪：{self.data.get('lastMood', 'neutral')}"
            rows.append({"text": text, "source": "本地关系记忆"})
        return rows

    def opening_line(self, now=None, min_gap=30 * 60, max_age=7 * 24 * 60 * 60):
        """One gentle topic callback per changed relationship revision."""
        if self.status == "protected" or not isinstance(self.data, dict):
            return ""
        topics = self.data.get("topics") or []
        if not topics:
            return ""
        revision = int(self.data.get("revision", 0))
        if int(self.data.get("lastOpeningRevision", -1)) == revision:
            return ""
        stamp = int(self._clock() if now is None else now)
        last_seen = int(self.data.get("lastSeen", 0))
        if not last_seen or stamp - last_seen < min_gap or stamp - last_seen > max_age:
            return ""
        topic = _topic(topics[0].get("topic"))
        if not topic:
            return ""
        if self.data.get("lastMood") in ("sad", "tired", "anxious", "anger"):
            return f"上次你提到{topic}，后来好一点了吗？不想说也没关系。"
        return f"上次你提到{topic}，今天想接着聊，还是先说点别的？"

    def mark_opening(self, now=None):
        if self.status == "protected" or not isinstance(self.data, dict):
            return False
        before = json.loads(json.dumps(self.data))
        self.data["lastOpeningRevision"] = int(self.data.get("revision", 0))
        self.data["lastOpeningAt"] = int(self._clock() if now is None else now)
        return self._save(before)

    def snapshot(self):
        data = self.data if isinstance(self.data, dict) else self._empty()
        return {"status": self.status, "revision": int(data.get("revision", 0)),
                "topics": [dict(row) for row in data.get("topics", [])],
                "episodes": [dict(row) for row in data.get("episodes", [])],
                "lastMood": data.get("lastMood", "neutral"),
                "lastSeen": int(data.get("lastSeen", 0))}

    def clear(self):
        if self.status == "protected":
            return False
        before = json.loads(json.dumps(self.data))
        self.data = self._empty()
        return self._save(before)
