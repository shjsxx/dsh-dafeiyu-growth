"""Focused runtime mixin extracted from the native Qt host.

No separate app/server; the installed host still owns windows, signals and permissions.
"""
from __future__ import annotations
import threading
import time
from PySide6.QtCore import QTimer
from live_online import fetch_live_facts
from knowledge_sources import fetch_knowledge_source, CATALOG as KNOWLEDGE_SOURCES
from web_learning import fetch_builtin_lexicon, PUBLIC_SOURCE_NAME


class PublicLearningRuntime:
    def _refresh_online_ui(self) -> None:
        snap = self.online_state.snapshot()
        curriculum = self.public_learning.snapshot(self.learning_busy)
        if self.growth_dialog is not None:
            self.growth_dialog.update_online({"live": snap, "curriculum": curriculum, "comparison": self.emotion_comparison})
        if self.conversation is not None:
            age = time.time() - snap.get("lastSuccess", 0)
            status = "live" if snap["enabled"] and snap.get("lastSuccess") and age < 600 and not snap.get("errorCode") else "cached" if snap.get("lastSuccess") else "offline"
            if not snap["enabled"]:
                status = "offline"
            self.conversation.set_online_state({"status": status, "enabled": snap["enabled"], "summary": snap.get("summary", "")})


    def _set_online_enabled(self, enabled: bool) -> None:
        if not self.online_state.set_enabled(bool(enabled), owner_action=True):
            self._refresh_online_ui()
            return
        self.public_learning.set_realtime_mode(bool(enabled))
        self._refresh_online_ui()
        if enabled:
            QTimer.singleShot(0, self._online_tick)
            QTimer.singleShot(0, self._auto_learning_tick)


    def _online_tick(self) -> None:
        self._refresh_online_ui()
        if self.online_state.is_due():
            self._start_online()


    def _start_online(self, manual=False) -> bool:
        if self.online_worker_busy or self.learning.audit_blocked or self.learning.journal.status in ("protected", "error"):
            return False
        reservation = self.online_state.begin(manual=manual)
        if not reservation["ok"]:
            if manual and self.conversation is not None:
                self.conversation.add_system("动态资料尚在冷却或未开启；不会重复请求。")
            self._refresh_online_ui()
            return False
        token = reservation["attempt_id"]
        task = self.learning.prepare_live()
        if task is None:
            self.online_state.finish({"ok": False, "errorCode": "storage"}, attempt_id=token)
            self._refresh_online_ui()
            return False
        if not self.learning.journal.mark_running(task["id"]):
            self.online_state.finish({"ok": False, "errorCode": "storage"}, attempt_id=token)
            self.learning.apply_public(task, {"ok": False, "errorCode": "storage"})
            return False
        self.online_task, self.online_attempt = task, token
        self.online_worker_busy = True
        self._refresh_learning_ui()
        threading.Thread(target=self._online_worker, args=(token, bool(manual)), daemon=True).start()
        return True


    def _online_worker(self, token, manual) -> None:
        try:
            result = fetch_live_facts()
        except Exception:
            result = {"ok": False, "errorCode": "network"}
        try:
            self.online_result.emit({"token": token, "manual": manual, "result": result})
        except RuntimeError:
            pass


    def _on_online_result(self, payload) -> None:
        if not isinstance(payload, dict) or payload.get("token") != self.online_attempt:
            return
        self.online_worker_busy = False
        token, task = self.online_attempt, self.online_task
        self.online_task, self.online_attempt = None, None
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {"ok": False, "errorCode": "schema"}
        cancelled = not self.online_state.is_current(token)
        flat = dict(result)
        flat.update(result.get("metrics", {}) if isinstance(result.get("metrics"), dict) else {})
        applied = self.learning.apply_public(task, flat, cancelled=cancelled)
        committed = result if cancelled or applied["ok"] or not result.get("ok") else {"ok": False, "errorCode": "storage"}
        finished = self.online_state.finish(committed, attempt_id=token)
        if not cancelled:
            if payload.get("manual") and self.conversation is not None:
                snap = self.online_state.snapshot()
                self.conversation.add_system(snap.get("summary") if applied["ok"] and finished["ok"] else "动态资料更新未完整确认；保留带时间缓存，请检查训练记录和联网状态。")
        self._refresh_learning_ui()


    def _refresh_learning_ui(self) -> None:
        self._refresh_online_ui()
        if self.growth_dialog is not None:
            info = self.public_learning.snapshot(self.learning_busy)
            info["sourceName"] = "CLDR 情感 · 科学/地理 · Stack Overflow 问答摘要" if self.public_learning.knowledge_enabled else PUBLIC_SOURCE_NAME
            info["modelProtected"] = self.learning.audit_blocked or self.learning.journal.status in ("protected", "error")
            info["activeSource"] = self.active_learning_source
            self.growth_dialog.update_learning(info)
            self.growth_dialog.update_companion(self.learning.companion.snapshot())
            training_info = self.learning.snapshot(self.active_learning_source)
            training_info["busy"] = self.learning_busy
            training_info["evaluation"] = self.emotion.snapshot().get("evaluation", {})
            self.growth_dialog.update_training(training_info)


    def _set_auto_learning(self, enabled: bool) -> None:
        self.public_learning.set_enabled(enabled)
        self._refresh_learning_ui()
        if self.public_learning.enabled:
            QTimer.singleShot(0, self._auto_learning_tick)


    def _set_knowledge_learning(self, enabled: bool) -> None:
        self.public_learning.set_knowledge_enabled(enabled)
        self._refresh_learning_ui()
        if enabled and self.public_learning.enabled:
            QTimer.singleShot(0, self._auto_learning_tick)


    def _auto_learning_tick(self) -> None:
        if not self.learning_busy and self.public_learning.is_due():
            self._start_public_learning(manual=False)


    def _open_web_learning(self) -> None:
        # Owner already authorized public reading; no per-fetch modal.
        self._start_public_learning(manual=True)


    def _start_public_learning(self, manual: bool = False) -> bool:
        if self.learning_busy:
            return False
        if self.learning.audit_blocked or self.learning.journal.status in ("protected", "error"):
            self._refresh_learning_ui()
            return False
        if not self.public_learning.begin(manual=manual):
            self._refresh_learning_ui()
            if manual:
                self._show_overlay(self.public_learning.error or "刚刚检查过，稍等一分钟再试。",
                                   "不会重复请求资料源", "WAITING", 3200)
                self._sync_bubble_size()
                self.update()
            return False
        self.learning_scope = self.public_learning.knowledge_enabled
        self.learning_tasks = self.learning.prepare_public(self.learning_scope)
        if not self.learning_tasks:
            self.public_learning.finish(False, error="训练日志未能保存；没有发起联网。")
            self._refresh_learning_ui()
            return False
        self.learning_busy = True
        self._refresh_learning_ui()
        if manual:
            self._show_overlay("开始检查公开课程资料…", "每项结果会记入成长卡的训练页；不上传私聊", "THINKING", 30000)
            self._sync_bubble_size()
            self.update()
        threading.Thread(target=self._learning_worker, args=(not manual,), daemon=True).start()
        return True


    def _on_learning_progress(self, progress) -> None:
        if not isinstance(progress, dict):
            return
        task = next((task for task in self.learning_tasks if task["id"] == progress.get("id") and task["source"] == progress.get("source")), None)
        if task is not None:
            self.learning.journal.mark_running(task["id"])
            self.active_learning_source = task["source"]
            self._refresh_learning_ui()


    def _learning_worker(self, automatic: bool) -> None:
        items = []
        for task in list(self.learning_tasks):
            source = task["source"]
            if (automatic and not self.public_learning.enabled) or (source != "cldr_zh" and not self.public_learning.knowledge_enabled):
                result = {"ok": False, "cancelled": True}
            else:
                try:
                    self.learning_progress.emit(task)
                    protected = self.emotion.status in ("protected", "read_only") if source == "cldr_zh" else self.learning.knowledge.status == "protected"
                    result = {"ok": False, "errorCode": "protected"} if protected else fetch_builtin_lexicon() if source == "cldr_zh" else fetch_knowledge_source(source)
                except Exception:
                    result = {"ok": False, "errorCode": "network"}
            items.append({"task": task, "result": result})
        try:
            self.learning_result.emit({"automatic": automatic, "items": items})
        except RuntimeError:
            pass


    def _on_learning_result(self, result) -> None:
        self.learning_busy = False
        if not isinstance(result, dict):
            result = {"ok": False, "errorCode": "schema"}
        automatic = bool(result.get("automatic"))
        disabled = automatic and not self.public_learning.enabled
        rows = result.get("items")
        # Compatibility for deterministic local smoke fixtures: one source.
        if not isinstance(rows, list):
            rows = [{"task": self.learning_tasks[0], "result": result}] if self.learning_tasks else []
        by_id = {row["task"]["id"]: row.get("result", {}) for row in rows if isinstance(row, dict) and isinstance(row.get("task"), dict) and isinstance(row["task"].get("id"), int)}
        outcomes, sources = [], []
        added, retry_after = 0, 0
        for task in self.learning_tasks:
            item = by_id.get(task["id"], {"ok": False, "errorCode": "schema"})
            if not isinstance(item, dict):
                item = {"ok": False, "errorCode": "schema"}
            cancelled = disabled or item.get("cancelled") is True or (task["source"] != "cldr_zh" and not self.public_learning.knowledge_enabled)
            applied = self.learning.apply_public(task, item, cancelled)
            if not cancelled:
                backoff = item.get("retry_after", 0)
                if type(backoff) is int and backoff > 0:
                    retry_after = max(retry_after, backoff)
                outcomes.append(applied["ok"])
                added += applied["added"]
                if applied["ok"]:
                    sources.append(PUBLIC_SOURCE_NAME if task["source"] == "cldr_zh" else KNOWLEDGE_SOURCES[task["source"]]["name"])
        self.learning_tasks = []
        self.active_learning_source = ""
        ok = bool(outcomes) and all(outcomes)
        error = "部分公开资料未能更新；保留旧资料，详细结果见训练页。"
        if not disabled:
            saved = self.public_learning.finish(ok, added, sources, "" if ok else error, retry_after=retry_after)
            if not saved:
                error = "资料结果已记录，但更新计划未保存；请查看训练页。"
                ok = False
            if self.public_learning.knowledge_enabled and not self.learning_scope:
                self.public_learning.next_check = time.time() + 60
                self.public_learning.save()
        if self.growth_dialog is not None:
            self.growth_dialog.update_emotion(self.emotion.snapshot())
        self._refresh_learning_ui()
        if not automatic:
            self._show_overlay(
                (f"收录了 {added} 条新线索和知识。" if added else "课程已检查，没有新增记录。") if ok else error,
                "每项过程已记录在训练页；资料收录不是大模型训练",
                "SUCCESS" if ok else "ERROR", 5500,
            )
            self._sync_bubble_size()
            self.update()

