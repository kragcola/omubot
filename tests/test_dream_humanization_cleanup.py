from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from plugins.dream import DreamAgent
from services.humanization import (
    AFFECTION_FAMILIARITY_SLOT,
    REGISTER_LABEL_SLOT,
    create_humanization_state_bus,
    humanization_source,
)
from services.memory.card_store import CardStore
from services.system_module import Scope


def _card_store(tmp_path: Path) -> CardStore:
    return CardStore(db_path=str(tmp_path / "dream_cleanup_cards.db"))


def _age_slot(bus: object, slot_id: str, *, minutes: int) -> None:
    key = next(key for key in bus._values if key[0] == slot_id)  # type: ignore[attr-defined]
    bus._values[key] = replace(  # type: ignore[attr-defined]
        bus._values[key],  # type: ignore[attr-defined]
        updated_at=datetime.now() - timedelta(minutes=minutes),
    )


def test_dream_cleanup_removes_stale_per_session_state(tmp_path: Path) -> None:
    bus = create_humanization_state_bus()
    scope = Scope(session_id="group_100", group_id="100", user_id="u1", turn_id="t1")
    bus.set(
        REGISTER_LABEL_SLOT,
        {"label": "playful"},
        scope=scope,
        source=humanization_source("dream_cleanup:test"),
        confidence=0.8,
    )
    _age_slot(bus, REGISTER_LABEL_SLOT, minutes=31)
    agent = DreamAgent(store=_card_store(tmp_path), runtime_state=bus)

    assert agent._cleanup_runtime_state() == 1
    assert bus.get(REGISTER_LABEL_SLOT, scope=scope) is None


def test_dream_cleanup_keeps_per_user_familiarity(tmp_path: Path) -> None:
    bus = create_humanization_state_bus()
    scope = Scope(user_id="u1")
    bus.set(
        AFFECTION_FAMILIARITY_SLOT,
        {"familiarity": 0.7},
        scope=scope,
        source=humanization_source("dream_cleanup:test"),
        confidence=1.0,
    )
    _age_slot(bus, AFFECTION_FAMILIARITY_SLOT, minutes=31)
    agent = DreamAgent(store=_card_store(tmp_path), runtime_state=bus)

    assert agent._cleanup_runtime_state() == 0
    assert bus.get(AFFECTION_FAMILIARITY_SLOT, scope=scope) is not None


def test_dream_cleanup_without_runtime_state_noops(tmp_path: Path) -> None:
    agent = DreamAgent(store=_card_store(tmp_path), runtime_state=None)

    assert agent._cleanup_runtime_state() == 0
