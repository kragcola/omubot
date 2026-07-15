from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from kernel.types import PromptBlock
from services.block_trace.climate_provider import (
    build_climate_turn_snapshot,
    read_climate_turn_snapshot,
)
from services.dialogue_climate.state import ClimateState
from services.humanization import CLOCK_CURRENT_SLOT, THINKER_LAST_DECISION_SLOT, create_humanization_state_bus
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.llm.thinker import ThinkDecision, write_clock_state, write_thinker_decision_state
from services.memory.card_store import CardStore
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
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
        self.prompt_calls: list[object] = []
        self.thinker_calls: list[object] = []

    async def fire_on_pre_prompt(self, prompt_ctx) -> None:
        self.prompt_calls.append(prompt_ctx)

    async def fire_on_post_reply(self, reply_ctx) -> None:
        return None

    async def fire_on_thinker_decision(self, thinker_ctx) -> None:
        self.thinker_calls.append(thinker_ctx)


class _FakeCardStore:
    pass


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
    short_term: ShortTermMemory | None = None,
    runtime_state=None,
    bus=None,
    clock_context_getter=None,
    mood_getter=None,
    slang_store_getter: Callable[[], Any] | None = None,
    thinker_provider_enabled: bool = False,
    group_timeline: GroupTimeline | None = None,
) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=short_term if short_term is not None else ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=True,
        runtime_state=runtime_state,
        bus=bus,
        clock_context_getter=clock_context_getter,
        mood_getter=mood_getter,
        slang_store_getter=slang_store_getter,
        thinker_provider_enabled=thinker_provider_enabled,
        group_timeline=group_timeline,
    )


class _FakeThinkerSlangStore:
    def __init__(self, *, conflict_group_id: str) -> None:
        self.conflict_group_id = conflict_group_id
        self.find_calls: list[dict[str, object]] = []
        self.injectable_calls: list[dict[str, object]] = []

    async def find_matching_terms(
        self,
        *,
        group_id: str,
        text: str,
        include_candidates: bool,
    ) -> list[SimpleNamespace]:
        self.find_calls.append({
            "group_id": group_id,
            "text": text,
            "include_candidates": include_candidates,
        })
        if group_id != self.conflict_group_id:
            return []
        return [
            SimpleNamespace(
                term="群内说法",
                aliases=["窝讨厌泥"],
                status="approved",
            )
        ]

    async def get_injectable_terms(self, **kwargs: object) -> list[object]:
        self.injectable_calls.append(dict(kwargs))
        return []


def test_write_thinker_decision_state_happy_path() -> None:
    bus = create_humanization_state_bus()
    decision = ThinkDecision(
        action="reply",
        thought="查文档",
        topic_intent_label="技术讨论",
        retrieve_mode="doc",
        rewritten_query="omubot 部署方式",
        unknown_terms=["op"],
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
    assert snapshot.value["unknown_terms"] == ["op"]
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
                unknown_terms=["op"],
                sticker=False,
                tone="认真",
                instruction_signal="none",
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
    thinker_ctx = plugin_bus.thinker_calls[0]
    assert getattr(thinker_ctx, "topic_intent_label", "") == "技术讨论"
    assert getattr(thinker_ctx, "retrieve_mode", "") == "doc"
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
        "unknown_terms": ["op"],
        "sticker": False,
        "tone": "认真",
        "instruction_signal": "none",
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
async def test_visual_identity_question_forces_retrieval_skip(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    timeline.add(
        "100",
        role="user",
        speaker="Alice(1)",
        content=[
            {"type": "text", "text": "这是谁«图片1: 未能可信识别具体角色»"},
            {"type": "image_ref", "path": "/tmp/current.jpg", "media_type": "image/jpeg"},
        ],
    )
    plugin_bus = _Bus()
    runtime_state = create_humanization_state_bus()
    client = await _client(
        persona_runtime,
        runtime_state=runtime_state,
        bus=plugin_bus,
        group_timeline=timeline,
    )
    client._card_store = cast(CardStore, _FakeCardStore())
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="询问",
                retrieve_mode="hybrid",
                rewritten_query="图片里的人是谁",
                thought="看图识人",
                unknown_terms=[],
                sticker=False,
                tone="认真",
                instruction_signal="none",
                usage={},
            )
            result = await client.chat(
                session_id="group_100",
                user_id="1",
                user_content="",
                identity=identity_snapshot,
                group_id="100",
                force_reply=True,
            )
    finally:
        await client.close()

    assert result == "reply text"
    assert len(plugin_bus.prompt_calls) == 1
    prompt_ctx = plugin_bus.prompt_calls[0]
    assert getattr(prompt_ctx, "retrieve_mode", "") == "skip"
    assert getattr(prompt_ctx, "rewritten_query", "missing") == ""
    snapshot = runtime_state.snapshot_all_for_trace()
    thinker_values = [
        row["value"]
        for row in snapshot.values()
        if row["slot_id"] == THINKER_LAST_DECISION_SLOT
    ]
    assert thinker_values[-1]["retrieve_mode"] == "skip"
    assert thinker_values[-1]["rewritten_query"] == ""


@pytest.mark.asyncio
async def test_private_bare_visual_question_drops_historical_image_context(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    short_term = ShortTermMemory()
    short_term.add(
        "private_100",
        "user",
        [
            {"type": "text", "text": "«图片1: 草薙宁宁»"},
            {"type": "image_ref", "path": "/tmp/nene.jpg", "media_type": "image/jpeg"},
        ],
    )
    short_term.add("private_100", "assistant", "这是草薙宁宁。")
    plugin_bus = _Bus()
    client = await _client(
        persona_runtime,
        short_term=short_term,
        bus=plugin_bus,
    )
    client._card_store = cast(CardStore, _FakeCardStore())
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="询问",
                retrieve_mode="hybrid",
                rewritten_query="草薙宁宁是谁",
                thought="根据历史回答",
                unknown_terms=[],
                sticker=False,
                tone="认真",
                instruction_signal="none",
                usage={},
            )
            result = await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="这是谁",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    assert result == "reply text"
    recent_messages = mock_think.await_args.kwargs["recent_messages"]
    rendered_recent = str(recent_messages)
    assert "本轮待处理消息没有图片或引用图片" in rendered_recent
    assert "草薙宁宁" not in rendered_recent
    assert "/tmp/nene.jpg" not in rendered_recent
    assert len(plugin_bus.prompt_calls) == 1
    assert getattr(plugin_bus.prompt_calls[0], "retrieve_mode", "") == "skip"


@pytest.mark.asyncio
async def test_llm_client_passes_runtime_state_and_turn_id_to_providers(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    runtime_state = create_humanization_state_bus()
    provider_bus = _ProviderBus()
    client = await _client(persona_runtime, runtime_state=runtime_state, bus=_Bus())
    client._card_store = cast(CardStore, _FakeCardStore())
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


@pytest.mark.parametrize(
    ("user_content", "expected_original", "expected_candidate"),
    [
        ("窝讨厌泥", "窝讨厌泥", "我讨厌你"),
        ("今天天气很好", "", ""),
    ],
)
@pytest.mark.asyncio
async def test_llm_client_passes_homophone_hint_to_thinker(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    user_content: str,
    expected_original: str,
    expected_candidate: str,
) -> None:
    client = await _client(persona_runtime, bus=_Bus())
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = _think_ns()
            await client.chat(
                session_id="private_100",
                user_id="100",
                user_content=user_content,
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    await_args = mock_think.await_args
    assert await_args is not None
    assert "homophone_hint" in await_args.kwargs
    hint = await_args.kwargs["homophone_hint"]
    if expected_original:
        assert hint
        assert expected_original in hint
        assert expected_candidate in hint
    else:
        assert hint == ""


@pytest.mark.parametrize(
    ("group_id", "expect_hint"),
    [
        ("100", False),
        ("200", True),
    ],
)
@pytest.mark.asyncio
async def test_llm_client_scopes_homophone_hint_suppression_to_group_slang(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    group_id: str,
    expect_hint: bool,
) -> None:
    store = _FakeThinkerSlangStore(conflict_group_id="100")
    client = await _client(
        persona_runtime,
        bus=_Bus(),
        slang_store_getter=lambda: store,
    )
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = _think_ns()
            await client.chat(
                session_id=f"group_{group_id}",
                user_id="100",
                user_content="窝讨厌泥",
                identity=identity_snapshot,
                group_id=group_id,
            )
    finally:
        await client.close()

    await_args = mock_think.await_args
    assert await_args is not None
    hint = await_args.kwargs["homophone_hint"]
    if expect_hint:
        assert hint
        assert "窝讨厌泥" in hint
        assert "我讨厌你" in hint
    else:
        assert hint == ""
    assert store.find_calls == [{
        "group_id": group_id,
        "text": "窝讨厌泥",
        "include_candidates": False,
    }]


@pytest.mark.asyncio
async def test_llm_client_keeps_private_user_text_verbatim_with_homophone_hint(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    short_term = ShortTermMemory()
    client = await _client(
        persona_runtime,
        short_term=short_term,
        bus=_Bus(),
    )
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = _think_ns()
            await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="窝讨厌泥",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    stored_messages = short_term.get("private_100")
    assert stored_messages[0]["role"] == "user"
    assert stored_messages[0]["content"] == "窝讨厌泥"
    assert stored_messages[0]["content"] != "我讨厌你"
    await_args = mock_think.await_args
    assert await_args is not None
    hint = await_args.kwargs["homophone_hint"]
    assert hint
    assert "窝讨厌泥" in hint
    assert "我讨厌你" in hint


@pytest.mark.asyncio
async def test_llm_client_publishes_one_climate_snapshot_before_thinker(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    runtime_state = create_humanization_state_bus()
    client = await _client(persona_runtime, runtime_state=runtime_state, bus=_Bus())
    try:
        assert hasattr(client, "set_climate_context_getter")
        client.set_climate_context_getter(
            lambda **_: build_climate_turn_snapshot(
                state=ClimateState(tension=0.7, familiarity=0.8),
                group_id="100",
                user_id="u1",
                relationship_text="【与当前用户的关系】\n关系不错。",
            )
        )
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="闲聊",
                retrieve_mode="skip",
                rewritten_query="",
                thought="简短接话",
                sticker=False,
                tone="日常",
                usage={},
            )
            await client.chat(
                session_id="group_100",
                user_id="u1",
                user_content="hello",
                identity=identity_snapshot,
                group_id="100",
            )
    finally:
        await client.close()

    await_args = mock_think.await_args
    assert await_args is not None
    call = await_args.kwargs
    assert "关系不错" in call["climate_text"]
    assert "回复短一些" in call["climate_text"]
    assert call["mood_text"] == ""
    assert call["affection_text"] == ""
    snapshot = read_climate_turn_snapshot(
        runtime_state,
        session_id="group_100",
        group_id="100",
        user_id="u1",
    )
    assert snapshot["policy"]["reply_bias"] == "short"


@pytest.mark.asyncio
async def test_llm_client_passes_mood_fit_target_to_providers(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    provider_bus = _ProviderBus()
    mood = SimpleNamespace(label="兴奋", energy=1.0, valence=1.0, openness=1.0, tension=0.0)
    client = await _client(persona_runtime, bus=_Bus(), mood_getter=lambda **_: mood)
    client._card_store = cast(CardStore, _FakeCardStore())
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
    client._card_store = cast(CardStore, _FakeCardStore())
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


def _think_ns(**over):
    base = dict(
        action="reply",
        topic_intent_label="闲聊",
        retrieve_mode="hybrid",
        rewritten_query="",
        thought="接个梗活跃气氛",
        unknown_terms=[],
        sticker=False,
        tone="日常",
        instruction_signal="none",
        light_kind="",
        reply_necessity="low",
        usage={"input_tokens": 10, "cache_read": 0, "cache_create": 0, "output_tokens": 2},
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_necessity_gate_downgrades_low_reply_to_silence(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """B3: low-necessity proactive reply (no trigger) is suppressed to silence."""
    client = await _client(persona_runtime)
    client._thinker_necessity_gate_enabled = True
    client._thinker_necessity_gate_addressed_exempt = True
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT) as main_call,
        ):
            mock_think.return_value = _think_ns(reply_necessity="low")
            result = await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="哈哈哈",
                identity=identity_snapshot,
            )
    finally:
        await client.close()
    assert result is None  # downgraded reply->wait → silence
    main_call.assert_not_called()  # main LLM never invoked


@pytest.mark.asyncio
async def test_necessity_gate_disabled_keeps_low_reply(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """Gate disabled (default) → low necessity still replies (== status quo)."""
    client = await _client(persona_runtime)
    client._thinker_necessity_gate_enabled = False
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = _think_ns(reply_necessity="low")
            result = await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="哈哈哈",
                identity=identity_snapshot,
            )
    finally:
        await client.close()
    assert result == "reply text"  # no gate → replies as before


@pytest.mark.asyncio
async def test_necessity_gate_high_necessity_replies(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """Gate on, but high necessity → not suppressed."""
    client = await _client(persona_runtime)
    client._thinker_necessity_gate_enabled = True
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = _think_ns(reply_necessity="high", thought="对方在求助")
            result = await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="帮我看下这个报错",
                identity=identity_snapshot,
            )
    finally:
        await client.close()
    assert result == "reply text"


@pytest.mark.asyncio
async def test_necessity_gate_exempts_ratified_role(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """C1: a low-necessity reply is NOT suppressed when the unified receiver
    role (from the scheduler via ctx.extra) is 'ratified' — i.e. the user is
    continuing an exchange the bot is part of. Regression for 'reply 后无反应':
    necessity_gate must use the SAME 被寻址 definition as the scheduler, not its
    own `trigger is None` guess."""
    from services.tools.context import ToolContext

    client = await _client(persona_runtime)
    client._thinker_necessity_gate_enabled = True
    client._thinker_necessity_gate_addressed_exempt = True
    ctx = ToolContext(bot=None, user_id="100", group_id="993065015", session_id="group_993065015")
    ctx.extra["receiver_role"] = "ratified"  # scheduler decided this is a continuation
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT) as main_call,
        ):
            mock_think.return_value = _think_ns(reply_necessity="low", thought="接梗打回去")
            result = await client.chat(
                session_id="group_993065015",
                user_id="100",
                user_content="",
                identity=identity_snapshot,
                group_id="993065015",
                ctx=ctx,
            )
    finally:
        await client.close()
    # ratified → exempt → NOT downgraded → main LLM ran, reply produced.
    assert result == "reply text"
    main_call.assert_called()


@pytest.mark.asyncio
async def test_necessity_gate_suppresses_overhearer_role(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """C1: low-necessity + role=overhearer (not part of the exchange) → still
    suppressed. Confirms the role exemption is selective, not a blanket pass."""
    from services.tools.context import ToolContext

    client = await _client(persona_runtime)
    client._thinker_necessity_gate_enabled = True
    client._thinker_necessity_gate_addressed_exempt = True
    ctx = ToolContext(bot=None, user_id="100", group_id="993065015", session_id="group_993065015")
    ctx.extra["receiver_role"] = "overhearer"
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT) as main_call,
        ):
            mock_think.return_value = _think_ns(reply_necessity="low", thought="接梗")
            result = await client.chat(
                session_id="group_993065015",
                user_id="100",
                user_content="",
                identity=identity_snapshot,
                group_id="993065015",
                ctx=ctx,
            )
    finally:
        await client.close()
    assert result is None  # overhearer + low → suppressed
    main_call.assert_not_called()


# ---------------------------------------------------------------------------
# #2: force_reply now runs the thinker (weak-reply classification on @ turns)
# ---------------------------------------------------------------------------


def _trigger(mode: str, **extra):
    return SimpleNamespace(mode=mode, extra=dict(extra), obligation=None)


@pytest.mark.asyncio
async def test_force_reply_runs_thinker_when_enabled(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """#2: with force_reply_enabled (default), an @-mention turn (force_reply)
    runs the pre-reply thinker — the sole producer of light_kind /
    reply_necessity. Previously `not force_reply` skipped it entirely."""
    client = await _client(persona_runtime)
    client._thinker_force_reply_enabled = True
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = _think_ns(action="reply")
            result = await client.chat(
                session_id="group_100",
                user_id="100",
                user_content="",
                identity=identity_snapshot,
                group_id="100",
                force_reply=True,
                trigger=_trigger("at_mention", addressee_self=True),
            )
    finally:
        await client.close()
    assert result == "reply text"
    mock_think.assert_awaited_once()  # thinker ran on the force_reply turn


@pytest.mark.asyncio
async def test_force_reply_skips_thinker_when_disabled(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """Flag off → legacy behavior: force_reply skips the thinker."""
    client = await _client(persona_runtime)
    client._thinker_force_reply_enabled = False
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT),
        ):
            mock_think.return_value = _think_ns(action="reply")
            result = await client.chat(
                session_id="group_100",
                user_id="100",
                user_content="",
                identity=identity_snapshot,
                group_id="100",
                force_reply=True,
                trigger=_trigger("at_mention", addressee_self=True),
            )
    finally:
        await client.close()
    assert result == "reply text"
    mock_think.assert_not_awaited()  # legacy skip


@pytest.mark.asyncio
async def test_force_reply_at_mention_honors_wait_for_deferral(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """A plain @-mention may honor a thinker `wait` — the scheduler's
    addressed-wait deferral (gated on mode==at_mention + _last_thinker_action
    =='wait') re-fires it. chat() returns None and leaves _last_thinker_action
    == 'wait' so the deferral path can pick it up."""
    client = await _client(persona_runtime)
    client._thinker_force_reply_enabled = True
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT) as main_call,
        ):
            mock_think.return_value = _think_ns(action="wait", thought="等用户说完")
            result = await client.chat(
                session_id="group_100",
                user_id="100",
                user_content="",
                identity=identity_snapshot,
                group_id="100",
                force_reply=True,
                trigger=_trigger("at_mention", addressee_self=True),
            )
    finally:
        await client.close()
    assert result is None  # wait honored
    assert client._last_thinker_action == "wait"  # deferral can re-fire
    main_call.assert_not_called()


@pytest.mark.asyncio
async def test_force_reply_correction_overrides_wait(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """#2 wait-guard: a non-@ obligated force_reply (correction) has NO deferral
    path, so a thinker `wait` must be overridden to reply — otherwise the
    obligated turn is silently dropped."""
    client = await _client(persona_runtime)
    client._thinker_force_reply_enabled = True
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT) as main_call,
        ):
            mock_think.return_value = _think_ns(action="wait", thought="先等等")
            result = await client.chat(
                session_id="group_100",
                user_id="100",
                user_content="",
                identity=identity_snapshot,
                group_id="100",
                force_reply=True,
                trigger=_trigger("correction"),
            )
    finally:
        await client.close()
    assert result == "reply text"  # wait overridden → reply emitted
    assert client._last_thinker_action == "reply"
    main_call.assert_called()


@pytest.mark.asyncio
async def test_force_reply_deferred_refire_overrides_wait(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """A deferred addressed re-fire (force_after_wait) is the LAST chance — its
    quiet window already elapsed, so a fresh `wait` must NOT defer again; the
    guard overrides it to reply even though the mode is at_mention."""
    client = await _client(persona_runtime)
    client._thinker_force_reply_enabled = True
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_MAIN_RESULT) as main_call,
        ):
            mock_think.return_value = _think_ns(action="wait", thought="还想等")
            result = await client.chat(
                session_id="group_100",
                user_id="100",
                user_content="",
                identity=identity_snapshot,
                group_id="100",
                force_reply=True,
                trigger=_trigger("at_mention", addressee_self=True, force_after_wait=True),
            )
    finally:
        await client.close()
    assert result == "reply text"  # no second deferral → reply
    assert client._last_thinker_action == "reply"
    main_call.assert_called()
