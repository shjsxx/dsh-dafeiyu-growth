"""Naturalness layer changes only generic local lines, never facts or actions."""
import unittest

from chat import DEFAULT_REPLIES
from naturalization import naturalize


class NaturalizationTests(unittest.TestCase):
    def test_repeated_generic_line_gets_a_different_generic_line(self):
        first = DEFAULT_REPLIES[0]
        result = naturalize(first, user_text="嗯", last_reply=first)
        self.assertNotEqual(result, first)
        self.assertTrue(result)

    def test_topic_memory_makes_a_generic_fallback_relational(self):
        result = naturalize(DEFAULT_REPLIES[1], topics=[{"topic": "公园"}], seed=1)
        self.assertIn("公园", result)
        self.assertNotEqual(result, DEFAULT_REPLIES[1])

    def test_sad_mood_prefers_gentle_fallback(self):
        result = naturalize(DEFAULT_REPLIES[2], mood="sad", seed=0)
        self.assertIn("慢慢", result)

    def test_specific_factual_answer_is_untouched(self):
        text = "巴黎是法国首都。"
        self.assertEqual(naturalize(text, user_text="巴黎是什么", last_reply=text), text)

    def test_no_raw_user_or_assistant_text_is_embedded(self):
        result = naturalize(DEFAULT_REPLIES[0], user_text="私密原句", topics=[{"topic": "考试"}])
        self.assertNotIn("私密原句", result)
