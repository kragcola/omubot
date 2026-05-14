from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from kernel.router import (
    _extract_group_command_text,
    _has_recent_assistant_reply,
    _is_directed_followup_text,
    _last_assistant_replied_to_user,
)
from kernel.types import Command
from services.command import CommandDispatcher
from services.memory.timeline import GroupTimeline


class _Bus:
    def __init__(self, commands: list[Command]) -> None:
        self._commands = commands

    def collect_commands(self) -> list[Command]:
        return list(self._commands)


@pytest.fixture
def plugin_ctx():
    return SimpleNamespace(config=SimpleNamespace(admins={}))


@pytest.fixture
def bot():
    inst = SimpleNamespace()
    inst.send = AsyncMock()
    return inst


@pytest.fixture
def event():
    return SimpleNamespace()


def _seg(seg_type: str, **data: str) -> SimpleNamespace:
    return SimpleNamespace(type=seg_type, data=data)


async def test_dispatch_requires_leading_slash(bot, event, plugin_ctx) -> None:
    handler = AsyncMock()
    dispatcher = CommandDispatcher(_Bus([Command(name="debug", handler=handler)]))

    matched = await dispatcher.dispatch(
        bot,
        event,
        "看这个 /debug",
        is_private=False,
        user_id="1",
        group_id="123",
        plugin_ctx=plugin_ctx,
    )

    assert matched is False
    handler.assert_not_awaited()


async def test_dispatch_accepts_leading_slash_after_whitespace(bot, event, plugin_ctx) -> None:
    handler = AsyncMock()
    dispatcher = CommandDispatcher(_Bus([Command(name="debug", handler=handler)]))

    matched = await dispatcher.dispatch(
        bot,
        event,
        "   /debug hello",
        is_private=False,
        user_id="1",
        group_id="123",
        plugin_ctx=plugin_ctx,
    )

    assert matched is True
    handler.assert_awaited_once()


async def test_dispatch_tolerates_trailing_punctuation_on_top_level_command(bot, event, plugin_ctx) -> None:
    handler = AsyncMock()
    dispatcher = CommandDispatcher(_Bus([Command(name="吃什么", handler=handler)]))

    matched = await dispatcher.dispatch(
        bot,
        event,
        "/吃什么。",
        is_private=False,
        user_id="1",
        group_id="123",
        plugin_ctx=plugin_ctx,
    )

    assert matched is True
    handler.assert_awaited_once()


def test_group_command_text_allows_naked_command() -> None:
    msg = [_seg("text", text="/吃什么。")]

    assert _extract_group_command_text(cast(Any, msg), "384801062") == "/吃什么。"


def test_group_command_text_allows_bot_at_command() -> None:
    msg = [_seg("at", qq="384801062"), _seg("text", text=" /吃什么")]

    assert _extract_group_command_text(cast(Any, msg), "384801062") == "/吃什么"


def test_group_command_text_allows_textual_at_bot_nickname_command() -> None:
    msg = [_seg("text", text="@emu不吃小杯面 /吃什么")]

    assert _extract_group_command_text(cast(Any, msg), "384801062", ["emu", "姆姆"]) == "/吃什么"


def test_group_command_text_allows_textual_at_bot_with_displayed_self_id() -> None:
    msg = [_seg("text", text="@emu不吃小杯面 (384801062) /吃什么")]

    assert _extract_group_command_text(cast(Any, msg), "384801062", ["emu", "姆姆"]) == "/吃什么"


def test_group_command_text_allows_bot_nickname_command() -> None:
    msg = [_seg("text", text="emu /吃什么")]

    assert _extract_group_command_text(cast(Any, msg), "384801062", ["emu", "姆姆"]) == "/吃什么"


def test_group_command_text_allows_chinese_bot_nickname_command() -> None:
    msg = [_seg("text", text="姆姆 /吃什么")]

    assert _extract_group_command_text(cast(Any, msg), "384801062", ["emu", "姆姆"]) == "/吃什么"


def test_group_command_text_rejects_other_at_command() -> None:
    msg = [_seg("at", qq="2459515872"), _seg("text", text=" /吃什么 两块钱以内的")]

    assert _extract_group_command_text(cast(Any, msg), "384801062") is None


def test_group_command_text_rejects_textual_other_at_command() -> None:
    msg = [_seg("text", text="@小明 /吃什么")]

    assert _extract_group_command_text(cast(Any, msg), "384801062", ["emu", "姆姆"]) is None


def test_group_command_text_rejects_textual_other_at_with_displayed_qq() -> None:
    msg = [_seg("text", text="@姆姆 (2459515872) /吃什么")]

    assert _extract_group_command_text(cast(Any, msg), "384801062", ["emu", "姆姆"]) is None


def test_group_command_text_rejects_mid_sentence_command() -> None:
    msg = [_seg("text", text="我刚刚说 /吃什么 不是命令")]

    assert _extract_group_command_text(cast(Any, msg), "384801062", ["emu", "姆姆"]) is None


def test_directed_followup_text_matches_short_pointing_question() -> None:
    assert _is_directed_followup_text("我也能去吗")
    assert _is_directed_followup_text("我可以吗")
    assert _is_directed_followup_text("我也可以吗")
    assert _is_directed_followup_text("可以吗")
    assert _is_directed_followup_text("带我吗")
    assert not _is_directed_followup_text("我也能去吗，顺便帮我看看晚饭吃什么")
    assert not _is_directed_followup_text("好的")
    assert not _is_directed_followup_text("嗯")
    assert not _is_directed_followup_text("可以吧那你去")


def test_directed_followup_requires_recent_assistant_reply() -> None:
    timeline = GroupTimeline()

    assert not _has_recent_assistant_reply(timeline, "123", within_s=180.0)

    timeline.add("123", role="assistant", content="可以一起去呀")

    assert _has_recent_assistant_reply(timeline, "123", within_s=180.0)


def test_last_assistant_replied_to_current_user_only() -> None:
    timeline = GroupTimeline()
    timeline.add("123", role="user", content="讲讲今天", speaker="Alice(111)")
    timeline.add("123", role="assistant", content="今天很暖和")

    assert _last_assistant_replied_to_user(timeline, "123", "111", within_s=180.0)
    assert not _last_assistant_replied_to_user(timeline, "123", "222", within_s=180.0)


def test_last_assistant_replied_to_user_rejects_multi_user_turn() -> None:
    timeline = GroupTimeline()
    timeline.add("123", role="user", content="讲讲今天", speaker="Alice(111)")
    timeline.add("123", role="user", content="我也听", speaker="Bob(222)")
    timeline.add("123", role="assistant", content="今天很暖和")

    assert not _last_assistant_replied_to_user(timeline, "123", "111", within_s=180.0)
    assert not _last_assistant_replied_to_user(timeline, "123", "222", within_s=180.0)


def test_last_assistant_replied_to_user_ignores_trigger_marker() -> None:
    timeline = GroupTimeline()
    timeline.add("123", role="user", content="讲讲今天", speaker="Alice(111)")
    timeline.add_pending_trigger("123", reason="用户追问上一轮回复", target_user_id="111")
    timeline.add("123", role="assistant", content="今天很暖和")

    assert _last_assistant_replied_to_user(timeline, "123", "111", within_s=180.0)


async def test_dispatch_tolerates_trailing_punctuation_on_english_command(bot, event, plugin_ctx) -> None:
    handler = AsyncMock()
    dispatcher = CommandDispatcher(_Bus([Command(name="plugins", handler=handler)]))

    matched = await dispatcher.dispatch(
        bot,
        event,
        "/plugins，",
        is_private=False,
        user_id="1",
        group_id="123",
        plugin_ctx=plugin_ctx,
    )

    assert matched is True
    handler.assert_awaited_once()


async def test_dispatch_tolerates_trailing_punctuation_before_args(bot, event, plugin_ctx) -> None:
    handler = AsyncMock()
    dispatcher = CommandDispatcher(_Bus([Command(name="debug", handler=handler)]))

    matched = await dispatcher.dispatch(
        bot,
        event,
        "   /debug。 hello",
        is_private=False,
        user_id="1",
        group_id="123",
        plugin_ctx=plugin_ctx,
    )

    assert matched is True
    handler.assert_awaited_once()
    ctx = handler.await_args.args[0]
    assert ctx.args == "hello"


async def test_dispatch_tolerates_trailing_punctuation_on_subcommand(bot, event, plugin_ctx) -> None:
    root_handler = AsyncMock()
    like_handler = AsyncMock()
    dispatcher = CommandDispatcher(
        _Bus(
            [
                Command(
                    name="food",
                    handler=root_handler,
                    sub_commands=[
                        Command(name="like", handler=like_handler),
                    ],
                )
            ]
        )
    )

    matched = await dispatcher.dispatch(
        bot,
        event,
        "/food like。 辣的。",
        is_private=False,
        user_id="1",
        group_id="123",
        plugin_ctx=plugin_ctx,
    )

    assert matched is True
    like_handler.assert_awaited_once()
    root_handler.assert_not_awaited()
    ctx = like_handler.await_args.args[0]
    assert ctx.args == "辣的。"


async def test_dispatch_tolerates_repeated_trailing_punctuation_on_subcommand(bot, event, plugin_ctx) -> None:
    like_handler = AsyncMock()
    dispatcher = CommandDispatcher(
        _Bus(
            [
                Command(
                    name="food",
                    handler=AsyncMock(),
                    sub_commands=[Command(name="like", handler=like_handler)],
                )
            ]
        )
    )

    matched = await dispatcher.dispatch(
        bot,
        event,
        "/food like。。。 辣的",
        is_private=False,
        user_id="1",
        group_id="123",
        plugin_ctx=plugin_ctx,
    )

    assert matched is True
    like_handler.assert_awaited_once()


async def test_unknown_command_with_trailing_punctuation_still_does_not_match(bot, event, plugin_ctx) -> None:
    handler = AsyncMock()
    dispatcher = CommandDispatcher(_Bus([Command(name="debug", handler=handler)]))

    matched = await dispatcher.dispatch(
        bot,
        event,
        "/unknown。",
        is_private=False,
        user_id="1",
        group_id="123",
        plugin_ctx=plugin_ctx,
    )

    assert matched is False
    handler.assert_not_awaited()


async def test_dispatch_handler_failure_replies_to_user(bot, event, plugin_ctx) -> None:
    async def _boom(_ctx) -> None:
        raise RuntimeError("boom")

    dispatcher = CommandDispatcher(_Bus([Command(name="debug", handler=_boom)]))

    matched = await dispatcher.dispatch(
        bot,
        event,
        "/debug",
        is_private=False,
        user_id="1",
        group_id="123",
        plugin_ctx=plugin_ctx,
    )

    assert matched is True
    bot.send.assert_awaited_once()
    sent_message = bot.send.await_args.args[1]
    assert "指令执行失败" in str(sent_message)
