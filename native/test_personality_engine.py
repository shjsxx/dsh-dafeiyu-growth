"""Personality engine: bounded traits, tone, initiative and memory influence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from personality_engine import DIMENSIONS, PersonalityEngine


class PersonalityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'personality.json'
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def engine(self):
        return PersonalityEngine(self.path, clock=lambda: self.now)

    def test_initial_update_is_bounded_and_persisted(self):
        engine = self.engine()
        snapshot = engine.update({'trust': .8, 'curiosity': .7, 'energy': .9, 'valence': .6},
                                 {}, {'counters': {'turns': 120}, 'streak': 6})
        self.assertEqual(snapshot['status'], 'ready')
        self.assertEqual(set(snapshot['traits']), set(DIMENSIONS))
        for value in snapshot['traits'].values():
            self.assertTrue(0 <= value <= 100)
        self.assertTrue(self.path.exists())
        document = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(document['version'], 1)

    def test_tone_mapping_and_initiative_are_bounded(self):
        engine = self.engine()
        self.assertEqual(engine.tone('sad'), 'gentle')
        self.assertIn(engine.tone('neutral'), {'steady', 'curious'})
        for mood in ('sad', 'joy', 'neutral', 'tired'):
            for has_memory in (False, True):
                value = engine.initiative(mood, has_memory)
                self.assertTrue(.05 <= value <= .95)
        memory = [{'text': '喜欢夜里散步'}]
        self.assertIn('夜里散步', engine.proactive('陪着你', memory, 'neutral'))
        self.assertEqual(engine.proactive('陪着你', [], 'neutral'), '陪着你')

    def test_decorate_is_limited_to_emotional_short_replies(self):
        engine = self.engine()
        text = engine.decorate('嗯，我在。', 'sad')
        self.assertIn('不催你', text)
        self.assertEqual(engine.decorate('巴黎是法国首都。', 'neutral'), '巴黎是法国首都。')
        self.assertEqual(engine.decorate('你要不要继续？', 'sad'), '你要不要继续？')

    def test_update_is_deterministic_and_slow(self):
        first = self.engine()
        second = self.engine()
        args = ({'trust': .8, 'curiosity': .7, 'energy': .5, 'valence': .2}, {},
                {'counters': {'turns': 10}, 'streak': 2})
        self.assertEqual(first.update(*args), second.update(*args))
        before = first.snapshot()['traits']
        first.update(*args)
        after = first.snapshot()['traits']
        self.assertTrue(all(abs(after[key] - before[key]) <= 20 for key in DIMENSIONS))

    def test_corrupt_file_and_failed_write_are_safe(self):
        self.path.write_text('{bad', encoding='utf-8')
        before = self.path.read_bytes()
        engine = self.engine()
        self.assertEqual(engine.status, 'protected')
        self.assertEqual(engine.update({'trust': .9}), engine.snapshot())
        self.assertEqual(self.path.read_bytes(), before)

        other = Path(self.directory.name) / 'write.json'
        writer = PersonalityEngine(other, clock=lambda: self.now)
        with mock.patch('personality_engine.os.replace', side_effect=OSError('disk')):
            writer.update({'trust': .9})
        self.assertFalse(other.exists())
        self.assertFalse(writer.snapshot()['status'] == 'ready')


if __name__ == '__main__':
    unittest.main()
