"""Tests for explicit, bounded native-state backups."""
import json
import tempfile
import unittest
from pathlib import Path

from state_backup import BackupError, StateBackup


class StateBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.data = self.root / "state"
        self.data.mkdir()
        self.backups = self.root / "backups"
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def write_state(self, name="growth.json", body=None):
        path = self.data / name
        path.write_text(json.dumps(body or {"version": 1, "value": 1}), encoding="utf-8")
        return path

    def manager(self, **kwargs):
        return StateBackup(self.data, self.backups, clock=lambda: self.now, **kwargs)

    def test_create_verify_and_restore_exact_bytes(self):
        growth = self.write_state()
        memory = self.write_state("memory.json", {"version": 1, "records": []})
        original_growth = growth.read_bytes()
        original_memory = memory.read_bytes()
        backup = self.manager()
        snapshot = backup.create("manual")
        self.assertIsNotNone(snapshot)
        manifest = backup.verify(snapshot)
        self.assertEqual(set(manifest["files"]), {"growth.json", "memory.json"})

        growth.write_text('{"version":1,"value":99}', encoding="utf-8")
        memory.write_text('{"version":1,"records":[{"text":"changed"}]}', encoding="utf-8")
        restored = backup.restore(snapshot)
        self.assertEqual(restored, ["growth.json", "memory.json"])
        self.assertEqual(growth.read_bytes(), original_growth)
        self.assertEqual(memory.read_bytes(), original_memory)

    def test_restore_creates_safety_snapshot(self):
        self.write_state()
        backup = self.manager()
        snapshot = backup.create("first")
        self.write_state(body={"version": 1, "value": 2})
        backup.restore(snapshot)
        labels = [row["label"] for row in backup.list()]
        self.assertIn("before-restore", labels)
        self.assertGreaterEqual(len(backup.list()), 2)

    def test_corrupt_snapshot_is_rejected_before_writing(self):
        growth = self.write_state()
        original = growth.read_bytes()
        backup = self.manager()
        snapshot = backup.create()
        (snapshot / "growth.json").write_text("tampered", encoding="utf-8")
        with self.assertRaises(BackupError):
            backup.restore(snapshot)
        self.assertEqual(growth.read_bytes(), original)

    def test_corrupt_manifest_is_rejected(self):
        self.write_state()
        backup = self.manager()
        snapshot = backup.create()
        (snapshot / "manifest.json").write_text("{bad", encoding="utf-8")
        with self.assertRaises(BackupError):
            backup.verify(snapshot)

    def test_unknown_or_traversal_manifest_entry_is_rejected(self):
        self.write_state()
        backup = self.manager()
        snapshot = backup.create()
        document = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        document["files"]["../outside.json"] = {"size": 1, "sha256": "0" * 64}
        (snapshot / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(BackupError):
            backup.verify(snapshot)

    def test_retention_keeps_newest_snapshots(self):
        self.write_state()
        backup = StateBackup(self.data, self.backups, max_snapshots=3, clock=lambda: self.now)
        for index in range(6):
            backup.clock = lambda index=index: self.now + index * 60
            backup.create(f"run-{index}")
        rows = backup.list()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["label"], "run-5")

    def test_empty_data_directory_has_nothing_to_backup(self):
        backup = self.manager()
        self.assertIsNone(backup.create())
        self.assertEqual(backup.list(), [])

    def test_large_file_is_rejected_instead_of_silently_omitted(self):
        self.write_state()
        (self.data / "memory.json").write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        backup = self.manager()
        with self.assertRaises(BackupError):
            backup.create()


if __name__ == "__main__":
    unittest.main()
