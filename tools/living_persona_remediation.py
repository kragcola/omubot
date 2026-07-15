"""Dry-run and apply narrowly scoped Living Persona data remediation."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.storage.backup import build_restore_plan


def _scope_predicate(allowed_group_ids: set[str]) -> tuple[str, tuple[str, ...]]:
    normalized = tuple(sorted(str(value).strip() for value in allowed_group_ids if str(value).strip()))
    if not normalized:
        return "(scope = 'global' AND scope_id = 'global')", ()
    placeholders = ", ".join("?" for _ in normalized)
    return (
        "((scope = 'global' AND scope_id = 'global') "
        f"OR (scope = 'group' AND scope_id IN ({placeholders})))",
        normalized,
    )


def _read_only_connection(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.expanduser().resolve()
    return sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=5.0)


def build_plan(
    db_path: str | Path,
    *,
    allowed_group_ids: set[str],
) -> dict[str, Any]:
    """Return a read-only remediation plan without changing any card."""
    path = Path(db_path)
    valid_sql, valid_params = _scope_predicate(allowed_group_ids)
    base = "source = 'dream_reflection' AND status = 'active'"
    with _read_only_connection(path) as connection:
        connection.row_factory = sqlite3.Row
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        invalid_rows = connection.execute(
            f"""
            SELECT card_id, scope, scope_id
            FROM memory_cards
            WHERE {base} AND NOT {valid_sql}
            ORDER BY card_id
            """,
            valid_params,
        ).fetchall()
        valid_count = int(connection.execute(
            f"SELECT COUNT(*) FROM memory_cards WHERE {base} AND {valid_sql}",
            valid_params,
        ).fetchone()[0])
        by_scope = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT scope, scope_id, COUNT(*) AS count
                FROM memory_cards
                WHERE {base} AND NOT {valid_sql}
                GROUP BY scope, scope_id
                ORDER BY count DESC, scope, scope_id
                """,
                valid_params,
            ).fetchall()
        ]
    return {
        "db_path": str(path),
        "quick_check": quick_check,
        "allowed_group_ids": sorted(allowed_group_ids),
        "valid_active_count": valid_count,
        "invalid_active_count": len(invalid_rows),
        "candidate_card_ids": [str(row["card_id"]) for row in invalid_rows],
        "invalid_by_scope": by_scope,
        "action": "expire_only",
    }


def _trusted_memory_backup_dir(backup_root: Path, backup_id: str) -> Path | None:
    matches: list[Path] = []
    for manifest_path in backup_root.rglob("manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("backup_id") != backup_id:
            continue
        matches.append(manifest_path.parent)

    if len(matches) != 1:
        return None
    backup_dir = matches[0]
    try:
        restore_plan = build_restore_plan(backup_dir, item_id="memory_cards")
    except (FileNotFoundError, KeyError, OSError, ValueError):
        return None
    if (
        restore_plan.manifest_schema_version != 2
        or restore_plan.legacy_manifest
        or not restore_plan.can_apply
        or len(restore_plan.items) != 1
    ):
        return None
    item = restore_plan.items[0]
    if (
        item.item_id != "memory_cards"
        or item.item_type != "sqlite"
        or item.compatibility not in {"compatible", "upgrade_on_start"}
    ):
        return None
    return backup_dir


def apply_plan(
    db_path: str | Path,
    *,
    allowed_group_ids: set[str],
    backup_root: str | Path,
    confirmed_backup_id: str,
) -> dict[str, Any]:
    """Expire planned cards after verifying an exact trusted backup manifest."""
    path = Path(db_path)
    normalized_allowed = {
        str(value).strip()
        for value in allowed_group_ids
        if str(value).strip()
    }
    if not normalized_allowed:
        raise ValueError(
            "apply requires a non-empty allowed_group_ids set; "
            "empty allowlist would mass-expire every non-global dream_reflection card"
        )
    backup_id = str(confirmed_backup_id or "").strip()
    backup_dir = (
        _trusted_memory_backup_dir(Path(backup_root), backup_id)
        if backup_id
        else None
    )
    if backup_dir is None:
        raise ValueError("a trusted backup containing memory_cards is required before apply")

    before = build_plan(path, allowed_group_ids=normalized_allowed)
    valid_sql, valid_params = _scope_predicate(normalized_allowed)
    updated_at = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(path, timeout=10.0) as connection:
        connection.execute("BEGIN IMMEDIATE")
        # Re-validate the trusted backup still resolves uniquely before mutating.
        rechecked = _trusted_memory_backup_dir(Path(backup_root), backup_id)
        if rechecked is None or rechecked != backup_dir:
            connection.rollback()
            raise ValueError("a trusted backup containing memory_cards is required before apply")
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check.lower() != "ok":
            connection.rollback()
            raise ValueError(f"live memory_cards quick_check failed: {quick_check}")
        cursor = connection.execute(
            f"""
            UPDATE memory_cards
            SET status = 'expired', updated_at = ?
            WHERE source = 'dream_reflection'
              AND status = 'active'
              AND NOT {valid_sql}
            """,
            (updated_at, *valid_params),
        )
        expired_count = int(cursor.rowcount)
        connection.commit()

    after = build_plan(path, allowed_group_ids=normalized_allowed)
    return {
        "backup_id": backup_id,
        "backup_dir": str(backup_dir),
        "expired_count": expired_count,
        "before": before,
        "after": after,
        "live_quick_check": "ok",
    }


def load_allowed_group_ids(policy_path: str | Path) -> set[str]:
    payload = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("group policy must be a JSON object")
    values = payload.get("whitelist")
    if not isinstance(values, list):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="storage/memory_cards.db")
    parser.add_argument("--group-policy", default="config/group-policy.json")
    parser.add_argument("--allowed-group", action="append", default=[])
    parser.add_argument("--backup-root", default="storage/backups")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmed-backup-id", default="")
    args = parser.parse_args(argv)

    allowed = {
        str(value).strip()
        for value in args.allowed_group
        if str(value).strip()
    }
    if not allowed:
        allowed = load_allowed_group_ids(args.group_policy)

    if args.apply:
        result = apply_plan(
            args.db,
            allowed_group_ids=allowed,
            backup_root=args.backup_root,
            confirmed_backup_id=args.confirmed_backup_id,
        )
    else:
        result = build_plan(args.db, allowed_group_ids=allowed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
