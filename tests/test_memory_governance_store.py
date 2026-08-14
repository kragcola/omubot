"""Behavioral RED tests for the append-only memory governance shadow store."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sqlite3
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

T0 = datetime(2026, 7, 21, 2, 0, tzinfo=UTC)


def _contracts() -> ModuleType:
    spec = importlib.util.find_spec("services.memory.governance_contracts")
    assert spec is not None, "services.memory.governance_contracts is required"
    return importlib.import_module("services.memory.governance_contracts")


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _store_module() -> ModuleType:
    spec = importlib.util.find_spec("services.memory.governance_store")
    assert spec is not None, (
        "services.memory.governance_store.MemoryGovernanceStore is required"
    )
    return importlib.import_module("services.memory.governance_store")


def _new_store(path: Path) -> Any:
    module = _store_module()
    return module.MemoryGovernanceStore(str(path))


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


def _candidate(
    contracts: ModuleType,
    *,
    item: str = "one",
    content: str = "likes jasmine tea",
    category: str = "preference",
    operation: str = "create",
    target_ref: str | None = None,
) -> Any:
    evidence = contracts.EvidenceAtomV1(
        evidence_ref=f"message:onebot:private:42:msg-{item}",
        content_sha256=contracts.sha256_text(f"raw evidence {item}"),
        quote=f"raw evidence {item}",
        actor_ref="user:qq:42",
        occurred_at=T0,
    )
    observation = contracts.ObservationV1(
        source_kind="user_statement",
        producer_kind="memo",
        producer_version="memo-v2",
        producer_run_id="memo-run-store-red",
        subject_ref="user:qq:42",
        owner_scope="user",
        owner_id="42",
        visibility="private",
        origin_group_ref=None,
        claim=contracts.CardClaimV1(category=category, content=content),
        evidence=(evidence,),
        observed_at=T0 + timedelta(seconds=1),
        source_occurred_at=T0,
        time_basis="source_event",
        valid_from=None,
        valid_to=None,
        confidence=0.8,
    )
    proposal = contracts.ProjectionProposalV1.create(
        projection_kind="card",
        operation=operation,
        target_ref=target_ref,
        payload={"category": category, "content": content},
    )
    return contracts.CandidateEnvelopeV1.create(
        observation=observation,
        proposal=proposal,
        producer_kind="memo",
        producer_run_id="memo-run-store-red",
        producer_item_id=item,
        produced_at=T0 + timedelta(seconds=2),
        model_output=f'{{"item":"{item}"}}',
    )


def _conflict(
    contracts: ModuleType,
    first: Any,
    second: Any,
    *,
    suffix: str = "city",
    candidate_ids: tuple[str, ...] | None = None,
    detected_at: datetime | None = None,
) -> Any:
    return contracts.ConflictV1.create(
        kind="contradiction",
        subject_ref="user:qq:42",
        claim_key=f"fact:{suffix}",
        observation_ids=(
            first.observation.observation_id,
            second.observation.observation_id,
        ),
        candidate_ids=(
            (first.candidate_id, second.candidate_id)
            if candidate_ids is None
            else candidate_ids
        ),
        existing_projection_refs=("card:legacy-1",),
        detected_at=detected_at or T0 + timedelta(seconds=3),
        detector="deterministic",
        basis_evidence_refs=tuple(
            atom.evidence_ref
            for candidate in (first, second)
            for atom in candidate.observation.evidence
        ),
    )


def _promotion_event(
    contracts: ModuleType,
    candidate: Any,
    event_kind: str,
    *,
    occurred_at: datetime | None = None,
    actor_kind: str = "policy",
    actor_ref: str = "policy:memory-v1",
    conflict_ids: tuple[str, ...] = (),
    projection_kind: str | None = None,
    operation: str | None = None,
    projection_ref: str | None = None,
    receipt_ref: str | None = None,
) -> Any:
    return contracts.PromotionEventV1.create(
        candidate_id=candidate.candidate_id,
        candidate_sha256=candidate.candidate_sha256,
        event_kind=event_kind,
        actor_kind=actor_kind,
        actor_ref=actor_ref,
        occurred_at=occurred_at or T0 + timedelta(seconds=4),
        reason_code="store_red",
        operator_note="",
        conflict_ids=conflict_ids,
        projection_kind=projection_kind or candidate.proposal.projection_kind,
        operation=operation or candidate.proposal.operation,
        projection_ref=projection_ref,
        receipt_ref=receipt_ref,
    )


def _table_columns(connection: sqlite3.Connection) -> dict[str, set[str]]:
    tables = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
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


def _find_table(
    columns_by_table: dict[str, set[str]],
    *,
    required: set[str],
    excluded: set[str] | frozenset[str] = frozenset(),
) -> str:
    matches = [
        table
        for table, columns in columns_by_table.items()
        if required <= columns and (not excluded or not excluded <= columns)
    ]
    assert len(matches) == 1, (
        f"expected one table with required={sorted(required)} "
        f"excluded={sorted(excluded)}, got {matches}"
    )
    return matches[0]


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def test_store_module_import_and_constructor_have_no_implicit_storage_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    explicit_path = tmp_path / "explicit-shadow.db"
    assert not (tmp_path / "storage").exists()
    assert not explicit_path.exists()

    store = _new_store(explicit_path)

    assert Path(store.db_path) == explicit_path
    assert not explicit_path.exists()
    assert not (tmp_path / "storage").exists()


@pytest.mark.asyncio
async def test_init_creates_verified_schema_v1_only_at_explicit_path_and_close_is_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "governance.db"
    store = _new_store(path)
    assert not path.exists()

    await store.init()
    assert path.is_file()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["governance.db"]
    with _sqlite(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        schema = _table_columns(connection)
        assert any("checksum" in columns for columns in schema.values())
        assert any("observation_id" in columns for columns in schema.values())
        assert any("candidate_id" in columns for columns in schema.values())
        assert any("conflict_id" in columns for columns in schema.values())
        assert any("event_id" in columns for columns in schema.values())

    await store.close()
    await store.close()


@pytest.mark.asyncio
async def test_concurrent_init_is_single_flight_and_close_releases_one_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _store_module()
    real_connect_sqlite = module.connect_sqlite
    created_count = 0
    live_count = 0
    close_count = 0

    class TrackedConnection:
        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.closed = False

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        async def close(self) -> None:
            nonlocal live_count, close_count
            if self.closed:
                return
            await self.inner.close()
            self.closed = True
            live_count -= 1
            close_count += 1

    connections: list[TrackedConnection] = []

    async def tracked_connect_sqlite(*args: Any, **kwargs: Any) -> TrackedConnection:
        nonlocal created_count, live_count
        inner = await real_connect_sqlite(*args, **kwargs)
        tracked = TrackedConnection(inner)
        connections.append(tracked)
        created_count += 1
        live_count += 1
        return tracked

    monkeypatch.setattr(module, "connect_sqlite", tracked_connect_sqlite)
    store = module.MemoryGovernanceStore(str(tmp_path / "concurrent-init.db"))
    try:
        await asyncio.wait_for(
            asyncio.gather(store.init(), store.init()),
            timeout=2.0,
        )
        assert created_count == 1
        assert live_count == 1

        await asyncio.wait_for(store.close(), timeout=1.0)
        assert live_count == 0
        assert close_count == 1
        await store.close()
        assert close_count == 1
    finally:
        await store.close()
        for connection in connections:
            if not connection.closed:
                await connection.close()


@pytest.mark.asyncio
async def test_store_public_surface_is_append_and_read_only(tmp_path: Path) -> None:
    async with _opened_store(tmp_path / "surface.db") as store:
        for name in (
            "update_observation",
            "delete_observation",
            "update_candidate",
            "delete_candidate",
            "update_conflict",
            "delete_conflict",
            "update_promotion_event",
            "delete_promotion_event",
        ):
            assert not hasattr(store, name), f"append-only store must not expose {name}"
        public_mutators = {
            name
            for name in dir(store)
            if not name.startswith("_") and name.startswith(("update", "delete"))
        }
        assert public_mutators == set()


@pytest.mark.asyncio
async def test_append_candidate_is_atomic_idempotent_and_restart_durable(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    path = tmp_path / "candidate.db"
    candidate = _candidate(contracts)

    async with _opened_store(path) as store:
        await store.append_candidate(candidate)
        await store.append_candidate(candidate)
        assert await store.get_observation(candidate.observation.observation_id) == candidate.observation
        assert await store.get_candidate(candidate.candidate_id) == candidate

    async with _opened_store(path) as reopened:
        assert await reopened.get_observation(candidate.observation.observation_id) == candidate.observation
        assert await reopened.get_candidate(candidate.candidate_id) == candidate


@pytest.mark.parametrize("record_kind", ["observation", "candidate", "promotion_event"])
@pytest.mark.asyncio
async def test_reads_reject_rows_whose_redundant_columns_mismatch_payload(
    tmp_path: Path,
    record_kind: str,
) -> None:
    contracts = _contracts()
    path = tmp_path / f"forged-{record_kind}.db"
    candidate = _candidate(contracts, item=f"integrity-{record_kind}")
    approved = _promotion_event(contracts, candidate, "promotion_approved")

    async with _opened_store(path) as store:
        await store.append_candidate(candidate)
        if record_kind == "promotion_event":
            await store.append_promotion_event(approved)

        with _sqlite(path) as connection:
            schema = _table_columns(connection)
            observation_table = _find_table(
                schema,
                required={"observation_id", "observation_sha256", "payload_json"},
            )
            candidate_table = _find_table(
                schema,
                required={"candidate_id", "candidate_sha256", "observation_id"},
            )
            event_table = _find_table(
                schema,
                required={"event_id", "idempotency_key", "candidate_id"},
            )
            forged_sha256 = "e" * 64
            forged_timestamp = "1999-01-01T00:00:00Z"

            if record_kind == "observation":
                forged_id = "mobs_" + "f" * 24
                payload_json = connection.execute(
                    f"SELECT payload_json FROM {_quote_identifier(observation_table)} "
                    "WHERE observation_id = ?",
                    (candidate.observation.observation_id,),
                ).fetchone()["payload_json"]
                connection.execute(
                    f"INSERT INTO {_quote_identifier(observation_table)} "
                    "(observation_id, observation_sha256, payload_json, "
                    "observed_at, recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        forged_id,
                        forged_sha256,
                        payload_json,
                        forged_timestamp,
                        forged_timestamp,
                    ),
                )
            elif record_kind == "candidate":
                forged_id = "mcand_" + "f" * 24
                payload_json = connection.execute(
                    f"SELECT payload_json FROM {_quote_identifier(candidate_table)} "
                    "WHERE candidate_id = ?",
                    (candidate.candidate_id,),
                ).fetchone()["payload_json"]
                connection.execute(
                    f"INSERT INTO {_quote_identifier(candidate_table)} "
                    "(candidate_id, candidate_sha256, observation_id, projection_kind, "
                    "operation, payload_json, produced_at, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        forged_id,
                        forged_sha256,
                        candidate.observation.observation_id,
                        "style",
                        "supersede",
                        payload_json,
                        forged_timestamp,
                        forged_timestamp,
                    ),
                )
            else:
                forged_id = "mpev_" + "f" * 24
                append_order_table = _find_table(
                    schema,
                    required={"append_seq", "entity_kind", "entity_id", "recorded_at"},
                )
                payload_json = connection.execute(
                    f"SELECT payload_json FROM {_quote_identifier(event_table)} "
                    "WHERE event_id = ?",
                    (approved.event_id,),
                ).fetchone()["payload_json"]
                append_cursor = connection.execute(
                    f"INSERT INTO {_quote_identifier(append_order_table)} "
                    "(entity_kind, entity_id, recorded_at) VALUES (?, ?, ?)",
                    ("promotion_event", forged_id, forged_timestamp),
                )
                assert append_cursor.lastrowid is not None
                append_seq = int(append_cursor.lastrowid)
                connection.execute(
                    f"INSERT INTO {_quote_identifier(event_table)} "
                    "(append_seq, event_id, idempotency_key, candidate_id, candidate_sha256, "
                    "event_kind, projection_kind, operation, payload_json, "
                    "occurred_at, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        append_seq,
                        forged_id,
                        "sha256:" + forged_sha256,
                        candidate.candidate_id,
                        forged_sha256,
                        "promotion_rejected",
                        "style",
                        "supersede",
                        payload_json,
                        forged_timestamp,
                        forged_timestamp,
                    ),
                )
            connection.commit()

        with pytest.raises((RuntimeError, TypeError, ValueError)):
            if record_kind == "observation":
                await store.get_observation(forged_id)
            elif record_kind == "candidate":
                await store.get_candidate(forged_id)
            else:
                await store.list_promotion_events(candidate.candidate_id)


@pytest.mark.asyncio
async def test_append_candidate_rejects_id_hash_collision_without_overwrite(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    original = _candidate(contracts, item="original", content="likes tea")
    counterfeit = _candidate(contracts, item="counterfeit", content="likes coffee")
    object.__setattr__(counterfeit, "candidate_id", original.candidate_id)

    async with _opened_store(tmp_path / "collision.db") as store:
        await store.append_candidate(original)
        with pytest.raises((TypeError, ValueError)):
            await store.append_candidate(counterfeit)
        assert await store.get_candidate(original.candidate_id) == original
        assert await store.get_observation(
            counterfeit.observation.observation_id
        ) is None


@pytest.mark.asyncio
async def test_append_conflict_requires_existing_participants_and_is_idempotent(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    first = _candidate(contracts, item="a", content="lives in Shanghai", category="fact")
    second = _candidate(contracts, item="b", content="lives in Beijing", category="fact")
    conflict = _conflict(contracts, first, second)
    path = tmp_path / "conflict.db"

    async with _opened_store(path) as store:
        with pytest.raises((TypeError, ValueError)):
            await store.append_conflict(conflict)
        assert await store.get_conflict(conflict.conflict_id) is None

        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_conflict(conflict)
        await store.append_conflict(conflict)
        assert await store.get_conflict(conflict.conflict_id) == conflict

    with _sqlite(path) as connection:
        schema = _table_columns(connection)
        observation_link = _find_table(
            schema,
            required={"conflict_id", "observation_id"},
        )
        candidate_link = _find_table(
            schema,
            required={"conflict_id", "candidate_id"},
            excluded={"event_id"},
        )
        projection_link = _find_table(
            schema,
            required={"conflict_id", "projection_ref"},
        )
        assert connection.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(observation_link)}"
        ).fetchone()[0] == 2
        assert connection.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(candidate_link)}"
        ).fetchone()[0] == 2
        assert connection.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(projection_link)}"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_conflict_collision_cannot_replace_immutable_links(tmp_path: Path) -> None:
    contracts = _contracts()
    first = _candidate(contracts, item="a", content="lives in Shanghai", category="fact")
    second = _candidate(contracts, item="b", content="lives in Beijing", category="fact")
    conflict = _conflict(contracts, first, second)
    counterfeit = _conflict(contracts, first, second, suffix="other")
    object.__setattr__(counterfeit, "conflict_id", conflict.conflict_id)

    async with _opened_store(tmp_path / "conflict-collision.db") as store:
        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_conflict(conflict)
        with pytest.raises((TypeError, ValueError)):
            await store.append_conflict(counterfeit)
        assert await store.get_conflict(conflict.conflict_id) == conflict


@pytest.mark.asyncio
async def test_conflict_reads_fold_and_retry_reject_auxiliary_candidate_link_mismatch(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    first = _candidate(contracts, item="link-a", content="lives in Shanghai", category="fact")
    second = _candidate(contracts, item="link-b", content="lives in Beijing", category="fact")
    unrelated = _candidate(
        contracts,
        item="link-unrelated",
        content="owns a bicycle",
        category="fact",
    )
    conflict = _conflict(contracts, first, second, suffix="auxiliary-link")
    unrelated_approval = _promotion_event(
        contracts,
        unrelated,
        "promotion_approved",
    )
    path = tmp_path / "conflict-auxiliary-link-mismatch.db"

    async with _opened_store(path) as store:
        for candidate in (first, second, unrelated):
            await store.append_candidate(candidate)
        await store.append_promotion_event(unrelated_approval)
        await store.append_conflict(conflict)

        with _sqlite(path) as connection:
            schema = _table_columns(connection)
            candidate_link_table = _find_table(
                schema,
                required={"conflict_id", "candidate_id"},
                excluded={"event_id"},
            )
            connection.execute(
                f"INSERT INTO {_quote_identifier(candidate_link_table)} "
                "(conflict_id, candidate_id) VALUES (?, ?)",
                (conflict.conflict_id, unrelated.candidate_id),
            )
            connection.commit()

        results = await asyncio.gather(
            store.get_conflict(conflict.conflict_id),
            store.fold_candidate(unrelated.candidate_id),
            store.append_conflict(conflict),
            return_exceptions=True,
        )
        assert all(
            isinstance(result, (RuntimeError, TypeError, ValueError))
            for result in results
        ), f"auxiliary conflict-link mismatch must fail closed, got {results!r}"


@pytest.mark.asyncio
async def test_promotion_append_requires_exact_candidate_digest_and_proposal(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    candidate = _candidate(contracts)
    path = tmp_path / "promotion-binding.db"

    wrong_digest = _promotion_event(contracts, candidate, "promotion_approved")
    object.__setattr__(wrong_digest, "candidate_sha256", "0" * 64)
    wrong_kind = _promotion_event(
        contracts,
        candidate,
        "promotion_approved",
        projection_kind="style",
    )
    wrong_operation = _promotion_event(
        contracts,
        candidate,
        "promotion_approved",
        operation="supersede",
    )

    async with _opened_store(path) as store:
        await store.append_candidate(candidate)
        for event in (wrong_digest, wrong_kind, wrong_operation):
            with pytest.raises((TypeError, ValueError)):
                await store.append_promotion_event(event)
        assert await store.list_promotion_events(candidate.candidate_id) == ()


@pytest.mark.asyncio
async def test_promotion_events_are_idempotent_append_ordered_and_restart_durable(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    candidate = _candidate(contracts)
    same_time = T0 + timedelta(seconds=5)
    approved = _promotion_event(
        contracts,
        candidate,
        "promotion_approved",
        occurred_at=same_time,
    )
    applied = _promotion_event(
        contracts,
        candidate,
        "projection_applied",
        occurred_at=same_time,
        actor_kind="projector",
        actor_ref="projector:card-v1",
        projection_ref="card:77",
        receipt_ref="receipt:card-77",
    )
    path = tmp_path / "promotion-order.db"

    async with _opened_store(path) as store:
        await store.append_candidate(candidate)
        await store.append_promotion_event(approved)
        await store.append_promotion_event(approved)
        await store.append_promotion_event(applied)
        await store.append_promotion_event(applied)
        assert await store.list_promotion_events(candidate.candidate_id) == (
            approved,
            applied,
        )
        folded = await store.fold_candidate(candidate.candidate_id)
        assert folded.projection_event_id == applied.event_id
        assert folded.receipt_ref == "receipt:card-77"

    async with _opened_store(path) as reopened:
        assert await reopened.list_promotion_events(candidate.candidate_id) == (
            approved,
            applied,
        )
        assert (await reopened.fold_candidate(candidate.candidate_id)).receipt_ref == (
            "receipt:card-77"
        )


@pytest.mark.asyncio
async def test_unresolved_conflicts_require_complete_operator_resolution(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    first = _candidate(contracts, item="a", content="lives in Shanghai", category="fact")
    second = _candidate(contracts, item="b", content="lives in Beijing", category="fact")
    conflict = _conflict(
        contracts,
        first,
        second,
        detected_at=T0 + timedelta(seconds=3),
    )
    policy_approval = _promotion_event(
        contracts,
        first,
        "promotion_approved",
        occurred_at=T0 + timedelta(seconds=4),
    )
    incomplete_operator = _promotion_event(
        contracts,
        first,
        "promotion_approved",
        occurred_at=T0 + timedelta(seconds=4),
        actor_kind="operator",
        actor_ref="operator:alice",
    )
    complete_operator = _promotion_event(
        contracts,
        first,
        "promotion_approved",
        occurred_at=T0 + timedelta(seconds=4),
        actor_kind="operator",
        actor_ref="operator:alice",
        conflict_ids=(conflict.conflict_id,),
    )

    async with _opened_store(tmp_path / "conflict-resolution.db") as store:
        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_conflict(conflict)
        with pytest.raises((TypeError, ValueError)):
            await store.append_promotion_event(policy_approval)
        with pytest.raises((TypeError, ValueError)):
            await store.append_promotion_event(incomplete_operator)
        await store.append_promotion_event(complete_operator)
        assert await store.list_promotion_events(first.candidate_id) == (
            complete_operator,
        )
        folded = await store.fold_candidate(first.candidate_id)
        assert _enum_value(folded.status) == "approved"
        assert folded.resolved_conflict_ids == (conflict.conflict_id,)
        assert folded.unresolved_conflict_ids == ()


@pytest.mark.asyncio
async def test_observation_linked_conflict_blocks_policy_for_each_candidate(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    first = _candidate(contracts, item="a", content="lives in Shanghai", category="fact")
    second = _candidate(contracts, item="b", content="lives in Beijing", category="fact")
    conflict = _conflict(contracts, first, second, candidate_ids=())
    first_policy_approval = _promotion_event(contracts, first, "promotion_approved")
    second_policy_approval = _promotion_event(contracts, second, "promotion_approved")
    operator_approval = _promotion_event(
        contracts,
        first,
        "promotion_approved",
        actor_kind="operator",
        actor_ref="operator:alice",
        conflict_ids=(conflict.conflict_id,),
    )

    async with _opened_store(tmp_path / "observation-conflict-resolution.db") as store:
        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_conflict(conflict)
        assert await store.get_conflict(conflict.conflict_id) == conflict

        for policy_approval in (first_policy_approval, second_policy_approval):
            with pytest.raises((TypeError, ValueError)):
                await store.append_promotion_event(policy_approval)

        await store.append_promotion_event(operator_approval)
        assert await store.list_promotion_events(first.candidate_id) == (
            operator_approval,
        )
        assert await store.list_promotion_events(second.candidate_id) == ()


@pytest.mark.asyncio
async def test_missing_observation_link_cannot_hide_conflict_from_read_or_promotion(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    first = _candidate(
        contracts,
        item="missing-link-a",
        content="lives in Shanghai",
        category="fact",
    )
    second = _candidate(
        contracts,
        item="missing-link-b",
        content="lives in Beijing",
        category="fact",
    )
    conflict = _conflict(
        contracts,
        first,
        second,
        suffix="missing-observation-link",
        candidate_ids=(),
    )
    approval = _promotion_event(contracts, first, "promotion_approved")
    path = tmp_path / "conflict-missing-observation-link.db"

    async with _opened_store(path) as store:
        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_conflict(conflict)

        with _sqlite(path) as connection:
            schema = _table_columns(connection)
            observation_link_table = _find_table(
                schema,
                required={"conflict_id", "observation_id"},
            )
            trigger_rows = connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = ? ORDER BY name",
                (observation_link_table,),
            ).fetchall()
            trigger_schema_before = tuple(
                (str(row["name"]), str(row["sql"])) for row in trigger_rows
            )
            delete_guards = [
                row
                for row in trigger_rows
                if "BEFORE DELETE" in str(row["sql"] or "").upper()
            ]
            assert len(delete_guards) == 1
            delete_guard_name = str(delete_guards[0]["name"])
            delete_guard_sql = delete_guards[0]["sql"]
            assert isinstance(delete_guard_sql, str)

            connection.execute(
                f"DROP TRIGGER {_quote_identifier(delete_guard_name)}"
            )
            deleted = connection.execute(
                f"DELETE FROM {_quote_identifier(observation_link_table)} "
                "WHERE conflict_id = ? AND observation_id = ?",
                (conflict.conflict_id, first.observation.observation_id),
            )
            assert deleted.rowcount == 1
            connection.execute(delete_guard_sql)
            connection.commit()

            trigger_schema_after = tuple(
                (str(row["name"]), str(row["sql"]))
                for row in connection.execute(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = 'trigger' AND tbl_name = ? ORDER BY name",
                    (observation_link_table,),
                ).fetchall()
            )
            assert trigger_schema_after == trigger_schema_before
            assert connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(observation_link_table)} "
                "WHERE conflict_id = ? AND observation_id = ?",
                (conflict.conflict_id, first.observation.observation_id),
            ).fetchone()[0] == 0
            assert connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(observation_link_table)} "
                "WHERE conflict_id = ? AND observation_id = ?",
                (conflict.conflict_id, second.observation.observation_id),
            ).fetchone()[0] == 1

        read_result, approval_result = await asyncio.gather(
            store.get_conflict(conflict.conflict_id),
            store.append_promotion_event(approval),
            return_exceptions=True,
        )
        events = await store.list_promotion_events(first.candidate_id)
        failure_types = (RuntimeError, TypeError, ValueError)
        assert isinstance(read_result, failure_types), (
            f"missing observation link must make conflict reads fail closed, got "
            f"{read_result!r}"
        )
        assert isinstance(approval_result, failure_types), (
            "missing observation link must not hide the conflict from promotion "
            f"discovery, got approval={approval_result!r}, events={events!r}"
        )
        assert events == ()


@pytest.mark.asyncio
async def test_backdated_operator_approval_cannot_resolve_future_detected_conflict(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    first = _candidate(
        contracts,
        item="future-a",
        content="lives in Shanghai",
        category="fact",
    )
    second = _candidate(
        contracts,
        item="future-b",
        content="lives in Beijing",
        category="fact",
    )
    conflict = _conflict(
        contracts,
        first,
        second,
        suffix="future-city",
        detected_at=T0 + timedelta(seconds=10),
    )
    backdated_approval = _promotion_event(
        contracts,
        first,
        "promotion_approved",
        occurred_at=T0 + timedelta(seconds=4),
        actor_kind="operator",
        actor_ref="operator:alice",
        conflict_ids=(conflict.conflict_id,),
    )

    async with _opened_store(tmp_path / "future-conflict-backdated-approval.db") as store:
        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_conflict(conflict)

        with pytest.raises((TypeError, ValueError)):
            await store.append_promotion_event(backdated_approval)
        assert await store.list_promotion_events(first.candidate_id) == ()


@pytest.mark.asyncio
async def test_late_conflict_after_approval_requires_resolution_before_projection(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    first = _candidate(contracts, item="late-a", content="lives in Shanghai", category="fact")
    second = _candidate(contracts, item="late-b", content="lives in Beijing", category="fact")
    approved_at = T0 + timedelta(seconds=4)
    conflict_detected_at = T0 + timedelta(seconds=5)
    approved = _promotion_event(
        contracts,
        first,
        "promotion_approved",
        occurred_at=approved_at,
    )
    conflict = _conflict(
        contracts,
        first,
        second,
        suffix="late-city",
        detected_at=conflict_detected_at,
    )
    blocked_projection = _promotion_event(
        contracts,
        first,
        "projection_applied",
        occurred_at=T0 + timedelta(seconds=6),
        actor_kind="projector",
        actor_ref="projector:card-v1",
        projection_ref="card:late-city",
        receipt_ref="receipt:blocked-late-city",
    )
    path = tmp_path / "late-conflict-after-approval.db"

    async with _opened_store(path) as store:
        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_promotion_event(approved)
        await store.append_conflict(conflict)

        conflicted = await store.fold_candidate(first.candidate_id)
        assert _enum_value(conflicted.status) == "conflicted"
        assert conflicted.decision_event_id == approved.event_id
        assert conflicted.resolved_conflict_ids == ()
        assert conflicted.unresolved_conflict_ids == (conflict.conflict_id,)

        with pytest.raises((TypeError, ValueError)):
            await store.append_promotion_event(blocked_projection)
        assert await store.list_promotion_events(first.candidate_id) == (approved,)

        unknown_resolution = _promotion_event(
            contracts,
            first,
            "conflict_resolved",
            occurred_at=T0 + timedelta(seconds=7),
            actor_kind="operator",
            actor_ref="operator:alice",
            conflict_ids=("mconf_" + "f" * 24,),
        )
        with pytest.raises((TypeError, ValueError)):
            await store.append_promotion_event(unknown_resolution)

        resolution = _promotion_event(
            contracts,
            first,
            "conflict_resolved",
            occurred_at=T0 + timedelta(seconds=7),
            actor_kind="operator",
            actor_ref="operator:alice",
            conflict_ids=(conflict.conflict_id,),
        )
        await store.append_promotion_event(resolution)
        restored = await store.fold_candidate(first.candidate_id)
        assert _enum_value(restored.status) == "approved"
        assert restored.decision_event_id == approved.event_id
        assert restored.resolved_conflict_ids == (conflict.conflict_id,)
        assert restored.unresolved_conflict_ids == ()

        applied = _promotion_event(
            contracts,
            first,
            "projection_applied",
            occurred_at=T0 + timedelta(seconds=8),
            actor_kind="projector",
            actor_ref="projector:card-v1",
            projection_ref="card:late-city",
            receipt_ref="receipt:late-city",
        )
        await store.append_promotion_event(applied)
        projected = await store.fold_candidate(first.candidate_id)
        assert _enum_value(projected.status) == "projected"
        assert projected.projection_event_id == applied.event_id
        assert projected.resolved_conflict_ids == (conflict.conflict_id,)
        assert projected.unresolved_conflict_ids == ()


@pytest.mark.asyncio
async def test_late_conflict_after_projection_surfaces_projected_conflict(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    first = _candidate(
        contracts,
        item="projected-a",
        content="lives in Shanghai",
        category="fact",
    )
    second = _candidate(
        contracts,
        item="projected-b",
        content="lives in Beijing",
        category="fact",
    )
    approved = _promotion_event(
        contracts,
        first,
        "promotion_approved",
        occurred_at=T0 + timedelta(seconds=4),
    )
    applied = _promotion_event(
        contracts,
        first,
        "projection_applied",
        occurred_at=T0 + timedelta(seconds=5),
        actor_kind="projector",
        actor_ref="projector:card-v1",
        projection_ref="card:projected-city",
        receipt_ref="receipt:projected-city",
    )
    conflict = _conflict(
        contracts,
        first,
        second,
        suffix="projected-city",
        detected_at=T0 + timedelta(seconds=6),
    )

    async with _opened_store(tmp_path / "late-conflict-after-projection.db") as store:
        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_promotion_event(approved)
        await store.append_promotion_event(applied)
        await store.append_conflict(conflict)

        folded = await store.fold_candidate(first.candidate_id)
        assert _enum_value(folded.status) == "projected_conflict"
        assert folded.decision_event_id == approved.event_id
        assert folded.projection_event_id == applied.event_id
        assert folded.resolved_conflict_ids == ()
        assert folded.unresolved_conflict_ids == (conflict.conflict_id,)


@pytest.mark.asyncio
async def test_backdated_conflict_recorded_after_projection_uses_append_order(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    first = _candidate(
        contracts,
        item="recorded-late-a",
        content="lives in Shanghai",
        category="fact",
    )
    second = _candidate(
        contracts,
        item="recorded-late-b",
        content="lives in Beijing",
        category="fact",
    )
    approved = _promotion_event(
        contracts,
        first,
        "promotion_approved",
        occurred_at=T0 + timedelta(seconds=4),
    )
    applied = _promotion_event(
        contracts,
        first,
        "projection_applied",
        occurred_at=T0 + timedelta(seconds=5),
        actor_kind="projector",
        actor_ref="projector:card-v1",
        projection_ref="card:recorded-late-city",
        receipt_ref="receipt:recorded-late-city",
    )
    conflict = _conflict(
        contracts,
        first,
        second,
        suffix="recorded-late-city",
        detected_at=T0 + timedelta(seconds=3),
    )
    resolution = _promotion_event(
        contracts,
        first,
        "conflict_resolved",
        occurred_at=T0 + timedelta(seconds=6),
        actor_kind="operator",
        actor_ref="operator:alice",
        conflict_ids=(conflict.conflict_id,),
    )

    async with _opened_store(tmp_path / "backdated-conflict-recorded-late.db") as store:
        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_promotion_event(approved)
        await store.append_promotion_event(applied)
        await store.append_conflict(conflict)

        conflicted = await store.fold_candidate(first.candidate_id)
        assert _enum_value(conflicted.status) == "projected_conflict"
        assert conflicted.decision_event_id == approved.event_id
        assert conflicted.projection_event_id == applied.event_id
        assert conflicted.resolved_conflict_ids == ()
        assert conflicted.unresolved_conflict_ids == (conflict.conflict_id,)

        await store.append_promotion_event(resolution)
        restored = await store.fold_candidate(first.candidate_id)
        assert _enum_value(restored.status) == "projected"
        assert restored.decision_event_id == approved.event_id
        assert restored.projection_event_id == applied.event_id
        assert restored.resolved_conflict_ids == (conflict.conflict_id,)
        assert restored.unresolved_conflict_ids == ()
        assert await store.list_promotion_events(first.candidate_id) == (
            approved,
            applied,
            resolution,
        )


@pytest.mark.asyncio
async def test_opposite_promotion_decisions_race_to_one_durable_winner(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    path = tmp_path / "decision-race.db"
    first_store = _new_store(path)
    second_store = _new_store(path)
    await first_store.init()
    await second_store.init()
    candidate = _candidate(contracts)
    approved = _promotion_event(contracts, candidate, "promotion_approved")
    rejected = _promotion_event(
        contracts,
        candidate,
        "promotion_rejected",
        occurred_at=T0 + timedelta(seconds=5),
    )
    try:
        await first_store.append_candidate(candidate)
        results = await asyncio.gather(
            first_store.append_promotion_event(approved),
            second_store.append_promotion_event(rejected),
            return_exceptions=True,
        )
        assert sum(isinstance(result, BaseException) for result in results) == 1
        events = await first_store.list_promotion_events(candidate.candidate_id)
        assert len(events) == 1
        assert events[0] in {approved, rejected}
        folded = await first_store.fold_candidate(candidate.candidate_id)
        assert folded.decision_event_id == events[0].event_id
    finally:
        await first_store.close()
        await second_store.close()


@pytest.mark.asyncio
async def test_exact_promotion_event_race_reports_one_insert_across_connections(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    path = tmp_path / "exact-decision-race.db"
    first_store = _new_store(path)
    second_store = _new_store(path)
    await first_store.init()
    await second_store.init()
    candidate = _candidate(contracts, item="exact-race")
    rejected = _promotion_event(contracts, candidate, "promotion_rejected")
    try:
        await first_store.append_candidate(candidate)
        results = await asyncio.gather(
            first_store.append_promotion_event_with_outcome(rejected),
            second_store.append_promotion_event_with_outcome(rejected),
        )

        assert sorted(inserted for _, inserted in results) == [False, True]
        assert all(event == rejected for event, _ in results)
        assert await first_store.list_promotion_events(candidate.candidate_id) == (
            rejected,
        )
        assert await first_store.append_promotion_event(rejected) == rejected
    finally:
        await first_store.close()
        await second_store.close()


@pytest.mark.asyncio
async def test_schema_has_fk_indexes_and_update_delete_guards_for_all_truth_rows(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    path = tmp_path / "schema-guards.db"
    first = _candidate(contracts, item="a", content="lives in Shanghai", category="fact")
    second = _candidate(contracts, item="b", content="lives in Beijing", category="fact")
    conflict = _conflict(contracts, first, second)
    approved = _promotion_event(
        contracts,
        first,
        "promotion_approved",
        actor_kind="operator",
        actor_ref="operator:alice",
        conflict_ids=(conflict.conflict_id,),
    )

    async with _opened_store(path) as store:
        await store.append_candidate(first)
        await store.append_candidate(second)
        await store.append_conflict(conflict)
        await store.append_promotion_event(approved)

    with _sqlite(path) as connection:
        schema = _table_columns(connection)
        ledger_tables = {
            table for table, columns in schema.items() if "checksum" in columns
        }
        truth_tables = {
            table
            for table, columns in schema.items()
            if table not in ledger_tables
            and columns
            & {
                "observation_id",
                "candidate_id",
                "conflict_id",
                "event_id",
                "projection_ref",
            }
        }
        assert len(truth_tables) >= 7
        trigger_rows = connection.execute(
            "SELECT tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
        trigger_sql_by_table: dict[str, str] = {}
        for row in trigger_rows:
            trigger_sql_by_table.setdefault(str(row["tbl_name"]), "")
            trigger_sql_by_table[str(row["tbl_name"])] += str(row["sql"] or "").upper()

        for table in truth_tables:
            assert connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
            ).fetchone()[0] > 0
            trigger_sql = trigger_sql_by_table.get(table, "")
            assert "UPDATE" in trigger_sql and "DELETE" in trigger_sql
            first_column = next(iter(schema[table]))
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(
                    f"UPDATE {_quote_identifier(table)} "
                    f"SET {_quote_identifier(first_column)} = {_quote_identifier(first_column)}"
                )
            connection.rollback()
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(f"DELETE FROM {_quote_identifier(table)}")
            connection.rollback()

        fk_tables = {
            table
            for table, columns in schema.items()
            if {"candidate_id", "observation_id"} <= columns
            or {"conflict_id", "observation_id"} <= columns
            or {"conflict_id", "candidate_id"} <= columns
            or "event_id" in columns
        }
        assert fk_tables
        for table in fk_tables:
            assert connection.execute(
                f"PRAGMA foreign_key_list({_quote_identifier(table)})"
            ).fetchall(), f"{table} must declare foreign keys"
            assert connection.execute(
                f"PRAGMA index_list({_quote_identifier(table)})"
            ).fetchall(), f"{table} must be indexed"


@pytest.mark.asyncio
async def test_reopen_rejects_checksum_tamper(tmp_path: Path) -> None:
    path = tmp_path / "checksum-tamper.db"
    async with _opened_store(path):
        pass

    with _sqlite(path) as connection:
        schema = _table_columns(connection)
        ledger = _find_table(schema, required={"checksum"})
        connection.execute(
            f"UPDATE {_quote_identifier(ledger)} SET checksum = ?",
            ("sha256:" + "0" * 64,),
        )
        connection.commit()

    reopened = _new_store(path)
    with pytest.raises((RuntimeError, ValueError)):
        await reopened.init()
    await reopened.close()


@pytest.mark.asyncio
async def test_reopen_rejects_structural_index_tamper(tmp_path: Path) -> None:
    path = tmp_path / "index-tamper.db"
    async with _opened_store(path):
        pass

    with _sqlite(path) as connection:
        indexes = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND sql IS NOT NULL ORDER BY name"
        ).fetchall()
        assert indexes, "schema v1 requires owned explicit indexes"
        connection.execute(f"DROP INDEX {_quote_identifier(str(indexes[0]['name']))}")
        connection.commit()

    reopened = _new_store(path)
    with pytest.raises((RuntimeError, ValueError)):
        await reopened.init()
    await reopened.close()


@pytest.mark.asyncio
async def test_reopen_rejects_index_uniqueness_semantic_tamper(tmp_path: Path) -> None:
    path = tmp_path / "index-uniqueness-tamper.db"
    async with _opened_store(path):
        pass

    with _sqlite(path) as connection:
        explicit_indexes = connection.execute(
            "SELECT name, tbl_name FROM sqlite_master "
            "WHERE type = 'index' AND sql IS NOT NULL ORDER BY name"
        ).fetchall()
        selected: tuple[str, str, tuple[str, ...]] | None = None
        for index in explicit_indexes:
            index_name = str(index["name"])
            table_name = str(index["tbl_name"])
            metadata = next(
                row
                for row in connection.execute(
                    f"PRAGMA index_list({_quote_identifier(table_name)})"
                ).fetchall()
                if str(row["name"]) == index_name
            )
            columns = tuple(
                str(row["name"])
                for row in connection.execute(
                    f"PRAGMA index_info({_quote_identifier(index_name)})"
                ).fetchall()
            )
            if int(metadata["unique"]) == 0 and columns:
                selected = (index_name, table_name, columns)
                break
        assert selected is not None, "schema v1 requires a non-unique explicit index"
        index_name, table_name, columns = selected

        connection.execute(f"DROP INDEX {_quote_identifier(index_name)}")
        quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
        connection.execute(
            f"CREATE UNIQUE INDEX {_quote_identifier(index_name)} "
            f"ON {_quote_identifier(table_name)} ({quoted_columns})"
        )
        connection.commit()

    reopened = _new_store(path)
    with pytest.raises((RuntimeError, ValueError)):
        await reopened.init()
    await reopened.close()


@pytest.mark.parametrize("object_kind", ["table", "view"])
@pytest.mark.asyncio
async def test_reopen_rejects_unowned_schema_objects(
    tmp_path: Path,
    object_kind: str,
) -> None:
    path = tmp_path / f"unowned-{object_kind}.db"
    async with _opened_store(path):
        pass

    with _sqlite(path) as connection:
        if object_kind == "table":
            connection.execute("CREATE TABLE injected_unowned_object (value TEXT)")
        else:
            connection.execute(
                "CREATE VIEW injected_unowned_object AS SELECT 'unexpected' AS value"
            )
        connection.commit()

    reopened = _new_store(path)
    with pytest.raises((RuntimeError, ValueError)):
        await reopened.init()
    await reopened.close()


@pytest.mark.asyncio
async def test_raised_candidate_append_rolls_back_observation_and_releases_lock(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    path = tmp_path / "candidate-fault.db"
    candidate = _candidate(contracts)
    store = _new_store(path)
    await store.init()
    try:
        with _sqlite(path) as connection:
            schema = _table_columns(connection)
            candidate_table = _find_table(
                schema,
                required={"candidate_id", "candidate_sha256", "observation_id"},
            )
            connection.execute(
                f"CREATE TRIGGER injected_candidate_abort BEFORE INSERT ON "
                f"{_quote_identifier(candidate_table)} BEGIN "
                "SELECT RAISE(ABORT, 'injected candidate append failure'); END"
            )
            connection.commit()

        with pytest.raises(Exception, match="injected candidate append failure"):
            await store.append_candidate(candidate)
        assert await store.get_observation(candidate.observation.observation_id) is None
        assert await store.get_candidate(candidate.candidate_id) is None
    finally:
        await store.close()

    with _sqlite(path) as probe:
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()


@pytest.mark.asyncio
async def test_same_store_readers_wait_until_failed_candidate_append_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = _contracts()
    path = tmp_path / "candidate-read-isolation.db"
    candidate = _candidate(contracts)
    candidate_insert_entered = threading.Event()
    release_candidate_insert = threading.Event()
    real_connect = sqlite3.connect

    def connect_with_candidate_gate(
        database: Any,
        *args: Any,
        **kwargs: Any,
    ) -> sqlite3.Connection:
        connection = real_connect(database, *args, **kwargs)

        def candidate_insert_gate() -> None:
            candidate_insert_entered.set()
            if not release_candidate_insert.wait(timeout=2.0):
                raise sqlite3.OperationalError("candidate insert gate timed out")
            raise sqlite3.IntegrityError("injected candidate append rollback")

        connection.create_function("test_candidate_insert_gate", 0, candidate_insert_gate)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect_with_candidate_gate)
    store = _new_store(path)
    append_task: asyncio.Task[Any] | None = None
    reader_tasks: tuple[asyncio.Task[Any], ...] = ()
    await store.init()
    try:
        with _sqlite(path) as connection:
            schema = _table_columns(connection)
            candidate_table = _find_table(
                schema,
                required={"candidate_id", "candidate_sha256", "observation_id"},
            )
            connection.execute(
                "CREATE TRIGGER injected_candidate_gate BEFORE INSERT ON "
                f"{_quote_identifier(candidate_table)} BEGIN "
                "SELECT test_candidate_insert_gate(); END"
            )
            connection.commit()

        append_task = asyncio.create_task(store.append_candidate(candidate))
        entered = await asyncio.wait_for(
            asyncio.to_thread(candidate_insert_entered.wait, 1.0),
            timeout=1.5,
        )
        assert entered, "candidate INSERT trigger was not reached"

        reader_tasks = (
            asyncio.create_task(
                store.get_observation(candidate.observation.observation_id)
            ),
            asyncio.create_task(store.get_candidate(candidate.candidate_id)),
        )
        await asyncio.sleep(0)
        assert all(not task.done() for task in reader_tasks), (
            "same-store reads must wait while candidate append is uncommitted"
        )

        release_candidate_insert.set()
        with pytest.raises(Exception, match="user-defined function raised exception"):
            await asyncio.wait_for(append_task, timeout=1.0)
        read_results = await asyncio.wait_for(
            asyncio.gather(*reader_tasks),
            timeout=1.0,
        )
        assert read_results == [None, None]
    finally:
        release_candidate_insert.set()
        pending_tasks = tuple(
            task
            for task in (append_task, *reader_tasks)
            if task is not None and not task.done()
        )
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        await store.close()


@pytest.mark.asyncio
async def test_close_waits_for_inflight_append_then_rejects_post_close_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = _contracts()
    path = tmp_path / "close-during-append.db"
    candidate = _candidate(contracts, item="close-race")
    candidate_insert_entered = threading.Event()
    release_candidate_insert = threading.Event()
    real_connect = sqlite3.connect

    def connect_with_candidate_gate(
        database: Any,
        *args: Any,
        **kwargs: Any,
    ) -> sqlite3.Connection:
        connection = real_connect(database, *args, **kwargs)

        def candidate_insert_gate() -> None:
            candidate_insert_entered.set()
            if not release_candidate_insert.wait(timeout=2.0):
                raise sqlite3.OperationalError("candidate close gate timed out")

        connection.create_function("test_candidate_close_gate", 0, candidate_insert_gate)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect_with_candidate_gate)
    store = _new_store(path)
    append_task: asyncio.Task[Any] | None = None
    close_task: asyncio.Task[Any] | None = None
    await store.init()
    try:
        with _sqlite(path) as connection:
            schema = _table_columns(connection)
            candidate_table = _find_table(
                schema,
                required={"candidate_id", "candidate_sha256", "observation_id"},
            )
            connection.execute(
                "CREATE TRIGGER injected_candidate_close_gate BEFORE INSERT ON "
                f"{_quote_identifier(candidate_table)} BEGIN "
                "SELECT test_candidate_close_gate(); END"
            )
            connection.commit()

        append_task = asyncio.create_task(store.append_candidate(candidate))
        entered = await asyncio.wait_for(
            asyncio.to_thread(candidate_insert_entered.wait, 1.0),
            timeout=1.5,
        )
        assert entered, "candidate INSERT close gate was not reached"

        close_task = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not close_task.done(), "close must wait for the active append"

        release_candidate_insert.set()
        results = await asyncio.wait_for(
            asyncio.gather(append_task, close_task, return_exceptions=True),
            timeout=2.0,
        )
        assert results[0] == candidate
        assert results[1] is None

        with pytest.raises((RuntimeError, ValueError)):
            await store.append_candidate(_candidate(contracts, item="after-close"))
    finally:
        release_candidate_insert.set()
        pending_tasks = tuple(
            task
            for task in (append_task, close_task)
            if task is not None and not task.done()
        )
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        await store.close()

    with _sqlite(path) as connection:
        connection.execute("DROP TRIGGER injected_candidate_close_gate")
        connection.commit()

    async with _opened_store(path) as reopened:
        assert await reopened.get_observation(
            candidate.observation.observation_id
        ) == candidate.observation
        assert await reopened.get_candidate(candidate.candidate_id) == candidate


@pytest.mark.asyncio
async def test_repeated_cancellation_after_commit_stays_attached_and_is_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = _contracts()
    path = tmp_path / "cancel-after-commit.db"
    candidate = _candidate(contracts, item="cancel-after-commit")
    commit_completed = asyncio.Event()
    release_commit_wrapper = asyncio.Event()
    store = _new_store(path)
    append_task: asyncio.Task[Any] | None = None
    await store.init()
    try:
        connection_candidates = tuple(
            value
            for value in vars(store).values()
            if callable(getattr(value, "commit", None))
            and callable(getattr(value, "rollback", None))
        )
        assert len(connection_candidates) == 1, (
            "initialized store must own exactly one persistent SQLite connection"
        )
        connection = connection_candidates[0]
        real_commit = connection.commit

        async def commit_then_wait() -> None:
            await real_commit()
            commit_completed.set()
            await release_commit_wrapper.wait()

        monkeypatch.setattr(connection, "commit", commit_then_wait)
        append_task = asyncio.create_task(store.append_candidate(candidate))
        await asyncio.wait_for(commit_completed.wait(), timeout=1.0)

        append_task.cancel()
        await asyncio.sleep(0)
        append_task.cancel()
        await asyncio.sleep(0)
        assert not append_task.done(), (
            "append must remain attached to a commit that has completed durably"
        )

        release_commit_wrapper.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(append_task, timeout=1.0)
    finally:
        release_commit_wrapper.set()
        if append_task is not None:
            if not append_task.done():
                append_task.cancel()
            await asyncio.gather(append_task, return_exceptions=True)
        await store.close()

    async with _opened_store(path) as reopened:
        assert await reopened.get_observation(
            candidate.observation.observation_id
        ) == candidate.observation
        assert await reopened.get_candidate(candidate.candidate_id) == candidate
        await reopened.append_candidate(candidate)
        assert await reopened.get_candidate(candidate.candidate_id) == candidate

    with _sqlite(path) as connection:
        schema = _table_columns(connection)
        observation_table = _find_table(
            schema,
            required={"observation_id", "observation_sha256", "payload_json"},
        )
        candidate_table = _find_table(
            schema,
            required={"candidate_id", "candidate_sha256", "observation_id"},
        )
        assert connection.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(observation_table)} "
            "WHERE observation_id = ?",
            (candidate.observation.observation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(candidate_table)} "
            "WHERE candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_raised_promotion_append_leaves_no_event_and_releases_lock(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    path = tmp_path / "event-fault.db"
    candidate = _candidate(contracts)
    event = _promotion_event(contracts, candidate, "promotion_approved")
    store = _new_store(path)
    await store.init()
    try:
        await store.append_candidate(candidate)
        with _sqlite(path) as connection:
            schema = _table_columns(connection)
            event_table = _find_table(schema, required={"event_id", "candidate_id"})
            connection.execute(
                f"CREATE TRIGGER injected_event_abort BEFORE INSERT ON "
                f"{_quote_identifier(event_table)} BEGIN "
                "SELECT RAISE(ABORT, 'injected promotion append failure'); END"
            )
            connection.commit()

        with pytest.raises(Exception, match="injected promotion append failure"):
            await store.append_promotion_event(event)
        assert await store.list_promotion_events(candidate.candidate_id) == ()
    finally:
        await store.close()

    with _sqlite(path) as probe:
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()


@pytest.mark.asyncio
async def test_cancelled_append_under_write_contention_leaves_no_partial_rows_or_lock(
    tmp_path: Path,
) -> None:
    contracts = _contracts()
    path = tmp_path / "cancelled-append.db"
    candidate = _candidate(contracts)
    store = _new_store(path)
    await store.init()
    blocker = sqlite3.connect(path, timeout=1.0)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        task = asyncio.create_task(store.append_candidate(candidate))
        await asyncio.sleep(0.02)
        assert not task.done(), "append should be waiting on the external writer"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        blocker.rollback()
        assert await store.get_observation(candidate.observation.observation_id) is None
        assert await store.get_candidate(candidate.candidate_id) is None
    finally:
        blocker.close()
        await store.close()

    with _sqlite(path) as probe:
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()
