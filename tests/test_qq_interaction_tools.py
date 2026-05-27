"""Tests for QQ outbound interaction tools."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools.context import ToolContext
from services.tools.interaction_tools import QQInteractionTool, reset_interaction_tool_limits
from services.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_limits() -> None:
    reset_interaction_tool_limits()


def _ctx(*, profile: str = "performance", group_id: str | None = "100") -> ToolContext:
    bot = SimpleNamespace(call_api=AsyncMock(return_value=None))
    return ToolContext(bot=bot, user_id="200", group_id=group_id, extra={"humanization_profile": profile})


async def test_poke_user_calls_napcat_send_poke() -> None:
    ctx = _ctx()
    result = await QQInteractionTool("poke").execute(ctx, user_id="300")

    assert result == "已戳 300"
    ctx.bot.call_api.assert_awaited_once_with("send_poke", user_id=300, group_id=100)


async def test_react_to_message_calls_napcat_emoji_like() -> None:
    ctx = _ctx()
    result = await QQInteractionTool("reaction").execute(ctx, message_id="9001", emoji_code="66")

    assert result == "已添加表情回应"
    ctx.bot.call_api.assert_awaited_once_with("set_msg_emoji_like", message_id=9001, emoji_id="66")


async def test_poke_group_rate_limit_caps_two_per_minute() -> None:
    tool = QQInteractionTool("poke")
    assert "已戳" in await tool.execute(_ctx(group_id="100"), user_id="301")
    assert "已戳" in await tool.execute(_ctx(group_id="100"), user_id="302")

    result = await tool.execute(_ctx(group_id="100"), user_id="303")

    assert "过于频繁" in result


async def test_poke_user_rate_limit_caps_one_per_five_minutes() -> None:
    tool = QQInteractionTool("poke")
    assert "已戳" in await tool.execute(_ctx(group_id="101"), user_id="301")

    result = await tool.execute(_ctx(group_id="102"), user_id="301")

    assert "过于频繁" in result


async def test_react_group_rate_limit_caps_three_per_minute() -> None:
    tool = QQInteractionTool("reaction")
    for message_id in ("1", "2", "3"):
        assert "已添加" in await tool.execute(_ctx(group_id="100"), message_id=message_id, emoji_code="66")

    result = await tool.execute(_ctx(group_id="100"), message_id="4", emoji_code="66")

    assert "过于频繁" in result


def test_economy_profile_does_not_register_interaction_tools() -> None:
    registry = ToolRegistry()
    registry.register_interaction_tools(profile="economy")

    assert registry.empty


def test_performance_profile_registers_interaction_tools() -> None:
    registry = ToolRegistry()
    registry.register_interaction_tools(profile="performance")

    assert registry.get("poke_user") is not None
    assert registry.get("react_to_message") is not None


def test_resolved_flags_control_registration() -> None:
    resolved = SimpleNamespace(
        qq_interactions_poke_outbound_enabled=True,
        qq_interactions_reaction_outbound_enabled=False,
    )
    registry = ToolRegistry()
    registry.register_interaction_tools(resolved_humanization=resolved, profile="custom")

    assert registry.get("poke_user") is not None
    assert registry.get("react_to_message") is None


async def test_balanced_active_poke_is_rejected() -> None:
    ctx = _ctx(profile="balanced")
    result = await QQInteractionTool("poke").execute(ctx, user_id="300")

    assert "未开启" in result
    ctx.bot.call_api.assert_not_awaited()


async def test_balanced_passive_context_can_poke() -> None:
    ctx = _ctx(profile="balanced")
    ctx.extra["trigger_mode"] = "qq_interaction"
    result = await QQInteractionTool("poke").execute(ctx, user_id="300")

    assert result == "已戳 300"
    ctx.bot.call_api.assert_awaited_once()


async def test_cancelled_poke_releases_token_bucket() -> None:
    ctx = _ctx()
    ctx.bot.call_api = AsyncMock(side_effect=asyncio.CancelledError())
    tool = QQInteractionTool("poke")

    with pytest.raises(asyncio.CancelledError):
        await tool.execute(ctx, user_id="300")

    ctx.bot.call_api = AsyncMock(return_value=None)
    result = await tool.execute(ctx, user_id="300")

    assert result == "已戳 300"
