"""Isolated settings tests: temp-owned files, mocked DPAPI, no API/mic/key service."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import realtime_config as rc


class RealtimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "realtime.json"
        self.vault = {}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        patch.object(rc, "protect_secret", side_effect=self.protect).start()
        patch.object(rc, "unprotect_secret", side_effect=lambda blob: self.vault[blob]).start()
        patch("socket.create_connection", side_effect=AssertionError("network forbidden")).start()
        patch("cloud_chat.load_config", side_effect=AssertionError("cloud config forbidden")).start()

    def protect(self, text):
        token = "mock-current-user-dpapi-" + str(len(self.vault))
        self.vault[token] = text
        return token

    def save(self, **overrides):
        kwargs = dict(endpoint=rc.DEFAULT_ENDPOINT, model=rc.DEFAULT_MODEL, voice=rc.DEFAULT_VOICE,
                      max_tokens=1024, session_seconds=600, protocol="ga", path=self.path)
        kwargs.update(overrides)
        return rc.save_config(**kwargs)

    def document(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_missing_defaults_no_file_and_no_cloud_environment_key(self):
        config = rc.load_config(self.path, env={"DSH_DAFEIYU_CLOUD_KEY": "do-not-use"})
        self.assertFalse(self.path.exists())
        self.assertEqual(config["endpoint"], rc.DEFAULT_ENDPOINT)
        self.assertEqual(config["model"], "gpt-realtime")
        self.assertEqual(config["voice"], "marin")
        self.assertEqual(config["max_tokens"], 1024)
        self.assertEqual(config["session_seconds"], 600)
        self.assertEqual(config["protocol"], "ga")
        self.assertEqual(config["key"], "")
        self.assertIsNone(config["error"])

    def test_default_path_precedence(self):
        root = Path(self.directory.name)
        env = {"DSH_DAFEIYU_REALTIME_FILE": str(root / "explicit.json"),
               "DSH_DAFEIYU_GROWTH_FILE": str(root / "profile" / "growth.json"),
               "DSH_HOME": str(root / "home"), "DSH_DAFEIYU_CLOUD_FILE": "ignored"}
        self.assertEqual(rc.default_path(env), root / "explicit.json")
        env.pop("DSH_DAFEIYU_REALTIME_FILE")
        self.assertEqual(rc.default_path(env), root / "profile" / "realtime.json")
        env.pop("DSH_DAFEIYU_GROWTH_FILE")
        self.assertEqual(rc.default_path(env), root / "home" / "dsh-dafeiyu-growth" / "realtime.json")
        self.assertEqual(rc.load_config(env=env)["path"], str(rc.default_path(env)))

    def test_endpoint_normalization_and_custom_exact_paths(self):
        accepted = {
            "https://API.OpenAI.com:443/v1/": rc.DEFAULT_ENDPOINT,
            "wss://api.openai.com/v1": rc.DEFAULT_ENDPOINT,
            "https://api.example": "wss://api.example/realtime",
            "wss://api.example/": "wss://api.example/realtime",
            "https://api.example:8443/custom/socket/": "wss://api.example:8443/custom/socket/",
            "http://localhost:9000/v1": "ws://localhost:9000/v1/realtime",
            "ws://127.0.0.1:80/custom": "ws://127.0.0.1/custom",
            "http://[::1]:9010/v1": "ws://[::1]:9010/v1/realtime",
            "wss://[2001:db8::1]/custom": "wss://[2001:db8::1]/custom",
        }
        for value, expected in accepted.items():
            with self.subTest(value=value):
                self.assertEqual(rc.normalize_endpoint(value), expected)

    def test_rejects_unsafe_urls_without_echoing_any_input(self):
        bad = ["", None, "javascript:alert(1)", "file:///etc/passwd", "https:///socket",
               "https://secret@api.example/socket", "https://api.example?key=secret",
               "https://api.example?", "https://api.example/#secret", "https://api.example/#",
               "https://api.example/white space", "https://api.example/\nsecret",
               "https://api.example/\rsecret", "https://api.example/\u00a0secret",
               "https://api.example/\u2028secret", " https://api.example",
               "https://api.example/%0d%0aAuthorization:secret", "https://api.example/%20secret",
               "https://api.example/%250asecret", "https://api.example/%3fkey=secret",
               "https://api.example/%zz", "https://api.example/../secret",
               "https://api.example\\@evil.invalid/socket", "https://api.example:0/v1",
               "https://api.example:65536/v1", "https://api.example:+443/v1",
               "https://api.example:0443/v1", "https://api.example:/v1",
               "https://api.example:weird/v1", "https://bad_host/socket",
               "http://192.168.1.1/v1", "ws://10.0.0.1/realtime", "http://172.16.1.1/v1",
               "http://127.1/v1", "http://localhost.evil.invalid/v1", "http://localhost./v1",
               "http://[0:0:0:0:0:0:0:1]/v1", "http://[::ffff:127.0.0.1]/v1"]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(rc.RealtimeConfigError) as caught:
                    rc.normalize_endpoint(value)
                self.assertEqual(str(caught.exception), rc.ERRORS["endpoint"])

    def test_strict_transport_validation_and_no_extra_fields(self):
        for field, values in {"model": ["", "x" * 101, "m?key=x", "a b", "模型", "a\n"],
                              "voice": ["", "x" * 65, "v/name", "v:one", "v\n"],
                              "max_tokens": [15, 4097, True, "1024", 12.2],
                              "session_seconds": [59, 1801, False, "600"],
                              "protocol": ["GA", "legacy", None]}.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(rc.RealtimeConfigError):
                    rc.validate_config({field: value})
        checked = rc.validate_config({"headers": {"Authorization": "bad"}, "tools": [], "system_prompt": "no"})
        self.assertEqual(set(checked), {*rc._FIELDS, "key"})

    def test_model_url_query_is_encoded_not_concatenated(self):
        model = "org/model:rev_1.2-test"
        url = rc.build_connection_url({"model": model})
        self.assertIn("org%2Fmodel%3Arev_1.2-test", url)
        self.assertEqual(parse_qs(urlsplit(url).query), {"model": [model]})

    def test_key_ascii_limits_and_no_controls(self):
        for key in ["x", "x" * 8192, "a printable key ~"]:
            self.assertEqual(rc.validate_config({"key": key})["key"], key)
        for key in [None, b"x", "x" * 8193, "x\n", "x\r", "x\t", "x\0", "x\x7f", "密钥"]:
            with self.subTest(key_type=type(key).__name__), self.assertRaises(rc.RealtimeConfigError):
                rc.validate_config({"key": key})

    def test_dpapi_envelope_binds_exact_endpoint_and_plaintext_never_on_disk(self):
        config = self.save(key="sensitive-test-token")
        self.assertEqual(config["key"], "sensitive-test-token")
        self.assertTrue(config["key_saved"])
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("sensitive-test-token", text)
        self.assertNotIn("fingerprint", text)
        self.assertNotIn("identity", text)
        envelope = json.loads(self.vault[self.document()["key_protected"]])
        self.assertEqual(envelope["endpoint"], rc.DEFAULT_ENDPOINT)
        self.assertEqual(envelope["key"], "sensitive-test-token")

    def test_model_voice_budgets_only_edits_preserve_encrypted_key(self):
        self.save(key="old-secret")
        previous = self.document()["key_protected"]
        config = self.save(model="new-model", voice="new-voice", max_tokens=4096, session_seconds=1800, protocol="beta")
        self.assertEqual(config["key"], "old-secret")
        self.assertEqual(self.document()["key_protected"], previous)

    def test_every_endpoint_change_requires_new_key_or_explicit_clear(self):
        self.save(key="old-secret")
        before = self.path.read_bytes()
        for endpoint in ["wss://other.example/v1/realtime", "wss://api.openai.com/custom",
                         "wss://api.openai.com:8443/v1/realtime"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(rc.RealtimeConfigError) as caught:
                self.save(endpoint=endpoint)
            self.assertEqual(caught.exception.code, "target")
            self.assertEqual(self.path.read_bytes(), before)
        config = self.save(endpoint="wss://other.example/custom", key="new-secret")
        self.assertEqual(config["key"], "new-secret")
        self.assertEqual(config["endpoint"], "wss://other.example/custom")

    def test_first_custom_save_also_requires_new_key_or_explicit_clear(self):
        with self.assertRaises(rc.RealtimeConfigError):
            self.save(endpoint="wss://other.example/custom")
        self.assertFalse(self.path.exists())
        config = self.save(endpoint="wss://other.example/custom", clear_key=True)
        self.assertEqual(config["key"], "")
        self.assertTrue(self.document()["environment_key_disabled"])

    def test_environment_key_official_only_never_forwarded_and_clear_disables_it(self):
        with patch.dict(os.environ, {"DSH_DAFEIYU_REALTIME_KEY": "environment-secret"}):
            loaded = rc.load_config(self.path)
            self.assertEqual(loaded["key"], "environment-secret")
            with self.assertRaises(rc.RealtimeConfigError):
                self.save(endpoint="wss://other.example/custom")
            saved = self.save(endpoint="wss://other.example/custom", clear_key=True)
            self.assertFalse(saved["key"])
            self.assertIsNone(saved["error"])
            official = self.save(clear_key=True)
            self.assertFalse(official["key"])
            self.assertIsNone(official["error"])

    def test_tampered_custom_file_does_not_receive_environment_key(self):
        self.save()
        document = self.document()
        document["endpoint"] = "wss://other.example/custom"
        self.path.write_text(json.dumps(document), encoding="utf-8")
        config = rc.load_config(self.path, env={"DSH_DAFEIYU_REALTIME_KEY": "environment-secret"})
        self.assertEqual(config["key"], "")
        self.assertEqual(config["error"], rc.ERRORS["environment"])

    def test_saved_key_does_not_fallback_to_environment_and_clear_is_explicit(self):
        self.save(key="stored-secret")
        loaded = rc.load_config(self.path, env={"DSH_DAFEIYU_REALTIME_KEY": "environment-secret"})
        self.assertEqual(loaded["key"], "stored-secret")
        self.save(clear_key=True)
        self.assertEqual(rc.load_config(self.path, env={"DSH_DAFEIYU_REALTIME_KEY": "environment-secret"})["key"], "")
        with self.assertRaises(rc.RealtimeConfigError):
            self.save(key="secret", clear_key=True)

    def test_mutated_ram_endpoint_cannot_reuse_loaded_key(self):
        loaded = self.save(key="stored-secret")
        loaded["endpoint"] = "wss://other.example/custom"
        with self.assertRaises(rc.RealtimeConfigError):
            rc.validate_config(loaded)
        with self.assertRaises(rc.RealtimeConfigError):
            rc.consent_identity(loaded)

    def test_ciphertext_cannot_be_copied_to_a_different_endpoint(self):
        self.save(key="stored-secret")
        document = self.document()
        document["endpoint"] = "wss://other.example/custom"
        self.path.write_text(json.dumps(document), encoding="utf-8")
        before = self.path.read_bytes()
        config = rc.load_config(self.path, env={"DSH_DAFEIYU_REALTIME_KEY": "environment-secret"})
        self.assertEqual(config["key"], "")
        self.assertEqual(config["error"], rc.ERRORS["binding"])
        with self.assertRaises(rc.RealtimeConfigError):
            self.save(key="replacement")
        self.assertEqual(self.path.read_bytes(), before)

    def test_unreadable_file_is_not_replaced_and_error_never_echoes_path(self):
        self.save()
        before = self.path.read_bytes()
        with patch.object(Path, "open", side_effect=PermissionError("secret-token /private/location")):
            config = rc.load_config(self.path)
            with self.assertRaises(rc.RealtimeConfigError) as caught:
                self.save(clear_key=True)
        self.assertEqual(config["error"], rc.ERRORS["read"])
        self.assertEqual(str(caught.exception), rc.ERRORS["read"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_unsupported_extra_and_duplicate_json_are_preserved(self):
        self.save()
        valid = self.document()
        variants = [b"not JSON secret-token", b"[]", b'{"version":1,"version":1}',
                    json.dumps({**valid, "version": 99}).encode(),
                    json.dumps({**valid, "extra_json": {"tools": []}}).encode(),
                    json.dumps({**valid, "key": "plaintext-secret"}).encode(),
                    b"x" * (rc.MAX_FILE_BYTES + 1)]
        for body in variants:
            with self.subTest(length=len(body)):
                self.path.write_bytes(body)
                loaded = rc.load_config(self.path, env={"DSH_DAFEIYU_REALTIME_KEY": "do-not-fallback"})
                self.assertEqual(loaded["error"], rc.ERRORS["corrupt"])
                self.assertEqual(loaded["key"], "")
                with self.assertRaises(rc.RealtimeConfigError):
                    self.save(clear_key=True)
                self.assertEqual(self.path.read_bytes(), body)

    def test_decryption_failure_cannot_overwrite_existing_file(self):
        self.save(key="old-secret")
        before = self.path.read_bytes()
        with patch.object(rc, "unprotect_secret", side_effect=RuntimeError("old-secret PRIVATE-PATH")):
            loaded = rc.load_config(self.path, env={"DSH_DAFEIYU_REALTIME_KEY": "do-not-fallback"})
            self.assertEqual(loaded["error"], rc.ERRORS["decrypt"])
            self.assertFalse(loaded["key"])
            with self.assertRaises(rc.RealtimeConfigError):
                self.save(key="replacement")
        self.assertEqual(self.path.read_bytes(), before)

    def test_encryption_failure_is_fixed_safe_error_and_does_not_create_file(self):
        with patch.object(rc, "protect_secret", side_effect=RuntimeError("secret-token PRIVATE-PATH")):
            with self.assertRaises(rc.RealtimeConfigError) as caught:
                self.save(key="secret-token")
        self.assertEqual(str(caught.exception), rc.ERRORS["protect"])
        self.assertFalse(self.path.exists())

    def test_public_config_has_no_secret_ciphertext_or_fingerprint(self):
        loaded = self.save(key="stored-secret")
        public = rc.public_config({**loaded, "key_protected": "ciphertext", "key_hash": "private-hash"})
        self.assertNotIn("key", public)
        self.assertNotIn("key_protected", public)
        self.assertNotIn("key_hash", public)
        self.assertNotIn("stored-secret", repr(public))
        self.assertEqual(public["_revision"], loaded["_revision"])
        self.assertTrue(public["key_saved"])
        self.assertNotIn("untrusted-secret", repr(rc.public_config({"error": "untrusted-secret"})))

    def test_consent_revision_includes_every_option_and_key_only_in_ram(self):
        original = self.save(key="stored-secret")
        identity = rc.consent_identity(original)
        self.assertEqual(identity, rc.consent_identity(copy.deepcopy(original)))
        self.assertEqual(len(identity), 64)
        changes = {"endpoint": "wss://other.example/custom", "model": "other-model", "voice": "alloy",
                   "max_tokens": 16, "session_seconds": 60, "protocol": "beta", "key": "new-secret"}
        for field, value in changes.items():
            changed = copy.deepcopy(original)
            changed[field] = value
            if field == "endpoint":
                changed["_key_endpoint"] = value
            with self.subTest(field=field):
                self.assertNotEqual(identity, rc.consent_identity(changed))
        self.assertNotIn(identity, self.path.read_text(encoding="utf-8"))

    def test_stale_ui_revision_prevents_overwriting_newer_settings(self):
        first = self.save()
        self.save(model="newer")
        before = self.path.read_bytes()
        with self.assertRaises(rc.RealtimeConfigError) as caught:
            self.save(model="stale", expected_revision=first["_revision"])
        self.assertEqual(caught.exception.code, "changed")
        self.assertEqual(self.path.read_bytes(), before)

    def test_read_digest_protects_changes_during_atomic_save(self):
        self.save()
        original_assert = rc._assert_revision
        changed_body = None

        def concurrent_write(selected, revision):
            nonlocal changed_body
            document = self.document()
            document["model"] = "concurrent"
            changed_body = json.dumps(document).encode()
            self.path.write_bytes(changed_body)
            original_assert(selected, revision)

        with patch.object(rc, "_assert_revision", side_effect=concurrent_write):
            with self.assertRaises(rc.RealtimeConfigError) as caught:
                self.save(model="stale")
        self.assertEqual(caught.exception.code, "changed")
        self.assertEqual(self.path.read_bytes(), changed_body)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_atomic_fsync_and_replace_failure_preserve_original_and_cleanup(self):
        self.save(model="original")
        before = self.path.read_bytes()
        with patch.object(rc.os, "fsync", wraps=os.fsync) as sync, patch.object(
                rc.os, "replace", side_effect=OSError("private-path secret-token")):
            with self.assertRaises(rc.RealtimeConfigError) as caught:
                self.save(model="changed")
        self.assertEqual(caught.exception.code, "write")
        self.assertTrue(sync.called)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_error_config_cannot_be_used_for_connection_or_consent(self):
        config = {"error": rc.ERRORS["corrupt"]}
        with self.assertRaises(rc.RealtimeConfigError):
            rc.build_connection_url(config)
        with self.assertRaises(rc.RealtimeConfigError):
            rc.consent_identity(config)


if __name__ == "__main__":
    unittest.main()
