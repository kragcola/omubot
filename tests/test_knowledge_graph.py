"""Tests for lightweight knowledge graph governance."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.context.types import ContextHit
from services.knowledge_graph import GraphCandidate, GraphFact, KnowledgeGraphService


class _MockLLMClient:
    """Mock LLMClient for graph extractor tests.

    PR2 (2026-05-21) wires the LLM extractor into KnowledgeGraphService.
    These tests inject scripted responses to assert the governance path
    independent of a live model.
    """

    def __init__(self, responses_by_sentence: dict[str, str] | None = None) -> None:
        self._responses = responses_by_sentence or {}
        self.calls: list[str] = []

    async def _call(self, request: Any) -> dict[str, Any]:
        sentence = ""
        for msg in getattr(request, "user_messages", []) or []:
            content = msg.get("content") if isinstance(msg, dict) else ""
            if isinstance(content, str) and content.startswith("Input:"):
                sentence = content[len("Input:"):].strip()
        self.calls.append(sentence)
        text = self._responses.get(sentence, '{"facts": []}')
        return {"text": text}


@pytest.mark.asyncio
async def test_high_confidence_extraction_now_requires_review(tmp_path) -> None:
    """PR2 governance: ALL automated extractions must be reviewed.

    The legacy ``confidence >= 0.85`` direct-active fast path was removed.
    A 0.85 confidence still produces a *pending candidate*, never an
    active fact, unless an admin promotes it via approve_candidate or
    passes ``promote_directly=True`` (privileged override).
    """
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        result = await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_1", "quote": "喜欢音游"},
        )
        assert isinstance(result, GraphCandidate)
        assert await graph.list_relationships() == []

        candidates = await graph.list_candidates()
        assert candidates[0]["candidate_id"] == result.candidate_id
        fact = await graph.approve_candidate(result.candidate_id)
        assert isinstance(fact, GraphFact)
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_promote_directly_bypasses_review_for_admin_paths(tmp_path) -> None:
    """``promote_directly=True`` is a privileged escape for admin flows.

    Auto-extraction callers MUST leave it False; admin handlers (manual
    approve, supersede, seed test data) may use it explicitly.
    """
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        result = await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="admin_seed",
            evidence={"card_id": "card_1", "quote": "喜欢音游"},
            promote_directly=True,
        )
        assert isinstance(result, GraphFact)
        relationships = await graph.list_relationships()
        assert relationships[0]["subject"] == "用户123"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_mid_confidence_candidate_requires_review(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        result = await graph.submit_fact_candidate(
            subject="群456",
            predicate="正在讨论",
            object="知识库",
            confidence=0.7,
            source="test",
            evidence={"chunk_id": "docs/a.md::知识库"},
        )

        assert isinstance(result, GraphCandidate)
        candidates = await graph.list_candidates()
        assert candidates[0]["candidate_id"] == result.candidate_id

        fact = await graph.approve_candidate(result.candidate_id)
        assert fact is not None
        assert fact.object == "知识库"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_low_confidence_candidate_is_ignored(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        result = await graph.submit_fact_candidate(
            subject="A",
            predicate="可能相关",
            object="B",
            confidence=0.3,
            source="test",
            evidence={"card_id": "card_2"},
        )

        assert result is None
        assert await graph.list_candidates() == []
        assert await graph.list_relationships() == []
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_extract_from_context_hits_creates_pending_candidates(tmp_path) -> None:
    """LLM extractor route: even high-confidence triples land in pending."""
    llm = _MockLLMClient(responses_by_sentence={
        "用户123喜欢音游": (
            '{"facts":[{"subject":"用户123","predicate":"喜欢",'
            '"object":"音游","confidence":0.82,"evidence":"用户123喜欢音游"}]}'
        ),
        "Omubot 管理端采用雾青控制台": (
            '{"facts":[{"subject":"Omubot 管理端","predicate":"采用",'
            '"object":"雾青控制台","confidence":0.78,"evidence":"采用雾青控制台"}]}'
        ),
    })
    graph = KnowledgeGraphService(tmp_path / "graph.db", llm_client=llm)
    await graph.init()
    try:
        summary = await graph.extract_from_context_hits([
            ContextHit(
                id="card_like",
                type="memory_card",
                content="用户123喜欢音游。",
                score=1.0,
                source="test",
                scope="user",
                scope_id="123",
            ),
            ContextHit(
                id="chunk_style",
                type="doc_chunk",
                content="Omubot 管理端采用雾青控制台。",
                score=0.8,
                source="docs/admin.md",
                title="管理端风格",
            ),
        ])

        assert summary["extracted"] == 2
        # Both extractions are pending — no active fast-path anymore.
        assert summary["accepted"] == 0
        assert summary["pending"] == 2
        candidates = await graph.list_candidates()
        predicates = sorted(item["predicate"] for item in candidates)
        assert predicates == ["喜欢", "采用"]
        assert all(item["status"] == "pending" for item in candidates)
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_extract_from_context_hits_rejects_banned_subjects(tmp_path) -> None:
    """Validation gate: conjunctions/adverbs leaked from the LLM are dropped."""
    llm = _MockLLMClient(responses_by_sentence={
        "而不是核心仍然学习辅助功能": (
            '{"facts":[{"subject":"而不","predicate":"是",'
            '"object":"核心仍然学习辅助","confidence":0.9,'
            '"evidence":"而不是核心仍然"}]}'
        ),
    })
    graph = KnowledgeGraphService(tmp_path / "graph.db", llm_client=llm)
    await graph.init()
    try:
        summary = await graph.extract_from_context_hits([
            ContextHit(
                id="chunk_garbage",
                type="doc_chunk",
                content="而不是核心仍然学习辅助功能。",
                score=0.5,
                source="docs/x.md",
            ),
        ])
        assert summary["extracted"] == 0
        assert await graph.list_candidates() == []
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_extract_from_context_hits_without_llm_client_returns_empty(tmp_path) -> None:
    """Without an LLM client we refuse to extract — no silent regex fallback."""
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        summary = await graph.extract_from_context_hits([
            ContextHit(
                id="card_like",
                type="memory_card",
                content="用户123喜欢音游。",
                score=1.0,
                source="test",
                scope="user",
                scope_id="123",
            ),
        ])
        assert summary["extracted"] == 0
        assert await graph.list_candidates() == []
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_extract_from_context_hits_dedupes_existing_facts(tmp_path) -> None:
    llm = _MockLLMClient(responses_by_sentence={
        "用户123喜欢音游": (
            '{"facts":[{"subject":"用户123","predicate":"喜欢",'
            '"object":"音游","confidence":0.82,"evidence":"用户123喜欢音游"}]}'
        ),
    })
    graph = KnowledgeGraphService(tmp_path / "graph.db", llm_client=llm)
    await graph.init()
    try:
        hit = ContextHit(
            id="card_like",
            type="memory_card",
            content="用户123喜欢音游。",
            score=1.0,
            source="test",
            scope="user",
            scope_id="123",
        )
        await graph.extract_from_context_hits([hit])
        await graph.extract_from_context_hits([hit])

        candidates = await graph.list_candidates()
        # Both runs find the same triple; second run dedupes via find_candidate.
        assert len(candidates) == 1
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_supersede_and_rollback_relationship_restores_previous_fact(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        result = await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_1", "quote": "用户123喜欢音游"},
            promote_directly=True,
        )
        assert isinstance(result, GraphFact)

        replacement = await graph.supersede_relationship(
            result.fact_id,
            subject="用户123",
            predicate="喜欢",
            object="节奏游戏",
            confidence=0.91,
            source="admin",
            note="口径更准确",
        )
        assert replacement is not None
        active = await graph.list_relationships()
        assert len(active) == 1
        assert active[0]["object"] == "节奏游戏"
        assert active[0]["supersedes"] == result.fact_id

        ok = await graph.rollback_relationship(replacement.fact_id, note="撤回取代")
        assert ok is True
        restored = await graph.list_relationships()
        assert len(restored) == 1
        assert restored[0]["fact_id"] == result.fact_id
        assert restored[0]["object"] == "音游"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_scope_risks_list_legacy_global_memory_facts(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        legacy = await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_legacy", "quote": "旧版本没有作用域"},
            promote_directly=True,
        )
        scoped = await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="爵士",
            confidence=0.9,
            source="test",
            evidence={
                "card_id": "card_scoped",
                "scope": "user",
                "scope_id": "123",
            },
            promote_directly=True,
        )
        doc_fact = await graph.submit_fact_candidate(
            subject="Omubot",
            predicate="采用",
            object="雾青控制台",
            confidence=0.9,
            source="test",
            evidence={"chunk_id": "docs/admin.md::style"},
            promote_directly=True,
        )

        assert isinstance(legacy, GraphFact)
        assert isinstance(scoped, GraphFact)
        assert isinstance(doc_fact, GraphFact)
        risks = await graph.list_scope_risks()

        assert [item["fact_id"] for item in risks] == [legacy.fact_id]
        assert risks[0]["scope"] == "global"
        assert risks[0]["evidence"][0]["type"] == "memory_card"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_scoped_relationship_window_batches_evidence_loading(
    tmp_path,
    monkeypatch,
) -> None:
    graph = KnowledgeGraphService(tmp_path / "scoped-evidence.db")
    await graph.init()
    try:
        fact = await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="音游",
            confidence=0.8,
            source="test",
            evidence={"type": "fixture", "id": "evidence-1"},
            scope="user",
            scope_id="123",
            promote_directly=True,
        )
        assert isinstance(fact, GraphFact)

        async def reject_n_plus_one(_fact_id: str) -> list[dict[str, Any]]:
            raise AssertionError("scoped window must batch evidence loading")

        monkeypatch.setattr(graph._store, "list_evidence", reject_n_plus_one)
        rows = await graph.list_relationships_for_scopes(
            allowed_scopes=[("user", "123"), ("global", "global")],
            limit_per_scope=200,
        )

        row = next(item for item in rows if item["fact_id"] == fact.fact_id)
        assert [(item["type"], item["id"]) for item in row["evidence"]] == [
            ("fixture", "evidence-1")
        ]
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_concurrent_approve_candidate_same_service_promotes_once(tmp_path) -> None:
    """Same service, two concurrent approve_candidate: one winner, one fact, one listener."""
    graph = KnowledgeGraphService(tmp_path / "graph-race.db")
    await graph.init()
    try:
        candidate = await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_race", "quote": "喜欢音游"},
        )
        assert isinstance(candidate, GraphCandidate)

        listener_calls: list[str] = []

        async def _listener(fact: GraphFact, _evidence: dict[str, Any]) -> None:
            listener_calls.append(fact.fact_id)

        graph.add_fact_listener(_listener)
        results = await asyncio.gather(
            graph.approve_candidate(candidate.candidate_id),
            graph.approve_candidate(candidate.candidate_id),
        )
        successes = [item for item in results if item is not None]
        assert len(successes) == 1
        assert sum(1 for item in results if item is None) == 1

        relationships = await graph.list_relationships()
        assert len(relationships) == 1
        assert relationships[0]["fact_id"] == successes[0].fact_id

        refreshed = await graph._store.get_candidate(candidate.candidate_id)
        assert refreshed is not None
        assert refreshed.status == "active"

        db = graph._store._require_db()
        async with db.execute("SELECT COUNT(*) AS n FROM graph_facts") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row["n"]) == 1
        async with db.execute("SELECT COUNT(*) AS n FROM graph_evidence") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row["n"]) == 1
        assert listener_calls == [successes[0].fact_id]
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_concurrent_approve_candidate_separate_connections_promotes_once(
    tmp_path,
) -> None:
    """Two services / connections on the same SQLite file: exactly one fact."""
    db_path = tmp_path / "graph-cross-conn.db"
    seeder = KnowledgeGraphService(db_path)
    await seeder.init()
    try:
        candidate = await seeder.submit_fact_candidate(
            subject="用户456",
            predicate="在用",
            object="Omubot",
            confidence=0.88,
            source="test",
            evidence={"card_id": "card_cross", "quote": "在用 Omubot"},
        )
        assert isinstance(candidate, GraphCandidate)
        cid = candidate.candidate_id
    finally:
        await seeder.close()

    left = KnowledgeGraphService(db_path)
    right = KnowledgeGraphService(db_path)
    await left.init()
    await right.init()
    try:
        left_calls: list[str] = []
        right_calls: list[str] = []

        async def _left(fact: GraphFact, _evidence: dict[str, Any]) -> None:
            left_calls.append(fact.fact_id)

        async def _right(fact: GraphFact, _evidence: dict[str, Any]) -> None:
            right_calls.append(fact.fact_id)

        left.add_fact_listener(_left)
        right.add_fact_listener(_right)

        results = await asyncio.gather(
            left.approve_candidate(cid),
            right.approve_candidate(cid),
        )
        successes = [item for item in results if item is not None]
        assert len(successes) == 1
        assert sum(1 for item in results if item is None) == 1

        async with left._store._require_db().execute(
            "SELECT COUNT(*) AS n FROM graph_facts WHERE status = 'active'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row["n"]) == 1

        async with left._store._require_db().execute(
            "SELECT status FROM extraction_candidates WHERE candidate_id = ?",
            (cid,),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["status"] == "active"

        assert len(left_calls) + len(right_calls) == 1
        assert (left_calls + right_calls) == [successes[0].fact_id]
    finally:
        await left.close()
        await right.close()


@pytest.mark.asyncio
async def test_approve_candidate_cancel_rolls_back_and_skips_listener(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: real task.cancel mid-promotion rolls back and does not poison ordinary writes.

    Blocks after transaction mutations but before commit, cancels the promote
    task, asserts candidate stays pending with zero fact/evidence/listener, and
    proves a concurrent ordinary-connection write commits successfully (not
    joined to or rolled back with the promotion transaction).
    """
    graph = KnowledgeGraphService(tmp_path / "graph-cancel.db")
    await graph.init()
    try:
        candidate = await graph.submit_fact_candidate(
            subject="用户789",
            predicate="关注",
            object="知识图谱",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_cancel", "quote": "关注知识图谱"},
        )
        assert isinstance(candidate, GraphCandidate)

        listener_calls: list[str] = []

        async def _listener(fact: GraphFact, _evidence: dict[str, Any]) -> None:
            listener_calls.append(fact.fact_id)

        graph.add_fact_listener(_listener)

        reached_block = asyncio.Event()
        release_block = asyncio.Event()
        original_insert = graph._store._insert_evidence

        async def _block_after_evidence(
            fact_id: str,
            evidence: dict[str, Any],
            *,
            db: Any | None = None,
        ) -> None:
            await original_insert(fact_id, evidence, db=db)
            # Mutations (CAS + fact + evidence) are in the open promotion txn;
            # block before promote_candidate commits so cancel can roll back.
            reached_block.set()
            await release_block.wait()

        monkeypatch.setattr(graph._store, "_insert_evidence", _block_after_evidence)

        promote_task = asyncio.create_task(graph.approve_candidate(candidate.candidate_id))
        await asyncio.wait_for(reached_block.wait(), timeout=2.0)

        # Ordinary self._db write while promotion holds an open transaction on
        # the dedicated promotion connection. Start as a task so SQLite busy
        # wait cannot deadlock the test before cancel is delivered.
        ordinary_task = asyncio.create_task(
            graph.submit_fact_candidate(
                subject="旁路写者",
                predicate="记录",
                object="隔离事务",
                confidence=0.7,
                source="test",
                evidence={"card_id": "card_ordinary", "quote": "ordinary write"},
            )
        )
        await asyncio.sleep(0.05)

        promote_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await promote_task
        # Unblock any residual waiters if cancel landed after the await site.
        release_block.set()

        concurrent = await asyncio.wait_for(ordinary_task, timeout=5.0)
        assert isinstance(concurrent, GraphCandidate)

        refreshed = await graph._store.get_candidate(candidate.candidate_id)
        assert refreshed is not None
        assert refreshed.status == "pending"
        assert await graph.list_relationships() == []
        assert listener_calls == []

        # Cancelled promotion left no fact/evidence; ordinary candidate remains.
        db = graph._store._require_db()
        async with db.execute("SELECT COUNT(*) AS n FROM graph_facts") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row["n"]) == 0
        async with db.execute("SELECT COUNT(*) AS n FROM graph_evidence") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row["n"]) == 0

        ordinary = await graph._store.get_candidate(concurrent.candidate_id)
        assert ordinary is not None
        assert ordinary.status == "pending"
        assert ordinary.subject == "旁路写者"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_reject_after_active_false_active_fact_intact(tmp_path) -> None:
    """reject_candidate CAS must not overwrite active; fact stays active."""
    graph = KnowledgeGraphService(tmp_path / "graph-reject-active.db")
    await graph.init()
    try:
        candidate = await graph.submit_fact_candidate(
            subject="用户甲",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_ra", "quote": "喜欢音游"},
        )
        assert isinstance(candidate, GraphCandidate)
        fact = await graph.approve_candidate(candidate.candidate_id)
        assert fact is not None
        assert fact.status == "active"

        ok = await graph.reject_candidate(candidate.candidate_id, note="late reject")
        assert ok is False

        refreshed = await graph._store.get_candidate(candidate.candidate_id)
        assert refreshed is not None
        assert refreshed.status == "active"

        relationships = await graph.list_relationships()
        assert len(relationships) == 1
        assert relationships[0]["fact_id"] == fact.fact_id
        assert relationships[0]["status"] == "active"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_keep_after_active_false(tmp_path) -> None:
    """keep_candidate CAS must not pull active back to pending."""
    graph = KnowledgeGraphService(tmp_path / "graph-keep-active.db")
    await graph.init()
    try:
        candidate = await graph.submit_fact_candidate(
            subject="用户乙",
            predicate="在用",
            object="Omubot",
            confidence=0.88,
            source="test",
            evidence={"card_id": "card_ka", "quote": "在用 Omubot"},
        )
        assert isinstance(candidate, GraphCandidate)
        fact = await graph.approve_candidate(candidate.candidate_id)
        assert fact is not None

        ok = await graph.keep_candidate(candidate.candidate_id, note="late keep")
        assert ok is False

        refreshed = await graph._store.get_candidate(candidate.candidate_id)
        assert refreshed is not None
        assert refreshed.status == "active"
        assert len(await graph.list_relationships()) == 1
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_concurrent_approve_versus_reject_one_coherent_outcome(tmp_path) -> None:
    """Concurrent approve vs reject: exactly one winner, coherent terminal state."""
    graph = KnowledgeGraphService(tmp_path / "graph-approve-reject.db")
    await graph.init()
    try:
        candidate = await graph.submit_fact_candidate(
            subject="用户丙",
            predicate="关注",
            object="知识图谱",
            confidence=0.91,
            source="test",
            evidence={"card_id": "card_ar", "quote": "关注知识图谱"},
        )
        assert isinstance(candidate, GraphCandidate)
        cid = candidate.candidate_id

        approve_result, reject_result = await asyncio.gather(
            graph.approve_candidate(cid),
            graph.reject_candidate(cid, note="race reject"),
        )

        refreshed = await graph._store.get_candidate(cid)
        assert refreshed is not None
        facts = await graph.list_relationships()

        if approve_result is not None:
            assert reject_result is False
            assert refreshed.status == "active"
            assert len(facts) == 1
            assert facts[0]["fact_id"] == approve_result.fact_id
        else:
            assert reject_result is True
            assert refreshed.status == "rejected"
            assert facts == []
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_concurrent_approve_versus_keep_never_fact_plus_pending(
    tmp_path,
) -> None:
    """Concurrent approve vs keep: never active fact + pending candidate."""
    graph = KnowledgeGraphService(tmp_path / "graph-approve-keep.db")
    await graph.init()
    try:
        candidate = await graph.submit_fact_candidate(
            subject="用户丁",
            predicate="记录",
            object="CAS 竞态",
            confidence=0.87,
            source="test",
            evidence={"card_id": "card_ak", "quote": "CAS 竞态"},
        )
        assert isinstance(candidate, GraphCandidate)
        cid = candidate.candidate_id

        approve_result, keep_result = await asyncio.gather(
            graph.approve_candidate(cid),
            graph.keep_candidate(cid, note="race keep"),
        )

        refreshed = await graph._store.get_candidate(cid)
        assert refreshed is not None
        facts = await graph.list_relationships()

        # Coherent outcomes only:
        # - approve wins: fact active, candidate active (keep may or may not
        #   have written a review_note while still pending before promote CAS)
        # - keep "wins" first then approve: still promote from pending, or
        #   approve loses if somehow status left non-pending (not expected for keep→pending)
        if approve_result is not None:
            assert refreshed.status == "active"
            assert len(facts) == 1
            assert facts[0]["fact_id"] == approve_result.fact_id
            # Must never leave a pending candidate alongside the fact
            assert refreshed.status != "pending"
        else:
            assert keep_result is True
            assert refreshed.status == "pending"
            assert facts == []

        # Explicit invariant: no active fact with pending candidate
        if facts:
            assert refreshed.status != "pending"
        if refreshed.status == "pending":
            assert facts == []
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_reapprove_after_attempted_revert_no_duplicate_fact(tmp_path) -> None:
    """After promote, reject/keep fail; re-approve does not duplicate the fact."""
    graph = KnowledgeGraphService(tmp_path / "graph-reapprove.db")
    await graph.init()
    try:
        candidate = await graph.submit_fact_candidate(
            subject="用户戊",
            predicate="使用",
            object="pytest",
            confidence=0.93,
            source="test",
            evidence={"card_id": "card_re", "quote": "使用 pytest"},
        )
        assert isinstance(candidate, GraphCandidate)
        cid = candidate.candidate_id

        fact1 = await graph.approve_candidate(cid)
        assert fact1 is not None

        assert await graph.reject_candidate(cid, note="attempt revert reject") is False
        assert await graph.keep_candidate(cid, note="attempt revert keep") is False

        fact2 = await graph.approve_candidate(cid)
        assert fact2 is None

        refreshed = await graph._store.get_candidate(cid)
        assert refreshed is not None
        assert refreshed.status == "active"

        db = graph._store._require_db()
        async with db.execute(
            "SELECT COUNT(*) AS n FROM graph_facts WHERE status = 'active'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert int(row["n"]) == 1

        relationships = await graph.list_relationships()
        assert len(relationships) == 1
        assert relationships[0]["fact_id"] == fact1.fact_id
    finally:
        await graph.close()
