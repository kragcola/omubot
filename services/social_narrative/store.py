"""SQLite store for group-scoped factual shared experiences."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from services.storage import close_with_checkpoint, connect_sqlite

_CREATE_EXPERIENCES = """
CREATE TABLE IF NOT EXISTS social_narrative_experiences (
    experience_id       TEXT PRIMARY KEY,
    group_id            TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    entity_kind         TEXT NOT NULL CHECK (entity_kind = 'factual'),
    evidence_message_id TEXT NOT NULL,
    evidence_time       TEXT NOT NULL,
    evidence_source     TEXT NOT NULL,
    user_text           TEXT NOT NULL,
    bot_reply           TEXT NOT NULL,
    affection_score     REAL,
    affection_tier      TEXT,
    climate_valence     REAL,
    climate_familiarity REAL,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'invalidated')),
    created_at          TEXT NOT NULL,
    invalidated_at      TEXT,
    invalidation_reason TEXT,
    UNIQUE (group_id, user_id, evidence_message_id, evidence_source)
)
"""

_CREATE_ENTITIES = """
CREATE TABLE IF NOT EXISTS social_narrative_entities (
    group_id             TEXT NOT NULL,
    user_id              TEXT NOT NULL,
    entity_kind          TEXT NOT NULL CHECK (entity_kind = 'factual'),
    interaction_count    INTEGER NOT NULL DEFAULT 0,
    last_experience_id   TEXT,
    last_interaction_at  TEXT,
    affection_score      REAL,
    affection_tier       TEXT,
    climate_valence      REAL,
    climate_familiarity  REAL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
)
"""

_CREATE_TOMBSTONES = """
CREATE TABLE IF NOT EXISTS social_narrative_evidence_tombstones (
    group_id            TEXT NOT NULL,
    evidence_message_id TEXT NOT NULL,
    invalidated_at      TEXT NOT NULL,
    reason              TEXT NOT NULL,
    PRIMARY KEY (group_id, evidence_message_id)
)
"""

_CREATE_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_social_experience_scope_time
    ON social_narrative_experiences(group_id, user_id, status, evidence_time DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_social_experience_evidence
    ON social_narrative_experiences(group_id, evidence_message_id, status)
    """,
)


@dataclass(frozen=True, slots=True)
class SocialExperience:
    experience_id: str
    group_id: str
    user_id: str
    entity_kind: str
    evidence_message_id: str
    evidence_time: str
    evidence_source: str
    user_text: str
    bot_reply: str
    status: str
    created_at: str
    affection_score: float | None = None
    affection_tier: str | None = None
    climate_valence: float | None = None
    climate_familiarity: float | None = None
    invalidated_at: str | None = None
    invalidation_reason: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _bounded_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _experience_id(
    *,
    group_id: str,
    user_id: str,
    evidence_message_id: str,
    evidence_source: str,
) -> str:
    raw = "\x1f".join((group_id, user_id, evidence_message_id, evidence_source))
    return "social_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _row_to_experience(row: aiosqlite.Row) -> SocialExperience:
    return SocialExperience(
        experience_id=str(row["experience_id"]),
        group_id=str(row["group_id"]),
        user_id=str(row["user_id"]),
        entity_kind=str(row["entity_kind"]),
        evidence_message_id=str(row["evidence_message_id"]),
        evidence_time=str(row["evidence_time"]),
        evidence_source=str(row["evidence_source"]),
        user_text=str(row["user_text"]),
        bot_reply=str(row["bot_reply"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        affection_score=row["affection_score"],
        affection_tier=row["affection_tier"],
        climate_valence=row["climate_valence"],
        climate_familiarity=row["climate_familiarity"],
        invalidated_at=row["invalidated_at"],
        invalidation_reason=row["invalidation_reason"],
    )


class SocialNarrativeStore:
    """Own factual, group-local social evidence inside ``memory_cards.db``."""

    def __init__(self, db_path: str | Path = "storage/memory_cards.db") -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        self._db = await connect_sqlite(self._db_path)
        await self._db.execute(_CREATE_EXPERIENCES)
        await self._db.execute(_CREATE_ENTITIES)
        await self._db.execute(_CREATE_TOMBSTONES)
        for statement in _CREATE_INDEXES:
            await self._db.execute(statement)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="social_narrative")
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SocialNarrativeStore is not initialized")
        return self._db

    async def record_shared_experience(
        self,
        *,
        group_id: str | None,
        user_id: str,
        evidence_message_id: str | int | None,
        evidence_time: str,
        evidence_source: str,
        user_text: str,
        bot_reply: str,
        entity_kind: str = "factual",
        relationship: dict[str, Any] | None = None,
    ) -> SocialExperience | None:
        group = _required_text(group_id, "group_id")
        user = _required_text(user_id, "user_id")
        message_id = _required_text(evidence_message_id, "evidence_message_id")
        observed_at = _required_text(evidence_time, "evidence_time")
        source = _required_text(evidence_source, "evidence_source")
        if str(entity_kind).strip().lower() != "factual":
            raise ValueError("real-person social narrative must use entity_kind=factual")
        user_snapshot = _bounded_text(user_text, 600)
        bot_snapshot = _bounded_text(bot_reply, 900)
        if not user_snapshot or not bot_snapshot:
            raise ValueError("user_text and bot_reply are required")

        experience_id = _experience_id(
            group_id=group,
            user_id=user,
            evidence_message_id=message_id,
            evidence_source=source,
        )
        created_at = _now_iso()
        values = relationship or {}
        accepted = await self._record_transaction(
            experience_id=experience_id,
            group_id=group,
            user_id=user,
            evidence_message_id=message_id,
            evidence_time=observed_at,
            evidence_source=source,
            user_text=user_snapshot,
            bot_reply=bot_snapshot,
            relationship=values,
            created_at=created_at,
        )
        if not accepted:
            return None
        row = await self._get_by_id(experience_id)
        if row is None:
            raise RuntimeError("social narrative insert did not produce a record")
        return row

    async def _record_transaction(
        self,
        *,
        experience_id: str,
        group_id: str,
        user_id: str,
        evidence_message_id: str,
        evidence_time: str,
        evidence_source: str,
        user_text: str,
        bot_reply: str,
        relationship: dict[str, Any],
        created_at: str,
    ) -> bool:
        db = self._require_db()
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                tombstone_cursor = await db.execute(
                    """
                    SELECT 1 FROM social_narrative_evidence_tombstones
                    WHERE group_id = ? AND evidence_message_id = ?
                    """,
                    (group_id, evidence_message_id),
                )
                if await tombstone_cursor.fetchone() is not None:
                    await db.commit()
                    return False
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO social_narrative_experiences
                        (experience_id, group_id, user_id, entity_kind,
                         evidence_message_id, evidence_time, evidence_source,
                         user_text, bot_reply, affection_score, affection_tier,
                         climate_valence, climate_familiarity, status, created_at)
                    VALUES (?, ?, ?, 'factual', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        experience_id,
                        group_id,
                        user_id,
                        evidence_message_id,
                        evidence_time,
                        evidence_source,
                        user_text,
                        bot_reply,
                        relationship.get("affection_score"),
                        relationship.get("affection_tier"),
                        relationship.get("climate_valence"),
                        relationship.get("climate_familiarity"),
                        created_at,
                    ),
                )
                if cursor.rowcount <= 0:
                    await db.commit()
                    return True
                await db.execute(
                """
                INSERT INTO social_narrative_entities
                    (group_id, user_id, entity_kind, interaction_count,
                     last_experience_id, last_interaction_at,
                     affection_score, affection_tier,
                     climate_valence, climate_familiarity,
                     created_at, updated_at)
                VALUES (?, ?, 'factual', 1, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id, user_id) DO UPDATE SET
                    interaction_count = interaction_count + 1,
                    last_experience_id = excluded.last_experience_id,
                    last_interaction_at = excluded.last_interaction_at,
                    affection_score = COALESCE(excluded.affection_score, affection_score),
                    affection_tier = COALESCE(excluded.affection_tier, affection_tier),
                    climate_valence = COALESCE(excluded.climate_valence, climate_valence),
                    climate_familiarity = COALESCE(
                        excluded.climate_familiarity,
                        climate_familiarity
                    ),
                    updated_at = excluded.updated_at
                """,
                (
                    group_id,
                    user_id,
                    experience_id,
                    evidence_time,
                    relationship.get("affection_score"),
                    relationship.get("affection_tier"),
                    relationship.get("climate_valence"),
                    relationship.get("climate_familiarity"),
                    created_at,
                    created_at,
                ),
                )
                await db.commit()
                return True
            except BaseException:
                await db.rollback()
                raise

    async def _get_by_id(self, experience_id: str) -> SocialExperience | None:
        cursor = await self._require_db().execute(
            "SELECT * FROM social_narrative_experiences WHERE experience_id = ?",
            (experience_id,),
        )
        row = await cursor.fetchone()
        return _row_to_experience(row) if row is not None else None

    async def recall(
        self,
        *,
        group_id: str,
        user_id: str,
        limit: int = 10,
    ) -> list[SocialExperience]:
        group = _required_text(group_id, "group_id")
        user = _required_text(user_id, "user_id")
        cursor = await self._require_db().execute(
            """
            SELECT * FROM social_narrative_experiences
            WHERE group_id = ? AND user_id = ? AND status = 'active'
            ORDER BY evidence_time DESC, created_at DESC
            LIMIT ?
            """,
            (group, user, max(1, min(50, int(limit)))),
        )
        return [_row_to_experience(row) for row in await cursor.fetchall()]

    async def projection_count(self, *, group_id: str, user_id: str) -> int:
        cursor = await self._require_db().execute(
            """
            SELECT interaction_count FROM social_narrative_entities
            WHERE group_id = ? AND user_id = ?
            """,
            (_required_text(group_id, "group_id"), _required_text(user_id, "user_id")),
        )
        row = await cursor.fetchone()
        return int(row["interaction_count"] or 0) if row is not None else 0

    async def invalidate_evidence(
        self,
        *,
        group_id: str,
        evidence_message_id: str | int,
        reason: str = "source_message_invalidated",
    ) -> int:
        group = _required_text(group_id, "group_id")
        message_id = _required_text(evidence_message_id, "evidence_message_id")
        db = self._require_db()
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                invalidated_at = _now_iso()
                await db.execute(
                    """
                    INSERT OR IGNORE INTO social_narrative_evidence_tombstones
                        (group_id, evidence_message_id, invalidated_at, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        group,
                        message_id,
                        invalidated_at,
                        _bounded_text(reason, 180),
                    ),
                )
                cursor = await db.execute(
                    """
                    SELECT DISTINCT user_id FROM social_narrative_experiences
                    WHERE group_id = ? AND evidence_message_id = ? AND status = 'active'
                    """,
                    (group, message_id),
                )
                users = [str(row["user_id"]) for row in await cursor.fetchall()]
                if not users:
                    await db.commit()
                    return 0
                updated = await db.execute(
                    """
                    UPDATE social_narrative_experiences
                    SET status = 'invalidated', invalidated_at = ?, invalidation_reason = ?
                    WHERE group_id = ? AND evidence_message_id = ? AND status = 'active'
                    """,
                    (invalidated_at, _bounded_text(reason, 180), group, message_id),
                )
                for user in users:
                    await self._rebuild_projection(group, user, updated_at=invalidated_at)
                await db.commit()
                return int(updated.rowcount)
            except BaseException:
                await db.rollback()
                raise

    async def _rebuild_projection(
        self,
        group_id: str,
        user_id: str,
        *,
        updated_at: str,
    ) -> None:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT experience_id, evidence_time, affection_score, affection_tier,
                   climate_valence, climate_familiarity
            FROM social_narrative_experiences
            WHERE group_id = ? AND user_id = ? AND status = 'active'
            ORDER BY evidence_time DESC, created_at DESC
            """,
            (group_id, user_id),
        )
        rows = list(await cursor.fetchall())
        latest_id = str(rows[0]["experience_id"]) if rows else None
        latest_time = str(rows[0]["evidence_time"]) if rows else None
        affection_score = rows[0]["affection_score"] if rows else None
        affection_tier = rows[0]["affection_tier"] if rows else None
        climate_valence = rows[0]["climate_valence"] if rows else None
        climate_familiarity = rows[0]["climate_familiarity"] if rows else None
        await db.execute(
            """
            UPDATE social_narrative_entities
            SET interaction_count = ?, last_experience_id = ?,
                last_interaction_at = ?, affection_score = ?, affection_tier = ?,
                climate_valence = ?, climate_familiarity = ?, updated_at = ?
            WHERE group_id = ? AND user_id = ?
            """,
            (
                len(rows),
                latest_id,
                latest_time,
                affection_score,
                affection_tier,
                climate_valence,
                climate_familiarity,
                updated_at,
                group_id,
                user_id,
            ),
        )

    async def build_prompt_context(
        self,
        *,
        group_id: str,
        user_id: str,
        limit: int = 6,
    ) -> str:
        experiences = await self.recall(group_id=group_id, user_id=user_id, limit=limit)
        if not experiences:
            return ""
        lines = [
            "【与当前群友的共同经历 / factual】",
            "- 这些内容仅来自当前群可追溯消息证据；不得补写真人线下行为，不得跨群引用。",
        ]
        for item in reversed(experiences):
            lines.append(
                f"- 证据 message_id={item.evidence_message_id} time={item.evidence_time}："
                f"对方说“{_bounded_text(item.user_text, 120)}”；"
                f"我回复“{_bounded_text(item.bot_reply, 140)}”"
            )
        return "\n".join(lines)

    async def build_group_reflection_context(
        self,
        *,
        group_id: str,
        limit: int = 12,
    ) -> str:
        group = _required_text(group_id, "group_id")
        if group == "global":
            return ""
        cursor = await self._require_db().execute(
            """
            SELECT * FROM social_narrative_experiences
            WHERE group_id = ? AND status = 'active'
            ORDER BY evidence_time DESC, created_at DESC
            LIMIT ?
            """,
            (group, max(1, min(50, int(limit)))),
        )
        experiences = [
            _row_to_experience(row)
            for row in await cursor.fetchall()
        ]
        if not experiences:
            return ""
        lines = [
            f"【当前群共同经历 / factual】group={group}",
            "- 仅可使用下列群聊证据；不得补写真人线下行为，不得跨群引用。",
        ]
        for item in reversed(experiences):
            lines.append(
                f"- user_id={item.user_id} 证据 message_id={item.evidence_message_id} "
                f"time={item.evidence_time}：对方说“{_bounded_text(item.user_text, 100)}”；"
                f"我回复“{_bounded_text(item.bot_reply, 120)}”"
            )
        return "\n".join(lines)

    async def stats(self) -> dict[str, int]:
        db = self._require_db()
        result: dict[str, int] = {}
        for key, sql in (
            ("active_experiences", "SELECT COUNT(*) FROM social_narrative_experiences WHERE status = 'active'"),
            (
                "invalidated_experiences",
                "SELECT COUNT(*) FROM social_narrative_experiences WHERE status = 'invalidated'",
            ),
            ("entities", "SELECT COUNT(*) FROM social_narrative_entities"),
        ):
            cursor = await db.execute(sql)
            row = await cursor.fetchone()
            result[key] = int(row[0] or 0) if row is not None else 0
        return result
