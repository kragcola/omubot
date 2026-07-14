"""Declarative inventory for Omubot's SQLite databases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath


class ConnectionProfile(StrEnum):
    WAL_NORMAL = "wal_normal"
    DELETE_FULL = "delete_full"
    SHORT_LIVED = "short_lived"


class BackupProfile(StrEnum):
    CRITICAL_DAILY = "critical_daily"
    DAILY = "daily"
    MIGRATION_ONLY = "migration_only"
    REBUILDABLE = "rebuildable"
    NONE = "none"


class RetentionProfile(StrEnum):
    FOREVER = "forever"
    OWNER_MANAGED = "owner_managed"
    REBUILDABLE = "rebuildable"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class DatabaseSpec:
    id: str
    path: str
    owner: str
    clients: tuple[str, ...]
    target_user_version: int
    connection_profile: ConnectionProfile
    backup_profile: BackupProfile
    retention_profile: RetentionProfile
    critical: bool = False
    optional: bool = False
    sensitive: bool = False
    rebuildable: bool = False


class DatabaseCatalog:
    def __init__(self, specs: tuple[DatabaseSpec, ...]) -> None:
        self._specs = tuple(specs)

    def all(self) -> tuple[DatabaseSpec, ...]:
        return self._specs

    def get(self, db_id: str) -> DatabaseSpec:
        for spec in self._specs:
            if spec.id == db_id:
                return spec
        raise KeyError(db_id)

    def resolve(self, repo_root: str | Path, db_id: str) -> Path:
        spec = self.get(db_id)
        issue = _path_issue(spec.path)
        if issue is not None:
            raise ValueError(issue)
        storage_root = (Path(repo_root).resolve() / "storage").resolve()
        resolved = (Path(repo_root).resolve() / spec.path).resolve()
        if not resolved.is_relative_to(storage_root):
            raise ValueError(f"database path escapes storage root: {spec.path}")
        return resolved

    def validate(self) -> tuple[str, ...]:
        issues: list[str] = []
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        for spec in self._specs:
            if not spec.id.strip():
                issues.append("database id must not be empty")
            elif spec.id in seen_ids:
                issues.append(f"duplicate database id: {spec.id}")
            seen_ids.add(spec.id)

            if spec.path in seen_paths:
                issues.append(f"duplicate database path: {spec.path}")
            seen_paths.add(spec.path)
            path_issue = _path_issue(spec.path)
            if path_issue is not None:
                issues.append(path_issue)

            if not spec.owner.strip():
                issues.append(f"database {spec.id} has no schema owner")
            if not spec.clients:
                issues.append(f"database {spec.id} has no clients")
            if spec.target_user_version <= 0:
                issues.append(f"database {spec.id} target_user_version must be positive")
            if not isinstance(spec.connection_profile, ConnectionProfile):
                issues.append(f"database {spec.id} has invalid connection profile")
            if not isinstance(spec.backup_profile, BackupProfile):
                issues.append(f"database {spec.id} has invalid backup profile")
            if not isinstance(spec.retention_profile, RetentionProfile):
                issues.append(f"database {spec.id} has invalid retention profile")
        return tuple(issues)


def _path_issue(raw_path: str) -> str | None:
    path = PurePosixPath(raw_path)
    if path.is_absolute():
        return f"database path must be relative: {raw_path}"
    if not path.parts or path.parts[0] != "storage":
        return f"database path must be under storage/: {raw_path}"
    if ".." in path.parts:
        return f"database path must not contain parent traversal: {raw_path}"
    if path.suffix != ".db":
        return f"database path must end in .db: {raw_path}"
    return None


def _spec(
    db_id: str,
    path: str,
    owner: str,
    *,
    clients: tuple[str, ...] | None = None,
    connection: ConnectionProfile = ConnectionProfile.WAL_NORMAL,
    backup: BackupProfile = BackupProfile.DAILY,
    retention: RetentionProfile = RetentionProfile.FOREVER,
    critical: bool = False,
    optional: bool = False,
    sensitive: bool = False,
    rebuildable: bool = False,
) -> DatabaseSpec:
    return DatabaseSpec(
        id=db_id,
        path=path,
        owner=owner,
        clients=clients or (owner,),
        target_user_version=1,
        connection_profile=connection,
        backup_profile=backup,
        retention_profile=retention,
        critical=critical,
        optional=optional,
        sensitive=sensitive,
        rebuildable=rebuildable,
    )


DEFAULT_DATABASE_CATALOG = DatabaseCatalog(
    (
        _spec("affection_stage", "storage/affection_stage.db", "services.persona.affection_classifier", optional=True),
        _spec(
            "block_trace",
            "storage/block_trace.db",
            "services.block_trace.store",
            retention=RetentionProfile.OWNER_MANAGED,
        ),
        _spec(
            "character_recognition",
            "storage/character_recognition.db",
            "services.media.character_registry_db",
            clients=("services.media.character_registry_db", "services.media.recognition_cache"),
        ),
        _spec("consolidator_candidates", "storage/consolidator_candidates.db", "services.memory_consolidator.store"),
        _spec(
            "consolidator_normalizer",
            "storage/consolidator_normalizer.db",
            "services.memory_consolidator.normalizer",
        ),
        _spec("episodic", "storage/episodic.db", "services.episodic.store", critical=True),
        _spec(
            "hawkes_cache",
            "storage/hawkes_cache.db",
            "services.scheduler_hawkes.cache",
            backup=BackupProfile.REBUILDABLE,
            retention=RetentionProfile.REBUILDABLE,
            optional=True,
            rebuildable=True,
        ),
        _spec("knowledge_graph", "storage/knowledge_graph.db", "services.knowledge_graph.store"),
        _spec(
            "knowledge_index",
            "storage/knowledge_index.db",
            "services.knowledge.store",
            backup=BackupProfile.REBUILDABLE,
            retention=RetentionProfile.REBUILDABLE,
            rebuildable=True,
        ),
        _spec(
            "learning_normalizer",
            "storage/learning_normalizer.db",
            "services.learning_normalizer.store",
            connection=ConnectionProfile.DELETE_FULL,
        ),
        _spec(
            "living_persona_m1_metrics",
            "storage/living_persona/m1_metrics.db",
            "services.dialogue_climate.m1_metrics",
            optional=True,
        ),
        _spec(
            "living_persona_m2_climate",
            "storage/living_persona/m2_climate.db",
            "services.dialogue_climate.m2_metrics",
            optional=True,
        ),
        _spec("memory_cards", "storage/memory_cards.db", "services.memory.card_store", critical=True),
        _spec(
            "messages",
            "storage/messages.db",
            "services.memory.message_log",
            clients=("services.memory.message_log", "services.conversation_archive.store"),
            connection=ConnectionProfile.DELETE_FULL,
            retention=RetentionProfile.OWNER_MANAGED,
            critical=True,
        ),
        _spec(
            "research_events",
            "storage/research_events.db",
            "services.group.research_event_store",
            backup=BackupProfile.MIGRATION_ONLY,
            retention=RetentionProfile.DISABLED,
            optional=True,
            sensitive=True,
        ),
        _spec(
            "scheduler_replay",
            "storage/scheduler_replay.db",
            "services.scheduler_replay.replay",
            connection=ConnectionProfile.SHORT_LIVED,
            backup=BackupProfile.REBUILDABLE,
            retention=RetentionProfile.REBUILDABLE,
            optional=True,
            rebuildable=True,
        ),
        _spec(
            "slang",
            "storage/slang.db",
            "services.slang.store",
            connection=ConnectionProfile.DELETE_FULL,
            critical=True,
        ),
        _spec("stickers", "storage/stickers/stickers.db", "services.media.sticker_store", optional=True),
        _spec("style", "storage/style.db", "services.style.store", connection=ConnectionProfile.DELETE_FULL),
        _spec(
            "topic_corpus",
            "storage/topic_corpus.db",
            "services.group.corpus_capture",
            optional=True,
            sensitive=True,
        ),
        _spec(
            "usage",
            "storage/usage.db",
            "services.llm.usage",
            clients=("services.llm.usage", "services.humanization.health_guard"),
        ),
    )
)

_DEFAULT_ISSUES = DEFAULT_DATABASE_CATALOG.validate()
if _DEFAULT_ISSUES:
    raise RuntimeError(f"invalid default database catalog: {_DEFAULT_ISSUES}")
