"""Headless construction test for the explicit research dialog."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from research_ui import ResearchDialog

APP = QApplication.instance() or QApplication([])


class ResearchDialogTests(unittest.TestCase):
    def test_dialog_opens_without_starting_research(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"DSH_HOME": directory}):
            dialog = ResearchDialog()
            try:
                self.assertEqual(dialog.tasks.count(), 0)
                self.assertFalse(dialog.approve_button.isEnabled())
                self.assertFalse(dialog.run_button.isEnabled())
                self.assertEqual(dialog.service.research.list(), [])
                self.assertFalse((Path(directory) / "dsh-dafeiyu-growth" / "research.json").exists())
            finally:
                dialog.close()

    def test_auto_plan_only_fills_editable_urls(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"DSH_HOME": directory}):
            dialog = ResearchDialog()
            try:
                dialog.goal.setPlainText("Python asyncio 官方文档")
                dialog.plan()
                self.assertIn("https://docs.python.org/3/", dialog.urls.toPlainText())
                self.assertIn("已生成", dialog.status.text())
                self.assertEqual(dialog.service.research.list(), [])
                self.assertFalse((Path(directory) / "dsh-dafeiyu-growth" / "research.json").exists())
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
