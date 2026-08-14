"""Pure candidate-only adapters for legacy Memo and compaction outputs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from services.memory.governance_contracts import (
    CandidateEnvelopeV1,
    CardClaimV1,
    EvidenceAtomV1,
    ObservationV1,
    OwnerScope,
    ProducerKind,
    ProjectionProposalV1,
    SourceKind,
    TimeBasis,
    Visibility,
    sha256_text,
)

_CARD_CATEGORIES = frozenset(
    {"preference", "boundary", "relationship", "event", "promise", "fact", "status"}
)
_MEMO_ALLOWED_KEYS = frozenset(
    {
        "action",
        "category",
        "content",
        "target_card_id",
        "target",
        "scope",
        "scope_id",
        "subject",
        "subject_ref",
        "visibility",
    }
)
_COMPACTION_ALLOWED_KEYS = frozenset({"scope", "scope_id", "category", "content"})
_ONEBOT_MESSAGE_EVIDENCE_RE = re.compile(
    r"^message:onebot:(private|group):([1-9][0-9]*):"
    r"(?:message:)?([A-Za-z0-9][A-Za-z0-9_.@/+\-]{0,159})$"
)


def _numeric_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        raise ValueError(f"{field_name} must be numeric")
    return str(int(text))


def _clean_required(value: object, field_name: str, *, maximum: int = 2_000) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{field_name} must be 1..{maximum} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError(f"{field_name} contains control characters")
    return text


def _category(value: object) -> str:
    category = str(value or "").strip().lower()
    if category not in _CARD_CATEGORIES:
        raise ValueError(f"invalid card category: {value!r}")
    return category


def _candidate(
    *,
    producer_kind: Literal["memo", "compaction"],
    producer_version: str,
    producer_run_id: str,
    producer_item_id: str,
    subject_ref: str,
    owner_scope: Literal["user", "group"],
    owner_id: str,
    visibility: Literal["private", "same_group"],
    origin_group_ref: str | None,
    category: str,
    content: str,
    evidence: tuple[EvidenceAtomV1, ...],
    observed_at: datetime | str,
    source_occurred_at: datetime | str | None,
    source_kind: Literal["user_statement", "conversation_inference"],
    operation: Literal["create", "reinforce", "supersede"],
    target_ref: str | None,
    model_output: str,
) -> CandidateEnvelopeV1:
    observation = ObservationV1(
        source_kind=SourceKind(source_kind),
        producer_kind=ProducerKind(producer_kind),
        producer_version=producer_version,
        producer_run_id=producer_run_id,
        subject_ref=subject_ref,
        owner_scope=OwnerScope(owner_scope),
        owner_id=owner_id,
        visibility=Visibility(visibility),
        origin_group_ref=origin_group_ref,
        claim=CardClaimV1(category=category, content=content),
        evidence=evidence,
        observed_at=observed_at,
        source_occurred_at=source_occurred_at,
        time_basis=(
            TimeBasis.SOURCE_EVENT
            if source_occurred_at is not None
            else TimeBasis.UNKNOWN
        ),
        valid_from=None,
        valid_to=None,
        confidence=0.7 if operation == "supersede" else 0.6,
    )
    proposal = ProjectionProposalV1.create(
        projection_kind="card",
        operation=operation,
        target_ref=target_ref,
        payload={"category": category, "content": content},
    )
    return CandidateEnvelopeV1.create(
        observation=observation,
        proposal=proposal,
        producer_kind=producer_kind,
        producer_run_id=producer_run_id,
        producer_item_id=producer_item_id,
        produced_at=observed_at,
        model_output=model_output,
    )


def memo_decisions_to_candidates(
    *,
    decisions: Sequence[Mapping[str, Any]],
    trusted_user_id: str,
    origin_group_id: str | None,
    source_message_id: str,
    user_message: str,
    observed_at: datetime | str,
    source_occurred_at: datetime | str | None = None,
    producer_run_id: str,
    model_output: str,
    allowed_target_card_ids: Iterable[str] = (),
) -> tuple[CandidateEnvelopeV1, ...]:
    """Map already-parsed Memo decisions into trusted shadow candidates."""

    user_id = _numeric_id(trusted_user_id, "trusted_user_id")
    group_id = (
        _numeric_id(origin_group_id, "origin_group_id")
        if origin_group_id is not None and str(origin_group_id).strip()
        else None
    )
    message_id = _clean_required(source_message_id, "source_message_id", maximum=160)
    if not isinstance(user_message, str):
        raise TypeError("user_message must be a string")
    quote = user_message
    allowed_targets = {
        _clean_required(value, "allowed_target_card_id", maximum=160)
        for value in allowed_target_card_ids
    }
    actor_ref = f"user:qq:{user_id}"
    if group_id is None:
        evidence_ref = f"message:onebot:private:{user_id}:message:{message_id}"
        visibility: Literal["private", "same_group"] = "private"
        origin_ref = None
    else:
        evidence_ref = f"message:onebot:group:{group_id}:message:{message_id}"
        visibility = "same_group"
        origin_ref = f"group:qq:{group_id}"
    evidence = (
        EvidenceAtomV1(
            evidence_ref=evidence_ref,
            content_sha256=sha256_text(quote),
            quote=quote,
            actor_ref=actor_ref,
            occurred_at=source_occurred_at,
        ),
    )

    result: list[CandidateEnvelopeV1] = []
    for index, raw in enumerate(decisions):
        if not isinstance(raw, Mapping):
            raise TypeError("memo decision must be a mapping")
        decision = {str(key): value for key, value in raw.items()}
        if set(decision) - _MEMO_ALLOWED_KEYS:
            raise ValueError("memo decision contains unknown fields")
        action = str(decision.get("action") or "add").strip().lower()
        if action == "skip":
            continue
        if action not in {"add", "reinforce", "supersede"}:
            raise ValueError(f"invalid memo action: {action!r}")
        _reject_memo_spoof(
            decision,
            user_id=user_id,
            visibility=visibility,
            subject_ref=actor_ref,
        )
        category = _category(decision.get("category"))
        content = _clean_required(decision.get("content"), "content")
        target_raw = decision.get("target_card_id", decision.get("target"))
        target_id = str(target_raw or "").strip()
        if action == "add":
            if target_id:
                raise ValueError("memo add cannot select a target card")
            operation: Literal["create", "reinforce", "supersede"] = "create"
            target_ref = None
        else:
            if not target_id or target_id not in allowed_targets:
                raise ValueError("memo target card is outside the trusted allowlist")
            operation = action  # type: ignore[assignment]
            target_ref = f"card:{target_id}"
        result.append(
            _candidate(
                producer_kind="memo",
                producer_version="memo-candidate-v1",
                producer_run_id=producer_run_id,
                producer_item_id=f"memo-{index}",
                subject_ref=actor_ref,
                owner_scope="user",
                owner_id=user_id,
                visibility=visibility,
                origin_group_ref=origin_ref,
                category=category,
                content=content,
                evidence=evidence,
                observed_at=observed_at,
                source_occurred_at=source_occurred_at,
                source_kind="user_statement",
                operation=operation,
                target_ref=target_ref,
                model_output=model_output,
            )
        )
    return tuple(result)


def _reject_memo_spoof(
    decision: Mapping[str, Any],
    *,
    user_id: str,
    visibility: str,
    subject_ref: str,
) -> None:
    if "scope" in decision and str(decision["scope"] or "").strip() != "user":
        raise ValueError("memo scope must remain trusted user scope")
    if (
        "scope_id" in decision
        and _numeric_id(decision["scope_id"], "scope_id") != user_id
    ):
        raise ValueError("memo scope_id does not match trusted user")
    supplied_subject = decision.get("subject_ref", decision.get("subject"))
    if supplied_subject is not None and str(supplied_subject).strip() != subject_ref:
        raise ValueError("memo subject does not match trusted user")
    if "visibility" in decision and str(decision["visibility"] or "").strip() != visibility:
        raise ValueError("memo visibility does not match trusted context")


def compaction_tool_uses_to_candidates(
    *,
    tool_inputs: Sequence[Mapping[str, Any]],
    context_kind: Literal["private", "group"] | str,
    trusted_private_user_id: str | None,
    trusted_group_id: str | None,
    trusted_speaker_user_ids: Iterable[str],
    evidence_by_item: Sequence[Iterable[EvidenceAtomV1]],
    observed_at: datetime | str,
    producer_run_id: str,
    model_output: str,
) -> tuple[CandidateEnvelopeV1, ...]:
    """Map existing compaction add_card inputs into create-only candidates."""

    item_evidence = tuple(tuple(values) for values in evidence_by_item)
    if len(item_evidence) != len(tool_inputs) or any(not values for values in item_evidence):
        raise ValueError("every compaction item requires item-specific evidence")
    if not all(
        isinstance(atom, EvidenceAtomV1)
        for values in item_evidence
        for atom in values
    ):
        raise TypeError("compaction evidence must contain EvidenceAtomV1 values")
    if context_kind not in {"private", "group"}:
        raise ValueError(f"invalid compaction context_kind: {context_kind!r}")
    private_user = (
        _numeric_id(trusted_private_user_id, "trusted_private_user_id")
        if trusted_private_user_id is not None and str(trusted_private_user_id).strip()
        else None
    )
    group_id = (
        _numeric_id(trusted_group_id, "trusted_group_id")
        if trusted_group_id is not None and str(trusted_group_id).strip()
        else None
    )
    speakers = {
        _numeric_id(value, "trusted_speaker_user_id")
        for value in trusted_speaker_user_ids
    }
    if context_kind == "private" and (private_user is None or group_id is not None):
        raise ValueError("private compaction requires only a trusted private user")
    if context_kind == "group" and (group_id is None or private_user is not None):
        raise ValueError("group compaction requires only a trusted group")
    result: list[CandidateEnvelopeV1] = []
    for index, (raw, evidence) in enumerate(zip(tool_inputs, item_evidence, strict=True)):
        if not isinstance(raw, Mapping):
            raise TypeError("compaction tool input must be a mapping")
        item = {str(key): value for key, value in raw.items()}
        if set(item) != _COMPACTION_ALLOWED_KEYS:
            raise ValueError("compaction supports the existing add_card payload only")
        scope = str(item.get("scope") or "").strip().lower()
        scope_id = _numeric_id(item.get("scope_id"), "scope_id")
        category = _category(item.get("category"))
        content = _clean_required(item.get("content"), "content")
        if context_kind == "private":
            assert private_user is not None
            if scope != "user" or scope_id != private_user:
                raise ValueError("private compaction target is outside trusted session")
            subject_ref = f"user:qq:{private_user}"
            owner_scope: Literal["user", "group"] = "user"
            visibility: Literal["private", "same_group"] = "private"
            origin_ref = None
            _validate_private_evidence(evidence, user_id=private_user)
        else:
            assert group_id is not None
            if scope == "user" and scope_id in speakers:
                subject_ref = f"user:qq:{scope_id}"
                owner_scope = "user"
            elif scope == "group" and scope_id == group_id:
                subject_ref = f"group:qq:{group_id}"
                owner_scope = "group"
            else:
                raise ValueError("group compaction target is outside proven context")
            visibility = "same_group"
            origin_ref = f"group:qq:{group_id}"
            _validate_group_evidence(
                evidence,
                group_id=group_id,
                trusted_speakers=speakers,
                required_actor_id=scope_id if owner_scope == "user" else None,
            )
        occurred_values = [
            atom.occurred_at for atom in evidence if atom.occurred_at is not None
        ]
        source_at = max(occurred_values) if occurred_values else None
        result.append(
            _candidate(
                producer_kind="compaction",
                producer_version="compaction-candidate-v1",
                producer_run_id=producer_run_id,
                producer_item_id=f"compact-{index}",
                subject_ref=subject_ref,
                owner_scope=owner_scope,
                owner_id=scope_id,
                visibility=visibility,
                origin_group_ref=origin_ref,
                category=category,
                content=content,
                evidence=evidence,
                observed_at=observed_at,
                source_occurred_at=source_at,
                source_kind="conversation_inference",
                operation="create",
                target_ref=None,
                model_output=model_output,
            )
        )
    return tuple(result)


def _validate_private_evidence(
    evidence: tuple[EvidenceAtomV1, ...],
    *,
    user_id: str,
) -> None:
    expected_actor = f"user:qq:{user_id}"
    for atom in evidence:
        context_kind, context_id, _ = _parse_onebot_message_evidence_ref(
            atom.evidence_ref
        )
        if (
            context_kind != "private"
            or context_id != user_id
            or atom.actor_ref != expected_actor
        ):
            raise ValueError("private compaction evidence is outside the trusted session")


def _validate_group_evidence(
    evidence: tuple[EvidenceAtomV1, ...],
    *,
    group_id: str,
    trusted_speakers: set[str],
    required_actor_id: str | None,
) -> None:
    expected_actor = f"user:qq:{required_actor_id}" if required_actor_id else None
    for atom in evidence:
        context_kind, context_id, _ = _parse_onebot_message_evidence_ref(
            atom.evidence_ref
        )
        if context_kind != "group" or context_id != group_id:
            raise ValueError("group compaction evidence is outside the trusted group")
        prefix, separator, actor_id = atom.actor_ref.rpartition(":")
        if not separator or prefix != "user:qq" or actor_id not in trusted_speakers:
            raise ValueError("group compaction evidence actor is not a proven speaker")
        if expected_actor is not None and atom.actor_ref != expected_actor:
            raise ValueError("user candidate evidence must come from that exact user")


def _parse_onebot_message_evidence_ref(value: str) -> tuple[str, str, str]:
    match = _ONEBOT_MESSAGE_EVIDENCE_RE.fullmatch(value)
    if match is None:
        raise ValueError("compaction evidence_ref must identify one OneBot message")
    return match.group(1), match.group(2), match.group(3)


__all__ = [
    "compaction_tool_uses_to_candidates",
    "memo_decisions_to_candidates",
]
