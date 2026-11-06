"""v1.0 integration facade for the controlled-evolution modules.

The service only wires components together. It never starts a background
research loop, never deletes memory, and never opens a network connection until
an explicitly approved research task is run by a caller.
"""
from __future__ import annotations

import os
from pathlib import Path

from evolution_kernel import EvolutionKernel, EvolutionLedger, PolicyGate
from memory_curator import MemoryCurator
from persona_reflection import PersonaReflection
from research_planner import ResearchPlanner
from research_queue import ResearchQueue
from web_scout import WebScout


class EvolutionService:
    def __init__(self, data_dir=None, network_hosts=None, scout=None, clock=None):
        self.data_dir = Path(data_dir).expanduser() if data_dir else self._default_dir()
        if scout is not None and getattr(scout, "kernel", None) is not None:
            # Adopt the supplied scout's kernel so proposal, fetch and audit
            # decisions all share one ledger/allowlist.
            self.kernel = scout.kernel
        else:
            self.kernel = EvolutionKernel(
                EvolutionLedger(self.data_dir / "evolution.json", clock=clock),
                PolicyGate(network_hosts))
        self.scout = scout or WebScout(self.kernel, self.data_dir / "web-cache", clock=clock)
        self.research = ResearchQueue(self.data_dir / "research.json", self.scout, clock=clock)
        self.planner = ResearchPlanner(self.kernel.gate)
        self.curator = MemoryCurator(self.kernel, now=clock)
        self.reflection = PersonaReflection(self.kernel)

    @staticmethod
    def _default_dir():
        return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth"

    def status(self):
        ledger = self.kernel.ledger.verify()
        tasks = self.research.list()
        counts = {}
        for task in tasks:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        return {
            "localOnly": True,
            "audit": ledger,
            "network": {
                "mode": "allowlist",
                "hosts": sorted(self.kernel.gate.network_hosts),
                "budget": "per-task",
            },
            "research": {"total": len(tasks), "statuses": counts},
            "auditRecent": self.kernel.ledger.recent(8),
            "memory": {"mode": "proposal-only", "autoDelete": False},
            "persona": {"mode": "bounded-reflection", "maxDelta": self.kernel.gate.max_persona_delta},
        }

    def record_audit(self, kind, decision, reason, summary="", meta=None):
        """Append one bounded event without failing the user's action."""
        try:
            self.kernel.ledger.append(str(kind), str(decision), str(reason), str(summary), meta)
            return True
        except Exception:
            return False

    # Thin, explicit delegates. No caller in this module starts them automatically.
    def plan_research(self, goal, max_sources=3):
        """Generate an editable allowlisted URL plan; no network or state write."""
        plan = self.planner.plan(goal, max_sources=max_sources)
        self.record_audit("research", "planned", "local allowlist planner",
                          f"{len(plan.get('urls') or [])} sources",
                          {"sources": len(plan.get("urls") or []),
                           "matched": bool(plan.get("matched"))})
        return plan

    def propose_research(self, goal, urls, max_requests=6, max_bytes=500_000):
        task = self.research.propose(goal, urls, max_requests, max_bytes)
        self.record_audit("research", "proposed", "explicit proposal", task["id"],
                          {"urls": len(task.get("urls") or []), "requests": task.get("maxRequests", 0)})
        return task

    def approve_research(self, task_id):
        task = self.research.approve(task_id)
        self.record_audit("research", "approved", "explicit approval", task["id"])
        return task

    def pause_research(self, task_id):
        task = self.research.pause(task_id)
        self.record_audit("research", "paused", "explicit pause", task["id"])
        return task

    def resume_research(self, task_id):
        task = self.research.resume(task_id)
        self.record_audit("research", "resumed", "explicit resume", task["id"])
        return task

    def cancel_research(self, task_id):
        task = self.research.cancel(task_id)
        self.record_audit("research", "cancelled", "explicit cancel", task["id"])
        return task

    def run_research(self, task_id):
        self.record_audit("research", "started", "explicit run", task_id)
        try:
            task = self.research.run_task(task_id)
        except Exception as error:
            self.record_audit("research", "failed", type(error).__name__, task_id)
            raise
        status = "done" if task.get("status") == "done" else "failed"
        self.record_audit("research", status, "run result", task_id,
                          {"requests": task.get("used", 0), "bytes": task.get("bytes", 0)})
        return task

    def plan_memory(self, records):
        return self.curator.plan(records)

    def reflect_persona(self, previous, current, events=()):
        return self.reflection.reflect(previous, current, events)
