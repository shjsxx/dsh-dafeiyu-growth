"""All-action preference model: temporary files only, no real profile."""
import json
from pathlib import Path
import tempfile
import unittest
from action_preferences import ActionPreferences, MAX_ACTIONS


class ActionPreferencesTests(unittest.TestCase):
    def test_shown_and_liked_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'action_preferences.json'
            model = ActionPreferences(path)
            self.assertEqual(model.status, 'new')
            self.assertTrue(model.observe('悠闲哼歌', 'shown'))
            self.assertTrue(model.observe('悠闲哼歌', 'liked'))
            self.assertGreater(model.preference('悠闲哼歌'), 0)
            reopened = ActionPreferences(path)
            self.assertEqual(reopened.status, 'ready')
            row = reopened.snapshot()['top'][0]
            self.assertEqual(row['name'], '悠闲哼歌')
            self.assertEqual(row['shown'], 1)
            self.assertEqual(row['liked'], 1)

    def test_dislike_and_unknown_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            model = ActionPreferences(Path(directory) / 'p.json')
            self.assertTrue(model.observe('吃西瓜', 'disliked'))
            self.assertLess(model.preference('吃西瓜'), 0)
            self.assertFalse(model.observe('', 'liked'))
            self.assertFalse(model.observe('x', 'unknown'))
            self.assertFalse(model.observe('x' * 81, 'shown'))
            self.assertEqual(model.preference('missing'), 0.0)

    def test_corrupt_and_wrong_version_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'p.json'
            path.write_text('{broken', encoding='utf-8')
            model = ActionPreferences(path)
            self.assertEqual(model.status, 'protected')
            self.assertFalse(model.observe('悠闲哼歌', 'shown'))
            path.write_text(json.dumps({'version': 1, 'actions': {}}), encoding='utf-8')
            self.assertEqual(ActionPreferences(path).status, 'protected')

    def test_choose_only_returns_known_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            model = ActionPreferences(Path(directory) / 'p.json')
            self.assertIsNone(model.choose())
            self.assertTrue(model.observe('悠闲哼歌', 'shown'))
            self.assertTrue(model.observe('吃西瓜', 'shown'))
            self.assertIn(model.choose(), ('悠闲哼歌', '吃西瓜'))
            self.assertEqual(model.choose(['吃西瓜']), '吃西瓜')
            self.assertIsNone(model.choose(['不存在']))

    def test_action_table_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            model = ActionPreferences(Path(directory) / 'p.json')
            for index in range(MAX_ACTIONS):
                self.assertTrue(model.observe(f'动作{index}', 'shown'))
            self.assertFalse(model.observe('溢出动作', 'shown'))
            self.assertEqual(len(model.snapshot(limit=20)['top']), 20)


if __name__ == '__main__':
    unittest.main()
