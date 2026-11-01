"""Offline Qt/journal/runtime regressions with an entirely fake voice engine.

No SAPI import, microphone, speech output, system hotkey, app launch, PC input,
network, real profile, or live pet is used. Journal writes are temporary only.
The runtime is loaded under a private module name with voice_engine replaced
before import, so no test can accidentally construct the real engine.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from computer_control import Action, ComputerController
from conversation_ui import ConversationDialog
from training_journal import TrainingJournal
from voice_ui import VoiceDataPanel, VoiceSessionDialog


class FakeVoiceEngine:
    def __init__(self, callback):
        self.callback = callback
        self.generation = 1
        self.start = Mock(return_value=True)
        self.speak = Mock()
        self.interrupt = Mock()
        self.stop_count = 0
        self.close_count = 0
        self.probes = 0

    def capabilities(self):
        self.probes += 1
        return {"chinese_available": True, "tts_available": True}

    def stop(self):
        self.stop_count += 1
        self.generation += 1

    def close(self):
        self.close_count += 1
        self.stop()


_fake_voice_module = ModuleType("voice_engine")
_fake_voice_module.VoiceEngine = FakeVoiceEngine
_spec = importlib.util.spec_from_file_location(
    "_voice_runtime_features_test_only", Path(__file__).with_name("voice_runtime.py"))
_runtime = importlib.util.module_from_spec(_spec)
with patch.dict(sys.modules, {"voice_engine": _fake_voice_module}):
    _spec.loader.exec_module(_runtime)


class FakeClock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class StubJournal:
    """Explicit audit failure injection and transition ordering, no disk I/O."""
    def __init__(self, order):
        self.order = order
        self.rows = {}
        self.sequence = 0
        self.begin_ok = self.running_ok = self.finish_ok = self.record_ok = True
        self.status = "ready"

    def snapshot(self):
        return {"status": self.status, "events": list(self.rows.values()), "totals": {}}

    def begin(self, kind, domain, source, queued=False):
        self.order.append(("begin", kind, source))
        if not self.begin_ok:
            return None
        self.sequence += 1
        self.rows[self.sequence] = {"id": self.sequence, "kind": kind,
            "source": source, "status": "queued" if queued else "running"}
        return self.sequence

    def mark_running(self, event_id):
        self.order.append(("mark_running", event_id))
        row = self.rows.get(event_id)
        if not self.running_ok or row is None or row["status"] != "queued":
            return False
        row["status"] = "running"
        return True

    def finish(self, event_id, status="success", metrics=None, labels=None, error=""):
        self.order.append(("finish", event_id, status, labels))
        row = self.rows.get(event_id)
        if not self.finish_ok or row is None or row["status"] not in ("queued", "running"):
            return False
        row.update(status=status, metrics=metrics or {}, labels=labels or {})
        return True

    def record(self, kind, domain, source, status="success", metrics=None, labels=None, error=""):
        self.order.append(("record", kind, source, metrics, labels))
        if not self.record_ok:
            return None
        event_id = self.begin(kind, domain, source)
        return event_id if self.finish(event_id, status, metrics, labels, error) else None


class StubInput:
    def __init__(self):
        self.value = ""

    def text(self):
        return self.value

    def setText(self, value):
        self.value = value


class StubConversation:
    def __init__(self, host):
        self.host = host
        self.input = StubInput()
        self.busy = False
        self.pending_text = ""
        self.cloud_mode = False
        self.cloud_allowed = False
        self.submissions = []
        self.show_reply = Mock()
        self.add_system = Mock()
        self.show = Mock()

    def is_cloud_mode(self):
        return self.cloud_mode

    def session_cloud_enabled(self):
        return self.cloud_allowed

    def send(self):
        text = self.input.text()
        self.input.setText("")
        self.submissions.append((text, self.host.direct_input_source))
        self.host._intercept_command(text)


class StubHost(QWidget, _runtime.VoiceRuntime):
    voice_event = Signal(dict)

    def __init__(self):
        QWidget.__init__(self)
        self.clock = FakeClock()
        self.order = []
        self.executor = Mock(side_effect=lambda action: self.order.append(("executor", action)) or True)
        self.computer = ComputerController(clock=self.clock, executor=self.executor)
        self.learning = SimpleNamespace(journal=StubJournal(self.order), audit_blocked=False,
            observe_emotion=Mock(return_value={"mood": "neutral"}), adapt=Mock())
        self.emotion = SimpleNamespace(classify=Mock(return_value={"mood": "neutral"}),
                                       compare_public=Mock(return_value={}))
        with patch.object(_runtime, "ComputerController", return_value=self.computer):
            self._init_voice_runtime()
        self.conversation = StubConversation(self)
        self.hotkey_calls = 0
        self.refreshes = 0
        self.timer_calls = []

    def _ensure_conversation(self):
        pass

    def _place_conversation(self, dialog):
        pass

    def _refresh_learning_ui(self):
        self.refreshes += 1

    def _register_safety_hotkey(self):
        self.hotkey_calls += 1
        self.safety_hotkey = True
        return True

    def _release_safety_hotkey(self):
        # Parent added explicit release later; never reach native user32 here.
        self.safety_hotkey = False


class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])


class VoiceUiTests(QtTests):
    def setUp(self):
        self.dialog = VoiceSessionDialog()
        self.signals = {}
        for name in ("start_requested", "stop_requested", "interrupt_requested",
                     "emergency_requested", "learning_changed", "control_changed",
                     "confirm_requested", "cancel_requested"):
            self.signals[name] = Mock()
            getattr(self.dialog, name).connect(self.signals[name])

    def tearDown(self):
        self.dialog.deleteLater()

    def assert_passive(self):
        for name, callback in self.signals.items():
            callback.assert_not_called()

    def test_state_sync_and_programmatic_checkbox_changes_grant_nothing(self):
        self.assertFalse(self.dialog.learning.isChecked())
        self.assertFalse(self.dialog.control.isChecked())
        self.dialog.update_state({"active": True, "learning": True, "control": True,
            "capabilities": {"chinese_available": True, "tts_available": True}})
        self.dialog.learning.setChecked(False)
        self.dialog.control.setChecked(False)
        self.dialog.show()
        self.assert_passive()
        for label in self.dialog.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        for button in (self.dialog.confirm_button, self.dialog.start_button):
            self.assertFalse(button.autoDefault())
            self.assertFalse(button.isDefault())

    def test_checkbox_clicks_forward_exact_bool_but_programmatic_changes_do_not(self):
        self.dialog.show()
        QTest.mouseClick(self.dialog.learning, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.dialog.control, Qt.MouseButton.LeftButton)
        self.signals["learning_changed"].assert_called_once_with(True)
        self.signals["control_changed"].assert_called_once_with(True)
        self.dialog.learning.setChecked(False)
        self.dialog.control.setChecked(False)
        self.signals["learning_changed"].assert_called_once_with(True)
        self.signals["control_changed"].assert_called_once_with(True)

    def test_proposal_is_passive_and_button_emits_only_once(self):
        proposal = {"status": "pending", "token": "test-token", "label": "打开记事本"}
        self.dialog.set_proposal(proposal)
        self.assert_passive()
        self.dialog.confirm_button.click()
        self.dialog.confirm_button.click()
        self.signals["confirm_requested"].assert_called_once_with("test-token")
        self.assertFalse(self.dialog.confirm_button.isEnabled())
        self.dialog.set_proposal()
        self.dialog.confirm_button.click()
        self.signals["confirm_requested"].assert_called_once()

    def test_clear_and_close_cannot_leave_a_confirmation_token(self):
        self.dialog.set_proposal({"status": "pending", "token": "old", "label": "打开计算器"})
        self.dialog.set_proposal()
        self.dialog.confirm_button.click()
        self.signals["confirm_requested"].assert_not_called()
        self.dialog.set_proposal({"status": "pending", "token": "next", "label": "打开计算器"})
        self.dialog._closed()
        self.dialog._confirm()
        self.signals["confirm_requested"].assert_not_called()

    def test_malformed_capabilities_fail_closed_without_signal_or_sensitive_names(self):
        for caps in (None, [], ["chinese_available"], True, 1, "available", {}):
            with self.subTest(caps=caps):
                self.dialog.update_state({"capabilities": caps, "active": "true",
                                          "learning": 1, "control": "true"})
                self.assertFalse(self.dialog.start_button.isEnabled())
                self.assertFalse(self.dialog.learning.isChecked())
                self.assertFalse(self.dialog.control.isChecked())
        self.dialog.update_state({"capabilities": {"chinese_available": "true",
            "tts_available": False, "recognizers": ["PRIVATE_DEVICE_NAME"],
            "path": "C:\\PRIVATE_DEVICE_NAME"}})
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertNotIn("PRIVATE_DEVICE_NAME", self.dialog.capabilities.text())
        for malformed in ("false", 1, [True], {"available": True}, None):
            with self.subTest(tts_available=malformed):
                self.dialog.update_state({"capabilities": {"tts_available": malformed}})
                self.assertIn("播报不可用", self.dialog.capabilities.text())
        self.assert_passive()

    def test_data_panel_numeric_bounded_plain_and_never_displays_raw_fields(self):
        panel = VoiceDataPanel()
        self.addCleanup(panel.deleteLater)
        panel.update_data({"totals": {"voice_sessions": -3, "voice_segments": "999",
            "voice_spoken": True, "computer_executed": 10**30}, "events": [
            {"kind": "voice_io", "status": "observed", "started": float("inf"),
             "labels": {"voice_event": "recognized", "text": "PRIVATE_RAW_TRANSCRIPT"},
             "text": "PRIVATE_RAW_TRANSCRIPT", "token": "PRIVATE_TOKEN"}]})
        text = "\n".join(label.text() for label in panel.findChildren(QLabel))
        self.assertNotIn("PRIVATE_RAW_TRANSCRIPT", text)
        self.assertNotIn("PRIVATE_TOKEN", text)
        self.assertNotIn("999", text)
        self.assertIn("10000000000", text)
        self.assertIn("语音声学／合成模型训练：0", text)
        for label in panel.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_data_panel_ignores_malformed_nested_containers_and_unhashable_enums(self):
        panel = VoiceDataPanel()
        self.addCleanup(panel.deleteLater)
        for totals in (None, [], "invalid", True, {}):
            with self.subTest(totals=totals):
                panel.update_data({"totals": totals})
        for row in ({"kind": "voice_io", "labels": {"voice_event": []}},
                    {"kind": "computer_control", "labels": {"decision": {}}},
                    {"kind": "voice_io", "status": []},
                    {"kind": "voice_io", "status": {}},
                    {"kind": [], "labels": None}, None):
            with self.subTest(row=row):
                panel.update_data({"events": [row]})


class VoiceJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "voice-journal.json"
        self.journal = TrainingJournal(self.path)

    def record_voice(self, event, status="observed", metrics=None):
        return self.journal.record("voice_io", "general", "local_voice", status,
                                   metrics, {"voice_event": event})

    def test_voice_and_operation_counters_are_separate_from_any_model_fit(self):
        for event in ("session_start", "recognized", "spoken", "voice_interrupt", "voice_reject"):
            self.assertIsNotNone(self.record_voice(event, metrics={"weight_steps": 999,
                "trained": 1, "updates_before": 0, "updates_after": 999,
                "adaptation_passes": 99}))
        self.record_voice("recognized", status="failed")
        self.journal.record("computer_control", "action", "typed_direct", "success",
            {"weight_steps": 999, "trained": 1},
            {"computer_action": "open_notepad", "decision": "executed"})
        self.journal.record("computer_control", "action", "voice_direct", "skipped",
            labels={"decision": "blocked"})
        totals = self.journal.snapshot()["totals"]
        for key in ("voice_sessions", "voice_segments", "voice_spoken", "voice_interrupted", "voice_rejected"):
            self.assertEqual(totals[key], 1, key)
        self.assertEqual(totals["computer_proposals"], 2)
        self.assertEqual(totals["computer_executed"], 1)
        self.assertEqual(totals["computer_blocked"], 1)
        self.assertEqual(totals["weight_steps"], 0)
        self.assertEqual(totals["emotion_adaptation_passes"], 0)
        for row in self.journal.snapshot()["events"]:
            self.assertEqual(row["metrics"]["trained"], 0)
            self.assertEqual(row["metrics"]["weight_steps"], 0)
            self.assertNotIn("adaptation_passes", row["metrics"])

    def test_sanitized_audit_retains_counts_and_enum_provenance_not_raw_text(self):
        secret = "PRIVATE_TRANSCRIPT_TOKEN_PATH_DEVICE"
        self.journal.record("voice_io", "general", "local_voice", "observed",
            {"characters": 8, "confidence": .9, "raw_text": secret, "token": secret},
            {"voice_event": "recognized", "transcript": secret, "path": secret})
        self.journal.record("computer_control", "action", "voice_direct", "success",
            labels={"decision": "executed", "computer_action": "open_calculator",
                    "token": secret, "text": secret, "device": secret})
        snapshot = self.journal.snapshot()
        self.assertNotIn(secret, json.dumps(snapshot))
        self.assertNotIn(secret, self.path.read_text(encoding="utf-8"))
        self.assertEqual({row["source"] for row in snapshot["events"]}, {"local_voice", "voice_direct"})
        reader = TrainingJournal(self.path, read_only=True)
        self.assertEqual(reader.snapshot()["totals"]["voice_segments"], 1)
        self.assertEqual(reader.snapshot()["totals"]["computer_executed"], 1)
        for source in ("model", "web", "assistant", "server", "voice", "tool"):
            self.assertIsNone(self.journal.begin("computer_control", "action", source))

    def test_execution_counter_requires_success_and_executed_decision(self):
        for status, decision in (("failed", "executed"), ("cancelled", "executed"),
                                 ("observed", "executed"), ("success", "blocked"),
                                 ("success", "requested")):
            self.journal.record("computer_control", "action", "typed_direct", status,
                labels={"computer_action": "open_notepad", "decision": decision})
        totals = self.journal.snapshot()["totals"]
        self.assertEqual(totals["computer_executed"], 0)
        self.assertEqual(totals["weight_steps"], 0)


class VoiceRuntimeTests(QtTests):
    def setUp(self):
        self.host = StubHost()
        self.timer = patch.object(_runtime, "QTimer", SimpleNamespace(
            singleShot=lambda delay, callback: self.host.timer_calls.append((delay, callback))))
        self.timer.start()
        self.addCleanup(self.timer.stop)

    def tearDown(self):
        if self.host.voice_tools is not None:
            self.host.voice_tools.close()
        if self.host.voice_engine is not None:
            self.host.voice_engine.close()
        self.host.deleteLater()

    def proposal(self, source="typed_direct", text="云岚，打开记事本"):
        self.host.computer.set_enabled(True)
        self.host.direct_input_source = source
        self.host.voice_confidence = .95
        self.assertTrue(self.host._intercept_command(text))
        self.host.direct_input_source = "typed_direct"
        self.assertIsNotNone(self.host.operation_row)
        return dict(self.host.operation_row)

    def listening(self):
        self.host._open_voice_tools()
        self.host.voice_active = True
        return self.host.voice_engine.generation

    def final(self, text, confidence=.95, generation=None):
        generation = self.host.voice_engine.generation if generation is None else generation
        self.host.voice_event.emit({"kind": "final", "generation": generation,
                                    "text": text, "confidence": confidence})

    def test_open_tools_is_passive_and_never_requests_microphone_hotkey_or_permission(self):
        self.host._open_voice_tools()
        self.assertIsInstance(self.host.voice_engine, FakeVoiceEngine)
        self.host.voice_engine.start.assert_not_called()
        self.host.voice_engine.speak.assert_not_called()
        self.assertEqual(self.host.hotkey_calls, 0)
        self.assertFalse(self.host.computer.snapshot()["enabled"])
        self.host.executor.assert_not_called()
        self.assertEqual(self.host.learning.journal.rows, {})

    def test_ordinary_questions_quotes_addressed_chat_and_long_chat_bypass_pc_parser(self):
        ordinary = (
            "你好吗？", "你说过‘你好’。", '他说"今天晴天"。',
            "“云岚，打开计算器”是一个示例句子。", "云岚，你今天开心吗？",
            "小云岚，请问你喜欢什么？", "云岚，我想聊聊天。",
            "今天我想聊聊生活。" * 20,
            "云岚，" + "今天我想聊聊生活。" * 20,
        )
        for text in ordinary:
            with self.subTest(text=text):
                self.assertIs(self.host._intercept_command(text), False)
        self.assertEqual(self.host.computer.snapshot()["requests"], 0)
        self.assertEqual(self.host.learning.journal.rows, {})
        self.assertEqual(self.host.order, [])
        self.assertIsNone(self.host.operation_row)
        self.assertIsNone(self.host.voice_tools)
        self.host.executor.assert_not_called()

    def test_named_malicious_computer_intent_is_blocked_not_treated_as_chat(self):
        self.assertIs(self.host._intercept_command("云岚，执行任意脚本"), True)
        self.assertEqual(self.host.computer.snapshot()["requests"], 1)
        self.assertEqual(self.host.computer.snapshot()["recent"][-1]["reason"],
                         "unsupported_command")
        rows = list(self.host.learning.journal.rows.values())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "computer_control")
        self.assertEqual(rows[0]["status"], "skipped")
        self.assertEqual(rows[0]["labels"], {"decision": "blocked"})
        self.assertIsNone(self.host.operation_row)
        self.host.executor.assert_not_called()

    def test_checkbox_permission_survives_focus_and_listening_false_before_typed_proposal(self):
        conversation = ConversationDialog(self.host)
        self.host.conversation = conversation
        conversation.local_submitted.connect(self.host._intercept_command)
        self.addCleanup(conversation.deleteLater)
        self.host._open_voice_tools()
        tools = self.host.voice_tools
        tools.control.click()
        self.assertTrue(tools.control.isChecked())
        self.assertTrue(self.host.computer.snapshot()["enabled"])
        self.assertEqual(self.host.hotkey_calls, 1)
        self.app.sendEvent(tools, QEvent(QEvent.Type.WindowDeactivate))
        conversation.show()
        conversation.activateWindow()
        conversation.input.setFocus()
        self.host.voice_event.emit({"kind": "listening", "listening": False,
            "generation": self.host.voice_engine.generation})
        self.app.processEvents()
        self.assertTrue(self.host.computer.snapshot()["enabled"])
        self.assertTrue(tools.control.isChecked())
        self.assertEqual(self.host.voice_engine.stop_count, 0)
        conversation.input.setText("云岚，打开计算器")
        conversation.send()
        self.assertIsNotNone(self.host.operation_row)
        self.assertEqual(self.host.operation_row["source"], "typed_direct")
        self.assertEqual(self.host.operation_row["action"], "open_calculator")
        self.assertEqual(self.host.computer.snapshot()["pending"], 1)
        self.host.executor.assert_not_called()

    def test_direct_proposal_never_executes_until_confirmation_button_audit_first(self):
        row = self.proposal()
        self.host.executor.assert_not_called()
        self.assertEqual(self.host.learning.journal.rows[row["id"]]["status"], "queued")
        self.assertEqual(self.host.hotkey_calls, 0)
        self.host.voice_tools.confirm_button.click()
        self.host.voice_tools.confirm_button.click()
        self.host._confirm_operation(row["token"])
        self.host.executor.assert_called_once_with(Action.OPEN_NOTEPAD)
        running = next(i for i, event in enumerate(self.host.order) if event[0] == "mark_running")
        executed = next(i for i, event in enumerate(self.host.order) if event[0] == "executor")
        finished = next(i for i, event in enumerate(self.host.order)
                        if event[0] == "finish" and event[2] == "success")
        self.assertLess(running, executed)
        self.assertLess(executed, finished)
        self.assertIsNone(self.host.operation_row)

    def test_voice_command_routes_as_voice_and_voice_confirmation_cannot_execute(self):
        self.listening()
        self.host.computer.set_enabled(True)
        self.final("云岚，打开计算器")
        row = dict(self.host.operation_row)
        self.assertEqual(row["source"], "voice_direct")
        # PC proposals are intercepted before chat/cloud submission.
        self.assertEqual(self.host.conversation.submissions, [])
        self.assertEqual(self.host.direct_input_source, "typed_direct")
        self.final("云岚，确认")
        self.host.executor.assert_not_called()
        self.host.voice_tools.confirm_button.click()
        self.host.executor.assert_called_once_with(Action.OPEN_CALCULATOR)

    def test_failed_proposal_audit_revokes_permission_and_cannot_execute(self):
        self.host.learning.journal.begin_ok = False
        self.host.computer.set_enabled(True)
        self.host._intercept_command("云岚，打开记事本")
        self.assertIsNone(self.host.operation_row)
        self.assertFalse(self.host.computer.snapshot()["enabled"])
        self.host.executor.assert_not_called()

    def test_failed_running_audit_or_blocked_audit_prevents_dispatch(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                row = self.proposal()
                self.host.learning.audit_blocked = blocked
                self.host.learning.journal.running_ok = False
                self.host.voice_tools.confirm_button.click()
                self.host._confirm_operation(row["token"])
                self.assertFalse(self.host.computer.snapshot()["enabled"])
                self.assertIsNone(self.host.operation_row)
                self.host.executor.assert_not_called()
                self.host.learning.audit_blocked = False
                self.host.learning.journal.running_ok = True

    def test_failed_post_dispatch_audit_revokes_without_second_execution_or_training_claim(self):
        row = self.proposal()
        self.host.learning.journal.finish_ok = False
        self.host.voice_tools.confirm_button.click()
        self.host._confirm_operation(row["token"])
        self.host.executor.assert_called_once()
        self.assertFalse(self.host.computer.snapshot()["enabled"])
        self.assertIn("未知", self.host.voice_notice)
        self.assertNotEqual(self.host.learning.journal.rows[row["id"]]["status"], "success")

    def test_expired_token_and_stale_timer_cannot_execute_or_clear_new_proposal(self):
        old = self.proposal()
        old_callback = self.host.timer_calls[-1][1]
        new = self.proposal(text="云岚，打开计算器")
        old_callback()
        self.assertEqual(self.host.operation_row["token"], new["token"])
        self.host._confirm_operation(old["token"])
        self.host.clock.now += 31
        self.host.voice_tools.confirm_button.click()
        self.host.executor.assert_not_called()
        self.assertIsNone(self.host.operation_row)
        self.assertEqual(self.host.computer.snapshot()["pending"], 0)

    def test_cancel_stop_and_dialog_close_revoke_pending_token(self):
        for operation in (self.host._cancel_operation, self.host._emergency_stop,
                          lambda: self.host.voice_tools.close()):
            with self.subTest(operation=operation):
                row = self.proposal()
                operation()
                self.host._confirm_operation(row["token"])
                self.assertIsNone(self.host.operation_row)
                self.host.executor.assert_not_called()

    def test_microphone_start_requires_explicit_button_and_audited_listening_event(self):
        self.host._open_voice_tools()
        engine = self.host.voice_engine
        engine.start.assert_not_called()
        self.host.voice_tools.start_button.click()
        engine.start.assert_called_once()
        self.assertTrue(self.host.voice_starting)
        self.assertFalse(self.host.voice_active)
        self.host.voice_event.emit({"kind": "listening", "generation": engine.generation,
                                    "listening": True})
        self.assertTrue(self.host.voice_active)
        self.assertFalse(self.host.voice_starting)
        self.assertTrue(any(event[0] == "record" and event[4] == {"voice_event": "session_start"}
                            for event in self.host.order))
        self.host.executor.assert_not_called()

    def test_voice_transcript_stays_out_of_journal_and_chat_source_resets(self):
        self.listening()
        secret = "PRIVATE_PERSON_TRANSCRIPT_CONTENT_73"
        self.final(secret, confidence=.91)
        self.assertEqual(self.host.conversation.submissions, [(secret, "voice_direct")])
        self.assertEqual(self.host.direct_input_source, "typed_direct")
        self.assertNotIn(secret, json.dumps(self.host.order))
        recognized = [event for event in self.host.order if event[0] == "record"
                      and event[4] == {"voice_event": "recognized"}]
        self.assertEqual(len(recognized), 1)
        self.assertEqual(recognized[0][2], "local_voice")
        self.assertEqual(recognized[0][3], {"confidence": .91, "characters": len(secret)})
        self.host.executor.assert_not_called()

    def test_voice_emergency_does_not_depend_on_audit_success(self):
        self.listening()
        self.host.computer.set_enabled(True)
        self.host.learning.journal.record_ok = False
        self.host.conversation.busy = True
        self.final("小云岚，紧急停止", confidence=.01)
        self.assertFalse(self.host.computer.snapshot()["enabled"])
        self.assertFalse(self.host.voice_active)
        self.host.executor.assert_not_called()

    def test_stopped_generation_callbacks_and_speech_do_not_replay(self):
        generation = self.listening()
        self.host.computer.set_enabled(True)
        self.host._voice_stop()
        self.final("云岚，打开记事本", generation=generation)
        self.assertEqual(self.host.conversation.submissions, [])
        self.host._speak_voice_reply("private reply", generation)
        self.host.voice_engine.speak.assert_not_called()
        self.host.executor.assert_not_called()

    def test_untrusted_contexts_low_confidence_and_stale_generation_cannot_propose(self):
        self.host.computer.set_enabled(True)
        for source in ("model", "web", "assistant", "server", "desktop", "tool"):
            self.host.direct_input_source = source
            self.host._intercept_command("云岚，打开记事本")
            self.assertIsNone(self.host.operation_row)
        self.host.direct_input_source = "typed_direct"
        generation = self.listening()
        self.final("云岚，打开记事本", generation=generation - 1)
        self.final("云岚，打开记事本", confidence=.149999)
        self.assertIsNone(self.host.operation_row)
        self.host.executor.assert_not_called()

    def test_busy_draft_pending_and_cloud_without_consent_drop_voice_without_replay(self):
        self.listening()
        self.host.computer.set_enabled(True)
        for attr, value in (("busy", True), ("pending_text", "pending")):
            setattr(self.host.conversation, attr, value)
            self.final("云岚，打开记事本")
            setattr(self.host.conversation, attr, False if attr == "busy" else "")
        self.host.conversation.input.setText("typed draft")
        self.final("云岚，打开记事本")
        self.assertEqual(self.host.conversation.input.text(), "typed draft")
        self.host.conversation.input.setText("")
        self.assertIsNone(self.host.operation_row)
        self.host.conversation.cloud_mode = True
        self.final("你好，这是一段私人聊天")
        self.assertEqual(self.host.conversation.submissions, [])
        self.assertIsNone(self.host.operation_row)
        # Local fixed commands need no cloud consent and are never uploaded.
        self.final("云岚，打开记事本")
        self.assertEqual(self.host.conversation.submissions, [])
        self.assertEqual(self.host.operation_row["source"], "voice_direct")
        self.host.executor.assert_not_called()

    def test_voice_learning_is_opt_in_and_never_calls_local_adaptation_below_threshold(self):
        self.host.direct_input_source = "voice_direct"
        for enabled, confidence in ((False, .99), (True, .84)):
            self.host.voice_learning = enabled
            self.host.voice_confidence = confidence
            self.host._chat_reading("private user words")
            self.assertFalse(self.host._chat_may_learn)
        self.host.learning.observe_emotion.assert_not_called()
        self.host.learning.adapt.assert_not_called()
        self.host.voice_learning = True
        self.host.voice_confidence = .85
        self.host._chat_reading("private user words")
        self.host.learning.observe_emotion.assert_called_once_with("private user words", "local_voice")
        self.host.learning.adapt.assert_called_once_with("chat", "neutral", "local_voice")

    def test_voice_emergency_bypasses_draft_busy_pending_and_cloud_consent(self):
        self.listening()
        self.host.computer.set_enabled(True)
        self.host.conversation.busy = True
        self.host.conversation.pending_text = "pending"
        self.host.conversation.input.setText("typed draft")
        self.host.conversation.cloud_mode = True
        self.final("云岚，紧急停止", confidence=0.0)
        self.assertFalse(self.host.computer.snapshot()["enabled"])
        self.assertFalse(self.host.voice_active)
        self.assertEqual(self.host.conversation.input.text(), "typed draft")
        self.host.executor.assert_not_called()

    def test_single_direct_emergency_counts_once_not_twice(self):
        self.host._intercept_command("云岚，紧急停止")
        self.assertEqual(self.host.computer.snapshot()["emergency"], 1)
        self.host.executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
