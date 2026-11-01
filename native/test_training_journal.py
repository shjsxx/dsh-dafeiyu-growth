"""Pure temp-file audit tests: no live pet, learner or network access."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import training_journal as audit
from training_journal import TrainingJournal


class TrainingJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "training.json"
        self.clock = patch.object(audit.time, "time", return_value=1_800_000_000.0)
        self.clock.start()
        self.journal = TrainingJournal(self.path)

    def tearDown(self):
        self.clock.stop()
        self.directory.cleanup()

    def action(self, metrics=None, status="success"):
        return self.journal.record("action_fit", "action", "local_feedback", status, metrics)

    def test_constructor_and_read_only_preview_create_nothing(self):
        self.assertFalse(self.path.exists())
        reader = TrainingJournal(self.path, read_only=True)
        self.assertIsNone(reader.begin("action_fit", "action", "local_feedback"))
        self.assertFalse(reader.finish(1))
        self.assertFalse(self.path.exists())

    def test_action_steps_derive_from_committed_counter_delta_not_flags(self):
        self.assertIsNotNone(self.action({"updates_before": 4, "updates_after": 7, "weight_steps": 999, "trained": 0}))
        snap = self.journal.snapshot()
        self.assertEqual(snap["totals"]["weight_steps"], 3)
        self.assertEqual(snap["events"][0]["metrics"]["weight_steps"], 3)
        self.assertEqual(snap["events"][0]["metrics"]["trained"], 1)

    def test_missing_or_rewound_counter_pair_is_not_training_evidence(self):
        for metrics in ({"weight_steps": 44, "trained": 1}, {"updates_before": 2},
                        {"updates_before": 5, "updates_after": 2},
                        {"updates_before": 1, "updates_after": 1}):
            self.assertIsNotNone(self.action(metrics))
        self.assertEqual(self.journal.snapshot()["totals"]["weight_steps"], 0)
        self.assertTrue(all(not row["metrics"]["trained"] for row in self.journal.snapshot()["events"]))

    def test_failed_skipped_observed_cancelled_and_unknown_never_add_steps(self):
        for status in ("failed", "skipped", "observed", "cancelled", "interrupted", "no_change"):
            self.assertIsNotNone(self.action({"updates_before": 0, "updates_after": 3, "trained": 1}, status))
        totals = self.journal.snapshot()["totals"]
        self.assertEqual(totals["weight_steps"], 0)
        self.assertEqual(totals["failed"], 1)
        self.assertEqual(totals["interrupted_unknown"], 1)

    def test_download_index_state_and_control_cannot_claim_neural_training(self):
        for kind in ("public_lexicon", "knowledge_index", "companion_adapt", "control", "emotion_read"):
            self.assertIsNotNone(self.journal.record(kind, "general", "core_curriculum", metrics={
                "weight_steps": 99, "trained": 1, "updates_before": 0, "updates_after": 99,
                "loss_before": 8, "loss_after": 0, "adaptation_passes": 23, "added": 4}))
        snap = self.journal.snapshot()
        self.assertEqual(snap["totals"]["weight_steps"], 0)
        self.assertEqual(snap["totals"]["emotion_adaptation_passes"], 0)
        self.assertEqual(snap["totals"]["knowledge_added"], 4)
        for row in snap["events"]:
            self.assertEqual(row["metrics"]["trained"], 0)
            self.assertNotIn("loss_after", row["metrics"])
            self.assertNotIn("adaptation_passes", row["metrics"])
            if row["kind"] != "control":
                self.assertNotIn("updates_after", row["metrics"])

    def test_emotion_adaptation_is_a_separate_non_sgd_total(self):
        self.journal.record("emotion_fit", "emotion", "local_chat", metrics={
            "adaptation_passes": 1, "weight_steps": 70, "updates_before": 1, "updates_after": 71})
        self.journal.record("emotion_fit", "emotion", "local_chat", status="failed", metrics={"adaptation_passes": 10})
        totals = self.journal.snapshot()["totals"]
        self.assertEqual(totals["emotion_adaptation_passes"], 1)
        self.assertEqual(totals["weight_steps"], 0)

    def test_no_duplicate_finish_or_queued_transition(self):
        event = self.journal.begin("action_fit", "action", "local_outcome", queued=True)
        self.assertTrue(self.journal.mark_running(event))
        self.assertFalse(self.journal.mark_running(event))
        self.assertTrue(self.journal.finish(event, metrics={"updates_before": 0, "updates_after": 1}))
        before = self.journal.snapshot()
        self.assertFalse(self.journal.finish(event, metrics={"updates_before": 0, "updates_after": 3}))
        self.assertFalse(self.journal.mark_running(event))
        self.assertEqual(before, self.journal.snapshot())

    def test_restart_marks_pending_commit_unknown_not_failed_and_is_idempotent(self):
        event = self.journal.begin("action_fit", "action", "local_outcome")
        restarted = TrainingJournal(self.path)
        snap = restarted.snapshot()
        self.assertEqual(snap["events"][0]["id"], event)
        self.assertEqual(snap["events"][0]["status"], "interrupted")
        self.assertEqual(snap["events"][0]["error"], "commit_unknown")
        self.assertEqual(snap["totals"]["failed"], 0)
        self.assertEqual(snap["totals"]["interrupted_unknown"], 1)
        self.assertEqual(snap["totals"]["weight_steps"], 0)
        self.assertEqual(TrainingJournal(self.path).snapshot(), snap)

    def test_read_only_pending_preview_does_not_recover_or_rewrite(self):
        self.journal.begin("action_fit", "action", "local_outcome")
        original = self.path.read_bytes()
        reader = TrainingJournal(self.path, read_only=True)
        self.assertEqual(reader.snapshot()["events"][0]["status"], "running")
        self.assertFalse(reader.mark_running(1))
        self.assertFalse(reader.finish(1))
        self.assertEqual(self.path.read_bytes(), original)

    def test_snapshots_and_input_metrics_are_detached(self):
        metrics = {"updates_before": 0, "updates_after": 1}
        labels = {"action": "run"}
        event = self.journal.begin("action_fit", "action", "local_feedback")
        self.journal.finish(event, metrics=metrics, labels=labels)
        metrics["updates_after"] = 888
        labels["action"] = "hop"
        snapshot = self.journal.snapshot()
        snapshot["events"][0]["metrics"]["updates_after"] = 999
        snapshot["totals"]["weight_steps"] = 999
        snapshot["events"][0]["labels"]["action"] = "hop"
        again = self.journal.snapshot()
        self.assertEqual(again["totals"]["weight_steps"], 1)
        self.assertEqual(again["events"][0]["labels"]["action"], "run")

    def test_private_strings_are_never_written_or_echoed(self):
        secret = "private-user-name-sentence-API-key-94c31"
        event = self.journal.begin("emotion_fit", "emotion", "local_chat")
        self.assertTrue(self.journal.finish(event, metrics={"loss_before": secret, "key": secret, "nested": {"text": secret}, "adaptation_passes": 1}, labels={"emotion": "sad", "name": secret, "action": secret}))
        self.assertFalse(self.journal.finish(event, error=secret))
        self.journal.record("control", "general", "owner_control")
        for path in Path(self.directory.name).iterdir():
            if path.is_file():
                self.assertNotIn(secret, path.read_text(encoding="utf-8"))
        self.assertNotIn(secret, repr(self.journal.snapshot()))

    def test_malformed_metrics_are_dropped_without_overflow_or_false_steps(self):
        invalid = (float("nan"), float("inf"), -float("inf"), True, "3", None, [], {}, 10 ** 500, -1, 1.25)
        for value in invalid:
            self.assertIsNotNone(self.action({"updates_before": 0, "updates_after": value, "weight_steps": 99}))
        self.assertEqual(self.journal.snapshot()["totals"]["weight_steps"], 0)
        encoded = self.path.read_text(encoding="utf-8")
        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)

    def test_fraction_and_signed_metric_bounds(self):
        self.action({"reward": 5, "confidence_before": -0.1, "probability_after": 1.2, "duration_ms": -2, "loss_after": -1, "updates_before": 0, "updates_after": 1})
        values = self.journal.snapshot()["events"][0]["metrics"]
        self.assertNotIn("reward", values)
        self.assertNotIn("confidence_before", values)
        self.assertNotIn("probability_after", values)
        self.assertNotIn("loss_after", values)
        self.assertGreaterEqual(values["duration_ms"], 0)

    def test_malformed_api_arguments_have_no_effect(self):
        for value in ([], {}, True, None, 10 ** 500):
            self.assertIsNone(self.journal.begin(value, "action", "local_feedback"))
            self.assertFalse(self.journal.finish(value))
            self.assertFalse(self.journal.mark_running(value))
            self.assertIsNone(self.journal.record("control", "general", "owner_control", status=value))
        self.assertFalse(self.path.exists())

    def assert_protected_preserved(self, content):
        self.path.write_bytes(content)
        journal = TrainingJournal(self.path)
        self.assertEqual(journal.status, "protected")
        self.assertIsNone(journal.begin("control", "general", "owner_control"))
        self.assertEqual(self.path.read_bytes(), content)

    def test_corrupt_truncated_duplicate_and_nonfinite_json_preserved(self):
        for content in (b"{broken", b"[]", b'{"version":1,"version":1}',
                        b'{"version":NaN}', b'{"version":Infinity}', b"\xff"):
            self.assert_protected_preserved(content)

    def test_invalid_structure_ids_counters_and_versions_preserved(self):
        self.action({"updates_before": 0, "updates_after": 1})
        good = json.loads(self.path.read_text(encoding="utf-8"))
        cases = []
        for field, value in (("version", 2), ("revision", True), ("sequence", -1), ("sequence", 10 ** 500)):
            bad = copy.deepcopy(good)
            bad[field] = value
            cases.append(bad)
        bad = copy.deepcopy(good)
        bad["events"].append(copy.deepcopy(bad["events"][0]))
        cases.append(bad)
        bad = copy.deepcopy(good)
        bad["totals"]["runs"] = 0
        cases.append(bad)
        bad = copy.deepcopy(good)
        bad["events"][0]["started"] = "private raw input"
        cases.append(bad)
        for case in cases:
            self.assert_protected_preserved(json.dumps(case).encode("utf-8"))

    def test_malformed_persisted_metrics_are_corruption_not_plausible_zero(self):
        self.action({"updates_before": 0, "updates_after": 1})
        good = json.loads(self.path.read_text(encoding="utf-8"))
        for value in (True, "private raw text", -1, 1.5, 10 ** 500):
            bad = copy.deepcopy(good)
            bad["events"][0]["metrics"]["updates_after"] = value
            self.assert_protected_preserved(json.dumps(bad).encode("utf-8"))

    def test_tampered_persisted_training_flags_fail_closed(self):
        self.action({"updates_before": 0, "updates_after": 1})
        good = json.loads(self.path.read_text(encoding="utf-8"))
        for key, value in (("weight_steps", 999), ("trained", 0), ("adaptation_passes", 4)):
            bad = copy.deepcopy(good)
            bad["events"][0]["metrics"][key] = value
            self.assert_protected_preserved(json.dumps(bad).encode("utf-8"))

    def test_unknown_loaded_private_fields_are_not_reemitted_into_new_files(self):
        self.action()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        secret = "private-external-field-291ac"
        raw["private"] = secret
        raw["events"][0]["labels"]["name"] = secret
        raw["events"][0]["metrics"]["sentence"] = secret
        raw["events"][0]["error"] = secret
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        clean_writer = TrainingJournal(self.path)
        self.assertNotIn(secret, repr(clean_writer.snapshot()))
        self.assertIsNotNone(clean_writer.record("control", "general", "owner_control"))
        for path in (self.path, clean_writer.backup_path):
            self.assertNotIn(secret, path.read_text(encoding="utf-8"))

    def test_oversized_file_is_not_replaced(self):
        self.assert_protected_preserved(b" " * (audit.MAX_BYTES + 1))

    def test_external_change_and_second_writer_are_protected(self):
        other = TrainingJournal(self.path)
        self.action({"updates_before": 0, "updates_after": 1})
        previous = self.path.read_bytes()
        self.assertIsNone(other.begin("control", "general", "owner_control"))
        self.assertEqual(other.status, "protected")
        self.assertEqual(self.path.read_bytes(), previous)
        self.path.write_bytes(b"external-change")
        self.assertIsNone(self.journal.begin("control", "general", "owner_control"))
        self.assertEqual(self.journal.status, "protected")
        self.assertEqual(self.path.read_bytes(), b"external-change")

    def test_atomic_replace_failure_preserves_primary_memory_and_cleans_temps(self):
        self.action({"updates_before": 0, "updates_after": 1})
        old_file = self.path.read_bytes()
        old_state = copy.deepcopy(self.journal.data)
        actual_replace = audit.os.replace

        def fail_primary(source, target):
            if Path(target) == self.path:
                raise OSError("secret OS error must not be logged")
            return actual_replace(source, target)

        with patch.object(audit.os, "replace", side_effect=fail_primary):
            self.assertIsNone(self.journal.begin("control", "general", "owner_control"))
        self.assertEqual(self.path.read_bytes(), old_file)
        self.assertEqual(self.journal.data, old_state)
        self.assertEqual(list(Path(self.directory.name).glob("*.tmp")), [])
        self.assertNotIn("secret", self.journal.error)

    def test_fsync_failure_preserves_old_file(self):
        self.action()
        previous = self.path.read_bytes()
        with patch.object(audit.os, "fsync", side_effect=OSError("cannot sync")):
            self.assertIsNone(self.journal.begin("control", "general", "owner_control"))
        self.assertEqual(self.path.read_bytes(), previous)

    def test_failed_finalization_blocks_new_runs_until_that_row_is_retried(self):
        event = self.journal.begin("action_fit", "action", "local_feedback")
        other = self.journal.begin("control", "general", "owner_control", queued=True)
        with patch.object(audit, "_write_atomic", side_effect=OSError("full")):
            self.assertFalse(self.journal.finish(event, metrics={"updates_before": 0, "updates_after": 1}))
        self.assertIsNone(self.journal.begin("control", "general", "owner_control"))
        self.assertFalse(self.journal.mark_running(other))
        self.assertTrue(self.journal.finish(other, "cancelled"))
        self.assertIsNone(self.journal.begin("control", "general", "owner_control"))
        self.assertTrue(self.journal.finish(event, metrics={"updates_before": 0, "updates_after": 1}))
        self.assertIsNotNone(self.journal.begin("control", "general", "owner_control"))

    def test_recovery_save_failure_protects_without_overwriting(self):
        self.journal.begin("action_fit", "action", "local_feedback")
        previous = self.path.read_bytes()
        with patch.object(audit, "_write_atomic", side_effect=OSError("full")):
            recovered = TrainingJournal(self.path)
        self.assertEqual(recovered.status, "protected")
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertIsNone(recovered.begin("control", "general", "owner_control"))

    def test_count_retention_preserves_lifetime_totals_and_monotonic_ids(self):
        self.journal = TrainingJournal(self.path, limit=10)
        for index in range(15):
            self.action({"updates_before": index, "updates_after": index + 1})
        snap = self.journal.snapshot()
        self.assertEqual(snap["retained"], 10)
        self.assertEqual(snap["totals"]["runs"], 15)
        self.assertEqual(snap["totals"]["weight_steps"], 15)
        self.assertEqual([row["id"] for row in snap["events"]], list(range(15, 5, -1)))

    def test_age_retention_never_evicts_pending(self):
        pending = self.journal.begin("action_fit", "action", "local_outcome")
        self.action()
        with patch.object(audit.time, "time", return_value=1_800_000_000.0 + audit.RETENTION_SECONDS + 1):
            self.journal.record("control", "general", "owner_control")
        rows = self.journal.snapshot()["events"]
        self.assertEqual(len(rows), 2)
        self.assertIn(pending, [row["id"] for row in rows])
        self.assertEqual(self.journal.snapshot()["totals"]["runs"], 3)

    def test_all_pending_limit_blocks_new_run_without_eviction(self):
        self.journal = TrainingJournal(self.path, limit=10)
        for _ in range(10):
            self.assertIsNotNone(self.journal.begin("action_fit", "action", "local_outcome"))
        previous = self.path.read_bytes()
        self.assertIsNone(self.journal.begin("control", "general", "owner_control"))
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(self.journal.snapshot()["retained"], 10)

    def test_read_only_age_pruning_never_changes_disk(self):
        self.action()
        previous = self.path.read_bytes()
        with patch.object(audit.time, "time", return_value=1_800_000_000.0 + audit.RETENTION_SECONDS + 1):
            reader = TrainingJournal(self.path, read_only=True)
        self.assertEqual(reader.snapshot()["retained"], 0)
        self.assertEqual(reader.snapshot()["totals"]["runs"], 1)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_final_curriculum_source_and_domain_enums_are_available(self):
        for source in ("cldr_units", "mime_db", "countries"):
            self.assertIsNotNone(self.journal.record("knowledge_index", "science", source, metrics={"added": 2}))
        self.assertEqual(self.journal.snapshot()["totals"]["knowledge_added"], 6)

    def test_backward_clock_has_nonnegative_duration(self):
        event = self.journal.begin("control", "general", "owner_control")
        with patch.object(audit.time, "time", return_value=1_700_000_000.0):
            self.assertTrue(self.journal.finish(event))
        self.assertEqual(self.journal.snapshot()["events"][0]["metrics"]["duration_ms"], 0)


if __name__ == "__main__":
    unittest.main()
