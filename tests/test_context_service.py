"""Tests for unified ContextService adapters."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.context import ContextService, GraphContextSource, KnowledgeContextSource, MemoryContextSource
from services.context.types import ContextHit
from services.knowledge import KnowledgeBase
from services.knowledge_graph import KnowledgeGraphService
from services.knowledge_graph.types import GraphFact
from services.memory.card_store import CardStore, NewCard
from services.memory.retrieval import RetrievalGate
from services.similarity import NgramSimilarityProvider


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
async def test_context_memory_hits_expose_provenance_and_score_breakdown(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        card_id = await store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id="123",
                content="对猫毛过敏",
                source="memo_extractor",
                confidence=0.8,
                priority=7,
            ),
            source_msg_id="msg-42",
            captured_at="2026-07-15T12:00:00+08:00",
            captured_by="memo_extractor:v2",
        )
        service = ContextService([MemoryContextSource(store)])

        hits = await service.search("猫毛过敏", user_id="123", top_k=5)

        hit = next(item for item in hits if item.id == card_id)
        assert hit.provenance is not None
        assert hit.provenance.source_id == card_id
        assert hit.provenance.source_message_id == "msg-42"
        assert hit.provenance.captured_at == "2026-07-15T12:00:00+08:00"
        assert hit.provenance.captured_by == "memo_extractor:v2"
        assert hit.score_breakdown is not None
        assert hit.score_breakdown.confidence == pytest.approx(0.8)
        assert hit.score_breakdown.importance == pytest.approx(0.7)
        assert hit.score_breakdown.recency == pytest.approx(1.0)
        assert hit.score_breakdown.source_score == pytest.approx(0.895)
        assert hit.score_breakdown.fusion == pytest.approx(hit.score)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_runtime_context_uses_shared_retrieval_gate_and_respects_semantic_disabled(
    tmp_path,
) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="对猫毛过敏",
        ))
        gate = RetrievalGate(
            card_store=store,
            refresh_interval=10,
            semantic_enabled=False,
            semantic_backend="ngram",
        )
        ctx = SimpleNamespace(
            card_store=store,
            retrieval=gate,
            group_memory_config=None,
            bus=None,
            knowledge_base=None,
            knowledge_graph=None,
        )
        service = ContextService.from_runtime(ctx)

        await service.search(
            "先问天气",
            session_id="private_123",
            user_id="123",
            top_k=5,
        )
        hits = await service.search(
            "对猫会过敏",
            session_id="private_123",
            user_id="123",
            top_k=5,
        )

        assert not any(hit.content == "对猫毛过敏" for hit in hits)
        assert any(hit.retriever == "card_store_hint" for hit in hits)
        assert gate.semantic_status()["queries"] == 0

        memo_text = await gate.build_memo_block(
            "private_123",
            "123",
            None,
        )
        assert "lookup_cards" in memo_text
        assert "对猫毛过敏" not in memo_text
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
        assert hits[0].metadata["scope_card_count"] == 1
        assert hits[0].metadata["matched_card_count"] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_context_metadata_exposes_scope_and_matched_counts(tmp_path) -> None:
    """Context hits expose unambiguous scope_card_count / matched_card_count."""
    store = CardStore(str(tmp_path / "memory_counts.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="喜欢音游节奏大师",
        ))
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="会弹钢琴",
        ))
        await store.add_card(NewCard(
            category="fact",
            scope="global",
            scope_id="global",
            content="全局运营规则",
        ))

        service = ContextService([MemoryContextSource(store)])
        # Consume new-session full path
        await service.search(
            "先消耗首轮", session_id="private_123", user_id="123", top_k=5,
        )
        hits = await service.search(
            "音游", session_id="private_123", user_id="123", top_k=1,
        )

        assert hits
        assert any(hit.content == "喜欢音游节奏大师" for hit in hits)
        for hit in hits:
            assert hit.metadata["scope_card_count"] == 3
            assert hit.metadata["matched_card_count"] == 1
            # Compatibility alias retained where already emitted on hits
            assert hit.metadata["card_count"] == hit.metadata["scope_card_count"]
            assert hit.metadata["decision"] == "keyword"
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
            promote_directly=True,
        )

        service = ContextService([GraphContextSource(graph)])
        hits = await service.search("音游", top_k=5)

        assert hits
        assert hits[0].type == "graph_fact"
        assert "音游" in hits[0].content
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_exposes_temporal_supersede_provenance(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        old = await graph.submit_fact_candidate(
            subject="用户123",
            predicate="居住地",
            object="杭州",
            confidence=0.8,
            source="memory_card",
            evidence={"type": "memory_card", "id": "card-old", "quote": "以前住在杭州"},
            scope="user",
            scope_id="123",
            promote_directly=True,
        )
        assert isinstance(old, GraphFact)
        new = await graph.supersede_relationship(
            old.fact_id,
            subject="用户123",
            predicate="居住地",
            object="上海",
            confidence=0.9,
            source="memory_update",
            evidence={"type": "memory_card", "id": "card-new", "quote": "现在住在上海"},
        )
        assert isinstance(new, GraphFact)

        service = ContextService([GraphContextSource(graph)])
        hits = await service.search(
            "用户123现在住在上海",
            user_id="123",
            top_k=5,
        )

        assert any(hit.id == new.fact_id for hit in hits)
        assert all(hit.id != old.fact_id for hit in hits)
        hit = next(item for item in hits if item.id == new.fact_id)
        assert hit.metadata["supersedes"] == old.fact_id
        assert hit.provenance is not None
        assert hit.provenance.owner == "graph_facts"
        assert hit.provenance.source_id == new.fact_id
        assert hit.provenance.captured_by == "memory_update"
        assert hit.provenance.supersedes_id == old.fact_id
        assert "memory_card:card-new" in hit.provenance.evidence_refs
        assert hit.score_breakdown is not None
        assert hit.score_breakdown.relevance is not None
        assert hit.score_breakdown.confidence == pytest.approx(0.9)
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_bounded_hops_add_only_bridge_facts(tmp_path) -> None:
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        facts = [
            ("用户123", "饲养宠物", "小白"),
            ("小白", "品种", "暹罗猫"),
            ("暹罗猫", "饮食建议", "低敏猫粮"),
            ("无关用户", "饲养宠物", "小黑"),
        ]
        for subject, predicate, object_ in facts:
            await graph.submit_fact_candidate(
                subject=subject,
                predicate=predicate,
                object=object_,
                confidence=0.9,
                source="multi_hop_fixture",
                evidence={"type": "fixture", "id": f"{subject}:{predicate}"},
                scope="user",
                scope_id="123",
                promote_directly=True,
            )

        query = "用户123的宠物为什么适合低敏猫粮"
        direct_only = ContextService([GraphContextSource(graph, max_hops=0)])
        expanded = ContextService([GraphContextSource(graph, max_hops=2)])

        direct_hits = await direct_only.search(query, user_id="123", top_k=8)
        expanded_hits = await expanded.search(query, user_id="123", top_k=8)

        assert not any(hit.content == "小白 --品种-> 暹罗猫" for hit in direct_hits)
        bridge = next(
            hit for hit in expanded_hits
            if hit.content == "小白 --品种-> 暹罗猫"
        )
        assert bridge.metadata["graph_hop"] == 1
        assert bridge.metadata["direct_match"] is False
        assert bridge.score_breakdown is not None
        assert bridge.score_breakdown.relevance is not None
        assert all("无关用户" not in hit.content for hit in expanded_hits)
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_single_direct_seed_does_not_expand(tmp_path) -> None:
    """Negative: one lexical direct seed must not open multi-hop expansion.

    Frozen contract: expansion requires at least two direct seeds (and max_hops>0).
    Behavior already present when the query only clears the threshold once.
    """
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        facts = [
            ("用户123", "喜欢", "音游"),
            ("音游", "类型", "节奏游戏"),
            ("节奏游戏", "代表作", "Phigros"),
        ]
        for subject, predicate, object_ in facts:
            await graph.submit_fact_candidate(
                subject=subject,
                predicate=predicate,
                object=object_,
                confidence=0.9,
                source="single_seed_fixture",
                evidence={"type": "fixture", "id": f"{subject}:{predicate}"},
                scope="user",
                scope_id="123",
                promote_directly=True,
            )

        # Only the first triple is a direct lexical seed for this query.
        service = ContextService([GraphContextSource(graph, max_hops=2)])
        hits = await service.search(
            "用户123喜欢什么",
            user_id="123",
            top_k=8,
        )

        assert hits, "expected the single direct seed fact"
        assert all(int(hit.metadata.get("graph_hop", 0)) == 0 for hit in hits)
        assert all(hit.metadata.get("direct_match") is True for hit in hits)
        assert not any(hit.content == "音游 --类型-> 节奏游戏" for hit in hits)
        assert not any(hit.content == "节奏游戏 --代表作-> Phigros" for hit in hits)
        assert any(
            hit.content == "用户123 --喜欢-> 音游"
            for hit in hits
        )
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_hub_fixture_stays_bounded_and_excludes_unrelated(
    tmp_path,
) -> None:
    """Negative: high-connectivity hub stays bounded; disconnected branches stay out.

    Frozen contract: expansion is entity-edge multi-hop with a hard result cap
    (top_k) and hop<=2. Completely unrelated graph components (no shared
    subject/object entities with the seed frontier) must not appear.
    """
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        # Two direct seeds share hub "中枢节点"; many spokes raise degree.
        seed_and_bridge = [
            ("用户123", "关注主题", "中枢节点"),
            ("用户123", "收藏资料", "中枢节点"),
            ("中枢节点", "桥接", "目标资料"),
            ("目标资料", "摘要", "重点结论"),
        ]
        hub_spokes = [
            (f"挂靠实体{i}", "挂靠", "中枢节点")
            for i in range(20)
        ]
        # Disjoint component: zero entity overlap with the seed frontier.
        disconnected = [
            ("孤立用户", "养了", "金鱼"),
            ("金鱼", "品种", "草金"),
            ("草金", "习性", "冷水"),
        ]
        for subject, predicate, object_ in (
            *seed_and_bridge,
            *hub_spokes,
            *disconnected,
        ):
            await graph.submit_fact_candidate(
                subject=subject,
                predicate=predicate,
                object=object_,
                confidence=0.9,
                source="hub_fixture",
                evidence={
                    "type": "fixture",
                    "id": f"{subject}:{predicate}:{object_}",
                },
                scope="user",
                scope_id="123",
                promote_directly=True,
            )

        service = ContextService([GraphContextSource(graph, max_hops=2)])
        top_k = 6
        hits = await service.search(
            "用户123关注主题和收藏资料",
            user_id="123",
            top_k=top_k,
        )

        assert hits
        assert len(hits) == top_k
        assert all(int(hit.metadata.get("graph_hop", 0)) <= 2 for hit in hits)
        # Disconnected component must never enter the pack.
        assert all("孤立用户" not in hit.content for hit in hits)
        assert all("金鱼" not in hit.content for hit in hits)
        assert all("草金" not in hit.content for hit in hits)
        # Direct seeds first / present; hub degree must not explode past top_k.
        hop0 = [hit for hit in hits if int(hit.metadata.get("graph_hop", 0)) == 0]
        assert len(hop0) >= 2
        assert all(
            "用户123" in hit.content and "中枢节点" in hit.content
            for hit in hop0
        )
        # Prefer the bounded branch that can continue past the hub instead of
        # filling the pack with high-confidence leaf spokes.
        assert any(hit.content == "中枢节点 --桥接-> 目标资料" for hit in hits)
        assert any(hit.content == "目标资料 --摘要-> 重点结论" for hit in hits)
        assert sum("挂靠实体" in hit.content for hit in hits) <= 2
        # Even with 20 spokes + bridges, the returned set stops at top_k.
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_scope_window_retains_lower_confidence_user_target(
    tmp_path,
) -> None:
    graph = KnowledgeGraphService(tmp_path / "scope_window.db")
    await graph.init()
    try:
        for i in range(200):
            await graph.submit_fact_candidate(
                subject=f"噪声主体{i}",
                predicate="噪声关系",
                object=f"噪声客体{i}",
                confidence=0.99,
                source="scope_window_fixture",
                evidence={"type": "fixture", "id": f"noise-{i}"},
                scope="global",
                scope_id="global",
                promote_directly=True,
            )
        target = await graph.submit_fact_candidate(
            subject="用户123",
            predicate="真正偏好",
            object="稀有目标知识",
            confidence=0.40,
            source="scope_window_fixture",
            evidence={"type": "fixture", "id": "user-target"},
            scope="user",
            scope_id="123",
            promote_directly=True,
        )
        assert isinstance(target, GraphFact)

        source = GraphContextSource(graph, max_hops=2)
        hits = await source.search(
            "用户123真正偏好稀有目标知识",
            user_id="123",
            top_k=8,
        )

        assert target.fact_id in {hit.id for hit in hits}
        assert all(
            (hit.scope, hit.scope_id) in {("global", "global"), ("user", "123")}
            for hit in hits
        )
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_scope_window_prioritizes_current_scope_and_global() -> None:
    class CapturingGraph:
        def __init__(self) -> None:
            self.allowed_scopes: list[tuple[str, str]] = []

        async def list_relationships_for_scopes(
            self,
            *,
            allowed_scopes: list[tuple[str, str]],
            limit_per_scope: int,
        ) -> list[dict]:
            del limit_per_scope
            self.allowed_scopes = allowed_scopes
            return []

    class SharedPools:
        def resolve_group_pools(self, group_id: str) -> list[str]:
            assert group_id == "456"
            return [
                "001", "002", "003", "004", "005",
                "006", "007", "008", "009", "456",
            ]

    graph = CapturingGraph()
    source = GraphContextSource(graph, group_memory_config=SharedPools())

    assert await source.search("测试", group_id="456", top_k=8) == []
    assert graph.allowed_scopes[:2] == [
        ("group", "456"),
        ("global", "global"),
    ]
    assert len(graph.allowed_scopes) == 8


@pytest.mark.asyncio
async def test_graph_context_hub_rank_uses_raw_lexical_score_before_floor() -> None:
    def graph_item(
        fact_id: str,
        subject: str,
        predicate: str,
        object_: str,
        *,
        confidence: float,
    ) -> dict:
        return {
            "fact_id": fact_id,
            "subject": subject,
            "predicate": predicate,
            "object": object_,
            "confidence": confidence,
            "status": "active",
            "scope": "user",
            "scope_id": "123",
            "source": "fixture",
            "evidence": [],
        }

    class StaticGraph:
        async def list_relationships_for_scopes(
            self,
            *,
            allowed_scopes: list[tuple[str, str]],
            limit_per_scope: int,
        ) -> list[dict]:
            del allowed_scopes, limit_per_scope
            facts = [
                graph_item("d1", "user", "direct-a", "hub", confidence=0.9),
                graph_item("d2", "user", "direct-b", "hub", confidence=0.9),
                graph_item("related", "hub", "related", "answer", confidence=0.4),
            ]
            for index in range(5):
                facts.append(
                    graph_item(
                        f"noise-{index}",
                        "hub",
                        "noise",
                        f"branch-{index}",
                        confidence=0.99,
                    )
                )
                facts.append(
                    graph_item(
                        f"tail-{index}",
                        f"branch-{index}",
                        "tail",
                        f"leaf-{index}",
                        confidence=0.99,
                    )
                )
            return facts

    class ScriptedSimilarity(NgramSimilarityProvider):
        def similarity(self, left: str, right: str) -> float:
            del left
            if "direct-a" in right or "direct-b" in right:
                return 0.9
            if "related" in right:
                return 0.10
            return 0.0

    source = GraphContextSource(StaticGraph(), max_hops=2)
    source._similarity = ScriptedSimilarity()

    hits = await source.search("query", user_id="123", top_k=5)

    assert "related" in {hit.id for hit in hits}


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
            promote_directly=True,
        )
        await graph.submit_fact_candidate(
            subject="群456",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_group", "scope": "group", "scope_id": "456"},
            promote_directly=True,
        )
        await graph.submit_fact_candidate(
            subject="Omubot",
            predicate="支持",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"chunk_id": "docs/music.md::音游"},
            promote_directly=True,
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


@pytest.mark.asyncio
async def test_context_service_optional_plan_meta_secret_free() -> None:
    """Additive plan_meta plumbing: present only when provided; no raw query leak fields."""
    service = ContextService([_StaticSource([
        ContextHit(
            id="mem_1",
            type="memory_card",
            content="记忆内容",
            score=0.9,
            source="test",
        ),
    ])])
    plan_meta = {
        "version": "qa_rg_v1",
        "needs": ["preference"],
        "profile_id": "preference_memory_v1",
        "mode": "hybrid",
        "top_k": 5,
        "type_caps": {"doc_chunk": 1, "memory_card": 5},
        "reason_codes": ["need:preference"],
        "enabled": True,
        "identity": False,
    }
    pack = await service.build_prompt_context(
        "我喜欢吃什么 secret_token_xyz",
        user_id="123",
        top_k=5,
        plan_meta=plan_meta,
    )
    assert pack.text
    recent = service.recent(limit=1)[0]
    assert "query_aware_plan" in recent
    assert recent["query_aware_plan"]["version"] == "qa_rg_v1"
    assert recent["query_aware_plan"]["needs"] == ["preference"]
    assert recent["query_aware_plan"]["profile_id"] == "preference_memory_v1"
    assert "secret_token_xyz" not in str(recent["query_aware_plan"])
    # Metrics recent surface includes additive field when present.
    metrics = service.metrics()
    assert metrics["recent"][-1]["query_aware_plan"]["identity"] is False


@pytest.mark.asyncio
async def test_context_service_plan_meta_drops_extra_secret_and_isolates_nested() -> None:
    """Unknown keys dropped; nested mutation of caller plan_meta must not mutate recorded state."""
    service = ContextService([_StaticSource([
        ContextHit(
            id="mem_1",
            type="memory_card",
            content="记忆内容",
            score=0.9,
            source="test",
        ),
    ])])
    plan_meta: dict = {
        "version": "qa_rg_v1",
        "needs": ["preference"],
        "profile_id": "preference_memory_v1",
        "mode": "hybrid",
        "top_k": 5,
        "type_caps": {"doc_chunk": 1, "memory_card": 5},
        "reason_codes": ["need:preference"],
        "enabled": True,
        "identity": False,
        "extra_secret": "sk-should-not-persist",
        "api_key": "leak",
    }
    await service.search(
        "我喜欢吃什么",
        user_id="123",
        top_k=5,
        plan_meta=plan_meta,
    )
    recorded = service.recent(limit=1)[0]["query_aware_plan"]
    assert "extra_secret" not in recorded
    assert "api_key" not in recorded
    assert set(recorded.keys()) <= {
        "version",
        "needs",
        "profile_id",
        "mode",
        "top_k",
        "type_caps",
        "reason_codes",
        "enabled",
        "identity",
    }
    # Post-call nested mutation isolation.
    plan_meta["type_caps"]["doc_chunk"] = 999
    plan_meta["needs"].append("hacked")
    plan_meta["reason_codes"].append("evil")
    plan_meta["extra_secret"] = "still-leaking"
    reloaded = service.recent(limit=1)[0]["query_aware_plan"]
    assert reloaded["type_caps"]["doc_chunk"] == 1
    assert reloaded["needs"] == ["preference"]
    assert "evil" not in reloaded["reason_codes"]
    assert "extra_secret" not in reloaded
    metrics = service.metrics()
    assert metrics["recent"][-1]["query_aware_plan"]["type_caps"]["doc_chunk"] == 1


@pytest.mark.asyncio
async def test_context_service_preference_type_cap_limits_doc_after_rrf(tmp_path) -> None:
    """Real mixed-source search: preference doc_chunk cap yields <=1 doc after RRF."""
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="preference",
            scope="user",
            scope_id="123",
            content="用户喜欢吃拉面和寿司",
        ))
        docs = tmp_path / "docs"
        docs.mkdir()
        for i in range(4):
            (docs / f"food_{i}.md").write_text(
                f"# 美食手册 {i}\n\n"
                f"用户喜欢吃 拉面 寿司 偏好 口味 文档资料段落 {i}。\n"
                f"更多关于喜欢吃什么的说明与推荐。\n",
                encoding="utf-8",
            )
        kb = KnowledgeBase(str(docs))
        kb.reload()
        service = ContextService([
            MemoryContextSource(store),
            KnowledgeContextSource(kb),
        ])
        hits = await service.search(
            "我喜欢吃什么",
            user_id="123",
            top_k=10,
            type_caps={"memory_card": 5, "doc_chunk": 1, "graph_fact": 2},
            mode="hybrid",
        )
        doc_count = sum(1 for h in hits if h.type == "doc_chunk")
        assert doc_count <= 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_context_service_absent_plan_meta_compat() -> None:
    """When plan_meta is omitted, recent rows stay pre-v1 shape (no query_aware_plan key)."""
    service = ContextService([_StaticSource([
        ContextHit(
            id="mem_1",
            type="memory_card",
            content="记忆内容",
            score=0.9,
            source="test",
        ),
    ])])
    pack = await service.build_prompt_context("雾青控制台", user_id="123", top_k=5)
    assert pack.text
    recent = service.recent(limit=1)[0]
    assert "query_aware_plan" not in recent
    metrics = service.metrics()
    assert "query_aware_plan" not in metrics["recent"][-1]


@pytest.mark.asyncio
async def test_context_service_pack_evidence_gate_after_type_caps() -> None:
    """Gate runs after search type caps; does not resurrect capped hits; records metrics."""
    from services.context.pack_evidence_gate import PackEvidenceGatePolicy
    from services.context.types import ContextProvenance, ContextScoreBreakdown

    # Three docs + one weak untrusted memory. type_caps doc_chunk=1 keeps one doc.
    class _MixedSource:
        name = "static"

        async def search(self, *args, **kwargs):
            del args, kwargs
            return [
                ContextHit(
                    id="doc_a",
                    type="doc_chunk",
                    content="文档 A 内容足够长",
                    score=0.9,
                    source="knowledge",
                    score_breakdown=ContextScoreBreakdown(source_score=0.9),
                ),
                ContextHit(
                    id="doc_b",
                    type="doc_chunk",
                    content="文档 B 内容足够长",
                    score=0.8,
                    source="knowledge",
                    score_breakdown=ContextScoreBreakdown(source_score=0.8),
                ),
                ContextHit(
                    id="mem_weak",
                    type="memory_card",
                    content="弱记忆",
                    score=0.7,
                    source="memo_extractor",
                    status="active",
                    provenance=ContextProvenance(owner="memory_cards"),
                    score_breakdown=ContextScoreBreakdown(
                        source_score=0.7, confidence=0.1
                    ),
                    metadata={"confidence": 0.1},
                ),
            ]

    service = ContextService(
        [_MixedSource()],
        pack_evidence_gate=PackEvidenceGatePolicy(enabled=True),
    )
    search_hits = await service.search(
        "q",
        user_id="123",
        top_k=10,
        type_caps={"doc_chunk": 1, "memory_card": 5},
    )
    search_ids = {h.id for h in search_hits}
    assert "doc_b" not in search_ids  # type cap already dropped
    assert "doc_a" in search_ids
    assert "mem_weak" in search_ids

    pack = await service.build_prompt_context(
        "q2",
        user_id="123",
        top_k=10,
        type_caps={"doc_chunk": 1, "memory_card": 5},
        wrap_with_safety_tags=False,
    )
    pack_ids = [h.id for h in pack.hits]
    assert "doc_b" not in pack_ids
    # Weak memory demoted but still eligible for pack (unless budget drops it)
    assert pack.trace_seed_ids == ("mem_weak",)
    recent = service.recent(limit=1)[0]
    assert "pack_evidence_gate" in recent
    peg = recent["pack_evidence_gate"]
    assert peg["version"] == "peg_v1"
    assert peg["enabled"] is True
    assert set(peg.keys()) <= {"version", "enabled", "identity", "actions", "reasons"}
    assert "mem_weak" not in str(peg)
    assert "q2" not in str(peg)
    assert peg["actions"]["demote"] >= 1


@pytest.mark.asyncio
async def test_context_service_pack_gate_disabled_identity() -> None:
    from services.context.pack_evidence_gate import PackEvidenceGatePolicy
    from services.context.types import ContextProvenance, ContextScoreBreakdown

    weak = ContextHit(
        id="mem_weak",
        type="memory_card",
        content="弱记忆身份",
        score=0.9,
        source="memo_extractor",
        provenance=ContextProvenance(),
        score_breakdown=ContextScoreBreakdown(source_score=0.9, confidence=0.05),
        metadata={"confidence": 0.05},
    )
    service = ContextService(
        [_StaticSource([weak])],
        pack_evidence_gate=PackEvidenceGatePolicy(enabled=False),
    )
    pack = await service.build_prompt_context(
        "identity",
        user_id="1",
        wrap_with_safety_tags=False,
    )
    assert [h.id for h in pack.hits] == ["mem_weak"]
    recent = service.recent(limit=1)[0]
    assert recent["pack_evidence_gate"]["identity"] is True
    assert recent["pack_evidence_gate"]["enabled"] is False


@pytest.mark.asyncio
async def test_context_service_empty_after_gate() -> None:
    from services.context.pack_evidence_gate import PackEvidenceGatePolicy
    from services.context.types import ContextScoreBreakdown

    blank = ContextHit(
        id="doc_blank",
        type="doc_chunk",
        content="   ",
        score=0.9,
        source="knowledge",
        score_breakdown=ContextScoreBreakdown(source_score=0.9),
    )
    service = ContextService(
        [_StaticSource([blank])],
        pack_evidence_gate=PackEvidenceGatePolicy(enabled=True),
    )
    pack = await service.build_prompt_context("empty", user_id="1")
    assert pack.text == ""
    assert pack.hits == []
    assert pack.omitted_count >= 1
    assert pack.trace_seed_ids == ()


@pytest.mark.asyncio
async def test_context_pack_to_dict_external_compat() -> None:
    from services.context.types import ContextPack

    pack = ContextPack(
        text="t",
        hits=[],
        omitted_count=2,
        trace_seed_ids=("a",),
    )
    d = pack.to_dict()
    assert d == {"text": "t", "hits": [], "omitted_count": 2}
    assert "trace_seed_ids" not in d


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
