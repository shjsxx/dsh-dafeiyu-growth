"""Optional Qt tests for the unified local/cloud conversation window."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from conversation_ui import ConversationDialog


class ConversationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mode_and_messages_are_local_and_plain_text(self):
        dialog = ConversationDialog()
        self.assertFalse(dialog.is_cloud_mode())
        dialog.set_cloud_state({"endpoint": "https://api.deepseek.com/v1/chat/completions",
                                "model": "m", "max_tokens": 64, "key_saved": True})
        dialog.add_message("user", "<b>hello</b>", "本地")
        dialog.add_message("assistant", "我在。", "本地")
        for label in dialog.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn("api.deepseek.com", dialog.cloud_state.text())
        dialog.deleteLater()

    def test_cloud_confirm_is_inline_and_hides_after_cancel(self):
        dialog = ConversationDialog()
        dialog.set_cloud_state({"endpoint": "https://api.example/v1/chat/completions",
                                "model": "m", "max_tokens": 64, "key_saved": True})
        dialog.cloud_button.setChecked(True)
        dialog.input.setText("今天有点累")
        dialog.send()
        self.assertFalse(dialog.confirm_bar.isHidden())
        self.assertIn("今天有点累", dialog.confirm_label.text())
        dialog.cancel_confirm()
        self.assertTrue(dialog.confirm_bar.isHidden())
        self.assertTrue(dialog.input.isEnabled())
        dialog.deleteLater()

    def test_reply_reenables_input(self):
        dialog = ConversationDialog()
        dialog.set_busy(True)
        self.assertFalse(dialog.input.isEnabled())
        dialog.show_reply("好呀。", "本地")
        self.assertTrue(dialog.input.isEnabled())
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
