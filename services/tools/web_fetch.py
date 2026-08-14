"""网页抓取工具：获取 URL 内容。"""

import ipaddress
import re
from collections.abc import Mapping
from typing import Any

import aiohttp

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
from services.tools.safe_http import (
    MAX_PUBLIC_URL_LENGTH,
    PublicHttpTextResponse,
    UnsafePublicUrl,
    fetch_public_text,
    is_allowed_public_address,
    normalize_public_http_url,
)

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
MAX_LENGTH = 4000


def _is_safe_url(url: str, *, allow_proxy_dns_net: bool = True) -> bool:
    """拒绝内网/本机地址，防止 SSRF。"""
    import socket
    try:
        normalized = normalize_public_http_url(
            url,
            allow_proxy_dns_net=allow_proxy_dns_net,
        )
        hostname = normalized.hostname
        # 解析域名，检查所有 IP 是否为公网地址
        try:
            addrinfos = socket.getaddrinfo(hostname, None)
            for _, _, _, _, sockaddr in addrinfos:
                addr = ipaddress.ip_address(sockaddr[0])
                if not _is_allowed_addr(addr, allow_proxy_dns_net=allow_proxy_dns_net):
                    return False
            return bool(addrinfos)
        except socket.gaierror:
            return False
    except Exception:
        return False


def _is_allowed_addr(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_proxy_dns_net: bool = True,
) -> bool:
    """判断地址是否允许访问：全局地址或代理 DNS 段。"""
    return is_allowed_public_address(
        addr,
        allow_proxy_dns_net=allow_proxy_dns_net,
    )


class WebFetchTool(Tool):
    def __init__(
        self,
        *,
        timeout_seconds: float = 15,
        max_length: int = MAX_LENGTH,
        follow_redirects: bool = True,
        user_agent: str = "Mozilla/5.0 QQBot/1.0",
        allow_proxy_dns_net: bool = True,
    ) -> None:
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._max_length = max(100, int(max_length))
        self._follow_redirects = bool(follow_redirects)
        self._user_agent = user_agent or "Mozilla/5.0 QQBot/1.0"
        self._allow_proxy_dns_net = bool(allow_proxy_dns_net)

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "抓取指定 URL 的网页内容，返回纯文本。适合查询在线信息、文档、新闻等。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的网页 URL",
                    "maxLength": MAX_PUBLIC_URL_LENGTH,
                },
            },
            "required": ["url"],
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=dict(self.parameters),
            owner="web_fetch",
            effect=ToolEffect.EXTERNAL_READ,
            required_scopes=("network:fetch",),
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
        del ctx
        try:
            normalized = normalize_public_http_url(
                str(arguments.get("url") or ""),
                allow_proxy_dns_net=self._allow_proxy_dns_net,
            )
        except UnsafePublicUrl as exc:
            raise ValueError("web_fetch requires a public HTTP URL") from exc
        return ToolInvocationBinding(
            target_ref=f"network:web-origin:{normalized.origin}"
        )

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        url: str = kwargs["url"]
        governed = bool(ctx.run_id.strip())

        try:
            response: PublicHttpTextResponse = await fetch_public_text(
                url,
                timeout_seconds=self._timeout_seconds,
                follow_redirects=self._follow_redirects,
                headers={"User-Agent": self._user_agent},
                max_bytes=max(4096, self._max_length * 4),
                allow_proxy_dns_net=self._allow_proxy_dns_net,
            )
        except UnsafePublicUrl:
            if governed:
                raise
            return "拒绝访问: 不允许访问非公网或跨源地址"
        except TimeoutError:
            if governed:
                raise
            return f"请求超时: 网页在 {self._timeout_seconds:g} 秒内未响应 ({url})"
        except aiohttp.ClientConnectorError as exc:
            if governed:
                raise TimeoutError("web_fetch connection failed") from exc
            return f"连接失败: 无法连接到服务器 ({url})"
        except aiohttp.ClientError as e:
            if governed:
                if isinstance(
                    e,
                    (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError),
                ):
                    raise TimeoutError("web_fetch transport failed") from e
                raise
            return f"请求失败: {type(e).__name__} ({url})"

        if response.status_code >= 400:
            if governed:
                if response.status_code in {408, 425, 429} or response.status_code >= 500:
                    raise TimeoutError(
                        f"web_fetch retryable HTTP {response.status_code}"
                    )
                raise RuntimeError(f"web_fetch terminal HTTP {response.status_code}")
            return f"请求失败: HTTP {response.status_code} ({url})"

        text = _SCRIPT_STYLE_RE.sub(" ", response.text)
        text = _TAG_RE.sub(" ", text)
        text = _SPACE_RE.sub(" ", text).strip()

        truncated = response.truncated or len(text) > self._max_length
        text = text[:self._max_length]
        if truncated:
            text += "...(已截断)"
        return text
