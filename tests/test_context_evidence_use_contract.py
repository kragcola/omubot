"""Evidence-use contract v1 (euc_v1): pack-state observability + soft instruction.

Contract is observability and a soft model-facing constraint, not proof that
an answer used evidence. Metrics are secret-free and closed-set.
"""

from __future__ import annotations

import pytest

from services.context.types import (
    ContextHit,
    ContextPack,
    ContextProvenance,
    ContextScoreBreakdown,
)


def _mem(
    id: str,
    *,
    content: str = "记忆内容",
    source: str = "memo_extractor",
    confidence: float | None = 0.9,
    retriever: str = "card_store",
    msg_id: str = "msg1",
) -> ContextHit:
    return ContextHit(
        id=id,
        type="memory_card",
        content=content,
        score=0.5,
        source=source,
        status="active",
        retriever=retriever,
        provenance=ContextProvenance(
            owner="memory_cards",
            source_id=id,
            source_message_id=msg_id,
        ),
        score_breakdown=ContextScoreBreakdown(
            source_score=0.5, confidence=confidence
        ),
        metadata={"confidence": confidence} if confidence is not None else {},
    )


def _hint(id: str = "memory_hint:u1") -> ContextHit:
    return ContextHit(
        id=id,
        type="memory_card",
        content="(最小记忆提示)",
        score=0.01,
        source="card_store",
        status="active",
        retriever="card_store_hint",
        provenance=ContextProvenance(owner="memory_cards", source_id=id),
        score_breakdown=ContextScoreBreakdown(source_score=0.01, confidence=0.0),
        metadata={"confidence": 0.0},
    )


def _doc(id: str = "d1", content: str = "文档正文") -> ContextHit:
    return ContextHit(
        id=id,
        type="doc_chunk",
        content=content,
        score=0.4,
        source="knowledge",
        status="active",
        retriever="knowledge_bm25_ngram",
        score_breakdown=ContextScoreBreakdown(source_score=0.4),
    )


# ---------------------------------------------------------------------------
# Pure state derivation
# ---------------------------------------------------------------------------


def test_derive_skip_distinct_from_empty() -> None:
    from services.context.evidence_use_contract import derive_evidence_use_contract

    skip = derive_evidence_use_contract(
        pack_hits=[],
        omitted_count=0,
        peg_metrics=None,
        retrieve_mode="skip",
        enabled=True,
        inject_constrained_instruction=True,
    )
    empty = derive_evidence_use_contract(
        pack_hits=[],
        omitted_count=0,
        peg_metrics={"enabled": True, "actions": {"keep": 0, "demote": 0, "omit": 0}},
        retrieve_mode="hybrid",
        enabled=True,
        inject_constrained_instruction=True,
    )
    assert skip.pack_state == "skip"
    assert empty.pack_state == "empty"
    assert skip.pack_state != empty.pack_state
    assert skip.inject_instruction is False
    assert empty.inject_instruction is True
    assert empty.action == "constrained_instruction"
    assert skip.action == "none"


def test_derive_hint_only() -> None:
    from services.context.evidence_use_contract import derive_evidence_use_contract

    c = derive_evidence_use_contract(
        pack_hits=[_hint()],
        omitted_count=0,
        peg_metrics={"enabled": True, "actions": {"keep": 1, "demote": 0, "omit": 0}},
        retrieve_mode="hybrid",
        enabled=True,
        inject_constrained_instruction=True,
    )
    assert c.pack_state == "hint_only"
    assert c.inject_instruction is True
    assert c.action == "constrained_instruction"
    assert c.enabled is True
    assert c.identity is False


def test_derive_nonempty() -> None:
    from services.context.evidence_use_contract import derive_evidence_use_contract

    c = derive_evidence_use_contract(
        pack_hits=[_mem("m1"), _doc()],
        omitted_count=0,
        peg_metrics={"enabled": True, "actions": {"keep": 2, "demote": 0, "omit": 0}},
        retrieve_mode="fact",
        enabled=True,
        inject_constrained_instruction=True,
    )
    assert c.pack_state == "nonempty"
    assert c.inject_instruction is False
    assert c.action == "none"


def test_derive_demote_present_no_instruction_by_default() -> None:
    from services.context.evidence_use_contract import derive_evidence_use_contract

    weak = _mem(
        "m_weak",
        confidence=0.1,
        msg_id="",
        source="memo_extractor",
    )
    # Empty msg_id → no evidence; pack may still include demoted hit.
    weak = ContextHit(
        id="m_weak",
        type="memory_card",
        content="弱记忆",
        score=0.7,
        source="memo_extractor",
        status="active",
        retriever="card_store",
        provenance=ContextProvenance(owner="memory_cards"),
        score_breakdown=ContextScoreBreakdown(source_score=0.7, confidence=0.1),
        metadata={"confidence": 0.1},
    )
    c = derive_evidence_use_contract(
        pack_hits=[weak],
        omitted_count=0,
        peg_metrics={
            "enabled": True,
            "actions": {"keep": 0, "demote": 1, "omit": 0},
            "reasons": {"gate_demote_memory_weak_empty": 1},
        },
        retrieve_mode="hybrid",
        enabled=True,
        inject_constrained_instruction=True,
    )
    assert c.pack_state == "demote_present"
    assert c.inject_instruction is False
    assert c.action == "none"


def test_derive_omit_only_injects_like_empty() -> None:
    """omit_only: diagnostic state, but final pack has no usable evidence → soft inject."""
    from services.context.evidence_use_contract import derive_evidence_use_contract

    c = derive_evidence_use_contract(
        pack_hits=[],
        omitted_count=2,
        peg_metrics={
            "enabled": True,
            "actions": {"keep": 0, "demote": 0, "omit": 2},
            "reasons": {"gate_omit_blank_doc": 2},
        },
        retrieve_mode="hybrid",
        enabled=True,
        inject_constrained_instruction=True,
    )
    assert c.pack_state == "omit_only"
    assert c.inject_instruction is True
    assert c.action == "constrained_instruction"


def test_derive_demoted_then_budget_dropped_is_empty_not_demote_present() -> None:
    """Final post-pack state wins: demote signal but zero surviving hits → empty + inject."""
    from services.context.evidence_use_contract import derive_evidence_use_contract

    c = derive_evidence_use_contract(
        pack_hits=[],
        omitted_count=1,
        peg_metrics={
            "enabled": True,
            "actions": {"keep": 0, "demote": 1, "omit": 0},
            "reasons": {"gate_demote_memory_weak_empty": 1},
        },
        retrieve_mode="hybrid",
        enabled=True,
        inject_constrained_instruction=True,
    )
    assert c.pack_state == "empty"
    assert c.inject_instruction is True
    assert c.action == "constrained_instruction"


def test_derive_omit_only_skip_mode_no_inject() -> None:
    from services.context.evidence_use_contract import derive_evidence_use_contract

    c = derive_evidence_use_contract(
        pack_hits=[],
        omitted_count=1,
        peg_metrics={"actions": {"keep": 0, "demote": 0, "omit": 1}},
        retrieve_mode="skip",
        enabled=True,
        inject_constrained_instruction=True,
    )
    assert c.pack_state == "skip"
    assert c.inject_instruction is False
    assert c.action == "none"


def test_derive_disabled_is_identity_no_inject() -> None:
    from services.context.evidence_use_contract import derive_evidence_use_contract

    c = derive_evidence_use_contract(
        pack_hits=[],
        omitted_count=0,
        peg_metrics=None,
        retrieve_mode="hybrid",
        enabled=False,
        inject_constrained_instruction=True,
    )
    assert c.enabled is False
    assert c.identity is True
    assert c.inject_instruction is False
    assert c.action == "identity"
    assert c.pack_state == "empty"  # state still classified, behavior identity


def test_inject_flag_off_suppresses_instruction_even_for_empty() -> None:
    from services.context.evidence_use_contract import derive_evidence_use_contract

    c = derive_evidence_use_contract(
        pack_hits=[],
        omitted_count=0,
        peg_metrics=None,
        retrieve_mode="hybrid",
        enabled=True,
        inject_constrained_instruction=False,
    )
    assert c.pack_state == "empty"
    assert c.inject_instruction is False
    assert c.action == "none"


def test_malformed_peg_metrics_fail_closed() -> None:
    from services.context.evidence_use_contract import derive_evidence_use_contract

    c = derive_evidence_use_contract(
        pack_hits=[],
        omitted_count=99,
        peg_metrics={
            "actions": {"keep": "nope", "demote": -3, "omit": None, "hack": 9},
            "reasons": {"gate_omit_blank_doc": 1, "evil_reason": 5},
            "query": "SECRET_QUERY",
            "content": "leak",
        },
        retrieve_mode="hybrid",
        enabled=True,
        inject_constrained_instruction=True,
    )
    # Fail closed: untrusted actions → treat as no demote/omit signal → empty
    assert c.pack_state == "empty"
    metrics = c.to_metrics()
    assert "query" not in metrics
    assert "content" not in metrics
    assert "SECRET_QUERY" not in str(metrics)
    assert "leak" not in str(metrics)
    assert "evil_reason" not in str(metrics.get("reasons", {}))
    assert set(metrics["actions"].keys()) <= {"keep", "demote", "omit"}


# ---------------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------------


def test_sanitize_rejects_arbitrary_keys_and_payload_likes() -> None:
    from services.context.evidence_use_contract import sanitize_evidence_use_contract_metrics

    dirty = {
        "version": "euc_v1",
        "enabled": True,
        "identity": False,
        "pack_state": "nonempty",
        "action": "none",
        "inject_instruction": False,
        "query": "user secret question",
        "title": "card title",
        "id": "mem_42",
        "card_id": "c1",
        "message_id": "m9",
        "content": "full answer text",
        "answer_used_evidence": True,
        "hits": [{"id": "x"}],
        "actions": {"keep": 1, "demote": 0, "omit": 0, "extra": 3},
        "reasons": {"gate_keep_default": 1, "not_a_reason": 2},
        "pack_text": "body",
        "unknown_field": 123,
    }
    clean = sanitize_evidence_use_contract_metrics(dirty)
    allowed = {
        "version",
        "enabled",
        "identity",
        "pack_state",
        "action",
        "inject_instruction",
        "actions",
        "reasons",
    }
    assert set(clean.keys()) <= allowed
    assert "answer_used_evidence" not in clean
    assert clean["version"] == "euc_v1"
    assert clean["pack_state"] == "nonempty"
    assert "query" not in clean
    assert "title" not in clean
    assert "id" not in clean
    assert "content" not in clean
    assert clean["actions"] == {"keep": 1, "demote": 0, "omit": 0}
    assert clean["reasons"] == {"gate_keep_default": 1}
    # Reject unknown pack_state / action
    bad_state = sanitize_evidence_use_contract_metrics(
        {"pack_state": "grounded", "action": "force_pass_turn", "enabled": True}
    )
    assert bad_state["pack_state"] == "empty"
    assert bad_state["action"] == "none"
    assert bad_state["inject_instruction"] is False


def test_sanitize_none_and_non_mapping_fail_closed() -> None:
    from services.context.evidence_use_contract import sanitize_evidence_use_contract_metrics

    for raw in (None, "x", 1, [], {"enabled": "yes", "pack_state": None}):
        clean = sanitize_evidence_use_contract_metrics(raw)  # type: ignore[arg-type]
        assert clean["version"] == "euc_v1"
        assert clean["enabled"] is False or isinstance(clean["enabled"], bool)
        assert clean["pack_state"] in {
            "empty",
            "hint_only",
            "nonempty",
            "demote_present",
            "omit_only",
            "skip",
        }
        assert "query" not in clean


def test_sanitize_inconsistent_identity_action_fail_closed() -> None:
    """action=identity only when disabled or identity=true; inject only for empty/hint_only/omit_only."""
    from services.context.evidence_use_contract import sanitize_evidence_use_contract_metrics

    # Enabled + non-identity cannot keep action=identity
    clean = sanitize_evidence_use_contract_metrics(
        {
            "enabled": True,
            "identity": False,
            "pack_state": "empty",
            "action": "identity",
            "inject_instruction": True,
        }
    )
    assert clean["enabled"] is True
    assert clean["identity"] is False
    assert clean["action"] == "constrained_instruction"
    assert clean["inject_instruction"] is True

    # identity=true forces action=identity and no inject even if flags claim otherwise
    clean2 = sanitize_evidence_use_contract_metrics(
        {
            "enabled": True,
            "identity": True,
            "pack_state": "empty",
            "action": "constrained_instruction",
            "inject_instruction": True,
        }
    )
    assert clean2["identity"] is True
    assert clean2["action"] == "identity"
    assert clean2["inject_instruction"] is False

    # constrained_instruction without inject → none
    clean3 = sanitize_evidence_use_contract_metrics(
        {
            "enabled": True,
            "identity": False,
            "pack_state": "empty",
            "action": "constrained_instruction",
            "inject_instruction": False,
        }
    )
    assert clean3["action"] == "none"
    assert clean3["inject_instruction"] is False

    # demote_present must not inject / constrained_instruction
    clean4 = sanitize_evidence_use_contract_metrics(
        {
            "enabled": True,
            "identity": False,
            "pack_state": "demote_present",
            "action": "constrained_instruction",
            "inject_instruction": True,
        }
    )
    assert clean4["pack_state"] == "demote_present"
    assert clean4["inject_instruction"] is False
    assert clean4["action"] == "none"

    # omit_only may inject when consistent
    clean5 = sanitize_evidence_use_contract_metrics(
        {
            "enabled": True,
            "identity": False,
            "pack_state": "omit_only",
            "action": "constrained_instruction",
            "inject_instruction": True,
        }
    )
    assert clean5["pack_state"] == "omit_only"
    assert clean5["inject_instruction"] is True
    assert clean5["action"] == "constrained_instruction"

    # disabled forces identity even if inject claimed
    clean6 = sanitize_evidence_use_contract_metrics(
        {
            "enabled": False,
            "identity": False,
            "pack_state": "omit_only",
            "action": "constrained_instruction",
            "inject_instruction": True,
        }
    )
    assert clean6["enabled"] is False
    assert clean6["identity"] is True
    assert clean6["action"] == "identity"
    assert clean6["inject_instruction"] is False


# ---------------------------------------------------------------------------
# ContextPack wire compatibility
# ---------------------------------------------------------------------------


def test_context_pack_to_dict_omits_evidence_use_contract() -> None:
    from services.context.evidence_use_contract import derive_evidence_use_contract

    contract = derive_evidence_use_contract(
        pack_hits=[],
        omitted_count=0,
        peg_metrics=None,
        retrieve_mode="hybrid",
        enabled=True,
        inject_constrained_instruction=True,
    )
    pack = ContextPack(
        text="",
        hits=[],
        omitted_count=0,
        evidence_use_contract=contract,
    )
    d = pack.to_dict()
    assert d == {"text": "", "hits": [], "omitted_count": 0}
    assert "evidence_use_contract" not in d
    assert "trace_seed_ids" not in d


def test_legacy_context_pack_without_contract_field() -> None:
    """Old/fake packs with only text/hits still construct."""
    pack = ContextPack(text="t", hits=[])
    assert pack.evidence_use_contract is None
    assert pack.to_dict() == {"text": "t", "hits": [], "omitted_count": 0}


# ---------------------------------------------------------------------------
# ContextService integration
# ---------------------------------------------------------------------------


class _StaticSource:
    name = "static"

    def __init__(self, hits: list[ContextHit]) -> None:
        self._hits = hits

    async def search(self, *args, **kwargs):
        del args, kwargs
        return list(self._hits)


@pytest.mark.asyncio
async def test_service_records_contract_on_recent_and_metrics_aggregate() -> None:
    from services.context.pack_evidence_gate import PackEvidenceGatePolicy
    from services.context.service import ContextService

    service = ContextService(
        [_StaticSource([])],
        pack_evidence_gate=PackEvidenceGatePolicy(enabled=True),
    )
    pack = await service.build_prompt_context(
        "q_empty",
        user_id="u1",
        mode="hybrid",
    )
    assert pack.hits == []
    assert pack.evidence_use_contract is not None
    assert pack.evidence_use_contract.pack_state == "empty"

    recent = service.recent(limit=1)[0]
    assert "evidence_use_contract" in recent
    euc = recent["evidence_use_contract"]
    assert euc["version"] == "euc_v1"
    assert euc["pack_state"] == "empty"
    assert "q_empty" not in str(euc)
    assert "u1" not in str(euc)
    assert set(euc.keys()) <= {
        "version",
        "enabled",
        "identity",
        "pack_state",
        "action",
        "inject_instruction",
        "actions",
        "reasons",
    }

    # Second: skip mode
    await service.build_prompt_context("q_skip", user_id="u1", mode="skip")
    # Third: nonempty
    service2 = ContextService(
        [_StaticSource([_mem("m1"), _doc()])],
        pack_evidence_gate=PackEvidenceGatePolicy(enabled=True),
    )
    await service2.build_prompt_context("q_ok", user_id="u1", mode="hybrid")
    await service2.build_prompt_context("q_skip2", user_id="u1", mode="skip")

    # Merge recent for aggregate check on service (empty + skip)
    m = service.metrics()
    assert "evidence_use_contract" in m
    agg = m["evidence_use_contract"]
    assert "state_counts" in agg
    assert agg["state_counts"].get("empty", 0) >= 1
    assert agg["state_counts"].get("skip", 0) >= 1
    assert "action_counts" in agg
    # Old keys preserved
    for key in (
        "total_queries",
        "miss_count",
        "miss_rate",
        "hit_count",
        "duplicate_hits",
        "duplicate_rate",
        "avg_pack_chars",
        "max_pack_chars",
        "omitted_total",
        "hit_type_counts",
        "hit_source_counts",
        "recent",
    ):
        assert key in m


@pytest.mark.asyncio
async def test_service_contract_disabled_identity_no_inject_flag() -> None:
    from services.context.evidence_use_contract import EvidenceUseContractPolicy
    from services.context.pack_evidence_gate import PackEvidenceGatePolicy
    from services.context.service import ContextService

    service = ContextService(
        [_StaticSource([])],
        pack_evidence_gate=PackEvidenceGatePolicy(enabled=True),
        evidence_use_contract=EvidenceUseContractPolicy(
            enabled=False,
            inject_constrained_instruction=True,
        ),
    )
    pack = await service.build_prompt_context("q", user_id="1")
    assert pack.evidence_use_contract is not None
    assert pack.evidence_use_contract.identity is True
    assert pack.evidence_use_contract.inject_instruction is False
    recent = service.recent(limit=1)[0]
    assert recent["evidence_use_contract"]["identity"] is True
    assert recent["evidence_use_contract"]["enabled"] is False


@pytest.mark.asyncio
async def test_service_skip_pack_state_on_recent() -> None:
    from services.context.service import ContextService

    service = ContextService([_StaticSource([_mem("m1")])])
    pack = await service.build_prompt_context("hello", mode="skip")
    assert pack.hits == []
    assert pack.evidence_use_contract is not None
    assert pack.evidence_use_contract.pack_state == "skip"
    assert service.recent(limit=1)[0]["evidence_use_contract"]["pack_state"] == "skip"


@pytest.mark.asyncio
async def test_service_demoted_then_budget_empty_injects_for_plugin() -> None:
    """Gate demotes weak memory; tiny pack budget drops it → empty + inject.

    Proves ContextPlugin would receive inject_instruction=True after a real
    demote signal with zero final pack hits (Codex acceptance regression).
    """
    from services.context.pack_evidence_gate import PackEvidenceGatePolicy
    from services.context.packing import ContextBudget
    from services.context.service import ContextService

    weak = ContextHit(
        id="m_weak_budget",
        type="memory_card",
        content="X" * 400,
        score=0.95,
        source="memo_extractor",
        status="active",
        retriever="card_store",
        provenance=ContextProvenance(owner="memory_cards", source_id="m_weak_budget"),
        score_breakdown=ContextScoreBreakdown(source_score=0.95, confidence=0.1),
        metadata={"confidence": 0.1},
    )
    # total_tokens tiny + buffer so no hit can fit after framing/buffer.
    tiny = ContextBudget(
        total_tokens=1,
        memory_tokens=1,
        doc_tokens=0,
        graph_tokens=0,
        buffer_tokens=0,
    )
    service = ContextService(
        [_StaticSource([weak])],
        budget=tiny,
        pack_evidence_gate=PackEvidenceGatePolicy(enabled=True),
    )
    pack = await service.build_prompt_context("q_demote_drop", user_id="u1", mode="hybrid")
    assert pack.hits == []
    assert pack.evidence_use_contract is not None
    assert pack.evidence_use_contract.pack_state == "empty"
    assert pack.evidence_use_contract.inject_instruction is True
    assert pack.evidence_use_contract.action == "constrained_instruction"
    # Prior demote signal still visible in closed actions (from peg).
    assert pack.evidence_use_contract.actions.get("demote", 0) >= 1
    euc = service.recent(limit=1)[0]["evidence_use_contract"]
    assert euc["pack_state"] == "empty"
    assert euc["inject_instruction"] is True
    assert euc["action"] == "constrained_instruction"


@pytest.mark.asyncio
async def test_service_omit_only_injects_for_plugin() -> None:
    """All candidates hard-omitted → omit_only + inject_instruction for plugin."""
    from services.context.pack_evidence_gate import PackEvidenceGatePolicy
    from services.context.service import ContextService

    blank = _doc("d_blank", content="   \n  ")
    service = ContextService(
        [_StaticSource([blank])],
        pack_evidence_gate=PackEvidenceGatePolicy(enabled=True),
    )
    pack = await service.build_prompt_context("q_omit", user_id="u1", mode="hybrid")
    assert pack.hits == []
    assert pack.evidence_use_contract is not None
    assert pack.evidence_use_contract.pack_state == "omit_only"
    assert pack.evidence_use_contract.inject_instruction is True
    assert pack.evidence_use_contract.action == "constrained_instruction"
    assert pack.evidence_use_contract.actions.get("omit", 0) >= 1
    euc = service.recent(limit=1)[0]["evidence_use_contract"]
    assert euc["pack_state"] == "omit_only"
    assert euc["inject_instruction"] is True


# ---------------------------------------------------------------------------
# ContextPlugin instruction injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_injects_constrained_block_only_for_empty() -> None:
    from kernel.types import Identity, PromptContext
    from plugins.context.plugin import ContextPlugin

    plugin = ContextPlugin()
    plugin._enabled = True
    plugin._takeover = True
    plugin._use_token_budget = True
    plugin._max_hits = 5
    plugin._max_doc_hits = 3
    plugin._budget = None
    plugin._query_aware_plan_enabled = False
    plugin._evidence_use_contract_enabled = True
    plugin._evidence_use_inject = True
    plugin._temporal_trace_enabled = False
    plugin._temporal_trace_assembler = None
    plugin._graph_auto_extract = False
    plugin._graph = None

    class _Svc:
        async def build_prompt_context(self, query: str, **kwargs):
            del query, kwargs
            from services.context.evidence_use_contract import derive_evidence_use_contract

            contract = derive_evidence_use_contract(
                pack_hits=[],
                omitted_count=0,
                peg_metrics=None,
                retrieve_mode="hybrid",
                enabled=True,
                inject_constrained_instruction=True,
            )
            return ContextPack(
                text="",
                hits=[],
                omitted_count=0,
                evidence_use_contract=contract,
            )

    plugin._service = _Svc()
    ctx = PromptContext(
        session_id="s1",
        group_id=None,
        user_id="u1",
        identity=Identity(id="bot", name="Bot", personality="t"),
        conversation_text="用户在问什么偏好",
    )
    await plugin.on_pre_prompt(ctx)
    labels = [b.label for b in ctx.blocks]
    assert "上下文资料" not in labels  # empty pack text
    constrained = [b for b in ctx.blocks if b.source == "context_evidence_use"]
    assert len(constrained) == 1
    assert constrained[0].position == "dynamic"
    assert constrained[0].priority < 50  # low priority vs main context 50
    assert "pass_turn" not in constrained[0].text.lower()
    assert "拒" not in constrained[0].text or "拒绝" not in constrained[0].text
    # Soft constraint language present (Chinese)
    assert constrained[0].text.strip()


@pytest.mark.asyncio
async def test_plugin_no_inject_for_nonempty_or_skip() -> None:
    from kernel.types import Identity, PromptContext
    from plugins.context.plugin import ContextPlugin
    from services.context.evidence_use_contract import derive_evidence_use_contract

    plugin = ContextPlugin()
    plugin._enabled = True
    plugin._takeover = True
    plugin._use_token_budget = True
    plugin._max_hits = 5
    plugin._max_doc_hits = 3
    plugin._budget = None
    plugin._query_aware_plan_enabled = False
    plugin._evidence_use_contract_enabled = True
    plugin._evidence_use_inject = True
    plugin._temporal_trace_enabled = False
    plugin._temporal_trace_assembler = None
    plugin._graph_auto_extract = False
    plugin._graph = None

    class _Nonempty:
        async def build_prompt_context(self, query: str, **kwargs):
            del query
            mode = kwargs.get("mode", "hybrid")
            if mode == "skip":
                c = derive_evidence_use_contract(
                    pack_hits=[],
                    omitted_count=0,
                    peg_metrics=None,
                    retrieve_mode="skip",
                    enabled=True,
                    inject_constrained_instruction=True,
                )
                return ContextPack(text="", hits=[], evidence_use_contract=c)
            hits = [_mem("m1")]
            c = derive_evidence_use_contract(
                pack_hits=hits,
                omitted_count=0,
                peg_metrics={"actions": {"keep": 1, "demote": 0, "omit": 0}},
                retrieve_mode=mode,
                enabled=True,
                inject_constrained_instruction=True,
            )
            return ContextPack(
                text="【记忆卡片】\n- x",
                hits=hits,
                evidence_use_contract=c,
            )

    plugin._service = _Nonempty()
    ctx1 = PromptContext(
        session_id="s1",
        group_id=None,
        user_id="u1",
        identity=Identity(id="bot", name="Bot", personality="t"),
        conversation_text="有资料的问题",
    )
    await plugin.on_pre_prompt(ctx1)
    assert not any(b.source == "context_evidence_use" for b in ctx1.blocks)
    assert any(b.label == "上下文资料" for b in ctx1.blocks)

    ctx2 = PromptContext(
        session_id="s1",
        group_id=None,
        user_id="u1",
        identity=Identity(id="bot", name="Bot", personality="t"),
        conversation_text="跳过检索的问题",
    )
    ctx2.retrieve_mode = "skip"
    await plugin.on_pre_prompt(ctx2)
    assert not any(b.source == "context_evidence_use" for b in ctx2.blocks)


@pytest.mark.asyncio
async def test_plugin_inject_does_not_disturb_trace_seed_ids() -> None:
    from unittest.mock import AsyncMock

    from kernel.types import Identity, PromptContext
    from plugins.context.plugin import ContextPlugin
    from services.context.evidence_use_contract import derive_evidence_use_contract

    seed_id = "mem_seed_pre_gate"
    plugin = ContextPlugin()
    plugin._enabled = True
    plugin._takeover = True
    plugin._use_token_budget = True
    plugin._max_hits = 5
    plugin._max_doc_hits = 3
    plugin._budget = None
    plugin._query_aware_plan_enabled = False
    plugin._evidence_use_contract_enabled = True
    plugin._evidence_use_inject = True
    plugin._temporal_trace_enabled = True
    plugin._graph_auto_extract = False
    plugin._graph = None

    assembler = AsyncMock()

    class _TraceResult:
        text = "【记忆时间轨迹】\n- current: x"

    assembler.assemble = AsyncMock(return_value=_TraceResult())
    plugin._temporal_trace_assembler = assembler

    class _EmptyWithSeed:
        async def build_prompt_context(self, query: str, **kwargs):
            del query, kwargs
            # omit_only now injects like empty; seeds still independent of inject.
            c = derive_evidence_use_contract(
                pack_hits=[],
                omitted_count=1,
                peg_metrics={"actions": {"keep": 0, "demote": 0, "omit": 1}},
                retrieve_mode="hybrid",
                enabled=True,
                inject_constrained_instruction=True,
            )
            assert c.pack_state == "omit_only"
            assert c.inject_instruction is True
            return ContextPack(
                text="",
                hits=[],
                omitted_count=1,
                trace_seed_ids=(seed_id,),
                evidence_use_contract=c,
            )

    plugin._service = _EmptyWithSeed()
    ctx = PromptContext(
        session_id="s1",
        group_id=None,
        user_id="u1",
        identity=Identity(id="bot", name="Bot", personality="t"),
        conversation_text="以前你记得我喜欢什么",
        current_message="以前你记得我喜欢什么",
    )
    ctx.retrieve_mode = "hybrid"
    await plugin.on_pre_prompt(ctx)
    assembler.assemble.assert_awaited()
    call_kwargs = assembler.assemble.await_args.kwargs
    assert seed_id in call_kwargs["active_memory_hits"]
    # Instruction block may also be present for empty
    assert any(b.source == "context_evidence_use" for b in ctx.blocks)


@pytest.mark.asyncio
async def test_plugin_contract_disabled_no_instruction_block() -> None:
    from kernel.types import Identity, PromptContext
    from plugins.context.plugin import ContextPlugin
    from services.context.evidence_use_contract import derive_evidence_use_contract

    plugin = ContextPlugin()
    plugin._enabled = True
    plugin._takeover = True
    plugin._use_token_budget = True
    plugin._max_hits = 5
    plugin._max_doc_hits = 3
    plugin._budget = None
    plugin._query_aware_plan_enabled = False
    plugin._evidence_use_contract_enabled = False
    plugin._evidence_use_inject = True
    plugin._temporal_trace_enabled = False
    plugin._temporal_trace_assembler = None
    plugin._graph_auto_extract = False
    plugin._graph = None

    class _Empty:
        async def build_prompt_context(self, query: str, **kwargs):
            del query, kwargs
            c = derive_evidence_use_contract(
                pack_hits=[],
                omitted_count=0,
                peg_metrics=None,
                retrieve_mode="hybrid",
                enabled=False,
                inject_constrained_instruction=True,
            )
            return ContextPack(text="", hits=[], evidence_use_contract=c)

    plugin._service = _Empty()
    ctx = PromptContext(
        session_id="s1",
        group_id=None,
        user_id="u1",
        identity=Identity(id="bot", name="Bot", personality="t"),
        conversation_text="空检索问题",
    )
    await plugin.on_pre_prompt(ctx)
    assert not any(b.source == "context_evidence_use" for b in ctx.blocks)


def test_config_default_and_schema_have_evidence_use_contract() -> None:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "plugins" / "context"
    default = json.loads((root / "config.default.json").read_text(encoding="utf-8"))
    schema = json.loads((root / "config.schema.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))

    euc = default["values"]["evidence_use_contract"]
    assert euc["enabled"] is True
    assert euc["inject_constrained_instruction"] is True

    props = schema["properties"]["evidence_use_contract"]
    assert props["type"] == "object"
    assert "enabled" in props["properties"]
    assert "inject_constrained_instruction" in props["properties"]

    assert manifest["version"] == "0.1.14"
    fields = manifest["config"]["restart_required_fields"]
    assert any(
        f == "evidence_use_contract" or str(f).startswith("evidence_use_contract")
        for f in fields
    )


def test_plugin_version_is_0_1_14() -> None:
    from plugins.context.plugin import ContextPlugin

    assert ContextPlugin.version == "0.1.14"


# ---------------------------------------------------------------------------
# Eval default path unchanged; opt-in records pack_state only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_default_path_unchanged_no_pack_state_field() -> None:
    from services.context.eval import ContextEvalCase, evaluate_context_case
    from services.context.service import ContextService

    service = ContextService([_StaticSource([_mem("m1")])])
    case = ContextEvalCase(
        id="c1",
        query="记忆内容",
        session_id="s",
        user_id="u",
        group_id=None,
        top_k=5,
        max_chars=2000,
    )
    result = await evaluate_context_case(service, case)
    payload = result.to_dict()
    assert "pack_state" not in payload
    assert "evidence_use_contract" not in payload
    assert result.error == ""


@pytest.mark.asyncio
async def test_eval_opt_in_records_pack_state_not_grounding() -> None:
    from services.context.eval import ContextEvalCase, evaluate_context_case
    from services.context.service import ContextService

    service = ContextService([_StaticSource([])])
    case = ContextEvalCase(
        id="c_empty",
        query="无关问题xyz",
        session_id="s",
        user_id="u",
        group_id=None,
        top_k=5,
        max_chars=2000,
    )
    result = await evaluate_context_case(
        service,
        case,
        score_evidence_use_contract=True,
    )
    payload = result.to_dict()
    assert payload.get("pack_state") == "empty"
    assert "answer_used_evidence" not in payload
    assert "grounding" not in str(payload).lower() or payload.get("pack_state") == "empty"
    # Must not claim grounding success
    assert payload.get("answer_used_evidence") is not True
