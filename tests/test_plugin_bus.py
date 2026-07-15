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
        topic_intent_label="闲聊",
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
        self.dependencies: dict[str, str] = {}
        self.optional_dependencies: dict[str, str] = {}

    async def on_startup(self, ctx: PluginContext) -> None:
        self.recorder.append(self.name)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self.recorder.append(self.name)


class DependencyPlugin(TrackingPlugin):
    def __init__(self, name: str, *, version: str = "1.0.0") -> None:
        super().__init__()
        self.name = name
        self.version = version
        self.dependencies = {}
        self.required_dependencies: dict[str, str] = {}
        self.optional_dependencies: dict[str, str] = {}


class FailingStartupDependencyPlugin(DependencyPlugin):
    async def on_startup(self, ctx: PluginContext) -> None:
        raise RuntimeError("provider startup failed")


class PartiallyStartedPlugin(TrackingPlugin):
    name = "partially_started"

    def __init__(self) -> None:
        super().__init__()
        self.resource_allocated = False
        self.resource_released = False

    async def on_startup(self, ctx: PluginContext) -> None:
        self.resource_allocated = True
        raise RuntimeError("startup failed after allocating resource")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self.resource_released = True


class TimeoutStartupDependencyPlugin(DependencyPlugin):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.hook_budgets_ms = {"on_startup": 10}
        self.cancelled = asyncio.Event()

    async def on_startup(self, ctx: PluginContext) -> None:
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.startup_called = True


class ToolProvidingPlugin(AmadeusPlugin):
    """Provides concrete Tool instances."""
    name = "tool_provider"
    priority = 1

    def __init__(self, tools: list[Tool] | None = None):
        super().__init__()
        self._tools = tools or []

    def register_tools(self) -> list[Tool]:
        return list(self._tools)


class PermissionedPlugin(TrackingPlugin):
    name = "permissioned"
    permissions = ["message"]  # noqa: RUF012 - test fixture metadata


class SlowHookPlugin(TrackingPlugin):
    name = "slow_hook"
    hook_budget_ms = 1

    async def on_tick(self, ctx: PluginContext) -> None:
        await asyncio.sleep(0.01)
        await super().on_tick(ctx)


class BlockingHookPlugin(TrackingPlugin):
    name = "blocking_hook"
    hook_budget_ms = 20

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def on_tick(self, ctx: PluginContext) -> None:
        self.entered.set()
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.tick_calls += 1


class SlowStartupPlugin(TrackingPlugin):
    name = "slow_startup"
    hook_budget_ms = 1

    async def on_startup(self, ctx: PluginContext) -> None:
        await asyncio.sleep(0.02)
        self.startup_called = True


class ExplicitDeadlineStartupPlugin(TrackingPlugin):
    name = "deadline_startup"

    def __init__(self) -> None:
        super().__init__()
        self.hook_budgets_ms = {"on_startup": 10}
        self.cancelled = asyncio.Event()

    async def on_startup(self, ctx: PluginContext) -> None:
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.startup_called = True


class CancellationSwallowingHookPlugin(TrackingPlugin):
    name = "swallowing_hook"
    hook_budget_ms = 20

    def __init__(self) -> None:
        super().__init__()
        self.cancel_count = 0

    async def on_tick(self, ctx: PluginContext) -> None:
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self.cancel_count += 1
        try:
            await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            self.cancel_count += 1
            raise
        self.tick_calls += 1


class InternalTimeoutHookPlugin(TrackingPlugin):
    name = "internal_timeout"

    async def on_tick(self, ctx: PluginContext) -> None:
        raise TimeoutError("plugin-owned timeout")


class RecoveringMessagePlugin(AmadeusPlugin):
    name = "recovering_message"
    priority = 80

    def __init__(self, fail_times: int = 1):
        super().__init__()
        self.fail_times = fail_times
        self.calls = 0

    async def on_message(self, ctx: MessageContext) -> bool:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("temporary failure")
        return False


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
        t2.name = "tracker_2"
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

    def test_shutdown_cleans_up_plugin_disabled_after_partial_startup(self) -> None:
        bus = PluginBus()
        plugin = PartiallyStartedPlugin()
        bus.register(plugin)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))
        assert plugin.resource_allocated is True
        assert plugin.enabled is False

        asyncio.run(bus.fire_on_shutdown(_plugin_ctx()))

        assert plugin.resource_released is True

    def test_boot_disabled_runtime_plugin_is_warmed_before_hot_enable(self) -> None:
        bus = PluginBus()
        plugin = TrackingPlugin()
        plugin.enabled = False
        bus.register(plugin)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))
        assert plugin.startup_called is True
        assert plugin.enabled is False

        assert bus.set_plugin_enabled("tracker", True) is True
        asyncio.run(bus.fire_on_tick(_plugin_ctx()))

        assert plugin.tick_calls == 1
        [health] = bus.plugin_health()
        assert health["state"] == "healthy"

    def test_default_hook_budget_does_not_limit_lifecycle_hooks(self) -> None:
        bus = PluginBus()
        plugin = SlowStartupPlugin()
        bus.register(plugin)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert plugin.startup_called is True
        [health] = bus.plugin_health()
        assert health["timeout_calls"] == 0

    def test_explicit_lifecycle_hook_budget_enforces_deadline(self) -> None:
        bus = PluginBus()
        plugin = ExplicitDeadlineStartupPlugin()
        bus.register(plugin)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert plugin.startup_called is False
        assert plugin.cancelled.is_set()
        [health] = bus.plugin_health()
        assert health["timeout_calls"] == 1
        assert health["last_timeout_hook"] == "on_startup"


class TestDependencyContract:
    def test_legacy_missing_dependency_fails_closed(self) -> None:
        bus = PluginBus()
        plugin = DependencyPlugin("consumer")
        plugin.dependencies = {"missing": ">=1.0.0"}
        bus.register(plugin)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))
        asyncio.run(bus.fire_on_tick(_plugin_ctx()))

        assert plugin.startup_called is False
        assert plugin.tick_calls == 0
        assert plugin.enabled is False
        [health] = bus.plugin_health()
        assert health["state"] == "disabled"
        assert health["dependency_blocked"] is True
        assert any("missing" in error for error in health["dependency_errors"])

    @pytest.mark.parametrize(
        ("provider_enabled", "provider_version", "expected_reason"),
        [
            (False, "1.0.0", "disabled"),
            (True, "1.0.0", "version mismatch"),
        ],
    )
    def test_required_dependency_disabled_or_incompatible_fails_closed(
        self,
        provider_enabled: bool,
        provider_version: str,
        expected_reason: str,
    ) -> None:
        bus = PluginBus()
        provider = DependencyPlugin("provider", version=provider_version)
        provider.enabled = provider_enabled
        consumer = DependencyPlugin("consumer")
        consumer.required_dependencies = {"provider": ">=2.0.0"}
        bus.register(provider)
        bus.register(consumer)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert consumer.startup_called is False
        assert consumer.enabled is False
        health = {item["name"]: item for item in bus.plugin_health()}
        assert any(
            expected_reason in error
            for error in health["consumer"]["dependency_errors"]
        )

    def test_optional_dependency_missing_does_not_block_startup(self) -> None:
        bus = PluginBus()
        plugin = DependencyPlugin("consumer")
        plugin.optional_dependencies = {"missing": ">=1.0.0"}
        bus.register(plugin)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert plugin.startup_called is True
        assert plugin.enabled is True
        [health] = bus.plugin_health()
        assert health["dependency_blocked"] is False
        assert health["dependency_errors"] == []
        assert health["optional_dependency_degraded"] is True
        assert any(
            "missing" in error
            for error in health["optional_dependency_errors"]
        )
        assert health["state"] == "degraded"

    @pytest.mark.parametrize(
        ("provider_enabled", "provider_version", "expected_reason"),
        [
            (False, "2.0.0", "disabled"),
            (True, "1.0.0", "version mismatch"),
        ],
    )
    def test_optional_dependency_unavailable_reports_degraded_without_blocking(
        self,
        provider_enabled: bool,
        provider_version: str,
        expected_reason: str,
    ) -> None:
        bus = PluginBus()
        provider = DependencyPlugin("provider", version=provider_version)
        provider.enabled = provider_enabled
        consumer = DependencyPlugin("consumer")
        consumer.optional_dependencies = {"provider": ">=2.0.0"}
        bus.register(provider)
        bus.register(consumer)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert consumer.startup_called is True
        assert consumer.enabled is True
        health = {item["name"]: item for item in bus.plugin_health()}["consumer"]
        assert health["dependency_blocked"] is False
        assert health["optional_dependency_degraded"] is True
        assert any(
            expected_reason in error
            for error in health["optional_dependency_errors"]
        )
        assert health["state"] == "degraded"

    def test_optional_dependency_health_recovers_after_provider_reenabled(self) -> None:
        bus = PluginBus()
        provider = DependencyPlugin("provider")
        consumer = DependencyPlugin("consumer")
        consumer.optional_dependencies = {"provider": ">=1.0.0"}
        bus.register(provider)
        bus.register(consumer)
        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert bus.set_plugin_enabled("provider", False) is True
        degraded = {item["name"]: item for item in bus.plugin_health()}["consumer"]
        assert consumer.enabled is True
        assert degraded["optional_dependency_degraded"] is True
        assert degraded["state"] == "degraded"

        assert bus.set_plugin_enabled("provider", True) is True
        recovered = {item["name"]: item for item in bus.plugin_health()}["consumer"]
        assert recovered["optional_dependency_degraded"] is False
        assert recovered["optional_dependency_errors"] == []
        assert recovered["state"] == "healthy"

    def test_optional_dependency_startup_failure_degrades_consumer(self) -> None:
        bus = PluginBus()
        provider = FailingStartupDependencyPlugin("provider")
        consumer = DependencyPlugin("consumer")
        consumer.optional_dependencies = {"provider": ">=1.0.0"}
        bus.register(provider)
        bus.register(consumer)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert provider.enabled is False
        assert consumer.enabled is True
        assert consumer.startup_called is True
        health = {item["name"]: item for item in bus.plugin_health()}["consumer"]
        assert health["optional_dependency_degraded"] is True
        assert any(
            "startup failed" in error
            for error in health["optional_dependency_errors"]
        )

    def test_required_and_optional_dependencies_order_before_consumer(self) -> None:
        bus = PluginBus()
        order: list[str] = []
        required = OrderedPlugin("required", 100, order)
        optional = OrderedPlugin("optional", 200, order)
        consumer = OrderedPlugin("consumer", 1, order)
        consumer.dependencies = {"required": ">=0.1.0"}
        consumer.optional_dependencies = {"optional": ">=0.1.0"}
        bus.register(consumer)
        bus.register(optional)
        bus.register(required)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert order == ["required", "optional", "consumer"]

    def test_manifest_applies_required_and_optional_dependencies(self) -> None:
        plugin = DependencyPlugin("consumer")

        PluginBus._apply_manifest(
            plugin,
            {
                "required_dependencies": {"required": ">=1.0.0"},
                "optional_dependencies": {"optional": "~=2.0"},
            },
        )

        assert plugin.required_dependencies == {"required": ">=1.0.0"}
        assert plugin.optional_dependencies == {"optional": "~=2.0"}

    def test_runtime_toggle_cannot_bypass_required_dependency(self) -> None:
        bus = PluginBus()
        provider = DependencyPlugin("provider")
        consumer = DependencyPlugin("consumer")
        consumer.required_dependencies = {"provider": ">=1.0.0"}
        bus.register(provider)
        bus.register(consumer)
        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert bus.set_plugin_enabled("provider", False) is True
        asyncio.run(bus.fire_on_tick(_plugin_ctx()))

        assert consumer.enabled is False
        assert consumer.tick_calls == 0
        assert bus.set_plugin_enabled("consumer", True) is False
        health = {item["name"]: item for item in bus.plugin_health()}
        assert health["consumer"]["dependency_blocked"] is True

    def test_required_dependent_recovers_after_runtime_provider_reenabled(self) -> None:
        bus = PluginBus()
        provider = DependencyPlugin("provider")
        consumer = DependencyPlugin("consumer")
        consumer.required_dependencies = {"provider": ">=1.0.0"}
        bus.register(provider)
        bus.register(consumer)
        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert bus.set_plugin_enabled("provider", False) is True
        assert consumer.enabled is False

        assert bus.set_plugin_enabled("provider", True) is True

        assert consumer.enabled is True
        health = {item["name"]: item for item in bus.plugin_health()}
        assert health["consumer"]["dependency_blocked"] is False
        assert health["consumer"]["dependency_errors"] == []

    def test_transitive_required_dependents_recover_after_provider_reenabled(self) -> None:
        bus = PluginBus()
        provider = DependencyPlugin("provider")
        middle = DependencyPlugin("middle")
        consumer = DependencyPlugin("consumer")
        middle.required_dependencies = {"provider": ">=1.0.0"}
        consumer.required_dependencies = {"middle": ">=1.0.0"}
        bus.register(provider)
        bus.register(middle)
        bus.register(consumer)
        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert bus.set_plugin_enabled("provider", False) is True
        assert middle.enabled is False
        assert consumer.enabled is False

        assert bus.set_plugin_enabled("provider", True) is True

        assert middle.enabled is True
        assert consumer.enabled is True

    def test_explicitly_disabled_dependent_stays_disabled_after_provider_recovers(
        self,
    ) -> None:
        bus = PluginBus()
        provider = DependencyPlugin("provider")
        consumer = DependencyPlugin("consumer")
        consumer.required_dependencies = {"provider": ">=1.0.0"}
        bus.register(provider)
        bus.register(consumer)
        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert bus.set_plugin_enabled("provider", False) is True
        assert consumer.enabled is False
        assert bus.set_plugin_enabled("consumer", False) is True

        assert bus.set_plugin_enabled("provider", True) is True

        assert consumer.enabled is False
        health = {item["name"]: item for item in bus.plugin_health()}
        assert health["consumer"]["dependency_blocked"] is False

    def test_self_required_dependency_cycle_fails_closed(self) -> None:
        bus = PluginBus()
        plugin = DependencyPlugin("self_cycle")
        plugin.required_dependencies = {"self_cycle": ">=1.0.0"}
        bus.register(plugin)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert plugin.startup_called is False
        assert plugin.enabled is False
        [health] = bus.plugin_health()
        assert health["dependency_blocked"] is True
        assert any("cycle" in error for error in health["dependency_errors"])

    def test_mutual_required_dependency_cycle_fails_closed(self) -> None:
        bus = PluginBus()
        first = DependencyPlugin("first")
        second = DependencyPlugin("second")
        first.required_dependencies = {"second": ">=1.0.0"}
        second.required_dependencies = {"first": ">=1.0.0"}
        bus.register(first)
        bus.register(second)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert first.startup_called is False
        assert second.startup_called is False
        assert first.enabled is False
        assert second.enabled is False
        health = {item["name"]: item for item in bus.plugin_health()}
        assert all(item["dependency_blocked"] for item in health.values())
        assert all(
            any("cycle" in error for error in item["dependency_errors"])
            for item in health.values()
        )

    def test_required_provider_startup_error_blocks_consumer(self) -> None:
        bus = PluginBus()
        provider = FailingStartupDependencyPlugin("provider")
        consumer = DependencyPlugin("consumer")
        consumer.required_dependencies = {"provider": ">=1.0.0"}
        independent = DependencyPlugin("independent")
        bus.register(provider)
        bus.register(consumer)
        bus.register(independent)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert provider.enabled is False
        assert consumer.enabled is False
        assert consumer.startup_called is False
        assert independent.startup_called is True
        health = {item["name"]: item for item in bus.plugin_health()}
        assert health["provider"]["startup_failed"] is True
        assert any(
            "startup failed" in error
            for error in health["consumer"]["dependency_errors"]
        )
        assert bus.set_plugin_enabled("provider", True) is False
        assert bus.set_plugin_enabled("consumer", True) is False
        assert provider.enabled is False
        assert consumer.enabled is False

    def test_required_provider_startup_timeout_blocks_consumer(self) -> None:
        bus = PluginBus()
        provider = TimeoutStartupDependencyPlugin("provider")
        consumer = DependencyPlugin("consumer")
        consumer.required_dependencies = {"provider": ">=1.0.0"}
        bus.register(provider)
        bus.register(consumer)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert provider.cancelled.is_set()
        assert provider.enabled is False
        assert consumer.enabled is False
        assert consumer.startup_called is False
        health = {item["name"]: item for item in bus.plugin_health()}
        assert health["provider"]["startup_failed"] is True
        assert health["provider"]["timeout_calls"] == 1
        assert any(
            "startup failed" in error
            for error in health["consumer"]["dependency_errors"]
        )

    def test_optional_cycle_does_not_break_unrelated_required_startup_order(self) -> None:
        bus = PluginBus()
        order: list[str] = []
        provider = OrderedPlugin("provider", 100, order)
        consumer = OrderedPlugin("consumer", 1, order)
        consumer.dependencies = {"provider": ">=0.1.0"}
        optional_a = OrderedPlugin("optional_a", 10, order)
        optional_b = OrderedPlugin("optional_b", 20, order)
        optional_a.optional_dependencies = {"optional_b": ">=0.1.0"}
        optional_b.optional_dependencies = {"optional_a": ">=0.1.0"}
        bus.register(consumer)
        bus.register(optional_a)
        bus.register(optional_b)
        bus.register(provider)

        asyncio.run(bus.fire_on_startup(_plugin_ctx()))

        assert provider.enabled is True
        assert consumer.enabled is True
        assert "provider" in order
        assert "consumer" in order
        assert order.index("provider") < order.index("consumer")


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
        consumer2.name = "echo_consumer_2"
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
        mood_plugin = BlockAppendingPlugin(block_text="心情: 开心", label="mood")
        mood_plugin.name = "mood_block_plugin"
        bus.register(mood_plugin)

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
        t2.name = "tracker_2"
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

    def test_fire_on_thinker_decision_wait_action(self) -> None:
        bus = PluginBus()
        tracker = TrackingPlugin()
        bus.register(tracker)

        ctx = ThinkerContext(
            session_id="s1",
            group_id="g1",
            user_id="u1",
            action="wait",
            thought="先不说",
            topic_intent_label="闲聊",
        )
        asyncio.run(bus.fire_on_thinker_decision(ctx))
        assert len(tracker.thinker_calls) == 1
        assert tracker.thinker_calls[0].action == "wait"


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
        second_provider = ToolProvidingPlugin([FakeTool()])
        second_provider.name = "tool_provider_2"
        bus.register(second_provider)

        tools = bus.collect_tools()
        assert len(tools) == 3

    def test_collect_tools_fails_fast_with_plugin_owner(self) -> None:
        """A broken provider must abort atomic registry replacement."""
        bus = PluginBus()
        bus.register(FailingToolPlugin())
        bus.register(TrackingPlugin())  # returns empty list

        with pytest.raises(
            RuntimeError,
            match="collect_tools failed for plugin failing_tool",
        ):
            bus.collect_tools()


# ============================================================================
# Tick dispatch tests
# ============================================================================


class TestTickDispatch:
    def test_fire_on_tick_calls_all(self) -> None:
        bus = PluginBus()
        t1 = TrackingPlugin()
        t2 = TrackingPlugin()
        t2.name = "tracker_2"
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

    def test_plugin_health_records_errors_and_elapsed(self) -> None:
        bus = PluginBus()
        bus.register(CrashingPlugin(crash_on="on_message"))

        consumed = asyncio.run(bus.fire_on_message(_msg_ctx()))

        assert consumed is False
        [health] = bus.plugin_health()
        assert health["name"] == "crasher"
        assert health["state"] == "degraded"
        assert health["calls"] == 1
        assert health["errors"] == 1
        assert health["last_hook"] == "on_message"
        assert "crash in on_message" in health["last_error"]

    def test_set_plugin_enabled_gates_hooks(self) -> None:
        bus = PluginBus()
        tracker = TrackingPlugin()
        bus.register(tracker)

        assert bus.set_plugin_enabled("tracker", False) is True
        asyncio.run(bus.fire_on_tick(_plugin_ctx()))

        assert tracker.tick_calls == 0
        [health] = bus.plugin_health()
        assert health["state"] == "disabled"
        assert health["enabled"] is False

    def test_system_plugin_cannot_be_disabled(self) -> None:
        bus = PluginBus()
        plugin = TrackingPlugin()
        plugin.name = "chat"
        bus.register(plugin)

        assert bus.set_plugin_enabled("chat", False) is False

        [health] = bus.plugin_health()
        assert health["enabled"] is True
        assert plugin.enabled is True
        assert plugin.tier == "system"
        assert plugin.toggle_policy == "locked"

    def test_manifest_permissions_gate_hook_and_tool_collection(self) -> None:
        bus = PluginBus()
        plugin = PermissionedPlugin()
        bus.register(plugin)

        consumed = asyncio.run(bus.fire_on_message(_msg_ctx()))
        asyncio.run(bus.fire_on_thinker_decision(_thinker_ctx()))
        asyncio.run(bus.fire_on_post_reply(_reply_ctx()))
        asyncio.run(bus.fire_on_tick(_plugin_ctx()))

        assert consumed is False
        assert plugin.tick_calls == 0
        assert bus.collect_tools() == []
        [health] = bus.plugin_health()
        assert health["permission_denials"] == 4
        assert health["permission_denials_by_hook"] == {
            "reply": {
                "on_thinker_decision": 1,
                "on_post_reply": 1,
            },
            "tick": {"on_tick": 1},
            "tool": {"register_tools": 1},
        }
        assert health["state"] == "permission_limited"
        assert health["display_label"] == "按权限运行"
        assert health["display_type"] == "info"
        assert health["last_permission_denied"] == "tool"
        assert health["last_permission_denied_hook"] == "register_tools"

    def test_hook_budget_records_slow_calls(self) -> None:
        bus = PluginBus()
        plugin = SlowHookPlugin()
        bus.register(plugin)

        asyncio.run(bus.fire_on_tick(_plugin_ctx()))

        [health] = bus.plugin_health()
        assert health["state"] == "degraded"
        assert health["slow_calls"] == 1
        assert health["last_slow_hook"] == "on_tick"
        assert health["hooks"]["on_tick"]["slow_calls"] == 1

    def test_hook_budget_is_a_hard_timeout_and_updates_health(self) -> None:
        async def run() -> tuple[BlockingHookPlugin, list[dict[str, Any]]]:
            bus = PluginBus()
            bus._SLOW_BURST_LIMIT = 1
            bus._SOFT_ISOLATION_COOLDOWN_SECONDS = 60.0
            plugin = BlockingHookPlugin()
            bus.register(plugin)

            await bus.fire_on_tick(_plugin_ctx())
            return plugin, bus.plugin_health()

        plugin, [health] = asyncio.run(run())

        assert plugin.cancelled.is_set()
        assert plugin.tick_calls == 0
        assert health["state"] == "throttled"
        assert health["calls"] == 1
        assert health["errors"] == 1
        assert health["slow_calls"] == 1
        assert health["timeout_calls"] == 1
        assert health["last_timeout_hook"] == "on_tick"
        assert health["cooldown_reason"] == "slow_burst"
        assert health["hooks"]["on_tick"]["timeout_calls"] == 1

    def test_outer_cancellation_propagates_without_polluting_health(self) -> None:
        async def run() -> tuple[BlockingHookPlugin, list[dict[str, Any]]]:
            bus = PluginBus()
            plugin = BlockingHookPlugin()
            plugin.hook_budget_ms = 10_000
            bus.register(plugin)

            task = asyncio.create_task(bus.fire_on_tick(_plugin_ctx()))
            await plugin.entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return plugin, bus.plugin_health()

        plugin, [health] = asyncio.run(run())

        assert plugin.cancelled.is_set()
        assert plugin.tick_calls == 0
        assert health["state"] == "healthy"
        assert health["calls"] == 0
        assert health["errors"] == 0
        assert health["slow_calls"] == 0
        assert health["timeout_calls"] == 0

    def test_hook_deadline_cannot_be_bypassed_by_swallowing_cancellation(self) -> None:
        async def run() -> tuple[float, CancellationSwallowingHookPlugin, dict[str, Any]]:
            bus = PluginBus()
            plugin = CancellationSwallowingHookPlugin()
            bus.register(plugin)

            started_at = asyncio.get_running_loop().time()
            await bus.fire_on_tick(_plugin_ctx())
            elapsed = asyncio.get_running_loop().time() - started_at
            await asyncio.sleep(0.05)
            [health] = bus.plugin_health()
            return elapsed, plugin, health

        elapsed, plugin, health = asyncio.run(run())

        assert elapsed < 0.08
        assert plugin.cancel_count >= 2
        assert plugin.tick_calls == 0
        assert health["timeout_calls"] == 1

    def test_plugin_owned_timeout_error_is_not_recorded_as_hook_deadline(self) -> None:
        bus = PluginBus()
        bus.register(InternalTimeoutHookPlugin())

        asyncio.run(bus.fire_on_tick(_plugin_ctx()))

        [health] = bus.plugin_health()
        assert health["calls"] == 1
        assert health["errors"] == 1
        assert health["slow_calls"] == 0
        assert health["timeout_calls"] == 0
        assert "plugin-owned timeout" in health["last_error"]

    def test_error_burst_enters_soft_isolation_and_suppresses_future_hooks(self) -> None:
        bus = PluginBus()
        bus._ERROR_BURST_LIMIT = 3
        bus._SOFT_ISOLATION_COOLDOWN_SECONDS = 60.0
        bus.register(CrashingPlugin(crash_on="on_message"))
        consumer = EchoConsumerPlugin(prefix="!")
        bus.register(consumer)

        for _ in range(3):
            consumed = asyncio.run(bus.fire_on_message(_msg_ctx(content="!ping")))
            assert consumed is True

        [crasher, _consumer_health] = bus.plugin_health()
        assert crasher["calls"] == 3
        assert crasher["state"] == "throttled"
        assert crasher["cooldown_reason"] == "error_burst"
        assert crasher["cooldown_remaining_seconds"] > 0

        consumed = asyncio.run(bus.fire_on_message(_msg_ctx(content="!ping")))
        assert consumed is True

        [crasher, _consumer_health] = bus.plugin_health()
        assert crasher["calls"] == 3
        assert crasher["suppressed_calls"] == 1
        assert crasher["last_suppressed_hook"] == "on_message"
        assert consumer.on_message_calls == ["!ping", "!ping", "!ping", "!ping"]

    def test_soft_isolation_expires_and_plugin_can_run_again(self) -> None:
        bus = PluginBus()
        bus._ERROR_BURST_LIMIT = 1
        bus._SOFT_ISOLATION_COOLDOWN_SECONDS = 0.01
        plugin = RecoveringMessagePlugin(fail_times=1)
        bus.register(plugin)

        consumed = asyncio.run(bus.fire_on_message(_msg_ctx()))
        assert consumed is False

        [health] = bus.plugin_health()
        assert health["state"] == "throttled"

        asyncio.run(asyncio.sleep(0.02))
        consumed = asyncio.run(bus.fire_on_message(_msg_ctx()))
        assert consumed is False

        [health] = bus.plugin_health()
        assert plugin.calls == 2
        assert health["cooldown_remaining_seconds"] == 0
        assert health["state"] == "degraded"

    def test_slow_burst_enters_soft_isolation(self) -> None:
        bus = PluginBus()
        bus._SLOW_BURST_LIMIT = 1
        bus._SOFT_ISOLATION_COOLDOWN_SECONDS = 60.0
        plugin = SlowHookPlugin()
        bus.register(plugin)

        asyncio.run(bus.fire_on_tick(_plugin_ctx()))

        [health] = bus.plugin_health()
        assert health["state"] == "throttled"
        assert health["cooldown_reason"] == "slow_burst"
        assert health["slow_burst_count"] == 1

        asyncio.run(bus.fire_on_tick(_plugin_ctx()))

        [health] = bus.plugin_health()
        assert plugin.tick_calls == 0
        assert health["suppressed_calls"] == 1
        assert health["hooks"]["on_tick"]["suppressed_calls"] == 1


# ============================================================================
# Plugin discovery tests
# ============================================================================


def _write_discovery_plugin_contract(plugin_dir: Path, name: str) -> None:
    (plugin_dir / "plugin.json").write_text(
        f"""{{
  "manifest_version": 3,
  "name": "{name}",
  "display_name": {{"zh": "{name}", "en": "{name}"}},
  "description": "Plugin discovery test fixture.",
  "version": "0.1.0",
  "priority": 100,
  "tier": "user",
  "toggle_policy": "runtime",
  "category": "tool",
  "permissions": [],
  "capabilities": [],
  "author": "Omubot Tests",
  "min_omubot_version": "0.1.0",
  "config": {{
    "defaults": "config.default.json",
    "schema": "config.schema.json",
    "apply_mode": "hot",
    "restart_required_fields": []
  }},
  "store": {{
    "visibility": "local",
    "marketplace_id": ""
  }}
}}
""",
        encoding="utf-8",
    )
    (plugin_dir / "config.default.json").write_text(
        f'{{"schema_version": 1, "plugin": "{name}", "values": {{}}}}\n',
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}, "additionalProperties": false}\n',
        encoding="utf-8",
    )


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
        _write_discovery_plugin_contract(plugin_dir, "my_plugin")
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
            _write_discovery_plugin_contract(plugin_dir, name)
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
