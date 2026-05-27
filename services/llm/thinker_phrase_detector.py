"""Detect visible replies that parrot thinker free-text thought."""

from __future__ import annotations

from dataclasses import dataclass

from services.llm.dedup_gate import normalize_text
from services.llm.sentinel_registry import (
    GuardrailContext,
    GuardrailHit,
    GuardrailResult,
    register_rule,
)


def _ngrams(text: str, size: int) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index:index + size] for index in range(len(text) - size + 1)}


@dataclass(frozen=True, slots=True)
class DetectResult:
    hit: bool
    overlap: float
    matched_ngrams: tuple[str, ...] = ()


def detect(
    reply: str,
    thinker_thought: str,
    *,
    ngram: int = 4,
    threshold: float = 0.4,
) -> DetectResult:
    visible = normalize_text(reply)
    thought = normalize_text(thinker_thought)
    if not visible or not thought:
        return DetectResult(False, 0.0, ())
    visible_grams = _ngrams(visible, max(1, ngram))
    thought_grams = _ngrams(thought, max(1, ngram))
    if not visible_grams or not thought_grams:
        return DetectResult(False, 0.0, ())
    matched = tuple(sorted(visible_grams & thought_grams))
    overlap = len(matched) / max(1, len(thought_grams))
    return DetectResult(overlap >= threshold, overlap, matched)


def _phrase_action(config: object | None) -> str:
    guardrail = getattr(config, "sentinel_guardrail", config)
    return str(getattr(guardrail, "thinker_phrase_action", "rewrite") or "rewrite")


def _phrase_threshold(config: object | None) -> float:
    guardrail = getattr(config, "sentinel_guardrail", config)
    try:
        return float(getattr(guardrail, "thinker_phrase_threshold", 0.4))
    except (TypeError, ValueError):
        return 0.4


def _phrase_ngram(config: object | None) -> int:
    guardrail = getattr(config, "sentinel_guardrail", config)
    try:
        return max(1, int(getattr(guardrail, "thinker_phrase_ngram", 4)))
    except (TypeError, ValueError):
        return 4


def thinker_phrase_rule(text: str, ctx: GuardrailContext) -> GuardrailResult:
    result = detect(
        text,
        ctx.thinker_thought,
        ngram=_phrase_ngram(ctx.config),
        threshold=_phrase_threshold(ctx.config),
    )
    if not result.hit:
        return GuardrailResult(passed=True, text=text)
    action = _phrase_action(ctx.config)
    hit = GuardrailHit(
        name="thinker_phrase",
        severity="medium",
        action="block" if action == "block" else "rewrite",
        overlap=result.overlap,
        metadata={"matched_ngrams": list(result.matched_ngrams[:8])},
    )
    if action == "warn":
        return GuardrailResult(passed=True, text=text, hits=(hit,))
    if action == "block":
        return GuardrailResult(passed=False, text="", hits=(hit,), blocked=True)
    return GuardrailResult(
        passed=False,
        text="",
        hits=(hit,),
        metadata={"thinker_phrase_decision": action},
    )


register_rule(thinker_phrase_rule)
