"""EpisodeStore: 5-state episodic memory with revision tracking."""

from __future__ import annotations

import contextlib
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
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
from services.storage import close_with_checkpoint, connect_sqlite

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

EpisodeState = Literal["dry_run", "candidate", "approved", "enabled_for_prompt", "disabled"]
EpisodeScope = Literal["group", "global"]

EPISODE_STATES: tuple[str, ...] = ("dry_run", "candidate", "approved", "enabled_for_prompt", "disabled")
VALID_TRANSITIONS: dict[str, set[str]] = {
    "dry_run": {"candidate", "disabled"},
    "candidate": {"approved", "disabled"},
    "approved": {"enabled_for_prompt", "disabled"},
    "enabled_for_prompt": {"disabled"},
    "disabled": {"approved"},
}
PER_GROUP_MAX_ACTIVE = 50
CANDIDATE_CONFIDENCE_THRESHOLD = 0.6

_CREATE_EPISODES_TABLE = """\
CREATE TABLE IF NOT EXISTS episodes (
    episode_id          TEXT PRIMARY KEY,
    group_id            TEXT NOT NULL DEFAULT '',
    scope               TEXT NOT NULL DEFAULT 'group',
    situation           TEXT NOT NULL,
    observed_context    TEXT NOT NULL DEFAULT '',
    action_taken        TEXT NOT NULL DEFAULT '',
    outcome_signal      TEXT NOT NULL DEFAULT '',
    reflection          TEXT NOT NULL DEFAULT '',
    linked_memory_ids   TEXT NOT NULL DEFAULT '[]',
    confidence          REAL NOT NULL DEFAULT 0.5,
    episode_state       TEXT NOT NULL DEFAULT 'dry_run',
    source              TEXT NOT NULL DEFAULT 'consolidator',
    decay_at            TEXT NOT NULL DEFAULT '',
    last_used_at        TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    disabled_by_admin   INTEGER NOT NULL DEFAULT 0,
    cross_group_visible       INTEGER NOT NULL DEFAULT 0,
    cross_group_enabled_by    TEXT NOT NULL DEFAULT '',
    cross_group_enabled_at    TEXT NOT NULL DEFAULT '',
    cross_group_enabled_for_groups TEXT NOT NULL DEFAULT '[]',
    cross_group_enabled_reason     TEXT NOT NULL DEFAULT '',
    meta_json           TEXT NOT NULL DEFAULT '{}'
)"""

_CREATE_REVISIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS episode_revisions (
    revision_id   TEXT PRIMARY KEY,
    episode_id    TEXT NOT NULL,
    action        TEXT NOT NULL,
    actor         TEXT NOT NULL DEFAULT 'system',
    prev_state    TEXT NOT NULL DEFAULT '',
    new_state     TEXT NOT NULL DEFAULT '',
    before_json   TEXT NOT NULL DEFAULT '{}',
    after_json    TEXT NOT NULL DEFAULT '{}',
    reason        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    meta_json     TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE
)"""

_CREATE_OBSERVATIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS episode_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id    TEXT NOT NULL,
    scope         TEXT NOT NULL,
    group_id      TEXT NOT NULL DEFAULT '',
    observed_at   TEXT NOT NULL,
    trigger_type  TEXT NOT NULL,
    message_id    TEXT NOT NULL DEFAULT '',
    meta          TEXT NOT NULL DEFAULT '{}',
    UNIQUE(episode_id, message_id, trigger_type) ON CONFLICT IGNORE
)"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_episode_state ON episodes(episode_state, group_id)",
    "CREATE INDEX IF NOT EXISTS idx_episode_group ON episodes(scope, group_id, episode_state)",
    "CREATE INDEX IF NOT EXISTS idx_episode_decay ON episodes(decay_at) WHERE decay_at != ''",
    (
        "CREATE INDEX IF NOT EXISTS idx_episode_cross_group "
        "ON episodes(cross_group_visible, episode_state) WHERE cross_group_visible IN (1, 2)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_episode_rev ON episode_revisions(episode_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_episode_obs_today ON episode_observations(observed_at, episode_id)",
    "CREATE INDEX IF NOT EXISTS idx_episode_obs_scope ON episode_observations(scope, group_id, observed_at)",
]


@dataclass
class Episode:
    episode_id: str
    group_id: str
    scope: EpisodeScope
    situation: str
    observed_context: str
    action_taken: str
    outcome_signal: str
    reflection: str
    linked_memory_ids: list[str]
    confidence: float
    episode_state: EpisodeState
    source: str
    decay_at: str
    last_used_at: str
    created_at: str
    updated_at: str
    disabled_by_admin: bool = False
    cross_group_visible: bool = False
    cross_group_visibility: CrossGroupVisibility = "none"
    cross_group_enabled_by: str = ""
    cross_group_enabled_at: str = ""
    cross_group_enabled_for_groups: list[str] = field(default_factory=list)
    cross_group_enabled_reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class EpisodeRevision:
    revision_id: str
    episode_id: str
    action: str
    actor: str
    prev_state: str
    new_state: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str
    created_at: str
    meta: dict[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


def _generate_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


_PHASE_B_BUS_METHODS: tuple[str, ...] = ("record", "list_for_request", "find_by_source_ref")


def _phase_b_unlocked() -> bool:
    """Phase B deployment gate for ``enabled_for_prompt``.

    Returns True only when the BlockTraceBus implementation referenced by
    multilayer-memory plan § 10.2 is importable and exposes the protocol's
    required methods. This is a deploy-time check, not a per-request health
    probe — the goal is to refuse to promote episodes into the live prompt
    when the trace bus that audits them isn't shipped yet.
    """
    try:
        from services.block_trace import BlockTraceBus
    except ImportError:
        return False
    return all(hasattr(BlockTraceBus, name) for name in _PHASE_B_BUS_METHODS)


def _row_to_episode(row: aiosqlite.Row) -> Episode:
    keys = row.keys()
    d = dict(row)
    linked: list[str] = []
    if "linked_memory_ids" in keys:
        raw = d["linked_memory_ids"] or "[]"
        try:
            linked = json.loads(raw) if isinstance(raw, str) else []
        except (json.JSONDecodeError, TypeError):
            linked = []
    meta: dict[str, Any] = {}
    if "meta_json" in keys:
        try:
            meta = json.loads(d.get("meta_json") or "{}") or {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
    return Episode(
        episode_id=d["episode_id"],
        group_id=d.get("group_id", ""),
        scope=d.get("scope", "group"),
        situation=d.get("situation", ""),
        observed_context=d.get("observed_context", ""),
        action_taken=d.get("action_taken", ""),
        outcome_signal=d.get("outcome_signal", ""),
        reflection=d.get("reflection", ""),
        linked_memory_ids=linked,
        confidence=float(d.get("confidence", 0.5)),
        episode_state=d.get("episode_state", "dry_run"),
        source=d.get("source", "consolidator"),
        decay_at=d.get("decay_at", ""),
        last_used_at=d.get("last_used_at", ""),
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at", ""),
        disabled_by_admin=bool(d.get("disabled_by_admin", 0)),
        cross_group_visible=legacy_cross_group_visible(visibility_from_db(d.get("cross_group_visible", 0))),
        cross_group_visibility=visibility_from_db(d.get("cross_group_visible", 0)),
        cross_group_enabled_by=d.get("cross_group_enabled_by", ""),
        cross_group_enabled_at=d.get("cross_group_enabled_at", ""),
        cross_group_enabled_for_groups=_parse_string_list(d.get("cross_group_enabled_for_groups", "")),
        cross_group_enabled_reason=d.get("cross_group_enabled_reason", ""),
        meta=meta,
    )


def _parse_string_list(raw: Any) -> list[str]:
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


def _row_to_revision(row: aiosqlite.Row) -> EpisodeRevision:
    d = dict(row)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        before = json.loads(d.get("before_json") or "{}") or {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        after = json.loads(d.get("after_json") or "{}") or {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        meta = json.loads(d.get("meta_json") or "{}") or {}
    return EpisodeRevision(
        revision_id=d["revision_id"],
        episode_id=d["episode_id"],
        action=d.get("action", ""),
        actor=d.get("actor", "system"),
        prev_state=d.get("prev_state", ""),
        new_state=d.get("new_state", ""),
        before=before,
        after=after,
        reason=d.get("reason", ""),
        created_at=d.get("created_at", ""),
        meta=meta,
    )


class EpisodeStore:
    """Episodic memory store with 5-state lifecycle and revision history."""

    def __init__(self, db_path: str = "storage/episodic.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        # Transition listeners — subscribed by side-channel writers (D.5
        # graph edge bridge, future declarative_facts). Each listener is
        # awaited inside try/except: graph mirroring must never roll back
        # a state transition (audit § D.5 "写 graph 失败不回滚").
        self._transition_listeners: list[
            Callable[[Episode, str, str, str], Awaitable[None]]
        ] = []

    def add_transition_listener(
        self,
        listener: Callable[[Episode, str, str, str], Awaitable[None]],
    ) -> None:
        """Register a coroutine to fire after every successful transition.

        Listener signature: ``async (episode, prev_state, new_state, actor)``.
        The ``episode`` snapshot is the row **before** the transition (so
        ``episode.episode_state == prev_state``); listeners that need the
        post-transition row should re-fetch via ``get_episode``.

        Listener exceptions are swallowed and logged at WARN — the graph
        bridge is an auxiliary index, never a blocker on the source-of-
        truth state machine.
        """
        self._transition_listeners.append(listener)

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("EpisodeStore not initialized — call init() first")
        return self._db

    async def init(self) -> None:
        self._db = await connect_sqlite(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_EPISODES_TABLE)
        await self._db.execute(_CREATE_REVISIONS_TABLE)
        await self._db.execute(_CREATE_OBSERVATIONS_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await self._db.execute(idx_sql)
        await self._ensure_column(
            "episodes", "cross_group_enabled_for_groups", "TEXT NOT NULL DEFAULT '[]'"
        )
        await self._ensure_column(
            "episodes", "cross_group_enabled_reason", "TEXT NOT NULL DEFAULT ''"
        )
        await self._db.commit()
        logger.info("EpisodeStore initialized: {}", self._db_path)

    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        db = self._require_db()
        cursor = await db.execute(f"PRAGMA table_info({table})")
        names = {str(row["name"]) for row in await cursor.fetchall()}
        if column not in names:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def close(self) -> None:
        if self._db:
            await close_with_checkpoint(self._db, name="episodic")
            self._db = None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_episode(
        self,
        *,
        situation: str,
        observed_context: str = "",
        action_taken: str = "",
        outcome_signal: str = "",
        reflection: str = "",
        group_id: str = "",
        scope: str = "group",
        source: str = "consolidator",
        confidence: float = 0.5,
        linked_memory_ids: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Episode:
        db = self._require_db()
        now = _now_iso()
        episode_id = _generate_id("ep")
        confidence = _clamp01(confidence)
        linked = linked_memory_ids or []
        ep_meta = meta or {}

        await db.execute(
            """INSERT INTO episodes (
                episode_id, group_id, scope, situation, observed_context,
                action_taken, outcome_signal, reflection, linked_memory_ids,
                confidence, episode_state, source, decay_at, last_used_at,
                created_at, updated_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'dry_run', ?, '', '', ?, ?, ?)""",
            (
                episode_id, group_id, scope, situation, observed_context,
                action_taken, outcome_signal, reflection, json.dumps(linked),
                confidence, source, now, now, json.dumps(ep_meta),
            ),
        )
        await db.commit()

        return Episode(
            episode_id=episode_id,
            group_id=group_id,
            scope=scope,  # type: ignore[arg-type]
            situation=situation,
            observed_context=observed_context,
            action_taken=action_taken,
            outcome_signal=outcome_signal,
            reflection=reflection,
            linked_memory_ids=linked,
            confidence=confidence,
            episode_state="dry_run",
            source=source,
            decay_at="",
            last_used_at="",
            created_at=now,
            updated_at=now,
            meta=ep_meta,
        )

    async def get_episode(self, episode_id: str) -> Episode | None:
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_episode(row) if row else None

    async def list_episodes(
        self,
        *,
        group_id: str = "",
        state_filter: str | list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Episode]:
        db = self._require_db()
        clauses: list[str] = []
        params: list[Any] = []
        if group_id:
            clauses.append("group_id = ?")
            params.append(group_id)
        if state_filter:
            if isinstance(state_filter, str):
                clauses.append("episode_state = ?")
                params.append(state_filter)
            else:
                placeholders = ",".join("?" for _ in state_filter)
                clauses.append(f"episode_state IN ({placeholders})")
                params.extend(state_filter)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM episodes {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_episode(r) for r in rows]

    async def list_for_recall(
        self,
        *,
        group_id: str,
        limit: int = 3,
        include_decayed: bool = False,
    ) -> list[Episode]:
        """Episodes eligible for prompt injection (D.4 recall path).

        Default behavior keeps the Phase D recall invariant and only returns
        ``episode_state='enabled_for_prompt'`` rows scoped to ``group_id``.
        ``include_decayed=True`` widens the reader for long-range lookups so
        disabled/decayed episodes remain discoverable outside the prompt path.

        Order: ``confidence DESC, updated_at DESC`` so the most-trusted
        recently-promoted reflections surface first; ``last_used_at`` is
        deliberately not in the ORDER BY (it's an audit field, not a
        ranking signal — see ``update_last_used``).
        """
        if not group_id:
            return []
        db = self._require_db()
        states = ("enabled_for_prompt", "disabled") if include_decayed else ("enabled_for_prompt",)
        placeholders = ",".join("?" for _ in states)
        async with db.execute(
            "SELECT * FROM episodes "
            f"WHERE episode_state IN ({placeholders}) AND group_id = ? "
            "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (*states, group_id, max(0, int(limit))),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_episode(r) for r in rows]

    async def update_last_used(self, episode_id: str) -> bool:
        """Stamp ``last_used_at`` for an episode that was just recalled.

        Best-effort: returns ``False`` when the episode does not exist or
        has been deleted between recall and stamp; never raises so a slow
        UPDATE on the recall path can't mask the prompt injection itself.
        """
        if not episode_id:
            return False
        db = self._require_db()
        now = _now_iso()
        cursor = await db.execute(
            "UPDATE episodes SET last_used_at = ? WHERE episode_id = ?",
            (now, episode_id),
        )
        await db.commit()
        return (cursor.rowcount or 0) > 0

    async def record_observation(
        self,
        episode_id: str,
        *,
        message_id: str = "",
        trigger_type: str = "episode_inject",
        group_id: str = "",
        scope: str = "group",
        meta: dict[str, Any] | None = None,
        observed_at: str | None = None,
    ) -> bool:
        episode_id = str(episode_id or "").strip()
        if not episode_id:
            return False
        db = self._require_db()
        cursor = await db.execute(
            """INSERT OR IGNORE INTO episode_observations
               (episode_id, scope, group_id, observed_at, trigger_type, message_id, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                episode_id,
                "global" if scope == "global" else "group",
                str(group_id or ""),
                observed_at or _now_iso(),
                str(trigger_type or "episode_inject")[:80],
                str(message_id or ""),
                json.dumps(meta or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        await db.commit()
        return (cursor.rowcount or 0) > 0

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def transition_state(
        self,
        episode_id: str,
        *,
        new_state: str,
        actor: str = "system",
        reason: str = "",
    ) -> bool:
        db = self._require_db()
        ep = await self.get_episode(episode_id)
        if ep is None:
            return False

        current = ep.episode_state
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {current} -> {new_state}. "
                f"Allowed: {sorted(allowed)}"
            )

        if new_state == "enabled_for_prompt" and not _phase_b_unlocked():
            raise ValueError(
                "Cannot transition to enabled_for_prompt: Phase B BlockTraceBus not yet available"
            )

        if new_state == "approved":
            count = await self._count_active(group_id=ep.group_id)
            if count >= PER_GROUP_MAX_ACTIVE:
                raise ValueError(
                    f"Group {ep.group_id} has reached max active episodes ({PER_GROUP_MAX_ACTIVE})"
                )

        now = _now_iso()
        await db.execute(
            "UPDATE episodes SET episode_state = ?, updated_at = ? WHERE episode_id = ?",
            (new_state, now, episode_id),
        )
        await db.commit()

        await self.record_revision(
            episode_id,
            action=f"state_{current}_to_{new_state}",
            actor=actor,
            prev_state=current,
            new_state=new_state,
            before={"episode_state": current},
            after={"episode_state": new_state},
            reason=reason,
        )
        await self._fire_transition_listeners(ep, current, new_state, actor)
        return True

    async def _fire_transition_listeners(
        self,
        episode: Episode,
        prev_state: str,
        new_state: str,
        actor: str,
    ) -> None:
        for listener in self._transition_listeners:
            try:
                await listener(episode, prev_state, new_state, actor)
            except Exception as exc:
                logger.warning(
                    "episode transition listener failed | "
                    "episode={} {}->{} listener={} err={}",
                    episode.episode_id, prev_state, new_state,
                    getattr(listener, "__qualname__", repr(listener)),
                    exc,
                )

    async def auto_promote_dry_runs(self, *, group_id: str = "") -> int:
        db = self._require_db()
        clauses = ["episode_state = 'dry_run'", "confidence >= ?"]
        params: list[Any] = [CANDIDATE_CONFIDENCE_THRESHOLD]
        if group_id:
            clauses.append("group_id = ?")
            params.append(group_id)
        where = " AND ".join(clauses)
        async with db.execute(
            f"SELECT episode_id FROM episodes WHERE {where}", params
        ) as cur:
            rows = await cur.fetchall()

        promoted = 0
        for row in rows:
            try:
                await self.transition_state(
                    row["episode_id"], new_state="candidate", actor="system",
                    reason="auto-promote: confidence >= threshold",
                )
                promoted += 1
            except ValueError:
                pass
        return promoted

    # ------------------------------------------------------------------
    # Decay
    # ------------------------------------------------------------------

    async def expire_decayed(self) -> int:
        db = self._require_db()
        now = _now_iso()
        async with db.execute(
            "SELECT episode_id FROM episodes WHERE episode_state = 'enabled_for_prompt' "
            "AND decay_at != '' AND decay_at <= ?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()

        expired = 0
        for row in rows:
            try:
                await self.transition_state(
                    row["episode_id"], new_state="disabled", actor="system",
                    reason="decay_at expired",
                )
                expired += 1
            except ValueError:
                pass
        return expired

    # ------------------------------------------------------------------
    # Cross-group visibility
    # ------------------------------------------------------------------

    async def set_cross_group_visibility(
        self,
        episode_id: str,
        *,
        visible: bool | None = None,
        visibility: CrossGroupVisibility | None = None,
        actor: str,
        reason: str = "",
        enabled_for_groups: list[str] | None = None,
    ) -> bool:
        db = self._require_db()
        ep = await self.get_episode(episode_id)
        if ep is None:
            return False
        now = _now_iso()
        resolved_visibility = resolve_cross_group_visibility(visible=visible, visibility=visibility)
        new_val = visibility_to_db(resolved_visibility)
        enabled = resolved_visibility != "none"
        enabled_by = actor if enabled else ""
        enabled_at = now if enabled else ""
        groups_payload = (
            [str(g).strip() for g in (enabled_for_groups or []) if str(g).strip()]
            if enabled
            else []
        )
        reason_payload = reason if enabled else ""
        await db.execute(
            "UPDATE episodes SET cross_group_visible = ?, cross_group_enabled_by = ?, "
            "cross_group_enabled_at = ?, cross_group_enabled_for_groups = ?, "
            "cross_group_enabled_reason = ?, updated_at = ? WHERE episode_id = ?",
            (
                new_val,
                enabled_by,
                enabled_at,
                json.dumps(groups_payload, ensure_ascii=False),
                reason_payload,
                now,
                episode_id,
            ),
        )
        await db.commit()
        action = "cross_group_enable" if enabled else "cross_group_disable"
        await self.record_revision(
            episode_id,
            action=action,
            actor=actor,
            prev_state=ep.episode_state,
            new_state=ep.episode_state,
            before={
                "cross_group_visible": ep.cross_group_visible,
                "cross_group_visibility": ep.cross_group_visibility,
                "cross_group_enabled_for_groups": list(ep.cross_group_enabled_for_groups),
                "cross_group_enabled_reason": ep.cross_group_enabled_reason,
            },
            after={
                "cross_group_visible": enabled,
                "cross_group_visibility": resolved_visibility,
                "cross_group_enabled_for_groups": groups_payload,
                "cross_group_enabled_reason": reason_payload,
            },
            reason=reason or f"cross_group_visibility={resolved_visibility}",
        )
        return True

    # ------------------------------------------------------------------
    # Revision
    # ------------------------------------------------------------------

    async def record_revision(
        self,
        episode_id: str,
        *,
        action: str,
        actor: str = "system",
        prev_state: str = "",
        new_state: str = "",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason: str = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        db = self._require_db()
        revision_id = _generate_id("eprev")
        now = _now_iso()
        await db.execute(
            """INSERT INTO episode_revisions (
                revision_id, episode_id, action, actor, prev_state, new_state,
                before_json, after_json, reason, created_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                revision_id, episode_id, action, actor, prev_state, new_state,
                json.dumps(before or {}), json.dumps(after or {}),
                reason, now, json.dumps(meta or {}),
            ),
        )
        await db.commit()
        return revision_id

    async def list_revisions(
        self, episode_id: str, *, limit: int = 50
    ) -> list[EpisodeRevision]:
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM episode_revisions WHERE episode_id = ? ORDER BY created_at DESC LIMIT ?",
            (episode_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_revision(r) for r in rows]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def count_by_state(self, *, group_id: str = "") -> dict[str, int]:
        db = self._require_db()
        if group_id:
            sql = "SELECT episode_state, COUNT(*) as cnt FROM episodes WHERE group_id = ? GROUP BY episode_state"
            params: tuple[Any, ...] = (group_id,)
        else:
            sql = "SELECT episode_state, COUNT(*) as cnt FROM episodes GROUP BY episode_state"
            params = ()
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        result: dict[str, int] = {s: 0 for s in EPISODE_STATES}
        for row in rows:
            state = row["episode_state"]
            if state in result:
                result[state] = row["cnt"]
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _count_active(self, *, group_id: str) -> int:
        db = self._require_db()
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM episodes WHERE group_id = ? "
            "AND episode_state IN ('approved', 'enabled_for_prompt')",
            (group_id,),
        ) as cur:
            row = await cur.fetchone()
        return row["cnt"] if row else 0
