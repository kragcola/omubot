"""CardStore: typed memory cards in SQLite with scope, confidence, and supersedes edges."""

from __future__ import annotations

import asyncio
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
# Visibility is distinct from storage scope. Missing/unknown → fail closed
# for cross-scope automatic recall (see services.memory.visibility).
_VALID_VISIBILITIES: frozenset[str] = frozenset(("private", "same_group", "global"))

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
    source_msg_id TEXT DEFAULT NULL,
    captured_at   TEXT DEFAULT NULL,
    captured_by   TEXT NOT NULL DEFAULT 'unknown',
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

_CREATE_OBSERVATIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS memory_card_observations (
    observation_id    TEXT PRIMARY KEY,
    card_id           TEXT NOT NULL,
    decision          TEXT NOT NULL,
    source_message_id TEXT,
    evidence_text     TEXT,
    observed_at       TEXT NOT NULL,
    captured_by       TEXT NOT NULL DEFAULT 'unknown',
    meta_json         TEXT
)"""

_CREATE_OBSERVATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_obs_card_id ON memory_card_observations(card_id)",
    "CREATE INDEX IF NOT EXISTS idx_obs_source_message_id "
    "ON memory_card_observations(source_message_id)",
    # Idempotency: same (card, source, decision) only once when source is present.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_card_source_decision_unique "
    "ON memory_card_observations(card_id, source_message_id, decision) "
    "WHERE source_message_id IS NOT NULL",
]

_INSERT = """\
INSERT INTO memory_cards
    (card_id, category, scope, scope_id, content, confidence, status, priority,
     supersedes, source, source_msg_id, captured_at, captured_by,
     created_at, updated_at, last_seen_at, ttl_turns, series_id,
     origin_group_id, visibility, subject_user_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

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
    source_msg_id: str | None
    captured_at: str | None
    captured_by: str
    created_at: str
    updated_at: str
    last_seen_at: str | None
    ttl_turns: int | None
    series_id: str | None = None
    # Visibility / provenance (additive; None on legacy rows → fail closed)
    origin_group_id: str | None = None
    visibility: str | None = None
    subject_user_id: str | None = None


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
    origin_group_id: str | None = None
    visibility: str | None = None
    subject_user_id: str | None = None

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
        if self.origin_group_id is not None:
            og = str(self.origin_group_id).strip()
            self.origin_group_id = og or None
        if self.visibility is not None:
            vis = str(self.visibility).strip().lower()
            if vis not in _VALID_VISIBILITIES:
                raise ValueError(
                    f"Invalid visibility: {self.visibility!r}, "
                    f"must be one of {sorted(_VALID_VISIBILITIES)}"
                )
            self.visibility = vis
        if self.subject_user_id is not None:
            sub = str(self.subject_user_id).strip()
            self.subject_user_id = sub or None


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


@dataclass
class CardObservation:
    observation_id: str
    card_id: str
    decision: str
    source_message_id: str | None
    evidence_text: str | None
    observed_at: str
    captured_by: str
    meta_json: str | None = None


def _row_to_card(row: aiosqlite.Row) -> Card:
    keys = row.keys()
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
        source_msg_id=row["source_msg_id"] if "source_msg_id" in keys else None,
        captured_at=row["captured_at"] if "captured_at" in keys else None,
        captured_by=row["captured_by"] if "captured_by" in keys else "unknown",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_seen_at=row["last_seen_at"],
        ttl_turns=row["ttl_turns"],
        series_id=row["series_id"] if "series_id" in keys else None,
        origin_group_id=row["origin_group_id"] if "origin_group_id" in keys else None,
        visibility=row["visibility"] if "visibility" in keys else None,
        subject_user_id=row["subject_user_id"] if "subject_user_id" in keys else None,
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


def _row_to_observation(row: aiosqlite.Row) -> CardObservation:
    return CardObservation(
        observation_id=row["observation_id"],
        card_id=row["card_id"],
        decision=row["decision"],
        source_message_id=row["source_message_id"],
        evidence_text=row["evidence_text"],
        observed_at=row["observed_at"],
        captured_by=row["captured_by"] if "captured_by" in row.keys() else "unknown",  # noqa: SIM118
        meta_json=row["meta_json"] if "meta_json" in row.keys() else None,  # noqa: SIM118
    )


def _normalize_source_msg_id(source_msg_id: Any) -> str | None:
    if source_msg_id is None:
        return None
    value = str(source_msg_id).strip()
    return value or None


def _generate_observation_id() -> str:
    return "obs_" + secrets.token_hex(8)


class CardStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        # Same-instance write serialization (supersede / reinforce / mutators).
        self._write_lock = asyncio.Lock()

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
        with contextlib.suppress(Exception):
            await self._db.execute("ALTER TABLE memory_cards ADD COLUMN source_msg_id TEXT DEFAULT NULL")
        with contextlib.suppress(Exception):
            await self._db.execute("ALTER TABLE memory_cards ADD COLUMN captured_at TEXT DEFAULT NULL")
        with contextlib.suppress(Exception):
            await self._db.execute(
                "ALTER TABLE memory_cards ADD COLUMN captured_by TEXT NOT NULL DEFAULT 'unknown'"
            )
        # Visibility / provenance (additive; NULL = legacy fail-closed for cross-scope)
        with contextlib.suppress(Exception):
            await self._db.execute(
                "ALTER TABLE memory_cards ADD COLUMN origin_group_id TEXT DEFAULT NULL"
            )
        with contextlib.suppress(Exception):
            await self._db.execute(
                "ALTER TABLE memory_cards ADD COLUMN visibility TEXT DEFAULT NULL"
            )
        with contextlib.suppress(Exception):
            await self._db.execute(
                "ALTER TABLE memory_cards ADD COLUMN subject_user_id TEXT DEFAULT NULL"
            )
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_cards_series ON memory_cards(series_id)")
        with contextlib.suppress(Exception):
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_cards_origin_group "
                "ON memory_cards(origin_group_id, visibility, status)"
            )
        # Write-policy observations (additive)
        await self._db.execute(_CREATE_OBSERVATIONS_TABLE)
        for idx in _CREATE_OBSERVATION_INDEXES:
            await self._db.execute(idx)
        await self._db.commit()

        await self._backfill_food_series()

        if migrate_from_md:
            cursor = await self._require_db().execute(_COUNT_ALL)
            count_row = await cursor.fetchone()
            count = count_row[0] if count_row else 0
            if count == 0 and Path(migrate_from_md).exists():
                from services.memory.migrate import migrate_md_to_cards
                n = await migrate_md_to_cards(migrate_from_md, self)
                logger.info("CardStore migrated {} cards from {}", n, migrate_from_md)

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="cards")
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("CardStore is not initialized")
        return self._db

    async def _backfill_food_series(self) -> None:
        """Assign series_id to old food cards that predate the series feature."""
        migrated = 0
        db = self._require_db()

        # Repair early recommendation cards that were mistakenly stored as preferences.
        cursor = await db.execute(
            "SELECT DISTINCT scope, scope_id FROM memory_cards "
            "WHERE source = 'food_plugin' AND category = 'preference' AND content LIKE '推荐了%'",
        )
        for row in await cursor.fetchall():
            scope, scope_id = row["scope"], row["scope_id"]
            series = await self.get_or_create_series(
                f"food_served:{scope_id}", scope=scope, scope_id=scope_id,
                label="食物推荐记录", source="food_plugin",
            )
            cur = await db.execute(
                "UPDATE memory_cards SET category = 'event', series_id = ?, updated_at = ? "
                "WHERE source = 'food_plugin' AND category = 'preference' "
                "AND content LIKE '推荐了%' AND scope = ? AND scope_id = ?",
                (series.series_id, _now_iso(), scope, scope_id),
            )
            migrated += cur.rowcount

        # Food event cards → food_served:{scope_id}
        cursor = await db.execute(
            "SELECT DISTINCT scope, scope_id FROM memory_cards "
            "WHERE source = 'food_plugin' AND category = 'event' AND series_id IS NULL",
        )
        for row in await cursor.fetchall():
            scope, scope_id = row["scope"], row["scope_id"]
            series = await self.get_or_create_series(
                f"food_served:{scope_id}", scope=scope, scope_id=scope_id,
                label="食物推荐记录", source="food_plugin",
            )
            cur = await db.execute(
                "UPDATE memory_cards SET series_id = ? "
                "WHERE source = 'food_plugin' AND category = 'event' "
                "AND scope = ? AND scope_id = ? AND series_id IS NULL",
                (series.series_id, scope, scope_id),
            )
            migrated += cur.rowcount

        # Food preference cards → food_pref:{scope_id}
        cursor = await db.execute(
            "SELECT DISTINCT scope, scope_id FROM memory_cards "
            "WHERE source = 'food_plugin' AND category = 'preference' AND series_id IS NULL",
        )
        for row in await cursor.fetchall():
            scope, scope_id = row["scope"], row["scope_id"]
            series = await self.get_or_create_series(
                f"food_pref:{scope_id}", scope=scope, scope_id=scope_id,
                label="食物口味偏好", source="food_plugin",
            )
            cur = await db.execute(
                "UPDATE memory_cards SET series_id = ? "
                "WHERE source = 'food_plugin' AND category = 'preference' "
                "AND scope = ? AND scope_id = ? AND series_id IS NULL",
                (series.series_id, scope, scope_id),
            )
            migrated += cur.rowcount

        # Also backfill preference cards with source='user_config' (from _add_preference)
        cursor = await db.execute(
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
            cur = await db.execute(
                "UPDATE memory_cards SET series_id = ? "
                "WHERE source = 'user_config' AND category = 'preference' "
                "AND (content LIKE '喜欢吃%' OR content LIKE '不喜欢吃%') "
                "AND scope = ? AND scope_id = ? AND series_id IS NULL",
                (series.series_id, scope, scope_id),
            )
            migrated += cur.rowcount

        await db.commit()
        if migrated:
            logger.info("CardStore backfilled {} food cards into series", migrated)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def add_card(
        self,
        card: NewCard,
        *,
        source_msg_id: str | None = None,
        captured_at: str | None = None,
        captured_by: str = "unknown",
    ) -> str:
        async with self._write_lock:
            return await self._add_card_unlocked(
                card,
                source_msg_id=source_msg_id,
                captured_at=captured_at,
                captured_by=captured_by,
            )

    async def _add_card_unlocked(
        self,
        card: NewCard,
        *,
        source_msg_id: str | None = None,
        captured_at: str | None = None,
        captured_by: str = "unknown",
        commit: bool = True,
    ) -> str:
        card_id = _generate_card_id()
        now = _now_iso()
        source_msg_id_value = str(source_msg_id).strip() if source_msg_id is not None else None
        if source_msg_id_value == "":
            source_msg_id_value = None
        captured_by_value = str(captured_by or "").strip() or "unknown"
        captured_at_value = str(captured_at).strip() if captured_at is not None else ""
        if not captured_at_value and source_msg_id_value is not None:
            captured_at_value = now
        db = self._require_db()
        await db.execute(
            _INSERT,
            (
                card_id, card.category, card.scope, card.scope_id, card.content,
                card.confidence, "active", card.priority,
                card.supersedes, card.source, source_msg_id_value,
                captured_at_value or None, captured_by_value,
                now, now, None, card.ttl_turns,
                card.series_id,
                card.origin_group_id,
                card.visibility,
                card.subject_user_id,
            ),
        )
        if commit:
            await db.commit()
        logger.debug("card added | id={} category={} scope={}/{}", card_id, card.category, card.scope, card.scope_id)
        return card_id

    async def update_card(self, card_id: str, **fields: Any) -> bool:
        async with self._write_lock:
            return await self._update_card_unlocked(card_id, **fields)

    async def _update_card_unlocked(self, card_id: str, **fields: Any) -> bool:
        if not fields:
            return False
        allowed = {
            "content", "category", "confidence", "priority", "status",
            "supersedes", "last_seen_at", "ttl_turns", "scope", "scope_id",
            "series_id", "origin_group_id", "visibility", "subject_user_id",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*updates.values(), card_id]
        db = self._require_db()
        cursor = await db.execute(
            f"UPDATE memory_cards SET {set_clause} WHERE card_id = ?",
            values,
        )
        await db.commit()
        return cursor.rowcount > 0

    async def get_card(self, card_id: str) -> Card | None:
        cursor = await self._require_db().execute(_SELECT_BY_ID, (card_id,))
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
        db = self._require_db()
        if category:
            cursor = await db.execute(_SELECT_ENTITY_CATEGORY, (scope, scope_id, status, category))
        else:
            cursor = await db.execute(_SELECT_ENTITY, (scope, scope_id, status))
        rows = await cursor.fetchall()
        cards = [_row_to_card(r) for r in rows]
        cards.sort(key=lambda c: (-c.priority, c.updated_at), reverse=False)
        return cards

    async def supersede_card(
        self,
        old_card_id: str,
        new_card: NewCard,
        *,
        source_msg_id: str | None = None,
        source_message_id: str | None = None,
        captured_by: str = "unknown",
        evidence_text: str | None = None,
        evidence: str | None = None,
        meta_json: str | None = None,
    ) -> str:
        """Insert *new_card*, mark *old_card_id* superseded, optionally record obs.

        Runs under the same-instance write lock with BEGIN IMMEDIATE so concurrent
        supersedes of the same card serialize cleanly: exactly one successor, and
        the loser fails without orphans. The new card must keep the same owner
        scope/scope_id; trusted Dream/tool callers may correct its category.
        """
        # Accept either source_msg_id or source_message_id alias.
        src = _normalize_source_msg_id(
            source_msg_id if source_msg_id is not None else source_message_id
        )
        evidence_value = evidence_text if evidence_text is not None else evidence
        # Provenance/observation only when evidence or source is supplied.
        # Dream/tools call with default captured_by="unknown" and no evidence —
        # keep legacy insert+status behaviour without observation rows.
        has_provenance = bool(
            src is not None
            or (evidence_value is not None and str(evidence_value).strip())
        )

        async with self._write_lock:
            db = self._require_db()
            # Reserve a write lock at the SQLite level before any mutation.
            await db.execute("BEGIN IMMEDIATE")
            try:
                # Re-read / validate old card INSIDE the transaction.
                cursor = await db.execute(_SELECT_BY_ID, (old_card_id,))
                row = await cursor.fetchone()
                if row is None:
                    raise ValueError(
                        f"cannot supersede non-active or missing card: {old_card_id!r}"
                    )
                old = _row_to_card(row)
                if old.status != "active":
                    raise ValueError(
                        f"cannot supersede non-active or missing card: {old_card_id!r}"
                    )

                # Ownership cannot move across scopes. Category corrections are
                # allowed for trusted Dream/tool callers; MemoExtractor enforces
                # same-category targets before reaching the store.
                if (
                    new_card.scope != old.scope
                    or new_card.scope_id != old.scope_id
                ):
                    raise ValueError(
                        "supersede new card must match old scope/scope_id: "
                        f"old=({old.scope!r},{old.scope_id!r},{old.category!r}) "
                        f"new=({new_card.scope!r},{new_card.scope_id!r},{new_card.category!r})"
                    )

                new_card.supersedes = old_card_id
                card_id = _generate_card_id()
                now = _now_iso()
                captured_by_value = str(captured_by or "").strip() or "unknown"
                captured_at_value = now if src is not None else None

                # Conditional status flip: only one concurrent winner gets rowcount==1.
                status_cur = await db.execute(
                    "UPDATE memory_cards SET status = ?, updated_at = ? "
                    "WHERE card_id = ? AND status = 'active'",
                    ("superseded", now, old_card_id),
                )
                if status_cur.rowcount != 1:
                    raise ValueError(
                        f"cannot supersede non-active or missing card: {old_card_id!r}"
                    )

                # Inline insert (do not call add_card — would re-enter write lock).
                # Preserve visibility metadata from the new card; if omitted,
                # inherit origin/visibility/subject from the superseded card so
                # write-policy supersede does not silently strip scope metadata.
                origin_group_id = (
                    new_card.origin_group_id
                    if new_card.origin_group_id is not None
                    else old.origin_group_id
                )
                visibility = (
                    new_card.visibility
                    if new_card.visibility is not None
                    else old.visibility
                )
                subject_user_id = (
                    new_card.subject_user_id
                    if new_card.subject_user_id is not None
                    else old.subject_user_id
                )
                await db.execute(
                    _INSERT,
                    (
                        card_id,
                        new_card.category,
                        new_card.scope,
                        new_card.scope_id,
                        new_card.content,
                        new_card.confidence,
                        "active",
                        new_card.priority,
                        old_card_id,
                        new_card.source,
                        src,
                        captured_at_value,
                        captured_by_value,
                        now,
                        now,
                        None,
                        new_card.ttl_turns,
                        new_card.series_id,
                        origin_group_id,
                        visibility,
                        subject_user_id,
                    ),
                )
                if has_provenance:
                    obs_id = _generate_observation_id()
                    await db.execute(
                        "INSERT INTO memory_card_observations "
                        "(observation_id, card_id, decision, source_message_id, "
                        "evidence_text, observed_at, captured_by, meta_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            obs_id,
                            card_id,
                            "supersede",
                            src,
                            (
                                str(evidence_value).strip()
                                if evidence_value is not None
                                else None
                            )
                            or None,
                            now,
                            captured_by_value,
                            meta_json,
                        ),
                    )
                await db.commit()
            except BaseException:
                with contextlib.suppress(Exception):
                    await db.rollback()
                raise

            logger.debug(
                "card superseded | old={} new={} category={}",
                old_card_id,
                card_id,
                new_card.category,
            )
            return card_id

    async def mark_seen(self, card_id: str) -> bool:
        return await self.update_card(card_id, last_seen_at=_now_iso())

    async def expire_card(self, card_id: str) -> bool:
        return await self.update_card(card_id, status="expired")

    async def list_observations(
        self,
        card_id: str,
        *,
        limit: int | None = None,
    ) -> list[CardObservation]:
        """List observations for a card.

        ``limit`` is optional and backward-compatible: omit for all rows
        (legacy callers). When provided, returns at most ``limit`` rows in
        observed_at ASC order.
        """
        sql = (
            "SELECT * FROM memory_card_observations WHERE card_id = ? "
            "ORDER BY observed_at ASC, observation_id ASC"
        )
        params: list[Any] = [card_id]
        if limit is not None:
            lim = max(0, int(limit))
            if lim == 0:
                return []
            sql += " LIMIT ?"
            params.append(lim)
        cursor = await self._require_db().execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_observation(r) for r in rows]

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def list_entities(self, scope: str) -> list[str]:
        cursor = await self._require_db().execute(_SELECT_ENTITIES, (scope,))
        rows = await cursor.fetchall()
        return [r["scope_id"] for r in rows]

    async def count_entity_cards(self, scope: str, scope_id: str) -> dict[str, int]:
        cursor = await self._require_db().execute(_COUNT_ENTITY, (scope, scope_id))
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
        cursor = await self._require_db().execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_card(r) for r in rows]

    async def list_active_chain_heads(
        self,
        scope: str,
        scope_id: str,
        *,
        limit: int = 24,
        category: str | None = None,
    ) -> list[Card]:
        """Active cards that supersede a parent (temporal-trace chain heads).

        Hard-capped at 24. Ordering is deterministic across calls.
        """
        limit = max(0, min(int(limit), 24))
        if limit == 0:
            return []
        sql = (
            "SELECT * FROM memory_cards "
            "WHERE scope = ? AND scope_id = ? AND status = 'active' "
            "AND supersedes IS NOT NULL AND supersedes != ''"
        )
        params: list[Any] = [scope, scope_id]
        if category is not None:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY updated_at DESC, card_id ASC LIMIT ?"
        params.append(limit)
        cursor = await self._require_db().execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_card(r) for r in rows]

    async def walk_supersedes_chain(
        self,
        head_card_id: str,
        *,
        max_depth: int = 4,
    ) -> list[Card]:
        """Walk head → parents via ``supersedes`` pointers (head first).

        Fail-closed: returns ``[]`` on missing head/parent, non-active head,
        non-superseded parent, cross-scope/category, or cycle.

        Public ``max_depth`` is hard-clamped to 4 (temporal-trace v1 bound).
        """
        max_depth = max(0, min(int(max_depth), 4))
        if max_depth == 0 or not head_card_id:
            return []
        head = await self.get_card(head_card_id)
        if head is None or head.status != "active":
            return []
        chain: list[Card] = [head]
        seen: set[str] = {head.card_id}
        current = head
        while len(chain) < max_depth:
            parent_id = (current.supersedes or "").strip()
            if not parent_id:
                break
            if parent_id in seen:
                return []
            parent = await self.get_card(parent_id)
            if parent is None:
                return []
            if parent.status != "superseded":
                return []
            if (
                parent.scope != head.scope
                or parent.scope_id != head.scope_id
                or parent.category != head.category
            ):
                return []
            seen.add(parent.card_id)
            chain.append(parent)
            current = parent
        return chain

    async def search_cards(self, query: str, *, scope: str | None = None, limit: int = 10) -> list[Card]:
        sql = "SELECT * FROM memory_cards WHERE status = 'active' AND content LIKE ?"
        params: list[Any] = [f"%{query}%"]
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        sql += " ORDER BY priority DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._require_db().execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_card(r) for r in rows]

    # ------------------------------------------------------------------
    # Series CRUD
    # ------------------------------------------------------------------

    async def create_series(self, series: NewCardSeries) -> CardSeries:
        async with self._write_lock:
            return await self._create_series_unlocked(series)

    async def _create_series_unlocked(self, series: NewCardSeries) -> CardSeries:
        series_id = "ser_" + secrets.token_hex(4)
        now = _now_iso()
        db = self._require_db()
        await db.execute(
            "INSERT INTO card_series (series_id, series_key, scope, scope_id, label, source, "
            "created_at, updated_at, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (series_id, series.series_key, series.scope, series.scope_id,
             series.label, series.source, now, now, series.meta_json),
        )
        await db.commit()
        return CardSeries(
            series_id=series_id, series_key=series.series_key,
            scope=series.scope, scope_id=series.scope_id,
            label=series.label, source=series.source,
            created_at=now, updated_at=now, meta_json=series.meta_json,
        )

    async def get_series(self, series_id: str) -> CardSeries | None:
        cursor = await self._require_db().execute(
            "SELECT * FROM card_series WHERE series_id = ?", (series_id,))
        row = await cursor.fetchone()
        return _row_to_series(row) if row else None

    async def get_series_by_key(self, series_key: str) -> CardSeries | None:
        cursor = await self._require_db().execute(
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
        async with self._write_lock:
            # Re-check under lock to avoid races / double create.
            existing = await self.get_series_by_key(series_key)
            if existing:
                return existing
            return await self._create_series_unlocked(NewCardSeries(
                series_key=series_key, scope=scope, scope_id=scope_id,
                label=label, source=source,
            ))

    async def get_series_cards(self, series_id: str, *, status: str = "active") -> list[Card]:
        cursor = await self._require_db().execute(
            "SELECT * FROM memory_cards WHERE series_id = ? AND status = ? ORDER BY created_at DESC",
            (series_id, status))
        rows = await cursor.fetchall()
        return [_row_to_card(r) for r in rows]

    async def list_entity_series(self, scope: str, scope_id: str) -> list[CardSeries]:
        cursor = await self._require_db().execute(
            "SELECT * FROM card_series WHERE scope = ? AND scope_id = ? ORDER BY created_at DESC",
            (scope, scope_id))
        rows = await cursor.fetchall()
        return [_row_to_series(r) for r in rows]

    async def list_all_series(self) -> list[CardSeries]:
        cursor = await self._require_db().execute(
            "SELECT * FROM card_series ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [_row_to_series(r) for r in rows]

    async def find_by_source_message_ids(
        self,
        message_ids: Any,
        *,
        allowed_scopes: set[tuple[str, str]] | None = None,
    ) -> list[Card]:
        """Return active cards whose ``source_msg_id`` is in ``message_ids``.

        Dedupes by card_id, orders by card_id ascending. When
        ``allowed_scopes`` is provided, only cards whose (scope, scope_id)
        pair is in that set are returned.
        """
        if not message_ids:
            return []
        mids: list[str] = []
        for raw in message_ids:
            if raw is None:
                continue
            mid = str(raw).strip()
            if not mid or mid in mids:
                continue
            mids.append(mid)
        if not mids:
            return []
        placeholders = ", ".join("?" for _ in mids)
        sql = (
            "SELECT * FROM memory_cards "
            f"WHERE status = 'active' AND source_msg_id IN ({placeholders})"
        )
        params: list[Any] = list(mids)
        if allowed_scopes is not None:
            scope_pairs = [
                (str(scope), str(scope_id))
                for scope, scope_id in allowed_scopes
                if scope is not None and scope_id is not None
            ]
            if not scope_pairs:
                return []
            scope_clauses = " OR ".join("(scope = ? AND scope_id = ?)" for _ in scope_pairs)
            sql += f" AND ({scope_clauses})"
            for scope, scope_id in scope_pairs:
                params.extend([scope, scope_id])
        sql += " ORDER BY card_id ASC"
        cursor = await self._require_db().execute(sql, tuple(params))
        cards = [_row_to_card(row) for row in await cursor.fetchall()]
        # Defensive dedupe by card_id (SQL should already be unique).
        seen: set[str] = set()
        result: list[Card] = []
        for card in cards:
            if card.card_id in seen:
                continue
            seen.add(card.card_id)
            result.append(card)
        return result

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
        cursor = await self._require_db().execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        card = _row_to_card(row)
        return card if card.confidence >= threshold else None

    async def reinforce(
        self,
        card_id: str,
        boost: float = 0.1,
        *,
        evidence_text: str | None = None,
        source_message_id: str | None = None,
        captured_by: str | None = None,
        decision: str = "reinforce",
        meta_json: str | None = None,
    ) -> bool:
        """Increase confidence of a card (cap at 1.0) and update last_seen_at.

        When evidence/source is supplied, confidence + observation land in the
        same transaction. Same (card_id, source_message_id, decision) is
        idempotent (unique partial index).
        """
        src = _normalize_source_msg_id(source_message_id)
        has_observation = bool(
            src is not None
            or (evidence_text is not None and str(evidence_text).strip())
        )

        async with self._write_lock:
            card = await self.get_card(card_id)
            if card is None:
                return False

            # Legacy path: confidence + last_seen only (under write lock, no nested lock).
            if not has_observation:
                new_conf = min(1.0, card.confidence + boost)
                return await self._update_card_unlocked(
                    card_id, confidence=new_conf, last_seen_at=_now_iso()
                )

            now = _now_iso()
            new_conf = min(1.0, card.confidence + boost)
            captured_by_value = str(captured_by or "").strip() or "unknown"
            decision_value = str(decision or "reinforce").strip() or "reinforce"
            evidence_value = (
                str(evidence_text).strip() if evidence_text is not None else None
            ) or None
            db = self._require_db()

            # Idempotent: if observation already exists, skip conf re-boost.
            if src is not None:
                cursor = await db.execute(
                    "SELECT observation_id FROM memory_card_observations "
                    "WHERE card_id = ? AND source_message_id = ? AND decision = ?",
                    (card_id, src, decision_value),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    return True

            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    "UPDATE memory_cards SET confidence = ?, last_seen_at = ?, "
                    "updated_at = ? WHERE card_id = ?",
                    (new_conf, now, now, card_id),
                )
                obs_id = _generate_observation_id()
                await db.execute(
                    "INSERT INTO memory_card_observations "
                    "(observation_id, card_id, decision, source_message_id, "
                    "evidence_text, observed_at, captured_by, meta_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        obs_id,
                        card_id,
                        decision_value,
                        src,
                        evidence_value,
                        now,
                        captured_by_value,
                        meta_json,
                    ),
                )
                await db.commit()
            except BaseException:
                with contextlib.suppress(Exception):
                    await db.rollback()
                raise
            return True

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
