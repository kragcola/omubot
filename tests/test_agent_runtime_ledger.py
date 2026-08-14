"""Behavior contracts for the dark Agent Runtime SQLite ledger."""

import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime

import aiosqlite
import pytest

import services.agent_runtime.ledger as ledger_module
from services.storage.migrations import MigrationRunner


async def test_ledger_is_explicitly_initialized_and_appends_run_created_event(
    tmp_path,
) -> None:
    ledger_type = getattr(ledger_module, "AgentRuntimeLedger", None)
    assert ledger_type is not None

    db_path = tmp_path / "agent-runtime.db"
    ledger = ledger_type(db_path)
    assert not db_path.exists()

    await ledger.init()
    run = await ledger.create_run(
        run_id="run-1",
        trigger_type="message",
        trigger_ref="qq:message:100",
        principal_kind="user",
        principal_id="user-1",
        session_id="group:200",
        group_id="200",
        registry_generation=3,
        metadata={"source": "test"},
    )
    events = await ledger.list_events(run_id="run-1")
    await ledger.close()

    assert run.run_id == "run-1"
    assert run.status == "queued"
    assert run.registry_generation == 3
    assert len(events) == 1
    assert events[0].entity_kind == "run"
    assert events[0].event_type == "created"
    assert events[0].from_status == ""
    assert events[0].to_status == "queued"

    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "agent_runs",
            "agent_tool_calls",
            "agent_runtime_events",
        } <= tables


async def test_v2_schema_snapshots_default_concurrency_mode(tmp_path) -> None:
    db_path = tmp_path / "agent-runtime.db"
    ledger = ledger_module.AgentRuntimeLedger(db_path)
    await ledger.init()
    await ledger.create_run(
        run_id="run-v2-concurrency",
        trigger_type="recovery",
        trigger_ref="runtime:v2-concurrency",
        principal_kind="service",
        principal_id="runtime",
    )
    call = await ledger.create_tool_call(
        call_id="call-v2-concurrency",
        run_id="run-v2-concurrency",
        step_id="step-v2-concurrency",
        tool_name="read_tool",
        tool_version="1",
        owner="test",
        effect="read",
        principal_kind="service",
        principal_id="runtime",
        args_digest="sha256:v2-concurrency",
        idempotency_mode="not_needed",
    )
    await ledger.close()

    assert getattr(call, "concurrency_mode", None) == "global_serial"
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = {
            row[1]: row
            for row in db.execute("PRAGMA table_info(agent_tool_calls)").fetchall()
        }
        assert columns["concurrency_mode"][2] == "TEXT"
        assert columns["concurrency_mode"][3] == 1
        assert columns["concurrency_mode"][4] == "'global_serial'"
        indexes = {
            row[1]
            for row in db.execute("PRAGMA index_list(agent_tool_calls)").fetchall()
        }
        assert "idx_agent_calls_concurrency" in indexes


async def test_existing_v1_database_upgrades_to_v2_without_losing_calls(
    tmp_path,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    await MigrationRunner(db_path=db_path, db_id="agent_runtime").ensure(
        (ledger_module._MIGRATION_V1,)
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT INTO agent_runs (
                run_id, trigger_type, trigger_ref, principal_kind,
                principal_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-v1-upgrade",
                "recovery",
                "runtime:v1-upgrade",
                "service",
                "runtime",
                "queued",
                "2026-07-21T00:00:00+00:00",
                "2026-07-21T00:00:00+00:00",
            ),
        )
        db.execute(
            """
            INSERT INTO agent_tool_calls (
                call_id, run_id, step_id, tool_name, tool_version, owner,
                effect, principal_kind, principal_id, args_digest,
                idempotency_mode, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "call-v1-upgrade",
                "run-v1-upgrade",
                "step-v1-upgrade",
                "read_tool",
                "1",
                "test",
                "read",
                "service",
                "runtime",
                "sha256:v1-upgrade",
                "not_needed",
                "proposed",
                "2026-07-21T00:00:00+00:00",
                "2026-07-21T00:00:00+00:00",
            ),
        )

    ledger = ledger_module.AgentRuntimeLedger(db_path)
    await ledger.init()
    call = await ledger.get_tool_call("call-v1-upgrade")
    await ledger.close()

    assert call is not None
    assert call.concurrency_mode == "global_serial"
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"


async def test_reconciliation_is_append_only_idempotent_and_run_atomic(
    tmp_path,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-reconcile",
        trigger_type="recovery",
        trigger_ref="runtime:reconcile",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.transition_run(
        "run-reconcile",
        to_status="running",
        actor="coordinator",
    )

    async def create_unknown(call_id: str) -> None:
        await ledger.create_tool_call(
            call_id=call_id,
            run_id="run-reconcile",
            step_id=call_id,
            tool_name="external_effect",
            tool_version="1",
            owner="test",
            effect="external_irreversible",
            principal_kind="service",
            principal_id="runtime",
            target_ref=f"external:{call_id}",
            args_digest=f"sha256:{call_id}",
            idempotency_mode="reconcile_only",
            concurrency_mode="keyed_serial",
            concurrency_key=f"external:{call_id}",
        )
        await ledger.transition_tool_call(
            call_id,
            to_status="ready",
            actor="policy",
        )
        await ledger.claim_tool_call(
            call_id,
            lease_owner="worker",
            lease_until="2099-01-01T00:00:00+00:00",
            actor="worker",
        )
        await ledger.transition_tool_call(
            call_id,
            to_status="dispatching",
            actor="worker",
            expected_lease_owner="worker",
        )
        await ledger.transition_tool_call(
            call_id,
            to_status="unknown",
            actor="worker",
            error_code="provider_unknown",
            expected_lease_owner="worker",
        )

    await create_unknown("call-reconcile-a")
    await create_unknown("call-reconcile-b")
    await ledger.transition_run(
        "run-reconcile",
        to_status="waiting_external",
        actor="coordinator",
    )
    reconcile = ledger.record_tool_reconciliation

    first = await reconcile(
        "call-reconcile-a",
        decision="confirmed_succeeded",
        actor="operator:1",
        adapter_id="manual-test",
        evidence_ref="operator:ticket:1001",
        note_digest="sha256:" + "a" * 64,
        external_id="provider-1001",
    )
    same = await reconcile(
        "call-reconcile-a",
        decision="confirmed_succeeded",
        actor="operator:1",
        adapter_id="manual-test",
        evidence_ref="operator:ticket:1001",
        note_digest="sha256:" + "a" * 64,
        external_id="provider-1001",
    )
    run_after_first = await ledger.get_run("run-reconcile")
    call_after_first = await ledger.get_tool_call("call-reconcile-a")

    with pytest.raises(ValueError, match="different reconciliation"):
        await reconcile(
            "call-reconcile-a",
            decision="confirmed_not_applied",
            actor="operator:1",
            adapter_id="manual-test",
            evidence_ref="operator:ticket:1002",
            note_digest="sha256:" + "b" * 64,
        )

    second = await reconcile(
        "call-reconcile-b",
        decision="confirmed_not_applied",
        actor="operator:1",
        adapter_id="manual-test",
        evidence_ref="operator:ticket:1002",
        note_digest="sha256:" + "b" * 64,
    )
    run_after_second = await ledger.get_run("run-reconcile")
    events = await ledger.list_events(run_id="run-reconcile")
    await ledger.close()

    assert first.call_id == "call-reconcile-a"
    assert first.decision == "confirmed_succeeded"
    assert first.external_id == "provider-1001"
    assert same == first
    assert second.decision == "confirmed_not_applied"
    assert call_after_first is not None and call_after_first.status == "unknown"
    assert run_after_first is not None
    assert run_after_first.status == "waiting_external"
    assert run_after_second is not None and run_after_second.status == "running"
    reconciliation_events = [
        event for event in events
        if event.event_type == "reconciliation_resolved"
    ]
    assert len(reconciliation_events) == 2


async def test_concurrent_reconciliation_allows_only_one_truth(
    tmp_path,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    first = ledger_module.AgentRuntimeLedger(db_path)
    second = ledger_module.AgentRuntimeLedger(db_path)
    await first.init()
    await second.init()
    await first.create_run(
        run_id="run-reconcile-race",
        trigger_type="recovery",
        trigger_ref="runtime:reconcile-race",
        principal_kind="service",
        principal_id="runtime",
    )
    await first.transition_run(
        "run-reconcile-race",
        to_status="running",
        actor="runtime",
    )
    await first.create_tool_call(
        call_id="call-reconcile-race",
        run_id="run-reconcile-race",
        step_id="call-reconcile-race",
        tool_name="external_effect",
        tool_version="1",
        owner="test",
        effect="external_irreversible",
        principal_kind="service",
        principal_id="runtime",
        target_ref="external:race",
        args_digest="sha256:race",
        idempotency_mode="reconcile_only",
        concurrency_mode="keyed_serial",
        concurrency_key="external:race",
    )
    await first.transition_tool_call(
        "call-reconcile-race",
        to_status="ready",
        actor="policy",
    )
    await first.claim_tool_call(
        "call-reconcile-race",
        lease_owner="worker",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="worker",
    )
    await first.transition_tool_call(
        "call-reconcile-race",
        to_status="dispatching",
        actor="worker",
        expected_lease_owner="worker",
    )
    await first.transition_tool_call(
        "call-reconcile-race",
        to_status="unknown",
        actor="worker",
        expected_lease_owner="worker",
    )
    await first.transition_run(
        "run-reconcile-race",
        to_status="waiting_external",
        actor="runtime",
    )

    outcomes = await asyncio.gather(
        first.record_tool_reconciliation(
            "call-reconcile-race",
            decision="confirmed_succeeded",
            actor="operator:race",
            adapter_id="race-a",
            evidence_ref="operator:race:a",
            note_digest="sha256:" + "a" * 64,
        ),
        second.record_tool_reconciliation(
            "call-reconcile-race",
            decision="confirmed_not_applied",
            actor="operator:race",
            adapter_id="race-b",
            evidence_ref="operator:race:b",
            note_digest="sha256:" + "b" * 64,
        ),
        return_exceptions=True,
    )
    events = await first.list_events(run_id="run-reconcile-race")
    await first.close()
    await second.close()

    records = [
        outcome
        for outcome in outcomes
        if isinstance(outcome, ledger_module.ToolReconciliationRecord)
    ]
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(records) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ValueError)
    assert "different reconciliation" in str(failures[0])
    assert [event.event_type for event in events].count(
        "reconciliation_resolved"
    ) == 1


@pytest.mark.parametrize(
    ("concurrency_mode", "concurrency_key"),
    [
        ("global_serial", ""),
        ("keyed_serial", "resource:same"),
    ],
)
async def test_claim_atomically_rejects_concurrency_conflict_across_connections(
    tmp_path,
    concurrency_mode: str,
    concurrency_key: str,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    first_ledger = ledger_module.AgentRuntimeLedger(db_path)
    second_ledger = ledger_module.AgentRuntimeLedger(db_path)
    await first_ledger.init()
    await second_ledger.init()
    await first_ledger.create_run(
        run_id="run-concurrency-claim",
        trigger_type="recovery",
        trigger_ref="runtime:concurrency-claim",
        principal_kind="service",
        principal_id="runtime",
    )
    for call_id in ("call-concurrency-a", "call-concurrency-b"):
        await first_ledger.create_tool_call(
            call_id=call_id,
            run_id="run-concurrency-claim",
            step_id=call_id,
            tool_name="concurrent_read",
            tool_version="1",
            owner="test",
            effect="read",
            principal_kind="service",
            principal_id="runtime",
            args_digest=f"sha256:{call_id}",
            idempotency_mode="not_needed",
            concurrency_mode=concurrency_mode,
            concurrency_key=concurrency_key,
        )
        await first_ledger.transition_tool_call(
            call_id,
            to_status="ready",
            actor="policy",
        )

    outcomes = await asyncio.gather(
        first_ledger.claim_tool_call(
            "call-concurrency-a",
            lease_owner="worker-a",
            lease_until="2099-01-01T00:00:00+00:00",
            actor="executor",
        ),
        second_ledger.claim_tool_call(
            "call-concurrency-b",
            lease_owner="worker-b",
            lease_until="2099-01-01T00:00:00+00:00",
            actor="executor",
        ),
        return_exceptions=True,
    )
    records = [
        await first_ledger.get_tool_call("call-concurrency-a"),
        await first_ledger.get_tool_call("call-concurrency-b"),
    ]
    await second_ledger.close()
    await first_ledger.close()

    assert sum(isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sorted(record.status for record in records if record) == [
        "claimed",
        "ready",
    ]
    assert sum(record.attempt_count for record in records if record) == 1


async def test_parallel_claims_can_succeed_across_connections(tmp_path) -> None:
    db_path = tmp_path / "agent-runtime.db"
    first_ledger = ledger_module.AgentRuntimeLedger(db_path)
    second_ledger = ledger_module.AgentRuntimeLedger(db_path)
    await first_ledger.init()
    await second_ledger.init()
    await first_ledger.create_run(
        run_id="run-parallel-claim",
        trigger_type="recovery",
        trigger_ref="runtime:parallel-claim",
        principal_kind="service",
        principal_id="runtime",
    )
    for call_id in ("call-parallel-a", "call-parallel-b"):
        await first_ledger.create_tool_call(
            call_id=call_id,
            run_id="run-parallel-claim",
            step_id=call_id,
            tool_name="parallel_read",
            tool_version="1",
            owner="test",
            effect="read",
            principal_kind="service",
            principal_id="runtime",
            args_digest=f"sha256:{call_id}",
            idempotency_mode="not_needed",
            concurrency_mode="parallel",
        )
        await first_ledger.transition_tool_call(
            call_id,
            to_status="ready",
            actor="policy",
        )

    outcomes = await asyncio.gather(
        first_ledger.claim_tool_call(
            "call-parallel-a",
            lease_owner="worker-a",
            lease_until="2099-01-01T00:00:00+00:00",
            actor="executor",
        ),
        second_ledger.claim_tool_call(
            "call-parallel-b",
            lease_owner="worker-b",
            lease_until="2099-01-01T00:00:00+00:00",
            actor="executor",
        ),
    )
    await second_ledger.close()
    await first_ledger.close()

    assert [outcome.status for outcome in outcomes] == ["claimed", "claimed"]


async def test_live_lease_recovery_requeues_claimed_and_marks_dispatch_unknown(
    tmp_path,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    recover_expired = ledger.recover_expired_tool_calls
    await ledger.create_run(
        run_id="run-live-lease-recovery",
        trigger_type="recovery",
        trigger_ref="runtime:live-lease-recovery",
        principal_kind="service",
        principal_id="runtime",
    )

    async def create_claimed(call_id: str, lease_until: str) -> None:
        await ledger.create_tool_call(
            call_id=call_id,
            run_id="run-live-lease-recovery",
            step_id=call_id,
            tool_name="parallel_read",
            tool_version="1",
            owner="test",
            effect="read",
            principal_kind="service",
            principal_id="runtime",
            args_digest=f"sha256:{call_id}",
            idempotency_mode="not_needed",
            concurrency_mode="parallel",
        )
        await ledger.transition_tool_call(
            call_id,
            to_status="ready",
            actor="policy",
        )
        await ledger.claim_tool_call(
            call_id,
            lease_owner="worker",
            lease_until=lease_until,
            actor="executor",
        )

    await create_claimed("call-expired-claimed", "2020-01-01T00:00:00+00:00")
    await create_claimed("call-expired-dispatch", "2020-01-01T00:00:00+00:00")
    await ledger.transition_tool_call(
        "call-expired-dispatch",
        to_status="dispatching",
        actor="executor",
        expected_lease_owner="worker",
    )
    await create_claimed("call-live-claimed", "2099-01-01T00:00:00+00:00")

    recovered = await recover_expired(
        now=datetime(2026, 7, 21, tzinfo=UTC),
        actor="lease-reaper",
    )
    second_pass = await recover_expired(
        now=datetime(2026, 7, 21, tzinfo=UTC),
        actor="lease-reaper",
    )
    expired_claimed = await ledger.get_tool_call("call-expired-claimed")
    expired_dispatch = await ledger.get_tool_call("call-expired-dispatch")
    live_claimed = await ledger.get_tool_call("call-live-claimed")
    await ledger.close()

    assert {record.call_id for record in recovered} == {
        "call-expired-claimed",
        "call-expired-dispatch",
    }
    assert second_pass == []
    assert expired_claimed is not None
    assert expired_dispatch is not None
    assert live_claimed is not None
    assert expired_claimed.status == "ready"
    assert expired_claimed.error_code == "lease_expired_before_dispatch"
    assert expired_dispatch.status == "unknown"
    assert expired_dispatch.error_code == "lease_expired_post_dispatch_unknown"
    assert live_claimed.status == "claimed"


@pytest.mark.parametrize(
    "lease_until",
    ["not-a-time", "2026-07-21T00:00:00"],
)
async def test_claim_rejects_invalid_or_naive_lease_deadline(
    tmp_path,
    lease_until: str,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-invalid-lease",
        trigger_type="recovery",
        trigger_ref="runtime:invalid-lease",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.create_tool_call(
        call_id="call-invalid-lease",
        run_id="run-invalid-lease",
        step_id="call-invalid-lease",
        tool_name="read_tool",
        tool_version="1",
        owner="test",
        effect="read",
        principal_kind="service",
        principal_id="runtime",
        args_digest="sha256:invalid-lease",
        idempotency_mode="not_needed",
    )
    await ledger.transition_tool_call(
        "call-invalid-lease",
        to_status="ready",
        actor="policy",
    )

    with pytest.raises(ValueError, match="lease_until must be timezone-aware ISO-8601"):
        await ledger.claim_tool_call(
            "call-invalid-lease",
            lease_owner="worker",
            lease_until=lease_until,
            actor="executor",
        )
    call = await ledger.get_tool_call("call-invalid-lease")
    await ledger.close()

    assert call is not None and call.status == "ready"


async def test_reclaimed_call_rejects_stale_lease_owner_transition(
    tmp_path,
) -> None:
    lease_error_type = getattr(ledger_module, "LeaseOwnershipError", RuntimeError)
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-lease-fence",
        trigger_type="recovery",
        trigger_ref="runtime:lease-fence",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.create_tool_call(
        call_id="call-lease-fence",
        run_id="run-lease-fence",
        step_id="call-lease-fence",
        tool_name="parallel_read",
        tool_version="1",
        owner="test",
        effect="read",
        principal_kind="service",
        principal_id="runtime",
        args_digest="sha256:lease-fence",
        idempotency_mode="not_needed",
        concurrency_mode="parallel",
    )
    await ledger.transition_tool_call(
        "call-lease-fence",
        to_status="ready",
        actor="policy",
    )
    await ledger.claim_tool_call(
        "call-lease-fence",
        lease_owner="worker-a",
        lease_until="2020-01-01T00:00:00+00:00",
        actor="executor",
    )
    await ledger.recover_expired_tool_calls(
        now=datetime(2026, 7, 21, tzinfo=UTC),
        actor="lease-reaper",
    )
    await ledger.claim_tool_call(
        "call-lease-fence",
        lease_owner="worker-b",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="executor",
    )

    try:
        await ledger.transition_tool_call(
            "call-lease-fence",
            to_status="dispatching",
            actor="executor",
            expected_lease_owner="worker-a",
        )
        stale_error = None
    except Exception as exc:
        stale_error = exc
    call = await ledger.get_tool_call("call-lease-fence")
    await ledger.close()

    assert isinstance(stale_error, lease_error_type)
    assert call is not None and call.status == "claimed"
    assert call.lease_owner == "worker-b"


async def test_reclaimed_call_rejects_stale_worker_cancellation(tmp_path) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-cancel-fence",
        trigger_type="recovery",
        trigger_ref="runtime:cancel-fence",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.create_tool_call(
        call_id="call-cancel-fence",
        run_id="run-cancel-fence",
        step_id="call-cancel-fence",
        tool_name="parallel_read",
        tool_version="1",
        owner="test",
        effect="read",
        principal_kind="service",
        principal_id="runtime",
        args_digest="sha256:cancel-fence",
        idempotency_mode="not_needed",
        concurrency_mode="parallel",
    )
    await ledger.transition_tool_call(
        "call-cancel-fence",
        to_status="ready",
        actor="policy",
    )
    await ledger.claim_tool_call(
        "call-cancel-fence",
        lease_owner="worker-a",
        lease_until="2020-01-01T00:00:00+00:00",
        actor="executor",
    )
    await ledger.recover_expired_tool_calls(
        now=datetime(2026, 7, 21, tzinfo=UTC),
        actor="lease-reaper",
    )
    await ledger.claim_tool_call(
        "call-cancel-fence",
        lease_owner="worker-b",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="executor",
    )

    try:
        await ledger.cancel_tool_call(
            "call-cancel-fence",
            actor="worker-a",
            reason="executor_cancelled",
            expected_lease_owner="worker-a",
        )
        stale_error = None
    except Exception as exc:
        stale_error = exc
    call = await ledger.get_tool_call("call-cancel-fence")
    await ledger.close()

    assert isinstance(stale_error, ledger_module.LeaseOwnershipError)
    assert call is not None and call.status == "claimed"
    assert call.lease_owner == "worker-b"


async def test_approval_digest_is_atomically_attached_to_pending_call(
    tmp_path,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    record_approval = ledger.record_tool_call_approval
    await ledger.create_run(
        run_id="run-approval-record",
        trigger_type="message",
        trigger_ref="qq:message:approval-record",
        principal_kind="user",
        principal_id="approval-user",
    )
    await ledger.create_tool_call(
        call_id="call-approval-record",
        run_id="run-approval-record",
        step_id="call-approval-record",
        tool_name="external_write",
        tool_version="1",
        owner="test",
        effect="external_irreversible",
        principal_kind="user",
        principal_id="approval-user",
        target_ref="external:approval-user",
        args_digest="sha256:approval-args",
        idempotency_mode="reconcile_only",
    )
    await ledger.transition_tool_call(
        "call-approval-record",
        to_status="approval_pending",
        actor="policy",
    )

    approved = await record_approval(
        "call-approval-record",
        approval_ref_digest="sha256:approval-grant",
        actor="approver",
    )
    events = await ledger.list_events(run_id="run-approval-record")
    await ledger.close()

    assert approved.status == "approval_pending"
    assert approved.approval_ref_digest == "sha256:approval-grant"
    assert events[-1].event_type == "approval_granted"
    assert events[-1].from_status == "approval_pending"
    assert events[-1].to_status == "approval_pending"
    assert events[-1].metadata == {
        "approval_ref_digest": "sha256:approval-grant"
    }


async def test_run_transitions_are_append_only_and_terminal_states_fail_closed(
    tmp_path,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-state",
        trigger_type="tick",
        trigger_ref="tick:1",
        principal_kind="service",
        principal_id="scheduler",
    )
    running = await ledger.transition_run(
        "run-state",
        to_status="running",
        actor="coordinator",
        metadata={"step": 1},
    )
    succeeded = await ledger.transition_run(
        "run-state",
        to_status="succeeded",
        actor="coordinator",
    )
    with pytest.raises(ValueError, match="illegal run transition"):
        await ledger.transition_run(
            "run-state",
            to_status="running",
            actor="coordinator",
        )

    events = await ledger.list_events(run_id="run-state")
    await ledger.close()

    assert running.status == "running"
    assert succeeded.status == "succeeded"
    assert succeeded.terminal_at
    assert [(event.from_status, event.to_status) for event in events] == [
        ("", "queued"),
        ("queued", "running"),
        ("running", "succeeded"),
    ]


async def test_tool_call_transitions_preserve_policy_snapshot_and_events(
    tmp_path,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-tool",
        trigger_type="message",
        trigger_ref="qq:message:200",
        principal_kind="user",
        principal_id="user-2",
    )

    call = await ledger.create_tool_call(
        call_id="call-1",
        run_id="run-tool",
        step_id="step-1",
        tool_name="qzone_publish",
        tool_version="2",
        owner="qzone_journal",
        effect="external_irreversible",
        principal_kind="user",
        principal_id="user-2",
        target_ref="qzone:user-2",
        args_digest="sha256:args",
        idempotency_mode="reconcile_only",
        idempotency_key_digest="sha256:key",
        approval_ref_digest="sha256:approval",
        concurrency_key="qzone:user-2",
        metadata={"policy_version": "p1"},
    )
    await ledger.transition_tool_call(
        "call-1",
        to_status="approval_pending",
        actor="policy",
    )
    await ledger.transition_tool_call(
        "call-1",
        to_status="ready",
        actor="approval",
    )
    claimed = await ledger.claim_tool_call(
        "call-1",
        lease_owner="worker-1",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="executor",
    )
    await ledger.transition_tool_call(
        "call-1",
        to_status="dispatching",
        actor="executor",
        expected_lease_owner="worker-1",
    )
    succeeded = await ledger.transition_tool_call(
        "call-1",
        to_status="succeeded",
        actor="executor",
        safe_result={"published": True},
        external_id="post-1",
        expected_lease_owner="worker-1",
    )

    with pytest.raises(ValueError, match="illegal tool call transition"):
        await ledger.transition_tool_call(
            "call-1",
            to_status="ready",
            actor="executor",
        )

    events = await ledger.list_events(run_id="run-tool")
    await ledger.close()

    assert call.status == "proposed"
    assert call.effect == "external_irreversible"
    assert call.idempotency_mode == "reconcile_only"
    assert call.approval_ref_digest == "sha256:approval"
    assert claimed.attempt_count == 1
    assert claimed.lease_owner == "worker-1"
    assert succeeded.status == "succeeded"
    assert succeeded.safe_result == {"published": True}
    assert succeeded.external_id == "post-1"
    assert succeeded.terminal_at
    assert [
        (event.entity_kind, event.from_status, event.to_status)
        for event in events
    ] == [
        ("run", "", "queued"),
        ("tool_call", "", "proposed"),
        ("tool_call", "proposed", "approval_pending"),
        ("tool_call", "approval_pending", "ready"),
        ("tool_call", "ready", "claimed"),
        ("tool_call", "claimed", "dispatching"),
        ("tool_call", "dispatching", "succeeded"),
    ]


async def test_tool_call_cancellation_distinguishes_pre_and_post_dispatch(
    tmp_path,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-cancel",
        trigger_type="message",
        trigger_ref="qq:message:cancel",
        principal_kind="user",
        principal_id="user-3",
    )

    async def create_call(call_id: str) -> None:
        await ledger.create_tool_call(
            call_id=call_id,
            run_id="run-cancel",
            step_id=call_id,
            tool_name="external_send",
            tool_version="1",
            owner="test",
            effect="external_irreversible",
            principal_kind="user",
            principal_id="user-3",
            args_digest=f"sha256:{call_id}",
            idempotency_mode="reconcile_only",
        )

    await create_call("call-before")
    cancelled = await ledger.cancel_tool_call(
        "call-before",
        actor="coordinator",
        reason="run_cancelled",
    )

    await create_call("call-after")
    await ledger.transition_tool_call(
        "call-after", to_status="ready", actor="policy"
    )
    await ledger.claim_tool_call(
        "call-after",
        lease_owner="worker-1",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="executor",
    )
    await ledger.transition_tool_call(
        "call-after",
        to_status="dispatching",
        actor="executor",
        expected_lease_owner="worker-1",
    )
    unknown = await ledger.cancel_tool_call(
        "call-after",
        actor="coordinator",
        reason="shutdown",
        force=True,
    )
    events = await ledger.list_events(run_id="run-cancel")
    await ledger.close()

    assert cancelled.status == "cancelled"
    assert cancelled.error_code == "run_cancelled"
    assert unknown.status == "unknown"
    assert unknown.error_code == "shutdown_post_dispatch_unknown"
    assert ("dispatching", "cancelled") not in {
        (event.from_status, event.to_status) for event in events
    }
    assert ("dispatching", "unknown") in {
        (event.from_status, event.to_status) for event in events
    }


async def test_restart_recovery_requeues_claimed_but_never_retries_unknown_dispatch(
    tmp_path,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    ledger = ledger_module.AgentRuntimeLedger(db_path)
    await ledger.init()
    await ledger.create_run(
        run_id="run-recovery",
        trigger_type="recovery",
        trigger_ref="boot:1",
        principal_kind="service",
        principal_id="runtime",
    )

    async def create_ready_call(call_id: str) -> None:
        await ledger.create_tool_call(
            call_id=call_id,
            run_id="run-recovery",
            step_id=call_id,
            tool_name="external_send",
            tool_version="1",
            owner="test",
            effect="external_irreversible",
            principal_kind="service",
            principal_id="runtime",
            args_digest=f"sha256:{call_id}",
            idempotency_mode="reconcile_only",
            concurrency_mode="parallel",
        )
        await ledger.transition_tool_call(
            call_id, to_status="ready", actor="policy"
        )

    await create_ready_call("call-claimed")
    await ledger.claim_tool_call(
        "call-claimed",
        lease_owner="dead-worker",
        lease_until="2020-01-01T00:00:00+00:00",
        actor="executor",
    )
    await create_ready_call("call-dispatching")
    await ledger.claim_tool_call(
        "call-dispatching",
        lease_owner="dead-worker",
        lease_until="2020-01-01T00:00:00+00:00",
        actor="executor",
    )
    await ledger.transition_tool_call(
        "call-dispatching",
        to_status="dispatching",
        actor="executor",
        expected_lease_owner="dead-worker",
    )
    await ledger.close()

    recovered_ledger = ledger_module.AgentRuntimeLedger(db_path)
    await recovered_ledger.init()
    recovered = await recovered_ledger.recover_incomplete_tool_calls(
        actor="recovery",
        exclusive_startup=True,
    )
    second_pass = await recovered_ledger.recover_incomplete_tool_calls(
        actor="recovery",
        exclusive_startup=True,
    )
    claimed = await recovered_ledger.get_tool_call("call-claimed")
    dispatching = await recovered_ledger.get_tool_call("call-dispatching")
    events = await recovered_ledger.list_events(run_id="run-recovery")
    await recovered_ledger.close()

    assert {record.call_id for record in recovered} == {
        "call-claimed",
        "call-dispatching",
    }
    assert second_pass == []
    assert claimed is not None
    assert claimed.status == "ready"
    assert claimed.attempt_count == 1
    assert claimed.lease_owner == ""
    assert dispatching is not None
    assert dispatching.status == "unknown"
    assert dispatching.attempt_count == 1
    assert dispatching.error_code == "restart_post_dispatch_unknown"
    assert [
        (event.from_status, event.to_status)
        for event in events
        if event.call_id == "call-dispatching"
    ][-1] == ("dispatching", "unknown")


async def test_restart_recovery_requires_explicit_exclusive_startup(
    tmp_path,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-exclusive-recovery",
        trigger_type="recovery",
        trigger_ref="runtime:exclusive-recovery",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.create_tool_call(
        call_id="call-exclusive-recovery",
        run_id="run-exclusive-recovery",
        step_id="call-exclusive-recovery",
        tool_name="read_tool",
        tool_version="1",
        owner="test",
        effect="read",
        principal_kind="service",
        principal_id="runtime",
        args_digest="sha256:exclusive-recovery",
        idempotency_mode="not_needed",
    )
    await ledger.transition_tool_call(
        "call-exclusive-recovery",
        to_status="ready",
        actor="policy",
    )
    await ledger.claim_tool_call(
        "call-exclusive-recovery",
        lease_owner="live-worker",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="executor",
    )

    try:
        await ledger.recover_incomplete_tool_calls(actor="recovery")
        recovery_error = None
    except ValueError as exc:
        recovery_error = exc
    call = await ledger.get_tool_call("call-exclusive-recovery")
    await ledger.close()

    assert recovery_error is not None
    assert "exclusive_startup=True" in str(recovery_error)
    assert call is not None and call.status == "claimed"
    assert call.lease_owner == "live-worker"


async def test_repeated_cancellation_cannot_detach_transaction_rollback(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    event_inserted = asyncio.Event()
    release = asyncio.Event()
    rollback_started = asyncio.Event()
    original_append = ledger_module.AgentRuntimeLedger._append_event
    original_rollback = aiosqlite.Connection.rollback

    async def blocking_append(db, **kwargs) -> None:
        await original_append(db, **kwargs)
        event_inserted.set()
        await release.wait()

    async def blocking_rollback(db) -> None:
        rollback_started.set()
        await release.wait()
        await original_rollback(db)

    monkeypatch.setattr(
        ledger_module.AgentRuntimeLedger,
        "_append_event",
        staticmethod(blocking_append),
    )
    monkeypatch.setattr(aiosqlite.Connection, "rollback", blocking_rollback)

    task = asyncio.create_task(
        ledger.create_run(
            run_id="run-cancelled-write",
            trigger_type="message",
            trigger_ref="qq:message:cancelled-write",
            principal_kind="service",
            principal_id="runtime",
        )
    )
    await event_inserted.wait()
    task.cancel()
    await rollback_started.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    monkeypatch.undo()
    run = await ledger.create_run(
        run_id="run-cancelled-write",
        trigger_type="message",
        trigger_ref="qq:message:retry",
        principal_kind="service",
        principal_id="runtime",
    )
    events = await ledger.list_events(run_id=run.run_id)
    await ledger.close()

    assert run.status == "queued"
    assert len(events) == 1


async def test_repeated_cancellation_cannot_detach_ledger_close(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    db = ledger._require_db()
    close_started = asyncio.Event()
    release = asyncio.Event()
    original_close = ledger_module.close_with_checkpoint

    async def blocking_close(connection, *, name: str) -> None:
        close_started.set()
        await release.wait()
        await original_close(connection, name=name)

    monkeypatch.setattr(ledger_module, "close_with_checkpoint", blocking_close)
    close_task = asyncio.create_task(ledger.close())
    await close_started.wait()
    close_task.cancel()
    await asyncio.sleep(0)
    close_task.cancel()
    release.set()
    try:
        with pytest.raises(asyncio.CancelledError):
            await close_task
        monkeypatch.undo()
        with pytest.raises(ValueError, match="no active connection"):
            await db.execute("SELECT 1")
    finally:
        release.set()
        monkeypatch.undo()
        with contextlib.suppress(ValueError):
            await original_close(db, name="agent_runtime_test_cleanup")


async def test_migration_uses_content_checksum_and_rejects_ledger_tampering(
    tmp_path,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    ledger = ledger_module.AgentRuntimeLedger(db_path)
    await ledger.init()
    await ledger.close()

    with sqlite3.connect(db_path) as db:
        checksum = db.execute(
            """
            SELECT checksum
            FROM _omubot_schema_migrations
            WHERE db_id = 'agent_runtime' AND version = 1
            """
        ).fetchone()[0]
        assert checksum.startswith("sha256:")
        assert len(checksum) == len("sha256:") + 64
        db.execute(
            """
            UPDATE _omubot_schema_migrations
            SET checksum = 'sha256:tampered'
            WHERE db_id = 'agent_runtime' AND version = 1
            """
        )

    corrupted = ledger_module.AgentRuntimeLedger(db_path)
    with pytest.raises(ValueError, match="migration ledger drift"):
        await corrupted.init()


async def test_migration_verification_rejects_missing_required_index(
    tmp_path,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    ledger = ledger_module.AgentRuntimeLedger(db_path)
    await ledger.init()
    await ledger.close()

    with sqlite3.connect(db_path) as db:
        db.execute("DROP INDEX idx_agent_calls_status")

    corrupted = ledger_module.AgentRuntimeLedger(db_path)
    with pytest.raises(RuntimeError, match="applied schema verification failed"):
        await corrupted.init()


async def test_v2_migration_verification_rejects_missing_concurrency_index(
    tmp_path,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    ledger = ledger_module.AgentRuntimeLedger(db_path)
    await ledger.init()
    await ledger.close()

    with sqlite3.connect(db_path) as db:
        db.execute("DROP INDEX idx_agent_calls_concurrency")

    corrupted = ledger_module.AgentRuntimeLedger(db_path)
    with pytest.raises(RuntimeError, match="applied schema verification failed"):
        await corrupted.init()


async def test_migration_verification_rejects_wrong_same_name_index(
    tmp_path,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    ledger = ledger_module.AgentRuntimeLedger(db_path)
    await ledger.init()
    await ledger.close()

    with sqlite3.connect(db_path) as db:
        db.execute("DROP INDEX idx_agent_calls_status")
        db.execute(
            """
            CREATE INDEX idx_agent_calls_status
            ON agent_tool_calls(tool_name)
            """
        )

    corrupted = ledger_module.AgentRuntimeLedger(db_path)
    with pytest.raises(RuntimeError, match="applied schema verification failed"):
        await corrupted.init()


async def test_migration_verification_rejects_missing_omitted_column(
    tmp_path,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    ledger = ledger_module.AgentRuntimeLedger(db_path)
    await ledger.init()
    await ledger.close()

    with sqlite3.connect(db_path) as db:
        db.execute("ALTER TABLE agent_runs DROP COLUMN group_id")

    corrupted = ledger_module.AgentRuntimeLedger(db_path)
    with pytest.raises(RuntimeError, match="applied schema verification failed"):
        await corrupted.init()


async def test_run_cancellation_atomically_closes_open_tool_calls(
    tmp_path,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-cascade",
        trigger_type="message",
        trigger_ref="qq:message:cascade",
        principal_kind="user",
        principal_id="user-cascade",
    )

    async def create_call(call_id: str) -> None:
        await ledger.create_tool_call(
            call_id=call_id,
            run_id="run-cascade",
            step_id=call_id,
            tool_name="external_send",
            tool_version="1",
            owner="test",
            effect="external_irreversible",
            principal_kind="user",
            principal_id="user-cascade",
            args_digest=f"sha256:{call_id}",
            idempotency_mode="reconcile_only",
        )

    await create_call("call-proposed")
    await create_call("call-dispatching")
    await ledger.transition_tool_call(
        "call-dispatching", to_status="ready", actor="policy"
    )
    await ledger.claim_tool_call(
        "call-dispatching",
        lease_owner="worker",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="executor",
    )
    await ledger.transition_tool_call(
        "call-dispatching",
        to_status="dispatching",
        actor="executor",
        expected_lease_owner="worker",
    )

    run = await ledger.transition_run(
        "run-cascade",
        to_status="cancelled",
        actor="coordinator",
        metadata={"reason": "shutdown"},
    )
    proposed = await ledger.get_tool_call("call-proposed")
    dispatching = await ledger.get_tool_call("call-dispatching")
    await ledger.close()

    assert run.status == "cancelled"
    assert proposed is not None
    assert proposed.status == "cancelled"
    assert proposed.error_code == "run_cancelled"
    assert dispatching is not None
    assert dispatching.status == "unknown"
    assert dispatching.error_code == "run_cancelled_post_dispatch_unknown"


async def test_restart_recovery_rolls_back_all_calls_when_one_transition_fails(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-atomic-recovery",
        trigger_type="recovery",
        trigger_ref="boot:atomic",
        principal_kind="service",
        principal_id="runtime",
    )
    for call_id in ("call-a", "call-b"):
        await ledger.create_tool_call(
            call_id=call_id,
            run_id="run-atomic-recovery",
            step_id=call_id,
            tool_name="read_tool",
            tool_version="1",
            owner="test",
            effect="read",
            principal_kind="service",
            principal_id="runtime",
            args_digest=f"sha256:{call_id}",
            idempotency_mode="not_needed",
            concurrency_mode="parallel",
        )
        await ledger.transition_tool_call(
            call_id, to_status="ready", actor="policy"
        )
        await ledger.claim_tool_call(
            call_id,
            lease_owner="dead-worker",
            lease_until="2020-01-01T00:00:00+00:00",
            actor="executor",
        )

    original_append = ledger_module.AgentRuntimeLedger._append_event
    recovery_events = 0

    async def fail_second_recovery_event(db, **kwargs) -> None:
        nonlocal recovery_events
        if kwargs.get("actor") == "recovery":
            recovery_events += 1
            if recovery_events == 2:
                raise RuntimeError("injected recovery event failure")
        await original_append(db, **kwargs)

    monkeypatch.setattr(
        ledger_module.AgentRuntimeLedger,
        "_append_event",
        staticmethod(fail_second_recovery_event),
    )
    with pytest.raises(RuntimeError, match="injected recovery event failure"):
        await ledger.recover_incomplete_tool_calls(
            actor="recovery",
            exclusive_startup=True,
        )
    monkeypatch.undo()

    first = await ledger.get_tool_call("call-a")
    second = await ledger.get_tool_call("call-b")
    await ledger.close()

    assert first is not None
    assert second is not None
    assert first.status == "claimed"
    assert second.status == "claimed"


async def test_terminal_run_rejects_late_tool_call_creation(tmp_path) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-terminal",
        trigger_type="message",
        trigger_ref="qq:message:terminal",
        principal_kind="user",
        principal_id="user-terminal",
    )
    await ledger.transition_run(
        "run-terminal", to_status="cancelled", actor="coordinator"
    )

    with pytest.raises(ValueError, match="terminal Agent run"):
        await ledger.create_tool_call(
            call_id="call-too-late",
            run_id="run-terminal",
            step_id="step-late",
            tool_name="read_tool",
            tool_version="1",
            owner="test",
            effect="read",
            principal_kind="user",
            principal_id="user-terminal",
            args_digest="sha256:late",
            idempotency_mode="not_needed",
        )

    assert await ledger.get_tool_call("call-too-late") is None
    await ledger.close()


async def test_run_cannot_succeed_with_open_tool_calls(tmp_path) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-open-call",
        trigger_type="message",
        trigger_ref="qq:message:open-call",
        principal_kind="user",
        principal_id="user-open-call",
    )
    await ledger.transition_run(
        "run-open-call", to_status="running", actor="coordinator"
    )
    await ledger.create_tool_call(
        call_id="call-open",
        run_id="run-open-call",
        step_id="step-open",
        tool_name="read_tool",
        tool_version="1",
        owner="test",
        effect="read",
        principal_kind="user",
        principal_id="user-open-call",
        args_digest="sha256:open",
        idempotency_mode="not_needed",
    )

    with pytest.raises(ValueError, match="open tool calls"):
        await ledger.transition_run(
            "run-open-call", to_status="succeeded", actor="coordinator"
        )

    run = await ledger.get_run("run-open-call")
    call = await ledger.get_tool_call("call-open")
    await ledger.close()
    assert run is not None
    assert run.status == "running"
    assert call is not None
    assert call.status == "proposed"


async def test_terminal_parent_blocks_stale_call_from_being_claimed(tmp_path) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-stale-call",
        trigger_type="recovery",
        trigger_ref="boot:stale-call",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.create_tool_call(
        call_id="call-stale",
        run_id="run-stale-call",
        step_id="step-stale",
        tool_name="read_tool",
        tool_version="1",
        owner="test",
        effect="read",
        principal_kind="service",
        principal_id="runtime",
        args_digest="sha256:stale",
        idempotency_mode="not_needed",
    )
    await ledger.transition_run(
        "run-stale-call", to_status="cancelled", actor="coordinator"
    )

    db = ledger._require_db()
    await db.execute(
        """
        UPDATE agent_tool_calls
        SET status = 'ready', terminal_at = '', error_code = ''
        WHERE call_id = 'call-stale'
        """
    )
    await db.commit()

    with pytest.raises(ValueError, match="terminal Agent run"):
        await ledger.claim_tool_call(
            "call-stale",
            lease_owner="worker",
            lease_until="2099-01-01T00:00:00+00:00",
            actor="executor",
        )

    stale = await ledger.get_tool_call("call-stale")
    await ledger.close()
    assert stale is not None
    assert stale.status == "ready"
    assert stale.attempt_count == 0


async def test_tool_call_can_only_be_claimed_once(tmp_path) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-single-claim",
        trigger_type="message",
        trigger_ref="qq:message:single-claim",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.create_tool_call(
        call_id="call-single-claim",
        run_id="run-single-claim",
        step_id="step-single-claim",
        tool_name="read_tool",
        tool_version="1",
        owner="test",
        effect="read",
        principal_kind="service",
        principal_id="runtime",
        args_digest="sha256:single-claim",
        idempotency_mode="not_needed",
    )
    await ledger.transition_tool_call(
        "call-single-claim", to_status="ready", actor="policy"
    )

    first = await ledger.claim_tool_call(
        "call-single-claim",
        lease_owner="worker-1",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="executor",
    )
    with pytest.raises(ValueError, match="illegal tool call transition"):
        await ledger.claim_tool_call(
            "call-single-claim",
            lease_owner="worker-2",
            lease_until="2099-01-02T00:00:00+00:00",
            actor="executor",
        )

    claimed = await ledger.get_tool_call("call-single-claim")
    await ledger.close()
    assert first.attempt_count == 1
    assert claimed is not None
    assert claimed.attempt_count == 1
    assert claimed.lease_owner == "worker-1"


@pytest.mark.parametrize("idempotency_mode", ["required", "provider_supported"])
async def test_idempotent_tool_call_requires_key_digest(
    tmp_path,
    idempotency_mode: str,
) -> None:
    ledger = ledger_module.AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    await ledger.create_run(
        run_id="run-idempotency",
        trigger_type="message",
        trigger_ref="qq:message:idempotency",
        principal_kind="service",
        principal_id="runtime",
    )

    with pytest.raises(ValueError, match="idempotency_key_digest is required"):
        await ledger.create_tool_call(
            call_id=f"call-{idempotency_mode}",
            run_id="run-idempotency",
            step_id="step-idempotency",
            tool_name="write_tool",
            tool_version="1",
            owner="test",
            effect="write_local",
            principal_kind="service",
            principal_id="runtime",
            args_digest="sha256:idempotency",
            idempotency_mode=idempotency_mode,
        )

    await ledger.close()


async def test_success_clears_transient_recovery_error(tmp_path) -> None:
    db_path = tmp_path / "agent-runtime.db"
    ledger = ledger_module.AgentRuntimeLedger(db_path)
    await ledger.init()
    await ledger.create_run(
        run_id="run-recovery-success",
        trigger_type="recovery",
        trigger_ref="boot:recovery-success",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.create_tool_call(
        call_id="call-recovery-success",
        run_id="run-recovery-success",
        step_id="step-recovery-success",
        tool_name="read_tool",
        tool_version="1",
        owner="test",
        effect="read",
        principal_kind="service",
        principal_id="runtime",
        args_digest="sha256:recovery-success",
        idempotency_mode="not_needed",
    )
    await ledger.transition_tool_call(
        "call-recovery-success", to_status="ready", actor="policy"
    )
    await ledger.claim_tool_call(
        "call-recovery-success",
        lease_owner="dead-worker",
        lease_until="2020-01-01T00:00:00+00:00",
        actor="executor",
    )
    await ledger.close()

    recovered = ledger_module.AgentRuntimeLedger(db_path)
    await recovered.init()
    await recovered.recover_incomplete_tool_calls(
        actor="recovery",
        exclusive_startup=True,
    )
    after_recovery = await recovered.get_tool_call("call-recovery-success")
    assert after_recovery is not None
    assert after_recovery.error_code == "restart_before_dispatch"

    await recovered.claim_tool_call(
        "call-recovery-success",
        lease_owner="worker-2",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="executor",
    )
    await recovered.transition_tool_call(
        "call-recovery-success",
        to_status="dispatching",
        actor="executor",
        expected_lease_owner="worker-2",
    )
    succeeded = await recovered.transition_tool_call(
        "call-recovery-success",
        to_status="succeeded",
        actor="executor",
        safe_result={"value": "ok"},
        expected_lease_owner="worker-2",
    )
    await recovered.close()

    assert succeeded.status == "succeeded"
    assert succeeded.error_code == ""
