"""Domain rerank benchmark for local memory embeddings.

A tiny logistic reranker over frozen BGE cosine features, trained and evaluated
on a synthetic Chinese memory fixture. It never fine-tunes the base model and
contains no text: only feature weights and aggregate metrics are persisted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

from embedding_backend import EmbeddingBackend
from semantic_memory import concept_overlap, similarity as hash_similarity

# v2 invalidates weights trained against earlier feature/tokenizer behavior.
VERSION = 2
MAX_RERANK_BYTES = 2 * 1024 * 1024
FEATURE_REVISION = 2
FEATURES = ("cosine", "hash", "concept", "overlap", "kind_prior", "importance", "length")
KIND_PRIOR = {"preference": 1.0, "habit": .82, "diary": .62, "public": .48, "culture": .42}


def default_path():
    explicit = os.environ.get("DSH_DAFEIYU_RERANK_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "embedding_rerank.json"


def load_fixture(path=None):
    path = Path(path) if path else Path(__file__).with_name("fixtures") / "embedding_eval.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid embedding fixture")
    records, queries = data.get("records"), data.get("queries")
    if not isinstance(records, list) or not isinstance(queries, list):
        raise ValueError("invalid embedding fixture")
    known = set()
    for row in records:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
                or row["id"] in known or row.get("kind") not in KIND_PRIOR
                or not isinstance(row.get("text"), str) or not row["text"].strip()):
            raise ValueError("invalid or duplicate fixture record")
        known.add(row["id"])
    for row in queries:
        if (not isinstance(row, dict) or not isinstance(row.get("text"), str) or not row["text"].strip()
                or type(row.get("no_answer", False)) is not bool):
            raise ValueError("invalid fixture query")
        if row.get("no_answer", False):
            if row.get("expected") is not None:
                raise ValueError("no-answer query has expected record")
        elif not isinstance(row.get("expected"), str) or row["expected"] not in known:
            raise ValueError("unknown fixture expected ID")
    return records, queries


def feature_row(query, record, cosine):
    text = str(record.get("text") or "")
    overlap = len(set(query) & set(text)) / max(1, min(len(query), len(text)))
    return [max(0.0, min(1.0, cosine)),
            max(0.0, min(1.0, hash_similarity(query, text))),
            max(0.0, min(1.0, concept_overlap(query, text))),
            max(0.0, min(1.0, overlap)),
            KIND_PRIOR.get(record.get("kind"), .4),
            max(0, min(100, int(record.get("importance") or 0))) / 100,
            max(0.0, min(1.0, len(text) / 120))]


def _sigmoid(value):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _numeric_row(values):
    return (isinstance(values, (list, tuple)) and len(values) == len(FEATURES)
            and all(_finite_number(value) for value in values))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _compatibility(model_dir=None):
    """Bind weights to the actual assets AND preprocessing implementation.

    Missing assets are represented explicitly; no downloads or model loads.
    """
    from embedding_backend import FILES
    backend = EmbeddingBackend(model_dir)
    assets = {}
    for name in FILES:
        path = backend.directory / name
        if not path.is_file():
            assets[name] = None
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        assets[name] = digest.hexdigest()
    import embedding_backend
    import semantic_memory
    return {"feature_revision": FEATURE_REVISION, "assets": assets,
            "preprocessing": hashlib.sha256(Path(embedding_backend.__file__).read_bytes()).hexdigest(),
            "semantic": hashlib.sha256(Path(semantic_memory.__file__).read_bytes()).hexdigest()}


class Reranker:
    def __init__(self, weights=None, bias=0.0, mean=None, std=None, metrics=None, concept_bonus=.5, enabled=False):
        self.weights = [0.0] * len(FEATURES) if weights is None else weights
        self.mean = [0.0] * len(FEATURES) if mean is None else mean
        self.std = [1.0] * len(FEATURES) if std is None else std
        self.bias, self.concept_bonus, self.enabled = bias, concept_bonus, enabled
        self.metrics = {} if metrics is None else metrics
        self.compatibility = None
        self.valid = (all(_numeric_row(vector) for vector in (self.weights, self.mean, self.std))
                      and _finite_number(bias) and _finite_number(concept_bonus)
                      and 0 <= concept_bonus <= 2 and type(enabled) is bool
                      and isinstance(self.metrics, dict)
                      and all(value > 1e-6 for value in self.std))
        if self.valid:
            self.weights, self.mean, self.std = map(list, (self.weights, self.mean, self.std))

    def score(self, features):
        if not self.valid or not _numeric_row(features):
            return 0.0
        try:
            normalized = [(value - self.mean[index]) / self.std[index]
                          for index, value in enumerate(features)]
            logit = self.bias + sum(a * b for a, b in zip(self.weights, normalized))
            if not all(_finite_number(value) for value in normalized) or not _finite_number(logit):
                return 0.0
            return _sigmoid(logit) + self.concept_bonus * max(0.0, min(1.0, features[2]))
        except (OverflowError, TypeError, ValueError):
            return 0.0

    def save(self, path, enabled=False, *, model_dir=None):
        if not self.valid or type(enabled) is not bool:
            raise ValueError("invalid reranker")
        compatibility = _compatibility(model_dir)
        if self.compatibility is not None and self.compatibility != compatibility:
            raise ValueError("reranker model compatibility mismatch")
        payload = {"version": VERSION, "features": list(FEATURES), "weights": self.weights,
                   "bias": self.bias, "mean": self.mean, "std": self.std,
                   "concept_bonus": self.concept_bonus, "compatibility": compatibility,
                   "metrics": self.metrics, "enabled": enabled}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(body) > MAX_RERANK_BYTES:
            raise ValueError("reranker document too large")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(body)
        temporary.replace(path)
        return path

    @classmethod
    def load(cls, path=None, *, model_dir=None):
        path = Path(path) if path is not None else default_path()
        try:
            with path.open("rb") as stream:
                body = stream.read(MAX_RERANK_BYTES + 1)
            if len(body) > MAX_RERANK_BYTES:
                return None
            raw = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
            required = {"version", "features", "weights", "bias", "mean", "std",
                        "metrics", "concept_bonus", "enabled", "compatibility"}
            if (not isinstance(raw, dict) or set(raw) != required
                    or type(raw["version"]) is not int or raw["version"] != VERSION
                    or raw["features"] != list(FEATURES)
                    or any(raw[key] is None for key in required)):
                return None
            candidate = cls(raw["weights"], raw["bias"], raw["mean"], raw["std"],
                            raw["metrics"], raw["concept_bonus"], raw["enabled"])
            if not candidate.valid or raw["compatibility"] != _compatibility(model_dir):
                return None
            candidate.compatibility = raw["compatibility"]
            return candidate
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return None


def train_reranker(train_rows, embeddings, epochs=600, lr=.08, l2=.002):
    """train_rows: [(query, positive_id, records, vectors)]."""
    examples = []
    for query, expected, records, vectors in train_rows:
        for index, record in enumerate(records):
            examples.append((feature_row(query, record, _dot(vectors["query"], vectors["records"][index])),
                             1.0 if record["id"] == expected else 0.0))
    if not examples:
        return None
    width = len(FEATURES)
    mean = [sum(row[i] for row, _ in examples) / len(examples) for i in range(width)]
    std = []
    for index in range(width):
        variance = sum((row[index] - mean[index]) ** 2 for row, _ in examples) / len(examples)
        std.append(max(1e-4, math.sqrt(variance)))
    normalized = [([(row[i] - mean[i]) / std[i] for i in range(width)], label) for row, label in examples]
    weights, bias = [0.0] * width, 0.0
    positives = sum(1 for _, label in normalized if label)
    negative_weight = max(1.0, positives / max(1, len(normalized) - positives))
    for _ in range(max(50, int(epochs))):
        for row, label in normalized:
            probability = _sigmoid(bias + sum(a * b for a, b in zip(weights, row)))
            weight = 4.0 if label else 1.0
            error = (probability - label) * weight
            bias -= lr * error
            for index in range(width):
                weights[index] -= lr * (error * row[index] + l2 * weights[index])
    return Reranker(weights, bias, mean, std)


def _dot(left, right):
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def _rank_metrics(records, vectors, queries, scorer):
    hits, rr, ndcg = [], [], []
    details = []
    for query, expected, vectors_for_query in queries:
        scores = []
        for index, record in enumerate(records):
            cosine = _dot(vectors_for_query, vectors[index])
            scores.append((scorer(query, record, cosine), index))
        scores.sort(reverse=True)
        rank = next((position + 1 for position, (_, index) in enumerate(scores)
                     if records[index]["id"] == expected), None)
        hits.append(1.0 if rank == 1 else 0.0)
        rr.append(1.0 / rank if rank is not None else 0.0)
        ndcg.append(1.0 / math.log2(rank + 1) if rank is not None and rank <= 5 else 0.0)
        details.append({"query": query, "expected": expected, "rank": rank})
    count = sum(expected is not None for _, expected, _ in queries)
    no_answer = len(queries) - count
    return {"hit1": round(sum(hits) / count, 4) if count else None,
            "mrr": round(sum(rr) / count, 4) if count else None,
            "ndcg5": round(sum(ndcg) / count, 4) if count else None,
            "answerable": count, "no_answer": no_answer,
            # Dense ranking has no abstention gate and always returns records.
            "no_answer_accuracy": (0.0 if records else 1.0) if no_answer else None,
            "queries": len(queries), "details": details}


def evaluate(fixture_path=None, model_dir=None):
    records, queries = load_fixture(fixture_path)
    backend = EmbeddingBackend(model_dir)
    if not backend.files_present():
        return {"ok": False, "error": "embedding_model_missing", "dir": str(backend.directory)}
    if not records or not queries:
        return {"ok": False, "error": "empty_fixture"}
    try:
        record_vectors = backend.encode([record["text"] for record in records])
        query_vectors = backend.encode([query["text"] for query in queries])
    except Exception:
        return {"ok": False, "error": "embedding_failed"}
    if (not _valid_vectors(record_vectors, len(records))
            or not _valid_vectors(query_vectors, len(queries), len(record_vectors[0]))):
        return {"ok": False, "error": "embedding_failed"}
    indexed = sorted(range(len(queries)), key=lambda index: hashlib.blake2b(
        queries[index]["text"].encode("utf-8"), digest_size=8).hexdigest())
    all_queries = [(queries[index]["text"], queries[index].get("expected"), query_vectors[index])
                   for index in indexed]
    baseline = _rank_metrics(records, record_vectors, all_queries,
                             lambda query, record, cosine: cosine)
    # Query-level 5-fold cross-validation: every query is tested once on a
    # reranker that never saw it, instead of a single lucky split.
    folds = 5
    details = []
    reranker = None
    for fold in range(folds):
        test_indexes = indexed[fold::folds]
        train_indexes = [index for index in indexed if index not in test_indexes]
        train_rows = [(queries[index]["text"], queries[index].get("expected"), records,
                       {"query": query_vectors[index], "records": record_vectors}) for index in train_indexes
                      if not queries[index].get("no_answer", False)]
        if len(train_rows) < 10:
            continue
        fold_reranker = train_reranker(train_rows, None)
        if fold_reranker is None:
            continue
        reranker = fold_reranker
        for index in test_indexes:
            query = queries[index]["text"]
            scores = []
            for record_index, record in enumerate(records):
                cosine = _dot(query_vectors[index], record_vectors[record_index])
                scores.append((fold_reranker.score(feature_row(query, record, cosine)), record_index))
            scores.sort(reverse=True)
            rank = next((position + 1 for position, (_, record_index) in enumerate(scores)
                         if records[record_index]["id"] == queries[index].get("expected")), None)
            details.append({"query": query, "expected": queries[index].get("expected"), "rank": rank})
    if reranker is None:
        return {"ok": False, "error": "too_few_training_queries"}
    count = max(1, sum(row["expected"] is not None for row in details))
    reranked = {"hit1": round(sum(1 for row in details if row["rank"] == 1) / count, 4),
                "mrr": round(sum(1.0 / row["rank"] for row in details if row["rank"] is not None) / count, 4),
                "ndcg5": round(sum(1.0 / math.log2(row["rank"] + 1) for row in details
                                  if row["rank"] is not None and row["rank"] <= 5) / count, 4),
                "answerable": sum(row["expected"] is not None for row in details),
                "no_answer": sum(row["expected"] is None for row in details),
                "no_answer_accuracy": 0.0 if any(row["expected"] is None for row in details) else None,
                "queries": len(details), "details": details}
    improved = reranked["mrr"] >= baseline["mrr"] and reranked["hit1"] >= baseline["hit1"] \
        and (reranked["mrr"] > baseline["mrr"] or reranked["hit1"] > baseline["hit1"])
    reranker = train_reranker([(queries[index]["text"], queries[index].get("expected"), records,
                                {"query": query_vectors[index], "records": record_vectors}) for index in indexed
                               if not queries[index].get("no_answer", False)], None)
    reranker.compatibility = _compatibility(model_dir)
    reranker.metrics = {"baseline": baseline, "rerank": reranked, "folds": folds,
                        "test_queries": len(details), "improved": improved}
    return {"ok": True, "baseline": baseline, "rerank": reranked, "improved": improved,
            "reranker": reranker, "records": len(records), "queries": len(queries)}


def _valid_vectors(vectors, count, width=None):
    if not isinstance(vectors, (list, tuple)) or len(vectors) != count:
        return False
    if not vectors:
        return count == 0
    if width is None:
        width = len(vectors[0]) if isinstance(vectors[0], (list, tuple)) else 0
    return (width > 0 and all(isinstance(row, (list, tuple)) and len(row) == width
                             and all(_finite_number(value) for value in row) for row in vectors))


def evaluate_production(fixture_path=None, model_dir=None, *, reranker=None, k=20):
    """Evaluate real candidate gating/top-k, with an isolated OFF baseline.

    model_dir selects local embedding assets. reranker=None (default) NEVER
    reads the owner's reranker; pass a valid Reranker explicitly to compare it.
    No model download, training, owner/profile write, or environment mutation.
    Missing assets are status=unavailable; invalid fixtures/models/outputs are
    status=error without metrics. MRR is truncated at k (the engine caps at20).
    """
    import tempfile
    from memory_engine import MemoryEngine, _identity, _plain
    base = {"ok": False, "training_performed": False, "reranker": "off" if reranker is None else "override"}
    if type(k) is not int or not 1 <= k <= 20:
        return {**base, "status": "error", "error": "invalid_options"}
    if reranker is not None and (not isinstance(reranker, Reranker) or not reranker.valid):
        return {**base, "status": "error", "error": "invalid_reranker"}
    try:
        records, queries = load_fixture(fixture_path)
        if not records or not queries:
            raise ValueError("empty fixture")
        normalized = []
        identities = {}
        for row in records:
            text = _plain(row["text"])
            identity = _identity(row["kind"], text)
            if not text or identity in identities.values():
                raise ValueError("ambiguous fixture record")
            importance = row.get("importance", 50)
            if not _finite_number(importance) or not 0 <= importance <= 100:
                raise ValueError("invalid importance")
            identities[row["id"]] = identity
            normalized.append({"id": identity, "kind": row["kind"], "text": text,
                               "source": "fixture", "importance": int(importance),
                               "createdAt": 1, "lastSeen": 1, "hits": 1})
    except (OSError, ValueError, TypeError, UnicodeError):
        return {**base, "status": "error", "error": "invalid_fixture"}
    try:
        backend = EmbeddingBackend(model_dir)
        if not backend.files_present():
            return {**base, "status": "unavailable", "error": "embedding_model_missing",
                    "dir": str(backend.directory)}
        if (reranker is not None and reranker.compatibility is not None
                and reranker.compatibility != _compatibility(model_dir)):
            return {**base, "status": "error", "error": "incompatible_reranker"}
        # Validate encoding independently: engine fallback must not disguise a
        # broken neural benchmark as a successful lexical-only result.
        vectors = backend.encode([row["text"] for row in normalized])
        if not _valid_vectors(vectors, len(normalized)):
            return {**base, "status": "error", "error": "embedding_failed"}
        with tempfile.TemporaryDirectory() as directory:
            engine = MemoryEngine(Path(directory) / "memory.json")
            engine.data = {"version": 1, "revision": 1, "publicRevision": 0, "records": normalized}
            engine._embedding = backend
            def checked_encode(texts):
                encoded = backend.encode(texts)
                if not _valid_vectors(encoded, len(texts), len(vectors[0])):
                    raise ValueError("embedding failed")
                return encoded
            engine._encode = checked_encode
            engine._vectors = {row["id"]: vector for row, vector in zip(normalized, vectors)}
            # Do NOT set _vectors_ready: preserve the production cold-store
            # shortlist for >80 records, rather than silently evaluating warm.
            engine._reranker, engine._reranker_checked = reranker, True
            ranks, details = [], []
            answerable = no_answer = no_answer_correct = 0
            for query in queries:
                hits = engine.recall(query["text"], limit=k, kinds=tuple(KIND_PRIOR))
                if not isinstance(hits, (list, tuple)) or len(hits) > k:
                    raise ValueError("malformed recall result")
                ids = [hit.get("id") if isinstance(hit, dict) else None for hit in hits]
                known = set(identities.values())
                if any(not isinstance(identity, str) or identity not in known for identity in ids) or len(set(ids)) != len(ids):
                    raise ValueError("invalid recall IDs")
                if getattr(backend, "status", "ready") in ("error", "unavailable"):
                    raise ValueError("embedding failed")
                expected = identities.get(query.get("expected"))
                rank = ids.index(expected) + 1 if expected in ids else None
                ranks.append(rank)
                if query.get("no_answer", False):
                    no_answer += 1
                    no_answer_correct += not ids
                else:
                    answerable += 1
                details.append({"expected": query.get("expected"), "expected_id": expected, "rank": rank,
                                "no_answer": query.get("no_answer", False), "returned_ids": ids})
    except Exception:
        return {**base, "status": "error", "error": "evaluation_failed"}
    answer_ranks = [row["rank"] for row in details if not row["no_answer"]]
    top1 = sum(rank == 1 for rank in answer_ranks)
    return {**base, "ok": True, "status": "evaluated", "k": k,
            "hit1": round(top1 / answerable, 4) if answerable else None,
            "mrr": round(sum(1.0 / rank for rank in answer_ranks if rank is not None) / answerable, 4) if answerable else None,
            "ndcg5": round(sum(1.0 / math.log2(rank + 1) for rank in answer_ranks
                               if rank is not None and rank <= 5) / answerable, 4) if answerable else None,
            "answerable": answerable, "no_answer": no_answer,
            "no_answer_accuracy": round(no_answer_correct / no_answer, 4) if no_answer else None,
            "end_to_end_accuracy": round((top1 + no_answer_correct) / len(queries), 4),
            "queries": len(queries), "ranks": ranks, "details": details}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--enable", action="store_true", help="enable rerank in memory recall")
    parser.add_argument("--production", action="store_true", help="run the fixture through MemoryEngine (reranker OFF by default)")
    parser.add_argument("--fixture")
    parser.add_argument("--model-dir")
    parser.add_argument("--reranker-file", help="explicit production reranker override; never inherited")
    parser.add_argument("--k", type=int, default=20, help="production recall depth, 1..20")
    args = parser.parse_args()
    if args.production:
        reranker = None
        if args.reranker_file:
            reranker = Reranker.load(args.reranker_file, model_dir=args.model_dir)
            if reranker is None:
                print(json.dumps({"ok": False, "status": "error", "error": "invalid_reranker"}))
                return 1
        report = evaluate_production(args.fixture, args.model_dir, reranker=reranker, k=args.k)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    result = evaluate(args.fixture, args.model_dir)
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False))
        return 1
    reranker = result.pop("reranker")
    print(json.dumps({key: result[key] for key in ("improved", "baseline", "rerank", "records", "queries")},
                     ensure_ascii=False, indent=2))
    if args.train:
        reranker.save(default_path(), enabled=args.enable and result["improved"], model_dir=args.model_dir)
        print(f"saved {default_path()} enabled={bool(args.enable and result['improved'])}")
    return 0 if result["improved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
