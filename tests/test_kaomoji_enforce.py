from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from services.humanization import REGISTER_LABEL_SLOT, create_humanization_state_bus
from services.humanization.state import humanization_source
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot, PersonaRuntime
from services.system_module import RuntimeStateBus, Scope
from services.tools.registry import ToolRegistry


def _prompt(persona_runtime: PersonaRuntime) -> PromptBuilder:
    return PromptBuilder(persona_runtime=persona_runtime)


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
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
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
        prompt_builder=_prompt(persona_runtime),
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
                identity=identity_snapshot,
            )
    finally:
        await client.close()
    assert final_reply is not None
    return final_reply, mock_api.await_count


async def test_kaomoji_enforce_strips_kaomoji_emits_residual_prose(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    # #3: enforce fires (strict=False always fires when kaomoji present) → the
    # kaomoji span is stripped and the residual prose ("好耶") is emitted in a
    # SINGLE round. The old path forced a second "只发图" round that swallowed
    # all prose; that round no longer exists.
    final_reply, rounds = await _chat_reply(persona_runtime, identity_snapshot, strict=False)

    assert final_reply == "好耶"
    assert rounds == 1


async def test_kaomoji_enforce_strict_playful_strips_residual(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    final_reply, rounds = await _chat_reply(
        persona_runtime, identity_snapshot,
        strict=True,
        register_label="playful",
        mood={"label": "兴奋", "energy": 0.9, "valence": 0.6},
    )

    assert final_reply == "好耶"
    assert rounds == 1


async def test_kaomoji_enforce_strict_high_mood_strips_residual(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    final_reply, rounds = await _chat_reply(
        persona_runtime, identity_snapshot,
        strict=True,
        register_label="playful",
        mood={"label": "期待", "energy": 0.8, "valence": 0.5},
    )

    assert final_reply == "好耶"
    assert rounds == 1


async def test_kaomoji_enforce_pure_kaomoji_returns_empty(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    # #3 pure-kaomoji: no prose survives the strip → the reply becomes a wordless
    # sticker (the sticker stands as the reply), and chat() returns "". Uses two
    # inline kaomoji so the reply survives _clean_reply (a *solo* single
    # parenthetical is stripped upstream by _STAGE_DIR_SOLO_RE and never reaches
    # the enforce path — addressed turns get a context sticker via _fallback_ack
    # instead). No sticker store in this fixture, so the send is a no-op; the
    # point is that chat() returns "" rather than echoing the kaomoji as text.
    final_reply, rounds = await _chat_reply(
        persona_runtime, identity_snapshot,
        strict=False,
        reply="(≧▽≦)(｡･ω･｡)",
    )

    assert final_reply == ""
    assert rounds == 1


async def test_kaomoji_enforce_strict_blocks_non_playful_register(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    # Strict gate not satisfied → enforce does NOT fire → kaomoji preserved as-is.
    final_reply, rounds = await _chat_reply(
        persona_runtime, identity_snapshot,
        strict=True,
        register_label="quiet",
        mood={"label": "playful"},
    )

    assert final_reply == "好耶(≧▽≦)"
    assert rounds == 1


async def test_kaomoji_enforce_strict_blocks_non_playful_mood(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    final_reply, rounds = await _chat_reply(
        persona_runtime, identity_snapshot,
        strict=True,
        register_label="playful",
        mood={"label": "cold"},
    )

    assert final_reply == "好耶(≧▽≦)"
    assert rounds == 1


async def test_kaomoji_enforce_requires_kaomoji_text(
    persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    final_reply, rounds = await _chat_reply(
        persona_runtime, identity_snapshot,
        strict=True,
        register_label="playful",
        mood={"label": "playful"},
        reply="只是普通回复",
    )

    assert final_reply == "只是普通回复"
    assert rounds == 1
