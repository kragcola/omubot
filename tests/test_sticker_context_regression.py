from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from kernel.config import ResolvedHumanization
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


def _client(
    tmp_path,
    *,
    humanization: ResolvedHumanization | None = None,
) -> tuple[LLMClient, StickerStore, MagicMock]:
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
        humanization_resolver=lambda _group_id: humanization or ResolvedHumanization(),
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


async def test_literal_do_not_send_veto_suppresses_post_reply_sticker(tmp_path) -> None:
    """A bare reply of "do not send" is an unambiguous sticker correction."""
    client, _store, bot = _client(tmp_path)
    try:
        await _chat_with_context(client, bot, user_content="不要发")
    finally:
        await client.close()

    bot.send_group_msg.assert_not_awaited()


async def test_feedback_veto_strips_model_visual_cq_to_text_fallback(tmp_path) -> None:
    """A model cannot bypass a sticker correction with raw visual CQ codes."""
    client, _store, bot = _client(tmp_path)
    raw_visual_reply = (
        "[CQ:image,file=base64://AAAA,summary=[动画表情]]"
        "[CQ:mface,emoji_id=sticker]"
        "[CQ:face,id=1]"
    )
    try:
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            return_value=_main_result(raw_visual_reply),
        ):
            reply = await client.chat(
                session_id="group_123",
                group_id="123",
                user_id="456",
                user_content="不要发",
                identity=IdentitySnapshot(
                    id="test",
                    name="Bot",
                    personality="I am a bot.",
                    proactive="Proactive rules.",
                ),
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                trigger=SimpleNamespace(target_message_id=42, mode="reply"),
                force_reply=True,
            )
    finally:
        await client.close()

    assert reply
    assert "[CQ:image" not in reply
    assert "[CQ:mface" not in reply
    assert "[CQ:face" not in reply
    bot.send_group_msg.assert_not_awaited()


async def test_feedback_veto_sanitizes_light_token_before_callback_and_timeline(tmp_path) -> None:
    """Closing/greeting emits must not bypass the feedback veto with raw CQ."""
    client, _store, _bot = _client(tmp_path)
    emitted: list[str] = []
    raw_visual = "[CQ:reply,id=42][CQ:at,qq=456][CQ:mface,emoji_id=sticker]"

    async def on_segment(segment: str) -> bool:
        emitted.append(segment)
        return True

    client._call = AsyncMock(return_value={"text": raw_visual})  # type: ignore[method-assign]
    client._maybe_light_reply_sticker = AsyncMock()  # type: ignore[method-assign]
    try:
        with patch(
            "services.llm.client._pick_empty_visible_reply_fallback",
            return_value="我会用文字回应。",
        ):
            result = await client._handle_light_reply(
                light_kind="closing",
                thinker_action="light_reply",
                conversation_text="不要发",
                mood_text="",
                user_id="456",
                group_id="123",
                identity_name="Bot",
                trigger=None,
                on_segment=on_segment,
                timeline=client._timeline,
                thinker_usage={},
                session_id="group_123",
                t0=0.0,
                block_visual_cq=True,
            )
    finally:
        await client.close()

    assert result is not None
    assert emitted == ["[CQ:reply,id=42][CQ:at,qq=456]我会用文字回应。"]
    assert client._timeline is not None
    assert "[CQ:mface" not in client._timeline.get_turns("123")[-1]["content"]


async def test_feedback_veto_sanitizes_plan_then_utter_before_callback_and_timeline(tmp_path) -> None:
    """The proactive plan path keeps callback state aligned with what it sends."""
    humanization = ResolvedHumanization(plan_then_utter_enabled=True)
    client, _store, _bot = _client(tmp_path, humanization=humanization)
    emitted: list[str] = []
    raw_visual = "[CQ:reply,id=42][CQ:at,qq=456][CQ:image,file=base64://AAAA]"

    async def on_segment(segment: str) -> bool:
        emitted.append(segment)
        return True

    client._call = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _main_result('{"utterances":["先接住","再补一句"]}'),
            _main_result(raw_visual),
            _main_result("我会先看文字。"),
        ]
    )
    try:
        with (
            patch("services.llm.client.asyncio.sleep", new_callable=AsyncMock, return_value=None),
            patch(
                "services.llm.client._pick_empty_visible_reply_fallback",
                return_value="我会用文字回应。",
            ),
        ):
            reply = await client._maybe_plan_then_utter(
                system_blocks=[],
                messages=[],
                session_id="group_123",
                group_id="123",
                user_id="456",
                turn_id="turn-1",
                humanization=humanization,
                on_segment=on_segment,
                tool_defs=[],
                is_group=True,
                force_reply=False,
                user_content="不要发",
                source_message_id=None,
                thinker_action="reply",
                thinker_thought="",
                tool_call_records=[],
                started_at=0.0,
                trigger_mode="",
                block_visual_cq=True,
            )
    finally:
        await client.close()

    assert reply == ""
    assert emitted == [
        "[CQ:reply,id=42][CQ:at,qq=456]我会用文字回应。",
        "我会先看文字。",
    ]
    assert client._timeline is not None
    assert "[CQ:image" not in client._timeline.get_turns("123")[-1]["content"]


async def test_feedback_veto_sanitizes_pause_extend_before_callback_and_timeline(tmp_path) -> None:
    """An extension cannot add a raw face CQ after an initial text response."""
    humanization = ResolvedHumanization(pause_then_extend_enabled=True)
    client, _store, _bot = _client(tmp_path, humanization=humanization)
    emitted: list[str] = []
    raw_visual = "[CQ:reply,id=42][CQ:at,qq=456][CQ:face,id=1]"

    async def on_segment(segment: str) -> bool:
        emitted.append(segment)
        return True

    client._call = AsyncMock(return_value=_main_result(raw_visual))  # type: ignore[method-assign]
    try:
        with (
            patch("services.llm.client.asyncio.sleep", new_callable=AsyncMock, return_value=None),
            patch(
                "services.llm.client._pick_empty_visible_reply_fallback",
                return_value="我会用文字回应。",
            ),
        ):
            emitted_extensions = await client._maybe_extend(
                last_reply="我先说第一层，不过，",
                system_blocks=[],
                messages=[],
                session_id="group_123",
                group_id="123",
                user_id="456",
                turn_id="turn-1",
                humanization=humanization,
                on_segment=on_segment,
                block_visual_cq=True,
            )
    finally:
        await client.close()

    assert emitted_extensions == ["[CQ:reply,id=42][CQ:at,qq=456]我会用文字回应。"]
    assert emitted == emitted_extensions
    assert client._timeline is not None
    assert "[CQ:face" not in client._timeline.get_turns("123")[-1]["content"]


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


async def test_send_sticker_tool_use_feedback_veto_blocks_legacy_and_direct_paths(tmp_path) -> None:
    """A model tool call must not bypass the current-turn feedback veto."""
    client, store, bot = _client(tmp_path)
    sticker_id = next(iter(store.list_all()))
    tool_ctx = ToolContext(
        bot=bot,
        user_id="456",
        group_id="123",
        session_id="group_123",
        extra={"sticker_user_feedback_veto": True},
    )
    tool_use = SimpleNamespace(
        id="tool-veto",
        name="send_sticker",
        input={"sticker_id": sticker_id},
    )
    try:
        legacy_result = await client._dispatch_tool_uses(
            tool_uses=(tool_use,),
            tool_ctx=tool_ctx,
            runtime_invocation_id=None,
        )
        direct_tool = client._tools.get("send_sticker")
        assert direct_tool is not None
        direct_result = await direct_tool.execute(tool_ctx, sticker_id=sticker_id)
    finally:
        await client.close()

    expected = "Tool error: send_sticker suppressed by explicit user feedback"
    assert legacy_result == [expected]
    assert direct_result == expected
    bot.send_group_msg.assert_not_awaited()


async def test_vetoed_sticker_tool_use_keeps_force_reply_text_fallback(tmp_path) -> None:
    """A rejected sticker tool use cannot turn the reply floor into a sticker."""
    client, store, bot = _client(tmp_path)
    sticker_id = next(iter(store.list_all()))
    tool_use = SimpleNamespace(
        id="tool-veto-fallback",
        name="send_sticker",
        input={"sticker_id": sticker_id},
    )
    first_round = _main_result("")
    first_round["tool_uses"] = [tool_use]

    try:
        with (
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                side_effect=[first_round, _main_result("")],
            ) as call_api,
        ):
            reply = await client.chat(
                session_id="private_456",
                group_id=None,
                user_id="456",
                user_content=_FEEDBACK_CONTENT,
                identity=IdentitySnapshot(
                    id="test",
                    name="Bot",
                    personality="I am a bot.",
                    proactive="Proactive rules.",
                ),
                ctx=ToolContext(bot=bot, user_id="456", session_id="private_456"),
                trigger=SimpleNamespace(target_message_id=42, mode="reply"),
                force_reply=True,
            )
    finally:
        await client.close()

    assert reply
    assert "[CQ:image" not in reply
    assert call_api.await_count == 2
    bot.send_group_msg.assert_not_awaited()


async def test_failed_sticker_tool_use_keeps_force_reply_text_fallback(tmp_path) -> None:
    """A failed send must not make the subsequent reply floor emit another sticker."""
    client, store, bot = _client(tmp_path)
    sticker_id = next(iter(store.list_all()))
    bot.send_group_msg.side_effect = RuntimeError("onebot send failed")
    assert client._timeline is not None
    # Group fallback lookup reads finalized timeline turns, so seed the matching
    # user intent that was already present before the current failed send.
    client._timeline.add(
        "123",
        role="user",
        speaker="Alice(456)",
        content="可爱表情包",
        message_id=41,
    )
    client._timeline.add("123", role="assistant", content="嗯嗯")
    tool_use = SimpleNamespace(
        id="tool-send-failure",
        name="send_sticker",
        input={"sticker_id": sticker_id},
    )
    first_round = _main_result("")
    first_round["tool_uses"] = [tool_use]

    try:
        with (
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                side_effect=[first_round, _main_result("")],
            ) as call_api,
            patch("nonebot.adapters.onebot.v11.MessageSegment.image", return_value=MagicMock()),
        ):
            reply = await client.chat(
                session_id="group_123",
                group_id="123",
                user_id="456",
                user_content=_CONTEXT_CONTENT,
                identity=IdentitySnapshot(
                    id="test",
                    name="Bot",
                    personality="I am a bot.",
                    proactive="Proactive rules.",
                ),
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                trigger=SimpleNamespace(target_message_id=42, mode="reply"),
                force_reply=True,
            )
    finally:
        await client.close()

    assert reply
    assert "[CQ:image" not in reply
    assert call_api.await_count == 2
    bot.send_group_msg.assert_awaited_once()


async def test_feedback_veto_uses_text_only_empty_reply_fallback_without_tool_call(tmp_path) -> None:
    """Current-turn feedback also constrains an empty direct response fallback."""
    client, _store, bot = _client(tmp_path)
    try:
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            return_value=_main_result(""),
        ):
            reply = await client.chat(
                session_id="private_456",
                group_id=None,
                user_id="456",
                user_content=_FEEDBACK_CONTENT,
                identity=IdentitySnapshot(
                    id="test",
                    name="Bot",
                    personality="I am a bot.",
                    proactive="Proactive rules.",
                ),
                ctx=ToolContext(bot=bot, user_id="456", session_id="private_456"),
                trigger=SimpleNamespace(target_message_id=42, mode="reply"),
                force_reply=True,
            )
    finally:
        await client.close()

    assert reply
    assert "[CQ:image" not in reply
    bot.send_group_msg.assert_not_awaited()


async def test_feedback_vetoed_pure_kaomoji_keeps_force_reply_text_fallback(tmp_path) -> None:
    """A vetoed kaomoji conversion cannot turn an obligated reply into silence."""
    client, _store, bot = _client(tmp_path)
    try:
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            return_value=_main_result("(≧▽≦)(｡･ω･｡)"),
        ):
            reply = await client.chat(
                session_id="group_123",
                group_id="123",
                user_id="456",
                user_content=_FEEDBACK_CONTENT,
                identity=IdentitySnapshot(
                    id="test",
                    name="Bot",
                    personality="I am a bot.",
                    proactive="Proactive rules.",
                ),
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                trigger=SimpleNamespace(target_message_id=42, mode="reply"),
                force_reply=True,
            )
    finally:
        await client.close()

    assert reply
    assert "[CQ:image" not in reply
    bot.send_group_msg.assert_not_awaited()


async def test_sticker_only_failed_delivery_blocks_second_tool_send(tmp_path) -> None:
    """An ambiguous sticker-only failure cannot be retried by the main tool loop."""
    client, store, bot = _client(tmp_path)
    sticker_id = next(iter(store.list_all()))
    client._thinker_enabled = True
    bot.send_group_msg.side_effect = [RuntimeError("onebot response lost"), None]
    thinker_decision = SimpleNamespace(
        action="light_reply",
        light_kind="sticker_only",
        sticker=True,
        thought="",
        topic_intent_label="闲聊",
        retrieve_mode="skip",
        rewritten_query="",
        instruction_signal="none",
        usage={},
        tone="元气",
        reply_necessity="high",
        unknown_terms=[],
    )
    retry_tool_use = SimpleNamespace(
        id="tool-after-light-failure",
        name="send_sticker",
        input={"sticker_id": sticker_id},
    )
    first_main_round = _main_result("")
    first_main_round["tool_uses"] = [retry_tool_use]

    try:
        with (
            patch(
                "services.llm.thinker.think",
                new=AsyncMock(return_value=thinker_decision),
            ),
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                side_effect=[first_main_round, _main_result("我知道了，只用文字回应。")],
            ),
            patch("nonebot.adapters.onebot.v11.MessageSegment.image", return_value=MagicMock()),
        ):
            reply = await client.chat(
                session_id="group_123",
                group_id="123",
                user_id="456",
                user_content=_CONTEXT_CONTENT,
                identity=IdentitySnapshot(
                    id="test",
                    name="Bot",
                    personality="I am a bot.",
                    proactive="Proactive rules.",
                ),
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
            )
    finally:
        await client.close()

    assert reply and "只用文字回应" in reply
    assert bot.send_group_msg.await_count == 1


async def test_failed_sticker_only_attempt_sanitizes_pause_extend_visual_cq(tmp_path) -> None:
    """A failed sticker attempt also blocks raw visual CQ from a delayed extension."""
    humanization = ResolvedHumanization(
        pause_then_extend_enabled=True,
        disable_natural_split=True,
    )
    client, store, bot = _client(tmp_path, humanization=humanization)
    sticker_id = next(iter(store.list_all()))
    client._thinker_enabled = True
    bot.send_group_msg.side_effect = [RuntimeError("onebot response lost"), None]
    emitted: list[str] = []
    thinker_decision = SimpleNamespace(
        action="light_reply",
        light_kind="sticker_only",
        sticker=True,
        thought="",
        topic_intent_label="闲聊",
        retrieve_mode="skip",
        rewritten_query="",
        instruction_signal="none",
        usage={},
        tone="元气",
        reply_necessity="high",
        unknown_terms=[],
    )
    retry_tool_use = SimpleNamespace(
        id="tool-after-light-failure-extend",
        name="send_sticker",
        input={"sticker_id": sticker_id},
    )
    first_main_round = _main_result("")
    first_main_round["tool_uses"] = [retry_tool_use]

    async def on_segment(segment: str) -> bool:
        emitted.append(segment)
        return True

    try:
        with (
            patch(
                "services.llm.thinker.think",
                new=AsyncMock(return_value=thinker_decision),
            ),
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                side_effect=[
                    first_main_round,
                    _main_result("我知道了，只用文字回应，不过，"),
                    _main_result("[CQ:image,file=base64://AAAA,sub_type=1]"),
                ],
            ),
            patch("services.llm.client.asyncio.sleep", new_callable=AsyncMock, return_value=None),
            patch(
                "services.llm.client.PauseExtend.decide",
                side_effect=[
                    SimpleNamespace(should_extend=True, wait_seconds=0.0, reasons=()),
                    SimpleNamespace(should_extend=False, wait_seconds=0.0, reasons=()),
                ],
            ),
            patch(
                "services.llm.client._pick_empty_visible_reply_fallback",
                return_value="我会用文字回应。",
            ),
            patch("nonebot.adapters.onebot.v11.MessageSegment.image", return_value=MagicMock()),
        ):
            await client.chat(
                session_id="group_123",
                group_id="123",
                user_id="456",
                user_content=_CONTEXT_CONTENT,
                identity=IdentitySnapshot(
                    id="test",
                    name="Bot",
                    personality="I am a bot.",
                    proactive="Proactive rules.",
                ),
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                on_segment=on_segment,
            )
    finally:
        await client.close()

    assert emitted
    assert all("[CQ:image" not in segment for segment in emitted)
    assert emitted[-1] == "我会用文字回应。"
    assert bot.send_group_msg.await_count == 1
    assert client._timeline is not None
    assert "[CQ:image" not in "\n".join(
        str(turn["content"]) for turn in client._timeline.get_turns("123")
    )


async def test_governed_sticker_veto_filters_and_reassembles_results(tmp_path) -> None:
    """A vetoed sticker never reaches the governed dispatcher or shifts results."""
    client, store, bot = _client(tmp_path)
    sticker_id = next(iter(store.list_all()))

    class _RecordingDispatcher:
        def __init__(self) -> None:
            self.calls: list[tuple[object, tuple[dict[str, object], ...], object]] = []

        async def dispatch_tool_uses(self, *, invocation_id, tool_uses, bot):
            self.calls.append((invocation_id, tool_uses, bot))
            return ["governed read result"]

    dispatcher = _RecordingDispatcher()
    client.set_runtime_tool_dispatcher(dispatcher)
    tool_ctx = ToolContext(
        bot=bot,
        user_id="456",
        group_id="123",
        session_id="group_123",
        extra={"sticker_user_feedback_veto": True},
    )
    tool_uses = (
        SimpleNamespace(
            id="tool-veto-governed",
            name="send_sticker",
            input={"sticker_id": sticker_id},
        ),
        SimpleNamespace(
            id="tool-read-governed",
            name="read_runtime_state",
            input={"key": "status"},
        ),
    )
    try:
        results = await client._dispatch_tool_uses(
            tool_uses=tool_uses,
            tool_ctx=tool_ctx,
            runtime_invocation_id="invocation-1",
        )
    finally:
        await client.close()

    expected = "Tool error: send_sticker suppressed by explicit user feedback"
    assert results == [expected, "governed read result"]
    assert dispatcher.calls == [
        (
            "invocation-1",
            (
                {
                    "id": "tool-read-governed",
                    "name": "read_runtime_state",
                    "arguments": {"key": "status"},
                },
            ),
            bot,
        )
    ]
    bot.send_group_msg.assert_not_awaited()


async def test_group_pending_source_is_not_appended_twice_for_continuation(tmp_path) -> None:
    """Scheduler fallback content is already represented in GroupTimeline.pending."""
    client, _store, bot = _client(tmp_path)
    assert client._timeline is not None
    client._timeline.add(
        "123",
        role="user",
        speaker="Alice(456)",
        content="你怎么不叫",
        message_id=99,
    )
    captured_messages: list[list[object]] = []

    async def capture_call(*args, **kwargs):
        del kwargs
        captured_messages.append(args[5])
        return _main_result("叫啦")

    try:
        with patch("services.llm.client.call_api", new=AsyncMock(side_effect=capture_call)):
            await client.chat(
                session_id="group_123",
                group_id="123",
                user_id="456",
                user_content="你怎么不叫",
                identity=IdentitySnapshot(
                    id="test",
                    name="Bot",
                    personality="I am a bot.",
                    proactive="Proactive rules.",
                ),
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                trigger=None,
            )
    finally:
        await client.close()

    user_tail_occurrences = sum(
        str(message.get("content", "")).count("你怎么不叫")
        for message in captured_messages[-1]
        if isinstance(message, dict)
        and message.get("role") == "user"
    )
    assert user_tail_occurrences == 1
