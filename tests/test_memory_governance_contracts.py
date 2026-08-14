"""Behavioral contract tests for immutable memory governance v1 values."""

from __future__ import annotations

import importlib
import importlib.util
import math
from dataclasses import FrozenInstanceError, is_dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from types import ModuleType
from typing import Any

import pytest

T0 = datetime(2026, 7, 20, 4, 30, tzinfo=UTC)


def _contracts() -> ModuleType:
    spec = importlib.util.find_spec("services.memory.governance_contracts")
    assert spec is not None, "services.memory.governance_contracts v1 is required"
    return importlib.import_module("services.memory.governance_contracts")


def _values(enum_type: Any) -> set[str]:
    return {str(member.value) for member in enum_type}


def _evidence(c: ModuleType, quote: str = "I prefer jasmine tea", *, suffix: str = "1") -> Any:
    return c.EvidenceAtomV1(
        evidence_ref=f"message:onebot:group:9001:msg-{suffix}",
        content_sha256=c.sha256_text(quote),
        quote=quote,
        actor_ref="user:qq:42",
        occurred_at=T0,
    )


def _observation_kwargs(c: ModuleType, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "source_kind": "user_statement",
        "producer_kind": "memo",
        "producer_version": "memo-v2",
        "producer_run_id": "run-7",
        "subject_ref": "user:qq:42",
        "owner_scope": "user",
        "owner_id": "42",
        "visibility": "same_group",
        "origin_group_ref": "group:qq:9001",
        "claim": c.CardClaimV1(category="preference", content="likes jasmine tea"),
        "evidence": (_evidence(c),),
        "observed_at": T0 + timedelta(minutes=2),
        "source_occurred_at": T0,
        "time_basis": "source_event",
        "valid_from": None,
        "valid_to": None,
        "confidence": 0.85,
    }
    values.update(overrides)
    return values


def _observation(c: ModuleType, **overrides: Any) -> Any:
    return c.ObservationV1(**_observation_kwargs(c, **overrides))


def _candidate(c: ModuleType, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "observation": _observation(c),
        "proposal": c.ProjectionProposalV1.create(
            projection_kind="card",
            operation="create",
            payload={"category": "preference", "content": "likes jasmine tea"},
        ),
        "producer_kind": "memo",
        "producer_run_id": "run-7",
        "producer_item_id": "decision-0",
        "produced_at": T0 + timedelta(minutes=3),
        "model_output": '{"action":"add"}',
    }
    values.update(overrides)
    return c.CandidateEnvelopeV1.create(**values)


def _promotion_event(c: ModuleType, candidate: Any, event_kind: str, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "candidate_id": candidate.candidate_id,
        "candidate_sha256": candidate.candidate_sha256,
        "event_kind": event_kind,
        "actor_kind": "policy",
        "actor_ref": "policy:memory-v1",
        "occurred_at": T0 + timedelta(minutes=4),
        "reason_code": "contract_test",
        "operator_note": "",
        "conflict_ids": (),
        "projection_kind": "card",
        "operation": "create",
        "projection_ref": None,
        "receipt_ref": None,
    }
    values.update(overrides)
    return c.PromotionEventV1.create(**values)


def test_v1_closed_value_sets_are_exact() -> None:
    c = _contracts()

    expected = {
        "SourceKind": {
            "user_statement", "conversation_inference", "system_event",
            "operator_assertion", "migration",
        },
        "ProducerKind": {"memo", "compaction", "consolidator", "manual", "migration"},
        "OwnerScope": {"user", "group", "global", "world"},
        "Visibility": {"private", "same_group", "global"},
        "TimeBasis": {"source_event", "producer_clock", "unknown"},
        "ProjectionKind": {"card", "slang", "style", "episode", "graph_relation"},
        "Operation": {"create", "reinforce", "supersede", "expire"},
        "ConflictKind": {
            "duplicate", "contradiction", "ambiguous_supersession", "subject_mismatch",
            "visibility_mismatch", "temporal_overlap",
        },
        "PromotionKind": {
            "review_queued", "promotion_approved", "promotion_rejected",
            "conflict_resolved", "projection_applied", "projection_failed",
        },
        "ActorKind": {"policy", "operator", "migration", "projector"},
    }
    assert {name: _values(getattr(c, name)) for name in expected} == expected


def test_claim_variants_keep_domain_fields_frozen_and_tuple_backed() -> None:
    c = _contracts()

    claims = (
        c.CardClaimV1(category="fact", content="user likes tea"),
        c.FactClaimV1(subject="user:qq:42", predicate="likes", object_value="tea"),
        c.SlangClaimV1(term="yyds", meaning="excellent", aliases=("YYDS",)),
        c.StyleClaimV1(expression="short replies", situation="casual chat"),
        c.EpisodeClaimV1(situation="tea discussion", action_taken="recommended jasmine"),
        c.GraphRelationClaimV1(
            subject_node="user:qq:42", predicate="likes", object_node="concept:global:world:tea"
        ),
    )

    assert all(is_dataclass(claim) for claim in claims)
    assert claims[2].aliases == ("YYDS",)
    assert isinstance(claims[2].aliases, tuple)
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        claims[0].content = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        c.FactClaimV1(subject="user:qq:42", predicate="likes")
    with pytest.raises(ValueError):
        c.SlangClaimV1(term="x", meaning="y", repeat_policy="invented")


def test_evidence_requires_canonical_ref_matching_digest_bounded_quote_and_aware_time() -> None:
    c = _contracts()
    local_time = datetime(2026, 7, 20, 12, 30, tzinfo=timezone(timedelta(hours=8)))
    atom = c.EvidenceAtomV1(
        evidence_ref="message:onebot:private:42:msg-1",
        content_sha256=c.sha256_text("raw user text"),
        quote="raw user text",
        actor_ref="user:qq:0042",
        occurred_at=local_time,
    )

    assert atom.actor_ref == "user:qq:42"
    assert atom.occurred_at == T0
    assert atom.content_sha256 == c.sha256_text(atom.quote)
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        atom.quote = "changed"  # type: ignore[misc]

    invalid = (
        {"evidence_ref": "legacy-row-1"},
        {"evidence_ref": "legacy:row-1"},
        {"content_sha256": "0" * 64},
        {"quote": "", "content_sha256": c.sha256_text("")},
        {"quote": "x" * 1_000_000, "content_sha256": c.sha256_text("x" * 1_000_000)},
        {"occurred_at": datetime(2026, 7, 20, 4, 30)},
    )
    base = {
        "evidence_ref": "message:onebot:private:42:msg-1",
        "content_sha256": c.sha256_text("raw user text"),
        "quote": "raw user text",
        "actor_ref": "user:qq:42",
        "occurred_at": T0,
    }
    for override in invalid:
        with pytest.raises((TypeError, ValueError)):
            c.EvidenceAtomV1(**(base | override))


def test_observation_normalizes_equivalent_inputs_and_evidence_order_into_one_identity() -> None:
    c = _contracts()
    first = _evidence(c, "first", suffix="2")
    second = _evidence(c, "second", suffix="1")
    local_observed = (T0 + timedelta(minutes=2)).astimezone(timezone(timedelta(hours=8)))

    a = _observation(c, evidence=(first, second))
    b = _observation(
        c,
        subject_ref="user:qq:0042",
        owner_id="0042",
        origin_group_ref="group:qq:09001",
        evidence=(second, first),
        observed_at=local_observed,
    )

    assert a == b
    assert a.observation_id == b.observation_id
    assert a.observation_sha256 == b.observation_sha256
    assert len(a.observation_sha256) == 64
    assert a.subject_ref == "user:qq:42"
    assert a.owner_id == "42"
    assert a.origin_group_ref == "group:qq:9001"
    assert isinstance(a.evidence, tuple)
    assert tuple(atom.evidence_ref for atom in a.evidence) == tuple(
        sorted((first.evidence_ref, second.evidence_ref))
    )
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        a.confidence = 0.1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_kind", "web_guess"),
        ("producer_kind", "agent"),
        ("owner_scope", "tenant"),
        ("visibility", "friends"),
        ("time_basis", "local_clock"),
        ("subject_ref", "42"),
        ("subject_ref", "user:qq:not-numeric"),
        ("observed_at", datetime(2026, 7, 20, 4, 32)),
        ("confidence", True),
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("confidence", math.nan),
        ("confidence", math.inf),
    ],
)
def test_observation_rejects_open_values_unsafe_subjects_naive_time_and_bad_confidence(
    field: str,
    value: object,
) -> None:
    c = _contracts()
    with pytest.raises((TypeError, ValueError)):
        _observation(c, **{field: value})


def test_observation_enforces_evidence_time_validity_and_visibility_policy() -> None:
    c = _contracts()

    invalid_overrides = (
        {"evidence": ()},
        {"time_basis": "source_event", "source_occurred_at": None},
        {"time_basis": "unknown", "source_occurred_at": T0},
        {"valid_from": T0 + timedelta(days=2), "valid_to": T0 + timedelta(days=1)},
        {"valid_from": T0, "valid_to": T0},
        {"visibility": "same_group", "origin_group_ref": None},
        {"owner_scope": "group", "owner_id": "9001", "visibility": "private"},
        {"owner_scope": "global", "owner_id": "global", "visibility": "private"},
        {"owner_scope": "world", "owner_id": "world", "visibility": "private"},
        {"visibility": "global"},
    )
    for override in invalid_overrides:
        with pytest.raises((TypeError, ValueError)):
            _observation(c, **override)

    manual_global = _observation(
        c,
        source_kind="operator_assertion",
        producer_kind="manual",
        visibility="global",
    )
    visibility = getattr(manual_global.visibility, "value", manual_global.visibility)
    assert visibility == "global"


def test_projection_proposal_is_canonical_frozen_and_operation_target_is_strict() -> None:
    c = _contracts()
    payload = {"content": "likes tea", "category": "preference"}
    proposal = c.ProjectionProposalV1.create(
        projection_kind="card", operation="create", payload=payload
    )
    payload["content"] = "mutated after construction"

    assert dict(proposal.payload) == {"category": "preference", "content": "likes tea"}
    assert proposal.target_ref is None
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        proposal.target_ref = "card:7"  # type: ignore[misc]
    with pytest.raises(TypeError):
        proposal.payload["content"] = "mutated"  # type: ignore[index]

    with pytest.raises((TypeError, ValueError)):
        c.ProjectionProposalV1.create(
            projection_kind="card", operation="create", payload=payload, target_ref="card:7"
        )
    with pytest.raises((TypeError, ValueError)):
        c.ProjectionProposalV1.create(
            projection_kind="card", operation="reinforce", payload=payload
        )
    with pytest.raises((TypeError, ValueError)):
        c.ProjectionProposalV1.create(
            projection_kind="card", operation="supersede", payload=payload, target_ref="fact:7"
        )
    with pytest.raises((TypeError, ValueError)):
        c.ProjectionProposalV1.create(
            projection_kind="unknown", operation="create", payload=payload
        )


def test_candidate_envelope_is_shadow_only_stable_and_has_no_mutable_aliases() -> None:
    c = _contracts()
    payload = {"category": "preference", "content": "likes jasmine tea"}
    proposal = c.ProjectionProposalV1.create(
        projection_kind="card", operation="create", payload=payload
    )
    candidate = _candidate(c, proposal=proposal)
    same = _candidate(c, proposal=proposal)
    payload["content"] = "changed"

    assert candidate == same
    assert candidate.mode == "shadow"
    assert candidate.candidate_id == same.candidate_id
    assert candidate.candidate_sha256 == same.candidate_sha256
    assert candidate.idempotency_key == same.idempotency_key
    assert candidate.model_output_sha256 == c.sha256_text('{"action":"add"}')
    assert candidate.proposal.payload["content"] == "likes jasmine tea"
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        candidate.mode = "live"  # type: ignore[misc]


def test_conflict_identity_uses_sorted_unique_refs_and_has_no_resolution_slot() -> None:
    c = _contracts()
    conflict = c.ConflictV1.create(
        kind="contradiction",
        subject_ref="user:qq:0042",
        claim_key="fact:city",
        observation_ids=("obs-b", "obs-a", "obs-b"),
        candidate_ids=("cand-b", "cand-a", "cand-a"),
        existing_projection_refs=("card:2", "card:1", "card:2"),
        detected_at=T0.astimezone(timezone(timedelta(hours=8))),
        detector="deterministic",
        basis_evidence_refs=("message:two", "message:one", "message:one"),
    )

    assert conflict.subject_ref == "user:qq:42"
    assert conflict.observation_ids == ("obs-a", "obs-b")
    assert conflict.candidate_ids == ("cand-a", "cand-b")
    assert conflict.existing_projection_refs == ("card:1", "card:2")
    assert conflict.basis_evidence_refs == ("message:one", "message:two")
    assert conflict.detected_at == T0
    assert conflict.conflict_id
    assert conflict.conflict_key
    assert not hasattr(conflict, "resolution")
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        conflict.kind = "duplicate"  # type: ignore[misc]

    for observations, evidence in (("obs-a", ("message:one",)), (("obs-a", "obs-b"), ())):
        observation_ids = (observations,) if isinstance(observations, str) else observations
        with pytest.raises((TypeError, ValueError)):
            c.ConflictV1.create(
                kind="duplicate",
                subject_ref="user:qq:42",
                claim_key="fact:city",
                observation_ids=observation_ids,
                detected_at=T0,
                basis_evidence_refs=evidence,
            )


def test_promotion_events_are_append_only_hashed_and_exact_duplicates_idempotent() -> None:
    c = _contracts()
    candidate = _candidate(c)
    queued = _promotion_event(c, candidate, "review_queued")
    approved = _promotion_event(
        c, candidate, "promotion_approved", occurred_at=T0 + timedelta(minutes=5)
    )
    applied = _promotion_event(
        c,
        candidate,
        "projection_applied",
        actor_kind="projector",
        actor_ref="projector:card-v1",
        occurred_at=T0 + timedelta(minutes=6),
        projection_ref="card:77",
        receipt_ref="receipt:apply-77",
    )

    folded = c.fold_promotion_events((queued, approved, applied))
    duplicate_folded = c.fold_promotion_events((queued, queued, approved, applied, applied))
    assert folded == duplicate_folded
    assert queued.note_sha256 == c.sha256_text(queued.operator_note)
    assert queued.idempotency_key
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        queued.reason_code = "changed"  # type: ignore[misc]


def test_promotion_fold_enforces_decision_order_conflicts_and_single_receipt() -> None:
    c = _contracts()
    candidate = _candidate(c)
    approved = _promotion_event(c, candidate, "promotion_approved")
    rejected = _promotion_event(
        c, candidate, "promotion_rejected", occurred_at=T0 + timedelta(minutes=5)
    )
    applied_a = _promotion_event(
        c,
        candidate,
        "projection_applied",
        actor_kind="projector",
        actor_ref="projector:card-v1",
        occurred_at=T0 + timedelta(minutes=6),
        projection_ref="card:77",
        receipt_ref="receipt:a",
    )
    applied_b = _promotion_event(
        c,
        candidate,
        "projection_applied",
        actor_kind="projector",
        actor_ref="projector:card-v1",
        occurred_at=T0 + timedelta(minutes=7),
        projection_ref="card:77",
        receipt_ref="receipt:b",
    )

    for events in ((rejected, approved), (applied_a,), (approved, applied_a, applied_b)):
        with pytest.raises((TypeError, ValueError)):
            c.fold_promotion_events(events)

    with pytest.raises((TypeError, ValueError)):
        c.fold_promotion_events((approved,), known_conflict_ids=("conflict:a",))

    incomplete_operator = _promotion_event(
        c,
        candidate,
        "promotion_approved",
        actor_kind="operator",
        actor_ref="operator:alice",
        conflict_ids=("conflict:a",),
    )
    with pytest.raises((TypeError, ValueError)):
        c.fold_promotion_events(
            (incomplete_operator,), known_conflict_ids=("conflict:a", "conflict:b")
        )

    complete_operator = _promotion_event(
        c,
        candidate,
        "promotion_approved",
        actor_kind="operator",
        actor_ref="operator:alice",
        conflict_ids=("conflict:b", "conflict:a"),
    )
    assert c.fold_promotion_events(
        (complete_operator,), known_conflict_ids=("conflict:a", "conflict:b")
    )


def test_promotion_fold_exposes_sorted_resolved_and_unresolved_conflict_ids() -> None:
    c = _contracts()
    candidate = _candidate(c)
    queued = _promotion_event(c, candidate, "review_queued")
    unresolved = c.fold_promotion_events(
        (queued,),
        known_conflict_ids=("conflict:b", "conflict:a", "conflict:b"),
    )
    assert unresolved.resolved_conflict_ids == ()
    assert unresolved.unresolved_conflict_ids == ("conflict:a", "conflict:b")

    complete_operator = _promotion_event(
        c,
        candidate,
        "promotion_approved",
        actor_kind="operator",
        actor_ref="operator:alice",
        conflict_ids=("conflict:b", "conflict:a"),
    )
    resolved = c.fold_promotion_events(
        (complete_operator,),
        known_conflict_ids=("conflict:b", "conflict:a"),
    )
    assert resolved.resolved_conflict_ids == ("conflict:a", "conflict:b")
    assert resolved.unresolved_conflict_ids == ()


def test_promotion_fold_sequence_mode_rejects_global_append_seq_reuse() -> None:
    c = _contracts()
    candidate = _candidate(c)
    queued = _promotion_event(c, candidate, "review_queued")
    conflict_id = "conflict:global-sequence-collision"

    with pytest.raises((TypeError, ValueError)):
        c.fold_promotion_events(
            (queued,),
            known_conflict_ids=(conflict_id,),
            event_append_seq={queued.event_id: 7},
            conflict_append_seq={conflict_id: 7},
        )


def test_promotion_event_receipt_fields_are_closed_by_event_kind() -> None:
    c = _contracts()
    candidate = _candidate(c)

    with pytest.raises((TypeError, ValueError)):
        _promotion_event(c, candidate, "review_queued", receipt_ref="receipt:early")
    with pytest.raises((TypeError, ValueError)):
        _promotion_event(c, candidate, "projection_applied", projection_ref="card:77")
    with pytest.raises((TypeError, ValueError)):
        _promotion_event(c, candidate, "projection_failed")

    failed = _promotion_event(
        c,
        candidate,
        "projection_failed",
        actor_kind="projector",
        actor_ref="projector:card-v1",
        receipt_ref="receipt:failed-77",
    )
    assert failed.receipt_ref == "receipt:failed-77"
    assert failed.projection_ref is None


def test_conflict_resolved_event_is_operator_only_and_has_closed_fields() -> None:
    c = _contracts()
    candidate = _candidate(c)
    resolved = _promotion_event(
        c,
        candidate,
        "conflict_resolved",
        actor_kind="operator",
        actor_ref="operator:alice",
        occurred_at=T0 + timedelta(minutes=6),
        conflict_ids=("conflict:b", "conflict:a", "conflict:b"),
    )

    assert resolved.conflict_ids == ("conflict:a", "conflict:b")
    assert resolved.projection_ref is None
    assert resolved.receipt_ref is None

    invalid_overrides = (
        {"conflict_ids": ()},
        {
            "actor_kind": "policy",
            "actor_ref": "policy:memory-v1",
            "conflict_ids": ("conflict:a",),
        },
        {
            "actor_kind": "projector",
            "actor_ref": "projector:card-v1",
            "conflict_ids": ("conflict:a",),
        },
        {"conflict_ids": ("conflict:a",), "projection_ref": "card:77"},
        {"conflict_ids": ("conflict:a",), "receipt_ref": "receipt:resolve-a"},
    )
    for overrides in invalid_overrides:
        with pytest.raises((TypeError, ValueError)):
            _promotion_event(c, candidate, "conflict_resolved", **overrides)


def test_projection_proposal_direct_construction_revalidates_and_deep_freezes() -> None:
    """The dataclass constructor must not be weaker than the proposal factory."""
    c = _contracts()
    payload = {"content": "likes tea", "category": "preference"}
    proposal = c.ProjectionProposalV1.create(
        projection_kind="card",
        operation="create",
        payload={"category": "preference", "content": "likes tea"},
    )
    direct_proposal = replace(proposal, payload=payload)
    payload["content"] = "mutated after direct construction"

    assert dict(direct_proposal.payload) == {
        "category": "preference",
        "content": "likes tea",
    }
    with pytest.raises(TypeError):
        direct_proposal.payload["content"] = "mutated"  # type: ignore[index]
    with pytest.raises((TypeError, ValueError)):
        replace(proposal, projection_kind="invented")
    with pytest.raises((TypeError, ValueError)):
        replace(proposal, operation="reinforce", target_ref=None)


def test_candidate_envelope_direct_construction_rejects_forged_mode_and_digests() -> None:
    c = _contracts()
    proposal = c.ProjectionProposalV1.create(
        projection_kind="card",
        operation="create",
        payload={"category": "preference", "content": "likes tea"},
    )
    candidate = _candidate(c, proposal=proposal)
    with pytest.raises((TypeError, ValueError)):
        replace(candidate, mode="live")
    with pytest.raises((TypeError, ValueError)):
        replace(candidate, candidate_sha256="0" * 64)
    with pytest.raises((TypeError, ValueError)):
        replace(candidate, idempotency_key="forged-idempotency-key")


def test_conflict_direct_construction_revalidates_closed_kind_and_normalizes_refs() -> None:
    c = _contracts()
    candidate = _candidate(c)
    conflict = c.ConflictV1.create(
        kind="contradiction",
        subject_ref="user:qq:42",
        claim_key="fact:city",
        observation_ids=("obs-a", "obs-b"),
        candidate_ids=(candidate.candidate_id,),
        detected_at=T0,
        detector="deterministic",
        basis_evidence_refs=("message:one",),
    )
    direct_conflict = replace(
        conflict,
        observation_ids=("obs-b", "obs-a", "obs-b"),
        candidate_ids=(candidate.candidate_id, candidate.candidate_id),
    )
    assert direct_conflict.observation_ids == ("obs-a", "obs-b")
    assert direct_conflict.candidate_ids == (candidate.candidate_id,)
    with pytest.raises((TypeError, ValueError)):
        replace(conflict, kind="invented")
    with pytest.raises((TypeError, ValueError)):
        replace(conflict, basis_evidence_refs=())


def test_promotion_event_direct_construction_revalidates_receipt_and_digest_fields() -> None:
    c = _contracts()
    candidate = _candidate(c)
    applied = _promotion_event(
        c,
        candidate,
        "projection_applied",
        actor_kind="projector",
        actor_ref="projector:card-v1",
        projection_ref="card:77",
        receipt_ref="receipt:apply-77",
    )
    with pytest.raises((TypeError, ValueError)):
        replace(applied, event_kind="invented")
    with pytest.raises((TypeError, ValueError)):
        replace(applied, receipt_ref=None)
    with pytest.raises((TypeError, ValueError)):
        replace(applied, idempotency_key="forged-idempotency-key")


@pytest.mark.parametrize(
    ("event_kind", "actor_kind", "actor_ref", "extra"),
    [
        ("review_queued", "policy", "policy:memory-v1", {}),
        ("promotion_approved", "operator", "operator:alice", {}),
        ("review_queued", "migration", "migration:legacy-v1", {}),
        (
            "projection_failed",
            "projector",
            "projector:card-v1",
            {"receipt_ref": "receipt:failed-valid"},
        ),
    ],
)
def test_promotion_event_accepts_matching_actor_namespaces(
    event_kind: str,
    actor_kind: str,
    actor_ref: str,
    extra: dict[str, str],
) -> None:
    c = _contracts()
    candidate = _candidate(c)

    event = _promotion_event(
        c,
        candidate,
        event_kind,
        actor_kind=actor_kind,
        actor_ref=actor_ref,
        **extra,
    )
    assert str(event.actor_ref).startswith(f"{actor_kind}:")


@pytest.mark.parametrize(
    ("actor_kind", "actor_ref"),
    [
        ("policy", "operator:alice"),
        ("operator", "policy:memory-v1"),
        ("migration", "projector:card-v1"),
        ("projector", "migration:legacy-v1"),
        ("projector", "projector:"),
    ],
)
def test_promotion_event_actor_kind_requires_matching_nonempty_ref_namespace(
    actor_kind: str,
    actor_ref: str,
) -> None:
    c = _contracts()
    candidate = _candidate(c)

    with pytest.raises((TypeError, ValueError)):
        _promotion_event(
            c,
            candidate,
            "review_queued",
            actor_kind=actor_kind,
            actor_ref=actor_ref,
        )


@pytest.mark.parametrize("event_kind", ["promotion_approved", "promotion_rejected"])
def test_projector_actor_cannot_make_promotion_decisions(event_kind: str) -> None:
    c = _contracts()
    candidate = _candidate(c)

    with pytest.raises((TypeError, ValueError)):
        _promotion_event(
            c,
            candidate,
            event_kind,
            actor_kind="projector",
            actor_ref="projector:card-v1",
        )


@pytest.mark.parametrize(
    ("projection_kind", "operation", "projection_ref"),
    [
        ("style", "create", "style:77"),
        ("card", "supersede", "card:77"),
    ],
)
def test_promotion_fold_rejects_cross_event_projection_contract_mismatch(
    projection_kind: str,
    operation: str,
    projection_ref: str,
) -> None:
    c = _contracts()
    candidate = _candidate(c)
    occurred_at = T0 + timedelta(minutes=5)
    approved = _promotion_event(
        c,
        candidate,
        "promotion_approved",
        occurred_at=occurred_at,
        projection_kind="card",
        operation="create",
    )
    mismatched_applied = _promotion_event(
        c,
        candidate,
        "projection_applied",
        actor_kind="projector",
        actor_ref="projector:card-v1",
        occurred_at=occurred_at + timedelta(seconds=1),
        projection_kind=projection_kind,
        operation=operation,
        projection_ref=projection_ref,
        receipt_ref="receipt:mismatch",
    )

    with pytest.raises((TypeError, ValueError)):
        c.fold_promotion_events((approved, mismatched_applied))


def test_promotion_fold_preserves_explicit_append_order_for_equal_timestamps() -> None:
    c = _contracts()
    candidate = _candidate(c)
    same_time = T0 + timedelta(minutes=5)
    approved = _promotion_event(
        c,
        candidate,
        "promotion_approved",
        occurred_at=same_time,
    )
    applied = _promotion_event(
        c,
        candidate,
        "projection_applied",
        actor_kind="projector",
        actor_ref="projector:card-v1",
        occurred_at=same_time,
        projection_ref="card:77",
        receipt_ref="receipt:apply-77",
    )

    folded = c.fold_promotion_events((approved, applied))
    assert folded.projection_event_id == applied.event_id
    assert folded.receipt_ref == "receipt:apply-77"
    with pytest.raises((TypeError, ValueError)):
        c.fold_promotion_events((applied, approved))


def test_projection_failed_fold_exposes_latest_failure_event_and_receipt() -> None:
    c = _contracts()
    candidate = _candidate(c)
    approved = _promotion_event(c, candidate, "promotion_approved")
    failed = _promotion_event(
        c,
        candidate,
        "projection_failed",
        actor_kind="projector",
        actor_ref="projector:card-v1",
        occurred_at=T0 + timedelta(minutes=6),
        receipt_ref="receipt:failed-77",
    )

    folded = c.fold_promotion_events((approved, failed))
    assert folded.projection_event_id == failed.event_id
    assert folded.receipt_ref == "receipt:failed-77"


def test_negative_zero_confidence_canonicalizes_to_positive_zero_identity() -> None:
    c = _contracts()
    negative = _observation(c, confidence=-0.0)
    positive = _observation(c, confidence=0.0)

    assert math.copysign(1.0, negative.confidence) == 1.0
    assert negative == positive
    assert negative.observation_id == positive.observation_id
    assert negative.observation_sha256 == positive.observation_sha256
