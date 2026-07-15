from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

import kernel.router as router
from kernel.bus import PluginBus
from kernel.config import BotConfig
from kernel.types import PluginContext
from services.memory.timeline import GroupTimeline


class _MatcherStub:
    def __init__(self, callbacks: list[Any]) -> None:
        self._callbacks = callbacks
        self.finish = AsyncMock()

    def handle(self, *_args: object, **_kwargs: object):
        def decorate(fn: Any) -> Any:
            self._callbacks.append(fn)
            return fn

        return decorate


class _DriverStub:
    def on_startup(self, callback: Any) -> Any:
        return callback

    def on_shutdown(self, callback: Any) -> Any:
        return callback

    def on_bot_connect(self, callback: Any) -> Any:
        return callback

    def on_bot_disconnect(self, callback: Any) -> Any:
        return callback


def _config(presence_mode: str) -> BotConfig:
    group: dict[str, Any] = {
        "access": {"mode": "blacklist", "whitelist": [], "blacklist": []},
    }
    if presence_mode != "active":
        group["overrides"] = {"100": {"presence_mode": presence_mode}}
    return BotConfig.model_validate({"group": group})


def _context(*, presence_mode: str, muted: bool) -> PluginContext:
    ctx = PluginContext(config=_config(presence_mode))
    ctx.timeline = GroupTimeline()
    ctx.scheduler = MagicMock()
    ctx.scheduler.is_muted.return_value = muted
    ctx.llm_client = MagicMock()
    ctx.llm_client._session = MagicMock()
    ctx.llm_client._session.get = MagicMock(side_effect=AssertionError("no download in silent ingest"))
    ctx.llm_client._call = AsyncMock(side_effect=AssertionError("no LLM in silent ingest"))
    ctx.llm_client.chat = AsyncMock(side_effect=AssertionError("no LLM in silent ingest"))
    ctx.vision_client = MagicMock()
    ctx.vision_client.describe_image = AsyncMock(
        side_effect=AssertionError("no vision in silent ingest")
    )
    ctx.character_recognizer = MagicMock()
    ctx.character_recognizer.identify = AsyncMock(
        side_effect=AssertionError("no recognition in silent ingest")
    )
    ctx.image_cache = MagicMock()
    ctx.image_cache.save = AsyncMock(side_effect=AssertionError("no image cache download"))
    ctx.image_cache.save_bytes = AsyncMock(side_effect=AssertionError("no image cache download"))
    ctx.vision_enabled = True
    ctx.max_images_per_message = 5
    return ctx


def _install_group_handler(
    monkeypatch: pytest.MonkeyPatch,
    bus: PluginBus,
    ctx: PluginContext,
) -> Any:
    callbacks: list[Any] = []

    def matcher(*_args: object, **_kwargs: object) -> _MatcherStub:
        return _MatcherStub(callbacks)

    monkeypatch.setattr(router, "on_message", matcher)
    monkeypatch.setattr(router, "on_notice", matcher)
    monkeypatch.setattr(router, "claim_router_install", lambda _driver: None)
    monkeypatch.setattr(router, "get_driver", lambda: _DriverStub())
    monkeypatch.setattr(router, "_capture_research_group_event", lambda *_args: None)
    router.setup_routers(
        bus=bus,
        ctx=ctx,
        connection_pipeline=SimpleNamespace(
            on_connect=AsyncMock(),
            on_disconnect=AsyncMock(),
        ),
    )
    found = {getattr(fn, "__name__", ""): fn for fn in callbacks}
    assert "_collect_group_context" in found
    return found["_collect_group_context"]


def _event() -> GroupMessageEvent:
    card = json.dumps({"meta": {"detail_1": {"title": "silent card"}}})
    message = Message(
        [
            MessageSegment.text("current body"),
            MessageSegment(
                "image",
                {"file": "direct.png", "summary": "[direct image]", "url": "https://invalid.test/a.png"},
            ),
            MessageSegment.json(card),
            MessageSegment(
                "forward",
                {
                    "content": [
                        {
                            "sender": {"user_id": 77, "nickname": "Forwarder"},
                            "content": [
                                {"type": "text", "data": {"text": "inline body"}}
                            ],
                        }
                    ]
                },
            ),
        ]
    )
    event = GroupMessageEvent(  # type: ignore[arg-type]
        time=1,
        self_id=42,
        post_type="message",
        sub_type="normal",
        user_id=123,
        message_type="group",
        message_id=5,
        message=message,
        original_message=message,
        raw_message=str(message),
        font=0,
        sender={"user_id": 123, "nickname": "Alice"},
        to_me=False,
        group_id=100,
    )
    event.reply = SimpleNamespace(
        message_id=88,
        sender=SimpleNamespace(user_id="66", nickname="Parent"),
        message=Message(
            [
                MessageSegment("reply", {"id": "44"}),
                MessageSegment.text("parent body"),
            ]
        ),
    )
    return event


def _event_from_messages(
    message: Message,
    *,
    original_message: Message | None = None,
    reply: object | None = None,
) -> GroupMessageEvent:
    original = original_message if original_message is not None else message
    event = GroupMessageEvent(  # type: ignore[arg-type]
        time=1,
        self_id=42,
        post_type="message",
        sub_type="normal",
        user_id=123,
        message_type="group",
        message_id=6,
        message=message,
        original_message=original,
        raw_message=str(original),
        font=0,
        sender={"user_id": 123, "nickname": "Alice"},
        to_me=False,
        group_id=100,
    )
    # The adapter constructor normalizes original_message back to message;
    # assign the pre-strip copy afterward to reproduce NoneBot's matcher state.
    event.original_message = original
    event.reply = reply  # type: ignore[assignment]
    return event


def _side_effect_guard_bot() -> SimpleNamespace:
    return SimpleNamespace(
        self_id="42",
        get_msg=AsyncMock(side_effect=AssertionError("suppressed ingest must not fetch")),
        call_api=AsyncMock(side_effect=AssertionError("suppressed ingest must not call APIs")),
        send=AsyncMock(side_effect=AssertionError("suppressed ingest must not send")),
        send_group_msg=AsyncMock(side_effect=AssertionError("suppressed ingest must not send")),
        send_private_msg=AsyncMock(side_effect=AssertionError("suppressed ingest must not send")),
    )


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("presence_mode", "muted"),
    [
        pytest.param("silent_learn", False, id="silent-learn"),
        pytest.param("active", True, id="muted-active"),
    ],
)
async def test_suppressed_group_ingest_keeps_rich_structure_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    presence_mode: str,
    muted: bool,
) -> None:
    """An empty bus isolates old silent-safe plugins from renderer side effects."""
    bus = PluginBus()
    ctx = _context(presence_mode=presence_mode, muted=muted)
    handler = _install_group_handler(monkeypatch, bus, ctx)
    bot = SimpleNamespace(
        self_id="42",
        get_msg=AsyncMock(side_effect=AssertionError("silent ingest must not complete ancestry")),
        call_api=AsyncMock(side_effect=AssertionError("silent ingest must not call OneBot APIs")),
        send=AsyncMock(side_effect=AssertionError("silent ingest must not send")),
        send_group_msg=AsyncMock(side_effect=AssertionError("silent ingest must not send")),
        send_private_msg=AsyncMock(side_effect=AssertionError("silent ingest must not send")),
    )

    await handler(bot, _event())

    bot.get_msg.assert_not_awaited()
    bot.call_api.assert_not_awaited()
    bot.send.assert_not_awaited()
    bot.send_group_msg.assert_not_awaited()
    ctx.llm_client._call.assert_not_awaited()
    ctx.llm_client.chat.assert_not_awaited()
    ctx.vision_client.describe_image.assert_not_awaited()
    ctx.character_recognizer.identify.assert_not_awaited()
    ctx.image_cache.save.assert_not_awaited()
    ctx.image_cache.save_bytes.assert_not_awaited()

    pending = ctx.timeline.get_pending("100")
    assert len(pending) == 1
    text = _content_text(pending[0]["content"])
    assert "current body" in text
    assert "Parent" in text and "parent body" in text
    assert "direct image" in text
    assert "silent card" in text
    assert "Forwarder" in text and "inline body" in text


@pytest.mark.asyncio
async def test_off_group_does_not_enter_timeline_or_trigger_rich_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = PluginBus()
    ctx = _context(presence_mode="off", muted=False)
    handler = _install_group_handler(monkeypatch, bus, ctx)
    bot = SimpleNamespace(
        self_id="42",
        get_msg=AsyncMock(side_effect=AssertionError("off group must not fetch")),
        call_api=AsyncMock(side_effect=AssertionError("off group must not call APIs")),
        send=AsyncMock(side_effect=AssertionError("off group must not send")),
        send_group_msg=AsyncMock(side_effect=AssertionError("off group must not send")),
        send_private_msg=AsyncMock(side_effect=AssertionError("off group must not send")),
    )

    await handler(bot, _event())

    assert ctx.timeline.get_pending("100") == []
    bot.get_msg.assert_not_awaited()
    bot.call_api.assert_not_awaited()
    bot.send.assert_not_awaited()
    bot.send_group_msg.assert_not_awaited()
    ctx.llm_client._session.get.assert_not_called()
    ctx.llm_client._call.assert_not_awaited()
    ctx.llm_client.chat.assert_not_awaited()
    ctx.vision_client.describe_image.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["direct-rich", "immediate-reply-only"])
async def test_silent_learn_rich_only_message_enters_timeline_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    bus = PluginBus()
    ctx = _context(presence_mode="silent_learn", muted=False)
    handler = _install_group_handler(monkeypatch, bus, ctx)
    bot = _side_effect_guard_bot()

    if case == "direct-rich":
        card = json.dumps({"meta": {"detail_1": {"title": "rich-only card"}}})
        message = Message(
            [
                MessageSegment(
                    "image",
                    {
                        "file": "rich-only.png",
                        "summary": "[rich-only image]",
                        "url": "https://invalid.test/rich-only.png",
                    },
                ),
                MessageSegment.json(card),
                MessageSegment(
                    "forward",
                    {
                        "content": [
                            {
                                "sender": {"user_id": 77, "nickname": "RichForwarder"},
                                "content": [
                                    {"type": "text", "data": {"text": "rich-only forward"}}
                                ],
                            }
                        ]
                    },
                ),
            ]
        )
        event = _event_from_messages(message)
        expected = ("rich-only image", "rich-only card", "RichForwarder", "rich-only forward")
    else:
        reply = SimpleNamespace(
            message_id=88,
            sender=SimpleNamespace(user_id="66", nickname="ReplyParent"),
            message=Message([MessageSegment.text("reply-only body")]),
        )
        event = _event_from_messages(Message(), reply=reply)
        expected = ("ReplyParent", "reply-only body")

    await handler(bot, event)

    pending = ctx.timeline.get_pending("100")
    assert len(pending) == 1
    text = _content_text(pending[0]["content"])
    assert all(fragment in text for fragment in expected)
    bot.get_msg.assert_not_awaited()
    bot.call_api.assert_not_awaited()
    bot.send.assert_not_awaited()
    bot.send_group_msg.assert_not_awaited()
    bot.send_private_msg.assert_not_awaited()
    ctx.llm_client._session.get.assert_not_called()
    ctx.llm_client._call.assert_not_awaited()
    ctx.llm_client.chat.assert_not_awaited()
    ctx.vision_client.describe_image.assert_not_awaited()
    ctx.character_recognizer.identify.assert_not_awaited()
    ctx.image_cache.save.assert_not_awaited()
    ctx.image_cache.save_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_silent_semantic_restore_replaces_current_body_not_matching_quote_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = PluginBus()
    ctx = _context(presence_mode="silent_learn", muted=False)
    ctx.bot_nicknames = ["emu"]
    handler = _install_group_handler(monkeypatch, bus, ctx)
    bot = _side_effect_guard_bot()
    reply = SimpleNamespace(
        message_id=88,
        sender=SimpleNamespace(user_id="66", nickname="Parent"),
        message=Message([MessageSegment.text("。")]),
    )
    event = _event_from_messages(
        Message([MessageSegment.text("。")]),
        original_message=Message([MessageSegment.text("emu。")]),
        reply=reply,
    )

    await handler(bot, event)

    pending = ctx.timeline.get_pending("100")
    assert len(pending) == 1
    text = _content_text(pending[0]["content"])
    quote_end = text.index("[/QUOTED_MSG]")
    quoted = text[:quote_end]
    current = text[quote_end + len("[/QUOTED_MSG]"):].strip()
    assert "\n。\n" in quoted
    assert "emu。" not in quoted
    assert current == "emu。"
    bot.get_msg.assert_not_awaited()
    bot.call_api.assert_not_awaited()
    bot.send.assert_not_awaited()
    ctx.llm_client._session.get.assert_not_called()
    ctx.vision_client.describe_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_silent_semantic_restore_does_not_rewrite_later_rich_punctuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = PluginBus()
    ctx = _context(presence_mode="silent_learn", muted=False)
    ctx.bot_nicknames = ["emu"]
    handler = _install_group_handler(monkeypatch, bus, ctx)
    bot = _side_effect_guard_bot()
    card = json.dumps({"meta": {"detail_1": {"title": "。"}}})
    forward = MessageSegment(
        "forward",
        {
            "content": [
                {
                    "sender": {"user_id": 77, "nickname": "Rich"},
                    "content": [{"type": "text", "data": {"text": "。"}}],
                }
            ]
        },
    )
    event = _event_from_messages(
        Message([MessageSegment.text("。"), MessageSegment.json(card), forward]),
        original_message=Message(
            [MessageSegment.text("emu。"), MessageSegment.json(card), forward]
        ),
    )

    await handler(bot, event)

    pending = ctx.timeline.get_pending("100")
    assert len(pending) == 1
    text = _content_text(pending[0]["content"])
    assert text.startswith("emu。[卡片: 。]")
    assert "Rich(77): 。" in text
    assert text.count("emu。") == 1
    bot.get_msg.assert_not_awaited()
    bot.call_api.assert_not_awaited()
    ctx.llm_client._session.get.assert_not_called()
    ctx.vision_client.describe_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_silent_nickname_prefix_restore_preserves_text_image_text_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = PluginBus()
    ctx = _context(presence_mode="silent_learn", muted=False)
    ctx.bot_nicknames = ["emu"]
    handler = _install_group_handler(monkeypatch, bus, ctx)
    bot = _side_effect_guard_bot()
    image = MessageSegment(
        "image",
        {"file": "inline.png", "summary": "[image]", "url": "https://invalid.test/a.png"},
    )
    event = _event_from_messages(
        Message([MessageSegment.text("hi "), image, MessageSegment.text("after")]),
        original_message=Message(
            [MessageSegment.text("emu hi "), image, MessageSegment.text("after")]
        ),
    )

    await handler(bot, event)

    pending = ctx.timeline.get_pending("100")
    assert len(pending) == 1
    text = _content_text(pending[0]["content"])
    assert text == "emu hi «image»after"
    bot.get_msg.assert_not_awaited()
    bot.call_api.assert_not_awaited()
    ctx.llm_client._session.get.assert_not_called()
    ctx.vision_client.describe_image.assert_not_awaited()
    ctx.image_cache.save.assert_not_awaited()
