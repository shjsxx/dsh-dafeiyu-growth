"""Owner-configured, independent cloud chat channel for 云岚.

This module deliberately does NOT use DSH's shared provider/session API. It
defaults to one approved short user message plus a fixed non-personal prompt.
Explicit volatile session consent can add at most six RAM-only dialogue rows,
with 4000 combined characters including the current input. Never tools, DSH
history/session IDs, attachments, files, screens or plugin extensions. The UI
must confirm each request unless its displayed endpoint/model has active
owner-approved continuous-session consent.

The API key is encrypted with Windows DPAPI (current user) and never written in
plaintext. If DPAPI is unavailable, a key can be supplied through
``DSH_DAFEIYU_CLOUD_KEY`` for the current process instead. Text and keys are not
logged. HTTP redirects are rejected so an approved request cannot be forwarded
to a different host.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = 1
MAX_TEXT = 2000
MAX_RESPONSE = 128 * 1024
MAX_TOKENS = 1024
MIN_TOKENS = 16
SYSTEM_PROMPT = (
    "你是云岚，一只住在用户桌面上的小云灵。"
    "请用简短、温和、有陪伴感的中文回答，认真回应内容，不机械重复免责声明。"
    "可以表达模拟的心情；被问到意识或感情真实性时，要诚实说明这是情感表达模拟，不声称真实主观体验。"
    "不要用愧疚、排他关系或要求用户一直陪伴来影响用户，也不要声称完成未提供证据的训练。"
    "不要声称自己拥有工具、系统权限、文件访问能力，也不要声称读取了未提供的信息。"
    "如果用户要求执行系统命令、修改权限或访问隐私数据，请说明这需要用户本人另行授权。"
)


class CloudConfigError(ValueError):
    pass


def default_path() -> Path:
    override = os.environ.get("DSH_DAFEIYU_CLOUD_FILE")
    if override:
        return Path(override)
    growth_file = os.environ.get("DSH_DAFEIYU_GROWTH_FILE")
    if growth_file:
        return Path(growth_file).resolve().parent / "cloud.json"
    dsh_home = os.environ.get("DSH_HOME")
    root = Path(dsh_home) if dsh_home else Path.home() / ".dsh"
    return root / "dsh-dafeiyu-growth" / "cloud.json"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise CloudConfigError("Windows DPAPI is required to store the API key")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, source_buffer = _blob(data)
    output = _DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    args = (ctypes.byref(source), None, None, None, None, flags, ctypes.byref(output))
    if not fn(*args):
        raise CloudConfigError("DPAPI operation failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def protect_secret(value: str) -> str:
    if not value:
        return ""
    return base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii")


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise CloudConfigError("stored API key is invalid") from error
    return _dpapi(raw, False).decode("utf-8")


def _validate_endpoint(endpoint: str) -> str:
    endpoint = " ".join(str(endpoint or "").split())
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise CloudConfigError("endpoint must be an http(s) URL")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise CloudConfigError("plain HTTP is only allowed for localhost")
    if parsed.username or parsed.password:
        raise CloudConfigError("credentials in the URL are not allowed")
    if parsed.query or parsed.fragment:
        raise CloudConfigError("endpoint must not contain a query or fragment")
    return endpoint


def normalize_endpoint(value: str) -> str:
    """Accept a base URL or a full chat-completions URL.

    Users commonly paste ``https://api.deepseek.com/v1``; the API path is
    ``/v1/chat/completions``. Do not guess for arbitrary custom paths.
    """
    endpoint = _validate_endpoint(value)
    parsed = urllib.parse.urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        final_path = path
    elif path in ("", "/v1"):
        final_path = f"{path}/chat/completions"
    else:
        final_path = path
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, final_path, "", ""))


def _validate_tokens(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_TOKENS <= value <= MAX_TOKENS:
        raise CloudConfigError(f"max tokens must be {MIN_TOKENS}-{MAX_TOKENS}")
    return value


def load_config(path=None, env=None):
    """Return config with decrypted key in memory; never log the key."""
    env = os.environ if env is None else env
    selected = Path(path) if path is not None else default_path()
    config = {
        "endpoint": "",
        "model": "",
        "max_tokens": 256,
        "key": "",
        "key_saved": False,
        "error": None,
        "path": str(selected),
    }
    try:
        raw = json.loads(selected.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        try:
            config["endpoint"] = normalize_endpoint(raw.get("endpoint", ""))
        except CloudConfigError:
            config["endpoint"] = ""
        config["model"] = str(raw.get("model", ""))[:80]
        try:
            config["max_tokens"] = _validate_tokens(raw.get("max_tokens", 256))
        except CloudConfigError:
            config["max_tokens"] = 256
        protected = raw.get("key_protected")
        key_endpoint = raw.get("key_endpoint")
        if isinstance(protected, str) and protected:
            if isinstance(key_endpoint, str) and key_endpoint and key_endpoint != config["endpoint"]:
                config["error"] = "saved API key belongs to a different endpoint; re-enter it"
            else:
                try:
                    config["key"] = unprotect_secret(protected)
                    config["key_saved"] = True
                except (CloudConfigError, OSError):
                    config["error"] = "saved API key cannot be decrypted; re-enter it"
    if not config["key"]:
        env_key = env.get("DSH_DAFEIYU_CLOUD_KEY", "")
        env_endpoint = env.get("DSH_DAFEIYU_CLOUD_KEY_ENDPOINT", "")
        host = urllib.parse.urlsplit(config["endpoint"]).hostname if config["endpoint"] else ""
        env_allowed = (not host or host in ("api.deepseek.com", "localhost", "127.0.0.1")
                       or (env_endpoint and env_endpoint == config["endpoint"]))
        if env_key and env_allowed:
            config["key"] = env_key
    return config


def save_config(endpoint, model, max_tokens, key="", path=None, clear_key=False):
    """Persist endpoint/model/cap; an empty key keeps the existing encrypted key."""
    selected = Path(path) if path is not None else default_path()
    endpoint = _validate_endpoint(endpoint)
    endpoint = normalize_endpoint(endpoint)
    model = " ".join(str(model or "").split())[:80]
    if not model:
        raise CloudConfigError("model is required")
    max_tokens = _validate_tokens(max_tokens)
    previous = {}
    try:
        previous = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    previous_endpoint = previous.get("endpoint")
    protected = previous.get("key_protected", "")
    if clear_key:
        protected = ""
    elif key:
        protected = protect_secret(str(key))
    elif protected and previous_endpoint and previous_endpoint != endpoint:
        raise CloudConfigError("endpoint changed; re-enter the API key for the new endpoint")
    document = {
        "version": VERSION,
        "endpoint": endpoint,
        "model": model,
        "max_tokens": max_tokens,
        "key_protected": protected,
        "key_endpoint": endpoint if protected else "",
    }
    selected.parent.mkdir(parents=True, exist_ok=True)
    temporary = selected.with_name(selected.name + ".tmp")
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, selected)
        try:
            os.chmod(selected, 0o600)
        except OSError:
            pass
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return load_config(selected)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def bounded_history(history, current_text=""):
    """Only caller-approved RAM dialogue; never system/tool roles or metadata.

    A session opt-in in ConversationDialog is required before supplying history.
    This transport additionally caps the combined content to 4000 characters.
    """
    if not isinstance(history, (list, tuple)):
        return []
    budget = max(0, 4000 - len(current_text))
    rows = []
    for row in reversed(history[-6:]):
        if not isinstance(row, dict) or row.get("role") not in ("user", "assistant"):
            continue
        text = row.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        text = text.strip()[:MAX_TEXT]
        if len(text) > budget:
            break
        rows.append({"role": row["role"], "content": text})
        budget -= len(text)
    return list(reversed(rows))


def ask_cloud(config, text, timeout=30.0, history=None):
    """Send approved text and optional explicitly session-approved bounded context.

    Default remains exact one-message/no-history. Never tools or DSH session ID.
    """
    if not isinstance(config, dict):
        return {"ok": False, "error": "cloud is not configured"}
    try:
        endpoint = normalize_endpoint(config.get("endpoint", ""))
        model = str(config.get("model", "")).strip()
        if not model:
            raise CloudConfigError("model is required")
        tokens = _validate_tokens(int(config.get("max_tokens", 256)))
    except (CloudConfigError, TypeError, ValueError) as error:
        return {"ok": False, "error": str(error)}
    approved = str(text or "").strip()
    if not approved:
        return {"ok": False, "error": "empty message"}
    if len(approved) > MAX_TEXT:
        return {"ok": False, "error": f"message is longer than {MAX_TEXT} characters"}
    payload = {
        "model": model,
        "messages": ([{"role": "system", "content": SYSTEM_PROMPT}]
                     + bounded_history(history, approved)
                     + [{"role": "user", "content": approved}]),
        "max_tokens": tokens,
        "stream": False,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "Yunlan/0.3 (local desktop pet)"}
    key = config.get("key") if isinstance(config.get("key"), str) else ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=max(5.0, min(60.0, float(timeout)))) as response:
            if getattr(response, "status", 200) != 200:
                return {"ok": False, "error": f"cloud returned HTTP {getattr(response, 'status', 'unknown')}"}
            body = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"ok": False, "error": "接口路径不存在（HTTP 404）；请填写完整的 .../chat/completions，或只填 base URL（如 https://api.deepseek.com/v1）"}
        if error.code in (401, 403):
            return {"ok": False, "error": f"认证失败（HTTP {error.code}）；请检查接口密钥是否有权限"}
        if error.code == 400:
            return {"ok": False, "error": "请求被拒绝（HTTP 400）；请检查模型名、输出上限和接口参数（DeepSeek 官方常用 deepseek-chat / deepseek-reasoner）"}
        return {"ok": False, "error": f"cloud returned HTTP {error.code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"ok": False, "error": "cloud connection failed or timed out"}
    if len(body) > MAX_RESPONSE:
        return {"ok": False, "error": "cloud response is too large"}
    try:
        data = json.loads(body.decode("utf-8"))
        choice = data["choices"][0]
        content = choice.get("message", {}).get("content") or choice.get("text")
    except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
        return {"ok": False, "error": "cloud response format was not recognized"}
    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "error": "cloud returned an empty answer"}
    return {"ok": True, "text": content.strip()[:4000], "endpoint": endpoint, "model": model}
