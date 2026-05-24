from dataclasses import replace

import pytest

from services.system_module import (
    MODULE_CATALOG,
    REQUIRED_MODULE_IDS,
    RESERVED_MODULE_IDS,
    ModuleContract,
    RuntimeStateBus,
    Scope,
    SourceRef,
    StateSlotDefinition,
    StateSlotOwnershipError,
    catalog_contracts,
    catalog_system_modules,
    validate_module_graph,
)


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_catalog_has_expected_module_shape() -> None:
    contracts = catalog_contracts()
    module_ids = {contract.id for contract in contracts}

    assert len(contracts) == 34
    assert module_ids >= REQUIRED_MODULE_IDS
    assert module_ids >= RESERVED_MODULE_IDS
    assert all(next(c for c in contracts if c.id == module_id).required for module_id in REQUIRED_MODULE_IDS)
    assert all(next(c for c in contracts if c.id == module_id).reserved for module_id in RESERVED_MODULE_IDS)
    assert all(not next(c for c in contracts if c.id == module_id).enabled for module_id in RESERVED_MODULE_IDS)

    system_modules = catalog_system_modules()
    assert system_modules["core.identity"]["required"] is True
    assert system_modules["state.world"]["reserved"] is True


def test_default_catalog_validates_as_dry_run_graph() -> None:
    result = validate_module_graph(MODULE_CATALOG)

    assert result.ok is True
    assert result.issues == ()
    assert "runtime.adapter" in result.module_order
    assert result.module_order.index("runtime.adapter") < result.module_order.index("runtime.scheduler")


def test_module_contract_from_dict_parses_yaml_style_payload() -> None:
    contract = ModuleContract.from_dict({
        "id": "runtime.thinker",
        "group": "runtime",
        "version": "2.1.0",
        "persona_bindings": {
            "reads": ["thinker.yaml"],
            "writes": [],
        },
        "state_owns": [
            {"id": "runtime.thinker.decision", "schema": "omubot.state.thinker_decision.v2"},
        ],
        "state_consumes": ["state.mood.current"],
        "depends_on": ["state.mood"],
        "switch_surface": {
            "on_disabled": {
                "behavior": "degrade",
                "default_decision": {"action": "reply"},
            },
        },
    })

    assert contract.id == "runtime.thinker"
    assert contract.group == "runtime"
    assert contract.persona_reads == ("thinker.yaml",)
    assert contract.state_owns[0].id == "runtime.thinker.decision"
    assert contract.switch_surface.on_disabled.default_decision == {"action": "reply"}


def test_validator_rejects_disabled_required_module() -> None:
    contracts = [
        replace(contract, enabled=False)
        if contract.id == "core.identity"
        else contract
        for contract in MODULE_CATALOG
    ]

    result = validate_module_graph(contracts)

    assert result.ok is False
    assert "required_module_disabled" in _codes(result)


def test_validator_rejects_missing_state_owner_and_duplicate_owner() -> None:
    duplicate = ModuleContract(
        id="core.duplicate",
        group="core",
        state_owns=(
            StateSlotDefinition(
                id="state.mood.current",
                schema="omubot.state.duplicate.v2",
            ),
        ),
    )
    missing = ModuleContract(
        id="core.missing_consumer",
        group="core",
        state_consumes=("state.no_owner",),
    )

    result = validate_module_graph([*MODULE_CATALOG, duplicate, missing])

    assert result.ok is False
    assert "duplicate_state_owner" in _codes(result)
    assert "missing_state_owner" in _codes(result)


def test_validator_detects_dependency_cycle() -> None:
    a = ModuleContract(id="core.a", group="core", depends_on=("core.b",))
    b = ModuleContract(id="core.b", group="core", depends_on=("core.a",))

    result = validate_module_graph([a, b])

    assert result.ok is False
    assert "module_dependency_cycle" in _codes(result)


def test_runtime_state_bus_enforces_owner_and_trace_snapshot() -> None:
    contracts = catalog_contracts()
    bus = RuntimeStateBus(contracts)
    scope = Scope(session_id="s1", group_id="g1", user_id="u1", turn_id="t1")

    bus.set(
        "runtime.adapter.last_event",
        {"text": "hi"},
        scope=scope,
        source=SourceRef("runtime.adapter", "conversation_archive:1"),
        confidence=1.0,
    )

    snapshot = bus.get("runtime.adapter.last_event", scope=scope)
    assert snapshot is not None
    assert snapshot.value == {"text": "hi"}
    trace = bus.snapshot_all_for_trace()
    assert next(iter(trace.values()))["source"]["module_id"] == "runtime.adapter"

    with pytest.raises(StateSlotOwnershipError):
        bus.set(
            "runtime.adapter.last_event",
            {"text": "bad"},
            scope=scope,
            source=SourceRef("core.identity", "conversation_archive:2"),
            confidence=1.0,
        )

    with pytest.raises(ValueError, match="evidence_path"):
        bus.set(
            "runtime.adapter.last_event",
            {"text": "bad"},
            scope=scope,
            source=SourceRef("runtime.adapter", ""),
            confidence=1.0,
        )


def test_runtime_state_bus_clears_per_turn_slots() -> None:
    bus = RuntimeStateBus(catalog_contracts())
    scope = Scope(session_id="s1", group_id="g1", user_id="u1", turn_id="t1")

    bus.set(
        "runtime.adapter.last_event",
        {"text": "hi"},
        scope=scope,
        source=SourceRef("runtime.adapter", "conversation_archive:1"),
        confidence=1.0,
    )
    assert bus.get("runtime.adapter.last_event", scope=scope) is not None

    bus.clear_per_turn(scope=scope)

    assert bus.get("runtime.adapter.last_event", scope=scope) is None
