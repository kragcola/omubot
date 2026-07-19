"""Graph Provenance Gate v1 (gpg_v1) — RED→GREEN contract tests.

Write-side normalizer rejects empty/whitespace/conflict/graph_fact-primary
evidence; read-side light quarantine hides graph_fact:* from ContextProvenance
evidence_refs without changing RRF/hop/confidence.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from kernel.config import BotConfig, KnowledgeGraphConfig
from services.context.sources import _graph_evidence_refs
from services.knowledge_graph import GraphCandidate, GraphFact, KnowledgeGraphService
from services.knowledge_graph.provenance import (
    GraphProvenanceError,
    is_primary_evidence_type,
    normalize_graph_evidence,
    primary_evidence_from_rows,
)

# ---------------------------------------------------------------------------
# Pure normalizer (domain slice)
# ---------------------------------------------------------------------------


def test_normalize_rejects_whitespace_generic_id() -> None:
    with pytest.raises(GraphProvenanceError) as exc:
        normalize_graph_evidence({"type": "message", "id": "   "})
    assert exc.value.code == "whitespace_id"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field in ("id", "card_id", "chunk_id", "message_id")
        for value in (True, False, float("nan"), float("inf"), float("-inf"))
    ],
)
def test_normalize_rejects_boolean_and_nonfinite_id_scalars(
    field: str,
    value: object,
) -> None:
    with pytest.raises(GraphProvenanceError) as exc:
        normalize_graph_evidence({field: value})
    assert exc.value.code == "invalid_id_scalar"
    assert _graph_evidence_refs({"evidence": [{field: value}]}) == ()


def test_normalize_rejects_empty_evidence() -> None:
    with pytest.raises(GraphProvenanceError) as exc:
        normalize_graph_evidence({})
    assert exc.value.code in {"empty_evidence", "missing_id"}


def test_normalize_bare_id_canonicalizes_to_evidence_primary() -> None:
    """Legacy bare non-empty id (no type) → type=evidence; primary after norm.

    Pre-gpg reverse lookup used bare evidence_id; empty type read as
    evidence:<id>. Whitespace-only id remains rejected.
    """
    bare = normalize_graph_evidence({"id": "only-id"})
    assert bare["type"] == "evidence"
    assert bare["id"] == "only-id"
    assert "card_id" not in bare
    assert "chunk_id" not in bare
    assert "message_id" not in bare
    assert is_primary_evidence_type(bare["type"]) is True

    with_quote = normalize_graph_evidence({"id": "9001", "quote": "  hi  "})
    assert with_quote["type"] == "evidence"
    assert with_quote["id"] == "9001"
    assert with_quote["quote"] == "hi"

    # Round-trip for context refs (evidence:<id> form)
    refs = _graph_evidence_refs({"evidence": [with_quote]})
    assert refs == ("evidence:9001",)

    copied = primary_evidence_from_rows([{"id": "9001", "quote": "x"}])
    assert copied is not None
    assert copied["type"] == "evidence"
    assert copied["id"] == "9001"

    # Whitespace bare id still rejected (do not weaken)
    with pytest.raises(GraphProvenanceError) as exc:
        normalize_graph_evidence({"id": "   "})
    assert exc.value.code == "whitespace_id"


def test_normalize_explicit_type_id_does_not_synthesize_alias() -> None:
    """type=doc_chunk + id alone must not invent chunk_id (bridge no-op).

    Alias fields are emitted only when the input used card_id/chunk_id/message_id.
    """
    generic = normalize_graph_evidence(
        {"type": "doc_chunk", "id": "fallback_id", "quote": ""}
    )
    assert generic["type"] == "doc_chunk"
    assert generic["id"] == "fallback_id"
    assert "chunk_id" not in generic

    # Explicit alias still produces type + id + alias
    aliased = normalize_graph_evidence({"chunk_id": "docs/a.md::k"})
    assert aliased["type"] == "doc_chunk"
    assert aliased["id"] == "docs/a.md::k"
    assert aliased["chunk_id"] == "docs/a.md::k"

    typed_alias = normalize_graph_evidence(
        {"type": "doc_chunk", "chunk_id": "docs/b.md::k", "id": "docs/b.md::k"}
    )
    assert typed_alias["chunk_id"] == "docs/b.md::k"


def test_normalize_rejects_conflicting_id_and_card_id() -> None:
    with pytest.raises(GraphProvenanceError) as exc:
        normalize_graph_evidence({"card_id": "card_a", "id": "card_b"})
    assert exc.value.code == "conflicting_ids"


def test_normalize_rejects_conflicting_type_and_card_alias() -> None:
    with pytest.raises(GraphProvenanceError) as exc:
        normalize_graph_evidence({"type": "message", "card_id": "card_1"})
    assert exc.value.code in {"conflicting_types", "conflicting_ids"}


def test_normalize_rejects_graph_fact_as_primary() -> None:
    with pytest.raises(GraphProvenanceError) as exc:
        normalize_graph_evidence({"type": "graph_fact", "id": "gf_abc"})
    assert exc.value.code == "graph_fact_primary"


def test_normalize_card_chunk_message_fixture_canonical() -> None:
    card = normalize_graph_evidence(
        {"card_id": "card_1", "quote": "  hi  ", "source": "s", "scope": "user", "scope_id": "1"}
    )
    assert card["type"] == "memory_card"
    assert card["id"] == "card_1"
    assert card["card_id"] == "card_1"
    assert card["quote"] == "hi"
    assert card["source"] == "s"
    assert card["scope"] == "user"
    assert card["scope_id"] == "1"

    chunk = normalize_graph_evidence({"chunk_id": "docs/a.md::k"})
    assert chunk["type"] == "doc_chunk"
    assert chunk["id"] == "docs/a.md::k"
    assert chunk["chunk_id"] == "docs/a.md::k"

    msg = normalize_graph_evidence({"message_id": "msg-9"})
    assert msg["type"] == "message"
    assert msg["id"] == "msg-9"
    assert msg["message_id"] == "msg-9"

    fixture = normalize_graph_evidence({"type": "fixture", "id": "evidence-1"})
    assert fixture["type"] == "fixture"
    assert fixture["id"] == "evidence-1"
    # Explicit type+id must not synthesize alias fields
    assert "card_id" not in fixture
    assert "chunk_id" not in fixture
    assert "message_id" not in fixture


def test_is_primary_evidence_type_deny_derived_only() -> None:
    """Primary = non-empty typed ref that is not in the derived deny-set."""
    assert is_primary_evidence_type("memory_card") is True
    assert is_primary_evidence_type("doc_chunk") is True
    assert is_primary_evidence_type("message") is True
    assert is_primary_evidence_type("fixture") is True
    assert is_primary_evidence_type("observation") is True
    assert is_primary_evidence_type("episode") is True
    assert is_primary_evidence_type("evidence") is True  # legacy bare-id canonical type
    assert is_primary_evidence_type("graph_fact") is False
    assert is_primary_evidence_type("") is False
    assert is_primary_evidence_type(None) is False


def test_observation_custom_typed_ref_unified_primary_contract() -> None:
    """RED proof: normalize / is_primary / context refs / supersede-copy agree.

    Explicit non-derived types (e.g. observation) must be primary on both
    read-side evidence_refs and supersede primary_evidence_from_rows.
    """
    raw = {"type": "observation", "id": "obs_1"}
    canonical = normalize_graph_evidence(raw)
    assert canonical["type"] == "observation"
    assert canonical["id"] == "obs_1"
    assert is_primary_evidence_type(canonical["type"]) is True

    refs = _graph_evidence_refs(
        {"evidence": [canonical, {"type": "graph_fact", "id": "gf_x"}]}
    )
    assert refs == ("observation:obs_1",)
    assert "graph_fact:gf_x" not in refs

    copied = primary_evidence_from_rows(
        [
            {"type": "graph_fact", "id": "gf_x"},
            {"type": "observation", "id": "obs_1", "quote": "  note  "},
        ]
    )
    assert copied is not None
    assert copied["type"] == "observation"
    assert copied["id"] == "obs_1"
    assert copied.get("quote") == "note"

    # graph_fact alone remains non-primary everywhere
    assert primary_evidence_from_rows(
        [{"type": "graph_fact", "id": "gf_only"}]
    ) is None
    assert _graph_evidence_refs(
        {"evidence": [{"type": "graph_fact", "id": "gf_only"}]}
    ) == ()


# ---------------------------------------------------------------------------
# Service / store write gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whitespace_generic_id_direct_promote_writes_nothing(tmp_path: Path) -> None:
    """Specific failing assertion: no active fact and no evidence rows."""
    graph = KnowledgeGraphService(tmp_path / "ws.db", provenance_gate_enabled=True)
    await graph.init()
    try:
        with pytest.raises(GraphProvenanceError):
            await graph._store.add_fact(
                subject="用户1",
                predicate="喜欢",
                object="音游",
                confidence=0.9,
                source="test",
                evidence={"type": "message", "id": "   "},
                status="active",
            )
        # Also block privileged promote path at service level (returns None)
        result = await graph.submit_fact_candidate(
            subject="用户1",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"type": "message", "id": "   "},
            promote_directly=True,
        )
        assert result is None
        assert await graph.list_relationships() == []
        async with aiosqlite.connect(tmp_path / "ws.db") as db:
            async with db.execute("SELECT COUNT(*) AS n FROM graph_facts") as cur:
                assert (await cur.fetchone())[0] == 0
            async with db.execute("SELECT COUNT(*) AS n FROM graph_evidence") as cur:
                assert (await cur.fetchone())[0] == 0
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_empty_evidence_mid_confidence_no_pending_when_gate_enabled(
    tmp_path: Path,
) -> None:
    graph = KnowledgeGraphService(tmp_path / "empty.db", provenance_gate_enabled=True)
    await graph.init()
    try:
        result = await graph.submit_fact_candidate(
            subject="用户1",
            predicate="喜欢",
            object="音游",
            confidence=0.72,
            source="extract",
            evidence={},
        )
        assert result is None
        assert await graph.list_candidates() == []
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_conflicting_ids_rejected_no_row(tmp_path: Path) -> None:
    graph = KnowledgeGraphService(tmp_path / "conflict.db", provenance_gate_enabled=True)
    await graph.init()
    try:
        result = await graph.submit_fact_candidate(
            subject="用户1",
            predicate="喜欢",
            object="音游",
            confidence=0.8,
            source="extract",
            evidence={"card_id": "card_a", "id": "card_b"},
        )
        assert result is None
        assert await graph.list_candidates() == []
        assert await graph.list_relationships() == []
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_valid_shapes_round_trip_canonical_refs(tmp_path: Path) -> None:
    graph = KnowledgeGraphService(tmp_path / "canon.db", provenance_gate_enabled=True)
    await graph.init()
    try:
        card = await graph.submit_fact_candidate(
            subject="用户1",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="t",
            evidence={"card_id": "card_1", "quote": "喜欢音游"},
            promote_directly=True,
        )
        assert isinstance(card, GraphFact)
        card_ev = await graph._store.list_evidence(card.fact_id)
        assert card_ev[0]["type"] == "memory_card"
        assert card_ev[0]["id"] == "card_1"

        chunk = await graph.submit_fact_candidate(
            subject="文档",
            predicate="描述",
            object="风格",
            confidence=0.9,
            source="t",
            evidence={"chunk_id": "docs/a.md::k"},
            promote_directly=True,
        )
        assert isinstance(chunk, GraphFact)
        chunk_ev = await graph._store.list_evidence(chunk.fact_id)
        assert chunk_ev[0]["type"] == "doc_chunk"
        assert chunk_ev[0]["id"] == "docs/a.md::k"

        msg = await graph.submit_fact_candidate(
            subject="用户2",
            predicate="说",
            object="你好",
            confidence=0.9,
            source="t",
            evidence={"message_id": "m-1"},
            promote_directly=True,
        )
        assert isinstance(msg, GraphFact)
        msg_ev = await graph._store.list_evidence(msg.fact_id)
        assert msg_ev[0]["type"] == "message"
        assert msg_ev[0]["id"] == "m-1"

        fixture = await graph.submit_fact_candidate(
            subject="fixture",
            predicate="is",
            object="valid",
            confidence=0.9,
            source="t",
            evidence={"type": "fixture", "id": "evidence-1"},
            promote_directly=True,
        )
        assert isinstance(fixture, GraphFact)
        fix_ev = await graph._store.list_evidence(fixture.fact_id)
        assert fix_ev[0]["type"] == "fixture"
        assert fix_ev[0]["id"] == "evidence-1"

        # Candidate path also stores canonical evidence_json
        cand = await graph.submit_fact_candidate(
            subject="用户3",
            predicate="用",
            object="Omubot",
            confidence=0.7,
            source="extract",
            evidence={"card_id": "card_pending"},
        )
        assert isinstance(cand, GraphCandidate)
        assert cand.evidence["type"] == "memory_card"
        assert cand.evidence["id"] == "card_pending"
        assert cand.evidence["card_id"] == "card_pending"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_legacy_invalid_pending_approve_requeues_with_closed_note(
    tmp_path: Path,
) -> None:
    graph = KnowledgeGraphService(tmp_path / "legacy.db", provenance_gate_enabled=True)
    await graph.init()
    try:
        # Seed invalid pending by disabling gate on a throwaway service path:
        # insert raw candidate JSON via store with gate off temporarily.
        bad = await graph._store.add_candidate(
            subject="用户1",
            predicate="喜欢",
            object="垃圾",
            confidence=0.8,
            source="legacy",
            evidence={},  # will be blocked on promote when gate enabled
            status="pending",
            _skip_provenance_gate=True,  # type: ignore[call-arg]
        )
    except TypeError:
        # Fallback: use gate-disabled service to seed, then reopen with gate on
        await graph.close()
        seeder = KnowledgeGraphService(tmp_path / "legacy.db", provenance_gate_enabled=False)
        await seeder.init()
        try:
            seeded = await seeder.submit_fact_candidate(
                subject="用户1",
                predicate="喜欢",
                object="垃圾",
                confidence=0.8,
                source="legacy",
                evidence={},
            )
            assert isinstance(seeded, GraphCandidate)
            cid = seeded.candidate_id
        finally:
            await seeder.close()
        graph = KnowledgeGraphService(tmp_path / "legacy.db", provenance_gate_enabled=True)
        await graph.init()
    else:
        cid = bad.candidate_id

    try:
        fact = await graph.approve_candidate(cid)
        assert fact is None
        rows = await graph.list_candidates(status="pending")
        assert len(rows) == 1
        assert rows[0]["candidate_id"] == cid
        note = str(rows[0].get("review_note") or "")
        assert note.startswith("provenance_gate:")
        assert await graph.list_relationships() == []
        async with aiosqlite.connect(tmp_path / "legacy.db") as db:
            async with db.execute("SELECT COUNT(*) AS n FROM graph_facts") as cur:
                assert (await cur.fetchone())[0] == 0
            async with db.execute("SELECT COUNT(*) AS n FROM graph_evidence") as cur:
                assert (await cur.fetchone())[0] == 0
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_approve_invalid_cancels_clean_no_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeder = KnowledgeGraphService(tmp_path / "cancel.db", provenance_gate_enabled=False)
    await seeder.init()
    try:
        seeded = await seeder.submit_fact_candidate(
            subject="用户1",
            predicate="喜欢",
            object="取消",
            confidence=0.8,
            source="legacy",
            evidence={},
        )
        assert isinstance(seeded, GraphCandidate)
        cid = seeded.candidate_id
    finally:
        await seeder.close()

    graph = KnowledgeGraphService(tmp_path / "cancel.db", provenance_gate_enabled=True)
    await graph.init()
    try:
        original = graph._store.promote_candidate

        async def _cancel(*args: Any, **kwargs: Any) -> Any:
            # Simulate cancellation while promoting invalid legacy evidence
            raise asyncio.CancelledError()

        monkeypatch.setattr(graph._store, "promote_candidate", _cancel)
        with pytest.raises(asyncio.CancelledError):
            await graph.approve_candidate(cid)
        # Restore and ensure candidate still pending, no facts
        monkeypatch.setattr(graph._store, "promote_candidate", original)
        rows = await graph.list_candidates(status="pending")
        assert any(r["candidate_id"] == cid for r in rows)
        assert await graph.list_relationships() == []
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_supersede_copies_valid_primary_and_refuses_empty_derived(
    tmp_path: Path,
) -> None:
    graph = KnowledgeGraphService(tmp_path / "sup.db", provenance_gate_enabled=True)
    await graph.init()
    try:
        old = await graph.submit_fact_candidate(
            subject="用户1",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="t",
            evidence={"card_id": "card_1", "quote": "喜欢音游"},
            promote_directly=True,
        )
        assert isinstance(old, GraphFact)
        replacement = await graph.supersede_relationship(
            old.fact_id,
            subject="用户1",
            predicate="喜欢",
            object="节奏游戏",
            confidence=0.91,
            source="admin",
            note="更准",
        )
        assert replacement is not None
        rep_ev = await graph._store.list_evidence(replacement.fact_id)
        assert rep_ev[0]["type"] == "memory_card"
        assert rep_ev[0]["id"] == "card_1"

        # Build active fact with only graph_fact-derived / empty primary by
        # inserting via raw SQL (simulates pre-gate rows).
        fact_id = "gf_derived_only"
        async with aiosqlite.connect(tmp_path / "sup.db") as db:
            await db.execute(
                "INSERT INTO graph_facts "
                "(fact_id, subject, predicate, object, confidence, status, scope, scope_id, "
                "source, supersedes, metadata_json, created_at, updated_at) "
                "VALUES (?, '用户x', '是', '派生', 0.9, 'active', 'global', 'global', "
                "'legacy', NULL, '{}', '2026-01-01T00:00:00+08:00', '2026-01-01T00:00:00+08:00')",
                (fact_id,),
            )
            await db.execute(
                "INSERT INTO graph_evidence "
                "(evidence_row_id, fact_id, evidence_type, evidence_id, quote, created_at) "
                "VALUES ('ge_d1', ?, 'graph_fact', ?, '', '2026-01-01T00:00:00+08:00')",
                (fact_id, fact_id),
            )
            await db.commit()

        refused = await graph.supersede_relationship(
            fact_id,
            subject="用户x",
            predicate="是",
            object="新值",
            confidence=0.95,
            source="admin",
        )
        assert refused is None
        still = await graph.get_relationship(fact_id)
        assert still is not None
        assert still["status"] == "active"
        assert still["object"] == "派生"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_supersede_explicit_valid_evidence_works(tmp_path: Path) -> None:
    graph = KnowledgeGraphService(tmp_path / "sup2.db", provenance_gate_enabled=True)
    await graph.init()
    try:
        old = await graph.submit_fact_candidate(
            subject="用户1",
            predicate="喜欢",
            object="A",
            confidence=0.9,
            source="t",
            evidence={"card_id": "card_old"},
            promote_directly=True,
        )
        assert isinstance(old, GraphFact)
        new = await graph.supersede_relationship(
            old.fact_id,
            subject="用户1",
            predicate="喜欢",
            object="B",
            confidence=0.92,
            source="admin",
            evidence={"chunk_id": "docs/b.md::k"},
            note="换证据",
        )
        assert new is not None
        ev = await graph._store.list_evidence(new.fact_id)
        assert ev[0]["type"] == "doc_chunk"
        assert ev[0]["id"] == "docs/b.md::k"
    finally:
        await graph.close()


# ---------------------------------------------------------------------------
# Read-time quarantine
# ---------------------------------------------------------------------------


def test_graph_evidence_refs_ignores_graph_fact_preserves_primary() -> None:
    refs = _graph_evidence_refs(
        {
            "evidence": [
                {"type": "graph_fact", "id": "gf_self"},
                {"type": "memory_card", "id": "card_1"},
                {"type": "doc_chunk", "id": "docs/a.md::k"},
                {"type": "message", "id": "m1"},
                {"type": "fixture", "id": "fx1"},
                {"type": "memory_card", "id": "   "},
            ]
        }
    )
    assert "graph_fact:gf_self" not in refs
    assert refs == (
        "memory_card:card_1",
        "doc_chunk:docs/a.md::k",
        "message:m1",
        "fixture:fx1",
    )


def test_graph_evidence_refs_empty_when_only_derived() -> None:
    assert _graph_evidence_refs(
        {"evidence": [{"type": "graph_fact", "id": "gf_x"}]}
    ) == ()
    assert _graph_evidence_refs({"evidence": []}) == ()


def test_graph_provenance_kind_helper_values() -> None:
    from services.context.sources import _graph_provenance_kind

    assert (
        _graph_provenance_kind(
            {"evidence": [{"type": "memory_card", "id": "c1"}]}
        )
        == "primary"
    )
    assert (
        _graph_provenance_kind(
            {"evidence": [{"type": "graph_fact", "id": "gf1"}]}
        )
        == "derived_only"
    )
    assert _graph_provenance_kind({"evidence": []}) == "none"


# ---------------------------------------------------------------------------
# Disabled = legacy acceptance; config bootstrap defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_disabled_legacy_accepts_whitespace_and_empty_pending(
    tmp_path: Path,
) -> None:
    """Unsafe rollback path: disabled preserves truthy / empty-candidate legacy."""
    graph = KnowledgeGraphService(tmp_path / "legacy_on.db", provenance_gate_enabled=False)
    await graph.init()
    try:
        # Empty evidence + mid confidence still becomes pending (legacy)
        cand = await graph.submit_fact_candidate(
            subject="用户1",
            predicate="喜欢",
            object="空证",
            confidence=0.7,
            source="legacy",
            evidence={},
        )
        assert isinstance(cand, GraphCandidate)

        # Whitespace id truthy → direct promote still writes (legacy unsafe)
        fact = await graph.submit_fact_candidate(
            subject="用户2",
            predicate="喜欢",
            object="空白",
            confidence=0.9,
            source="legacy",
            evidence={"type": "message", "id": "   "},
            promote_directly=True,
        )
        assert isinstance(fact, GraphFact)
        ev = await graph._store.list_evidence(fact.fact_id)
        assert len(ev) == 1
        assert ev[0]["id"].strip() == "" or ev[0]["id"] == "   "
    finally:
        await graph.close()


def test_bot_config_provenance_gate_default_enabled() -> None:
    cfg = BotConfig()
    assert cfg.knowledge_graph.provenance_gate_enabled is True
    off = BotConfig(knowledge_graph=KnowledgeGraphConfig(provenance_gate_enabled=False))
    assert off.knowledge_graph.provenance_gate_enabled is False


def test_knowledge_graph_service_accepts_explicit_policy(tmp_path: Path) -> None:
    g_on = KnowledgeGraphService(tmp_path / "a.db", provenance_gate_enabled=True)
    assert g_on.provenance_gate_enabled is True
    g_off = KnowledgeGraphService(tmp_path / "b.db", provenance_gate_enabled=False)
    assert g_off.provenance_gate_enabled is False


def test_bootstrap_passes_config_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_runtime must pass provenance_gate_enabled from BotConfig."""
    import inspect

    import bootstrap.chat_runtime as runtime

    src = inspect.getsource(runtime.build_chat_runtime)
    assert "provenance_gate_enabled" in src
    assert "knowledge_graph" in src


# ---------------------------------------------------------------------------
# Same-pattern surface: store methods honor gate flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_add_candidate_normalizes_when_enabled(tmp_path: Path) -> None:
    from services.knowledge_graph.store import KnowledgeGraphStore

    store = KnowledgeGraphStore(tmp_path / "st.db", provenance_gate_enabled=True)
    await store.init()
    try:
        cand = await store.add_candidate(
            subject="s",
            predicate="p",
            object="o",
            confidence=0.7,
            source="t",
            evidence={"card_id": "c1"},
        )
        assert cand.evidence["type"] == "memory_card"
        assert cand.evidence["id"] == "c1"
        raw = json.loads(
            (
                await (
                    await store._require_db().execute(
                        "SELECT evidence_json FROM extraction_candidates WHERE candidate_id = ?",
                        (cand.candidate_id,),
                    )
                ).fetchone()
            )[0]
        )
        assert raw["type"] == "memory_card"
        assert raw["id"] == "c1"
    finally:
        await store.close()
