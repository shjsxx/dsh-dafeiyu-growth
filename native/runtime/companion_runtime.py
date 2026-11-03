"""Focused runtime mixin extracted from the native Qt host.

No separate app/server; the installed host still owns windows, signals and permissions.
"""
from __future__ import annotations
import threading
import time
from growth_native import GrowthDialog
from memory_reply import broad_memory_query, compose_memory_answer


class CompanionRuntime:
    def _with_engine(self, state):
        """Merge independent growth-engine state; XP still mirrors the profile."""
        if not isinstance(state, dict):
            return state
        snapshot = self.growth_engine.sync(state)
        merged = dict(state)
        if not isinstance(snapshot, dict):
            merged["engine"] = {"status": self.growth_engine.status,
                                "error": self.growth_engine.error}
            return merged
        for key in ("xp", "level", "into", "need", "ratio", "stage", "streak", "days"):
            merged[key] = snapshot[key]
        merged["engine"] = {"version": snapshot["version"], "revision": snapshot["revision"],
                            "status": snapshot["status"], "milestones": snapshot["milestones"]}
        return merged


    def _start_crawl(self) -> None:
        """Opt-in public poetry crawl; one fixed MIT/public-domain API file."""
        if self.crawler_busy or not getattr(self.public_learning, "enabled", False):
            return
        self.crawler_busy = True
        def work():
            try:
                record = self.crawler.fetch_poem()
            except Exception as error:
                record = {"error": getattr(error, "code", "crawl_failed")}
            try:
                self.crawl_result.emit(record)
            except RuntimeError:
                pass
        threading.Thread(target=work, name="yunlan-public-crawl", daemon=True).start()


    def _on_crawl_result(self, record) -> None:
        self.crawler_busy = False
        if not isinstance(record, dict) or record.get("error"):
            return
        if self.memory.ingest_external(record) and self.growth_dialog is not None:
            self.growth_dialog.update_memory(self.memory.snapshot())


    def _memory_answer(self, text: str):
        """Shared local-memory answer for local chat and cloud-mode interception."""
        self.memory.ingest_behavior(self.learner.snapshot())
        hits = self.memory.recall(text, 5, kinds=("preference", "habit", "diary"))
        self._capture_retrieval(text, hits)
        if not hits and broad_memory_query(text):
            hits = self.memory.recent(3, kinds=("preference", "habit", "diary"))
        answer = compose_memory_answer(
            text, self.learner.data.get("userName", ""), self.learner.data.get("likes", []), hits)
        return (answer, "记忆引擎 · 本地记忆") if answer else (None, "")


    def _sync_continuity(self) -> None:
        if not self._continuity_started:
            self.continuity.record_startup()
            self._continuity_started = True
        mood = self.emotion.current().get("label", "neutral")
        has_memory = bool(self.memory.recent(1, kinds=("preference", "habit", "diary", "culture")))
        personality = self.personality.snapshot(mood, has_memory)
        memory = self.memory.snapshot()
        view = self.continuity.verify(personality, memory)
        if view.get('stable') is None:
            view['message'] = '首次基线，尚无重启/跨天证据'
        self.continuity.checkpoint(personality, memory)
        if self.growth_dialog is not None:
            self.growth_dialog.update_continuity(view)


    def _sync_personality(self) -> None:
        self.personality.update(self.learning.companion.snapshot(), self.emotion.snapshot(),
                                self.growth_state)
        mood = self.emotion.current().get("label", "neutral")
        has_memory = bool(self.memory.recent(1, kinds=("preference", "habit", "diary", "culture")))
        if self.growth_dialog is not None:
            self.growth_dialog.update_personality(self.personality.snapshot(mood, has_memory))


    def _personality_talk(self, base: str) -> str:
        mood = self.emotion.current().get("label", "neutral")
        hits = self.memory.recent(3, kinds=("preference", "habit", "diary", "culture"))
        return self.personality.proactive(base, hits, mood)


    def _sync_memory(self) -> None:
        """Absorb local summaries and already-validated public records."""
        try:
            self.memory.ingest_state(self.growth_state)
            self.memory.ingest_behavior(self.learner.snapshot())
            knowledge = self.learning.knowledge.snapshot()
            revision = knowledge.get("revision", 0)
            if revision != self._memory_knowledge_revision:
                self.memory.ingest_knowledge(self.learning.knowledge.public_records(), revision)
                self._memory_knowledge_revision = revision
        except (OSError, ValueError, TypeError):
            pass
        if self.growth_dialog is not None:
            self.growth_dialog.update_memory(self.memory.snapshot())


    def _clear_memory_engine(self) -> None:
        self._reset_conversation_context()
        ok = self.memory.clear()
        from retrieval_review import RetrievalReviewStore
        reviews_ok = RetrievalReviewStore().clear()
        message = ('记忆引擎和本地检索反馈已清除。' if ok and reviews_ok else
                   '记忆引擎已清除，但本地检索反馈清除失败，请检查存档。' if ok else
                   '记忆引擎清除失败；对话上下文与发送授权已撤销，请检查存档。')
        ok = bool(ok and reviews_ok)
        if self.growth_dialog is not None:
            self.growth_dialog.update_memory(self.memory.snapshot())
        self._show_overlay(message, "云岚 · 记忆", "SUCCESS" if ok else "ERROR", 4200)
        self._sync_bubble_size()


    def _refresh_growth(self) -> None:
        self._refresh_online_ui()
        if self.learner._dirty:
            self.learner.save()
        if self.growth_dialog is not None and self.growth_dialog.isVisible():
            self.growth_dialog.update_emotion(self.emotion.snapshot())
            self._refresh_learning_ui()
        next_state = self.growth_reader.refresh()
        if next_state is None:
            return
        next_state = self._with_engine(next_state)
        knowledge_snapshot = self.learning.knowledge.snapshot()
        if knowledge_snapshot.get("revision", 0) != self._memory_knowledge_revision:
            self._sync_memory()
        today = time.strftime("%Y-%m-%d")
        if not self._continuity_started or today != getattr(self, "_continuity_day", ""):
            self._sync_continuity()
            self._continuity_day = today
        if next_state == self.growth_state:
            return
        self.growth_state = next_state
        self._sync_memory()
        self._sync_bubble_size()
        self._sync_personality()
        self._sync_continuity()
        self._continuity_day = time.strftime("%Y-%m-%d")
        if self.growth_dialog is not None:
            self.growth_dialog.update_state(self.growth_state)
            self.growth_dialog.update_neural(self.brain.info())
            self.growth_dialog.update_emotion(self.emotion.snapshot())
        self.update()


    def _forget_memory(self) -> None:
        self._reset_conversation_context()
        ok = self.learner.forget_user()
        preference_ok = self.memory.clear_preferences()
        from retrieval_review import RetrievalReviewStore
        reviews_ok = RetrievalReviewStore().clear()
        if self.growth_dialog is not None:
            self.growth_dialog.update_behavior(self.learner.snapshot())
            self.growth_dialog.update_memory(self.memory.snapshot())
            self.growth_dialog.report_memory_status(ok and preference_ok and reviews_ok)
        self.next_talk_ms = self._now_ms() + 120000


    def _show_growth_card(self) -> None:
        self._stop_motion()
        self._refresh_growth()
        self._sync_memory()
        if self.growth_dialog is None:
            self.growth_dialog = GrowthDialog(self)
            self.growth_dialog.forget_requested.connect(self._forget_memory)
            self.growth_dialog.neural_rollback_requested.connect(self._rollback_neural)
            self.growth_dialog.neural_reset_requested.connect(self._reset_neural)
            self.growth_dialog.cloud_settings_requested.connect(self._open_cloud_settings)
            self.growth_dialog.voice_tools_requested.connect(self._open_voice_tools)
            self.growth_dialog.emotion_learning_requested.connect(self._open_web_learning)
            self.growth_dialog.emotion_auto_changed.connect(self._set_auto_learning)
            self.growth_dialog.knowledge_auto_changed.connect(self._set_knowledge_learning)
            self.growth_dialog.online_enabled_changed.connect(self._set_online_enabled)
            self.growth_dialog.online_refresh_requested.connect(lambda: self._start_online(manual=True))
            self.growth_dialog.memory_clear_requested.connect(self._clear_memory_engine)
        self._sync_personality()
        self._sync_continuity()
        self.growth_dialog.update_state(self.growth_state)
        self.growth_dialog.update_behavior(self.learner.snapshot())
        self.growth_dialog.update_neural(self.brain.info())
        self.growth_dialog.update_cloud(self.cloud_config)
        self.growth_dialog.update_emotion(self.emotion.snapshot())
        self.growth_dialog.update_memory(self.memory.snapshot())
        self._refresh_learning_ui()
        self.growth_dialog.open_near(self)

