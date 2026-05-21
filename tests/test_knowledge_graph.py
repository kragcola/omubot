"""Tests for lightweight knowledge graph governance."""

from __future__ import annotations

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
