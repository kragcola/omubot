"""Tests for repeatable ContextService evaluation fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.context import (
    ContextService,
    GraphContextSource,
    KnowledgeContextSource,
    MemoryContextSource,
    evaluate_context_cases,
    load_context_eval_cases,
)
from services.context.eval import ContextEvalCase, ContextHitExpectation
from services.context.types import ContextHit
from services.knowledge import KnowledgeBase
from services.knowledge_graph import KnowledgeGraphService
from services.memory.card_store import CardStore, NewCard

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "context_eval" / "basic.json"
OWNER_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "context_eval" / "owner_scenarios.json"
OWNER_REALISTIC_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "context_eval" / "owner_realistic.json"


@pytest.mark.asyncio
async def test_context_eval_fixture_passes_with_memory_doc_and_graph_sources(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        await store.add_card(NewCard(
            category="preference",
            scope="user",
            scope_id="123",
            content="喜欢 Docker Compose 部署方式",
        ))
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="999",
            content="不要注入的私密内容",
        ))

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "deploy.md").write_text(
            "# 部署手册\n\n"
            "## Docker Compose\n"
            "使用 Docker Compose 启动 Omubot，并检查环境变量。\n",
            encoding="utf-8",
        )
        kb = KnowledgeBase(str(docs))
        kb.reload()

        await graph.submit_fact_candidate(
            subject="用户123",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"card_id": "card_1"},
        )

        service = ContextService([
            MemoryContextSource(store),
            KnowledgeContextSource(kb),
            GraphContextSource(graph),
        ])
        cases = load_context_eval_cases(FIXTURE_PATH)
        summary = await evaluate_context_cases(service, cases)

        assert summary.total_cases == 3
        assert summary.passed_cases == 3
        assert summary.required_hit_recall == 1.0
        assert summary.forbidden_violations == 0
        assert summary.duplicate_hits == 0
        assert summary.pack_budget_violations == 0
        assert summary.to_dict()["results"][0]["pack_chars"] > 0
    finally:
        await graph.close()
        await store.close()


@pytest.mark.asyncio
async def test_context_eval_reports_missing_forbidden_and_duplicates() -> None:
    service = ContextService([_StaticSource([
        ContextHit(
            id="doc_1",
            type="doc_chunk",
            content="这是禁止内容",
            score=0.9,
            source="test",
        ),
        ContextHit(
            id="doc_2",
            type="doc_chunk",
            content="重复上下文",
            score=0.8,
            source="test",
        ),
        ContextHit(
            id="doc_3",
            type="doc_chunk",
            content="重复上下文",
            score=0.7,
            source="test",
        ),
    ])])
    case = ContextEvalCase(
        id="bad-case",
        query="任意问题",
        required_hits=[ContextHitExpectation(type="memory_card", contains="必须命中")],
        forbidden_hits=[ContextHitExpectation(contains="禁止内容")],
        max_duplicate_hits=0,
    )

    summary = await evaluate_context_cases(service, [case])
    result = summary.results[0]

    assert summary.passed_cases == 0
    assert summary.required_hit_recall == 0.0
    assert result.missing_required
    assert result.forbidden_violations
    assert result.duplicate_count == 1


@pytest.mark.asyncio
async def test_context_eval_reports_pack_budget_violations() -> None:
    service = ContextService([_StaticSource([
        ContextHit(
            id="doc_1",
            type="doc_chunk",
            content="这是一段明显超过预算的上下文资料，用来证明评测会阻止 Prompt pack 悄悄膨胀。",
            score=0.9,
            source="test",
        ),
    ])])
    case = ContextEvalCase(
        id="too-long-pack",
        query="预算",
        max_pack_chars=30,
        max_duplicate_hits=0,
    )

    summary = await evaluate_context_cases(service, [case])
    result = summary.results[0]

    assert summary.passed_cases == 0
    assert summary.pack_budget_violations == 1
    assert result.pack_budget_exceeded is True
    assert result.max_pack_chars == 30
    assert result.pack_chars > 30


@pytest.mark.asyncio
async def test_owner_context_eval_scenarios_guard_scope_and_noise(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        await _seed_owner_context_store(store)
        kb = _seed_owner_knowledge_base(tmp_path)
        await graph.submit_fact_candidate(
            subject="Omubot 管理端",
            predicate="采用",
            object="雾青控制台",
            confidence=0.9,
            source="test",
            evidence={"chunk_id": "docs/admin.md::style"},
        )

        service = ContextService([
            MemoryContextSource(store),
            KnowledgeContextSource(kb),
            GraphContextSource(graph),
        ])
        cases = load_context_eval_cases(OWNER_FIXTURE_PATH)
        summary = await evaluate_context_cases(service, cases)
        results_by_id = {result.case_id: result for result in summary.results}

        assert summary.total_cases == 5
        assert summary.passed_cases == 5
        assert summary.required_hit_recall == 1.0
        assert summary.forbidden_violations == 0
        assert summary.duplicate_hits == 0
        assert summary.pack_budget_violations == 0
        assert results_by_id["private-user-memory-scope"].hit_count >= 1
        assert results_by_id["group-memory-scope-isolation"].hit_count >= 1
        assert results_by_id["unrelated-owner-query-no-fill"].hit_count == 0
    finally:
        await graph.close()
        await store.close()


@pytest.mark.asyncio
async def test_owner_realistic_context_eval_fixture_passes(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        await _seed_owner_context_store(store)
        kb = _seed_owner_knowledge_base(tmp_path)
        await graph.submit_fact_candidate(
            subject="Omubot 管理端",
            predicate="采用",
            object="雾青控制台",
            confidence=0.9,
            source="test",
            evidence={"chunk_id": "docs/admin.md::style"},
        )

        service = ContextService([
            MemoryContextSource(store),
            KnowledgeContextSource(kb),
            GraphContextSource(graph),
        ])
        cases = load_context_eval_cases(OWNER_REALISTIC_FIXTURE_PATH)
        summary = await evaluate_context_cases(service, cases)

        assert summary.total_cases == 6
        assert summary.passed_cases == 6
        assert summary.required_hit_recall == 1.0
        assert summary.forbidden_violations == 0
        assert summary.duplicate_hits == 0
        assert summary.pack_budget_violations == 0
    finally:
        await graph.close()
        await store.close()


class _StaticSource:
    name = "static"

    def __init__(self, hits: list[ContextHit]) -> None:
        self._hits = hits

    async def search(
        self,
        query: str,
        *,
        user_id: str = "",
        group_id: str | None = None,
        top_k: int = 8,
    ) -> list[ContextHit]:
        del query, user_id, group_id
        return self._hits[:top_k]


async def _seed_owner_context_store(store: CardStore) -> None:
    await store.add_card(NewCard(
        category="preference",
        scope="user",
        scope_id="u_owner",
        content="睡前使用蒸汽眼罩会让主人更容易放松",
    ))
    await store.add_card(NewCard(
        category="fact",
        scope="group",
        scope_id="g_table",
        content="本群采购蒸汽眼罩只是闲聊，不应出现在私聊上下文",
    ))
    await store.add_card(NewCard(
        category="fact",
        scope="group",
        scope_id="g_other",
        content="其他群也在聊蒸汽眼罩",
    ))
    await store.add_card(NewCard(
        category="event",
        scope="group",
        scope_id="g_table",
        content="本群正在跑团地下城，下一次开团先整理人物卡",
    ))
    await store.add_card(NewCard(
        category="fact",
        scope="user",
        scope_id="u_owner",
        content="个人跑团地下城剧透只应留在私聊，不应进入群聊上下文",
    ))
    await store.add_card(NewCard(
        category="event",
        scope="group",
        scope_id="g_other",
        content="其他群跑团地下城安排是周末晚上",
    ))


def _seed_owner_knowledge_base(tmp_path) -> KnowledgeBase:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "deepseek.md").write_text(
        "# DeepSeek V4 缓存\n\n"
        "## 稳定前缀\n"
        "DeepSeek V4 模式下，动态上下文必须进入 tail metadata，避免污染稳定前缀。\n",
        encoding="utf-8",
    )
    (docs / "ops.md").write_text(
        "# 运行维护\n\n"
        "## NapCat\n"
        "如果 NapCat 连接失败，先检查 WebSocket 地址、反向连接配置和容器网络。\n",
        encoding="utf-8",
    )
    kb = KnowledgeBase(str(docs))
    kb.reload()
    return kb
