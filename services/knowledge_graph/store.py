"""SQLite store for derived graph facts and extraction candidates."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

from services.knowledge_graph.types import GraphCandidate, GraphFact
from services.storage import connect_sqlite

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

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_graph_facts_subject ON graph_facts(subject, status)",
    "CREATE INDEX IF NOT EXISTS idx_graph_facts_object ON graph_facts(object, status)",
    "CREATE INDEX IF NOT EXISTS idx_graph_facts_scope ON graph_facts(scope, scope_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_graph_candidates_status ON extraction_candidates(status, confidence)",
    "CREATE INDEX IF NOT EXISTS idx_graph_candidates_scope ON extraction_candidates(scope, scope_id, status)",
]


class KnowledgeGraphStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await connect_sqlite(self._db_path)
        await self._db.execute(_CREATE_FACTS)
        await self._db.execute(_CREATE_EVIDENCE)
        await self._db.execute(_CREATE_CANDIDATES)
        await self._ensure_column("graph_facts", "scope", "TEXT NOT NULL DEFAULT 'global'")
        await self._ensure_column("graph_facts", "scope_id", "TEXT NOT NULL DEFAULT 'global'")
        await self._ensure_column("extraction_candidates", "scope", "TEXT NOT NULL DEFAULT 'global'")
        await self._ensure_column("extraction_candidates", "scope_id", "TEXT NOT NULL DEFAULT 'global'")
        for statement in _CREATE_INDEXES:
            await self._db.execute(statement)
        await self._db.commit()

    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        cursor = await self._db.execute(f"PRAGMA table_info({table})")
        columns = {row["name"] for row in await cursor.fetchall()}
        if column not in columns:
            await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def add_fact(
        self,
        *,
        subject: str,
        predicate: str,
        object: str,
        confidence: float,
        source: str,
        evidence: dict[str, Any],
        status: str = "active",
        scope: str = "global",
        scope_id: str = "global",
        supersedes: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GraphFact:
        self._require_evidence(evidence)
        fact_id = "gf_" + secrets.token_hex(6)
        now = _now_iso()
        await self._db.execute(
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
        await self._db.commit()
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
        status: str = "pending",
        scope: str = "global",
        scope_id: str = "global",
    ) -> GraphCandidate:
        candidate_id = "gc_" + secrets.token_hex(6)
        now = _now_iso()
        await self._db.execute(
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
        await self._db.commit()
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
        cursor = await self._db.execute(
            "SELECT * FROM graph_facts WHERE status = ? ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (status, limit),
        )
        return [_row_to_fact(row) for row in await cursor.fetchall()]

    async def list_scope_risk_facts(self, *, limit: int = 100) -> list[GraphFact]:
        """List legacy global facts that appear to come from memory-card evidence."""
        cursor = await self._db.execute(
            "SELECT DISTINCT f.* FROM graph_facts f "
            "JOIN graph_evidence e ON e.fact_id = f.fact_id "
            "WHERE f.status = 'active' AND f.scope = 'global' AND f.scope_id = 'global' "
            "AND (e.evidence_type = 'memory_card' OR e.evidence_id LIKE 'card_%') "
            "ORDER BY f.updated_at DESC, f.confidence DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_fact(row) for row in await cursor.fetchall()]

    async def get_fact(self, fact_id: str) -> GraphFact | None:
        cursor = await self._db.execute(
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
        cursor = await self._db.execute(
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
        cursor = await self._db.execute(
            "SELECT * FROM extraction_candidates "
            f"WHERE subject = ? AND predicate = ? AND object = ? AND scope = ? AND scope_id = ? "
            f"AND status IN ({placeholders}) "
            "ORDER BY confidence DESC, updated_at DESC LIMIT 1",
            (subject, predicate, object, scope, scope_id, *statuses),
        )
        row = await cursor.fetchone()
        return _row_to_candidate(row) if row else None

    async def list_candidates(self, *, status: str = "pending", limit: int = 100) -> list[GraphCandidate]:
        cursor = await self._db.execute(
            "SELECT * FROM extraction_candidates WHERE status = ? ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (status, limit),
        )
        return [_row_to_candidate(row) for row in await cursor.fetchall()]

    async def get_candidate(self, candidate_id: str) -> GraphCandidate | None:
        cursor = await self._db.execute(
            "SELECT * FROM extraction_candidates WHERE candidate_id = ?",
            (candidate_id,),
        )
        row = await cursor.fetchone()
        return _row_to_candidate(row) if row else None

    async def set_candidate_status(self, candidate_id: str, status: str, *, review_note: str = "") -> bool:
        cursor = await self._db.execute(
            "UPDATE extraction_candidates SET status = ?, review_note = ?, updated_at = ? WHERE candidate_id = ?",
            (status, review_note, _now_iso(), candidate_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

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
        cursor = await self._db.execute(
            "UPDATE graph_facts SET status = ?, metadata_json = ?, updated_at = ? WHERE fact_id = ?",
            (status, json.dumps(metadata, ensure_ascii=False), _now_iso(), fact_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_entities(self, *, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self._db.execute(
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
        cursor = await self._db.execute(
            "SELECT * FROM graph_evidence WHERE fact_id = ? ORDER BY created_at ASC",
            (fact_id,),
        )
        return [_row_to_evidence(row) for row in await cursor.fetchall()]

    async def _insert_evidence(self, fact_id: str, evidence: dict[str, Any]) -> None:
        evidence_type = _evidence_type(evidence)
        evidence_id = str(evidence.get("id") or evidence.get("card_id") or evidence.get("chunk_id") or "")
        quote = str(evidence.get("quote") or "")
        await self._db.execute(
            "INSERT INTO graph_evidence (evidence_row_id, fact_id, evidence_type, evidence_id, quote, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ge_" + secrets.token_hex(6), fact_id, evidence_type, evidence_id, quote, _now_iso()),
        )

    @staticmethod
    def _require_evidence(evidence: dict[str, Any]) -> None:
        evidence_id = evidence.get("id") or evidence.get("card_id") or evidence.get("chunk_id")
        if not evidence_id:
            raise ValueError("graph fact requires card_id or chunk_id evidence")


def _row_to_fact(row: aiosqlite.Row) -> GraphFact:
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
    )


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
    )


def _evidence_type(evidence: dict[str, Any]) -> str:
    explicit = str(evidence.get("type") or "").strip()
    if explicit:
        return explicit
    if evidence.get("card_id"):
        return "memory_card"
    if evidence.get("chunk_id"):
        return "doc_chunk"
    return ""


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
