"""Temp-file tests for expressive numeric state; no global profile/network."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import companion_state as module
from companion_state import CompanionState


class CompanionStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "companion.json"
        self.now = 1_800_000_000.0
        self.clock = patch.object(module.time, "time", return_value=self.now)
        self.clock.start()
        self.state = CompanionState(self.path)

    def tearDown(self):
        self.clock.stop()
        self.directory.cleanup()

    def test_constructor_and_snapshot_are_offline_and_do_not_create_files(self):
        self.assertFalse(self.path.exists())
        snap = self.state.snapshot()
        self.assertEqual(snap["mood"], "relaxed")
        self.assertTrue(snap["simulated"])
        self.assertFalse(self.path.exists())

    def test_numeric_events_persist_and_snapshot_is_detached(self):
        result = self.state.apply("pet_head", now=self.now)
        self.assertTrue(result["ok"])
        self.assertGreater(result["after"]["trust"], result["before"]["trust"])
        self.assertEqual(result["after"]["events"], 1)
        again = CompanionState(self.path)
        self.assertEqual(again.snapshot()["events"], 1)
        self.assertEqual(again.snapshot()["trust"], result["after"]["trust"])
        result["after"]["trust"] = -999
        self.assertGreater(self.state.snapshot()["trust"], 0)

    def test_repeated_events_remain_finite_and_bounded(self):
        for event in ("pet_head", "chat", "learn", "run_ok", "fall", "rest", "feedback_no"):
            for _ in range(15):
                self.assertTrue(self.state.apply(event, now=self.now)["ok"])
        snap = self.state.snapshot(self.now)
        for key in ("energy", "trust", "curiosity"):
            self.assertTrue(math.isfinite(snap[key]))
            self.assertGreaterEqual(snap[key], 0)
            self.assertLessEqual(snap[key], 1)
        self.assertGreaterEqual(snap["valence"], -1)
        self.assertLessEqual(snap["valence"], 1)
        self.assertEqual(snap["events"], 105)

    def test_event_count_never_exceeds_persisted_bound(self):
        self.state.data["events"] = 1_000_000_000
        self.state.apply("rest", now=self.now)
        self.assertLessEqual(self.state.snapshot(self.now)["events"], 1_000_000_000)

    def test_offline_absence_never_reduces_trust_or_creates_guilt(self):
        for _ in range(3):
            self.state.apply("pet_head", now=self.now)
        before = self.state.snapshot(self.now)
        future = self.now + 30 * 86400
        with patch.object(module.time, "time", return_value=future):
            after = self.state.snapshot()
            line = self.state.line()
        self.assertEqual(after["trust"], before["trust"])
        self.assertEqual(after["events"], before["events"])
        self.assertEqual(after["mood"], "relaxed")
        self.assertGreaterEqual(after["valence"], .19)
        for phrase in ("不理我", "为什么不来", "让我失望", "惩罚", "抛弃我"):
            self.assertNotIn(phrase, line)

    def test_decay_returns_energy_valence_and_curiosity_toward_baseline(self):
        self.state.data.update(valence=.9, energy=.05, curiosity=.95)
        before = self.state.snapshot(self.now)
        after = self.state.snapshot(self.now + 3600)
        self.assertLess(after["valence"], before["valence"])
        self.assertGreater(after["energy"], before["energy"])
        self.assertLess(after["curiosity"], before["curiosity"])
        self.assertEqual(after["trust"], before["trust"])

    def test_cheek_and_poke_have_short_lived_reactions(self):
        self.state.apply("pet_cheek", now=self.now)
        self.assertEqual(self.state.snapshot(self.now)["mood"], "shy")
        self.assertNotEqual(self.state.snapshot(self.now + 25)["mood"], "shy")
        self.state.apply("poke", now=self.now + 25)
        self.assertEqual(self.state.snapshot(self.now + 25)["mood"], "startled")
        self.assertNotEqual(self.state.snapshot(self.now + 34)["mood"], "startled")

    def test_emotional_chat_comfort_takes_priority_over_own_curiosity(self):
        self.state.apply("chat", "sad", now=self.now)
        for _ in range(3):
            self.state.apply("learn", now=self.now)
        self.assertEqual(self.state.snapshot(self.now)["mood"], "comforting")
        self.assertIn("不催你", self.state.line(user_mood="sad"))
        self.assertNotEqual(self.state.snapshot(self.now + 181)["mood"], "comforting")

    def test_learning_increases_curiosity_without_claiming_sentience(self):
        before = self.state.snapshot(self.now)
        self.state.apply("learn", now=self.now)
        after = self.state.apply("learn", now=self.now)["after"]
        self.assertGreater(after["curiosity"], before["curiosity"])
        self.assertEqual(after["mood"], "curious")
        self.assertTrue(after["simulated"])

    def test_curiosity_without_learning_does_not_claim_new_knowledge(self):
        for _ in range(10):
            self.state.apply("feedback_no", now=self.now)
        self.assertEqual(self.state.snapshot(self.now)["mood"], "curious")
        self.assertNotIn("收录", self.state.line())
        self.assertNotIn("学会", self.state.line())

    def test_feedback_no_does_not_remove_trust_or_punish_owner(self):
        before = self.state.snapshot(self.now)
        after = self.state.apply("feedback_no", now=self.now)["after"]
        self.assertEqual(after["trust"], before["trust"])
        self.assertGreater(after["curiosity"], before["curiosity"])

    def test_read_only_apply_never_commits_event_or_overwrites_file(self):
        self.state.apply("chat", now=self.now)
        previous = self.path.read_bytes()
        reader = CompanionState(self.path, read_only=True)
        before = reader.snapshot(self.now)
        result = reader.apply("learn", now=self.now)
        self.assertFalse(result["ok"])
        self.assertEqual(reader.snapshot(self.now), before)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_corrupt_and_oversized_file_preserved(self):
        for content in (b"{broken", b"[]", b" " * (32 * 1024 + 1)):
            self.path.write_bytes(content)
            state = CompanionState(self.path)
            self.assertEqual(state.status, "protected")
            self.assertFalse(state.apply("learn", now=self.now)["ok"])
            self.assertEqual(self.path.read_bytes(), content)

    def test_save_failure_does_not_pretend_event_was_committed(self):
        self.state.apply("pet_head", now=self.now)
        before = copy.deepcopy(self.state.data)
        previous = self.path.read_bytes()
        with patch.object(module.os, "replace", side_effect=OSError("private details")):
            result = self.state.apply("learn", now=self.now)
        self.assertFalse(result["ok"])
        self.assertEqual(self.state.data, before)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertNotIn("private details", self.state.error)

    def test_failed_save_after_elapsed_time_preserves_pre_attempt_state(self):
        self.state.apply("pet_head", now=self.now)
        before = copy.deepcopy(self.state.data)
        previous = self.path.read_bytes()
        with patch.object(module.os, "replace", side_effect=OSError("full")):
            result = self.state.apply("learn", now=self.now + 100)
        self.assertFalse(result["ok"])
        self.assertEqual(self.state.data, before)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_no_private_strings_in_reason_mood_or_saved_fields(self):
        secret = "private-sentence-name-key-7b91"
        self.assertFalse(self.state.apply(secret, secret, now=self.now)["ok"])
        self.assertTrue(self.state.apply("chat", secret, now=self.now)["ok"])
        self.assertNotIn(secret, self.path.read_text(encoding="utf-8"))
        self.assertNotIn(secret, repr(self.state.snapshot(self.now)))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(set(raw) <= {"version", "valence", "energy", "trust", "curiosity", "events", "updated", "comfortUntil", "reactionUntil", "reaction", "reason"})

    def test_malformed_loaded_numeric_state_cannot_escape_bounds(self):
        raw = dict(self.state.data, valence=float("nan"), energy=float("inf"), trust=True, curiosity="private", events=10 ** 500)
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        state = CompanionState(self.path)
        snap = state.snapshot(self.now)
        for key in ("valence", "energy", "trust", "curiosity", "events"):
            self.assertTrue(math.isfinite(snap[key]))
        self.assertGreaterEqual(snap["events"], 0)
        self.assertLessEqual(snap["events"], 1e9)
        self.assertNotIn("private", repr(snap))


if __name__ == "__main__":
    unittest.main()
