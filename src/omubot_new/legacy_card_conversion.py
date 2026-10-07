"""Deterministic reports from explicitly supplied synthetic legacy Card JSON.

No fact payload, source enrollment, authorization or storage operation is produced.
Legacy provenance labels never become current authenticated Archive identities.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

Scalar = str | int | float | bool | None
RecordStatus = Literal["requires_current_owner_review", "unsupported", "invalid"]
ReportStatus = Literal[
    "mapping_only_requires_review", "mapping_only_with_unsupported", "mapping_only_with_errors"
]
_REQUIRED = (
    "card_id",
    "category",
    "scope",
    "scope_id",
    "content",
    "confidence",
    "status",
    "priority",
    "supersedes",
    "source",
    "source_msg_id",
    "captured_at",
    "captured_by",
    "created_at",
    "updated_at",
    "last_seen_at",
    "ttl_turns",
)
_OPTIONAL = ("series_id", "origin_group_id", "visibility", "subject_user_id")
_FIELDS = frozenset((*_REQUIRED, *_OPTIONAL))
_NULLABLE_STRINGS = frozenset({"supersedes", "source_msg_id", "captured_at", "last_seen_at", *_OPTIONAL})
_CATEGORIES = frozenset({"preference", "boundary", "relationship", "event", "promise", "fact", "status"})
_STATUSES = frozenset({"active", "superseded", "expired"})
_VISIBILITY = frozenset({"private", "same_group", "global"})
_TIMES = ("created_at", "updated_at", "captured_at", "last_seen_at")


class LegacyCardInputError(ValueError):
    """Whole-document failure, with a content-free stable diagnostic code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CardMappingIssue:
    field: str | None
    code: str
    kind: Literal["invalid", "unsupported"]


@dataclass(frozen=True, slots=True)
class CardBodySummary:
    utf8_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class LegacyCardRecordReport:
    row_index: int
    legacy_id: str | None
    metadata: dict[str, Scalar]
    body: CardBodySummary | None
    status: RecordStatus
    issues: tuple[CardMappingIssue, ...]
    duplicate_indexes: tuple[int, ...]
    target_id: None = None


@dataclass(frozen=True, slots=True)
class LegacyCardMappingReport:
    records: tuple[LegacyCardRecordReport, ...]
    status: ReportStatus
    schema_version: Literal[1] = 1
    source_kind: Literal["synthetic"] = "synthetic"
    mapping_only: Literal[True] = True
    application_performed: Literal[False] = False
    authorization_checked: Literal[False] = False
    limitations: tuple[str, ...] = (
        "synthetic_card_json_only_not_real_legacy_export_or_markdown_enrollment",
        "category_syntax_is_not_a_typed_fact_or_import_permission",
        "source_and_captured_by_labels_do_not_authenticate_the_source_author",
        "legacy_message_id_is_not_a_current_archive_source_id",
        "personal_facts_require_curated_typed_fact_current_authenticated_source_binding_review_and_apply",
        "no_new_owner_ids_or_executable_migration_payload_are_generated",
        "legacy_to_owner_id_mapping_and_atomic_import_require_separate_phase_three_contract",
        "no_scope_aggregation_time_defaults_or_turn_ttl_are_applied",
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LegacyCardInputError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_constant: str) -> None:
    raise LegacyCardInputError("invalid_json_number")


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise LegacyCardInputError("invalid_json_number")
    return value


def _record(row: object, index: int, duplicates: dict[str, tuple[int, ...]]) -> LegacyCardRecordReport:
    issues: list[CardMappingIssue] = []
    metadata: dict[str, Scalar] = {}
    body: CardBodySummary | None = None

    def issue(field: str | None, code: str, kind: Literal["invalid", "unsupported"] = "invalid") -> None:
        issues.append(CardMappingIssue(field, code, kind))

    if not isinstance(row, dict):
        return LegacyCardRecordReport(
            index, None, {}, None, "invalid", (CardMappingIssue(None, "expected_card_object", "invalid"),), ()
        )
    values = cast(dict[str, object], row)
    for key in _REQUIRED:
        if key not in values:
            issue(key, "missing_field")
    for key, value in values.items():
        if key not in _FIELDS:
            issue(key, "unknown_card_field", "unsupported")
            continue
        if key == "content":
            if not isinstance(value, str):
                issue(key, "expected_string")
            else:
                try:
                    encoded = value.encode("utf-8")
                except UnicodeError:
                    issue(key, "invalid_unicode")
                else:
                    body = CardBodySummary(len(encoded), hashlib.sha256(encoded).hexdigest())
                    if not encoded:
                        issue(key, "empty_content_has_no_typed_fact", "unsupported")
            continue
        if value is None or type(value) in {str, int, float, bool}:
            if isinstance(value, str):
                try:
                    value.encode("utf-8")
                except UnicodeError:
                    issue(key, "invalid_unicode")
                    continue
            metadata[key] = cast(Scalar, value)
        else:
            issue(key, "expected_scalar_metadata")
            continue
        if key == "confidence":
            if type(value) not in {int, float}:
                issue(key, "expected_number")
        elif key in {"priority", "ttl_turns"}:
            if not (type(value) is int or key == "ttl_turns" and value is None):
                issue(key, "expected_integer")
        elif not (isinstance(value, str) or key in _NULLABLE_STRINGS and value is None):
            issue(key, "expected_string")
    for key in ("card_id", "category", "scope"):
        if metadata.get(key) == "":
            issue(key, "empty_identity")
    if "category" in metadata and metadata["category"] not in _CATEGORIES:
        issue("category", "invalid_category")
    if "status" in metadata and metadata["status"] not in _STATUSES:
        issue("status", "invalid_status")
    if metadata.get("visibility") is not None and metadata["visibility"] not in _VISIBILITY:
        issue("visibility", "invalid_visibility")
    for key in _TIMES:
        value = metadata.get(key)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                issue(key, "invalid_timestamp")
            else:
                if parsed.tzinfo is None:
                    issue(key, "timestamp_timezone_unknown", "unsupported")
    scope = metadata.get("scope")
    if scope == "group":
        issue("scope", "group_domain_contract_required", "unsupported")
    elif scope == "global":
        issue("scope", "global_scope_not_mapped", "unsupported")
    elif scope != "user":
        issue("scope", "unsupported_scope", "unsupported")
    if scope in {"user", "group"} and metadata.get("scope_id") == "":
        issue("scope_id", "empty_scope_id")
    if metadata.get("visibility") == "private":
        issue("visibility", "private_visibility_not_mapped", "unsupported")
    elif metadata.get("visibility") != "same_group":
        issue("visibility", "same_group_visibility_required", "unsupported")
    if not metadata.get("origin_group_id"):
        issue("origin_group_id", "missing_origin_group_no_default", "unsupported")
    if not metadata.get("subject_user_id"):
        issue("subject_user_id", "missing_subject_no_inference", "unsupported")
    elif scope == "user" and metadata["subject_user_id"] != metadata.get("scope_id"):
        issue("subject_user_id", "third_person_not_mapped", "unsupported")
    if metadata.get("category") == "relationship":
        issue("category", "relationship_requires_separate_domain_evidence", "unsupported")
    if metadata.get("source") in {"canon", "episode"}:
        issue("source", "named_domain_is_not_authenticated_evidence", "unsupported")
    if not metadata.get("source"):
        issue("source", "missing_source_provenance", "unsupported")
    if not metadata.get("source_msg_id"):
        issue("source_msg_id", "missing_source_message_binding", "unsupported")
    if not metadata.get("captured_at"):
        issue("captured_at", "missing_observation_time", "unsupported")
    if metadata.get("status") != "active":
        issue("status", "legacy_inactive_not_applied", "unsupported")
    if metadata.get("ttl_turns") is not None:
        issue("ttl_turns", "ttl_turns_semantics_unsupported", "unsupported")
    identity = metadata.get("card_id")
    legacy_id = identity if isinstance(identity, str) and identity else None
    duplicate_indexes = duplicates.get(legacy_id, ()) if legacy_id is not None else ()
    if duplicate_indexes:
        issue("card_id", "duplicate_legacy_id")
    status: RecordStatus = (
        "invalid"
        if any(item.kind == "invalid" for item in issues)
        else "unsupported"
        if issues
        else "requires_current_owner_review"
    )
    return LegacyCardRecordReport(index, legacy_id, metadata, body, status, tuple(issues), duplicate_indexes)


def convert_legacy_cards(data: bytes, *, max_input_bytes: int) -> LegacyCardMappingReport:
    """Parse one bounded UTF-8 JSON input; caller supplies its positive byte budget.

    The CLI validates its budget once before boundedread. Every card gets a report,
    including malformed rows. Whole-envelope errors raise and produce no report.
    Original metadata is retained without normalization; no time/author is inferred.
    """
    if len(data) > max_input_bytes:
        raise LegacyCardInputError("input_byte_budget")
    try:
        document = cast(
            object,
            json.loads(
                data.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
                parse_float=_finite_float,
            ),
        )
    except LegacyCardInputError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise LegacyCardInputError("invalid_json") from exc
    if not isinstance(document, dict):
        raise LegacyCardInputError("invalid_envelope")
    envelope = cast(dict[str, object], document)
    if set(envelope) != {"schema_version", "source_kind", "cards"}:
        raise LegacyCardInputError("invalid_envelope")
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        raise LegacyCardInputError("unsupported_schema_version")
    if envelope["source_kind"] != "synthetic":
        raise LegacyCardInputError("unsupported_source_kind")
    cards = envelope["cards"]
    if not isinstance(cards, list):
        raise LegacyCardInputError("expected_cards_array")
    card_rows = cast(list[object], cards)
    positions: dict[str, list[int]] = {}
    for index, row in enumerate(card_rows):
        if isinstance(row, dict):
            identity = cast(dict[str, object], row).get("card_id")
            if isinstance(identity, str):
                positions.setdefault(identity, []).append(index)
    duplicates = {identity: tuple(indexes) for identity, indexes in positions.items() if len(indexes) > 1}
    records = tuple(_record(row, index, duplicates) for index, row in enumerate(card_rows))
    counts = Counter(item.status for item in records)
    status: ReportStatus = (
        "mapping_only_with_errors"
        if counts["invalid"]
        else "mapping_only_with_unsupported"
        if counts["unsupported"]
        else "mapping_only_requires_review"
    )
    return LegacyCardMappingReport(records, status)
