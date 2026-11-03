"""Passive auxiliary-card regressions; no runtime, device, or network imports."""
import os
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel
from voice_ui import VoiceDataPanel
from online_ui import OnlinePanel
from render_growth_preview import synthetic_fixture


class AuxiliaryRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_voice_tiles_api_and_compatibility_are_distinct(self):
        panel = VoiceDataPanel()
        self.addCleanup(panel.deleteLater)
        panel.update_data(synthetic_fixture()['training']['journal'])
        self.assertEqual(len(panel.tile_values), 4)
        self.assertEqual(panel.tile_values['local'].text(), '1')
        self.assertEqual(panel.tile_values['ops'].text(), '0')
        self.assertEqual(panel.api_values['tokens'].text(), '640 / 240')
        self.assertIn('语音会话 1', panel.metrics.text())
        self.assertIn('非账单', panel.realtime_metrics.text())
        self.assertTrue(panel.metrics.isHidden())
        self.assertIn('识别片段', panel.recent.text())
        self.assertEqual(panel.audit_rows[0].focusPolicy(), Qt.FocusPolicy.StrongFocus)

    def test_voice_rejects_bool_raw_content_and_cross_enum_labels(self):
        panel = VoiceDataPanel()
        self.addCleanup(panel.deleteLater)
        panel.update_data({'totals': {'voice_sessions': True, 'realtime_input_tokens': 10**1000},
            'events': [{'kind': 'voice_io', 'started': True, 'status': ['PRIVATE'],
                        'labels': {'voice_event': 'open_notepad', 'decision': 'PRIVATE'},
                        'text': 'PRIVATE'}]})
        self.assertEqual(panel.tile_values['local'].text(), '0')
        self.assertEqual(panel.api_values['tokens'].text(), '10000000000 / 0')
        self.assertIn('--:--:--', panel.recent.text())
        self.assertNotIn('打开记事本', panel.recent.text())
        self.assertNotIn('PRIVATE', '\n'.join(item.text() for item in panel.findChildren(QLabel)))
        for item in panel.findChildren(QLabel):
            self.assertEqual(item.textFormat(), Qt.TextFormat.PlainText)

    def test_online_updates_are_passive_and_compatibility_timing_matches_grid(self):
        panel = OnlinePanel()
        self.addCleanup(panel.deleteLater)
        signals = [Mock(), Mock(), Mock()]
        for signal, callback in zip((panel.enabled_changed, panel.refresh_requested,
                                     panel.curriculum_requested), signals):
            signal.connect(callback)
        for state in ('empty', 'populated', 'error'):
            panel.update_state(synthetic_fixture(state)['online'])
            for value in panel.time_values.values():
                self.assertIn(value.text(), panel.timing.text())
        self.assertEqual(panel.status.property('state'), 'error')
        for callback in signals:
            callback.assert_not_called()
        self.assertTrue(panel.timing.isHidden())

    def test_preview_fixtures_are_bounded_and_voice_is_never_training(self):
        data = synthetic_fixture()['training']
        self.assertLessEqual(len(data['journal']['events']), 12)
        for event in data['journal']['events']:
            if event['kind'] in ('voice_io', 'computer_control'):
                self.assertEqual(event['metrics'].get('trained'), 0)
                self.assertEqual(event['metrics'].get('weight_steps', 0), 0)


if __name__ == '__main__':
    unittest.main()
