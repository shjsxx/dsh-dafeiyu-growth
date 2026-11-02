"""Pure emotion evaluation; transient text never appears in audit output.

Six train labels are supported. ``neutral`` means abstain, never ``calm``.
Legacy ``macro_f1`` is the full-taxonomy average, including unsupported classes.
"""
from __future__ import annotations

import hashlib

EMOTIONS = ("joy", "sad", "anger", "anxious", "tired", "calm")
ABSTAIN = "neutral"
LABEL_CONTRACT = {"train_labels": list(EMOTIONS), "abstain_label": ABSTAIN,
                  "neutral_is_calm": False}


def _hash(text):
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def normalize(text):
    return " ".join(str(text or "").split())


def coverage(labels):
    supported = [label for label in EMOTIONS if label in labels]
    return {"supported_labels": supported,
            "missing_labels": [label for label in EMOTIONS if label not in supported],
            "class_coverage": round(len(supported) / len(EMOTIONS), 4)}


def prepare(rows, preserve_groups=False):
    """Return deduped pairs (legacy), or (text,label,opaque-group) triples.

    Dict rows accept group_id or dialogue_id and optional source_id. Group IDs
    are namespaced by source, hashed, and never exposed in audit. Duplicate
    texts join groups transitively, so deduplication cannot hide group leakage.
    Untagged rows each form a text group. No dialogue structure is inferred.
    """
    clean, seen, conflicts, parents = [], {}, set(), {}
    audit = {"rows_total": 0, "valid": 0, "skipped": 0, "duplicates": 0,
             "conflicts": 0, "abstentions": 0, "unmapped": 0,
             "group_tagged": 0, "labels": {}}

    def root(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for row in rows if isinstance(rows, (list, tuple)) else []:
        audit["rows_total"] += 1
        group = source = None
        if isinstance(row, dict):
            text, label = row.get("text"), row.get("label")
            group = row.get("group_id") or row.get("dialogue_id")
            source = row.get("source_id", "")
        elif isinstance(row, (list, tuple)) and len(row) in (2, 3):
            text, label = row[:2]
            group = row[2] if len(row) == 3 else None
        else:
            text = label = None
        if label == ABSTAIN:
            audit["abstentions"] += 1
        elif not isinstance(label, str) or label not in EMOTIONS:
            audit["unmapped"] += 1
        if not isinstance(text, str) or not isinstance(label, str) or label not in EMOTIONS:
            audit["skipped"] += 1
            continue
        compact = normalize(text)
        if not 2 <= len(compact) <= 160 or sum("\u4e00" <= ch <= "\u9fff" for ch in compact) < max(2, len(compact) // 4):
            audit["skipped"] += 1
            continue
        digest = _hash(compact)
        tagged = isinstance(group, (str, int)) and not isinstance(group, bool) and str(group).strip()
        key = ("g:" + _hash(repr((str(source), str(group))))) if tagged else "t:" + digest
        if tagged:
            audit["group_tagged"] += 1
        root(key)
        if digest in seen:
            old_label, old_group = seen[digest]
            left, right = sorted((root(key), root(old_group)))
            parents[right] = left
            if old_label != label:
                audit["conflicts"] += 1
                conflicts.add(digest)
            else:
                audit["duplicates"] += 1
            audit["skipped"] += 1
            continue
        seen[digest] = (label, key)
        clean.append((compact, label, key))
    clean = [(text, label, root(group)) for text, label, group in clean if _hash(text) not in conflicts]
    for _, label, _ in clean:
        audit["labels"][label] = audit["labels"].get(label, 0) + 1
    audit["valid"] = len(clean)
    audit["min_class"] = min(audit["labels"].values()) if audit["labels"] else 0
    audit["classes"] = len(audit["labels"])
    audit.update(coverage(audit["labels"]))
    return (clean if preserve_groups else [(text, label) for text, label, _ in clean]), audit


def stratified_split(clean, holdout=.2):
    """Legacy deterministic row split; not a dialogue-disjoint guarantee."""
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


def grouped_split(rows, holdout=.2):
    """Deterministic whole-group holdout; accepts dict rows or tagged triples.

    Re-prepares rows to join duplicate-linked groups. Returns legacy pairs.
    Approximate stratification only: groups may span classes; a class confined
    to one group stays in train. Empty validation is possible and must be
    rejected by the caller, never silently replaced by a leaking row split.
    """
    clean, _ = prepare(rows, preserve_groups=True)
    ratio = max(.1, min(.4, float(holdout)))
    groups, remaining, targets, selected = {}, {}, {}, {}
    for text, label, group in clean:
        groups.setdefault(group, []).append((text, label))
        remaining[label] = remaining.get(label, 0) + 1
    targets = {label: max(1, round(count * ratio)) for label, count in remaining.items()}
    train, valid = [], []
    for group in sorted(groups, key=_hash):
        chunk = groups[group]
        counts = {}
        for _, label in chunk:
            counts[label] = counts.get(label, 0) + 1
        take = (all(remaining[label] > count for label, count in counts.items())
                and any(selected.get(label, 0) < targets[label] for label in counts))
        (valid if take else train).extend(chunk)
        if take:
            for label, count in counts.items():
                remaining[label] -= count
                selected[label] = selected.get(label, 0) + count
    return (sorted(train, key=lambda row: _hash(row[0])),
            sorted(valid, key=lambda row: _hash(row[0])))


def metrics(truth, predicted, labels=None):
    """Report full-taxonomy and supported-class means separately.

    Default taxonomy is always the six train labels (never inferred from
    predictions). Out-of-taxonomy predictions, including neutral, abstain.
    """
    labels = [label for label in (labels if labels is not None else EMOTIONS) if label in EMOTIONS]
    labels = list(dict.fromkeys(labels))
    if len(truth) != len(predicted):
        raise ValueError("emotion_metric_length_mismatch")
    confusion = {label: {other: 0 for other in labels} for label in labels}
    abstentions = 0
    for actual, guess in zip(truth, predicted):
        if guess not in labels:
            abstentions += 1
        elif actual in confusion:
            confusion[actual][guess] += 1
    support = len(truth)
    per_class, recalls, f1s, supported_f1s = [], [], [], []
    for label in labels:
        tp = confusion[label][label]
        predicted_count = predicted.count(label)
        actual_count = truth.count(label)
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / actual_count if actual_count else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if actual_count:
            recalls.append(recall)
            supported_f1s.append(f1)
        f1s.append(f1)
        per_class.append({"label": label, "precision": round(precision, 4),
                          "recall": round(recall, 4), "f1": round(f1, 4), "support": actual_count})
    full = round(sum(f1s) / len(f1s), 4) if f1s else 0.0
    return {
        "accuracy": round(sum(confusion[label][label] for label in labels) / support, 4) if support else 0.0,
        "balanced_accuracy": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "macro_f1": full, "macro_f1_full_taxonomy": full,
        "macro_f1_supported": round(sum(supported_f1s) / len(supported_f1s), 4) if supported_f1s else 0.0,
        "support": support, "abstentions": abstentions, "labels": labels, "per_class": per_class,
        "confusion": [[confusion[actual][guess] for guess in labels] for actual in labels],
        **coverage(truth),
    }


def evaluate(classify, rows, labels=None):
    return metrics([label for _, label in rows],
                   [classify(text).get("label", ABSTAIN) for text, _ in rows], labels)


def acceptable(before, after):
    """Improve macro/accuracy without a material accuracy regression."""
    if not before or not after:
        return False
    acc_ok = after["accuracy"] >= before["accuracy"] - .02
    improved = (after["accuracy"] > before["accuracy"] + 1e-9
                or after["macro_f1"] > before["macro_f1"] + .01
                or after["balanced_accuracy"] > before["balanced_accuracy"] + .01)
    return bool(acc_ok and improved)
