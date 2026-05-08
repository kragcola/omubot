"""HttpApiPlugin: HTTP API 调用工具。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin
from services.tools.base import Tool
from services.tools.http_api import HttpApiTool


class HttpApiConfig(BaseModel):
    """HTTP API 工具配置。"""

    timeout_seconds: float = 15
    max_response_chars: int = 4000
    follow_redirects: bool = True
    allowed_methods: list[str] = Field(default_factory=lambda: ["GET", "POST"])


class HttpApiPlugin(AmadeusPlugin):
    name = "http_api"
    description = "HTTP API 调用工具：支持 GET/POST 通用 REST 接口调用"
    version = "1.1.1"
    priority = 1

    def register_tools(self) -> list[Tool]:
        cfg = load_plugin_config("plugins/http_api/config.default.json", HttpApiConfig)
        return [
            HttpApiTool(
                timeout_seconds=cfg.timeout_seconds,
                max_response_chars=cfg.max_response_chars,
                follow_redirects=cfg.follow_redirects,
                allowed_methods=cfg.allowed_methods,
            )
        ]
