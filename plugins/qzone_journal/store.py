"""Governed SQLite outbox for QZone Journal drafts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

from plugins.qzone_journal.public_safety import (
    contains_secret_or_id_assignment,
    scrub_public_text,
    scrub_qq_like_digits,
)
from services.storage import close_with_checkpoint, connect_sqlite
from services.storage.migrations import Migration, MigrationRunner

_CST = ZoneInfo("Asia/Shanghai")
_DB_ID = "qzone_journal"
_MIGRATION_CHECKSUM = "qzone-journal-v1-drafts-outbox-20260715"
_MIGRATION_V2_CHECKSUM = "qzone-journal-v2-manual-resolutions-20260715"
_MIGRATION_V3_CHECKSUM = "qzone-journal-v3-review-provenance-20260716"
_MIGRATION_V4_CHECKSUM = "qzone-journal-v4-review-decisions-20260716"
_MIGRATION_V5_CHECKSUM = "qzone-journal-v5-draft-revisions-20260716"
_MIGRATION_V6_CHECKSUM = "qzone-journal-v6-approval-scope-20260718"
_NOTE_MAX_LEN = 500
_RECOMPOSE_ELIGIBLE_STATUSES = frozenset({"pending_review", "rejected"})
_ALLOWED_DRAFT_STATUSES = frozenset({
    "pending_review",
    "approved",
    "dispatching",
    "published",
    "rejected",
    "failed",
    "unknown",
})
_EXTERNAL_POST_ID_MAX_LEN = 120
_EXTERNAL_POST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_STABLE_ID_MAX_LEN = 180
_SOURCE_SUMMARY_MAX_LEN = 500
_PROVENANCE_JSON_MAX_LEN = 4000
_ALLOWED_SUBJECT_KINDS = frozenset({"self", "fiction", "factual"})
_ALLOWED_PRIVACY = frozenset({"public", "private", "unknown"})
_ALLOWED_APPROVAL_SCOPES = frozenset({"dry_run", "live"})
_PROVENANCE_ALLOWED_KEYS_V1 = frozenset({
    "schema_version",
    "arc_id",
    "arc_revision",
    "arc_stage",
    "arc_scope",
    "advanced_context_included",
    "fiction_partner_entity_ids",
})
_PROVENANCE_ALLOWED_KEYS_V2 = _PROVENANCE_ALLOWED_KEYS_V1 | frozenset({
    "public_projection",
})
# Backward-compatible alias used by fail-closed key scans.
_PROVENANCE_ALLOWED_KEYS = _PROVENANCE_ALLOWED_KEYS_V2
_PROVENANCE_FORBIDDEN_KEYS = frozenset({
    "cookie",
    "cookies",
    "p_skey",
    "token",
    "tokens",
    "credential",
    "credentials",
    "password",
    "secret",
    "authorization",
    "social_narrative",
    "user_id",
    "group_id",
    "uin",
    "raw",
    "body",
    "entity_key",
    "internal_ref",
    "internal_refs",
    "surface",
    "surfaces",
    "raw_summary",
    "projected_summary",
})
_CREATE_DRAFTS = """
CREATE TABLE IF NOT EXISTS qzone_journal_drafts (
    draft_id          TEXT PRIMARY KEY,
    dedupe_key        TEXT NOT NULL UNIQUE,
    event_date        TEXT NOT NULL,
    source            TEXT NOT NULL,
    content           TEXT NOT NULL,
    status            TEXT NOT NULL
                      CHECK (status IN (
                          'pending_review', 'approved', 'dispatching',
                          'published', 'rejected', 'failed', 'unknown'
                      )),
    publish_date      TEXT,
    external_post_id  TEXT,
    last_error_code   TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
)
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_qzone_drafts_status ON qzone_journal_drafts(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_qzone_drafts_publish_date ON qzone_journal_drafts(publish_date, status)",
)

_CREATE_MANUAL_RESOLUTIONS = """
CREATE TABLE IF NOT EXISTS qzone_journal_manual_resolutions (
    resolution_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id          TEXT NOT NULL,
    decision          TEXT NOT NULL
                      CHECK (decision IN ('confirm_published', 'confirm_not_published')),
    note              TEXT NOT NULL,
    previous_status   TEXT NOT NULL,
    new_status        TEXT NOT NULL,
    external_post_id  TEXT,
    created_at        TEXT NOT NULL
)
"""

_CREATE_MANUAL_RESOLUTION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_qzone_manual_resolutions_draft "
    "ON qzone_journal_manual_resolutions(draft_id, created_at)",
)

_CREATE_REVIEW_DECISIONS = """
CREATE TABLE IF NOT EXISTS qzone_journal_review_decisions (
    decision_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id          TEXT NOT NULL,
    decision          TEXT NOT NULL
                      CHECK (decision IN ('approve', 'reject')),
    note              TEXT NOT NULL DEFAULT '',
    previous_status   TEXT NOT NULL,
    new_status        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)
"""

_CREATE_REVIEW_DECISION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_qzone_review_decisions_draft "
    "ON qzone_journal_review_decisions(draft_id, created_at)",
)


@dataclass(frozen=True, slots=True)
class JournalDraft:
    draft_id: str
    dedupe_key: str
    event_date: date
    source: str
    content: str
    status: str
    publish_date: date | None
    external_post_id: str | None
    last_error_code: str | None
    created_at: str
    updated_at: str
    stable_id: str | None = None
    subject_kind: str | None = None
    privacy: str | None = None
    salience: float | None = None
    source_summary: str | None = None
    provenance_json: str | None = None
    revision_root_id: str | None = None
    revision: int | None = None
    supersedes_draft_id: str | None = None
    approval_scope: str = "dry_run"


class DayDraftBudgetExceededError(RuntimeError):
    """Raised when per-event-date draft budget is exhausted at enqueue time."""


class InvalidDraftTransitionError(RuntimeError):
    """A review or delivery transition is not legal from the current state."""


def _is_supersedes_unique_conflict(exc: BaseException) -> bool:
    """True only for UNIQUE conflicts on ``qzone_journal_drafts.supersedes_draft_id``.

    Prefer SQLite extended result metadata (``sqlite_errorcode`` /
    ``sqlite_errorname``) when present, plus a narrowly verified constraint
    identity. Other UNIQUE/CHECK/NOT NULL integrity failures must not match.
    """
    if not isinstance(exc, sqlite3.IntegrityError):
        return False
    message = str(exc)
    # Exact constraint identity — never map other unique columns.
    if "qzone_journal_drafts.supersedes_draft_id" not in message:
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    name = getattr(exc, "sqlite_errorname", None)
    if code is not None:
        return int(code) == 2067  # SQLITE_CONSTRAINT_UNIQUE
    if name is not None:
        return str(name) == "SQLITE_CONSTRAINT_UNIQUE"
    # Extended metadata unavailable (rare): require classic UNIQUE wording.
    return "UNIQUE constraint failed" in message


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _draft_id(dedupe_key: str) -> str:
    return "qzd_" + hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:24]


def _sanitize_note(note: str) -> str:
    # Shared public scrub (unicode normalize + secret/id redaction) then length.
    cleaned = scrub_public_text(note)
    if not cleaned:
        raise ValueError("manual resolution note is required")
    if len(cleaned) > _NOTE_MAX_LEN:
        raise ValueError(f"manual resolution note exceeds {_NOTE_MAX_LEN} characters")
    return cleaned


def _sanitize_optional_operator_note(note: str | None) -> str:
    """Optional review note: scrub secrets/ids; empty after scrub becomes ''."""
    if note is None:
        return ""
    cleaned = scrub_public_text(note)
    if len(cleaned) > _NOTE_MAX_LEN:
        raise ValueError(f"operator note exceeds {_NOTE_MAX_LEN} characters")
    return cleaned


def _sanitize_external_post_id(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if len(cleaned) > _EXTERNAL_POST_ID_MAX_LEN:
        raise ValueError(
            f"external_post_id exceeds {_EXTERNAL_POST_ID_MAX_LEN} characters"
        )
    if _EXTERNAL_POST_ID_RE.fullmatch(cleaned) is None:
        raise ValueError("external_post_id contains unsupported characters")
    return cleaned


def _sanitize_approval_scope(value: str) -> str:
    scope = str(value or "").strip()
    if scope not in _ALLOWED_APPROVAL_SCOPES:
        raise ValueError("approval_scope must be dry_run or live")
    return scope


def _row_keys(row: aiosqlite.Row) -> set[str]:
    try:
        return set(row.keys())
    except Exception:
        return set()


def _optional_row_str(row: aiosqlite.Row, key: str) -> str | None:
    keys = _row_keys(row)
    if key not in keys:
        return None
    value = row[key]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_row_float(row: aiosqlite.Row, key: str) -> float | None:
    keys = _row_keys(row)
    if key not in keys:
        return None
    value = row[key]
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_row_int(row: aiosqlite.Row, key: str) -> int | None:
    keys = _row_keys(row)
    if key not in keys:
        return None
    value = row[key]
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_to_draft(row: aiosqlite.Row) -> JournalDraft:
    publish_date = str(row["publish_date"] or "").strip()
    draft_id = str(row["draft_id"])
    revision_root = _optional_row_str(row, "revision_root_id") or draft_id
    revision = _optional_row_int(row, "revision")
    if revision is None or revision < 1:
        revision = 1
    return JournalDraft(
        draft_id=draft_id,
        dedupe_key=str(row["dedupe_key"]),
        event_date=date.fromisoformat(str(row["event_date"])),
        source=str(row["source"]),
        content=str(row["content"]),
        status=str(row["status"]),
        publish_date=date.fromisoformat(publish_date) if publish_date else None,
        external_post_id=(
            str(row["external_post_id"])
            if row["external_post_id"] is not None
            else None
        ),
        last_error_code=(
            str(row["last_error_code"])
            if row["last_error_code"] is not None
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        stable_id=_optional_row_str(row, "stable_id"),
        subject_kind=_optional_row_str(row, "subject_kind"),
        privacy=_optional_row_str(row, "privacy"),
        salience=_optional_row_float(row, "salience"),
        source_summary=_optional_row_str(row, "source_summary"),
        provenance_json=_optional_row_str(row, "provenance_json"),
        revision_root_id=revision_root,
        revision=revision,
        supersedes_draft_id=_optional_row_str(row, "supersedes_draft_id"),
        approval_scope=(
            _optional_row_str(row, "approval_scope") or "dry_run"
        ),
    )


def _revision_dedupe_key(*, root_id: str, revision: int, supersedes_id: str) -> str:
    """Unique per-row key; logical event identity remains on revision 1."""
    material = f"rev|{root_id}|{revision}|{supersedes_id}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"qzone_rev:{digest}"


def _contains_secret_assignment(text: str) -> bool:
    return contains_secret_or_id_assignment(text)


def _scrub_qq_like_digits(text: str) -> str:
    """Replace standalone 5-16 digit runs (digit-boundary) with a redaction marker."""
    return scrub_qq_like_digits(text)


def _sanitize_optional_text(value: str | None, *, max_len: int, field: str) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    if not cleaned:
        return None
    if len(cleaned) > max_len:
        raise ValueError(f"{field} exceeds {max_len} characters")
    return cleaned


def _sanitize_review_text(
    value: str | None,
    *,
    max_len: int,
    field: str,
    allow_pure_redaction: bool = False,
) -> str | None:
    """Reject secret assignments; scrub QQ-like digit runs from review fields."""
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    if not cleaned:
        return None
    if _contains_secret_assignment(cleaned):
        raise ValueError(f"{field} contains secret or id field assignment")
    cleaned = _scrub_qq_like_digits(cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        return None
    if cleaned == "[redacted]" and not allow_pure_redaction:
        raise ValueError(f"{field} must not contain QQ-like identifiers")
    if len(cleaned) > max_len:
        raise ValueError(f"{field} exceeds {max_len} characters")
    return cleaned


def _sanitize_subject_kind(value: str | None) -> str | None:
    cleaned = _sanitize_optional_text(value, max_len=32, field="subject_kind")
    if cleaned is None:
        return None
    if cleaned not in _ALLOWED_SUBJECT_KINDS:
        raise ValueError(
            "subject_kind must be self, fiction, or factual when provided"
        )
    return cleaned


def _sanitize_privacy(value: str | None) -> str | None:
    cleaned = _sanitize_optional_text(value, max_len=32, field="privacy")
    if cleaned is None:
        return None
    if cleaned not in _ALLOWED_PRIVACY:
        raise ValueError("privacy must be public, private, or unknown when provided")
    return cleaned


def _sanitize_salience(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("salience must be a float in [0, 1]") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError("salience must be a float in [0, 1]")
    return number


def _require_real_bool(value: Any, *, field: str) -> bool:
    """Accept only real bool; reject truthy strings like 'false' / '1'."""
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be a boolean")


def _sanitize_public_projection_meta(value: Any) -> dict[str, Any]:
    """Persist only bounded safe public projection metadata (no internal refs).

    Delegates to the shared strict validator in ``public_projection`` so store
    and projector share exact types, closed claims/templates, and allowlists.
    """
    from plugins.qzone_journal.public_projection import (
        validate_public_projection_metadata,
    )

    if not isinstance(value, dict):
        raise ValueError("provenance.public_projection must be an object")
    lowered = {str(key).strip().lower() for key in value}
    if lowered & _PROVENANCE_FORBIDDEN_KEYS:
        raise ValueError("public_projection contains forbidden keys")
    return validate_public_projection_metadata(value)


def _sanitize_provenance(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("provenance_json must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("provenance must be a JSON object")
        payload = parsed
    elif isinstance(value, dict):
        payload = value
    else:
        raise ValueError("provenance must be a mapping or JSON object string")

    lowered = {str(key).strip().lower() for key in payload}
    forbidden = lowered & _PROVENANCE_FORBIDDEN_KEYS
    if forbidden:
        raise ValueError(
            "provenance contains forbidden keys: " + ", ".join(sorted(forbidden))
        )

    schema_version = payload.get("schema_version", 1)
    if type(schema_version) is not int:
        raise ValueError("provenance.schema_version must be an integer")
    schema_version_int = schema_version
    if schema_version_int not in {1, 2}:
        raise ValueError("unsupported provenance.schema_version")

    allowed_keys = (
        _PROVENANCE_ALLOWED_KEYS_V2
        if schema_version_int == 2
        else _PROVENANCE_ALLOWED_KEYS_V1
    )
    unknown = set(str(key) for key in payload) - allowed_keys
    if unknown:
        raise ValueError(
            "provenance contains unsupported keys: " + ", ".join(sorted(unknown))
        )

    arc_id = _sanitize_review_text(
        payload.get("arc_id") if payload.get("arc_id") is not None else None,
        max_len=64,
        field="provenance.arc_id",
    )
    arc_stage = _sanitize_review_text(
        payload.get("arc_stage") if payload.get("arc_stage") is not None else None,
        max_len=40,
        field="provenance.arc_stage",
    )
    arc_scope = _sanitize_review_text(
        payload.get("arc_scope") if payload.get("arc_scope") is not None else None,
        max_len=40,
        field="provenance.arc_scope",
    )
    try:
        arc_revision = int(payload.get("arc_revision", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("provenance.arc_revision must be an integer") from exc
    arc_revision = max(0, min(arc_revision, 1_000_000))

    if "advanced_context_included" not in payload:
        advanced_included = False
    else:
        advanced_included = _require_real_bool(
            payload.get("advanced_context_included"),
            field="provenance.advanced_context_included",
        )

    partner_ids_raw = payload.get("fiction_partner_entity_ids", [])
    if partner_ids_raw is None:
        partner_ids_raw = []
    if not isinstance(partner_ids_raw, list):
        raise ValueError("provenance.fiction_partner_entity_ids must be a list")
    partner_ids: list[str] = []
    for item in partner_ids_raw:
        cleaned = _sanitize_review_text(
            str(item) if item is not None else None,
            max_len=48,
            field="fiction partner entity id",
            allow_pure_redaction=True,
        )
        if cleaned is None or cleaned == "[redacted]":
            # Drop partner ids that collapse to pure redaction after scrubbing.
            continue
        if cleaned not in partner_ids:
            partner_ids.append(cleaned)
        if len(partner_ids) >= 12:
            break

    public_projection_meta: dict[str, Any] | None = None
    if schema_version_int == 2:
        if "public_projection" not in payload or payload.get("public_projection") is None:
            raise ValueError(
                "provenance schema_version 2 requires public_projection"
            )
        public_projection_meta = _sanitize_public_projection_meta(
            payload.get("public_projection")
        )
    elif "public_projection" in payload and payload.get("public_projection") is not None:
        raise ValueError(
            "public_projection requires provenance schema_version 2"
        )

    normalized: dict[str, Any] = {
        "schema_version": schema_version_int,
        "arc_id": arc_id,
        "arc_revision": arc_revision,
        "arc_stage": arc_stage,
        "arc_scope": arc_scope,
        "advanced_context_included": advanced_included,
        "fiction_partner_entity_ids": partner_ids,
    }
    if public_projection_meta is not None:
        normalized["public_projection"] = public_projection_meta
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    # Final fail-closed scan of the encoded payload.
    # Hex source_event_hash is already validated as 64-char [0-9a-f]; strip it
    # before QQ-digit scan so legitimate digests with digit runs are not rejected.
    scan_blob = encoded
    if public_projection_meta is not None:
        digest = str(public_projection_meta.get("source_event_hash") or "")
        if digest:
            scan_blob = scan_blob.replace(digest, "")
    if scrub_qq_like_digits(scan_blob) != scan_blob or _contains_secret_assignment(
        scan_blob
    ):
        raise ValueError(
            "provenance must not retain QQ-like identifiers or secret assignments"
        )
    # Internal-key tokens must never appear in persisted provenance.
    if re.search(r"(?i)\b(?:user|group|entity)[:#/]", encoded):
        raise ValueError("provenance must not retain internal identity keys")
    if len(encoded) > _PROVENANCE_JSON_MAX_LEN:
        raise ValueError(
            f"provenance_json exceeds {_PROVENANCE_JSON_MAX_LEN} characters"
        )
    return encoded


def validate_review_fields_for_compose(
    *,
    stable_id: str | None = None,
    subject_kind: str | None = None,
    privacy: str | None = None,
    salience: float | int | None = None,
    source_summary: str | None = None,
    provenance: Any | None = None,
) -> None:
    """Validate review fields with the same safety rules enqueue applies.

    Raises ``ValueError`` on poison stable_id / summary / provenance so callers
    can refuse before LLM compose. Enqueue still re-validates as defense in depth.
    """
    _sanitize_review_text(
        stable_id,
        max_len=_STABLE_ID_MAX_LEN,
        field="stable_id",
    )
    _sanitize_subject_kind(subject_kind)
    _sanitize_privacy(privacy)
    _sanitize_salience(salience)
    _sanitize_review_text(
        source_summary,
        max_len=_SOURCE_SUMMARY_MAX_LEN,
        field="source_summary",
    )
    if provenance is not None:
        _sanitize_provenance(provenance)


def _row_to_resolution(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "resolution_id": int(row["resolution_id"]),
        "draft_id": str(row["draft_id"]),
        "decision": str(row["decision"]),
        "note": str(row["note"]),
        "previous_status": str(row["previous_status"]),
        "new_status": str(row["new_status"]),
        "external_post_id": (
            str(row["external_post_id"])
            if row["external_post_id"] is not None
            else None
        ),
        "created_at": str(row["created_at"]),
    }


def _row_to_review_decision(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "decision_id": int(row["decision_id"]),
        "draft_id": str(row["draft_id"]),
        "decision": str(row["decision"]),
        "note": str(row["note"] or ""),
        "previous_status": str(row["previous_status"]),
        "new_status": str(row["new_status"]),
        "created_at": str(row["created_at"]),
    }


async def _apply_v1(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_DRAFTS)
    for statement in _CREATE_INDEXES:
        await db.execute(statement)


async def _verify_v1(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute("PRAGMA table_info(qzone_journal_drafts)")
    try:
        columns = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    required = {
        "draft_id",
        "dedupe_key",
        "event_date",
        "source",
        "content",
        "status",
        "publish_date",
        "external_post_id",
        "last_error_code",
        "created_at",
        "updated_at",
    }
    if not required <= columns:
        return False
    cursor = await db.execute("PRAGMA index_list(qzone_journal_drafts)")
    try:
        indexes = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    return {
        "idx_qzone_drafts_status",
        "idx_qzone_drafts_publish_date",
    } <= indexes


async def _apply_v2(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_MANUAL_RESOLUTIONS)
    for statement in _CREATE_MANUAL_RESOLUTION_INDEXES:
        await db.execute(statement)


async def _verify_v2(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute("PRAGMA table_info(qzone_journal_manual_resolutions)")
    try:
        columns = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    required = {
        "resolution_id",
        "draft_id",
        "decision",
        "note",
        "previous_status",
        "new_status",
        "external_post_id",
        "created_at",
    }
    if not required <= columns:
        return False
    cursor = await db.execute("PRAGMA index_list(qzone_journal_manual_resolutions)")
    try:
        indexes = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    return "idx_qzone_manual_resolutions_draft" in indexes


_V3_COLUMNS: tuple[tuple[str, str], ...] = (
    ("stable_id", "TEXT"),
    ("subject_kind", "TEXT"),
    ("privacy", "TEXT"),
    ("salience", "REAL"),
    ("source_summary", "TEXT"),
    ("provenance_json", "TEXT"),
)


async def _apply_v3(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(qzone_journal_drafts)")
    try:
        existing = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    for name, definition in _V3_COLUMNS:
        if name in existing:
            continue
        await db.execute(
            f"ALTER TABLE qzone_journal_drafts ADD COLUMN {name} {definition}"
        )


async def _verify_v3(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute("PRAGMA table_info(qzone_journal_drafts)")
    try:
        columns = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    required = {name for name, _definition in _V3_COLUMNS}
    return required <= columns


async def _apply_v4(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_REVIEW_DECISIONS)
    for statement in _CREATE_REVIEW_DECISION_INDEXES:
        await db.execute(statement)


async def _verify_v4(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute("PRAGMA table_info(qzone_journal_review_decisions)")
    try:
        columns = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    required = {
        "decision_id",
        "draft_id",
        "decision",
        "note",
        "previous_status",
        "new_status",
        "created_at",
    }
    if not required <= columns:
        return False
    cursor = await db.execute("PRAGMA index_list(qzone_journal_review_decisions)")
    try:
        indexes = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    return "idx_qzone_review_decisions_draft" in indexes


_V5_COLUMNS: tuple[tuple[str, str], ...] = (
    ("revision_root_id", "TEXT"),
    ("revision", "INTEGER"),
    ("supersedes_draft_id", "TEXT"),
)

_CREATE_REVISION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_qzone_drafts_revision_root "
    "ON qzone_journal_drafts(revision_root_id, revision)",
    # At most one direct successor per source draft (CAS / no fork).
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_qzone_drafts_supersedes_unique "
    "ON qzone_journal_drafts(supersedes_draft_id) "
    "WHERE supersedes_draft_id IS NOT NULL",
)


async def _apply_v5(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(qzone_journal_drafts)")
    try:
        existing = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    for name, definition in _V5_COLUMNS:
        if name in existing:
            continue
        await db.execute(
            f"ALTER TABLE qzone_journal_drafts ADD COLUMN {name} {definition}"
        )
    # Legacy / pre-v5 rows become revision 1 rooted at themselves.
    await db.execute(
        """
        UPDATE qzone_journal_drafts
        SET revision_root_id = draft_id,
            revision = 1,
            supersedes_draft_id = NULL
        WHERE revision_root_id IS NULL
           OR revision IS NULL
           OR revision < 1
        """
    )
    for statement in _CREATE_REVISION_INDEXES:
        await db.execute(statement)


async def _verify_v5(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute("PRAGMA table_info(qzone_journal_drafts)")
    try:
        columns = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    required = {name for name, _definition in _V5_COLUMNS}
    if not required <= columns:
        return False
    cursor = await db.execute(
        """
        SELECT COUNT(*) AS count
        FROM qzone_journal_drafts
        WHERE revision_root_id IS NULL
           OR revision IS NULL
           OR revision < 1
        """
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None or int(row["count"] or 0) != 0:
        return False
    cursor = await db.execute("PRAGMA index_list(qzone_journal_drafts)")
    try:
        indexes = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    return {
        "idx_qzone_drafts_revision_root",
        "idx_qzone_drafts_supersedes_unique",
    } <= indexes


async def _apply_v6(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(qzone_journal_drafts)")
    try:
        existing = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    if "approval_scope" not in existing:
        await db.execute(
            "ALTER TABLE qzone_journal_drafts "
            "ADD COLUMN approval_scope TEXT NOT NULL DEFAULT 'dry_run' "
            "CHECK (approval_scope IN ('dry_run', 'live'))"
        )


async def _verify_v6(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute("PRAGMA table_info(qzone_journal_drafts)")
    try:
        columns = {str(row["name"]): row for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    column = columns.get("approval_scope")
    if column is None:
        return False
    if str(column["dflt_value"] or "").strip("'") != "dry_run":
        return False
    cursor = await db.execute(
        "SELECT COUNT(*) AS count FROM qzone_journal_drafts "
        "WHERE approval_scope NOT IN ('dry_run', 'live')"
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return row is not None and int(row["count"] or 0) == 0


_MIGRATIONS = (
    Migration(
        version=1,
        name="create_qzone_journal_outbox",
        checksum=_MIGRATION_CHECKSUM,
        apply=_apply_v1,
        verify=_verify_v1,
    ),
    Migration(
        version=2,
        name="create_qzone_journal_manual_resolutions",
        checksum=_MIGRATION_V2_CHECKSUM,
        apply=_apply_v2,
        verify=_verify_v2,
    ),
    Migration(
        version=3,
        name="add_qzone_journal_review_provenance",
        checksum=_MIGRATION_V3_CHECKSUM,
        apply=_apply_v3,
        verify=_verify_v3,
    ),
    Migration(
        version=4,
        name="create_qzone_journal_review_decisions",
        checksum=_MIGRATION_V4_CHECKSUM,
        apply=_apply_v4,
        verify=_verify_v4,
    ),
    Migration(
        version=5,
        name="add_qzone_journal_draft_revisions",
        checksum=_MIGRATION_V5_CHECKSUM,
        apply=_apply_v5,
        verify=_verify_v5,
    ),
    Migration(
        version=6,
        name="add_qzone_journal_approval_scope",
        checksum=_MIGRATION_V6_CHECKSUM,
        apply=_apply_v6,
        verify=_verify_v6,
    ),
)


class JournalStore:
    """Own review, quota and dispatch state for QZone Journal."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_posts_per_day: int = 1,
        max_drafts_per_day: int | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._max_posts_per_day = max(1, int(max_posts_per_day))
        # None preserves direct-store seed fixtures (no day-draft budget).
        if max_drafts_per_day is None:
            self._max_drafts_per_day: int | None = None
        else:
            self._max_drafts_per_day = max(1, int(max_drafts_per_day))
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        runner = MigrationRunner(db_path=self._db_path, db_id=_DB_ID)
        await runner.ensure(_MIGRATIONS)
        self._db = await connect_sqlite(self._db_path, busy_timeout_ms=0)
        async with self._write_lock:
            await self._db.execute(
                """
                UPDATE qzone_journal_drafts
                SET status = 'unknown', last_error_code = 'restart_during_dispatch',
                    updated_at = ?
                WHERE status = 'dispatching'
                """,
                (_now_iso(),),
            )
            await self._db.commit()

    async def close(self) -> None:
        db = self._db
        self._db = None
        await close_with_checkpoint(db, name=_DB_ID)

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("JournalStore is not initialized")
        return self._db

    async def _begin_immediate(self, db: aiosqlite.Connection) -> None:
        while True:
            try:
                await db.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                await asyncio.sleep(0.02)

    async def _rollback(self, db: aiosqlite.Connection) -> None:
        task = asyncio.create_task(db.rollback())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task

    async def enqueue(
        self,
        *,
        dedupe_key: str,
        event_date: date,
        source: str,
        content: str,
        stable_id: str | None = None,
        subject_kind: str | None = None,
        privacy: str | None = None,
        salience: float | int | None = None,
        source_summary: str | None = None,
        provenance: Any | None = None,
        provenance_json: str | None = None,
    ) -> JournalDraft:
        key = str(dedupe_key or "").strip()
        source_name = str(source or "").strip()
        body = scrub_public_text(content)
        if not key or not source_name or not body:
            raise ValueError("dedupe_key, source and content are required")
        clean_stable_id = _sanitize_review_text(
            stable_id,
            max_len=_STABLE_ID_MAX_LEN,
            field="stable_id",
        )
        clean_subject_kind = _sanitize_subject_kind(subject_kind)
        clean_privacy = _sanitize_privacy(privacy)
        clean_salience = _sanitize_salience(salience)
        clean_source_summary = _sanitize_review_text(
            source_summary,
            max_len=_SOURCE_SUMMARY_MAX_LEN,
            field="source_summary",
        )
        if provenance is not None and provenance_json is not None:
            raise ValueError("provide only one of provenance or provenance_json")
        clean_provenance = _sanitize_provenance(
            provenance if provenance is not None else provenance_json
        )
        # Factual drafts require schema v2 provenance with public_projection meta.
        if clean_subject_kind == "factual":
            if clean_provenance is None:
                raise ValueError(
                    "factual subject_kind requires public_projection provenance"
                )
            try:
                prov_obj = json.loads(clean_provenance)
            except json.JSONDecodeError as exc:
                raise ValueError("factual provenance_json must be valid JSON") from exc
            if not isinstance(prov_obj, dict) or int(prov_obj.get("schema_version", 0)) != 2:
                raise ValueError(
                    "factual subject_kind requires provenance schema_version 2"
                )
            if not isinstance(prov_obj.get("public_projection"), dict):
                raise ValueError(
                    "factual subject_kind requires public_projection metadata"
                )
        db = self._require_db()
        async with self._write_lock:
            try:
                await self._begin_immediate(db)
                existing = await self._get_by_dedupe_key(db, key)
                if existing is not None:
                    await db.commit()
                    return existing
                # Atomic per-event-date draft budget (after same-dedupe, before insert).
                if self._max_drafts_per_day is not None:
                    occupied = await self._count_occupied_drafts_for_event_date(
                        db,
                        event_date,
                    )
                    if occupied >= self._max_drafts_per_day:
                        raise DayDraftBudgetExceededError(
                            "max_drafts_per_day exhausted for event_date "
                            f"{event_date.isoformat()}: occupied={occupied} "
                            f"budget={self._max_drafts_per_day}"
                        )
                now = _now_iso()
                new_draft_id = _draft_id(key)
                await db.execute(
                    """
                    INSERT INTO qzone_journal_drafts (
                        draft_id, dedupe_key, event_date, source, content,
                        status, created_at, updated_at,
                        stable_id, subject_kind, privacy, salience,
                        source_summary, provenance_json,
                        revision_root_id, revision, supersedes_draft_id
                    ) VALUES (
                        ?, ?, ?, ?, ?, 'pending_review', ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, 1, NULL
                    )
                    """,
                    (
                        new_draft_id,
                        key,
                        event_date.isoformat(),
                        source_name,
                        body,
                        now,
                        now,
                        clean_stable_id,
                        clean_subject_kind,
                        clean_privacy,
                        clean_salience,
                        clean_source_summary,
                        clean_provenance,
                        new_draft_id,
                    ),
                )
                await db.commit()
            except BaseException:
                await self._rollback(db)
                raise
        draft = await self.get(new_draft_id)
        if draft is None:
            raise RuntimeError("QZone draft insert did not produce a row")
        return draft

    async def create_revision(
        self,
        source_draft_id: str,
        *,
        content: str,
    ) -> JournalDraft:
        """Append a new pending_review revision superseding ``source_draft_id``.

        Eligible source statuses: pending_review, rejected (lineage tip only).
        CAS: at most one direct successor per source draft (unique supersedes).
        Day budget counts occupied lineage *tips* and is enforced with CAS in
        one txn. Source row is never mutated. source_summary/provenance are
        always inherited exactly from the source (no caller override surface).
        """
        body = scrub_public_text(content)
        if not body:
            raise ValueError("revision content is required")

        db = self._require_db()
        new_id: str | None = None
        async with self._write_lock:
            try:
                await self._begin_immediate(db)
                source = await self._get_by_id(db, str(source_draft_id))
                if source is None:
                    raise KeyError(source_draft_id)
                if source.status not in _RECOMPOSE_ELIGIBLE_STATUSES:
                    raise InvalidDraftTransitionError(
                        f"cannot recompose QZone draft from {source.status}"
                    )
                if not await self._is_lineage_tip(db, source.draft_id):
                    raise InvalidDraftTransitionError(
                        "source draft already has a successor revision"
                    )

                verified_summary = scrub_public_text(source.source_summary or "")
                if not verified_summary:
                    raise ValueError(
                        "source draft has no verified source_summary for recompose"
                    )
                # Factual body must equal inherited verified projected summary.
                if source.subject_kind == "factual" and body != verified_summary:
                    raise ValueError(
                        "factual revision content must equal verified "
                        "projected source_summary"
                    )

                if self._max_drafts_per_day is not None:
                    occupied = await self._count_occupied_drafts_for_event_date(
                        db,
                        source.event_date,
                    )
                    # Tip-only occupancy: a pending tip already holds one slot
                    # for this lineage (replacement is free). A rejected tip
                    # does not hold a slot, so recompose needs free budget.
                    source_already_occupied = source.status in (
                        "pending_review",
                        "approved",
                        "dispatching",
                        "unknown",
                        "published",
                    )
                    if (
                        occupied >= self._max_drafts_per_day
                        and not source_already_occupied
                    ):
                        raise DayDraftBudgetExceededError(
                            "max_drafts_per_day exhausted for event_date "
                            f"{source.event_date.isoformat()}: occupied={occupied} "
                            f"budget={self._max_drafts_per_day}"
                        )

                root_id = source.revision_root_id or source.draft_id
                next_revision = int(source.revision or 1) + 1
                rev_key = _revision_dedupe_key(
                    root_id=root_id,
                    revision=next_revision,
                    supersedes_id=source.draft_id,
                )
                # Collision should be impossible with unique supersedes; still guard.
                if await self._get_by_dedupe_key(db, rev_key) is not None:
                    raise InvalidDraftTransitionError(
                        "revision dedupe key collision for successor"
                    )
                new_id = _draft_id(rev_key)

                # Always inherit verified summary + provenance from source.
                inherited_summary = source.source_summary
                clean_provenance = source.provenance_json
                if source.subject_kind == "factual":
                    if clean_provenance is None:
                        raise ValueError(
                            "factual subject_kind requires public_projection provenance"
                        )
                    try:
                        prov_obj = json.loads(clean_provenance)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            "factual provenance_json must be valid JSON"
                        ) from exc
                    if (
                        not isinstance(prov_obj, dict)
                        or int(prov_obj.get("schema_version", 0)) != 2
                    ):
                        raise ValueError(
                            "factual subject_kind requires provenance schema_version 2"
                        )
                    if not isinstance(prov_obj.get("public_projection"), dict):
                        raise ValueError(
                            "factual subject_kind requires public_projection metadata"
                        )

                now = _now_iso()
                try:
                    await db.execute(
                        """
                        INSERT INTO qzone_journal_drafts (
                            draft_id, dedupe_key, event_date, source, content,
                            status, publish_date, external_post_id, last_error_code,
                            created_at, updated_at,
                            stable_id, subject_kind, privacy, salience,
                            source_summary, provenance_json,
                            revision_root_id, revision, supersedes_draft_id
                        ) VALUES (
                            ?, ?, ?, ?, ?, 'pending_review', NULL, NULL, NULL,
                            ?, ?,
                            ?, ?, ?, ?,
                            ?, ?,
                            ?, ?, ?
                        )
                        """,
                        (
                            new_id,
                            rev_key,
                            source.event_date.isoformat(),
                            source.source,
                            body,
                            now,
                            now,
                            source.stable_id,
                            source.subject_kind,
                            source.privacy,
                            source.salience,
                            inherited_summary,
                            clean_provenance,
                            root_id,
                            next_revision,
                            source.draft_id,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    # Multi-process race: only successor UNIQUE → domain 409.
                    # Other integrity/BUSY/cancel/unknown continue to propagate.
                    if _is_supersedes_unique_conflict(exc):
                        raise InvalidDraftTransitionError(
                            "source draft already has a successor revision"
                        ) from exc
                    raise
                await db.commit()
            except BaseException:
                await self._rollback(db)
                raise
        if new_id is None:
            raise RuntimeError("QZone revision insert did not produce an id")
        draft = await self.get(new_id)
        if draft is None:
            raise RuntimeError("QZone revision insert did not produce a row")
        return draft

    # Alias used by some call sites / tests.
    recompose = create_revision

    async def list_revisions(self, draft_id: str) -> list[JournalDraft]:
        """Return full lineage for the revision root of ``draft_id``, ascending."""
        db = self._require_db()
        current = await self._get_by_id(db, str(draft_id))
        if current is None:
            raise KeyError(draft_id)
        root_id = current.revision_root_id or current.draft_id
        cursor = await db.execute(
            """
            SELECT * FROM qzone_journal_drafts
            WHERE revision_root_id = ? OR draft_id = ?
            ORDER BY revision ASC, created_at ASC, draft_id ASC
            """,
            (root_id, root_id),
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [_row_to_draft(row) for row in rows]

    async def approve(
        self,
        draft_id: str,
        *,
        note: str | None = None,
        approval_scope: str = "dry_run",
    ) -> JournalDraft:
        clean_note = _sanitize_optional_operator_note(note)
        clean_scope = _sanitize_approval_scope(approval_scope)
        db = self._require_db()
        async with self._write_lock:
            try:
                await self._begin_immediate(db)
                current = await self._get_by_id(db, str(draft_id))
                if current is None:
                    raise KeyError(draft_id)
                if current.status == "approved":
                    # Idempotent replay only while this row is still the tip.
                    if not await self._is_lineage_tip(db, current.draft_id):
                        raise InvalidDraftTransitionError(
                            "cannot approve QZone draft: not lineage tip"
                        )
                    if current.approval_scope != clean_scope:
                        raise InvalidDraftTransitionError(
                            "cannot change approval_scope on an approved QZone draft"
                        )
                    await db.commit()
                    return current
                if current.status != "pending_review":
                    raise InvalidDraftTransitionError(
                        f"cannot approve QZone draft from {current.status}"
                    )
                if not await self._is_lineage_tip(db, current.draft_id):
                    raise InvalidDraftTransitionError(
                        "cannot approve QZone draft: not lineage tip"
                    )
                now = _now_iso()
                await db.execute(
                    """
                    UPDATE qzone_journal_drafts
                    SET status = 'approved', approval_scope = ?, updated_at = ?
                    WHERE draft_id = ? AND status = 'pending_review'
                    """,
                    (clean_scope, now, str(draft_id)),
                )
                await db.execute(
                    """
                    INSERT INTO qzone_journal_review_decisions (
                        draft_id, decision, note, previous_status, new_status,
                        created_at
                    ) VALUES (?, 'approve', ?, 'pending_review', 'approved', ?)
                    """,
                    (str(draft_id), clean_note, now),
                )
                await db.commit()
            except BaseException:
                await self._rollback(db)
                raise
        draft = await self.get(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        return draft

    async def claim_for_publish(
        self,
        draft_id: str,
        *,
        now: datetime | None = None,
    ) -> JournalDraft | None:
        local_now = (now or datetime.now(_CST)).astimezone(_CST)
        publish_date = local_now.date().isoformat()
        db = self._require_db()
        async with self._write_lock:
            try:
                await self._begin_immediate(db)
                current = await self._get_by_id(db, str(draft_id))
                if (
                    current is None
                    or current.status != "approved"
                    or not await self._is_lineage_tip(db, current.draft_id)
                ):
                    await db.commit()
                    return None
                cursor = await db.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM qzone_journal_drafts
                    WHERE publish_date = ?
                      AND status IN ('dispatching', 'published', 'unknown')
                    """,
                    (publish_date,),
                )
                try:
                    row = await cursor.fetchone()
                finally:
                    await cursor.close()
                used = int(row["count"] or 0) if row is not None else 0
                if used >= self._max_posts_per_day:
                    await db.commit()
                    return None
                await db.execute(
                    """
                    UPDATE qzone_journal_drafts
                    SET status = 'dispatching', publish_date = ?, updated_at = ?
                    WHERE draft_id = ? AND status = 'approved'
                    """,
                    (publish_date, _now_iso(), str(draft_id)),
                )
                await db.commit()
            except BaseException:
                await self._rollback(db)
                raise
        return await self.get(draft_id)

    async def mark_published(
        self,
        draft_id: str,
        *,
        external_post_id: str,
    ) -> JournalDraft:
        # Same sanitize contract as confirm_published; required non-empty id.
        clean_external = _sanitize_external_post_id(external_post_id)
        if clean_external is None:
            raise ValueError("external_post_id is required")
        db = self._require_db()
        async with self._write_lock:
            try:
                await self._begin_immediate(db)
                current = await self._get_by_id(db, str(draft_id))
                if current is None:
                    raise KeyError(draft_id)
                if current.status != "dispatching":
                    raise InvalidDraftTransitionError(
                        f"cannot mark QZone draft published from {current.status}"
                    )
                await db.execute(
                    """
                    UPDATE qzone_journal_drafts
                    SET status = 'published', external_post_id = ?,
                        last_error_code = NULL, updated_at = ?
                    WHERE draft_id = ? AND status = 'dispatching'
                    """,
                    (clean_external, _now_iso(), str(draft_id)),
                )
                await db.commit()
            except BaseException:
                await self._rollback(db)
                raise
        draft = await self.get(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        return draft

    async def mark_unknown(self, draft_id: str, *, reason: str) -> JournalDraft:
        draft = await self._transition_from_dispatching(
            draft_id,
            status="unknown",
            error_code=reason,
        )
        if draft is None:
            raise KeyError(draft_id)
        return draft

    async def confirm_published(
        self,
        draft_id: str,
        *,
        note: str,
        external_post_id: str | None = None,
    ) -> JournalDraft:
        clean_note = _sanitize_note(note)
        clean_external = _sanitize_external_post_id(external_post_id)
        db = self._require_db()
        async with self._write_lock:
            try:
                await self._begin_immediate(db)
                current = await self._get_by_id(db, str(draft_id))
                if current is None:
                    raise KeyError(draft_id)
                # Tip CAS before idempotent shortcuts, mutation, or audit insert.
                if not await self._is_lineage_tip(db, current.draft_id):
                    raise InvalidDraftTransitionError(
                        "cannot confirm_published QZone draft: not lineage tip"
                    )
                if current.status == "published":
                    prior = await self._get_latest_manual_resolution(
                        db,
                        draft_id=str(draft_id),
                        decision="confirm_published",
                    )
                    if (
                        prior is None
                        or prior["note"] != clean_note
                        or prior["external_post_id"] != clean_external
                    ):
                        raise InvalidDraftTransitionError(
                            "cannot confirm_published QZone draft from published "
                            "without a matching manual resolution"
                        )
                    await db.commit()
                    return current
                if current.status != "unknown":
                    raise InvalidDraftTransitionError(
                        f"cannot confirm_published QZone draft from {current.status}"
                    )
                now = _now_iso()
                await db.execute(
                    """
                    UPDATE qzone_journal_drafts
                    SET status = 'published',
                        external_post_id = COALESCE(?, external_post_id),
                        last_error_code = NULL,
                        updated_at = ?
                    WHERE draft_id = ? AND status = 'unknown'
                    """,
                    (clean_external, now, str(draft_id)),
                )
                await db.execute(
                    """
                    INSERT INTO qzone_journal_manual_resolutions (
                        draft_id, decision, note, previous_status, new_status,
                        external_post_id, created_at
                    ) VALUES (?, 'confirm_published', ?, 'unknown', 'published', ?, ?)
                    """,
                    (str(draft_id), clean_note, clean_external, now),
                )
                await db.commit()
            except BaseException:
                await self._rollback(db)
                raise
        draft = await self.get(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        return draft

    async def confirm_not_published(
        self,
        draft_id: str,
        *,
        note: str,
    ) -> JournalDraft:
        clean_note = _sanitize_note(note)
        db = self._require_db()
        async with self._write_lock:
            try:
                await self._begin_immediate(db)
                current = await self._get_by_id(db, str(draft_id))
                if current is None:
                    raise KeyError(draft_id)
                # Tip CAS before idempotent shortcuts, mutation, or audit insert.
                if not await self._is_lineage_tip(db, current.draft_id):
                    raise InvalidDraftTransitionError(
                        "cannot confirm_not_published QZone draft: not lineage tip"
                    )
                if current.status == "approved":
                    prior = await self._get_latest_manual_resolution(
                        db,
                        draft_id=str(draft_id),
                        decision="confirm_not_published",
                    )
                    if (
                        prior is not None
                        and prior["note"] == clean_note
                        and current.publish_date is None
                        and current.external_post_id is None
                    ):
                        await db.commit()
                        return current
                    raise InvalidDraftTransitionError(
                        "cannot confirm_not_published QZone draft from approved "
                        "without a matching manual resolution"
                    )
                if current.status != "unknown":
                    raise InvalidDraftTransitionError(
                        f"cannot confirm_not_published QZone draft from {current.status}"
                    )
                now = _now_iso()
                await db.execute(
                    """
                    UPDATE qzone_journal_drafts
                    SET status = 'approved',
                        publish_date = NULL,
                        external_post_id = NULL,
                        last_error_code = NULL,
                        updated_at = ?
                    WHERE draft_id = ? AND status = 'unknown'
                    """,
                    (now, str(draft_id)),
                )
                await db.execute(
                    """
                    INSERT INTO qzone_journal_manual_resolutions (
                        draft_id, decision, note, previous_status, new_status,
                        external_post_id, created_at
                    ) VALUES (?, 'confirm_not_published', ?, 'unknown', 'approved', NULL, ?)
                    """,
                    (str(draft_id), clean_note, now),
                )
                await db.commit()
            except BaseException:
                await self._rollback(db)
                raise
        draft = await self.get(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        return draft

    async def list_manual_resolutions(
        self,
        *,
        draft_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        db = self._require_db()
        safe_limit = max(1, min(500, int(limit)))
        if draft_id is None:
            cursor = await db.execute(
                """
                SELECT * FROM qzone_journal_manual_resolutions
                ORDER BY resolution_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            )
        else:
            cursor = await db.execute(
                """
                SELECT * FROM qzone_journal_manual_resolutions
                WHERE draft_id = ?
                ORDER BY resolution_id DESC
                LIMIT ?
                """,
                (str(draft_id), safe_limit),
            )
        try:
            return [_row_to_resolution(row) for row in await cursor.fetchall()]
        finally:
            await cursor.close()

    async def _get_latest_manual_resolution(
        self,
        db: aiosqlite.Connection,
        *,
        draft_id: str,
        decision: str,
    ) -> dict[str, Any] | None:
        cursor = await db.execute(
            """
            SELECT * FROM qzone_journal_manual_resolutions
            WHERE draft_id = ? AND decision = ?
            ORDER BY resolution_id DESC
            LIMIT 1
            """,
            (draft_id, decision),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return _row_to_resolution(row) if row is not None else None

    async def reject(
        self,
        draft_id: str,
        *,
        reason: str,
        note: str | None = None,
    ) -> JournalDraft:
        # Retain reason-code semantics on the draft; optional store note is
        # scrubbed. Admin layer requires a non-empty note before calling.
        clean_note = _sanitize_optional_operator_note(note)
        error_code = str(reason or "review_rejected")[:180]
        db = self._require_db()
        async with self._write_lock:
            try:
                await self._begin_immediate(db)
                current = await self._get_by_id(db, str(draft_id))
                if current is None:
                    raise KeyError(draft_id)
                if current.status == "rejected":
                    if not await self._is_lineage_tip(db, current.draft_id):
                        raise InvalidDraftTransitionError(
                            "cannot reject QZone draft: not lineage tip"
                        )
                    await db.commit()
                    return current
                if current.status != "pending_review":
                    raise InvalidDraftTransitionError(
                        f"cannot reject QZone draft from {current.status}"
                    )
                if not await self._is_lineage_tip(db, current.draft_id):
                    raise InvalidDraftTransitionError(
                        "cannot reject QZone draft: not lineage tip"
                    )
                now = _now_iso()
                await db.execute(
                    """
                    UPDATE qzone_journal_drafts
                    SET status = 'rejected', last_error_code = ?, updated_at = ?
                    WHERE draft_id = ? AND status = 'pending_review'
                    """,
                    (error_code, now, str(draft_id)),
                )
                await db.execute(
                    """
                    INSERT INTO qzone_journal_review_decisions (
                        draft_id, decision, note, previous_status, new_status,
                        created_at
                    ) VALUES (?, 'reject', ?, 'pending_review', 'rejected', ?)
                    """,
                    (str(draft_id), clean_note, now),
                )
                await db.commit()
            except BaseException:
                await self._rollback(db)
                raise
        draft = await self.get(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        return draft

    async def list(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_superseded: bool = False,
    ) -> list[JournalDraft]:
        """List drafts for operational queues (lineage tips by default).

        Non-tip historical rows are excluded unless ``include_superseded=True``
        (physical-row / full-history escape for internal tests). Admin list,
        delivery, and pagination totals use the tip-only default so pending r1
        + pending r2 reports one current item (r2).
        """
        db = self._require_db()
        safe_limit = max(1, min(500, int(limit)))
        safe_offset = max(0, int(offset))
        tip_predicate = (
            ""
            if include_superseded
            else """
              AND NOT EXISTS (
                  SELECT 1
                  FROM qzone_journal_drafts AS succ
                  WHERE succ.supersedes_draft_id = d.draft_id
              )
            """
        )
        if status is not None:
            status_key = str(status).strip()
            if status_key not in _ALLOWED_DRAFT_STATUSES:
                raise ValueError(f"unsupported draft status filter: {status_key}")
            cursor = await db.execute(
                f"""
                SELECT d.* FROM qzone_journal_drafts AS d
                WHERE d.status = ?
                {tip_predicate}
                ORDER BY d.created_at DESC, d.draft_id DESC
                LIMIT ? OFFSET ?
                """,
                (status_key, safe_limit, safe_offset),
            )
        else:
            cursor = await db.execute(
                f"""
                SELECT d.* FROM qzone_journal_drafts AS d
                WHERE 1 = 1
                {tip_predicate}
                ORDER BY d.created_at DESC, d.draft_id DESC
                LIMIT ? OFFSET ?
                """,
                (safe_limit, safe_offset),
            )
        try:
            return [_row_to_draft(row) for row in await cursor.fetchall()]
        finally:
            await cursor.close()

    async def count(
        self,
        *,
        status: str | None = None,
        include_superseded: bool = False,
    ) -> int:
        """Count drafts for operational surfaces (lineage tips by default).

        See :meth:`list` for ``include_superseded`` semantics. Pagination
        ``total`` must follow tips so Admin pages stay consistent with rows.
        """
        db = self._require_db()
        tip_predicate = (
            ""
            if include_superseded
            else """
              AND NOT EXISTS (
                  SELECT 1
                  FROM qzone_journal_drafts AS succ
                  WHERE succ.supersedes_draft_id = d.draft_id
              )
            """
        )
        if status is not None:
            status_key = str(status).strip()
            if status_key not in _ALLOWED_DRAFT_STATUSES:
                raise ValueError(f"unsupported draft status filter: {status_key}")
            cursor = await db.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM qzone_journal_drafts AS d
                WHERE d.status = ?
                {tip_predicate}
                """,
                (status_key,),
            )
        else:
            cursor = await db.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM qzone_journal_drafts AS d
                WHERE 1 = 1
                {tip_predicate}
                """
            )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return int(row["count"] or 0) if row is not None else 0

    async def list_review_decisions(
        self,
        *,
        draft_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        db = self._require_db()
        safe_limit = max(1, min(500, int(limit)))
        if draft_id is None:
            cursor = await db.execute(
                """
                SELECT * FROM qzone_journal_review_decisions
                ORDER BY decision_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            )
        else:
            cursor = await db.execute(
                """
                SELECT * FROM qzone_journal_review_decisions
                WHERE draft_id = ?
                ORDER BY decision_id DESC
                LIMIT ?
                """,
                (str(draft_id), safe_limit),
            )
        try:
            return [_row_to_review_decision(row) for row in await cursor.fetchall()]
        finally:
            await cursor.close()

    async def get_by_dedupe_key(self, dedupe_key: str) -> JournalDraft | None:
        return await self._get_by_dedupe_key(
            self._require_db(),
            str(dedupe_key),
        )

    async def get(self, draft_id: str) -> JournalDraft | None:
        return await self._get_by_id(self._require_db(), str(draft_id))

    async def is_lineage_tip(self, draft_id: str) -> bool:
        """Return whether ``draft_id`` is the current lineage tip.

        Store-owned SQL only. Known tip → True, known non-tip → False.
        Unknown id raises ``KeyError`` (callers map to 404).
        """
        db = self._require_db()
        current = await self._get_by_id(db, str(draft_id))
        if current is None:
            raise KeyError(draft_id)
        return await self._is_lineage_tip(db, current.draft_id)

    async def count_occupied_drafts_for_event_date(self, event_date: date) -> int:
        """Count lineage-tip rows that occupy the per-event-day draft budget.

        Occupied tip statuses: pending_review, approved, dispatching, unknown,
        published. rejected and failed tips do not occupy the budget. Historical
        immutable rows are ignored: only tips (no successor via
        ``supersedes_draft_id``) participate. Unique supersedes ⇒ one tip per
        lineage, so COUNT(*) of tips equals logical event occupancy.
        """
        return await self._count_occupied_drafts_for_event_date(
            self._require_db(),
            event_date,
        )

    async def _is_lineage_tip(
        self,
        db: aiosqlite.Connection,
        draft_id: str,
    ) -> bool:
        """True when no row has supersedes_draft_id pointing at ``draft_id``."""
        cursor = await db.execute(
            """
            SELECT 1 FROM qzone_journal_drafts
            WHERE supersedes_draft_id = ?
            LIMIT 1
            """,
            (str(draft_id),),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return row is None

    async def _count_occupied_drafts_for_event_date(
        self,
        db: aiosqlite.Connection,
        event_date: date,
    ) -> int:
        day = event_date.isoformat() if isinstance(event_date, date) else str(event_date)
        # Tip-only: historical occupied statuses on superseded rows must not
        # hold the day budget after the lineage tip becomes free (e.g. rejected).
        cursor = await db.execute(
            """
            SELECT COUNT(*) AS count
            FROM qzone_journal_drafts AS d
            WHERE d.event_date = ?
              AND d.status IN (
                  'pending_review', 'approved', 'dispatching',
                  'unknown', 'published'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM qzone_journal_drafts AS succ
                  WHERE succ.supersedes_draft_id = d.draft_id
              )
            """,
            (day,),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return int(row["count"] or 0) if row is not None else 0

    async def list_recent_source_summaries(
        self,
        *,
        limit: int = 32,
    ) -> list[str]:
        """Return recent tip-only non-empty source_summary values for novelty.

        Multiple revisions of one lineage share inherited source_summary; only
        the lineage tip is returned so one logical event cannot crowd others.
        """
        safe_limit = max(0, min(200, int(limit)))
        if safe_limit == 0:
            return []
        cursor = await self._require_db().execute(
            """
            SELECT d.source_summary
            FROM qzone_journal_drafts AS d
            WHERE d.source_summary IS NOT NULL
              AND TRIM(d.source_summary) != ''
              AND NOT EXISTS (
                  SELECT 1
                  FROM qzone_journal_drafts AS succ
                  WHERE succ.supersedes_draft_id = d.draft_id
              )
            ORDER BY d.created_at DESC, d.draft_id DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        summaries: list[str] = []
        for row in rows:
            text = str(row["source_summary"] or "").strip()
            if text:
                summaries.append(text)
        return summaries

    async def stats(self, *, include_superseded: bool = False) -> dict[str, Any]:
        """Status histogram for operational surfaces (tips by default).

        Pass ``include_superseded=True`` for physical-row totals used by
        internal tests / full history audits. Admin health uses the default.
        """
        if include_superseded:
            cursor = await self._require_db().execute(
                """
                SELECT status, COUNT(*) AS count
                FROM qzone_journal_drafts
                GROUP BY status
                """
            )
        else:
            cursor = await self._require_db().execute(
                """
                SELECT d.status AS status, COUNT(*) AS count
                FROM qzone_journal_drafts AS d
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM qzone_journal_drafts AS succ
                    WHERE succ.supersedes_draft_id = d.draft_id
                )
                GROUP BY d.status
                """
            )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        by_status = {status: 0 for status in _ALLOWED_DRAFT_STATUSES}
        for row in rows:
            key = str(row["status"])
            by_status[key] = int(row["count"] or 0)
        return {"total": sum(by_status.values()), "by_status": by_status}

    async def _transition_from_dispatching(
        self,
        draft_id: str,
        *,
        status: str,
        error_code: str,
    ) -> JournalDraft | None:
        db = self._require_db()
        async with self._write_lock:
            try:
                await self._begin_immediate(db)
                await db.execute(
                    """
                    UPDATE qzone_journal_drafts
                    SET status = ?, last_error_code = ?, updated_at = ?
                    WHERE draft_id = ? AND status = 'dispatching'
                    """,
                    (
                        status,
                        " ".join(str(error_code or status).split())[:180],
                        _now_iso(),
                        str(draft_id),
                    ),
                )
                await db.commit()
            except BaseException:
                await self._rollback(db)
                raise
        return await self.get(draft_id)

    async def _get_by_id(
        self,
        db: aiosqlite.Connection,
        draft_id: str,
    ) -> JournalDraft | None:
        cursor = await db.execute(
            "SELECT * FROM qzone_journal_drafts WHERE draft_id = ?",
            (draft_id,),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return _row_to_draft(row) if row is not None else None

    async def _get_by_dedupe_key(
        self,
        db: aiosqlite.Connection,
        dedupe_key: str,
    ) -> JournalDraft | None:
        cursor = await db.execute(
            "SELECT * FROM qzone_journal_drafts WHERE dedupe_key = ?",
            (dedupe_key,),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return _row_to_draft(row) if row is not None else None


__all__ = [
    "DayDraftBudgetExceededError",
    "InvalidDraftTransitionError",
    "JournalDraft",
    "JournalStore",
    "validate_review_fields_for_compose",
]
