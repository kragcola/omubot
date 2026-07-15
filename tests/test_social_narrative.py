"""RED contracts for evidence-backed factual shared experiences."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from services.memory.card_store import CardStore, NewCard


def _social_module() -> Any:
    try:
        module = importlib.import_module("services.social_narrative")
    except ModuleNotFoundError as exc:
        if exc.name != "services.social_narrative":
            raise
        module = None
    assert module is not None, (
        "services.social_narrative must provide the factual shared-experience store"
    )
    return module


def _store_type() -> type[Any]:
    store_type = getattr(_social_module(), "SocialNarrativeStore", None)
    assert inspect.isclass(store_type), (
        "services.social_narrative must expose SocialNarrativeStore"
    )
    return store_type


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _open_store(db_path: Path) -> Any:
    store = _store_type()(db_path)
    init = getattr(store, "init", None) or getattr(store, "startup", None)
    assert callable(init), "SocialNarrativeStore must provide init() or startup()"
    await _maybe_await(init())
    return store


async def _close_store(store: Any) -> None:
    close = getattr(store, "close", None) or getattr(store, "shutdown", None)
    if callable(close):
        await _maybe_await(close())


async def _record(store: Any, **payload: Any) -> Any:
    method = getattr(store, "record_shared_experience", None) or getattr(store, "record", None)
    assert callable(method), (
        "SocialNarrativeStore must record evidence-backed shared experiences"
    )
    return await _maybe_await(method(**payload))


async def _recall(store: Any, *, group_id: str, user_id: str) -> list[Any]:
    method = (
        getattr(store, "recall", None)
        or getattr(store, "list_shared_experiences", None)
        or getattr(store, "get_for_context", None)
    )
    assert callable(method), (
        "SocialNarrativeStore must recall by the exact (group_id, user_id) scope"
    )
    result = await _maybe_await(method(group_id=group_id, user_id=user_id, limit=20))
    if isinstance(result, tuple):
        result = result[0]
    assert isinstance(result, list), "social narrative recall must return a list"
    return result


async def _projection_count(store: Any, *, group_id: str, user_id: str) -> int:
    method = getattr(store, "projection_count", None)
    assert callable(method), (
        "SocialNarrativeStore must expose projection_count for idempotency verification"
    )
    return int(await _maybe_await(method(group_id=group_id, user_id=user_id)))


def _value(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _payload(
    *,
    group_id: str | None = "200",
    user_id: str = "100",
    evidence_message_id: str | None = "7001",
    user_text: str = "我们刚才一起把排练节奏理顺了",
    bot_reply: str = "嗯，先从最容易乱掉的那一段开始。",
) -> dict[str, Any]:
    return {
        "group_id": group_id,
        "user_id": user_id,
        "evidence_message_id": evidence_message_id,
        "evidence_time": "2026-07-15T20:30:00+08:00",
        "evidence_source": "reply_context",
        "user_text": user_text,
        "bot_reply": bot_reply,
        "entity_kind": "factual",
    }


async def test_social_narrative_init_preserves_existing_memory_cards(tmp_path: Path) -> None:
    db_path = tmp_path / "memory_cards.db"
    cards = CardStore(str(db_path))
    await cards.init()
    await cards.add_card(NewCard(
        category="fact",
        scope="group",
        scope_id="200",
        content="existing memory card sentinel",
    ))
    await cards.close()
    with sqlite3.connect(db_path) as conn:
        before_columns = conn.execute("PRAGMA table_info(memory_cards)").fetchall()
        before_count = conn.execute("SELECT COUNT(*) FROM memory_cards").fetchone()[0]

    store = await _open_store(db_path)
    await _close_store(store)

    with sqlite3.connect(db_path) as conn:
        after_columns = conn.execute("PRAGMA table_info(memory_cards)").fetchall()
        after_count = conn.execute("SELECT COUNT(*) FROM memory_cards").fetchone()[0]
    assert after_columns == before_columns
    assert after_count == before_count == 1


async def test_record_is_factual_complete_and_idempotent(tmp_path: Path) -> None:
    store = await _open_store(tmp_path / "memory_cards.db")
    try:
        created = await _record(store, **_payload())
        first_count = await _projection_count(store, group_id="200", user_id="100")
        await _record(store, **_payload())
        recalled = await _recall(store, group_id="200", user_id="100")
        second_count = await _projection_count(store, group_id="200", user_id="100")
    finally:
        await _close_store(store)

    record = created if created is not None else recalled[0]
    assert recalled and len(recalled) == 1
    assert first_count > 0
    assert second_count == first_count
    assert _value(record, "group_id") == "200"
    assert _value(record, "user_id") == "100"
    assert str(_value(record, "evidence_message_id")) == "7001"
    assert _value(record, "evidence_time") == "2026-07-15T20:30:00+08:00"
    assert _value(record, "evidence_source") == "reply_context"
    assert _value(record, "user_text") == "我们刚才一起把排练节奏理顺了"
    assert _value(record, "bot_reply") == "嗯，先从最容易乱掉的那一段开始。"
    assert _value(record, "entity_kind") == "factual"


async def test_recall_is_strictly_isolated_by_group_and_user(tmp_path: Path) -> None:
    store = await _open_store(tmp_path / "memory_cards.db")
    try:
        await _record(store, **_payload(user_text="g200-u100"))
        await _record(store, **_payload(
            user_id="101",
            evidence_message_id="7002",
            user_text="g200-u101",
        ))
        await _record(store, **_payload(
            group_id="201",
            evidence_message_id="7003",
            user_text="g201-u100",
        ))
        g200_u100 = await _recall(store, group_id="200", user_id="100")
        g200_u101 = await _recall(store, group_id="200", user_id="101")
        g201_u100 = await _recall(store, group_id="201", user_id="100")
    finally:
        await _close_store(store)

    assert [_value(row, "user_text") for row in g200_u100] == ["g200-u100"]
    assert [_value(row, "user_text") for row in g200_u101] == ["g200-u101"]
    assert [_value(row, "user_text") for row in g201_u100] == ["g201-u100"]


async def test_invalidate_evidence_removes_recall_and_repairs_projection(tmp_path: Path) -> None:
    store = await _open_store(tmp_path / "memory_cards.db")
    try:
        await _record(store, **_payload())
        invalidate = getattr(store, "invalidate_evidence", None)
        assert callable(invalidate), (
            "SocialNarrativeStore must invalidate one evidence message and its projections"
        )
        changed = await _maybe_await(invalidate(
            group_id="200",
            evidence_message_id="7001",
        ))
        recalled = await _recall(store, group_id="200", user_id="100")
        count = await _projection_count(store, group_id="200", user_id="100")
    finally:
        await _close_store(store)

    assert changed
    assert recalled == []
    assert count == 0


@pytest.mark.parametrize(
    "payload",
    [
        _payload(group_id=""),
        _payload(group_id=None),
        _payload(evidence_message_id=None),
    ],
    ids=["empty-group", "private", "missing-message-id"],
)
async def test_record_rejects_non_group_or_missing_evidence(
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    store = await _open_store(tmp_path / "memory_cards.db")
    try:
        with pytest.raises((TypeError, ValueError)):
            await _record(store, **payload)
    finally:
        await _close_store(store)


async def test_prompt_context_declares_factual_evidence_and_real_person_redline(
    tmp_path: Path,
) -> None:
    store = await _open_store(tmp_path / "memory_cards.db")
    try:
        await _record(store, **_payload())
        build = getattr(store, "build_prompt_context", None)
        assert callable(build), (
            "SocialNarrativeStore must build scoped factual prompt context"
        )
        context = str(await _maybe_await(build(group_id="200", user_id="100", limit=10)))
    finally:
        await _close_store(store)

    assert "factual" in context
    assert "7001" in context or "证据" in context
    assert "不得补写真人线下行为" in context


async def test_invalidating_latest_evidence_restores_previous_relationship_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory_cards.db"
    store = await _open_store(db_path)
    older = _payload(
        evidence_message_id="7001",
        user_text="第一次共同经历",
    )
    older["evidence_time"] = "2026-07-15T20:00:00+08:00"
    older["relationship"] = {
        "affection_score": 0.25,
        "affection_tier": "familiar",
        "climate_valence": 0.15,
        "climate_familiarity": 0.35,
    }
    latest = _payload(
        evidence_message_id="7002",
        user_text="第二次共同经历",
    )
    latest["evidence_time"] = "2026-07-15T21:00:00+08:00"
    latest["relationship"] = {
        "affection_score": 0.9,
        "affection_tier": "trusted",
        "climate_valence": 0.8,
        "climate_familiarity": 0.95,
    }
    try:
        await _record(store, **older)
        await _record(store, **latest)
        changed = await _maybe_await(store.invalidate_evidence(
            group_id="200",
            evidence_message_id="7002",
        ))
    finally:
        await _close_store(store)

    assert changed == 1
    relationship_columns = {
        "affection_score",
        "affection_tier",
        "climate_valence",
        "climate_familiarity",
    }
    with sqlite3.connect(db_path) as conn:
        experience_columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(social_narrative_experiences)"
            ).fetchall()
        }
        assert relationship_columns <= experience_columns, (
            "each factual experience must persist its own relationship snapshot"
        )
        experience_rows = conn.execute(
            """
            SELECT evidence_message_id, affection_score, affection_tier,
                   climate_valence, climate_familiarity
            FROM social_narrative_experiences
            ORDER BY evidence_message_id
            """
        ).fetchall()
        entity_row = conn.execute(
            """
            SELECT affection_score, affection_tier,
                   climate_valence, climate_familiarity
            FROM social_narrative_entities
            WHERE group_id = '200' AND user_id = '100'
            """
        ).fetchone()

    assert experience_rows == [
        ("7001", 0.25, "familiar", 0.15, 0.35),
        ("7002", 0.9, "trusted", 0.8, 0.95),
    ]
    assert entity_row == (0.25, "familiar", 0.15, 0.35), (
        "invalidating the latest evidence must rebuild relationship projection "
        "from the previous active experience"
    )


async def test_cancelled_record_rolls_back_before_same_evidence_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = await _open_store(tmp_path / "memory_cards.db")
    db = store._db
    assert db is not None
    original_execute = db.execute
    original_rollback = db.rollback
    entity_insert_entered = asyncio.Event()
    never_release = asyncio.Event()
    rollback_calls = 0

    async def execute_with_entity_gate(
        sql: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if "INSERT INTO social_narrative_entities" in sql:
            entity_insert_entered.set()
            await never_release.wait()
        return await original_execute(sql, *args, **kwargs)

    async def track_rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        await original_rollback()

    monkeypatch.setattr(db, "execute", execute_with_entity_gate)
    monkeypatch.setattr(db, "rollback", track_rollback)
    record_task = asyncio.create_task(_record(store, **_payload()))
    try:
        await asyncio.wait_for(entity_insert_entered.wait(), timeout=1.0)
        record_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await record_task

        monkeypatch.setattr(db, "execute", original_execute)
        retried = await _record(store, **_payload())
        recalled = await _recall(store, group_id="200", user_id="100")
        projection = await _projection_count(store, group_id="200", user_id="100")
    finally:
        if not record_task.done():
            record_task.cancel()
            await asyncio.gather(record_task, return_exceptions=True)
        await _close_store(store)

    assert rollback_calls == 1, (
        "CancelledError between experience insert and entity projection must rollback"
    )
    assert retried is not None
    assert len(recalled) == 1
    assert projection == 1, (
        "same-evidence retry must rebuild one complete experience/entity transaction"
    )


async def test_recall_before_record_persists_tombstone_and_blocks_late_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory_cards.db"
    first = await _open_store(db_path)
    try:
        await _maybe_await(first.invalidate_evidence(
            group_id="200",
            evidence_message_id="7001",
        ))
        await _maybe_await(first.invalidate_evidence(
            group_id="200",
            evidence_message_id="7001",
        ))
    finally:
        await _close_store(first)

    restarted = await _open_store(db_path)
    late_record: Any = None
    try:
        try:
            late_record = await _record(restarted, **_payload())
        except ValueError:
            late_record = None
        recalled_once = await _recall(restarted, group_id="200", user_id="100")
        projection_once = await _projection_count(
            restarted,
            group_id="200",
            user_id="100",
        )
        await _maybe_await(restarted.invalidate_evidence(
            group_id="200",
            evidence_message_id="7001",
        ))
        recalled_twice = await _recall(restarted, group_id="200", user_id="100")
        projection_twice = await _projection_count(
            restarted,
            group_id="200",
            user_id="100",
        )
    finally:
        await _close_store(restarted)

    if late_record is not None:
        assert _value(late_record, "status") != "active", (
            "late evidence behind a persisted recall tombstone must never become active"
        )
    assert recalled_once == []
    assert projection_once == 0
    assert recalled_twice == []
    assert projection_twice == 0
