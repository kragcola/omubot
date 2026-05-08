"""Tests for unified ContextService adapters."""

from __future__ import annotations

import pytest

from services.context import ContextService, GraphContextSource, KnowledgeContextSource, MemoryContextSource
from services.context.types import ContextHit
from services.knowledge import KnowledgeBase
from services.knowledge_graph import KnowledgeGraphService
from services.memory.card_store import CardStore, NewCard


@pytest.mark.asyncio
async def test_context_service_returns_memory_and_doc_hits(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="preference",
            scope="user",
            scope_id="123",
            content="喜欢 Docker Compose 部署方式",
        ))

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "deploy.md").write_text(
            "# 部署手册\n\n"
            "## Docker Compose\n"
            "使用 Docker Compose 启动 Omubot，需要配置环境变量。\n",
            encoding="utf-8",
        )
        kb = KnowledgeBase(str(docs))
        kb.reload()

        service = ContextService([
            MemoryContextSource(store),
            KnowledgeContextSource(kb),
        ])

        hits = await service.search("Docker Compose", user_id="123", top_k=10)
        hit_types = {hit.type for hit in hits}

        assert "memory_card" in hit_types
        assert "doc_chunk" in hit_types
        assert any("Docker Compose" in hit.content for hit in hits)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_context_pack_keeps_hits_explainable(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="fact",
            scope="group",
            scope_id="456",
            content="群里正在整理知识库重构方案",
        ))

        service = ContextService([MemoryContextSource(store)])
        pack = await service.build_prompt_context(
            "知识库重构",
            user_id="123",
            group_id="456",
            top_k=5,
            max_chars=800,
        )

        assert "记忆卡片" in pack.text
        assert "知识库重构" in pack.text
        assert pack.hits[0].metadata["category"] == "fact"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_context_first_turn_returns_scoped_cards_without_query_match(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="对猫毛过敏",
        ))
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="999",
            content="不应该跨用户泄露",
        ))

        service = ContextService([MemoryContextSource(store)])
        pack = await service.build_prompt_context(
            "今天天气怎么样",
            session_id="private_123",
            user_id="123",
            top_k=5,
            max_chars=800,
        )

        assert "对猫毛过敏" in pack.text
        assert "不应该跨用户泄露" not in pack.text
        assert pack.hits[0].metadata["decision"] == "full_new_session"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_context_semantic_match_after_first_turn(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="对猫毛过敏",
        ))

        service = ContextService([MemoryContextSource(store)])
        await service.search("先消耗首轮", session_id="private_123", user_id="123", top_k=5)
        hits = await service.search("猫毛过敏吗", session_id="private_123", user_id="123", top_k=5)

        assert any(hit.content == "对猫毛过敏" for hit in hits)
        assert any(hit.metadata["decision"] == "semantic_ngram" for hit in hits)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_context_returns_minimal_hint_when_scoped_cards_exist(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="喜欢雾青控制台",
        ))

        service = ContextService([MemoryContextSource(store)])
        await service.search("先消耗首轮", session_id="private_123", user_id="123", top_k=5)
        hits = await service.search("海底两万里", session_id="private_123", user_id="123", top_k=5)

        assert len(hits) == 1
        assert hits[0].retriever == "card_store_hint"
        assert hits[0].metadata["card_count"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_context_service_can_return_graph_hits(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_1"},
        )

        service = ContextService([GraphContextSource(graph)])
        hits = await service.search("音游", top_k=5)

        assert hits
        assert hits[0].type == "graph_fact"
        assert "音游" in hits[0].content
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_source_respects_memory_scope(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_user", "scope": "user", "scope_id": "123"},
        )
        await graph.submit_fact_candidate(
            subject="群456",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_group", "scope": "group", "scope_id": "456"},
        )
        await graph.submit_fact_candidate(
            subject="Omubot",
            predicate="支持",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"chunk_id": "docs/music.md::音游"},
        )

        service = ContextService([GraphContextSource(graph)])
        user_hits = await service.search("音游", user_id="123", top_k=10)
        group_hits = await service.search("音游", user_id="123", group_id="456", top_k=10)
        other_hits = await service.search("音游", user_id="999", group_id="999", top_k=10)

        assert any(hit.scope == "user" and hit.scope_id == "123" for hit in user_hits)
        assert not any(hit.scope == "group" and hit.scope_id == "456" for hit in user_hits)
        assert any(hit.scope == "group" and hit.scope_id == "456" for hit in group_hits)
        assert not any(hit.scope == "user" and hit.scope_id == "123" for hit in group_hits)
        assert {hit.scope for hit in other_hits} == {"global"}
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_context_service_metrics_track_pack_size_and_misses(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="喜欢雾青控制台",
        ))
        service = ContextService([MemoryContextSource(store)])

        pack = await service.build_prompt_context("雾青控制台", user_id="123", top_k=5, max_chars=800)
        await service.build_prompt_context("完全无关问题", user_id="123", top_k=5, max_chars=800)

        metrics = service.metrics()
        assert pack.text
        assert metrics["total_queries"] == 2
        assert metrics["miss_count"] == 1
        assert metrics["avg_pack_chars"] > 0
        assert metrics["max_pack_chars"] >= len(pack.text)
        assert metrics["hit_type_counts"]["memory_card"] == 1
        assert metrics["duplicate_rate"] == 0.0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_context_metrics_record_source_errors_once() -> None:
    service = ContextService([_FailingSource(), _StaticSource([
        ContextHit(
            id="doc_1",
            type="doc_chunk",
            content="仍然有有效命中",
            score=0.8,
            source="test",
        ),
    ])])

    hits = await service.search("有效命中", top_k=5)
    metrics = service.metrics()

    assert len(hits) == 1
    assert metrics["total_queries"] == 1
    assert metrics["miss_count"] == 0
    assert metrics["recent"][0]["error"] == "failing:RuntimeError"


class _FailingSource:
    name = "failing"

    async def search(self, *args, **kwargs) -> list[ContextHit]:
        del args, kwargs
        raise RuntimeError("boom")


class _StaticSource:
    name = "static"

    def __init__(self, hits: list[ContextHit]) -> None:
        self._hits = hits

    async def search(self, *args, **kwargs) -> list[ContextHit]:
        del args, kwargs
        return list(self._hits)
