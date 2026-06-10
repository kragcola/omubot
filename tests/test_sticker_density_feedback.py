from __future__ import annotations

from services.humanization import MOOD_CURRENT_SLOT, MoodClassifier, create_humanization_state_bus
from services.sticker import StickerDecisionContext, StickerDecisionProvider
from services.system_module import Scope


def _scope() -> Scope:
    return Scope(session_id="group_100", group_id="100", user_id="u1")


async def test_sticker_decision_provider_writes_feedback_signal() -> None:
    bus = create_humanization_state_bus()

    decision = await StickerDecisionProvider().decide(
        StickerDecisionContext(frequent_candidates=("s1",)),
        runtime_state=bus,
        scope=_scope(),
        rng=lambda: 0.0,
    )

    snapshot = bus.get(MOOD_CURRENT_SLOT, scope=_scope())
    assert decision.should_send is True
    assert snapshot is not None
    assert snapshot.value["signals"]["feedback_sticker_density"] == 0.3


async def test_mood_classifier_consumes_feedback_once_and_clears_slot() -> None:
    bus = create_humanization_state_bus()
    scope = _scope()
    await StickerDecisionProvider().decide(
        StickerDecisionContext(frequent_candidates=("s1",)),
        runtime_state=bus,
        scope=scope,
        rng=lambda: 0.0,
    )

    decision = await MoodClassifier().classify_and_write(
        [
            {"role": "user", "content": "今天这个梗还挺顺的", "created_at": 1},
            {"role": "user", "content": "我们继续接一下这个话头", "created_at": 2},
            {"role": "user", "content": "看看[CQ:image,file=x]", "created_at": 3},
            {"role": "user", "content": "确实有点好笑哈哈", "created_at": 4},
        ],
        bus=bus,
        scope=scope,
    )

    snapshot = bus.get(MOOD_CURRENT_SLOT, scope=scope)
    assert decision.label == "playful"
    assert decision.signals.feedback_sticker_density == 0.3
    assert decision.signals.sticker_density == 0.55
    assert snapshot is not None
    assert snapshot.value["signals"]["feedback_sticker_density"] == 0.0


async def test_sticker_density_feedback_skips_when_no_send() -> None:
    bus = create_humanization_state_bus()

    decision = await StickerDecisionProvider().decide(
        StickerDecisionContext(thinker_candidates=("hint",)),
        runtime_state=bus,
        scope=_scope(),
        rng=lambda: 0.99,
    )

    assert decision.should_send is False
    assert bus.get(MOOD_CURRENT_SLOT, scope=_scope()) is None
