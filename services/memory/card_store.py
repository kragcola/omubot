"""CardStore: typed memory cards in SQLite with scope, confidence, and supersedes edges."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import aiosqlite
from loguru import logger

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
     supersedes, source, created_at, updated_at, last_seen_at, ttl_turns)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

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

    def __post_init__(self) -> None:
        if self.category not in _VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {self.category!r}, must be one of {sorted(_VALID_CATEGORIES)}")
        if self.scope not in _VALID_SCOPES:
            raise ValueError(f"Invalid scope: {self.scope!r}, must be one of {sorted(_VALID_SCOPES)}")


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
    )


class CardStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self, *, migrate_from_md: str | None = None) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_TABLE)
        for idx in _CREATE_INDEXES:
            await self._db.execute(idx)
        await self._db.commit()

        if migrate_from_md:
            cursor = await self._db.execute(_COUNT_ALL)
            count = (await cursor.fetchone())[0]
            if count == 0 and Path(migrate_from_md).exists():
                from services.memory.migrate import migrate_md_to_cards
                n = await migrate_md_to_cards(migrate_from_md, self)
                logger.info("CardStore migrated {} cards from {}", n, migrate_from_md)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

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
            ),
        )
        await self._db.commit()
        logger.debug("card added | id={} category={} scope={}/{}", card_id, card.category, card.scope, card.scope_id)
        return card_id

    async def update_card(self, card_id: str, **fields: Any) -> bool:
        if not fields:
            return False
        allowed = {"content", "category", "confidence", "priority", "status",
                   "supersedes", "last_seen_at", "ttl_turns", "scope", "scope_id"}
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
    # Dedup / Reinforce / Decay
    # ------------------------------------------------------------------

    async def find_similar(
        self,
        scope: str,
        scope_id: str,
        content: str,
        threshold: float = 0.5,
    ) -> Card | None:
        """Find an active card with Jaccard similarity >= threshold over character bigrams."""
        if len(content) < 3:
            return None
        content_bigrams = _bigrams(content)
        if not content_bigrams:
            return None
        cards = await self.get_entity_cards(scope, scope_id)
        best: Card | None = None
        best_score = threshold
        for card in cards:
            card_bigrams = _bigrams(card.content)
            score = _jaccard(content_bigrams, card_bigrams)
            if score > best_score:
                best_score = score
                best = card
        return best

    async def reinforce(self, card_id: str, bump: float = 0.05) -> bool:
        """Bump confidence (cap 1.0) and touch last_seen_at. Returns True if updated."""
        card = await self.get_card(card_id)
        if card is None:
            return False
        new_conf = min(1.0, card.confidence + bump)
        return await self.update_card(
            card_id, confidence=new_conf, last_seen_at=_now_iso(),
        )

    async def decay_confidence(self, config: Any) -> int:
        """Decay confidence of all active cards, expire those below confidence_floor.

        Returns count of expired cards.
        """
        if config is None:
            return 0
        floor: float = getattr(config, "confidence_floor", 0.25)
        if floor is None:
            floor = 0.25
        factor: float = getattr(config, "decay_per_day", 0.02)
        if factor is None:
            factor = 0.02
        if factor <= 0:
            return 0

        cursor = await self._db.execute(
            "UPDATE memory_cards SET confidence = confidence * ?, updated_at = ? "
            "WHERE status = 'active'",
            (1.0 - factor, _now_iso()),
        )
        await self._db.commit()
        decayed = cursor.rowcount

        cursor = await self._db.execute(
            "SELECT card_id FROM memory_cards WHERE status = 'active' AND confidence < ?",
            (floor,),
        )
        rows = await cursor.fetchall()
        expired = 0
        for row in rows:
            await self.expire_card(row["card_id"])
            expired += 1
        if expired:
            await self._db.commit()

        if decayed or expired:
            logger.info(
                "card_store decay | decayed={} expired={} factor={} floor={}",
                decayed, expired, factor, floor,
            )
        return expired

    async def expire_ttl_cards(self, ttl_config: Any) -> int:
        """Expire active cards past their TTL. Returns count of expired cards."""
        if ttl_config is None:
            return 0
        default_ttl: int = getattr(ttl_config, "default", 0) or 0
        per_category: dict[str, int] = getattr(ttl_config, "per_category", {}) or {}
        if default_ttl <= 0 and not per_category:
            return 0

        expired_count = 0
        now = datetime.now(tz=TZ_SHANGHAI)

        for category, ttl_days in per_category.items():
            if ttl_days <= 0:
                continue
            cutoff = now.replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            from datetime import timedelta
            cutoff = cutoff - timedelta(days=ttl_days)
            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
            cursor = await self._db.execute(
                "SELECT card_id FROM memory_cards "
                "WHERE category = ? AND status = 'active' AND updated_at < ?",
                (category, cutoff_str),
            )
            rows = await cursor.fetchall()
            for row in rows:
                await self.expire_card(row["card_id"])
                expired_count += 1

        if default_ttl > 0:
            from datetime import timedelta
            cutoff = now.replace(
                hour=0, minute=0, second=0, microsecond=0,
            ) - timedelta(days=default_ttl)
            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
            # Exclude categories that have their own TTL
            exclude_cats = [c for c, d in per_category.items() if d > 0]
            if exclude_cats:
                placeholders = ",".join("?" for _ in exclude_cats)
                cursor = await self._db.execute(
                    f"SELECT card_id FROM memory_cards "
                    f"WHERE status = 'active' AND updated_at < ? AND category NOT IN ({placeholders})",
                    (cutoff_str, *exclude_cats),
                )
            else:
                cursor = await self._db.execute(
                    "SELECT card_id FROM memory_cards WHERE status = 'active' AND updated_at < ?",
                    (cutoff_str,),
                )
            rows = await cursor.fetchall()
            for row in rows:
                await self.expire_card(row["card_id"])
                expired_count += 1

        if expired_count:
            await self._db.commit()
            logger.info(
                "card_store expired {} cards | default_ttl={}d per_category={}",
                expired_count, default_ttl, per_category,
            )

        return expired_count

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

    async def build_entity_prompt(
        self, scope: str, scope_id: str, *, pool_group_ids: list[str] | None = None,
    ) -> str:
        """Build a prompt block for an entity, optionally merging pool groups.

        When *pool_group_ids* is provided, cards from those groups are also
        included alongside the entity's own cards — enabling multi-group
        memory pools.
        """
        cards = list(await self.get_entity_cards(scope, scope_id))

        pool_cards_by_group: dict[str, list[Card]] = {}
        if pool_group_ids:
            for gid in pool_group_ids:
                if gid == scope_id:
                    continue
                g_cards = await self.get_entity_cards("group", gid)
                if g_cards:
                    pool_cards_by_group[gid] = list(g_cards)
                    cards.extend(g_cards)

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
            # Annotate cards from other groups in the pool
            if c.scope == "group" and c.scope_id and c.scope_id != scope_id:
                lines.append(f"[{cat_label}｜群{c.scope_id}] {c.content}")
            else:
                lines.append(f"[{cat_label}] {c.content}")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _generate_card_id() -> str:
    return "card_" + secrets.token_hex(4)


def _now_iso() -> str:
    return datetime.now(tz=TZ_SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S")


def _bigrams(text: str) -> set[str]:
    """Return set of character bigrams from text."""
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity coefficient."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无卡片"
    parts = []
    for cat, cnt in sorted(counts.items()):
        label = CATEGORY_LABELS.get(cat, cat)
        parts.append(f"{label}×{cnt}")
    return " ".join(parts)
