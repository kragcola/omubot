from __future__ import annotations

from typing import Any

import pytest

from kernel.router import _render_forward_msg


class FakeBot:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_api(self, api: str, **data: Any) -> Any:
        self.calls.append((api, data))
        message_id = str(data["message_id"])
        response = self.responses[message_id]
        if isinstance(response, Exception):
            raise response
        return response


class ExplodingSegment(dict[str, Any]):
    def get(self, key: str, default: Any = None) -> Any:
        raise AssertionError(f"segment beyond character budget was inspected: {key}")


def _node(name: str, user_id: int, content: list[Any]) -> dict[str, Any]:
    return {
        "sender": {"nickname": name, "user_id": user_id},
        "content": content,
    }


def _text(value: str) -> dict[str, Any]:
    return {"type": "text", "data": {"text": value}}


def _inline_forward(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "forward", "data": {"content": nodes}}


def _forward_id(message_id: str) -> dict[str, Any]:
    return {"type": "forward", "data": {"id": message_id}}


def _called_message_ids(bot: FakeBot) -> list[str]:
    return [str(data["message_id"]) for api, data in bot.calls if api == "get_forward_msg"]


@pytest.mark.asyncio
async def test_expands_three_levels_of_inline_forward_content() -> None:
    deepest = _node("Deep", 104, [_text("deep text")])
    third = _node("Third", 103, [_text("third text"), _inline_forward([deepest])])
    second = _node("Second", 102, [_text("second text"), _inline_forward([third])])
    first = _node("First", 101, [_text("first text"), _inline_forward([second])])
    bot = FakeBot({"root": {"messages": [first]}})

    rendered = await _render_forward_msg("root", bot)

    expected_fragments = [
        "First(101): first text",
        "Second(102): second text",
        "Third(103): third text",
        "Deep(104): deep text",
    ]
    assert all(fragment in rendered for fragment in expected_fragments)
    positions = [rendered.index(fragment) for fragment in expected_fragments]
    assert positions == sorted(positions)
    assert bot.calls == [("get_forward_msg", {"message_id": "root"})]


@pytest.mark.asyncio
async def test_inline_content_takes_precedence_over_forward_id_even_when_empty() -> None:
    embedded = _node("Embedded", 201, [_text("inline wins")])
    root = _node(
        "Root",
        200,
        [
            {"type": "forward", "data": {"id": "ignored", "content": [embedded]}},
            {"type": "forward", "data": {"id": "also-ignored", "content": []}},
        ],
    )
    bot = FakeBot(
        {
            "root": {"messages": [root]},
            "ignored": {"messages": [_node("Wrong", 999, [_text("external content")])]},
            "also-ignored": {"messages": [_node("Wrong", 998, [_text("external empty")])]},
        }
    )

    rendered = await _render_forward_msg("root", bot)

    assert "Embedded(201): inline wins" in rendered
    assert "external content" not in rendered
    assert "external empty" not in rendered
    assert _called_message_ids(bot) == ["root"]


@pytest.mark.asyncio
async def test_missing_inline_content_fetches_nested_forward_by_id() -> None:
    root = _node("Parent", 301, [_text("before"), _forward_id("child"), _text("after")])
    child = _node("Child", 302, [_text("fetched text")])
    bot = FakeBot(
        {
            "root": {"messages": [root]},
            "child": {"messages": [child]},
        }
    )

    rendered = await _render_forward_msg("root", bot)

    assert "Parent(301): before" in rendered
    assert "Child(302): fetched text" in rendered
    assert "after" in rendered
    assert _called_message_ids(bot) == ["root", "child"]


@pytest.mark.asyncio
async def test_cyclic_forward_id_stops_with_explicit_placeholder() -> None:
    root = _node("Looper", 401, [_text("kept text"), _forward_id("root")])
    bot = FakeBot({"root": {"messages": [root]}})

    rendered = await _render_forward_msg("root", bot)

    assert "Looper(401): kept text" in rendered
    assert "循环" in rendered
    assert _called_message_ids(bot) == ["root"]


@pytest.mark.asyncio
async def test_repeated_forward_id_is_not_fetched_repeatedly() -> None:
    root = _node("Root", 501, [_forward_id("shared"), _forward_id("shared")])
    shared = _node("Shared", 502, [_text("shared text")])
    bot = FakeBot(
        {
            "root": {"messages": [root]},
            "shared": {"messages": [shared]},
        }
    )

    rendered = await _render_forward_msg("root", bot)

    assert "Shared(502): shared text" in rendered
    assert _called_message_ids(bot) == ["root", "shared"]


@pytest.mark.asyncio
async def test_maximum_inline_depth_emits_truncation_placeholder() -> None:
    content: list[dict[str, Any]] = [_text("deepest-must-not-render")]
    for level in reversed(range(256)):
        content = [_inline_forward([_node(f"Level{level}", 600 + level, content)])]
    root = _node("Root", 599, [_text("root survives"), *content])
    bot = FakeBot({"root": {"messages": [root]}})

    rendered = await _render_forward_msg("root", bot)

    assert "Root(599): root survives" in rendered
    assert "deepest-must-not-render" not in rendered
    assert "截断" in rendered


@pytest.mark.asyncio
async def test_total_node_limit_emits_truncation_placeholder() -> None:
    nodes = [_node(f"N{index}", 10_000 + index, [_text("x")]) for index in range(5_000)]
    bot = FakeBot({"root": {"messages": nodes}})

    rendered = await _render_forward_msg("root", bot)

    assert "N0(10000): x" in rendered
    assert "N4999(14999): x" not in rendered
    assert "截断" in rendered


@pytest.mark.asyncio
async def test_total_character_limit_emits_truncation_placeholder() -> None:
    huge_text = "character-budget-probe-" * 50_000
    root = _node("Writer", 701, [_text(huge_text)])
    bot = FakeBot({"root": {"messages": [root]}})

    rendered = await _render_forward_msg("root", bot)

    assert rendered.startswith("«合并转发消息»")
    assert len(rendered) < len(huge_text)
    assert len(rendered) <= 2_000
    assert "截断" in rendered


@pytest.mark.asyncio
async def test_character_budget_stops_scanning_later_segments() -> None:
    segments = [_text("xx") for _ in range(5_000)]
    segments.append(ExplodingSegment())
    root = _node("Writer", 702, segments)
    bot = FakeBot({"root": {"messages": [root]}})

    rendered = await _render_forward_msg("root", bot)

    assert len(rendered) <= 2_000
    assert "截断" in rendered


@pytest.mark.asyncio
async def test_segment_budget_counts_malformed_non_dict_segments() -> None:
    segments: list[Any] = [None] * 5_000
    segments.append(ExplodingSegment())
    root = _node("Writer", 703, segments)
    bot = FakeBot({"root": {"messages": [root]}})

    rendered = await _render_forward_msg("root", bot)

    assert len(rendered) <= 2_000
    assert "截断" in rendered


@pytest.mark.asyncio
async def test_leading_whitespace_does_not_hide_following_nested_content() -> None:
    child = _node("Child", 704, [_text("still visible")])
    root = _node("Writer", 705, [_text(" " * 5_000), _inline_forward([child])])
    bot = FakeBot({"root": {"messages": [root]}})

    rendered = await _render_forward_msg("root", bot)

    assert "Child(704): still visible" in rendered
    assert "截断" not in rendered


@pytest.mark.asyncio
async def test_nested_api_failure_preserves_parent_and_sibling_content() -> None:
    parent = _node("Parent", 801, [_text("before failure"), _forward_id("broken"), _text("after failure")])
    sibling = _node("Sibling", 802, [_text("sibling survives")])
    bot = FakeBot(
        {
            "root": {"messages": [parent, sibling]},
            "broken": RuntimeError("nested fetch failed"),
        }
    )

    rendered = await _render_forward_msg("root", bot)

    assert "Parent(801): before failure" in rendered
    assert "after failure" in rendered
    assert "Sibling(802): sibling survives" in rendered
    assert _called_message_ids(bot) == ["root", "broken"]


@pytest.mark.asyncio
async def test_existing_segment_summaries_remain_readable() -> None:
    node = _node(
        "Alice",
        901,
        [
            _text("hello"),
            {"type": "image", "data": {"url": "https://example.test/a.jpg"}},
            {"type": "face", "data": {"id": "14"}},
            {"type": "at", "data": {"qq": "all"}},
            {"type": "file", "data": {"name": "report.pdf"}},
        ],
    )
    bot = FakeBot({"root": {"messages": [node]}})

    rendered = await _render_forward_msg("root", bot)

    assert "Alice(901): hello" in rendered
    assert "«图片»" in rendered
    assert "https://example.test/a.jpg" not in rendered
    assert "«表情»" in rendered
    assert "@all" in rendered
    assert "«文件: report.pdf»" in rendered
