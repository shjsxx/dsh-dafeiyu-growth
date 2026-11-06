"""Relationship memory persists structured topics, never raw conversation."""
import json
import tempfile
import unittest
from pathlib import Path

from companion_context import CompanionContext
from relationship import RelationshipStore


class RelationshipStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "relationship.json"
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def store(self):
        return RelationshipStore(self.path, clock=lambda: self.now)

    def context(self):
        context = CompanionContext()
        context.record("我昨天去了公园", "公园那边怎么样？")
        context.record("风很舒服", "原来公园的风让你觉得很舒服。")
        return context

    def test_structured_topics_survive_without_sentences(self):
        store = self.store()
        self.assertTrue(store.remember(self.context(), mood="joy", now=self.now))
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("我昨天去了公园", raw)
        self.assertNotIn("风很舒服", raw)
        self.assertIn("公园", raw)
        loaded = self.store()
        self.assertEqual(loaded.last_topic(), "公园")
        self.assertEqual(loaded.data["lastMood"], "joy")

    def test_seed_restores_followup_topic_after_new_session(self):
        store = self.store()
        store.remember(self.context(), mood="neutral", now=self.now)
        fresh = CompanionContext()
        store.seed_context(fresh)
        plan = fresh.plan("那里怎么样？")
        self.assertIn("公园", plan["text"])

    def test_bounds_and_corruption(self):
        store = self.store()
        context = CompanionContext()
        for index in range(30):
            context.record(f"我去了地点{index}", "记下了。")
        store.remember(context, now=self.now)
        self.assertLessEqual(len(store.snapshot()["topics"]), 20)
        self.path.write_text("{bad", encoding="utf-8")
        broken = self.store()
        self.assertEqual(broken.status, "protected")
        self.assertFalse(broken.remember(context, now=self.now))

    def test_opening_line_is_once_per_revision_and_not_too_soon(self):
        store = self.store()
        store.remember(self.context(), mood="neutral", now=self.now)
        self.assertEqual(store.opening_line(now=self.now + 60), "")
        line = store.opening_line(now=self.now + 3600)
        self.assertIn("公园", line)
        self.assertTrue(store.mark_opening(now=self.now + 3600))
        self.assertEqual(store.opening_line(now=self.now + 7200), "")
        store.remember(self.context(), mood="sad", now=self.now + 8000)
        self.assertIn("好一点", store.opening_line(now=self.now + 10000))


if __name__ == "__main__":
    unittest.main()
