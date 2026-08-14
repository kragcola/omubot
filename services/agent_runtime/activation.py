"""Fail-closed production activation profile for Agent Runtime v2."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SOURCE_NAMES = ("runtime", "memory", "worldbook", "operator", "invocation")
_EXPECTED_SCHEMA_VERSIONS = {
    "runtime": 2,
    "memory": 1,
    "worldbook": 1,
    "operator": 1,
    "invocation": 2,
}
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WORKER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,95}$")
_EVIDENCE_REF_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{2,239}$")
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9:_-]{1,119}$")
_MAX_TARGET_REF_LENGTH = 512


@dataclass(frozen=True, slots=True)
class ProductionSourceV1:
    """One explicit database and its recovery evidence."""

    name: str
    database_path: Path
    expected_schema_version: int
    backup_path: Path
    backup_sha256: str
    restore_evidence_ref: str
    rollback_evidence_ref: str


@dataclass(frozen=True, slots=True)
class ProductionAttestationSourceV1:
    """One immutable rollout evidence manifest pinned by the activation profile."""

    manifest_path: Path
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class ProductionActivationProfileV1:
    """Validated inputs needed before a production runtime can be wired."""

    worker_id: str
    max_workers: int
    principal_scopes: tuple[str, ...]
    allowed_target_refs: tuple[str, ...]
    sources: tuple[ProductionSourceV1, ...]
    attestation: ProductionAttestationSourceV1 | None

    def attestation_profile_fingerprint(self) -> str:
        """Bind evidence to the exact activation inputs without exposing them."""

        payload = {
            "worker_id": self.worker_id,
            "max_workers": self.max_workers,
            "principal_scopes": self.principal_scopes,
            "allowed_target_refs": self.allowed_target_refs,
            "sources": [
                {
                    "name": source.name,
                    "database_path": str(source.database_path),
                    "expected_schema_version": source.expected_schema_version,
                    "backup_path": str(source.backup_path),
                    "backup_sha256": source.backup_sha256,
                    "restore_evidence_ref": source.restore_evidence_ref,
                    "rollback_evidence_ref": source.rollback_evidence_ref,
                }
                for source in self.sources
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        repo_root: str | Path,
    ) -> ProductionActivationProfileV1 | None:
        """Build an activation profile without touching the filesystem.

        Disabled settings intentionally return ``None`` before inspecting source
        fields. This lets the default configuration stay dark without resolving
        or creating implicit paths.
        """

        if not bool(getattr(settings, "enabled", False)):
            return None

        root = Path(repo_root).resolve()
        worker_id = _clean_worker_id(getattr(settings, "worker_id", ""))
        max_workers = getattr(settings, "max_workers", None)
        if isinstance(max_workers, bool) or int(max_workers or 0) != 1:
            raise ValueError("production activation requires exactly one worker")
        principal_scopes = _clean_principal_scopes(
            getattr(settings, "principal_scopes", ())
        )
        allowed_target_refs = _clean_allowed_target_refs(
            getattr(settings, "allowed_target_refs", ())
        )
        attestation = _attestation_from_settings(
            getattr(settings, "attestation", None),
            repo_root=root,
        )
        if attestation is None:
            raise ValueError(
                "enabled production activation requires a digest-pinned attestation manifest"
            )

        sources: list[ProductionSourceV1] = []
        seen_database_paths: set[Path] = set()
        for name in _SOURCE_NAMES:
            raw = getattr(settings, name, None)
            if raw is None:
                raise ValueError(f"{name} source configuration is required")
            source = _source_from_settings(name=name, settings=raw, repo_root=root)
            if source.database_path in seen_database_paths:
                raise ValueError("production activation source paths must be distinct")
            seen_database_paths.add(source.database_path)
            sources.append(source)
        return cls(
            worker_id=worker_id,
            max_workers=1,
            principal_scopes=principal_scopes,
            allowed_target_refs=allowed_target_refs,
            sources=tuple(sources),
            attestation=attestation,
        )

    async def preflight(self) -> dict[str, Any]:
        """Read source and backup integrity without creating or changing files."""

        checks = await asyncio.gather(
            *(_preflight_source(source) for source in self.sources)
        )
        source_reports = {
            name: report
            for name, report in zip(_SOURCE_NAMES, checks, strict=True)
        }
        ready = all(report["status"] == "ready" for report in source_reports.values())
        return {
            "status": "ready" if ready else "not_ready",
            "sources": source_reports,
        }

    def source(self, name: str) -> ProductionSourceV1:
        for source in self.sources:
            if source.name == name:
                return source
        raise KeyError(name)


def _source_from_settings(
    *,
    name: str,
    settings: Any,
    repo_root: Path,
) -> ProductionSourceV1:
    expected_schema = _EXPECTED_SCHEMA_VERSIONS[name]
    configured_schema = getattr(settings, "expected_schema_version", None)
    if isinstance(configured_schema, bool) or configured_schema != expected_schema:
        raise ValueError(
            f"{name} expected_schema_version must be {expected_schema}"
        )
    backup_digest = _clean_digest(
        getattr(settings, "backup_sha256", ""),
        field=f"{name} backup_sha256",
    )
    return ProductionSourceV1(
        name=name,
        database_path=_resolve_database_path(
            getattr(settings, "db_path", ""),
            field=f"{name} db_path",
            repo_root=repo_root,
        ),
        expected_schema_version=expected_schema,
        backup_path=_resolve_backup_path(
            getattr(settings, "backup_path", ""),
            field=f"{name} backup_path",
            repo_root=repo_root,
        ),
        backup_sha256=backup_digest,
        restore_evidence_ref=_clean_evidence_ref(
            getattr(settings, "restore_evidence_ref", ""),
            field=f"{name} restore_evidence_ref",
        ),
        rollback_evidence_ref=_clean_evidence_ref(
            getattr(settings, "rollback_evidence_ref", ""),
            field=f"{name} rollback_evidence_ref",
        ),
    )


def _attestation_from_settings(
    settings: Any,
    *,
    repo_root: Path,
) -> ProductionAttestationSourceV1 | None:
    if settings is None:
        return None
    manifest_path = str(getattr(settings, "manifest_path", "") or "").strip()
    manifest_sha256 = str(getattr(settings, "manifest_sha256", "") or "").strip()
    if not manifest_path and not manifest_sha256:
        return None
    if not manifest_path or not manifest_sha256:
        raise ValueError(
            "attestation manifest_path and manifest_sha256 are both required"
        )
    resolved_path = _resolve_backup_path(
        manifest_path,
        field="attestation manifest_path",
        repo_root=repo_root,
    )
    if resolved_path.suffix != ".json":
        raise ValueError("attestation manifest_path must end in .json")
    return ProductionAttestationSourceV1(
        manifest_path=resolved_path,
        manifest_sha256=_clean_digest(
            manifest_sha256,
            field="attestation manifest_sha256",
        ),
    )


def _resolve_database_path(value: object, *, field: str, repo_root: Path) -> Path:
    resolved = _resolve_under_storage(value, field=field, repo_root=repo_root)
    if resolved.suffix != ".db":
        raise ValueError(f"{field} must end in .db")
    return resolved


def _resolve_backup_path(value: object, *, field: str, repo_root: Path) -> Path:
    return _resolve_under_storage(value, field=field, repo_root=repo_root)


def _resolve_under_storage(value: object, *, field: str, repo_root: Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field} is required")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError(f"{field} must be relative to the repository")
    resolved = (repo_root / candidate).resolve()
    storage_root = (repo_root / "storage").resolve()
    if not resolved.is_relative_to(storage_root):
        raise ValueError(f"{field} must be under storage")
    return resolved


def _clean_worker_id(value: object) -> str:
    worker_id = str(value or "").strip()
    if _WORKER_ID_RE.fullmatch(worker_id) is None:
        raise ValueError("worker_id is invalid")
    return worker_id


def _clean_principal_scopes(value: object) -> tuple[str, ...]:
    values = _clean_policy_sequence(value, field="principal_scopes")
    scopes: set[str] = set()
    for raw_scope in values:
        scope = str(raw_scope or "").strip()
        if _SCOPE_RE.fullmatch(scope) is None:
            raise ValueError("principal scope is invalid")
        scopes.add(scope)
    if not scopes:
        raise ValueError("principal_scopes must include at least one scope")
    return tuple(sorted(scopes))


def _clean_allowed_target_refs(value: object) -> tuple[str, ...]:
    values = _clean_policy_sequence(value, field="allowed_target_refs")
    targets: set[str] = set()
    for raw_target in values:
        target = str(raw_target or "").strip()
        if (
            not target
            or len(target) > _MAX_TARGET_REF_LENGTH
            or not target.isascii()
            or "*" in target
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in target)
        ):
            raise ValueError("allowed target reference is invalid")
        targets.add(target)
    if not targets:
        raise ValueError("allowed_target_refs must include at least one target")
    return tuple(sorted(targets))


def _clean_policy_sequence(value: object, *, field: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be an explicit sequence")
    return tuple(value)


def _clean_digest(value: object, *, field: str) -> str:
    digest = str(value or "").strip().lower()
    if _DIGEST_RE.fullmatch(digest) is None:
        raise ValueError(f"{field} is invalid")
    return digest


def _clean_evidence_ref(value: object, *, field: str) -> str:
    reference = str(value or "").strip()
    if _EVIDENCE_REF_RE.fullmatch(reference) is None:
        raise ValueError(f"{field} is invalid")
    return reference


async def _preflight_source(source: ProductionSourceV1) -> dict[str, str]:
    return await asyncio.to_thread(_preflight_source_sync, source)


def _preflight_source_sync(source: ProductionSourceV1) -> dict[str, str]:
    if not source.database_path.is_file():
        return {"status": "not_ready", "reason": "source_unavailable"}
    if not source.backup_path.is_file():
        return {"status": "not_ready", "reason": "backup_unavailable"}
    try:
        actual_digest = _sha256_file(source.backup_path)
    except OSError:
        return {"status": "not_ready", "reason": "backup_unavailable"}
    if actual_digest != source.backup_sha256:
        return {"status": "not_ready", "reason": "backup_integrity_failed"}
    try:
        uri = f"{source.database_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return {"status": "not_ready", "reason": "source_integrity_failed"}
    if user_version != source.expected_schema_version:
        return {"status": "not_ready", "reason": "schema_version_mismatch"}
    if integrity != "ok":
        return {"status": "not_ready", "reason": "source_integrity_failed"}
    return {"status": "ready", "reason": "verified"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


__all__ = [
    "ProductionActivationProfileV1",
    "ProductionAttestationSourceV1",
    "ProductionSourceV1",
]
