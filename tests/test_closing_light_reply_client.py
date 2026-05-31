"""Client-level tests for the weak-reply P0 closing token generator.

Focus: `_gen_closing_token` returns a clean short token, never raises into the
main path (returns "" on failure), and a cancellation of the underlying call
propagates (D2 — speculative gen must not swallow outer cancellation).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.persona import PersonaRuntime
from services.tools.registry import ToolRegistry


async def _client(persona_runtime: PersonaRuntime) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=False,
    )


async def test_gen_closing_token_returns_clean_text(persona_runtime) -> None:
    client = await _client(persona_runtime)
    client._call = AsyncMock(return_value={"text": "晚安哦~明天见"})  # type: ignore[method-assign]
    try:
        token = await client._gen_closing_token(
            conversation_text="好吧晚安",
            mood_text="",
            user_id="1",
            group_id="100",
            identity_name="测试",
        )
    finally:
        await client.close()
    assert token == "晚安哦~明天见"


async def test_gen_closing_token_strips_quotes_and_caps(persona_runtime) -> None:
    client = await _client(persona_runtime)
    client._call = AsyncMock(return_value={"text": '"' + ("晚" * 40) + '"'})  # type: ignore[method-assign]
    try:
        token = await client._gen_closing_token(
            conversation_text="睡了",
            mood_text="",
            user_id="1",
            group_id="100",
            identity_name="测试",
        )
    finally:
        await client.close()
    assert '"' not in token
    assert len(token) <= 32


async def test_gen_closing_token_failure_returns_empty(persona_runtime) -> None:
    client = await _client(persona_runtime)
    client._call = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    try:
        token = await client._gen_closing_token(
            conversation_text="拜拜",
            mood_text="",
            user_id="1",
            group_id="100",
            identity_name="测试",
        )
    finally:
        await client.close()
    assert token == ""  # never raises into the main path; caller uses static fallback


async def test_gen_closing_token_cancellation_propagates(persona_runtime) -> None:
    """D2: if the underlying call is cancelled, _gen_closing_token must not
    swallow it into a fake token — cancellation has to propagate so the
    SpeculativeExecutor / chat task can unwind cleanly."""
    client = await _client(persona_runtime)

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(60)

    client._call = _hang  # type: ignore[method-assign]
    try:
        task = asyncio.create_task(
            client._gen_closing_token(
                conversation_text="先这样",
                mood_text="",
                user_id="1",
                group_id="100",
                identity_name="测试",
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await client.close()
