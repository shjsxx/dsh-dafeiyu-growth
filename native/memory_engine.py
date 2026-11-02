"""Local + automatic public memory for the native companion.

Local memories are bounded summaries of the owner's existing diary and learned
habits. Public memories are summaries that the existing, opt-in public-learning
scheduler already fetched and validated; this engine never starts its own
crawler, never bypasses robots/terms and never adds a new network request.

The store is versioned, atomic, bounded and privacy-deletable. It contains no
chat transcripts beyond the summaries already stored in the growth profile.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path

from semantic_memory import similarity as semantic_similarity
from embedding_backend import EmbeddingBackend

VERSION = 1
MAX_BYTES = 512 * 1024
MAX_RECORDS = 600
MAX_TEXT = 240
MAX_SOURCE = 60
KIND_WEIGHT = {"preference": 82, "habit": 72, "diary": 58, "public": 46, "culture": 62}
KIND_LABEL = {"preference": "偏好", "habit": "习惯", "diary": "回顾", "public": "公开知识", "culture": "诗文"}


def default_path():
    explicit = os.environ.get("DSH_DAFEIYU_MEMORY_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "memory.json"
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "memory.json"


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _count(value, cap=10 ** 13):
    number = _finite(value)
    return int(max(0, min(cap, number))) if number is not None else 0


def _plain(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _identity(kind, text):
    return hashlib.blake2b((kind + "\0" + text).encode("utf-8"), digest_size=8).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


class MemoryEngine:
    def __init__(self, path=None, clock=None):
        self.path = Path(path).expanduser() if path is not None else default_path()
        self._clock = clock or (lambda: time.time())
        self.status = "new"
        self.error = ""
        self.data = None
        self._embedding = None
        self._vectors = {}
        self._load()

    def _load(self):
        try:
            with self.path.open("rb") as source:
                body = source.read(MAX_BYTES + 1)
        except FileNotFoundError:
            self.data = self._empty()
            return
        except OSError:
            self.status, self.error = "protected", "memory file is unreadable"
            return
        if len(body) > MAX_BYTES:
            self.status, self.error = "protected", "memory file is too large"
            return
        try:
            raw = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs)
            if not isinstance(raw, dict) or raw.get("version") != VERSION:
                raise ValueError
            records = raw.get("records")
            if not isinstance(records, list):
                raise ValueError
            clean = []
            for row in records[-MAX_RECORDS:]:
                if not isinstance(row, dict) or row.get("kind") not in KIND_WEIGHT:
                    continue
                text = _plain(row.get("text"))
                if not text:
                    continue
                clean.append({
                    "id": _identity(row["kind"], text),
                    "kind": row["kind"],
                    "text": text,
                    "source": _plain(row.get("source"), MAX_SOURCE) or row["kind"],
                    "importance": max(0, min(100, _count(row.get("importance")))),
                    "createdAt": _count(row.get("createdAt")),
                    "lastSeen": _count(row.get("lastSeen")),
                    "hits": max(1, _count(row.get("hits"), 1000)),
                })
            self.data = {"version": VERSION, "revision": _count(raw.get("revision")),
                         "records": clean, "publicRevision": _count(raw.get("publicRevision"))}
            self.status = "ready"
        except (ValueError, TypeError, UnicodeDecodeError):
            self.data = None
            self.status, self.error = "protected", "memory file is corrupt or unsupported"

    def _empty(self):
        return {"version": VERSION, "revision": 0, "records": [], "publicRevision": 0}

    def _save(self, before):
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self.status = "ready"
            self.error = ""
            return True
        except (OSError, ValueError, TypeError):
            self.data = before
            self.status, self.error = "error", "memory file could not be written"
            return False
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _remember(self, kind, text, source, now):
        text = _plain(text)
        if not text:
            return False
        identity = _identity(kind, text)
        existing = next((row for row in self.data["records"] if row["id"] == identity), None)
        if existing:
            existing["lastSeen"] = now
            existing["hits"] = min(1000, existing["hits"] + 1)
            existing["importance"] = min(100, existing["importance"] + 1)
            return False
        self.data["records"].append({
            "id": identity, "kind": kind, "text": text, "source": _plain(source, MAX_SOURCE) or kind,
            "importance": KIND_WEIGHT[kind], "createdAt": now, "lastSeen": now, "hits": 1,
        })
        return True

    @staticmethod
    def _prune(records):
        rows = sorted(records, key=lambda row: (row["importance"] + min(20, row["hits"]), row["lastSeen"]))
        keep = rows[-MAX_RECORDS:]
        keep.sort(key=lambda row: row["lastSeen"], reverse=True)
        return keep

    def ingest_behavior(self, behavior, now=None):
        """Remember explicitly provided name/likes from interaction memory."""
        if self.status == "protected" or not isinstance(behavior, dict):
            return 0
        stamp = int(self._clock() if now is None else now)
        before = {**self.data, "records": [dict(row) for row in self.data["records"]]}
        added = 0
        name = _plain(behavior.get("userName"), 20)
        if name and self._remember("preference", "称呼：" + name, "local_preference", stamp):
            added += 1
        likes = behavior.get("likes") if isinstance(behavior.get("likes"), list) else []
        for like in likes[:6]:
            text = _plain(like, 40)
            if text and self._remember("preference", "喜欢：" + text, "local_preference", stamp):
                added += 1
        if not added:
            return 0
        self.data["records"] = self._prune(self.data["records"])
        self.data["revision"] += 1
        return 1 if self._save(before) else 0

    def ingest_state(self, state, now=None):
        """Remember bounded diary/learned summaries from the existing profile."""
        if self.status == "protected" or not isinstance(state, dict):
            return 0
        stamp = int(self._clock() if now is None else now)
        before = {**self.data, "records": [dict(row) for row in self.data["records"]]}
        added = 0
        learned = state.get("learned") if isinstance(state.get("learned"), list) else []
        for row in learned[:50]:
            if isinstance(row, dict) and self._remember("habit", row.get("text"), "local_habit", stamp):
                added += 1
        diary = state.get("diary") if isinstance(state.get("diary"), list) else []
        for row in diary[-50:]:
            if isinstance(row, dict) and self._remember("diary", row.get("text"), "local_diary", stamp):
                added += 1
        if not added:
            return 0
        self.data["records"] = self._prune(self.data["records"])
        self.data["revision"] += 1
        return 1 if self._save(before) else 0

    def ingest_knowledge(self, records, revision, now=None):
        """Absorb already-fetched, validated public records; never fetch here."""
        if self.status == "protected" or not isinstance(records, list):
            return 0
        revision = _count(revision)
        if revision <= 0 or revision == self.data.get("publicRevision"):
            return 0
        stamp = int(self._clock() if now is None else now)
        before = {**self.data, "records": [dict(row) for row in self.data["records"]]}
        added = 0
        for row in records[:600]:
            if not isinstance(row, dict):
                continue
            topic, text = _plain(row.get("topic"), 60), _plain(row.get("text"))
            if not text:
                continue
            summary = (topic + "：" if topic else "") + text
            source = _plain(row.get("source") or row.get("sourceName"), MAX_SOURCE) or "public"
            if self._remember("public", summary, source, stamp):
                added += 1
        changed = revision != self.data.get("publicRevision")
        self.data["publicRevision"] = revision
        if added:
            self.data["records"] = self._prune(self.data["records"])
            self.data["revision"] += 1
        return 1 if changed and self._save(before) else 0

    def ingest_external(self, record, now=None):
        """Remember one bounded, licensed public summary from the crawler."""
        if self.status == "protected" or not isinstance(record, dict):
            return 0
        topic, text = _plain(record.get("topic"), 60), _plain(record.get("text"), 600)
        kind = record.get("kind") if record.get("kind") in ("public", "culture") else "public"
        source = _plain(record.get("sourceLabel") or record.get("source"), MAX_SOURCE) or "public"
        if not topic or len(text) < 10:
            return 0
        stamp = int(self._clock() if now is None else now)
        before = {**self.data, "records": [dict(row) for row in self.data["records"]]}
        if not self._remember(kind, topic + "：" + text, source, stamp):
            return 0
        self.data["records"] = self._prune(self.data["records"])
        self.data["revision"] += 1
        return 1 if self._save(before) else 0

    def recent(self, limit=5, kinds=None):
        """Return recently active memories for broad “what do you remember” asks."""
        if self.status == "protected":
            return []
        allowed = set(kinds) if isinstance(kinds, (list, tuple, set)) else set(KIND_WEIGHT)
        rows = sorted((row for row in self.data["records"] if row["kind"] in allowed),
                      key=lambda row: (row["lastSeen"], row["importance"]), reverse=True)
        return [{"kind": row["kind"], "kindLabel": KIND_LABEL[row["kind"]], "text": row["text"],
                 "source": row["source"], "importance": row["importance"], "score": 10}
                for row in rows[:max(1, min(20, limit))]]

    def _embedding_scores(self, query, rows):
        """Neural rerank when the optional local model is installed."""
        if self._embedding is None:
            self._embedding = EmbeddingBackend()
        if not self._embedding.files_present() or not rows:
            return {}
        pending = [row["text"] for row in rows if row["id"] not in self._vectors]
        if pending:
            vectors = self._embedding.encode(pending)
            if len(vectors) != len(pending):
                return {}
            for row, vector in zip([row for row in rows if row["id"] not in self._vectors], vectors):
                self._vectors[row["id"]] = vector
            if len(self._vectors) > MAX_RECORDS:
                self._vectors = dict(list(self._vectors.items())[-MAX_RECORDS:])
        query_vector = self._embedding.encode([query])
        if not query_vector:
            return {}
        query_vector = query_vector[0]
        return {row["id"]: max(0.0, min(1.0, sum(a * b for a, b in zip(query_vector, self._vectors[row["id"]]))))
                for row in rows if row["id"] in self._vectors}

    def recall(self, query, limit=5, kinds=None):
        """Rank memories by local neural embeddings, then hashed concepts."""
        if self.status == "protected":
            return []
        query = _plain(query, 80)
        if len(query) < 2:
            return []
        allowed = set(kinds) if isinstance(kinds, (list, tuple, set)) else set(KIND_WEIGHT)
        grams = {query[i:i + 2] for i in range(len(query) - 1)}
        candidates = []
        for row in self.data["records"]:
            if row["kind"] not in allowed:
                continue
            semantic = semantic_similarity(query, row["text"])
            overlap = sum(1 for gram in grams if gram in row["text"])
            score = semantic * 100 + row["importance"] * .15 + overlap * 1.2
            if semantic >= .03 or overlap:
                candidates.append((score, row))
        candidates.sort(key=lambda item: (item[0], item[1]["lastSeen"]), reverse=True)
        if not candidates:
            candidates = [(0, row) for row in sorted(self.data["records"], key=lambda row: row["lastSeen"], reverse=True)
                          if row["kind"] in allowed][:40]
        candidates = candidates[:40]
        rows = [row for _, row in candidates]
        neural = self._embedding_scores(query, rows)
        scored = []
        for base, row in candidates:
            if row["id"] in neural:
                cosine = neural[row["id"]]
                if cosine < .40:
                    continue
                score = cosine * 100 + row["importance"] * .12
            else:
                score = base
            scored.append((score, row["lastSeen"], row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [{"kind": row["kind"], "kindLabel": KIND_LABEL[row["kind"]],
                 "text": row["text"], "source": row["source"], "importance": row["importance"],
                 "score": round(score, 1)}
                for score, _, row in scored[:max(1, min(20, limit))]]

    def snapshot(self, recent=3):
        data = self.data or self._empty()
        counts = {"preference": 0, "habit": 0, "diary": 0, "public": 0, "culture": 0}
        for row in data["records"]:
            counts[row["kind"]] += 1
        newest = sorted(data["records"], key=lambda row: row["lastSeen"], reverse=True)[:max(0, min(10, recent))]
        return {"version": VERSION, "status": self.status, "error": self.error,
                "revision": data["revision"], "total": len(data["records"]), "counts": counts,
                "embedding": "ready" if (self._embedding or EmbeddingBackend()).files_present() else "unavailable",
                "publicRevision": data.get("publicRevision", 0), "recent": [
                    {"kindLabel": KIND_LABEL[row["kind"]], "text": row["text"], "source": row["source"]}
                    for row in newest]}

    def clear(self):
        if self.status == "protected":
            return False
        before = {**self.data, "records": [dict(row) for row in self.data["records"]]}
        self.data["records"] = []
        self.data["publicRevision"] = 0
        self.data["revision"] += 1
        return self._save(before)
