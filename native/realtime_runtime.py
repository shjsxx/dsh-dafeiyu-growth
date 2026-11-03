"""Explicit-consent runtime for independent realtime voice.

Provider transcripts are unverified data: they never enter the computer parser,
text-chat provider, or automatic fitting. Only owner-reviewed text can locally
adapt emotion. Every session is volatile and bound to endpoint/model/key/settings.
"""
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QPushButton, QScrollArea, QFrame, QHBoxLayout
from realtime_audio import RealtimeAudio
from realtime_transport import RealtimeTransport
from realtime_config import load_config, public_config, consent_identity
from realtime_settings_ui import RealtimeSettingsDialog
from training_journal import REALTIME_REASONS


class AudioUploadConsent(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle('允许本次实时音频上传？')
        self.setModal(False)
        self.resize(470, 500)
        self.setStyleSheet('QDialog{background:#F4F6FA} QLabel,QCheckBox{font-family:"Microsoft YaHei UI";font-size:13px;color:#273E50} QPushButton{padding:8px;color:#294B60}')
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QLabel('本次持续音频将发送到：\n' + str(config.get('endpoint', '')) + '\n模型：' + str(config.get('model', '')) + '\n音色：' + str(config.get('voice', '')) + '\n协议：' + str(config.get('protocol', 'ga')) + '\n最长本次时长：' + str(config.get('session_seconds', 600)) + '秒；每次回复最多 ' + str(config.get('max_tokens', 1024)) + ' tokens。\n\n连接配置确认后才开启麦克风；期间周围声音也可能被上传。音频和本次会话文本会在该服务处理，可能产生费用；该服务的留存、隐私及支持地区政策适用，本机不承诺服务端零留存。\n\n这是独立语音接口，不包含其他DSH会话、文件、屏幕或工具。不会把密钥发往重定向目标；不自动重连或切换服务。修改地址／模型／Key／音色／预算即断开并撤销。\n\n默认不保存原始音频或转写；只写数字用量和固定事件日志。云端转写没有可靠情感标签，需你核对后才用于本地适应；模型输出不能授予电脑权限。\n\n关闭语音／聊天窗口、隐藏桌宠或急停会停止上传。时长上限不是费用硬上限。')
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)
        root.addWidget(scroll)
        self.headphones = QCheckBox('我已使用耳机，了解本通道没有声学回声消除')
        self.headphones.setToolTip('服务端VAD不等于本机回声消除；外放可能导致回灌、误打断和额外费用。')
        root.addWidget(self.headphones)
        self.allow = QPushButton('允许本次音频上传并连接')
        self.allow.setEnabled(False)
        self.headphones.clicked.connect(lambda checked: self.allow.setEnabled(checked))
        self.allow.clicked.connect(self.accept)
        cancel = QPushButton('取消，不连接')
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(cancel)
        row.addWidget(self.allow)
        root.addLayout(row)
        for button in (self.allow, cancel):
            button.setDefault(False)
            button.setAutoDefault(False)


class RealtimeRuntime:
    def _init_realtime_runtime(self):
        self.realtime = None
        self.realtime_audio = None
        self.realtime_config = None
        self.realtime_identity = None
        self.realtime_active = self.realtime_starting = False
        self.realtime_pending = None
        self.realtime_settings = None
        self.realtime_last_user = None
        self.realtime_generation = None
        self.realtime_epoch = 0
        self.realtime_started = 0.0
        self.realtime_audio_items = {}
        self.realtime_stats = {}
        self.realtime_stat_timer = QTimer(self)
        self.realtime_stat_timer.setInterval(1000)
        self.realtime_stat_timer.timeout.connect(self._realtime_tick)
        self.realtime_stat_timer.start()
        self.realtime_last_flush = 0.0

    def _realtime_view(self):
        config = self.realtime_config or {}
        return {'active': self.realtime_active, 'starting': self.realtime_starting,
                'target': str(config.get('endpoint', '')) + (' · ' + str(config.get('model', '')) if config else '请先填写独立语音 API 设置'),
                'review_available': bool(self.realtime_last_user)}

    def _open_realtime_settings(self):
        # Even canceled edits intentionally disconnect: no old consent stays
        # alive behind a settings window while an endpoint/key is being changed.
        self._realtime_stop('settings_changed')
        if self.realtime_settings is not None:
            self.realtime_settings.raise_()
            return
        config = load_config()
        self.realtime_config = public_config(config)
        dialog = RealtimeSettingsDialog(config, self)
        self.realtime_settings = dialog
        dialog.accepted.connect(self._realtime_settings_saved)
        dialog.finished.connect(lambda *_: setattr(self, 'realtime_settings', None))
        dialog.finished.connect(dialog.deleteLater)
        dialog.show()
        self._place_conversation(dialog)
        dialog.raise_()
        dialog.activateWindow()

    def _realtime_settings_saved(self):
        self._realtime_stop('settings_changed')
        self.realtime_config = public_config(load_config())
        self.voice_notice = '实时语音设置已保存；没有自动连接、录音或上传。'
        self._refresh_voice_tools()

    def _request_realtime_start(self):
        if self.realtime_active or self.realtime_starting or self.realtime_pending:
            return
        config = load_config()
        self.realtime_config = public_config(config)
        if config.get('error') or not config.get('key'):
            self.voice_notice = '实时语音 API 尚未就绪。请先配置独立地址、模型和 API Key。'
            self._refresh_voice_tools()
            self._open_realtime_settings()
            return
        if not RealtimeAudio.capabilities()['available']:
            self.voice_notice = '默认音频设备不支持24kHz单声道16位PCM；没有连接或开启麦克风。'
            self._refresh_voice_tools()
            return
        if self.learning.audit_blocked or self.learning.journal.snapshot()['status'] in ('error', 'protected'):
            self.voice_notice = '审计不可写，未连接或上传音频。'
            self._refresh_voice_tools()
            return
        self._ensure_conversation()
        self._voice_stop()  # Exclusive ownership: never local+remote capture.
        identity = consent_identity(config)
        dialog = AudioUploadConsent(public_config(config), self)
        self.realtime_pending = dialog
        dialog.accepted.connect(lambda: self._begin_realtime_if_current(identity))
        dialog.finished.connect(lambda *_: setattr(self, 'realtime_pending', None))
        dialog.finished.connect(dialog.deleteLater)
        dialog.show()
        self._place_conversation(dialog)
        dialog.raise_()
        dialog.activateWindow()

    def _begin_realtime_if_current(self, expected):
        config = load_config()
        if config.get('error') or not config.get('key') or consent_identity(config) != expected:
            self.voice_notice = '实时语音目标或密钥已变化，未连接；请重新核对并授权。'
            self._refresh_voice_tools()
            return
        journal = self.learning.journal
        if self.learning.audit_blocked or not journal.record('voice_io', 'general', 'realtime_voice', 'observed', labels={'voice_event':'rt_connect'}):
            self.voice_notice = '实时语音审计无法写入，未连接。'
            return
        if not self._register_safety_hotkey():
            self.voice_notice = '无法注册全局急停，未连接实时语音。请解除 Ctrl+Alt+F12 冲突后重试。'
            self._refresh_voice_tools()
            return
        self.realtime_identity = expected
        self.realtime_config = public_config(config)
        self.realtime_last_user = None
        self.realtime_stats = {}
        self.realtime_audio_items = {}
        self.realtime_started = time.monotonic()
        self.realtime_last_flush = self.realtime_started
        self.realtime_starting = True
        self.realtime_epoch += 1
        epoch = self.realtime_epoch
        self.realtime = RealtimeTransport(lambda event: self.voice_event.emit(dict(event, engine_kind='openai_realtime', runtime_epoch=epoch)))
        if not self.realtime.start(config):
            self._realtime_stop('start_failed')
            self.voice_notice = '实时语音配置不符或依赖不可用，没有开启麦克风。'
        else:
            self.realtime_generation = self.realtime.generation
            self.voice_notice = '正在连接目标并等待配置确认，麦克风尚未开启。'
        self._refresh_voice_tools()

    def _realtime_upload(self, pcm):
        return bool(self.realtime and self.realtime_active and self.realtime.send_audio(pcm))

    def _realtime_played(self, item_id, ms):
        if self.realtime:
            self.realtime.mark_played(item_id, ms)
        previous = self.realtime_audio_items.get(item_id, 0)
        if type(ms) is int and ms > previous:
            self.realtime_audio_items[item_id] = ms
            self.realtime_stats['played_ms'] = self.realtime_stats.get('played_ms', 0) + ms - previous
        if len(self.realtime_audio_items) > 128:
            self._realtime_stop('metadata_limit')

    def _realtime_audio_error(self, code):
        self._realtime_stop('audio_failed')
        self.voice_notice = '音频设备失败或缓冲积压，已停止音频上传和播报；不会自动重连。'
        self._refresh_voice_tools()

    def _flush_realtime_stats(self):
        stats = self.realtime_stats
        if not stats:
            return True
        self.realtime_stats = {}
        return bool(self.learning.journal.record('voice_io', 'general', 'realtime_voice', 'observed', metrics=stats, labels={'voice_event':'rt_usage'}))

    def _realtime_stop(self, reason='owner_stop'):
        self.realtime_epoch += 1  # Invalidate even already-queued events from an old object.
        active = self.realtime_active or self.realtime_starting
        self.realtime_active = self.realtime_starting = False
        pending, self.realtime_pending = self.realtime_pending, None
        if pending:
            pending.reject()
        if self.realtime_audio:
            try:
                self.realtime_audio._report_played()
            except Exception:
                pass
            self.realtime_audio.stop()
            self.realtime_audio.deleteLater()
            self.realtime_audio = None
        transport, self.realtime = self.realtime, None
        if transport:
            transport.stop()
            transport.close()
        self.realtime_generation = self.realtime_identity = None
        self.realtime_last_user = None
        self.realtime_audio_items = {}
        self._flush_realtime_stats()
        if active:
            duration = int(max(0, time.monotonic() - self.realtime_started) * 1000)
            safe_reason = reason if isinstance(reason, str) and reason in REALTIME_REASONS else 'stopped'
            self.learning.journal.record('voice_io', 'general', 'realtime_voice', 'observed', {'duration_ms': duration}, {'voice_event':'rt_stop', 'rt_reason':safe_reason})
            self.voice_learning = False
            self._cancel_operation()
            self.computer.set_enabled(False)
            getattr(self, '_release_safety_hotkey', lambda: None)()
        if active and self.conversation:
            self.conversation.add_system('实时语音已断开；没有后台重连或继续上传音频。')

    def _realtime_interrupt(self, barge_token=None):
        if not self.realtime:
            return False
        # Report actual processed audio before cancel/truncate. Received bytes
        # and queued samples are NOT evidence that the owner heard them.
        if self.realtime_audio:
            self.realtime_audio.clear_playback()
        result = self.realtime.interrupt(barge_token=barge_token) if barge_token is not None else self.realtime.interrupt()
        if result:
            self.learning.journal.record('voice_io', 'general', 'realtime_voice', 'observed', labels={'voice_event':'rt_interrupt'})
        return result

    def _realtime_tick(self):
        if not self.realtime_active and not self.realtime_starting:
            return
        if time.monotonic() - self.realtime_started >= int((self.realtime_config or {}).get('session_seconds', 600)):
            self._realtime_stop('session_limit')
            self.voice_notice = '已达到本次实时语音时长上限，停止上传与播报；需要重新授权。'
            self._refresh_voice_tools()
            return
        config = load_config()
        if config.get('error') or consent_identity(config) != self.realtime_identity:
            self._realtime_stop('settings_changed')
            self.voice_notice = '语音设置、密钥或授权目标变化，已断开；需要重新允许音频上传。'
            self._refresh_voice_tools()
            return
        if time.monotonic() - self.realtime_last_flush >= 10:
            self.realtime_last_flush = time.monotonic()
            if not self._flush_realtime_stats():
                self._realtime_stop('audit_failed')
                self.voice_notice = '实时语音用量审计失败，已停止连接。'
            self._refresh_voice_tools()

    def _on_realtime_event(self, event):
        if (not isinstance(event, dict) or not self.realtime or event.get('generation') != self.realtime_generation
                or event.get('runtime_epoch') != self.realtime_epoch):
            return
        kind = event.get('kind')
        if kind == 'session_ready':
            if not self.realtime_starting or self.realtime_active or self.realtime_audio is not None:
                return
            if not self.learning.journal.record('voice_io', 'general', 'realtime_voice', 'observed', labels={'voice_event':'rt_start'}):
                self._realtime_stop('audit_failed')
                return
            self.realtime_audio = RealtimeAudio(self._realtime_upload, self._realtime_played, self._realtime_audio_error, self)
            self.realtime_active = True
            self.realtime_starting = False
            if not self.realtime_audio.start():
                self._realtime_stop('audio_failed')
                return
            self._ensure_conversation()
            self.conversation.show()
            self.conversation.add_system('本次实时语音已连接：正在持续上传麦克风音频；云端文本仅临时显示。')
            self.voice_notice = 'OpenAI Realtime 协议已就绪，正在持续双向传输音频；请使用耳机。'
        elif kind == 'audio_sent':
            size = event.get('bytes')
            if type(size) is int and 0 < size <= 4800:
                self.realtime_stats['audio_input_bytes'] = self.realtime_stats.get('audio_input_bytes', 0) + size
            return  # Audio frames never redraw the entire growth UI.
        elif kind == 'audio_delta':
            pcm, item = event.get('pcm'), event.get('item_id')
            if self.realtime_audio and self.realtime.is_audio_current(item) and self.realtime_audio.enqueue(pcm, item):
                self.realtime_stats['audio_output_bytes'] = self.realtime_stats.get('audio_output_bytes', 0) + len(pcm)
            return
        elif kind in ('speech_started', 'audio_clear'):
            if kind == 'speech_started':
                token = event.get('barge_token')
                if not isinstance(token, str) or not token:
                    self._realtime_stop('protocol_error')
                    return
                self._realtime_interrupt(token)
                self.voice_notice = '检测到你开始说话，已清空旧播报并请求取消／截断；继续处理本次音频。'
            elif self.realtime_audio:
                self.realtime_audio.clear_playback()
        elif kind == 'transcript':
            if event.get('final') is not True or not self.realtime_active:
                return
            text = event.get('text')
            if not isinstance(text, str) or not text:
                return
            text = text[:4000]
            role = event.get('role')
            if role not in ('user', 'assistant'):
                return
            if self.conversation:
                self.conversation.add_message(role, text, '云端实时转写 · 未核对' if role == 'user' else '云端实时语音', context=False)
            if role == 'user':
                if not self.learning.journal.record('voice_io', 'general', 'realtime_voice', 'observed', {'characters': len(text)}, {'voice_event':'rt_recognized'}):
                    self._realtime_stop('audit_failed')
                    return
                self.realtime_last_user = (text, self.realtime_generation)
                self.emotion_comparison = self.emotion.compare_public(text)
                self.realtime_stats['characters'] = self.realtime_stats.get('characters', 0) + len(text)
                # No fabricated confidence and no automatic PC command or fit.
                self.voice_notice = '收到云端转写（未核对）；仅本机线索对照，不自动训练或提出电脑操作。'
        elif kind == 'response_done':
            usage = event.get('usage')
            if isinstance(usage, dict):
                for key in ('input_tokens', 'output_tokens', 'input_audio_tokens', 'output_audio_tokens', 'cached_tokens'):
                    value = usage.get(key)
                    if type(value) is int and 0 <= value <= 10**9:
                        self.realtime_stats[key] = self.realtime_stats.get(key, 0) + value
        elif kind == 'security_notice':
            self.voice_notice = '服务端工具请求已拒绝；语音模型没有电脑操作权限。'
        elif kind in ('error', 'disconnected', 'limit'):
            raw_code = event.get('code')
            code = raw_code if isinstance(raw_code, str) and raw_code in REALTIME_REASONS else kind
            self.learning.journal.record('voice_io', 'general', 'realtime_voice', 'observed', labels={'voice_event':'rt_error' if kind == 'error' else 'rt_limit' if kind == 'limit' else 'rt_disconnect', 'rt_reason':code})
            self._realtime_stop(code)
            self._cancel_operation()
            self.computer.set_enabled(False)
            self.voice_notice = '实时语音已停止：' + REALTIME_REASONS.get(code, '连接已结束') + '。没有自动重试；请核对接口及账户后重新授权。'
        else:
            return
        self._refresh_voice_tools()

    def _review_realtime_transcript(self):
        candidate = self.realtime_last_user
        if not candidate or candidate[1] != self.realtime_generation:
            return
        text, generation = candidate
        dialog = QDialog(self)
        dialog.setWindowTitle('核对语音转写后本地适应')
        dialog.resize(440, 380)
        box = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        review = QLabel(text)
        review.setWordWrap(True)
        review.setTextFormat(Qt.TextFormat.PlainText)
        scroll.setWidget(review)
        box.addWidget(scroll)
        hint = QLabel('仅当这确实是你刚说的话才采纳。会更新本地互动及明确自述的情感权重；不上传、不给电脑授权，也不保存转写原句。')
        hint.setTextFormat(Qt.TextFormat.PlainText)
        hint.setWordWrap(True)
        box.addWidget(hint)
        button = QPushButton('这确实是我的话，采纳为本地适应')
        button.setAutoDefault(False)
        button.clicked.connect(dialog.accept)
        box.addWidget(button)
        if dialog.exec() == QDialog.DialogCode.Accepted and self.realtime_generation == generation and self.realtime_last_user == candidate:
            self.realtime_last_user = None
            reading = self.learning.observe_emotion(text, 'local_voice')
            self.learning.adapt('chat', reading.get('mood', 'neutral'), 'local_voice')
            self.learner.note_chat()
            self.voice_notice = '已按你核对的文字完成本地适应；不是语音模型训练。'
            self._refresh_voice_tools()
