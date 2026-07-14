"""Visible-reply guardrails for sentinel leaks and A-cluster post-processing."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal

GuardrailAction = Literal["warn", "strip", "redact", "block", "rewrite"]
GuardrailSeverity = Literal["low", "medium", "high"]


@dataclasses.dataclass(frozen=True, slots=True)
class SentinelEntry:
    name: str
    pattern: str | re.Pattern[str]
    severity: GuardrailSeverity = "medium"
    action: GuardrailAction = "strip"
    replacement: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class GuardrailHit:
    name: str
    severity: GuardrailSeverity
    action: GuardrailAction
    match_text: str = ""
    overlap: float = 0.0
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class GuardrailResult:
    passed: bool
    text: str
    hits: tuple[GuardrailHit, ...] = ()
    blocked: bool = False
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class GuardrailContext:
    thinker_thought: str = ""
    last_assistant_text: str = ""
    user_message: str = ""
    session_count: int = 0
    bot_name: str = ""
    config: Any = None


RuleHandler = Callable[[str, GuardrailContext], GuardrailResult]

# Explicit execution order for A-cluster rules. Rules run in ascending order so
# the pipeline is deterministic regardless of module import timing — each rule
# module self-registers on import, and circular imports made the arrival order
# (and thus the old append-only order) non-deterministic. In particular
# persona_drift MUST run before schedule_overshare: a sentence like
# "我是{name}，下午3:00…" carries both a declaration and a time leak, and if
# schedule_overshare strips the whole sentence first, persona_drift is starved.
RULE_ORDER_SENTINEL = 0
RULE_ORDER_DEDUP = 10
RULE_ORDER_PERSONA_DRIFT = 20
RULE_ORDER_SCHEDULE_OVERSHARE = 30
RULE_ORDER_THINKER_PHRASE = 40
RULE_ORDER_DEFAULT = 100


_DEFAULT_SENTINELS: tuple[SentinelEntry, ...] = (
    SentinelEntry("sentinel_image", re.compile(r"«图片(?::[^»]*)?[^»]*»"), action="strip"),
    SentinelEntry("sentinel_face", re.compile(r"«表情»"), action="strip"),
    SentinelEntry("sentinel_audio", re.compile(r"«音频[^»]*»"), action="strip"),
    SentinelEntry("sentinel_reply", re.compile(r"«回复[^»]*»"), action="strip"),
    SentinelEntry("sentinel_sticker", re.compile(r"«表情包:[^»]+»"), action="strip"),
    SentinelEntry("sentinel_img_tag", re.compile(r"«img:\d+»"), action="strip"),
    SentinelEntry("sparkle_symbol_watcher", re.compile(r"[✨☆★✧⭐]"), severity="low", action="warn"),
)


def _compile_pattern(pattern: str | re.Pattern[str]) -> re.Pattern[str]:
    if isinstance(pattern, re.Pattern):
        return pattern
    return re.compile(pattern)


def sentinel_guardrail_enabled(config: object | None) -> bool:
    if config is None:
        return True
    guardrail = getattr(config, "sentinel_guardrail", config)
    return bool(getattr(guardrail, "enabled", False))


def _dedupe_hits(existing: list[GuardrailHit], new_hits: Sequence[GuardrailHit]) -> None:
    seen = {
        (hit.name, hit.action, hit.match_text, round(hit.overlap, 4))
        for hit in existing
    }
    for hit in new_hits:
        key = (hit.name, hit.action, hit.match_text, round(hit.overlap, 4))
        if key in seen:
            continue
        existing.append(hit)
        seen.add(key)


class SentinelRegistry:
    def __init__(self) -> None:
        # Each entry is (order, sequence, handler). ``order`` gives an explicit,
        # import-timing-independent execution order; ``sequence`` preserves
        # registration order as a stable tiebreaker within the same ``order``.
        self._rules: list[tuple[int, int, RuleHandler]] = []
        self._seq = 0
        for entry in _DEFAULT_SENTINELS:
            self.register(entry)

    def _add_rule(self, handler: RuleHandler, order: int) -> None:
        self._rules.append((order, self._seq, handler))
        self._seq += 1

    def _ordered_rules(self) -> list[RuleHandler]:
        return [handler for _order, _seq, handler in sorted(self._rules, key=lambda item: (item[0], item[1]))]

    def register(self, entry: SentinelEntry) -> None:
        pattern = _compile_pattern(entry.pattern)

        def _rule(text: str, ctx: GuardrailContext) -> GuardrailResult:
            if not sentinel_guardrail_enabled(ctx.config):
                return GuardrailResult(passed=True, text=text)
            matches = list(pattern.finditer(text))
            if not matches:
                return GuardrailResult(passed=True, text=text)
            hits = tuple(
                GuardrailHit(
                    name=entry.name,
                    severity=entry.severity,
                    action=entry.action,
                    match_text=match.group(0),
                )
                for match in matches
            )
            if entry.action == "warn":
                return GuardrailResult(passed=True, text=text, hits=hits)
            if entry.action == "redact":
                cleaned = pattern.sub(entry.replacement or "[redacted]", text)
                return GuardrailResult(passed=True, text=cleaned, hits=hits)
            if entry.action == "block":
                return GuardrailResult(passed=False, text="", hits=hits, blocked=True)
            cleaned = pattern.sub(entry.replacement, text)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
            return GuardrailResult(passed=True, text=cleaned, hits=hits)

        self._add_rule(_rule, RULE_ORDER_SENTINEL)

    def register_rule(self, handler: RuleHandler, *, order: int = RULE_ORDER_DEFAULT) -> None:
        self._add_rule(handler, order)

    def apply(
        self,
        text: str,
        *,
        thinker_thought: str = "",
        last_assistant_text: str = "",
        user_message: str = "",
        session_count: int = 0,
        bot_name: str = "",
        config: Any = None,
    ) -> GuardrailResult:
        current = text
        hits: list[GuardrailHit] = []
        metadata: dict[str, Any] = {}
        failed_closed = False
        context = GuardrailContext(
            thinker_thought=thinker_thought,
            last_assistant_text=last_assistant_text,
            user_message=user_message,
            session_count=session_count,
            bot_name=bot_name,
            config=config,
        )
        for rule in self._ordered_rules():
            result = rule(current, context)
            if result.text or result.passed:
                current = result.text
            _dedupe_hits(hits, result.hits)
            if result.metadata:
                metadata.update(result.metadata)
            if result.blocked:
                return GuardrailResult(
                    passed=False,
                    text=current,
                    hits=tuple(hits),
                    blocked=True,
                    metadata=metadata,
                )
            if not result.passed:
                failed_closed = True
        if failed_closed:
            return GuardrailResult(
                passed=False,
                text=current,
                hits=tuple(hits),
                blocked=False,
                metadata=metadata,
            )
        return GuardrailResult(
            passed=True,
            text=current,
            hits=tuple(hits),
            blocked=False,
            metadata=metadata,
        )


_REGISTRY = SentinelRegistry()


def register(entry: SentinelEntry) -> None:
    _REGISTRY.register(entry)


def register_rule(handler: RuleHandler, *, order: int = RULE_ORDER_DEFAULT) -> None:
    _REGISTRY.register_rule(handler, order=order)


def apply_guardrails(
    text: str,
    *,
    thinker_thought: str = "",
    last_assistant_text: str = "",
    user_message: str = "",
    session_count: int = 0,
    bot_name: str = "",
    config: Any = None,
) -> GuardrailResult:
    return _REGISTRY.apply(
        text,
        thinker_thought=thinker_thought,
        last_assistant_text=last_assistant_text,
        user_message=user_message,
        session_count=session_count,
        bot_name=bot_name,
        config=config,
    )


# Register non-sentinel A-cluster rules on import. Execution order is governed by
# the explicit ``order`` each rule passes to ``register_rule`` (see RULE_ORDER_*),
# NOT by the order of these imports — so import timing / circular imports can no
# longer reshuffle the pipeline.
from services.llm import dedup_gate as _dedup_gate  # noqa: E402,F401
from services.llm import persona_drift_stripper as _persona_drift_stripper  # noqa: E402,F401
from services.llm import schedule_overshare_detector as _schedule_overshare_detector  # noqa: E402,F401
from services.llm import thinker_phrase_detector as _thinker_phrase_detector  # noqa: E402,F401
