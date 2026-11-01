"""Optional Qt test for the explicit public-lexicon confirmation gate."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from learning_ui import LexiconLearningDialog


class LearningUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_confirmation_gate_and_source(self):
        dialog = LexiconLearningDialog()
        self.assertFalse(dialog.go.isEnabled())
        dialog.source.setText("https://example.test/lexicon.json")
        dialog.confirm.setChecked(True)
        self.assertTrue(dialog.go.isEnabled())
        self.assertEqual(dialog.source_url(), "https://example.test/lexicon.json")
        dialog.confirm.setChecked(False)
        self.assertFalse(dialog.go.isEnabled())
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
