from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services.humanization import AFFECTION_STAGE_SLOT, create_humanization_state_bus
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
