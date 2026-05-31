"""Client-level wiring tests for the Issue 15 instruction authority gate.

Focus: the DENY path must NOT touch the main LLM (D2 — no pollution of the
generation path), and shadow mode must never enforce.
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
    )


def _gate(mode: str = "active", **overrides) -> InstructionAuthorityGate:
    cfg = InstructionGateConfig(enabled=True, mode=mode, **overrides)
    return InstructionAuthorityGate(cfg, rng=random.Random(0))


async def test_deny_emits_refusal_and_stops(persona_runtime, tmp_path) -> None:
    store = AuthorityStore(storage_dir=str(tmp_path))
    client = await _client(persona_runtime, gate=_gate(), store=store)
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

    assert hint is None  # caller must stop — no main LLM
    assert len(segments) == 1
    assert segments[0].startswith("[CQ:reply,id=42]")  # quote the original message


async def test_deny_without_trigger_has_no_quote(persona_runtime, tmp_path) -> None:
    store = AuthorityStore(storage_dir=str(tmp_path))
    client = await _client(persona_runtime, gate=_gate(), store=store)
    segments: list[str] = []

    async def on_segment(text: str) -> bool:
        segments.append(text)
        return True

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

    assert hint is None
    assert len(segments) == 1
    assert not segments[0].startswith("[CQ:reply")


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
