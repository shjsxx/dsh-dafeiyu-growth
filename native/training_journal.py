"""Bounded, local audit trail. A completed download is never neural training.

Call begin BEFORE changing a model and finish(success) only AFTER its commit.
Only action_fit/success with actual update counters contributes SGD steps.
An interrupted row has UNKNOWN model-commit outcome, not proven failure.
Cross-file model/audit recovery still requires a model transaction marker.
One writer is expected; RLock + disk digest is not a cross-process lock.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path

KINDS = {
    "action_fit": "动作网络 · 权重更新", "emotion_fit": "情绪分类 · 在线拟合",
    "emotion_read": "情绪观察 · 非训练", "public_lexicon": "公开情感词典 · 收录",
    "knowledge_index": "知识库 · 校验与索引", "companion_adapt": "陪伴状态 · 调整",
    "control": "模型管理 · 非训练", "voice_io": "连续语音 · 识别与播报（非训练）",
    "computer_control": "电脑操作 · 逐项授权审计", 
}
DOMAINS = {"emotion": "情感", "action": "动作", "geography": "地理", "astronomy": "天文", "chemistry": "化学", "computing": "计算机", "general": "常识", "science": "科学"}
SOURCES = {
    "local_outcome": "本地练习结果", "local_feedback": "你的动作反馈",
    "local_chat": "直接对云岚说的话（不保存原文）", "local_touch": "触摸互动",
    "realtime_voice": "独立实时语音接口（未核对转写，仅数字日志）",
    "local_voice": "本机语音转写（不保存原文）", "typed_direct": "你的直接文字命令", "voice_direct": "你的直接语音命令", 
    "cldr_zh": "Unicode CLDR 中文表情注释", "countries": "公开国家资料",
    "cldr_units": "Unicode CLDR 单位资料", "mime_db": "公开媒体类型资料",
    "stackexchange": "Stack Overflow 审阅问答摘要", "usgs_live": "USGS 动态公开监测",
    "elements": "公开元素资料", "planets": "公开行星资料",
    "core_curriculum": "内置基础知识课程", "owner_control": "你的本地设置操作",
    "public_dataset": "公开许可情感数据集（不保存原文）",
}
STATUSES = {"queued": "排队", "running": "进行中", "success": "完成", "no_change": "无新增", "observed": "仅观察", "skipped": "未训练", "failed": "失败", "cancelled": "已取消", "interrupted": "上次中断（提交状态未知）"}
ERRORS = {"": "", "rate_limit": "公开接口要求稍后再试，已暂停请求", "network": "公开资料暂时不可用", "schema": "资料格式或大小不符", "storage": "本地保存失败", "protected": "存档受保护", "disabled": "自动读取已关闭", "interrupted": "审计未写完，模型提交结果未知", "commit_unknown": "模型提交结果未知，不计为训练成功或已证实失败", "model_rejected": "模型拒绝本次更新", "no_label": "没有明确自述标签，未更新权重", "no_new_data": "资料未变化", "cancelled": "已取消", "rollback": "已回滚模型（并非训练提升）", "reset": "已重置模型（并非训练提升）"}
METRICS = {
    "samples_before", "samples_after", "updates_before", "updates_after", "version_before", "version_after",
    "features_before", "features_after", "confidence_before", "confidence_after", "reward",
    "probability_before", "probability_after", "loss_before", "loss_after", "weight_steps", "trained", "adaptation_passes",
    "records_before", "records_after", "added", "changed", "removed", "validated", "rejected", "bytes", "duration_ms",
    "valence_before", "valence_after", "energy_before", "energy_after", "trust_before", "trust_after",
    "curiosity_before", "curiosity_after", "event_count", "public_cues_before", "public_cues_after", "characters", "confidence",
    "audio_input_bytes", "audio_output_bytes", "played_ms", "input_tokens", "output_tokens", "input_audio_tokens", "output_audio_tokens", "cached_tokens",
    "dataset_rows", "dataset_skipped", "dataset_validation", "dataset_accuracy_before", "dataset_accuracy_after",
    "dataset_balanced_before", "dataset_balanced_after", "dataset_macro_f1_before", "dataset_macro_f1_after",
    "dataset_duplicates", "dataset_conflicts", "dataset_classes", "dataset_min_class", "changed_weights",
}
VOICE_EVENTS = {'session_start', 'recognized', 'spoken', 'voice_interrupt', 'voice_reject', 'session_stop', 'voice_error', 'rt_connect', 'rt_start', 'rt_stop', 'rt_usage', 'rt_interrupt', 'rt_error', 'rt_disconnect', 'rt_limit', 'rt_recognized'}
COMPUTER_ACTIONS = {'open_notepad', 'open_calculator', 'volume_up', 'volume_down', 'volume_mute'}
DECISIONS = {'requested', 'executed', 'blocked', 'cancelled', 'expired', 'emergency_stop'}
LABEL_TEXT = {'session_start':'开启语音', 'recognized':'识别片段', 'spoken':'播报完成', 'voice_interrupt':'打断播报', 'voice_reject':'拒收片段', 'session_stop':'关闭语音', 'voice_error':'语音异常', 'open_notepad':'打开记事本', 'open_calculator':'打开计算器', 'volume_up':'调高音量', 'volume_down':'调低音量', 'volume_mute':'切换静音', 'requested':'操作请求', 'executed':'操作执行', 'blocked':'安全拦截', 'cancelled':'取消', 'expired':'过期', 'emergency_stop':'紧急停止'}
LABEL_TEXT.update({'rt_connect':'请求实时语音连接', 'rt_start':'实时音频通道就绪', 'rt_stop':'停止实时音频上传', 'rt_usage':'实时语音数字用量', 'rt_interrupt':'实时语音打断／截断', 'rt_error':'实时语音失败', 'rt_disconnect':'实时语音断线', 'rt_limit':'实时语音时长上限', 'rt_recognized':'云端转写（未核对）'})
REALTIME_REASONS = {'owner_stop':'主动关闭', 'settings_changed':'设置或密钥变化', 'local_mode':'切回本机语音', 'shutdown':'桌宠退出', 'audio_failed':'音频设备或缓冲失败', 'audit_failed':'数字审计失败', 'metadata_limit':'会话资源达到上限', 'session_limit':'会话时长达到上限', 'start_failed':'配置或依赖不可用', 'authentication_failed':'接口密钥或权限不符', 'rate_limited':'接口限流或额度不足', 'connection_failed':'连接失败', 'connection_closed':'连接中断', 'connect_timeout':'连接超时', 'handshake_timeout':'配置确认超时', 'redirect_refused':'拒绝接口重定向', 'dependency_unavailable':'实时接口依赖不可用', 'protocol_error':'接口协议数据不符', 'model_mismatch':'返回模型与授权不符', 'tools_not_disabled':'接口未禁用工具', 'resource_limit':'连接资源达到上限', 'backpressure':'音频发送积压', 'server_error':'服务端错误', 'audio_format_mismatch':'接口音频格式不符', 'voice_mismatch':'返回音色与授权不符', 'token_limit_mismatch':'接口未采纳回复上限', 'error':'实时接口错误', 'disconnected':'接口断开', 'limit':'时限结束', 'stopped':'连接已停止'}
LABEL_TEXT.update(REALTIME_REASONS)
LABEL_VALUES = {
    'rt_reason': set(REALTIME_REASONS),
    'voice_event': VOICE_EVENTS, 'computer_action': COMPUTER_ACTIONS, 'decision': DECISIONS,
    "action": {"rest", "run", "hop", "talk", "spin"},
    "emotion": {"joy", "sad", "anger", "anxious", "tired", "calm", "neutral"},
    "own_mood": {"relaxed", "cheerful", "curious", "sleepy", "comforting", "shy", "startled"},
}
FINAL = set(STATUSES) - {"queued", "running"}
LIMIT = 2000
MAX_BYTES = 4 * 1024 * 1024
MAX_NUMBER = 10 ** 13
RETENTION_SECONDS = 30 * 24 * 60 * 60
TOTAL_KEYS = ("runs", "failed", "weight_steps", "emotion_adaptation_passes", "knowledge_added", "interrupted_unknown", "voice_sessions", "voice_segments", "voice_spoken", "voice_interrupted", "voice_rejected", "computer_proposals", "computer_executed", "computer_blocked", "realtime_sessions", "realtime_segments", "realtime_interrupted", "realtime_errors", "realtime_duration_ms", "realtime_audio_input_bytes", "realtime_audio_output_bytes", "realtime_played_ms", "realtime_input_tokens", "realtime_output_tokens", "realtime_input_audio_tokens", "realtime_output_audio_tokens", "realtime_cached_tokens")
SIGNED_METRICS = {"reward", "valence_before", "valence_after"}
FRACTION_METRICS = {"confidence", "confidence_before", "confidence_after", "probability_before", "probability_after", "energy_before", "energy_after", "trust_before", "trust_after", "curiosity_before", "curiosity_after", "dataset_accuracy_before", "dataset_accuracy_after", "dataset_balanced_before", "dataset_balanced_after", "dataset_macro_f1_before", "dataset_macro_f1_after"}
CONTINUOUS_METRICS = SIGNED_METRICS | FRACTION_METRICS | {"duration_ms", "loss_before", "loss_after"}


def default_path():
    override = os.environ.get("DSH_DAFEIYU_TRAINING_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "training.json"
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "training.json"


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return -MAX_NUMBER <= value <= MAX_NUMBER
    return math.isfinite(value) and abs(value) <= MAX_NUMBER


def _counter(value):
    if not _finite(value) or value < 0 or int(value) != value:
        raise ValueError("invalid bounded counter")
    return int(value)


def _stamp(value):
    if not _finite(value) or value < 0:
        raise ValueError("invalid timestamp")
    return float(value)


def _metrics(raw):
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in raw.items():
        if not isinstance(key, str) or key not in METRICS or not _finite(value):
            continue
        if key in SIGNED_METRICS:
            if -1 <= value <= 1:
                result[key] = float(value)
        elif key in FRACTION_METRICS:
            if 0 <= value <= 1:
                result[key] = float(value)
        elif key in CONTINUOUS_METRICS:
            if value >= 0:
                result[key] = float(value)
        elif value >= 0 and int(value) == value:
            result[key] = int(value)
    return result


def _labels(raw):
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if isinstance(key, str) and key in LABEL_VALUES and isinstance(value, str) and value in LABEL_VALUES[key]}


def _scoped_metrics(kind, status, raw):
    values = _metrics(raw)
    # Caller-supplied summary flags are never evidence of actual SGD.
    values["weight_steps"] = 0
    values["trained"] = 0
    if kind == "action_fit" and status == "success":
        before, after = values.get("updates_before"), values.get("updates_after")
        if before is not None and after is not None and after >= before:
            values["weight_steps"] = after - before
            values["trained"] = int(after > before)
    if kind == "emotion_fit" and status == "success":
        passes = values.get("adaptation_passes", 0)
        values["adaptation_passes"] = passes
        values["trained"] = int(passes > 0)
    else:
        values.pop("adaptation_passes", None)
    if kind not in ("action_fit", "emotion_fit"):
        for key in ("loss_before", "loss_after", "probability_before", "probability_after"):
            values.pop(key, None)
    if kind not in ("action_fit", "emotion_fit", "control"):
        for key in ("updates_before", "updates_after", "samples_before", "samples_after"):
            values.pop(key, None)
    return values


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON constant")


def _read_bytes(path):
    with path.open("rb") as stream:
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("oversized journal")
    return content


def _validate(raw):
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1 or not isinstance(raw.get("events"), list):
        raise ValueError("invalid journal")
    revision, sequence = _counter(raw.get("revision")), _counter(raw.get("sequence"))
    totals_raw = raw.get("totals")
    if not isinstance(totals_raw, dict):
        raise ValueError("invalid totals")
    totals = {key: _counter(totals_raw.get(key, 0)) for key in TOTAL_KEYS}
    rows, previous = [], 0
    for row in raw["events"]:
        if not isinstance(row, dict):
            raise ValueError("invalid row")
        for key, allowed in (("kind", KINDS), ("domain", DOMAINS), ("source", SOURCES), ("status", STATUSES)):
            if not isinstance(row.get(key), str) or row[key] not in allowed:
                raise ValueError("invalid enum")
        event_id = _counter(row.get("id"))
        if event_id <= previous or event_id > sequence:
            raise ValueError("invalid event sequence")
        previous = event_id
        started, finished = _stamp(row.get("started")), _stamp(row.get("finished"))
        if (row["status"] in FINAL and finished < started) or (row["status"] not in FINAL and finished != 0):
            raise ValueError("invalid event time")
        error = row.get("error", "")
        error = error if isinstance(error, str) and error in ERRORS else ""
        raw_metrics = row.get("metrics", {})
        if not isinstance(raw_metrics, dict):
            raise ValueError("invalid metric container")
        finite_metrics = _metrics(raw_metrics)
        if any(key in METRICS and key not in finite_metrics for key in raw_metrics):
            raise ValueError("invalid persisted metric")
        scoped = _scoped_metrics(row["kind"], row["status"], finite_metrics)
        for key in ("weight_steps", "trained", "adaptation_passes"):
            if key in raw_metrics and raw_metrics[key] != scoped.get(key, 0):
                raise ValueError("unproven persisted training claim")
        rows.append({"id": event_id, "started": started, "finished": finished, "kind": row["kind"], "domain": row["domain"], "source": row["source"], "status": row["status"], "metrics": scoped, "labels": _labels(row.get("labels")), "error": error})
    if totals["runs"] < len(rows):
        raise ValueError("invalid run total")
    return {"version": 1, "revision": revision, "sequence": sequence, "totals": totals, "events": rows}


def _document_bytes(data):
    content = (json.dumps(_validate(data), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if len(content) > MAX_BYTES:
        raise ValueError("oversized journal")
    return content


def _write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


class TrainingJournal:
    def __init__(self, path=None, read_only=False, limit=LIMIT):
        self.path = Path(path) if path is not None else default_path()
        self.backup_path = self.path.with_name(self.path.name + ".bak")
        self.read_only = bool(read_only)
        try:
            self.limit = max(10, min(LIMIT, _counter(limit)))
        except ValueError:
            self.limit = LIMIT
        self.lock = threading.RLock()
        self.status, self.error = "new", ""
        self._disk_digest = None
        self._failed_finalizations = set()
        self.data = {"version": 1, "revision": 0, "sequence": 0, "totals": {key: 0 for key in TOTAL_KEYS}, "events": []}
        try:
            content = _read_bytes(self.path)
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self._protect()
            return
        try:
            self.data = _validate(json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant))
            self._disk_digest = hashlib.sha256(content).digest()
            self.status = "ready"
            now = _stamp(time.time())
            pending = [row for row in self.data["events"] if row["status"] not in FINAL]
            if pending and not self.read_only:
                next_data = copy.deepcopy(self.data)
                for row in next_data["events"]:
                    if row["status"] not in FINAL:
                        row.update(status="interrupted", finished=max(now, row["started"]), error="commit_unknown")
                        next_data["totals"]["interrupted_unknown"] += 1
                self._prune(next_data, now)
                if not self._commit(next_data):
                    self._protect("无法保存中断恢复状态；保留日志并暂停新记录。")
            else:
                self._prune(self.data, now)
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            self._protect()

    def _protect(self, message="训练日志损坏或不可读，保留原文件；新训练暂停，避免漏记。"):
        self.status, self.error = "protected", message

    def _prune(self, data, now):
        cutoff = max(0, now - RETENTION_SECONDS)
        data["events"] = [row for row in data["events"] if row["status"] not in FINAL or row["finished"] >= cutoff]
        while len(data["events"]) > self.limit:
            index = next((i for i, row in enumerate(data["events"]) if row["status"] in FINAL), None)
            if index is None:
                return False
            data["events"].pop(index)
        return True

    def _unchanged_disk(self):
        try:
            digest = hashlib.sha256(_read_bytes(self.path)).digest()
        except FileNotFoundError:
            if self._disk_digest is None:
                return True
        except (OSError, ValueError):
            self._protect()
            return False
        else:
            if digest == self._disk_digest:
                return True
        self._protect("训练日志已被其他写入者修改；保留现有文件并暂停新记录。")
        return False

    def _commit(self, data):
        if self.read_only or self.status == "protected":
            return False
        try:
            candidate = copy.deepcopy(data)
            candidate["revision"] = self.data["revision"] + 1
            normalized = _validate(candidate)
            content = _document_bytes(normalized)
            previous = _document_bytes(self.data)
            if not self._unchanged_disk():
                return False
            if self._disk_digest is not None:
                _write_atomic(self.backup_path, previous)
            _write_atomic(self.path, content)
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            self.status = "error"
            self.error = "训练日志无法保存；未记录的新训练不会开始。"
            return False
        self.data = normalized
        self._disk_digest = hashlib.sha256(content).digest()
        self.status, self.error = "ready", ""
        return True

    def begin(self, kind, domain, source, queued=False):
        if not all(isinstance(value, str) and value in allowed for value, allowed in ((kind, KINDS), (domain, DOMAINS), (source, SOURCES))):
            return None
        with self.lock:
            if self.read_only or self.status == "protected" or self._failed_finalizations:
                return None
            try:
                now = _stamp(time.time())
                next_data = copy.deepcopy(self.data)
                event_id = next_data["sequence"] + 1
                next_data["sequence"] = event_id
                next_data["totals"]["runs"] += 1
                next_data["events"].append({"id": event_id, "started": now, "finished": 0, "kind": kind, "domain": domain, "source": source, "status": "queued" if queued else "running", "metrics": {}, "labels": {}, "error": ""})
                if not self._prune(next_data, now):
                    return None
                return event_id if self._commit(next_data) else None
            except (ValueError, TypeError, OverflowError):
                return None

    def mark_running(self, event_id):
        try:
            event_id = _counter(event_id)
        except ValueError:
            return False
        with self.lock:
            if self._failed_finalizations:
                return False
            next_data = copy.deepcopy(self.data)
            row = next((row for row in next_data["events"] if row["id"] == event_id), None)
            if row is None or row["status"] != "queued":
                return False
            try:
                row.update(status="running", started=_stamp(time.time()))
            except ValueError:
                return False
            return self._commit(next_data)

    def finish(self, event_id, status="success", metrics=None, labels=None, error=""):
        if not isinstance(status, str) or status not in FINAL or not isinstance(error, str) or error not in ERRORS:
            return False
        try:
            event_id = _counter(event_id)
        except ValueError:
            return False
        with self.lock:
            next_data = copy.deepcopy(self.data)
            row = next((row for row in next_data["events"] if row["id"] == event_id), None)
            if row is None or row["status"] in FINAL:
                return False
            try:
                finished = max(_stamp(time.time()), row["started"])
            except ValueError:
                return False
            values = _scoped_metrics(row["kind"], status, metrics)
            values.setdefault("duration_ms", min(MAX_NUMBER, max(0, (finished - row["started"]) * 1000)))
            row.update(status=status, finished=finished, metrics=values, labels=_labels(labels), error="commit_unknown" if status == "interrupted" else error)
            if status == "failed":
                next_data["totals"]["failed"] += 1
            if status == "interrupted":
                next_data["totals"]["interrupted_unknown"] += 1
            next_data["totals"]["weight_steps"] += values["weight_steps"]
            if row["kind"] == "emotion_fit" and status == "success":
                next_data["totals"]["emotion_adaptation_passes"] += values.get("adaptation_passes", 0)
            if row["kind"] == "knowledge_index" and status == "success":
                next_data["totals"]["knowledge_added"] += values.get("added", 0)
            if row['kind'] == 'voice_io' and status in ('success', 'observed'):
                key = {'session_start':'voice_sessions', 'recognized':'voice_segments', 'spoken':'voice_spoken', 'voice_interrupt':'voice_interrupted', 'voice_reject':'voice_rejected'}.get(row['labels'].get('voice_event'))
                if key:
                    next_data['totals'][key] += 1
                if row['source'] == 'realtime_voice':
                    rt_event = row['labels'].get('voice_event')
                    rt_key = {'rt_start':'realtime_sessions', 'rt_recognized':'realtime_segments', 'rt_interrupt':'realtime_interrupted', 'rt_error':'realtime_errors'}.get(rt_event)
                    if rt_key:
                        next_data['totals'][rt_key] += 1
                    if rt_event == 'rt_stop':
                        next_data['totals']['realtime_duration_ms'] += int(values.get('duration_ms', 0))
                    if rt_event == 'rt_usage':
                        for metric in ('audio_input_bytes', 'audio_output_bytes', 'played_ms', 'input_tokens', 'output_tokens', 'input_audio_tokens', 'output_audio_tokens', 'cached_tokens'):
                            next_data['totals']['realtime_' + metric] += int(values.get(metric, 0))
            if row['kind'] == 'computer_control':
                next_data['totals']['computer_proposals'] += 1
                decision = row['labels'].get('decision')
                if status == 'success' and decision == 'executed':
                    next_data['totals']['computer_executed'] += 1
                if decision == 'blocked':
                    next_data['totals']['computer_blocked'] += 1
            self._prune(next_data, finished)
            ok = self._commit(next_data)
            if ok:
                self._failed_finalizations.discard(event_id)
            else:
                self._failed_finalizations.add(event_id)
            return ok

    def record(self, kind, domain, source, status="success", metrics=None, labels=None, error=""):
        if not isinstance(status, str) or status not in FINAL or not isinstance(error, str) or error not in ERRORS:
            return None
        event_id = self.begin(kind, domain, source)
        return event_id if event_id and self.finish(event_id, status, metrics, labels, error) else None

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "error": self.error, "revision": self.data["revision"], "total": self.data["totals"]["runs"], "totals": copy.deepcopy(self.data["totals"]), "retained": len(self.data["events"]), "limit": self.limit, "retention_days": RETENTION_SECONDS // 86400, "events": copy.deepcopy(list(reversed(self.data["events"]))) }
