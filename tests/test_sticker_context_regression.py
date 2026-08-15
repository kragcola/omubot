from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.humanization import create_humanization_state_bus
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.media.sticker_store import StickerStore
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot, PersonaRuntime
from services.sticker import StickerDecisionProvider
from services.tools.context import ToolContext
from services.tools.registry import ToolRegistry
from services.tools.sticker_tools import SendStickerTool

_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"sticker-context-regression"
_CONTEXT_USER_TEXT = "这段聊天是在说今天的小猫照片"
_CONTEXT_QUOTED_TEXT = "照片里的小猫已经睡着了。"
_CONTEXT_CONTENT = (
    f"{_CONTEXT_USER_TEXT}\n"
    "[QUOTED_MSG sender_id=alice sender_name=Alice]\n"
    f"{_CONTEXT_QUOTED_TEXT}\n"
    "[/QUOTED_MSG]"
)
_FEEDBACK_TEXT = "但是你根本发之前不看上边的字"
_QUOTED_FEEDBACK_TEXT = "请先看完这段内容，不要发送可爱表情包。"
_FEEDBACK_CONTENT = (
    f"{_FEEDBACK_TEXT}\n"
    "[QUOTED_MSG sender_id=alice sender_name=Alice]\n"
    f"{_QUOTED_FEEDBACK_TEXT}\n"
    "[/QUOTED_MSG]"
)


def _main_result(text: str) -> dict[str, object]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 120,
        "output_tokens": 20,
        "cache_read": 0,
        "cache_create": 0,
    }


def _client(tmp_path) -> tuple[LLMClient, StickerStore, MagicMock]:
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    store.add(_JPEG_DATA, "可爱", "适合开心时发送的可爱表情包")
    tools = ToolRegistry()
    tools.register(SendStickerTool(store, runtime_state=runtime_state))
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=PersonaRuntime()),
        short_term=ShortTermMemory(),
        tools=tools,
        group_timeline=GroupTimeline(),
        thinker_enabled=False,
        runtime_state=runtime_state,
        sticker_placement_config=SimpleNamespace(
            enabled=True,
            cooldown_ms=45_000,
            score_threshold=0.01,
        ),
    )
    return client, store, bot


async def _chat_with_context(
    client: LLMClient,
    bot: MagicMock,
    *,
    user_content: str,
) -> None:
    with (
        patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            return_value=_main_result("这个真的很可爱！"),
        ),
        patch("nonebot.adapters.onebot.v11.MessageSegment.image", return_value=MagicMock()),
    ):
        await client.chat(
            session_id="group_123",
            group_id="123",
            user_id="456",
            user_content=user_content,
            identity=IdentitySnapshot(
                id="test",
                name="Bot",
                personality="I am a bot.",
                proactive="Proactive rules.",
            ),
            ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
            trigger=SimpleNamespace(target_message_id=42, mode="reply"),
        )


async def test_post_reply_sticker_query_includes_current_user_and_quote(tmp_path) -> None:
    """Selection must see the current input instead of only the bot's reply."""
    client, store, bot = _client(tmp_path)
    queries: list[str] = []
    original_search = store.search_by_intent_scored

    def capture_query(query: str, top_k: int = 5) -> list[tuple[str, float]]:
        queries.append(query)
        return original_search(query, top_k=top_k)

    try:
        with patch.object(store, "search_by_intent_scored", side_effect=capture_query):
            await _chat_with_context(client, bot, user_content=_CONTEXT_CONTENT)
    finally:
        await client.close()

    assert queries
    assert _CONTEXT_USER_TEXT in queries[-1]
    assert _CONTEXT_QUOTED_TEXT in queries[-1]
    bot.send_group_msg.assert_awaited_once()


async def test_post_reply_sticker_feedback_veto_suppresses_send(tmp_path) -> None:
    """Explicit feedback on a quoted turn must not decorate the correction.

    The post-reply selector used to receive only the generated reply. That let a
    cheerful phrase in the reply attach a sticker even when the current user and
    the message they quoted explicitly asked the bot not to do so.
    """
    client, _store, bot = _client(tmp_path)
    decisions = []
    original_decide = StickerDecisionProvider.decide

    async def capture_decision(provider, context, **kwargs):
        decision = await original_decide(provider, context, **kwargs)
        decisions.append(decision)
        return decision

    try:
        with patch.object(StickerDecisionProvider, "decide", new=capture_decision):
            await _chat_with_context(client, bot, user_content=_FEEDBACK_CONTENT)
    finally:
        await client.close()

    assert decisions
    assert decisions[-1].reason == "user_feedback_veto"
    bot.send_group_msg.assert_not_awaited()


async def test_post_reply_sticker_feedback_veto_overrides_force_send(tmp_path) -> None:
    """A correction must also win over the kaomoji/sticker-only force path."""
    client, _store, bot = _client(tmp_path)
    try:
        with patch("nonebot.adapters.onebot.v11.MessageSegment.image", return_value=MagicMock()):
            sent = await client._send_post_reply_sticker_if_needed(
                reply="好的，我会先看文字。",
                user_content=_FEEDBACK_CONTENT,
                thinker_decision=SimpleNamespace(sticker=True),
                session_id="group_123",
                group_id="123",
                user_id="456",
                turn_id="turn-force",
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                already_sent=False,
                force_send=True,
            )
    finally:
        await client.close()

    assert sent is False
    bot.send_group_msg.assert_not_awaited()
