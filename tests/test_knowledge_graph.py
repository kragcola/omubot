"""Tests for lightweight knowledge graph governance."""

from __future__ import annotations

import pytest

from services.context.types import ContextHit
from services.knowledge_graph import GraphCandidate, GraphFact, KnowledgeGraphService


@pytest.mark.asyncio
async def test_high_confidence_candidate_becomes_active_fact(tmp_path) -> None:
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
async def test_extract_from_context_hits_creates_governed_graph_facts(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        summary = await graph.extract_from_context_hits([
            ContextHit(
                id="card_like",
                type="memory_card",
                content="用户123喜欢音游",
                score=1.0,
                source="test",
                scope="user",
                scope_id="123",
            ),
            ContextHit(
                id="chunk_style",
                type="doc_chunk",
                content="Omubot 管理端采用雾青控制台",
                score=0.8,
                source="docs/admin.md",
                title="管理端风格",
            ),
        ])

        assert summary["extracted"] == 2
        assert summary["accepted"] == 1
        assert summary["pending"] == 1
        relationships = await graph.list_relationships()
        candidates = await graph.list_candidates()
        assert relationships[0]["subject"] == "用户123"
        assert relationships[0]["predicate"] == "喜欢"
        assert relationships[0]["object"] == "音游"
        assert relationships[0]["scope"] == "user"
        assert relationships[0]["scope_id"] == "123"
        assert relationships[0]["evidence"][0]["id"] == "card_like"
        assert candidates[0]["predicate"] == "采用"
        assert candidates[0]["scope"] == "global"
        assert candidates[0]["scope_id"] == "global"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_extract_from_context_hits_dedupes_existing_facts(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        hit = ContextHit(
            id="card_like",
            type="memory_card",
            content="用户123喜欢音游",
            score=1.0,
            source="test",
            scope="user",
            scope_id="123",
        )
        await graph.extract_from_context_hits([hit])
        await graph.extract_from_context_hits([hit])

        relationships = await graph.list_relationships()
        assert len(relationships) == 1
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
        )
        doc_fact = await graph.submit_fact_candidate(
            subject="Omubot",
            predicate="采用",
            object="雾青控制台",
            confidence=0.9,
            source="test",
            evidence={"chunk_id": "docs/admin.md::style"},
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
