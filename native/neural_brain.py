"""Isolated, local neural *action preference* for Yunlan (stdlib only).

This is a real 10 -> 12 tanh -> 4 softmax MLP, not a text model. It learns
numeric contextual preferences by clipped categorical supervised SGD. It cannot read
language, infer intent from chat, grant privileges, use a network, or decide
whether an action is safe. The caller MUST apply work/pointer/animation blockers
upstream, pass eligible actions to ``choose``, and never turn a blocked action
into a negative training example. This module deliberately applies no blockers.

Exact feature contract (FEATURE_VERSION = 1), in vector order::

    0  sin(2*pi*hour/24)                  [-1, 1]
    1  cos(2*pi*hour/24)                  [-1, 1]
    2  seconds_since_interaction / 300    [ 0, 1]
    3  ongoing_work                      { 0, 1}
    4  pointer_near                      { 0, 1}
    5  card_open OR chat_open            { 0, 1}
    6  movement_fatigue                   [ 0, 1]
    7  run_success_ratio                 [ 0, 1]
    8  jump_success_ratio                [ 0, 1]
    9  affinity                          [ 0, 1]

``hour`` is fractional local wall-clock hours, clipped to [0,24], with 24
identical to 0. Elapsed time is seconds clipped to [0,300]. Fatigue, success
ratios and affinity MUST already be normalized by the caller; they are clipped
to [0,1], not interpreted as percentages. Missing/invalid numeric fields default
to zero except the success ratios and affinity, which default to 0.5. Numeric
strings, NaN, infinity, and booleans used as continuous numbers are invalid.
Flags accept bools or finite numbers (>= 0.5 means true); strings are false.
Other mapping keys are ignored, never retained, and never written to disk.

``observe`` accepts only the actions rest/run/hop/talk, finite numeric rewards
(clipped to [-1,1]), and the fixed source enum explicit/outcome. Explicit feedback
has weight 1; outcome feedback has weight 0.25. Positive rewards target the chosen
action; negative rewards target a uniform distribution over the other actions.
Cross-entropy gradients are scaled by abs(reward) * source weight. This stable
supervised target avoids vanishing rejection gradients at a saturated policy.
At most three SGD steps occur per observation: newest, one older replay sample
(when available), newest. A zero reward is stored but does not itself generate
an SGD update. Replay is capped at
128 rows containing ONLY numeric features, action index, reward, and source enum.
There is no raw context, chat, name, text, timestamp, or free-form metadata.

Persistence is a single atomic JSON model+replay document plus an atomic .bak
last-good checkpoint. An observation checkpoints the previous committed state;
rollback restores that checkpoint, including counters. checkpoint() explicitly
refreshes it. reset() clears replay/counters and restores seeded initial weights.
model_version starts at 1 and advances per accepted observation; reset/rollback
intentionally rewind it. Construction and prediction never create files.

Corrupt/incompatible/oversized existing models enter status='protected': safe
seeded (or last-good in-memory) predictions remain available, but ALL writes,
including rollback/checkpoint, require an explicit successful reset(). External
file changes are also checked before writes. I/O failures leave memory unchanged.
Statuses are new, ready, read_only, protected, or error; error is None or a fixed
human-readable diagnostic, never raw input text.

There is one writer per resolved path IN THIS PROCESS, enforced by a weak owner
registry and an instance RLock. Additional instances can predict but not write;
close() releases ownership. There is NO cross-process lock or cross-process
writer-safety claim (the optimistic file-change check is not such a lock).
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import tempfile
import threading
from collections.abc import Mapping
import weakref


ACTIONS = ("rest", "run", "hop", "talk")
INPUT_SIZE = 10
HIDDEN_SIZE = 12
OUTPUT_SIZE = 4
SCHEMA_VERSION = 1
FEATURE_VERSION = 1
DEFAULT_SEED = 1729
REPLAY_CAPACITY = 128
MAX_STEPS_PER_OBSERVATION = 3
LEARNING_RATE = 0.05
GRADIENT_CLIP = 1.0
PARAMETER_LIMIT = 8.0
MAX_FILE_BYTES = 256 * 1024
MAX_COUNT = 2**53 - 1
SOURCE_WEIGHTS = {"explicit": 1.0, "outcome": 0.25}

_OWNER_LOCK = threading.RLock()
_OWNERS = weakref.WeakValueDictionary()


def default_path() -> Path:
    """Match behavior.py's growth-file/home layout, with a neural override."""
    override = os.environ.get("DSH_DAFEIYU_NEURAL_FILE")
    if override:
        return Path(override)
    growth_file = os.environ.get("DSH_DAFEIYU_GROWTH_FILE")
    if growth_file:
        return Path(growth_file).resolve().parent / "neural.json"
    dsh_home = os.environ.get("DSH_HOME")
    root = Path(dsh_home) if dsh_home else Path.home() / ".dsh"
    return root / "dsh-dafeiyu-growth" / "neural.json"


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _bounded(value, default=0.0, low=0.0, high=1.0):
    value = _finite(value)
    return default if value is None else max(low, min(high, value))


def _flag(value):
    if isinstance(value, bool):
        return float(value)
    return float(_bounded(value) >= 0.5)


def encode_context(mapping) -> list:
    """Encode only the documented fields as ten finite, bounded floats."""
    context = mapping if isinstance(mapping, Mapping) else {}
    hour = _bounded(context.get("hour"), low=0.0, high=24.0) % 24.0
    angle = hour * (2.0 * math.pi / 24.0)
    return [
        math.sin(angle),
        math.cos(angle),
        _bounded(context.get("seconds_since_interaction"), high=300.0) / 300.0,
        _flag(context.get("ongoing_work")),
        _flag(context.get("pointer_near")),
        max(_flag(context.get("card_open")), _flag(context.get("chat_open"))),
        _bounded(context.get("movement_fatigue")),
        _bounded(context.get("run_success_ratio"), default=0.5),
        _bounded(context.get("jump_success_ratio"), default=0.5),
        _bounded(context.get("affinity"), default=0.5),
    ]


def _initial_state(seed):
    rng = random.Random(seed)
    scale1 = math.sqrt(6.0 / (INPUT_SIZE + HIDDEN_SIZE))
    scale2 = math.sqrt(6.0 / (HIDDEN_SIZE + OUTPUT_SIZE))
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "architecture": [INPUT_SIZE, HIDDEN_SIZE, OUTPUT_SIZE],
        "model_version": 1,
        "sample_count": 0,
        "update_count": 0,
        "weights": {
            "w1": [[rng.uniform(-scale1, scale1) for _ in range(INPUT_SIZE)]
                   for _ in range(HIDDEN_SIZE)],
            "b1": [0.0] * HIDDEN_SIZE,
            "w2": [[rng.uniform(-scale2, scale2) for _ in range(HIDDEN_SIZE)]
                   for _ in range(OUTPUT_SIZE)],
            "b2": [0.0] * OUTPUT_SIZE,
        },
        "replay": [],
    }


def _check_vector(vector, size, low, high):
    if not isinstance(vector, list) or len(vector) != size:
        raise ValueError("invalid vector shape")
    for value in vector:
        value = _finite(value)
        if value is None or not low <= value <= high:
            raise ValueError("invalid numeric value")


def _check_weights(weights):
    if not isinstance(weights, dict) or set(weights) != {"w1", "b1", "w2", "b2"}:
        raise ValueError("invalid weights")
    for name, rows, columns in (("w1", HIDDEN_SIZE, INPUT_SIZE),
                                ("w2", OUTPUT_SIZE, HIDDEN_SIZE)):
        matrix = weights[name]
        if not isinstance(matrix, list) or len(matrix) != rows:
            raise ValueError("invalid matrix shape")
        for row in matrix:
            _check_vector(row, columns, -PARAMETER_LIMIT, PARAMETER_LIMIT)
    _check_vector(weights["b1"], HIDDEN_SIZE, -PARAMETER_LIMIT, PARAMETER_LIMIT)
    _check_vector(weights["b2"], OUTPUT_SIZE, -PARAMETER_LIMIT, PARAMETER_LIMIT)


def _check_state(state):
    keys = {"schema_version", "feature_version", "architecture", "model_version",
            "sample_count", "update_count", "weights", "replay"}
    if not isinstance(state, dict) or set(state) != keys:
        raise ValueError("invalid document fields")
    if (type(state["schema_version"]) is not int
            or state["schema_version"] != SCHEMA_VERSION
            or type(state["feature_version"]) is not int
            or state["feature_version"] != FEATURE_VERSION):
        raise ValueError("incompatible version")
    if (state["architecture"] != [INPUT_SIZE, HIDDEN_SIZE, OUTPUT_SIZE]
            or not isinstance(state["architecture"], list)
            or any(type(size) is not int for size in state["architecture"])):
        raise ValueError("invalid architecture")
    for key in ("sample_count", "update_count", "model_version"):
        value = state[key]
        if type(value) is not int or not 0 <= value <= MAX_COUNT:
            raise ValueError("invalid counter")
    if state["model_version"] < 1:
        raise ValueError("invalid model version")
    if state["update_count"] > state["sample_count"] * MAX_STEPS_PER_OBSERVATION:
        raise ValueError("invalid update count")
    _check_weights(state["weights"])
    replay = state["replay"]
    if (not isinstance(replay, list) or len(replay) > REPLAY_CAPACITY
            or len(replay) != min(state["sample_count"], REPLAY_CAPACITY)):
        raise ValueError("invalid replay size")
    for sample in replay:
        if not isinstance(sample, dict) or set(sample) != {"features", "action", "reward", "source"}:
            raise ValueError("invalid replay fields")
        _check_vector(sample["features"], INPUT_SIZE, -1.0, 1.0)
        if any(value < 0.0 for value in sample["features"][2:]):
            raise ValueError("invalid normalized features")
        if any(sample["features"][index] not in (0.0, 1.0) for index in (3, 4, 5)):
            raise ValueError("invalid flag features")
        if type(sample["action"]) is not int or not 0 <= sample["action"] < OUTPUT_SIZE:
            raise ValueError("invalid replay action")
        reward = _finite(sample["reward"])
        if reward is None or not -1.0 <= reward <= 1.0:
            raise ValueError("invalid reward")
        if not isinstance(sample["source"], str) or sample["source"] not in SOURCE_WEIGHTS:
            raise ValueError("invalid source")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate document field")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON number")


def _read_document(path):
    with path.open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("model file too large")
    state = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object,
                       parse_constant=_reject_constant)
    _check_state(state)
    return state, hashlib.sha256(content).digest()


def _document_bytes(state):
    _check_state(state)
    content = (json.dumps(state, allow_nan=False, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("utf-8")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("model file too large")
    return content


def _write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp",
                                          dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except OSError:
            pass


def _forward(weights, features):
    hidden = [math.tanh(sum(w * x for w, x in zip(row, features)) + bias)
              for row, bias in zip(weights["w1"], weights["b1"])]
    logits = [sum(w * h for w, h in zip(row, hidden)) + bias
              for row, bias in zip(weights["w2"], weights["b2"])]
    peak = max(logits)
    values = [math.exp(value - peak) for value in logits]
    total = sum(values)
    probs = [value / total for value in values]
    if not all(math.isfinite(value) and 0.0 < value <= 1.0 for value in probs):
        raise ValueError("non-finite policy")
    return hidden, probs


def _sgd_step(weights, sample):
    """Backpropagate a stable reward-conditioned categorical cross-entropy."""
    reward = sample["reward"]
    if reward == 0.0:
        return False
    strength = abs(reward) * SOURCE_WEIGHTS[sample["source"]]
    features = sample["features"]
    hidden, probs = _forward(weights, features)
    if reward > 0.0:
        target = [float(index == sample["action"]) for index in range(OUTPUT_SIZE)]
    else:
        target = [float(index != sample["action"]) / (OUTPUT_SIZE - 1)
                  for index in range(OUTPUT_SIZE)]
    dlogits = [strength * (prob - desired) for prob, desired in zip(probs, target)]
    # Compute hidden derivatives with the OLD output weights, before any update.
    dhidden = [(1.0 - value * value)
               * sum(weights["w2"][i][j] * dlogits[i] for i in range(OUTPUT_SIZE))
               for j, value in enumerate(hidden)]
    gradients = {
        "w1": [[delta * value for value in features] for delta in dhidden],
        "b1": dhidden,
        "w2": [[delta * value for value in hidden] for delta in dlogits],
        "b2": dlogits,
    }
    flat = (gradients["b1"] + gradients["b2"]
            + [value for key in ("w1", "w2") for row in gradients[key] for value in row])
    norm = math.hypot(*flat)
    if not math.isfinite(norm):
        raise ValueError("non-finite gradient")
    scale = min(1.0, GRADIENT_CLIP / norm) if norm else 1.0

    def updated(value, gradient):
        value -= LEARNING_RATE * scale * gradient
        if not math.isfinite(value):
            raise ValueError("non-finite update")
        return max(-PARAMETER_LIMIT, min(PARAMETER_LIMIT, value))

    for key in ("w1", "w2"):
        weights[key] = [[updated(value, gradient)
                         for value, gradient in zip(row, grad_row)]
                        for row, grad_row in zip(weights[key], gradients[key])]
    for key in ("b1", "b2"):
        weights[key] = [updated(value, gradient)
                        for value, gradient in zip(weights[key], gradients[key])]
    return True


class NeuralBrain:
    """Bounded neural preference policy; no GUI, text inference, or action safety."""

    def __init__(self, path=None, seed=DEFAULT_SEED):
        if type(seed) is not int or not -(2**63) <= seed < 2**63:
            raise ValueError("seed must be a signed 64-bit integer")
        selected_path = Path(path) if path is not None else default_path()
        self.path = selected_path.expanduser().resolve()
        self.backup_path = self.path.with_name(self.path.name + ".bak")
        self._seed = seed
        self._lock = threading.RLock()
        self._choice_rng = random.Random(seed ^ 0x594C414E)
        self._state = _initial_state(seed)
        self._last_good = copy.deepcopy(self._state)
        self._disk_digest = None
        self._protected = False
        self._closed = False
        self.status = "new"
        self.error = None
        self._owner_key = os.path.normcase(str(self.path.resolve()))
        with _OWNER_LOCK:
            if _OWNERS.get(self._owner_key) is None:
                _OWNERS[self._owner_key] = self
        try:
            self._state, self._disk_digest = _read_document(self.path)
            self._last_good = copy.deepcopy(self._state)
            self.status = "ready"
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, RecursionError):
            self._protect("invalid or unreadable model file; reset required")
        if not self._is_writer() and not self._protected:
            self.status = "read_only"
            self.error = "another instance owns writes for this path in this process"

    @property
    def sample_count(self):
        return self._state["sample_count"]

    @property
    def update_count(self):
        return self._state["update_count"]

    @property
    def model_version(self):
        return self._state["model_version"]

    @property
    def replay_size(self):
        return len(self._state["replay"])

    @property
    def weights(self):
        """Detached numeric weights for diagnostics; modifying them has no effect."""
        with self._lock:
            return copy.deepcopy(self._state["weights"])

    @property
    def replay(self):
        """Detached, bounded numeric replay, with fixed action/source metadata."""
        with self._lock:
            return copy.deepcopy(self._state["replay"])

    def action_stats(self) -> dict:
        """Numeric replay statistics; no context or free-form data."""
        counts = [0] * OUTPUT_SIZE
        rewards = []
        for row in self._state["replay"]:
            counts[int(row["action"])] += 1
            rewards.append(float(row["reward"]))
        total = len(self._state["replay"])
        entropy = 0.0
        for count in counts:
            if count:
                share = count / total
                entropy -= share * math.log(share)
        return {"counts": counts,
                "diversity": round(entropy / math.log(OUTPUT_SIZE), 4) if total else 0.0,
                "avg_reward": round(sum(rewards) / len(rewards), 4) if rewards else 0.0,
                "positive_ratio": round(sum(1 for value in rewards if value > 0) / len(rewards), 4) if rewards else 0.0}

    def info(self) -> dict:
        """Detached training/health metadata containing no user text."""
        with self._lock:
            return {
                "architecture": [INPUT_SIZE, HIDDEN_SIZE, OUTPUT_SIZE],
                "feature_version": FEATURE_VERSION,
                "samples": self.sample_count,
                "updates": self.update_count,
                "version": self.model_version,
                "replay_size": self.replay_size,
                "replay_capacity": REPLAY_CAPACITY,
                "status": self.status,
                "error": self.error,
                **self.action_stats(),
            }

    def _is_writer(self):
        with _OWNER_LOCK:
            return not self._closed and _OWNERS.get(self._owner_key) is self

    def _writable(self, allow_protected=False):
        if not self._is_writer():
            self.error = "instance is closed or another instance owns writes"
            if not self._protected:
                self.status = "read_only"
            return False
        if self._protected and not allow_protected:
            self.error = "protected model; explicit reset required"
            return False
        return True

    def _protect(self, message):
        self._protected = True
        self.status = "protected"
        self.error = message

    def _unchanged_disk(self):
        """Optimistic tamper/change guard, deliberately NOT a process lock."""
        try:
            _, digest = _read_document(self.path)
        except FileNotFoundError:
            if self._disk_digest is None:
                return True
        except (OSError, ValueError, TypeError, RecursionError):
            self._protect("invalid or unreadable model file; reset required")
            return False
        else:
            if digest == self._disk_digest:
                return True
        self._protect("model file changed outside this writer; reset required")
        return False

    def _commit(self, state, checkpoint=True, force=False):
        if not self._writable(allow_protected=force):
            return False
        try:
            content = _document_bytes(state)
            previous = _document_bytes(self._last_good)
        except (ValueError, TypeError, OverflowError, RecursionError):
            self.error = "invalid numeric model update; previous model retained"
            self.status = "error" if not self._protected else "protected"
            return False
        if not force and not self._unchanged_disk():
            return False
        try:
            if checkpoint:
                _write_atomic(self.backup_path, content if force else previous)
            _write_atomic(self.path, content)
        except OSError:
            self.error = "model write failed; previous in-memory model retained"
            self.status = "protected" if self._protected else "error"
            return False
        self._state = state
        self._last_good = copy.deepcopy(state)
        self._disk_digest = hashlib.sha256(content).digest()
        self._protected = False
        self.status = "ready"
        self.error = None
        return True

    def predict(self, context) -> dict:
        """Return finite probabilities for all actions; never apply hard blockers."""
        with self._lock:
            features = encode_context(context)
            try:
                _check_weights(self._state["weights"])
                _, probs = _forward(self._state["weights"], features)
            except (ValueError, TypeError, KeyError, OverflowError):
                self._state = copy.deepcopy(self._last_good)
                self._protect("invalid in-memory model; reset required")
                _, probs = _forward(self._state["weights"], features)
            return dict(zip(ACTIONS, probs))

    def choose(self, context, allowed=None, explore=0.1):
        """Sample an epsilon-uniform mixture over eligible actions; empty -> rest.

        With explore=0, sampling follows the renormalized neural policy (not
        argmax). With explore=1 it is uniform. Unknown actions are ignored. A
        single action string is accepted; at most 64 iterable entries are read.
        An empty/invalid eligible set returns inert 'rest' as the ONLY filtering
        exception, even if rest was not listed; it never initiates movement/chat.
        """
        with self._lock:
            if allowed is None:
                choices = list(ACTIONS)
            elif isinstance(allowed, str):
                choices = [allowed] if allowed in ACTIONS else []
            else:
                try:
                    permitted = {value for value in itertools.islice(iter(allowed), 64)
                                 if isinstance(value, str) and value in ACTIONS}
                except TypeError:
                    return "rest"
                choices = [action for action in ACTIONS if action in permitted]
            if not choices:
                return "rest"
            probs = self.predict(context)
            total = sum(probs[action] for action in choices)
            epsilon = _bounded(explore, default=0.1)
            draw = self._choice_rng.random()
            cumulative = 0.0
            for action in choices:
                cumulative += (1.0 - epsilon) * probs[action] / total + epsilon / len(choices)
                if draw < cumulative:
                    return action
            return choices[-1]  # Round-off only; never escapes the allowed set.

    def observe(self, context, action, reward, source="explicit") -> bool:
        """Commit one numeric reward sample and <=3 clipped backprop SGD steps.

        False means no sample/update was committed. Invalid feedback is rejected;
        finite out-of-range rewards are clipped. This does NOT judge blockers.
        """
        with self._lock:
            if not self._writable():
                return False
            value = _finite(reward)
            if (not isinstance(action, str) or action not in ACTIONS or value is None
                    or not isinstance(source, str) or source not in SOURCE_WEIGHTS):
                self.error = "invalid action, reward, or feedback source"
                return False
            try:
                _check_state(self._state)
            except (ValueError, TypeError, KeyError, OverflowError):
                self._state = copy.deepcopy(self._last_good)
                self._protect("invalid in-memory model; reset required")
                return False
            if (self.sample_count >= MAX_COUNT or self.model_version >= MAX_COUNT
                    or self.update_count > MAX_COUNT - MAX_STEPS_PER_OBSERVATION):
                self.error = "model counter limit reached; reset required"
                return False
            sample = {"features": encode_context(context), "action": ACTIONS.index(action),
                      "reward": max(-1.0, min(1.0, value)), "source": source}
            candidate = copy.deepcopy(self._state)
            history = candidate["replay"]
            schedule = [sample]
            if history:
                # Curriculum replay: prefer strong feedback on rare actions,
                # rotate deterministically so one sample cannot dominate.
                counts = [0] * OUTPUT_SIZE
                for row in history:
                    counts[int(row["action"])] += 1
                ranked = sorted(
                    range(len(history)),
                    key=lambda index: (abs(history[index]["reward"]) + 1.0 / (1 + counts[history[index]["action"]]),
                                       index),
                    reverse=True)
                replay_index = ranked[candidate["sample_count"] % min(8, len(ranked))]
                schedule.append(history[replay_index])
                schedule.append(sample)
            try:
                for row in schedule:
                    if _sgd_step(candidate["weights"], row):
                        candidate["update_count"] += 1
                candidate["sample_count"] += 1
                candidate["model_version"] += 1
                candidate["replay"] = (history + [sample])[-REPLAY_CAPACITY:]
                _check_state(candidate)
            except (ValueError, TypeError, KeyError, OverflowError):
                self.status = "error"
                self.error = "invalid numeric model update; previous model retained"
                return False
            return self._commit(candidate)

    def checkpoint(self) -> bool:
        """Atomically refresh .bak from current state, without changing counters."""
        with self._lock:
            if not self._writable() or not self._unchanged_disk():
                return False
            try:
                _write_atomic(self.backup_path, _document_bytes(self._state))
            except (OSError, ValueError, TypeError, OverflowError, RecursionError):
                self.error = "checkpoint write failed; previous model retained"
                self.status = "error"
                return False
            self.status = "ready" if self._disk_digest is not None else "new"
            self.error = None
            return True

    def rollback(self) -> bool:
        """Restore valid .bak, including replay/counters; never recover protected load."""
        with self._lock:
            if not self._writable():
                return False
            try:
                state, _ = _read_document(self.backup_path)
            except (OSError, ValueError, TypeError, RecursionError):
                self.error = "no valid checkpoint available"
                return False
            return self._commit(state, checkpoint=False)

    def reset(self) -> bool:
        """Explicitly replace model AND checkpoint with clean seeded initial state."""
        with self._lock:
            if not self._writable(allow_protected=True):
                return False
            if not self._commit(_initial_state(self._seed), force=True):
                return False
            self._choice_rng = random.Random(self._seed ^ 0x594C414E)
            return True

    def close(self):
        """Release this instance's in-process writer claim; inference still works."""
        with self._lock:
            with _OWNER_LOCK:
                if _OWNERS.get(self._owner_key) is self:
                    del _OWNERS[self._owner_key]
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()
