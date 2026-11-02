"""Grounded memory slots and deletion races; temp stores/fake local models only."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from memory_engine import MemoryEngine, _identity, _slot


class FakeEmbedding:
    def files_present(self):
        return True

    def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


class BlockingEmbedding(FakeEmbedding):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.active = 0
        self.max_active = 0

    def encode(self, texts):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if not self.release.wait(5):
                raise RuntimeError('test encode timeout')
            return super().encode(texts)
        finally:
            self.active -= 1


class ConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'memory.json'
        self.now = 1_800_000_000
        self.model_patch = mock.patch('memory_engine.EmbeddingBackend.files_present', return_value=False)
        self.model_patch.start()
        self.reranker_patch = mock.patch.dict('os.environ', {'DSH_DAFEIYU_RERANK': '0'})
        self.reranker_patch.start()

    def tearDown(self):
        self.reranker_patch.stop()
        self.model_patch.stop()
        self.directory.cleanup()

    def engine(self):
        return MemoryEngine(self.path, clock=lambda: self.now)

    @staticmethod
    def tool(count, tool='edit'):
        return {'learned': [{'id': 'tool', 'text': f'最常用的工具是 {tool}（{count} 次）'}]}

    def test_counter_replaces_slot_preserving_age_hits_and_changes_identity(self):
        engine = self.engine()
        engine.ingest_state(self.tool(1696))
        old = copy.deepcopy(engine.data['records'][0])
        self.now += 60
        self.assertEqual(engine.ingest_state(self.tool(1700)), 1)
        self.assertEqual(len(engine.data['records']), 1)
        latest = engine.data['records'][0]
        self.assertIn('1700', latest['text'])
        self.assertNotEqual(latest['id'], old['id'])
        self.assertEqual(latest['slot'], old['slot'])
        self.assertEqual(latest['createdAt'], old['createdAt'])
        self.assertEqual(latest['hits'], old['hits'])
        self.assertIn(old['id'], latest['previousIds'])
        self.assertEqual(self.engine().data, engine.data)
        engine.ingest_state(self.tool(1900, 'read'))
        self.assertEqual(len(engine.data['records']), 1)
        self.assertIn('read', engine.data['records'][0]['text'])

    def test_name_conflict_latest_one_but_distinct_likes_remain(self):
        engine = self.engine()
        engine.ingest_behavior({'userName': '旧名字', 'likes': ['咖啡', '散步']})
        oldest = next(row for row in engine.data['records'] if row['text'].startswith('称呼'))
        self.now += 10
        engine.ingest_behavior({'userName': '新名字'})
        names = [row for row in engine.data['records'] if row['text'].startswith('称呼')]
        self.assertEqual(len(names), 1)
        self.assertEqual(names[0]['text'], '称呼：新名字')
        self.assertEqual(names[0]['createdAt'], oldest['createdAt'])
        self.assertEqual(len(engine.data['records']), 3)

    def test_refresh_recall_recent_and_passive_summary_do_not_reward_access(self):
        engine = self.engine()
        engine.ingest_state(self.tool(1696))
        before = copy.deepcopy(engine.data)
        body = self.path.read_bytes()
        for _ in range(20):
            self.now += 60
            self.assertEqual(engine.ingest_state(self.tool(1696)), 0)
            engine.recall('最常用的工具')
            engine.recent()
            engine.consolidated()
        self.assertEqual(engine.data, before)
        self.assertEqual(self.path.read_bytes(), body)

    def test_clear_slot_tombstones_prevent_counter_and_name_resurrection(self):
        engine = self.engine()
        engine.ingest_behavior({'userName': '旧名字'})
        engine.ingest_state(self.tool(1696))
        engine._embedding = FakeEmbedding()
        self.assertTrue(engine.prepare_vectors())
        self.assertTrue(engine._vectors)
        self.assertTrue(engine.clear())
        self.assertEqual(engine._vectors, {})
        self.assertFalse(engine._vectors_ready)
        self.assertEqual(engine.ingest_state(self.tool(1701, 'read')), 0)
        self.assertEqual(engine.ingest_behavior({'userName': '新名字'}), 0)
        reloaded = self.engine()
        self.assertEqual(reloaded.ingest_state(self.tool(2000)), 0)
        self.assertEqual(reloaded.ingest_behavior({'userName': '另一个名字'}), 0)
        self.assertEqual(reloaded.consolidated(), [])
        self.assertTrue(reloaded.data['blockedSlots'])

    def test_clear_preferences_purges_vectors_but_preserves_habits(self):
        engine = self.engine()
        engine.ingest_behavior({'userName': '旧名字', 'likes': ['咖啡']})
        engine.ingest_state(self.tool(12))
        engine._embedding = FakeEmbedding()
        engine.prepare_vectors()
        self.assertTrue(engine.clear_preferences())
        self.assertEqual(engine._vectors, {})
        self.assertEqual(engine.ingest_behavior({'userName': '新名字', 'likes': ['咖啡']}), 0)
        self.assertEqual([row['kind'] for row in engine.data['records']], ['habit'])
        self.assertEqual(self.engine().ingest_behavior({'userName': '改名字'}), 0)

    def test_v1_migration_coalesces_existing_variants_without_writing_on_load(self):
        def row(kind, text, stamp, hits):
            return {'kind': kind, 'text': text, 'source': 'old_profile', 'createdAt': stamp,
                    'lastSeen': stamp, 'hits': hits, 'importance': 70}
        rows = [row('preference', '称呼：甲', 1, 3), row('preference', '称呼：乙', 10, 2),
                row('habit', '最常用的工具是 edit（1696 次）', 2, 6),
                row('habit', '最常用的工具是 edit（1697 次）', 11, 4),
                row('diary', '已经完成的本地记录', 5, 1)]
        self.path.write_text(json.dumps({'version': 1, 'revision': 7, 'records': rows}), encoding='utf-8')
        original = self.path.read_bytes()
        engine = self.engine()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(engine.data['revision'], 7)
        self.assertEqual(len(engine.data['records']), 3)
        habit = next(row for row in engine.data['records'] if row['kind'] == 'habit')
        self.assertIn('1697', habit['text'])
        self.assertEqual(habit['createdAt'], 2)
        self.assertEqual(habit['hits'], 6)
        self.assertEqual(habit['id'], _identity('habit', habit['text']))
        self.assertEqual(engine.data['blockedSlots'], [])
        self.assertEqual([row['text'] for row in engine.data['records'] if row['kind'] == 'preference'], ['称呼：乙'])

    def test_summary_is_bounded_grounded_referenced_and_returns_copies(self):
        engine = self.engine()
        engine.ingest_behavior({'userName': '小林'})
        engine.ingest_state(self.tool(100))
        engine.ingest_state({'diary': [{'text': '完成了一次本地回顾'}]})
        engine.ingest_knowledge([{'topic': '法国', 'text': '首都是巴黎'}], 1)
        summaries = engine.consolidated(limit=99)
        self.assertEqual({row['group'] for row in summaries}, {'preferences', 'habits', 'episodes'})
        self.assertEqual(len(summaries), 3)
        for summary in summaries:
            stored = next(row for row in engine.data['records'] if row['id'] == summary['id'])
            self.assertEqual(summary['text'], stored['text'])
            self.assertEqual(summary['references'], [{'id': stored['id'], 'source': stored['source']}])
        summaries[0]['text'] = 'invented'
        summaries[0]['references'][0]['source'] = 'invented'
        self.assertNotIn('invented', json.dumps(engine.consolidated()))
        self.assertEqual(engine.consolidated(0), [])
        self.assertEqual(len(engine.consolidated(1)), 1)
        hits = engine.recall('最常用的工具')
        self.assertTrue(hits)
        self.assertIn('id', hits[0])
        self.assertIn('id', engine.recent()[0])
        self.assertEqual(engine.recent()[0]['score'], 0)
        self.assertEqual(engine.recent()[0]['retrieval'], 'recent')
        self.assertIn('id', engine.snapshot()['recent'][0])

    def test_unrelated_lexical_shortlist_does_not_fabricate_zero_relevance_hits(self):
        engine = self.engine()
        engine.ingest_state({'diary': [{'text': '甲乙丙丁'}]})
        self.assertEqual(engine.recall('xyzabcdefgh'), [])

    def test_delete_during_background_warmup_cannot_repopulate_cache(self):
        for preferences_only in (False, True):
            engine = self.engine()
            engine.ingest_behavior({'userName': '旧名字'})
            engine.ingest_state(self.tool(100))
            model = BlockingEmbedding()
            engine._embedding = model
            result = []
            thread = threading.Thread(target=lambda: result.append(engine.prepare_vectors(batch=1)))
            thread.start()
            try:
                self.assertTrue(model.started.wait(2))
                self.assertTrue(engine.clear_preferences() if preferences_only else engine.clear())
            finally:
                model.release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, [False])
            self.assertEqual(engine._vectors, {})
            self.assertFalse(engine._vectors_ready)
            # Separate temp path prevents tombstones in one case influencing another.
            self.path = Path(self.directory.name) / 'next.json'

    def test_background_encodes_serialize_lazy_session_access(self):
        engine = self.engine()
        engine.ingest_state(self.tool(100))
        model = BlockingEmbedding()
        engine._embedding = model
        results = []
        first = threading.Thread(target=lambda: results.append(engine.prepare_vectors()))
        second = threading.Thread(target=lambda: results.append(engine.prepare_vectors()))
        first.start()
        try:
            self.assertTrue(model.started.wait(2))
            second.start()
        finally:
            model.release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(model.max_active, 1)
        self.assertTrue(engine._vectors_ready)
        self.assertEqual(len(engine._vectors), 1)

    def test_mutation_during_warmup_rejects_obsolete_text_vectors(self):
        engine = self.engine()
        engine.ingest_state(self.tool(100))
        old_id = engine.data['records'][0]['id']
        model = BlockingEmbedding()
        engine._embedding = model
        results = []
        thread = threading.Thread(target=lambda: results.append(engine.prepare_vectors()))
        thread.start()
        try:
            self.assertTrue(model.started.wait(2))
            engine.ingest_state(self.tool(101))
        finally:
            model.release.set()
            thread.join(3)
        self.assertEqual(results, [False])
        self.assertNotIn(old_id, engine._vectors)
        self.assertFalse(engine._vectors_ready)
        engine.prepare_vectors()
        self.assertEqual(set(engine._vectors), {engine.data['records'][0]['id']})

    def test_failed_save_rolls_back_slot_update_and_tombstones(self):
        engine = self.engine()
        engine.ingest_state(self.tool(100))
        before = copy.deepcopy(engine.data)
        body = self.path.read_bytes()
        with mock.patch('memory_engine.os.replace', side_effect=OSError('disk')):
            self.assertEqual(engine.ingest_state(self.tool(101)), 0)
        self.assertEqual(engine.data, before)
        self.assertEqual(self.path.read_bytes(), body)
        with mock.patch('memory_engine.os.replace', side_effect=OSError('disk')):
            self.assertFalse(engine.clear())
        self.assertEqual(engine.data, before)
        self.assertEqual(self.path.read_bytes(), body)

    def test_corrupt_and_future_files_are_protected_unchanged(self):
        for body in (b'{bad', b'{"version":9,"records":[]}', b'{"version":1,"records":{}}'):
            self.path.write_bytes(body)
            engine = self.engine()
            self.assertEqual(engine.status, 'protected')
            self.assertEqual(engine.consolidated(), [])
            self.assertEqual(engine.ingest_state(self.tool(12)), 0)
            self.assertFalse(engine.clear())
            self.assertEqual(self.path.read_bytes(), body)

    def test_numeric_slots_do_not_merge_unrelated_habits_or_film_titles(self):
        self.assertEqual(_slot('habit', '遇到过 10 次工具报错，自己爬起来了 8 次'),
                         _slot('habit', '遇到过 11 次工具报错，自己爬起来了 9 次'))
        self.assertNotEqual(_slot('habit', '喜欢007电影'), _slot('habit', '喜欢008电影'))
        self.assertNotEqual(_slot('habit', '使用edit 10次'), _slot('habit', '使用read 10次'))


if __name__ == '__main__':
    unittest.main()
