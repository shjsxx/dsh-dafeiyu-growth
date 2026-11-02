"""Dialogue repair: the four-turn failure from the screenshot must not recur."""
import unittest

from chat import reply_to
from dialogue import fallback_reply, is_meta_question, meta_reply


class DialogueTests(unittest.TestCase):
    def test_greeting_does_not_invite_forced_cheer(self):
        reply = reply_to("你好呀，小云岚", "", "neutral")
        self.assertNotIn("加油", reply["text"])
        self.assertIn("我在", reply["text"])

    def test_negated_encouragement_gets_support_not_cheer(self):
        reply = reply_to("不想加油了", "", "joy")
        self.assertIn("那就不加油了", reply["text"])
        self.assertNotIn("开心", reply["text"])

    def test_meta_question_quotes_previous_turn(self):
        self.assertTrue(is_meta_question("为什么是答非所问呢"))
        self.assertTrue(is_meta_question("为什么答非所问"))
        answer = meta_reply("为什么答非所问", "不想加油了", "那你先歇一会儿")
        self.assertIn("不想加油了", answer)
        self.assertIn("那你先歇一会儿", answer)
        self.assertIn("没接上", answer)
        self.assertIn("没有正面回答", answer)

    def test_meta_question_without_context_admits_it(self):
        answer = meta_reply("没听懂", "", "")
        self.assertIn("没接住", answer)
        self.assertNotIn("答非所问", answer)

    def test_fallback_never_emits_a_random_mood_line(self):
        answer = fallback_reply("为什么天空是蓝的", "我今天有点累")
        self.assertIn("还没答上来", answer)
        self.assertIn("我今天有点累", answer)
        self.assertNotIn("嘿嘿", answer)
        self.assertNotIn("开心攒起来", answer)

    def test_representation_of_screenshot_turns(self):
        first = reply_to("你好呀，小云岚", "", "neutral")
        second = reply_to("不想加油了", "", "joy")
        third = meta_reply("为什么是答非所问呢", "不想加油了", second["text"])
        fourth = meta_reply("为什么答非所问", "为什么是答非所问呢", third)
        self.assertNotIn("加油", first["text"])
        self.assertIn("那就不加油了", second["text"])
        self.assertIn("不想加油了", third)
        self.assertIn("不再重复解释", fourth)
        for text in (first["text"], second["text"], third, fourth):
            self.assertNotIn("我有点雀跃", text)


if __name__ == '__main__':
    unittest.main()
