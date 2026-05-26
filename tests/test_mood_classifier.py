from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from services.humanization import MOOD_CURRENT_SLOT, MoodClassifier, create_humanization_state_bus
from services.system_module import Scope


def _scope() -> Scope:
    return Scope(session_id="group_100", group_id="100", user_id="u1", turn_id="t1")


@pytest.mark.asyncio
async def test_mood_classifier_detects_cold_short_replies() -> None:
    messages = [{"role": "user", "content_text": text} for text in ("嗯", "行", "哦", "不用", "随便")]

    decision = await MoodClassifier().classify(messages)

    assert decision.label == "cold"
    assert decision.signals.short_reply_ratio == 1.0


@pytest.mark.asyncio
async def test_mood_classifier_detects_tired_delay() -> None:
    messages = [
        {"role": "user", "content_text": "我先把这段说明看完", "created_at": 0.0},
        {"role": "user", "content_text": "刚刚走开了一下再继续", "created_at": 180.0},
    ]

    decision = await MoodClassifier().classify(messages)

    assert decision.label == "tired"
    assert decision.signals.reply_delay_s == 180.0


@pytest.mark.asyncio
async def test_mood_classifier_detects_playful_sticker_density() -> None:
    messages = [
        {"role": "user", "content_text": "[CQ:image,file=1]"},
        {"role": "user", "content_text": "这个表情太好笑了"},
        {"role": "user", "content_text": "mface"},
    ]

    decision = await MoodClassifier().classify(messages)

    assert decision.label == "playful"
    assert decision.signals.sticker_density == 1.0


@pytest.mark.asyncio
async def test_mood_classifier_detects_high_energy_particles() -> None:
    messages = [
        {"role": "user", "content_text": "这个真的很好玩啊我们继续看看"},
        {"role": "user", "content_text": "哈哈我懂了呢再来一次"},
    ]

    decision = await MoodClassifier().classify(messages)

    assert decision.label == "high"
    assert decision.signals.tone_particle_rate == 1.0


@pytest.mark.asyncio
async def test_mood_classifier_writes_mood_slot_with_decay() -> None:
    bus = create_humanization_state_bus()

    decision = await MoodClassifier().classify_and_write(
        [{"role": "user", "content_text": "[CQ:mface] 哈哈"}],
        bus=bus,
        scope=_scope(),
    )
    snapshot = bus.get(MOOD_CURRENT_SLOT, scope=_scope())

    assert decision.label == "playful"
    assert snapshot is not None
    assert snapshot.value["label"] == "playful"
    assert snapshot.decay_at is not None
    assert snapshot.decay_at <= datetime.now() + timedelta(seconds=305)


@pytest.mark.asyncio
async def test_cancel_during_mood_transition_does_not_dirty_slot() -> None:
    class SlowClassifier(MoodClassifier):
        async def classify(self, messages, *, feedback_sticker_density=0.0):  # type: ignore[no-untyped-def]
            await asyncio.sleep(60)
            return await super().classify(messages, feedback_sticker_density=feedback_sticker_density)

    bus = create_humanization_state_bus()
    task = asyncio.create_task(
        SlowClassifier().classify_and_write(
            [{"role": "user", "content_text": "嗯"}],
            bus=bus,
            scope=_scope(),
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert bus.get(MOOD_CURRENT_SLOT, scope=_scope()) is None
