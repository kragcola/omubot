"""Behavior contract for Omubot's declarative SQLite database catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

EXPECTED_DATABASES = {
    "affection_stage": "storage/affection_stage.db",
    "block_trace": "storage/block_trace.db",
    "character_recognition": "storage/character_recognition.db",
    "consolidator_candidates": "storage/consolidator_candidates.db",
    "consolidator_normalizer": "storage/consolidator_normalizer.db",
    "episodic": "storage/episodic.db",
    "hawkes_cache": "storage/hawkes_cache.db",
    "knowledge_graph": "storage/knowledge_graph.db",
    "knowledge_index": "storage/knowledge_index.db",
    "learning_normalizer": "storage/learning_normalizer.db",
    "living_persona_climate_baselines": "storage/living_persona/climate_baselines.db",
    "living_persona_m1_metrics": "storage/living_persona/m1_metrics.db",
    "living_persona_m2_climate": "storage/living_persona/m2_climate.db",
    "memory_cards": "storage/memory_cards.db",
    "messages": "storage/messages.db",
    "research_events": "storage/research_events.db",
    "research_topic_assignments": "storage/research_topic_assignments.db",
    "scheduler_replay": "storage/scheduler_replay.db",
    "slang": "storage/slang.db",
    "stickers": "storage/stickers/stickers.db",
    "style": "storage/style.db",
    "topic_corpus": "storage/topic_corpus.db",
    "usage": "storage/usage.db",
}


def _load_api() -> tuple[Any, Any, Any, Any, Any, Any]:
    try:
        from services.storage.catalog import (
            DEFAULT_DATABASE_CATALOG,
            BackupProfile,
            ConnectionProfile,
            DatabaseCatalog,
            DatabaseSpec,
            RetentionProfile,
        )
    except ImportError as exc:
        pytest.fail(
            "missing expected services.storage.catalog public API",
            pytrace=False,
        )
        raise AssertionError("unreachable") from exc
    return (
        DatabaseSpec,
        DatabaseCatalog,
        ConnectionProfile,
        BackupProfile,
        RetentionProfile,
        DEFAULT_DATABASE_CATALOG,
    )


def _profiles() -> tuple[Any, Any, Any]:
    _, _, connection_profile, backup_profile, retention_profile, _ = _load_api()
    return (
        next(iter(connection_profile)),
        next(iter(backup_profile)),
        next(iter(retention_profile)),
    )


def _spec(
    *,
    db_id: str = "sample",
    path: str = "storage/sample.db",
    owner: str = "tests.sample",
    clients: tuple[str, ...] = ("tests.sample",),
    connection_profile: Any = None,
    backup_profile: Any = None,
    retention_profile: Any = None,
) -> Any:
    database_spec, _, _, _, _, _ = _load_api()
    default_connection, default_backup, default_retention = _profiles()
    return database_spec(
        id=db_id,
        path=path,
        owner=owner,
        clients=clients,
        target_user_version=1,
        connection_profile=connection_profile or default_connection,
        backup_profile=backup_profile or default_backup,
        retention_profile=retention_profile or default_retention,
    )


def test_default_catalog_covers_exact_source_database_ids_and_paths() -> None:
    _, _, _, _, _, catalog = _load_api()

    actual = {spec.id: spec.path for spec in catalog.all()}

    assert actual == EXPECTED_DATABASES


def test_default_catalog_is_valid_and_has_unique_ids_and_paths() -> None:
    _, _, _, _, _, catalog = _load_api()
    specs = catalog.all()

    assert catalog.validate() == ()
    assert len({spec.id for spec in specs}) == len(specs)
    assert len({spec.path for spec in specs}) == len(specs)


def test_shared_database_can_declare_one_owner_and_multiple_clients() -> None:
    _, database_catalog, _, _, _, default_catalog = _load_api()
    shared = _spec(
        db_id="shared",
        path="storage/shared.db",
        owner="services.shared_schema",
        clients=("services.writer", "services.reader"),
    )

    catalog = database_catalog((shared,))

    assert catalog.get("shared").owner == "services.shared_schema"
    assert catalog.get("shared").clients == ("services.writer", "services.reader")
    assert len(default_catalog.get("messages").clients) >= 2
    assert len(default_catalog.get("character_recognition").clients) >= 2
    assert default_catalog.get("research_events").clients == (
        "services.group.research_event_store",
        "services.group.topic_assignment_runner",
    )


def test_profiles_are_enum_values_and_raw_profile_strings_are_invalid() -> None:
    _, database_catalog, connection_profile, backup_profile, retention_profile, catalog = _load_api()

    for spec in catalog.all():
        assert isinstance(spec.connection_profile, connection_profile)
        assert isinstance(spec.backup_profile, backup_profile)
        assert isinstance(spec.retention_profile, retention_profile)

    invalid_specs = (
        _spec(db_id="bad_connection", path="storage/bad_connection.db", connection_profile="wal"),
        _spec(db_id="bad_backup", path="storage/bad_backup.db", backup_profile="daily"),
        _spec(db_id="bad_retention", path="storage/bad_retention.db", retention_profile="forever"),
    )
    for invalid in invalid_specs:
        assert database_catalog((invalid,)).validate()


@pytest.mark.parametrize(
    "invalid_path",
    (
        "/tmp/absolute.db",
        "../escape.db",
        "storage/../escape.db",
        "other/not-storage.db",
        "storage/wrong.sqlite",
        "storage/missing-extension",
    ),
)
def test_invalid_database_paths_are_reported_and_cannot_resolve(
    tmp_path: Path,
    invalid_path: str,
) -> None:
    _, database_catalog, _, _, _, _ = _load_api()
    catalog = database_catalog((_spec(path=invalid_path),))

    assert catalog.validate()
    with pytest.raises(ValueError):
        catalog.resolve(tmp_path, "sample")


def test_validate_reports_duplicate_ids_and_paths() -> None:
    _, database_catalog, _, _, _, _ = _load_api()
    specs = (
        _spec(db_id="duplicate", path="storage/one.db"),
        _spec(db_id="duplicate", path="storage/two.db"),
        _spec(db_id="third", path="storage/one.db"),
    )

    issues = database_catalog(specs).validate()

    assert len(issues) >= 2


def test_get_unknown_database_id_raises_key_error() -> None:
    _, _, _, _, _, catalog = _load_api()

    with pytest.raises(KeyError):
        catalog.get("not_registered")


def test_resolve_returns_path_under_repository_storage(tmp_path: Path) -> None:
    _, database_catalog, _, _, _, _ = _load_api()
    catalog = database_catalog((_spec(),))

    resolved = catalog.resolve(tmp_path, "sample")

    assert resolved == (tmp_path / "storage" / "sample.db").resolve()
    assert resolved.is_relative_to((tmp_path / "storage").resolve())
