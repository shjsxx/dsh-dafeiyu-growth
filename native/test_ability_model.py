"""Pure, bounded six-dimension ability indices. No IO, network or Qt."""
import json
import unittest

from ability_model import DIMENSIONS, ENGINE_INPUTS, derive_abilities


class AbilityModelTests(unittest.TestCase):
    def populated(self):
        return (
            {'traits': {'stamina': 42, 'wit': 30, 'affinity': 55, 'calm': 70},
             'counters': {'turns': 90, 'toolCalls': 40, 'recoveries': 3},
             'streak': 6, 'learned': [{'id': 'a', 'text': '习惯'}] * 5,
             'diary': [{'text': '回顾'}] * 7},
            {'agility': 60, 'balance': 48, 'runs': 30, 'jumps': 12, 'falls': 4,
             'recoveries': 3, 'pets': 20, 'chats': 15},
            {'knowledge': {'count': 40}, 'journal': {'totals': {
                'weight_steps': 12, 'emotion_adaptation_passes': 3}}},
            {'learned': 6, 'fit_samples': 8, 'lexicon': 50},
        )

    def test_six_bounded_dimensions_and_summary(self):
        result = derive_abilities(*self.populated())
        self.assertEqual([row['key'] for row in result['dimensions']],
                         [row['key'] for row in DIMENSIONS])
        self.assertEqual(len(result['dimensions']), 6)
        for row in result['dimensions']:
            self.assertIsInstance(row['value'], int)
            self.assertGreaterEqual(row['value'], 0)
            self.assertLessEqual(row['value'], 100)
            self.assertTrue(row['rank'])
            self.assertTrue(row['basis'])
        self.assertGreaterEqual(result['overall'], 0)
        self.assertLessEqual(result['overall'], 100)
        self.assertIn(result['strongest']['key'], {row['key'] for row in result['dimensions']})
        self.assertIn(result['focus']['key'], {row['key'] for row in result['dimensions']})

    def test_empty_and_malformed_inputs_are_zero_and_never_raise(self):
        result = derive_abilities({}, {}, {}, {})
        self.assertEqual([row['value'] for row in result['dimensions']], [0] * 6)
        hostile = derive_abilities(None, {'agility': 'x', 'runs': float('inf')},
                                   {'knowledge': {'count': -4}}, {'learned': []})
        for row in hostile['dimensions']:
            self.assertTrue(0 <= row['value'] <= 100)
        json.dumps(result, allow_nan=False)

    def test_deterministic_and_engine_contract_is_stable(self):
        first = derive_abilities(*self.populated())
        second = derive_abilities(*self.populated())
        self.assertEqual(first, second)
        self.assertEqual(set(first['engine_inputs']), {'growth', 'memory', 'personality'})
        self.assertEqual(set(first['engine_inputs']['growth']), set(ENGINE_INPUTS['growth']))
        self.assertEqual(first['kind'], 'practice_index_not_certification')

    def test_more_practice_raises_the_matching_dimension_only(self):
        base_state = self.populated()[0]
        base = derive_abilities(base_state, {}, {}, {})
        trained = derive_abilities(base_state, {'agility': 90, 'runs': 100, 'jumps': 50}, {}, {})
        self.assertGreater(trained['dimensions'][0]['value'], base['dimensions'][0]['value'])
        self.assertEqual(trained['dimensions'][4]['value'], base['dimensions'][4]['value'])


if __name__ == '__main__':
    unittest.main()
