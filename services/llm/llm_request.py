"""Unified LLM call contract.

All LLM call sites must construct an ``LLMRequest`` instead of stitching
system prompts inline. This is the spine refactor described in
``docs/audits/prompt-cache-research-2026-05-18.md`` § 12.

The contract enforces three things by construction:

1. System prompt is split into static / stable / dynamic segments and
   serialized in that exact order. Callers cannot accidentally put a
   per-turn block in front of a stable identity prompt.
2. Every call carries an explicit ``task`` label. Profile routing,
   per-task usage accounting, and per-task cache-hit attribution all key
   off this label, so call sites must declare it.
3. Capability requirements are declared up-front. ``LLMClient._call``
   refuses to dispatch a request whose ``requires_capabilities`` are not
   satisfied by the resolved profile (fail-fast, not silent fallback).

The ``LLMTask`` Literal is the single source of truth for valid task
names. ``admin/routes/api/providers.py`` (``_LLM_TASKS``) and
``admin/frontend/src/views/system/helpers/types.ts`` (``ProviderTaskKey``)
must stay in sync with it; that's enforced by tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, get_args

LLMTask = Literal[
    # Core message-handling chain.
    "main",
    "thinker",
    "compact",
    "reply_gate",
    "vision",
    # Slang governance pipeline.
    "slang",
    "slang_review",
    "slang_drift",
    "slang_semantic",
    # Other learning / extraction services.
    "style",
    "memo",
    # Plugin-direct call sites. Each plugin entry point gets its own
    # task name so admins can route it to a different provider in the
    # admin panel without touching code.
    "chat_private",
    "bilibili_intent",
    "element_detect",
    # Multilayer memory framework (Phase A.5+ in
    # multilayer-memory-learning-report-2026-05-17.md). Reserved here
    # so future call sites land on a typed contract from day one.
    "graph_review",
    "graph_edge_classifier",
    "reflection_consolidator",
    "episode_summarizer",
]


def all_llm_tasks() -> tuple[str, ...]:
    """Return every valid task name. Used to keep admin task list in sync."""
    return tuple(get_args(LLMTask))


LLMCapability = Literal["chat", "tools", "thinking", "vision", "json", "compact"]


_SEGMENT_STATIC = "static"
_SEGMENT_STABLE = "stable"
_SEGMENT_DYNAMIC = "dynamic"


def _normalize_block(block: str | dict[str, Any], segment: str) -> dict[str, Any] | None:
    """Normalize a caller-supplied block into a provider-ready dict.

    Callers may pass either a raw string (most common — pre-built prompt
    text) or an already-shaped block dict (e.g. plugin-contributed
    blocks that already carry ``cache_control``). Empty / whitespace-only
    text is dropped — passing it through would create a meaningless
    extra system block and shift the cached prefix.
    """
    if isinstance(block, str):
        text = block.strip()
        if not text:
            return None
        return {"type": "text", "text": text, "_omu_segment": segment}

    if not isinstance(block, dict):
        return None

    block_type = str(block.get("type", "text") or "text")
    if block_type == "text":
        text = str(block.get("text", "") or "").strip()
        if not text:
            return None
        normalized = dict(block)
        normalized["type"] = "text"
        normalized["text"] = text
        normalized.setdefault("_omu_segment", segment)
        return normalized

    # Non-text blocks (e.g. image) pass through but still get tagged.
    normalized = dict(block)
    normalized.setdefault("_omu_segment", segment)
    return normalized


@dataclass
class LLMRequest:
    """Unified LLM call contract.

    System prompt is segmented into three buckets that are concatenated
    in a fixed order — static first, dynamic last — so DeepSeek's token
    prefix cache stays warm across calls of the same task.

    Attributes
    ----------
    task:
        One of :data:`LLMTask`. Drives profile routing, usage
        accounting, and per-task cache-hit telemetry.
    static_blocks:
        Byte-stable across every call of this task (identity prompt,
        output schema). Must not contain mood, time, or per-call state.
    stable_blocks:
        Occasionally varying per group / per profile (e.g. group-level
        custom prompt, tool library view).
    dynamic_blocks:
        Per-turn content (mood, affection, current schedule). Anything
        that changes every call belongs here.
    user_messages:
        Conversation messages in Anthropic-shape dicts; providers
        translate this to their native format.
    tools:
        Tool definitions. ``None`` means no tools.
    requires_capabilities:
        Capabilities the resolved profile must declare. ``LLMClient._call``
        raises ``ValueError`` when the profile is missing any required
        capability — fail-fast, never silent fallback.
    auto_record_usage:
        When True (default) the spine writes one ``llm_calls`` row per
        ``_call`` invocation. Set False for callers that aggregate token
        counts across multiple rounds before writing a single row (the
        canonical example is ``LLMClient._compact_with_tools`` and the
        main ``chat()`` tool loop, both of which preserve the "1 session
        = 1 usage row" contract asserted by ``test_compact_records_usage``
        / ``test_chat_records_usage``). Cache diagnostic recording is
        unaffected — it always runs per call so each round still appears
        in ``cache_diagnostic_history(task)``.
    """

    task: LLMTask
    user_id: str = ""
    group_id: str | None = None

    static_blocks: list[str | dict[str, Any]] = field(default_factory=list)
    stable_blocks: list[str | dict[str, Any]] = field(default_factory=list)
    dynamic_blocks: list[str | dict[str, Any]] = field(default_factory=list)

    user_messages: list[Any] = field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    max_tokens: int = 1024
    thinking: dict[str, Any] | None = None

    requires_capabilities: tuple[LLMCapability, ...] = ()
    auto_record_usage: bool = True

    def system_blocks(self) -> list[dict[str, Any]]:
        """Compose ``static → stable → dynamic`` system blocks.

        The order is fixed by construction; callers cannot reorder it.
        Empty / whitespace-only text blocks are dropped silently so a
        plugin returning an empty rendering doesn't shift the cached
        prefix downstream.
        """
        out: list[dict[str, Any]] = []
        for raw in self.static_blocks:
            normalized = _normalize_block(raw, _SEGMENT_STATIC)
            if normalized is not None:
                out.append(normalized)
        for raw in self.stable_blocks:
            normalized = _normalize_block(raw, _SEGMENT_STABLE)
            if normalized is not None:
                out.append(normalized)
        for raw in self.dynamic_blocks:
            normalized = _normalize_block(raw, _SEGMENT_DYNAMIC)
            if normalized is not None:
                out.append(normalized)
        return out

    def to_provider_payload(self) -> tuple[list[dict[str, Any]], list[Any], list[dict[str, Any]] | None]:
        """Return ``(system_blocks, messages, tools)`` ready for ``call_api``.

        Provider-specific shape conversion (Anthropic vs OpenAI vs
        DeepSeek) happens inside the provider classes; this method just
        guarantees the segmented order before it leaves the spine.
        """
        return self.system_blocks(), list(self.user_messages), self.tools
