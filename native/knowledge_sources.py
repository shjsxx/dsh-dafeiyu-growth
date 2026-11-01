"""Fixed, data-only curriculum sources and conservative normalizers.

Source text never selects another URL, runs code, becomes a system instruction,
or changes permission boundaries. Facts are rendered by our own templates.

License scope: this file is MIT except the REVIEWED_ANSWERS dict below, whose
three Chinese adaptations are derived from Stack Overflow answers licensed
CC BY-SA 4.0; see native/third_party/Stack-Overflow-ATTRIBUTION.txt.
"""
from __future__ import annotations

import base64
import hashlib
import html
import io
import gzip
import json
import re
import time
import urllib.error
import urllib.request

from web_learning import _NoRedirect

MAX_BYTES = 512 * 1024
# Platform entries are deliberately metadata-only until an owner-approved official
# API/RSS adapter is configured. They never imply permission to scrape login pages,
# private feeds, anti-bot challenges, or download video media.
PLATFORM_SOURCES = {
    "zhihu_public": {
        "name": "知乎 · 公开知识摘要", "category": "平台公开源",
        "mode": "official_api_or_rss", "status": "未连接",
        "description": "常规页面限制普通爬虫；等待可用的官方内容接口或创作者授权，不冒用爬虫身份。", 
        "ingest": "摘要与来源元数据，不保存全文或账号信息",
    },
    "short_video_public": {
        "name": "短视频 · 公开知识元数据", "category": "平台公开源",
        "mode": "official_api_or_rss", "status": "未连接",
        "description": "仅允许官方公开 API/RSS 的标题、摘要和来源；不下载视频、不绕过验证、不采集推荐流。",
        "ingest": "标题/摘要/主题标签白名单，需逐项审核",
    },
}


def platform_catalog_snapshot():
    return [{"id": key, **value} for key, value in PLATFORM_SOURCES.items()]


CATALOG = {
    "countries": {
        "name": "Countries 国家/地区资料", "version": "3.4.1 · 已审阅的首都字段子集", "domain": "geography", "license": "MIT",
        "url": "https://raw.githubusercontent.com/annexare/Countries/1c393325611d89d3511de01269a4f60fe9dc1c6f/dist/countries.min.json",
        "api": "https://api.github.com/repos/annexare/Countries/contents/dist/countries.min.json?ref=1c393325611d89d3511de01269a4f60fe9dc1c6f",
        "licenseUrl": "https://github.com/annexare/Countries/blob/1c393325611d89d3511de01269a4f60fe9dc1c6f/LICENSE",
    },
    "cldr_units": {
        "name": "Unicode CLDR 中文计量单位", "version": "48.0.0 · 名称与书写格式", "domain": "science", "license": "Unicode-3.0",
        "url": "https://raw.githubusercontent.com/unicode-org/cldr-json/48.0.0/cldr-json/cldr-units-full/main/zh/units.json",
        "api": "https://api.github.com/repos/unicode-org/cldr-json/contents/cldr-json/cldr-units-full/main/zh/units.json?ref=48.0.0",
        "licenseUrl": "https://github.com/unicode-org/cldr-json/blob/48.0.0/LICENSE",
    },
    "mime_db": {
        "name": "mime-db 文件格式元数据", "version": "1.54.0 · 常见格式子集", "domain": "computing", "license": "MIT",
        "url": "https://raw.githubusercontent.com/jshttp/mime-db/v1.54.0/db.json",
        "api": "https://api.github.com/repos/jshttp/mime-db/contents/db.json?ref=v1.54.0",
        "licenseUrl": "https://github.com/jshttp/mime-db/blob/v1.54.0/LICENSE",
    },
    "stackexchange": {
        "name": "Stack Overflow · 审阅问答摘要", "version": "3 则固定问答 · 中文改写 2026.10.31", "domain": "computing", "license": "CC BY-SA 4.0",
        "url": "https://api.stackexchange.com/2.3/answers/26853961;6581949;36798076?site=stackoverflow&pagesize=3&filter=withbody",
        "api": "", "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0/",
    },
    "usgs_live": {
        "name": "USGS · 最近一小时动态监测", "version": "GeoJSON · M2.5+ past hour", "domain": "geography", "license": "USGS public domain",
        "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_hour.geojson",
        "api": "", "licenseUrl": "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
    },
    "core_curriculum": {"name": "云岚基础知识小课", "version": "2026.10.31.1", "domain": "general", "license": "原创讲解", "url": "", "licenseUrl": ""},
}
# Only stable, reviewed pairs are imported. Not a list of sovereign states, not
# geopolitical/travel advice, and no inference about contested/multiple seats.
CAPITALS = {
    "CN": ("中国", "北京", "Beijing"), "FR": ("法国", "巴黎", "Paris"),
    "CA": ("加拿大", "渥太华", "Ottawa"), "JP": ("日本", "东京", "Tokyo"),
    "KR": ("韩国", "首尔", "Seoul"), "GB": ("英国", "伦敦", "London"),
    "DE": ("德国", "柏林", "Berlin"), "IT": ("意大利", "罗马", "Rome"),
    "ES": ("西班牙", "马德里", "Madrid"), "PT": ("葡萄牙", "里斯本", "Lisbon"),
    "GR": ("希腊", "雅典", "Athens"), "AU": ("澳大利亚", "堪培拉", "Canberra"),
    "NZ": ("新西兰", "惠灵顿", "Wellington"), "IN": ("印度", "新德里", "New Delhi"),
    "TH": ("泰国", "曼谷", "Bangkok"), "PH": ("菲律宾", "马尼拉", "Manila"),
    "KE": ("肯尼亚", "内罗毕", "Nairobi"), "BR": ("巴西", "巴西利亚", "Brasília"),
    "AR": ("阿根廷", "布宜诺斯艾利斯", "Buenos Aires"), "MX": ("墨西哥", "墨西哥城", "Mexico City"),
    "CL": ("智利", "圣地亚哥", "Santiago"), "TR": ("土耳其", "安卡拉", "Ankara"),
}
UNIT_PREFIXES = {"length", "mass", "area", "volume", "duration", "angle", "electric", "frequency", "power", "temperature", "speed", "force", "torque", "light", "digital", "graphics", "energy", "pressure"}
COMMON_MIME = {"application/json", "application/pdf", "application/zip", "application/xml", "application/octet-stream", "application/gzip", "application/wasm", "application/javascript", "text/javascript", "text/plain", "text/html", "text/css", "text/csv", "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "audio/mpeg", "audio/ogg", "audio/wav", "video/mp4", "video/webm"}


def _record(key, domain, topic, aliases, answer, intent):
    return {"key": key, "domain": domain, "topic": topic, "aliases": aliases, "answer": answer, "intent": intent}


def normalize_countries(raw):
    if not isinstance(raw, dict) or len(raw) > 1000:
        raise ValueError("country schema")
    records = []
    for code, (chinese, capital, expected) in CAPITALS.items():
        row = raw.get(code)
        if not isinstance(row, dict):
            continue
        actual = row.get("capital")
        if not isinstance(actual, str) or actual.casefold() != expected.casefold():
            continue
        name = row.get("name")
        aliases = [chinese, chinese + "首都"]
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z .'-]{2,60}", name):
            aliases.append(name)
        records.append(_record("capital-" + code.lower(), "geography", chinese + "的首都", aliases,
            f"{chinese}的首都是{capital}（{expected}）。这是 Countries 3.4.1 中已审阅的首都字段，不是实时旅行或地缘政治资料。", "capital"))
    if not records:
        raise ValueError("no reviewed capitals")
    return records, len(raw) - len(records)


def normalize_units(raw):
    try:
        rows = raw["main"]["zh"]["units"]["long"]
    except (KeyError, TypeError):
        raise ValueError("unit schema") from None
    if not isinstance(rows, dict) or len(rows) > 1000:
        raise ValueError("unit count")
    records = []
    for key, row in rows.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,70}", key) or key.split("-", 1)[0] not in UNIT_PREFIXES or not isinstance(row, dict):
            continue
        name, pattern = row.get("displayName"), row.get("unitPattern-count-other")
        if not isinstance(name, str) or not 1 <= len(name) <= 32 or not isinstance(pattern, str) or len(pattern) > 70 or pattern.count("{0}") != 1:
            continue
        if any(char in name + pattern for char in "<>\r\n\t") or re.search(r"\{(?!0\})", pattern):
            continue
        # Names/patterns only. We never derive conversion factors from CLDR.
        domain = "computing" if key.startswith(("digital-", "graphics-")) else "astronomy" if key in ("length-light-year", "length-parsec", "length-astronomical-unit") else "science"
        records.append(_record(key, domain, name + "的单位名称", [name, key, key.split("-", 1)[1]],
            f"{key} 的中文单位名称是“{name}”；资料中的书写示例为“{pattern.replace('{0}', '1')}”。这条记录提供名称和格式，不提供换算公式。", "unit_name"))
    if not records:
        raise ValueError("no unit names")
    return records, len(rows) - len(records)


def normalize_mime(raw):
    if not isinstance(raw, dict) or len(raw) > 6000:
        raise ValueError("mime schema")
    records = []
    for mime in sorted(COMMON_MIME):
        row = raw.get(mime)
        if not isinstance(row, dict):
            continue
        extensions = row.get("extensions", [])
        if not isinstance(extensions, list):
            continue
        extensions = list(dict.fromkeys(ext for ext in extensions[:20] if isinstance(ext, str) and re.fullmatch(r"[a-z0-9]{1,15}", ext)))[:8]
        charset = row.get("charset", "")
        charset = charset if isinstance(charset, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,20}", charset) else ""
        answer = f"{mime} 是一种 MIME 内容类型。"
        if extensions:
            answer += "资料列出的常见扩展名包括 " + "、".join("." + ext for ext in extensions) + "。"
        if charset:
            answer += f"该条目的默认字符集记录为 {charset}。"
        answer += "扩展名和 MIME 名称都不能单独证明真实文件内容或安全性。"
        aliases = [mime] + ["." + ext for ext in extensions] + [ext + "格式" for ext in extensions]
        records.append(_record(mime.replace("/", "-"), "computing", mime, aliases, answer, "mime"))
    if not records:
        raise ValueError("no common MIME formats")
    return records, len(raw) - len(records)


# These are reviewed Chinese adaptations of three known, attributed answers,
# not a generic HTML-to-training-text pipeline. Changed/missing markers, owners,
# license, or API backoff makes the entire batch fail without deleting cache.
# CC BY-SA 4.0 content: keep attribution and share-alike when redistributing
# these three adaptations (the rest of this file remains MIT).
REVIEWED_ANSWERS = {
    26853961: {"question": 38987, "owner": "Aaron Hall", "owner_id": 541136,
        "topic": "Python 字典浅合并", "aliases": ["Python字典合并", "字典浅合并", "dict合并"],
        "markers": ["z = x | y", "z = {**x, **y}", "3.9"],
        "answer": "Python 3.9 及以上可以用 x | y 新建浅合并字典；相同键取右侧 y 的值。Python 3.5 起也可以用 {**x, **y}。这不是嵌套字典的递归深合并。"},
    6581949: {"question": 100003, "owner": "Bite code", "owner_id": 9951,
        "topic": "Python 元类", "aliases": ["Python元类", "元类", "metaclass"],
        "markers": ["Classes are objects", "Metaclasses", "type"],
        "answer": "在 Python 中，类本身也是对象。元类是创建类对象的类；默认通常由 type 创建类。它属于较高级机制，普通应用未必需要自定义元类。"},
    36798076: {"question": 136097, "owner": "Du D.", "owner_id": 1302259,
        "topic": "Python 类方法与静态方法", "aliases": ["类方法", "静态方法", "classmethod", "staticmethod"],
        "markers": ["@classmethod", "@staticmethod", "class-level"],
        "answer": "Python 的类方法使用 @classmethod，并接收调用它的类 cls，常用于依赖类状态或构造替代实例的方法。@staticmethod 没有隐式的 self/cls 参数，适用于放在类名空间中但不依赖实例的操作。"},
}


def normalize_stackexchange(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list) or len(raw["items"]) > 10:
        raise ValueError("public Q&A schema")
    if raw.get("error_id") is not None or raw.get("backoff") or raw.get("has_more") is True:
        raise ValueError("public API unavailable or requires backoff")
    records, seen = [], set()
    for row in raw["items"]:
        if not isinstance(row, dict) or type(row.get("answer_id")) is not int:
            continue
        answer_id = row["answer_id"]
        expected = REVIEWED_ANSWERS.get(answer_id)
        owner = row.get("owner")
        body = row.get("body")
        if answer_id in seen:
            raise ValueError("duplicate public answer")
        if expected is None or row.get("question_id") != expected["question"] or row.get("content_license") != "CC BY-SA 4.0":
            continue
        if not isinstance(owner, dict) or owner.get("user_id") != expected["owner_id"] or html.unescape(str(owner.get("display_name", ""))) != expected["owner"]:
            continue
        if not isinstance(body, str) or not 10 <= len(body) <= 80000:
            continue
        if not all(marker in html.unescape(body) for marker in expected["markers"]):
            continue
        seen.add(answer_id)
        attribution = (f" 中文摘要改写自 {expected['owner']} 等人的 Stack Overflow 回答："
                       f"https://stackoverflow.com/a/{answer_id} 。本条摘要按 CC BY-SA 4.0 提供："
                       "https://creativecommons.org/licenses/by-sa/4.0/ 。摘要经人工审阅，不是实时权威结论；代码不执行。")
        records.append(_record("answer-" + str(answer_id), "computing", expected["topic"], expected["aliases"], expected["answer"] + attribution, "concept"))
    if len(records) != len(REVIEWED_ANSWERS):
        raise ValueError("reviewed public answers changed; keep old cache")
    return records, len(raw["items"]) - len(records)


NORMALIZERS = {"countries": normalize_countries, "cldr_units": normalize_units, "mime_db": normalize_mime, "stackexchange": normalize_stackexchange}


def _get_json(url, api=False, allow_gzip=False):
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json", "Accept-Encoding": "gzip" if allow_gzip else "identity", "User-Agent": "Yunlan/0.6 public knowledge reader (no private payload)"})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=12) as response:
        encoding = response.headers.get("Content-Encoding", "identity").lower()
        if encoding not in (("", "identity", "gzip") if allow_gzip else ("", "identity")):
            raise ValueError("compressed data refused")
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_BYTES:
            raise ValueError("oversized")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("oversized")
    wire_size = len(body)
    if allow_gzip and (encoding == "gzip" or body.startswith(b"\x1f\x8b")):
        with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as compressed:
            body = compressed.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("decompressed data oversized")
    raw = json.loads(body.decode("utf-8-sig"))
    if api and isinstance(raw, dict) and raw.get("encoding") == "base64":
        value = raw.get("content")
        if not isinstance(value, str):
            raise ValueError("API schema")
        decoded = base64.b64decode("".join(value.split()), validate=True)
        if len(decoded) > MAX_BYTES:
            raise ValueError("oversized")
        raw = json.loads(decoded.decode("utf-8-sig"))
    fingerprint = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return raw, wire_size, fingerprint


def fetch_knowledge_source(source_id):
    """No arbitrary URL parameter and no user text; at most two fixed GETs."""
    if not isinstance(source_id, str) or source_id not in NORMALIZERS:
        return {"ok": False, "source": source_id, "errorCode": "schema"}
    source = CATALOG[source_id]
    started = time.monotonic()
    error = "network"
    for endpoint, api in ((source["url"], False), (source["api"], True)):
        if not endpoint:
            continue
        try:
            raw, size, fingerprint = _get_json(endpoint, api, True) if source_id == "stackexchange" else _get_json(endpoint, api)
            if source_id == "stackexchange" and isinstance(raw, dict):
                retry = raw.get("backoff", 0)
                if type(retry) is int and retry > 0:
                    return {"ok": False, "source": source_id, "errorCode": "rate_limit", "retry_after": retry,
                            "duration_ms": (time.monotonic() - started) * 1000}
            records, rejected = NORMALIZERS[source_id](raw)
            return {"ok": True, "source": source_id, "records": records, "rejected": rejected, "validated": len(records), "bytes": size, "hash": fingerprint, "duration_ms": (time.monotonic() - started) * 1000}
        except (urllib.error.URLError, TimeoutError, OSError):
            error = "network"
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            error = "schema"
    return {"ok": False, "source": source_id, "errorCode": error, "duration_ms": (time.monotonic() - started) * 1000}
