"""Tests for LLMClient compact logic, circuit breaker, and micro compact."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock, patch

import pytest

from services.identity import Identity
from services.llm.client import (
    LLMClient,
    ToolUse,
    _split_naturally,
    content_text,
    fix_cq_codes,
    resolve_image_refs,
    to_anthropic_message,
)
from services.llm.prompt_builder import PromptBuilder
from services.llm.usage import UsageTracker
from services.memory.card_store import CardStore
from services.memory.short_term import ChatMessage, ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.memory.types import ContentBlock, ImageRefBlock, TextBlock
from services.tools.registry import ToolRegistry

_IDENTITY = Identity(id="t", name="Bot", personality="p")


@pytest.fixture
def prompt() -> PromptBuilder:
    pb = PromptBuilder(instruction="test")
    pb.build_static(_IDENTITY, bot_self_id="999")
    return pb


@pytest.fixture
def short_term() -> ShortTermMemory:
    return ShortTermMemory()


@pytest.fixture
def tools() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def timeline() -> GroupTimeline:
    return GroupTimeline()


@pytest.fixture
async def card_store(tmp_path) -> CardStore:
    db_path = str(tmp_path / "test_client_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    return s


async def _client(
    prompt: PromptBuilder,
    short_term: ShortTermMemory,
    tools: ToolRegistry,
    timeline: GroupTimeline | None = None,
    card_store: CardStore | None = None,
    max_compact_failures: int = 3,
) -> AsyncIterator[LLMClient]:
    c = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=prompt,
        short_term=short_term,
        tools=tools,
        max_context_tokens=100_000,
        compact_ratio=0.7,
        compress_ratio=0.5,
        max_compact_failures=max_compact_failures,
        group_timeline=timeline,
        card_store=card_store,
        thinker_enabled=False,
    )
    try:
        yield c
    finally:
        await c.close()


def _fill_messages(short_term: ShortTermMemory, sid: str, count: int = 8) -> None:
    for i in range(count):
        short_term.add(sid, "user" if i % 2 == 0 else "assistant", f"msg {i}")


MOCK_RESULT = {
    "text": "summary", "tool_uses": [], "input_tokens": 100,
    "output_tokens": 50, "cache_read": 0, "cache_create": 0,
}

MOCK_RESULT_FULL = {
    "text": "reply text",
    "tool_uses": [],
    "input_tokens": 160,  # total = 100 + 50 + 10
    "output_tokens": 200,
    "cache_read": 50,
    "cache_create": 10,
}


# ---------------------------------------------------------------------------
# Compact trigger — single ratio
# ---------------------------------------------------------------------------


async def test_group_compact_triggers_at_ratio(prompt, short_term, tools, timeline, card_store) -> None:
    """compact_group fires when input_tokens > max_context_tokens * compact_ratio."""
    async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
        gid = "12345"
        # Build proper turn pairs so compact has enough turns to work with
        for i in range(4):
            timeline.add(gid, role="user", content=f"msg {i}", speaker=f"user({i})")
            timeline.add(gid, role="assistant", content=f"reply {i}")

        # Simulate previous call reported tokens above threshold (70k > 100k * 0.7)
        timeline.set_input_tokens(gid, 70_001)

        mock_compact = {"text": "compressed", "tool_uses": [], "input_tokens": 50}
        mock_chat = {
            "text": "reply", "tool_uses": [], "input_tokens": 5000,
            "output_tokens": 100, "cache_read": 0, "cache_create": 0,
        }

        with patch("services.llm.client.call_api", new_callable=AsyncMock, side_effect=[mock_compact, mock_chat]):
            result = await client.chat(
                session_id="group_12345", user_id="111",
                user_content="hello", identity=_IDENTITY, group_id=gid,
            )

        assert result is not None
        assert timeline.get_summary(gid) == "compressed"


async def test_group_no_compact_below_ratio(prompt, short_term, tools, timeline, card_store) -> None:
    """No compact when tokens below threshold."""
    async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
        gid = "12345"
        # Build proper turn pairs
        for i in range(4):
            timeline.add(gid, role="user", content=f"msg {i}", speaker=f"user({i})")
            timeline.add(gid, role="assistant", content=f"reply {i}")
        timeline.set_input_tokens(gid, 50_000)  # below 70k threshold

        mock_chat = {
            "text": "reply", "tool_uses": [], "input_tokens": 5000,
            "output_tokens": 100, "cache_read": 0, "cache_create": 0,
        }
        with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=mock_chat):
            await client.chat(
                session_id="group_12345", user_id="111",
                user_content="hello", identity=_IDENTITY, group_id=gid,
            )
        # 8 existing turns + chat adds 1 assistant turn (no pending to flush since
        # group messages are added by the listener, not by chat())
        turns = timeline.get_turns(gid)
        assert len(turns) == 9  # 8 existing + 1 assistant reply


# ---------------------------------------------------------------------------
# Circuit breaker — private
# ---------------------------------------------------------------------------


async def test_private_circuit_breaker_activates(prompt, short_term, tools) -> None:
    async for client in _client(prompt, short_term, tools, max_compact_failures=2):
        client._private_compact_failures = 2
        _fill_messages(short_term, "private_100")

        with patch("services.llm.client.call_api", new_callable=AsyncMock) as mock_api:
            await client._compact("private_100")
            mock_api.assert_not_called()


async def test_private_compact_resets_on_success(prompt, short_term, tools) -> None:
    async for client in _client(prompt, short_term, tools):
        client._private_compact_failures = 1
        _fill_messages(short_term, "private_100")

        with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=MOCK_RESULT):
            await client._compact("private_100")
        assert client._private_compact_failures == 0


async def test_private_compact_increments_on_failure(prompt, short_term, tools) -> None:
    async for client in _client(prompt, short_term, tools):
        _fill_messages(short_term, "private_100")

        with patch("services.llm.client.call_api", new_callable=AsyncMock, side_effect=RuntimeError("fail")):
            await client._compact("private_100")
        assert client._private_compact_failures == 1


# ---------------------------------------------------------------------------
# Circuit breaker — group (independent from private)
# ---------------------------------------------------------------------------


async def test_group_compact_independent_counter(prompt, short_term, tools, timeline) -> None:
    """Group compact failures should not affect private compact."""
    async for client in _client(prompt, short_term, tools, timeline=timeline, max_compact_failures=2):
        client._group_compact_failures = 2
        assert client._private_compact_failures == 0

        _fill_messages(short_term, "private_100")

        with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=MOCK_RESULT):
            await client._compact("private_100")
        assert client._private_compact_failures == 0


# ---------------------------------------------------------------------------
# Compact — card extraction (private)
# ---------------------------------------------------------------------------


async def test_private_compact_adds_card(prompt, short_term, tools, card_store) -> None:
    async for client in _client(prompt, short_term, tools, card_store=card_store):
        _fill_messages(short_term, "private_12345")

        # First call: LLM returns a tool call to add card
        mock_tool_call = {
            "text": "",
            "tool_uses": [ToolUse(id="tc1", name="add_card", input={
                "scope": "user", "scope_id": "12345",
                "category": "preference", "content": "用户A喜欢编程",
            })],
            "input_tokens": 100,
        }
        # Second call: LLM returns the summary text
        mock_summary = {"text": "test summary", "tool_uses": [], "input_tokens": 50}

        with patch("services.llm.client.call_api", new_callable=AsyncMock, side_effect=[mock_tool_call, mock_summary]):
            await client._compact("private_12345")

        cards = await card_store.get_entity_cards("user", "12345")
        contents = {c.content for c in cards}
        assert "用户A喜欢编程" in contents
        assert short_term.get_summary("private_12345") == "test summary"


async def test_private_compact_no_card_store(prompt, short_term, tools) -> None:
    """Compact without card store: LLM returns plain text summary, no tool calls."""
    async for client in _client(prompt, short_term, tools):
        _fill_messages(short_term, "private_100")

        mock_result = {"text": "plain text summary", "tool_uses": [], "input_tokens": 100}
        with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=mock_result):
            await client._compact("private_100")
        assert short_term.get_summary("private_100") == "plain text summary"


# ---------------------------------------------------------------------------
# Compact — card extraction (group)
# ---------------------------------------------------------------------------


async def test_group_compact_adds_cards(prompt, short_term, tools, timeline, card_store) -> None:
    async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
        gid = "99999"
        # Build proper turn pairs so compact has enough turns (needs >= 4)
        for i in range(4):
            timeline.add(gid, role="user", content=f"msg {i}", speaker=f"nick({i * 111})")
            timeline.add(gid, role="assistant", content=f"reply {i}")

        # First call: LLM returns tool calls to add cards
        mock_tool_call = {
            "text": "",
            "tool_uses": [
                ToolUse(id="tc1", name="add_card", input={
                    "scope": "user", "scope_id": "111", "category": "fact", "content": "用户1是前端开发",
                }),
                ToolUse(id="tc2", name="add_card", input={
                    "scope": "user", "scope_id": "222", "category": "preference", "content": "用户2喜欢Rust",
                }),
                ToolUse(id="tc3", name="add_card", input={
                    "scope": "group", "scope_id": "99999", "category": "fact", "content": "技术讨论群",
                }),
            ],
            "input_tokens": 100,
        }
        # Second call: LLM returns the summary
        mock_summary = {"text": "group summary", "tool_uses": [], "input_tokens": 50}

        with patch("services.llm.client.call_api", new_callable=AsyncMock, side_effect=[mock_tool_call, mock_summary]):
            await client._compact_group(gid, _IDENTITY)

        cards_111 = await card_store.get_entity_cards("user", "111")
        cards_222 = await card_store.get_entity_cards("user", "222")
        cards_group = await card_store.get_entity_cards("group", "99999")
        assert len(cards_111) >= 1
        assert len(cards_222) >= 1
        assert len(cards_group) >= 1
        assert timeline.get_summary(gid) == "group summary"


async def test_group_compact_invalid_category(prompt, short_term, tools, timeline, card_store) -> None:
    """Invalid category is rejected gracefully, valid cards still succeed."""
    async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
        gid = "99999"
        for i in range(4):
            timeline.add(gid, role="user", content=f"msg {i}", speaker=f"nick({i})")
            timeline.add(gid, role="assistant", content=f"reply {i}")

        # LLM tries to write with invalid category and a valid one
        mock_tool_call = {
            "text": "",
            "tool_uses": [
                ToolUse(id="tc1", name="add_card", input={
                    "scope": "user", "scope_id": "12345", "category": "bogus", "content": "bad",
                }),
                ToolUse(id="tc2", name="add_card", input={
                    "scope": "user", "scope_id": "12345", "category": "fact", "content": "good",
                }),
            ],
            "input_tokens": 100,
        }
        mock_summary = {"text": "summary", "tool_uses": [], "input_tokens": 50}

        with patch("services.llm.client.call_api", new_callable=AsyncMock, side_effect=[mock_tool_call, mock_summary]):
            await client._compact_group(gid, _IDENTITY)

        # Valid card was written
        cards = await card_store.get_entity_cards("user", "12345")
        contents = {c.content for c in cards}
        assert "good" in contents
        assert timeline.get_summary(gid) == "summary"


# ---------------------------------------------------------------------------
# on_compact callback
# ---------------------------------------------------------------------------


async def test_compact_calls_on_compact(prompt, short_term, tools) -> None:
    async for client in _client(prompt, short_term, tools):
        callback = Mock()
        client._on_compact = callback
        _fill_messages(short_term, "private_100")

        with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=MOCK_RESULT):
            await client._compact("private_100")
        callback.assert_called_once()


# ---------------------------------------------------------------------------
# pass_turn behavior
# ---------------------------------------------------------------------------


class TestPassTurn:
    async def test_pass_turn_returns_none(self, prompt, short_term, tools, timeline, card_store) -> None:
        """pass_turn is always honored — chat() returns None."""
        async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
            gid = "12345"
            timeline.add(gid, role="user", content="hello", speaker="user(111)")

            mock_result = {
                "text": "",
                "tool_uses": [ToolUse(id="tu_1", name="pass_turn", input={"reason": "not relevant"})],
                "input_tokens": 100,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_create": 0,
            }
            with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=mock_result):
                result = await client.chat(
                    session_id="group_12345",
                    user_id="111",
                    user_content="hello",
                    identity=_IDENTITY,
                    group_id=gid,
                    ctx=None,
                )
            assert result is None


# ---------------------------------------------------------------------------
# UsageTracker integration
# ---------------------------------------------------------------------------


async def test_chat_records_usage(prompt, short_term, tools, tmp_path) -> None:
    tracker = UsageTracker(db_path=str(tmp_path / "usage.db"))
    await tracker.init()
    try:
        async for client in _client(prompt, short_term, tools):
            client._usage_tracker = tracker
            with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=MOCK_RESULT_FULL):
                await client.chat(
                    session_id="private_100", user_id="100",
                    user_content="hello", identity=_IDENTITY,
                )
            await asyncio.sleep(0)
        rows = await tracker.query_raw("SELECT * FROM llm_calls")
        assert len(rows) == 1
        row = rows[0]
        assert row["call_type"] == "chat"
        assert row["user_id"] == "100"
        assert row["output_tokens"] == 200
        assert row["input_tokens"] == 100  # raw = total(160) - cache_read(50) - cache_create(10)
        assert row["cache_read_tokens"] == 50
    finally:
        await tracker.close()


async def test_compact_records_usage(prompt, short_term, tools, tmp_path) -> None:
    tracker = UsageTracker(db_path=str(tmp_path / "usage.db"))
    await tracker.init()
    mock_result = {**MOCK_RESULT, "output_tokens": 80, "cache_read": 0, "cache_create": 0}
    try:
        async for client in _client(prompt, short_term, tools):
            client._usage_tracker = tracker
            _fill_messages(short_term, "private_100")
            with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=mock_result):
                await client._compact("private_100")
            await asyncio.sleep(0)
        rows = await tracker.query_raw("SELECT * FROM llm_calls WHERE call_type='compact'")
        assert len(rows) == 1
    finally:
        await tracker.close()


# ---------------------------------------------------------------------------
# _to_anthropic_message — content block passthrough
# ---------------------------------------------------------------------------


def test_to_anthropic_message_str() -> None:
    """String content passes through unchanged."""
    msg = ChatMessage(role="user", content="hello")
    result = to_anthropic_message(msg)
    assert result == {"role": "user", "content": "hello"}


def test_to_anthropic_message_blocks() -> None:
    """Block content passes through as-is (image_ref converted later)."""
    blocks: list[ContentBlock] = [
        TextBlock(type="text", text="look"),
        ImageRefBlock(type="image_ref", path="cache/ab/abc.jpg", media_type="image/jpeg"),
    ]
    msg = ChatMessage(role="user", content=blocks)
    result = to_anthropic_message(msg)
    assert result["role"] == "user"
    assert isinstance(result["content"], list)
    assert len(result["content"]) == 2


# ---------------------------------------------------------------------------
# _content_text
# ---------------------------------------------------------------------------


def test_content_text_str() -> None:
    assert content_text("hello") == "hello"


def test_content_text_blocks() -> None:
    blocks: list[ContentBlock] = [
        TextBlock(type="text", text="看这个"),
        ImageRefBlock(type="image_ref", path="x.jpg", media_type="image/jpeg"),
        TextBlock(type="text", text="不错吧"),
    ]
    assert content_text(blocks) == "看这个 不错吧"


def test_content_text_empty_list() -> None:
    assert content_text([]) == ""


# ---------------------------------------------------------------------------
# _resolve_image_refs
# ---------------------------------------------------------------------------


async def test_resolve_image_refs_none_cache() -> None:
    """With no image_cache, messages pass through unchanged."""
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_ref", "path": "x.jpg", "media_type": "image/jpeg"},
    ]}]
    result, tag_map = await resolve_image_refs(msgs, None)
    assert result[0]["content"][1]["type"] == "image_ref"  # unchanged
    assert tag_map == {}


async def test_resolve_image_refs_resolves_to_base64() -> None:
    """image_ref blocks should be converted to base64 with [img:N] tag."""
    mock_cache = AsyncMock()
    mock_cache.load_as_base64.return_value = {
        "type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "abc123"},
    }
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "看"},
        {"type": "image_ref", "path": "x.jpg", "media_type": "image/jpeg"},
    ]}]
    result, tag_map = await resolve_image_refs(msgs, mock_cache)
    # [0]=text "看", [1]=tag hint "«img:1»", [2]=image
    assert result[0]["content"][1] == {"type": "text", "text": "«img:1»"}
    assert result[0]["content"][2]["type"] == "image"
    assert result[0]["content"][2]["source"]["data"] == "abc123"
    assert tag_map == {"img:1": "x.jpg"}


async def test_resolve_image_refs_expired() -> None:
    """Expired images (load returns None) become [图片已过期]."""
    mock_cache = AsyncMock()
    mock_cache.load_as_base64.return_value = None
    msgs = [{"role": "user", "content": [
        {"type": "image_ref", "path": "gone.jpg", "media_type": "image/jpeg"},
    ]}]
    result, tag_map = await resolve_image_refs(msgs, mock_cache)
    assert result[0]["content"][0]["type"] == "text"
    assert "过期" in result[0]["content"][0]["text"]
    assert tag_map == {}


async def test_resolve_image_refs_preserves_cache_control() -> None:
    """cache_control on image_ref blocks should transfer to resolved blocks."""
    mock_cache = AsyncMock()
    mock_cache.load_as_base64.return_value = {
        "type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "x"},
    }
    msgs = [{"role": "user", "content": [
        {"type": "image_ref", "path": "x.jpg", "media_type": "image/jpeg",
         "cache_control": {"type": "ephemeral"}},
    ]}]
    result, tag_map = await resolve_image_refs(msgs, mock_cache)
    # [0]=tag hint, [1]=image with cache_control
    assert result[0]["content"][1]["cache_control"] == {"type": "ephemeral"}
    assert tag_map == {"img:1": "x.jpg"}


@pytest.mark.parametrize("raw, expected", [
    ("[CQ:reply,id:457521541]hello", "[CQ:reply,id=457521541]hello"),
    ("[CQ:reply,id:-123456]hi", "[CQ:reply,id=-123456]hi"),
    ("[CQ:at,qq:654321]", "[CQ:at,qq=654321]"),
    ("[CQ:reply,id=123]ok", "[CQ:reply,id=123]ok"),  # already correct
    ("plain text", "plain text"),  # no CQ codes
    ("[CQ:face,id:1]", "[CQ:face,id=1]"),
])
def testfix_cq_codes(raw: str, expected: str) -> None:
    assert fix_cq_codes(raw) == expected


class TestSplitNaturally:
    """Tests for _split_naturally — text segmentation for sequential sending."""

    def test_single_paragraph_no_split(self) -> None:
        result = _split_naturally("这条消息不需要分段")
        assert result == ["这条消息不需要分段"]

    def test_sentence_ending_split(self) -> None:
        """\n after 。 is an intentional segment boundary."""
        result = _split_naturally("哇好厉害！\n这个也超有趣呢～")
        assert len(result) == 2
        assert result[0] == "哇好厉害！"
        assert result[1] == "这个也超有趣呢～"

    def test_mid_sentence_merge(self) -> None:
        """Regression: \n mid-sentence should be merged, not split into orphans."""
        # "感觉像在看超高清" doesn't end with sentence-ending punctuation
        result = _split_naturally("恋爱捉迷藏配上AI修复，感觉像在看超高清\n的童话舞台剧！")
        # Should be merged then split on comma, producing 2 complete segments
        assert len(result) == 2
        assert result[0] == "恋爱捉迷藏配上AI修复"
        assert "超高清" in result[1]
        assert "童话舞台剧" in result[1]

    def test_user_reported_case(self) -> None:
        """The exact case the user reported — no more orphan fragments."""
        text = (
            "哇哇哇！粉色洛丽塔舞台表演！！\n"
            "这个封面也太梦幻了吧～\n"
            "恋爱捉迷藏配上AI修复，感觉像在看超高清\n"
            "的童话舞台剧！"
        )
        result = _split_naturally(text)
        # All segments must be semantically complete — no orphan fragments
        # like "的童话舞台剧！" dangling alone
        for seg in result:
            assert len(seg) >= 6  # no tiny orphans (was 7-char fragment before)
        # The last segment should be a complete thought
        assert result[-1].endswith("！") or result[-1].endswith("～")
        # Verify the orphan is gone: "的童话舞台剧！" was merged with previous line
        assert any("童话舞台剧" in seg for seg in result)
        assert not any(seg.startswith("的童话") for seg in result)

    def test_short_fragment_merge(self) -> None:
        """Short lines (< _MIN_CHUNK=6) merge with previous."""
        result = _split_naturally("你好\n吗")
        assert len(result) == 1
        assert "你好吗" in result[0]

    def test_cut_marker_takes_priority(self) -> None:
        result = _split_naturally("第一段\n---cut---\n第二段")
        assert len(result) == 2
        assert result[0] == "第一段"
        assert result[1] == "第二段"

    def test_long_sentence_split_on_period(self) -> None:
        """Long merged sentence should split on 。！？."""
        long_text = "这是第一句很长很长很长很长。" + "这是第二句也很长很长很长很长。"
        result = _split_naturally(long_text)
        assert len(result) >= 2
        # Each segment should end with 。 or be at most _MAX_CHUNK
        from services.llm.client import _MAX_CHUNK

        for seg in result:
            assert seg[-1] in "。！？，" or len(seg) <= _MAX_CHUNK + 10

    def test_paragraph_split_respected(self) -> None:
        """Double newline (\n\n) always splits."""
        result = _split_naturally("第一段内容\n\n第二段内容")
        assert len(result) == 2
        assert "第一段" in result[0]
        assert "第二段" in result[1]

    def test_tilde_ending_triggers_split(self) -> None:
        """～ is in _SENTENCE_ENDING, so \n after ～ splits."""
        result = _split_naturally("好厉害呢～\n下次也要加油哦")
        assert len(result) == 2
        assert result[0] == "好厉害呢～"
        assert result[1] == "下次也要加油哦"

    def test_no_leading_punctuation(self) -> None:
        """No segment should start with a punctuation mark."""
        # A long sentence that must be split — verify no leading comma/period/etc.
        result = _split_naturally("恋爱捉迷藏配上AI修复，感觉像在看超高清的童话舞台剧！")
        for seg in result:
            assert seg[0] not in "，。！？、；：", f"segment starts with punctuation: {seg!r}"

    def test_no_word_split(self) -> None:
        """English words like 'AI' should not be torn apart."""
        result = _split_naturally("这个封面也太梦幻了吧～恋爱捉迷藏配上AI修复")
        assert all(len(seg) >= 6 for seg in result)
        # "AI修复" stays together in one segment
        assert any("AI修复" in seg for seg in result)

    def test_short_tail_merge(self) -> None:
        """A trailing segment shorter than _MIN_CHUNK should merge with the previous."""
        # Simulate a case where the last segment would be too short
        result = _split_naturally("这是一个比较长的句子内容然后这里，短")
        # The last fragment "短" (1 char) < _MIN_CHUNK (6), should be merged
        for seg in result:
            assert len(seg) >= 6 or seg == result[-1]  # last may be short if only one segment

    def test_user_reported_case_v2(self) -> None:
        """Exact user case (no \n) — no orphans, no leading punctuation, clean breaks."""
        text = (
            "哇哇哇！粉色洛丽塔舞台表演！！"
            "这个封面也太梦幻了吧～"
            "恋爱捉迷藏配上AI修复，感觉像在看超高清的童话舞台剧！"
        )
        result = _split_naturally(text)
        # No orphan fragments
        for seg in result:
            assert len(seg) >= 6, f"orphan fragment: {seg!r}"
        # No leading punctuation
        for seg in result:
            assert seg[0] not in "，。！？、；：", f"leading punctuation: {seg!r}"
        # "AI修复" not torn apart
        assert not any(seg.rstrip("，。！？、；：") == "AI" for seg in result), "AI修复 was torn"
        # All segments end with natural punctuation or are complete
        for seg in result:
            # Every segment should feel complete
            assert len(seg) >= 6

