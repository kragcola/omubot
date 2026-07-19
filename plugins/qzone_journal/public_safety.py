"""Shared public-text scrubbing for QZone prompt, draft, and review fields."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Digit boundaries only: scrub identifiers even beside letters/underscores.
QQ_LIKE_RE = re.compile(r"(?<!\d)\d{5,16}(?!\d)")

# Fullwidth digits (NFKC-equivalent) so QQ-like runs hidden as ３８４… are caught.
_FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")

# Assignment ops: ASCII and fullwidth (NFKC-equivalent) forms after Cf strip.
# Whole secret value is redacted, including whitespace-separated Bearer tokens.
# Value ends at whitespace (except Bearer form) or Chinese/ASCII delimiters.
SECRET_OR_ID_ASSIGN_RE = re.compile(
    r"(?i)\b(?:"
    r"user_id|group_id|uin|"
    r"p_skey|cookie|cookies|authorization|"
    r"token|tokens|credential|credentials|"
    r"password|secret"
    r")\s*[:=：＝]\s*(?:"
    r"Bearer\s+[^\s,，;；|。！？!?]+"
    r"|"
    r"[^\s,，;；|。！？!?]+"
    r")"
)


def _normalize_public_unicode(text: str) -> str:
    """Strip Unicode format chars, then map fullwidth digits (NFKC-compatible).

    Full-string NFKC is avoided so Chinese punctuation (e.g. 线索：、故事里，)
    stays intact; assignment detection accepts fullwidth ops and delimiters.
    """
    chars: list[str] = []
    for ch in str(text or ""):
        if unicodedata.category(ch) == "Cf":
            continue
        codepoint = ord(ch)
        if ch not in {"：", "＝", "，"} and 0xFF01 <= codepoint <= 0xFF5E:
            ch = chr(codepoint - 0xFEE0)
        chars.append(ch)
    stripped = "".join(chars)
    return stripped.translate(_FULLWIDTH_DIGIT_TRANS)


def contains_secret_or_id_assignment(value: Any) -> bool:
    text = _normalize_public_unicode(str(value or ""))
    return SECRET_OR_ID_ASSIGN_RE.search(text) is not None


def scrub_qq_like_digits(value: Any) -> str:
    text = _normalize_public_unicode(str(value or ""))
    return QQ_LIKE_RE.sub("[redacted]", text)


def scrub_public_text(value: Any, *, preserve_newlines: bool = False) -> str:
    """Normalize unicode/whitespace and remove identifiers/secrets from public material.

    When ``preserve_newlines`` is True, horizontal whitespace is collapsed within
    each line but newline structure is retained (advanced worldbook / composer).
    """
    text = _normalize_public_unicode(str(value or ""))
    if not text.strip():
        return ""
    text = SECRET_OR_ID_ASSIGN_RE.sub("[redacted]", text)
    text = QQ_LIKE_RE.sub("[redacted]", text)
    if preserve_newlines:
        lines = [
            re.sub(r"[^\S\n]+", " ", line).strip()
            for line in text.split("\n")
        ]
        return "\n".join(lines).strip()
    return re.sub(r"\s+", " ", text).strip()


__all__ = [
    "contains_secret_or_id_assignment",
    "scrub_public_text",
    "scrub_qq_like_digits",
]
