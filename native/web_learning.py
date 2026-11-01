"""Read-only public emotional-cue updates, with no private query or payload.

Automation uses only a fixed Unicode CLDR Chinese annotation source; it cannot
follow downloaded instructions, links, or arbitrary search queries. The legacy
custom JSON reader remains bounded at 256KB. Built-in CLDR data is bounded at
768KB, contains only selected emoji/whitelisted short Chinese cues, and uses the
permissive Unicode-3.0 data license (notice bundled under native/third_party).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

EMOTIONS = ("joy", "sad", "anger", "anxious", "tired", "calm")
MAX_BYTES = 256 * 1024
MAX_WORDS = 500
TIMEOUT = 12
PUBLIC_SOURCE = "https://raw.githubusercontent.com/unicode-org/cldr-json/48.0.0/cldr-json/cldr-annotations-full/annotations/zh/annotations.json"
PUBLIC_SOURCE_API = "https://api.github.com/repos/unicode-org/cldr-json/contents/cldr-json/cldr-annotations-full/annotations/zh/annotations.json?ref=48.0.0"
PUBLIC_SOURCE_NAME = "Unicode CLDR 48.0.0 中文表情注释 · Unicode-3.0"
PUBLIC_LICENSE = "https://github.com/unicode-org/cldr-json/blob/48.0.0/cldr-json/cldr-annotations-full/LICENSE"
MAX_PUBLIC_BYTES = 768 * 1024
# Only these emotional glyphs and exact, short Chinese labels may be learned.
# Generic faces, unrelated tags and any downloaded instructions are ignored.
PUBLIC_EMOJI = {
    "joy": tuple("😀😃😄😁😆😊😂🤣🥰😍🤩🥳"),
    "sad": tuple("😢😭😞😔🙁☹💔"),
    "anger": tuple("😠😡🤬😤"),
    "anxious": tuple("😨😰😱😧😟😬"),
    "tired": tuple("😪😴🥱😫😩"),
    "calm": ("😌",),
}
PUBLIC_WORDS = {
    "joy": set("高兴 快乐 开心 幸福 喜悦 大笑 笑脸 嘿嘿 哈哈 呵呵 嘻嘻 微笑 喜笑颜开 兴高采烈 捧腹大笑 满意 心花怒放 眉开眼笑 笑哭了".split()),
    "sad": set("伤心 难过 哀伤 哭泣 大哭 哭脸 失望 悲伤 沮丧 不开心 不高兴 苦恼 委屈 心碎 悲痛 忧伤 失落 伤感".split()),
    "anger": set("愤怒 生气 怒火 恼火 气愤 不爽 火冒三丈 发怒 愤愤不平 怒气冲冲".split()),
    "anxious": set("焦虑 紧张 担心 担忧 害怕 恐惧 惊恐 惧怕 忧虑 不安 惶恐 惊慌 惊吓 惊惧 惴惴不安".split()),
    "tired": set("疲惫 疲倦 疲劳 困倦 想睡 累了 打哈欠 哈欠 倦容 睡觉 瞌睡".split()),
    "calm": set("如释重负 松了口气 松口气 放松 平静 安心 释然 轻松 舒缓 缓和".split()),
}


class LearningSourceError(ValueError):
    pass


def validate_source(url: str) -> str:
    url = " ".join(str(url or "").split())
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise LearningSourceError("source must be an https URL")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise LearningSourceError("plain HTTP is only allowed for localhost")
    if parsed.username or parsed.password:
        raise LearningSourceError("credentials in the URL are not allowed")
    if parsed.fragment:
        raise LearningSourceError("source must not contain a fragment")
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _mapping(raw) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        entries = raw.get("entries")
        if isinstance(entries, list):
            for item in entries:
                if isinstance(item, dict):
                    emotion = item.get("emotion")
                    words = item.get("words")
                    if emotion in EMOTIONS and isinstance(words, list):
                        result.setdefault(emotion, []).extend(words)
        else:
            for emotion, words in raw.items():
                if emotion in EMOTIONS and isinstance(words, list):
                    result.setdefault(emotion, []).extend(words)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                emotion, word = item
                if emotion in EMOTIONS and isinstance(word, str):
                    result.setdefault(emotion, []).append(word)
    cleaned: dict[str, list[str]] = {}
    seen = set()
    total = 0
    for emotion, words in result.items():
        bucket = []
        for word in words:
            if not isinstance(word, str):
                continue
            cue = " ".join(word.split())[:20]
            if not cue or any(ord(char) < 32 for char in cue):
                continue
            key = (emotion, cue)
            if key in seen:
                continue
            seen.add(key)
            bucket.append(cue)
            total += 1
            if total >= MAX_WORDS:
                break
        if bucket:
            cleaned[emotion] = bucket
        if total >= MAX_WORDS:
            break
    if not cleaned:
        raise LearningSourceError("source did not contain emotion words")
    return cleaned


def fetch_lexicon(url: str) -> dict:
    try:
        source = validate_source(url)
    except LearningSourceError as error:
        return {"ok": False, "error": str(error)}
    request = urllib.request.Request(
        source,
        headers={
            "Accept": "application/json",
            "User-Agent": "Yunlan/0.4 (public lexicon learner)",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_BYTES:
                return {"ok": False, "error": "source is larger than 256 KB"}
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                return {"ok": False, "error": "source is larger than 256 KB"}
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "json" not in content_type and not body.lstrip().startswith((b"{", b"[")):
                return {"ok": False, "error": "source must return JSON"}
    except urllib.error.HTTPError as error:
        return {"ok": False, "error": f"source returned HTTP {error.code}"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"ok": False, "error": "source could not be fetched"}
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "source is not valid JSON"}
    try:
        mapping = _mapping(raw)
    except LearningSourceError as error:
        return {"ok": False, "error": str(error)}
    return {
        "ok": True, "mapping": mapping,
        "count": sum(len(words) for words in mapping.values()),
        "host": urllib.parse.urlsplit(source).hostname or "",
    }


def parse_public_annotations(raw):
    root = raw.get("annotations") if isinstance(raw, dict) else None
    identity = root.get("identity") if isinstance(root, dict) else None
    if not isinstance(identity, dict) or identity.get("language") != "zh":
        raise LearningSourceError("公开资料不是预期的中文 CLDR 注释")
    annotations = root.get("annotations")
    if not isinstance(annotations, dict):
        raise LearningSourceError("公开资料缺少表情注释")
    mapping = {}
    for emotion, glyphs in PUBLIC_EMOJI.items():
        cues = set()
        for glyph in glyphs:
            row = annotations.get(glyph) or annotations.get(glyph + "\ufe0f")
            if not isinstance(row, dict):
                continue
            labels = []
            for key in ("default", "tts"):
                if isinstance(row.get(key), list):
                    labels.extend(word for word in row[key][:60] if isinstance(word, str))
            # The source must actually describe the selected glyph emotionally.
            matched = set(labels) & PUBLIC_WORDS[emotion]
            if matched:
                cues.add(glyph)
                cues.update(matched)
        if cues:
            mapping[emotion] = sorted(cues)
    if not mapping:
        raise LearningSourceError("公开资料没有可用的情感线索；保留现有词典")
    return _mapping(mapping)


def _download_public_annotations(url, with_metadata=False):
    import base64
    import hashlib
    request = urllib.request.Request(
        url, method="GET", headers={
            "Accept": "application/vnd.github.raw+json" if url == PUBLIC_SOURCE_API else "application/json",
            "User-Agent": "Yunlan/0.4 (public emotion annotations; no private payload)",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=TIMEOUT) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_PUBLIC_BYTES:
            raise LearningSourceError("公开资料超过 768 KB，已停止读取")
        body = response.read(MAX_PUBLIC_BYTES + 1)
        if len(body) > MAX_PUBLIC_BYTES:
            raise LearningSourceError("公开资料超过 768 KB，已停止读取")
    raw = json.loads(body.decode("utf-8-sig"))
    if url == PUBLIC_SOURCE_API and isinstance(raw, dict) and raw.get("encoding") == "base64":
        content = raw.get("content")
        if not isinstance(content, str):
            raise LearningSourceError("公开资料格式异常")
        decoded = base64.b64decode("".join(content.split()), validate=True)
        if len(decoded) > MAX_PUBLIC_BYTES:
            raise LearningSourceError("公开资料超过 768 KB，已停止读取")
        raw = json.loads(decoded.decode("utf-8-sig"))
    if with_metadata:
        fingerprint = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return raw, len(body), fingerprint
    return raw


def fetch_builtin_lexicon():
    """At most two fixed public GETs; no text/history/credentials arguments."""
    import time
    started = time.monotonic()
    error_code = "network"
    error = "暂时无法连接公开资料源；保留现有词典。"
    for url in (PUBLIC_SOURCE, PUBLIC_SOURCE_API):
        try:
            raw, size, fingerprint = _download_public_annotations(url, with_metadata=True)
            mapping = parse_public_annotations(raw)
            return {
                "ok": True, "mapping": mapping,
                "count": sum(len(words) for words in mapping.values()),
                "host": "unicode-org/cldr-json", "sources": [PUBLIC_SOURCE_NAME],
                "sourceUrl": PUBLIC_SOURCE, "licenseUrl": PUBLIC_LICENSE,
                "bytes": size, "hash": fingerprint,
                "duration_ms": (time.monotonic() - started) * 1000,
            }
        except urllib.error.HTTPError as failure:
            error = f"公开资料源 HTTP {failure.code}；保留现有词典。"
        except (urllib.error.URLError, TimeoutError, OSError):
            error = "暂时无法连接公开资料源；保留现有词典。"
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            error_code = "schema"
            error = "公开资料格式不符或超过大小限制；保留现有词典。"
    return {"ok": False, "error": error, "errorCode": error_code,
            "duration_ms": (time.monotonic() - started) * 1000}
