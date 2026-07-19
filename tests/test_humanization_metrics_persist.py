from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from kernel.types import PluginContext
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
    assert stats["persona_drift_hits"] == 0
    assert stats["schedule_overshare_hits"] == 0
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
            "persona_drift_hits": 2,
            "persona_drift_rewritten": 2,
            "schedule_overshare_hits": 1,
            "schedule_overshare_rewritten": 1,
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
    assert stats["persona_drift_hits"] == 2
    assert stats["persona_drift_rewritten"] == 2
    assert stats["schedule_overshare_hits"] == 1
    assert stats["schedule_overshare_rewritten"] == 1
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


async def test_runtime_metric_stats_aggregate_anchor_and_slang_events(
    store: BlockTraceStore,
) -> None:
    await store.record_runtime_metric(metric_key="anchor_reinject_count", group_id="100", amount=1)
    await store.record_runtime_metric(metric_key="anchor_reinject_count", group_id="100", amount=1)
    await store.record_runtime_metric(metric_key="slang_lookup_resolved", group_id="100", amount=3)
    await store.record_runtime_metric(metric_key="slang_lookup_unresolved", group_id="100", amount=2)

    stats = await store.stats()

    assert stats["anchor_reinject_count"] == 2
    assert stats["slang_lookup_resolved"] == 3
    assert stats["slang_lookup_unresolved"] == 2


async def test_runtime_metric_stats_aggregate_qzone_journal_events(
    store: BlockTraceStore,
) -> None:
    await store.record_runtime_metric(metric_key="qzone_draft_created", amount=2)
    await store.record_runtime_metric(metric_key="qzone_draft_rejected", amount=4)
    await store.record_runtime_metric(
        metric_key="qzone_selection_decision",
        amount=5,
        metadata={"reason": "reject_out_ranked", "source": "event_replan"},
    )
    await store.record_runtime_metric(metric_key="qzone_publish_dry_run", amount=1)
    await store.record_runtime_metric(metric_key="qzone_publish_succeeded", amount=1)
    await store.record_runtime_metric(metric_key="qzone_publish_unknown", amount=3)

    stats = await store.stats()

    assert stats["qzone_draft_created"] == 2
    assert stats["qzone_draft_rejected"] == 4
    assert stats["qzone_selection_decision"] == 5
    assert stats["qzone_publish_dry_run"] == 1
    assert stats["qzone_publish_succeeded"] == 1
    assert stats["qzone_publish_unknown"] == 3


async def test_qzone_selection_metrics_never_persist_adversarial_raw_source(
    tmp_path: Path,
) -> None:
    """Contract 2: adversarial raw source text must not land in runtime metric metadata.

    UIN / cookie / bearer / assignment-shaped secrets in untrusted source labels must
    become a closed safe code such as ``unknown`` in qzone_selection_decision metadata.
    A poison review field on an official source must also stay out of
    qzone_draft_rejected metadata.
    """
    from plugins.qzone_journal.plugin import PluginConfig, QZoneJournalPlugin

    class _LLMProbe:
        def __init__(self) -> None:
            self.calls: list[object] = []

        async def __call__(self, request: object) -> dict[str, str]:
            self.calls.append(request)
            return {"text": "should-not-compose"}

        async def _call(self, request: object) -> dict[str, str]:
            return await self(request)

    class _StoryArcStore:
        def __init__(self, events: list[dict[str, object]]) -> None:
            self._arc = SimpleNamespace(last_events=[dict(e) for e in events])

        def load_active(self) -> SimpleNamespace:
            return self._arc

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    adversarial_sources = [
        "uin=384801062",
        "cookie=p_skey=SECRETCOOKIEVALUE",
        "Bearer sk-live-adversarial-token-001",
        "token=abc123&password=hunter2",
    ]
    events: list[dict[str, object]] = [
        {
            "source": source,
            "date": today,
            "event_id": f"adv-{idx}",
            "summary": "对抗性来源不应进入 metric 元数据",
            "salience": 0.95,
            "subject_kind": "fiction",
            "privacy": "public",
        }
        for idx, source in enumerate(adversarial_sources)
    ]
    # Force qzone_draft_rejected with a secret-laden review field while retaining
    # the closed official source contract.
    events.append(
        {
            "source": "event_replan",
            "date": today,
            "event_id": "384801062",
            "summary": "毒字段触发 draft_rejected",
            "salience": 0.99,
            "subject_kind": "fiction",
            "privacy": "public",
        }
    )

    trace_store = BlockTraceStore(db_path=tmp_path / "trace-qzone-adv.db")
    await trace_store.init()
    llm = _LLMProbe()
    plugin = QZoneJournalPlugin(
        config=PluginConfig(
            enabled=True,
            dry_run=True,
            allow_live_publish=False,
            manual_review=True,
        )
    )
    schedule = SimpleNamespace(date=today, day_narrative="和平常一样的一天", slots=[])
    ctx = PluginContext(
        storage_dir=tmp_path,
        plugin_data_dir=tmp_path / "plugins",
        llm_client=llm,
        schedule_store=SimpleNamespace(current=schedule),
        story_arc_store=_StoryArcStore(events),
        block_trace_store=trace_store,
    )

    try:
        await plugin.on_startup(ctx)
        try:
            await plugin.on_tick(ctx)
        finally:
            await plugin.on_shutdown(ctx)

        rows = await trace_store.list_runtime_metrics()
        qzone_rows = [
            row
            for row in rows
            if row["metric_key"]
            in {"qzone_selection_decision", "qzone_draft_rejected"}
        ]
        assert qzone_rows, "tick must emit qzone selection/reject runtime metrics"
        metric_keys = {row["metric_key"] for row in qzone_rows}
        assert "qzone_selection_decision" in metric_keys
        assert "qzone_draft_rejected" in metric_keys

        forbidden_fragments = (
            "384801062",
            "p_skey",
            "SECRETCOOKIEVALUE",
            "Bearer",
            "sk-live-adversarial-token-001",
            "token=abc123",
            "password=hunter2",
            "cookie=",
            "uin=",
        )
        for row in qzone_rows:
            meta = row.get("metadata") or {}
            blob = json.dumps(meta, ensure_ascii=False)
            for fragment in forbidden_fragments:
                assert fragment not in blob, (
                    f"metric {row['metric_key']} metadata leaked {fragment!r}: {meta!r}"
                )
            source_value = str(meta.get("source", "") or "")
            # Unknown/untrusted adversarial sources collapse to a closed safe code.
            assert source_value in {
                "",
                "unknown",
                "event_replan",
                "dream_reflection",
                "schedule_generator",
            }, f"unsafe source persisted: {source_value!r}"
    finally:
        await trace_store.close()


async def test_client_record_runtime_metric_writes_through_store(
    store: BlockTraceStore,
) -> None:
    """The LLMClient helper must persist a queryable runtime_metric_events row.

    Regression guard for the anchor/slang observability埋点: a no-op helper (the
    pre-fix state) would leave these features invisible no matter how much traffic
    flows. Asserts external observable state (a DB row), not a return value.
    """
    from services.llm.client import LLMClient

    class _FakeBudgetManager:
        def __init__(self, trace_store: BlockTraceStore) -> None:
            self._store = trace_store

    client = LLMClient.__new__(LLMClient)
    client._budget_manager = _FakeBudgetManager(store)  # type: ignore[attr-defined]

    await client._record_runtime_metric(
        metric_key="anchor_reinject_count",
        group_id="984198159",
        metadata={"anchor_turn": 6},
    )
    await client._record_runtime_metric(
        metric_key="slang_lookup_resolved",
        group_id="984198159",
        amount=2,
        metadata={"requested": 3, "sources": {"local_db": 2}},
    )

    rows = await store.list_runtime_metrics(group_id="984198159")
    by_key = {row["metric_key"]: row for row in rows}
    assert by_key["anchor_reinject_count"]["amount"] == 1
    assert by_key["anchor_reinject_count"]["metadata"] == {"anchor_turn": 6}
    assert by_key["slang_lookup_resolved"]["amount"] == 2
    assert by_key["slang_lookup_resolved"]["metadata"]["sources"] == {"local_db": 2}


async def test_client_record_runtime_metric_silent_without_store() -> None:
    """Helper must be a safe no-op when the store handle is absent (boot/degraded)."""
    from services.llm.client import LLMClient

    class _NoStoreBudgetManager:
        pass

    client = LLMClient.__new__(LLMClient)
    client._budget_manager = _NoStoreBudgetManager()  # type: ignore[attr-defined]

    # Must not raise even though no store is reachable.
    await client._record_runtime_metric(metric_key="anchor_reinject_count", group_id="1")
