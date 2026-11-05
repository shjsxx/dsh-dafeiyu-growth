"""Unit tests for the local behaviour-learning store."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from behavior import BehaviorLearner, default_path


class BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "behavior.json"
        self.now = lambda: datetime(2026, 10, 31, 12, 0, 0)

    def tearDown(self):
        self.directory.cleanup()

    def learner(self):
        return BehaviorLearner(self.path, now=self.now)

    def test_empty_store_does_not_create_a_file(self):
        learner = self.learner()
        snapshot = learner.snapshot()
        self.assertEqual(snapshot["runs"], 0)
        self.assertEqual(snapshot["agility"], 0)
        self.assertEqual(snapshot["discoveries"], [])
        self.assertFalse(self.path.exists())

    def test_running_practices_agility_and_unlocks_skills(self):
        learner = self.learner()
        for _ in range(5):
            learner.note_run(400)
        snapshot = learner.snapshot()
        self.assertEqual(snapshot["runs"], 5)
        self.assertGreater(snapshot["agility"], 0)
        ids = [row["id"] for row in snapshot["discoveries"]]
        self.assertIn("run1", ids)
        self.assertIn("run5", ids)
        self.assertEqual(snapshot["discoveries"][0]["at"], "2026-10-31T12:00:00")

    def test_action_count_is_visible_in_snapshot_and_survives_reload(self):
        learner = self.learner()
        learner.note_action("悠闲哼歌")
        self.assertEqual(learner.snapshot()["actions"], 1)
        self.assertEqual(self.learner().snapshot()["actions"], 1)

    def test_jumps_and_falls_teach_balance(self):
        learner = self.learner()
        before = learner.fall_chance()
        learner.note_jump()
        learner.note_fall()
        learner.note_getup()
        snapshot = learner.snapshot()
        self.assertEqual(snapshot["jumps"], 1)
        self.assertEqual(snapshot["falls"], 1)
        self.assertEqual(snapshot["recoveries"], 1)
        self.assertGreater(snapshot["balance"], 0)
        self.assertLess(learner.fall_chance(), before)
        ids = [row["id"] for row in snapshot["discoveries"]]
        self.assertIn("fall1", ids)
        self.assertIn("jump1", ids)

    def test_fall_chance_stays_bounded(self):
        learner = self.learner()
        self.assertGreaterEqual(learner.fall_chance(), 0.05)
        self.assertLessEqual(learner.fall_chance(), 0.18)
        for _ in range(40):
            learner.note_fall()
        self.assertGreaterEqual(learner.fall_chance(), 0.05)
        self.assertLessEqual(learner.fall_chance(), 0.06)

    def test_run_speed_grows_with_agility(self):
        learner = self.learner()
        slow = learner.run_speed()
        for _ in range(30):
            learner.note_run(600)
        self.assertGreater(learner.run_speed(), slow)
        self.assertLessEqual(learner.run_speed(), 480)

    def test_persistence_round_trip(self):
        learner = self.learner()
        learner.note_run(250)
        learner.note_jump()
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["runs"], 1)
        reloaded = self.learner()
        self.assertEqual(reloaded.snapshot()["runs"], 1)
        self.assertEqual(reloaded.snapshot()["jumps"], 1)
        self.assertEqual(reloaded.snapshot()["discoveries"][0]["text"], "学会在桌面上小跑")

    def test_corrupt_and_hostile_files_fall_back(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.learner().snapshot()["runs"], 0)
        self.path.write_text(json.dumps({"runs": "99", "agility": 1e9, "balance": -4,
                                         "discoveries": [None, {"text": 5}, {"id": "x", "text": "ok"}]}),
                             encoding="utf-8")
        snapshot = self.learner().snapshot()
        self.assertEqual(snapshot["runs"], 0)
        self.assertEqual(snapshot["agility"], 100)
        self.assertEqual(snapshot["balance"], 0)
        self.assertEqual(snapshot["discoveries"], [{"id": "x", "text": "ok", "at": ""}])

    def test_snapshot_keeps_only_recent_six_discoveries(self):
        learner = self.learner()
        learner.data["discoveries"] = [
            {"id": f"d{i}", "text": f"技能 {i}", "at": "2026-10-31T00:00:00"} for i in range(10)
        ]
        self.assertEqual(len(learner.snapshot()["discoveries"]), 6)
        self.assertEqual(learner.snapshot()["discoveries"][-1]["text"], "技能 9")

    def test_petting_learns_favourite_spot(self):
        learner = self.learner()
        for _ in range(5):
            learner.note_pet("head")
        learner.note_pet("tail")
        snapshot = learner.snapshot()
        self.assertEqual(snapshot["pets"], 6)
        self.assertEqual(snapshot["headPats"], 5)
        self.assertEqual(snapshot["favorite"], "摸头")
        ids = [row["id"] for row in snapshot["discoveries"]]
        self.assertIn("pet1", ids)
        self.assertIn("head5", ids)

    def test_no_favourite_when_counts_are_tied(self):
        learner = self.learner()
        for _ in range(3):
            learner.note_pet("head")
            learner.note_pet("tail")
        self.assertEqual(learner.snapshot()["favorite"], "")

    def test_chat_and_greeting_memory(self):
        learner = self.learner()
        learner.note_chat()
        learner.note_greeting()
        learner.set_name("  小林  ")
        snapshot = learner.snapshot()
        self.assertEqual(snapshot["chats"], 1)
        self.assertEqual(snapshot["greetings"], 1)
        self.assertEqual(snapshot["userName"], "小林")
        ids = [row["id"] for row in snapshot["discoveries"]]
        self.assertIn("chat1", ids)
        self.assertTrue(any("记住了你的名字" in row["text"] for row in snapshot["discoveries"]))

    def test_bond_survives_reload(self):
        learner = self.learner()
        learner.note_pet("head")
        learner.set_name("阿岚")
        reloaded = self.learner()
        snapshot = reloaded.snapshot()
        self.assertEqual(snapshot["pets"], 1)
        self.assertEqual(snapshot["userName"], "阿岚")

    def test_likes_are_deduplicated_and_persisted(self):
        learner = self.learner()
        learner.add_like("喝咖啡")
        learner.add_like("喝咖啡")
        learner.add_like("听雨")
        snapshot = learner.snapshot()
        self.assertEqual(snapshot["likes"], ["喝咖啡", "听雨"])
        self.assertTrue(any("记得你喜欢喝咖啡" in row["text"] for row in snapshot["discoveries"]))
        self.assertEqual(self.learner().snapshot()["likes"], ["喝咖啡", "听雨"])

    def test_reset_clears_practice(self):
        learner = self.learner()
        learner.note_run(300)
        learner.reset()
        snapshot = learner.snapshot()
        self.assertEqual(snapshot["runs"], 0)
        self.assertEqual(snapshot["discoveries"], [])

    def test_invalid_or_zero_distance_does_not_award_running_practice(self):
        learner = self.learner()
        for distance in (0, -10, None, "100", True, float("nan"), float("inf")):
            learner.note_run(distance)
        self.assertEqual(learner.snapshot()["runs"], 0)
        self.assertEqual(learner.snapshot()["agility"], 0)
        self.assertFalse(self.path.exists())

    def test_recovery_requires_an_unrecovered_fall(self):
        learner = self.learner()
        learner.note_getup()
        self.assertEqual(learner.snapshot()["recoveries"], 0)
        learner.note_fall()
        learner.note_getup()
        learner.note_getup()
        self.assertEqual(learner.snapshot()["recoveries"], 1)
        self.assertEqual(learner.data["balance"], 4.5)

    def test_recovery_discovery_requires_recoveries_not_just_falls(self):
        learner = self.learner()
        for _ in range(3):
            learner.note_fall()
        self.assertNotIn("fall3", [row["id"] for row in learner.data["discoveries"]])
        for _ in range(3):
            learner.note_getup()
        self.assertIn("fall3", [row["id"] for row in learner.data["discoveries"]])

    def test_name_correction_removes_old_name_from_discoveries_and_disk(self):
        learner = self.learner()
        learner.set_name("旧称呼")
        learner.set_name("新称呼")
        self.assertEqual(learner.snapshot()["userName"], "新称呼")
        self.assertNotIn("旧称呼", self.path.read_text(encoding="utf-8"))
        self.assertTrue(any("新称呼" in row["text"] for row in learner.snapshot()["discoveries"]))
        self.assertNotIn("旧称呼", json.dumps(self.learner().snapshot(), ensure_ascii=False))

    def test_evicted_like_is_not_retained_in_discoveries(self):
        learner = self.learner()
        learner.add_like("旧偏好")
        for index in range(6):
            learner.add_like(f"新偏好{index}")
        self.assertNotIn("旧偏好", self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(self.learner().data["likes"]), 6)

    def test_non_string_personal_memory_is_ignored(self):
        learner = self.learner()
        for value in (None, False, 42, ["秘密"]):
            learner.set_name(value)
            learner.add_like(value)
        self.assertEqual(learner.snapshot()["userName"], "")
        self.assertEqual(learner.snapshot()["likes"], [])
        self.assertFalse(self.path.exists())

    def test_failed_save_reports_failure_and_remains_dirty_for_retry(self):
        from unittest.mock import patch
        learner = self.learner()
        with patch("behavior.os.replace", side_effect=PermissionError("locked")):
            learner.note_run(100)
            self.assertFalse(learner.save())
        self.assertTrue(learner._dirty)
        self.assertFalse(self.path.exists())
        self.assertTrue(learner.save())
        self.assertFalse(learner._dirty)
        self.assertEqual(self.learner().snapshot()["runs"], 1)

    def test_load_reconciles_stale_personal_discoveries(self):
        self.path.write_text(json.dumps({
            "userName": "当前名字", "likes": ["听雨"],
            "discoveries": [
                {"id": "name1", "text": "记住了你的名字：旧名字"},
                {"id": "like1", "text": "记得你喜欢旧偏好"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        learner = self.learner()
        text = json.dumps(learner.snapshot(), ensure_ascii=False)
        self.assertNotIn("旧名字", text)
        self.assertNotIn("旧偏好", text)
        self.assertIn("当前名字", text)
        self.assertIn("听雨", text)

    def test_loaded_likes_are_deduplicated_and_bounded(self):
        self.path.write_text(json.dumps({"likes": [" a ", "a", "b", "c", "d", "e", "f", "g"]}), encoding="utf-8")
        self.assertEqual(self.learner().data["likes"], ["b", "c", "d", "e", "f", "g"])

    def test_head_pat_discovery_does_not_override_actual_favorite(self):
        learner = self.learner()
        for _ in range(8):
            learner.note_pet("tail")
        for _ in range(5):
            learner.note_pet("head")
        self.assertEqual(learner.snapshot()["favorite"], "挠尾巴")
        head = next(row for row in learner.data["discoveries"] if row["id"] == "head5")
        self.assertNotIn("最喜欢", head["text"])
        self.assertIn("五次", head["text"])

    def test_loaded_known_discoveries_are_verified_and_use_current_labels(self):
        self.path.write_text(json.dumps({
            "runs": 5, "falls": 3, "recoveries": 0,
            "discoveries": [
                {"id": "fall3", "text": "already recovered"},
                {"id": "run5", "text": "avoids windows"},
                {"id": "run5", "text": "duplicate"},
            ],
        }), encoding="utf-8")
        snapshot = self.learner().snapshot()
        self.assertEqual(len(snapshot["discoveries"]), 1)
        self.assertEqual(snapshot["discoveries"][0]["id"], "run5")
        self.assertIn("五次", snapshot["discoveries"][0]["text"])

    def test_forget_user_erases_interactions_and_keeps_movement_practice(self):
        learner = self.learner()
        learner.note_run(300)
        learner.note_jump()
        learner.note_fall()
        learner.note_getup()
        for _ in range(5):
            learner.note_pet("head")
        learner.note_pet("tail")
        learner.note_pet("body")
        learner.note_chat()
        learner.note_greeting()
        learner.set_name("私密名字")
        learner.add_like("私密偏好")
        learner.data["discoveries"].append({"id": "legacy", "text": "私密旧记忆", "at": ""})
        before = dict(learner.data)
        self.assertTrue(learner.forget_user())
        reloaded = self.learner()
        for key in ("runs", "jumps", "falls", "recoveries", "distance", "agility", "balance"):
            self.assertEqual(reloaded.data[key], before[key])
        for key in ("pets", "headPats", "tailPats", "pokes", "chats", "greetings"):
            self.assertEqual(reloaded.data[key], 0)
        self.assertEqual(reloaded.snapshot()["userName"], "")
        self.assertEqual(reloaded.snapshot()["likes"], [])
        self.assertEqual(reloaded.favorite_spot(), "")
        stored = self.path.read_text(encoding="utf-8")
        self.assertNotIn("私密", stored)
        ids = {row["id"] for row in reloaded.data["discoveries"]}
        self.assertEqual(ids, {"run1", "jump1", "fall1"})

    def test_forget_user_reports_persistent_erasure_failure(self):
        from unittest.mock import patch
        learner = self.learner()
        learner.set_name("尚未擦除的名字")
        with patch("behavior.os.replace", side_effect=PermissionError("locked")):
            self.assertFalse(learner.forget_user())
        self.assertTrue(learner._dirty)
        self.assertEqual(self.learner().data["userName"], "尚未擦除的名字")
        self.assertTrue(learner.save())
        self.assertEqual(self.learner().data["userName"], "")

    def test_environment_default_path(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"DSH_DAFEIYU_BEHAVIOR_FILE": str(self.path)}, clear=True):
            self.assertEqual(default_path(), self.path)
        with patch.dict(os.environ, {"DSH_HOME": self.directory.name}, clear=True):
            self.assertEqual(
                default_path(),
                Path(self.directory.name) / "dsh-dafeiyu-growth" / "behavior.json",
            )


if __name__ == "__main__":
    unittest.main()
