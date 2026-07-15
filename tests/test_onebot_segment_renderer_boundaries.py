from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from services.llm.client import _clean_visual_query_text
from services.onebot_segments import RichRenderLimits, render_onebot_segments


def _reply(message_id: str) -> dict[str, Any]:
    return {"type": "reply", "data": {"id": message_id}}


def _text(value: str) -> dict[str, Any]:
    return {"type": "text", "data": {"text": value}}


def _forward(message_id: str) -> dict[str, Any]:
    return {"type": "forward", "data": {"id": message_id}}


def _record(
    message_id: str,
    segments: list[object],
    *,
    nickname: str = "Quoted",
) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "sender": {"user_id": message_id, "nickname": nickname},
        "message": segments,
    }


class _ExplodingSegment(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        raise AssertionError(f"segment beyond budget was inspected: {key}")

    def __iter__(self):
        raise AssertionError("segment beyond budget was iterated")

    def __len__(self) -> int:
        raise AssertionError("segment beyond budget length was inspected")

    def get(self, key: str, default: Any = None) -> Any:
        raise AssertionError(f"segment beyond budget was inspected: {key}")


class _ExplodingNode(_ExplodingSegment):
    pass


@pytest.mark.asyncio
async def test_reply_max_depth_marks_truncation_and_does_not_fetch_deeper() -> None:
    calls: list[str] = []

    async def resolve(message_id: str) -> object:
        calls.append(message_id)
        return _record(
            message_id,
            [_reply("2"), _text("first ancestor survives")],
        )

    result = await render_onebot_segments(
        [_reply("1"), _text("current survives")],
        reply_resolver=resolve,
        limits=RichRenderLimits(max_depth=1),
    )

    assert "#2" in result.text and "已截断" in result.text
    assert "first ancestor survives" in result.text and "current survives" in result.text
    assert calls == ["1"]


@pytest.mark.asyncio
async def test_max_nodes_stops_before_inspecting_later_forward_node() -> None:
    result = await render_onebot_segments(
        [
            {
                "type": "forward",
                "data": {
                    "content": [
                        {
                            "sender": {"user_id": 1, "nickname": "First"},
                            "content": [_text("first node")],
                        },
                        _ExplodingNode(),
                    ]
                },
            }
        ],
        limits=RichRenderLimits(max_nodes=1),
    )

    assert "First(1)" in result.text and "first node" in result.text
    assert "截断" in result.text


@pytest.mark.asyncio
async def test_max_segments_stops_before_inspecting_later_segment() -> None:
    result = await render_onebot_segments(
        [_text("kept"), _text(" too"), _ExplodingSegment()],
        limits=RichRenderLimits(max_segments=2),
    )

    assert result.text.startswith("kept too")
    assert "截断" in result.text


@pytest.mark.asyncio
async def test_max_chars_stops_before_inspecting_later_segment() -> None:
    result = await render_onebot_segments(
        [_text("x" * 10_000), _ExplodingSegment()],
        limits=RichRenderLimits(max_chars=40),
    )

    assert len(result.text) == 40
    assert result.text.endswith("«内容已截断»")
    assert result.truncated is True


@pytest.mark.asyncio
async def test_max_fetches_marks_remaining_reply_unavailable() -> None:
    calls: list[str] = []

    async def resolve(message_id: str) -> object:
        calls.append(message_id)
        return _record(message_id, [_text(f"body-{message_id}")])

    result = await render_onebot_segments(
        [_reply("1"), _reply("2"), _reply("3"), _text("current survives")],
        reply_resolver=resolve,
        limits=RichRenderLimits(max_fetches=2),
    )

    assert "body-1" in result.text and "body-2" in result.text
    assert "#3" in result.text and "无法获取" in result.text
    assert "current survives" in result.text
    assert calls == ["1", "2"]


@pytest.mark.asyncio
async def test_reply_resolver_timeout_is_local_and_keeps_sibling_text() -> None:
    calls: list[str] = []

    async def resolve(message_id: str) -> object:
        calls.append(message_id)
        await asyncio.sleep(0.05)
        return _record(message_id, [_text("too late")])

    result = await render_onebot_segments(
        [_reply("slow"), _text("sibling survives")],
        reply_resolver=resolve,
        limits=RichRenderLimits(resolve_timeout_s=0.001),
    )

    assert "#slow" in result.text and "无法获取" in result.text
    assert "sibling survives" in result.text
    assert calls == ["slow"]


@pytest.mark.asyncio
async def test_repeated_reply_id_uses_resolver_cache() -> None:
    calls: list[str] = []

    async def resolve(message_id: str) -> object:
        calls.append(message_id)
        return _record(message_id, [_text("cached body")])

    result = await render_onebot_segments(
        [_reply("same"), _reply("same")],
        reply_resolver=resolve,
    )

    assert result.text.count("cached body") == 2
    assert calls == ["same"]


@pytest.mark.asyncio
async def test_reply_resolver_cancellation_propagates() -> None:
    async def resolve(_message_id: str) -> object:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await render_onebot_segments([_reply("cancel")], reply_resolver=resolve)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["chars", "nodes", "segments"])
async def test_truncated_quote_stays_closed_and_visual_cleaner_keeps_current_body(
    budget: str,
) -> None:
    if budget == "chars":
        reply = _record("10", [_text("quoted " + "x" * 10_000)])
        limits = RichRenderLimits(max_chars=90)
    elif budget == "nodes":
        reply = _record(
            "10",
            [
                {
                    "type": "forward",
                    "data": {
                        "content": [
                            {"sender": {"user_id": 1}, "content": [_text("first")]},
                            {"sender": {"user_id": 2}, "content": [_text("second")]},
                        ]
                    },
                }
            ],
        )
        limits = RichRenderLimits(max_nodes=2)
    else:
        reply = _record("10", [_text("first"), _text("second")])
        limits = RichRenderLimits(max_segments=1)

    rendered = await render_onebot_segments((), reply=reply, limits=limits)
    combined = f"{rendered.text}current body"

    assert rendered.text.count("[QUOTED_MSG") == 1
    assert rendered.text.count("[/QUOTED_MSG]") == 1
    assert _clean_visual_query_text(combined) == "current body"


class _ProbeNode(Mapping[str, Any]):
    def __init__(self) -> None:
        self.touched = False

    def __getitem__(self, key: str) -> Any:
        self.touched = True
        raise AssertionError(f"node beyond budget was inspected: {key}")

    def __iter__(self):
        self.touched = True
        return iter(())

    def __len__(self) -> int:
        self.touched = True
        return 0

    def get(self, key: str, default: Any = None) -> Any:
        self.touched = True
        raise AssertionError(f"node beyond budget was inspected: {key}")


@pytest.mark.asyncio
async def test_malformed_forward_nodes_count_toward_node_budget() -> None:
    probe = _ProbeNode()

    rendered = await render_onebot_segments(
        [
            {
                "type": "forward",
                "data": {"content": [None, "bad", 123, probe]},
            }
        ],
        limits=RichRenderLimits(max_nodes=3),
    )

    assert "截断" in rendered.text
    assert "富消息不可用" not in rendered.text
    assert probe.touched is False


@pytest.mark.asyncio
async def test_image_renderer_timeout_falls_back_and_keeps_sibling_text() -> None:
    async def render_image(
        _data: Mapping[str, Any],
        _source_message_id: int | None,
    ) -> tuple[str, None]:
        await asyncio.sleep(0.05)
        return "late description", None

    rendered = await render_onebot_segments(
        [
            {"type": "image", "data": {"summary": "[fallback image]"}},
            _text(" sibling survives"),
        ],
        image_renderer=render_image,
        limits=RichRenderLimits(resolve_timeout_s=0.001),
    )

    assert "fallback image" in rendered.text
    assert "late description" not in rendered.text
    assert "sibling survives" in rendered.text


@pytest.mark.asyncio
async def test_image_renderer_cancellation_propagates() -> None:
    async def render_image(
        _data: Mapping[str, Any],
        _source_message_id: int | None,
    ) -> tuple[str, None]:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await render_onebot_segments(
            [{"type": "image", "data": {"summary": "[image]"}}],
            image_renderer=render_image,
        )


@pytest.mark.asyncio
async def test_embedded_forward_content_takes_precedence_over_renderer() -> None:
    calls: list[str] = []

    async def render_forward(message_id: str) -> str:
        calls.append(message_id)
        return "wrong external body"

    rendered = await render_onebot_segments(
        [
            {
                "type": "forward",
                "data": {
                    "id": "must-not-fetch",
                    "content": [
                        {
                            "sender": {"user_id": 7, "nickname": "Inline"},
                            "content": [_text("inline body")],
                        }
                    ],
                },
            }
        ],
        forward_renderer=render_forward,
    )

    assert "Inline(7)" in rendered.text and "inline body" in rendered.text
    assert "wrong external body" not in rendered.text
    assert calls == []


@pytest.mark.asyncio
async def test_repeated_forward_id_uses_renderer_cache() -> None:
    calls: list[str] = []

    async def render_forward(message_id: str) -> str:
        calls.append(message_id)
        return "cached forward body"

    rendered = await render_onebot_segments(
        [_forward("same"), _forward("same")],
        forward_renderer=render_forward,
    )

    assert rendered.text.count("cached forward body") == 2
    assert calls == ["same"]


@pytest.mark.asyncio
async def test_forward_renderer_respects_shared_fetch_budget() -> None:
    calls: list[str] = []

    async def render_forward(message_id: str) -> str:
        calls.append(message_id)
        return f"forward-{message_id}"

    rendered = await render_onebot_segments(
        [_forward("1"), _forward("2"), _text(" sibling survives")],
        forward_renderer=render_forward,
        limits=RichRenderLimits(max_fetches=1),
    )

    assert "forward-1" in rendered.text
    assert "#2" in rendered.text and "已截断" in rendered.text
    assert "sibling survives" in rendered.text
    assert calls == ["1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "failure"])
async def test_forward_renderer_failure_is_local_and_keeps_sibling(
    mode: str,
) -> None:
    async def render_forward(_message_id: str) -> str:
        if mode == "timeout":
            await asyncio.sleep(0.05)
            return "too late"
        raise RuntimeError("forward unavailable")

    rendered = await render_onebot_segments(
        [_forward("broken"), _text(" sibling survives")],
        forward_renderer=render_forward,
        limits=RichRenderLimits(resolve_timeout_s=0.001),
    )

    assert "无法获取内容" in rendered.text
    assert "too late" not in rendered.text
    assert "sibling survives" in rendered.text


@pytest.mark.asyncio
async def test_forward_renderer_cancellation_propagates() -> None:
    async def render_forward(_message_id: str) -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await render_onebot_segments(
            [_forward("cancel")],
            forward_renderer=render_forward,
        )


@pytest.mark.asyncio
async def test_nested_quote_near_char_limit_never_emits_partial_open_marker() -> None:
    async def resolve(message_id: str) -> object:
        return _record(message_id, [_text("nested body")], nickname="Nested")

    rendered = await render_onebot_segments(
        (),
        reply=_record(
            "outer",
            [_text("x" * 70), _reply("inner")],
            nickname="Outer",
        ),
        reply_resolver=resolve,
        limits=RichRenderLimits(max_chars=160),
    )
    combined = f"{rendered.text}current body"

    assert len(rendered.text) <= 160
    assert rendered.text.count("[QUOTED_MSG") == rendered.text.count("[/QUOTED_MSG]")
    assert "[QUOTED_MS«" not in rendered.text
    assert _clean_visual_query_text(combined) == "current body"
