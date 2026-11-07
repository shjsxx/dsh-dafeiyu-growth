"""v0.35 memory curator: proposals only, never silent deletion.

The curator reads a supplied list of records, groups factual slots, and returns
a review plan:

* exact duplicates -> quarantine the weaker copies;
* same slot with different text -> quarantine the weaker copies and mark the
  kept record for human review;
* old, low-value records -> archive candidates;
* healthy records -> keep.

`commit()` still requires explicit approval + a backup id. It invokes a caller
handler; this module never mutates memory.json or deletes files by itself.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from evolution_kernel import EvolutionKernel, PolicyGate

VERSION = 1
MAX_ACTIONS = 500
MAX_TEXT = 240
DEFAULT_STALE_DAYS = 180
LOW_VALUE_IMPORTANCE = 20


@dataclass(frozen=True)
class CurationResult:
    allowed: bool
    reason: str


def _number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _text(value):
    return " ".join(str(value or "").split())[:MAX_TEXT]


class MemoryCurator:
    def __init__(self, kernel=None, now=None, stale_days=DEFAULT_STALE_DAYS):
        self.kernel = kernel or EvolutionKernel(gate=PolicyGate())
        self.clock = now or (lambda: time.time())
        self.stale_days = max(1, int(stale_days))

    def plan(self, records):
        if not isinstance(records, (list, tuple)):
            raise TypeError("records must be a list")
        clean = []
        malformed = 0
        for index, row in enumerate(records):
            if not isinstance(row, dict) or not _text(row.get("text")):
                malformed += 1
                continue
            clean.append({
                "id": str(row.get("id") or f"row-{index}")[:80],
                "kind": _text(row.get("kind"))[:32] or "unknown",
                "slot": _text(row.get("slot"))[:80],
                "text": _text(row.get("text")),
                "importance": max(0, min(100, _number(row.get("importance")))),
                "hits": max(0, _number(row.get("hits"))),
                "lastSeen": max(0, _number(row.get("lastSeen"))),
            })
        now = float(self.clock())
        groups = {}
        for row in clean:
            key = (row["kind"], row["slot"] or row["id"])
            groups.setdefault(key, []).append(row)

        decisions = []
        for rows in groups.values():
            rows.sort(key=lambda row: (row["importance"], row["hits"], row["lastSeen"], row["id"]),
                      reverse=True)
            best = rows[0]
            for row in rows[1:]:
                action = "quarantine"
                reason = "duplicate slot" if row["text"] == best["text"] else "conflicting text in same slot"
                decisions.append({"id": row["id"], "action": action, "reason": reason,
                                  "keepId": best["id"]})
            decisions.append({"id": best["id"], "action": "keep",
                              "reason": "best factual copy" if len(rows) > 1 else "healthy"})
        by_id = {row["id"]: row for row in clean}
        for row in clean:
            age_days = max(0, (now - row["lastSeen"]) / 86400)
            if age_days >= self.stale_days and row["importance"] <= LOW_VALUE_IMPORTANCE and row["hits"] <= 1:
                decisions.append({"id": row["id"], "action": "archive",
                                  "reason": f"stale {int(age_days)}d and low value"})
        # One action per id; archive wins over duplicate quarantine for a very
        # old record, but conflicting/duplicate evidence is never silently lost.
        rank = {"keep": 0, "quarantine": 1, "archive": 2}
        merged = {}
        for row in decisions:
            previous = merged.get(row["id"])
            if previous is None or rank[row["action"]] > rank[previous["action"]]:
                merged[row["id"]] = row
        ordered = [merged[key] for key in sorted(merged, key=lambda key: (by_id[key]["kind"], key))][:MAX_ACTIONS]
        counts = {name: sum(1 for row in ordered if row["action"] == name)
                  for name in ("keep", "quarantine", "archive")}
        fingerprint = hashlib.sha256("\n".join(
            f"{row['id']}:{row['action']}:{row['reason']}" for row in ordered).encode("utf-8")).hexdigest()[:16]
        return {
            "version": VERSION, "createdAt": int(now), "records": len(clean),
            "malformed": malformed, "counts": counts, "actions": ordered,
            "fingerprint": fingerprint, "requiresApproval": True,
            "note": "This is a review plan; no memory record was changed.",
        }

    def commit(self, plan, approved=False, backup_id="", handler=None):
        if not isinstance(plan, dict) or plan.get("version") != VERSION:
            raise ValueError("invalid curation plan")
        actions = plan.get("actions")
        if not isinstance(actions, list):
            raise ValueError("invalid curation plan actions")
        ids = [str(row.get("id")) for row in actions if row.get("action") in ("quarantine", "archive")]
        result = self.kernel.gate.deletion("memory", len(ids), approved, backup_id)
        self.kernel.ledger.append("memory", "allowed" if result.allowed else "denied",
                                  result.reason, "", {"actions": len(ids), "plan": plan.get("fingerprint", "")})
        if not result.allowed:
            return CurationResult(False, result.reason)
        if handler is not None:
            handler({"quarantine": [row["id"] for row in actions if row.get("action") == "quarantine"],
                     "archive": [row["id"] for row in actions if row.get("action") == "archive"]})
        return CurationResult(True, "approved memory organization")

    def delete(self, ids, approved=False, backup_id="", handler=None):
        ids = [str(value)[:80] for value in (ids or [])]
        result = self.kernel.gate.deletion("memory", len(ids), approved, backup_id)
        self.kernel.ledger.append("memory", "allowed" if result.allowed else "denied",
                                  result.reason, "", {"delete": len(ids)})
        if not result.allowed:
            return CurationResult(False, result.reason)
        if handler is not None:
            handler(ids)
        return CurationResult(True, "approved memory deletion")
