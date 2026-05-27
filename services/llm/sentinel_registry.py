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
    config: Any = None


RuleHandler = Callable[[str, GuardrailContext], GuardrailResult]


_DEFAULT_SENTINELS: tuple[SentinelEntry, ...] = (
    SentinelEntry("sentinel_image", re.compile(r"«图片(?::[^»]*)?[^»]*»"), action="strip"),
    SentinelEntry("sentinel_face", re.compile(r"«表情»"), action="strip"),
    SentinelEntry("sentinel_audio", re.compile(r"«音频[^»]*»"), action="strip"),
    SentinelEntry("sentinel_reply", re.compile(r"«回复[^»]*»"), action="strip"),
    SentinelEntry("sentinel_sticker", re.compile(r"«表情包:[^»]+»"), action="strip"),
    SentinelEntry("sentinel_img_tag", re.compile(r"«img:\d+»"), action="strip"),
)


def _compile_pattern(pattern: str | re.Pattern[str]) -> re.Pattern[str]:
    if isinstance(pattern, re.Pattern):
        return pattern
    return re.compile(pattern)


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
        self._rules: list[RuleHandler] = []
        for entry in _DEFAULT_SENTINELS:
            self.register(entry)

    def register(self, entry: SentinelEntry) -> None:
        pattern = _compile_pattern(entry.pattern)

        def _rule(text: str, _ctx: GuardrailContext) -> GuardrailResult:
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

        self._rules.append(_rule)

    def register_rule(self, handler: RuleHandler) -> None:
        self._rules.append(handler)

    def apply(
        self,
        text: str,
        *,
        thinker_thought: str = "",
        last_assistant_text: str = "",
        config: Any = None,
    ) -> GuardrailResult:
        current = text
        hits: list[GuardrailHit] = []
        metadata: dict[str, Any] = {}
        failed_closed = False
        context = GuardrailContext(
            thinker_thought=thinker_thought,
            last_assistant_text=last_assistant_text,
            config=config,
        )
        for rule in self._rules:
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


def register_rule(handler: RuleHandler) -> None:
    _REGISTRY.register_rule(handler)


def apply_guardrails(
    text: str,
    *,
    thinker_thought: str = "",
    last_assistant_text: str = "",
    config: Any = None,
) -> GuardrailResult:
    return _REGISTRY.apply(
        text,
        thinker_thought=thinker_thought,
        last_assistant_text=last_assistant_text,
        config=config,
    )


# Register non-sentinel A-cluster rules on import.
from services.llm import dedup_gate as _dedup_gate  # noqa: E402,F401
from services.llm import thinker_phrase_detector as _thinker_phrase_detector  # noqa: E402,F401
