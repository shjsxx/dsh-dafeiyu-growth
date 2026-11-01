"""Independent native Realtime settings; no network, microphone, or cloud config IO.

Only cloud_chat's current-user Windows DPAPI primitives are reused. Secrets and
consent fingerprints stay in RAM; disk contains only a target-bound DPAPI blob.
An environment key is automatically eligible only for the official default URL.
Custom targets require a newly entered key or an explicit keyless/clear save.
Saving settings does not authorize an upload; the parent owns session consent.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

from cloud_chat import protect_secret, unprotect_secret

VERSION = 1
DEFAULT_ENDPOINT = "wss://api.openai.com/v1/realtime"
DEFAULT_MODEL = "gpt-realtime"
DEFAULT_VOICE = "marin"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_SESSION_SECONDS = 600
DEFAULT_PROTOCOL = "ga"
MIN_TOKENS, MAX_TOKENS = 16, 4096
MIN_SESSION_SECONDS, MAX_SESSION_SECONDS = 60, 1800
MAX_FILE_BYTES = 128 * 1024
ERRORS = {
    "endpoint": "实时接口地址无效；请使用无账号、查询或片段的 HTTPS/WSS 地址，明文仅限本机回环。",
    "model": "模型名无效；请填写至多100位英文字母、数字或 . _ : / -。",
    "voice": "音色名无效；请填写至多64位英文字母、数字或 . _ -。",
    "tokens": "输出上限必须是16至4096的整数。",
    "seconds": "单次会话时限必须是60至1800秒的整数。",
    "protocol": "协议无效；只能选择 GA 或兼容服务专用 Beta。",
    "key": "密钥无效；只能输入1至8192位可打印 ASCII 字符，不能含换行或控制字符。",
    "key_options": "不能同时输入新密钥和选择清除密钥。",
    "target": "接口地址已改变；请为新地址重新输入密钥，或明确选择清除密钥。旧密钥和环境密钥不会自动转移。",
    "binding": "已保存密钥与接口地址不匹配；为保护密钥，未加载此配置。请先备份并人工修复配置文件。",
    "decrypt": "无法解密已保存密钥；未改写原文件。请使用原 Windows 用户，或先备份并人工修复配置文件。",
    "read": "无法读取实时配置；未改写原文件。请检查配置文件访问权限。",
    "corrupt": "实时配置损坏或格式不受支持；未改写原文件。请先备份并人工修复配置文件。",
    "changed": "实时配置已被其他操作修改；未保存。请关闭并重新打开设置。",
    "write": "无法安全保存实时配置；未授权任何连接。请检查保存位置及权限。",
    "protect": "无法使用当前 Windows 用户加密密钥；未保存。",
    "environment": "环境密钥未自动用于自定义接口；请重新输入密钥，或明确选择清除密钥。",
}
_FIELDS = ("endpoint", "model", "voice", "max_tokens", "session_seconds", "protocol")
_DISK_FIELDS = frozenset(("version", *_FIELDS, "key_protected", "environment_key_disabled"))
_MODEL = re.compile(r"[A-Za-z0-9._:/-]{1,100}\Z")
_VOICE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


class RealtimeConfigError(ValueError):
    """Fixed Chinese error code/message, never underlying exceptions or inputs."""

    def __init__(self, code="corrupt"):
        self.code = code if code in ERRORS else "corrupt"
        super().__init__(ERRORS[self.code])


def default_path(env=None) -> Path:
    env = os.environ if env is None else env
    if env.get("DSH_DAFEIYU_REALTIME_FILE"):
        return Path(env["DSH_DAFEIYU_REALTIME_FILE"])
    if env.get("DSH_DAFEIYU_GROWTH_FILE"):
        return Path(env["DSH_DAFEIYU_GROWTH_FILE"]).resolve().parent / "realtime.json"
    root = Path(env["DSH_HOME"]) if env.get("DSH_HOME") else Path.home() / ".dsh"
    return root / "dsh-dafeiyu-growth" / "realtime.json"


def normalize_endpoint(value: str) -> str:
    """Canonical query-free websocket URL; never repair whitespace/injection.

    A root or /v1 base appends /realtime; every other full custom path stays
    exact (including its trailing slash). Valid numeric non-default ports work.
    """
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 2048:
            raise ValueError
        if any(not 33 <= ord(char) <= 126 for char in value) or "\\" in value:
            raise ValueError
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "wss", "http", "ws") or not parsed.netloc:
            raise ValueError
        if "@" in parsed.netloc or "?" in value or "#" in value:
            raise ValueError
        host = parsed.hostname
        literal_host = host
        if not host or parsed.username is not None or parsed.password is not None:
            raise ValueError
        port = parsed.port
        # urlsplit accepts an empty :port and leading plus/zero variants.
        authority = parsed.netloc
        suffix = authority[authority.index("]") + 1:] if authority.startswith("[") else authority[len(host):]
        if suffix and (not re.fullmatch(r":[1-9][0-9]{0,4}", suffix) or port is None):
            raise ValueError
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        if ":" in host:
            ipaddress.IPv6Address(host)
            if "%" in host or not authority.startswith("["):
                raise ValueError
            host = ipaddress.IPv6Address(host).compressed
            display_host = f"[{host}]"
        else:
            if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                           for label in host.split(".")):
                raise ValueError
            display_host = host
        plain = parsed.scheme in ("http", "ws")
        if plain and literal_host not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError
        path = parsed.path
        if re.search(r"%(?![0-9A-Fa-f]{2})", path):
            raise ValueError
        decoded = unquote(path, errors="strict")
        if any(not 33 <= ord(char) <= 126 for char in decoded) or any(char in decoded for char in "\\?#%"):
            raise ValueError
        if any(segment in (".", "..") for segment in decoded.split("/")):
            raise ValueError
        if path in ("", "/", "/v1", "/v1/"):
            path = path.rstrip("/") + "/realtime"
        scheme = "ws" if plain else "wss"
        canonical_port = "" if port in (None, 80 if plain else 443) else f":{port}"
        return urlunsplit((scheme, display_host + canonical_port, path, "", ""))
    except (ValueError, TypeError, UnicodeError):
        raise RealtimeConfigError("endpoint") from None


def _integer(value, low, high, code):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RealtimeConfigError(code)
    return value


def _key(value):
    if not isinstance(value, str) or len(value) > 8192 or any(not 32 <= ord(c) <= 126 for c in value):
        raise RealtimeConfigError("key")
    return value


def validate_config(config):
    """Return only validated transport fields and RAM key, dropping extras.

    Loaded keys carry an in-memory endpoint binding, so modifying a loaded dict
    cannot accidentally forward its key to a new target. No IO is performed.
    """
    if not isinstance(config, dict):
        raise RealtimeConfigError("corrupt")
    if config.get("error"):
        code = next((code for code, message in ERRORS.items() if message == config["error"]), "corrupt")
        raise RealtimeConfigError(code)
    endpoint = normalize_endpoint(config.get("endpoint", DEFAULT_ENDPOINT))
    model = config.get("model", DEFAULT_MODEL)
    voice = config.get("voice", DEFAULT_VOICE)
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise RealtimeConfigError("model")
    if not isinstance(voice, str) or not _VOICE.fullmatch(voice):
        raise RealtimeConfigError("voice")
    protocol = config.get("protocol", DEFAULT_PROTOCOL)
    if protocol not in ("ga", "beta"):
        raise RealtimeConfigError("protocol")
    key = _key(config.get("key", ""))
    if key and config.get("_key_endpoint", endpoint) != endpoint:
        raise RealtimeConfigError("target")
    return {"endpoint": endpoint, "model": model, "voice": voice,
            "max_tokens": _integer(config.get("max_tokens", DEFAULT_MAX_TOKENS), MIN_TOKENS, MAX_TOKENS, "tokens"),
            "session_seconds": _integer(config.get("session_seconds", DEFAULT_SESSION_SECONDS),
                                        MIN_SESSION_SECONDS, MAX_SESSION_SECONDS, "seconds"),
            "protocol": protocol, "key": key}


def build_connection_url(config):
    """Build the sole permitted model query with proper URL encoding; no IO."""
    checked = validate_config(config)
    return checked["endpoint"] + "?" + urlencode({"model": checked["model"]})


def consent_identity(config):
    """Ephemeral consent revision over exact target, all options, and RAM key.

    Neither this hash nor the secret's fingerprint is written to disk.
    """
    checked = validate_config(config)
    identity = [checked[field] for field in _FIELDS]
    identity.append(hashlib.sha256(checked["key"].encode("ascii")).hexdigest())
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _base(selected):
    return {**validate_config({}), "key_saved": False, "error": None,
            "path": str(selected), "_revision": "missing", "_key_endpoint": "", "_key_source": "none"}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _snapshot(selected):
    """Read a bounded snapshot, distinguishing absence from unreadable data."""
    try:
        with selected.open("rb") as source:
            body = source.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        # A dangling symlink or inaccessible directory is not a missing config.
        if selected.is_symlink():
            raise RealtimeConfigError("read") from None
        return None, "missing"
    except OSError:
        raise RealtimeConfigError("read") from None
    if len(body) > MAX_FILE_BYTES:
        raise RealtimeConfigError("corrupt")
    return body, hashlib.sha256(body).hexdigest()


def _decode(body):
    try:
        raw = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs)
        if not isinstance(raw, dict) or set(raw) != _DISK_FIELDS:
            raise ValueError
        if type(raw["version"]) is not int or raw["version"] != VERSION:
            raise ValueError
        if not isinstance(raw["environment_key_disabled"], bool):
            raise ValueError
        protected = raw["key_protected"]
        if not isinstance(protected, str) or len(protected) > MAX_FILE_BYTES // 2:
            raise ValueError
        checked = validate_config(raw)
    except (ValueError, TypeError, UnicodeError):
        raise RealtimeConfigError("corrupt") from None
    if protected:
        try:
            envelope = json.loads(unprotect_secret(protected), object_pairs_hook=_pairs)
            if not isinstance(envelope, dict) or set(envelope) != {"version", "endpoint", "key"}:
                raise ValueError
            if type(envelope["version"]) is not int or envelope["version"] != VERSION or not _key(envelope["key"]):
                raise ValueError
        except Exception:
            raise RealtimeConfigError("decrypt") from None
        if envelope["endpoint"] != checked["endpoint"]:
            raise RealtimeConfigError("binding")
        checked["key"] = envelope["key"]
    return raw, checked


def load_config(path=None, env=None):
    """Read only realtime.json; return a RAM secret or fixed safe error.

    Missing files are not created. Corrupt/unreadable files never fall back to an
    environment key, and are never overwritten by save_config.
    """
    env = os.environ if env is None else env
    selected = Path(path) if path is not None else default_path(env)
    config = _base(selected)
    try:
        body, revision = _snapshot(selected)
        config["_revision"] = revision
        raw, checked = _decode(body) if body is not None else ({}, validate_config({}))
        config.update(checked)
        config["key_saved"] = bool(raw.get("key_protected"))
        if config["key"]:
            config["_key_source"] = "saved"
        elif not raw.get("environment_key_disabled", False):
            environment_key = _key(env.get("DSH_DAFEIYU_REALTIME_KEY", ""))
            if environment_key:
                if config["endpoint"] != DEFAULT_ENDPOINT:
                    raise RealtimeConfigError("environment")
                config["key"] = environment_key
                config["_key_source"] = "environment"
        config["_key_endpoint"] = config["endpoint"] if config["key"] else ""
    except RealtimeConfigError as error:
        config["key"] = ""
        config["key_saved"] = False
        config["_key_source"] = "none"
        config["_key_endpoint"] = ""
        config["error"] = str(error)
    return config


def public_config(config):
    """Allowlisted UI copy; no decrypted key, ciphertext, or secret fingerprint."""
    checked = validate_config({field: config[field] for field in _FIELDS if field in config})
    checked.pop("key")
    checked.update(key_saved=bool(config.get("key_saved")),
                   error=config.get("error") if config.get("error") in ERRORS.values() else
                         (ERRORS["corrupt"] if config.get("error") else None),
                   path=str(config.get("path", "")), _revision=config.get("_revision"))
    return checked


def _assert_revision(selected, revision):
    _body, current = _snapshot(selected)
    if current != revision:
        raise RealtimeConfigError("changed")


def save_config(endpoint, model, voice, max_tokens, session_seconds, protocol,
                key="", clear_key=False, path=None, *, expected_revision=None):
    """Atomically save explicit settings; never authorize capture or a connection.

    Empty key preserves a saved key only at the *exact* canonical endpoint.
    Changed endpoints require a new key or explicit clear (also disables env
    fallback). expected_revision optionally guards an entire settings edit.
    Every save additionally verifies its read digest immediately before replace.
    """
    selected = Path(path) if path is not None else default_path()
    checked = validate_config(dict(endpoint=endpoint, model=model, voice=voice,
                                   max_tokens=max_tokens, session_seconds=session_seconds,
                                   protocol=protocol, key=key))
    if not isinstance(clear_key, bool) or (clear_key and key):
        raise RealtimeConfigError("key_options")
    body, revision = _snapshot(selected)
    if expected_revision is not None and expected_revision != revision:
        raise RealtimeConfigError("changed")
    previous, old = _decode(body) if body is not None else ({}, validate_config({}))
    if checked["endpoint"] != old["endpoint"] and not key and not clear_key:
        raise RealtimeConfigError("target")
    protected = previous.get("key_protected", "")
    env_disabled = previous.get("environment_key_disabled", False)
    if clear_key:
        protected, env_disabled = "", True
    elif key:
        try:
            envelope = {"version": VERSION, "endpoint": checked["endpoint"], "key": key}
            protected = protect_secret(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")))
            if not isinstance(protected, str) or not protected:
                raise ValueError
        except Exception:
            raise RealtimeConfigError("protect") from None
        env_disabled = True
    document = {"version": VERSION, **{field: checked[field] for field in _FIELDS},
                "key_protected": protected, "environment_key_disabled": env_disabled}
    temporary = None
    try:
        selected.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix="." + selected.name + ".", suffix=".tmp", dir=selected.parent)
        temporary = Path(temporary_name)
        with os.fdopen(fd, "wb") as output:
            output.write((json.dumps(document, ensure_ascii=True, indent=2) + "\n").encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        _assert_revision(selected, revision)
        os.replace(temporary, selected)
        temporary = None
        # On POSIX also make the directory entry durable; Windows replace is atomic.
        if os.name != "nt":
            directory_fd = os.open(selected.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except RealtimeConfigError:
        raise
    except OSError:
        raise RealtimeConfigError("write") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return load_config(selected)
