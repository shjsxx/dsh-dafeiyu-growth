"""Tests for proposal-only memory curation; synthetic records only."""
import tempfile
import unittest
from pathlib import Path

from evolution_kernel import EvolutionKernel, EvolutionLedger, PolicyGate
from memory_curator import MemoryCurator


class MemoryCuratorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.kernel = EvolutionKernel(
            EvolutionLedger(self.root / "evolution.json", clock=lambda: 1_800_000_000),
            PolicyGate({"example.com"}))
        self.now = 1_800_000_000
        self.curator = MemoryCurator(self.kernel, now=lambda: self.now)

    def tearDown(self):
        self.directory.cleanup()

    def test_duplicate_slot_quarantines_weaker_copy(self):
        records = [
            {"id": "a", "kind": "preference", "slot": "name", "text": "称呼：小云",
             "importance": 82, "hits": 4, "lastSeen": self.now},
            {"id": "b", "kind": "preference", "slot": "name", "text": "称呼：小云",
             "importance": 82, "hits": 2, "lastSeen": self.now - 100},
        ]
        plan = self.curator.plan(records)
        self.assertEqual(plan["counts"]["quarantine"], 1)
        self.assertEqual(plan["counts"]["keep"], 1)
        quarantined = next(row for row in plan["actions"] if row["action"] == "quarantine")
        self.assertEqual(quarantined["id"], "b")
        self.assertEqual(plan["requiresApproval"], True)

    def test_conflicting_text_in_same_slot_is_quarantined_and_marked(self):
        records = [
            {"id": "a", "kind": "preference", "slot": "likes", "text": "喜欢：猫",
             "importance": 80, "hits": 2, "lastSeen": self.now},
            {"id": "b", "kind": "preference", "slot": "likes", "text": "喜欢：狗",
             "importance": 70, "hits": 1, "lastSeen": self.now},
        ]
        plan = self.curator.plan(records)
        bad = next(row for row in plan["actions"] if row["id"] == "b")
        self.assertEqual(bad["action"], "quarantine")
        self.assertIn("conflicting", bad["reason"])
        self.assertEqual(plan["counts"]["keep"], 1)

    def test_stale_low_value_record_is_archive_candidate_only(self):
        records = [{"id": "old", "kind": "diary", "slot": "old", "text": "旧回顾",
                    "importance": 10, "hits": 1, "lastSeen": self.now - 200 * 86400}]
        plan = self.curator.plan(records)
        self.assertEqual(plan["counts"]["archive"], 1)
        self.assertIn("stale", plan["actions"][0]["reason"])

    def test_malformed_records_are_counted_not_copied(self):
        plan = self.curator.plan([None, {"id": "x"}, {"id": "y", "text": "ok", "kind": "diary"}])
        self.assertGreaterEqual(plan["malformed"], 2)
        self.assertEqual(plan["records"], 1)

    def test_commit_requires_approval_and_backup(self):
        plan = self.curator.plan([{"id": "old", "kind": "diary", "slot": "old", "text": "x",
                                   "importance": 10, "hits": 1, "lastSeen": self.now - 900 * 86400}])
        called = []
        denied = self.curator.commit(plan, approved=False, backup_id="", handler=called.append)
        self.assertFalse(denied.allowed)
        self.assertEqual(called, [])
        allowed = self.curator.commit(plan, approved=True, backup_id="snap-1", handler=called.append)
        self.assertTrue(allowed.allowed)
        self.assertEqual(called[0]["archive"], ["old"])
        self.assertTrue(self.kernel.ledger.verify()["ok"])

    def test_delete_is_gated_and_requires_handler(self):
        called = []
        self.assertFalse(self.curator.delete(["a"], approved=False, backup_id="", handler=called.append).allowed)
        self.assertTrue(self.curator.delete(["a"], approved=True, backup_id="snap-1", handler=called.append).allowed)
        self.assertEqual(called, [["a"]])
        self.assertEqual([row["decision"] for row in self.kernel.ledger.recent(2)], ["denied", "allowed"])

    def test_plan_is_deterministic_and_does_not_mutate_records(self):
        records = [{"id": "b", "kind": "diary", "slot": "b", "text": "b", "importance": 1,
                    "hits": 1, "lastSeen": self.now},
                   {"id": "a", "kind": "diary", "slot": "a", "text": "a", "importance": 1,
                    "hits": 1, "lastSeen": self.now}]
        before = [dict(row) for row in records]
        first = self.curator.plan(records)
        second = self.curator.plan(records)
        self.assertEqual(first, second)
        self.assertEqual(records, before)


if __name__ == "__main__":
    unittest.main()
