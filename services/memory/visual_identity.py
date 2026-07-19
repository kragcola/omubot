"""Durable exact visual-identity store (full SHA-256).

Stores explicit user corrections of image identity (e.g. "this image is 高松灯")
keyed by the full normalized SHA-256 of the image bytes. Visibility is
private | same_group | global and fails closed across user/group boundaries.

Perceptual / near-image lookup is intentionally deferred.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from services.memory.visibility import normalize_visibility
from services.storage import close_with_checkpoint, connect_sqlite

_L = logger.bind(channel="debug")

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VALID_VISIBILITIES = frozenset(("private", "same_group", "global"))

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS visual_identities (
    image_sha256       TEXT NOT NULL,
    entity_label       TEXT NOT NULL,
    correcting_user_id TEXT NOT NULL,
    scope_key          TEXT NOT NULL,
    origin_group_id    TEXT,
    visibility         TEXT NOT NULL,
    source_message_id  TEXT,
    provenance         TEXT NOT NULL DEFAULT 'user_correction',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (image_sha256, correcting_user_id, scope_key)
)
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_visual_id_sha "
    "ON visual_identities(image_sha256)",
    "CREATE INDEX IF NOT EXISTS idx_visual_id_group "
    "ON visual_identities(origin_group_id, visibility)",
)

_EXPECTED_PRIMARY_KEY = {
    "image_sha256": 1,
    "correcting_user_id": 2,
    "scope_key": 3,
}
_LEGACY_TABLE = "visual_identities_scope_migration"


def normalize_image_sha256(value: str | None) -> str | None:
    """Normalize to lowercase 64-char hex SHA-256, or None if invalid."""
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw.startswith("sha256:"):
        raw = raw[7:].strip()
    if raw.startswith("0x"):
        raw = raw[2:].strip()
    # Accept short-hash display forms only when full hash is provided elsewhere;
    # this store requires the full digest.
    if not _SHA256_RE.fullmatch(raw):
        return None
    return raw


def _require_authorized_correction_context(
    *,
    image_sha256: str,
    provenance: str,
    visual_evidence: dict[str, Any] | None,
    trigger: dict[str, Any] | None,
) -> None:
    """Require the explicit single-image human-correction write contract."""
    evidence_sha = (
        normalize_image_sha256(str(visual_evidence.get("image_sha256") or ""))
        if isinstance(visual_evidence, dict)
        else None
    )
    requested_sha = normalize_image_sha256(image_sha256)
    authorized = (
        isinstance(visual_evidence, dict)
        and str(visual_evidence.get("provenance") or "").strip()
        == "visual_system"
        and evidence_sha is not None
        and evidence_sha == requested_sha
        and isinstance(trigger, dict)
        and str(trigger.get("mode") or "").strip() == "correction"
        and trigger.get("evidence_count") == 1
        and str(provenance or "").strip() == "user_correction"
    )
    if not authorized:
        raise ValueError("authorized correction context is required")


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


@dataclass(slots=True)
class VisualIdentityRecord:
    image_sha256: str
    entity_label: str
    correcting_user_id: str
    scope_key: str
    origin_group_id: str | None
    visibility: str
    source_message_id: str | None
    provenance: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class NewVisualIdentity:
    image_sha256: str
    entity_label: str
    correcting_user_id: str
    origin_group_id: str | None = None
    visibility: str | None = None
    source_message_id: str | None = None
    provenance: str = "user_correction"

    def normalized(self) -> NewVisualIdentity:
        sha = normalize_image_sha256(self.image_sha256)
        if sha is None:
            raise ValueError(
                f"image_sha256 must be full 64-char hex SHA-256, got {self.image_sha256!r}"
            )
        label = str(self.entity_label or "").strip()
        if not label:
            raise ValueError("entity_label is required")
        user = str(self.correcting_user_id or "").strip()
        if not user:
            raise ValueError("correcting_user_id is required")
        origin = (
            str(self.origin_group_id).strip()
            if self.origin_group_id is not None
            else None
        )
        if origin == "":
            origin = None
        vis_raw = self.visibility
        if vis_raw is None:
            visibility = "same_group" if origin else "private"
        else:
            visibility = normalize_visibility(str(vis_raw)) or ""
            if visibility not in _VALID_VISIBILITIES:
                raise ValueError(
                    f"Invalid visibility: {self.visibility!r}, "
                    f"expected one of {sorted(_VALID_VISIBILITIES)}"
                )
        src = (
            str(self.source_message_id).strip()
            if self.source_message_id is not None
            else None
        )
        if src == "":
            src = None
        prov = str(self.provenance or "user_correction").strip() or "user_correction"
        return NewVisualIdentity(
            image_sha256=sha,
            entity_label=label,
            correcting_user_id=user,
            origin_group_id=origin,
            visibility=visibility,
            source_message_id=src,
            provenance=prov,
        )


def _row_to_record(row: Any) -> VisualIdentityRecord:
    keys = row.keys() if hasattr(row, "keys") else ()
    return VisualIdentityRecord(
        image_sha256=str(row["image_sha256"]),
        entity_label=str(row["entity_label"]),
        correcting_user_id=str(row["correcting_user_id"]),
        scope_key=str(row["scope_key"]),
        origin_group_id=row["origin_group_id"] if "origin_group_id" in keys else None,
        visibility=str(row["visibility"]),
        source_message_id=(
            row["source_message_id"] if "source_message_id" in keys else None
        ),
        provenance=str(row["provenance"] if "provenance" in keys else "user_correction"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _scope_key(*, visibility: str, origin_group_id: str | None) -> str:
    if visibility == "same_group":
        origin = str(origin_group_id or "").strip()
        if not origin:
            raise ValueError("same_group visual identity requires origin_group_id")
        return f"group:{origin}"
    if visibility == "private":
        return "private"
    if visibility == "global":
        return "global"
    raise ValueError(f"unsupported visual identity visibility: {visibility!r}")


def visual_identity_visible(
    record: VisualIdentityRecord | Any,
    *,
    current_user_id: str,
    current_group_id: str | None,
) -> bool:
    """Fail-closed visibility for exact visual-identity recall."""
    user = str(current_user_id or "").strip()
    if not user:
        return False
    correcting = str(getattr(record, "correcting_user_id", "") or "").strip()
    if correcting != user:
        return False

    visibility = normalize_visibility(getattr(record, "visibility", None))
    if visibility is None:
        return False
    if visibility == "private":
        # Private corrections only surface in private chat (no group context).
        return current_group_id is None or not str(current_group_id).strip()
    if visibility == "global":
        return True
    # same_group: require matching origin group when the caller is in a group.
    group = str(current_group_id).strip() if current_group_id else ""
    if not group:
        # Private chat does not inherit same_group visual corrections.
        return False
    origin = getattr(record, "origin_group_id", None)
    if origin is None or not str(origin).strip():
        return False
    return str(origin).strip() == group


class VisualIdentityStore:
    """SQLite-backed exact visual-identity map (full SHA-256 keys)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: Any = None
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        self._db = await connect_sqlite(self._db_path)
        try:
            await self._ensure_schema()
            for idx in _CREATE_INDEXES:
                await self._db.execute(idx)
            await self._db.commit()
        except BaseException:
            await self._db.close()
            self._db = None
            raise

    async def _ensure_schema(self) -> None:
        db = self._require_db()
        cursor = await db.execute("PRAGMA table_info(visual_identities)")
        try:
            rows = list(await cursor.fetchall())
        finally:
            await cursor.close()
        if not rows:
            await db.execute(_CREATE_TABLE)
            return
        primary_key = {
            str(row["name"]): int(row["pk"])
            for row in rows
            if int(row["pk"]) > 0
        }
        if primary_key == _EXPECTED_PRIMARY_KEY:
            return
        await self._migrate_legacy_schema()

    async def _migrate_legacy_schema(self) -> None:
        """Atomically add scope_key to pre-scope development schemas."""
        db = self._require_db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(f"DROP TABLE IF EXISTS {_LEGACY_TABLE}")
            await db.execute(
                f"ALTER TABLE visual_identities RENAME TO {_LEGACY_TABLE}"
            )
            await db.execute(_CREATE_TABLE)
            cursor = await db.execute(f"SELECT * FROM {_LEGACY_TABLE}")
            try:
                rows = list(await cursor.fetchall())
            finally:
                await cursor.close()
            for index, row in enumerate(rows):
                keys = set(row.keys()) if hasattr(row, "keys") else set()
                visibility = str(row["visibility"] or "")
                origin_group_id = (
                    row["origin_group_id"]
                    if "origin_group_id" in keys
                    else None
                )
                legacy_scope_key = (
                    str(row["scope_key"] or "").strip()
                    if "scope_key" in keys
                    else ""
                )
                if legacy_scope_key:
                    scope_key = legacy_scope_key
                else:
                    try:
                        scope_key = _scope_key(
                            visibility=visibility,
                            origin_group_id=origin_group_id,
                        )
                    except ValueError:
                        # Preserve malformed legacy rows but keep them outside
                        # every public lookup scope (fail-closed quarantine).
                        scope_key = f"quarantine:{index}"
                await db.execute(
                    "INSERT OR REPLACE INTO visual_identities ("
                    "image_sha256, entity_label, correcting_user_id, scope_key, "
                    "origin_group_id, visibility, source_message_id, provenance, "
                    "created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(row["image_sha256"]),
                        str(row["entity_label"]),
                        str(row["correcting_user_id"]),
                        scope_key,
                        origin_group_id,
                        visibility,
                        row["source_message_id"]
                        if "source_message_id" in keys
                        else None,
                        str(
                            row["provenance"]
                            if "provenance" in keys
                            else "user_correction"
                        ),
                        str(row["created_at"]),
                        str(row["updated_at"]),
                    ),
                )
            await db.execute(f"DROP TABLE {_LEGACY_TABLE}")
            await db.commit()
            _L.info(
                "visual identity schema migrated | rows={} primary_key=scope_key",
                len(rows),
            )
        except BaseException:
            await db.rollback()
            raise

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db)
            self._db = None

    def _require_db(self) -> Any:
        if self._db is None:
            raise RuntimeError("VisualIdentityStore not initialized")
        return self._db

    async def upsert(self, record: NewVisualIdentity) -> VisualIdentityRecord:
        """Insert or update one scoped visual correction."""
        n = record.normalized()
        scope_key = _scope_key(
            visibility=str(n.visibility),
            origin_group_id=n.origin_group_id,
        )
        now = _now_iso()
        async with self._write_lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT created_at FROM visual_identities "
                "WHERE image_sha256 = ? AND correcting_user_id = ? AND scope_key = ?",
                (n.image_sha256, n.correcting_user_id, scope_key),
            )
            existing = await cursor.fetchone()
            created_at = (
                str(existing["created_at"]) if existing is not None else now
            )
            await db.execute(
                "INSERT INTO visual_identities ("
                "image_sha256, entity_label, correcting_user_id, scope_key, origin_group_id, "
                "visibility, source_message_id, provenance, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(image_sha256, correcting_user_id, scope_key) DO UPDATE SET "
                "entity_label = excluded.entity_label, "
                "origin_group_id = excluded.origin_group_id, "
                "visibility = excluded.visibility, "
                "source_message_id = excluded.source_message_id, "
                "provenance = excluded.provenance, "
                "updated_at = excluded.updated_at",
                (
                    n.image_sha256,
                    n.entity_label,
                    n.correcting_user_id,
                    scope_key,
                    n.origin_group_id,
                    n.visibility,
                    n.source_message_id,
                    n.provenance,
                    created_at,
                    now,
                ),
            )
            await db.commit()
        _L.debug(
            "visual identity upsert | sha={} user={} label={!r}",
            n.image_sha256[:12],
            n.correcting_user_id,
            n.entity_label,
        )
        stored = await self.get_exact(
            n.image_sha256,
            correcting_user_id=n.correcting_user_id,
            scope_key=scope_key,
        )
        assert stored is not None
        return stored

    async def get_exact(
        self,
        image_sha256: str,
        *,
        correcting_user_id: str | None = None,
        scope_key: str | None = None,
    ) -> VisualIdentityRecord | None:
        """Deterministic exact lookup by full SHA-256 (optional user filter)."""
        sha = normalize_image_sha256(image_sha256)
        if sha is None:
            return None
        db = self._require_db()
        if correcting_user_id is not None:
            user = str(correcting_user_id).strip()
            if not user:
                return None
            if scope_key is not None:
                key = str(scope_key).strip()
                if not key:
                    return None
                cursor = await db.execute(
                    "SELECT * FROM visual_identities "
                    "WHERE image_sha256 = ? AND correcting_user_id = ? AND scope_key = ?",
                    (sha, user, key),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM visual_identities "
                    "WHERE image_sha256 = ? AND correcting_user_id = ? "
                    "ORDER BY updated_at DESC LIMIT 2",
                    (sha, user),
                )
                rows = await cursor.fetchall()
                if len(rows) != 1:
                    return None
                return _row_to_record(rows[0])
        else:
            # Unscoped multi-user fetch is not a public recall path; return
            # the single row only when exactly one corrector exists.
            cursor = await db.execute(
                "SELECT * FROM visual_identities WHERE image_sha256 = ? "
                "ORDER BY updated_at DESC LIMIT 2",
                (sha,),
            )
            rows = await cursor.fetchall()
            if len(rows) != 1:
                return None
            return _row_to_record(rows[0])
        row = await cursor.fetchone()
        return _row_to_record(row) if row is not None else None

    async def lookup_for_context(
        self,
        image_sha256: str,
        *,
        current_user_id: str,
        current_group_id: str | None,
    ) -> VisualIdentityRecord | None:
        """Exact lookup that applies fail-closed visibility for the caller."""
        group = str(current_group_id or "").strip()
        scope_keys = [f"group:{group}", "global"] if group else ["private", "global"]
        for key in scope_keys:
            record = await self.get_exact(
                image_sha256,
                correcting_user_id=current_user_id,
                scope_key=key,
            )
            if record is None:
                continue
            if visual_identity_visible(
                record,
                current_user_id=current_user_id,
                current_group_id=current_group_id,
            ):
                return record
        return None

    async def store_correction(
        self,
        *,
        image_sha256: str,
        entity_label: str,
        correcting_user_id: str,
        origin_group_id: str | None = None,
        visibility: str | None = None,
        source_message_id: str | None = None,
        provenance: str = "user_correction",
        visual_evidence: dict[str, Any] | None = None,
        trigger: dict[str, Any] | None = None,
    ) -> VisualIdentityRecord:
        """Store one explicitly authorized human visual correction.

        Evidence and trigger metadata are verified at the persistence boundary
        but are not stored as memory cards.
        """
        _require_authorized_correction_context(
            image_sha256=image_sha256,
            provenance=provenance,
            visual_evidence=visual_evidence,
            trigger=trigger,
        )
        return await self.upsert(
            NewVisualIdentity(
                image_sha256=image_sha256,
                entity_label=entity_label,
                correcting_user_id=correcting_user_id,
                origin_group_id=origin_group_id,
                visibility=visibility,
                source_message_id=source_message_id,
                provenance=provenance,
            )
        )


# Soft alias for import-surface stability.
VisualIdentity = VisualIdentityRecord
