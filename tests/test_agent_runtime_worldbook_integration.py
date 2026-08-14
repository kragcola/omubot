"""RED contracts for governed Worldbook proposals and verified commit receipts."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from plugins.schedule.story_arc import StoryArc
from services.social_narrative import SocialExperience
from services.worldbook import EventRecord
from services.worldbook.reducer import EventReducer

PROPOSED_AT = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
EVIDENCE_AT = datetime(2026, 7, 22, 3, 30, tzinfo=UTC)
RAW_USER = "RAW_USER_SENTINEL_WORLDBOOK_P4"
RAW_BOT = "RAW_BOT_SENTINEL_WORLDBOOK_P4"
OFFLINE_BEHAVIOR = "昨晚在校外见面"


def _contracts() -> ModuleType:
    spec = importlib.util.find_spec("services.worldbook.governance_contracts")
    assert spec is not None, (
        "services.worldbook.governance_contracts must own Worldbook governance values"
    )
    return importlib.import_module("services.worldbook.governance_contracts")


def _adapters() -> ModuleType:
    spec = importlib.util.find_spec("services.worldbook.governed_adapters")
    assert spec is not None, (
        "services.worldbook.governed_adapters must expose pure proposal adapters"
    )
    return importlib.import_module("services.worldbook.governed_adapters")


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _json_tree(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_tree(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_tree(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_tree(item) for item in value]
    return value


def _serialized(value: Any) -> str:
    return json.dumps(_json_tree(value), ensure_ascii=False, sort_keys=True)


def _dataclass_is_frozen(value: Any) -> bool:
    return bool(value.__dataclass_params__.frozen)


def _assert_deeply_immutable(value: Any) -> None:
    if is_dataclass(value) and not isinstance(value, type):
        dataclass_fields = fields(value)
        assert _dataclass_is_frozen(value)
        if dataclass_fields:
            first = dataclass_fields[0]
            with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
                setattr(value, first.name, getattr(value, first.name))
        for field in dataclass_fields:
            _assert_deeply_immutable(getattr(value, field.name))
        return
    if isinstance(value, Mapping):
        assert not isinstance(value, dict)
        if value:
            first_key = next(iter(value))
            with pytest.raises((TypeError, AttributeError)):
                value[first_key] = value[first_key]  # type: ignore[index]
        for item in value.values():
            _assert_deeply_immutable(item)
        return
    if isinstance(value, tuple):
        for item in value:
            _assert_deeply_immutable(item)
        return
    assert not isinstance(value, (dict, list, set, bytearray))


def _assign_arc_id(value: Any) -> None:
    value.arc_id = "arc.changed"


def _social_record(**overrides: Any) -> SocialExperience:
    values: dict[str, Any] = {
        "experience_id": "social.exp.7001",
        "group_id": "200",
        "user_id": "100",
        "entity_kind": "factual",
        "evidence_message_id": "7001",
        "evidence_time": "2026-07-22T11:30:00+08:00",
        "evidence_source": "reply_context",
        "user_text": f"{RAW_USER}: {OFFLINE_BEHAVIOR}",
        "bot_reply": RAW_BOT,
        "status": "active",
        "created_at": "2026-07-22T11:31:00+08:00",
    }
    values.update(overrides)
    return SocialExperience(**values)


def _social_proposal(record: SocialExperience | None = None, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "record": record or _social_record(),
        "world_id": "world.main",
        "target_arc_id": "arc.social.main",
        "current_group_id": "200",
        "current_user_id": "100",
        "proposed_at": PROPOSED_AT,
    }
    values.update(overrides)
    return _adapters().build_social_event_proposal(**values)


def _schedule_proposal(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "world_id": "omubot.default",
        "target_arc_id": "arc.schedule.main",
        "schedule_date": "2026-07-23",
        "summary": "Prepare the rehearsal room.",
        "proposed_at": PROPOSED_AT,
    }
    values.update(overrides)
    return _adapters().build_schedule_event_proposal(**values)


def _committed_fixture(proposal: Any) -> tuple[EventRecord, StoryArc, tuple[str, ...]]:
    proposed_commit = replace(
        proposal.event,
        status="committed",
        committed_at="2026-07-22T12:05:00+08:00",
        step=7,
    )
    arc = StoryArc(
        arc_id=proposal.world_ref.arc_id,
        revision=3,
    )
    committed = EventReducer().apply(arc, proposed_commit, now_step=7)
    committed_ids = tuple(arc.event_budget["committed_event_ids"])
    return committed, arc, committed_ids


def _verified_receipt_fixture() -> tuple[Any, Any, EventRecord, StoryArc, tuple[str, ...]]:
    proposal = _schedule_proposal()
    committed, arc, committed_ids = _committed_fixture(proposal)
    receipt = _adapters().verify_committed_world_event(
        proposal,
        committed_event=committed,
        committed_arc_snapshot=arc.to_dict(),
        committed_event_ids=committed_ids,
        persisted_revision=arc.revision,
    )
    return receipt, proposal, committed, arc, committed_ids


def test_worldbook_governance_is_independent_from_p3_memory_projection_enums() -> None:
    memory = importlib.import_module("services.memory.governance_contracts")
    contracts = _contracts()
    adapters = _adapters()

    assert not hasattr(memory.ProducerKind, "WORLDBOOK")
    assert not hasattr(memory.ProjectionKind, "WORLDBOOK_EVENT")
    for name in ("WorldRefV1", "WorldbookEventProposalV1", "WorldbookCommitReceiptV1"):
        assert inspect.isclass(getattr(contracts, name, None)), f"missing {name}"
    for name in (
        "build_social_event_proposal",
        "build_schedule_event_proposal",
        "verify_committed_world_event",
    ):
        assert callable(getattr(adapters, name, None)), f"missing {name}"


@pytest.mark.parametrize(
    ("world_id", "arc_id"),
    [
        ("", "arc.main"),
        ("world.main", ""),
        ("has space", "arc.main"),
        ("world.main", "../arc"),
    ],
)
def test_world_ref_is_frozen_and_rejects_noncanonical_ids(
    world_id: str,
    arc_id: str,
) -> None:
    contracts = _contracts()
    with pytest.raises((TypeError, ValueError)):
        contracts.WorldRefV1(world_id=world_id, arc_id=arc_id)

    valid = contracts.WorldRefV1(world_id="world.main", arc_id="arc.main")
    assert is_dataclass(valid)
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        _assign_arc_id(valid)


def test_schedule_proposal_is_frozen_canonical_deterministic_and_evidence_free() -> None:
    contracts = _contracts()
    first = _schedule_proposal()
    second = _schedule_proposal()

    assert isinstance(first, contracts.WorldbookEventProposalV1)
    assert first == second
    assert first.proposal_id == second.proposal_id
    assert first.proposal_sha256 == second.proposal_sha256
    assert len(first.proposal_sha256) == 64
    assert len(first.proposal_id) <= 120
    assert first.world_ref == contracts.WorldRefV1("omubot.default", "arc.schedule.main")
    assert _enum_value(first.source) == "schedule"
    assert first.status == "proposal"
    assert first.evidence == ()
    assert isinstance(first.event, EventRecord)
    assert first.event.status == "proposal"
    assert first.event.arc_id == "arc.schedule.main"
    assert first.event.summary == "Prepare the rehearsal room."
    assert first.event.evidence_refs == ()
    assert _enum_value(first.event.source) == "schedule"
    serialized = _serialized(first)
    assert "user:qq:" not in serialized
    assert "group:qq:" not in serialized
    assert "social_evidence" not in serialized
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        first.status = "committed"
    with pytest.raises((TypeError, ValueError)):
        replace(first, status="committed")
    with pytest.raises((TypeError, ValueError)):
        replace(first, proposal_sha256="0" * 64)


@pytest.mark.parametrize("schedule_date", ["", "2026-7-23", "23-07-2026", "2026-02-30"])
def test_schedule_proposal_requires_a_real_iso_calendar_date(schedule_date: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        _schedule_proposal(schedule_date=schedule_date)


def test_schedule_proposal_rejects_blank_summary_and_binds_all_identity_dimensions() -> None:
    with pytest.raises((TypeError, ValueError)):
        _schedule_proposal(summary="   ")

    baseline = _schedule_proposal()
    changed = (
        _schedule_proposal(world_id="world.other"),
        _schedule_proposal(target_arc_id="arc.schedule.side"),
        _schedule_proposal(schedule_date="2026-07-24"),
    )
    assert all(item.proposal_id != baseline.proposal_id for item in changed)
    assert all(item.event.event_id != baseline.event.event_id for item in changed)


def test_social_proposal_is_exact_scope_evidence_bound_and_never_contains_raw_text() -> None:
    contracts = _contracts()
    proposal = _social_proposal()

    assert isinstance(proposal, contracts.WorldbookEventProposalV1)
    assert proposal.world_ref == contracts.WorldRefV1("world.main", "arc.social.main")
    assert _enum_value(proposal.source) == "social_evidence"
    assert proposal.status == "proposal"
    assert proposal.event.status == "proposal"
    assert proposal.event.arc_id == "arc.social.main"
    assert _enum_value(proposal.event.source) == "social_evidence"
    assert proposal.evidence
    assert isinstance(proposal.evidence, tuple)
    evidence = proposal.evidence[0]
    assert isinstance(evidence, memory_contracts().EvidenceAtomV1)
    assert evidence.actor_ref == "user:qq:100"
    assert evidence.occurred_at == EVIDENCE_AT
    assert evidence.quote.strip()
    assert tuple(atom.evidence_ref for atom in proposal.evidence) == proposal.event.evidence_refs
    serialized = _serialized(proposal)
    assert RAW_USER not in serialized
    assert RAW_BOT not in serialized
    assert OFFLINE_BEHAVIOR not in serialized


def memory_contracts() -> ModuleType:
    return importlib.import_module("services.memory.governance_contracts")


@pytest.mark.parametrize(
    ("record", "scope_overrides"),
    [
        (_social_record(status="invalidated"), {}),
        (_social_record(entity_kind="fiction"), {}),
        (_social_record(group_id=""), {}),
        (_social_record(evidence_message_id=""), {}),
        (_social_record(evidence_source="   "), {}),
        (_social_record(evidence_time="2026-07-22T11:30:00"), {}),
        (_social_record(), {"current_group_id": "201"}),
        (_social_record(), {"current_user_id": "101"}),
    ],
    ids=[
        "invalidated",
        "non-factual",
        "private-or-missing-group",
        "missing-message-evidence",
        "missing-evidence-source",
        "naive-evidence-time",
        "cross-group",
        "cross-user",
    ],
)
def test_social_proposal_rejects_inactive_private_cross_scope_or_incomplete_evidence(
    record: SocialExperience,
    scope_overrides: dict[str, str],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _social_proposal(record, **scope_overrides)


def test_social_proposal_identity_cannot_collide_across_trust_dimensions() -> None:
    baseline = _social_proposal()
    changed = (
        _social_proposal(world_id="world.other"),
        _social_proposal(target_arc_id="arc.social.side"),
        _social_proposal(_social_record(group_id="201"), current_group_id="201"),
        _social_proposal(_social_record(user_id="101"), current_user_id="101"),
        _social_proposal(_social_record(evidence_message_id="7002")),
        _social_proposal(_social_record(evidence_time="2026-07-22T11:31:00+08:00")),
        _social_proposal(_social_record(evidence_source="manual_review")),
    )
    assert _social_proposal() == baseline
    assert all(item.proposal_id != baseline.proposal_id for item in changed)
    assert all(item.event.event_id != baseline.event.event_id for item in changed)


@pytest.mark.parametrize(
    "dimension",
    ["group_id", "evidence_message_id", "evidence_source"],
)
def test_social_source_binding_splice_rejects_mismatched_baseline_evidence(
    dimension: str,
) -> None:
    contracts = _contracts()
    baseline = _social_proposal()
    if dimension == "group_id":
        altered = _social_proposal(
            _social_record(group_id="201"),
            current_group_id="201",
        )
    elif dimension == "evidence_message_id":
        altered = _social_proposal(_social_record(evidence_message_id="7002"))
    else:
        altered = _social_proposal(_social_record(evidence_source="manual_review"))

    before = _serialized(baseline)
    baseline_evidence = baseline.evidence
    baseline_source_ref = baseline.source_ref
    assert isinstance(altered, contracts.WorldbookEventProposalV1)
    assert altered.source_binding != baseline.source_binding
    assert altered.event.event_id != baseline.event.event_id
    with pytest.raises((TypeError, ValueError)):
        replace(
            baseline,
            source_binding=altered.source_binding,
            event=replace(
                baseline.event,
                event_id=altered.event.event_id,
            ),
        )

    assert _serialized(baseline) == before
    assert baseline.evidence == baseline_evidence
    assert baseline.source_ref == baseline_source_ref


def test_proposal_replace_rejects_a_different_canonical_event_id() -> None:
    proposal = _schedule_proposal()
    before = _serialized(proposal)

    with pytest.raises((TypeError, ValueError)):
        replace(
            proposal,
            event=replace(
                proposal.event,
                event_id="wevt_schedule_other_canonical_event",
            ),
        )

    assert _serialized(proposal) == before


@pytest.mark.parametrize("field", ["step", "recovery_steps"])
def test_integer_metadata_bool_contract_proposal_rejects_boolean(
    field: str,
) -> None:
    proposal = _schedule_proposal()
    integer_event = replace(proposal.event, **{field: 0})
    integer_proposal = replace(proposal, event=integer_event)
    before = _serialized(proposal)

    assert type(getattr(integer_proposal.event, field)) is int
    assert getattr(integer_proposal.event, field) == 0
    with pytest.raises((TypeError, ValueError)):
        replace(
            proposal,
            event=replace(proposal.event, **{field: False}),
        )

    assert _serialized(proposal) == before


def test_social_proposal_replace_rejects_raw_evidence_even_with_synced_event_refs() -> None:
    contracts = memory_contracts()
    proposal = _social_proposal()
    before = _serialized(proposal)
    forged_evidence = replace(
        proposal.evidence[0],
        quote=RAW_USER,
        content_sha256=contracts.sha256_text(RAW_USER),
    )
    forged_event = replace(
        proposal.event,
        evidence_refs=(forged_evidence.evidence_ref,),
    )

    with pytest.raises((TypeError, ValueError)):
        replace(
            proposal,
            evidence=(forged_evidence,),
            event=forged_event,
        )

    assert _serialized(proposal) == before


def test_social_proposal_replace_rejects_source_ref_evidence_ref_mismatch() -> None:
    proposal = _social_proposal()
    before = _serialized(proposal)

    with pytest.raises((TypeError, ValueError)):
        replace(
            proposal,
            source_ref="social_experience:social.exp.other",
        )

    assert _serialized(proposal) == before


@pytest.mark.parametrize(
    "variable_deltas",
    [
        {"social_resonance": {"nested": [0.05]}},
        {"social_resonance": 0.06},
    ],
    ids=["nested-mutable-input", "non-fixed-effect"],
)
def test_social_proposal_replace_rejects_nonclosed_effects(
    variable_deltas: dict[str, Any],
) -> None:
    proposal = _social_proposal()
    before = _serialized(proposal)

    with pytest.raises((TypeError, ValueError)):
        replace(
            proposal,
            event=replace(
                proposal.event,
                variable_deltas=variable_deltas,
            ),
        )

    assert _serialized(proposal) == before


@pytest.mark.parametrize(
    ("field", "token"),
    [
        ("summary", "user:qq:100"),
        ("summary", "group:qq:200"),
        ("summary", "social_evidence"),
        ("source_ref", "user:qq:100"),
        ("source_ref", "group:qq:200"),
        ("source_ref", "social_evidence"),
    ],
)
def test_schedule_proposal_replace_rejects_social_scope_token_injection(
    field: str,
    token: str,
) -> None:
    proposal = _schedule_proposal()
    before = _serialized(proposal)

    with pytest.raises((TypeError, ValueError)):
        if field == "summary":
            replace(
                proposal,
                event=replace(
                    proposal.event,
                    summary=f"Prepare the rehearsal room. {token}",
                ),
            )
        else:
            replace(proposal, source_ref=f"schedule:2026-07-23:{token}")

    assert _serialized(proposal) == before


@pytest.mark.parametrize(
    "carrier",
    [
        "social_experience:social.exp.1 user_id=100 group_id=200 RAW_USER",
        "user_text=RAW_USER",
        "bot_reply=RAW_BOT",
        "evidence_message_id=7001",
        "message_pk:7001",
        "quote=ordinary",
        "occurred_at=2026-07-22T03:30:00Z",
        "user:qq:100",
        "group:qq:200",
    ],
    ids=[
        "reviewer-exact-payload",
        "user-text-field",
        "bot-reply-field",
        "evidence-message-id-field",
        "message-pk-prefix",
        "quote-field",
        "occurred-at-field",
        "user-qq-prefix",
        "group-qq-prefix",
    ],
)
def test_schedule_builder_rejects_structured_social_carrier_vocabulary(
    carrier: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    values: dict[str, Any] = {
        "world_id": "omubot.default",
        "target_arc_id": "arc.schedule.main",
        "schedule_date": "2026-07-23",
        "summary": f"Prepare the rehearsal room. Source note: {carrier}",
        "proposed_at": PROPOSED_AT,
    }
    before = _serialized(values)

    legal = _adapters().build_schedule_event_proposal(
        **(values | {"summary": "Prepare the rehearsal room after lunch."})
    )
    assert legal.event.summary == "Prepare the rehearsal room after lunch."
    with pytest.raises((TypeError, ValueError)):
        _adapters().build_schedule_event_proposal(**values)

    assert _serialized(values) == before
    assert list(tmp_path.iterdir()) == []


def test_source_binding_identity_contract_exposes_private_structured_social_binding() -> None:
    proposal = _social_proposal()
    binding = proposal.source_binding

    assert is_dataclass(binding) or isinstance(binding, Mapping)
    serialized = _serialized(binding)
    for trusted_dimension in (
        "social.exp.7001",
        "200",
        "100",
        "7001",
        "reply_context",
    ):
        assert trusted_dimension in serialized
    assert "2026-07-22" in serialized
    assert "11:30:00+08:00" in serialized or "03:30:00+00:00" in serialized
    assert RAW_USER not in serialized
    assert RAW_BOT not in serialized
    assert OFFLINE_BEHAVIOR not in serialized
    _assert_deeply_immutable(binding)


def test_source_binding_identity_contract_digest_and_event_id_cannot_be_forged() -> None:
    contracts = _contracts()
    proposal = _social_proposal()
    digest_field = next(
        field for field in fields(proposal) if field.name == "source_binding_sha256"
    )
    forged_digest = "f" * 64
    deterministic_id = getattr(contracts, "deterministic_world_event_id", None)

    if callable(deterministic_id):
        forged_event_id = deterministic_id(
            source=proposal.source,
            world_ref=proposal.world_ref,
            source_binding_sha256=forged_digest,
        )
        with pytest.raises((TypeError, ValueError)):
            replace(
                proposal,
                source_binding_sha256=forged_digest,
                event=replace(proposal.event, event_id=forged_event_id),
            )
    else:
        with pytest.raises((TypeError, ValueError)):
            replace(proposal, source_binding_sha256=forged_digest)

    assert digest_field.init is False


def test_source_binding_identity_contract_schedule_summary_changes_stable_ids() -> None:
    first = _schedule_proposal(summary="Prepare the rehearsal room.")
    changed = _schedule_proposal(summary="Prepare a different rehearsal room.")

    assert first.event.event_id != changed.event.event_id
    assert first.proposal_id != changed.proposal_id


def test_source_binding_identity_contract_schedule_time_does_not_change_stable_ids() -> None:
    first = _schedule_proposal(proposed_at=PROPOSED_AT)
    later = _schedule_proposal(proposed_at=PROPOSED_AT + timedelta(hours=1))

    assert first.event.event_id == later.event.event_id
    assert first.proposal_id == later.proposal_id
    assert first.proposal_sha256 != later.proposal_sha256


def test_proposal_adapters_are_pure_and_imports_create_no_default_storage(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = str(repo_root)
    if existing_pythonpath:
        pythonpath = os.pathsep.join((pythonpath, existing_pythonpath))
    script = """
import inspect
from datetime import UTC, datetime

from services.social_narrative import SocialExperience
from services.worldbook import governance_contracts
from services.worldbook import governed_adapters as adapters

for name in ("build_social_event_proposal", "build_schedule_event_proposal"):
    function = getattr(adapters, name)
    assert not inspect.iscoroutinefunction(function)
    assert not set(inspect.signature(function).parameters).intersection(
        {"store", "runtime", "db", "db_path", "governance_store"}
    )

proposed_at = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
adapters.build_schedule_event_proposal(
    world_id="omubot.default",
    target_arc_id="arc.schedule.main",
    schedule_date="2026-07-23",
    summary="Prepare the rehearsal room.",
    proposed_at=proposed_at,
)
record = SocialExperience(
    experience_id="social.exp.7001",
    group_id="200",
    user_id="100",
    entity_kind="factual",
    evidence_message_id="7001",
    evidence_time="2026-07-22T11:30:00+08:00",
    evidence_source="reply_context",
    user_text="offline behavior",
    bot_reply="acknowledged",
    status="active",
    created_at="2026-07-22T11:31:00+08:00",
)
adapters.build_social_event_proposal(
    record=record,
    world_id="world.main",
    target_arc_id="arc.social.main",
    current_group_id="200",
    current_user_id="100",
    proposed_at=proposed_at,
)
assert governance_contracts.WorldbookEventProposalV1 is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": pythonpath},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_receipt_proof_contract_rejects_direct_public_construction() -> None:
    receipt, _proposal, _committed, _arc, _committed_ids = _verified_receipt_fixture()
    receipt_type = type(receipt)
    constructor_kwargs = {
        name: getattr(receipt, name)
        for name, parameter in inspect.signature(receipt_type).parameters.items()
        if parameter.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        and hasattr(receipt, name)
    }

    with pytest.raises((TypeError, ValueError)):
        receipt_type(**constructor_kwargs)


def test_receipt_api_exposes_no_callable_digest_accepting_factory() -> None:
    receipt, _proposal, _committed, _arc, _committed_ids = _verified_receipt_fixture()
    receipt_type = type(receipt)
    contracts = _contracts()

    assert isinstance(receipt, contracts.WorldbookCommitReceiptV1)
    with pytest.raises((TypeError, ValueError)):
        receipt_type()

    digest_accepting_factories: dict[str, tuple[str, ...]] = {}
    for name in vars(receipt_type):
        if name.startswith("__"):
            continue
        member = getattr(receipt_type, name)
        if not callable(member):
            continue
        try:
            parameters = inspect.signature(member).parameters
        except (TypeError, ValueError):
            continue
        proof_parameters = tuple(
            parameter_name
            for parameter_name in parameters
            if "sha256" in parameter_name or "digest" in parameter_name
        )
        if proof_parameters:
            digest_accepting_factories[name] = proof_parameters

    assert digest_accepting_factories == {}


def test_receipt_proof_contract_exposes_event_arc_and_durable_id_digests() -> None:
    receipt, _proposal, _committed, _arc, _committed_ids = _verified_receipt_fixture()
    digest_names = {
        field.name
        for field in fields(receipt)
        if field.name.endswith("_sha256")
    }
    semantic_digest_names = {
        "event": {
            name
            for name in digest_names
            if "event" in name
            and "event_ids" not in name
            and "committed_ids" not in name
        },
        "arc": {name for name in digest_names if "arc" in name},
        "durable_event_ids": {
            name
            for name in digest_names
            if "event_ids" in name or "committed_ids" in name
        },
    }

    for dimension, names in semantic_digest_names.items():
        assert names, f"receipt must expose a digest for {dimension}"
        for name in names:
            value = getattr(receipt, name)
            assert isinstance(value, str)
            assert len(value) == 64
            int(value, 16)
    assert len(receipt.receipt_sha256) == 64


def test_receipt_proof_contract_is_deeply_immutable_without_event_payload() -> None:
    receipt, _proposal, _committed, _arc, _committed_ids = _verified_receipt_fixture()

    assert not any(
        isinstance(getattr(receipt, field.name), EventRecord)
        for field in fields(receipt)
    )
    assert not any(
        isinstance(getattr(receipt, field.name), (dict, list, set, bytearray))
        for field in fields(receipt)
    )
    _assert_deeply_immutable(receipt)


def test_receipt_proof_contract_replace_cannot_forge_digest_or_revision() -> None:
    receipt, _proposal, _committed, _arc, _committed_ids = _verified_receipt_fixture()
    proof_fields = [
        field
        for field in fields(receipt)
        if field.name.endswith("_sha256") or field.name == "persisted_revision"
    ]

    assert proof_fields
    for field in proof_fields:
        forged_value: object = (
            receipt.persisted_revision + 1
            if field.name == "persisted_revision"
            else "f" * 64
        )
        assert forged_value != getattr(receipt, field.name)
        with pytest.raises((TypeError, ValueError)):
            replace(receipt, **{field.name: forged_value})


def test_verified_commit_receipt_requires_durable_exact_event_and_revision() -> None:
    contracts = _contracts()
    proposal = _schedule_proposal()
    committed, arc, committed_ids = _committed_fixture(proposal)
    before = (_serialized(proposal), _serialized(committed), _serialized(arc.to_dict()))

    receipt = _adapters().verify_committed_world_event(
        proposal,
        committed_event=committed,
        committed_arc_snapshot=arc.to_dict(),
        committed_event_ids=committed_ids,
        persisted_revision=arc.revision,
    )

    assert isinstance(receipt, contracts.WorldbookCommitReceiptV1)
    assert receipt.world_ref == proposal.world_ref
    assert receipt.proposal_id == proposal.proposal_id
    assert receipt.proposal_sha256 == proposal.proposal_sha256
    assert receipt.event_id == committed.event_id
    assert receipt.arc_id == arc.arc_id
    assert receipt.persisted_revision == arc.revision
    assert len(receipt.persisted_event_sha256) == 64
    assert len(receipt.receipt_sha256) == 64
    assert before == (
        _serialized(proposal),
        _serialized(committed),
        _serialized(arc.to_dict()),
    )
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        receipt.persisted_revision = 99
    with pytest.raises((TypeError, ValueError)):
        replace(receipt, persisted_event_sha256="0" * 64)


def test_verify_accepts_real_reducer_legacy_history_projection() -> None:
    contracts = _contracts()
    proposal = _schedule_proposal()
    committed_input = replace(proposal.event, status="committed")
    arc = StoryArc(
        arc_id=proposal.world_ref.arc_id,
        revision=1,
        event_budget={},
        event_history=[],
        last_events=[],
    )

    committed_event = EventReducer().apply(arc, committed_input, now_step=7)
    snapshot = arc.to_dict()
    committed_ids = tuple(arc.event_budget["committed_event_ids"])
    history_row = next(
        row for row in arc.event_history if row["event_id"] == committed_event.event_id
    )
    before = (
        _serialized(proposal),
        _serialized(committed_event),
        _serialized(snapshot),
        committed_ids,
        arc.revision,
    )

    assert isinstance(committed_event, EventRecord)
    assert arc.revision > 0
    assert "status" not in history_row
    assert "recovery_steps" not in history_row
    try:
        receipt = _adapters().verify_committed_world_event(
            proposal,
            committed_event=committed_event,
            committed_arc_snapshot=snapshot,
            committed_event_ids=committed_ids,
            persisted_revision=arc.revision,
        )
    finally:
        assert before == (
            _serialized(proposal),
            _serialized(committed_event),
            _serialized(snapshot),
            committed_ids,
            arc.revision,
        )

    assert isinstance(receipt, contracts.WorldbookCommitReceiptV1)
    assert receipt.event_id == committed_event.event_id
    assert receipt.persisted_revision == arc.revision


def test_verifier_rejects_boolean_snapshot_revision_without_mutation() -> None:
    contracts = _contracts()
    proposal = _schedule_proposal()
    committed, arc, committed_ids = _committed_fixture(proposal)
    valid_snapshot = json.loads(json.dumps(arc.to_dict()))
    valid_snapshot["revision"] = 1

    valid_receipt = _adapters().verify_committed_world_event(
        proposal,
        committed_event=committed,
        committed_arc_snapshot=valid_snapshot,
        committed_event_ids=committed_ids,
        persisted_revision=1,
    )
    assert isinstance(valid_receipt, contracts.WorldbookCommitReceiptV1)
    assert valid_receipt.persisted_revision == 1

    boolean_snapshot = json.loads(json.dumps(valid_snapshot))
    boolean_snapshot["revision"] = True
    before = (
        _serialized(proposal),
        _serialized(committed),
        _serialized(boolean_snapshot),
        committed_ids,
    )
    try:
        with pytest.raises((TypeError, ValueError)):
            _adapters().verify_committed_world_event(
                proposal,
                committed_event=committed,
                committed_arc_snapshot=boolean_snapshot,
                committed_event_ids=committed_ids,
                persisted_revision=1,
            )
    finally:
        assert before == (
            _serialized(proposal),
            _serialized(committed),
            _serialized(boolean_snapshot),
            committed_ids,
        )


def test_verifier_rejects_conflicting_explicit_snapshot_world_without_mutation() -> None:
    contracts = _contracts()
    proposal = _schedule_proposal()
    committed, arc, committed_ids = _committed_fixture(proposal)
    legacy_snapshot = json.loads(json.dumps(arc.to_dict()))

    assert "world_id" not in legacy_snapshot
    legacy_receipt = _adapters().verify_committed_world_event(
        proposal,
        committed_event=committed,
        committed_arc_snapshot=legacy_snapshot,
        committed_event_ids=committed_ids,
        persisted_revision=arc.revision,
    )
    assert isinstance(legacy_receipt, contracts.WorldbookCommitReceiptV1)

    matching_snapshot = json.loads(json.dumps(legacy_snapshot))
    matching_snapshot["world_id"] = "omubot.default"
    matching_receipt = _adapters().verify_committed_world_event(
        proposal,
        committed_event=committed,
        committed_arc_snapshot=matching_snapshot,
        committed_event_ids=committed_ids,
        persisted_revision=arc.revision,
    )
    assert isinstance(matching_receipt, contracts.WorldbookCommitReceiptV1)

    conflicting_snapshot = json.loads(json.dumps(legacy_snapshot))
    conflicting_snapshot["world_id"] = "world.other"
    before = (
        _serialized(proposal),
        _serialized(committed),
        _serialized(conflicting_snapshot),
        committed_ids,
    )
    try:
        with pytest.raises((TypeError, ValueError)):
            _adapters().verify_committed_world_event(
                proposal,
                committed_event=committed,
                committed_arc_snapshot=conflicting_snapshot,
                committed_event_ids=committed_ids,
                persisted_revision=arc.revision,
            )
    finally:
        assert before == (
            _serialized(proposal),
            _serialized(committed),
            _serialized(conflicting_snapshot),
            committed_ids,
        )


def test_legacy_arc_snapshot_only_verifies_the_default_singleton_world() -> None:
    contracts = _contracts()
    other_world = contracts.WorldRefV1("world.other", "arc.schedule.main")
    proposal = _schedule_proposal(world_id=other_world.world_id)
    committed, arc, committed_ids = _committed_fixture(proposal)
    snapshot = arc.to_dict()
    before = (
        _serialized(proposal),
        _serialized(committed),
        _serialized(snapshot),
        committed_ids,
    )

    assert proposal.world_ref == other_world
    with pytest.raises((TypeError, ValueError)):
        _adapters().verify_committed_world_event(
            proposal,
            committed_event=committed,
            committed_arc_snapshot=snapshot,
            committed_event_ids=committed_ids,
            persisted_revision=arc.revision,
        )

    assert before == (
        _serialized(proposal),
        _serialized(committed),
        _serialized(snapshot),
        committed_ids,
    )
    assert contracts.LEGACY_SINGLETON_WORLD_ID == "omubot.default"


@pytest.mark.parametrize("field", ["step", "recovery_steps"])
def test_integer_metadata_bool_contract_verifier_rejects_synchronized_boolean_event(
    field: str,
) -> None:
    contracts = _contracts()
    proposal = _schedule_proposal()
    committed, arc, committed_ids = _committed_fixture(proposal)
    integer_event = replace(committed, **{field: 0})
    integer_snapshot = json.loads(json.dumps(arc.to_dict()))
    if field == "step":
        for collection in ("event_history", "last_events"):
            for row in integer_snapshot[collection]:
                if row["event_id"] == integer_event.event_id:
                    row["step"] = 0

    integer_receipt = _adapters().verify_committed_world_event(
        proposal,
        committed_event=integer_event,
        committed_arc_snapshot=integer_snapshot,
        committed_event_ids=committed_ids,
        persisted_revision=arc.revision,
    )
    assert isinstance(integer_receipt, contracts.WorldbookCommitReceiptV1)
    assert type(getattr(integer_event, field)) is int

    boolean_event = replace(integer_event, **{field: False})
    boolean_snapshot = json.loads(json.dumps(integer_snapshot))
    if field == "step":
        for collection in ("event_history", "last_events"):
            for row in boolean_snapshot[collection]:
                if row["event_id"] == boolean_event.event_id:
                    row["step"] = False
    before = (
        _serialized(proposal),
        _serialized(boolean_event),
        _serialized(boolean_snapshot),
        committed_ids,
    )
    try:
        with pytest.raises((TypeError, ValueError)):
            _adapters().verify_committed_world_event(
                proposal,
                committed_event=boolean_event,
                committed_arc_snapshot=boolean_snapshot,
                committed_event_ids=committed_ids,
                persisted_revision=arc.revision,
            )
    finally:
        assert before == (
            _serialized(proposal),
            _serialized(boolean_event),
            _serialized(boolean_snapshot),
            committed_ids,
        )


def test_commit_verification_fails_closed_on_uncommitted_mismatch_or_missing_durability() -> None:
    proposal = _schedule_proposal()
    committed, arc, committed_ids = _committed_fixture(proposal)
    adapters = _adapters()

    invalid_calls = (
        {
            "committed_event": proposal.event,
            "committed_arc_snapshot": arc.to_dict(),
            "committed_event_ids": committed_ids,
            "persisted_revision": arc.revision,
        },
        {
            "committed_event": replace(committed, summary="A different persisted payload."),
            "committed_arc_snapshot": arc.to_dict(),
            "committed_event_ids": committed_ids,
            "persisted_revision": arc.revision,
        },
        {
            "committed_event": committed,
            "committed_arc_snapshot": replace(arc, arc_id="arc.other").to_dict(),
            "committed_event_ids": committed_ids,
            "persisted_revision": arc.revision,
        },
        {
            "committed_event": committed,
            "committed_arc_snapshot": arc.to_dict(),
            "committed_event_ids": (),
            "persisted_revision": arc.revision,
        },
        {
            "committed_event": committed,
            "committed_arc_snapshot": replace(
                arc,
                event_history=[],
                last_events=[],
            ).to_dict(),
            "committed_event_ids": committed_ids,
            "persisted_revision": arc.revision,
        },
        {
            "committed_event": committed,
            "committed_arc_snapshot": replace(
                arc,
                event_budget={"committed_event_ids": []},
            ).to_dict(),
            "committed_event_ids": committed_ids,
            "persisted_revision": arc.revision,
        },
        {
            "committed_event": committed,
            "committed_arc_snapshot": arc.to_dict(),
            "committed_event_ids": committed_ids,
            "persisted_revision": arc.revision + 1,
        },
    )
    for kwargs in invalid_calls:
        with pytest.raises((TypeError, ValueError)):
            adapters.verify_committed_world_event(proposal, **kwargs)
