"""Bounded local/public memory engine: dedupe, recall, privacy deletion."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from memory_engine import MemoryEngine, MAX_RECORDS


class MemoryEngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'memory.json'
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def engine(self):
        return MemoryEngine(self.path, clock=lambda: self.now)

    @staticmethod
    def state():
        return {'learned': [{'id': 'a', 'text': '喜欢在夜里散步'},
                            {'id': 'b', 'text': '习惯周末整理桌面'}],
                'diary': [{'text': '今天完成了一次本地回顾'},
                          {'text': '第一次学会自己爬起来'}]}

    def test_local_ingest_is_bounded_deduplicated_and_persisted(self):
        engine = self.engine()
        self.assertEqual(engine.ingest_state(self.state()), 1)
        snapshot = engine.snapshot()
        self.assertEqual(snapshot['status'], 'ready')
        self.assertEqual(snapshot['counts']['habit'], 2)
        self.assertEqual(snapshot['counts']['diary'], 2)
        self.assertEqual(engine.ingest_state(self.state()), 0)
        self.assertEqual(engine.snapshot()['total'], 4)
        body = self.path.read_text(encoding='utf-8')
        self.assertIn('夜里散步', body)

    def test_public_ingest_uses_revision_and_never_fetches(self):
        engine = self.engine()
        records = [{'topic': '法国', 'text': '首都是巴黎', 'source': 'countries'},
                   {'topic': '单位', 'text': '米是长度单位', 'source': 'cldr_units'}]
        self.assertEqual(engine.ingest_knowledge(records, 3), 1)
        self.assertEqual(engine.snapshot()['counts']['public'], 2)
        self.assertEqual(engine.ingest_knowledge(records, 3), 0)
        self.assertEqual(engine.snapshot()['revision'], 1)

    def test_recall_ranks_relevant_and_handles_short_queries(self):
        engine = self.engine()
        engine.ingest_state(self.state())
        engine.ingest_knowledge([{'topic': '法国', 'text': '首都是巴黎', 'source': 'countries'}], 1)
        hits = engine.recall('巴黎')
        self.assertTrue(hits)
        self.assertIn('巴黎', hits[0]['text'])
        self.assertEqual(engine.recall(''), [])

    def test_clear_removes_all_memories_and_is_persisted(self):
        engine = self.engine()
        engine.ingest_state(self.state())
        self.assertTrue(engine.clear())
        self.assertEqual(engine.snapshot()['total'], 0)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['records'], [])

    def test_corrupt_unknown_and_write_failure_are_safe(self):
        self.path.write_text('{bad', encoding='utf-8')
        before = self.path.read_bytes()
        engine = self.engine()
        self.assertEqual(engine.status, 'protected')
        self.assertEqual(engine.ingest_state(self.state()), 0)
        self.assertEqual(engine.recall('回顾'), [])
        self.assertEqual(self.path.read_bytes(), before)
        self.path.write_text(json.dumps({'version': 99, 'records': []}), encoding='utf-8')
        self.assertEqual(self.engine().status, 'protected')

    def test_write_failure_does_not_claim_memories(self):
        engine = self.engine()
        with mock.patch('memory_engine.os.replace', side_effect=OSError('disk')):
            self.assertEqual(engine.ingest_state(self.state()), 0)
        self.assertEqual(engine.snapshot()['total'], 0)
        self.assertFalse(self.path.exists())

    def test_records_are_capped(self):
        rows = [{'kind': 'diary', 'text': f'第{i}条回顾记录', 'source': 'local_diary',
                 'importance': 50, 'createdAt': 1, 'lastSeen': i, 'hits': 1}
                for i in range(MAX_RECORDS + 200)]
        self.assertLessEqual(len(MemoryEngine._prune(rows)), MAX_RECORDS)

    def test_malformed_inputs_are_ignored(self):
        engine = self.engine()
        self.assertEqual(engine.ingest_state(None), 0)
        self.assertEqual(engine.ingest_knowledge(None, 1), 0)
        self.assertEqual(engine.snapshot()['total'], 0)


if __name__ == '__main__':
    unittest.main()
