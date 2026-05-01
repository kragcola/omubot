"""VisionPlugin: 视觉客户端（Qwen VL）。

创建 VisionClient 用于图片描述，供 StickerPlugin 的 DescribeImageTool 使用。
"""

from __future__ import annotations

from loguru import logger

from kernel.types import AmadeusPlugin, PluginContext

_L = logger.bind(channel="system")


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

        from plugins.vision.client import VisionClient

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
