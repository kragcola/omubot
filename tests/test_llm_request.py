"""Tests for the unified LLM call contract (services/llm/llm_request.py).

These tests pin three properties that the spine refactor depends on:

1. Static / stable / dynamic blocks always serialize in that order, no
   matter what order callers populated the dataclass.
2. Empty or whitespace-only blocks are dropped — passing one through
   would shift the cached prefix downstream.
3. ``LLMTask`` Literal stays in lock-step with admin-side mirrors.
   Adding a new task without updating ``_LLM_TASKS`` (backend) or
   ``ProviderTaskKey`` (frontend) is a footgun we want to fail fast.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.llm.llm_request import (
    TASK_CACHE_PROFILES,
    LLMRequest,
    LLMTask,
    all_llm_tasks,
    apply_cache_breakpoints,
    cache_profile_for_task,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_all_llm_tasks_returns_literal_args() -> None:
    from typing import get_args

    assert all_llm_tasks() == get_args(LLMTask)
    assert "main" in all_llm_tasks()
    assert "reply_gate" in all_llm_tasks()
    assert "chat_private" in all_llm_tasks()
    assert "graph_review" in all_llm_tasks()


def test_system_blocks_preserve_static_stable_dynamic_order() -> None:
    req = LLMRequest(
        task="thinker",
        # Intentionally populate dynamic first to prove dataclass field
        # order doesn't leak into composition.
        dynamic_blocks=["dynamic-mood"],
        static_blocks=["static-identity"],
        stable_blocks=["stable-group"],
    )
    blocks = req.system_blocks()
    assert [b["text"] for b in blocks] == [
        "static-identity",
        "stable-group",
        "dynamic-mood",
    ]
    assert [b["_omu_segment"] for b in blocks] == ["static", "stable", "dynamic"]


def test_system_blocks_drop_empty_and_whitespace_only_text() -> None:
    req = LLMRequest(
        task="slang",
        static_blocks=["", "   ", "real-static"],
        stable_blocks=[{"type": "text", "text": "   \n  "}],
        dynamic_blocks=["real-dynamic", ""],
    )
    blocks = req.system_blocks()
    assert [b["text"] for b in blocks] == ["real-static", "real-dynamic"]


def test_system_blocks_strip_caller_provided_cache_control() -> None:
    """Spine is the single source of truth for ``cache_control``.

    Caller-supplied ``cache_control`` on dict blocks is stripped during
    normalization; ``apply_cache_breakpoints`` (called from
    ``LLMClient._dispatch_call``) re-stamps according to the per-task
    profile. This prevents double-counting against Anthropic's
    ≤4-marker cap.
    """
    req = LLMRequest(
        task="main",
        static_blocks=[
            {
                "type": "text",
                "text": "with-cache-control",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    )
    [block] = req.system_blocks()
    assert block["text"] == "with-cache-control"
    assert "cache_control" not in block
    assert block["_omu_segment"] == "static"


def test_system_blocks_strip_whitespace_from_dict_text() -> None:
    req = LLMRequest(
        task="main",
        dynamic_blocks=[{"type": "text", "text": "  padded  "}],
    )
    [block] = req.system_blocks()
    assert block["text"] == "padded"


def test_system_blocks_pass_through_image_blocks() -> None:
    image_block = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAA="},
    }
    req = LLMRequest(task="vision", dynamic_blocks=[image_block])
    [block] = req.system_blocks()
    assert block["type"] == "image"
    assert block["_omu_segment"] == "dynamic"


def test_to_provider_payload_returns_system_messages_tools_triple() -> None:
    tools = [{"name": "search", "description": "search docs"}]
    messages = [{"role": "user", "content": "hi"}]
    req = LLMRequest(
        task="main",
        static_blocks=["identity"],
        user_messages=messages,
        tools=tools,
    )
    system, msgs, returned_tools = req.to_provider_payload()
    assert system[0]["text"] == "identity"
    assert msgs == messages
    assert returned_tools == tools


def test_default_construction_has_safe_defaults() -> None:
    req = LLMRequest(task="main")
    assert req.user_id == ""
    assert req.group_id is None
    assert req.tools is None
    assert req.thinking is None
    assert req.requires_capabilities == ()
    assert req.system_blocks() == []
    assert req.user_messages == []


# ---------------------------------------------------------------------------
# Cross-layer sync: LLMTask must equal the admin backend / frontend mirrors.
# ---------------------------------------------------------------------------


def test_admin_backend_LLM_TASKS_matches_llm_request_literal() -> None:
    """admin/routes/api/providers.py imports all_llm_tasks(), so this is
    really a regression net for that import line."""
    from admin.routes.api.providers import _LLM_TASKS

    assert tuple(_LLM_TASKS) == all_llm_tasks()


def test_admin_frontend_ProviderTaskKey_matches_llm_request_literal() -> None:
    """The frontend Literal isn't importable from Python, so we parse
    the .ts file. Either source can drift; the test fails when it does."""
    types_path = REPO_ROOT / "admin" / "frontend" / "src" / "views" / "system" / "helpers" / "types.ts"
    text = types_path.read_text(encoding="utf-8")
    match = re.search(
        r"export type ProviderTaskKey\s*=\s*([^\n]+(?:\n\s*\|\s*'[^']+')*)",
        text,
    )
    assert match is not None, "ProviderTaskKey type alias not found"
    keys = set(re.findall(r"'([^']+)'", match.group(1)))
    assert keys == set(all_llm_tasks()), (
        "ProviderTaskKey in admin/frontend/.../helpers/types.ts has drifted "
        f"from LLMTask literal. Missing: {set(all_llm_tasks()) - keys}, "
        f"extra: {keys - set(all_llm_tasks())}"
    )


def test_kernel_config_defaults_cover_all_llm_tasks() -> None:
    """Every LLMTask must have a default profile fallback — otherwise
    `task_profiles[task]` is unset and `_profile_for_task` quietly
    falls back to ``main`` without admin-panel visibility."""
    from kernel.config import LLMConfig

    cfg = LLMConfig()
    for task in all_llm_tasks():
        assert task in cfg.task_profiles, (
            f"task {task!r} has no default profile — add it to "
            f"kernel/config.py:_ensure_main_profile defaults"
        )
        assert cfg.task_profiles[task]


def test_unknown_task_string_is_rejected_by_type_checker_only() -> None:
    """LLMTask is a Literal; runtime construction with an unknown string
    succeeds (Python typing is structural). Document this so future
    readers don't expect runtime enforcement here."""
    # This must not raise — runtime acceptance is intentional.
    req = LLMRequest(task="not-a-real-task")  # type: ignore[arg-type]
    assert req.task == "not-a-real-task"


@pytest.mark.parametrize(
    "task",
    [
        "main",
        "thinker",
        "compact",
        "reply_gate",
        "slang",
        "slang_review",
        "slang_drift",
        "slang_semantic",
        "style",
        "memo",
        "chat_private",
        "bilibili_intent",
        "element_detect",
        "graph_review",
        "graph_edge_classifier",
        "reflection_consolidator",
        "episode_summarizer",
        "vision",
    ],
)
def test_each_documented_task_can_be_constructed(task: str) -> None:
    req = LLMRequest(task=task)  # type: ignore[arg-type]
    assert req.task == task


# ---------------------------------------------------------------------------
# Cache-breakpoint injection — spine takes over from prompt_builder /
# client message builders / per-provider tool tail. See
# services/llm/llm_request.py: TASK_CACHE_PROFILES + apply_cache_breakpoints.
# ---------------------------------------------------------------------------


def test_apply_cache_breakpoints_plugin_default_one() -> None:
    """Plugin-direct paths (default profile) get exactly 1 system marker.

    Before the spine took over this responsibility the plugin-direct
    pipelines (memo / slang* / style / chat_private / etc.) had ZERO
    cache markers because they bypassed prompt_builder. This test pins
    the post-fix behavior.
    """
    req = LLMRequest(task="memo", static_blocks=["x"])
    blocks = req.system_blocks()
    out = apply_cache_breakpoints(blocks, task="memo", has_tools=False)
    assert len(out) == 1
    assert out[0]["cache_control"] == {"type": "ephemeral"}


def test_apply_cache_breakpoints_main_caps_at_four() -> None:
    """Main path: 3 system + 1 tools + 1 message-side = 5 → spine caps to 4.

    The system budget is computed as:
        min(profile.system_breakpoints, 4 - tools(1) - message_breakpoint(1))
    so for ``main`` it lands at 2 system markers, leaving room for the
    Anthropic tool tail and the message-side marker stamped elsewhere.
    """
    req = LLMRequest(
        task="main",
        static_blocks=["static-1"],
        stable_blocks=["stable-1"],
        dynamic_blocks=["dynamic-1"],
    )
    blocks = req.system_blocks()
    out = apply_cache_breakpoints(blocks, task="main", has_tools=True)
    cache_count = sum(1 for b in out if b.get("cache_control"))
    # System markers from spine + reserved (tools + message-side) ≤ 4.
    assert cache_count + 1 + 1 <= 4
    # Static must always win the first slot — it's the byte-stable prefix.
    assert out[0].get("cache_control") == {"type": "ephemeral"}


def test_apply_cache_breakpoints_strips_caller_provided() -> None:
    """Pre-existing ``cache_control`` on system blocks is stripped before re-stamping."""
    blocks = [
        {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}, "_omu_segment": "static"},
        {"type": "text", "text": "b", "cache_control": {"type": "ephemeral"}, "_omu_segment": "stable"},
        {"type": "text", "text": "c", "cache_control": {"type": "ephemeral"}, "_omu_segment": "dynamic"},
    ]
    out = apply_cache_breakpoints(blocks, task="memo", has_tools=False)
    cache_count = sum(1 for b in out if b.get("cache_control"))
    # Default profile = 1 system breakpoint, no tools, no message-side.
    assert cache_count == 1
    # Static wins, others get stripped.
    assert out[0].get("cache_control") == {"type": "ephemeral"}
    assert out[1].get("cache_control") is None
    assert out[2].get("cache_control") is None


def test_apply_cache_breakpoints_with_tools_reduces_budget() -> None:
    """``has_tools=True`` reserves one slot for the provider tool tail."""
    blocks = [
        {"type": "text", "text": "x", "_omu_segment": "static"},
    ]
    # thinker has profile.system_breakpoints=2 → with tools the budget
    # is min(2, 4 - 1) = 2 (still room). With main + tools +
    # message_breakpoint = min(3, 4 - 1 - 1) = 2.
    out_main = apply_cache_breakpoints(blocks, task="main", has_tools=True)
    out_thinker = apply_cache_breakpoints(blocks, task="thinker", has_tools=True)
    # Single block can hold at most one marker regardless of budget.
    assert out_main[0].get("cache_control") == {"type": "ephemeral"}
    assert out_thinker[0].get("cache_control") == {"type": "ephemeral"}


def test_apply_cache_breakpoints_unknown_task_uses_default() -> None:
    """Tasks not in TASK_CACHE_PROFILES fall back to DEFAULT (1 system marker)."""
    blocks = [
        {"type": "text", "text": "x", "_omu_segment": "static"},
    ]
    out = apply_cache_breakpoints(blocks, task="not-a-task", has_tools=False)
    assert sum(1 for b in out if b.get("cache_control")) == 1


def test_cache_profile_covers_every_llm_task() -> None:
    """Every LLMTask should have an explicit profile — defaults are fine
    but they should be intentional, not implicit."""
    for task in all_llm_tasks():
        assert task in TASK_CACHE_PROFILES, (
            f"task {task!r} missing from TASK_CACHE_PROFILES — "
            f"add it explicitly, even if it just inherits the default"
        )


def test_cache_profile_for_task_returns_dataclass_with_expected_fields() -> None:
    profile = cache_profile_for_task("main")
    assert profile.system_breakpoints == 3
    assert profile.message_breakpoint is True
    # slang tasks bumped to 2 (shared_prefix + task-specific prompt)
    assert cache_profile_for_task("slang").system_breakpoints == 2
    assert cache_profile_for_task("slang_review").system_breakpoints == 2
    assert cache_profile_for_task("slang_drift").system_breakpoints == 2
    assert cache_profile_for_task("slang_semantic").system_breakpoints == 2
    fallback = cache_profile_for_task("not-a-task")
    assert fallback.system_breakpoints == 1
    assert fallback.message_breakpoint is False
