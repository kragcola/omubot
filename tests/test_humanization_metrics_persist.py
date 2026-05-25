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
