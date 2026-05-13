"""JSON API: /api/admin/learning/today — aggregate today's learning activity
across slang / style / stickers, for the dashboard's learning module.

Each source reports:
  - approved_today     (今日新入库)
  - reviewed_today     (今日审核过 — 含通过与否决)
  - pending            (当前排队待审条数)
  - latest: recent items (title / subtitle / status / time)
  - samples: up to 5 small preview items

Reads are best-effort: any failing source returns zeros and an `error` field,
never 500s the whole endpoint.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter

# UTC+8 (Asia/Shanghai) — match services' TZ_SHANGHAI behaviour.
TZ_SHANGHAI = timezone(timedelta(hours=8))

# Max items to return per "latest" list.
LATEST_LIMIT = 5


def _today_range_iso() -> tuple[str, str]:
    """Return [start, end) ISO strings in UTC+8 for today."""
    now = datetime.now(TZ_SHANGHAI)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _today_prefix() -> str:
    """YYYY-MM-DD prefix for LIKE queries against TEXT timestamp columns."""
    return datetime.now(TZ_SHANGHAI).date().isoformat()


async def _collect_slang(db_path: str) -> dict[str, Any]:
    """Count today's slang approvals + reviews; return latest approvals."""
    payload: dict[str, Any] = {
        "approved_today": 0,
        "reviewed_today": 0,
        "pending": 0,
        "today_hits": 0,
        "latest": [],
    }
    if not db_path or not Path(db_path).exists():
        return payload

    try:
        today_prefix = _today_prefix()
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM slang_terms "
                "WHERE status = 'approved' AND updated_at LIKE ?",
                (f"{today_prefix}%",),
            )
            row = await cur.fetchone()
            payload["approved_today"] = int(row["cnt"] or 0) if row else 0

            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM slang_terms WHERE updated_at LIKE ?",
                (f"{today_prefix}%",),
            )
            row = await cur.fetchone()
            payload["reviewed_today"] = int(row["cnt"] or 0) if row else 0

            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM slang_pending_candidates",
            )
            row = await cur.fetchone()
            payload["pending"] = int(row["cnt"] or 0) if row else 0

            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM slang_observations "
                "WHERE observed_at LIKE ?",
                (f"{today_prefix}%",),
            )
            row = await cur.fetchone()
            payload["today_hits"] = int(row["cnt"] or 0) if row else 0

            # latest approved today (title = term, subtitle = group or scope)
            cur = await db.execute(
                "SELECT term, meaning, group_id, updated_at, status "
                "FROM slang_terms "
                "WHERE status = 'approved' AND updated_at LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (f"{today_prefix}%", LATEST_LIMIT),
            )
            rows = await cur.fetchall()

            def _slang_subtitle(r: aiosqlite.Row) -> str:
                meaning = str(r["meaning"] or "").strip()
                if meaning:
                    return meaning[:60]
                gid = str(r["group_id"] or "").strip()
                return f"群 {gid}" if gid else ""

            payload["latest"] = [
                {
                    "title": str(r["term"] or "").strip() or "(未命名黑话)",
                    "subtitle": _slang_subtitle(r),
                    "time": _format_time(str(r["updated_at"] or "")),
                    "status": str(r["status"] or ""),
                }
                for r in rows
            ]
    except Exception as exc:  # noqa: BLE001 — best-effort reporting
        payload["error"] = str(exc)
    return payload


async def _collect_style(db_path: str) -> dict[str, Any]:
    """Count today's style expression approvals + reviews; return latest."""
    payload: dict[str, Any] = {
        "approved_today": 0,
        "reviewed_today": 0,
        "pending": 0,
        "latest": [],
    }
    if not db_path or not Path(db_path).exists():
        return payload

    try:
        today_prefix = _today_prefix()
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM style_expressions "
                "WHERE status = 'approved' AND updated_at LIKE ?",
                (f"{today_prefix}%",),
            )
            row = await cur.fetchone()
            payload["approved_today"] = int(row["cnt"] or 0) if row else 0

            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM style_expressions "
                "WHERE updated_at LIKE ?",
                (f"{today_prefix}%",),
            )
            row = await cur.fetchone()
            payload["reviewed_today"] = int(row["cnt"] or 0) if row else 0

            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM style_expressions WHERE status = 'pending'",
            )
            row = await cur.fetchone()
            payload["pending"] = int(row["cnt"] or 0) if row else 0

            cur = await db.execute(
                "SELECT situation, style, scope, group_id, updated_at, status "
                "FROM style_expressions "
                "WHERE status = 'approved' AND updated_at LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (f"{today_prefix}%", LATEST_LIMIT),
            )
            rows = await cur.fetchall()
            payload["latest"] = [
                {
                    "title": str(r["style"] or "").strip()[:40] or "(未命名表达)",
                    "subtitle": (
                        (str(r["situation"] or "").strip()[:60])
                        or (f"群 {r['group_id']}"
                            if r["scope"] == "group" and r["group_id"]
                            else str(r["scope"] or "global"))
                    ),
                    "time": _format_time(str(r["updated_at"] or "")),
                    "status": str(r["status"] or ""),
                }
                for r in rows
            ]
    except Exception as exc:  # noqa: BLE001
        payload["error"] = str(exc)
    return payload


def _collect_stickers(storage_dir: Path) -> dict[str, Any]:
    """Count today's sticker additions; return latest N.

    Sticker `created_at` is stored as UTC ISO-8601; convert to UTC+8
    before comparing to today's date. Prefix matching on UTC strings
    would miss 00:00–07:59 local time.
    """
    payload: dict[str, Any] = {
        "added_today": 0,
        "total": 0,
        "latest": [],
        "samples": [],
    }

    index_path = storage_dir / "stickers" / "index.json"
    if not index_path.exists():
        return payload

    try:
        with index_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        payload["error"] = str(exc)
        return payload

    stickers = raw.get("stickers") if isinstance(raw, dict) else None
    if not isinstance(stickers, dict):
        # some stores use top-level dict {id: entry}
        stickers = raw if isinstance(raw, dict) else {}

    today_start, today_end = _today_range_iso()
    today_start_dt = datetime.fromisoformat(today_start)
    today_end_dt = datetime.fromisoformat(today_end)

    today_added: list[tuple[str, dict[str, Any], datetime]] = []
    total = 0

    for sticker_id, entry in stickers.items():
        if not isinstance(entry, dict):
            continue
        total += 1
        created_raw = str(entry.get("created_at") or "")
        if not created_raw:
            continue
        try:
            created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            created_local = created_dt.astimezone(TZ_SHANGHAI)
        except Exception:
            continue
        if today_start_dt <= created_local < today_end_dt:
            today_added.append((sticker_id, entry, created_local))

    today_added.sort(key=lambda tup: tup[2], reverse=True)

    payload["total"] = total
    payload["added_today"] = len(today_added)
    payload["latest"] = [
        {
            "id": sid,
            "title": (str(entry.get("description") or "").strip()[:40]
                      or "(无描述)"),
            "subtitle": (str(entry.get("usage_hint") or "").strip()[:40]
                         or f"来源 {entry.get('source') or '未知'}"),
            "time": created_local.strftime("%H:%M"),
        }
        for sid, entry, created_local in today_added[:LATEST_LIMIT]
    ]
    payload["samples"] = [sid for sid, _, _ in today_added[:8]]
    return payload


def _format_time(ts: str) -> str:
    """Parse ISO-8601 timestamp, return HH:MM; on failure return original."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(TZ_SHANGHAI)
        return dt.strftime("%H:%M")
    except Exception:
        return ts[:16] if len(ts) >= 16 else ts


def create_learning_router(
    *,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _slang_db_path() -> str:
        store = getattr(ctx, "slang_store", None) if ctx is not None else None
        if store is not None:
            return str(getattr(store, "db_path", "") or getattr(store, "_db_path", "") or "")
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx is not None else Path("storage")
        return str(storage_dir / "slang.db")

    def _style_db_path() -> str:
        store = getattr(ctx, "style_store", None) if ctx is not None else None
        if store is not None:
            return str(getattr(store, "db_path", "") or getattr(store, "_db_path", "") or "")
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx is not None else Path("storage")
        return str(storage_dir / "style.db")

    def _storage_dir() -> Path:
        return Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx is not None else Path("storage")

    @router.get("/learning/today")
    async def learning_today() -> dict[str, Any]:
        slang = await _collect_slang(_slang_db_path())
        style = await _collect_style(_style_db_path())
        stickers = _collect_stickers(_storage_dir())

        total_new = (
            int(slang.get("approved_today") or 0)
            + int(style.get("approved_today") or 0)
            + int(stickers.get("added_today") or 0)
        )
        total_reviewed = (
            int(slang.get("reviewed_today") or 0)
            + int(style.get("reviewed_today") or 0)
        )

        return {
            "as_of": datetime.now(TZ_SHANGHAI).isoformat(),
            "total_new": total_new,
            "total_reviewed": total_reviewed,
            "slang": slang,
            "style": style,
            "stickers": stickers,
        }

    return router
