"""Hermetic voice tests: mocked COM, no actual microphone or TTS playback.

Run: python -m unittest discover -s native -p test_voice_engine.py -v
All synthetic recognition below is explicitly fake input supplied by the tests;
the production engine never synthesizes a recognition result.
"""
import copy
from collections import deque
import inspect
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import voice_engine as ve
import voice_probe as vp


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.005)
    raise AssertionError("Timed out waiting for mocked worker")


def ready_caps():
    caps = ve._empty_capabilities("ready")
    caps.update(available=True, recognition_available=True, chinese_available=True,
                tts_available=True, continuous=True, recognizer_count=1,
                voice_count=1, audio_input_count=1, languages=["zh-CN"])
    return caps


class FakeBackend:
    def __init__(self, caps=None):
        self.caps = caps or ready_caps()
        self.calls = []
        self.events = deque()
        self.emit = None
        self.active = self.speaking = False
        self.max_active = 0
        self.starts = self.speaks = 0
        self.spoken = []
        self.block_speak = None
        self.speak_entered = threading.Event()
        self.closed = threading.Event()
        self.fail_start = False

    def record(self, kind):
        self.calls.append((kind, threading.get_ident()))

    def initialize(self):
        self.record("initialize")
        return copy.deepcopy(self.caps)

    def start(self, emit, is_current=lambda: True):
        self.record("start")
        if self.fail_start:
            raise RuntimeError("PRIVATE exception text must not be emitted")
        if not is_current():
            return False
        if self.active:
            raise AssertionError("overlapping recognizers")
        self.starts += 1
        self.active = True
        self.max_active = max(self.max_active, int(self.active))
        self.emit = emit
        return True

    def stop(self):
        self.record("stop")
        self.active = self.speaking = False

    def speak(self, text):
        self.record("speak")
        if self.speaking:
            raise AssertionError("overlapping TTS")
        self.speaking = True
        self.speaks += 1
        self.spoken.append(text)
        self.speak_entered.set()
        if self.block_speak is not None:
            self.block_speak.wait(2.0)

    def interrupt(self):
        self.record("interrupt")
        self.speaking = False

    def is_speaking(self):
        return self.speaking

    def pump(self):
        self.record("pump")
        while self.events:
            self.events.popleft()()

    def inject(self, kind, text="", confidence=0.95, emit=None):
        emit = emit or self.emit
        self.events.append(lambda: emit(kind, text, confidence))

    def close(self):
        self.record("close")
        self.stop()
        self.closed.set()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.factory = patch.object(ve, "_SapiBackend", return_value=self.backend)
        self.mock_factory = self.factory.start()
        self.events = []
        self.engine = ve.VoiceEngine(self.events.append)

    def tearDown(self):
        if self.backend.block_speak is not None:
            self.backend.block_speak.set()
        self.engine.close()
        self.factory.stop()

    def start(self):
        self.assertTrue(self.engine.start())
        generation = self.engine.generation
        wait_for(lambda: any(e["kind"] == "listening" and e["listening"] and
                             e["generation"] == generation for e in self.events))

    def finals(self):
        return [e for e in self.events if e["kind"] == "final"]

    def test_constructor_has_no_com_or_microphone_and_probe_is_passive(self):
        self.assertFalse(self.mock_factory.called)
        caps = self.engine.capabilities()
        self.assertEqual(caps["status"], "ready")
        self.assertEqual(self.backend.starts, 0)
        self.assertEqual(self.backend.speaks, 0)
        self.assertFalse(caps["barge_in"])
        self.assertFalse(caps["full_duplex"])
        self.assertFalse(caps["echo_cancellation"])
        self.assertTrue(caps["requires_command_confirmation"])
        caps["languages"].append("MUTATION")
        self.assertNotIn("MUTATION", self.engine.capabilities()["languages"])

    def test_no_chinese_is_truthfully_unavailable(self):
        self.backend.caps = ve._empty_capabilities("no_chinese_recognizer")
        self.assertFalse(self.engine.start())
        self.assertEqual(self.engine.capabilities()["status"], "no_chinese_recognizer")
        self.assertEqual(self.backend.starts, 0)
        self.assertFalse(self.finals())

    def test_start_idempotent_restart_has_one_worker_and_no_overlap(self):
        self.start()
        generation = self.engine.generation
        for _ in range(20):
            self.assertTrue(self.engine.start())
        self.assertEqual(self.engine.generation, generation)
        self.engine.stop()
        self.start()
        wait_for(lambda: self.backend.starts == 2)
        self.assertGreater(self.engine.generation, generation)
        self.assertEqual(self.backend.max_active, 1)
        self.assertEqual(len({thread for _, thread in self.backend.calls}), 1)
        self.assertNotEqual(self.backend.calls[0][1], threading.get_ident())

    def test_stop_immediately_invalidates_old_callbacks_and_destroys_audio(self):
        self.start()
        stale_emit = self.backend.emit
        old_generation = self.engine.generation
        self.engine.stop()
        self.backend.inject("final", "STALE PRIVATE TEXT", emit=stale_emit)
        wait_for(lambda: not self.backend.active)
        self.assertGreater(self.engine.generation, old_generation)
        self.assertFalse(self.finals())
        self.start()
        self.backend.inject("final", "OLD AFTER RESTART", emit=stale_emit)
        self.backend.inject("final", "你好云岚")
        wait_for(lambda: len(self.finals()) == 1)
        self.assertEqual(self.finals()[0]["text"], "你好云岚")
        self.assertEqual(self.finals()[0]["generation"], self.engine.generation)

    def test_final_confidence_bounds_limits_and_mandatory_confirmation(self):
        self.start()
        for confidence in (-1, ve.CONFIDENCE_FLOOR - 0.01, float("nan"), float("inf"), "bad"):
            self.backend.inject("final", "低信心", confidence)
        self.backend.inject("final", "\x00\n" + "中" * 10000, 1.5)
        wait_for(lambda: len(self.finals()) == 1)
        event = self.finals()[0]
        self.assertEqual(len(event["text"]), ve.MAX_TEXT)
        self.assertNotIn("\x00", event["text"])
        self.assertEqual(event["confidence"], 1.0)
        self.assertTrue(event["requires_confirmation"])
        self.assertEqual(event["source"], "microphone_unverified")
        self.assertEqual(event["engine_kind"], "windows_sapi")

    def test_transcript_floor_is_not_calibrated_accuracy_or_action_permission(self):
        self.start()
        self.backend.inject("final", "低于转写门槛", ve.CONFIDENCE_FLOOR - 0.001)
        self.backend.inject("final", "今天的天气很好", 0.3922)
        self.backend.inject("final", "刚好达到转写门槛", ve.CONFIDENCE_FLOOR)
        wait_for(lambda: len(self.finals()) == 2)
        self.assertEqual(self.finals()[0]["confidence"], 0.3922)
        self.assertTrue(all(event["requires_confirmation"] for event in self.finals()))
        self.assertTrue(all(event["confidence_kind"] == "sapi_engine_score_uncalibrated"
                            and event["confidence_calibrated"] is False for event in self.finals()))
        caps = self.engine.capabilities()
        self.assertEqual(caps["confidence_floor"], 0.15)
        self.assertFalse(caps["confidence_calibrated"])

    def test_partial_is_ram_only_and_rejected_final_never_emitted(self):
        self.start()
        self.backend.inject("partial", "正在听", 0.2)
        self.backend.inject("rejected")
        self.backend.inject("final", "", 1)
        wait_for(lambda: any(e["kind"] == "partial" for e in self.events))
        self.assertFalse(self.finals())

    def test_rejected_final_counts_are_text_free_and_partials_do_not_spam(self):
        self.start()
        self.engine._recent_tts.append((self.engine._normalized("自声回灌示例"), time.monotonic() + 3))
        self.backend.inject("final", "低分私密原句", ve.CONFIDENCE_FLOOR - 0.01)
        self.backend.inject("rejected")
        self.backend.inject("final", "自声回灌示例", 0.8)
        for _ in range(20):
            self.backend.inject("partial", "自声回灌示例", 0.8)
        marker = threading.Event()
        self.backend.events.append(marker.set)
        self.assertTrue(marker.wait(1))
        rejected = [event for event in self.events if event["kind"] == "rejected"]
        self.assertEqual([event["code"] for event in rejected],
                         ["low_score", "not_recognized", "echo_suppressed"])
        self.assertTrue(all("text" not in event for event in rejected))
        self.assertTrue(all(0 <= event["confidence"] <= 1 for event in rejected))
        stale = self.backend.emit
        self.engine.stop()
        self.backend.inject("rejected", emit=stale)
        marker = threading.Event()
        self.backend.events.append(marker.set)
        self.assertTrue(marker.wait(1))
        self.assertEqual(len([event for event in self.events if event["kind"] == "rejected"]), 3)

    def test_async_tts_bounded_latest_only_queue_and_interrupt(self):
        self.engine.capabilities()
        self.backend.block_speak = threading.Event()
        self.assertTrue(self.engine.speak("第一句"))
        self.assertTrue(self.backend.speak_entered.wait(1))
        for _ in range(500):
            self.assertTrue(self.engine.speak("中" * 10000))
        with self.engine._condition:
            self.assertLessEqual(len(self.engine._commands), ve.MAX_QUEUE)
            speaks = [c for c in self.engine._commands if c[0] == "speak"]
            self.assertEqual(len(speaks), 1)
            self.assertEqual(len(speaks[0][2][1]), 800)
        self.engine.interrupt()
        self.backend.block_speak.set()
        wait_for(lambda: any(e["kind"] == "speech_end" for e in self.events))
        self.assertEqual(self.backend.speaks, 1)
        self.assertFalse(self.backend.speaking)
        self.assertFalse(self.engine.speak("\x00 \n"))
        self.assertFalse(self.engine.speak(object()))

    def test_replace_tts_never_overlaps_and_end_has_no_text(self):
        self.assertTrue(self.engine.speak("第一句"))
        wait_for(lambda: self.backend.speaks == 1)
        self.assertTrue(self.engine.speak("第二句"))
        wait_for(lambda: self.backend.speaks == 2)
        self.backend.speaking = False
        wait_for(lambda: any(e.get("reason") == "completed" for e in self.events))
        speech = [e for e in self.events if e["kind"].startswith("speech_")]
        self.assertTrue(all("text" not in event for event in speech))
        self.assertTrue(any(e.get("reason") == "replaced" for e in speech))

    def test_speaker_echo_is_gated_and_soundstart_never_barges_in(self):
        self.start()
        self.engine.speak("云岚早上好今天很高兴见到你")
        wait_for(lambda: self.backend.speaking)
        interrupts = sum(kind == "interrupt" for kind, _ in self.backend.calls)
        self.backend.inject("sound_start")
        self.backend.inject("partial", "云岚早上好")
        self.backend.inject("final", "云岚早上好")
        marker = threading.Event()
        self.backend.events.append(marker.set)
        self.assertTrue(marker.wait(1))
        self.assertFalse(self.finals())
        self.assertEqual(sum(kind == "interrupt" for kind, _ in self.backend.calls), interrupts)
        self.assertTrue(self.backend.speaking)
        self.engine.interrupt()
        wait_for(lambda: not self.backend.speaking)
        # Move the timer only, not wall time, to test the text-overlap barrier.
        self.engine._echo_until = 0
        self.backend.inject("sound_start")
        self.backend.inject("final", "云岚早上好")
        self.backend.inject("sound_start")
        self.backend.inject("final", "请显示帮助")
        wait_for(lambda: len(self.finals()) == 1)
        self.assertEqual(self.finals()[0]["text"], "请显示帮助")

    def test_start_failure_sanitized_and_recoverable(self):
        self.backend.fail_start = True
        self.assertTrue(self.engine.start())
        wait_for(lambda: any(e["kind"] == "error" for e in self.events))
        error = next(e for e in self.events if e["kind"] == "error")
        self.assertEqual(error["code"], "start_failed")
        self.assertNotIn("PRIVATE", repr(self.events))
        self.backend.fail_start = False
        self.start()

    def test_callback_failure_does_not_kill_worker(self):
        self.engine._callback = lambda event: (_ for _ in ()).throw(RuntimeError("private"))
        self.assertTrue(self.engine.start())
        wait_for(lambda: self.backend.active)
        self.engine._callback = self.events.append
        self.backend.inject("final", "正常继续")
        wait_for(lambda: len(self.finals()) == 1)

    def test_close_joins_worker_releases_and_prevents_reopen(self):
        self.start()
        self.engine.speak("测试")
        wait_for(lambda: self.backend.speaks == 1)
        self.engine.close()
        self.assertTrue(self.backend.closed.is_set())
        self.assertFalse(self.engine._thread.is_alive())
        self.assertFalse(self.backend.active)
        self.assertFalse(self.backend.speaking)
        self.assertEqual(len(self.engine._commands), 0)
        self.assertEqual(len(self.engine._recent_tts), 0)
        self.assertFalse(self.engine.start())
        self.assertFalse(self.engine.speak("不可重启"))
        self.assertEqual(self.engine.capabilities()["status"], "closed")

    def test_no_external_calls_or_persistence_imports(self):
        source = inspect.getsource(ve)
        for forbidden in ("import urllib", "import requests", "import socket", "import subprocess",
                          "import logging", "import PySide", "open(", "write_text(",
                          "SharedRecognizer\"", "EnsureDispatch(", "DispatchWithEvents("):
            self.assertNotIn(forbidden, source)


class BackendTests(unittest.TestCase):
    class Token:
        def __init__(self, name, language="804"):
            self.name, self.language = name, language

        def GetDescription(self):
            return self.name

        def GetAttribute(self, key):
            return self.language

    class Collection:
        def __init__(self, rows):
            self.rows = rows
            self.Count = len(rows)

        def Item(self, index):
            return self.rows[index]

    def test_probe_only_enumerates_no_input_context_or_speak(self):
        tokens = self.Collection([self.Token("中\x00\n" + "文" * 500)])
        inputs = self.Collection([self.Token("Realtek Microphone"), self.Token("Stereo Mix")])
        class EnumerationOnly:
            def GetRecognizers(self):
                return tokens
            def GetAudioInputs(self):
                return inputs
            def GetVoices(self):
                return tokens
            def __setattr__(self, key, value):
                raise AssertionError("Probe assigned a property")
        calls = []
        backend = ve._SapiBackend()
        backend.dispatch = lambda progid: (calls.append(progid) or EnumerationOnly())
        caps = backend.probe()
        self.assertEqual(calls, ["SAPI.SpInprocRecognizer", "SAPI.SpVoice"])
        self.assertTrue(caps["recognition_available"])
        self.assertEqual(caps["recognizers"][0]["languages"], ["zh-CN"])
        self.assertLessEqual(len(caps["recognizers"][0]["name"]), ve.MAX_LABEL)
        self.assertFalse(caps["audio_inputs"][1]["eligible"])

    def test_probe_token_collection_and_languages_are_bounded(self):
        collection = self.Collection([self.Token("voice", "804;409;garbage;fffff")] * 200)
        backend = ve._SapiBackend()
        backend.dispatch = lambda progid: SimpleNamespace(
            GetRecognizers=lambda: collection, GetAudioInputs=lambda: collection,
            GetVoices=lambda: collection)
        caps = backend.probe()
        self.assertEqual(caps["recognizer_count"], ve.MAX_TOKENS)
        self.assertEqual(caps["voice_count"], ve.MAX_TOKENS)
        self.assertEqual(caps["audio_input_count"], ve.MAX_TOKENS)
        self.assertEqual(set(caps["languages"]), {"zh-CN", "en-US"})

    def test_sink_extracts_bounded_text_confidence_and_never_leaks_errors(self):
        events = []
        sink = ve._RecoSink(lambda *args: events.append(args), lambda value: value)
        info = SimpleNamespace(GetText=lambda *args: "中" * 3000,
                               Rule=SimpleNamespace(EngineConfidence=0.91))
        sink.Recognition(1, 0, 0, SimpleNamespace(PhraseInfo=info))
        self.assertEqual(events[0][0], "final")
        self.assertEqual(len(events[0][1]), 800)
        self.assertEqual(events[0][2], 0.91)
        info.Rule = SimpleNamespace(Confidence=0)
        sink.Hypothesis(1, 0, SimpleNamespace(PhraseInfo=info))
        self.assertEqual(events[-1][2], 0.0)  # No invented categorical probability.
        sink.Recognition(1, 0, 0, object())
        self.assertEqual(events[-1], ("recognition_error", "", 0.0))
        sink.close()
        sink.SoundStart(1, 0)
        self.assertEqual(len(events), 3)

    def backend_harness(self):
        trace = []
        class Tracked:
            def __init__(self, label):
                object.__setattr__(self, "label", label)
            def __setattr__(self, key, value):
                trace.append((self.label, key, value))
                object.__setattr__(self, key, value)
        recognizer, context, grammar = (Tracked(label) for label in ("recognizer", "context", "grammar"))
        chinese = self.Token("Chinese", "804")
        microphone = self.Token("Microphone")
        connection = SimpleNamespace(
            Advise=lambda gateway: (trace.append(("connection", "advise", True)) or 17),
            Unadvise=lambda cookie: trace.append(("connection", "unadvise", cookie)))
        container = SimpleNamespace(FindConnectionPoint=lambda iid: connection)
        context._oleobj_ = SimpleNamespace(QueryInterface=lambda iid: container)
        context.CreateGrammar = lambda number: grammar
        grammar.DictationLoad = lambda name, flags: trace.append(("grammar", "load", (name, flags)))
        grammar.DictationSetState = lambda state: trace.append(("grammar", "active", state))
        recognizer.GetRecognizers = lambda: self.Collection([chinese])
        recognizer.GetAudioInputs = lambda: self.Collection([microphone])
        recognizer.CreateRecoContext = lambda: context
        backend = ve._SapiBackend()
        backend.pythoncom = SimpleNamespace(IID_IConnectionPointContainer="container")
        backend.event_iid = ve._RECO_EVENTS_IID
        backend.dispatch = lambda value: recognizer if isinstance(value, str) else value
        backend.wrap = lambda sink: sink
        trace.clear()
        return backend, trace, recognizer, context

    def test_explicit_start_owns_grammar_and_stop_disables_audio_before_unadvise(self):
        backend, trace, recognizer, context = self.backend_harness()
        self.assertTrue(backend.start(lambda *args: None))
        self.assertEqual(context.RetainedAudio, 0)
        self.assertEqual(recognizer.State, 1)
        self.assertIn(("grammar", "load", ("", 0)), trace)
        self.assertLess(trace.index(("connection", "advise", True)),
                        trace.index(("recognizer", "State", 1)))
        sink = backend.sink
        trace.clear()
        backend.stop()
        self.assertLess(trace.index(("recognizer", "State", 3)),
                        trace.index(("connection", "unadvise", 17)))
        self.assertIn(("grammar", "active", 0), trace)
        self.assertIsNone(backend.recognizer)
        self.assertIsNone(backend.context)
        self.assertIsNone(sink.dispatch)

    def test_cancelled_start_never_activates_input(self):
        backend, trace, _, _ = self.backend_harness()
        self.assertFalse(backend.start(lambda *args: None, lambda: False))
        self.assertNotIn(("recognizer", "State", 1), trace)
        self.assertIsNone(backend.recognizer)

    def test_tts_is_literal_async_purge_and_prefer_chinese(self):
        calls = []
        voice = SimpleNamespace(GetVoices=lambda: self.Collection([
            self.Token("English", "409"), self.Token("Chinese", "804")]),
            Speak=lambda text, flags: calls.append((text, flags)))
        backend = ve._SapiBackend()
        backend.dispatch = lambda progid: voice
        backend.speak("<speak>literal</speak>")
        self.assertEqual(voice.Voice.name, "Chinese")
        self.assertEqual(calls, [("<speak>literal</speak>", 19)])
        backend.interrupt()
        self.assertEqual(calls[-1], ("", 19))


class ProbeTests(unittest.TestCase):
    def test_unsupported_probe_never_initializes_com_or_reads_user_audio(self):
        with patch.object(vp.sys, "platform", "not_windows"), patch.object(vp, "_sample") as sample:
            result = vp.run_synthetic_probe()
        sample.assert_not_called()
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertFalse(result["ok"])
        self.assertFalse(result["microphone_used"])
        self.assertFalse(result["speaker_playback_used"])
        self.assertFalse(result["audio_files_used"])
        self.assertFalse(result["training_performed"])

    def test_probe_import_failure_returns_fixed_sanitized_status(self):
        with patch.object(vp.sys, "platform", "win32"), patch.dict(vp.sys.modules, {"pythoncom": None}):
            result = vp.run_synthetic_probe()
        self.assertEqual(result["status"], "pywin32_unavailable")
        self.assertFalse(result["ok"])
        self.assertEqual(result["samples"], [])

    def test_probe_aggregates_only_bounded_sanitized_sample_results(self):
        pythoncom = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        modules = {"pythoncom": pythoncom, "pywintypes": SimpleNamespace(),
                   "win32com": SimpleNamespace(),
                   "win32com.client": SimpleNamespace(dynamic=SimpleNamespace(Dispatch=Mock())),
                   "win32com.server": SimpleNamespace(),
                   "win32com.server.util": SimpleNamespace(wrap=Mock())}
        def sample(*args):
            return {"sample": args[-1], "raw_final_count": 1, "accepted_final_count": 1,
                    "expected_match": True, "scores": [0.3], "recognition_error_count": 0,
                    "cleanup_ok": True, "status": "ok"}
        with patch.object(vp.sys, "platform", "win32"), patch.dict(vp.sys.modules, modules), \
                patch.object(vp, "_sample", side_effect=sample) as run_sample:
            result = vp.run_synthetic_probe()
        self.assertTrue(result["ok"])
        self.assertEqual(result["accepted_final_count"], 2)
        self.assertEqual(result["expected_match_count"], 2)
        self.assertEqual(run_sample.call_count, 2)
        self.assertTrue(all("text" not in row for row in result["samples"]))
        pythoncom.CoInitialize.assert_called_once_with()
        pythoncom.CoUninitialize.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
