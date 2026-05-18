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
    LLMRequest,
    LLMTask,
    all_llm_tasks,
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


def test_system_blocks_accept_dict_blocks_with_cache_control() -> None:
    """Plugin-contributed blocks may already carry cache_control."""
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
    assert block["cache_control"] == {"type": "ephemeral"}
    # Spine tags the segment for downstream diagnostics.
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
