from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kernel.types import Command
from services.command import CommandDispatcher


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
