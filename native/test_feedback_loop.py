"""Tests for the P1 feedback -> personality -> memory loop."""
import json
import tempfile
import unittest
from pathlib import Path

from memory_engine import MemoryEngine
from personality_engine import PersonalityEngine


class FeedbackLoopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def personality(self):
        return PersonalityEngine(self.root / "personality.json", clock=lambda: self.now)

    def memory(self):
        return MemoryEngine(self.root / "memory.json", clock=lambda: self.now)

    def test_like_moves_traits_slowly_and_persists(self):
        engine = self.personality()
        before = engine.snapshot()["traits"]
        engine.feedback(True)
        after = engine.snapshot()["traits"]
        self.assertGreater(after["warmth"], before["warmth"])
        self.assertGreater(after["playfulness"], before["playfulness"])
        self.assertEqual(engine.snapshot()["revision"], 1)
        reloaded = self.personality()
        self.assertEqual(reloaded.snapshot()["traits"], after)

    def test_dislike_does_not_collapse_warmth(self):
        engine = self.personality()
        engine.feedback(True)
        before = engine.snapshot()["traits"]
        engine.feedback(False)
        after = engine.snapshot()["traits"]
        self.assertLessEqual(after["playfulness"], before["playfulness"])
        self.assertGreaterEqual(after["warmth"], before["warmth"])
        self.assertGreaterEqual(after["warmth"], 0)

    def test_feedback_is_bounded_under_repetition(self):
        engine = self.personality()
        for _ in range(500):
            engine.feedback(True)
        traits = engine.snapshot()["traits"]
        for value in traits.values():
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)
        raw = (self.root / "personality.json").read_text(encoding="utf-8")
        self.assertNotIn("喜欢", raw)
        self.assertNotIn("不喜欢", raw)

    def test_protected_personality_is_not_modified(self):
        path = self.root / "personality.json"
        path.write_text("{bad", encoding="utf-8")
        before = path.read_bytes()
        engine = self.personality()
        self.assertEqual(engine.status, "protected")
        engine.feedback(True)
        self.assertEqual(path.read_bytes(), before)

    def test_action_feedback_is_one_memory_slot_and_can_change_verdict(self):
        memory = self.memory()
        self.assertEqual(memory.remember_feedback("原地左转奔跑", True), 1)
        first = memory.snapshot()
        self.assertEqual(first["total"], 1)
        row = first["recent"][0]
        self.assertIn("喜欢", row["text"])
        self.assertEqual(memory.remember_feedback("原地左转奔跑", False), 1)
        second = memory.snapshot()
        self.assertEqual(second["total"], 1)
        self.assertIn("不喜欢", second["recent"][0]["text"])
        reloaded = self.memory()
        self.assertEqual(reloaded.snapshot()["total"], 1)
        self.assertIn("不喜欢", reloaded.snapshot()["recent"][0]["text"])

    def test_repeated_same_verdict_does_not_duplicate_memory(self):
        memory = self.memory()
        self.assertEqual(memory.remember_feedback("螃蟹走路", True), 1)
        self.assertEqual(memory.remember_feedback("螃蟹走路", True), 0)
        self.assertEqual(memory.snapshot()["total"], 1)

    def test_memory_feedback_contains_no_chat_or_private_text(self):
        memory = self.memory()
        memory.remember_feedback("螃蟹走路", True)
        raw = (self.root / "memory.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertEqual(data["version"], 1)
        self.assertNotIn("userName", raw)
        self.assertNotIn("聊天", raw)


if __name__ == "__main__":
    unittest.main()
