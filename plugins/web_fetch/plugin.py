"""WebFetchPlugin: 网页抓取工具。"""

from __future__ import annotations

from pydantic import BaseModel

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin
from services.tools.base import Tool
from services.tools.web_fetch import WebFetchTool


class WebFetchConfig(BaseModel):
    """网页抓取工具配置。"""

    timeout_seconds: float = 15
    max_length: int = 4000
    follow_redirects: bool = True
    user_agent: str = "Mozilla/5.0 QQBot/1.0"
    allow_proxy_dns_net: bool = True


class WebFetchPlugin(AmadeusPlugin):
    name = "web_fetch"
    description = "网页抓取工具：抓取指定 URL 内容并返回纯文本"
    version = "1.1.1"
    priority = 1

    def register_tools(self) -> list[Tool]:
        cfg = load_plugin_config("plugins/web_fetch/config.default.json", WebFetchConfig)
        return [
            WebFetchTool(
                timeout_seconds=cfg.timeout_seconds,
                max_length=cfg.max_length,
                follow_redirects=cfg.follow_redirects,
                user_agent=cfg.user_agent,
                allow_proxy_dns_net=cfg.allow_proxy_dns_net,
            )
        ]
