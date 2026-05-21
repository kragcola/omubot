"""CardStore: typed memory cards in SQLite with scope, confidence, and supersedes edges."""

from __future__ import annotations

import contextlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import aiosqlite
from loguru import logger

from services.storage import close_with_checkpoint, connect_sqlite

if TYPE_CHECKING:
    pass

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

CATEGORY_LABELS: dict[str, str] = {
    "preference": "偏好",
    "boundary": "边界",
    "relationship": "关系",
    "event": "事件",
    "promise": "承诺",
    "fact": "事实",
    "status": "状态",
}

_VALID_CATEGORIES: frozenset[str] = frozenset(CATEGORY_LABELS.keys())
_VALID_SCOPES: frozenset[str] = frozenset(("user", "group", "global"))
_VALID_STATUSES: frozenset[str] = frozenset(("active", "superseded", "expired"))

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS memory_cards (
    card_id       TEXT PRIMARY KEY,
    category      TEXT NOT NULL,
    scope         TEXT NOT NULL,
    scope_id      TEXT NOT NULL,
    content       TEXT NOT NULL,
    confidence    REAL NOT NULL DEFAULT 0.7,
    status        TEXT NOT NULL DEFAULT 'active',
    priority      INTEGER NOT NULL DEFAULT 5,
    supersedes    TEXT,
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_seen_at  TEXT,
    ttl_turns     INTEGER
)"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_cards_scope ON memory_cards(scope, scope_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_cards_category ON memory_cards(scope, scope_id, category, status)",
    "CREATE INDEX IF NOT EXISTS idx_cards_source ON memory_cards(source)",
]

_INSERT = """\
INSERT INTO memory_cards
    (card_id, category, scope, scope_id, content, confidence, status, priority,
     supersedes, source, created_at, updated_at, last_seen_at, ttl_turns, series_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

_CREATE_SERIES_TABLE = """\
CREATE TABLE IF NOT EXISTS card_series (
    series_id    TEXT PRIMARY KEY,
    series_key   TEXT NOT NULL,
    scope        TEXT NOT NULL,
    scope_id     TEXT NOT NULL,
    label        TEXT,
    source       TEXT NOT NULL DEFAULT 'system',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    meta_json    TEXT
)"""

_CREATE_SERIES_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_series_key ON card_series(series_key)",
    "CREATE INDEX IF NOT EXISTS idx_series_scope ON card_series(scope, scope_id)",
]

_SELECT_BY_ID = "SELECT * FROM memory_cards WHERE card_id = ?"
_SELECT_ENTITY = (
    "SELECT * FROM memory_cards WHERE scope = ? AND scope_id = ? AND status = ?"
)
_SELECT_ENTITY_CATEGORY = (
    "SELECT * FROM memory_cards WHERE scope = ? AND scope_id = ? AND status = ? AND category = ?"
)
_SELECT_ENTITIES = "SELECT DISTINCT scope_id FROM memory_cards WHERE scope = ? AND status = 'active'"
_COUNT_ENTITY = (
    "SELECT category, COUNT(*) as cnt FROM memory_cards "
    "WHERE scope = ? AND scope_id = ? AND status = 'active' GROUP BY category"
)
_COUNT_ALL = "SELECT COUNT(*) FROM memory_cards"


@dataclass
class Card:
    card_id: str
    category: str
    scope: str
    scope_id: str
    content: str
    confidence: float
    status: str
    priority: int
    supersedes: str | None
    source: str
    created_at: str
    updated_at: str
    last_seen_at: str | None
    ttl_turns: int | None
    series_id: str | None = None


@dataclass
class NewCard:
    category: str
    scope: str
    scope_id: str
    content: str
    confidence: float = 0.7
    priority: int = 5
    source: str = "manual"
    supersedes: str | None = None
    ttl_turns: int | None = None
    series_id: str | None = None

    def __post_init__(self) -> None:
        if self.category not in _VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {self.category!r}, must be one of {sorted(_VALID_CATEGORIES)}")
        if self.scope not in _VALID_SCOPES:
            raise ValueError(f"Invalid scope: {self.scope!r}, must be one of {sorted(_VALID_SCOPES)}")
        self.scope_id = self.scope_id.strip()
        if self.scope in {"user", "group"} and not self.scope_id:
            raise ValueError("scope_id is required for user/group cards")
        if self.scope == "global" and not self.scope_id:
            self.scope_id = "global"


@dataclass
class CardSeries:
    series_id: str
    series_key: str
    scope: str
    scope_id: str
    label: str | None
    source: str
    created_at: str
    updated_at: str
    meta_json: str | None


@dataclass
class NewCardSeries:
    series_key: str
    scope: str
    scope_id: str
    label: str | None = None
    source: str = "system"
    meta_json: str | None = None


def _row_to_card(row: aiosqlite.Row) -> Card:
    return Card(
        card_id=row["card_id"],
        category=row["category"],
        scope=row["scope"],
        scope_id=row["scope_id"],
        content=row["content"],
        confidence=row["confidence"],
        status=row["status"],
        priority=row["priority"],
        supersedes=row["supersedes"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_seen_at=row["last_seen_at"],
        ttl_turns=row["ttl_turns"],
        series_id=row["series_id"] if "series_id" in row.keys() else None,  # noqa: SIM118
    )


def _row_to_series(row: aiosqlite.Row) -> CardSeries:
    return CardSeries(
        series_id=row["series_id"],
        series_key=row["series_key"],
        scope=row["scope"],
        scope_id=row["scope_id"],
        label=row["label"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        meta_json=row["meta_json"],
    )


class CardStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self, *, migrate_from_md: str | None = None) -> None:
        self._db = await connect_sqlite(self._db_path)
        await self._db.execute(_CREATE_TABLE)
        for idx in _CREATE_INDEXES:
            await self._db.execute(idx)
        # Series table + migration
        await self._db.execute(_CREATE_SERIES_TABLE)
        for idx in _CREATE_SERIES_INDEXES:
            await self._db.execute(idx)
        with contextlib.suppress(Exception):
            await self._db.execute("ALTER TABLE memory_cards ADD COLUMN series_id TEXT")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_cards_series ON memory_cards(series_id)")
        await self._db.commit()

        await self._backfill_food_series()

        if migrate_from_md:
            cursor = await self._db.execute(_COUNT_ALL)
            count = (await cursor.fetchone())[0]
            if count == 0 and Path(migrate_from_md).exists():
                from services.memory.migrate import migrate_md_to_cards
                n = await migrate_md_to_cards(migrate_from_md, self)
                logger.info("CardStore migrated {} cards from {}", n, migrate_from_md)

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="cards")
            self._db = None

    async def _backfill_food_series(self) -> None:
        """Assign series_id to old food cards that predate the series feature."""
        migrated = 0

        # Repair early recommendation cards that were mistakenly stored as preferences.
        cursor = await self._db.execute(
            "SELECT DISTINCT scope, scope_id FROM memory_cards "
            "WHERE source = 'food_plugin' AND category = 'preference' AND content LIKE '推荐了%'",
        )
        for row in await cursor.fetchall():
            scope, scope_id = row["scope"], row["scope_id"]
            series = await self.get_or_create_series(
                f"food_served:{scope_id}", scope=scope, scope_id=scope_id,
                label="食物推荐记录", source="food_plugin",
            )
            cur = await self._db.execute(
                "UPDATE memory_cards SET category = 'event', series_id = ?, updated_at = ? "
                "WHERE source = 'food_plugin' AND category = 'preference' "
                "AND content LIKE '推荐了%' AND scope = ? AND scope_id = ?",
                (series.series_id, _now_iso(), scope, scope_id),
            )
            migrated += cur.rowcount

        # Food event cards → food_served:{scope_id}
        cursor = await self._db.execute(
            "SELECT DISTINCT scope, scope_id FROM memory_cards "
            "WHERE source = 'food_plugin' AND category = 'event' AND series_id IS NULL",
        )
        for row in await cursor.fetchall():
            scope, scope_id = row["scope"], row["scope_id"]
            series = await self.get_or_create_series(
                f"food_served:{scope_id}", scope=scope, scope_id=scope_id,
                label="食物推荐记录", source="food_plugin",
            )
            cur = await self._db.execute(
                "UPDATE memory_cards SET series_id = ? "
                "WHERE source = 'food_plugin' AND category = 'event' "
                "AND scope = ? AND scope_id = ? AND series_id IS NULL",
                (series.series_id, scope, scope_id),
            )
            migrated += cur.rowcount

        # Food preference cards → food_pref:{scope_id}
        cursor = await self._db.execute(
            "SELECT DISTINCT scope, scope_id FROM memory_cards "
            "WHERE source = 'food_plugin' AND category = 'preference' AND series_id IS NULL",
        )
        for row in await cursor.fetchall():
            scope, scope_id = row["scope"], row["scope_id"]
            series = await self.get_or_create_series(
                f"food_pref:{scope_id}", scope=scope, scope_id=scope_id,
                label="食物口味偏好", source="food_plugin",
            )
            cur = await self._db.execute(
                "UPDATE memory_cards SET series_id = ? "
                "WHERE source = 'food_plugin' AND category = 'preference' "
                "AND scope = ? AND scope_id = ? AND series_id IS NULL",
                (series.series_id, scope, scope_id),
            )
            migrated += cur.rowcount

        # Also backfill preference cards with source='user_config' (from _add_preference)
        cursor = await self._db.execute(
            "SELECT DISTINCT scope, scope_id FROM memory_cards "
            "WHERE source = 'user_config' AND category = 'preference' "
            "AND (content LIKE '喜欢吃%' OR content LIKE '不喜欢吃%') AND series_id IS NULL",
        )
        for row in await cursor.fetchall():
            scope, scope_id = row["scope"], row["scope_id"]
            series = await self.get_or_create_series(
                f"food_pref:{scope_id}", scope=scope, scope_id=scope_id,
                label="食物口味偏好", source="food_plugin",
            )
            cur = await self._db.execute(
                "UPDATE memory_cards SET series_id = ? "
                "WHERE source = 'user_config' AND category = 'preference' "
                "AND (content LIKE '喜欢吃%' OR content LIKE '不喜欢吃%') "
                "AND scope = ? AND scope_id = ? AND series_id IS NULL",
                (series.series_id, scope, scope_id),
            )
            migrated += cur.rowcount

        await self._db.commit()
        if migrated:
            logger.info("CardStore backfilled {} food cards into series", migrated)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def add_card(self, card: NewCard) -> str:
        card_id = _generate_card_id()
        now = _now_iso()
        await self._db.execute(
            _INSERT,
            (
                card_id, card.category, card.scope, card.scope_id, card.content,
                card.confidence, "active", card.priority,
                card.supersedes, card.source, now, now, None, card.ttl_turns,
                card.series_id,
            ),
        )
        await self._db.commit()
        logger.debug("card added | id={} category={} scope={}/{}", card_id, card.category, card.scope, card.scope_id)
        return card_id

    async def update_card(self, card_id: str, **fields: Any) -> bool:
        if not fields:
            return False
        allowed = {"content", "category", "confidence", "priority", "status",
                   "supersedes", "last_seen_at", "ttl_turns", "scope", "scope_id", "series_id"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*updates.values(), card_id]
        cursor = await self._db.execute(
            f"UPDATE memory_cards SET {set_clause} WHERE card_id = ?",
            values,
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def get_card(self, card_id: str) -> Card | None:
        cursor = await self._db.execute(_SELECT_BY_ID, (card_id,))
        row = await cursor.fetchone()
        return _row_to_card(row) if row else None

    async def get_entity_cards(
        self,
        scope: str,
        scope_id: str,
        *,
        status: str = "active",
        category: str | None = None,
    ) -> list[Card]:
        if category:
            cursor = await self._db.execute(_SELECT_ENTITY_CATEGORY, (scope, scope_id, status, category))
        else:
            cursor = await self._db.execute(_SELECT_ENTITY, (scope, scope_id, status))
        rows = await cursor.fetchall()
        cards = [_row_to_card(r) for r in rows]
        cards.sort(key=lambda c: (-c.priority, c.updated_at), reverse=False)
        return cards

    async def supersede_card(self, old_card_id: str, new_card: NewCard) -> str:
        new_card.supersedes = old_card_id
        new_id = await self.add_card(new_card)
        await self.update_card(old_card_id, status="superseded")
        return new_id

    async def mark_seen(self, card_id: str) -> bool:
        return await self.update_card(card_id, last_seen_at=_now_iso())

    async def expire_card(self, card_id: str) -> bool:
        return await self.update_card(card_id, status="expired")

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def list_entities(self, scope: str) -> list[str]:
        cursor = await self._db.execute(_SELECT_ENTITIES, (scope,))
        rows = await cursor.fetchall()
        return [r["scope_id"] for r in rows]

    async def count_entity_cards(self, scope: str, scope_id: str) -> dict[str, int]:
        cursor = await self._db.execute(_COUNT_ENTITY, (scope, scope_id))
        rows = await cursor.fetchall()
        return {r["category"]: r["cnt"] for r in rows}

    async def list_cards(
        self,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
        status: str = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> list[Card]:
        """List cards with optional scope/scope_id filters and pagination."""
        sql = "SELECT * FROM memory_cards WHERE status = ?"
        params: list[Any] = [status]
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        if scope_id:
            sql += " AND scope_id = ?"
            params.append(scope_id)
        sql += " ORDER BY priority DESC, updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_card(r) for r in rows]

    async def search_cards(self, query: str, *, scope: str | None = None, limit: int = 10) -> list[Card]:
        sql = "SELECT * FROM memory_cards WHERE status = 'active' AND content LIKE ?"
        params: list[Any] = [f"%{query}%"]
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        sql += " ORDER BY priority DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_card(r) for r in rows]

    # ------------------------------------------------------------------
    # Series CRUD
    # ------------------------------------------------------------------

    async def create_series(self, series: NewCardSeries) -> CardSeries:
        series_id = "ser_" + secrets.token_hex(4)
        now = _now_iso()
        await self._db.execute(
            "INSERT INTO card_series (series_id, series_key, scope, scope_id, label, source, "
            "created_at, updated_at, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (series_id, series.series_key, series.scope, series.scope_id,
             series.label, series.source, now, now, series.meta_json),
        )
        await self._db.commit()
        return CardSeries(
            series_id=series_id, series_key=series.series_key,
            scope=series.scope, scope_id=series.scope_id,
            label=series.label, source=series.source,
            created_at=now, updated_at=now, meta_json=series.meta_json,
        )

    async def get_series(self, series_id: str) -> CardSeries | None:
        cursor = await self._db.execute(
            "SELECT * FROM card_series WHERE series_id = ?", (series_id,))
        row = await cursor.fetchone()
        return _row_to_series(row) if row else None

    async def get_series_by_key(self, series_key: str) -> CardSeries | None:
        cursor = await self._db.execute(
            "SELECT * FROM card_series WHERE series_key = ?", (series_key,))
        row = await cursor.fetchone()
        return _row_to_series(row) if row else None

    async def get_or_create_series(
        self, series_key: str, scope: str, scope_id: str,
        *, label: str | None = None, source: str = "system",
    ) -> CardSeries:
        existing = await self.get_series_by_key(series_key)
        if existing:
            return existing
        return await self.create_series(NewCardSeries(
            series_key=series_key, scope=scope, scope_id=scope_id,
            label=label, source=source,
        ))

    async def get_series_cards(self, series_id: str, *, status: str = "active") -> list[Card]:
        cursor = await self._db.execute(
            "SELECT * FROM memory_cards WHERE series_id = ? AND status = ? ORDER BY created_at DESC",
            (series_id, status))
        rows = await cursor.fetchall()
        return [_row_to_card(r) for r in rows]

    async def list_entity_series(self, scope: str, scope_id: str) -> list[CardSeries]:
        cursor = await self._db.execute(
            "SELECT * FROM card_series WHERE scope = ? AND scope_id = ? ORDER BY created_at DESC",
            (scope, scope_id))
        rows = await cursor.fetchall()
        return [_row_to_series(r) for r in rows]

    async def list_all_series(self) -> list[CardSeries]:
        cursor = await self._db.execute(
            "SELECT * FROM card_series ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [_row_to_series(r) for r in rows]

    # ------------------------------------------------------------------
    # Similarity & reinforcement
    # ------------------------------------------------------------------

    async def find_similar(
        self, scope: str, scope_id: str, content: str,
        *, threshold: float = 0.6, category: str | None = None,
    ) -> Card | None:
        """Find an existing active card with similar content (prefix match)."""
        prefix = content.split("，")[0].split("。")[0].split("、")[0][:20]
        if len(prefix) < 2:
            return None
        sql = "SELECT * FROM memory_cards WHERE scope = ? AND scope_id = ? AND status = 'active' AND content LIKE ?"
        params: list[Any] = [scope, scope_id, f"{prefix}%"]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY confidence DESC LIMIT 1"
        cursor = await self._db.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        card = _row_to_card(row)
        return card if card.confidence >= threshold else None

    async def reinforce(self, card_id: str, boost: float = 0.1) -> bool:
        """Increase confidence of a card (cap at 1.0) and update last_seen_at."""
        card = await self.get_card(card_id)
        if card is None:
            return False
        new_conf = min(1.0, card.confidence + boost)
        return await self.update_card(card_id, confidence=new_conf, last_seen_at=_now_iso())

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    async def build_global_index(self) -> str:
        """Token-efficient entity overview with per-category counts."""
        user_ids = await self.list_entities("user")
        group_ids = await self.list_entities("group")

        parts: list[str] = ["【记忆索引】"]
        if user_ids:
            lines = []
            for uid in sorted(user_ids):
                counts = await self.count_entity_cards("user", uid)
                label = _format_counts(counts)
                lines.append(f"用户 @{uid}: {label}")
            parts.append("\n".join(lines))
        if group_ids:
            lines = []
            for gid in sorted(group_ids):
                counts = await self.count_entity_cards("group", gid)
                label = _format_counts(counts)
                lines.append(f"群 #{gid}: {label}")
            parts.append("\n".join(lines))

        if len(parts) == 1:
            return ""
        return "\n".join(parts)

    async def build_entity_prompt(self, scope: str, scope_id: str) -> str:
        cards = await self.get_entity_cards(scope, scope_id)
        if not cards:
            label = "用户" if scope == "user" else "群" if scope == "group" else "全局"
            return f"【{label}记忆 / {scope_id}】\n暂无记录"
        if scope == "user":
            header = f"【用户记忆 / @{scope_id}】"
        elif scope == "group":
            header = f"【群记忆 / #{scope_id}】"
        else:
            header = "【全局记忆】"
        lines = [header]
        for c in cards:
            cat_label = CATEGORY_LABELS.get(c.category, c.category)
            lines.append(f"[{cat_label}] {c.content}")
        return "\n".join(lines)

    async def get_multi_scope_cards(
        self,
        scope: str,
        scope_ids: list[str],
        *,
        status: str = "active",
    ) -> list[Card]:
        """Combine cards from multiple scope_ids, deduplicated and sorted."""
        seen: set[str] = set()
        merged: list[Card] = []
        for sid in scope_ids:
            cards = await self.get_entity_cards(scope, sid, status=status)
            for c in cards:
                if c.card_id not in seen:
                    seen.add(c.card_id)
                    merged.append(c)
        merged.sort(key=lambda c: (-c.priority, c.updated_at), reverse=False)
        return merged

    async def build_entity_prompt_multi(self, scope: str, scope_ids: list[str]) -> str:
        """Build a prompt combining cards from multiple scope_ids (pool mode)."""
        cards = await self.get_multi_scope_cards(scope, scope_ids)
        if not cards:
            label = "用户" if scope == "user" else "群" if scope == "group" else "全局"
            display = scope_ids[0] if len(scope_ids) == 1 else ", ".join(scope_ids)
            return f"【{label}记忆 / {display}】\n暂无记录"
        if scope == "user":
            display = scope_ids[0] if len(scope_ids) == 1 else ", ".join(scope_ids[:3])
            header = f"【用户记忆 / @{display}】"
        elif scope == "group":
            display = scope_ids[0] if len(scope_ids) == 1 else ", ".join(scope_ids[:3])
            header = f"【群记忆 / #{display}】"
        else:
            header = "【全局记忆】"
        lines = [header]
        for c in cards:
            cat_label = CATEGORY_LABELS.get(c.category, c.category)
            lines.append(f"[{cat_label}] {c.content}")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _generate_card_id() -> str:
    return "card_" + secrets.token_hex(4)


def _now_iso() -> str:
    return datetime.now(tz=TZ_SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S")


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无卡片"
    parts = []
    for cat, cnt in sorted(counts.items()):
        label = CATEGORY_LABELS.get(cat, cat)
        parts.append(f"{label}×{cnt}")
    return " ".join(parts)
