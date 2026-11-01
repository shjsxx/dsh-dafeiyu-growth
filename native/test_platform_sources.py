"""No live traffic: public platform allowlist, attribution and bounded transport."""
import copy
import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import knowledge_sources as sources
from knowledge_store import KnowledgeStore
from learning_state import PublicLearningState


def fixture():
    return {"items": [{"answer_id": aid, "question_id": row["question"],
        "content_license": "CC BY-SA 4.0", "owner": {"user_id": row["owner_id"], "display_name": row["owner"]},
        "body": "<p>" + " ".join(row["markers"]) + "</p>"}
        for aid, row in sources.REVIEWED_ANSWERS.items()], "has_more": False}


def response(body, encoding="identity"):
    value = io.BytesIO(body)
    value.headers = {"Content-Encoding": encoding, "Content-Length": str(len(body))}
    return value


class PlatformSourceTests(unittest.TestCase):
    def test_only_reviewed_templates_with_attribution_are_retained(self):
        raw = fixture()
        for row in raw["items"]:
            row["body"] += '<script>UPLOAD_PRIVATE</script><a href="https://private.invalid">go</a>'
            row["owner"]["profile_image"] = "https://private.invalid"
        rows, rejected = sources.normalize_stackexchange(raw)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rejected, 0)
        self.assertNotIn("UPLOAD_PRIVATE", repr(rows))
        self.assertNotIn("private.invalid", repr(rows))
        for row in rows:
            self.assertIn("https://stackoverflow.com/a/", row["answer"])
            self.assertIn("https://creativecommons.org/licenses/by-sa/4.0/", row["answer"])
            self.assertIn("改写", row["answer"])

    def test_changed_author_license_markers_or_ids_fail_without_partial_import(self):
        for key, value in (("content_license", "all rights reserved"), ("question_id", 1), ("body", "changed"), ("answer_id", True), ("owner", {"user_id": 123, "display_name": "fake"})):
            raw = fixture()
            raw["items"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                sources.normalize_stackexchange(raw)

    def test_duplicates_or_partial_page_do_not_delete_old_cached_answers(self):
        for raw in ({"items": fixture()["items"][:2]}, {"items": fixture()["items"] * 2}, {"items": fixture()["items"], "has_more": True}):
            with self.subTest(raw=len(raw["items"])), self.assertRaises(ValueError):
                sources.normalize_stackexchange(raw)

    def test_schema_limits_and_error_wrappers(self):
        for raw in (None, [], {"items": None}, {"items": [{}] * 11}, dict(fixture(), error_id=502), dict(fixture(), backoff=100)):
            with self.subTest(raw=type(raw)), self.assertRaises(ValueError):
                sources.normalize_stackexchange(raw)

    def test_platform_pending_catalog_is_metadata_not_network_permission(self):
        rows = sources.platform_catalog_snapshot()
        self.assertEqual({row["id"] for row in rows}, {"zhihu_public", "short_video_public"})
        with patch.object(sources, "_get_json") as fetch:
            for source in ("zhihu_public", "short_video_public", "https://localhost/", [], None):
                self.assertFalse(sources.fetch_knowledge_source(source)["ok"])
            fetch.assert_not_called()

    def test_one_fixed_public_api_request_no_cookie_token_or_download_links(self):
        opener = Mock()
        opener.open.return_value = response(json.dumps(fixture()).encode())
        with patch.object(sources.urllib.request, "build_opener", return_value=opener):
            result = sources.fetch_knowledge_source("stackexchange")
        self.assertTrue(result["ok"])
        self.assertEqual(opener.open.call_count, 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, sources.CATALOG["stackexchange"]["url"])
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header("Cookie"))
        self.assertIsNone(request.get_header("Authorization"))
        self.assertNotIn("access_token", request.full_url)

    def test_explicit_bounded_gzip_transport_for_official_api_only(self):
        body = gzip.compress(json.dumps(fixture()).encode())
        opener = Mock()
        opener.open.return_value = response(body, "gzip")
        with patch.object(sources.urllib.request, "build_opener", return_value=opener):
            result = sources.fetch_knowledge_source("stackexchange")
        self.assertTrue(result["ok"])
        self.assertEqual(result["bytes"], len(body))
        opener.open.return_value = response(body, "gzip")
        with patch.object(sources.urllib.request, "build_opener", return_value=opener), self.assertRaises(ValueError):
            sources._get_json(sources.CATALOG["countries"]["url"])

    def test_gzip_bomb_is_capped_after_decompression(self):
        body = gzip.compress(b"x" * (sources.MAX_BYTES + 1))
        opener = Mock()
        opener.open.return_value = response(body, "gzip")
        with patch.object(sources.urllib.request, "build_opener", return_value=opener):
            result = sources.fetch_knowledge_source("stackexchange")
        self.assertFalse(result["ok"])
        self.assertEqual(opener.open.call_count, 1)

    def test_server_backoff_is_returned_and_not_retried(self):
        with patch.object(sources, "_get_json", return_value=(dict(fixture(), backoff=7200), 100, "a" * 64)) as fetch:
            result = sources.fetch_knowledge_source("stackexchange")
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "rate_limit")
        self.assertEqual(result["retry_after"], 7200)
        self.assertEqual(fetch.call_count, 1)

    def test_new_knowledge_indexes_idempotently_and_retrieves_with_license(self):
        with tempfile.TemporaryDirectory() as folder:
            store = KnowledgeStore(Path(folder) / "knowledge.json")
            rows, _ = sources.normalize_stackexchange(fixture())
            self.assertEqual(store.merge("stackexchange", rows)["added"], 3)
            self.assertEqual(store.merge("stackexchange", rows)["added"], 0)
            answer = store.lookup("Python元类是什么？")
            self.assertIn("CC BY-SA 4.0", answer["text"])
            self.assertEqual(store.snapshot()["count"], 3)

    def test_server_cooldown_is_persisted_and_manual_click_cannot_bypass_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            state = PublicLearningState(path)
            state.set_enabled(True)
            self.assertTrue(state.begin(now=1000))
            self.assertTrue(state.finish(False, now=1001, retry_after=7200))
            again = PublicLearningState(path)
            self.assertFalse(again.begin(now=2000, manual=True))
            self.assertFalse(again.is_due(now=2000))
            self.assertTrue(again.begin(now=8202, manual=True))


if __name__ == "__main__":
    unittest.main()
