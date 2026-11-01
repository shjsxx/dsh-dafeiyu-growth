"""Hermetic realtime-runtime tests with a standalone Qt host and fake devices.

No voice_features import, VoiceEngine, QAudio hardware, network, user32, real
profile/key provider, or owner config/journal file is used. Runtime load_config,
transport, audio, clock, settings dialog and executor are entirely injected.
"""
import copy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from computer_control import ComputerController
import realtime_config as config_api
import realtime_runtime as runtime


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeTransport:
    instances = []
    order = None
    start_ok = True

    def __init__(self, callback):
        # A newly constructed real transport restarts its generation at one.
        # Session isolation must therefore use the parent's monotonic epoch.
        self.generation = 1
        self.callback = callback
        self.config = None
        self.send_ok = True
        self.start_count = self.stop_count = self.close_count = 0
        self.sent = []
        self.played = []
        self.interrupt_count = 0
        self.interrupt_tokens = []
        self.invalid_audio_items = set()
        self.is_audio_current = Mock(side_effect=lambda item: item not in self.invalid_audio_items)
        type(self).instances.append(self)

    def start(self, config):
        self.start_count += 1
        self.config = copy.deepcopy(config)
        self.order.append("transport_start")
        return self.start_ok

    def emit(self, kind, **fields):
        event = {"kind": kind, "generation": self.generation, **fields}
        self.callback(event)

    def send_audio(self, pcm):
        self.sent.append(pcm)
        return self.send_ok

    def mark_played(self, item, milliseconds):
        self.order.append("transport_mark_played")
        self.played.append((item, milliseconds))

    def interrupt(self, barge_token=None):
        self.order.append("transport_interrupt")
        self.interrupt_count += 1
        self.interrupt_tokens.append(barge_token)
        return True

    def stop(self):
        self.order.append("transport_stop")
        self.stop_count += 1

    def close(self):
        self.close_count += 1


class FakeAudio(QObject):
    instances = []
    order = None
    available = True
    start_ok = True

    def __init__(self, on_audio, on_played, on_error, parent=None):
        super().__init__(parent)
        self.on_audio, self.on_played, self.on_error = on_audio, on_played, on_error
        self.start_count = self.stop_count = self.clear_count = self.report_count = 0
        self.active = False
        self.fail_enqueue = False
        self.queued = []
        self.played_on_report = None
        self.enqueue_calls = []
        type(self).instances.append(self)

    @classmethod
    def capabilities(cls):
        return {"available": cls.available, "echo_cancellation": False}

    def start(self):
        self.order.append("audio_start")
        self.start_count += 1
        self.active = bool(self.start_ok)
        return self.active

    def capture(self, pcm):
        if self.active and self.on_audio(pcm) is not True:
            self.on_error("upload_backpressure")

    def enqueue(self, pcm, item):
        self.enqueue_calls.append((pcm, item))
        if self.fail_enqueue:
            self.on_error("playback_backpressure")
            return False
        if not self.active:
            return False
        self.queued.append((pcm, item))
        return True

    def clear_playback(self):
        # Like real audio, report measured played samples before resetting sink.
        self._report_played()
        self.order.append("audio_clear")
        self.clear_count += 1
        self.queued.clear()

    def _report_played(self):
        self.order.append("report_played")
        self.report_count += 1
        if self.played_on_report is not None:
            item, milliseconds = self.played_on_report
            self.on_played(item, milliseconds)

    def stop(self):
        self.order.append("audio_stop")
        self.stop_count += 1
        self.active = False


class FakeSettings(QDialog):
    instances = []

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config_api.public_config(config)
        type(self).instances.append(self)


class StubHost(QWidget, runtime.RealtimeRuntime):
    voice_event = Signal(dict)

    def __init__(self, clock, order):
        QWidget.__init__(self)
        self.order = order
        self.journal_rows = []
        self.audit_ok = True
        journal = Mock()
        journal.snapshot.return_value = {"status": "ready"}

        def record(kind, domain, source, status="success", metrics=None, labels=None, error=""):
            row = dict(kind=kind, domain=domain, source=source, status=status,
                       metrics=copy.deepcopy(metrics or {}), labels=copy.deepcopy(labels or {}), error=error)
            self.order.append(row["labels"].get("voice_event", "journal_record"))
            self.journal_rows.append(row)
            return len(self.journal_rows) if self.audit_ok else None

        journal.record.side_effect = record
        self.learning = SimpleNamespace(journal=journal, audit_blocked=False,
                                        observe_emotion=Mock(return_value={"mood": "happy"}),
                                        adapt=Mock(), fit=Mock(), remember=Mock(),
                                        store_memory=Mock(), memory=Mock())
        self.emotion = SimpleNamespace(compare_public=Mock(return_value={"mood": "neutral"}), fit=Mock())
        self.learner = SimpleNamespace(note_chat=Mock(), fit=Mock())
        self.executor = Mock(return_value=True)
        self.computer = ComputerController(clock=clock, executor=self.executor)
        self.computer.propose = Mock(wraps=self.computer.propose)
        self.conversation = Mock()
        self.chat = SimpleNamespace(send=Mock(), fit=Mock())
        self.memory = Mock()
        self.voice_notice = ""
        self.voice_learning = False
        self._voice_stop = Mock(side_effect=lambda: self.order.append("local_voice_stop"))
        self._ensure_conversation = Mock()
        self._place_conversation = Mock()
        self._register_safety_hotkey = Mock(return_value=True)
        self._cancel_operation = Mock()
        self._refresh_voice_tools = Mock()
        self._intercept_command = Mock()
        self._init_realtime_runtime()
        self.voice_event.connect(self._on_realtime_event, Qt.ConnectionType.QueuedConnection)


class RealtimeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.clock = FakeClock()
        self.order = []
        self.config = {**config_api.validate_config({}), "key": "hermetic-owner-key", "key_saved": True,
                       "error": None, "path": "", "_revision": "mock-revision"}
        self.addCleanup(patch.stopall)
        self.load = patch.object(runtime, "load_config", side_effect=lambda: copy.deepcopy(self.config)).start()
        patch.object(runtime, "RealtimeTransport", FakeTransport).start()
        patch.object(runtime, "RealtimeAudio", FakeAudio).start()
        patch.object(runtime, "RealtimeSettingsDialog", FakeSettings).start()
        patch.object(runtime.time, "monotonic", self.clock).start()
        patch("socket.create_connection", side_effect=AssertionError("network forbidden")).start()
        patch.object(config_api, "load_config", side_effect=AssertionError("owner config IO forbidden")).start()
        patch.object(config_api, "protect_secret", side_effect=AssertionError("DPAPI forbidden")).start()
        patch.object(config_api, "unprotect_secret", side_effect=AssertionError("DPAPI forbidden")).start()
        FakeTransport.instances = []
        FakeTransport.order = self.order
        FakeTransport.start_ok = True
        FakeAudio.instances = []
        FakeAudio.order = self.order
        FakeAudio.available = FakeAudio.start_ok = True
        FakeSettings.instances = []
        self.host = StubHost(self.clock, self.order)

    def tearDown(self):
        self.host.realtime_stat_timer.stop()
        self.host._realtime_stop("test_cleanup")
        if self.host.realtime_settings is not None:
            self.host.realtime_settings.reject()
        self.host.computer.close()
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def events(self):
        self.app.processEvents()

    def host_event(self, kind, **fields):
        """Inject native queued events with explicit current epoch by default.

        Transport.emit uses the actual runtime callback, which stamps the epoch
        captured at construction rather than whichever session is live now.
        """
        self.host.voice_event.emit({"kind": kind, "generation": self.host.realtime_generation,
                                    "runtime_epoch": self.host.realtime_epoch, **fields})

    def consent(self):
        self.host._request_realtime_start()
        self.events()
        self.assertIsNotNone(self.host.realtime_pending)
        return self.host.realtime_pending

    def grant(self):
        dialog = self.consent()
        QTest.mouseClick(dialog.headphones, Qt.MouseButton.LeftButton)
        self.assertTrue(dialog.headphones.isChecked())
        self.assertTrue(dialog.allow.isEnabled())
        QTest.mouseClick(dialog.allow, Qt.MouseButton.LeftButton)
        self.events()
        self.assertTrue(self.host.realtime_starting)
        return FakeTransport.instances[-1]

    def ready(self):
        transport = self.grant()
        transport.emit("session_ready")
        self.events()
        self.assertTrue(self.host.realtime_active)
        return transport, FakeAudio.instances[-1]

    def assert_no_automatic_learning_or_action(self):
        self.host._intercept_command.assert_not_called()
        self.host.computer.propose.assert_not_called()
        self.host.executor.assert_not_called()
        self.host.conversation.send.assert_not_called()
        self.host.chat.send.assert_not_called()
        self.host.chat.fit.assert_not_called()
        self.host.learning.observe_emotion.assert_not_called()
        self.host.learning.adapt.assert_not_called()
        self.host.learning.fit.assert_not_called()
        self.host.learning.remember.assert_not_called()
        self.host.learning.store_memory.assert_not_called()
        self.host.learning.memory.assert_not_called()
        self.host.emotion.fit.assert_not_called()
        self.host.learner.fit.assert_not_called()
        self.host.learner.note_chat.assert_not_called()
        self.host.memory.assert_not_called()

    def test_initialization_only_starts_supervision_timer_no_io_or_consent(self):
        self.assertTrue(self.host.realtime_stat_timer.isActive())
        self.assertFalse(self.host.realtime_active)
        self.assertFalse(self.host.realtime_starting)
        self.assertEqual(FakeTransport.instances, [])
        self.assertEqual(FakeAudio.instances, [])
        self.load.assert_not_called()
        self.assertEqual(self.host.journal_rows, [])

    def test_programmatic_checkbox_changes_or_enter_cannot_authorize_upload(self):
        dialog = self.consent()
        self.assertFalse(dialog.allow.isEnabled())
        self.assertEqual(FakeTransport.instances, [])
        self.assertEqual(FakeAudio.instances, [])
        dialog.headphones.setChecked(True)
        self.events()
        self.assertFalse(dialog.allow.isEnabled())
        QTest.mouseClick(dialog.allow, Qt.MouseButton.LeftButton)
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        self.events()
        self.assertEqual(FakeTransport.instances, [])
        self.assertEqual(FakeAudio.instances, [])
        self.assertFalse(self.host.realtime_starting)
        dialog.reject()
        self.assertIsNone(self.host.realtime_pending)

    def test_physical_consent_starts_transport_only_then_audit_precedes_microphone(self):
        transport = self.grant()
        self.assertEqual(FakeAudio.instances, [])
        self.assertFalse(self.host._realtime_upload(b"\0\0"))
        self.assertLess(self.order.index("rt_connect"), self.order.index("transport_start"))
        transport.emit("session_ready")
        self.assertEqual(FakeAudio.instances, [])  # queued native signal, not worker-thread UI
        self.events()
        self.assertTrue(self.host.realtime_active)
        self.assertLess(self.order.index("rt_start"), self.order.index("audio_start"))
        self.assertEqual(transport.sent, [])

    def test_target_or_any_consent_option_changed_while_prompt_open_denies_start(self):
        changes = {"endpoint": "wss://other.example/custom", "model": "other-model", "voice": "alloy",
                   "protocol": "beta", "max_tokens": 16, "session_seconds": 60, "key": "new-owner-key"}
        for field, value in changes.items():
            with self.subTest(field=field):
                original = copy.deepcopy(self.config)
                dialog = self.consent()
                self.config[field] = value
                QTest.mouseClick(dialog.headphones, Qt.MouseButton.LeftButton)
                QTest.mouseClick(dialog.allow, Qt.MouseButton.LeftButton)
                self.events()
                self.assertEqual(FakeTransport.instances, [])
                self.assertEqual(FakeAudio.instances, [])
                self.assertFalse(self.host.realtime_starting)
                self.config = original

    def test_unavailable_devices_or_unwritable_audit_never_connect(self):
        FakeAudio.available = False
        self.host._request_realtime_start()
        self.assertEqual(FakeTransport.instances, [])
        self.assertIsNone(self.host.realtime_pending)
        FakeAudio.available = True
        for blocked, status in ((True, "ready"), (False, "protected"), (False, "error")):
            with self.subTest(blocked=blocked, status=status):
                self.host.learning.audit_blocked = blocked
                self.host.learning.journal.snapshot.return_value = {"status": status}
                self.host._request_realtime_start()
                self.assertEqual(FakeTransport.instances, [])
                self.assertIsNone(self.host.realtime_pending)

    def test_rt_connect_audit_failure_denies_transport_and_rt_start_failure_denies_capture(self):
        dialog = self.consent()
        self.host.audit_ok = False
        QTest.mouseClick(dialog.headphones, Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.allow, Qt.MouseButton.LeftButton)
        self.events()
        self.assertEqual(FakeTransport.instances, [])
        self.host.audit_ok = True
        transport = self.grant()
        self.host.audit_ok = False
        transport.emit("session_ready")
        self.events()
        self.assertEqual(FakeAudio.instances, [])
        self.assertIsNone(self.host.realtime)
        self.assertEqual(transport.stop_count, 1)

    def test_old_generation_ready_is_ignored_and_duplicate_ready_cannot_open_two_mics(self):
        transport = self.grant()
        transport.emit("session_ready", generation=transport.generation - 1)
        self.events()
        self.assertEqual(FakeAudio.instances, [])
        transport.emit("session_ready")
        self.events()
        transport.emit("session_ready")
        self.events()
        self.assertEqual(len(FakeAudio.instances), 1)
        self.assertEqual(FakeAudio.instances[0].start_count, 1)

    def test_epoch_is_monotonic_and_rejects_old_transport_with_colliding_generation_one(self):
        self.assertEqual(self.host.realtime_epoch, 0)
        old, old_audio = self.ready()
        old_epoch = self.host.realtime_epoch
        self.assertEqual(old.generation, 1)
        self.host._realtime_stop()
        stopped_epoch = self.host.realtime_epoch
        self.assertGreater(stopped_epoch, old_epoch)
        current = self.grant()
        current_epoch = self.host.realtime_epoch
        self.assertEqual(current.generation, old.generation)
        self.assertGreater(current_epoch, stopped_epoch)
        # These use the old callback's captured epoch, not the current epoch.
        old.emit("session_ready")
        old.emit("disconnected")
        old.emit("transcript", role="user", text="late-old-session", final=True)
        old.emit("audio_delta", pcm=b"\0\0", item_id="old-item")
        self.events()
        self.assertIs(self.host.realtime, current)
        self.assertTrue(self.host.realtime_starting)
        self.assertEqual(len(FakeAudio.instances), 1)
        self.assertFalse(old_audio.active)
        self.host.conversation.add_message.assert_not_called()
        current.emit("session_ready")
        self.events()
        self.assertTrue(self.host.realtime_active)
        self.assertEqual(len(FakeAudio.instances), 2)
        self.assertEqual(self.host.realtime_epoch, current_epoch)
        self.assert_no_automatic_learning_or_action()

    def test_missing_old_epoch_and_duplicate_current_ready_are_rejected(self):
        transport = self.grant()
        self.host.voice_event.emit({"kind": "session_ready", "generation": transport.generation})
        self.host_event("session_ready", runtime_epoch=None)
        self.host_event("session_ready", runtime_epoch=self.host.realtime_epoch - 1)
        self.events()
        self.assertEqual(FakeAudio.instances, [])
        self.assertTrue(self.host.realtime_starting)
        self.host_event("session_ready")
        self.events()
        self.assertEqual(len(FakeAudio.instances), 1)
        self.host_event("session_ready")
        self.events()
        self.assertEqual(len(FakeAudio.instances), 1)
        self.assertEqual(sum(row["labels"].get("voice_event") == "rt_start"
                             for row in self.host.journal_rows), 1)
        # Even a matching current generation cannot make an old/missing-epoch
        # transcript, disconnect, or barge-in affect this live session.
        for epoch in (None, self.host.realtime_epoch - 1):
            self.host_event("transcript", runtime_epoch=epoch, role="user", text="stale", final=True)
            self.host_event("disconnected", runtime_epoch=epoch)
            self.host_event("speech_started", runtime_epoch=epoch, barge_token="stale-token")
        self.events()
        self.assertTrue(self.host.realtime_active)
        self.host.conversation.add_message.assert_not_called()
        self.assertEqual(FakeAudio.instances[-1].clear_count, 0)
        self.assertEqual(transport.interrupt_count, 0)

    def test_pcm_canceled_while_queued_in_qt_is_rechecked_before_audio_enqueue(self):
        transport, audio = self.ready()
        pcm = b"\0\0" * 12
        transport.emit("audio_delta", pcm=pcm, item_id="canceled-response")
        # Transport may cancel while the emitted Qt event is waiting for the
        # GUI thread. Do not replay PCM merely because its epoch still matches.
        transport.invalid_audio_items.add("canceled-response")
        self.events()
        transport.is_audio_current.assert_called_once_with("canceled-response")
        self.assertEqual(audio.enqueue_calls, [])
        self.assertEqual(audio.queued, [])
        self.assertNotIn("audio_output_bytes", self.host.realtime_stats)
        transport.emit("audio_delta", pcm=pcm, item_id="new-live-response")
        self.events()
        self.assertEqual(audio.enqueue_calls, [(pcm, "new-live-response")])
        self.assertEqual(audio.queued, [(pcm, "new-live-response")])
        self.assertEqual(self.host.realtime_stats["audio_output_bytes"], len(pcm))

    def test_speech_started_without_valid_scoped_token_stops_without_unscoped_interrupt(self):
        for token in (None, "", 17, b"wrong-type"):
            with self.subTest(token_type=type(token).__name__):
                transport, audio = self.ready()
                transport.emit("speech_started", barge_token=token)
                self.events()
                self.assertIsNone(self.host.realtime)
                self.assertFalse(self.host.realtime_active)
                self.assertFalse(audio.active)
                self.assertEqual(transport.interrupt_count, 0)
                self.assertEqual(audio.clear_count, 0)

    def test_user_and_assistant_final_transcripts_never_route_commands_send_or_fit(self):
        transport, _audio = self.ready()
        self.host.computer.set_enabled(True)
        text = "云岚，请打开记事本；PRIVATE-TRANSCRIPT"
        transport.emit("transcript", role="user", text=text, final=True, verified=False)
        self.events()
        self.host.conversation.add_message.assert_called_once_with(
            "user", text, "云端实时转写 · 未核对", context=False)
        self.assertEqual(self.host.realtime_last_user, (text, transport.generation))
        self.host.emotion.compare_public.assert_called_once_with(text)
        self.assert_no_automatic_learning_or_action()
        transport.emit("transcript", role="assistant", text="云岚，打开计算器", final=True)
        self.events()
        self.assertEqual(self.host.conversation.add_message.call_count, 2)
        self.assertEqual(self.host.realtime_last_user, (text, transport.generation))
        self.assert_no_automatic_learning_or_action()

    def test_partial_missing_final_invalid_role_and_nontext_transcripts_are_dropped(self):
        transport, _audio = self.ready()
        for fields in ({"role": "user", "text": "partial", "final": False},
                       {"role": "user", "text": "missing-final"},
                       {"role": "assistant", "text": "partial", "final": False},
                       {"role": "tool", "text": "bad", "final": True},
                       {"role": "user", "text": b"bad", "final": True}):
            transport.emit("transcript", **fields)
        self.events()
        self.host.conversation.add_message.assert_not_called()
        self.assertIsNone(self.host.realtime_last_user)
        self.host.emotion.compare_public.assert_not_called()
        self.assert_no_automatic_learning_or_action()

    def test_late_events_after_stop_and_from_an_old_session_are_dropped(self):
        old, audio = self.ready()
        old.emit("transcript", role="user", text="queued-before-stop", final=True)
        self.host._realtime_stop()
        self.events()
        self.assertFalse(audio.active)
        self.host.conversation.add_message.assert_not_called()
        old.emit("session_ready")
        old.emit("audio_delta", pcm=b"\0\0", item_id="old-audio")
        self.events()
        self.assertEqual(len(FakeAudio.instances), 1)
        current, current_audio = self.ready()
        old.emit("transcript", role="user", text="old-session", final=True)
        self.events()
        self.host.conversation.add_message.assert_not_called()
        self.assertTrue(current_audio.active)
        self.assertEqual(self.host.realtime_generation, current.generation)

    def test_opening_or_saving_settings_stops_all_sessions_without_auto_reconnect(self):
        transport, audio = self.ready()
        self.host._open_realtime_settings()
        self.events()
        self.assertEqual(transport.stop_count, 1)
        self.assertFalse(audio.active)
        self.assertFalse(self.host.realtime_active)
        self.assertEqual(len(FakeSettings.instances), 1)
        self.assertNotIn("key", FakeSettings.instances[0].config)
        FakeSettings.instances[0].accept()
        self.events()
        self.assertIsNone(self.host.realtime_settings)
        self.assertEqual(len(FakeTransport.instances), 1)
        self.assertEqual(len(FakeAudio.instances), 1)
        self.assertIn("没有自动连接", self.host.voice_notice)

    def test_missing_key_opens_settings_only_and_no_owner_config_function_is_called(self):
        self.config["key"] = ""
        self.host._request_realtime_start()
        self.events()
        self.assertEqual(len(FakeSettings.instances), 1)
        self.assertEqual(FakeTransport.instances, [])
        self.assertEqual(FakeAudio.instances, [])
        self.assertIsNone(self.host.realtime_pending)

    def test_capture_failure_playback_backpressure_and_upload_backpressure_stop(self):
        for failure in ("microphone", "playback", "upload"):
            with self.subTest(failure=failure):
                transport, audio = self.ready()
                if failure == "microphone":
                    audio.on_error("device_error")
                elif failure == "playback":
                    audio.fail_enqueue = True
                    transport.emit("audio_delta", pcm=b"\0\0", item_id="item")
                    self.events()
                else:
                    transport.send_ok = False
                    audio.capture(b"\0\0")
                self.assertFalse(audio.active)
                self.assertIsNone(self.host.realtime)
                self.assertFalse(self.host.realtime_active)
                self.assertEqual(transport.stop_count, 1)

    def test_session_limit_disconnect_and_error_stop_without_retry(self):
        for kind in ("limit", "disconnected", "error"):
            with self.subTest(kind=kind):
                transport, audio = self.ready()
                count = len(FakeTransport.instances)
                self.host.computer.set_enabled(True)
                transport.emit(kind, code="untrusted-key-or-url-must-not-be-logged")
                self.events()
                self.host._realtime_tick()
                self.assertIsNone(self.host.realtime)
                self.assertFalse(audio.active)
                self.assertEqual(len(FakeTransport.instances), count)
                self.assertFalse(self.host.computer.snapshot()["enabled"])
                self.assert_no_automatic_learning_or_action()

    def test_elapsed_session_budget_stops_starting_or_active_session_without_provider_event(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                transport = self.grant()
                if ready:
                    transport.emit("session_ready")
                    self.events()
                count = len(FakeTransport.instances)
                self.clock.now += self.config["session_seconds"]
                self.host._realtime_tick()
                self.assertIsNone(self.host.realtime)
                self.assertFalse(self.host.realtime_active)
                self.assertFalse(self.host.realtime_starting)
                self.assertEqual(transport.stop_count, 1)
                self.assertEqual(len(FakeTransport.instances), count)
                self.assertFalse(self.host.computer.snapshot()["enabled"])

    def test_start_failures_close_transport_and_never_leave_a_live_microphone(self):
        FakeTransport.start_ok = False
        dialog = self.consent()
        QTest.mouseClick(dialog.headphones, Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.allow, Qt.MouseButton.LeftButton)
        self.events()
        self.assertIsNone(self.host.realtime)
        self.assertFalse(self.host.realtime_starting)
        self.assertEqual(FakeAudio.instances, [])
        self.assertEqual(FakeTransport.instances[-1].stop_count, 1)
        FakeTransport.start_ok = True
        FakeAudio.start_ok = False
        transport = self.grant()
        transport.emit("session_ready")
        self.events()
        self.assertIsNone(self.host.realtime)
        self.assertFalse(self.host.realtime_active)
        self.assertFalse(FakeAudio.instances[-1].active)
        self.assertEqual(transport.stop_count, 1)

    def test_final_transcript_before_session_ready_is_not_accepted_for_review(self):
        transport = self.grant()
        transport.emit("transcript", role="user", text="pre-ready-text", final=True)
        self.events()
        self.host.conversation.add_message.assert_not_called()
        self.assertIsNone(self.host.realtime_last_user)
        self.assert_no_automatic_learning_or_action()

    def test_speech_started_barge_in_reports_played_then_clears_and_passes_exact_token(self):
        transport, audio = self.ready()
        audio.played_on_report = ("captured-response-item", 125)
        transport.emit("speech_started", barge_token="captured-response-revision-17")
        self.events()
        self.assertEqual(audio.clear_count, 1)
        self.assertEqual(audio.report_count, 1)
        self.assertEqual(transport.played, [("captured-response-item", 125)])
        self.assertEqual(transport.interrupt_count, 1)
        self.assertEqual(transport.interrupt_tokens, ["captured-response-revision-17"])
        self.assertLess(self.order.index("report_played"), self.order.index("transport_mark_played"))
        self.assertLess(self.order.index("transport_mark_played"), self.order.index("audio_clear"))
        self.assertLess(self.order.index("audio_clear"), self.order.index("transport_interrupt"))
        self.assertTrue(self.host.realtime_active)
        self.assert_no_automatic_learning_or_action()

    def test_external_settings_key_and_budget_changes_revoke_active_consent(self):
        for field, value in (("endpoint", "wss://other.example/custom"), ("model", "other-model"),
                             ("voice", "alloy"), ("protocol", "beta"), ("key", "replacement"),
                             ("max_tokens", 16), ("session_seconds", 60)):
            with self.subTest(field=field):
                original = copy.deepcopy(self.config)
                transport, audio = self.ready()
                self.config[field] = value
                self.host._realtime_tick()
                self.assertIsNone(self.host.realtime)
                self.assertFalse(audio.active)
                self.assertEqual(transport.stop_count, 1)
                self.config = original

    def test_usage_journal_is_fixed_labels_and_numeric_only_no_text_key_or_audio(self):
        transport, audio = self.ready()
        raw = b"RAW-PRIVATE-PCM!!"
        text = "PRIVATE-TRANSCRIPT"
        transport.emit("audio_sent", bytes=2400)
        transport.emit("audio_sent", bytes=True)
        transport.emit("audio_sent", bytes=-1)
        transport.emit("audio_delta", pcm=raw, item_id="private-item-id")
        transport.emit("transcript", role="user", text=text, final=True)
        transport.emit("response_done", usage={"input_tokens": 12, "output_tokens": 7,
                       "input_audio_tokens": 4, "output_audio_tokens": 3, "cached_tokens": 2,
                       "unknown": "PRIVATE-TRANSCRIPT", "key": "hermetic-owner-key"})
        self.events()
        audio.on_played("private-item-id", 40)
        audio.on_played("private-item-id", 40)
        audio.on_played("private-item-id", 65)
        self.clock.now += 11
        self.host._realtime_tick()
        usage_rows = [row for row in self.host.journal_rows if row["labels"].get("voice_event") == "rt_usage"]
        self.assertEqual(len(usage_rows), 1)
        self.assertEqual(usage_rows[0]["metrics"], {"audio_input_bytes": 2400,
                         "audio_output_bytes": len(raw), "characters": len(text), "played_ms": 65,
                         "input_tokens": 12, "output_tokens": 7, "input_audio_tokens": 4,
                         "output_audio_tokens": 3, "cached_tokens": 2})
        self.host._realtime_stop()
        serialized = json.dumps(self.host.journal_rows)
        for forbidden in ("PRIVATE-TRANSCRIPT", "hermetic-owner-key", "RAW-PRIVATE-PCM", "private-item-id"):
            self.assertNotIn(forbidden, serialized)
        for row in self.host.journal_rows:
            self.assertEqual(row["source"], "realtime_voice")
            self.assertTrue(set(row["labels"]) <= {"voice_event", "rt_reason"})
            self.assertIn("voice_event", row["labels"])
            if "rt_reason" in row["labels"]:
                self.assertIn(row["labels"]["rt_reason"], runtime.REALTIME_REASONS)
            self.assertTrue(all(type(value) in (int, float) for value in row["metrics"].values()))
        self.assertEqual(self.host.realtime_stats, {})

    def test_failed_usage_audit_stops_capture_and_transport(self):
        transport, audio = self.ready()
        transport.emit("audio_sent", bytes=2400)
        self.events()
        self.host.audit_ok = False
        self.clock.now += 11
        self.host._realtime_tick()
        self.assertIsNone(self.host.realtime)
        self.assertFalse(audio.active)
        self.assertEqual(transport.stop_count, 1)

    def test_interrupt_clears_playback_before_transport_cancel_without_granting_actions(self):
        transport, audio = self.ready()
        self.assertTrue(self.host._realtime_interrupt())
        self.assertLess(self.order.index("audio_clear"), self.order.index("transport_interrupt"))
        self.assertEqual(audio.clear_count, 1)
        self.assertEqual(transport.interrupt_count, 1)
        self.assert_no_automatic_learning_or_action()

    def test_local_adaptation_requires_explicit_accepted_review_of_current_latest_text(self):
        transport, _audio = self.ready()
        transport.emit("transcript", role="user", text="我今天很高兴", final=True)
        self.events()
        with patch.object(runtime.QDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.host._review_realtime_transcript()
        self.host.learning.observe_emotion.assert_not_called()
        self.host.learning.adapt.assert_not_called()
        with patch.object(runtime.QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.host._review_realtime_transcript()
        self.host.learning.observe_emotion.assert_called_once_with("我今天很高兴", "local_voice")
        self.host.learning.adapt.assert_called_once_with("chat", "happy", "local_voice")
        self.host.learner.note_chat.assert_called_once_with()
        self.assertIsNone(self.host.realtime_last_user)
        self.host._intercept_command.assert_not_called()
        self.host.executor.assert_not_called()
        self.host.chat.send.assert_not_called()
        self.host.learning.remember.assert_not_called()
        self.host.learning.store_memory.assert_not_called()
        self.assertNotIn("我今天很高兴", json.dumps(self.host.journal_rows))

    def test_review_cannot_adapt_when_latest_text_or_generation_changed_during_dialog(self):
        transport, _audio = self.ready()
        candidate = ("first-text", transport.generation)
        for change in ("text", "generation", "stop"):
            with self.subTest(change=change):
                self.host.realtime_last_user = candidate
                self.host.realtime_generation = transport.generation

                def modal_result():
                    if change == "text":
                        self.host.realtime_last_user = ("second-text", transport.generation)
                    elif change == "generation":
                        self.host.realtime_generation = transport.generation + 1
                    else:
                        self.host._realtime_stop()
                    return QDialog.DialogCode.Accepted

                with patch.object(runtime.QDialog, "exec", side_effect=modal_result):
                    self.host._review_realtime_transcript()
                self.host.learning.observe_emotion.assert_not_called()
                self.host.learning.adapt.assert_not_called()
                self.host.learner.note_chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
