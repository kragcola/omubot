"""Regression tests for ContextPlugin prompt takeover safety."""

from __future__ import annotations

import asyncio

import pytest

from kernel.bus import PluginBus
from kernel.types import Identity, PromptContext
from plugins.context.plugin import ContextPlugin
from plugins.knowledge.plugin import KnowledgePlugin
from plugins.memo.plugin import MemoPlugin
from services.context.types import ContextHit, ContextPack
from services.memory.card_store import CardStore, NewCard


@pytest.mark.asyncio
async def test_context_takeover_suppresses_legacy_dynamic_prompt_blocks(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="preference",
            scope="user",
            scope_id="123",
            content="喜欢 Docker Compose 部署方式",
        ))

        bus = PluginBus()
        context = _enabled_context_plugin()
        knowledge = _enabled_knowledge_plugin(context_takeover=True)
        memo = _enabled_memo_plugin(store, context_takeover=True)
        bus.register(context)
        bus.register(knowledge)
        bus.register(memo)

        prompt_ctx = _prompt_ctx()
        await bus.fire_on_pre_prompt(prompt_ctx)

        labels = [block.label for block in prompt_ctx.blocks]
        assert labels.count("上下文资料") == 1
        assert "知识库" not in labels
        assert "记忆卡片" not in labels
        assert "全局索引" in labels
        assert "统一文档资料" in _block_text(prompt_ctx, "上下文资料")
        assert "统一记忆资料" in _block_text(prompt_ctx, "上下文资料")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_prompt_blocks_return_when_context_takeover_is_disabled(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="preference",
            scope="user",
            scope_id="123",
            content="喜欢 Docker Compose 部署方式",
        ))

        bus = PluginBus()
        knowledge = _enabled_knowledge_plugin(context_takeover=False)
        memo = _enabled_memo_plugin(store, context_takeover=False)
        bus.register(knowledge)
        bus.register(memo)

        prompt_ctx = _prompt_ctx()
        await bus.fire_on_pre_prompt(prompt_ctx)

        labels = [block.label for block in prompt_ctx.blocks]
        assert "上下文资料" not in labels
        assert "知识库" in labels
        assert "记忆卡片" in labels
        assert "旧知识库资料" in _block_text(prompt_ctx, "知识库")
        assert "喜欢 Docker Compose 部署方式" in _block_text(prompt_ctx, "记忆卡片")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_context_plugin_auto_extracts_graph_candidates_after_prompt_pack() -> None:
    graph = _FakeGraph()
    plugin = _enabled_context_plugin()
    plugin._graph_auto_extract = True
    plugin._graph = graph

    prompt_ctx = _prompt_ctx()
    await plugin.on_pre_prompt(prompt_ctx)
    await asyncio.sleep(0)

    assert [block.label for block in prompt_ctx.blocks] == ["上下文资料"]
    assert len(graph.extracted_batches) == 1
    assert [hit.type for hit in graph.extracted_batches[0]] == ["memory_card", "doc_chunk"]


def _enabled_context_plugin() -> ContextPlugin:
    from services.context.packing import DEFAULT_BUDGET
    plugin = ContextPlugin()
    plugin._enabled = True
    plugin._takeover = True
    plugin._max_hits = 10
    plugin._max_chars = 1200
    plugin._graph_auto_extract = False
    plugin._service = _FakeContextService()
    plugin._budget = DEFAULT_BUDGET
    plugin._use_token_budget = True
    return plugin


def _enabled_knowledge_plugin(*, context_takeover: bool) -> KnowledgePlugin:
    plugin = KnowledgePlugin()
    plugin._enabled = True
    plugin._kb = _FakeKnowledgeBase()
    plugin._max_chunks = 3
    plugin._context_takeover = context_takeover
    return plugin


def _enabled_memo_plugin(store: CardStore, *, context_takeover: bool) -> MemoPlugin:
    plugin = MemoPlugin()
    plugin._card_store = store
    plugin._retrieval = None
    plugin._context_takeover = context_takeover
    return plugin


def _prompt_ctx() -> PromptContext:
    return PromptContext(
        session_id="private_123",
        group_id=None,
        user_id="123",
        identity=Identity(id="bot", name="Bot", personality="test"),
        conversation_text="Docker Compose",
    )


def _block_text(ctx: PromptContext, label: str) -> str:
    for block in ctx.blocks:
        if block.label == label:
            return block.text
    return ""


class _FakeContextService:
    async def build_prompt_context(
        self,
        query: str,
        *,
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        top_k: int = 10,
        max_chars: int | None = None,
        budget: object | None = None,
        type_caps: dict[str, int] | None = None,
        mode: str = "hybrid",
    ) -> ContextPack:
        del query, session_id, user_id, group_id, top_k, max_chars, budget, type_caps, mode
        return ContextPack(
            text="【记忆卡片】\n- [用户记忆] 统一记忆资料\n\n【文档资料】\n- [部署手册] 统一文档资料",
            hits=[
                ContextHit(
                    id="mem_1",
                    type="memory_card",
                    content="统一记忆资料",
                    score=0.9,
                    source="test",
                ),
                ContextHit(
                    id="doc_1",
                    type="doc_chunk",
                    content="统一文档资料",
                    score=0.8,
                    source="test",
                ),
            ],
        )


class _FakeKnowledgeBase:
    def retrieve(self, query: str, top_k: int = 3) -> list[str]:
        del query, top_k
        return ["旧知识库资料"]


class _FakeGraph:
    def __init__(self) -> None:
        self.extracted_batches: list[list[ContextHit]] = []

    async def extract_from_context_hits(self, hits: list[ContextHit]) -> dict[str, int]:
        self.extracted_batches.append(list(hits))
        return {"extracted": len(hits), "accepted": 0, "pending": len(hits), "ignored": 0}
