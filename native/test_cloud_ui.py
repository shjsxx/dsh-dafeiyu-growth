"""Optional Qt tests for cloud settings and per-message confirmation."""
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit

from cloud_ui import CloudChatDialog, CloudConfirmDialog, CloudSettingsDialog


class CloudUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_dialog_does_not_expose_existing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            dialog = CloudSettingsDialog({
                "endpoint": "https://api.example/v1/chat", "model": "m",
                "max_tokens": 128, "key_saved": True, "key": "secret",
                "path": str(Path(directory) / "cloud.json"),
            })
            self.assertEqual(dialog.key.text(), "")
            self.assertEqual(dialog.key.echoMode(), QLineEdit.EchoMode.Password)
            self.assertIn("留空", dialog.key.placeholderText())
            dialog.deleteLater()

    def test_confirmation_preview_is_plain_text_and_exact(self):
        text = "<b>hello</b>\nsecond line"
        dialog = CloudConfirmDialog("https://api.example/v1/chat", "m", 128, text)
        preview = dialog.findChild(QPlainTextEdit)
        self.assertIsNotNone(preview)
        self.assertEqual(preview.toPlainText(), text)
        for label in dialog.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        dialog.deleteLater()

    def test_chat_input_is_bounded(self):
        dialog = CloudChatDialog(name="<b>owner</b>")
        self.assertEqual(dialog.input.maxLength(), 2000)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
