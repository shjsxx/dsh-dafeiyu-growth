"""Persistent public-read schedule tests; no actual network."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from learning_state import PublicLearningState, DAY, BACKOFF


class PublicLearningStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "learning.json"
        self.state = PublicLearningState(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_new_install_does_not_grant_itself_network_access(self):
        self.assertFalse(self.state.enabled)
        self.assertFalse(self.state.is_due(1000))
        self.assertFalse(self.state.begin(1000))
        self.assertFalse(self.path.exists())

    def test_owner_enable_is_persistent_and_disabling_stops_new_requests(self):
        self.assertTrue(self.state.set_enabled(True))
        again = PublicLearningState(self.path)
        self.assertTrue(again.is_due(1000))
        self.assertTrue(again.set_enabled(False))
        self.assertFalse(again.is_due(1000))
        self.assertFalse(PublicLearningState(self.path).enabled)

    def test_attempt_is_saved_before_request_to_prevent_restart_loops(self):
        self.state.set_enabled(True)
        self.assertTrue(self.state.begin(1000))
        restarted = PublicLearningState(self.path)
        self.assertFalse(restarted.is_due(1001))
        self.assertFalse(restarted.begin(1001, manual=True))
        self.assertEqual(restarted.next_check, 1000 + BACKOFF[0])

    def test_success_schedules_daily_and_does_not_store_private_content(self):
        self.state.set_enabled(True)
        self.state.begin(1000)
        self.state.finish(True, 42, ("Unicode CLDR / Unicode-3.0",), now=1010)
        self.assertEqual(self.state.next_check, 1010 + DAY)
        self.assertEqual(self.state.last_success, 1010)
        self.assertEqual(self.state.snapshot()["lastAdded"], 42)
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("chat", document)
        self.assertNotIn("key", document)
        self.assertEqual(document["scope"], "public-emotion-lexicons-read-only")

    def test_failures_back_off_and_preserve_last_success(self):
        self.state.finish(True, 1, now=1000)
        for index in range(5):
            self.state.finish(False, error="offline", now=2000)
            self.assertEqual(self.state.next_check, 2000 + BACKOFF[min(index, 3)])
            self.assertEqual(self.state.last_success, 1000)
        self.state.finish(True, 0, now=5000)
        self.assertEqual(self.state.failures, 0)
        self.assertEqual(self.state.error, "")

    def test_manual_read_is_possible_without_enabling_background_schedule(self):
        self.assertTrue(self.state.begin(1000, manual=True))
        self.assertFalse(self.state.enabled)

    def test_corrupt_setting_disables_and_preserves_original(self):
        self.path.write_text("{broken", encoding="utf-8")
        state = PublicLearningState(self.path)
        self.assertTrue(state.protected)
        self.assertFalse(state.set_enabled(True))
        self.assertFalse(state.begin(1000, manual=True))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_cannot_persist_attempt_means_no_request(self):
        self.state.enabled = True
        with patch.object(Path, "write_text", side_effect=OSError("readonly")):
            self.assertFalse(self.state.begin(1000))
        self.assertIn("不会发起联网", self.state.error)

    def test_old_emotion_authorization_does_not_implicitly_enable_other_knowledge(self):
        self.path.write_text('{"enabled":true,"nextCheck":2000}', encoding="utf-8")
        old = PublicLearningState(self.path)
        self.assertTrue(old.enabled)
        self.assertFalse(old.knowledge_enabled)

    def test_owner_knowledge_scope_is_persisted_and_requests_an_initial_check(self):
        self.state.next_check = 32_503_000_000
        self.assertTrue(self.state.set_knowledge_enabled(True))
        again = PublicLearningState(self.path)
        self.assertTrue(again.knowledge_enabled)
        self.assertLess(again.next_check, 32_503_000_000)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['scope'], 'public-safe-curriculum-read-only')
        self.assertTrue(again.set_knowledge_enabled(False))
        self.assertFalse(PublicLearningState(self.path).knowledge_enabled)

    def test_knowledge_scope_write_failure_preserves_previous_scope_and_due_time(self):
        self.state.next_check = 5000
        with patch.object(Path, "write_text", side_effect=OSError("readonly")):
            self.assertFalse(self.state.set_knowledge_enabled(True))
        self.assertFalse(self.state.knowledge_enabled)
        self.assertEqual(self.state.next_check, 5000)

    def test_bad_numeric_setting_fails_closed(self):
        self.path.write_text('{"enabled":true,"failures":null}', encoding="utf-8")
        self.assertFalse(PublicLearningState(self.path).enabled)


if __name__ == "__main__":
    unittest.main()
