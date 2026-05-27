"""Near-duplicate guardrail for consecutive assistant replies."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from services.llm.sentinel_registry import (
    GuardrailContext,
    GuardrailHit,
    GuardrailResult,
    register_rule,
)

_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).lower().strip()
    value = _PUNCT_RE.sub("", value)
    return value


def _ngrams(text: str, size: int) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index:index + size] for index in range(len(text) - size + 1)}


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    is_duplicate: bool
    overlap: float


def is_near_duplicate(
    reply: str,
    last_assistant: str,
    *,
    ngram: int = 5,
    threshold: float = 0.4,
) -> DuplicateDecision:
    current = normalize_text(reply)
    previous = normalize_text(last_assistant)
    if not current or not previous:
        return DuplicateDecision(False, 0.0)
    shorter, longer = sorted((current, previous), key=len)
    if shorter and shorter in longer and len(shorter) / max(1, len(longer)) >= 0.6:
        return DuplicateDecision(True, 1.0)
    current_grams = _ngrams(current, max(1, ngram))
    previous_grams = _ngrams(previous, max(1, ngram))
    if not current_grams or not previous_grams:
        return DuplicateDecision(False, 0.0)
    union = current_grams | previous_grams
    overlap = len(current_grams & previous_grams) / max(1, len(union))
    return DuplicateDecision(overlap >= threshold, overlap)


def _dedup_action(config: object | None) -> str:
    guardrail = getattr(config, "sentinel_guardrail", config)
    return str(getattr(guardrail, "dedup_action", "rewrite") or "rewrite")


def _dedup_threshold(config: object | None) -> float:
    guardrail = getattr(config, "sentinel_guardrail", config)
    try:
        return float(getattr(guardrail, "dedup_threshold", 0.4))
    except (TypeError, ValueError):
        return 0.4


def _dedup_ngram(config: object | None) -> int:
    guardrail = getattr(config, "sentinel_guardrail", config)
    try:
        return max(1, int(getattr(guardrail, "dedup_ngram", 5)))
    except (TypeError, ValueError):
        return 5


def dedup_rule(text: str, ctx: GuardrailContext) -> GuardrailResult:
    decision = is_near_duplicate(
        text,
        ctx.last_assistant_text,
        ngram=_dedup_ngram(ctx.config),
        threshold=_dedup_threshold(ctx.config),
    )
    if not decision.is_duplicate:
        return GuardrailResult(passed=True, text=text)
    action = _dedup_action(ctx.config)
    hit = GuardrailHit(
        name="near_duplicate",
        severity="medium",
        action="block" if action == "block" else "rewrite",
        overlap=decision.overlap,
        metadata={"last_assistant_text": ctx.last_assistant_text[:120]},
    )
    if action == "warn":
        return GuardrailResult(passed=True, text=text, hits=(hit,))
    if action == "block":
        return GuardrailResult(passed=False, text="", hits=(hit,), blocked=True)
    return GuardrailResult(
        passed=False,
        text="",
        hits=(hit,),
        metadata={"near_duplicate_decision": action},
    )


register_rule(dedup_rule)
