"""Joint Dual-Path Memory Telemetry v1 (jdt_v1) — RED→GREEN contract tests.

Pure aggregation + BlockTraceStore read-only joint view + Admin route.
No schema migration; no LLMClient / PromptBudgetManager / dual-path mutation.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.block_trace import create_block_trace_router
from kernel.config import BlockTraceConfig, BotConfig
from services.block_trace.budget_manager import PromptBudgetManager
from services.block_trace.joint_telemetry import (
    CLOSED_DECISIONS,
    JDT_VERSION,
    JOINT_ROLES,
    OUTCOME_FLAGS,
    RELEVANT_SOURCES,
    aggregate_joint_snapshot,
    clamp_limit,
    classify_request_traces,
    disabled_snapshot,
    empty_decision_totals,
    empty_outcome_counts,
)
from services.block_trace.store import BlockTraceStore
from services.block_trace.types import BudgetDecision, PromptBlockCandidate, PromptBlockTrace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trace_map(
    *,
    source: str,
    decision: str,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {"source": source, "decision": decision}
    row.update(extra)
    return row


def _make_pbt(
    *,
    request_id: str,
    source: str,
    decision: BudgetDecision = "accepted",
    candidate_id: str = "pbc_x",
    label: str = "safe-label",
    hit_reason: str = "safe-hit",
    created_at: str = "2026-07-17T12:00:00+08:00",
    metadata: dict[str, Any] | None = None,
    evidence_refs: tuple[str, ...] = (),
    provider: str = "test_provider",
    budget_reason: str = "test budget",
) -> PromptBlockTrace:
    return PromptBlockTrace(
        trace_id="",
        request_id=request_id,
        task="main",
        source=source,
        provider=provider,
        candidate_id=candidate_id,
        decision=decision,
        hit_reason=hit_reason,
        evidence_refs=evidence_refs,
        token_estimate=10,
        char_count=30,
        position="dynamic",
        label=label,
        priority=50,
        budget_reason=budget_reason,
        metadata=metadata or {},
        created_at=created_at,
    )


def _assert_closed_payload_shape(payload: dict[str, Any], *, enabled: bool) -> None:
    assert set(payload.keys()) == {
        "version",
        "enabled",
        "sample_size",
        "decision_totals",
        "outcome_counts",
        "recent",
    }
    assert payload["version"] == JDT_VERSION
    assert payload["enabled"] is enabled
    assert isinstance(payload["sample_size"], int)
    assert set(payload["decision_totals"].keys()) == set(JOINT_ROLES)
    for role in JOINT_ROLES:
        assert set(payload["decision_totals"][role].keys()) == set(CLOSED_DECISIONS)
        for d in CLOSED_DECISIONS:
            assert isinstance(payload["decision_totals"][role][d], int)
    assert set(payload["outcome_counts"].keys()) == set(OUTCOME_FLAGS)
    for flag in OUTCOME_FLAGS:
        assert isinstance(payload["outcome_counts"][flag], int)
    assert isinstance(payload["recent"], list)
    for item in payload["recent"]:
        assert set(item.keys()) == {"decision_totals", "outcomes"}
        assert set(item["decision_totals"].keys()) == set(JOINT_ROLES)
        assert set(item["outcomes"].keys()) == set(OUTCOME_FLAGS)
        for flag in OUTCOME_FLAGS:
            assert isinstance(item["outcomes"][flag], bool)


# Values / open field names that must never appear as payload keys or
# secret-bearing substrings. Avoid bare tokens that are accidental substrings
# of closed role names (e.g. "text" ⊂ "context_temporal_trace").
FORBIDDEN_SECRET_VALUES = (
    "SECRET user query about sk-live-SECRET",
    "hostile content body",
    "hostile text",
    "hostile-label",
    "hostile-hit",
    "g_SECRET",
    "u_SECRET",
    "s_SECRET",
    "cand_secret_1",
    "cand_secret_2",
    "ev_id_SECRET_1",
    "hostile quote SECRET",
    "hostile_provider",
    "sk-live-SECRET",
    "budget SECRET reason",
    "leak me",
)

FORBIDDEN_OPEN_KEYS = frozenset(
    {
        "query",
        "content",
        "text",
        "label",
        "hit_reason",
        "group_id",
        "user_id",
        "session_id",
        "candidate_id",
        "evidence",
        "evidence_refs",
        "quote",
        "provider",
        "metadata",
        "budget_reason",
        "group",
        "user",
        "session",
    }
)


# ---------------------------------------------------------------------------
# A–E pure classification
# ---------------------------------------------------------------------------


def test_a_both_context_main_and_episode_accepted() -> None:
    classified = classify_request_traces(
        [
            _trace_map(source="context", decision="accepted"),
            _trace_map(source="episode", decision="accepted"),
        ]
    )
    outcomes = classified["outcomes"]
    assert outcomes["both_present"] is True
    assert outcomes["both_survived"] is True
    assert outcomes["context_only_survived"] is False
    assert outcomes["episode_only_survived"] is False
    assert outcomes["neither_survived"] is False
    assert classified["decision_totals"]["context_main"]["accepted"] == 1
    assert classified["decision_totals"]["episode"]["accepted"] == 1

    snap = aggregate_joint_snapshot(
        request_groups=[
            (
                "req_both",
                [
                    _trace_map(source="context", decision="accepted"),
                    _trace_map(source="episode", decision="accepted"),
                ],
            )
        ]
    )
    _assert_closed_payload_shape(snap, enabled=True)
    assert snap["sample_size"] == 1
    assert snap["outcome_counts"]["both_present"] == 1
    assert snap["outcome_counts"]["both_survived"] == 1
    assert snap["decision_totals"]["context_main"]["accepted"] == 1
    assert snap["decision_totals"]["episode"]["accepted"] == 1


def test_b_context_main_rejected_episode_accepted() -> None:
    classified = classify_request_traces(
        [
            _trace_map(source="context", decision="rejected"),
            _trace_map(source="episode", decision="accepted"),
        ]
    )
    outcomes = classified["outcomes"]
    assert outcomes["episode_only_survived"] is True
    assert outcomes["context_main_dropped_episode_survived"] is True
    assert outcomes["both_survived"] is False
    assert outcomes["context_only_survived"] is False
    assert classified["decision_totals"]["context_main"]["rejected"] == 1
    assert classified["decision_totals"]["episode"]["accepted"] == 1


def test_c_no_main_constrained_and_episode_accepted() -> None:
    classified = classify_request_traces(
        [
            _trace_map(source="context_evidence_use", decision="accepted"),
            _trace_map(source="episode", decision="accepted"),
        ]
    )
    outcomes = classified["outcomes"]
    assert outcomes["both_present"] is True
    assert outcomes["both_survived"] is True
    assert outcomes["context_main_absent_episode_survived"] is True
    assert outcomes["constrained_and_episode_survived"] is True
    assert outcomes["context_main_dropped_episode_survived"] is False
    assert classified["decision_totals"]["context_constrained"]["accepted"] == 1
    assert classified["decision_totals"]["context_main"]["accepted"] == 0


def test_d_trimmed_survives_counts_exact_unknown_fail_closed() -> None:
    classified = classify_request_traces(
        [
            _trace_map(source="context", decision="trimmed"),
            _trace_map(source="context", decision="trimmed"),
            _trace_map(source="episode", decision="trimmed"),
            _trace_map(source="slang", decision="accepted"),  # unknown source
            _trace_map(source="context", decision="shadow_only"),  # unknown decision
            _trace_map(source="context", decision="weird"),  # unknown decision
            "not-a-mapping",  # type: ignore[list-item]
            None,  # type: ignore[list-item]
            {"source": "context"},  # missing decision
            {"decision": "accepted"},  # missing source
        ]
    )
    totals = classified["decision_totals"]
    assert totals["context_main"]["trimmed"] == 2
    assert totals["episode"]["trimmed"] == 1
    assert totals["context_main"]["accepted"] == 0
    assert totals["context_main"]["rejected"] == 0
    # no dynamic roles
    assert set(totals.keys()) == set(JOINT_ROLES)
    outcomes = classified["outcomes"]
    assert outcomes["both_survived"] is True
    assert outcomes["neither_survived"] is False
    # no dynamic outcome keys
    assert set(outcomes.keys()) == set(OUTCOME_FLAGS)

    snap = aggregate_joint_snapshot(
        request_groups=[("req_d", [
            _trace_map(source="context", decision="trimmed"),
            _trace_map(source="episode", decision="trimmed"),
            _trace_map(source="unknown_src", decision="accepted"),
        ])]
    )
    blob = json.dumps(snap, ensure_ascii=False)
    assert "unknown_src" not in blob
    assert "slang" not in blob
    _assert_closed_payload_shape(snap, enabled=True)


def test_d_unknown_decision_does_not_make_a_path_present() -> None:
    classified = classify_request_traces(
        [
            _trace_map(source="context", decision="shadow_only"),
            _trace_map(source="episode", decision="accepted"),
        ]
    )

    assert classified["decision_totals"]["context_main"] == {
        "accepted": 0,
        "trimmed": 0,
        "rejected": 0,
    }
    assert classified["outcomes"]["both_present"] is False
    assert classified["outcomes"]["episode_only_survived"] is True
    assert classified["outcomes"]["context_main_absent_episode_survived"] is True

    unknown_only = aggregate_joint_snapshot(
        request_groups=[
            (
                "req_shadow_only",
                [_trace_map(source="context", decision="shadow_only")],
            )
        ]
    )
    assert unknown_only["sample_size"] == 0
    assert unknown_only["recent"] == []
    assert unknown_only["outcome_counts"] == empty_outcome_counts()


def test_e_secret_hygiene_hostile_fields_never_in_payload() -> None:
    hostile_rid = "group_984198159_123456"
    hostile = [
        _trace_map(
            source="context",
            decision="accepted",
            query="SECRET user query about sk-live-SECRET",
            content="hostile content body",
            text="hostile text",
            label="hostile-label",
            hit_reason="hostile-hit",
            group_id="g_SECRET",
            user_id="u_SECRET",
            session_id="s_SECRET",
            candidate_id="cand_secret_1",
            evidence_refs=["ev_id_SECRET_1"],
            evidence="ev_id_SECRET_1",
            quote="hostile quote SECRET",
            provider="hostile_provider",
            metadata={"token": "sk-live-SECRET", "note": "hostile"},
            budget_reason="budget SECRET reason",
        ),
        _trace_map(
            source="episode",
            decision="accepted",
            label="ep-hostile",
            candidate_id="cand_secret_2",
            metadata={"query": "leak me"},
        ),
    ]
    snap = aggregate_joint_snapshot(request_groups=[(hostile_rid, hostile)])
    _assert_closed_payload_shape(snap, enabled=True)
    blob = json.dumps(snap, ensure_ascii=False)
    # Runtime request_id embeds session/group/user identifiers and must not appear.
    assert hostile_rid not in blob
    for needle in FORBIDDEN_SECRET_VALUES:
        assert needle not in blob, f"leaked secret value: {needle}"
    assert "SECRET" not in blob
    assert "hostile" not in blob
    assert "sk-live" not in blob
    assert "ev_id_" not in blob
    assert "cand_secret" not in blob

    # Walk all keys: only closed structural keys allowed.
    def _walk_keys(obj: Any) -> list[str]:
        keys: list[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                keys.append(str(k))
                keys.extend(_walk_keys(v))
        elif isinstance(obj, list):
            for item in obj:
                keys.extend(_walk_keys(item))
        return keys

    all_keys = set(_walk_keys(snap))
    leaked = all_keys & FORBIDDEN_OPEN_KEYS
    assert leaked == set(), f"open keys leaked into payload: {leaked}"
    # Explicit key scan on recent item
    item = snap["recent"][0]
    for key in item:
        assert key in {"decision_totals", "outcomes"}
    # Nested maps must not grow dynamic source keys
    assert set(item["decision_totals"].keys()) == set(JOINT_ROLES)


def test_disabled_snapshot_exact_zero_shape() -> None:
    snap = disabled_snapshot()
    _assert_closed_payload_shape(snap, enabled=False)
    assert snap == {
        "version": JDT_VERSION,
        "enabled": False,
        "sample_size": 0,
        "decision_totals": empty_decision_totals(),
        "outcome_counts": empty_outcome_counts(),
        "recent": [],
    }
    assert aggregate_joint_snapshot(request_groups=[], enabled=False) == snap


def test_clamp_limit_closed_range() -> None:
    assert clamp_limit(None) == 50
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(1) == 1
    assert clamp_limit(200) == 200
    assert clamp_limit(999) == 200
    assert clamp_limit("nope") == 50  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# F–G store snapshot
# ---------------------------------------------------------------------------


@pytest.fixture
async def bt_store(tmp_path: Path):
    store = BlockTraceStore(db_path=str(tmp_path / "jdt.db"))
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_f_store_bounded_latest_requests_complete_rows(bt_store: BlockTraceStore) -> None:
    # Three relevant requests + one unrelated-only request.
    # Newest-first by created_at.
    await bt_store.record(
        _make_pbt(
            request_id="req_old",
            source="context",
            decision="accepted",
            created_at="2026-07-17T10:00:00+08:00",
        )
    )
    await bt_store.record(
        _make_pbt(
            request_id="req_old",
            source="episode",
            decision="rejected",
            candidate_id="pbc_e1",
            created_at="2026-07-17T10:00:01+08:00",
        )
    )
    await bt_store.record(
        _make_pbt(
            request_id="req_mid",
            source="context_temporal_trace",
            decision="trimmed",
            created_at="2026-07-17T11:00:00+08:00",
        )
    )
    await bt_store.record(
        _make_pbt(
            request_id="req_mid",
            source="episode",
            decision="accepted",
            candidate_id="pbc_e2",
            created_at="2026-07-17T11:00:01+08:00",
        )
    )
    await bt_store.record(
        _make_pbt(
            request_id="req_new",
            source="context_evidence_use",
            decision="accepted",
            created_at="2026-07-17T12:00:00+08:00",
        )
    )
    await bt_store.record(
        _make_pbt(
            request_id="req_new",
            source="episode",
            decision="accepted",
            candidate_id="pbc_e3",
            created_at="2026-07-17T12:00:01+08:00",
        )
    )
    # Unrelated-only request must be excluded from sample.
    await bt_store.record(
        _make_pbt(
            request_id="req_unrelated",
            source="slang",
            decision="accepted",
            created_at="2026-07-17T13:00:00+08:00",
        )
    )
    # Extra unrelated row on a relevant request must not appear in payload secrets.
    await bt_store.record(
        _make_pbt(
            request_id="req_new",
            source="slang",
            decision="accepted",
            candidate_id="cand_secret_extra",
            label="hostile-label",
            hit_reason="hostile-hit",
            created_at="2026-07-17T12:00:02+08:00",
            metadata={"token": "sk-live-SECRET"},
        )
    )

    # Spy: list_for_request / recent must not be used (no N+1 / mid-stream cut).
    list_calls: list[str] = []
    recent_calls: list[int] = []
    orig_list = bt_store.list_for_request
    orig_recent = bt_store.recent

    async def _list_spy(request_id: str) -> list[PromptBlockTrace]:
        list_calls.append(request_id)
        return await orig_list(request_id)

    async def _recent_spy(limit: int = 50) -> list[PromptBlockTrace]:
        recent_calls.append(limit)
        return await orig_recent(limit)

    bt_store.list_for_request = _list_spy  # type: ignore[method-assign]
    bt_store.recent = _recent_spy  # type: ignore[method-assign]

    snap = await bt_store.joint_dual_path_snapshot(limit=2)
    assert list_calls == [], "must not N+1 list_for_request"
    assert recent_calls == [], "must not call recent() and cut mid-stream"
    _assert_closed_payload_shape(snap, enabled=True)
    assert snap["sample_size"] == 2
    # Newest relevant first: req_new then req_mid (req_unrelated excluded; req_old outside limit)
    assert snap["recent"][0]["decision_totals"]["context_constrained"]["accepted"] == 1
    assert snap["recent"][1]["decision_totals"]["context_temporal_trace"]["trimmed"] == 1
    # Complete relevant rows for req_new: constrained + episode
    new_item = snap["recent"][0]
    assert new_item["decision_totals"]["context_constrained"]["accepted"] == 1
    assert new_item["decision_totals"]["episode"]["accepted"] == 1
    assert new_item["outcomes"]["both_survived"] is True
    assert new_item["outcomes"]["constrained_and_episode_survived"] is True
    mid_item = snap["recent"][1]
    assert mid_item["decision_totals"]["context_temporal_trace"]["trimmed"] == 1
    assert mid_item["decision_totals"]["episode"]["accepted"] == 1

    blob = json.dumps(snap, ensure_ascii=False)
    assert "sk-live-SECRET" not in blob
    assert "hostile-label" not in blob
    assert "cand_secret_extra" not in blob
    assert "req_unrelated" not in blob

    # Full sample includes all three relevant requests.
    full = await bt_store.joint_dual_path_snapshot(limit=50)
    assert full["sample_size"] == 3
    assert full["recent"][2]["decision_totals"]["context_main"]["accepted"] == 1
    assert full["outcome_counts"]["both_present"] == 3


@pytest.mark.asyncio
async def test_g_kill_switch_never_executes_sql(tmp_path: Path) -> None:
    store = BlockTraceStore(
        db_path=str(tmp_path / "jdt-off.db"),
        joint_dual_path_telemetry_enabled=False,
    )
    await store.init()
    try:
        await store.record(
            _make_pbt(
                request_id="req_ks",
                source="context",
                decision="accepted",
            )
        )
        db = store._conn()
        sql_trace: list[str] = []

        def _trace(statement: str) -> None:
            sql_trace.append(statement)

        await db.set_trace_callback(_trace)
        try:
            snap = await store.joint_dual_path_snapshot(limit=10)
        finally:
            await db.set_trace_callback(lambda _s: None)

        assert snap == disabled_snapshot()
        _assert_closed_payload_shape(snap, enabled=False)
        joint_sql = [
            s
            for s in sql_trace
            if "prompt_block_traces" in s.lower()
            or "request_id" in s.lower()
        ]
        assert joint_sql == [], f"kill-switch must not SELECT: {joint_sql}"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_limit_clamped(bt_store: BlockTraceStore) -> None:
    for i in range(5):
        await bt_store.record(
            _make_pbt(
                request_id=f"req_{i}",
                source="episode",
                decision="accepted",
                created_at=f"2026-07-17T1{i}:00:00+08:00",
            )
        )
    snap = await bt_store.joint_dual_path_snapshot(limit=0)
    assert snap["sample_size"] >= 1
    snap2 = await bt_store.joint_dual_path_snapshot(limit=10_000)
    assert snap2["sample_size"] == 5


@pytest.mark.asyncio
async def test_store_excludes_shadow_only_and_empty_request_ids(
    bt_store: BlockTraceStore,
) -> None:
    await bt_store.record(
        _make_pbt(
            request_id="req_shadow",
            source="episode",
            decision="shadow_only",
            created_at="2026-07-17T13:00:00+08:00",
        )
    )
    await bt_store.record(
        _make_pbt(
            request_id="",
            source="context",
            decision="accepted",
            created_at="2026-07-17T12:30:00+08:00",
        )
    )
    await bt_store.record(
        _make_pbt(
            request_id="req_live",
            source="context",
            decision="accepted",
            created_at="2026-07-17T12:00:00+08:00",
        )
    )

    snap = await bt_store.joint_dual_path_snapshot(limit=50)

    assert snap["sample_size"] == 1
    assert snap["recent"][0]["decision_totals"]["context_main"]["accepted"] == 1
    assert "req_live" not in json.dumps(snap)


# ---------------------------------------------------------------------------
# H Admin route
# ---------------------------------------------------------------------------


def test_h_admin_route_public_snapshot_and_fail_closed() -> None:
    calls: list[int | None] = []

    class _Store:
        async def joint_dual_path_snapshot(self, limit: int = 50) -> dict[str, Any]:
            calls.append(limit)
            return aggregate_joint_snapshot(
                request_groups=[
                    (
                        "req_admin",
                        [
                            _trace_map(source="context", decision="accepted"),
                            _trace_map(source="episode", decision="accepted"),
                        ],
                    )
                ]
            )

    app = FastAPI()
    app.include_router(
        create_block_trace_router(ctx=SimpleNamespace(block_trace_store=_Store()))
    )
    with TestClient(app) as client:
        resp = client.get("/block-trace/joint-memory-paths", params={"limit": 7})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    _assert_closed_payload_shape(body["snapshot"], enabled=True)
    assert calls == [7]


def test_h_admin_missing_store_fail_closed() -> None:
    app = FastAPI()
    app.include_router(create_block_trace_router(ctx=SimpleNamespace()))
    with TestClient(app) as client:
        resp = client.get("/block-trace/joint-memory-paths")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "BlockTraceStore not available"


def test_h_admin_exception_type_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from admin.routes.api import block_trace as block_trace_route

    warnings: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        block_trace_route,
        "logger",
        SimpleNamespace(
            warning=lambda message, *args: warnings.append((message, args))
        ),
    )

    class _Boom:
        async def joint_dual_path_snapshot(self, limit: int = 50) -> dict[str, Any]:
            raise RuntimeError("secret path /tmp/leak SECRET details")

    app = FastAPI()
    app.include_router(
        create_block_trace_router(ctx=SimpleNamespace(block_trace_store=_Boom()))
    )
    with TestClient(app) as client:
        resp = client.get("/block-trace/joint-memory-paths")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "joint_snapshot_failed:RuntimeError"
    blob = json.dumps(body, ensure_ascii=False)
    assert "SECRET" not in blob
    assert "/tmp/leak" not in blob
    assert "secret path" not in blob
    assert warnings == [
        ("joint_dual_path_snapshot failed | error={}", ("RuntimeError",))
    ]


def test_h_admin_cancelled_error_propagates() -> None:
    """CancelledError must not be converted to type-only diagnostics.

    Starlette TestClient may re-raise as concurrent.futures.CancelledError
    after the route lets asyncio.CancelledError bubble (BaseException).
    """
    import concurrent.futures

    class _Cancel:
        async def joint_dual_path_snapshot(self, limit: int = 50) -> dict[str, Any]:
            raise asyncio.CancelledError()

    app = FastAPI()
    app.include_router(
        create_block_trace_router(ctx=SimpleNamespace(block_trace_store=_Cancel()))
    )
    with TestClient(app, raise_server_exceptions=True) as client, pytest.raises(
        (asyncio.CancelledError, concurrent.futures.CancelledError)
    ):
        client.get("/block-trace/joint-memory-paths")


def test_h_admin_limit_bounds_via_query() -> None:
    limits: list[int] = []

    class _Store:
        async def joint_dual_path_snapshot(self, limit: int = 50) -> dict[str, Any]:
            limits.append(limit)
            return disabled_snapshot() | {"enabled": True}

    app = FastAPI()
    app.include_router(
        create_block_trace_router(ctx=SimpleNamespace(block_trace_store=_Store()))
    )
    with TestClient(app) as client:
        # FastAPI Query ge/le should reject out-of-range OR store clamps;
        # contract: endpoint accepts closed range and passes validated limit.
        ok = client.get("/block-trace/joint-memory-paths", params={"limit": 50})
        assert ok.status_code == 200
        bad_low = client.get("/block-trace/joint-memory-paths", params={"limit": 0})
        bad_high = client.get(
            "/block-trace/joint-memory-paths", params={"limit": 500}
        )
    assert limits[0] == 50
    # Out-of-range must not reach store with unsafe limit: 422 or clamped.
    if bad_low.status_code == 200:
        assert limits[-2 if bad_high.status_code == 200 else -1] in (1, 50)
    else:
        assert bad_low.status_code == 422
    if bad_high.status_code == 200:
        assert limits[-1] <= 200
    else:
        assert bad_high.status_code == 422


# ---------------------------------------------------------------------------
# I Config + constructor compatibility
# ---------------------------------------------------------------------------


def test_i_config_default_true_and_bot_config_field() -> None:
    cfg = BlockTraceConfig()
    assert cfg.joint_dual_path_telemetry_enabled is True
    bot = BotConfig()
    assert hasattr(bot, "block_trace")
    assert bot.block_trace.joint_dual_path_telemetry_enabled is True


@pytest.mark.asyncio
async def test_i_old_constructor_compatibility(tmp_path: Path) -> None:
    # Existing call sites: BlockTraceStore(db_path=...) only.
    store = BlockTraceStore(db_path=str(tmp_path / "compat.db"))
    await store.init()
    try:
        assert getattr(store, "_joint_dual_path_telemetry_enabled", True) is True
        snap = await store.joint_dual_path_snapshot()
        _assert_closed_payload_shape(snap, enabled=True)
        assert snap["sample_size"] == 0
    finally:
        await store.close()


def test_i_bootstrap_pass_through_source_ast() -> None:
    src = Path("bootstrap/chat_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    text = ast.unparse(tree) if hasattr(ast, "unparse") else src
    assert "joint_dual_path_telemetry_enabled" in src
    assert "BlockTraceStore" in text
    # Composition root must pass config flag into constructor.
    assert "block_trace" in src
    assert "joint_dual_path_telemetry_enabled" in src


# ---------------------------------------------------------------------------
# J Regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_j_schema_version_remains_v1(bt_store: BlockTraceStore) -> None:
    db_path = bt_store._db_path
    await bt_store.close()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("PRAGMA user_version").fetchone()
        assert row is not None
        assert int(row[0]) == 1
    # re-open for fixture teardown safety
    store2 = BlockTraceStore(db_path=db_path)
    await store2.init()
    await store2.close()


@pytest.mark.asyncio
async def test_j_stats_and_alignment_shape_unchanged(bt_store: BlockTraceStore) -> None:
    await bt_store.record(
        _make_pbt(request_id="req_s", source="context", decision="accepted")
    )
    await bt_store.record(
        _make_pbt(
            request_id="req_s",
            source="episode",
            decision="trimmed",
            candidate_id="pbc_ep",
        )
    )
    stats = await bt_store.stats()
    assert "total" in stats
    assert "by_decision" in stats
    assert "by_source" in stats
    assert "by_position" in stats
    assert "joint" not in stats
    assert "jdt" not in json.dumps(stats)

    # alignment route shape
    app = FastAPI()
    app.include_router(
        create_block_trace_router(
            ctx=SimpleNamespace(block_trace_store=bt_store),
        )
    )
    with TestClient(app) as client:
        resp = client.get("/block-trace/alignment", params={"limit": 10})
        stats_resp = client.get("/block-trace/stats")
    assert resp.status_code == 200
    align = resp.json()
    assert align["ok"] is True
    assert "sample_size" in align
    assert "mode" in align
    assert "by_source" in align
    assert stats_resp.status_code == 200
    assert stats_resp.json()["ok"] is True


def test_j_prompt_budget_manager_accepted_block_list_unchanged() -> None:
    # Signature stability: process still accepts candidates and returns
    # (surviving blocks, accepted decisions). No joint-telemetry side effects.
    assert hasattr(PromptBudgetManager, "process")
    sig = inspect.signature(PromptBudgetManager.process)
    params = list(sig.parameters)
    assert "self" in params
    assert "candidates" in params
    assert "request_id" in params

    from services.block_trace import types as tmod

    assert hasattr(tmod, "AcceptedDecision")
    assert hasattr(tmod, "PromptBlockCandidate")
    c = PromptBlockCandidate(
        candidate_id="c1",
        source="episode",
        provider="episode_provider",
        layer="dynamic",
        label="x",
        text="y",
        priority=50,
        position="dynamic",
        scope="global",
        group_id="",
        hit_reason="h",
        char_count=1,
    )
    mgr = PromptBudgetManager(trace_store=None)  # type: ignore[arg-type]
    blocks, accepted = mgr.process([c], request_id="req_budget_reg")
    assert isinstance(blocks, list)
    assert isinstance(accepted, list)
    assert len(blocks) == 1
    assert len(accepted) == 1
    assert accepted[0].source == "episode"
    assert accepted[0].decision == "accepted"


def test_j_relevant_sources_closed() -> None:
    assert frozenset(
        {
            "context",
            "context_temporal_trace",
            "context_evidence_use",
            "episode",
        }
    ) == RELEVANT_SOURCES
