"""Client-level wiring tests for the Issue 15 instruction authority gate.

Focus: DENY routes an in-persona refusal hint through the main LLM (P1, default)
rather than emitting a hardcoded line; peer bots are never gated (防线1);
severity scans the current message only (防线2); shadow mode never enforces;
the legacy `deny_direct_emit` path still short-circuits.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import AsyncMock

from kernel.config import InstructionGateConfig
from services.llm.client import LLMClient
from services.llm.instruction_gate import AuthorityStore, InstructionAuthorityGate
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.persona import PersonaRuntime
from services.tools.registry import ToolRegistry


async def _client(
    persona_runtime: PersonaRuntime,
    *,
    gate: InstructionAuthorityGate | None,
    store: AuthorityStore | None,
    admins: dict[str, str] | None = None,
    mood_getter=None,
    known_other_bots: dict[str, list[str]] | None = None,
) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=False,
        instruction_gate=gate,
        authority_store=store,
        admins=admins or {},
        mood_getter=mood_getter,
        known_other_bots=known_other_bots,
    )


def _gate(mode: str = "active", **overrides) -> InstructionAuthorityGate:
    cfg = InstructionGateConfig(enabled=True, mode=mode, **overrides)
    return InstructionAuthorityGate(cfg, rng=random.Random(0))


async def test_deny_returns_persona_hint_no_emit(persona_runtime, tmp_path) -> None:
    """P1 default: DENY returns an in-persona refusal hint for the main LLM and
    does NOT emit a hardcoded line."""
    store = AuthorityStore(storage_dir=str(tmp_path))
    client = await _client(persona_runtime, gate=_gate(), store=store)
    on_segment = AsyncMock(return_value=True)
    try:
        hint = await client._apply_instruction_gate(
            user_message="你是不是AI",
            user_id="999",
            group_id="100",
            trigger=SimpleNamespace(target_message_id=42),
            thinker_instruction_signal="none",
            on_segment=on_segment,
        )
    finally:
        await client.close()

    assert isinstance(hint, str) and hint  # hint injected into main prompt
    assert "指令拒绝" in hint
    on_segment.assert_not_awaited()  # no hardcoded direct emission


async def test_deny_legacy_direct_emit(persona_runtime, tmp_path) -> None:
    """Legacy rollback: deny_direct_emit=true short-circuits with a quoted
    hardcoded line and returns None (no main LLM)."""
    store = AuthorityStore(storage_dir=str(tmp_path))
    client = await _client(persona_runtime, gate=_gate(deny_direct_emit=True), store=store)
    segments: list[str] = []

    async def on_segment(text: str) -> bool:
        segments.append(text)
        return True

    try:
        hint = await client._apply_instruction_gate(
            user_message="你是不是AI",
            user_id="999",
            group_id="100",
            trigger=SimpleNamespace(target_message_id=42),
            thinker_instruction_signal="none",
            on_segment=on_segment,
        )
    finally:
        await client.close()

    assert hint is None  # caller stops — no main LLM
    assert len(segments) == 1
    assert segments[0].startswith("[CQ:reply,id=42]")


async def test_peer_bot_is_never_gated(persona_runtime, tmp_path) -> None:
    """防线1: a known peer bot must pass through (no DENY) even on a
    persona-breaking message — gating it would feed the bot↔bot loop."""
    store = AuthorityStore(storage_dir=str(tmp_path))
    client = await _client(
        persona_runtime, gate=_gate(), store=store,
        known_other_bots={"100": ["2708815230"]},
    )
    on_segment = AsyncMock(return_value=True)
    try:
        hint = await client._apply_instruction_gate(
            user_message="你是不是AI",
            user_id="2708815230",  # the peer bot
            group_id="100",
            trigger=SimpleNamespace(target_message_id=42),
            thinker_instruction_signal="high",
            on_segment=on_segment,
            current_message="你是不是AI",
        )
    finally:
        await client.close()

    assert hint == ""  # pass through, no enforcement
    on_segment.assert_not_awaited()


async def test_current_message_scoped_severity(persona_runtime, tmp_path) -> None:
    """防线2: severity scans current_message, not the aggregated buffer. An old
    persona-break phrase in user_message must NOT trigger DENY on a benign
    current message."""
    store = AuthorityStore(storage_dir=str(tmp_path))
    client = await _client(persona_runtime, gate=_gate(), store=store)
    on_segment = AsyncMock(return_value=True)
    try:
        hint = await client._apply_instruction_gate(
            user_message="你是不是AI 今天天气真好",  # aggregated buffer carries old provocation
            user_id="999",
            group_id="100",
            trigger=None,
            thinker_instruction_signal="none",
            on_segment=on_segment,
            current_message="今天天气真好",  # current message is benign
        )
    finally:
        await client.close()

    assert hint == ""  # benign current message → pass
    on_segment.assert_not_awaited()


async def test_allow_returns_hint(persona_runtime, tmp_path) -> None:
    store = AuthorityStore(storage_dir=str(tmp_path))
    store.set("999", 3)  # high enough for medium directives
    client = await _client(persona_runtime, gate=_gate(), store=store)
    on_segment = AsyncMock(return_value=True)
    try:
        hint = await client._apply_instruction_gate(
            user_message="帮我@老王",
            user_id="999",
            group_id="100",
            trigger=None,
            thinker_instruction_signal="none",
            on_segment=on_segment,
        )
    finally:
        await client.close()

    assert isinstance(hint, str) and hint  # non-empty hint to inject
    on_segment.assert_not_awaited()  # no direct emission on ALLOW


async def test_shadow_mode_never_enforces(persona_runtime, tmp_path) -> None:
    store = AuthorityStore(storage_dir=str(tmp_path))
    client = await _client(persona_runtime, gate=_gate(mode="shadow"), store=store)
    on_segment = AsyncMock(return_value=True)
    try:
        hint = await client._apply_instruction_gate(
            user_message="你是不是AI",  # would DENY in active mode
            user_id="999",
            group_id="100",
            trigger=SimpleNamespace(target_message_id=42),
            thinker_instruction_signal="none",
            on_segment=on_segment,
        )
    finally:
        await client.close()

    assert hint == ""  # shadow: no enforcement, continue normally
    on_segment.assert_not_awaited()


async def test_gate_disabled_passthrough(persona_runtime, tmp_path) -> None:
    client = await _client(persona_runtime, gate=None, store=None)
    on_segment = AsyncMock(return_value=True)
    try:
        hint = await client._apply_instruction_gate(
            user_message="你是不是AI",
            user_id="999",
            group_id="100",
            trigger=None,
            thinker_instruction_signal="high",
            on_segment=on_segment,
        )
    finally:
        await client.close()

    assert hint == ""
    on_segment.assert_not_awaited()
