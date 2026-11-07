"""Main-Qt-thread voice and computer-action boundary for the native helper.

Only two direct chat handlers call _intercept_command. Cloud/provider replies,
public sources, pet chatter and DSH events have no route to an executor.
"""
import sys
import re
from PySide6.QtCore import QTimer
from voice_engine import VoiceEngine
from voice_ui import VoiceSessionDialog
from computer_control import ComputerController
from realtime_runtime import RealtimeRuntime

HOTKEY_ID = 0x594C
GROWTH_HOTKEY_ID = 0x594D
CHAT_HOTKEY_ID = 0x594E


class VoiceRuntime(RealtimeRuntime):
    def _init_voice_runtime(self):
        self.voice_engine = None
        self.voice_tools = None
        self.voice_active = False
        self.voice_starting = False
        self.voice_learning = False
        self.voice_notice = ''
        self.voice_caps = {}
        self.voice_confidence = 0.0
        self.direct_input_source = 'typed_direct'
        self.computer = ComputerController()
        self.operation_row = None
        self.safety_hotkey = False
        self.growth_hotkey = False
        self.chat_hotkey = False
        self._init_realtime_runtime()
        self._register_growth_hotkey()
        self._register_chat_hotkey()
        self.voice_event.connect(self._on_voice_event)

    def _register_safety_hotkey(self):
        if self.safety_hotkey:
            return True
        if sys.platform != 'win32':
            return False
        try:
            import ctypes
            self.safety_hotkey = bool(ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, 0x0001 | 0x0002 | 0x4000, 0x7B))
        except Exception:
            self.safety_hotkey = False
        return self.safety_hotkey

    def _register_growth_hotkey(self):
        if self.growth_hotkey:
            return True
        if sys.platform != 'win32':
            return False
        try:
            import ctypes
            # Ctrl+Alt+G: a keyboard route to the growth card when the
            # transparent renderer swallows synthetic/context-menu clicks.
            self.growth_hotkey = bool(ctypes.windll.user32.RegisterHotKey(
                None, GROWTH_HOTKEY_ID, 0x0001 | 0x0002 | 0x4000, 0x47))
        except Exception:
            self.growth_hotkey = False
        return self.growth_hotkey

    def _register_chat_hotkey(self):
        if self.chat_hotkey:
            return True
        if sys.platform != 'win32':
            return False
        try:
            import ctypes
            self.chat_hotkey = bool(ctypes.windll.user32.RegisterHotKey(
                None, CHAT_HOTKEY_ID, 0x0001 | 0x0002 | 0x4000, 0x43))
        except Exception:
            self.chat_hotkey = False
        return self.chat_hotkey

    def _release_chat_hotkey(self):
        if self.chat_hotkey and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(None, CHAT_HOTKEY_ID)
        self.chat_hotkey = False

    def _release_growth_hotkey(self):
        if self.growth_hotkey and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(None, GROWTH_HOTKEY_ID)
        self.growth_hotkey = False

    def _release_safety_hotkey(self):
        if self.safety_hotkey and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
        self.safety_hotkey = False

    def _open_voice_tools(self):
        self._ensure_conversation()
        if self.voice_tools is None:
            dialog = VoiceSessionDialog(self)
            dialog.start_requested.connect(self._voice_start)
            dialog.realtime_settings_requested.connect(self._open_realtime_settings)
            dialog.realtime_start_requested.connect(self._request_realtime_start)
            dialog.realtime_review_requested.connect(self._review_realtime_transcript)
            dialog.stop_requested.connect(self._voice_stop)
            dialog.interrupt_requested.connect(self._voice_interrupt)
            dialog.emergency_requested.connect(self._emergency_stop)
            dialog.learning_changed.connect(self._voice_learning_changed)
            dialog.control_changed.connect(self._computer_permission_changed)
            # This signal is emitted ONLY by the native confirmation click slot.
            # It is never connected to ASR, model output or the stdin protocol.
            dialog.confirm_requested.connect(self._confirm_operation)
            dialog.cancel_requested.connect(self._cancel_operation)
            dialog.closed.connect(self._voice_tools_closed)
            self.voice_tools = dialog
        if self.voice_engine is None:
            self.voice_engine = VoiceEngine(self.voice_event.emit)
            self.voice_caps = self.voice_engine.capabilities()
        self._refresh_voice_tools()
        self.voice_tools.show()
        self._place_conversation(self.voice_tools)
        self.voice_tools.raise_()
        self.voice_tools.activateWindow()

    def _refresh_voice_tools(self):
        if self.voice_tools is not None:
            self.voice_tools.update_state({'active': self.voice_active, 'starting': self.voice_starting,
                'learning': self.voice_learning, 'control': self.computer.snapshot()['enabled'],
                'notice': self.voice_notice, 'capabilities': self.voice_caps, 'realtime': self._realtime_view()})
        self._refresh_learning_ui()

    def _record_voice(self, event, confidence=0.0, characters=0):
        return self.learning.journal.record('voice_io', 'general', 'local_voice', 'observed',
            {'confidence': confidence, 'characters': characters}, {'voice_event': event})

    def _voice_start(self):
        if self.voice_engine is None or self.voice_active or self.voice_starting:
            return
        self._realtime_stop('local_mode')
        if self.learning.audit_blocked or self.learning.journal.snapshot()['status'] in ('error', 'protected'):
            self.voice_notice = '审计不可写，未开启语音。请检查本地存储。'
        else:
            self._register_safety_hotkey()
            self.voice_starting = self.voice_engine.start()
            self.voice_notice = '正在开启本次麦克风…' if self.voice_starting else '本机中文语音识别不可用；没有录音。'
        self._refresh_voice_tools()

    def _voice_stop(self):
        self._realtime_stop()
        active = self.voice_active or self.voice_starting
        self.voice_active = self.voice_starting = False
        if self.voice_engine:
            self.voice_engine.stop()
        self.voice_learning = False
        self._cancel_operation()
        self.computer.set_enabled(False)
        self._release_safety_hotkey()
        if active:
            self._record_voice('session_stop')
        self.voice_notice = '麦克风与播报已关闭；语音适应与操作授权已撤销。'
        self._refresh_voice_tools()

    def _voice_tools_closed(self):
        self.voice_tools = None
        self._voice_stop()

    def _voice_learning_changed(self, enabled):
        self.voice_learning = bool(enabled) and self.voice_active
        self.voice_notice = '高置信语音可用于本地适应；声学模型未训练。' if self.voice_learning else '语音适应关闭；转写仍可聊天。需先开启麦克风再允许适应。'
        self._refresh_voice_tools()

    def _voice_interrupt(self):
        if self.realtime_active or self.realtime_starting:
            self._realtime_interrupt()
            self.voice_notice = '已清空云端旧播报，并按实际播放进度请求截断；继续监听本次音频。'
            self._refresh_voice_tools()
            return
        if self.voice_engine:
            self.voice_engine.interrupt()
        self._record_voice('voice_interrupt')
        self.voice_notice = '播报已打断；约1秒回声抑制后可继续说。'
        self._refresh_voice_tools()

    def _computer_permission_changed(self, enabled):
        if enabled and (not self._register_safety_hotkey() or self.learning.audit_blocked or self.learning.journal.snapshot()['status'] in ('error', 'protected')):
            self.voice_notice = '无法注册全局急停快捷键或审计不可写：未授予电脑操作。'
            enabled = False
        self._cancel_operation()
        self.computer.set_enabled(bool(enabled))
        if not enabled and not self.voice_active and not self.voice_starting and not self.realtime_active and not self.realtime_starting:
            self._release_safety_hotkey()
        self._refresh_voice_tools()

    def _emergency_stop(self, controller_stopped=False):
        self._cancel_operation()
        if not controller_stopped:
            self.computer.emergency_stop()
        self._voice_stop()
        self.learning.journal.record('computer_control', 'action', 'owner_control', 'observed', labels={'decision':'emergency_stop'})
        self.voice_notice = '已急停：关闭麦克风／播报，撤销操作授权和所有待确认项。已打开的应用不会被强制关闭。'
        self._refresh_voice_tools()

    def _cancel_operation(self, *_):
        row = self.operation_row
        self.operation_row = None
        self.computer.cancel()
        if row:
            self.learning.journal.finish(row['id'], 'cancelled', labels={'computer_action':row['action'], 'decision':'cancelled'})
        if self.voice_tools is not None:
            self.voice_tools.set_proposal()

    def _expire_operation(self, token):
        row = self.operation_row
        if row and row['token'] == token:
            self.computer.snapshot()  # Lazy monotonic expiration in controller.
            self.operation_row = None
            self.computer.cancel()
            self.learning.journal.finish(row['id'], 'cancelled', labels={'computer_action':row['action'], 'decision':'expired'})
            if self.voice_tools:
                self.voice_tools.set_proposal()
            self.voice_notice = '操作确认已过期，没有执行。'
            self._refresh_voice_tools()

    def _intercept_command(self, text):
        # The strict operation parser is NOT a generic chat validator. Ordinary
        # questions, quotations, long chat and self-reports must remain chat.
        if not isinstance(text, str) or not re.match(r'^(?:小云岚|云岚)[ ,，:：]*(?:请[ ]*)?(?:打开|调高|调低|切换|紧急停止|执行|运行|安装|删除|点击|输入|支付)', text.strip()):
            return False
        source = self.direct_input_source
        confidence = self.voice_confidence if source == 'voice_direct' else 1.0
        proposal = self.computer.propose(text, source, confidence)
        if proposal['reason'] == 'emergency_stop':
            self._emergency_stop(controller_stopped=True)
            if self.conversation:
                self.conversation.show_reply('已紧急停止本次语音和电脑操作。', '本地安全控制', True)
            return True
        if proposal['status'] == 'ignored':
            return False
        if proposal['status'] == 'pending':
            old = self.operation_row
            if old:
                self.learning.journal.finish(old['id'], 'cancelled', labels={'computer_action':old['action'], 'decision':'cancelled'})
            event_id = None if self.learning.audit_blocked else self.learning.journal.begin('computer_control', 'action', source, queued=True)
            if event_id is None:
                self.computer.emergency_stop()
                self.operation_row = None
                self.voice_notice = '安全审计无法写入，已拦截操作并撤销授权。'
            else:
                self.operation_row = {'id':event_id, 'token':proposal['token'], 'action':proposal['action'], 'source':source}
                # No permission is granted by opening this passive confirmation.
                self._open_voice_tools()
                self.voice_tools.set_proposal(proposal)
                QTimer.singleShot(30010, lambda token=proposal['token']: self._expire_operation(token))
                self.voice_notice = '已收到直接命令。必须在本窗口点「确认这一次操作」，否则不执行。'
        else:
            self.learning.journal.record('computer_control', 'action', source, 'skipped', labels={'decision':'blocked'})
            reasons = {'permission_disabled':'本次操作权限尚未开启', 'session_closed':'操作会话已关闭', 'unsupported_command':'不在固定命令白名单', 'unsafe_text':'含疑问、引号、控制字符或额外指令', 'text_too_long':'命令过长', 'low_voice_confidence':'语音引擎分数低于候选阈值', 'invalid_confidence':'语音分数无效', 'untrusted_source':'不是直接输入来源'}
            self.voice_notice = '没有执行：' + reasons.get(proposal.get('reason'), '安全校验未通过') + '。可在「语音／权限」核对。'
        if self.conversation:
            self.conversation.show_reply(self.voice_notice, '电脑操作安全检查 · 非模型训练', True)
        self._refresh_voice_tools()
        return True

    def _confirm_operation(self, token):
        row = self.operation_row
        if not row or row['token'] != token:
            return
        journal = self.learning.journal
        if self.learning.audit_blocked or not journal.mark_running(row['id']):
            self._emergency_stop()
            return
        self.operation_row = None  # No second/reentrant click can dispatch.
        result = self.computer.confirm(token, origin='ui_click')
        executed = result['status'] == 'executed'
        decision = 'executed' if executed else 'blocked'
        saved = journal.finish(row['id'], 'success' if executed else 'failed', labels={'computer_action':row['action'], 'decision':decision})
        if not saved:
            self.computer.emergency_stop()
            self.voice_notice = '操作结果的审计未完成，已撤销权限；提交状态未知，不计训练成功。'
        else:
            self.voice_notice = '已发出这一次固定操作（未读取屏幕验证应用内容）。' if executed else '操作未执行或启动失败。'
        if self.voice_tools:
            self.voice_tools.set_proposal()
        if self.conversation:
            self.conversation.add_system(self.voice_notice)
        self._refresh_voice_tools()

    def _chat_reading(self, text):
        voice = self.direct_input_source == 'voice_direct'
        learn = not voice or (self.voice_learning and self.voice_confidence >= .85)
        self._chat_may_learn = learn
        reading = self.learning.observe_emotion(text, 'local_voice' if voice else 'local_chat') if learn else self.emotion.classify(text)
        self.emotion_comparison = self.emotion.compare_public(text)
        if learn:
            self.learning.adapt('chat', reading.get('mood', 'neutral'), 'local_voice' if voice else 'local_chat')
        return dict(reading, mood=reading.get('mood', reading.get('label', 'neutral')))

    def _speak_voice_reply(self, text, generation=None):
        if self.voice_engine and self.voice_active and (generation is not None and generation == self.voice_engine.generation):
            self.voice_engine.speak(text)

    def _on_voice_event(self, event):
        if isinstance(event, dict) and event.get('engine_kind') == 'openai_realtime':
            self._on_realtime_event(event)
            return
        if not isinstance(event, dict) or not self.voice_engine or event.get('generation') != self.voice_engine.generation:
            return
        kind = event.get('kind')
        if kind == 'listening':
            self.voice_active = event.get('listening') is True
            self.voice_starting = False
            if self.voice_active:
                if not self._record_voice('session_start'):
                    self._voice_stop()
                    return
                self._ensure_conversation()
                self.conversation.show()
                self._place_conversation(self.conversation)
            self.voice_notice = '连续监听已开启；原始音频不落盘。' if self.voice_active else '监听已停止。'
        elif kind in ('partial', 'final') and self.voice_active:
            text = str(event.get('text', ''))[:800]
            self.voice_notice = ('正在识别：' if kind == 'partial' else '已识别：') + text
            if kind == 'final' and text:
                confidence = event.get('confidence', 0.0)
                if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
                    confidence = 0.0
                self.voice_confidence = confidence
                # Stop is deny-only and remains available even with a draft,
                # pending cloud request, disabled operations or low confidence.
                if re.fullmatch(r'(?:小云岚|云岚)[ ,，:：]*请?[ ]*紧急停止[ 。.!！]*', text):
                    self._emergency_stop()
                    return
                if not self._record_voice('recognized', confidence, len(text)):
                    self._voice_stop()
                    return
                dialog = self.conversation
                if not dialog:
                    return
                # Refuse overwriting a typed draft or a pending/cloud submission.
                # No buffered transcript is replayed later as a fresh command.
                if dialog.busy or dialog.pending_text or dialog.input.text():
                    self._record_voice('voice_reject', confidence, len(text))
                    self.voice_notice = '当前有草稿／待确认／待回复，未发送本段语音；没有排队重放。'
                else:
                    self.direct_input_source = 'voice_direct'
                    try:
                        # Voice may only enter cloud with separately explicit,
                        # target-bound per-session cloud authorization.
                        if self._intercept_command(text):
                            pass
                        elif dialog.is_cloud_mode() and not dialog.session_cloud_enabled():
                            self.voice_notice = '云端尚未允许本次连续云聊，本段未上传。'
                            self._record_voice('voice_reject', confidence, len(text))
                        else:
                            dialog.input.setText(text)
                            dialog.send()
                    finally:
                        self.direct_input_source = 'typed_direct'
        elif kind == 'rejected' and self.voice_active:
            self._record_voice('voice_reject')
            self.voice_notice = '片段未通过识别／回声检查，未聊天、未学习、未提出操作。'
        elif kind == 'speech_end':
            if event.get('reason') == 'completed':
                self._record_voice('spoken')
            self.voice_notice = '播报已结束，继续监听。'
        elif kind == 'speech_start':
            self.voice_notice = '正在播报；本机抑制自声回灌，可点击「打断播报」。'
        elif kind == 'error':
            self._record_voice('voice_error')
            self._voice_stop()
            self.voice_notice = '本机语音引擎发生错误，麦克风／播报和操作权限已关闭。'
        self._refresh_voice_tools()

    def _shutdown_voice(self):
        self._realtime_stop('shutdown')
        self.realtime_stat_timer.stop()
        self._cancel_operation()
        self.computer.close()
        if self.voice_engine:
            self.voice_engine.close()
        self._release_growth_hotkey()
        self._release_chat_hotkey()
        if self.safety_hotkey and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
        self.safety_hotkey = False
