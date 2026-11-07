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
            "metrics": self.metrics(),
            "memory": {"mode": "proposal-only", "autoDelete": False},
            "persona": {"mode": "bounded-reflection", "maxDelta": self.kernel.gate.max_persona_delta},
        }

    def metrics(self):
        """Local operational metrics only; never a claim of general intelligence."""
        from collections import Counter
        from memory_engine import MemoryEngine
        from memory_health import MemoryHealthConfig
        from persona_health import PersonaHealthConfig
        from persona_history import PersonaHistory
        from research_scheduler import ResearchSchedulerConfig

        tasks = self.research.list()
        done = sum(1 for row in tasks if row.get("status") == "done")
        failed = sum(1 for row in tasks if row.get("status") == "failed")
        raw_tasks = self.research.tasks
        citations = sum(len(row.get("citations") or []) for row in raw_tasks)
        requests = sum(int(row.get("used") or 0) for row in raw_tasks)
        bytes_used = sum(int(row.get("bytes") or 0) for row in raw_tasks)
        try:
            memory = MemoryEngine(self.data_dir / "memory.json").stats()
        except Exception:
            memory = {"status": "unavailable", "total": 0, "counts": {}, "revision": 0}
        try:
            history = PersonaHistory(self.data_dir / "persona-history.json").entries()
        except Exception:
            history = []
        try:
            persona_status = PersonaHealthConfig(self.data_dir / "persona-health.json").last_status
        except Exception:
            persona_status = ""
        enabled = {}
        for name, config_type, filename in (
            ("researchScheduler", ResearchSchedulerConfig, "research-scheduler.json"),
            ("memoryHealth", MemoryHealthConfig, "memory-health.json"),
            ("personaHealth", PersonaHealthConfig, "persona-health.json"),
        ):
            try:
                enabled[name] = bool(config_type(self.data_dir / filename).enabled)
            except Exception:
                enabled[name] = False
        by_kind = Counter(str(row.get("kind") or "unknown") for row in self.kernel.ledger.events)
        return {
            "generatedAt": int(self.kernel.ledger.clock()),
            "research": {
                "total": len(tasks), "done": done, "failed": failed,
                "successRate": round(done / (done + failed), 2) if done + failed else None,
                "citations": citations, "requests": requests, "bytes": bytes_used,
            },
            "memory": {"status": memory.get("status"), "total": int(memory.get("total") or 0),
                       "revision": int(memory.get("revision") or 0),
                       "counts": dict(memory.get("counts") or {})},
            "persona": {"historyEntries": len(history),
                        "lastRevision": int(history[-1].get("revision", 0)) if history else 0,
                        "lastReviewStatus": persona_status},
            "automation": enabled,
            "audit": {"events": len(self.kernel.ledger.events),
                      "byKind": dict(by_kind)},
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
