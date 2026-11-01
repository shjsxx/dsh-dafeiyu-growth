"""Small, private emotion reader that learns from explicit owner statements.

It is deliberately not a large language model and never uses the network. Text
is turned into hashed character features; only hashes and numeric weights are
stored. A seed lexicon gives useful answers immediately, and explicit lines
such as "我今天很难过" add bounded increments to hashed feature weights. This is
additive adaptation, not SGD/backpropagation. Confidence is a heuristic reading,
not measured accuracy or emotion quality. Only committed changes count as fits.

No raw private sentence, name, or chat log is written to disk or audit metadata.
Public imported cue words remain available for lexicon matching.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

EMOTIONS = ("joy", "sad", "anger", "anxious", "tired", "calm")
LABELS = {
    "joy": "开心", "sad": "低落", "anger": "生气",
    "anxious": "焦虑", "tired": "疲惫", "calm": "平静", "neutral": "平静",
}
SEED = {
    "joy": ("开心", "高兴", "快乐", "兴奋", "幸福", "喜欢", "爱", "太好了", "棒", "不错",
            "哈哈", "嘿嘿", "爽", "美好", "期待", "惊喜", "感动", "好多了", "美滋滋"),
    "sad": ("难过", "伤心", "哭", "失落", "委屈", "孤独", "低落", "沮丧", "崩溃", "想哭",
            "不开心", "没意思", "心痛", "遗憾", "难受", "堵得慌", "心里堵", "没劲", "提不起劲"),
    "anger": ("生气", "愤怒", "火大", "气死", "烦", "讨厌", "可恶", "抓狂", "恼火",
              "受不了", "窝火", "气人", "烦死", "火冒三丈"),
    "anxious": ("焦虑", "紧张", "担心", "害怕", "慌", "压力", "不安", "恐惧", "忐忑",
                "心慌", "失眠", "纠结", "坐立不安"),
    "tired": ("累", "困", "疲惫", "没力气", "撑不住", "熬夜", "睡不够", "乏力", "倦",
              "瘫", "心累", "累死"),
    "calm": ("平静", "安心", "放松", "舒服", "还好", "踏实", "安稳", "轻松", "淡定", "缓过来"),
}
NEGATIONS = ("不", "没", "没有", "别", "并不", "不是", "未")
NEGATED_TARGET = {
    "joy": "sad", "sad": "calm", "anger": "calm",
    "anxious": "calm", "tired": "calm", "calm": "anxious",
}
POSITIVE = {"joy"}
NEGATIVE = {"sad", "anger", "anxious", "tired"}
EMOJI = {
    "joy": ("😊", "😄", "🥰", "😁", "😆", "❤️", "💖"),
    "sad": ("😭", "😢", "😞", "💔", "🥺"),
    "anger": ("😠", "😡", "🤬"),
    "anxious": ("😰", "😨", "😟"),
    "tired": ("😴", "🥱"),
    "calm": ("😌", "🙂"),
}
FIRST_PERSON = re.compile(r"(?:我|俺|本人|自己)")
SECOND_PERSON = re.compile(r"(?:你|您|他|她|它)")
QUESTION = re.compile(r"[?？]\s*$|(?:吗|呢|么)\s*[。.!！]?\s*$")
WORD_RUN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")
FEATURE_CAP = 640
LR = 0.12


def _hash(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=5).hexdigest()


def _default_path() -> Path:
    explicit = os.environ.get("DSH_DAFEIYU_EMOTION_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "emotion.json"
    return Path.home() / ".dsh" / "dsh-dafeiyu-growth" / "emotion.json"


def _clamp(value, low, high):
    return max(low, min(high, value))


def _finite(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        return value if math.isfinite(value) else default
    except (ValueError, OverflowError):
        return default


def _counter(value, maximum=10 ** 13):
    return int(_clamp(_finite(value), 0, maximum))


class EmotionModel:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else _default_path()
        self.weights: dict[str, dict[str, float]] = {}
        self.extra_cues: dict[str, set[str]] = {emotion: set() for emotion in EMOTIONS}
        self.seed_count = 0
        self.learned_count = 0
        self.revision = 0
        self.fit_samples = 0
        self.adaptation_passes = 0
        self.last_import = {"persisted": False, "features_before": 0, "features_after": 0,
                            "public_cues_before": 0, "public_cues_after": 0, "added": 0}
        self.events = 0
        self.mood = "neutral"
        self.mood_confidence = 0.0
        self.mood_ms = 0.0
        self.lexicon_count = 0
        self.source_host = ""
        self.status = "new"
        self.error = ""
        self._seed()
        self._load()

    # ---- persistence -------------------------------------------------
    def _seed(self) -> None:
        self.weights = {emotion: {} for emotion in EMOTIONS}
        for emotion, words in SEED.items():
            for word in words:
                self.weights[emotion][_hash("c:" + word)] = 1.0
        for emotion, emoji in EMOJI.items():
            for symbol in emoji:
                self.weights[emotion][_hash("e:" + symbol)] = 0.9
        self.seed_count = sum(len(row) for row in self.weights.values())

    def _load(self) -> None:
        if not self.path.exists():
            self.status = "new"
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("weights"), dict):
                raise ValueError("missing weights")
            loaded = {emotion: {} for emotion in EMOTIONS}
            for emotion, row in raw["weights"].items():
                if emotion not in loaded or not isinstance(row, dict):
                    continue
                for key, value in list(row.items())[:FEATURE_CAP]:
                    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{10}", key):
                        continue
                    number = _finite(value, None)
                    if number is not None:
                        loaded[emotion][key] = _clamp(number, -3.0, 3.0)
            if not any(loaded.values()):
                raise ValueError("empty weights")
            cues = {emotion: set() for emotion in EMOTIONS}
            extra = raw.get("extraCues")
            if isinstance(extra, dict):
                for emotion, words in extra.items():
                    if emotion in cues and isinstance(words, list):
                        cues[emotion] = {word[:20] for word in words[:300]
                                         if isinstance(word, str) and word.strip()}
            # Parse into locals first: a rejected profile must not partially
            # install weights, counters, cues, or mood over the seed state.
            mood = raw.get("mood")
            self.weights, self.extra_cues = loaded, cues
            self.events = _counter(raw.get("events"), 1_000_000)
            self.learned_count = _counter(raw.get("learnedCount"), 100_000)
            self.revision = _counter(raw.get("revision"))
            self.fit_samples = _counter(raw.get("fit_samples"))
            self.adaptation_passes = _counter(raw.get("adaptation_passes"))
            self.lexicon_count = sum(len(words) for words in cues.values())
            host = raw.get("sourceHost")
            self.source_host = host[:120] if isinstance(host, str) else ""
            self.mood = mood if isinstance(mood, str) and mood in (*EMOTIONS, "neutral") else "neutral"
            self.mood_confidence = _clamp(_finite(raw.get("moodConfidence")), 0, 1)
            self.mood_ms = max(0.0, _finite(raw.get("moodMs")))
            self.status = "ready"
        except (OSError, ValueError, TypeError, OverflowError):
            self.status = "protected"
            self.error = "emotion file could not be read; using seed model"
            self._seed()

    def _document(self) -> dict:
        return {
            "version": 1,
            "weights": {emotion: dict(sorted(row.items())) for emotion, row in self.weights.items()},
            "events": self.events,
            "learnedCount": self.learned_count,
            "revision": self.revision,
            "fit_samples": self.fit_samples,
            "adaptation_passes": self.adaptation_passes,
            "lexiconCount": self.lexicon_count,
            "sourceHost": self.source_host,
            "extraCues": {emotion: sorted(words) for emotion, words in self.extra_cues.items()},
            "mood": self.mood,
            "moodConfidence": self.mood_confidence,
            "moodMs": self.mood_ms,
        }

    def save(self) -> bool:
        if self.status == "protected":
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            temp.write_text(json.dumps(self._document(), ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                            encoding="utf-8")
            os.replace(temp, self.path)
            self.status = "ready"
            self.error = ""
            return True
        except (OSError, ValueError, TypeError, OverflowError):
            self.status = "error"
            self.error = "emotion file could not be written"
            return False

    def _feature_count(self) -> int:
        """Number of stored emotion/feature weight entries, not private keys."""
        return sum(len(row) for row in self.weights.values())

    def _state(self) -> dict:
        state = {key: getattr(self, key) for key in (
            "learned_count", "events", "mood", "mood_confidence", "mood_ms",
            "lexicon_count", "source_host", "revision", "fit_samples", "adaptation_passes", "status", "error",
        )}
        state["weights"] = {emotion: dict(row) for emotion, row in self.weights.items()}
        state["extra_cues"] = {emotion: set(words) for emotion, words in self.extra_cues.items()}
        return state

    def _commit_candidate(self, before: dict) -> bool:
        try:
            persisted = self.save() is True
        except (OSError, ValueError, TypeError, OverflowError):
            persisted = False
        if not persisted:
            for key, value in before.items():
                setattr(self, key, value)
            if self.status != "protected":
                self.status = "error"
                self.error = "emotion file could not be written"
        return persisted

    @staticmethod
    def _changed_weights(before, after) -> int:
        return sum(before[emotion].get(key) != after[emotion].get(key)
                   for emotion in EMOTIONS
                   for key in before[emotion].keys() | after[emotion].keys())

    @staticmethod
    def _prune(row) -> None:
        if len(row) > FEATURE_CAP:
            for key in sorted(row, key=lambda key: (abs(row[key]), key))[:len(row) - FEATURE_CAP]:
                del row[key]

    # ---- features and inference -------------------------------------
    @staticmethod
    def _negated(text: str, word: str) -> bool:
        for prefix in NEGATIONS:
            if prefix + word in text:
                return True
        return False

    def _features(self, text: str) -> set[str]:
        compact = re.sub(r"\s+", "", text.lower())[:300]
        features: set[str] = set()
        for emotion, words in SEED.items():
            for word in words:
                if word in compact:
                    features.add(_hash("c:" + word))
        for words in self.extra_cues.values():
            for word in words:
                if word and word in compact:
                    features.add(_hash("c:" + word))
        for emoji in [item for group in EMOJI.values() for item in group]:
            if emoji in compact:
                features.add(_hash("e:" + emoji))
        for run in WORD_RUN.findall(compact):
            for size in (2, 3):
                for index in range(0, max(0, len(run) - size + 1)):
                    features.add(_hash("g:" + run[index:index + size]))
        return features

    def classify(self, text: str) -> dict:
        raw = str(text or "")[:300]
        compact = re.sub(r"\s+", "", raw.lower())
        scores = {emotion: 0.0 for emotion in EMOTIONS}
        features = self._features(raw)
        for feature in sorted(features):
            for emotion in EMOTIONS:
                scores[emotion] += self.weights[emotion].get(feature, 0.0)
        # Negated cues should not also score their positive counterpart.
        for emotion, words in SEED.items():
            for word in words:
                if word in compact and self._negated(compact, word):
                    scores[emotion] -= 0.9
                    scores[NEGATED_TARGET[emotion]] += 0.9
        if "！" in compact or "!" in compact:
            scores["joy"] += 0.25
            scores["anger"] += 0.25
        if "？" in compact or "?" in compact:
            scores["anxious"] += 0.15
        if "…" in compact or "..." in compact:
            scores["sad"] += 0.2
        if QUESTION.search(compact):
            for emotion in EMOTIONS:
                scores[emotion] *= 0.75
        if not any(scores.values()):
            return {"label": "neutral", "confidence": 0.0, "confidence_kind": "heuristic", "scores": scores, "explicit": None}
        peak = max(scores.values())
        ordered = sorted(scores.values(), reverse=True)
        if peak < 0.55 or (peak < 1.15 and ordered[0] - ordered[1] < 0.15):
            return {"label": "neutral", "confidence": 0.0, "confidence_kind": "heuristic", "scores": scores, "explicit": None}
        total = sum(math.exp(max(-8.0, min(8.0, value))) for value in scores.values())
        probabilities = {
            emotion: math.exp(max(-8.0, min(8.0, value))) / total for emotion, value in scores.items()
        }
        label = max(probabilities, key=probabilities.get)
        return {
            "label": label,
            "confidence": round(probabilities[label], 3),
            "confidence_kind": "heuristic",
            "scores": {key: round(value, 3) for key, value in scores.items()},
            "explicit": None,
        }

    def compare_public(self, text: str) -> dict:
        """Local-only inference vs imported public cues; no sentence is returned.

        These are two heuristic readings, not cloud mind-reading or accuracy.
        No request ever includes text. Public lexicon freshness is tracked by
        the independent public scheduler, not inferred from a classification.
        """
        compact = re.sub(r"\s+", "", str(text or "").lower())[:300]
        counts = {emotion: 0 for emotion in EMOTIONS}
        for emotion, words in self.extra_cues.items():
            for word in words:
                if word and word in compact:
                    target = NEGATED_TARGET[emotion] if self._negated(compact, word) else emotion
                    counts[target] += 1
        ranked = sorted(counts, key=lambda key: counts[key], reverse=True)
        public = ranked[0] if counts[ranked[0]] > 0 and counts[ranked[0]] > counts[ranked[1]] else "neutral"
        local = self.classify(text)["label"]
        return {"modelLabel": local, "publicLabel": public,
                "matches": sum(counts.values()), "publicCues": self.lexicon_count,
                "agreement": local == public if public != "neutral" else None,
                "revision": self.revision, "localOnly": True}

    def detect_self_label(self, text: str) -> dict | None:
        """Conservative first-person self-report, e.g. 我今天有点累。"""
        raw = str(text or "")[:300]
        if QUESTION.search(raw):
            return None
        compact = re.sub(r"\s+", "", raw)
        found = []
        for emotion, words in SEED.items():
            for word in words:
                index = compact.find(word)
                if index < 0:
                    continue
                prefix = compact[max(0, index - 14):index]
                if not FIRST_PERSON.search(prefix):
                    continue
                if SECOND_PERSON.search(compact[max(0, index - 5):index]):
                    continue
                target = emotion
                negated = self._negated(compact, word)
                if negated:
                    target = NEGATED_TARGET[emotion]
                found.append((index, target, word, negated))
        if not found:
            return None
        found.sort(key=lambda item: item[0])
        _, label, _, negated = found[0]
        return {"label": label, "negated": negated}

    def _learn(self, text: str, label: str) -> int:
        """Apply one bounded additive pass; return actual changed weight cells.

        This is not a gradient, optimizer step, or loss-based fitting procedure.
        Selection and eviction are sorted so hash randomisation cannot change
        which features survive. Final-state comparison excludes reverted work.
        """
        features = sorted(self._features(text))[:48]
        if not features:
            return 0
        before = {emotion: dict(row) for emotion, row in self.weights.items()}
        row = self.weights[label]
        for feature in features:
            row[feature] = _clamp(row.get(feature, 0.0) + LR, -3.0, 3.0)
        for emotion in EMOTIONS:
            if emotion != label:
                other = self.weights[emotion]
                for feature in features:
                    if feature in other:
                        other[feature] = _clamp(other[feature] - LR * 0.2, -3.0, 3.0)
        self._prune(row)
        changed = self._changed_weights(before, self.weights)
        self.learned_count = min(100_000, self.learned_count + max(0, len(row) - len(before[label])))
        return changed

    def observe(self, text: str, now_ms: float | None = None) -> dict:
        """Infer and transactionally adapt only an explicit self-report.

        Audit contains only committed counts and fixed booleans, never input,
        feature hashes, labels, or model weights. A failed save rolls back even
        the observation/mood, so it cannot masquerade as persisted learning.
        """
        text = str(text or "")[:300]
        result = self.classify(text)
        explicit = self.detect_self_label(text)
        before = self._state()
        features_before = self._feature_count()
        changed = 0
        if explicit and explicit["label"] in EMOTIONS:
            result.update({"label": explicit["label"], "explicit": explicit["label"]})
            result["confidence"] = max(0.62, result["confidence"])
        persisted = False
        if self.status != "protected":
            if explicit and explicit["label"] in EMOTIONS:
                changed = self._learn(text, explicit["label"])
                if changed:
                    self.revision += 1
                    self.fit_samples += 1
                    self.adaptation_passes += 1
            label = result["label"]
            if explicit:
                label = explicit["label"]
            elif SECOND_PERSON.search(text) or QUESTION.search(text):
                label = "neutral"
            self.mood = label
            self.mood_confidence = _clamp(
                max(0.5, result["confidence"]) if label != "neutral" else 0.0, 0.0, 1.0
            )
            self.mood_ms = max(0.0, _finite(now_ms, time.time() * 1000))
            self.events += 1
            persisted = self._commit_candidate(before)
        result["mood"] = self.mood
        result["learned"] = bool(persisted and changed)
        result["audit"] = {
            "persisted": persisted, "adaptation_passes": int(persisted and bool(changed)),
            "changed_weights": changed if persisted else 0,
            "features_before": features_before, "features_after": self._feature_count(),
            "version_before": before["revision"], "version_after": self.revision,
            "samples_before": before["fit_samples"], "samples_after": self.fit_samples,
        }
        return result

    def current(self, now_ms: float | None = None) -> dict:
        now_ms = now_ms if now_ms is not None else time.time() * 1000
        mood = self.mood
        confidence = self.mood_confidence
        if not self.mood_ms or now_ms - self.mood_ms > 10 * 60 * 1000:
            mood = "neutral"
            confidence = 0.0
        return {"label": mood, "name": LABELS.get(mood, "平静"), "confidence": round(confidence, 3),
                "confidence_kind": "heuristic"}

    def merge_lexicon(self, mapping: dict, source_host: str = "") -> int:
        """Commit bounded public cue collection, not a neural training pass.

        Return newly retained public cues only after successful persistence.
        Existing seed weight entries may be reused without adding features.
        ``last_import`` distinguishes a committed no-change from save failure.
        """
        before = self._state()
        features_before = self._feature_count()
        cues_before = sum(len(words) for words in self.extra_cues.values())
        self.last_import = {
            "persisted": False, "features_before": features_before, "features_after": features_before,
            "public_cues_before": cues_before, "public_cues_after": cues_before, "added": 0,
        }
        if self.status == "protected":
            return 0
        mapping = mapping if isinstance(mapping, dict) else {}
        for emotion in EMOTIONS:
            words = mapping.get(emotion)
            if not isinstance(words, (list, tuple)):
                continue
            row = self.weights[emotion]
            cues = sorted({re.sub(r"\s+", "", word)[:20] for word in words[:300]
                           if isinstance(word, str) and word.strip()})
            for cue in cues:
                if not cue or (cue not in self.extra_cues[emotion] and len(self.extra_cues[emotion]) >= 300):
                    continue
                row.setdefault(_hash("c:" + cue), 1.0)
                self.extra_cues[emotion].add(cue)
            self._prune(row)
        self.lexicon_count = sum(len(words) for words in self.extra_cues.values())
        if isinstance(source_host, str) and source_host:
            self.source_host = source_host[:120]
        if self.weights != before["weights"] or self.extra_cues != before["extra_cues"]:
            self.revision += 1
        persisted = self._commit_candidate(before)
        added = max(0, self.lexicon_count - cues_before) if persisted else 0
        self.last_import.update({
            "persisted": persisted, "features_after": self._feature_count(),
            "public_cues_after": sum(len(words) for words in self.extra_cues.values()), "added": added,
        })
        return added

    def snapshot(self) -> dict:
        current = self.current()
        return {
            **current,
            "mood": current["label"],
            "emotion": current["label"],
            "events": self.events,
            "learned": self.learned_count,
            "features": self._feature_count(),
            "revision": self.revision,
            "fit_samples": self.fit_samples,
            "adaptation_passes": self.adaptation_passes,
            "lexicon": self.lexicon_count,
            "source": self.source_host,
            "status": self.status,
            "error": self.error,
        }

    def reset(self) -> bool:
        before = self._state()
        self._seed()
        self.learned_count = 0
        self.fit_samples = 0
        self.adaptation_passes = 0
        self.revision = before["revision"] + 1
        self.events = 0
        self.mood = "neutral"
        self.mood_confidence = 0.0
        self.mood_ms = 0.0
        self.lexicon_count = 0
        self.source_host = ""
        self.extra_cues = {emotion: set() for emotion in EMOTIONS}
        self.status = "new"
        self.error = ""
        return self._commit_candidate(before)

    def close(self) -> None:
        self.save()
