"""Tests for LLMClient compact logic, circuit breaker, and micro compact."""

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from kernel.config import GroupConfig, GroupOverride, ReplySegmentationConfig
from services.identity import Identity
from services.llm.client import (
    LLMClient,
    RateLimitError,
    ToolUse,
    content_text,
    fix_cq_codes,
    resolve_image_refs,
    to_anthropic_message,
)
from services.llm.llm_request import LLMRequest
from services.llm.prompt_builder import PromptBuilder
from services.llm.segmentation import reply_segment_plan
from services.llm.usage import UsageTracker
from services.memory.card_store import CardStore
from services.memory.short_term import ChatMessage, ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.memory.types import ContentBlock, ImageRefBlock, TextBlock
from services.tools.registry import ToolRegistry

_IDENTITY = Identity(id="t", name="Bot", personality="p")


def _normalize_reply(text: str | None) -> str:
    """Strip whitespace, commas, and trailing periods that natural_split may mutate."""
    if text is None:
        return ""
    return text.replace("\n", "").replace(" ", "").replace("，", "").rstrip("。")


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
    group_config: GroupConfig | None = None,
    reply_segmentation_config: ReplySegmentationConfig | None = None,
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
        group_config=group_config,
        reply_segmentation_config=reply_segmentation_config,
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


def test_reply_segments_can_disable_natural_split() -> None:
    cfg = ReplySegmentationConfig(
        enabled=True,
        natural_split_enabled=False,
        max_segment_chars=6,
    )
    text = "第一句很长很长。第二句很长很长。"

    plan = reply_segment_plan(fix_cq_codes(text), cfg)

    assert plan.segments == [text]
    assert plan.raw_count == 1
    assert plan.limit_status == "none"
    assert plan.inter_segment_delays == []


async def test_chat_uses_injected_reply_segmentation_config(prompt, short_term, tools, timeline) -> None:
    """Production chat path should honor config.reply_segmentation, not old client constants."""
    cfg = ReplySegmentationConfig(
        max_segment_chars=6,
        max_send_segments=2,
        inter_segment_delay_s=0.0,
    )
    text = "第一句很长很长。第二句很长很长。第三句很长很长。"
    sent: list[str] = []

    async def _on_segment(seg: str) -> None:
        sent.append(seg)

    async for client in _client(
        prompt,
        short_term,
        tools,
        timeline=timeline,
        reply_segmentation_config=cfg,
    ):
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            return_value={**MOCK_RESULT_FULL, "text": text},
        ):
            result = await client.chat(
                session_id="group_12345",
                user_id="111",
                user_content="hello",
                identity=_IDENTITY,
                group_id="12345",
                on_segment=_on_segment,
            )

    assert len(sent) <= 1
    assert result is not None
    timeline_text = timeline.get_turns("12345")[-1]["content"]
    assert result in timeline_text
    if sent:
        assert sent[0] in timeline_text
    assert short_term.get("group_12345") == []


class _StaticTool:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} description"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    def to_openai_tool(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, ctx, **kwargs):
        return "ok"


class _Bus:
    def __init__(self) -> None:
        self.post_reply_calls: list[object] = []
        self.thinker_calls: list[object] = []

    async def fire_on_pre_prompt(self, prompt_ctx) -> None:
        return None

    async def fire_on_post_reply(self, reply_ctx) -> None:
        self.post_reply_calls.append(reply_ctx)

    async def fire_on_thinker_decision(self, thinker_ctx) -> None:
        self.thinker_calls.append(thinker_ctx)


# ---------------------------------------------------------------------------
# Provider profile rate limit state
# ---------------------------------------------------------------------------


async def test_profile_rate_limit_cooldown_is_per_profile(prompt, short_term, tools) -> None:
    async for client in _client(prompt, short_term, tools):
        client._task_profiles = {
            "main": SimpleNamespace(
                base_url="http://main",
                api_key="sk-main",
                model="main-model",
                api_format="anthropic",
            ),
            "slang": SimpleNamespace(
                base_url="http://slang",
                api_key="sk-slang",
                model="slang-model",
                api_format="openai",
            ),
        }
        client.set_task_profile_names({"main": "main", "slang": "slang"})

        with patch("services.llm.client.call_api", new_callable=AsyncMock) as mock_api:
            mock_api.side_effect = RateLimitError("HTTP 429")
            slang_request = LLMRequest(
                task="slang",
                static_blocks=["sys"],
                user_messages=[{"role": "user", "content": "x"}],
            )
            with pytest.raises(RateLimitError):
                await client._call(slang_request)
            assert mock_api.await_count == 1

            # The limited slang profile fails fast during cooldown and does not
            # make another HTTP call.
            with pytest.raises(RateLimitError):
                await client._call(slang_request)
            assert mock_api.await_count == 1

            # Main profile remains callable because cooldown is keyed by profile.
            mock_api.side_effect = None
            mock_api.return_value = MOCK_RESULT_FULL
            result = await client._call([], [])
            assert result["text"] == "reply text"
            assert mock_api.await_count == 2

        payload = client.provider_rate_limit_payload()
        assert payload["profiles"]["slang"]["status"] == "cooldown"
        assert payload["profiles"]["slang"]["rate_limited"] == 1
        assert payload["profiles"]["slang"]["blocked_calls"] == 1
        assert payload["profiles"]["main"]["successes"] == 1


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


async def test_group_profile_injects_prompt_and_filters_tools(prompt, short_term, timeline, card_store) -> None:
    tools = ToolRegistry()
    tools.register(_StaticTool("send_sticker"))
    tools.register(_StaticTool("slang_lookup"))
    tools.register(_StaticTool("lookup_cards"))
    group_config = GroupConfig(
        overrides={
            12345: GroupOverride(
                reply_style="gentle",
                custom_prompt="少说教，优先接住群内气氛。",
                allowed_tools=["lookup_cards", "send_sticker"],
                blocked_tools=["lookup_cards"],
                sticker_mode="off",
                slang_enabled=False,
            ),
        },
    )

    async for client in _client(
        prompt,
        short_term,
        tools,
        timeline=timeline,
        card_store=card_store,
        group_config=group_config,
    ):
        captured: dict[str, object] = {}

        async def _fake_call_api(*args, _captured=captured, **kwargs):
            _captured["system_blocks"] = args[4]
            _captured["tools"] = kwargs.get("tools")
            return MOCK_RESULT_FULL

        with patch("services.llm.client.call_api", new_callable=AsyncMock, side_effect=_fake_call_api):
            result = await client.chat(
                session_id="group_12345",
                user_id="111",
                user_content="hello",
                identity=_IDENTITY,
                group_id="12345",
            )

        assert result == "reply text"
        system_text = "\n".join(
            str(block.get("text", ""))
            for block in captured["system_blocks"]  # type: ignore[index]
            if isinstance(block, dict)
        )
        assert "群聊回复偏好" in system_text
        assert "少说教，优先接住群内气氛。" in system_text

        tool_names = {
            str(tool.get("name", ""))
            for tool in (captured["tools"] or [])  # type: ignore[operator]
            if isinstance(tool, dict)
        }
        assert "lookup_cards" not in tool_names
        assert "send_sticker" not in tool_names
        assert "slang_lookup" not in tool_names
        assert "pass_turn" in tool_names


async def test_deepseek_main_moves_dynamic_prompt_blocks_to_tail_metadata(
    prompt,
    short_term,
    timeline,
    card_store,
) -> None:
    tools = ToolRegistry()

    class _Bus:
        async def fire_on_pre_prompt(self, prompt_ctx) -> None:
            prompt_ctx.add_block("稳定规则", label="稳定块", position="stable")
            prompt_ctx.add_block("本轮动态信息", label="动态块", position="dynamic")

        async def fire_on_post_reply(self, reply_ctx) -> None:
            return None

    async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
        client._bus = _Bus()
        client._task_profiles = {
            "main": SimpleNamespace(
                base_url="https://api.deepseek.com",
                api_key="sk-deepseek-main",
                model="deepseek-v4-flash",
                api_format="deepseek",
            ),
        }
        client.set_task_profile_names({"main": "main"})

        captured: dict[str, object] = {}

        async def _fake_call_api(*args, _captured=captured, **kwargs):
            _captured["system_blocks"] = args[4]
            _captured["messages"] = args[5]
            _captured["request_options"] = kwargs.get("request_options")
            return MOCK_RESULT_FULL | {
                "provider_kind": "deepseek",
                "provider_mode": "native",
                "prompt_cache_hit_tokens": 50,
                "prompt_cache_miss_tokens": 100,
                "reasoning_replay_tokens": 0,
                "payload_sanitized": False,
            }

        with patch("services.llm.client.call_api", new_callable=AsyncMock, side_effect=_fake_call_api):
            await client.chat(
                session_id="group_12345",
                user_id="111",
                user_content="hello",
                identity=_IDENTITY,
                group_id="12345",
            )

        system_text = "\n".join(
            str(block.get("text", ""))
            for block in captured["system_blocks"]  # type: ignore[index]
            if isinstance(block, dict)
        )
        assert "稳定块" in system_text
        assert "动态块" not in system_text

        messages = captured["messages"]  # type: ignore[assignment]
        last_user = next(msg for msg in reversed(messages) if msg["role"] == "user")
        assert "<turn_meta>" in str(last_user["content"])
        assert "动态块" in str(last_user["content"])

        request_options = captured["request_options"]  # type: ignore[assignment]
        assert isinstance(request_options, dict)
        assert str(request_options.get("user_id", "")).startswith("grp_")


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

    async def test_textual_pass_turn_is_suppressed_in_group(
        self, prompt, short_term, tools, timeline, card_store,
    ) -> None:
        async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
            bus = _Bus()
            client._bus = bus

            with patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                return_value={
                    "text": "[pass_turn] 不相关",
                    "tool_uses": [],
                    "input_tokens": 100,
                    "output_tokens": 1,
                    "cache_read": 0,
                    "cache_create": 0,
                },
            ):
                result = await client.chat(
                    session_id="group_12345",
                    user_id="111",
                    user_content="hello",
                    identity=_IDENTITY,
                    group_id="12345",
                    ctx=None,
                )

            assert result is None
            assert len(bus.post_reply_calls) == 0
            assert len(timeline.get_turns("12345")) == 0

    async def test_empty_reply_falls_back_in_private_chat(self, prompt, short_term, tools) -> None:
        async for client in _client(prompt, short_term, tools):
            bus = _Bus()
            client._bus = bus

            with patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                return_value={
                    "text": "pass_turn",
                    "tool_uses": [],
                    "input_tokens": 100,
                    "output_tokens": 1,
                    "cache_read": 0,
                    "cache_create": 0,
                },
            ):
                result = await client.chat(
                    session_id="private_100",
                    user_id="100",
                    user_content="hello",
                    identity=_IDENTITY,
                )

            assert _normalize_reply(result) == _normalize_reply("我先缓一下，马上接你。")
            assert len(bus.post_reply_calls) == 1
            assert _normalize_reply(bus.post_reply_calls[0].reply_content) == _normalize_reply(
                "我先缓一下，马上接你。",
            )

    async def test_pass_turn_tool_omitted_when_force_reply(
        self, prompt, short_term, tools, timeline, card_store,
    ) -> None:
        """force_reply=True must strip pass_turn from tool_defs so the LLM cannot pick it."""
        async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
            captured: dict[str, Any] = {}

            async def fake_call(*args: Any, _captured: dict[str, Any] = captured, **kwargs: Any) -> dict[str, Any]:
                _captured["tools"] = kwargs.get("tools")
                return {
                    "text": "ok",
                    "tool_uses": [],
                    "input_tokens": 10, "output_tokens": 1,
                    "cache_read": 0, "cache_create": 0,
                }

            gid = "12345"
            timeline.add(gid, role="user", content="@我", speaker="user(111)")

            with patch("services.llm.client.call_api", side_effect=fake_call):
                await client.chat(
                    session_id=f"group_{gid}",
                    user_id="111",
                    user_content="",
                    identity=_IDENTITY,
                    group_id=gid,
                    ctx=None,
                    force_reply=True,
                )

            tool_names = {t.get("name") for t in captured["tools"] or [] if isinstance(t, dict)}
            assert "pass_turn" not in tool_names

    async def test_pass_turn_overridden_when_force_reply(
        self, prompt, short_term, tools, timeline, card_store,
    ) -> None:
        """If LLM still calls pass_turn under force_reply (cached toolset), override and emit a reply."""
        async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
            gid = "12345"
            timeline.add(gid, role="user", content="@我", speaker="user(111)")

            mock_result = {
                "text": "",
                "tool_uses": [ToolUse(id="tu_1", name="pass_turn", input={"reason": "群里在聊别的"})],
                "input_tokens": 100, "output_tokens": 0,
                "cache_read": 0, "cache_create": 0,
            }
            with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=mock_result):
                result = await client.chat(
                    session_id=f"group_{gid}",
                    user_id="111",
                    user_content="",
                    identity=_IDENTITY,
                    group_id=gid,
                    ctx=None,
                    force_reply=True,
                )

            assert result is not None and result.strip() != ""

    async def test_low_confidence_pass_turn_light_ack_when_gate_enabled(
        self, prompt, short_term, tools, timeline, card_store,
    ) -> None:
        async for client in _client(prompt, short_term, tools, timeline=timeline, card_store=card_store):
            client._pass_turn_confidence_gate = True
            client._pass_turn_confidence_threshold = 0.4
            gid = "12345"
            timeline.add(gid, role="user", content="还要不要接？", speaker="user(111)")

            mock_result = {
                "text": "",
                "tool_uses": [ToolUse(
                    id="tu_1",
                    name="pass_turn",
                    input={"reason": "不确定是否该接", "confidence": 0.2},
                )],
                "input_tokens": 100,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_create": 0,
            }
            with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=mock_result):
                result = await client.chat(
                    session_id=f"group_{gid}",
                    user_id="111",
                    user_content="",
                    identity=_IDENTITY,
                    group_id=gid,
                    ctx=None,
                )

            assert _normalize_reply(result) == _normalize_reply("嗯，我在。")

    async def test_tool_calls_are_exposed_to_post_reply(self, prompt, short_term, tools) -> None:
        async for client in _client(prompt, short_term, tools):
            bus = _Bus()
            client._bus = bus
            tools.register(_StaticTool("lookup_cards"))

            with patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                side_effect=[
                    {
                        "text": "",
                        "tool_uses": [ToolUse(id="tu_1", name="lookup_cards", input={"query": "猫"})],
                        "input_tokens": 100,
                        "output_tokens": 5,
                        "cache_read": 0,
                        "cache_create": 0,
                    },
                    {
                        "text": "找到啦",
                        "tool_uses": [],
                        "input_tokens": 80,
                        "output_tokens": 20,
                        "cache_read": 0,
                        "cache_create": 0,
                    },
                ],
            ):
                result = await client.chat(
                    session_id="private_100",
                    user_id="100",
                    user_content="查一下",
                    identity=_IDENTITY,
                )

            assert result == "找到啦"
            assert len(bus.post_reply_calls) == 1
            assert bus.post_reply_calls[0].tool_calls
            assert bus.post_reply_calls[0].tool_calls[0]["name"] == "lookup_cards"

    async def test_thinker_retrieve_mode_propagates_to_hook(self, prompt, short_term, tools) -> None:
        """PR5: thinker decision retrieve_mode reaches ThinkerContext via the bus hook."""
        async for client in _client(prompt, short_term, tools):
            client._thinker_enabled = True
            bus = _Bus()
            client._bus = bus

            with (
                patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
                patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=MOCK_RESULT_FULL),
            ):
                mock_think.return_value = SimpleNamespace(
                    action="reply",
                    retrieve_mode="doc",
                    thought="查文档",
                    sticker=False,
                    tone="认真",
                    usage={"input_tokens": 10, "cache_read": 0, "cache_create": 0, "output_tokens": 2},
                )
                result = await client.chat(
                    session_id="private_100",
                    user_id="100",
                    user_content="omubot 怎么部署",
                    identity=_IDENTITY,
                )

            assert result == "reply text"
            assert len(bus.thinker_calls) == 1
            assert bus.thinker_calls[0].action == "reply"
            assert bus.thinker_calls[0].retrieve_mode == "doc"


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


# ---------------------------------------------------------------------------
# Stage B — LLMRequest spine path on _call
# ---------------------------------------------------------------------------


_SPINE_RESULT = {
    "text": "spine reply",
    "tool_uses": [],
    "input_tokens": 200,
    "output_tokens": 80,
    "cache_read": 120,
    "cache_create": 0,
    "prompt_cache_hit_tokens": 120,
    "prompt_cache_miss_tokens": 80,
}


def _spine_profile(*caps: str) -> SimpleNamespace:
    """Build a fake task_profile with the given capability set."""
    return SimpleNamespace(
        base_url="http://spine",
        api_key="sk-spine",
        model="spine-model",
        api_format="deepseek",
        capabilities=list(caps),
    )


async def test_spine_call_dispatches_llm_request(prompt, short_term, tools) -> None:
    """LLMRequest path threads task / system_blocks / messages through call_api."""
    async for client in _client(prompt, short_term, tools):
        client._task_profiles = {
            "main": _spine_profile("chat", "tools"),
            "thinker": _spine_profile("chat"),
        }
        client.set_task_profile_names({"main": "main", "thinker": "thinker"})

        req = LLMRequest(
            task="thinker",
            user_id="42",
            group_id="g1",
            static_blocks=["identity-prompt"],
            dynamic_blocks=["mood: happy"],
            user_messages=[{"role": "user", "content": "hi"}],
            max_tokens=64,
        )

        with patch(
            "services.llm.client.call_api", new_callable=AsyncMock, return_value=_SPINE_RESULT
        ) as mock_api:
            result = await client._call(req)

        assert result["text"] == "spine reply"
        assert mock_api.await_count == 1
        # call_api receives the segmented system blocks in static→stable→dynamic order
        kwargs_or_args = mock_api.await_args
        passed_system = kwargs_or_args.args[4]
        assert [b["text"] for b in passed_system] == ["identity-prompt", "mood: happy"]
        # task_profile resolution honored "thinker"
        passed_model = kwargs_or_args.args[3]
        assert passed_model == "spine-model"


async def test_spine_call_enforces_capabilities(prompt, short_term, tools) -> None:
    """Profile missing a required capability triggers fail-fast ValueError."""
    async for client in _client(prompt, short_term, tools):
        client._task_profiles = {"main": _spine_profile("chat")}
        client.set_task_profile_names({"main": "main"})

        req = LLMRequest(
            task="main",
            static_blocks=["sys"],
            user_messages=[{"role": "user", "content": "hi"}],
            requires_capabilities=("vision",),
        )

        with patch(
            "services.llm.client.call_api", new_callable=AsyncMock, return_value=_SPINE_RESULT
        ) as mock_api:
            with pytest.raises(ValueError, match="missing capability"):
                await client._call(req)
            # capability check happens before any HTTP attempt
            assert mock_api.await_count == 0


async def test_spine_capability_check_uses_resolved_task_profile(prompt, short_term, tools) -> None:
    """Task-profile mappings may point at a custom profile name; enforcement must use the resolved task entry."""
    async for client in _client(prompt, short_term, tools):
        client._task_profiles = {
            "main": _spine_profile("chat"),
            "scheduler_eot": _spine_profile("chat", "json"),
        }
        client.set_task_profile_names({"main": "main", "scheduler_eot": "haiku"})

        req = LLMRequest(
            task="scheduler_eot",
            static_blocks=["sys"],
            user_messages=[{"role": "user", "content": "hi"}],
            requires_capabilities=("chat", "json"),
        )

        with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_SPINE_RESULT) as mock_api:
            await client._call(req)

        assert mock_api.await_count == 1


async def test_spine_call_records_usage_with_task(prompt, short_term, tools, tmp_path) -> None:
    """LLMRequest path auto-records usage labelled with req.task."""
    tracker = UsageTracker(db_path=str(tmp_path / "spine_usage.db"))
    await tracker.init()
    try:
        async for client in _client(prompt, short_term, tools):
            client._usage_tracker = tracker
            client._task_profiles = {"main": _spine_profile("chat", "tools")}
            client.set_task_profile_names({"main": "main"})

            req = LLMRequest(
                task="bilibili_intent",
                user_id="42",
                static_blocks=["intent-prompt"],
                user_messages=[{"role": "user", "content": "?"}],
                max_tokens=128,
            )

            with patch(
                "services.llm.client.call_api", new_callable=AsyncMock, return_value=_SPINE_RESULT
            ):
                await client._call(req)
            await asyncio.sleep(0)

        rows = await tracker.query_raw(
            "SELECT * FROM llm_calls WHERE call_type = 'bilibili_intent'"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["user_id"] == "42"
        assert row["output_tokens"] == 80
        assert row["cache_read_tokens"] == 120
        assert row["prompt_cache_hit_tokens"] == 120
        assert row["prompt_cache_miss_tokens"] == 80
    finally:
        await tracker.close()


async def test_spine_call_per_task_cache_hit_pct(prompt, short_term, tools) -> None:
    """ProfileRateLimitState tracks per-task cache hit pct independently."""
    async for client in _client(prompt, short_term, tools):
        client._task_profiles = {"main": _spine_profile("chat", "tools")}
        client.set_task_profile_names({"main": "main", "thinker": "main"})

        result_high = {**_SPINE_RESULT, "prompt_cache_hit_tokens": 180,
                       "prompt_cache_miss_tokens": 20}
        result_low = {**_SPINE_RESULT, "prompt_cache_hit_tokens": 10,
                      "prompt_cache_miss_tokens": 90}

        async def fake_api(*args, **kwargs):
            # Flip behaviour based on which task drove the call.
            return fake_api._result

        fake_api._result = result_high
        with patch("services.llm.client.call_api", new=fake_api):
            await client._call(LLMRequest(task="main", static_blocks=["s"],
                                          user_messages=[{"role": "user", "content": "x"}]))
            fake_api._result = result_low
            await client._call(LLMRequest(task="thinker", static_blocks=["s"],
                                          user_messages=[{"role": "user", "content": "x"}]))

        state = client._profile_rate_state("main")
        per_task = state.last_cache_hit_pct_by_task
        assert pytest.approx(per_task["main"], abs=0.5) == 90.0
        assert pytest.approx(per_task["thinker"], abs=0.5) == 10.0


async def test_spine_call_records_cache_diagnostic(prompt, short_term, tools) -> None:
    """Per-axis cache diagnostic + diff is captured for spine-path calls."""
    async for client in _client(prompt, short_term, tools):
        client._task_profiles = {"main": _spine_profile("chat", "tools")}
        client.set_task_profile_names({"main": "main"})

        with patch(
            "services.llm.client.call_api", new_callable=AsyncMock, return_value=_SPINE_RESULT
        ):
            req1 = LLMRequest(
                task="main",
                static_blocks=["identity"],
                dynamic_blocks=["mood: happy"],
                user_messages=[{"role": "user", "content": "hi"}],
            )
            await client._call(req1)
            req2 = LLMRequest(
                task="main",
                static_blocks=["identity"],
                dynamic_blocks=["mood: sad"],  # only dynamic block changed
                user_messages=[{"role": "user", "content": "hi"}],
            )
            await client._call(req2)

        history = client.cache_diagnostic_history("main", limit=10)
        assert len(history) == 2
        first_snapshot, first_diff = history[0]
        second_snapshot, second_diff = history[1]
        assert first_diff is None  # first ever call has no prior snapshot
        assert second_diff is not None
        assert second_diff.system_changed is True
        assert second_diff.tools_changed is False
        assert second_diff.changed_block_indices == [1]  # the dynamic block
        # static block still hashes identical
        assert first_snapshot.per_block_hashes[0] == second_snapshot.per_block_hashes[0]


async def test_spine_call_cancel_path_no_partial_record(
    prompt, short_term, tools, tmp_path
) -> None:
    """D2: when _call is cancelled mid-flight, no usage row + no diagnostic entry."""
    tracker = UsageTracker(db_path=str(tmp_path / "cancel_usage.db"))
    await tracker.init()
    try:
        async for client in _client(prompt, short_term, tools):
            client._usage_tracker = tracker
            client._task_profiles = {"main": _spine_profile("chat", "tools")}
            client.set_task_profile_names({"main": "main"})

            async def hang(*args, **kwargs):
                await asyncio.sleep(10)
                return _SPINE_RESULT

            req = LLMRequest(
                task="main",
                static_blocks=["s"],
                user_messages=[{"role": "user", "content": "x"}],
            )

            with patch("services.llm.client.call_api", new=hang), pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(client._call(req), timeout=0.05)
            await asyncio.sleep(0)

        rows = await tracker.query_raw("SELECT * FROM llm_calls")
        assert rows == []
        assert client.cache_diagnostic_history("main") == []
    finally:
        await tracker.close()


async def test_spine_call_legacy_signature_still_works(prompt, short_term, tools) -> None:
    """Legacy 6-positional _call(...) continues to dispatch unchanged."""
    async for client in _client(prompt, short_term, tools):
        client._task_profiles = {"main": _spine_profile("chat")}
        client.set_task_profile_names({"main": "main"})

        with patch(
            "services.llm.client.call_api", new_callable=AsyncMock, return_value=_SPINE_RESULT
        ) as mock_api:
            result = await client._call(
                [{"type": "text", "text": "legacy-system"}],
                [{"role": "user", "content": "hi"}],
                tools=None,
                max_tokens=32,
                task="main",
            )

        assert result["text"] == "spine reply"
        assert mock_api.await_count == 1
        # legacy path does NOT auto-record usage (callers do it themselves).
        # Diagnostic ring stays empty for this path too.
        assert client.cache_diagnostic_history("main") == []


async def test_compact_aggregated_usage_with_per_round_diagnostic(
    prompt, short_term, tools, tmp_path
) -> None:
    """DL.5: compact runs multiple rounds. The aggregated ``len(rows)==1``
    contract from ``test_compact_records_usage`` must hold (caller writes
    one row at the end with ``auto_record_usage=False``), AND each round
    must show up in ``cache_diagnostic_history('compact')`` so admins can
    see which axis broke between rounds.
    """
    tracker = UsageTracker(db_path=str(tmp_path / "compact_aggregated.db"))
    await tracker.init()
    try:
        async for client in _client(prompt, short_term, tools):
            client._usage_tracker = tracker
            _fill_messages(short_term, "private_100")
            mock_result = {**MOCK_RESULT, "output_tokens": 80, "cache_read": 0, "cache_create": 0}
            with patch(
                "services.llm.client.call_api", new_callable=AsyncMock, return_value=mock_result
            ):
                await client._compact("private_100")
            await asyncio.sleep(0)

            # Aggregated contract: one row written by the caller, not per round.
            rows = await tracker.query_raw("SELECT * FROM llm_calls WHERE call_type='compact'")
            assert len(rows) == 1, f"compact must write exactly one usage row, got {len(rows)}"

            # Diagnostic still records every round so admins can see prefix breaks.
            history = client.cache_diagnostic_history("compact")
            assert len(history) >= 1, "compact must record at least one diagnostic snapshot"
    finally:
        await tracker.close()


async def test_main_chat_aggregated_usage_with_per_round_diagnostic(
    prompt, short_term, tools, tmp_path
) -> None:
    """DL.5: main chat() runs the tool loop. The ``len(rows)==1`` contract
    from ``test_chat_records_usage`` must hold (caller aggregates), AND
    cache_diagnostic_history('main') sees each round individually.
    """
    tracker = UsageTracker(db_path=str(tmp_path / "main_aggregated.db"))
    await tracker.init()
    mock_result = {**MOCK_RESULT, "output_tokens": 200}
    try:
        async for client in _client(prompt, short_term, tools):
            client._usage_tracker = tracker
            with patch(
                "services.llm.client.call_api", new_callable=AsyncMock, return_value=mock_result
            ):
                await client.chat(
                    session_id="private_100", user_id="100",
                    user_content="hello", identity=_IDENTITY,
                )
            await asyncio.sleep(0)

            rows = await tracker.query_raw("SELECT * FROM llm_calls WHERE call_type='chat'")
            assert len(rows) == 1, f"chat must write exactly one usage row, got {len(rows)}"

            history = client.cache_diagnostic_history("main")
            assert len(history) >= 1, "main chat must record at least one diagnostic snapshot"
    finally:
        await tracker.close()


# ---------------------------------------------------------------------------
# Spine cache_control injection (apply_cache_breakpoints) — integration:
# verify _dispatch_call stamps cache_control on the system blocks handed
# to call_api, even for paths that previously bypassed prompt_builder.
# ---------------------------------------------------------------------------


async def test_dispatch_stamps_cache_control_on_plugin_direct_path(
    prompt: PromptBuilder, short_term: ShortTermMemory, tools: ToolRegistry,
) -> None:
    """Plugin-direct LLMRequest (e.g. ``memo``) must reach call_api with
    a cache_control marker on its tail system block — even though the
    caller passed only bare strings into ``static_blocks``.
    """
    captured: dict[str, list[dict]] = {}

    async def _capture(
        session, base_url, api_key, model,
        system_blocks, messages,
        *, max_tokens, tools=None, thinking=None, api_format=None,
        request_options=None,
    ):
        captured["system_blocks"] = list(system_blocks)
        return MOCK_RESULT

    async for client in _client(prompt, short_term, tools):
        with patch("services.llm.client.call_api", new=_capture):
            req = LLMRequest(
                task="memo",
                static_blocks=["plugin-system-prompt"],
                user_messages=[{"role": "user", "content": "x"}],
            )
            await client._call(req)

        blocks = captured["system_blocks"]
        assert blocks, "spine must hand at least one system block to call_api"
        cache_count = sum(1 for b in blocks if b.get("cache_control"))
        assert cache_count == 1, (
            f"memo (default profile) should land exactly 1 system "
            f"breakpoint, got {cache_count}"
        )
        assert blocks[0].get("cache_control") == {"type": "ephemeral"}


async def test_dispatch_strips_caller_cache_control_then_re_stamps(
    prompt: PromptBuilder, short_term: ShortTermMemory, tools: ToolRegistry,
) -> None:
    """Even when the caller pre-stamps ``cache_control`` on a dict block,
    spine strips it (via ``_normalize_block``) and re-applies according
    to the per-task profile. Single source of truth = spine."""
    captured: dict[str, list[dict]] = {}

    async def _capture(
        session, base_url, api_key, model,
        system_blocks, messages,
        *, max_tokens, tools=None, thinking=None, api_format=None,
        request_options=None,
    ):
        captured["system_blocks"] = list(system_blocks)
        return MOCK_RESULT

    async for client in _client(prompt, short_term, tools):
        with patch("services.llm.client.call_api", new=_capture):
            req = LLMRequest(
                task="slang",
                static_blocks=[
                    {"type": "text", "text": "shared-prefix", "cache_control": {"type": "ephemeral"}},
                ],
                stable_blocks=[
                    {"type": "text", "text": "task-prompt", "cache_control": {"type": "ephemeral"}},
                ],
                user_messages=[{"role": "user", "content": "x"}],
            )
            await client._call(req)

        blocks = captured["system_blocks"]
        cache_count = sum(1 for b in blocks if b.get("cache_control"))
        # slang profile = 2 system breakpoints. Caller pre-stamped
        # cache_control on both; spine strips then re-applies per profile.
        # Two segments (static + stable) → 2 markers on segment tails.
        assert cache_count == 2
        assert blocks[0].get("cache_control") == {"type": "ephemeral"}
        assert blocks[1].get("cache_control") == {"type": "ephemeral"}
