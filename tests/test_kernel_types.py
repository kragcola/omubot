"""Tests for omubot.kernel.types — Context types, Plugin base, Tool ABC."""

from __future__ import annotations

import pytest

from kernel.types import (
    AmadeusPlugin,
    Identity,
    MessageContext,
    PluginContext,
    PromptBlock,
    PromptContext,
    ReplyContext,
    ThinkerContext,
    Tool,
    ToolContext,
)

# ============================================================================
# PluginContext
# ============================================================================

class TestPluginContext:
    def test_default_fields(self) -> None:
        ctx = PluginContext()
        assert ctx.config is None
        assert ctx.card_store is None
        assert ctx.llm_client is None
        assert ctx.storage_dir is not None  # Path default

    def test_set_storage_dir(self) -> None:
        from pathlib import Path
        ctx = PluginContext(storage_dir=Path("/tmp/omubot"))
        assert ctx.storage_dir == Path("/tmp/omubot")


# ============================================================================
# MessageContext
# ============================================================================

class TestMessageContext:
    def test_group_message(self) -> None:
        ctx = MessageContext(
            session_id="group_123",
            group_id="123",
            user_id="999",
            content="hello",
            raw_message={"message": [{"type": "text", "data": {"text": "hello"}}]},
        )
        assert ctx.is_group is True
        assert ctx.is_private is False
        assert ctx.is_at is False

    def test_private_message(self) -> None:
        ctx = MessageContext(
            session_id="private_456",
            group_id=None,
            user_id="999",
            content="hi",
            raw_message={"message": []},
            is_private=True,
        )
        assert ctx.is_group is False
        assert ctx.is_private is True

    def test_at_message(self) -> None:
        ctx = MessageContext(
            session_id="group_123",
            group_id="123",
            user_id="999",
            content="hello",
            raw_message={},
            is_at=True,
        )
        assert ctx.is_at is True

    def test_message_id(self) -> None:
        ctx = MessageContext(
            session_id="group_123",
            group_id="123",
            user_id="999",
            content="x",
            raw_message={},
            message_id=42,
        )
        assert ctx.message_id == 42


# ============================================================================
# PromptContext
# ============================================================================

class TestPromptContext:
    def test_add_single_block(self) -> None:
        ctx = PromptContext(
            session_id="s1",
            group_id="g1",
            user_id="u1",
            identity=Identity(name="test"),
        )
        ctx.add_block("今日好感度: 25", label="affection", position="dynamic")
        assert len(ctx.blocks) == 1
        assert ctx.blocks[0].text == "今日好感度: 25"
        assert ctx.blocks[0].label == "affection"
        assert ctx.blocks[0].position == "dynamic"

    def test_add_multiple_blocks_preserves_order(self) -> None:
        ctx = PromptContext(
            session_id="s1",
            group_id=None,
            user_id="u1",
            identity=Identity(name="test"),
        )
        ctx.add_block("A", label="first")
        ctx.add_block("B", label="second")
        ctx.add_block("C", label="third")
        assert [b.text for b in ctx.blocks] == ["A", "B", "C"]

    def test_default_position_is_dynamic(self) -> None:
        ctx = PromptContext(
            session_id="s1",
            group_id=None,
            user_id="u1",
            identity=Identity(name="test"),
        )
        ctx.add_block("test")
        assert ctx.blocks[0].position == "dynamic"

    def test_blocks_empty_by_default(self) -> None:
        ctx = PromptContext(
            session_id="s1",
            group_id=None,
            user_id="u1",
            identity=Identity(name="test"),
        )
        assert ctx.blocks == []

    def test_privacy_mask_default(self) -> None:
        ctx = PromptContext(
            session_id="s1",
            group_id=None,
            user_id="u1",
            identity=Identity(name="test"),
        )
        assert ctx.privacy_mask is True

    def test_conversation_text(self) -> None:
        ctx = PromptContext(
            session_id="s1",
            group_id="g1",
            user_id="u1",
            identity=Identity(name="test"),
            conversation_text="最近在聊音游",
        )
        assert ctx.conversation_text == "最近在聊音游"


# ============================================================================
# ReplyContext
# ============================================================================

class TestReplyContext:
    def test_basic_reply(self) -> None:
        ctx = ReplyContext(
            session_id="s1",
            group_id="g1",
            user_id="u1",
            reply_content="你好呀~",
        )
        assert ctx.reply_content == "你好呀~"
        assert ctx.tool_calls == []
        assert ctx.elapsed_ms == 0.0

    def test_with_tool_calls(self) -> None:
        ctx = ReplyContext(
            session_id="s1",
            group_id="g1",
            user_id="u1",
            reply_content="ok",
            tool_calls=[{"name": "search", "result": "found"}],
            elapsed_ms=1500.0,
            thinker_action="search",
            thinker_thought="需要查一下资料",
        )
        assert len(ctx.tool_calls) == 1
        assert ctx.elapsed_ms == 1500.0
        assert ctx.thinker_action == "search"
        assert ctx.thinker_thought == "需要查一下资料"


# ============================================================================
# ThinkerContext
# ============================================================================

class TestThinkerContext:
    def test_reply_decision(self) -> None:
        ctx = ThinkerContext(
            session_id="s1",
            group_id="g1",
            user_id="u1",
            action="reply",
            thought="该回复了",
            elapsed_ms=500.0,
        )
        assert ctx.action == "reply"
        assert ctx.thought == "该回复了"
        assert ctx.elapsed_ms == 500.0

    def test_wait_decision(self) -> None:
        ctx = ThinkerContext(
            session_id="s1",
            group_id="g1",
            user_id="u1",
            action="wait",
            thought="太困了不回了",
        )
        assert ctx.action == "wait"


# ============================================================================
# PromptBlock
# ============================================================================

class TestPromptBlock:
    def test_default_position(self) -> None:
        block = PromptBlock(text="test")
        assert block.position == "dynamic"
        assert block.label == ""

    def test_static_block(self) -> None:
        block = PromptBlock(text="always here", label="identity", position="static")
        assert block.position == "static"

    def test_stable_block(self) -> None:
        block = PromptBlock(text="global index", label="index", position="stable")
        assert block.position == "stable"


# ============================================================================
# Tool ABC
# ============================================================================

class TestTool:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            Tool()  # type: ignore[abstract]

    def test_concrete_tool_can_instantiate(self) -> None:
        class EchoTool(Tool):
            @property
            def name(self) -> str:
                return "echo"

            @property
            def description(self) -> str:
                return "Echoes input"

            @property
            def parameters(self) -> dict:
                return {"type": "object", "properties": {}}

            async def execute(self, ctx: ToolContext, **kwargs: str) -> str:
                return "echo: " + str(kwargs)

        tool = EchoTool()
        assert tool.name == "echo"
        assert tool.description == "Echoes input"

    def test_to_openai_tool_format(self) -> None:
        class MyTool(Tool):
            @property
            def name(self) -> str:
                return "test"

            @property
            def description(self) -> str:
                return "A test tool"

            @property
            def parameters(self) -> dict:
                return {"type": "object", "properties": {"x": {"type": "string"}}}

            async def execute(self, ctx: ToolContext, **kwargs: str) -> str:
                return "ok"

        tool = MyTool()
        openai_repr = tool.to_openai_tool()
        assert openai_repr["type"] == "function"
        assert openai_repr["function"]["name"] == "test"
        assert openai_repr["function"]["description"] == "A test tool"


# ============================================================================
# ToolContext
# ============================================================================

class TestToolContext:
    def test_defaults(self) -> None:
        ctx = ToolContext()
        assert ctx.bot is None
        assert ctx.user_id == ""
        assert ctx.group_id is None
        assert ctx.session_id == ""
        assert ctx.extra == {}

    def test_group_context(self) -> None:
        ctx = ToolContext(
            user_id="999",
            group_id="123",
            session_id="group_123",
        )
        assert ctx.user_id == "999"
        assert ctx.group_id == "123"

    def test_extra_dict(self) -> None:
        ctx = ToolContext(extra={"key": "value"})
        assert ctx.extra["key"] == "value"


# ============================================================================
# AmadeusPlugin
# ============================================================================

class TestAmadeusPlugin:
    def test_default_properties(self) -> None:
        plugin = AmadeusPlugin()
        assert plugin.name == ""
        assert plugin.description == ""
        assert plugin.version == "0.1.0"
        assert plugin.priority == 100

    def test_on_message_returns_false_by_default(self) -> None:
        plugin = AmadeusPlugin()
        import asyncio

        async def run():
            ctx = MessageContext(
                session_id="s1",
                group_id="g1",
                user_id="u1",
                content="hello",
                raw_message={},
            )
            return await plugin.on_message(ctx)

        result = asyncio.run(run())
        assert result is False

    def test_on_startup_is_noop(self) -> None:
        plugin = AmadeusPlugin()
        import asyncio

        async def run():
            await plugin.on_startup(PluginContext())

        asyncio.run(run())  # no exception

    def test_register_tools_returns_empty(self) -> None:
        plugin = AmadeusPlugin()
        assert plugin.register_tools() == []

    def test_on_shutdown_is_noop(self) -> None:
        plugin = AmadeusPlugin()
        import asyncio

        async def run():
            await plugin.on_shutdown(PluginContext())

        asyncio.run(run())  # no exception

    def test_on_thinker_decision_is_noop(self) -> None:
        plugin = AmadeusPlugin()
        import asyncio

        async def run():
            ctx = ThinkerContext(
                session_id="s1",
                group_id="g1",
                user_id="u1",
                action="reply",
                thought="hello",
            )
            await plugin.on_thinker_decision(ctx)

        asyncio.run(run())  # no exception

    def test_on_pre_prompt_is_noop(self) -> None:
        plugin = AmadeusPlugin()
        import asyncio

        async def run():
            ctx = PromptContext(
                session_id="s1",
                group_id=None,
                user_id="u1",
                identity=Identity(name="test"),
            )
            await plugin.on_pre_prompt(ctx)

        asyncio.run(run())  # no exception

    def test_on_post_reply_is_noop(self) -> None:
        plugin = AmadeusPlugin()
        import asyncio

        async def run():
            ctx = ReplyContext(
                session_id="s1",
                group_id="g1",
                user_id="u1",
                reply_content="hi",
            )
            await plugin.on_post_reply(ctx)

        asyncio.run(run())  # no exception

    def test_on_tick_is_noop(self) -> None:
        plugin = AmadeusPlugin()
        import asyncio

        async def run():
            await plugin.on_tick(PluginContext())

        asyncio.run(run())  # no exception


# ============================================================================
# Identity
# ============================================================================

class TestIdentity:
    def test_defaults(self) -> None:
        ident = Identity()
        assert ident.name == ""
        assert ident.personality == ""

    def test_with_name(self) -> None:
        ident = Identity(name="TestBot", personality="乐于助人")
        assert ident.name == "TestBot"
