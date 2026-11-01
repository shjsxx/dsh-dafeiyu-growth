"""Integration boundaries for online display, private comparisons and history."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel
from cloud_chat import bounded_history
from emotion_model import EmotionModel
from learning_state import PublicLearningState
from online_ui import OnlinePanel


class RealtimeFeaturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_public_comparison_is_transient_and_not_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            model = EmotionModel(Path(directory) / "emotion.json")
            model.extra_cues["joy"].add("开心")
            model.lexicon_count = 1
            before = model.snapshot()
            with patch.object(Path, "write_text", side_effect=AssertionError("no IO")):
                reading = model.compare_public("我今天很开心，私有名字不该出现在快照")
            self.assertEqual(reading["publicLabel"], "joy")
            self.assertEqual(reading["modelLabel"], "joy")
            self.assertTrue(reading["localOnly"])
            self.assertNotIn("私有名字", json.dumps(reading, ensure_ascii=False))
            self.assertEqual(before, model.snapshot())
            self.assertFalse(model.path.exists())
            self.assertEqual(model.compare_public("我不开心")["publicLabel"], "sad")

    def test_no_public_cues_is_not_a_claimed_match(self):
        with tempfile.TemporaryDirectory() as directory:
            model = EmotionModel(Path(directory) / "emotion.json")
            self.assertIsNone(model.compare_public("什么东西？")["agreement"])

    def test_history_roles_and_budget(self):
        rows = [{"role": "system", "content": "untrusted system"}, {"role": "tool", "content": "execute"},
                {"role": "user", "content": "u" * 2000}, {"role": "assistant", "content": "a" * 2000}]
        history = bounded_history(rows, "x" * 2000)
        self.assertEqual(history, [{"role": "assistant", "content": "a" * 2000}])
        self.assertLessEqual(sum(len(row["content"]) for row in history) + 2000, 4000)
        self.assertEqual(bounded_history(None), [])
        self.assertEqual(bounded_history([{"role": "user", "content": {"secret": 1}}]), [])

    def test_hourly_mode_retains_server_backoff_and_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            state = PublicLearningState(path)
            state.set_enabled(True)
            state.finish(True, now=1000)
            state.set_realtime_mode(True)
            self.assertEqual(state.next_check, 4600)
            again = PublicLearningState(path)
            self.assertTrue(again.realtime_mode)
            again.finish(False, now=2000, retry_after=10_000)
            before = again.next_check
            again.set_realtime_mode(False)
            again.set_realtime_mode(True)
            self.assertEqual(again.next_check, before)
            self.assertFalse(again.begin(3000, manual=True))
            again.finish(True, now=20_000)
            self.assertEqual(again.next_check, 23_600)

    def test_online_panel_passive_plaintext_and_sanitized(self):
        panel = OnlinePanel()
        changes = []
        panel.enabled_changed.connect(changes.append)
        panel.refresh_requested.connect(lambda: changes.append("fetch"))
        panel.update_state({"live": {"enabled": True, "lastSuccess": time.time(), "sourceUpdatedAtMs": time.time() * 1000},
                            "comparison": {"modelLabel": "joy", "publicLabel": "sad"}})
        self.assertEqual(changes, [])
        self.assertIn("开心", panel.comparison.text())
        self.assertIn("低落", panel.comparison.text())
        for value in (None, [], "<script>secret</script>", {"live": {"enabled": True, "lastSuccess": 10 ** 10000, "sourceUpdatedAtMs": 10 ** 10000}, "comparison": {"modelLabel": [], "publicLabel": {}}}):
            panel.update_state(value)
        for label in panel.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
            self.assertNotIn("secret", label.text())
        self.assertFalse(panel.isVisible())
        panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
