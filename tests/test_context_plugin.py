"""Regression tests for ContextPlugin prompt takeover safety."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from kernel.bus import PluginBus
from kernel.types import Identity, PromptContext
from plugins.context.plugin import ContextConfig, ContextPlugin
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


@pytest.mark.asyncio
async def test_context_plugin_skip_mode_still_records_metrics_via_service() -> None:
    """PR6 fix: skip mode must reach ContextService.search so metrics include skip traffic.

    Pre-PR6 the plugin returned early on skip, leaving /context/metrics blind to the
    proportion of conversations that bypassed retrieval.
    """
    fake = _FakeContextService()
    plugin = _enabled_context_plugin()
    plugin._service = fake

    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "skip"
    await plugin.on_pre_prompt(prompt_ctx)

    assert len(fake.calls) == 1
    assert fake.calls[0]["mode"] == "skip"
    assert fake.calls[0]["query"] == "Docker Compose"
    # Skip pack returns empty text (FakeContextService obeys the contract via the mode kwarg below)
    assert all(block.label != "上下文资料" or block.text for block in prompt_ctx.blocks)


@pytest.mark.asyncio
async def test_context_plugin_skips_punctuation_only_query() -> None:
    fake = _FakeContextService()
    plugin = _enabled_context_plugin()
    plugin._service = fake

    prompt_ctx = _prompt_ctx()
    prompt_ctx.conversation_text = "。"
    await plugin.on_pre_prompt(prompt_ctx)

    assert fake.calls == []
    assert [block.label for block in prompt_ctx.blocks] == []


@pytest.mark.asyncio
async def test_context_plugin_on_startup_overrides_pre_existing_service(tmp_path) -> None:
    """PR6 fix: ChatPlugin (priority=0) pre-creates ctx.context_service with library
    defaults; ContextPlugin (priority=7) MUST overwrite it so configured RRF / budget
    parameters reach the live retrieval path.
    """
    from types import SimpleNamespace

    pre_existing_service = object()
    ctx = SimpleNamespace(
        context_service=pre_existing_service,
        bus=None,
        card_store=None,
        knowledge_base=None,
        knowledge_graph=None,
        group_memory_config=None,
    )

    plugin = ContextPlugin()
    plugin._enabled = True
    await plugin.on_startup(ctx)  # type: ignore[arg-type]

    assert ctx.context_service is not pre_existing_service, (
        "ContextPlugin must replace the ChatPlugin-defaulted service so configured "
        "rrf_k / rrf_weights / budget take effect"
    )
    assert ctx.context_service is plugin._service
    # The newly built service must carry the configured RRF parameters
    assert plugin._service is not None
    assert getattr(plugin._service, "_rrf_k", None) == plugin._rrf_k
    assert getattr(plugin._service, "_rrf_weights", None) == plugin._rrf_weights
    del tmp_path  # unused; pytest fixture only


@pytest.mark.asyncio
async def test_context_non_takeover_excludes_memory_source(monkeypatch) -> None:
    """Legacy MemoPlugin must remain the sole dynamic-memory owner."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "plugins.context.plugin.load_plugin_config",
        lambda *_args, **_kwargs: ContextConfig(takeover_dynamic_prompt=False),
    )
    ctx = SimpleNamespace(
        context_service=None,
        context_prompt_owner="",
        bus=None,
        card_store=object(),
        retrieval=object(),
        knowledge_base=None,
        knowledge_graph=None,
        group_memory_config=None,
    )

    plugin = ContextPlugin()
    await plugin.on_startup(ctx)  # type: ignore[arg-type]

    source_names = [
        str(getattr(source, "name", ""))
        for source in getattr(plugin._service, "_sources", [])
    ]
    assert "memory" not in source_names
    assert ctx.context_prompt_owner == ""


def _enabled_context_plugin() -> ContextPlugin:
    from services.context.packing import DEFAULT_BUDGET
    plugin = ContextPlugin()
    plugin._enabled = True
    plugin._takeover = True
    plugin._max_hits = 10
    plugin._max_doc_hits = 3
    plugin._max_chars = 1200
    plugin._graph_auto_extract = False
    plugin._service = _FakeContextService()
    plugin._budget = DEFAULT_BUDGET
    plugin._use_token_budget = True
    plugin._query_aware_plan_enabled = True
    return plugin


def _enabled_knowledge_plugin(*, context_takeover: bool) -> KnowledgePlugin:
    plugin = KnowledgePlugin()
    plugin._enabled = True
    plugin._kb = cast(Any, _FakeKnowledgeBase())
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
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

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
        plan_meta: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ContextPack:
        del session_id, user_id, group_id, kwargs
        self.calls.append({
            "mode": mode,
            "query": query,
            "top_k": top_k,
            "type_caps": dict(type_caps or {}),
            "budget": budget,
            "max_chars": max_chars,
            "plan_meta": plan_meta,
        })
        if mode == "skip":
            return ContextPack(text="", hits=[])
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


# ===========================================================================
# Temporal trace sidecar (R9/R10/R12/R13 version/config/mode/cancel)
# ===========================================================================


def _trace_labels(ctx: PromptContext) -> list[str]:
    return [b.label for b in ctx.blocks]


def _trace_blocks(ctx: PromptContext) -> list[Any]:
    return [b for b in ctx.blocks if b.label == "记忆时间轨迹"]


class _FakeTemporalTrace:
    """Minimal DTO matching TemporalTraceAssembler result surface."""

    def __init__(self, text: str, reason: str = "historical_intent") -> None:
        self.text = text
        self.reason = reason
        self.knowledge_points: list[dict[str, Any]] = [
            {
                "current": "用户住在上海",
                "earlier": ["用户住在杭州"],
                "trajectory": "杭州 → 上海",
                "evidence_refs": ["card_sh", "m_sh"],
                "reason": reason,
                "confidence": 0.8,
            }
        ]


class _FakeTraceAssembler:
    def __init__(
        self,
        result: Any = None,
        *,
        exc: BaseException | None = None,
    ) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def assemble(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


def _plugin_with_trace(
    *,
    takeover: bool = True,
    enabled: bool = True,
    temporal_enabled: bool = True,
    assembler: Any | None = None,
    retrieve_mode: str = "hybrid",
) -> ContextPlugin:
    plugin = _enabled_context_plugin()
    plugin._enabled = enabled
    plugin._takeover = takeover
    # GREEN surfaces — attribute names locked by these tests.
    plugin._temporal_trace_enabled = temporal_enabled
    plugin._temporal_trace_config = {
        "enabled": temporal_enabled,
        "max_heads": 24,
        "max_kps": 2,
        "max_depth": 4,
        "max_chars": 600,
    }
    if assembler is not None:
        plugin._temporal_trace_assembler = assembler
    del retrieve_mode  # mode is set on PromptContext, not plugin
    return plugin


def test_context_plugin_version_is_0_1_14() -> None:
    """ContextPlugin.version must bump to 0.1.14 with evidence_use_contract."""
    assert ContextPlugin.version == "0.1.14"


def test_context_plugin_manifest_version_is_0_1_14() -> None:
    import json
    from pathlib import Path

    manifest_path = Path(__file__).resolve().parents[1] / "plugins" / "context" / "plugin.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["version"] == "0.1.14"


def test_context_config_default_has_temporal_trace_nested() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "context"
        / "config.default.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data["values"]
    assert "temporal_trace" in values
    tt = values["temporal_trace"]
    assert tt["enabled"] is True
    assert tt["max_heads"] == 24
    assert tt["max_kps"] == 2
    assert tt["max_depth"] == 4
    assert tt["max_chars"] == 600


def test_context_config_schema_validates_temporal_trace_bounds() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "context"
        / "config.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    props = schema["properties"]
    assert "temporal_trace" in props
    tt = props["temporal_trace"]
    assert tt["type"] == "object"
    tt_props = tt["properties"]
    for key in ("enabled", "max_heads", "max_kps", "max_depth", "max_chars"):
        assert key in tt_props
    # Exact schema bounds (no defaults / no "may be higher" leeway).
    assert tt_props["max_heads"]["minimum"] == 1
    assert tt_props["max_heads"]["maximum"] == 24
    assert tt_props["max_kps"]["minimum"] == 1
    assert tt_props["max_kps"]["maximum"] == 2
    assert tt_props["max_depth"]["minimum"] == 1
    assert tt_props["max_depth"]["maximum"] == 4
    assert tt_props["max_chars"]["minimum"] == 100
    assert tt_props["max_chars"]["maximum"] == 600


def test_temporal_trace_config_model_field_bounds_match_schema() -> None:
    """Runtime pydantic bounds must reject out-of-range values and accept edges."""
    from pydantic import ValidationError

    from plugins.context.plugin import TemporalTraceConfigModel

    # Exact boundaries accept.
    TemporalTraceConfigModel(max_heads=1, max_kps=1, max_depth=1, max_chars=100)
    TemporalTraceConfigModel(max_heads=24, max_kps=2, max_depth=4, max_chars=600)

    # Invalid low / high reject.
    for kwargs in (
        {"max_heads": 0},
        {"max_heads": 25},
        {"max_kps": 0},
        {"max_kps": 3},
        {"max_depth": 0},
        {"max_depth": 5},
        {"max_chars": 99},
        {"max_chars": 601},
        {"max_chars": 1},
    ):
        with pytest.raises(ValidationError):
            TemporalTraceConfigModel(**kwargs)


def test_context_plugin_json_restart_required_includes_temporal_trace() -> None:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "plugins" / "context" / "plugin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = data["config"]["restart_required_fields"]
    # Nested object or dotted keys both acceptable.
    assert any(
        f == "temporal_trace" or str(f).startswith("temporal_trace")
        for f in fields
    )


def test_context_config_model_has_temporal_trace_nested() -> None:
    """R11: pydantic ContextConfig exposes nested temporal_trace with defaults."""
    cfg = ContextConfig()
    assert hasattr(cfg, "temporal_trace")
    tt = cfg.temporal_trace
    enabled = getattr(tt, "enabled", None) if not isinstance(tt, dict) else tt.get("enabled")
    max_heads = getattr(tt, "max_heads", None) if not isinstance(tt, dict) else tt.get("max_heads")
    max_kps = getattr(tt, "max_kps", None) if not isinstance(tt, dict) else tt.get("max_kps")
    max_depth = getattr(tt, "max_depth", None) if not isinstance(tt, dict) else tt.get("max_depth")
    max_chars = getattr(tt, "max_chars", None) if not isinstance(tt, dict) else tt.get("max_chars")
    assert enabled is True
    assert max_heads == 24
    assert max_kps == 2
    assert max_depth == 4
    assert max_chars == 600


def test_context_config_default_has_query_aware_plan_enabled() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "context"
        / "config.default.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    qap = data["values"]["query_aware_plan"]
    assert qap["enabled"] is True


def test_context_config_schema_has_query_aware_plan() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "context"
        / "config.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    props = schema["properties"]
    assert "query_aware_plan" in props
    assert props["query_aware_plan"]["type"] == "object"
    assert "enabled" in props["query_aware_plan"]["properties"]


def test_context_plugin_json_restart_required_includes_query_aware_plan() -> None:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "plugins" / "context" / "plugin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = data["config"]["restart_required_fields"]
    assert any(
        f == "query_aware_plan" or str(f).startswith("query_aware_plan")
        for f in fields
    )


def test_context_config_model_has_query_aware_plan_nested() -> None:
    from plugins.context.plugin import QueryAwarePlanConfigModel

    cfg = ContextConfig()
    assert hasattr(cfg, "query_aware_plan")
    qap = cfg.query_aware_plan
    enabled = getattr(qap, "enabled", None) if not isinstance(qap, dict) else qap.get("enabled")
    assert enabled is True
    # Explicit disable accepted.
    assert QueryAwarePlanConfigModel(enabled=False).enabled is False


def test_context_config_default_has_card_eligibility() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "context"
        / "config.default.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    ce = data["values"]["card_eligibility"]
    assert ce["enabled"] is True
    assert ce["status_ttl_days"] == 30
    assert ce["event_ttl_days"] == 180


def test_context_config_schema_has_card_eligibility() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "context"
        / "config.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    props = schema["properties"]
    assert "card_eligibility" in props
    ce = props["card_eligibility"]
    assert ce["type"] == "object"
    ce_props = ce["properties"]
    assert "enabled" in ce_props
    assert ce_props["status_ttl_days"]["minimum"] == 1
    assert ce_props["status_ttl_days"]["maximum"] == 3650
    assert ce_props["event_ttl_days"]["minimum"] == 1
    assert ce_props["event_ttl_days"]["maximum"] == 3650


def test_context_plugin_json_restart_required_includes_card_eligibility() -> None:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "plugins" / "context" / "plugin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = data["config"]["restart_required_fields"]
    assert any(
        f == "card_eligibility" or str(f).startswith("card_eligibility")
        for f in fields
    )


def test_context_config_model_has_card_eligibility_nested() -> None:
    from plugins.context.plugin import CardEligibilityConfigModel

    cfg = ContextConfig()
    assert hasattr(cfg, "card_eligibility")
    ce = cfg.card_eligibility
    assert isinstance(ce, CardEligibilityConfigModel)
    assert ce.enabled is True
    assert ce.status_ttl_days == 30
    assert ce.event_ttl_days == 180


def test_context_config_default_has_pack_evidence_gate() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "context"
        / "config.default.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    peg = data["values"]["pack_evidence_gate"]
    assert peg["enabled"] is True
    assert peg["memory_soft_confidence"] == 0.45
    assert peg["graph_soft_confidence"] == 0.60


def test_context_config_schema_has_pack_evidence_gate() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "context"
        / "config.schema.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    props = data["properties"]
    assert "pack_evidence_gate" in props
    peg = props["pack_evidence_gate"]
    assert peg["type"] == "object"
    pprops = peg["properties"]
    assert pprops["memory_soft_confidence"]["minimum"] == 0
    assert pprops["memory_soft_confidence"]["maximum"] == 1
    assert pprops["graph_soft_confidence"]["minimum"] == 0
    assert pprops["graph_soft_confidence"]["maximum"] == 1


def test_context_plugin_json_restart_required_includes_pack_evidence_gate() -> None:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "plugins" / "context" / "plugin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = data["config"]["restart_required_fields"]
    assert any(
        f == "pack_evidence_gate" or str(f).startswith("pack_evidence_gate")
        for f in fields
    )


def test_context_config_model_has_pack_evidence_gate_nested() -> None:
    from plugins.context.plugin import PackEvidenceGateConfigModel

    cfg = ContextConfig()
    assert hasattr(cfg, "pack_evidence_gate")
    peg = cfg.pack_evidence_gate
    assert isinstance(peg, PackEvidenceGateConfigModel)
    assert peg.enabled is True
    assert peg.memory_soft_confidence == 0.45
    assert peg.graph_soft_confidence == 0.60


@pytest.mark.asyncio
async def test_query_aware_plan_wiring_preserves_retrieve_mode() -> None:
    fake = _FakeContextService()
    plugin = _enabled_context_plugin()
    plugin._service = fake
    plugin._query_aware_plan_enabled = True
    prompt_ctx = _prompt_ctx()
    prompt_ctx.conversation_text = "我喜欢吃什么"
    prompt_ctx.retrieve_mode = "fact"
    await plugin.on_pre_prompt(prompt_ctx)
    assert len(fake.calls) == 1
    assert fake.calls[0]["mode"] == "fact"
    assert fake.calls[0]["plan_meta"] is not None
    assert fake.calls[0]["plan_meta"]["mode"] == "fact"
    assert "preference" in fake.calls[0]["plan_meta"]["needs"]
    # Preference profile tightens doc occupancy.
    assert fake.calls[0]["type_caps"].get("doc_chunk", 0) <= 1


@pytest.mark.asyncio
async def test_query_aware_plan_disabled_is_identity_caps() -> None:
    fake = _FakeContextService()
    plugin = _enabled_context_plugin()
    plugin._service = fake
    plugin._max_hits = 5
    plugin._max_doc_hits = 3
    plugin._query_aware_plan_enabled = False
    prompt_ctx = _prompt_ctx()
    prompt_ctx.conversation_text = "我喜欢吃什么"
    prompt_ctx.retrieve_mode = "hybrid"
    await plugin.on_pre_prompt(prompt_ctx)
    call = fake.calls[0]
    assert call["mode"] == "hybrid"
    assert call["top_k"] == 5
    assert call["type_caps"] == {"doc_chunk": 3}
    assert call["budget"] is plugin._budget
    assert call["plan_meta"] is not None
    assert call["plan_meta"]["identity"] is True
    assert call["plan_meta"]["enabled"] is False
    assert call["plan_meta"]["profile_id"] == "ordinary_identity"


@pytest.mark.asyncio
async def test_query_aware_plan_exception_fail_open_pre_v1() -> None:
    fake = _FakeContextService()
    plugin = _enabled_context_plugin()
    plugin._service = fake
    plugin._max_hits = 5
    plugin._max_doc_hits = 3
    plugin._query_aware_plan_enabled = True

    def _boom(**_kwargs: Any) -> Any:
        raise RuntimeError("planner boom")

    import plugins.context.plugin as plugin_mod

    # Patch the import site used inside on_pre_prompt.
    import services.context.query_plan as qp_mod

    original = qp_mod.plan_query_aware_retrieval
    qp_mod.plan_query_aware_retrieval = _boom  # type: ignore[assignment]
    try:
        prompt_ctx = _prompt_ctx()
        prompt_ctx.conversation_text = "我喜欢吃什么"
        prompt_ctx.retrieve_mode = "hybrid"
        await plugin.on_pre_prompt(prompt_ctx)
    finally:
        qp_mod.plan_query_aware_retrieval = original  # type: ignore[assignment]
        del plugin_mod

    call = fake.calls[0]
    assert call["mode"] == "hybrid"
    assert call["top_k"] == 5
    assert call["type_caps"] == {"doc_chunk": 3}
    assert call["budget"] is plugin._budget
    assert call["plan_meta"] is None


@pytest.mark.asyncio
async def test_query_aware_plan_ordinary_identity_matches_pre_v1_caps() -> None:
    fake = _FakeContextService()
    plugin = _enabled_context_plugin()
    plugin._service = fake
    plugin._max_hits = 5
    plugin._max_doc_hits = 3
    plugin._query_aware_plan_enabled = True
    prompt_ctx = _prompt_ctx()
    prompt_ctx.conversation_text = "雾青控制台是什么"
    prompt_ctx.retrieve_mode = "hybrid"
    await plugin.on_pre_prompt(prompt_ctx)
    call = fake.calls[0]
    assert call["mode"] == "hybrid"
    assert call["top_k"] == 5
    assert call["type_caps"] == {"doc_chunk": 3}
    assert call["budget"] is plugin._budget
    assert call["plan_meta"] is not None
    assert call["plan_meta"]["identity"] is True
    assert call["plan_meta"]["profile_id"] == "ordinary_identity"


@pytest.mark.asyncio
async def test_use_token_budget_false_still_applies_type_caps_not_budget() -> None:
    """When use_token_budget=False, preference type_caps apply; plugin uses max_chars only."""
    fake = _FakeContextService()
    plugin = _enabled_context_plugin()
    plugin._service = fake
    plugin._max_hits = 5
    plugin._max_doc_hits = 3
    plugin._max_chars = 1200
    plugin._use_token_budget = False
    plugin._query_aware_plan_enabled = True
    prompt_ctx = _prompt_ctx()
    prompt_ctx.conversation_text = "我喜欢吃什么"
    prompt_ctx.retrieve_mode = "hybrid"
    await plugin.on_pre_prompt(prompt_ctx)
    call = fake.calls[0]
    assert call["type_caps"].get("doc_chunk", 0) <= 1
    assert "preference" in (call["plan_meta"] or {}).get("needs", [])
    assert call["budget"] is None
    assert call["max_chars"] == 1200


@pytest.mark.asyncio
async def test_rewritten_query_does_not_authorize_temporal_trace() -> None:
    """current_message may drive planner; rewritten_query alone must not authorize TemporalTrace.

    Fake assembler mirrors real TemporalTraceAssembler: only current_message
    markers authorize; rewritten_query is matching help only.
    """

    class _AuthAwareFakeAssembler:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def assemble(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            msg = kwargs.get("current_message") or ""
            # Real authorization is current_message-only (historical/premise markers).
            if any(m in msg for m in ("以前", "之前", "曾经", "不是还", "还住", "还记得")):
                return _FakeTemporalTrace("authorized-from-current-message")
            return None

    assembler = _AuthAwareFakeAssembler()
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.rewritten_query = "我以前住在哪里"
    prompt_ctx.current_message = "今天天气怎么样"
    prompt_ctx.conversation_text = "今天天气怎么样"
    await plugin.on_pre_prompt(prompt_ctx)
    assert assembler.calls
    assert assembler.calls[0].get("current_message") == "今天天气怎么样"
    assert assembler.calls[0].get("rewritten_query") == "我以前住在哪里"
    assert not any(b.label == "记忆时间轨迹" for b in prompt_ctx.blocks)


@pytest.mark.asyncio
async def test_r13_hybrid_mode_injects_temporal_trace_block() -> None:
    """R9/R13: hybrid + takeover + temporal enabled → 记忆时间轨迹 block."""
    assembler = _FakeTraceAssembler(
        _FakeTemporalTrace("当前: 用户住在上海\n更早: 用户住在杭州\n以当前为准")
    )
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.current_message = "我以前住在哪里"
    await plugin.on_pre_prompt(prompt_ctx)

    labels = _trace_labels(prompt_ctx)
    assert "上下文资料" in labels
    assert labels.count("记忆时间轨迹") == 1
    blocks = _trace_blocks(prompt_ctx)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.source == "context_temporal_trace"
    assert block.priority == 45
    assert block.position == "dynamic"
    assert "上海" in block.text
    assert "杭州" in block.text
    # Ordinary pack remains separate and must not embed superseded-only story.
    pack_text = _block_text(prompt_ctx, "上下文资料")
    assert "统一记忆资料" in pack_text
    # Trace is NOT folded into ordinary pack label.
    assert pack_text != block.text


@pytest.mark.asyncio
async def test_r13_fact_mode_also_injects_temporal_trace() -> None:
    assembler = _FakeTraceAssembler(
        _FakeTemporalTrace("当前: 用户住在上海\n更早: 用户住在杭州\n以当前为准")
    )
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "fact"
    prompt_ctx.current_message = "我不是还住杭州吗"
    await plugin.on_pre_prompt(prompt_ctx)
    assert len(_trace_blocks(prompt_ctx)) == 1


@pytest.mark.asyncio
async def test_r13_skip_mode_no_temporal_trace_block() -> None:
    assembler = _FakeTraceAssembler(
        _FakeTemporalTrace("should-not-appear")
    )
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "skip"
    prompt_ctx.current_message = "我以前住在哪里"
    await plugin.on_pre_prompt(prompt_ctx)
    assert _trace_blocks(prompt_ctx) == []
    assert "记忆时间轨迹" not in _trace_labels(prompt_ctx)


@pytest.mark.asyncio
async def test_r13_doc_mode_no_temporal_trace_block() -> None:
    assembler = _FakeTraceAssembler(_FakeTemporalTrace("should-not-appear"))
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "doc"
    prompt_ctx.current_message = "我以前住在哪里"
    await plugin.on_pre_prompt(prompt_ctx)
    assert _trace_blocks(prompt_ctx) == []


@pytest.mark.asyncio
async def test_r13_takeover_false_no_temporal_trace_block() -> None:
    assembler = _FakeTraceAssembler(_FakeTemporalTrace("should-not-appear"))
    plugin = _plugin_with_trace(takeover=False, assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.current_message = "我以前住在哪里"
    await plugin.on_pre_prompt(prompt_ctx)
    assert _trace_blocks(prompt_ctx) == []


@pytest.mark.asyncio
async def test_r13_temporal_trace_disabled_no_block() -> None:
    assembler = _FakeTraceAssembler(_FakeTemporalTrace("should-not-appear"))
    plugin = _plugin_with_trace(temporal_enabled=False, assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.current_message = "我以前住在哪里"
    await plugin.on_pre_prompt(prompt_ctx)
    assert _trace_blocks(prompt_ctx) == []
    assert assembler.calls == []  # must not even invoke assembler when disabled


@pytest.mark.asyncio
async def test_r13_empty_assembler_result_no_block() -> None:
    assembler = _FakeTraceAssembler(None)
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.current_message = "我现在住在哪里"
    await plugin.on_pre_prompt(prompt_ctx)
    # Ordinary pack may still inject.
    assert "上下文资料" in _trace_labels(prompt_ctx)
    assert _trace_blocks(prompt_ctx) == []


@pytest.mark.asyncio
async def test_r12_assembler_exception_yields_no_partial_trace_block() -> None:
    assembler = _FakeTraceAssembler(exc=RuntimeError("boom"))
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.current_message = "我以前住在哪里"
    # Ordinary Exception → no partial 记忆时间轨迹; plugin should not crash.
    await plugin.on_pre_prompt(prompt_ctx)
    assert _trace_blocks(prompt_ctx) == []
    # Ordinary pack still present.
    assert "上下文资料" in _trace_labels(prompt_ctx)


@pytest.mark.asyncio
async def test_r12_cancelled_error_propagates_no_partial_trace_block() -> None:
    assembler = _FakeTraceAssembler(exc=asyncio.CancelledError())
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.current_message = "我以前住在哪里"
    with pytest.raises(asyncio.CancelledError):
        await plugin.on_pre_prompt(prompt_ctx)
    assert _trace_blocks(prompt_ctx) == []


@pytest.mark.asyncio
async def test_r4_r5_plugin_passes_current_message_to_assembler() -> None:
    """Authorization uses current_message; rewrite is matching help only."""
    assembler = _FakeTraceAssembler(
        _FakeTemporalTrace("当前: 上海\n更早: 杭州\n以当前为准")
    )
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.current_message = "我以前住在哪里"
    prompt_ctx.rewritten_query = "用户居住地"
    prompt_ctx.conversation_text = "buffer 内容 以前住哪 现在呢"
    await plugin.on_pre_prompt(prompt_ctx)
    assert assembler.calls, "assembler must be invoked"
    call = assembler.calls[0]
    assert call.get("current_message") == "我以前住在哪里"
    # rewritten_query may be forwarded for matching help but must not replace auth.
    if "rewritten_query" in call:
        assert call["rewritten_query"] == "用户居住地"


@pytest.mark.asyncio
async def test_r1_ordinary_pack_block_unchanged_when_trace_present() -> None:
    assembler = _FakeTraceAssembler(
        _FakeTemporalTrace("当前: 用户住在上海\n更早: 用户住在杭州\n以当前为准")
    )
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.current_message = "我以前住在哪里"
    await plugin.on_pre_prompt(prompt_ctx)

    pack_blocks = [b for b in prompt_ctx.blocks if b.label == "上下文资料"]
    assert len(pack_blocks) == 1
    assert pack_blocks[0].source == "context"
    assert pack_blocks[0].priority == 50
    assert "统一记忆资料" in pack_blocks[0].text
    # Superseded-only content must not appear inside ordinary pack from fake service.
    assert "用户住在杭州" not in pack_blocks[0].text

    trace_blocks = _trace_blocks(prompt_ctx)
    assert len(trace_blocks) == 1
    assert trace_blocks[0].priority == 45
    assert trace_blocks[0].source == "context_temporal_trace"


@pytest.mark.asyncio
async def test_r12_zero_schema_migration_no_graph_facts_in_trace_block() -> None:
    assembler = _FakeTraceAssembler(
        _FakeTemporalTrace("当前: 用户住在上海\n更早: 用户住在杭州\n以当前为准")
    )
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.current_message = "我以前住在哪里"
    await plugin.on_pre_prompt(prompt_ctx)
    for block in _trace_blocks(prompt_ctx):
        assert "graph_fact" not in block.text
        assert block.source == "context_temporal_trace"


@pytest.mark.asyncio
async def test_punctuation_only_ordinary_query_still_runs_temporal_trace() -> None:
    """Empty/punctuation ordinary retrieval skips pack, but fact/hybrid still runs trace."""
    assembler = _FakeTraceAssembler(
        _FakeTemporalTrace("当前: 用户住在上海\n更早: 用户住在杭州\n以当前为准")
    )
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "hybrid"
    prompt_ctx.conversation_text = "..."
    prompt_ctx.rewritten_query = "..."
    prompt_ctx.current_message = "我以前住在哪里"
    await plugin.on_pre_prompt(prompt_ctx)
    # Ordinary pack is strictly skipped for punctuation-only query.
    assert "上下文资料" not in _trace_labels(prompt_ctx)
    assert _trace_labels(prompt_ctx).count("记忆时间轨迹") == 1
    assert len(_trace_blocks(prompt_ctx)) == 1
    assert assembler.calls
    assert assembler.calls[0].get("current_message") == "我以前住在哪里"


@pytest.mark.asyncio
async def test_empty_ordinary_query_still_runs_temporal_trace_on_current_message() -> None:
    assembler = _FakeTraceAssembler(
        _FakeTemporalTrace("当前: 用户住在上海\n更早: 用户住在杭州\n以当前为准")
    )
    plugin = _plugin_with_trace(assembler=assembler)
    prompt_ctx = _prompt_ctx()
    prompt_ctx.retrieve_mode = "fact"
    prompt_ctx.conversation_text = ""
    prompt_ctx.rewritten_query = ""
    prompt_ctx.current_message = "我不是还住杭州吗"
    await plugin.on_pre_prompt(prompt_ctx)
    assert len(_trace_blocks(prompt_ctx)) == 1
    # Ordinary pack skip remains when there is no semantic query.
    pack = [b for b in prompt_ctx.blocks if b.label == "上下文资料"]
    assert pack == []


def test_context_schema_temporal_max_chars_hard_max_600() -> None:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "context"
        / "config.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    tt = schema["properties"]["temporal_trace"]["properties"]
    assert tt["max_chars"]["minimum"] == 100
    assert tt["max_chars"]["maximum"] == 600


@pytest.mark.asyncio
async def test_context_plugin_on_startup_builds_assembler_from_real_card_store(
    tmp_path,
) -> None:
    """True on_startup wiring: do not assign _temporal_trace_assembler manually."""
    from types import SimpleNamespace

    from kernel.config import GroupMemoryConfig, MemoryModeConfig, PoolConfig
    from services.context.types import ContextHit, ContextPack
    from services.memory.retrieval import RetrievalGate

    db_path = str(tmp_path / "startup_trace.db")
    store = CardStore(db_path=db_path)
    await store.init()
    try:
        g_old = await store.add_card(
            NewCard(
                category="fact",
                scope="group",
                scope_id="pool_startup",
                content="群约定聚餐在杭州",
            ),
            source_msg_id="m_su_old",
            captured_by="memo_extractor",
        )
        g_new = await store.supersede_card(
            g_old,
            NewCard(
                category="fact",
                scope="group",
                scope_id="pool_startup",
                content="群约定聚餐在上海",
            ),
            source_msg_id="m_su_new",
            evidence_text="改了",
            captured_by="memo_extractor",
        )
        gmc = GroupMemoryConfig(
            memory=MemoryModeConfig(
                mode="pool",
                pools={
                    "pool_startup": PoolConfig(name="startup", groups=["984198159"]),
                },
            ),
        )
        ctx = SimpleNamespace(
            context_service=object(),
            bus=None,
            card_store=store,
            retrieval=RetrievalGate(card_store=store),
            knowledge_base=None,
            knowledge_graph=None,
            group_memory_config=gmc,
            memo=None,
        )
        plugin = ContextPlugin()
        await plugin.on_startup(ctx)  # type: ignore[arg-type]
        assert plugin._temporal_trace_assembler is not None
        assert plugin._temporal_trace_enabled is True
        assert ctx.retrieval._card_eligibility is plugin._card_eligibility_policy
        assert plugin._temporal_trace_assembler._card_eligibility is plugin._card_eligibility_policy

        # Keep ordinary pack active-only (no superseded story).
        class _Svc:
            async def build_prompt_context(self, query: str, **kwargs: Any) -> ContextPack:
                del query, kwargs
                return ContextPack(
                    text="【记忆卡片】\n- [群] 统一记忆资料",
                    hits=[
                        ContextHit(
                            id=g_new,
                            type="memory_card",
                            content="群约定聚餐在上海",
                            score=0.9,
                            source="test",
                        ),
                    ],
                )

        plugin._service = _Svc()
        prompt_ctx = PromptContext(
            session_id="group_984198159",
            group_id="984198159",
            user_id="123",
            identity=Identity(id="bot", name="Bot", personality="test"),
            conversation_text="以前群聚餐定在哪里",
            current_message="以前群聚餐定在哪里",
            retrieve_mode="hybrid",
        )
        await plugin.on_pre_prompt(prompt_ctx)
        labels = [b.label for b in prompt_ctx.blocks]
        assert "上下文资料" in labels
        pack = [b for b in prompt_ctx.blocks if b.label == "上下文资料"]
        assert pack and "杭州" not in pack[0].text
        assert "上海" in pack[0].text or "统一记忆资料" in pack[0].text
        trace = [b for b in prompt_ctx.blocks if b.label == "记忆时间轨迹"]
        assert len(trace) == 1
        assert "杭州" in trace[0].text
        assert "上海" in trace[0].text
        assert "说明:" in trace[0].text
        assert trace[0].text.lstrip().startswith("当前:")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_context_plugin_prefers_trace_seed_ids_over_packed_hits() -> None:
    """When ordinary pack omits a pre-gate memory, TemporalTrace still uses trace_seed_ids."""
    from types import SimpleNamespace

    seed_id = "card_pre_gate_seed"
    assembled: list[list[str]] = []

    class _Assembler:
        async def assemble(self, **kwargs: Any) -> SimpleNamespace:
            hits = list(kwargs.get("active_memory_hits") or [])
            assembled.append(hits)
            return SimpleNamespace(text="当前: seed trace body\n说明: ok")

    class _Svc:
        async def build_prompt_context(self, query: str, **kwargs: Any) -> ContextPack:
            del query, kwargs
            # Packed hits intentionally exclude the seed (gate omit/demote simulation).
            return ContextPack(
                text="",
                hits=[],
                omitted_count=1,
                trace_seed_ids=(seed_id,),
            )

    plugin = _plugin_with_trace(assembler=_Assembler())
    plugin._service = _Svc()
    plugin._enabled = True
    plugin._takeover = True
    plugin._temporal_trace_enabled = True
    prompt_ctx = PromptContext(
        session_id="user_1",
        group_id=None,
        user_id="1",
        identity=Identity(id="bot", name="Bot", personality="test"),
        conversation_text="以前你说什么来着",
        current_message="以前你说什么来着",
        retrieve_mode="hybrid",
    )
    await plugin.on_pre_prompt(prompt_ctx)
    # Empty ordinary pack → no main context block
    assert not any(b.label == "上下文资料" for b in prompt_ctx.blocks)
    assert assembled and assembled[0] == [seed_id]
    assert any(b.label == "记忆时间轨迹" for b in prompt_ctx.blocks)


@pytest.mark.asyncio
async def test_context_plugin_trace_seed_fallback_for_legacy_fake_pack() -> None:
    """Old/fake packs without trace_seed_ids fall back to packed memory ids."""
    from types import SimpleNamespace

    assembled: list[list[str]] = []

    class _Assembler:
        async def assemble(self, **kwargs: Any) -> SimpleNamespace:
            assembled.append(list(kwargs.get("active_memory_hits") or []))
            return SimpleNamespace(text="当前: packed\n说明: ok")

    class _LegacyPack:
        def __init__(self) -> None:
            self.text = "【记忆卡片】\n- [用户] x"
            self.hits = [
                ContextHit(
                    id="card_packed_only",
                    type="memory_card",
                    content="x",
                    score=0.9,
                    source="test",
                )
            ]
            self.omitted_count = 0
            # no trace_seed_ids attribute

    class _Svc:
        async def build_prompt_context(self, query: str, **kwargs: Any) -> Any:
            del query, kwargs
            return _LegacyPack()

    plugin = _plugin_with_trace(assembler=_Assembler())
    plugin._service = _Svc()
    plugin._enabled = True
    plugin._takeover = True
    plugin._temporal_trace_enabled = True
    prompt_ctx = PromptContext(
        session_id="user_1",
        group_id=None,
        user_id="1",
        identity=Identity(id="bot", name="Bot", personality="test"),
        conversation_text="以前",
        current_message="以前",
        retrieve_mode="hybrid",
    )
    await plugin.on_pre_prompt(prompt_ctx)
    assert assembled and assembled[0] == ["card_packed_only"]
