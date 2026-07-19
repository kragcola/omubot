"""Evidence-use / temporal-trace offline eval gate (memory-longmem-evidence-use-eval-gate-v1).

Honest capability-dimension gate over ContextService ordinary hits + TemporalTraceAssembler
structured fields. Not official LongMemEval-V2 / LoCoMo / MemTrace leaderboard parity.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from services.context import (
    ContextService,
    MemoryContextSource,
    evaluate_context_case,
    evaluate_context_cases,
    load_context_eval_cases,
)
from services.context.eval import ContextEvalCase, TemporalTraceExpectation
from services.memory.card_store import CardStore, NewCard
from services.memory.temporal_trace import TemporalTraceAssembler, TemporalTraceConfig

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "context_eval" / "long_memory_evidence_use_v1.json"
)
BASIC_FIXTURE = Path(__file__).parent / "fixtures" / "context_eval" / "basic.json"
LONG_MEMORY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "context_eval" / "long_memory_frontier.json"
)


# ---------------------------------------------------------------------------
# Schema / round-trip contracts (RED before production implementation)
# ---------------------------------------------------------------------------


def test_context_eval_case_accepts_current_message_and_rewritten_query() -> None:
    case = ContextEvalCase.from_dict({
        "id": "schema-msg",
        "query": "检索查询",
        "current_message": "我不是还住杭州吗",
        "rewritten_query": "我住在哪里",
    })
    assert case.current_message == "我不是还住杭州吗"
    assert case.rewritten_query == "我住在哪里"
    payload = case.to_dict()
    assert payload["current_message"] == "我不是还住杭州吗"
    assert payload["rewritten_query"] == "我住在哪里"
    assert "trace" not in payload or payload.get("trace") in (None, {})


def test_temporal_trace_expectation_round_trip() -> None:
    raw = {
        "expected_present": True,
        "reason": "premise_conflict",
        "required_current_contains": ["上海"],
        "forbidden_current_contains": ["杭州"],
        "required_earlier_contains": ["杭州"],
        "forbidden_earlier_contains": ["错误更早"],
        "required_trajectory_contains": ["杭州", "上海"],
        "forbidden_trajectory_contains": ["北京"],
        "required_text_contains": ["当前"],
        "forbidden_text_contains": ["泄漏"],
        "required_evidence_refs": ["card:abc"],
        "required_evidence_ref_prefixes": ["card:", "message:"],
        "forbidden_evidence_refs": ["card:bad"],
        "forbidden_evidence_ref_prefixes": ["obs:"],
        "max_knowledge_points": 2,
        "max_trace_chars": 600,
    }
    exp = TemporalTraceExpectation.from_dict(raw)
    assert exp is not None
    assert exp.expected_present is True
    assert exp.reason == "premise_conflict"
    assert exp.required_current_contains == ["上海"]
    assert exp.required_evidence_ref_prefixes == ["card:", "message:"]
    assert exp.max_knowledge_points == 2
    assert exp.max_trace_chars == 600
    back = exp.to_dict()
    assert back["reason"] == "premise_conflict"
    assert back["required_trajectory_contains"] == ["杭州", "上海"]
    reloaded = TemporalTraceExpectation.from_dict(back)
    assert reloaded is not None
    assert reloaded.reason == "premise_conflict"


@pytest.mark.parametrize(
    "trace",
    [
        {"expected_present": "false"},
        {"expected_present": 1},
        {"required_current_contains": "上海"},
        {"required_current_contains": ["上海", 1]},
        {"max_knowledge_points": "2"},
        {"unknown_expectation_key": True},
    ],
)
def test_temporal_trace_expectation_rejects_invalid_schema(trace: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)):
        TemporalTraceExpectation.from_dict(trace)


@pytest.mark.parametrize(
    "trace",
    [
        {"expected_present": False, "reason": "premise_conflict"},
        {"expected_present": False, "required_current_contains": ["上海"]},
        {"expected_present": False, "required_evidence_refs": ["card:c1"]},
    ],
)
def test_temporal_trace_expectation_rejects_absent_required_contradiction(
    trace: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="expected_present=false"):
        TemporalTraceExpectation.from_dict(trace)


@pytest.mark.parametrize("trace", ["invalid", ["invalid"], 1, True])
def test_context_eval_case_rejects_non_mapping_trace(trace: Any) -> None:
    with pytest.raises(TypeError, match="trace expectation must be a dict"):
        ContextEvalCase.from_dict({"id": "invalid-trace", "query": "q", "trace": trace})


def test_legacy_case_dict_round_trip_without_trace_keys() -> None:
    legacy = {
        "id": "legacy-basic",
        "query": "q",
        "user_id": "u",
        "required_hits": [{"contains": "x"}],
        "forbidden_hits": [{"contains": "y"}],
        "max_duplicate_hits": 0,
    }
    case = ContextEvalCase.from_dict(legacy)
    payload = case.to_dict()
    assert payload["id"] == "legacy-basic"
    assert payload["query"] == "q"
    assert payload.get("current_message", "") == ""
    assert payload.get("rewritten_query", "") == ""
    # Additive only: no forced non-empty trace blob for legacy cases.
    reloaded = ContextEvalCase.from_dict(payload)
    assert reloaded.id == case.id
    assert reloaded.required_hits[0].contains == "x"


def test_result_summary_to_dict_preserves_legacy_keys_and_adds_trace() -> None:
    from services.context.eval import ContextEvalResult, ContextEvalSummary

    result = ContextEvalResult(
        case_id="c1",
        query="q",
        passed=True,
        hit_count=1,
        pack_chars=10,
        omitted_count=0,
        required_total=1,
        required_matched=1,
        required_recall=1.0,
    )
    d = result.to_dict()
    for key in (
        "case_id",
        "query",
        "passed",
        "hit_count",
        "pack_chars",
        "omitted_count",
        "required_total",
        "required_matched",
        "required_recall",
        "missing_required",
        "forbidden_violations",
        "duplicate_count",
        "max_pack_chars",
        "pack_budget_exceeded",
        "max_hit_count",
        "hit_count_exceeded",
        "error",
    ):
        assert key in d
    for key in (
        "trace_checked",
        "trace_present",
        "trace_reason",
        "trace_chars",
        "trace_violations",
        "missing_evidence_use",
    ):
        assert key in d

    summary = ContextEvalSummary(
        total_cases=1,
        passed_cases=1,
        required_total=1,
        required_matched=1,
        forbidden_violations=0,
        duplicate_hits=0,
        pack_budget_violations=0,
        hit_count_violations=0,
        avg_pack_chars=10.0,
        results=[result],
    )
    sd = summary.to_dict()
    for key in (
        "total_cases",
        "passed_cases",
        "pass_rate",
        "required_total",
        "required_matched",
        "required_hit_recall",
        "forbidden_violations",
        "duplicate_hits",
        "pack_budget_violations",
        "hit_count_violations",
        "avg_pack_chars",
        "results",
    ):
        assert key in sd
    assert "trace_violations" in sd


# ---------------------------------------------------------------------------
# Fail-closed / scoring contracts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e8_missing_assembler_with_trace_expectation_fails_closed() -> None:
    case = ContextEvalCase.from_dict({
        "id": "e8-missing-assembler",
        "query": "我以前住在哪里",
        "current_message": "我以前住在哪里",
        "trace": {"expected_present": True, "reason": "historical_intent"},
    })
    service = ContextService([])
    result = await evaluate_context_case(service, case)
    assert result.passed is False
    assert result.trace_checked is True
    assert result.trace_present is False
    payload = result.to_dict()
    assert payload["error"] == "missing_trace_assembler" or any(
        "missing_trace_assembler" in str(v) for v in payload.get("trace_violations", [])
    )
    assert payload["trace_violations"] or payload["missing_evidence_use"] or payload["error"]


@pytest.mark.asyncio
async def test_reason_only_expectation_requires_trace_presence() -> None:
    class _Absent:
        async def assemble(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            return None

    case = ContextEvalCase.from_dict({
        "id": "reason-implies-presence",
        "query": "我以前住在哪里",
        "trace": {"reason": "historical_intent"},
    })
    result = await evaluate_context_case(ContextService([]), case, trace_assembler=_Absent())

    assert result.passed is False
    assert result.trace_checked is True
    assert result.trace_present is False
    assert any(item["code"] == "trace_expected_present" for item in result.trace_violations)


@pytest.mark.asyncio
async def test_direct_absent_required_expectation_cannot_fail_open() -> None:
    class _Absent:
        async def assemble(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            return None

    case = ContextEvalCase(
        id="direct-contradiction",
        query="q",
        trace=TemporalTraceExpectation(
            expected_present=False,
            required_current_contains=["上海"],
        ),
    )
    result = await evaluate_context_case(ContextService([]), case, trace_assembler=_Absent())

    assert result.passed is False
    assert any(
        item["code"] == "contradictory_trace_expectation"
        for item in result.trace_violations
    )


@pytest.mark.asyncio
async def test_no_trace_expectation_without_assembler_behaves_as_before() -> None:
    service = ContextService([])
    case = ContextEvalCase(
        id="no-trace",
        query="hello",
        required_hits=[],
        max_hit_count=0,
    )
    result = await evaluate_context_case(service, case)
    assert result.passed is True
    assert result.trace_checked is False
    assert result.error == ""


@pytest.mark.asyncio
async def test_trace_assembler_exception_fails_case_not_pass() -> None:
    class _Boom:
        async def assemble(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise RuntimeError("assembler exploded")

    case = ContextEvalCase.from_dict({
        "id": "assembler-boom",
        "query": "q",
        "current_message": "我以前住在杭州吗",
        "trace": {"expected_present": True},
    })
    service = ContextService([])
    result = await evaluate_context_case(service, case, trace_assembler=_Boom())
    assert result.passed is False
    assert (
        result.error in {"RuntimeError", "trace_assembler_error"}
        or "error" in result.error.lower()
        or result.trace_violations
    )


@pytest.mark.parametrize(
    "malformed_trace",
    [
        {},
        object(),
        {"text": "trace", "reason": "premise_conflict", "knowledge_points": []},
        {
            "text": "trace",
            "reason": "premise_conflict",
            "knowledge_points": [{
                "current": {"content": "上海"},
                "earlier": "杭州",
                "trajectory": "杭州 -> 上海",
                "evidence_refs": "card:c1",
            }],
        },
    ],
)
@pytest.mark.asyncio
async def test_expected_present_rejects_structurally_malformed_trace(
    malformed_trace: Any,
) -> None:
    class _Malformed:
        async def assemble(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            return malformed_trace

    case = ContextEvalCase.from_dict({
        "id": "malformed-present",
        "query": "q",
        "trace": {"expected_present": True},
    })
    result = await evaluate_context_case(
        ContextService([]),
        case,
        trace_assembler=_Malformed(),
    )

    assert result.passed is False
    assert result.trace_present is True
    assert any(item["code"] == "trace_malformed" for item in result.trace_violations)


@pytest.mark.asyncio
async def test_trace_assembler_cancellation_propagates() -> None:
    class _Cancel:
        async def assemble(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise asyncio.CancelledError()

    case = ContextEvalCase.from_dict({
        "id": "assembler-cancel",
        "query": "q",
        "current_message": "我以前住在哪里",
        "trace": {"expected_present": True},
    })
    service = ContextService([])
    with pytest.raises(asyncio.CancelledError):
        await evaluate_context_case(service, case, trace_assembler=_Cancel())


@pytest.mark.asyncio
async def test_e9_structured_current_earlier_trajectory_mismatches_readable() -> None:
    class _FakeTrace:
        def __init__(self) -> None:
            self.text = "当前: 用户住在上海\n更早: 用户住在杭州\n轨迹: 杭州 → 上海"
            self.reason = "premise_conflict"
            self.knowledge_points = [
                type("KP", (), {
                    "current": type("N", (), {"content": "用户住在上海", "card_id": "c1"})(),
                    "earlier": [type("N", (), {"content": "用户住在杭州", "card_id": "c0"})()],
                    "trajectory": "杭州 → 上海",
                    "evidence_refs": ["card:c1", "message:m1"],
                    "reason": "premise_conflict",
                    "confidence": 0.9,
                })()
            ]

    class _FakeAsm:
        async def assemble(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            return _FakeTrace()

    case = ContextEvalCase.from_dict({
        "id": "e9-mismatch",
        "query": "q",
        "current_message": "我不是还住杭州吗",
        "trace": {
            "expected_present": True,
            "reason": "premise_conflict",
            "required_current_contains": ["北京"],
            "forbidden_earlier_contains": ["杭州"],
            "required_trajectory_contains": ["广州"],
        },
    })
    service = ContextService([])
    result = await evaluate_context_case(service, case, trace_assembler=_FakeAsm())
    assert result.passed is False
    assert result.trace_checked is True
    assert result.trace_present is True
    assert result.trace_reason == "premise_conflict"
    violations = result.to_dict()["trace_violations"]
    joined = json.dumps(violations, ensure_ascii=False)
    assert "current" in joined or "required_current" in joined
    assert "earlier" in joined or "forbidden_earlier" in joined
    assert "trajectory" in joined or "required_trajectory" in joined


@pytest.mark.asyncio
async def test_required_evidence_must_co_locate_in_one_knowledge_point() -> None:
    trace = {
        "text": "two unrelated temporal traces",
        "reason": "premise_conflict",
        "knowledge_points": [
            {
                "current": {"content": "用户住在上海"},
                "earlier": [{"content": "用户住在南京"}],
                "trajectory": "南京 -> 上海",
                "evidence_refs": ["card:c1"],
                "reason": "premise_conflict",
            },
            {
                "current": {"content": "用户住在北京"},
                "earlier": [{"content": "用户住在杭州"}],
                "trajectory": "杭州 -> 北京",
                "evidence_refs": ["message:m2"],
                "reason": "premise_conflict",
            },
        ],
    }

    class _Split:
        async def assemble(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            return trace

    case = ContextEvalCase.from_dict({
        "id": "split-kp",
        "query": "住哪里",
        "trace": {
            "expected_present": True,
            "reason": "premise_conflict",
            "required_current_contains": ["上海"],
            "required_earlier_contains": ["杭州"],
            "required_trajectory_contains": ["杭州", "上海"],
            "required_evidence_ref_prefixes": ["card:", "message:"],
        },
    })
    result = await evaluate_context_case(
        ContextService([]),
        case,
        trace_assembler=_Split(),
    )

    assert result.passed is False
    assert any(
        item["code"] == "trace_required_fields_split_across_knowledge_points"
        for item in result.trace_violations
    )


@pytest.mark.asyncio
async def test_evaluate_forwards_active_memory_hits_and_scope_to_assembler() -> None:
    captured: dict[str, Any] = {}

    class _CaptureAsm:
        async def assemble(self, *args: Any, **kwargs: Any) -> None:
            del args
            captured.update(kwargs)
            return None

    class _HitSource:
        name = "memory"

        async def search(
            self,
            query: str,
            *,
            user_id: str = "",
            group_id: str | None = None,
            top_k: int = 8,
            **kwargs: Any,
        ) -> list[Any]:
            del query, user_id, group_id, top_k, kwargs
            from services.context.types import ContextHit

            return [
                ContextHit(
                    id="card_hit_1",
                    type="memory_card",
                    content="用户住在上海",
                    score=0.9,
                    source="memory",
                )
            ]

    service = ContextService([_HitSource()])
    case = ContextEvalCase.from_dict({
        "id": "forward-scope",
        "query": "住哪里",
        "current_message": "我不是还住杭州吗",
        "rewritten_query": "住哪里",
        "session_id": "sess-1",
        "user_id": "u_a",
        "group_id": None,
        "trace": {"expected_present": False},
    })
    await evaluate_context_case(service, case, trace_assembler=_CaptureAsm())
    assert captured.get("current_message") == "我不是还住杭州吗"
    assert captured.get("rewritten_query") == "住哪里"
    assert captured.get("session_id") == "sess-1"
    assert captured.get("user_id") == "u_a"
    hits = captured.get("active_memory_hits")
    assert hits is not None
    assert any(getattr(h, "id", None) == "card_hit_1" for h in hits)


# ---------------------------------------------------------------------------
# Real CardStore + TemporalTraceAssembler integration (E1–E7, E10, fixture)
# ---------------------------------------------------------------------------


async def _seed_evidence_use_store(store: CardStore) -> tuple[str, str]:
    # Content mirrors long_memory_frontier wording so ngram/keyword ordinary
    # retrieval remains realistic without changing production ranking.
    hangzhou = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="u_a",
            content="以前住在杭州",
            source="evidence_use_fixture",
        ),
        source_msg_id="msg-hz",
        captured_at="2026-01-10T10:00:00+08:00",
        captured_by="evidence_use_fixture",
    )
    shanghai = await store.supersede_card(
        hangzhou,
        NewCard(
            category="fact",
            scope="user",
            scope_id="u_a",
            content="现在住在上海",
            source="evidence_use_fixture",
        ),
        source_msg_id="msg-sh",
        evidence_text="搬到上海了",
        captured_by="evidence_use_fixture",
    )
    await store.add_card(
        NewCard(
            category="event",
            scope="group",
            scope_id="g_alpha",
            content="阿尔法群团建地点定在东湖",
            source="evidence_use_fixture",
        ),
        source_msg_id="msg-alpha",
        captured_by="evidence_use_fixture",
    )
    await store.add_card(
        NewCard(
            category="event",
            scope="group",
            scope_id="g_beta",
            content="贝塔群团建地点定在西山",
            source="evidence_use_fixture",
        ),
        source_msg_id="msg-beta",
        captured_by="evidence_use_fixture",
    )
    return hangzhou, shanghai


@pytest.mark.asyncio
async def test_e1_ordinary_current_after_supersede_no_trace(tmp_path) -> None:
    store = CardStore(str(tmp_path / "e1.db"))
    await store.init()
    try:
        await _seed_evidence_use_store(store)
        service = ContextService([MemoryContextSource(store)])
        case = ContextEvalCase.from_dict({
            "id": "e1",
            "query": "现在住在哪里",
            "current_message": "现在住在哪里",
            "user_id": "u_a",
            "required_hits": [{"type": "memory_card", "contains": "上海"}],
            "forbidden_hits": [{"contains": "杭州"}],
        })
        result = await evaluate_context_case(
            service,
            case,
            trace_assembler=TemporalTraceAssembler(store, TemporalTraceConfig()),
        )
        assert result.passed is True
        assert result.trace_checked is False
        assert result.required_matched == 1
        assert not result.forbidden_violations
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_e2_premise_conflict_structured_fields(tmp_path) -> None:
    store = CardStore(str(tmp_path / "e2.db"))
    await store.init()
    try:
        await _seed_evidence_use_store(store)
        service = ContextService([MemoryContextSource(store)])
        assembler = TemporalTraceAssembler(store, TemporalTraceConfig())
        case = ContextEvalCase.from_dict({
            "id": "e2",
            "query": "现在住在哪里",
            "current_message": "我不是还住杭州吗",
            "rewritten_query": "现在住在哪里",
            "user_id": "u_a",
            "required_hits": [{"type": "memory_card", "contains": "上海"}],
            "forbidden_hits": [{"contains": "杭州"}],
            "trace": {
                "expected_present": True,
                "reason": "premise_conflict",
                "required_current_contains": ["上海"],
                "required_earlier_contains": ["杭州"],
                "required_trajectory_contains": ["杭州", "上海"],
                "required_evidence_ref_prefixes": ["card:", "message:"],
                "max_knowledge_points": 2,
                "max_trace_chars": 600,
            },
        })
        result = await evaluate_context_case(service, case, trace_assembler=assembler)
        assert result.passed is True, result.to_dict()
        assert result.trace_checked is True
        assert result.trace_present is True
        assert result.trace_reason == "premise_conflict"
        assert result.trace_chars > 0
        assert not result.trace_violations
        # Ordinary remains active-only.
        assert not result.forbidden_violations
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_e3_historical_intent_earlier_trajectory(tmp_path) -> None:
    store = CardStore(str(tmp_path / "e3.db"))
    await store.init()
    try:
        await _seed_evidence_use_store(store)
        service = ContextService([MemoryContextSource(store)])
        assembler = TemporalTraceAssembler(store, TemporalTraceConfig())
        case = ContextEvalCase.from_dict({
            "id": "e3",
            "query": "现在住在哪里",
            "current_message": "我以前住在哪里",
            "rewritten_query": "现在住在哪里",
            "user_id": "u_a",
            "required_hits": [{"type": "memory_card", "contains": "上海"}],
            "forbidden_hits": [{"contains": "杭州"}],
            "trace": {
                "expected_present": True,
                "reason": "historical_intent",
                "required_earlier_contains": ["杭州"],
                "required_trajectory_contains": ["杭州", "上海"],
                "required_current_contains": ["上海"],
                "required_evidence_ref_prefixes": ["card:", "message:"],
            },
        })
        result = await evaluate_context_case(service, case, trace_assembler=assembler)
        assert result.passed is True, result.to_dict()
        assert result.trace_reason == "historical_intent"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_e4_rewrite_only_historical_marker_trace_absent(tmp_path) -> None:
    store = CardStore(str(tmp_path / "e4.db"))
    await store.init()
    try:
        await _seed_evidence_use_store(store)
        service = ContextService([MemoryContextSource(store)])
        assembler = TemporalTraceAssembler(store, TemporalTraceConfig())
        case = ContextEvalCase.from_dict({
            "id": "e4",
            "query": "现在住在哪里",
            "current_message": "现在住在哪里",
            "rewritten_query": "我以前住在哪里",
            "user_id": "u_a",
            "required_hits": [{"type": "memory_card", "contains": "上海"}],
            "forbidden_hits": [{"contains": "杭州"}],
            "trace": {"expected_present": False},
        })
        result = await evaluate_context_case(service, case, trace_assembler=assembler)
        assert result.passed is True, result.to_dict()
        assert result.trace_checked is True
        assert result.trace_present is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_e5_cross_group_no_evidence_leak(tmp_path) -> None:
    store = CardStore(str(tmp_path / "e5.db"))
    await store.init()
    try:
        await _seed_evidence_use_store(store)
        service = ContextService([MemoryContextSource(store)])
        assembler = TemporalTraceAssembler(store, TemporalTraceConfig())
        case = ContextEvalCase.from_dict({
            "id": "e5",
            "query": "团建地点定在哪里",
            "current_message": "团建地点以前定在哪里",
            "user_id": "u_a",
            "group_id": "g_alpha",
            "required_hits": [{"type": "memory_card", "contains": "阿尔法群团建地点定在东湖"}],
            "forbidden_hits": [{"contains": "贝塔群"}, {"contains": "西山"}],
            "trace": {
                "expected_present": False,
                "forbidden_text_contains": ["贝塔", "西山"],
                "forbidden_earlier_contains": ["贝塔", "西山"],
                "forbidden_current_contains": ["贝塔", "西山"],
                "forbidden_trajectory_contains": ["贝塔", "西山"],
            },
        })
        result = await evaluate_context_case(service, case, trace_assembler=assembler)
        assert result.passed is True, result.to_dict()
        assert not result.forbidden_violations
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_e6_no_evidence_zero_hits_and_trace_absent(tmp_path) -> None:
    store = CardStore(str(tmp_path / "e6.db"))
    await store.init()
    try:
        await _seed_evidence_use_store(store)
        service = ContextService([MemoryContextSource(store)])
        assembler = TemporalTraceAssembler(store, TemporalTraceConfig())
        case = ContextEvalCase.from_dict({
            "id": "e6",
            "query": "量子章鱼的股票代码是什么",
            "current_message": "量子章鱼的股票代码以前是什么",
            "user_id": "u_a",
            "group_id": "g_alpha",
            "max_hit_count": 0,
            "max_pack_chars": 0,
            "forbidden_hits": [{"type": "memory_card"}],
            "trace": {"expected_present": False},
        })
        result = await evaluate_context_case(service, case, trace_assembler=assembler)
        assert result.passed is True, result.to_dict()
        assert result.hit_count == 0
        assert result.trace_present is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_e7_cross_category_chain_is_rejected_by_store_walk_and_eval_gate(
    tmp_path,
) -> None:
    """A trusted category correction is stored but cannot become a temporal trace."""
    store = CardStore(str(tmp_path / "e7.db"))
    await store.init()
    try:
        parent = await store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id="u_a",
                content="用户住在杭州",
                source="e7",
            ),
            source_msg_id="e7-hz",
        )
        successor = await store.supersede_card(
            parent,
            NewCard(
                category="preference",
                scope="user",
                scope_id="u_a",
                content="用户住在上海",
                source="e7",
            ),
            source_msg_id="e7-sh",
        )
        assert await store.walk_supersedes_chain(successor) == []

        service = ContextService([MemoryContextSource(store)])
        assembler = TemporalTraceAssembler(store, TemporalTraceConfig())
        case = ContextEvalCase.from_dict({
            "id": "e7",
            "query": "住哪里",
            "current_message": "我不是还住杭州吗",
            "user_id": "u_a",
            "trace": {"expected_present": False},
        })
        result = await evaluate_context_case(service, case, trace_assembler=assembler)
        assert result.trace_present is False
        assert result.passed is True, result.to_dict()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_e10_kp_char_duplicate_pack_budgets_still_enforced() -> None:
    from services.context.types import ContextHit

    class _Static:
        name = "static"

        async def search(self, query: str, **kwargs: Any) -> list[ContextHit]:
            del query, kwargs
            return [
                ContextHit(id="a", type="memory_card", content="上海" * 40, score=0.9, source="t"),
                ContextHit(id="b", type="memory_card", content="上海" * 40, score=0.8, source="t"),
            ]

    def _kp(
        current: str,
        earlier: list[str],
        trajectory: str,
        refs: list[str],
    ) -> Any:
        return type("KP", (), {
            "current": type("N", (), {"content": current})(),
            "earlier": [type("N", (), {"content": item})() for item in earlier],
            "trajectory": trajectory,
            "evidence_refs": refs,
            "reason": "historical_intent",
            "confidence": 1.0,
        })()

    class _Trace:
        def __init__(self) -> None:
            self.text = "x" * 50
            self.reason = "historical_intent"
            self.knowledge_points = [
                _kp("上海", ["杭州"], "杭州 → 上海", ["card:1", "message:2"]),
                _kp("上海2", ["杭州2"], "杭州2 → 上海2", ["card:3"]),
                _kp("上海3", [], "上海3", []),
            ]

    class _Asm:
        async def assemble(self, *a: Any, **k: Any) -> Any:
            del a, k
            return _Trace()

    case = ContextEvalCase.from_dict({
        "id": "e10-budgets",
        "query": "q",
        "current_message": "我以前住在哪里",
        "max_duplicate_hits": 0,
        "max_pack_chars": 20,
        "max_hit_count": 1,
        "trace": {
            "expected_present": True,
            "max_knowledge_points": 2,
            "max_trace_chars": 10,
        },
    })
    service = ContextService([_Static()])
    result = await evaluate_context_case(service, case, trace_assembler=_Asm())
    assert result.passed is False
    assert result.duplicate_count >= 1 or result.pack_budget_exceeded or result.hit_count_exceeded
    assert result.trace_violations
    joined = json.dumps(result.trace_violations, ensure_ascii=False)
    assert "max_knowledge_points" in joined or "max_trace_chars" in joined or "knowledge" in joined


@pytest.mark.asyncio
async def test_evidence_use_fixture_fully_passes_real_stack(tmp_path) -> None:
    store = CardStore(str(tmp_path / "fixture.db"))
    await store.init()
    try:
        await _seed_evidence_use_store(store)
        service = ContextService([MemoryContextSource(store)])
        assembler = TemporalTraceAssembler(store, TemporalTraceConfig())
        cases = load_context_eval_cases(FIXTURE_PATH)
        summary = await evaluate_context_cases(service, cases, trace_assembler=assembler)
        assert summary.total_cases == 6
        assert summary.passed_cases == 6, {
            r.case_id: r.to_dict() for r in summary.results if not r.passed
        }
        assert summary.to_dict()["trace_violations"] == 0
        raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        assert "NOT official" in raw["description"] or "not official" in raw["description"].lower()
        assert "leaderboard" in raw["description"].lower() or "能力" in raw["description"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_existing_long_memory_frontier_still_green(tmp_path) -> None:
    store = CardStore(str(tmp_path / "frontier.db"))
    await store.init()
    try:
        # Same seed pattern as test_context_eval long memory frontier.
        await store.add_card(NewCard(
            category="preference",
            scope="user",
            scope_id="u_a",
            content="偏好的饮料是无糖乌龙茶",
            source="longmem_fixture",
        ))
        await store.add_card(NewCard(
            category="preference",
            scope="user",
            scope_id="u_b",
            content="偏好的饮料是可乐",
            source="longmem_fixture",
        ))
        old_location = await store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id="u_a",
                content="以前住在杭州",
                source="longmem_fixture",
            ),
            source_msg_id="msg-location-old",
            captured_at="2026-01-02T10:00:00+08:00",
            captured_by="longmem_fixture",
        )
        await store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id="u_a",
                content="现在住在上海",
                source="longmem_fixture",
                supersedes=old_location,
            ),
            source_msg_id="msg-location-new",
            captured_at="2026-06-18T10:00:00+08:00",
            captured_by="longmem_fixture",
        )
        await store.update_card(old_location, status="superseded")
        old_meeting = await store.add_card(
            NewCard(
                category="event",
                scope="user",
                scope_id="u_a",
                content="会议原定周四晚上七点",
                source="longmem_fixture",
            ),
            source_msg_id="msg-meeting-old",
            captured_at="2026-07-01T09:00:00+08:00",
            captured_by="longmem_fixture",
        )
        await store.add_card(
            NewCard(
                category="event",
                scope="user",
                scope_id="u_a",
                content="会议改到周五晚上八点",
                source="longmem_fixture",
                supersedes=old_meeting,
            ),
            source_msg_id="msg-meeting-new",
            captured_at="2026-07-02T09:00:00+08:00",
            captured_by="longmem_fixture",
        )
        await store.update_card(old_meeting, status="superseded")
        await store.add_card(NewCard(
            category="event",
            scope="group",
            scope_id="g_alpha",
            content="阿尔法群团建地点定在东湖",
            source="longmem_fixture",
        ))
        await store.add_card(NewCard(
            category="event",
            scope="group",
            scope_id="g_beta",
            content="贝塔群团建地点定在西山",
            source="longmem_fixture",
        ))

        service = ContextService([MemoryContextSource(store)])
        cases = load_context_eval_cases(LONG_MEMORY_FIXTURE)
        summary = await evaluate_context_cases(service, cases)
        assert summary.passed_cases == summary.total_cases == 5
        assert summary.to_dict()["trace_violations"] == 0
    finally:
        await store.close()


def test_fixture_json_parses_and_declares_synthetic_capability() -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert isinstance(raw["cases"], list)
    assert len(raw["cases"]) >= 6
    desc = raw["description"].lower()
    assert "not official" in desc or "synthetic" in desc or "能力" in raw["description"]
    cases = load_context_eval_cases(FIXTURE_PATH)
    assert any(c.trace is not None for c in cases)
    assert any(c.current_message for c in cases)
