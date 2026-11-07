"""Tests for bounded research memory and explicit archive moves."""
import json
import tempfile
import unittest
from pathlib import Path

from memory_engine import MemoryEngine


class MemoryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "memory.json"
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def memory(self):
        return MemoryEngine(self.path, clock=lambda: self.now)

    def test_research_summary_is_bounded_and_persistent(self):
        memory = self.memory()
        self.assertEqual(memory.remember_research("公开来源摘要 " * 100, now=self.now), 1)
        snapshot = memory.snapshot()
        self.assertEqual(snapshot["total"], 1)
        self.assertTrue(snapshot["recent"][0]["text"].startswith("研究摘要："))
        reloaded = self.memory()
        self.assertEqual(reloaded.snapshot()["total"], 1)

    def test_archive_moves_records_before_removing_them(self):
        memory = self.memory()
        memory.remember_research("first", now=self.now)
        memory.remember_research("second", now=self.now + 1)
        records = memory.export_records()
        self.assertEqual(len(records), 2)
        moved = memory.archive_records([records[0]["id"]], "quarantine", "duplicate", now=self.now + 2)
        self.assertEqual(moved, 1)
        self.assertEqual(memory.snapshot()["total"], 1)
        archive = json.loads((self.path.parent / "memory-archive.json").read_text(encoding="utf-8"))
        self.assertEqual(len(archive["records"]), 1)
        self.assertEqual(archive["records"][0]["mode"], "quarantine")
        reloaded = self.memory()
        self.assertEqual(reloaded.snapshot()["total"], 1)

    def test_archive_unknown_ids_is_noop(self):
        memory = self.memory()
        memory.remember_research("one", now=self.now)
        self.assertEqual(memory.archive_records(["missing"], "archive", "stale"), 0)
        self.assertEqual(memory.snapshot()["total"], 1)

    def test_archive_can_be_listed_and_restored_explicitly(self):
        memory = self.memory()
        memory.remember_research("restore me", now=self.now)
        record = memory.export_records()[0]
        self.assertEqual(memory.archive_records([record["id"]], "archive", "stale", now=self.now + 1), 1)
        archived = memory.list_archive()
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["text"], record["text"])
        self.assertEqual(memory.restore_archive([record["id"]], approved=False), 0)
        self.assertEqual(memory.snapshot()["total"], 0)
        self.assertEqual(memory.restore_archive([record["id"]], approved=True, now=self.now + 2), 1)
        self.assertEqual(memory.snapshot()["total"], 1)
        self.assertEqual(memory.list_archive(), [])
        self.assertEqual(self.memory().snapshot()["total"], 1)

    def test_restore_is_idempotent_and_rejects_unknown_ids(self):
        memory = self.memory()
        memory.remember_research("keep", now=self.now)
        record = memory.export_records()[0]
        memory.archive_records([record["id"]], "quarantine", "test", now=self.now + 1)
        self.assertEqual(memory.restore_archive([record["id"]], approved=True, now=self.now + 2), 1)
        self.assertEqual(memory.restore_archive([record["id"]], approved=True, now=self.now + 3), 0)
        self.assertEqual(memory.restore_archive(["missing"], approved=True), 0)
        self.assertEqual(memory.snapshot()["total"], 1)


if __name__ == "__main__":
    unittest.main()
