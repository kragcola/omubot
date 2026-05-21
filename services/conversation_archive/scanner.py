"""Small compatibility helpers for archive-backed incremental scanners."""

from __future__ import annotations

from typing import Any

from loguru import logger

_L = logger.bind(channel="debug")


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def read_scan_batch(
    message_log: Any,
    *,
    scanner_name: str,
    group_id: str,
    limit: int,
    scanner_version: str = "",
    params_hash: str = "",
    required: bool = True,
    backtrack_window: int = 50,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read a scanner batch, falling back to the legacy recent-window API.

    Test doubles and old MessageLog-shaped objects only need ``query_recent``.
    Real ``ConversationArchive`` instances can expose ``read_scan_batch`` for
    cursor-backed reads.
    """
    archive_reader = getattr(message_log, "read_scan_batch", None)
    if callable(archive_reader):
        try:
            return await archive_reader(
                scanner_name=scanner_name,
                group_id=str(group_id),
                limit=int(limit),
                scanner_version=scanner_version,
                params_hash=params_hash,
                required=required,
                backtrack_window=backtrack_window,
                meta=meta,
            )
        except Exception as exc:
            _L.warning(
                "conversation archive scan failed, falling back to recent window | scanner={} group={} error={}",
                scanner_name,
                group_id,
                exc,
            )
    rows = await message_log.query_recent(str(group_id), limit=int(limit))
    return {
        "source": "legacy_fallback",
        "fallback_reason": "archive_unavailable",
        "scanner_name": scanner_name,
        "group_id": str(group_id),
        "rows": rows,
        "run_id": None,
        "from_message_pk": 0,
        "to_message_pk": 0,
        "backtrack_from_message_pk": 0,
        "cursor_status": "fallback",
        "needs_rescan": False,
        "can_advance": False,
    }


async def finish_scan_batch(
    message_log: Any,
    batch: dict[str, Any],
    *,
    status: str,
    scanned_count: int = 0,
    extracted_count: int = 0,
    filtered_count: int = 0,
    saved_count: int = 0,
    error: str | None = None,
    advance_cursor: bool = True,
    meta: dict[str, Any] | None = None,
) -> None:
    """Finish a scanner batch when archive support is available."""
    if batch.get("source") != "archive":
        return
    finisher = getattr(message_log, "finish_scan_batch", None)
    if not callable(finisher):
        return
    try:
        await finisher(
            batch,
            status=status,
            scanned_count=scanned_count,
            extracted_count=extracted_count,
            filtered_count=filtered_count,
            saved_count=saved_count,
            error=error,
            advance_cursor=advance_cursor,
            meta=meta,
        )
    except Exception as exc:
        _L.warning(
            "conversation archive scan finish failed | scanner={} group={} status={} error={}",
            batch.get("scanner_name"),
            batch.get("group_id"),
            status,
            exc,
        )


async def add_evidence_message_ref(
    message_log: Any,
    *,
    group_id: str,
    source_row: dict[str, Any],
    ref_owner: str,
    ref_type: str = "evidence",
    external_table: str | None = None,
    external_id: str | None = None,
    snapshot_text: str | None = None,
    snapshot_json: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str | None:
    """Best-effort archive ref for a business evidence row."""
    message_pk = _int_or_none(source_row.get("message_pk"))
    if message_pk is not None:
        adder = getattr(message_log, "add_message_ref", None)
        if callable(adder):
            try:
                return await adder(
                    message_pk=message_pk,
                    ref_owner=ref_owner,
                    ref_type=ref_type,
                    external_table=external_table,
                    external_id=external_id,
                    snapshot_text=snapshot_text,
                    snapshot_json=snapshot_json,
                    meta=meta,
                )
            except Exception as exc:
                _L.warning(
                    "conversation archive evidence ref failed | owner={} table={} external_id={} error={}",
                    ref_owner,
                    external_table,
                    external_id,
                    exc,
                )
                return None

    platform_message_id = _int_or_none(source_row.get("message_id"))
    platform_adder = getattr(message_log, "add_message_ref_for_platform_message", None)
    if platform_message_id is None or not callable(platform_adder):
        return None
    try:
        return await platform_adder(
            chat_type="group",
            chat_id=str(group_id),
            platform_message_id=platform_message_id,
            ref_owner=ref_owner,
            ref_type=ref_type,
            external_table=external_table,
            external_id=external_id,
            snapshot_text=snapshot_text,
            snapshot_json=snapshot_json,
            meta=meta,
        )
    except Exception as exc:
        _L.warning(
            "conversation archive platform evidence ref failed | owner={} group={} message_id={} error={}",
            ref_owner,
            group_id,
            platform_message_id,
            exc,
        )
        return None
