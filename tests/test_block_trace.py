"""Tests for BlockTraceStore + PromptBudgetManager."""

from __future__ import annotations

import pytest

from services.block_trace.budget_manager import PromptBudgetManager
from services.block_trace.llm_call_trace import record_llm_call_trace
from services.block_trace.store import BlockTraceStore
from services.block_trace.types import BudgetDecision, PromptBlockCandidate, PromptBlockTrace
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
    decision: BudgetDecision = "accepted",
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


async def test_record_llm_call_trace_links_semantic_gate_and_thinker(
    store: BlockTraceStore,
) -> None:
    request_id = "u13_double_haiku:group_100:msg_42"

    await record_llm_call_trace(
        store,
        request_id=request_id,
        task="reply_gate",
        provider="semantic_gate",
        session_id="group_100",
        group_id="100",
        user_id="200",
        event_id="42",
        metadata={"candidate_reason": "short_contextual_candidate"},
    )
    await record_llm_call_trace(
        store,
        request_id=request_id,
        task="thinker",
        provider="thinker",
        session_id="group_100",
        group_id="100",
        user_id="200",
        turn_id="group_100:123456",
        metadata={"action": "speak", "source": "pre_reply_thinker"},
    )

    traces = await store.list_for_request(request_id)

    assert {trace.provider for trace in traces} == {"semantic_gate", "thinker"}
    assert {trace.decision for trace in traces} == {"shadow_only"}
    assert {trace.metadata["session_id"] for trace in traces} == {"group_100"}
    assert traces[0].metadata["observer"] == "u13_double_haiku_trace"


async def test_record_llm_call_trace_is_fail_soft() -> None:
    class _BrokenStore:
        async def record(self, _trace: PromptBlockTrace) -> None:
            raise RuntimeError("boom")

    await record_llm_call_trace(
        _BrokenStore(),
        request_id="u13_double_haiku:broken",
        task="thinker",
        provider="thinker",
    )


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


def _make_candidate(
    *,
    text: str,
    label: str,
    priority: int,
    source: str,
    candidate_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    hit_reason: str | None = None,
) -> PromptBlockCandidate:
    return PromptBlockCandidate(
        candidate_id=candidate_id or f"pbc_{source}",
        source=source,
        provider=f"{source}_provider",
        layer="dynamic",
        label=label,
        text=text,
        priority=priority,
        position="dynamic",
        scope="group",
        group_id="100",
        hit_reason=hit_reason or f"{source}_test",
        char_count=len(text),
        evidence_refs=evidence_refs,
        metadata={"source": source},
    )


class _ObservationStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.records: list[tuple[str, dict]] = []

    async def record_observation(self, target_id: str, **kwargs) -> bool:
        if self.fail:
            raise RuntimeError("observation boom")
        self.records.append((target_id, kwargs))
        return True


async def test_budget_manager_accept_all(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=4000)
    candidates = [
        _make_candidate(text="a" * 100, label="test1", priority=10, source="s1"),
        _make_candidate(text="b" * 200, label="test2", priority=20, source="s2"),
    ]
    result, accepted = mgr.process(candidates, request_id="req_all")
    assert len(result) == 2
    assert len(accepted) == 2
    assert result[0].source == "s1"
    assert result[1].source == "s2"
    assert accepted[0].source == "s1"
    assert accepted[1].source == "s2"


async def test_budget_manager_trim(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=150)
    candidates = [
        _make_candidate(text="a" * 100, label="hi-pri", priority=10, source="s1"),
        _make_candidate(text="b" * 100, label="lo-pri", priority=20, source="s2"),
    ]
    result, accepted = mgr.process(candidates, request_id="req_trim")
    assert len(result) == 2
    assert len(accepted) == 1
    assert len(result[0].text) == 100
    assert len(result[1].text) == 50
    assert accepted[0].source == "s1"


async def test_budget_manager_reject(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=80)
    candidates = [
        _make_candidate(text="a" * 80, label="hi-pri", priority=10, source="s1"),
        _make_candidate(text="b" * 50, label="lo-pri", priority=20, source="s2"),
    ]
    result, accepted = mgr.process(candidates, request_id="req_reject")
    assert len(result) == 1
    assert len(accepted) == 1
    assert result[0].source == "s1"


async def test_budget_manager_priority_order(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=150)
    candidates = [
        _make_candidate(text="b" * 100, label="lo-pri", priority=99, source="lo"),
        _make_candidate(text="a" * 100, label="hi-pri", priority=5, source="hi"),
    ]
    result, accepted = mgr.process(candidates, request_id="req_order")
    assert result[0].source == "hi"
    assert len(result[0].text) == 100
    assert result[1].source == "lo"
    assert len(result[1].text) == 50
    assert [item.source for item in accepted] == ["hi"]


async def test_budget_manager_traces_recorded(store: BlockTraceStore) -> None:
    mgr = PromptBudgetManager(store, max_dynamic_chars=4000)
    candidates = [
        _make_candidate(
            text="x" * 50,
            label="t",
            priority=10,
            source="src",
            candidate_id="pbc_trace_src",
            evidence_refs=("ref_1",),
        ),
    ]
    result, accepted = mgr.process(candidates, request_id="req_trace_check")
    assert len(result) == 1
    assert len(accepted) == 1
    assert accepted[0].candidate_id == "pbc_trace_src"
    assert accepted[0].evidence_refs == ("ref_1",)
    import asyncio
    await asyncio.sleep(0.1)
    traces = await store.list_for_request("req_trace_check")
    assert len(traces) >= 1
    assert traces[0].decision == "accepted"
    assert traces[0].candidate_id == "pbc_trace_src"
    assert traces[0].evidence_refs == ("ref_1",)


async def test_budget_manager_records_style_and_episode_observations_for_accepted(
    store: BlockTraceStore,
) -> None:
    import asyncio

    style_store = _ObservationStore()
    episode_store = _ObservationStore()
    mgr = PromptBudgetManager(
        store,
        max_dynamic_chars=4000,
        style_store_getter=lambda: style_store,
        episode_store_getter=lambda: episode_store,
    )
    candidates = [
        _make_candidate(
            text="style",
            label="style profile",
            priority=10,
            source="style",
            candidate_id="pbc_style_profile",
            evidence_refs=("expr_1", "expr_2"),
            hit_reason="style_profile_injection",
        ),
        _make_candidate(
            text="episode",
            label="episode recall",
            priority=20,
            source="episode",
            candidate_id="pbc_episode",
            evidence_refs=("ep_1", "ep_2"),
            hit_reason="episode_recall_enabled_for_prompt",
        ),
    ]

    _blocks, accepted = mgr.process(candidates, request_id="req_obs", group_id="100")

    assert [item.candidate_id for item in accepted] == ["pbc_style_profile", "pbc_episode"]
    await asyncio.sleep(0.1)
    assert [record[0] for record in style_store.records] == ["expr_1", "expr_2"]
    assert [record[1]["trigger_type"] for record in style_store.records] == [
        "profile_inject",
        "profile_inject",
    ]
    assert all(record[1]["message_id"] == "req_obs" for record in style_store.records)
    assert [record[0] for record in episode_store.records] == ["ep_1", "ep_2"]
    assert all(record[1]["trigger_type"] == "episode_inject" for record in episode_store.records)


async def test_budget_manager_records_slang_observations_for_accepted(
    store: BlockTraceStore,
) -> None:
    import asyncio

    slang_store = _ObservationStore()
    mgr = PromptBudgetManager(
        store,
        max_dynamic_chars=4000,
        slang_store_getter=lambda: slang_store,
    )
    candidates = [
        _make_candidate(
            text="slang",
            label="群内黑话",
            priority=10,
            source="slang",
            candidate_id="pbc_slang",
            evidence_refs=("term_1", "term_2", "term_1"),
            hit_reason="slang_injection",
        ),
    ]

    _blocks, accepted = mgr.process(candidates, request_id="req_slang_obs", group_id="100")

    assert [item.candidate_id for item in accepted] == ["pbc_slang"]
    await asyncio.sleep(0.1)
    assert [record[0] for record in slang_store.records] == ["term_1", "term_2"]
    assert all(record[1]["group_id"] == "100" for record in slang_store.records)
    assert all(record[1]["reason"] == "prompt_inject:req_slang_obs" for record in slang_store.records)


async def test_budget_manager_records_trimmed_but_not_rejected_observations(
    store: BlockTraceStore,
) -> None:
    import asyncio

    style_store = _ObservationStore()
    slang_store = _ObservationStore()
    mgr = PromptBudgetManager(
        store,
        max_dynamic_chars=100,
        slang_store_getter=lambda: slang_store,
        style_store_getter=lambda: style_store,
    )
    candidates = [
        _make_candidate(
            text="a" * 40,
            label="accepted",
            priority=10,
            source="style",
            candidate_id="pbc_accepted",
            evidence_refs=("expr_accepted",),
            hit_reason="style_expression_injection",
        ),
        _make_candidate(
            text="b" * 80,
            label="trimmed",
            priority=20,
            source="style",
            candidate_id="pbc_trimmed",
            evidence_refs=("expr_trimmed",),
            hit_reason="style_expression_injection",
        ),
        _make_candidate(
            text="c" * 10,
            label="rejected",
            priority=30,
            source="style",
            candidate_id="pbc_rejected",
            evidence_refs=("expr_rejected",),
            hit_reason="style_expression_injection",
        ),
        _make_candidate(
            text="d" * 10,
            label="slang rejected",
            priority=40,
            source="slang",
            candidate_id="pbc_slang_rejected",
            evidence_refs=("term_rejected",),
            hit_reason="slang_injection",
        ),
    ]

    _blocks, accepted = mgr.process(candidates, request_id="req_budget_obs")

    assert [item.candidate_id for item in accepted] == ["pbc_accepted"]
    await asyncio.sleep(0.1)
    assert [record[0] for record in style_store.records] == ["expr_accepted", "expr_trimmed"]
    assert [record[1]["trigger_type"] for record in style_store.records] == [
        "expression_inject",
        "expression_inject_trimmed",
    ]
    assert style_store.records[1][1]["meta"]["budget_decision"] == "trimmed"
    assert slang_store.records == []
    traces = await store.list_for_request("req_budget_obs")
    decisions = {trace.candidate_id: trace.decision for trace in traces}
    assert decisions == {
        "pbc_accepted": "accepted",
        "pbc_trimmed": "trimmed",
        "pbc_rejected": "rejected",
        "pbc_slang_rejected": "rejected",
    }


async def test_budget_manager_records_trimmed_observations_for_all_hit_sources(
    store: BlockTraceStore,
) -> None:
    import asyncio

    slang_store = _ObservationStore()
    style_store = _ObservationStore()
    episode_store = _ObservationStore()

    PromptBudgetManager(
        store,
        max_dynamic_chars=20,
        slang_store_getter=lambda: slang_store,
    ).process([
        _make_candidate(
            text="s" * 40,
            label="slang trimmed",
            priority=10,
            source="slang",
            candidate_id="pbc_slang_trimmed",
            evidence_refs=("term_trimmed", "term_trimmed"),
            hit_reason="slang_injection",
        ),
    ], request_id="req_slang_trimmed", group_id="100")

    PromptBudgetManager(
        store,
        max_dynamic_chars=20,
        style_store_getter=lambda: style_store,
    ).process([
        _make_candidate(
            text="p" * 40,
            label="style profile trimmed",
            priority=10,
            source="style",
            candidate_id="pbc_style_profile_trimmed",
            evidence_refs=("profile_trimmed",),
            hit_reason="style_profile_injection",
        ),
    ], request_id="req_style_trimmed", group_id="100")

    PromptBudgetManager(
        store,
        max_dynamic_chars=20,
        episode_store_getter=lambda: episode_store,
    ).process([
        _make_candidate(
            text="e" * 40,
            label="episode trimmed",
            priority=10,
            source="episode",
            candidate_id="pbc_episode_trimmed",
            evidence_refs=("ep_trimmed",),
            hit_reason="episode_recall_enabled_for_prompt",
        ),
    ], request_id="req_episode_trimmed", group_id="100")

    await asyncio.sleep(0.1)

    assert slang_store.records == [
        ("term_trimmed", {
            "group_id": "100",
            "raw_text": "",
            "context": "",
            "reason": "prompt_inject_trimmed:req_slang_trimmed",
        }),
    ]
    assert style_store.records[0][0] == "profile_trimmed"
    assert style_store.records[0][1]["trigger_type"] == "profile_inject_trimmed"
    assert style_store.records[0][1]["meta"]["budget_decision"] == "trimmed"
    assert episode_store.records[0][0] == "ep_trimmed"
    assert episode_store.records[0][1]["trigger_type"] == "episode_inject_trimmed"
    assert episode_store.records[0][1]["meta"]["budget_decision"] == "trimmed"


async def test_budget_manager_observation_failures_do_not_break_process(
    store: BlockTraceStore,
) -> None:
    import asyncio

    style_store = _ObservationStore(fail=True)
    mgr = PromptBudgetManager(
        store,
        max_dynamic_chars=4000,
        style_store_getter=lambda: style_store,
    )
    candidates = [
        _make_candidate(
            text="style",
            label="style",
            priority=10,
            source="style",
            candidate_id="pbc_style_fail",
            evidence_refs=("expr_1",),
            hit_reason="style_expression_injection",
        ),
    ]

    blocks, accepted = mgr.process(candidates, request_id="req_obs_fail")

    assert len(blocks) == 1
    assert len(accepted) == 1
    await asyncio.sleep(0.1)
    assert style_store.records == []


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
