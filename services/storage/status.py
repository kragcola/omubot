"""Read-only status snapshots for the governed SQLite catalog."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from services.storage.catalog import (
    DEFAULT_DATABASE_CATALOG,
    DatabaseCatalog,
    DatabaseSpec,
)


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    db_id: str
    path: str
    owner: str
    clients: tuple[str, ...]
    exists: bool
    status: str
    detail: str
    size_bytes: int
    user_version: int | None
    target_user_version: int
    version_status: str
    journal_mode: str
    quick_check: str
    connection_profile: str
    backup_profile: str
    retention_profile: str
    critical: bool
    optional: bool
    sensitive: bool
    rebuildable: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DatabaseStatusSnapshot:
    items: tuple[DatabaseStatus, ...]
    total_size_bytes: int
    ok_count: int
    missing_count: int
    error_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "summary": {
                "total": len(self.items),
                "total_size_bytes": self.total_size_bytes,
                "ok_count": self.ok_count,
                "missing_count": self.missing_count,
                "error_count": self.error_count,
            },
        }


def inspect_database_catalog(
    repo_root: str | Path,
    *,
    catalog: DatabaseCatalog = DEFAULT_DATABASE_CATALOG,
) -> DatabaseStatusSnapshot:
    root = Path(repo_root).resolve()
    items = tuple(
        _inspect_database(root, catalog, spec)
        for spec in catalog.all()
    )
    return DatabaseStatusSnapshot(
        items=items,
        total_size_bytes=sum(item.size_bytes for item in items),
        ok_count=sum(item.status == "ok" for item in items),
        missing_count=sum(item.status == "missing" for item in items),
        error_count=sum(item.status == "error" for item in items),
    )


def _inspect_database(
    repo_root: Path,
    catalog: DatabaseCatalog,
    spec: DatabaseSpec,
) -> DatabaseStatus:
    path = catalog.resolve(repo_root, spec.id)
    common = {
        "db_id": spec.id,
        "path": spec.path,
        "owner": spec.owner,
        "clients": spec.clients,
        "target_user_version": spec.target_user_version,
        "connection_profile": spec.connection_profile.value,
        "backup_profile": spec.backup_profile.value,
        "retention_profile": spec.retention_profile.value,
        "critical": spec.critical,
        "optional": spec.optional,
        "sensitive": spec.sensitive,
        "rebuildable": spec.rebuildable,
    }
    if not path.exists():
        return DatabaseStatus(
            **common,
            exists=False,
            status="missing",
            detail="数据库文件尚不存在",
            size_bytes=0,
            user_version=None,
            version_status="missing",
            journal_mode="",
            quick_check="",
        )

    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
            quick_check = str(
                connection.execute("PRAGMA quick_check").fetchone()[0]
            )
            journal_mode = str(
                connection.execute("PRAGMA journal_mode").fetchone()[0]
            )
            user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
    except Exception as exc:
        return DatabaseStatus(
            **common,
            exists=True,
            status="error",
            detail=str(exc)[:180],
            size_bytes=_database_footprint_bytes(path),
            user_version=None,
            version_status="unknown",
            journal_mode="",
            quick_check="error",
        )

    if user_version > spec.target_user_version:
        version_status = "future"
    elif user_version < spec.target_user_version:
        version_status = "legacy"
    else:
        version_status = "current"
    quick_ok = quick_check.lower() == "ok"
    status = "ok" if quick_ok and version_status != "future" else "error"
    detail = (
        f"snapshot=live_ro, quick_check={quick_check}, journal={journal_mode}, "
        f"version={user_version}/{spec.target_user_version}"
    )
    return DatabaseStatus(
        **common,
        exists=True,
        status=status,
        detail=detail,
        size_bytes=_database_footprint_bytes(path),
        user_version=user_version,
        version_status=version_status,
        journal_mode=journal_mode,
        quick_check=quick_check,
    )


def _database_footprint_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (
            path,
            Path(f"{path}-wal"),
            Path(f"{path}-shm"),
            Path(f"{path}-journal"),
        )
        if candidate.exists()
    )
