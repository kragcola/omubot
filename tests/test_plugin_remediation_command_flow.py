"""RED contracts for command fast-path ordering and slash handling."""

from __future__ import annotations

from itertools import permutations
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

import kernel.router as router
from kernel.bus import PluginBus
from kernel.config import BotConfig
from kernel.types import AmadeusPlugin, Command, MessageContext, PluginContext
from services.command import CommandDispatcher


class _HookPlugin(AmadeusPlugin):
    name = "side_effect_hook"
    priority = 1

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events
        self.calls: list[str] = []

    async def on_message(self, ctx: MessageContext) -> bool:
        self.calls.append(str(ctx.content))
        self.events.append("hook")
        return False


class _CommandPlugin(AmadeusPlugin):
    name = "command_owner"
    priority = 20

    def __init__(self, events: list[str], name: str = "debug") -> None:
        super().__init__()
        self.name = name
        self.events = events
        self.handler = AsyncMock(side_effect=self._handle)

    def register_commands(self) -> list[Command]:
        return [Command(name="debug", handler=self.handler)]

    async def _handle(self, _ctx: object) -> None:
        self.events.append("command")


class _MatcherStub:
    def __init__(self, callbacks: list[tuple[object, _MatcherStub]]) -> None:
        self.finish = AsyncMock()
        self._callbacks = callbacks

    def handle(self, *_args: object, **_kwargs: object):
        def decorate(fn):
            self._callbacks.append((fn, self))
            return fn

        return decorate


class _DriverStub:
    def on_startup(self, callback):
        return callback

    def on_shutdown(self, callback):
        return callback

    def on_bot_connect(self, callback):
        return callback

    def on_bot_disconnect(self, callback):
        return callback


def _config(*, presence_mode: str = "active", blocked: bool = False) -> BotConfig:
    access = {
        "mode": "whitelist" if blocked else "blacklist",
        "whitelist": [],
        "blacklist": [],
    }
    values: dict[str, object] = {"group": {"access": access}}
    if presence_mode != "active":
        values["group"] = {
            **values["group"],  # type: ignore[typeddict-item]
            "overrides": {"100": {"presence_mode": presence_mode}},
        }
    return BotConfig.model_validate(values)


def _context(
    bus: PluginBus,
    *,
    presence_mode: str = "active",
    muted: bool = False,
    blocked: bool = False,
) -> PluginContext:
    config = _config(presence_mode=presence_mode, blocked=blocked)
    ctx = PluginContext(config=config)
    ctx.group_config = config.group  # type: ignore[reportAttributeAccessIssue]
    ctx.command_dispatcher = CommandDispatcher(bus)

    scheduler = MagicMock()
    scheduler.is_muted.return_value = muted
    scheduler.notify = MagicMock()
    scheduler.cancel_debounce = MagicMock()
    scheduler._humanizer_runtime.return_value = {}
    ctx.scheduler = scheduler

    ctx.timeline = MagicMock()
    ctx.timeline.get_recent.return_value = []
    ctx.timeline.get_turns.return_value = []
    ctx.humanizer = MagicMock()
    ctx.humanizer.delay = AsyncMock()
    ctx.short_term = MagicMock()
    ctx.msg_log = MagicMock()
    ctx.bot_pair_guard = MagicMock()
    ctx.bot_pair_guard.is_suppressed.return_value = False
    ctx.bot_pair_guard.record_inbound.return_value = True
    ctx.runtime_state = MagicMock()
    ctx.block_trace_store = MagicMock()
    ctx.llm_client = MagicMock()
    ctx.llm_client._call = AsyncMock(return_value={"text": "LLM"})
    ctx.llm_client.chat = AsyncMock(return_value="LLM")
    ctx.persona_runtime = MagicMock()
    ctx.persona_runtime.identity_snapshot.return_value = SimpleNamespace(
        id="bot",
        name="bot",
        personality="p",
        proactive="on",
    )
    ctx.message_coalescer = None
    return ctx


def _install_router(
    monkeypatch: pytest.MonkeyPatch,
    bus: PluginBus,
    ctx: PluginContext,
) -> dict[str, tuple[Any, _MatcherStub]]:
    callbacks: list[tuple[Any, _MatcherStub]] = []

    def make_matcher(*_args: object, **_kwargs: object) -> _MatcherStub:
        return _MatcherStub(callbacks)

    monkeypatch.setattr(router, "on_message", make_matcher)
    monkeypatch.setattr(router, "claim_router_install", lambda _driver: None)
    monkeypatch.setattr(router, "get_driver", lambda: _DriverStub())
    router.setup_routers(bus=bus, ctx=ctx, runtime=None)

    found = {getattr(fn, "__name__", ""): (fn, matcher) for fn, matcher in callbacks}
    assert "_collect_group_context" in found
    assert "_handle_private_chat" in found
    return found


def _group_event(text: str, *, segments: list[MessageSegment] | None = None):
    message = Message(segments if segments is not None else [MessageSegment.text(text)])
    return router.GroupMessageEvent(  # type: ignore[arg-type]
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
        sender={"user_id": 123, "nickname": "Alice"},  # type: ignore[arg-type]
        to_me=False,
        group_id=100,
    )


def _private_event(text: str):
    message = Message([MessageSegment.text(text)])
    return router.MessageEvent(  # type: ignore[arg-type]
        time=1,
        self_id=42,
        post_type="message",
        sub_type="normal",
        user_id=123,
        message_type="private",
        message_id=6,
        message=message,
        original_message=message,
        raw_message=str(message),
        font=0,
        sender={"user_id": 123, "nickname": "Alice"},  # type: ignore[arg-type]
        to_me=True,
    )


def _bot() -> SimpleNamespace:
    return SimpleNamespace(
        self_id="42",
        send=AsyncMock(),
        send_group_msg=AsyncMock(),
        send_private_msg=AsyncMock(),
        get_group_member_info=AsyncMock(return_value={"nickname": "Alice"}),
        get_login_info=AsyncMock(return_value={"user_id": 42}),
    )


@pytest.mark.asyncio
async def test_active_group_command_runs_before_side_effect_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    bus = PluginBus()
    command = _CommandPlugin(events)
    hook = _HookPlugin(events)
    bus.register(command)
    bus.register(hook)
    ctx = _context(bus)
    callbacks = _install_router(monkeypatch, bus, ctx)
    bot = _bot()

    await callbacks["_collect_group_context"][0](bot, _group_event("/debug"))

    assert events == ["command"]
    hook_calls = hook.calls
    assert hook_calls == []
    command.handler.assert_awaited_once()
    assert ctx.llm_client._call.await_count == 0
    assert ctx.llm_client.chat.await_count == 0


@pytest.mark.asyncio
async def test_unknown_group_slash_is_consumed_without_hook_or_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = PluginBus()
    hook = _HookPlugin([])
    bus.register(hook)
    ctx = _context(bus)
    callbacks = _install_router(monkeypatch, bus, ctx)
    bot = _bot()

    await callbacks["_collect_group_context"][0](bot, _group_event("/unknown"))

    assert hook.calls == []
    assert ctx.llm_client._call.await_count == 0
    assert ctx.llm_client.chat.await_count == 0
    outbound = bot.send.await_args_list + bot.send_group_msg.await_args_list
    assert len(outbound) == 1
    assert "/unknown" in str(outbound[0])


@pytest.mark.asyncio
async def test_unknown_private_slash_is_consumed_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = PluginBus()
    ctx = _context(bus)
    callbacks = _install_router(monkeypatch, bus, ctx)
    bot = _bot()
    private_matcher = callbacks["_handle_private_chat"][1]

    await callbacks["_handle_private_chat"][0](bot, _private_event("/unknown"))

    assert ctx.llm_client._call.await_count == 0
    assert ctx.llm_client.chat.await_count == 0
    private_matcher.finish.assert_awaited_once()
    assert private_matcher.finish.await_args is not None
    assert "/unknown" in str(private_matcher.finish.await_args.args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("presence_mode", "muted", "blocked"),
    [
        pytest.param("silent_learn", False, False, id="silent-learn"),
        pytest.param("off", False, False, id="off"),
        pytest.param("active", True, False, id="muted"),
        pytest.param("active", False, True, id="access-blocked"),
    ],
)
async def test_group_command_guards_keep_suppressed_groups_zero_outbound(
    monkeypatch: pytest.MonkeyPatch,
    presence_mode: str,
    muted: bool,
    blocked: bool,
) -> None:
    events: list[str] = []
    bus = PluginBus()
    command = _CommandPlugin(events)
    hook = _HookPlugin(events)
    bus.register(command)
    bus.register(hook)
    ctx = _context(
        bus,
        presence_mode=presence_mode,
        muted=muted,
        blocked=blocked,
    )
    callbacks = _install_router(monkeypatch, bus, ctx)
    bot = _bot()

    await callbacks["_collect_group_context"][0](bot, _group_event("/debug"))

    assert events == []
    command.handler.assert_not_awaited()
    assert hook.calls == []
    assert ctx.llm_client._call.await_count == 0
    assert ctx.llm_client.chat.await_count == 0
    assert bot.send.await_count == 0
    assert bot.send_group_msg.await_count == 0


@pytest.mark.parametrize(
    "ordered_segments",
    list(permutations(
        (
            MessageSegment.text("/debug save"),
            MessageSegment.image("https://example.invalid/image.jpg"),
            MessageSegment.reply(99),
        )
    )),
)
def test_group_command_extraction_scans_text_after_any_segment_order(
    ordered_segments: tuple[MessageSegment, ...],
) -> None:
    message = Message(list(ordered_segments))

    assert router._extract_group_command_text(message, "42") == "/debug save"
