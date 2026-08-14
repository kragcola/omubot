"""Store-backed named operator authentication for Agent Runtime v2."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from services.storage.catalog import ConnectionProfile
from services.storage.migrations import Migration, MigrationRunner
from services.storage.sqlite import connect_sqlite

_DB_ID = "agent_runtime_operator_auth"
_OPERATOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,95}$")
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9:_-]{1,119}$")
_MAX_RESOURCE_REF = 512
_MIN_CREDENTIAL_LENGTH = 24
_MAX_CREDENTIAL_LENGTH = 512

_CREATE_OPERATORS = """
CREATE TABLE IF NOT EXISTS agent_runtime_operators (
    operator_id TEXT PRIMARY KEY,
    credential_salt BLOB NOT NULL,
    credential_hash BLOB NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_CREATE_SCOPES = """
CREATE TABLE IF NOT EXISTS agent_runtime_operator_scopes (
    operator_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    PRIMARY KEY (operator_id, scope),
    FOREIGN KEY (operator_id) REFERENCES agent_runtime_operators(operator_id)
        ON DELETE CASCADE
)
"""
_CREATE_RESOURCE_GRANTS = """
CREATE TABLE IF NOT EXISTS agent_runtime_operator_resources (
    operator_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    resource_ref TEXT NOT NULL,
    PRIMARY KEY (operator_id, scope, resource_ref),
    FOREIGN KEY (operator_id) REFERENCES agent_runtime_operators(operator_id)
        ON DELETE CASCADE
)
"""
_CREATE_RESOURCE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_agent_runtime_operator_resources_lookup
ON agent_runtime_operator_resources(operator_id, scope, resource_ref)
"""
_MIGRATION_V1_CHECKSUM = "sha256:" + hashlib.sha256(
    "\n".join(
        (
            _CREATE_OPERATORS,
            _CREATE_SCOPES,
            _CREATE_RESOURCE_GRANTS,
            _CREATE_RESOURCE_INDEX,
        )
    ).encode()
).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthorizedOperatorV1:
    """Authenticated operator snapshot without a credential or raw ACL secrets."""

    operator_id: str
    granted_scopes: tuple[str, ...]


async def _apply_v1(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_OPERATORS)
    await db.execute(_CREATE_SCOPES)
    await db.execute(_CREATE_RESOURCE_GRANTS)
    await db.execute(_CREATE_RESOURCE_INDEX)


async def _verify_v1(db: aiosqlite.Connection) -> bool:
    expected = {
        "agent_runtime_operators": {
            "operator_id",
            "credential_salt",
            "credential_hash",
            "enabled",
            "created_at",
            "updated_at",
        },
        "agent_runtime_operator_scopes": {"operator_id", "scope"},
        "agent_runtime_operator_resources": {
            "operator_id",
            "scope",
            "resource_ref",
        },
    }
    for table, columns in expected.items():
        cursor = await db.execute(f"PRAGMA table_info({table})")
        try:
            actual = {str(row["name"]) for row in await cursor.fetchall()}
        finally:
            await cursor.close()
        if actual != columns:
            return False
    cursor = await db.execute(
        "PRAGMA index_info(idx_agent_runtime_operator_resources_lookup)"
    )
    try:
        index_columns = tuple(str(row["name"]) for row in await cursor.fetchall())
    finally:
        await cursor.close()
    return index_columns == ("operator_id", "scope", "resource_ref")


_MIGRATION_V1 = Migration(
    version=1,
    name="create_agent_runtime_operator_auth",
    checksum=_MIGRATION_V1_CHECKSUM,
    apply=_apply_v1,
    verify=_verify_v1,
)


class OperatorAuthorizationStoreV1:
    """Persist operator credentials, scopes and exact resource grants."""

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
            ).ensure((_MIGRATION_V1,))
            db = await connect_sqlite(
                self.db_path,
                busy_timeout_ms=0,
                profile=ConnectionProfile.DELETE_FULL,
            )
            try:
                if not await _verify_v1(db):
                    raise RuntimeError("operator authorization schema verification failed")
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

    async def provision(
        self,
        *,
        operator_id: str,
        credential: str,
        granted_scopes: tuple[str, ...],
        resource_grants: tuple[tuple[str, str], ...],
        enabled: bool = True,
    ) -> None:
        """Replace one operator's credential and ACL atomically.

        Provisioning is an explicit administrative operation. It is deliberately
        not invoked from request handling or activation bootstrap.
        """

        clean_operator_id = _clean_operator_id(operator_id)
        clean_credential = _clean_credential(credential)
        scopes = _clean_scopes(granted_scopes)
        grants = _clean_resource_grants(resource_grants, scopes=scopes)
        salt = secrets.token_bytes(16)
        credential_hash = _derive_credential_hash(clean_credential, salt)
        now = datetime.now(UTC).isoformat()

        async with self._write_lock:
            db = self._require_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    """
                    INSERT INTO agent_runtime_operators (
                        operator_id, credential_salt, credential_hash,
                        enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(operator_id) DO UPDATE SET
                        credential_salt = excluded.credential_salt,
                        credential_hash = excluded.credential_hash,
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                    """,
                    (
                        clean_operator_id,
                        salt,
                        credential_hash,
                        int(bool(enabled)),
                        now,
                        now,
                    ),
                )
                await db.execute(
                    "DELETE FROM agent_runtime_operator_scopes WHERE operator_id = ?",
                    (clean_operator_id,),
                )
                await db.execute(
                    "DELETE FROM agent_runtime_operator_resources WHERE operator_id = ?",
                    (clean_operator_id,),
                )
                await db.executemany(
                    """
                    INSERT INTO agent_runtime_operator_scopes (operator_id, scope)
                    VALUES (?, ?)
                    """,
                    ((clean_operator_id, scope) for scope in scopes),
                )
                await db.executemany(
                    """
                    INSERT INTO agent_runtime_operator_resources (
                        operator_id, scope, resource_ref
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (clean_operator_id, scope, resource_ref)
                        for scope, resource_ref in grants
                    ),
                )
                await db.commit()
            except BaseException:
                await _rollback_preserving_cancellation(db)
                raise

    async def authenticate(
        self,
        *,
        operator_id: str,
        credential: str,
    ) -> AuthorizedOperatorV1 | None:
        """Verify a named credential and return the durable scope snapshot."""

        clean_operator_id = _clean_operator_id(operator_id)
        clean_credential = _clean_credential(credential)
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT credential_salt, credential_hash, enabled
            FROM agent_runtime_operators
            WHERE operator_id = ?
            """,
            (clean_operator_id,),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        if row is None or int(row["enabled"]) != 1:
            return None
        expected_hash = bytes(row["credential_hash"])
        actual_hash = _derive_credential_hash(
            clean_credential,
            bytes(row["credential_salt"]),
        )
        if not hmac.compare_digest(actual_hash, expected_hash):
            return None
        cursor = await db.execute(
            """
            SELECT scope
            FROM agent_runtime_operator_scopes
            WHERE operator_id = ?
            ORDER BY scope
            """,
            (clean_operator_id,),
        )
        try:
            scopes = tuple(str(row["scope"]) for row in await cursor.fetchall())
        finally:
            await cursor.close()
        return AuthorizedOperatorV1(
            operator_id=clean_operator_id,
            granted_scopes=scopes,
        )

    async def allows(
        self,
        operator: AuthorizedOperatorV1,
        *,
        scope: str,
        resource_ref: str,
    ) -> bool:
        """Check current durable state, not only an old authenticated snapshot."""

        if not isinstance(operator, AuthorizedOperatorV1):
            raise TypeError("operator must be an AuthorizedOperatorV1")
        clean_scope = _clean_scope(scope)
        clean_resource_ref = _clean_resource_ref(resource_ref)
        if clean_scope not in operator.granted_scopes:
            return False
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT 1
            FROM agent_runtime_operators AS operator
            JOIN agent_runtime_operator_scopes AS scope_grant
              ON scope_grant.operator_id = operator.operator_id
            JOIN agent_runtime_operator_resources AS resource_grant
              ON resource_grant.operator_id = operator.operator_id
             AND resource_grant.scope = scope_grant.scope
            WHERE operator.operator_id = ?
              AND operator.enabled = 1
              AND scope_grant.scope = ?
              AND resource_grant.resource_ref = ?
            LIMIT 1
            """,
            (operator.operator_id, clean_scope, clean_resource_ref),
        )
        try:
            return await cursor.fetchone() is not None
        finally:
            await cursor.close()

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("OperatorAuthorizationStoreV1 is not initialized")
        return self._db


def _clean_operator_id(value: object) -> str:
    operator_id = str(value or "").strip()
    if _OPERATOR_ID_RE.fullmatch(operator_id) is None:
        raise ValueError("operator_id is invalid")
    return operator_id


def _clean_credential(value: object) -> str:
    credential = str(value or "")
    if (
        len(credential) < _MIN_CREDENTIAL_LENGTH
        or len(credential) > _MAX_CREDENTIAL_LENGTH
        or not credential.isascii()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in credential)
    ):
        raise ValueError("operator credential is invalid")
    return credential


def _clean_scope(value: object) -> str:
    scope = str(value or "").strip()
    if _SCOPE_RE.fullmatch(scope) is None:
        raise ValueError("operator scope is invalid")
    return scope


def _clean_scopes(values: tuple[str, ...]) -> tuple[str, ...]:
    scopes = tuple(sorted({_clean_scope(value) for value in values}))
    if not scopes:
        raise ValueError("at least one operator scope is required")
    return scopes


def _clean_resource_ref(value: object) -> str:
    resource_ref = str(value or "").strip()
    if (
        len(resource_ref) < 3
        or len(resource_ref) > _MAX_RESOURCE_REF
        or not resource_ref.isascii()
        or "*" in resource_ref
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in resource_ref)
    ):
        raise ValueError("operator resource_ref is invalid")
    return resource_ref


def _clean_resource_grants(
    values: tuple[tuple[str, str], ...],
    *,
    scopes: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    cleaned = tuple(
        sorted(
            {
                (_clean_scope(scope), _clean_resource_ref(resource_ref))
                for scope, resource_ref in values
            }
        )
    )
    if any(scope not in scopes for scope, _resource_ref in cleaned):
        raise ValueError("operator resource grant requires a granted scope")
    return cleaned


def _derive_credential_hash(credential: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        credential.encode(),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )


async def _rollback_preserving_cancellation(db: aiosqlite.Connection) -> None:
    await _await_required_cleanup(asyncio.create_task(db.rollback()))


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


__all__ = ["AuthorizedOperatorV1", "OperatorAuthorizationStoreV1"]
