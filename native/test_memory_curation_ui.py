"""Headless construction test for the proposal-only memory dialog."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from memory_curation_ui import MemoryCurationDialog

APP = QApplication.instance() or QApplication([])


class MemoryCurationDialogTests(unittest.TestCase):
    def test_open_does_not_read_or_change_memory(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"DSH_HOME": directory}):
            dialog = MemoryCurationDialog()
            try:
                self.assertIsNone(dialog.plan)
                self.assertFalse(dialog.commit_button.isEnabled())
                self.assertFalse((Path(directory) / "dsh-dafeiyu-growth" / "memory.json").exists())
            finally:
                dialog.close()

    def test_plan_uses_real_memory_records_without_mutating_them(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"DSH_HOME": directory}):
            root = Path(directory) / "dsh-dafeiyu-growth"
            root.mkdir()
            path = root / "memory.json"
            path.write_text(json.dumps({
                "version": 1, "revision": 1,
                "records": [{
                    "id": "a" * 16, "kind": "habit", "text": "旧习惯",
                    "source": "local_habit", "importance": 0,
                    "createdAt": 1, "lastSeen": 1, "hits": 1,
                }],
                "blocked": [], "blockedSlots": [], "publicRevision": 0,
            }, ensure_ascii=False), encoding="utf-8")
            before = path.read_bytes()
            dialog = MemoryCurationDialog()
            try:
                dialog.build_plan()
                self.assertEqual(dialog.plan["records"], 1)
                self.assertEqual(dialog.plan["counts"]["archive"], 1)
                self.assertEqual(dialog.rows.count(), 1)
                self.assertTrue(dialog.commit_button.isEnabled())
                self.assertEqual(path.read_bytes(), before)
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
