from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from kernel.config import BotConfig
from kernel.types import MessageContext, PluginContext
from plugins.chat.plugin import ChatPlugin, _humanization_resolve, _register_humanization_interaction_tools
from services.humanization import (
    REGISTER_LABEL_SLOT,
    WILLINGNESS_STAGE_SLOT,
    RegisterClassifier,
    create_humanization_state_bus,
)
from services.llm.arbiter import ArbiterClient
from services.system_module import Scope
from services.tools.registry import ToolRegistry


class _FakeLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[Any] = []

    async def _call(self, request: Any) -> dict[str, str]:
        self.requests.append(request)
        return {"text": json.dumps(self.payload, ensure_ascii=False)}


class _SlowLLM:
    async def _call(self, request: Any) -> dict[str, str]:
        del request
        await asyncio.sleep(60)
        return {"text": '{"label":"playful","confidence":1.0}'}


class _Timeline:
    def get_turns(self, group_id: str) -> list[dict[str, Any]]:
        assert group_id == "100"
        return [
            {"role": "user", "content": "alice: 先认真讲一下"},
            {"role": "assistant", "content": "可以，我先给结论。"},
        ]

    def get_turn_time(self, group_id: str, index: int) -> float:
        assert group_id == "100"
        return [time.time() - 120.0, time.time() - 30.0][index]


def _message(text: str = "这事认真点说", *, group_id: str = "100") -> MessageContext:
    return MessageContext(
        session_id=f"group_{group_id}",
        group_id=group_id,
        user_id="u1",
        content=text,
        raw_message={},
        nickname="alice",
    )


def _plugin_ctx(
    *,
    register_classifier: bool,
    classifier: object | None,
    runtime_state: object | None,
    runtime_groups: list[str] | None = None,
    episode_store: object | None = None,
    card_store: object | None = None,
    mood_engine: object | None = None,
) -> PluginContext:
    return PluginContext(
        config=BotConfig.model_validate({
            "humanization": {
                "register_classifier": register_classifier,
                "runtime_groups": runtime_groups or [],
            }
        }),
        runtime_state=runtime_state,
        timeline=_Timeline(),
        humanization_register_classifier=classifier,
        episode_store=episode_store,
        card_store=card_store,
        mood_engine=mood_engine,
    )


def test_chat_plugin_wires_register_classifier_when_flag_enabled() -> None:
    plugin = ChatPlugin()
    ctx = PluginContext()
    llm = object()

    plugin._wire_humanization_runtime(
        ctx,
        BotConfig.model_validate({"humanization": {"register_classifier": True}}),
        llm,
    )

    assert isinstance(ctx.humanization_register_classifier, RegisterClassifier)


def test_chat_plugin_leaves_register_classifier_unwired_by_default() -> None:
    plugin = ChatPlugin()
    ctx = PluginContext(humanization_register_classifier=object())

    plugin._wire_humanization_runtime(ctx, BotConfig(), object())

    assert ctx.humanization_register_classifier is None


def test_chat_plugin_builds_arbiter_client_with_llm_fallbacks() -> None:
    plugin = ChatPlugin()
    config = BotConfig.model_validate({
        "llm": {
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-test",
            "model": "deepseek-v4-flash",
        },
        "arbiter": {
            "enabled": True,
        },
    })
    llm = SimpleNamespace(_session=object())

    arbiter = plugin._build_arbiter_client(config, llm, usage_tracker=None)

    assert isinstance(arbiter, ArbiterClient)
    assert config.arbiter.resolved_api_base == "https://api.deepseek.com"
    assert config.arbiter.resolved_api_key == "sk-test"
    assert config.arbiter.resolved_model == "deepseek-v4-flash"


def test_humanization_resolve_honors_group_profile_override() -> None:
    cfg = BotConfig.model_validate({
        "humanization": {"profile": "economy"},
        "group": {
            "access": {"mode": "whitelist", "whitelist": [100]},
            "overrides": {
                "100": {
                    "presence_mode": "active",
                    "humanization_profile": "balanced",
                }
            },
        },
    })

    resolved = _humanization_resolve(cfg, "100")

    assert resolved.streaming_segment_enabled is True
    assert resolved.pause_then_extend_enabled is True
    assert resolved.disable_natural_split is True


def test_register_interaction_tools_wired_for_performance() -> None:
    registry = ToolRegistry()
    _register_humanization_interaction_tools(
        BotConfig.model_validate({"humanization": {"profile": "performance"}}),
        registry,
    )

    assert registry.get("poke_user") is not None
    assert registry.get("react_to_message") is not None


def test_register_interaction_tools_not_wired_for_default_profiles() -> None:
    for profile in ("economy", "balanced", "custom"):
        registry = ToolRegistry()
        _register_humanization_interaction_tools(
            BotConfig.model_validate({"humanization": {"profile": profile}}),
            registry,
        )

        assert registry.get("poke_user") is None
        assert registry.get("react_to_message") is None


@pytest.mark.asyncio
async def test_chat_plugin_register_classifier_default_off_does_not_consume_or_write() -> None:
    plugin = ChatPlugin()
    bus = create_humanization_state_bus()
    classifier = RegisterClassifier(_FakeLLM({"label": "serious", "confidence": 0.9}))
    plugin._ctx = _plugin_ctx(register_classifier=False, classifier=classifier, runtime_state=bus)

    consumed = await plugin.on_message(_message())

    assert consumed is False
    assert bus.get(REGISTER_LABEL_SLOT, scope=Scope(session_id="group_100", group_id="100", user_id="u1")) is None


@pytest.mark.asyncio
async def test_chat_plugin_register_classifier_skips_non_gray_group() -> None:
    plugin = ChatPlugin()
    bus = create_humanization_state_bus()
    llm = _FakeLLM({"label": "serious", "confidence": 0.9})
    plugin._ctx = _plugin_ctx(
        register_classifier=True,
        classifier=RegisterClassifier(llm),
        runtime_state=bus,
        runtime_groups=["200"],
    )

    consumed = await plugin.on_message(_message(group_id="100"))

    assert consumed is False
    assert llm.requests == []
    assert bus.get(REGISTER_LABEL_SLOT, scope=Scope(session_id="group_100", group_id="100", user_id="u1")) is None


@pytest.mark.asyncio
async def test_chat_plugin_register_classifier_writes_state_and_does_not_consume() -> None:
    plugin = ChatPlugin()
    bus = create_humanization_state_bus()
    llm = _FakeLLM({"label": "serious", "confidence": 0.9, "reason": "在处理问题"})
    plugin._ctx = _plugin_ctx(
        register_classifier=True,
        classifier=RegisterClassifier(llm),
        runtime_state=bus,
    )

    consumed = await plugin.on_message(_message())

    snapshot = bus.get(REGISTER_LABEL_SLOT, scope=Scope(session_id="group_100", group_id="100", user_id="u1"))
    assert consumed is False
    assert snapshot is not None
    assert snapshot.value["label"] == "serious"
    assert snapshot.value["window_size"] == 3
    assert len(llm.requests) == 1


@pytest.mark.asyncio
async def test_chat_plugin_register_classifier_cancel_path_does_not_dirty_write() -> None:
    plugin = ChatPlugin()
    bus = create_humanization_state_bus()
    plugin._ctx = _plugin_ctx(
        register_classifier=True,
        classifier=RegisterClassifier(_SlowLLM()),
        runtime_state=bus,
    )
    task = asyncio.create_task(plugin.on_message(_message("接一下这个梗")))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert bus.get(REGISTER_LABEL_SLOT, scope=Scope(session_id="group_100", group_id="100", user_id="u1")) is None


@pytest.mark.asyncio
async def test_chat_plugin_writes_willingness_and_memory_signal_cache() -> None:
    class _Episode:
        def __init__(self, outcome_signal: str) -> None:
            self.outcome_signal = outcome_signal
            self.situation = "今天在认真解释问题"
            self.observed_context = "群友继续追问"
            self.updated_at = "2026-05-29T12:00:00"

    class _EpisodeStore:
        async def list_episodes(self, **_kwargs):
            return [_Episode("用户后来愿意继续聊")]

        async def list_for_recall(self, **_kwargs):
            return [_Episode("用户后来愿意继续聊")]

    class _CardStore:
        async def list_cards(self, **_kwargs):
            return [object(), object(), object()]

    class _MoodProfile:
        def __init__(self, valence: float) -> None:
            self.valence = valence

    class _MoodEngine:
        def recent_profiles(self, **_kwargs):
            return [_MoodProfile(-0.2), _MoodProfile(0.3)]

    plugin = ChatPlugin()
    bus = create_humanization_state_bus()
    plugin._ctx = _plugin_ctx(
        register_classifier=False,
        classifier=None,
        runtime_state=bus,
        episode_store=_EpisodeStore(),
        card_store=_CardStore(),
        mood_engine=_MoodEngine(),
    )

    consumed = await plugin.on_message(_message())

    willingness = bus.get(
        WILLINGNESS_STAGE_SLOT,
        scope=Scope(session_id="group_100", group_id="100", user_id="u1"),
    )
    assert consumed is False
    assert willingness is not None
    assert willingness.value["willingness_stage"] in {"acquaint", "familiar"}
    assert plugin._ctx.memory_relation_signals[("100", "u1")]["outcome_ratio"] >= 0.5
