"""RED contracts for the allowlisted Social Narrative adapter."""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any, cast

from kernel.types import Identity, PluginContext, PromptContext, ReplyContext
from plugins.affection.models import AffectionProfile
from services.dialogue_climate.state import ClimateState


def _plugin_module() -> Any:
    try:
        module = importlib.import_module("plugins.social_narrative.plugin")
    except ModuleNotFoundError as exc:
        if exc.name not in {"plugins.social_narrative", "plugins.social_narrative.plugin"}:
            raise
        module = None
    assert module is not None, (
        "plugins.social_narrative.plugin must provide the Social Narrative adapter"
    )
    return module


def _plugin_types() -> tuple[type[Any], type[Any]]:
    module = _plugin_module()
    config_type = getattr(module, "SocialNarrativeConfig", None)
    plugin_type = getattr(module, "SocialNarrativePlugin", None)
    assert inspect.isclass(config_type), "adapter must expose SocialNarrativeConfig"
    assert inspect.isclass(plugin_type), "adapter must expose SocialNarrativePlugin"
    return config_type, plugin_type


def _reply_context(
    *,
    group_id: str | None,
    user_id: str = "100",
    source_message_id: int | None = 7001,
) -> ReplyContext:
    ctx = ReplyContext(
        session_id=f"group_{group_id}" if group_id else "private_100",
        group_id=group_id,
        user_id=user_id,
        user_msg="我们刚才一起把排练节奏理顺了",
        reply_content="嗯，先从最容易乱掉的那一段开始。",
        thinker_action="reply",
    )
    cast(Any, ctx).source_message_id = source_message_id
    return ctx


class _FakeNarrativeStore:
    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []
        self.context_calls: list[tuple[str, str]] = []

    async def record_shared_experience(self, **payload: Any) -> dict[str, Any]:
        self.recorded.append(dict(payload))
        return dict(payload)

    async def record(self, **payload: Any) -> dict[str, Any]:
        return await self.record_shared_experience(**payload)

    async def build_prompt_context(self, *, group_id: str, user_id: str, limit: int = 10) -> str:
        del limit
        self.context_calls.append((group_id, user_id))
        if (group_id, user_id) != ("200", "100"):
            return ""
        return "共同经历-200-100；entity_kind=factual；证据=7001；不得补写真人线下行为"

    async def recall(self, *, group_id: str, user_id: str, limit: int = 10) -> list[dict[str, str]]:
        del limit
        if (group_id, user_id) != ("200", "100"):
            return []
        return [{
            "group_id": "200",
            "user_id": "100",
            "entity_kind": "factual",
            "evidence_message_id": "7001",
            "user_text": "共同经历-200-100",
        }]


async def _started_plugin(
    store: _FakeNarrativeStore,
    *,
    affection_engine: Any = None,
    climate_engine: Any = None,
) -> tuple[Any, PluginContext]:
    config_type, plugin_type = _plugin_types()
    config = config_type(enabled=True, allowed_group_ids=["200"])
    plugin = plugin_type(config)
    ctx = PluginContext(
        social_narrative_store=store,
        affection_engine=affection_engine,
        climate_engine=climate_engine,
    )
    await plugin.on_startup(ctx)
    return plugin, ctx


class _FakeAffectionStore:
    def __init__(self) -> None:
        self.get_calls: list[str] = []

    def get(self, user_id: str) -> AffectionProfile:
        self.get_calls.append(user_id)
        return AffectionProfile(user_id=user_id, score=67.5)


class _FakeAffectionEngine:
    def __init__(self) -> None:
        self._store = _FakeAffectionStore()


class _FakeClimateEngine:
    def __init__(self) -> None:
        self.resolve_calls: list[tuple[str, str]] = []

    def resolve(self, *, group_id: str, user_id: str) -> ClimateState:
        self.resolve_calls.append((group_id, user_id))
        return ClimateState(valence=0.72, familiarity=0.61)


def test_social_narrative_config_is_fail_closed_by_default() -> None:
    config_type, _ = _plugin_types()
    config = config_type()
    assert config.enabled is False
    assert config.allowed_group_ids == []


def test_social_narrative_manifest_permissions_and_silent_contract() -> None:
    manifest_path = (
        Path(__file__).resolve().parent.parent
        / "plugins"
        / "social_narrative"
        / "plugin.json"
    )
    assert manifest_path.is_file(), "social_narrative requires a plugin.json manifest"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {"prompt", "reply", "storage"} <= set(payload.get("permissions", []))
    _, plugin_type = _plugin_types()
    assert plugin_type.silent_safe is False


async def test_adapter_records_only_allowlisted_group_reply_with_message_evidence() -> None:
    store = _FakeNarrativeStore()
    plugin, ctx = await _started_plugin(store)
    try:
        await plugin.on_post_reply(_reply_context(group_id="200", source_message_id=7001))
        await plugin.on_post_reply(_reply_context(group_id=None, source_message_id=7002))
        await plugin.on_post_reply(_reply_context(group_id="201", source_message_id=7003))
        await plugin.on_post_reply(_reply_context(group_id="200", source_message_id=None))
    finally:
        await plugin.on_shutdown(ctx)

    assert len(store.recorded) == 1
    recorded = store.recorded[0]
    assert recorded["group_id"] == "200"
    assert recorded["user_id"] == "100"
    assert str(recorded["evidence_message_id"]) == "7001"
    assert recorded["user_text"] == "我们刚才一起把排练节奏理顺了"
    assert recorded["bot_reply"] == "嗯，先从最容易乱掉的那一段开始。"
    assert recorded["entity_kind"] == "factual"
    assert recorded["evidence_time"]
    assert recorded["evidence_source"]


async def test_adapter_records_current_relationship_snapshot_from_runtime_engines() -> None:
    store = _FakeNarrativeStore()
    affection_engine = _FakeAffectionEngine()
    climate_engine = _FakeClimateEngine()
    plugin, ctx = await _started_plugin(
        store,
        affection_engine=affection_engine,
        climate_engine=climate_engine,
    )
    try:
        await plugin.on_post_reply(_reply_context(group_id="200", user_id="100"))
    finally:
        await plugin.on_shutdown(ctx)

    assert affection_engine._store.get_calls == ["100"]
    assert climate_engine.resolve_calls == [("200", "100")]
    assert store.recorded[0].get("relationship") == {
        "affection_score": 67.5,
        "affection_tier": "好朋友",
        "climate_valence": 0.72,
        "climate_familiarity": 0.61,
    }


async def test_adapter_records_without_relationship_engines() -> None:
    store = _FakeNarrativeStore()
    plugin, ctx = await _started_plugin(store)
    try:
        await plugin.on_post_reply(_reply_context(group_id="200", user_id="100"))
    finally:
        await plugin.on_shutdown(ctx)

    assert len(store.recorded) == 1
    assert store.recorded[0].get("relationship", {}) == {}


async def test_is_group_reflection_eligible_is_public_and_fail_closed() -> None:
    store = _FakeNarrativeStore()
    plugin, ctx = await _started_plugin(store)
    try:
        assert plugin.is_group_reflection_eligible("200") is True
        assert plugin.is_group_reflection_eligible("201") is False
        assert plugin.is_group_reflection_eligible("0") is False
        assert plugin.is_group_reflection_eligible(None) is False
        assert plugin.is_group_reflection_eligible("wxs") is False
    finally:
        await plugin.on_shutdown(ctx)
    assert plugin.is_group_reflection_eligible("200") is False


async def test_on_pre_prompt_injects_only_current_group_and_user() -> None:
    store = _FakeNarrativeStore()
    plugin, ctx = await _started_plugin(store)
    exact = PromptContext(
        session_id="group_200",
        group_id="200",
        user_id="100",
        identity=Identity(name="凤笑梦"),
    )
    other_user = PromptContext(
        session_id="group_200",
        group_id="200",
        user_id="101",
        identity=Identity(name="凤笑梦"),
    )
    other_group = PromptContext(
        session_id="group_201",
        group_id="201",
        user_id="100",
        identity=Identity(name="凤笑梦"),
    )
    try:
        await plugin.on_pre_prompt(exact)
        await plugin.on_pre_prompt(other_user)
        await plugin.on_pre_prompt(other_group)
    finally:
        await plugin.on_shutdown(ctx)

    assert len(exact.blocks) == 1
    assert "共同经历-200-100" in exact.blocks[0].text
    assert other_user.blocks == []
    assert other_group.blocks == []
    assert ("200", "100") in store.context_calls
    assert ("201", "100") not in store.context_calls
