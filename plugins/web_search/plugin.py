"""WebSearchPlugin: DuckDuckGo 网页搜索工具。"""

from __future__ import annotations

from pydantic import BaseModel

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin
from services.tools.base import Tool
from services.tools.web_search import WebSearchTool


class WebSearchConfig(BaseModel):
    """网页搜索工具配置。"""

    default_results: int = 5
    max_results: int = 10
    mode: str = "auto"
    bing_market: str = "zh-CN"
    timeout_seconds: float = 15


class WebSearchPlugin(AmadeusPlugin):
    name = "web_search"
    description = "网页搜索工具：通过 DuckDuckGo 搜索互联网，返回标题、链接和摘要"
    version = "1.1.1"
    priority = 1

    def register_tools(self) -> list[Tool]:
        cfg = load_plugin_config("plugins/web_search/config.default.json", WebSearchConfig)
        return [
            WebSearchTool(
                default_results=cfg.default_results,
                max_results=cfg.max_results,
                mode=cfg.mode,
                bing_market=cfg.bing_market,
                timeout_seconds=cfg.timeout_seconds,
            )
        ]
