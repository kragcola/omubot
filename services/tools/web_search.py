"""Web search tool: Bing Web Search API (primary) + DuckDuckGo (fallback).

Set SEARCH_API_KEY env var to a Bing Web Search API key from Azure.
Without it, falls back to DuckDuckGo (may return empty results from datacenter IPs).
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Mapping
from typing import Any

import httpx

from kernel.types import (
    ToolApproval,
    ToolConcurrency,
    ToolEffect,
    ToolIdempotency,
    ToolInvocationBinding,
    ToolRetryPolicy,
    ToolSpec,
)
from services.tools.base import Tool
from services.tools.context import ToolContext

MAX_RESULTS = 5
BING_API = "https://api.bing.microsoft.com/v7.0/search"


class WebSearchTool(Tool):
    def __init__(
        self,
        *,
        default_results: int = MAX_RESULTS,
        max_results: int = 10,
        mode: str = "auto",
        bing_market: str = "zh-CN",
        timeout_seconds: float = 15,
    ) -> None:
        self._default_results = max(1, int(default_results))
        self._max_results = max(1, int(max_results))
        self._mode = mode if mode in {"auto", "bing", "ddg"} else "auto"
        self._bing_market = bing_market or "zh-CN"
        self._timeout_seconds = max(1.0, float(timeout_seconds))

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "使用搜索引擎搜索互联网，返回相关网页的标题、链接和摘要。"
            "适合查询实时信息、新闻、技术文档等。"
            "如果需要查看某个结果的完整内容，可以用 web_fetch 抓取对应链接。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {
                    "type": "integer",
                    "description": f"返回结果数量，默认 {self._default_results}，最多 {self._max_results}",
                    "minimum": 1,
                    "maximum": self._max_results,
                },
            },
            "required": ["query"],
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=dict(self.parameters),
            owner="web_search",
            effect=ToolEffect.EXTERNAL_READ,
            required_scopes=("network:search",),
            approval=ToolApproval.NEVER,
            idempotency=ToolIdempotency.NOT_NEEDED,
            retry_policy=ToolRetryPolicy.SAFE_TRANSIENT,
            concurrency=ToolConcurrency.PARALLEL,
            timeout_ms=max(1, int(self._timeout_seconds * 1000)),
            binding_required=True,
            data_classification=("public_web",),
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        del ctx, arguments
        return ToolInvocationBinding(target_ref="network:web-search")

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        query: str = kwargs["query"]
        max_results = max(
            1,
            min(
                int(kwargs.get("max_results", self._default_results)),
                self._max_results,
            ),
        )
        api_key = os.environ.get("SEARCH_API_KEY", "")
        governed = bool(ctx.run_id.strip())

        if self._mode == "bing":
            if not api_key:
                if governed:
                    raise RuntimeError("web_search credential unavailable")
                return "Bing 搜索未配置 SEARCH_API_KEY。"
            return await _bing_search(
                query,
                max_results,
                api_key,
                market=self._bing_market,
                timeout_seconds=self._timeout_seconds,
                propagate_errors=governed,
            )
        if self._mode == "ddg":
            return await _ddg_search(
                query,
                max_results,
                propagate_errors=governed,
            )
        if api_key:
            return await _bing_search(
                query,
                max_results,
                api_key,
                market=self._bing_market,
                timeout_seconds=self._timeout_seconds,
                propagate_errors=governed,
            )
        return await _ddg_search(
            query,
            max_results,
            propagate_errors=governed,
        )


async def _bing_search(
    query: str,
    max_results: int,
    api_key: str,
    *,
    market: str = "zh-CN",
    timeout_seconds: float = 15,
    propagate_errors: bool = False,
) -> str:
    """Search via Bing Web Search API."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
        try:
            resp = await client.get(
                BING_API,
                params={"q": query, "count": max_results, "mkt": market},
                headers={"Ocp-Apim-Subscription-Key": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            if propagate_errors:
                if isinstance(e, httpx.HTTPStatusError):
                    status = e.response.status_code
                    if status not in {408, 425, 429} and status < 500:
                        raise RuntimeError(
                            f"web_search terminal HTTP {status}"
                        ) from e
                raise TimeoutError("web_search provider failed") from e
            return f"Bing 搜索失败: {e}"

    pages = (data.get("webPages") or {}).get("value") or []
    if not pages:
        return "未找到相关结果。"

    lines: list[str] = []
    for i, p in enumerate(pages, 1):
        lines.append(f"{i}. {p['name']}\n   {p['url']}\n   {p.get('snippet', '')}")
    return "\n\n".join(lines)


async def _ddg_search(
    query: str,
    max_results: int,
    *,
    propagate_errors: bool = False,
) -> str:
    """Search via DuckDuckGo (fallback, may not work from datacenter IPs)."""
    import asyncio

    try:
        results = await asyncio.to_thread(_ddg_search_sync, query, max_results)
    except Exception as e:
        if propagate_errors:
            raise TimeoutError("web_search provider failed") from e
        return f"搜索失败: {e}"

    if not results:
        return "未找到相关结果。"

    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['href']}\n   {r['body']}")
    return "\n\n".join(lines)


def _ddg_search_sync(query: str, max_results: int) -> list[dict[str, str]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        from ddgs import DDGS

        d = DDGS()
    return d.text(query, max_results=max_results)  # type: ignore[return-value]
