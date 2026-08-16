"""Web search tool: Bing API (credentialed) plus bounded public RSS fallback.

Set SEARCH_API_KEY env var to a Bing Web Search API key from Azure.
Without it, ``auto`` uses Bing's public RSS endpoint through the shared async
public-HTTP boundary.  The explicit DuckDuckGo compatibility mode remains
available for legacy plugin configuration.
"""

from __future__ import annotations

import html
import os
import re
import warnings
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree

import aiohttp
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
from services.tools.safe_http import UnsafePublicUrl, fetch_public_text

MAX_RESULTS = 5
BING_API = "https://api.bing.microsoft.com/v7.0/search"
BING_RSS_SEARCH = "https://cn.bing.com/search"
_BING_RSS_MAX_BYTES = 64 * 1024
_SEARCH_USER_AGENT = "Mozilla/5.0 QQBot/1.0"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


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
        return await _bing_rss_search(
            query,
            max_results,
            market=self._bing_market,
            timeout_seconds=self._timeout_seconds,
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
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        trust_env=False,
    ) as client:
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
    return _format_results(
        [
            {
                "title": str(page.get("name") or ""),
                "href": str(page.get("url") or ""),
                "body": str(page.get("snippet") or ""),
            }
            for page in pages
        ]
    )


async def _bing_rss_search(
    query: str,
    max_results: int,
    *,
    market: str = "zh-CN",
    timeout_seconds: float = 15,
    propagate_errors: bool = False,
) -> str:
    """Search a fixed public Bing RSS endpoint with cancellable async I/O."""
    url = f"{BING_RSS_SEARCH}?{urlencode({'format': 'rss', 'q': query, 'mkt': market})}"
    try:
        response = await fetch_public_text(
            url,
            timeout_seconds=timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": _SEARCH_USER_AGENT},
            max_bytes=_BING_RSS_MAX_BYTES,
        )
    except (TimeoutError, aiohttp.ClientError, UnsafePublicUrl) as exc:
        if propagate_errors:
            raise TimeoutError("web_search provider failed") from exc
        return f"Bing 搜索失败: {type(exc).__name__}"

    if response.status_code < 200 or response.status_code >= 300:
        if propagate_errors:
            if response.status_code in {408, 425, 429} or response.status_code >= 500:
                raise TimeoutError(
                    f"web_search retryable HTTP {response.status_code}"
                )
            raise RuntimeError(f"web_search terminal HTTP {response.status_code}")
        return f"Bing 搜索失败: HTTP {response.status_code}"

    try:
        results = _parse_bing_rss_results(response.text, max_results=max_results)
    except ElementTree.ParseError as exc:
        if propagate_errors:
            raise TimeoutError("web_search provider returned invalid RSS") from exc
        return "Bing 搜索失败: 无法解析搜索结果。"
    return _format_results(results)


def _parse_bing_rss_results(text: str, *, max_results: int) -> list[dict[str, str]]:
    root = ElementTree.fromstring(text)
    results: list[dict[str, str]] = []
    for item in root.iter():
        if _local_name(item.tag) != "item":
            continue
        fields = {
            _local_name(child.tag): _clean_result_text("".join(child.itertext()))
            for child in item
        }
        title = fields.get("title", "")
        href = fields.get("link", "")
        if not title or not href:
            continue
        results.append(
            {
                "title": title,
                "href": href,
                "body": fields.get("description", ""),
            }
        )
        if len(results) >= max_results:
            break
    return results


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_result_text(value: str) -> str:
    return _SPACE_RE.sub(" ", _HTML_TAG_RE.sub(" ", html.unescape(value))).strip()


def _format_results(results: list[dict[str, str]]) -> str:
    if not results:
        return "未找到相关结果。"
    return "\n\n".join(
        f"{index}. {result['title']}\n   {result['href']}\n   {result['body']}"
        for index, result in enumerate(results, 1)
    )


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

    return _format_results(results)


def _ddg_search_sync(query: str, max_results: int) -> list[dict[str, str]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        from ddgs import DDGS

        d = DDGS()
    return d.text(query, max_results=max_results)  # type: ignore[return-value]
