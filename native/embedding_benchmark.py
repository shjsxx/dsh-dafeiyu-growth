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

VERSION = 1
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
    records, queries = data.get("records"), data.get("queries")
    if not isinstance(records, list) or not isinstance(queries, list):
        raise ValueError("invalid embedding fixture")
    known = {row.get("id") for row in records}
    return ([row for row in records if isinstance(row, dict) and row.get("id") in known],
            [row for row in queries if isinstance(row, dict) and row.get("expected") in known])


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


class Reranker:
    def __init__(self, weights=None, bias=0.0, mean=None, std=None, metrics=None, concept_bonus=.5, enabled=False):
        self.enabled = bool(enabled)
        self.weights = list(weights or [0.0] * len(FEATURES))
        self.bias = float(bias or 0.0)
        self.mean = list(mean or [0.0] * len(FEATURES))
        self.std = list(std or [1.0] * len(FEATURES))
        self.metrics = dict(metrics or {})
        self.concept_bonus = max(0.0, min(2.0, float(concept_bonus or 0.0)))
        self.valid = all(
            isinstance(vector, list) and len(vector) == len(FEATURES)
            and all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector)
            for vector in (self.weights, self.mean, self.std)
        ) and math.isfinite(self.bias) and all(value > 1e-6 for value in self.std)

    def score(self, features):
        if not self.valid or len(features) < len(FEATURES):
            return 0.0
        normalized = [(value - self.mean[index]) / self.std[index]
                      for index, value in enumerate(features[:len(FEATURES)])]
        base = _sigmoid(self.bias + sum(a * b for a, b in zip(self.weights, normalized)))
        return base + self.concept_bonus * max(0.0, min(1.0, features[2]))

    def save(self, path, enabled=False):
        if not self.valid:
            raise ValueError("invalid reranker")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": VERSION, "features": list(FEATURES), "weights": self.weights,
                   "bias": self.bias, "mean": self.mean, "std": self.std,
                   "concept_bonus": self.concept_bonus,
                   "metrics": self.metrics, "enabled": bool(enabled)}
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
        return path

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path else default_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(raw, dict) or raw.get("version") != VERSION
                    or raw.get("features") != list(FEATURES)):
                return None
            candidate = cls(raw.get("weights"), raw.get("bias"), raw.get("mean"), raw.get("std"),
                            raw.get("metrics"), raw.get("concept_bonus", .5), raw.get("enabled", False))
            return candidate if candidate.valid else None
        except Exception:
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
                     if records[index]["id"] == expected), len(scores) + 1)
        hits.append(1.0 if rank == 1 else 0.0)
        rr.append(1.0 / rank)
        ndcg.append(1.0 / math.log2(rank + 1) if rank <= 5 else 0.0)
        details.append({"query": query, "expected": expected, "rank": rank})
    count = max(1, len(queries))
    return {"hit1": round(sum(hits) / count, 4), "mrr": round(sum(rr) / count, 4),
            "ndcg5": round(sum(ndcg) / count, 4), "queries": len(queries), "details": details}


def evaluate(fixture_path=None, model_dir=None):
    records, queries = load_fixture(fixture_path)
    backend = EmbeddingBackend(model_dir)
    if not backend.files_present():
        return {"ok": False, "error": "embedding_model_missing", "dir": str(backend.directory)}
    record_vectors = backend.encode([record["text"] for record in records])
    query_vectors = backend.encode([query["text"] for query in queries])
    if len(record_vectors) != len(records) or len(query_vectors) != len(queries):
        return {"ok": False, "error": "embedding_failed"}
    indexed = sorted(range(len(queries)), key=lambda index: hashlib.blake2b(
        queries[index]["text"].encode("utf-8"), digest_size=8).hexdigest())
    all_queries = [(queries[index]["text"], queries[index]["expected"], query_vectors[index])
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
        train_rows = [(queries[index]["text"], queries[index]["expected"], records,
                       {"query": query_vectors[index], "records": record_vectors}) for index in train_indexes]
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
                         if records[record_index]["id"] == queries[index]["expected"]), len(scores) + 1)
            details.append({"query": query, "expected": queries[index]["expected"], "rank": rank})
    count = max(1, len(details))
    reranked = {"hit1": round(sum(1 for row in details if row["rank"] == 1) / count, 4),
                "mrr": round(sum(1.0 / row["rank"] for row in details) / count, 4),
                "ndcg5": round(sum(1.0 / math.log2(row["rank"] + 1) if row["rank"] <= 5 else 0.0
                                  for row in details) / count, 4),
                "queries": len(details), "details": details}
    improved = reranked["mrr"] >= baseline["mrr"] and reranked["hit1"] >= baseline["hit1"] \
        and (reranked["mrr"] > baseline["mrr"] or reranked["hit1"] > baseline["hit1"])
    if reranker is None:
        return {"ok": False, "error": "too_few_training_queries"}
    reranker = train_reranker([(queries[index]["text"], queries[index]["expected"], records,
                                {"query": query_vectors[index], "records": record_vectors}) for index in indexed], None)
    reranker.metrics = {"baseline": baseline, "rerank": reranked, "folds": folds,
                        "test_queries": len(details), "improved": improved}
    return {"ok": True, "baseline": baseline, "rerank": reranked, "improved": improved,
            "reranker": reranker, "records": len(records), "queries": len(queries)}


def evaluate_production(fixture_path=None, model_dir=None):
    """Run the same fixture through the real MemoryEngine candidate gate."""
    import tempfile
    from memory_engine import MemoryEngine
    records, queries = load_fixture(fixture_path)
    with tempfile.TemporaryDirectory() as directory:
        engine = MemoryEngine(Path(directory) / "memory.json")
        engine.data = {"version": 1, "revision": 1, "publicRevision": 0, "records": [
            {"id": row["id"], "kind": row["kind"], "text": row["text"],
             "source": "fixture", "importance": int(row.get("importance") or 50),
             "createdAt": 1, "lastSeen": 1, "hits": 1} for row in records]}
        ranks = []
        for query in queries:
            hits = engine.recall(query["text"], limit=len(records),
                                 kinds=("preference", "habit", "diary", "public", "culture"))
            ids = [next((row["id"] for row in engine.data["records"] if row["text"] == hit["text"]), "")
                   for hit in hits]
            rank = ids.index(query["expected"]) + 1 if query["expected"] in ids else len(records) + 1
            ranks.append(rank)
    count = max(1, len(ranks))
    return {"hit1": round(sum(1 for rank in ranks if rank == 1) / count, 4),
            "mrr": round(sum(1.0 / rank for rank in ranks) / count, 4),
            "ndcg5": round(sum(1.0 / math.log2(rank + 1) if rank <= 5 else 0.0 for rank in ranks) / count, 4),
            "queries": len(ranks), "ranks": ranks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--enable", action="store_true", help="enable rerank in memory recall")
    parser.add_argument("--production", action="store_true", help="run the fixture through MemoryEngine")
    args = parser.parse_args()
    if args.production:
        print(json.dumps(evaluate_production(), ensure_ascii=False, indent=2))
        return 0
    result = evaluate()
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False))
        return 1
    reranker = result.pop("reranker")
    print(json.dumps({key: result[key] for key in ("improved", "baseline", "rerank", "records", "queries")},
                     ensure_ascii=False, indent=2))
    if args.train:
        reranker.save(default_path(), enabled=args.enable and result["improved"])
        print(f"saved {default_path()} enabled={bool(args.enable and result['improved'])}")
    return 0 if result["improved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
