"""Small, opt-in native actions, never a model/tool or desktop automation API.

The parent must supply trusted routing: only a *direct* user text/voice event may
call propose(), and only a real native confirmation-button handler may call
confirm(). Source/origin strings and public Python methods are not an OS security
boundary. Do not expose these methods to model tools, web messages, recognized
screen content, synthesized speech, or generic assistant-command dispatchers.

A proposal never executes. Every operation needs a fresh, single-use, 30-second
in-memory token and a UI click while this session is enabled. Voice cannot
confirm. No permission/token survives a new instance. Statistics are operational
counts, not training, intelligence, or proof of the system's final volume state.
Voice scores are uncalibrated recognition hints, not user identity or a correct-
transcription probability. The 0.15 floor admits proposals ONLY; even a score of
1.0 never grants execution or substitutes for a native confirmation click.
Only this module's fixed executable paths and fixed global volume keys exist;
there is no arbitrary path, argument, URL, shell, script, or keystroke interface.
"""
from __future__ import annotations

import math
import os
import re
import secrets
import subprocess
import threading
import time
import unicodedata
from collections import deque
from enum import Enum
from pathlib import Path
from types import MappingProxyType


class Action(str, Enum):
    OPEN_NOTEPAD = "open_notepad"
    OPEN_CALCULATOR = "open_calculator"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    VOLUME_MUTE = "volume_mute"


_LABELS = MappingProxyType({
    Action.OPEN_NOTEPAD: "打开记事本",
    Action.OPEN_CALCULATOR: "打开计算器",
    Action.VOLUME_UP: "调高系统音量一格（全局）",
    Action.VOLUME_DOWN: "调低系统音量一格（全局）",
    Action.VOLUME_MUTE: "切换系统静音（全局，可能恢复声音）",
})
_PHRASES = MappingProxyType({
    "打开记事本": Action.OPEN_NOTEPAD,
    "打开计算器": Action.OPEN_CALCULATOR,
    "调高音量": Action.VOLUME_UP,
    "调低音量": Action.VOLUME_DOWN,
    "切换静音": Action.VOLUME_MUTE,
})
_SOURCES = frozenset(("typed_direct", "voice_direct"))
# Do not normalize Unicode or strip arbitrary punctuation. In particular, a
# question mark, quote, newline, shell symbol, or invisible character is not
# punctuation that may be removed to turn a sentence into an instruction.
_GRAMMAR = re.compile(
    r"(?:小云岚|云岚)[ ]*(?:[，,、:：][ ]*)?(?:请[ ]*)?"
    r"(?P<phrase>打开记事本|打开计算器|调高音量|调低音量|切换静音|紧急停止)"
    r"[ ]*[。.!！]?"
)
_UNSAFE_SYMBOLS = frozenset("\"'`“”‘’「」『』《》<>\\/|&;$%^*()（）[]{}=+~#@?？")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_BACKEND_LOCK = threading.Lock()
_TTL = 30.0
# Recognition acceptance only, never authorization or speaker verification.
VOICE_PROPOSAL_FLOOR = 0.15


def _windows_root() -> Path:
    """Get the OS root, not an arbitrary SystemRoot supplied by caller text/env."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32.dll", winmode=0x00000800)
    get_root = kernel32.GetWindowsDirectoryW
    get_root.argtypes = (wintypes.LPWSTR, wintypes.UINT)
    get_root.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_root(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError("unavailable_system_root")
    root = Path(buffer.value)
    if (not root.is_absolute() or not re.fullmatch(r"[A-Za-z]:", root.drive)
            or root.root != "\\"):
        raise OSError("invalid_system_root")
    resolved = root.resolve(strict=True)
    if os.path.normcase(str(root)) != os.path.normcase(str(resolved)):
        raise OSError("redirected_system_root")
    environmental_root = os.environ.get("SystemRoot")
    if environmental_root:
        supplied = Path(environmental_root)
        if (not supplied.is_absolute()
                or os.path.normcase(str(supplied.resolve(strict=True)))
                != os.path.normcase(str(resolved))):
            raise OSError("invalid_system_root")
    return resolved


def _execute_windows_action(action: Action) -> bool:
    """Trusted backend: only Action enum values, never commands from any input.

    A True result means launch/input dispatch succeeded, not that a window opened
    or a particular volume/mute state was observed. No elevation or fallback.
    """
    if type(action) is not Action or os.name != "nt":
        return False
    try:
        with _BACKEND_LOCK:
            if action in (Action.OPEN_NOTEPAD, Action.OPEN_CALCULATOR):
                root = _windows_root()
                basename = ("notepad.exe" if action is Action.OPEN_NOTEPAD
                            else "calc.exe")
                expected = root / "System32" / basename
                resolved = expected.resolve(strict=True)
                if (not resolved.is_file() or not resolved.is_absolute()
                        or os.path.normcase(str(resolved))
                        != os.path.normcase(str(expected))):
                    return False
                subprocess.Popen(
                    [str(resolved)], shell=False, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=True, cwd=str(root / "System32"),
                )
                return True
            # These keys are global. The fixed UI label must say so. Mute is a
            # toggle; never claim this establishes a particular mute state.
            import ctypes
            from ctypes import wintypes

            key = {
                Action.VOLUME_UP: 0xAF,
                Action.VOLUME_DOWN: 0xAE,
                Action.VOLUME_MUTE: 0xAD,
            }.get(action)
            if key is None:
                return False
            user32 = ctypes.WinDLL("user32.dll", winmode=0x00000800)
            send_key = user32.keybd_event
            send_key.argtypes = (wintypes.BYTE, wintypes.BYTE, wintypes.DWORD,
                                 ctypes.c_size_t)
            send_key.restype = None
            try:
                send_key(key, 0, 0, 0)
            finally:
                send_key(key, 0, 0x0002, 0)
            return True
    except Exception:
        # Never leak a path, OS exception, environment value, or input into audit.
        return False


class ComputerController:
    """Session-scoped proposal/physical-UI-confirmation gate.

    ``clock`` is a trusted injectable callable returning seconds; ``expiresAt``
    uses that clock (Unix seconds by default). The default also uses monotonic
    deadlines, so a wall-clock change cannot extend a token. A custom clock must
    be finite; moving it backwards invalidates any outstanding token.
    ``executor`` is a trusted callable accepting Action ONLY and returning bool.
    Tests must inject it instead of performing actual desktop operations.
    """

    def __init__(self, clock=None, executor=None):
        if clock is not None and not callable(clock):
            raise TypeError("clock_must_be_callable")
        if executor is not None and not callable(executor):
            raise TypeError("executor_must_be_callable")
        self._clock = time.time if clock is None else clock
        self._steady_clock = time.monotonic if clock is None else clock
        self._executor = _execute_windows_action if executor is None else executor
        self._lock = threading.RLock()
        self._enabled = False
        self._closed = False
        self._generation = 0
        self._pending = None
        self._last_at = None
        self._recent = deque(maxlen=50)
        self._counts = dict.fromkeys((
            "requests", "blocked", "executed", "failed", "cancelled",
            "expired", "emergency",
        ), 0)

    def _now(self):
        at, steady = self._clock(), self._steady_clock()
        if (type(at) not in (int, float) or type(steady) not in (int, float)
                or not math.isfinite(at) or not math.isfinite(steady)):
            raise ValueError("invalid_clock")
        at, steady = float(at), float(steady)
        if self._last_at is not None and at < self._last_at:
            self._invalidate(at, "expired", "clock_changed")
        self._last_at = at
        if self._pending and (at >= self._pending["expiresAt"]
                              or steady >= self._pending["deadline"]):
            self._invalidate(at, "expired", "expired")
        return at, steady

    def _event(self, at, event, reason, action=None, source="none"):
        self._recent.append({
            "at": at, "event": event, "reason": reason,
            "action": action.value if type(action) is Action else "none",
            "source": source if source in _SOURCES else "none",
        })

    def _invalidate(self, at, event="cancelled", reason="cancelled"):
        self._generation += 1
        if self._pending is not None:
            action = self._pending["action"]
            self._pending = None
            self._counts[event] += 1
            self._event(at, event, reason, action)

    def _blocked(self, at, reason, action=None, source="none"):
        self._counts["blocked"] += 1
        self._event(at, "blocked", reason, action, source)
        return self._result("blocked", reason)

    @staticmethod
    def _result(status, reason, pending=None):
        return {
            "status": status, "reason": reason,
            "token": pending["token"] if pending else None,
            "action": pending["action"].value if pending else None,
            "label": _LABELS[pending["action"]] if pending else None,
            "expiresAt": pending["expiresAt"] if pending else None,
        }

    def set_enabled(self, enabled):
        """Call only from the parent's explicit native permission control."""
        with self._lock:
            at, _ = self._now()
            if type(enabled) is not bool:
                return self._blocked(at, "invalid_permission")
            if self._closed:
                return self._blocked(at, "session_closed")
            if not enabled:
                self._invalidate(at, reason="disabled")
            self._enabled = enabled
            self._event(at, "permission", "enabled" if enabled else "disabled")
            return self._result("enabled" if enabled else "disabled", "permission_changed")

    def propose(self, text, source, confidence=1.0):
        """Parse a complete direct command; never execute or learn from it.

        Valid grammar: 云岚/小云岚, optional comma/colon and 请, then exactly
        打开记事本 / 打开计算器 / 调高音量 / 调低音量 / 切换静音 / 紧急停止.
        Only spaces and a single safe sentence terminator may surround it.
        A direct emergency stop works while disabled and at any confidence.
        Ordinary voice scores below 0.15 are blocked; scores at/above it only
        admit a proposal. This uncalibrated score is not identity or probability.
        """
        with self._lock:
            at, steady = self._now()
            self._counts["requests"] += 1
            if type(source) is not str or source not in _SOURCES:
                return self._blocked(at, "untrusted_source")
            if type(text) is not str:
                return self._blocked(at, "invalid_text", source=source)
            if len(text) > 80:
                return self._blocked(at, "text_too_long", source=source)
            if any(unicodedata.category(c).startswith("C")
                   or c in _UNSAFE_SYMBOLS for c in text):
                return self._blocked(at, "unsafe_text", source=source)
            match = _GRAMMAR.fullmatch(text.strip(" "))
            if match is None:
                if "云岚" in text:
                    return self._blocked(at, "unsupported_command", source=source)
                self._event(at, "ignored", "not_command", source=source)
                return self._result("ignored", "not_command")
            phrase = match.group("phrase")
            if phrase == "紧急停止":
                self._stop_locked(at)
                return self._result("ignored", "emergency_stop")
            action = _PHRASES[phrase]
            if self._closed:
                return self._blocked(at, "session_closed", action, source)
            if not self._enabled:
                return self._blocked(at, "permission_disabled", action, source)
            try:
                valid_confidence = (type(confidence) in (int, float)
                                    and math.isfinite(confidence)
                                    and 0.0 <= confidence <= 1.0)
            except (OverflowError, ValueError):
                valid_confidence = False
            if not valid_confidence:
                return self._blocked(at, "invalid_confidence", action, source)
            if source == "voice_direct" and confidence < VOICE_PROPOSAL_FLOOR:
                return self._blocked(at, "low_voice_confidence", action, source)
            self._invalidate(at, reason="superseded")
            self._pending = {
                "token": secrets.token_urlsafe(32), "action": action,
                "expiresAt": at + _TTL, "deadline": steady + _TTL,
                "generation": self._generation,
            }
            self._event(at, "pending", "awaiting_ui_click", action, source)
            return self._result("pending", "awaiting_ui_click", self._pending)

    @staticmethod
    def _valid_token(token):
        return type(token) is str and _TOKEN_PATTERN.fullmatch(token) is not None

    def confirm(self, token, origin="ui_click"):
        """Only a trusted native button click may reach this method.

        The token is consumed before calling the executor, including on failure.
        The lock spans validation, consumption, and dispatch; concurrent clicks
        cannot execute twice. This does not undo already-dispatched OS effects.
        """
        with self._lock:
            at, _ = self._now()
            if type(origin) is not str or origin != "ui_click":
                return self._blocked(at, "invalid_origin")
            if self._closed:
                return self._blocked(at, "session_closed")
            if not self._enabled:
                return self._blocked(at, "permission_disabled")
            pending = self._pending
            if (not self._valid_token(token) or pending is None
                    or not secrets.compare_digest(token, pending["token"])
                    or pending["generation"] != self._generation):
                return self._blocked(at, "invalid_token")
            self._pending = None
            self._generation += 1
            action = pending["action"]
            try:
                success = self._executor(action) is True
            except Exception:
                success = False
            status = "executed" if success else "failed"
            self._counts[status] += 1
            reason = "dispatched" if success else "backend_failed"
            self._event(at, status, reason, action)
            result = self._result(status, reason)
            result["action"] = action.value
            result["label"] = _LABELS[action]
            return result

    def cancel(self, token=None):
        """Cancel pending approval, not permission; stale UI tokens cancel nothing."""
        with self._lock:
            at, _ = self._now()
            if token is not None and (
                    not self._valid_token(token) or self._pending is None
                    or not secrets.compare_digest(token, self._pending["token"])):
                return self._blocked(at, "invalid_token")
            self._invalidate(at)
            return self._result("cancelled", "cancelled")

    def _stop_locked(self, at):
        self._enabled = False
        self._invalidate(at, reason="emergency_stop")
        self._counts["emergency"] += 1
        self._event(at, "emergency", "emergency_stop")

    def emergency_stop(self):
        """Deny-only operation: invalidate approval and disable the session."""
        with self._lock:
            at, _ = self._now()
            self._stop_locked(at)
            return self._result("stopped", "emergency_stop")

    def close(self):
        """Permanently close this instance; a new session starts disabled."""
        with self._lock:
            at, _ = self._now()
            self._enabled = False
            self._closed = True
            self._invalidate(at, reason="session_closed")
            self._event(at, "session", "session_closed")
            return self._result("closed", "session_closed")

    session_close = close

    def snapshot(self):
        """Detached, bounded, sanitized stats; no token, transcript, path, or PII.

        pending is the current 0/1 gauge. requests counts propose() calls;
        blocked counts rejected API operations. cancelled includes supersession,
        disable, stop, and close of an outstanding proposal. Nothing persists.
        """
        with self._lock:
            self._now()
            return {
                **self._counts,
                "pending": int(self._pending is not None),
                "enabled": self._enabled,
                "mode": ("closed" if self._closed else
                         "confirm_only" if self._enabled else "disabled"),
                "availableActions": [
                    {"action": action.value, "label": _LABELS[action]}
                    for action in Action
                ],
                "recent": [dict(event) for event in self._recent],
            }
