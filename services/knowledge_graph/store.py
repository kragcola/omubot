"""SQLite store for derived graph facts and extraction candidates."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite
from loguru import logger

from services.cross_group import (
    CrossGroupVisibility,
    legacy_cross_group_visible,
    resolve_cross_group_visibility,
    visibility_from_db,
    visibility_to_db,
)
from services.knowledge_graph.provenance import (
    GraphProvenanceError,
    normalize_graph_evidence,
)
from services.knowledge_graph.types import GraphCandidate, GraphFact, GraphStatus
from services.storage import close_with_checkpoint, connect_sqlite

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

_CREATE_FACTS = """\
CREATE TABLE IF NOT EXISTS graph_facts (
    fact_id     TEXT PRIMARY KEY,
    subject     TEXT NOT NULL,
    predicate   TEXT NOT NULL,
    object      TEXT NOT NULL,
    confidence  REAL NOT NULL,
    status      TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'global',
    scope_id    TEXT NOT NULL DEFAULT 'global',
    source      TEXT NOT NULL,
    supersedes  TEXT,
    metadata_json TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)"""

_CREATE_EVIDENCE = """\
CREATE TABLE IF NOT EXISTS graph_evidence (
    evidence_row_id TEXT PRIMARY KEY,
    fact_id         TEXT NOT NULL,
    evidence_type   TEXT NOT NULL,
    evidence_id     TEXT NOT NULL,
    quote           TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY(fact_id) REFERENCES graph_facts(fact_id) ON DELETE CASCADE
)"""

_CREATE_CANDIDATES = """\
CREATE TABLE IF NOT EXISTS extraction_candidates (
    candidate_id TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,
    predicate    TEXT NOT NULL,
    object       TEXT NOT NULL,
    confidence   REAL NOT NULL,
    status       TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'global',
    scope_id     TEXT NOT NULL DEFAULT 'global',
    source       TEXT NOT NULL,
    evidence_json TEXT,
    review_note  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
)"""

_CREATE_NODES = """\
CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id         TEXT PRIMARY KEY,
    node_type       TEXT NOT NULL,
    source_table    TEXT NOT NULL DEFAULT '',
    source_id       TEXT NOT NULL DEFAULT '',
    scope           TEXT NOT NULL DEFAULT 'group',
    group_id        TEXT NOT NULL DEFAULT '',
    label           TEXT NOT NULL DEFAULT '',
    properties_json TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'active',
    cross_group_visible       INTEGER NOT NULL DEFAULT 0,
    cross_group_enabled_by    TEXT NOT NULL DEFAULT '',
    cross_group_enabled_at    TEXT NOT NULL DEFAULT '',
    cross_group_enabled_for_groups TEXT NOT NULL DEFAULT '[]',
    cross_group_enabled_reason TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)"""

_CREATE_EDGES = """\
CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id         TEXT PRIMARY KEY,
    edge_type       TEXT NOT NULL,
    from_node_id    TEXT NOT NULL,
    to_node_id      TEXT NOT NULL,
    scope           TEXT NOT NULL DEFAULT 'group',
    group_id        TEXT NOT NULL DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0.5,
    evidence_refs   TEXT NOT NULL DEFAULT '[]',
    properties_json TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'active',
    cross_group_visible       INTEGER NOT NULL DEFAULT 0,
    cross_group_enabled_by    TEXT NOT NULL DEFAULT '',
    cross_group_enabled_at    TEXT NOT NULL DEFAULT '',
    cross_group_enabled_for_groups TEXT NOT NULL DEFAULT '[]',
    cross_group_enabled_reason TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY(from_node_id) REFERENCES graph_nodes(node_id),
    FOREIGN KEY(to_node_id) REFERENCES graph_nodes(node_id)
)"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_graph_facts_subject ON graph_facts(subject, status)",
    "CREATE INDEX IF NOT EXISTS idx_graph_facts_object ON graph_facts(object, status)",
    "CREATE INDEX IF NOT EXISTS idx_graph_facts_scope ON graph_facts(scope, scope_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_graph_candidates_status ON extraction_candidates(status, confidence)",
    "CREATE INDEX IF NOT EXISTS idx_graph_candidates_scope ON extraction_candidates(scope, scope_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_gn_type_scope ON graph_nodes(node_type, scope, group_id)",
    "CREATE INDEX IF NOT EXISTS idx_gn_source ON graph_nodes(source_table, source_id)",
    "CREATE INDEX IF NOT EXISTS idx_ge_type ON graph_edges(edge_type, status)",
    "CREATE INDEX IF NOT EXISTS idx_ge_from ON graph_edges(from_node_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_ge_to ON graph_edges(to_node_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_ge_scope ON graph_edges(scope, group_id, status)",
]


class KnowledgeGraphStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        provenance_gate_enabled: bool = True,
        observability_enabled: bool = True,
    ) -> None:
        self._db_path = str(db_path)
        # gpg_v1 write gate. False = legacy truthy acceptance (unsafe rollback).
        self.provenance_gate_enabled = bool(provenance_gate_enabled)
        # gpo_v1 read-side quality health. False skips quality scan and omits
        # nested observability from health_snapshot (pre-gpo key shape).
        self.observability_enabled = bool(observability_enabled)
        self._db: aiosqlite.Connection | None = None
        # Dedicated connection for promote_candidate only. Ordinary mutators
        # (add_candidate, add_fact, set_candidate_status, graph nodes/edges)
        # use self._db and must never share an open promotion transaction.
        self._promotion_db: aiosqlite.Connection | None = None
        # Serializes same-instance candidate promotions; cross-connection races
        # still rely on BEGIN IMMEDIATE + compare-and-set on candidate status.
        self._promotion_lock = asyncio.Lock()

    async def init(self) -> None:
        self._db = await connect_sqlite(self._db_path)
        await self._db.execute(_CREATE_FACTS)
        await self._db.execute(_CREATE_EVIDENCE)
        await self._db.execute(_CREATE_CANDIDATES)
        await self._db.execute(_CREATE_NODES)
        await self._db.execute(_CREATE_EDGES)
        await self._ensure_column("graph_facts", "scope", "TEXT NOT NULL DEFAULT 'global'")
        await self._ensure_column("graph_facts", "scope_id", "TEXT NOT NULL DEFAULT 'global'")
        await self._ensure_column("extraction_candidates", "scope", "TEXT NOT NULL DEFAULT 'global'")
        await self._ensure_column("extraction_candidates", "scope_id", "TEXT NOT NULL DEFAULT 'global'")
        # Cross-group visibility migration (A2)
        await self._ensure_column("graph_facts", "cross_group_visible", "INTEGER NOT NULL DEFAULT 0")
        await self._ensure_column("graph_facts", "cross_group_enabled_by", "TEXT NOT NULL DEFAULT ''")
        await self._ensure_column("graph_facts", "cross_group_enabled_at", "TEXT NOT NULL DEFAULT ''")
        await self._ensure_column(
            "graph_facts", "cross_group_enabled_for_groups", "TEXT NOT NULL DEFAULT '[]'"
        )
        await self._ensure_column(
            "graph_facts", "cross_group_enabled_reason", "TEXT NOT NULL DEFAULT ''"
        )
        await self._ensure_column("extraction_candidates", "cross_group_visible", "INTEGER NOT NULL DEFAULT 0")
        await self._ensure_column("extraction_candidates", "cross_group_enabled_by", "TEXT NOT NULL DEFAULT ''")
        await self._ensure_column("extraction_candidates", "cross_group_enabled_at", "TEXT NOT NULL DEFAULT ''")
        await self._ensure_column(
            "extraction_candidates", "cross_group_enabled_for_groups", "TEXT NOT NULL DEFAULT '[]'"
        )
        await self._ensure_column(
            "extraction_candidates", "cross_group_enabled_reason", "TEXT NOT NULL DEFAULT ''"
        )
        for statement in _CREATE_INDEXES:
            await self._db.execute(statement)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_graph_facts_cross_group "
            "ON graph_facts(cross_group_visible, status) "
            "WHERE cross_group_visible IN (1, 2)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_graph_candidates_cross_group "
            "ON extraction_candidates(cross_group_visible, status) "
            "WHERE cross_group_visible IN (1, 2)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_gn_cross_group "
            "ON graph_nodes(cross_group_visible, status) "
            "WHERE cross_group_visible IN (1, 2)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ge_cross_group "
            "ON graph_edges(cross_group_visible, status) "
            "WHERE cross_group_visible IN (1, 2)"
        )
        await self._db.commit()
        # Open after schema is ready so promotions never share self._db's
        # autocommit / ordinary-write transaction state.
        self._promotion_db = await connect_sqlite(self._db_path)

    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        db = self._require_db()
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = {row["name"] for row in await cursor.fetchall()}
        if column not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def close(self) -> None:
        if self._promotion_db is not None:
            await close_with_checkpoint(
                self._promotion_db, name="knowledge_graph_promotion"
            )
            self._promotion_db = None
        if self._db is not None:
            await close_with_checkpoint(self._db, name="knowledge_graph")
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("KnowledgeGraphStore is not initialized")
        return self._db

    def _require_promotion_db(self) -> aiosqlite.Connection:
        if self._promotion_db is None:
            raise RuntimeError("KnowledgeGraphStore promotion connection is not initialized")
        return self._promotion_db

    async def add_fact(
        self,
        *,
        subject: str,
        predicate: str,
        object: str,
        confidence: float,
        source: str,
        evidence: dict[str, Any],
        status: GraphStatus = "active",
        scope: str = "global",
        scope_id: str = "global",
        supersedes: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GraphFact:
        evidence = self._prepare_evidence(evidence)
        fact_id = "gf_" + secrets.token_hex(6)
        now = _now_iso()
        db = self._require_db()
        await db.execute(
            "INSERT INTO graph_facts "
            "(fact_id, subject, predicate, object, confidence, status, scope, scope_id, source, supersedes, "
            "metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fact_id,
                subject,
                predicate,
                object,
                confidence,
                status,
                scope,
                scope_id,
                source,
                supersedes,
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await self._insert_evidence(fact_id, evidence)
        await db.commit()
        return GraphFact(
            fact_id=fact_id,
            subject=subject,
            predicate=predicate,
            object=object,
            confidence=confidence,
            status=status,
            source=source,
            scope=scope,
            scope_id=scope_id,
            supersedes=supersedes,
            metadata=metadata or {},
            evidence=[],
            created_at=now,
            updated_at=now,
        )

    async def add_candidate(
        self,
        *,
        subject: str,
        predicate: str,
        object: str,
        confidence: float,
        source: str,
        evidence: dict[str, Any],
        status: GraphStatus = "pending",
        scope: str = "global",
        scope_id: str = "global",
    ) -> GraphCandidate:
        evidence = self._prepare_evidence(evidence, require_for_candidate=True)
        candidate_id = "gc_" + secrets.token_hex(6)
        now = _now_iso()
        db = self._require_db()
        await db.execute(
            "INSERT INTO extraction_candidates "
            "(candidate_id, subject, predicate, object, confidence, status, scope, scope_id, source, evidence_json, "
            "review_note, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate_id,
                subject,
                predicate,
                object,
                confidence,
                status,
                scope,
                scope_id,
                source,
                json.dumps(evidence, ensure_ascii=False),
                "",
                now,
                now,
            ),
        )
        await db.commit()
        return GraphCandidate(
            candidate_id=candidate_id,
            subject=subject,
            predicate=predicate,
            object=object,
            confidence=confidence,
            status=status,
            source=source,
            evidence=evidence,
            scope=scope,
            scope_id=scope_id,
            created_at=now,
            updated_at=now,
        )

    async def list_facts(self, *, status: str = "active", limit: int = 100) -> list[GraphFact]:
        cursor = await self._require_db().execute(
            "SELECT * FROM graph_facts WHERE status = ? ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (status, limit),
        )
        return [_row_to_fact(row) for row in await cursor.fetchall()]

    async def list_facts_by_scopes(
        self,
        *,
        allowed_scopes: list[tuple[str, str]],
        status: str = "active",
        limit_per_scope: int = 200,
    ) -> list[GraphFact]:
        """Return a confidence-ordered, fair window for each allowed scope."""
        scope_pairs = sorted({
            (str(scope), str(scope_id))
            for scope, scope_id in allowed_scopes
        })
        if not scope_pairs:
            return []
        normalized_limit = max(1, int(limit_per_scope))
        scope_sql = " OR ".join(
            "(scope = ? AND scope_id = ?)" for _ in scope_pairs
        )
        params: list[Any] = [status]
        for scope, scope_id in scope_pairs:
            params.extend((scope, scope_id))
        params.append(normalized_limit)
        cursor = await self._require_db().execute(
            "WITH ranked AS ("
            "SELECT graph_facts.*, "
            "ROW_NUMBER() OVER ("
            "PARTITION BY scope, scope_id "
            "ORDER BY confidence DESC, updated_at DESC, fact_id ASC"
            ") AS scope_rank "
            "FROM graph_facts WHERE status = ? AND (" + scope_sql + ")"
            ") SELECT * FROM ranked WHERE scope_rank <= ? "
            "ORDER BY confidence DESC, updated_at DESC, fact_id ASC",
            params,
        )
        return [_row_to_fact(row) for row in await cursor.fetchall()]

    async def list_scope_risk_facts(self, *, limit: int = 100) -> list[GraphFact]:
        """List legacy global facts that appear to come from memory-card evidence."""
        cursor = await self._require_db().execute(
            "SELECT DISTINCT f.* FROM graph_facts f "
            "JOIN graph_evidence e ON e.fact_id = f.fact_id "
            "WHERE f.status = 'active' AND f.scope = 'global' AND f.scope_id = 'global' "
            "AND (e.evidence_type = 'memory_card' OR e.evidence_id LIKE 'card_%') "
            "ORDER BY f.updated_at DESC, f.confidence DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_fact(row) for row in await cursor.fetchall()]

    async def get_fact(self, fact_id: str) -> GraphFact | None:
        cursor = await self._require_db().execute(
            "SELECT * FROM graph_facts WHERE fact_id = ?",
            (fact_id,),
        )
        row = await cursor.fetchone()
        return _row_to_fact(row) if row else None

    async def find_fact(
        self,
        *,
        subject: str,
        predicate: str,
        object: str,
        scope: str = "global",
        scope_id: str = "global",
        statuses: tuple[str, ...] = ("active",),
    ) -> GraphFact | None:
        placeholders = ", ".join("?" for _ in statuses)
        cursor = await self._require_db().execute(
            "SELECT * FROM graph_facts "
            f"WHERE subject = ? AND predicate = ? AND object = ? AND scope = ? AND scope_id = ? "
            f"AND status IN ({placeholders}) "
            "ORDER BY confidence DESC, updated_at DESC LIMIT 1",
            (subject, predicate, object, scope, scope_id, *statuses),
        )
        row = await cursor.fetchone()
        return _row_to_fact(row) if row else None

    async def find_candidate(
        self,
        *,
        subject: str,
        predicate: str,
        object: str,
        scope: str = "global",
        scope_id: str = "global",
        statuses: tuple[str, ...] = ("pending",),
    ) -> GraphCandidate | None:
        placeholders = ", ".join("?" for _ in statuses)
        cursor = await self._require_db().execute(
            "SELECT * FROM extraction_candidates "
            f"WHERE subject = ? AND predicate = ? AND object = ? AND scope = ? AND scope_id = ? "
            f"AND status IN ({placeholders}) "
            "ORDER BY confidence DESC, updated_at DESC LIMIT 1",
            (subject, predicate, object, scope, scope_id, *statuses),
        )
        row = await cursor.fetchone()
        return _row_to_candidate(row) if row else None

    async def list_candidates(self, *, status: str = "pending", limit: int = 100) -> list[GraphCandidate]:
        cursor = await self._require_db().execute(
            "SELECT * FROM extraction_candidates WHERE status = ? ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (status, limit),
        )
        return [_row_to_candidate(row) for row in await cursor.fetchall()]

    async def get_candidate(self, candidate_id: str) -> GraphCandidate | None:
        cursor = await self._require_db().execute(
            "SELECT * FROM extraction_candidates WHERE candidate_id = ?",
            (candidate_id,),
        )
        row = await cursor.fetchone()
        return _row_to_candidate(row) if row else None

    async def set_candidate_status(self, candidate_id: str, status: str, *, review_note: str = "") -> bool:
        """Unconditional status write (legacy / internal). Prefer transition_candidate_status."""
        db = self._require_db()
        cursor = await db.execute(
            "UPDATE extraction_candidates SET status = ?, review_note = ?, updated_at = ? WHERE candidate_id = ?",
            (status, review_note, _now_iso(), candidate_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def transition_candidate_status(
        self,
        candidate_id: str,
        *,
        to_status: str,
        allowed_statuses: Collection[str],
        review_note: str = "",
    ) -> bool:
        """Compare-and-set candidate status in one UPDATE.

        Succeeds only when the row exists and its current status is in
        ``allowed_statuses``. Never pre-reads: the WHERE clause is the sole
        authority (no TOCTOU). Returns False when the CAS loses (missing row
        or disallowed status such as ``active`` / ``rejected`` when not listed).
        """
        allowed = tuple(dict.fromkeys(str(s) for s in allowed_statuses if str(s)))
        if not allowed:
            raise ValueError(
                "transition_candidate_status requires a non-empty allowed_statuses set"
            )
        db = self._require_db()
        cursor = await db.execute(
            "UPDATE extraction_candidates "
            "SET status = ?, review_note = ?, updated_at = ? "
            f"WHERE candidate_id = ? AND status IN ({', '.join('?' for _ in allowed)})",
            (to_status, review_note, _now_iso(), candidate_id, *allowed),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def promote_candidate(
        self,
        candidate_id: str,
        *,
        review_note: str = "approved",
        allowed_statuses: Collection[str] = ("pending",),
        metadata: dict[str, Any] | None = None,
    ) -> tuple[GraphFact, dict[str, Any]] | None:
        """Atomically promote a candidate to one active fact + evidence.

        Runs exclusively on the dedicated ``_promotion_db`` connection under
        the per-store promotion lock so ordinary ``self._db`` writers cannot
        interleave into, commit, or be rolled back with this transaction:

        1. ``BEGIN IMMEDIATE``
        2. load the candidate; require status in ``allowed_statuses``
        3. compare-and-set that exact row to ``active``
        4. insert exactly one ``graph_facts`` row and one evidence row
        5. commit once

        On any failure or cancellation the transaction is rolled back and the
        candidate / facts tables are left unchanged. Returns ``(fact, evidence)``
        where *evidence* is the candidate's original evidence dict (for
        post-commit listeners); ``fact.evidence`` is the persisted evidence list.
        Concurrent winners: exactly one success, loser returns ``None``.
        """
        allowed = tuple(dict.fromkeys(str(s) for s in allowed_statuses if str(s)))
        if not allowed:
            raise ValueError("promote_candidate requires a non-empty allowed_statuses set")

        async with self._promotion_lock:
            db = self._require_promotion_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "SELECT * FROM extraction_candidates WHERE candidate_id = ?",
                    (candidate_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    await db.rollback()
                    return None
                candidate = _row_to_candidate(row)
                if candidate.status not in allowed:
                    await db.rollback()
                    return None

                evidence = self._prepare_evidence(
                    dict(candidate.evidence or {}),
                )

                now = _now_iso()
                status_cur = await db.execute(
                    "UPDATE extraction_candidates "
                    "SET status = ?, review_note = ?, updated_at = ? "
                    f"WHERE candidate_id = ? AND status IN ({', '.join('?' for _ in allowed)})",
                    ("active", review_note, now, candidate_id, *allowed),
                )
                if status_cur.rowcount != 1:
                    await db.rollback()
                    return None

                fact_id = "gf_" + secrets.token_hex(6)
                await db.execute(
                    "INSERT INTO graph_facts "
                    "(fact_id, subject, predicate, object, confidence, status, scope, scope_id, source, supersedes, "
                    "metadata_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fact_id,
                        candidate.subject,
                        candidate.predicate,
                        candidate.object,
                        candidate.confidence,
                        "active",
                        candidate.scope,
                        candidate.scope_id,
                        candidate.source,
                        None,
                        json.dumps(metadata or {}, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                await self._insert_evidence(fact_id, evidence, db=db)

                evidence_cursor = await db.execute(
                    "SELECT * FROM graph_evidence WHERE fact_id = ? ORDER BY created_at ASC",
                    (fact_id,),
                )
                evidence_rows = [
                    _row_to_evidence(ev_row) for ev_row in await evidence_cursor.fetchall()
                ]
                await db.commit()
            except GraphProvenanceError:
                with contextlib.suppress(Exception):
                    await db.rollback()
                raise
            except BaseException:
                with contextlib.suppress(Exception):
                    await db.rollback()
                raise

            fact = GraphFact(
                fact_id=fact_id,
                subject=candidate.subject,
                predicate=candidate.predicate,
                object=candidate.object,
                confidence=candidate.confidence,
                status="active",
                source=candidate.source,
                scope=candidate.scope,
                scope_id=candidate.scope_id,
                supersedes=None,
                metadata=metadata or {},
                evidence=evidence_rows,
                created_at=now,
                updated_at=now,
            )
            return fact, evidence

    async def set_fact_status(
        self,
        fact_id: str,
        status: str,
        *,
        metadata_update: dict[str, Any] | None = None,
    ) -> bool:
        fact = await self.get_fact(fact_id)
        if fact is None:
            return False
        metadata = dict(fact.metadata)
        if metadata_update:
            metadata.update(metadata_update)
        db = self._require_db()
        cursor = await db.execute(
            "UPDATE graph_facts SET status = ?, metadata_json = ?, updated_at = ? WHERE fact_id = ?",
            (status, json.dumps(metadata, ensure_ascii=False), _now_iso(), fact_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def set_fact_cross_group_visibility(
        self,
        fact_id: str,
        *,
        visible: bool | None = None,
        visibility: CrossGroupVisibility | None = None,
        actor: str,
        reason: str = "",
        enabled_for_groups: list[str] | None = None,
    ) -> bool:
        """Toggle cross-group visibility on a graph fact.

        KG has no revision table yet (Phase A.5 graph schema will add one), so
        we log to loguru as the audit trail in the meantime.
        """
        fact = await self.get_fact(fact_id)
        if fact is None:
            return False
        now = _now_iso()
        resolved_visibility = resolve_cross_group_visibility(visible=visible, visibility=visibility)
        enabled = resolved_visibility != "none"
        enabled_by = actor if enabled else ""
        enabled_at = now if enabled else ""
        groups_payload = (
            [str(g).strip() for g in (enabled_for_groups or []) if str(g).strip()]
            if enabled
            else []
        )
        reason_payload = reason if enabled else ""
        db = self._require_db()
        cursor = await db.execute(
            """UPDATE graph_facts
               SET cross_group_visible = ?, cross_group_enabled_by = ?,
                   cross_group_enabled_at = ?, cross_group_enabled_for_groups = ?,
                   cross_group_enabled_reason = ?, updated_at = ?
               WHERE fact_id = ?""",
            (
                visibility_to_db(resolved_visibility),
                enabled_by,
                enabled_at,
                json.dumps(groups_payload, ensure_ascii=False),
                reason_payload,
                now,
                fact_id,
            ),
        )
        await db.commit()
        if cursor.rowcount <= 0:
            return False
        logger.info(
            "graph_fact cross_group {} by={} reason={!r} groups={} fact_id={}",
            "enable" if enabled else "disable",
            actor,
            reason,
            groups_payload,
            fact_id,
        )
        return True

    async def set_candidate_cross_group_visibility(
        self,
        candidate_id: str,
        *,
        visible: bool | None = None,
        visibility: CrossGroupVisibility | None = None,
        actor: str,
        reason: str = "",
        enabled_for_groups: list[str] | None = None,
    ) -> bool:
        """Toggle cross-group visibility on an extraction candidate."""
        candidate = await self.get_candidate(candidate_id)
        if candidate is None:
            return False
        now = _now_iso()
        resolved_visibility = resolve_cross_group_visibility(visible=visible, visibility=visibility)
        enabled = resolved_visibility != "none"
        enabled_by = actor if enabled else ""
        enabled_at = now if enabled else ""
        groups_payload = (
            [str(g).strip() for g in (enabled_for_groups or []) if str(g).strip()]
            if enabled
            else []
        )
        reason_payload = reason if enabled else ""
        db = self._require_db()
        cursor = await db.execute(
            """UPDATE extraction_candidates
               SET cross_group_visible = ?, cross_group_enabled_by = ?,
                   cross_group_enabled_at = ?, cross_group_enabled_for_groups = ?,
                   cross_group_enabled_reason = ?, updated_at = ?
               WHERE candidate_id = ?""",
            (
                visibility_to_db(resolved_visibility),
                enabled_by,
                enabled_at,
                json.dumps(groups_payload, ensure_ascii=False),
                reason_payload,
                now,
                candidate_id,
            ),
        )
        await db.commit()
        if cursor.rowcount <= 0:
            return False
        logger.info(
            "graph_candidate cross_group {} by={} reason={!r} groups={} candidate_id={}",
            "enable" if enabled else "disable",
            actor,
            reason,
            groups_payload,
            candidate_id,
        )
        return True

    async def health_snapshot(self) -> dict[str, Any]:
        """Read-only graph population + optional evidence-quality health.

        Returns the pre-gpo top-level fields always. When
        ``observability_enabled`` is True, adds nested ``observability``
        (gpo_v1) with closed secret-free counters. Never writes.
        """
        from datetime import timedelta, timezone

        from services.knowledge_graph.observability import (
            accumulate_active_evidence,
            accumulate_pending_candidate,
            empty_observability_payload,
        )

        if self._db is None:
            return {"available": False}

        db = self._db
        # Created_at is stored with +08:00 offset; build the cutoff in the
        # same form so lexical comparison works without julianday casts.
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        since_24h_iso = (now - timedelta(hours=24)).isoformat()

        cursor = await db.execute(
            "SELECT status, COUNT(*) FROM extraction_candidates "
            "WHERE created_at >= ? GROUP BY status",
            (since_24h_iso,),
        )
        candidate_24h = {row[0]: int(row[1]) for row in await cursor.fetchall()}

        cursor = await db.execute(
            "SELECT status, COUNT(*) FROM extraction_candidates GROUP BY status"
        )
        candidate_total = {row[0]: int(row[1]) for row in await cursor.fetchall()}

        cursor = await db.execute(
            "SELECT source, COUNT(*) FROM graph_facts "
            "WHERE status='active' GROUP BY source"
        )
        facts_active_by_source = {
            row[0]: int(row[1]) for row in await cursor.fetchall()
        }

        cursor = await db.execute(
            "SELECT COUNT(*) FROM graph_facts "
            "WHERE status='active' AND created_at >= ?",
            (since_24h_iso,),
        )
        row = await cursor.fetchone()
        facts_active_24h = int(row[0]) if row else 0

        cursor = await db.execute(
            "SELECT edge_type, COUNT(*) FROM graph_edges "
            "WHERE status='active' AND created_at >= ? GROUP BY edge_type",
            (since_24h_iso,),
        )
        edges_24h = {row[0]: int(row[1]) for row in await cursor.fetchall()}

        payload: dict[str, Any] = {
            "available": True,
            "checked_at": now.isoformat(),
            "since": since_24h_iso,
            "candidate_24h": candidate_24h,
            "candidate_total": candidate_total,
            "facts_active_by_source": facts_active_by_source,
            "facts_active_24h": facts_active_24h,
            "edges_24h": edges_24h,
        }

        if not self.observability_enabled:
            return payload

        obs = empty_observability_payload(
            provenance_gate_enabled=self.provenance_gate_enabled,
        )

        cursor = await db.execute(
            "SELECT fact_id FROM graph_facts WHERE status='active' "
            "ORDER BY fact_id ASC"
        )
        fact_ids = [str(r[0]) for r in await cursor.fetchall()]
        evidence_by_fact = await self.list_evidence_for_facts(fact_ids)
        for fact_id in fact_ids:
            accumulate_active_evidence(obs, evidence_by_fact.get(fact_id) or [])

        cursor = await db.execute(
            "SELECT evidence_json, review_note FROM extraction_candidates "
            "WHERE status='pending' ORDER BY candidate_id ASC"
        )
        for pend_row in await cursor.fetchall():
            raw_evidence: Any
            evidence_json = pend_row[0]
            if evidence_json is None or evidence_json == "":
                raw_evidence = None
            else:
                try:
                    raw_evidence = json.loads(evidence_json)
                except (json.JSONDecodeError, TypeError, ValueError):
                    raw_evidence = str(evidence_json)
            accumulate_pending_candidate(
                obs,
                evidence=raw_evidence,
                review_note=pend_row[1] or "",
            )

        payload["observability"] = obs
        return payload

    async def list_entities(self, *, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self._require_db().execute(
            "SELECT subject AS name, COUNT(*) AS fact_count FROM graph_facts WHERE status = 'active' "
            "GROUP BY subject UNION ALL "
            "SELECT object AS name, COUNT(*) AS fact_count FROM graph_facts WHERE status = 'active' GROUP BY object "
            "LIMIT ?",
            (limit,),
        )
        merged: dict[str, int] = {}
        for row in await cursor.fetchall():
            merged[row["name"]] = merged.get(row["name"], 0) + int(row["fact_count"])
        return [
            {"name": name, "fact_count": count}
            for name, count in sorted(merged.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]

    async def list_evidence(self, fact_id: str) -> list[dict[str, Any]]:
        cursor = await self._require_db().execute(
            "SELECT * FROM graph_evidence WHERE fact_id = ? ORDER BY created_at ASC",
            (fact_id,),
        )
        return [_row_to_evidence(row) for row in await cursor.fetchall()]

    async def list_evidence_for_facts(
        self,
        fact_ids: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Batch evidence lookup with bounded SQLite parameter chunks."""
        ordered_ids = list(dict.fromkeys(str(fact_id) for fact_id in fact_ids if fact_id))
        evidence_by_fact = {fact_id: [] for fact_id in ordered_ids}
        chunk_size = 400
        for offset in range(0, len(ordered_ids), chunk_size):
            chunk = ordered_ids[offset:offset + chunk_size]
            placeholders = ", ".join("?" for _ in chunk)
            cursor = await self._require_db().execute(
                "SELECT * FROM graph_evidence "
                f"WHERE fact_id IN ({placeholders}) "
                "ORDER BY fact_id ASC, created_at ASC, evidence_row_id ASC",
                chunk,
            )
            for row in await cursor.fetchall():
                evidence_by_fact[str(row["fact_id"])].append(_row_to_evidence(row))
        return evidence_by_fact

    async def find_fact_ids_by_evidence_refs(
        self,
        evidence_ids: Any,
        *,
        allowed_scopes: set[tuple[str, str]] | None = None,
    ) -> list[str]:
        """Return active fact_ids linked to any of the given evidence ids.

        Joins ``graph_evidence`` → ``graph_facts`` with ``status='active'``
        only. Results are deduped and ordered by fact_id ascending. When
        ``allowed_scopes`` is set, only facts whose (scope, scope_id) pair
        is allowed are returned.
        """
        if not evidence_ids:
            return []
        eids: list[str] = []
        for raw in evidence_ids:
            if raw is None:
                continue
            eid = str(raw).strip()
            if not eid or eid in eids:
                continue
            eids.append(eid)
        if not eids:
            return []
        placeholders = ", ".join("?" for _ in eids)
        sql = (
            "SELECT DISTINCT f.fact_id FROM graph_facts f "
            "JOIN graph_evidence e ON e.fact_id = f.fact_id "
            f"WHERE f.status = 'active' AND e.evidence_id IN ({placeholders})"
        )
        params: list[Any] = list(eids)
        if allowed_scopes is not None:
            scope_pairs = [
                (str(scope), str(scope_id))
                for scope, scope_id in allowed_scopes
                if scope is not None and scope_id is not None
            ]
            if not scope_pairs:
                return []
            scope_clauses = " OR ".join("(f.scope = ? AND f.scope_id = ?)" for _ in scope_pairs)
            sql += f" AND ({scope_clauses})"
            for scope, scope_id in scope_pairs:
                params.extend([scope, scope_id])
        sql += " ORDER BY f.fact_id ASC"
        cursor = await self._require_db().execute(sql, tuple(params))
        return [str(row["fact_id"]) for row in await cursor.fetchall()]

    async def _insert_evidence(
        self,
        fact_id: str,
        evidence: dict[str, Any],
        *,
        db: aiosqlite.Connection | None = None,
    ) -> None:
        """Insert one evidence row on *db* (or the ordinary store connection).

        Callers inside an open promotion transaction must pass that connection
        explicitly so the insert is not silently routed to ``self._db``.
        Evidence is expected to already be prepared via ``_prepare_evidence``.
        """
        conn = db if db is not None else self._require_db()
        evidence_type = _evidence_type(evidence)
        evidence_id = str(
            evidence.get("id")
            or evidence.get("card_id")
            or evidence.get("chunk_id")
            or evidence.get("message_id")
            or ""
        )
        quote = str(evidence.get("quote") or "")
        await conn.execute(
            "INSERT INTO graph_evidence (evidence_row_id, fact_id, evidence_type, evidence_id, quote, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ge_" + secrets.token_hex(6), fact_id, evidence_type, evidence_id, quote, _now_iso()),
        )

    def _prepare_evidence(
        self,
        evidence: dict[str, Any] | None,
        *,
        require_for_candidate: bool = False,
    ) -> dict[str, Any]:
        """Normalize (gate on) or legacy-require (gate off) write-side evidence.

        When the gate is enabled, always returns a stripped canonical dict and
        raises ``GraphProvenanceError`` on invalid input.

        When disabled (unsafe rollback), preserves legacy truthy acceptance:
        any non-empty id/card_id/chunk_id (including whitespace-only strings)
        is accepted for facts; candidates may store empty ``{}``.
        """
        raw = dict(evidence or {})
        if self.provenance_gate_enabled:
            return normalize_graph_evidence(raw)

        # Legacy unsafe path — keep truthy behavior for emergency rollback.
        if require_for_candidate and not raw:
            return {}
        self._require_evidence_legacy(raw)
        return raw

    @staticmethod
    def _require_evidence_legacy(evidence: dict[str, Any]) -> None:
        evidence_id = evidence.get("id") or evidence.get("card_id") or evidence.get("chunk_id")
        if not evidence_id:
            raise ValueError("graph fact requires card_id or chunk_id evidence")

    # Back-compat alias used by older tests/monkeypatches.
    _require_evidence = _require_evidence_legacy


def _row_to_fact(row: aiosqlite.Row) -> GraphFact:
    keys = row.keys()
    visibility = visibility_from_db(row["cross_group_visible"]) if "cross_group_visible" in keys else "none"
    return GraphFact(
        fact_id=row["fact_id"],
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        confidence=row["confidence"],
        status=row["status"],
        source=row["source"],
        scope=row["scope"],
        scope_id=row["scope_id"],
        supersedes=row["supersedes"],
        metadata=json.loads(row["metadata_json"] or "{}"),
        evidence=[],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        cross_group_visible=legacy_cross_group_visible(visibility),
        cross_group_visibility=visibility,
        cross_group_enabled_by=row["cross_group_enabled_by"] if "cross_group_enabled_by" in keys else "",
        cross_group_enabled_at=row["cross_group_enabled_at"] if "cross_group_enabled_at" in keys else "",
        cross_group_enabled_for_groups=_parse_groups_list(
            row["cross_group_enabled_for_groups"] if "cross_group_enabled_for_groups" in keys else ""
        ),
        cross_group_enabled_reason=row["cross_group_enabled_reason"] if "cross_group_enabled_reason" in keys else "",
    )


def _parse_groups_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    return []


def _row_to_evidence(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "evidence_row_id": row["evidence_row_id"],
        "fact_id": row["fact_id"],
        "type": row["evidence_type"],
        "id": row["evidence_id"],
        "quote": row["quote"] or "",
        "created_at": row["created_at"],
    }


def _row_to_candidate(row: aiosqlite.Row) -> GraphCandidate:
    keys = row.keys()
    visibility = visibility_from_db(row["cross_group_visible"]) if "cross_group_visible" in keys else "none"
    return GraphCandidate(
        candidate_id=row["candidate_id"],
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        confidence=row["confidence"],
        status=row["status"],
        source=row["source"],
        scope=row["scope"],
        scope_id=row["scope_id"],
        evidence=json.loads(row["evidence_json"] or "{}"),
        review_note=row["review_note"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        cross_group_visible=legacy_cross_group_visible(visibility),
        cross_group_visibility=visibility,
        cross_group_enabled_by=row["cross_group_enabled_by"] if "cross_group_enabled_by" in keys else "",
        cross_group_enabled_at=row["cross_group_enabled_at"] if "cross_group_enabled_at" in keys else "",
        cross_group_enabled_for_groups=_parse_groups_list(
            row["cross_group_enabled_for_groups"] if "cross_group_enabled_for_groups" in keys else ""
        ),
        cross_group_enabled_reason=row["cross_group_enabled_reason"] if "cross_group_enabled_reason" in keys else "",
    )


def _evidence_type(evidence: dict[str, Any]) -> str:
    explicit = str(evidence.get("type") or "").strip()
    if explicit:
        return explicit
    if evidence.get("card_id"):
        return "memory_card"
    if evidence.get("chunk_id"):
        return "doc_chunk"
    if evidence.get("message_id"):
        return "message"
    return ""



def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
