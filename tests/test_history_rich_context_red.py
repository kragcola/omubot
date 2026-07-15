from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

import services.history_backfill as history_backfill
from kernel.config import BotConfig
from kernel.types import PluginContext
from services.history_backfill import load_group_history
from services.memory.timeline import GroupTimeline


class _SessionContext:
    def __init__(self) -> None:
        self.session = object()

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _ImageCache:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def save(self, _session: object, *, url: str, file_id: str) -> dict[str, str]:
        self.calls.append((url, file_id))
        return {
            "type": "image_ref",
            "path": f"/tmp/{file_id}.jpg",
            "media_type": "image/jpeg",
        }


class _ExplodingForwardData(dict[str, Any]):
    def get(self, key: str, default: Any = None) -> Any:
        if key == "content":
            raise RuntimeError("broken inline forward payload")
        return super().get(key, default)


class _ActuallyBrokenForwardData(dict[str, Any]):
    def __getitem__(self, key: str) -> Any:
        if key == "content":
            raise RuntimeError("broken inline forward payload")
        return super().__getitem__(key)


def _message(
    message_id: int,
    segments: list[dict[str, Any]],
    *,
    user_id: int = 7,
    nickname: str = "Alice",
) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "sender": {"user_id": user_id, "nickname": nickname},
        "message": segments,
    }


async def _load(
    monkeypatch: pytest.MonkeyPatch,
    messages: list[dict[str, Any]],
    *,
    image_cache: object | None = None,
) -> tuple[GroupTimeline, AsyncMock]:
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **_kwargs: _SessionContext())
    bot_call = AsyncMock(return_value={"messages": messages})
    bot = type("HistoryBot", (), {"call_api": bot_call})()
    timeline = GroupTimeline()
    await load_group_history(
        bot=bot,  # type: ignore[arg-type]
        group_ids=["100"],
        timeline=timeline,
        image_cache=image_cache,  # type: ignore[arg-type]
    )
    return timeline, bot_call


@pytest.mark.asyncio
async def test_history_keeps_json_card_alongside_existing_direct_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = json.dumps(
        {"meta": {"detail_1": {"title": "card title", "desc": "card description"}}}
    )
    image_cache = _ImageCache()
    timeline, _bot_call = await _load(
        monkeypatch,
        [
            _message(
                1,
                [
                    {"type": "json", "data": {"data": card}},
                    {
                        "type": "image",
                        "data": {"url": "https://example.test/a.jpg", "file": "direct.jpg"},
                    },
                ],
            )
        ],
        image_cache=image_cache,
    )

    pending = timeline.get_pending("100")
    assert len(pending) == 1
    content = pending[0]["content"]
    assert isinstance(content, list)
    assert any(block.get("type") == "image_ref" for block in content)
    text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
    assert "card title" in text and "card description" in text
    assert image_cache.calls == [("https://example.test/a.jpg", "direct")]


@pytest.mark.asyncio
async def test_history_expands_inline_forward_without_extra_onebot_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline, bot_call = await _load(
        monkeypatch,
        [
            _message(
                2,
                [
                    {
                        "type": "forward",
                        "data": {
                            "content": [
                                {
                                    "sender": {"user_id": 77, "nickname": "Forwarder"},
                                    "content": [
                                        {"type": "text", "data": {"text": "inline body"}}
                                    ],
                                }
                            ]
                        },
                    }
                ],
            )
        ],
    )

    pending = timeline.get_pending("100")
    assert len(pending) == 1
    content = str(pending[0]["content"])
    assert "Forwarder" in content and "77" in content and "inline body" in content
    assert bot_call.await_count == 1
    assert bot_call.await_args.kwargs == {
        "group_id": 100,
        "count": 30,
    }


@pytest.mark.asyncio
async def test_history_id_only_forward_keeps_marker_without_extra_onebot_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline, bot_call = await _load(
        monkeypatch,
        [
            _message(
                3,
                [{"type": "forward", "data": {"id": "forward-outside-window"}}],
            )
        ],
    )

    pending = timeline.get_pending("100")
    assert len(pending) == 1
    content = str(pending[0]["content"])
    assert "forward-outside-window" in content and "未展开" in content
    assert bot_call.await_count == 1
    assert bot_call.await_args.args == ("get_group_msg_history",)


@pytest.mark.asyncio
async def test_history_resolves_window_reply_locally_and_marks_external_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline, bot_call = await _load(
        monkeypatch,
        [
            _message(10, [{"type": "text", "data": {"text": "ancestor body"}}]),
            _message(
                11,
                [
                    {"type": "reply", "data": {"id": "10"}},
                    {"type": "text", "data": {"text": "local child"}},
                ],
                user_id=8,
                nickname="Bob",
            ),
            _message(
                12,
                [
                    {"type": "reply", "data": {"id": "999"}},
                    {"type": "text", "data": {"text": "external child"}},
                ],
                user_id=9,
                nickname="Carol",
            ),
        ],
    )

    pending = timeline.get_pending("100")
    assert len(pending) == 3
    local = str(pending[1]["content"])
    external = str(pending[2]["content"])
    assert "QUOTED_MSG" in local and "Alice" in local and "ancestor body" in local
    assert "local child" in local
    assert "999" in external and "external child" in external
    assert bot_call.await_count == 1


@pytest.mark.asyncio
async def test_one_broken_rich_history_message_does_not_block_later_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = json.dumps({"meta": {"detail_1": {"title": "later card"}}})
    timeline, _bot_call = await _load(
        monkeypatch,
        [
            _message(
                20,
                [{"type": "forward", "data": _ExplodingForwardData({"content": []})}],
            ),
            _message(21, [{"type": "json", "data": {"data": card}}]),
            _message(22, [{"type": "text", "data": {"text": "later plain"}}]),
        ],
    )

    pending_text = [str(item["content"]) for item in timeline.get_pending("100")]
    assert any("later card" in content for content in pending_text)
    assert any("later plain" in content for content in pending_text)


@pytest.mark.asyncio
async def test_runtime_history_backfill_never_passes_ctx_vision_client_to_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_loader = AsyncMock()
    monkeypatch.setattr(history_backfill, "load_group_history", captured_loader)
    vision_client = SimpleNamespace(
        describe_image=AsyncMock(side_effect=AssertionError("startup backfill must not use vision"))
    )
    ctx = PluginContext(config=BotConfig())
    ctx.timeline = GroupTimeline()
    ctx.image_cache = object()
    ctx.sticker_store = object()
    ctx.vision_enabled = True
    ctx.vision_client = vision_client
    bot = SimpleNamespace(self_id="42")

    await history_backfill.run_history_backfill(ctx, bot, group_ids=["100"])

    captured_loader.assert_awaited_once()
    kwargs = captured_loader.await_args.kwargs
    assert kwargs["vision_client"] is None
    assert kwargs["image_cache"] is ctx.image_cache
    assert kwargs["sticker_store"] is ctx.sticker_store
    vision_client.describe_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_one_message_uses_one_rich_budget_and_isolates_real_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_segments = [
        {
            "type": "json",
            "data": {
                "data": json.dumps(
                    {"meta": {"detail_1": {"title": f"card-{index}-" + "x" * 1800}}}
                )
            },
        }
        for index in range(3)
    ]
    timeline, _bot_call = await _load(
        monkeypatch,
        [
            _message(30, long_segments),
            _message(
                31,
                [
                    {
                        "type": "forward",
                        "data": _ActuallyBrokenForwardData({"content": []}),
                    }
                ],
            ),
            _message(32, [{"type": "text", "data": {"text": "later survives"}}]),
        ],
    )

    pending = timeline.get_pending("100")
    assert any("富消息不可用" in str(item["content"]) for item in pending)
    assert any("later survives" in str(item["content"]) for item in pending)
    long_content = str(pending[0]["content"])
    assert len(long_content) <= 2000
    assert "截断" in long_content
