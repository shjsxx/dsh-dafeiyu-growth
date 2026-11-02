"""One-shot, opt-in update from an openly licensed Chinese emotion dataset.

The dataset text is public and licensed; it is used only to derive hashed
features and is never written to disk, printed, or added to the repository.
Only bounded numeric audit fields are journaled.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import time
import urllib.request
from pathlib import Path

from emotion_model import EmotionModel
from emotion_eval import EMOTIONS, LABEL_CONTRACT, coverage
from training_journal import TrainingJournal

DATASET = {
    "id": "zzhdbw/Simplified_Chinese_Multi-Emotion_Dialogue_Dataset",
    "revision": "d0d148b971f09c8269bc7afa045e37802a73eaa9",
    "license": "Apache-2.0",
    "url": "https://huggingface.co/datasets/zzhdbw/Simplified_Chinese_Multi-Emotion_Dialogue_Dataset/resolve/d0d148b971f09c8269bc7afa045e37802a73eaa9/Simplified_Chinese_Multi-Emotion_Dialogue_Dataset.csv",
    "upstream": "Johnson8187/Chinese_Multi-Emotion_Dialogue_Dataset (MIT)",
}
LABEL_MAP = {"开心": "joy", "伤心": "sad", "生气": "anger", "平静": "calm", "平淡": "neutral"}
UNSAFE = ("色情", "约炮", "裸聊", "赌博", "博彩", "自杀", "自残", "杀人", "毒品", "贷款", "加微信", "代开发票")
MAX_BYTES = 4 * 1024 * 1024


def download(url=DATASET["url"], timeout=30):
    """Pinned dataset, with an explicit mirror fallback for blocked networks."""
    candidates = [url]
    if url == DATASET["url"]:
        candidates.append(url.replace("https://huggingface.co", "https://hf-mirror.com"))
    last_error = None
    for candidate in candidates:
        try:
            request = urllib.request.Request(candidate, headers={"User-Agent": "Yunlan/0.22 public-dataset update"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError("dataset_http")
                body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise RuntimeError("dataset_too_large")
            return body
        except (OSError, RuntimeError) as error:
            last_error = error
    raise RuntimeError("dataset_unreachable") from last_error


def parse_csv(body):
    """Bounded CSV; accepts mapped Chinese or canonical train labels.

    neutral/平淡 are counted as abstentions, not emitted as training rows.
    Optional source_id + group_id/dialogue_id produce dict rows; otherwise
    legacy pairs are preserved. IDs are transient and never journaled.
    """
    if len(body) > MAX_BYTES:
        raise RuntimeError("dataset_too_large")
    text = body.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "text" not in reader.fieldnames:
        raise RuntimeError("dataset_schema")
    label_field = "emotion" if "emotion" in reader.fieldnames else "label" if "label" in reader.fieldnames else ""
    if not label_field:
        raise RuntimeError("dataset_schema")
    rows, stats = [], {"rows_total": 0, "filtered": 0, "unmapped": 0, "unsafe": 0,
                       "abstentions": 0, "group_tagged": 0}
    counts = {}
    for record in reader:
        stats["rows_total"] += 1
        if stats["rows_total"] > 20000:
            raise RuntimeError("dataset_too_many_rows")
        content = " ".join(str(record.get("text") or "").split())
        original = str(record.get(label_field) or "").strip()
        label = original if original in (*EMOTIONS, "neutral") else LABEL_MAP.get(original)
        if label == "neutral":
            stats["abstentions"] += 1
            continue
        if not label:
            stats["unmapped"] += 1
            continue
        unsafe = any(term in content for term in UNSAFE)
        if (not 4 <= len(content) <= 120 or "http" in content.lower() or "@" in content
                or unsafe
                or sum("\u4e00" <= ch <= "\u9fff" for ch in content) < max(3, len(content) // 3)):
            stats["filtered"] += 1
            stats["unsafe"] += int(unsafe)
            continue
        group = str(record.get("group_id") or record.get("dialogue_id") or "").strip()
        source = str(record.get("source_id") or "").strip()
        if len(group) > 120 or len(source) > 120:
            stats["filtered"] += 1
            continue
        if group or source:
            rows.append({"text": content, "label": label, "group_id": group, "source_id": source})
            stats["group_tagged"] += int(bool(group))
        else:
            rows.append((content, label))
        counts[label] = counts.get(label, 0) + 1
    stats.update(coverage(counts))
    stats["label_counts"] = counts
    return rows, stats


def load_local(path):
    """Read at most the same byte budget as download; never crawl or copy text."""
    with Path(path).open("rb") as stream:
        return parse_csv(stream.read(MAX_BYTES + 1))


def backup_if_exists(path):
    path = Path(path)
    if not path.is_file():
        return None
    target = path.with_name(path.name + f".{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(path, target)
    return target


def main():
    import argparse
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", action="store_true",
                        help="train into a staging copy; print its path, do not touch the live model")
    parser.add_argument("--labeled-file", type=Path,
                        help="local UTF-8 CSV; text,label[,source_id,group_id/dialogue_id]; no download")
    parser.add_argument("--model-file", type=Path, help="explicit destination profile (required for local input unless staging)")
    args = parser.parse_args()
    if args.labeled_file and not (args.model_file or args.staging):
        parser.error("--labeled-file requires --model-file or --staging; live owner data is not the default")
    source = "local_labeled_file" if args.labeled_file else "huggingface.co/" + DATASET["id"]
    if args.staging or args.labeled_file:
        rows, stats = load_local(args.labeled_file) if args.labeled_file else parse_csv(download())
        live = args.model_file or EmotionModel().path
        if args.staging:
            stage_dir = Path(tempfile.mkdtemp(prefix="yunlan-emotion-"))
            stage = stage_dir / "emotion.json"
            if live.is_file():
                shutil.copy2(live, stage)
        else:
            stage = args.model_file
        model = EmotionModel(stage)
        audit = model.learn_dataset(rows, source=source)
        print(json.dumps({**audit, "ok": bool(audit["persisted"]), "model_file": str(stage),
                          "live": str(live), "filtered": stats["filtered"], "unmapped": stats["unmapped"],
                          "staging": str(stage) if args.staging else None,
                          "audit": audit, "parser": stats, "label_contract": LABEL_CONTRACT}, ensure_ascii=False))
        return 0 if audit["persisted"] else 2
    model = EmotionModel(args.model_file)
    if model.status == "protected":
        print(json.dumps({"ok": False, "reason": "protected"}, ensure_ascii=False))
        return 1
    rows, stats = parse_csv(download())
    backup = backup_if_exists(model.path)
    audit = model.learn_dataset(rows, source=source)
    journal = TrainingJournal()
    metrics = {
        "dataset_rows": int(audit["rows_total"]), "dataset_skipped": int(audit["skipped"]),
        "dataset_validation": int(audit["validation"]),
        "dataset_accuracy_before": float(audit["accuracy_before"]),
        "dataset_accuracy_after": float(audit["accuracy_after"]),
        "dataset_balanced_before": float(audit["balanced_before"]),
        "dataset_balanced_after": float(audit["balanced_after"]),
        "dataset_macro_f1_before": float(audit["macro_f1_before"]),
        "dataset_macro_f1_after": float(audit["macro_f1_after"]),
        "dataset_duplicates": int(audit["duplicates"]),
        "dataset_conflicts": int(audit["conflicts"]),
        "dataset_classes": int(audit["classes"]),
        "dataset_min_class": int(audit["min_class"]),
        "changed_weights": int(audit["changed_weights"]),
        "adaptation_passes": 1 if audit["persisted"] else 0,
    }
    status = "success" if audit["persisted"] else "skipped"
    journal.record("emotion_fit", "emotion", "public_dataset", status, metrics,
                   error="" if audit["persisted"] else "no_new_data")
    result = {
        "ok": bool(audit["persisted"]),
        "dataset": DATASET["id"], "revision": DATASET["revision"], "license": DATASET["license"],
        "rows_used": int(audit["rows_used"]), "validation": int(audit["validation"]),
        "accuracy_before": audit["accuracy_before"], "accuracy_after": audit["accuracy_after"],
        "balanced_before": audit["balanced_before"], "balanced_after": audit["balanced_after"],
        "macro_f1_before": audit["macro_f1_before"], "macro_f1_after": audit["macro_f1_after"],
        "duplicates": audit["duplicates"], "conflicts": audit["conflicts"],
        "changed_weights": int(audit["changed_weights"]), "reason": audit["reason"],
        "version_after": int(audit["version_after"]), "filtered": stats["filtered"],
        "unmapped": stats["unmapped"], "backup": bool(backup),
        "parser": stats, "audit": audit, "label_contract": LABEL_CONTRACT,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if audit["persisted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
