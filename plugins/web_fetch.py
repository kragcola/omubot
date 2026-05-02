"""WebFetchPlugin: 网页抓取工具。"""

from __future__ import annotations

from kernel.types import AmadeusPlugin
from services.tools.base import Tool
from services.tools.web_fetch import WebFetchTool


class WebFetchPlugin(AmadeusPlugin):
    name = "web_fetch"
    description = "网页抓取工具：抓取指定 URL 内容并返回纯文本"
    version = "1.1.0"
    priority = 1

    def register_tools(self) -> list[Tool]:
        return [WebFetchTool()]
