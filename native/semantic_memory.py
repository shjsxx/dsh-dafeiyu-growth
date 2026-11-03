"""Local semantic-ish recall without models or network.

Feature hashing over Chinese character n-grams, ASCII words and a small curated
synonym/concept table. This is lexical-semantic generalization, not a neural
embedding: it bridges common paraphrases but cannot understand arbitrary text.
Pure, deterministic and bounded; no raw text is stored here.
"""
from __future__ import annotations

import hashlib
import math
import re

DIM = 2048
_WORD = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
_PUNCT = re.compile(r"[\s，。,.!！?？：:；;、\"'“”‘’（）()【】\[\]…—\-]+")

# Canonical concept -> surface variants. Small on purpose: broad enough to
# bridge common Chinese paraphrases, narrow enough to stay predictable.
CONCEPTS = {
    "comfort": ("难过", "伤心", "低落", "不开心", "郁闷", "委屈", "想哭", "哭"),
    "happy": ("开心", "高兴", "快乐", "兴奋", "愉快", "哈哈"),
    "walk": ("散步", "走路", "走走", "遛弯", "逛"),
    "night": ("夜里", "晚上", "夜晚", "深夜", "半夜"),
    "like": ("喜欢", "偏好", "爱吃", "爱喝", "爱好"),
    "work": ("工作", "上班", "加班", "忙", "项目", "代码"),
    "rest": ("休息", "睡觉", "困", "晚安", "歇", "眯"),
    "worry": ("焦虑", "紧张", "担心", "害怕", "压力", "不安"),
    "anger": ("生气", "愤怒", "烦", "讨厌", "火大"),
    "greet": ("你好", "嗨", "在吗", "早上好", "晚安"),
    "music": ("音乐", "歌", "听歌", "歌曲", "旋律"),
    "weather": ("下雨", "雨天", "天气", "风"),
    "sleep_time": ("睡觉", "睡眠", "晚安", "十二点", "几点"),
    "capital": ("首都", "都城", "巴黎"),
    "boil": ("沸点", "沸腾", "100度", "摄氏度"),
}
_SURFACE = {word: concept for concept, words in CONCEPTS.items() for word in words}


def normalize(text):
    return _PUNCT.sub("", str(text or "").lower())[:300]


def _hash_index(token):
    value = hashlib.blake2b(token.encode("utf-8"), digest_size=4).hexdigest()
    return int(value, 16) % DIM


def features(text):
    """Sparse hashed feature vector: {index: weight}."""
    raw = normalize(text)
    if not raw:
        return {}
    vector = {}
    def add(token, weight):
        index = _hash_index(token)
        vector[index] = vector.get(index, 0.0) + weight
    words = _WORD.findall(raw)
    for word in words:
        add("w:" + word, 2.2 if len(word) > 1 else 1.0)
        for size in (2, 3):
            for start in range(max(0, len(word) - size + 1)):
                add(f"c{size}:" + word[start:start + size], 1.0)
    for surface, concept in _SURFACE.items():
        if surface in raw:
            add("k:" + concept, 1.4)
            add("s:" + surface, .7)
    return vector


def cosine(left, right):
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(index, 0.0) for index, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def similarity(query, text):
    return round(cosine(features(query), features(text)), 4)


def concept_set(text):
    raw = normalize(text)
    return {concept for surface, concept in _SURFACE.items() if surface in raw}


def concept_overlap(query, text):
    left, right = concept_set(query), concept_set(text)
    if not left:
        return 0.0
    return round(len(left & right) / len(left), 4)


def rank(query, texts):
    """Return (index, score) sorted descending; empty for unusable input."""
    query_vector = features(query)
    if not query_vector:
        return []
    scored = []
    for index, text in enumerate(texts):
        score = cosine(query_vector, features(text))
        if score >= .04:
            scored.append((index, round(score, 4)))
    scored.sort(key=lambda row: (-row[1], row[0]))
    return scored
