"""Explicit, source-bound N6 factual candidate writes.

This module owns N6 candidates and applied facts. Ordinary proposals require
review and application. Q02A additionally permits a default-off, source-bound
trusted self-nickname command; it never invokes a model or changes Persona.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import Field

from .archive import ArchiveService
from .policy import Policy
from .store import (
    Store,
    StoreConnection,
    StoreRow,
    payload_digest,
    record_payload_conflict,
)
from .types import Event, OperationError, Scope, StrictModel

MemoryAction = Literal["add", "reinforce", "supersede", "skip"]
CandidateDecision = Literal["approved", "rejected", "withdrawn"]
MemorySuggestionReason = Literal[
    "stable_preference",
    "time_bounded_plan",
    "communication_boundary",
    "explicit_correction",
]

_ACTIONS = frozenset({"add", "reinforce", "supersede", "skip"})
_SUGGESTION_REASONS = frozenset(
    {
        "stable_preference",
        "time_bounded_plan",
        "communication_boundary",
        "explicit_correction",
    }
)
_REVIEW_DECISIONS = frozenset({"approved", "rejected", "withdrawn"})
_CANDIDATE_STATUSES = frozenset(
    {
        "pending",
        "conflict_pending",
        "approved",
        "applied",
        "rejected",
        "withdrawn",
        "skipped",
    }
)
_FACT_STATUSES = frozenset({"active", "superseded", "disabled"})
_CODE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_MAX_SOURCES = 32
_MAX_VISIBLE_FACTS = 256
_MAX_CARD_CLASSIFICATIONS = 2048
_CARD_CATEGORIES = frozenset({"preference", "boundary", "relationship", "event", "promise", "fact", "status"})
_MAX_TEMPORAL_VERSIONS = 8
_MAX_SEARCH_TOKENS = 32
_MAX_SEARCH_TOKEN_CHARS = 256
_MAX_REVOCATION_BATCH = 32
_SELF_NICKNAME_PREDICATE = "identity.preferred_name"
_SELF_NICKNAME_PREFIX = re.compile(r"^(?:以后(?:就)?|你可以|请)?叫我")
_SELF_NICKNAME_CHARS = re.compile(r"^[A-Za-z\u3400-\u9fff\u3040-\u30ff]{1,16}$")
# Unknown names outside this narrow literal grammar stay on the manual path.
_SELF_NICKNAME_MANUAL_WORDS = (
    "指令", "忽略", "规则", "管理员", "系统", "开发者", "密码", "电话", "住址", "身份证",
    "癌",
    "抑郁",
    "命令",
    "服从",
    "无条件",
    "必须",
    "执行",
    "权限",
    "绕过",
    "越权",
    "人设",
    "人格",
    "扮演",
    "developer",
    "疾病", "病", "色情", "自杀", "傻", "蠢", "垃圾", "废物", "主人", "爸爸", "妈妈",
    "老公", "老婆", "然后", "删除", "好吗", "可以吗", "行吗", "qq", "system", "admin",
    "password", "instruction", "ignore", "fuck", "shit", "hiv", "aids",
)


def _self_nickname(text: str) -> str | None:
    command = text.strip().rstrip("。.!！")
    prefix = _SELF_NICKNAME_PREFIX.match(command)
    if prefix is None:
        return None
    name = command[prefix.end() :].strip()
    if len(name) >= 2 and (name[0], name[-1]) in {('"', '"'), ("“", "”"), ("「", "」")}:
        name = name[1:-1]
    if (
        _SELF_NICKNAME_CHARS.fullmatch(name) is None
        or unicodedata.normalize("NFC", name) != name
        or any(word in name.casefold() for word in _SELF_NICKNAME_MANUAL_WORDS)
    ):
        raise OperationError("self_nickname_manual_required")
    return name


def self_nickname_command(event: Event) -> str | None:
    """Recognize a literal command, not write authority; ingress supplies the Event.

    Only the authenticated exact Bot mention may be removed for parsing. The
    full Event text remains the evidence compared with Archive during apply.
    """
    if type(event) is not Event:
        raise OperationError("invalid_self_nickname_event")
    if event.reply_to:
        return None
    text = event.text
    if event.mentioned or event.mention_targets:
        if not event.mentioned or event.mention_targets != (event.scope.bot_id,):
            return None
        text = text.lstrip()
        if not text.startswith("@Bot"):
            return None
        text = text[len("@Bot"):].lstrip()
    return _self_nickname(text)


MatterState = Literal["candidate", "approved", "active", "completed", "cancelled", "expired"]
_MATTER_STATES = frozenset({"candidate", "approved", "active", "completed", "cancelled", "expired"})
_OPEN_MATTER_STATES = frozenset({"candidate", "approved", "active"})


@dataclass(frozen=True, slots=True)
class MemoryMatter:
    matter_id: str
    scope: Scope
    subject_id: str
    source_id: str
    source_revision: int
    summary: str
    condition: str | None
    state: MatterState
    revision: int
    observed_at: float | None
    due_at: float | None
    expires_at: float
    replaces_matter_id: str
    reason: str
    actor: str
    created_at: float
    updated_at: float
    purpose: Literal["inbound_reply"] = "inbound_reply"


@dataclass(frozen=True, slots=True)
class MemoryMatterPointer:
    matter_id: str
    scope: Scope
    subject_id: str
    summary: str
    condition: str | None
    observed_at: float | None
    due_at: float | None
    expires_at: float
    revision: int
    source_id: str
    source_revision: int
    purpose: Literal["inbound_reply"] = "inbound_reply"


@dataclass(frozen=True, slots=True)
class MemoryMatterPage:
    items: tuple[MemoryMatter, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: str
    scope: Scope
    subject_id: str
    predicate: str
    value: str
    action: MemoryAction
    target_fact_id: str | None
    source_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    candidate_revision: int
    status: str
    conflict_set_id: str | None
    skip_reason: str | None
    applied_fact_id: str | None
    applied_event_id: str | None
    actor: str
    created_at: float
    updated_at: float
    observed_at: float | None = None
    valid_from: float | None = None
    valid_to: float | None = None
    suggestion_reason: MemorySuggestionReason | None = None


@dataclass(frozen=True, slots=True)
class MemoryFact:
    fact_id: str
    scope: Scope
    subject_id: str
    predicate: str
    value: str
    source_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    fact_revision: int
    status: str
    valid_from: float | None
    valid_to: float | None
    created_at: float
    updated_at: float
    observed_at: float | None = None
    applied_at: float | None = None
    supersedes_fact_id: str | None = None


class MemoryFactPointer(StrictModel):
    """Current applied self-authored fact identity; contains no archived message body."""

    kind: Literal["self_applied_fact"] = "self_applied_fact"
    scope: Scope
    subject_id: str
    fact_id: str
    fact_revision: int = Field(ge=1)
    fact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    applied_event_id: str
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    source_revisions: tuple[tuple[str, int], ...] = Field(min_length=1, max_length=32)

    @property
    def self_entity_id(self) -> str:
        identity = json.dumps([self.scope.model_dump(), self.subject_id], sort_keys=True)
        return "self:" + hashlib.sha256(identity.encode()).hexdigest()[:59]


class SelfAliasPointer(StrictModel):
    """A governed nickname window derived from the sole Memory fact owner."""

    kind: Literal["self_nickname_alias"] = "self_nickname_alias"
    scope: Scope
    subject_id: str
    surface: str
    fact_id: str
    fact_revision: int = Field(ge=1)
    fact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["active", "superseded"]
    window_from: float
    window_to: float | None
    applied_event_id: str
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    source_revisions: tuple[tuple[str, int], ...] = Field(min_length=1, max_length=32)


class SelfAliasResolution(StrictModel):
    """An exact scoped lookup result, never permission or a canonical user registry."""

    reader_id: str
    scope: Scope
    surface: str
    at: float
    status: Literal["missing", "resolved", "ambiguous"]
    candidates: tuple[SelfAliasPointer, ...]

    @property
    def subject_id(self) -> str | None:
        return self.candidates[0].subject_id if self.status == "resolved" else None


@dataclass(frozen=True, slots=True)
class SelfNicknameReceipt:
    """The actual application outcome; historical IDs do not grant future visibility."""

    receipt_id: str
    scope: Scope
    actor: str
    subject_id: str
    source_id: str
    source_revision: int
    source_event_id: str
    platform_message_id: str
    candidate_id: str
    applied_event_id: str
    fact_id: str
    fact_revision: int
    applied_at: float
    status: Literal["applied"] = "applied"


@dataclass(frozen=True, slots=True)
class MemoryTemporalTrace:
    """A complete, source-authorized supersede chain in oldest-first order."""

    head_fact_id: str
    versions: tuple[MemoryFact, ...]
    omitted_reason: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryCard:
    """A current MemoryFact projection, never a second persisted fact."""

    fact: MemoryFact
    category: str
    classification_revision: int


@dataclass(frozen=True, slots=True)
class MemoryCardQueryResult:
    cards: tuple[MemoryCard, ...]
    total_active: int
    matched_active: int
    # Scope-bound IDs only: exclude classified-but-ineligible facts from the
    # ordinary fact path without exposing source-revoked/expired fact bodies.
    classified_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryFactSearchResult:
    """Bounded query result; truncation is explicit and content-free."""

    facts: tuple[MemoryFact, ...]
    truncated: bool

    def __post_init__(self) -> None:
        if len(self.facts) > _MAX_VISIBLE_FACTS:
            raise ValueError("memory_search_limit")
        if type(self.truncated) is not bool:
            raise ValueError("memory_search_truncation")


@dataclass(frozen=True, slots=True)
class MemoryFactPage:
    """A stable, bounded page of current facts visible in one exact scope."""

    facts: tuple[MemoryFact, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class MemoryCandidatePage:
    """A bounded admin list; the cursor advances over scanned, not visible, rows."""

    candidates: tuple[MemoryCandidate, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class MemoryRevocationReceipt:
    """Identity-only receipt for one committed source-revocation projection."""

    revocation_id: str
    scope: Scope
    source_id: str
    source_revision: int
    status: Literal["applied"]
    receipt_id: str
    candidate_count: int
    fact_count: int
    applied_at: float


@dataclass(frozen=True, slots=True)
class MemoryFactExtractionReceipt:
    """Content-free receipt for one source-bound factual extraction result."""

    result_id: str
    scope: Scope
    source_id: str
    domain: Literal["fact"]
    extractor_version: str
    source_revision: int
    result_status: Literal["candidates", "no_evidence"]
    candidate_ids: tuple[str, ...]
    result_digest: str
    created_at: float


def _identity(value: object, code: str = "invalid_memory_identity", *, limit: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or any(ord(char) < 32 for char in value)
        or "://" in value
        or "/" in value
        or "\\" in value
    ):
        raise OperationError(code)
    return value


def _code(value: object, code: str) -> str:
    if not isinstance(value, str) or _CODE_IDENTIFIER.fullmatch(value) is None:
        raise OperationError(code)
    return value


def _memory_value(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or unicodedata.normalize("NFC", value) != value
        or any(unicodedata.category(char).startswith("C") for char in value)
        or any(char in "\r\n\u0085\u2028\u2029" for char in value)
    ):
        raise OperationError("invalid_memory_value")
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise OperationError("invalid_memory_value") from exc
    if encoded_length > 1024:
        raise OperationError("invalid_memory_value")
    return value


def _optional_code(value: object, code: str) -> str | None:
    if value is None or value == "":
        return None
    return _code(value, code)


def _scope(scope: object) -> Scope:
    if type(scope) is not Scope:
        raise OperationError("invalid_memory_scope")
    return scope


def _now(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OperationError("invalid_memory_timestamp")
    number = float(value)
    if not math.isfinite(number):
        raise OperationError("invalid_memory_timestamp")
    return number


def _optional_time(value: object) -> float | None:
    return None if value is None else _now(value)


def _validity_window(
    valid_from: object, valid_to: object
) -> tuple[float | None, float | None]:
    start = _optional_time(valid_from)
    end = _optional_time(valid_to)
    if start is not None and end is not None and end <= start:
        raise OperationError("invalid_memory_validity")
    return start, end


def _fact_is_current(row: StoreRow, now: float) -> bool:
    valid_from = _optional_time(row["valid_from"])
    valid_to = _optional_time(row["valid_to"])
    return (
        row["suppressed_at"] is None
        and (valid_from is None or valid_from <= now)
        and (valid_to is None or now < valid_to)
    )


def _search_tokens(values: Sequence[object]) -> tuple[str, ...]:
    if isinstance(values, str) or len(values) > _MAX_SEARCH_TOKENS:
        raise OperationError("invalid_memory_search_tokens")
    result: list[str] = []
    for value in values:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > _MAX_SEARCH_TOKEN_CHARS
            or any(unicodedata.category(char).startswith("C") for char in value)
        ):
            raise OperationError("invalid_memory_search_tokens")
        if value not in result:
            result.append(value)
    return tuple(result)


def _like_contains(value: str) -> str:
    """Build a literal contains pattern for SQLite LIKE."""

    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sources(values: Sequence[str]) -> tuple[str, ...]:
    if not values or len(values) > _MAX_SOURCES:
        raise OperationError("invalid_memory_sources")
    result: list[str] = []
    for value in values:
        normalized = _identity(value, "invalid_memory_source")
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _json_refs(values: Sequence[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _decode_refs(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise OperationError("invalid_memory_sources")
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_memory_sources") from exc
    if not isinstance(decoded, list):
        raise OperationError("invalid_memory_sources")
    decoded_items = cast(list[object], decoded)
    if any(not isinstance(item, str) for item in decoded_items):
        raise OperationError("invalid_memory_sources")
    return _sources([item for item in decoded_items if isinstance(item, str)])


def _correction_target_revision(details: object) -> tuple[str, int, int]:
    if not isinstance(details, str):
        raise OperationError("invalid_memory_candidate")
    try:
        decoded = json.loads(details)
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_memory_candidate") from exc
    if not isinstance(decoded, dict):
        raise OperationError("invalid_memory_candidate")
    payload = cast(dict[str, object], decoded)
    fact_id = _identity(payload.get("fact_id"), "invalid_memory_candidate")
    expected_revision = payload.get("expected_revision")
    fact_revision = payload.get("fact_revision")
    if (
        type(expected_revision) is not int
        or expected_revision < 1
        or type(fact_revision) is not int
        or fact_revision < 1
    ):
        raise OperationError("invalid_memory_candidate")
    return fact_id, expected_revision, fact_revision


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()}"


def _optional(value: object) -> str | None:
    if value is None or value == "":
        return None
    return _identity(value, "invalid_memory_identity")


def _candidate_result(
    row: StoreRow,
    *,
    source_ids: tuple[str, ...] | None = None,
    evidence_refs: tuple[str, ...] | None = None,
) -> MemoryCandidate:
    action = row["action"]
    status = row["status"]
    revision = row["candidate_revision"]
    if action not in _ACTIONS or status not in _CANDIDATE_STATUSES:
        raise OperationError("invalid_memory_candidate")
    if type(revision) is not int or revision < 1:
        raise OperationError("invalid_memory_candidate")
    scope = Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"]))
    refs = _decode_refs(row["source_ids"]) if source_ids is None else source_ids
    evidence = _decode_refs(row["evidence_refs"]) if evidence_refs is None else evidence_refs
    suggestion_reason = _optional_code(
        row["suggestion_reason"], "invalid_memory_suggestion_reason"
    )
    if suggestion_reason is not None and (
        suggestion_reason not in _SUGGESTION_REASONS
        or action not in {"add", "supersede"}
        or (action == "supersede" and suggestion_reason != "explicit_correction")
        or (action == "add" and suggestion_reason == "explicit_correction")
    ):
        raise OperationError("invalid_memory_suggestion_reason")
    created_at = _now(row["created_at"])
    updated_at = _now(row["updated_at"])
    return MemoryCandidate(
        candidate_id=_identity(row["candidate_id"], "invalid_memory_candidate"),
        scope=scope,
        subject_id=_identity(row["subject_id"], "invalid_memory_subject"),
        predicate=_code(row["predicate"], "invalid_memory_predicate"),
        value=_memory_value(row["value"]),
        action=cast(MemoryAction, action),
        target_fact_id=_optional(row["target_fact_id"]),
        source_ids=refs,
        evidence_refs=evidence,
        candidate_revision=revision,
        status=status,
        conflict_set_id=_optional(row["conflict_set_id"]),
        skip_reason=_optional_code(row["skip_reason"], "invalid_memory_skip_reason"),
        suggestion_reason=cast(MemorySuggestionReason | None, suggestion_reason),
        applied_fact_id=_optional(row["applied_fact_id"]),
        applied_event_id=_optional(row["applied_event_id"]),
        actor=_identity(row["actor"], "invalid_memory_actor"),
        created_at=created_at,
        updated_at=updated_at,
        observed_at=_optional_time(row["observed_at"]),
        valid_from=_optional_time(row["valid_from"]),
        valid_to=_optional_time(row["valid_to"]),
    )


def _candidate_payload_digest(
    *,
    bot_id: str,
    group_id: str,
    source_ids: tuple[str, ...],
    subject_id: str,
    predicate: str,
    value: str,
    action: MemoryAction,
    target_fact_id: str | None,
    skip_reason: str,
    suggestion_reason: str,
    valid_from: float | None,
    valid_to: float | None,
    expected_target_revision: int | None,
) -> str:
    return payload_digest(
        {
            "action": action,
            "bot_id": bot_id,
            "expected_target_revision": expected_target_revision,
            "group_id": group_id,
            "predicate": predicate,
            "skip_reason": skip_reason,
            "source_ids": source_ids,
            "subject_id": subject_id,
            "suggestion_reason": suggestion_reason,
            "target_fact_id": target_fact_id or "",
            "valid_from": valid_from,
            "valid_to": valid_to,
            "value": value,
        }
    )


def _fact_result(
    row: StoreRow,
    *,
    source_ids: tuple[str, ...] | None = None,
    evidence_refs: tuple[str, ...] | None = None,
) -> MemoryFact:
    status = row["status"]
    revision = row["fact_revision"]
    if status not in _FACT_STATUSES or type(revision) is not int or revision < 1:
        raise OperationError("invalid_memory_fact")
    refs = _decode_refs(row["source_ids"]) if source_ids is None else source_ids
    evidence = _decode_refs(row["evidence_refs"]) if evidence_refs is None else evidence_refs
    return MemoryFact(
        fact_id=_identity(row["fact_id"], "invalid_memory_fact"),
        scope=Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"])),
        subject_id=_identity(row["subject_id"], "invalid_memory_subject"),
        predicate=_code(row["predicate"], "invalid_memory_predicate"),
        value=_memory_value(row["value"]),
        source_ids=refs,
        evidence_refs=evidence,
        fact_revision=revision,
        status=status,
        valid_from=_optional_time(row["valid_from"]),
        valid_to=_optional_time(row["valid_to"]),
        created_at=_now(row["created_at"]),
        updated_at=_now(row["updated_at"]),
        observed_at=_optional_time(row["observed_at"]),
        applied_at=_optional_time(row["applied_at"]),
        supersedes_fact_id=(
            _identity(row["supersedes_fact_id"], "invalid_memory_fact")
            if row["supersedes_fact_id"]
            else None
        ),
    )


def _revocation_result(row: StoreRow) -> MemoryRevocationReceipt:
    status = row["status"]
    source_revision = row["source_revision"]
    candidate_count = row["candidate_count"]
    fact_count = row["fact_count"]
    if (
        status != "applied"
        or type(source_revision) is not int
        or source_revision < 1
        or type(candidate_count) is not int
        or candidate_count < 0
        or type(fact_count) is not int
        or fact_count < 0
        or not isinstance(row["receipt_id"], str)
        or not row["receipt_id"]
        or row["applied_at"] is None
    ):
        raise OperationError("invalid_memory_revocation")
    return MemoryRevocationReceipt(
        revocation_id=_identity(row["revocation_id"], "invalid_memory_revocation"),
        scope=Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"])),
        source_id=_identity(row["source_id"], "invalid_memory_source"),
        source_revision=source_revision,
        status="applied",
        receipt_id=_identity(row["receipt_id"], "invalid_memory_revocation"),
        candidate_count=candidate_count,
        fact_count=fact_count,
        applied_at=_now(row["applied_at"]),
    )


def _fact_extraction_result(row: StoreRow) -> MemoryFactExtractionReceipt:
    source_revision = row["source_revision"]
    result_status = row["result_status"]
    encoded_candidates = row["candidate_ids_json"]
    if (
        row["domain"] != "fact"
        or type(source_revision) is not int
        or source_revision < 1
        or result_status not in {"candidates", "no_evidence"}
        or not isinstance(encoded_candidates, str)
    ):
        raise OperationError("invalid_memory_fact_extraction_result")
    try:
        decoded = json.loads(encoded_candidates)
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_memory_fact_extraction_result") from exc
    if not isinstance(decoded, list):
        raise OperationError("invalid_memory_fact_extraction_result")
    decoded_items = cast(list[object], decoded)
    if any(not isinstance(item, str) for item in decoded_items):
        raise OperationError("invalid_memory_fact_extraction_result")
    candidate_values = cast(list[str], decoded_items)
    candidate_ids = tuple(
        _identity(item, "invalid_memory_fact_extraction_result") for item in candidate_values
    )
    if (
        len(candidate_ids) > 3
        or tuple(sorted(set(candidate_ids))) != candidate_ids
        or (result_status == "candidates") != bool(candidate_ids)
    ):
        raise OperationError("invalid_memory_fact_extraction_result")
    return MemoryFactExtractionReceipt(
        result_id=_identity(row["result_id"], "invalid_memory_fact_extraction_result"),
        scope=Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"])),
        source_id=_identity(row["source_id"], "invalid_memory_source"),
        domain="fact",
        extractor_version=_code(
            row["extractor_version"], "invalid_memory_fact_extraction_result"
        ),
        source_revision=source_revision,
        result_status=cast(Literal["candidates", "no_evidence"], result_status),
        candidate_ids=candidate_ids,
        result_digest=_identity(
            row["result_digest"], "invalid_memory_fact_extraction_result"
        ),
        created_at=_now(row["created_at"]),
    )


def _matter_revision(value: int) -> None:
    if type(value) is not int or value < 1:
        raise OperationError("invalid_memory_revision")


def _matter_result(row: StoreRow) -> MemoryMatter:
    state = row["state"]
    if state not in _MATTER_STATES:
        raise OperationError("invalid_memory_matter_state")
    return MemoryMatter(
        matter_id=str(row["matter_id"]),
        scope=Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"])),
        subject_id=str(row["subject_id"]),
        source_id=str(row["source_id"]),
        source_revision=int(row["source_revision"]),
        summary=str(row["summary"]),
        condition=str(row["condition_text"]) or None,
        state=cast(MatterState, state),
        revision=int(row["revision"]),
        observed_at=_optional_time(row["observed_at"]),
        due_at=_optional_time(row["due_at"]),
        expires_at=_now(row["expires_at"]),
        replaces_matter_id=str(row["replaces_matter_id"]),
        reason=str(row["reason"]),
        actor=str(row["actor"]),
        created_at=_now(row["created_at"]),
        updated_at=_now(row["updated_at"]),
    )


def _matter_payload(matter: MemoryMatter) -> tuple[object, ...]:
    return (
        matter.scope,
        matter.subject_id,
        matter.source_id,
        matter.source_revision,
        matter.summary,
        matter.condition,
        matter.observed_at,
        matter.due_at,
        matter.expires_at,
        matter.replaces_matter_id,
        matter.actor,
    )


def _matter_pointer(matter: MemoryMatter) -> MemoryMatterPointer:
    return MemoryMatterPointer(
        matter.matter_id,
        matter.scope,
        matter.subject_id,
        matter.summary,
        matter.condition,
        matter.observed_at,
        matter.due_at,
        matter.expires_at,
        matter.revision,
        matter.source_id,
        matter.source_revision,
    )


class MemoryService:
    """Own N6 candidate/fact state behind the shared Store writer."""

    def __init__(
        self,
        store: Store,
        policy: Policy,
        *,
        clock: Callable[[], float] = time.time,
        self_nickname_enabled: bool = False,
        self_nickname_groups: Collection[str] = (),
    ) -> None:
        self.store = store
        self.policy = policy
        self._clock = clock
        if type(self_nickname_enabled) is not bool or isinstance(self_nickname_groups, str):
            raise OperationError("invalid_self_nickname_config")
        groups = frozenset(self_nickname_groups)
        if len(groups) > 128:
            raise OperationError("invalid_self_nickname_config")
        for group in groups:
            _identity(group, "invalid_self_nickname_config", limit=64)
        self._self_nickname_enabled = self_nickname_enabled
        self._self_nickname_groups = groups
        self._self_nickname_archive: ArchiveService | None = None

    @property
    def self_nickname_enabled(self) -> bool:
        return self._self_nickname_enabled

    def disable_self_nickname(self) -> None:
        self._self_nickname_enabled = False

    def bind_self_nickname_archive(self, archive: ArchiveService) -> None:
        """Startup binds the sole active Archive without opening a second spool."""
        if archive.store is not self.store or archive.policy is not self.policy:
            raise OperationError("self_nickname_dependency_mismatch")
        if self._self_nickname_archive is not None and self._self_nickname_archive is not archive:
            raise RuntimeError("self nickname archive already bound")
        self._self_nickname_archive = archive

    def unbind_self_nickname_archive(self, archive: ArchiveService) -> None:
        """Teardown invalidates in-flight commands before the bound spool is closed."""
        if self._self_nickname_archive is not archive:
            raise RuntimeError("self nickname archive binding mismatch")
        self._self_nickname_archive = None

    def _now(self) -> float:
        return _now(self._clock())

    @staticmethod
    def _validate_scope(scope: Scope, policy: Policy) -> None:
        if scope.bot_id != policy.bot_id:
            raise OperationError("denied")

    @staticmethod
    def _validate_sources(
        db: StoreConnection,
        source_ids: tuple[str, ...],
        scope: Scope,
        subject_id: str,
    ) -> None:
        for source_id in source_ids:
            row = db.execute(
                "SELECT * FROM archive_sources WHERE source_id=?", (source_id,)
            ).fetchone()
            tombstone = db.execute(
                "SELECT * FROM archive_source_tombstones WHERE source_id=?", (source_id,)
            ).fetchone()
            if tombstone is not None and (
                tombstone["bot_id"] != scope.bot_id or tombstone["group_id"] != scope.group_id
            ):
                raise OperationError("denied")
            if row is None:
                if tombstone is not None:
                    raise OperationError("source_revoked")
                raise OperationError("source_not_found")
            if row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
                raise OperationError("denied")
            # A tombstone wins over a stale active row. Check it on every read
            # and write rather than trusting status alone.
            if tombstone is not None or row["status"] != "active":
                raise OperationError("source_revoked")
            if row["source_kind"] != "human_message" or row["speaker_kind"] != "human":
                raise OperationError("source_kind_forbidden")
            if row["speaker_id"] != subject_id:
                raise OperationError("source_author_mismatch")

    @staticmethod
    def _source_observed_at(db: StoreConnection, source_id: str) -> float:
        row = db.execute(
            "SELECT observed_at FROM archive_sources WHERE source_id=?", (source_id,)
        ).fetchone()
        if row is None:
            raise OperationError("source_not_found")
        return _now(row["observed_at"])

    def _authorize_source_and_actor(
        self,
        db: StoreConnection,
        *,
        actor: str,
        scope: Scope,
        source_ids: tuple[str, ...],
        subject_id: str,
    ) -> None:
        self._validate_sources(db, source_ids, scope, subject_id)
        self.policy.check_transaction(db, subject_id, scope, "message.read", "", "", False, False)
        self.policy.check_transaction(db, subject_id, scope, "memory.archive", "", "", False, False)
        self.policy.check_transaction(db, subject_id, scope, "memory.learn", "", "", False, False)
        if actor != subject_id:
            self.policy.check_transaction(db, actor, scope, "memory.learn", "", "", False, False)

    def _authorize_actor_capability(
        self, db: StoreConnection, actor: str, scope: Scope, action: str
    ) -> None:
        self.policy.check_transaction(db, actor, scope, action, "", "", False, False)

    def assert_retrieval_actor_transaction(
        self, db: StoreConnection, actor: str, scope: Scope
    ) -> int:
        """Check the viewer gate inside a model or send intent transaction."""
        return self.policy.check_transaction(
            db, actor, scope, "memory.retrieve", "", "", False, False
        )

    def assert_retrieval_source_transaction(
        self,
        db: StoreConnection,
        subject_id: str,
        source_ids: tuple[str, ...],
        scope: Scope,
    ) -> None:
        """Check exact source/tombstone and long-term visibility gates."""
        self._validate_sources(db, source_ids, scope, subject_id)
        for action in ("message.read", "memory.archive", "memory.learn"):
            self.policy.check_transaction(
                db, subject_id, scope, action, "", "", False, False
            )

    def assert_retrieval_fact_transaction(
        self,
        db: StoreConnection,
        *,
        fact_id: str,
        fact_revision: int,
        scope: Scope,
        subject_id: str,
        predicate: str,
        value: str,
        source_ids: tuple[str, ...],
        evidence_refs: tuple[str, ...],
    ) -> None:
        """Match one active fact and its visible refs in the same transaction."""
        row = db.execute(
            "SELECT * FROM memory_facts WHERE fact_id=?", (fact_id,)
        ).fetchone()
        if (
            row is None
            or row["status"] != "active"
            or not _fact_is_current(row, self._now())
            or row["fact_revision"] != fact_revision
            or row["bot_id"] != scope.bot_id
            or row["group_id"] != scope.group_id
            or row["subject_id"] != subject_id
            or row["predicate"] != predicate
            or row["value"] != value
        ):
            raise OperationError("stale_retrieval")
        stored_sources = _decode_refs(row["source_ids"])
        stored_evidence = _decode_refs(row["evidence_refs"])
        if stored_sources != source_ids or stored_evidence != evidence_refs:
            raise OperationError("stale_retrieval")
        active_refs = self._active_source_refs(db, source_ids, scope, subject_id)
        visible_evidence = tuple(ref for ref in stored_evidence if ref in active_refs)
        if active_refs != source_ids or visible_evidence != evidence_refs:
            raise OperationError("stale_retrieval")

    @staticmethod
    def _matter_row(db: StoreConnection, matter_id: str, scope: Scope) -> MemoryMatter:
        row = db.execute("SELECT * FROM memory_matters WHERE matter_id=?", (matter_id,)).fetchone()
        if row is None:
            raise OperationError("matter_not_found")
        if row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
            raise OperationError("denied")
        return _matter_result(row)

    def _assert_matter_source(
        self,
        db: StoreConnection,
        matter: MemoryMatter,
        *,
        actor: str | None = None,
    ) -> None:
        if actor is None:
            self.assert_retrieval_source_transaction(
                db,
                matter.subject_id,
                (matter.source_id,),
                matter.scope,
            )
        else:
            self._authorize_source_and_actor(
                db,
                actor=actor,
                scope=matter.scope,
                source_ids=(matter.source_id,),
                subject_id=matter.subject_id,
            )
        source = db.execute(
            "SELECT source_revision,observed_at FROM archive_sources WHERE source_id=?", (matter.source_id,)
        ).fetchone()
        if source is None or source["source_revision"] != matter.source_revision:
            raise OperationError("source_revision_conflict")
        if matter.observed_at is not None and matter.observed_at != source["observed_at"]:
            raise OperationError("matter_observed_at_mismatch")

    def _visible_matter(self, db: StoreConnection, matter: MemoryMatter) -> bool:
        try:
            self._assert_matter_source(db, matter)
        except OperationError as exc:
            if exc.code in {"denied", "source_revoked", "source_not_found", "source_revision_conflict"}:
                return False
            raise
        return True

    @staticmethod
    def _audit_matter(db: StoreConnection, matter: MemoryMatter, *, actor: str, code: str) -> None:
        db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
            (
                "memory_matter",
                matter.matter_id,
                matter.revision,
                code,
                json.dumps(
                    {
                        "actor": actor,
                        "source_id": matter.source_id,
                        "source_revision": matter.source_revision,
                    },
                    separators=(",", ":"),
                ),
            ),
        )

    @staticmethod
    def invalidate_matter_qualification_transaction(
        db: StoreConnection, *, bot_id: str, actor: str, now: float,
        remains_qualified: Callable[[str, Scope], bool],
    ) -> None:
        """Policy calls this owner inside its revocation transaction, before regrant can race."""
        rows = db.execute(
            "SELECT * FROM memory_matters WHERE bot_id=? "
            "AND state IN ('candidate','approved','active') ORDER BY matter_id", (bot_id,),
        ).fetchall()
        for row in rows:
            matter = _matter_result(row)
            if remains_qualified(matter.subject_id, matter.scope):
                continue
            updated = replace(matter, state="cancelled", reason="permission_revoked",
                              revision=matter.revision + 1, updated_at=now)
            db.execute(
                "UPDATE memory_matters SET state='cancelled',reason='permission_revoked',"
                "revision=?,updated_at=? WHERE matter_id=? AND revision=?",
                (updated.revision, now, matter.matter_id, matter.revision),
            )
            MemoryService._audit_matter(db, updated, actor=actor, code="permission_revoked")

    def _new_matter(
        self,
        *,
        actor: str,
        scope: Scope,
        operation_id: str,
        source_id: str,
        expected_source_revision: int,
        subject_id: str,
        summary: str,
        expires_at: float,
        due_at: float | None,
        observed_at: float | None,
        condition: str | None,
        replaces_matter_id: str = "",
    ) -> MemoryMatter:
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        operation_id = _identity(operation_id, "invalid_memory_operation")
        subject_id = _identity(subject_id, "invalid_memory_subject", limit=64)
        source_id = _identity(source_id, "invalid_memory_source")
        _matter_revision(expected_source_revision)
        summary = _memory_value(summary)
        condition = None if condition is None else _memory_value(condition)
        observed_at, due_at = _optional_time(observed_at), _optional_time(due_at)
        expires_at = _now(expires_at)
        now = self._now()
        if expires_at <= now:
            raise OperationError("matter_expired")
        return MemoryMatter(
            matter_id=_stable_id("matter", scope.bot_id, scope.group_id, subject_id, operation_id),
            scope=scope,
            subject_id=subject_id,
            source_id=source_id,
            source_revision=expected_source_revision,
            summary=summary,
            condition=condition,
            state="candidate",
            revision=1,
            observed_at=observed_at,
            due_at=due_at,
            expires_at=expires_at,
            replaces_matter_id=replaces_matter_id,
            reason="",
            actor=actor,
            created_at=now,
            updated_at=now,
        )

    def _insert_matter_transaction(self, db: StoreConnection, matter: MemoryMatter) -> MemoryMatter:
        self._assert_matter_source(db, matter, actor=matter.actor)
        prior = db.execute("SELECT * FROM memory_matters WHERE matter_id=?", (matter.matter_id,)).fetchone()
        if prior is not None:
            existing = _matter_result(prior)
            if _matter_payload(existing) != _matter_payload(matter):
                raise OperationError("idempotency_conflict")
            return existing
        db.execute(
            "INSERT INTO memory_matters (matter_id,bot_id,group_id,subject_id,source_id,source_revision,"
            "summary,condition_text,state,revision,observed_at,due_at,expires_at,replaces_matter_id,"
            "reason,actor,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                matter.matter_id,
                matter.scope.bot_id,
                matter.scope.group_id,
                matter.subject_id,
                matter.source_id,
                matter.source_revision,
                matter.summary,
                matter.condition or "",
                matter.state,
                matter.revision,
                matter.observed_at,
                matter.due_at,
                matter.expires_at,
                matter.replaces_matter_id,
                matter.reason,
                matter.actor,
                matter.created_at,
                matter.updated_at,
            ),
        )
        self._audit_matter(db, matter, actor=matter.actor, code="proposed")
        return matter

    async def propose_matter(
        self,
        *,
        actor: str,
        scope: Scope,
        operation_id: str,
        source_id: str,
        expected_source_revision: int,
        subject_id: str,
        summary: str,
        expires_at: float,
        due_at: float | None = None,
        observed_at: float | None = None,
        condition: str | None = None,
    ) -> MemoryMatter:
        """Create a manually reviewed self-authored matter; never infer observation or completion."""
        matter = self._new_matter(
            actor=actor,
            scope=scope,
            operation_id=operation_id,
            source_id=source_id,
            expected_source_revision=expected_source_revision,
            subject_id=subject_id,
            summary=summary,
            expires_at=expires_at,
            due_at=due_at,
            observed_at=observed_at,
            condition=condition,
        )
        return await self.store.transaction(lambda db: self._insert_matter_transaction(db, matter))

    def _change_matter_transaction(
        self,
        db: StoreConnection,
        matter: MemoryMatter,
        *,
        expected_revision: int,
        actor: str,
        capability: str,
        allowed: frozenset[str],
        state: MatterState,
        reason: str,
    ) -> MemoryMatter:
        self._assert_matter_source(db, matter, actor=actor)
        self._authorize_actor_capability(db, actor, matter.scope, capability)
        if matter.revision != expected_revision:
            raise OperationError("revision_conflict")
        if matter.state not in allowed:
            raise OperationError("matter_not_applicable")
        now = self._now()
        if matter.expires_at <= now:
            raise OperationError("matter_expired")
        updated = replace(matter, state=state, reason=reason, revision=matter.revision + 1, updated_at=now)
        db.execute(
            "UPDATE memory_matters SET state=?,reason=?,revision=?,updated_at=? "
            "WHERE matter_id=? AND revision=?",
            (state, reason, updated.revision, now, matter.matter_id, expected_revision),
        )
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise OperationError("revision_conflict")
        self._audit_matter(db, updated, actor=actor, code=reason or state)
        return updated

    async def _change_matter(
        self,
        matter_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
        capability: str,
        allowed: frozenset[str],
        state: MatterState,
        reason: str,
    ) -> MemoryMatter:
        matter_id = _identity(matter_id, "invalid_memory_matter")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        _matter_revision(expected_revision)
        return await self.store.transaction(
            lambda db: self._change_matter_transaction(
                db,
                self._matter_row(db, matter_id, scope),
                expected_revision=expected_revision,
                actor=actor,
                capability=capability,
                allowed=allowed,
                state=state,
                reason=reason,
            )
        )

    async def review_matter(
        self,
        matter_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
        decision: Literal["approved", "rejected"],
    ) -> MemoryMatter:
        if decision not in {"approved", "rejected"}:
            raise OperationError("invalid_memory_decision")
        return await self._change_matter(
            matter_id,
            actor=actor,
            scope=scope,
            expected_revision=expected_revision,
            capability="memory.review",
            allowed=frozenset({"candidate"}),
            state="approved" if decision == "approved" else "cancelled",
            reason="" if decision == "approved" else "rejected",
        )

    async def apply_matter(
        self,
        matter_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
    ) -> MemoryMatter:
        return await self._change_matter(
            matter_id,
            actor=actor,
            scope=scope,
            expected_revision=expected_revision,
            capability="memory.apply",
            allowed=frozenset({"approved"}),
            state="active",
            reason="",
        )

    async def complete_matter(
        self,
        matter_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
    ) -> MemoryMatter:
        return await self._change_matter(
            matter_id,
            actor=actor,
            scope=scope,
            expected_revision=expected_revision,
            capability="memory.apply",
            allowed=frozenset({"active"}),
            state="completed",
            reason="completed",
        )

    async def cancel_matter(
        self,
        matter_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
        reason: str = "cancelled",
    ) -> MemoryMatter:
        reason = _code(reason, "invalid_memory_matter_reason")
        return await self._change_matter(
            matter_id,
            actor=actor,
            scope=scope,
            expected_revision=expected_revision,
            capability="memory.apply",
            allowed=_OPEN_MATTER_STATES,
            state="cancelled",
            reason=reason,
        )

    async def replace_matter(
        self,
        target_matter_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
        operation_id: str,
        source_id: str,
        expected_source_revision: int,
        subject_id: str,
        summary: str,
        expires_at: float,
        due_at: float | None = None,
        observed_at: float | None = None,
        condition: str | None = None,
    ) -> MemoryMatter:
        """Atomically cancel the old active matter and create a new candidate, still unapproved."""
        target_matter_id = _identity(target_matter_id, "invalid_memory_matter")
        _matter_revision(expected_revision)
        candidate = self._new_matter(
            actor=actor,
            scope=scope,
            operation_id=operation_id,
            source_id=source_id,
            expected_source_revision=expected_source_revision,
            subject_id=subject_id,
            summary=summary,
            expires_at=expires_at,
            due_at=due_at,
            observed_at=observed_at,
            condition=condition,
            replaces_matter_id=target_matter_id,
        )

        def commit(db: StoreConnection) -> MemoryMatter:
            target = self._matter_row(db, target_matter_id, candidate.scope)
            if target.subject_id != candidate.subject_id:
                raise OperationError("matter_subject_mismatch")
            # A replay must match its creation payload, but must not cancel a target twice.
            prior = db.execute(
                "SELECT * FROM memory_matters WHERE matter_id=?", (candidate.matter_id,)
            ).fetchone()
            self._assert_matter_source(db, candidate, actor=candidate.actor)
            self._authorize_actor_capability(db, candidate.actor, candidate.scope, "memory.apply")
            if prior is not None:
                existing = _matter_result(prior)
                if _matter_payload(existing) != _matter_payload(candidate):
                    raise OperationError("idempotency_conflict")
                return existing
            self._change_matter_transaction(
                db,
                target,
                actor=candidate.actor,
                expected_revision=expected_revision,
                capability="memory.apply",
                allowed=frozenset({"active"}),
                state="cancelled",
                reason="corrected",
            )
            return self._insert_matter_transaction(db, candidate)

        return await self.store.transaction(commit)

    async def read_matter(self, matter_id: str, *, actor: str, scope: Scope) -> MemoryMatter | None:
        matter_id = _identity(matter_id, "invalid_memory_matter")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)

        def read(db: StoreConnection) -> MemoryMatter | None:
            self.assert_retrieval_actor_transaction(db, actor, scope)
            row = db.execute("SELECT * FROM memory_matters WHERE matter_id=?", (matter_id,)).fetchone()
            if row is None:
                return None
            if row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
                raise OperationError("denied")
            matter = _matter_result(row)
            return matter if self._visible_matter(db, matter) else None

        return await self.store.transaction(read)

    async def list_matters(
        self,
        *,
        actor: str,
        scope: Scope,
        subject_id: str | None = None,
        state: MatterState | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> MemoryMatterPage:
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise OperationError("invalid_memory_page_limit")
        if subject_id is not None:
            subject_id = _identity(subject_id, "invalid_memory_subject", limit=64)
        if state is not None and state not in _MATTER_STATES:
            raise OperationError("invalid_memory_matter_state")
        if after is not None:
            after = _identity(after, "invalid_memory_cursor")

        def read(db: StoreConnection) -> MemoryMatterPage:
            self.assert_retrieval_actor_transaction(db, actor, scope)
            clauses, params = ["bot_id=?", "group_id=?"], [scope.bot_id, scope.group_id]
            if subject_id is not None:
                clauses.append("subject_id=?")
                params.append(subject_id)
            if state is not None:
                clauses.append("state=?")
                params.append(state)
            if after is not None:
                self._matter_row(db, after, scope)
                clauses.append("matter_id>?")
                params.append(after)
            rows = db.execute(
                "SELECT * FROM memory_matters WHERE " + " AND ".join(clauses) + " ORDER BY matter_id LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            items = tuple(
                matter for row in rows[:limit] if self._visible_matter(db, matter := _matter_result(row))
            )
            return MemoryMatterPage(items, str(rows[limit - 1]["matter_id"]) if len(rows) > limit else None)

        return await self.store.transaction(read)

    async def read_active_matters(
        self,
        *,
        actor: str,
        scope: Scope,
        subject_id: str,
        query: str,
        limit: int = 3,
    ) -> tuple[MemoryMatterPointer, ...]:
        """Bounded self-matter candidates, not a semantic confidence or completion judgment.

        Zero lexical overlap still yields recent qualified candidates so a later
        '成功了' can refer to '明天有演出'. The final request decides relevance.
        """
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        subject_id = _identity(subject_id, "invalid_memory_subject", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if type(limit) is not int or not 1 <= limit <= 3:
            raise OperationError("invalid_memory_matter_limit")
        query = query.casefold()

        def read(db: StoreConnection) -> tuple[MemoryMatterPointer, ...]:
            self.assert_retrieval_actor_transaction(db, actor, scope)
            rows = db.execute(
                "SELECT * FROM memory_matters WHERE bot_id=? AND group_id=? AND subject_id=? "
                "AND state='active' AND expires_at>? ORDER BY updated_at DESC,matter_id LIMIT ?",
                (scope.bot_id, scope.group_id, subject_id, self._now(), _MAX_VISIBLE_FACTS),
            ).fetchall()
            visible = [_matter_result(row) for row in rows]
            visible = [matter for matter in visible if self._visible_matter(db, matter)]
            # This is only an ordering hint, never a filter or truth assertion.
            visible.sort(
                key=lambda matter: (
                    not (
                        bool(query)
                        and (query in matter.summary.casefold() or matter.summary.casefold() in query)
                    )
                )
            )
            return tuple(_matter_pointer(matter) for matter in visible[:limit])

        return await self.store.transaction(read)

    def assert_matter_pointer_transaction(
        self,
        db: StoreConnection,
        *,
        actor: str,
        pointer: MemoryMatterPointer,
    ) -> None:
        """Recheck exact current matter, source and author qualification at model/send intent."""
        self._validate_scope(pointer.scope, self.policy)
        self.assert_retrieval_actor_transaction(db, actor, pointer.scope)
        row = db.execute("SELECT * FROM memory_matters WHERE matter_id=?", (pointer.matter_id,)).fetchone()
        if row is None:
            raise OperationError("stale_retrieval")
        matter = _matter_result(row)
        if matter.state != "active" or matter.expires_at <= self._now() or _matter_pointer(matter) != pointer:
            raise OperationError("stale_retrieval")
        self._assert_matter_source(db, matter)

    async def expire_matters(
        self, *, scope: Scope | None = None, limit: int = _MAX_REVOCATION_BATCH,
    ) -> tuple[str, ...]:
        """Bounded internal invalidation; identity receipts disclose no source contents."""
        if scope is not None:
            scope = _scope(scope)
            self._validate_scope(scope, self.policy)
        if type(limit) is not int or not 1 <= limit <= _MAX_REVOCATION_BATCH:
            raise OperationError("invalid_memory_matter_limit")

        def commit(db: StoreConnection) -> tuple[str, ...]:
            now = self._now()
            where = " AND group_id=?" if scope is not None else ""
            parameters: tuple[object, ...] = (self.policy.bot_id,)
            if scope is not None:
                parameters += (scope.group_id,)
            rows = db.execute(
                "SELECT * FROM memory_matters WHERE bot_id=?" + where + " "
                "AND state IN ('candidate','approved','active') AND expires_at<=? "
                "ORDER BY expires_at,matter_id LIMIT ?",
                (*parameters, now, limit),
            ).fetchall()
            ids: list[str] = []
            for row in rows:
                matter = _matter_result(row)
                updated = replace(
                    matter, state="expired", reason="expired", revision=matter.revision + 1, updated_at=now
                )
                db.execute(
                    "UPDATE memory_matters SET state='expired',reason='expired',revision=?,updated_at=? "
                    "WHERE matter_id=? AND revision=?",
                    (updated.revision, now, matter.matter_id, matter.revision),
                )
                self._audit_matter(db, updated, actor="memory-expiry", code="expired")
                ids.append(matter.matter_id)
            return tuple(ids)

        return await self.store.transaction(commit)

    def _self_fact_pointer_transaction(
        self, db: StoreConnection, *, actor: str, scope: Scope, fact_id: str,
    ) -> tuple[MemoryFactPointer, MemoryFact]:
        self._validate_scope(scope, self.policy)
        self.assert_retrieval_actor_transaction(db, actor, scope)
        row = db.execute("SELECT * FROM memory_facts WHERE fact_id=?", (fact_id,)).fetchone()
        if row is None or row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
            raise OperationError("memory_fact_not_found")
        if row["status"] != "active" or not _fact_is_current(row, self._now()):
            raise OperationError("stale_memory_fact")
        fact = _fact_result(row)
        self.assert_retrieval_source_transaction(db, fact.subject_id, fact.source_ids, scope)
        if not fact.evidence_refs or not set(fact.evidence_refs) <= set(fact.source_ids):
            raise OperationError("invalid_memory_fact")
        proof = db.execute(
            "SELECT e.event_id FROM memory_events e JOIN memory_candidates c "
            "ON c.candidate_id=e.candidate_id WHERE e.fact_id=? AND e.status='applied' "
            "AND c.status='applied' AND c.applied_fact_id=e.fact_id "
            "AND c.applied_event_id=e.event_id ORDER BY e.created_at,e.event_id LIMIT 1",
            (fact.fact_id,),
        ).fetchone()
        if proof is None or fact.applied_at is None:
            raise OperationError("memory_fact_not_applied")
        revisions = tuple((source_id, int(db.execute(
            "SELECT source_revision FROM archive_sources WHERE source_id=?", (source_id,),
        ).fetchone()[0])) for source_id in fact.source_ids)
        payload = {
            "scope": scope.model_dump(), "subject": fact.subject_id, "fact": fact.fact_id,
            "revision": fact.fact_revision, "predicate": fact.predicate, "value": fact.value,
            "sources": fact.source_ids, "evidence": fact.evidence_refs,
            "source_revisions": revisions, "valid_from": fact.valid_from, "valid_to": fact.valid_to,
            "applied_at": fact.applied_at, "applied_event_id": str(proof[0]),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return MemoryFactPointer(
            scope=scope, subject_id=fact.subject_id, fact_id=fact.fact_id,
            fact_revision=fact.fact_revision, fact_digest=digest, applied_event_id=str(proof[0]),
            source_ids=fact.source_ids, evidence_refs=fact.evidence_refs, source_revisions=revisions,
        ), fact

    async def fact_pointer(self, fact_id: str, *, actor: str, scope: Scope) -> MemoryFactPointer:
        """Resolve complete current storage, rather than the filtered retrieval DTO."""
        fact_id = _identity(fact_id)
        def read(db: StoreConnection) -> MemoryFactPointer:
            db.execute("BEGIN")
            return self._self_fact_pointer_transaction(
                db, actor=actor, scope=scope, fact_id=fact_id,
            )[0]
        return await self.store.transaction(read)

    def assert_fact_pointer_transaction(
        self, db: StoreConnection, *, actor: str, scope: Scope, pointer: MemoryFactPointer,
    ) -> MemoryFact:
        current, fact = self._self_fact_pointer_transaction(
            db, actor=actor, scope=scope, fact_id=pointer.fact_id,
        )
        if current != pointer:
            raise OperationError("stale_memory_fact")
        return fact

    def _self_alias_pointer_transaction(
        self, db: StoreConnection, *, scope: Scope, row: StoreRow,
    ) -> SelfAliasPointer:
        fact = _fact_result(row)
        self.assert_retrieval_source_transaction(db, fact.subject_id, fact.source_ids, scope)
        if not fact.evidence_refs or not set(fact.evidence_refs) <= set(fact.source_ids):
            raise OperationError("invalid_self_alias_source")
        # A preferred_name label alone is not proof of the trusted self command.
        proof = db.execute(
            "SELECT e.event_id,e.created_at,a.details FROM memory_events e "
            "JOIN memory_candidates c ON c.candidate_id=e.candidate_id "
            "JOIN audit a ON a.kind='memory_self_nickname' "
            "AND json_extract(a.details,'$.applied_event_id')=e.event_id "
            "WHERE e.fact_id=? AND e.status='applied' AND c.status='applied' "
            "AND c.applied_fact_id=e.fact_id AND c.applied_event_id=e.event_id "
            "AND c.bot_id=? AND c.group_id=? AND c.subject_id=? "
            "AND c.actor=c.subject_id AND e.actor=c.subject_id "
            "AND c.predicate=? AND c.value=? ORDER BY e.created_at,e.event_id LIMIT 1",
            (fact.fact_id, scope.bot_id, scope.group_id, fact.subject_id,
             _SELF_NICKNAME_PREDICATE, fact.value),
        ).fetchone()
        if proof is None or fact.applied_at is None:
            raise OperationError("self_alias_command_proof_required")
        receipt = json.loads(str(proof["details"]))
        if (receipt["fact_id"] != fact.fact_id or receipt["subject_id"] != fact.subject_id
                or receipt["actor"] != fact.subject_id or Scope(**receipt["scope"]) != scope
                or receipt["source_id"] not in fact.source_ids):
            raise OperationError("invalid_self_alias_source")
        revisions = tuple((source_id, int(db.execute(
            "SELECT source_revision FROM archive_sources WHERE source_id=?", (source_id,),
        ).fetchone()[0])) for source_id in fact.source_ids)
        if (receipt["source_id"], receipt["source_revision"]) not in revisions:
            raise OperationError("stale_self_alias")
        opening = max(fact.applied_at, fact.valid_from if fact.valid_from is not None else fact.applied_at)
        payload = {
            "scope": scope.model_dump(), "subject": fact.subject_id, "surface": fact.value,
            "fact": fact.fact_id, "revision": fact.fact_revision, "status": fact.status,
            "window_from": opening, "window_to": fact.valid_to,
            "sources": fact.source_ids, "evidence": fact.evidence_refs, "source_revisions": revisions,
            "applied_event_id": str(proof["event_id"]), "command_receipt_id": receipt["receipt_id"],
        }
        return SelfAliasPointer(
            scope=scope, subject_id=fact.subject_id, surface=fact.value,
            fact_id=fact.fact_id, fact_revision=fact.fact_revision,
            fact_digest=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            status=cast(Literal["active", "superseded"], fact.status),
            window_from=opening, window_to=fact.valid_to, applied_event_id=str(proof["event_id"]),
            source_ids=fact.source_ids, evidence_refs=fact.evidence_refs, source_revisions=revisions,
        )

    def _resolve_self_alias_transaction(
        self, db: StoreConnection, *, actor: str, scope: Scope, surface: str, at: float,
    ) -> SelfAliasResolution:
        self._validate_scope(scope, self.policy)
        self.assert_retrieval_actor_transaction(db, actor, scope)
        rows = db.execute(
            "SELECT f.* FROM memory_facts f WHERE f.bot_id=? AND f.group_id=? "
            "AND f.predicate=? AND f.value=? AND f.status IN ('active','superseded') "
            "AND f.suppressed_at IS NULL AND f.applied_at IS NOT NULL AND f.applied_at<=? "
            "AND (f.valid_from IS NULL OR f.valid_from<=?) "
            "AND (f.valid_to IS NULL OR f.valid_to>?) "
            "AND EXISTS (SELECT 1 FROM audit a WHERE a.kind='memory_self_nickname' "
            "AND json_extract(a.details,'$.fact_id')=f.fact_id) ORDER BY f.fact_id LIMIT ?",
            (scope.bot_id, scope.group_id, _SELF_NICKNAME_PREDICATE, surface, at, at, at,
             _MAX_VISIBLE_FACTS + 1),
        ).fetchall()
        if len(rows) > _MAX_VISIBLE_FACTS:
            raise OperationError("self_alias_candidate_limit")
        pointers = tuple(self._self_alias_pointer_transaction(db, scope=scope, row=row) for row in rows)
        subjects = {pointer.subject_id for pointer in pointers}
        status: Literal["missing", "resolved", "ambiguous"] = (
            "missing" if not subjects else "resolved" if len(subjects) == 1 else "ambiguous"
        )
        return SelfAliasResolution(reader_id=actor, scope=scope, surface=surface, at=at,
                                   status=status, candidates=pointers)

    def current_self_alias_transaction(self, db: StoreConnection, *, actor: str,
                                       scope: Scope) -> SelfAliasPointer | None:
        """One active own nickname, proven by the existing self-command ledger."""
        self._validate_scope(scope, self.policy)
        self.assert_retrieval_actor_transaction(db, actor, scope)
        at = self._now()
        rows = db.execute(
            "SELECT * FROM memory_facts WHERE bot_id=? AND group_id=? AND subject_id=? "
            "AND predicate=? AND status='active' AND suppressed_at IS NULL "
            "AND applied_at IS NOT NULL AND applied_at<=? "
            "AND (valid_from IS NULL OR valid_from<=?) AND (valid_to IS NULL OR valid_to>?) "
            "AND EXISTS (SELECT 1 FROM audit a WHERE a.kind='memory_self_nickname' "
            "AND json_extract(a.details,'$.fact_id')=memory_facts.fact_id) LIMIT 2",
            (scope.bot_id, scope.group_id, actor, _SELF_NICKNAME_PREDICATE, at, at, at),
        ).fetchall()
        if len(rows) != 1:
            return None
        return self._self_alias_pointer_transaction(db, scope=scope, row=rows[0])

    async def read_current_self_alias(self, *, actor: str, scope: Scope) -> SelfAliasPointer | None:
        return await self.store.transaction(lambda db: self.current_self_alias_transaction(
            db, actor=actor, scope=scope))

    async def resolve_self_alias(
        self, *, actor: str, scope: Scope, surface: str, at: float | None = None,
    ) -> SelfAliasResolution:
        """Resolve exact self-authored nickname windows with current source authority.

        Superseded facts qualify only in their old window. This deliberately does
        not weaken the active-fact pointer used by Graph and ordinary retrieval.
        Raw text expiry does not expire an approved long-term nickname fact.
        """
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        surface = _identity(surface, "invalid_self_alias_surface", limit=16)
        if (_SELF_NICKNAME_CHARS.fullmatch(surface) is None
                or unicodedata.normalize("NFC", surface) != surface):
            raise OperationError("invalid_self_alias_surface")
        instant = self._now() if at is None else _now(at)
        if instant > self._now():
            raise OperationError("invalid_self_alias_time")
        def read(db: StoreConnection) -> SelfAliasResolution:
            db.execute("BEGIN")
            return self._resolve_self_alias_transaction(
                db, actor=actor, scope=scope, surface=surface, at=instant,
            )
        return await self.store.transaction(read)

    def assert_self_alias_resolution_transaction(
        self, db: StoreConnection, *, actor: str, scope: Scope, frozen: SelfAliasResolution,
    ) -> None:
        """Re-read the full frozen lookup and all current author gates in one transaction."""
        if frozen.reader_id != actor or frozen.scope != scope or frozen.at > self._now():
            raise OperationError("stale_self_alias")
        current = self._resolve_self_alias_transaction(
            db, actor=actor, scope=scope, surface=frozen.surface, at=frozen.at,
        )
        if current != frozen:
            raise OperationError("stale_self_alias")

    def _target_fact(
        self,
        db: StoreConnection,
        target_fact_id: str,
        scope: Scope,
        subject_id: str,
        predicate: str,
    ) -> StoreRow:
        row = db.execute(
            "SELECT * FROM memory_facts WHERE fact_id=?", (target_fact_id,)
        ).fetchone()
        if row is None:
            raise OperationError("target_not_found")
        if row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
            raise OperationError("denied")
        if row["subject_id"] != subject_id or row["predicate"] != predicate:
            raise OperationError("target_mismatch")
        if row["status"] != "active":
            raise OperationError("target_not_active")
        if not self._active_source_refs(
            db, _decode_refs(row["source_ids"]), scope, subject_id
        ):
            raise OperationError("source_revoked")
        return row

    @staticmethod
    def _normalization_target(scope: Scope, subject_id: str, predicate: str, value: str) -> str:
        return _stable_id("memnorm", scope.bot_id, scope.group_id, subject_id, predicate, value)

    @staticmethod
    def _append_ref(existing: tuple[str, ...], incoming: Sequence[str]) -> tuple[str, ...]:
        values = list(existing)
        for value in incoming:
            if value not in values:
                if len(values) >= _MAX_SOURCES:
                    raise OperationError("memory_source_limit")
                values.append(value)
        return tuple(values)

    @staticmethod
    def _insert_fact(
        db: StoreConnection,
        *,
        fact_id: str,
        scope: Scope,
        subject_id: str,
        predicate: str,
        value: str,
        source_ids: tuple[str, ...],
        observed_at: float,
        valid_from: float | None,
        valid_to: float | None,
        now: float,
        supersedes_fact_id: str | None = None,
    ) -> None:
        refs = _json_refs(source_ids)
        db.execute(
            "INSERT INTO memory_facts("
            "fact_id,bot_id,group_id,subject_id,predicate,value,source_ids,evidence_refs,"
            "fact_revision,status,observed_at,valid_from,valid_to,applied_at,"
            "supersedes_fact_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fact_id,
                scope.bot_id,
                scope.group_id,
                subject_id,
                predicate,
                value,
                refs,
                refs,
                1,
                "active",
                observed_at,
                valid_from,
                valid_to,
                now,
                supersedes_fact_id,
                now,
                now,
            ),
        )

    @staticmethod
    def _update_fact_sources(
        db: StoreConnection,
        *,
        fact_id: str,
        fact_revision: int,
        source_ids: tuple[str, ...],
        now: float,
    ) -> None:
        refs = _json_refs(source_ids)
        db.execute(
            "UPDATE memory_facts SET source_ids=?,evidence_refs=?,fact_revision=?,"
            "updated_at=? WHERE fact_id=? AND status='active'",
            (refs, refs, fact_revision, now, fact_id),
        )

    async def record_fact_extraction_result(
        self,
        *,
        actor: str,
        scope: Scope,
        source_id: str,
        subject_id: str,
        extractor_version: str,
        expected_source_revision: int,
        candidate_ids: Sequence[str],
    ) -> MemoryFactExtractionReceipt:
        """Persist one authorized factual result, including an empty result."""
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        source_id = _identity(source_id, "invalid_memory_source")
        subject_id = _identity(subject_id, "invalid_memory_subject", limit=64)
        extractor_version = _code(
            extractor_version, "invalid_memory_fact_extraction_result"
        )
        if type(expected_source_revision) is not int or expected_source_revision < 1:
            raise OperationError("invalid_memory_revision")
        if isinstance(candidate_ids, (str, bytes)) or len(candidate_ids) > 3:
            raise OperationError("invalid_memory_fact_extraction_result")
        normalized_ids = tuple(
            sorted(
                {
                    _identity(candidate_id, "invalid_memory_candidate")
                    for candidate_id in candidate_ids
                }
            )
        )
        source_ids = (source_id,)
        result_status: Literal["candidates", "no_evidence"] = (
            "candidates" if normalized_ids else "no_evidence"
        )
        candidate_ids_json = json.dumps(
            list(normalized_ids), ensure_ascii=False, separators=(",", ":")
        )
        result_id = _stable_id(
            "mfr",
            scope.bot_id,
            scope.group_id,
            source_id,
            "fact",
            extractor_version,
        )
        result_digest = payload_digest(
            {
                "bot_id": scope.bot_id,
                "candidate_ids": normalized_ids,
                "domain": "fact",
                "extractor_version": extractor_version,
                "group_id": scope.group_id,
                "result_status": result_status,
                "source_id": source_id,
                "source_revision": expected_source_revision,
            }
        )
        now = self._now()

        def commit(db: StoreConnection) -> StoreRow | None:
            self._authorize_source_and_actor(
                db,
                actor=actor,
                scope=scope,
                source_ids=source_ids,
                subject_id=subject_id,
            )
            source = db.execute(
                "SELECT source_revision FROM archive_sources "
                "WHERE source_id=? AND bot_id=? AND group_id=?",
                (source_id, scope.bot_id, scope.group_id),
            ).fetchone()
            if source is None:
                raise OperationError("source_not_found")
            if source["source_revision"] != expected_source_revision:
                raise OperationError("archive_source_changed")

            for candidate_id in normalized_ids:
                candidate = db.execute(
                    "SELECT bot_id,group_id,subject_id,source_ids FROM memory_candidates "
                    "WHERE candidate_id=?",
                    (candidate_id,),
                ).fetchone()
                if (
                    candidate is None
                    or candidate["bot_id"] != scope.bot_id
                    or candidate["group_id"] != scope.group_id
                    or candidate["subject_id"] != subject_id
                    or _decode_refs(candidate["source_ids"]) != source_ids
                ):
                    raise OperationError("invalid_memory_candidate")

            existing = db.execute(
                "SELECT * FROM memory_fact_extraction_results WHERE result_id=?",
                (result_id,),
            ).fetchone()
            if existing is not None:
                if existing["result_digest"] != result_digest:
                    record_payload_conflict(
                        db,
                        kind="memory_fact_extraction_result",
                        identity=result_id,
                        revision=expected_source_revision,
                        reason="identity_payload_mismatch",
                        stored_digest=str(existing["result_digest"]),
                        incoming_digest=result_digest,
                    )
                    return None
                return existing

            db.execute(
                """
                INSERT INTO memory_fact_extraction_results(
                    result_id,bot_id,group_id,source_id,domain,extractor_version,
                    source_revision,result_status,candidate_ids_json,result_digest,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result_id,
                    scope.bot_id,
                    scope.group_id,
                    source_id,
                    "fact",
                    extractor_version,
                    expected_source_revision,
                    result_status,
                    candidate_ids_json,
                    result_digest,
                    now,
                ),
            )
            return db.execute(
                "SELECT * FROM memory_fact_extraction_results WHERE result_id=?",
                (result_id,),
            ).fetchone()

        row = await self.store.transaction(commit)
        if row is None:
            raise OperationError("payload_conflict")
        return _fact_extraction_result(row)

    def _self_nickname_source_transaction(
        self, db: StoreConnection, event: Event, *, actor: str, target_id: str,
        source_id: str, expected_source_revision: int, archive: ArchiveService,
    ) -> StoreRow:
        scope = _scope(event.scope)
        self._validate_scope(scope, self.policy)
        if (not self._self_nickname_enabled or scope.group_id not in self._self_nickname_groups
                or self._self_nickname_archive is not archive):
            raise OperationError("self_nickname_disabled")
        if actor != event.user_id or target_id != actor or actor == self.policy.bot_id:
            raise OperationError("self_nickname_identity_mismatch")
        self._authorize_source_and_actor(db, actor=actor, scope=scope,
                                        source_ids=(source_id,), subject_id=actor)
        source = db.execute("SELECT * FROM archive_sources WHERE source_id=?", (source_id,)).fetchone()
        assert source is not None
        if (source["source_revision"] != expected_source_revision
                or source["origin_event_id"] != event.event_id
                or source["platform_message_id"] != event.message_id
                or (event.event_time is not None and source["observed_at"] != event.event_time)):
            raise OperationError("self_nickname_source_mismatch")
        return source

    def assert_self_nickname_receipt_transaction(
        self, db: StoreConnection, event: Event, receipt: SelfNicknameReceipt,
    ) -> None:
        """Recheck the actual own-command result at the controlled reply boundary."""
        archive = self._self_nickname_archive
        if archive is None:
            raise OperationError("self_nickname_disabled")
        if (receipt.scope != event.scope or receipt.actor != event.user_id
                or receipt.subject_id != event.user_id
                or receipt.source_event_id != event.event_id
                or receipt.platform_message_id != event.message_id):
            raise OperationError("self_nickname_identity_mismatch")
        self._self_nickname_source_transaction(
            db, event, actor=event.user_id, target_id=event.user_id,
            source_id=receipt.source_id, expected_source_revision=receipt.source_revision,
            archive=archive,
        )
        fact = db.execute("SELECT * FROM memory_facts WHERE fact_id=?", (receipt.fact_id,)).fetchone()
        if (fact is None or not _fact_is_current(fact, self._now())
                or fact["fact_revision"] != receipt.fact_revision
                or fact["subject_id"] != event.user_id
                or fact["predicate"] != _SELF_NICKNAME_PREDICATE
                or receipt.source_id not in _decode_refs(fact["source_ids"])):
            raise OperationError("self_nickname_receipt_not_current")
        self.assert_retrieval_source_transaction(
            db, event.user_id, _decode_refs(fact["source_ids"]), receipt.scope,
        )

    async def read_self_nickname_receipt(self, event: Event) -> SelfNicknameReceipt | None:
        """Read only this authenticated event's receipt, never generic fact contents."""
        if not self._self_nickname_enabled or event.scope.group_id not in self._self_nickname_groups:
            return None
        receipt_id = _stable_id("selfnick", event.scope.bot_id, event.scope.group_id, event.event_id)

        def read(db: StoreConnection) -> SelfNicknameReceipt | None:
            row = db.execute(
                "SELECT details FROM audit WHERE kind='memory_self_nickname' "
                "AND identity=? ORDER BY id DESC LIMIT 1", (receipt_id,),
            ).fetchone()
            if row is None:
                return None
            data = json.loads(str(row["details"]))
            del data["expected_fact_id"], data["expected_fact_revision"]
            data["scope"] = Scope(**data["scope"])
            receipt = SelfNicknameReceipt(**data)
            self.assert_self_nickname_receipt_transaction(db, event, receipt)
            return receipt

        return await self.store.transaction(read)

    async def prepare_self_nickname_target(
        self, event: Event, *, actor: str, target_id: str, source_id: str,
        expected_source_revision: int,
    ) -> tuple[str | None, int]:
        """Read only the own scoped nickname CAS identity, without generic retrieve grants."""
        if self_nickname_command(event) is None:
            raise OperationError("self_nickname_command_required")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        target_id = _identity(target_id, "invalid_memory_subject", limit=64)
        source_id = _identity(source_id, "invalid_memory_source")
        _identity(event.message_id, "invalid_self_nickname_event", limit=64)
        if type(expected_source_revision) is not int or expected_source_revision < 1:
            raise OperationError("invalid_memory_revision")
        archive = self._self_nickname_archive
        if archive is None:
            raise OperationError("self_nickname_archive_unavailable")

        def read(db: StoreConnection) -> tuple[str | None, int]:
            self._self_nickname_source_transaction(db, event, actor=actor, target_id=target_id,
                source_id=source_id,expected_source_revision=expected_source_revision,archive=archive)
            row = db.execute("SELECT fact_id,fact_revision FROM memory_facts WHERE bot_id=? AND group_id=? "
                "AND subject_id=? AND predicate=? AND status='active'",
                (event.scope.bot_id,event.scope.group_id,actor,_SELF_NICKNAME_PREDICATE)).fetchone()
            return (None, 0) if row is None else (str(row["fact_id"]), int(row["fact_revision"]))

        return await self.store.transaction(read)

    async def apply_self_nickname(
        self,
        event: Event,
        *,
        actor: str,
        target_id: str,
        source_id: str,
        expected_source_revision: int,
        expected_fact_id: str | None = None,
        expected_fact_revision: int = 0,
    ) -> SelfNicknameReceipt | None:
        """Consume only an authenticated ingress command, never a model tool call.

        Archive raw must equal this Event for every new application. It is read
        transiently with its original expiry; an already applied event can only
        return its receipt while that exact fact and source remain current.
        """
        if not self._self_nickname_enabled:
            return None
        if type(event) is not Event:
            raise OperationError("invalid_self_nickname_event")
        scope = _scope(event.scope)
        self._validate_scope(scope, self.policy)
        if scope.group_id not in self._self_nickname_groups:
            return None
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        target_id = _identity(target_id, "invalid_memory_subject", limit=64)
        if actor != event.user_id or target_id != actor or actor == self.policy.bot_id:
            raise OperationError("self_nickname_identity_mismatch")
        name = self_nickname_command(event)
        if name is None:
            return None
        source_id = _identity(source_id, "invalid_memory_source")
        _identity(event.message_id, "invalid_self_nickname_event", limit=64)
        if type(expected_source_revision) is not int or expected_source_revision < 1:
            raise OperationError("invalid_memory_revision")
        if type(expected_fact_revision) is not int or expected_fact_revision < 0:
            raise OperationError("invalid_memory_revision")
        if expected_fact_id is None:
            if expected_fact_revision != 0:
                raise OperationError("invalid_memory_target_revision")
        else:
            expected_fact_id = _identity(expected_fact_id, "invalid_memory_fact")
            if expected_fact_revision == 0:
                raise OperationError("invalid_memory_target_revision")
        archive = self._self_nickname_archive
        if archive is None:
            raise OperationError("self_nickname_archive_unavailable")
        receipt_id = _stable_id("selfnick", scope.bot_id, scope.group_id, event.event_id)

        def inspect(db: StoreConnection) -> tuple[StoreRow, SelfNicknameReceipt | None]:
            source = self._self_nickname_source_transaction(
                db, event, actor=actor, target_id=target_id, source_id=source_id,
                expected_source_revision=expected_source_revision, archive=archive
            )
            prior = db.execute(
                "SELECT details FROM audit WHERE kind='memory_self_nickname' "
                "AND identity=? ORDER BY id DESC LIMIT 1",
                (receipt_id,),
            ).fetchone()
            if prior is not None:
                data = json.loads(str(prior["details"]))
                # CAS protects a new write. Returning an actual prior receipt
                # does not reapply it when a fresh target lookup has advanced.
                del data["expected_fact_id"], data["expected_fact_revision"]
                data["scope"] = Scope(**data["scope"])
                receipt = SelfNicknameReceipt(**data)
                if (
                    receipt.source_id != source_id
                    or receipt.source_revision != expected_source_revision
                    or receipt.actor != actor
                    or receipt.subject_id != target_id
                ):
                    raise OperationError("payload_conflict")
                fact = db.execute("SELECT * FROM memory_facts WHERE fact_id=?", (receipt.fact_id,)).fetchone()
                if (
                    fact is None
                    or not _fact_is_current(fact, self._now())
                    or fact["fact_revision"] != receipt.fact_revision
                ):
                    raise OperationError("self_nickname_receipt_not_current")
                if fact["value"] != name:
                    raise OperationError("payload_conflict")
                self.assert_retrieval_source_transaction(db, actor, _decode_refs(fact["source_ids"]), scope)
                return source, receipt
            if source["text_expires_at"] is None or self._now() >= source["text_expires_at"]:
                raise OperationError("archive_text_unavailable")
            return source, None

        source, prior = await self.store.transaction(inspect)
        if prior is not None:
            return prior
        body = await archive.read_local_text(actor, source_id, scope)
        if body is None:
            raise OperationError("archive_text_unavailable")
        if body != event.text:
            raise OperationError("self_nickname_text_mismatch")
        expires_at = source["text_expires_at"]

        def commit(db: StoreConnection) -> SelfNicknameReceipt:
            current_source, prior = inspect(db)
            if prior is not None:
                return prior
            if current_source["text_expires_at"] != expires_at:
                raise OperationError("archive_source_changed")
            predicate = _SELF_NICKNAME_PREDICATE
            current = db.execute(
                "SELECT * FROM memory_facts WHERE bot_id=? AND group_id=? "
                "AND subject_id=? AND predicate=? AND status='active'",
                (scope.bot_id, scope.group_id, actor, predicate),
            ).fetchone()
            if (current is None) != (expected_fact_id is None) or (
                current is not None
                and (
                    current["fact_id"] != expected_fact_id
                    or current["fact_revision"] != expected_fact_revision
                )
            ):
                raise OperationError("revision_conflict")
            if db.execute(
                "SELECT 1 FROM memory_candidates WHERE bot_id=? AND group_id=? "
                "AND subject_id=? AND predicate=? "
                "AND status IN ('pending','conflict_pending','approved') LIMIT 1",
                (scope.bot_id, scope.group_id, actor, predicate),
            ).fetchone():
                raise OperationError("self_nickname_conflict_pending")
            if db.execute(
                "SELECT 1 FROM memory_observations AS o JOIN memory_candidates AS c "
                "ON c.candidate_id=o.candidate_id WHERE o.source_id=? AND c.predicate=? LIMIT 1",
                (source_id, predicate),
            ).fetchone():
                raise OperationError("self_nickname_source_already_used")
            now = self._now()
            observed_at = _now(current_source["observed_at"])
            action: MemoryAction = "add"
            if current is not None:
                current = self._target_fact(db, str(current["fact_id"]), scope, actor, predicate)
                if not _fact_is_current(current, now):
                    raise OperationError("fact_not_current")
                if current["observed_at"] is not None and observed_at < current["observed_at"]:
                    raise OperationError("correction_stale")
                action = "reinforce" if current["value"] == name else "supersede"
            target = expected_fact_id or self._normalization_target(scope, actor, predicate, name)
            candidate_id = _stable_id("memcand", target, source_id, action, predicate, name)
            refs = _json_refs((source_id,))
            reason = (
                "stable_preference"
                if action == "add"
                else "explicit_correction"
                if action == "supersede"
                else ""
            )
            db.execute(
                "INSERT INTO memory_candidates(candidate_id,bot_id,group_id,subject_id,predicate,value,"
                "action,target_fact_id,source_ids,evidence_refs,candidate_revision,status,suggestion_reason,"
                "actor,created_at,updated_at,observed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,2,'approved',?,?,?,?,?)",
                (
                    candidate_id,
                    scope.bot_id,
                    scope.group_id,
                    actor,
                    predicate,
                    name,
                    action,
                    expected_fact_id or "",
                    refs,
                    refs,
                    reason,
                    actor,
                    now,
                    now,
                    observed_at,
                ),
            )
            db.execute(
                "INSERT INTO memory_observations(observation_id,target_id,source_id,action,candidate_id,"
                "observed_at,actor) VALUES (?,?,?,?,?,?,?)",
                (
                    _stable_id("memobs", target, source_id, action),
                    target,
                    source_id,
                    action,
                    candidate_id,
                    observed_at,
                    actor,
                ),
            )
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('memory_candidate',?,2,"
                "'trusted_self_nickname',?)",
                (
                    candidate_id,
                    json.dumps(
                        {
                            "actor": actor,
                            "source_id": source_id,
                            "event_id": event.event_id,
                            "policy": "Q02A",
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
            if action == "supersede":
                db.execute(
                    "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('memory_candidate',?,2,"
                    "'correction_target_revision',?)",
                    (
                        candidate_id,
                        json.dumps(
                            {
                                "fact_id": expected_fact_id,
                                "expected_revision": expected_fact_revision,
                                "fact_revision": expected_fact_revision,
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
            row = db.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            assert row is not None
            applied = self._apply_approved_candidate_transaction(
                db,
                row,
                actor=actor,
                expected_revision=2,
                now=now,
                cancelled=lambda: (
                    not self._self_nickname_enabled or self._self_nickname_archive is not archive
                ),
            )
            assert applied.applied_fact_id is not None and applied.applied_event_id is not None
            fact = db.execute(
                "SELECT fact_revision FROM memory_facts WHERE fact_id=?", (applied.applied_fact_id,)
            ).fetchone()
            assert fact is not None
            receipt = SelfNicknameReceipt(
                receipt_id,
                scope,
                actor,
                actor,
                source_id,
                expected_source_revision,
                event.event_id,
                event.message_id,
                candidate_id,
                applied.applied_event_id,
                applied.applied_fact_id,
                int(fact["fact_revision"]),
                now,
            )
            data = {field: getattr(receipt, field) for field in receipt.__dataclass_fields__}
            data["scope"] = scope.model_dump()
            data.update(expected_fact_id=expected_fact_id, expected_fact_revision=expected_fact_revision)
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details) "
                "VALUES ('memory_self_nickname',?,?,'applied',?)",
                (receipt_id, receipt.fact_revision, json.dumps(data, separators=(",", ":"))),
            )
            return receipt

        return await self.store.transaction(commit)

    async def propose(
        self,
        *,
        actor: str,
        scope: Scope,
        source_id: str,
        subject_id: str,
        predicate: str,
        value: str,
        action: MemoryAction,
        target_fact_id: str | None = None,
        expected_target_revision: int | None = None,
        reliable_update: bool = False,
        current_self_correction: bool = False,
        reason: str | None = None,
        suggestion_reason: MemorySuggestionReason | None = None,
        valid_from: float | None = None,
        valid_to: float | None = None,
    ) -> MemoryCandidate:
        """Persist one explicit candidate decision and one evidence observation."""
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        source_ids = _sources((source_id,))
        subject_id = _identity(subject_id, "invalid_memory_subject", limit=64)
        predicate = _code(predicate, "invalid_memory_predicate")
        value = _memory_value(value)
        if action not in _ACTIONS:
            raise OperationError("invalid_memory_action")
        valid_from, valid_to = _validity_window(valid_from, valid_to)
        if type(reliable_update) is not bool:
            raise OperationError("invalid_memory_update_signal")
        if type(current_self_correction) is not bool:
            raise OperationError("invalid_memory_correction_signal")
        if current_self_correction and (action != "supersede" or not reliable_update):
            raise OperationError("invalid_memory_correction_signal")
        if action == "skip":
            reason = _code(reason, "invalid_memory_skip_reason")
        elif reason is not None:
            raise OperationError("invalid_memory_skip_reason")
        if suggestion_reason is not None:
            checked_suggestion_reason = _code(
                suggestion_reason, "invalid_memory_suggestion_reason"
            )
            if (
                checked_suggestion_reason not in _SUGGESTION_REASONS
                or action not in {"add", "supersede"}
                or (action == "supersede" and checked_suggestion_reason != "explicit_correction")
                or (action == "add" and checked_suggestion_reason == "explicit_correction")
            ):
                raise OperationError("invalid_memory_suggestion_reason")
            suggestion_reason = cast(MemorySuggestionReason, checked_suggestion_reason)
        stored_suggestion_reason = suggestion_reason or ""
        target_fact_id = _optional(target_fact_id)
        if expected_target_revision is not None:
            if type(expected_target_revision) is not int or expected_target_revision < 1:
                raise OperationError("invalid_memory_revision")
            if action != "supersede" or target_fact_id is None:
                raise OperationError("invalid_memory_target_revision")
        now = self._now()

        def commit(db: StoreConnection) -> MemoryCandidate | None:
            self._authorize_source_and_actor(
                db,
                actor=actor,
                scope=scope,
                source_ids=source_ids,
                subject_id=subject_id,
            )
            observed_at = self._source_observed_at(db, source_id)

            def audit_payload_mismatch(
                existing: StoreRow,
                *,
                reason_code: str,
                stored_expected_revision: int | None = None,
            ) -> None:
                stored_digest = _candidate_payload_digest(
                    bot_id=str(existing["bot_id"]),
                    group_id=str(existing["group_id"]),
                    source_ids=_decode_refs(existing["source_ids"]),
                    subject_id=str(existing["subject_id"]),
                    predicate=str(existing["predicate"]),
                    value=str(existing["value"]),
                    action=cast(MemoryAction, existing["action"]),
                    target_fact_id=_optional(existing["target_fact_id"]),
                    skip_reason=str(existing["skip_reason"]),
                    suggestion_reason=str(existing["suggestion_reason"]),
                    valid_from=_optional_time(existing["valid_from"]),
                    valid_to=_optional_time(existing["valid_to"]),
                    expected_target_revision=stored_expected_revision,
                )
                incoming_digest = _candidate_payload_digest(
                    bot_id=scope.bot_id,
                    group_id=scope.group_id,
                    source_ids=source_ids,
                    subject_id=subject_id,
                    predicate=predicate,
                    value=value,
                    action=action,
                    target_fact_id=target_fact_id,
                    skip_reason=reason or "",
                    suggestion_reason=stored_suggestion_reason,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    expected_target_revision=expected_target_revision,
                )
                record_payload_conflict(
                    db,
                    kind="memory_candidate",
                    identity=str(existing["candidate_id"]),
                    revision=int(existing["candidate_revision"]),
                    reason=reason_code,
                    stored_digest=stored_digest,
                    incoming_digest=incoming_digest,
                )

            target: StoreRow | None = None
            if action in {"reinforce", "supersede"}:
                if target_fact_id is None:
                    raise OperationError("target_required")
            elif target_fact_id is not None:
                raise OperationError("target_forbidden")

            target_id = target_fact_id or self._normalization_target(scope, subject_id, predicate, value)
            existing_observation = db.execute(
                "SELECT candidate_id FROM memory_observations "
                "WHERE target_id=? AND source_id=? AND action=?",
                (target_id, source_id, action),
            ).fetchone()
            if existing_observation is not None:
                existing = db.execute(
                    "SELECT * FROM memory_candidates WHERE candidate_id=?",
                    (existing_observation["candidate_id"],),
                ).fetchone()
                if existing is None:
                    raise OperationError("invalid_memory_observation")
                if target_fact_id is not None:
                    target = db.execute(
                        "SELECT * FROM memory_facts WHERE fact_id=?", (target_fact_id,)
                    ).fetchone()
                same_payload = not (
                    existing["bot_id"] != scope.bot_id
                    or existing["group_id"] != scope.group_id
                    or existing["subject_id"] != subject_id
                    or existing["value"] != value
                    or existing["predicate"] != predicate
                    or existing["target_fact_id"] != (target_fact_id or "")
                    or existing["suggestion_reason"] != stored_suggestion_reason
                    or _validity_window(existing["valid_from"], existing["valid_to"])
                    != (valid_from, valid_to)
                )
                if not same_payload:
                    audit_payload_mismatch(
                        existing, reason_code="candidate_payload_mismatch"
                    )
                    return None
                if expected_target_revision is not None:
                    expected_revision_event = db.execute(
                        "SELECT details FROM audit WHERE kind='memory_candidate' "
                        "AND identity=? AND code='correction_target_revision' "
                        "ORDER BY id DESC LIMIT 1",
                        (existing["candidate_id"],),
                    ).fetchone()
                    if expected_revision_event is None:
                        audit_payload_mismatch(
                            existing,
                            reason_code="candidate_target_revision_missing",
                        )
                        return None
                    recorded_fact_id, recorded_expected_revision, _ = (
                        _correction_target_revision(expected_revision_event["details"])
                    )
                    if (
                        recorded_fact_id != target_fact_id
                        or recorded_expected_revision != expected_target_revision
                    ):
                        audit_payload_mismatch(
                            existing,
                            reason_code="candidate_target_revision_mismatch",
                            stored_expected_revision=recorded_expected_revision,
                        )
                        return None
                should_be_suppressed = (
                    current_self_correction
                    and existing["status"] != "applied"
                    and target is not None
                    and observed_at <= now
                    and (valid_from is None or valid_from <= now)
                    and (valid_to is None or now < valid_to)
                    and target["suppression_candidate_id"] != existing["candidate_id"]
                    # A future-dated correction was intentionally left visible
                    # when first proposed. Retrying it after the start date must
                    # not turn a replay into a new suppression.
                    and not (
                        existing["valid_from"] is not None
                        and _now(existing["created_at"])
                        < _now(existing["valid_from"])
                    )
                )
                if should_be_suppressed:
                    raise OperationError("payload_conflict")
                return _candidate_result(existing)

            if target_fact_id is not None:
                target = self._target_fact(db, target_fact_id, scope, subject_id, predicate)
                if (
                    expected_target_revision is not None
                    and target["fact_revision"] != expected_target_revision
                ):
                    raise OperationError("revision_conflict")
                if expected_target_revision is not None and not _fact_is_current(target, now):
                    raise OperationError("fact_not_current")
                if expected_target_revision is not None:
                    target_observed_at = _optional_time(target["observed_at"])
                    if target_observed_at is not None and observed_at < target_observed_at:
                        raise OperationError("correction_stale")
                if action == "reinforce" and target["value"] != value:
                    raise OperationError("target_mismatch")
                if action == "supersede" and target["value"] == value:
                    raise OperationError("duplicate_active_fact")

            if target is not None and target["suppressed_at"] is not None:
                raise OperationError("target_suppressed")

            conflict = False
            if action == "supersede" and not reliable_update:
                conflict = True
            if action == "add":
                active = db.execute(
                    "SELECT 1 FROM memory_facts WHERE bot_id=? AND group_id=? "
                    "AND subject_id=? AND predicate=? AND status='active' LIMIT 1",
                    (scope.bot_id, scope.group_id, subject_id, predicate),
                ).fetchone()
                conflict = active is not None
            conflict_set_id = (
                _stable_id("memconf", scope.bot_id, scope.group_id, subject_id, predicate)
                if conflict
                else ""
            )
            status = "skipped" if action == "skip" else "conflict_pending" if conflict else "pending"
            candidate_id = _stable_id(
                "memcand", target_id, source_id, action, predicate, value
            )
            db.execute(
                "INSERT INTO memory_candidates("
                "candidate_id,bot_id,group_id,subject_id,predicate,value,action,target_fact_id,"
                "source_ids,evidence_refs,candidate_revision,status,conflict_set_id,skip_reason,"
                "suggestion_reason,"
                "applied_fact_id,applied_event_id,actor,created_at,updated_at,"
                "observed_at,valid_from,valid_to) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    scope.bot_id,
                    scope.group_id,
                    subject_id,
                    predicate,
                    value,
                    action,
                    target_fact_id or "",
                    _json_refs(source_ids),
                    _json_refs(source_ids),
                    1,
                    status,
                    conflict_set_id,
                    reason or "",
                    stored_suggestion_reason,
                    "",
                    "",
                    actor,
                    now,
                    now,
                    observed_at,
                    valid_from,
                    valid_to,
                ),
            )
            observation_id = _stable_id("memobs", target_id, source_id, action)
            db.execute(
                "INSERT INTO memory_observations("
                "observation_id,target_id,source_id,action,candidate_id,fact_id,observed_at,actor) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    observation_id,
                    target_id,
                    source_id,
                    action,
                    candidate_id,
                    "",
                    observed_at,
                    actor,
                ),
            )
            if (
                current_self_correction
                and observed_at <= now
                and (valid_from is None or valid_from <= now)
                and (valid_to is None or now < valid_to)
            ):
                if target_fact_id is None or target is None:
                    raise OperationError("target_required")
                if not _fact_is_current(target, now):
                    raise OperationError("target_not_current")
                target_observed_at = _optional_time(target["observed_at"])
                if target_observed_at is not None and observed_at < target_observed_at:
                    raise OperationError("correction_stale")
                next_fact_revision = int(target["fact_revision"]) + 1
                db.execute(
                    "UPDATE memory_facts SET suppressed_at=?,suppression_candidate_id=?,"
                    "fact_revision=?,updated_at=? WHERE fact_id=? AND fact_revision=? "
                    "AND status='active' AND suppressed_at IS NULL",
                    (
                        now,
                        candidate_id,
                        next_fact_revision,
                        now,
                        target_fact_id,
                        target["fact_revision"],
                    ),
                )
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise OperationError("revision_conflict")
                db.execute(
                    "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
                    (
                        "memory_fact",
                        target_fact_id,
                        next_fact_revision,
                        "suppressed_pending_review",
                        json.dumps(
                            {"candidate_id": candidate_id, "source_id": source_id},
                            separators=(",", ":"),
                        ),
                    ),
                )
            if expected_target_revision is not None:
                if target is None:
                    raise OperationError("target_required")
                committed_target_revision = int(target["fact_revision"])
                if (
                    current_self_correction
                    and observed_at <= now
                    and (valid_from is None or valid_from <= now)
                    and (valid_to is None or now < valid_to)
                ):
                    committed_target_revision += 1
                db.execute(
                    "INSERT INTO audit(kind,identity,revision,code,details) "
                    "VALUES (?,?,?,?,?)",
                    (
                        "memory_candidate",
                        candidate_id,
                        1,
                        "correction_target_revision",
                        json.dumps(
                            {
                                "fact_id": target_fact_id,
                                "expected_revision": expected_target_revision,
                                "fact_revision": committed_target_revision,
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
            row = db.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise OperationError("invalid_memory_candidate")
            return _candidate_result(row)

        candidate = await self.store.transaction(commit)
        if candidate is None:
            raise OperationError("payload_conflict")
        return candidate

    async def propose_fact_correction(
        self,
        *,
        actor: str,
        scope: Scope,
        fact_id: str,
        expected_fact_revision: int,
        source_id: str,
        subject_id: str,
        predicate: str,
        value: str,
        reliable_update: bool = False,
        current_self_correction_evidence: bool = False,
        valid_from: float | None = None,
        valid_to: float | None = None,
    ) -> MemoryCandidate:
        """Create a source-backed correction candidate for one exact active fact.

        The old fact remains readable until the correction is reviewed and
        applied. Only an explicit ``current_self_correction_evidence`` signal
        uses the existing same-transaction suppression path; the fact and
        target revision are checked in the proposal transaction.
        """
        fact_id = _identity(fact_id, "invalid_memory_fact")
        if type(expected_fact_revision) is not int or expected_fact_revision < 1:
            raise OperationError("invalid_memory_revision")
        if type(current_self_correction_evidence) is not bool:
            raise OperationError("invalid_memory_correction_signal")
        return await self.propose(
            actor=actor,
            scope=scope,
            source_id=source_id,
            subject_id=subject_id,
            predicate=predicate,
            value=value,
            action="supersede",
            target_fact_id=fact_id,
            expected_target_revision=expected_fact_revision,
            reliable_update=reliable_update,
            current_self_correction=current_self_correction_evidence,
            valid_from=valid_from,
            valid_to=valid_to,
        )

    async def resolve_supersede_conflict(
        self,
        candidate_id: str,
        *,
        actor: str,
        expected_revision: int,
        expected_target_revision: int,
        reason: str,
    ) -> MemoryCandidate:
        """Move one verified supersede conflict into the normal review queue.

        This records an explicit administrator decision and the exact target
        fact revision that was checked. It does not approve or apply the
        candidate, and it does not suppress or otherwise mutate the old fact.
        """
        candidate_id = _identity(candidate_id, "invalid_memory_candidate")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        if type(expected_revision) is not int or expected_revision < 1:
            raise OperationError("invalid_memory_revision")
        if type(expected_target_revision) is not int or expected_target_revision < 1:
            raise OperationError("invalid_memory_revision")
        reason = _code(reason, "invalid_memory_conflict_reason")
        now = self._now()

        def commit(db: StoreConnection) -> MemoryCandidate:
            row = db.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise OperationError("candidate_not_found")
            scope = Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"]))
            source_ids = _decode_refs(row["source_ids"])
            subject_id = str(row["subject_id"])
            self._authorize_source_and_actor(
                db,
                actor=actor,
                scope=scope,
                source_ids=source_ids,
                subject_id=subject_id,
            )
            self._authorize_actor_capability(db, actor, scope, "memory.review")
            if row["candidate_revision"] != expected_revision:
                raise OperationError("revision_conflict")
            if row["status"] != "conflict_pending":
                raise OperationError("candidate_not_conflict_pending")
            if row["action"] != "supersede" or not row["target_fact_id"]:
                raise OperationError("candidate_not_resolvable")

            valid_from, valid_to = _validity_window(row["valid_from"], row["valid_to"])
            if valid_from is not None and valid_from > now:
                raise OperationError("future_effect_not_due")
            if valid_to is not None and valid_to <= now:
                raise OperationError("candidate_expired")
            observed_at = self._source_observed_at(db, source_ids[0])
            if observed_at > now:
                raise OperationError("future_effect_not_due")

            target_fact_id = _identity(row["target_fact_id"], "invalid_memory_fact")
            target = self._target_fact(
                db,
                target_fact_id,
                scope,
                subject_id,
                str(row["predicate"]),
            )
            if target["fact_revision"] != expected_target_revision:
                raise OperationError("revision_conflict")
            if not _fact_is_current(target, now):
                raise OperationError("fact_not_current")
            if target["suppression_candidate_id"]:
                raise OperationError("target_suppressed")
            if target["value"] == row["value"]:
                raise OperationError("duplicate_active_fact")
            target_observed_at = _optional_time(target["observed_at"])
            if target_observed_at is not None and observed_at < target_observed_at:
                raise OperationError("correction_stale")
            active_count = int(
                db.execute(
                    "SELECT count(*) FROM memory_facts WHERE bot_id=? AND group_id=? "
                    "AND subject_id=? AND predicate=? AND status='active'",
                    (scope.bot_id, scope.group_id, subject_id, row["predicate"]),
                ).fetchone()[0]
            )
            if active_count != 1:
                raise OperationError("conflict_pending")

            proposal_event = db.execute(
                "SELECT details FROM audit WHERE kind='memory_candidate' "
                "AND identity=? AND code='correction_target_revision' "
                "ORDER BY id DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
            original_expected_revision = expected_target_revision
            if proposal_event is not None:
                recorded_fact_id, original_expected_revision, _ = _correction_target_revision(
                    proposal_event["details"]
                )
                if recorded_fact_id != target_fact_id:
                    raise OperationError("invalid_memory_candidate")

            next_revision = expected_revision + 1
            db.execute(
                "UPDATE memory_candidates SET candidate_revision=?,status='pending',"
                "conflict_set_id='',updated_at=? WHERE candidate_id=? "
                "AND candidate_revision=? AND status='conflict_pending' AND action='supersede'",
                (next_revision, now, candidate_id, expected_revision),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise OperationError("revision_conflict")
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
                (
                    "memory_candidate",
                    candidate_id,
                    next_revision,
                    "correction_conflict_resolved",
                    json.dumps(
                        {
                            "actor": actor,
                            "reason": reason,
                            "fact_id": target_fact_id,
                            "expected_revision": original_expected_revision,
                            "fact_revision": expected_target_revision,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
            updated = db.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if updated is None:
                raise OperationError("invalid_memory_candidate")
            return _candidate_result(updated)

        return await self.store.transaction(commit)

    async def review(
        self,
        candidate_id: str,
        *,
        actor: str,
        expected_revision: int,
        decision: CandidateDecision,
    ) -> MemoryCandidate:
        return await self.store.transaction(
            lambda db: self.review_transaction(
                db, candidate_id, actor=actor, expected_revision=expected_revision, decision=decision
            )
        )

    def review_transaction(
        self,
        db: StoreConnection,
        candidate_id: str,
        *,
        actor: str,
        expected_revision: int,
        decision: CandidateDecision,
    ) -> MemoryCandidate:
        """Owner transition inside the caller's single Store transaction."""
        candidate_id = _identity(candidate_id, "invalid_memory_candidate")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        if type(expected_revision) is not int or expected_revision < 1:
            raise OperationError("invalid_memory_revision")
        if decision not in _REVIEW_DECISIONS:
            raise OperationError("invalid_memory_decision")
        now = self._now()

        row = db.execute("SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if row is None:
            raise OperationError("candidate_not_found")
        scope = Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"]))
        source_ids = _decode_refs(row["source_ids"])
        self._authorize_source_and_actor(
            db,
            actor=actor,
            scope=scope,
            source_ids=source_ids,
            subject_id=str(row["subject_id"]),
        )
        self._authorize_actor_capability(db, actor, scope, "memory.review")
        if row["candidate_revision"] != expected_revision:
            raise OperationError("revision_conflict")
        if row["status"] not in {"pending", "conflict_pending"}:
            raise OperationError("candidate_not_reviewable")
        if row["status"] == "conflict_pending" and decision == "approved":
            raise OperationError("conflict_pending")
        next_revision = expected_revision + 1
        db.execute(
            "UPDATE memory_candidates SET candidate_revision=?,status=?,updated_at=? "
            "WHERE candidate_id=? AND candidate_revision=?",
            (next_revision, decision, now, candidate_id, expected_revision),
        )
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise OperationError("revision_conflict")
        db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
            (
                "memory_candidate",
                candidate_id,
                next_revision,
                decision,
                json.dumps({"actor": actor}, separators=(",", ":")),
            ),
        )
        updated = db.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if updated is None:
            raise OperationError("invalid_memory_candidate")
        return _candidate_result(updated)

    async def restore_suppressed_fact(
        self,
        fact_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
        reason: str,
    ) -> MemoryFact:
        """Explicitly restore a rejected correction's old fact after review."""
        fact_id = _identity(fact_id, "invalid_memory_fact")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if type(expected_revision) is not int or expected_revision < 1:
            raise OperationError("invalid_memory_revision")
        reason = _code(reason, "invalid_memory_restore_reason")
        now = self._now()

        def commit(db: StoreConnection) -> MemoryFact:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            self._authorize_actor_capability(db, actor, scope, "memory.review")
            self._authorize_actor_capability(db, actor, scope, "memory.apply")
            row = db.execute("SELECT * FROM memory_facts WHERE fact_id=?", (fact_id,)).fetchone()
            if row is None:
                raise OperationError("fact_not_found")
            if row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
                raise OperationError("denied")
            if row["fact_revision"] != expected_revision:
                raise OperationError("revision_conflict")
            candidate_id = str(row["suppression_candidate_id"])
            if row["status"] != "active" or row["suppressed_at"] is None or not candidate_id:
                raise OperationError("fact_not_suppressed")
            candidate = db.execute(
                "SELECT status FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if candidate is None or candidate["status"] not in {"rejected", "withdrawn"}:
                raise OperationError("correction_still_pending")
            subject_id = _identity(row["subject_id"], "invalid_memory_subject")
            source_ids = _decode_refs(row["source_ids"])
            self.assert_retrieval_source_transaction(db, subject_id, source_ids, scope)
            valid_from, valid_to = _validity_window(row["valid_from"], row["valid_to"])
            if (valid_from is not None and valid_from > now) or (
                valid_to is not None and valid_to <= now
            ):
                raise OperationError("fact_not_current")
            next_revision = expected_revision + 1
            db.execute(
                "UPDATE memory_facts SET suppressed_at=NULL,suppression_candidate_id='',"
                "fact_revision=?,updated_at=? WHERE fact_id=? AND fact_revision=? "
                "AND status='active' AND suppressed_at IS NOT NULL",
                (next_revision, now, fact_id, expected_revision),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise OperationError("revision_conflict")
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
                (
                    "memory_fact", fact_id, next_revision, "suppression_restored",
                    json.dumps(
                        {"actor": actor, "candidate_id": candidate_id, "reason": reason},
                        separators=(",", ":"),
                    ),
                ),
            )
            restored = db.execute("SELECT * FROM memory_facts WHERE fact_id=?", (fact_id,)).fetchone()
            if restored is None:
                raise OperationError("invalid_memory_fact")
            return _fact_result(restored)

        return await self.store.transaction(commit)

    async def disable_fact(
        self,
        fact_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
        reason: str,
    ) -> MemoryFact:
        return await self.store.transaction(
            lambda db: self.disable_fact_transaction(
                db, fact_id, actor=actor, scope=scope, expected_revision=expected_revision, reason=reason
            )
        )

    def disable_fact_transaction(
        self,
        db: StoreConnection,
        fact_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
        reason: str,
    ) -> MemoryFact:
        """Owner transition inside the caller's single Store transaction."""
        """Disable one applied fact and withdraw candidates based on it.

        This keeps the fact and its evidence for audit while making it
        immediately ineligible for both hot and cold reads. The selected fact
        is returned only while its source is still readable in this scope.
        """
        fact_id = _identity(fact_id, "invalid_memory_fact")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if type(expected_revision) is not int or expected_revision < 1:
            raise OperationError("invalid_memory_revision")
        reason = _code(reason, "invalid_memory_disable_reason")
        now = self._now()

        self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
        self._authorize_actor_capability(db, actor, scope, "memory.review")
        self._authorize_actor_capability(db, actor, scope, "memory.apply")
        row = db.execute("SELECT * FROM memory_facts WHERE fact_id=?", (fact_id,)).fetchone()
        if row is None:
            raise OperationError("fact_not_found")
        if row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
            raise OperationError("denied")
        if row["fact_revision"] != expected_revision:
            raise OperationError("revision_conflict")
        if row["status"] != "active":
            raise OperationError("fact_not_active")
        subject_id = _identity(row["subject_id"], "invalid_memory_subject")
        source_ids = _decode_refs(row["source_ids"])
        visible_sources = self._readable_source_refs(db, source_ids, scope, subject_id)
        visible_evidence = tuple(ref for ref in _decode_refs(row["evidence_refs"]) if ref in visible_sources)
        if not visible_evidence:
            raise OperationError("source_revoked")

        next_revision = expected_revision + 1
        db.execute(
            "UPDATE memory_facts SET status='disabled',fact_revision=?,updated_at=? "
            "WHERE fact_id=? AND fact_revision=? AND status='active'",
            (next_revision, now, fact_id, expected_revision),
        )
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise OperationError("revision_conflict")
        db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
            (
                "memory_fact",
                fact_id,
                next_revision,
                "fact_disabled",
                json.dumps({"actor": actor, "reason": reason}, separators=(",", ":")),
            ),
        )

        pending = db.execute(
            "SELECT candidate_id,candidate_revision FROM memory_candidates "
            "WHERE bot_id=? AND group_id=? AND target_fact_id=? "
            "AND status IN ('pending','conflict_pending','approved') "
            "ORDER BY candidate_id",
            (scope.bot_id, scope.group_id, fact_id),
        ).fetchall()
        for candidate in pending:
            candidate_id = _identity(candidate["candidate_id"], "invalid_memory_candidate")
            candidate_revision = candidate["candidate_revision"]
            if type(candidate_revision) is not int or candidate_revision < 1:
                raise OperationError("invalid_memory_candidate")
            next_candidate_revision = candidate_revision + 1
            db.execute(
                "UPDATE memory_candidates SET candidate_revision=?,status='withdrawn',"
                "updated_at=? WHERE candidate_id=? AND candidate_revision=? "
                "AND status IN ('pending','conflict_pending','approved')",
                (
                    next_candidate_revision,
                    now,
                    candidate_id,
                    candidate_revision,
                ),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise OperationError("revision_conflict")
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
                (
                    "memory_candidate",
                    candidate_id,
                    next_candidate_revision,
                    "target_fact_disabled",
                    json.dumps(
                        {"actor": actor, "fact_id": fact_id},
                        separators=(",", ":"),
                    ),
                ),
            )

        disabled = db.execute("SELECT * FROM memory_facts WHERE fact_id=?", (fact_id,)).fetchone()
        if disabled is None:
            raise OperationError("invalid_memory_fact")
        return _fact_result(
            disabled,
            source_ids=visible_sources,
            evidence_refs=visible_evidence,
        )

    @staticmethod
    def _cancelled(probe: Callable[[], bool] | None) -> bool:
        if probe is None:
            return False
        try:
            return bool(probe())
        except Exception as exc:
            raise OperationError("cancelled_before_commit") from exc

    @staticmethod
    def _valid_source_refs(
        db: StoreConnection,
        source_ids: tuple[str, ...],
        scope: Scope,
        subject_id: str,
    ) -> tuple[str, ...]:
        """Return active, same-scope human sources, with tombstones winning."""
        active: list[str] = []
        for source_id in source_ids:
            row = db.execute(
                "SELECT * FROM archive_sources WHERE source_id=?", (source_id,)
            ).fetchone()
            tombstone = db.execute(
                "SELECT * FROM archive_source_tombstones WHERE source_id=?", (source_id,)
            ).fetchone()
            if row is None or row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
                continue
            if tombstone is not None or row["status"] != "active":
                continue
            if (
                row["source_kind"] == "human_message"
                and row["speaker_kind"] == "human"
                and row["speaker_id"] == subject_id
            ):
                active.append(source_id)
        return tuple(active)

    def _reconcile_revocation(
        self, db: StoreConnection, row: StoreRow, *, now: float
    ) -> tuple[int, int]:
        """Project one tombstone into candidate/fact state in the open transaction."""
        revocation_id = _identity(row["revocation_id"], "invalid_memory_revocation")
        source_id = _identity(row["source_id"], "invalid_memory_source")
        scope = Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"]))
        source_revision = row["source_revision"]
        if type(source_revision) is not int or source_revision < 1:
            raise OperationError("invalid_memory_revocation")
        tombstone = db.execute(
            "SELECT bot_id,group_id,source_revision FROM archive_source_tombstones "
            "WHERE source_id=?",
            (source_id,),
        ).fetchone()
        if (
            tombstone is None
            or tombstone["bot_id"] != scope.bot_id
            or tombstone["group_id"] != scope.group_id
            or type(tombstone["source_revision"]) is not int
            or tombstone["source_revision"] < source_revision
        ):
            raise OperationError("memory_revocation_source_mismatch")
        candidate_count = 0
        candidates = db.execute(
            "SELECT DISTINCT c.* FROM memory_observations AS o "
            "JOIN memory_candidates AS c ON c.candidate_id=o.candidate_id "
            "WHERE o.source_id=? AND c.bot_id=? AND c.group_id=? "
            "AND c.status IN ('pending','conflict_pending','approved') "
            "ORDER BY c.candidate_id",
            (source_id, scope.bot_id, scope.group_id),
        ).fetchall()
        for candidate in candidates:
            candidate_id = _identity(candidate["candidate_id"], "invalid_memory_candidate")
            expected_revision = candidate["candidate_revision"]
            if type(expected_revision) is not int or expected_revision < 1:
                raise OperationError("invalid_memory_candidate")
            next_revision = expected_revision + 1
            db.execute(
                "UPDATE memory_candidates SET candidate_revision=?,status='withdrawn',"
                "updated_at=? WHERE candidate_id=? AND candidate_revision=? "
                "AND status IN ('pending','conflict_pending','approved')",
                (next_revision, now, candidate_id, expected_revision),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise OperationError("memory_revocation_conflict")
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
                (
                    "memory_candidate",
                    candidate_id,
                    next_revision,
                    "source_revoked",
                    json.dumps(
                        {"revocation_id": revocation_id, "source_id": source_id},
                        separators=(",", ":"),
                    ),
                ),
            )
            candidate_count += 1

        fact_count = 0
        facts = db.execute(
            "SELECT DISTINCT f.* FROM memory_observations AS o "
            "JOIN memory_facts AS f ON f.fact_id=o.fact_id "
            "WHERE o.source_id=? AND o.fact_id<>'' AND f.bot_id=? AND f.group_id=? "
            "AND f.status='active' ORDER BY f.fact_id",
            (source_id, scope.bot_id, scope.group_id),
        ).fetchall()
        for fact in facts:
            fact_id = _identity(fact["fact_id"], "invalid_memory_fact")
            valid_refs = self._valid_source_refs(
                db,
                _decode_refs(fact["source_ids"]),
                scope,
                _identity(fact["subject_id"], "invalid_memory_subject"),
            )
            if valid_refs:
                continue
            expected_revision = fact["fact_revision"]
            if type(expected_revision) is not int or expected_revision < 1:
                raise OperationError("invalid_memory_fact")
            next_revision = expected_revision + 1
            db.execute(
                "UPDATE memory_facts SET status='disabled',valid_to=?,fact_revision=?,"
                "updated_at=? WHERE fact_id=? AND fact_revision=? AND status='active'",
                (now, next_revision, now, fact_id, expected_revision),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise OperationError("memory_revocation_conflict")
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
                (
                    "memory_fact",
                    fact_id,
                    next_revision,
                    "source_revoked",
                    json.dumps(
                        {"revocation_id": revocation_id, "source_id": source_id},
                        separators=(",", ":"),
                    ),
                ),
            )
            fact_count += 1
        matters = db.execute(
            "SELECT * FROM memory_matters WHERE source_id=? AND bot_id=? AND group_id=? "
            "AND state IN ('candidate','approved','active') ORDER BY matter_id",
            (source_id, scope.bot_id, scope.group_id),
        ).fetchall()
        for matter_row in matters:
            matter = _matter_result(matter_row)
            updated = replace(
                matter,
                state="cancelled",
                reason="source_revoked",
                revision=matter.revision + 1,
                updated_at=now,
            )
            db.execute(
                "UPDATE memory_matters SET state='cancelled',reason='source_revoked',revision=?,updated_at=? "
                "WHERE matter_id=? AND revision=?",
                (updated.revision, now, matter.matter_id, matter.revision),
            )
            self._audit_matter(db, updated, actor="memory-revocation", code="source_revoked")
        return candidate_count, fact_count

    async def reconcile_pending_revocations(
        self,
        *,
        actor: str,
        scope: Scope,
        limit: int = _MAX_REVOCATION_BATCH,
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[MemoryRevocationReceipt, ...]:
        """Replay bounded source-revocation work with one atomic Store transaction.

        This is a controlled internal propagation operation.  It deliberately
        does not require the revoked author's read/learn grant or an operator's
        business ``memory.apply`` grant: those grants are the state being
        invalidated.  The method exposes only identity/count receipts and
        performs no memory disclosure.
        """
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if type(limit) is not int or not 1 <= limit <= _MAX_REVOCATION_BATCH:
            raise OperationError("invalid_memory_revocation_limit")
        now = self._now()

        def commit(db: StoreConnection) -> tuple[MemoryRevocationReceipt, ...]:
            if self._cancelled(cancelled):
                raise OperationError("cancelled_before_commit")
            rows = db.execute(
                "SELECT * FROM memory_revocations WHERE bot_id=? AND group_id=? "
                "AND status='pending' ORDER BY created_at,revocation_id LIMIT ?",
                (scope.bot_id, scope.group_id, limit),
            ).fetchall()
            receipts: list[MemoryRevocationReceipt] = []
            for row in rows:
                candidate_count, fact_count = self._reconcile_revocation(
                    db, row, now=now
                )
                if self._cancelled(cancelled):
                    raise OperationError("cancelled_before_commit")
                revocation_id = _identity(row["revocation_id"], "invalid_memory_revocation")
                receipt_id = _stable_id("memrevreceipt", revocation_id)
                db.execute(
                    "UPDATE memory_revocations SET status='applied',receipt_id=?,"
                    "candidate_count=?,fact_count=?,updated_at=?,applied_at=? "
                    "WHERE revocation_id=? AND status='pending'",
                    (receipt_id, candidate_count, fact_count, now, now, revocation_id),
                )
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise OperationError("memory_revocation_conflict")
                applied = db.execute(
                    "SELECT * FROM memory_revocations WHERE revocation_id=?",
                    (revocation_id,),
                ).fetchone()
                if applied is None:
                    raise OperationError("invalid_memory_revocation")
                receipts.append(_revocation_result(applied))
            return tuple(receipts)

        return await self.store.transaction(commit)

    async def apply(
        self,
        candidate_id: str,
        *,
        actor: str,
        expected_revision: int,
        cancelled: Callable[[], bool] | None = None,
    ) -> MemoryCandidate:
        return await self.store.transaction(
            lambda db: self.apply_transaction(
                db, candidate_id, actor=actor, expected_revision=expected_revision, cancelled=cancelled
            )
        )

    def apply_transaction(
        self,
        db: StoreConnection,
        candidate_id: str,
        *,
        actor: str,
        expected_revision: int,
        cancelled: Callable[[], bool] | None = None,
    ) -> MemoryCandidate:
        """Owner transition inside the caller's single Store transaction."""
        candidate_id = _identity(candidate_id, "invalid_memory_candidate")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        if type(expected_revision) is not int or expected_revision < 1:
            raise OperationError("invalid_memory_revision")
        now = self._now()

        if self._cancelled(cancelled):
            raise OperationError("cancelled_before_commit")
        row = db.execute("SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if row is None:
            raise OperationError("candidate_not_found")
        if row["candidate_revision"] != expected_revision:
            raise OperationError("revision_conflict")
        if row["status"] != "approved":
            raise OperationError("candidate_not_approved")
        scope = Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"]))
        subject_id = str(row["subject_id"])
        source_ids = _decode_refs(row["source_ids"])
        self._authorize_source_and_actor(
            db,
            actor=actor,
            scope=scope,
            source_ids=source_ids,
            subject_id=subject_id,
        )
        self._authorize_actor_capability(db, actor, scope, "memory.apply")
        return self._apply_approved_candidate_transaction(
            db, row, actor=actor, expected_revision=expected_revision, now=now, cancelled=cancelled
        )

    def _apply_approved_candidate_transaction(
        self,
        db: StoreConnection,
        row: StoreRow,
        *,
        actor: str,
        expected_revision: int,
        now: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> MemoryCandidate:
        """Private transition: caller establishes approval and its exact authorization policy."""
        candidate_id = str(row["candidate_id"])
        scope = Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"]))
        subject_id = str(row["subject_id"])
        source_ids = _decode_refs(row["source_ids"])
        valid_from, valid_to = _validity_window(row["valid_from"], row["valid_to"])
        if valid_from is not None and valid_from > now:
            raise OperationError("future_effect_not_due")
        if valid_to is not None and valid_to <= now:
            raise OperationError("candidate_expired")
        observed_at = self._source_observed_at(db, source_ids[0])
        action = cast(MemoryAction, row["action"])
        predicate = str(row["predicate"])
        value = str(row["value"])
        target_fact_id = _optional(row["target_fact_id"])
        if target_fact_id is not None:
            target_suppression = db.execute(
                "SELECT suppression_candidate_id FROM memory_facts WHERE fact_id=?",
                (target_fact_id,),
            ).fetchone()
            if (
                target_suppression is not None
                and target_suppression["suppression_candidate_id"]
                and target_suppression["suppression_candidate_id"] != candidate_id
            ):
                raise OperationError("target_suppressed")
        fact_id = ""
        if action == "add":
            active_predicate = db.execute(
                "SELECT 1 FROM memory_facts WHERE bot_id=? AND group_id=? AND subject_id=? "
                "AND predicate=? AND status='active' LIMIT 1",
                (scope.bot_id, scope.group_id, subject_id, predicate),
            ).fetchone()
            active = db.execute(
                "SELECT * FROM memory_facts WHERE bot_id=? AND group_id=? AND subject_id=? "
                "AND predicate=? AND value=? AND status='active' LIMIT 1",
                (scope.bot_id, scope.group_id, subject_id, predicate, value),
            ).fetchone()
            if active is None and active_predicate is not None:
                raise OperationError("conflict_pending")
            if active is None:
                fact_id = _stable_id("memfact", candidate_id)
                self._insert_fact(
                    db,
                    fact_id=fact_id,
                    scope=scope,
                    subject_id=subject_id,
                    predicate=predicate,
                    value=value,
                    source_ids=source_ids,
                    observed_at=observed_at,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    now=now,
                )
            else:
                fact_id = str(active["fact_id"])
                merged = self._append_ref(_decode_refs(active["source_ids"]), source_ids)
                self._update_fact_sources(
                    db,
                    fact_id=fact_id,
                    fact_revision=int(active["fact_revision"]) + 1,
                    source_ids=merged,
                    now=now,
                )
        elif action == "reinforce":
            if target_fact_id is None:
                raise OperationError("target_required")
            target = self._target_fact(db, target_fact_id, scope, subject_id, predicate)
            if target["value"] != value:
                raise OperationError("target_mismatch")
            fact_id = target_fact_id
            target_refs = self._active_source_refs(db, _decode_refs(target["source_ids"]), scope, subject_id)
            merged = self._append_ref(target_refs, source_ids)
            self._update_fact_sources(
                db,
                fact_id=fact_id,
                fact_revision=int(target["fact_revision"]) + 1,
                source_ids=merged,
                now=now,
            )
        elif action == "supersede":
            if target_fact_id is None:
                raise OperationError("target_required")
            target = self._target_fact(db, target_fact_id, scope, subject_id, predicate)
            target_revision_event = db.execute(
                "SELECT details FROM audit WHERE kind='memory_candidate' "
                "AND identity=? AND code IN "
                "('correction_target_revision','correction_conflict_resolved') "
                "ORDER BY id DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if target_revision_event is not None:
                recorded_fact_id, _, recorded_revision = _correction_target_revision(
                    target_revision_event["details"]
                )
                if recorded_fact_id != target_fact_id:
                    raise OperationError("invalid_memory_candidate")
                if target["fact_revision"] != recorded_revision:
                    raise OperationError("revision_conflict")
            if target["value"] == value:
                raise OperationError("duplicate_active_fact")
            active_count = int(
                db.execute(
                    "SELECT count(*) FROM memory_facts WHERE bot_id=? AND group_id=? "
                    "AND subject_id=? AND predicate=? AND status='active'",
                    (scope.bot_id, scope.group_id, subject_id, predicate),
                ).fetchone()[0]
            )
            if active_count != 1:
                raise OperationError("conflict_pending")
            db.execute(
                "UPDATE memory_facts SET status='superseded',valid_to=?,fact_revision=?,"
                "updated_at=? WHERE fact_id=? AND status='active'",
                (now, int(target["fact_revision"]) + 1, now, target_fact_id),
            )
            fact_id = _stable_id("memfact", candidate_id)
            self._insert_fact(
                db,
                fact_id=fact_id,
                scope=scope,
                subject_id=subject_id,
                predicate=predicate,
                value=value,
                source_ids=source_ids,
                observed_at=observed_at,
                valid_from=valid_from,
                valid_to=valid_to,
                now=now,
                supersedes_fact_id=target_fact_id,
            )
        else:
            raise OperationError("candidate_not_applicable")

        observation = db.execute(
            "SELECT observation_id FROM memory_observations WHERE candidate_id=? "
            "AND source_id=? AND action=?",
            (candidate_id, source_ids[0], action),
        ).fetchone()
        if observation is None:
            raise OperationError("invalid_memory_observation")
        db.execute(
            "UPDATE memory_observations SET fact_id=? WHERE observation_id=?",
            (fact_id, observation["observation_id"]),
        )
        event_id = _stable_id("memevent", candidate_id, str(expected_revision), action)
        db.execute(
            "INSERT INTO memory_events("
            "event_id,candidate_id,candidate_revision,action,fact_id,status,actor,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                event_id,
                candidate_id,
                expected_revision + 1,
                action,
                fact_id,
                "applied",
                actor,
                now,
            ),
        )
        next_revision = expected_revision + 1
        db.execute(
            "UPDATE memory_candidates SET candidate_revision=?,status='applied',"
            "applied_fact_id=?,applied_event_id=?,updated_at=? "
            "WHERE candidate_id=? AND candidate_revision=? AND status='approved'",
            (next_revision, fact_id, event_id, now, candidate_id, expected_revision),
        )
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise OperationError("revision_conflict")
        if self._cancelled(cancelled):
            raise OperationError("cancelled_before_commit")
        updated = db.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if updated is None:
            raise OperationError("invalid_memory_candidate")
        return _candidate_result(updated)

    async def read_candidate(
        self, candidate_id: str, *, actor: str, scope: Scope
    ) -> MemoryCandidate | None:
        candidate_id = _identity(candidate_id, "invalid_memory_candidate")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)

        def read(db: StoreConnection) -> MemoryCandidate | None:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            row = db.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                return None
            if row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
                raise OperationError("denied")
            candidate = _candidate_result(row)
            visible_refs = self._readable_source_refs(
                db, candidate.source_ids, scope, candidate.subject_id
            )
            visible_evidence = tuple(
                ref for ref in candidate.evidence_refs if ref in visible_refs
            )
            return replace(
                candidate, source_ids=visible_refs, evidence_refs=visible_evidence
            )

        return await self.store.transaction(read)

    async def list_candidates(
        self, *, actor: str, scope: Scope
    ) -> tuple[MemoryCandidate, ...]:
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)

        def read(db: StoreConnection) -> tuple[MemoryCandidate, ...]:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            result: list[MemoryCandidate] = []
            rows = db.execute(
                "SELECT * FROM memory_candidates WHERE bot_id=? AND group_id=? "
                "ORDER BY created_at,candidate_id LIMIT 257",
                (scope.bot_id, scope.group_id),
            ).fetchall()
            if len(rows) > 256:
                raise OperationError("memory_candidate_limit")
            for row in rows:
                candidate = _candidate_result(row)
                try:
                    visible_refs = self._readable_source_refs(
                        db, candidate.source_ids, scope, candidate.subject_id
                    )
                except OperationError:
                    continue
                visible_evidence = tuple(
                    ref for ref in candidate.evidence_refs if ref in visible_refs
                )
                result.append(
                    replace(
                        candidate,
                        source_ids=visible_refs,
                        evidence_refs=visible_evidence,
                    )
                )
            return tuple(result)

        return await self.store.transaction(read)

    async def list_candidates_page(
        self,
        *,
        actor: str,
        scope: Scope,
        limit: int = 32,
        after: str | None = None,
        status: str | None = None,
    ) -> MemoryCandidatePage:
        """Page a scoped review list without loading the entire candidate table."""
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise OperationError("invalid_memory_page_limit")
        if after is not None:
            after = _identity(after, "invalid_memory_cursor")
        if status is not None and status not in _CANDIDATE_STATUSES:
            raise OperationError("invalid_memory_status")

        def read(db: StoreConnection) -> MemoryCandidatePage:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            clauses = ["bot_id=?", "group_id=?"]
            params: list[object] = [scope.bot_id, scope.group_id]
            if status is not None:
                clauses.append("status=?")
                params.append(status)
            if after is not None:
                anchor = db.execute(
                    "SELECT created_at FROM memory_candidates "
                    "WHERE candidate_id=? AND bot_id=? AND group_id=?",
                    (after, scope.bot_id, scope.group_id),
                ).fetchone()
                if anchor is None:
                    raise OperationError("invalid_memory_cursor")
                clauses.append("(created_at<? OR (created_at=? AND candidate_id<?))")
                params.extend((anchor["created_at"], anchor["created_at"], after))
            query = (
                "SELECT * FROM memory_candidates WHERE " + " AND ".join(clauses)
                + " ORDER BY created_at DESC,candidate_id DESC LIMIT ?"
            )
            rows = db.execute(query, (*params, limit + 1)).fetchall()
            page_rows = rows[:limit]
            result: list[MemoryCandidate] = []
            for row in page_rows:
                candidate = _candidate_result(row)
                try:
                    refs = self._readable_source_refs(
                        db, candidate.source_ids, scope, candidate.subject_id
                    )
                except OperationError:
                    continue
                result.append(
                    replace(
                        candidate,
                        source_ids=refs,
                        evidence_refs=tuple(
                            ref for ref in candidate.evidence_refs if ref in refs
                        ),
                    )
                )
            next_cursor = str(page_rows[-1]["candidate_id"]) if len(rows) > limit else None
            return MemoryCandidatePage(tuple(result), next_cursor)

        return await self.store.transaction(read)

    async def review_queue(
        self, *, actor: str, scope: Scope
    ) -> tuple[MemoryCandidate, ...]:
        candidates = await self.list_candidates(actor=actor, scope=scope)
        return tuple(item for item in candidates if item.status == "conflict_pending")

    async def read_fact(
        self, fact_id: str, *, actor: str, scope: Scope
    ) -> MemoryFact | None:
        fact_id = _identity(fact_id, "invalid_memory_fact")
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)

        def read(db: StoreConnection) -> MemoryFact | None:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            row = db.execute(
                "SELECT * FROM memory_facts WHERE fact_id=? AND status='active'", (fact_id,)
            ).fetchone()
            if row is None:
                return None
            if row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id:
                raise OperationError("denied")
            if not _fact_is_current(row, self._now()):
                return None
            fact = _fact_result(row)
            active_refs = self._active_source_refs(
                db, fact.source_ids, scope, fact.subject_id
            )
            if not active_refs:
                return None
            visible_evidence = tuple(ref for ref in fact.evidence_refs if ref in active_refs)
            return replace(
                fact, source_ids=active_refs, evidence_refs=visible_evidence
            )

        return await self.store.transaction(read)

    def _temporal_trace_transaction(
        self,
        db: StoreConnection,
        *,
        scope: Scope,
        subject_id: str,
        head_fact_id: str,
    ) -> MemoryTemporalTrace:
        now = self._now()
        head = db.execute(
            "SELECT * FROM memory_facts WHERE fact_id=?", (head_fact_id,)
        ).fetchone()
        if head is None:
            return MemoryTemporalTrace(head_fact_id, (), "head_missing")
        owner = (scope.bot_id, scope.group_id, subject_id)
        if (head["bot_id"], head["group_id"], head["subject_id"]) != owner:
            return MemoryTemporalTrace(head_fact_id, (), "cross_owner")
        if (
            head["status"] != "active"
            or head["suppressed_at"] is not None
            or not _fact_is_current(head, now)
        ):
            return MemoryTemporalTrace(head_fact_id, (), "head_not_current")

        rows = [head]
        seen = {head_fact_id}
        current = head
        while current["supersedes_fact_id"]:
            parent_id = str(current["supersedes_fact_id"])
            if parent_id in seen:
                return MemoryTemporalTrace(head_fact_id, (), "cycle")
            if len(rows) >= _MAX_TEMPORAL_VERSIONS:
                return MemoryTemporalTrace(head_fact_id, (), "version_limit")
            parent = db.execute(
                "SELECT * FROM memory_facts WHERE fact_id=?", (parent_id,)
            ).fetchone()
            if parent is None:
                return MemoryTemporalTrace(head_fact_id, (), "broken_link")
            if (
                (parent["bot_id"], parent["group_id"], parent["subject_id"])
                != owner
                or parent["predicate"] != head["predicate"]
            ):
                return MemoryTemporalTrace(head_fact_id, (), "cross_owner")
            if parent["status"] != "superseded":
                return MemoryTemporalTrace(head_fact_id, (), "invalid_parent_state")
            children = db.execute(
                "SELECT fact_id FROM memory_facts WHERE supersedes_fact_id=? LIMIT 2",
                (parent_id,),
            ).fetchall()
            if len(children) != 1 or children[0]["fact_id"] != current["fact_id"]:
                return MemoryTemporalTrace(head_fact_id, (), "ambiguous_chain")

            rows.append(parent)
            seen.add(parent_id)
            current = parent

        if len(rows) < 2:
            return MemoryTemporalTrace(head_fact_id, (), "no_history")

        for row in rows:
            source_ids = _decode_refs(row["source_ids"])
            evidence_refs = _decode_refs(row["evidence_refs"])
            visible_sources = self._active_source_refs(
                db, source_ids, scope, subject_id
            )
            if not source_ids or visible_sources != source_ids:
                return MemoryTemporalTrace(head_fact_id, (), "source_unavailable")
            if not evidence_refs or any(ref not in visible_sources for ref in evidence_refs):
                return MemoryTemporalTrace(head_fact_id, (), "evidence_unavailable")

        versions = tuple(_fact_result(row) for row in reversed(rows))
        if any(not fact.evidence_refs or not fact.source_ids for fact in versions):
            return MemoryTemporalTrace(head_fact_id, (), "evidence_unavailable")
        return MemoryTemporalTrace(head_fact_id, versions)

    async def read_temporal_trace(
        self,
        *,
        actor: str,
        scope: Scope,
        subject_id: str,
        head_fact_id: str,
    ) -> MemoryTemporalTrace:
        """Read one complete supersede chain without treating parents as active facts."""
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        subject_id = _identity(subject_id, "invalid_memory_subject", limit=64)
        head_fact_id = _identity(head_fact_id, "invalid_memory_fact")

        def read(db: StoreConnection) -> MemoryTemporalTrace:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            return self._temporal_trace_transaction(
                db,
                scope=scope,
                subject_id=subject_id,
                head_fact_id=head_fact_id,
            )

        return await self.store.transaction(read)

    def assert_temporal_trace_transaction(
        self,
        db: StoreConnection,
        *,
        actor: str,
        scope: Scope,
        trace: MemoryTemporalTrace,
    ) -> None:
        """Fail closed if any version, edge, source, or grant changed before use."""
        self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
        if not trace.versions:
            raise OperationError("stale_retrieval")
        fresh = self._temporal_trace_transaction(
            db,
            scope=scope,
            subject_id=trace.versions[-1].subject_id,
            head_fact_id=trace.head_fact_id,
        )
        if fresh.omitted_reason is not None or fresh.versions != trace.versions:
            raise OperationError("stale_retrieval")

    async def find_exact_current_fact(
        self,
        *,
        actor: str,
        scope: Scope,
        subject_id: str,
        predicate: str,
        value: str,
    ) -> MemoryFact | None:
        """Resolve an explicit old-value correction without a recent-facts window."""
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        subject_id = _identity(subject_id, "invalid_memory_subject", limit=64)
        predicate = _code(predicate, "invalid_memory_predicate")
        value = _memory_value(value)

        def read(db: StoreConnection) -> MemoryFact | None:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            row = db.execute(
                "SELECT * FROM memory_facts WHERE bot_id=? AND group_id=? "
                "AND subject_id=? AND predicate=? AND value=? AND status='active' LIMIT 1",
                (scope.bot_id, scope.group_id, subject_id, predicate, value),
            ).fetchone()
            if row is None or not _fact_is_current(row, self._now()):
                return None
            fact = _fact_result(row)
            refs = self._active_source_refs(db, fact.source_ids, scope, subject_id)
            if not refs:
                return None
            return replace(
                fact,
                source_ids=refs,
                evidence_refs=tuple(ref for ref in fact.evidence_refs if ref in refs),
            )

        return await self.store.transaction(read)

    def _active_source_refs(
        self,
        db: StoreConnection,
        source_ids: tuple[str, ...],
        scope: Scope,
        subject_id: str,
    ) -> tuple[str, ...]:
        for action in ("message.read", "memory.archive", "memory.learn"):
            try:
                self.policy.check_transaction(
                    db, subject_id, scope, action, "", "", False, False
                )
            except OperationError:
                return ()
        return self._valid_source_refs(db, source_ids, scope, subject_id)

    def _readable_source_refs(
        self,
        db: StoreConnection,
        source_ids: tuple[str, ...],
        scope: Scope,
        subject_id: str,
    ) -> tuple[str, ...]:
        refs = self._active_source_refs(db, source_ids, scope, subject_id)
        if not refs:
            raise OperationError("source_revoked")
        return refs

    async def list_facts(
        self, *, actor: str, scope: Scope
    ) -> tuple[MemoryFact, ...]:
        """Return a deterministic, bounded active-fact window for one scope."""
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)

        def read(db: StoreConnection) -> tuple[MemoryFact, ...]:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            now = self._now()
            result: list[MemoryFact] = []
            rows = db.execute(
                "SELECT * FROM memory_facts WHERE bot_id=? AND group_id=? AND status='active' "
                "AND suppressed_at IS NULL "
                "AND (valid_from IS NULL OR valid_from<=?) "
                "AND (valid_to IS NULL OR valid_to>?) "
                "ORDER BY updated_at DESC,fact_id DESC LIMIT ?",
                (scope.bot_id, scope.group_id, now, now, _MAX_VISIBLE_FACTS),
            ).fetchall()
            for row in rows:
                if not _fact_is_current(row, now):
                    continue
                fact = _fact_result(row)
                refs = self._active_source_refs(db, fact.source_ids, scope, fact.subject_id)
                if refs:
                    visible_evidence = tuple(ref for ref in fact.evidence_refs if ref in refs)
                    result.append(
                        replace(fact, source_ids=refs, evidence_refs=visible_evidence)
                    )
            result.sort(key=lambda fact: (fact.created_at, fact.fact_id))
            return tuple(result)

        return await self.store.transaction(read)

    async def list_active_facts_page(
        self,
        *,
        actor: str,
        scope: Scope,
        subject_id: str | None = None,
        limit: int = 32,
        after: str | None = None,
    ) -> MemoryFactPage:
        """Page every current active fact by stable fact-id keyset cursor.

        Unlike ``list_facts``, this admin-facing selector has no recent-fact
        window. Each page still checks the exact group, viewer grant, source
        author grants, and current source tombstones before exposing values.
        ``after`` is the last scanned fact id, so filtered facts cannot make a
        later page repeat or skip the cursor boundary.
        """
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if subject_id is not None:
            subject_id = _identity(subject_id, "invalid_memory_subject", limit=64)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise OperationError("invalid_memory_page_limit")
        if after is not None:
            after = _identity(after, "invalid_memory_cursor")

        def read(db: StoreConnection) -> MemoryFactPage:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            now = self._now()
            clauses = [
                "bot_id=?",
                "group_id=?",
                "status='active'",
                "suppressed_at IS NULL",
                "(valid_from IS NULL OR valid_from<=?)",
                "(valid_to IS NULL OR valid_to>?)",
            ]
            params: list[object] = [scope.bot_id, scope.group_id, now, now]
            if subject_id is not None:
                clauses.append("subject_id=?")
                params.append(subject_id)
            if after is not None:
                anchor_clauses = [
                    "fact_id=?",
                    "bot_id=?",
                    "group_id=?",
                ]
                anchor_params: list[object] = [after, scope.bot_id, scope.group_id]
                if subject_id is not None:
                    anchor_clauses.append("subject_id=?")
                    anchor_params.append(subject_id)
                anchor = db.execute(
                    "SELECT fact_id FROM memory_facts WHERE "
                    + " AND ".join(anchor_clauses),
                    anchor_params,
                ).fetchone()
                if anchor is None:
                    raise OperationError("invalid_memory_cursor")
                clauses.append("fact_id<?")
                params.append(after)

            rows = db.execute(
                "SELECT * FROM memory_facts WHERE " + " AND ".join(clauses)
                + " ORDER BY fact_id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            page_rows = rows[:limit]
            result: list[MemoryFact] = []
            for row in page_rows:
                if not _fact_is_current(row, now):
                    continue
                fact = _fact_result(row)
                try:
                    visible_sources = self._readable_source_refs(
                        db, fact.source_ids, scope, fact.subject_id
                    )
                except OperationError:
                    continue
                visible_evidence = tuple(
                    ref for ref in fact.evidence_refs if ref in visible_sources
                )
                if not visible_evidence:
                    continue
                result.append(
                    replace(
                        fact,
                        source_ids=visible_sources,
                        evidence_refs=visible_evidence,
                    )
                )
            next_cursor = str(page_rows[-1]["fact_id"]) if len(rows) > limit else None
            return MemoryFactPage(tuple(result), next_cursor)

        return await self.store.transaction(read)

    @staticmethod
    def _card_category(category: str) -> str:
        if category not in _CARD_CATEGORIES:
            raise OperationError("invalid_memory_card_category")
        return category

    @staticmethod
    def _card_time_eligible(fact: MemoryFact, category: str, now: float) -> bool:
        # Retrieval owns the shared category/Shanghai timestamp rules. Import
        # lazily because Retrieval already consumes this Memory owner.
        from .retrieval import CardEligibilityPolicy, card_time_eligible

        return card_time_eligible(
            category=category,
            updated_at=datetime.fromtimestamp(fact.updated_at, UTC).isoformat(),
            created_at=datetime.fromtimestamp(fact.created_at, UTC).isoformat(),
            now=datetime.fromtimestamp(now, UTC),
            policy=CardEligibilityPolicy(enabled=True),
        )

    def _card_fact_transaction(
        self, db: StoreConnection, scope: Scope, fact_id: str
    ) -> MemoryFact | None:
        row = db.execute(
            "SELECT * FROM memory_facts WHERE bot_id=? AND group_id=? AND fact_id=? "
            "AND status='active'",
            (scope.bot_id, scope.group_id, fact_id),
        ).fetchone()
        if row is None or not _fact_is_current(row, self._now()):
            return None
        fact = _fact_result(row)
        refs = self._active_source_refs(db, fact.source_ids, scope, fact.subject_id)
        evidence = tuple(ref for ref in fact.evidence_refs if ref in refs)
        if not refs or not evidence:
            return None
        return replace(fact, source_ids=refs, evidence_refs=evidence)

    async def _write_card_classification(
        self, *, actor: str, scope: Scope, fact_id: str,
        expected_fact_revision: int, expected_classification_revision: int,
        category: str | None,
    ) -> int:
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        fact_id = _identity(fact_id, "invalid_memory_fact")
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if (type(expected_fact_revision) is not int or expected_fact_revision < 1
                or type(expected_classification_revision) is not int
                or expected_classification_revision < 0):
            raise OperationError("invalid_memory_card_revision")

        def write(db: StoreConnection) -> int:
            policy_revision = self.policy.check_transaction(
                db, actor, scope, "memory.apply", "", "", False, False
            )
            self.assert_retrieval_actor_transaction(db, actor, scope)
            fact = self._card_fact_transaction(db, scope, fact_id)
            if fact is None or fact.fact_revision != expected_fact_revision:
                raise OperationError("stale_memory_card_fact")
            row = db.execute(
                "SELECT classification_revision FROM memory_card_classifications "
                "WHERE bot_id=? AND group_id=? AND fact_id=?",
                (scope.bot_id, scope.group_id, fact_id),
            ).fetchone()
            revision = int(row[0]) if row is not None else 0
            if revision != expected_classification_revision:
                raise OperationError("revision_conflict")
            if row is None:
                count = db.execute(
                    "SELECT count(*) FROM memory_card_classifications WHERE bot_id=? AND group_id=?",
                    (scope.bot_id, scope.group_id),
                ).fetchone()[0]
                if count >= _MAX_CARD_CLASSIFICATIONS:
                    raise OperationError("memory_card_scope_budget")
            revision += 1
            db.execute(
                "INSERT INTO memory_card_classifications VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(bot_id,group_id,fact_id) DO UPDATE SET "
                "category=excluded.category,classification_revision=excluded.classification_revision,"
                "actor=excluded.actor,policy_revision=excluded.policy_revision,updated_at=excluded.updated_at",
                (scope.bot_id, scope.group_id, fact_id, category, revision, actor,
                 policy_revision, self._now()),
            )
            return revision

        return await self.store.transaction(write)

    async def classify_card(
        self, *, actor: str, scope: Scope, fact_id: str,
        expected_fact_revision: int, expected_classification_revision: int, category: str,
    ) -> int:
        """Explicitly classify an applied self-fact using both owner revisions."""
        return await self._write_card_classification(
            actor=actor, scope=scope, fact_id=fact_id,
            expected_fact_revision=expected_fact_revision,
            expected_classification_revision=expected_classification_revision,
            category=self._card_category(category),
        )

    async def clear_classification(
        self, *, actor: str, scope: Scope, fact_id: str,
        expected_fact_revision: int, expected_classification_revision: int,
    ) -> int:
        """Clear only classification; retain the version to prevent CAS ABA."""
        return await self._write_card_classification(
            actor=actor, scope=scope, fact_id=fact_id,
            expected_fact_revision=expected_fact_revision,
            expected_classification_revision=expected_classification_revision, category=None,
        )

    async def query_cards(
        self, *, actor: str, scope: Scope, query: str = "",
        category: str | None = None, limit: int = _MAX_VISIBLE_FACTS,
    ) -> MemoryCardQueryResult:
        """Complete bounded-scope eligibility/count before ranking and top-k."""
        from .retrieval import (
            _bounded_query_tokens,  # pyright: ignore[reportPrivateUsage]
            _score,  # pyright: ignore[reportPrivateUsage]
        )

        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if type(query) is not str or len(query) > 8192:
            raise OperationError("invalid_memory_card_query")
        tokens, truncated = _bounded_query_tokens(query)
        if truncated:
            raise OperationError("invalid_memory_card_query")
        if category is not None:
            category = self._card_category(category)
        if type(limit) is not int or not 1 <= limit <= _MAX_VISIBLE_FACTS:
            raise OperationError("invalid_memory_search_limit")

        def read(db: StoreConnection) -> MemoryCardQueryResult:
            self.assert_retrieval_actor_transaction(db, actor, scope)
            rows = db.execute(
                "SELECT fact_id,category,classification_revision FROM memory_card_classifications "
                "WHERE bot_id=? AND group_id=? ORDER BY fact_id LIMIT ?",
                (scope.bot_id, scope.group_id, _MAX_CARD_CLASSIFICATIONS + 1),
            ).fetchall()
            if len(rows) > _MAX_CARD_CLASSIFICATIONS:
                raise OperationError("memory_card_scope_budget")
            total = 0
            matches: list[tuple[float, MemoryCard]] = []
            now = self._now()
            for row in rows:
                if row["category"] is None:
                    continue
                fact = self._card_fact_transaction(db, scope, str(row["fact_id"]))
                current_category = str(row["category"])
                if fact is None or not self._card_time_eligible(fact, current_category, now):
                    continue
                total += 1
                if category is not None and category != current_category:
                    continue
                score = _score(fact, tokens)
                if query.strip() and score <= 0:
                    continue
                matches.append((score, MemoryCard(fact, current_category,
                                                  int(row["classification_revision"]))))
            matches.sort(key=lambda item: (item[0], item[1].fact.updated_at,
                                            item[1].fact.fact_id), reverse=True)
            return MemoryCardQueryResult(
                tuple(card for _, card in matches[:limit]), total, len(matches),
                tuple(str(row["fact_id"]) for row in rows if row["category"] is not None),
            )

        return await self.store.transaction(read)

    async def search_cards(
        self, *, actor: str, scope: Scope, query: str,
        category: str | None = None, limit: int = _MAX_VISIBLE_FACTS,
    ) -> MemoryCardQueryResult:
        return await self.query_cards(actor=actor, scope=scope, query=query,
                                      category=category, limit=limit)

    async def read_card(self, *, actor: str, scope: Scope, fact_id: str) -> MemoryCard | None:
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        fact_id = _identity(fact_id, "invalid_memory_fact")
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)

        def read(db: StoreConnection) -> MemoryCard | None:
            self.assert_retrieval_actor_transaction(db, actor, scope)
            row = db.execute(
                "SELECT category,classification_revision FROM memory_card_classifications "
                "WHERE bot_id=? AND group_id=? AND fact_id=? AND category IS NOT NULL",
                (scope.bot_id, scope.group_id, fact_id),
            ).fetchone()
            if row is None:
                return None
            fact = self._card_fact_transaction(db, scope, fact_id)
            category = str(row["category"])
            if fact is None or not self._card_time_eligible(fact, category, self._now()):
                return None
            return MemoryCard(fact, category, int(row["classification_revision"]))

        return await self.store.transaction(read)

    async def read_card_metadata(
        self, *, actor: str, scope: Scope, fact_id: str,
    ) -> tuple[MemoryFact, str | None, int] | None:
        """Read current fact metadata, retaining a cleared classification's CAS revision.

        Classification TTL still controls card consumption; management uses the same
        current fact/source eligibility as classify_card, including unclassified facts.
        """
        actor = _identity(actor, "invalid_memory_actor", limit=64)
        fact_id = _identity(fact_id, "invalid_memory_fact")
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)

        def read(db: StoreConnection) -> tuple[MemoryFact, str | None, int] | None:
            self.assert_retrieval_actor_transaction(db, actor, scope)
            fact = self._card_fact_transaction(db, scope, fact_id)
            if fact is None:
                return None
            row = db.execute(
                "SELECT category,classification_revision FROM memory_card_classifications "
                "WHERE bot_id=? AND group_id=? AND fact_id=?",
                (scope.bot_id, scope.group_id, fact_id),
            ).fetchone()
            if row is None:
                return fact, None, 0
            category = None if row["category"] is None else str(row["category"])
            return fact, category, int(row["classification_revision"])

        return await self.store.transaction(read)

    def assert_unclassified_fact_transaction(
        self, db: StoreConnection, *, scope: Scope, fact_id: str,
    ) -> None:
        """A fact entry cannot bypass a classification added after its snapshot."""
        classified = db.execute(
            "SELECT 1 FROM memory_card_classifications "
            "WHERE bot_id=? AND group_id=? AND fact_id=? AND category IS NOT NULL",
            (scope.bot_id, scope.group_id, fact_id),
        ).fetchone()
        if classified is not None:
            raise OperationError("stale_retrieval")

    def assert_card_projection_transaction(
        self, db: StoreConnection, *, actor: str, scope: Scope, card: MemoryCard,
    ) -> None:
        """Recheck the exact classification and current fact at Actions intent."""
        self.assert_retrieval_actor_transaction(db, actor, scope)
        row = db.execute(
            "SELECT category,classification_revision FROM memory_card_classifications "
            "WHERE bot_id=? AND group_id=? AND fact_id=?",
            (scope.bot_id, scope.group_id, card.fact.fact_id),
        ).fetchone()
        if (row is None or row["category"] != card.category
                or row["classification_revision"] != card.classification_revision
                or card.fact.scope != scope):
            raise OperationError("stale_retrieval")
        fact = self._card_fact_transaction(db, scope, card.fact.fact_id)
        if (fact is None or fact != card.fact
                or not self._card_time_eligible(card.fact, card.category, self._now())):
            raise OperationError("stale_retrieval")
        self.assert_retrieval_fact_transaction(
            db, fact_id=fact.fact_id, fact_revision=fact.fact_revision, scope=scope,
            subject_id=fact.subject_id, predicate=fact.predicate, value=fact.value,
            source_ids=fact.source_ids, evidence_refs=fact.evidence_refs,
        )

    async def search_facts(
        self,
        *,
        actor: str,
        scope: Scope,
        tokens: Sequence[str],
        subject_id: str | None = None,
        limit: int = _MAX_VISIBLE_FACTS,
    ) -> MemoryFactSearchResult:
        """Search a bounded set of current facts without a recent-fact window."""

        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        terms = _search_tokens(tokens)
        if subject_id is not None:
            subject_id = _identity(subject_id, "invalid_memory_subject", limit=64)
        if type(limit) is not int or not 1 <= limit <= _MAX_VISIBLE_FACTS:
            raise OperationError("invalid_memory_search_limit")

        def read(db: StoreConnection) -> MemoryFactSearchResult:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            if not terms:
                return MemoryFactSearchResult((), False)

            now = self._now()
            scores: list[str] = []
            parameters: list[object] = []
            for token in terms:
                clauses: list[str] = []
                for column in ("value", "predicate", "subject_id"):
                    clauses.append(f"{column} LIKE ? ESCAPE '\\'")
                    parameters.append(_like_contains(token))
                # Refs are JSON arrays. Quoting the token in the pattern keeps
                # the SQL prefilter aligned with the scorer's exact ref match.
                for column in ("source_ids", "evidence_refs"):
                    clauses.append(f"{column} LIKE ? ESCAPE '\\'")
                    parameters.append(_like_contains(f'"{token}"'))
                scores.append("CASE WHEN (" + " OR ".join(clauses) + ") THEN 1 ELSE 0 END")

            subject_clause = " AND subject_id=?" if subject_id is not None else ""
            subject_params: tuple[object, ...] = (
                (subject_id,) if subject_id is not None else ()
            )

            rows = db.execute(
                "SELECT * FROM (SELECT memory_facts.*, ("
                + " + ".join(scores)
                + ") AS query_match_count FROM memory_facts WHERE bot_id=? AND group_id=? "
                "AND status='active' AND suppressed_at IS NULL "
                + subject_clause
                + " AND (valid_from IS NULL OR valid_from<=?) "
                "AND (valid_to IS NULL OR valid_to>?)) "
                "WHERE query_match_count>0 "
                "ORDER BY query_match_count DESC,updated_at DESC,fact_id DESC LIMIT ?",
                (
                    *parameters,
                    scope.bot_id,
                    scope.group_id,
                    *subject_params,
                    now,
                    now,
                    limit + 1,
                ),
            ).fetchall()
            truncated = len(rows) > limit
            result: list[MemoryFact] = []
            for row in rows[:limit]:
                if not _fact_is_current(row, now):
                    continue
                fact = _fact_result(row)
                refs = self._active_source_refs(
                    db, fact.source_ids, scope, fact.subject_id
                )
                if not refs:
                    continue
                visible_evidence = tuple(
                    ref for ref in fact.evidence_refs if ref in refs
                )
                result.append(
                    replace(fact, source_ids=refs, evidence_refs=visible_evidence)
                )
            return MemoryFactSearchResult(tuple(result), truncated)

        return await self.store.transaction(read)

    async def search_hot_facts(
        self,
        *,
        actor: str,
        scope: Scope,
        subject_ids: Sequence[str],
        limit: int = 12,
    ) -> MemoryFactSearchResult:
        """Read stable, source-authorized facts for explicitly addressed people.

        The caller supplies only the current speaker and trusted current-turn
        addressees. Exact subject and predicate constraints are part of the SQL
        query, so unrelated recent facts cannot hide eligible hot facts.
        """

        actor = _identity(actor, "invalid_memory_actor", limit=64)
        scope = _scope(scope)
        self._validate_scope(scope, self.policy)
        if isinstance(subject_ids, str) or type(limit) is not int or not 1 <= limit <= 12:
            raise OperationError("invalid_memory_hot_query")
        try:
            subject_count = len(subject_ids)
        except TypeError as exc:
            raise OperationError("invalid_memory_hot_query") from exc
        if subject_count > 4:
            raise OperationError("invalid_memory_hot_query")
        subjects: list[str] = []
        for subject_id in subject_ids:
            normalized = _identity(
                subject_id, "invalid_memory_subject", limit=64
            )
            if normalized not in subjects:
                subjects.append(normalized)
        predicates = (
            "identity.preferred_name",
            "communication.boundary",
            "communication.preference",
        )

        def read(db: StoreConnection) -> MemoryFactSearchResult:
            self._authorize_actor_capability(db, actor, scope, "memory.retrieve")
            if not subjects:
                return MemoryFactSearchResult((), False)

            now = self._now()
            subject_clause = ",".join("?" for _ in subjects)
            predicate_clause = ",".join("?" for _ in predicates)
            priority = "CASE predicate " + " ".join(
                f"WHEN ? THEN {index}" for index, _ in enumerate(predicates)
            ) + " ELSE 99 END"
            rows = db.execute(
                "SELECT * FROM memory_facts WHERE bot_id=? AND group_id=? "
                "AND subject_id IN (" + subject_clause + ") "
                "AND predicate IN (" + predicate_clause + ") "
                "AND status='active' AND applied_at IS NOT NULL "
                "AND suppressed_at IS NULL "
                "AND (valid_from IS NULL OR valid_from<=?) "
                "AND (valid_to IS NULL OR valid_to>?) "
                "ORDER BY " + priority + ", (valid_to IS NOT NULL), valid_to DESC, fact_id ASC "
                "LIMIT ?",
                (
                    scope.bot_id,
                    scope.group_id,
                    *subjects,
                    *predicates,
                    now,
                    now,
                    *predicates,
                    limit + 1,
                ),
            ).fetchall()
            truncated = len(rows) > limit
            result: list[MemoryFact] = []
            for row in rows[:limit]:
                if not _fact_is_current(row, now):
                    continue
                fact = _fact_result(row)
                refs = self._active_source_refs(
                    db, fact.source_ids, scope, fact.subject_id
                )
                if not refs:
                    continue
                evidence = tuple(ref for ref in fact.evidence_refs if ref in refs)
                if not evidence:
                    continue
                result.append(
                    replace(fact, source_ids=refs, evidence_refs=evidence)
                )
            return MemoryFactSearchResult(tuple(result), truncated)

        return await self.store.transaction(read)
