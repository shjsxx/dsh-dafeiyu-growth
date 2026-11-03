"""Exercise real Qt grants and outgoing-history provenance; no network/storage."""
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from conversation_ui import ConversationDialog


class ConsentRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialog = ConversationDialog()
        self.dialog.set_cloud_state({'endpoint':'https://example.invalid/v1/chat/completions',
                                     'model':'test', 'max_tokens':64})

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def memory(self):
        self.dialog.memory_checkbox.click()
        self.assertEqual(self.dialog._confirm_kind, 'memory')
        self.dialog.allow_session_cloud()
        self.assertTrue(self.dialog.memory_context_enabled())

    def history(self):
        self.dialog.session_checkbox.click()
        self.assertEqual(self.dialog._confirm_kind, 'session')
        self.dialog.allow_session_cloud()
        self.assertTrue(self.dialog.session_cloud_enabled())

    def test_memory_permission_survives_independent_history_grant(self):
        self.memory()
        self.history()
        self.assertTrue(self.dialog.memory_context_enabled())
        self.dialog.session_checkbox.click()
        self.assertFalse(self.dialog.session_cloud_enabled())
        self.assertTrue(self.dialog.memory_context_enabled())

    def test_message_preview_discloses_optional_memory_instead_of_only_this(self):
        self.memory()
        self.dialog.cloud_button.setChecked(True)
        self.dialog.input.setText('本次问题')
        self.dialog.send()
        self.assertIn('摘要/800字符', self.dialog.confirm_label.text())
        self.assertNotIn('仅这一段', self.dialog.confirm_label.text())

    def test_default_preview_excludes_memory(self):
        self.dialog.cloud_button.setChecked(True)
        self.dialog.input.setText('本次问题')
        self.dialog.send()
        self.assertIn('不含历史或本地记忆摘要', self.dialog.confirm_label.text())

    def test_revoking_memory_excludes_past_memory_derived_replies(self):
        self.history()
        self.memory()
        self.dialog.show_reply('PRIVATE_MEMORY_FACT', private_context=True)
        self.dialog.show_reply('ORDINARY_REPLY')
        self.assertIn('PRIVATE_MEMORY_FACT', str(self.dialog.cloud_context()))
        self.dialog.memory_checkbox.click()
        self.assertTrue(self.dialog.session_cloud_enabled())
        self.assertNotIn('PRIVATE_MEMORY_FACT', str(self.dialog.cloud_context()))
        self.assertIn('ORDINARY_REPLY', str(self.dialog.cloud_context()))

    def test_proactive_private_fact_is_never_automatic_history(self):
        self.history()
        self.memory()
        self.dialog.show_proactive('UNSOLICITED_MEMORY_FACT')
        self.assertNotIn('UNSOLICITED_MEMORY_FACT', str(self.dialog.cloud_context()))

    def test_privacy_reset_purges_rows_draft_grants_and_pending(self):
        self.history()
        self.memory()
        row = self.dialog.add_message('user', 'PRIVATE_INPUT')
        self.dialog.input.setText('PRIVATE_DRAFT')
        self.dialog.set_busy(True)
        self.dialog.reset_local_context()
        self.assertFalse(self.dialog._rows)
        self.assertEqual(row['content'], '')
        self.assertEqual(self.dialog.input.text(), '')
        self.assertFalse(self.dialog.session_cloud_enabled())
        self.assertFalse(self.dialog.memory_context_enabled())
        self.assertFalse(self.dialog.busy)
        self.assertFalse(self.dialog._closed_notified)

    def test_target_change_revokes_both_grants(self):
        self.history()
        self.memory()
        self.dialog.set_cloud_state({'endpoint':'https://other.invalid/v1', 'model':'test', 'max_tokens':64})
        self.assertFalse(self.dialog.session_cloud_enabled())
        self.assertFalse(self.dialog.memory_context_enabled())


if __name__ == '__main__': unittest.main()
