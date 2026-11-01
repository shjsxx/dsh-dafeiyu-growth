"""Presentation regressions for the passive mist-teal dashboard redesign.

These tests use only synthetic snapshots and never instantiate stores or models.
Existing business/privacy suites remain the authority for snapshot semantics.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QLabel

from data_panel import DataPanel
from training_ui import TrainingPanel


class WorkbenchRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        self.app.processEvents()

    def test_panels_share_teal_and_readable_type_without_legacy_navy(self):
        for panel in (TrainingPanel(), DataPanel()):
            with self.subTest(panel=type(panel).__name__):
                qss = panel.styleSheet()
                self.assertIn("#17343D", qss)
                self.assertIn("#187E83", qss)
                self.assertIn("#D9E6E8", qss)
                self.assertIn("font-size: 24px", qss)
                self.assertNotIn("#172F49", qss)
                self.assertNotIn("font-size: 11px", qss)
                self.assertNotIn("font-size: 10px", qss)
                for label in panel.findChildren(QLabel):
                    self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
                panel.deleteLater()

    def test_training_metric_grid_is_two_by_two_with_wrapped_captions(self):
        panel = TrainingPanel()
        grid = panel.overview_page.layout().itemAt(0).layout()
        self.assertIsInstance(grid, QGridLayout)
        self.assertEqual(grid.rowCount(), 2)
        self.assertEqual(grid.columnCount(), 2)
        for caption in panel.pulse_captions.values():
            self.assertTrue(caption.wordWrap())
        self.assertEqual(panel.learning_button.minimumHeight(), 32)
        self.assertEqual(panel.history_list.height(), 150)
        self.assertEqual(panel.detail_view.height(), 110)
        panel.deleteLater()

    def test_empty_record_guidance_tracks_filters_without_placeholder_records(self):
        panel = TrainingPanel()
        self.assertEqual(panel.history_list.count(), 0)
        self.assertFalse(panel.history_empty_label.isHidden())
        event = {"id": 1, "kind": "action_fit", "domain": "action",
                 "source": "local_outcome", "status": "success", "metrics": {}}
        panel.update_data({"journal": {"revision": 1, "events": [event]}})
        self.assertTrue(panel.history_empty_label.isHidden())
        panel.domain_filter.setCurrentIndex(panel.domain_filter.findData("emotion"))
        self.assertFalse(panel.history_empty_label.isHidden())
        self.assertEqual(panel.history_list.count(), 0)
        panel.deleteLater()

    def test_source_and_model_semantic_states_remain_fixed_enums(self):
        panel = DataPanel()
        panel.update_data({"sourceCatalog": [{"id": "countries", "status": "error"}],
                           "emotion": {"status": "read_only"},
                           "knowledge": {"status": "protected"}})
        self.assertEqual(panel.source_labels["countries"].property("sourceState"), "error")
        self.assertEqual(panel.emotion_revision_label.property("modelState"), "read_only")
        self.assertEqual(panel.knowledge_revision_label.property("modelState"), "protected")
        self.assertTrue(panel.recent_label.property("empty"))
        panel.update_data({"sourceCatalog": [{"id": "countries", "status": "PRIVATE"}]})
        self.assertEqual(panel.source_labels["countries"].property("sourceState"), "unknown")
        self.assertNotIn("PRIVATE", panel.source_labels["countries"].text())
        panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
