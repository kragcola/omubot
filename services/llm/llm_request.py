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
    "persona_import",
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
    # Scheduler humanization probes.
    "scheduler_eot",
    "scheduler_replay_judge",
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
    text) or an already-shaped block dict. Empty / whitespace-only text
    is dropped — passing it through would create a meaningless extra
    system block and shift the cached prefix.

    Any ``cache_control`` field on incoming dict blocks is stripped:
    spine is the single source of truth for prompt-cache breakpoints
    (see :func:`apply_cache_breakpoints`). Allowing callers to pre-set
    breakpoints would double-count against Anthropic's ≤4-marker cap.
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
        normalized = {k: v for k, v in block.items() if k != "cache_control"}
        normalized["type"] = "text"
        normalized["text"] = text
        normalized.setdefault("_omu_segment", segment)
        return normalized

    # Non-text blocks (e.g. image) pass through but still get tagged.
    # cache_control on non-text blocks is also stripped — spine is the
    # single source of truth (see ``apply_cache_breakpoints``).
    normalized = {k: v for k, v in block.items() if k != "cache_control"}
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


# ---------------------------------------------------------------------------
# Per-task cache profile + spine-side breakpoint injector
# ---------------------------------------------------------------------------
#
# Anthropic limits a single request to ≤4 ephemeral cache breakpoints
# total (counted across system + tools + messages). Before the spine
# took over this responsibility, ``cache_control`` was stamped in 5+
# scattered locations (prompt_builder, client message builders,
# per-provider tool tail, plugin pre_prompt wrapper) with no global
# counter — so worst-case ``main`` requests already exceeded the cap
# silently. Plugin-direct paths (``memo`` / ``slang*`` / ``style`` etc.)
# meanwhile got ZERO breakpoints because they bypassed prompt_builder
# entirely, which the dashboard cache-pipeline panel exposed on
# 2026-05-19. ``apply_cache_breakpoints`` is now the single source of
# truth: ``LLMClient._dispatch_call`` calls it once per request, after
# resolving the task profile, and provider tool-tail / message-side
# breakpoints get a budgeted slot reserved out of the ≤4 cap.


@dataclass(frozen=True)
class TaskCacheProfile:
    """How many cache breakpoints spine emits for this task.

    Attributes
    ----------
    system_breakpoints:
        Number of ephemeral markers on system blocks. Spine places one
        at the tail of each segment (static → stable → dynamic),
        outer-first, capped at this number.
    message_breakpoint:
        ``True`` when the path also stamps a user-message-side
        breakpoint elsewhere (group/private message builders that own
        timeline state). Spine reads this to subtract from the system
        budget when computing the ≤4 cap. It is a *predicate*, not a
        directive — spine never injects message-side markers itself.
    """

    system_breakpoints: int = 1
    message_breakpoint: bool = False


# Single source of truth — same module as ``LLMTask`` so the two stay
# aligned. Every entry in ``LLMTask`` should appear here; new tasks
# fall through to ``DEFAULT_TASK_CACHE_PROFILE``.
TASK_CACHE_PROFILES: dict[str, TaskCacheProfile] = {
    # core_chat — main goes through prompt_builder which produces a
    # static identity block, a state_board block, and a stable plugin
    # tail (≤3 cacheable system segments) plus a message-side
    # breakpoint and an Anthropic tool-tail. ``system_breakpoints=3``
    # + tools + message = 5 → spine caps at 4 by dropping the
    # outermost system marker first (static > stable > dynamic).
    "main":       TaskCacheProfile(system_breakpoints=3, message_breakpoint=True),
    "thinker":    TaskCacheProfile(system_breakpoints=2, message_breakpoint=False),
    "compact":    TaskCacheProfile(system_breakpoints=1, message_breakpoint=False),
    "reply_gate": TaskCacheProfile(system_breakpoints=1, message_breakpoint=False),
    # vision — single static prompt, no plugin contributions.
    "vision":     TaskCacheProfile(system_breakpoints=1, message_breakpoint=False),
    # slang governance — 2 static blocks: shared_prefix (identical across
    # all 4 slang tasks, ~800 chars) + task-specific system prompt.
    # Marking both lets the shared prefix cache survive across different
    # slang task calls (extractor → reviewer → drift → semantic).
    "slang":          TaskCacheProfile(system_breakpoints=2),
    "slang_review":   TaskCacheProfile(system_breakpoints=2),
    "slang_drift":    TaskCacheProfile(system_breakpoints=2),
    "slang_semantic": TaskCacheProfile(system_breakpoints=2),
    # learning / extraction services.
    "style":           TaskCacheProfile(system_breakpoints=1),
    "memo":            TaskCacheProfile(system_breakpoints=1),
    # persona_import — single static system prompt that wraps importer
    # extraction; no plugin contributions, no message-side reuse.
    "persona_import":  TaskCacheProfile(system_breakpoints=1),
    # chat_private — debug plugin path: low-frequency, 1-2 system blocks
    # all in static segment. Single marker on the tail is sufficient.
    "chat_private":    TaskCacheProfile(system_breakpoints=1),
    "bilibili_intent": TaskCacheProfile(system_breakpoints=1),
    "element_detect":  TaskCacheProfile(system_breakpoints=1),
    # memory_graph (reserved) — defaults; tune after first call sites
    # land and dashboard surfaces real hit rates.
    "graph_review":            TaskCacheProfile(system_breakpoints=1),
    "graph_edge_classifier":   TaskCacheProfile(system_breakpoints=1),
    "reflection_consolidator": TaskCacheProfile(system_breakpoints=1),
    "episode_summarizer":      TaskCacheProfile(system_breakpoints=1),
    "scheduler_eot":           TaskCacheProfile(system_breakpoints=1),
    "scheduler_replay_judge":  TaskCacheProfile(system_breakpoints=1),
}

DEFAULT_TASK_CACHE_PROFILE = TaskCacheProfile(system_breakpoints=1)


def cache_profile_for_task(task: str) -> TaskCacheProfile:
    """Return the cache profile for ``task``, with safe default fallback."""
    return TASK_CACHE_PROFILES.get(task, DEFAULT_TASK_CACHE_PROFILE)


_ANTHROPIC_CACHE_CAP = 4  # Anthropic prompt-cache hard limit per request.


def apply_cache_breakpoints(
    system_blocks: list[dict[str, Any]],
    *,
    task: str,
    has_tools: bool,
) -> list[dict[str, Any]]:
    """Strip caller-provided ``cache_control`` and re-apply per task profile.

    Spine is the single source of truth — this function is the only
    place ``cache_control`` is stamped on system blocks. Anything
    upstream is treated as advisory and discarded so we cannot
    double-count against Anthropic's ≤4-marker cap.

    Placement strategy (outer-first, end-of-segment): for ``N``
    system breakpoints, walk segments in order (static, stable,
    dynamic) and mark the **last** block of each. ``static`` always
    wins the first slot because it changes least; ``dynamic`` is
    sacrificed first when capping kicks in.

    Tools tail (added by provider) and message-side marker (added by
    group/private message builders that own timeline state) each eat
    one slot from the ≤4 cap when they apply.
    """
    profile = cache_profile_for_task(task)

    reserved = (1 if has_tools else 0) + (1 if profile.message_breakpoint else 0)
    system_budget = max(
        0, min(profile.system_breakpoints, _ANTHROPIC_CACHE_CAP - reserved),
    )

    stripped: list[dict[str, Any]] = []
    for block in system_blocks:
        if isinstance(block, dict) and "cache_control" in block:
            block = {k: v for k, v in block.items() if k != "cache_control"}
        stripped.append(block)

    if system_budget <= 0:
        return stripped

    # Find the tail index of each segment. ``_omu_segment`` is set by
    # ``_normalize_block``; anything missing it (raw caller dicts that
    # bypassed normalization) falls back to ``static`` so its marker
    # gets the highest-priority slot — safer than dropping it.
    tails: dict[str, int] = {}
    for i, block in enumerate(stripped):
        seg = str(block.get("_omu_segment", "") or "static") if isinstance(block, dict) else "static"
        tails[seg] = i

    chosen: list[int] = []
    for seg in (_SEGMENT_STATIC, _SEGMENT_STABLE, _SEGMENT_DYNAMIC):
        if seg in tails and len(chosen) < system_budget:
            chosen.append(tails[seg])

    for idx in chosen:
        block = stripped[idx]
        if isinstance(block, dict):
            stripped[idx] = {**block, "cache_control": {"type": "ephemeral"}}

    return stripped
