"""Automatic persona history is bounded, structured, and privacy-preserving."""
import json
import tempfile
import unittest
from pathlib import Path

from persona_history import PersonaHistory


TRAITS = ("warmth", "curiosity", "playfulness", "steadiness", "expressiveness")


def snapshot(revision, **values):
    traits = {key: 50.0 for key in TRAITS}
    traits.update(values)
    return {"revision": revision, "traits": traits}


class PersonaHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "persona-history.json"
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def history(self):
        return PersonaHistory(self.path, clock=lambda: self.now)

    def test_changed_snapshots_are_recorded_and_unchanged_is_noop(self):
        history = self.history()
        self.assertTrue(history.record(snapshot(0), now=self.now))
        self.assertFalse(history.record(snapshot(0), now=self.now + 1))
        self.assertTrue(history.record(snapshot(1, warmth=55), now=self.now + 2))
        entries = history.entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[-1]["traits"]["warmth"], 55)

    def test_evidence_keeps_only_structured_known_fields(self):
        history = self.history()
        history.record(snapshot(1), evidence=[
            {"kind": "feedback", "trait": "warmth", "action": "run",
             "liked": True, "at": self.now, "text": "private chat"},
            {"kind": "unknown", "trait": "warmth", "text": "drop"},
        ], now=self.now)
        evidence = history.latest()["evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["action"], "run")
        self.assertNotIn("text", evidence[0])

    def test_corrupt_history_is_protected(self):
        self.path.write_text("{not json", encoding="utf-8")
        history = self.history()
        self.assertEqual(history.status, "protected")
        self.assertFalse(history.record(snapshot(1), now=self.now))

    def test_history_never_stores_raw_snapshot_text(self):
        history = self.history()
        history.record(snapshot(1), reason="feedback " + "x" * 200, now=self.now)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertLessEqual(len(raw["entries"][-1]["reason"]), 40)
        self.assertNotIn("chat", raw["entries"][-1])


if __name__ == "__main__":
    unittest.main()
