import asyncio
import contextlib
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from starlette.testclient import TestClient

from admin.auth import AdminAuthMiddleware
from admin.routes.api import create_api_router
from admin.routes.api.auth import create_auth_router
from admin.routes.api.groups import create_groups_router
from admin.routes.api.learning_normalizer import create_learning_normalizer_router
from admin.routes.api.memory import create_memory_router
from admin.routes.api.plugins import create_plugins_router
from admin.routes.api.protocol import create_protocol_router
from admin.routes.api.providers import create_providers_router
from admin.routes.api.slang import create_slang_router
from admin.routes.api.soul import create_soul_router
from admin.routes.api.system import create_system_router
from kernel.bus import PluginBus
from kernel.config import BotConfig
from kernel.types import AmadeusPlugin, Command, MessageContext
from services.errors import RuntimeErrorStore
from services.learning_normalizer import LearningNormalizerStore
from services.plugin_config import PluginConfigStore
from services.plugin_state import PluginStateStore
from services.protocol_trace import ProtocolConnectionHistory, ProtocolTraceStore
from services.slang import SlangDriftAssessment, SlangSettings, SlangStore
from services.tools.base import Tool
from services.tools.registry import ToolRegistry


class _DummyMessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["333"]

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        return [{
            "role": "user",
            "speaker": "Alice(123456)",
            "content_text": "hello",
            "message_id": 1,
            "created_at": 1_746_563_200.0,
        }]


class _FakeDriftReviewer:
    def __init__(self, verdict: str = "real_drift", confidence: float = 0.9, reason: str = "test") -> None:
        self.verdict = verdict
        self.confidence = confidence
        self.reason = reason

    async def review_drift(self, **_kwargs):
        return SlangDriftAssessment(
            verdict=self.verdict,  # type: ignore[arg-type]
            confidence=self.confidence,
            reason=self.reason,
            reviewed=True,
        )


class _DummyScheduler:
    def get_all_slots(self) -> dict[str, dict]:
        return {"444": {"msg_count": 1}}


class _DummyBot:
    self_id = "10000"

    async def get_login_info(self) -> dict:
        return {"user_id": 10000, "nickname": "Omubot"}

    async def get_group_list(self) -> list[dict]:
        return [{"group_id": 555, "group_name": "Test Group"}]


class _TraceBot:
    self_id = "20000"

    async def call_api(self, action: str, **params):
        if action == "explode":
            raise RuntimeError("boom")
        return {"action": action, "params": params}


class _MissingProtocolMethodBot:
    self_id = "30000"

    async def get_login_info(self) -> dict:
        return {"user_id": 30000, "nickname": "Partial"}


class _FailingProtocolBot:
    self_id = "40000"

    async def get_login_info(self) -> dict:
        raise RuntimeError("login unavailable")

    async def get_group_list(self) -> list[dict]:
        raise RuntimeError("group list timeout")


class _DummyGroupConfig:
    def __init__(self) -> None:
        self.overrides = {111: object()}
        self.allowed_groups = [222]
        self.access = SimpleNamespace(mode="blacklist", whitelist=[], blacklist=[])
        self.presence = SimpleNamespace(default_mode="active")

    def resolve(self, group_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            access_allowed=True,
            at_only=group_id == 111,
            talk_value=0.5,
            planner_smooth=3.0,
            debounce_seconds=5.0,
            batch_size=10,
            history_load_count=30,
            privacy_mask=True,
            blocked_users={999} if group_id == 111 else set(),
            allowed_tools={"alpha_tool"} if group_id == 111 else set(),
            blocked_tools={"beta_tool"} if group_id == 111 else set(),
            reply_style="default",
            custom_prompt="",
            tools_enabled=True,
            sticker_mode="inherit",
            slang_enabled=True,
            presence_mode="active",
        )


class _DummyStore:
    def __init__(self) -> None:
        self.last_card = None

    async def add_card(self, card):
        self.last_card = card
        return "card_test"


class _DummyTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} description"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, ctx, **kwargs):
        return "ok"


class _AlphaPlugin(AmadeusPlugin):
    name = "alpha"
    description = "alpha plugin"
    version = "1.0.0"
    priority = 10
    settings_schema = {  # noqa: RUF012 - test fixture metadata
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean", "title": "启用"},
        },
    }

    def register_commands(self) -> list[Command]:
        return [Command(name="alpha", handler=lambda ctx: None, description="alpha cmd")]

    def register_tools(self) -> list[Tool]:
        return [_DummyTool("alpha_tool")]


class _BetaPlugin(AmadeusPlugin):
    name = "beta"
    description = "beta plugin"
    version = "1.0.0"
    priority = 20

    def register_commands(self) -> list[Command]:
        return [Command(name="beta", handler=lambda ctx: None, description="beta cmd", admin_only=True)]

    def register_tools(self) -> list[Tool]:
        return [_DummyTool("beta_tool")]


class _RuntimeMetaPlugin(AmadeusPlugin):
    name = "calendar_context"
    description = "calendar runtime"
    version = "1.0.0"

    def runtime_meta(self) -> dict[str, object]:
        return {
            "loaded_years": [2026, 2027],
            "current_year": 2027,
            "current_year_available": True,
            "data_source": "storage",
        }


class _FailingMessagePlugin(AmadeusPlugin):
    name = "failing_message"
    priority = 15

    async def on_message(self, ctx) -> bool:
        raise RuntimeError("boom")


class _DummyBus:
    def __init__(self, plugins: list[AmadeusPlugin]) -> None:
        self.plugins = plugins

    def get_plugin(self, name: str):
        for plugin in self.plugins:
            if plugin.name == name:
                return plugin
        return None


def _msg_ctx(content: str = "hello") -> MessageContext:
    return MessageContext(
        session_id="group_123",
        group_id="123",
        user_id="999",
        content=content,
        raw_message={},
    )


class _DummyIdentityMgr:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.loaded_paths: list[str] = []

    async def load_file(self, path: str) -> None:
        self.loaded_paths.append(path)
        if self.should_fail:
            raise RuntimeError("reload failed")


def test_admin_events_requires_auth(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "secret")

    app = FastAPI()
    app.add_middleware(AdminAuthMiddleware)

    auth_router = APIRouter(prefix="/api/admin")
    auth_router.include_router(create_auth_router())
    app.include_router(auth_router)

    @app.get("/api/admin/events")
    async def _events():
        return {"ok": True}

    client = TestClient(app)

    unauth = client.get("/api/admin/events")
    assert unauth.status_code == 401

    login = client.post("/api/admin/login", json={"token": "secret"})
    assert login.status_code == 200
    assert login.json()["ok"] is True

    authed = client.get("/api/admin/events")
    assert authed.status_code == 200
    assert authed.json() == {"ok": True}


def test_learning_normalizer_api_lists_items_and_revisions(tmp_path: Path) -> None:
    async def _seed(store: LearningNormalizerStore) -> tuple[str, str]:
        first = await store.attach_candidate(
            domain="slang",
            scope="group",
            group_id="100",
            raw_text="凤笑梦bot",
            source_table="slang_terms",
            source_id="a",
            profile="slang",
        )
        await store.attach_candidate(
            domain="slang",
            scope="group",
            group_id="100",
            raw_text="凤笑梦bot啊",
            source_table="slang_terms",
            source_id="b",
            profile="slang",
        )
        return first.cluster_id, first.item_id

    store = LearningNormalizerStore(tmp_path / "ln.db")
    asyncio.run(store.init())
    try:
        cluster_id, _item_id = asyncio.run(_seed(store))
        app = FastAPI()
        app.include_router(
            create_learning_normalizer_router(ctx=SimpleNamespace(learning_normalizer_store=store)),
            prefix="/api/admin",
        )
        client = TestClient(app)

        clusters_resp = client.get("/api/admin/learning-normalizer/clusters", params={"domain": "slang"})
        assert clusters_resp.status_code == 200
        assert clusters_resp.json()["total"] == 1

        items_resp = client.get(f"/api/admin/learning-normalizer/clusters/{cluster_id}/items")
        assert items_resp.status_code == 200
        payload = items_resp.json()
        assert len(payload["items"]) == 2
        revisions = payload["revisions"]
        auto_merge = next(item for item in revisions if item["action"] == "auto_merge")

        undo_resp = client.post(f"/api/admin/learning-normalizer/revisions/{auto_merge['revision_id']}/undo")
        assert undo_resp.status_code == 200
        assert undo_resp.json()["ok"] is True
    finally:
        asyncio.run(store.close())


def test_learning_normalizer_api_syncs_source_meta_after_undo(tmp_path: Path) -> None:
    async def _seed(slang_store: SlangStore, normalizer: LearningNormalizerStore) -> tuple[str, str, str]:
        term = await slang_store.create_term(
            term="凤笑梦bot",
            meaning="测试用语",
            aliases=[],
            group_id="100",
            status="candidate",
        )
        term_id = term.term_id
        first = await normalizer.attach_candidate(
            domain="slang",
            scope="group",
            group_id="100",
            raw_text="凤笑梦bot",
            source_table="slang_terms",
            source_id=term_id,
            profile="slang",
        )
        second = await normalizer.attach_candidate(
            domain="slang",
            scope="group",
            group_id="100",
            raw_text="凤笑梦bot啊",
            source_table="slang_terms",
            source_id=term_id,
            profile="slang",
        )
        await slang_store.update_term(term_id, meta=second.to_meta(), record_revision=False)
        revisions = await normalizer.list_cluster_revisions(first.cluster_id, action="auto_merge")
        return term_id, second.cluster_id, revisions[0].revision_id

    slang_store = SlangStore(tmp_path / "slang.db")
    normalizer = LearningNormalizerStore(tmp_path / "ln.db")
    asyncio.run(slang_store.init())
    asyncio.run(normalizer.init())
    try:
        term_id, old_cluster_id, revision_id = asyncio.run(_seed(slang_store, normalizer))
        app = FastAPI()
        app.include_router(
            create_learning_normalizer_router(
                ctx=SimpleNamespace(learning_normalizer_store=normalizer, slang_store=slang_store),
            ),
            prefix="/api/admin",
        )
        client = TestClient(app)

        resp = client.post(f"/api/admin/learning-normalizer/revisions/{revision_id}/undo")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        term = asyncio.run(slang_store.get_term(term_id))
        assert term is not None
        assert term.meta["normalization_cluster_id"] != old_cluster_id
        assert term.meta["normalization_method"] == "manual_adjust"
    finally:
        asyncio.run(normalizer.close())
        asyncio.run(slang_store.close())


def test_create_api_router_uses_custom_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "custom.json"
    config_path.write_text('{"llm":{"model":"x"}}', encoding="utf-8")

    app = FastAPI()
    app.include_router(create_api_router(config_path=str(config_path)))
    client = TestClient(app)

    resp = client.get("/api/admin/config")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["path"] == str(config_path)
    assert payload["format_mode"] == "json"
    assert payload["migration_pending"] is False
    assert "schema" in payload["editor"]
    assert payload["editor"]["values"]["llm"]["model"] == "x"


def test_config_endpoint_exposes_segmentation_and_scheduler_controls(tmp_path: Path) -> None:
    config_path = tmp_path / "custom.json"
    config_path.write_text(json.dumps({
        "reply_segmentation": {
            "max_segment_chars": 20,
            "max_send_segments": 0,
            "soft_max_send_segments": 12,
        },
        "scheduler": {
            "concurrency": {
                "global_llm_limit": 2,
                "first_segment_release": False,
            },
        },
        "reply_workflow": {
            "mode": "shadow",
            "semantic_force_threshold": 0.78,
            "semantic_timeout_ms": 2200,
            "semantic_max_chars": 48,
            "directed_followup_window_s": 180.0,
            "shadow_log_private": True,
        },
    }), encoding="utf-8")

    app = FastAPI()
    app.include_router(create_api_router(config_path=str(config_path)))
    client = TestClient(app)

    resp = client.get("/api/admin/config")
    assert resp.status_code == 200
    payload = resp.json()
    values = payload["editor"]["values"]

    assert values["reply_segmentation"]["max_send_segments"] == 0
    assert values["reply_segmentation"]["soft_max_send_segments"] == 12
    assert values["scheduler"]["concurrency"]["global_llm_limit"] == 2
    assert values["scheduler"]["concurrency"]["first_segment_release"] is False
    assert values["reply_workflow"]["mode"] == "shadow"
    assert values["reply_workflow"]["semantic_force_threshold"] == 0.78
    assert values["reply_workflow"]["semantic_timeout_ms"] == 2200
    assert values["reply_workflow"]["semantic_max_chars"] == 48
    assert values["reply_workflow"]["directed_followup_window_s"] == 180.0
    assert values["reply_workflow"]["shadow_log_private"] is True

    schema_by_path = {
        child["path"]: child
        for section in payload["editor"]["schema"]
        for child in section.get("children", [])
    }
    top_schema_by_path = {section["path"]: section for section in payload["editor"]["schema"]}
    assert schema_by_path["reply_segmentation.max_send_segments"]["display_label"] == "硬性段数上限"
    assert schema_by_path["reply_segmentation.soft_max_send_segments"]["risk_level"] == "careful"
    assert schema_by_path["reply_segmentation.boundary_backend"]["display_label"] == "分段边界后端"
    assert top_schema_by_path["reply_workflow"]["kind"] == "object"
    reply_workflow_children = {
        child["path"]: child
        for child in top_schema_by_path["reply_workflow"]["children"]
    }
    assert reply_workflow_children["reply_workflow.mode"]["display_label"] == "回复工作流模式"
    assert reply_workflow_children["reply_workflow.mode"]["kind"] == "select"
    assert reply_workflow_children["reply_workflow.mode"]["options"] == ["off", "shadow", "semantic"]
    assert reply_workflow_children["reply_workflow.semantic_force_threshold"]["kind"] == "number"
    assert schema_by_path["scheduler.concurrency"]["kind"] == "object"

    concurrency_children = {
        child["path"]: child
        for child in schema_by_path["scheduler.concurrency"]["children"]
    }
    assert concurrency_children["scheduler.concurrency.global_llm_limit"]["kind"] == "number"
    assert concurrency_children["scheduler.concurrency.first_segment_release"]["risk_level"] == "danger"

    values["reply_segmentation"]["max_segment_chars"] = 28
    values["reply_segmentation"]["soft_max_send_segments"] = 10
    values["scheduler"]["concurrency"]["global_llm_limit"] = 3
    values["reply_workflow"]["mode"] = "semantic"
    values["reply_workflow"]["semantic_force_threshold"] = 0.82

    preview_resp = client.post("/api/admin/config/preview", json={"mode": "structured", "values": values})
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["ok"] is True
    changed_paths = {item["path"] for item in preview["changes"]}
    assert "reply_segmentation.max_segment_chars" in changed_paths
    assert "reply_segmentation.soft_max_send_segments" in changed_paths
    assert "scheduler.concurrency.global_llm_limit" in changed_paths
    assert "reply_workflow.mode" in changed_paths
    assert "reply_workflow.semantic_force_threshold" in changed_paths

    save_resp = client.post("/api/admin/config", json={"mode": "structured", "values": values})
    assert save_resp.status_code == 200
    saved = save_resp.json()
    assert saved["ok"] is True
    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["reply_segmentation"]["max_segment_chars"] == 28
    assert written["reply_segmentation"]["soft_max_send_segments"] == 10
    assert written["scheduler"]["concurrency"]["global_llm_limit"] == 3
    assert written["reply_workflow"]["mode"] == "semantic"
    assert written["reply_workflow"]["semantic_force_threshold"] == 0.82


def test_knowledge_api_returns_structured_hits_from_live_context() -> None:
    class _Hit:
        chunk_id = "docs/test.md::部署"
        content = "Docker Compose 部署说明"
        source = "docs/test.md"
        title = "部署"
        score = 1.25

        def to_dict(self):
            return {
                "id": self.chunk_id,
                "chunk_id": self.chunk_id,
                "content": self.content,
                "source": self.source,
                "title": self.title,
                "score": self.score,
                "metadata": {"retriever": "test"},
            }

    class _Knowledge:
        loaded = True
        chunk_count = 1

        def stats(self):
            return {"loaded": True, "chunk_count": 1, "source_count": 1}

        def search_hits(self, query: str, top_k: int = 3):
            assert query == "Docker"
            assert top_k == 20
            return [_Hit()]

    app = FastAPI()
    app.include_router(create_api_router(ctx=SimpleNamespace(knowledge_base=_Knowledge())))
    client = TestClient(app)

    resp = client.get("/api/admin/knowledge", params={"q": "Docker"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["available"] is True
    assert payload["entry_count"] == 1
    assert payload["results"][0]["chunk_id"] == "docs/test.md::部署"
    assert payload["results"][0]["source"] == "docs/test.md"


def test_knowledge_graph_api_degrades_when_service_missing() -> None:
    app = FastAPI()
    app.include_router(create_api_router(ctx=SimpleNamespace()))
    client = TestClient(app)

    entities = client.get("/api/admin/knowledge/graph/entities")
    relationships = client.get("/api/admin/knowledge/graph/relationships")
    candidates = client.get("/api/admin/knowledge/graph/candidates")
    scope_risks = client.get("/api/admin/knowledge/graph/scope-risks")

    assert entities.status_code == 200
    assert entities.json() == {"available": False, "entities": []}
    assert relationships.status_code == 200
    assert relationships.json() == {"available": False, "relationships": []}
    assert candidates.status_code == 200
    assert candidates.json() == {"available": False, "candidates": []}
    assert scope_risks.status_code == 200
    assert scope_risks.json() == {"available": False, "relationships": []}


def test_knowledge_graph_api_returns_runtime_graph() -> None:
    class _Graph:
        async def list_entities(self, *, limit: int = 100):
            assert limit == 80
            return [{"name": "用户123", "fact_count": 1}]

        async def list_relationships(self, *, limit: int = 100):
            assert limit == 120
            return [{"fact_id": "gf_1", "subject": "用户123", "predicate": "喜欢", "object": "音游"}]

        async def list_scope_risks(self, *, limit: int = 100):
            assert limit == 40
            return [{"fact_id": "gf_risk", "subject": "用户123", "predicate": "喜欢", "object": "音游"}]

        async def list_candidates(self, *, status: str = "pending", limit: int = 100):
            assert status == "pending"
            assert limit == 50
            return [{"candidate_id": "gc_1", "subject": "用户123"}]

        async def get_relationship(self, fact_id: str):
            assert fact_id == "gf_1"
            return {
                "fact_id": "gf_1",
                "subject": "用户123",
                "predicate": "喜欢",
                "object": "音游",
                "evidence": [{"id": "card_1", "quote": "喜欢音游"}],
            }

        async def rollback_relationship(self, fact_id: str, *, note: str = ""):
            assert fact_id == "gf_1"
            assert note == "撤销"
            return True

        async def supersede_relationship(self, fact_id: str, **kwargs):
            assert fact_id == "gf_1"
            assert kwargs["object"] == "节奏游戏"
            return SimpleNamespace(to_dict=lambda: {"fact_id": "gf_2", "object": "节奏游戏"})

    app = FastAPI()
    app.include_router(create_api_router(ctx=SimpleNamespace(knowledge_graph=_Graph())))
    client = TestClient(app)

    entities = client.get("/api/admin/knowledge/graph/entities", params={"limit": 80})
    relationships = client.get("/api/admin/knowledge/graph/relationships", params={"limit": 120})
    relationship = client.get("/api/admin/knowledge/graph/relationships/gf_1")
    rollback = client.post("/api/admin/knowledge/graph/relationships/gf_1/rollback", params={"note": "撤销"})
    supersede = client.post(
        "/api/admin/knowledge/graph/relationships/gf_1/supersede",
        json={"subject": "用户123", "predicate": "喜欢", "object": "节奏游戏"},
    )
    candidates = client.get("/api/admin/knowledge/graph/candidates", params={"limit": 50})
    scope_risks = client.get("/api/admin/knowledge/graph/scope-risks", params={"limit": 40})

    assert entities.status_code == 200
    assert entities.json()["entities"][0]["name"] == "用户123"
    assert relationships.status_code == 200
    assert relationships.json()["relationships"][0]["fact_id"] == "gf_1"
    assert relationship.status_code == 200
    assert relationship.json()["relationship"]["evidence"][0]["id"] == "card_1"
    assert rollback.status_code == 200
    assert rollback.json()["ok"] is True
    assert supersede.status_code == 200
    assert supersede.json()["fact"]["fact_id"] == "gf_2"
    assert candidates.status_code == 200
    assert candidates.json()["candidates"][0]["candidate_id"] == "gc_1"
    assert scope_risks.status_code == 200
    assert scope_risks.json()["relationships"][0]["fact_id"] == "gf_risk"


def test_knowledge_api_sort_modes() -> None:
    class _Source:
        def __init__(self, source: str, status: str, chunk_count: int, updated_at: str) -> None:
            self._payload = {
                "source": source,
                "path": f"docs/{source}",
                "status": status,
                "chunk_count": chunk_count,
                "updated_at": updated_at,
            }

        def to_dict(self) -> dict[str, object]:
            return dict(self._payload)

    class _Knowledge:
        loaded = True

        def stats(self):
            return {"loaded": True, "chunk_count": 6, "source_count": 2}

        def sources(self):
            return [
                _Source("older.md", "indexed", 2, "2026-05-12T09:00:00+08:00"),
                _Source("newer.md", "skipped", 1, "2026-05-13T09:00:00+08:00"),
            ]

    class _Graph:
        async def list_relationships(self, *, limit: int = 100):
            assert limit == 100
            return [
                {
                    "fact_id": "gf_old",
                    "confidence": 0.4,
                    "updated_at": "2026-05-13T09:00:00+08:00",
                    "created_at": "2026-05-13T08:00:00+08:00",
                },
                {
                    "fact_id": "gf_best",
                    "confidence": 0.9,
                    "updated_at": "2026-05-12T09:00:00+08:00",
                    "created_at": "2026-05-12T08:00:00+08:00",
                },
            ]

        async def list_candidates(self, *, status: str = "pending", limit: int = 100):
            assert status == "pending"
            assert limit == 100
            return [
                {
                    "candidate_id": "gc_old",
                    "confidence": 0.4,
                    "updated_at": "2026-05-13T09:00:00+08:00",
                    "created_at": "2026-05-13T08:00:00+08:00",
                },
                {
                    "candidate_id": "gc_best",
                    "confidence": 0.9,
                    "updated_at": "2026-05-12T09:00:00+08:00",
                    "created_at": "2026-05-12T08:00:00+08:00",
                },
            ]

    app = FastAPI()
    app.include_router(create_api_router(ctx=SimpleNamespace(knowledge_base=_Knowledge(), knowledge_graph=_Graph())))
    client = TestClient(app)

    sources_default = client.get("/api/admin/knowledge/sources").json()["sources"]
    sources_time = client.get("/api/admin/knowledge/sources", params={"sort": "time"}).json()["sources"]
    assert sources_default[0]["source"] == "older.md"
    assert sources_time[0]["source"] == "newer.md"

    relationships_default = client.get("/api/admin/knowledge/graph/relationships").json()["relationships"]
    relationships_time = client.get(
        "/api/admin/knowledge/graph/relationships",
        params={"sort": "time"},
    ).json()["relationships"]
    assert relationships_default[0]["fact_id"] == "gf_best"
    assert relationships_time[0]["fact_id"] == "gf_old"

    candidates_default = client.get("/api/admin/knowledge/graph/candidates").json()["candidates"]
    candidates_time = client.get("/api/admin/knowledge/graph/candidates", params={"sort": "time"}).json()["candidates"]
    assert candidates_default[0]["candidate_id"] == "gc_best"
    assert candidates_time[0]["candidate_id"] == "gc_old"


def test_context_metrics_api_returns_runtime_metrics() -> None:
    class _ContextService:
        def metrics(self, *, limit: int = 80):
            assert limit == 12
            return {
                "total_queries": 2,
                "miss_count": 1,
                "miss_rate": 0.5,
                "avg_pack_chars": 120,
                "duplicate_rate": 0,
                "recent": [{"query": "Docker", "hit_count": 1}],
            }

    app = FastAPI()
    app.include_router(create_api_router(ctx=SimpleNamespace(context_service=_ContextService())))
    client = TestClient(app)

    resp = client.get("/api/admin/context/metrics", params={"limit": 12})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["available"] is True
    assert payload["metrics"]["total_queries"] == 2
    assert payload["metrics"]["recent"][0]["query"] == "Docker"


def test_config_endpoint_falls_back_to_legacy_toml_and_migrates_to_json(tmp_path: Path) -> None:
    legacy_toml = tmp_path / "config.toml"
    legacy_toml.write_text("[llm]\nmodel='legacy'\n", encoding="utf-8")
    target_json = tmp_path / "config.json"

    app = FastAPI()
    app.include_router(create_api_router(config_path=str(target_json)))
    client = TestClient(app)

    resp = client.get("/api/admin/config")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["format_mode"] == "legacy"
    assert payload["migration_pending"] is True
    assert payload["editor"]["values"]["llm"]["model"] == "legacy"

    values = payload["editor"]["values"]
    values["llm"]["model"] = "migrated"
    save_resp = client.post("/api/admin/config", json={"mode": "structured", "values": values})
    assert save_resp.status_code == 200
    save_payload = save_resp.json()
    assert save_payload["ok"] is True
    assert save_payload["format_mode"] == "json"
    assert save_payload["migration_pending"] is False

    assert target_json.is_file()
    written = json.loads(target_json.read_text(encoding="utf-8"))
    assert written["llm"]["model"] == "migrated"
    assert legacy_toml.is_file()


def test_config_preview_and_audit_history_mask_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "llm": {
            "model": "before-model",
            "api_key": "sk-before-secret",
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    app = FastAPI()
    app.include_router(create_api_router(config_path=str(config_path)))
    client = TestClient(app)

    initial_resp = client.get("/api/admin/config")
    assert initial_resp.status_code == 200
    payload = initial_resp.json()
    values = payload["editor"]["values"]
    values["llm"]["model"] = "after-model"
    values["llm"]["api_key"] = "sk-after-secret"

    preview_resp = client.post("/api/admin/config/preview", json={"mode": "structured", "values": values})
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["ok"] is True
    assert preview["summary"]["total"] == 2
    assert "llm" in preview["summary"]["top_levels"]

    model_change = next(item for item in preview["changes"] if item["path"] == "llm.model")
    assert model_change["before_display"] == "before-model"
    assert model_change["after_display"] == "after-model"

    key_change = next(item for item in preview["changes"] if item["path"] == "llm.api_key")
    assert key_change["secret"] is True
    assert key_change["before_display"] != "sk-before-secret"
    assert key_change["after_display"] != "sk-after-secret"
    assert "***" in key_change["before_display"]
    assert "***" in key_change["after_display"]

    save_resp = client.post("/api/admin/config", json={"mode": "structured", "values": values})
    assert save_resp.status_code == 200
    saved = save_resp.json()
    assert saved["ok"] is True
    assert saved["audit_entry"]["summary"]["total"] == 2

    history_resp = client.get("/api/admin/config/history")
    assert history_resp.status_code == 200
    history = history_resp.json()
    assert history["entries"]
    latest = history["entries"][0]
    assert latest["config_path"] == str(config_path)
    latest_key_change = next(item for item in latest["changes"] if item["path"] == "llm.api_key")
    assert latest_key_change["before_display"] != "sk-before-secret"
    assert latest_key_change["after_display"] != "sk-after-secret"
    assert "***" in latest_key_change["before_display"]
    assert "***" in latest_key_change["after_display"]
    assert Path(history["path"]).is_file()


def test_config_backups_and_restore_flow(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "llm": {
            "model": "before-model",
            "api_key": "sk-before-secret",
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    app = FastAPI()
    app.include_router(create_api_router(config_path=str(config_path)))
    client = TestClient(app)

    initial_payload = client.get("/api/admin/config").json()
    first_values = initial_payload["editor"]["values"]
    first_values["llm"]["model"] = "saved-model"
    first_values["llm"]["api_key"] = "sk-saved-secret"

    first_save = client.post("/api/admin/config", json={"mode": "structured", "values": first_values})
    assert first_save.status_code == 200
    first_saved_payload = first_save.json()
    assert first_saved_payload["ok"] is True
    assert first_saved_payload["backup_entry"]["trigger"] == "save"
    first_backup_id = first_saved_payload["backup_entry"]["id"]

    second_values = client.get("/api/admin/config").json()["editor"]["values"]
    second_values["llm"]["model"] = "latest-model"
    second_values["llm"]["api_key"] = "sk-latest-secret"
    second_save = client.post("/api/admin/config", json={"mode": "structured", "values": second_values})
    assert second_save.status_code == 200
    assert second_save.json()["ok"] is True

    backups_resp = client.get("/api/admin/config/backups")
    assert backups_resp.status_code == 200
    backups_payload = backups_resp.json()
    assert backups_payload["entries"]
    backup_ids = {item["id"] for item in backups_payload["entries"]}
    assert first_backup_id in backup_ids
    serialized_backups = json.dumps(backups_payload, ensure_ascii=False)
    assert "sk-saved-secret" not in serialized_backups
    assert "sk-latest-secret" not in serialized_backups
    assert Path(backups_payload["path"]).is_file()

    restore_resp = client.post("/api/admin/config/restore", json={"backup_id": first_backup_id})
    assert restore_resp.status_code == 200
    restored_payload = restore_resp.json()
    assert restored_payload["ok"] is True
    assert restored_payload["editor"]["values"]["llm"]["model"] == "saved-model"
    assert restored_payload["editor"]["values"]["llm"]["api_key"] == "sk-saved-secret"
    assert restored_payload["audit_entry"]["mode"] == "restore"
    assert restored_payload["backup_entry"]["trigger"] == "restore"

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["llm"]["model"] == "saved-model"
    assert written["llm"]["api_key"] == "sk-saved-secret"

    history_resp = client.get("/api/admin/config/history")
    assert history_resp.status_code == 200
    history_payload = history_resp.json()
    assert history_payload["entries"][0]["mode"] == "restore"

    backups_after_restore = client.get("/api/admin/config/backups").json()["entries"]
    restore_triggers = {item["trigger"] for item in backups_after_restore[:4]}
    assert "restore" in restore_triggers
    assert "pre_restore" in restore_triggers


def test_groups_endpoint_discovers_groups_and_normalizes_messages() -> None:
    app = FastAPI()
    app.include_router(
        create_groups_router(
            group_config=_DummyGroupConfig(),
            message_log=_DummyMessageLog(),
            scheduler=_DummyScheduler(),
            bot=_DummyBot(),
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.get("/api/admin/groups")
    assert resp.status_code == 200
    group_ids = {item["group_id"] for item in resp.json()["groups"]}
    assert group_ids == {"111", "222", "333", "444", "555"}

    msg_resp = client.get("/api/admin/groups/333/messages")
    assert msg_resp.status_code == 200
    msg = msg_resp.json()["messages"][0]
    assert msg["user_id"] == "123456"
    assert msg["message"] == "hello"
    assert isinstance(msg["timestamp"], str)
    assert len(msg["timestamp"]) == 19


def test_groups_endpoint_uses_runtime_inventory_when_live_refresh_fails() -> None:
    ctx = SimpleNamespace(
        group_inventory={
            "666": {"group_id": 666, "group_name": "Runtime Group A"},
            "777": {"group_id": 777, "group_name": "Runtime Group B"},
        },
        bot=_FailingProtocolBot(),
    )
    app = FastAPI()
    app.include_router(
        create_groups_router(
            group_config=_DummyGroupConfig(),
            ctx=ctx,
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.get("/api/admin/groups")
    assert resp.status_code == 200
    groups_by_id = {item["group_id"]: item for item in resp.json()["groups"]}
    assert {"111", "222", "666", "777"}.issubset(groups_by_id)
    assert groups_by_id["666"]["group_name"] == "Runtime Group A"
    assert groups_by_id["777"]["group_name"] == "Runtime Group B"


def test_groups_policy_endpoint_persists_separate_json(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    runtime_config = BotConfig.model_validate({
        "group": {
            "allowed_groups": [111],
        },
    })

    app = FastAPI()
    app.include_router(
        create_groups_router(
            config=runtime_config,
            group_config=runtime_config.group,
            message_log=_DummyMessageLog(),
            config_path=str(config_path),
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    initial = client.get("/api/admin/groups/policy")
    assert initial.status_code == 200
    assert initial.json()["policy"]["mode"] == "whitelist"
    assert initial.json()["policy"]["whitelist"] == [111]

    saved_resp = client.post(
        "/api/admin/groups/policy",
        json={
            "mode": "blacklist",
            "whitelist": [111],
            "blacklist": [333],
            "log_dropped": False,
        },
    )
    assert saved_resp.status_code == 200
    saved = saved_resp.json()
    assert saved["ok"] is True
    assert saved["policy"]["mode"] == "blacklist"
    assert saved["policy"]["blacklist"] == [333]
    assert runtime_config.group.access.mode == "blacklist"
    assert runtime_config.group.access.blacklist == [333]

    policy_file = tmp_path / "group-policy.json"
    assert policy_file.is_file()
    written = json.loads(policy_file.read_text(encoding="utf-8"))
    assert written == {
        "mode": "blacklist",
        "whitelist": [111],
        "blacklist": [333],
        "log_dropped": False,
    }

    groups_by_id = {item["group_id"]: item for item in saved["groups"]}
    assert groups_by_id["111"]["access_allowed"] is True
    assert groups_by_id["333"]["access_allowed"] is False


def test_groups_profile_can_enable_slang_learning_without_speaking_access(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    runtime_config = BotConfig.model_validate({
        "group": {
            "access": {
                "mode": "whitelist",
                "whitelist": [111],
                "blacklist": [],
            },
        },
    })

    app = FastAPI()
    app.include_router(
        create_groups_router(
            config=runtime_config,
            group_config=runtime_config.group,
            message_log=_DummyMessageLog(),
            config_path=str(config_path),
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    detail = client.get("/api/admin/groups/222/profile").json()["group"]
    assert detail["access_allowed"] is False
    assert detail["presence_mode"] == "off"
    assert detail["slang_enabled"] is False

    payload = {
        "blocked_users": detail["blocked_users"],
        "allowed_tools": detail["allowed_tools"],
        "blocked_tools": detail["blocked_tools"],
        "at_only": detail["at_only"],
        "talk_value": detail["talk_value"],
        "planner_smooth": detail["planner_smooth"],
        "debounce_seconds": detail["debounce_seconds"],
        "batch_size": detail["batch_size"],
        "history_load_count": detail["history_load_count"],
        "reply_style": detail["reply_style"],
        "custom_prompt": detail["custom_prompt"],
        "tools_enabled": detail["tools_enabled"],
        "sticker_mode": detail["sticker_mode"],
        "slang_enabled": True,
        "presence_mode": "off",
    }
    saved_resp = client.post("/api/admin/groups/222/profile", json=payload)
    assert saved_resp.status_code == 200
    saved = saved_resp.json()["group"]
    assert saved["access_allowed"] is False
    assert saved["presence_mode"] == "silent_learn"
    assert saved["slang_enabled"] is True
    assert runtime_config.group.allows_learning_group(222) is True
    assert runtime_config.group.allows_active_group(222) is False

    written = json.loads(config_path.read_text(encoding="utf-8"))
    override = written["group"]["overrides"]["222"]
    assert override["presence_mode"] == "silent_learn"
    assert override["slang_enabled"] is True


def test_groups_profile_endpoint_persists_override_and_resets(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    bus = PluginBus()
    bus.register(_AlphaPlugin())
    bus.register(_BetaPlugin())
    runtime_config = BotConfig.model_validate({
        "group": {
            "talk_value": 0.3,
            "planner_smooth": 3.0,
            "debounce_seconds": 5.0,
            "batch_size": 10,
            "history_load_count": 30,
            "blocked_users": [70001],
            "allowed_tools": ["alpha_tool"],
            "reply_style": "default",
            "custom_prompt": "",
            "tools_enabled": True,
            "sticker_mode": "inherit",
            "slang_enabled": True,
            "allowed_groups": [123456],
        },
    })

    app = FastAPI()
    app.include_router(
        create_groups_router(
            config=runtime_config,
            group_config=runtime_config.group,
            message_log=_DummyMessageLog(),
            bus=bus,
            config_path=str(config_path),
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    detail_resp = client.get("/api/admin/groups/123456/profile")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["ok"] is True
    assert detail["group"]["global_blocked_users"] == [70001]
    assert detail["group"]["allowed_tools"] == ["alpha_tool"]
    assert detail["group"]["presence_mode"] == "active"
    assert detail["group"]["access_allowed"] is True
    assert {tool["name"] for tool in detail["tool_catalog"]} == {"alpha_tool", "beta_tool"}
    assert detail["audit"]["entries"] == []

    save_resp = client.post(
        "/api/admin/groups/123456/profile",
        json={
            "blocked_users": [888, "999"],
            "allowed_tools": ["alpha_tool", "beta_tool"],
            "blocked_tools": ["alpha_tool"],
            "at_only": True,
            "talk_value": 0.72,
            "planner_smooth": 4.5,
            "debounce_seconds": 9.0,
            "batch_size": 8,
            "history_load_count": 40,
            "reply_style": "playful",
            "custom_prompt": "多接梗，少说教。",
            "tools_enabled": False,
            "sticker_mode": "off",
            "slang_enabled": False,
            "presence_mode": "silent_learn",
        },
    )
    assert save_resp.status_code == 200
    saved = save_resp.json()
    assert saved["ok"] is True
    assert saved["group"]["profile_customized"] is True
    assert saved["group"]["blocked_users"] == [888, 999, 70001]
    assert saved["group"]["allowed_tools"] == ["beta_tool"]
    assert saved["group"]["blocked_tools"] == ["alpha_tool"]
    assert saved["group"]["reply_style"] == "playful"
    assert saved["group"]["tools_enabled"] is False
    assert saved["group"]["slang_enabled"] is False
    assert saved["group"]["presence_mode"] == "silent_learn"
    assert saved["group"]["access_allowed"] is True
    assert saved["audit_entry"]["summary"]["changed_count"] >= 1

    resolved = runtime_config.group.resolve(123456)
    assert resolved.at_only is True
    assert resolved.blocked_users == {70001, 888, 999}
    assert resolved.allowed_tools == {"beta_tool"}
    assert resolved.blocked_tools == {"alpha_tool"}
    assert resolved.reply_style == "playful"
    assert resolved.custom_prompt == "多接梗，少说教。"
    assert resolved.tools_enabled is False
    assert resolved.sticker_mode == "off"
    assert resolved.slang_enabled is False
    assert resolved.presence_mode == "silent_learn"

    written = json.loads(config_path.read_text(encoding="utf-8"))
    override = written["group"]["overrides"]["123456"]
    assert override["blocked_users"] == [888, 999]
    assert override["allowed_tools"] == ["beta_tool"]
    assert override["blocked_tools"] == ["alpha_tool"]
    assert override["reply_style"] == "playful"
    assert override["tools_enabled"] is False

    detail_after_save = client.get("/api/admin/groups/123456/profile")
    assert detail_after_save.status_code == 200
    detail_after_payload = detail_after_save.json()
    assert len(detail_after_payload["audit"]["entries"]) == 1
    assert detail_after_payload["audit"]["entries"][0]["action"] == "save"

    reset_resp = client.delete("/api/admin/groups/123456/profile")
    assert reset_resp.status_code == 200
    reset = reset_resp.json()
    assert reset["ok"] is True
    assert reset["group"]["profile_customized"] is False
    assert reset["group"]["blocked_users"] == [70001]
    assert reset["group"]["allowed_tools"] == ["alpha_tool"]
    assert reset["group"]["presence_mode"] == "active"

    resolved_after_reset = runtime_config.group.resolve(123456)
    assert resolved_after_reset.at_only is False
    assert resolved_after_reset.blocked_users == {70001}
    assert resolved_after_reset.reply_style == "default"
    assert resolved_after_reset.tools_enabled is True

    detail_after_reset = client.get("/api/admin/groups/123456/profile")
    assert detail_after_reset.status_code == 200
    reset_payload = detail_after_reset.json()
    assert len(reset_payload["audit"]["entries"]) == 2
    assert reset_payload["audit"]["entries"][0]["action"] == "reset"


def test_system_version_contract(monkeypatch) -> None:
    async def _fake_latest_release():
        return {
            "tag_name": "v9.9.9",
            "name": "Version 9.9.9",
            "html_url": "https://example.com/releases/v9.9.9",
        }

    monkeypatch.setattr("services.version.fetch_latest_release", _fake_latest_release)

    app = FastAPI()
    app.include_router(create_system_router(), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["latest_tag"] == "v9.9.9"
    assert data["latest_name"] == "Version 9.9.9"
    assert data["latest_url"] == "https://example.com/releases/v9.9.9"
    assert data["has_update"] is True


def test_system_health_uses_runtime_context_bot_reference() -> None:
    app = FastAPI()
    app.include_router(
        create_system_router(ctx=SimpleNamespace(bot=object())),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.get("/api/admin/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["napcat"] == "connected"
    assert data["connected_bots"] >= 1


def test_system_endpoint_reports_active_sessions_from_short_term_store() -> None:
    short_term = SimpleNamespace(_store={"private_1": [], "group_2": []})

    app = FastAPI()
    app.include_router(create_system_router(short_term_memory=short_term), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/system")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_sessions"] == 2
    assert data["restart_notice"]["supported"] is True
    assert len(data["restart_notice"]["impact"]) >= 2
    assert len(data["restart_notice"]["checklist"]) >= 2


def test_system_restart_endpoint_returns_success_with_custom_executor() -> None:
    called = {"count": 0}

    def _dummy_exit(_code: int) -> None:
        called["count"] += 1

    app = FastAPI()
    app.include_router(create_system_router(restart_executor=_dummy_exit), prefix="/api/admin")
    client = TestClient(app)

    resp = client.post("/api/admin/system/restart")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_memory_create_card_requires_scope_id_for_user_group() -> None:
    store = _DummyStore()
    app = FastAPI()
    app.include_router(create_memory_router(card_store=store), prefix="/api/admin")
    client = TestClient(app)

    bad = client.post(
        "/api/admin/memory/cards",
        json={"category": "fact", "scope": "user", "scope_id": "", "content": "x"},
    )
    assert bad.status_code == 200
    assert bad.json()["ok"] is False
    assert store.last_card is None

    good = client.post(
        "/api/admin/memory/cards",
        json={"category": "fact", "scope": "global", "scope_id": "", "content": "x"},
    )
    assert good.status_code == 200
    assert good.json()["ok"] is True
    assert store.last_card is not None
    assert store.last_card.scope_id == "global"


def test_memory_cards_accept_sort_param() -> None:
    class _ListStore:
        def __init__(self) -> None:
            self.last_kwargs: dict[str, object] | None = None

        async def list_cards(self, **kwargs):
            self.last_kwargs = kwargs
            return []

    store = _ListStore()
    app = FastAPI()
    app.include_router(create_memory_router(card_store=store), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/memory/cards", params={"sort": "time"})
    assert resp.status_code == 200
    assert resp.json()["sort"] == "time"
    assert store.last_kwargs is not None
    assert store.last_kwargs["sort"] == "time"


def test_plugin_endpoints_include_ownership() -> None:
    bus = _DummyBus([_AlphaPlugin(), _BetaPlugin()])

    app = FastAPI()
    app.include_router(create_plugins_router(bus=bus), prefix="/api/admin")
    client = TestClient(app)

    tools_resp = client.get("/api/admin/tools")
    assert tools_resp.status_code == 200
    tools = tools_resp.json()["tools"]
    assert {tool["plugin"] for tool in tools} == {"alpha", "beta"}

    detail_resp = client.get("/api/admin/plugins/alpha")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert [cmd["name"] for cmd in detail["commands"]] == ["alpha"]
    assert [tool["function"]["name"] for tool in detail["tools"]] == ["alpha_tool"]


def test_plugin_health_and_state_endpoint_refreshes_tools(tmp_path: Path) -> None:
    bus = PluginBus()
    plugin = _AlphaPlugin()
    plugin.permissions = ["tool", "command"]
    plugin.category = "utility"
    bus.register(plugin)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    registry = ToolRegistry()
    for tool in bus.collect_tools():
        registry.register(tool)

    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=bus,
            tool_registry=registry,
            plugin_state_store=state_store,
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    health_resp = client.get("/api/admin/plugins/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["plugins"][0]["name"] == "alpha"
    assert health_resp.json()["plugins"][0]["display_label"] == "健康"

    disable_resp = client.post("/api/admin/plugins/alpha/state", json={"enabled": False})
    assert disable_resp.status_code == 200
    assert disable_resp.json()["ok"] is True
    assert registry.empty is True
    assert state_store.get("alpha") is False

    detail_resp = client.get("/api/admin/plugins/alpha")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["enabled"] is False
    assert detail["persistent_enabled"] is False
    assert detail["category"] == "utility"
    assert detail["permissions"] == ["tool", "command"]
    assert "settings_schema" in detail

    state_resp = client.get("/api/admin/plugins/state")
    assert state_resp.status_code == 200
    assert state_resp.json()["plugins"]["alpha"]["enabled"] is False


def test_plugin_detail_exposes_runtime_meta() -> None:
    bus = _DummyBus([_RuntimeMetaPlugin()])

    app = FastAPI()
    app.include_router(create_plugins_router(bus=bus), prefix="/api/admin")
    client = TestClient(app)

    detail_resp = client.get("/api/admin/plugins/calendar_context")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["runtime_meta"]["current_year"] == 2027
    assert detail["runtime_meta"]["data_source"] == "storage"


def test_plugin_list_exposes_friendly_permission_limited_health() -> None:
    bus = PluginBus()
    plugin = _AlphaPlugin()
    plugin.permissions = ["message"]
    bus.register(plugin)

    assert bus.collect_tools() == []

    app = FastAPI()
    app.include_router(create_plugins_router(bus=bus), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/plugins")
    assert resp.status_code == 200
    [alpha] = resp.json()["plugins"]
    assert alpha["health"]["state"] == "permission_limited"
    assert alpha["health"]["display_label"] == "按权限运行"
    assert alpha["health"]["display_type"] == "info"
    assert alpha["health"]["permission_denials"] >= 1


def test_plugin_state_refuses_locked_system_plugin(tmp_path: Path) -> None:
    bus = PluginBus()
    plugin = _AlphaPlugin()
    plugin.name = "chat"
    bus.register(plugin)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")

    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_state_store=state_store),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.post("/api/admin/plugins/chat/state", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "系统级插件无法关闭"
    assert bus.get_plugin("chat").enabled is True
    assert state_store.get("chat") is None


def test_plugin_list_hides_system_plugins_by_default(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    bus = PluginBus()
    user_plugin = _AlphaPlugin()
    system_plugin = _BetaPlugin()
    system_plugin.name = "history_loader"
    bus.register(user_plugin)
    bus.register(system_plugin)

    app = FastAPI()
    app.include_router(create_plugins_router(bus=bus, plugin_root=plugin_root), prefix="/api/admin")
    client = TestClient(app)

    default_resp = client.get("/api/admin/plugins")
    assert default_resp.status_code == 200
    assert [item["name"] for item in default_resp.json()["plugins"]] == ["alpha"]

    system_resp = client.get("/api/admin/plugins?include_system=true")
    assert system_resp.status_code == 200
    names = [item["name"] for item in system_resp.json()["plugins"]]
    assert names == ["alpha", "history_loader"]
    system_plugin_payload = next(item for item in system_resp.json()["plugins"] if item["name"] == "history_loader")
    assert system_plugin_payload["locked"] is True
    assert system_plugin_payload["tier"] == "system"


def test_plugin_meta_reports_legacy_and_blocks_plugin_center(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    (plugin_root / "alpha.py").write_text("class Placeholder: pass\n", encoding="utf-8")
    (plugin_root / "alpha.toml").write_text("enabled = true\n", encoding="utf-8")

    bus = PluginBus()
    bus.register(_AlphaPlugin())

    app = FastAPI()
    app.include_router(create_plugins_router(bus=bus, plugin_root=plugin_root), prefix="/api/admin")
    client = TestClient(app)

    meta_resp = client.get("/api/admin/plugins/meta")
    assert meta_resp.status_code == 200
    meta = meta_resp.json()
    assert meta["plugin_api_version"] >= 3
    assert meta["plugin_layout_version"] >= 2
    assert meta["legacy_detected"] is True
    assert "alpha" in meta["legacy_plugins"]

    blocked_resp = client.get("/api/admin/plugins")
    assert blocked_resp.status_code == 200
    blocked_payload = blocked_resp.json()
    assert blocked_payload["blocked"] is True
    assert blocked_payload["blocked_reason"] == "legacy_single_file_detected"
    assert blocked_payload["plugins"] == []


def test_plugin_list_reports_config_status_for_user_plugin(tmp_path: Path) -> None:
    bus = PluginBus()
    plugin = _AlphaPlugin()
    bus.register(plugin)
    config_store = PluginConfigStore(tmp_path / "plugin-config.json")

    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.get("/api/admin/plugins")
    assert resp.status_code == 200
    [alpha] = resp.json()["plugins"]
    assert alpha["tier"] == "user"
    assert alpha["locked"] is False
    assert alpha["config_status"] == "ready"
    assert alpha["configurable"] is True


def test_calendar_context_settings_schema_matches_current_config(tmp_path: Path) -> None:
    from plugins.calendar_context.plugin import CalendarContextPlugin

    bus = PluginBus()
    bus.register(CalendarContextPlugin())
    config_store = PluginConfigStore(tmp_path / "plugin-config.json")

    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.get("/api/admin/plugins/calendar_context/settings")
    assert resp.status_code == 200
    properties = resp.json()["schema"]["properties"]

    assert "builtin_dataset" not in properties
    assert "extra_data_files" not in properties
    assert "builtin_birthdays_file" in properties
    assert "extra_year_files" in properties


def test_non_whitelist_system_plugin_is_downgraded_to_user() -> None:
    bus = PluginBus()
    plugin = _AlphaPlugin()
    plugin.tier = "system"
    plugin.toggle_policy = "locked"
    bus.register(plugin)

    app = FastAPI()
    app.include_router(create_plugins_router(bus=bus), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/plugins")
    assert resp.status_code == 200
    [alpha] = resp.json()["plugins"]
    assert alpha["name"] == "alpha"
    assert alpha["tier"] == "user"
    assert alpha["locked"] is False
    assert alpha["toggle_policy"] == "runtime"


def test_plugin_settings_rejects_read_only_system_plugin(tmp_path: Path) -> None:
    bus = PluginBus()
    plugin = _AlphaPlugin()
    plugin.name = "chat"
    plugin.config_spec = {"apply_mode": "read_only"}
    bus.register(plugin)
    config_store = PluginConfigStore(tmp_path / "plugin-config.json")

    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )
    client = TestClient(app)

    settings_resp = client.get("/api/admin/plugins/chat/settings")
    assert settings_resp.status_code == 200
    settings = settings_resp.json()
    assert settings["schema"] == {}
    assert settings["apply_mode"] == "read_only"

    save_resp = client.post("/api/admin/plugins/chat/settings", json={"values": {"enabled": True}})
    assert save_resp.status_code == 200
    assert save_resp.json()["ok"] is False
    assert save_resp.json()["error"] == "系统级插件配置只读"
    assert config_store.get("chat") == {}


def test_plugin_health_endpoint_exposes_soft_isolation_state() -> None:
    bus = PluginBus()
    bus._ERROR_BURST_LIMIT = 1
    bus._SOFT_ISOLATION_COOLDOWN_SECONDS = 60.0
    plugin = _FailingMessagePlugin()
    bus.register(plugin)

    asyncio.run(bus.fire_on_message(_msg_ctx(content="hello")))

    app = FastAPI()
    app.include_router(create_plugins_router(bus=bus), prefix="/api/admin")
    client = TestClient(app)

    health_resp = client.get("/api/admin/plugins/health")
    assert health_resp.status_code == 200
    [health] = health_resp.json()["plugins"]
    assert health["name"] == "failing_message"
    assert health["state"] == "throttled"
    assert health["cooldown_reason"] == "error_burst"
    assert health["cooldown_remaining_seconds"] > 0


def test_plugin_settings_endpoint_persists_values(tmp_path: Path) -> None:
    bus = PluginBus()
    plugin = _AlphaPlugin()
    bus.register(plugin)
    config_store = PluginConfigStore(tmp_path / "plugin-config.json")

    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=bus,
            plugin_config_store=config_store,
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    initial_resp = client.get("/api/admin/plugins/alpha/settings")
    assert initial_resp.status_code == 200
    initial = initial_resp.json()
    assert initial["schema"]["type"] == "object"
    assert initial["effective_values"]["enabled"] is False
    assert initial["values"] == {}

    save_resp = client.post("/api/admin/plugins/alpha/settings", json={"values": {"enabled": True}})
    assert save_resp.status_code == 200
    payload = save_resp.json()
    assert payload["ok"] is True
    assert payload["settings"]["values"]["enabled"] is True
    assert config_store.get("alpha") == {"enabled": True}

    detail_resp = client.get("/api/admin/plugins/alpha")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["settings"]["values"]["enabled"] is True


def test_plugin_index_endpoint_reports_local_source_and_compatibility(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    alpha_path = plugin_root / "alpha.py"
    alpha_path.write_text("class Placeholder: pass\n", encoding="utf-8")
    alpha_manifest = plugin_root / "alpha.json"
    alpha_manifest.write_text(json.dumps({
        "name": "alpha",
        "version": "1.0.0",
        "min_omubot_version": "999.0.0",
    }), encoding="utf-8")
    alpha_hash = hashlib.sha256(alpha_path.read_bytes()).hexdigest()
    alpha_manifest_hash = hashlib.sha256(alpha_manifest.read_bytes()).hexdigest()
    (plugin_root / "alpha.sig").write_text(json.dumps({
        "scheme": "sha256",
        "signer": "local-ci",
        "key_id": "dev-key",
        "signed_at": "2026-05-07T10:00:00+08:00",
        "entry_sha256": alpha_hash,
        "manifest_sha256": alpha_manifest_hash,
        "source": {
            "origin": "trusted",
            "entry_path": "plugins/alpha.py",
        },
    }), encoding="utf-8")

    orphan_dir = plugin_root / "orphan"
    orphan_dir.mkdir()
    (orphan_dir / "plugin.py").write_text("class Placeholder: pass\n", encoding="utf-8")
    (orphan_dir / "plugin.json").write_text("{invalid json", encoding="utf-8")

    (plugin_root / "draft.py").write_text("class Placeholder: pass\n", encoding="utf-8")
    (plugin_root / "signedbad.py").write_text("class Placeholder: pass\n", encoding="utf-8")
    (plugin_root / "signedbad.sig").write_text(json.dumps({
        "scheme": "sha256",
        "signer": "local-ci",
        "entry_sha256": "deadbeef",
        "source": {
            "origin": "trusted",
            "entry_path": "plugins/signedbad.py",
        },
    }), encoding="utf-8")

    bus = PluginBus()
    bus.register(_AlphaPlugin())

    app = FastAPI()
    app.include_router(create_plugins_router(bus=bus, plugin_root=plugin_root), prefix="/api/admin")
    client = TestClient(app)

    meta_resp = client.get("/api/admin/plugins/meta")
    assert meta_resp.status_code == 200
    meta = meta_resp.json()
    assert meta["legacy_detected"] is True

    list_resp = client.get("/api/admin/plugins")
    assert list_resp.status_code == 200
    blocked = list_resp.json()
    assert blocked["blocked"] is True
    assert blocked["blocked_reason"] == "legacy_single_file_detected"
    assert blocked["plugins"] == []

    index_resp = client.get("/api/admin/plugins/index")
    assert index_resp.status_code == 200
    payload = index_resp.json()
    assert payload["install_policy"]["remote_install_enabled"] is False
    assert payload["summary"]["indexed_count"] == 4
    assert payload["summary"]["loaded_count"] == 1
    assert payload["summary"]["not_loaded_count"] == 3
    assert payload["summary"]["compatibility_issue_count"] == 1
    assert payload["summary"]["manifest_invalid_count"] == 1
    assert payload["summary"]["ready_to_load_count"] == 0
    assert payload["summary"]["blocked_count"] == 4
    assert payload["summary"]["attention_count"] == 0
    assert payload["summary"]["signature_verified_count"] == 1
    assert payload["summary"]["signature_issue_count"] == 2

    orphan = next(item for item in payload["entries"] if item["name"] == "orphan")
    assert orphan["loaded"] is False
    assert orphan["manifest_status"] == "invalid"
    assert orphan["governance_status"] == "blocked"
    assert orphan["action_hint"]
    assert orphan["warnings"]

    draft = next(item for item in payload["entries"] if item["name"] == "draft")
    assert draft["loaded"] is False
    assert draft["manifest_status"] == "missing"
    assert draft["governance_status"] == "blocked"
    assert draft["blocked_reason"] == "legacy_single_file_unsupported"
    assert draft["action_hint"]

    signedbad = next(item for item in payload["entries"] if item["name"] == "signedbad")
    assert signedbad["signature_status"] == "mismatch"
    assert signedbad["source_attestation_status"] == "mismatch"
    assert signedbad["governance_status"] == "blocked"

    detail_resp = client.get("/api/admin/plugins/alpha")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["blocked"] is True
    assert detail["blocked_reason"] == "legacy_single_file_detected"

    alpha_entry = next(item for item in payload["entries"] if item["name"] == "alpha")
    assert alpha_entry["min_omubot_version"] == "999.0.0"
    assert alpha_entry["compatibility_status"] == "incompatible"
    assert alpha_entry["governance_status"] == "blocked"
    assert alpha_entry["signature_status"] == "verified"
    assert alpha_entry["source_attestation_status"] == "mismatch"
    assert alpha_entry["signature_signer"] == "local-ci"


def test_provider_and_protocol_endpoints() -> None:
    async def _provider_tester(profile):
        return {"text": f"OK {profile.model}"}

    llm_client = SimpleNamespace(provider_rate_limit_payload=lambda: {
        "profiles": {
            "slang": {
                "profile": "slang",
                "status": "cooldown",
                "cooldown_remaining_seconds": 4.5,
                "cooldown_until": 1_746_563_204.5,
                "total_calls": 2,
                "successes": 1,
                "failures": 1,
                "rate_limited": 1,
                "blocked_calls": 0,
                "consecutive_rate_limits": 1,
                "last_task": "slang",
                "last_error": "HTTP 429",
                "last_success_at": 1_746_563_200.0,
                "last_limited_at": 1_746_563_201.0,
            },
        },
        "tasks": {},
    })

    config = BotConfig.model_validate({
        "llm": {
            "base_url": "https://legacy.example/anthropic",
            "api_key": "sk-test-secret",
            "model": "legacy-main",
            "profiles": {
                "slang": {
                    "api_format": "openai",
                    "base_url": "https://openai.example/v1",
                    "model": "slang-mini",
                },
            },
        },
        "napcat": {"api_url": "http://napcat:29300"},
    })

    app = FastAPI()
    protocol_ctx = SimpleNamespace(protocol_connections=ProtocolConnectionHistory())
    app.include_router(
        create_providers_router(config=config, llm_client=llm_client, provider_tester=_provider_tester),
        prefix="/api/admin",
    )
    app.include_router(create_protocol_router(config=config, ctx=protocol_ctx, bot=_DummyBot()), prefix="/api/admin")
    client = TestClient(app)

    providers_resp = client.get("/api/admin/providers")
    assert providers_resp.status_code == 200
    profiles = {item["name"]: item for item in providers_resp.json()["profiles"]}
    assert profiles["main"]["model"] == "legacy-main"
    assert profiles["slang"]["api_format"] == "openai"
    assert profiles["main"]["api_key_mask"].startswith("sk-")
    assert profiles["slang"]["rate_limit"]["status"] == "cooldown"
    api_format_options = {item["value"] for item in providers_resp.json()["api_format_options"]}
    assert api_format_options >= {"anthropic", "openai", "deepseek"}
    assert providers_resp.json()["rate_limits"]["profiles"]["slang"]["rate_limited"] == 1
    task_profiles = {item["task"]: item["profile"] for item in providers_resp.json()["task_profiles"]}
    assert task_profiles["slang"] == "slang"
    assert task_profiles["reply_gate"] == "main"

    provider_test_resp = client.post("/api/admin/providers/slang/test")
    assert provider_test_resp.status_code == 200
    provider_test = provider_test_resp.json()
    assert provider_test["ok"] is True
    assert provider_test["profile"] == "slang"
    assert provider_test["model"] == "slang-mini"
    assert provider_test["api_format"] == "openai"
    assert provider_test["text_preview"].startswith("OK")
    assert provider_test["usage_summary"] == {}

    protocol_health_resp = client.get("/api/admin/protocol/health")
    assert protocol_health_resp.status_code == 200
    health_payload = protocol_health_resp.json()
    assert health_payload["compatibility"][0]["key"] == "onebot_v11_http_ws"
    assert health_payload["connection"]["current_status"] == "connected"

    compatibility_resp = client.get("/api/admin/protocol/compatibility")
    assert compatibility_resp.status_code == 200
    compatibility = compatibility_resp.json()
    assert compatibility["fallback_target"] == "llonebot"
    assert {item["key"] for item in compatibility["items"]} >= {"onebot_v11_http_ws", "group_history"}

    protocol_resp = client.post("/api/admin/protocol/probe")
    assert protocol_resp.status_code == 200
    protocol = protocol_resp.json()
    assert protocol["adapter"] == "napcat"
    assert protocol["connected_bots"] == 1
    assert protocol["compatibility"][0]["napcat"] == "supported"
    assert protocol["connection"]["connected_bots"] == 1
    statuses = {item["key"]: item["status"] for item in protocol["capabilities"]}
    assert statuses["bot_connection"] == "ok"
    assert statuses["group_list"] == "ok"

    connections_resp = client.get("/api/admin/protocol/connections")
    assert connections_resp.status_code == 200
    connections = connections_resp.json()
    assert connections["summary"]["current_status"] == "connected"
    assert connections["events"][0]["status"] == "connected"


def test_provider_selection_persists_and_hot_switches(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "llm": {
            "base_url": "https://legacy.example/anthropic",
            "api_key": "sk-test",
            "model": "legacy-main",
            "default_profile": "main",
            "profiles": {
                "alt": {
                    "api_format": "openai",
                    "base_url": "https://alt.example/v1",
                    "model": "alt-main",
                },
                "slang": {
                    "api_format": "openai",
                    "base_url": "https://slang.example/v1",
                    "model": "slang-mini",
                },
            },
            "task_profiles": {
                "slang": "slang",
            },
        },
    }), encoding="utf-8")
    config = BotConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))

    class _HotSwitchClient:
        def __init__(self) -> None:
            self.names: dict[str, str] = {}
            self.models: dict[str, str] = {}

        def provider_rate_limit_payload(self) -> dict:
            return {"profiles": {}, "tasks": {}}

        def set_task_profiles(self, task_profiles: dict, task_profile_names: dict[str, str]) -> None:
            self.names = dict(task_profile_names)
            self.models = {task: profile.model for task, profile in task_profiles.items()}

    hot_client = _HotSwitchClient()
    app = FastAPI()
    app.include_router(
        create_providers_router(config=config, config_path=str(config_path), llm_client=hot_client),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.post("/api/admin/providers/selection", json={
        "default_profile": "alt",
        "task_profiles": {
            "thinker": "alt",
            "reply_gate": "alt",
            "compact": "main",
            "slang": "slang",
            "vision": "alt",
        },
    })
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["runtime_applied"] is True
    assert config.llm.default_profile == "alt"
    assert config.llm.task_profiles["main"] == "alt"
    assert hot_client.names["main"] == "alt"
    assert hot_client.models["main"] == "alt-main"
    assert hot_client.names["slang"] == "slang"
    assert hot_client.names["reply_gate"] == "alt"

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["llm"]["default_profile"] == "alt"
    assert written["llm"]["task_profiles"]["main"] == "alt"
    assert written["llm"]["task_profiles"]["reply_gate"] == "alt"
    assert written["llm"]["task_profiles"]["compact"] == "main"

    providers_resp = client.get("/api/admin/providers")
    profiles = {item["name"]: item for item in providers_resp.json()["profiles"]}
    assert profiles["alt"]["active"] is True
    task_profiles = {item["task"]: item["profile"] for item in providers_resp.json()["task_profiles"]}
    assert task_profiles["main"] == "alt"
    assert task_profiles["compact"] == "main"

    invalid_resp = client.post("/api/admin/providers/selection", json={
        "default_profile": "missing",
        "task_profiles": {},
    })
    assert invalid_resp.status_code == 200
    invalid_payload = invalid_resp.json()
    assert invalid_payload["ok"] is False
    assert "missing" in invalid_payload["error"]


def test_provider_definition_editor_persists_profiles_and_sanitizes_selection(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "llm": {
            "base_url": "https://legacy.example/anthropic",
            "api_key": "sk-legacy-main",
            "model": "legacy-main",
            "max_tokens": 1024,
            "default_profile": "alt",
            "profiles": {
                "alt": {
                    "api_format": "openai",
                    "base_url": "https://alt.example/v1",
                    "api_key": "sk-alt-secret",
                    "model": "alt-main",
                    "capabilities": ["chat", "tools"],
                },
                "slang": {
                    "api_format": "openai",
                    "base_url": "https://slang.example/v1",
                    "model": "slang-mini",
                },
            },
            "task_profiles": {
                "main": "alt",
                "thinker": "alt",
                "slang": "slang",
                "vision": "alt",
            },
        },
    }), encoding="utf-8")
    config = BotConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))

    class _HotSwitchClient:
        def __init__(self) -> None:
            self.names: dict[str, str] = {}
            self.models: dict[str, str] = {}

        def provider_rate_limit_payload(self) -> dict:
            return {"profiles": {}, "tasks": {}}

        def set_task_profiles(self, task_profiles: dict, task_profile_names: dict[str, str]) -> None:
            self.names = dict(task_profile_names)
            self.models = {task: profile.model for task, profile in task_profiles.items()}

    hot_client = _HotSwitchClient()
    app = FastAPI()
    app.include_router(
        create_providers_router(config=config, config_path=str(config_path), llm_client=hot_client),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.post("/api/admin/providers/definitions", json={
        "profiles": [
            {
                "name": "main",
                "api_format": "openai",
                "base_url": "https://main.example/v1",
                "api_key_mode": "replace",
                "api_key": "sk-main-new",
                "model": "main-v2",
                "max_tokens": 2048,
                "capabilities": ["chat", "tools", "thinking"],
            },
            {
                "name": "alt",
                "api_format": "openai",
                "base_url": "https://alt.example/v1",
                "api_key_mode": "keep",
                "model": "alt-v2",
                "max_tokens": 1536,
                "capabilities": ["chat", "tools", "compact"],
            },
            {
                "name": "visionx",
                "api_format": "openai",
                "base_url": "https://vision.example/v1",
                "api_key_mode": "clear",
                "model": "vision-plus",
                "max_tokens": 4096,
                "capabilities": ["chat", "vision", "json"],
            },
        ],
    })
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["runtime_applied"] is True
    assert config.llm.base_url == "https://main.example/v1"
    assert config.llm.api_key == "sk-main-new"
    assert config.llm.profiles["alt"].api_key == "sk-alt-secret"
    assert config.llm.task_profiles["slang"] == "alt"
    assert hot_client.names["slang"] == "alt"
    assert hot_client.models["main"] == "alt-v2"

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["llm"]["base_url"] == "https://main.example/v1"
    assert written["llm"]["api_key"] == "sk-main-new"
    assert written["llm"]["profiles"]["alt"]["api_key"] == "sk-alt-secret"
    assert written["llm"]["task_profiles"]["slang"] == "alt"
    assert "slang" not in written["llm"]["profiles"]
    assert written["llm"]["profiles"]["visionx"]["api_key"] == ""

    providers_resp = client.get("/api/admin/providers")
    assert providers_resp.status_code == 200
    profiles = {item["name"]: item for item in providers_resp.json()["profiles"]}
    assert "visionx" in profiles
    assert "slang" not in profiles
    assert profiles["alt"]["model"] == "alt-v2"


def test_protocol_trace_store_and_endpoint() -> None:
    trace_store = ProtocolTraceStore(max_items=10)
    bot = _TraceBot()
    assert trace_store.wrap_bot(bot) is True
    assert trace_store.wrap_bot(bot) is False

    asyncio.run(bot.call_api("get_group_list", group_id=123))
    with contextlib.suppress(RuntimeError):
        asyncio.run(bot.call_api("explode"))

    ctx = SimpleNamespace(protocol_trace=trace_store)
    app = FastAPI()
    app.include_router(create_protocol_router(ctx=ctx, bot=bot), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/protocol/traces")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["summary"]["ok"] == 1
    assert payload["summary"]["failed"] == 1
    assert payload["traces"][0]["status"] == "failed"

    health_resp = client.get("/api/admin/protocol/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["trace_summary"]["failed"] == 1


def test_protocol_mock_no_bot_contract() -> None:
    history = ProtocolConnectionHistory(max_items=10)
    ctx = SimpleNamespace(protocol_connections=history)
    app = FastAPI()
    app.include_router(create_protocol_router(ctx=ctx, bot=None), prefix="/api/admin")
    client = TestClient(app)

    health_resp = client.get("/api/admin/protocol/health")
    assert health_resp.status_code == 200
    health = health_resp.json()
    assert health["connected_bots"] == 0
    assert health["connection"]["current_status"] == "disconnected"
    assert {item["key"]: item["status"] for item in health["capabilities"]}["bot_connection"] == "failed"

    probe_resp = client.post("/api/admin/protocol/probe")
    assert probe_resp.status_code == 200
    probe = probe_resp.json()
    assert probe["connected_bots"] == 0
    assert {item["key"]: item["status"] for item in probe["capabilities"]}["bot_connection"] == "failed"
    assert probe["compatibility"][0]["key"] == "onebot_v11_http_ws"


def test_protocol_mock_partial_and_failing_bot_contracts() -> None:
    partial_history = ProtocolConnectionHistory(max_items=10)
    partial_ctx = SimpleNamespace(protocol_connections=partial_history)
    partial_app = FastAPI()
    partial_app.include_router(
        create_protocol_router(ctx=partial_ctx, bot=_MissingProtocolMethodBot()),
        prefix="/api/admin",
    )
    partial_client = TestClient(partial_app)

    partial_resp = partial_client.post("/api/admin/protocol/probe")
    assert partial_resp.status_code == 200
    partial = partial_resp.json()
    partial_statuses = {item["key"]: item["status"] for item in partial["capabilities"]}
    assert partial_statuses["bot_connection"] == "ok"
    assert partial_statuses["login_info"] == "ok"
    assert partial_statuses["group_list"] == "failed"
    assert partial["connection"]["last_error"] == "method_missing"

    failing_history = ProtocolConnectionHistory(max_items=10)
    failing_ctx = SimpleNamespace(protocol_connections=failing_history)
    failing_app = FastAPI()
    failing_app.include_router(
        create_protocol_router(ctx=failing_ctx, bot=_FailingProtocolBot()),
        prefix="/api/admin",
    )
    failing_client = TestClient(failing_app)

    failing_resp = failing_client.post("/api/admin/protocol/probe")
    assert failing_resp.status_code == 200
    failing = failing_resp.json()
    failing_statuses = {item["key"]: item["status"] for item in failing["capabilities"]}
    assert failing_statuses["bot_connection"] == "ok"
    assert failing_statuses["login_info"] == "failed"
    assert failing_statuses["group_list"] == "failed"
    assert "login unavailable" in failing["connection"]["last_error"]
    assert "group list timeout" in failing["connection"]["last_error"]

    connections_resp = failing_client.get("/api/admin/protocol/connections")
    assert connections_resp.status_code == 200
    events = connections_resp.json()["events"]
    assert events[0]["kind"] == "error"
    assert events[0]["source"] == "protocol_probe"


def test_protocol_trace_mock_redacts_limits_and_records_failures() -> None:
    trace_store = ProtocolTraceStore(max_items=2)
    bot = _TraceBot()
    assert trace_store.wrap_bot(bot) is True

    asyncio.run(bot.call_api("first", access_token="secret-token", payload=b"abc"))
    asyncio.run(bot.call_api("second", api_key="sk-secret"))
    with contextlib.suppress(RuntimeError):
        asyncio.run(bot.call_api("explode", password="secret-password"))

    payload = trace_store.as_payload(limit=10)
    assert payload["max_items"] == 10
    assert len(payload["traces"]) == 3
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["failed"] == 1
    assert payload["summary"]["last_error"] == "boom"
    latest = payload["traces"][0]
    assert latest["action"] == "explode"
    assert latest["params"]["password"] == "***"
    older = payload["traces"][1]
    assert older["params"]["api_key"] == "***"
    oldest = payload["traces"][2]
    assert oldest["params"]["access_token"] == "***"
    assert oldest["params"]["payload"] == "<bytes:3>"


def test_protocol_connection_history_records_changes_and_errors() -> None:
    history = ProtocolConnectionHistory(max_items=10)

    disconnected = history.record_snapshot(connected_bots=0, self_ids=[], source="health")
    assert disconnected["current_status"] == "disconnected"

    connected = history.record_snapshot(connected_bots=1, self_ids=["10000"], source="connect")
    assert connected["current_status"] == "connected"
    assert connected["last_recovery_seconds"] is not None

    errored = history.record_error("get_login_info failed", source="probe")
    assert errored["last_error"] == "get_login_info failed"

    payload = history.as_payload()
    assert [event["kind"] for event in payload["events"][:3]] == ["error", "state", "state"]
    assert payload["events"][0]["source"] == "probe"


def test_system_runtime_errors_endpoint_and_health(tmp_path: Path) -> None:
    store = RuntimeErrorStore(max_events=20, max_groups=10)
    store.record(level="WARNING", channel="slang", logger_name="tests", message="extract timeout", ts=1000)
    store.record(level="ERROR", channel="slang", logger_name="tests", message="extract timeout", ts=1001)
    store.record(level="ERROR", channel="llm", logger_name="tests", message="provider failed", ts=1002)

    ctx = SimpleNamespace(runtime_errors=store, storage_dir=tmp_path)

    app = FastAPI()
    app.include_router(create_system_router(ctx=ctx), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/system/errors", params={"event_limit": 2, "group_limit": 2})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["warnings"] == 1
    assert payload["summary"]["errors"] == 2
    assert len(payload["events"]) == 2
    assert len(payload["groups"]) == 2

    health_resp = client.get("/api/admin/services/health")
    assert health_resp.status_code == 200
    health_payload = health_resp.json()
    runtime_service = next(item for item in health_payload["services"] if item["id"] == "runtime_errors")
    assert runtime_service["status"] == "warning"
    assert runtime_service["meta"]["errors"] == 2
    assert health_payload["alerts"]
    assert health_payload["policy"]["mode"] == "thresholded"
    assert health_payload["maintenance_window"]["recommended"] is True


def test_system_services_health_endpoint(tmp_path: Path) -> None:
    for name in ("messages.db", "memory_cards.db", "slang.db"):
        with sqlite3.connect(tmp_path / name) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS marker (id INTEGER PRIMARY KEY)")

    config = BotConfig.model_validate({
        "llm": {
            "base_url": "https://llm.example/v1",
            "api_key": "sk-test",
            "model": "omubot-main",
        },
        "napcat": {"api_url": "http://napcat.example"},
    })
    bus = PluginBus()
    bus.register(_AlphaPlugin())
    runtime_errors = RuntimeErrorStore()
    ctx = SimpleNamespace(
        storage_dir=tmp_path,
        bus=bus,
        llm_client=object(),
        protocol_trace=ProtocolTraceStore(),
        protocol_connections=ProtocolConnectionHistory(),
        runtime_errors=runtime_errors,
        msg_log=SimpleNamespace(_db_path=str(tmp_path / "messages.db")),
        card_store=SimpleNamespace(_db_path=str(tmp_path / "memory_cards.db")),
        slang_store=SimpleNamespace(_db_path=str(tmp_path / "slang.db"), initialized=False),
        short_term=SimpleNamespace(_store={"group_1": object()}),
        retrieval=SimpleNamespace(semantic_status=lambda: {
            "enabled": True,
            "requested_backend": "embedding",
            "active_backend": "ngram",
            "queries": 4,
            "hits": 2,
            "fallbacks": 1,
            "errors": 1,
            "last_error": "embedding backend unavailable",
        }),
    )

    app = FastAPI()
    app.include_router(create_system_router(config=config, ctx=ctx, bot=_DummyBot()), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/services/health")
    assert resp.status_code == 200
    payload = resp.json()
    service_ids = {item["id"] for item in payload["services"]}
    assert {"llm", "plugin_bus", "runtime_errors", "sqlite", "memory", "slang", "napcat"} <= service_ids
    sqlite_service = next(item for item in payload["services"] if item["id"] == "sqlite")
    assert sqlite_service["status"] == "ok"
    runtime_service = next(item for item in payload["services"] if item["id"] == "runtime_errors")
    assert runtime_service["status"] == "ok"
    memory_service = next(item for item in payload["services"] if item["id"] == "memory")
    assert memory_service["status"] == "warning"
    assert memory_service["meta"]["semantic"]["queries"] == 4
    assert memory_service["meta"]["semantic"]["hits"] == 2
    assert memory_service["meta"]["semantic"]["hit_rate"] == 0.5
    assert payload["summary"]["ok"] >= 3
    assert payload["alerts"] == []
    assert payload["policy"]["suppressed_count"] >= 2
    assert payload["maintenance_window"]["recommended"] is False
    assert payload["maintenance_window"]["restart_recommended"] is False


def test_system_services_health_warns_on_missing_archive_backfill(tmp_path: Path) -> None:
    from services.health import collect_service_health

    with sqlite3.connect(tmp_path / "messages.db") as conn:
        conn.execute(
            """CREATE TABLE group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                role TEXT NOT NULL,
                speaker TEXT,
                content_text TEXT,
                content_json TEXT,
                message_id INTEGER,
                created_at REAL NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE conversation_messages (
                message_pk INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_row_id INTEGER
            )"""
        )
        conn.execute(
            """INSERT INTO group_messages
               (group_id, role, speaker, content_text, content_json, message_id, created_at)
               VALUES ('100', 'user', 'A(1)', 'missing', NULL, 1, 1.0)"""
        )
    for name in ("memory_cards.db", "slang.db"):
        with sqlite3.connect(tmp_path / name) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS marker (id INTEGER PRIMARY KEY)")

    ctx = SimpleNamespace(
        storage_dir=tmp_path,
        bus=PluginBus(),
        llm_client=object(),
        protocol_trace=ProtocolTraceStore(),
        protocol_connections=ProtocolConnectionHistory(),
        runtime_errors=RuntimeErrorStore(),
        msg_log=SimpleNamespace(_db_path=str(tmp_path / "messages.db")),
        card_store=SimpleNamespace(_db_path=str(tmp_path / "memory_cards.db")),
        slang_store=SimpleNamespace(_db_path=str(tmp_path / "slang.db")),
    )
    ctx.bus.register(_AlphaPlugin())
    config = BotConfig.model_validate({
        "llm": {
            "base_url": "https://llm.example/v1",
            "api_key": "sk-test",
            "model": "omubot-main",
        },
        "napcat": {"api_url": "http://napcat.example"},
    })

    payload = asyncio.run(collect_service_health(ctx=ctx, config=config, bot=_DummyBot()))
    sqlite_service = next(item for item in payload["services"] if item["id"] == "sqlite")
    assert sqlite_service["status"] == "warning"
    assert sqlite_service["meta"]["messages_archive"]["missing_archive_count"] == 1
    assert payload["alerts"] == []


def test_system_services_health_allows_archive_extra_rows(tmp_path: Path) -> None:
    from services.health import collect_service_health

    with sqlite3.connect(tmp_path / "messages.db") as conn:
        conn.execute(
            """CREATE TABLE group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                role TEXT NOT NULL,
                speaker TEXT,
                content_text TEXT,
                content_json TEXT,
                message_id INTEGER,
                created_at REAL NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE conversation_messages (
                message_pk INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_row_id INTEGER
            )"""
        )
        conn.execute(
            """INSERT INTO group_messages
               (group_id, role, speaker, content_text, content_json, message_id, created_at)
               VALUES ('100', 'user', 'A(1)', 'synced', NULL, 1, 1.0)"""
        )
        conn.execute("INSERT INTO conversation_messages (legacy_row_id) VALUES (1)")
        conn.execute("INSERT INTO conversation_messages (legacy_row_id) VALUES (999)")
    for name in ("memory_cards.db", "slang.db"):
        with sqlite3.connect(tmp_path / name) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS marker (id INTEGER PRIMARY KEY)")

    ctx = SimpleNamespace(
        storage_dir=tmp_path,
        bus=PluginBus(),
        llm_client=object(),
        protocol_trace=ProtocolTraceStore(),
        protocol_connections=ProtocolConnectionHistory(),
        runtime_errors=RuntimeErrorStore(),
        msg_log=SimpleNamespace(_db_path=str(tmp_path / "messages.db")),
        card_store=SimpleNamespace(_db_path=str(tmp_path / "memory_cards.db")),
        slang_store=SimpleNamespace(_db_path=str(tmp_path / "slang.db")),
    )
    ctx.bus.register(_AlphaPlugin())
    config = BotConfig.model_validate({
        "llm": {
            "base_url": "https://llm.example/v1",
            "api_key": "sk-test",
            "model": "omubot-main",
        },
        "napcat": {"api_url": "http://napcat.example"},
    })

    payload = asyncio.run(collect_service_health(ctx=ctx, config=config, bot=_DummyBot()))
    sqlite_service = next(item for item in payload["services"] if item["id"] == "sqlite")
    assert sqlite_service["status"] == "ok"
    assert sqlite_service["meta"]["messages_archive"]["missing_archive_count"] == 0
    assert sqlite_service["meta"]["messages_archive"]["archive_extra_count"] == 1


def test_system_services_health_flags_throttled_plugin_bus(tmp_path: Path) -> None:
    config = BotConfig.model_validate({
        "llm": {
            "base_url": "https://llm.example/v1",
            "api_key": "sk-test",
            "model": "omubot-main",
        },
    })
    bus = PluginBus()
    bus._ERROR_BURST_LIMIT = 1
    bus._SOFT_ISOLATION_COOLDOWN_SECONDS = 60.0
    bus.register(_FailingMessagePlugin())
    asyncio.run(bus.fire_on_message(_msg_ctx()))

    ctx = SimpleNamespace(
        storage_dir=tmp_path,
        bus=bus,
        llm_client=object(),
        protocol_trace=ProtocolTraceStore(),
        protocol_connections=ProtocolConnectionHistory(),
        runtime_errors=RuntimeErrorStore(),
    )

    app = FastAPI()
    app.include_router(create_system_router(config=config, ctx=ctx), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/services/health")
    assert resp.status_code == 200
    payload = resp.json()
    plugin_service = next(item for item in payload["services"] if item["id"] == "plugin_bus")
    assert plugin_service["status"] == "warning"
    assert plugin_service["meta"]["throttled_plugins"] == 1
    assert plugin_service["meta"]["suppressed_calls"] == 0
    alert_sources = {item["source"] for item in payload["alerts"]}
    assert "plugin_bus" in alert_sources


def test_slang_api_lifecycle(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())
    asyncio.run(store.upsert_candidate(
        term="猫饼",
        meaning="群里说离谱但可爱的操作",
        group_id="100",
        user_id="u1",
        raw_text="猫饼",
        confidence=0.7,
    ))

    app = FastAPI()
    app.include_router(create_slang_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    terms_resp = client.get("/api/admin/slang/terms", params={"status": "candidate"})
    assert terms_resp.status_code == 200
    terms = terms_resp.json()["terms"]
    assert len(terms) == 1
    term_id = terms[0]["term_id"]

    approve_resp = client.post(f"/api/admin/slang/terms/{term_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["ok"] is True
    assert approve_resp.json()["term"]["status"] == "approved"

    settings_resp = client.post("/api/admin/slang/settings", json={
        "settings": {
            "max_injected_terms": 5,
            "group_allowlist": ["100"],
            "global_excluded_group_ids": ["200"],
        },
    })
    assert settings_resp.status_code == 200
    assert settings_resp.json()["settings"]["max_injected_terms"] == 5
    assert settings_resp.json()["settings"]["group_allowlist"] == ["100"]
    assert settings_resp.json()["settings"]["global_excluded_group_ids"] == ["200"]

    asyncio.run(store.close())


def test_slang_api_v2_bulk_merge_stats_pending_and_runs(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())
    term_a = asyncio.run(store.upsert_candidate(
        term="猫饼",
        meaning="群里说离谱但可爱的操作",
        group_id="100",
        user_id="u1",
        raw_text="猫饼",
        confidence=0.7,
    ))
    term_b = asyncio.run(store.upsert_candidate(
        term="猫猫饼",
        meaning="群里说离谱但可爱的操作",
        group_id="100",
        user_id="u2",
        raw_text="猫猫饼",
        confidence=0.6,
    ))
    asyncio.run(store.upsert_candidate(
        term="观察中",
        meaning="低频候选",
        group_id="100",
        raw_text="观察中",
        min_count=2,
        observed_count=1,
    ))
    run_id = asyncio.run(store.start_extraction_run(group_count=1))
    asyncio.run(store.finish_extraction_run(run_id, scanned_messages=3, extracted_terms=1))
    assert term_a is not None
    assert term_b is not None

    app = FastAPI()
    app.include_router(create_slang_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    bulk_resp = client.post("/api/admin/slang/terms/bulk", json={
        "action": "approve",
        "term_ids": [term_a, term_b],
    })
    assert bulk_resp.status_code == 200
    assert bulk_resp.json()["changed"] == 2

    merge_resp = client.post("/api/admin/slang/terms/merge", json={
        "target_id": term_a,
        "source_ids": [term_b],
    })
    assert merge_resp.status_code == 200
    assert merge_resp.json()["ok"] is True
    assert "猫猫饼" in merge_resp.json()["term"]["aliases"]

    recompute_resp = client.post(f"/api/admin/slang/terms/{term_a}/recompute-confidence")
    assert recompute_resp.status_code == 200
    assert recompute_resp.json()["ok"] is True
    assert "confidence_signals" in recompute_resp.json()["term"]["meta"]

    pending_resp = client.get("/api/admin/slang/pending")
    assert pending_resp.status_code == 200
    assert pending_resp.json()["total"] == 1

    runs_resp = client.get("/api/admin/slang/extract/runs")
    assert runs_resp.status_code == 200
    assert runs_resp.json()["runs"][0]["run_id"] == run_id

    stats_resp = client.get("/api/admin/slang/stats")
    assert stats_resp.status_code == 200
    assert stats_resp.json()["review"]["total_terms"] >= 2

    asyncio.run(store.close())


def test_slang_api_extract_runs_includes_abandoned_status(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())
    run_id = asyncio.run(store.start_extraction_run(group_count=1, meta={"kind": "daily_ai_review"}))
    asyncio.run(store.abandon_extraction_run(run_id, reason="daily_ai_review_timeout"))

    app = FastAPI()
    app.include_router(create_slang_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    runs_resp = client.get("/api/admin/slang/extract/runs")
    assert runs_resp.status_code == 200
    payload = runs_resp.json()["runs"]
    assert payload[0]["run_id"] == run_id
    assert payload[0]["status"] == "abandoned"
    assert payload[0]["error"] == "daily_ai_review_timeout"

    asyncio.run(store.close())


def test_slang_api_force_daily_review_uses_plugin(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())

    class _ReviewPlugin:
        def __init__(self) -> None:
            self.calls: list[tuple[str | None, bool, bool, bool, bool, str]] = []

        async def run_daily_ai_review(
            self,
            ctx: object,
            settings=None,
            group_id=None,
            review_candidates=False,
            review_all_pending=False,
            rerun_reviewed_candidates=False,
            candidate_review_filter="",
        ):
            del ctx
            self.calls.append((
                group_id,
                bool(getattr(settings, "daily_ai_review_enabled", False)),
                bool(review_candidates),
                bool(review_all_pending),
                bool(rerun_reviewed_candidates),
                str(candidate_review_filter or ""),
            ))
            return {
                "ok": True,
                "run_id": "run_force_review",
                "groups": [group_id or ""],
                "semantic_reviewed": 1,
                "semantic_approved": 0,
                "semantic_rejected": 0,
                "semantic_kept": 0,
                "semantic_no_info": 0,
                "semantic_failed": 0,
            }

    plugin = _ReviewPlugin()
    bus = SimpleNamespace(get_plugin=lambda name: plugin if name == "slang" else None)

    app = FastAPI()
    app.include_router(
        create_slang_router(ctx=SimpleNamespace(tool_registry=None), bus=bus, store=store),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.post("/api/admin/slang/review/run", json={"group_id": "100", "force": True})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["run_id"] == "run_force_review"
    assert plugin.calls == [("100", True, False, False, False, "")]

    resp = client.post(
        "/api/admin/slang/review/run",
        json={"group_id": "100", "force": True, "review_candidates": True},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["run_id"] == "run_force_review"
    assert plugin.calls[-1] == ("100", True, True, False, False, "")

    resp = client.post(
        "/api/admin/slang/review/run",
        json={
            "group_id": "100",
            "force": True,
            "review_candidates": True,
            "review_all_pending": True,
        },
    )
    assert resp.status_code == 200
    assert plugin.calls[-1] == ("100", True, True, True, False, "")

    resp = client.post(
        "/api/admin/slang/review/run",
        json={
            "group_id": "100",
            "force": True,
            "review_candidates": True,
            "rerun_reviewed_candidates": True,
        },
    )
    assert resp.status_code == 200
    assert plugin.calls[-1] == ("100", True, True, False, True, "")

    asyncio.run(store.close())


def test_slang_api_extract_run_fallback_uses_shared_quality_helpers(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())
    settings = asyncio.run(store.load_settings())
    settings.candidate_min_count = 1
    asyncio.run(store.save_settings(settings))

    class _ExtractMessageLog:
        async def list_group_ids(self) -> list[str]:
            return ["100"]

        async def query_recent(self, group_id: str, limit: int = 20) -> list[dict[str, object]]:
            assert group_id == "100"
            return [
                {
                    "role": "user",
                    "speaker": "Alice(10001)",
                    "content_text": "先来一句无关话",
                    "message_id": 1,
                    "created_at": 1.0,
                },
                {
                    "role": "user",
                    "speaker": "Bob(10002)",
                    "content_text": "abc! 继续",
                    "message_id": 2,
                    "created_at": 2.0,
                },
                {
                    "role": "user",
                    "speaker": "Carol(10003)",
                    "content_text": "末尾再补一句",
                    "message_id": 3,
                    "created_at": 3.0,
                },
            ]

    class _ExtractLLM:
        async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
            del system_blocks, messages, tools, max_tokens, thinking
            return {
                "text": (
                    '{"terms":[{"term":"abc","meaning":"群里短缩写",'
                    '"aliases":[],"evidence":"abc! 继续","confidence":0.78,'
                    '"reason":"群内解释","repeat_policy":"understand_only"}]}'
                )
            }

    app = FastAPI()
    app.include_router(
        create_slang_router(
            ctx=SimpleNamespace(msg_log=_ExtractMessageLog()),
            llm_client=_ExtractLLM(),
            store=store,
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    resp = client.post("/api/admin/slang/extract/run", json={"group_id": "100", "limit": 20})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["candidates"] == 1

    terms, total = asyncio.run(store.list_terms(group_id="100"))
    assert total == 1
    [term] = terms
    assert term.term == "abc"
    assert term.usage_count == 1
    assert term.unique_users == ["10002"]

    observations = asyncio.run(store.list_observations(term.term_id))
    assert len(observations) == 1
    assert observations[0].message_id == 2
    assert observations[0].raw_text == "abc! 继续"
    assert observations[0].user_id == "10002"

    asyncio.run(store.close())


def test_slang_api_debug_message_seed_and_delete(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())

    class _DebugDB:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def execute(self, sql: str, params: tuple[object, ...]) -> None:
            self.calls.append((sql, params))

        async def commit(self) -> None:
            self.calls.append(("COMMIT", ()))

    class _DebugMessageLog:
        def __init__(self) -> None:
            self.records: list[dict[str, object]] = []
            self._db = _DebugDB()

        async def record(self, **kwargs):
            self.records.append(kwargs)

    msg_log = _DebugMessageLog()

    app = FastAPI()
    app.include_router(
        create_slang_router(ctx=SimpleNamespace(msg_log=msg_log), store=store),
        prefix="/api/admin",
    )
    client = TestClient(app)

    seed_resp = client.post("/api/admin/slang/debug/message/seed", json={
        "group_id": "100",
        "role": "user",
        "speaker": "SmokeUser(999000)",
        "content_text": "烟雾测试消息",
        "message_id": 123456,
    })
    assert seed_resp.status_code == 200
    assert seed_resp.json()["ok"] is True
    assert msg_log.records == [{
        "group_id": "100",
        "role": "user",
        "speaker": "SmokeUser(999000)",
        "content_text": "烟雾测试消息",
        "content_json": None,
        "message_id": 123456,
    }]

    delete_resp = client.post("/api/admin/slang/debug/message/delete", json={
        "group_id": "100",
        "content_text": "烟雾测试消息",
        "message_id": 123456,
    })
    assert delete_resp.status_code == 200
    assert delete_resp.json()["ok"] is True
    assert msg_log._db.calls[0][0].startswith("DELETE FROM group_messages")
    assert msg_log._db.calls[0][1] == ("100", "烟雾测试消息", 123456)
    assert msg_log._db.calls[1][0] == "COMMIT"

    asyncio.run(store.close())


def test_slang_api_debug_pending_seed_and_delete(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())

    app = FastAPI()
    app.include_router(create_slang_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    seed_resp = client.post("/api/admin/slang/debug/pending/seed", json={
        "group_id": "100",
        "term": "烟雾复核测试",
        "meaning": "自动化烟雾测试样本",
        "aliases": ["烟雾测试"],
        "count": 100,
        "confidence": 0.99,
        "evidence": "烟雾复核测试 这是临时烟雾测试样本",
        "reason": "semantic_smoke_seed",
    })
    assert seed_resp.status_code == 200
    payload = seed_resp.json()
    assert payload["ok"] is True
    pending_id = payload["pending_id"]

    pending_resp = client.get("/api/admin/slang/pending", params={"group_id": "100"})
    assert pending_resp.status_code == 200
    pending = pending_resp.json()["pending"]
    assert any(item["pending_id"] == pending_id for item in pending)

    delete_resp = client.post("/api/admin/slang/debug/pending/delete", json={
        "pending_id": pending_id,
        "group_id": "100",
        "term": "烟雾复核测试",
    })
    assert delete_resp.status_code == 200
    assert delete_resp.json()["ok"] is True

    pending_resp = client.get("/api/admin/slang/pending", params={"group_id": "100"})
    assert pending_resp.status_code == 200
    assert all(item["pending_id"] != pending_id for item in pending_resp.json()["pending"])

    asyncio.run(store.close())


def test_slang_api_global_scan_creates_candidate_only(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())
    for group_id in ["100", "200", "300"]:
        term_id = asyncio.run(store.upsert_candidate(
            term="云猫饼",
            meaning="远程围观离谱但可爱的事情",
            group_id=group_id,
            raw_text="云猫饼",
            confidence=0.7,
        ))
        assert term_id is not None

    app = FastAPI()
    app.include_router(create_slang_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    scan_resp = client.post("/api/admin/slang/global/scan", json={"min_groups": 3})
    assert scan_resp.status_code == 200
    assert scan_resp.json()["created"] == 1

    terms_resp = client.get("/api/admin/slang/terms", params={"scope": "global", "status": "candidate"})
    assert terms_resp.status_code == 200
    terms = terms_resp.json()["terms"]
    assert len(terms) == 1
    assert terms[0]["scope"] == "global"
    assert terms[0]["status"] == "candidate"

    asyncio.run(store.close())


def test_slang_api_manual_create_term(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())

    app = FastAPI()
    app.include_router(create_slang_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    create_resp = client.post("/api/admin/slang/terms/create", json={
        "term": "猫饼",
        "meaning": "群里说离谱但可爱的操作",
        "aliases": ["猫猫饼"],
        "scope": "group",
        "group_id": "100",
        "status": "approved",
        "repeat_policy": "allow_rephrase",
        "notes": "人工录入",
        "evidence": "这也太猫饼了",
    })
    assert create_resp.status_code == 200
    payload = create_resp.json()
    assert payload["ok"] is True
    assert payload["term"]["source"] == "manual"
    assert payload["term"]["status"] == "approved"
    assert payload["term"]["confidence"] >= 0.8

    terms_resp = client.get("/api/admin/slang/terms", params={"group_id": "100", "status": "approved"})
    assert terms_resp.status_code == 200
    terms = terms_resp.json()["terms"]
    assert len(terms) == 1
    assert terms[0]["term"] == "猫饼"

    duplicate = client.post("/api/admin/slang/terms/create", json={
        "term": "猫猫饼",
        "meaning": "重复别名",
        "scope": "group",
        "group_id": "100",
    })
    assert duplicate.status_code == 200
    assert duplicate.json()["ok"] is False

    asyncio.run(store.close())


def test_slang_api_ai_review_filters_and_actions(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())
    term_id = asyncio.run(store.upsert_ai_approved_term(
        term="猫饼",
        meaning="网络梗，用来形容离谱但可爱的操作",
        group_id="100",
        raw_text="猫饼太典了",
        confidence=0.9,
        reason="搜索结果和群内证据一致",
        meta={"search_evidence": "猫饼是什么梗"},
    ))
    assert term_id is not None

    app = FastAPI()
    app.include_router(create_slang_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    needs_resp = client.get("/api/admin/slang/terms", params={"review_filter": "needs_human_review"})
    assert needs_resp.status_code == 200
    assert needs_resp.json()["total"] == 1
    assert needs_resp.json()["terms"][0]["source"] == "ai_auto_review"

    human_resp = client.post(f"/api/admin/slang/terms/{term_id}/human-approve")
    assert human_resp.status_code == 200
    human_payload = human_resp.json()
    assert human_payload["ok"] is True
    assert human_payload["term"]["status"] == "approved"
    assert human_payload["term"]["meta"]["human_reviewed"] is True

    reviewed_resp = client.get("/api/admin/slang/terms", params={"review_filter": "human_reviewed"})
    assert reviewed_resp.status_code == 200
    assert reviewed_resp.json()["total"] == 1

    returned_resp = client.post(f"/api/admin/slang/terms/{term_id}/return-candidate")
    assert returned_resp.status_code == 200
    assert returned_resp.json()["term"]["status"] == "candidate"

    deny_resp = client.post(f"/api/admin/slang/terms/{term_id}/deny")
    assert deny_resp.status_code == 200
    assert deny_resp.json()["term"]["status"] == "muted"
    assert deny_resp.json()["term"]["meta"]["review_decision"] == "denied"

    rejected_resp = client.get("/api/admin/slang/terms", params={"review_filter": "ai_rejected"})
    assert rejected_resp.status_code == 200
    assert rejected_resp.json()["total"] == 0

    returned_again_resp = client.post(f"/api/admin/slang/terms/{term_id}/return-candidate")
    assert returned_again_resp.status_code == 200
    returned_again_payload = returned_again_resp.json()
    assert returned_again_payload["ok"] is True
    assert returned_again_payload["term"]["status"] == "candidate"
    assert returned_again_payload["term"]["source"] == "admin_returned"
    assert returned_again_payload["term"]["meta"]["review_decision"] == "returned_to_candidate"
    assert "ai_approved" not in returned_again_payload["term"]["meta"]
    assert "candidate_review_state" not in returned_again_payload["term"]["meta"]

    asyncio.run(store.close())


def test_slang_api_v3_drift_and_revisions(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())
    store.set_drift_reviewer(_FakeDriftReviewer("real_drift", reason="核心指代变化"))
    term = asyncio.run(store.create_term(
        term="猫饼",
        meaning="群里说离谱但可爱的操作",
        scope="group",
        group_id="100",
        status="approved",
        confidence=0.9,
    ))
    settings = SlangSettings(drift_detection_enabled=True, drift_min_confidence=0.6)
    asyncio.run(store.upsert_candidate(
        term="猫饼",
        meaning="固定成员的新称呼",
        group_id="100",
        raw_text="猫饼来了",
        confidence=0.9,
        reason="冲突释义",
        settings=settings,
    ))

    app = FastAPI()
    app.include_router(create_slang_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    drift_resp = client.get("/api/admin/slang/drift")
    assert drift_resp.status_code == 200
    drift_payload = drift_resp.json()
    assert drift_payload["total"] == 1
    drift_id = drift_payload["reviews"][0]["drift_id"]

    revisions_resp = client.get(f"/api/admin/slang/terms/{term.term_id}/revisions")
    assert revisions_resp.status_code == 200
    assert any(item["action"] == "drift_detected" for item in revisions_resp.json()["revisions"])

    accept_resp = client.post(f"/api/admin/slang/drift/{drift_id}/accept")
    assert accept_resp.status_code == 200
    assert accept_resp.json()["ok"] is True
    assert accept_resp.json()["term"]["meaning"] == "固定成员的新称呼"

    processed_resp = client.get("/api/admin/slang/drift", params={"status": "accepted"})
    assert processed_resp.status_code == 200
    assert processed_resp.json()["total"] == 1

    asyncio.run(store.close())


def test_slang_api_replays_open_drift_reviews(tmp_path: Path) -> None:
    store = SlangStore(tmp_path / "slang.db")
    asyncio.run(store.init())
    store.set_drift_reviewer(_FakeDriftReviewer("real_drift", reason="核心指代变化"))
    asyncio.run(store.create_term(
        term="没米",
        meaning="没有钱或没有资源",
        scope="group",
        group_id="100",
        status="approved",
        confidence=0.9,
    ))
    settings = SlangSettings(drift_detection_enabled=True, drift_min_confidence=0.6)
    asyncio.run(store.upsert_candidate(
        term="没米",
        meaning="一种完全不同的新释义",
        group_id="100",
        raw_text="没米了",
        confidence=0.9,
        reason="冲突释义",
        settings=settings,
    ))
    store.set_drift_reviewer(_FakeDriftReviewer("same_meaning", reason="回放判定同义"))

    app = FastAPI()
    app.include_router(create_slang_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    dry_run_resp = client.post("/api/admin/slang/drift/replay", json={"apply": False})
    assert dry_run_resp.status_code == 200
    assert dry_run_resp.json()["closed_same_meaning"] == 1
    assert client.get("/api/admin/slang/drift").json()["total"] == 1

    apply_resp = client.post("/api/admin/slang/drift/replay", json={"apply": True})
    assert apply_resp.status_code == 200
    assert apply_resp.json()["closed_same_meaning"] == 1
    assert client.get("/api/admin/slang/drift").json()["total"] == 0
    rejected_payload = client.get("/api/admin/slang/drift", params={"status": "rejected"}).json()
    assert rejected_payload["total"] == 1
    assert rejected_payload["reviews"][0]["meta"]["drift_semantic_verdict"] == "same_meaning"

    asyncio.run(store.close())


def test_soul_endpoint_saves_legacy_files_and_reloads_identity(tmp_path: Path) -> None:
    (tmp_path / "identity.md").write_text(
        (
            "# 凤笑梦\n\n开场介绍。\n\n## 基础身份\n\n| 项目 | 内容 |\n| --- | --- |\n"
            "| 所属团体 | WxS |\n\n## 插话方式\n\n被叫名字时必须回复。\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "instruction.md").write_text(
        "## 回复风格\n\n- 明亮\n- 主动\n\n## 工具使用\n\n1. 先判断\n2. 再调用\n",
        encoding="utf-8",
    )

    identity_mgr = _DummyIdentityMgr()
    app = FastAPI()
    app.include_router(
        create_soul_router(soul_dir=str(tmp_path), identity_mgr=identity_mgr),
        prefix="/api/admin",
    )
    client = TestClient(app)

    initial = client.get("/api/admin/soul")
    assert initial.status_code == 200
    initial_data = initial.json()
    assert initial_data["format_mode"] == "legacy"
    assert initial_data["migration_pending"] is False
    assert initial_data["editor"]["meta"]["display_title"] == "凤笑梦"
    assert initial_data["editor"]["meta"]["description"] == "开场介绍。"
    assert initial_data["editor"]["proactive"]["enabled"] is True
    assert [section["title"] for section in initial_data["editor"]["instruction_sections"]] == ["回复风格", "工具使用"]

    saved = client.post("/api/admin/soul/save", json={"editor": initial_data["editor"]})
    assert saved.status_code == 200
    save_data = saved.json()
    assert save_data["ok"] is True
    assert save_data["reload_ok"] is True
    assert "config/soul/identity.md" in save_data["message"]
    assert "config/soul/instruction.md" in save_data["message"]
    assert "SKILL.md" not in save_data["message"]
    assert not (tmp_path / "SKILL.md").exists()
    assert (tmp_path / "identity.md").is_file()
    assert (tmp_path / "instruction.md").is_file()
    assert identity_mgr.loaded_paths == [str(tmp_path / "identity.md")]

    identity_text = (tmp_path / "identity.md").read_text(encoding="utf-8")
    instruction_text = (tmp_path / "instruction.md").read_text(encoding="utf-8")
    assert "# 凤笑梦" in identity_text
    assert "## 基础身份" in identity_text
    assert "## 插话方式" in identity_text
    assert "被叫名字时必须回复。" in identity_text
    assert "## 回复风格" in instruction_text
    assert "## 工具使用" in instruction_text

    after = client.get("/api/admin/soul")
    assert after.status_code == 200
    after_data = after.json()
    assert after_data["format_mode"] == "legacy"
    assert after_data["migration_pending"] is False


def test_soul_endpoint_parses_and_roundtrips_legacy_blocks(tmp_path: Path) -> None:
    (tmp_path / "identity.md").write_text(
        """# 凤笑梦 (Emu Otori)

元气少女。

## 基础身份

| 项目 | 内容 |
| --- | --- |
| 所属团体 | WxS |

## 像与不像

- 明亮
- 主动

## 家庭关系

- **祖父**：梦想根源、价值观来源。
- `姐姐`：日常关系亲近。

## 插话方式

被叫名字时必须回复。
""",
        encoding="utf-8",
    )
    (tmp_path / "instruction.md").write_text(
        """## 回复风格

1. 先观察
2. 再回应

## 工具使用

### 调用前检查

> 这是一段保留格式的自由文本。

#### **更深小标题**

这段应当被识别为同级可编辑小标题，且去掉 `Markdown` 标记。
""",
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(create_soul_router(soul_dir=str(tmp_path)), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/soul")
    assert resp.status_code == 200
    data = resp.json()
    assert data["format_mode"] == "legacy"
    assert data["editor"]["meta"]["name"] == "凤笑梦 (Emu Otori)"
    assert data["editor"]["meta"]["display_title"] == "凤笑梦 (Emu Otori)"
    assert data["editor"]["meta"]["description"] == "元气少女。"
    assert [section["title"] for section in data["editor"]["persona_sections"]] == ["基础身份", "像与不像", "家庭关系"]
    assert [section["title"] for section in data["editor"]["instruction_sections"]] == ["回复风格", "工具使用"]

    persona_blocks = data["editor"]["persona_sections"][0]["blocks"]
    assert persona_blocks[0]["type"] == "kv_table"
    assert persona_blocks[0]["rows"][0] == {"key": "所属团体", "value": "WxS"}
    family_blocks = data["editor"]["persona_sections"][2]["blocks"]
    assert family_blocks[0]["items"] == ["祖父：梦想根源、价值观来源。", "姐姐：日常关系亲近。"]

    style_blocks = data["editor"]["instruction_sections"][0]["blocks"]
    assert style_blocks[0]["type"] == "numbered_list"
    assert style_blocks[0]["items"] == ["先观察", "再回应"]

    tool_blocks = data["editor"]["instruction_sections"][1]["blocks"]
    assert tool_blocks[0]["heading"] == "调用前检查"
    assert tool_blocks[0]["type"] == "free_text"
    assert tool_blocks[1]["heading"] == "更深小标题"
    assert tool_blocks[1]["type"] == "paragraph"
    assert tool_blocks[1]["text"] == "这段应当被识别为同级可编辑小标题，且去掉 Markdown 标记。"
    assert data["editor"]["proactive"]["enabled"] is True

    saved = client.post("/api/admin/soul/save", json={"editor": data["editor"]})
    assert saved.status_code == 200
    assert saved.json()["ok"] is True

    roundtrip = client.get("/api/admin/soul")
    assert roundtrip.status_code == 200
    roundtrip_data = roundtrip.json()
    assert [section["title"] for section in roundtrip_data["editor"]["instruction_sections"]] == [
        "回复风格",
        "工具使用",
    ]
    assert roundtrip_data["editor"]["proactive"]["text"] == "被叫名字时必须回复。"


def test_soul_endpoint_ignores_skill_md_as_runtime_source(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_text = """---
name: 技能角色
---

# 技能角色

## 技能章节

不应被 Soul API 当作运行时来源。
"""
    skill_path.write_text(
        skill_text,
        encoding="utf-8",
    )
    (tmp_path / "identity.md").write_text("# 双文件角色\n\n## 基础身份\n\n普通正文。\n", encoding="utf-8")
    (tmp_path / "instruction.md").write_text("## 回复风格\n\n- 简洁\n", encoding="utf-8")

    app = FastAPI()
    app.include_router(create_soul_router(soul_dir=str(tmp_path)), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/soul")
    assert resp.status_code == 200
    data = resp.json()
    assert data["format_mode"] == "legacy"
    assert data["editor"]["meta"]["name"] == "双文件角色"
    assert data["editor"]["persona_sections"][0]["title"] == "基础身份"

    saved = client.post("/api/admin/soul/save", json={"editor": data["editor"]})
    assert saved.status_code == 200
    save_data = saved.json()
    assert save_data["ok"] is True
    assert "SKILL.md" not in save_data["message"]
    assert skill_path.read_text(encoding="utf-8") == skill_text


def test_soul_endpoint_serves_persona_generation_guide(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(create_soul_router(soul_dir=str(tmp_path)), prefix="/api/admin")
    client = TestClient(app)

    resp = client.get("/api/admin/soul/persona-guide")
    assert resp.status_code == 200
    data = resp.json()
    assert "AI 自主生成" in data["title"]
    assert "identity.md" in data["markdown"]
    assert "instruction.md" in data["markdown"]


def test_soul_endpoint_reports_reload_warning_without_losing_save(tmp_path: Path) -> None:
    (tmp_path / "identity.md").write_text("# 测试\n\n内容。\n", encoding="utf-8")
    (tmp_path / "instruction.md").write_text("## 回复风格\n\n- 简洁\n", encoding="utf-8")

    app = FastAPI()
    app.include_router(
        create_soul_router(soul_dir=str(tmp_path), identity_mgr=_DummyIdentityMgr(should_fail=True)),
        prefix="/api/admin",
    )
    client = TestClient(app)

    editor = client.get("/api/admin/soul").json()["editor"]
    saved = client.post("/api/admin/soul/save", json={"editor": editor})
    assert saved.status_code == 200
    data = saved.json()
    assert data["ok"] is True
    assert data["reload_ok"] is False
    assert "运行时同步失败" in data["message"]
    assert not (tmp_path / "SKILL.md").exists()
    assert (tmp_path / "identity.md").is_file()
    assert (tmp_path / "instruction.md").is_file()
