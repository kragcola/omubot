"""HttpApiPlugin: HTTP API 调用工具。"""

from __future__ import annotations

from kernel.types import AmadeusPlugin
from services.tools.base import Tool
from services.tools.http_api import HttpApiTool


class HttpApiPlugin(AmadeusPlugin):
    name = "http_api"
    description = "HTTP API 调用工具：支持 GET/POST 通用 REST 接口调用"
    version = "1.1.0"
    priority = 1

    def register_tools(self) -> list[Tool]:
        return [HttpApiTool()]
