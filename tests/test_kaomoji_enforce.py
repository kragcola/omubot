from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from services.humanization import REGISTER_LABEL_SLOT, create_humanization_state_bus
from services.humanization.state import humanization_source
from services.identity import Identity
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.system_module import RuntimeStateBus, Scope
from services.tools.registry import ToolRegistry

_IDENTITY = Identity(id="t", name="Bot", personality="p")


def _prompt() -> PromptBuilder:
    prompt = PromptBuilder(instruction="test")
    prompt.build_static(_IDENTITY, bot_self_id="999")
    return prompt


def _result(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 120,
        "output_tokens": 20,
        "cache_read": 0,
        "cache_create": 0,
    }


def _scope() -> Scope:
    return Scope(session_id="group_100", group_id="100", user_id="u1")


def _set_register(runtime_state: RuntimeStateBus, label: str) -> None:
    runtime_state.set(
        REGISTER_LABEL_SLOT,
        {"label": label},
        scope=_scope(),
        source=humanization_source("tests/test_kaomoji_enforce.py"),
        confidence=1.0,
    )


async def _chat_reply(
    *,
    strict: bool,
    register_label: str | None = None,
    mood: object | None = None,
    reply: str = "好耶(≧▽≦)",
) -> tuple[str, int]:
    runtime_state = create_humanization_state_bus()
    if register_label is not None:
        _set_register(runtime_state, register_label)
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=GroupTimeline(),
        thinker_enabled=False,
        runtime_state=runtime_state,
        mood_getter=(lambda **_: mood),
        humanization_kaomoji_enforce_strict=strict,
    )
    try:
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            side_effect=[_result(reply), _result("第二轮补图")],
        ) as mock_api:
            final_reply = await client.chat(
                session_id="group_100",
                group_id="100",
                user_id="u1",
                user_content="hello",
                identity=_IDENTITY,
            )
    finally:
        await client.close()
    assert final_reply is not None
    return final_reply, mock_api.await_count


async def test_kaomoji_enforce_strict_default_preserves_v1_round() -> None:
    final_reply, rounds = await _chat_reply(strict=False)

    assert final_reply == "第二轮补图"
    assert rounds == 2


async def test_kaomoji_enforce_strict_allows_playful_mood() -> None:
    final_reply, rounds = await _chat_reply(
        strict=True,
        register_label="playful",
        mood={"label": "playful"},
    )

    assert final_reply == "第二轮补图"
    assert rounds == 2


async def test_kaomoji_enforce_strict_allows_high_mood() -> None:
    final_reply, rounds = await _chat_reply(
        strict=True,
        register_label="playful",
        mood={"label": "high"},
    )

    assert final_reply == "第二轮补图"
    assert rounds == 2


async def test_kaomoji_enforce_strict_blocks_non_playful_register() -> None:
    final_reply, rounds = await _chat_reply(
        strict=True,
        register_label="quiet",
        mood={"label": "playful"},
    )

    assert final_reply == "好耶(≧▽≦)"
    assert rounds == 1


async def test_kaomoji_enforce_strict_blocks_non_playful_mood() -> None:
    final_reply, rounds = await _chat_reply(
        strict=True,
        register_label="playful",
        mood={"label": "cold"},
    )

    assert final_reply == "好耶(≧▽≦)"
    assert rounds == 1


async def test_kaomoji_enforce_requires_kaomoji_text() -> None:
    final_reply, rounds = await _chat_reply(
        strict=True,
        register_label="playful",
        mood={"label": "playful"},
        reply="只是普通回复",
    )

    assert final_reply == "只是普通回复"
    assert rounds == 1
