"""Domain rerank benchmark: CV improvement, persistence and fallback."""
from pathlib import Path
import tempfile
import unittest

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
            reranker.save(path, enabled=True)
            loaded = Reranker.load(path)
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded.enabled)
            body = path.read_text(encoding='utf-8')
            self.assertNotIn('夜里散步', body)
            self.assertNotIn('音乐场景', body)

    def test_cross_validated_rerank_beats_or_ties_baseline_when_model_exists(self):
        result = evaluate()
        if not result.get('ok'):
            self.skipTest(result.get('error', 'embedding model unavailable'))
        self.assertGreaterEqual(result['rerank']['hit1'], result['baseline']['hit1'])
        self.assertGreaterEqual(result['rerank']['mrr'], result['baseline']['mrr'])
        self.assertEqual(result['rerank']['queries'], result['queries'])
        production = evaluate_production()
        self.assertEqual(production['queries'], result['queries'])
        self.assertGreaterEqual(production['hit1'], .9)


if __name__ == '__main__':
    unittest.main()
