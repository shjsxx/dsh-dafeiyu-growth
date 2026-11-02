"""Long-term continuity anchors: restart/day stability without text."""
import json
from pathlib import Path
import tempfile
import unittest

from continuity import ContinuityStore


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'continuity.json'
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def store(self):
        return ContinuityStore(self.path, clock=lambda: self.now)

    @staticmethod
    def personality(revision=2):
        return {'revision': revision, 'traits': {'warmth': 62, 'curiosity': 58,
                'playfulness': 41, 'steadiness': 60, 'expressiveness': 55}}

    @staticmethod
    def memory(revision=3, total=9):
        return {'revision': revision, 'total': total,
                'counts': {'preference': 1, 'habit': 2, 'diary': 1, 'public': 4, 'culture': 1}}

    def test_startup_and_daily_checkpoint_are_stable_across_reload(self):
        store = self.store()
        self.assertTrue(store.record_startup())
        self.assertTrue(store.checkpoint(self.personality(), self.memory()))
        view = store.verify(self.personality(), self.memory())
        self.assertTrue(view['stable'])
        self.assertEqual(view['days'], 1)
        self.assertEqual(view['startups'], 1)
        reloaded = self.store()
        self.assertTrue(reloaded.verify(self.personality(), self.memory())['stable'])

    def test_unchanged_state_does_not_rewrite_or_add_revision(self):
        store = self.store()
        store.checkpoint(self.personality(), self.memory())
        first = self.path.stat().st_mtime_ns
        revision = store.verify()['revision']
        store.checkpoint(self.personality(), self.memory())
        self.assertEqual(self.path.stat().st_mtime_ns, first)
        self.assertEqual(store.verify()['revision'], revision)

    def test_new_day_and_drift_are_reported_without_text(self):
        store = self.store()
        store.checkpoint(self.personality(), self.memory())
        store._clock = lambda: self.now + 86400
        store.checkpoint(self.personality(3), self.memory(4, 10))
        self.assertEqual(store.verify()['days'], 2)
        self.assertFalse(store.verify(self.personality(9), self.memory(9, 99))['stable'])
        body = self.path.read_text(encoding='utf-8')
        for word in ('喜欢', '夜里', '聊天'):
            self.assertNotIn(word, body)

    def test_corrupt_file_is_preserved_and_protected(self):
        self.path.write_text('{bad', encoding='utf-8')
        before = self.path.read_bytes()
        store = self.store()
        self.assertEqual(store.status, 'protected')
        self.assertFalse(store.record_startup())
        self.assertFalse(store.checkpoint(self.personality(), self.memory()))
        self.assertEqual(self.path.read_bytes(), before)

    def test_anchors_are_capped(self):
        store = self.store()
        for day in range(220):
            store._clock = lambda d=day: self.now + d * 86400
            store.checkpoint(self.personality(day), self.memory(day))
        document = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertLessEqual(len(document['anchors']), 180)
        self.assertLessEqual(store.verify()['days'], 180)


if __name__ == '__main__':
    unittest.main()
