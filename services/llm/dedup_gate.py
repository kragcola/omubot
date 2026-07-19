"""Near-duplicate guardrail for consecutive assistant replies.

Also enforces bounded multi-turn phrase-family repetition for role-specific
stock phrases (e.g. caught-out / 被发现 family). Quoted or repeated user text
is excluded from bot self-repetition evidence via assistant-only history.

Import order note: ``normalize_text`` must stay importable without finishing
sentinel_registry load (thinker_phrase_detector imports it).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.llm.sentinel_registry import GuardrailContext, GuardrailResult

_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)

# Role-specific stock phrase families (surface forms → family id).
_CAUGHT_OUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"被你(看穿|发现|抓到|逮到|识破)"),
    re.compile(r"被发现了"),
    re.compile(r"哎呀被发现"),
    re.compile(r"我认栽"),
)

_PHRASE_FAMILIES: dict[str, tuple[re.Pattern[str], ...]] = {
    "caught_out": _CAUGHT_OUT_PATTERNS,
}


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


@dataclass(frozen=True, slots=True)
class PhraseFamilyHistory:
    """Assistant-only phrase-family evidence window (bounded multi-turn)."""

    assistant_texts: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_turns(
        cls,
        turns: list[dict[str, str]] | tuple[dict[str, str], ...],
        *,
        max_turns: int = 12,
    ) -> PhraseFamilyHistory:
        assistant: list[str] = []
        for turn in turns:
            role = str(turn.get("role") or "").strip().lower()
            text = str(turn.get("text") or turn.get("content") or "").strip()
            if role == "assistant" and text:
                assistant.append(text)
        if max_turns > 0:
            assistant = assistant[-max_turns:]
        return cls(assistant_texts=tuple(assistant))


def _family_match(text: str, family: str) -> bool:
    patterns = _PHRASE_FAMILIES.get(family)
    if not patterns:
        return False
    value = str(text or "")
    return any(p.search(value) for p in patterns)


def is_phrase_family_repeat(
    current: str,
    history: list[str] | tuple[str, ...] | PhraseFamilyHistory,
    *,
    family: str = "caught_out",
    min_prior: int = 2,
) -> bool:
    """True when current hits a stock family and history has enough prior hits.

    ``history`` must be assistant-authored turns only. User quotes of the same
    surface form are not passed in and therefore cannot count as self-repetition.
    """
    prior_texts = (
        history.assistant_texts
        if isinstance(history, PhraseFamilyHistory)
        else tuple(history)
    )
    if not _family_match(current, family):
        return False
    prior_hits = 0
    for item in prior_texts:
        if not _family_match(item, family):
            continue
        stripped = item.strip()
        if stripped.startswith(("「", "『", "“", '"', "'")) and stripped.endswith(
            ("」", "』", "”", '"', "'")
        ):
            continue
        prior_hits += 1
    return prior_hits >= max(1, min_prior)


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


def dedup_rule(
    text: str,
    ctx: GuardrailContext,
    *,
    assistant_history: list[str] | tuple[str, ...] | None = None,
) -> GuardrailResult:
    from services.llm.sentinel_registry import (
        GuardrailHit,
        GuardrailResult,
        sentinel_guardrail_enabled,
    )

    if not sentinel_guardrail_enabled(ctx.config):
        return GuardrailResult(passed=True, text=text)

    hits: list[Any] = []
    history = list(assistant_history or ())
    if not history:
        ctx_hist = getattr(ctx, "assistant_history", None) or ()
        history = [str(item) for item in ctx_hist if str(item or "").strip()]
    if not history:
        last = str(getattr(ctx, "last_assistant_text", "") or "")
        if last:
            history = [last]
    if is_phrase_family_repeat(text, history, family="caught_out"):
        hits.append(
            GuardrailHit(
                name="phrase_family_repeat",
                severity="medium",
                action="rewrite",
                metadata={"family": "caught_out"},
            )
        )

    decision = is_near_duplicate(
        text,
        ctx.last_assistant_text,
        ngram=_dedup_ngram(ctx.config),
        threshold=_dedup_threshold(ctx.config),
    )
    if decision.is_duplicate:
        action = _dedup_action(ctx.config)
        hits.append(
            GuardrailHit(
                name="near_duplicate",
                severity="medium",
                action="block" if action == "block" else "rewrite",
                overlap=decision.overlap,
                metadata={"last_assistant_text": ctx.last_assistant_text[:120]},
            )
        )
        if action == "warn":
            return GuardrailResult(passed=True, text=text, hits=tuple(hits))
        if action == "block":
            return GuardrailResult(passed=False, text="", hits=tuple(hits), blocked=True)
        return GuardrailResult(
            passed=False,
            text="",
            hits=tuple(hits),
            metadata={"near_duplicate_decision": action},
        )

    if hits:
        return GuardrailResult(
            passed=False,
            text="",
            hits=tuple(hits),
            metadata={"phrase_family_decision": "rewrite"},
        )
    return GuardrailResult(passed=True, text=text)


def _register() -> None:
    from services.llm.sentinel_registry import RULE_ORDER_DEDUP, register_rule

    register_rule(dedup_rule, order=RULE_ORDER_DEDUP)


_register()
