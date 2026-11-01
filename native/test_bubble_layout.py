"""Persistence tests for the collapsible idle bubble."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'runtime'))

from layout_store import DEFAULT_LAYOUT, load_layout, normalise_layout, save_layout  # noqa: E402


class CollapseLayoutTests(unittest.TestCase):
    def test_collapsed_defaults_to_true(self):
        self.assertTrue(DEFAULT_LAYOUT['bubbleCollapsed'])
        self.assertTrue(normalise_layout({})['bubbleCollapsed'])

    def test_expanded_preference_round_trips(self):
        layout = normalise_layout({'bubbleCollapsed': False, 'scale': 0.6})
        self.assertFalse(layout['bubbleCollapsed'])
        self.assertEqual(layout['scale'], 0.6)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'layout.json'
            save_layout(path, layout)
            self.assertFalse(json.loads(path.read_text(encoding='utf-8'))['bubbleCollapsed'])
            self.assertFalse(load_layout(path)['bubbleCollapsed'])

    def test_malformed_value_keeps_default(self):
        self.assertTrue(normalise_layout({'bubbleCollapsed': 'no'})['bubbleCollapsed'])
        self.assertTrue(normalise_layout({'bubbleCollapsed': 0})['bubbleCollapsed'])

    def test_motion_controls_round_trip(self):
        self.assertTrue(DEFAULT_LAYOUT['desktopRoaming'])
        self.assertTrue(DEFAULT_LAYOUT['autonomousChatter'])
        layout = normalise_layout({'desktopRoaming': False, 'autonomousChatter': False})
        self.assertFalse(layout['desktopRoaming'])
        self.assertFalse(layout['autonomousChatter'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'layout.json'
            save_layout(path, layout)
            reloaded = load_layout(path)
            self.assertFalse(reloaded['desktopRoaming'])
            self.assertFalse(reloaded['autonomousChatter'])
        self.assertTrue(normalise_layout({'desktopRoaming': 0})['desktopRoaming'])


if __name__ == '__main__':
    unittest.main()
