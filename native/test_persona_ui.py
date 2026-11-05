"""Headless construction test for persona reflection."""
import os
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from persona_ui import PersonaReflectionDialog

APP = QApplication.instance() or QApplication([])


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


if __name__ == "__main__":
    unittest.main()
