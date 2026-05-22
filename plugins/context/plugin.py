"""ContextPlugin: unified dynamic context prompt injection."""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter

from loguru import logger
from pydantic import BaseModel

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext, PromptContext

_L = logger.bind(channel="system")


class ContextBudgetConfig(BaseModel):
    total_tokens: int = 6000
    memory_tokens: int = 1500
    doc_tokens: int = 2500
    graph_tokens: int = 1700
    buffer_tokens: int = 300


class ContextConfig(BaseModel):
    enabled: bool = True
    takeover_dynamic_prompt: bool = True
    max_hits: int = 5
    max_doc_hits: int = 3
    max_chars: int = 2400
    graph_auto_extract: bool = True
    rrf_k: int = 60
    rrf_weights: dict[str, float] = {"doc": 0.5, "memory": 0.3, "graph": 0.2}
    budget: ContextBudgetConfig = ContextBudgetConfig()
    use_token_budget: bool = True


class ContextPlugin(AmadeusPlugin):
    name = "context"
    description = "统一上下文：聚合记忆卡片、文档知识和图谱事实"
    version = "0.1.0"
    priority = 7

    def __init__(self) -> None:
        super().__init__()
        self._enabled = False
        self._takeover = True
        self._max_hits = 5
        self._max_doc_hits = 3
        self._max_chars = 2400
        self._graph_auto_extract = True
        self._rrf_k = 60
        self._rrf_weights: dict[str, float] = {"doc": 0.5, "memory": 0.3, "graph": 0.2}
        self._budget = None  # services.context.packing.ContextBudget; resolved in on_startup
        self._use_token_budget = True
        self._service = None
        self._graph = None
        self._pending_graph_tasks: set[asyncio.Task[dict[str, int]]] = set()

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = load_plugin_config("plugins/context/config.default.json", ContextConfig)
        self._enabled = cfg.enabled
        self._takeover = cfg.takeover_dynamic_prompt
        self._max_hits = cfg.max_hits
        self._max_doc_hits = cfg.max_doc_hits
        self._max_chars = cfg.max_chars
        self._graph_auto_extract = cfg.graph_auto_extract
        self._rrf_k = max(1, int(cfg.rrf_k))
        self._rrf_weights = {k: float(v) for k, v in cfg.rrf_weights.items()}
        self._use_token_budget = cfg.use_token_budget

        if not self._enabled:
            _L.info("context plugin disabled; legacy memo/knowledge prompt injection remains active")
            return

        from services.context import ContextService
        from services.context.packing import ContextBudget

        self._budget = ContextBudget(
            total_tokens=cfg.budget.total_tokens,
            memory_tokens=cfg.budget.memory_tokens,
            doc_tokens=cfg.budget.doc_tokens,
            graph_tokens=cfg.budget.graph_tokens,
            buffer_tokens=cfg.budget.buffer_tokens,
        )

        self._service = getattr(ctx, "context_service", None) or ContextService.from_runtime(
            ctx,
            bus=ctx.bus,
            rrf_k=self._rrf_k,
            rrf_weights=self._rrf_weights,
            budget=self._budget,
        )
        self._graph = getattr(ctx, "knowledge_graph", None)
        ctx.context_service = self._service
        if self._takeover:
            ctx.context_prompt_owner = "context"
        _L.info(
            "context plugin enabled | takeover={} max_hits={} max_doc_hits={} "
            "use_token_budget={} budget_total={} max_chars_legacy={} rrf_k={} rrf_weights={}",
            self._takeover,
            self._max_hits,
            self._max_doc_hits,
            self._use_token_budget,
            self._budget.total_tokens,
            self._max_chars,
            self._rrf_k,
            self._rrf_weights,
        )

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if not self._enabled or self._service is None:
            return
        query = ctx.conversation_text.strip()
        if not query:
            return
        # PR5: thinker decided which sources to query. "skip" → no retrieval at all.
        retrieve_mode = getattr(ctx, "retrieve_mode", "hybrid") or "hybrid"
        if retrieve_mode == "skip":
            _L.debug("context skip | session={} reason=thinker_skip", ctx.session_id)
            return
        t0 = asyncio.get_running_loop().time()
        if self._use_token_budget:
            pack = await self._service.build_prompt_context(
                query,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                top_k=self._max_hits,
                budget=self._budget,
                type_caps={"doc_chunk": self._max_doc_hits},
                mode=retrieve_mode,
            )
        else:
            pack = await self._service.build_prompt_context(
                query,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                top_k=self._max_hits,
                max_chars=self._max_chars,
                type_caps={"doc_chunk": self._max_doc_hits},
                mode=retrieve_mode,
            )
        elapsed_ms = (asyncio.get_running_loop().time() - t0) * 1000
        _L.debug(
            "context prompt pack | mode={} query={!r} hits={} types={} doc_chunks={} "
            "pack_chars={} omitted={} elapsed={:.1f}ms sources={}",
            retrieve_mode,
            _safe_query(query),
            len(pack.hits),
            dict(Counter(str(getattr(hit, "type", "")) for hit in pack.hits)),
            sum(1 for hit in pack.hits if getattr(hit, "type", "") == "doc_chunk"),
            len(pack.text),
            pack.omitted_count,
            elapsed_ms,
            _hit_sources(pack.hits),
        )
        if pack.text:
            ctx.add_block(
                text=pack.text,
                label="上下文资料",
                position="dynamic",
                priority=50,
                source="context",
            )
        if self._graph_auto_extract and self._graph is not None and pack.hits:
            self._schedule_graph_extract(pack.hits)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        del ctx
        if not self._pending_graph_tasks:
            return
        for task in list(self._pending_graph_tasks):
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._pending_graph_tasks, return_exceptions=True)
        self._pending_graph_tasks.clear()

    def _schedule_graph_extract(self, hits: list[object]) -> None:
        task = asyncio.create_task(self._graph.extract_from_context_hits(list(hits)))
        self._pending_graph_tasks.add(task)

        def _on_done(done: asyncio.Task[dict[str, int]]) -> None:
            self._pending_graph_tasks.discard(done)
            if done.cancelled():
                return
            try:
                summary = done.result()
            except Exception as exc:
                _L.warning("context graph auto extract failed | error={}", type(exc).__name__)
                return
            if summary.get("extracted"):
                _L.debug("context graph auto extract completed | summary={}", summary)

        task.add_done_callback(_on_done)


def _safe_query(query: str, limit: int = 80) -> str:
    text = " ".join((query or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _hit_sources(hits: list[object], limit: int = 4) -> list[str]:
    sources: list[str] = []
    for hit in hits:
        if len(sources) >= limit:
            break
        hit_type = str(getattr(hit, "type", "") or "")
        source = str(getattr(hit, "source", "") or "")
        title = str(getattr(hit, "title", "") or "")
        if not source and not title:
            continue
        label = f"{hit_type}:{source}"
        if title:
            label += f"::{title}"
        sources.append(_safe_query(label, limit=120))
    return sources
