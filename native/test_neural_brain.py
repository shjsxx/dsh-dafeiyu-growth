"""Isolated stdlib tests: python -B -m unittest -v test_neural_brain.

No Qt, numpy, helper process, service, GUI, or build/install step is involved.
All model writes are inside TemporaryDirectory, including subprocess tests.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import neural_brain as neural
from neural_brain import ACTIONS, NeuralBrain, encode_context


CONTEXT = {
    "hour": 15.5,
    "seconds_since_interaction": 90.0,
    "ongoing_work": False,
    "pointer_near": False,
    "card_open": False,
    "chat_open": False,
    "movement_fatigue": 0.2,
    "run_success_ratio": 0.8,
    "jump_success_ratio": 0.6,
    "affinity": 0.7,
}


class NeuralBrainTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="yunlan-neural-test-")
        self.root = Path(self.directory.name)
        self.brains = []

    def tearDown(self):
        for brain in self.brains:
            brain.close()
        self.directory.cleanup()

    def brain(self, name=None, seed=37):
        name = name or "model-{}.json".format(len(self.brains))
        brain = NeuralBrain(self.root / name, seed=seed)
        self.brains.append(brain)
        return brain

    def assert_policy(self, probs):
        self.assertEqual(tuple(probs), ACTIONS)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=14)
        for prob in probs.values():
            self.assertTrue(math.isfinite(prob))
            self.assertGreater(prob, 0.0)
            self.assertLessEqual(prob, 1.0)

    def test_exact_feature_contract(self):
        self.assertEqual(encode_context({}), [0.0, 1.0, 0.0, 0.0, 0.0, 0.0,
                                               0.0, 0.5, 0.5, 0.5])
        context = dict(CONTEXT, hour=6, seconds_since_interaction=150,
                       ongoing_work=True, pointer_near=True, chat_open=True)
        encoded = encode_context(context)
        self.assertEqual(len(encoded), 10)
        self.assertAlmostEqual(encoded[0], 1.0)
        self.assertAlmostEqual(encoded[1], 0.0)
        self.assertEqual(encoded[2:], [0.5, 1.0, 1.0, 1.0, 0.2, 0.8, 0.6, 0.7])
        self.assertEqual(encode_context({"hour": 0}), encode_context({"hour": 24}))
        self.assertEqual(encode_context({"card_open": True})[5], 1.0)
        self.assertEqual(encode_context({"chat_open": True})[5], 1.0)
        self.assertEqual(encode_context({"card_open": True, "chat_open": True})[5], 1.0)
        self.assertEqual(encode_context({"hour": True}), encode_context({}))
        self.assertEqual(encode_context({"ongoing_work": "yes"})[3], 0.0)

    def test_feature_invalid_values_and_extremes_are_bounded(self):
        values = [-1e300, 1e300, float("nan"), float("inf"), float("-inf"),
                  True, False, None, "SECRET", [], {}, 10**1000]
        fields = tuple(CONTEXT)
        for value in values:
            with self.subTest(value_type=type(value).__name__, value=repr(value)[:30]):
                features = encode_context(dict.fromkeys(fields, value))
                self.assertEqual(len(features), 10)
                self.assertTrue(all(math.isfinite(item) for item in features))
                self.assertTrue(all(-1.0 <= item <= 1.0 for item in features[:2]))
                self.assertTrue(all(0.0 <= item <= 1.0 for item in features[2:]))
        self.assertEqual(encode_context(None), encode_context({}))
        self.assertEqual(encode_context("not a mapping"), encode_context({}))
        features = encode_context(dict.fromkeys(fields, 1e300))
        self.assertEqual(features[2:], [1.0] * 8)
        self.assertEqual(encode_context({"seconds_since_interaction": -1})[2], 0.0)
        self.assertEqual(encode_context({"movement_fatigue": 100})[6], 1.0)
        self.assertEqual(encode_context({"affinity": 0.7})[9], 0.7)

    def test_default_path_precedence_matches_behavior_layout(self):
        env = {"DSH_DAFEIYU_NEURAL_FILE": "", "DSH_DAFEIYU_GROWTH_FILE": "",
               "DSH_HOME": "", "DSH_DAFEIYU_BEHAVIOR_FILE": "ignored.json"}
        with mock.patch.dict(os.environ, env), mock.patch.object(Path, "home", return_value=self.root):
            self.assertEqual(neural.default_path(), self.root / ".dsh" / "dsh-dafeiyu-growth" / "neural.json")
            os.environ["DSH_HOME"] = str(self.root / "custom-home")
            self.assertEqual(neural.default_path(), self.root / "custom-home" / "dsh-dafeiyu-growth" / "neural.json")
            os.environ["DSH_DAFEIYU_GROWTH_FILE"] = str(self.root / "profile" / "growth.json")
            self.assertEqual(neural.default_path(), self.root / "profile" / "neural.json")
            os.environ["DSH_DAFEIYU_NEURAL_FILE"] = str(self.root / "override.json")
            self.assertEqual(neural.default_path(), self.root / "override.json")
            brain = NeuralBrain(seed=4)
            self.brains.append(brain)
            self.assertEqual(brain.path, self.root / "override.json")
            self.assertFalse(brain.path.exists())

    def test_deterministic_initialization_and_no_constructor_writes(self):
        first, second, different = self.brain(seed=42), self.brain(seed=42), self.brain(seed=43)
        self.assertEqual(first.weights, second.weights)
        self.assertEqual(first.predict(CONTEXT), second.predict(CONTEXT))
        self.assertNotEqual(first.weights, different.weights)
        self.assertNotEqual(first.predict(CONTEXT), different.predict(CONTEXT))
        for brain in (first, second, different):
            self.assertEqual(brain.status, "new")
            self.assertIsNone(brain.error)
            self.assertEqual((brain.sample_count, brain.update_count, brain.model_version), (0, 0, 1))
            self.assertFalse(brain.path.exists())
            self.assertFalse(brain.backup_path.exists())
        self.assert_policy(first.predict(CONTEXT))
        for invalid in (True, "42", 2**64, 1.5):
            with self.assertRaises(ValueError):
                NeuralBrain(self.root / "unused.json", seed=invalid)

    def test_true_backprop_changes_both_layers_and_biases(self):
        brain = self.brain()
        before_weights = brain.weights
        before_probability = brain.predict(CONTEXT)["hop"]
        self.assertTrue(brain.observe(CONTEXT, "hop", 1.0))
        for layer in ("w1", "w2", "b1", "b2"):
            self.assertNotEqual(before_weights[layer], brain.weights[layer], layer)
        self.assertGreater(brain.predict(CONTEXT)["hop"], before_probability)
        self.assertEqual((brain.sample_count, brain.update_count, brain.model_version), (1, 1, 2))
        self.assertEqual(brain.replay_size, 1)
        info = brain.info()
        self.assertEqual(info["architecture"], [10, 12, 4])
        self.assertEqual((info["samples"], info["updates"], info["version"]), (1, 1, 2))
        self.assertEqual(info["replay_capacity"], 128)
        info["architecture"][0] = 999
        self.assertEqual(brain.info()["architecture"], [10, 12, 4])
        self.assertEqual(brain.status, "ready")
        self.assertIsNone(brain.error)
        self.assertTrue(brain.path.is_file())
        self.assertTrue(brain.backup_path.is_file())
        detached = brain.weights
        detached["w1"][0][0] = float("nan")
        self.assertNotEqual(brain.weights["w1"][0][0], detached["w1"][0][0])

    def test_repeated_positive_and_negative_feedback_change_probability(self):
        brain = self.brain()
        initial = brain.predict(CONTEXT)["run"]
        for _ in range(45):
            self.assertTrue(brain.observe(CONTEXT, "run", 1))
        positive = brain.predict(CONTEXT)["run"]
        self.assertGreater(positive, initial + 0.3)
        for _ in range(60):
            self.assertTrue(brain.observe(CONTEXT, "run", -1))
        negative = brain.predict(CONTEXT)["run"]
        self.assertLess(negative, positive - 0.3)
        self.assertLess(negative, initial)
        self.assert_policy(brain.predict(CONTEXT))

    def test_generalizes_to_unseen_nearby_numeric_context(self):
        brain = self.brain()
        nearby = dict(CONTEXT, hour=15.6, seconds_since_interaction=95,
                      movement_fatigue=0.22, affinity=0.69)
        before = brain.predict(nearby)["talk"]
        for _ in range(18):
            self.assertTrue(brain.observe(CONTEXT, "talk", 1))
        self.assertGreater(brain.predict(nearby)["talk"], before + 0.2)
        # No context-key lookup table: unseen raw text does not change prediction.
        same_numbers = dict(CONTEXT, chat_text="UNSEEN PRIVATE CHAT", user_name="SECRET")
        self.assertEqual(brain.predict(CONTEXT), brain.predict(same_numbers))
        self.assertNotIn(encode_context(nearby), [row["features"] for row in brain.replay])

    def test_explicit_feedback_has_greater_weight_than_outcome(self):
        explicit, outcome = self.brain(seed=9), self.brain(seed=9)
        before = explicit.predict(CONTEXT)["talk"]
        self.assertTrue(explicit.observe(CONTEXT, "talk", 0.25, source="explicit"))
        self.assertTrue(outcome.observe(CONTEXT, "talk", 0.25, source="outcome"))
        explicit_change = explicit.predict(CONTEXT)["talk"] - before
        outcome_change = outcome.predict(CONTEXT)["talk"] - before
        self.assertGreater(outcome_change, 0)
        self.assertGreater(explicit_change, outcome_change * 2.5)

    def test_positive_and_negative_sgd_agree_with_finite_difference(self):
        # Numerically verify the analytic hidden-layer derivative, not just a
        # changed bias/output table. Stay below the global clipping threshold.
        for reward in (0.05, -0.05):
            state = neural._initial_state(71)
            weights = state["weights"]
            features = encode_context(CONTEXT)
            sample = {"features": features, "action": 2, "reward": reward, "source": "explicit"}
            original = weights["w1"][0][0]
            epsilon = 1e-6

            def loss(offset):
                shifted = copy.deepcopy(weights)
                shifted["w1"][0][0] += offset
                probs = neural._forward(shifted, features)[1]
                if reward > 0:
                    return -reward * math.log(probs[2])
                return -abs(reward) * sum(math.log(prob) / 3 for i, prob in enumerate(probs) if i != 2)

            numerical_gradient = (loss(epsilon) - loss(-epsilon)) / (2 * epsilon)
            self.assertTrue(neural._sgd_step(weights, sample))
            analytic_gradient = (original - weights["w1"][0][0]) / neural.LEARNING_RATE
            self.assertAlmostEqual(analytic_gradient, numerical_gradient, places=8)

    def test_global_gradient_and_parameters_are_clipped(self):
        weights = neural._initial_state(4)["weights"]
        # Strong hidden-to-output coupling gives a norm larger than the limit.
        weights["w1"] = [[0.0] * neural.INPUT_SIZE for _ in range(neural.HIDDEN_SIZE)]
        weights["w2"] = [[8.0] * neural.HIDDEN_SIZE, [-8.0] * neural.HIDDEN_SIZE,
                         [8.0] * neural.HIDDEN_SIZE, [-8.0] * neural.HIDDEN_SIZE]
        before = copy.deepcopy(weights)
        row = {"features": encode_context(CONTEXT), "action": 0,
               "reward": 1.0, "source": "explicit"}
        neural._sgd_step(weights, row)
        differences = []
        for key in ("w1", "w2"):
            differences.extend(new - old for new_row, old_row in zip(weights[key], before[key])
                               for new, old in zip(new_row, old_row))
        for key in ("b1", "b2"):
            differences.extend(new - old for new, old in zip(weights[key], before[key]))
        self.assertLessEqual(math.hypot(*differences), neural.LEARNING_RATE * neural.GRADIENT_CLIP + 1e-14)
        neural._check_weights(weights)

    def test_policy_finite_at_parameter_limits_and_context_extremes(self):
        brain = self.brain()
        for key in ("w1", "w2"):
            brain._state["weights"][key] = [
                [neural.PARAMETER_LIMIT * (-1 if (i + j) % 2 else 1) for j, _ in enumerate(row)]
                for i, row in enumerate(brain._state["weights"][key])]
        for key in ("b1", "b2"):
            brain._state["weights"][key] = [neural.PARAMETER_LIMIT] * len(brain._state["weights"][key])
        for value in (0, -1e300, 1e300, float("nan"), float("inf"), "private text"):
            self.assert_policy(brain.predict(dict.fromkeys(CONTEXT, value)))
        rng = random.Random(3)
        for _ in range(250):
            self.assert_policy(brain.predict({key: rng.uniform(-1000, 1000) for key in CONTEXT}))

    def test_allowed_filters_and_sampling_are_bounded(self):
        brain = self.brain()
        for explore in (0, 0.1, 1, -100, 100, float("nan"), None):
            for _ in range(12):
                self.assertEqual(brain.choose(CONTEXT, allowed=["hop"], explore=explore), "hop")
                self.assertIn(brain.choose(CONTEXT, allowed=["run", "talk", "INVALID"], explore=explore), ("run", "talk"))
        for allowed in ([], (), set(), ["UNKNOWN"], [None, 5], None):
            if allowed is None:
                self.assertIn(brain.choose(CONTEXT, allowed), ACTIONS)
            else:
                self.assertEqual(brain.choose(CONTEXT, allowed), "rest")
        self.assertEqual(brain.choose(CONTEXT, "rest"), "rest")
        self.assertEqual(brain.choose(CONTEXT, "invalid"), "rest")
        self.assertEqual(brain.choose(CONTEXT, 17), "rest")
        self.assertEqual(brain.choose(CONTEXT, (action for action in ["hop"])), "hop")
        uniform = {brain.choose(CONTEXT, explore=1) for _ in range(100)}
        self.assertEqual(uniform, set(ACTIONS))
        first, second = self.brain(seed=6), self.brain(seed=6)
        self.assertEqual([first.choose(CONTEXT) for _ in range(25)],
                         [second.choose(CONTEXT) for _ in range(25)])
        # The model intentionally does not turn context flags into safety rules.
        self.assertEqual(brain.choose(dict(CONTEXT, ongoing_work=True, pointer_near=True), ["run"]), "run")

    def test_invalid_feedback_is_rejected_and_finite_rewards_are_clipped(self):
        brain = self.brain()
        initial = brain.weights
        for action, reward, source in (("unknown", 1, "explicit"), (None, 1, "explicit"),
                                       ("run", float("nan"), "explicit"),
                                       ("run", float("inf"), "explicit"),
                                       ("run", True, "explicit"), ("run", "1", "explicit"),
                                       ("run", 10**1000, "explicit"),
                                       ("run", 1, "PRIVATE CHAT"), ("run", 1, [])):
            self.assertFalse(brain.observe(CONTEXT, action, reward, source))
            self.assertEqual(brain.sample_count, 0)
            self.assertEqual(brain.weights, initial)
            self.assertFalse(brain.path.exists())
        self.assertTrue(brain.observe(CONTEXT, "run", 1e300))
        self.assertEqual(brain.replay[-1]["reward"], 1.0)
        self.assertTrue(brain.observe(CONTEXT, "run", -1e300))
        self.assertEqual(brain.replay[-1]["reward"], -1.0)
        zero = self.brain()
        self.assertTrue(zero.observe(CONTEXT, "rest", 0))
        self.assertEqual((zero.sample_count, zero.update_count, zero.model_version), (1, 0, 2))
        self.assertEqual(zero.weights, initial)

    def test_replay_capacity_step_limit_and_disk_size(self):
        brain = self.brain()
        for index in range(neural.REPLAY_CAPACITY + 9):
            before = brain.update_count
            self.assertTrue(brain.observe(dict(CONTEXT, seconds_since_interaction=index),
                                          ACTIONS[index % 4], 0.5, source="outcome"))
            self.assertLessEqual(brain.update_count - before, neural.MAX_STEPS_PER_OBSERVATION)
            self.assertLessEqual(brain.replay_size, neural.REPLAY_CAPACITY)
        self.assertEqual(brain.replay_size, neural.REPLAY_CAPACITY)
        self.assertEqual(brain.sample_count, neural.REPLAY_CAPACITY + 9)
        self.assertEqual(brain.model_version, brain.sample_count + 1)
        self.assertAlmostEqual(brain.replay[0]["features"][2], 9 / 300)
        self.assertLess(brain.path.stat().st_size, neural.MAX_FILE_BYTES)
        self.assertLess(brain.backup_path.stat().st_size, neural.MAX_FILE_BYTES)
        self.assert_policy(brain.predict(CONTEXT))

    def test_disk_has_no_raw_text_or_arbitrary_context(self):
        brain = self.brain()
        private = "DO NOT PERSIST THIS PRIVATE USER CHAT 9b70df"
        context = dict(CONTEXT, text=private, chat=private, name=private,
                       profile={"secret": private}, affinity=private)
        self.assertTrue(brain.observe(context, "talk", 1))
        self.assertTrue(brain.observe(context, "talk", 0.1, source="outcome"))
        for path in (brain.path, brain.backup_path):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn(private, content)
            payload = json.loads(content)
            self.assertNotIn("context", payload)
            self.assertNotIn("text", payload)
            for row in payload["replay"]:
                self.assertEqual(set(row), {"features", "action", "reward", "source"})
                self.assertEqual(len(row["features"]), 10)
                self.assertTrue(all(type(value) is float for value in row["features"]))
                self.assertIs(type(row["action"]), int)
                self.assertIn(row["source"], ("explicit", "outcome"))

    def test_corrupt_load_never_overwrites_and_requires_explicit_reset(self):
        valid = neural._initial_state(37)
        malformed_shape = copy.deepcopy(valid)
        malformed_shape["weights"]["w1"][0].pop()
        nan = copy.deepcopy(valid)
        nan["weights"]["w2"][0][0] = float("nan")
        bad_count = dict(valid, sample_count=-1)
        extra_text = dict(valid, user_chat="PRIVATE RAW TEXT")
        wrong_architecture = dict(valid, architecture=[10, 13, 4])
        documents = [b"not JSON", b"[]", b"{}", b"\xff\xfe", b"{}" * neural.MAX_FILE_BYTES,
                     json.dumps(malformed_shape).encode(), json.dumps(nan).encode(),
                     json.dumps(bad_count).encode(), json.dumps(extra_text).encode(),
                     json.dumps(wrong_architecture).encode(), b'{"schema_version":1,"schema_version":1}']
        for index, content in enumerate(documents):
            with self.subTest(index=index):
                path = self.root / "corrupt-{}.json".format(index)
                path.write_bytes(content)
                # A good checkpoint must NOT silently bypass the reset requirement.
                backup = path.with_name(path.name + ".bak")
                backup.write_bytes(neural._document_bytes(valid))
                brain = self.brain(path.name)
                self.assertEqual(brain.status, "protected")
                self.assertIn("reset", brain.error)
                self.assert_policy(brain.predict(CONTEXT))
                self.assertIn(brain.choose(CONTEXT), ACTIONS)
                self.assertFalse(brain.observe(CONTEXT, "run", 1))
                self.assertFalse(brain.rollback())
                self.assertFalse(brain.checkpoint())
                self.assertEqual(path.read_bytes(), content)
                self.assertTrue(brain.reset())
                self.assertEqual(brain.status, "ready")
                self.assertIsNone(brain.error)
                neural._read_document(path)
                self.assertTrue(brain.observe(CONTEXT, "run", 1))

    def test_external_corruption_and_valid_external_change_are_protected(self):
        for name, replacement in (("broken.json", b"corruption"),
                                  ("changed.json", neural._document_bytes(neural._initial_state(19)))):
            brain = self.brain(name)
            self.assertTrue(brain.observe(CONTEXT, "run", 1))
            before = brain.weights
            brain.path.write_bytes(replacement)
            self.assertFalse(brain.observe(CONTEXT, "talk", 1))
            self.assertEqual(brain.weights, before)
            self.assertEqual(brain.sample_count, 1)
            self.assertEqual(brain.path.read_bytes(), replacement)
            self.assertEqual(brain.status, "protected")
            self.assertFalse(brain.rollback())
            self.assertTrue(brain.reset())

    def test_atomic_save_failure_keeps_previous_model_and_cleans_temp(self):
        brain = self.brain()
        self.assertTrue(brain.observe(CONTEXT, "run", 1))
        before = brain.weights
        before_bytes = brain.path.read_bytes()
        real_replace = os.replace

        def failing_replace(source, destination):
            if Path(destination) == brain.path:
                raise OSError("simulated atomic replace failure")
            return real_replace(source, destination)

        with mock.patch.object(neural.os, "replace", side_effect=failing_replace):
            self.assertFalse(brain.observe(CONTEXT, "hop", 1))
        self.assertEqual(brain.weights, before)
        self.assertEqual(brain.sample_count, 1)
        self.assertEqual(brain.path.read_bytes(), before_bytes)
        self.assertEqual(brain.status, "error")
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertTrue(brain.observe(CONTEXT, "hop", 1))
        self.assertEqual(brain.sample_count, 2)
        self.assertIsNone(brain.error)

    def test_last_good_checkpoint_rollback_and_restart(self):
        brain = self.brain()
        self.assertFalse(brain.rollback())
        self.assertTrue(brain.checkpoint())
        self.assertFalse(brain.path.exists())
        self.assertTrue(brain.observe(CONTEXT, "run", 1))
        first_weights = brain.weights
        first_replay = brain.replay
        first_policy = brain.predict(CONTEXT)
        first_counts = (brain.sample_count, brain.update_count, brain.model_version)
        self.assertTrue(brain.checkpoint())
        self.assertTrue(brain.observe(CONTEXT, "talk", -1))
        self.assertNotEqual(brain.weights, first_weights)
        brain.close()
        restarted = self.brain(brain.path.name, seed=999)
        self.assertTrue(restarted.rollback())
        self.assertEqual(restarted.weights, first_weights)
        self.assertEqual(restarted.replay, first_replay)
        self.assertEqual(restarted.predict(CONTEXT), first_policy)
        self.assertEqual((restarted.sample_count, restarted.update_count, restarted.model_version), first_counts)
        self.assertTrue(restarted.rollback())  # Idempotent; does not toggle states.
        self.assertEqual(restarted.weights, first_weights)
        self.assertIsNone(restarted.error)

    def test_invalid_checkpoint_does_not_damage_current_model(self):
        brain = self.brain()
        self.assertTrue(brain.observe(CONTEXT, "run", 1))
        brain.backup_path.write_text("NaN", encoding="utf-8")
        before = brain.path.read_bytes()
        self.assertFalse(brain.rollback())
        self.assertEqual(brain.path.read_bytes(), before)
        self.assertEqual(brain.sample_count, 1)
        self.assertTrue(brain.checkpoint())
        self.assertTrue(brain.rollback())
        self.assertIsNone(brain.error)

    def test_reset_clears_history_and_replaces_checkpoint(self):
        brain = self.brain()
        initial = brain.weights
        for _ in range(3):
            self.assertTrue(brain.observe(CONTEXT, "hop", -1))
        self.assertTrue(brain.reset())
        self.assertEqual(brain.weights, initial)
        self.assertEqual(brain.replay, [])
        self.assertEqual((brain.sample_count, brain.update_count, brain.model_version), (0, 0, 1))
        self.assertTrue(brain.rollback())
        self.assertEqual(brain.weights, initial)
        self.assertEqual(brain.replay, [])

    def test_single_in_process_writer_enforced_and_close_releases(self):
        owner = self.brain("shared.json")
        self.assertTrue(owner.observe(CONTEXT, "run", 1))
        reader = self.brain("shared.json")
        self.assertEqual(reader.status, "read_only")
        self.assertEqual(reader.predict(CONTEXT), owner.predict(CONTEXT))
        before = owner.path.read_bytes()
        self.assertFalse(reader.observe(CONTEXT, "hop", 1))
        self.assertFalse(reader.checkpoint())
        self.assertFalse(reader.rollback())
        self.assertFalse(reader.reset())
        self.assertEqual(owner.path.read_bytes(), before)
        owner.close()
        self.assertFalse(owner.observe(CONTEXT, "run", 1))
        successor = self.brain("shared.json")
        self.assertEqual(successor.status, "ready")
        self.assertTrue(successor.observe(CONTEXT, "hop", 1))
        self.assertEqual(successor.sample_count, 2)
        self.assert_policy(owner.predict(CONTEXT))

    def test_invalid_memory_falls_back_to_last_good_without_writing(self):
        brain = self.brain()
        self.assertTrue(brain.observe(CONTEXT, "talk", 1))
        last_policy = brain.predict(CONTEXT)
        last_disk = brain.path.read_bytes()
        brain._state["weights"]["w1"][0][0] = float("nan")
        self.assertEqual(brain.predict(CONTEXT), last_policy)
        self.assertEqual(brain.status, "protected")
        self.assertFalse(brain.observe(CONTEXT, "run", 1))
        self.assertEqual(brain.path.read_bytes(), last_disk)
        self.assertTrue(brain.reset())

    def test_process_restart_preserves_policy_replay_counts_and_can_continue(self):
        brain = self.brain()
        for _ in range(8):
            self.assertTrue(brain.observe(CONTEXT, "talk", 1))
        expected = brain.predict(CONTEXT)
        counts = (brain.sample_count, brain.update_count, brain.model_version)
        brain.close()
        script = (
            "import json,sys; from neural_brain import NeuralBrain; "
            "b=NeuralBrain(sys.argv[1],seed=999); context=json.loads(sys.argv[2]); "
            "before=b.predict(context); counts=[b.sample_count,b.update_count,b.model_version]; "
            "size=b.replay_size; ok=b.observe(context,'talk',1); "
            "print(json.dumps({'before':before,'counts':counts,'size':size,'ok':ok,"
            "'after_count':b.sample_count,'status':b.status})); b.close()"
        )
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", "-c", script, str(brain.path), json.dumps(CONTEXT)],
                                cwd=str(Path(__file__).parent), env=environment,
                                text=True, capture_output=True, check=True, timeout=20)
        output = json.loads(result.stdout)
        self.assertEqual(output["before"], expected)
        self.assertEqual(tuple(output["counts"]), counts)
        self.assertEqual(output["size"], 8)
        self.assertTrue(output["ok"])
        self.assertEqual(output["after_count"], 9)
        self.assertEqual(output["status"], "ready")
        restarted = self.brain(brain.path.name)
        self.assertEqual(restarted.sample_count, 9)
        self.assertEqual(restarted.replay_size, 9)
        self.assert_policy(restarted.predict(CONTEXT))


if __name__ == "__main__":
    unittest.main()
