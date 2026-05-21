"""Tests for BlockTraceStore + PromptBudgetManager."""

from __future__ import annotations

import pytest

from kernel.types import PromptBlock
from services.block_trace.budget_manager import PromptBudgetManager
from services.block_trace.store import BlockTraceStore
from services.block_trace.types import PromptBlockTrace
from services.episodic.store import _phase_b_unlocked


@pytest.fixture
async def store(tmp_path):
    s = BlockTraceStore(db_path=str(tmp_path / "bt.db"))
    await s.init()
    yield s
    await s.close()


def _make_trace(
    *,
    request_id: str = "req_1",
    source: str = "slang",
    decision: str = "accepted",
    label: str = "群内黑话",
    candidate_id: str = "pbc_aaa",
    priority: int = 40,
    char_count: int = 100,
) -> PromptBlockTrace:
    return PromptBlockTrace(
        trace_id="",
        request_id=request_id,
        task="main",
        source=source,
        provider=source + "_plugin",
        candidate_id=candidate_id,
        decision=decision,
        hit_reason=label,
        evidence_refs=(),
        token_estimate=char_count // 3,
        char_count=char_count,
        position="dynamic",
        label=label,
        priority=priority,
        budget_reason=f"{decision}: test",
    )


# === BlockTraceStore ===


async def test_store_init_creates_table(store: BlockTraceStore) -> None:
    assert store._db is not None
    cursor = await store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='prompt_block_traces'"
    )
    row = await cursor.fetchone()
    assert row is not None


async def test_record_and_list_for_request(store: BlockTraceStore) -> None:
    t1 = _make_trace(request_id="req_A", source="slang", candidate_id="pbc_1")
    t2 = _make_trace(request_id="req_A", source="style", candidate_id="pbc_2", priority=45)
    t3 = _make_trace(request_id="req_B", source="memory", candidate_id="pbc_3")
    await store.record(t1)
    await store.record(t2)
    await store.record(t3)
    result = await store.list_for_request("req_A")
    assert len(result) == 2
    assert all(r.request_id == "req_A" for r in result)


async def test_record_batch(store: BlockTraceStore) -> None:
    traces = [
        _make_trace(candidate_id=f"pbc_{i}", request_id="req_batch")
        for i in range(5)
    ]
    await store.record_batch(traces)
    result = await store.list_for_request("req_batch")
    assert len(result) == 5


async def test_find_by_source_ref(store: BlockTraceStore) -> None:
    t = _make_trace(source="memory", candidate_id="pbc_findme")
    await store.record(t)
    result = await store.find_by_source_ref(source="memory", source_id="pbc_findme")
    assert len(result) == 1
    assert result[0].source == "memory"


async def test_recent(store: BlockTraceStore) -> None:
    for i in range(10):
        await store.record(_make_trace(candidate_id=f"pbc_r{i}", request_id=f"req_{i}"))
    result = await store.recent(limit=5)
    assert len(result) == 5


async def test_stats(store: BlockTraceStore) -> None:
    await store.record(_make_trace(decision="accepted", candidate_id="pbc_s1"))
    await store.record(_make_trace(decision="accepted", candidate_id="pbc_s2"))
    await store.record(_make_trace(decision="trimmed", candidate_id="pbc_s3"))
    s = await store.stats()
    assert s["total"] == 3
    assert s["by_decision"]["accepted"] == 2
    assert s["by_decision"]["trimmed"] == 1


async def test_prune(store: BlockTraceStore) -> None:
    t = _make_trace(candidate_id="pbc_old")
    t_with_old_date = PromptBlockTrace(
        trace_id="bt_old",
        request_id=t.request_id,
        task=t.task,
        source=t.source,
        provider=t.provider,
        candidate_id=t.candidate_id,
        decision=t.decision,
        hit_reason=t.hit_reason,
        evidence_refs=t.evidence_refs,
        token_estimate=t.token_estimate,
        char_count=t.char_count,
        position=t.position,
        label=t.label,
        priority=t.priority,
        budget_reason=t.budget_reason,
        created_at="2020-01-01T00:00:00+08:00",
    )
    await store.record(t_with_old_date)
    await store.record(_make_trace(candidate_id="pbc_new"))
    deleted = await store.prune(keep_days=1)
    assert deleted == 1
    remaining = await store.recent(limit=100)
    assert len(remaining) == 1


# === PromptBudgetManager ===


async def test_budget_manager_accept_all(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=4000)
    blocks = [
        PromptBlock(text="a" * 100, label="test1", position="dynamic", priority=10, source="s1"),
        PromptBlock(text="b" * 200, label="test2", position="dynamic", priority=20, source="s2"),
    ]
    result = mgr.process(blocks, request_id="req_all")
    assert len(result) == 2
    assert result[0].source == "s1"
    assert result[1].source == "s2"


async def test_budget_manager_trim(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=150)
    blocks = [
        PromptBlock(text="a" * 100, label="hi-pri", position="dynamic", priority=10, source="s1"),
        PromptBlock(text="b" * 100, label="lo-pri", position="dynamic", priority=20, source="s2"),
    ]
    result = mgr.process(blocks, request_id="req_trim")
    assert len(result) == 2
    assert len(result[0].text) == 100
    assert len(result[1].text) == 50


async def test_budget_manager_reject(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=80)
    blocks = [
        PromptBlock(text="a" * 80, label="hi-pri", position="dynamic", priority=10, source="s1"),
        PromptBlock(text="b" * 50, label="lo-pri", position="dynamic", priority=20, source="s2"),
    ]
    result = mgr.process(blocks, request_id="req_reject")
    assert len(result) == 1
    assert result[0].source == "s1"


async def test_budget_manager_priority_order(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=150)
    blocks = [
        PromptBlock(text="b" * 100, label="lo-pri", position="dynamic", priority=99, source="lo"),
        PromptBlock(text="a" * 100, label="hi-pri", position="dynamic", priority=5, source="hi"),
    ]
    result = mgr.process(blocks, request_id="req_order")
    assert result[0].source == "hi"
    assert len(result[0].text) == 100
    assert result[1].source == "lo"
    assert len(result[1].text) == 50


async def test_budget_manager_traces_recorded(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=4000)
    blocks = [
        PromptBlock(text="x" * 50, label="t", position="dynamic", priority=10, source="src"),
    ]
    mgr.process(blocks, request_id="req_trace_check")
    import asyncio
    await asyncio.sleep(0.1)
    traces = await store.list_for_request("req_trace_check")
    assert len(traces) >= 1
    assert traces[0].decision == "accepted"


def test_phase_b_gate_unlocked() -> None:
    """With BlockTraceBus shipped and exposing the § 10.2 protocol shape,
    the Phase B gate must be open."""
    assert _phase_b_unlocked() is True


def test_phase_b_gate_locks_when_bus_missing_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a future refactor drops one of the Protocol methods on BlockTraceBus,
    the gate must close — `enabled_for_prompt` should refuse to promote until
    the bus contract is whole again."""
    import services.block_trace as bt_module

    class _StubBus:
        async def record(self, *_a, **_kw): ...
        async def list_for_request(self, *_a, **_kw): ...
        # `find_by_source_ref` deliberately missing

    monkeypatch.setattr(bt_module, "BlockTraceBus", _StubBus)
    assert _phase_b_unlocked() is False


def test_phase_b_gate_locks_when_bus_unimportable(monkeypatch: pytest.MonkeyPatch) -> None:
    """If `services.block_trace` cannot be imported (e.g. broken refactor in
    flight), the gate must close rather than crash transition_state."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "services.block_trace":
            raise ImportError("simulated: block_trace package broken")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert _phase_b_unlocked() is False


def test_block_trace_bus_alias_points_to_store() -> None:
    """`BlockTraceBus` is the architectural name in the multilayer-memory plan
    (§ 10.2 of the report); `BlockTraceStore` is the concrete implementation.
    Lock the alias so callers can write to the architectural name without
    learning the implementation name."""
    from services.block_trace import BlockTraceBus, BlockTraceStore

    assert BlockTraceBus is BlockTraceStore


def test_block_trace_bus_satisfies_protocol_shape() -> None:
    """`BlockTraceBus` must expose `record / list_for_request / find_by_source_ref`
    matching the Protocol drafted in § 10.2 of the multilayer-memory plan."""
    from services.block_trace import BlockTraceBus

    for name in ("record", "list_for_request", "find_by_source_ref"):
        assert hasattr(BlockTraceBus, name), f"BlockTraceBus missing {name}"
