"""Hermetic Realtime protocol tests: fake PCM only, never devices or external APIs."""
import base64
import json
import queue
import socket
import threading
import time
import unittest
from unittest.mock import patch

from realtime_transport import (
    MAX_AUDIO_BASE64, MAX_MESSAGE_BYTES, MAX_QUEUED_AUDIO,
    RealtimeTransport, SYSTEM_PROMPT,
)

try:
    from websockets.sync.server import serve
except ImportError:
    serve = None

PCM = b"\x01\x00" * 2400
CONFIG = dict(endpoint="wss://approved.invalid/v1/realtime", model="gpt-realtime-test",
              key="private-key-never-in-events", voice="marin", max_tokens=128,
              session_seconds=60, protocol="ga")


class Events:
    def __init__(self):
        self.rows = []
        self.cv = threading.Condition()

    def __call__(self, event):
        with self.cv:
            self.rows.append(event)
            self.cv.notify_all()

    def wait(self, kind, count=1, timeout=2):
        deadline = time.monotonic() + timeout
        with self.cv:
            while sum(row["kind"] == kind for row in self.rows) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"missing {kind}: {[row['kind'] for row in self.rows]}")
                self.cv.wait(remaining)
            return [row for row in self.rows if row["kind"] == kind][-1]

    def kinds(self, kind):
        with self.cv:
            return [row for row in self.rows if row["kind"] == kind]


class FakeWS:
    def __init__(self, ack=True):
        self.inbound = queue.Queue()
        self.sent = []
        self.cv = threading.Condition()
        self.closed = threading.Event()
        self.ack = ack
        self.block_append = None
        self.append_entered = threading.Event()

    def push(self, row):
        self.inbound.put(json.dumps(row) if isinstance(row, dict) else row)

    def send(self, raw):
        row = json.loads(raw)
        if row["type"] == "input_audio_buffer.append":
            self.append_entered.set()
            if self.block_append is not None:
                self.block_append.wait(2)
        if self.closed.is_set():
            raise OSError("secret-error-detail")
        with self.cv:
            self.sent.append(row)
            self.cv.notify_all()
        if row["type"] == "session.update" and self.ack:
            self.push({"type": "session.updated", "session": {"model": row["session"]["model"]}})

    def recv(self, timeout):
        if self.closed.is_set():
            raise OSError("secret-error-detail")
        try:
            row = self.inbound.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError() from None
        if row is None:
            raise OSError("closed")
        return row

    def close(self):
        self.closed.set()
        if self.block_append:
            self.block_append.set()
        self.inbound.put(None)

    def wait_sent(self, kind, count=1):
        deadline = time.monotonic() + 2
        with self.cv:
            while sum(row["type"] == kind for row in self.sent) < count:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"missing send {kind}")
                self.cv.wait(left)
            return [row for row in self.sent if row["type"] == kind][-1]


class Connector:
    def __init__(self, ws):
        self.ws = ws
        self.calls = []

    def __call__(self, uri, **kwargs):
        # Copy headers because transport deliberately clears its own copy.
        self.calls.append((uri, dict(kwargs, additional_headers=dict(kwargs["additional_headers"]))))
        return self.ws


class RealtimeTransportTests(unittest.TestCase):
    def make_transport(self, ack=True, config=None, clock=None):
        events, ws = Events(), FakeWS(ack)
        connector = Connector(ws)
        transport = RealtimeTransport(events, connector, clock)
        self.addCleanup(transport.close)
        self.assertTrue(transport.start(dict(CONFIG, **(config or {}))))
        ws.wait_sent("session.update")
        if ack:
            events.wait("session_ready")
        return transport, ws, events, connector

    def assistant_audio(self, ws, response="resp_1", item="item_1", pcm=PCM, kind="response.output_audio.delta"):
        ws.push({"type": "response.created", "response": {"id": response}})
        ws.push({"type": kind, "response_id": response, "item_id": item,
                 "content_index": 0, "delta": base64.b64encode(pcm).decode("ascii")})

    def test_ga_exact_shape_headers_and_only_fixed_instructions(self):
        transport, ws, events, connector = self.make_transport(config={"history": "PRIVATE HISTORY", "tools": ["execute"]})
        uri, options = connector.calls[0]
        self.assertEqual(uri, "wss://approved.invalid/v1/realtime?model=gpt-realtime-test")
        self.assertEqual(options["additional_headers"], {"Authorization": "Bearer " + CONFIG["key"]})
        self.assertIsNone(options["proxy"])
        self.assertIsNone(options["compression"])
        self.assertLessEqual(options["open_timeout"], 8)
        self.assertLessEqual(options["max_size"], 1024 * 1024)
        self.assertFalse(options["logger"].propagate)
        payload = ws.sent[0]["session"]
        self.assertEqual(payload["type"], "realtime")
        self.assertEqual(payload["model"], CONFIG["model"])
        self.assertEqual(payload["output_modalities"], ["audio"])
        self.assertEqual(payload["tools"], [])
        self.assertEqual(payload["tool_choice"], "none")
        self.assertEqual(payload["max_output_tokens"], 128)
        self.assertTrue(payload["instructions"].startswith(SYSTEM_PROMPT))
        self.assertNotIn("PRIVATE HISTORY", json.dumps(payload))
        audio = payload["audio"]
        self.assertEqual(audio["input"]["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertEqual(audio["output"]["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertEqual(audio["output"]["voice"], "marin")
        self.assertEqual(audio["input"]["transcription"]["model"], "gpt-4o-mini-transcribe")
        self.assertEqual(audio["input"]["turn_detection"], {
            "type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300,
            "silence_duration_ms": 500, "create_response": True, "interrupt_response": True})
        self.assertEqual(len(ws.sent), 1)  # No greeting, response.create, or microphone input.
        self.assertFalse(transport.interrupt())  # Idle speech is not an audited interruption.
        self.assertTrue(transport.ready)
        self.assertTrue(transport.can_send_audio)
        self.assertNotIn(CONFIG["key"], str(events.rows))
        self.assertNotIn(CONFIG["endpoint"], str(events.rows))

    def test_beta_shape_header_and_legacy_audio_event(self):
        _, ws, events, connector = self.make_transport(config={"protocol": "beta", "voice": "alloy",
                                                                  "transcription_model": "whisper-1"})
        session = ws.sent[0]["session"]
        self.assertEqual(session["input_audio_format"], "pcm16")
        self.assertEqual(session["output_audio_format"], "pcm16")
        self.assertEqual(session["modalities"], ["audio", "text"])
        self.assertEqual(session["voice"], "alloy")
        self.assertEqual(session["input_audio_transcription"], {"model": "whisper-1"})
        self.assertEqual(connector.calls[0][1]["additional_headers"]["OpenAI-Beta"], "realtime=v1")
        self.assistant_audio(ws, kind="response.audio.delta")
        self.assertEqual(events.wait("audio_delta")["pcm"], PCM)

    def test_created_is_not_configuration_ack_and_audio_is_not_buffered(self):
        transport, ws, events, _ = self.make_transport(ack=False)
        ws.push({"type": "session.created", "session": {"model": CONFIG["model"]}})
        self.assertFalse(transport.ready)
        self.assertFalse(transport.send_audio(PCM))
        ws.push({"type": "session.updated", "session": {}})  # Compatible server may omit model.
        events.wait("session_ready")
        self.assertEqual(len(ws.sent), 1)
        self.assertTrue(transport.send_audio(PCM))
        self.assertEqual(events.wait("audio_sent")["bytes"], len(PCM))
        self.assertEqual(base64.b64decode(ws.wait_sent("input_audio_buffer.append")["audio"]), PCM)

    def test_reported_different_model_or_tools_fails_closed(self):
        for session, code in (({"model": "other"}, "model_mismatch"),
                              ({"tools": [{"type": "function"}]}, "tools_not_disabled"),
                              ({"tool_choice": "auto"}, "tools_not_disabled")):
            with self.subTest(code=code, session=session):
                transport, ws, events, _ = self.make_transport(ack=False)
                ws.push({"type": "session.updated", "session": session})
                self.assertEqual(events.wait("error")["code"], code)
                self.assertFalse(transport.send_audio(PCM))
                self.assertFalse(events.kinds("session_ready"))
                transport.stop()

    def test_ga_ack_rejects_explicit_wrong_pcm_rate_or_malformed_format_before_ready(self):
        invalid = [{"audio": None}, {"audio": {"input": None}}, {"audio": {"output": []}}]
        for direction in ("input", "output"):
            for fmt in ({"type": "audio/pcmu"}, {"rate": 8000}, {"rate": 24000.0},
                        {"rate": True}, {"type": None}, None, "pcm16"):
                invalid.append({"audio": {direction: {"format": fmt}}})
        for session in invalid:
            with self.subTest(session=session):
                transport, ws, events, _ = self.make_transport(ack=False)
                ws.push({"type": "session.updated", "session": session})
                self.assertEqual(events.wait("error")["code"], "audio_format_mismatch")
                self.assertFalse(transport.ready)
                self.assertFalse(transport.send_audio(PCM))
                self.assertFalse(events.kinds("session_ready"))
                transport.stop()

    def test_beta_ack_rejects_explicit_wrong_formats_or_voice_before_ready(self):
        invalid = [({"input_audio_format": "g711_ulaw"}, "audio_format_mismatch"),
                   ({"output_audio_format": "g711_alaw"}, "audio_format_mismatch"),
                   ({"output_audio_format": None}, "audio_format_mismatch"),
                   ({"voice": "wrong-voice"}, "voice_mismatch")]
        for session, code in invalid:
            with self.subTest(session=session):
                transport, ws, events, _ = self.make_transport(ack=False, config={"protocol": "beta"})
                ws.push({"type": "session.updated", "session": session})
                self.assertEqual(events.wait("error")["code"], code)
                self.assertFalse(transport.ready)
                self.assertFalse(transport.send_audio(PCM))
                self.assertFalse(events.kinds("session_ready"))
                transport.stop()

    def test_ga_ack_rejects_wrong_voice_or_explicit_nonmatching_token_budget(self):
        invalid = [({"audio": {"output": {"voice": "wrong-voice"}}}, "voice_mismatch"),
                   ({"audio": {"output": {"voice": None}}}, "voice_mismatch")]
        invalid.extend(({"max_output_tokens": value}, "token_limit_mismatch")
                       for value in (64, 129, "inf", True, None, 128.0))
        for session, code in invalid:
            with self.subTest(session=session):
                transport, ws, events, _ = self.make_transport(ack=False)
                ws.push({"type": "session.updated", "session": session})
                self.assertEqual(events.wait("error")["code"], code)
                self.assertFalse(transport.ready)
                self.assertFalse(transport.send_audio(PCM))
                self.assertFalse(events.kinds("session_ready"))
                transport.stop()

    def test_ack_allows_matching_full_echoes_and_missing_partial_echoes_for_both_protocols(self):
        cases = [("ga", {}), ("ga", {"audio": {}}),
                 ("ga", {"audio": {"input": {"format": {"type": "audio/pcm"}},
                                    "output": {"format": {"rate": 24000}, "voice": "marin"}}}),
                 ("beta", {}), ("beta", {"input_audio_format": "pcm16", "voice": "marin"}),
                 ("ga", None), ("beta", None)]
        for protocol, echo in cases:
            with self.subTest(protocol=protocol, echo=echo):
                transport, ws, events, _ = self.make_transport(ack=False, config={"protocol": protocol})
                session = ws.sent[0]["session"] if echo is None else echo
                ws.push({"type": "session.updated", "session": session})
                events.wait("session_ready")
                self.assertTrue(transport.ready)
                self.assertFalse(events.kinds("error"))
                transport.stop()

    def test_pcm_bounds_and_backpressure_fail_closed_including_inflight(self):
        transport, ws, events, _ = self.make_transport()
        for invalid in (b"", b"x", PCM + b"\x00\x00", "not bytes", bytearray(PCM)):
            self.assertFalse(transport.send_audio(invalid))
        ws.block_append = threading.Event()
        self.assertTrue(transport.send_audio(PCM))
        self.assertTrue(ws.append_entered.wait(1))
        for _ in range(MAX_QUEUED_AUDIO // len(PCM) - 1):
            self.assertTrue(transport.send_audio(PCM))
        self.assertFalse(transport.send_audio(PCM))
        self.assertEqual(events.wait("error")["code"], "backpressure")
        self.assertTrue(ws.closed.wait(1))
        self.assertFalse(events.kinds("audio_sent"))
        self.assertFalse(transport.send_audio(PCM))

    def test_interrupt_uses_actual_played_boundary_and_suppresses_late_audio(self):
        transport, ws, events, _ = self.make_transport()
        self.assistant_audio(ws, pcm=PCM * 3)
        events.wait("audio_delta")
        self.assertFalse(transport.mark_played("item_1", 301))
        self.assertTrue(transport.mark_played("item_1", 75))
        self.assertFalse(transport.mark_played("item_1", 74))
        self.assertTrue(transport.interrupt())
        cancel = ws.wait_sent("response.cancel")
        truncate = ws.wait_sent("conversation.item.truncate")
        self.assertEqual(cancel, {"type": "response.cancel", "response_id": "resp_1"})
        self.assertEqual(truncate, {"type": "conversation.item.truncate", "item_id": "item_1",
                                    "content_index": 0, "audio_end_ms": 75})
        ws.push({"type": "response.output_audio.delta", "response_id": "resp_1", "item_id": "late_item",
                 "delta": base64.b64encode(PCM).decode()})
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "item_id": "sync", "transcript": "sync"})
        events.wait("transcript")
        self.assertEqual(len(events.kinds("audio_delta")), 1)
        self.assertFalse(transport.mark_played("item_1", 100))
        self.assertFalse(transport.interrupt())
        self.assertEqual(sum(row["type"] == "response.cancel" for row in ws.sent), 1)

    def test_server_vad_cancel_race_is_benign_only_after_actual_local_cancel(self):
        transport, ws, events, connector = self.make_transport()
        self.assistant_audio(ws)
        events.wait("audio_delta")
        self.assertTrue(transport.interrupt())
        ws.wait_sent("response.cancel")
        ws.push({"type": "error", "error": {"code": "response_cancel_not_active", "message": "raw-secret"}})
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        events.wait("speech_stopped")
        self.assertTrue(transport.ready)
        self.assertFalse(events.kinds("error"))
        self.assertEqual(len(connector.calls), 1)
        self.assertNotIn("raw-secret", str(events.rows))
        other, other_ws, other_events, _ = self.make_transport()
        other_ws.push({"type": "error", "error": {"code": "response_cancel_not_active"}})
        self.assertEqual(other_events.wait("error")["code"], "server_error")
        self.assertFalse(other.ready)

    def test_completed_response_cannot_be_reactivated_by_duplicate_created_or_audio(self):
        transport, ws, events, _ = self.make_transport()
        self.assistant_audio(ws)
        events.wait("audio_delta")
        self.assertTrue(transport.mark_played("item_1", 100))
        ws.push({"type": "response.done", "response": {"id": "resp_1", "status": "completed"}})
        events.wait("response_done")
        self.assistant_audio(ws)
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        events.wait("speech_stopped")
        self.assertFalse(transport.interrupt())
        self.assertEqual(len(events.kinds("audio_delta")), 1)
        self.assertFalse(any(row["type"] == "response.cancel" for row in ws.sent))

    def test_no_heard_claim_without_mark_and_post_done_playback_can_truncate(self):
        transport, ws, events, _ = self.make_transport()
        self.assistant_audio(ws)
        events.wait("audio_delta")
        ws.push({"type": "response.done", "response": {"id": "resp_1", "status": "completed"}})
        events.wait("response_done")
        transport.interrupt()
        self.assertEqual(ws.wait_sent("conversation.item.truncate")["audio_end_ms"], 0)
        self.assertFalse(any(row["type"] == "response.cancel" for row in ws.sent))

    def test_speech_notice_leaves_interrupt_to_owner_after_playback_accounting(self):
        transport, ws, events, _ = self.make_transport()
        self.assistant_audio(ws)
        events.wait("audio_delta")
        ws.push({"type": "input_audio_buffer.speech_started"})
        token = events.wait("speech_started")["barge_token"]
        self.assertFalse(any(row["type"] == "response.cancel" for row in ws.sent))
        self.assertTrue(transport.mark_played("item_1", 90))
        self.assertTrue(transport.interrupt(barge_token=token))
        self.assertEqual(ws.wait_sent("conversation.item.truncate")["audio_end_ms"], 90)

    def test_delayed_gui_barge_targets_only_old_response_and_never_clears_new_reply(self):
        transport, ws, events, _ = self.make_transport()
        self.assistant_audio(ws, response="old_response", item="old_item", pcm=PCM * 3)
        old_audio = events.wait("audio_delta")  # Simulate a callback still queued in Qt.
        self.assertTrue(transport.is_playable(old_audio["item_id"]))
        self.assertTrue(transport.is_audio_current(old_audio["item_id"]))
        ws.push({"type": "input_audio_buffer.speech_started"})
        token = events.wait("speech_started")["barge_token"]
        self.assertFalse(transport.is_playable(old_audio["item_id"]))
        # Network advances two turns while Qt has not handled speech_started.
        ws.push({"type": "response.output_audio.delta", "response_id": "old_response", "item_id": "late_old",
                 "delta": base64.b64encode(PCM).decode()})
        self.assistant_audio(ws, response="new_response", item="new_item")
        new_audio = events.wait("audio_delta", count=2)
        self.assertEqual(new_audio["response_id"], "new_response")
        self.assertTrue(transport.is_playable("new_item"))
        # Parent now reports old playback progress/clears Qt, consumes the token.
        self.assertTrue(transport.mark_played("old_item", 75))
        clears = len(events.kinds("audio_clear"))
        self.assertTrue(transport.interrupt(barge_token=token))
        self.assertEqual(ws.wait_sent("response.cancel")["response_id"], "old_response")
        truncate = ws.wait_sent("conversation.item.truncate")
        self.assertEqual(truncate["item_id"], "old_item")
        self.assertEqual(truncate["audio_end_ms"], 75)
        self.assertFalse(transport.interrupt(barge_token=token))  # One-use.
        self.assertFalse(transport.interrupt(barge_token="unknown_token"))
        self.assertTrue(transport.is_playable("new_item"))
        self.assertEqual(len(events.kinds("audio_clear")), clears)
        self.assertEqual(len(events.kinds("audio_delta")), 2)  # late_old was refused.
        self.assertFalse(any(row.get("response_id") == "new_response" for row in ws.sent))
        self.assertFalse(any(row.get("item_id") == "new_item" for row in ws.sent))
        # A deliberate manual click still cancels the NEW current response.
        self.assertTrue(transport.interrupt())
        self.assertEqual(ws.wait_sent("response.cancel", count=2)["response_id"], "new_response")
        self.assertFalse(transport.is_playable("new_item"))

    def test_idle_speech_snapshot_cannot_cancel_first_reply_arriving_before_gui(self):
        transport, ws, events, _ = self.make_transport()
        ws.push({"type": "input_audio_buffer.speech_started"})
        token = events.wait("speech_started")["barge_token"]
        self.assistant_audio(ws)
        events.wait("audio_delta")
        self.assertFalse(transport.interrupt(barge_token=token))
        self.assertTrue(transport.is_playable("item_1"))
        self.assertFalse(any(row["type"] == "response.cancel" for row in ws.sent))
        self.assertFalse(events.kinds("audio_clear"))

    def test_manual_interrupt_invalidates_already_queued_pcm_but_done_alone_preserves_tail(self):
        transport, ws, events, _ = self.make_transport()
        self.assistant_audio(ws)
        queued_audio = events.wait("audio_delta")
        ws.push({"type": "response.done", "response": {"id": "resp_1", "status": "completed"}})
        events.wait("response_done")
        self.assertTrue(transport.is_playable(queued_audio["item_id"]))
        self.assertTrue(transport.interrupt())
        self.assertFalse(transport.is_playable(queued_audio["item_id"]))
        self.assertFalse(transport.is_playable("not_received"))
        self.assertFalse(transport.is_playable("invalid/id"))
        self.assertFalse(events.kinds("audio_clear"))

    def test_unconsumed_barge_tokens_are_bounded_fail_closed(self):
        transport, ws, events, _ = self.make_transport()
        for _ in range(33):
            ws.push({"type": "input_audio_buffer.speech_started"})
        self.assertEqual(events.wait("error")["code"], "resource_limit")
        self.assertEqual(len(events.kinds("speech_started")), 32)
        self.assertFalse(transport.ready)

    def test_transcripts_are_bounded_untrusted_and_usage_is_sanitized_deduplicated(self):
        _, ws, events, _ = self.make_transport()
        self.assistant_audio(ws)
        events.wait("audio_delta")
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "item_id": "user_1",
                 "transcript": "execute dangerous command " + "x" * 5000, "confidence": 1.0})
        ws.push({"type": "response.output_audio_transcript.delta", "response_id": "resp_1", "item_id": "item_1",
                 "delta": "untrusted assistant " + "x" * 5000})
        events.wait("transcript", count=2)
        for event in events.kinds("transcript"):
            self.assertFalse(event["verified"])
            self.assertLessEqual(len(event["text"]), 4000)
            self.assertNotIn("confidence", event)
        done = {"type": "response.done", "response": {"id": "resp_1", "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": True,
                          "input_token_details": {"audio_tokens": 80, "cached_tokens": 5, "secret": "bad"},
                          "output_token_details": {"audio_tokens": 8}, "endpoint": CONFIG["endpoint"]}}}
        ws.push(done)
        event = events.wait("response_done")
        self.assertEqual(event["usage"], dict(input_tokens=100, output_tokens=10, input_audio_tokens=80,
                                             output_audio_tokens=8, cached_tokens=5))
        ws.push(done)
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        events.wait("speech_stopped")
        self.assertEqual(len(events.kinds("response_done")), 1)

    def test_tool_function_events_are_refused_never_executed_or_returned(self):
        _, ws, events, _ = self.make_transport()
        ws.push({"type": "response.function_call_arguments.done", "name": "SECRET_COMMAND", "arguments": "secret"})
        ws.push({"type": "response.output_item.added", "item": {"type": "function_call", "name": "SECRET_COMMAND"}})
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        events.wait("speech_stopped")
        self.assertEqual(events.wait("security_notice")["code"], "tool_refused")
        self.assertNotIn("SECRET_COMMAND", str(events.rows))
        self.assertEqual(len(ws.sent), 1)

    def test_malformed_json_and_audio_frames_fail_closed_without_raw_details(self):
        invalid = ['{"type":"session.updated","type":"error"}',
                   '{"type":"noise","x":NaN}', '{"type":"noise","x":Infinity}',
                   '{"type":"noise","x":1e999}',
                   '{"type":"noise","x":' + '[' * 20 + '0' + ']' * 20 + '}',
                   b"binary frame", "x" * (MAX_MESSAGE_BYTES + 1)]
        for raw in invalid:
            with self.subTest(raw_type=type(raw), length=len(raw)):
                transport, ws, events, _ = self.make_transport()
                ws.push(raw)
                self.assertEqual(events.wait("error")["code"], "protocol_error")
                self.assertFalse(transport.ready)
                transport.stop()
        for encoded in ("!!!!", "YQ==", "A" * (MAX_AUDIO_BASE64 + 4), "AB=="):
            with self.subTest(encoded_length=len(encoded)):
                transport, ws, events, _ = self.make_transport()
                ws.push({"type": "response.created", "response": {"id": "r"}})
                ws.push({"type": "response.output_audio.delta", "response_id": "r", "item_id": "i", "delta": encoded})
                self.assertEqual(events.wait("error")["code"], "protocol_error")
                self.assertFalse(events.kinds("audio_delta"))
                transport.stop()

    def test_invalid_ids_are_rejected_and_unknown_response_audio_is_ignored(self):
        _, ws, events, _ = self.make_transport()
        ws.push({"type": "response.output_audio.delta", "response_id": "unknown", "item_id": "item",
                 "delta": base64.b64encode(PCM).decode()})
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        events.wait("speech_stopped")
        self.assertFalse(events.kinds("audio_delta"))
        ws.push({"type": "response.created", "response": {"id": "unsafe/id"}})
        self.assertEqual(events.wait("error")["code"], "protocol_error")

    def test_rate_failure_has_no_retry_no_raw_message_and_no_more_input(self):
        transport, ws, events, connector = self.make_transport()
        ws.push({"type": "error", "error": {"code": "rate_limit_exceeded", "message": "secret-error-detail"}})
        self.assertEqual(events.wait("error")["code"], "rate_limited")
        self.assertFalse(transport.send_audio(PCM))
        self.assertEqual(len(connector.calls), 1)
        self.assertNotIn("secret-error-detail", str(events.rows))

    def test_budget_and_handshake_timeouts_close_even_without_incoming_frames(self):
        for seconds, advance, expected in ((60, 9, "handshake_timeout"), (2, 3, "session_limit")):
            with self.subTest(expected=expected):
                now = [100.0]
                transport, ws, events, _ = self.make_transport(ack=False, config={"session_seconds": seconds}, clock=lambda: now[0])
                now[0] += advance
                event = events.wait("error" if expected == "handshake_timeout" else "limit")
                self.assertEqual(event["code"], expected)
                self.assertTrue(ws.closed.wait(1))
                self.assertFalse(transport.ready)

    def test_duration_watchdog_aborts_blocked_send_without_claiming_audio_sent(self):
        now = [100.0]
        transport, ws, events, _ = self.make_transport(config={"session_seconds": 2}, clock=lambda: now[0])
        ws.block_append = threading.Event()
        self.assertTrue(transport.send_audio(PCM))
        self.assertTrue(ws.append_entered.wait(1))
        now[0] += 3
        self.assertEqual(events.wait("limit")["code"], "session_limit")
        self.assertTrue(ws.closed.wait(1))
        self.assertFalse(events.kinds("audio_sent"))
        self.assertFalse(transport.ready)

    @unittest.skipIf(serve is None, "default connector requires installed websockets")
    def test_localhost_dns_rebinding_is_rejected_before_socket_or_key_send(self):
        events = Events()
        transport = RealtimeTransport(events)
        self.addCleanup(transport.stop)
        rebound = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 80))]
        with patch("realtime_transport.socket.getaddrinfo", return_value=rebound), \
                patch("realtime_transport.socket.socket", side_effect=AssertionError("no socket permitted")):
            self.assertTrue(transport.start(dict(CONFIG, endpoint="ws://localhost/v1/realtime")))
            self.assertEqual(events.wait("error")["code"], "protocol_error")
        self.assertNotIn(CONFIG["key"], str(events.rows))

    def test_stop_cancels_pending_connect_and_ignores_late_old_generation(self):
        events, entered, release = Events(), threading.Event(), threading.Event()
        old_ws, new_ws = FakeWS(), FakeWS()
        calls = []

        def connector(uri, **kwargs):
            calls.append(uri)
            if len(calls) == 1:
                entered.set()
                release.wait(2)
                return old_ws
            return new_ws

        transport = RealtimeTransport(events, connector)
        self.addCleanup(transport.stop)
        self.assertTrue(transport.start(CONFIG))
        self.assertTrue(entered.wait(1))
        old_generation = transport.generation
        transport.stop()
        self.assertGreater(transport.generation, old_generation)
        self.assertFalse(transport.send_audio(PCM))
        self.assertTrue(transport.start(CONFIG))
        events.wait("session_ready")
        release.set()
        self.assertTrue(old_ws.closed.wait(1))
        self.assertEqual(old_ws.sent, [])
        self.assertTrue(all(row["generation"] != old_generation for row in events.rows))
        self.assertTrue(transport.ready)

    def test_stop_drops_queued_audio_invalidates_and_never_reconnects(self):
        transport, ws, events, connector = self.make_transport()
        generation = transport.generation
        transport.stop()
        count = len(events.rows)
        self.assertFalse(transport.send_audio(PCM))
        self.assertFalse(transport.interrupt())
        self.assertGreater(transport.generation, generation)
        ws.push({"type": "session.updated", "session": {}})
        ws.push({"type": "response.created", "response": {"id": "late"}})
        self.assertTrue(ws.closed.wait(1))
        self.assertEqual(len(events.rows), count)
        self.assertEqual(len(connector.calls), 1)
        self.assertEqual(events.rows[-1]["code"], "stopped")

    def test_invalid_config_never_calls_connector_or_sends_key(self):
        for override in ({"endpoint": "https://example.invalid"}, {"endpoint": "ws://remote.invalid"},
                         {"endpoint": "wss://user:pass@example.invalid/path"},
                         {"endpoint": "wss://example.invalid/path?model=wrong"},
                         {"endpoint": "wss://example.invalid/path?"},
                         {"endpoint": "wss://example.invalid/path#"},
                         {"endpoint": "wss://example.invalid/\r\n"}, {"model": "bad\nmodel"},
                         {"key": "key\r\nInjected: bad"}, {"protocol": "automatic"},
                         {"session_seconds": True}, {"max_tokens": 999999}):
            with self.subTest(override=override):
                connector = Connector(FakeWS())
                transport = RealtimeTransport(Events(), connector)
                self.assertFalse(transport.start(dict(CONFIG, **override)))
                self.assertEqual(connector.calls, [])


@unittest.skipIf(serve is None, "installed websockets.sync unavailable; fake tests remain hermetic")
class LoopbackRealtimeTests(unittest.TestCase):
    def server(self, handler, **kwargs):
        server = serve(handler, "127.0.0.1", 0, logger=None, **kwargs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        return server, f"ws://127.0.0.1:{server.socket.getsockname()[1]}/v1/realtime"

    def test_real_websocket_two_turns_fake_pcm_exact_model_voice_and_no_preack_audio(self):
        ack_gate, connected, finished = threading.Event(), threading.Event(), threading.Event()
        captured, failure = {}, []

        def handler(ws):
            try:
                captured["path"] = ws.request.path
                captured["headers"] = dict(ws.request.headers)
                ws.send(json.dumps({"type": "session.created", "session": {"model": "server-default"}}))
                update = json.loads(ws.recv(timeout=2))
                captured["update"] = update
                connected.set()
                if not ack_gate.wait(2):
                    raise AssertionError("ack gate timeout")
                # recv timeout proves that no audio was sent while config unacked.
                try:
                    unexpected = ws.recv(timeout=0.05)
                    raise AssertionError(f"unexpected pre-ack message type: {json.loads(unexpected)['type']}")
                except TimeoutError:
                    pass
                ws.send(json.dumps({"type": "session.updated", "session": {"model": CONFIG["model"], "tool_choice": "none"}}))
                captured["pcm"] = []
                for number in range(2):
                    incoming = json.loads(ws.recv(timeout=2))
                    if incoming["type"] != "input_audio_buffer.append":
                        raise AssertionError("not PCM")
                    captured["pcm"].append(base64.b64decode(incoming["audio"], validate=True))
                    response, item = f"response_{number}", f"item_{number}"
                    rows = [
                        {"type": "input_audio_buffer.speech_started"},
                        {"type": "input_audio_buffer.speech_stopped"},
                        {"type": "conversation.item.input_audio_transcription.completed", "item_id": f"user_{number}", "transcript": f"fake turn {number}"},
                        {"type": "response.created", "response": {"id": response}},
                        {"type": "response.output_audio.delta", "response_id": response, "item_id": item, "content_index": 0,
                         "delta": base64.b64encode(PCM).decode()},
                        {"type": "response.output_audio_transcript.done", "response_id": response, "item_id": item, "transcript": f"reply {number}"},
                        {"type": "response.done", "response": {"id": response, "status": "completed", "usage": {"input_tokens": 20, "output_tokens": 10}}},
                    ]
                    for row in rows:
                        ws.send(json.dumps(row))
                finished.set()
                # Keep socket alive until the transport's explicit stop.
                try:
                    ws.recv(timeout=2)
                except Exception:
                    pass
            except Exception as exc:
                failure.append(type(exc).__name__)
                connected.set()
                finished.set()

        _, endpoint = self.server(handler)
        events = Events()
        transport = RealtimeTransport(events)
        self.addCleanup(transport.stop)
        self.assertTrue(transport.start(dict(CONFIG, endpoint=endpoint)))
        self.assertTrue(connected.wait(2))
        self.assertFalse(failure)
        self.assertFalse(transport.ready)
        self.assertFalse(transport.send_audio(PCM))
        ack_gate.set()
        events.wait("session_ready")
        for number in range(2):
            self.assertTrue(transport.send_audio(PCM))
            events.wait("response_done", number + 1)
            self.assertTrue(transport.mark_played(f"item_{number}", 100))
        self.assertTrue(finished.wait(2))
        self.assertFalse(failure)
        self.assertEqual(captured["pcm"], [PCM, PCM])
        self.assertEqual(captured["path"], "/v1/realtime?model=gpt-realtime-test")
        self.assertEqual(captured["headers"]["authorization"], "Bearer " + CONFIG["key"])
        self.assertNotIn("openai-beta", captured["headers"])
        self.assertEqual(captured["update"]["session"]["audio"]["output"]["voice"], "marin")
        self.assertEqual(len(events.kinds("transcript")), 4)
        self.assertEqual(len(events.kinds("audio_delta")), 2)
        self.assertEqual(sum(event["bytes"] for event in events.kinds("audio_sent")), len(PCM) * 2)
        self.assertNotIn(CONFIG["key"], str(events.rows))
        transport.stop()
        self.assertFalse(transport.send_audio(PCM))

    def test_redirect_is_not_followed_and_auth_never_reaches_other_target(self):
        forwarded = threading.Event()
        _, destination = self.server(lambda ws: forwarded.set())
        visited = []

        def redirect(connection, request):
            visited.append(request.headers.get("Authorization"))
            response = connection.respond(302, "Redirect refused test")
            response.headers["Location"] = destination
            return response

        _, endpoint = self.server(lambda ws: None, process_request=redirect)
        events = Events()
        transport = RealtimeTransport(events)
        self.addCleanup(transport.stop)
        self.assertTrue(transport.start(dict(CONFIG, endpoint=endpoint)))
        # Hosted runners may surface a refused redirect as a closed connection;
        # either way the security property is that no auth reaches the target.
        self.assertIn(events.wait("error")["code"], {"redirect_refused", "connection_failed", "connection_closed"})
        self.assertEqual(visited, ["Bearer " + CONFIG["key"]])
        self.assertFalse(forwarded.is_set())
        self.assertNotIn(destination, str(events.rows))


if __name__ == "__main__":
    unittest.main()
