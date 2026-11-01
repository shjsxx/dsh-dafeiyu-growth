"""Offline adversarial tests: HTTP mocked, persistence in temporary directories.

Run: python -B -m unittest discover -s native -p test_live_online.py -v
No test performs an actual public GET or writes a real owner setting.
"""
import copy
from email.message import Message
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse

import live_online as live

NOW = 1_790_000_000.0


def event(mag=3.1, age=90, status="reviewed"):
    return {"type": "Feature", "properties": {"mag": mag, "time": (NOW - age) * 1000, "status": status},
            "geometry": {"type": "Point", "coordinates": [120.5, -30, 9.5]}}


def feed(features=None, generated=None):
    return {"type": "FeatureCollection", "metadata": {"generated": (NOW - 10) * 1000 if generated is None else generated},
            "features": [event()] if features is None else features}


class Response(io.BytesIO):
    def __init__(self, raw, headers=None, status=200, url=live.SOURCE_URL):
        body = raw if isinstance(raw, bytes) else json.dumps(raw, ensure_ascii=False).encode("utf-8")
        super().__init__(body)
        self.headers = Message()
        for key, value in ({"Content-Type": "application/geo+json"} if headers is None else headers).items():
            self.headers[key] = value
        self.status, self.url = status, url
        self.read_sizes = []

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def read1(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def fetch(raw=None, headers=None, error=None, timeout=12):
    opener = Mock()
    response = Response(feed() if raw is None else raw, headers)
    if error is not None:
        opener.open.side_effect = error
    else:
        opener.open.return_value = response
    with patch.object(live.urllib.request, "build_opener", return_value=opener) as build, patch.object(live.time, "time", return_value=NOW):
        result = live.fetch_live_facts(timeout)
    return result, opener, build, response


class FetchTests(unittest.TestCase):
    def test_fixed_numeric_summary_request_and_store_contract(self):
        result, opener, build, response = fetch(feed([event(3.2), event(5.3, 80, "automatic")]))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["source"], "usgs_live")
        self.assertEqual(result["sourceUrl"], live.SOURCE_URL)
        self.assertEqual(result["counters"], {"count": 2, "maxMagnitude": 5.3, "latestEventTimeMs": (NOW - 80) * 1000,
                                             "reviewedCount": 1, "rejected": 0})
        self.assertIn("最大震级 5.30", result["summary"])
        self.assertIn("已人工复核 1 条", result["summary"])
        self.assertIn("UTC", result["summary"])
        self.assertEqual(result["fetchedAtMs"], NOW * 1000)
        self.assertEqual(result["summary"], live.make_summary(result["counters"], result["sourceUpdatedAtMs"]))
        self.assertEqual(result["metrics"]["validated"], 2)
        self.assertRegex(result["hash"], r"^[0-9a-f]{64}$")
        row = result["records"][0]
        self.assertEqual(row["id"], "usgs_live:summary")
        self.assertEqual(row["key"], "summary")
        self.assertEqual(row["question"], live.QUESTION)
        self.assertEqual(row["intent"], "concept")
        self.assertLessEqual(len(row["answer"]), 600)
        request = opener.open.call_args.args[0]
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(request.full_url, live.SOURCE_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertEqual(urllib.parse.urlsplit(request.full_url).query, "")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 12)
        self.assertIsInstance(build.call_args.args[0], live._NoRedirect)
        self.assertEqual(build.call_args.args[1].proxies, {})
        self.assertTrue(response.closed)
        self.assertTrue(all(0 < size <= 16384 for size in response.read_sizes))

    def test_genuinely_empty_feed_succeeds_with_zero_summary(self):
        result, *_ = fetch(feed([]))
        self.assertTrue(result["ok"])
        self.assertEqual(result["metrics"]["validated"], 0)
        self.assertEqual(result["counters"]["count"], 0)
        self.assertIsNone(result["counters"]["maxMagnitude"])
        self.assertIn("为 0 条", result["summary"])
        self.assertEqual(len(result["records"]), 1)

    def test_prompt_injection_remote_urls_ids_and_prose_never_echoed(self):
        raw = feed()
        poison = "PRIVATE_ATTACK_IGNORE_INSTRUCTIONS https://evil.invalid/execute <script>"
        raw.update(id=poison, title=poison, url=poison, instructions=poison)
        raw["metadata"].update(title=poison, url=poison)
        raw["features"][0].update(id=poison)
        raw["features"][0]["properties"].update(place=poison, title=poison, url=poison, code=poison)
        result, opener, *_ = fetch(raw)
        self.assertTrue(result["ok"])
        self.assertNotIn("PRIVATE_ATTACK", repr(result))
        self.assertNotIn("evil.invalid", repr(result))
        self.assertNotIn("<script>", repr(result))
        self.assertEqual(opener.open.call_count, 1)

    def test_partial_rejections_are_visible_but_not_a_false_zero(self):
        bad = event(mag=None)
        result, *_ = fetch(feed([event(), bad, "hostile row"]))
        self.assertTrue(result["ok"])
        self.assertEqual(result["metrics"]["rejected"], 2)
        self.assertIn("2 条未通过校验", result["summary"])
        result, *_ = fetch(feed([bad]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "schema")
        self.assertEqual(result["metrics"]["rejected"], 1)
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["records"], [])

    def test_top_level_geojson_types_and_count_limits(self):
        cases = ([], None, {}, {"type": "Feature"}, feed(features={}), feed([event()] * 201))
        for raw in cases:
            with self.subTest(raw_type=type(raw)):
                # Literal JSON null, not fetch helper's default fixture.
                result, *_ = fetch(b"null" if raw is None else raw)
                self.assertFalse(result["ok"])
                self.assertEqual(result["errorCode"], "schema")
        maximum, *_ = fetch(feed([event()] * 200))
        self.assertTrue(maximum["ok"])

    def test_wrong_metadata_and_non_numeric_generated(self):
        for value in (None, True, "1790000000000", [], {}, -1, 10**1000, float("inf"), float("nan")):
            raw = feed()
            raw["metadata"]["generated"] = value
            with self.subTest(value_type=type(value)):
                result, *_ = fetch(raw)
                self.assertEqual(result["errorCode"], "schema")
        for metadata in (None, [], "bad"):
            raw = feed()
            raw["metadata"] = metadata
            self.assertEqual(fetch(raw)[0]["errorCode"], "schema")

    def test_source_freshness_rejects_stale_or_far_future(self):
        for seconds in (-600.001, 300.001):
            result, *_ = fetch(feed([], (NOW + seconds) * 1000))
            self.assertEqual(result["errorCode"], "schema")
        for seconds in (-600, 300):
            self.assertTrue(fetch(feed([], (NOW + seconds) * 1000))[0]["ok"])

    def test_event_age_is_within_source_generated_hour_and_not_future(self):
        for age in (-0.001, 3600.001):
            result, *_ = fetch(feed([event(age=age)], NOW * 1000))
            self.assertFalse(result["ok"])
        for age in (0, 3600):
            self.assertTrue(fetch(feed([event(age=age)], NOW * 1000))[0]["ok"])
        self.assertFalse(fetch(feed([event(age=1)], (NOW - 10) * 1000))[0]["ok"])
        # The one-hour window is anchored to metadata.generated, not fetch time.
        for age in (30, 3630):
            self.assertTrue(fetch(feed([event(age=age)], (NOW - 30) * 1000))[0]["ok"])
        self.assertFalse(fetch(feed([event(age=3630.001)], (NOW - 30) * 1000))[0]["ok"])
        # A source clock slightly ahead must not permit future event timestamps.
        self.assertFalse(fetch(feed([event(age=-1)], (NOW + 60) * 1000))[0]["ok"])

    def test_forged_ninety_minute_event_is_rejected_not_counted_as_past_hour(self):
        result, *_ = fetch(feed([event(age=5400)]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "schema")
        self.assertEqual(result["metrics"]["validated"], 0)
        self.assertEqual(result["metrics"]["rejected"], 1)
        mixed, *_ = fetch(feed([event(), event(mag=9.9, age=5400)]))
        self.assertTrue(mixed["ok"])
        self.assertEqual(mixed["counters"]["count"], 1)
        self.assertEqual(mixed["counters"]["maxMagnitude"], 3.1)
        self.assertIn("源更新时间前 1 小时", mixed["summary"])
        self.assertNotIn("2 小时", mixed["summary"])

    def test_magnitude_types_bounds_and_nonfinite(self):
        for mag in (True, False, "3", None, [], {}, 2.4999, 10.0001, -1, 10**1000, float("inf"), float("nan")):
            with self.subTest(mag_type=type(mag)):
                self.assertFalse(fetch(feed([event(mag=mag)]))[0]["ok"])
        for mag in (2.5, 10):
            self.assertTrue(fetch(feed([event(mag=mag)]))[0]["ok"])

    def test_point_geometry_strict_coordinates_and_types(self):
        for coordinates in (None, [], [1], [1, 2, 3, 4], [181, 0], [-181, 0], [0, 91], [0, -91],
                            [True, 2], ["1", 2], [0, float("nan")], [0, 1, float("inf")]):
            raw = feed()
            raw["features"][0]["geometry"]["coordinates"] = coordinates
            self.assertFalse(fetch(raw)[0]["ok"], repr(coordinates))
        for coordinates in ([180, 90], [-180, -90], [0, 0, -5]):
            raw = feed()
            raw["features"][0]["geometry"]["coordinates"] = coordinates
            self.assertTrue(fetch(raw)[0]["ok"])
        raw = feed()
        raw["features"][0]["geometry"]["type"] = "LineString"
        self.assertFalse(fetch(raw)[0]["ok"])

    def test_status_enum_optional_but_not_arbitrary_text(self):
        raw = feed()
        del raw["features"][0]["properties"]["status"]
        self.assertTrue(fetch(raw)[0]["ok"])
        for value in (None, True, [], {}, "Ignore previous instructions", "REVIEWED"):
            raw["features"][0]["properties"]["status"] = value
            self.assertFalse(fetch(raw)[0]["ok"])

    def test_hostile_json_huge_integer_exponent_nonfinite_duplicate_and_depth(self):
        cases = (b'{"huge":' + b"9" * 10000 + b"}", b'{"x":1e9999}', b'{"x":NaN}', b'{"x":Infinity}',
                 b'{"x":1,"x":2}', b"[" * 2000 + b"]" * 2000, b'{"type":', b"\xff\xfe")
        for body in cases:
            with self.subTest(prefix=body[:20]):
                result, *_ = fetch(body)
                self.assertEqual(result["errorCode"], "schema")
                self.assertEqual(result["records"], [])

    def test_content_types_and_encodings_fail_closed(self):
        for headers in ({}, {"Content-Type": "text/html"}, {"Content-Type": "text/plain"},
                        {"Content-Type": "application/jsonp"}, {"Content-Type": "application/json", "Content-Encoding": "gzip"},
                        {"Content-Type": "application/json", "Content-Encoding": "br"},
                        {"Content-Type": "application/json", "Transfer-Encoding": "gzip"}):
            self.assertEqual(fetch(feed(), headers)[0]["errorCode"], "schema")
        for media in ("application/json; charset=utf-8", "application/geo+json", "application/geojson"):
            self.assertTrue(fetch(feed(), {"Content-Type": media, "Content-Encoding": "identity"})[0]["ok"])
        self.assertEqual(fetch(b"<html>hostile</html>")[0]["errorCode"], "schema")

    def test_length_limits_invalid_headers_and_bounded_read(self):
        for length, code in ((str(live.MAX_BYTES + 1), "size"), ("9" * 100, "size"),
                             ("-1", "schema"), ("NaN", "schema"), ("1.5", "schema"), ("1", "schema")):
            result, *_ = fetch(feed(), {"Content-Type": "application/json", "Content-Length": length})
            self.assertEqual(result["errorCode"], code)
        result, _, _, response = fetch(b" " * (live.MAX_BYTES + 100))
        self.assertEqual(result["errorCode"], "size")
        self.assertEqual(result["metrics"]["bytes"], live.MAX_BYTES + 1)
        self.assertTrue(response.closed)
        body = json.dumps(feed()).encode()
        self.assertTrue(fetch(body, {"Content-Type": "application/json", "Content-Length": str(len(body))})[0]["ok"])

    def test_duplicate_headers_and_transfer_length_conflicts(self):
        response = Response(feed())
        response.headers["Content-Type"] = "text/html"
        opener = Mock()
        opener.open.return_value = response
        with patch.object(live.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(live.fetch_live_facts()["errorCode"], "schema")
        headers = {"Content-Type": "application/json", "Transfer-Encoding": "chunked", "Content-Length": "100"}
        self.assertEqual(fetch(feed(), headers)[0]["errorCode"], "schema")

    def test_redirects_same_or_other_host_never_followed(self):
        for target in (live.SOURCE_URL + "?redirect=1", "https://evil.invalid/upload"):
            failure = urllib.error.HTTPError(live.SOURCE_URL, 302, "PRIVATE_ERROR", {"Location": target}, None)
            result, opener, *_ = fetch(error=failure)
            self.assertEqual(result["errorCode"], "network")
            self.assertEqual(opener.open.call_count, 1)
            self.assertNotIn("PRIVATE_ERROR", repr(result))
            self.assertIsNone(live._NoRedirect().redirect_request(None, None, 302, "Found", {}, target))

    def test_already_redirected_response_and_non_200_fail(self):
        for response in (Response(feed(), url="https://evil.invalid/"), Response(feed(), status=301), Response(feed(), status=204)):
            opener = Mock()
            opener.open.return_value = response
            with patch.object(live.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(live.fetch_live_facts()["errorCode"], "network")

    def test_429_and_503_retry_after_clamped_without_echoing_headers(self):
        for code in (429, 503):
            for value, expected in (("0", 300), ("5", 300), ("900", 900), ("86401", 86400),
                                    ("9" * 5000, 86400), ("bad-private-header", 300), ("Fri, 01 Jan 2099", 300)):
                failure = urllib.error.HTTPError(live.SOURCE_URL, code, "PRIVATE_ERROR", {"Retry-After": value}, None)
                result, opener, *_ = fetch(error=failure)
                self.assertEqual(result["errorCode"], "rate_limit")
                self.assertEqual(result["retry_after"], expected)
                self.assertEqual(opener.open.call_count, 1)
                self.assertNotIn("PRIVATE_ERROR", repr(result))
                self.assertNotIn("bad-private-header", repr(result))

    def test_network_protocol_failures_never_emit_raw_error(self):
        for failure in (urllib.error.URLError("secret URL/private token"), TimeoutError("PRIVATE"),
                        OSError("PRIVATE"), http.client.IncompleteRead(b"PRIVATE"), http.client.BadStatusLine("PRIVATE")):
            result, *_ = fetch(error=failure)
            self.assertEqual(result["errorCode"], "network")
            self.assertNotIn("PRIVATE", repr(result))
            self.assertNotIn("secret", repr(result))

    def test_timeout_clamp_and_invalid_types(self):
        for timeout, expected in ((100, 15), (0, 0.1), (12, 12)):
            result, opener, *_ = fetch(timeout=timeout)
            self.assertTrue(result["ok"])
            self.assertEqual(opener.open.call_args.kwargs["timeout"], expected)
        for timeout in (True, "12", float("nan"), float("inf"), 10**1000):
            result, opener, *_ = fetch(timeout=timeout)
            self.assertFalse(result["ok"])
            self.assertEqual(result["errorCode"], "schema")
            opener.open.assert_not_called()

    def test_caller_deadline_and_single_transport_even_when_dns_ignores_timeout(self):
        entered, release = threading.Event(), threading.Event()
        response = Response(feed())
        opener = Mock()

        def blocked_open(*_args, **_kwargs):
            entered.set()
            release.wait(3)
            return response

        opener.open.side_effect = blocked_open
        try:
            with patch.object(live.urllib.request, "build_opener", return_value=opener), patch.object(live.time, "time", return_value=NOW):
                started = time.monotonic()
                result = live.fetch_live_facts(timeout=0.1)
                self.assertTrue(entered.is_set())
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(result["errorCode"], "network")
                self.assertFalse(result["ok"])
                self.assertEqual(live.fetch_live_facts(timeout=0.1)["errorCode"], "network")
                self.assertEqual(opener.open.call_count, 1)
        finally:
            release.set()
            self.assertTrue(live._TRANSPORT_LOCK.acquire(timeout=2))
            live._TRANSPORT_LOCK.release()
        self.assertTrue(response.closed)
        self.assertEqual(response.read_sizes, [])

    def test_fetch_performs_no_local_file_io(self):
        with patch.object(Path, "open", side_effect=AssertionError("unexpected disk I/O")):
            self.assertTrue(fetch()[0]["ok"])


class StateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "online.json"
        self.now = NOW
        self.state = live.LiveOnlineState(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.directory.cleanup()

    def enable(self):
        self.assertTrue(self.state.set_enabled(True, owner_action=True))

    def start(self, manual=False):
        result = self.state.begin(manual=manual)
        self.assertTrue(result["ok"], result)
        return result["attempt_id"]

    def success(self, count=1):
        return {"ok": True, "sourceUpdatedAtMs": self.now * 1000,
                "counters": {"count": count, "maxMagnitude": 4.0 if count else None,
                             "latestEventTimeMs": (self.now - 30) * 1000 if count else None,
                             "reviewedCount": count, "rejected": 0},
                "summary": "PRIVATE_ATTACK downloaded prose must not persist"}

    def read_disk(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_default_disabled_constructor_and_manual_never_opt_in(self):
        self.assertFalse(self.path.exists())
        self.assertFalse(self.state.snapshot()["enabled"])
        self.assertFalse(self.state.is_due())
        self.assertEqual(self.state.begin()["reason"], "disabled")
        self.assertEqual(self.state.begin(manual=True)["reason"], "disabled")
        self.assertFalse(self.state.set_enabled(True))
        self.assertFalse(self.state.set_enabled("yes", owner_action=True))
        self.assertFalse(self.path.exists())
        self.enable()
        self.assertTrue(self.read_disk()["enabled"])
        self.assertTrue(live.LiveOnlineState(self.path).enabled)

    def test_environment_path_precedence(self):
        with patch.dict(os.environ, {"DSH_DAFEIYU_ONLINE_FILE": str(self.path), "DSH_DAFEIYU_GROWTH_FILE": "unused/growth.json", "DSH_HOME": "unused-home"}):
            self.assertEqual(live.default_path(), self.path)
        with patch.dict(os.environ, {"DSH_DAFEIYU_ONLINE_FILE": "", "DSH_DAFEIYU_GROWTH_FILE": str(self.path.parent / "growth.json")}):
            self.assertEqual(live.default_path(), self.path)
        with patch.dict(os.environ, {"DSH_DAFEIYU_ONLINE_FILE": "", "DSH_DAFEIYU_GROWTH_FILE": "", "DSH_HOME": str(self.path.parent)}):
            self.assertEqual(live.default_path(), self.path.parent / "dsh-dafeiyu-growth" / "online.json")

    def test_begin_reserves_durably_and_prevents_parallel_polling(self):
        self.enable()
        attempt = self.start()
        self.assertTrue(self.state.is_current(attempt))
        self.assertEqual(self.state.begin(manual=True)["reason"], "working")
        self.assertFalse(self.state.is_due())
        raw = self.read_disk()
        self.assertEqual(raw["lastAttempt"], self.now)
        self.assertEqual(raw["nextCheck"], self.now + 300)
        restarted = live.LiveOnlineState(self.path, clock=lambda: self.now)
        self.assertFalse(restarted.snapshot()["working"])
        self.assertEqual(restarted.begin(manual=True)["reason"], "interval")
        self.now += 59.9
        self.assertFalse(restarted.begin(manual=True)["ok"])
        self.now += 0.1
        self.assertTrue(restarted.begin(manual=True)["ok"])

    def test_success_persists_only_numeric_regenerated_summary(self):
        self.enable()
        attempt = self.start()
        result = self.state.finish(self.success(), attempt_id=attempt)
        self.assertTrue(result["ok"])
        snap = result["snapshot"]
        self.assertEqual(snap["lastSuccess"], self.now)
        self.assertEqual(snap["nextCheck"], self.now + 300)
        self.assertEqual(snap["failures"], 0)
        self.assertNotIn("PRIVATE_ATTACK", self.path.read_text(encoding="utf-8"))
        restarted = live.LiveOnlineState(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.snapshot()["summary"], snap["summary"])
        self.now += 299.9
        self.assertFalse(restarted.is_due())
        self.now += 0.1
        self.assertTrue(restarted.is_due())

    def test_manual_floor_60_seconds_bypasses_only_regular_interval(self):
        self.enable()
        attempt = self.start()
        self.state.finish(self.success(), attempt_id=attempt)
        self.now += 59.99
        self.assertFalse(self.state.begin(manual=True)["ok"])
        self.now += 0.01
        self.assertFalse(self.state.begin()["ok"])
        self.assertTrue(self.state.begin(manual=True)["ok"])

    def test_manual_floor_also_holds_at_epoch_zero(self):
        self.now = 0
        self.enable()
        token = self.start()
        self.assertTrue(self.state.finish(self.success(count=0), attempt_id=token)["ok"])
        self.assertEqual(self.state.begin(manual=True)["reason"], "interval")
        self.now = 60
        self.assertTrue(self.state.begin(manual=True)["ok"])

    def test_server_cooldown_survives_restart_manual_and_disable(self):
        self.enable()
        attempt = self.start()
        self.state.finish({"ok": False, "errorCode": "rate_limit", "retry_after": 1000}, attempt_id=attempt)
        self.assertEqual(self.state.snapshot()["retryUntil"], NOW + 1000)
        self.now += 300
        self.assertEqual(self.state.begin(manual=True)["reason"], "cooldown")
        self.assertTrue(self.state.set_enabled(False))
        self.assertTrue(self.state.set_enabled(True, owner_action=True))
        restarted = live.LiveOnlineState(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.begin(manual=True)["reason"], "cooldown")
        self.now = NOW + 999.99
        self.assertFalse(restarted.begin(manual=True)["ok"])
        self.now = NOW + 1000
        self.assertTrue(restarted.begin(manual=True)["ok"])

    def test_retry_after_numeric_clamp_and_bad_state_result(self):
        for retry, expected in ((0, 300), (1, 300), (1000000, 86400), (float("nan"), 300), ("PRIVATE", 300)):
            with tempfile.TemporaryDirectory() as directory:
                state = live.LiveOnlineState(Path(directory) / "online.json", clock=lambda: self.now)
                state.set_enabled(True, owner_action=True)
                token = state.begin()["attempt_id"]
                state.finish({"ok": False, "errorCode": "rate_limit", "retry_after": retry}, attempt_id=token)
                self.assertEqual(state.snapshot()["retryUntil"], self.now + expected)
                self.assertNotIn("PRIVATE", repr(state.snapshot()))

    def test_failure_backoff_300_900_3600_and_fixed_errors(self):
        self.enable()
        for index, delay in enumerate((300, 900, 3600, 3600), 1):
            attempt = self.start()
            self.state.finish({"ok": False, "errorCode": "network", "error": "PRIVATE"}, attempt_id=attempt)
            snap = self.state.snapshot()
            self.assertEqual(snap["failures"], index)
            self.assertEqual(snap["nextCheck"], self.now + delay)
            self.assertNotIn("PRIVATE", repr(snap))
            self.now += delay
        attempt = self.start()
        self.assertTrue(self.state.finish(self.success(), attempt_id=attempt)["ok"])
        self.assertEqual(self.state.snapshot()["failures"], 0)

    def test_storage_apply_failure_is_not_success_and_preserves_prior_summary(self):
        self.enable()
        attempt = self.start()
        self.state.finish(self.success(), attempt_id=attempt)
        previous = self.state.snapshot()
        self.now += 300
        attempt = self.start()
        result = self.state.finish({"ok": False, "errorCode": "storage"}, attempt_id=attempt)
        self.assertFalse(result["ok"])
        self.assertEqual(result["snapshot"]["lastSuccess"], previous["lastSuccess"])
        self.assertEqual(result["snapshot"]["summary"], previous["summary"])
        self.assertEqual(result["snapshot"]["errorCode"], "storage")

    def test_disable_reenable_and_cancel_invalidate_inflight_generations(self):
        self.enable()
        first = self.start()
        self.assertTrue(self.state.set_enabled(False))
        self.assertFalse(self.state.is_current(first))
        self.assertFalse(self.state.finish(self.success(), attempt_id=first)["ok"])
        self.assertTrue(self.state.set_enabled(True, owner_action=True))
        self.now += 60
        second = self.start(manual=True)
        self.assertNotEqual(first, second)
        self.assertFalse(self.state.finish(self.success(), attempt_id=first)["ok"])
        self.assertTrue(self.state.is_current(second))
        self.state.cancel()
        self.assertFalse(self.state.is_current(second))
        self.assertFalse(self.state.finish(self.success(), attempt_id=second)["ok"])
        self.assertEqual(self.state.begin(manual=True)["reason"], "interval")
        self.assertEqual(self.state.snapshot()["lastSuccess"], 0)

    def test_cancelled_429_still_persists_cooldown_without_enabling_or_applying(self):
        self.enable()
        attempt = self.start()
        self.state.set_enabled(False)
        result = self.state.finish({"ok": False, "errorCode": "rate_limit", "retry_after": 1800}, attempt_id=attempt)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "cancelled")
        self.assertFalse(self.state.enabled)
        self.assertEqual(self.state.snapshot()["retryUntil"], self.now + 1800)
        self.assertEqual(self.state.snapshot()["lastSuccess"], 0)
        self.assertEqual(self.state.snapshot()["summary"], "")
        self.now += 60
        self.enable()
        self.assertEqual(self.state.begin(manual=True)["reason"], "cooldown")
        restarted = live.LiveOnlineState(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.begin(manual=True)["reason"], "cooldown")

    def test_unknown_or_replayed_cancelled_tokens_cannot_change_cooldown(self):
        self.enable()
        token = self.start()
        failure = {"ok": False, "errorCode": "rate_limit", "retry_after": 1800}
        self.state.finish(failure, attempt_id="unknown-token")
        self.assertTrue(self.state.is_current(token))
        self.assertEqual(self.state.retry_until, 0)
        self.state.cancel()
        self.state.finish(failure, attempt_id=token)
        self.assertEqual(self.state.retry_until, self.now + 1800)
        self.now += 60
        self.state.finish(failure, attempt_id=token)
        self.assertEqual(self.state.retry_until, NOW + 1800)

    def test_cancelled_429_does_not_finish_or_replace_new_generation(self):
        self.enable()
        old = self.start()
        self.state.cancel()
        self.now += 60
        current = self.start(manual=True)
        self.state.finish({"ok": False, "errorCode": "rate_limit", "retry_after": 1800}, attempt_id=old)
        self.assertTrue(self.state.is_current(current))
        self.assertTrue(self.state.finish(self.success(), attempt_id=current)["ok"])
        self.assertEqual(self.state.snapshot()["nextCheck"], self.now + 1800)

    def test_missing_attempt_id_cannot_finish_active_worker(self):
        self.enable()
        attempt = self.start()
        self.assertFalse(self.state.finish(self.success())["ok"])
        self.assertTrue(self.state.is_current(attempt))

    def test_bad_success_counters_and_source_freshness_become_schema_failure(self):
        for change in ({"sourceUpdatedAtMs": (NOW - 601) * 1000}, {"sourceUpdatedAtMs": (NOW + 301) * 1000},
                       {"counters": {"count": True}}, {"counters": {"count": 0, "maxMagnitude": "PRIVATE", "reviewedCount": 0}},
                       {"counters": {"count": 1, "maxMagnitude": float("inf"), "reviewedCount": 0}}):
            with tempfile.TemporaryDirectory() as directory:
                state = live.LiveOnlineState(Path(directory) / "online.json", clock=lambda: self.now)
                state.set_enabled(True, owner_action=True)
                token = state.begin()["attempt_id"]
                raw = self.success()
                raw.update(change)
                result = state.finish(raw, attempt_id=token)
                self.assertFalse(result["ok"])
                self.assertEqual(result["snapshot"]["errorCode"], "schema")
                self.assertEqual(result["snapshot"]["lastSuccess"], 0)

    def test_success_counters_cannot_claim_future_or_old_events_or_false_empty(self):
        self.enable()
        for age in (-1, 3600.001, 5400):
            token = self.start(manual=True)
            raw = self.success()
            raw["counters"]["latestEventTimeMs"] = (self.now - age) * 1000
            result = self.state.finish(raw, attempt_id=token)
            self.assertFalse(result["ok"])
            self.assertEqual(result["snapshot"]["errorCode"], "schema")
            self.now += 60
        token = self.start(manual=True)
        raw = self.success(count=0)
        raw["counters"]["rejected"] = 1
        self.assertFalse(self.state.finish(raw, attempt_id=token)["ok"])
        self.assertEqual(self.state.last_success, 0)

    def test_summary_and_persisted_counters_cannot_regenerate_ninety_minute_claim(self):
        raw = self.success()
        raw["counters"]["latestEventTimeMs"] = (self.now - 5400) * 1000
        with self.assertRaises(ValueError):
            live.make_summary(raw["counters"], raw["sourceUpdatedAtMs"])
        self.enable()
        persisted = self.read_disk()
        persisted.update(counters=raw["counters"], sourceUpdatedAtMs=raw["sourceUpdatedAtMs"])
        body = json.dumps(persisted).encode("utf-8")
        self.path.write_bytes(body)
        restarted = live.LiveOnlineState(self.path, clock=lambda: self.now)
        self.assertTrue(restarted.protected)
        self.assertFalse(restarted.enabled)
        self.assertEqual(self.path.read_bytes(), body)

    def test_corrupt_file_is_disabled_protected_and_never_overwritten(self):
        for body in (b"{bad", b"[]", b"null", b'{"version":1,"enabled":true}',
                     b"x" * (live.MAX_STATE_BYTES + 1), b'{"x":' + b"9" * 5000 + b"}"):
            self.path.write_bytes(body)
            state = live.LiveOnlineState(self.path, clock=lambda: self.now)
            self.assertTrue(state.protected)
            self.assertFalse(state.enabled)
            self.assertFalse(state.set_enabled(True, owner_action=True))
            self.assertFalse(state.begin(manual=True)["ok"])
            self.assertEqual(self.path.read_bytes(), body)

    def test_persisted_summary_is_ignored_and_regenerated(self):
        self.enable()
        token = self.start()
        self.state.finish(self.success(), attempt_id=token)
        raw = self.read_disk()
        raw["summary"] = "PRIVATE_ATTACK https://evil.invalid/execute"
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        restarted = live.LiveOnlineState(self.path, clock=lambda: self.now)
        self.assertFalse(restarted.protected)
        self.assertNotIn("PRIVATE_ATTACK", repr(restarted.snapshot()))
        self.assertNotIn("evil.invalid", repr(restarted.snapshot()))
        restarted.set_enabled(False)
        self.assertNotIn("PRIVATE_ATTACK", self.path.read_text(encoding="utf-8"))

    def test_malicious_persisted_types_and_nonfinite_are_protected(self):
        self.enable()
        valid = self.read_disk()
        changes = ({"enabled": 1}, {"failures": True}, {"lastAttempt": float("nan")},
                   {"nextCheck": 10**1000}, {"errorCode": "PRIVATE_ATTACK"}, {"sourceUpdatedAtMs": 12345})
        for change in changes:
            raw = dict(valid)
            raw.update(change)
            body = json.dumps(raw).encode()
            self.path.write_bytes(body)
            state = live.LiveOnlineState(self.path, clock=lambda: self.now)
            self.assertTrue(state.protected)
            self.assertFalse(state.enabled)
            self.assertNotIn("PRIVATE_ATTACK", repr(state.snapshot()))
            self.assertEqual(self.path.read_bytes(), body)

    def test_external_corruption_and_stale_instances_fail_closed(self):
        self.enable()
        stale = live.LiveOnlineState(self.path, clock=lambda: self.now)
        self.path.write_bytes(b"corrupt external update")
        self.assertFalse(self.state.begin()["ok"])
        self.assertTrue(self.state.protected)
        self.assertFalse(stale.set_enabled(True, owner_action=True))
        self.assertEqual(self.path.read_bytes(), b"corrupt external update")

    def test_atomic_write_failure_preserves_old_bytes_and_blocks_network_slot(self):
        self.enable()
        before = self.path.read_bytes()
        with patch.object(live.os, "replace", side_effect=OSError("PRIVATE OS detail")):
            result = self.state.begin()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "storage")
        self.assertFalse(self.state.enabled)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.assertNotIn("PRIVATE", repr(self.state.snapshot()))

    def test_failed_finish_save_does_not_claim_success(self):
        self.enable()
        attempt = self.start()
        before = self.path.read_bytes()
        with patch.object(live.os, "replace", side_effect=OSError("PRIVATE")):
            result = self.state.finish(self.success(), attempt_id=attempt)
        self.assertFalse(result["ok"])
        self.assertEqual(result["snapshot"]["lastSuccess"], 0)
        self.assertEqual(result["snapshot"]["summary"], "")
        self.assertFalse(result["snapshot"]["enabled"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_scheduler_never_opens_http(self):
        with patch.object(live.urllib.request, "build_opener", side_effect=AssertionError("network forbidden")):
            self.enable()
            token = self.start()
            self.state.finish(self.success(count=0), attempt_id=token)
            self.assertIn("为 0 条", self.state.snapshot()["summary"])

    def test_snapshot_is_a_deep_copy(self):
        self.enable()
        token = self.start()
        self.state.finish(self.success(), attempt_id=token)
        snapshot = self.state.snapshot()
        snapshot["counters"]["count"] = 199
        self.assertEqual(self.state.snapshot()["counters"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
