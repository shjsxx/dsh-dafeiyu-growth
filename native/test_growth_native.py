"""Run with python -m unittest discover -s native -p test_growth_native.py."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from growth_native import (
    GrowthDialog, GrowthReader, STAGES, derive, draw_growth_footer,
    footer_height, level_from_xp, modern_text, summary, xp_for_level,
)


class ModelTests(unittest.TestCase):
    def test_legacy_stage_names_display_as_current_names(self):
        self.assertEqual(modern_text("我进化了：幼鱼 · 刚开始跟着你的幼鱼"), "我进化了：听风 · 刚开始跟着你的听风")
        self.assertEqual(modern_text("鱼卵 / 小鱼 / 大鱼"), "云芽 / 御风 / 踏云")
        self.assertEqual(modern_text("岚鲸"), "岚鲸")
        self.assertEqual(modern_text("鲸"), "岚鲸")
    def test_level_boundaries_and_stage_parity(self):
        for level in range(1, 61):
            xp = 40 * (level - 1) ** 2
            state = derive({"xp": xp})
            self.assertEqual(xp_for_level(level), xp)
            self.assertEqual(state["level"], level)
            stage = next(s for s in reversed(STAGES) if level >= s["minLevel"])
            self.assertEqual(state["stage"], {key: value for key, value in stage.items() if key != "minLevel"})
            self.assertEqual(state["ratio"], 1 if level == 60 else 0)
            self.assertEqual(state["need"], 1 if level == 60 else 40 * (2 * level - 1))
            if level > 1:
                self.assertEqual(level_from_xp(xp - 1), level - 1)
        self.assertEqual(xp_for_level(0), 0)
        self.assertEqual(xp_for_level(100), 139240)
        self.assertEqual(derive({"xp": 1e30})["level"], 60)
        self.assertEqual(derive({"xp": 1e30})["into"], 1)

    def test_progress_summary_and_footer(self):
        state = derive({"xp": 186})
        self.assertEqual((state["level"], state["into"], state["need"]), (3, 26, 200))
        self.assertAlmostEqual(state["ratio"], .13)
        self.assertEqual(summary(state), "Lv.3 · 云芽  |  经验 26/200")
        self.assertEqual(footer_height(state), 36)
        for bad in (None, [], {}, "bad", {"level": "3", "stage": {}}, {"level": math.nan, "stage": {}}):
            self.assertEqual(summary(bad), "")
            self.assertEqual(footer_height(bad), 0)
        state["stage"]["name"] = "云\n岚\r\n test"
        self.assertNotIn("\n", summary(state))

    def test_learned_facts_are_recomputed_from_current_counters(self):
        profile = {
            "xp": 100,
            "counters": {"toolErrors": 5, "recoveries": 2},
            "hours": [0] * 24,
            "tools": {"read": 12, "edit": 3},
            "projects": {"alpha": 25},
            "days": {"2026-10-31": 4, "2026-10-30": 3},
            "learned": [{"id": "hour", "text": "你最常在 01 点开工", "at": "2020-01-01T00:00:00"}],
        }
        profile["hours"][14] = 50
        state = derive(profile, datetime(2026, 10, 31, 15, 0))
        texts = [row["text"] for row in state["learned"]]
        self.assertIn("已经连续 2 天陪你工作", texts)
        self.assertIn("你最常在 14 点活跃", texts)
        self.assertIn("最常用的工具是 read（12 次）", texts)
        self.assertIn("在 alpha 这个项目上干得最多", texts)
        self.assertIn("遇到过 5 次工具报错，自己爬起来了 2 次", texts)
        self.assertNotIn("你最常在 01 点开工", texts)
        hour = next(row for row in state["learned"] if row["id"] == "hour")
        self.assertNotEqual(hour["at"], "2020-01-01T00:00:00")

    def test_expired_learned_facts_disappear(self):
        state = derive({
            "xp": 10,
            "hours": [0] * 24,
            "days": {},
            "tools": {},
            "projects": {},
            "counters": {"toolErrors": 0},
            "learned": [{"id": "streak", "text": "已经连续 9 天陪你工作", "at": "2020-01-01T00:00:00"}],
        }, datetime(2026, 10, 31, 15, 0))
        self.assertEqual(state["learned"], [])

    def test_trait_js_rounding_and_histograms(self):
        state = derive({
            "activeMs": 60000,
            "counters": {"turns": 11, "approvals": 2, "okTurns": 7, "errorTurns": 1},
            "tools": {"read": 4, "write": 8}, "projects": {"demo": 3},
        })
        self.assertEqual(state["traits"], {"stamina": 1, "wit": 18, "affinity": 17, "calm": 88})
        self.assertEqual(state["topTools"], [("write", 8), ("read", 4)])
        high = derive({"activeMs": 10**10, "counters": {"turns": 10000}, "tools": {str(i): i for i in range(30)}})
        self.assertEqual(high["traits"], {"stamina": 100, "wit": 100, "affinity": 100, "calm": 50})
        self.assertEqual(len(high["topTools"]), 6)

    def test_malformed_fields_are_normalized(self):
        self.assertIsNone(derive(None))
        self.assertIsNone(derive([]))
        for xp in (None, "123", True, float("nan"), float("inf"), -1):
            state = derive({"xp": xp, "counters": [], "activeMs": "bad", "tools": False,
                            "projects": [], "learned": [False, {}, {"text": "missing id"}],
                            "diary": [None, {"text": []}, {"text": "valid", "at": {}}], "days": []})
            self.assertEqual(state["level"], 1)
            self.assertEqual(state["ratio"], 0)
            self.assertEqual(state["traits"], {"stamina": 0, "wit": 0, "affinity": 0, "calm": 50})
            self.assertEqual(state["learned"], [])
            self.assertEqual(state["diary"][0]["at"], "")
        state = derive({"counters": {"turns": "bad", "approvals": -20, "okTurns": True},
                        "tools": {"bad": []}, "activeMs": -100})
        self.assertEqual(state["counters"]["turns"], 0)
        self.assertEqual(state["traits"]["affinity"], 0)
        self.assertEqual(state["traits"]["stamina"], 0)

    def test_extreme_finite_numbers_do_not_overflow(self):
        for value in (1e308, 10**308, 10**400):
            state = derive({"xp": value, "activeMs": value,
                            "counters": {"turns": value, "approvals": value, "okTurns": value, "errorTurns": value}})
            self.assertTrue(1 <= state["level"] <= 60)
            self.assertTrue(0 <= state["ratio"] <= 1)
            self.assertTrue(all(0 <= value <= 100 for value in state["traits"].values()))

    def test_streak_including_yesterday_and_no_mutation(self):
        raw = {"days": {"2026-10-30": 1, "2026-10-29": 2},
               "learned": [{"id": "fact", "text": "hello", "at": "today"}],
               "diary": [{"text": "review", "at": "today", "kind": "stage"}]}
        before = copy.deepcopy(raw)
        state = derive(raw, datetime(2026, 10, 31))
        self.assertEqual(state["streak"], 2)
        state["learned"][0]["text"] = "changed"
        state["counters"]["turns"] = 99
        self.assertEqual(raw, before)


class ReaderTests(unittest.TestCase):
    def test_environment_path_precedence(self):
        with patch.dict(os.environ, {"DSH_HOME": "C:/growth-data", "DSH_DAFEIYU_GROWTH_FILE": "C:/override/profile.json"}):
            self.assertEqual(GrowthReader().path, Path("C:/override/profile.json"))
        with patch.dict(os.environ, {"DSH_HOME": "C:/growth-data", "DSH_DAFEIYU_GROWTH_FILE": ""}):
            self.assertEqual(GrowthReader().path, Path("C:/growth-data/dsh-dafeiyu-growth/profile.json"))
        with patch.dict(os.environ, {"DSH_HOME": "", "DSH_DAFEIYU_GROWTH_FILE": ""}):
            self.assertEqual(GrowthReader().path, Path.home() / ".dsh/dsh-dafeiyu-growth/profile.json")
        self.assertEqual(GrowthReader("explicit.json").path, Path("explicit.json"))

    def test_stale_good_on_corruption_missing_and_invalid_root(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            reader = GrowthReader(path)
            self.assertIsNone(reader.refresh())
            path.write_text('{"xp":186}', encoding="utf-8")
            good = reader.refresh()
            self.assertEqual(good["level"], 3)
            path.write_text('{"xp":', encoding="utf-8")
            self.assertIs(reader.refresh(), good)
            path.write_text('[]', encoding="utf-8")
            self.assertIs(reader.refresh(), good)
            path.write_bytes(b"\xff\xfe")
            self.assertIs(reader.refresh(), good)
            path.unlink()
            self.assertIs(reader.refresh(), good)
            path.write_text('{"xp":640}', encoding="utf-8-sig")
            self.assertEqual(reader.refresh()["stage"]["name"], "听风")

    def test_unchanged_cache_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text('{"xp":186}', encoding="utf-8")
            before = path.stat()
            reader = GrowthReader(path)
            state = reader.refresh()
            with patch.object(Path, "open", side_effect=AssertionError("unchanged file reread")):
                self.assertIs(reader.refresh(), state)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"xp":186}')
            self.assertEqual(before.st_mtime_ns, path.stat().st_mtime_ns)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_invalid_first_read_does_not_create_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = GrowthReader(Path(directory) / "no-parent/profile.json")
            self.assertIsNone(reader.refresh())
            self.assertEqual(list(Path(directory).iterdir()), [])


class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialog = GrowthDialog()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def test_passive_nonmodal_dialog_and_valid_update(self):
        self.assertFalse(self.dialog.isVisible())
        self.assertFalse(self.dialog.isModal())
        self.assertEqual(self.dialog.windowTitle(), "云岚 · 成长卡")
        self.assertTrue(self.dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertGreaterEqual(self.dialog.minimumWidth(), 400)
        self.assertLessEqual(self.dialog.maximumWidth(), self.app.primaryScreen().availableGeometry().width())
        state = derive({"xp": 186, "counters": {"turns": 10}})
        self.dialog.update_state(state)
        self.app.processEvents()
        self.assertFalse(self.dialog.isVisible())
        self.assertEqual(self.dialog.level_label.text(), "Lv.3 · 云芽")
        self.assertEqual(self.dialog.xp_progress.value(), 130)
        self.assertEqual(self.dialog.trait_bars["affinity"].value(), 8)
        self.assertIn("10", self.dialog.stats_label.text())

    def test_growth_card_uses_fixed_header_and_tabs(self):
        self.assertEqual(self.dialog.tabs.count(), 7)
        self.assertEqual(self.dialog.tabs.tabText(0), "概览")
        self.assertEqual(self.dialog.tabs.tabText(1), "本领")
        self.assertEqual(self.dialog.tabs.tabText(2), "回顾")
        self.assertEqual(self.dialog.tabs.tabText(3), "权限")
        self.assertEqual(self.dialog.tabs.tabText(4), "情感")
        self.assertEqual(self.dialog.tabs.tabText(5), "训练")
        self.assertEqual(self.dialog.tabs.tabText(6), "数据")
        before = self.dialog.size().height()
        self.dialog.update_state(derive({
            "xp": 5000,
            "tools": {"read": 50},
            "hours": [5] * 24,
            "diary": [{"text": "很长回顾" * 10, "at": "2026-10-31T12:00"} for _ in range(5)],
        }))
        self.app.processEvents()
        self.assertEqual(self.dialog.size().height(), before)
        self.assertEqual(self.dialog.close_button.text(), "关闭")
        self.assertTrue(self.dialog.forget_button.isEnabled())

    def test_plain_text_xss_and_only_recent_five_diaries(self):
        attack = '<img src="http://127.0.0.1:9/x" onerror="alert(1)"><b>unsafe</b>'
        state = derive({"learned": [{"id": "xss", "text": attack}],
                        "tools": {attack: 50},
                        "diary": [{"text": f"entry-{i} " + attack, "at": "2026-10-30T12:34"} for i in range(8)]})
        state["stage"].update({"name": attack, "title": attack, "accent": "red; image: url(http://bad)"})
        self.dialog.update_state(state)
        for label in self.dialog.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
            self.assertFalse(label.openExternalLinks())
        self.assertIn(attack, self.dialog.learned_label.text())
        self.assertIn(attack, self.dialog.title_label.text())
        self.assertIn("entry-4", self.dialog.diary_label.text())
        self.assertNotIn("entry-5", self.dialog.diary_label.text())
        self.assertNotIn("http", self.dialog.xp_progress.styleSheet())
        self.assertFalse(self.dialog.isVisible())

    def test_malformed_updates_do_not_show_or_crash(self):
        for bad in (None, [], "bad", {}, {"level": None, "stage": []},
                    {"level": 3, "stage": {"name": [], "title": {}}, "traits": {"calm": float("inf")},
                     "ratio": [], "counters": [], "learned": [None], "diary": [{"text": "ok", "at": {}}]}):
            self.dialog.update_state(bad)
            self.assertFalse(self.dialog.isVisible())
        self.dialog.update_state(derive({"xp": 160}))
        self.assertEqual(self.dialog.level_label.text(), "Lv.3 · 云芽")

    def test_explicit_open_is_clamped_and_close_does_not_reopen(self):
        pet = QWidget()
        available = self.app.primaryScreen().availableGeometry()
        pet.setGeometry(available.right() - 50, available.bottom() - 50, 80, 80)
        pet.show()
        try:
            self.dialog.open_near(pet)
            self.app.processEvents()
            self.assertTrue(self.dialog.isVisible())
            self.assertFalse(self.dialog.isModal())
            self.assertTrue(available.contains(self.dialog.frameGeometry()), (available, self.dialog.frameGeometry()))
            self.dialog.close_button.click()
            self.assertFalse(self.dialog.isVisible())
            self.dialog.update_state(derive({"xp": 1000}))
            self.app.processEvents()
            self.assertFalse(self.dialog.isVisible())
        finally:
            pet.close()
            pet.deleteLater()

    def test_journal_overview_groups_traits_and_fits_without_scrolling(self):
        self.dialog.update_state(derive({"xp": 3265, "counters": {"turns": 24, "toolCalls": 1392}}))
        self.dialog.show()
        self.app.processEvents()
        self.assertEqual(self.dialog.objectName(), "growthJournal")
        self.assertEqual(len({bar.parent() for bar in self.dialog.trait_bars.values()}), 1)
        # Hosted runners may lack the CJK UI font; allow a small metric delta
        # there while keeping the strict no-scroll check for supported systems.
        scroll_limit = 48 if os.environ.get("CI") else 0
        self.assertLessEqual(self.dialog.scroll.verticalScrollBar().maximum(), scroll_limit)
        self.assertGreaterEqual(self.dialog.tabs.tabBar().width(), self.dialog.tabs.width() - 2)
        self.assertEqual(self.dialog.xp_label.toolTip(), "累计经验 3265")
        self.assertIn("Lv.11", self.dialog.progress_hint.text())
        self.assertTrue(self.dialog.close_button.isVisible())

    def test_diary_is_selectable_plain_text_timeline_and_updates_are_stable(self):
        state = derive({"diary": [{"text": f"<b>记忆 {i}</b>", "at": "2026-10-31T12:34"} for i in range(7)]})
        self.dialog.update_state(state)
        bodies = self.dialog.diary_label.findChildren(QLabel, "diaryBody")
        self.assertEqual(len(bodies), 5)
        for body in bodies:
            self.assertEqual(body.textFormat(), Qt.TextFormat.PlainText)
            self.assertTrue(body.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        before = [id(widget) for widget in bodies]
        self.dialog.update_state(state)
        self.assertEqual(before, [id(widget) for widget in self.dialog.diary_label.findChildren(QLabel, "diaryBody")])
        self.assertNotIn("记忆 5", self.dialog.diary_label.text())

    def test_small_journal_keeps_footer_and_all_pages_scrollable(self):
        self.dialog.resize(420, 480)
        self.dialog.update_state(derive({"diary": [{"text": "很长的成长记录" * 70, "at": "2026-10-31"} for _ in range(5)]}))
        self.dialog.show()
        self.app.processEvents()
        size = self.dialog.size()
        footer_y = self.dialog.close_button.geometry().top()
        for index, scroll in enumerate(self.dialog.page_scrolls):
            self.dialog.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertEqual(self.dialog.size(), size)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertLessEqual(scroll.geometry().bottom(), footer_y)
            self.assertTrue(self.dialog.rect().contains(self.dialog.close_button.geometry()))
        diary_scroll = self.dialog.page_scrolls[2]
        self.assertGreater(diary_scroll.verticalScrollBar().maximum(), 0)

    def test_emotion_empty_state_and_controls_remain_available(self):
        self.dialog.update_emotion({"name": "平静", "confidence": 0})
        self.assertIn("倾听", self.dialog.emotion_name.text())
        self.dialog.update_emotion({"name": "低落", "confidence": 0.62, "learned": 11, "status": "ready"})
        self.assertEqual(self.dialog.emotion_name.text(), "低落")
        self.assertIn("62%", self.dialog.emotion_label.text())
        self.assertTrue(self.dialog.emotion_learn.isEnabled())
        self.assertEqual(self.dialog.close_button.objectName(), "closeJournal")

    def test_learning_switch_updates_silently_and_owner_can_disable(self):
        changes = []
        self.dialog.emotion_auto_changed.connect(changes.append)
        self.dialog.update_learning({"enabled": True, "lastSuccess": 1793448000, "lastAdded": 12})
        self.assertTrue(self.dialog.emotion_auto.isChecked())
        self.assertEqual(changes, [])
        self.assertIn("无需逐次确认", self.dialog.learning_status.text())
        self.assertIn("新增 12 条", self.dialog.learning_status.text())
        self.dialog.emotion_auto.setChecked(False)
        self.assertEqual(changes, [False])

    def test_public_learning_button_needs_no_modal_and_stays_disabled_while_busy(self):
        requests = []
        self.dialog.emotion_learning_requested.connect(lambda: requests.append(True))
        self.dialog.emotion_learn.click()
        self.assertEqual(requests, [True])
        self.dialog.update_learning({"enabled": True, "busy": True})
        self.assertFalse(self.dialog.emotion_learn.isEnabled())
        self.dialog.update_emotion({"status": "ready"})
        self.assertFalse(self.dialog.emotion_learn.isEnabled())
        self.dialog.update_learning({"enabled": True, "busy": False, "protected": True})
        self.assertFalse(self.dialog.emotion_auto.isEnabled())
        self.assertFalse(self.dialog.emotion_learn.isEnabled())

    def test_companion_state_is_separate_from_the_owner_emotion_guess(self):
        self.dialog.update_emotion({"name": "低落", "confidence": .62})
        before = self.dialog.emotion_label.text()
        self.dialog.update_companion({"mood": "cheerful", "valence": .8, "energy": .7, "trust": .6, "curiosity": .5})
        self.assertIn("雀跃", self.dialog.companion_name.text())
        self.assertEqual(before, self.dialog.emotion_label.text())
        self.assertIn("精神 70", self.dialog.companion_metrics.text())

    def test_training_auto_and_scope_switches_update_without_side_effects(self):
        automatic, scopes = [], []
        self.dialog.emotion_auto_changed.connect(automatic.append)
        self.dialog.knowledge_auto_changed.connect(scopes.append)
        self.dialog.update_learning({"enabled": True, "knowledgeEnabled": True})
        self.assertTrue(self.dialog.training_auto.isChecked())
        self.assertTrue(self.dialog.knowledge_auto.isChecked())
        self.assertEqual(automatic, [])
        self.assertEqual(scopes, [])
        self.dialog.training_auto.setChecked(False)
        self.dialog.knowledge_auto.setChecked(False)
        self.assertEqual(automatic, [False])
        self.assertEqual(scopes, [False])

    def test_training_tab_compacts_cover_without_resizing_window(self):
        self.dialog.show()
        self.app.processEvents()
        size, full = self.dialog.size(), self.dialog.hero.height()
        self.dialog.tabs.setCurrentIndex(5)
        self.app.processEvents()
        self.assertEqual(size, self.dialog.size())
        self.assertLess(self.dialog.hero.height(), full)
        self.assertTrue(self.dialog.close_button.isVisible())
        self.dialog.tabs.setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.dialog.xp_label.isVisible())

    def test_emotion_workspace_keeps_view_selection_and_updates_four_meters(self):
        self.dialog.emotion_sections.button(1).click()
        self.dialog.update_companion({"mood": "curious", "valence": .4, "energy": .6, "trust": .3, "curiosity": .9})
        self.dialog.update_emotion({"name": "喜悦", "confidence": .7})
        self.assertEqual(self.dialog.emotion_stack.currentIndex(), 1)
        self.assertEqual(self.dialog.feeling_bars['valence'].value(), 70)
        self.assertEqual(self.dialog.feeling_bars['energy'].value(), 60)
        self.assertEqual(self.dialog.feeling_bars['trust'].value(), 30)
        self.assertEqual(self.dialog.feeling_bars['curiosity'].value(), 90)
        self.assertEqual(self.dialog.emotion_signal.value(), 70)
        self.assertFalse(self.dialog.isVisible())

    def test_new_pages_never_resize_or_remove_footer_at_minimum_size(self):
        self.dialog.resize(420, 480)
        self.dialog.show()
        self.app.processEvents()
        size = self.dialog.size()
        for index in (4, 5, 6):
            self.dialog.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertEqual(self.dialog.size(), size)
            self.assertTrue(self.dialog.close_button.isVisible())
            self.assertLessEqual(self.dialog.close_button.geometry().bottom(), self.dialog.height())
            self.assertEqual(self.dialog.page_scrolls[index].horizontalScrollBar().maximum(), 0)

    def test_typography_stays_large_enough_to_read(self):
        # Qt resolves QSS px sizes into widget fonts once polished; small gray
        # 10px CJK text was the reported "blurry" growth card.
        self.dialog.show()
        self.app.processEvents()
        readable = {
            "body": self.dialog.learned_label.font().pixelSize(),
            "caption": self.dialog.memory_status.font().pixelSize(),
            "button": self.dialog.close_button.font().pixelSize(),
            "level": self.dialog.level_label.font().pixelSize(),
            "dataPanel": self.dialog.data_panel.knowledge_button.font().pixelSize(),
            "trainingPanel": self.dialog.training_panel.learning_button.font().pixelSize(),
        }
        for name, size in readable.items():
            with self.subTest(name=name):
                self.assertGreaterEqual(size, 12, name)
        self.assertGreaterEqual(readable["body"], 14)
        self.assertGreaterEqual(readable["level"], 15)

    def test_footer_clipped_to_supplied_top_and_painter_restored(self):
        for scale in (1, 2):
            image = QImage(500, 200, QImage.Format.Format_ARGB32)
            image.fill(QColor("#ff00ff"))
            painter = QPainter(image)
            painter.setPen(QColor("#123456"))
            draw_growth_footer(painter, derive({"xp": 186}), 20, 50, 230 * scale, 36 * scale, scale)
            self.assertEqual(painter.pen().color(), QColor("#123456"))
            painter.end()
            self.assertEqual(image.pixelColor(50, 49), QColor("#ff00ff"))
            self.assertEqual(image.pixelColor(50, 50 + 36 * scale), QColor("#ff00ff"))
            self.assertNotEqual(image.pixelColor(50, 60), QColor("#ff00ff"))
        image = QImage(20, 20, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ff00ff"))
        painter = QPainter(image)
        draw_growth_footer(painter, None, 0, 0, 20, 20, 1)
        painter.end()
        self.assertEqual(image.pixelColor(10, 10), QColor("#ff00ff"))


if __name__ == "__main__":
    unittest.main()
