"""v0.36 persona reflection and rollback planning.

The reflection layer is read-only: it compares two snapshots, links bounded
trait changes to event metadata, and reports drift/core violations. Rollback is
a separate, explicitly approved operation with a backup id; it never mutates the
personality file by itself.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from evolution_kernel import EvolutionKernel, PolicyGate

VERSION = 1
TRAIT_LABELS = {
    "warmth": "温暖",
    "curiosity": "好奇",
    "playfulness": "俏皮",
    "steadiness": "稳定",
    "expressiveness": "表达",
}
TRAITS = tuple(TRAIT_LABELS)
EVENT_KINDS = ("feedback", "interaction", "practice", "reflection")


@dataclass(frozen=True)
class Reflection:
    status: str
    summary: str
    changes: list
    reasons: list
    previousRevision: int
    currentRevision: int


class PersonaReflection:
    def __init__(self, kernel=None, max_delta=2.0):
        self.kernel = kernel or EvolutionKernel(gate=PolicyGate())
        self.max_delta = max(0.0, min(10.0, float(max_delta)))

    @staticmethod
    def _revision(snapshot):
        value = snapshot.get("revision") if isinstance(snapshot, dict) else 0
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    @staticmethod
    def _traits(snapshot):
        traits = snapshot.get("traits") if isinstance(snapshot, dict) else None
        if not isinstance(traits, dict):
            raise ValueError("snapshot has no traits")
        result = {}
        for key in TRAITS:
            value = traits.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"snapshot trait is missing: {key}")
            result[key] = round(max(0.0, min(100.0, float(value))), 2)
        return result

    @staticmethod
    def _evidence(events):
        clean = []
        for event in events or []:
            if not isinstance(event, dict):
                continue
            kind = str(event.get("kind") or "")[:24]
            if kind not in EVENT_KINDS:
                continue
            row = {"kind": kind, "at": int(event.get("at") or 0)}
            if isinstance(event.get("action"), str):
                row["action"] = " ".join(event["action"].split())[:80]
            if isinstance(event.get("trait"), str) and event["trait"] in TRAITS:
                row["trait"] = event["trait"]
            if isinstance(event.get("liked"), bool):
                row["liked"] = event["liked"]
            clean.append(row)
        return clean[:20]

    def reflect(self, previous, current, events=()):
        before = self._traits(previous)
        after = self._traits(current)
        reasons = []
        gate = self.kernel.gate.persona(previous, current)
        if not gate.allowed:
            reasons.append(gate.reason)
        changes = []
        for key in TRAITS:
            delta = round(after[key] - before[key], 2)
            if abs(delta) < 0.05:
                continue
            evidence = [row for row in self._evidence(events) if row.get("trait") == key]
            changes.append({
                "trait": key, "label": TRAIT_LABELS[key], "from": before[key],
                "to": after[key], "delta": delta,
                "evidence": evidence,
                "linked": bool(evidence),
            })
            if not evidence:
                reasons.append(f"{TRAIT_LABELS[key]}变化没有可关联证据")
            if abs(delta) > self.max_delta + 1e-9:
                reasons.append(f"{TRAIT_LABELS[key]}变化超过漂移上限")
        if not changes and gate.allowed:
            status = "stable"
            summary = "人格没有发生可观察的变化。"
        elif gate.allowed and not reasons:
            status = "ok"
            bits = [f"{row['label']} {row['delta']:+.1f}" for row in changes]
            summary = "人格有界变化：" + "、".join(bits)
        else:
            status = "needs_review"
            summary = "人格变化需要复核：" + "；".join(reasons[:5])
        return Reflection(status, summary, changes, reasons,
                          self._revision(previous), self._revision(current))

    def rollback_plan(self, current, target, approved=False, backup_id="", handler=None):
        result = self.kernel.gate.self_modify(approved, backup_id)
        self.kernel.ledger.append("persona", "allowed" if result.allowed else "denied",
                                  "rollback request", "", {
                                      "from": self._revision(current),
                                      "to": self._revision(target),
                                  })
        if not result.allowed:
            return result
        if handler is not None:
            handler(target)
        return result

    def report(self, reflection):
        if not isinstance(reflection, Reflection):
            raise TypeError("reflection must be a Reflection")
        return {
            "version": VERSION, "status": reflection.status, "summary": reflection.summary,
            "changes": reflection.changes, "reasons": reflection.reasons,
            "previousRevision": reflection.previousRevision,
            "currentRevision": reflection.currentRevision,
            "simulated": True,
        }
