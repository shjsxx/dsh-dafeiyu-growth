"""Self-contained offscreen tests; no persisted stores, models, or network.

Run: Hermes Python -B -m unittest -v test_data_panel
"""
import copy
import importlib
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

import data_panel
from data_panel import DataPanel, DOMAIN_TEXT, EVENT_LIMIT, MAX_COUNTER, SOURCE_TEXT


def event(event_id=1, **changes):
    row = {"id": event_id, "started": 100 + event_id, "finished": 200 + event_id,
           "kind": "knowledge_index", "domain": "geography", "source": "countries",
           "status": "success", "metrics": {"added": 22, "validated": 30, "rejected": 8}}
    row.update(changes)
    return row


def snapshot(rows=None):
    return {
        "emotion": {"revision": 7, "fit_samples": 13, "adaptation_passes": 9,
                    "lexicon": 48, "features": 130, "events": 56, "learned": 11, "status": "ready"},
        "knowledge": {"count": 42, "revision": 3, "status": "ready",
                      "domains": {"geography": 17, "computing": 20, "science": 5}},
        "training": {"journal": {"revision": 1, "events": [event()] if rows is None else rows,
                                 "totals": {"runs": 90, "failed": 2, "weight_steps": 180,
                                            "emotion_adaptation_passes": 6, "knowledge_added": 77,
                                            "interrupted_unknown": 1}},
                     "brain": {"samples": 50, "updates": 20, "version": 3, "status": "ready"}},
        "sourceCatalog": [{"id": key, "status": state} for key, state in zip(SOURCE_TEXT,
                          ("indexed", "indexed", "indexed", "available", "disabled", "unconnected"))],
        "platformSources": [{"id": "zhihu_public", "status": "pending"},
                            {"id": "short_video_public", "status": "pending"}],
        "companion": {"own_mood": "curious", "name": "unused", "reason": "unused"},
    }


class DataPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = DataPanel()

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def text(self, tooltips=False):
        labels = self.panel.findChildren(QLabel)
        parts = [label.text() for label in labels]
        if tooltips:
            parts.extend(widget.toolTip() for widget in [self.panel] + self.panel.findChildren(QWidget))
        return "\n".join(parts)

    def test_default_is_compact_passive_and_self_styled(self):
        self.assertFalse(self.panel.isVisible())
        self.assertEqual(self.panel.views.currentIndex(), 0)
        self.assertTrue(self.panel.knowledge_button.isChecked())
        self.assertEqual(self.panel.hero_values["knowledge"].text(), "0")
        self.assertIn("暂无已结束", self.panel.recent_label.text())
        self.assertIn("已收录 0/6", self.panel.source_count_label.text())
        self.assertIn("#172F49", self.panel.styleSheet())
        self.assertIn("#dataPanel", self.panel.styleSheet())
        self.assertNotIn("trainingPanel", self.panel.styleSheet())
        self.assertFalse(hasattr(self.panel, "learning_requested"))
        for label in self.panel.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_knowledge_accepted_count_revision_and_actual_domain_distribution(self):
        self.panel.update_data(snapshot())
        self.assertEqual(self.panel.hero_values["knowledge"].text(), "42")
        self.assertIn("已收录 42 条", self.panel.knowledge_label.text())
        self.assertIn("r3", self.panel.knowledge_revision_label.text())
        self.assertIn("就绪", self.panel.knowledge_revision_label.text())
        self.assertEqual(self.panel.domain_labels["geography"].text(), "地理  17")
        self.assertEqual(self.panel.domain_labels["computing"].text(), "计算机  20")
        self.assertEqual(self.panel.domain_labels["astronomy"].text(), "天文  0")
        self.assertEqual(self.panel.domain_bars["geography"].value(), 17 * 1000 // 42)
        self.assertEqual(self.panel.domain_bars["computing"].value(), 20 * 1000 // 42)
        self.assertEqual(set(self.panel.domain_labels), set(DOMAIN_TEXT))
        self.assertIn("非训练", self.panel.knowledge_label.text())

    def test_emotion_model_metrics_not_heuristic_mood_or_confidence(self):
        info = snapshot()
        info["emotion"].update(name="PRIVATE_NAME", reason="PRIVATE_REASON", confidence=.99)
        self.panel.update_data(info)
        self.panel.emotion_button.click()
        self.assertEqual(self.panel.views.currentIndex(), 1)
        self.assertIn("r7 · 就绪", self.panel.emotion_revision_label.text())
        expected = {"fit_samples": "13", "adaptation_passes": "9", "lexicon": "48",
                    "features": "130", "events": "56", "learned": "11"}
        self.assertEqual({key: label.text() for key, label in self.panel.emotion_values.items()}, expected)
        self.assertEqual(self.panel.hero_values["emotion"].text(), "13")
        self.assertNotIn("PRIVATE", self.text(True))
        self.assertNotIn("99%", self.text(True))
        self.assertIn("不是准确率", self.text(True))
        self.assertIn("不是反向传播", self.text(True))

    def test_all_training_totals_are_distinct_and_action_model_metrics_are_whitelisted(self):
        self.panel.update_data(snapshot())
        self.assertEqual(self.panel.hero_values["training"].text(), "90")
        self.assertEqual(self.panel.training_totals_label.text(), "动作 SGD 180 步 · 情感适应 6 次 · 失败 2 · 未知 1")
        tooltip = self.panel.training_totals_label.toolTip()
        self.assertIn("样本 50 · 更新 20 · v3 · 就绪", tooltip)
        self.assertIn("累计知识新增 77 条", tooltip)
        self.assertIn("均不是准确率", tooltip)

    def test_recent_public_ingest_is_finalized_newest_first_and_committed_only(self):
        rows = [event(1), event(2, source="cldr_zh", kind="public_lexicon", domain="emotion",
                               metrics={"added": 5, "changed": 2, "validated": 6, "rejected": 1}),
                event(3, status="failed", metrics={"added": 999, "changed": 888, "validated": 4, "rejected": 3}),
                event(4, status="running"), event(5, status="queued"),
                event(6, source="local_chat", kind="emotion_fit", domain="emotion"),
                event(7, status="success", finished=0)]
        self.panel.update_data(snapshot(rows))
        self.assertEqual(self.panel.ingest_count_label.text(), "留存已结束 3 次")
        self.assertEqual(self.panel.ingest_totals_label.text(), "新增 27 · 变更 2 · 校验 40 · 拒绝 12")
        self.assertEqual(self.panel.recent_label.text().splitlines(), ["国家资料 · 失败 · 不计新增", "表情词典 · 完成 · 新增 5"])
        self.assertNotIn("999", self.text(True))
        self.assertNotIn("888", self.text(True))
        self.assertIn("非终身总数", self.panel.ingest_totals_label.toolTip())

    def test_no_change_cancelled_and_interrupted_do_not_claim_new_acceptance(self):
        self.panel.update_data(snapshot([event(1, status="no_change"), event(2, status="cancelled"),
                                         event(3, status="interrupted")]))
        self.assertIn("留存已结束 3 次", self.panel.ingest_count_label.text())
        self.assertIn("新增 0", self.panel.ingest_totals_label.text())
        self.assertIn("提交未知 · 不计新增", self.panel.recent_label.text())
        self.assertIn("已取消 · 不计新增", self.panel.recent_label.text())

    def test_bad_and_incompatible_ingest_rows_rejected(self):
        rows = [None, [], "PRIVATE", {}, event(True), event(-1), event(1.5), event(10 ** 400),
                event(1, source=[]), event(2, kind={}), event(3, domain=[]), event(4, status={}),
                event(5, finished=float("inf")), event(6, finished=10 ** 400),
                event(7, source="cldr_zh"), event(8, domain="emotion"),
                event(9, source="PRIVATE_SOURCE"), event(10, status="PRIVATE_STATUS")]
        self.panel.update_data(snapshot(rows))
        self.assertIn("留存已结束 0 次", self.panel.ingest_count_label.text())
        self.assertNotIn("PRIVATE", self.text(True))

    def test_unknown_names_html_urls_errors_and_private_text_never_cross_boundary(self):
        secret = '<img src="file:///PRIVATE" onerror="upload()">PRIVATE_API_KEY\nhttps://private.invalid'
        info = snapshot([event(source="countries", name=secret, error=secret, text=secret,
                               labels={"emotion": secret}, metrics={"added": 3, "rejected": secret}),
                         event(2, source=secret, domain=secret, status=secret)])
        info["emotion"].update(name=secret, reason=secret, text=secret, source=secret, error=secret, status=secret)
        info["knowledge"].update(error=secret, records=secret, facts=secret,
                                domains={secret: 10 ** 400, "geography": 3}, sources={secret: secret})
        info["training"]["brain"].update(status=secret, error=secret, private=secret)
        info["training"]["journal"].update(status=secret, error=secret, private=secret)
        info["companion"] = {"name": secret, "reason": secret, "own_mood": secret}
        info["sourceCatalog"] += [{"id": secret, "name": secret, "status": "indexed"},
                                  {"id": "stackexchange", "status": secret}]
        info["platformSources"] = [{"id": "zhihu_public", "name": secret, "status": "已连接", "url": secret, "error": secret}]
        self.panel.update_data(info)
        for forbidden in ("PRIVATE", "upload", "private.invalid", "file:///", "<img", "已连接"):
            self.assertNotIn(forbidden, self.text(True))
        self.assertIn("地理  3", self.text())
        self.assertIn("新增 3", self.panel.ingest_totals_label.text())
        for label in self.panel.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_source_count_is_deduplicated_fixed_catalog_not_platform_connections(self):
        info = snapshot()
        info["sourceCatalog"] += [{"id": "countries", "status": "indexed"}] * 10
        info["sourceCatalog"] += [{"id": "PRIVATE", "status": "indexed"}, {"id": [], "status": "indexed"},
                                  {"id": "zhihu_public", "status": "indexed"}]
        info["platformSources"] = [{"id": "zhihu_public", "status": "indexed"},
                                   {"id": "short_video_public", "status": "connected"}]
        self.panel.update_data(info)
        self.assertEqual(self.panel.source_count_label.text(), "已收录 3/6 · 可收录 1")
        self.assertEqual(len(self.panel.source_labels), 6)
        self.assertEqual(self.panel.source_labels["stackexchange"].text(), "技术问答 · 未连接")
        self.assertEqual(self.panel.source_labels["mime_db"].text(), "文件格式 · 已停用")
        self.assertIn("知乎 · 待接入", self.panel.platform_label.text())
        self.assertIn("短视频 · 待接入", self.panel.platform_label.text())
        self.assertIn("未连接", self.panel.platform_label.text())
        self.assertNotIn("PRIVATE", self.text(True))

    def test_all_catalog_states_use_fixed_translation(self):
        states = ("available", "indexed", "error", "disabled", "pending", "unconnected")
        info = snapshot()
        info["sourceCatalog"] = [{"id": key, "status": state} for key, state in zip(SOURCE_TEXT, states)]
        self.panel.update_data(info)
        for source, state in zip(SOURCE_TEXT, states):
            self.assertEqual(self.panel.source_labels[source].text(), f"{SOURCE_TEXT[source]} · {data_panel.SOURCE_STATE_TEXT[state]}")
        info["sourceCatalog"] = [{"id": "countries", "status": []}, {"id": "cldr_zh", "status": "PRIVATE"}]
        self.panel.update_data(info)
        self.assertIn("已收录 0/6", self.panel.source_count_label.text())
        self.assertTrue(all("待确认" in label.text() for label in self.panel.source_labels.values()))

    def test_malformed_top_level_and_nested_payloads_are_safe(self):
        values = (None, False, True, 4, 3.5, "PRIVATE", [], float("nan"), float("inf"), 10 ** 400)
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.panel.update_data(value)
                self.panel.update_data({key: value for key in ("emotion", "knowledge", "training", "companion", "sourceCatalog", "platformSources")})
                self.panel.update_data({"training": {"journal": value, "brain": value}})
                self.panel.update_data({"training": {"journal": {"totals": value, "events": [value]}}})
        self.assertNotIn("PRIVATE", self.text(True))
        self.assertEqual(self.panel.hero_values["knowledge"].text(), "0")

    def test_nonfinite_huge_boolean_negative_fractional_counters_rejected_without_overflow(self):
        invalid = (10 ** 10000, -(10 ** 10000), float("inf"), float("-inf"), float("nan"),
                   True, False, -1, .5, "123", [], {})
        for value in invalid:
            with self.subTest(kind=type(value).__name__):
                info = snapshot([event(metrics={"added": value, "validated": value, "rejected": value})])
                info["knowledge"] = {"count": value, "revision": value, "domains": {key: value for key in DOMAIN_TEXT}}
                info["emotion"] = {key: value for key in ("revision", *self.panel.emotion_values)}
                info["training"]["journal"]["totals"] = {key: value for key in info["training"]["journal"]["totals"]}
                self.panel.update_data(info)
                self.assertEqual(self.panel.hero_values["knowledge"].text(), "0")
                self.assertTrue(all(label.text() == "0" for label in self.panel.emotion_values.values()))
                self.assertTrue(all(bar.value() == 0 for bar in self.panel.domain_bars.values()))
                self.assertNotIn("nan", self.text(True))
                self.assertNotIn("inf", self.text(True))

    def test_integral_floats_and_largest_allowed_counters_are_finite_compact_and_precise_in_tooltips(self):
        info = snapshot()
        info["knowledge"]["count"] = MAX_COUNTER
        info["knowledge"]["domains"] = {domain: MAX_COUNTER for domain in DOMAIN_TEXT}
        info["emotion"]["fit_samples"] = 13.0
        self.panel.update_data(info)
        self.assertEqual(self.panel.hero_values["knowledge"].text(), "10.0万亿")
        self.assertEqual(self.panel.hero_values["knowledge"].toolTip(), str(MAX_COUNTER))
        self.assertEqual(self.panel.hero_values["emotion"].text(), "13")
        self.assertTrue(all(bar.value() == 166 for bar in self.panel.domain_bars.values()))

    def test_selection_survives_identical_changed_and_malformed_updates_without_signals(self):
        self.panel.emotion_button.click()
        spy = QSignalSpy(self.panel.views.currentChanged)
        button_spies = [QSignalSpy(button.toggled) for button in (self.panel.emotion_button, self.panel.knowledge_button)]
        bar_spies = [QSignalSpy(bar.valueChanged) for bar in self.panel.domain_bars.values()]
        for info in (snapshot(), snapshot([event(99)]), {}, None, snapshot()):
            self.panel.update_data(info)
            self.assertEqual(self.panel.views.currentIndex(), 1)
            self.assertTrue(self.panel.emotion_button.isChecked())
        self.assertEqual(spy.count(), 0)
        self.assertTrue(all(spy.count() == 0 for spy in button_spies + bar_spies))

    def test_updates_detach_metadata_and_never_mutate_caller(self):
        info = snapshot()
        original = copy.deepcopy(info)
        self.panel.update_data(info)
        self.assertEqual(info, original)
        displayed = self.text(True)
        info["knowledge"]["count"] = 999
        info["training"]["journal"]["events"][0]["metrics"]["added"] = 999
        info["sourceCatalog"][0]["status"] = "PRIVATE"
        self.assertEqual(self.text(True), displayed)

    def test_duplicate_ids_and_excess_history_are_bounded(self):
        self.panel.update_data(snapshot([event(1), event(1), event(2)]))
        self.assertIn("留存已结束 2 次", self.panel.ingest_count_label.text())
        self.panel.update_data(snapshot([event(i) for i in range(1, EVENT_LIMIT + 101)]))
        self.assertIn(f"留存已结束 {EVENT_LIMIT} 次", self.panel.ingest_count_label.text())
        self.assertEqual(len(self.panel.recent_label.text().splitlines()), 2)

    def test_import_construct_update_and_local_switch_have_no_io_training_show_or_external_signals(self):
        blocked = {name: None for name in ("learning_hub", "training_journal", "emotion_model", "knowledge_store", "neural_brain", "training_ui")}
        with patch("builtins.open", side_effect=AssertionError("file access")), \
                patch("pathlib.Path.read_text", side_effect=AssertionError("read")), \
                patch("pathlib.Path.write_text", side_effect=AssertionError("write")), \
                patch("socket.socket", side_effect=AssertionError("network")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("url")), \
                patch.object(QWidget, "show", side_effect=AssertionError("show")), \
                patch.dict(sys.modules, blocked):
            module = importlib.reload(data_panel)
            panel = module.DataPanel()
            panel.update_data(snapshot())
            panel.emotion_button.click()
            panel.update_data(snapshot())
            self.assertFalse(panel.isVisible())
            self.assertEqual(panel.views.currentIndex(), 1)
            panel.deleteLater()

    def test_layout_fits_420_width_and_about_400_height_without_horizontal_scroll(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.resize(420, 430)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(9, 4, 9, 4)
        layout.addWidget(self.panel)
        scroll.setWidget(content)
        self.panel.update_data(snapshot([event(i) for i in range(1, 100)]))
        scroll.show()
        self.app.processEvents()
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(self.panel.sizeHint().height(), 420)
        self.assertLessEqual(self.panel.minimumSizeHint().width(), 400)
        for button in (self.panel.knowledge_button, self.panel.emotion_button):
            button.click()
            self.app.processEvents()
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            for label in self.panel.findChildren(QLabel):
                if label.isVisible():
                    for line in label.text().splitlines():
                        self.assertLessEqual(label.fontMetrics().horizontalAdvance(line), label.width() + 2,
                                             (label.text(), label.width()))
        self.panel.setParent(None)
        scroll.close()
        scroll.deleteLater()


if __name__ == "__main__":
    unittest.main()
