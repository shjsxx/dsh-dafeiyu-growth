"""Headless construction test for the proposal-only memory dialog."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
