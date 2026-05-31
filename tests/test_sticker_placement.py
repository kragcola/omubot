from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.humanization import create_humanization_state_bus
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.media.sticker_store import StickerStore
from services.memory.short_term import ShortTermMemory
from services.persona import PersonaRuntime
from services.tools.context import ToolContext
from services.tools.registry import ToolRegistry
from services.tools.sticker_tools import SendStickerTool

_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"jpeg-payload-a"


async def _client(tmp_path) -> tuple[LLMClient, str, MagicMock]:
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sticker_id, _ = store.add(_JPEG_DATA, "笑哭", "开心接梗时发")
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    tools = ToolRegistry()
    tools.register(SendStickerTool(store, runtime_state=runtime_state))
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=PersonaRuntime()),
        short_term=ShortTermMemory(),
        tools=tools,
        thinker_enabled=False,
        runtime_state=runtime_state,
        sticker_placement_config=SimpleNamespace(enabled=True, cooldown_ms=45_000),
    )
    return client, sticker_id, bot


async def test_post_reply_sticker_sends_when_thinker_requests_and_tool_not_used(tmp_path) -> None:
    client, _sticker_id, bot = await _client(tmp_path)
    try:
        with patch("nonebot.adapters.onebot.v11.MessageSegment.image", return_value=MagicMock()):
            sent = await client._send_post_reply_sticker_if_needed(
                reply="哈哈太好笑了。明天见。",
                thinker_decision=SimpleNamespace(sticker=True),
                session_id="group_123",
                group_id="123",
                user_id="456",
                turn_id="turn-1",
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                already_sent=False,
            )
    finally:
        await client.close()

    assert sent is True
    bot.send_group_msg.assert_awaited_once()


async def test_post_reply_sticker_skips_when_already_sent(tmp_path) -> None:
    client, _sticker_id, bot = await _client(tmp_path)
    try:
        with patch("nonebot.adapters.onebot.v11.MessageSegment.image", return_value=MagicMock()):
            sent = await client._send_post_reply_sticker_if_needed(
                reply="好的",
                thinker_decision=SimpleNamespace(sticker=True),
                session_id="group_123",
                group_id="123",
                user_id="456",
                turn_id="turn-1",
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                already_sent=True,
            )
    finally:
        await client.close()

    assert sent is False
    bot.send_group_msg.assert_not_awaited()
