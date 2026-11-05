"""Unified learning archive tests: synthetic snapshots, no owner data."""
import unittest

from ability_model import derive_abilities
from learning_archive import build_learning_archive


class LearningArchiveTests(unittest.TestCase):
    def snapshots(self):
        state = {
            "counters": {"turns": 12},
            "learned": [{"text": "喜欢夜里散步", "at": "2026-11-04T20:00:00"}],
            "diary": [{"kind": "daily", "text": "今天完成了迁移", "at": "2026-11-04T21:00:00"}],
        }
        behavior = {"runs": 4, "jumps": 2, "actions": 9, "falls": 1, "recoveries": 1,
                    "pets": 3, "chats": 5, "discoveries": [{"text": "学会了小跑", "at": "2026-11-04T19:00:00"}]}
        training = {"journal": {"status": "ready", "totals": {"runs": 30, "weight_steps": 7,
                                                             "emotion_adaptation_passes": 2},
                                "events": [{"kind": "training", "domain": "emotion", "status": "success",
                                            "finished": 1793850000}]},
                    "knowledge": {"count": 11}}
        emotion = {"learnedCount": 20, "lexiconCount": 9, "fit_samples": 12}
        memory = {"status": "ready", "total": 42, "counts": {"preference": 2, "public": 40},
                  "recent": [{"kindLabel": "偏好", "text": "喜欢蓝色"}]}
        preferences = {"status": "ready", "actions": 6, "observations": 25, "liked": 3,
                       "disliked": 1, "updated": 1793850000}
        companion = {"affinity": 60, "days": 3}
        return state, behavior, training, emotion, memory, preferences, companion

    def test_all_sources_are_counted_and_named(self):
        archive = build_learning_archive(*self.snapshots())
        values = {row["key"]: row["value"] for row in archive["sources"]}
        self.assertEqual(values["practice"], 25)
        self.assertEqual(values["preferences"], 6)
        self.assertEqual(values["memory"], 42)
        self.assertEqual(values["training"], 30)
        self.assertEqual(archive["evidence_total"], 25 + 25 + 42 + 30)
        self.assertEqual(archive["preferences"]["observations"], 25)
        self.assertEqual(archive["memory"]["learned"], 1)
        self.assertEqual(archive["timeline"][0]["source"], "训练")
        self.assertEqual(archive["kind"], "all_records_are_learning")

    def test_hostile_values_degrade_and_never_raise(self):
        archive = build_learning_archive(None, {"runs": "x", "actions": float("inf")},
                                         {"journal": []}, None, {"total": -4},
                                         {"actions": True, "top": [None]}, None)
        self.assertEqual(archive["evidence_total"], 0)
        self.assertEqual(archive["timeline"], [])
        self.assertEqual(archive["memory"]["total"], 0)

    def test_preferences_and_memory_feed_ability_dimensions(self):
        state, behavior, training, emotion, memory, preferences, _ = self.snapshots()
        base = derive_abilities(state, behavior, training, emotion)
        enriched = derive_abilities(state, behavior, training, emotion, preferences, memory)
        dims = {row["key"]: row for row in enriched["dimensions"]}
        self.assertGreater(dims["memory"]["value"], 0)
        self.assertGreaterEqual(dims["memory"]["value"],
                                {row["key"]: row for row in base["dimensions"]}["memory"]["value"])
        self.assertIn("偏好", dims["action"]["basis"])
        self.assertIn("记忆", dims["learning"]["basis"])


if __name__ == "__main__":
    unittest.main()
