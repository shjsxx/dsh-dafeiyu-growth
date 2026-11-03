"""Offscreen tests for the passive, privacy-safe growth training journal.

Run with the Hermes Python interpreter and QT_QPA_PLATFORM=offscreen.
No persisted journal, model, network access, or integration changes are needed.
"""
import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from training_journal import DOMAINS, KINDS, LIMIT, METRICS, SOURCES
from training_ui import PAGE_SIZE, TrainingPanel


def event(event_id=1, **changes):
    row = {
        "id": event_id, "started": 1793451600, "finished": 1793451602,
        "kind": "action_fit", "domain": "action", "source": "local_outcome",
        "status": "success", "metrics": {"loss_before": 1.4, "loss_after": .9,
        "probability_before": .25, "probability_after": .4, "weight_steps": 2},
        "labels": {"action": "run"}, "error": "",
    }
    row.update(changes)
    return row


def data(rows=None, revision=1, **changes):
    rows = [event()] if rows is None else rows
    info = {
        "journal": {"status": "ready", "revision": revision, "total": len(rows),
                    "totals": {"runs": 90, "failed": 2, "weight_steps": 180,
                               "emotion_adaptation_passes": 7, "knowledge_added": 8},
                    "retained": len(rows), "limit": LIMIT, "events": rows},
        "knowledge": {"count": 42, "domains": {"geography": 17, "computing": 25}},
        "brain": {"samples": 50, "updates": 20, "version": 3, "status": "ready"},
    }
    info.update(changes)
    return info


class TrainingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = TrainingPanel()

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def visible_text(self):
        parts = [label.text() + "\n" + label.toolTip() for label in self.panel.findChildren(QLabel)]
        parts.extend(self.panel.history_list.item(i).text() for i in range(self.panel.history_list.count()))
        parts.append(self.panel.detail_view.toPlainText())
        return "\n".join(parts)

    def ids(self):
        return [self.panel.history_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.panel.history_list.count())]

    def show_panel(self, view="记录"):
        self.panel.view_buttons[view].click()
        self.panel.resize(400, 435)
        self.panel.show()
        self.app.processEvents()

    def test_default_empty_bounded_readonly_and_nonmodal(self):
        self.assertFalse(self.panel.isVisible())
        self.assertEqual(self.panel.history_list.count(), 0)
        self.assertTrue(self.panel.detail_view.isReadOnly())
        self.assertEqual(self.panel.history_list.minimumHeight(), 150)
        self.assertEqual(self.panel.history_list.maximumHeight(), 150)
        self.assertEqual(self.panel.detail_view.minimumHeight(), 110)
        self.assertEqual(self.panel.detail_view.maximumHeight(), 110)
        self.assertFalse(self.panel.older_button.isEnabled())
        self.assertFalse(self.panel.newer_button.isEnabled())
        self.assertIn("暂无匹配记录", self.panel.detail_view.toPlainText())

    def test_malformed_info_is_safe(self):
        for malformed in (None, False, 4, "bad", [], {"journal": []},
                          {"journal": {"revision": [], "totals": [], "events": [None, [], 4]}},
                          {"journal": {"revision": float("nan"), "events": {}}, "brain": [], "knowledge": []},
                          {"journal": {"status": [], "revision": True, "total": True},
                           "brain": {"status": {}}, "knowledge": {"count": float("inf"), "domains": []},
                           "activeSource": {}}):
            with self.subTest(malformed=repr(malformed)):
                self.panel.update_data(malformed)
        self.assertEqual(self.panel.history_list.count(), 0)
        self.assertIn("知识库 0 条", self.panel.knowledge_label.text())

    def test_malformed_event_fields_are_rejected(self):
        rows = [None, "text", [], {}, event(1, kind=[]), event(2, domain={}),
                event(3, source=[]), event(4, status={}), event(5, id=True),
                event(6, id=-1), event(7, id=2.5), event(8, id=10 ** 400),
                event(9, id=float("nan")), event(10, started=[], finished={}, metrics=[], labels=[])]
        self.panel.update_data(data(rows))
        self.assertEqual(self.ids(), [10])
        self.assertIn("开始：—", self.panel.detail_view.toPlainText())

    def test_summary_shows_audit_weight_knowledge_and_brain_facts(self):
        self.panel.update_data(data())
        self.assertIn("已记录 90 次事件 · 失败 2", self.panel.summary_label.text())
        self.assertEqual(self.panel.pulse_values["action"].text(), "180")
        self.assertEqual(self.panel.pulse_values["emotion"].text(), "7")
        self.assertEqual(self.panel.pulse_values["knowledge"].text(), "42")
        self.assertIn("动作 SGD", self.panel.pulse_captions["action"].text())
        self.assertIn("情感适应", self.panel.pulse_captions["emotion"].text())
        self.assertIn("动作 SGD 180 步", self.panel.summary_label.toolTip())
        self.assertIn("情感适应 7 次", self.panel.summary_label.toolTip())
        self.assertIn("非反向传播", self.panel.summary_label.toolTip())
        self.assertIn("累计新增知识 8 条", self.panel.summary_label.toolTip())
        self.assertIn("不伪造旧训练", self.panel.summary_label.toolTip())
        self.assertIn("知识库 42 条", self.panel.knowledge_label.text())
        self.assertIn("地理 17", self.panel.knowledge_label.text())
        self.assertIn("计算机 25", self.panel.knowledge_label.text())
        self.assertIn("样本 50 · 更新 20 · v3 · 就绪", self.panel.brain_label.text())

    def test_evaluation_panel_shows_emotion_holdout_and_action_diversity(self):
        info = data()
        info["brain"].update(diversity=.72, avg_reward=-.1, positive_ratio=.6)
        info["evaluation"] = {"accuracy": .709, "balanced_accuracy": .651,
                              "macro_f1": .68, "support": 461}
        self.panel.update_data(info)
        self.assertIn("准确率 71%", self.panel.evaluation_label.text())
        self.assertIn("macro F1 0.68", self.panel.evaluation_label.text())
        self.assertIn("多样性 0.72", self.panel.brain_label.text())
        self.assertIn("均奖励 -0.10", self.panel.brain_label.text())

    def test_hostile_strings_and_private_fields_are_never_displayed(self):
        secret = '<img src="file:///PRIVATE" onerror="upload()">PRIVATE_API_KEY\nhttps://secret.invalid'
        row = event(metrics={"loss_after": .6, "upload": secret, "reward": secret},
                    labels={"action": secret, "private": secret}, error=secret,
                    prompt=secret, logs=secret, upload=secret, private=secret)
        info = data([row], activeSource=secret,
                    brain={"samples": secret, "status": secret, "error": secret, "private": secret},
                    knowledge={"count": 3, "domains": {secret: 99, "geography": 3}, "facts": secret})
        info["journal"]["error"] = secret
        self.panel.update_data(info)
        self.assertEqual(self.panel.history_list.count(), 1)
        text = self.visible_text()
        for forbidden in ("PRIVATE", "upload", "secret.invalid", "file:///", "<img"):
            self.assertNotIn(forbidden, text)
        self.assertIn("本地练习结果", text)
        self.assertTrue(self.panel.source_label.isHidden())
        for label in self.panel.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(self.panel.history_list.editTriggers(), self.panel.history_list.EditTrigger.NoEditTriggers)

    def test_unknown_source_kind_domain_and_status_cannot_leak(self):
        hostile = "<b>PRIVATE_LOG</b>"
        rows = [event(1, source=hostile), event(2, kind=hostile),
                event(3, domain=hostile), event(4, status=hostile)]
        self.panel.update_data(data(rows))
        self.assertEqual(self.panel.history_list.count(), 0)
        self.assertNotIn("PRIVATE_LOG", self.visible_text())

    def test_active_source_uses_enum_labels_only(self):
        self.panel.update_data(data(activeSource="cldr_zh"))
        self.assertEqual(self.panel.source_label.text(), "当前来源：" + SOURCES["cldr_zh"])
        self.panel.update_data(data(activeSource="https://private.invalid"))
        self.assertTrue(self.panel.source_label.isHidden())
        self.assertEqual(self.panel.source_label.text(), "")

    def test_pagination_is_newest_first_thirty_and_older_newer(self):
        self.panel.update_data(data([event(i) for i in range(75, 0, -1)]))
        self.assertEqual(self.ids(), list(range(75, 45, -1)))
        self.assertIn("1 / 3 · 75 条", self.panel.page_label.text())
        self.assertFalse(self.panel.newer_button.isEnabled())
        self.panel.older_button.click()
        self.assertEqual(self.ids(), list(range(45, 15, -1)))
        self.panel.older_button.click()
        self.assertEqual(self.ids(), list(range(15, 0, -1)))
        self.assertFalse(self.panel.older_button.isEnabled())
        self.panel.newer_button.click()
        self.assertEqual(self.ids()[0], 45)
        self.assertLessEqual(self.panel.history_list.count(), PAGE_SIZE)

    def test_domain_and_kind_filters_compose_and_reset_page(self):
        rows = [event(i, domain="emotion" if i % 2 else "action",
                      kind="emotion_fit" if i % 3 else "emotion_read", source="local_chat")
                for i in range(100, 0, -1)]
        self.panel.update_data(data(rows))
        self.panel.older_button.click()
        self.panel.domain_filter.setCurrentIndex(self.panel.domain_filter.findData("emotion"))
        self.assertIn("1 / 2 · 50 条", self.panel.page_label.text())
        self.assertTrue(all(i % 2 for i in self.ids()))
        self.panel.kind_filter.setCurrentIndex(self.panel.kind_filter.findData("emotion_read"))
        self.assertTrue(all(i % 2 and i % 3 == 0 for i in self.ids()))
        self.assertIn("1 / 1 · 17 条", self.panel.page_label.text())
        self.panel.domain_filter.setCurrentIndex(self.panel.domain_filter.findData("astronomy"))
        self.assertEqual(self.ids(), [])
        self.assertIn("暂无匹配记录", self.panel.detail_view.toPlainText())

    def test_same_revision_preserves_items_selection_and_both_scrolls(self):
        info = data([event(i) for i in range(90, 0, -1)])
        self.panel.update_data(info)
        self.show_panel()
        self.panel.history_list.setCurrentRow(20)
        self.panel.history_list.verticalScrollBar().setValue(77)
        self.panel.detail_view.verticalScrollBar().setValue(2)
        cursor = self.panel.detail_view.textCursor()
        cursor.setPosition(3)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 5)
        self.panel.detail_view.setTextCursor(cursor)
        original = self.panel.history_list.item(20)
        list_scroll = self.panel.history_list.verticalScrollBar().value()
        detail_scroll = self.panel.detail_view.verticalScrollBar().value()
        selection = self.panel.detail_view.textCursor().selectedText()
        updated = copy.deepcopy(info)
        updated["knowledge"]["count"] = 99
        updated["activeSource"] = "countries"
        self.panel.update_data(updated)
        self.assertIs(self.panel.history_list.item(20), original)
        self.assertEqual(self.panel.history_list.currentRow(), 20)
        self.assertEqual(self.panel.history_list.verticalScrollBar().value(), list_scroll)
        self.assertEqual(self.panel.detail_view.verticalScrollBar().value(), detail_scroll)
        self.assertEqual(self.panel.detail_view.textCursor().selectedText(), selection)
        self.assertIn("知识库 99 条", self.panel.knowledge_label.text())

    def test_same_revision_is_authoritative_even_for_changed_event_payload(self):
        self.panel.update_data(data())
        original = self.panel.history_list.item(0)
        self.panel.update_data(data([event(2)], revision=1))
        self.assertIs(self.panel.history_list.item(0), original)
        self.assertEqual(self.ids(), [1])

    def test_new_revision_with_unchanged_events_does_not_rebuild(self):
        self.panel.update_data(data())
        original = self.panel.history_list.item(0)
        self.panel.update_data(data(revision=2))
        self.assertIs(self.panel.history_list.item(0), original)

    def test_new_events_preserve_selection_across_page_boundary(self):
        rows = [event(i) for i in range(60, 0, -1)]
        self.panel.update_data(data(rows))
        self.panel.history_list.setCurrentRow(29)
        self.assertEqual(self.panel.history_list.currentItem().data(Qt.ItemDataRole.UserRole), 31)
        self.panel.update_data(data([event(61)] + rows, revision=2))
        self.assertEqual(self.panel.history_list.currentItem().data(Qt.ItemDataRole.UserRole), 31)
        self.assertIn("2 / 3", self.panel.page_label.text())
        self.assertIn("记录 #31", self.panel.detail_view.toPlainText())

    def test_selected_record_details_update_on_new_revision(self):
        self.panel.update_data(data([event(status="running", finished=0, metrics={})]))
        self.assertIn("进行中", self.panel.detail_view.toPlainText())
        self.panel.update_data(data([event()], revision=2))
        self.assertIn("状态：完成", self.panel.detail_view.toPlainText())
        self.assertIn("0.9", self.panel.detail_view.toPlainText())

    def test_removed_selection_and_shortened_history_are_safe(self):
        self.panel.update_data(data([event(i) for i in range(70, 0, -1)]))
        self.panel.older_button.click()
        self.panel.older_button.click()
        self.panel.update_data(data([event(999)], revision=2))
        self.assertEqual(self.ids(), [999])
        self.assertIn("1 / 1", self.panel.page_label.text())
        self.assertIn("记录 #999", self.panel.detail_view.toPlainText())

    def test_details_include_times_source_type_status_and_honest_loss_note(self):
        self.panel.update_data(data())
        text = self.panel.detail_view.toPlainText()
        for expected in ("开始：", "结束：", "来源：本地练习结果", "类型：动作网络", "状态：完成",
                         "本次样本目标交叉熵", "不是测试准确率", "动作偏好", "不代表实际成功率",
                         "1.4 → 0.9", "0.25 → 0.4", "动作：跑动"):
            self.assertIn(expected, text)

    def test_kind_specific_disclaimers_are_honest(self):
        expectations = {
            "emotion_fit": ("不是情绪质量", "不代表泛化准确率提升"),
            "emotion_read": ("仅观察，未训练", "不是情绪质量"),
            "public_lexicon": ("公开词典的下载与收录", "不是神经网络训练"),
            "knowledge_index": ("下载、校验与索引", "不是神经网络训练"),
            "companion_adapt": ("模拟陪伴状态更新", "不是真实情绪"),
            "control": ("不是训练提升", "回滚不代表学得更好"),
        }
        for revision, (kind, notes) in enumerate(expectations.items(), 1):
            with self.subTest(kind=kind):
                self.panel.update_data(data([event(kind=kind)], revision=revision))
                for note in notes:
                    self.assertIn(note, self.panel.detail_view.toPlainText())

    def test_all_schema_metrics_and_labels_are_supported(self):
        metrics = {key: 1.25 for key in METRICS}
        row = event(kind="companion_adapt", metrics=metrics,
                    labels={"action": "rest", "emotion": "calm", "own_mood": "curious"})
        self.panel.update_data(data([row]))
        text = self.panel.detail_view.toPlainText()
        for expected in ("情绪分类置信度（不是质量）", "模拟愉悦度", "权重更新步数", "用时（毫秒）",
                         "动作：休息", "情绪标签：平静", "模拟状态：好奇"):
            self.assertIn(expected, text)

    def test_invalid_metrics_and_nonfinite_timestamps_are_ignored(self):
        row = event(metrics={"loss_before": float("nan"), "loss_after": float("inf"),
                             "reward": True, "weight_steps": "secret", "duration_ms": 10 ** 400,
                             "probability_after": .7}, started=float("inf"), finished=10 ** 400)
        self.panel.update_data(data([row]))
        text = self.panel.detail_view.toPlainText()
        self.assertIn("动作偏好概率：— → 0.7", text)
        self.assertNotIn("nan", text)
        self.assertNotIn("inf", text)
        self.assertNotIn("secret", text)
        self.assertIn("开始：—", text)

    def test_corruption_and_save_errors_warn_without_echoing_raw_errors(self):
        info = data()
        info["journal"].update(status="protected", error="PRIVATE_ERROR")
        self.panel.update_data(info)
        self.assertIn("日志损坏或不可读", self.panel.warning_label.text())
        self.assertFalse(self.panel.learning_button.isEnabled())
        self.assertNotIn("PRIVATE_ERROR", self.visible_text())
        info["journal"]["status"] = "error"
        self.panel.update_data(info)
        self.assertIn("日志无法保存", self.panel.warning_label.text())

    def test_readonly_and_brain_protection_warning(self):
        info = data()
        info["journal"]["read_only"] = True
        self.panel.update_data(info)
        self.assertIn("只读预览", self.panel.warning_label.text())
        self.assertFalse(self.panel.learning_button.isEnabled())
        info["journal"].pop("read_only")
        info["journal"]["status"] = "read_only"
        self.panel.update_data(info)
        self.assertFalse(self.panel.learning_button.isEnabled())
        info["journal"]["status"] = "ready"
        info["brain"]["status"] = "protected"
        self.panel.update_data(info)
        self.assertIn("动作模型只读或受保护", self.panel.warning_label.text())
        self.assertTrue(self.panel.learning_button.isEnabled())  # Other scheduler domains may still work.

    def test_busy_update_disables_learning_even_when_journal_revision_is_unchanged(self):
        payload = data()
        self.panel.update_data(payload)
        self.assertTrue(self.panel.learning_button.isEnabled())
        payload['busy'] = True
        self.panel.update_data(payload)
        self.assertFalse(self.panel.learning_button.isEnabled())
        self.assertEqual(self.panel.learning_button.text(), '学习中…')
        payload['busy'] = False
        self.panel.update_data(payload)
        self.assertTrue(self.panel.learning_button.isEnabled())

    def test_only_explicit_button_emits_learning_requested(self):
        spy = QSignalSpy(self.panel.learning_requested)
        self.panel.update_data(data([event(i) for i in range(65, 0, -1)]))
        self.panel.update_data(data([event(i) for i in range(65, 0, -1)], revision=2))
        self.panel.older_button.click()
        self.panel.newer_button.click()
        self.panel.domain_filter.setCurrentIndex(self.panel.domain_filter.findData("action"))
        self.panel.history_list.setCurrentRow(2)
        self.assertEqual(spy.count(), 0)
        self.assertEqual(self.panel.learning_button.text(), "现在学习")
        self.panel.learning_button.click()
        self.assertEqual(spy.count(), 1)
        self.panel.learning_button.click()
        self.assertEqual(spy.count(), 2)

    def test_construct_and_update_have_no_file_network_or_journal_side_effects(self):
        with patch("builtins.open", side_effect=AssertionError("file access")), \
                patch("pathlib.Path.read_text", side_effect=AssertionError("read")), \
                patch("pathlib.Path.write_text", side_effect=AssertionError("write")), \
                patch("socket.socket", side_effect=AssertionError("network")), \
                patch("training_journal.TrainingJournal", side_effect=AssertionError("journal construction")):
            panel = TrainingPanel()
            spy = QSignalSpy(panel.learning_requested)
            panel.update_data(data(sourceCatalog=[{"id": "stackexchange", "status": "connected"}],
                                   platformSources=[{"id": "zhihu_public", "status": "connected"}]))
            panel.view_buttons["来源"].click()
            self.assertEqual(panel.source_status_labels["stackexchange"].text(), "已连接")
            self.assertIn("知乎公开源：未连接", panel.platform_label.text())
            self.assertEqual(spy.count(), 0)
            self.assertFalse(panel.isVisible())
            panel.deleteLater()

    def test_updates_do_not_mutate_caller_snapshot(self):
        info = data(sourceCatalog=[{"id": "stackexchange", "status": "available", "name": "PRIVATE"}],
                    platformSources=[{"id": "zhihu_public", "status": "PRIVATE"}])
        original = copy.deepcopy(info)
        self.panel.update_data(info)
        self.panel.history_list.setCurrentRow(0)
        self.assertEqual(info, original)
        info["journal"]["events"][0]["metrics"]["loss_after"] = 999
        info["sourceCatalog"][0]["status"] = "connected"
        self.assertNotIn("999", self.panel.detail_view.toPlainText())
        self.assertEqual(self.panel.source_status_labels["stackexchange"].text(), "可读取")

    def test_duplicate_ids_and_excess_events_are_bounded(self):
        self.panel.update_data(data([event(1), event(1), event(2)]))
        self.assertEqual(self.ids(), [1, 2])
        self.panel.update_data(data([event(i) for i in range(LIMIT + 100, 0, -1)], revision=2))
        self.assertEqual(self.panel.history_list.count(), PAGE_SIZE)
        self.assertIn(f"{LIMIT} 条", self.panel.page_label.text())
        self.assertEqual(self.panel.history_list.height(), 150)
        self.assertEqual(self.panel.detail_view.height(), 110)

    def test_all_segments_fit_growth_viewport_without_outer_scroll(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.resize(410, 435)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)
        scroll.setWidget(content)
        payload = data([event(i) for i in range(300, 0, -1)], activeSource="local_chat")
        payload["knowledge"]["domains"] = {key: 12345 for key in DOMAINS}
        payload["journal"].update(status="protected", read_only=True)
        payload["brain"]["status"] = "protected"
        self.panel.update_data(payload)
        scroll.show()
        self.app.processEvents()
        for title in ("总览", "记录", "来源"):
            with self.subTest(view=title):
                self.panel.view_buttons[title].click()
                self.app.processEvents()
                self.assertLessEqual(self.panel.minimumSizeHint().height(), 435)
                self.assertLess(self.panel.sizeHint().height(), 450)
                self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                self.assertEqual(self.panel.history_list.height(), 150)
                self.assertEqual(self.panel.detail_view.height(), 110)
                page = self.panel.view_stack.currentWidget()
                self.assertLessEqual(page.geometry().bottom(), self.panel.height())
        self.panel.setParent(None)
        scroll.close()
        scroll.deleteLater()

    def test_segmented_views_default_to_overview_and_only_show_one_page(self):
        self.assertIsInstance(self.panel.view_stack, QStackedWidget)
        self.assertEqual(self.panel.view_stack.count(), 3)
        self.assertEqual(list(self.panel.view_buttons), ["总览", "记录", "来源"])
        self.assertIs(self.panel.view_stack.currentWidget(), self.panel.overview_page)
        self.show_panel("总览")
        self.assertTrue(self.panel.pulse_values["action"].isVisible())
        self.assertFalse(self.panel.history_list.isVisible())
        self.assertFalse(self.panel.platform_label.isVisible())
        for title, page in (("记录", self.panel.records_page), ("来源", self.panel.sources_page)):
            self.panel.view_buttons[title].click()
            self.app.processEvents()
            self.assertIs(self.panel.view_stack.currentWidget(), page)
            self.assertEqual(sum(button.isChecked() for button in self.panel.view_buttons.values()), 1)
        self.assertFalse(self.panel.history_list.isVisible())
        self.assertTrue(self.panel.platform_label.isVisible())
        self.panel.view_stack.setCurrentIndex(0)
        self.assertTrue(self.panel.view_buttons["总览"].isChecked())

    def test_segment_switch_preserves_record_selection_scroll_and_emits_nothing(self):
        self.panel.update_data(data([event(i) for i in range(90, 0, -1)]))
        self.show_panel()
        self.panel.history_list.setCurrentRow(20)
        self.panel.history_list.verticalScrollBar().setValue(77)
        self.panel.detail_view.verticalScrollBar().setValue(2)
        original = self.panel.history_list.item(20)
        list_scroll = self.panel.history_list.verticalScrollBar().value()
        detail_scroll = self.panel.detail_view.verticalScrollBar().value()
        self.assertGreater(list_scroll, 0)
        self.assertGreater(detail_scroll, 0)
        spy = QSignalSpy(self.panel.learning_requested)
        for title in ("来源", "总览", "记录"):
            self.panel.view_buttons[title].click()
            self.app.processEvents()
        self.assertIs(self.panel.history_list.currentItem(), original)
        self.assertEqual(self.panel.history_list.verticalScrollBar().value(), list_scroll)
        self.assertEqual(self.panel.detail_view.verticalScrollBar().value(), detail_scroll)
        self.assertEqual(spy.count(), 0)

    def test_metric_tiles_count_committed_steps_passes_and_current_index_not_events(self):
        rows = [event(5), event(4, kind="emotion_read"), event(3, kind="emotion_fit"),
                event(2, kind="companion_adapt"), event(1, kind="companion_adapt", status="failed")]
        self.panel.update_data(data(rows))
        self.assertEqual({key: label.text() for key, label in self.panel.pulse_values.items()},
                         {"action": "180", "emotion": "7", "knowledge": "42", "companion": "1"})
        self.assertIn("已提交步数", self.panel.pulse_captions["action"].text())
        self.assertIn("累计轮次", self.panel.pulse_captions["emotion"].text())
        self.assertIn("当前条目", self.panel.pulse_captions["knowledge"].text())
        self.assertIn("近期", self.panel.pulse_captions["companion"].text())
        payload = data(rows, revision=2)
        payload["journal"]["totals"] = {}
        self.panel.update_data(payload)
        self.assertEqual(self.panel.pulse_values["action"].text(), "0")
        self.assertEqual(self.panel.pulse_values["emotion"].text(), "0")

    def test_metric_tiles_reject_nonfinite_private_and_boolean_counts(self):
        payload = data(knowledge={"count": float("inf")})
        payload["journal"]["totals"].update(weight_steps=True, emotion_adaptation_passes="PRIVATE")
        self.panel.update_data(payload)
        for key in ("action", "emotion", "knowledge"):
            self.assertEqual(self.panel.pulse_values[key].text(), "0")
        self.assertNotIn("PRIVATE", self.visible_text())
        payload["journal"]["totals"].update(weight_steps=10 ** 13, emotion_adaptation_passes=10 ** 400)
        self.panel.update_data(payload)
        self.assertEqual(self.panel.pulse_values["action"].text(), str(10 ** 13))
        self.assertTrue(self.panel.pulse_values["action"].property("compactValue"))
        self.assertEqual(self.panel.pulse_values["emotion"].text(), "0")

    def test_recent_public_import_totals_use_successful_added_metrics_only(self):
        rows = [
            event(9, kind="knowledge_index", source="countries", metrics={"added": 4}),
            event(8, kind="knowledge_index", source="cldr_units", metrics={"added": 6}),
            event(7, kind="knowledge_index", source="mime_db", metrics={"added": 100}, status="failed"),
            event(6, kind="knowledge_index", source="countries", metrics={"added": 50}, status="no_change"),
            event(5, kind="knowledge_index", source="core_curriculum", metrics={"added": 90}),
            event(4, kind="public_lexicon", source="cldr_zh", metrics={"added": 3}),
            event(3, kind="public_lexicon", source="cldr_zh", metrics={"added": 80}, status="failed"),
            event(2, kind="public_lexicon", source="cldr_zh", metrics={"added": True}),
            event(1, kind="knowledge_index", source="countries", metrics={"added": float("nan")}),
        ]
        self.panel.update_data(data(rows))
        self.assertEqual(self.panel.public_import_label.text(), "近期公开收录 10 条 · 词典新增 3 条")
        self.panel.update_data(data([event(100, kind="knowledge_index", source="countries", metrics={"added": 99})]))
        self.assertEqual(self.panel.public_import_label.text(), "近期公开收录 10 条 · 词典新增 3 条")

    def test_stackexchange_records_use_fixed_journal_source_and_actual_added_metrics(self):
        # The owner adds this enum with its API adapter; catalog UI never expands
        # the journal's accepted sources by itself.
        with patch.dict(SOURCES, {"stackexchange": "Stack Exchange 官方公开 API"}):
            row = event(1, kind="knowledge_index", source="stackexchange", metrics={"added": 5})
            self.panel.update_data(data([row]))
            self.assertEqual(self.ids(), [1])
            self.assertIn("来源：Stack Exchange 官方公开 API", self.panel.detail_view.toPlainText())
            self.assertEqual(self.panel.public_import_label.text(), "近期公开收录 5 条 · 词典新增 0 条")
            self.assertEqual(self.panel.source_status_labels["stackexchange"].text(), "已收录")

    def test_source_catalog_has_fixed_names_and_does_not_assume_connection(self):
        expected = {"cldr_zh", "core_curriculum", "countries", "cldr_units", "mime_db", "stackexchange"}
        self.assertEqual(set(self.panel.source_status_labels), expected)
        self.assertEqual({label.text() for label in self.panel.source_status_labels.values()}, {"未校验"})
        self.assertIn("Stack Exchange", self.visible_text())
        self.panel.update_data(data(sourceCatalog=[
            {"id": "cldr_zh", "status": "error"}, {"id": "core_curriculum", "status": "indexed"},
            {"id": "countries", "status": "indexed"}, {"id": "cldr_units", "status": "available"},
            {"id": "mime_db", "status": "disabled"}, {"id": "stackexchange", "status": "pending"},
        ]))
        self.assertEqual({key: label.text() for key, label in self.panel.source_status_labels.items()}, {
            "cldr_zh": "暂不可用", "core_curriculum": "已收录", "countries": "已收录",
            "cldr_units": "可读取", "mime_db": "已停用", "stackexchange": "待启用"})
        self.assertIn("已收录 2 / 6 项", self.panel.coverage_label.text())

    def test_source_catalog_and_legacy_platform_fields_cannot_echo_private_text(self):
        secret = '<img src="file:///PRIVATE_TOKEN">https://secret.invalid'
        payload = data(sourceCatalog=[
            {"id": "stackexchange", "status": "available", "name": secret, "description": secret, "url": secret},
            {"id": "countries", "status": secret, "name": secret},
            {"id": secret, "status": "connected"}, {"id": [], "status": {}},
            {"id": "zhihu_public", "status": "connected", "name": secret},
            {"id": "short_video_public", "status": "indexed", "name": secret},
        ], platformSources=[
            {"id": "zhihu_public", "status": secret, "name": secret},
            {"id": "short_video_public", "status": "已连接", "name": secret},
        ])
        self.panel.update_data(payload)
        self.assertEqual(self.panel.source_status_labels["stackexchange"].text(), "可读取")
        self.assertEqual(self.panel.source_status_labels["countries"].text(), "未校验")
        self.assertEqual(self.panel.platform_label.text(), "知乎公开源：未连接  ·  短视频公开元数据：未连接")
        for forbidden in ("PRIVATE", "secret.invalid", "<img", "file:///", "已连接"):
            self.assertNotIn(forbidden, self.visible_text())
        payload["platformSources"] = [{"id": "zhihu_public", "status": "connected"}]
        self.panel.update_data(payload)
        self.assertIn("知乎公开源：未连接", self.panel.platform_label.text())

    def test_source_catalog_malformed_duplicates_and_oversize_are_bounded(self):
        for malformed in (None, False, "PRIVATE", {}, [None, 4, [], {"id": {}, "status": []}]):
            self.panel.update_data(data(sourceCatalog=malformed, platformSources=malformed))
            self.assertNotIn("PRIVATE", self.visible_text())
        self.panel.update_data(data(sourceCatalog=[
            {"id": "countries", "status": "indexed"}, {"id": "countries", "status": "connected"},
        ]))
        self.assertEqual(self.panel.source_status_labels["countries"].text(), "已收录")
        self.panel.update_data(data(sourceCatalog=[{}] * 32 + [{"id": "countries", "status": "connected"}]))
        self.assertEqual(self.panel.source_status_labels["countries"].text(), "未校验")

    def test_catalog_fallback_uses_only_fixed_existing_indexes_or_public_success(self):
        payload = data([event(1, kind="public_lexicon", source="cldr_zh", status="no_change")],
                       knowledge={"sources": {"countries": {}, "PRIVATE": {}, "stackexchange": {}}})
        self.panel.update_data(payload)
        for key in ("countries", "stackexchange", "cldr_zh"):
            self.assertEqual(self.panel.source_status_labels[key].text(), "已收录")
        self.assertEqual(self.panel.source_status_labels["mime_db"].text(), "未校验")
        self.assertNotIn("PRIVATE", self.visible_text())
        payload["sourceCatalog"] = [{"id": "countries", "status": "error"}]
        self.panel.update_data(payload)
        self.assertEqual(self.panel.source_status_labels["countries"].text(), "暂不可用")

    def test_filter_controls_only_contain_schema_enums(self):
        self.assertEqual(self.panel.domain_filter.count(), len(DOMAINS) + 1)
        self.assertEqual(self.panel.kind_filter.count(), len(KINDS) + 1)
        self.assertEqual({self.panel.domain_filter.itemData(i) for i in range(1, self.panel.domain_filter.count())}, set(DOMAINS))
        self.assertEqual({self.panel.kind_filter.itemData(i) for i in range(1, self.panel.kind_filter.count())}, set(KINDS))


if __name__ == "__main__":
    unittest.main()
