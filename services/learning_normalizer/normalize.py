"""Deterministic normalization and fuzzy matching helpers.

This module is intentionally dependency-light except for rapidfuzz. It does
not know about slang/style storage and never mutates original chat evidence.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from rapidfuzz import fuzz

NormalizationProfile = Literal["general", "slang", "style", "catchphrase"]

_URL_RE = re.compile(r"https?://\S+")
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
_LEADING_MARKUP_RE = re.compile(r"^[#>\-\s*`_~]+")
_GENERAL_PUNCT_RE = re.compile(r"[\s`*_~#>\[\](){}《》<>:：,，。.!！?？;；\"'“”‘’|/\\]+")
_STYLE_PUNCT_RE = re.compile(r"[，。！？、,.!?;；:：\"'`~\-—_()[\]{}<>《》「」『』【】|/\\]+")


@dataclass(slots=True)
class NormalizationScore:
    """Similarity score with the feature that made the decision legible."""

    score: float
    method: str
    left_key: str
    right_key: str
    left_fingerprint: str
    right_fingerprint: str

    @property
    def exact(self) -> bool:
        return bool(self.left_key and self.left_key == self.right_key)

    @property
    def fingerprint_match(self) -> bool:
        return bool(self.left_fingerprint and self.left_fingerprint == self.right_fingerprint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "method": self.method,
            "left_key": self.left_key,
            "right_key": self.right_key,
            "left_fingerprint": self.left_fingerprint,
            "right_fingerprint": self.right_fingerprint,
        }


def normalize_key(value: str, profile: NormalizationProfile = "general") -> str:
    """Return a stable key for learning-time dedupe and fuzzy matching."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if profile in {"general", "slang", "catchphrase"}:
        text = _URL_RE.sub("", text)
        text = _MD_LINK_RE.sub("", text)
        text = _LEADING_MARKUP_RE.sub("", text)
        text = _GENERAL_PUNCT_RE.sub("", text)
    else:
        text = re.sub(r"\s+", "", text)
        text = _STYLE_PUNCT_RE.sub("", text)
    return _fold_repeated_chars(text)


def fingerprint_key(value: str, profile: NormalizationProfile = "general") -> str:
    """Return an OpenRefine-inspired n-gram fingerprint key."""
    key = normalize_key(value, profile)
    if len(key) <= 2:
        return key
    n = 2 if len(key) < 8 else 3
    grams = {key[index : index + n] for index in range(len(key) - n + 1)}
    return "".join(sorted(grams))


def extract_features(value: str, profile: NormalizationProfile = "general") -> dict[str, Any]:
    """Describe normalization changes for audit UI and prompt-side meta."""
    raw = str(value or "")
    nfkc = unicodedata.normalize("NFKC", raw)
    normalized = normalize_key(raw, profile)
    folded = _fold_repeated_chars(_strip_for_profile(nfkc, profile))
    return {
        "profile": profile,
        "raw_len": len(raw),
        "normalized_key": normalized,
        "fingerprint_key": fingerprint_key(raw, profile),
        "nfkc_changed": raw != nfkc,
        "punct_or_space_removed": bool(_strip_for_profile(nfkc, profile) != nfkc.strip().casefold()),
        "repeated_chars_folded": folded != _strip_for_profile(nfkc, profile),
        "ascii": normalized.isascii(),
        "key_len": len(normalized),
    }


def score_similarity(left: str, right: str, profile: NormalizationProfile = "general") -> NormalizationScore:
    """Score two values using exact/fingerprint/rapidfuzz in that order."""
    left_key = normalize_key(left, profile)
    right_key = normalize_key(right, profile)
    left_fp = fingerprint_key(left, profile)
    right_fp = fingerprint_key(right, profile)
    if not left_key or not right_key:
        return NormalizationScore(0.0, "empty", left_key, right_key, left_fp, right_fp)
    if left_key == right_key:
        return NormalizationScore(1.0, "exact", left_key, right_key, left_fp, right_fp)
    if left_fp and left_fp == right_fp:
        return NormalizationScore(0.96, "fingerprint", left_key, right_key, left_fp, right_fp)
    ratio = float(fuzz.ratio(left_key, right_key)) / 100.0
    partial = float(fuzz.partial_ratio(left_key, right_key)) / 100.0
    token = float(fuzz.token_sort_ratio(left_key, right_key)) / 100.0
    best = max(ratio, partial * 0.96, token * 0.94)
    method = "rapidfuzz_ratio"
    if best == partial * 0.96:
        method = "rapidfuzz_partial"
    elif best == token * 0.94:
        method = "rapidfuzz_token_sort"
    return NormalizationScore(best, method, left_key, right_key, left_fp, right_fp)


def _strip_for_profile(value: str, profile: NormalizationProfile) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if profile in {"general", "slang", "catchphrase"}:
        text = _URL_RE.sub("", text)
        text = _MD_LINK_RE.sub("", text)
        text = _LEADING_MARKUP_RE.sub("", text)
        return _GENERAL_PUNCT_RE.sub("", text)
    text = re.sub(r"\s+", "", text)
    return _STYLE_PUNCT_RE.sub("", text)


def _fold_repeated_chars(value: str, max_run: int = 2) -> str:
    if not value:
        return ""
    chars: list[str] = []
    previous = ""
    run = 0
    for char in value:
        if char == previous:
            run += 1
        else:
            previous = char
            run = 1
        if run <= max_run:
            chars.append(char)
    return "".join(chars)
