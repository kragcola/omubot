"""PR5 — retrieve_mode routing in ContextService.search / build_prompt_context.

Verifies the four mode lanes:

- skip   : no retrieval at all (empty hits, empty pack)
- doc    : only knowledge / document chunks
- fact   : only memory + graph
- hybrid : all three sources (default behavior)

Each mode test uses a stub set of three sources (memory / knowledge / graph)
and asserts which ones were actually called and what hits surface.
"""

from __future__ import annotations

import pytest

from services.context.service import ContextService
from services.context.types import ContextHit


class _StubSource:
    """Minimal stub matching the duck-typed source protocol used by ContextService."""

    def __init__(self, name: str, hits: list[ContextHit]) -> None:
        self.name = name
        self._hits = hits
        self.calls: int = 0

    async def search(
        self,
        query: str,
        *,
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        top_k: int = 10,
    ) -> list[ContextHit]:
        del query, session_id, user_id, group_id, top_k
        self.calls += 1
        return list(self._hits)


def _make_service() -> tuple[ContextService, _StubSource, _StubSource, _StubSource]:
    memory = _StubSource(
        "memory",
        [ContextHit(id="m1", type="memory_card", content="user-likes-docker", score=0.9, source="memory")],
    )
    knowledge = _StubSource(
        "knowledge",
        [ContextHit(id="k1", type="doc_chunk", content="docker compose up", score=0.7, source="knowledge")],
    )
    graph = _StubSource(
        "graph",
        [ContextHit(id="g1", type="graph_fact", content="user 喜欢 docker", score=0.6, source="graph")],
    )
    service = ContextService([memory, knowledge, graph])
    return service, memory, knowledge, graph


@pytest.mark.asyncio
async def test_search_skip_mode_calls_no_sources() -> None:
    service, memory, knowledge, graph = _make_service()
    hits = await service.search("anything", mode="skip")
    assert hits == []
    assert memory.calls == 0
    assert knowledge.calls == 0
    assert graph.calls == 0


@pytest.mark.asyncio
async def test_search_doc_mode_only_hits_knowledge() -> None:
    service, memory, knowledge, graph = _make_service()
    hits = await service.search("omubot 怎么部署", mode="doc")
    assert memory.calls == 0
    assert knowledge.calls == 1
    assert graph.calls == 0
    assert {hit.type for hit in hits} == {"doc_chunk"}


@pytest.mark.asyncio
async def test_search_fact_mode_hits_memory_and_graph_only() -> None:
    service, memory, knowledge, graph = _make_service()
    hits = await service.search("1416930401 喜欢什么", mode="fact")
    assert memory.calls == 1
    assert knowledge.calls == 0
    assert graph.calls == 1
    assert {hit.type for hit in hits} == {"memory_card", "graph_fact"}


@pytest.mark.asyncio
async def test_search_hybrid_mode_hits_all_sources() -> None:
    service, memory, knowledge, graph = _make_service()
    hits = await service.search("综合查询", mode="hybrid")
    assert memory.calls == 1
    assert knowledge.calls == 1
    assert graph.calls == 1
    assert {hit.type for hit in hits} == {"memory_card", "doc_chunk", "graph_fact"}


@pytest.mark.asyncio
async def test_search_default_mode_is_hybrid() -> None:
    """Backward compat: callers that don't pass mode get the pre-PR5 behavior."""
    service, memory, knowledge, graph = _make_service()
    await service.search("默认行为")
    assert memory.calls == 1
    assert knowledge.calls == 1
    assert graph.calls == 1


@pytest.mark.asyncio
async def test_search_unknown_mode_falls_back_to_hybrid() -> None:
    service, memory, knowledge, graph = _make_service()
    hits = await service.search("非法 mode", mode="banana")
    assert len(hits) == 3
    assert memory.calls == 1
    assert knowledge.calls == 1
    assert graph.calls == 1


@pytest.mark.asyncio
async def test_build_prompt_context_skip_returns_empty_pack() -> None:
    service, memory, knowledge, graph = _make_service()
    pack = await service.build_prompt_context("hello", mode="skip")
    assert pack.text == ""
    assert pack.hits == []
    assert memory.calls == 0
    assert knowledge.calls == 0
    assert graph.calls == 0


@pytest.mark.asyncio
async def test_build_prompt_context_doc_only_renders_doc_chunk() -> None:
    service, _memory, _knowledge, _graph = _make_service()
    pack = await service.build_prompt_context("omubot 部署", mode="doc")
    assert any(hit.type == "doc_chunk" for hit in pack.hits)
    assert all(hit.type == "doc_chunk" for hit in pack.hits)


@pytest.mark.asyncio
async def test_build_prompt_context_fact_only_renders_memory_and_graph() -> None:
    service, _memory, _knowledge, _graph = _make_service()
    pack = await service.build_prompt_context("用户偏好", mode="fact")
    types = {hit.type for hit in pack.hits}
    assert "doc_chunk" not in types
    assert types.issubset({"memory_card", "graph_fact"})


@pytest.mark.asyncio
async def test_search_skip_records_metrics_with_mode() -> None:
    service, _memory, _knowledge, _graph = _make_service()
    await service.search("trace check", session_id="s1", mode="skip")
    recent = service.recent(limit=5)
    assert recent
    assert recent[-1]["retrieve_mode"] == "skip"
    assert recent[-1]["hit_count"] == 0


@pytest.mark.asyncio
async def test_search_doc_records_metrics_with_mode() -> None:
    service, _memory, _knowledge, _graph = _make_service()
    await service.search("trace check", session_id="s1", mode="doc")
    recent = service.recent(limit=5)
    assert recent[-1]["retrieve_mode"] == "doc"
