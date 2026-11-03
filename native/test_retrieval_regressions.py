"""Offline regressions: all fixtures/models live in temporary directories."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import embedding_benchmark as benchmark
from retrieval_review import evaluate_labels


class FakeBackend:
    def __init__(self, directory=None):
        self.directory = Path(directory or 'unused')
        self.status = 'ready'

    def files_present(self):
        return True

    def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


class RetrievalRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, records=None, queries=None):
        path = self.root / 'fixture.json'
        path.write_text(json.dumps({'records': records or [
            {'id': 'fixture-a', 'kind': 'preference', 'text': 'same text', 'importance': 50},
            {'id': 'fixture-b', 'kind': 'habit', 'text': 'same text', 'importance': 50}],
            'queries': queries or [{'text': 'some query', 'expected': 'fixture-b'}]}), encoding='utf-8')
        return path

    def test_production_uses_stable_ids_not_text_and_disables_owner_reranker(self):
        from memory_engine import MemoryEngine, _identity
        seen = []
        def recall(engine, query, limit=5, kinds=None):
            seen.append(engine)
            self.assertEqual(engine._embedding.directory, self.root)
            self.assertTrue(engine._reranker_checked)
            self.assertIsNone(engine._reranker)
            self.assertEqual(engine.data['records'][1]['id'], _identity('habit', 'same text'))
            return [{'id': _identity('habit', 'same text'), 'text': 'same text'}]
        with mock.patch.object(benchmark, 'EmbeddingBackend', FakeBackend), \
                mock.patch.object(MemoryEngine, 'recall', recall), \
                mock.patch.object(benchmark.Reranker, 'load', side_effect=AssertionError('owner model read')):
            result = benchmark.evaluate_production(self.fixture(), model_dir=self.root)
        self.assertTrue(result['ok'])
        self.assertEqual(result['hit1'], 1)
        self.assertEqual(len(seen), 1)

    def test_production_requests_supported_top_k_and_misses_score_zero(self):
        from memory_engine import MemoryEngine
        records = [{'id': f'r{i}', 'kind': 'public', 'text': f'record {i}'} for i in range(25)]
        with mock.patch.object(benchmark, 'EmbeddingBackend', FakeBackend), \
                mock.patch.object(MemoryEngine, 'recall', return_value=[]) as recall:
            result = benchmark.evaluate_production(self.fixture(records, [{'text': 'missing', 'expected': 'r24'}]), self.root)
        self.assertEqual(recall.call_args.kwargs['limit'], 20)
        self.assertEqual(result['mrr'], 0)
        self.assertEqual(result['ranks'], [None])
        self.assertEqual(result['k'], 20)

    def test_production_no_answer_is_not_dropped_or_given_rank_credit(self):
        from memory_engine import MemoryEngine
        path = self.fixture(queries=[{'text': 'no answer', 'expected': None, 'no_answer': True}])
        with mock.patch.object(benchmark, 'EmbeddingBackend', FakeBackend), \
                mock.patch.object(MemoryEngine, 'recall', return_value=[]):
            result = benchmark.evaluate_production(path, self.root)
        self.assertEqual(result['queries'], 1)
        self.assertEqual(result['answerable'], 0)
        self.assertIsNone(result['mrr'])
        self.assertEqual(result['no_answer_accuracy'], 1)
        self.assertEqual(result['end_to_end_accuracy'], 1)

    def test_production_model_unavailable_is_distinct_from_failure(self):
        with mock.patch.object(benchmark, 'EmbeddingBackend', FakeBackend), \
                mock.patch.object(FakeBackend, 'files_present', return_value=False):
            result = benchmark.evaluate_production(self.fixture(), self.root)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('mrr', result)
        with mock.patch.object(benchmark, 'EmbeddingBackend', FakeBackend), \
                mock.patch.object(FakeBackend, 'encode', return_value=[]):
            result = benchmark.evaluate_production(self.fixture(), self.root)
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('mrr', result)

    def test_rank_metrics_missing_expected_scores_zero(self):
        result = benchmark._rank_metrics([{'id': 'a'}], [[1.0]], [('q', 'missing', [1.0])], lambda *args: 1)
        self.assertEqual(result['mrr'], 0)
        self.assertEqual(result['ndcg5'], 0)

    def test_reranker_rejects_wrong_width_bool_string_nonfinite_and_overflow(self):
        good = [0.0] * 7
        bad_vectors = [[], [0.0] * 8, [True] * 7, ['0'] * 7,
                       [float('nan')] * 7, [float('inf')] * 7, [10 ** 1000] * 7]
        for vector in bad_vectors:
            for field in ('weights', 'mean', 'std'):
                with self.subTest(field=field, vector=str(vector)[:30]):
                    options = {'weights': good, 'mean': good, 'std': [1.0] * 7, field: vector}
                    self.assertFalse(benchmark.Reranker(**options).valid)
        for scalar in (True, '0', float('nan'), float('inf'), 10 ** 1000):
            for field in ('bias', 'concept_bonus'):
                with self.subTest(field=field, scalar=str(scalar)[:30]):
                    self.assertFalse(benchmark.Reranker(**{field: scalar}).valid)

    def test_score_rejects_malformed_features_without_throwing(self):
        reranker = benchmark.Reranker()
        for features in (None, [], [0] * 8, [True] * 7, ['0'] * 7,
                         [float('nan')] * 7, [float('inf')] * 7, [10 ** 1000] * 7):
            with self.subTest(features=str(features)[:30]):
                self.assertEqual(reranker.score(features), 0)

    def test_reranker_load_rejects_missing_fields_duplicate_fields_and_oversized_document(self):
        path = self.root / 'rerank.json'
        for body in (json.dumps({'version': benchmark.VERSION, 'features': list(benchmark.FEATURES)}),
                     '{"version":1,"version":1}', ' ' * (2 * 1024 * 1024 + 1)):
            path.write_text(body, encoding='utf-8')
            self.assertIsNone(benchmark.Reranker.load(path))

    def test_reranker_rejects_stale_model_and_tokenizer_assets(self):
        path = self.root / 'rerank.json'
        model = self.root / 'model'
        model.mkdir()
        for name in ('model_quantized.onnx', 'vocab.txt', 'tokenizer_config.json'):
            (model / name).write_bytes(b'initial')
        reranker = benchmark.Reranker()
        reranker.save(path, model_dir=model)
        self.assertIsNotNone(benchmark.Reranker.load(path, model_dir=model))
        for name in ('model_quantized.onnx', 'vocab.txt', 'tokenizer_config.json'):
            asset = model / name
            asset.write_bytes(b'changed')
            self.assertIsNone(benchmark.Reranker.load(path, model_dir=model))
            asset.write_bytes(b'initial')

    def test_production_invalid_fixture_reports_error_without_metrics(self):
        path = self.root / 'bad-fixture.json'
        cases = [[], {'records': [{'id': 'a'}], 'queries': []},
                 {'records': [{'id': 'a', 'kind': 'habit', 'text': 'same'}] * 2, 'queries': []},
                 {'records': [{'id': 'a', 'kind': 'habit', 'text': 'same'}],
                  'queries': [{'text': 'q', 'expected': 'a', 'no_answer': True}]}]
        for data in cases:
            path.write_text(json.dumps(data), encoding='utf-8')
            result = benchmark.evaluate_production(path, self.root)
            self.assertEqual(result['error'], 'invalid_fixture')
            self.assertNotIn('mrr', result)

    def test_production_explicit_override_reaches_engine_without_owner_load(self):
        from memory_engine import MemoryEngine
        reranker = benchmark.Reranker()
        def recall(engine, query, limit=5, kinds=None):
            self.assertIs(engine.reranker(), reranker)
            return []
        with mock.patch.object(benchmark, 'EmbeddingBackend', FakeBackend), \
                mock.patch.object(MemoryEngine, 'recall', recall), \
                mock.patch.object(benchmark.Reranker, 'load', side_effect=AssertionError('owner read')):
            result = benchmark.evaluate_production(self.fixture(), self.root, reranker=reranker)
        self.assertTrue(result['ok'])
        self.assertEqual(result['reranker'], 'override')

    def test_production_rejects_unknown_duplicate_and_missing_hit_ids(self):
        from memory_engine import MemoryEngine, _identity
        identity = _identity('habit', 'same text')
        for hits in ([{'text': 'same text'}], [{'id': 'fixture-b'}],
                     [{'id': identity}, {'id': identity}], 'bad'):
            with self.subTest(hits=hits), \
                    mock.patch.object(benchmark, 'EmbeddingBackend', FakeBackend), \
                    mock.patch.object(MemoryEngine, 'recall', return_value=hits):
                result = benchmark.evaluate_production(self.fixture(), self.root)
            self.assertEqual(result['status'], 'error')
            self.assertNotIn('mrr', result)

    def test_production_detects_query_embedding_failure_not_lexical_fallback(self):
        # First record batch succeeds, then the real engine's query encode fails.
        with mock.patch.object(benchmark, 'EmbeddingBackend', FakeBackend), \
                mock.patch.object(FakeBackend, 'encode', side_effect=[[[1.0, 0.0]] * 2, []]):
            result = benchmark.evaluate_production(self.fixture(), self.root)
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('mrr', result)

    def test_dense_ranking_no_answer_is_separate_from_answerable_mrr(self):
        result = benchmark._rank_metrics([{'id': 'a'}], [[1.0]],
                                        [('answer', 'a', [1.0]), ('unknown', None, [1.0])],
                                        lambda *args: 1)
        self.assertEqual(result['mrr'], 1)
        self.assertEqual(result['answerable'], 1)
        self.assertEqual(result['no_answer_accuracy'], 0)

    def test_loaded_reranker_rejects_bad_numeric_schema_and_feature_revision(self):
        path = self.root / 'rerank.json'
        benchmark.Reranker().save(path, model_dir=self.root)
        good = json.loads(path.read_text(encoding='utf-8'))
        for field, value in [('weights', []), ('mean', [True] * 7), ('std', [0] * 7),
                             ('bias', '0'), ('concept_bonus', float('nan')), ('enabled', 1),
                             ('version', True), ('metrics', [])]:
            with self.subTest(field=field):
                path.write_text(json.dumps({**good, field: value}), encoding='utf-8')
                self.assertIsNone(benchmark.Reranker.load(path, model_dir=self.root))
        path.write_text(json.dumps(good), encoding='utf-8')
        with mock.patch.object(benchmark, 'FEATURE_REVISION', benchmark.FEATURE_REVISION + 1):
            self.assertIsNone(benchmark.Reranker.load(path, model_dir=self.root))
        # Duplicate known fields are rejected, even when every other field is valid.
        body = json.dumps(good)
        path.write_text(body[:-1] + ', "bias": 0}', encoding='utf-8')
        self.assertIsNone(benchmark.Reranker.load(path, model_dir=self.root))

    def test_review_rejects_ambiguous_duplicate_corpus_ids(self):
        class Engine:
            data = {'records': [{'id': 'a'}, {'id': 'a'}]}
            def recall(self, query, limit=5):
                return [{'id': 'a'}]
        rows = [{'query': f'query{i}', 'group': f'g{i}', 'candidates': ['a'],
                 'relevant': ['a'], 'no_answer': False} for i in range(5)]
        result = evaluate_labels(Engine(), rows, split='all')
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertNotIn('mrr', result)


if __name__ == '__main__':
    unittest.main()
