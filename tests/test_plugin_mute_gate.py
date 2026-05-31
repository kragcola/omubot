from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from kernel.types import MessageContext
from plugins.echo.plugin import EchoConfig, EchoPlugin
from plugins.element_detector.plugin import ElementDetector, ElementDetectorPlugin, ElementMatch
from plugins.food.plugin import FoodPlugin


class _TimelineStub:
    def add(self, *args, **kwargs) -> None:
        del args, kwargs


class _SchedulerStub:
    def __init__(self, *, muted: bool) -> None:
        self.muted = muted

    def cancel_debounce(self, group_id: str) -> None:
        del group_id

    def is_muted(self, group_id: str) -> bool:
        del group_id
        return self.muted

    def _humanizer_runtime(self, group_id: str) -> dict[str, object]:
        return {"group_id": group_id, "register": None, "slot": None, "mood": None}


def _ctx(*, plain_text: str, raw_message: dict[str, object], bot: object | None = None) -> MessageContext:
    return MessageContext(
        session_id="group_100",
        group_id="100",
        user_id="u1",
        content=plain_text,
        raw_message=raw_message,
        bot=bot,
        nickname="Alice",
        message_id=1,
    )


@pytest.mark.asyncio
async def test_echo_plugin_skips_send_when_group_muted() -> None:
    plugin = EchoPlugin()
    plugin._config = EchoConfig(enabled=True, ignore_command_messages=True)
    plugin._tracker = cast(Any, SimpleNamespace(process=lambda *_args: "复读一下"))
    plugin._humanizer = SimpleNamespace(delay=AsyncMock())
    plugin._scheduler = _SchedulerStub(muted=True)
    plugin._timeline = _TimelineStub()
    bot = SimpleNamespace(send_group_msg=AsyncMock())

    consumed = await plugin.on_message(_ctx(
        plain_text="复读一下",
        raw_message={"plain_text": "复读一下", "echo_key": "复读一下", "segments": "复读一下"},
        bot=bot,
    ))

    assert consumed is True
    bot.send_group_msg.assert_not_called()
    plugin._humanizer.delay.assert_not_called()


@pytest.mark.asyncio
async def test_element_detector_skips_send_when_group_muted() -> None:
    plugin = ElementDetectorPlugin()
    plugin._detector = cast(
        ElementDetector,
        SimpleNamespace(detect=lambda *_args, **_kwargs: ElementMatch("收到", False)),
    )
    plugin._humanizer = SimpleNamespace(delay=AsyncMock())
    plugin._scheduler = _SchedulerStub(muted=True)
    plugin._timeline = _TimelineStub()
    bot = SimpleNamespace(send_group_msg=AsyncMock())

    consumed = await plugin.on_message(_ctx(
        plain_text="你在吗",
        raw_message={"plain_text": "你在吗"},
        bot=bot,
    ))

    assert consumed is True
    bot.send_group_msg.assert_not_called()
    plugin._humanizer.delay.assert_not_called()


@pytest.mark.asyncio
async def test_food_feedback_recommend_skips_send_when_group_muted() -> None:
    plugin = FoodPlugin()
    plugin._ctx = cast(Any, SimpleNamespace(scheduler=_SchedulerStub(muted=True)))
    plugin._do_recommend = AsyncMock(return_value="麻辣烫")
    plugin._record_served_safe = AsyncMock()
    bot = SimpleNamespace(send_group_msg=AsyncMock())

    await plugin._feedback_recommend(bot, "100", "u1", "换一个", message_id=1)

    bot.send_group_msg.assert_not_called()
    plugin._do_recommend.assert_not_awaited()
