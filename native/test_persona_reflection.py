"""Tests for read-only persona reflection and gated rollback."""
import tempfile
import unittest
from pathlib import Path

from evolution_kernel import EvolutionKernel, EvolutionLedger, INVARIANTS, PolicyGate
from persona_reflection import PersonaReflection


class PersonaReflectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.kernel = EvolutionKernel(
            EvolutionLedger(self.root / "evolution.json", clock=lambda: 1_800_000_000),
            PolicyGate({"example.com"}))
        self.reflection = PersonaReflection(self.kernel)

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def snapshot(revision, **traits):
        values = {"warmth": 50, "curiosity": 50, "playfulness": 50,
                  "steadiness": 50, "expressiveness": 50}
        values.update(traits)
        return {"revision": revision, "traits": values, **INVARIANTS}

    def test_bounded_change_links_feedback_evidence(self):
        result = self.reflection.reflect(
            self.snapshot(1), self.snapshot(2, warmth=50.5, playfulness=50.6),
            [{"kind": "feedback", "action": "轻快摇摆舞", "liked": True, "trait": "warmth", "at": 10},
             {"kind": "feedback", "action": "轻快摇摆舞", "liked": True, "trait": "playfulness", "at": 10}])
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.changes), 2)
        self.assertTrue(all(row["linked"] for row in result.changes))
        self.assertIn("轻快摇摆舞", result.changes[0]["evidence"][0]["action"])

    def test_drift_over_cap_needs_review(self):
        result = self.reflection.reflect(self.snapshot(1), self.snapshot(2, warmth=54))
        self.assertEqual(result.status, "needs_review")
        self.assertTrue(any("漂移上限" in reason for reason in result.reasons))

    def test_core_invariant_change_needs_review(self):
        current = self.snapshot(2, warmth=50.5)
        current["no_silent_delete"] = False
        result = self.reflection.reflect(self.snapshot(1), current,
                                         [{"kind": "interaction", "trait": "warmth", "at": 10}])
        self.assertEqual(result.status, "needs_review")
        self.assertTrue(any("immutable" in reason for reason in result.reasons))

    def test_no_change_is_stable(self):
        result = self.reflection.reflect(self.snapshot(1), self.snapshot(1))
        self.assertEqual(result.status, "stable")
        self.assertEqual(result.changes, [])

    def test_unlinked_change_is_flagged_without_private_text(self):
        current = self.snapshot(2, warmth=50.5)
        result = self.reflection.reflect(self.snapshot(1), current,
                                         [{"kind": "feedback", "text": "private chat", "at": 10}])
        self.assertEqual(result.status, "needs_review")
        self.assertTrue(any("没有可关联证据" in reason for reason in result.reasons))
        self.assertNotIn("private chat", self.reflection.report(result)["summary"])

    def test_rollback_requires_approval_and_backup(self):
        denied = self.reflection.rollback_plan(self.snapshot(2), self.snapshot(1))
        self.assertFalse(denied.allowed)
        called = []
        allowed = self.reflection.rollback_plan(self.snapshot(2), self.snapshot(1),
                                                approved=True, backup_id="snap-1",
                                                handler=called.append)
        self.assertTrue(allowed.allowed)
        self.assertEqual(called[0]["revision"], 1)
        self.assertTrue(self.kernel.ledger.verify()["ok"])
        self.assertEqual([row["decision"] for row in self.kernel.ledger.recent(2)], ["denied", "allowed"])


if __name__ == "__main__":
    unittest.main()
