"""工具系统测试：注册表、SSRF 校验、鉴权。"""

import asyncio
from typing import Any

import pytest

import services.tools.http_api as http_api_module
import services.tools.web_fetch as web_fetch_module
import services.tools.web_search as web_search_module
from services.tools.base import Tool
from services.tools.context import ToolContext
from services.tools.datetime_tool import DateTimeTool
from services.tools.group_admin import MuteUserTool
from services.tools.http_api import HttpApiTool
from services.tools.registry import ToolRegistry
from services.tools.safe_http import PublicHttpTextResponse, UnsafePublicUrl
from services.tools.web_fetch import WebFetchTool, _is_safe_url
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


async def test_web_fetch_execute_uses_pinned_public_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        captured["url"] = url
        captured.update(kwargs)
        return PublicHttpTextResponse(
            status_code=200,
            text="<p>safe body</p>",
            final_url=url,
            truncated=False,
        )

    def forbid_legacy_transport(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("legacy httpx transport was used")

    monkeypatch.setattr(
        web_fetch_module,
        "fetch_public_text",
        fake_fetch,
        raising=False,
    )
    monkeypatch.setattr("httpx.AsyncClient", forbid_legacy_transport)

    result = await WebFetchTool(max_length=1000).execute(
        ToolContext(user_id="10001"),
        url="https://93.184.216.34/docs",
    )

    assert result == "safe body"
    assert captured["url"] == "https://93.184.216.34/docs"
    assert captured["follow_redirects"] is True
    assert captured["allow_proxy_dns_net"] is True
    assert captured["max_bytes"] >= 4000


async def test_web_fetch_execute_maps_transport_target_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        del url, kwargs
        raise UnsafePublicUrl("redirect crossed origin")

    def forbid_legacy_transport(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("legacy httpx transport was used")

    monkeypatch.setattr(
        web_fetch_module,
        "fetch_public_text",
        reject_fetch,
        raising=False,
    )
    monkeypatch.setattr("httpx.AsyncClient", forbid_legacy_transport)

    result = await WebFetchTool().execute(
        ToolContext(user_id="10001"),
        url="https://93.184.216.34/start",
    )

    assert result == "拒绝访问: 不允许访问非公网或跨源地址"


async def test_web_fetch_legacy_execute_keeps_timeout_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timeout_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        del url, kwargs
        raise TimeoutError("transport timed out")

    monkeypatch.setattr(web_fetch_module, "fetch_public_text", timeout_fetch)

    result = await WebFetchTool(timeout_seconds=7).execute(
        ToolContext(user_id="10001"),
        url="https://example.com/docs",
    )

    assert result == "请求超时: 网页在 7 秒内未响应 (https://example.com/docs)"


async def test_http_api_get_only_execute_uses_pinned_public_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        captured["url"] = url
        captured.update(kwargs)
        return PublicHttpTextResponse(
            status_code=200,
            text='{"temperature": 22}',
            final_url=url,
            truncated=False,
        )

    def forbid_legacy_transport(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("legacy httpx transport was used")

    monkeypatch.setattr(http_api_module, "fetch_public_text", fake_fetch, raising=False)
    monkeypatch.setattr(http_api_module.httpx, "AsyncClient", forbid_legacy_transport)

    result = await HttpApiTool(allowed_methods=["GET"]).execute(
        ToolContext(user_id="10001"),
        method="GET",
        url="https://api.example.com/weather",
        headers={"Accept": "application/json"},
    )

    assert '"temperature": 22' in result
    assert captured["url"] == "https://api.example.com/weather"
    assert captured["headers"] == {"Accept": "application/json"}
    assert captured["follow_redirects"] is True
    assert captured["max_bytes"] >= 16_000


async def test_http_api_mixed_legacy_execute_keeps_httpx_post_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class _LegacyResponse:
        text = '{"accepted": true}'

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, bool]:
            return {"accepted": True}

    class _LegacyClient:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> None:
            del args

        async def post(self, url: str, **kwargs: Any) -> _LegacyResponse:
            calls.append({"method": "POST", "url": url, **kwargs})
            return _LegacyResponse()

        async def get(self, url: str, **kwargs: Any) -> _LegacyResponse:
            calls.append({"method": "GET", "url": url, **kwargs})
            return _LegacyResponse()

    monkeypatch.setattr(http_api_module, "_is_safe_url", lambda url: bool(url))
    monkeypatch.setattr(
        http_api_module.httpx,
        "AsyncClient",
        lambda **kwargs: _LegacyClient(),
    )

    result = await HttpApiTool().execute(
        ToolContext(user_id="10001"),
        method="POST",
        url="https://api.example.com/actions",
        headers={"X-Legacy": "kept"},
        body={"action": "preview"},
    )

    assert '"accepted": true' in result.lower()
    assert calls == [
        {
            "method": "POST",
            "url": "https://api.example.com/actions",
            "headers": {"X-Legacy": "kept"},
            "json": {"action": "preview"},
        }
    ]


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


async def test_web_search_auto_without_credential_uses_bounded_async_rss_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    captured: dict[str, object] = {}

    async def fake_fetch(
        url: str,
        *,
        timeout_seconds: float,
        follow_redirects: bool,
        headers: dict[str, str],
        max_bytes: int,
        allow_proxy_dns_net: bool = True,
    ) -> PublicHttpTextResponse:
        captured.update(
            url=url,
            timeout_seconds=timeout_seconds,
            follow_redirects=follow_redirects,
            headers=headers,
            max_bytes=max_bytes,
            allow_proxy_dns_net=allow_proxy_dns_net,
        )
        return PublicHttpTextResponse(
            status_code=200,
            final_url=url,
            truncated=False,
            text=(
                "<?xml version='1.0'?><rss><channel><item>"
                "<title>Async result</title><link>https://example.com/result</link>"
                "<description>Bounded provider</description>"
                "</item></channel></rss>"
            ),
    )

    monkeypatch.setattr(web_search_module, "fetch_public_text", fake_fetch, raising=False)

    result = await WebSearchTool(timeout_seconds=7).execute(
        ToolContext(run_id="governed-search"),
        query="async provider contract",
    )

    assert result == "1. Async result\n   https://example.com/result\n   Bounded provider"
    assert str(captured["url"]).startswith("https://cn.bing.com/search?")
    assert captured["timeout_seconds"] == 7
    assert captured["follow_redirects"] is False
    assert isinstance(captured["max_bytes"], int)
    assert captured["max_bytes"] >= 4096


async def test_web_search_auto_cancellation_reaches_async_rss_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocking_fetch(*_args: object, **_kwargs: object) -> PublicHttpTextResponse:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("blocked RSS request unexpectedly completed")

    monkeypatch.setattr(web_search_module, "fetch_public_text", blocking_fetch)
    task = asyncio.create_task(
        WebSearchTool().execute(
            ToolContext(run_id="governed-search-cancellation"),
            query="async cancellation contract",
        )
    )

    await asyncio.wait_for(entered.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_web_search_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_results = [
        {"title": "Result 1", "href": "https://example.com/1", "body": "Snippet 1"},
        {"title": "Result 2", "href": "https://example.com/2", "body": "Snippet 2"},
    ]
    monkeypatch.setattr(
        "services.tools.web_search._ddg_search_sync",
        lambda q, n: fake_results,
    )
    tool = WebSearchTool(mode="ddg")
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
    tool = WebSearchTool(mode="ddg")
    ctx = ToolContext(user_id="123")
    result = await tool.execute(ctx, query="nonexistent gibberish xyz")
    assert "未找到" in result


async def test_web_search_error_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error(q: str, n: int) -> list:
        raise RuntimeError("network error")

    monkeypatch.setattr("services.tools.web_search._ddg_search_sync", raise_error)
    tool = WebSearchTool(mode="ddg")
    ctx = ToolContext(user_id="123")
    result = await tool.execute(ctx, query="test")
    assert "搜索失败" in result


async def test_web_search_max_results_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    def capture_n(q: str, n: int) -> list:
        captured["n"] = n
        return []

    monkeypatch.setattr("services.tools.web_search._ddg_search_sync", capture_n)
    tool = WebSearchTool(mode="ddg")
    ctx = ToolContext(user_id="123")
    await tool.execute(ctx, query="test", max_results=99)
    assert captured["n"] == 10


async def test_web_search_max_results_has_a_positive_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, int] = {}

    def capture_n(q: str, n: int) -> list:
        del q
        captured["n"] = n
        return []

    monkeypatch.setattr("services.tools.web_search._ddg_search_sync", capture_n)
    await WebSearchTool(mode="ddg").execute(
        ToolContext(user_id="123"),
        query="test",
        max_results=-9,
    )

    assert captured["n"] == 1
