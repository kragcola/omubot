"""Smoke tests for /api/admin/dashboard/cache-pipelines endpoint.

Verifies the endpoint folds raw ``llm_calls`` rows into the 4
pipelines defined by ``services/llm/llm_pipelines.py`` and computes
weighted hit percentages correctly. Also covers the call_type alias
translation (``chat`` / ``proactive`` → ``main``) and the
None-when-no-cache-data fallback.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.dashboard import create_dashboard_router
from services.llm.usage import UsageTracker


@pytest.fixture
async def tracker(tmp_path):
    t = UsageTracker(db_path=str(tmp_path / "usage.db"))
    await t.init()
    try:
        yield t
    finally:
        await t.close()


def _client(usage_tracker: UsageTracker) -> TestClient:
    app = FastAPI()
    app.include_router(create_dashboard_router(usage_tracker=usage_tracker), prefix="/api/admin")
    return TestClient(app)


async def _record(
    tracker: UsageTracker,
    *,
    call_type: str,
    hit: int,
    miss: int,
) -> None:
    await tracker.record(
        call_type=call_type,
        user_id="100",
        group_id=None,
        model="m",
        provider_kind="anthropic",
        input_tokens=hit + miss,
        cache_read_tokens=hit,
        cache_create_tokens=0,
        output_tokens=10,
        prompt_cache_hit_tokens=hit,
        prompt_cache_miss_tokens=miss,
        reasoning_replay_tokens=0,
        tool_rounds=0,
        elapsed_s=0.1,
        error=None,
    )


async def test_cache_pipelines_endpoint_smoke(tracker) -> None:
    """Mixed-task data: every pipeline aggregates correctly + overall matches."""
    # core_chat: main(900/100), thinker(400/600), reply_gate(0/0)
    await _record(tracker, call_type="main",       hit=900, miss=100)
    await _record(tracker, call_type="thinker",    hit=400, miss=600)
    await _record(tracker, call_type="reply_gate", hit=0,   miss=0)
    # slang: slang(800/200), slang_review(0/0)
    await _record(tracker, call_type="slang",        hit=800, miss=200)
    await _record(tracker, call_type="slang_review", hit=0,   miss=0)
    # learning: memo(50/950)
    await _record(tracker, call_type="memo", hit=50, miss=950)
    # legacy alias: chat → main, proactive → main
    await _record(tracker, call_type="chat",      hit=100, miss=0)
    await _record(tracker, call_type="proactive", hit=0,   miss=100)
    # unclassified: counted in overall but no pipeline
    await _record(tracker, call_type="dream", hit=300, miss=200)

    client = _client(tracker)
    resp = client.get("/api/admin/dashboard/cache-pipelines?period=day")
    assert resp.status_code == 200
    data = resp.json()

    # Top-level shape
    assert data["period"] == "day"
    assert "generated_at" in data
    assert {p["key"] for p in data["pipelines"]} == {
        "core_chat", "slang", "learning", "memory_graph",
    }

    # Overall sums everything (including unclassified dream).
    overall = data["overall"]
    assert overall["calls"] == 9
    # hit = 900+400+0+800+0+50+100+0+300 = 2550
    assert overall["hit_tokens"] == 2550
    # miss = 100+600+0+200+0+950+0+100+200 = 2150
    assert overall["miss_tokens"] == 2150
    assert overall["hit_pct"] == pytest.approx(2550 / 4700)

    pipelines = {p["key"]: p for p in data["pipelines"]}

    # core_chat folds chat/proactive into main bucket.
    cc = pipelines["core_chat"]
    # 5 raw rows: main + chat + proactive + thinker + reply_gate (compact had 0 rows)
    assert cc["calls"] == 5
    assert cc["hit_tokens"] == 900 + 400 + 0 + 100 + 0
    assert cc["miss_tokens"] == 100 + 600 + 0 + 0 + 100
    cc_per_task = {t["task"]: t for t in cc["per_task"]}
    # main absorbs the legacy chat/proactive aliases.
    assert cc_per_task["main"]["calls"] == 3
    assert cc_per_task["main"]["hit_tokens"] == 900 + 100 + 0
    assert cc_per_task["main"]["miss_tokens"] == 100 + 0 + 100
    # reply_gate had 1 row with hit=miss=0 — placeholder pct still None.
    assert cc_per_task["reply_gate"]["calls"] == 1
    assert cc_per_task["reply_gate"]["hit_pct"] is None
    # compact never invoked — pure placeholder.
    assert cc_per_task["compact"]["calls"] == 0
    assert cc_per_task["compact"]["hit_pct"] is None
    # tasks list preserved in declared order
    assert [t["task"] for t in cc["per_task"]] == ["main", "thinker", "compact", "reply_gate"]

    # slang pipeline arithmetic.
    sl = pipelines["slang"]
    assert sl["hit_tokens"] == 800
    assert sl["miss_tokens"] == 200
    assert sl["hit_pct"] == pytest.approx(0.8)

    # learning had only memo activity; rest stay placeholders.
    le = pipelines["learning"]
    assert le["hit_tokens"] == 50
    assert le["miss_tokens"] == 950
    assert le["hit_pct"] == pytest.approx(0.05)
    le_tasks = {t["task"]: t for t in le["per_task"]}
    assert le_tasks["memo"]["calls"] == 1
    assert le_tasks["vision"]["hit_pct"] is None

    # memory_graph fully empty — placeholder pipeline.
    mg = pipelines["memory_graph"]
    assert mg["calls"] == 0
    assert mg["hit_pct"] is None
    assert all(t["hit_pct"] is None for t in mg["per_task"])
    # Reserved tasks all listed.
    assert {t["task"] for t in mg["per_task"]} == {
        "graph_review", "graph_edge_classifier",
        "reflection_consolidator", "episode_summarizer",
    }


async def test_cache_pipelines_endpoint_returns_null_when_no_cache_data(tracker) -> None:
    """All hit+miss=0 → every pipeline + overall reports hit_pct=None."""
    await _record(tracker, call_type="main", hit=0, miss=0)
    await _record(tracker, call_type="slang", hit=0, miss=0)

    client = _client(tracker)
    resp = client.get("/api/admin/dashboard/cache-pipelines?period=day")
    assert resp.status_code == 200
    data = resp.json()

    assert data["overall"]["hit_pct"] is None
    for p in data["pipelines"]:
        assert p["hit_pct"] is None, f"{p['key']} should be None when no tokens"


async def test_cache_pipelines_endpoint_rejects_bad_period(tracker) -> None:
    client = _client(tracker)
    resp = client.get("/api/admin/dashboard/cache-pipelines?period=hour")
    assert resp.status_code == 200
    assert "error" in resp.json()


async def test_dashboard_endpoint_exposes_cache_hit_pct(tracker) -> None:
    """Hero + panel must read the same numerator/denominator."""
    await _record(tracker, call_type="main",  hit=800, miss=200)
    await _record(tracker, call_type="slang", hit=100, miss=0)

    client = _client(tracker)
    resp = client.get("/api/admin/dashboard")
    assert resp.status_code == 200
    usage = resp.json()["usage"]
    # cache_hit_pct = 900 / 1100
    assert usage["cache_hit_pct"] == pytest.approx(900 / 1100)


async def test_dashboard_endpoint_returns_null_cache_hit_pct_when_empty(tracker) -> None:
    client = _client(tracker)
    resp = client.get("/api/admin/dashboard")
    assert resp.status_code == 200
    assert resp.json()["usage"]["cache_hit_pct"] is None
