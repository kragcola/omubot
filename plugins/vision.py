"""VisionPlugin: 视觉客户端（Qwen VL）。

创建 VisionClient 用于图片描述，供 StickerPlugin 的 DescribeImageTool 使用。
"""

from __future__ import annotations

import base64
import json
from typing import Any

import aiohttp
from loguru import logger

from kernel.types import AmadeusPlugin, PluginContext

_L = logger.bind(channel="system")

_STICKER_DESCRIBE_PROMPT = (
    "请用一句简短的中文描述这张图片/表情包：它展示了什么内容、传达了什么样的情绪或态度、"
    "适合在什么聊天场景中使用。描述要像真人随手发的表情包说明，不要用学术语言。"
    "如果是表情包/梗图，重点抓住它的'用法'——什么情况下发这个。"
    "如果是普通图片，简要描述画面内容即可。"
)


class VisionClient:
    """Calls Qwen VL model (OpenAI-compatible API) to describe images."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    async def describe_image(
        self,
        image_data: bytes,
        media_type: str = "image/jpeg",
        prompt: str | None = None,
    ) -> str | None:
        """Send an image to Qwen VL and return a short description.

        Returns None on any failure (network, API error, unexpected response).
        """
        b64 = base64.b64encode(image_data).decode()
        data_url = f"data:{media_type};base64,{b64}"

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt or _STICKER_DESCRIBE_PROMPT},
                    ],
                }
            ],
            "max_tokens": 128,
            "temperature": 0.3,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self._base_url}/chat/completions"

        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout_s)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(url, json=body, headers=headers) as resp,
            ):
                    if resp.status >= 400:
                        body_text = await resp.text()
                        logger.error(
                            "Qwen VL {} | body={}", resp.status, body_text[:300]
                        )
                        return None
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as e:
            logger.warning("Qwen VL request failed: {}", e)
            return None

        try:
            desc = data["choices"][0]["message"]["content"]
            return desc.strip()
        except (KeyError, IndexError, TypeError) as e:
            logger.warning("Qwen VL unexpected response format: {}", e)
            return None


class VisionPlugin(AmadeusPlugin):
    name = "vision"
    description = "视觉客户端：Qwen VL 图片描述"
    version = "1.0.0"
    priority = 8  # Before StickerPlugin (40) which uses it

    async def on_startup(self, ctx: PluginContext) -> None:
        config = ctx.config
        if not config.vision.qwen.enabled or not config.vision.qwen.api_key:
            _L.info("Qwen VL vision disabled, skipping")
            ctx.vision_client = None
            return

        ctx.vision_client = VisionClient(
            base_url=config.vision.qwen.base_url,
            api_key=config.vision.qwen.api_key,
            model=config.vision.qwen.model,
            timeout_s=30.0,
        )
        _L.info(
            "Qwen VL vision client initialized | model={} base_url={}",
            config.vision.qwen.model,
            config.vision.qwen.base_url,
        )
