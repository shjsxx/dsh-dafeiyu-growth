"""Explicit local retrieval labels and a no-training end-to-end evaluator.

No automatic capture: add() requires consent is True. Only the one query,
stable candidate/relevance IDs and session group are stored, never candidate
texts or transcripts. The evaluator calls the real engine.recall; it does not
train, fetch data, or claim scores from too few independent human labels.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

VERSION = 1
LIMIT = 1000
MAX_BYTES = 2 * 1024 * 1024
SPLITS = ('train', 'validation', 'test', 'all')


def default_path():
    override = os.environ.get('DSH_DAFEIYU_REVIEW_FILE', '').strip()
    if override:
        return Path(override).expanduser()
    profile = os.environ.get('DSH_DAFEIYU_GROWTH_FILE', '').strip()
    root = Path(profile).expanduser().parent if profile else Path(os.environ.get('DSH_HOME') or Path.home() / '.dsh') / 'dsh-dafeiyu-growth'
    return root / 'retrieval_reviews.json'


def _identity(value):
    return (isinstance(value, str) and value == value.strip() and 0 < len(value) <= 96
            and not any(ord(char) < 32 for char in value))


def _ids(values):
    """Reject malformed or silently truncated label sets; never discard IDs."""
    if not isinstance(values, (list, tuple)) or len(values) > 40:
        raise ValueError('invalid ID list')
    if not all(_identity(value) for value in values) or len(set(values)) != len(values):
        raise ValueError('invalid or duplicate ID')
    return list(values)


def _clean_row(row):
    required = {'query', 'group', 'candidates', 'relevant', 'no_answer'}
    if not isinstance(row, dict) or not required <= row.keys() or set(row) - required - {'at'}:
        raise ValueError('invalid label schema')
    query, group = row['query'], row['group']
    if not isinstance(query, str) or not query.strip() or len(query) > 300 or '\x00' in query:
        raise ValueError('invalid query')
    if not _identity(group):
        raise ValueError('invalid group')
    candidates, relevant = _ids(row['candidates']), _ids(row['relevant'])
    no_answer = row['no_answer']
    if type(no_answer) is not bool or no_answer == bool(relevant):
        raise ValueError('select relevant records OR no-answer')
    if not set(relevant) <= set(candidates):
        raise ValueError('label is not in candidate set')
    at = row.get('at', 0)
    if type(at) is not int or not 0 <= at <= 10 ** 13:
        raise ValueError('invalid timestamp')
    return {'query': query.strip(), 'group': group, 'candidates': candidates,
            'relevant': relevant, 'no_answer': no_answer, 'at': at}


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


class RetrievalReviewStore:
    """Bounded explicit-consent store; corrupted files are read-only/protected.

    add/clear are atomic and leave in-memory rows unchanged on failure. Writes
    detect external file changes rather than overwrite labels from another UI.
    Invalid add payloads return False without poisoning a healthy store.
    """
    def __init__(self, path=None):
        self.path = Path(path).expanduser() if path is not None else default_path()
        self.status, self.rows, self._fingerprint = 'new', [], None
        try:
            with self.path.open('rb') as stream:
                body = stream.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError('too large')
            raw = json.loads(body.decode('utf-8'), object_pairs_hook=_json_object)
            if (not isinstance(raw, dict) or set(raw) != {'version', 'rows'}
                    or type(raw['version']) is not int or raw['version'] != VERSION
                    or not isinstance(raw['rows'], list) or len(raw['rows']) > LIMIT):
                raise ValueError('schema')
            rows = [_clean_row(row) for row in raw['rows']]
            self.rows, self.status = rows, 'ready'
            self._fingerprint = hashlib.sha256(body).digest()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, UnicodeDecodeError, RecursionError):
            self.status = 'protected'

    def _write(self, rows):
        if self.status == 'protected':
            return False
        temporary = None
        try:
            # Validate even public rows mutated directly by a caller.
            rows = [_clean_row(row) for row in rows]
            payload = json.dumps({'version': VERSION, 'rows': rows}, ensure_ascii=False,
                                 separators=(',', ':'), allow_nan=False).encode('utf-8')
            if len(payload) > MAX_BYTES or len(rows) > LIMIT:
                raise ValueError('too large')
            if self.path.exists():
                with self.path.open('rb') as stream:
                    current = stream.read(MAX_BYTES + 1)
                if hashlib.sha256(current).digest() != self._fingerprint:
                    self.status = 'protected'
                    return False
            elif self._fingerprint is not None:
                self.status = 'protected'
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # mkstemp gives a unique private file (0600 on POSIX), not a shared
            # predictable .tmp path. Replace publishes a complete document.
            descriptor, name = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.rows, self.status = rows, 'ready'
            self._fingerprint = hashlib.sha256(payload).digest()
            return True
        except (OSError, ValueError, TypeError, UnicodeError):
            self.status = 'error'
            return False
        finally:
            if (temporary is not None and temporary.parent.resolve() == self.path.parent.resolve()
                    and temporary.name.startswith(self.path.name + '.') and temporary.suffix == '.tmp'):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def add(self, query, candidates, relevant=(), *, no_answer=False, group='', consent=False):
        if consent is not True or self.status == 'protected':
            return False
        try:
            row = _clean_row({'query': query, 'group': group, 'candidates': candidates,
                              'relevant': relevant, 'no_answer': no_answer, 'at': int(time.time())})
        except (ValueError, TypeError, UnicodeError):
            return False
        # Retention is explicit and bounded; never drop invalid labels silently.
        return self._write((self.rows + [row])[-LIMIT:])

    def clear(self):
        """Explicitly erase labels atomically; protected files stay untouched."""
        return self._write([])


def partition(rows, name='test'):
    """Fixed session-group hash 60/20/20 split, independent of input order."""
    if name not in SPLITS:
        raise ValueError('unknown partition')
    if not isinstance(rows, (list, tuple)) or len(rows) > LIMIT:
        raise ValueError('invalid labels')
    clean = [_clean_row(row) for row in rows]
    result = []
    for row in clean:
        bucket = int(hashlib.sha256(row['group'].encode('utf-8')).hexdigest()[:8], 16) % 10
        split = 'train' if bucket < 6 else 'validation' if bucket < 8 else 'test'
        if name == 'all' or split == name:
            result.append(row)
    return result


def evaluate_labels(engine, rows, *, split='test', minimum_groups=3, k=5):
    """Score actual recall outputs, not stored candidate rankings.

    Deleted relevant IDs invalidate that row; deleted candidate IDs are also
    reported. Minimum 5 non-stale rows / 3 groups applies AFTER filtering. k
    is 1..20; ndcg5 is a legacy alias emitted only when k=5. No metrics are
    emitted for insufficient data, unavailable engines, or malformed outputs.
    """
    base = {'partition': split, 'training_performed': False}
    if (type(k) is not int or not 1 <= k <= 20 or type(minimum_groups) is not int
            or minimum_groups < 3):
        return {**base, 'status': 'invalid_options'}
    try:
        selected = partition(rows, split)
    except (ValueError, TypeError):
        return {**base, 'status': 'invalid_labels'}
    groups = len({row['group'] for row in selected})
    base.update(rows=len(selected), groups=groups)
    if len(selected) < 5 or groups < minimum_groups:
        return {**base, 'status': 'insufficient_data',
                'message': '需要至少5条人工标注、3个独立会话组；未生成准确率。'}
    data = getattr(engine, 'data', None)
    if (getattr(engine, 'status', '') == 'protected' or not isinstance(data, dict)
            or not isinstance(data.get('records'), list)):
        return {**base, 'status': 'engine_unavailable'}
    records = data['records']
    if any(not isinstance(row, dict) or not _identity(row.get('id')) for row in records):
        return {**base, 'status': 'engine_unavailable'}
    known = {row['id'] for row in records}
    eligible, stale, stale_candidates = [], 0, set()
    for row in selected:
        stale_candidates.update(set(row['candidates']) - known)
        if not set(row['relevant']) <= known:
            stale += 1
        else:
            eligible.append(row)
    eligible_groups = len({row['group'] for row in eligible})
    base.update(stale_labels=stale, stale_candidate_ids=len(stale_candidates),
                evaluated_rows=len(eligible), evaluated_groups=eligible_groups, k=k)
    if len(eligible) < 5 or eligible_groups < minimum_groups:
        return {**base, 'status': 'insufficient_data',
                'message': '排除已删除记忆的过期标注后，独立标注不足；未生成准确率。'}
    answerable = no_answer_count = 0
    hit1 = reciprocal = ndcg = found = recall_sum = no_answer_correct = 0
    returned_no_answer = actual_no_answer_return = 0
    for row in eligible:
        relevant = set(row['relevant'])
        try:
            hits = engine.recall(row['query'], limit=k)
            if not isinstance(hits, (list, tuple)) or len(hits) > k:
                raise ValueError('invalid recall result')
            returned = [hit.get('id') if isinstance(hit, dict) else None for hit in hits]
            if (not all(_identity(identity) and identity in known for identity in returned)
                    or len(set(returned)) != len(returned)):
                raise ValueError('missing, unknown or duplicate recall ID')
        except Exception:
            # No private engine exception, query, candidate text or ID leaks.
            return {**base, 'status': 'evaluation_error', 'message': '检索失败或缺少有效稳定ID；未生成准确率。'}
        if row['no_answer']:
            no_answer_count += 1
            no_answer_correct += not returned
        else:
            answerable += 1
            ranks = [index + 1 for index, identity in enumerate(returned) if identity in relevant]
            recall_sum += len(ranks) / len(relevant)
            if ranks:
                hit1 += ranks[0] == 1
                found += 1
                reciprocal += 1.0 / ranks[0]
                dcg = sum(1.0 / math.log2(rank + 1) for rank in ranks)
                ideal = sum(1.0 / math.log2(index + 2) for index in range(min(k, len(relevant))))
                ndcg += dcg / ideal if ideal else 0.0
        if not returned:
            returned_no_answer += 1
            actual_no_answer_return += row['no_answer']
    score = round(ndcg / answerable, 4) if answerable else None
    result = {**base, 'status': 'evaluated', 'answerable': answerable, 'no_answer': no_answer_count,
              'hit1': round(hit1 / answerable, 4) if answerable else None,
              'mrr': round(reciprocal / answerable, 4) if answerable else None,
              'ndcg_at_k': score,
              'retrieved_relevant_at_k': round(found / answerable, 4) if answerable else None,
              'recall_at_k': round(recall_sum / answerable, 4) if answerable else None,
              'no_answer_accuracy': round(no_answer_correct / no_answer_count, 4) if no_answer_count else None,
              'abstention_precision': round(actual_no_answer_return / returned_no_answer, 4) if returned_no_answer else None,
              'end_to_end_accuracy': round((hit1 + no_answer_correct) / len(eligible), 4)}
    if k == 5:
        result['ndcg5'] = score
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviews')
    parser.add_argument('--memory')
    parser.add_argument('--partition', choices=SPLITS, default='test')
    args = parser.parse_args()
    store = RetrievalReviewStore(args.reviews)
    if store.status == 'protected':
        print(json.dumps({'status': 'protected', 'training_performed': False}))
        return 1
    from memory_engine import MemoryEngine
    engine = MemoryEngine(args.memory)
    report = evaluate_labels(engine, store.rows, split=args.partition)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
