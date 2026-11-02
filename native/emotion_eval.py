"""Pure evaluation and data-quality helpers for the emotion model.

Inputs are transient (text, label) pairs. Outputs contain only counts, fixed
labels and numeric metrics; no raw text is returned or retained.
"""
from __future__ import annotations

import hashlib

EMOTIONS = ("joy", "sad", "anger", "anxious", "tired", "calm")


def _hash(text):
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def normalize(text):
    return " ".join(str(text or "").split())


def prepare(rows):
    """Filter/dedupe/conflict-check rows and return (clean, audit)."""
    clean, seen, conflicts = [], {}, set()
    audit = {"rows_total": 0, "valid": 0, "skipped": 0, "duplicates": 0,
             "conflicts": 0, "labels": {}}
    for row in rows if isinstance(rows, (list, tuple)) else []:
        audit["rows_total"] += 1
        if isinstance(row, dict):
            text, label = row.get("text"), row.get("label")
        elif isinstance(row, (list, tuple)) and len(row) == 2:
            text, label = row
        else:
            text = label = None
        if not isinstance(text, str) or not isinstance(label, str) or label not in EMOTIONS:
            audit["skipped"] += 1
            continue
        compact = normalize(text)
        if not 2 <= len(compact) <= 160 or sum("\u4e00" <= ch <= "\u9fff" for ch in compact) < max(2, len(compact) // 4):
            audit["skipped"] += 1
            continue
        digest = _hash(compact)
        if digest in seen:
            if seen[digest] != label:
                audit["conflicts"] += 1
                conflicts.add(digest)
            else:
                audit["duplicates"] += 1
            audit["skipped"] += 1
            continue
        seen[digest] = label
        clean.append((compact, label))
    if conflicts:
        clean = [row for row in clean if _hash(row[0]) not in conflicts]
    for _, label in clean:
        audit["labels"][label] = audit["labels"].get(label, 0) + 1
    audit["valid"] = len(clean)
    audit["min_class"] = min(audit["labels"].values()) if audit["labels"] else 0
    audit["classes"] = len(audit["labels"])
    return clean, audit


def stratified_split(clean, holdout=.2):
    """Deterministic per-class split; each class keeps at least one train row."""
    ratio = max(.1, min(.4, float(holdout)))
    groups = {}
    for text, label in clean:
        groups.setdefault(label, []).append((text, label))
    train, valid = [], []
    for label in sorted(groups):
        rows = sorted(groups[label], key=lambda row: _hash(row[0]))
        if len(rows) < 2:
            train.extend(rows)
            continue
        take = max(1, min(len(rows) - 1, int(round(len(rows) * ratio))))
        valid.extend(rows[:take])
        train.extend(rows[take:])
    train.sort(key=lambda row: _hash(row[0]))
    valid.sort(key=lambda row: _hash(row[0]))
    return train, valid


def metrics(truth, predicted, labels=None):
    """Accuracy, balanced accuracy, macro F1, per-class P/R/F1 and confusion."""
    labels = list(labels or sorted(set(truth) | set(predicted)))
    if not labels:
        return {"accuracy": 0.0, "balanced_accuracy": 0.0, "macro_f1": 0.0,
                "support": 0, "labels": [], "per_class": [], "confusion": []}
    confusion = {label: {other: 0 for other in labels} for label in labels}
    for actual, guess in zip(truth, predicted):
        if actual in confusion and guess in confusion[actual]:
            confusion[actual][guess] += 1
    support = len(truth)
    per_class = []
    recalls = []
    f1s = []
    for label in labels:
        tp = confusion[label][label]
        predicted_count = sum(confusion[actual][label] for actual in labels)
        actual_count = sum(confusion[label].values())
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / actual_count if actual_count else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
        if actual_count:
            recalls.append(recall)
        f1s.append(f1)
        per_class.append({"label": label, "precision": round(precision, 4),
                          "recall": round(recall, 4), "f1": round(f1, 4),
                          "support": actual_count})
    correct = sum(confusion[label][label] for label in labels)
    return {
        "accuracy": round(correct / support, 4) if support else 0.0,
        "balanced_accuracy": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        "support": support, "labels": labels, "per_class": per_class,
        "confusion": [[confusion[actual][guess] for guess in labels] for actual in labels],
    }


def evaluate(classify, rows, labels=None):
    truth = [label for _, label in rows]
    predicted = [classify(text).get("label", "neutral") for text, _ in rows]
    return metrics(truth, predicted, labels)


def acceptable(before, after):
    """Improve macro/accuracy without a material accuracy regression."""
    if not before or not after:
        return False
    acc_ok = after["accuracy"] >= before["accuracy"] - .02
    improved = (after["accuracy"] > before["accuracy"] + 1e-9
                or after["macro_f1"] > before["macro_f1"] + .01
                or after["balanced_accuracy"] > before["balanced_accuracy"] + .01)
    return bool(acc_ok and improved)
