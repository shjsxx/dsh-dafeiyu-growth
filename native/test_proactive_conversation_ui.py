"""Offscreen UI contracts: passive display, explicit consent, bounded RAM only."""
import builtins
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QLabel, QLineEdit, QPushButton,
    QStyle, QStyleOptionButton,
)
from shiboken6 import isValid

from conversation_ui import (
    ConversationDialog, MAX_CONTEXT_CHARS, MAX_CONTEXT_MESSAGES,
    MAX_CONTEXT_MESSAGE_CHARS, MAX_HISTORY_ROWS,
)


CONFIG = {"endpoint": "https://api.example/v1/chat/completions", "model": "chat-model",
          "max_tokens": 64, "key_saved": True}


class ProactiveConversationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if isValid(dialog):
                dialog.deleteLater()
        self.app.processEvents()

    def dialog(self, configured=True):
        dialog = ConversationDialog()
        self.dialogs.append(dialog)
        if configured:
            dialog.set_cloud_state(CONFIG)
        return dialog

    def allow_session(self, dialog):
        dialog.session_checkbox.click()
        self.assertFalse(dialog.session_cloud_enabled())
        self.assertFalse(dialog.session_checkbox.isChecked())
        self.assertFalse(dialog.session_allow_button.isHidden())
        dialog.session_allow_button.click()
        self.assertTrue(dialog.session_cloud_enabled())
        self.assertTrue(dialog.session_checkbox.isChecked())

    def test_construction_and_proactive_never_request_focus_or_activation(self):
        with patch.object(QDialog, "activateWindow") as activate, \
                patch.object(QDialog, "raise_") as raise_window, \
                patch.object(QLineEdit, "setFocus") as focus:
            dialog = self.dialog()
            self.assertFalse(dialog.isVisible())
            self.assertTrue(dialog.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
            dialog.input.setText("还没有发出的草稿")
            dialog.show_proactive("我在这里，随时可以接着聊。")
            self.app.processEvents()
            dialog.show_proactive("第二句", "<b>主动</b>")
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())
            self.assertFalse(dialog.isModal())
            self.assertEqual(dialog.input.text(), "还没有发出的草稿")
            self.assertFalse(dialog.is_cloud_mode())
            self.assertEqual(len(dialog._rows), 2)
            self.assertLessEqual(dialog.width(), 410)
            self.assertLessEqual(dialog.height(), 300)
            activate.assert_not_called()
            raise_window.assert_not_called()
            focus.assert_not_called()

    def test_expand_collapse_preserves_history_draft_channel_and_consent(self):
        dialog = self.dialog()
        dialog.cloud_button.setChecked(True)
        self.allow_session(dialog)
        dialog.input.setText("接着说")
        dialog.show_proactive("第一条")
        before = [row["content"] for row in dialog._rows]
        dialog.expand_button.click()
        self.assertFalse(dialog.compact)
        self.assertLessEqual(dialog.width(), 440)
        self.assertLessEqual(dialog.height(), 560)
        dialog.show_proactive("第二条")
        self.assertFalse(dialog.compact)
        dialog.expand_button.click()
        self.assertTrue(dialog.compact)
        self.assertEqual([row["content"] for row in dialog._rows], before + ["第二条"])
        self.assertEqual(dialog.input.text(), "接着说")
        self.assertTrue(dialog.is_cloud_mode())
        self.assertTrue(dialog.session_cloud_enabled())

    def test_compact_three_rows_scroll_to_last_synchronous_reply_after_layout(self):
        dialog = self.dialog()
        dialog.local_submitted.connect(lambda _text: dialog.show_reply("早呀，我在这里。", "本地"))
        dialog.show_proactive("你忙你的，我在旁边安安稳稳地陪着。", "云岚主动搭话 · 可直接回复")
        QTest.qWait(80)
        dialog.input.setText("早安")
        QTest.keyClick(dialog.input, Qt.Key.Key_Return)
        QTest.qWait(100)
        self.assertTrue(dialog.compact)
        self.assertEqual(len(dialog._rows), 3)
        self.assertEqual(dialog._rows[-1]["content"], "早呀，我在这里。")
        bar = dialog.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)  # The three-row transcript overflows.
        self.assertEqual(bar.value(), bar.maximum())
        label = dialog._rows[-1]["widget"].findChild(QLabel, "bubbleText")
        viewport = dialog.scroll.viewport()
        visible_rect = QRect(label.mapTo(viewport, QPoint(0, 0)), label.size())
        self.assertTrue(viewport.rect().contains(visible_rect))
        # Once this append has settled, ordinary owner scrolling is not pinned.
        bar.setValue(0)
        QTest.qWait(80)
        self.assertEqual(bar.value(), 0)

    def test_short_bubble_keeps_standard_metadata_on_one_line(self):
        dialog = self.dialog()
        dialog.show_proactive("我在。", "云岚主动搭话 · 可直接回复")
        self.app.processEvents()
        bubble = dialog.findChild(QFrame, "bubbleAssistant")
        meta = bubble.findChild(QLabel, "bubbleMeta")
        self.assertGreaterEqual(bubble.width(), 240)
        self.assertGreaterEqual(meta.width(), meta.fontMetrics().horizontalAdvance(meta.text()))
        self.assertLessEqual(meta.height(), meta.fontMetrics().height() + 2)

    def test_checkbox_indicators_are_visible_and_checked_fill_changes(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        for checkbox in (dialog.online_checkbox, dialog.session_checkbox):
            with self.subTest(text=checkbox.text()):
                option = QStyleOptionButton()
                checkbox.initStyleOption(option)
                rect = checkbox.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox)
                self.assertGreaterEqual(rect.width(), 14)
                self.assertGreaterEqual(rect.height(), 14)
                before = checkbox.grab().toImage()
                center = rect.center()
                unchecked = before.pixelColor(center)
                # The unchecked border itself is visible, not transparent.
                self.assertNotEqual(before.pixelColor(rect.left(), center.y()), unchecked)
                checkbox.setChecked(True)
                self.app.processEvents()
                checked = checkbox.grab().toImage().pixelColor(center)
                self.assertNotEqual(checked, unchecked)
                checkbox.setChecked(False)

    def test_confirmation_auto_expands_then_restores_compact_without_losing_input(self):
        dialog = self.dialog()
        dialog.cloud_button.setChecked(True)
        dialog.show_proactive("保留历史")
        dialog.input.setText("逐条确认草稿")
        dialog.send()
        self.app.processEvents()
        self.assertFalse(dialog.compact)
        self.assertEqual(dialog.size().toTuple(), (440, 560))
        self.assertGreaterEqual(dialog.confirm_scroll.height(), 150)
        self.assertTrue(dialog.rect().contains(dialog.input.geometry()))
        dialog.cancel_confirm()
        self.assertTrue(dialog.compact)
        self.assertEqual(dialog.input.text(), "逐条确认草稿")
        dialog.send()
        dialog.confirm_cloud()
        self.assertTrue(dialog.compact)
        dialog.show_reply("回复")
        self.assertEqual(dialog._rows[0]["content"], "保留历史")
        dialog.input.setText("允许不会发送的草稿")
        dialog.session_checkbox.click()
        self.assertFalse(dialog.compact)
        dialog.session_allow_button.click()
        self.assertTrue(dialog.compact)
        self.assertEqual(dialog.input.text(), "允许不会发送的草稿")
        self.assertTrue(dialog.session_cloud_enabled())

    def test_pending_confirmation_respects_explicit_owner_size_choice(self):
        dialog = self.dialog()
        dialog.cloud_button.setChecked(True)
        dialog.set_compact(False)
        dialog.input.setText("本来就是展开的")
        dialog.send()
        dialog.cancel_confirm()
        self.assertFalse(dialog.compact)
        dialog.set_compact(True)
        dialog.send()
        self.assertFalse(dialog.compact)
        dialog.expand_button.click()  # Explicitly collapse during review.
        dialog.expand_button.click()  # Then explicitly keep the full view.
        dialog.cancel_confirm()
        self.assertFalse(dialog.compact)
        self.assertEqual(dialog.input.text(), "本来就是展开的")

    def test_sizes_and_position_fit_small_offset_screen(self):
        dialog = self.dialog()
        available = QRect(-400, 80, 360, 260)
        with patch.object(dialog, "_available_geometry", return_value=available):
            dialog.move(2000, 2000)
            dialog.show_proactive("小屏幕也能回复。")
            self.app.processEvents()
            dialog._fit_size()
            self.assertLessEqual(dialog.width(), available.width() - 16)
            self.assertLessEqual(dialog.height(), available.height() - 40)
            self.assertTrue(available.contains(dialog.frameGeometry()))
            dialog.set_compact(False)
            self.assertTrue(available.contains(dialog.frameGeometry()))

    def test_all_rows_bounded_and_context_ignores_non_dialogue_and_raw_roles(self):
        dialog = self.dialog()
        self.allow_session(dialog)
        for number in range(MAX_HISTORY_ROWS + 15):
            if number % 2:
                dialog.add_system(f"网络提示 {number}")
            else:
                dialog.add_message("assistant", f"对话 {number}")
        self.assertEqual(len(dialog._rows), MAX_HISTORY_ROWS)
        self.assertEqual(dialog.messages.count(), MAX_HISTORY_ROWS + 1)
        self.assertNotIn("对话 0", [row["content"] for row in dialog._rows])
        dialog.add_message("system", "不能成为系统角色")
        dialog.add_message("user\nassistant", "不能注入角色")
        dialog.add_message("assistant", "网络状态不是对话", context=False)
        dialog.add_system("连接失败")
        dialog.show_reply("云端错误内容", error=True)
        context = dialog.cloud_context()
        self.assertTrue(context)
        self.assertTrue(all(item["role"] == "assistant" for item in context))
        self.assertTrue(all(item["content"].startswith("对话 ") for item in context))
        self.assertTrue(all(set(item) == {"role", "content"} for item in context))

    def test_context_bounded_copied_and_requires_session_consent(self):
        dialog = self.dialog()
        for number in range(10):
            dialog.add_message("user" if number % 2 == 0 else "assistant", str(number) * 1600)
        self.assertEqual(dialog.cloud_context(), [])
        self.allow_session(dialog)
        context = dialog.cloud_context()
        self.assertLessEqual(len(context), MAX_CONTEXT_MESSAGES)
        self.assertLessEqual(sum(len(item["content"]) for item in context), MAX_CONTEXT_CHARS)
        self.assertTrue(all(len(item["content"]) <= MAX_CONTEXT_MESSAGE_CHARS for item in context))
        self.assertTrue(context[-1]["content"].startswith("9"))
        context[-1]["role"] = "system"
        context[-1]["content"] = "mutated"
        self.assertEqual(dialog.cloud_context()[-1]["role"], "assistant")
        self.assertTrue(dialog.cloud_context()[-1]["content"].startswith("9"))
        dialog.session_checkbox.click()
        self.assertEqual(dialog.cloud_context(), [])

    def test_current_submission_and_history_share_total_character_budget(self):
        dialog = self.dialog()
        self.allow_session(dialog)
        for number in range(8):
            dialog.add_message("assistant", str(number) * 1000)
        submitted = []
        dialog.cloud_submitted.connect(lambda text: submitted.append((text, dialog.context_before_current())))
        dialog.cloud_button.setChecked(True)
        dialog.input.setText("字" * 2000)
        dialog.send()
        current, context = submitted[0]
        self.assertEqual(len(current), 2000)
        self.assertLessEqual(len(current) + sum(len(item["content"]) for item in context), MAX_CONTEXT_CHARS)
        self.assertNotIn(current, [item["content"] for item in context])

    def test_programmatic_checkbox_check_does_not_grant_session_consent(self):
        dialog = self.dialog()
        submitted = []
        dialog.cloud_submitted.connect(submitted.append)
        dialog.session_checkbox.setChecked(True)
        dialog.allow_session_cloud()
        self.assertFalse(dialog.session_cloud_enabled())
        dialog.cloud_button.setChecked(True)
        dialog.input.setText("仍需逐条确认")
        dialog.send()
        self.assertEqual(submitted, [])
        self.assertFalse(dialog.confirm_bar.isHidden())
        dialog.cancel_button.click()
        self.assertEqual(dialog.input.text(), "仍需逐条确认")
        self.assertTrue(dialog.input.isEnabled())

    def test_six_short_messages_and_explicit_exclude_latest(self):
        dialog = self.dialog()
        self.allow_session(dialog)
        for number in range(9):
            dialog.add_message("user" if number % 2 == 0 else "assistant", str(number))
        self.assertEqual([entry["content"] for entry in dialog.cloud_context()], list("345678"))
        self.assertEqual([entry["content"] for entry in dialog.context_before_current()], list("234567"))

    def test_online_state_synchronization_is_not_an_owner_action(self):
        dialog = self.dialog()
        enabled, refreshed = [], []
        dialog.online_enabled_changed.connect(enabled.append)
        dialog.online_refresh_requested.connect(lambda: refreshed.append(True))
        self.assertFalse(dialog.online_checkbox.isChecked())
        dialog.set_online_state({"status": "live", "enabled": True, "summary": "公开资料：示例 " * 80})
        self.assertEqual(enabled, [])
        self.assertEqual(refreshed, [])
        self.assertEqual(dialog.online_state.text(), "实时在线")
        self.assertLessEqual(len(dialog.online_summary.text()), 160)
        self.assertTrue(dialog.online_summary.isHidden())
        dialog.set_compact(False)
        self.assertFalse(dialog.online_summary.isHidden())
        dialog.online_checkbox.click()
        self.assertEqual(enabled, [False])
        dialog.refresh_button.click()
        self.assertEqual(refreshed, [True])
        dialog.set_online_state({"status": "cached", "enabled": False})
        self.assertEqual(dialog.online_state.text(), "缓存资料")
        dialog.set_online_state({"status": "<b>live</b>", "summary": ["not text"]})
        self.assertEqual(dialog.online_state.text(), "未连接")
        self.assertEqual(dialog.online_summary.text(), "")
        self.assertEqual(enabled, [False])
        for label in dialog.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_session_click_requires_explicit_button_and_enabling_never_sends(self):
        dialog = self.dialog()
        submitted = []
        dialog.cloud_submitted.connect(submitted.append)
        dialog.cloud_button.setChecked(True)
        dialog.input.setText("此时不发送")
        dialog.session_checkbox.click()
        self.assertFalse(dialog.session_cloud_enabled())
        self.assertFalse(dialog.confirm_bar.isHidden())
        for required in (CONFIG["endpoint"], CONFIG["model"], "6", "4000", "DSH", "文件", "屏幕"):
            self.assertIn(required, dialog.confirm_label.text())
        dialog.confirm_cloud()  # A stale/default message confirmation cannot grant consent.
        dialog.send()
        self.assertEqual(submitted, [])
        dialog.session_allow_button.click()
        self.assertTrue(dialog.session_cloud_enabled())
        self.assertEqual(submitted, [])
        self.assertEqual(dialog.input.text(), "此时不发送")
        dialog.send()
        self.assertEqual(submitted, ["此时不发送"])
        self.assertTrue(dialog.busy)

    def test_default_keyboard_return_still_requires_per_message_confirmation(self):
        dialog = self.dialog()
        submitted = []
        dialog.cloud_submitted.connect(submitted.append)
        dialog.cloud_button.setChecked(True)
        dialog.show()
        self.app.processEvents()
        dialog.input.setText("逐条确认")
        QTest.keyClick(dialog.input, Qt.Key.Key_Return)
        self.assertEqual(submitted, [])
        self.assertFalse(dialog.confirm_bar.isHidden())
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        self.assertEqual(submitted, [])
        self.assertTrue(all(not button.autoDefault() and not button.isDefault()
                            for button in dialog.findChildren(QPushButton)))
        dialog.confirm_send_button.click()
        self.assertEqual(submitted, ["逐条确认"])
        self.assertEqual(dialog.cloud_context(), [])
        dialog.show_reply("第一条回复")
        dialog.input.setText("第二条仍需确认")
        QTest.keyClick(dialog.input, Qt.Key.Key_Return)
        self.assertEqual(submitted, ["逐条确认"])
        self.assertFalse(dialog.confirm_bar.isHidden())
        self.assertIn("仅这一段", dialog.confirm_label.text())

    def test_continuous_keyboard_reply_context_excludes_current_and_failed_turns(self):
        dialog = self.dialog()
        local, cloud = [], []
        dialog.local_submitted.connect(local.append)
        dialog.cloud_submitted.connect(lambda text: cloud.append((text, dialog.context_before_current())))
        dialog.show_proactive("你好，今天想聊什么？")
        dialog.input.setText("本地继续")
        QTest.keyClick(dialog.input, Qt.Key.Key_Return)
        self.assertEqual(local, ["本地继续"])
        dialog.show_reply("本地回应")
        self.allow_session(dialog)
        dialog.cloud_button.setChecked(True)
        dialog.input.setText("云端第一条")
        QTest.keyClick(dialog.input, Qt.Key.Key_Return)
        self.assertEqual(cloud[0][0], "云端第一条")
        # Proactive bubbles can contain durable private facts, not user-authored
        # dialogue. They must not be laundered into history by session consent.
        self.assertEqual([entry["content"] for entry in cloud[0][1]], ["本地继续", "本地回应"])
        self.assertNotIn('你好，今天想聊什么？', str(cloud[0][1]))
        self.assertTrue(dialog.confirm_bar.isHidden())
        dialog.show_reply("成功回复", "云端")
        dialog.input.setText("失败的输入")
        dialog.send()
        self.assertIn({"role": "user", "content": "云端第一条"}, cloud[-1][1])
        dialog.show_reply("网络失败", error=True)
        dialog.input.setText("重试输入")
        dialog.send()
        self.assertNotIn("失败的输入", [entry["content"] for entry in cloud[-1][1]])
        self.assertNotIn("网络失败", [entry["content"] for entry in cloud[-1][1]])
        self.assertNotIn("重试输入", [entry["content"] for entry in cloud[-1][1]])
        self.assertFalse(dialog.input.isEnabled())
        dialog.show_reply("好了")
        self.assertTrue(dialog.input.isEnabled())
        self.assertTrue(dialog.session_cloud_enabled())

    def test_endpoint_model_change_revokes_and_pending_send_is_not_retargeted(self):
        dialog = self.dialog()
        self.allow_session(dialog)
        dialog.set_cloud_state(dict(CONFIG, max_tokens=128))
        self.assertTrue(dialog.session_cloud_enabled())
        dialog.set_cloud_state(dict(CONFIG, model="another-model"))
        self.assertFalse(dialog.session_cloud_enabled())
        self.assertFalse(dialog.session_checkbox.isChecked())
        self.allow_session(dialog)
        dialog.set_cloud_state(dict(CONFIG, endpoint="https://other.example/v1/chat/completions"))
        self.assertFalse(dialog.session_cloud_enabled())
        dialog.cloud_button.setChecked(True)
        dialog.input.setText("不能悄悄换目标")
        dialog.send()
        submitted = []
        dialog.cloud_submitted.connect(submitted.append)
        dialog.set_cloud_state(CONFIG)
        self.assertTrue(dialog.confirm_bar.isHidden())
        self.assertEqual(dialog.input.text(), "不能悄悄换目标")
        dialog.confirm_cloud()
        self.assertEqual(submitted, [])
        dialog.session_checkbox.click()
        dialog.set_cloud_state(dict(CONFIG, model="changed-during-consent"))
        dialog.allow_session_cloud()
        self.assertFalse(dialog.session_cloud_enabled())

    def test_changed_token_budget_cancels_pending_preview_and_restores_draft(self):
        dialog = self.dialog()
        submitted = []
        dialog.cloud_submitted.connect(submitted.append)
        dialog.cloud_button.setChecked(True)
        dialog.input.setText("不能用旧预算确认新预算")
        dialog.send()
        self.assertIn("64 tokens", dialog.confirm_label.text())
        self.assertFalse(dialog.compact)
        dialog.set_cloud_state(dict(CONFIG, max_tokens=1024))
        self.assertTrue(dialog.confirm_bar.isHidden())
        self.assertEqual(dialog.pending_text, "")
        self.assertEqual(dialog.input.text(), "不能用旧预算确认新预算")
        self.assertTrue(dialog.compact)
        dialog.confirm_cloud()
        self.assertEqual(submitted, [])
        dialog.send()
        self.assertIn("1024 tokens", dialog.confirm_label.text())
        self.assertEqual(submitted, [])

    def test_invalid_targets_never_authorize_session_or_send(self):
        dialog = self.dialog(False)
        submitted = []
        dialog.cloud_submitted.connect(submitted.append)
        dialog.cloud_button.setChecked(True)
        for endpoint in ("", "javascript:alert(1)", "http://public.example/v1", "https://user:secret@api.example/v1",
                         "https://api.example/v1?secret=x", "https://[bad", "https://api.example:99999/v1"):
            with self.subTest(endpoint=endpoint):
                dialog.set_cloud_state(dict(CONFIG, endpoint=endpoint))
                dialog.session_checkbox.click()
                dialog.allow_session_cloud()
                self.assertFalse(dialog.session_cloud_enabled())
                dialog.input.setText("不能发送")
                dialog.send()
                self.assertTrue(dialog.confirm_bar.isHidden())
                self.assertEqual(submitted, [])

    def test_session_close_and_new_dialog_are_volatile(self):
        dialog = self.dialog()
        self.allow_session(dialog)
        dialog.add_message("user", "只在内存")
        closed = []
        dialog.closed.connect(lambda: closed.append(True))
        dialog.close()
        self.assertEqual(closed, [True])
        self.assertFalse(dialog.session_cloud_enabled())
        self.assertEqual(dialog.cloud_context(), [])
        fresh = self.dialog()
        self.assertFalse(fresh.session_cloud_enabled())
        self.assertFalse(fresh.session_checkbox.isChecked())
        self.assertFalse(fresh.online_checkbox.isChecked())
        self.assertEqual(fresh._rows, [])

    def test_escape_reject_and_accept_revoke_session_and_notify_once(self):
        for action in ("escape", "reject", "accept"):
            with self.subTest(action=action):
                dialog = self.dialog()
                self.allow_session(dialog)
                closed = []
                dialog.closed.connect(lambda: closed.append(True))
                dialog.show()
                self.app.processEvents()
                if action == "escape":
                    QTest.keyClick(dialog, Qt.Key.Key_Escape)
                else:
                    getattr(dialog, action)()
                self.assertFalse(dialog.session_cloud_enabled())
                self.assertEqual(dialog.cloud_context(), [])
                self.assertEqual(closed, [True])

    def test_mode_cancel_busy_and_proactive_during_confirmation_preserve_contract(self):
        dialog = self.dialog()
        dialog.cloud_button.setChecked(True)
        dialog.input.setText("待确认的原文")
        dialog.send()
        dialog.show_proactive("继续陪你", "主动")
        self.assertEqual(dialog.pending_text, "待确认的原文")
        self.assertFalse(dialog.input.isEnabled())
        dialog.local_button.setChecked(True)
        self.assertEqual(dialog.input.text(), "待确认的原文")
        self.assertTrue(dialog.confirm_bar.isHidden())
        dialog.set_busy(True)
        dialog.show_proactive("不会解锁正在等待的输入")
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.input.isEnabled())
        dialog.show_reply("等待结束")
        self.assertTrue(dialog.input.isEnabled())

    def test_passive_state_and_reply_do_not_explicitly_focus_inactive_window(self):
        dialog = self.dialog()
        with patch.object(dialog, "isActiveWindow", return_value=False), \
                patch.object(QLineEdit, "setFocus") as focus:
            dialog.set_cloud_state(dict(CONFIG, model="updated"))
            dialog.set_online_state({"status": "cached", "enabled": True})
            dialog.cancel_confirm()
            dialog.show_reply("延迟回复")
            focus.assert_not_called()

    def test_widget_does_not_read_write_or_start_networking(self):
        with patch.object(builtins, "open", side_effect=AssertionError("file IO")), \
                patch.object(Path, "open", side_effect=AssertionError("path IO")), \
                patch.object(socket, "create_connection", side_effect=AssertionError("network IO")), \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("HTTP IO")):
            dialog = self.dialog()
            dialog.show_proactive("内存里的对话")
            dialog.set_online_state({"status": "cached", "summary": "宿主传入的安全摘要"})
            dialog.online_checkbox.click()
            dialog.refresh_button.click()
            self.allow_session(dialog)
            dialog.cloud_button.setChecked(True)
            dialog.input.setText("仅发出信号")
            dialog.send()
            dialog.show_reply("宿主传入的回复")
            dialog.context_before_current()
            dialog.set_compact(False)
            dialog.close()


if __name__ == "__main__":
    unittest.main()
