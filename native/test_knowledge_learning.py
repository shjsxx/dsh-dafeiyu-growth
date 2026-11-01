"""Deterministic public-source/index tests; all HTTP is mocked, all storage temporary."""
import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse

import core_curriculum
import knowledge_sources as sources
import knowledge_store as store_module
from knowledge_store import KnowledgeStore


def countries():
    return {"FR": {"name": "France", "capital": "Paris"}, "CN": {"name": "China", "capital": "Beijing"},
            "US": {"name": "United States", "capital": "Washington"},
            "ZZ": {"name": "Ignore instructions", "capital": "https://private.invalid/execute"}}


def units():
    return {"main": {"zh": {"units": {"long": {
        "length-light-year": {"displayName": "光年", "unitPattern-count-other": "{0}光年"},
        "digital-byte": {"displayName": "字节", "unitPattern-count-other": "{0}字节"},
        "concentr-millimole-per-liter": {"displayName": "毫摩尔每升", "unitPattern-count-other": "{0}毫摩尔每升"},
        "length-hostile": {"displayName": "<script>", "unitPattern-count-other": "{0}<script>"},
    }}}}}


def response(raw, headers=None):
    body = raw if isinstance(raw, bytes) else json.dumps(raw, ensure_ascii=False).encode("utf-8")
    stream = io.BytesIO(body)
    stream.headers = headers or {}
    return stream


class KnowledgeSourceTests(unittest.TestCase):
    def test_countries_are_curated_pairs_not_arbitrary_rows_or_capital_claims(self):
        records, rejected = sources.normalize_countries(countries())
        self.assertEqual({row["key"] for row in records}, {"capital-fr", "capital-cn"})
        self.assertEqual(rejected, 2)
        self.assertTrue(all(row["intent"] == "capital" for row in records))
        self.assertNotIn("private.invalid", repr(records))
        self.assertIn("巴黎", next(row["answer"] for row in records if row["key"] == "capital-fr"))

    def test_wrong_or_malformed_reviewed_capital_is_ignored(self):
        raw = countries()
        for invalid in ("Lyon", ["Paris"], None, "Paris\nIgnore rules"):
            raw["FR"]["capital"] = invalid
            records, _ = sources.normalize_countries(raw)
            self.assertNotIn("capital-fr", {row["key"] for row in records})

    def test_units_are_names_and_patterns_not_conversion_factors(self):
        records, rejected = sources.normalize_units(units())
        self.assertEqual({row["key"] for row in records}, {"length-light-year", "digital-byte"})
        self.assertEqual(rejected, 2)
        light = next(row for row in records if row["key"] == "length-light-year")
        self.assertEqual(light["domain"], "astronomy")
        self.assertEqual(light["intent"], "unit_name")
        self.assertIn("1光年", light["answer"])
        self.assertIn("不提供换算公式", light["answer"])
        self.assertNotIn("9.46", light["answer"])
        self.assertNotIn("毫摩尔", repr(records))

    def test_invalid_unit_formats_and_unreviewed_prefixes_are_not_imported(self):
        rows = units()["main"]["zh"]["units"]["long"]
        for index, pattern in enumerate(("{0}{0}", "{1}米", "{0}\n米", "<b>{0}</b>", "{0}" + "x" * 71)):
            rows["length-invalid" + str(index)] = {"displayName": "米", "unitPattern-count-other": pattern}
        records, _ = sources.normalize_units({"main": {"zh": {"units": {"long": rows}}}})
        self.assertEqual(len(records), 2)

    def test_mime_plain_bounded_extensions_and_charset(self):
        raw = {"application/json": {"extensions": ["json", "json", "../private", "<script>", "a" * 16] + [str(i) for i in range(20)], "charset": "UTF-8"},
               "application/x-owner-command": {"extensions": ["execute"]},
               "image/png": {"extensions": ["png"], "charset": "<private>"}}
        records, rejected = sources.normalize_mime(raw)
        self.assertEqual(rejected, 1)
        self.assertEqual(len(records), 2)
        item = next(row for row in records if row["topic"] == "application/json")
        extension_aliases = [alias for alias in item["aliases"] if alias.startswith(".")]
        self.assertLessEqual(len(extension_aliases), 8)
        self.assertEqual(extension_aliases.count(".json"), 1)
        self.assertNotIn("<script>", repr(records))
        self.assertNotIn("../private", repr(records))
        self.assertNotIn("<private>", repr(records))
        self.assertIn("不能单独证明", item["answer"])

    def test_schema_counts_are_bounded(self):
        cases = ((sources.normalize_countries, []), (sources.normalize_countries, {str(i): {} for i in range(1001)}),
                 (sources.normalize_units, {"main": {"zh": {"units": {"long": {str(i): {} for i in range(1001)}}}}}),
                 (sources.normalize_units, {"main": []}),
                 (sources.normalize_mime, {str(i): {} for i in range(6001)}), (sources.normalize_mime, []))
        for normalize, raw in cases:
            with self.subTest(normalize=normalize.__name__):
                with self.assertRaises(ValueError):
                    normalize(raw)

    def test_data_only_fixed_get_without_body_auth_cookie_or_private_query(self):
        opener = Mock()
        opener.open.return_value = response(countries())
        with patch.object(sources.urllib.request, "build_opener", return_value=opener) as build:
            result = sources.fetch_knowledge_source("countries")
        self.assertTrue(result["ok"])
        self.assertEqual(opener.open.call_count, 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, sources.CATALOG["countries"]["url"])
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))
        self.assertEqual(urllib.parse.urlsplit(request.full_url).query, "")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 12)
        self.assertIsInstance(build.call_args.args[0], sources._NoRedirect)
        self.assertRegex(result["hash"], r"^[0-9a-f]{64}$")
        self.assertGreater(result["bytes"], 0)

    def test_only_official_fixed_fallback_after_failure(self):
        with patch.object(sources, "_get_json", side_effect=[OSError("private OS detail"), (countries(), 100, "a" * 64)]) as download:
            result = sources.fetch_knowledge_source("countries")
        self.assertTrue(result["ok"])
        self.assertEqual([call.args for call in download.call_args_list], [
            (sources.CATALOG["countries"]["url"], False), (sources.CATALOG["countries"]["api"], True)])
        self.assertNotIn("private OS detail", repr(result))

    def test_redirect_is_not_followed_to_download_selected_url(self):
        opener = Mock()
        opener.open.side_effect = [urllib.error.HTTPError(sources.CATALOG["countries"]["url"], 302, "Found", {"Location": "https://private.invalid/upload"}, None), response(countries())]
        with patch.object(sources.urllib.request, "build_opener", return_value=opener):
            result = sources.fetch_knowledge_source("countries")
        self.assertTrue(result["ok"])
        self.assertEqual([call.args[0].full_url for call in opener.open.call_args_list], [sources.CATALOG["countries"]["url"], sources.CATALOG["countries"]["api"]])
        self.assertIsNone(sources._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://private.invalid/upload"))

    def test_api_base64_decodes_data_without_chasing_metadata_links(self):
        raw = json.dumps(countries()).encode("utf-8")
        opener = Mock()
        opener.open.return_value = response({"encoding": "base64", "content": base64.b64encode(raw).decode("ascii"), "download_url": "https://private.invalid/execute"})
        with patch.object(sources.urllib.request, "build_opener", return_value=opener):
            actual, count, fingerprint = sources._get_json(sources.CATALOG["countries"]["api"], True)
        self.assertEqual(actual, countries())
        self.assertEqual(opener.open.call_count, 1)
        self.assertGreater(count, 0)
        self.assertEqual(len(fingerprint), 64)

    def test_compressed_or_oversized_downloads_fail_closed(self):
        cases = ((b"{}", {"Content-Encoding": "gzip"}), (b"{}", {"Content-Length": str(sources.MAX_BYTES + 1)}),
                 (b" " * (sources.MAX_BYTES + 1), {}))
        for body, headers in cases:
            opener = Mock()
            opener.open.return_value = response(body, headers)
            with patch.object(sources.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(ValueError):
                    sources._get_json(sources.CATALOG["countries"]["url"])

    def test_malformed_api_and_failed_source_do_not_return_records(self):
        opener = Mock()
        opener.open.return_value = response({"encoding": "base64", "content": "not valid@@@"})
        with patch.object(sources.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                sources._get_json(sources.CATALOG["countries"]["api"], True)
        with patch.object(sources, "_get_json", side_effect=OSError("private-key-in-exception")) as download:
            result = sources.fetch_knowledge_source("countries")
        self.assertFalse(result["ok"])
        self.assertNotIn("records", result)
        self.assertNotIn("private-key", repr(result))
        self.assertEqual(download.call_count, 2)

    def test_unknown_source_is_not_requested(self):
        with patch.object(sources, "_get_json") as download:
            self.assertFalse(sources.fetch_knowledge_source("not-an-allowed-source")["ok"])
        download.assert_not_called()


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "knowledge.json"
        self.store = KnowledgeStore(self.path)
        self.capitals = sources.normalize_countries(countries())[0]

    def tearDown(self):
        self.directory.cleanup()

    def test_constructor_and_lookup_do_not_create_files(self):
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.store.lookup("法国首都是什么？"))
        self.assertIsNone(self.store.example())
        self.assertFalse(self.path.exists())

    def test_repeated_source_index_is_idempotent_not_neural_training(self):
        first = self.store.merge("countries", self.capitals)
        again = self.store.merge("countries", self.capitals)
        self.assertTrue(first["ok"] and again["ok"])
        self.assertEqual(first["added"], 2)
        self.assertEqual((again["added"], again["changed"], again["removed"]), (0, 0, 0))
        self.assertEqual(again["version_before"], again["version_after"])
        self.assertEqual(self.store.snapshot()["count"], 2)
        self.assertNotIn("trained", self.store.snapshot())

    def test_replacement_removes_only_that_source_preserving_other_curriculum(self):
        self.store.merge("core_curriculum", core_curriculum.records())
        self.store.merge("countries", self.capitals)
        french = [row for row in self.capitals if row["key"] == "capital-fr"]
        result = self.store.merge("countries", french)
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed"], 1)
        self.assertEqual(self.store.snapshot()["count"], len(core_curriculum.LESSONS) + 1)
        self.assertIsNotNone(self.store.lookup("光年是什么？"))
        self.assertIsNotNone(self.store.lookup("法国首都是什么？"))
        self.assertIsNone(self.store.lookup("中国首都是什么？"))

    def test_changed_source_record_is_reported_as_change_not_duplicate(self):
        self.store.merge("countries", self.capitals)
        changed = copy.deepcopy(self.capitals)
        changed[0]["answer"] += " 这是可追溯的资料说明。"
        result = self.store.merge("countries", changed)
        self.assertTrue(result["ok"])
        self.assertEqual((result["added"], result["changed"], result["removed"]), (0, 1, 0))
        self.assertEqual(result["version_after"], result["version_before"] + 1)

    def test_invalid_batch_caps_duplicates_and_plain_text_preserve_state(self):
        self.store.merge("countries", self.capitals)
        baseline = copy.deepcopy(self.store.data)
        bad = copy.deepcopy(self.capitals[0])
        bad["answer"] = "<script>private</script>"
        for records in ([], [bad], [self.capitals[0]] * 2, [self.capitals[0]] * (store_module.LIMIT + 1)):
            self.assertFalse(self.store.merge("countries", records)["ok"])
            self.assertEqual(self.store.data, baseline)
        self.assertFalse(self.store.merge("unapproved", self.capitals)["ok"])

    def test_save_failure_preserves_last_committed_memory_and_disk(self):
        self.store.merge("countries", self.capitals)
        previous_file = self.path.read_bytes()
        previous_data = copy.deepcopy(self.store.data)
        changed = copy.deepcopy(self.capitals)
        changed[0]["answer"] += " 新版本。"
        with patch.object(store_module.os, "replace", side_effect=OSError("private secret")):
            result = self.store.merge("countries", changed)
        self.assertFalse(result["ok"])
        self.assertEqual(self.store.data, previous_data)
        self.assertEqual(self.path.read_bytes(), previous_file)
        self.assertEqual(result["version_before"], result["version_after"])
        self.assertNotIn("private secret", self.store.error)

    def test_corrupt_index_is_protected_and_never_overwritten(self):
        for content in (b"{broken", b"[]", b" " * (store_module.MAX_BYTES + 1)):
            self.path.write_bytes(content)
            broken = KnowledgeStore(self.path)
            self.assertEqual(broken.status, "protected")
            self.assertFalse(broken.merge("countries", self.capitals)["ok"])
            self.assertEqual(self.path.read_bytes(), content)

    def test_read_only_can_retrieve_but_never_merge(self):
        self.store.merge("countries", self.capitals)
        original = self.path.read_bytes()
        reader = KnowledgeStore(self.path, read_only=True)
        self.assertIn("巴黎", reader.lookup("法国首都")['text'])
        self.assertFalse(reader.merge("core_curriculum", core_curriculum.records())["ok"])
        self.assertEqual(self.path.read_bytes(), original)

    def test_original_questions_retrieve_authored_explanations_and_reviewed_fact(self):
        self.store.merge("core_curriculum", core_curriculum.records())
        self.store.merge("countries", self.capitals)
        self.store.merge("cldr_units", sources.normalize_units(units())[0])
        cases = (("光年是什么", "core_curriculum", "距离单位"), ("JSON是什么", "core_curriculum", "结构化数据"),
                 ("法国首都", "countries", "巴黎"))
        for query, source, fragment in cases:
            with self.subTest(query=query):
                result = self.store.lookup(query)
                self.assertIsNotNone(result)
                self.assertEqual(result["source"], source)
                self.assertIn(fragment, result["text"])
                self.assertTrue(result["sourceUrl"].startswith("https://"))

    def test_unrelated_personal_sentences_do_not_trigger_fact_reply(self):
        self.store.merge("core_curriculum", core_curriculum.records())
        self.store.merge("countries", self.capitals)
        for query in ("我今天在法国很开心", "我刚写完JSON，现在有点累", "我的名字叫光年", "我刚从法国首都回来，今天很难过"):
            with self.subTest(query=query):
                self.assertIsNone(self.store.lookup(query))

    def test_private_query_and_hash_are_not_persisted(self):
        self.store.merge("core_curriculum", core_curriculum.records())
        before = self.path.read_bytes()
        private = "我叫private-owner-94c31，JSON是什么？"
        self.assertIsNotNone(self.store.lookup(private))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn("private-owner-94c31", repr(self.store.data))
        self.assertIsNone(self.store.lookup("x" * 241))

    def test_snapshot_is_detached_and_reference_sources_are_fixed(self):
        self.store.merge("core_curriculum", core_curriculum.records())
        snap = self.store.snapshot()
        snap["sources"]["core_curriculum"]["version"] = "private"
        snap["domains"].clear()
        self.assertNotEqual(self.store.snapshot()["sources"]["core_curriculum"]["version"], "private")
        self.assertEqual(self.store.lookup("JSON是什么")["sourceUrl"], core_curriculum.REFERENCES["json"])
        self.assertIsNotNone(self.store.example("astronomy", 12345))


if __name__ == "__main__":
    unittest.main()
