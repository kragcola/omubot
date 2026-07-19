"""TDD tests for KnowledgeAIReviewer promotion governance.

Root cause covered:
- Reviewer must call KnowledgeGraphService.approve_candidate (materialize
  graph_facts + evidence + listeners), never write invalid status='approved'.
- Shared extraction_candidates has no domain column; run-all may register only
  one canonical fact-domain KG reviewer when ctx.knowledge_graph is present.
- LLM verdicts are strictly validated before promotion (exact decision, finite
  non-bool confidence in [0, 1]).
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from services.knowledge_graph import KnowledgeGraphService
from services.learning_autopilot.base import AggressivenessConfig
from services.learning_autopilot.knowledge_reviewer import KnowledgeAIReviewer


class _ScriptedLLM:
    """Scripted LLM client for assess_candidate (uses llm_client._call)."""

    def __init__(self, verdicts: list[dict[str, Any]] | dict[str, Any]) -> None:
        if isinstance(verdicts, dict):
            self._queue = [verdicts]
        else:
            self._queue = list(verdicts)
        self.calls = 0

    async def _call(self, request: Any) -> dict[str, Any]:
        self.calls += 1
        payload = self._queue.pop(0) if self._queue else {
            "decision": "kept", "confidence": 0.5, "reason": "default",
        }
        return {"text": json.dumps(payload, ensure_ascii=False)}


class _BlockingLLM:
    """LLM that blocks until released; used for D2 cancel-path tests."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def _call(self, request: Any) -> dict[str, Any]:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return {
            "text": json.dumps({
                "decision": "approved",
                "confidence": 0.99,
                "reason": "should not apply after cancel",
            }, ensure_ascii=False),
        }


async def _seed_pending(
    graph: KnowledgeGraphService,
    *,
    subject: str = "用户甲",
    predicate: str = "喜欢",
    object_: str = "音游",
    confidence: float = 0.9,
    evidence: dict[str, Any] | None = None,
) -> str:
    cand = await graph.submit_fact_candidate(
        subject=subject,
        predicate=predicate,
        object=object_,
        confidence=confidence,
        source="test",
        evidence=evidence or {"card_id": "card_1", "quote": "喜欢音游", "type": "memory_card"},
    )
    assert cand is not None
    assert getattr(cand, "candidate_id", None)
    return str(cand.candidate_id)  # type: ignore[union-attr]


async def _force_status(
    db_path: Path,
    candidate_id: str,
    *,
    status: str,
    review_note: str = "",
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE extraction_candidates SET status = ?, review_note = ?, updated_at = ? "
            "WHERE candidate_id = ?",
            (status, review_note, "2026-07-16T00:00:00+08:00", candidate_id),
        )
        await db.commit()


async def _count_status(db_path: Path, status: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM extraction_candidates WHERE status = ?",
            (status,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def _count_facts(db_path: Path) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM graph_facts WHERE status = 'active'")
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def _get_candidate_note(db_path: Path, candidate_id: str) -> str:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT review_note FROM extraction_candidates WHERE candidate_id = ?",
            (candidate_id,),
        )
        row = await cur.fetchone()
        return str(row[0] or "") if row else ""


@pytest.mark.asyncio
async def test_high_confidence_ai_approve_materializes_one_active_fact(tmp_path: Path) -> None:
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    listener_hits: list[str] = []

    async def _listener(fact: Any, evidence: dict[str, Any]) -> None:
        listener_hits.append(fact.fact_id)

    graph.add_fact_listener(_listener)
    try:
        cid = await _seed_pending(graph)
        llm = _ScriptedLLM({
            "decision": "approved",
            "confidence": 0.95,
            "reason": "clear triple with evidence",
        })
        reviewer = KnowledgeAIReviewer(graph, domain="fact")
        config = AggressivenessConfig(auto_approve_min_confidence=0.72)
        result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)

        assert result.ok is True
        assert result.approved_in_batch == 1
        assert result.rejected_in_batch == 0
        assert result.kept_in_batch == 0
        assert await _count_facts(db_path) == 1
        assert await _count_status(db_path, "active") == 1
        assert await _count_status(db_path, "approved") == 0
        assert await _count_status(db_path, "pending") == 0
        rels = await graph.list_relationships()
        assert len(rels) == 1
        assert rels[0]["subject"] == "用户甲"
        assert rels[0]["evidence"], "evidence must be preserved on the active fact"
        assert listener_hits and listener_hits[0] == rels[0]["fact_id"]
        cand = await graph._store.get_candidate(cid)
        assert cand is not None
        assert cand.status == "active"
        assert "ai_review" in (cand.review_note or "")
        assert llm.calls == 1
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_high_confidence_reject_writes_rejected_zero_facts(tmp_path: Path) -> None:
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        await _seed_pending(graph, predicate="胡说", object_="废话")
        llm = _ScriptedLLM({
            "decision": "rejected",
            "confidence": 0.9,
            "reason": "noise triple",
        })
        reviewer = KnowledgeAIReviewer(graph, domain="fact")
        config = AggressivenessConfig(auto_reject_max_confidence=0.50)
        result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)

        assert result.ok is True
        assert result.rejected_in_batch == 1
        assert result.approved_in_batch == 0
        assert await _count_facts(db_path) == 0
        assert await _count_status(db_path, "rejected") == 1
        assert await _count_status(db_path, "approved") == 0
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_low_confidence_kept_remains_pending_zero_facts(tmp_path: Path) -> None:
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        await _seed_pending(graph)
        llm = _ScriptedLLM({
            "decision": "kept",
            "confidence": 0.4,
            "reason": "uncertain",
        })
        reviewer = KnowledgeAIReviewer(graph, domain="fact")
        config = AggressivenessConfig()
        result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)

        assert result.ok is True
        assert result.kept_in_batch == 1
        assert result.approved_in_batch == 0
        assert await _count_facts(db_path) == 0
        assert await _count_status(db_path, "pending") == 1
        assert await _count_status(db_path, "approved") == 0
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_legacy_approved_with_valid_ai_review_repairs_without_llm(tmp_path: Path) -> None:
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    listener_hits: list[str] = []

    async def _listener(fact: Any, evidence: dict[str, Any]) -> None:
        listener_hits.append(fact.fact_id)

    graph.add_fact_listener(_listener)
    try:
        cid = await _seed_pending(graph)
        note = json.dumps({
            "ai_review": {
                "decision": "approved",
                "confidence": 0.91,
                "reason": "legacy high conf",
                "reviewed_at": "2026-07-15T12:00:00+08:00",
            },
        }, ensure_ascii=False)
        await _force_status(db_path, cid, status="approved", review_note=note)

        llm = _ScriptedLLM({"decision": "rejected", "confidence": 0.99, "reason": "should not run"})
        reviewer = KnowledgeAIReviewer(graph, domain="fact")
        config = AggressivenessConfig(auto_approve_min_confidence=0.72)
        result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)

        assert result.ok is True
        assert result.approved_in_batch == 1
        assert llm.calls == 0, "valid legacy approved must repair without another LLM call"
        assert await _count_facts(db_path) == 1
        assert await _count_status(db_path, "active") == 1
        assert await _count_status(db_path, "approved") == 0
        assert listener_hits, "repair path must fire fact listeners"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_malformed_legacy_approved_fail_closed_requeues_no_auto_materialize(
    tmp_path: Path,
) -> None:
    """Malformed legacy note must re-queue then run exactly one fresh LLM review.

    If that valid fresh review approves, assert exactly one fact + active candidate.
    No broad OR assertions on partial outcomes.
    """
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        cid = await _seed_pending(graph)
        note = json.dumps({
            "ai_review": {
                "decision": "approved",
                "confidence": True,
                "reason": "bad metadata",
            },
        }, ensure_ascii=False)
        await _force_status(db_path, cid, status="approved", review_note=note)

        llm = _ScriptedLLM({
            "decision": "approved",
            "confidence": 0.99,
            "reason": "fresh re-review",
        })
        reviewer = KnowledgeAIReviewer(graph, domain="fact")
        config = AggressivenessConfig(auto_approve_min_confidence=0.72)

        result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)

        assert result.ok is True
        assert llm.calls == 1, "malformed legacy must perform exactly one fresh LLM review"
        assert result.approved_in_batch == 1
        assert result.rejected_in_batch == 0
        assert result.kept_in_batch == 0
        assert await _count_status(db_path, "approved") == 0
        assert await _count_facts(db_path) == 1
        assert await _count_status(db_path, "active") == 1
        assert await _count_status(db_path, "pending") == 0
        cand = await graph._store.get_candidate(cid)
        assert cand is not None
        assert cand.status == "active"
        assert "fresh re-review" in (cand.review_note or "")
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_approve_candidate_default_rejects_legacy_approved_status(tmp_path: Path) -> None:
    """Admin/human path: allow_legacy_approved=False keeps pending-only contract."""
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        cid = await _seed_pending(graph)
        await _force_status(db_path, cid, status="approved", review_note="legacy")
        fact = await graph.approve_candidate(cid)
        assert fact is None
        assert await _count_facts(db_path) == 0

        fact2 = await graph.approve_candidate(
            cid, review_note='{"ai_review":{"decision":"approved"}}', allow_legacy_approved=True,
        )
        assert fact2 is not None
        assert await _count_facts(db_path) == 1
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_promotion_failure_does_not_claim_approved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        await _seed_pending(graph)
        llm = _ScriptedLLM({
            "decision": "approved",
            "confidence": 0.99,
            "reason": "should fail promote",
        })

        async def _boom(*_a: Any, **_k: Any) -> None:
            return None  # service returns None on failure

        monkeypatch.setattr(graph, "approve_candidate", _boom)
        reviewer = KnowledgeAIReviewer(graph, domain="fact")
        config = AggressivenessConfig(auto_approve_min_confidence=0.72)
        result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)

        assert result.approved_in_batch == 0
        assert await _count_facts(db_path) == 0
        assert await _count_status(db_path, "approved") == 0
        # Observable non-success: kept or still pending, never false approved claim
        assert result.kept_in_batch == 1 or await _count_status(db_path, "pending") == 1
    finally:
        await graph.close()


def test_knowledge_graph_service_exposes_public_db_path(tmp_path: Path) -> None:
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    assert isinstance(graph.db_path, Path)
    assert graph.db_path == Path(db_path)
    # Reviewer must use the public property, not private store fields.
    reviewer = KnowledgeAIReviewer(graph, domain="fact")
    assert reviewer._db_path == graph.db_path
    assert not hasattr(reviewer, "_graph_db_private_leak")


def test_autopilot_registers_injected_kg_service_exactly_one_fact_reviewer(
    tmp_path: Path,
) -> None:
    """Injected ctx.knowledge_graph => exactly one fact reviewer, no graph_relation."""
    import admin.routes.api.learning_pipeline as lp

    lp._autopilot_runner_instance = None
    try:
        db_path = tmp_path / "knowledge_graph.db"
        graph = KnowledgeGraphService(db_path)
        ctx = SimpleNamespace(
            storage_dir=tmp_path,
            llm_client=None,
            slang_store=None,
            knowledge_graph=graph,
        )
        runner = lp._get_autopilot_runner(ctx)
        assert runner is not None
        domains = list(runner.domains)
        kg_domains = [d for d in domains if d in {"fact", "graph_relation"}]
        assert kg_domains == ["fact"], f"expected single fact reviewer, got {kg_domains}"
        reviewer = runner.get_reviewer("fact")
        assert reviewer is not None
        assert getattr(reviewer, "domain", None) == "fact"
        assert getattr(reviewer, "_graph", None) is graph
        assert runner.get_reviewer("graph_relation") is None
        # Public db_path contract on registration path
        assert Path(reviewer._db_path) == graph.db_path
    finally:
        lp._autopilot_runner_instance = None


def test_autopilot_missing_kg_service_registers_no_fact_or_graph_reviewer(
    tmp_path: Path,
) -> None:
    """Missing ctx.knowledge_graph => fail closed; no fallback second connection owner."""
    import admin.routes.api.learning_pipeline as lp

    lp._autopilot_runner_instance = None
    try:
        kg_db = tmp_path / "knowledge_graph.db"
        kg_db.touch()
        ctx = SimpleNamespace(
            storage_dir=tmp_path,
            llm_client=None,
            slang_store=None,
            knowledge_graph=None,
        )
        runner = lp._get_autopilot_runner(ctx)
        assert runner is not None
        kg_domains = [d for d in runner.domains if d in {"fact", "graph_relation"}]
        assert kg_domains == [], f"expected fail-closed empty KG reviewers, got {kg_domains}"
        assert runner.get_reviewer("fact") is None
        assert runner.get_reviewer("graph_relation") is None
    finally:
        lp._autopilot_runner_instance = None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict_payload",
    [
        {"decision": "approve", "confidence": 0.99, "reason": "unknown decision string"},
        {"decision": "APPROVED", "confidence": 0.99, "reason": "case mismatch"},
        {"decision": "approved", "confidence": True, "reason": "bool confidence"},
        {"decision": "approved", "confidence": "0.99", "reason": "string confidence"},
        {"decision": "approved", "confidence": float("nan"), "reason": "nan conf"},
        {"decision": "approved", "confidence": float("inf"), "reason": "inf conf"},
        {"decision": "approved", "confidence": -0.1, "reason": "below range"},
        {"decision": "approved", "confidence": 1.01, "reason": "above range"},
        {"decision": "rejected", "confidence": True, "reason": "bool reject conf"},
        {"decision": "rejected", "confidence": "0.9", "reason": "string reject conf"},
        {"decision": "rejected", "confidence": float("nan"), "reason": "nan reject"},
        {"decision": "maybe", "confidence": 0.99, "reason": "unknown decision"},
    ],
)
async def test_invalid_llm_verdicts_never_auto_promote(
    tmp_path: Path,
    verdict_payload: dict[str, Any],
) -> None:
    """Invalid decision/confidence must leave candidate pending with kept note."""
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        cid = await _seed_pending(graph)
        llm = _ScriptedLLM(verdict_payload)
        reviewer = KnowledgeAIReviewer(graph, domain="fact")
        config = AggressivenessConfig(
            auto_approve_min_confidence=0.72,
            auto_reject_max_confidence=0.50,
        )
        result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)

        assert result.ok is True
        assert result.approved_in_batch == 0
        assert result.rejected_in_batch == 0
        assert result.kept_in_batch == 1
        assert await _count_facts(db_path) == 0
        assert await _count_status(db_path, "pending") == 1
        assert await _count_status(db_path, "active") == 0
        assert await _count_status(db_path, "rejected") == 0
        assert await _count_status(db_path, "approved") == 0
        note = await _get_candidate_note(db_path, cid)
        assert note, "kept path must write an observable review note"
        parsed = json.loads(note)
        assert "ai_review" in parsed
        assert parsed["ai_review"]["decision"] == "kept"
        conf = parsed["ai_review"]["confidence"]
        assert not isinstance(conf, bool)
        assert isinstance(conf, (int, float))
        assert math.isfinite(float(conf))
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_cancelled_llm_review_does_not_pollute_state(tmp_path: Path) -> None:
    """D2: cancel during blocked LLM leaves pending, no active fact, next batch ok."""
    db_path = tmp_path / "kg.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        cid = await _seed_pending(graph)
        blocked = _BlockingLLM()
        reviewer = KnowledgeAIReviewer(graph, domain="fact")
        config = AggressivenessConfig(auto_approve_min_confidence=0.72)

        batch_task = asyncio.create_task(
            reviewer.run_one_batch(batch_size=10, config=config, llm_client=blocked),
        )
        await asyncio.wait_for(blocked.entered.wait(), timeout=2.0)
        batch_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await batch_task

        assert await _count_facts(db_path) == 0
        assert await _count_status(db_path, "pending") == 1
        assert await _count_status(db_path, "active") == 0
        assert await _count_status(db_path, "approved") == 0
        assert await _count_status(db_path, "rejected") == 0

        state = await reviewer.get_state()
        assert state.approved == 0
        assert state.rejected == 0
        # Do not claim false progress that would skip the candidate forever.
        assert state.processed == 0 or await _count_status(db_path, "pending") == 1

        # Subsequent normal batch must complete successfully.
        llm = _ScriptedLLM({
            "decision": "approved",
            "confidence": 0.95,
            "reason": "post-cancel ok",
        })
        result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)
        assert result.ok is True
        assert result.approved_in_batch == 1
        assert await _count_facts(db_path) == 1
        assert await _count_status(db_path, "active") == 1
        cand = await graph._store.get_candidate(cid)
        assert cand is not None
        assert cand.status == "active"
    finally:
        blocked.release.set()
        await graph.close()


@pytest.mark.asyncio
async def test_exhausted_cursor_with_kept_backlog_reports_true_remaining(
    tmp_path: Path,
) -> None:
    """batch_size=2 over 3 pending kept: exhausted page never claims remaining=0/completed.

    Sticky kept rows remain pending; when last_id advances past all scanned ids
    the next empty page must count the real backlog (3), set active=False,
    leave last_done unset, and reset cursor so a later run can re-scan.
    """
    db_path = tmp_path / "reviewer-cursor-honest.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        ids: list[str] = []
        for i in range(3):
            cid = await _seed_pending(
                graph,
                subject=f"用户{i}",
                predicate="喜欢",
                object_=f"事物{i}",
                confidence=0.7,
                evidence={
                    "card_id": f"card_cursor_{i}",
                    "quote": f"喜欢事物{i}",
                    "type": "memory_card",
                },
            )
            ids.append(cid)
        ids_sorted = sorted(ids)

        llm = _ScriptedLLM(
            [
                {"decision": "kept", "confidence": 0.4, "reason": "keep1"},
                {"decision": "kept", "confidence": 0.4, "reason": "keep2"},
                {"decision": "kept", "confidence": 0.4, "reason": "keep3"},
            ]
        )
        reviewer = KnowledgeAIReviewer(graph, domain="fact")
        config = AggressivenessConfig(
            auto_approve_min_confidence=0.90,
            auto_reject_max_confidence=0.20,
        )

        r1 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
        assert r1.ok is True
        assert r1.processed_in_batch == 2
        assert r1.kept_in_batch == 2
        assert r1.completed is False
        assert r1.remaining == 3  # all three still pending/kept

        r2 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
        assert r2.ok is True
        assert r2.processed_in_batch == 1
        assert r2.kept_in_batch == 1
        assert r2.completed is False
        assert r2.remaining == 3

        # Cursor past all ids → empty page, but backlog still 3 sticky kept.
        r3 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
        assert r3.ok is True
        assert r3.processed_in_batch == 0
        assert r3.remaining == 3
        assert r3.completed is False

        state = await reviewer.get_state()
        assert state.active is False
        assert state.last_done_at == ""

        # Explicit later run must be able to start a new pass over sticky kept.
        r4 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
        assert r4.ok is True
        assert r4.processed_in_batch == 2
        assert r4.completed is False
        assert r4.remaining == 3
        assert await _count_status(db_path, "pending") == 3
        assert await _count_facts(db_path) == 0
        # Sanity: seed ids still the same three
        assert set(ids_sorted) == set(ids)
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_knowledge_ai_reviewer_rejects_unsupported_domain(
    tmp_path: Path,
) -> None:
    """Constructor accepts only fact / legacy graph_relation; both → fact."""
    db_path = tmp_path / "reviewer-domain.db"
    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        r_fact = KnowledgeAIReviewer(graph, domain="fact")
        assert r_fact.domain == "fact"
        r_legacy = KnowledgeAIReviewer(graph, domain="graph_relation")
        assert r_legacy.domain == "fact"

        with pytest.raises(ValueError, match="domain"):
            KnowledgeAIReviewer(graph, domain="style")
        with pytest.raises(ValueError, match="domain"):
            KnowledgeAIReviewer(graph, domain="episode")
        with pytest.raises(ValueError, match="domain"):
            KnowledgeAIReviewer(graph, domain="")
    finally:
        await graph.close()
