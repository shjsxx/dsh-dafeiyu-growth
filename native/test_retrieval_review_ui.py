"""Offscreen tests for the passive consent-gated retrieval review dialog."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from retrieval_review_ui import RetrievalReviewDialog


HITS = [{'id': 'stable-a', 'text': '第一条记忆'}, {'id': 'stable-b', 'text': '第二条记忆'}]


class RetrievalReviewUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()

    def dialog(self, query='本次问题', hits=None, group='session-group'):
        dialog = RetrievalReviewDialog(query, HITS if hits is None else hits, group)
        self.dialogs.append(dialog)
        return dialog

    def check(self, dialog, index=0):
        dialog.candidates_list.item(index).setCheckState(Qt.CheckState.Checked)

    def test_default_has_no_consent_no_label_no_payload(self):
        dialog = self.dialog()
        self.assertFalse(dialog.consent_box.isChecked())
        self.assertFalse(dialog.no_answer_box.isChecked())
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIsNone(dialog.payload())
        self.assertEqual(dialog.consent_box.text(), '同意仅在本机保存该问题和命中标注（不保存整段对话，不上传）')

    def test_both_label_and_explicit_consent_are_required(self):
        dialog = self.dialog()
        self.check(dialog)
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIsNone(dialog.payload())
        dialog.consent_box.setChecked(True)
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.consent_box.setChecked(False)
        self.assertFalse(dialog.save_button.isEnabled())

    def test_consent_alone_does_not_accept_or_save(self):
        dialog = self.dialog()
        dialog.consent_box.setChecked(True)
        dialog.accept()
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIsNone(dialog.payload())

    def test_multiselect_returns_only_stable_ids_after_accept(self):
        dialog = self.dialog()
        self.check(dialog, 0)
        self.check(dialog, 1)
        dialog.consent_box.setChecked(True)
        self.assertIsNone(dialog.payload())
        dialog.save_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.payload(), {'query': '本次问题', 'candidates': ['stable-a', 'stable-b'],
                                           'relevant': ['stable-a', 'stable-b'], 'no_answer': False,
                                           'group': 'session-group'})
        self.assertNotIn('记忆', str(dialog.payload()))
        payload = dialog.payload()
        payload['relevant'].clear()
        self.assertEqual(dialog.payload()['relevant'], ['stable-a', 'stable-b'])

    def test_no_answer_and_relevant_are_mutually_exclusive_both_directions(self):
        dialog = self.dialog()
        self.check(dialog, 0)
        self.check(dialog, 1)
        dialog.no_answer_box.setChecked(True)
        self.assertEqual(dialog._selected(), [])
        self.check(dialog, 0)
        self.assertFalse(dialog.no_answer_box.isChecked())
        dialog.no_answer_box.setChecked(True)
        dialog.consent_box.setChecked(True)
        dialog.accept()
        self.assertEqual(dialog.payload()['relevant'], [])
        self.assertTrue(dialog.payload()['no_answer'])

    def test_empty_candidates_allow_explicit_no_answer(self):
        dialog = self.dialog(hits=[])
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.no_answer_box.setChecked(True)
        dialog.consent_box.setChecked(True)
        dialog.accept()
        self.assertEqual(dialog.payload()['candidates'], [])
        self.assertTrue(dialog.payload()['no_answer'])

    def test_cancel_discards_even_valid_consented_labels(self):
        dialog = self.dialog()
        self.check(dialog)
        dialog.consent_box.setChecked(True)
        dialog.cancel_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertIsNone(dialog.payload())

    def test_query_and_candidate_html_remain_literal_plaintext(self):
        query = '<img src="https://example.invalid/private">问题 & 数据'
        text = '<a href="https://example.invalid">私有记忆</a>'
        dialog = self.dialog(query=query, hits=[{'id': 'a', 'text': text}])
        self.assertEqual(dialog.query_view.toPlainText(), query)
        self.assertTrue(dialog.query_view.isReadOnly())
        self.assertEqual(dialog.candidates_list.item(0).text(), text)

    def test_invalid_candidates_query_or_group_fail_closed(self):
        cases = [(None, HITS, 'g'), ('问题', HITS, ''), ('x' * 301, HITS, 'g'),
                 ('问题', [{'text': 'missing ID'}], 'g'),
                 ('问题', [{'id': 'a', 'text': '第一'}, {'id': 'a', 'text': '重复'}], 'g'),
                 ('问题', [{'id': 'a', 'text': ''}], 'g'),
                 ('问题', 'malformed', 'g')]
        for query, hits, group in cases:
            with self.subTest(query=query, group=group):
                dialog = self.dialog(query=query, hits=hits, group=group)
                dialog.no_answer_box.setChecked(True)
                dialog.consent_box.setChecked(True)
                dialog.accept()
                self.assertFalse(dialog.save_button.isEnabled())
                self.assertIsNone(dialog.payload())

    def test_dialog_has_no_persistence_network_or_training_side_effects(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {'DSH_DAFEIYU_REVIEW_FILE': str(Path(directory) / 'reviews.json')}), \
                mock.patch('retrieval_review.RetrievalReviewStore.add') as add, \
                mock.patch('urllib.request.urlopen') as network:
            dialog = self.dialog()
            self.check(dialog)
            dialog.consent_box.setChecked(True)
            dialog.accept()
            self.assertIsNotNone(dialog.payload())
            add.assert_not_called()
            network.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
