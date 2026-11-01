"""Offline Windows SAPI speech, with explicit consent and no Qt dependency.

Public API: ``VoiceEngine(callback).capabilities(), start(), stop(), speak(text),
interrupt(), close()``.  The callback receives plain dictionaries ON THE COM
WORKER THREAD; a Qt parent must forward them through a queued signal.  Keep the
callback short.  ``start`` requests continuous, segmented Chinese dictation;
``True`` means accepted, not that the microphone is already open.  Wait for a
``listening`` event with ``listening=True``.  Only an explicit start opens audio.
``stop`` invalidates callbacks synchronously and queues audio-off/COM teardown;
``close`` also joins the worker (bounded wait).  No transcript/audio persistence,
network, subprocess, Qt, shell commands, model downloads, or paid services.

This is NOT a full-duplex speech model and has NO hardware echo cancellation.
Recognition continues during TTS but results are gated during playback and its
short tail; similar recent TTS text is also rejected.  SoundStart deliberately
DOES NOT interrupt playback (speaker echo is not evidence of user barge-in).
Use an explicit interrupt button.  Arbitrary system playback cannot reliably be
distinguished from a person: ALL proposed computer actions require parent UI
confirmation, even for high-confidence recognition.  Never execute text here.
The .15 score floor permits transcript/chat display only.  ``confidence`` is
SAPI's uncalibrated engine score, NOT a probability of correct recognition;
parents must apply separate stricter learning/action gates and explicit consent.

COM is apartment-owned.  Probing ONLY enumerates installed tokens (no context,
audio input assignment, Speak, or microphone capture).  An in-process recognizer
with our own dictation grammar cannot enable Windows shared voice commands.
The hand-written IDispatch connection-point sink avoids WithEvents/makepy and
writable gen_py caches, including in PyInstaller.  Bundle pythoncom, pywintypes,
win32com.client.dynamic, win32com.server.util/policy/exception and winerror.
"""
from __future__ import annotations

from collections import deque
import copy
import locale
import math
import sys
import threading
import time

ENGINE_KIND = "windows_sapi"
MAX_TEXT = 800
MAX_TOKENS = 32
MAX_LABEL = 96
MAX_QUEUE = 16
# Transcript/chat noise floor, NOT learning or computer-action authorization.
# Chinese SAPI EngineConfidence is an uncalibrated engine score, not accuracy;
# common correct local synthetic utterances often score around 0.2--0.4.
# The parent must keep independent strict learning/action gates and UI consent.
CONFIDENCE_FLOOR = 0.15
CONFIDENCE_KIND = "sapi_engine_score_uncalibrated"
ECHO_TAIL_SECONDS = 1.0
ECHO_MEMORY_SECONDS = 3.0
_RECO_EVENTS_IID = "{7B8FCB42-0E9D-4F00-A048-7B04D6179D3D}"


def _text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return ""
    # Bound work before Unicode filtering; neither metadata nor user text is
    # interpreted as XML, a path, markup, an executable, or a system command.
    value = value[: limit * 4]
    return " ".join("".join(c for c in value if c.isprintable() or c.isspace()).split())[:limit]


def _confidence(value):
    try:
        value = float(value)
        return max(0.0, min(1.0, value)) if math.isfinite(value) else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


def _languages(token):
    try:
        raw = token.GetAttribute("Language")
    except Exception:
        return []
    if not isinstance(raw, str):
        return []
    result = []
    for part in raw[:256].split(";")[:16]:
        try:
            lcid = int(part.strip(), 16)
        except ValueError:
            continue
        if 0 < lcid <= 0xFFFF:
            result.append((lcid, locale.windows_locale.get(lcid, "lcid-%04x" % lcid).replace("_", "-")))
    return result


def _description(token):
    try:
        return _text(token.GetDescription(), MAX_LABEL)
    except Exception:
        return "Installed SAPI token"


def _loopback(label):
    return any(word in label.casefold() for word in
               ("stereo mix", "stereomix", "what u hear", "what you hear", "loopback", "wave out", "立体声混音"))


def _items(collection):
    # Do not iterate a potentially unbounded/malformed COM enumerator.
    count = max(0, min(MAX_TOKENS, int(collection.Count)))
    for index in range(count):
        try:
            yield collection.Item(index)
        except Exception:
            continue


def _empty_capabilities(status="not_probed"):
    return {
        "engine_kind": ENGINE_KIND, "status": status, "available": False,
        "recognition_available": False, "chinese_available": False,
        "tts_available": False, "continuous": False, "barge_in": False,
        "full_duplex": False, "echo_cancellation": False,
        "requires_command_confirmation": True, "max_text_chars": MAX_TEXT,
        "confidence_floor": CONFIDENCE_FLOOR, "confidence_kind": CONFIDENCE_KIND,
        "confidence_calibrated": False, "recognizer_count": 0,
        "voice_count": 0, "audio_input_count": 0, "languages": [],
        "recognizers": [], "voices": [], "audio_inputs": [],
    }


class _RecoSink:
    """SAPI's documented outgoing dispinterface, without generated wrappers."""
    _public_methods_ = ["StartStream", "EndStream", "SoundStart", "SoundEnd",
                        "PhraseStart", "Recognition", "Hypothesis", "FalseRecognition"]

    def __init__(self, emit, dispatch):
        self._dispid_to_func_ = {
            1: "StartStream", 2: "EndStream", 4: "SoundStart", 5: "SoundEnd",
            6: "PhraseStart", 7: "Recognition", 8: "Hypothesis", 11: "FalseRecognition",
        }
        self.emit = emit
        self.dispatch = dispatch

    def _query_interface_(self, iid):
        # An outgoing dispinterface has no native pywin32 vtable gateway.
        # Return an IDispatch pointer, not integer 1/_com_interfaces_, exactly
        # as generated pywin32 event sinks do, but without generating a cache.
        if str(iid).upper() == _RECO_EVENTS_IID:
            from win32com.server.util import wrap
            return wrap(self)
        return None

    def StartStream(self, *args):
        pass

    def EndStream(self, *args):
        pass

    def SoundStart(self, *args):
        self.emit("sound_start", "", 0.0)

    def SoundEnd(self, *args):
        pass

    def PhraseStart(self, *args):
        self.emit("phrase_start", "", 0.0)

    def FalseRecognition(self, *args):
        # Never turn rejected speech into a command or a fake transcript.
        self.emit("rejected", "", 0.0)

    def _result(self, kind, result):
        try:
            result = self.dispatch(result)
            info = result.PhraseInfo
            text = _text(info.GetText(0, -1, True))
            rule = info.Rule
            try:
                confidence = _confidence(rule.EngineConfidence)
            except Exception:
                # Never invent a numerical engine score from the categorical
                # low/normal/high enum.  Missing scores fail the final floor.
                confidence = 0.0
            self.emit(kind, text, confidence)
        except Exception:
            # COM exceptions can contain recognized text/device paths: no repr.
            self.emit("recognition_error", "", 0.0)

    def Recognition(self, stream_number, stream_position, recognition_type, result):
        self._result("final", result)

    def Hypothesis(self, stream_number, stream_position, result):
        self._result("partial", result)

    def close(self):
        self.emit = lambda *args: None
        self.dispatch = None


class _SapiBackend:
    """Only constructed/called/released from the one engine worker apartment."""
    def __init__(self):
        self.pythoncom = None
        self.dispatch = None
        self.wrap = None
        self.event_iid = None
        self.recognizer = self.context = self.grammar = None
        self.connection = self.cookie = self.sink = self.gateway = None
        self.voice = None

    def initialize(self):
        if sys.platform != "win32":
            return _empty_capabilities("unsupported_platform")
        try:
            import pythoncom
            import pywintypes
            from win32com.client import dynamic
            from win32com.server.util import wrap
        except ImportError:
            return _empty_capabilities("pywin32_unavailable")
        pythoncom.CoInitialize()
        self.pythoncom, self.dispatch, self.wrap = pythoncom, dynamic.Dispatch, wrap
        self.event_iid = pywintypes.IID(_RECO_EVENTS_IID)
        return self.probe()

    def probe(self):
        caps = _empty_capabilities("sapi_unavailable")
        recognizer = voice = None
        try:
            recognizer = self.dispatch("SAPI.SpInprocRecognizer")
            for token in _items(recognizer.GetRecognizers()):
                langs = _languages(token)
                caps["recognizers"].append({"name": _description(token),
                    "languages": [name for _, name in langs],
                    "chinese": any(lcid & 0x3FF == 4 for lcid, _ in langs)})
            for token in _items(recognizer.GetAudioInputs()):
                name = _description(token)
                caps["audio_inputs"].append({"name": name, "eligible": not _loopback(name)})
        except Exception:
            pass
        finally:
            recognizer = None
        try:
            voice = self.dispatch("SAPI.SpVoice")
            for token in _items(voice.GetVoices()):
                caps["voices"].append({"name": _description(token),
                                        "languages": [name for _, name in _languages(token)]})
        except Exception:
            pass
        finally:
            voice = None
        caps["recognizer_count"] = len(caps["recognizers"])
        caps["voice_count"] = len(caps["voices"])
        caps["audio_input_count"] = len(caps["audio_inputs"])
        caps["languages"] = sorted({lang for row in caps["recognizers"] for lang in row["languages"]})[:MAX_TOKENS]
        caps["chinese_available"] = any(row["chinese"] for row in caps["recognizers"])
        caps["recognition_available"] = bool(caps["chinese_available"] and any(row["eligible"] for row in caps["audio_inputs"]))
        caps["available"] = caps["continuous"] = caps["recognition_available"]
        caps["tts_available"] = bool(caps["voices"])
        caps["status"] = ("ready" if caps["recognition_available"] else
                          "no_chinese_recognizer" if not caps["chinese_available"] else "no_audio_input")
        return caps

    def start(self, emit, is_current=lambda: True):
        self.stop()
        try:
            self.recognizer = self.dispatch("SAPI.SpInprocRecognizer")
            self.recognizer.State = 0  # SRSInactive before attaching input.
            chinese = [token for token in _items(self.recognizer.GetRecognizers())
                       if any(lcid & 0x3FF == 4 for lcid, _ in _languages(token))]
            chinese.sort(key=lambda token: not any(lcid == 0x804 for lcid, _ in _languages(token)))
            if not chinese:
                raise RuntimeError("no_chinese_recognizer")
            self.recognizer.Recognizer = chinese[0]
            inputs = [token for token in _items(self.recognizer.GetAudioInputs()) if not _loopback(_description(token))]
            if not inputs:
                raise RuntimeError("no_audio_input")
            self.recognizer.AudioInput = inputs[0]
            self.context = self.dispatch(self.recognizer.CreateRecoContext())
            self.context.State = 0  # Disabled until event sink + grammar ready.
            self.context.RetainedAudio = 0  # SRAONone: never retain audio in results.
            self.context.EventInterests = 1 | 2 | 4 | 8 | 16 | 32 | 512 | 16384
            self.sink = _RecoSink(emit, self.dispatch)
            self.gateway = self.wrap(self.sink)
            container = self.context._oleobj_.QueryInterface(self.pythoncom.IID_IConnectionPointContainer)
            self.connection = container.FindConnectionPoint(self.event_iid)
            self.cookie = self.connection.Advise(self.gateway)
            self.grammar = self.dispatch(self.context.CreateGrammar(1))
            self.grammar.DictationLoad("", 0)  # Static, local installed dictation.
            self.grammar.DictationSetState(1)
            if not is_current():
                self.stop()
                return False
            self.context.State = 1
            self.recognizer.State = 1  # This is the ONLY capture activation.
            return True
        except Exception:
            self.stop()
            raise

    def _voice(self):
        if self.voice is None:
            self.voice = self.dispatch("SAPI.SpVoice")
            tokens = list(_items(self.voice.GetVoices()))
            if not tokens:
                self.voice = None
                raise RuntimeError("no_voice")
            chinese = [token for token in tokens if any(lcid & 0x3FF == 4 for lcid, _ in _languages(token))]
            self.voice.Voice = (chinese or tokens)[0]
        return self.voice

    def speak(self, text):
        # Async + purge-before + literal text (never SAPI XML/file flags).
        self._voice().Speak(text, 1 | 2 | 16)

    def is_speaking(self):
        return bool(self.voice is not None and not self.voice.WaitUntilDone(0))

    def interrupt(self):
        if self.voice is not None:
            try:
                self.voice.Speak("", 1 | 2 | 16)
            except Exception:
                pass

    def stop(self):
        # Turn audio off BEFORE unadvising or releasing an in-process engine.
        if self.recognizer is not None:
            try:
                self.recognizer.State = 3  # SRSInactiveWithPurge.
            except Exception:
                pass
        if self.grammar is not None:
            try:
                self.grammar.DictationSetState(0)
            except Exception:
                pass
        if self.context is not None:
            try:
                self.context.State = 0
            except Exception:
                pass
        if self.connection is not None and self.cookie is not None:
            try:
                self.connection.Unadvise(self.cookie)
            except Exception:
                pass
        if self.sink is not None:
            self.sink.close()
        self.connection = self.cookie = self.gateway = self.sink = None
        self.grammar = self.context = self.recognizer = None
        self.interrupt()
        self.voice = None

    def pump(self):
        if self.pythoncom is not None:
            self.pythoncom.PumpWaitingMessages()

    def close(self):
        self.stop()
        if self.pythoncom is not None:
            pythoncom, self.pythoncom = self.pythoncom, None
            self.dispatch = self.wrap = None
            pythoncom.CoUninitialize()


class VoiceEngine:
    def __init__(self, callback):
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback
        self._condition = threading.Condition(threading.RLock())
        self._commands = deque(maxlen=MAX_QUEUE)
        self._ready = threading.Event()
        self._thread = None
        self._closed = False
        self._generation = 0
        self._requested = self._listening = False
        self._caps = _empty_capabilities()
        self._backend = None  # Used by worker only; never expose COM to caller.
        self._speech_active = False
        self._speech_generation = 0
        self._speech_serial = 0
        self._active_serial = 0
        self._echo_until = 0.0
        self._recent_tts = deque(maxlen=3)
        self._utterance_blocked = False

    @property
    def generation(self):
        with self._condition:
            return self._generation

    def _ensure_worker(self):
        with self._condition:
            if self._closed:
                return False
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="local-sapi-voice", daemon=True)
                self._thread.start()
            return True

    def capabilities(self):
        """Read-only installed-token query; never assigns audio or plays TTS.

        First call waits at most three seconds for worker enumeration; a slow
        COM installation returns status='probing', not a false ready claim.
        Results are defensive copies with bounded, sanitized display metadata.
        """
        if self._ensure_worker():
            if threading.current_thread() is not self._thread:
                self._ready.wait(3.0)
        with self._condition:
            result = copy.deepcopy(self._caps)
            if self._closed:
                result["status"] = "closed"
            elif not self._ready.is_set():
                result["status"] = "probing"
            return result

    def start(self):
        caps = self.capabilities()
        with self._condition:
            if self._closed or not caps["recognition_available"]:
                return False
            if self._requested:
                return True
            self._generation += 1
            self._requested = True
            self._commands.clear()
            self._commands.append(("start", self._generation, None))
            self._condition.notify()
            return True

    def stop(self):
        """Invalidate stale results now; worker disables input and releases COM."""
        with self._condition:
            if self._closed:
                return
            self._generation += 1
            self._speech_serial += 1
            self._requested = self._listening = False
            self._commands.clear()
            if self._thread is not None:
                self._commands.append(("stop", self._generation, None))
                self._condition.notify()

    def speak(self, text):
        text = _text(text)
        if not text:
            return False
        caps = self.capabilities()
        with self._condition:
            if self._closed or not caps["tts_available"]:
                return False
            self._speech_serial += 1
            # Latest utterance wins.  RAM and COM audio queues cannot grow.
            self._commands = deque((cmd for cmd in self._commands if cmd[0] not in ("speak", "interrupt")), maxlen=MAX_QUEUE)
            self._commands.append(("speak", self._generation, (self._speech_serial, text)))
            self._condition.notify()
            return True

    def interrupt(self):
        """Cancel queued/current TTS explicitly; sound events never do this."""
        with self._condition:
            if self._closed:
                return
            self._speech_serial += 1
            self._commands = deque((cmd for cmd in self._commands if cmd[0] not in ("speak", "interrupt")), maxlen=MAX_QUEUE)
            if self._thread is not None:
                self._commands.appendleft(("interrupt", self._generation, None))
                self._condition.notify()

    def close(self):
        with self._condition:
            if not self._closed:
                self._closed = True
                self._generation += 1
                self._speech_serial += 1
                self._requested = self._listening = False
                self._callback = None
                self._commands.clear()
                self._commands.append(("close", self._generation, None))
                self._condition.notify()
            thread = self._thread
        if thread is not None and threading.current_thread() is not thread:
            thread.join(2.0)

    def _emit(self, kind, generation, **fields):
        # Serialize callback delivery against stop/close.  No old-generation
        # callback can BEGIN after stop returns.  Callback exceptions are private.
        with self._condition:
            if self._closed or generation != self._generation:
                return
            event = {"kind": kind, "engine_kind": ENGINE_KIND,
                     "generation": generation, "confidence": 0.0,
                     "confidence_kind": CONFIDENCE_KIND, "confidence_calibrated": False}
            event.update(fields)
            try:
                self._callback(event)
            except Exception:
                pass

    @staticmethod
    def _normalized(text):
        return "".join(char.casefold() for char in text[:MAX_TEXT] if char.isalnum())

    def _echo_like(self, text):
        now = time.monotonic()
        candidate = self._normalized(text)
        if len(candidate) < 2:
            return False
        grams = {candidate[i:i + 2] for i in range(len(candidate) - 1)}
        for utterance, deadline in self._recent_tts:
            if deadline < now:
                continue
            if candidate in utterance:
                return True
            other = {utterance[i:i + 2] for i in range(len(utterance) - 1)}
            overlap = len(grams & other)
            if overlap >= 2 and overlap / max(1, len(grams)) >= 0.70:
                return True
        return False

    def _on_recognition(self, generation, kind, text="", confidence=0.0):
        with self._condition:
            if self._closed or generation != self._generation or not self._requested:
                return
            blocked = self._speech_active or time.monotonic() < self._echo_until
            if kind in ("sound_start", "phrase_start"):
                self._utterance_blocked = blocked
                return
            if kind == "rejected":
                self._utterance_blocked = False
                self._emit("rejected", generation, code="not_recognized", confidence=_confidence(confidence))
                return
            if kind == "recognition_error":
                self._emit("error", generation, code="recognition_failed")
                return
            if kind not in ("partial", "final"):
                return
            suppress = blocked or self._utterance_blocked
            if kind == "final":
                self._utterance_blocked = False
            text, confidence = _text(text), _confidence(confidence)
            if suppress or not text or self._echo_like(text):
                if kind == "final":
                    self._emit("rejected", generation, confidence=confidence,
                               code="echo_suppressed" if suppress or text else "not_recognized")
                return
            if kind == "final" and confidence < CONFIDENCE_FLOOR:
                self._emit("rejected", generation, code="low_score", confidence=confidence)
                return
            self._emit(kind, generation, text=text, confidence=confidence,
                       requires_confirmation=True, source="microphone_unverified")

    def _finish_speech(self, reason):
        if self._speech_active:
            self._speech_active = False
            now = time.monotonic()
            self._echo_until = now + ECHO_TAIL_SECONDS
            if self._recent_tts:
                utterance, _ = self._recent_tts.pop()
                self._recent_tts.append((utterance, now + ECHO_MEMORY_SECONDS))
            self._emit("speech_end", self._speech_generation, reason=reason)

    def _run(self):
        backend = None
        try:
            backend = self._backend = _SapiBackend()
            try:
                caps = backend.initialize()
            except Exception:
                caps = _empty_capabilities("sapi_unavailable")
            with self._condition:
                self._caps = caps
                self._ready.set()
            while True:
                with self._condition:
                    if not self._commands:
                        self._condition.wait(0.02)
                    command = self._commands.popleft() if self._commands else None
                if command:
                    kind, generation, payload = command
                    if kind == "close":
                        break
                    with self._condition:
                        current = not self._closed and generation == self._generation
                    if not current:
                        continue
                    try:
                        if kind in ("start", "stop"):
                            self._finish_speech("stopped")
                            backend.stop()
                            self._recent_tts.clear()
                            self._echo_until = 0.0
                            self._utterance_blocked = False
                            if kind == "start":
                                def is_current(g=generation):
                                    with self._condition:
                                        return not self._closed and g == self._generation and self._requested
                                started = backend.start(
                                    lambda event, text="", confidence=0.0, g=generation:
                                    self._on_recognition(g, event, text, confidence), is_current)
                                if not started:
                                    continue
                            with self._condition:
                                if generation == self._generation:
                                    self._listening = kind == "start"
                            self._emit("listening", generation, listening=kind == "start")
                            self._emit("status", generation, status="listening" if kind == "start" else "stopped")
                        elif kind == "interrupt":
                            backend.interrupt()
                            self._finish_speech("interrupted")
                        elif kind == "speak":
                            serial, text = payload
                            with self._condition:
                                valid = serial == self._speech_serial and generation == self._generation and not self._closed
                            if valid:
                                backend.interrupt()
                                self._finish_speech("replaced")
                                self._speech_generation = generation
                                self._active_serial = serial
                                # Establish the echo barrier BEFORE COM can emit.
                                self._speech_active = True
                                self._recent_tts.append((self._normalized(text), float("inf")))
                                backend.speak(text)
                                with self._condition:
                                    still_valid = (serial == self._speech_serial and
                                                   generation == self._generation and not self._closed)
                                if still_valid:
                                    self._emit("speech_start", generation)
                                else:
                                    backend.interrupt()
                                    self._finish_speech("interrupted")
                            payload = text = None
                    except Exception:
                        backend.stop()
                        self._finish_speech("error")
                        with self._condition:
                            if generation == self._generation:
                                self._requested = self._listening = False
                        self._emit("listening", generation, listening=False)
                        self._emit("error", generation, code="start_failed" if kind == "start" else "speech_failed")
                try:
                    backend.pump()
                    if self._speech_active and not backend.is_speaking():
                        self._finish_speech("completed")
                except Exception:
                    backend.stop()
                    self._finish_speech("error")
                    with self._condition:
                        generation = self._generation
                        self._requested = self._listening = False
                    self._emit("listening", generation, listening=False)
                    self._emit("error", generation, code="audio_failed")
        finally:
            if backend is not None:
                backend.close()
            self._backend = None
            self._recent_tts.clear()
            self._speech_active = False
            self._ready.set()
