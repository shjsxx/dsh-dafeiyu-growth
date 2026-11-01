"""Tests for the private hashed emotion learner."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from emotion_model import EmotionModel, EMOTIONS, FEATURE_CAP, LR


class EmotionModelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "emotion.json"
        self.model = EmotionModel(self.path)

    def tearDown(self):
        self.model.close()
        self.directory.cleanup()

    def test_seed_labels(self):
        cases = {
            "我今天很难过": "sad",
            "好开心啊": "joy",
            "气死我了": "anger",
            "我有点焦虑": "anxious",
            "我累死了": "tired",
            "现在很平静": "calm",
        }
        for text, expected in cases.items():
            self.assertEqual(self.model.classify(text)["label"], expected, text)

    def test_negation_and_questions(self):
        self.assertEqual(self.model.classify("我不开心")["label"], "sad")
        self.assertEqual(self.model.classify("我不累")["label"], "calm")
        self.assertEqual(self.model.observe("你觉得难过吗")["mood"], "neutral")
        self.assertEqual(self.model.observe("我好开心")["mood"], "joy")

    def test_explicit_self_report_learns_hashed_features(self):
        phrase = "我很难过，像被抽空了一样"
        for _ in range(4):
            result = self.model.observe(phrase)
            self.assertTrue(result["learned"])
        self.assertEqual(self.model.classify("像被抽空了一样")["label"], "sad")
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("抽空", raw)
        self.assertNotIn("难过", raw)
        self.assertGreater(self.model.learned_count, 0)

    def test_mood_persists_and_expires(self):
        self.model.observe("我有点累")
        self.assertEqual(self.model.current()["label"], "tired")
        self.model.save()
        again = EmotionModel(self.path)
        try:
            self.assertEqual(again.current()["label"], "tired")
            later = again.current(now_ms=(self.model.mood_ms or 0) + 11 * 60 * 1000)
            self.assertEqual(later["label"], "neutral")
        finally:
            again.close()

    def test_merge_public_lexicon(self):
        added = self.model.merge_lexicon({"sad": ["惆怅", "空落落"]}, "example.test")
        self.assertEqual(added, 2)
        self.assertEqual(self.model.classify("我有点惆怅")["label"], "sad")
        self.assertEqual(self.model.snapshot()["source"], "example.test")

    def test_repeated_public_updates_are_idempotent(self):
        mapping = {"sad": ["惆怅", "空落落"], "tired": ["倦容"]}
        self.assertEqual(self.model.merge_lexicon(mapping), 3)
        snapshot = self.model.snapshot()
        self.assertEqual(self.model.merge_lexicon(mapping), 0)
        self.assertEqual(self.model.snapshot()["lexicon"], snapshot["lexicon"])
        again = EmotionModel(self.path)
        self.assertEqual(again.merge_lexicon(mapping), 0)
        self.assertEqual(again.classify("我有点空落落")["label"], "sad")

    def test_public_vocabulary_stays_bounded_across_daily_updates(self):
        for batch in range(5):
            self.model.merge_lexicon({"joy": [f"公开短词{batch * 300 + i}" for i in range(300)]})
        self.assertLessEqual(len(self.model.extra_cues["joy"]), 300)
        self.assertLessEqual(len(self.model.weights["joy"]), 640)
        self.assertLessEqual(self.model.snapshot()["lexicon"], 300)

    def test_reset_clears_learned_lexicon_and_mood(self):
        self.model.merge_lexicon({"joy": ["雀跃"]})
        self.model.observe("我今天很难过")
        self.assertTrue(self.model.reset())
        self.assertEqual(self.model.snapshot()["mood"], "neutral")
        self.assertEqual(self.model.snapshot()["lexicon"], 0)
        self.assertEqual(self.model.classify("我有点惆怅")["label"], "neutral")

    def test_corrupt_file_is_preserved_and_read_only(self):
        self.path.write_text("{broken", encoding="utf-8")
        model = EmotionModel(self.path)
        try:
            self.assertEqual(model.status, "protected")
            self.assertFalse(model.save())
            self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")
        finally:
            model.close()

    def test_weights_are_bounded(self):
        for _ in range(50):
            self.model.observe("我今天特别特别难过")
        for row in self.model.weights.values():
            for value in row.values():
                self.assertGreaterEqual(value, -3.0)
                self.assertLessEqual(value, 3.0)
        self.assertEqual(set(self.model.weights), set(EMOTIONS))

    def test_committed_fit_audit_and_counters_persist(self):
        before = self.model.snapshot()
        result = self.model.observe("我今天有点难过", now_ms=1234)
        audit = result["audit"]
        self.assertTrue(result["learned"])
        self.assertTrue(audit["persisted"])
        self.assertEqual(audit["adaptation_passes"], 1)
        self.assertGreater(audit["changed_weights"], 0)
        self.assertEqual(audit["features_before"], before["features"])
        self.assertEqual(audit["features_after"], self.model.snapshot()["features"])
        self.assertEqual((audit["version_before"], audit["version_after"]), (0, 1))
        self.assertEqual((audit["samples_before"], audit["samples_after"]), (0, 1))
        again = EmotionModel(self.path)
        self.assertEqual(again.revision, 1)
        self.assertEqual(again.fit_samples, 1)
        self.assertEqual(again.adaptation_passes, 1)
        self.assertEqual(again._document(), self.model._document())

    def test_fit_can_change_weights_without_new_features(self):
        feature = "0000000001"
        self.model.weights["sad"][feature] = .5
        features_before = self.model.snapshot()["features"]
        with patch.object(self.model, "_features", return_value={feature}):
            result = self.model.observe("我很难过")
        audit = result["audit"]
        self.assertTrue(result["learned"])
        self.assertEqual(audit["changed_weights"], 1)
        self.assertEqual(audit["features_before"], features_before)
        self.assertEqual(audit["features_after"], features_before)
        self.assertEqual(audit["adaptation_passes"], 1)
        self.assertAlmostEqual(self.model.weights["sad"][feature], .5 + LR)

    def test_saturation_is_not_reported_as_learning(self):
        feature = "0000000001"
        for emotion in EMOTIONS:
            self.model.weights[emotion][feature] = 3 if emotion == "sad" else -3
        before = copy.deepcopy(self.model.weights)
        with patch.object(self.model, "_features", return_value={feature}):
            result = self.model.observe("我很难过")
        audit = result["audit"]
        self.assertFalse(result["learned"])
        self.assertTrue(audit["persisted"])  # The observation, not a fit, was saved.
        self.assertEqual(audit["changed_weights"], 0)
        self.assertEqual(audit["adaptation_passes"], 0)
        self.assertEqual(audit["version_before"], audit["version_after"])
        self.assertEqual(audit["samples_before"], audit["samples_after"])
        self.assertEqual(self.model.weights, before)
        self.assertEqual(self.model.events, 1)
        self.assertEqual(self.model.fit_samples, 0)
        self.assertEqual(self.model.adaptation_passes, 0)

    def test_pruned_candidate_without_net_change_is_not_learning(self):
        self.model.weights["sad"] = {f"{i:010x}": 3.0 for i in range(FEATURE_CAP)}
        before = copy.deepcopy(self.model.weights)
        with patch.object(self.model, "_features", return_value={"ffffffffff"}):
            result = self.model.observe("我很难过")
        self.assertEqual(self.model.weights, before)
        self.assertFalse(result["learned"])
        self.assertEqual(result["audit"]["changed_weights"], 0)
        self.assertEqual(result["audit"]["adaptation_passes"], 0)

    def test_changed_weights_counts_other_emotion_cells(self):
        feature = "0000000001"
        for emotion in EMOTIONS:
            self.model.weights[emotion][feature] = 3 if emotion == "sad" else .5
        with patch.object(self.model, "_features", return_value={feature}):
            result = self.model.observe("我很难过")
        self.assertTrue(result["learned"])
        self.assertEqual(result["audit"]["changed_weights"], len(EMOTIONS) - 1)

    def test_feature_selection_is_sorted_and_reproducible(self):
        features = [f"{i:010x}" for i in range(100, 180)]
        other = EmotionModel(Path(self.directory.name) / "other.json")
        with patch.object(self.model, "_features", return_value=list(reversed(features))):
            first = self.model.observe("我很难过", now_ms=123)
        with patch.object(other, "_features", return_value=features):
            second = other.observe("我很难过", now_ms=123)
        self.assertEqual(first["audit"], second["audit"])
        self.assertEqual(self.model.weights, other.weights)
        for feature in sorted(features)[:48]:
            self.assertEqual(self.model.weights["sad"][feature], LR)
        for feature in sorted(features)[48:]:
            self.assertNotIn(feature, self.model.weights["sad"])

    def test_no_label_is_only_observation_not_weight_training(self):
        before = copy.deepcopy(self.model.weights)
        result = self.model.observe("今天的文件已经整理完毕", now_ms=500)
        self.assertFalse(result["learned"])
        self.assertTrue(result["audit"]["persisted"])
        self.assertEqual(result["audit"]["changed_weights"], 0)
        self.assertEqual(result["audit"]["adaptation_passes"], 0)
        self.assertEqual(self.model.weights, before)
        self.assertEqual(self.model.events, 1)
        self.assertEqual(self.model.revision, 0)
        self.assertEqual(self.model.fit_samples, 0)
        self.assertEqual(self.model.adaptation_passes, 0)

    def test_failed_fit_save_restores_weights_counters_and_mood(self):
        self.model.observe("我好开心", now_ms=100)
        before = self.model._document()
        raw = self.path.read_bytes()
        with patch.object(self.model, "save", return_value=False):
            result = self.model.observe("我特别难过，什么都不想做", now_ms=200)
        self.assertEqual(self.model._document(), before)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.model.status, "error")
        self.assertFalse(result["learned"])
        self.assertEqual(result["mood"], "joy")
        audit = result["audit"]
        self.assertFalse(audit["persisted"])
        self.assertEqual(audit["changed_weights"], 0)
        self.assertEqual(audit["adaptation_passes"], 0)
        self.assertEqual(audit["features_before"], audit["features_after"])
        self.assertEqual(audit["version_before"], audit["version_after"])
        self.assertEqual(audit["samples_before"], audit["samples_after"])

    def test_raised_save_error_restores_candidate(self):
        before = self.model._document()
        with patch.object(self.model, "save", side_effect=OSError("PRIVATE_PATH")):
            result = self.model.observe("我很难过")
        self.assertFalse(result["learned"])
        self.assertFalse(result["audit"]["persisted"])
        self.assertEqual(self.model._document(), before)
        self.assertNotIn("PRIVATE_PATH", self.model.error)

    def test_real_atomic_replace_failure_preserves_original_file(self):
        self.model.observe("我今天很开心", now_ms=100)
        before = self.model._document()
        raw = self.path.read_bytes()
        with patch("emotion_model.os.replace", side_effect=OSError("replace denied")):
            result = self.model.observe("我很难过", now_ms=200)
        self.assertFalse(result["audit"]["persisted"])
        self.assertFalse(result["learned"])
        self.assertEqual(self.model._document(), before)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_failed_unlabelled_observation_restores_mood_and_events(self):
        before = self.model._document()
        with patch.object(self.model, "save", return_value=False):
            result = self.model.observe("这是一条普通记录", now_ms=123)
        self.assertFalse(result["audit"]["persisted"])
        self.assertEqual(self.model._document(), before)

    def test_protected_observe_does_not_mutate_or_claim_learning(self):
        self.path.write_text("{broken", encoding="utf-8")
        protected = EmotionModel(self.path)
        before = protected._document()
        with patch.object(protected, "_learn", wraps=protected._learn) as learn, \
                patch.object(protected, "save", wraps=protected.save) as save:
            result = protected.observe("我很难过", now_ms=123)
            learn.assert_not_called()
            save.assert_not_called()
        self.assertEqual(protected._document(), before)
        self.assertEqual(protected.status, "protected")
        self.assertFalse(result["learned"])
        self.assertFalse(result["audit"]["persisted"])
        self.assertEqual(result["audit"]["changed_weights"], 0)
        protected.close()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_protected_import_is_zero_and_preserves_corrupt_file(self):
        self.path.write_text("{broken", encoding="utf-8")
        protected = EmotionModel(self.path)
        before = protected._document()
        self.assertEqual(protected.merge_lexicon({"sad": ["惆怅"]}, "example.test"), 0)
        self.assertEqual(protected._document(), before)
        self.assertFalse(protected.last_import["persisted"])
        self.assertEqual(protected.last_import["added"], 0)
        self.assertEqual(protected.last_import["features_before"], protected.last_import["features_after"])
        protected.close()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_failed_import_restores_cues_weights_counters_and_source(self):
        self.model.merge_lexicon({"sad": ["惆怅"]}, "original.test")
        self.model.observe("我好开心", now_ms=100)
        before = self.model._document()
        raw = self.path.read_bytes()
        with patch.object(self.model, "save", return_value=False):
            added = self.model.merge_lexicon({"joy": ["乐不可支"]}, "new.test")
        self.assertEqual(added, 0)
        self.assertEqual(self.model._document(), before)
        self.assertEqual(self.path.read_bytes(), raw)
        audit = self.model.last_import
        self.assertFalse(audit["persisted"])
        self.assertEqual(audit["added"], 0)
        self.assertEqual(audit["features_before"], audit["features_after"])
        self.assertEqual(audit["public_cues_before"], audit["public_cues_after"])

    def test_import_audit_counts_public_cues_separately_from_fitting(self):
        features = self.model.snapshot()["features"]
        self.assertEqual(self.model.merge_lexicon({"sad": ["难过"]}), 1)
        audit = self.model.last_import
        self.assertTrue(audit["persisted"])
        self.assertEqual((audit["features_before"], audit["features_after"]), (features, features))
        self.assertEqual((audit["public_cues_before"], audit["public_cues_after"]), (0, 1))
        self.assertEqual(audit["added"], 1)
        self.assertEqual(self.model.fit_samples, 0)
        self.assertEqual(self.model.adaptation_passes, 0)
        self.assertEqual(self.model.revision, 1)
        self.assertEqual(self.model.merge_lexicon({"sad": ["难过"]}), 0)
        self.assertTrue(self.model.last_import["persisted"])
        self.assertEqual(self.model.revision, 1)
        again = EmotionModel(self.path)
        self.assertEqual(again.revision, 1)
        self.assertEqual(again.merge_lexicon({"sad": ["难过"]}), 0)

    def test_import_is_deterministic_bounded_and_idempotent_at_capacity(self):
        other = EmotionModel(Path(self.directory.name) / "other.json")
        cues = [f"公共词{i}" for i in range(300)]
        self.model.weights["sad"] = {f"{i:010x}": 1.0 for i in range(FEATURE_CAP)}
        other.weights = copy.deepcopy(self.model.weights)
        self.assertEqual(self.model.merge_lexicon({"sad": cues}), 300)
        self.assertEqual(other.merge_lexicon({"sad": list(reversed(cues))}), 300)
        self.assertEqual(self.model.weights, other.weights)
        self.assertEqual(self.model.extra_cues, other.extra_cues)
        before = self.model._document()
        self.assertEqual(self.model.merge_lexicon({"sad": cues}), 0)
        self.assertEqual(self.model._document(), before)
        self.assertLessEqual(len(self.model.weights["sad"]), FEATURE_CAP)
        self.assertEqual(len(self.model.extra_cues["sad"]), 300)
        self.assertEqual(self.model.merge_lexicon({"sad": ["另一条公共词"]}), 0)
        self.assertEqual(self.model._document(), before)

    def test_malformed_import_ignores_non_string_cues(self):
        class NotText:
            def __str__(self):
                raise AssertionError("arbitrary objects must not become cues")
        for malformed in (None, [], "bad", {"sad": None}, {"sad": [None, {}, [], True, NotText(), " "]}):
            with self.subTest(mapping=type(malformed).__name__):
                self.assertEqual(self.model.merge_lexicon(malformed), 0)
        self.assertEqual(self.model.lexicon_count, 0)
        self.assertEqual(self.model.revision, 0)

    def test_legacy_profile_remains_loadable_with_zero_new_counters(self):
        self.model.observe("我有点累", now_ms=123)
        legacy = self.model._document()
        for key in ("revision", "fit_samples", "adaptation_passes"):
            legacy.pop(key)
        self.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        again = EmotionModel(self.path)
        self.assertEqual(again.status, "ready")
        self.assertEqual(again.revision, 0)
        self.assertEqual(again.fit_samples, 0)
        self.assertEqual(again.adaptation_passes, 0)
        self.assertEqual(again.weights, self.model.weights)
        self.assertEqual(again.events, self.model.events)
        self.assertEqual(again.mood, self.model.mood)
        self.assertEqual(again.learned_count, self.model.learned_count)
        self.assertTrue(again.save())
        upgraded = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(upgraded["version"], 1)
        self.assertEqual(upgraded["revision"], 0)
        self.assertEqual(upgraded["fit_samples"], 0)
        self.assertEqual(upgraded["adaptation_passes"], 0)

    def test_audits_never_include_raw_private_text_features_or_hashes(self):
        private = "私人姓名海棠编号777"
        result = self.model.observe("我很难过，" + private)
        audit = result["audit"]
        self.assertEqual(set(audit), {"persisted", "adaptation_passes", "changed_weights",
                                     "features_before", "features_after", "version_before", "version_after",
                                     "samples_before", "samples_after"})
        for value in audit.values():
            self.assertIsInstance(value, (bool, int))
        self.assertNotIn(private, json.dumps(result, ensure_ascii=False))
        stored = self.path.read_text(encoding="utf-8")
        self.assertNotIn(private, stored)
        self.assertNotIn("难过", stored)
        self.assertNotIn("loss", json.dumps(audit))
        self.assertNotIn("accuracy", json.dumps(audit))
        self.model.merge_lexicon({"joy": ["公共喜悦词"]}, "example.test")
        for value in self.model.last_import.values():
            self.assertIsInstance(value, (bool, int))
        self.assertNotIn("公共喜悦词", json.dumps(self.model.last_import, ensure_ascii=False))
        self.assertNotIn("example.test", json.dumps(self.model.last_import))

    def test_classification_confidence_is_explicitly_heuristic(self):
        for text in ("我很难过", "完全没有任何情绪词句", ""):
            self.assertEqual(self.model.classify(text)["confidence_kind"], "heuristic")
        self.assertEqual(self.model.observe("我很难过")["confidence_kind"], "heuristic")
        self.assertEqual(self.model.snapshot()["confidence_kind"], "heuristic")
        self.assertNotIn("accuracy", self.model.snapshot())
        self.assertNotIn("loss", self.model.snapshot())

    def test_failed_reset_also_restores_committed_model(self):
        self.model.observe("我很难过")
        self.model.merge_lexicon({"sad": ["惆怅"]})
        before = self.model._document()
        with patch.object(self.model, "save", return_value=False):
            self.assertFalse(self.model.reset())
        self.assertEqual(self.model._document(), before)

    def test_failed_protected_reset_does_not_unprotect_corrupt_file(self):
        self.path.write_text("{broken", encoding="utf-8")
        protected = EmotionModel(self.path)
        with patch.object(protected, "save", return_value=False):
            self.assertFalse(protected.reset())
        self.assertEqual(protected.status, "protected")
        protected.close()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_nonfinite_legacy_metadata_does_not_escape_into_saved_state(self):
        raw = self.model._document()
        raw.update(moodConfidence=float("nan"), moodMs=float("inf"), revision=float("inf"),
                   fit_samples=True, adaptation_passes="PRIVATE_STRING")
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        again = EmotionModel(self.path)
        self.assertEqual(again.status, "ready")
        self.assertEqual(again.mood_confidence, 0)
        self.assertEqual(again.mood_ms, 0)
        self.assertEqual(again.revision, 0)
        self.assertEqual(again.fit_samples, 0)
        self.assertEqual(again.adaptation_passes, 0)
        self.assertTrue(again.save())
        self.assertNotIn("NaN", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("Infinity", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("PRIVATE_STRING", self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
