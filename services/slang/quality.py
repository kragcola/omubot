"""Shared quality guards for slang extraction and review."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from services.similarity import NgramSimilarityProvider, normalize_text_key

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
_ASCIIISH_KEY_RE = re.compile(r"^[a-z0-9-]+$")
_SIMILARITY = NgramSimilarityProvider()


@dataclass(slots=True)
class SlangQualityAssessment:
    accepted: bool
    cleaned_aliases: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def normalize_slang_key(value: str) -> str:
    return normalize_text_key(value)


def _fold_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().lower()


def _looks_asciiish(value: str) -> bool:
    return bool(_ASCIIISH_KEY_RE.fullmatch(value))


def speaker_to_user_id(speaker: str | None) -> str:
    if not speaker:
        return ""
    match = re.search(r"\((\d{4,})\)\s*$", str(speaker))
    return match.group(1) if match else ""


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
    return bool(is_generic_meaning(raw_meaning))


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


def matches_slang_candidate(candidate: str, text: str) -> bool:
    raw_candidate = _fold_text(candidate)
    raw_text = _fold_text(text)
    candidate_key = normalize_slang_key(candidate)
    text_key = normalize_slang_key(text)
    if not candidate_key or not text_key:
        return False
    if candidate_key == text_key:
        return True
    if len(candidate_key) > 3:
        return candidate_key in text_key
    if candidate_key not in text_key:
        return False
    if not raw_candidate:
        return False
    if _looks_asciiish(candidate_key):
        pattern = rf"(?<![0-9a-z]){re.escape(raw_candidate)}(?![0-9a-z])"
        return bool(re.search(pattern, raw_text))
    return raw_candidate in raw_text


def estimate_slang_occurrences(term: str, aliases: list[str], rows: list[dict[str, Any]]) -> int:
    candidates = [term, *aliases]
    count = 0
    for row in rows:
        text = str(row.get("content_text") or "")
        if any(matches_slang_candidate(candidate, text) for candidate in candidates):
            count += 1
    return max(1, count)


def select_slang_source_row(evidence: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    evidence_text = str(evidence or "").strip()
    if not evidence_text:
        return rows[-1]
    for row in reversed(rows):
        if str(row.get("content_text") or "").strip() == evidence_text:
            return row
    evidence_key = normalize_slang_key(evidence_text)
    if not evidence_key:
        return rows[-1]

    evidence_folded = _fold_text(evidence_text)
    best_row: dict[str, Any] | None = None
    best_score: tuple[int, float, int, int] | None = None
    for index, row in enumerate(rows):
        text = str(row.get("content_text") or "").strip()
        if not text:
            continue
        text_key = normalize_slang_key(text)
        if not text_key:
            continue
        text_folded = _fold_text(text)
        tier = 0
        similarity = 0.0
        if evidence_key == text_key:
            tier = 4
            similarity = 1.0
        elif len(evidence_key) >= 4 and len(text_key) >= 4 and (
            evidence_key in text_key or text_key in evidence_key
        ):
            tier = 3
            similarity = _SIMILARITY.similarity(evidence_folded, text_folded)
        else:
            similarity = _SIMILARITY.similarity(evidence_folded, text_folded)
            if len(evidence_key) >= 4 and len(text_key) >= 4 and similarity >= 0.72:
                tier = 2
        if tier <= 0:
            continue
        score = (tier, round(similarity, 3), min(len(evidence_key), len(text_key)), index)
        if best_score is None or score > best_score:
            best_score = score
            best_row = row
    return best_row or rows[-1]


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
