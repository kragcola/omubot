from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from nonebot.adapters.onebot.v11 import ActionFailed, Message, MessageSegment

import kernel.router as router
from kernel.config import BotConfig, GroupConfig
from kernel.types import PluginContext
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot
from services.scheduler import GroupChatScheduler


class _Runtime:
    def identity_snapshot(self) -> IdentitySnapshot:
        return IdentitySnapshot(id="bot", name="bot", personality="p", proactive="on")


class _NeverAwaitable:
    def __await__(self) -> Any:
        yield from asyncio.Event().wait().__await__()


class _ImmediateAwaitable:
    def __await__(self) -> Any:
        if False:
            yield None
        return None


class _CaptureFake:
    def __init__(self, *, fail: bool = False, block_if_awaited: bool = False) -> None:
        self.fail = fail
        self.block_if_awaited = block_if_awaited
        self.attempts = 0
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        if not any(token in name for token in ("capture", "enqueue", "record")):
            raise AttributeError(name)

        def record(*args: Any, **kwargs: Any) -> Any:
            self.attempts += 1
            if self.fail:
                raise RuntimeError("capture unavailable")
            self.calls.append((name, args, kwargs))
            if self.block_if_awaited:
                return _NeverAwaitable()
            return _ImmediateAwaitable()

        return record


def _research_policy(*, enabled: bool, group_allowlist: list[str]) -> Any:
    config = BotConfig()
    policy = getattr(config, "research_event_capture", None)
    assert policy is not None, "BotConfig must expose research_event_capture"
    return type(policy)(enabled=enabled, group_allowlist=group_allowlist)


def _router_capture() -> Any:
    capture = getattr(router, "_capture_research_group_event", None)
    assert callable(capture), "kernel.router must expose _capture_research_group_event"
    return capture


def _ctx(policy: Any, capture: _CaptureFake) -> PluginContext:
    return cast(
        PluginContext,
        SimpleNamespace(
            config=SimpleNamespace(research_event_capture=policy),
            research_event_capture=capture,
        ),
    )


def _group_event(message: Message, *, message_id: int, reply_id: int | None = None) -> Any:
    reply = None if reply_id is None else SimpleNamespace(message_id=reply_id)
    return SimpleNamespace(
        post_type="message",
        message_type="group",
        group_id=100,
        user_id=200,
        message_id=message_id,
        message=message,
        original_message=message,
        raw_message=str(message),
        reply=reply,
        get_plaintext=message.extract_plain_text,
    )


async def _invoke_router_capture(ctx: PluginContext, capture: Any, event: Any) -> None:
    result = capture(ctx, SimpleNamespace(self_id="999"), event)
    if inspect.isawaitable(result):
        await result


def _call_payload(call: tuple[str, tuple[Any, ...], dict[str, Any]]) -> dict[str, Any]:
    _, args, kwargs = call
    if kwargs:
        return dict(kwargs)
    assert len(args) == 1, "capture should receive one event payload or keyword fields"
    payload = args[0]
    if isinstance(payload, Mapping):
        return dict(payload)
    if is_dataclass(payload):
        return asdict(payload)
    return vars(payload)


def _call_contains(call: tuple[str, tuple[Any, ...], dict[str, Any]], expected: Any) -> bool:
    def contains(value: Any) -> bool:
        if value == expected:
            return True
        if isinstance(value, Mapping):
            return any(contains(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(contains(item) for item in value)
        if is_dataclass(value):
            return contains(asdict(value))
        if hasattr(value, "__dict__"):
            return contains(vars(value))
        return False

    _, args, kwargs = call
    return contains(args) or contains(kwargs)


def _scheduler(capture: _CaptureFake, bot: Any | None) -> GroupChatScheduler:
    parameters = inspect.signature(GroupChatScheduler).parameters
    assert "research_event_capture" in parameters, "GroupChatScheduler must accept research_event_capture injection"
    scheduler = GroupChatScheduler(
        llm=AsyncMock(),
        timeline=GroupTimeline(),
        persona_runtime=_Runtime(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0.0),
        research_event_capture=capture,
    )
    if bot is not None:
        scheduler.set_bot(cast(Any, bot))
    return scheduler


def test_research_event_capture_config_defaults_disabled_and_empty() -> None:
    config = BotConfig()
    policy = getattr(config, "research_event_capture", None)

    assert policy is not None, "BotConfig must expose research_event_capture"
    assert policy.enabled is False
    assert policy.group_allowlist == []
    assert policy.allows_group("100") is False


def test_research_event_capture_allows_only_explicit_groups() -> None:
    policy = _research_policy(enabled=True, group_allowlist=["100"])

    assert policy.allows_group("100") is True
    assert policy.allows_group("101") is False


@pytest.mark.asyncio
async def test_router_capture_enqueues_each_raw_event_without_blocking() -> None:
    capture_fn = _router_capture()
    recorder = _CaptureFake(block_if_awaited=True)
    ctx = _ctx(_research_policy(enabled=True, group_allowlist=["100"]), recorder)
    text_event = _group_event(
        Message(
            [
                MessageSegment.reply(41),
                MessageSegment.at("300"),
                MessageSegment.at("400"),
                MessageSegment.text("hello"),
            ]
        ),
        message_id=501,
        reply_id=41,
    )
    image_event = _group_event(
        Message([MessageSegment.image("https://example.invalid/image.jpg")]),
        message_id=502,
    )

    await asyncio.wait_for(_invoke_router_capture(ctx, capture_fn, text_event), timeout=0.05)
    await asyncio.wait_for(_invoke_router_capture(ctx, capture_fn, image_event), timeout=0.05)

    assert len(recorder.calls) == 2
    text_payload = _call_payload(recorder.calls[0])
    assert text_payload["message_id"] == 501
    assert text_payload["reply_to_message_id"] == 41
    assert tuple(text_payload["at_targets"]) == ("300", "400")
    image_payload = _call_payload(recorder.calls[1])
    assert image_payload["message_id"] == 502
    assert image_payload["text"] is None
    assert image_payload["content_type"] == "image"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "group_allowlist"),
    [
        (False, ["100"]),
        (True, ["101"]),
    ],
)
async def test_router_capture_ignores_disabled_or_unlisted_groups(
    enabled: bool,
    group_allowlist: list[str],
) -> None:
    capture_fn = _router_capture()
    recorder = _CaptureFake()
    ctx = _ctx(_research_policy(enabled=enabled, group_allowlist=group_allowlist), recorder)
    event = _group_event(Message("hello"), message_id=501)

    await _invoke_router_capture(ctx, capture_fn, event)

    assert recorder.attempts == 0
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_router_capture_swallows_recorder_errors() -> None:
    capture_fn = _router_capture()
    recorder = _CaptureFake(fail=True)
    ctx = _ctx(_research_policy(enabled=True, group_allowlist=["100"]), recorder)
    event = _group_event(Message("hello"), message_id=501)

    await _invoke_router_capture(ctx, capture_fn, event)

    assert recorder.attempts == 1
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_scheduler_without_bot_or_with_silence_emits_no_outbound_capture() -> None:
    recorder = _CaptureFake()
    scheduler_without_bot = _scheduler(recorder, bot=None)

    await scheduler_without_bot._send_to_group("100", "hello")

    bot = SimpleNamespace(self_id="1", send_group_msg=AsyncMock())
    scheduler_with_bot = _scheduler(recorder, bot=bot)
    await scheduler_with_bot._send_to_group("100", None)  # type: ignore[arg-type]

    bot.send_group_msg.assert_not_awaited()
    assert recorder.calls == []
    await scheduler_without_bot.close()
    await scheduler_with_bot.close()


@pytest.mark.asyncio
async def test_scheduler_captures_once_after_success_with_returned_message_id() -> None:
    recorder = _CaptureFake()
    bot = SimpleNamespace(
        self_id="1",
        send_group_msg=AsyncMock(return_value={"message_id": 701}),
    )
    scheduler = _scheduler(recorder, bot=bot)

    await scheduler._send_to_group("100", "hello")

    assert bot.send_group_msg.await_count == 1
    assert len(recorder.calls) == 1
    assert _call_contains(recorder.calls[0], "100")
    assert _call_contains(recorder.calls[0], 701)
    await scheduler.close()


@pytest.mark.asyncio
async def test_scheduler_action_failed_retry_captures_only_successful_send() -> None:
    recorder = _CaptureFake()
    bot = SimpleNamespace(
        self_id="1",
        send_group_msg=AsyncMock(
            side_effect=[
                ActionFailed(status="failed", retcode=100, data=None, echo=None),
                {"message_id": 702},
            ]
        ),
    )
    scheduler = _scheduler(recorder, bot=bot)

    await scheduler._send_to_group("100", "hello")

    assert bot.send_group_msg.await_count == 2
    assert len(recorder.calls) == 1
    assert _call_contains(recorder.calls[0], 702)
    await scheduler.close()
