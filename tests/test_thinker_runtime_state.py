from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kernel.types import PromptBlock
from services.humanization import CLOCK_CURRENT_SLOT, THINKER_LAST_DECISION_SLOT, create_humanization_state_bus
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.llm.thinker import ThinkDecision, write_clock_state, write_thinker_decision_state
from services.memory.short_term import ShortTermMemory
from services.persona import IdentitySnapshot, PersonaRuntime
from services.system_module import Scope
from services.tools.registry import ToolRegistry

_MAIN_RESULT = {
    "text": "reply text",
    "tool_uses": [],
    "input_tokens": 160,
    "output_tokens": 200,
    "cache_read": 50,
    "cache_create": 10,
}


class _Bus:
    def __init__(self) -> None:
        self.thinker_calls: list[object] = []

    async def fire_on_pre_prompt(self, prompt_ctx) -> None:
        return None

    async def fire_on_post_reply(self, reply_ctx) -> None:
        return None

    async def fire_on_thinker_decision(self, thinker_ctx) -> None:
        self.thinker_calls.append(thinker_ctx)


class _ProviderBus:
    mode = "active"

    def __init__(self, blocks: list[PromptBlock] | None = None) -> None:
        self.qctx = None
        self.blocks = blocks or []

    async def run_active(self, qctx):
        self.qctx = qctx
        return list(self.blocks)


def _prompt(persona_runtime: PersonaRuntime) -> PromptBuilder:
    return PromptBuilder(persona_runtime=persona_runtime)


async def _client(
    persona_runtime: PersonaRuntime,
    *,
    runtime_state=None,
    bus=None,
    clock_context_getter=None,
    mood_getter=None,
    thinker_provider_enabled: bool = False,
) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=True,
        runtime_state=runtime_state,
        bus=bus,
        clock_context_getter=clock_context_getter,
        mood_getter=mood_getter,
        thinker_provider_enabled=thinker_provider_enabled,
    )


def test_write_thinker_decision_state_happy_path() -> None:
    bus = create_humanization_state_bus()
    decision = ThinkDecision(
        action="reply",
        thought="查文档",
        topic_intent_label="技术讨论",
        retrieve_mode="doc",
        rewritten_query="omubot 部署方式",
        sticker=True,
        tone="认真",
        usage={"input_tokens": 10},
    )

    write_thinker_decision_state(
        bus,
        decision,
        session_id="private_100",
        group_id=None,
        user_id="100",
        turn_id="turn-1",
    )

    snapshot = bus.get(
        THINKER_LAST_DECISION_SLOT,
        scope=Scope(session_id="private_100", user_id="100", turn_id="turn-1"),
    )
    assert snapshot is not None
    assert snapshot.value["action"] == "reply"
    assert snapshot.value["topic_intent_label"] == "技术讨论"
    assert snapshot.value["retrieve_mode"] == "doc"
    assert snapshot.value["rewritten_query"] == "omubot 部署方式"
    assert snapshot.value["sticker"] is True


def test_write_clock_state_happy_path() -> None:
    bus = create_humanization_state_bus()
    features = {
        "date": "2026-05-25",
        "hour": 2,
        "minute": 30,
        "weekday": 0,
        "weekday_cn": "周一",
        "is_weekend": False,
        "is_holiday": False,
        "slot_time": "02:00",
        "slot_activity": "睡觉",
        "slot_mood_hint": "困倦",
    }

    write_clock_state(
        bus,
        features,
        session_id="private_100",
        group_id=None,
        user_id="100",
        turn_id="turn-1",
    )

    snapshot = bus.get(
        CLOCK_CURRENT_SLOT,
        scope=Scope(session_id="private_100", user_id="100", turn_id="turn-1"),
    )
    assert snapshot is not None
    assert snapshot.value == features


@pytest.mark.asyncio
async def test_llm_client_writes_thinker_state_and_keeps_hook(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    runtime_state = create_humanization_state_bus()
    plugin_bus = _Bus()
    clock_features = {
        "date": "2026-05-25",
        "hour": 2,
        "minute": 30,
        "weekday": 0,
        "weekday_cn": "周一",
        "is_weekend": False,
        "is_holiday": False,
        "slot_time": "02:00",
        "slot_activity": "睡觉",
        "slot_mood_hint": "困倦",
    }
    client = await _client(
        persona_runtime,
        runtime_state=runtime_state,
        bus=plugin_bus,
        clock_context_getter=lambda **_: clock_features,
    )
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="技术讨论",
                retrieve_mode="doc",
                rewritten_query="omubot 怎么部署",
                thought="查文档",
                sticker=False,
                tone="认真",
                usage={"input_tokens": 10, "cache_read": 0, "cache_create": 0, "output_tokens": 2},
            )
            result = await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="omubot 怎么部署",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    assert result == "reply text"
    assert len(plugin_bus.thinker_calls) == 1
    assert plugin_bus.thinker_calls[0].topic_intent_label == "技术讨论"
    assert plugin_bus.thinker_calls[0].retrieve_mode == "doc"
    trace = runtime_state.snapshot_all_for_trace()
    thinker_values = [
        row["value"]
        for row in trace.values()
        if row["slot_id"] == THINKER_LAST_DECISION_SLOT
    ]
    assert thinker_values == [{
        "action": "reply",
        "thought": "查文档",
        "topic_intent_label": "技术讨论",
        "retrieve_mode": "doc",
        "rewritten_query": "omubot 怎么部署",
        "sticker": False,
        "tone": "认真",
        "usage": {"input_tokens": 10, "cache_read": 0, "cache_create": 0, "output_tokens": 2},
    }]
    clock_rows = [
        row
        for row in trace.values()
        if row["slot_id"] == CLOCK_CURRENT_SLOT
    ]
    thinker_rows = [
        row
        for row in trace.values()
        if row["slot_id"] == THINKER_LAST_DECISION_SLOT
    ]
    assert len(clock_rows) == 1
    assert clock_rows[0]["value"] == clock_features
    assert clock_rows[0]["scope"]["turn_id"] == thinker_rows[0]["scope"]["turn_id"]


@pytest.mark.asyncio
async def test_llm_client_passes_runtime_state_and_turn_id_to_providers(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    runtime_state = create_humanization_state_bus()
    provider_bus = _ProviderBus()
    client = await _client(persona_runtime, runtime_state=runtime_state, bus=_Bus())
    client._card_store = object()
    client.set_provider_bus(provider_bus)
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="闲聊",
                retrieve_mode="skip",
                rewritten_query="",
                thought="接一下",
                sticker=False,
                tone="日常",
                usage={},
            )
            await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="hello",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    assert provider_bus.qctx is not None
    assert provider_bus.qctx.runtime_state is runtime_state
    assert provider_bus.qctx.turn_id


@pytest.mark.asyncio
async def test_llm_client_passes_mood_fit_target_to_providers(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    provider_bus = _ProviderBus()
    mood = SimpleNamespace(label="兴奋", energy=1.0, valence=1.0, openness=1.0, tension=0.0)
    client = await _client(persona_runtime, bus=_Bus(), mood_getter=lambda **_: mood)
    client._card_store = object()
    client.set_provider_bus(provider_bus)
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="闲聊",
                retrieve_mode="skip",
                rewritten_query="",
                thought="接一下",
                sticker=False,
                tone="日常",
                usage={},
            )
            await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="hello",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    assert provider_bus.qctx is not None
    assert provider_bus.qctx.mood_fit_target == 1.0


@pytest.mark.asyncio
async def test_llm_client_keeps_legacy_thinker_block_when_provider_disabled(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    captured: dict[str, object] = {}

    async def _capture_main(*args, **_kwargs):
        captured["system_blocks"] = args[4]
        return _MAIN_RESULT

    client = await _client(persona_runtime, bus=_Bus())
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new=_capture_main),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                retrieve_mode="skip",
                rewritten_query="",
                thought="接一下",
                sticker=False,
                tone="日常",
                usage={},
            )
            await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="hello",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    system_text = _system_text(captured["system_blocks"])
    assert "【意图：闲聊】" in system_text
    assert "【tone: 日常】" in system_text
    assert "你决定说话：" not in system_text


@pytest.mark.asyncio
async def test_llm_client_uses_thinker_provider_without_legacy_double_injection(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    captured: dict[str, object] = {}

    async def _capture_main(*args, **_kwargs):
        captured["system_blocks"] = args[4]
        return _MAIN_RESULT

    block = PromptBlock(
        text="本轮回复意图：按标签方向回应，不要把这些标签原样写给用户。",
        label="本轮意图",
        position="dynamic",
        source="context",
        provider="thinker_provider",
    )
    provider_bus = _ProviderBus(blocks=[block])
    client = await _client(persona_runtime, bus=_Bus(), thinker_provider_enabled=True)
    client._card_store = object()
    client.set_provider_bus(provider_bus)
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new=_capture_main),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="闲聊",
                retrieve_mode="skip",
                rewritten_query="",
                thought="接一下",
                sticker=False,
                tone="日常",
                usage={},
            )
            await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="hello",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    assert provider_bus.qctx is not None
    system_text = _system_text(captured["system_blocks"])
    assert "【本轮意图】" in system_text
    assert "本轮回复意图" in system_text
    assert "意图标签" not in system_text or "【本轮意图】" in system_text
    assert "你决定说话：接一下" not in system_text


def _system_text(blocks: object) -> str:
    return "\n".join(
        str(block.get("text", ""))
        for block in blocks  # type: ignore[union-attr]
        if isinstance(block, dict)
    )


def test_thinker_runtime_state_per_turn_can_be_cleared() -> None:
    bus = create_humanization_state_bus()
    scope = Scope(session_id="group_100", group_id="100", user_id="u1", turn_id="t1")
    write_thinker_decision_state(
        bus,
        ThinkDecision(action="wait", thought="先等一下"),
        session_id=scope.session_id,
        group_id=scope.group_id,
        user_id=scope.user_id,
        turn_id=scope.turn_id,
    )

    assert bus.get(THINKER_LAST_DECISION_SLOT, scope=scope) is not None
    bus.clear_per_turn(scope=scope)
    assert bus.get(THINKER_LAST_DECISION_SLOT, scope=scope) is None


@pytest.mark.asyncio
async def test_thinker_runtime_state_cancel_path_does_not_dirty_write(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    runtime_state = create_humanization_state_bus()
    client = await _client(persona_runtime, runtime_state=runtime_state)

    async def _slow_think(*args, **kwargs):
        await asyncio.sleep(60)
        return ThinkDecision(action="reply", thought="慢慢回")

    try:
        with patch("services.llm.thinker.think", new=_slow_think):
            task = asyncio.create_task(
                client.chat(
                    session_id="private_100",
                    user_id="100",
                    user_content="hello",
                    identity=identity_snapshot,
                )
            )
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await client.close()

    assert runtime_state.snapshot_all_for_trace() == {}


def test_thinker_runtime_state_isolates_multiple_groups() -> None:
    bus = create_humanization_state_bus()
    first_scope = Scope(session_id="group_100", group_id="100", user_id="u1", turn_id="t1")
    second_scope = Scope(session_id="group_200", group_id="200", user_id="u1", turn_id="t1")

    write_thinker_decision_state(
        bus,
        ThinkDecision(action="reply", thought="接一下", topic_intent_label="闲聊", retrieve_mode="hybrid"),
        session_id=first_scope.session_id,
        group_id=first_scope.group_id,
        user_id=first_scope.user_id,
        turn_id=first_scope.turn_id,
    )
    write_thinker_decision_state(
        bus,
        ThinkDecision(action="wait", thought="先不插话", topic_intent_label="闲聊", retrieve_mode="skip"),
        session_id=second_scope.session_id,
        group_id=second_scope.group_id,
        user_id=second_scope.user_id,
        turn_id=second_scope.turn_id,
    )

    first = bus.get(THINKER_LAST_DECISION_SLOT, scope=first_scope)
    second = bus.get(THINKER_LAST_DECISION_SLOT, scope=second_scope)
    assert first is not None
    assert second is not None
    assert first.value["action"] == "reply"
    assert second.value["action"] == "wait"
