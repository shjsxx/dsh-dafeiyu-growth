"""Audit integration using real learners ONLY at injected temporary paths."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import core_curriculum
import knowledge_sources
import learning_hub as module
from companion_state import CompanionState
from emotion_model import EmotionModel
from knowledge_store import KnowledgeStore
from learning_hub import LearningHub
from neural_brain import NeuralBrain
from training_journal import TrainingJournal

CONTEXT = {"hour": 14, "seconds_since_interaction": 130, "ongoing_work": False,
           "pointer_near": False, "card_open": False, "chat_open": False,
           "movement_fatigue": .2, "run_success_ratio": .7, "jump_success_ratio": .6, "affinity": .65}


class LearningHubTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.brain = NeuralBrain(self.root / "neural.json")
        self.emotion = EmotionModel(self.root / "emotion.json")
        self.journal = TrainingJournal(self.root / "training.json")
        self.knowledge = KnowledgeStore(self.root / "knowledge.json")
        self.companion = CompanionState(self.root / "companion.json")
        self.network = patch.object(knowledge_sources, "_get_json", side_effect=AssertionError("network forbidden in hub tests"))
        self.network.start()
        # Any accidental construction of an uninjected default model is a failure.
        with patch.object(module, "TrainingJournal", side_effect=AssertionError("global journal forbidden")), \
             patch.object(module, "KnowledgeStore", side_effect=AssertionError("global index forbidden")), \
             patch.object(module, "CompanionState", side_effect=AssertionError("global companion forbidden")):
            self.hub = LearningHub(self.brain, self.emotion, self.journal, self.knowledge, self.companion)

    def tearDown(self):
        self.network.stop()
        self.brain.close()
        self.emotion.close()
        self.directory.cleanup()

    def row(self, kind, source=None):
        return next(row for row in self.journal.snapshot()["events"] if row["kind"] == kind and (source is None or row["source"] == source))

    def knowledge_result(self):
        records, rejected = knowledge_sources.normalize_countries({"FR": {"name": "France", "capital": "Paris"}})
        return {"ok": True, "source": "countries", "records": records, "rejected": rejected,
                "validated": len(records), "bytes": 80, "hash": "a" * 64, "duration_ms": 2}

    def task(self, source="cldr_zh"):
        return next(task for task in self.hub.prepare_public(include_knowledge=source != "cldr_zh") if task["source"] == source)

    def test_all_paths_are_injected_and_core_bootstrap_is_nontraining(self):
        self.assertIs(self.hub.brain, self.brain)
        self.assertIs(self.hub.emotion, self.emotion)
        self.assertIs(self.hub.journal, self.journal)
        self.assertIs(self.hub.knowledge, self.knowledge)
        self.assertIs(self.hub.companion, self.companion)
        self.assertEqual(self.knowledge.snapshot()["count"], len(core_curriculum.LESSONS))
        row = self.row("knowledge_index", "core_curriculum")
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["metrics"]["trained"], 0)
        self.assertEqual(row["metrics"]["weight_steps"], 0)
        self.assertEqual(self.brain.info()["samples"], 0)
        self.assertFalse(self.brain.path.exists())

    def test_core_bootstrap_is_idempotent_across_reconstruction(self):
        before = self.journal.snapshot()
        with patch.object(self.knowledge, "merge", wraps=self.knowledge.merge) as merge:
            LearningHub(self.brain, self.emotion, self.journal, self.knowledge, self.companion)
        merge.assert_not_called()
        self.assertEqual(self.journal.snapshot(), before)

    def test_obsolete_core_source_version_refreshes_without_duplicate_added_count(self):
        raw = copy.deepcopy(self.knowledge.data)
        raw["sources"]["core_curriculum"]["version"] = "obsolete-version"
        self.knowledge.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        loaded = KnowledgeStore(self.knowledge.path)
        self.assertFalse(loaded.has_source("core_curriculum", core_curriculum.VERSION))
        before_total = self.journal.snapshot()["totals"]["knowledge_added"]
        with patch.object(loaded, "merge", wraps=loaded.merge) as merge:
            LearningHub(self.brain, self.emotion, self.journal, loaded, self.companion)
        merge.assert_called_once()
        self.assertTrue(loaded.has_source("core_curriculum", core_curriculum.VERSION))
        self.assertEqual(loaded.snapshot()["count"], len(core_curriculum.LESSONS))
        self.assertEqual(self.journal.snapshot()["totals"]["knowledge_added"], before_total)
        self.assertEqual(self.row("knowledge_index", "core_curriculum")["metrics"]["added"], 0)

    def test_real_action_fit_logs_actual_before_after_and_same_sample_objective(self):
        before = self.brain.info()
        probabilities = self.brain.predict(CONTEXT)
        self.assertTrue(self.hub.observe_action(CONTEXT, "hop", 1.0, "explicit"))
        after = self.brain.info()
        row = self.row("action_fit")
        values = row["metrics"]
        self.assertEqual(row["source"], "local_feedback")
        self.assertEqual(row["status"], "success")
        for key, field in (("samples", "samples"), ("updates", "updates"), ("version", "version")):
            self.assertEqual(values[key + "_before"], before[field])
            self.assertEqual(values[key + "_after"], after[field])
        self.assertEqual(values["weight_steps"], after["updates"] - before["updates"])
        self.assertGreater(values["weight_steps"], 0)
        self.assertAlmostEqual(values["loss_before"], -math.log(probabilities["hop"]))
        self.assertAlmostEqual(values["probability_before"], probabilities["hop"])
        self.assertAlmostEqual(values["probability_after"], self.brain.predict(CONTEXT)["hop"])
        self.assertGreater(values["probability_after"], values["probability_before"])
        self.assertEqual(self.journal.snapshot()["totals"]["weight_steps"], after["updates"])
        self.assertNotIn("accuracy", values)
        self.assertNotIn("improved", values)

    def test_negative_objective_is_uniform_rejection_not_positive_target(self):
        before = self.brain.predict(CONTEXT)
        self.assertTrue(self.hub.observe_action(CONTEXT, "run", -.8, "outcome"))
        values = self.row("action_fit")["metrics"]
        expected = -.8 * .25 * sum(math.log(before[action]) for action in before if action != "run") / 3
        self.assertAlmostEqual(values["loss_before"], expected)
        self.assertEqual(self.row("action_fit")["source"], "local_outcome")
        self.assertLess(values["probability_after"], values["probability_before"])

    def test_zero_reward_without_replay_is_accepted_but_not_sgd(self):
        self.assertTrue(self.hub.observe_action(CONTEXT, "rest", 0, "explicit"))
        values = self.row("action_fit")["metrics"]
        self.assertEqual(values["samples_after"] - values["samples_before"], 1)
        self.assertEqual(values["version_after"] - values["version_before"], 1)
        self.assertEqual(values["weight_steps"], 0)
        self.assertEqual(values["trained"], 0)
        self.assertNotIn("loss_before", values)

    def test_zero_new_reward_may_train_older_nonzero_replay(self):
        self.hub.observe_action(CONTEXT, "hop", 1, "explicit")
        updates = self.brain.info()["updates"]
        self.assertTrue(self.hub.observe_action(CONTEXT, "rest", 0, "explicit"))
        values = self.row("action_fit")["metrics"]
        self.assertEqual(values["weight_steps"], self.brain.info()["updates"] - updates)
        self.assertGreater(values["weight_steps"], 0)
        self.assertEqual(values["trained"], 1)
        self.assertNotIn("loss_before", values)  # no newest-sample objective at reward zero

    def test_prediction_lookup_and_snapshot_are_not_training(self):
        before = self.brain.info()
        audit_before = self.journal.snapshot()
        for _ in range(3):
            self.brain.predict(CONTEXT)
            self.brain.choose(CONTEXT, ["rest", "run", "hop"])
            self.emotion.classify("我今天很难过")
            self.knowledge.lookup("JSON是什么")
            self.hub.snapshot()
        self.assertEqual(self.brain.info(), before)
        self.assertEqual(self.journal.snapshot(), audit_before)
        self.assertFalse(self.brain.path.exists())
        self.assertFalse(self.emotion.path.exists())
        self.assertFalse(self.companion.path.exists())

    def test_audit_begin_failure_prevents_all_model_mutation_calls(self):
        with patch.object(self.journal, "begin", return_value=None), \
             patch.object(self.brain, "observe", wraps=self.brain.observe) as action, \
             patch.object(self.emotion, "observe", wraps=self.emotion.observe) as emotion, \
             patch.object(self.companion, "apply", wraps=self.companion.apply) as companion:
            self.assertFalse(self.hub.observe_action(CONTEXT, "hop", 1))
            self.assertFalse(self.hub.observe_emotion("我很难过")["learned"])
            self.assertFalse(self.hub.adapt("pet_head"))
            self.assertEqual(self.hub.prepare_public(True), [])
        action.assert_not_called()
        emotion.assert_not_called()
        companion.assert_not_called()

    def test_bootstrap_audit_failure_prevents_index_commit(self):
        fresh = KnowledgeStore(self.root / "another-index.json")
        with patch.object(self.journal, "begin", return_value=None), patch.object(fresh, "merge", wraps=fresh.merge) as merge:
            LearningHub(self.brain, self.emotion, self.journal, fresh, self.companion)
        merge.assert_not_called()
        self.assertFalse(fresh.path.exists())

    def test_finish_failure_means_committed_model_unknown_audit_and_blocks_more_fits(self):
        with patch.object(self.journal, "finish", return_value=False):
            self.assertFalse(self.hub.observe_action(CONTEXT, "run", 1, "explicit"))
        committed = self.brain.info()
        self.assertGreater(committed["updates"], 0)
        self.assertTrue(self.hub.audit_blocked)
        self.assertEqual(self.row("action_fit")["status"], "running")
        self.assertFalse(self.hub.observe_action(CONTEXT, "hop", 1, "explicit"))
        self.assertFalse(self.hub.control("reset"))
        self.assertFalse(self.hub.adapt("learn"))
        self.assertFalse(self.hub.observe_emotion("我很难过")["learned"])
        self.assertEqual(self.brain.info(), committed)
        self.assertEqual(self.hub.snapshot()["journal"]["status"], "error")
        recovered = TrainingJournal(self.journal.path)
        pending = next(row for row in recovered.snapshot()["events"] if row["kind"] == "action_fit")
        self.assertEqual(pending["status"], "interrupted")
        self.assertEqual(pending["error"], "commit_unknown")
        self.assertEqual(recovered.snapshot()["totals"]["failed"], 0)
        self.assertEqual(recovered.snapshot()["totals"]["weight_steps"], 0)

    def test_controls_rewind_model_counters_without_inflating_sgd_totals(self):
        self.hub.observe_action(CONTEXT, "run", 1, "explicit")
        self.hub.observe_action(CONTEXT, "hop", 1, "explicit")
        total = self.journal.snapshot()["totals"]["weight_steps"]
        updates = self.brain.info()["updates"]
        self.assertTrue(self.hub.control("rollback"))
        self.assertLess(self.brain.info()["updates"], updates)
        self.assertTrue(self.hub.control("reset"))
        self.assertEqual(self.brain.info()["updates"], 0)
        self.assertEqual(self.journal.snapshot()["totals"]["weight_steps"], total)
        for row in self.journal.snapshot()["events"]:
            if row["kind"] == "control":
                self.assertEqual(row["metrics"]["weight_steps"], 0)
                self.assertEqual(row["metrics"]["trained"], 0)

    def test_invalid_action_numeric_inputs_are_rejected_without_audit_or_training(self):
        before = self.journal.snapshot()
        for value in (True, float("nan"), float("inf"), "private", 10 ** 500):
            with self.subTest(value=type(value).__name__):
                self.assertFalse(self.hub.observe_action(CONTEXT, "run", value))
        self.assertEqual(self.journal.snapshot(), before)
        self.assertEqual(self.brain.info()["samples"], 0)

    def test_explicit_emotion_uses_transactional_audit_and_separate_adaptation_total(self):
        result = self.hub.observe_emotion("我很难过，像被抽空了一样")
        self.assertTrue(result["audit"]["persisted"])
        self.assertTrue(result["learned"])
        row = self.row("emotion_fit")
        values = row["metrics"]
        self.assertEqual(row["status"], "success")
        self.assertEqual(values["adaptation_passes"], 1)
        self.assertEqual(values["changed"], result["audit"]["changed_weights"])
        self.assertEqual(values["samples_after"], self.emotion.fit_samples)
        self.assertEqual(values["version_after"], self.emotion.revision)
        self.assertEqual(values["weight_steps"], 0)
        self.assertEqual(self.journal.snapshot()["totals"]["weight_steps"], 0)
        self.assertEqual(self.journal.snapshot()["totals"]["emotion_adaptation_passes"], 1)

    def test_emotion_save_failure_logs_failure_without_committed_adaptation(self):
        before = copy.deepcopy(self.emotion.weights)
        with patch.object(self.emotion, "save", return_value=False):
            result = self.hub.observe_emotion("我很难过，像被抽空了一样")
        self.assertFalse(result["learned"])
        self.assertFalse(result["audit"]["persisted"])
        self.assertEqual(self.emotion.weights, before)
        self.assertEqual(self.emotion.fit_samples, 0)
        row = self.row("emotion_fit")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["metrics"]["trained"], 0)
        self.assertEqual(self.journal.snapshot()["totals"]["emotion_adaptation_passes"], 0)

    def test_public_dictionary_save_failure_is_downloaded_not_applied_or_trained(self):
        task = self.task()
        before = copy.deepcopy(self.emotion.weights)
        with patch.object(self.emotion, "save", return_value=False):
            result = self.hub.apply_public(task, {"ok": True, "mapping": {"sad": ["惆怅"]}, "count": 1, "bytes": 80})
        self.assertFalse(result["ok"])
        self.assertEqual(result["added"], 0)
        self.assertEqual(self.emotion.weights, before)
        self.assertEqual(self.emotion.lexicon_count, 0)
        row = self.row("public_lexicon")
        self.assertEqual((row["status"], row["error"]), ("failed", "storage"))
        self.assertEqual(row["metrics"]["trained"], 0)
        self.assertEqual(self.journal.snapshot()["totals"]["weight_steps"], 0)

    def test_no_self_label_is_observation_not_emotion_training(self):
        result = self.hub.observe_emotion("你觉得难过吗")
        self.assertFalse(result["learned"])
        row = self.row("emotion_read")
        self.assertEqual(row["status"], "observed")
        self.assertEqual(row["error"], "no_label")
        self.assertEqual(row["metrics"]["trained"], 0)
        self.assertEqual(self.emotion.fit_samples, 0)

    def test_public_tasks_are_queued_with_fixed_source_ids(self):
        tasks = self.hub.prepare_public(True)
        self.assertEqual({task["source"] for task in tasks}, {"cldr_zh", "countries", "cldr_units", "mime_db", "stackexchange"})
        rows = {row["id"]: row for row in self.journal.snapshot()["events"]}
        self.assertTrue(all(rows[task["id"]]["status"] == "queued" for task in tasks))

    def test_public_knowledge_index_is_nontraining_and_retrievable(self):
        before = self.brain.info()
        task = self.task("countries")
        self.assertTrue(self.hub.apply_public(task, self.knowledge_result())["ok"])
        row = self.row("knowledge_index", "countries")
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["metrics"]["added"], 1)
        self.assertEqual(row["metrics"]["trained"], 0)
        self.assertEqual(row["metrics"]["weight_steps"], 0)
        self.assertEqual(self.brain.info(), before)
        self.assertIn("巴黎", self.knowledge.lookup("法国首都")["text"])
        self.assertEqual(self.journal.snapshot()["totals"]["weight_steps"], 0)
        self.assertEqual(self.emotion.fit_samples, 0)

    def test_public_dictionary_import_and_repeat_are_not_neural_fit(self):
        before = self.brain.info()
        payload = {"ok": True, "mapping": {"sad": ["惆怅"]}, "count": 1, "bytes": 80}
        first = self.hub.apply_public(self.task(), payload)
        self.assertEqual(first, {"ok": True, "added": 1})
        self.assertEqual(self.emotion.snapshot()["lexicon"], 1)
        self.assertEqual(self.row("public_lexicon")["metrics"]["trained"], 0)
        again = self.hub.apply_public(self.task(), payload)
        self.assertEqual(again, {"ok": True, "added": 0})
        self.assertEqual(self.row("public_lexicon")["status"], "no_change")
        self.assertEqual(self.brain.info(), before)
        self.assertEqual(self.emotion.fit_samples, 0)
        self.assertEqual(self.emotion.adaptation_passes, 0)
        self.assertEqual(self.journal.snapshot()["totals"]["weight_steps"], 0)

    def test_disabled_inflight_cancellation_keeps_all_existing_data(self):
        self.hub.apply_public(self.task(), {"ok": True, "mapping": {"sad": ["惆怅"]}, "count": 1})
        before_emotion = copy.deepcopy(self.emotion._state())
        before_knowledge = copy.deepcopy(self.knowledge.data)
        task = self.task()
        with patch.object(self.emotion, "merge_lexicon", wraps=self.emotion.merge_lexicon) as merge:
            result = self.hub.apply_public(task, {"ok": True, "mapping": {"joy": ["欣喜若狂"]}, "count": 1}, cancelled=True)
        merge.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(self.emotion._state(), before_emotion)
        self.assertEqual(self.knowledge.data, before_knowledge)
        self.assertEqual(self.row("public_lexicon")["status"], "cancelled")
        self.assertEqual(self.row("public_lexicon")["error"], "disabled")

    def test_cancelled_knowledge_download_preserves_previously_indexed_source(self):
        self.hub.apply_public(self.task("countries"), self.knowledge_result())
        before = copy.deepcopy(self.knowledge.data)
        task = self.task("countries")
        with patch.object(self.knowledge, "merge", wraps=self.knowledge.merge) as merge:
            result = self.hub.apply_public(task, self.knowledge_result(), cancelled=True)
        merge.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(self.knowledge.data, before)
        self.assertIn("巴黎", self.knowledge.lookup("法国首都")["text"])
        self.assertEqual(self.row("knowledge_index", "countries")["status"], "cancelled")

    def test_failed_download_does_not_apply_or_inflate_training(self):
        task = self.task("countries")
        before = copy.deepcopy(self.knowledge.data)
        result = self.hub.apply_public(task, {"ok": False, "errorCode": "network", "private": "never log this"})
        self.assertFalse(result["ok"])
        self.assertEqual(self.knowledge.data, before)
        row = self.row("knowledge_index", "countries")
        self.assertEqual((row["status"], row["error"]), ("failed", "network"))
        self.assertEqual(row["metrics"]["weight_steps"], 0)
        self.assertNotIn("never log this", repr(self.journal.snapshot()))

    def test_public_index_finish_failure_blocks_future_fits_without_claiming_rollback(self):
        task = self.task("countries")
        with patch.object(self.journal, "finish", return_value=False):
            result = self.hub.apply_public(task, self.knowledge_result())
        self.assertFalse(result["ok"])
        self.assertIn("巴黎", self.knowledge.lookup("法国首都")["text"])
        self.assertTrue(self.hub.audit_blocked)
        self.assertFalse(self.hub.observe_action(CONTEXT, "hop", 1))
        self.assertEqual(self.row("knowledge_index", "countries")["status"], "queued")

    def test_private_inputs_and_keys_do_not_reach_model_or_audit_json(self):
        secret = "private-owner-sentence-key-84bf2"
        context = dict(CONTEXT, name=secret, text=secret, key=secret, affinity=secret)
        self.hub.observe_action(context, "hop", 1, "explicit")
        self.hub.observe_emotion("我叫" + secret + "，我今天很难过")
        self.knowledge.lookup(secret + "，JSON是什么？")
        for path in self.root.iterdir():
            if path.is_file():
                self.assertNotIn(secret, path.read_text(encoding="utf-8"))
        self.assertNotIn(secret, repr(self.hub.snapshot()))


if __name__ == "__main__":
    unittest.main()
