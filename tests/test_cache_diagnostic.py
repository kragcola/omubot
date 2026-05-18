"""Tests for the per-axis cache diagnostic snapshots."""

from __future__ import annotations

from services.llm.cache_diagnostic import (
    compute_cache_diagnostic,
    diff_cache_diagnostics,
)


def _system(*texts: str) -> list[dict]:
    return [{"type": "text", "text": t, "_omu_segment": "static"} for t in texts]


def test_identical_inputs_produce_identical_hashes() -> None:
    sys_blocks = _system("identity", "tools-overview")
    tools = [{"name": "search"}]
    msgs = [{"role": "user", "content": "hi"}]
    a = compute_cache_diagnostic(
        task="main", profile="main",
        system_blocks=sys_blocks, tools=tools, messages=msgs,
    )
    b = compute_cache_diagnostic(
        task="main", profile="main",
        system_blocks=sys_blocks, tools=tools, messages=msgs,
    )
    assert a.system_hash == b.system_hash
    assert a.tools_hash == b.tools_hash
    assert a.messages_hash == b.messages_hash
    assert a.per_block_hashes == b.per_block_hashes


def test_cache_control_change_does_not_shift_system_hash() -> None:
    """TTL upgrades must not look like content drift (Claude Code's sn9)."""
    base = [{"type": "text", "text": "identity"}]
    with_ttl = [{"type": "text", "text": "identity", "cache_control": {"type": "ephemeral"}}]
    a = compute_cache_diagnostic(task="main", profile="main",
                                 system_blocks=base, tools=None, messages=[])
    b = compute_cache_diagnostic(task="main", profile="main",
                                 system_blocks=with_ttl, tools=None, messages=[])
    assert a.system_hash == b.system_hash


def test_segment_tag_does_not_shift_system_hash() -> None:
    """_omu_segment is a debug tag added by LLMRequest, not real content."""
    a = compute_cache_diagnostic(
        task="main", profile="main",
        system_blocks=[{"type": "text", "text": "x"}],
        tools=None, messages=[],
    )
    b = compute_cache_diagnostic(
        task="main", profile="main",
        system_blocks=[{"type": "text", "text": "x", "_omu_segment": "static"}],
        tools=None, messages=[],
    )
    assert a.system_hash == b.system_hash


def test_image_with_large_base64_hashes_by_length() -> None:
    """Same image redownloaded after transcoding shouldn't read as drift."""
    long_a = "A" * 1024
    long_b = "B" * 1024
    block_a = {"type": "image", "source": {"type": "base64", "data": long_a}}
    block_b = {"type": "image", "source": {"type": "base64", "data": long_b}}
    a = compute_cache_diagnostic(task="vision", profile="main",
                                 system_blocks=[block_a], tools=None, messages=[])
    b = compute_cache_diagnostic(task="vision", profile="main",
                                 system_blocks=[block_b], tools=None, messages=[])
    assert a.system_hash == b.system_hash


def test_diff_pinpoints_changed_block_index() -> None:
    prev_blocks = _system("identity", "tools", "mood:happy")
    curr_blocks = _system("identity", "tools", "mood:sad")
    prev = compute_cache_diagnostic(task="thinker", profile="main",
                                    system_blocks=prev_blocks, tools=None, messages=[])
    curr = compute_cache_diagnostic(task="thinker", profile="main",
                                    system_blocks=curr_blocks, tools=None, messages=[])
    diff = diff_cache_diagnostics(prev, curr)
    assert diff.system_changed is True
    assert diff.tools_changed is False
    assert diff.changed_block_indices == [2]


def test_diff_detects_added_and_removed_tools() -> None:
    prev = compute_cache_diagnostic(task="main", profile="main",
                                    system_blocks=[], tools=[{"name": "a"}, {"name": "b"}],
                                    messages=[])
    curr = compute_cache_diagnostic(task="main", profile="main",
                                    system_blocks=[], tools=[{"name": "a"}, {"name": "c"}],
                                    messages=[])
    diff = diff_cache_diagnostics(prev, curr)
    assert diff.tools_changed is True
    assert diff.added_tools == ["c"]
    assert diff.removed_tools == ["b"]


def test_diff_detects_changed_tool_schema() -> None:
    prev = compute_cache_diagnostic(task="main", profile="main",
                                    system_blocks=[],
                                    tools=[{"name": "search", "description": "v1"}],
                                    messages=[])
    curr = compute_cache_diagnostic(task="main", profile="main",
                                    system_blocks=[],
                                    tools=[{"name": "search", "description": "v2"}],
                                    messages=[])
    diff = diff_cache_diagnostics(prev, curr)
    assert diff.tools_changed is True
    assert diff.changed_tools == ["search"]
    assert diff.added_tools == []
    assert diff.removed_tools == []


def test_diff_first_changed_message_index_handles_appended_messages() -> None:
    prev = compute_cache_diagnostic(
        task="main", profile="main", system_blocks=[], tools=None,
        messages=[{"role": "user", "content": "a"}],
    )
    curr = compute_cache_diagnostic(
        task="main", profile="main", system_blocks=[], tools=None,
        messages=[
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ],
    )
    diff = diff_cache_diagnostics(prev, curr)
    assert diff.messages_changed is True
    # Common prefix is identical; the change starts at the new message.
    assert diff.first_changed_message_index == 1


def test_diff_first_changed_message_index_inside_overlap() -> None:
    prev = compute_cache_diagnostic(
        task="main", profile="main", system_blocks=[], tools=None,
        messages=[
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ],
    )
    curr = compute_cache_diagnostic(
        task="main", profile="main", system_blocks=[], tools=None,
        messages=[
            {"role": "user", "content": "a"},
            {"role": "user", "content": "DIFFERENT"},
        ],
    )
    diff = diff_cache_diagnostics(prev, curr)
    assert diff.first_changed_message_index == 1


def test_diff_rejects_cross_task_comparison() -> None:
    a = compute_cache_diagnostic(task="thinker", profile="main",
                                 system_blocks=[], tools=None, messages=[])
    b = compute_cache_diagnostic(task="slang", profile="main",
                                 system_blocks=[], tools=None, messages=[])
    try:
        diff_cache_diagnostics(a, b)
    except ValueError as exc:
        assert "different tasks" in str(exc)
    else:
        raise AssertionError("expected ValueError on cross-task diff")


def test_per_block_segments_are_recorded_in_order() -> None:
    blocks = [
        {"type": "text", "text": "s1", "_omu_segment": "static"},
        {"type": "text", "text": "b1", "_omu_segment": "stable"},
        {"type": "text", "text": "d1", "_omu_segment": "dynamic"},
    ]
    snap = compute_cache_diagnostic(task="main", profile="main",
                                    system_blocks=blocks, tools=None, messages=[])
    assert snap.per_block_segments == ["static", "stable", "dynamic"]


def test_per_block_lengths_count_text_only() -> None:
    blocks = [
        {"type": "text", "text": "abc"},
        {"type": "image", "source": {"data": "x"}},
        {"type": "text", "text": "de"},
    ]
    snap = compute_cache_diagnostic(task="main", profile="main",
                                    system_blocks=blocks, tools=None, messages=[])
    assert snap.per_block_lengths == [3, 0, 2]


def test_to_dict_round_trips_all_fields() -> None:
    snap = compute_cache_diagnostic(
        task="main", profile="main",
        system_blocks=[{"type": "text", "text": "hi"}],
        tools=[{"name": "t"}],
        messages=[{"role": "user", "content": "hello"}],
    )
    data = snap.to_dict()
    assert set(data) >= {
        "task", "profile", "system_hash", "tools_hash", "messages_hash",
        "per_block_hashes", "per_block_lengths", "per_block_segments",
        "per_tool_hashes", "per_message_hashes",
    }
    assert data["task"] == "main"
    assert data["profile"] == "main"
