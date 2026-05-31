"""Rule-only low-signal text short-circuit before thinker/main LLM calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_PUNCT_ONLY_RE = re.compile(r"^[\s`*_~#>\[\](){}《》<>:：,，。.!！?？;；\"'“”‘’|/\\~～…]+$")
_SINGLE_EMOJI_RE = re.compile(
    r"^(?:"
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"
    r"|[\(（][^A-Za-z0-9一-龥]{1,12}[\)）]"
    r")$"
)
_SINGLE_CHAR_RE = re.compile(r"^[A-Za-z0-9一-龥]$")


@dataclass(frozen=True, slots=True)
class PreflightResult:
    should_skip: bool
    reason: str = ""
    density: float = 1.0


def _normalized_text(text: str) -> str:
    return str(text or "").strip()


def _is_repetition_only(text: str, *, min_count: int) -> bool:
    compact = _normalized_text(text)
    if len(compact) < max(2, int(min_count)):
        return False
    first = compact[0]
    return all(ch == first for ch in compact)


def preflight(
    text: str,
    *,
    is_reply_to_bot: bool = False,
    is_at_bot: bool = False,
    config: Any | None = None,
) -> PreflightResult:
    content = _normalized_text(text)
    if not content:
        return PreflightResult(True, "punctuation_only", 0.0)

    if bool(getattr(config, "bypass_on_reply_to_bot", True)) and is_reply_to_bot:
        return PreflightResult(False, "", 1.0)
    if bool(getattr(config, "bypass_on_at_bot", True)) and is_at_bot:
        return PreflightResult(False, "", 1.0)

    if bool(getattr(config, "skip_punctuation_only", True)) and _PUNCT_ONLY_RE.fullmatch(content):
        return PreflightResult(True, "punctuation_only", 0.0)
    if bool(getattr(config, "skip_single_emoji", True)) and _SINGLE_EMOJI_RE.fullmatch(content):
        return PreflightResult(True, "single_emoji", 0.1)
    if bool(getattr(config, "skip_single_char", True)) and _SINGLE_CHAR_RE.fullmatch(content):
        return PreflightResult(True, "single_char", 0.15)
    if bool(getattr(config, "skip_repetition", True)) and _is_repetition_only(
        content,
        min_count=int(getattr(config, "min_repetition_count", 3) or 3),
    ):
        return PreflightResult(True, "repetition", 0.12)
    return PreflightResult(False, "", 1.0)

