"""HTTP API 调用工具：通用 REST 接口调用。"""

import json
from collections.abc import Mapping
from typing import Any

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
from services.tools.safe_http import (
    MAX_PUBLIC_URL_LENGTH,
    PublicHttpTextResponse,
    UnsafePublicUrl,
    fetch_public_text,
    normalize_public_http_url,
)
from services.tools.web_fetch import _is_safe_url

MAX_RESPONSE_LENGTH = 4000
_SAFE_GET_HEADERS = frozenset({"Accept", "Accept-Language"})
_MAX_HEADER_VALUE_LENGTH = 512


class HttpApiTool(Tool):
    def __init__(
        self,
        *,
        timeout_seconds: float = 15,
        max_response_chars: int = MAX_RESPONSE_LENGTH,
        follow_redirects: bool = True,
        allowed_methods: list[str] | None = None,
    ) -> None:
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._max_response_chars = max(100, int(max_response_chars))
        self._follow_redirects = bool(follow_redirects)
        requested = (
            ["GET", "POST"] if allowed_methods is None else list(allowed_methods)
        )
        normalized = [str(item).upper() for item in requested]
        self._governed_get_only = bool(normalized) and set(normalized) == {"GET"}
        self._allowed_methods = list(
            dict.fromkeys(item for item in normalized if item in {"GET", "POST"})
        ) or ["GET"]

    @property
    def name(self) -> str:
        return "http_api"

    @property
    def description(self) -> str:
        if self._governed_get_only:
            return "调用外部 HTTP API，仅支持 GET 读取公开 JSON 或文本数据。"
        return "调用外部 HTTP API，支持 GET/POST。适合查询天气、翻译、汇率等第三方服务。"

    @property
    def parameters(self) -> dict[str, Any]:
        if self._governed_get_only:
            return {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "enum": ["GET"],
                        "description": "HTTP 方法",
                        "default": "GET",
                    },
                    "url": {
                        "type": "string",
                        "description": "API URL",
                        "maxLength": MAX_PUBLIC_URL_LENGTH,
                    },
                    "headers": {
                        "type": "object",
                        "description": "可选的内容协商请求头",
                        "properties": {
                            "Accept": {
                                "type": "string",
                                "maxLength": _MAX_HEADER_VALUE_LENGTH,
                            },
                            "Accept-Language": {
                                "type": "string",
                                "maxLength": _MAX_HEADER_VALUE_LENGTH,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            }
        return {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": self._allowed_methods,
                    "description": "HTTP 方法",
                    "default": self._allowed_methods[0],
                },
                "url": {"type": "string", "description": "API URL"},
                "headers": {"type": "object", "description": "请求头", "additionalProperties": {"type": "string"}},
                "body": {"type": "object", "description": "POST 请求体（JSON）"},
            },
            "required": ["url"],
        }

    @property
    def spec(self) -> ToolSpec:
        if not self._governed_get_only:
            return super().spec
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=dict(self.parameters),
            owner="http_api",
            effect=ToolEffect.EXTERNAL_READ,
            required_scopes=("network:http-api:read",),
            approval=ToolApproval.NEVER,
            idempotency=ToolIdempotency.NOT_NEEDED,
            retry_policy=ToolRetryPolicy.SAFE_TRANSIENT,
            concurrency=ToolConcurrency.PARALLEL,
            timeout_ms=max(1, int(self._timeout_seconds * 1000)),
            binding_required=True,
            data_classification=("public_api",),
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        del ctx
        if not self._governed_get_only:
            return ToolInvocationBinding()
        method = str(arguments.get("method") or "GET").upper()
        if method != "GET" or "body" in arguments:
            raise ValueError("governed http_api only accepts GET")
        self._validated_get_headers(arguments.get("headers"))
        try:
            normalized = normalize_public_http_url(
                str(arguments.get("url") or "")
            )
        except UnsafePublicUrl as exc:
            raise ValueError("http_api GET requires a public HTTP URL") from exc
        return ToolInvocationBinding(
            target_ref=f"network:http-api-origin:{normalized.origin}"
        )

    @staticmethod
    def _validated_get_headers(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("http_api GET headers must be an object")
        headers: dict[str, str] = {}
        for raw_name, raw_value in value.items():
            name = str(raw_name)
            if name not in _SAFE_GET_HEADERS or not isinstance(raw_value, str):
                raise ValueError("http_api GET header is not allowed")
            if (
                len(raw_value) > _MAX_HEADER_VALUE_LENGTH
                or any(
                    ord(char) < 32 or 127 <= ord(char) <= 159
                    for char in raw_value
                )
            ):
                raise ValueError("http_api GET header value is invalid")
            headers[name] = raw_value
        return headers

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        if self._governed_get_only:
            return await self._execute_public_get(ctx, kwargs)

        url: str = kwargs["url"]
        if not _is_safe_url(url):
            return "拒绝访问: 不允许访问内网地址"

        method: str = str(kwargs.get("method", self._allowed_methods[0])).upper()
        if method not in self._allowed_methods:
            return f"不支持的 HTTP 方法: {method}"
        headers: dict[str, str] = kwargs.get("headers", {})
        body: dict[str, Any] | None = kwargs.get("body")

        async with httpx.AsyncClient(timeout=self._timeout_seconds, follow_redirects=self._follow_redirects) as client:
            if method == "POST":
                resp = await client.post(url, headers=headers, json=body)
            else:
                resp = await client.get(url, headers=headers)
            resp.raise_for_status()

        try:
            data = resp.json()
            text = json.dumps(data, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, ValueError):
            text = resp.text

        if len(text) > self._max_response_chars:
            text = text[:self._max_response_chars] + "...(已截断)"
        return text

    async def _execute_public_get(
        self,
        ctx: ToolContext,
        kwargs: Mapping[str, Any],
    ) -> str:
        url = str(kwargs["url"])
        governed = bool(ctx.run_id.strip())
        method = str(kwargs.get("method") or "GET").upper()
        if method != "GET" or "body" in kwargs:
            if governed:
                raise ValueError("governed http_api only accepts GET")
            return f"不支持的 HTTP 方法: {method}"
        try:
            headers = self._validated_get_headers(kwargs.get("headers"))
        except ValueError:
            if governed:
                raise
            return "拒绝访问: 请求头不在允许列表"

        try:
            response: PublicHttpTextResponse = await fetch_public_text(
                url,
                timeout_seconds=self._timeout_seconds,
                follow_redirects=self._follow_redirects,
                headers=headers,
                max_bytes=max(4096, self._max_response_chars * 4),
            )
        except UnsafePublicUrl:
            if governed:
                raise
            return "拒绝访问: 不允许访问非公网或跨源地址"
        except TimeoutError:
            if governed:
                raise
            return f"请求超时: API 在 {self._timeout_seconds:g} 秒内未响应 ({url})"
        except aiohttp.ClientConnectorError as exc:
            if governed:
                raise TimeoutError("http_api GET connection failed") from exc
            return f"连接失败: 无法连接到 API ({url})"
        except aiohttp.ClientError as exc:
            if governed:
                if isinstance(
                    exc,
                    (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError),
                ):
                    raise TimeoutError("http_api GET transport failed") from exc
                raise
            return f"请求失败: {type(exc).__name__} ({url})"

        if response.status_code >= 400:
            if governed:
                if response.status_code in {408, 425, 429} or response.status_code >= 500:
                    raise TimeoutError(
                        f"http_api GET retryable HTTP {response.status_code}"
                    )
                raise RuntimeError(
                    f"http_api GET terminal HTTP {response.status_code}"
                )
            return f"请求失败: HTTP {response.status_code} ({url})"

        try:
            data = json.loads(response.text)
            text = json.dumps(data, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, ValueError):
            text = response.text

        truncated = response.truncated or len(text) > self._max_response_chars
        text = text[: self._max_response_chars]
        if truncated:
            text += "...(已截断)"
        return text
