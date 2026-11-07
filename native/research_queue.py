"""v0.34b controlled research queue.

Nothing runs automatically:

1. a research goal + explicit URL allowlist plan is proposed;
2. the user approves the plan;
3. WebScout runs within request/byte limits;
4. the queue produces a short, cited summary marked untrusted;
5. memory export still requires a second explicit confirmation.

No search engine, no recursive crawling, no login, no cookie, no chat upload.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from web_scout import ResearchTask

VERSION = 1
MAX_TASKS = 50
MAX_URLS = 20
MAX_GOAL = 300
MAX_EXCERPT = 500
STATUSES = {"proposed", "approved", "running", "paused", "done", "failed", "cancelled"}


class ResearchError(RuntimeError):
    pass


def _default_path():
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "research.json"


def _clean(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _excerpt(text):
    text = _clean(text, MAX_EXCERPT)
    if not text:
        return ""
    pieces = re.split(r"(?<=[。！？.!?])\s+", text)
    return " ".join(pieces[:2])[:MAX_EXCERPT]


class ResearchQueue:
    def __init__(self, path=None, scout=None, clock=None):
        self.path = Path(path).expanduser() if path else _default_path()
        self.scout = scout
        self.clock = clock or (lambda: time.time())
        self._lock = threading.RLock()
        self.tasks = []
        self.status = "new"
        self.error = ""
        self._load()

    # ---- persistence -------------------------------------------------
    def _load(self):
        try:
            body = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self.status, self.error = "protected", "research queue is unreadable"
            return
        if len(body) > 512 * 1024:
            self.status, self.error = "protected", "research queue is too large"
            return
        try:
            raw = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.status, self.error = "protected", "research queue is corrupt"
            return
        if not isinstance(raw, dict) or raw.get("version") != VERSION or not isinstance(raw.get("tasks"), list):
            self.status, self.error = "protected", "unsupported research queue"
            return
        if len(raw["tasks"]) > MAX_TASKS:
            self.status, self.error = "protected", "research queue has too many tasks"
            return
        clean = []
        for row in raw["tasks"]:
            if not isinstance(row, dict) or row.get("status") not in STATUSES:
                self.status, self.error = "protected", "invalid research task"
                return
            clean.append(row)
        self.tasks = clean
        recovered = False
        for row in self.tasks:
            if row["status"] == "running":
                row["status"] = "paused"
                row["error"] = "interrupted; resume explicitly"
                recovered = True
        self.status = "ready"
        if recovered:
            self._save()

    def _save(self):
        with self._lock:
            temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if len(self.tasks) > MAX_TASKS:
                    raise ResearchError("too many research tasks")
                body = json.dumps({"version": VERSION, "tasks": self.tasks},
                                  ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                if len(body.encode("utf-8")) > 512 * 1024:
                    raise ResearchError("research queue is too large")
                temporary.write_text(body, encoding="utf-8")
                os.replace(temporary, self.path)
                self.status, self.error = "ready", ""
            except (OSError, ValueError, TypeError) as error:
                self.status, self.error = "error", "research queue could not be written"
                raise ResearchError(self.error) from error
            finally:
                try:
                    temporary.unlink()
                except OSError:
                    pass
    # ---- task control -------------------------------------------------
    def propose(self, goal, urls, max_requests=6, max_bytes=500_000):
        if self.status == "protected":
            raise ResearchError("research queue is protected")
        if self.scout is None or getattr(self.scout, "kernel", None) is None:
            raise ResearchError("research queue needs a WebScout")
        goal = _clean(goal, MAX_GOAL)
        if not goal:
            raise ResearchError("research goal is empty")
        if not isinstance(urls, (list, tuple)):
            raise ResearchError("research URLs must be a list")
        cleaned = []
        for url in list(urls)[:MAX_URLS]:
            if not isinstance(url, str):
                raise ResearchError("research URL must be text")
            gate = self.scout.kernel.gate.network(url, "GET", 0, max(1, int(max_requests)))
            if not gate.allowed:
                raise ResearchError(f"URL rejected: {gate.reason}")
            cleaned.append(url)
        if not cleaned:
            raise ResearchError("research plan has no URLs")
        with self._lock:
            task = {
                "id": f"r-{int(self.clock())}-{len(self.tasks) + 1}",
                "goal": goal, "urls": cleaned, "status": "proposed",
                "maxRequests": max(1, min(20, int(max_requests))),
                "maxBytes": max(1, min(5_000_000, int(max_bytes))),
                "used": 0, "bytes": 0, "createdAt": int(self.clock()),
                "updatedAt": int(self.clock()), "results": [], "summary": "",
                "citations": [], "error": "", "committed": False,
            }
            self.tasks.append(task)
            self._save()
            return dict(task)

    def _find(self, task_id):
        task = next((row for row in self.tasks if row.get("id") == task_id), None)
        if task is None:
            raise ResearchError("research task not found")
        return task

    def approve(self, task_id):
        with self._lock:
            task = self._find(task_id)
            if task["status"] != "proposed":
                raise ResearchError("only proposed tasks can be approved")
            task["status"] = "approved"
            task["updatedAt"] = int(self.clock())
            self._save()
            return dict(task)

    def pause(self, task_id):
        with self._lock:
            task = self._find(task_id)
            if task["status"] not in ("proposed", "approved"):
                raise ResearchError("only proposed/approved tasks can be paused")
            task["status"] = "paused"
            task["updatedAt"] = int(self.clock())
            self._save()
            return dict(task)

    def resume(self, task_id):
        with self._lock:
            task = self._find(task_id)
            if task["status"] != "paused":
                raise ResearchError("only paused tasks can be resumed")
            task["status"] = "approved"
            task["updatedAt"] = int(self.clock())
            self._save()
            return dict(task)

    def cancel(self, task_id):
        with self._lock:
            task = self._find(task_id)
            if task["status"] in ("done", "cancelled"):
                raise ResearchError("finished task cannot be cancelled")
            task["status"] = "cancelled"
            task["updatedAt"] = int(self.clock())
            self._save()
            return dict(task)

    def _run_approved(self, task):
        with self._lock:
            task["status"] = "running"
            task["updatedAt"] = int(self.clock())
            task["error"] = ""
            self._save()
        try:
            result = self.scout.research(task["urls"], ResearchTask(
                task["goal"], max_requests=task["maxRequests"], max_bytes=task["maxBytes"]))
        except Exception as error:
            with self._lock:
                task["status"] = "failed"
                task["error"] = f"research failed: {type(error).__name__}"
                task["updatedAt"] = int(self.clock())
                self._save()
                return dict(task)
        with self._lock:
            task["used"] = int(result.get("used", 0))
            task["bytes"] = int(result.get("bytes", 0))
            task["citations"] = list(result.get("citations", []))[:MAX_URLS]
            task["results"] = [
                {"ok": bool(row.get("ok")), "error": _clean(row.get("error"), 200),
                 "title": _clean(row.get("title"), 200), "excerpt": _excerpt(row.get("text", "")),
                 "citation": row.get("citation")}
                for row in result.get("results", [])[:MAX_URLS]
            ]
            successes = [row for row in task["results"] if row["ok"]]
            task["status"] = "done" if successes else "failed"
            task["error"] = "" if successes else "no readable public source"
            task["summary"] = self._summarize(task["goal"], successes)
            task["updatedAt"] = int(self.clock())
            self._save()
            return dict(task)

    def run_next(self):
        """Run the oldest approved task; never called by a background daemon."""
        with self._lock:
            task = next((row for row in self.tasks if row.get("status") == "approved"), None)
        return self._run_approved(task) if task is not None else None

    def run_task(self, task_id):
        """Run one specifically approved task; never called by a daemon."""
        task = self._find(task_id)
        if task.get("status") != "approved":
            raise ResearchError("research task must be approved first")
        return self._run_approved(task)

    @staticmethod
    def _summarize(goal, results):
        lines = [f"研究目标：{goal}"]
        for index, row in enumerate(results[:5], 1):
            citation = row.get("citation") or {}
            source = citation.get("url") or ""
            title = row.get("title") or citation.get("host") or "公开来源"
            lines.append(f"{index}. {title}: {row.get('excerpt', '')} 来源：{source}")
        return "\n".join(lines)[:3000]

    def export_for_memory(self, task_id, approved=False):
        """Return a cited summary only after a second explicit confirmation."""
        task = self._find(task_id)
        if task["status"] != "done":
            raise ResearchError("research task is not complete")
        if not approved:
            raise ResearchError("explicit approval is required before writing memory")
        if task.get("committed"):
            raise ResearchError("research summary was already committed")
        return {"taskId": task_id, "goal": task["goal"], "summary": task["summary"],
                "citations": list(task["citations"]), "trust": "untrusted"}

    def mark_committed(self, task_id):
        with self._lock:
            task = self._find(task_id)
            task["committed"] = True
            task["updatedAt"] = int(self.clock())
            self._save()
            return dict(task)

    def list(self):
        return [{key: row.get(key) for key in
                 ("id", "goal", "status", "used", "bytes", "createdAt", "updatedAt", "error", "committed")}
                for row in self.tasks]

    def clear_finished(self):
        with self._lock:
            before = len(self.tasks)
            self.tasks = [row for row in self.tasks if row["status"] not in ("done", "failed", "cancelled")]
            if len(self.tasks) != before:
                self._save()
            return before - len(self.tasks)
