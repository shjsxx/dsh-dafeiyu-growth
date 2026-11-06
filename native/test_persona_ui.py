"""Headless construction test for persona reflection."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from persona_history import PersonaHistory
from persona_ui import PersonaReflectionDialog

APP = QApplication.instance() or QApplication([])
TRAITS = ("warmth", "curiosity", "playfulness", "steadiness", "expressiveness")


class PersonaDialogTests(unittest.TestCase):
    def test_open_without_anchors_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"DSH_HOME": directory}):
            dialog = PersonaReflectionDialog()
            try:
                self.assertIsNone(dialog.previous)
                self.assertFalse(dialog.rollback_button.isEnabled())
                self.assertIn("两个连续性锚点", dialog.summary.text())
            finally:
                dialog.close()

    def test_automatic_history_is_used_with_structured_evidence(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"DSH_HOME": directory}):
            root = Path(directory) / "dsh-dafeiyu-growth"
            root.mkdir()
            traits = {key: 50.0 for key in TRAITS}
            changed = {**traits, "warmth": 55.0}
            (root / "personality.json").write_text(json.dumps({
                "version": 2, "revision": 1, "traits": changed,
                "interactions": 1, "updatedAt": 1_800_000_002,
                "observedTurns": 1,
            }), encoding="utf-8")
            history = PersonaHistory(root / "persona-history.json", clock=lambda: 1_800_000_002)
            history.record({"revision": 0, "traits": traits}, now=1_800_000_001)
            history.record({"revision": 1, "traits": changed}, evidence=[{
                "kind": "feedback", "trait": "warmth", "action": "run",
                "liked": True, "at": 1_800_000_002,
            }], now=1_800_000_002)
            dialog = PersonaReflectionDialog()
            try:
                self.assertIn("自动记录", dialog.summary.text())
                self.assertEqual(dialog.changes.count(), 1)
                self.assertIn("有证据", dialog.changes.item(0).text())
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
