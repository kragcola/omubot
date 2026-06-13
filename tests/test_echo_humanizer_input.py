from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from kernel.types import MessageContext
from plugins.echo import EchoConfig, EchoPlugin, _visible_text_for_humanizer
from plugins.element_detector import ElementDetector, ElementDetectorPlugin, ElementMatch


class _HumanizerSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def delay(self, text: str, **kwargs: object) -> None:
        payload: dict[str, object] = {"text": text}
        payload.update(kwargs)
        self.calls.append(payload)


class _SchedulerStub:
    def __init__(self, runtime: dict[str, object]) -> None:
        self.runtime = runtime
        self.cancelled: list[str] = []

    def cancel_debounce(self, group_id: str) -> None:
        self.cancelled.append(group_id)

    def is_muted(self, group_id: str) -> bool:
        return False

    def _humanizer_runtime(self, group_id: str) -> dict[str, object]:
        return {"group_id": group_id, **self.runtime}


class _TimelineStub:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def add(self, group_id: str, **kwargs: object) -> None:
        self.rows.append({"group_id": group_id, **kwargs})


def _message_context(
    *,
    plain_text: str,
    raw_message: dict[str, object],
    bot: object | None = None,
) -> MessageContext:
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


def test_visible_text_for_humanizer_keeps_plain_text() -> None:
    assert _visible_text_for_humanizer("哈哈哈好搞笑") == "哈哈哈好搞笑"


def test_visible_text_for_humanizer_compresses_image_marker() -> None:
    assert _visible_text_for_humanizer("看这个[image:1:abc123def]") == "看这个__"


def test_visible_text_for_humanizer_compresses_multiple_markers() -> None:
    assert _visible_text_for_humanizer("[face:178][image:x:y]好的") == "____好的"


def test_visible_text_for_humanizer_leaves_break_text() -> None:
    assert _visible_text_for_humanizer("打断复读！") == "打断复读！"


@pytest.mark.asyncio
async def test_echo_plugin_uses_visible_text_and_scheduler_runtime() -> None:
    plugin = EchoPlugin()
    plugin._config = EchoConfig(enabled=True, ignore_command_messages=True)
    plugin._tracker = cast(Any, SimpleNamespace(process=lambda *_args: "看这个[image:1:abc123def]"))
    plugin._humanizer = _HumanizerSpy()
    plugin._scheduler = _SchedulerStub({
        "register": {"label": "playful"},
        "slot": {"energy": 0.4},
        "mood": {"label": "playful", "energy": 0.6},
    })
    plugin._timeline = _TimelineStub()
    bot = SimpleNamespace(send_group_msg=AsyncMock())
    segments = [{"type": "text", "data": {"text": "看这个"}}]

    consumed = await plugin.on_message(_message_context(
        plain_text="看这个",
        raw_message={
            "plain_text": "看这个",
            "echo_key": "看这个[image:1:abc123def]",
            "segments": segments,
        },
        bot=bot,
    ))

    assert consumed is True
    assert plugin._humanizer.calls == [{
        "text": "看这个__",
        "group_id": "100",
        "register": {"label": "playful"},
        "slot": {"energy": 0.4},
        "mood": {"label": "playful", "energy": 0.6},
    }]
    bot.send_group_msg.assert_awaited_once_with(group_id=100, message=segments)


@pytest.mark.asyncio
async def test_echo_plugin_repeats_original_segments_over_stripped() -> None:
    """When the router supplies pre-strip ``echo_segments`` (nickname vocative),
    echo must repeat those, not the adapter-stripped ``segments`` (bare "。")."""
    plugin = EchoPlugin()
    plugin._config = EchoConfig(enabled=True, ignore_command_messages=True)
    plugin._tracker = cast(Any, SimpleNamespace(process=lambda *_args: "姆。"))
    plugin._humanizer = _HumanizerSpy()
    plugin._scheduler = _SchedulerStub({"register": None, "slot": None, "mood": None})
    plugin._timeline = _TimelineStub()
    bot = SimpleNamespace(send_group_msg=AsyncMock())
    original = [{"type": "text", "data": {"text": "姆。"}}]
    stripped = [{"type": "text", "data": {"text": "。"}}]

    consumed = await plugin.on_message(_message_context(
        plain_text="姆。",
        raw_message={
            "plain_text": "姆。",
            "echo_key": "姆。",
            "segments": stripped,
            "echo_segments": original,
        },
        bot=bot,
    ))

    assert consumed is True
    bot.send_group_msg.assert_awaited_once_with(group_id=100, message=original)


@pytest.mark.asyncio
async def test_echo_plugin_falls_back_to_segments_without_original() -> None:
    """Back-compat: when no ``echo_segments`` is supplied, echo uses ``segments``."""
    plugin = EchoPlugin()
    plugin._config = EchoConfig(enabled=True, ignore_command_messages=True)
    plugin._tracker = cast(Any, SimpleNamespace(process=lambda *_args: "哈哈"))
    plugin._humanizer = _HumanizerSpy()
    plugin._scheduler = _SchedulerStub({"register": None, "slot": None, "mood": None})
    plugin._timeline = _TimelineStub()
    bot = SimpleNamespace(send_group_msg=AsyncMock())
    segments = [{"type": "text", "data": {"text": "哈哈"}}]

    consumed = await plugin.on_message(_message_context(
        plain_text="哈哈",
        raw_message={"plain_text": "哈哈", "echo_key": "哈哈", "segments": segments},
        bot=bot,
    ))

    assert consumed is True
    bot.send_group_msg.assert_awaited_once_with(group_id=100, message=segments)
    plugin = ElementDetectorPlugin()
    plugin._detector = cast(
        ElementDetector,
        SimpleNamespace(detect=lambda *_args, **_kwargs: ElementMatch("收到啦", False)),
    )
    plugin._humanizer = _HumanizerSpy()
    plugin._scheduler = _SchedulerStub({
        "register": {"label": "playful"},
        "slot": {"energy": 0.4},
        "mood": {"label": "playful"},
    })
    plugin._timeline = _TimelineStub()
    bot = SimpleNamespace(send_group_msg=AsyncMock())

    consumed = await plugin.on_message(_message_context(
        plain_text="你在吗",
        raw_message={"plain_text": "你在吗"},
        bot=bot,
    ))

    assert consumed is True
    assert plugin._humanizer.calls == [{
        "text": "收到啦",
        "group_id": "100",
        "register": {"label": "playful"},
        "slot": {"energy": 0.4},
        "mood": {"label": "playful"},
    }]
    bot.send_group_msg.assert_awaited_once_with(group_id=100, message="收到啦")


@pytest.mark.asyncio
async def test_element_detector_passes_runtime_for_llm_reply() -> None:
    plugin = ElementDetectorPlugin()
    plugin._detector = cast(
        ElementDetector,
        SimpleNamespace(detect=lambda *_args, **_kwargs: ElementMatch("生成一条回复", True)),
    )
    plugin._humanizer = _HumanizerSpy()
    plugin._scheduler = _SchedulerStub({
        "register": {"label": "playful"},
        "slot": {"energy": 0.5},
        "mood": {"label": "playful"},
    })
    plugin._timeline = _TimelineStub()
    plugin._llm_client = SimpleNamespace(_call=AsyncMock(return_value={"text": "来啦"}))
    plugin._identity = SimpleNamespace(name="Omubot")
    bot = SimpleNamespace(send_group_msg=AsyncMock())

    consumed = await plugin.on_message(_message_context(
        plain_text="快接这个梗",
        raw_message={"plain_text": "快接这个梗"},
        bot=bot,
    ))

    assert consumed is True
    assert plugin._humanizer.calls == [{
        "text": "来啦",
        "group_id": "100",
        "register": {"label": "playful"},
        "slot": {"energy": 0.5},
        "mood": {"label": "playful"},
    }]
    bot.send_group_msg.assert_awaited_once_with(group_id=100, message="来啦")
