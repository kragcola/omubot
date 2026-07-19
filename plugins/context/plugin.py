"""ContextPlugin: unified dynamic context prompt injection."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any, cast

from loguru import logger
from pydantic import BaseModel, Field

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext, PromptContext

_L = logger.bind(channel="system")
_SEMANTIC_QUERY_RE = re.compile(r"[0-9A-Za-z㐀-鿿ぁ-ヿ가-힯]")


def _has_semantic_query_text(query: str) -> bool:
    return bool(_SEMANTIC_QUERY_RE.search(query or ""))


class ContextBudgetConfig(BaseModel):
    total_tokens: int = 6000
    memory_tokens: int = 1500
    doc_tokens: int = 2500
    graph_tokens: int = 1700
    buffer_tokens: int = 300


class TemporalTraceConfigModel(BaseModel):
    """Runtime bounds mirror plugins/context/config.schema.json temporal_trace."""

    enabled: bool = True
    max_heads: int = Field(default=24, ge=1, le=24)
    max_kps: int = Field(default=2, ge=1, le=2)
    max_depth: int = Field(default=4, ge=1, le=4)
    max_chars: int = Field(default=600, ge=100, le=600)


class QueryAwarePlanConfigModel(BaseModel):
    """Kill-switch for Query-Aware Retrieval Planner v1 (qa_rg_v1)."""

    enabled: bool = True


class CardEligibilityConfigModel(BaseModel):
    """Card category time eligibility for prompt recall (v1).

    Read-time only; does not write status or interpret ``ttl_turns``.
    Default: status=30d, event=180d; other categories do not auto-expire.
    """

    enabled: bool = True
    status_ttl_days: int = Field(default=30, ge=1, le=3650)
    event_ttl_days: int = Field(default=180, ge=1, le=3650)


class PackEvidenceGateConfigModel(BaseModel):
    """Pack-time confidence/evidence gate (v1).

    Evidence-aware tiering after search RRF/type caps and before pack.
    Disabled path is strict identity. Soft thresholds are [0, 1].
    """

    enabled: bool = True
    memory_soft_confidence: float = Field(default=0.45, ge=0.0, le=1.0)
    graph_soft_confidence: float = Field(default=0.60, ge=0.0, le=1.0)


class EvidenceUseContractConfigModel(BaseModel):
    """Evidence-use / pack-state contract v1 (euc_v1).

    Observability + optional soft constrained instruction for empty /
    hint_only / omit_only (final pack has no usable non-hint evidence).
    demote_present with a surviving demoted hit does not inject.
    Not proof of grounding. Disabled path is identity (no instruction inject).
    """

    enabled: bool = True
    inject_constrained_instruction: bool = True


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
    temporal_trace: TemporalTraceConfigModel = TemporalTraceConfigModel()
    query_aware_plan: QueryAwarePlanConfigModel = QueryAwarePlanConfigModel()
    card_eligibility: CardEligibilityConfigModel = CardEligibilityConfigModel()
    pack_evidence_gate: PackEvidenceGateConfigModel = PackEvidenceGateConfigModel()
    evidence_use_contract: EvidenceUseContractConfigModel = EvidenceUseContractConfigModel()


class ContextPlugin(AmadeusPlugin):
    name = "context"
    description = "统一聚合记忆卡片、文档知识和未来图谱事实的系统级上下文插件"
    version = "0.1.14"
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
        self._service: Any = None
        self._graph: Any = None
        self._pending_graph_tasks: set[asyncio.Task[dict[str, int]]] = set()
        self._temporal_trace_enabled = True
        self._temporal_trace_config: dict[str, Any] | TemporalTraceConfigModel = {
            "enabled": True,
            "max_heads": 24,
            "max_kps": 2,
            "max_depth": 4,
            "max_chars": 600,
        }
        self._temporal_trace_assembler: Any | None = None
        self._group_memory_config: Any | None = None
        self._query_aware_plan_enabled = True
        self._card_eligibility_enabled = True
        self._card_eligibility_policy: Any = None
        self._pack_evidence_gate_policy: Any = None
        self._evidence_use_contract_policy: Any = None
        self._evidence_use_contract_enabled = True
        self._evidence_use_inject = True

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
        self._query_aware_plan_enabled = bool(cfg.query_aware_plan.enabled)

        from services.memory.card_eligibility import CardEligibilityPolicy

        self._card_eligibility_enabled = bool(cfg.card_eligibility.enabled)
        self._card_eligibility_policy = CardEligibilityPolicy(
            enabled=bool(cfg.card_eligibility.enabled),
            category_ttl_days={
                "status": int(cfg.card_eligibility.status_ttl_days),
                "event": int(cfg.card_eligibility.event_ttl_days),
            },
        )

        from services.context.pack_evidence_gate import PackEvidenceGatePolicy

        peg = cfg.pack_evidence_gate
        self._pack_evidence_gate_policy = PackEvidenceGatePolicy(
            enabled=bool(peg.enabled),
            memory_soft_confidence=float(peg.memory_soft_confidence),
            graph_soft_confidence=float(peg.graph_soft_confidence),
        )

        from services.context.evidence_use_contract import EvidenceUseContractPolicy

        euc = cfg.evidence_use_contract
        self._evidence_use_contract_enabled = bool(euc.enabled)
        self._evidence_use_inject = bool(euc.inject_constrained_instruction)
        self._evidence_use_contract_policy = EvidenceUseContractPolicy(
            enabled=bool(euc.enabled),
            inject_constrained_instruction=bool(euc.inject_constrained_instruction),
        )
        # Apply to shared RetrievalGate when present (identity when disabled).
        gate = getattr(ctx, "retrieval", None)
        if gate is not None and hasattr(gate, "set_card_eligibility"):
            gate.set_card_eligibility(self._card_eligibility_policy)
        elif gate is not None and hasattr(gate, "_card_eligibility"):
            gate._card_eligibility = self._card_eligibility_policy

        tt = cfg.temporal_trace
        self._temporal_trace_enabled = bool(tt.enabled)
        self._temporal_trace_config = tt
        self._temporal_trace_assembler = None
        self._group_memory_config = getattr(ctx, "group_memory_config", None)

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

        # ChatPlugin (priority=0) pre-creates ctx.context_service with library defaults so the
        # bot never crashes if ContextPlugin is disabled. We always overwrite here so the configured
        # rrf_k / rrf_weights / budget actually reach the live retrieval path — otherwise admins
        # editing config.json would never see their RRF tuning take effect.
        self._service = ContextService.from_runtime(
            ctx,
            bus=ctx.bus,
            include_memory=self._takeover,
            rrf_k=self._rrf_k,
            rrf_weights=self._rrf_weights,
            budget=self._budget,
            pack_evidence_gate=self._pack_evidence_gate_policy,
            evidence_use_contract=self._evidence_use_contract_policy,
        )
        self._graph = getattr(ctx, "knowledge_graph", None)
        ctx.context_service = self._service
        if self._takeover:
            ctx.context_prompt_owner = "context"

        if self._temporal_trace_enabled:
            store = (
                getattr(ctx, "card_store", None)
                or getattr(getattr(ctx, "memo", None), "card_store", None)
                or getattr(getattr(ctx, "context_service", None), "card_store", None)
            )
            if store is not None:
                try:
                    from services.memory.temporal_trace import (
                        TemporalTraceAssembler,
                        TemporalTraceConfig,
                    )

                    # Bounds already enforced by TemporalTraceConfigModel (schema-aligned).
                    self._temporal_trace_assembler = TemporalTraceAssembler(
                        store,
                        config=TemporalTraceConfig(
                            enabled=True,
                            max_heads=int(tt.max_heads),
                            max_kps=int(tt.max_kps),
                            max_depth=int(tt.max_depth),
                            max_chars=int(tt.max_chars),
                        ),
                        group_memory_config=self._group_memory_config,
                        card_eligibility=self._card_eligibility_policy,
                    )
                except Exception as exc:
                    _L.warning(
                        "temporal_trace assembler unavailable | error={}",
                        type(exc).__name__,
                    )
                    self._temporal_trace_assembler = None

        _L.info(
            "context plugin enabled | takeover={} max_hits={} max_doc_hits={} "
            "use_token_budget={} budget_total={} max_chars_legacy={} rrf_k={} rrf_weights={} "
            "temporal_trace={} query_aware_plan={} card_eligibility={} pack_evidence_gate={} "
            "evidence_use_contract={}",
            self._takeover,
            self._max_hits,
            self._max_doc_hits,
            self._use_token_budget,
            self._budget.total_tokens,
            self._max_chars,
            self._rrf_k,
            self._rrf_weights,
            self._temporal_trace_enabled,
            self._query_aware_plan_enabled,
            self._card_eligibility_enabled,
            bool(getattr(self._pack_evidence_gate_policy, "enabled", True)),
            bool(getattr(self._evidence_use_contract_policy, "enabled", True)),
        )

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if not self._enabled or self._service is None:
            return
        # Prefer thinker's rewritten query (decontextualized, named-entity-expanded)
        # so the retrieval sees a self-contained question rather than the noisy
        # raw "recent + pending" join. Empty rewritten_query (skip / wait /
        # thinker disabled / parse failure) silently falls back to conversation_text
        # — zero-break compatibility with legacy callers.
        rewritten = (getattr(ctx, "rewritten_query", "") or "").strip()
        query = rewritten or ctx.conversation_text.strip()
        retrieve_mode = getattr(ctx, "retrieve_mode", "hybrid") or "hybrid"
        pack: Any = None
        run_ordinary = bool(query) and _has_semantic_query_text(query)

        if not run_ordinary:
            if query:
                _L.info(
                    "context prompt pack skipped | query_source={} query={!r} reason=punctuation_only",
                    "rewritten" if rewritten else "raw",
                    _safe_query(query),
                )
            # Ordinary pack skipped; still allow temporal-trace for fact/hybrid
            # when current_message carries historical/premise authorization.
            await self._maybe_inject_temporal_trace(
                ctx,
                pack=None,
                retrieve_mode=retrieve_mode,
                rewritten=rewritten,
            )
            return

        query_source = "rewritten" if rewritten else "raw"
        # Query-Aware Planner v1: after query/mode resolution, before build_prompt_context.
        # Fail-open to exact pre-v1 caps/budget/top_k when planner raises.
        top_k = self._max_hits
        type_caps: dict[str, int] = {"doc_chunk": self._max_doc_hits}
        pack_budget = self._budget
        plan_meta: dict[str, object] | None = None
        try:
            from services.context.query_plan import plan_query_aware_retrieval

            plan = plan_query_aware_retrieval(
                query=query,
                current_message=getattr(ctx, "current_message", "") or "",
                retrieve_mode=retrieve_mode,
                max_hits=self._max_hits,
                max_doc_hits=self._max_doc_hits,
                budget=self._budget,
                enabled=self._query_aware_plan_enabled,
            )
            # retrieve_mode is intentionally never taken from plan — preserve Thinker mode.
            top_k = int(plan.top_k)
            type_caps = dict(plan.type_caps)
            if self._use_token_budget:
                pack_budget = plan.budget
            plan_meta = plan.to_metrics()
        except Exception as exc:
            _L.warning(
                "query_aware_plan failed open | error={}",
                type(exc).__name__,
            )
            top_k = self._max_hits
            type_caps = {"doc_chunk": self._max_doc_hits}
            pack_budget = self._budget
            plan_meta = None

        # PR5: thinker decided which sources to query. "skip" → no retrieval injection.
        # PR6 fix: still call build_prompt_context so ContextService records the skip event in
        # metrics (recent / hit_source_counts), otherwise admin /context/metrics misses skip
        # traffic entirely. ContextService.search short-circuits to zero source calls when mode=skip.
        t0 = asyncio.get_running_loop().time()
        if self._use_token_budget:
            pack = await self._service.build_prompt_context(
                query,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                top_k=top_k,
                budget=pack_budget,
                type_caps=type_caps,
                mode=retrieve_mode,
                plan_meta=plan_meta,
            )
        else:
            pack = await self._service.build_prompt_context(
                query,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                top_k=top_k,
                max_chars=self._max_chars,
                type_caps=type_caps,
                mode=retrieve_mode,
                plan_meta=plan_meta,
            )
        elapsed_ms = (asyncio.get_running_loop().time() - t0) * 1000
        _L.info(
            "context prompt pack | mode={} query_source={} query={!r} hits={} types={} doc_chunks={} "
            "pack_chars={} omitted={} elapsed={:.1f}ms sources={} plan_profile={}",
            retrieve_mode,
            query_source,
            _safe_query(query),
            len(pack.hits),
            dict(Counter(str(getattr(hit, "type", "")) for hit in pack.hits)),
            sum(1 for hit in pack.hits if getattr(hit, "type", "") == "doc_chunk"),
            len(pack.text),
            pack.omitted_count,
            elapsed_ms,
            _hit_sources(pack.hits),
            (plan_meta or {}).get("profile_id", ""),
        )
        if pack.text:
            ctx.add_block(
                text=pack.text,
                label="上下文资料",
                position="dynamic",
                priority=50,
                source="context",
            )
        self._maybe_inject_evidence_use_instruction(ctx, pack=pack)
        if self._graph_auto_extract and self._graph is not None and pack.hits:
            self._schedule_graph_extract(pack.hits)

        await self._maybe_inject_temporal_trace(
            ctx,
            pack=pack,
            retrieve_mode=retrieve_mode,
            rewritten=rewritten,
        )

    def _maybe_inject_evidence_use_instruction(
        self,
        ctx: PromptContext,
        *,
        pack: Any,
    ) -> None:
        """Inject soft constrained-mode Chinese block when contract says so.

        Injectable states: empty / hint_only / omit_only (final pack has no
        usable non-hint evidence). demote_present with survivors does not
        inject. Kill-switch / contract.identity / inject_instruction=false
        → no-op. Never forces pass_turn, silence, or hard abstention. Does
        not alter TemporalTrace seeds.
        """
        if not self._enabled or not self._evidence_use_contract_enabled:
            return
        if not self._evidence_use_inject:
            return
        contract = getattr(pack, "evidence_use_contract", None)
        if contract is None:
            return
        if getattr(contract, "identity", False):
            return
        if not bool(getattr(contract, "inject_instruction", False)):
            return
        from services.context.evidence_use_contract import (
            CONSTRAINED_MODE_INSTRUCTION_ZH,
            INSTRUCTION_BLOCK_LABEL,
            INSTRUCTION_BLOCK_PRIORITY,
            INSTRUCTION_BLOCK_SOURCE,
        )

        ctx.add_block(
            text=CONSTRAINED_MODE_INSTRUCTION_ZH,
            label=INSTRUCTION_BLOCK_LABEL,
            position="dynamic",
            priority=INSTRUCTION_BLOCK_PRIORITY,
            source=INSTRUCTION_BLOCK_SOURCE,
        )

    async def _maybe_inject_temporal_trace(
        self,
        ctx: PromptContext,
        *,
        pack: Any,
        retrieve_mode: str,
        rewritten: str,
    ) -> None:
        if not (
            self._enabled
            and self._takeover
            and self._temporal_trace_enabled
            and retrieve_mode in {"fact", "hybrid"}
        ):
            return
        assembler = getattr(self, "_temporal_trace_assembler", None)
        if assembler is None:
            return
        # Prefer pre-gate real memory ids (pack.trace_seed_ids) so TemporalTrace
        # still sees historical/premise seeds when ordinary pack demotes/omits.
        # Fallback: packed memory ids for old/fake packs without the field.
        seed_ids = getattr(pack, "trace_seed_ids", None) if pack is not None else None
        hits: list[str] = []
        if seed_ids:
            hits = [str(x) for x in seed_ids if x]
        else:
            for h in getattr(pack, "hits", None) or []:
                if getattr(h, "type", "") != "memory_card":
                    continue
                hid = getattr(h, "id", None) or getattr(h, "card_id", None)
                if hid and not str(hid).startswith("memory_hint:"):
                    hits.append(str(hid))
        try:
            result = await assembler.assemble(
                current_message=getattr(ctx, "current_message", "") or "",
                rewritten_query=rewritten,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                active_memory_hits=[x for x in hits if x],
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            result = None
        if result is not None and (getattr(result, "text", "") or "").strip():
            ctx.add_block(
                text=result.text,
                label="记忆时间轨迹",
                position="dynamic",
                priority=45,
                source="context_temporal_trace",
            )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        del ctx
        if not self._pending_graph_tasks:
            return
        for task in list(self._pending_graph_tasks):
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._pending_graph_tasks, return_exceptions=True)
        self._pending_graph_tasks.clear()

    def _schedule_graph_extract(self, hits: Sequence[object]) -> None:
        graph = self._graph
        if graph is None:
            return
        task = asyncio.create_task(cast(Any, graph).extract_from_context_hits(list(hits)))
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
            if summary.get("extracted") is None:
                return
            _L.info(
                "context graph auto extract completed | extracted={} accepted={} pending={} ignored={}",
                summary.get("extracted", 0),
                summary.get("accepted", 0),
                summary.get("pending", 0),
                summary.get("ignored", 0),
            )

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
