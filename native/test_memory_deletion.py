"""Deletion tombstones and preference clearing."""
from pathlib import Path
import tempfile
import unittest

from memory_engine import MemoryEngine


class MemoryDeletionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = MemoryEngine(Path(self.directory.name) / 'memory.json',
                                   clock=lambda: 1_800_000_000)

    def tearDown(self):
        self.directory.cleanup()

    def test_cleared_rows_are_not_reingested(self):
        state = {'diary': [{'text': '今天完成了一次本地回顾'}]}
        self.engine.ingest_state(state)
        self.assertEqual(self.engine.ingest_state(state), 0)
        self.engine.clear()
        self.assertEqual(self.engine.ingest_state(state), 0)
        self.assertEqual(self.engine.snapshot()['total'], 0)

    def test_clear_preferences_only_removes_name_and_likes(self):
        self.engine.ingest_behavior({'userName': '小林', 'likes': ['夜里散步']})
        self.engine.ingest_state({'diary': [{'text': '今天完成了一次本地回顾'}]})
        self.assertTrue(self.engine.clear_preferences())
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot['counts']['preference'], 0)
        self.assertEqual(snapshot['counts']['diary'], 1)
        self.assertEqual(self.engine.ingest_behavior({'userName': '小林'}), 0)


if __name__ == '__main__':
    unittest.main()
