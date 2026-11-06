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
            "memory": {"mode": "proposal-only", "autoDelete": False},
            "persona": {"mode": "bounded-reflection", "maxDelta": self.kernel.gate.max_persona_delta},
        }

    # Thin, explicit delegates. No caller in this module starts them automatically.
    def plan_research(self, goal, max_sources=3):
        """Generate an editable allowlisted URL plan; no network or state write."""
        return self.planner.plan(goal, max_sources=max_sources)

    def propose_research(self, goal, urls, max_requests=6, max_bytes=500_000):
        return self.research.propose(goal, urls, max_requests, max_bytes)

    def approve_research(self, task_id):
        return self.research.approve(task_id)

    def run_research(self, task_id):
        return self.research.run_task(task_id)

    def plan_memory(self, records):
        return self.curator.plan(records)

    def reflect_persona(self, previous, current, events=()):
        return self.reflection.reflect(previous, current, events)
