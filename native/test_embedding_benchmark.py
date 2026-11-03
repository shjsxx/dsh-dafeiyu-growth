"""Domain rerank benchmark: CV improvement, persistence and fallback."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from embedding_benchmark import Reranker, evaluate, evaluate_production, feature_row, load_fixture


class EmbeddingBenchmarkTests(unittest.TestCase):
    def test_synthetic_fixture_is_bounded(self):
        records, queries = load_fixture()
        self.assertGreaterEqual(len(records), 12)
        self.assertGreaterEqual(len(queries), 20)
        self.assertTrue(all(row['expected'] in {record['id'] for record in records} for row in queries))

    def test_feature_row_has_fixed_width_and_concept_bridge(self):
        records, queries = load_fixture()
        music = next(row for row in records if row['id'] == 'music')
        vector = feature_row('我喜欢的音乐场景', music, .5)
        self.assertEqual(len(vector), 7)
        self.assertEqual(vector[2], 1.0)
        self.assertTrue(all(0 <= value <= 1 for value in vector))

    def test_reranker_persists_weights_without_fixture_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rerank.json'
            reranker = Reranker([.1] * 7, .2, [0.0] * 7, [1.0] * 7,
                                {'improved': True}, concept_bonus=.5)
            reranker.save(path, enabled=True, model_dir=Path(directory) / 'absent-model')
            loaded = Reranker.load(path, model_dir=Path(directory) / 'absent-model')
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded.enabled)
            body = path.read_text(encoding='utf-8')
            self.assertNotIn('夜里散步', body)
            self.assertNotIn('音乐场景', body)

    def test_cross_validation_and_production_are_offline_and_do_not_require_quality_claims(self):
        # Evaluation correctness must not depend on installed owner assets or
        # an unjustified quality floor for a synthetic fixture.
        class Backend:
            def __init__(self, directory=None):
                self.directory, self.status = Path(directory), 'ready'
            def files_present(self):
                return True
            def encode(self, texts):
                return [[1.0, 0.0] for _ in texts]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch('embedding_benchmark.EmbeddingBackend', Backend):
            result = evaluate(model_dir=directory)
            self.assertTrue(result['ok'])
            self.assertEqual(result['rerank']['queries'], result['queries'])
            self.assertEqual(result['reranker'].metrics['test_queries'], result['queries'])
            production = evaluate_production(model_dir=directory)
            self.assertTrue(production['ok'])
            self.assertEqual(production['queries'], result['queries'])
            self.assertEqual(production['reranker'], 'off')


if __name__ == '__main__':
    unittest.main()
