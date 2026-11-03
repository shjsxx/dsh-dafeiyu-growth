"""Local + automatic public memory for the native companion.

Local memories are bounded summaries of the owner's existing diary and learned
habits. Public memories are summaries that the existing, opt-in public-learning
scheduler already fetched and validated; this engine never starts its own
crawler, never bypasses robots/terms and never adds a new network request.

The store is versioned, atomic, bounded and privacy-deletable. It contains no
chat transcripts beyond the summaries already stored in the growth profile.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import os
import re
import threading
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


def _slot(kind, text):
    """Stable, hashed factual slots, never semantic/LLM-inferred personality."""
    canonical = text
    if kind == "preference" and re.match(r"^称呼\s*[:：]", text):
        canonical = "name"
    elif kind == "habit":
        patterns = (("tool", r"^最常用的工具[是为]"),
                    ("hour", r"^你最常在\s*\d+\s*点活跃"),
                    ("streak", r"^已经连续\s*\d+\s*天陪你工作"),
                    ("project", r"^在 .+ 这个项目上干得最多"),
                    ("errors", r"^遇到过\s*\d+\s*次工具报错"))
        for label, pattern in patterns:
            if re.search(pattern, text):
                canonical = label
                break
        else:
            canonical = re.sub(r"\d+(?:\.\d+)?\s*(?=次|回|天|小时|分钟)", "#", text)
    return _identity("slot:" + kind, canonical)


def _merge_rows(records):
    """Latest assertion wins a slot; merge metadata without inventing accesses."""
    merged = {}
    for incoming in records:
        row = dict(incoming)
        row["slot"] = _slot(row["kind"], row["text"])
        old = merged.get(row["slot"])
        if old is None:
            merged[row["slot"]] = row
            continue
        latest, previous = (row, old) if (row["lastSeen"], row["createdAt"]) >= (old["lastSeen"], old["createdAt"]) else (old, row)
        result = dict(latest)
        result["createdAt"] = min(old["createdAt"], row["createdAt"])
        result["hits"] = max(old["hits"], row["hits"])
        result["previousIds"] = list(dict.fromkeys(previous.get("previousIds", []) +
            [previous["id"]] + latest.get("previousIds", []))) [-8:]
        result["previousIds"] = [value for value in result["previousIds"] if value != result["id"]]
        merged[row["slot"]] = result
    return list(merged.values())


def _locked(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


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
        self._reranker = None
        self._reranker_checked = False
        self._vectors_ready = False
        self._lock = threading.RLock()
        self._embedding_lock = threading.RLock()
        self._vector_epoch = 0
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
            if not isinstance(raw, dict) or isinstance(raw.get("version"), bool) or raw.get("version") != VERSION:
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
                    "slot": _slot(row["kind"], text),
                    "previousIds": [value for value in row.get("previousIds", [])[-8:]
                                    if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{16}", value)]
                                    if isinstance(row.get("previousIds"), list) else [],
                })
            blocked = raw.get("blocked")
            blocked = [str(value)[:32] for value in blocked[:MAX_RECORDS]] if isinstance(blocked, list) else []
            blocked_slots = raw.get("blockedSlots", [])
            blocked_slots = [value for value in blocked_slots[-MAX_RECORDS:]
                             if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{16}", value)] if isinstance(blocked_slots, list) else []
            # Old exact tombstones can be promoted when their corresponding row
            # is still present; hashes alone cannot reconstruct deleted plaintext.
            blocked_slots = list(dict.fromkeys(blocked_slots +
                [row["slot"] for row in clean if row["id"] in blocked]))[-MAX_RECORDS:]
            clean = [row for row in clean if row["id"] not in blocked and row["slot"] not in blocked_slots]
            self.data = {"version": VERSION, "revision": _count(raw.get("revision")),
                         "records": _merge_rows(clean), "blocked": blocked, "blockedSlots": blocked_slots,
                         "publicRevision": _count(raw.get("publicRevision"))}
            self.status = "ready"
        except (ValueError, TypeError, UnicodeDecodeError):
            self.data = None
            self.status, self.error = "protected", "memory file is corrupt or unsupported"

    def _empty(self):
        return {"version": VERSION, "revision": 0, "records": [], "blocked": [], "blockedSlots": [], "publicRevision": 0}

    @_locked
    def _save(self, before):
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            # Count caps are not byte caps: 600 records at maximum Chinese text
            # can exceed MAX_BYTES. Drop the least important rows until the
            # serialized document that we are about to load fits.
            while len(text.encode("utf-8")) > MAX_BYTES and self.data["records"]:
                victims = sorted(range(len(self.data["records"])),
                                 key=lambda index: (self.data["records"][index]["importance"]
                                                    + min(20, self.data["records"][index]["hits"]),
                                                    self.data["records"][index]["lastSeen"]))
                del self.data["records"][victims[0]]
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
            self._vector_epoch += 1
            valid = {row["id"] for row in self.data["records"]}
            self._vectors = {identity: vector for identity, vector in self._vectors.items() if identity in valid}
            self._vectors_ready = bool(valid) and valid.issubset(self._vectors)
            return True
        except (OSError, ValueError, TypeError):
            self.data = before
            self.status, self.error = "error", "memory file could not be written"
            return False
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    def _remember(self, kind, text, source, now):
        text = _plain(text)
        if not text:
            return False
        identity, slot = _identity(kind, text), _slot(kind, text)
        if identity in self.data.get("blocked", []) or slot in self.data.get("blockedSlots", []):
            return False
        existing = next((row for row in self.data["records"]
                         if row["id"] == identity or row.get("slot", _slot(row["kind"], row["text"])) == slot), None)
        if existing:
            if existing["id"] == identity:
                return False  # a profile refresh is NOT evidence of access/relevance
            prior_ids = existing.get("previousIds", []) + [existing["id"]]
            existing.update(id=identity, text=text, slot=slot,
                            source=_plain(source, MAX_SOURCE) or kind, lastSeen=now,
                            previousIds=[value for value in dict.fromkeys(prior_ids) if value != identity][-8:])
            return True  # changed grounded assertion: persist while preserving age/hits
        self.data["records"].append({
            "id": identity, "slot": slot, "previousIds": [],
            "kind": kind, "text": text, "source": _plain(source, MAX_SOURCE) or kind,
            "importance": KIND_WEIGHT[kind], "createdAt": now, "lastSeen": now, "hits": 1,
        })
        return True

    @staticmethod
    def _prune(records):
        rows = sorted(records, key=lambda row: (row["importance"] + min(20, row["hits"]), row["lastSeen"]))
        keep = rows[-MAX_RECORDS:]
        keep.sort(key=lambda row: row["lastSeen"], reverse=True)
        return keep

    @_locked
    def ingest_behavior(self, behavior, now=None):
        """Remember explicitly provided name/likes from interaction memory."""
        if self.status == "protected" or not isinstance(behavior, dict):
            return 0
        stamp = int(self._clock() if now is None else now)
        before = copy.deepcopy(self.data)
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

    @_locked
    def ingest_state(self, state, now=None):
        """Remember bounded diary/learned summaries from the existing profile."""
        if self.status == "protected" or not isinstance(state, dict):
            return 0
        stamp = int(self._clock() if now is None else now)
        before = copy.deepcopy(self.data)
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

    @_locked
    def ingest_knowledge(self, records, revision, now=None):
        """Absorb already-fetched, validated public records; never fetch here."""
        if self.status == "protected" or not isinstance(records, list):
            return 0
        revision = _count(revision)
        if revision <= 0 or revision == self.data.get("publicRevision"):
            return 0
        stamp = int(self._clock() if now is None else now)
        before = copy.deepcopy(self.data)
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

    @_locked
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
        before = copy.deepcopy(self.data)
        if not self._remember(kind, topic + "：" + text, source, stamp):
            return 0
        self.data["records"] = self._prune(self.data["records"])
        self.data["revision"] += 1
        return 1 if self._save(before) else 0

    @_locked
    def recent(self, limit=5, kinds=None):
        """Return recently active memories for broad “what do you remember” asks."""
        if self.status == "protected":
            return []
        allowed = set(kinds) if isinstance(kinds, (list, tuple, set)) else set(KIND_WEIGHT)
        rows = sorted((row for row in self.data["records"] if row["kind"] in allowed),
                      key=lambda row: (row["lastSeen"], row["importance"]), reverse=True)
        return [{"id": row["id"], "slot": row.get("slot", _slot(row["kind"], row["text"])),
                 "kind": row["kind"], "kindLabel": KIND_LABEL[row["kind"]], "text": row["text"],
                 "source": row["source"], "importance": row["importance"],
                 "score": 0, "retrieval": "recent"}
                for row in rows[:max(1, min(20, limit))]]

    @_locked
    def consolidated(self, limit=12):
        """Passive, bounded factual view: preferences/habits/episodes only.

        Each item is an existing assertion, never an invented narrative or a
        personality inference. IDs retain bounded previous row hashes; source
        references claim only known current provenance. This LOCAL view contains
        private memories: it is not implicit
        permission to send them to a provider. No scoring/access counters change.
        """
        if self.status == "protected":
            return []
        groups = {"preference": "preferences", "habit": "habits", "diary": "episodes"}
        rows = _merge_rows([dict(row) for row in self.data["records"] if row["kind"] in groups])
        rows.sort(key=lambda row: (row["lastSeen"], row["createdAt"]), reverse=True)
        result = []
        for row in rows[:max(0, min(30, _count(limit, 30)))]:
            ids = list(dict.fromkeys([row["id"]] + row.get("previousIds", [])))[:9]
            result.append({"group": groups[row["kind"]], "kind": row["kind"],
                           "id": row["id"], "ids": ids, "slot": row["slot"],
                           "text": row["text"], "source": row["source"], "sources": [row["source"]],
                           "references": [{"id": row["id"], "source": row["source"]}],
                           "createdAt": row["createdAt"], "lastSeen": row["lastSeen"], "hits": row["hits"]})
        return result

    def _encode(self, texts):
        # Session/tokenizer lazy load and encode must not race between recall
        # and a background warmup. Never acquire the data lock while held here.
        with self._embedding_lock:
            return self._embedding.encode(texts)

    def prepare_vectors(self, batch=32):
        """Warm copied rows outside data lock; discard results after any mutation."""
        with self._lock:
            if self.status == "protected" or self._vectors_ready:
                return False
            if self._embedding is None:
                self._embedding = EmbeddingBackend()
            if not self._embedding.files_present():
                return False
            epoch = self._vector_epoch
            rows = [dict(row) for row in self.data["records"] if row["id"] not in self._vectors]
        try:
            size = max(1, min(MAX_RECORDS, int(batch)))
            for start in range(0, len(rows), size):
                chunk = rows[start:start + size]
                vectors = self._encode([row["text"] for row in chunk])
                if len(vectors) != len(chunk):
                    return False
                with self._lock:
                    if epoch != self._vector_epoch:
                        return False
                    for row, vector in zip(chunk, vectors):
                        self._vectors[row["id"]] = vector
            with self._lock:
                if epoch != self._vector_epoch:
                    return False
                self._vectors_ready = True
            return True
        except (OSError, ValueError, TypeError, RuntimeError):
            return False

    def _embedding_scores(self, query, rows):
        """Neural rerank when the optional local model is installed."""
        if self._embedding is None:
            self._embedding = EmbeddingBackend()
        if not self._embedding.files_present() or not rows:
            return {}
        pending = [row["text"] for row in rows if row["id"] not in self._vectors]
        if pending:
            vectors = self._encode(pending)
            if len(vectors) != len(pending):
                return {}
            for row, vector in zip([row for row in rows if row["id"] not in self._vectors], vectors):
                self._vectors[row["id"]] = vector
            if len(self._vectors) > MAX_RECORDS:
                self._vectors = dict(list(self._vectors.items())[-MAX_RECORDS:])
        query_vector = self._encode([query])
        if not query_vector:
            return {}
        query_vector = query_vector[0]
        return {row["id"]: max(0.0, min(1.0, sum(a * b for a, b in zip(query_vector, self._vectors[row["id"]]))))
                for row in rows if row["id"] in self._vectors}

    @_locked
    def reranker(self):
        if self._reranker_checked:
            return self._reranker
        self._reranker_checked = True
        if os.environ.get("DSH_DAFEIYU_RERANK", "").strip() == "0":
            return None
        try:
            from embedding_benchmark import Reranker
            candidate = Reranker.load()
            if candidate and candidate.enabled and candidate.metrics.get("improved"):
                self._reranker = candidate
        except (ImportError, OSError, ValueError, TypeError):
            self._reranker = None
        return self._reranker

    @_locked
    def recall(self, query, limit=5, kinds=None):
        """Rank memories by local neural embeddings, then hashed concepts."""
        if self.status == "protected":
            return []
        query = _plain(query, 80)
        if len(query) < 2:
            return []
        allowed = set(kinds) if isinstance(kinds, (list, tuple, set)) else set(KIND_WEIGHT)
        grams = {query[i:i + 2] for i in range(len(query) - 1)}
        rows_all = [row for row in self.data["records"] if row["kind"] in allowed]
        candidates = []
        for row in rows_all:
            semantic = semantic_similarity(query, row["text"])
            overlap = sum(1 for gram in grams if gram in row["text"])
            score = semantic * 100 + row["importance"] * .15 + overlap * 1.2
            if semantic >= .03 or overlap:
                candidates.append((score, row))
        candidates.sort(key=lambda item: (item[0], item[1]["lastSeen"]), reverse=True)
        # Small stores and a warmed vector cache are scored in full. A cold
        # 600-row store falls back to a wider lexical shortlist (80, not 40).
        if len(rows_all) <= 80 or self._vectors_ready:
            candidates = [(score, row) for score, row in candidates]
            seen = {row["id"] for _, row in candidates}
            candidates.extend((0, row) for row in rows_all if row["id"] not in seen)
        else:
            candidates = candidates[:80]
        candidates = candidates[:max(80, min(600, len(rows_all)))]
        rows = [row for _, row in candidates]
        neural = self._embedding_scores(query, rows)
        reranker = self.reranker() if neural else None
        scored = []
        for base, row in candidates:
            if row["id"] in neural:
                cosine = neural[row["id"]]
                if cosine < .40:
                    continue
                if reranker is not None:
                    try:
                        from embedding_benchmark import feature_row
                        score = reranker.score(feature_row(query, row, cosine)) * 100
                    except (ImportError, AttributeError, IndexError, TypeError, ValueError):
                        score = cosine * 100 + row["importance"] * .12
                else:
                    score = cosine * 100 + row["importance"] * .12
            else:
                if base <= 0:
                    continue  # neural shortlist expansion is not lexical evidence
                score = base
            scored.append((score, row["lastSeen"], row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [{"id": row["id"], "slot": row.get("slot", _slot(row["kind"], row["text"])),
                 "kind": row["kind"], "kindLabel": KIND_LABEL[row["kind"]],
                 "text": row["text"], "source": row["source"], "importance": row["importance"],
                 "score": round(score, 1)}
                for score, _, row in scored[:max(1, min(20, limit))]]

    @_locked
    def snapshot(self, recent=3):
        data = self.data or self._empty()
        counts = {"preference": 0, "habit": 0, "diary": 0, "public": 0, "culture": 0}
        for row in data["records"]:
            counts[row["kind"]] += 1
        newest = sorted(data["records"], key=lambda row: row["lastSeen"], reverse=True)[:max(0, min(10, recent))]
        return {"version": VERSION, "status": self.status, "error": self.error,
                "revision": data["revision"], "total": len(data["records"]), "counts": counts,
                "embedding": "ready" if (self._embedding or EmbeddingBackend()).files_present() else "unavailable",
                "rerank": "on" if self.reranker() else "off",
                "publicRevision": data.get("publicRevision", 0), "recent": [
                    {"id": row["id"], "kindLabel": KIND_LABEL[row["kind"]], "text": row["text"], "source": row["source"]}
                    for row in newest]}

    @_locked
    def clear(self):
        if self.status == "protected":
            return False
        before = copy.deepcopy(self.data)
        blocked = list(dict.fromkeys(list(self.data.get("blocked", []))
                                     + [row["id"] for row in self.data["records"]]))[-MAX_RECORDS:]
        self.data["blocked"] = blocked
        self.data["blockedSlots"] = list(dict.fromkeys(self.data.get("blockedSlots", []) +
            [_slot(row["kind"], row["text"]) for row in self.data["records"]]))[-MAX_RECORDS:]
        self._vector_epoch += 1
        self._vectors.clear()
        self._vectors_ready = False
        self.data["records"] = []
        self.data["publicRevision"] = 0
        self.data["revision"] += 1
        return self._save(before)

    @_locked
    def clear_preferences(self):
        """Forget name/likes and prevent the same rows from being re-ingested."""
        if self.status == "protected":
            return False
        before = copy.deepcopy(self.data)
        preferences = [row for row in self.data["records"] if row["kind"] == "preference"]
        blocked = list(dict.fromkeys(list(self.data.get("blocked", []))
                                     + [row["id"] for row in preferences]))[-MAX_RECORDS:]
        self.data["blocked"] = blocked
        self.data["blockedSlots"] = list(dict.fromkeys(self.data.get("blockedSlots", []) +
            [_slot(row["kind"], row["text"]) for row in preferences]))[-MAX_RECORDS:]
        # Purge the whole derived cache rather than leave preference-derived
        # vectors or an in-flight warmup's results available after forgetting.
        self._vector_epoch += 1
        self._vectors.clear()
        self._vectors_ready = False
        self.data["records"] = [row for row in self.data["records"] if row["kind"] != "preference"]
        self.data["revision"] += 1
        return self._save(before)
