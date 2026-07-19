from __future__ import annotations

import json
from copy import deepcopy
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from kernel.config import (
    BlockTraceConfig,
    BotConfig,
    KnowledgeGraphConfig,
    load_plugin_config,
)
from kernel.types import Identity, PromptContext
from plugins.context.plugin import ContextConfig, ContextPlugin
from plugins.memo.plugin import MemoConfig, MemoExtractor
from services.block_trace import BlockTraceStore, disabled_snapshot
from services.context.evidence_use_contract import derive_evidence_use_contract
from services.context.pack_evidence_gate import (
    PackEvidenceGatePolicy,
    apply_pack_evidence_gate,
)
from services.context.packing import ContextBudget
from services.context.query_plan import plan_query_aware_retrieval
from services.context.types import ContextHit
from services.knowledge_graph.service import KnowledgeGraphService
from services.memory.card_eligibility import (
    CardEligibilityPolicy,
    filter_cards_for_recall,
)
from services.plugin_config import PluginConfigStore

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "docs/runbooks/memory-system-staged-rollout-v1.json"
RUNBOOK_PATH = ROOT / "docs/runbooks/memory-system-staged-rollout-v1.md"

FLAG_PATHS = (
    "memo.write_policy_enabled",
    "context.temporal_trace.enabled",
    "context.query_aware_plan.enabled",
    "context.card_eligibility.enabled",
    "context.pack_evidence_gate.enabled",
    "context.evidence_use_contract.enabled",
    "context.evidence_use_contract.inject_constrained_instruction",
    "knowledge_graph.provenance_gate_enabled",
    "knowledge_graph.observability_enabled",
    "block_trace.joint_dual_path_telemetry_enabled",
)


def _profile() -> dict[str, object]:
    assert PROFILE_PATH.is_file(), "machine-readable rollout profile is required"
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def _phase_flags(profile: dict[str, object]) -> list[dict[str, bool]]:
    raw_phases = profile["phases"]
    assert isinstance(raw_phases, list)
    phases: list[dict[str, bool]] = []
    for phase in raw_phases:
        assert isinstance(phase, dict)
        raw_flags = phase["flags"]
        assert isinstance(raw_flags, dict)
        phases.append({str(key): value for key, value in raw_flags.items() if isinstance(value, bool)})
    return phases


def _materialize_namespace(
    flags: dict[str, bool],
    namespace: str,
) -> dict[str, object]:
    """Convert one dotted operator namespace into its real nested payload."""

    prefix = f"{namespace}."
    payload: dict[str, object] = {}
    matched = 0
    for dotted_path, value in flags.items():
        if not dotted_path.startswith(prefix):
            continue
        parts = dotted_path[len(prefix) :].split(".")
        assert all(parts), f"invalid dotted flag path: {dotted_path!r}"
        current = payload
        for part in parts[:-1]:
            nested = current.setdefault(part, {})
            assert isinstance(nested, dict), (
                f"flag path collision while materializing {dotted_path!r}"
            )
            current = nested
        leaf = parts[-1]
        assert leaf not in current, f"duplicate flag path: {dotted_path!r}"
        current[leaf] = value
        matched += 1
    assert matched > 0, f"rollout profile has no {namespace!r} flags"
    return payload


def _merge_nested(
    base: dict[str, object],
    override: dict[str, object],
) -> dict[str, object]:
    merged = deepcopy(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_nested(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def test_rollout_profile_is_closed_cumulative_and_keeps_provenance_gate_safe() -> None:
    profile = _profile()

    assert profile["schema_version"] == 1
    assert profile["profile"] == "memory_staged_rollout_v1"
    phases = _phase_flags(profile)
    assert phases
    assert all(tuple(flags) == FLAG_PATHS for flags in phases)

    stage0 = phases[0]
    assert stage0["knowledge_graph.provenance_gate_enabled"] is True
    assert all(
        value is False
        for key, value in stage0.items()
        if key != "knowledge_graph.provenance_gate_enabled"
    )

    for previous, current in pairwise(phases):
        assert all(not previous[key] or current[key] for key in FLAG_PATHS)
        assert current["knowledge_graph.provenance_gate_enabled"] is True


def test_stage0_profile_parses_and_flappable_read_path_is_identity() -> None:
    stage0 = _phase_flags(_profile())[0]
    context = ContextConfig.model_validate(
        {
            "temporal_trace": {
                "enabled": stage0["context.temporal_trace.enabled"],
            },
            "query_aware_plan": {
                "enabled": stage0["context.query_aware_plan.enabled"],
            },
            "card_eligibility": {
                "enabled": stage0["context.card_eligibility.enabled"],
            },
            "pack_evidence_gate": {
                "enabled": stage0["context.pack_evidence_gate.enabled"],
            },
            "evidence_use_contract": {
                "enabled": stage0["context.evidence_use_contract.enabled"],
                "inject_constrained_instruction": stage0[
                    "context.evidence_use_contract.inject_constrained_instruction"
                ],
            },
        }
    )
    memo = MemoConfig(
        write_policy_enabled=stage0["memo.write_policy_enabled"],
    )
    graph = KnowledgeGraphConfig(
        provenance_gate_enabled=stage0[
            "knowledge_graph.provenance_gate_enabled"
        ],
        observability_enabled=stage0["knowledge_graph.observability_enabled"],
    )
    trace = BlockTraceConfig(
        joint_dual_path_telemetry_enabled=stage0[
            "block_trace.joint_dual_path_telemetry_enabled"
        ]
    )

    assert context.temporal_trace.enabled is False
    assert context.query_aware_plan.enabled is False
    assert context.card_eligibility.enabled is False
    assert context.pack_evidence_gate.enabled is False
    assert context.evidence_use_contract.enabled is False
    assert context.evidence_use_contract.inject_constrained_instruction is False
    assert memo.write_policy_enabled is False
    assert graph.provenance_gate_enabled is True
    assert graph.observability_enabled is False
    assert trace.joint_dual_path_telemetry_enabled is False

    base_budget = ContextBudget(
        total_tokens=6000,
        memory_tokens=1500,
        doc_tokens=2500,
        graph_tokens=1700,
        buffer_tokens=300,
    )
    plan = plan_query_aware_retrieval(
        query="What are all my earlier preferences?",
        current_message="现在都列出来",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=base_budget,
        enabled=context.query_aware_plan.enabled,
    )
    assert plan.identity is True
    assert plan.budget is base_budget
    assert plan.top_k == 5
    assert dict(plan.type_caps) == {"doc_chunk": 3}

    cards = [
        SimpleNamespace(status="active", category="status", updated_at="bad"),
        SimpleNamespace(status="disabled", category="event", updated_at=""),
    ]
    eligible = filter_cards_for_recall(
        cards,
        policy=CardEligibilityPolicy(enabled=context.card_eligibility.enabled),
    )
    assert eligible == cards
    assert all(left is right for left, right in zip(eligible, cards, strict=True))

    hits = [
        ContextHit(
            id="m1",
            type="memory_card",
            content="low confidence without evidence",
            score=0.1,
            source="memo",
        ),
        ContextHit(
            id="g1",
            type="graph_fact",
            content="A relates to B",
            score=0.1,
            source="knowledge_graph",
        ),
    ]
    gated = apply_pack_evidence_gate(
        hits,
        policy=PackEvidenceGatePolicy(enabled=context.pack_evidence_gate.enabled),
    )
    assert gated.omitted_count == 0
    assert list(gated.hits) == hits
    assert all(left is right for left, right in zip(gated.hits, hits, strict=True))
    assert gated.metrics["identity"] is True

    contract = derive_evidence_use_contract(
        pack_hits=list(gated.hits),
        peg_metrics=gated.metrics,
        enabled=context.evidence_use_contract.enabled,
        inject_constrained_instruction=(
            context.evidence_use_contract.inject_constrained_instruction
        ),
    )
    assert contract.identity is True
    assert contract.inject_instruction is False
    assert contract.action == "identity"


def test_stage0_operator_profile_uses_real_config_stores_and_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage0 = _phase_flags(_profile())[0]
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    for plugin_name in ("context", "memo"):
        (plugin_root / plugin_name).symlink_to(
            ROOT / "plugins" / plugin_name,
            target_is_directory=True,
        )

    config_dir = tmp_path / "storage" / "plugins" / "config"
    store = PluginConfigStore(path=config_dir, plugin_root=plugin_root)
    stage_plugin_values = {
        plugin_name: _materialize_namespace(stage0, plugin_name)
        for plugin_name in ("context", "memo")
    }
    assert set(stage_plugin_values["memo"]) == {"write_policy_enabled"}
    assert set(stage_plugin_values["context"]) == {
        "temporal_trace",
        "query_aware_plan",
        "card_eligibility",
        "pack_evidence_gate",
        "evidence_use_contract",
    }

    existing_plugin_values: dict[str, dict[str, object]] = {
        "context": {"max_hits": 7},
        "memo": {"user_max_chars": 350},
    }
    for plugin_name, existing in existing_plugin_values.items():
        store.set_values(plugin_name, existing)
        values = _merge_nested(existing, stage_plugin_values[plugin_name])
        store.set_values(plugin_name, values)
        override_path = store.plugin_path(plugin_name)
        assert override_path.relative_to(tmp_path) == Path(
            f"storage/plugins/config/{plugin_name}.json"
        )
        payload = json.loads(override_path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["plugin"] == plugin_name
        assert payload["values"] == values

    context_entry = store.get_entry("context")
    memo_entry = store.get_entry("memo")
    context_from_store = ContextConfig.model_validate(
        context_entry["effective_values"]
    )
    memo_from_store = MemoConfig.model_validate(memo_entry["effective_values"])
    assert context_from_store.temporal_trace.enabled is False
    assert context_from_store.query_aware_plan.enabled is False
    assert context_from_store.card_eligibility.enabled is False
    assert context_from_store.pack_evidence_gate.enabled is False
    assert context_from_store.evidence_use_contract.enabled is False
    assert context_from_store.evidence_use_contract.inject_constrained_instruction is False
    assert context_from_store.max_hits == 7
    assert memo_from_store.write_policy_enabled is False
    assert memo_from_store.user_max_chars == 350

    monkeypatch.chdir(tmp_path)
    context_runtime = load_plugin_config(
        "plugins/context/config.default.json",
        ContextConfig,
    )
    memo_runtime = load_plugin_config(
        "plugins/memo/config.default.json",
        MemoConfig,
    )
    assert context_runtime == context_from_store
    assert memo_runtime == memo_from_store

    main_values = {
        namespace: _materialize_namespace(stage0, namespace)
        for namespace in ("knowledge_graph", "block_trace")
    }
    bot_config = BotConfig.model_validate(main_values)
    assert bot_config.knowledge_graph.provenance_gate_enabled is True
    assert bot_config.knowledge_graph.observability_enabled is False
    assert bot_config.block_trace.joint_dual_path_telemetry_enabled is False


@pytest.mark.asyncio
async def test_stage0_profile_drives_real_runtime_disabled_paths(
    tmp_path: Path,
) -> None:
    stage0 = _phase_flags(_profile())[0]

    class _RecordingAssembler:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def assemble(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return SimpleNamespace(text="must not be injected")

    assembler = _RecordingAssembler()
    context_plugin = ContextPlugin()
    context_plugin._enabled = True
    context_plugin._takeover = True
    context_plugin._temporal_trace_enabled = stage0[
        "context.temporal_trace.enabled"
    ]
    context_plugin._temporal_trace_assembler = assembler
    prompt_ctx = PromptContext(
        session_id="group_stage0",
        group_id="stage0",
        user_id="operator",
        identity=Identity(),
        current_message="我以前住在哪里",
        retrieve_mode="hybrid",
    )
    await context_plugin._maybe_inject_temporal_trace(
        prompt_ctx,
        pack=SimpleNamespace(trace_seed_ids=("card_1",), hits=[]),
        retrieve_mode="hybrid",
        rewritten="我以前住在哪里",
    )
    assert assembler.calls == []
    assert prompt_ctx.blocks == []

    class _RecordingMemoStore:
        def __init__(self) -> None:
            self.added: list[tuple[Any, dict[str, Any]]] = []

        async def add_card(self, card: Any, **kwargs: Any) -> Any:
            self.added.append((card, kwargs))
            return card

    class _RecordingMemoLLM:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def __call__(self, request: Any) -> dict[str, str]:
            self.calls.append(request)
            return {"text": "[preference] 用户喜欢茉莉花茶"}

    memo_store = _RecordingMemoStore()
    memo_llm = _RecordingMemoLLM()
    extractor = MemoExtractor(
        card_store=cast(Any, memo_store),
        api_call=memo_llm,
        config=MemoConfig(
            write_policy_enabled=stage0["memo.write_policy_enabled"]
        ),
    )
    await extractor.extract_after_turn(
        user_id="stage0_user",
        group_id="stage0_group",
        user_msg="我喜欢茉莉花茶",
        bot_reply="记住了",
        source_message_id="stage0_msg",
    )
    assert len(memo_llm.calls) == 1
    request = memo_llm.calls[0]
    request_text = "\n".join(str(x) for x in request.static_blocks)
    assert "add|reinforce|supersede|skip" not in request_text
    assert len(memo_store.added) == 1
    card, add_kwargs = memo_store.added[0]
    assert card.category == "preference"
    assert card.scope == "user"
    assert card.scope_id == "stage0_user"
    assert add_kwargs["source_msg_id"] == "stage0_msg"
    assert extractor.stats["add"] == 1
    assert extractor.stats["reinforce"] == 0
    assert extractor.stats["supersede"] == 0

    graph_service = KnowledgeGraphService(
        db_path=tmp_path / "stage0-graph.db",
        provenance_gate_enabled=stage0[
            "knowledge_graph.provenance_gate_enabled"
        ],
        observability_enabled=stage0[
            "knowledge_graph.observability_enabled"
        ],
    )
    await graph_service.init()
    try:
        await graph_service.submit_fact_candidate(
            subject="Stage0",
            predicate="keeps",
            object="ProvenanceGate",
            confidence=0.9,
            source="rollout_contract",
            evidence={"type": "fixture", "id": "stage0_active"},
            promote_directly=True,
        )
        await graph_service._store.add_candidate(
            subject="Stage0",
            predicate="observes",
            object="Pending",
            confidence=0.7,
            source="rollout_contract",
            evidence={"type": "message", "id": "stage0_pending"},
            status="pending",
        )
        graph_sql: list[str] = []
        graph_db = graph_service._store._require_db()
        await graph_db.set_trace_callback(graph_sql.append)
        try:
            graph_snapshot = await graph_service.health_snapshot()
        finally:
            await graph_db.set_trace_callback(lambda _statement: None)
        assert set(graph_snapshot) == {
            "available",
            "checked_at",
            "since",
            "candidate_24h",
            "candidate_total",
            "facts_active_by_source",
            "facts_active_24h",
            "edges_24h",
        }
        assert "observability" not in graph_snapshot
        quality_sql = [
            statement
            for statement in graph_sql
            if (
                "graph_evidence" in statement.lower()
                or "evidence_json" in statement.lower()
                or (
                    "select fact_id from graph_facts" in statement.lower()
                    and "order by fact_id" in statement.lower()
                )
            )
        ]
        assert quality_sql == []
    finally:
        await graph_service.close()

    trace_store = BlockTraceStore(
        db_path=tmp_path / "stage0-block-trace.db",
        joint_dual_path_telemetry_enabled=stage0[
            "block_trace.joint_dual_path_telemetry_enabled"
        ],
    )
    await trace_store.init()
    try:
        trace_sql: list[str] = []
        trace_db = trace_store._conn()
        await trace_db.set_trace_callback(trace_sql.append)
        try:
            trace_snapshot = await trace_store.joint_dual_path_snapshot(limit=10)
        finally:
            await trace_db.set_trace_callback(lambda _statement: None)
        assert trace_snapshot == disabled_snapshot()
        assert [
            statement
            for statement in trace_sql
            if (
                "prompt_block_traces" in statement.lower()
                or "latest_requests" in statement.lower()
            )
        ] == []
    finally:
        await trace_store.close()


def test_rollout_runbook_covers_operational_acceptance_and_hard_slices() -> None:
    assert RUNBOOK_PATH.is_file(), "memory staged rollout runbook is required"
    text = RUNBOOK_PATH.read_text(encoding="utf-8")

    for required in (
        "Preflight",
        "Stage 0",
        "Operator flag mapping",
        "storage/plugins/config/context.json",
        "storage/plugins/config/memo.json",
        '"schema_version": 1',
        '"plugin": "context"',
        '"plugin": "memo"',
        '"values"',
        "config/config.json",
        "config/config.toml",
        "PluginConfigStore",
        "BotConfig",
        "Canary metrics",
        "Stop conditions",
        "Rollback",
        "Structural slices without kill-switches",
        "98887a5",
        "PPR",
        "GraphRAG",
        "MemGPT",
        "NapCat",
    ):
        assert required in text
