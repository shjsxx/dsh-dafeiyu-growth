"""Personality engine: bounded traits, tone, initiative and memory influence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from personality_engine import DIMENSIONS, VERSION, PersonalityEngine, UPDATE_SECONDS, REPAIR_SECONDS, REST_SECONDS


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
        self.assertEqual(document['version'], VERSION)

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

    def test_refreshes_never_adapt_traits_even_after_elapsed_time(self):
        engine = self.engine()
        state = {'counters': {'turns': 12}}
        engine.update(state=state)
        before = engine.data.copy()
        for _ in range(50):
            engine.update({'trust': 1.0}, state=state)
            engine.snapshot()
        self.assertEqual(engine.data, before)
        self.now += UPDATE_SECONDS * 10
        engine.update({'trust': 1.0}, state=state)
        self.assertEqual(engine.data, before)
        engine.update({'trust': 1.0}, state={'counters': {'turns': 13}})
        self.assertGreater(engine.data['revision'], before['revision'])
        self.assertEqual(engine.data['interactions'], before['interactions'] + 1)

    def test_three_new_turns_allow_slow_adaptation(self):
        engine = self.engine()
        engine.update(state={'counters': {'turns': 1}})
        before = engine.snapshot()
        engine.update(state={'counters': {'turns': 2}})
        self.assertEqual(engine.snapshot(), before)
        engine.update(state={'counters': {'turns': 4}})
        self.assertEqual(engine.data['interactions'], 2)
        for dimension in DIMENSIONS:
            self.assertLess(abs(engine.data['traits'][dimension] - before['traits'][dimension]), 10)

    def test_v1_load_is_read_only_and_first_save_preserves_original(self):
        original = {'version': 1, 'traits': {key: 64 for key in DIMENSIONS},
                    'revision': 5, 'interactions': 2, 'updatedAt': self.now - 9999}
        body = json.dumps(original).encode('utf-8')
        self.path.write_bytes(body)
        engine = self.engine()
        self.assertEqual(engine.data['traits']['warmth'], 64)
        self.assertEqual(self.path.read_bytes(), body)
        self.assertFalse(self.path.with_name('personality.json.v1.bak').exists())
        engine.observe('你答非所问')
        self.assertEqual(self.path.with_name('personality.json.v1.bak').read_bytes(), body)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['version'], VERSION)
        self.assertEqual(self.engine().data['traits'], engine.data['traits'])

    def test_migration_failure_rolls_back_and_never_overwrites_backup(self):
        original = {'version': 1, 'traits': {key: 50 for key in DIMENSIONS}}
        body = json.dumps(original).encode('utf-8')
        self.path.write_bytes(body)
        engine = self.engine()
        before = json.loads(json.dumps(engine.data))
        with mock.patch('personality_engine.os.replace', side_effect=OSError('disk')):
            engine.update({'trust': 1})
        self.assertEqual(engine.data, before)
        self.assertEqual(self.path.read_bytes(), body)
        self.assertEqual(self.path.with_name('personality.json.v1.bak').read_bytes(), body)
        engine.update({'trust': 1})
        self.assertEqual(engine.status, 'ready')
        self.assertEqual(self.path.with_name('personality.json.v1.bak').read_bytes(), body)

    def test_conflicting_v1_backup_blocks_save_without_clobber(self):
        original = json.dumps({'version': 1, 'traits': {key: 50 for key in DIMENSIONS}}).encode('utf-8')
        self.path.write_bytes(original)
        backup = self.path.with_name('personality.json.v1.bak')
        backup.write_bytes(b'earlier owner backup')
        engine = self.engine()
        before = json.loads(json.dumps(engine.data))
        engine.update({'trust': 1})
        self.assertEqual(engine.status, 'error')
        self.assertEqual(engine.data, before)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(backup.read_bytes(), b'earlier owner backup')

    def test_complaint_acknowledgment_and_cooldown_govern_proactive(self):
        engine = self.engine()
        hit = [{'text': '夜里散步'}]
        normal = engine.initiative('joy', True)
        acknowledgment = engine.observe('你答非所问，别敷衍我')
        self.assertIn('抱歉', acknowledgment)
        self.assertEqual(engine.proactive('来聊聊', hit, 'joy'), '')
        self.assertEqual(engine.initiative('joy', True), 0)
        self.assertEqual(engine.decorate('抱歉，刚才没接住。', 'joy'), '抱歉，刚才没接住。')
        self.assertEqual(engine.personality_policy()['initiative'], 'paused')
        self.assertEqual(self.engine().proactive('来聊聊', hit, 'joy'), '')
        engine.observe('我昨天去了公园', intent='share')
        self.assertEqual(engine.proactive('来聊聊', hit), '')
        self.now += REPAIR_SECONDS + 1
        self.assertLess(engine.initiative('joy', True), normal)
        self.assertNotIn('夜里散步', engine.proactive('轻轻陪着', hit, 'joy'))
        self.now += REPAIR_SECONDS
        self.assertIn('夜里散步', engine.proactive('轻轻陪着', hit, 'joy'))

    def test_rest_refusal_and_save_failure_still_protect_autonomy(self):
        for text in ('我要休息', '别主动打扰我'):
            engine = self.engine()
            engine.update()
            before = json.loads(json.dumps(engine.data))
            with mock.patch('personality_engine.os.replace', side_effect=OSError('disk')):
                engine.observe(text)
            self.assertEqual(engine.data, before)
            self.assertEqual(engine.proactive('hello'), '')
            self.now += REST_SECONDS + 1
            self.assertEqual(engine.proactive('hello'), 'hello')

    def test_policy_and_persistence_never_contain_text_or_unknown_fields(self):
        engine = self.engine()
        secret = 'token=SECRET_PRIVATE_CHAT_HISTORY'
        engine.update({'token': secret}, {'text': secret}, {'history': secret})
        engine.data['history'] = secret
        engine.data['goals'] = [secret]
        engine.data['traits']['secret'] = secret
        engine.observe('答非所问 ' + secret)
        body = self.path.read_text(encoding='utf-8')
        self.assertNotIn(secret, body)
        self.assertNotIn('history', body)
        policy = engine.personality_policy(secret)
        self.assertNotIn(secret, json.dumps(policy))
        self.assertEqual(set(policy['values']), {'respect_autonomy', 'honesty', 'low_interrupt'})
        policy['values'].append(secret)
        self.assertNotIn(secret, json.dumps(engine.personality_policy()))

    def test_future_duplicate_and_nonfinite_documents_are_protected(self):
        for text in ('{"version":99}', '{"version":1,"version":2}',
                     json.dumps({'version': 2, 'traits': {key: float('nan') for key in DIMENSIONS}})):
            self.path.write_text(text, encoding='utf-8')
            engine = self.engine()
            self.assertEqual(engine.status, 'protected')
            engine.observe('别打扰我')
            self.assertEqual(engine.proactive('hello'), '')
            self.assertEqual(self.path.read_text(encoding='utf-8'), text)


if __name__ == '__main__':
    unittest.main()
