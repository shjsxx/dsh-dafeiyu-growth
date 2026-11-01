"""Opt-in offline synthetic SAPI diagnostic, including the real transcript gate.

This is NOT microphone testing, live speech-quality measurement or training.
Two fixed non-private phrases are synthesized ONLY into SpMemoryStream and fed
ONLY to SpInprocRecognizer.AudioInputStream.  No sound device is selected for
recognition and no speaker playback, audio file, network or user data is used.

``run_synthetic_probe()`` returns only bounded counts, engine scores and booleans.
It creates no Qt objects.  Call from an isolated, explicitly opted-in smoke-test
process (including PyInstaller), not from the running pet's voice apartment.
The 15-second overall deadline covers asynchronous synthesis/event pumping;
Windows COM calls themselves cannot be forcibly interrupted safely in Python.

CLI: python native/voice_probe.py
Only sanitized JSON is printed; success requires expected accepted final text
from BOTH actual synthetic samples.  The real _RecoSink and VoiceEngine result
normalization/confidence/echo/confirmation gates are used, not fake recognition.
"""
from __future__ import annotations

from collections import deque
import json
import sys
import time

from voice_engine import (
    CONFIDENCE_FLOOR, CONFIDENCE_KIND, ENGINE_KIND, VoiceEngine, _RecoSink,
    _RECO_EVENTS_IID, _items, _languages,
)

TIMEOUT_SECONDS = 15.0
_SAMPLES = (
    ("今天的天气很好。", "天气很好"),
    ("你好，你今天怎么样。", "你好"),
)


def _initial_result():
    return {
        "schema_version": 1, "engine_kind": ENGINE_KIND, "status": "not_run",
        "ok": False, "synthetic_only": True, "microphone_used": False,
        "speaker_playback_used": False, "audio_files_used": False,
        "network_used": False, "training_performed": False,
        "timeout_seconds": TIMEOUT_SECONDS, "sample_count": len(_SAMPLES),
        "completed_samples": 0, "raw_final_count": 0,
        "accepted_final_count": 0, "expected_match_count": 0,
        "recognition_error_count": 0, "cleanup_ok": True,
        "confidence_floor": CONFIDENCE_FLOOR, "confidence_kind": CONFIDENCE_KIND,
        "confidence_calibrated": False, "samples": [],
    }


def _sample(pythoncom, pywintypes, dispatch, wrap, phrase, expected, deadline, index):
    # All recognized text is confined to these bounded volatile containers.
    accepted = deque(maxlen=16)
    raw = {"finals": 0, "errors": 0}
    sample = {"sample": index, "raw_final_count": 0, "accepted_final_count": 0,
              "expected_match": False, "scores": [], "recognition_error_count": 0,
              "cleanup_ok": True, "status": "probe_failed"}
    engine = VoiceEngine(accepted.append)
    # Diagnostic input does NOT call start(): doing so would select a microphone.
    # Feed real sink callbacks through the exact production final-result gate.
    engine._generation = 1
    engine._requested = True
    recognizer = context = grammar = connection = gateway = sink = None
    voice = stream = original_output_stream = original_output_token = None
    cookie = None
    voice_memory_assigned = False

    def emit(kind, text="", confidence=0.0):
        if kind == "final":
            raw["finals"] = min(16, raw["finals"] + 1)
        elif kind == "recognition_error":
            raw["errors"] = min(16, raw["errors"] + 1)
        engine._on_recognition(1, kind, text, confidence)

    def pump_until(predicate):
        while time.monotonic() < deadline:
            pythoncom.PumpWaitingMessages()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def cleanup(action):
        try:
            action()
        except Exception:
            sample["cleanup_ok"] = False

    try:
        if time.monotonic() >= deadline:
            sample["status"] = "timeout"
            return sample
        stream = dispatch("SAPI.SpMemoryStream")
        stream.Format.Type = 22  # SAFT22kHz16BitMono; raw PCM stays in memory.
        voice = dispatch("SAPI.SpVoice")
        original_output_stream = voice.AudioOutputStream
        original_output_token = voice.AudioOutput
        voices = [token for token in _items(voice.GetVoices())
                  if any(lcid & 0x3FF == 4 for lcid, _ in _languages(token))]
        if not voices:
            sample["status"] = "no_chinese_voice"
            return sample
        voice.Voice = voices[0]
        voice.AudioOutputStream = stream  # Assign BEFORE any Speak call.
        voice_memory_assigned = True
        voice.Speak(phrase, 1 | 2 | 16)  # Async, purge, literal; NEVER speaker.
        if not pump_until(lambda: bool(voice.WaitUntilDone(0))):
            sample["status"] = "timeout"
            return sample
        stream.Seek(0, 0)
        recognizer = dispatch("SAPI.SpInprocRecognizer")
        recognizer.State = 0
        recognizers = [token for token in _items(recognizer.GetRecognizers())
                       if any(lcid & 0x3FF == 4 for lcid, _ in _languages(token))]
        if not recognizers:
            sample["status"] = "no_chinese_recognizer"
            return sample
        recognizer.Recognizer = recognizers[0]
        # Never touch AudioInput/GetAudioInputs: only synthetic memory input.
        recognizer.AudioInputStream = stream
        context = dispatch(recognizer.CreateRecoContext())
        context.State = 0
        context.RetainedAudio = 0
        context.EventInterests = 1 | 2 | 4 | 8 | 16 | 32 | 512 | 16384
        sink = _RecoSink(emit, dispatch)
        gateway = wrap(sink)
        container = context._oleobj_.QueryInterface(pythoncom.IID_IConnectionPointContainer)
        connection = container.FindConnectionPoint(pywintypes.IID(_RECO_EVENTS_IID))
        cookie = connection.Advise(gateway)
        grammar = dispatch(context.CreateGrammar(1))
        grammar.DictationLoad("", 0)
        grammar.DictationSetState(1)
        context.State = 1
        recognizer.State = 1
        if not pump_until(lambda: raw["finals"] > 0 or raw["errors"] > 0):
            sample["status"] = "timeout"
        else:
            finals = [event for event in accepted if event["kind"] == "final"]
            sample["raw_final_count"] = raw["finals"]
            sample["accepted_final_count"] = len(finals)
            sample["scores"] = [round(event["confidence"], 4) for event in finals[:4]]
            sample["expected_match"] = any(expected in event["text"] for event in finals)
            sample["recognition_error_count"] = raw["errors"]
            sample["status"] = ("ok" if sample["expected_match"] else
                                "recognition_failed" if not finals else "unexpected_transcript")
    except Exception:
        # HRESULTs/COM exception strings may include device paths: never expose.
        sample["status"] = "probe_failed"
    finally:
        # Disable/purge recognition BEFORE unadvising, then cancel synthesis
        # while its output is still the memory stream.  Restoring the original
        # output does not play anything; no subsequent nonempty Speak occurs.
        if recognizer is not None:
            cleanup(lambda: setattr(recognizer, "State", 3))
        if grammar is not None:
            cleanup(lambda: grammar.DictationSetState(0))
        if context is not None:
            cleanup(lambda: setattr(context, "State", 0))
        if connection is not None and cookie is not None:
            cleanup(lambda: connection.Unadvise(cookie))
        if sink is not None:
            cleanup(sink.close)
        if voice is not None and voice_memory_assigned:
            cleanup(lambda: voice.Speak("", 1 | 2 | 16))
            if original_output_stream is not None:
                cleanup(lambda: setattr(voice, "AudioOutputStream", original_output_stream))
            elif original_output_token is not None:
                cleanup(lambda: setattr(voice, "AudioOutput", original_output_token))
        engine.close()
        accepted.clear()
        recognizer = context = grammar = connection = gateway = sink = None
        voice = stream = original_output_stream = original_output_token = None
        if not sample["cleanup_ok"]:
            sample["status"] = "cleanup_failed"
    return sample


def run_synthetic_probe():
    """Run opt-in fixed synthetic speech only; return sanitized bounded data."""
    result = _initial_result()
    if sys.platform != "win32":
        result["status"] = "unsupported_platform"
        return result
    try:
        import pythoncom
        import pywintypes
        from win32com.client import dynamic
        from win32com.server.util import wrap
    except Exception:
        result["status"] = "pywin32_unavailable"
        return result
    initialized = False
    try:
        pythoncom.CoInitialize()
        initialized = True
        deadline = time.monotonic() + TIMEOUT_SECONDS
        for index, (phrase, expected) in enumerate(_SAMPLES, 1):
            sample = _sample(pythoncom, pywintypes, dynamic.Dispatch, wrap,
                             phrase, expected, deadline, index)
            result["samples"].append(sample)
            result["completed_samples"] += 1
            result["raw_final_count"] += sample["raw_final_count"]
            result["accepted_final_count"] += sample["accepted_final_count"]
            result["expected_match_count"] += int(sample["expected_match"])
            result["recognition_error_count"] += sample["recognition_error_count"]
            result["cleanup_ok"] = result["cleanup_ok"] and sample["cleanup_ok"]
            if sample["status"] != "ok":
                result["status"] = sample["status"]
                break
        else:
            result["status"] = "ok"
            result["ok"] = result["expected_match_count"] == len(_SAMPLES) and result["cleanup_ok"]
    except Exception:
        result["status"] = "probe_failed"
    finally:
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                result["cleanup_ok"] = False
                result["status"] = "cleanup_failed"
                result["ok"] = False
    return result


if __name__ == "__main__":
    outcome = run_synthetic_probe()
    print(json.dumps(outcome, ensure_ascii=True, sort_keys=True))
    raise SystemExit(0 if outcome["ok"] else 1)
