"""Web search tool: Bing Web Search API (primary) + DuckDuckGo (fallback).

Set SEARCH_API_KEY env var to a Bing Web Search API key from Azure.
Without it, falls back to DuckDuckGo (may return empty results from datacenter IPs).
"""

from __future__ import annotations

import os
import warnings
from typing import Any

import httpx

from services.tools.base import Tool
from services.tools.context import ToolContext

MAX_RESULTS = 5
BING_API = "https://api.bing.microsoft.com/v7.0/search"


class WebSearchTool(Tool):
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
                    "description": "返回结果数量，默认 5，最多 10",
                },
            },
            "required": ["query"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        query: str = kwargs["query"]
        max_results = min(int(kwargs.get("max_results", MAX_RESULTS)), 10)
        api_key = os.environ.get("SEARCH_API_KEY", "")

        if api_key:
            return await _bing_search(query, max_results, api_key)
        else:
            return await _ddg_search(query, max_results)


async def _bing_search(query: str, max_results: int, api_key: str) -> str:
    """Search via Bing Web Search API."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        try:
            resp = await client.get(
                BING_API,
                params={"q": query, "count": max_results, "mkt": "zh-CN"},
                headers={"Ocp-Apim-Subscription-Key": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            return f"Bing 搜索失败: {e}"

    pages = (data.get("webPages") or {}).get("value") or []
    if not pages:
        return "未找到相关结果。"

    lines: list[str] = []
    for i, p in enumerate(pages, 1):
        lines.append(f"{i}. {p['name']}\n   {p['url']}\n   {p.get('snippet', '')}")
    return "\n\n".join(lines)


async def _ddg_search(query: str, max_results: int) -> str:
    """Search via DuckDuckGo (fallback, may not work from datacenter IPs)."""
    import asyncio

    try:
        results = await asyncio.to_thread(_ddg_search_sync, query, max_results)
    except Exception as e:
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
