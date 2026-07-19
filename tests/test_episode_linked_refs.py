"""RED behavior tests for EpisodeStore dual-read linked_memory_ids normalization.

Expected production surface (no new store methods):
- EpisodeStore.create_episode(..., linked_memory_ids=...)
- Episode.linked_memory_ids  — normalized storage strings (preserve_legacy=True)
- Episode.linked_memory_refs — tuple[LinkedMemoryRef, ...] for every valid stored entry

Normalization uses services.memory.linked_refs.normalize_linked_refs(..., preserve_legacy=True):
typed canonical, first-seen dedup, bare legacy strings preserved, invalid dropped.

Corrupt SQLite JSON in the existing linked_memory_ids TEXT column must never raise
on get_episode. Schema column remains linked_memory_ids (no new column).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from services.episodic import Episode, EpisodeStore
from services.memory.linked_refs import LinkedMemoryRef, normalize_linked_refs

# ---------------------------------------------------------------------------
# Fixtures — real temp EpisodeStore
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "episode_linked_refs.db")


@pytest.fixture
async def store(db_path: str):
    s = EpisodeStore(db_path)
    await s.init()
    try:
        yield s
    finally:
        await s.close()


async def _reopen(db_path: str) -> EpisodeStore:
    s = EpisodeStore(db_path)
    await s.init()
    return s


def _sqlite_update_linked_memory_ids(db_path: str, episode_id: str, raw_json: str) -> None:
    """Direct write to the existing TEXT column (corrupt / mixed payloads)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE episodes SET linked_memory_ids = ? WHERE episode_id = ?",
            (raw_json, episode_id),
        )
        conn.commit()
    finally:
        conn.close()


def _sqlite_column_names(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("PRAGMA table_info(episodes)").fetchall()
        return {row[1] for row in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. create_episode normalizes mixed inputs (preserve_legacy=True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_episode_normalizes_mixed_linked_memory_ids(store: EpisodeStore) -> None:
    """Mixed compact strings, {type,id} objects, duplicates, invalid, bare legacy."""
    mixed = [
        "card:card_1",
        {"type": "card", "id": "card_1"},  # duplicate of first (canonical)
        "fact:f1",
        "",  # invalid
        None,  # invalid
        "unknown:x",  # invalid kind
        "entity:user:qq:00123",  # canonicalize zeros
        "message_pk:0007",
        "mem_1",  # bare legacy preserved
        "legacy:mem_1",  # same identity as bare mem_1 → deduped
        "card:card_1",  # again
        "episode:ep1",
        {"type": "fact", "id": "f2"},
        {"kind": "card", "id": "nope"},  # wrong key → invalid
        123,  # invalid
        "   ",  # invalid
    ]

    expected = list(normalize_linked_refs(mixed, preserve_legacy=True))
    assert expected == [
        "card:card_1",
        "fact:f1",
        "entity:user:qq:123",
        "message_pk:7",
        "mem_1",
        "episode:ep1",
        "fact:f2",
    ]

    ep = await store.create_episode(
        situation="mixed linked refs",
        group_id="g1",
        linked_memory_ids=mixed,  # type: ignore[arg-type]
    )

    assert isinstance(ep, Episode)
    assert ep.linked_memory_ids == expected
    # list form (existing API) — not a bare tuple unless production chooses that;
    # values must match normalized storage strings in first-seen order.
    assert list(ep.linked_memory_ids) == expected


# ---------------------------------------------------------------------------
# 2. linked_memory_refs property — typed objects, legacy canonicalized
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_episode_linked_memory_refs_property(store: EpisodeStore) -> None:
    ep = await store.create_episode(
        situation="refs property",
        group_id="g1",
        linked_memory_ids=[
            "card:card_1",
            {"type": "entity", "id": "user:qq:00123"},
            "mem_1",
            "message_pk:0007",
            "fact:f1",
        ],
    )

    refs = ep.linked_memory_refs
    assert isinstance(refs, tuple)
    assert all(isinstance(r, LinkedMemoryRef) for r in refs)
    assert len(refs) == len(ep.linked_memory_ids)

    assert refs[0].kind == "card"
    assert refs[0].target_id == "card_1"
    assert refs[0].canonical == "card:card_1"

    assert refs[1].kind == "entity"
    assert refs[1].target_id == "user:qq:123"
    assert refs[1].canonical == "entity:user:qq:123"

    # Bare storage is mem_1, but property objects use legacy canonicalization.
    assert ep.linked_memory_ids[2] == "mem_1"
    assert refs[2].kind == "legacy"
    assert refs[2].target_id == "mem_1"
    assert refs[2].canonical == "legacy:mem_1"

    assert refs[3].kind == "message_pk"
    assert refs[3].canonical == "message_pk:7"

    assert refs[4].kind == "fact"
    assert refs[4].canonical == "fact:f1"


# ---------------------------------------------------------------------------
# 3. Reopen persistence preserves normalized storage + typed property
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reopen_preserves_normalized_storage_and_refs(
    store: EpisodeStore, db_path: str
) -> None:
    mixed = [
        "card:card_1",
        {"type": "card", "id": "card_1"},
        "mem_1",
        "entity:user:qq:00123",
        "unknown:x",
        "message_pk:0007",
    ]
    expected_ids = list(normalize_linked_refs(mixed, preserve_legacy=True))

    created = await store.create_episode(
        situation="persist linked refs",
        group_id="g1",
        linked_memory_ids=mixed,  # type: ignore[arg-type]
    )
    episode_id = created.episode_id
    assert created.linked_memory_ids == expected_ids

    await store.close()

    reopened = await _reopen(db_path)
    try:
        fetched = await reopened.get_episode(episode_id)
        assert fetched is not None
        assert fetched.linked_memory_ids == expected_ids
        assert list(fetched.linked_memory_ids) == [
            "card:card_1",
            "mem_1",
            "entity:user:qq:123",
            "message_pk:7",
        ]

        refs = fetched.linked_memory_refs
        assert isinstance(refs, tuple)
        assert [r.canonical for r in refs] == [
            "card:card_1",
            "legacy:mem_1",
            "entity:user:qq:123",
            "message_pk:7",
        ]
        assert refs[1].kind == "legacy"
        assert refs[1].target_id == "mem_1"
    finally:
        await reopened.close()


# ---------------------------------------------------------------------------
# 4. Legacy call path — bare mem strings stored exactly as given
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_linked_memory_ids_list_preserved_exactly(store: EpisodeStore) -> None:
    """Existing callers pass linked_memory_ids=[mem_1, mem_2] and must keep those strings."""
    ep = await store.create_episode(
        situation="legacy call",
        group_id="g1",
        linked_memory_ids=["mem_1", "mem_2"],
    )
    assert ep.linked_memory_ids == ["mem_1", "mem_2"]

    fetched = await store.get_episode(ep.episode_id)
    assert fetched is not None
    assert fetched.linked_memory_ids == ["mem_1", "mem_2"]

    refs = fetched.linked_memory_refs
    assert isinstance(refs, tuple)
    assert len(refs) == 2
    assert refs[0].kind == "legacy" and refs[0].canonical == "legacy:mem_1"
    assert refs[1].kind == "legacy" and refs[1].canonical == "legacy:mem_2"


# ---------------------------------------------------------------------------
# 5. Corrupt DB JSON — get_episode never raises; safe fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_linked_memory_ids_json_never_raises(
    store: EpisodeStore, db_path: str
) -> None:
    ep = await store.create_episode(
        situation="corrupt seed",
        group_id="g1",
        linked_memory_ids=["mem_1"],
    )
    episode_id = ep.episode_id
    await store.close()

    # invalid JSON
    _sqlite_update_linked_memory_ids(db_path, episode_id, "NOT_JSON{{{")
    reopened = await _reopen(db_path)
    try:
        fetched = await reopened.get_episode(episode_id)
        assert fetched is not None
        assert fetched.linked_memory_ids == []
        assert fetched.linked_memory_refs == ()
    finally:
        await reopened.close()

    # JSON object instead of list
    _sqlite_update_linked_memory_ids(
        db_path, episode_id, json.dumps({"type": "card", "id": "c1"})
    )
    reopened = await _reopen(db_path)
    try:
        fetched = await reopened.get_episode(episode_id)
        assert fetched is not None
        assert fetched.linked_memory_ids == []
        assert fetched.linked_memory_refs == ()
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_mixed_corrupt_list_keeps_only_valid_normalized_strings(
    store: EpisodeStore, db_path: str
) -> None:
    ep = await store.create_episode(
        situation="mixed corrupt list",
        group_id="g1",
        linked_memory_ids=["mem_seed"],
    )
    episode_id = ep.episode_id
    await store.close()

    # list with ints / dicts / empty / bad typed values
    payload = [
        1,
        2.5,
        True,
        None,
        "",
        "   ",
        {},
        {"kind": "card", "id": "wrong_key"},
        {"type": "card"},  # missing id
        {"type": "nope", "id": "x"},
        "unknown:x",
        "card:",
        "card:card_ok",
        {"type": "fact", "id": "f9"},
        "mem_legacy",
        "entity:user:qq:0009",
        "message_pk:0",  # invalid pk
        "message_pk:12",
    ]
    _sqlite_update_linked_memory_ids(db_path, episode_id, json.dumps(payload))

    expected = list(normalize_linked_refs(payload, preserve_legacy=True))
    assert expected == [
        "card:card_ok",
        "fact:f9",
        "mem_legacy",
        "entity:user:qq:9",
        "message_pk:12",
    ]

    reopened = await _reopen(db_path)
    try:
        fetched = await reopened.get_episode(episode_id)
        assert fetched is not None
        assert fetched.linked_memory_ids == expected
        assert [r.canonical for r in fetched.linked_memory_refs] == [
            "card:card_ok",
            "fact:f9",
            "legacy:mem_legacy",
            "entity:user:qq:9",
            "message_pk:12",
        ]
    finally:
        await reopened.close()


# ---------------------------------------------------------------------------
# 6. create_episode does not mutate caller-owned list / dict objects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_episode_does_not_mutate_caller_owned_inputs(store: EpisodeStore) -> None:
    obj = {"type": "card", "id": "card_1"}
    caller_list: list[object] = [
        "mem_1",
        obj,
        "fact:f1",
        "unknown:x",
    ]
    list_snapshot = list(caller_list)
    obj_snapshot = dict(obj)

    ep = await store.create_episode(
        situation="no mutate caller",
        group_id="g1",
        linked_memory_ids=caller_list,  # type: ignore[arg-type]
    )

    assert caller_list == list_snapshot
    assert obj == obj_snapshot
    # Identity preserved for list and nested dict
    assert caller_list[1] is obj

    assert ep.linked_memory_ids == ["mem_1", "card:card_1", "fact:f1"]
    # Returned list must not be the same object as the caller list
    assert ep.linked_memory_ids is not caller_list


# ---------------------------------------------------------------------------
# 7. Schema column unchanged; lifecycle/state defaults untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_new_linked_refs_column_and_lifecycle_untouched(
    store: EpisodeStore, db_path: str
) -> None:
    cols = _sqlite_column_names(db_path)
    assert "linked_memory_ids" in cols
    assert "linked_memory_refs" not in cols

    ep = await store.create_episode(
        situation="lifecycle intact",
        group_id="g1",
        confidence=0.8,
        linked_memory_ids=["mem_1", "card:c1"],
    )
    # Defaults from existing EpisodeStore contract remain
    assert ep.episode_state == "dry_run"
    assert ep.scope == "group"
    assert ep.source == "consolidator"
    assert ep.confidence == 0.8
    assert ep.group_id == "g1"
    assert ep.situation == "lifecycle intact"

    # Transition still works (state machine untouched)
    await store.transition_state(ep.episode_id, new_state="candidate", actor="system")
    fetched = await store.get_episode(ep.episode_id)
    assert fetched is not None
    assert fetched.episode_state == "candidate"
    # Normalization still present after transition
    assert fetched.linked_memory_ids == ["mem_1", "card:c1"]
    assert fetched.linked_memory_refs[0].canonical == "legacy:mem_1"
    assert fetched.linked_memory_refs[1].canonical == "card:c1"
