"""Shared quality guards for slang extraction and review."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from services.similarity import normalize_text_key

_GENERIC_ENTITY_LIKE = {
    "老师", "同学", "朋友", "兄弟", "姐妹", "哥哥", "姐姐", "妹妹", "弟弟",
    "老婆", "老公", "群主", "管理", "管理员", "今天", "明天", "昨天",
    "你好", "早安", "晚安", "谢谢", "收到", "可以", "好的", "哈哈", "呵呵",
}
_GENERIC_MEANING_EXACT = {
    "梗", "一个梗", "一种梗", "网络梗", "群内梗", "黑话", "群内黑话",
    "一个说法", "一种说法", "一种状态", "一个状态", "一个称呼", "一种称呼",
    "一个代称", "一种代称", "一个简称", "一种简称", "一个缩写", "一种缩写",
}
_GENERIC_MEANING_PATTERNS = (
    re.compile(r"^(一个|一种|某个|某种)?(梗|黑话|说法|状态|称呼|代称|简称|缩写|叫法)$"),
    re.compile(r"^(表示|形容|指代)(一种|一个|某种|某个)?(梗|黑话|说法|状态|称呼)$"),
)


@dataclass(slots=True)
class SlangQualityAssessment:
    accepted: bool
    cleaned_aliases: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def normalize_slang_key(value: str) -> str:
    return normalize_text_key(value)


def is_noise_term(term: str) -> bool:
    raw = str(term or "").strip()
    key = normalize_slang_key(raw)
    if len(key) < 2 or len(raw) > 32:
        return True
    if re.fullmatch(r"[\d.]+", raw):
        return True
    if key in {normalize_slang_key(item) for item in _GENERIC_ENTITY_LIKE}:
        return True
    return bool(re.fullmatch(r"[\W_]+", raw, flags=re.UNICODE))


def is_generic_meaning(meaning: str) -> bool:
    raw = str(meaning or "").strip()
    if not raw:
        return True
    key = normalize_slang_key(raw)
    if not key:
        return True
    if key in {normalize_slang_key(item) for item in _GENERIC_MEANING_EXACT}:
        return True
    return any(pattern.fullmatch(raw) for pattern in _GENERIC_MEANING_PATTERNS)


def is_low_signal_meaning(term: str, meaning: str) -> bool:
    raw_meaning = str(meaning or "").strip()
    if not raw_meaning:
        return True
    term_key = normalize_slang_key(term)
    meaning_key = normalize_slang_key(raw_meaning)
    if not meaning_key:
        return True
    if meaning_key == term_key:
        return True
    if term_key and term_key in meaning_key and len(meaning_key) <= len(term_key) + 2:
        return True
    if is_generic_meaning(raw_meaning):
        return True
    return False


def clean_aliases(term: str, aliases: list[str]) -> list[str]:
    term_key = normalize_slang_key(term)
    seen: set[str] = {term_key} if term_key else set()
    cleaned: list[str] = []
    for alias in aliases:
        raw = str(alias or "").strip()
        key = normalize_slang_key(raw)
        if not raw or not key or key in seen or is_noise_term(raw):
            continue
        seen.add(key)
        cleaned.append(raw)
    return cleaned


def assess_candidate_quality(term: str, meaning: str, aliases: list[str] | None = None) -> SlangQualityAssessment:
    cleaned_aliases = clean_aliases(term, aliases or [])
    reasons: list[str] = []
    if is_noise_term(term):
        reasons.append("noise_term")
    if is_low_signal_meaning(term, meaning):
        reasons.append("low_signal_meaning")
    return SlangQualityAssessment(
        accepted=not reasons,
        cleaned_aliases=cleaned_aliases,
        reasons=reasons,
    )
