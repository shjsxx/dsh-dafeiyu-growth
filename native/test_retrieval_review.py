"""Explicit retrieval labels use temporary paths; evaluation never trains."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from retrieval_review import LIMIT, MAX_BYTES, RetrievalReviewStore, evaluate_labels, partition


def label(index=0, *, group=None, relevant=None, no_answer=False):
    return {'query': f'问题{index}', 'candidates': ['a', 'b'],
            'relevant': ([] if no_answer else ['a']) if relevant is None else relevant,
            'no_answer': no_answer, 'group': group or f'group-{index // 2}', 'at': 0}


class Engine:
    def __init__(self, outputs=None):
        self.data = {'records': [{'id': 'a'}, {'id': 'b'}]}
        self.status = 'ready'
        self.outputs = outputs or {}
        self.calls = []

    def recall(self, query, limit=5):
        self.calls.append((query, limit))
        return self.outputs.get(query, [{'id': 'a'}])


class RetrievalReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'labels.json'
        self.store = RetrievalReviewStore(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def add(self, **overrides):
        values = label()
        values.pop('at')
        values.update(overrides)
        return self.store.add(**values)

    def test_only_literal_true_consent_saves(self):
        for consent in (False, None, 1, 'yes', [], {}):
            self.assertFalse(self.add(consent=consent))
        self.assertFalse(self.path.exists())
        self.assertTrue(self.add(consent=True))
        restarted = RetrievalReviewStore(self.path)
        self.assertEqual(restarted.status, 'ready')
        self.assertEqual(restarted.rows[0]['relevant'], ['a'])
        self.assertEqual(set(restarted.rows[0]), {'query', 'candidates', 'relevant', 'no_answer', 'group', 'at'})

    def test_no_answer_and_relevance_are_exclusive(self):
        for overrides in ({'relevant': []}, {'no_answer': True}, {'no_answer': 1},
                          {'relevant': ['missing']}, {'group': ''}):
            self.assertFalse(self.add(consent=True, **overrides))
        self.assertEqual(self.store.status, 'new')
        self.assertTrue(self.add(consent=True, relevant=[], no_answer=True))

    def test_ids_are_not_silently_dropped_truncated_or_deduplicated(self):
        for ids in (None, 'a', ['a', None], ['a', 'a'], ['x' * 97], [' a'],
                    ['a\x00'], [str(i) for i in range(41)]):
            with self.subTest(ids=repr(ids)):
                self.assertFalse(self.add(consent=True, candidates=ids))
        self.assertFalse(self.path.exists())

    def test_corrupted_or_unknown_schema_is_protected_and_untouched(self):
        valid = label()
        cases = ['{broken', json.dumps({'version': True, 'rows': []}),
                 json.dumps({'version': 1, 'rows': [dict(valid, unknown_label=True)]}),
                 json.dumps({'version': 1, 'rows': [dict(valid, no_answer='false')]}),
                 json.dumps({'version': 1, 'rows': [dict(valid, candidates=['a', None])]}),
                 json.dumps({'version': 1, 'rows': [dict(valid, relevant=['missing'])]}),
                 json.dumps({'version': 1, 'rows': [], 'unknown': True}),
                 '{"version":1,"rows":[],"rows":[]}',
                 json.dumps({'version': 1, 'rows': [valid] * (LIMIT + 1)})]
        for body in cases:
            with self.subTest(body=body[:70]):
                self.path.write_text(body, encoding='utf-8')
                store = RetrievalReviewStore(self.path)
                self.assertEqual(store.status, 'protected')
                self.assertEqual(store.rows, [])
                self.assertFalse(store.add('问题', ['a'], ['a'], group='g', consent=True))
                self.assertFalse(store.clear())
                self.assertEqual(self.path.read_text(encoding='utf-8'), body)

    def test_oversized_file_is_protected(self):
        self.path.write_bytes(b'x' * (MAX_BYTES + 1))
        self.assertEqual(RetrievalReviewStore(self.path).status, 'protected')

    def test_atomic_failed_add_and_clear_preserve_disk_and_memory(self):
        self.assertTrue(self.add(consent=True))
        before, body = copy.deepcopy(self.store.rows), self.path.read_bytes()
        with mock.patch('retrieval_review.os.replace', side_effect=OSError('private-data')):
            self.assertFalse(self.add(consent=True, query='新问题'))
            self.assertEqual(self.store.rows, before)
            self.assertFalse(self.store.clear())
        self.assertEqual(self.store.rows, before)
        self.assertEqual(self.path.read_bytes(), body)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_clear_is_explicit_atomic_and_survives_reload(self):
        self.assertTrue(self.add(consent=True))
        self.assertTrue(self.store.clear())
        self.assertEqual(RetrievalReviewStore(self.path).rows, [])
        self.assertEqual(self.store.status, 'ready')

    def test_external_changes_are_not_overwritten(self):
        self.assertTrue(self.add(consent=True))
        other = RetrievalReviewStore(self.path)
        self.assertTrue(other.add('其他问题', ['a'], ['a'], group='other', consent=True))
        body = self.path.read_bytes()
        self.assertFalse(self.store.clear())
        self.assertEqual(self.store.status, 'protected')
        self.assertEqual(self.path.read_bytes(), body)

    def test_candidate_text_and_transcript_fields_are_rejected(self):
        bad = dict(label(), transcript='private transcript')
        self.path.write_text(json.dumps({'version': 1, 'rows': [bad]}), encoding='utf-8')
        self.assertEqual(RetrievalReviewStore(self.path).status, 'protected')


class RetrievalReviewEvaluationTests(unittest.TestCase):
    def test_fixed_hash_partitions_are_group_disjoint(self):
        rows = [label(i) for i in range(100)]
        parts = {name: partition(rows, name) for name in ('train', 'validation', 'test')}
        seen = set()
        for name, part in parts.items():
            groups = {row['group'] for row in part}
            self.assertFalse(seen & groups)
            seen.update(groups)
            self.assertEqual(part, list(reversed(partition(list(reversed(rows)), name))))
            for row in part:
                bucket = int(hashlib.sha256(row['group'].encode()).hexdigest()[:8], 16) % 10
                self.assertEqual(name, 'train' if bucket < 6 else 'validation' if bucket < 8 else 'test')
        self.assertEqual(sum(map(len, parts.values())), len(rows))

    def test_insufficient_rows_or_groups_never_emit_scores_or_call_recall(self):
        for rows in ([label(i) for i in range(4)], [label(i, group='one') for i in range(10)]):
            engine = Engine()
            result = evaluate_labels(engine, rows, split='all')
            self.assertEqual(result['status'], 'insufficient_data')
            self.assertFalse(result['training_performed'])
            self.assertNotIn('hit1', result)
            self.assertNotIn('end_to_end_accuracy', result)
            self.assertEqual(engine.calls, [])

    def test_actual_recall_ranks_no_answer_and_end_to_end_metrics(self):
        rows = [label(i, no_answer=(i >= 4)) for i in range(6)]
        engine = Engine({'问题0': [{'id': 'a'}], '问题1': [{'id': 'b'}, {'id': 'a'}],
                         '问题2': [], '问题3': [{'id': 'b'}], '问题4': [], '问题5': [{'id': 'b'}]})
        result = evaluate_labels(engine, rows, split='all')
        self.assertEqual(result['status'], 'evaluated')
        self.assertEqual(engine.calls, [(f'问题{i}', 5) for i in range(6)])
        self.assertEqual(result['answerable'], 4)
        self.assertEqual(result['no_answer'], 2)
        self.assertEqual(result['hit1'], .25)
        self.assertEqual(result['mrr'], .375)
        self.assertEqual(result['retrieved_relevant_at_k'], .5)
        self.assertEqual(result['no_answer_accuracy'], .5)
        self.assertEqual(result['abstention_precision'], .5)
        self.assertEqual(result['end_to_end_accuracy'], .3333)
        self.assertGreater(result['ndcg5'], .25)
        self.assertLess(result['ndcg5'], .5)
        self.assertFalse(result['training_performed'])

    def test_multirelevance_recall_and_k_name(self):
        rows = [label(i, relevant=['a', 'b']) for i in range(6)]
        result = evaluate_labels(Engine(), rows, split='all', k=1)
        self.assertEqual(result['recall_at_k'], .5)
        self.assertEqual(result['ndcg_at_k'], 1)
        self.assertEqual(result['k'], 1)
        self.assertNotIn('ndcg5', result)

    def test_stale_relevant_rows_reduce_eligible_data_and_group_threshold(self):
        rows = [label(i) for i in range(6)]
        rows[0].update(candidates=['deleted'], relevant=['deleted'])
        rows[1].update(candidates=['deleted'], relevant=['deleted'])
        engine = Engine()
        result = evaluate_labels(engine, rows, split='all')
        self.assertEqual(result['status'], 'insufficient_data')
        self.assertEqual(result['stale_labels'], 2)
        self.assertEqual(result['stale_candidate_ids'], 1)
        self.assertEqual(result['evaluated_rows'], 4)
        self.assertNotIn('mrr', result)
        self.assertEqual(engine.calls, [])

    def test_stale_irrelevant_candidate_is_reported_not_silently_lost(self):
        rows = [label(i) for i in range(6)]
        rows[0]['candidates'].append('deleted')
        result = evaluate_labels(Engine(), rows, split='all')
        self.assertEqual(result['status'], 'evaluated')
        self.assertEqual(result['stale_labels'], 0)
        self.assertEqual(result['stale_candidate_ids'], 1)

    def test_invalid_labels_and_unknown_split_are_not_silent(self):
        rows = [label(i) for i in range(6)]
        rows[0]['relevant'] = ['unknown']
        self.assertEqual(evaluate_labels(Engine(), rows, split='all')['status'], 'invalid_labels')
        with self.assertRaises(ValueError):
            partition([], 'typo')
        self.assertEqual(evaluate_labels(Engine(), [], split='typo')['status'], 'invalid_labels')

    def test_minimum_cannot_be_bypassed_or_k_misreported(self):
        rows = [label(i) for i in range(6)]
        for options in ({'minimum_groups': 1}, {'k': 0}, {'k': 21}, {'k': True}):
            self.assertEqual(evaluate_labels(Engine(), rows, split='all', **options)['status'], 'invalid_options')

    def test_missing_unknown_or_duplicate_hit_ids_fail_closed(self):
        rows = [label(i) for i in range(6)]
        for hits in ([{'text': 'missing ID'}], [{'id': 'unknown'}], [{'id': 'a'}, {'id': 'a'}], 'bad'):
            result = evaluate_labels(Engine({'问题0': hits}), rows, split='all')
            self.assertEqual(result['status'], 'evaluation_error')
            self.assertNotIn('hit1', result)
            self.assertNotIn('missing ID', json.dumps(result))

    def test_engine_exceptions_and_protected_state_have_no_fake_scores(self):
        rows = [label(i) for i in range(6)]
        engine = Engine()
        with mock.patch.object(engine, 'recall', side_effect=RuntimeError('private query')):
            result = evaluate_labels(engine, rows, split='all')
        self.assertEqual(result['status'], 'evaluation_error')
        self.assertNotIn('private query', json.dumps(result))
        engine.status = 'protected'
        self.assertEqual(evaluate_labels(engine, rows, split='all')['status'], 'engine_unavailable')

    def test_real_memory_engine_recall_is_called_without_training(self):
        from memory_engine import MemoryEngine
        with tempfile.TemporaryDirectory() as directory:
            engine = MemoryEngine(Path(directory) / 'memory.json')
            engine._remember('preference', '喜欢：喝茶', 'test', 100)
            identity = engine.data['records'][0]['id']
            rows = [dict(label(i), candidates=[identity], relevant=[identity], query='喜欢喝茶') for i in range(6)]
            before = copy.deepcopy(engine.data)
            with mock.patch.object(engine, '_embedding_scores', return_value={}), \
                    mock.patch.object(engine, 'recall', wraps=engine.recall) as recall:
                result = evaluate_labels(engine, rows, split='all')
            self.assertEqual(result['status'], 'evaluated')
            self.assertEqual(result['hit1'], 1)
            self.assertEqual(recall.call_count, 6)
            self.assertEqual(engine.data, before)
            self.assertFalse(engine.path.exists())


if __name__ == '__main__':
    unittest.main()
