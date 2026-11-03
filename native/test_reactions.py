"""Reaction planning: keyword priority, mood fallback, bounded durations."""
import unittest

from reactions import REACTIONS, detect_reaction, plan


class ReactionTests(unittest.TestCase):
    def test_explicit_interactions_win(self):
        self.assertEqual(detect_reaction('快来，小云岚'), 'come')
        self.assertEqual(detect_reaction('给我讲个笑话吧'), 'funny')
        self.assertEqual(detect_reaction('好无聊啊'), 'bored')
        self.assertEqual(detect_reaction('我困了，想睡'), 'sleepy')
        self.assertEqual(detect_reaction('我想哭'), 'cry')
        self.assertEqual(detect_reaction('今天有点难过'), 'sad')

    def test_cry_beats_sad_and_words_beat_mood(self):
        self.assertEqual(detect_reaction('难过得想哭', 'sad'), 'cry')
        self.assertEqual(detect_reaction('我有点难过', 'joy'), 'sad')

    def test_mood_fallback_is_explicit(self):
        self.assertEqual(detect_reaction('今天', 'sad'), 'sad')
        self.assertEqual(detect_reaction('今天', 'tired'), 'sleepy')
        self.assertEqual(detect_reaction('今天', 'neutral'), '')

    def test_every_plan_is_bounded_and_complete(self):
        for key in REACTIONS:
            row = plan(key)
            self.assertEqual(row['key'], key)
            self.assertTrue(row['message'])
            self.assertTrue(row['detail'])
            self.assertTrue(1200 <= row['duration'] <= 8000)
            self.assertIn(row['effect'], {'sparkle', 'speed', 'rain', 'question', 'zzz', 'tears'})
            self.assertIn(row['motion'], {'spin', 'come', 'droop', 'sway', 'doze'})
        self.assertIsNone(plan('missing'))


if __name__ == '__main__':
    unittest.main()
