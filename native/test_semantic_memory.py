"""Semantic-ish local recall: paraphrase bridging without models."""
import unittest

from semantic_memory import DIM, cosine, features, rank, similarity


class SemanticMemoryTests(unittest.TestCase):
    def test_paraphrase_is_closer_than_unrelated(self):
        near = similarity('晚上喜欢做什么', '喜欢：夜里散步')
        far = similarity('晚上喜欢做什么', '公开知识：法国的首都是巴黎')
        self.assertGreater(near, far)
        self.assertGreater(near, .08)

    def test_synonym_concept_bridges_wording(self):
        self.assertGreater(similarity('我有点郁闷', '难过：想一个人待着'), .08)
        self.assertGreater(similarity('上班好忙', '工作：今天加班'), .08)

    def test_features_are_bounded_deterministic_and_empty_safe(self):
        left = features('夜里散步')
        self.assertEqual(left, features('夜里散步'))
        self.assertTrue(left)
        self.assertTrue(all(0 <= index < DIM for index in left))
        self.assertEqual(features(''), {})
        self.assertEqual(cosine({}, left), 0.0)

    def test_rank_returns_sorted_indexes(self):
        rows = ['喜欢：夜里散步', '公开知识：巴黎', '工作：加班']
        ranked = rank('晚上走走', rows)
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][0], 0)
        self.assertGreaterEqual(ranked[0][1], ranked[-1][1])


if __name__ == '__main__':
    unittest.main()
