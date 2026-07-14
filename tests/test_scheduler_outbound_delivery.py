from __future__ import annotations

import asyncio
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest
from nonebot.adapters.onebot.v11 import ActionFailed, Message

RAW_TEXT = "[CQ:reply,id=77][CQ:at,qq=123456789]你好"


def _load_api() -> tuple[type[Any], type[Any], type[Any], Any, type[Any]]:
    try:
        module = import_module("services.scheduler_pipeline.outbound_delivery")
    except ImportError as exc:
        pytest.fail(
            "missing expected services.scheduler_pipeline.outbound_delivery module",
            pytrace=False,
        )
        raise AssertionError("unreachable") from exc

    names = (
        "HumanizationContext",
        "OutboundDeliveryRequest",
        "OutboundDeliveryResult",
        "DeliveryStatus",
        "RuntimeOutboundDelivery",
    )
    missing = [name for name in names if not hasattr(module, name)]
    if missing:
        pytest.fail(
            f"outbound delivery API is missing required type(s): {', '.join(missing)}",
            pytrace=False,
        )
    return tuple(getattr(module, name) for name in names)  # type: ignore[return-value]


class _RecordingHumanizer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    async def delay(self, text: str, **kwargs: Any) -> None:
        self.events.append("humanizer")
        self.calls.append({"text": text, **kwargs})


class _RecordingBot:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    async def send_group_msg(self, *, group_id: int, message: Message) -> dict[str, int]:
        self.events.append("bot")
        self.calls.append({"group_id": group_id, "message": message})
        return {"message_id": 701}


class _RecordingResearchCapture:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    def capture_outbound(self, **kwargs: Any) -> bool:
        self.events.append("research_capture")
        self.calls.append(kwargs)
        return True


class _FailingResearchCapture(_RecordingResearchCapture):
    def capture_outbound(self, **kwargs: Any) -> bool:
        self.events.append("research_capture")
        self.calls.append(kwargs)
        raise RuntimeError("research unavailable")


class _FailingBot:
    def __init__(self, events: list[str], error: ActionFailed | None = None) -> None:
        self.events = events
        self.error = error or ActionFailed(
            status="failed",
            retcode=100,
            data=None,
            echo=None,
            wording="blocked by platform",
        )
        self.calls: list[dict[str, Any]] = []

    async def send_group_msg(self, *, group_id: int, message: Message) -> dict[str, int]:
        self.events.append("bot")
        self.calls.append({"group_id": group_id, "message": message})
        raise self.error


class _BlockingBot:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = asyncio.Event()

    async def send_group_msg(self, *, group_id: int, message: Message) -> dict[str, int]:
        del group_id, message
        self.events.append("bot")
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _RecordingPairGuard:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[str, str]] = []

    def record_outbound(self, group_id: str, target_user_id: str) -> bool:
        self.events.append("pair_guard")
        self.calls.append((group_id, target_user_id))
        return True


class _FailingPairGuard(_RecordingPairGuard):
    def record_outbound(self, group_id: str, target_user_id: str) -> bool:
        self.events.append("pair_guard")
        self.calls.append((group_id, target_user_id))
        raise RuntimeError("pair guard unavailable")


@pytest.mark.asyncio
async def test_runtime_outbound_delivery_sends_typed_message_and_records_success_once() -> None:
    context_type, request_type, _, delivery_status, runtime_type = _load_api()
    register = {"label": "quiet", "confidence": 0.88}
    slot = {"slot_activity": "晚自习后", "energy": 0.2}
    mood = SimpleNamespace(energy=0.3, valence=-0.2, openness=0.4)
    humanization = context_type(group_id="100", register=register, slot=slot, mood=mood)
    request = request_type(
        group_id="100",
        text=RAW_TEXT,
        humanize="normal",
        target_user_id="123456789",
        actor_id="1",
        humanization=humanization,
    )
    events: list[str] = []
    humanizer = _RecordingHumanizer(events)
    bot = _RecordingBot(events)
    research_capture = _RecordingResearchCapture(events)
    pair_guard = _RecordingPairGuard(events)
    delivery = runtime_type(
        bot=bot,
        humanizer=humanizer,
        research_capture=research_capture,
        pair_guard=pair_guard,
    )

    result = await delivery.deliver(request)

    assert humanizer.calls == [
        {
            "text": RAW_TEXT,
            "group_id": "100",
            "register": register,
            "slot": slot,
            "mood": mood,
        }
    ]
    assert len(bot.calls) == 1
    sent_message = bot.calls[0]["message"]
    assert bot.calls[0]["group_id"] == 100
    assert isinstance(sent_message, Message)
    assert sent_message == Message(RAW_TEXT)

    assert len(research_capture.calls) == 1
    captured = research_capture.calls[0]
    assert captured["group_id"] == "100"
    assert captured["actor_id"] == "1"
    assert captured["message_id"] == 701
    assert captured["reply_to_message_id"] == 77
    assert tuple(captured["at_targets"]) == ("123456789",)
    assert captured["text"] == "你好"
    assert captured["content_type"] == "text"

    assert pair_guard.calls == [("100", "123456789")]
    assert events == ["humanizer", "bot", "research_capture", "pair_guard"]
    assert result.status is delivery_status.SENT
    assert result.message_id == 701
    assert result.pair_guard_recorded is True
    assert result.elapsed_s >= 0.0
    assert result.retcode is None
    assert result.wording is None


@pytest.mark.asyncio
async def test_runtime_outbound_delivery_returns_action_failed_without_side_effects() -> None:
    context_type, request_type, _, delivery_status, runtime_type = _load_api()
    request = request_type(
        group_id="100",
        text="发送失败",
        humanize="normal",
        target_user_id="123456789",
        actor_id="1",
        humanization=context_type(group_id="100"),
    )
    events: list[str] = []
    humanizer = _RecordingHumanizer(events)
    bot = _FailingBot(events)
    research_capture = _RecordingResearchCapture(events)
    pair_guard = _RecordingPairGuard(events)
    delivery = runtime_type(
        bot=bot,
        humanizer=humanizer,
        research_capture=research_capture,
        pair_guard=pair_guard,
    )

    result = await delivery.deliver(request)

    assert events == ["humanizer", "bot"]
    assert research_capture.calls == []
    assert pair_guard.calls == []
    assert result.status is delivery_status.FAILED
    assert result.message_id is None
    assert result.pair_guard_recorded is False
    assert result.elapsed_s >= 0.0
    assert result.retcode == 100
    assert result.wording == "blocked by platform"


@pytest.mark.asyncio
async def test_runtime_outbound_delivery_unwraps_nested_action_failed_info() -> None:
    context_type, request_type, _, delivery_status, runtime_type = _load_api()
    request = request_type(
        group_id="100",
        text="平台禁言",
        humanize="normal",
        target_user_id="123456789",
        actor_id="1",
        humanization=context_type(group_id="100"),
    )
    events: list[str] = []
    error = ActionFailed(info={"retcode": 1200, "wording": "forbidden"})
    humanizer = _RecordingHumanizer(events)
    bot = _FailingBot(events, error)
    research_capture = _RecordingResearchCapture(events)
    pair_guard = _RecordingPairGuard(events)
    delivery = runtime_type(
        bot=bot,
        humanizer=humanizer,
        research_capture=research_capture,
        pair_guard=pair_guard,
    )

    result = await delivery.deliver(request)

    assert len(bot.calls) == 1
    assert events == ["humanizer", "bot"]
    assert research_capture.calls == []
    assert pair_guard.calls == []
    assert result.status is delivery_status.FAILED
    assert result.retcode == 1200
    assert result.wording == "forbidden"


@pytest.mark.asyncio
async def test_runtime_outbound_delivery_propagates_cancellation_without_side_effects() -> None:
    context_type, request_type, _, _, runtime_type = _load_api()
    request = request_type(
        group_id="100",
        text="取消中",
        humanize="normal",
        target_user_id="123456789",
        actor_id="1",
        humanization=context_type(group_id="100"),
    )
    events: list[str] = []
    humanizer = _RecordingHumanizer(events)
    bot = _BlockingBot(events)
    research_capture = _RecordingResearchCapture(events)
    pair_guard = _RecordingPairGuard(events)
    delivery = runtime_type(
        bot=bot,
        humanizer=humanizer,
        research_capture=research_capture,
        pair_guard=pair_guard,
    )

    task = asyncio.create_task(delivery.deliver(request))
    await asyncio.wait_for(bot.started.wait(), timeout=0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == ["humanizer", "bot"]
    assert research_capture.calls == []
    assert pair_guard.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   "])
async def test_runtime_outbound_delivery_skips_empty_text_without_side_effects(text: str) -> None:
    context_type, request_type, _, delivery_status, runtime_type = _load_api()
    request = request_type(
        group_id="100",
        text=text,
        humanize="normal",
        target_user_id="123456789",
        actor_id="1",
        humanization=context_type(group_id="100"),
    )
    events: list[str] = []
    humanizer = _RecordingHumanizer(events)
    bot = _RecordingBot(events)
    research_capture = _RecordingResearchCapture(events)
    pair_guard = _RecordingPairGuard(events)
    delivery = runtime_type(
        bot=bot,
        humanizer=humanizer,
        research_capture=research_capture,
        pair_guard=pair_guard,
    )

    result = await delivery.deliver(request)

    assert events == []
    assert bot.calls == []
    assert research_capture.calls == []
    assert pair_guard.calls == []
    assert result.status is delivery_status.SKIPPED
    assert result.elapsed_s == 0.0
    assert result.message_id is None
    assert result.retcode is None
    assert result.wording is None
    assert result.pair_guard_recorded is False


@pytest.mark.asyncio
async def test_runtime_outbound_delivery_continues_after_research_capture_error() -> None:
    context_type, request_type, _, delivery_status, runtime_type = _load_api()
    request = request_type(
        group_id="100",
        text="研究记录失败仍已发送",
        humanize="normal",
        target_user_id="123456789",
        actor_id="1",
        humanization=context_type(group_id="100"),
    )
    events: list[str] = []
    humanizer = _RecordingHumanizer(events)
    bot = _RecordingBot(events)
    research_capture = _FailingResearchCapture(events)
    pair_guard = _RecordingPairGuard(events)
    delivery = runtime_type(
        bot=bot,
        humanizer=humanizer,
        research_capture=research_capture,
        pair_guard=pair_guard,
    )

    result = await delivery.deliver(request)

    assert len(bot.calls) == 1
    assert len(research_capture.calls) == 1
    assert pair_guard.calls == [("100", "123456789")]
    assert events == ["humanizer", "bot", "research_capture", "pair_guard"]
    assert result.status is delivery_status.SENT
    assert result.message_id == 701
    assert result.pair_guard_recorded is True


@pytest.mark.asyncio
async def test_runtime_outbound_delivery_returns_sent_after_pair_guard_error() -> None:
    context_type, request_type, _, delivery_status, runtime_type = _load_api()
    request = request_type(
        group_id="100",
        text="pair guard 失败仍已发送",
        humanize="normal",
        target_user_id="123456789",
        actor_id="1",
        humanization=context_type(group_id="100"),
    )
    events: list[str] = []
    humanizer = _RecordingHumanizer(events)
    bot = _RecordingBot(events)
    research_capture = _RecordingResearchCapture(events)
    pair_guard = _FailingPairGuard(events)
    delivery = runtime_type(
        bot=bot,
        humanizer=humanizer,
        research_capture=research_capture,
        pair_guard=pair_guard,
    )

    result = await delivery.deliver(request)

    assert len(bot.calls) == 1
    assert len(research_capture.calls) == 1
    assert pair_guard.calls == [("100", "123456789")]
    assert events == ["humanizer", "bot", "research_capture", "pair_guard"]
    assert result.status is delivery_status.SENT
    assert result.message_id == 701
    assert result.pair_guard_recorded is False
