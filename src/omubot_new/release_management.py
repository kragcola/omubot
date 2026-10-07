"""Local source-release pins, consistent DB backups and manual preparation only.

This owner never installs a release, stops a process, copies secret material or
restores a runtime. A published receipt covers SQLite and explicit Config TOML.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import tomllib
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from zipfile import BadZipFile, ZipFile

from pydantic import Field, ValidationError, field_validator

from .config import Config
from .store import Store, drain_on_cancel
from .types import OperationError, StrictModel


class _Member(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def relative_member(cls, value: str) -> str:
        if (
            not value
            or "\\" in value
            or PurePosixPath(value).is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("invalid release member")
        return value


class _Manifest(StrictModel):
    format: str = Field(pattern=r"^omubot-new-source-template$")
    version: int = Field(ge=1, le=1)
    files: list[_Member] = Field(min_length=1, max_length=4096)


class _BackupDocument(StrictModel):
    format: Literal["omubot-native-preparation-v1"]
    scope: Literal["sqlite-and-config-only"]
    schema_version: int = Field(alias="schema", ge=1)
    instance_id: str = Field(min_length=1, max_length=64)
    bot_id: str = Field(min_length=1, max_length=64)
    settings_revision: int = Field(ge=1)
    settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_revision: int = Field(ge=0)
    counts: tuple[tuple[str, int], ...] = Field(max_length=16)
    database_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_release_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    target_release_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _read(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OperationError("invalid_release_file")
            return handle.read()
    except OSError as exc:
        raise OperationError("release_file_unavailable") from exc


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceRelease:
    path: Path
    sha256: str

    def __post_init__(self) -> None:
        path, expected_sha256 = self.path, self.sha256
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise OperationError("invalid_release_sha")
        raw = _read(path)
        if _sha(raw) != expected_sha256:
            raise OperationError("release_changed")
        try:
            with ZipFile(path) as archive:
                manifest = _Manifest.model_validate_json(archive.read("manifest.json"))
                members = archive.infolist()
                expected = {item.path for item in manifest.files}
                if (
                    len(expected) != len(manifest.files)
                    or len(members) != len(expected) + 1
                    or {item.filename for item in members} != expected | {"manifest.json"}
                ):
                    raise OperationError("invalid_release_manifest")
                for item in manifest.files:
                    content = archive.read(item.path)
                    if len(content) != item.size or _sha(content) != item.sha256:
                        raise OperationError("invalid_release_manifest")
        except (BadZipFile, KeyError, ValidationError, OSError) as exc:
            raise OperationError("invalid_release_manifest") from exc
        if _sha(_read(path)) != expected_sha256:
            raise OperationError("release_changed")
        object.__setattr__(self, "path", path.absolute())

    @classmethod
    def inspect(cls, path: Path, *, expected_sha256: str) -> SourceRelease:
        """Pin an already downloaded export-template ZIP, without extraction."""
        return cls(path, expected_sha256)

    def verify(self) -> None:
        if _sha(_read(self.path)) != self.sha256:
            raise OperationError("release_changed")


@dataclass(frozen=True, slots=True)
class _Snapshot:
    schema: int
    instance_id: str
    bot_id: str
    settings_revision: int
    settings_sha256: str
    policy_revision: int
    counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class BackupReceipt:
    directory: Path
    database_sha256: str
    config_sha256: str
    schema: int
    instance_id: str
    bot_id: str
    settings_revision: int
    settings_sha256: str
    policy_revision: int
    counts: tuple[tuple[str, int], ...]
    current_release: SourceRelease | None
    target_release: SourceRelease
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class ManualUpdatePlan:
    preparation_ready: bool
    blockers: tuple[str, ...]
    update_steps: tuple[str, ...]
    rollback_steps: tuple[str, ...]
    limitations: tuple[str, ...]


class NativeUpdatePreparation:
    """One injected open Store; no second SQL writer or control service."""

    def __init__(self, store: Store) -> None:
        if store.instance_id is None or store.bot_id is None:
            raise OperationError("release_unbound_store")
        self.store = store
        self._lock = asyncio.Lock()

    def _config(self, raw: bytes) -> Config:
        try:
            data = tomllib.loads(raw.decode("utf-8"))
            if "db_path" in data or data.get("mode", "offline") != "offline":
                raise ValueError("instance startup configuration required")
            config = Config.model_validate({**data, "db_path": str(self.store.path)})
        except (UnicodeError, ValueError, ValidationError) as exc:
            raise OperationError("invalid_release_config") from exc
        if (config.instance_id, config.bot_id) != (self.store.instance_id, self.store.bot_id):
            raise OperationError("release_instance_mismatch")
        return config

    def _metadata(self, path: Path) -> _Snapshot:
        # Only closed, isolated Store.backup outputs are read here. Reading the
        # sealed file as immutable avoids creating WAL/SHM beside a private receipt.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise OperationError("backup_invalid")
            identity = db.execute("SELECT instance_id,bot_id FROM instance WHERE id=1").fetchone()
            if identity != (self.store.instance_id, self.store.bot_id):
                raise OperationError("release_instance_mismatch")
            settings = db.execute(
                "SELECT revision,document FROM config_versions ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            if settings is None:
                raise OperationError("release_settings_missing")
            policy = db.execute("SELECT revision FROM policy WHERE id=1").fetchone()
            if policy is None:
                raise OperationError("invalid_policy")
            counts = tuple(
                (table, db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in ("requests", "actions", "audit", "memory_facts", "config_versions")
            )
            return _Snapshot(
                schema=int(db.execute("PRAGMA user_version").fetchone()[0]),
                instance_id=str(identity[0]),
                bot_id=str(identity[1]),
                settings_revision=int(settings[0]),
                settings_sha256=_sha(str(settings[1]).encode()),
                policy_revision=int(policy[0]),
                counts=counts,
            )

    async def prepare(
        self,
        *,
        config_path: Path,
        destination: Path,
        expected_config_sha256: str,
        expected_settings_revision: int,
        expected_policy_revision: int,
        target_release: SourceRelease,
        current_release: SourceRelease | None = None,
    ) -> BackupReceipt:
        async with self._lock:
            target_release.verify()
            if current_release is not None:
                current_release.verify()
            raw = _read(config_path)
            self._config(raw)
            if _sha(raw) != expected_config_sha256:
                raise OperationError("release_config_changed")
            destination = await asyncio.to_thread(destination.absolute)
            try:
                destination.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise OperationError("backup_destination_exists") from exc
            staged = Path(tempfile.mkdtemp(prefix=".preparing-", dir=destination))
            published: list[Path] = []
            committed = False
            try:
                db_path = staged / "state.sqlite3"
                await self.store.backup(db_path)

                def publish() -> BackupReceipt:
                    nonlocal committed
                    metadata = self._metadata(db_path)
                    if (
                        metadata.settings_revision != expected_settings_revision
                        or metadata.policy_revision != expected_policy_revision
                    ):
                        raise OperationError("revision_conflict")
                    config_copy = staged / "config.toml"
                    config_copy.write_bytes(raw)
                    database_sha = _sha(_read(db_path))
                    document = {
                        **asdict(metadata),
                        "database_sha256": database_sha,
                        "config_sha256": _sha(raw),
                        "current_release_sha256": current_release.sha256 if current_release else None,
                        "target_release_sha256": target_release.sha256,
                        "scope": "sqlite-and-config-only",
                        "format": "omubot-native-preparation-v1",
                    }
                    receipt_bytes = (
                        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
                    ).encode()
                    receipt_file = staged / "receipt.json"
                    receipt_file.write_bytes(receipt_bytes)
                    for path in (db_path, config_copy, receipt_file):
                        path.chmod(0o600)
                        with path.open("rb") as handle:
                            os.fsync(handle.fileno())
                    target_release.verify()
                    if current_release is not None:
                        current_release.verify()
                    if _read(config_path) != raw:
                        raise OperationError("release_config_changed")
                    for path in (db_path, config_copy, receipt_file):
                        final = destination / path.name
                        os.link(path, final)
                        published.append(final)
                    committed = True
                    return BackupReceipt(
                        directory=destination,
                        database_sha256=database_sha,
                        config_sha256=_sha(raw),
                        schema=metadata.schema,
                        instance_id=metadata.instance_id,
                        bot_id=metadata.bot_id,
                        settings_revision=metadata.settings_revision,
                        settings_sha256=metadata.settings_sha256,
                        policy_revision=metadata.policy_revision,
                        counts=metadata.counts,
                        current_release=current_release,
                        target_release=target_release,
                        receipt_sha256=_sha(receipt_bytes),
                    )

                return await drain_on_cancel(asyncio.create_task(asyncio.to_thread(publish)))

            finally:
                if not committed:
                    for path in published:
                        path.unlink()
                shutil.rmtree(staged)
                if not committed:
                    destination.rmdir()

    def inspect_backup(
        self, directory: Path, *, expected_receipt_sha256: str,
        target_release: SourceRelease, current_release: SourceRelease | None = None,
    ) -> BackupReceipt:
        """Recover an exact private receipt after a lost HTTP response or restart."""
        raw = _read(directory / "receipt.json")
        if _sha(raw) != expected_receipt_sha256:
            raise OperationError("backup_changed")
        try:
            saved = _BackupDocument.model_validate_json(raw)
        except ValidationError as exc:
            raise OperationError("backup_invalid") from exc
        if (saved.target_release_sha256 != target_release.sha256
                or saved.current_release_sha256 != (current_release.sha256 if current_release else None)):
            raise OperationError("release_changed")
        receipt = BackupReceipt(
            directory.absolute(), saved.database_sha256, saved.config_sha256,
            saved.schema_version, saved.instance_id, saved.bot_id, saved.settings_revision,
            saved.settings_sha256, saved.policy_revision, saved.counts,
            current_release, target_release, expected_receipt_sha256,
        )
        self.manual_plan(receipt)
        return receipt

    def manual_plan(self, receipt: BackupReceipt, *, schema_compatible: bool = False) -> ManualUpdatePlan:
        """Verify the preparation before returning instructions; execute nothing."""
        if (
            not receipt.directory.is_dir()
            or _sha(_read(receipt.directory / "receipt.json")) != receipt.receipt_sha256
            or _sha(_read(receipt.directory / "state.sqlite3")) != receipt.database_sha256
            or _sha(_read(receipt.directory / "config.toml")) != receipt.config_sha256
        ):
            raise OperationError("backup_changed")
        self._config(_read(receipt.directory / "config.toml"))
        metadata = self._metadata(receipt.directory / "state.sqlite3")
        expected = _Snapshot(
            receipt.schema,
            receipt.instance_id,
            receipt.bot_id,
            receipt.settings_revision,
            receipt.settings_sha256,
            receipt.policy_revision,
            receipt.counts,
        )
        if metadata != expected:
            raise OperationError("backup_changed")
        receipt.target_release.verify()
        if receipt.current_release is not None:
            receipt.current_release.verify()
        blockers = () if receipt.current_release else ("current_release_unknown",)
        if not schema_compatible:
            blockers += ("schema_compatibility_unverified",)
        return ManualUpdatePlan(
            not blockers,
            blockers,
            (
                "Verify external credentials, current authorization/revocations and N6 cleaner separately.",
                "Stop only the selected instance's writer manually; preserve all new-data differences.",
                "Install the pinned target in an independent environment; "
                "verify identity/schema/config before ingress.",
            ),
            (
                "Use the pinned prior release and this DB/config snapshot together; "
                "never downgrade schema by code alone.",
                "Reconcile current revocations and post-snapshot data before restoring; "
                "do not replay unknown actions.",
            ),
            (
                "Preparation readiness is not permission to deploy or proof of complete runtime recovery.",
                "Revisions describe the point-in-time backup; later live writes may differ. "
                "The prior source archive is a caller declaration, not installed-release attestation.",
                "No credentials, model secrets, spool/key, external assets or service state are backed up.",
                "Original TTL and source permissions still apply; "
                "retained private data must not be resurrected.",
            ),
        )
