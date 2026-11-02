"""Phase 0 native BigFish helper.

Derived from dsh-dafeiyu 0.1.23 (MIT, Copyright (c) 2026 QCYTSN,
https://github.com/QCYTSN/dsh-dafeiyu); see native/upstream-LICENSE.
This derivative is MIT as well; see the repository LICENSE and NOTICE.md.

The DSH plugin owns this process and sends newline-delimited JSON over stdin.
Closing stdin is a lifecycle signal: the helper exits instead of becoming an
independent desktop application.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
import urllib.parse
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, TextIO

try:
    from .animation_model import AnimationModel, crossfade_duration
    from .layout_store import default_layout_path, load_layout, save_layout
    from .asset_paths import bundle_root
    from .pet_geometry import bubble_top, position_window
except ImportError:
    from animation_model import AnimationModel, crossfade_duration
    from layout_store import default_layout_path, load_layout, save_layout
    from asset_paths import bundle_root
    from pet_geometry import bubble_top, position_window


PROTOCOL_VERSION = 1
STATES = {"IDLE", "THINKING", "WORKING", "WAITING", "SUCCESS", "ERROR", "DISCONNECTED"}
# A decoded 412x344 ARGB32 frame costs ~0.55 MB, so keeping every frame of a
# 24 fps set resident would need well over a gigabyte. Frames are read from disk
# once as compressed bytes and decoded through a bounded cache instead.
DECODED_FRAME_CACHE = 24


def configure_qt_platform() -> None:
    """Prefer XWayland when available so desktop-window controls keep working."""
    if sys.platform != "linux" or os.environ.get("QT_QPA_PLATFORM"):
        return
    platforms: list[str] = []
    if os.environ.get("DISPLAY"):
        platforms.append("xcb")
    if os.environ.get("WAYLAND_DISPLAY"):
        platforms.append("wayland")
    if platforms:
        os.environ["QT_QPA_PLATFORM"] = ";".join(platforms)


def configure_stdio() -> None:
    """Make the JSONL pipe UTF-8 regardless of the Windows console code page."""
    for stream, errors in ((sys.stdin, "strict"), (sys.stdout, "backslashreplace"), (sys.stderr, "backslashreplace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors=errors)


def loopback_url(value, fallback: str = "http://127.0.0.1:3080/") -> str:
    """Only open loopback http(s) URLs; never arbitrary schemes or remote hosts."""
    try:
        parsed = urllib.parse.urlsplit(str(value or ""))
        if (parsed.scheme not in ("http", "https") or not parsed.netloc
                or parsed.username or parsed.password or parsed.fragment):
            return fallback
        if (parsed.hostname or "").lower() not in ("127.0.0.1", "localhost", "::1"):
            return fallback
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    except ValueError:
        return fallback


def parse_message(line: str) -> dict[str, Any]:
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("message must be an object")
    if message.get("protocolVersion") != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    kind = message.get("kind")
    if kind in {"state", "pulse"} and message.get("state") not in STATES:
        raise ValueError("unsupported companion state")
    return message


def emit_reply(kind: str, **payload: Any) -> None:
    print(
        json.dumps(
            {"protocolVersion": PROTOCOL_VERSION, "kind": kind, "timestamp": int(time.time() * 1000), **payload},
            ensure_ascii=False,
        ),
        flush=True,
    )


# ---- Glove cursor (native Win32 .cur) ----
def _cursor_api() -> tuple[Any, Any, Any]:
    """Return configured (LoadCursorFromFileW, SetCursor, LoadCursorW) callables.

    Function signatures are declared explicitly so HCURSOR values are handled
    as pointer-sized handles: without argtypes/restype, ctypes treats the
    return value as a C int and truncates 64-bit HCURSORs. wintypes has no
    HCURSOR type, so ``wintypes.HANDLE`` (pointer-sized) is used.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    load_from_file = user32.LoadCursorFromFileW
    load_from_file.argtypes = [wintypes.LPCWSTR]
    load_from_file.restype = wintypes.HANDLE

    set_cursor = user32.SetCursor
    set_cursor.argtypes = [wintypes.HANDLE]
    set_cursor.restype = wintypes.HANDLE

    load_cursor = user32.LoadCursorW
    load_cursor.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR]
    load_cursor.restype = wintypes.HANDLE

    return load_from_file, set_cursor, load_cursor


def load_native_cursor(path: Path) -> Any:
    """Load a .cur cursor with LoadCursorFromFileW (pointer-sized handles).

    Returns None outside Windows or on failure; callers then fall back to the
    Qt default cursor. The resource is loaded at its natural size: per the MSDN
    remarks, LoadCursorFromFileW does not participate in DPI virtualization, so
    the 32x32 .cur is NOT scaled up automatically; the OS renders the cursor
    bitmap on the same pipeline as system cursors.
    """
    if sys.platform != "win32":
        return None
    try:
        handle = _cursor_api()[0](str(path))
        return handle or None
    except Exception:
        return None


def set_native_cursor(handle: Any) -> None:
    """Set the current cursor immediately (bypasses Qt's cursor pipeline)."""
    try:
        _cursor_api()[1](handle)
    except Exception:
        pass


def reset_native_cursor() -> None:
    """Restore the default arrow cursor (IDC_ARROW)."""
    try:
        import ctypes

        # MAKEINTRESOURCE(32512): the identifier is passed as a pointer value.
        arrow = ctypes.cast(ctypes.c_void_p(32512), ctypes.c_wchar_p)
        _cursor_api()[2](None, arrow)
    except Exception:
        pass


class GloveCursorController:
    """Pure state machine for the glove cursor; no Qt or Win32 dependencies.

    ``open_h`` / ``closed_h`` may be None (non-Windows, or the .cur assets are
    missing): every query then returns None, so callers simply fall back to the
    default cursor. This keeps all cursor-state decisions testable.
    """

    WM_LBUTTONDOWN = 0x0201

    def __init__(self, open_h: Any, closed_h: Any) -> None:
        self.open_h = open_h
        self.closed_h = closed_h
        self.pressed = False

    def handle_for(self, closed: bool) -> Any:
        return self.closed_h if closed else self.open_h

    def on_enter(self) -> Any:
        """Pointer entered the pet window: show the open hand."""
        return self.open_h

    def on_leave(self) -> None:
        """Pointer left the pet window: clear the pressed flag, reset to arrow."""
        self.pressed = False
        return None

    def on_press(self) -> Any:
        """Left button pressed: show the closed fist."""
        self.pressed = True
        return self.closed_h

    def on_release(self, inside: bool) -> Any:
        """Left button released: open hand when still inside, otherwise None."""
        self.pressed = False
        return self.open_h if inside else None

    def on_wm_setcursor(self, mouse_msg: int) -> Any:
        """WM_SETCURSOR decision: closed on WM_LBUTTONDOWN (or while pressed)."""
        return self.handle_for(mouse_msg == self.WM_LBUTTONDOWN or self.pressed)


class EventRecorder:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._stream: TextIO | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("a", encoding="utf-8")

    def record(self, message: dict[str, Any]) -> None:
        if self._stream is None:
            return
        self._stream.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._stream.flush()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()


def run_headless(recorder: EventRecorder) -> int:
    try:
        emit_reply("ready")
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = parse_message(line)
            except (ValueError, json.JSONDecodeError) as error:
                print(json.dumps({"kind": "error", "message": str(error)}), flush=True)
                continue
            recorder.record(message)
            if message.get("kind") == "ping":
                emit_reply("pong")
                continue
            if message.get("kind") == "shutdown":
                break
    finally:
        recorder.close()
    return 0


def run_visual(recorder: EventRecorder, snapshot_path: Path | None = None) -> int:
    configure_qt_platform()
    try:
        from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QPoint, QPointF, QRectF, Qt, QTimer, QUrl, Signal
        from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF
        from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QWidget, QWidgetAction
        from growth_native import GrowthReader, GrowthDialog
        from growth_theme import MENU_QSS, icon as theme_icon
        from growth_engine import GrowthEngine
        from memory_engine import MemoryEngine
        from memory_reply import broad_memory_query, compose_memory_answer, compose_public_answer
        from public_crawler import PoetryCrawler
        from behavior import BehaviorLearner
        from chat import reply_to, DEFAULT_REPLIES
        from motion_model import plan_run, run_pose
        from neural_brain import NeuralBrain
        from emotion_model import EmotionModel, LABELS
        from cloud_chat import load_config, ask_cloud
        from cloud_ui import CloudSettingsDialog
        from conversation_ui import ConversationDialog
        from voice_runtime import VoiceRuntime, HOTKEY_ID
        from learning_state import PublicLearningState
        from live_online import LiveOnlineState, fetch_live_facts
        from learning_hub import LearningHub
        from knowledge_sources import fetch_knowledge_source, CATALOG as KNOWLEDGE_SOURCES
        from web_learning import fetch_builtin_lexicon, PUBLIC_SOURCE_NAME
    except ImportError as error:
        print(
            f"PySide6 is required for visual mode ({error}). Run with --headless for protocol tests.",
            file=sys.stderr,
        )
        recorder.close()
        return 2

    class Inbox(QObject):
        message = Signal(dict)
        closed = Signal()

    manifest_path = bundle_root() / "assets" / "pet-manifest.json"
    asset_root = manifest_path.parent / "pet"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"Unable to load BigFish asset manifest: {error}", file=sys.stderr)
        recorder.close()
        return 2

    class GloveCursorFilter(QAbstractNativeEventFilter):
        """Owns WM_SETCURSOR while the pointer is over the pet window.

        The pet shows a glove hand cursor: an open hand on hover and a closed
        fist while the left button is held. Native .cur cursors are loaded with
        LoadCursorFromFileW and handed to the OS cursor pipeline directly (the
        same rendering path as system cursors; note the API loads the resource
        at its natural 32x32 size and does not participate in DPI
        virtualization). The message is consumed immediately instead of going
        through Qt's QCursor bitmaps, which are re-scaled by Qt and only take
        effect on the next cursor update cycle.
        """

        def __init__(self, widget: Any, open_h: Any, closed_h: Any) -> None:
            super().__init__()
            self.widget = widget
            self.open_h = open_h
            self.closed_h = closed_h

        def nativeEventFilter(self, event_type: bytes, message: Any):
            try:
                if event_type not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                    return False, 0
                import ctypes
                from ctypes import wintypes

                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312 and int(msg.wParam) == HOTKEY_ID:  # WM_HOTKEY
                    self.widget._emergency_stop()
                    return True, 0
                if msg.message != 0x0020:  # WM_SETCURSOR
                    return False, 0
                hwnd = int(msg.hWnd) if msg.hWnd else 0
                if hwnd and hwnd != int(self.widget.winId()):
                    # Leave popups (context menu, …) to Qt's default cursor.
                    return False, 0
                hit_test = msg.lParam & 0xFFFF
                if hit_test != 1:  # HTCLIENT
                    return False, 0
                mouse_msg = (msg.lParam >> 16) & 0xFFFF
                handle = self.widget.glove.on_wm_setcursor(mouse_msg)
                if handle:
                    set_native_cursor(handle)
                    return True, 0
            except Exception:
                pass
            return False, 0

    class CompanionWindow(QWidget, VoiceRuntime):
        cloud_reply = Signal(dict)
        learning_result = Signal(dict)
        learning_progress = Signal(dict)
        online_result = Signal(dict)
        voice_event = Signal(dict)
        crawl_result = Signal(dict)

        LABELS = {
            "IDLE": "休息中",
            "THINKING": "思考中",
            "WORKING": "干活中",
            "WAITING": "等你呢",
            "SUCCESS": "完成啦",
            "ERROR": "出问题了",
            "DISCONNECTED": "已断开",
        }

        def __init__(self) -> None:
            super().__init__()
            self.layout_path = default_layout_path()
            self.layout = load_layout(self.layout_path)
            configured_scale = os.environ.get("DSH_DAFEIYU_SCALE")
            try:
                self.scale = min(1.4, max(0.55, float(configured_scale))) if configured_scale else self.layout["scale"]
            except ValueError:
                self.scale = self.layout["scale"]
            configured_bubble_scale = os.environ.get("DSH_DAFEIYU_BUBBLE_SCALE")
            try:
                self.bubble_scale = (
                    min(1.2, max(0.8, float(configured_bubble_scale)))
                    if configured_bubble_scale
                    else self.layout["bubbleScale"]
                )
            except ValueError:
                self.bubble_scale = self.layout["bubbleScale"]
            configured_reduced_motion = os.environ.get("DSH_DAFEIYU_REDUCED_MOTION")
            self.reduced_motion = (
                configured_reduced_motion == "1"
                if configured_reduced_motion is not None
                else self.layout["reducedMotion"]
            )
            configured_sound_enabled = os.environ.get("DSH_DAFEIYU_SOUND_ENABLED")
            self.sound_enabled = configured_sound_enabled != "0"
            configured_sound_states = os.environ.get("DSH_DAFEIYU_SOUND_STATES")
            self.sound_states = (
                {part.strip() for part in configured_sound_states.split(",") if part.strip()}
                if configured_sound_states is not None
                else {"SUCCESS", "ERROR"}
            )
            self.activity_level = os.environ.get("DSH_DAFEIYU_ACTIVITY_LEVEL", "normal")
            configured_bubble_mode = os.environ.get("DSH_DAFEIYU_BUBBLE_MODE")
            self.bubble_mode = (
                configured_bubble_mode
                if configured_bubble_mode in {"always", "hidden", "custom"}
                else self.layout.get("bubbleMode", "always")
            )
            configured_bubble_states = os.environ.get("DSH_DAFEIYU_BUBBLE_STATES")
            if configured_bubble_states is not None:
                self.bubble_states = [part.strip() for part in configured_bubble_states.split(",") if part.strip()]
            else:
                self.bubble_states = list(self.layout.get("bubbleStates", ["SUCCESS", "ERROR", "WAITING"]))
            # A live dialogue always expands the card; when nothing is happening
            # the card shrinks to a one-line pill that can be toggled by click.
            self.bubble_collapsed = bool(self.layout.get("bubbleCollapsed", True))
            self.bubble_press = False
            self.applied_compact: bool | None = None
            self.model = AnimationModel(manifest)
            self.frame_sources: dict[str, bytes] = {}
            for clip in self.model.clips.values():
                for frame in clip.frames:
                    if frame in self.frame_sources:
                        continue
                    try:
                        self.frame_sources[frame] = (asset_root / frame).read_bytes()
                    except OSError as error:
                        raise RuntimeError(f"Unable to load BigFish frame: {frame}") from error
            self.decoded_frames: OrderedDict[str, QPixmap] = OrderedDict()
            # Fail here, not on the first paint, when the assets are unreadable or
            # the bundled Qt has no WebP image plugin.
            for clip in self.model.clips.values():
                if clip.frames:
                    self._pixmap(clip.frames[0])
            self.decoded_frames.clear()

            self.display_state = "IDLE"
            self.status_state = "IDLE"
            self.status_message = "我在这儿等新任务哦"
            self.status_detail = "DSH · 等待下一次任务"
            self.status_deadline_ms: int | None = self._now_ms() + 4200
            # Glove cursor: native .cur handles (loaded at natural size).
            self.glove_open_h = load_native_cursor(asset_root.parent / "cursor_grab.cur")
            self.glove_closed_h = load_native_cursor(asset_root.parent / "cursor_grabbing.cur")
            self.glove = GloveCursorController(self.glove_open_h, self.glove_closed_h)
            self.overlay_state: str | None = None
            self.overlay_message = ""
            self.overlay_detail = ""
            self.overlay_deadline_ms: int | None = None
            self.task = ""
            self.tasks: list[dict[str, Any]] = []
            self.webui_url = loopback_url(os.environ.get("DSH_DAFEIYU_WEBUI_URL", "http://127.0.0.1:3080/"))
            self.open_label = os.environ.get("DSH_DAFEIYU_OPEN_LABEL", "打开 DSH")
            self.balance_summary = ""
            self.growth_reader = GrowthReader()
            self.growth_engine = GrowthEngine()
            self.growth_state = self._with_engine(self.growth_reader.refresh())
            self.growth_dialog = None
            self.growth_timer = QTimer(self)
            self.growth_timer.timeout.connect(self._refresh_growth)
            self.growth_timer.start(2000)
            self.idle_timer = QTimer(self)
            self.idle_timer.timeout.connect(self._sync_idle_layout)
            self.idle_timer.start(1000)
            # Self-directed desktop behaviour: practice running/jumping, learn
            # from tumbles, and gradually become steadier. Local rules only.
            self.learner = BehaviorLearner()
            self.motion = None
            self.motion_enabled = self.layout.get("desktopRoaming", True) and os.environ.get("DSH_DAFEIYU_MOTION", "1") != "0"
            self.pet_tilt = 0.0
            self.pet_scale_y = 1.0
            self.pet_bob = 0.0
            self.pet_facing = 1
            self.chat_open = False
            self.conversation = None
            self.conversation_serial = 0
            self.last_proactive = ""
            self.emotion_comparison = {}
            self._init_voice_runtime()
            self.online_state = LiveOnlineState()
            self.online_task = None
            self.online_attempt = None
            self.online_worker_busy = False
            self.menu_open = False
            self.chatter_enabled = self.layout.get("autonomousChatter", True)
            self.brain = NeuralBrain()
            self.emotion = EmotionModel()
            self.learning = LearningHub(self.brain, self.emotion)
            self.memory = MemoryEngine()
            self._memory_knowledge_revision = -1
            self.crawler = PoetryCrawler()
            self.crawler_busy = False
            self.crawl_result.connect(self._on_crawl_result)
            self.crawler_timer = QTimer(self)
            self.crawler_timer.timeout.connect(self._start_crawl)
            self.crawler_timer.start(6 * 60 * 60 * 1000)
            QTimer.singleShot(20000, self._start_crawl)
            self.learning_tasks = []
            self.active_learning_source = ""
            self.learning_scope = False
            self.public_learning = PublicLearningState()
            self.learning_busy = False
            self.comfort_until_ms = 0
            self.cloud_config = load_config()
            self.cloud_busy = False
            self.fatigue = 0.0
            self.last_interaction_ms = self._now_ms()
            self.pending_neural = None
            self.last_neural = None
            self.last_neural_ms = 0
            self.recent_run_results = deque(maxlen=12)
            self.recent_jump_results = deque(maxlen=12)
            self.cloud_reply.connect(self._on_cloud_reply)
            self.learning_result.connect(self._on_learning_result)
            self.learning_progress.connect(self._on_learning_progress)
            self.online_result.connect(self._on_online_result)
            self.dust: list[dict[str, Any]] = []
            self.hearts: list[dict[str, Any]] = []
            self.pet_spin_scale = 1.0
            self.blush_until_ms = 0
            self.pat_streak = 0
            self.last_pat_ms = 0
            self.spin_base_facing = 1
            self.next_motion_ms = self._now_ms() + random.randint(7000, 14000)
            self.next_talk_ms = self._now_ms() + random.randint(50000, 90000)
            self.motion_timer = QTimer(self)
            self.motion_timer.setTimerType(Qt.TimerType.PreciseTimer)
            self.motion_timer.timeout.connect(self._motion_tick)
            self.motion_timer.start(16)
            self.drag_origin: QPoint | None = None
            self.pet_origin: QPoint | None = None
            self.pet_x = 0
            self.pet_y = 0
            self.dragging = False
            self.pointer_over = False
            self.last_tick_ms = self._now_ms()
            self.fade_from_pixmap: QPixmap | None = None
            self.fade_started = 0.0
            self.fade_duration = 0.15
            self.animation_timer = QTimer(self)
            self.animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
            self.animation_timer.timeout.connect(self._tick)
            self.animation_timer.start(self._animation_interval_ms())
            self.micro_timer = QTimer(self)
            self.micro_timer.setSingleShot(True)
            self.micro_timer.timeout.connect(self._play_idle_micro)
            if not self.reduced_motion:
                self._schedule_micro()
            self.snapshot_saved = False
            self.setWindowTitle("DSH 云岚 · 成长版")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._apply_window_size()
            QTimer.singleShot(0, self._restore_visible_position)
            self.learning_timer = QTimer(self)
            self.learning_timer.timeout.connect(self._auto_learning_tick)
            self.learning_timer.start(60000)
            QTimer.singleShot(15000, self._auto_learning_tick)
            self.online_timer = QTimer(self)
            self.online_timer.timeout.connect(self._online_tick)
            self.online_timer.start(15000)
            QTimer.singleShot(12000, self._online_tick)

        def apply_message(self, message: dict[str, Any]) -> None:
            recorder.record(message)
            kind = message.get("kind")
            if kind == "shutdown":
                QApplication.quit()
                return
            previous_frame = self.model.frame
            previous_clip = self.model.active_clip_name
            if kind in {"task", "tasks"} or (
                kind in {"state", "pulse"} and str(message.get("state", "IDLE")) != "IDLE"
            ):
                self._stop_motion()
            if kind == "task":
                self.task = str(message.get("task", ""))
                self._show_status(
                    str(message.get("message", self.task)),
                    str(message.get("detail", "")),
                    self.model.base_state,
                    None if self.model.base_state in {"THINKING", "WORKING", "WAITING", "ERROR"} else 6000,
                )
            elif kind == "tasks":
                raw_tasks = message.get("tasks")
                self.tasks = raw_tasks if isinstance(raw_tasks, list) else []
                self._sync_bubble_size()
            elif kind == "config":
                self._apply_config(message)
            elif kind == "balance":
                self.balance_summary = self._brand(str(message.get("summary", ""))[:200])
                self._sync_bubble_size()
            elif kind == "alert":
                if message.get("alert") == "APPROVAL":
                    self._notify_alert("APPROVAL")
            elif kind in {"state", "pulse"}:
                previous_base_state = self.model.base_state
                state = str(message.get("state", "IDLE"))
                self.display_state = state
                if kind == "pulse":
                    ttl_ms = max(250, int(message.get("ttlMs", 1800)))
                    resume_state = str(message.get("resumeState", self.model.base_state))
                    self.model.apply_pulse(
                        state,
                        ttl_ms,
                        self._now_ms(),
                        resume_state,
                        message.get("resumeActivity"),
                        complete_clip=not self.reduced_motion,
                    )
                    ttl_ms = self.model.pulse_deadline_ms - self._now_ms()
                    self._show_status(
                        str(message.get("resumeMessage", self.LABELS.get(resume_state, resume_state))),
                        str(message.get("resumeDetail", "")),
                        resume_state,
                        None if resume_state in {"THINKING", "WORKING", "WAITING", "ERROR"} else ttl_ms + 2200,
                    )
                    self._show_overlay(
                        str(message.get("message", self.LABELS.get(state, state))),
                        str(message.get("detail", "")),
                        state,
                        ttl_ms,
                    )
                    if state in {"SUCCESS", "ERROR"}:
                        self._notify_alert(state)
                else:
                    activity = None if self.reduced_motion else message.get("activity")
                    self.model.apply_state(state, activity)
                    self.display_state = self.model.pulse_state or state
                    if self.model.pulse_state is None:
                        self._clear_overlay()
                    persistent = state in {"THINKING", "WORKING", "WAITING", "ERROR"}
                    self._show_status(
                        str(message.get("message", self.LABELS.get(state, state))),
                        str(message.get("detail", "")),
                        state,
                        None if persistent else 4200,
                    )
                if previous_base_state != self.model.base_state and not self.reduced_motion:
                    self._schedule_micro()
            self._sync_frame_transition(previous_frame, previous_clip)
            self._sync_bubble_size()
            self.update()
            if snapshot_path is not None and not self.snapshot_saved:
                QTimer.singleShot(180, self._save_snapshot)

        def _set_reduced_motion(self, enabled: bool) -> None:
            self.reduced_motion = enabled
            self.animation_timer.setInterval(self._animation_interval_ms())
            if enabled:
                self._stop_motion()
                self.micro_timer.stop()
                if not self.dragging:
                    self.model.clear_overlay()
            else:
                self._schedule_micro()

        def _apply_config(self, message: dict[str, Any]) -> None:
            """Apply a live CONFIG message without restarting the window."""
            if isinstance(message.get("openUrl"), str):
                self.webui_url = loopback_url(message["openUrl"], self.webui_url)
            if isinstance(message.get("openLabel"), str):
                self.open_label = message["openLabel"]
            scale = message.get("scale")
            if isinstance(scale, (int, float)) and not isinstance(scale, bool):
                self.scale = min(1.4, max(0.55, float(scale)))
            bubble_scale = message.get("bubbleScale")
            if isinstance(bubble_scale, (int, float)) and not isinstance(bubble_scale, bool):
                self.bubble_scale = min(1.2, max(0.8, float(bubble_scale)))
            reduced_motion = message.get("reducedMotion")
            if isinstance(reduced_motion, bool) and reduced_motion != self.reduced_motion:
                self._set_reduced_motion(reduced_motion)
            sound_enabled = message.get("soundEnabled")
            if isinstance(sound_enabled, bool):
                self.sound_enabled = sound_enabled
            sound_states = message.get("soundStates")
            if isinstance(sound_states, list):
                self.sound_states = {state for state in sound_states if isinstance(state, str)}
            activity_level = message.get("activityLevel")
            if activity_level in {"quiet", "normal", "lively"}:
                self.activity_level = activity_level
                if not self.reduced_motion:
                    self._schedule_micro()
            bubble_mode = message.get("bubbleMode")
            if bubble_mode in {"always", "hidden", "custom"}:
                self.bubble_mode = bubble_mode
            bubble_states = message.get("bubbleStates")
            if isinstance(bubble_states, list):
                self.bubble_states = [str(state) for state in bubble_states if isinstance(state, str)]
            self._sync_bubble_size()
            self._save_layout()

        def _apply_glove(self, closed: bool) -> None:
            """Closed fist while the button is held, open hand otherwise.

            Sets the cursor directly so feedback is immediate instead of
            waiting for the next WM_SETCURSOR pass.
            """
            self.glove.pressed = closed
            handle = self.glove.handle_for(closed)
            if handle:
                set_native_cursor(handle)

        def enterEvent(self, event: Any) -> None:
            self.pointer_over = True
            self._apply_glove(False)
            super().enterEvent(event)

        def leaveEvent(self, event: Any) -> None:
            self.pointer_over = False
            self.glove.pressed = False
            reset_native_cursor()
            super().leaveEvent(event)

        def _animation_interval_ms(self) -> int:
            if self.reduced_motion:
                return 40
            # Sub-frame poll: a once-per-frame timer turns one early delivery into a whole-frame hitch.
            return max(8, min(20, round(self.model.active_clip.frame_ms / 3)))

        def _rearm_animation_interval(self) -> None:
            wanted = self._animation_interval_ms()
            if self.animation_timer.interval() != wanted:
                self.animation_timer.setInterval(wanted)

        def _pixmap(self, frame: str) -> QPixmap:
            cached = self.decoded_frames.get(frame)
            if cached is not None:
                self.decoded_frames.move_to_end(frame)
                return cached
            pixmap = QPixmap()
            if not pixmap.loadFromData(self.frame_sources[frame], "WEBP"):
                raise RuntimeError(f"Unable to load BigFish frame: {frame}")
            self.decoded_frames[frame] = pixmap
            while len(self.decoded_frames) > DECODED_FRAME_CACHE:
                self.decoded_frames.popitem(last=False)
            return pixmap

        def _tick(self) -> None:
            now_ms = self._now_ms()
            elapsed_ms = max(0, now_ms - self.last_tick_ms)
            self.last_tick_ms = now_ms
            had_pulse = self.model.pulse_state is not None
            previous_frame = self.model.frame
            previous_clip = self.model.active_clip_name
            model_elapsed = 0 if self.reduced_motion else elapsed_ms
            self.model.advance(model_elapsed, now_ms)
            self._sync_frame_transition(previous_frame, previous_clip)
            if had_pulse and self.model.pulse_state is None:
                self.display_state = self.model.base_state
            if self.overlay_deadline_ms is not None and now_ms >= self.overlay_deadline_ms:
                self._clear_overlay()
            self._rearm_animation_interval()
            self.update()

        def _play_idle_micro(self) -> None:
            if self.reduced_motion:
                return
            previous_frame = self.model.frame
            previous_clip = self.model.active_clip_name
            self.model.play_idle_micro(random.randrange(max(1, len(self.model.idle_micro_clips))))
            self._sync_frame_transition(previous_frame, previous_clip)
            self.update()
            self._schedule_micro()

        def _sync_frame_transition(
            self,
            previous_frame: str,
            previous_clip: str,
            *,
            allow_fade: bool = True,
        ) -> None:
            current_frame = self.model.frame
            if current_frame == previous_frame:
                return
            # Preserve an in-progress clip fade while its new video advances.
            if allow_fade and previous_clip == self.model.active_clip_name:
                return
            duration = crossfade_duration(previous_clip, self.model.active_clip_name) if allow_fade else None
            if duration is None:
                self.fade_from_pixmap = None
                return
            self.fade_from_pixmap = (
                self._pixmap(previous_frame) if previous_frame in self.frame_sources else None
            )
            self.fade_started = time.monotonic()
            self.fade_duration = duration

        def _play_model_overlay(
            self,
            clip_name: str,
            *,
            allow_fade: bool = True,
            repaint: bool = True,
        ) -> bool:
            previous_frame = self.model.frame
            previous_clip = self.model.active_clip_name
            if not self.model.play_overlay(clip_name):
                return False
            self._sync_frame_transition(previous_frame, previous_clip, allow_fade=allow_fade)
            if repaint:
                self.update()
            return True

        def _begin_drag(self) -> None:
            if self.dragging:
                return
            self.dragging = True
            self.micro_timer.stop()
            played = self._play_model_overlay("dragging", allow_fade=False, repaint=False)
            if not played:
                self.animation_timer.stop()
                return
            clip = self.model.active_clip
            if self.reduced_motion or not clip.loop:
                self.animation_timer.stop()
            else:
                self.last_tick_ms = self._now_ms()
                self.animation_timer.start(self._animation_interval_ms())

        def _finish_drag(self) -> None:
            if not self.dragging:
                return
            now_ms = self._now_ms()
            previous_frame = self.model.frame
            previous_clip = self.model.active_clip_name
            # Expire an underlying pulse before revealing it after a long drag.
            self.model.advance(0, now_ms)
            if self.reduced_motion or not self.model.play_overlay("dragging_release"):
                self.model.clear_overlay()
            self._sync_frame_transition(previous_frame, previous_clip, allow_fade=False)
            self.dragging = False
            self.last_tick_ms = now_ms
            self.pet_tilt = 0.0
            self.pet_bob = 0.0
            self.pet_scale_y = 1.0
            self.animation_timer.start(self._animation_interval_ms())
            if not self.reduced_motion:
                self._schedule_micro()

        def _schedule_micro(self) -> None:
            if self.reduced_motion:
                self.micro_timer.stop()
                return
            lower, upper = self.model.idle_micro_interval(self.activity_level)
            self.micro_timer.start(self.model.overlay_remaining_ms + random.randint(lower, upper))

        def _bubble_visible(self) -> bool:
            if self.bubble_mode == "hidden":
                return False
            if self.bubble_mode == "always":
                return True
            if len(self.tasks) >= 2:
                return any(task.get("state") in self.bubble_states for task in self.tasks)
            state = self.overlay_state or self.status_state or self.model.base_state or "IDLE"
            return state in self.bubble_states

        def _idle_only(self) -> bool:
            """True when no task or live dialogue line is on screen."""
            if self.tasks:
                return False
            now_ms = self._now_ms()
            if self.overlay_message and (
                self.overlay_deadline_ms is None or now_ms < self.overlay_deadline_ms
            ):
                return False
            if self.status_message and (
                self.status_deadline_ms is None or now_ms < self.status_deadline_ms
            ):
                return False
            return True

        def _compact_mode(self) -> bool:
            return self._bubble_visible() and self.bubble_collapsed and self._idle_only()

        def _card_width(self) -> int:
            base = 260 if self._compact_mode() else 328
            return round(base * self.bubble_scale)

        def _toggle_bubble_collapsed(self) -> None:
            if not self._idle_only():
                return
            self.bubble_collapsed = not self.bubble_collapsed
            self._sync_bubble_size()
            self._save_layout()
            self.update()

        def _bubble_rect_contains(self, point: QPoint) -> bool:
            if not self._bubble_visible():
                return False
            x, y, width, height = self._bubble_rect()
            return x <= point.x() < x + width and y <= point.y() < y + height

        def _draw_chevron(self, painter: QPainter, center_x: int, center_y: int, collapsed: bool, s: float) -> None:
            """Small round button hinting that the card can be collapsed."""
            radius = round(11 * s)
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F1F4F8"))
            painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)
            pen = QPen(QColor("#8B96A5"), max(1, round(1.6 * s)))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            span = round(4 * s)
            tip = round(2.5 * s)
            if collapsed:
                # Points down: click to expand.
                painter.drawLine(center_x - span, center_y - tip, center_x, center_y + tip)
                painter.drawLine(center_x, center_y + tip, center_x + span, center_y - tip)
            else:
                # Points up: click to collapse.
                painter.drawLine(center_x - span, center_y + tip, center_x, center_y - tip)
                painter.drawLine(center_x, center_y - tip, center_x + span, center_y + tip)
            painter.restore()

        def _sync_bubble_size(self) -> None:
            old_size = (self.width(), self.height())
            self._apply_window_size()
            if (self.width(), self.height()) != old_size:
                self._move_to_pet(self.pet_x, self.pet_y)

        def _sync_idle_layout(self) -> None:
            """Collapse back to the pill once a temporary dialogue expires."""
            if not self._bubble_visible():
                return
            if self.applied_compact == self._compact_mode():
                return
            self._sync_bubble_size()
            self.update()

        def hideEvent(self, event):
            # Never leave a hidden pet capturing sound with invisible controls.
            if hasattr(self, 'voice_active'):
                self._voice_stop()
            super().hideEvent(event)

        # ---- self-directed desktop behaviour ---------------------------
        def _conversation_engaged(self) -> bool:
            if self.voice_active or self.realtime_active or self.realtime_starting or self.realtime_pending or (self.voice_tools is not None and self.voice_tools.isVisible()):
                return True
            if self.conversation is not None and self.conversation.isVisible():
                return (not self.conversation.compact or self.conversation.busy
                        or bool(self.conversation.pending_text)
                        or bool(self.conversation.input.text().strip())
                        or self.conversation.input.hasFocus())
            return self.chat_open

        def _can_move(self) -> bool:
            if not self.motion_enabled or self.reduced_motion or self.dragging or self.motion:
                return False
            if self.comfort_until_ms and self._now_ms() < self.comfort_until_ms:
                return False
            if self.learning.companion.snapshot()["mood"] in ("comforting", "sleepy"):
                return False
            if self._conversation_engaged() or self.menu_open:
                return False
            if not self.isVisible() or self.tasks or not self._idle_only():
                return False
            if self._bubble_visible() and not self._compact_mode():
                return False
            if self.growth_dialog is not None and self.growth_dialog.isVisible():
                return False
            if self.pointer_over:
                return False
            return True

        def _schedule_next_motion(self, delay_ms: int | None = None) -> None:
            if delay_ms is None:
                if self.activity_level == "quiet":
                    lower, upper = 30000, 60000
                elif self.activity_level == "lively":
                    lower, upper = 8000, 16000
                else:
                    lower, upper = 15000, 30000
                feeling = self.learning.companion.snapshot()
                factor = .85 if feeling["mood"] == "cheerful" else 1.25 if feeling["energy"] < .45 else 1.0
                delay_ms = int(random.randint(lower, upper) * factor)
            self.next_motion_ms = self._now_ms() + delay_ms

        def _can_talk(self) -> bool:
            # Reduced motion changes animation, not access to conversation.
            if self.voice_active or self.voice_starting or self.realtime_active or self.realtime_starting or self.realtime_pending or (self.voice_tools is not None and self.voice_tools.isVisible()):
                return False
            if not self.chatter_enabled or self.dragging or self.motion or self.menu_open:
                return False
            if self.conversation is not None and self.conversation.isVisible():
                if (self.conversation.busy or self.conversation.pending_text
                        or self.conversation.input.text().strip()
                        or self.conversation.input.hasFocus()):
                    return False
            elif self.chat_open:
                return False
            if not self.isVisible() or self.model.base_state in ("WAITING", "ERROR"):
                return False
            # A task in another DSH window does not disable direct conversation;
            # its status bubble remains untouched. Never interrupt approval/error.
            if self.growth_dialog is not None and self.growth_dialog.isVisible():
                return False
            return True

        def _talk_text(self) -> str:
            mood = self.emotion.current().get("label", "neutral")
            if mood in ("sad", "tired", "anxious", "anger"):
                return self.learning.companion.line(user_mood=mood)
            feeling = self.learning.companion.snapshot()
            if feeling["mood"] == "curious" and random.random() < .30:
                fact = self.learning.knowledge.example(sequence=random.randrange(10000))
                if fact:
                    return "我有一条小知识想和你分享：" + fact["text"]
            favorite = self.learner.favorite_spot()
            if favorite and self.learner.data["pets"] >= 5 and random.random() < .20:
                return f"你好像喜欢{favorite}。我把这点小习惯记着呢。"
            likes = self.learner.data.get("likes") or []
            if likes and random.random() < .20:
                return f"刚想起你说过喜欢{random.choice(likes)}。想聊聊这个吗？"
            name = self.learner.data.get("userName", "")
            if name and random.random() < .25:
                return f"{name}，你忙你的，我就在旁边。"
            return self.learning.companion.line(user_mood=mood)

        def _maybe_talk(self, force: bool = False) -> None:
            now_ms = self._now_ms()
            if not force and now_ms < self.next_talk_ms:
                return
            if not self._can_talk():
                self.next_talk_ms = now_ms + 15000
                return
            text = self._talk_text()
            self.last_proactive = text
            self._ensure_conversation()
            was_visible = self.conversation.isVisible()
            self._refresh_online_ui()
            self.conversation.show_proactive(text, "云岚主动搭话 · 可直接回复")
            if not was_visible:
                self._place_conversation(self.conversation)
            self.chat_open = True
            self.last_neural = (self._neural_context(), "talk")
            self.last_neural_ms = now_ms
            if self._idle_only() and not self.tasks:
                self._show_overlay(text, "云岚 · 自己跑来说话", "IDLE", 4800)
            self.learner.note_greeting()
            self._sync_bubble_size()
            self.update()
            self.next_talk_ms = now_ms + (random.randint(120000, 240000) if self._idle_only() else random.randint(300000, 600000))

        def _neural_context(self) -> dict:
            now = time.localtime()
            growth = self.growth_state if isinstance(self.growth_state, dict) else {}
            traits = growth.get("traits") if isinstance(growth.get("traits"), dict) else {}
            runs = int(self.learner.data.get("runs", 0))
            falls = int(self.learner.data.get("falls", 0))
            jumps = int(self.learner.data.get("jumps", 0))
            attempts = runs + falls
            run_ratio = (runs / attempts) if attempts else 0.5
            if self.recent_run_results:
                run_ratio = sum(self.recent_run_results) / len(self.recent_run_results)
            jump_ratio = 1.0 if jumps else 0.5
            if self.recent_jump_results:
                jump_ratio = sum(self.recent_jump_results) / len(self.recent_jump_results)
            return {
                "hour": now.tm_hour + now.tm_min / 60.0,
                "seconds_since_interaction": max(0.0, (self._now_ms() - self.last_interaction_ms) / 1000.0),
                "ongoing_work": bool(self.tasks) or self.model.base_state != "IDLE",
                "pointer_near": self.pointer_over,
                "card_open": bool(self.growth_dialog and self.growth_dialog.isVisible()),
                "chat_open": self.chat_open,
                "movement_fatigue": self.fatigue,
                "run_success_ratio": run_ratio,
                "jump_success_ratio": jump_ratio,
                "affinity": max(0.0, min(1.0, float(traits.get("affinity", 50)) / 100.0)),
            }

        def _neural_observe(self, reward: float, source: str = "outcome") -> None:
            pending = self.pending_neural
            if not pending:
                return
            context, action = pending
            self.pending_neural = None
            self.last_neural = (context, action)
            self.last_neural_ms = self._now_ms()
            self.learning.observe_action(context, action, reward, source)
            self.learning.adapt("fall" if reward < 0 else "run_ok", source="local_outcome")
            if self.growth_dialog is not None and self.growth_dialog.isVisible():
                self.growth_dialog.update_neural(self.brain.info())

        def _neural_feedback(self, reward: float) -> None:
            self.last_interaction_ms = self._now_ms()
            last = self.last_neural
            if last and self.last_neural_ms and self._now_ms() - self.last_neural_ms > 120000:
                last = None
                self.last_neural = None
            if not last:
                self._show_overlay("还没有可以评价的动作。", "先让它跑一次或跳一次", "IDLE", 3200)
                self._sync_bubble_size()
                self.update()
                return
            context, action = last
            ok = self.learning.observe_action(context, action, reward, "explicit")
            if ok:
                self.learning.adapt("feedback_yes" if reward > 0 else "feedback_no", source="local_feedback")
            self.last_neural = None
            self.last_neural_ms = 0
            if self.growth_dialog is not None and self.growth_dialog.isVisible():
                self.growth_dialog.update_neural(self.brain.info())
            self._show_overlay(
                ("知道啦，我会多练练这个动作。" if reward > 0 else "明白，我调整一下自己的动作习惯。") if ok else "这次反馈未完整保存，先不重复训练。",
                "已记录训练过程 · 可在成长卡查看" if ok else "请查看训练页的保存状态", 
                "SUCCESS" if ok else "ERROR",
                4200,
            )
            self._sync_bubble_size()
            self.update()

        def _choose_neural_action(self) -> None:
            allowed = ["rest"]
            if self._can_move():
                allowed.extend(("run", "hop"))
            if self._can_talk():
                allowed.append("talk")
            if len(allowed) == 1:
                self._schedule_next_motion()
                return
            context = self._neural_context()
            try:
                action = self.brain.choose(context, allowed, explore=0.15)
            except (ValueError, TypeError, KeyError, OverflowError):
                action = "rest"
            if action == "rest":
                self._schedule_next_motion()
                return
            if action == "talk":
                self._maybe_talk(force=True)
                return
            ok = self._start_run() if action == "run" else self._start_hop()
            if not ok:
                self._schedule_next_motion()

        def _start_autonomous_action(self) -> None:
            self._choose_neural_action()

        def _start_run(
            self,
            distance: int | None = None,
            direction: int | None = None,
            force_fall: bool | None = None,
            speed: float | None = None,
            allow_during_chat: bool = False,
        ) -> bool:
            if self.motion or not self.isVisible() or self.reduced_motion or self.dragging or self.menu_open or self.tasks or self.model.base_state != "IDLE" or (self._conversation_engaged() and not allow_during_chat):
                return False
            pet_width, _ = self._pet_size()
            geometry = self._screen_geometry_at(self.pet_x + pet_width // 2, self.pet_y)
            if geometry is None:
                return False
            direction = direction if direction in (-1, 1) else random.choice((-1, 1))
            plan = plan_run(self.pet_x, self.pet_y, geometry.x() + 8,
                            geometry.x() + geometry.width() - pet_width - 8,
                            int(distance if distance is not None else random.randint(180, 480)),
                            direction, speed or self.learner.run_speed(),
                            self.learner.fall_chance(), force_fall, random)
            if plan is None:
                return False
            plan['t0'] = self._now_ms()
            self.motion = plan
            self.pending_neural = (self._neural_context(), "run")
            self.fatigue = min(1.0, self.fatigue + 0.25)
            self.update()
            return True

        def _start_hop(self, height: int | None = None, allow_during_chat: bool = False) -> bool:
            if self.motion or not self.isVisible() or self.reduced_motion or self.dragging or self.menu_open or self.tasks or self.model.base_state != "IDLE" or (self._conversation_engaged() and not allow_during_chat):
                return False
            height = int(height if height is not None else random.randint(34, 54))
            self.motion = {
                "kind": "hop",
                "x": self.pet_x,
                "base_y": self.pet_y,
                "t0": self._now_ms(),
                "dur": random.randint(560, 760),
                "height": height,
            }
            self.pending_neural = (self._neural_context(), "hop")
            self.fatigue = min(1.0, self.fatigue + 0.12)
            return True

        def _start_spin(self, turns: int = 1, allow_during_chat: bool = False) -> bool:
            if self.motion or not self.isVisible() or self.reduced_motion or self.dragging or self.menu_open or (self._conversation_engaged() and not allow_during_chat):
                return False
            pet_width, _ = self._pet_size()
            geometry = self._screen_geometry_at(self.pet_x + pet_width // 2, self.pet_y)
            if geometry is None:
                return False
            radius = 52
            # Keep the full orbit on-screen; do not turn the pet into a flattened card.
            radius = min(
                radius,
                max(24, (self.pet_x - geometry.left()) // 2),
                max(24, (geometry.right() - self.pet_x - pet_width) // 2),
            )
            self.spin_base_facing = 1 if self.pet_facing >= 0 else -1
            self.motion = {
                "kind": "spin",
                "t0": self._now_ms(),
                "dur": 1180 + 220 * max(1, min(2, int(turns))),
                "turns": max(1, min(2, int(turns))),
                "cx": self.pet_x,
                "cy": self.pet_y,
                "radius": radius,
                "direction": random.choice((-1, 1)),
                "base_facing": self.spin_base_facing,
            }
            self.pet_spin_scale = 1.0
            self.fatigue = min(1.0, self.fatigue + 0.08)
            return True

        def _begin_fall(self, run: dict[str, Any]) -> None:
            self.pet_y = run["y"]
            # A full-body rotation would leave the narrow window and get clipped,
            # so a tumble is expressed by squashing and a small lean instead.
            self.pet_tilt = float(run["direction"] * 12)
            self.pet_scale_y = 0.56
            self.pet_bob = 3.0
            self.learner.note_fall()
            self.recent_run_results.append(0.0)
            self._neural_observe(-0.8, "outcome")
            self._show_overlay(
                random.choice((
                    "跑太快，摔了一跤…",
                    "脚滑了一下，不过没事",
                    "冲太猛，刹不住了",
                    "哎呀，平地摔",
                )),
                "我自己能爬起来",
                "WAITING",
                2400,
            )
            self._sync_bubble_size()
            self.motion = {
                "kind": "fall",
                "until": self._now_ms() + 1500,
                "direction": run["direction"],
                "x": self.pet_x,
            }
            self.update()

        def _begin_getup(self) -> None:
            self.motion = {
                "kind": "getup",
                "t0": self._now_ms(),
                "dur": 520,
                "from_tilt": self.pet_tilt,
                "from_scale": self.pet_scale_y,
            }
            self.update()

        def _finish_motion(self) -> None:
            self.motion = None
            self.pet_tilt = 0.0
            self.pet_scale_y = 1.0
            self.pet_bob = 0.0
            self.pet_spin_scale = 1.0
            self._schedule_next_motion()
            self.update()

        def _stop_motion(self) -> None:
            motion = self.motion
            if motion is None:
                return
            self.pending_neural = None
            ground = motion.get("base_y") if motion.get("kind") == "hop" else motion.get("y")
            if motion.get("kind") == "spin":
                self._move_to_pet(int(motion.get("cx", self.pet_x)), int(motion.get("cy", self.pet_y)))
                self.pet_facing = self.spin_base_facing
            elif isinstance(ground, (int, float)):
                self._move_to_pet(self.pet_x, int(ground))
            self.motion = None
            self.pet_tilt = 0.0
            self.pet_scale_y = 1.0
            self.pet_bob = 0.0
            self.pet_spin_scale = 1.0
            self._schedule_next_motion()
            self.update()

        def _spawn_dust(self, run: dict[str, Any], now_ms: int) -> None:
            if now_ms - int(run.get("last_dust", 0)) < 70:
                return
            run["last_dust"] = now_ms
            pet_x, pet_y, pet_width, pet_height = self._pet_rect()
            foot_x = pet_x + pet_width // 2 - run["direction"] * pet_width * 0.18
            foot_y = pet_y + pet_height - 6
            self.dust.append({
                "sx": self.x() + foot_x + random.uniform(-6, 6),
                "sy": self.y() + foot_y + random.uniform(-3, 3),
                "born": now_ms,
                "radius": random.uniform(2.5, 5.0),
                "vx": -run["direction"] * random.uniform(6, 14),
            })
            if len(self.dust) > 14:
                self.dust = self.dust[-14:]

        def _motion_tick(self) -> None:
            now_ms = self._now_ms()
            self.fatigue = max(0.0, self.fatigue - 0.0007)
            if self.dust:
                self.dust = [d for d in self.dust if now_ms - int(d["born"]) < 620]
            if self.hearts:
                self.hearts = [h for h in self.hearts if now_ms - int(h["born"]) < 1100]
            motion = self.motion
            if motion is None:
                self._maybe_talk()
                if now_ms >= self.next_motion_ms and self._can_move():
                    self._start_autonomous_action()
                elif self.dust:
                    self.update()
                return
            kind = motion["kind"]
            if kind == "run":
                progress = min(1.0, (now_ms - motion["t0"]) / max(1, motion["dur"]))
                if motion.get("fall") and progress >= float(motion.get("fall_at", 0.7)):
                    self._begin_fall(motion)
                    return
                pose = run_pose(motion, progress)
                self._move_to_pet(pose['x'], pose['y'])
                self.pet_facing = pose['facing']
                self.pet_tilt = pose['tilt']
                self.pet_scale_y = pose['scale_y']
                self.pet_bob = 0.0
                if pose['hop'] < 2.2:
                    self._spawn_dust(motion, now_ms)
                if progress >= 1.0:
                    self.learner.note_run(abs(motion["to"] - motion["from"]))
                    self.recent_run_results.append(1.0)
                    self._neural_observe(0.6, "outcome")
                    self._finish_motion()
                else:
                    self.update()
            elif kind == "hop":
                progress = min(1.0, (now_ms - motion["t0"]) / max(1, motion["dur"]))
                height = motion["height"] * 4 * progress * (1 - progress)
                self._move_to_pet(motion["x"], int(motion["base_y"] - height))
                self.pet_tilt = math.sin(progress * math.pi * 2) * 2.2
                self.pet_scale_y = 1.0 + 0.04 * math.sin(progress * math.pi)
                if progress >= 1.0:
                    self._move_to_pet(motion["x"], motion["base_y"])
                    self.learner.note_jump()
                    self.recent_jump_results.append(1.0)
                    self._neural_observe(0.5, "outcome")
                    self._finish_motion()
                else:
                    self.update()
            elif kind == "spin":
                progress = min(1.0, (now_ms - motion["t0"]) / max(1, motion["dur"]))
                direction = int(motion.get("direction", 1))
                angle = progress * math.tau * int(motion.get("turns", 1)) * direction
                cx = int(motion.get("cx", self.pet_x))
                cy = int(motion.get("cy", self.pet_y))
                radius = float(motion.get("radius", 52))
                # A real 360° orbit: full body, never squashed to a paper strip.
                self._move_to_pet(
                    int(cx + math.sin(angle) * radius),
                    int(cy - (1 - math.cos(angle)) * radius * 0.30),
                )
                self.pet_facing = self.spin_base_facing * (1 if math.cos(angle) >= 0 else -1)
                self.pet_tilt = direction * math.cos(angle) * 5.0
                self.pet_bob = abs(math.sin(angle)) * 2.0
                self.pet_scale_y = 1.0
                self.pet_spin_scale = 1.0
                if progress >= 1.0:
                    self._move_to_pet(cx, cy)
                    self.pet_facing = self.spin_base_facing
                    self._finish_motion()
                else:
                    self.update()
            elif kind == "fall":
                if now_ms >= int(motion["until"]):
                    self._begin_getup()
                else:
                    self.update()
            elif kind == "getup":
                progress = min(1.0, (now_ms - motion["t0"]) / max(1, motion["dur"]))
                self.pet_tilt = float(motion["from_tilt"]) * (1 - progress)
                self.pet_scale_y = 1.0 - (1.0 - float(motion["from_scale"])) * (1 - progress)
                if progress >= 1.0:
                    self.learner.note_getup()
                    self._finish_motion()
                else:
                    self.update()

        def _apply_window_size(self) -> None:
            self.applied_compact = self._compact_mode()
            pet_width = round(int(manifest["maxFrameWidth"]) * self.scale)
            pet_height = round(int(manifest["maxFrameHeight"]) * self.scale)
            if self._bubble_visible():
                bubble_width = self._card_width()
                bubble_height = self._card_height()
                self.setFixedSize(max(pet_width + 50, bubble_width + 20), pet_height + bubble_height + 34)
            else:
                self.setFixedSize(pet_width + 50, pet_height + 26)

        def _screen_geometry_at(self, x: int, y: int):
            screen = QApplication.screenAt(QPoint(x, y)) or QApplication.primaryScreen()
            if screen is None:
                return None
            return screen.availableGeometry()

        def _pet_size(self) -> tuple[int, int]:
            return (
                round(int(manifest["maxFrameWidth"]) * self.scale),
                round(int(manifest["maxFrameHeight"]) * self.scale),
            )

        def _move_to_pet(self, pet_x: int, pet_y: int) -> None:
            """Resize or move the container around the character's anchor."""
            geometry = self._screen_geometry_at(pet_x, pet_y)
            screen = (geometry.x(), geometry.y(), geometry.width(), geometry.height()) if geometry is not None else None
            window_x, window_y, self.pet_x, self.pet_y = position_window(
                (pet_x, pet_y), self._pet_size(), (self.width(), self.height()), screen,
                self._card_height() if self._bubble_visible() else 0,
            )
            self.move(window_x, window_y)
            self.update()

        def _pet_offset_x(self, pet_width: int) -> int:
            return min(max(self.pet_x - self.x(), 0), self.width() - pet_width)

        def _pet_rect(self) -> tuple[int, int, int, int]:
            pet_width, pet_height = self._pet_size()
            offset_y = min(max(self.pet_y - self.y(), 0), self.height() - pet_height)
            return self._pet_offset_x(pet_width), offset_y, pet_width, pet_height

        def _bubble_rect(self) -> tuple[int, int, int, int]:
            card_width = self._card_width()
            card_height = self._card_height()
            pet_width, _ = self._pet_size()
            pet_center_x = self._pet_offset_x(pet_width) + pet_width // 2
            margin = 14
            card_x = pet_center_x - card_width // 2
            min_x = margin
            max_x = self.width() - card_width - margin
            if max_x < min_x:
                max_x = min_x
            card_x = min(max(card_x, min_x), max_x)
            _, pet_y, _, pet_height = self._pet_rect()
            card_y = bubble_top(pet_y, pet_height, card_height, self.height())
            return card_x, card_y, card_width, card_height

        def _restore_visible_position(self) -> None:
            pet_width, pet_height = self._pet_size()
            top_offset = self.height() - pet_height - 8
            center_offset = (self.width() - pet_width) // 2
            saved_pet_x = self.layout.get("petX")
            saved_pet_y = self.layout.get("petY")
            if isinstance(saved_pet_x, int) and isinstance(saved_pet_y, int):
                pet_x, pet_y = saved_pet_x, saved_pet_y
            else:
                saved_x = self.layout.get("x")
                saved_y = self.layout.get("y")
                if isinstance(saved_x, int) and isinstance(saved_y, int):
                    # Legacy layouts stored the window position.  Recreate the
                    # pet position that the old centered layout would have had.
                    pet_x = saved_x + center_offset
                    pet_y = saved_y + top_offset
                else:
                    geometry = self._screen_geometry_at(self.x() + self.width() // 2, self.y() + self.height() // 2)
                    if geometry is None:
                        return
                    pet_x = geometry.right() - pet_width - 24
                    pet_y = geometry.bottom() - pet_height - 24
            self._move_to_pet(pet_x, pet_y)

        def _save_layout(self) -> None:
            self.layout = {
                "version": 1,
                "x": self.x(),
                "y": self.y(),
                "petX": self.pet_x,
                "petY": self.pet_y,
                "scale": self.scale,
                "bubbleScale": self.bubble_scale,
                "reducedMotion": self.reduced_motion,
                "bubbleMode": self.bubble_mode,
                "bubbleStates": self.bubble_states,
                "bubbleCollapsed": self.bubble_collapsed,
                "desktopRoaming": self.motion_enabled,
                "autonomousChatter": self.chatter_enabled,
            }
            try:
                save_layout(self.layout_path, self.layout)
            except OSError as error:
                print(f"Unable to save BigFish layout: {error}", file=sys.stderr)

        def _save_snapshot(self) -> None:
            if snapshot_path is None or self.snapshot_saved:
                return
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            self.snapshot_saved = self.grab().save(str(snapshot_path), "PNG")

        @staticmethod
        def _brand(text: str) -> str:
            """Keep the companion's new name even when the host says old copy."""
            return text.replace("大肥鱼", "云岚") if isinstance(text, str) else text

        def _show_status(self, message: str, detail: str, state: str, ttl_ms: int | None) -> None:
            self.status_message = self._brand(message)
            self.status_detail = self._brand(detail)
            self.status_state = state
            self.status_deadline_ms = None if ttl_ms is None else self._now_ms() + ttl_ms

        def _show_overlay(self, message: str, detail: str, state: str, ttl_ms: int) -> None:
            self.overlay_message = self._brand(message)
            self.overlay_detail = self._brand(detail) or self.status_detail
            self.overlay_state = state
            self.overlay_deadline_ms = self._now_ms() + ttl_ms

        def _clear_overlay(self) -> None:
            self.overlay_message = ""
            self.overlay_detail = ""
            self.overlay_state = None
            self.overlay_deadline_ms = None

        @staticmethod
        def _now_ms() -> int:
            return int(time.monotonic() * 1000)

        def _current_card(self) -> tuple[str, str, str] | None:
            now_ms = self._now_ms()
            if self.overlay_message and (
                self.overlay_deadline_ms is None or now_ms < self.overlay_deadline_ms
            ):
                return self.overlay_message, self.overlay_detail, self.overlay_state or self.status_state
            if self.status_message and (
                self.status_deadline_ms is None or now_ms < self.status_deadline_ms
            ):
                return self.status_message, self.status_detail, self.status_state
            if self.balance_summary or self.growth_state:
                return "云岚陪你待命", "DSH · 等待下一次任务", "IDLE"
            return None

        @staticmethod
        def _status_colors(state: str) -> tuple[QColor, QColor]:
            return {
                "SUCCESS": (QColor("#D9F7E4"), QColor("#12B85A")),
                "ERROR": (QColor("#FDE3E3"), QColor("#E5484D")),
                "WAITING": (QColor("#FFF0CE"), QColor("#D88A00")),
                "THINKING": (QColor("#E2ECFF"), QColor("#4C78E8")),
                "WORKING": (QColor("#DDEBFF"), QColor("#3478F6")),
                "DISCONNECTED": (QColor("#ECEEF1"), QColor("#7B818A")),
            }.get(state, (QColor("#ECEEF1"), QColor("#747A84")))

        def _draw_status_icon(self, painter: QPainter, state: str, center_x: int, center_y: int) -> None:
            background, foreground = self._status_colors(state)
            radius = 23
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)
            pen = QPen(foreground, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if state == "SUCCESS":
                painter.drawLine(center_x - 10, center_y, center_x - 3, center_y + 8)
                painter.drawLine(center_x - 3, center_y + 8, center_x + 12, center_y - 10)
            elif state == "ERROR":
                painter.drawLine(center_x - 8, center_y - 8, center_x + 8, center_y + 8)
                painter.drawLine(center_x + 8, center_y - 8, center_x - 8, center_y + 8)
            elif state == "WAITING":
                painter.drawLine(center_x, center_y - 10, center_x, center_y + 3)
                painter.setBrush(foreground)
                painter.drawEllipse(center_x - 2, center_y + 9, 4, 4)
            elif state in {"THINKING", "WORKING"}:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(foreground)
                for offset in (-9, 0, 9):
                    painter.drawEllipse(center_x + offset - 3, center_y - 3, 6, 6)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(foreground)
                painter.drawEllipse(center_x - 5, center_y - 5, 10, 10)

        def _card_height(self) -> int:
            if self._compact_mode():
                return round(44 * self.bubble_scale)
            header = 54
            balance = 26 if self.balance_summary else 4
            growth = 36 if self.growth_state else 0
            if len(self.tasks) >= 2:
                rows = min(len(self.tasks), 3)
                return round((header + growth + rows * 22 + 8) * self.bubble_scale)
            return round((header + balance + growth + 8) * self.bubble_scale)

        def _draw_card_background(
            self,
            painter: QPainter,
            card_x: int,
            card_y: int,
            card_width: int,
            card_height: int,
            corner_radius: int,
            s: float,
        ) -> None:
            # Soft two-step shadow, a hairline border and a near-white surface.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, 12))
            painter.drawRoundedRect(
                card_x, card_y + round(5 * s), card_width, card_height,
                corner_radius, corner_radius,
            )
            painter.setBrush(QColor(15, 23, 42, 7))
            painter.drawRoundedRect(
                card_x, card_y + round(2 * s), card_width, card_height,
                corner_radius, corner_radius,
            )
            painter.setPen(QPen(QColor(213, 220, 229, 220), 1))
            painter.setBrush(QColor(255, 255, 255, 252))
            painter.drawRoundedRect(
                card_x, card_y, card_width, card_height,
                corner_radius, corner_radius,
            )

        def _draw_growth_row(
            self,
            painter: QPainter,
            card_x: int,
            card_width: int,
            row_y: int,
            s: float,
        ) -> None:
            """Integrated level/XP row: divider, stage dot, value and bar."""
            state = self.growth_state
            if not state:
                return
            pad = round(18 * s)
            painter.setPen(QPen(QColor(236, 239, 243, 255), max(1, round(s))))
            painter.drawLine(
                card_x + pad, row_y,
                card_x + card_width - pad, row_y,
            )
            stage = state.get("stage") if isinstance(state.get("stage"), dict) else {}
            try:
                accent = QColor(stage.get("accent") or "#8FBFD4")
            except (TypeError, ValueError):
                accent = QColor("#8FBFD4")
            if not accent.isValid():
                accent = QColor("#8FBFD4")

            label_font = QFont("Microsoft YaHei UI")
            label_font.setPointSizeF(9.0 * max(1.0, s))
            label_font.setWeight(QFont.Weight.DemiBold)
            value_font = QFont("Microsoft YaHei UI")
            value_font.setPointSizeF(8.5 * max(1.0, s))

            text_y = row_y + round(5 * s)
            row_height = round(20 * s)
            value = f"{int(state.get('into', 0))} / {int(state.get('need', 1))}"
            value_width = QFontMetrics(value_font).horizontalAdvance(value)
            label = f"Lv.{int(state.get('level', 1))} · {stage.get('name') or '鱼卵'}"
            label_x = card_x + pad + round(10 * s)
            label_width = max(20, card_width - pad * 2 - round(10 * s) - value_width - round(10 * s))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(
                card_x + pad, text_y + row_height // 2 - round(3 * s),
                round(6 * s), round(6 * s),
            )
            painter.setFont(label_font)
            painter.setPen(QColor("#263646"))
            painter.drawText(
                label_x, text_y, label_width, row_height,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                QFontMetrics(label_font).elidedText(label, Qt.TextElideMode.ElideRight, label_width),
            )
            painter.setFont(value_font)
            painter.setPen(QColor("#7C8797"))
            painter.drawText(
                card_x + pad, text_y,
                card_width - pad * 2, row_height,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                value,
            )

            bar_y = row_y + round(27 * s)
            bar_height = max(2, round(3 * s))
            bar_width = card_width - pad * 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#E8EDF2"))
            painter.drawRoundedRect(
                QRectF(card_x + pad, bar_y, bar_width, bar_height),
                bar_height / 2, bar_height / 2,
            )
            ratio = max(0.0, min(1.0, float(state.get("ratio") or 0)))
            if ratio:
                painter.setBrush(accent)
                painter.drawRoundedRect(
                    QRectF(card_x + pad, bar_y, bar_width * ratio, bar_height),
                    bar_height / 2, bar_height / 2,
                )

        def _draw_multi_task_card(
            self,
            painter: QPainter,
            card_x: int,
            card_y: int,
            card_width: int,
            card_height: int,
            s: float,
        ) -> None:
            title_font = QFont("Microsoft YaHei UI")
            title_font.setPointSizeF(10.2 * max(1.0, s))
            title_font.setWeight(QFont.Weight.DemiBold)
            detail_font = QFont("Microsoft YaHei UI")
            detail_font.setPointSizeF(8.6 * max(1.0, s))
            text_x = card_x + round(18 * s)
            text_width = max(40, card_width - round(36 * s))
            painter.setFont(title_font)
            painter.setPen(QColor("#1F2B3A"))
            title = f"{len(self.tasks)} 个任务进行中" + (" · 显示前三项" if len(self.tasks) > 3 else "")
            painter.drawText(
                text_x,
                card_y + round(11 * s),
                text_width,
                max(12, round(23 * s)),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                QFontMetrics(title_font).elidedText(title, Qt.TextElideMode.ElideRight, text_width),
            )
            painter.setFont(detail_font)
            for index, task in enumerate(self.tasks[:3]):
                row_y = card_y + round((38 + index * 22) * s)
                state = str(task.get("state", "IDLE"))
                state_label = self.LABELS.get(state, state)
                label = self._brand(task.get("project") or task.get("task") or task.get("message") or state_label)
                line = f"{state_label} · {label}"
                _, foreground = self._status_colors(state)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(foreground)
                painter.drawEllipse(text_x, row_y + round(4 * s), round(7 * s), round(7 * s))
                painter.setPen(QColor("#75808F"))
                painter.drawText(
                    text_x + round(14 * s),
                    row_y,
                    text_width - round(14 * s),
                    max(12, round(20 * s)),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    QFontMetrics(detail_font).elidedText(line, Qt.TextElideMode.ElideRight, text_width - round(14 * s)),
                )

        SOUND_FILES = {"SUCCESS": "success.wav", "ERROR": "error.wav", "APPROVAL": "approval.wav"}

        def _draw_compact_card(
            self,
            painter: QPainter,
            card_x: int,
            card_y: int,
            card_width: int,
            card_height: int,
            s: float,
        ) -> None:
            """One-line idle pill: stage dot, level, XP value and progress."""
            radius = round(14 * s)
            self._draw_card_background(painter, card_x, card_y, card_width, card_height, radius, s)

            pad = round(14 * s)
            chevron_x = card_x + card_width - round(18 * s)
            chevron_y = card_y + card_height // 2
            text_width = max(40, card_width - pad * 2 - round(34 * s))
            state = self.growth_state
            row_height = round(30 * s)

            label_font = QFont("Microsoft YaHei UI")
            label_font.setPointSizeF(9.4 * max(1.0, s))
            label_font.setWeight(QFont.Weight.DemiBold)
            value = ""
            value_font = QFont("Microsoft YaHei UI")
            reserved = 0
            if state:
                value_font.setPointSizeF(8.4 * max(1.0, s))
                value = f"{int(state.get('into', 0))}/{int(state.get('need', 1))}"
                reserved = QFontMetrics(value_font).horizontalAdvance(value) + round(8 * s)
            dot_width = round(12 * s)
            label_width = max(20, text_width - reserved - dot_width)
            label = "云岚"
            accent = QColor("#8FBFD4")
            if state:
                stage = state.get("stage") if isinstance(state.get("stage"), dict) else {}
                label = f"Lv.{int(state.get('level', 1))} · {stage.get('name') or '鱼卵'}"
                try:
                    accent = QColor(stage.get("accent") or "#8FBFD4")
                except (TypeError, ValueError):
                    accent = QColor("#8FBFD4")
                if not accent.isValid():
                    accent = QColor("#8FBFD4")
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawEllipse(
                    card_x + pad, card_y + row_height // 2 - round(3 * s),
                    round(6 * s), round(6 * s),
                )
            painter.setFont(label_font)
            painter.setPen(QColor("#263646"))
            painter.drawText(
                card_x + pad + dot_width, card_y + round(2 * s), label_width, row_height,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                QFontMetrics(label_font).elidedText(label, Qt.TextElideMode.ElideRight, label_width),
            )

            if state:
                painter.setFont(value_font)
                painter.setPen(QColor("#7C8797"))
                painter.drawText(
                    card_x + pad, card_y + round(2 * s), text_width, row_height,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    value,
                )
                bar_y = card_y + card_height - round(9 * s)
                bar_height = max(2, round(3 * s))
                bar_width = max(20, text_width)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#E8EDF2"))
                painter.drawRoundedRect(QRectF(card_x + pad, bar_y, bar_width, bar_height), bar_height / 2, bar_height / 2)
                ratio = max(0.0, min(1.0, float(state.get("ratio") or 0)))
                if ratio:
                    painter.setBrush(accent)
                    painter.drawRoundedRect(QRectF(card_x + pad, bar_y, bar_width * ratio, bar_height), bar_height / 2, bar_height / 2)

            self._draw_chevron(painter, chevron_x, chevron_y, True, s)

        def _notify_alert(self, state: str) -> None:
            if self.sound_enabled and state in self.sound_states:
                played = False
                if sys.platform == "win32":
                    try:
                        import winsound

                        sound_name = self.SOUND_FILES.get(state, "error.wav")
                        sound_path = bundle_root() / "assets" / "sounds" / sound_name
                        winsound.PlaySound(
                            str(sound_path),
                            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                        )
                        played = True
                    except (ImportError, OSError, RuntimeError):
                        pass
                if not played:
                    try:
                        QApplication.beep()
                    except Exception:
                        pass

        def paintEvent(self, _event: Any) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # 平滑缩放：放大/缩小时插值，避免锯齿和模糊
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            card = self._current_card() if self._bubble_visible() else None
            card_x, card_y, card_width, card_height = self._bubble_rect()
            s = self.bubble_scale
            corner_radius = round(18 * s)
            pad = round(18 * s)
            compact = self._compact_mode()
            multi_task = len(self.tasks) >= 2 and self._bubble_visible()

            if compact:
                self._draw_compact_card(painter, card_x, card_y, card_width, card_height, s)
            elif multi_task:
                self._draw_card_background(painter, card_x, card_y, card_width, card_height, corner_radius, s)
                self._draw_multi_task_card(painter, card_x, card_y, card_width, card_height, s)
            elif card:
                title, detail, card_state = card
                self._draw_card_background(painter, card_x, card_y, card_width, card_height, corner_radius, s)
                icon_center_x = card_x + card_width - round(24 * s)
                icon_center_y = card_y + round(22 * s)
                if self._idle_only():
                    # Nothing to report: the same spot becomes a collapse button.
                    self._draw_chevron(painter, icon_center_x, icon_center_y, False, s)
                else:
                    painter.save()
                    painter.translate(icon_center_x, icon_center_y)
                    painter.scale(s * 0.78, s * 0.78)
                    painter.translate(-icon_center_x, -icon_center_y)
                    self._draw_status_icon(painter, card_state, icon_center_x, icon_center_y)
                    painter.restore()

                text_x = card_x + pad
                text_width = max(40, card_width - pad * 2 - round(28 * s))
                title_font = QFont("Microsoft YaHei UI")
                title_font.setPointSizeF(10.2 * max(1.0, s))
                title_font.setWeight(QFont.Weight.DemiBold)
                detail_font = QFont("Microsoft YaHei UI")
                detail_font.setPointSizeF(8.6 * max(1.0, s))
                painter.setFont(title_font)
                painter.setPen(QColor("#1F2B3A"))
                title_text = QFontMetrics(title_font).elidedText(
                    title,
                    Qt.TextElideMode.ElideRight,
                    text_width,
                )
                painter.drawText(
                    text_x,
                    card_y + round(11 * s),
                    text_width,
                    max(12, round(23 * s)),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    title_text,
                )
                painter.setFont(detail_font)
                painter.setPen(QColor("#75808F"))
                detail_text = QFontMetrics(detail_font).elidedText(
                    detail,
                    Qt.TextElideMode.ElideRight,
                    text_width,
                )
                painter.drawText(
                    text_x,
                    card_y + round(34 * s),
                    text_width,
                    max(12, round(18 * s)),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    detail_text,
                )

            if self.balance_summary and self._bubble_visible() and not compact and not multi_task:
                balance_font = QFont("Microsoft YaHei UI")
                balance_font.setPointSizeF(8.2 * max(1.0, s))
                painter.setFont(balance_font)
                painter.setPen(QColor("#8B96A5"))
                text = QFontMetrics(balance_font).elidedText(
                    self.balance_summary,
                    Qt.TextElideMode.ElideRight,
                    card_width - pad * 2,
                )
                painter.drawText(
                    card_x + pad, card_y + round(55 * s),
                    card_width - pad * 2, round(15 * s),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text,
                )

            if self.growth_state and self._bubble_visible() and not compact:
                self._draw_growth_row(painter, card_x, card_width,
                                      card_y + card_height - round(44 * s), s)

            pixmap = self._pixmap(self.model.frame)
            self._draw_motion_fx(painter, pixmap)

            fade_alpha = 1.0
            if self.fade_from_pixmap is not None and not self.fade_from_pixmap.isNull():
                fade_elapsed = time.monotonic() - self.fade_started
                if fade_elapsed < self.fade_duration:
                    fade_alpha = min(1.0, (fade_elapsed / self.fade_duration) ** 0.7)
                else:
                    self.fade_from_pixmap = None

            def draw_pet(pix: QPixmap, alpha: float) -> None:
                pw = pix.width() * self.scale
                ph = pix.height() * self.scale
                x, y, _, _ = self._pet_rect()
                y -= self.pet_bob
                painter.save()
                painter.setOpacity(alpha)
                painter.translate(x + pw / 2, y + ph)
                painter.rotate(self.pet_tilt)
                painter.scale(self.pet_facing * self.pet_spin_scale, 1.0)
                painter.scale(1.0, self.pet_scale_y)
                painter.translate(-pw / 2, -ph)
                painter.drawPixmap(QRectF(0, 0, pw, ph), pix, QRectF(0, 0, pix.width(), pix.height()))
                painter.restore()

            if fade_alpha < 1.0 and self.fade_from_pixmap is not None:
                # Keep the old frame opaque underneath so the pet never flashes transparent.
                draw_pet(self.fade_from_pixmap, 1.0)
            draw_pet(pixmap, fade_alpha)
            now_ms = self._now_ms()
            if self.hearts:
                self._draw_hearts(painter, now_ms)
            if now_ms < self.blush_until_ms:
                pet_x, pet_y, pet_width, pet_height = self._pet_rect()
                self._draw_blush(painter, pet_x, pet_y, pet_width, pet_height, now_ms)

        def _spawn_hearts(self, count: int = 3, gold: bool = False) -> None:
            now_ms = self._now_ms()
            pet_x, pet_y, pet_width, pet_height = self._pet_rect()
            for _ in range(max(1, min(8, count))):
                self.hearts.append({
                    "born": now_ms + random.randint(0, 90),
                    "sx": self.x() + pet_x + pet_width * random.uniform(0.28, 0.72),
                    "sy": self.y() + pet_y + pet_height * random.uniform(0.20, 0.52),
                    "vx": random.uniform(-14, 14),
                    "vy": random.uniform(-38, -22),
                    "size": random.uniform(4.0, 7.5),
                    "gold": gold,
                })
            if len(self.hearts) > 24:
                self.hearts = self.hearts[-24:]

        def _draw_hearts(self, painter: QPainter, now_ms: int) -> None:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            for heart in self.hearts:
                age = now_ms - int(heart["born"])
                if age < 0 or age >= 1100:
                    continue
                alpha = int(210 * (1 - age / 1100))
                x = heart["sx"] - self.x() + heart["vx"] * age / 1000
                y = heart["sy"] - self.y() + heart["vy"] * age / 1000
                size = float(heart["size"]) * (1 + age / 2600)
                color = QColor(236, 154, 88, alpha) if heart.get("gold") else QColor(235, 116, 141, alpha)
                painter.setBrush(color)
                painter.drawEllipse(QRectF(x - size, y - size, size * 1.1, size * 1.1))
                painter.drawEllipse(QRectF(x, y - size, size * 1.1, size * 1.1))
                painter.drawPolygon(QPolygonF([
                    QPointF(x - size * 0.95, y + size * 0.12),
                    QPointF(x + size * 2.02, y + size * 0.12),
                    QPointF(x + size * 0.54, y + size * 1.60),
                ]))
            painter.restore()

        def _draw_blush(self, painter: QPainter, pet_x: int, pet_y: int, pet_width: int,
                        pet_height: int, now_ms: int) -> None:
            remaining = max(0, self.blush_until_ms - now_ms)
            alpha = int(95 * min(1.0, remaining / 500))
            if alpha <= 0:
                return
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(240, 138, 154, alpha))
            radius = max(4.0, pet_width * 0.052)
            for ratio in (0.34, 0.66):
                cx = pet_x + pet_width * ratio
                cy = pet_y + pet_height * 0.37
                painter.drawEllipse(QRectF(cx - radius, cy - radius * 0.55, radius * 1.4, radius * 0.9))
            painter.restore()

        def _draw_motion_fx(self, painter: QPainter, pixmap: QPixmap) -> None:
            """Dust, speed lines and tumble stars for self-directed movement."""
            pet_x, pet_y, pet_width, pet_height = self._pet_rect()
            now_ms = self._now_ms()
            motion = self.motion or {}
            kind = motion.get("kind")
            if kind == "spin":
                painter.save()
                center_x = pet_x + pet_width * 0.5
                center_y = pet_y + pet_height * 0.48
                radius = 0.40 * pet_width
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(238, 190, 104, 165), max(1.5, 2.0 * self.scale)))
                painter.drawArc(
                    QRectF(center_x - radius, center_y - radius * 0.38, radius * 2, radius * 0.76),
                    30 * 16, 300 * 16,
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 205, 116, 210))
                phase = (now_ms % 620) / 620
                for index in range(4):
                    angle = phase * math.tau + index * math.tau / 4
                    sx = center_x + math.cos(angle) * radius
                    sy = center_y + math.sin(angle) * radius * 0.38
                    painter.drawEllipse(QRectF(sx - 2.0 * self.scale, sy - 2.0 * self.scale,
                                               4.0 * self.scale, 4.0 * self.scale))
                painter.restore()
            if self.dust:
                painter.save()
                painter.setPen(Qt.PenStyle.NoPen)
                for dust in self.dust:
                    age = now_ms - int(dust["born"])
                    if age < 0 or age >= 620:
                        continue
                    alpha = int(90 * (1 - age / 620))
                    painter.setBrush(QColor(160, 178, 196, alpha))
                    radius = float(dust["radius"]) * (1 + age / 620)
                    x = dust["sx"] - self.x() + dust["vx"] * age / 620
                    y = dust["sy"] - self.y()
                    painter.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))
                painter.restore()
            if kind in {"fall", "getup"}:
                painter.save()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 196, 92, 210))
                head_x = pet_x + pet_width * 0.5
                head_y = pet_y + pet_height * (0.18 + 0.34 * (1.0 - self.pet_scale_y))
                phase = (now_ms % 900) / 900
                for index in range(3):
                    angle = phase * math.tau + index * math.tau / 3
                    star_x = head_x + math.cos(angle) * pet_width * 0.22
                    star_y = head_y + math.sin(angle) * pet_width * 0.10 - 10 * self.scale
                    painter.drawEllipse(QRectF(star_x - 2.2 * self.scale, star_y - 2.2 * self.scale,
                                               4.4 * self.scale, 4.4 * self.scale))
                painter.restore()

        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.last_interaction_ms = self._now_ms()
                self._stop_motion()
                self._apply_glove(True)
                self.bubble_press = self._bubble_rect_contains(event.position().toPoint())
                self.drag_origin = event.globalPosition().toPoint()
                self.pet_origin = QPoint(self.pet_x, self.pet_y)
                self.dragging = False

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            if self.drag_origin is not None and self.pet_origin is not None:
                if not self.dragging and (event.globalPosition().toPoint() - self.drag_origin).manhattanLength() > 5:
                    self._begin_drag()
                delta = event.globalPosition().toPoint() - self.drag_origin
                self._move_to_pet(self.pet_origin.x() + delta.x(), self.pet_origin.y() + delta.y())
                self.pet_tilt = max(-12.0, min(12.0, delta.x() * 0.35))
                self.pet_bob = min(4.0, abs(delta.y()) * 0.018)
                self.pet_scale_y = 1.0 + min(0.05, abs(delta.y()) / 900)
                self.update()

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                if self.dragging:
                    self._finish_drag()
                    self._move_to_pet(self.pet_x, self.pet_y)
                    self._save_layout()
                elif self.bubble_press:
                    # Clicking the bubble collapses/expands it instead of poking the pet.
                    self._toggle_bubble_collapsed()
                else:
                    self._play_click_interaction(event.position().x(), event.position().y())
            self.bubble_press = False
            self.drag_origin = None
            self.pet_origin = None
            self.dragging = False
            # The button state is authoritative: restore the open hand if the
            # pointer is still over the pet, or the default arrow otherwise.
            self.glove.pressed = False
            if self.rect().contains(event.position().toPoint()):
                self._apply_glove(False)
            else:
                reset_native_cursor()

        def _play_click_interaction(self, x: float, y: float) -> None:
            pet_x, pet_y, pet_width, pet_height = self._pet_rect()
            if not (pet_x <= x < pet_x + pet_width and pet_y <= y < pet_y + pet_height):
                return
            relative_x = x - pet_x
            if self.pet_facing < 0:
                relative_x = pet_width - relative_x
            relative_y = y - pet_y
            pix = self._pixmap(self.model.frame).toImage()
            source_x = max(0, min(pix.width() - 1, int(relative_x / self.scale)))
            source_y = max(0, min(pix.height() - 1, int(relative_y / self.scale)))
            if pix.pixelColor(source_x, source_y).alpha() < 24:
                return
            nx = relative_x / max(1.0, pet_width)
            ny = relative_y / max(1.0, pet_height)
            now_ms = self._now_ms()
            if ny < 0.44 and nx < 0.58:
                spot = "head"
                if now_ms - self.last_pat_ms <= 2400:
                    self.pat_streak += 1
                else:
                    self.pat_streak = 1
                self.last_pat_ms = now_ms
                self.blush_until_ms = now_ms + 1300
                self._spawn_hearts(2 if self.pat_streak < 3 else 7)
                if self.pat_streak >= 3:
                    self.pat_streak = 0
                    self._play_interaction("head_pat", "呼噜…再摸下去我要赖在你桌面上了。")
                else:
                    self._play_interaction("head_pat", "摸摸头，舒服。")
            elif ny < 0.52 and 0.52 <= nx < 0.78:
                spot = "cheek"
                self.blush_until_ms = now_ms + 1600
                self._spawn_hearts(4)
                self._play_interaction("head_pat", "脸被蹭到了…有点痒。")
            elif nx > 0.76 and ny > 0.38:
                spot = "tail"
                if random.random() < 0.38:
                    self._play_interaction("tail", "尾巴被碰了一下，转个圈给你看。")
                    QTimer.singleShot(120, lambda: self._start_spin(1, allow_during_chat=True))
                else:
                    self._play_interaction("tail", "尾巴不是进度条啦！")
            elif 0.30 <= nx <= 0.72 and ny > 0.60:
                spot = "poke"
                self.blush_until_ms = now_ms + 900
                self._spawn_hearts(2)
                self._play_interaction("poke", "肚皮不能随便戳！")
                QTimer.singleShot(120, lambda: self._start_hop(30, allow_during_chat=True))
            else:
                spot = "poke"
                self._play_interaction("poke", "戳我干嘛，任务还在跑呢")
            self.learner.note_pet(spot)
            companion_event = "pet_cheek" if spot == "cheek" else "pet_head" if spot == "head" else "pet_tail" if spot == "tail" else "poke"
            self.learning.adapt(companion_event)
            if self.growth_dialog is not None and self.growth_dialog.isVisible():
                self.growth_dialog.update_behavior(self.learner.snapshot())
            self.update()

        def _play_interaction(self, clip_name: str, message: str) -> None:
            previous_frame, previous_clip = self.model.frame, self.model.active_clip_name
            if not self.reduced_motion and not self.model.play_interaction(clip_name, self._now_ms()):
                return
            self._sync_frame_transition(previous_frame, previous_clip)
            self._show_overlay(message, self.status_detail, self.status_state, 1800)
            self._sync_bubble_size()
            self._schedule_micro()
            self.update()

        def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.blush_until_ms = self._now_ms() + 1500
                self._spawn_hearts(4)
                self._play_interaction("head_pat", "好啦好啦，知道你喜欢我~")

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
            ok = self.memory.clear()
            if self.growth_dialog is not None:
                self.growth_dialog.update_memory(self.memory.snapshot())
            self._show_overlay("记忆引擎已清除。" if ok else "无法清除记忆引擎；未改写原文件。",
                               "云岚 · 记忆", "SUCCESS" if ok else "ERROR", 4200)
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
            if next_state == self.growth_state:
                return
            self.growth_state = next_state
            self._sync_memory()
            self._sync_bubble_size()
            if self.growth_dialog is not None:
                self.growth_dialog.update_state(self.growth_state)
                self.growth_dialog.update_neural(self.brain.info())
                self.growth_dialog.update_emotion(self.emotion.snapshot())
            self.update()

        def _forget_memory(self) -> None:
            ok = self.learner.forget_user()
            if self.growth_dialog is not None:
                self.growth_dialog.update_behavior(self.learner.snapshot())
                self.growth_dialog.report_memory_status(ok)
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
            self.growth_dialog.update_state(self.growth_state)
            self.growth_dialog.update_behavior(self.learner.snapshot())
            self.growth_dialog.update_neural(self.brain.info())
            self.growth_dialog.update_cloud(self.cloud_config)
            self.growth_dialog.update_emotion(self.emotion.snapshot())
            self.growth_dialog.update_memory(self.memory.snapshot())
            self._refresh_learning_ui()
            self.growth_dialog.open_near(self)

        def contextMenuEvent(self, event: Any) -> None:
            self._stop_motion()
            self._refresh_growth()
            menu = QMenu(self)
            menu.setStyleSheet(MENU_QSS)
            menu.setFont(QFont("Microsoft YaHei UI", 10))
            menu.setMinimumWidth(252)
            menu.setToolTipsVisible(True)
            menu.setSeparatorsCollapsible(False)
            def menu_header(text):
                holder = QWidget(menu)
                holder.setStyleSheet("background: transparent;")
                row = QHBoxLayout(holder)
                row.setContentsMargins(14, 7, 14, 2)
                label = QLabel(text, holder)
                label.setStyleSheet("color:#7A939B;font-family:'Microsoft YaHei UI';font-size:11px;font-weight:700;background:transparent;")
                row.addWidget(label)
                action = QWidgetAction(menu)
                action.setDefaultWidget(holder)
                action.setText(text)
                action.setEnabled(False)
                menu.addAction(action)
            level = self.growth_state.get("level") if self.growth_state else None
            growth_action = menu.addAction(theme_icon("growth", size=18),
                                           f"查看成长卡（Lv.{level}）" if level else "查看成长卡")
            growth_action.setObjectName("dafeiyu-growth-card")
            growth_action.setToolTip("打开成长卡：属性、回顾、权限与训练记录")
            chat_action = menu.addAction(theme_icon("chat", size=18), "和云岚聊天…")
            chat_action.setObjectName("dafeiyu-chat")
            chat_action.setToolTip("打开本地聊天窗口")
            spin_action = menu.addAction(theme_icon("spin", size=18), "转一圈")
            spin_action.setObjectName("yunlan-spin")
            feedback_yes = menu.addAction(theme_icon("heart", size=18), "喜欢刚才的动作")
            feedback_no = menu.addAction(theme_icon("heart", "#9AAFB5", 18), "这个动作不太行")
            feedback_yes.setEnabled(self.last_neural is not None)
            feedback_no.setEnabled(self.last_neural is not None)
            menu.addSeparator()
            menu_header("外观")
            size_menu = menu.addMenu("大小")
            size_menu.setStyleSheet(MENU_QSS)
            size_menu.setFont(QFont("Microsoft YaHei UI", 10))
            size_actions = {}
            for label, scale in (("迷你", 0.6), ("小", 0.8), ("标准", 1.0), ("大", 1.25)):
                action = size_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(abs(self.scale - scale) < 0.05)
                if action.isChecked():
                    action.setIcon(theme_icon("check", size=16))
                size_actions[action] = scale
            bubble_size_menu = menu.addMenu("气泡大小")
            bubble_size_menu.setStyleSheet(MENU_QSS)
            bubble_size_menu.setFont(QFont("Microsoft YaHei UI", 10))
            bubble_size_actions = {}
            for label, bubble_scale in (("小", 0.8), ("标准", 1.0), ("大", 1.2)):
                action = bubble_size_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(abs(self.bubble_scale - bubble_scale) < 0.05)
                if action.isChecked():
                    action.setIcon(theme_icon("check", size=16))
                bubble_size_actions[action] = bubble_scale
            menu_header("陪伴")
            roam_action = menu.addAction(theme_icon("check" if self.motion_enabled else "spin", size=18), "桌面漫游")
            roam_action.setCheckable(True)
            roam_action.setChecked(self.motion_enabled)
            talk_action = menu.addAction(theme_icon("check" if self.chatter_enabled else "chat", size=18), "主动搭话")
            talk_action.setCheckable(True)
            talk_action.setChecked(self.chatter_enabled)
            reduced_action = menu.addAction(theme_icon("check" if self.reduced_motion else "spark", size=18), "减少动态")
            reduced_action.setCheckable(True)
            reduced_action.setChecked(self.reduced_motion)
            collapse_action = None
            if self._bubble_visible() and self._idle_only():
                collapse_action = menu.addAction(theme_icon("history", size=18),
                                                 "展开气泡" if self._compact_mode() else "收起气泡")
            menu.addSeparator()
            open_webui_action = menu.addAction(theme_icon("arrow", size=18), self.open_label)
            hide_action = menu.addAction(theme_icon("hide", "#627B84", 18), "本次隐藏")
            exit_action = menu.addAction(theme_icon("close", "#AF4C56", 18), "本次关闭")
            self.menu_open = True
            try:
                selected = menu.exec(event.globalPos())
            finally:
                self.menu_open = False
                self._schedule_next_motion()
            if selected == growth_action:
                self._show_growth_card()
            elif selected == chat_action:
                self._open_conversation()
            elif selected == spin_action:
                self._start_spin(1, allow_during_chat=True)
            elif selected == feedback_yes:
                self._neural_feedback(1.0)
            elif selected == feedback_no:
                self._neural_feedback(-1.0)
            elif collapse_action is not None and selected == collapse_action:
                self._toggle_bubble_collapsed()
            elif selected in size_actions:
                self.scale = size_actions[selected]
                self._apply_window_size()
                self._move_to_pet(self.pet_x, self.pet_y)
                self._save_layout()
                emit_reply("settings", scale=self.scale)
            elif selected in bubble_size_actions:
                self.bubble_scale = bubble_size_actions[selected]
                self._apply_window_size()
                self._move_to_pet(self.pet_x, self.pet_y)
                self._save_layout()
                emit_reply("settings", bubbleScale=self.bubble_scale)
            elif selected == roam_action:
                self.motion_enabled = roam_action.isChecked()
                self._stop_motion()
                self._save_layout()
            elif selected == talk_action:
                self.chatter_enabled = talk_action.isChecked()
                self.next_talk_ms = self._now_ms() + 90000
                self._save_layout()
            elif selected == reduced_action:
                self._set_reduced_motion(reduced_action.isChecked())
                self._save_layout()
                emit_reply("settings", reducedMotion=self.reduced_motion)
                self.update()
            elif selected == open_webui_action:
                self._open_client()
            elif selected == hide_action:
                self.hide()
            elif selected == exit_action:
                self._save_layout()
                emit_reply("closed", reason="user")
                QApplication.quit()

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
                self.conversation_serial += 1
                dialog = ConversationDialog(self)
                dialog.local_submitted.connect(self._conversation_local)
                dialog.cloud_submitted.connect(self._conversation_cloud)
                dialog.settings_requested.connect(self._open_cloud_settings)
                dialog.voice_tools_requested.connect(self._open_voice_tools)
                dialog.online_enabled_changed.connect(self._set_online_enabled)
                dialog.online_refresh_requested.connect(lambda: self._start_online(manual=True))
                dialog.closed.connect(self._conversation_closed)
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
            self.conversation_serial += 1
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
            self.memory.ingest_behavior(self.learner.snapshot())
            memory_hits = self.memory.recall(text, 5, kinds=("preference", "habit", "diary"))
            if not memory_hits and broad_memory_query(text):
                memory_hits = self.memory.recent(3, kinds=("preference", "habit", "diary"))
            memory_answer = compose_memory_answer(
                text, self.learner.data.get("userName", ""), self.learner.data.get("likes", []),
                memory_hits)
            if memory_answer:
                reply = {"text": memory_answer, "action": None}
                meta = "记忆引擎 · 本地记忆"
            else:
                reply, meta = self.learning.companion_reply(text, reply, mood)
                if reply.get("text") in DEFAULT_REPLIES:
                    public_answer, public_meta = compose_public_answer(text, self.memory.recall(text, 5))
                    if public_answer:
                        reply = {"text": public_answer, "action": None}
                        meta = public_meta
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
            elif self.last_proactive and text.strip("。.!！ ") in ("好呀", "好啊", "继续", "然后呢", "聊聊", "愿意", "想聊"):
                reply = dict(reply, text="好呀，那我们接着聊。你想先说说今天的心情，还是问我一条基础知识？也可以说「实时动态」，看看最近校验的公开资料。", action=None)
            if mood != "neutral":
                meta += f" · 情绪线索偏{LABELS.get(mood, '情绪')}"
            if self.conversation is not None and self.conversation.isVisible():
                self.conversation.show_reply(str(reply.get("text", "嗯。")), meta)
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
            threading.Thread(target=self._cloud_worker, args=(config, text, history, self.conversation_serial, self.voice_engine.generation if self.direct_input_source == 'voice_direct' and self.voice_engine else None), daemon=True).start()

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

        def _cloud_worker(self, config, text, history=None, session_serial=None, voice_generation=None) -> None:
            try:
                result = ask_cloud(config, text, history=history)
            except Exception:
                result = {"ok": False, "error": "云端请求失败"}
            result["requestSession"] = session_serial
            result['voiceGeneration'] = voice_generation
            try:
                self.cloud_reply.emit(result)
            except RuntimeError:
                pass

        def _on_cloud_reply(self, result) -> None:
            self.cloud_busy = False
            if not isinstance(result, dict):
                result = {"ok": False, "error": "云端返回异常"}
            if result.get("requestSession", self.conversation_serial) != self.conversation_serial:
                return  # Closed dialogue: never leak its late reply into a new one.
            active = self.conversation is not None and self.conversation.isVisible()
            if result.get("ok"):
                host = urllib.parse.urlsplit(str(result.get("endpoint", ""))).hostname or "云端"
                text = str(result.get("text", ""))[:1200]
                if active:
                    self.conversation.show_reply(text, f"云端 · {host}")
                    self._speak_voice_reply(text, result.get('voiceGeneration'))
                else:
                    self._show_overlay(text, f"云端 · {host} · 仅本次显示，不写入成长记录", "SUCCESS", 12000)
            else:
                message = str(result.get("error", "云端请求失败"))[:300]
                if active:
                    self.conversation.show_reply(message, "云端", True)
                else:
                    self._show_overlay(message, "没有自动重试；如仍需要，请重新确认发送", "ERROR", 6000)
            if not active:
                self._sync_bubble_size()
                self.update()

        def _rollback_neural(self) -> None:
            ok = self.learning.control("rollback")
            if self.growth_dialog is not None:
                self.growth_dialog.update_neural(self.brain.info())
            self._show_overlay("神经网络已回滚到上一版。" if ok else "没有可回滚的模型版本。",
                               "云岚 · 本地学习", "SUCCESS" if ok else "ERROR", 4200)
            self._sync_bubble_size()
            self.update()

        def _reset_neural(self) -> None:
            ok = self.learning.control("reset")
            if self.growth_dialog is not None:
                self.growth_dialog.update_neural(self.brain.info())
            self._show_overlay("神经网络已重置。" if ok else "无法重置网络；请检查文件状态。",
                               "云岚 · 本地学习", "SUCCESS" if ok else "ERROR", 4200)
            self._sync_bubble_size()
            self.update()

        def _open_client(self) -> None:
            if not QDesktopServices.openUrl(QUrl(self.webui_url)):
                print("Unable to open the configured DSH client", file=sys.stderr)
                self._show_overlay("无法打开 DSH", "请确认 DSH 已安装、打开地址设置正确", "ERROR", 4000)
                self._sync_bubble_size()
                self.update()

    application = QApplication(sys.argv[:1])
    application.setQuitOnLastWindowClosed(False)
    inbox = Inbox()
    window = CompanionWindow()
    application.aboutToQuit.connect(window._shutdown_voice)
    application.aboutToQuit.connect(window.brain.close)
    application.aboutToQuit.connect(window.emotion.close)
    application.aboutToQuit.connect(window.learner.save)
    glove_filter = GloveCursorFilter(window, window.glove_open_h, window.glove_closed_h)
    application.installNativeEventFilter(glove_filter)
    inbox.message.connect(window.apply_message)
    inbox.closed.connect(application.quit)

    def read_stdin() -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = parse_message(line)
                if message.get("kind") == "ping":
                    emit_reply("pong")
                inbox.message.emit(message)
            except (ValueError, json.JSONDecodeError) as error:
                print(json.dumps({"kind": "error", "message": str(error)}), flush=True)
        inbox.closed.emit()

    reader = threading.Thread(target=read_stdin, name="dsh-bigfish-stdin", daemon=True)
    reader.start()
    window.show()
    emit_reply("ready")
    smoke_directory = os.environ.get("DSH_DAFEIYU_GROWTH_SMOKE_DIR")
    if smoke_directory and os.environ.get("DSH_DAFEIYU_GROWTH_SMOKE_PROBE") == "1":
        from smoke import install_smoke_probe
        install_smoke_probe(application, window, smoke_directory)
    code = application.exec()
    recorder.close()
    return code


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="DSH BigFish native helper")
    parser.add_argument("--headless", action="store_true", help="validate the protocol without opening a window")
    parser.add_argument("--event-log", type=Path, help="append received protocol messages to a JSONL file")
    parser.add_argument("--snapshot", type=Path, help="save one diagnostic visual frame after the first message")
    parser.add_argument("--smoke-probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_probe:
        # Explicit opt-in for maintainer smoke runs only; normal launches and a
        # leaked environment variable alone can never trigger the test probe.
        os.environ["DSH_DAFEIYU_GROWTH_SMOKE_PROBE"] = "1"
    recorder = EventRecorder(args.event_log)
    return run_headless(recorder) if args.headless else run_visual(recorder, args.snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
