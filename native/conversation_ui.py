"""RAM-only native chat, with separate per-message and session cloud consent.

This widget never performs networking or persistence. The owner handles signals.
``cloud_submitted(str)`` is unchanged: in that synchronous signal handler query
``session_cloud_enabled()`` and ``context_before_current()`` for optional history.
Default per-message consent never authorizes history. A pending/current user
message is excluded from history until ``show_reply(..., error=False)`` succeeds.
"""
from __future__ import annotations

import urllib.parse

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

MAX_HISTORY_ROWS = 80
MAX_MESSAGE_CHARS = 8000
MAX_CONTEXT_MESSAGES = 6
MAX_CONTEXT_CHARS = 4000
MAX_CONTEXT_MESSAGE_CHARS = 1000

STYLE = """
    QDialog { background: #F4F6FA; }
    QScrollArea { background: transparent; border: none; }
    QWidget#chatContent { background: transparent; }
    QLabel, QCheckBox { color: #2B3846; font-family: "Microsoft YaHei UI"; font-size: 13px; background: transparent; }
    QCheckBox { spacing: 6px; }
    QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #8394A6; border-radius: 3px; background: #FFFFFF; }
    QCheckBox::indicator:checked { background: #3B7EA1; border-color: #2B6688; }
    QCheckBox::indicator:disabled { border-color: #B7C2CE; }
    QCheckBox::indicator:checked:disabled { background: #7D9CAD; }
    QLabel#chatTitle { font-size: 16px; font-weight: 700; color: #1B2838; }
    QLabel#chatHint, QLabel#cloudState, QLabel#onlineState { font-size: 12px; color: #5E7186; }
    QFrame#bubbleUser { background: #E4F1FA; border: 1px solid #C9DEEC; border-radius: 11px; }
    QFrame#bubbleAssistant { background: #FFFFFF; border: 1px solid #E6ECF3; border-radius: 11px; }
    QLabel#bubbleText { font-size: 14px; color: #26394d; }
    QLabel#bubbleMeta { font-size: 12px; color: #65768A; }
    QLineEdit { background: #FFFFFF; border: 1px solid #DCE3EB; border-radius: 9px; padding: 7px 9px; font-size: 14px; }
    QLineEdit:focus { border-color: #8FBFD4; }
    QPushButton { font-size: 13px; background: #FFFFFF; color: #415066; border: 1px solid #DCE3EB; border-radius: 8px; padding: 5px 9px; }
    QPushButton:hover { background: #F1F6FB; }
    QPushButton#modeLocal:checked, QPushButton#modeCloud:checked { background: #E8F2F9; border-color: #BFD8E8; color: #2E5B78; font-weight: 600; }
    QPushButton#chatSend { background: #E8F2F9; border-color: #C9DEEC; color: #2E5B78; font-weight: 600; }
    QPushButton:disabled { color: #A6AFBA; background: #F7F9FB; }
    QFrame#confirmBar { background: #FFF9EC; border: 1px solid #EEDFBE; border-radius: 9px; }
    QLabel#confirmText { color: #5A4A2B; font-size: 13px; }
"""


def _plain_label(text="", name=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        label.setObjectName(name)
    return label


class ConversationDialog(QDialog):
    local_submitted = Signal(str)
    cloud_submitted = Signal(str)
    settings_requested = Signal()
    voice_tools_requested = Signal()
    online_refresh_requested = Signal()
    online_enabled_changed = Signal(bool)
    retrieval_review_requested = Signal()
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("和云岚聊天")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # Set before any show, including a proactive first show. No focus request
        # is made in construction, proactive display, or passive state updates.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(STYLE)
        self.cloud = {"endpoint": "", "model": "", "max_tokens": 256, "key_saved": False}
        self.pending_text = ""
        self.busy = False
        self.compact = True
        self._confirm_kind = ""
        self._confirm_restore_compact = False
        self._session_target = None
        self._rows = []
        self._awaiting_user = None
        self._online_summary = ""
        self._closed_notified = False
        # Escape/reject/accept finish a QDialog without necessarily delivering a
        # closeEvent. Revoke volatile consent for every native closing path.
        self.finished.connect(self._finish_dialog)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)
        # Very small screens must be allowed to constrain the outer dialog.
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        title_row = QHBoxLayout()
        title_row.addWidget(_plain_label("和云岚聊天", "chatTitle"), 1)
        self.voice_tools_button = QPushButton('语音／权限')
        self.voice_tools_button.clicked.connect(self.voice_tools_requested.emit)
        title_row.addWidget(self.voice_tools_button)
        self.expand_button = QPushButton("展开")
        self.expand_button.clicked.connect(self.toggle_compact)
        title_row.addWidget(self.expand_button)
        layout.addLayout(title_row)
        self.hint = _plain_label("聊天原文仅在内存保留最近80行；明确自述可在本机适应情感权重，不保存原句。云端默认逐条确认。", "chatHint")
        self.hint.setWordWrap(True)
        self.hint.hide()
        layout.addWidget(self.hint)

        modes = QHBoxLayout()
        modes.setSpacing(5)
        self.local_button = QPushButton("本地")
        self.local_button.setObjectName("modeLocal")
        self.local_button.setCheckable(True)
        self.local_button.setChecked(True)
        self.cloud_button = QPushButton("云端")
        self.cloud_button.setObjectName("modeCloud")
        self.cloud_button.setCheckable(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.local_button)
        self.mode_group.addButton(self.cloud_button)
        modes.addWidget(self.local_button)
        modes.addWidget(self.cloud_button)
        self.cloud_state = _plain_label("云端未配置", "cloudState")
        self.cloud_state.setMinimumWidth(0)
        self.cloud_state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        modes.addWidget(self.cloud_state, 1)
        self.settings_button = QPushButton("接口设置")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        modes.addWidget(self.settings_button)
        layout.addLayout(modes)
        self.local_button.toggled.connect(self._mode_changed)

        online_row = QHBoxLayout()
        online_row.setSpacing(5)
        self.online_checkbox = QCheckBox("动态公开资料")
        self.online_checkbox.setToolTip("默认关闭。由你勾选后，宿主才可刷新公开资料；本窗口不联网。")
        # clicked, not toggled: programmatic synchronization is not permission.
        self.online_checkbox.clicked.connect(self._online_clicked)
        online_row.addWidget(self.online_checkbox)
        self.online_state = _plain_label("未连接", "onlineState")
        online_row.addWidget(self.online_state, 1)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.online_refresh_requested.emit)
        online_row.addWidget(self.refresh_button)
        layout.addLayout(online_row)
        self.online_summary = _plain_label("", "chatHint")
        self.online_summary.setWordWrap(True)
        self.online_summary.hide()
        layout.addWidget(self.online_summary)

        extra = QHBoxLayout()
        self._memory_target = None
        self.memory_checkbox = QCheckBox("本次允许模型使用记忆摘要")
        self.memory_checkbox.setToolTip("默认关闭。需单独确认；最多3条摘要/800字符，关闭窗口或切换目标即撤销。")
        self.memory_checkbox.clicked.connect(self._memory_clicked)
        extra.addWidget(self.memory_checkbox, 1)
        self.review_button = QPushButton("检索反馈")
        self.review_button.setToolTip("自愿在本机标注这次命中；不自动保存聊天、不上传、不训练。")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self.retrieval_review_requested.emit)
        extra.addWidget(self.review_button)
        layout.addLayout(extra)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(20)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("chatContent")
        self.messages = QVBoxLayout(content)
        self.messages.setContentsMargins(2, 4, 2, 4)
        self.messages.setSpacing(7)
        self.messages.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        # In compact mode a second layout pass often changes the scroll range
        # after the initial zero-delay callback. Coalesce bursts and follow that
        # late range only for this append; never pin ordinary owner scrolling.
        self._scroll_pending = False
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._scroll_to_end)
        self._scroll_settle_timer = QTimer(self)
        self._scroll_settle_timer.setSingleShot(True)
        self._scroll_settle_timer.setInterval(50)
        self._scroll_settle_timer.timeout.connect(self._finish_scroll_to_end)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._scroll_range_changed)

        self.session_checkbox = QCheckBox("本次连续云聊")
        self.session_checkbox.setToolTip("需另点「允许本次连续」。仅本窗口输入及最近最多 6 条对话；每次当前消息与历史合计最多 4000 字符。不含其他 DSH 会话、文件或屏幕。关闭窗口或接口 / 模型变化即撤销。")
        self.session_checkbox.clicked.connect(self._session_clicked)
        layout.addWidget(self.session_checkbox)

        self.confirm_bar = QFrame()
        self.confirm_bar.setObjectName("confirmBar")
        confirm_layout = QVBoxLayout(self.confirm_bar)
        confirm_layout.setContentsMargins(8, 6, 8, 6)
        confirm_layout.setSpacing(5)
        # Long typed text and consent wording remain reviewable without making
        # the dialog taller than the screen.
        self.confirm_scroll = QScrollArea()
        self.confirm_scroll.setWidgetResizable(True)
        self.confirm_scroll.setMinimumHeight(60)
        self.confirm_scroll.setMaximumHeight(180)
        self.confirm_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.confirm_label = _plain_label("", "confirmText")
        self.confirm_label.setWordWrap(True)
        self.confirm_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.confirm_scroll.setWidget(self.confirm_label)
        confirm_buttons = QHBoxLayout()
        confirm_buttons.addStretch()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel_confirm)
        self.session_allow_button = QPushButton("允许本次连续")
        self.session_allow_button.clicked.connect(self.allow_session_cloud)
        self.session_allow_button.hide()
        self.confirm_send_button = QPushButton("确认发送")
        self.confirm_send_button.setObjectName("chatSend")
        self.confirm_send_button.clicked.connect(self.confirm_cloud)
        confirm_buttons.addWidget(self.cancel_button)
        confirm_buttons.addWidget(self.session_allow_button)
        confirm_buttons.addWidget(self.confirm_send_button)
        confirm_layout.addWidget(self.confirm_scroll)
        confirm_layout.addLayout(confirm_buttons)
        self.confirm_bar.hide()
        layout.addWidget(self.confirm_bar)

        input_row = QHBoxLayout()
        input_row.setSpacing(7)
        self.input = QLineEdit()
        self.input.setMaxLength(2000)
        self.input.setPlaceholderText("说点什么…")
        self.input.returnPressed.connect(self.send)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("chatSend")
        self.send_button.clicked.connect(self.send)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button)
        layout.addLayout(input_row)
        # Enter submits the input, never a default confirmation/consent button.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self._fit_size()

    def _available_geometry(self):
        screen = self.screen()
        return screen.availableGeometry() if screen is not None else None

    def _fit_size(self):
        available = self._available_geometry()
        width, height = (410, 300) if self.compact else (440, 560)
        if available is not None:
            # Leave room for native title/frame decorations, also after show.
            frame = self.frameGeometry()
            extra_w = max(16, frame.width() - self.width())
            extra_h = max(40, frame.height() - self.height())
            width = min(width, max(1, available.width() - extra_w))
            height = min(height, max(1, available.height() - extra_h))
        self.setMinimumSize(0, 0)
        self.resize(width, height)
        if available is not None:
            frame = self.frameGeometry()
            left = max(available.left(), min(frame.left(), available.right() - frame.width() + 1))
            top = max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1))
            self.move(left, top)

    def toggle_compact(self):
        self.set_compact(not self.compact)

    def set_compact(self, compact=True):
        """Explicit size choice; preserves transcript, input, and consent.

        A choice made during a pending confirmation overrides its temporary
        auto-expansion, so accepting/cancelling will not undo the owner's choice.
        """
        self._confirm_restore_compact = False
        self._apply_compact(compact)

    def _apply_compact(self, compact):
        self.compact = bool(compact)
        self.expand_button.setText("展开" if self.compact else "收起")
        self.hint.setVisible(not self.compact)
        self.online_summary.setVisible(not self.compact and bool(self._online_summary))
        self.confirm_scroll.setMinimumHeight(60 if self.compact else 160)
        self._fit_size()

    def _show_confirmation(self):
        self.confirm_scroll.verticalScrollBar().setValue(0)
        self.confirm_bar.show()
        if self.compact:
            self._apply_compact(False)
            self._confirm_restore_compact = True
        self._update_controls()

    def _hide_confirmation(self):
        self.confirm_bar.hide()
        restore = self._confirm_restore_compact
        self._confirm_restore_compact = False
        if restore:
            self._apply_compact(True)

    def show_proactive(self, text, meta="云岚主动搭话"):
        """Append replyable dialogue and show nonmodally, without activation.

        Never calls raise_, activateWindow or setFocus; existing draft, channel,
        busy state and consent are unchanged. Expanded windows stay expanded.
        """
        # Proactive lines may be composed from durable local memories. They
        # were not supplied by the user and never become automatic history.
        self.add_message("assistant", text, meta, context=False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._fit_size()
        self.show()
        self._fit_size()

    def _online_clicked(self, checked):
        self.online_enabled_changed.emit(bool(checked))

    def set_online_state(self, info):
        """Render safe host facts; no networking or permission signal.

        ``info`` accepts status='live'|'cached'|'offline', optional enabled: bool,
        and an optional short factual summary: str (shown in expanded mode).
        Unknown status is always displayed as 未连接, never arbitrary markup.
        """
        info = info if isinstance(info, dict) else {}
        status = info.get("status")
        self.online_state.setText({"live": "实时在线", "cached": "缓存资料", "offline": "未连接"}.get(status, "未连接") if isinstance(status, str) else "未连接")
        if isinstance(info.get("enabled"), bool):
            blocker = QSignalBlocker(self.online_checkbox)
            self.online_checkbox.setChecked(info["enabled"])
            del blocker
        summary = info.get("summary", "")
        self._online_summary = " ".join(summary.split())[:160] if isinstance(summary, str) else ""
        self.online_summary.setText(self._online_summary)
        self.online_summary.setVisible(not self.compact and bool(self._online_summary))
        self.online_state.setToolTip(self._online_summary)

    def _target(self):
        endpoint, model = self.cloud["endpoint"], self.cloud["model"]
        if not endpoint or not model.strip() or len(endpoint) > 2048:
            return None
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            parsed.port  # Reject malformed/out-of-range ports as well.
            if (not parsed.hostname or parsed.scheme not in ("https", "http")
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or any(ch.isspace() for ch in endpoint)
                    or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
                return None
        except ValueError:
            return None
        return endpoint, model

    def set_cloud_state(self, config):
        config = config if isinstance(config, dict) else {}
        old_target = (self.cloud["endpoint"], self.cloud["model"])
        old_tokens = self.cloud["max_tokens"]
        tokens = config.get("max_tokens", 256)
        self.cloud = {
            "endpoint": config.get("endpoint", "") if isinstance(config.get("endpoint"), str) else "",
            "model": config.get("model", "") if isinstance(config.get("model"), str) else "",
            "max_tokens": tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and 16 <= tokens <= 1024 else 256,
            "key_saved": bool(config.get("key_saved")),
        }
        if old_target != (self.cloud["endpoint"], self.cloud["model"]):
            self._reset_session()
            self.cancel_confirm()
        elif old_tokens != self.cloud["max_tokens"] and self._confirm_kind == "message":
            # The inline approval names this cost/output budget. Do not keep an
            # obsolete preview if settings change while it is pending.
            self.cancel_confirm()
        if self._target():
            host = urllib.parse.urlsplit(self.cloud["endpoint"]).hostname
            self.cloud_state.setText(f"{host} · {self.cloud['model']}")
        else:
            self.cloud_state.setText("云端未配置 · 先点接口设置")
        self.cloud_state.setToolTip(f"{self.cloud['endpoint']} · {self.cloud['model']}")

    def _mode_changed(self, _checked):
        self.cancel_confirm()

    def is_cloud_mode(self):
        return self.cloud_button.isChecked()

    def _append_row(self, widget, role=None, text="", eligible=False, private_context=False):
        entry = {"widget": widget, "role": role, "content": text, "eligible": bool(eligible),
                 "private_context": bool(private_context)}
        self.messages.insertWidget(self.messages.count() - 1, widget)
        self._rows.append(entry)
        while len(self._rows) > MAX_HISTORY_ROWS:
            old = self._rows.pop(0)
            self.messages.removeWidget(old["widget"])
            old["widget"].hide()
            old["widget"].deleteLater()
            if self._awaiting_user is old:
                self._awaiting_user = None
        self._scroll_pending = True
        self._scroll_timer.start(0)
        self._scroll_settle_timer.start()
        return entry

    def add_message(self, role, text, meta="", *, context=True, private_context=False):
        """Append a plaintext bubble. Only exact user/assistant roles qualify.

        Hosts should use add_system for status/network notices, or context=False
        for other non-dialogue bubbles; errors in show_reply are excluded always.
        """
        text = str(text)[:MAX_MESSAGE_CHARS]
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        bubble = QFrame()
        bubble.setObjectName("bubbleUser" if role == "user" else "bubbleAssistant")
        bubble.setMaximumWidth(350)
        # Keep standard attribution on one line even for a very short reply;
        # do not let QLabel's narrow wrapped sizeHint create orphan characters.
        bubble.setMinimumWidth(min(240, max(1, self.width() - 60)))
        box = QVBoxLayout(bubble)
        box.setContentsMargins(10, 7, 10, 7)
        box.setSpacing(3)
        label = _plain_label(text, "bubbleText")
        label.setWordWrap(True)
        box.addWidget(label)
        if meta:
            meta_label = _plain_label(str(meta)[:200], "bubbleMeta")
            meta_label.setWordWrap(True)
            box.addWidget(meta_label)
        if role == "user":
            row.addStretch()
        row.addWidget(bubble)
        if role != "user":
            row.addStretch()
        safe_role = role if isinstance(role, str) and role in ("user", "assistant") else None
        return self._append_row(widget, safe_role, text, context and safe_role is not None,
                                private_context=private_context)

    def add_system(self, text):
        label = _plain_label(str(text)[:MAX_MESSAGE_CHARS], "chatHint")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._append_row(label)

    def _scroll_to_end(self):
        self.messages.activate()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _scroll_range_changed(self, _minimum, _maximum):
        if self._scroll_pending:
            self._scroll_timer.start(0)

    def _finish_scroll_to_end(self):
        self._scroll_to_end()
        self._scroll_pending = False

    def session_cloud_enabled(self):
        """Whether explicit volatile consent still matches a valid target."""
        target = self._target()
        return target is not None and self._session_target == target

    def cloud_context(self, exclude_latest=False):
        """Return fresh whitelisted {role, content} copies, never raw metadata.

        Only available with active target-bound session consent. At most six
        messages, each <=1000 chars and <=4000 chars total (newest preferred).
        While a user message awaits reply its length is reserved from that 4000
        character budget, so current submission + returned history also <=4000.
        Failed/pending typed messages, errors and system/network notices are not
        eligible. exclude_latest omits the newest user row, useful at emission;
        the current submitted user is already ineligible until a successful reply.
        """
        if not self.session_cloud_enabled():
            return []
        rows = self._rows
        if exclude_latest and rows and rows[-1]["role"] == "user":
            rows = rows[:-1]
        current_chars = len(self._awaiting_user["content"]) if self._awaiting_user is not None else 0
        result, remaining = [], max(0, MAX_CONTEXT_CHARS - current_chars)
        for entry in reversed(rows):
            if not entry["eligible"] or entry["role"] not in ("user", "assistant"):
                continue
            if entry.get('private_context') and not self.memory_context_enabled():
                continue
            content = entry["content"][:min(MAX_CONTEXT_MESSAGE_CHARS, remaining)]
            if content:
                result.append({"role": entry["role"], "content": content})
                remaining -= len(content)
            if len(result) >= MAX_CONTEXT_MESSAGES or remaining <= 0:
                break
        return list(reversed(result))

    def context_before_current(self):
        """History for cloud_submitted's handler; never duplicates its str arg."""
        return self.cloud_context(exclude_latest=True)

    def reset_local_context(self):
        """Erase RAM rows and revoke both grants without closing the window.

        Used by explicit forget/reset, not by routine refresh. Host must also
        invalidate its in-flight request identity before calling this method.
        """
        self._reset_session()
        self.pending_text = ''
        self._awaiting_user = None
        self._confirm_kind = ''
        self._hide_confirmation()
        self._scroll_timer.stop()
        self._scroll_settle_timer.stop()
        self._scroll_pending = False
        for entry in self._rows:
            widget = entry['widget']
            self.messages.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
            entry['content'] = ''
            entry['eligible'] = False
        self._rows.clear()
        self.input.clear()
        self.review_button.setEnabled(False)
        self.set_busy(False)

    def _reset_session(self, *, reset_memory=True):
        self._session_target = None
        blocker = QSignalBlocker(self.session_checkbox)
        self.session_checkbox.setChecked(False)
        del blocker
        if reset_memory:
            self._memory_target = None
            if hasattr(self, 'memory_checkbox'):
                blocker = QSignalBlocker(self.memory_checkbox)
                self.memory_checkbox.setChecked(False)
                del blocker

    def memory_context_enabled(self):
        return self._target() is not None and self._memory_target == self._target()

    def set_retrieval_available(self, available):
        self.review_button.setEnabled(bool(available) and not self.busy)

    def _memory_clicked(self, checked):
        self._memory_target = None
        blocker = QSignalBlocker(self.memory_checkbox)
        self.memory_checkbox.setChecked(False)
        del blocker
        if not checked:
            self.cancel_confirm()
            return
        self.cancel_confirm()
        if not self._target():
            self.add_system('先配置模型接口，再单独允许记忆摘要；没有发送任何资料。')
            return
        self._confirm_kind = 'memory'
        self.confirm_label.setText(
            f"单独允许本地记忆摘要发送到：{self.cloud['endpoint']} · {self.cloud['model']}\n"
            '仅在你点击发送时附带最多3条相关摘要、最多800字符；可能含本地偏好/回顾。'
            '历史+当前消息+摘要合计不超过4000字符。不附完整存档、其他DSH会话、文件或工具。'
            '仅本窗口/本目标有效，关闭窗口或更改接口模型立即撤销。现在允许不会发送消息。')
        self.session_allow_button.setText('允许本次记忆摘要')
        self.session_allow_button.show()
        self.confirm_send_button.hide()
        self._show_confirmation()

    def _session_clicked(self, checked):
        if not checked:
            self._reset_session(reset_memory=False)
            if self._confirm_kind == "session":
                self.cancel_confirm()
            return
        self._reset_session(reset_memory=False)
        self.cancel_confirm()
        if not self._target():
            self.add_system("先在「接口设置」配置有效的云端地址与模型，再允许本次连续云聊。")
            self.settings_requested.emit()
            return
        self._confirm_kind = "session"
        self.session_allow_button.setText("允许本次连续云聊")
        self.confirm_label.setText(
            f"本次连续云聊的目标：\n{self.cloud['endpoint']}\n模型：{self.cloud['model']}\n"
            "仅发送你在本窗口输入的消息，以及最近最多 6 条简短对话（每次当前消息与历史合计不超过 4000 字符）。"
            "不包含其他 DSH 会话、文件、屏幕或工具调用。\n"
            "仅本窗口有效；关闭窗口或接口 / 模型改变立即撤销。"
            "勾选或允许不会发送消息；允许后，仍需你逐次点发送或按回车。"
        )
        self.session_allow_button.show()
        self.confirm_send_button.hide()
        self._show_confirmation()

    def allow_session_cloud(self):
        """Explicit button action enables consent only; NEVER emits a message."""
        if self._confirm_kind == 'memory' and self._target() and not self.busy:
            self._memory_target = self._target()
            blocker = QSignalBlocker(self.memory_checkbox)
            self.memory_checkbox.setChecked(True)
            del blocker
            self._confirm_kind = ''
            self._hide_confirmation()
            self._update_controls()
            return
        if self._confirm_kind != "session" or not self._target() or self.busy:
            return
        self._session_target = self._target()
        blocker = QSignalBlocker(self.session_checkbox)
        self.session_checkbox.setChecked(True)
        del blocker
        self._confirm_kind = ""
        self._hide_confirmation()
        self._update_controls()

    def send(self):
        if self.busy or self._confirm_kind:
            return
        text = self.input.text().strip()
        if not text:
            return
        if not self.is_cloud_mode():
            self.input.clear()
            self._awaiting_user = self.add_message("user", text, "本地", context=False)
            self.local_submitted.emit(text)
            return
        if not self._target():
            self.add_system("云端接口还没配置。点右上角「接口设置」填写后，再确认发送。")
            self.settings_requested.emit()
            return
        if self.session_cloud_enabled():
            self._submit_cloud(text)
            return
        self.pending_text = text
        self.input.clear()
        self._confirm_kind = "message"
        context_notice = (
            '不含对话历史；另按你单独允许的本次记忆权限，附带最多3条相关摘要/800字符。'
            if self.memory_context_enabled() else '仅这一段，不含历史或本地记忆摘要。')
        self.confirm_label.setText(
            f"将发送到 {self.cloud['endpoint']} · {self.cloud['model']} · 最多 {self.cloud['max_tokens']} tokens：\n{text}\n"
            + context_notice + '不含其他 DSH 会话、文件、屏幕或工具调用。'
        )
        self.session_allow_button.hide()
        self.confirm_send_button.show()
        self._show_confirmation()

    def cancel_confirm(self):
        text = self.pending_text
        self.pending_text = ""
        self._confirm_kind = ""
        self._hide_confirmation()
        if text and not self.busy:
            self.input.setText(text)
        self._update_controls()

    def confirm_cloud(self):
        if self._confirm_kind != "message" or self.busy or not self._target():
            return
        text = self.pending_text
        if not text:
            return
        self.pending_text = ""
        self._confirm_kind = ""
        self._hide_confirmation()
        self._submit_cloud(text)

    def _submit_cloud(self, text):
        self._awaiting_user = self.add_message("user", text, "云端", context=False)
        self.input.clear()
        self.set_busy(True)
        self.cloud_submitted.emit(text)

    def _update_controls(self):
        enabled = not self.busy and not self._confirm_kind
        self.input.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        self.local_button.setEnabled(not self.busy)
        self.cloud_button.setEnabled(not self.busy)
        self.session_checkbox.setEnabled(not self.busy)
        self.memory_checkbox.setEnabled(not self.busy)
        self.send_button.setText("等待…" if self.busy else "发送")

    def set_busy(self, busy):
        self.busy = bool(busy)
        self._update_controls()

    def show_reply(self, text, meta="", error=False, *, private_context=False):
        if self._awaiting_user is not None:
            self._awaiting_user["eligible"] = not error
            self._awaiting_user = None
        self.add_message("assistant", text, meta if meta else ("云端错误" if error else "本地"),
                         context=not error, private_context=private_context)
        self.set_busy(False)
        # A delayed reply to an inactive proactive window must not steal focus.
        if self.isActiveWindow():
            self.input.setFocus()

    def _finish_dialog(self, _result=None):
        if self._closed_notified:
            return
        self._closed_notified = True
        self._reset_session()
        self.cancel_confirm()
        self.closed.emit()

    def closeEvent(self, event):
        self._finish_dialog()
        super().closeEvent(event)
