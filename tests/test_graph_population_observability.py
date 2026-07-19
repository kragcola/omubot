"""Graph Population & Evidence-Quality Observability v1 (gpo_v1) tests.

Read-only health snapshot: pure classification + store/service ownership +
admin route public API. No schema migration, repair, CLI, or frontend.
"""

from __future__ import annotations

import ast
import json
import secrets
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kernel.config import BotConfig, KnowledgeGraphConfig
from services.knowledge_graph import KnowledgeGraphService
from services.knowledge_graph.observability import (
    EVIDENCE_TYPE_BUCKETS,
    FACT_SUPPORT_BUCKETS,
    KNOWN_PROVENANCE_GATE_CODES,
    PENDING_QUALITY_BUCKETS,
    ROW_QUALITY_BUCKETS,
    classify_evidence_mapping_quality,
    classify_evidence_type_bucket,
    classify_fact_support,
    classify_pending_evidence_quality,
    classify_pending_gate_code,
    empty_observability_payload,
)
from services.knowledge_graph.store import KnowledgeGraphStore
from services.knowledge_graph.types import GraphFact

# ---------------------------------------------------------------------------
# Pure classifier (domain slice)
# ---------------------------------------------------------------------------


def test_classify_evidence_mapping_qualities() -> None:
    assert classify_evidence_mapping_quality(None) == "missing"
    assert classify_evidence_mapping_quality({}) == "missing"
    assert (
        classify_evidence_mapping_quality({"type": "memory_card", "id": "c1"})
        == "primary"
    )
    assert (
        classify_evidence_mapping_quality({"type": "graph_fact", "id": "gf_x"})
        == "derived"
    )
    assert (
        classify_evidence_mapping_quality({"type": "message", "id": "   "})
        == "invalid"
    )
    assert classify_evidence_mapping_quality("not-a-mapping") == "invalid"
    assert classify_evidence_mapping_quality([{"id": "x"}]) == "invalid"


def test_classify_fact_support_priority() -> None:
    assert classify_fact_support([]) == "none"
    assert classify_fact_support(None) == "none"
    assert (
        classify_fact_support([{"type": "memory_card", "id": "c1"}]) == "primary"
    )
    assert (
        classify_fact_support([{"type": "graph_fact", "id": "gf_1"}])
        == "derived_only"
    )
    assert (
        classify_fact_support([{"type": "message", "id": "   "}]) == "invalid_only"
    )
    # multi-evidence: primary wins over derived/invalid
    assert (
        classify_fact_support(
            [
                {"type": "graph_fact", "id": "gf_1"},
                {"type": "message", "id": "   "},
                {"type": "doc_chunk", "id": "ch1"},
            ]
        )
        == "primary"
    )
    # derived beats invalid when no primary
    assert (
        classify_fact_support(
            [
                {"type": "message", "id": "   "},
                {"type": "graph_fact", "id": "gf_2"},
            ]
        )
        == "derived_only"
    )


def test_classify_evidence_type_bucket_closed() -> None:
    for known in (
        "memory_card",
        "doc_chunk",
        "message",
        "evidence",
        "fixture",
        "observation",
        "episode",
        "graph_fact",
    ):
        assert classify_evidence_type_bucket(known) == known
    assert classify_evidence_type_bucket("") == "empty"
    assert classify_evidence_type_bucket(None) == "empty"
    assert classify_evidence_type_bucket("   ") == "empty"
    # attacker / custom label must not become a metric key
    assert classify_evidence_type_bucket("evil_custom") == "other"
    assert classify_evidence_type_bucket("drop_table;--") == "other"
    assert set(EVIDENCE_TYPE_BUCKETS) == {
        "memory_card",
        "doc_chunk",
        "message",
        "evidence",
        "fixture",
        "observation",
        "episode",
        "graph_fact",
        "other",
        "empty",
    }


def test_classify_pending_evidence_quality() -> None:
    assert classify_pending_evidence_quality(None) == "missing"
    assert classify_pending_evidence_quality({}) == "missing"
    assert classify_pending_evidence_quality("") == "missing"
    assert (
        classify_pending_evidence_quality({"type": "graph_fact", "id": "gf_p"})
        == "derived"
    )
    assert (
        classify_pending_evidence_quality({"type": "message", "id": "m1"})
        == "primary"
    )
    # malformed JSON string
    assert classify_pending_evidence_quality("{not-json") == "invalid"
    # valid JSON but wrong shape
    assert classify_pending_evidence_quality('["not", "mapping"]') == "invalid"
    assert classify_pending_evidence_quality({"type": "message", "id": "  "}) == "invalid"
    # JSON object string with primary
    assert (
        classify_pending_evidence_quality(
            json.dumps({"type": "fixture", "id": "fx1"})
        )
        == "primary"
    )


def test_classify_pending_gate_code_closed_and_secret_free() -> None:
    assert classify_pending_gate_code("") is None
    assert classify_pending_gate_code(None) is None
    assert classify_pending_gate_code("approved") is None
    assert classify_pending_gate_code("provenance_gate:missing_id") == "missing_id"
    assert classify_pending_gate_code("provenance_gate:empty_evidence") == "empty_evidence"
    # legacy code still closed
    assert classify_pending_gate_code("provenance_gate:missing_type") == "missing_type"
    secret = "provenance_gate:user_token_sk-abcSECRET123"
    assert classify_pending_gate_code(secret) == "other"
    # known set must include legacy missing_type and current gpg codes
    for code in (
        "empty_evidence",
        "invalid_shape",
        "whitespace_id",
        "conflicting_ids",
        "conflicting_types",
        "missing_id",
        "invalid_type",
        "graph_fact_primary",
        "invalid_id_scalar",
        "missing_type",
    ):
        assert code in KNOWN_PROVENANCE_GATE_CODES
    assert "other" not in KNOWN_PROVENANCE_GATE_CODES  # other is fallback label


def test_empty_observability_payload_shape() -> None:
    payload = empty_observability_payload(provenance_gate_enabled=True)
    assert payload["version"] == "gpo_v1"
    assert payload["enabled"] is True
    assert payload["provenance_gate_enabled"] is True
    assert set(payload["active_fact_support"]) == set(FACT_SUPPORT_BUCKETS)
    assert set(payload["active_evidence_rows"]) == {"primary", "derived", "invalid"}
    assert set(payload["active_evidence_type_histogram"]) == set(EVIDENCE_TYPE_BUCKETS)
    assert set(payload["pending_evidence_quality"]) == set(PENDING_QUALITY_BUCKETS)
    assert set(payload["pending_gate_codes"]) == set(KNOWN_PROVENANCE_GATE_CODES) | {
        "other"
    }
    for section in (
        "active_fact_support",
        "active_evidence_rows",
        "active_evidence_type_histogram",
        "pending_evidence_quality",
        "pending_gate_codes",
    ):
        assert all(isinstance(v, int) and v == 0 for v in payload[section].values())


# ---------------------------------------------------------------------------
# Store / service snapshot
# ---------------------------------------------------------------------------

PRE_GPO_KEYS = frozenset(
    {
        "available",
        "checked_at",
        "since",
        "candidate_24h",
        "candidate_total",
        "facts_active_by_source",
        "facts_active_24h",
        "edges_24h",
    }
)


async def _open_service(
    tmp_path: Path,
    *,
    observability_enabled: bool = True,
    provenance_gate_enabled: bool = True,
) -> KnowledgeGraphService:
    graph = KnowledgeGraphService(
        tmp_path / f"gpo_{secrets.token_hex(4)}.db",
        provenance_gate_enabled=provenance_gate_enabled,
        observability_enabled=observability_enabled,
    )
    await graph.init()
    return graph


async def _seed_evidence_row(
    store: KnowledgeGraphStore,
    fact_id: str,
    *,
    evidence_type: str,
    evidence_id: str,
) -> None:
    """Insert a graph_evidence row bypassing write gate (read-path fixtures)."""
    db = store._require_db()
    from services.knowledge_graph.store import _now_iso

    await db.execute(
        "INSERT INTO graph_evidence "
        "(evidence_row_id, fact_id, evidence_type, evidence_id, quote, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "ge_" + secrets.token_hex(6),
            fact_id,
            evidence_type,
            evidence_id,
            "",
            _now_iso(),
        ),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_health_snapshot_empty_db_zeros(tmp_path: Path) -> None:
    graph = await _open_service(tmp_path)
    try:
        snap = await graph.health_snapshot()
        assert snap["available"] is True
        assert set(snap.keys()) >= PRE_GPO_KEYS
        assert snap["candidate_24h"] == {}
        assert snap["candidate_total"] == {}
        assert snap["facts_active_by_source"] == {}
        assert snap["facts_active_24h"] == 0
        assert snap["edges_24h"] == {}
        obs = snap["observability"]
        assert obs["version"] == "gpo_v1"
        assert obs["enabled"] is True
        assert all(v == 0 for v in obs["active_fact_support"].values())
        assert all(v == 0 for v in obs["active_evidence_rows"].values())
        assert all(v == 0 for v in obs["active_evidence_type_histogram"].values())
        assert all(v == 0 for v in obs["pending_evidence_quality"].values())
        assert all(v == 0 for v in obs["pending_gate_codes"].values())
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_health_snapshot_active_fact_support_buckets(tmp_path: Path) -> None:
    graph = await _open_service(tmp_path)
    try:
        primary = await graph.submit_fact_candidate(
            subject="A",
            predicate="likes",
            object="B",
            confidence=0.9,
            source="test",
            evidence={"type": "memory_card", "id": "card_p"},
            promote_directly=True,
        )
        assert primary is not None

        # derived-only / invalid-only / none via raw SQL (bypass write gate)
        db = graph._store._require_db()
        from services.knowledge_graph.store import _now_iso

        now = _now_iso()
        await db.execute(
            "INSERT INTO graph_facts "
            "(fact_id, subject, predicate, object, confidence, status, "
            "scope, scope_id, source, supersedes, metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', 'global', 'global', 'test', NULL, '{}', ?, ?)",
            ("gf_derived_only", "E", "rel", "F", 0.7, now, now),
        )
        await db.commit()
        await _seed_evidence_row(
            graph._store,
            "gf_derived_only",
            evidence_type="graph_fact",
            evidence_id="gf_other",
        )

        # invalid-only fact
        await db.execute(
            "INSERT INTO graph_facts "
            "(fact_id, subject, predicate, object, confidence, status, "
            "scope, scope_id, source, supersedes, metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', 'global', 'global', 'test', NULL, '{}', ?, ?)",
            ("gf_invalid_only", "G", "rel", "H", 0.6, now, now),
        )
        await db.commit()
        await _seed_evidence_row(
            graph._store,
            "gf_invalid_only",
            evidence_type="message",
            evidence_id="   ",
        )

        # no-evidence fact
        await db.execute(
            "INSERT INTO graph_facts "
            "(fact_id, subject, predicate, object, confidence, status, "
            "scope, scope_id, source, supersedes, metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', 'global', 'global', 'test', NULL, '{}', ?, ?)",
            ("gf_none", "I", "rel", "J", 0.5, now, now),
        )
        await db.commit()

        # multi-evidence priority fact: invalid + derived + primary → primary
        multi = await graph.submit_fact_candidate(
            subject="K",
            predicate="owns",
            object="L",
            confidence=0.9,
            source="test",
            evidence={"type": "doc_chunk", "id": "chunk_m"},
            promote_directly=True,
        )
        assert isinstance(multi, GraphFact)
        await _seed_evidence_row(
            graph._store,
            multi.fact_id,
            evidence_type="graph_fact",
            evidence_id="gf_m",
        )
        await _seed_evidence_row(
            graph._store,
            multi.fact_id,
            evidence_type="message",
            evidence_id="  ",
        )

        # unknown custom type still primary (non-derived) but type hist → other
        # attacker_label is a valid non-derived type token — write must succeed.
        custom = await graph.submit_fact_candidate(
            subject="M",
            predicate="tag",
            object="N",
            confidence=0.9,
            source="test",
            evidence={"type": "attacker_label", "id": "atk1"},
            promote_directly=True,
        )
        assert custom is not None

        snap = await graph.health_snapshot()
        obs = snap["observability"]
        # Controlled fixture: primary=3, derived_only=1, invalid_only=1, none=1
        support = obs["active_fact_support"]
        assert support == {
            "primary": 3,
            "derived_only": 1,
            "invalid_only": 1,
            "none": 1,
        }

        rows = obs["active_evidence_rows"]
        assert rows == {
            "primary": 3,
            "derived": 2,
            "invalid": 2,
        }

        hist = obs["active_evidence_type_histogram"]
        assert hist == {
            "memory_card": 1,
            "doc_chunk": 1,
            "message": 2,
            "evidence": 0,
            "fixture": 0,
            "observation": 0,
            "episode": 0,
            "graph_fact": 2,
            "other": 1,
            "empty": 0,
        }
        assert "attacker_label" not in hist
        assert set(hist) == set(EVIDENCE_TYPE_BUCKETS)
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_health_snapshot_pending_quality_and_gate_codes(tmp_path: Path) -> None:
    graph = await _open_service(tmp_path, provenance_gate_enabled=False)
    try:
        # empty {}
        await graph._store.add_candidate(
            subject="p1",
            predicate="r",
            object="o1",
            confidence=0.7,
            source="test",
            evidence={},
            status="pending",
        )
        # derived graph_fact pending (gate off accepts)
        await graph._store.add_candidate(
            subject="p2",
            predicate="r",
            object="o2",
            confidence=0.7,
            source="test",
            evidence={"type": "graph_fact", "id": "gf_p"},
            status="pending",
        )
        # valid primary
        await graph._store.add_candidate(
            subject="p3",
            predicate="r",
            object="o3",
            confidence=0.7,
            source="test",
            evidence={"type": "message", "id": "m_ok"},
            status="pending",
        )

        db = graph._store._require_db()
        from services.knowledge_graph.store import _now_iso

        now = _now_iso()
        # malformed JSON evidence
        await db.execute(
            "INSERT INTO extraction_candidates "
            "(candidate_id, subject, predicate, object, confidence, status, "
            "scope, scope_id, source, evidence_json, review_note, created_at, updated_at) "
            "VALUES (?, 'ps', 'r', 'po', 0.7, 'pending', 'global', 'global', 'test', "
            "?, '', ?, ?)",
            ("gc_bad_json", "{not-json", now, now),
        )
        # known gate code
        await db.execute(
            "INSERT INTO extraction_candidates "
            "(candidate_id, subject, predicate, object, confidence, status, "
            "scope, scope_id, source, evidence_json, review_note, created_at, updated_at) "
            "VALUES (?, 'ps2', 'r', 'po2', 0.7, 'pending', 'global', 'global', 'test', "
            "'{}', ?, ?, ?)",
            ("gc_gate_known", "provenance_gate:missing_id", now, now),
        )
        secret_note = "provenance_gate:sk-live-SECRET_payload_xyz"
        await db.execute(
            "INSERT INTO extraction_candidates "
            "(candidate_id, subject, predicate, object, confidence, status, "
            "scope, scope_id, source, evidence_json, review_note, created_at, updated_at) "
            "VALUES (?, 'ps3', 'r', 'po3', 0.7, 'pending', 'global', 'global', 'test', "
            "'{}', ?, ?, ?)",
            ("gc_gate_secret", secret_note, now, now),
        )
        # approved / rejected with provenance_gate notes must not affect pending obs
        await db.execute(
            "INSERT INTO extraction_candidates "
            "(candidate_id, subject, predicate, object, confidence, status, "
            "scope, scope_id, source, evidence_json, review_note, created_at, updated_at) "
            "VALUES (?, 'pa', 'r', 'oa', 0.7, 'approved', 'global', 'global', 'test', "
            "?, ?, ?, ?)",
            (
                "gc_approved_gate",
                json.dumps({"type": "message", "id": "m_approved"}),
                "provenance_gate:missing_id",
                now,
                now,
            ),
        )
        await db.execute(
            "INSERT INTO extraction_candidates "
            "(candidate_id, subject, predicate, object, confidence, status, "
            "scope, scope_id, source, evidence_json, review_note, created_at, updated_at) "
            "VALUES (?, 'pr', 'r', 'or', 0.7, 'rejected', 'global', 'global', 'test', "
            "?, ?, ?, ?)",
            (
                "gc_rejected_gate",
                json.dumps({"type": "doc_chunk", "id": "c_rejected"}),
                "provenance_gate:empty_evidence",
                now,
                now,
            ),
        )
        await db.commit()

        snap = await graph.health_snapshot()
        obs = snap["observability"]
        pq = obs["pending_evidence_quality"]
        assert pq == {
            "missing": 3,
            "derived": 1,
            "primary": 1,
            "invalid": 1,
        }

        codes = obs["pending_gate_codes"]
        expected_codes = {code: 0 for code in KNOWN_PROVENANCE_GATE_CODES}
        expected_codes["other"] = 1
        expected_codes["missing_id"] = 1
        assert codes == expected_codes
        # secret must not appear as key or anywhere in payload
        blob = json.dumps(snap, ensure_ascii=False)
        assert "sk-live-SECRET" not in blob
        assert "SECRET_payload" not in blob
        assert secret_note not in blob
        for key in codes:
            assert key == "other" or key in KNOWN_PROVENANCE_GATE_CODES
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_health_snapshot_kill_switch_exact_old_keys(tmp_path: Path) -> None:
    graph = await _open_service(tmp_path, observability_enabled=False)
    try:
        # Seed active + pending rows so a mistaken full quality scan would query them.
        await graph.submit_fact_candidate(
            subject="KS",
            predicate="likes",
            object="KT",
            confidence=0.9,
            source="test",
            evidence={"type": "fixture", "id": "ks_fx"},
            promote_directly=True,
        )
        await graph._store.add_candidate(
            subject="ks_p",
            predicate="r",
            object="ks_o",
            confidence=0.7,
            source="test",
            evidence={"type": "message", "id": "ks_m"},
            status="pending",
        )

        called: list[str] = []
        evidence_calls: list[list[str]] = []
        import services.knowledge_graph.observability as obs_mod

        orig = obs_mod.classify_fact_support
        orig_list = graph._store.list_evidence_for_facts

        def _spy(*args: Any, **kwargs: Any) -> str:
            called.append("scan")
            return orig(*args, **kwargs)

        async def _list_spy(fact_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
            evidence_calls.append(list(fact_ids))
            return await orig_list(fact_ids)

        sql_trace: list[str] = []
        db = graph._store._require_db()

        def _trace(statement: str) -> None:
            sql_trace.append(statement)

        await db.set_trace_callback(_trace)
        obs_mod.classify_fact_support = _spy  # type: ignore[assignment]
        graph._store.list_evidence_for_facts = _list_spy  # type: ignore[method-assign]
        try:
            snap = await graph.health_snapshot()
        finally:
            obs_mod.classify_fact_support = orig  # type: ignore[assignment]
            graph._store.list_evidence_for_facts = orig_list  # type: ignore[method-assign]
            # aiosqlite types handler as Callable; sqlite accepts None to clear.
            await db.set_trace_callback(lambda _statement: None)

        assert set(snap.keys()) == PRE_GPO_KEYS
        assert "observability" not in snap
        assert snap["available"] is True
        assert called == [], "quality classification must not run when disabled"
        assert evidence_calls == [], "list_evidence_for_facts must never be awaited when disabled"

        quality_sql = [
            s
            for s in sql_trace
            if (
                "graph_evidence" in s.lower()
                or (
                    "extraction_candidates" in s.lower()
                    and "evidence_json" in s.lower()
                )
                or (
                    "graph_facts" in s.lower()
                    and "status='active'" in s.lower().replace(" ", "")
                    and "source" not in s.lower()
                    and "count" not in s.lower()
                )
            )
        ]
        assert quality_sql == [], (
            "quality-only pending/fact SQL must not run when observability_enabled=false: "
            f"{quality_sql!r}"
        )
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_health_snapshot_deterministic_and_read_only(tmp_path: Path) -> None:
    graph = await _open_service(tmp_path)
    try:
        await graph.submit_fact_candidate(
            subject="X",
            predicate="likes",
            object="Y",
            confidence=0.9,
            source="test",
            evidence={"type": "fixture", "id": "fx"},
            promote_directly=True,
        )
        db = graph._store._require_db()

        async def _count(sql: str) -> int:
            cursor = await db.execute(sql)
            row = await cursor.fetchone()
            assert row is not None
            return int(row[0])

        tables_before = await _count(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        )
        facts_before = await _count("SELECT COUNT(*) FROM graph_facts")
        ev_before = await _count("SELECT COUNT(*) FROM graph_evidence")
        cand_before = await _count("SELECT COUNT(*) FROM extraction_candidates")

        s1 = await graph.health_snapshot()
        s2 = await graph.health_snapshot()
        for s in (s1, s2):
            s.pop("checked_at", None)
            s.pop("since", None)
        assert s1 == s2

        assert (
            await _count("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
            == tables_before
        )
        assert await _count("SELECT COUNT(*) FROM graph_facts") == facts_before
        assert await _count("SELECT COUNT(*) FROM graph_evidence") == ev_before
        assert await _count("SELECT COUNT(*) FROM extraction_candidates") == cand_before
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_store_health_snapshot_unavailable_when_closed(tmp_path: Path) -> None:
    store = KnowledgeGraphStore(
        tmp_path / "closed.db",
        provenance_gate_enabled=True,
        observability_enabled=True,
    )
    # not initialized
    snap = await store.health_snapshot()
    assert snap == {"available": False}


# ---------------------------------------------------------------------------
# Admin route / config / bootstrap
# ---------------------------------------------------------------------------


def test_admin_graph_health_uses_service_public_method_not_private_db() -> None:
    src_path = Path("admin/routes/api/knowledge.py")
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    health_fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "create_knowledge_router":
            for child in ast.walk(node):
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == "graph_health"
                ):
                    health_fn = child
                    break
    assert health_fn is not None, "graph_health must exist"
    health_src = ast.get_source_segment(source, health_fn) or ""
    assert "._db" not in health_src
    assert "health_snapshot" in health_src

    # runtime: fake service, no store
    class _Graph:
        async def health_snapshot(self):
            return {
                "available": True,
                "checked_at": "t",
                "since": "s",
                "candidate_24h": {},
                "candidate_total": {},
                "facts_active_by_source": {},
                "facts_active_24h": 0,
                "edges_24h": {},
                "observability": empty_observability_payload(
                    provenance_gate_enabled=True
                ),
            }

    from admin.routes.api.knowledge import create_knowledge_router

    app = FastAPI()
    app.include_router(
        create_knowledge_router(ctx=SimpleNamespace(knowledge_graph=_Graph()))
    )
    client = TestClient(app)
    resp = client.get("/knowledge/graph/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["observability"]["version"] == "gpo_v1"


def test_admin_graph_health_fail_closed_missing_service() -> None:
    from admin.routes.api.knowledge import create_knowledge_router

    app = FastAPI()
    app.include_router(create_knowledge_router(ctx=SimpleNamespace()))
    client = TestClient(app)
    resp = client.get("/knowledge/graph/health")
    assert resp.status_code == 200
    assert resp.json() == {"available": False}


def test_admin_graph_health_legacy_fake_without_snapshot_fails_closed() -> None:
    class _Legacy:
        pass

    from admin.routes.api.knowledge import create_knowledge_router

    app = FastAPI()
    app.include_router(
        create_knowledge_router(ctx=SimpleNamespace(knowledge_graph=_Legacy()))
    )
    client = TestClient(app)
    resp = client.get("/knowledge/graph/health")
    assert resp.status_code == 200
    assert resp.json() == {"available": False}


def test_admin_graph_health_snapshot_error_type_only_no_secret_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Public health_snapshot failures must not leak exception messages."""
    import logging

    secret_path = "/secret/path/to/token_sk-live-LEAKED_SECRET.db"
    secret_msg = f"boom open failed at {secret_path}"

    class _BoomGraph:
        async def health_snapshot(self):
            raise RuntimeError(secret_msg)

    from admin.routes.api.knowledge import create_knowledge_router

    app = FastAPI()
    app.include_router(
        create_knowledge_router(ctx=SimpleNamespace(knowledge_graph=_BoomGraph()))
    )
    client = TestClient(app)
    with caplog.at_level(logging.WARNING):
        resp = client.get("/knowledge/graph/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "available": False,
        "error": "health_snapshot_failed:RuntimeError",
    }
    blob = json.dumps(body, ensure_ascii=False)
    assert secret_path not in blob
    assert "sk-live-LEAKED_SECRET" not in blob
    assert "boom open failed" not in blob
    assert "LEAKED_SECRET" not in blob
    # logs must carry only the exception type token, not the message/path
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in joined
    assert secret_path not in joined
    assert "sk-live-LEAKED_SECRET" not in joined
    assert secret_msg not in joined


def test_admin_graph_health_cancelled_error_propagates() -> None:
    """CancelledError must not be converted to unavailable diagnostics.

    Starlette TestClient may re-raise as concurrent.futures.CancelledError
    after the route lets asyncio.CancelledError bubble (BaseException).
    """
    import asyncio
    import concurrent.futures

    class _CancelGraph:
        async def health_snapshot(self):
            raise asyncio.CancelledError()

    from admin.routes.api.knowledge import create_knowledge_router

    app = FastAPI()
    app.include_router(
        create_knowledge_router(ctx=SimpleNamespace(knowledge_graph=_CancelGraph()))
    )
    client = TestClient(app, raise_server_exceptions=True)
    with pytest.raises((asyncio.CancelledError, concurrent.futures.CancelledError)):
        client.get("/knowledge/graph/health")


def test_bot_config_observability_default_enabled() -> None:
    cfg = BotConfig()
    assert cfg.knowledge_graph.observability_enabled is True
    off = BotConfig(
        knowledge_graph=KnowledgeGraphConfig(observability_enabled=False)
    )
    assert off.knowledge_graph.observability_enabled is False


def test_knowledge_graph_service_accepts_observability_flag(tmp_path: Path) -> None:
    g_on = KnowledgeGraphService(
        tmp_path / "a.db", observability_enabled=True
    )
    assert g_on.observability_enabled is True
    g_off = KnowledgeGraphService(
        tmp_path / "b.db", observability_enabled=False
    )
    assert g_off.observability_enabled is False


def test_bootstrap_passes_observability_enabled() -> None:
    src = Path("bootstrap/chat_runtime.py").read_text(encoding="utf-8")
    assert "observability_enabled" in src
    assert "KnowledgeGraphService" in src


def test_closed_bucket_constants_stable() -> None:
    assert ROW_QUALITY_BUCKETS == (
        "primary",
        "derived",
        "invalid",
        "missing",
    )
    assert FACT_SUPPORT_BUCKETS == (
        "primary",
        "derived_only",
        "invalid_only",
        "none",
    )
    assert "missing" in PENDING_QUALITY_BUCKETS
