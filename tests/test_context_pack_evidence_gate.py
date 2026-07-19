"""Pack-Time Confidence/Evidence Gate v1 (peg_v1) pure + pack-order tests."""

from __future__ import annotations

import pytest

from services.context.pack_evidence_gate import (
    DEFAULT_PACK_EVIDENCE_GATE_POLICY,
    GATE_VERSION,
    PackEvidenceGatePolicy,
    apply_pack_evidence_gate,
    extract_trace_seed_ids,
    is_memory_hint_hit,
    policy_from_mapping,
    sanitize_pack_evidence_gate_metrics,
)
from services.context.packing import ContextBudget, pack_context_hits
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
    status: str = "active",
    score: float = 0.5,
    msg_id: str = "",
    evidence_refs: tuple[str, ...] = (),
    retriever: str = "card_store",
    scope: str = "user",
    scope_id: str = "u1",
) -> ContextHit:
    bd = (
        ContextScoreBreakdown(source_score=score, confidence=confidence)
        if confidence is not None
        else ContextScoreBreakdown(source_score=score)
    )
    # When confidence is explicitly None, leave breakdown.confidence as None.
    if confidence is None:
        bd = ContextScoreBreakdown(source_score=score, confidence=None)
    prov = ContextProvenance(
        owner="memory_cards",
        source_id=id,
        source_message_id=msg_id,
        evidence_refs=evidence_refs,
    )
    return ContextHit(
        id=id,
        type="memory_card",
        content=content,
        score=score,
        source=source,
        status=status,
        retriever=retriever,
        scope=scope,
        scope_id=scope_id,
        provenance=prov,
        score_breakdown=bd,
        metadata={"confidence": confidence} if confidence is not None else {},
    )


def _doc(
    id: str,
    *,
    content: str = "文档正文",
    score: float = 0.4,
) -> ContextHit:
    return ContextHit(
        id=id,
        type="doc_chunk",
        content=content,
        score=score,
        source="knowledge",
        status="active",
        retriever="knowledge_bm25_ngram",
        score_breakdown=ContextScoreBreakdown(source_score=score),
    )


def _graph(
    id: str,
    *,
    content: str = "A --rel-> B",
    confidence: float | None = 0.9,
    status: str = "active",
    score: float = 0.3,
    hop: int = 0,
    msg_id: str = "",
    evidence_refs: tuple[str, ...] = (),
) -> ContextHit:
    bd = ContextScoreBreakdown(source_score=score, confidence=confidence)
    if confidence is None:
        bd = ContextScoreBreakdown(source_score=score, confidence=None)
    return ContextHit(
        id=id,
        type="graph_fact",
        content=content,
        score=score,
        source="knowledge_graph",
        status=status,
        retriever="graph_ngram",
        provenance=ContextProvenance(
            owner="graph_facts",
            source_id=id,
            source_message_id=msg_id,
            evidence_refs=evidence_refs,
        ),
        score_breakdown=bd,
        metadata={"graph_hop": hop, "confidence": confidence},
    )


def _ids(result) -> list[str]:
    return [h.id for h in result.hits]


def test_trusted_manual_empty_refs_kept() -> None:
    hit = _mem("m_manual", source="manual", confidence=0.2, msg_id="", evidence_refs=())
    r = apply_pack_evidence_gate([hit])
    assert _ids(r) == ["m_manual"]
    assert r.omitted_count == 0
    assert r.metrics["actions"]["keep"] == 1


def test_trusted_user_config_and_migration_and_food_kept() -> None:
    hits = [
        _mem("m_uc", source="user_config", confidence=0.1),
        _mem("m_mig", source="migration", confidence=0.1),
        _mem("m_food", source="food_plugin", confidence=0.1),
    ]
    r = apply_pack_evidence_gate(hits)
    assert _ids(r) == ["m_uc", "m_mig", "m_food"]


def test_evidence_rich_extractor_kept() -> None:
    hit = _mem(
        "m_ev",
        source="memo_extractor",
        confidence=0.3,
        msg_id="msg_42",
        evidence_refs=("message:msg_42",),
    )
    r = apply_pack_evidence_gate([hit])
    assert _ids(r) == ["m_ev"]
    assert r.metrics["reasons"].get("gate_keep_evidence") == 1


def test_low_conf_untrusted_memory_demoted() -> None:
    strong = _mem(
        "m_strong",
        source="memo_extractor",
        confidence=0.9,
        msg_id="m1",
        score=0.9,
    )
    weak = _mem(
        "m_weak",
        source="memo_extractor",
        confidence=0.2,
        msg_id="",
        evidence_refs=(),
        score=0.95,  # high RRF fusion score must not rescue
    )
    r = apply_pack_evidence_gate([strong, weak])
    assert _ids(r) == ["m_strong", "m_weak"]  # kept then demoted
    assert r.metrics["actions"]["keep"] == 1
    assert r.metrics["actions"]["demote"] == 1
    assert r.metrics["reasons"].get("gate_demote_memory_weak_empty") == 1


def test_nan_inf_memory_omitted() -> None:
    nan_hit = _mem("m_nan", confidence=float("nan"), msg_id="x")
    inf_hit = _mem("m_inf", confidence=float("inf"), msg_id="y")
    r = apply_pack_evidence_gate([nan_hit, inf_hit])
    assert _ids(r) == []
    assert r.omitted_count == 2
    assert r.metrics["actions"]["omit"] == 2
    assert r.metrics["reasons"].get("gate_omit_non_finite_confidence") == 2


def test_non_active_memory_omitted() -> None:
    hit = _mem("m_old", status="superseded", confidence=0.99, msg_id="m")
    r = apply_pack_evidence_gate([hit])
    assert _ids(r) == []
    assert r.omitted_count == 1


def test_doc_no_confidence_kept() -> None:
    hit = _doc("d1", content="有正文的文档")
    assert hit.score_breakdown is not None
    assert hit.score_breakdown.confidence is None
    r = apply_pack_evidence_gate([hit])
    assert _ids(r) == ["d1"]


def test_blank_doc_omitted() -> None:
    hit = _doc("d_blank", content="   \n  ")
    r = apply_pack_evidence_gate([hit])
    assert _ids(r) == []
    assert r.omitted_count == 1
    assert r.metrics["reasons"].get("gate_omit_blank_doc") == 1


def test_graph_evidence_rich_kept() -> None:
    hit = _graph(
        "g_ev",
        confidence=0.4,
        hop=2,
        evidence_refs=("observation:obs1",),
    )
    r = apply_pack_evidence_gate([hit])
    assert _ids(r) == ["g_ev"]


def test_weak_hop_graph_empty_evidence_omitted() -> None:
    hit = _graph("g_hop", confidence=0.2, hop=1, evidence_refs=(), msg_id="")
    r = apply_pack_evidence_gate([hit])
    assert _ids(r) == []
    assert r.omitted_count == 1
    assert r.metrics["reasons"].get("gate_omit_graph_weak_uncorroborated") == 1


def test_direct_empty_evidence_graph_demoted() -> None:
    hit = _graph("g_dir", confidence=0.2, hop=0, evidence_refs=(), msg_id="")
    r = apply_pack_evidence_gate([hit])
    assert _ids(r) == ["g_dir"]
    assert r.metrics["actions"]["demote"] == 1
    assert r.metrics["reasons"].get("gate_demote_graph_weak_empty") == 1


def test_memory_hint_kept() -> None:
    by_retriever = ContextHit(
        id="hint_x",
        type="memory_card",
        content="有 N 张卡片",
        score=0.05,
        source="card_store",
        retriever="card_store_hint",
        score_breakdown=ContextScoreBreakdown(source_score=0.05),
    )
    by_id = ContextHit(
        id="memory_hint:user:u1",
        type="memory_card",
        content="有 N 张卡片",
        score=0.05,
        source="card_store",
        retriever="card_store",
        score_breakdown=ContextScoreBreakdown(source_score=0.05),
    )
    assert is_memory_hint_hit(by_retriever)
    assert is_memory_hint_hit(by_id)
    r = apply_pack_evidence_gate([by_retriever, by_id])
    assert _ids(r) == ["hint_x", "memory_hint:user:u1"]
    assert r.metrics["reasons"].get("gate_keep_hint") == 2


def test_type_caps_not_resurrected() -> None:
    """Gate must not reintroduce hits already removed by type caps / top_k."""
    # Simulate post-cap list: only one doc survived caps earlier.
    kept_doc = _doc("d_cap", content="唯一文档")
    # A demoted weak memory is present; a "would-have-been" second doc is absent.
    weak_mem = _mem("m_weak", confidence=0.1, msg_id="", source="memo_extractor")
    r = apply_pack_evidence_gate([kept_doc, weak_mem])
    assert "d_other" not in _ids(r)
    assert set(_ids(r)) == {"d_cap", "m_weak"}


def test_tight_budget_favors_kept_over_demoted() -> None:
    kept = _mem(
        "m_kept",
        content="A" * 90,
        confidence=0.95,
        msg_id="m1",
        score=0.4,
    )
    demoted = _mem(
        "m_dem",
        content="B" * 90,
        confidence=0.1,
        msg_id="",
        source="memo_extractor",
        score=0.99,
    )
    gated = apply_pack_evidence_gate([demoted, kept])  # demoted first in search order
    assert _ids(gated) == ["m_kept", "m_dem"]  # kept first after gate
    budget = ContextBudget(
        total_tokens=80,
        memory_tokens=40,
        doc_tokens=0,
        graph_tokens=0,
        buffer_tokens=5,
    )
    pack = pack_context_hits(list(gated.hits), budget=budget, wrap_with_safety_tags=False)
    pack_ids = [h.id for h in pack.hits]
    assert "m_kept" in pack_ids
    # Tight budget should prefer earlier (kept) hit; demoted may be omitted by pack.
    if "m_dem" in pack_ids:
        assert pack_ids.index("m_kept") < pack_ids.index("m_dem")


def test_disabled_is_strict_identity() -> None:
    a = _mem("a", confidence=0.1, msg_id="", source="memo_extractor")
    b = _mem("b", status="superseded", confidence=0.9, msg_id="m")
    c = _doc("c", content="  ")
    inputs = [a, b, c]
    policy = PackEvidenceGatePolicy(enabled=False)
    r = apply_pack_evidence_gate(inputs, policy=policy)
    assert list(r.hits) == inputs  # same objects, same order
    assert r.hits[0] is a
    assert r.hits[1] is b
    assert r.hits[2] is c
    assert r.omitted_count == 0
    assert r.metrics["identity"] is True
    assert r.metrics["enabled"] is False


def test_policy_mapping_requires_real_bool_for_enabled() -> None:
    """Malformed raw mappings fail safe to enabled instead of coercing 0/strings."""
    assert policy_from_mapping({"enabled": False}).enabled is False
    assert policy_from_mapping({"enabled": 0}).enabled is True
    assert policy_from_mapping({"enabled": "false"}).enabled is True


def test_empty_pack() -> None:
    r = apply_pack_evidence_gate([])
    assert list(r.hits) == []
    assert r.omitted_count == 0
    assert r.metrics["actions"]["keep"] == 0


def test_cross_scope_untouched() -> None:
    """Gate does not rewrite scope / scope_id."""
    u = _mem("mu", scope="user", scope_id="u1", confidence=0.9, msg_id="m")
    g = _mem("mg", scope="group", scope_id="g9", confidence=0.9, msg_id="n")
    r = apply_pack_evidence_gate([u, g])
    assert r.hits[0].scope == "user" and r.hits[0].scope_id == "u1"
    assert r.hits[1].scope == "group" and r.hits[1].scope_id == "g9"


def test_input_hit_mutation_safety() -> None:
    hit = _mem("m1", confidence=0.2, msg_id="", source="memo_extractor")
    original_content = hit.content
    original_score = hit.score
    original_meta = dict(hit.metadata)
    r = apply_pack_evidence_gate([hit])
    assert hit.content is original_content or hit.content == original_content
    assert hit.score == original_score
    assert hit.metadata == original_meta
    # Demoted hit is the same object (not mutated), still present
    assert r.hits[0] is hit


def test_secret_free_gate_metrics() -> None:
    secret_hit = _mem(
        "card_secret_id_xyz",
        content="用户密码是 hunter2",
        confidence=0.1,
        msg_id="msg_secret_999",
        source="memo_extractor",
    )
    r = apply_pack_evidence_gate([secret_hit])
    blob = str(dict(r.metrics))
    assert "hunter2" not in blob
    assert "card_secret_id_xyz" not in blob
    assert "msg_secret_999" not in blob
    assert "用户密码" not in blob
    assert set(r.metrics.keys()) <= {
        "version",
        "enabled",
        "identity",
        "actions",
        "reasons",
    }
    assert r.metrics["version"] == GATE_VERSION
    cleaned = sanitize_pack_evidence_gate_metrics(
        {
            **dict(r.metrics),
            "query": "should drop",
            "api_key": "sk-leak",
            "reasons": {
                **dict(r.metrics["reasons"]),
                "evil_reason": 3,
            },
        }
    )
    assert "query" not in cleaned
    assert "api_key" not in cleaned
    assert "evil_reason" not in cleaned["reasons"]


def test_pre_gate_temporal_trace_seed_ids() -> None:
    real = _mem("card_real", confidence=0.1, msg_id="", source="memo_extractor")
    hint = ContextHit(
        id="memory_hint:user:u1",
        type="memory_card",
        content="hint",
        score=0.05,
        source="card_store",
        retriever="card_store_hint",
    )
    doc = _doc("d1")
    seeds = extract_trace_seed_ids([real, hint, doc])
    assert seeds == ("card_real",)
    # Even after gate demotes real, seed extraction is pre-gate caller's job
    gated = apply_pack_evidence_gate([real, hint, doc])
    assert "card_real" in _ids(gated)
    assert is_memory_hint_hit(hint)


def test_context_pack_to_dict_hides_trace_seed_ids() -> None:
    pack = ContextPack(
        text="x",
        hits=[],
        omitted_count=0,
        trace_seed_ids=("card_a", "card_b"),
    )
    d = pack.to_dict()
    assert set(d.keys()) == {"text", "hits", "omitted_count"}
    assert "trace_seed_ids" not in d


def test_high_conf_untrusted_empty_kept() -> None:
    hit = _mem(
        "m_ok",
        source="memo_extractor",
        confidence=0.8,
        msg_id="",
        evidence_refs=(),
    )
    r = apply_pack_evidence_gate([hit])
    assert _ids(r) == ["m_ok"]
    assert r.metrics["actions"]["keep"] == 1


def test_policy_soft_bounds_clamped() -> None:
    p = PackEvidenceGatePolicy(
        enabled=True,
        memory_soft_confidence=2.5,
        graph_soft_confidence=-1.0,
    )
    assert p.memory_soft_confidence == 1.0
    assert p.graph_soft_confidence == 0.0


def test_default_policy_values() -> None:
    p = DEFAULT_PACK_EVIDENCE_GATE_POLICY
    assert p.enabled is True
    assert p.memory_soft_confidence == pytest.approx(0.45)
    assert p.graph_soft_confidence == pytest.approx(0.60)


def test_kept_then_demoted_order_stable() -> None:
    d1 = _mem("d1", confidence=0.1, msg_id="", source="memo_extractor", score=0.9)
    k1 = _mem("k1", confidence=0.9, msg_id="m", score=0.1)
    d2 = _mem("d2", confidence=0.05, msg_id="", source="memo_extractor", score=0.8)
    k2 = _mem("k2", confidence=0.9, msg_id="n", score=0.2)
    r = apply_pack_evidence_gate([d1, k1, d2, k2])
    assert _ids(r) == ["k1", "k2", "d1", "d2"]


def test_graph_high_conf_empty_evidence_kept_or_demote_not_omit() -> None:
    """Finite high conf + empty evidence + hop0: not hard omit."""
    hit = _graph("g_hi", confidence=0.9, hop=0, evidence_refs=())
    r = apply_pack_evidence_gate([hit])
    assert "g_hi" in _ids(r)
    assert r.omitted_count == 0
