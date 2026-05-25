from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plugins.affection.engine import AffectionEngine
from plugins.affection.models import AffectionProfile
from plugins.affection.store import AffectionStore
from services.humanization import AFFECTION_FAMILIARITY_SLOT, create_humanization_state_bus
from services.system_module import Scope


def _store(tmp_path: Path) -> AffectionStore:
    path = tmp_path / "affection"
    path.mkdir()
    return AffectionStore(storage_dir=str(path))


def _snapshot(bus: object, user_id: str):
    return bus.get(AFFECTION_FAMILIARITY_SLOT, scope=Scope(user_id=user_id))  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_affection_familiarity_writes_ttl_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.startup()
    bus = create_humanization_state_bus()
    engine = AffectionEngine(store, score_increment=10.0, daily_cap=100.0)
    engine.set_runtime_state_bus(bus)

    await engine.record_interaction("1001")

    snapshot = _snapshot(bus, "1001")
    assert snapshot is not None
    assert snapshot.value["user_id"] == "1001"
    assert snapshot.value["familiarity"] > 0
    assert snapshot.value["score"] == 10.0
    assert snapshot.decay_at is not None
    assert datetime.now() + timedelta(minutes=59) <= snapshot.decay_at <= datetime.now() + timedelta(minutes=61)


@pytest.mark.asyncio
async def test_affection_familiarity_isolates_multiple_users(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.startup()
    bus = create_humanization_state_bus()
    engine = AffectionEngine(store, score_increment=10.0, daily_cap=100.0)
    engine.set_runtime_state_bus(bus)

    await engine.record_interaction("1001")
    await engine.record_interaction("1002")
    await engine.record_interaction("1002")

    first = _snapshot(bus, "1001")
    second = _snapshot(bus, "1002")
    assert first is not None
    assert second is not None
    assert first.value["user_id"] == "1001"
    assert second.value["user_id"] == "1002"
    assert first.value["familiarity"] < second.value["familiarity"]


@pytest.mark.asyncio
async def test_affection_familiarity_accumulates_on_record_interaction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.startup()
    bus = create_humanization_state_bus()
    engine = AffectionEngine(store, score_increment=5.0, daily_cap=100.0)
    engine.set_runtime_state_bus(bus)

    await engine.record_interaction("1001")
    first_score = _snapshot(bus, "1001").value["familiarity"]  # type: ignore[union-attr]
    await engine.record_interaction("1001")
    second = _snapshot(bus, "1001")

    assert second is not None
    assert second.value["daily_count"] == 2
    assert second.value["total_interactions"] == 2
    assert second.value["familiarity"] > first_score


@pytest.mark.asyncio
async def test_affection_familiarity_syncs_mood_bonus_and_tier(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.startup()
    store.save(AffectionProfile(user_id="1001", score=70.0, total_interactions=20, daily_count=3))
    bus = create_humanization_state_bus()
    engine = AffectionEngine(store, score_increment=5.0, daily_cap=100.0)
    engine.set_runtime_state_bus(bus)

    await engine.record_interaction("1001")

    snapshot = _snapshot(bus, "1001")
    assert snapshot is not None
    assert snapshot.value["tier"] == "好朋友"
    assert snapshot.value["mood_bonus_valence"] == 0.18
    assert snapshot.value["familiarity"] == engine.familiarity_score("1001")
