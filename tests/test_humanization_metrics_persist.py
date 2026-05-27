from __future__ import annotations

import pytest

from services.block_trace.store import BlockTraceStore
from services.humanization.scorer import HumanizationScore


@pytest.fixture
async def store(tmp_path):
    trace_store = BlockTraceStore(db_path=tmp_path / "trace.db")
    await trace_store.init()
    yield trace_store
    await trace_store.close()


async def test_humanization_metrics_table_is_created(store: BlockTraceStore) -> None:
    cursor = await store._conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='humanization_metrics'"
    )

    assert await cursor.fetchone() is not None


async def test_record_and_list_humanization_metrics_round_trip(
    store: BlockTraceStore,
) -> None:
    score = HumanizationScore(
        total=0.82,
        axes={"content": 1.0, "register": 0.7},
        issues=["register.quiet_too_loud"],
        meta={"scorer": "unit"},
    )

    metric_id = await store.record_humanization_metrics(
        request_id="req-1",
        group_id="100",
        session_id="group_100",
        turn_id="turn-1",
        score=score,
        metadata={"rewrite": False},
    )

    rows = await store.list_humanization_metrics(request_id="req-1")
    assert rows == [
        {
            "metric_id": metric_id,
            "request_id": "req-1",
            "group_id": "100",
            "session_id": "group_100",
            "turn_id": "turn-1",
            "score": 0.82,
            "axes": {"content": 1.0, "register": 0.7},
            "issues": ["register.quiet_too_loud"],
            "metadata": {"scorer": "unit", "rewrite": False},
            "created_at": rows[0]["created_at"],
        }
    ]


async def test_humanization_metric_stats_include_issue_counts(
    store: BlockTraceStore,
) -> None:
    await store.record_humanization_metrics(
        request_id="req-1",
        group_id="100",
        score={"score": 0.5, "axes": {}, "issues": ["surface.em_dash"]},
    )
    await store.record_humanization_metrics(
        request_id="req-2",
        group_id="100",
        score={"score": 1.0, "axes": {}, "issues": ["surface.em_dash", "mood.low_energy_overexcited"]},
    )
    await store.record_humanization_metrics(
        request_id="req-3",
        group_id="200",
        score={"score": 0.2, "axes": {}, "issues": ["content.empty"]},
    )

    stats = await store.humanization_metric_stats(group_id="100")

    assert stats["total"] == 2
    assert stats["avg_score"] == 0.75
    assert stats["by_issue"] == {
        "surface.em_dash": 2,
        "mood.low_energy_overexcited": 1,
    }
    assert stats["near_duplicate_hits"] == 0
    assert stats["thinker_phrase_hits"] == 0


async def test_humanization_metric_stats_aggregate_guardrail_metadata(
    store: BlockTraceStore,
) -> None:
    await store.record_humanization_metrics(
        request_id="req-1",
        group_id="100",
        score={"score": 0.8, "axes": {}, "issues": []},
        metadata={
            "near_duplicate_hits": 1,
            "near_duplicate_rewritten": 1,
            "thinker_phrase_hits": 2,
            "sentinel_strip_hits": 3,
        },
    )
    await store.record_humanization_metrics(
        request_id="req-2",
        group_id="100",
        score={"score": 0.9, "axes": {}, "issues": []},
        metadata={
            "near_duplicate_hits": 2,
            "near_duplicate_dropped": 1,
            "sentinel_block_hits": 1,
        },
    )

    stats = await store.humanization_metric_stats(group_id="100")

    assert stats["near_duplicate_hits"] == 3
    assert stats["near_duplicate_rewritten"] == 1
    assert stats["near_duplicate_dropped"] == 1
    assert stats["thinker_phrase_hits"] == 2
    assert stats["sentinel_strip_hits"] == 3
    assert stats["sentinel_block_hits"] == 1


async def test_prune_removes_old_humanization_metrics_but_preserves_return_contract(
    store: BlockTraceStore,
) -> None:
    await store.record_humanization_metrics(
        request_id="req-old",
        score={"score": 0.4, "axes": {}, "issues": []},
        created_at="2020-01-01T00:00:00+08:00",
    )
    await store.record_humanization_metrics(
        request_id="req-new",
        score={"score": 0.9, "axes": {}, "issues": []},
    )

    deleted_traces = await store.prune(keep_days=1)
    rows = await store.list_humanization_metrics(limit=10)

    assert deleted_traces == 0
    assert [row["request_id"] for row in rows] == ["req-new"]


async def test_runtime_metric_stats_aggregate_router_and_scheduler_events(
    store: BlockTraceStore,
) -> None:
    await store.record_runtime_metric(metric_key="pair_guard_inbound_recorded", group_id="100", amount=2)
    await store.record_runtime_metric(metric_key="pair_guard_suppressed", group_id="100", amount=1)
    await store.record_runtime_metric(metric_key="coalesce_enqueued", group_id="100", amount=3)
    await store.record_runtime_metric(metric_key="coalesce_flushed", group_id="100", amount=2)
    await store.record_runtime_metric(metric_key="coalesce_bypassed", group_id="100", amount=1)
    await store.record_runtime_metric(metric_key="pair_guard_outbound_recorded", group_id="100", amount=4)

    stats = await store.stats()

    assert stats["pair_guard_inbound_recorded"] == 2
    assert stats["pair_guard_suppressed"] == 1
    assert stats["coalesce_enqueued"] == 3
    assert stats["coalesce_flushed"] == 2
    assert stats["coalesce_bypassed"] == 1
    assert stats["pair_guard_outbound_recorded"] == 4
