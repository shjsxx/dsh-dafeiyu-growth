"""Native dialog tests; no real microphone/network/DPAPI/provider configuration."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QPushButton, QScrollArea

import realtime_config as rc
import realtime_settings_ui as ui


class RealtimeSettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "realtime.json"
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"DSH_DAFEIYU_REALTIME_FILE": str(self.path),
                                "DSH_DAFEIYU_REALTIME_KEY": ""}).start()
        patch("socket.create_connection", side_effect=AssertionError("network forbidden")).start()
        patch("cloud_chat.load_config", side_effect=AssertionError("cloud config forbidden")).start()
        self.vault = {}

        def protect(text):
            token = "mock-current-user-dpapi-" + str(len(self.vault))
            self.vault[token] = text
            return token

        patch.object(rc, "protect_secret", side_effect=protect).start()
        patch.object(rc, "unprotect_secret", side_effect=lambda token: self.vault[token]).start()

    def dialog(self, config=None):
        config = rc.load_config(self.path, env={}) if config is None else config
        dialog = ui.RealtimeSettingsDialog(config)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_construction_is_io_free_and_keeps_only_public_copy(self):
        config = {"endpoint": rc.DEFAULT_ENDPOINT, "model": "gpt-realtime", "voice": "marin",
                  "key": "never-display-existing-secret", "key_saved": True,
                  "key_protected": "do-not-retain-ciphertext", "path": str(self.path)}
        with patch.object(Path, "open", side_effect=AssertionError("construction IO forbidden")), patch.object(
                ui, "save_config", side_effect=AssertionError("construction save forbidden")):
            dialog = self.dialog(config)
        self.assertEqual(dialog.key.text(), "")
        self.assertEqual(dialog.key.echoMode(), QLineEdit.EchoMode.Password)
        self.assertIn("留空", dialog.key.placeholderText())
        self.assertNotIn("key", dialog.config)
        self.assertNotIn("key_protected", dialog.config)
        self.assertNotIn("never-display-existing-secret", repr(dialog.config))
        self.assertFalse(self.path.exists())

    def test_expected_controls_ranges_defaults_and_protocol_label(self):
        dialog = self.dialog()
        self.assertEqual(dialog.endpoint.text(), rc.DEFAULT_ENDPOINT)
        self.assertEqual(dialog.model.text(), "gpt-realtime")
        self.assertEqual(dialog.voice.text(), "marin")
        self.assertEqual((dialog.max_tokens.minimum(), dialog.max_tokens.maximum(), dialog.max_tokens.value()),
                         (16, 4096, 1024))
        self.assertEqual((dialog.session_seconds.minimum(), dialog.session_seconds.maximum(), dialog.session_seconds.value()),
                         (60, 1800, 600))
        self.assertEqual(dialog.protocol.currentData(), "ga")
        self.assertEqual([dialog.protocol.itemData(i) for i in range(dialog.protocol.count())], ["ga", "beta"])
        self.assertIn("兼容", dialog.protocol.itemText(1))
        self.assertEqual(dialog.model.maxLength(), 100)
        self.assertEqual(dialog.voice.maxLength(), 64)
        self.assertEqual(dialog.key.maxLength(), 8192)

    def test_scrollable_screen_bounded_readable_plaintext_and_no_test_button(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        self.assertIsInstance(dialog.scroll, QScrollArea)
        available = dialog.screen().availableGeometry()
        self.assertLessEqual(dialog.width(), available.width())
        self.assertLessEqual(dialog.height(), available.height())
        self.assertGreaterEqual(dialog.font().pixelSize(), 13)
        self.assertEqual(dialog.size().width(), 440 if available.width() >= 464 else available.width() - 24)
        labels = dialog.findChildren(QLabel)
        for label in labels:
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
            self.assertGreaterEqual(label.font().pixelSize(), 13)
        text = "\n".join(label.text() for label in labels)
        for required in ("原始麦克风音频", "API 密钥", "中国大陆", "服务账号", "文字聊天", "/chat/completions", "不会测试 API", "DPAPI"):
            self.assertIn(required, text)
        self.assertFalse(any("测试" in button.text() for button in dialog.findChildren(QPushButton)))
        dialog.hide()

    def test_cancel_discards_new_secret_and_never_saves(self):
        dialog = self.dialog()
        dialog.key.setText("new-secret")
        with patch.object(ui, "save_config") as save:
            dialog.cancel_button.click()
        save.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.key.text(), "")
        self.assertFalse(self.path.exists())

    def test_explicit_save_emits_accepted_only_after_local_persistence(self):
        dialog = self.dialog()
        accepted = []
        dialog.accepted.connect(lambda: accepted.append(self.path.exists()))
        dialog.key.setText("new-owner-token")
        dialog.voice.setText("alloy")
        dialog.protocol.setCurrentIndex(1)
        dialog.save_button.click()
        self.assertEqual(accepted, [True])
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(rc.load_config(self.path, env={})["key"], "new-owner-token")
        self.assertNotIn("new-owner-token", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("key", dialog.config)
        self.assertNotIn("new-owner-token", repr(dialog.config))
        self.assertEqual(dialog.key.text(), "")
        self.assertIn("已保存", dialog.status.text())
        self.assertIn("未授权", dialog.status.text())

    def test_empty_same_endpoint_preserves_saved_key_without_loading_into_input(self):
        original = rc.save_config(rc.DEFAULT_ENDPOINT, "gpt-realtime", "marin", 1024, 600, "ga",
                                  key="existing-owner-token", path=self.path)
        dialog = self.dialog(original)
        dialog.model.setText("other-model")
        dialog.save_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(rc.load_config(self.path, env={})["key"], "existing-owner-token")
        self.assertEqual(dialog.key.text(), "")
        self.assertNotIn("key", dialog.config)

    def test_new_endpoint_does_not_reuse_old_key_and_requires_clear_or_reentry(self):
        original = rc.save_config(rc.DEFAULT_ENDPOINT, "gpt-realtime", "marin", 1024, 600, "ga",
                                  key="existing-owner-token", path=self.path)
        before = self.path.read_bytes()
        dialog = self.dialog(original)
        accepted = []
        dialog.accepted.connect(lambda: accepted.append(True))
        dialog.endpoint.setText("wss://new.example/custom")
        dialog.save_button.click()
        self.assertEqual(accepted, [])
        self.assertEqual(dialog.status.text(), rc.ERRORS["target"])
        self.assertEqual(self.path.read_bytes(), before)
        dialog.clear_key.setChecked(True)
        dialog.save_button.click()
        self.assertEqual(accepted, [True])
        self.assertEqual(rc.load_config(self.path, env={})["key"], "")

    def test_validation_errors_do_not_accept_or_authorize(self):
        dialog = self.dialog()
        accepted = []
        dialog.accepted.connect(lambda: accepted.append(True))
        dialog.endpoint.setText("https://new.example/custom?key=secret-token")
        dialog.save_button.click()
        self.assertEqual(accepted, [])
        self.assertEqual(dialog.status.text(), rc.ERRORS["endpoint"])
        self.assertNotIn("secret-token", dialog.status.text())
        self.assertFalse(self.path.exists())

    def test_unknown_save_exception_is_never_displayed(self):
        dialog = self.dialog()
        with patch.object(ui, "save_config", side_effect=OSError("PRIVATE-PATH key=secret-token")):
            dialog.save_button.click()
        self.assertEqual(dialog.status.text(), rc.ERRORS["write"])
        self.assertFalse(self.path.exists())

    def test_corrupt_file_is_preserved_even_if_clear_is_requested(self):
        self.path.write_bytes(b"corrupt-owner-file")
        dialog = self.dialog()
        self.assertEqual(dialog.status.text(), rc.ERRORS["corrupt"])
        dialog.clear_key.setChecked(True)
        dialog.save_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(self.path.read_bytes(), b"corrupt-owner-file")

    def test_stale_dialog_cannot_overwrite_newer_configuration(self):
        original = rc.save_config(rc.DEFAULT_ENDPOINT, "gpt-realtime", "marin", 1024, 600, "ga", path=self.path)
        dialog = self.dialog(original)
        rc.save_config(rc.DEFAULT_ENDPOINT, "newer-model", "marin", 1024, 600, "ga", path=self.path)
        dialog.model.setText("stale-model")
        dialog.save_button.click()
        self.assertEqual(dialog.status.text(), rc.ERRORS["changed"])
        self.assertEqual(rc.load_config(self.path, env={})["model"], "newer-model")

    def test_invalid_display_fields_never_redirect_save_to_a_different_file(self):
        self.path.write_bytes(b"corrupt-owner-file")
        dialog = self.dialog({"endpoint": "invalid", "path": str(self.path)})
        self.assertEqual(dialog.config["path"], str(self.path))
        dialog.clear_key.setChecked(True)
        dialog.save_button.click()
        self.assertEqual(dialog.status.text(), rc.ERRORS["corrupt"])
        self.assertEqual(self.path.read_bytes(), b"corrupt-owner-file")

    def test_untrusted_constructor_error_is_fixed_text_not_secret(self):
        dialog = self.dialog({"error": "<b>secret-token PRIVATE-PATH</b>"})
        self.assertEqual(dialog.status.text(), rc.ERRORS["corrupt"])
        self.assertNotIn("secret-token", repr(dialog.config))


if __name__ == "__main__":
    unittest.main()
