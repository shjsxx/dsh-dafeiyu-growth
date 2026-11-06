"""Focused runtime mixin extracted from the native Qt host.

No separate app/server; the installed host still owns windows, signals and permissions.
"""
from __future__ import annotations
import threading
import time
import urllib.parse
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QApplication
from chat import reply_to, DEFAULT_REPLIES
from cloud_chat import load_config, ask_cloud
from cloud_ui import CloudSettingsDialog
from conversation_ui import ConversationDialog
from dialogue import fallback_reply, is_meta_question, meta_reply
from dialogue_planner import extract_topic, followup_reply, is_followup
from memory_reply import compose_public_answer
from emotion_model import LABELS
from reactions import detect_reaction
from companion_context import CompanionContext
from naturalization import naturalize
from relationship import RelationshipStore
from retrieval_review import RetrievalReviewStore
from retrieval_review_ui import RetrievalReviewDialog
from PySide6.QtWidgets import QDialog
import uuid


class ConversationRuntime:
    def _capture_retrieval(self, text, hits):
        self._latest_retrieval = {'query': str(text)[:300], 'hits': [dict(row) for row in hits[:8]]}
        if self.conversation is not None:
            self.conversation.set_retrieval_available(True)

    def _open_retrieval_review(self):
        latest = getattr(self, '_latest_retrieval', None)
        if not latest or self.conversation is None:
            return
        review = RetrievalReviewDialog(latest['query'], latest['hits'], self._review_group, self.conversation)
        if review.exec() == QDialog.DialogCode.Accepted:
            payload = review.payload()
            store = RetrievalReviewStore()
            ok = payload is not None and store.add(**payload, consent=True)
            self.conversation.add_system('检索标注已仅在本机保存；没有上传或训练。' if ok else
                                         '检索标注未保存；请检查本地标注文件的保护状态。')

    def _dialogue_context(self):
        # Native/voice input can arrive before any conversation window exists.
        if not hasattr(self, 'dialogue_context'):
            self.dialogue_context = CompanionContext()
        return self.dialogue_context

    def _relationship_store(self):
        store = getattr(self, 'relationship_store', None)
        if store is None:
            memory = getattr(self, 'memory', None)
            path = memory.path.with_name('relationship.json') if memory is not None else None
            store = RelationshipStore(path)
            self.relationship_store = store
        return store

    def _seed_dialogue_context(self):
        store = self._relationship_store()
        store.seed_context(self._dialogue_context())
        self.last_topic = store.last_topic()

    def _offer_relationship_opening(self):
        """Offer at most one gentle callback per relationship revision."""
        if self.conversation is None:
            return
        try:
            store = self._relationship_store()
            line = store.opening_line()
            if not line:
                return
            self.conversation.show_reply(line, "关系记忆 · 本地", private_context=True)
            store.mark_opening()
            self.last_topic = store.last_topic()
        except (OSError, ValueError, TypeError):
            pass

    def _reset_conversation_context(self):
        """Forget RAM-only context and invalidate outstanding model requests.

        Also purge any open dialog's visible/history/consent state.
        """
        dialog = getattr(self, 'conversation', None)
        if dialog is not None:
            dialog.reset_local_context()
        self._dialogue_context().clear()
        self.last_user_text = ''
        self.last_reply_text = ''
        self.last_topic = ''
        self.last_proactive = ''
        self._last_reply_private = False
        self._latest_retrieval = None
        self._pending_model_text = ''
        self._pending_model_request = None
        self.cloud_busy = False
        self.conversation_serial += 1

    def _record_exchange(self, text, answer, mood='neutral', *, private_context=False):
        self._dialogue_context().record(text, answer, mood=mood)
        try:
            self._relationship_store().remember(self._dialogue_context(), mood)
        except (OSError, ValueError, TypeError):
            pass
        self.last_user_text = str(text)[:200]
        self.last_reply_text = str(answer)[:300]
        self._last_reply_private = bool(private_context)

    def _place_conversation(self, dialog) -> None:
        screen = QApplication.screenAt(QPoint(self.pet_x, self.pet_y)) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = dialog.frameGeometry()
        left = self.frameGeometry().right() + 12
        if left + frame.width() > available.right() + 1:
            left = self.frameGeometry().left() - frame.width() - 12
        left = max(available.left(), min(left, available.right() - frame.width() + 1))
        top = max(available.top(), min(self.frameGeometry().top(), available.bottom() - frame.height() + 1))
        dialog.move(left, top)


    def _ensure_conversation(self) -> None:
        if self.conversation is None:
            self._reset_conversation_context()
            self._seed_dialogue_context()
            self._review_group = uuid.uuid4().hex
            dialog = ConversationDialog(self)
            dialog.local_submitted.connect(self._conversation_local)
            dialog.cloud_submitted.connect(self._conversation_cloud)
            dialog.settings_requested.connect(self._open_cloud_settings)
            dialog.voice_tools_requested.connect(self._open_voice_tools)
            dialog.online_enabled_changed.connect(self._set_online_enabled)
            dialog.online_refresh_requested.connect(lambda: self._start_online(manual=True))
            dialog.closed.connect(self._conversation_closed)
            dialog.retrieval_review_requested.connect(self._open_retrieval_review)
            self.conversation = dialog
            self.cloud_config = load_config()
            dialog.set_cloud_state(self.cloud_config)


    def _open_conversation(self) -> None:
        self._stop_motion()
        self._ensure_conversation()
        self.cloud_config = load_config()
        self.conversation.set_cloud_state(self.cloud_config)
        self._refresh_online_ui()
        self.conversation.show()
        self._place_conversation(self.conversation)
        self._offer_relationship_opening()
        self.conversation.raise_()
        self.conversation.activateWindow()
        self.chat_open = True
        self.conversation.input.setFocus()


    def _conversation_closed(self) -> None:
        if self.voice_tools is not None:
            self.voice_tools.close()
        else:
            self._voice_stop()
        self.chat_open = False
        self.conversation = None
        self._reset_conversation_context()
        # Dismissing a proactive card means give the owner some space.
        self.next_talk_ms = self._now_ms() + 300000


    def _conversation_local(self, text: str) -> None:
        self.last_interaction_ms = self._now_ms()
        if self._intercept_command(text):
            return
        reading = self._chat_reading(text)
        self._refresh_learning_ui()
        mood = reading.get("mood", "neutral")
        if mood in ("sad", "tired", "anxious"):
            self.comfort_until_ms = self._now_ms() + 60000
            self.next_talk_ms = self._now_ms() + 30000
        if mood == "joy":
            self._spawn_hearts(3)
        name = self.learner.data.get("userName", "")
        reply = reply_to(text, name, mood if mood != "neutral" else "")
        learned = reply.get("name") or ""
        if learned and self._chat_may_learn:
            self.learner.set_name(learned)
            name = learned
        if reply.get("like") and self._chat_may_learn:
            self.learner.add_like(reply["like"])
        if self._chat_may_learn:
            self.learner.note_chat()
        proposal = self._dialogue_context().plan(text, reply=reply, mood=mood)
        self.personality.observe(text, intent=proposal['intent'])
        private_context = bool(name and not learned and name in str(reply.get('text', '')))
        previous_private = getattr(self, '_last_reply_private', False)
        memory_answer, memory_meta = (None, '')
        if proposal['intent'] not in ('repair', 'rest', 'refusal'):
            memory_answer, memory_meta = self._memory_answer(text)
        if memory_answer:
            reply = {"text": memory_answer, "action": None}
            meta = memory_meta
            private_context = True
        elif proposal['handled'] and not (reply.get('action') or reply.get('name') or reply.get('like')):
            reply = proposal
            meta = '本地上下文 · ' + proposal['intent']
            if proposal['intent'] != 'share':
                private_context = private_context or previous_private
            # Prepare review candidates without persisting the query.
            hits = self.memory.recall(text, 5, kinds=('preference', 'habit', 'diary'))
            self._capture_retrieval(text, hits)
        elif is_meta_question(text):
            reply = {"text": meta_reply(text, self.last_user_text, self.last_reply_text)
                     or fallback_reply(text, self.last_user_text), "action": None}
            meta = "本地对话 · 更正"
            private_context = private_context or previous_private
        elif is_followup(text):
            reply = {"text": followup_reply(text, self.last_topic, self.last_reply_text), "action": None}
            meta = "本地对话 · 追问"
            private_context = private_context or previous_private
        else:
            reply, meta = self.learning.companion_reply(text, reply, mood)
            if reply.get("text") in DEFAULT_REPLIES:
                public_answer, public_meta = compose_public_answer(text, self.memory.recall(text, 5))
                if public_answer:
                    reply = {"text": public_answer, "action": None}
                    meta = public_meta
                else:
                    reply = self._dialogue_context().plan(text, reply=None, mood=mood)
                    reply['action'] = None
                    meta = '本地上下文 · 澄清'
        if (reply.get("text") in DEFAULT_REPLIES or mood in ("sad", "tired", "anxious", "anger")) \
                and not meta.startswith(("记忆引擎", "本地对话", "本地上下文")):
            reply = dict(reply, text=self.personality.decorate(
                str(reply.get("text", "")), mood, bool(self.memory.recent(1))))
        if reply.get("action") is None and not meta.startswith(("记忆引擎", "云端")):
            store = self._relationship_store()
            reply = dict(reply, text=naturalize(
                str(reply.get("text", "")), user_text=text, mood=mood,
                last_reply=self.last_reply_text,
                topics=store.snapshot().get("topics") or [],
                seed=store.snapshot().get("revision", 0)))
        if any(word in text for word in ("实时动态", "最新动态", "最新地震", "联网动态")):
            snap = self.online_state.snapshot()
            if snap.get("summary"):
                stale = time.time() - snap.get("lastSuccess", 0) > 600
                reply = dict(reply, text=("这是带时间的旧缓存，不是当前结果：" if stale else "刚校验过的公开资料：") + snap["summary"], action=None)
                meta = "USGS 公开监测 · 带来源时间"
            else:
                reply = dict(reply, text="动态来源还没有成功记录。开启「动态公开资料」后，我会校验官方资料；失败时会如实显示，不编造最新结果。", action=None)
            if self.online_state.enabled:
                self._start_online(manual=True)
        elif self.last_proactive and not proposal['handled'] and not private_context \
                and not is_meta_question(text) and not is_followup(text) \
                and text.strip("。.!！ ") in ("好呀", "好啊", "继续", "然后呢", "聊聊", "愿意", "想聊"):
            reply = dict(reply, text="好呀，那我们接着聊。你想先说说今天的心情，还是问我一条基础知识？也可以说「实时动态」，看看最近校验的公开资料。", action=None)
        self.last_proactive = ''
        if mood != "neutral":
            meta += f" · 情绪线索偏{LABELS.get(mood, '情绪')}"
        reaction_key = detect_reaction(text, mood)
        if reaction_key:
            self._trigger_reaction(reaction_key)
        if not is_meta_question(text) and not is_followup(text):
            self.last_topic = extract_topic(text)
        self._record_exchange(text, str(reply.get("text", "")), mood, private_context=private_context)
        if self.conversation is not None and self.conversation.isVisible():
            self.conversation.show_reply(str(reply.get("text", "嗯。")), meta,
                                         private_context=private_context)
            if self.direct_input_source == 'voice_direct' and self.voice_engine:
                self._speak_voice_reply(str(reply.get('text', '嗯。')), self.voice_engine.generation)
        if self.growth_dialog is not None and self.growth_dialog.isVisible():
            self.growth_dialog.update_behavior(self.learner.snapshot())
            self.growth_dialog.update_emotion(self.emotion.snapshot())
        action = reply.get("action")
        if action == "run":
            QTimer.singleShot(400, lambda: self._start_run(allow_during_chat=True))
        elif action == "hop":
            QTimer.singleShot(400, lambda: self._start_hop(allow_during_chat=True))
        elif action == "spin":
            QTimer.singleShot(400, lambda: self._start_spin(1, allow_during_chat=True))


    def _conversation_cloud(self, text: str) -> None:
        if self._intercept_command(text):
            return
        config = load_config()
        dialog = self.conversation
        if dialog is None:
            return
        if (config.get("endpoint"), config.get("model"), config.get("max_tokens")) != (dialog.cloud.get("endpoint"), dialog.cloud.get("model"), dialog.cloud.get("max_tokens")):
            dialog.set_cloud_state(config)
            dialog.show_reply("接口、模型或输出上限已变更；没有发送。请核对新目标后重新确认。", "本地提示", True)
            return
        proposal = self._dialogue_context().plan(text)
        self.personality.observe(text, intent=proposal['intent'])
        if proposal['intent'] in ('repair', 'rest', 'refusal'):
            answer = proposal['text']
            private_context = getattr(self, '_last_reply_private', False)
            dialog.show_reply(answer, '本地上下文 · 未发送模型', private_context=private_context)
            self._record_exchange(text, answer, private_context=private_context)
            return
        if is_meta_question(text):
            answer = meta_reply(text, self.last_user_text, self.last_reply_text)
            private_context = getattr(self, '_last_reply_private', False)
            dialog.show_reply(answer, "本地对话 · 更正 · 未发送云端", private_context=private_context)
            self._record_exchange(text, answer, private_context=private_context)
            return
        memory_answer, memory_meta = self._memory_answer(text)
        if memory_answer:
            dialog.show_reply(memory_answer, memory_meta + " · 未发送云端", private_context=True)
            self._record_exchange(text, memory_answer, private_context=True)
            return
        history = dialog.context_before_current()
        if not config.get("endpoint") or not config.get("model"):
            if self.conversation is not None:
                self.conversation.show_reply("云端接口还没配置。点右上角「接口设置」填写后再发送。", "云端", True)
            return
        if self.cloud_busy:
            if self.conversation is not None:
                self.conversation.show_reply("上一条云端回复还在等，请稍候。", "云端", True)
            return
        self.cloud_config = config
        self.cloud_busy = True
        self.last_interaction_ms = self._now_ms()
        reading = self._chat_reading(text)
        self._refresh_learning_ui()
        if reading.get("mood") in ("sad", "tired", "anxious"):
            self.comfort_until_ms = self._now_ms() + 60000
        memory = reply_to(text, self.learner.data.get("userName", ""))
        if self._chat_may_learn:
            if memory.get('name'):
                self.learner.set_name(memory['name'])
            if memory.get('like'):
                self.learner.add_like(memory['like'])
            self.learner.note_chat()
        if self.growth_dialog is not None and self.growth_dialog.isVisible():
            self.growth_dialog.update_behavior(self.learner.snapshot())
            self.growth_dialog.update_emotion(self.emotion.snapshot())
        authorized = dialog.memory_context_enabled()
        hits = []
        if authorized:
            # This request's own local retrieval, not a stale earlier turn.
            hits = self.memory.recall(text, 5, kinds=('preference', 'habit', 'diary'))
            self._capture_retrieval(text, hits)
        request_id = uuid.uuid4().hex
        context = {'policy': self.personality.personality_policy(proposal['intent']),
                   'memories': hits[:3], 'memory_authorized': authorized,
                   'requestId': request_id,
                   # Authorized history may itself contain private earlier replies.
                   '_memory_context_used': bool(authorized and (hits or history))}
        self._pending_model_text = text[:2000]
        self._pending_model_request = {'id': request_id, 'session': self.conversation_serial,
                                       'text': text[:2000], 'mood': reading.get('mood', 'neutral')}
        self.last_proactive = ''
        threading.Thread(target=self._cloud_worker, args=(config, text, history, self.conversation_serial,
                         self.voice_engine.generation if self.direct_input_source == 'voice_direct' and self.voice_engine else None,
                         context), daemon=True).start()


    def _open_cloud_settings(self) -> None:
        self._stop_motion()
        config = load_config()
        dialog = CloudSettingsDialog(config, self)
        self.chat_open = True
        try:
            accepted = dialog.exec()
        finally:
            self.chat_open = False
        if accepted:
            self.cloud_config = load_config()
            if self.growth_dialog is not None:
                self.growth_dialog.update_cloud(self.cloud_config)
            if self.conversation is not None:
                self.conversation.set_cloud_state(self.cloud_config)
            self._show_overlay("云端接口已保存在本机。", "密钥使用 Windows DPAPI 加密，不会写入聊天记录", "SUCCESS", 4200)
            self._sync_bubble_size()
            self.update()


    def _cloud_worker(self, config, text, history=None, session_serial=None, voice_generation=None, context=None) -> None:
        try:
            if isinstance(context, dict):
                result = ask_cloud(config, text, history=history, companion_policy=context.get('policy'),
                                   memory_context=context.get('memories'),
                                   memory_authorized=context.get('memory_authorized') is True)
            else:
                result = ask_cloud(config, text, history=history)
        except Exception:
            result = {"ok": False, "error": "云端请求失败"}
        if not isinstance(result, dict):
            result = {"ok": False, "error": "云端返回异常"}
        result["requestSession"] = session_serial
        result['voiceGeneration'] = voice_generation
        if isinstance(context, dict):
            result['requestId'] = context.get('requestId')
            result['_memory_context_used'] = context.get('_memory_context_used') is True
        try:
            self.cloud_reply.emit(result)
        except RuntimeError:
            pass


    def _on_cloud_reply(self, result) -> None:
        if not isinstance(result, dict):
            result = {"ok": False, "error": "云端返回异常"}
        if result.get("requestSession", self.conversation_serial) != self.conversation_serial:
            return  # Closed dialogue: never mutate a newer request's state.
        pending = getattr(self, '_pending_model_request', None)
        if result.get('requestId') is not None and (
                not pending or result['requestId'] != pending['id']):
            return
        self.cloud_busy = False
        self._pending_model_request = None
        self._pending_model_text = ''
        active = self.conversation is not None and self.conversation.isVisible()
        if result.get("ok"):
            host = urllib.parse.urlsplit(str(result.get("endpoint", ""))).hostname or "云端"
            text = str(result.get("text", ""))[:1200]
            mood = self.emotion.current().get("label", "neutral")
            if pending:
                self._record_exchange(pending['text'], text, pending.get('mood', mood),
                                      private_context=result.get('_memory_context_used') is True)
            self.last_reply_text = text[:300]
            if active:
                self.conversation.show_reply(text, f"云端 · {host}",
                                             private_context=result.get('_memory_context_used') is True)
                self._speak_voice_reply(text, result.get('voiceGeneration'))
            else:
                self._show_overlay(text, f"云端 · {host} · 仅本次显示，不写入成长记录", "SUCCESS", 12000)
        else:
            self._pending_model_text = ''
            message = str(result.get("error", "云端请求失败"))[:300]
            if active:
                self.conversation.show_reply(message, "云端", True)
            else:
                self._show_overlay(message, "没有自动重试；如仍需要，请重新确认发送", "ERROR", 6000)
        if not active:
            self._sync_bubble_size()
            self.update()

