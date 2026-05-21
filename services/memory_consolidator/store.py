"""ConsolidatorCandidatesStore — independent dry-run candidates db.

Schema mirrors the structure described in
``.claude/handoff/TASK-20260521-03-memory-consolidator-dryrun.md`` § 1.
Two tables — ``consolidator_runs`` + ``consolidator_candidates`` —
backed by ``storage/consolidator_candidates.db`` so dry-run output
**never** mixes with production slang/style/episodic/knowledge_graph
data. ``decide_candidate`` only updates the candidate's state field;
promotion to production stores is a future spec.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

import aiosqlite
from loguru import logger

from services.memory_consolidator.types import (
    CANDIDATE_DOMAINS,
    CANDIDATE_SCOPES,
    CANDIDATE_STATES,
    VALID_DECISION_TRANSITIONS,
    Candidate,
    CandidateDomain,
    CandidateScope,
    CandidateState,
    RunStatus,
    ScanRun,
    normalize_payload,
)
from services.storage import close_with_checkpoint, connect_sqlite

_L = logger.bind(channel="memory_consolidator")

_DEFAULT_DB_PATH = "storage/consolidator_candidates.db"

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS consolidator_runs (
    run_id            TEXT PRIMARY KEY,
    triggered_by      TEXT NOT NULL,
    group_id          TEXT NOT NULL DEFAULT '',
    scope             TEXT NOT NULL DEFAULT 'group',
    started_at        REAL NOT NULL,
    finished_at       REAL NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'running',
    scanned_count     INTEGER NOT NULL DEFAULT 0,
    candidates_count  INTEGER NOT NULL DEFAULT 0,
    error_text        TEXT NOT NULL DEFAULT '',
    meta_json         TEXT NOT NULL DEFAULT '{}'
)
"""

_CREATE_CANDIDATES = """
CREATE TABLE IF NOT EXISTS consolidator_candidates (
    candidate_id            TEXT PRIMARY KEY,
    run_id                  TEXT NOT NULL,
    domain                  TEXT NOT NULL,
    scope                   TEXT NOT NULL DEFAULT 'group',
    group_id                TEXT NOT NULL DEFAULT '',
    source_message_pks      TEXT NOT NULL DEFAULT '[]',
    payload_json            TEXT NOT NULL,
    confidence              REAL NOT NULL DEFAULT 0.0,
    state                   TEXT NOT NULL DEFAULT 'dry_run',
    decision_reason         TEXT NOT NULL DEFAULT '',
    decided_by              TEXT NOT NULL DEFAULT '',
    decided_at              REAL NOT NULL DEFAULT 0,
    normalizer_cluster_id   TEXT NOT NULL DEFAULT '',
    created_at              REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES consolidator_runs(run_id)
)
"""

_CREATE_REVISIONS = """
CREATE TABLE IF NOT EXISTS consolidator_candidate_revisions (
    revision_id     TEXT PRIMARY KEY,
    candidate_id    TEXT NOT NULL,
    action          TEXT NOT NULL,
    actor           TEXT NOT NULL DEFAULT '',
    before_json     TEXT NOT NULL DEFAULT '{}',
    after_json      TEXT NOT NULL DEFAULT '{}',
    reason          TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    meta_json       TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (candidate_id) REFERENCES consolidator_candidates(candidate_id)
)
"""

_CREATE_INDEXES = [
    (
        "CREATE INDEX IF NOT EXISTS idx_cand_run "
        "ON consolidator_candidates(run_id, state)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_cand_domain "
        "ON consolidator_candidates(domain, scope, group_id)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_cand_state "
        "ON consolidator_candidates(state, created_at)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_run_started "
        "ON consolidator_runs(started_at DESC)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_cand_revision "
        "ON consolidator_candidate_revisions(candidate_id, created_at DESC)"
    ),
]


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _now() -> float:
    return time.time()


def _row_to_candidate(row: aiosqlite.Row) -> Candidate:
    d = dict(row)
    pks_raw = d.get("source_message_pks") or "[]"
    try:
        parsed = json.loads(pks_raw) if isinstance(pks_raw, str) else []
    except json.JSONDecodeError:
        parsed = []
    pks = [
        int(item)
        for item in parsed
        if isinstance(item, int | float | str)
        and str(item).strip().lstrip("-").isdigit()
    ]
    payload_raw = d.get("payload_json") or "{}"
    try:
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return Candidate(
        candidate_id=str(d["candidate_id"]),
        run_id=str(d["run_id"]),
        domain=str(d["domain"]),  # type: ignore[arg-type]
        scope=str(d.get("scope", "group")),  # type: ignore[arg-type]
        group_id=str(d.get("group_id", "")),
        source_message_pks=pks,
        payload=payload,
        confidence=float(d.get("confidence", 0.0)),
        state=str(d.get("state", "dry_run")),  # type: ignore[arg-type]
        decision_reason=str(d.get("decision_reason", "")),
        decided_by=str(d.get("decided_by", "")),
        decided_at=float(d.get("decided_at", 0)),
        normalizer_cluster_id=str(d.get("normalizer_cluster_id", "")),
        created_at=float(d.get("created_at", 0)),
    )


def _row_to_run(row: aiosqlite.Row) -> ScanRun:
    d = dict(row)
    meta_raw = d.get("meta_json") or "{}"
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
    except json.JSONDecodeError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return ScanRun(
        run_id=str(d["run_id"]),
        triggered_by=str(d.get("triggered_by", "")),
        group_id=str(d.get("group_id", "")),
        scope=str(d.get("scope", "group")),  # type: ignore[arg-type]
        started_at=float(d.get("started_at", 0)),
        finished_at=float(d.get("finished_at", 0)),
        status=str(d.get("status", "running")),  # type: ignore[arg-type]
        scanned_count=int(d.get("scanned_count", 0)),
        candidates_count=int(d.get("candidates_count", 0)),
        error_text=str(d.get("error_text", "")),
        meta=meta,
    )


@dataclass(slots=True)
class CandidateFilter:
    run_id: str = ""
    domain: str = ""
    state: str = ""
    scope: str = ""
    group_id: str = ""


@dataclass(slots=True)
class CandidateRevision:
    """Append-only audit trail for admin-side payload edits.

    ``action`` is free-form (currently only ``"payload_edit"``); future
    actions can land here without a schema migration. ``before`` /
    ``after`` capture the projected payload on each side of the edit so
    admin can diff exactly what changed.
    """

    revision_id: str
    candidate_id: str
    action: str
    actor: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str
    created_at: float
    meta: dict[str, Any]


def _row_to_revision(row: aiosqlite.Row) -> CandidateRevision:
    d = dict(row)

    def _parse(field_name: str) -> dict[str, Any]:
        raw = d.get(field_name) or "{}"
        try:
            value = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            value = {}
        return value if isinstance(value, dict) else {}

    return CandidateRevision(
        revision_id=str(d["revision_id"]),
        candidate_id=str(d["candidate_id"]),
        action=str(d.get("action", "")),
        actor=str(d.get("actor", "")),
        before=_parse("before_json"),
        after=_parse("after_json"),
        reason=str(d.get("reason", "")),
        created_at=float(d.get("created_at", 0)),
        meta=_parse("meta_json"),
    )


class ConsolidatorCandidatesStore:
    """Independent SQLite store for typed dry-run candidates."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = str(db_path or _DEFAULT_DB_PATH)
        self._db: aiosqlite.Connection | None = None
        self.initialized = False

    @property
    def db_path(self) -> str:
        return self._db_path

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError(
                "ConsolidatorCandidatesStore not initialized — call init() first"
            )
        return self._db

    async def init(self) -> None:
        self._db = await connect_sqlite(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=DELETE")
        await self._db.execute("PRAGMA synchronous=FULL")
        await self._db.execute(_CREATE_RUNS)
        await self._db.execute(_CREATE_CANDIDATES)
        await self._db.execute(_CREATE_REVISIONS)
        for stmt in _CREATE_INDEXES:
            await self._db.execute(stmt)
        await self._db.commit()
        self.initialized = True
        _L.info("ConsolidatorCandidatesStore initialized: {}", self._db_path)

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="memory_consolidator")
            self._db = None
        self.initialized = False

    async def start_run(
        self,
        *,
        triggered_by: str,
        group_id: str,
        scope: CandidateScope = "group",
        meta: dict[str, Any] | None = None,
    ) -> str:
        if scope not in CANDIDATE_SCOPES:
            raise ValueError(f"invalid scope: {scope!r}")
        db = self._require_db()
        run_id = _gen_id("run")
        await db.execute(
            """INSERT INTO consolidator_runs
                   (run_id, triggered_by, group_id, scope, started_at,
                    status, meta_json)
               VALUES (?, ?, ?, ?, ?, 'running', ?)""",
            (
                run_id,
                str(triggered_by),
                str(group_id or ""),
                str(scope),
                _now(),
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        await db.commit()
        return run_id

    async def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        scanned_count: int = 0,
        candidates_count: int = 0,
        error_text: str = "",
    ) -> None:
        db = self._require_db()
        await db.execute(
            """UPDATE consolidator_runs
                   SET status = ?, finished_at = ?, scanned_count = ?,
                       candidates_count = ?, error_text = ?
                   WHERE run_id = ?""",
            (
                str(status),
                _now(),
                int(scanned_count),
                int(candidates_count),
                str(error_text or ""),
                str(run_id),
            ),
        )
        await db.commit()

    async def record_candidate(
        self,
        *,
        run_id: str,
        domain: CandidateDomain,
        scope: CandidateScope,
        group_id: str,
        source_message_pks: list[int],
        payload: dict[str, Any],
        confidence: float,
        normalizer_cluster_id: str = "",
    ) -> str:
        if domain not in CANDIDATE_DOMAINS:
            raise ValueError(f"invalid candidate domain: {domain!r}")
        if scope not in CANDIDATE_SCOPES:
            raise ValueError(f"invalid scope: {scope!r}")
        db = self._require_db()
        candidate_id = _gen_id("cand")
        clamped = max(0.0, min(1.0, float(confidence)))
        await db.execute(
            """INSERT INTO consolidator_candidates
                   (candidate_id, run_id, domain, scope, group_id,
                    source_message_pks, payload_json, confidence, state,
                    normalizer_cluster_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'dry_run', ?, ?)""",
            (
                candidate_id,
                str(run_id),
                str(domain),
                str(scope),
                str(group_id or ""),
                json.dumps([int(pk) for pk in source_message_pks], ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                clamped,
                str(normalizer_cluster_id or ""),
                _now(),
            ),
        )
        await db.commit()
        return candidate_id

    async def update_candidate_cluster(
        self,
        candidate_id: str,
        normalizer_cluster_id: str,
    ) -> None:
        db = self._require_db()
        await db.execute(
            """UPDATE consolidator_candidates
                   SET normalizer_cluster_id = ?
                   WHERE candidate_id = ?""",
            (str(normalizer_cluster_id or ""), str(candidate_id)),
        )
        await db.commit()

    async def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScanRun]:
        db = self._require_db()
        clamped_limit = max(1, min(int(limit), 200))
        async with db.execute(
            """SELECT * FROM consolidator_runs
               ORDER BY started_at DESC
               LIMIT ? OFFSET ?""",
            (clamped_limit, max(0, int(offset))),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_run(row) for row in rows]

    async def get_run(self, run_id: str) -> ScanRun | None:
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM consolidator_runs WHERE run_id = ?",
            (str(run_id),),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_run(row) if row else None

    async def list_candidates(
        self,
        *,
        run_id: str = "",
        domain: str = "",
        state: str = "",
        scope: str = "",
        group_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Candidate]:
        db = self._require_db()
        clauses: list[str] = []
        params: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(str(run_id))
        if domain:
            if domain not in CANDIDATE_DOMAINS:
                raise ValueError(f"invalid domain filter: {domain!r}")
            clauses.append("domain = ?")
            params.append(str(domain))
        if state:
            if state not in CANDIDATE_STATES:
                raise ValueError(f"invalid state filter: {state!r}")
            clauses.append("state = ?")
            params.append(str(state))
        if scope:
            if scope not in CANDIDATE_SCOPES:
                raise ValueError(f"invalid scope filter: {scope!r}")
            clauses.append("scope = ?")
            params.append(str(scope))
        if group_id:
            clauses.append("group_id = ?")
            params.append(str(group_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        clamped_limit = max(1, min(int(limit), 200))
        sql = (
            f"SELECT * FROM consolidator_candidates {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([clamped_limit, max(0, int(offset))])
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_candidate(row) for row in rows]

    async def get_candidate(self, candidate_id: str) -> Candidate | None:
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM consolidator_candidates WHERE candidate_id = ?",
            (str(candidate_id),),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_candidate(row) if row else None

    async def decide_candidate(
        self,
        candidate_id: str,
        *,
        state: CandidateState,
        decided_by: str,
        reason: str = "",
    ) -> bool:
        if state not in CANDIDATE_STATES:
            raise ValueError(f"invalid target state: {state!r}")
        existing = await self.get_candidate(candidate_id)
        if existing is None:
            return False
        allowed = VALID_DECISION_TRANSITIONS.get(existing.state, set())
        if state not in allowed:
            raise ValueError(
                f"invalid transition: {existing.state} -> {state}; "
                f"allowed={sorted(allowed)}"
            )
        db = self._require_db()
        await db.execute(
            """UPDATE consolidator_candidates
                   SET state = ?, decision_reason = ?, decided_by = ?,
                       decided_at = ?
                   WHERE candidate_id = ?""",
            (
                str(state),
                str(reason or ""),
                str(decided_by or ""),
                _now(),
                str(candidate_id),
            ),
        )
        await db.commit()
        return True

    _PAYLOAD_EDIT_ALLOWED_STATES: tuple[str, ...] = ("dry_run", "queued")

    async def update_candidate_payload(
        self,
        candidate_id: str,
        *,
        payload: dict[str, Any],
        actor: str,
        reason: str = "",
    ) -> Candidate | None:
        """Admin-only payload edit; gated on candidate ``state`` and
        normalized through :func:`normalize_payload`.

        Only ``state="dry_run"`` and ``state="queued"`` candidates may be
        edited. Post-decision (``approved`` / ``rejected``) edits are
        rejected with ``ValueError`` so the audit trail stays clean —
        admin must re-run consolidator for new candidates.

        The submitted ``payload`` is projected via
        :func:`normalize_payload` (``domain`` looked up from candidate)
        so unknown keys are silently dropped; the projected dict is what
        gets persisted and what gets logged into
        ``consolidator_candidate_revisions``.

        Returns the refreshed :class:`Candidate` or ``None`` if the row
        does not exist.
        """
        existing = await self.get_candidate(candidate_id)
        if existing is None:
            return None
        if existing.state not in self._PAYLOAD_EDIT_ALLOWED_STATES:
            raise ValueError(
                f"payload edit forbidden in state={existing.state!r}; "
                f"allowed={list(self._PAYLOAD_EDIT_ALLOWED_STATES)}"
            )
        try:
            projected = normalize_payload(existing.domain, payload)
        except ValueError:
            raise
        before_payload = dict(existing.payload)
        db = self._require_db()
        await db.execute(
            """UPDATE consolidator_candidates
                   SET payload_json = ?
                   WHERE candidate_id = ?""",
            (
                json.dumps(projected, ensure_ascii=False),
                str(candidate_id),
            ),
        )
        await self._record_revision(
            db=db,
            candidate_id=candidate_id,
            action="payload_edit",
            actor=actor,
            before={"payload": before_payload},
            after={"payload": projected},
            reason=reason,
            meta={"domain": existing.domain},
        )
        await db.commit()
        return await self.get_candidate(candidate_id)

    async def _record_revision(
        self,
        *,
        db: aiosqlite.Connection,
        candidate_id: str,
        action: str,
        actor: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason: str = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        revision_id = _gen_id("crev")
        await db.execute(
            """INSERT INTO consolidator_candidate_revisions
                   (revision_id, candidate_id, action, actor, before_json,
                    after_json, reason, created_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                revision_id,
                str(candidate_id),
                str(action),
                str(actor or ""),
                json.dumps(before or {}, ensure_ascii=False),
                json.dumps(after or {}, ensure_ascii=False),
                str(reason or ""),
                _now(),
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        return revision_id

    async def list_candidate_revisions(
        self,
        candidate_id: str,
        *,
        limit: int = 50,
    ) -> list[CandidateRevision]:
        db = self._require_db()
        clamped = max(1, min(int(limit), 200))
        async with db.execute(
            """SELECT * FROM consolidator_candidate_revisions
                   WHERE candidate_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
            (str(candidate_id), clamped),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_revision(row) for row in rows]
