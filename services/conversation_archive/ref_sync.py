"""Best-effort sync from business evidence tables into archive refs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from services.conversation_archive.store import ConversationArchive

_L = logger.bind(channel="debug")


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


async def _table_exists(db: aiosqlite.Connection, table_name: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    )
    return await cursor.fetchone() is not None


async def sync_business_message_refs(
    archive: ConversationArchive,
    *,
    slang_db_path: str | Path | None = None,
    style_db_path: str | Path | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    """Backfill archive refs from business evidence tables without importing them."""
    result: dict[str, Any] = {
        "ok": True,
        "slang": {"scanned": 0, "linked": 0, "missing_message": 0, "skipped": 0},
        "style": {"scanned": 0, "linked": 0, "missing_message": 0, "skipped": 0},
    }
    safe_limit = max(1, min(int(limit or 5000), 20000))
    if slang_db_path:
        result["slang"] = await _sync_slang_refs(archive, Path(slang_db_path), safe_limit)
    if style_db_path:
        result["style"] = await _sync_style_refs(archive, Path(style_db_path), safe_limit)
    return result


async def _sync_slang_refs(
    archive: ConversationArchive,
    db_path: Path,
    limit: int,
) -> dict[str, int]:
    stats = {"scanned": 0, "linked": 0, "missing_message": 0, "skipped": 0}
    if not db_path.exists():
        return stats
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            if not await _table_exists(db, "slang_observations"):
                return stats
            cursor = await db.execute(
                """SELECT observation_id, term_id, group_id, message_id, raw_text, observed_at
                   FROM slang_observations
                   WHERE message_id IS NOT NULL AND group_id != ''
                   ORDER BY observed_at DESC
                   LIMIT ?""",
                (limit,),
            )
            rows = [_row_dict(row) for row in await cursor.fetchall()]
    except Exception as exc:
        _L.warning("conversation archive slang ref sync failed | db={} error={}", db_path, exc)
        return stats

    for row in rows:
        stats["scanned"] += 1
        ref_id = await archive.add_message_ref_for_platform_message(
            chat_type="group",
            chat_id=str(row.get("group_id") or ""),
            platform_message_id=row.get("message_id"),
            ref_owner="slang",
            ref_type="evidence",
            external_table="slang_observations",
            external_id=str(row.get("observation_id") or ""),
            snapshot_text=str(row.get("raw_text") or ""),
            meta={
                "term_id": str(row.get("term_id") or ""),
                "observed_at": str(row.get("observed_at") or ""),
                "synced_from": "slang_observations",
            },
        )
        if ref_id:
            stats["linked"] += 1
        else:
            stats["missing_message"] += 1
    return stats


async def _sync_style_refs(
    archive: ConversationArchive,
    db_path: Path,
    limit: int,
) -> dict[str, int]:
    stats = {"scanned": 0, "linked": 0, "missing_message": 0, "skipped": 0}
    if not db_path.exists():
        return stats
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            if not await _table_exists(db, "style_evidence"):
                return stats
            cursor = await db.execute(
                """SELECT evidence_id, expression_id, group_id, message_id, raw_text, observed_at
                   FROM style_evidence
                   WHERE message_id IS NOT NULL AND group_id != ''
                   ORDER BY observed_at DESC
                   LIMIT ?""",
                (limit,),
            )
            rows = [_row_dict(row) for row in await cursor.fetchall()]
    except Exception as exc:
        _L.warning("conversation archive style ref sync failed | db={} error={}", db_path, exc)
        return stats

    for row in rows:
        stats["scanned"] += 1
        ref_id = await archive.add_message_ref_for_platform_message(
            chat_type="group",
            chat_id=str(row.get("group_id") or ""),
            platform_message_id=row.get("message_id"),
            ref_owner="style",
            ref_type="evidence",
            external_table="style_evidence",
            external_id=str(row.get("evidence_id") or ""),
            snapshot_text=str(row.get("raw_text") or ""),
            meta={
                "expression_id": str(row.get("expression_id") or ""),
                "observed_at": str(row.get("observed_at") or ""),
                "synced_from": "style_evidence",
            },
        )
        if ref_id:
            stats["linked"] += 1
        else:
            stats["missing_message"] += 1
    return stats
