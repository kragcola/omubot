from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from kernel.router import _content_to_text
from kernel.types import TriggerContext


class _FakeArbiter:
    def __init__(self, *, needs_correction: bool, correction_type: str | None = "amend") -> None:
        self.needs_correction = needs_correction
        self.correction_type = correction_type
        self.calls: list[dict[str, object]] = []

    async def judge_correction(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            needs_correction=self.needs_correction,
            correction_type=self.correction_type,
            fallback=False,
        )


class _FakeScheduler:
    def __init__(self, slot: SimpleNamespace, arbiter: object, correction_window_s: float = 30.0) -> None:
        self._slot = slot
        self._arbiter = arbiter
        self._arbiter_config = SimpleNamespace(
            enabled=True,
            correction_enabled=True,
            correction_window_s=correction_window_s,
        )

    def get_slot(self, group_id: str) -> SimpleNamespace:
        assert group_id == "123"
        return self._slot


def _slot(*, content: str, age_s: float) -> SimpleNamespace:
    return SimpleNamespace(
        last_reply_content=content,
        last_reply_time=time.time() - age_s,
    )


@pytest.mark.asyncio
async def test_correction_triggered_within_window() -> None:
    arbiter = _FakeArbiter(needs_correction=True, correction_type="amend")
    slot = _slot(content="刚才那条回复", age_s=5.0)

    correction = await arbiter.judge_correction(
        bot_reply=slot.last_reply_content,
        new_message="不是这个意思，我是说今晚",
        user_id="42",
        group_id="123",
    )
    trigger = TriggerContext(
        reason="用户补充了改变语义的信息，请自然修正上一条回复",
        mode="correction",
        target_message_id=1001,
        target_user_id="42",
        extra={
            "correction_type": correction.correction_type,
            "original_reply": slot.last_reply_content,
        },
    )
    slot.last_reply_content = ""

    assert correction.needs_correction is True
    assert trigger.mode == "correction"
    assert trigger.extra["correction_type"] == "amend"
    assert trigger.extra["original_reply"] == "刚才那条回复"
    assert arbiter.calls[0]["new_message"] == "不是这个意思，我是说今晚"
    assert slot.last_reply_content == ""


def test_correction_not_triggered_outside_window() -> None:
    arbiter = _FakeArbiter(needs_correction=True)
    slot = _slot(content="刚才那条回复", age_s=31.0)
    scheduler = _FakeScheduler(slot, arbiter, correction_window_s=30.0)

    expired = time.time() - slot.last_reply_time > scheduler._arbiter_config.correction_window_s

    assert expired is True
    assert arbiter.calls == []


@pytest.mark.asyncio
async def test_correction_false_falls_through_to_followup() -> None:
    arbiter = _FakeArbiter(needs_correction=False, correction_type=None)
    slot = _slot(content="刚才那条回复", age_s=3.0)

    correction = await arbiter.judge_correction(
        bot_reply=slot.last_reply_content,
        new_message="我也可以吗",
        user_id="42",
        group_id="123",
    )

    assert correction.needs_correction is False
    assert correction.correction_type is None
    followup = TriggerContext(reason="用户追问上一轮回复", mode="directed_followup", target_user_id="42")
    assert followup.mode == "directed_followup"


def test_correction_clears_last_reply() -> None:
    slot = _slot(content="刚才那条回复", age_s=2.0)

    slot.last_reply_content = ""
    slot.last_reply_time = 0.0

    assert slot.last_reply_content == ""
    assert slot.last_reply_time == 0.0


def test_content_to_text_handles_string_message() -> None:
    assert _content_to_text("我也可以吗") == "我也可以吗"
