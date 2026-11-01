"""One-shot, opt-in update from an openly licensed Chinese emotion dataset.

The dataset text is public and licensed; it is used only to derive hashed
features and is never written to disk, printed, or added to the repository.
Only bounded numeric audit fields are journaled.
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

from emotion_model import EmotionModel
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
    request = urllib.request.Request(url, headers={"User-Agent": "Yunlan/0.11 public-dataset update"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError("dataset_http")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise RuntimeError("dataset_too_large")
    return body


def parse_csv(body):
    text = body.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "text" not in reader.fieldnames:
        raise RuntimeError("dataset_schema")
    label_field = "emotion" if "emotion" in reader.fieldnames else "label" if "label" in reader.fieldnames else ""
    if not label_field:
        raise RuntimeError("dataset_schema")
    rows, stats = [], {"filtered": 0, "unmapped": 0, "unsafe": 0}
    for record in reader:
        content = " ".join(str(record.get("text") or "").split())
        label = LABEL_MAP.get(str(record.get(label_field) or "").strip())
        if not label:
            stats["unmapped"] += 1
            continue
        if (not 4 <= len(content) <= 120 or "http" in content.lower() or "@" in content
                or any(term in content for term in UNSAFE)
                or sum("\u4e00" <= ch <= "\u9fff" for ch in content) < max(3, len(content) // 3)):
            stats["filtered"] += 1
            continue
        rows.append((content, label))
    return rows, stats


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
    args = parser.parse_args()
    if args.staging:
        live = EmotionModel().path
        stage_dir = Path(tempfile.mkdtemp(prefix="yunlan-emotion-"))
        stage = stage_dir / "emotion.json"
        if live.is_file():
            shutil.copy2(live, stage)
        model = EmotionModel(stage)
        rows, stats = parse_csv(download())
        audit = model.learn_dataset(rows, source="huggingface.co/" + DATASET["id"])
        print(json.dumps({"ok": bool(audit["persisted"]), "staging": str(stage), "live": str(live),
                          "rows_used": int(audit["rows_used"]), "validation": int(audit["validation"]),
                          "accuracy_before": audit["accuracy_before"], "accuracy_after": audit["accuracy_after"],
                          "changed_weights": int(audit["changed_weights"]), "reason": audit["reason"],
                          "filtered": stats["filtered"], "unmapped": stats["unmapped"]}, ensure_ascii=False))
        return 0 if audit["persisted"] else 2
    model = EmotionModel()
    if model.status == "protected":
        print(json.dumps({"ok": False, "reason": "protected"}, ensure_ascii=False))
        return 1
    backup = backup_if_exists(model.path)
    rows, stats = parse_csv(download())
    audit = model.learn_dataset(rows, source="huggingface.co/" + DATASET["id"])
    journal = TrainingJournal()
    metrics = {
        "dataset_rows": int(audit["rows_total"]), "dataset_skipped": int(audit["skipped"]),
        "dataset_validation": int(audit["validation"]),
        "dataset_accuracy_before": float(audit["accuracy_before"]),
        "dataset_accuracy_after": float(audit["accuracy_after"]),
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
        "changed_weights": int(audit["changed_weights"]), "reason": audit["reason"],
        "version_after": int(audit["version_after"]), "filtered": stats["filtered"],
        "unmapped": stats["unmapped"], "backup": bool(backup),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if audit["persisted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
