"""Read-only Worldbook admin API tests — no side effects, sanitized snapshot."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.worldbook import (
    FORBIDDEN_RESPONSE_MARKERS,
    create_worldbook_router,
)
from services.block_trace.types import PromptBlockTrace
from services.worldbook.config import WorldbookConfig
from services.worldbook.domain import (
    EventProposal,
    FictionCommitRecord,
    LifeState,
    LifeStateItem,
    ProposalDecision,
    SourceMeta,
    StoryLedgerView,
    utc_now_iso,
)
from services.worldbook.runtime import WorldbookRuntime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL_RAW_PROPOSAL = "RAW_PROPOSAL_PAYLOAD_SHOULD_NOT_LEAK_xyz"
_SENTINEL_SOCIAL = "USER_TEXT_SOCIAL_NARRATIVE_SHOULD_NOT_LEAK_abc"
_SENTINEL_STORYLET = "STORYLET_BODY_TEXT_SHOULD_NOT_LEAK_def"
_SENTINEL_CANON = "CANON_BODY_TEXT_SHOULD_NOT_LEAK_ghi"


def _app_with_ctx(ctx: Any) -> TestClient:
    app = FastAPI()
    app.include_router(create_worldbook_router(ctx=ctx))
    return TestClient(app)


def _collect_routes(router: Any) -> list[tuple[str, frozenset[str]]]:
    out: list[tuple[str, frozenset[str]]] = []
    for route in router.routes:
        methods = frozenset(getattr(route, "methods", set()) or set())
        path = str(getattr(route, "path", "") or "")
        out.append((path, methods))
    return out


def _walk_strings(obj: Any) -> list[str]:
    found: list[str] = []
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found.append(str(k))
            found.extend(_walk_strings(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found.extend(_walk_strings(item))
    return found


def _tree_fingerprint(root: Path) -> dict[str, tuple[int, float]]:
    """Map relative path → (size, mtime_ns) for every file under root."""
    out: dict[str, tuple[int, float]] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        st = path.stat()
        rel = str(path.relative_to(root))
        out[rel] = (st.st_size, st.st_mtime_ns)
    return out


# ---------------------------------------------------------------------------
# No runtime / disabled — no side effects, stable payload
# ---------------------------------------------------------------------------


def test_snapshot_no_runtime_stable_and_no_side_effect(tmp_path: Path) -> None:
    marker = tmp_path / "should_not_be_created"
    ctx = SimpleNamespace(worldbook_runtime=None, worldbook_config=None)
    client = _app_with_ctx(ctx)
    before = list(tmp_path.iterdir()) if tmp_path.exists() else []

    resp = client.get("/worldbook/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["available"] is False
    assert body["reason"] == "runtime_not_mounted"
    assert body["gates"] is None
    assert body["registry"] is None
    assert body["ledger"] is None
    assert body["life"] is None
    assert body["lifecycle"] is None
    assert body["block_traces"] == []
    assert body["shadow"]["status"] == "skipped"

    # Second call identical
    resp2 = client.get("/worldbook/snapshot")
    assert resp2.json() == body

    assert not marker.exists()
    after = list(tmp_path.iterdir()) if tmp_path.exists() else []
    assert after == before


def test_snapshot_disabled_config_no_ensure_loaded_no_io(tmp_path: Path) -> None:
    """Runtime present but config.enabled=false → unavailable, no ensure_loaded."""
    calls: list[str] = []

    class _DisabledRuntime:
        def __init__(self) -> None:
            self.config = WorldbookConfig(enabled=False, state_dir=str(tmp_path / "wb"))
            self._loaded = False
            self._io_performed = False

        def ensure_loaded(self) -> None:
            calls.append("ensure_loaded")
            self._loaded = True
            self._io_performed = True

        @property
        def life_store(self) -> Any:
            calls.append("life_store")
            raise AssertionError("must not touch life_store when disabled")

        @property
        def proposal_store(self) -> Any:
            calls.append("proposal_store")
            raise AssertionError("must not touch proposal_store when disabled")

        @property
        def ledger(self) -> Any:
            calls.append("ledger")
            raise AssertionError("must not touch ledger when disabled")

    runtime = _DisabledRuntime()
    ctx = SimpleNamespace(worldbook_runtime=runtime)
    client = _app_with_ctx(ctx)
    state_dir = tmp_path / "wb"
    assert not state_dir.exists()

    resp = client.get("/worldbook/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["available"] is False
    assert body["reason"] == "worldbook_disabled"
    assert calls == []
    assert not state_dir.exists()
    assert runtime._loaded is False
    assert runtime._io_performed is False


def test_snapshot_missing_ctx_same_as_no_runtime() -> None:
    client = _app_with_ctx(None)
    resp = client.get("/worldbook/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "runtime_not_mounted"


# ---------------------------------------------------------------------------
# Enabled fake/real runtime — truthful snapshot
# ---------------------------------------------------------------------------


class _FakeBlockTraceStore:
    def __init__(self, traces: list[PromptBlockTrace]) -> None:
        self._traces = traces
        self.recent_calls: list[int] = []

    async def recent(self, limit: int = 50) -> list[PromptBlockTrace]:
        self.recent_calls.append(limit)
        return list(self._traces)[:limit]


class _FakeLifeStore:
    def __init__(self, state: LifeState) -> None:
        self._state = state
        self.load_calls = 0

    def load(self) -> LifeState:
        self.load_calls += 1
        return self._state


class _FakeProposalStore:
    def __init__(self, items: list[EventProposal]) -> None:
        self._items = items

    def list_proposals(self) -> list[EventProposal]:
        return list(self._items)


class _FakeDecisionStore:
    def __init__(self, items: list[ProposalDecision]) -> None:
        self._items = items

    def list_decisions(self) -> list[ProposalDecision]:
        return list(self._items)


class _FakeCommitStore:
    def __init__(self, items: list[FictionCommitRecord]) -> None:
        self._items = items

    def list_commits(self) -> list[FictionCommitRecord]:
        return list(self._items)


class _FakeLedger:
    def __init__(self, view: StoryLedgerView) -> None:
        self._view = view
        self.calls = 0

    def load_stack(self, **_kwargs: Any) -> StoryLedgerView:
        self.calls += 1
        return self._view


class _FakeCanon:
    loaded = True

    def list_entries(self) -> list[Any]:
        return [
            SimpleNamespace(
                entry_id="place.stage",
                title="Stage",
                text=_SENTINEL_CANON,
                priority=10,
                always_active=True,
                keywords=("a", "b"),
                aliases=("x",),
            )
        ]


class _FakeStorylets:
    _loaded = True
    loaded = True

    def list_storylets(self) -> list[Any]:
        return [
            SimpleNamespace(
                storylet_id="s1",
                title="Meet",
                text=_SENTINEL_STORYLET,
                severity="daily",
                priority=5,
                saliency=1.0,
                target_arc_id="arc.main",
                once=False,
            )
        ]


class _FakeEnabledRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.config = WorldbookConfig(
            enabled=True,
            chat_projection_enabled=True,
            storylet_enabled=True,
            dream_proposal_enabled=True,
            social_evidence_enabled=True,
            social_group_allowlist=["984198159", "111"],
            state_dir=str(tmp_path / "storage" / "worldbook"),
            canon_dir=str(tmp_path / "config" / "worldbook" / "canon"),
            storylet_dir=str(tmp_path / "config" / "worldbook" / "storylets"),
        )
        self._root = tmp_path
        self._paths = {
            "state_dir": tmp_path / "storage" / "worldbook",
            "canon_dir": tmp_path / "config" / "worldbook" / "canon",
            "storylet_dir": tmp_path / "config" / "worldbook" / "storylets",
        }
        self._loaded = True
        self._canon = _FakeCanon()
        self._storylets = _FakeStorylets()
        now = datetime.now(UTC)
        active_decay = (now + timedelta(hours=6)).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
        expired_decay = (now - timedelta(hours=1)).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
        self._life_state = LifeState(
            revision=3,
            updated_at=utc_now_iso(),
            applied_event_ids=["evt.a", "evt.b"],
            items={
                "mood.active": LifeStateItem(
                    key="mood.active",
                    value="calm",
                    meta=SourceMeta(
                        source="life_state",
                        scope="self",
                        confidence="medium",
                        privacy="private",
                        updated_at=utc_now_iso(),
                        decay_at=active_decay,
                        revision=1,
                        evidence_refs=("ev1",),
                    ),
                ),
                "mood.expired": LifeStateItem(
                    key="mood.expired",
                    value="stale",
                    meta=SourceMeta(
                        source="life_state",
                        scope="self",
                        confidence="low",
                        privacy="private",
                        updated_at=utc_now_iso(),
                        decay_at=expired_decay,
                        revision=1,
                    ),
                ),
            },
        )
        self._life = _FakeLifeStore(self._life_state)
        self._proposals = _FakeProposalStore(
            [
                EventProposal(
                    proposal_id="prop.1",
                    kind="fiction_event",
                    summary="reflect gently",
                    arc_id="arc.main",
                    payload={
                        "secret_field": _SENTINEL_RAW_PROPOSAL,
                        "user_text": _SENTINEL_SOCIAL,
                    },
                    created_at=utc_now_iso(),
                    source="dream_proposal",
                )
            ]
        )
        self._decisions = _FakeDecisionStore(
            [
                ProposalDecision(
                    decision_id="dec.1",
                    proposal_id="prop.1",
                    status="validated",
                    reason_code="ok",
                    reason="fine",
                    arc_id="arc.main",
                    proposal_fingerprint="abc123",
                    normalized_payload={
                        "user_text": _SENTINEL_SOCIAL,
                        "raw": _SENTINEL_RAW_PROPOSAL,
                    },
                )
            ]
        )
        self._commits = _FakeCommitStore(
            [
                FictionCommitRecord(
                    commit_id="cmt.1",
                    proposal_id="prop.1",
                    decision_id="dec.1",
                    event_id="evt.1",
                    arc_id="arc.main",
                )
            ]
        )
        self._ledger = _FakeLedger(
            StoryLedgerView(
                main={
                    "arc_id": "arc.main",
                    "title": "Main Arc",
                    "stage": "active",
                    "status": "active",
                    "arc_role": "main",
                    "stack_order": 0,
                    "goals": ["secret goal text"],
                    "open_threads": ["thread body"],
                },
                sides=(
                    {
                        "arc_id": "arc.side",
                        "title": "Side",
                        "stage": "active",
                        "status": "active",
                        "arc_role": "side",
                        "stack_order": 1,
                        "goals": [],
                        "open_threads": [],
                    },
                ),
                ambient=(
                    {
                        "arc_id": "arc.amb",
                        "title": "Ambient",
                        "stage": "active",
                        "status": "active",
                        "arc_role": "ambient",
                        "stack_order": 2,
                        "goals": [],
                        "open_threads": [],
                    },
                ),
                ordering=("arc.main", "arc.side", "arc.amb"),
            )
        )

    @property
    def canon_registry(self) -> Any:
        return self._canon

    @property
    def storylet_registry(self) -> Any:
        return self._storylets

    @property
    def life_store(self) -> Any:
        return self._life

    @property
    def proposal_store(self) -> Any:
        return self._proposals

    @property
    def decision_store(self) -> Any:
        return self._decisions

    @property
    def commit_store(self) -> Any:
        return self._commits

    @property
    def ledger(self) -> Any:
        return self._ledger


def test_snapshot_enabled_fake_runtime_truthful(tmp_path: Path) -> None:
    runtime = _FakeEnabledRuntime(tmp_path)
    traces = [
        PromptBlockTrace(
            trace_id="bt_wb1",
            request_id="r1",
            task="chat",
            source="worldbook",
            provider="worldbook",
            candidate_id="c1",
            decision="accepted",
            hit_reason="canon",
            evidence_refs=(),
            token_estimate=10,
            char_count=20,
            position="dynamic",
            label="World",
            created_at=utc_now_iso(),
        ),
        PromptBlockTrace(
            trace_id="bt_other",
            request_id="r2",
            task="chat",
            source="memory",
            provider="memory_provider",
            candidate_id="c2",
            decision="accepted",
            hit_reason="hit",
            evidence_refs=(),
            token_estimate=5,
            char_count=5,
            position="dynamic",
            label="Mem",
            created_at=utc_now_iso(),
        ),
        PromptBlockTrace(
            trace_id="bt_wb2",
            request_id="r3",
            task="chat",
            source="context",
            provider="worldbook",
            candidate_id="c3",
            decision="trimmed",
            hit_reason="budget",
            evidence_refs=(),
            token_estimate=1,
            char_count=1,
            position="dynamic",
            label="WB",
            created_at=utc_now_iso(),
        ),
    ]
    store = _FakeBlockTraceStore(traces)
    ctx = SimpleNamespace(worldbook_runtime=runtime, block_trace_store=store)
    client = _app_with_ctx(ctx)

    resp = client.get("/worldbook/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["available"] is True
    assert body["reason"] == "ok"

    gates = body["gates"]
    assert gates["enabled"] is True
    assert gates["chat_projection_enabled"] is True
    assert gates["allowlist_count"] == 2
    # Must not leak allowlist group ids
    blob = json.dumps(body, ensure_ascii=False)
    assert "984198159" not in blob

    reg = body["registry"]
    assert reg["runtime_loaded"] is True
    assert reg["canon"]["loaded"] is True
    assert reg["canon"]["count"] == 1
    assert reg["canon"]["entries"][0]["entry_id"] == "place.stage"
    assert reg["storylets"]["count"] == 1
    assert reg["storylets"]["entries"][0]["storylet_id"] == "s1"

    ledger = body["ledger"]
    assert ledger["main"]["arc_id"] == "arc.main"
    assert ledger["main"]["goal_count"] == 1
    assert ledger["side_count"] == 1
    assert ledger["ambient_count"] == 1
    assert "secret goal text" not in blob

    life = body["life"]
    assert life["item_count"] == 2
    assert life["expired_count"] == 1
    assert life["active_count"] == 1
    assert life["revision"] == 3
    assert life["applied_event_id_count"] == 2
    by_key = {row["key"]: row for row in life["items"]}
    assert by_key["mood.expired"]["expired"] is True
    assert by_key["mood.active"]["expired"] is False
    assert by_key["mood.active"]["provenance"]["source"] == "life_state"

    life_cycle = body["lifecycle"]
    assert life_cycle["proposal_count"] == 1
    assert life_cycle["decision_count"] == 1
    assert life_cycle["commit_count"] == 1
    prop = life_cycle["proposals"][0]
    assert prop["proposal_id"] == "prop.1"
    assert "payload" not in prop
    assert prop["payload_key_count"] == 2
    assert "secret_field" in prop["payload_keys"]
    assert _SENTINEL_RAW_PROPOSAL not in blob
    assert _SENTINEL_SOCIAL not in blob
    assert "normalized_payload" not in life_cycle["decisions"][0]
    assert life_cycle["commits"][0]["commit_id"] == "cmt.1"

    # BlockTrace filtered to worldbook source/provider
    assert store.recent_calls == [200]
    ids = {t["trace_id"] for t in body["block_traces"]}
    assert ids == {"bt_wb1", "bt_wb2"}
    assert "bt_other" not in ids

    # Shadow missing by default
    assert body["shadow"]["status"] == "missing"


def test_snapshot_real_runtime_minimal(tmp_path: Path) -> None:
    """Smoke: real WorldbookRuntime with enabled=true (no ensure_loaded forced)."""
    cfg = WorldbookConfig(
        enabled=True,
        state_dir=str(tmp_path / "storage" / "worldbook"),
        canon_dir=str(tmp_path / "config" / "worldbook" / "canon"),
        storylet_dir=str(tmp_path / "config" / "worldbook" / "storylets"),
    )
    (tmp_path / "config" / "worldbook" / "canon").mkdir(parents=True)
    (tmp_path / "config" / "worldbook" / "storylets").mkdir(parents=True)
    runtime = WorldbookRuntime(cfg, root=tmp_path)
    # Do not call ensure_loaded — registries remain unloaded
    ctx = SimpleNamespace(worldbook_runtime=runtime, block_trace_store=None)
    client = _app_with_ctx(ctx)
    resp = client.get("/worldbook/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["gates"]["enabled"] is True
    assert body["registry"]["runtime_loaded"] is False
    assert body["registry"]["canon"]["loaded"] is False
    assert body["registry"]["canon"]["count"] == 0
    assert body["life"]["item_count"] == 0
    assert not (tmp_path / "storage" / "worldbook").exists()


# ---------------------------------------------------------------------------
# Sentinels absent from serialized response
# ---------------------------------------------------------------------------


def test_raw_proposal_and_social_sentinels_absent(tmp_path: Path) -> None:
    runtime = _FakeEnabledRuntime(tmp_path)
    ctx = SimpleNamespace(worldbook_runtime=runtime, block_trace_store=None)
    client = _app_with_ctx(ctx)
    body = client.get("/worldbook/snapshot").json()
    blob = json.dumps(body, ensure_ascii=False)
    for sentinel in (
        _SENTINEL_RAW_PROPOSAL,
        _SENTINEL_SOCIAL,
        _SENTINEL_STORYLET,
        _SENTINEL_CANON,
        "secret goal text",
        "thread body",
    ):
        assert sentinel not in blob
    prop_keys = body["lifecycle"]["proposals"][0]["payload_keys"]
    prop_key_set = set(prop_keys)
    walked = _walk_strings(body)
    for marker in FORBIDDEN_RESPONSE_MARKERS:
        assert marker not in walked
    assert "user_text" not in prop_key_set


# ---------------------------------------------------------------------------
# Life expired computation
# ---------------------------------------------------------------------------


def test_life_expired_computation(tmp_path: Path) -> None:
    runtime = _FakeEnabledRuntime(tmp_path)
    # Add missing-TTL item treated as expired
    runtime._life_state.items["mood.no_ttl"] = LifeStateItem(
        key="mood.no_ttl",
        value="x",
        meta=SourceMeta(
            source="life_state",
            scope="self",
            confidence="medium",
            privacy="private",
            updated_at=utc_now_iso(),
            decay_at=None,
            revision=1,
        ),
    )
    ctx = SimpleNamespace(worldbook_runtime=runtime, block_trace_store=None)
    client = _app_with_ctx(ctx)
    life = client.get("/worldbook/snapshot").json()["life"]
    assert life["item_count"] == 3
    assert life["expired_count"] == 2  # expired + no_ttl
    assert life["active_count"] == 1
    by_key = {r["key"]: r for r in life["items"]}
    assert by_key["mood.no_ttl"]["expired"] is True
    assert by_key["mood.no_ttl"]["decay_at"] is None


# ---------------------------------------------------------------------------
# BlockTrace filtering
# ---------------------------------------------------------------------------


def test_block_trace_filtering_source_or_provider_worldbook(tmp_path: Path) -> None:
    runtime = _FakeEnabledRuntime(tmp_path)
    traces = [
        PromptBlockTrace(
            trace_id="keep_src",
            request_id="1",
            task="chat",
            source="worldbook",
            provider="other",
            candidate_id="a",
            decision="accepted",
            hit_reason="",
            evidence_refs=(),
            token_estimate=0,
            char_count=0,
            position="dynamic",
            label="",
        ),
        PromptBlockTrace(
            trace_id="keep_prov",
            request_id="2",
            task="chat",
            source="other",
            provider="worldbook",
            candidate_id="b",
            decision="accepted",
            hit_reason="",
            evidence_refs=(),
            token_estimate=0,
            char_count=0,
            position="dynamic",
            label="",
        ),
        PromptBlockTrace(
            trace_id="drop",
            request_id="3",
            task="chat",
            source="slang",
            provider="slang_provider",
            candidate_id="c",
            decision="accepted",
            hit_reason="",
            evidence_refs=(),
            token_estimate=0,
            char_count=0,
            position="dynamic",
            label="",
        ),
    ]
    store = _FakeBlockTraceStore(traces)
    ctx = SimpleNamespace(worldbook_runtime=runtime, block_trace_store=store)
    client = _app_with_ctx(ctx)
    body = client.get("/worldbook/snapshot").json()
    ids = {t["trace_id"] for t in body["block_traces"]}
    assert ids == {"keep_src", "keep_prov"}
    assert store.recent_calls == [200]


# ---------------------------------------------------------------------------
# Shadow: missing, corrupt, valid, oversized
# ---------------------------------------------------------------------------


def test_shadow_missing_corrupt_valid_oversized(tmp_path: Path) -> None:
    runtime = _FakeEnabledRuntime(tmp_path)
    state_dir = tmp_path / "storage" / "worldbook"
    state_dir.mkdir(parents=True)
    shadow_path = state_dir / "shadow_report.json"
    ctx = SimpleNamespace(worldbook_runtime=runtime, block_trace_store=None)
    client = _app_with_ctx(ctx)

    # missing
    body = client.get("/worldbook/snapshot").json()
    assert body["shadow"]["status"] == "missing"
    assert body["shadow"]["reason"] == "shadow_report_missing"

    # corrupt JSON
    shadow_path.write_text("{not-json", encoding="utf-8")
    body = client.get("/worldbook/snapshot").json()
    assert body["shadow"]["status"] == "corrupt"
    assert "invalid_json" in body["shadow"]["reason"]
    # never exception text
    blob = json.dumps(body)
    assert "JSONDecodeError" not in blob
    assert "Expecting" not in blob

    # valid
    report = {
        "overall_verdict": "pass",
        "registry": {"canon_count": 2, "storylet_count": 3},
        "steps": [{"day": 1}, {"day": 2}],
        "continuity": {
            "main_arc_id": "arc.main",
            "side_arc_ids": ["arc.side"],
            "ambient_arc_ids": [],
        },
        "social": {"user_text": _SENTINEL_SOCIAL},
        "storylet_observations": {"selected_storylet_ids": ["s1", "s2"]},
        "setback_recovery": {
            "major_setback_count": 1,
            "recovery_observed": True,
        },
        "invariants": {"verdict": "pass"},
        "metadata": {
            "pack_id": "living_story_v1",
            "report_essential_hash": "deadbeef",
        },
    }
    shadow_path.write_text(
        json.dumps(report, ensure_ascii=False),
        encoding="utf-8",
    )
    body = client.get("/worldbook/snapshot").json()
    shadow = body["shadow"]
    assert shadow["status"] == "ok"
    content = shadow["content"]
    assert content["overall_verdict"] == "pass"
    assert content["step_count"] == 2
    assert content["registry_counts"]["canon"] == 2
    assert content["setback_recovery"]["major_setback_count"] == 1
    assert content["selected_storylet_count"] == 2
    assert content["report_essential_hash"] == "deadbeef"
    # social raw not in summary
    blob = json.dumps(body, ensure_ascii=False)
    assert _SENTINEL_SOCIAL not in blob

    # oversized (> 2 MiB)
    shadow_path.write_bytes(b"{" + (b"x" * (2 * 1024 * 1024 + 100)) + b"}")
    body = client.get("/worldbook/snapshot").json()
    assert body["shadow"]["status"] == "oversized"
    assert body["shadow"]["max_bytes"] == 2 * 1024 * 1024
    assert body["shadow"]["size_bytes"] > 2 * 1024 * 1024
    # must not include exception text
    assert "Error" not in json.dumps(body)


# ---------------------------------------------------------------------------
# Revisions / files / mtimes unchanged after GET
# ---------------------------------------------------------------------------


def test_revisions_files_mtimes_unchanged_after_get(tmp_path: Path) -> None:
    """GET must not rewrite state files or bump life revision."""
    cfg = WorldbookConfig(
        enabled=True,
        state_dir=str(tmp_path / "storage" / "worldbook"),
        canon_dir=str(tmp_path / "config" / "worldbook" / "canon"),
        storylet_dir=str(tmp_path / "config" / "worldbook" / "storylets"),
    )
    state_dir = tmp_path / "storage" / "worldbook"
    state_dir.mkdir(parents=True)
    (tmp_path / "config" / "worldbook" / "canon").mkdir(parents=True)
    (tmp_path / "config" / "worldbook" / "storylets").mkdir(parents=True)

    # Seed life_state.json
    life_path = state_dir / "life_state.json"
    seeded = LifeState(
        revision=7,
        updated_at="2026-01-01T00:00:00Z",
        items={
            "k": LifeStateItem(
                key="k",
                value="v",
                meta=SourceMeta(
                    source="life_state",
                    scope="self",
                    confidence="medium",
                    privacy="private",
                    updated_at="2026-01-01T00:00:00Z",
                    decay_at="2099-01-01T00:00:00Z",
                    revision=1,
                ),
            )
        },
    )
    life_path.write_text(
        json.dumps(seeded.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Seed a proposal file (read path only)
    prop_dir = state_dir / "proposals"
    prop_dir.mkdir()
    prop = EventProposal(
        proposal_id="prop.seed",
        kind="reflection",
        summary="seed",
        payload={"x": 1},
        created_at="2026-01-01T00:00:00Z",
    )
    prop_path = prop_dir / "prop.seed.json"
    prop_path.write_text(
        json.dumps(prop.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    runtime = WorldbookRuntime(cfg, root=tmp_path)
    ctx = SimpleNamespace(worldbook_runtime=runtime, block_trace_store=None)
    client = _app_with_ctx(ctx)

    # Stabilize mtimes
    before_fp = _tree_fingerprint(state_dir)
    before_life = json.loads(life_path.read_text(encoding="utf-8"))

    body = client.get("/worldbook/snapshot").json()
    assert body["available"] is True
    assert body["life"]["revision"] == 7

    after_fp = _tree_fingerprint(state_dir)
    after_life = json.loads(life_path.read_text(encoding="utf-8"))

    # Same files and mtimes for pre-existing payload files
    for rel, meta in before_fp.items():
        assert rel in after_fp, f"file disappeared: {rel}"
        assert after_fp[rel] == meta, f"mtime/size changed: {rel}"
    assert after_life["revision"] == before_life["revision"] == 7
    assert after_life == before_life


# ---------------------------------------------------------------------------
# Only GET routes exist
# ---------------------------------------------------------------------------


def test_only_get_routes_exist() -> None:
    router = create_worldbook_router(ctx=None)
    routes = _collect_routes(router)
    assert routes, "expected at least one route"
    for path, methods in routes:
        # Starlette may include HEAD automatically for GET
        non_get = methods - {"GET", "HEAD"}
        assert not non_get, f"non-GET methods on {path}: {non_get}"
    paths = {p for p, _ in routes}
    assert "/worldbook/snapshot" in paths


def test_mounted_under_api_admin() -> None:
    from admin.routes.api import create_api_router

    router = create_api_router(ctx=SimpleNamespace())
    paths = {
        getattr(r, "path", "")
        for r in router.routes
        if "worldbook" in str(getattr(r, "path", ""))
    }
    assert any(p.endswith("/worldbook/snapshot") for p in paths)
    # Full path includes /api/admin prefix from aggregator
    assert any(
        p == "/api/admin/worldbook/snapshot" or p.endswith("/worldbook/snapshot")
        for p in paths
    )


def test_no_post_put_delete_on_snapshot() -> None:
    client = _app_with_ctx(SimpleNamespace(worldbook_runtime=None))
    for method in ("post", "put", "patch", "delete"):
        resp = getattr(client, method)("/worldbook/snapshot")
        assert resp.status_code in {405, 404}, (
            f"{method} should not be allowed, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Aggregator + list bounds smoke
# ---------------------------------------------------------------------------


def test_list_bounds_cap_at_100(tmp_path: Path) -> None:
    runtime = _FakeEnabledRuntime(tmp_path)
    # Flood proposals
    many = [
        EventProposal(
            proposal_id=f"prop.{i}",
            kind="reflection",
            summary=f"s{i}",
            created_at=utc_now_iso(),
        )
        for i in range(150)
    ]
    runtime._proposals = _FakeProposalStore(many)
    ctx = SimpleNamespace(worldbook_runtime=runtime, block_trace_store=None)
    client = _app_with_ctx(ctx)
    body = client.get("/worldbook/snapshot").json()
    assert body["lifecycle"]["proposal_count"] == 150
    assert len(body["lifecycle"]["proposals"]) == 100
