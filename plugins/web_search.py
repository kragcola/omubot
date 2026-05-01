"""WebSearchPlugin: DuckDuckGo 网页搜索工具。"""

from __future__ import annotations

from kernel.types import AmadeusPlugin
from services.tools.base import Tool
from services.tools.web_search import WebSearchTool


class WebSearchPlugin(AmadeusPlugin):
    name = "web_search"
    description = "网页搜索工具：通过 DuckDuckGo 搜索互联网，返回标题、链接和摘要"
    version = "1.0.0"
    priority = 1

    def register_tools(self) -> list[Tool]:
        return [WebSearchTool()]
