"""SQLite store for LearningNormalizer clusters and audit trail."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import aiosqlite

from services.learning_normalizer.normalize import (
    NormalizationProfile,
    extract_features,
    fingerprint_key,
    normalize_key,
    score_similarity,
)
from services.storage import close_with_checkpoint, connect_sqlite

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
NormalizerDomain = Literal["slang", "style", "general"]
NormalizerScope = Literal["group", "global"]

_DEFAULT_DB_PATH = "storage/learning_normalizer.db"
_DEFAULT_FUZZY_THRESHOLD = 0.92

_CREATE_CLUSTERS = """\
CREATE TABLE IF NOT EXISTS learning_normalizer_clusters (
    cluster_id     TEXT PRIMARY KEY,
    domain         TEXT NOT NULL,
    scope          TEXT NOT NULL,
    group_id       TEXT NOT NULL DEFAULT '',
    canonical_text TEXT NOT NULL,
    canonical_key  TEXT NOT NULL,
    fingerprint    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    confidence     REAL NOT NULL DEFAULT 1.0,
    item_count     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    locked_at      TEXT NOT NULL DEFAULT '',
    meta_json      TEXT NOT NULL DEFAULT '{}'
)"""

_CREATE_ITEMS = """\
CREATE TABLE IF NOT EXISTS learning_normalizer_items (
    item_id       TEXT PRIMARY KEY,
    cluster_id    TEXT NOT NULL,
    domain        TEXT NOT NULL,
    scope         TEXT NOT NULL,
    group_id      TEXT NOT NULL DEFAULT '',
    raw_text      TEXT NOT NULL,
    normalized_key TEXT NOT NULL,
    fingerprint   TEXT NOT NULL DEFAULT '',
    source_table  TEXT NOT NULL DEFAULT '',
    source_id     TEXT NOT NULL DEFAULT '',
    message_id    INTEGER,
    user_id       TEXT NOT NULL DEFAULT '',
    count         INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    features_json TEXT NOT NULL DEFAULT '{}',
    meta_json     TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(cluster_id) REFERENCES learning_normalizer_clusters(cluster_id) ON DELETE CASCADE
)"""

_CREATE_REVISIONS = """\
CREATE TABLE IF NOT EXISTS learning_normalizer_revisions (
    revision_id TEXT PRIMARY KEY,
    cluster_id  TEXT NOT NULL DEFAULT '',
    item_id     TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'system',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json  TEXT NOT NULL DEFAULT '{}',
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    meta_json   TEXT NOT NULL DEFAULT '{}'
)"""

_INDEXES = [
    (
        "DROP INDEX IF EXISTS idx_ln_cluster_exact"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_ln_cluster_exact "
        "ON learning_normalizer_clusters(domain, scope, group_id, canonical_key)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_ln_cluster_lookup "
        "ON learning_normalizer_clusters(domain, scope, group_id, fingerprint)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_ln_items_cluster ON learning_normalizer_items(cluster_id, last_seen_at)",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ln_item_source "
        "ON learning_normalizer_items(domain, source_table, source_id, raw_text)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_ln_revision_cluster ON learning_normalizer_revisions(cluster_id, created_at)",
]


@dataclass(slots=True)
class LearningNormalizerCluster:
    cluster_id: str
    domain: str
    scope: str
    group_id: str
    canonical_text: str
    canonical_key: str
    fingerprint: str
    status: str
    confidence: float
    item_count: int
    created_at: str
    updated_at: str
    locked_at: str
    meta: dict[str, Any] = field(default_factory=dict)
    cross_group_visible: bool = False
    cross_group_enabled_by: str = ""
    cross_group_enabled_at: str = ""
    cross_group_enabled_for_groups: list[str] = field(default_factory=list)
    cross_group_enabled_reason: str = ""


@dataclass(slots=True)
class LearningNormalizerItem:
    item_id: str
    cluster_id: str
    domain: str
    scope: str
    group_id: str
    raw_text: str
    normalized_key: str
    fingerprint: str
    source_table: str
    source_id: str
    message_id: int | None
    user_id: str
    count: int
    first_seen_at: str
    last_seen_at: str
    features: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LearningNormalizerRevision:
    revision_id: str
    cluster_id: str
    item_id: str
    action: str
    actor: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str
    created_at: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizationResult:
    cluster_id: str
    item_id: str
    canonical_text: str
    canonical_key: str
    normalized_key: str
    fingerprint: str
    score: float
    method: str
    auto_merged: bool
    created_cluster: bool
    features: dict[str, Any] = field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        return {
            "normalization_cluster_id": self.cluster_id,
            "normalization_item_id": self.item_id,
            "normalized_from": self.canonical_text,
            "normalized_key": self.normalized_key,
            "normalization_method": self.method,
            "normalization_score": round(self.score, 4),
            "normalization_features": self.features,
            "auto_merged": self.auto_merged,
            "normalization_created_cluster": self.created_cluster,
        }


class LearningNormalizerStore:
    """Store and score normalized learning variants."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self.initialized = False

    async def init(self) -> None:
        self._db = await connect_sqlite(self._db_path)
        await self._db.execute("PRAGMA journal_mode=DELETE")
        await self._db.execute("PRAGMA synchronous=FULL")
        await self._db.execute(_CREATE_CLUSTERS)
        await self._db.execute(_CREATE_ITEMS)
        await self._db.execute(_CREATE_REVISIONS)
        for statement in _INDEXES:
            await self._db.execute(statement)
        # Cross-group visibility migration (A2)
        await self._ensure_column("learning_normalizer_clusters", "cross_group_visible", "INTEGER NOT NULL DEFAULT 0")
        await self._ensure_column("learning_normalizer_clusters", "cross_group_enabled_by", "TEXT NOT NULL DEFAULT ''")
        await self._ensure_column("learning_normalizer_clusters", "cross_group_enabled_at", "TEXT NOT NULL DEFAULT ''")
        await self._ensure_column(
            "learning_normalizer_clusters",
            "cross_group_enabled_for_groups",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        await self._ensure_column(
            "learning_normalizer_clusters",
            "cross_group_enabled_reason",
            "TEXT NOT NULL DEFAULT ''",
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ln_cross_group "
            "ON learning_normalizer_clusters(cross_group_visible, domain) "
            "WHERE cross_group_visible = 1"
        )
        await self._db.commit()
        self.initialized = True

    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        cursor = await self._db.execute(f"PRAGMA table_info({table})")
        names = {str(row["name"]) for row in await cursor.fetchall()}
        if column not in names:
            await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="learning_normalizer")
            self._db = None
        self.initialized = False

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("LearningNormalizerStore not initialized")
        return self._db

    async def attach_candidate(
        self,
        *,
        domain: NormalizerDomain,
        scope: NormalizerScope,
        group_id: str,
        raw_text: str,
        source_table: str,
        source_id: str,
        message_id: int | None = None,
        user_id: str = "",
        profile: NormalizationProfile | None = None,
        meta: dict[str, Any] | None = None,
    ) -> NormalizationResult:
        """Attach a source candidate to an existing or new cluster."""
        profile = profile or ("style" if domain == "style" else "slang")
        raw = str(raw_text or "").strip()
        key = normalize_key(raw, profile)
        if not key:
            raise ValueError("raw_text cannot be normalized")
        fp = fingerprint_key(raw, profile)
        normalized_group = "global" if scope == "global" else str(group_id or "").strip()
        now = _now_iso()
        db = self._require_db()
        cluster, score, method = await self._find_cluster(
            domain=domain,
            scope=scope,
            group_id=normalized_group,
            raw_text=raw,
            profile=profile,
        )
        created_cluster = cluster is None
        if cluster is None:
            cluster = await self._create_cluster(
                domain=domain,
                scope=scope,
                group_id=normalized_group,
                canonical_text=raw,
                canonical_key=key,
                fingerprint=fp,
                confidence=1.0,
                meta={"profile": profile, **(meta or {})},
            )
            score = 1.0
            method = "new_cluster"

        item_features = extract_features(raw, profile)
        item_meta = {
            **(meta or {}),
            "match_method": method,
            "match_score": round(score, 4),
            "cluster_canonical_text": cluster.canonical_text,
        }
        item = await self._upsert_item(
            cluster_id=cluster.cluster_id,
            domain=domain,
            scope=scope,
            group_id=normalized_group,
            raw_text=raw,
            normalized_key=key,
            fingerprint=fp,
            source_table=source_table,
            source_id=source_id,
            message_id=message_id,
            user_id=user_id,
            features=item_features,
            meta=item_meta,
            now=now,
        )
        await db.execute(
            """UPDATE learning_normalizer_clusters
               SET item_count = (
                       SELECT COALESCE(SUM(count), 0)
                         FROM learning_normalizer_items
                        WHERE cluster_id = ?
                   ),
                   updated_at = ?
               WHERE cluster_id = ?""",
            (cluster.cluster_id, now, cluster.cluster_id),
        )
        await db.commit()
        if created_cluster:
            await self._record_revision(
                cluster.cluster_id,
                action="create_cluster",
                after=self.cluster_to_dict(cluster),
                reason="normalizer candidate created a new cluster",
                meta={"source_table": source_table, "source_id": source_id},
            )
        elif method != "source_update":
            await self._record_revision(
                cluster.cluster_id,
                item_id=item.item_id,
                action="auto_merge",
                after={"item": self.item_to_dict(item), "score": score, "method": method},
                reason="candidate automatically attached to existing cluster",
                meta={"source_table": source_table, "source_id": source_id},
            )
        return NormalizationResult(
            cluster_id=cluster.cluster_id,
            item_id=item.item_id,
            canonical_text=cluster.canonical_text,
            canonical_key=cluster.canonical_key,
            normalized_key=key,
            fingerprint=fp,
            score=score,
            method=method,
            auto_merged=not created_cluster,
            created_cluster=created_cluster,
            features=item_features,
        )

    async def list_clusters(
        self,
        *,
        domain: str = "",
        scope: str = "",
        group_id: str = "",
        status: str = "",
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LearningNormalizerCluster], int]:
        db = self._require_db()
        where: list[str] = []
        values: list[Any] = []
        if domain:
            where.append("domain = ?")
            values.append(domain)
        if scope:
            where.append("scope = ?")
            values.append(scope)
        if group_id:
            where.append("group_id = ?")
            values.append(group_id)
        if status:
            where.append("status = ?")
            values.append(status)
        if search:
            where.append("(canonical_text LIKE ? OR canonical_key LIKE ?)")
            pattern = f"%{search}%"
            values.extend([pattern, pattern])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        total_row = await (await db.execute(
            f"SELECT COUNT(*) AS cnt FROM learning_normalizer_clusters {where_sql}",
            values,
        )).fetchone()
        cursor = await db.execute(
            f"""SELECT * FROM learning_normalizer_clusters {where_sql}
                ORDER BY updated_at DESC, item_count DESC
                LIMIT ? OFFSET ?""",
            [*values, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))],
        )
        return [_row_to_cluster(row) for row in await cursor.fetchall()], int(total_row["cnt"] if total_row else 0)

    async def list_cluster_items(self, cluster_id: str, *, limit: int = 100) -> list[LearningNormalizerItem]:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM learning_normalizer_items
               WHERE cluster_id = ?
               ORDER BY count DESC, last_seen_at DESC
               LIMIT ?""",
            (cluster_id, max(1, min(int(limit or 100), 300))),
        )
        return [_row_to_item(row) for row in await cursor.fetchall()]

    async def list_cluster_revisions(
        self,
        cluster_id: str,
        *,
        action: str = "",
        limit: int = 50,
    ) -> list[LearningNormalizerRevision]:
        db = self._require_db()
        where = ["cluster_id = ?"]
        values: list[Any] = [cluster_id]
        if action:
            where.append("action = ?")
            values.append(action)
        cursor = await db.execute(
            f"""SELECT * FROM learning_normalizer_revisions
               WHERE {' AND '.join(where)}
               ORDER BY created_at DESC
               LIMIT ?""",
            [*values, max(1, min(int(limit or 50), 200))],
        )
        return [_row_to_revision(row) for row in await cursor.fetchall()]

    async def get_cluster(self, cluster_id: str) -> LearningNormalizerCluster | None:
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM learning_normalizer_clusters WHERE cluster_id = ?", (cluster_id,))
        row = await cursor.fetchone()
        return _row_to_cluster(row) if row else None

    async def get_item(self, item_id: str) -> LearningNormalizerItem | None:
        return await self._get_item(item_id)

    async def get_revision(self, revision_id: str) -> LearningNormalizerRevision | None:
        return await self._get_revision(revision_id)

    async def lock_cluster(
        self,
        cluster_id: str,
        canonical_text: str,
        *,
        actor: str = "admin",
        reason: str = "",
    ) -> bool:
        cluster = await self.get_cluster(cluster_id)
        if cluster is None:
            return False
        profile = str(cluster.meta.get("profile") or cluster.domain)
        if profile not in {"general", "slang", "style"}:
            profile = "general"
        canonical = str(canonical_text or "").strip()
        key = normalize_key(canonical, profile)  # type: ignore[arg-type]
        if not key:
            raise ValueError("canonical_text cannot be empty")
        before = self.cluster_to_dict(cluster)
        db = self._require_db()
        now = _now_iso()
        await db.execute(
            """UPDATE learning_normalizer_clusters
               SET canonical_text = ?, canonical_key = ?, fingerprint = ?,
                   status = 'locked', locked_at = ?, updated_at = ?
               WHERE cluster_id = ?""",
            (canonical, key, fingerprint_key(canonical, profile), now, now, cluster_id),  # type: ignore[arg-type]
        )
        await db.commit()
        after = self.cluster_to_dict(await self.get_cluster(cluster_id))
        await self._record_revision(
            cluster_id,
            action="lock_cluster",
            actor=actor,
            before=before,
            after=after,
            reason=reason,
        )
        return True

    async def split_item(self, item_id: str, *, actor: str = "admin", reason: str = "") -> str | None:
        item = await self._get_item(item_id)
        if item is None:
            return None
        source_cluster = await self.get_cluster(item.cluster_id)
        if source_cluster is None:
            return None
        before = {"item": self.item_to_dict(item), "source_cluster": self.cluster_to_dict(source_cluster)}
        new_cluster = await self._create_cluster(
            domain=item.domain,
            scope=item.scope,
            group_id=item.group_id,
            canonical_text=item.raw_text,
            canonical_key=item.normalized_key,
            fingerprint=item.fingerprint,
            confidence=1.0,
            meta={"split_from_cluster_id": item.cluster_id, **item.meta},
        )
        db = self._require_db()
        now = _now_iso()
        await db.execute(
            """UPDATE learning_normalizer_items
               SET cluster_id = ?, last_seen_at = ?
               WHERE item_id = ?""",
            (new_cluster.cluster_id, now, item_id),
        )
        await self._refresh_cluster_counts([item.cluster_id, new_cluster.cluster_id])
        await db.commit()
        await self._record_revision(
            new_cluster.cluster_id,
            item_id=item_id,
            action="split_item",
            actor=actor,
            before=before,
            after={"new_cluster": self.cluster_to_dict(new_cluster)},
            reason=reason,
        )
        return new_cluster.cluster_id

    async def undo_revision(self, revision_id: str, *, actor: str = "admin") -> bool:
        """Undo safe revision kinds."""
        revision = await self._get_revision(revision_id)
        if revision is None:
            return False
        if revision.action == "auto_merge":
            return await self._undo_auto_merge(revision, actor=actor)
        if revision.action != "split_item":
            return False
        item_id = revision.item_id
        source = revision.before.get("source_cluster") if isinstance(revision.before, dict) else None
        source_cluster_id = str((source or {}).get("cluster_id") or "")
        if not item_id or not source_cluster_id:
            return False
        item = await self._get_item(item_id)
        if item is None:
            return False
        before = {"item": self.item_to_dict(item)}
        db = self._require_db()
        await db.execute(
            "UPDATE learning_normalizer_items SET cluster_id = ?, last_seen_at = ? WHERE item_id = ?",
            (source_cluster_id, _now_iso(), item_id),
        )
        await self._refresh_cluster_counts([item.cluster_id, source_cluster_id])
        await db.commit()
        await self._record_revision(
            source_cluster_id,
            item_id=item_id,
            action="undo_revision",
            actor=actor,
            before=before,
            after={"source_revision_id": revision_id, "restored_cluster_id": source_cluster_id},
            reason="undo learning normalizer revision",
        )
        return True

    async def _undo_auto_merge(self, revision: LearningNormalizerRevision, *, actor: str) -> bool:
        item = await self._get_item(revision.item_id)
        if item is None:
            return False
        current_cluster = await self.get_cluster(item.cluster_id)
        if current_cluster is None:
            return False
        before = {"item": self.item_to_dict(item), "cluster": self.cluster_to_dict(current_cluster)}
        new_cluster = await self._create_cluster(
            domain=item.domain,
            scope=item.scope,
            group_id=item.group_id,
            canonical_text=item.raw_text,
            canonical_key=item.normalized_key,
            fingerprint=item.fingerprint,
            confidence=1.0,
            meta={"undo_auto_merge_revision_id": revision.revision_id, **item.meta},
        )
        db = self._require_db()
        now = _now_iso()
        await db.execute(
            """UPDATE learning_normalizer_items
               SET cluster_id = ?, last_seen_at = ?
               WHERE item_id = ?""",
            (new_cluster.cluster_id, now, item.item_id),
        )
        await self._refresh_cluster_counts([item.cluster_id, new_cluster.cluster_id])
        await db.commit()
        await self._record_revision(
            new_cluster.cluster_id,
            item_id=item.item_id,
            action="undo_revision",
            actor=actor,
            before=before,
            after={
                "source_revision_id": revision.revision_id,
                "new_cluster": self.cluster_to_dict(new_cluster),
            },
            reason="undo learning normalizer auto merge",
        )
        return True

    async def _find_cluster(
        self,
        *,
        domain: str,
        scope: str,
        group_id: str,
        raw_text: str,
        profile: NormalizationProfile,
    ) -> tuple[LearningNormalizerCluster | None, float, str]:
        key = normalize_key(raw_text, profile)
        fp = fingerprint_key(raw_text, profile)
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM learning_normalizer_clusters
               WHERE domain = ? AND status IN ('active', 'locked')
                 AND ((scope = ? AND group_id = ?) OR cross_group_visible = 1)
               ORDER BY updated_at DESC""",
            (domain, scope, group_id),
        )
        best: tuple[LearningNormalizerCluster, float, str] | None = None
        for row in await cursor.fetchall():
            cluster = _row_to_cluster(row)
            score = score_similarity(raw_text, cluster.canonical_text, profile)
            method_score = score.score
            method = score.method
            if key == cluster.canonical_key:
                method_score = 1.0
                method = "exact"
            elif fp and fp == cluster.fingerprint:
                method_score = 0.96
                method = "fingerprint"
            if not _allows_fuzzy(key, cluster.canonical_key, method, method_score):
                continue
            if best is None or method_score > best[1]:
                best = (cluster, method_score, method)
        if best is None:
            return None, 0.0, "new_cluster"
        return best

    async def _create_cluster(
        self,
        *,
        domain: str,
        scope: str,
        group_id: str,
        canonical_text: str,
        canonical_key: str,
        fingerprint: str,
        confidence: float,
        meta: dict[str, Any] | None,
    ) -> LearningNormalizerCluster:
        db = self._require_db()
        now = _now_iso()
        cluster_id = _new_id("lnc")
        await db.execute(
            """INSERT INTO learning_normalizer_clusters
               (cluster_id, domain, scope, group_id, canonical_text, canonical_key,
                fingerprint, status, confidence, item_count, created_at, updated_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, 0, ?, ?, ?)""",
            (
                cluster_id,
                domain,
                scope,
                group_id,
                canonical_text,
                canonical_key,
                fingerprint,
                max(0.0, min(1.0, float(confidence))),
                now,
                now,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        cursor = await db.execute("SELECT * FROM learning_normalizer_clusters WHERE cluster_id = ?", (cluster_id,))
        row = await cursor.fetchone()
        return _row_to_cluster(row)

    async def _upsert_item(
        self,
        *,
        cluster_id: str,
        domain: str,
        scope: str,
        group_id: str,
        raw_text: str,
        normalized_key: str,
        fingerprint: str,
        source_table: str,
        source_id: str,
        message_id: int | None,
        user_id: str,
        features: dict[str, Any],
        meta: dict[str, Any],
        now: str,
    ) -> LearningNormalizerItem:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM learning_normalizer_items
               WHERE domain = ? AND source_table = ? AND source_id = ? AND raw_text = ?
               LIMIT 1""",
            (domain, source_table, source_id, raw_text),
        )
        existing = await cursor.fetchone()
        if existing:
            item = _row_to_item(existing)
            await db.execute(
                """UPDATE learning_normalizer_items
                   SET count = count + 1, last_seen_at = ?, features_json = ?, meta_json = ?
                   WHERE item_id = ?""",
                (
                    now,
                    json.dumps({**item.features, **features}, ensure_ascii=False),
                    json.dumps({**item.meta, **meta}, ensure_ascii=False),
                    item.item_id,
                ),
            )
            item_cursor = await db.execute("SELECT * FROM learning_normalizer_items WHERE item_id = ?", (item.item_id,))
            row = await item_cursor.fetchone()
            return _row_to_item(row)
        item_id = _new_id("lni")
        await db.execute(
            """INSERT INTO learning_normalizer_items
               (item_id, cluster_id, domain, scope, group_id, raw_text, normalized_key,
                fingerprint, source_table, source_id, message_id, user_id, count,
                first_seen_at, last_seen_at, features_json, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (
                item_id,
                cluster_id,
                domain,
                scope,
                group_id,
                raw_text,
                normalized_key,
                fingerprint,
                source_table,
                source_id,
                message_id,
                user_id,
                now,
                now,
                json.dumps(features, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        item_cursor = await db.execute("SELECT * FROM learning_normalizer_items WHERE item_id = ?", (item_id,))
        row = await item_cursor.fetchone()
        return _row_to_item(row)

    async def _refresh_cluster_counts(self, cluster_ids: list[str]) -> None:
        db = self._require_db()
        now = _now_iso()
        for cluster_id in {item for item in cluster_ids if item}:
            await db.execute(
                """UPDATE learning_normalizer_clusters
                   SET item_count = (
                           SELECT COALESCE(SUM(count), 0)
                             FROM learning_normalizer_items
                            WHERE cluster_id = ?
                       ),
                       updated_at = ?
                   WHERE cluster_id = ?""",
                (cluster_id, now, cluster_id),
            )

    async def _get_item(self, item_id: str) -> LearningNormalizerItem | None:
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM learning_normalizer_items WHERE item_id = ?", (item_id,))
        row = await cursor.fetchone()
        return _row_to_item(row) if row else None

    async def _get_revision(self, revision_id: str) -> LearningNormalizerRevision | None:
        db = self._require_db()
        row = await (
            await db.execute("SELECT * FROM learning_normalizer_revisions WHERE revision_id = ?", (revision_id,))
        ).fetchone()
        return _row_to_revision(row) if row else None

    async def _record_revision(
        self,
        cluster_id: str,
        *,
        item_id: str = "",
        action: str,
        actor: str = "system",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason: str = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        db = self._require_db()
        revision_id = _new_id("lnr")
        await db.execute(
            """INSERT INTO learning_normalizer_revisions
               (revision_id, cluster_id, item_id, action, actor, before_json, after_json, reason, created_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                revision_id,
                cluster_id,
                item_id,
                action,
                actor,
                json.dumps(before or {}, ensure_ascii=False),
                json.dumps(after or {}, ensure_ascii=False),
                reason,
                _now_iso(),
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        await db.commit()
        return revision_id

    async def set_cross_group_visibility(
        self,
        cluster_id: str,
        *,
        visible: bool,
        actor: str,
        reason: str = "",
        enabled_for_groups: list[str] | None = None,
    ) -> bool:
        """Toggle cross-group visibility on a normalizer cluster."""
        db = self._require_db()
        cluster = await self.get_cluster(cluster_id)
        if cluster is None:
            return False
        now = _now_iso()
        enabled_by = actor if visible else ""
        enabled_at = now if visible else ""
        groups_payload = (
            [str(g).strip() for g in (enabled_for_groups or []) if str(g).strip()]
            if visible
            else []
        )
        reason_payload = reason if visible else ""
        cursor = await db.execute(
            """UPDATE learning_normalizer_clusters
               SET cross_group_visible = ?, cross_group_enabled_by = ?,
                   cross_group_enabled_at = ?, cross_group_enabled_for_groups = ?,
                   cross_group_enabled_reason = ?, updated_at = ?
               WHERE cluster_id = ?""",
            (
                int(visible),
                enabled_by,
                enabled_at,
                json.dumps(groups_payload, ensure_ascii=False),
                reason_payload,
                now,
                cluster_id,
            ),
        )
        await db.commit()
        if cursor.rowcount <= 0:
            return False
        action = "cross_group_enable" if visible else "cross_group_disable"
        await self._record_revision(
            cluster_id,
            action=action,
            actor=actor,
            before={
                "cross_group_visible": cluster.cross_group_visible,
                "cross_group_enabled_for_groups": list(cluster.cross_group_enabled_for_groups),
                "cross_group_enabled_reason": cluster.cross_group_enabled_reason,
            },
            after={
                "cross_group_visible": visible,
                "cross_group_enabled_for_groups": groups_payload,
                "cross_group_enabled_reason": reason_payload,
            },
            reason=reason or f"{action} by {actor}",
        )
        return True

    @staticmethod
    def cluster_to_dict(cluster: LearningNormalizerCluster | None) -> dict[str, Any]:
        if cluster is None:
            return {}
        return {
            "cluster_id": cluster.cluster_id,
            "domain": cluster.domain,
            "scope": cluster.scope,
            "group_id": cluster.group_id,
            "canonical_text": cluster.canonical_text,
            "canonical_key": cluster.canonical_key,
            "fingerprint": cluster.fingerprint,
            "status": cluster.status,
            "confidence": cluster.confidence,
            "item_count": cluster.item_count,
            "created_at": cluster.created_at,
            "updated_at": cluster.updated_at,
            "locked_at": cluster.locked_at,
            "meta": cluster.meta,
        }

    @staticmethod
    def item_to_dict(item: LearningNormalizerItem | None) -> dict[str, Any]:
        if item is None:
            return {}
        return {
            "item_id": item.item_id,
            "cluster_id": item.cluster_id,
            "domain": item.domain,
            "scope": item.scope,
            "group_id": item.group_id,
            "raw_text": item.raw_text,
            "normalized_key": item.normalized_key,
            "fingerprint": item.fingerprint,
            "source_table": item.source_table,
            "source_id": item.source_id,
            "message_id": item.message_id,
            "user_id": item.user_id,
            "count": item.count,
            "first_seen_at": item.first_seen_at,
            "last_seen_at": item.last_seen_at,
            "features": item.features,
            "meta": item.meta,
        }

    @staticmethod
    def revision_to_dict(revision: LearningNormalizerRevision | None) -> dict[str, Any]:
        if revision is None:
            return {}
        return {
            "revision_id": revision.revision_id,
            "cluster_id": revision.cluster_id,
            "item_id": revision.item_id,
            "action": revision.action,
            "actor": revision.actor,
            "before": revision.before,
            "after": revision.after,
            "reason": revision.reason,
            "created_at": revision.created_at,
            "meta": revision.meta,
        }


_DEFAULT_STORE: LearningNormalizerStore | None = None


async def get_default_store() -> LearningNormalizerStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = LearningNormalizerStore()
    if not _DEFAULT_STORE.initialized:
        await _DEFAULT_STORE.init()
    return _DEFAULT_STORE


def _allows_fuzzy(left_key: str, right_key: str, method: str, score: float) -> bool:
    if not left_key or not right_key:
        return False
    if method in {"exact", "fingerprint"}:
        return True
    if min(len(left_key), len(right_key)) <= 3:
        return False
    if left_key.isascii() and right_key.isascii() and min(len(left_key), len(right_key)) <= 4:
        return False
    return score >= _DEFAULT_FUZZY_THRESHOLD


def _row_to_cluster(row: aiosqlite.Row) -> LearningNormalizerCluster:
    keys = row.keys()
    if "cross_group_enabled_for_groups" in keys:
        raw_groups = row["cross_group_enabled_for_groups"] or "[]"
        try:
            parsed_groups = json.loads(raw_groups) if isinstance(raw_groups, str) else []
        except (json.JSONDecodeError, TypeError):
            parsed_groups = []
        enabled_for_groups = [
            str(item)
            for item in parsed_groups
            if isinstance(parsed_groups, list) and str(item).strip()
        ]
    else:
        enabled_for_groups = []
    return LearningNormalizerCluster(
        cluster_id=row["cluster_id"],
        domain=row["domain"],
        scope=row["scope"],
        group_id=row["group_id"],
        canonical_text=row["canonical_text"],
        canonical_key=row["canonical_key"],
        fingerprint=row["fingerprint"],
        status=row["status"],
        confidence=float(row["confidence"] or 0.0),
        item_count=int(row["item_count"] or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        locked_at=row["locked_at"],
        meta=_json_dict(row["meta_json"]),
        cross_group_visible=bool(row["cross_group_visible"]) if "cross_group_visible" in keys else False,
        cross_group_enabled_by=row["cross_group_enabled_by"] if "cross_group_enabled_by" in keys else "",
        cross_group_enabled_at=row["cross_group_enabled_at"] if "cross_group_enabled_at" in keys else "",
        cross_group_enabled_for_groups=enabled_for_groups,
        cross_group_enabled_reason=row["cross_group_enabled_reason"] if "cross_group_enabled_reason" in keys else "",
    )


def _row_to_item(row: aiosqlite.Row) -> LearningNormalizerItem:
    return LearningNormalizerItem(
        item_id=row["item_id"],
        cluster_id=row["cluster_id"],
        domain=row["domain"],
        scope=row["scope"],
        group_id=row["group_id"],
        raw_text=row["raw_text"],
        normalized_key=row["normalized_key"],
        fingerprint=row["fingerprint"],
        source_table=row["source_table"],
        source_id=row["source_id"],
        message_id=row["message_id"],
        user_id=row["user_id"],
        count=int(row["count"] or 0),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        features=_json_dict(row["features_json"]),
        meta=_json_dict(row["meta_json"]),
    )


def _row_to_revision(row: aiosqlite.Row) -> LearningNormalizerRevision:
    return LearningNormalizerRevision(
        revision_id=row["revision_id"],
        cluster_id=row["cluster_id"],
        item_id=row["item_id"],
        action=row["action"],
        actor=row["actor"],
        before=_json_dict(row["before_json"]),
        after=_json_dict(row["after_json"]),
        reason=row["reason"],
        created_at=row["created_at"],
        meta=_json_dict(row["meta_json"]),
    )


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
