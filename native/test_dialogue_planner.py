"""Dialogue policy: intent routing and follow-up anchoring."""
import unittest

from dialogue_planner import classify, extract_topic, followup_reply, is_followup


class DialoguePlannerTests(unittest.TestCase):
    def test_intents_are_mutually_explicit(self):
        self.assertEqual(classify("为什么答非所问"), "meta")
        self.assertEqual(classify("然后呢"), "followup")
        self.assertEqual(classify("你还记得我喜欢什么吗"), "memory")
        self.assertEqual(classify("快来"), "command")
        self.assertEqual(classify("我今天很难过"), "emotion")
        self.assertEqual(classify("你好呀"), "greeting")
        self.assertEqual(classify("法国的首都是什么"), "question")
        self.assertEqual(classify("今天天气不错"), "smalltalk")

    def test_followup_requires_short_context(self):
        self.assertTrue(is_followup("然后呢？"))
        self.assertFalse(is_followup("然后呢，我想问一个完全新的问题：法国的首都是什么"))
        self.assertIn("哪一句", followup_reply("然后呢", "", ""))

    def test_followup_anchors_actual_topic_instead_of_inventing(self):
        answer = followup_reply("后来呢", "我昨天去了公园", "听起来不错")
        self.assertIn("我昨天去了公园", answer)
        self.assertIn("后来发生了什么", answer)
        why = followup_reply("为什么呢", "我昨天去了公园", "听起来不错")
        self.assertIn("原因", why)

    def test_topic_strips_polite_filler(self):
        self.assertEqual(extract_topic("请问然后我昨天去了公园"), "我昨天去了公园")


if __name__ == '__main__':
    unittest.main()
