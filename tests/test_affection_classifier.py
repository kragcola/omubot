from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services.humanization import AFFECTION_STAGE_SLOT, create_humanization_state_bus
from services.persona import affection_classifier as affection_classifier_module
from services.persona.affection_classifier import (
    AffectionClassifier,
    AffectionDecision,
    AffectionSignals,
    AffectionStageStore,
)
from services.system_module import Scope


def _store(tmp_path: Path) -> AffectionStageStore:
    return AffectionStageStore(tmp_path / "affection_stage.db")


async def test_affection_classifier_cold_start_stranger() -> None:
    decision = await AffectionClassifier().classify(AffectionSignals(interaction_count=0))

    assert decision.stage == "stranger"
    assert decision.reason == "cold start"


async def test_affection_classifier_falls_back_to_acquaint_without_recent_store(tmp_path: Path) -> None:
    decision = await AffectionClassifier(_store(tmp_path)).stage_for_user("1001")

    assert decision.stage == "acquaint"
    assert decision.confidence == 0.4


async def test_affection_classifier_acquaint_boundary() -> None:
    decision = await AffectionClassifier().classify(
        AffectionSignals(interaction_count=8, reply_delay_s=600, register_consistency=0.45)
    )

    assert decision.stage == "acquaint"


async def test_affection_classifier_familiar_boundary() -> None:
    decision = await AffectionClassifier().classify(
        AffectionSignals(interaction_count=30, reply_delay_s=180, register_consistency=0.55)
    )

    assert decision.stage == "familiar"


async def test_affection_classifier_close_boundary() -> None:
    decision = await AffectionClassifier().classify(
        AffectionSignals(interaction_count=100, reply_delay_s=30, register_consistency=0.75)
    )

    assert decision.stage == "close"


async def test_affection_classifier_withdraw_from_no_reply() -> None:
    decision = await AffectionClassifier().classify(
        AffectionSignals(interaction_count=80, reply_delay_s=20, register_consistency=0.8, consecutive_no_reply=5)
    )

    assert decision.stage == "withdraw"


def test_affection_stage_store_round_trips_recent_decision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    decision = AffectionDecision("familiar", 0.74, "stable", AffectionSignals(interaction_count=30))

    store.upsert("1001", decision, group_id="g1", now=1000.0)

    loaded = store.load_recent("1001", group_id="g1", now=1001.0)
    assert loaded == decision


def test_affection_stage_store_ignores_older_than_24h(tmp_path: Path) -> None:
    store = _store(tmp_path)
    decision = AffectionDecision("close", 0.82, "dense", AffectionSignals(interaction_count=100))

    store.upsert("1001", decision, now=1000.0)

    assert store.load_recent("1001", now=1000.0 + 86_401) is None


async def test_affection_classifier_writes_bus_and_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bus = create_humanization_state_bus()
    classifier = AffectionClassifier(store)
    scope = Scope(user_id="1001")

    decision = await classifier.classify_and_write(
        "1001",
        AffectionSignals(interaction_count=100, reply_delay_s=20, register_consistency=0.8),
        bus=bus,
        scope=scope,
        group_id="g1",
    )

    snapshot = bus.get(AFFECTION_STAGE_SLOT, scope=scope)
    assert decision.stage == "close"
    assert snapshot is not None
    assert snapshot.value["stage"] == "close"
    assert snapshot.value["ttl_s"] == 86_400
    assert snapshot.decay_at is not None
    assert store.load_recent("1001", group_id="g1") is not None


async def test_affection_classifier_cancel_path_does_not_dirty_write(tmp_path: Path) -> None:
    class CancelClassifier(AffectionClassifier):
        async def classify(self, signals: AffectionSignals) -> AffectionDecision:
            raise asyncio.CancelledError

    store = _store(tmp_path)
    bus = create_humanization_state_bus()

    with pytest.raises(asyncio.CancelledError):
        await CancelClassifier(store).classify_and_write(
            "1001",
            AffectionSignals(interaction_count=100),
            bus=bus,
            scope=Scope(user_id="1001"),
        )

    assert bus.get(AFFECTION_STAGE_SLOT, scope=Scope(user_id="1001")) is None
    assert store.load_recent("1001") is None


async def test_affection_stage_slot_isolates_users_in_same_session() -> None:
    bus = create_humanization_state_bus()
    classifier = AffectionClassifier()
    first_scope = Scope(session_id="group_100", group_id="100", user_id="u1")
    second_scope = Scope(session_id="group_100", group_id="100", user_id="u2")

    await classifier.classify_and_write(
        "u1",
        AffectionSignals(interaction_count=0),
        bus=bus,
        scope=first_scope,
        group_id="100",
    )
    await classifier.classify_and_write(
        "u2",
        AffectionSignals(interaction_count=100, register_consistency=0.8),
        bus=bus,
        scope=second_scope,
        group_id="100",
    )

    first = bus.get(AFFECTION_STAGE_SLOT, scope=first_scope)
    second = bus.get(AFFECTION_STAGE_SLOT, scope=second_scope)
    assert first is not None and first.value["stage"] == "stranger"
    assert second is not None and second.value["stage"] == "close"


@pytest.mark.parametrize(
    ("score", "interactions", "expected"),
    [
        (0.0, 0, "stranger"),
        (8.0, 10, "acquaint"),
        (40.0, 50, "familiar"),
        (80.0, 120, "close"),
    ],
)
def test_affection_stage_converges_on_canonical_profile(
    score: float,
    interactions: int,
    expected: str,
) -> None:
    profile = type(
        "Profile",
        (),
        {"score": score, "total_interactions": interactions},
    )()
    resolver = getattr(affection_classifier_module, "stage_from_affection_profile", None)

    assert callable(resolver), "affection stage must converge on the canonical profile"
    assert resolver(profile) == expected
