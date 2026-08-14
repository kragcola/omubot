"""Behavioral RED tests for the append-only Worldbook governance store."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sqlite3
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from plugins.schedule.story_arc import StoryArc
from services.worldbook import EventRecord
from services.worldbook.governed_adapters import (
    build_schedule_event_proposal,
    verify_committed_world_event,
)
from services.worldbook.reducer import EventReducer

T0 = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)


def _store_module() -> ModuleType:
    spec = importlib.util.find_spec("services.worldbook.governance_store")
    assert spec is not None, "services.worldbook.governance_store.WorldbookGovernanceStore is required"
    return importlib.import_module("services.worldbook.governance_store")


def _contracts() -> ModuleType:
    return importlib.import_module("services.worldbook.governance_contracts")


def _new_store(path: Path) -> Any:
    return _store_module().WorldbookGovernanceStore(str(path))


@asynccontextmanager
async def _opened_store(path: Path) -> AsyncIterator[Any]:
    store = _new_store(path)
    await store.init()
    try:
        yield store
    finally:
        await store.close()


@contextmanager
def _sqlite(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=1.0)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _proposal(*, item: int = 0, world_id: str = "omubot.default") -> Any:
    return build_schedule_event_proposal(
        world_id=world_id,
        target_arc_id=f"arc.schedule.{item}",
        schedule_date=f"2026-08-{item + 1:02d}",
        summary=f"Governed schedule item {item}",
        proposed_at=T0 + timedelta(seconds=item),
    )


def _decision(
    proposal: Any,
    *,
    decision: str = "approve",
    reason_code: str = "reviewed",
    operator_ref: str = "operator:alice",
    decided_at: datetime | str = T0 + timedelta(minutes=1),
) -> Any:
    return _contracts().WorldbookOperatorDecisionV1.create(
        proposal_id=proposal.proposal_id,
        decision=decision,
        reason_code=reason_code,
        operator_ref=operator_ref,
        decided_at=decided_at,
    )


def _receipt(proposal: Any) -> Any:
    committed_input = replace(
        proposal.event,
        status="committed",
        committed_at="2026-07-22T12:05:00+08:00",
        step=7,
    )
    arc = StoryArc(arc_id=proposal.world_ref.arc_id, revision=3)
    committed = EventReducer().apply(arc, committed_input, now_step=7)
    assert isinstance(committed, EventRecord)
    return verify_committed_world_event(
        proposal,
        committed_event=committed,
        committed_arc_snapshot=arc.to_dict(),
        committed_event_ids=tuple(arc.event_budget["committed_event_ids"]),
        persisted_revision=arc.revision,
    )


def _table_columns(connection: sqlite3.Connection) -> dict[str, set[str]]:
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {
        str(row["name"]): {
            str(column["name"])
            for column in connection.execute(
                f'PRAGMA table_info("{str(row["name"]).replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
        }
        for row in tables
    }


def _find_table(columns: dict[str, set[str]], required: set[str]) -> str:
    matches = [name for name, values in columns.items() if required <= values]
    assert len(matches) == 1, f"expected one table with {sorted(required)}, got {matches}"
    return matches[0]


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def test_operator_decision_is_frozen_canonical_and_hash_bound() -> None:
    contracts = _contracts()
    proposal = _proposal()
    local_time = (T0 + timedelta(minutes=1)).astimezone(timezone(timedelta(hours=8)))
    first = _decision(proposal)
    equivalent = _decision(proposal, decided_at=local_time)

    assert first == equivalent
    assert {
        "decision_id",
        "proposal_id",
        "decision",
        "reason_code",
        "operator_ref",
        "decided_at",
        "record_sha256",
    } <= {field.name for field in fields(first)}
    assert first.decision == "approve"
    assert first.decided_at == T0 + timedelta(minutes=1)
    assert first.decision_id
    assert len(first.decision_id) <= 120
    assert len(first.record_sha256) == 64
    int(first.record_sha256, 16)
    payload = first.to_dict()
    assert payload.pop("record_sha256") == first.record_sha256
    assert contracts.worldbook_sha256(payload) == first.record_sha256
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        first.decision = "reject"
    with pytest.raises((TypeError, ValueError)):
        replace(first, record_sha256="0" * 64)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proposal_id", ""),
        ("decision", "allow"),
        ("reason_code", ""),
        ("operator_ref", ""),
        ("decided_at", datetime(2026, 7, 22, 4, 1)),
    ],
)
def test_operator_decision_rejects_blank_open_or_naive_values(
    field: str,
    value: object,
) -> None:
    proposal = _proposal()
    values: dict[str, Any] = {
        "proposal_id": proposal.proposal_id,
        "decision": "approve",
        "reason_code": "reviewed",
        "operator_ref": "operator:alice",
        "decided_at": T0 + timedelta(minutes=1),
    }
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        _contracts().WorldbookOperatorDecisionV1.create(**values)


def test_import_and_constructor_do_not_create_implicit_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    explicit_path = tmp_path / "explicit-worldbook.db"

    store = _new_store(explicit_path)

    assert Path(store.db_path) == explicit_path
    assert list(tmp_path.iterdir()) == []


async def test_init_creates_schema_v1_only_at_explicit_path_and_close_is_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "worldbook.db"
    store = _new_store(path)
    assert not path.exists()

    await store.init()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["worldbook.db"]
    with _sqlite(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        schema = _table_columns(connection)
        assert any("checksum" in values for values in schema.values())
        assert any("proposal_id" in values for values in schema.values())
        assert any("decision_id" in values for values in schema.values())
        assert any("receipt_sha256" in values for values in schema.values())

    await store.close()
    await store.close()


async def test_concurrent_init_is_single_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _store_module()
    real_connect = module.connect_sqlite
    created = 0

    async def tracked_connect(*args: Any, **kwargs: Any) -> Any:
        nonlocal created
        created += 1
        return await real_connect(*args, **kwargs)

    monkeypatch.setattr(module, "connect_sqlite", tracked_connect)
    store = module.WorldbookGovernanceStore(str(tmp_path / "single-flight.db"))
    try:
        await asyncio.wait_for(asyncio.gather(store.init(), store.init()), timeout=2.0)
        assert created == 1
    finally:
        await store.close()


async def test_proposal_is_atomic_idempotent_concurrent_and_restart_durable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "proposals.db"
    proposals = tuple(_proposal(item=index) for index in range(12))

    async with _opened_store(path) as store:
        results = await asyncio.wait_for(
            asyncio.gather(*(store.append_proposal(item) for item in proposals)),
            timeout=3.0,
        )
        assert tuple(results) == proposals
        assert await store.append_proposal(proposals[0]) == proposals[0]
        assert await store.get_proposal(proposals[0].proposal_id) == proposals[0]

    async with _opened_store(path) as reopened:
        assert await reopened.get_proposal(proposals[-1].proposal_id) == proposals[-1]


async def test_proposal_hash_or_id_collision_fails_closed_without_overwrite(
    tmp_path: Path,
) -> None:
    original = _proposal(item=1)
    counterfeit = _proposal(item=2)
    object.__setattr__(counterfeit, "proposal_id", original.proposal_id)

    async with _opened_store(tmp_path / "proposal-conflict.db") as store:
        await store.append_proposal(original)
        with pytest.raises((TypeError, ValueError)):
            await store.append_proposal(counterfeit)
        assert await store.get_proposal(original.proposal_id) == original


async def test_decision_requires_existing_exact_proposal_and_one_terminal_choice(
    tmp_path: Path,
) -> None:
    proposal = _proposal()
    approved = _decision(proposal)
    rejected = _decision(proposal, decision="reject", reason_code="not_supported")
    forged = _decision(proposal)
    object.__setattr__(forged, "record_sha256", "0" * 64)

    async with _opened_store(tmp_path / "decisions.db") as store:
        for invalid in (approved, forged):
            with pytest.raises((TypeError, ValueError)):
                await store.append_operator_decision(invalid)
        await store.append_proposal(proposal)
        assert await store.append_operator_decision(approved) == approved
        assert await store.append_operator_decision(approved) == approved
        with pytest.raises((TypeError, ValueError)):
            await store.append_operator_decision(rejected)
        assert await store.get_operator_decision(proposal.proposal_id) == approved


async def test_receipt_requires_existing_approved_exact_proposal_and_reducer_proof(
    tmp_path: Path,
) -> None:
    proposal = _proposal()
    receipt = _receipt(proposal)
    approved = _decision(proposal)
    forged = _receipt(proposal)
    object.__setattr__(forged, "proposal_sha256", "0" * 64)

    path = tmp_path / "receipts.db"
    async with _opened_store(path) as store:
        with pytest.raises((TypeError, ValueError)):
            await store.append_commit_receipt(receipt)
        await store.append_proposal(proposal)
        for invalid in (receipt, forged):
            with pytest.raises((TypeError, ValueError)):
                await store.append_commit_receipt(invalid)
        await store.append_operator_decision(approved)
        assert await store.append_commit_receipt(receipt) == receipt
        assert await store.append_commit_receipt(receipt) == receipt
        assert await store.get_commit_receipt(proposal.proposal_id) == receipt

    async with _opened_store(path) as reopened:
        assert await reopened.get_proposal(proposal.proposal_id) == proposal
        assert await reopened.get_operator_decision(proposal.proposal_id) == approved
        assert await reopened.get_commit_receipt(proposal.proposal_id) == receipt


async def test_rejected_proposal_can_never_receive_a_receipt(tmp_path: Path) -> None:
    proposal = _proposal()
    receipt = _receipt(proposal)

    async with _opened_store(tmp_path / "rejected.db") as store:
        await store.append_proposal(proposal)
        await store.append_operator_decision(_decision(proposal, decision="reject", reason_code="unsafe"))
        with pytest.raises((TypeError, ValueError)):
            await store.append_commit_receipt(receipt)
        assert await store.get_commit_receipt(proposal.proposal_id) is None


async def test_schema_guards_every_truth_table_against_update_and_delete(
    tmp_path: Path,
) -> None:
    path = tmp_path / "append-only.db"
    proposal = _proposal()
    async with _opened_store(path) as store:
        await store.append_proposal(proposal)
        await store.append_operator_decision(_decision(proposal))
        await store.append_commit_receipt(_receipt(proposal))

    with _sqlite(path) as connection:
        schema = _table_columns(connection)
        truth_tables = {
            _find_table(schema, {"proposal_id", "proposal_sha256", "payload_json"}),
            _find_table(schema, {"decision_id", "record_sha256", "payload_json"}),
            _find_table(schema, {"proposal_id", "receipt_sha256", "payload_json"}),
        }
        trigger_rows = connection.execute("SELECT tbl_name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
        trigger_sql = {
            table: " ".join(str(row["sql"] or "").upper() for row in trigger_rows if str(row["tbl_name"]) == table)
            for table in truth_tables
        }
        for table in truth_tables:
            assert "UPDATE" in trigger_sql[table] and "DELETE" in trigger_sql[table]
            first_column = next(iter(schema[table]))
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(f"UPDATE {_quote(table)} SET {_quote(first_column)} = {_quote(first_column)}")
            connection.rollback()
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(f"DELETE FROM {_quote(table)}")
            connection.rollback()


async def test_reopen_rejects_row_tamper_and_unowned_schema(tmp_path: Path) -> None:
    tamper_path = tmp_path / "row-tamper.db"
    proposal = _proposal()
    async with _opened_store(tamper_path) as store:
        await store.append_proposal(proposal)

    with _sqlite(tamper_path) as connection:
        table = _find_table(
            _table_columns(connection),
            {"proposal_id", "proposal_sha256", "payload_json"},
        )
        guards = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'trigger' AND tbl_name = ? AND upper(sql) LIKE '%UPDATE%'",
            (table,),
        ).fetchall()
        assert guards
        guard_schema = tuple((str(row["name"]), str(row["sql"])) for row in guards)
        for name, _sql in guard_schema:
            connection.execute(f"DROP TRIGGER {_quote(name)}")
        connection.execute(
            f"UPDATE {_quote(table)} SET payload_json = ? WHERE proposal_id = ?",
            (json.dumps({"forged": True}), proposal.proposal_id),
        )
        for _name, sql in guard_schema:
            connection.execute(sql)
        connection.commit()

    reopened = _new_store(tamper_path)
    with pytest.raises((RuntimeError, TypeError, ValueError)):
        await reopened.init()
    await reopened.close()

    extra_path = tmp_path / "extra-schema.db"
    async with _opened_store(extra_path):
        pass
    with _sqlite(extra_path) as connection:
        connection.execute("CREATE TABLE injected_unowned (value TEXT)")
        connection.commit()
    reopened = _new_store(extra_path)
    with pytest.raises((RuntimeError, ValueError)):
        await reopened.init()
    await reopened.close()


async def test_close_waits_for_inflight_append_and_rejects_later_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "close-race.db"
    proposal = _proposal()
    entered = threading.Event()
    release = threading.Event()
    real_connect = sqlite3.connect

    def connect_with_gate(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = real_connect(database, *args, **kwargs)

        def proposal_insert_gate() -> None:
            entered.set()
            if not release.wait(timeout=2.0):
                raise sqlite3.OperationalError("proposal close gate timed out")

        connection.create_function("test_worldbook_close_gate", 0, proposal_insert_gate)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect_with_gate)
    store = _new_store(path)
    await store.init()
    with _sqlite(path) as connection:
        proposal_table = _find_table(
            _table_columns(connection),
            {"proposal_id", "proposal_sha256", "payload_json"},
        )
        connection.execute(
            "CREATE TRIGGER injected_worldbook_close_gate BEFORE INSERT ON "
            f"{_quote(proposal_table)} BEGIN "
            "SELECT test_worldbook_close_gate(); END"
        )
        connection.commit()

    append_task = asyncio.create_task(store.append_proposal(proposal))
    close_task: asyncio.Task[Any] | None = None
    try:
        gate_entered = await asyncio.wait_for(
            asyncio.to_thread(entered.wait, 1.0),
            timeout=1.5,
        )
        assert gate_entered
        close_task = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not close_task.done()
        release.set()
        assert await asyncio.wait_for(append_task, timeout=1.0) == proposal
        await asyncio.wait_for(close_task, timeout=1.0)
        with pytest.raises((RuntimeError, ValueError)):
            await store.append_proposal(_proposal(item=2))
    finally:
        release.set()
        for task in (append_task, close_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (append_task, close_task) if task is not None),
            return_exceptions=True,
        )
        await store.close()

    with _sqlite(path) as connection:
        connection.execute("DROP TRIGGER injected_worldbook_close_gate")
        connection.commit()


async def test_cancelled_append_under_writer_contention_leaves_no_row_or_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cancelled.db"
    proposal = _proposal()
    store = _new_store(path)
    await store.init()
    blocker = sqlite3.connect(path, timeout=1.0)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        task = asyncio.create_task(store.append_proposal(proposal))
        await asyncio.sleep(0.02)
        assert not task.done(), "append should wait for the caller-opened writer"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        blocker.rollback()
        assert await store.get_proposal(proposal.proposal_id) is None
    finally:
        blocker.close()
        await store.close()

    with _sqlite(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
