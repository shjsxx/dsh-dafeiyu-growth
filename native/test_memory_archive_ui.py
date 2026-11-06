"""Headless construction test for the archive review dialog."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from memory_archive_ui import MemoryArchiveDialog

APP = QApplication.instance() or QApplication([])


class MemoryArchiveDialogTests(unittest.TestCase):
    def test_empty_archive_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"DSH_HOME": directory}):
            dialog = MemoryArchiveDialog()
            try:
                self.assertEqual(dialog.rows.count(), 0)
                self.assertFalse(dialog.restore_button.isEnabled())
                self.assertIn("为空", dialog.status.text())
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
