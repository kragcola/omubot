"""工具系统测试：注册表、SSRF 校验、鉴权。"""

import asyncio
from typing import Any

import pytest

from services.tools.base import Tool
from services.tools.context import ToolContext
from services.tools.datetime_tool import DateTimeTool
from services.tools.group_admin import MuteUserTool
from services.tools.registry import ToolRegistry
from services.tools.web_fetch import _is_safe_url
from services.tools.web_search import WebSearchTool


class _BlockingTool(Tool):
    def __init__(self, name: str = "blocking") -> None:
        self._name = name
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.completed = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "blocks until cancelled"

    @property
    def parameters(self) -> dict[str, Any]:
        return {}

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.entered.set()
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.completed = True
        return "completed"


class _CancellationSwallowingTool(_BlockingTool):
    def __init__(self) -> None:
        super().__init__("swallowing")
        self.cancel_count = 0

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self.cancel_count += 1
        try:
            await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            self.cancel_count += 1
            raise
        self.completed = True
        return "late success"


class _InternalTimeoutTool(_BlockingTool):
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        raise TimeoutError("tool-owned timeout")


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


async def test_registry_rejects_duplicate_tool_name() -> None:
    registry = ToolRegistry()
    original = _BlockingTool("duplicate")
    registry.register(original)

    with pytest.raises(ValueError, match="duplicate"):
        registry.register(_BlockingTool("duplicate"))

    assert registry.get("duplicate") is original


async def test_registry_tool_deadline_cancels_execution() -> None:
    registry = ToolRegistry(default_timeout_seconds=0.02)
    tool = _BlockingTool()
    registry.register(tool)

    result = await registry.call(tool.name, "{}", ToolContext(user_id="123"))

    assert "超时" in result
    assert tool.cancelled.is_set()
    assert tool.completed is False


async def test_registry_outer_cancellation_propagates() -> None:
    registry = ToolRegistry(default_timeout_seconds=10)
    tool = _BlockingTool()
    registry.register(tool)

    task = asyncio.create_task(
        registry.call(tool.name, "{}", ToolContext(user_id="123"))
    )
    await tool.entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert tool.cancelled.is_set()
    assert tool.completed is False


async def test_registry_deadline_cannot_be_bypassed_by_swallowing_cancel() -> None:
    registry = ToolRegistry(default_timeout_seconds=0.02)
    tool = _CancellationSwallowingTool()
    registry.register(tool)

    started_at = asyncio.get_running_loop().time()
    result = await registry.call(tool.name, "{}", ToolContext(user_id="123"))
    elapsed = asyncio.get_running_loop().time() - started_at
    await asyncio.sleep(0.05)

    assert elapsed < 0.08
    assert "超时" in result
    assert tool.cancel_count >= 2
    assert tool.completed is False


async def test_registry_does_not_misclassify_tool_owned_timeout_error() -> None:
    registry = ToolRegistry(default_timeout_seconds=10)
    tool = _InternalTimeoutTool("internal_timeout")
    registry.register(tool)

    result = await registry.call(tool.name, "{}", ToolContext(user_id="123"))

    assert result == "工具执行出错，请稍后重试"


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


# ── DateTimeTool ──


async def test_datetime_tool() -> None:
    tool = DateTimeTool()
    ctx = ToolContext(user_id="123")
    result = await tool.execute(ctx)
    assert "周" in result  # 包含星期
    assert "-" in result  # 日期格式


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
