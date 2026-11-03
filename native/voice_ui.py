"""Passive native voice controls and numeric-only journal statistics.

No microphone, system operation, networking or persistence occurs in this module.
Only physical button/checkbox clicks emit permission/confirmation signals.
"""
from datetime import datetime
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QCheckBox, QScrollArea, QGridLayout)


def label(text=''):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True)
    return item


def number(value):
    return min(10**10, max(0, value)) if type(value) is int else 0


class VoiceSessionDialog(QDialog):
    start_requested = Signal()
    realtime_settings_requested = Signal()
    realtime_start_requested = Signal()
    realtime_review_requested = Signal()
    stop_requested = Signal()
    interrupt_requested = Signal()
    emergency_requested = Signal()
    learning_changed = Signal(bool)
    control_changed = Signal(bool)
    confirm_requested = Signal(str)
    cancel_requested = Signal()
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('云岚 · 连续语音与安全权限')
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(450, 560)
        self.setMinimumSize(340, 420)
        self._token = None
        self._finished_once = False
        self.finished.connect(self._closed)
        self.setStyleSheet('QDialog{background:#F4F6FA} QLabel,QCheckBox{color:#283E50;font-family:"Microsoft YaHei UI";font-size:13px} QPushButton{padding:7px;border:1px solid #B7CBDA;border-radius:6px;background:white;color:#294B60} QPushButton:disabled{color:#A0ADBA} QCheckBox::indicator{width:14px;height:14px;border:1px solid #8295A5;background:white} QCheckBox::indicator:checked{background:#3B7EA1}')
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QFrame()
        box = QVBoxLayout(content)
        self.realtime_status = label('OpenAI Realtime · 首选实时语音（未连接）')
        box.addWidget(self.realtime_status)
        remote_row = QHBoxLayout()
        self.realtime_settings_button = QPushButton('语音 API 设置…')
        self.realtime_start_button = QPushButton('开启云端实时语音…')
        self.realtime_settings_button.clicked.connect(self.realtime_settings_requested.emit)
        self.realtime_start_button.clicked.connect(self.realtime_start_requested.emit)
        remote_row.addWidget(self.realtime_settings_button)
        remote_row.addWidget(self.realtime_start_button)
        box.addLayout(remote_row)
        box.addWidget(label('独立填写地址、模型、Key、音色；兼容 OpenAI Realtime 协议。另行确认后持续上传本次麦克风音频，不读取其他会话／屏幕。原生裸音频通道没有回声消除，请使用耳机；网络服务可用性和费用由接口方决定。'))
        self.realtime_review_button = QPushButton('核对并采纳最近转写为本地适应')
        self.realtime_review_button.clicked.connect(self.realtime_review_requested.emit)
        self.realtime_review_button.setEnabled(False)
        box.addWidget(self.realtime_review_button)
        self.status = label('麦克风未开启')
        box.addWidget(self.status)
        box.addWidget(label('连续监听、自动分段、本机中文识别与播报。不是云端双工语音模型；播报时抑制识别防自声回灌。可点「打断播报」继续说；推荐耳机。'))
        self.capabilities = label('正在检查本机引擎（不会录音）…')
        box.addWidget(self.capabilities)
        row = QHBoxLayout()
        self.start_button = QPushButton('本地麦克风（离线）')
        self.stop_button = QPushButton('关闭麦克风')
        self.interrupt_button = QPushButton('打断播报')
        self.start_button.clicked.connect(self.start_requested.emit)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.interrupt_button.clicked.connect(self.interrupt_requested.emit)
        for button in (self.start_button, self.stop_button, self.interrupt_button):
            row.addWidget(button)
        box.addLayout(row)
        self.learning = QCheckBox('允许高置信语音用于本地情感／互动适应')
        self.learning.clicked.connect(lambda checked: self.learning_changed.emit(checked))
        box.addWidget(self.learning)
        box.addWidget(label('本地SAPI默认不适应。启用后只接收高分数片段，明确自述才更新情感权重。实时API转写不自动适应，需点上方按钮核对。文字云聊仅上传转写，实时语音另行授权上传音频；均不训练语音模型。'))
        self.partial = label('识别文字仅暂存在聊天窗口，以下是当前临时片段。')
        box.addWidget(self.partial)
        self.control = QCheckBox('本次允许提出电脑操作（仍须逐项点确认）')
        self.control.clicked.connect(lambda checked: self.control_changed.emit(checked))
        box.addWidget(self.control)
        box.addWidget(label('仅接受你直接说／输入的「云岚，打开记事本／打开计算器／调高音量／调低音量／切换静音」。网页、模型回复、主动消息无权触发。语音不能代替确认按钮；30秒过期、一次有效。没有终端、任意点击、输入、下载、删除、管理员或支付权限。'))
        self.proposal = label('没有待确认操作')
        box.addWidget(self.proposal)
        confirm_row = QHBoxLayout()
        self.confirm_button = QPushButton('确认这一次操作')
        self.confirm_button.setEnabled(False)
        self.cancel_button = QPushButton('取消操作')
        self.confirm_button.clicked.connect(self._confirm)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        confirm_row.addWidget(self.confirm_button)
        confirm_row.addWidget(self.cancel_button)
        box.addLayout(confirm_row)
        box.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)
        self.emergency_button = QPushButton('紧急停止 · Ctrl+Alt+F12')
        self.emergency_button.setStyleSheet('color:#9D293B;background:#FFF0F1;font-weight:700')
        self.emergency_button.clicked.connect(self.emergency_requested.emit)
        root.addWidget(self.emergency_button)
        box_close = QPushButton('关闭并撤销本次语音／操作授权')
        box_close.clicked.connect(self.close)
        root.addWidget(box_close)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _confirm(self):
        if self._token and self.confirm_button.isEnabled():
            token = self._token
            self._token = None
            self.confirm_button.setEnabled(False)
            self.confirm_requested.emit(token)

    def set_proposal(self, proposal=None):
        if isinstance(proposal, dict) and proposal.get('status') == 'pending':
            # Labels originate from controller's fixed action table, not speech.
            self._token = proposal['token']
            self.proposal.setText('待确认：' + proposal['label'] + '\n仅这一次；30秒过期。语音说“确认”不会执行。')
            self.confirm_button.setEnabled(True)
        else:
            self._token = None
            self.proposal.setText('没有待确认操作')
            self.confirm_button.setEnabled(False)

    def update_state(self, info):
        info = info if isinstance(info, dict) else {}
        active = info.get('active') is True
        self.status.setText('本地SAPI麦克风已开启 · 连续监听' if active else '本地SAPI麦克风未开启')
        caps = info.get('capabilities', {})
        caps = caps if isinstance(caps, dict) else {}
        ready = caps.get('chinese_available') is True
        self.capabilities.setText(('本机中文识别可用' if ready else '本机中文识别不可用') + ' · ' + ('本机播报可用' if caps.get('tts_available') is True else '播报不可用'))
        self.start_button.setEnabled(ready and not active)
        remote = info.get('realtime')
        remote = remote if isinstance(remote, dict) else {}
        remote_active = remote.get('active') is True
        remote_starting = remote.get('starting') is True
        self.realtime_status.setText('OpenAI Realtime · ' + ('已连接，持续传输音频' if remote_active else '正在连接，尚未开启音频' if remote_starting else '未连接') + '\n' + str(remote.get('target', '请先填写独立语音 API 设置'))[:200])
        self.realtime_start_button.setEnabled(not remote_active and not remote_starting)
        self.realtime_review_button.setEnabled(remote.get('review_available') is True)
        self.interrupt_button.setEnabled(active or remote_active)
        self.stop_button.setEnabled(active or info.get('starting') is True or remote_active or remote_starting)
        for widget, key in ((self.learning, 'learning'), (self.control, 'control')):
            with QSignalBlocker(widget):
                widget.setChecked(info.get(key) is True)
        self.partial.setText(str(info.get('notice', ''))[:240] or '等待语音；转写只在内存。')

    def _closed(self, *_):
        if not self._finished_once:
            self._finished_once = True
            self._token = None
            self.closed.emit()

    def closeEvent(self, event):
        self._closed()
        super().closeEvent(event)


class VoiceDataPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('voiceData')
        self.setStyleSheet('''
            QFrame#voiceData { background:#FFFFFF; border:1px solid #D9E6E8; border-radius:12px; }
            QFrame#voiceData QLabel { color:#435E68; font-family:"Microsoft YaHei UI"; font-size:13px; border:none; background:transparent; }
            QFrame#voiceData QLabel[caption="true"] { font-size:12px; color:#627B84; }
            QFrame#voiceData QLabel[heading="true"] { font-size:15px; font-weight:700; color:#17343D; }
            QFrame#voiceData QFrame[voiceTile="true"] { background:#F3F6F7; border:1px solid #D9E6E8; border-radius:9px; }
            QFrame#voiceData QLabel[value="true"] { font-size:23px; font-weight:700; color:#11595F; }
            QFrame#voiceData QLabel[value="true"][compact="true"] { font-size:18px; }
            QFrame#voiceData QLabel#voiceBadge { color:#187E83; background:#EFF8F5; padding:3px 7px; border-radius:5px; font-size:12px; }
            QFrame#voiceData QFrame#voiceApi { background:#EFF8F5; border:1px solid #D9E6E8; border-radius:9px; }
            QFrame#voiceData QLabel#auditRow { border-bottom:1px solid #D9E6E8; padding:5px 0; font-size:12px; }
            QFrame#voiceData QLabel:focus { border:1px solid #187E83; border-radius:3px; }
        ''')
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(10)
        heading = QHBoxLayout()
        title = label('语音与受控操作')
        title.setProperty('heading', True)
        heading.addWidget(title, 1)
        self.badge = label('仅计数')
        self.badge.setObjectName('voiceBadge')
        heading.addWidget(self.badge)
        box.addLayout(heading)
        # Compatibility summaries remain available to existing integrations/tests,
        # but are not a visible multiline wall of statistics.
        self.metrics = label()
        self.metrics.hide()
        self.realtime_metrics = label()
        self.realtime_metrics.hide()
        self.tile_values, self.tile_details = {}, {}
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, (key, caption) in enumerate((('local', '本地语音 · 会话'), ('live', '实时 API · 会话'), ('spoken', '本地播报 · 次'), ('ops', '受控操作 · 已执行'))):
            tile = QFrame()
            tile.setProperty('voiceTile', True)
            tile_box = QVBoxLayout(tile)
            tile_box.setContentsMargins(10, 8, 10, 8)
            tile_box.setSpacing(3)
            cap = label(caption)
            cap.setProperty('caption', True)
            value, detail = label('0'), label('')
            value.setProperty('value', True)
            detail.setProperty('caption', True)
            for item in (cap, value, detail):
                tile_box.addWidget(item)
            self.tile_values[key], self.tile_details[key] = value, detail
            grid.addWidget(tile, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        api = QFrame()
        api.setObjectName('voiceApi')
        api_box = QVBoxLayout(api)
        api_box.setContentsMargins(10, 9, 10, 9)
        api_box.setSpacing(5)
        api_title = label('API 用量 · 服务报告，非账单')
        api_title.setStyleSheet('font-weight:700;color:#11595F;font-size:13px')
        api_box.addWidget(api_title)
        api_grid = QGridLayout()
        self.api_values = {}
        for index, (key, caption) in enumerate((('tokens', 'tokens · 输入 / 输出'), ('duration', '连接 / 播报处理 · 秒'), ('audio', '发送 / 接收音频 · KB'), ('health', '打断 / 异常 · 次'))):
            cap, value = label(caption), label('0 / 0')
            cap.setProperty('caption', True)
            api_grid.addWidget(cap, index, 0)
            api_grid.addWidget(value, index, 1)
            self.api_values[key] = value
        api_box.addLayout(api_grid)
        box.addWidget(api)
        note = label('语音声学／合成模型训练：0\n本机预装 / 云端 API 推理，均未训练语音模型。转写须本人核对才用于情感适应；互动调整不是神经训练。')
        note.setProperty('caption', True)
        box.addWidget(note)
        audit_title = label('最近审计 · 最多 6 条')
        audit_title.setStyleSheet('font-weight:700;color:#17343D;font-size:13px')
        box.addWidget(audit_title)
        self.recent = label('尚无语音／操作审计')
        self.recent.hide()
        self.audit_rows = []
        for _ in range(6):
            item = label()
            item.setObjectName('auditRow')
            item.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            item.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            box.addWidget(item)
            self.audit_rows.append(item)
        privacy = label('只读审计 · 不含录音、原句、地址、Key 或操作令牌')
        privacy.setProperty('caption', True)
        box.addWidget(privacy)
        self.update_data({})

    def update_data(self, journal):
        from training_journal import KINDS, STATUSES, LABEL_TEXT, LABEL_VALUES
        journal = journal if isinstance(journal, dict) else {}
        totals = journal.get('totals', {})
        totals = totals if isinstance(totals, dict) else {}
        self.metrics.setText('语音会话 {} · 识别片段 {} · 已播报 {}\n打断 {} · 拒收 {}\n操作请求 {} · 已执行 {} · 拦截 {}'.format(*(number(totals.get(key)) for key in ('voice_sessions', 'voice_segments', 'voice_spoken', 'voice_interrupted', 'voice_rejected', 'computer_proposals', 'computer_executed', 'computer_blocked'))))
        self.realtime_metrics.setText('实时 API 会话 {} · 转写 {} · 打断 {} · 异常 {}\n连接累计 {} 秒 · 设备处理播报 {} 秒\n已交给连接的音频 {} KB · 收到 {} KB\n服务报告 tokens：输入 {} / 输出 {}（非账单）'.format(*(number(totals.get(key)) for key in ('realtime_sessions', 'realtime_segments', 'realtime_interrupted', 'realtime_errors')), number(totals.get('realtime_duration_ms')) // 1000, number(totals.get('realtime_played_ms')) // 1000, number(totals.get('realtime_audio_input_bytes')) // 1024, number(totals.get('realtime_audio_output_bytes')) // 1024, number(totals.get('realtime_input_tokens')), number(totals.get('realtime_output_tokens'))))
        for key, metric in (('local', 'voice_sessions'), ('live', 'realtime_sessions'), ('spoken', 'voice_spoken'), ('ops', 'computer_executed')):
            value = self.tile_values[key]
            text = str(number(totals.get(metric)))
            value.setText(text)
            compact = len(text) > 8
            if value.property('compact') != compact:
                value.setProperty('compact', compact)
                value.style().unpolish(value)
                value.style().polish(value)
        self.tile_details['local'].setText('识别 {} · 拒收 {}'.format(number(totals.get('voice_segments')), number(totals.get('voice_rejected'))))
        self.tile_details['live'].setText('转写 {}'.format(number(totals.get('realtime_segments'))))
        self.tile_details['spoken'].setText('打断 {}'.format(number(totals.get('voice_interrupted'))))
        self.tile_details['ops'].setText('请求 {} · 拦截 {}'.format(number(totals.get('computer_proposals')), number(totals.get('computer_blocked'))))
        for key, first, second, divisor in (('tokens', 'realtime_input_tokens', 'realtime_output_tokens', 1), ('duration', 'realtime_duration_ms', 'realtime_played_ms', 1000), ('audio', 'realtime_audio_input_bytes', 'realtime_audio_output_bytes', 1024), ('health', 'realtime_interrupted', 'realtime_errors', 1)):
            self.api_values[key].setText('{} / {}'.format(number(totals.get(first)) // divisor, number(totals.get(second)) // divisor))
        rows = journal.get('events', [])
        lines = []
        for row in rows[:1000] if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get('kind'), str) or row.get('kind') not in ('voice_io', 'computer_control'):
                continue
            labels = row.get('labels', {})
            fixed = next((LABEL_TEXT.get(value, '') for key in ('voice_event', 'computer_action', 'decision') for value in (labels.get(key),) if isinstance(value, str) and value in LABEL_VALUES[key]), '') if isinstance(labels, dict) else ''
            try:
                started = row.get('started', 0)
                if type(started) not in (int, float) or not 0 < started < 32_503_680_000:
                    raise ValueError('invalid timestamp')
                stamp = datetime.fromtimestamp(started).strftime('%H:%M:%S')
            except (ValueError, OverflowError, OSError, TypeError):
                stamp = '--:--:--'
            status = row.get('status')
            status = status if isinstance(status, str) else ''
            lines.append(f"{stamp} · {fixed or KINDS[row['kind']]} · {STATUSES.get(status, '未知')}")
            if len(lines) >= 6:
                break
        self.recent.setText('\n'.join(lines) or '尚无语音／操作审计')
        self.badge.setText('有审计记录' if lines else '尚无审计')
        display = lines or ['尚无语音／操作审计 · 使用后显示计数']
        for index, item in enumerate(self.audit_rows):
            item.setVisible(index < len(display))
            item.setText(display[index] if index < len(display) else '')
