from __future__ import annotations

from types import SimpleNamespace

from services.humanization import (
    AFFECTION_STAGE_SLOT,
    create_humanization_state_bus,
    humanization_source,
)
from services.llm.client import LLMClient
from services.system_module import Scope


class _Store:
    def __init__(self, profile: object) -> None:
        self.profile = profile

    def get(self, user_id: str) -> object:
        assert user_id == "u1"
        return self.profile


def test_llm_affection_stage_falls_back_to_canonical_affection_profile() -> None:
    client = object.__new__(LLMClient)
    client._runtime_state = None
    client._affection_engine = SimpleNamespace(
        _store=_Store(SimpleNamespace(score=80.0, total_interactions=120)),
    )

    stage = client._current_affection_stage(
        Scope(session_id="group_100", group_id="100", user_id="u1"),
    )

    assert stage == "close"


def test_llm_affection_stage_prefers_dynamic_withdraw_slot() -> None:
    scope = Scope(session_id="group_100", group_id="100", user_id="u1")
    bus = create_humanization_state_bus()
    bus.set(
        AFFECTION_STAGE_SLOT,
        {"stage": "withdraw"},
        scope=scope,
        source=humanization_source("affection_stage:test"),
        confidence=1.0,
    )
    client = object.__new__(LLMClient)
    client._runtime_state = bus
    client._affection_engine = SimpleNamespace(
        _store=_Store(SimpleNamespace(score=80.0, total_interactions=120)),
    )

    assert client._current_affection_stage(scope) == "withdraw"
