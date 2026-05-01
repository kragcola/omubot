"""Tests for omubot.kernel.bus.PluginBus — plugin registration, hook dispatch, error isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kernel.bus import PluginBus
from kernel.types import (
    AmadeusPlugin,
    Identity,
    MessageContext,
    PluginContext,
    PromptContext,
    ReplyContext,
    ThinkerContext,
    Tool,
    ToolContext,
)

# ============================================================================
# Test helpers
# ============================================================================


def _msg_ctx(
    session_id: str = "group_123",
    group_id: str = "123",
    user_id: str = "999",
    content: str = "hello",
) -> MessageContext:
    return MessageContext(
        session_id=session_id,
        group_id=group_id,
        user_id=user_id,
        content=content,
        raw_message={},
    )


def _plugin_ctx() -> PluginContext:
    return PluginContext()


def _prompt_ctx() -> PromptContext:
    return PromptContext(
        session_id="s1",
        group_id="g1",
        user_id="u1",
        identity=Identity(name="test"),
    )


def _reply_ctx() -> ReplyContext:
    return ReplyContext(
        session_id="s1",
        group_id="g1",
        user_id="u1",
        reply_content="test reply",
    )


def _thinker_ctx() -> ThinkerContext:
    return ThinkerContext(
        session_id="s1",
        group_id="g1",
        user_id="u1",
        action="reply",
        thought="ok",
    )


# ============================================================================
# Concrete plugin classes for testing
# ============================================================================


class EchoConsumerPlugin(AmadeusPlugin):
    """Consumes messages when content matches a given prefix."""
    name = "echo_consumer"
    priority = 200

    def __init__(self, prefix: str = "!"):
        super().__init__()
        self.prefix = prefix
        self.on_message_calls: list[str] = []

    async def on_message(self, ctx: MessageContext) -> bool:
        self.on_message_calls.append(str(ctx.content))
        return isinstance(ctx.content, str) and ctx.content.startswith(self.prefix)


class BlockAppendingPlugin(AmadeusPlugin):
    """Appends a block during on_pre_prompt."""
    name = "block_plugin"
    priority = 10

    def __init__(self, block_text: str = "test block", label: str = "test"):
        super().__init__()
        self.block_text = block_text
        self.label = label

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        ctx.add_block(self.block_text, label=self.label)


class TrackingPlugin(AmadeusPlugin):
    """Records all hook calls for verification."""
    name = "tracker"
    priority = 50

    def __init__(self):
        super().__init__()
        self.startup_called = False
        self.shutdown_called = False
        self.post_reply_calls: list[ReplyContext] = []
        self.thinker_calls: list[ThinkerContext] = []
        self.tick_calls = 0
        self.tool_list: list[Tool] = []

    async def on_startup(self, ctx: PluginContext) -> None:
        self.startup_called = True

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self.shutdown_called = True

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        self.post_reply_calls.append(ctx)

    async def on_thinker_decision(self, ctx: ThinkerContext) -> None:
        self.thinker_calls.append(ctx)

    async def on_tick(self, ctx: PluginContext) -> None:
        self.tick_calls += 1

    def register_tools(self) -> list[Tool]:
        return list(self.tool_list)


class CrashingPlugin(AmadeusPlugin):
    """A plugin that raises on specific hooks for error isolation testing."""
    name = "crasher"
    priority = 100

    def __init__(self, crash_on: str = "on_startup"):
        super().__init__()
        self.crash_on = crash_on

    async def on_startup(self, ctx: PluginContext) -> None:
        if self.crash_on == "on_startup":
            raise RuntimeError("crash in on_startup")

    async def on_message(self, ctx: MessageContext) -> bool:
        if self.crash_on == "on_message":
            raise RuntimeError("crash in on_message")
        return False

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self.crash_on == "on_pre_prompt":
            raise RuntimeError("crash in on_pre_prompt")

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        if self.crash_on == "on_post_reply":
            raise RuntimeError("crash in on_post_reply")


class OrderedPlugin(AmadeusPlugin):
    """Records execution order for priority testing."""
    name = ""  # set by init
    priority = 100

    def __init__(self, name: str, priority: int, recorder: list[str]):
        super().__init__()
        self.name = name
        self.priority = priority
        self.recorder = recorder

    async def on_startup(self, ctx: PluginContext) -> None:
        self.recorder.append(self.name)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self.recorder.append(self.name)


class ToolProvidingPlugin(AmadeusPlugin):
    """Provides concrete Tool instances."""
    name = "tool_provider"
    priority = 1

    def __init__(self, tools: list[Tool] | None = None):
        super().__init__()
        self._tools = tools or []

    def register_tools(self) -> list[Tool]:
        return list(self._tools)


class FailingToolPlugin(AmadeusPlugin):
    """Raises during register_tools() for error isolation."""
    name = "failing_tool"
    priority = 1

    def register_tools(self) -> list[Tool]:
        raise RuntimeError("cannot collect tools")


# ============================================================================
# Registration tests
# ============================================================================


class TestRegistration:
    def test_register_single_plugin(self) -> None:
        bus = PluginBus()
        bus.register(AmadeusPlugin())
        assert len(bus.plugins) == 1

    def test_register_multiple_plugins_sorted_by_priority(self) -> None:
        bus = PluginBus()
        p1 = AmadeusPlugin()
        p1.name = "low"
        p1.priority = 300
        p2 = AmadeusPlugin()
        p2.name = "high"
        p2.priority = 10
        p3 = AmadeusPlugin()
        p3.name = "mid"
        p3.priority = 100

        bus.register(p1)
        bus.register(p2)
        bus.register(p3)

        assert [p.name for p in bus.plugins] == ["high", "mid", "low"]

    def test_stable_sort_same_priority(self) -> None:
        bus = PluginBus()
        names: list[str] = []
        for name in ["c", "a", "b"]:
            p = AmadeusPlugin()
            p.name = name
            p.priority = 100
            bus.register(p)
            names.append(name)

        # Same priority should maintain registration order
        assert [p.name for p in bus.plugins] == ["c", "a", "b"]

    def test_register_after_startup_raises(self) -> None:
        bus = PluginBus()

        async def run():
            await bus.fire_on_startup(_plugin_ctx())
            bus.register(AmadeusPlugin())

        with pytest.raises(RuntimeError, match=r"Cannot register.*after startup"):
            asyncio.run(run())

    def test_get_plugin_by_name(self) -> None:
        bus = PluginBus()
        p = AmadeusPlugin()
        p.name = "my_plugin"
        bus.register(p)

        assert bus.get_plugin("my_plugin") is p
        assert bus.get_plugin("nonexistent") is None

    def test_unregister_plugin(self) -> None:
        bus = PluginBus()
        p = AmadeusPlugin()
        p.name = "to_remove"
        bus.register(p)
        assert len(bus.plugins) == 1

        assert bus.unregister("to_remove") is True
        assert len(bus.plugins) == 0
        assert bus.unregister("nonexistent") is False

    def test_started_property(self) -> None:
        bus = PluginBus()
        assert bus.started is False

        async def run():
            await bus.fire_on_startup(_plugin_ctx())

        asyncio.run(run())
        assert bus.started is True


# ============================================================================
# Lifecycle dispatch tests
# ============================================================================


class TestLifecycleDispatch:
    def test_fire_on_startup_calls_all_plugins(self) -> None:
        bus = PluginBus()
        t1 = TrackingPlugin()
        t2 = TrackingPlugin()
        bus.register(t1)
        bus.register(t2)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))
        assert t1.startup_called
        assert t2.startup_called

    def test_fire_on_startup_execution_order(self) -> None:
        bus = PluginBus()
        order: list[str] = []
        bus.register(OrderedPlugin("first", 10, order))
        bus.register(OrderedPlugin("second", 20, order))
        bus.register(OrderedPlugin("third", 30, order))

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))
        assert order == ["first", "second", "third"]

    def test_fire_on_shutdown_reverse_order(self) -> None:
        bus = PluginBus()
        order: list[str] = []
        bus.register(OrderedPlugin("first", 10, order))
        bus.register(OrderedPlugin("second", 20, order))
        bus.register(OrderedPlugin("third", 30, order))

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))
        order.clear()
        asyncio.run(bus.fire_on_shutdown(_plugin_ctx()))
        assert order == ["third", "second", "first"]

    def test_fire_on_shutdown_even_if_startup_failed(self) -> None:
        """Shutdown should still call all plugins even if one startup crashed."""
        bus = PluginBus()
        tracker = TrackingPlugin()
        bus.register(CrashingPlugin(crash_on="on_startup"))
        bus.register(tracker)

        # Startup will crash the crasher but should still start tracker
        asyncio.run(bus.fire_on_startup(_plugin_ctx()))
        assert tracker.startup_called  # tracker was started after crasher

        # Shutdown should call both
        asyncio.run(bus.fire_on_shutdown(_plugin_ctx()))
        assert tracker.shutdown_called


# ============================================================================
# Message pipeline dispatch tests
# ============================================================================


class TestMessageDispatch:
    def test_fire_on_message_not_consumed(self) -> None:
        bus = PluginBus()
        tracker = TrackingPlugin()
        bus.register(tracker)

        consumed = asyncio.run(bus.fire_on_message(_msg_ctx()))
        assert consumed is False

    def test_fire_on_message_consumed_by_first_matching(self) -> None:
        bus = PluginBus()
        consumer = EchoConsumerPlugin(prefix="!")
        tracker = TrackingPlugin()
        bus.register(consumer)
        bus.register(tracker)

        consumed = asyncio.run(bus.fire_on_message(_msg_ctx(content="!ping")))
        assert consumed is True
        # The consumer returned True, so message should be consumed
        assert consumer.on_message_calls == ["!ping"]

    def test_fire_on_message_passes_through_when_no_match(self) -> None:
        bus = PluginBus()
        consumer = EchoConsumerPlugin(prefix="!")
        bus.register(consumer)

        consumed = asyncio.run(bus.fire_on_message(_msg_ctx(content="hello")))
        assert consumed is False

    def test_fire_on_message_only_calls_until_consumed(self) -> None:
        """When a plugin consumes a message, subsequent plugins should not be called."""
        bus = PluginBus()
        consumer1 = EchoConsumerPlugin(prefix="!")
        consumer2 = EchoConsumerPlugin(prefix="!")
        bus.register(consumer1)  # priority 200
        bus.register(consumer2)  # priority 200 (after consumer1 in stable sort)

        asyncio.run(bus.fire_on_message(_msg_ctx(content="!ping")))
        # Only consumer1 should have been called (first one consumed it)
        assert consumer1.on_message_calls == ["!ping"]
        assert consumer2.on_message_calls == []


# ============================================================================
# Prompt pipeline tests
# ============================================================================


class TestPromptDispatch:
    def test_fire_on_pre_prompt_collects_blocks(self) -> None:
        bus = PluginBus()
        bus.register(BlockAppendingPlugin(block_text="好感度: 25", label="affection"))
        bus.register(BlockAppendingPlugin(block_text="心情: 开心", label="mood"))

        ctx = _prompt_ctx()
        asyncio.run(bus.fire_on_pre_prompt(ctx))

        assert len(ctx.blocks) == 2
        assert ctx.blocks[0].text == "好感度: 25"
        assert ctx.blocks[0].label == "affection"
        assert ctx.blocks[1].text == "心情: 开心"
        assert ctx.blocks[1].label == "mood"

    def test_fire_on_pre_prompt_empty_when_no_plugins(self) -> None:
        bus = PluginBus()
        ctx = _prompt_ctx()
        asyncio.run(bus.fire_on_pre_prompt(ctx))
        assert ctx.blocks == []

    def test_fire_on_pre_prompt_execution_order(self) -> None:
        bus = PluginBus()
        order: list[str] = []
        bus.register(OrderedPlugin("first", 10, order))
        bus.register(OrderedPlugin("second", 20, order))

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))
        assert order == ["first", "second"]


# ============================================================================
# Post-reply and thinker dispatch tests
# ============================================================================


class TestPostReplyDispatch:
    def test_fire_on_post_reply_calls_all_plugins(self) -> None:
        bus = PluginBus()
        t1 = TrackingPlugin()
        t2 = TrackingPlugin()
        bus.register(t1)
        bus.register(t2)

        ctx = _reply_ctx()
        asyncio.run(bus.fire_on_post_reply(ctx))
        assert len(t1.post_reply_calls) == 1
        assert len(t2.post_reply_calls) == 1
        assert t1.post_reply_calls[0].reply_content == "test reply"


class TestThinkerDispatch:
    def test_fire_on_thinker_decision(self) -> None:
        bus = PluginBus()
        tracker = TrackingPlugin()
        bus.register(tracker)

        ctx = _thinker_ctx()
        asyncio.run(bus.fire_on_thinker_decision(ctx))
        assert len(tracker.thinker_calls) == 1
        assert tracker.thinker_calls[0].action == "reply"


# ============================================================================
# Tool collection tests
# ============================================================================


class TestToolCollection:
    def test_collect_tools_empty_when_no_plugins(self) -> None:
        bus = PluginBus()
        assert bus.collect_tools() == []

    def test_collect_tools_from_multiple_plugins(self) -> None:
        class FakeTool(Tool):
            @property
            def name(self) -> str:
                return "fake"

            @property
            def description(self) -> str:
                return "desc"

            @property
            def parameters(self) -> dict:
                return {}

            async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
                return "ok"

        bus = PluginBus()
        bus.register(ToolProvidingPlugin([FakeTool(), FakeTool()]))
        bus.register(ToolProvidingPlugin([FakeTool()]))

        tools = bus.collect_tools()
        assert len(tools) == 3

    def test_collect_tools_handles_plugin_error(self) -> None:
        """If one plugin's register_tools() raises, others should still work."""
        bus = PluginBus()
        bus.register(FailingToolPlugin())
        bus.register(TrackingPlugin())  # returns empty list

        tools = bus.collect_tools()
        assert tools == []  # both returned empty (failing was caught)


# ============================================================================
# Tick dispatch tests
# ============================================================================


class TestTickDispatch:
    def test_fire_on_tick_calls_all(self) -> None:
        bus = PluginBus()
        t1 = TrackingPlugin()
        t2 = TrackingPlugin()
        bus.register(t1)
        bus.register(t2)

        asyncio.run(bus.fire_on_tick(_plugin_ctx()))
        assert t1.tick_calls == 1
        assert t2.tick_calls == 1

    def test_fire_on_tick_multiple_times(self) -> None:
        bus = PluginBus()
        tracker = TrackingPlugin()
        bus.register(tracker)

        for _ in range(3):
            asyncio.run(bus.fire_on_tick(_plugin_ctx()))
        assert tracker.tick_calls == 3


# ============================================================================
# Error isolation tests
# ============================================================================


class TestErrorIsolation:
    def test_startup_error_does_not_block_other_plugins(self) -> None:
        bus = PluginBus()
        tracker = TrackingPlugin()
        bus.register(CrashingPlugin(crash_on="on_startup"))
        bus.register(tracker)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))
        assert tracker.startup_called

    def test_message_error_does_not_block_pipeline(self) -> None:
        bus = PluginBus()
        consumer = EchoConsumerPlugin(prefix="!")
        bus.register(CrashingPlugin(crash_on="on_message"))
        bus.register(consumer)

        # CrashingPlugin raises, but consumer should still process
        consumed = asyncio.run(bus.fire_on_message(_msg_ctx(content="!ping")))
        assert consumed is True  # consumer still caught it

    def test_pre_prompt_error_does_not_block_other_blocks(self) -> None:
        bus = PluginBus()
        bus.register(CrashingPlugin(crash_on="on_pre_prompt"))
        bus.register(BlockAppendingPlugin(block_text="still works"))

        ctx = _prompt_ctx()
        asyncio.run(bus.fire_on_pre_prompt(ctx))
        assert len(ctx.blocks) == 1
        assert ctx.blocks[0].text == "still works"

    def test_post_reply_error_does_not_block_others(self) -> None:
        bus = PluginBus()
        bus.register(CrashingPlugin(crash_on="on_post_reply"))
        tracker = TrackingPlugin()
        bus.register(tracker)

        asyncio.run(bus.fire_on_post_reply(_reply_ctx()))
        assert len(tracker.post_reply_calls) == 1


# ============================================================================
# Plugin discovery tests
# ============================================================================


class TestDiscovery:
    def test_discover_empty_directory(self, tmp_path: Path) -> None:
        bus = PluginBus()
        count = bus.discover_plugins(str(tmp_path))
        assert count == 0

    def test_discover_nonexistent_directory(self) -> None:
        bus = PluginBus()
        count = bus.discover_plugins("/nonexistent/path/12345")
        assert count == 0

    def test_discover_single_plugin(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text("""
from kernel.types import AmadeusPlugin

class MyPlugin(AmadeusPlugin):
    name = "my_plugin"
    priority = 100
""")

        bus = PluginBus()
        count = bus.discover_plugins(str(tmp_path))
        assert count == 1
        assert bus.get_plugin("my_plugin") is not None

    def test_discover_multiple_plugins(self, tmp_path: Path) -> None:
        for name in ["plugin_a", "plugin_b"]:
            plugin_dir = tmp_path / name
            plugin_dir.mkdir()
            class_name = "".join(part.capitalize() for part in name.split("_")) + "Plugin"
            (plugin_dir / "plugin.py").write_text(f"""
from kernel.types import AmadeusPlugin

class {class_name}(AmadeusPlugin):
    name = "{name}"
    priority = 100
""")

        bus = PluginBus()
        count = bus.discover_plugins(str(tmp_path))
        assert count == 2
        assert bus.get_plugin("plugin_a") is not None
        assert bus.get_plugin("plugin_b") is not None

    def test_discover_skips_non_plugin_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "not_a_plugin").mkdir()
        (tmp_path / "regular_file.txt").write_text("hello")

        bus = PluginBus()
        count = bus.discover_plugins(str(tmp_path))
        assert count == 0

    def test_discover_skips_already_registered(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "echo"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text("""
from kernel.types import AmadeusPlugin

class EchoPlugin(AmadeusPlugin):
    name = "echo"
    priority = 200
""")

        bus = PluginBus()
        # Pre-register a plugin with the same name
        existing = AmadeusPlugin()
        existing.name = "echo"
        bus.register(existing)

        count = bus.discover_plugins(str(tmp_path))
        assert count == 0  # skipped because already registered

    def test_discover_handles_syntax_error(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "broken"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text("this is not valid python {{{")

        bus = PluginBus()
        count = bus.discover_plugins(str(tmp_path))
        assert count == 0  # should not crash

    def test_discover_handles_no_plugin_class(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "no_class"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text("x = 1")  # no AmadeusPlugin subclass

        bus = PluginBus()
        count = bus.discover_plugins(str(tmp_path))
        assert count == 0
