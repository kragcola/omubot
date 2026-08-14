"""Pure trust-boundary tests for shadow memory candidate producers."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any

import pytest

OBSERVED_AT = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
SOURCE_AT = OBSERVED_AT - timedelta(seconds=5)


def _modules() -> tuple[ModuleType, ModuleType]:
    contracts_spec = importlib.util.find_spec("services.memory.governance_contracts")
    assert contracts_spec is not None, "memory governance contracts are required"
    producer_spec = importlib.util.find_spec("services.memory.candidate_producers")
    assert producer_spec is not None, "services.memory.candidate_producers is required"
    return (
        importlib.import_module("services.memory.governance_contracts"),
        importlib.import_module("services.memory.candidate_producers"),
    )


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _memo_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "decisions": ({"action": "add", "category": "preference", "content": "likes tea"},),
        "trusted_user_id": "42",
        "origin_group_id": "9001",
        "source_message_id": "msg-17",
        "user_message": "I like jasmine tea",
        "observed_at": OBSERVED_AT,
        "source_occurred_at": SOURCE_AT,
        "producer_run_id": "memo-run-1",
        "model_output": '[{"action":"add"}]',
        "allowed_target_card_ids": (),
    }
    values.update(overrides)
    return values


def _private_evidence(
    c: ModuleType,
    *,
    user_id: str = "42",
    actor_id: str = "42",
    suffix: str = "11",
    quote: str = "Alice said she likes tea",
) -> Any:
    return c.EvidenceAtomV1(
        evidence_ref=f"message:onebot:private:{user_id}:msg-{suffix}",
        content_sha256=c.sha256_text(quote),
        quote=quote,
        actor_ref=f"user:qq:{actor_id}",
        occurred_at=SOURCE_AT,
    )


def _group_evidence(
    c: ModuleType,
    *,
    group_id: str = "9001",
    actor_id: str = "42",
    suffix: str = "11",
    quote: str = "Alice said she likes tea",
) -> Any:
    return c.EvidenceAtomV1(
        evidence_ref=f"message:onebot:group:{group_id}:msg-{suffix}",
        content_sha256=c.sha256_text(quote),
        quote=quote,
        actor_ref=f"user:qq:{actor_id}",
        occurred_at=SOURCE_AT,
    )


def _compaction_kwargs(c: ModuleType, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "tool_inputs": (
            {"scope": "user", "scope_id": "42", "category": "fact", "content": "likes tea"},
        ),
        "context_kind": "private",
        "trusted_private_user_id": "42",
        "trusted_group_id": None,
        "trusted_speaker_user_ids": (),
        "evidence_by_item": ((_private_evidence(c),),),
        "observed_at": OBSERVED_AT,
        "producer_run_id": "compact-run-1",
        "model_output": "tool output",
    }
    values.update(overrides)
    return values


def _call_compaction(producers: ModuleType, **kwargs: Any) -> tuple[Any, ...]:
    function = producers.compaction_tool_uses_to_candidates
    assert "evidence_by_item" in inspect.signature(function).parameters, (
        "compaction producer requires an item-aligned evidence_by_item API"
    )
    return function(**kwargs)


def test_candidate_producers_are_synchronous_and_expose_no_storage_dependency() -> None:
    _, p = _modules()
    memo = p.memo_decisions_to_candidates
    compaction = p.compaction_tool_uses_to_candidates

    assert not inspect.iscoroutinefunction(memo)
    assert not inspect.iscoroutinefunction(compaction)
    assert "card_store" not in inspect.signature(memo).parameters
    assert "card_store" not in inspect.signature(compaction).parameters
    assert "evidence_by_item" in inspect.signature(compaction).parameters


def test_memo_group_add_binds_trusted_identity_origin_and_raw_message_evidence() -> None:
    c, p = _modules()
    candidates = p.memo_decisions_to_candidates(**_memo_kwargs())

    assert isinstance(candidates, tuple)
    assert len(candidates) == 1
    candidate = candidates[0]
    observation = candidate.observation
    evidence = observation.evidence[0]
    assert candidate.mode == "shadow"
    assert _enum_value(candidate.producer_kind) == "memo"
    assert _enum_value(observation.source_kind) == "user_statement"
    assert observation.subject_ref == "user:qq:42"
    assert _enum_value(observation.owner_scope) == "user"
    assert observation.owner_id == "42"
    assert _enum_value(observation.visibility) == "same_group"
    assert observation.origin_group_ref == "group:qq:9001"
    assert evidence.quote == "I like jasmine tea"
    assert evidence.content_sha256 == c.sha256_text("I like jasmine tea")
    assert evidence.actor_ref == "user:qq:42"
    assert evidence.evidence_ref.startswith("message:")
    assert all(part in evidence.evidence_ref for part in ("onebot", "group", "9001", "msg-17"))
    assert _enum_value(candidate.proposal.operation) == "create"
    assert candidate.proposal.target_ref is None


def test_memo_private_add_is_private_and_deterministic() -> None:
    c, p = _modules()
    kwargs = _memo_kwargs(origin_group_id=None)
    first = p.memo_decisions_to_candidates(**kwargs)
    second = p.memo_decisions_to_candidates(**kwargs)

    assert first == second
    assert len(first) == 1
    observation = first[0].observation
    assert _enum_value(observation.visibility) == "private"
    assert observation.origin_group_ref is None
    assert "private" in observation.evidence[0].evidence_ref
    assert "42" in observation.evidence[0].evidence_ref
    assert first[0].model_output_sha256 == c.sha256_text(kwargs["model_output"])


def test_memo_evidence_preserves_exact_bounded_raw_message_bytes() -> None:
    c, p = _modules()
    raw_message = "  I like jasmine tea.\nPlease remember this exactly.  "
    candidates = p.memo_decisions_to_candidates(
        **_memo_kwargs(user_message=raw_message)
    )

    assert len(candidates) == 1
    evidence = candidates[0].observation.evidence[0]
    assert evidence.quote == raw_message
    assert evidence.content_sha256 == c.sha256_text(raw_message)


def test_memo_skip_is_ignored_and_requires_message_identity_and_evidence() -> None:
    _, p = _modules()
    skipped = p.memo_decisions_to_candidates(
        **_memo_kwargs(decisions=({"action": "skip"},))
    )
    assert skipped == ()

    for override in ({"source_message_id": ""}, {"user_message": "   "}):
        with pytest.raises((TypeError, ValueError)):
            p.memo_decisions_to_candidates(**_memo_kwargs(**override))


@pytest.mark.parametrize(
    "spoof",
    [
        {"scope": "global"},
        {"scope": "user", "scope_id": "99"},
        {"subject": "user:qq:99"},
        {"visibility": "global"},
    ],
)
def test_memo_rejects_model_spoofing_instead_of_trusting_it(spoof: dict[str, str]) -> None:
    _, p = _modules()
    decision = {"action": "add", "category": "fact", "content": "spoof"} | spoof
    with pytest.raises((TypeError, ValueError)):
        p.memo_decisions_to_candidates(**_memo_kwargs(decisions=(decision,)))


def test_memo_rejects_invalid_category_and_off_allowlist_targets() -> None:
    _, p = _modules()
    with pytest.raises((TypeError, ValueError)):
        p.memo_decisions_to_candidates(
            **_memo_kwargs(
                decisions=({"action": "add", "category": "invented", "content": "bad"},)
            )
        )

    for action in ("reinforce", "supersede"):
        with pytest.raises((TypeError, ValueError)):
            p.memo_decisions_to_candidates(
                **_memo_kwargs(
                    decisions=(
                        {
                            "action": action,
                            "category": "fact",
                            "content": "changed",
                            "target_card_id": "card-7",
                        },
                    ),
                    allowed_target_card_ids=("card-8",),
                )
            )


@pytest.mark.parametrize("action", ["reinforce", "supersede"])
def test_memo_allows_only_explicitly_trusted_card_targets(action: str) -> None:
    _, p = _modules()
    candidates = p.memo_decisions_to_candidates(
        **_memo_kwargs(
            decisions=(
                {
                    "action": action,
                    "category": "fact",
                    "content": "updated fact",
                    "target_card_id": "card-7",
                },
            ),
            allowed_target_card_ids=("card-7",),
        )
    )

    assert len(candidates) == 1
    assert _enum_value(candidates[0].proposal.operation) == action
    assert candidates[0].proposal.target_ref == "card:card-7"


def test_compaction_requires_nonempty_evidence_for_every_tool_item() -> None:
    c, p = _modules()
    two_inputs = (
        {"scope": "user", "scope_id": "42", "category": "fact", "content": "likes tea"},
        {"scope": "user", "scope_id": "42", "category": "fact", "content": "likes coffee"},
    )

    for evidence_by_item in ((), ((_private_evidence(c),),), ((_private_evidence(c),), ())):
        with pytest.raises((TypeError, ValueError)):
            _call_compaction(
                p,
                **_compaction_kwargs(
                    c,
                    tool_inputs=two_inputs,
                    evidence_by_item=evidence_by_item,
                )
            )


def test_private_compaction_permits_only_exact_session_user_and_creates_private_card() -> None:
    c, p = _modules()
    candidates = _call_compaction(p, **_compaction_kwargs(c))

    assert isinstance(candidates, tuple)
    assert len(candidates) == 1
    candidate = candidates[0]
    observation = candidate.observation
    assert observation.subject_ref == "user:qq:42"
    assert _enum_value(observation.owner_scope) == "user"
    assert observation.owner_id == "42"
    assert _enum_value(observation.visibility) == "private"
    assert observation.origin_group_ref is None
    assert _enum_value(candidate.proposal.operation) == "create"

    for scope, scope_id in (("user", "99"), ("group", "9001"), ("global", "global")):
        with pytest.raises((TypeError, ValueError)):
            _call_compaction(
                p,
                **_compaction_kwargs(
                    c,
                    tool_inputs=(
                        {"scope": scope, "scope_id": scope_id, "category": "fact", "content": "bad"},
                    ),
                )
            )


def test_group_compaction_derives_group_and_proven_speaker_candidates_from_context() -> None:
    c, p = _modules()
    candidates = _call_compaction(
        p,
        **_compaction_kwargs(
            c,
            tool_inputs=(
                {"scope": "group", "scope_id": "9001", "category": "event", "content": "book club"},
                {"scope": "user", "scope_id": "42", "category": "fact", "content": "likes tea"},
            ),
            context_kind="group",
            trusted_private_user_id=None,
            trusted_group_id="9001",
            trusted_speaker_user_ids=("42", "77"),
            evidence_by_item=(
                (_group_evidence(c, actor_id="77", suffix="group"),),
                (_group_evidence(c, actor_id="42", suffix="user"),),
            ),
        )
    )

    assert len(candidates) == 2
    group_obs, user_obs = (candidate.observation for candidate in candidates)
    assert (group_obs.subject_ref, _enum_value(group_obs.owner_scope), group_obs.owner_id) == (
        "group:qq:9001", "group", "9001"
    )
    assert (user_obs.subject_ref, _enum_value(user_obs.owner_scope), user_obs.owner_id) == (
        "user:qq:42", "user", "42"
    )
    assert _enum_value(group_obs.visibility) == "same_group"
    assert _enum_value(user_obs.visibility) == "same_group"
    assert group_obs.origin_group_ref == user_obs.origin_group_ref == "group:qq:9001"
    assert all(_enum_value(candidate.proposal.operation) == "create" for candidate in candidates)


@pytest.mark.parametrize(
    ("scope", "scope_id"),
    [("user", "99"), ("group", "9002"), ("global", "global")],
)
def test_group_compaction_rejects_unseen_users_other_groups_and_global_scope(
    scope: str,
    scope_id: str,
) -> None:
    c, p = _modules()
    with pytest.raises((TypeError, ValueError)):
        _call_compaction(
            p,
            **_compaction_kwargs(
                c,
                tool_inputs=(
                    {"scope": scope, "scope_id": scope_id, "category": "fact", "content": "spoof"},
                ),
                context_kind="group",
                trusted_private_user_id=None,
                trusted_group_id="9001",
                trusted_speaker_user_ids=("42",),
                evidence_by_item=((_group_evidence(c),),),
            )
        )


def test_compaction_is_create_only_and_rejects_non_add_card_payloads() -> None:
    c, p = _modules()
    invalid_payloads = (
        {
            "action": "supersede",
            "card_id": "7",
            "scope": "user",
            "scope_id": "42",
            "category": "fact",
            "content": "bad",
        },
        {"scope": "user", "scope_id": "42", "category": "invented", "content": "bad"},
    )
    for payload in invalid_payloads:
        with pytest.raises((TypeError, ValueError)):
            _call_compaction(
                p,
                **_compaction_kwargs(c, tool_inputs=(payload,))
            )


def test_compaction_keeps_item_specific_evidence_isolated_per_candidate() -> None:
    c, p = _modules()
    tea_evidence = _private_evidence(c, suffix="tea", quote="likes tea")
    coffee_evidence = _private_evidence(c, suffix="coffee", quote="likes coffee")
    candidates = _call_compaction(
        p,
        **_compaction_kwargs(
            c,
            tool_inputs=(
                {
                    "scope": "user",
                    "scope_id": "42",
                    "category": "preference",
                    "content": "likes tea",
                },
                {
                    "scope": "user",
                    "scope_id": "42",
                    "category": "preference",
                    "content": "likes coffee",
                },
            ),
            evidence_by_item=((tea_evidence,), (coffee_evidence,)),
        )
    )

    assert len(candidates) == 2
    assert candidates[0].observation.evidence == (tea_evidence,)
    assert candidates[1].observation.evidence == (coffee_evidence,)
    assert tea_evidence not in candidates[1].observation.evidence
    assert coffee_evidence not in candidates[0].observation.evidence


@pytest.mark.parametrize(
    ("context_kind", "evidence_ref"),
    [
        pytest.param(
            "private",
            "message:onebot:private:42:",
            id="private-prefix-only",
        ),
        pytest.param(
            "group",
            "message:onebot:group:9001:",
            id="group-prefix-only",
        ),
    ],
)
def test_compaction_rejects_prefix_only_message_evidence_refs(
    context_kind: str,
    evidence_ref: str,
) -> None:
    c, p = _modules()
    atom = c.EvidenceAtomV1(
        evidence_ref=evidence_ref,
        content_sha256=c.sha256_text("Alice said she likes tea"),
        quote="Alice said she likes tea",
        actor_ref="user:qq:42",
        occurred_at=SOURCE_AT,
    )
    context_overrides: dict[str, Any] = {}
    if context_kind == "group":
        context_overrides = {
            "tool_inputs": (
                {
                    "scope": "group",
                    "scope_id": "9001",
                    "category": "event",
                    "content": "book club",
                },
            ),
            "context_kind": "group",
            "trusted_private_user_id": None,
            "trusted_group_id": "9001",
            "trusted_speaker_user_ids": ("42",),
        }

    with pytest.raises((TypeError, ValueError)):
        _call_compaction(
            p,
            **_compaction_kwargs(
                c,
                evidence_by_item=((atom,),),
                **context_overrides,
            ),
        )


@pytest.mark.parametrize(
    ("context_kind", "evidence_ref"),
    [
        pytest.param(
            "private",
            "message:onebot:private:42:msg-11:nested:group:9001:msg-22",
            id="private-trailing-context",
        ),
        pytest.param(
            "group",
            "message:onebot:group:9001:msg-11:nested:private:42:msg-22",
            id="group-trailing-context",
        ),
    ],
)
def test_compaction_rejects_message_evidence_refs_with_trailing_context(
    context_kind: str,
    evidence_ref: str,
) -> None:
    c, p = _modules()
    atom = c.EvidenceAtomV1(
        evidence_ref=evidence_ref,
        content_sha256=c.sha256_text("Alice said she likes tea"),
        quote="Alice said she likes tea",
        actor_ref="user:qq:42",
        occurred_at=SOURCE_AT,
    )
    context_overrides: dict[str, Any] = {}
    if context_kind == "group":
        context_overrides = {
            "tool_inputs": (
                {
                    "scope": "group",
                    "scope_id": "9001",
                    "category": "event",
                    "content": "book club",
                },
            ),
            "context_kind": "group",
            "trusted_private_user_id": None,
            "trusted_group_id": "9001",
            "trusted_speaker_user_ids": ("42",),
        }

    with pytest.raises((TypeError, ValueError)):
        _call_compaction(
            p,
            **_compaction_kwargs(
                c,
                evidence_by_item=((atom,),),
                **context_overrides,
            ),
        )


@pytest.mark.parametrize(
    "evidence",
    [
        pytest.param("private-other", id="other-private-origin"),
        pytest.param("group-origin", id="group-origin"),
        pytest.param("other-actor", id="other-private-actor"),
    ],
)
def test_private_compaction_rejects_evidence_outside_exact_private_context(
    evidence: str,
) -> None:
    c, p = _modules()
    atom = {
        "private-other": _private_evidence(c, user_id="99", actor_id="42"),
        "group-origin": _group_evidence(c, group_id="9001", actor_id="42"),
        "other-actor": _private_evidence(c, user_id="42", actor_id="99"),
    }[evidence]

    with pytest.raises((TypeError, ValueError)):
        _call_compaction(
            p,
            **_compaction_kwargs(c, evidence_by_item=((atom,),))
        )


@pytest.mark.parametrize(
    "evidence",
    [
        pytest.param("private-origin", id="private-origin"),
        pytest.param("cross-group", id="cross-group-origin"),
        pytest.param("unproven-actor", id="unproven-actor"),
    ],
)
def test_group_compaction_rejects_evidence_outside_exact_group_and_speaker_set(
    evidence: str,
) -> None:
    c, p = _modules()
    atom = {
        "private-origin": _private_evidence(c, user_id="42", actor_id="42"),
        "cross-group": _group_evidence(c, group_id="9002", actor_id="42"),
        "unproven-actor": _group_evidence(c, group_id="9001", actor_id="99"),
    }[evidence]

    with pytest.raises((TypeError, ValueError)):
        _call_compaction(
            p,
            **_compaction_kwargs(
                c,
                tool_inputs=(
                    {
                        "scope": "group",
                        "scope_id": "9001",
                        "category": "event",
                        "content": "book club",
                    },
                ),
                context_kind="group",
                trusted_private_user_id=None,
                trusted_group_id="9001",
                trusted_speaker_user_ids=("42", "77"),
                evidence_by_item=((atom,),),
            )
        )


def test_group_user_candidate_requires_evidence_from_that_exact_user() -> None:
    c, p = _modules()

    with pytest.raises((TypeError, ValueError)):
        _call_compaction(
            p,
            **_compaction_kwargs(
                c,
                tool_inputs=(
                    {
                        "scope": "user",
                        "scope_id": "42",
                        "category": "fact",
                        "content": "likes tea",
                    },
                ),
                context_kind="group",
                trusted_private_user_id=None,
                trusted_group_id="9001",
                trusted_speaker_user_ids=("42", "77"),
                evidence_by_item=((_group_evidence(c, actor_id="77"),),),
            )
        )
