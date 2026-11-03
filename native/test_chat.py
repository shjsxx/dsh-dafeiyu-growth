"""Tests for the offline chat rules (no model, no network)."""
import unittest

from chat import reply_to


class ChatReplyTests(unittest.TestCase):
    def test_learns_name_and_greets_by_name(self):
        learned = reply_to("我叫小林")
        self.assertEqual(learned["name"], "小林")
        self.assertIn("小林", learned["text"])
        self.assertIn("小林", reply_to("你好", name="小林")["text"])
        self.assertIn("小林", reply_to("我叫什么", name="小林")["text"])

    def test_name_question_without_name_asks_for_it(self):
        reply = reply_to("你知道我是谁吗")
        self.assertIn("名字", reply["text"])
        self.assertEqual(reply["name"], "")

    def test_actions_run_hop_and_spin(self):
        self.assertEqual(reply_to("跑一圈给我看")["action"], "run")
        self.assertEqual(reply_to("跳一个")["action"], "hop")
        self.assertEqual(reply_to("转一圈")["action"], "spin")
        self.assertIn("跳一个", reply_to("跳一个")["text"])

    def test_mood_changes_the_reply_but_command_still_wins(self):
        self.assertIn("陪你", reply_to("今天有点闷", mood="sad")["text"])
        self.assertIn("歇", reply_to("嗯", mood="tired")["text"])
        self.assertIn("呼吸", reply_to("嗯", mood="anxious")["text"])
        self.assertIn("安静", reply_to("嗯", mood="anger")["text"])
        self.assertIn("高兴", reply_to("嗯", mood="joy")["text"])
        self.assertEqual(reply_to("跑一圈", mood="sad")["action"], "run")
        self.assertEqual(reply_to("转一圈", mood="tired")["action"], "spin")

    def test_comfort_and_greetings(self):
        self.assertIn("晚安", reply_to("晚安")["text"])
        self.assertIn("累", reply_to("有点累了")["text"])
        self.assertIn("喜欢", reply_to("我喜欢你")["text"])

    def test_personal_event_gets_a_topic_question_not_a_generic_line(self):
        reply = reply_to("我昨天去了公园", "", "calm")
        self.assertIn("公园", reply["text"])
        self.assertIn("怎么样", reply["text"])
        self.assertNotIn("慢慢来", reply["text"])

    def test_unknown_text_is_deterministic(self):
        first = reply_to("今天天气不错")
        second = reply_to("今天天气不错")
        self.assertEqual(first, second)
        self.assertTrue(first["text"])
        self.assertIsNone(first["action"])

    def test_empty_text(self):
        reply = reply_to("   ")
        self.assertTrue(reply["text"])
        self.assertIsNone(reply["action"])

    def test_long_input_is_bounded(self):
        reply = reply_to("跑" * 500)
        self.assertIsNone(reply["action"])  # repeated keywords are not a command
        self.assertLessEqual(len(reply["text"]), 200)

    def test_remembers_likes(self):
        reply = reply_to("我很喜欢喝咖啡")
        self.assertEqual(reply["like"], "喝咖啡")
        self.assertIn("喝咖啡", reply["text"])
        self.assertNotIn("like", reply_to("我喜欢你"))
        self.assertNotIn("like", reply_to("你喜欢什么"))

    def test_name_questions_negations_quotes_and_hypotheticals_do_not_learn(self):
        for text in ("别叫我小林", "你叫我小林吗", "如果我叫小林", "他说我叫小林", "我叫什么", "我叫什么名字", "叫我什么名字", "我叫小林吗", "我叫小林？", "我叫小林还是小王", "“我叫小林”"):
            with self.subTest(text=text):
                self.assertEqual(reply_to(text)["name"], "")

    def test_calling_someone_or_requesting_wakeup_is_not_a_name_declaration(self):
        for text in ("我叫你跑一圈", "我叫了一辆车", "我叫过小林", "叫我起床", "叫我一声", "我叫小林但不要记", "我叫小林但别保存"):
            with self.subTest(text=text):
                self.assertEqual(reply_to(text)["name"], "")

    def test_explicit_memory_opt_out_does_not_write_a_preference(self):
        for text in ("我喜欢咖啡但不要记住", "我喜欢咖啡但别保存", "我喜欢咖啡不过不是每天", "我喜欢咖啡的话会告诉你"):
            with self.subTest(text=text):
                self.assertNotIn("like", reply_to(text))

    def test_long_name_is_not_silently_truncated_into_a_different_name(self):
        self.assertEqual(reply_to("我叫" + "林" * 21)["name"], "")
        self.assertEqual(reply_to("我叫小林。 ")["name"], "小林")

    def test_negated_actions_do_not_execute(self):
        for text in ("不要跑", "别跳", "不用表演", "不想玩", "请停止跑步", "我不喜欢跑步", "你不能蹦", "不要跳，跑也不行"):
            with self.subTest(text=text):
                self.assertIsNone(reply_to(text)["action"])

    def test_non_commands_do_not_execute(self):
        for text in ("你会跑吗？", "为什么跳", "如果我说跑一圈", "他说‘跳一个’", "我昨天跑步了", "跑步很健康", "我喜欢玩游戏吗？"):
            with self.subTest(text=text):
                self.assertIsNone(reply_to(text)["action"])

    def test_preferences_are_parsed_before_activity_keywords(self):
        for value in ("跑步", "跳舞", "玩游戏", "睡觉", "早起", "hello", "小林", "喜欢你的笑容"):
            with self.subTest(value=value):
                reply = reply_to("我喜欢" + value)
                self.assertEqual(reply.get("like"), value)
                self.assertIsNone(reply["action"])

    def test_non_declarations_never_write_preferences(self):
        for text in ("如果我喜欢咖啡", "你说我喜欢咖啡", "我喜欢咖啡吗", "我喜欢什么", "我喜欢谁", "我喜欢咖啡还是茶", "我喜欢咖啡？", "我不喜欢咖啡", "我以前喜欢咖啡", "“我喜欢咖啡”"):
            with self.subTest(text=text):
                self.assertNotIn("like", reply_to(text))

    def test_negated_affection_is_not_reciprocated(self):
        self.assertNotIn("我也喜欢你", reply_to("我不喜欢你")["text"])

    def test_non_memory_chat_does_not_persist_raw_text(self):
        import tempfile
        from pathlib import Path
        from behavior import BehaviorLearner
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "behavior.json"
            learner = BehaviorLearner(path)
            raw = "临时验证码928461请不要记录"
            reply = reply_to(raw)
            if reply.get("name"):
                learner.set_name(reply["name"])
            if reply.get("like"):
                learner.add_like(reply["like"])
            learner.note_chat()
            stored = path.read_text(encoding="utf-8")
            self.assertNotIn(raw, stored)
            self.assertNotIn("928461", stored)
            self.assertEqual(learner.snapshot()["userName"], "")
            self.assertEqual(learner.snapshot()["likes"], [])

    def test_unknown_reply_does_not_claim_to_store_memory(self):
        from chat import DEFAULT_REPLIES
        for text in DEFAULT_REPLIES:
            self.assertNotIn("记住啦", text)
            self.assertNotIn("记在小本子", text)
        self.assertNotIn("我记住了", reply_to("你喜欢什么")["text"])

    def test_sleep_goodnight_is_not_shadowed_by_generic_comfort(self):
        self.assertIn("晚安", reply_to("我要睡觉了")["text"])

    def test_rule_import_does_not_require_optional_qt(self):
        import subprocess
        import sys
        code = "import sys, chat; assert not any(k.startswith('PySide6') for k in sys.modules); assert chat.reply_to('跳一个')['action'] == 'hop'"
        from pathlib import Path
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=Path(__file__).resolve().parent, stdout=None, stderr=None)
        self.assertEqual(result.returncode, 0)

    def test_optional_qt_dialog_preserves_class_api_and_plain_text(self):
        import importlib.util
        if importlib.util.find_spec("PySide6") is None:
            self.skipTest("optional PySide6 is not installed")
        import os
        import subprocess
        import sys
        code = '''
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from chat import ChatDialog
from chat import ChatDialog as Again
app = QApplication([])
assert ChatDialog is Again and issubclass(ChatDialog, QDialog)
dialog = ChatDialog(name="<b>owner</b>")
assert dialog.input.maxLength() == 200
for label in dialog.findChildren(QLabel):
    assert label.textFormat() == Qt.TextFormat.PlainText
assert any("<b>owner</b>" in label.text() for label in dialog.findChildren(QLabel))
dialog.deleteLater()
'''
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        from pathlib import Path
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=Path(__file__).resolve().parent, env=environment, stdout=None, stderr=None)
        self.assertEqual(result.returncode, 0)

    def test_name_is_trimmed(self):
        reply = reply_to("我叫  阿岚  ")
        self.assertEqual(reply["name"], "阿岚")
        self.assertEqual(reply_to("我是真的累了")["name"], "")


if __name__ == "__main__":
    unittest.main()
