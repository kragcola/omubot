"""StyleStore: scoped expression habits with evidence and revision history."""

from __future__ import annotations

import contextlib
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import aiosqlite
from loguru import logger

from services.learning_normalizer import LearningNormalizerStore, normalize_key
from services.storage import close_with_checkpoint, connect_sqlite

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

StyleStatus = Literal["pending", "approved", "rejected", "muted"]
StyleScope = Literal["group", "global"]
StyleOutputPolicy = Literal["allow_use", "transform", "observe_only"]
StyleSourceType = Literal["human", "bot", "admin", "system"]
StyleFeedbackRating = Literal["positive", "negative", "neutral"]
StyleProfileStatus = Literal["draft", "enabled", "disabled"]

VALID_STATUSES: frozenset[str] = frozenset(("pending", "approved", "rejected", "muted"))
VALID_SCOPES: frozenset[str] = frozenset(("group", "global"))
VALID_OUTPUT_POLICIES: frozenset[str] = frozenset(("allow_use", "transform", "observe_only"))
VALID_SOURCE_TYPES: frozenset[str] = frozenset(("human", "bot", "admin", "system"))
VALID_FEEDBACK_RATINGS: frozenset[str] = frozenset(("positive", "negative", "neutral"))
VALID_PROFILE_STATUSES: frozenset[str] = frozenset(("draft", "enabled", "disabled"))

_OUTPUT_POLICY_RANK = {"allow_use": 0, "transform": 1, "observe_only": 2}

_CREATE_EXPRESSIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS style_expressions (
    expression_id    TEXT PRIMARY KEY,
    situation        TEXT NOT NULL,
    style            TEXT NOT NULL,
    situation_key    TEXT NOT NULL,
    style_key        TEXT NOT NULL,
    scope            TEXT NOT NULL,
    group_id         TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending',
    confidence       REAL NOT NULL DEFAULT 0.5,
    count            INTEGER NOT NULL DEFAULT 1,
    source           TEXT NOT NULL DEFAULT 'extractor',
    risk_tags_json   TEXT NOT NULL DEFAULT '[]',
    output_policy    TEXT NOT NULL DEFAULT 'transform',
    persona_fit      REAL NOT NULL DEFAULT 0.5,
    mood_fit         REAL NOT NULL DEFAULT 0.5,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    meta_json        TEXT NOT NULL DEFAULT '{}'
)"""

_CREATE_EVIDENCE_TABLE = """\
CREATE TABLE IF NOT EXISTS style_evidence (
    evidence_id      TEXT PRIMARY KEY,
    expression_id    TEXT NOT NULL,
    group_id         TEXT NOT NULL DEFAULT '',
    speaker          TEXT NOT NULL DEFAULT '',
    raw_text         TEXT NOT NULL DEFAULT '',
    context          TEXT NOT NULL DEFAULT '',
    source_type      TEXT NOT NULL DEFAULT 'human',
    message_id       INTEGER,
    observed_at      TEXT NOT NULL,
    FOREIGN KEY(expression_id) REFERENCES style_expressions(expression_id) ON DELETE CASCADE
)"""

_CREATE_REVISIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS style_revisions (
    revision_id      TEXT PRIMARY KEY,
    expression_id    TEXT NOT NULL,
    action           TEXT NOT NULL,
    actor            TEXT NOT NULL,
    before_json      TEXT NOT NULL DEFAULT '{}',
    after_json       TEXT NOT NULL DEFAULT '{}',
    reason           TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    meta_json        TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(expression_id) REFERENCES style_expressions(expression_id) ON DELETE CASCADE
)"""

_CREATE_FEEDBACK_TABLE = """\
CREATE TABLE IF NOT EXISTS style_feedback (
    feedback_id     TEXT PRIMARY KEY,
    target_type     TEXT NOT NULL DEFAULT 'expression',
    target_id       TEXT NOT NULL DEFAULT '',
    group_id        TEXT NOT NULL DEFAULT '',
    rating          TEXT NOT NULL DEFAULT 'neutral',
    source          TEXT NOT NULL DEFAULT 'admin',
    actor           TEXT NOT NULL DEFAULT '',
    raw_text        TEXT NOT NULL DEFAULT '',
    context         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    meta_json       TEXT NOT NULL DEFAULT '{}'
)"""

_CREATE_PROFILES_TABLE = """\
CREATE TABLE IF NOT EXISTS style_profiles (
    profile_id          TEXT PRIMARY KEY,
    scope               TEXT NOT NULL,
    group_id            TEXT NOT NULL DEFAULT '',
    version             INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft',
    content             TEXT NOT NULL,
    expression_ids_json TEXT NOT NULL DEFAULT '[]',
    risk_notes_json     TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    enabled_at          TEXT NOT NULL DEFAULT '',
    disabled_at         TEXT NOT NULL DEFAULT '',
    created_by          TEXT NOT NULL DEFAULT 'system',
    meta_json           TEXT NOT NULL DEFAULT '{}'
)"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_style_scope_status ON style_expressions(scope, group_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_style_updated ON style_expressions(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_style_prompt ON style_expressions(status, scope, group_id, confidence)",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_style_dedupe "
        "ON style_expressions(scope, group_id, situation_key, style_key)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_style_evidence_expr ON style_evidence(expression_id, observed_at)",
    "CREATE INDEX IF NOT EXISTS idx_style_revision_expr ON style_revisions(expression_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_style_feedback_target ON style_feedback(target_type, target_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_style_feedback_group ON style_feedback(group_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_style_profile_scope ON style_profiles(scope, group_id, status, version)",
]


@dataclass
class StyleExpression:
    expression_id: str
    situation: str
    style: str
    scope: StyleScope
    group_id: str
    status: StyleStatus
    confidence: float
    count: int
    source: str
    risk_tags: list[str]
    output_policy: StyleOutputPolicy
    persona_fit: float
    mood_fit: float
    created_at: str
    updated_at: str
    last_seen_at: str
    meta: dict[str, Any] = field(default_factory=dict)
    cross_group_visible: bool = False
    cross_group_enabled_by: str = ""
    cross_group_enabled_at: str = ""
    cross_group_enabled_for_groups: list[str] = field(default_factory=list)
    cross_group_enabled_reason: str = ""


@dataclass
class NewStyleExpression:
    situation: str
    style: str
    scope: StyleScope = "group"
    group_id: str = ""
    status: StyleStatus = "pending"
    confidence: float = 0.5
    source: str = "extractor"
    risk_tags: list[str] = field(default_factory=list)
    output_policy: StyleOutputPolicy = "transform"
    persona_fit: float = 0.5
    mood_fit: float = 0.5

    def __post_init__(self) -> None:
        self.situation = _clean_text(self.situation, max_len=160)
        self.style = _clean_text(self.style, max_len=220)
        if not self.situation:
            raise ValueError("situation is required")
        if not self.style:
            raise ValueError("style is required")
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope: {self.scope!r}")
        self.group_id = str(self.group_id or "").strip()
        if self.scope == "group" and not self.group_id:
            raise ValueError("group_id is required for group scoped style expressions")
        if self.scope == "global":
            self.group_id = "global"
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {self.status!r}")
        if self.output_policy not in VALID_OUTPUT_POLICIES:
            raise ValueError(f"Invalid output_policy: {self.output_policy!r}")
        self.confidence = _clamp01(self.confidence)
        self.persona_fit = _clamp01(self.persona_fit)
        self.mood_fit = _clamp01(self.mood_fit)
        self.source = _clean_text(self.source or "extractor", max_len=80) or "extractor"
        self.risk_tags = _normalize_tags(self.risk_tags)


@dataclass
class StyleEvidence:
    evidence_id: str
    expression_id: str
    group_id: str
    speaker: str
    raw_text: str
    context: str
    source_type: StyleSourceType
    message_id: int | None
    observed_at: str


@dataclass
class StyleRevision:
    revision_id: str
    expression_id: str
    action: str
    actor: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str
    created_at: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class StyleFeedback:
    feedback_id: str
    target_type: str
    target_id: str
    group_id: str
    rating: StyleFeedbackRating
    source: str
    actor: str
    raw_text: str
    context: str
    created_at: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class StyleProfile:
    profile_id: str
    scope: StyleScope
    group_id: str
    version: int
    status: StyleProfileStatus
    content: str
    expression_ids: list[str]
    risk_notes: list[str]
    created_at: str
    enabled_at: str
    disabled_at: str
    created_by: str
    meta: dict[str, Any] = field(default_factory=dict)


def normalize_style_key(value: str) -> str:
    """Return a lightweight normalized key for exact style-expression dedupe."""
    return normalize_key(value, "style")


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


def _normalizer_db_path_for(db_path: str) -> str:
    path = Path(db_path)
    if path.parent and str(path.parent) != ".":
        return str(path.parent / "learning_normalizer.db")
    return "storage/learning_normalizer.db"


def _generate_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _clean_text(value: str, *, max_len: int) -> str:
    return " ".join(str(value or "").split()).strip()[:max_len]


def _clamp01(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))


def _normalize_tags(tags: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if not tags:
        return []
    raw_items: list[Any] = re.split(r"[,，\s]+", tags) if isinstance(tags, str) else list(tags)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        tag = _clean_text(str(item or "").casefold(), max_len=48)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized[:16]


def _json_loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_loads_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return _normalize_tags([str(item) for item in parsed])


def _json_loads_str_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [_clean_text(str(item), max_len=300) for item in parsed if _clean_text(str(item), max_len=300)]


def _row_to_expression(row: aiosqlite.Row) -> StyleExpression:
    keys = tuple(row.keys())
    return StyleExpression(
        expression_id=row["expression_id"],
        situation=row["situation"],
        style=row["style"],
        scope=row["scope"],
        group_id=row["group_id"],
        status=row["status"],
        confidence=float(row["confidence"]),
        count=int(row["count"]),
        source=row["source"],
        risk_tags=_json_loads_list(row["risk_tags_json"]),
        output_policy=row["output_policy"],
        persona_fit=float(row["persona_fit"]),
        mood_fit=float(row["mood_fit"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_seen_at=row["last_seen_at"],
        meta=_json_loads_dict(row["meta_json"] if "meta_json" in keys else "{}"),
        cross_group_visible=bool(row["cross_group_visible"]) if "cross_group_visible" in keys else False,
        cross_group_enabled_by=row["cross_group_enabled_by"] if "cross_group_enabled_by" in keys else "",
        cross_group_enabled_at=row["cross_group_enabled_at"] if "cross_group_enabled_at" in keys else "",
        cross_group_enabled_for_groups=_json_loads_str_list(
            row["cross_group_enabled_for_groups"]
        ) if "cross_group_enabled_for_groups" in keys else [],
        cross_group_enabled_reason=row["cross_group_enabled_reason"] if "cross_group_enabled_reason" in keys else "",
    )


def _normalization_summary(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(meta, dict):
        return None
    cluster_id = str(meta.get("normalization_cluster_id") or "").strip()
    if not cluster_id:
        return None
    return {
        "cluster_id": cluster_id,
        "item_id": str(meta.get("normalization_item_id") or ""),
        "canonical_text": str(meta.get("normalized_from") or ""),
        "normalized_key": str(meta.get("normalized_key") or ""),
        "method": str(meta.get("normalization_method") or ""),
        "score": float(meta.get("normalization_score") or 0.0),
        "auto_merged": bool(meta.get("auto_merged")),
        "features": meta.get("normalization_features") if isinstance(meta.get("normalization_features"), dict) else {},
    }


def _row_to_evidence(row: aiosqlite.Row) -> StyleEvidence:
    return StyleEvidence(
        evidence_id=row["evidence_id"],
        expression_id=row["expression_id"],
        group_id=row["group_id"],
        speaker=row["speaker"],
        raw_text=row["raw_text"],
        context=row["context"],
        source_type=row["source_type"],
        message_id=row["message_id"],
        observed_at=row["observed_at"],
    )


def _row_to_revision(row: aiosqlite.Row) -> StyleRevision:
    return StyleRevision(
        revision_id=row["revision_id"],
        expression_id=row["expression_id"],
        action=row["action"],
        actor=row["actor"],
        before=_json_loads_dict(row["before_json"]),
        after=_json_loads_dict(row["after_json"]),
        reason=row["reason"],
        created_at=row["created_at"],
        meta=_json_loads_dict(row["meta_json"]),
    )


def _row_to_feedback(row: aiosqlite.Row) -> StyleFeedback:
    return StyleFeedback(
        feedback_id=row["feedback_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        group_id=row["group_id"],
        rating=row["rating"],
        source=row["source"],
        actor=row["actor"],
        raw_text=row["raw_text"],
        context=row["context"],
        created_at=row["created_at"],
        meta=_json_loads_dict(row["meta_json"]),
    )


def _row_to_profile(row: aiosqlite.Row) -> StyleProfile:
    return StyleProfile(
        profile_id=row["profile_id"],
        scope=row["scope"],
        group_id=row["group_id"],
        version=int(row["version"]),
        status=row["status"],
        content=row["content"],
        expression_ids=_json_loads_str_list(row["expression_ids_json"]),
        risk_notes=_json_loads_str_list(row["risk_notes_json"]),
        created_at=row["created_at"],
        enabled_at=row["enabled_at"],
        disabled_at=row["disabled_at"],
        created_by=row["created_by"],
        meta=_json_loads_dict(row["meta_json"]),
    )


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    names = {str(row["name"]) for row in await cursor.fetchall()}
    if column not in names:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


class StyleStore:
    """SQLite-backed expression habit store.

    Phase 1 only stores and exposes data. It does not collect messages,
    inject prompt blocks, or change runtime replies.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self.initialized = False
        self._status_listeners: list[
            Callable[[StyleExpression, str, str, str], Awaitable[None]]
        ] = []
        self._feedback_listeners: list[
            Callable[[StyleFeedback], Awaitable[None]]
        ] = []

    def add_status_listener(
        self,
        listener: Callable[[StyleExpression, str, str, str], Awaitable[None]],
    ) -> None:
        """Register a coroutine called after ``update_expression`` flips status.

        Listener signature:
        ``async (expression, prev_status, new_status, actor) -> None``.
        Failures inside listeners are caught by ``_fire_status_listeners``
        and logged as warnings — they MUST never break the source-of-truth
        path. Used by the Phase E.2 graph bridge to mirror approve/mute/
        reject transitions as ``style_applies_to_situation`` edges.
        """
        self._status_listeners.append(listener)

    async def _fire_status_listeners(
        self,
        expression: StyleExpression,
        prev_status: str,
        new_status: str,
        actor: str,
    ) -> None:
        for listener in self._status_listeners:
            try:
                await listener(expression, prev_status, new_status, actor)
            except Exception as exc:
                logger.warning(
                    "style status listener failed | expression={} "
                    "prev={} new={} listener={} err={}",
                    expression.expression_id,
                    prev_status,
                    new_status,
                    getattr(listener, "__qualname__", repr(listener)),
                    exc,
                )

    def add_feedback_listener(
        self,
        listener: Callable[[StyleFeedback], Awaitable[None]],
    ) -> None:
        """Register a coroutine called after every successful ``record_feedback``.

        Listener signature: ``async (feedback) -> None``.
        Used by the Phase E.3 graph bridge to mirror negative ratings as
        ``user_corrected_bot_about`` edges.
        """
        self._feedback_listeners.append(listener)

    async def _fire_feedback_listeners(self, feedback: StyleFeedback) -> None:
        for listener in self._feedback_listeners:
            try:
                await listener(feedback)
            except Exception as exc:
                logger.warning(
                    "style feedback listener failed | feedback={} "
                    "listener={} err={}",
                    feedback.feedback_id,
                    getattr(listener, "__qualname__", repr(listener)),
                    exc,
                )

    @property
    def db_path(self) -> str:
        return self._db_path

    async def init(self) -> None:
        self._db = await connect_sqlite(self._db_path)
        await self._db.execute("PRAGMA journal_mode=DELETE")
        await self._db.execute("PRAGMA synchronous=FULL")
        await self._db.execute(_CREATE_EXPRESSIONS_TABLE)
        await _ensure_column(self._db, "style_expressions", "meta_json", "TEXT NOT NULL DEFAULT '{}'")
        # Cross-group visibility migration (A2)
        await _ensure_column(self._db, "style_expressions", "cross_group_visible", "INTEGER NOT NULL DEFAULT 0")
        await _ensure_column(self._db, "style_expressions", "cross_group_enabled_by", "TEXT NOT NULL DEFAULT ''")
        await _ensure_column(self._db, "style_expressions", "cross_group_enabled_at", "TEXT NOT NULL DEFAULT ''")
        await _ensure_column(
            self._db,
            "style_expressions",
            "cross_group_enabled_for_groups",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        await _ensure_column(
            self._db,
            "style_expressions",
            "cross_group_enabled_reason",
            "TEXT NOT NULL DEFAULT ''",
        )
        await self._db.execute(_CREATE_EVIDENCE_TABLE)
        await self._db.execute(_CREATE_REVISIONS_TABLE)
        await self._db.execute(_CREATE_FEEDBACK_TABLE)
        await self._db.execute(_CREATE_PROFILES_TABLE)
        for index_sql in _CREATE_INDEXES:
            await self._db.execute(index_sql)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_style_cross_group "
            "ON style_expressions(cross_group_visible, status) "
            "WHERE cross_group_visible = 1"
        )
        await self._db.commit()
        self.initialized = True
        logger.info("StyleStore initialized | db={}", self._db_path)

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="style")
            self._db = None
        self.initialized = False

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("StyleStore not initialized")
        return self._db

    async def _attach_normalizer_candidate(
        self,
        *,
        raw_text: str,
        scope: StyleScope,
        group_id: str,
        source_table: str,
        source_id: str,
        message_id: int | None = None,
        user_id: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalizer: LearningNormalizerStore | None = None
        try:
            normalizer = LearningNormalizerStore(_normalizer_db_path_for(self._db_path))
            await normalizer.init()
            result = await normalizer.attach_candidate(
                domain="style",
                scope=scope,
                group_id=group_id,
                raw_text=raw_text,
                source_table=source_table,
                source_id=source_id,
                message_id=message_id,
                user_id=user_id,
                profile="style",
                meta=meta or {},
            )
            return result.to_meta()
        except Exception as exc:
            return {"normalization_error": str(exc)}
        finally:
            if normalizer is not None:
                with contextlib.suppress(Exception):
                    await normalizer.close()

    async def create_expression(
        self,
        expression: NewStyleExpression,
        *,
        actor: str = "system",
        reason: str = "",
    ) -> StyleExpression:
        db = self._require_db()
        now = _now_iso()
        expression_id = _generate_id("sty")
        normalization_meta = await self._attach_normalizer_candidate(
            raw_text=f"{expression.situation} {expression.style}",
            scope=expression.scope,
            group_id=expression.group_id,
            source_table="style_expressions",
            source_id=expression_id,
            meta={"path": "style_create"},
        )
        await db.execute(
            """INSERT INTO style_expressions (
                expression_id, situation, style, situation_key, style_key,
                scope, group_id, status, confidence, count, source,
                risk_tags_json, output_policy, persona_fit, mood_fit,
                created_at, updated_at, last_seen_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                expression_id,
                expression.situation,
                expression.style,
                normalize_style_key(expression.situation),
                normalize_style_key(expression.style),
                expression.scope,
                expression.group_id,
                expression.status,
                expression.confidence,
                1,
                expression.source,
                json.dumps(expression.risk_tags, ensure_ascii=False),
                expression.output_policy,
                expression.persona_fit,
                expression.mood_fit,
                now,
                now,
                now,
                json.dumps(normalization_meta, ensure_ascii=False),
            ),
        )
        await db.commit()
        created = await self.get_expression(expression_id)
        if created is None:
            raise RuntimeError("created style expression disappeared")
        await self.record_revision(
            expression_id,
            action="create",
            actor=actor,
            before={},
            after=self.expression_to_dict(created),
            reason=reason,
            meta={"normalization": _normalization_summary(normalization_meta)},
        )
        return created

    async def upsert_expression(
        self,
        expression: NewStyleExpression,
        *,
        evidence: dict[str, Any] | None = None,
        actor: str = "system",
        reason: str = "",
    ) -> StyleExpression:
        existing = await self.find_duplicate(expression)
        if existing is None:
            created = await self.create_expression(expression, actor=actor, reason=reason or "new expression")
            if evidence:
                await self.record_evidence(created.expression_id, **evidence)
            return created

        before = self.expression_to_dict(existing)
        merged_tags = _normalize_tags([*existing.risk_tags, *expression.risk_tags])
        output_policy = _stricter_output_policy(existing.output_policy, expression.output_policy)
        normalization_meta = await self._attach_normalizer_candidate(
            raw_text=f"{expression.situation} {expression.style}",
            scope=expression.scope,
            group_id=expression.group_id,
            source_table="style_expressions",
            source_id=existing.expression_id,
            meta={"path": "style_reinforce"},
        )
        fields = {
            "confidence": max(existing.confidence, expression.confidence),
            "count": existing.count + 1,
            "risk_tags_json": json.dumps(merged_tags, ensure_ascii=False),
            "output_policy": output_policy,
            "persona_fit": max(existing.persona_fit, expression.persona_fit),
            "mood_fit": max(existing.mood_fit, expression.mood_fit),
            "last_seen_at": _now_iso(),
            "meta_json": json.dumps({**existing.meta, **normalization_meta}, ensure_ascii=False),
        }
        await self._update_expression_fields(existing.expression_id, fields)
        updated = await self.get_expression(existing.expression_id)
        if updated is None:
            raise RuntimeError("updated style expression disappeared")
        if evidence:
            await self.record_evidence(updated.expression_id, **evidence)
        await self.record_revision(
            updated.expression_id,
            action="reinforce",
            actor=actor,
            before=before,
            after=self.expression_to_dict(updated),
            reason=reason or "duplicate expression reinforced",
            meta={"normalization": _normalization_summary(normalization_meta)},
        )
        return updated

    async def find_duplicate(self, expression: NewStyleExpression) -> StyleExpression | None:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM style_expressions
               WHERE scope = ? AND group_id = ? AND situation_key = ? AND style_key = ?
               LIMIT 1""",
            (
                expression.scope,
                expression.group_id,
                normalize_style_key(expression.situation),
                normalize_style_key(expression.style),
            ),
        )
        row = await cursor.fetchone()
        if row:
            return _row_to_expression(row)
        cursor = await db.execute(
            """SELECT * FROM style_expressions
               WHERE scope = ? AND group_id = ?
               ORDER BY updated_at DESC
               LIMIT 300""",
            (expression.scope, expression.group_id),
        )
        for candidate in await cursor.fetchall():
            existing = _row_to_expression(candidate)
            situation_score = _style_similarity(expression.situation, existing.situation)
            style_score = _style_similarity(expression.style, existing.style)
            if situation_score >= 0.92 and style_score >= 0.92:
                return existing
        return None

    async def get_expression(self, expression_id: str) -> StyleExpression | None:
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM style_expressions WHERE expression_id = ?", (expression_id,))
        row = await cursor.fetchone()
        return _row_to_expression(row) if row else None

    async def list_expressions(
        self,
        *,
        status: str | None = None,
        scope: str | None = None,
        group_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str = "default",
    ) -> tuple[list[StyleExpression], int]:
        db = self._require_db()
        where: list[str] = []
        values: list[Any] = []
        if status:
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid status: {status!r}")
            where.append("status = ?")
            values.append(status)
        if scope:
            if scope not in VALID_SCOPES:
                raise ValueError(f"Invalid scope: {scope!r}")
            where.append("scope = ?")
            values.append(scope)
        if group_id:
            where.append("group_id = ?")
            values.append(str(group_id))
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        count_cursor = await db.execute(f"SELECT COUNT(*) AS total FROM style_expressions {where_sql}", values)
        total_row = await count_cursor.fetchone()
        total = int(total_row["total"]) if total_row else 0
        if sort == "time":
            order_sql = "ORDER BY updated_at DESC, last_seen_at DESC, created_at DESC, expression_id DESC"
        else:
            order_sql = (
                "ORDER BY "
                "CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 WHEN 'muted' THEN 2 ELSE 3 END, "
                "confidence DESC, count DESC, last_seen_at DESC, updated_at DESC, expression_id DESC"
            )
        cursor = await db.execute(
            f"""SELECT * FROM style_expressions
                {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?""",
            [*values, max(1, min(int(limit or 100), 500)), max(0, int(offset or 0))],
        )
        rows = await cursor.fetchall()
        return [_row_to_expression(row) for row in rows], total

    async def get_prompt_expressions(
        self,
        *,
        group_id: str,
        conversation_text: str,
        include_global: bool = False,
        max_items: int = 3,
        min_confidence: float = 0.0,
    ) -> list[StyleExpression]:
        db = self._require_db()
        group_id = str(group_id or "").strip()
        if not group_id:
            return []
        query_key = normalize_style_key(conversation_text)
        if not query_key:
            return []
        conditions = ["(scope = 'group' AND group_id = ?)"]
        values: list[Any] = [group_id]
        if include_global:
            conditions.append("(scope = 'global' AND group_id = 'global')")
        conditions.append("(scope = 'group' AND cross_group_visible = 1)")
        cursor = await db.execute(
            f"""SELECT * FROM style_expressions
                WHERE status = 'approved'
                  AND output_policy != 'observe_only'
                  AND confidence >= ?
                  AND ({' OR '.join(conditions)})
                ORDER BY count DESC, last_seen_at DESC, updated_at DESC
                LIMIT ?""",
            [max(0.0, min(1.0, float(min_confidence or 0.0))), *values, max(1, min(max_items * 8, 80))],
        )
        candidates = [_row_to_expression(row) for row in await cursor.fetchall()]
        ranked = [
            (score, expression)
            for expression in candidates
            if (score := _expression_relevance(expression, query_key)) > 0.0
        ]
        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].confidence,
                item[1].count,
                item[1].last_seen_at,
                item[1].expression_id,
            ),
            reverse=True,
        )
        return [expression for _, expression in ranked[: max(1, min(int(max_items or 3), 10))]]

    async def build_prompt_block(
        self,
        *,
        group_id: str,
        conversation_text: str,
        include_global: bool = False,
        max_items: int = 3,
        max_chars: int = 800,
        min_confidence: float = 0.0,
    ) -> str:
        expressions = await self.get_prompt_expressions(
            group_id=group_id,
            conversation_text=conversation_text,
            include_global=include_global,
            max_items=max_items,
            min_confidence=min_confidence,
        )
        if not expressions:
            return ""
        limit = max(160, min(int(max_chars or 800), 2000))
        lines = [
            "【表达习惯参考】",
            "以下只用于调整本轮说话方式；不要照抄，不要改变核心人设。",
        ]
        for expression in expressions:
            line = _prompt_line(expression)
            if len("\n".join([*lines, line])) > limit:
                break
            lines.append(line)
        return "\n".join(lines) if len(lines) > 2 else ""

    async def record_feedback(
        self,
        *,
        target_type: str = "expression",
        target_id: str = "",
        group_id: str = "",
        rating: StyleFeedbackRating = "neutral",
        source: str = "admin",
        actor: str = "",
        raw_text: str = "",
        context: str = "",
        meta: dict[str, Any] | None = None,
    ) -> StyleFeedback:
        if rating not in VALID_FEEDBACK_RATINGS:
            raise ValueError(f"Invalid feedback rating: {rating!r}")
        db = self._require_db()
        feedback_id = _generate_id("sfb")
        await db.execute(
            """INSERT INTO style_feedback (
                feedback_id, target_type, target_id, group_id, rating,
                source, actor, raw_text, context, created_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                feedback_id,
                _clean_text(target_type or "expression", max_len=80) or "expression",
                _clean_text(target_id, max_len=120),
                _clean_text(group_id, max_len=80),
                rating,
                _clean_text(source or "admin", max_len=80) or "admin",
                _clean_text(actor, max_len=120),
                _clean_text(raw_text, max_len=1600),
                _clean_text(context, max_len=2400),
                _now_iso(),
                json.dumps(meta or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM style_feedback WHERE feedback_id = ?", (feedback_id,))
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("created style feedback disappeared")
        feedback = _row_to_feedback(row)
        await self._fire_feedback_listeners(feedback)
        return feedback

    async def list_feedback(
        self,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        group_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str = "default",
    ) -> tuple[list[StyleFeedback], int]:
        db = self._require_db()
        where: list[str] = []
        values: list[Any] = []
        if target_type:
            where.append("target_type = ?")
            values.append(str(target_type))
        if target_id:
            where.append("target_id = ?")
            values.append(str(target_id))
        if group_id:
            where.append("group_id = ?")
            values.append(str(group_id))
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        count_cursor = await db.execute(f"SELECT COUNT(*) AS total FROM style_feedback {where_sql}", values)
        count_row = await count_cursor.fetchone()
        total = int(count_row["total"]) if count_row else 0
        order_sql = (
            "ORDER BY "
            "CASE rating WHEN 'negative' THEN 0 WHEN 'positive' THEN 1 ELSE 2 END, "
            "created_at DESC, feedback_id DESC"
            if sort != "time"
            else "ORDER BY created_at DESC, feedback_id DESC"
        )
        cursor = await db.execute(
            f"""SELECT * FROM style_feedback
                {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?""",
            [*values, max(1, min(int(limit or 100), 500)), max(0, int(offset or 0))],
        )
        return [_row_to_feedback(row) for row in await cursor.fetchall()], total

    async def record_expression_feedback(
        self,
        expression_id: str,
        *,
        rating: StyleFeedbackRating,
        actor: str = "admin",
        reason: str = "",
        group_id: str = "",
        raw_text: str = "",
        context: str = "",
        meta: dict[str, Any] | None = None,
    ) -> StyleExpression | None:
        existing = await self.get_expression(expression_id)
        if existing is None:
            return None
        feedback = await self.record_feedback(
            target_type="expression",
            target_id=expression_id,
            group_id=group_id or existing.group_id,
            rating=rating,
            source="admin",
            actor=actor,
            raw_text=raw_text,
            context=context,
            meta={"reason": reason, **(meta or {})},
        )
        before = self.expression_to_dict(existing)
        delta = 0.04 if rating == "positive" else (-0.08 if rating == "negative" else 0.0)
        updates: dict[str, Any] = {
            "confidence": _clamp01(existing.confidence + delta),
        }
        if rating == "positive":
            updates["count"] = existing.count + 1
            updates["last_seen_at"] = _now_iso()
        await self._update_expression_fields(expression_id, updates)
        updated = await self.get_expression(expression_id)
        await self.record_revision(
            expression_id,
            action=f"feedback_{rating}",
            actor=actor,
            before=before,
            after=self.expression_to_dict(updated),
            reason=reason,
            meta={"feedback_id": feedback.feedback_id},
        )
        return updated

    async def generate_profile(
        self,
        *,
        scope: StyleScope = "group",
        group_id: str = "",
        include_global: bool = False,
        max_items: int = 12,
        enable: bool = True,
        actor: str = "system",
        reason: str = "",
    ) -> StyleProfile:
        scope, group_id = _normalize_profile_scope(scope, group_id)
        expressions = await self._profile_source_expressions(
            scope=scope,
            group_id=group_id,
            include_global=include_global,
            max_items=max_items,
        )
        if not expressions:
            raise ValueError("No approved style expressions available for profile generation")
        content, risk_notes = _build_profile_content(expressions)
        db = self._require_db()
        version = await self._next_profile_version(scope=scope, group_id=group_id)
        profile_id = _generate_id("sfp")
        now = _now_iso()
        status = "enabled" if enable else "draft"
        if enable:
            await self._disable_enabled_profiles(scope=scope, group_id=group_id, disabled_at=now)
        await db.execute(
            """INSERT INTO style_profiles (
                profile_id, scope, group_id, version, status, content,
                expression_ids_json, risk_notes_json, created_at,
                enabled_at, disabled_at, created_by, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id,
                scope,
                group_id,
                version,
                status,
                content,
                json.dumps([item.expression_id for item in expressions], ensure_ascii=False),
                json.dumps(risk_notes, ensure_ascii=False),
                now,
                now if enable else "",
                "",
                _clean_text(actor or "system", max_len=80) or "system",
                json.dumps({"reason": reason, "include_global": include_global}, ensure_ascii=False, sort_keys=True),
            ),
        )
        await db.commit()
        profile = await self.get_profile(profile_id)
        if profile is None:
            raise RuntimeError("created style profile disappeared")
        await self.record_feedback(
            target_type="profile",
            target_id=profile.profile_id,
            group_id=profile.group_id,
            rating="neutral",
            source="system",
            actor=actor,
            raw_text=profile.content,
            context="profile generated",
            meta={"scope": profile.scope, "version": profile.version, "status": profile.status},
        )
        return profile

    async def get_profile(self, profile_id: str) -> StyleProfile | None:
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM style_profiles WHERE profile_id = ?", (profile_id,))
        row = await cursor.fetchone()
        return _row_to_profile(row) if row else None

    async def list_profiles(
        self,
        *,
        scope: str | None = None,
        group_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "default",
    ) -> tuple[list[StyleProfile], int]:
        db = self._require_db()
        where: list[str] = []
        values: list[Any] = []
        if scope:
            if scope not in VALID_SCOPES:
                raise ValueError(f"Invalid scope: {scope!r}")
            where.append("scope = ?")
            values.append(scope)
        if group_id:
            where.append("group_id = ?")
            values.append(str(group_id))
        if status:
            if status not in VALID_PROFILE_STATUSES:
                raise ValueError(f"Invalid profile status: {status!r}")
            where.append("status = ?")
            values.append(status)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        count_cursor = await db.execute(f"SELECT COUNT(*) AS total FROM style_profiles {where_sql}", values)
        total_row = await count_cursor.fetchone()
        total = int(total_row["total"]) if total_row else 0
        order_sql = (
            "ORDER BY enabled_at DESC, created_at DESC, version DESC"
            if sort == "time"
            else (
                "ORDER BY CASE status WHEN 'enabled' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, "
                "version DESC, created_at DESC"
            )
        )
        cursor = await db.execute(
            f"""SELECT * FROM style_profiles
                {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?""",
            [*values, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))],
        )
        return [_row_to_profile(row) for row in await cursor.fetchall()], total

    async def set_profile_status(
        self,
        profile_id: str,
        status: StyleProfileStatus,
        *,
        actor: str = "admin",
        reason: str = "",
    ) -> StyleProfile | None:
        if status not in VALID_PROFILE_STATUSES:
            raise ValueError(f"Invalid profile status: {status!r}")
        profile = await self.get_profile(profile_id)
        if profile is None:
            return None
        db = self._require_db()
        now = _now_iso()
        if status == "enabled":
            await self._disable_enabled_profiles(scope=profile.scope, group_id=profile.group_id, disabled_at=now)
            enabled_at = now
            disabled_at = ""
        elif status == "disabled":
            enabled_at = profile.enabled_at
            disabled_at = now
        else:
            enabled_at = ""
            disabled_at = ""
        await db.execute(
            """UPDATE style_profiles
               SET status = ?, enabled_at = ?, disabled_at = ?
               WHERE profile_id = ?""",
            (status, enabled_at, disabled_at, profile_id),
        )
        await db.commit()
        updated = await self.get_profile(profile_id)
        await self.record_feedback(
            target_type="profile",
            target_id=profile_id,
            group_id=profile.group_id,
            rating="neutral",
            source="admin",
            actor=actor,
            raw_text=updated.content if updated else profile.content,
            context=f"profile status -> {status}",
            meta={"reason": reason, "scope": profile.scope, "version": profile.version},
        )
        return updated

    async def rollback_profile(
        self,
        *,
        scope: StyleScope = "group",
        group_id: str = "",
        actor: str = "admin",
        reason: str = "",
    ) -> StyleProfile | None:
        scope, group_id = _normalize_profile_scope(scope, group_id)
        profiles, _ = await self.list_profiles(scope=scope, group_id=group_id, limit=20)
        enabled = next((item for item in profiles if item.status == "enabled"), None)
        if enabled is not None:
            previous = next((item for item in profiles if item.version < enabled.version), None)
        else:
            previous = next((item for item in profiles if item.status != "enabled"), None)
        if previous is None:
            return None
        if enabled is not None:
            await self.set_profile_status(enabled.profile_id, "disabled", actor=actor, reason=reason or "rollback")
        return await self.set_profile_status(
            previous.profile_id,
            "enabled",
            actor=actor,
            reason=reason or "rollback to previous profile",
        )

    async def get_enabled_profiles(
        self,
        *,
        group_id: str,
        include_global: bool = False,
    ) -> list[StyleProfile]:
        db = self._require_db()
        group_id = str(group_id or "").strip()
        if not group_id:
            return []
        values: list[Any] = [group_id]
        conditions = ["(scope = 'group' AND group_id = ?)"]
        if include_global:
            conditions.append("(scope = 'global' AND group_id = 'global')")
        cursor = await db.execute(
            f"""SELECT * FROM style_profiles
                WHERE status = 'enabled'
                  AND ({' OR '.join(conditions)})
                ORDER BY scope ASC, version DESC, created_at DESC""",
            values,
        )
        return [_row_to_profile(row) for row in await cursor.fetchall()]

    async def build_profile_prompt_block(
        self,
        *,
        group_id: str,
        include_global: bool = False,
        max_chars: int = 900,
    ) -> str:
        profiles = await self.get_enabled_profiles(group_id=group_id, include_global=include_global)
        if not profiles:
            return ""
        limit = max(160, min(int(max_chars or 900), 2400))
        lines = [
            "【当前动态风格档案】",
            "以下用于把表达习惯压缩成稳定说话倾向；不得改变核心人设、身份、价值观或禁区。",
        ]
        for profile in profiles:
            heading = "全局共享" if profile.scope == "global" else f"群 {profile.group_id}"
            section = f"- {heading} v{profile.version}：{profile.content}"
            if len("\n".join([*lines, section])) > limit:
                break
            lines.append(section)
        return "\n".join(lines) if len(lines) > 2 else ""

    async def update_expression(
        self,
        expression_id: str,
        *,
        actor: str = "system",
        reason: str = "",
        **fields: Any,
    ) -> bool:
        existing = await self.get_expression(expression_id)
        if existing is None:
            return False
        before = self.expression_to_dict(existing)
        updates = self._normalize_update_fields(fields)
        if not updates:
            return False
        await self._update_expression_fields(expression_id, updates)
        updated = await self.get_expression(expression_id)
        await self.record_revision(
            expression_id,
            action="update",
            actor=actor,
            before=before,
            after=self.expression_to_dict(updated) if updated else {},
            reason=reason,
        )
        if (
            updated is not None
            and "status" in updates
            and updated.status != existing.status
        ):
            await self._fire_status_listeners(
                updated, existing.status, updated.status, actor
            )
        return True

    async def set_status(
        self,
        expression_id: str,
        status: StyleStatus,
        *,
        actor: str = "system",
        reason: str = "",
    ) -> bool:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status!r}")
        return await self.update_expression(expression_id, status=status, actor=actor, reason=reason)

    async def set_cross_group_visibility(
        self,
        expression_id: str,
        *,
        visible: bool,
        actor: str,
        reason: str = "",
        enabled_for_groups: list[str] | None = None,
    ) -> bool:
        """Toggle cross-group visibility. Only callable by admin."""
        existing = await self.get_expression(expression_id)
        if existing is None:
            return False
        before = self.expression_to_dict(existing)
        now = _now_iso()
        enabled_by = actor if visible else ""
        enabled_at = now if visible else ""
        groups_payload = (
            [str(g).strip() for g in (enabled_for_groups or []) if str(g).strip()]
            if visible
            else []
        )
        reason_payload = reason if visible else ""
        db = self._require_db()
        await db.execute(
            """UPDATE style_expressions
               SET cross_group_visible = ?, cross_group_enabled_by = ?,
                   cross_group_enabled_at = ?,
                   cross_group_enabled_for_groups = ?,
                   cross_group_enabled_reason = ?,
                   updated_at = ?
               WHERE expression_id = ?""",
            (
                int(visible),
                enabled_by,
                enabled_at,
                json.dumps(groups_payload, ensure_ascii=False),
                reason_payload,
                now,
                expression_id,
            ),
        )
        await db.commit()
        updated = await self.get_expression(expression_id)
        await self.record_revision(
            expression_id,
            action="cross_group_enable" if visible else "cross_group_disable",
            actor=actor,
            before=before,
            after=self.expression_to_dict(updated) if updated else {},
            reason=reason or f"cross_group_visible={'true' if visible else 'false'}",
        )
        return True

    async def _update_expression_fields(self, expression_id: str, fields: dict[str, Any]) -> None:
        db = self._require_db()
        fields = {**fields, "updated_at": _now_iso()}
        set_clause = ", ".join(f"{field} = ?" for field in fields)
        await db.execute(
            f"UPDATE style_expressions SET {set_clause} WHERE expression_id = ?",
            [*fields.values(), expression_id],
        )
        await db.commit()

    def _normalize_update_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        if "cross_group_visible" in fields:
            raise ValueError("cross_group_visible can only be set via set_cross_group_visibility()")
        updates: dict[str, Any] = {}
        if "situation" in fields:
            situation = _clean_text(str(fields["situation"]), max_len=160)
            if not situation:
                raise ValueError("situation is required")
            updates["situation"] = situation
            updates["situation_key"] = normalize_style_key(situation)
        if "style" in fields:
            style = _clean_text(str(fields["style"]), max_len=220)
            if not style:
                raise ValueError("style is required")
            updates["style"] = style
            updates["style_key"] = normalize_style_key(style)
        if "status" in fields:
            status = str(fields["status"])
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid status: {status!r}")
            updates["status"] = status
        if "confidence" in fields:
            updates["confidence"] = _clamp01(fields["confidence"])
        if "risk_tags" in fields:
            updates["risk_tags_json"] = json.dumps(_normalize_tags(fields["risk_tags"]), ensure_ascii=False)
        if "output_policy" in fields:
            output_policy = str(fields["output_policy"])
            if output_policy not in VALID_OUTPUT_POLICIES:
                raise ValueError(f"Invalid output_policy: {output_policy!r}")
            updates["output_policy"] = output_policy
        if "persona_fit" in fields:
            updates["persona_fit"] = _clamp01(fields["persona_fit"])
        if "mood_fit" in fields:
            updates["mood_fit"] = _clamp01(fields["mood_fit"])
        return updates

    async def record_evidence(
        self,
        expression_id: str,
        *,
        group_id: str = "",
        speaker: str = "",
        raw_text: str = "",
        context: str = "",
        source_type: StyleSourceType = "human",
        message_id: int | None = None,
        observed_at: str | None = None,
    ) -> str:
        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid source_type: {source_type!r}")
        db = self._require_db()
        evidence_id = _generate_id("sev")
        await db.execute(
            """INSERT INTO style_evidence (
                evidence_id, expression_id, group_id, speaker, raw_text,
                context, source_type, message_id, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence_id,
                expression_id,
                str(group_id or "").strip(),
                _clean_text(speaker, max_len=160),
                _clean_text(raw_text, max_len=1600),
                _clean_text(context, max_len=2400),
                source_type,
                message_id,
                observed_at or _now_iso(),
            ),
        )
        await db.commit()
        return evidence_id

    async def list_evidence(self, expression_id: str, *, limit: int = 50) -> list[StyleEvidence]:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM style_evidence
               WHERE expression_id = ?
               ORDER BY observed_at DESC, evidence_id DESC
               LIMIT ?""",
            (expression_id, max(1, min(int(limit or 50), 200))),
        )
        rows = await cursor.fetchall()
        return [_row_to_evidence(row) for row in rows]

    async def record_revision(
        self,
        expression_id: str,
        *,
        action: str,
        actor: str,
        before: dict[str, Any],
        after: dict[str, Any],
        reason: str = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        db = self._require_db()
        revision_id = _generate_id("srv")
        await db.execute(
            """INSERT INTO style_revisions (
                revision_id, expression_id, action, actor, before_json,
                after_json, reason, created_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                revision_id,
                expression_id,
                _clean_text(action, max_len=80) or "update",
                _clean_text(actor, max_len=80) or "system",
                json.dumps(before, ensure_ascii=False, sort_keys=True),
                json.dumps(after, ensure_ascii=False, sort_keys=True),
                _clean_text(reason, max_len=600),
                _now_iso(),
                json.dumps(meta or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        await db.commit()
        return revision_id

    async def list_revisions(self, expression_id: str, *, limit: int = 50) -> list[StyleRevision]:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM style_revisions
               WHERE expression_id = ?
               ORDER BY created_at DESC, rowid DESC
               LIMIT ?""",
            (expression_id, max(1, min(int(limit or 50), 200))),
        )
        rows = await cursor.fetchall()
        return [_row_to_revision(row) for row in rows]

    async def _profile_source_expressions(
        self,
        *,
        scope: StyleScope,
        group_id: str,
        include_global: bool,
        max_items: int,
    ) -> list[StyleExpression]:
        db = self._require_db()
        values: list[Any] = []
        conditions: list[str] = []
        if scope == "global":
            conditions.append("(scope = 'global' AND group_id = 'global')")
        else:
            conditions.append("(scope = 'group' AND group_id = ?)")
            values.append(group_id)
            if include_global:
                conditions.append("(scope = 'global' AND group_id = 'global')")
        cursor = await db.execute(
            f"""SELECT * FROM style_expressions
                WHERE status = 'approved'
                  AND output_policy != 'observe_only'
                  AND ({' OR '.join(conditions)})
                ORDER BY confidence DESC, count DESC, last_seen_at DESC
                LIMIT ?""",
            [*values, max(1, min(int(max_items or 12), 40))],
        )
        return [_row_to_expression(row) for row in await cursor.fetchall()]

    async def _next_profile_version(self, *, scope: StyleScope, group_id: str) -> int:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT MAX(version) AS version FROM style_profiles WHERE scope = ? AND group_id = ?",
            (scope, group_id),
        )
        row = await cursor.fetchone()
        return int(row["version"] or 0) + 1 if row else 1

    async def _disable_enabled_profiles(self, *, scope: StyleScope, group_id: str, disabled_at: str) -> None:
        db = self._require_db()
        await db.execute(
            """UPDATE style_profiles
               SET status = 'disabled', disabled_at = ?
               WHERE scope = ? AND group_id = ? AND status = 'enabled'""",
            (disabled_at, scope, group_id),
        )

    async def summary(self) -> dict[str, Any]:
        db = self._require_db()
        cursor = await db.execute("SELECT status, COUNT(*) AS count FROM style_expressions GROUP BY status")
        counts = {str(row["status"]): int(row["count"]) for row in await cursor.fetchall()}
        groups_cursor = await db.execute(
            "SELECT COUNT(DISTINCT group_id) AS count FROM style_expressions WHERE scope = 'group'"
        )
        groups_row = await groups_cursor.fetchone()
        risk_cursor = await db.execute(
            "SELECT COUNT(*) AS count FROM style_expressions WHERE risk_tags_json != '[]'"
        )
        risk_row = await risk_cursor.fetchone()
        feedback_cursor = await db.execute("SELECT COUNT(*) AS count FROM style_feedback")
        feedback_row = await feedback_cursor.fetchone()
        profile_cursor = await db.execute("SELECT COUNT(*) AS count FROM style_profiles")
        profile_row = await profile_cursor.fetchone()
        enabled_profile_cursor = await db.execute(
            "SELECT COUNT(*) AS count FROM style_profiles WHERE status = 'enabled'"
        )
        enabled_profile_row = await enabled_profile_cursor.fetchone()
        total = sum(counts.values())
        return {
            "total": total,
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "muted": counts.get("muted", 0),
            "group_count": int(groups_row["count"]) if groups_row else 0,
            "risk_tagged": int(risk_row["count"]) if risk_row else 0,
            "feedback_count": int(feedback_row["count"]) if feedback_row else 0,
            "profile_count": int(profile_row["count"]) if profile_row else 0,
            "enabled_profile_count": int(enabled_profile_row["count"]) if enabled_profile_row else 0,
        }

    @staticmethod
    def expression_to_dict(expression: StyleExpression | None) -> dict[str, Any]:
        if expression is None:
            return {}
        return {
            "expression_id": expression.expression_id,
            "situation": expression.situation,
            "style": expression.style,
            "scope": expression.scope,
            "group_id": expression.group_id,
            "status": expression.status,
            "confidence": expression.confidence,
            "count": expression.count,
            "source": expression.source,
            "risk_tags": expression.risk_tags,
            "output_policy": expression.output_policy,
            "persona_fit": expression.persona_fit,
            "mood_fit": expression.mood_fit,
            "created_at": expression.created_at,
            "updated_at": expression.updated_at,
            "last_seen_at": expression.last_seen_at,
            "meta": expression.meta,
            "normalization": _normalization_summary(expression.meta),
        }

    @staticmethod
    def evidence_to_dict(evidence: StyleEvidence) -> dict[str, Any]:
        return {
            "evidence_id": evidence.evidence_id,
            "expression_id": evidence.expression_id,
            "group_id": evidence.group_id,
            "speaker": evidence.speaker,
            "raw_text": evidence.raw_text,
            "context": evidence.context,
            "source_type": evidence.source_type,
            "message_id": evidence.message_id,
            "observed_at": evidence.observed_at,
        }

    @staticmethod
    def revision_to_dict(revision: StyleRevision) -> dict[str, Any]:
        return {
            "revision_id": revision.revision_id,
            "expression_id": revision.expression_id,
            "action": revision.action,
            "actor": revision.actor,
            "before": revision.before,
            "after": revision.after,
            "reason": revision.reason,
            "created_at": revision.created_at,
            "meta": revision.meta,
        }

    @staticmethod
    def feedback_to_dict(feedback: StyleFeedback) -> dict[str, Any]:
        return {
            "feedback_id": feedback.feedback_id,
            "target_type": feedback.target_type,
            "target_id": feedback.target_id,
            "group_id": feedback.group_id,
            "rating": feedback.rating,
            "source": feedback.source,
            "actor": feedback.actor,
            "raw_text": feedback.raw_text,
            "context": feedback.context,
            "created_at": feedback.created_at,
            "meta": feedback.meta,
        }

    @staticmethod
    def profile_to_dict(profile: StyleProfile | None) -> dict[str, Any]:
        if profile is None:
            return {}
        return {
            "profile_id": profile.profile_id,
            "scope": profile.scope,
            "group_id": profile.group_id,
            "version": profile.version,
            "status": profile.status,
            "content": profile.content,
            "expression_ids": profile.expression_ids,
            "risk_notes": profile.risk_notes,
            "created_at": profile.created_at,
            "enabled_at": profile.enabled_at,
            "disabled_at": profile.disabled_at,
            "created_by": profile.created_by,
            "meta": profile.meta,
        }


def _stricter_output_policy(left: StyleOutputPolicy, right: StyleOutputPolicy) -> StyleOutputPolicy:
    return left if _OUTPUT_POLICY_RANK[left] >= _OUTPUT_POLICY_RANK[right] else right


def _normalize_profile_scope(scope: str, group_id: str) -> tuple[StyleScope, str]:
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope: {scope!r}")
    normalized_group = _clean_text(group_id, max_len=80)
    if scope == "global":
        return "global", "global"
    if not normalized_group:
        raise ValueError("group_id is required for group scoped style profile")
    return "group", normalized_group


def _build_profile_content(expressions: list[StyleExpression]) -> tuple[str, list[str]]:
    lines: list[str] = []
    risk_notes: list[str] = []
    for expression in expressions:
        situation = _clean_text(expression.situation, max_len=80)
        style = _clean_text(expression.style, max_len=120)
        if not situation or not style:
            continue
        if expression.risk_tags or expression.output_policy == "transform":
            line = f"{situation}：理解这种表达节奏，但输出时转成凤笑梦式说法，不照搬原话。"
            if expression.risk_tags:
                risk_notes.append(f"{situation}: {', '.join(expression.risk_tags)}")
        else:
            line = f"{situation}：{style}"
        if line not in lines:
            lines.append(line)
        if len(lines) >= 8:
            break
    if not lines:
        raise ValueError("No usable style expressions for profile generation")
    return "；".join(lines), risk_notes[:8]


def _prompt_line(expression: StyleExpression) -> str:
    situation = _clean_text(expression.situation, max_len=90)
    style = _clean_text(expression.style, max_len=140)
    if expression.output_policy == "transform" or expression.risk_tags:
        return f"- 当{situation}时，可以参考：{style}。输出时按凤笑梦人设和当前心情转译，不要原样复刻。"
    return f"- 当{situation}时，可以{style}。"


def _expression_relevance(expression: StyleExpression, query_key: str) -> float:
    situation_key = normalize_style_key(expression.situation)
    style_key = normalize_style_key(expression.style)
    if not query_key or not situation_key:
        return 0.0
    if situation_key and (situation_key in query_key or query_key in situation_key):
        return 1.0
    if style_key and len(style_key) >= 4 and (style_key in query_key or query_key in style_key):
        return 0.8

    query_units = _key_units(query_key)
    expression_units = _key_units(f"{situation_key}{style_key}")
    if not query_units or not expression_units:
        return 0.0
    overlap = query_units & expression_units
    if not overlap:
        return 0.0
    return len(overlap) / max(3, min(len(query_units), len(expression_units)))


def _style_similarity(left: str, right: str) -> float:
    from services.learning_normalizer import score_similarity

    return score_similarity(left, right, "style").score


def _key_units(value: str) -> set[str]:
    text = normalize_style_key(value)
    if not text:
        return set()
    units: set[str] = set()
    units.update(text[index : index + 2] for index in range(max(0, len(text) - 1)))
    if len(text) >= 3:
        units.update(text[index : index + 3] for index in range(len(text) - 2))
    return {unit for unit in units if unit}
