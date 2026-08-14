"""Durable authoritative triggers for Agent Runtime v2 production wiring."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from services.agent_runtime.executor import TrustedToolContext
from services.agent_runtime.policy import RuntimePrincipal
from services.storage.catalog import ConnectionProfile
from services.storage.migrations import Migration, MigrationRunner
from services.storage.sqlite import connect_sqlite

_DB_ID = "agent_runtime_invocations"
_TRIGGER_TYPES = frozenset({"message", "tick", "domain_event", "recovery"})
_SAFE_SCOPE_RE = re.compile(r"^[a-z][a-z0-9:_-]{1,119}$")
_SAFE_TARGET_MAX = 256
_INVOCATION_ID_RE = re.compile(r"^inv_[0-9a-f]{32}$")
_WORKER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,95}$")
_LEASE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_WORKER_LEASE_NAME = "agent_runtime_production_worker"
_MIN_LEASE_TTL_SECONDS = 1.0
_MAX_LEASE_TTL_SECONDS = 300.0

_CREATE_INVOCATIONS = """
CREATE TABLE IF NOT EXISTS agent_runtime_trusted_invocations (
    invocation_id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL
        CHECK (trigger_type IN ('message', 'tick', 'domain_event', 'recovery')),
    trigger_ref TEXT NOT NULL UNIQUE,
    principal_kind TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    registry_generation INTEGER NOT NULL,
    granted_scopes_json TEXT NOT NULL,
    allowed_target_refs_json TEXT NOT NULL,
    onebot_message_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""
_CREATE_TRIGGER_INDEX = """
CREATE INDEX IF NOT EXISTS idx_agent_runtime_invocations_created
ON agent_runtime_trusted_invocations(created_at, invocation_id)
"""
_MIGRATION_V1_CHECKSUM = "sha256:" + hashlib.sha256(
    "\n".join((_CREATE_INVOCATIONS, _CREATE_TRIGGER_INDEX)).encode()
).hexdigest()

_CREATE_WORKER_LEASE = f"""
CREATE TABLE agent_runtime_worker_lease (
    lease_name TEXT PRIMARY KEY
        CHECK (lease_name = '{_WORKER_LEASE_NAME}'),
    owner_id TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL
)
"""
_MIGRATION_V2_CHECKSUM = "sha256:" + hashlib.sha256(
    _CREATE_WORKER_LEASE.encode()
).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthoritativeTriggerV1:
    """Normalized facts emitted by a trusted host adapter before model work."""

    trigger_type: str
    trigger_ref: str
    principal_kind: str
    principal_id: str
    session_id: str
    group_id: str
    registry_generation: int
    granted_scopes: tuple[str, ...]
    allowed_target_refs: tuple[str, ...]
    onebot_message_refs: tuple[str, ...]

    @classmethod
    def from_onebot_message(
        cls,
        *,
        group_id: str | None,
        user_id: str,
        message_id: str,
        session_id: str,
        registry_generation: int,
        allowed_target_refs: tuple[str, ...],
        granted_scopes: tuple[str, ...] = (),
    ) -> AuthoritativeTriggerV1:
        clean_user = _positive_id(user_id, field="user_id")
        clean_message = _positive_id(message_id, field="message_id")
        clean_group = _positive_id(group_id, field="group_id") if group_id else ""
        expected_session = (
            f"group_{clean_group}" if clean_group else f"private_{clean_user}"
        )
        clean_session = _safe_text(session_id, field="session_id", maximum=160)
        if clean_session != expected_session:
            raise ValueError("session_id does not match authoritative OneBot identity")
        message_ref = (
            f"onebot:group:{clean_group}:message:{clean_message}"
            if clean_group
            else f"onebot:user:{clean_user}:message:{clean_message}"
        )
        return cls(
            trigger_type="message",
            trigger_ref=message_ref,
            principal_kind="onebot_user",
            principal_id=clean_user,
            session_id=clean_session,
            group_id=clean_group,
            registry_generation=_non_negative_generation(registry_generation),
            granted_scopes=_clean_scopes(granted_scopes),
            allowed_target_refs=_clean_targets(allowed_target_refs),
            onebot_message_refs=(message_ref,),
        )


@dataclass(frozen=True, slots=True)
class TrustedInvocationRecordV1:
    invocation_id: str
    trigger_type: str
    trigger_ref: str
    principal_kind: str
    principal_id: str
    session_id: str
    group_id: str
    registry_generation: int
    granted_scopes: tuple[str, ...]
    allowed_target_refs: tuple[str, ...]
    onebot_message_refs: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class ReconstructedInvocationV1:
    record: TrustedInvocationRecordV1
    principal: RuntimePrincipal
    trusted_context: TrustedToolContext


@dataclass(frozen=True, slots=True)
class WorkerLeaseV1:
    """Opaque proof that one process currently owns Runtime v2 recovery."""

    owner_id: str
    lease_token: str
    lease_until: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _clean_worker_id(self.owner_id))
        object.__setattr__(self, "lease_token", _clean_lease_token(self.lease_token))
        object.__setattr__(
            self,
            "lease_until",
            _iso_utc(_aware_utc(self.lease_until, field="lease_until")),
        )


async def _apply_v1(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_INVOCATIONS)
    await db.execute(_CREATE_TRIGGER_INDEX)


async def _verify_v1(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute("PRAGMA table_info(agent_runtime_trusted_invocations)")
    try:
        columns = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    expected = {
        "invocation_id",
        "trigger_type",
        "trigger_ref",
        "principal_kind",
        "principal_id",
        "session_id",
        "group_id",
        "registry_generation",
        "granted_scopes_json",
        "allowed_target_refs_json",
        "onebot_message_refs_json",
        "created_at",
    }
    if columns != expected:
        return False
    cursor = await db.execute("PRAGMA index_info(idx_agent_runtime_invocations_created)")
    try:
        index_columns = tuple(str(row["name"]) for row in await cursor.fetchall())
    finally:
        await cursor.close()
    return index_columns == ("created_at", "invocation_id")


_MIGRATION_V1 = Migration(
    version=1,
    name="create_agent_runtime_trusted_invocations",
    checksum=_MIGRATION_V1_CHECKSUM,
    apply=_apply_v1,
    verify=_verify_v1,
)


async def _apply_v2(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_WORKER_LEASE)


async def _verify_v2(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute("PRAGMA table_info(agent_runtime_worker_lease)")
    try:
        columns = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    return columns == {
        "lease_name",
        "owner_id",
        "lease_token",
        "lease_until",
        "acquired_at",
        "renewed_at",
    }


_MIGRATION_V2 = Migration(
    version=2,
    name="create_agent_runtime_worker_lease",
    checksum=_MIGRATION_V2_CHECKSUM,
    apply=_apply_v2,
    verify=_verify_v2,
)


class TrustedInvocationStoreV1:
    """Own authoritative trigger records and reconstruction inputs."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        async with self._lifecycle_lock:
            if self._db is not None:
                return
            await MigrationRunner(
                db_path=self.db_path,
                db_id=_DB_ID,
                profile=ConnectionProfile.DELETE_FULL,
            ).ensure((_MIGRATION_V1, _MIGRATION_V2))
            db = await connect_sqlite(
                self.db_path,
                busy_timeout_ms=0,
                profile=ConnectionProfile.DELETE_FULL,
            )
            try:
                if not await _verify_v1(db) or not await _verify_v2(db):
                    raise RuntimeError("trusted invocation schema verification failed")
            except BaseException:
                await _close_preserving_cancellation(db)
                raise
            self._db = db

    async def close(self) -> None:
        async with self._lifecycle_lock:
            db = self._db
            self._db = None
            if db is not None:
                await _close_preserving_cancellation(db)

    async def record(
        self,
        trigger: AuthoritativeTriggerV1,
    ) -> TrustedInvocationRecordV1:
        if not isinstance(trigger, AuthoritativeTriggerV1):
            raise TypeError("trigger must be an AuthoritativeTriggerV1")
        invocation_id = _invocation_id(trigger)
        async with self._write_lock:
            db = self._require_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                existing = await _fetch_by_trigger_ref(db, trigger.trigger_ref)
                if existing is not None:
                    await db.rollback()
                    if _matches_trigger(existing, trigger):
                        return existing
                    raise ValueError("authoritative trigger conflicts with immutable record")
                now = datetime.now(UTC).isoformat()
                await db.execute(
                    """
                    INSERT INTO agent_runtime_trusted_invocations (
                        invocation_id, trigger_type, trigger_ref,
                        principal_kind, principal_id, session_id, group_id,
                        registry_generation, granted_scopes_json,
                        allowed_target_refs_json, onebot_message_refs_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        invocation_id,
                        trigger.trigger_type,
                        trigger.trigger_ref,
                        trigger.principal_kind,
                        trigger.principal_id,
                        trigger.session_id,
                        trigger.group_id,
                        trigger.registry_generation,
                        _json_tuple(trigger.granted_scopes),
                        _json_tuple(trigger.allowed_target_refs),
                        _json_tuple(trigger.onebot_message_refs),
                        now,
                    ),
                )
                await db.commit()
            except BaseException:
                await _rollback_preserving_cancellation(db)
                raise
        return TrustedInvocationRecordV1(
            invocation_id=invocation_id,
            trigger_type=trigger.trigger_type,
            trigger_ref=trigger.trigger_ref,
            principal_kind=trigger.principal_kind,
            principal_id=trigger.principal_id,
            session_id=trigger.session_id,
            group_id=trigger.group_id,
            registry_generation=trigger.registry_generation,
            granted_scopes=trigger.granted_scopes,
            allowed_target_refs=trigger.allowed_target_refs,
            onebot_message_refs=trigger.onebot_message_refs,
            created_at=now,
        )

    async def reconstruct(
        self,
        invocation_id: str,
        *,
        bot: Any = None,
    ) -> ReconstructedInvocationV1:
        record = await self.get(invocation_id)
        if record is None:
            raise KeyError(invocation_id)
        principal = RuntimePrincipal(
            kind=record.principal_kind,
            principal_id=record.principal_id,
            granted_scopes=record.granted_scopes,
            allowed_target_refs=record.allowed_target_refs,
        )
        context = TrustedToolContext(
            bot=bot,
            user_id=record.principal_id,
            group_id=record.group_id or None,
            session_id=record.session_id,
            extra={"onebot_message_refs": record.onebot_message_refs},
            auth_context_ref=f"invocation:{record.invocation_id}",
            trace_id=f"invocation:{record.invocation_id}",
        )
        return ReconstructedInvocationV1(
            record=record,
            principal=principal,
            trusted_context=context,
        )

    async def get(self, invocation_id: str) -> TrustedInvocationRecordV1 | None:
        clean_id = _safe_text(invocation_id, field="invocation_id", maximum=80)
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT invocation_id, trigger_type, trigger_ref,
                   principal_kind, principal_id, session_id, group_id,
                   registry_generation, granted_scopes_json,
                   allowed_target_refs_json, onebot_message_refs_json,
                   created_at
            FROM agent_runtime_trusted_invocations
            WHERE invocation_id = ?
            """,
            (clean_id,),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return _record_from_row(row) if row is not None else None

    async def acquire_worker_lease(
        self,
        *,
        worker_id: str,
        lease_ttl_seconds: float,
        now: datetime | None = None,
    ) -> WorkerLeaseV1 | None:
        """Atomically acquire the one production worker lease, if unowned."""

        owner_id = _clean_worker_id(worker_id)
        acquired_at = _lease_now(now)
        lease_until = acquired_at + timedelta(
            seconds=_clean_lease_ttl(lease_ttl_seconds)
        )
        token = _new_lease_token()
        lease = WorkerLeaseV1(
            owner_id=owner_id,
            lease_token=token,
            lease_until=_iso_utc(lease_until),
        )
        async with self._write_lock:
            db = self._require_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await _fetch_worker_lease(db)
                if current is not None and _lease_is_active(current, acquired_at):
                    await db.rollback()
                    return None
                await db.execute(
                    """
                    INSERT INTO agent_runtime_worker_lease (
                        lease_name, owner_id, lease_token, lease_until,
                        acquired_at, renewed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(lease_name) DO UPDATE SET
                        owner_id = excluded.owner_id,
                        lease_token = excluded.lease_token,
                        lease_until = excluded.lease_until,
                        acquired_at = excluded.acquired_at,
                        renewed_at = excluded.renewed_at
                    """,
                    (
                        _WORKER_LEASE_NAME,
                        owner_id,
                        token,
                        _iso_utc(lease_until),
                        _iso_utc(acquired_at),
                        _iso_utc(acquired_at),
                    ),
                )
                await db.commit()
            except BaseException:
                await _cleanup_abandoned_worker_lease_attempt(db, lease)
                raise
        return lease

    async def renew_worker_lease(
        self,
        lease: WorkerLeaseV1,
        *,
        lease_ttl_seconds: float,
        now: datetime | None = None,
    ) -> WorkerLeaseV1 | None:
        """Renew only the exact active lease and rotate its opaque token."""

        if not isinstance(lease, WorkerLeaseV1):
            raise TypeError("lease must be a WorkerLeaseV1")
        renewed_at = _lease_now(now)
        lease_until = renewed_at + timedelta(
            seconds=_clean_lease_ttl(lease_ttl_seconds)
        )
        token = _new_lease_token()
        renewed = WorkerLeaseV1(
            owner_id=lease.owner_id,
            lease_token=token,
            lease_until=_iso_utc(lease_until),
        )
        async with self._write_lock:
            db = self._require_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await _fetch_worker_lease(db)
                if (
                    current is None
                    or not _lease_matches(current, lease)
                    or not _lease_is_active(current, renewed_at)
                ):
                    await db.rollback()
                    return None
                updated = await db.execute(
                    """
                    UPDATE agent_runtime_worker_lease
                    SET lease_token = ?, lease_until = ?, renewed_at = ?
                    WHERE lease_name = ? AND owner_id = ? AND lease_token = ?
                    """,
                    (
                        token,
                        _iso_utc(lease_until),
                        _iso_utc(renewed_at),
                        _WORKER_LEASE_NAME,
                        lease.owner_id,
                        lease.lease_token,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("worker lease renewal lost compare-and-swap")
                await db.commit()
            except BaseException:
                await _cleanup_abandoned_worker_lease_attempt(db, renewed)
                raise
        return renewed

    async def extend_worker_lease(
        self,
        lease: WorkerLeaseV1,
        *,
        lease_ttl_seconds: float,
        now: datetime | None = None,
    ) -> WorkerLeaseV1 | None:
        """Reserve an exact owner/token lease without rotating its token.

        Provider execution needs a stable token while a process-local fence is
        held.  Unlike normal renewal, this deliberately preserves that token
        but still uses one SQLite transaction and a compare-and-swap over the
        exact owner/token pair.  A stale token, expired lease, or concurrent
        takeover therefore cannot be extended.
        """

        if not isinstance(lease, WorkerLeaseV1):
            raise TypeError("lease must be a WorkerLeaseV1")
        extended_at = _lease_now(now)
        requested_until = extended_at + timedelta(
            seconds=_clean_lease_ttl(lease_ttl_seconds)
        )
        extended: WorkerLeaseV1 | None = None
        async with self._write_lock:
            db = self._require_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                current = await _fetch_worker_lease(db)
                if (
                    current is None
                    or not _lease_owner_token_matches(current, lease)
                    or not _lease_is_active(current, extended_at)
                ):
                    await db.rollback()
                    return None
                current_until = _aware_utc(
                    current.lease_until,
                    field="persisted worker lease",
                )
                lease_until = max(current_until, requested_until)
                if lease_until == current_until:
                    await db.rollback()
                    return current
                extended = WorkerLeaseV1(
                    owner_id=lease.owner_id,
                    lease_token=lease.lease_token,
                    lease_until=_iso_utc(lease_until),
                )
                updated = await db.execute(
                    """
                    UPDATE agent_runtime_worker_lease
                    SET lease_until = ?, renewed_at = ?
                    WHERE lease_name = ? AND owner_id = ? AND lease_token = ?
                          AND lease_until = ?
                    """,
                    (
                        extended.lease_until,
                        _iso_utc(extended_at),
                        _WORKER_LEASE_NAME,
                        lease.owner_id,
                        lease.lease_token,
                        current.lease_until,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("worker lease execution fence lost compare-and-swap")
                await db.commit()
            except BaseException:
                if extended is None:
                    await _rollback_preserving_cancellation(db)
                else:
                    await _cleanup_abandoned_worker_lease_extension(db, extended)
                raise
        assert extended is not None
        return extended

    async def has_worker_lease(
        self,
        lease: WorkerLeaseV1,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Confirm exact current ownership before every governed dispatch."""

        if not isinstance(lease, WorkerLeaseV1):
            raise TypeError("lease must be a WorkerLeaseV1")
        checked_at = _lease_now(now)
        async with self._write_lock:
            current = await _fetch_worker_lease(self._require_db())
        return (
            current is not None
            and _lease_matches(current, lease)
            and _lease_is_active(current, checked_at)
        )

    async def release_worker_lease(self, lease: WorkerLeaseV1) -> bool:
        """Release only the exact lease token; stale owners cannot clear a new one."""

        if not isinstance(lease, WorkerLeaseV1):
            raise TypeError("lease must be a WorkerLeaseV1")
        async with self._write_lock:
            db = self._require_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                deleted = await db.execute(
                    """
                    DELETE FROM agent_runtime_worker_lease
                    WHERE lease_name = ? AND owner_id = ? AND lease_token = ?
                    """,
                    (_WORKER_LEASE_NAME, lease.owner_id, lease.lease_token),
                )
                await db.commit()
            except BaseException:
                await _rollback_preserving_cancellation(db)
                raise
        return deleted.rowcount == 1

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("TrustedInvocationStoreV1 is not initialized")
        return self._db


async def _fetch_by_trigger_ref(
    db: aiosqlite.Connection,
    trigger_ref: str,
) -> TrustedInvocationRecordV1 | None:
    cursor = await db.execute(
        """
        SELECT invocation_id, trigger_type, trigger_ref,
               principal_kind, principal_id, session_id, group_id,
               registry_generation, granted_scopes_json,
               allowed_target_refs_json, onebot_message_refs_json,
               created_at
        FROM agent_runtime_trusted_invocations
        WHERE trigger_ref = ?
        """,
        (trigger_ref,),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return _record_from_row(row) if row is not None else None


async def _fetch_worker_lease(
    db: aiosqlite.Connection,
) -> WorkerLeaseV1 | None:
    cursor = await db.execute(
        """
        SELECT owner_id, lease_token, lease_until
        FROM agent_runtime_worker_lease
        WHERE lease_name = ?
        """,
        (_WORKER_LEASE_NAME,),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        return None
    return WorkerLeaseV1(
        owner_id=str(row["owner_id"]),
        lease_token=str(row["lease_token"]),
        lease_until=str(row["lease_until"]),
    )


def _record_from_row(row: aiosqlite.Row) -> TrustedInvocationRecordV1:
    return TrustedInvocationRecordV1(
        invocation_id=str(row["invocation_id"]),
        trigger_type=str(row["trigger_type"]),
        trigger_ref=str(row["trigger_ref"]),
        principal_kind=str(row["principal_kind"]),
        principal_id=str(row["principal_id"]),
        session_id=str(row["session_id"]),
        group_id=str(row["group_id"]),
        registry_generation=int(row["registry_generation"]),
        granted_scopes=_json_tuple_load(row["granted_scopes_json"]),
        allowed_target_refs=_json_tuple_load(row["allowed_target_refs_json"]),
        onebot_message_refs=_json_tuple_load(row["onebot_message_refs_json"]),
        created_at=str(row["created_at"]),
    )


def _matches_trigger(
    record: TrustedInvocationRecordV1,
    trigger: AuthoritativeTriggerV1,
) -> bool:
    return (
        record.trigger_type,
        record.trigger_ref,
        record.principal_kind,
        record.principal_id,
        record.session_id,
        record.group_id,
        record.registry_generation,
        record.granted_scopes,
        record.allowed_target_refs,
        record.onebot_message_refs,
    ) == (
        trigger.trigger_type,
        trigger.trigger_ref,
        trigger.principal_kind,
        trigger.principal_id,
        trigger.session_id,
        trigger.group_id,
        trigger.registry_generation,
        trigger.granted_scopes,
        trigger.allowed_target_refs,
        trigger.onebot_message_refs,
    )


def _invocation_id(trigger: AuthoritativeTriggerV1) -> str:
    digest = hashlib.sha256(
        _canonical_json(
            {
                "trigger_type": trigger.trigger_type,
                "trigger_ref": trigger.trigger_ref,
                "principal_kind": trigger.principal_kind,
                "principal_id": trigger.principal_id,
                "session_id": trigger.session_id,
                "group_id": trigger.group_id,
                "registry_generation": trigger.registry_generation,
                "granted_scopes": trigger.granted_scopes,
                "allowed_target_refs": trigger.allowed_target_refs,
                "onebot_message_refs": trigger.onebot_message_refs,
            }
        ).encode()
    ).hexdigest()
    return f"inv_{digest[:32]}"


def is_authoritative_invocation_id(value: object) -> bool:
    """Return whether a value has the only ID shape emitted by this store."""

    return isinstance(value, str) and _INVOCATION_ID_RE.fullmatch(value) is not None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _json_tuple(values: tuple[str, ...]) -> str:
    return _canonical_json(list(values))


def _json_tuple_load(value: object) -> tuple[str, ...]:
    raw = json.loads(str(value))
    if not isinstance(raw, list):
        raise ValueError("persisted invocation tuple is malformed")
    return tuple(str(item) for item in raw)


def _positive_id(value: object, *, field: str) -> str:
    raw = str(value or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{field} must be a positive numeric identifier")
    return str(int(raw))


def _clean_worker_id(value: object) -> str:
    worker_id = str(value or "").strip()
    if _WORKER_ID_RE.fullmatch(worker_id) is None:
        raise ValueError("worker_id is invalid")
    return worker_id


def _clean_lease_token(value: object) -> str:
    token = str(value or "").strip()
    if _LEASE_TOKEN_RE.fullmatch(token) is None:
        raise ValueError("worker lease token is invalid")
    return token


def _clean_lease_ttl(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("worker lease ttl is invalid")
    if not isinstance(value, (str, int, float)):
        raise ValueError("worker lease ttl is invalid")
    try:
        ttl = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("worker lease ttl is invalid") from exc
    if not _MIN_LEASE_TTL_SECONDS <= ttl <= _MAX_LEASE_TTL_SECONDS:
        raise ValueError("worker lease ttl is invalid")
    return ttl


def _new_lease_token() -> str:
    return secrets.token_urlsafe(32)


def _lease_now(value: datetime | None) -> datetime:
    return _aware_utc(value or datetime.now(UTC), field="lease clock")


def _aware_utc(value: object, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} is invalid") from exc
    else:
        raise ValueError(f"{field} is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} is invalid")
    return parsed.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _lease_is_active(lease: WorkerLeaseV1, at: datetime) -> bool:
    return _aware_utc(lease.lease_until, field="persisted worker lease") > at


def _lease_matches(current: WorkerLeaseV1, expected: WorkerLeaseV1) -> bool:
    return (
        current.owner_id == expected.owner_id
        and current.lease_token == expected.lease_token
        and current.lease_until == expected.lease_until
    )


def _lease_owner_token_matches(
    current: WorkerLeaseV1,
    expected: WorkerLeaseV1,
) -> bool:
    """Match a fence token without weakening full-lease checks elsewhere."""

    return (
        current.owner_id == expected.owner_id
        and current.lease_token == expected.lease_token
    )


def _safe_text(value: object, *, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > maximum
        or not text.isascii()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in text)
    ):
        raise ValueError(f"{field} is invalid")
    return text


def _non_negative_generation(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("registry_generation is invalid")
    if isinstance(value, int):
        generation = value
    elif isinstance(value, str) and value.strip().isdigit():
        generation = int(value.strip())
    else:
        raise ValueError("registry_generation is invalid")
    if generation < 0:
        raise ValueError("registry_generation is invalid")
    return generation


def _clean_scopes(values: tuple[str, ...]) -> tuple[str, ...]:
    scopes: set[str] = set()
    for value in values:
        scope = str(value or "").strip()
        if _SAFE_SCOPE_RE.fullmatch(scope) is None:
            raise ValueError("granted scope is invalid")
        scopes.add(scope)
    return tuple(sorted(scopes))


def _clean_targets(values: tuple[str, ...]) -> tuple[str, ...]:
    targets: set[str] = set()
    for value in values:
        target = str(value or "").strip()
        if (
            not target
            or len(target) > _SAFE_TARGET_MAX
            or not target.isascii()
            or "*" in target
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in target)
        ):
            raise ValueError("allowed target reference is invalid")
        targets.add(target)
    return tuple(sorted(targets))


async def _rollback_preserving_cancellation(db: aiosqlite.Connection) -> None:
    await _await_required_cleanup(asyncio.create_task(db.rollback()))


async def _cleanup_abandoned_worker_lease_attempt(
    db: aiosqlite.Connection,
    lease: WorkerLeaseV1,
) -> None:
    """Remove only a lease that may have committed before caller cancellation."""

    async def cleanup() -> None:
        await db.rollback()
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """
            DELETE FROM agent_runtime_worker_lease
            WHERE lease_name = ? AND owner_id = ? AND lease_token = ?
            """,
            (_WORKER_LEASE_NAME, lease.owner_id, lease.lease_token),
        )
        await db.commit()

    await _await_required_cleanup(asyncio.create_task(cleanup()))


async def _cleanup_abandoned_worker_lease_extension(
    db: aiosqlite.Connection,
    lease: WorkerLeaseV1,
) -> None:
    """Remove a committed extension without deleting its prior same-token lease."""

    async def cleanup() -> None:
        await db.rollback()
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """
            DELETE FROM agent_runtime_worker_lease
            WHERE lease_name = ? AND owner_id = ? AND lease_token = ?
                  AND lease_until = ?
            """,
            (
                _WORKER_LEASE_NAME,
                lease.owner_id,
                lease.lease_token,
                lease.lease_until,
            ),
        )
        await db.commit()

    await _await_required_cleanup(asyncio.create_task(cleanup()))


async def _close_preserving_cancellation(db: Any) -> None:
    await _await_required_cleanup(asyncio.create_task(db.close()))


async def _await_required_cleanup(task: asyncio.Task[Any]) -> None:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
            continue
    task.result()
    if cancellation is not None:
        raise cancellation


__all__ = [
    "AuthoritativeTriggerV1",
    "ReconstructedInvocationV1",
    "TrustedInvocationRecordV1",
    "TrustedInvocationStoreV1",
    "WorkerLeaseV1",
]
