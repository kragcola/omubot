"""网页抓取工具：获取 URL 内容。"""

import ipaddress
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from services.tools.base import Tool
from services.tools.context import ToolContext

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
MAX_LENGTH = 4000


def _is_safe_url(url: str, *, allow_proxy_dns_net: bool = True) -> bool:
    """拒绝内网/本机地址，防止 SSRF。"""
    import socket
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if not hostname:
            return False
        if hostname in ("localhost", "host.docker.internal", "napcat"):
            return False
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


# 198.18.0.0/15: 本地 DNS 代理（如 Clash）将公网域名解析到此段
_PROXY_DNS_NET = ipaddress.ip_network("198.18.0.0/15")


def _is_allowed_addr(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_proxy_dns_net: bool = True,
) -> bool:
    """判断地址是否允许访问：全局地址或代理 DNS 段。"""
    if addr.is_global:
        return True
    return bool(allow_proxy_dns_net and isinstance(addr, ipaddress.IPv4Address) and addr in _PROXY_DNS_NET)


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
                "url": {"type": "string", "description": "要抓取的网页 URL"},
            },
            "required": ["url"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        url: str = kwargs["url"]
        if not _is_safe_url(url, allow_proxy_dns_net=self._allow_proxy_dns_net):
            return "拒绝访问: 不允许访问内网地址"

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds, follow_redirects=self._follow_redirects) as client:
                resp = await client.get(url, headers={"User-Agent": self._user_agent})
        except httpx.TimeoutException:
            return f"请求超时: 网页在 {self._timeout_seconds:g} 秒内未响应 ({url})"
        except httpx.ConnectError:
            return f"连接失败: 无法连接到服务器 ({url})"
        except httpx.HTTPError as e:
            return f"请求失败: {type(e).__name__} ({url})"

        if resp.status_code >= 400:
            return f"请求失败: HTTP {resp.status_code} ({url})"

        text = _SCRIPT_STYLE_RE.sub(" ", resp.text)
        text = _TAG_RE.sub(" ", text)
        text = _SPACE_RE.sub(" ", text).strip()

        if len(text) > self._max_length:
            text = text[:self._max_length] + "...(已截断)"
        return text
