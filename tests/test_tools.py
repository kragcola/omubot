"""工具系统测试：注册表、SSRF 校验、鉴权。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.calendar_context.service import CalendarContextService
from services.tools.context import ToolContext
from services.tools.datetime_tool import DateTimeTool
from services.tools.group_admin import MuteUserTool, SendGroupMsgTool
from services.tools.registry import ToolRegistry
from services.tools.web_fetch import _is_safe_url
from services.tools.web_search import WebSearchTool

# ── SSRF 校验 ──


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com", True),
        ("https://api.github.com/repos", True),
        ("http://localhost:8080", False),
        ("http://127.0.0.1:3000", False),
        ("http://10.0.0.1/admin", False),
        ("http://192.168.1.1", False),
        ("http://172.16.0.1", False),
        ("http://169.254.169.254/latest/meta-data/", False),
        ("http://napcat:3001", False),
        ("http://host.docker.internal:34567", False),
        ("", False),
        ("not-a-url", False),
    ],
)
def test_is_safe_url(url: str, expected: bool) -> None:
    assert _is_safe_url(url) == expected


# ── ToolRegistry ──


async def test_registry_call() -> None:
    registry = ToolRegistry()
    registry.register(DateTimeTool())
    ctx = ToolContext(user_id="123")

    result = await registry.call("get_datetime", "{}", ctx)
    assert "20" in result  # 包含年份


async def test_registry_unknown_tool() -> None:
    registry = ToolRegistry()
    ctx = ToolContext(user_id="123")
    result = await registry.call("nonexistent", "{}", ctx)
    assert "未知工具" in result


async def test_registry_to_openai_tools() -> None:
    registry = ToolRegistry()
    registry.register(DateTimeTool())
    tools = registry.to_openai_tools()
    assert len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "get_datetime"


async def test_registry_empty() -> None:
    registry = ToolRegistry()
    assert registry.empty
    registry.register(DateTimeTool())
    assert not registry.empty


# ── 群管理鉴权 ──


async def test_mute_requires_superuser() -> None:
    tool = MuteUserTool(superusers={"admin1"})
    ctx = ToolContext(bot=object(), user_id="regular_user", group_id="123")
    result = await tool.execute(ctx, user_id="target", duration=60)
    assert "权限不足" in result


async def test_mute_requires_group() -> None:
    tool = MuteUserTool(superusers={"admin1"})
    ctx = ToolContext(bot=object(), user_id="admin1", group_id=None)
    result = await tool.execute(ctx, user_id="target", duration=60)
    assert "仅在群聊中" in result


async def test_send_group_msg_uses_send_queue_when_available() -> None:
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    send_queue = MagicMock()
    send_queue.send_group_text = AsyncMock()
    tool = SendGroupMsgTool(superusers={"admin1"})
    ctx = ToolContext(bot=bot, user_id="admin1", group_id="123", extra={"send_queue": send_queue})

    result = await tool.execute(ctx, group_id="456", message="hello")

    assert "已发送消息到群 456" in result
    send_queue.send_group_text.assert_awaited_once_with("456", "hello", humanize="skip")
    bot.send_group_msg.assert_not_awaited()


async def test_send_group_msg_direct_fallback_without_send_queue() -> None:
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    tool = SendGroupMsgTool(superusers={"admin1"})
    ctx = ToolContext(bot=bot, user_id="admin1", group_id="123")

    result = await tool.execute(ctx, group_id="456", message="hello")

    assert "已发送消息到群 456" in result
    bot.send_group_msg.assert_awaited_once_with(group_id=456, message="hello")


# ── DateTimeTool ──


async def test_datetime_tool() -> None:
    service = CalendarContextService()
    service.load_dataset(
        birthdays_path=Path("plugins/calendar_context/data/birthdays.json"),
        special_days_path=Path("plugins/calendar_context/data/special_days.json"),
        builtin_years_dir=Path("plugins/calendar_context/data/years"),
    )
    tool = DateTimeTool(calendar_service=service)
    ctx = ToolContext(user_id="123")
    result = await tool.execute(ctx)
    assert "周" in result  # 包含星期
    assert "-" in result  # 日期格式


async def test_datetime_tool_renders_multiple_special_days() -> None:
    service = CalendarContextService()
    service.load_dataset(
        birthdays_path=Path("plugins/calendar_context/data/birthdays.json"),
        special_days_path=Path("plugins/calendar_context/data/special_days.json"),
        builtin_years_dir=Path("plugins/calendar_context/data/years"),
    )
    ctx = service.get_day_context(__import__("datetime").datetime(2026, 6, 1, 12, 0))
    assert ctx.special_days == ["儿童节", "国际牛奶日"]


# ── ToolRegistry 错误处理 ──


async def test_registry_bad_arguments() -> None:
    registry = ToolRegistry()
    registry.register(DateTimeTool())
    ctx = ToolContext(user_id="123")
    result = await registry.call("get_datetime", "not-json", ctx)
    assert "工具执行出错" in result


# ── WebSearchTool ──


async def test_web_search_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_results = [
        {"title": "Result 1", "href": "https://example.com/1", "body": "Snippet 1"},
        {"title": "Result 2", "href": "https://example.com/2", "body": "Snippet 2"},
    ]
    monkeypatch.setattr(
        "services.tools.web_search._ddg_search_sync",
        lambda q, n: fake_results,
    )
    tool = WebSearchTool()
    ctx = ToolContext(user_id="123")
    result = await tool.execute(ctx, query="test")
    assert "Result 1" in result
    assert "https://example.com/1" in result
    assert "Result 2" in result
    assert "1." in result and "2." in result


async def test_web_search_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.tools.web_search._ddg_search_sync",
        lambda q, n: [],
    )
    tool = WebSearchTool()
    ctx = ToolContext(user_id="123")
    result = await tool.execute(ctx, query="nonexistent gibberish xyz")
    assert "未找到" in result


async def test_web_search_error_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error(q: str, n: int) -> list:
        raise RuntimeError("network error")

    monkeypatch.setattr("services.tools.web_search._ddg_search_sync", raise_error)
    tool = WebSearchTool()
    ctx = ToolContext(user_id="123")
    result = await tool.execute(ctx, query="test")
    assert "搜索失败" in result


async def test_web_search_max_results_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    def capture_n(q: str, n: int) -> list:
        captured["n"] = n
        return []

    monkeypatch.setattr("services.tools.web_search._ddg_search_sync", capture_n)
    tool = WebSearchTool()
    ctx = ToolContext(user_id="123")
    await tool.execute(ctx, query="test", max_results=99)
    assert captured["n"] == 10
