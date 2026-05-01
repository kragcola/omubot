"""StickerPlugin: 表情包工具与 prompt 注入。

通过 on_pre_prompt 注入表情包使用规则和表情包库视图到 system prompt。
"""

from __future__ import annotations

from kernel.types import AmadeusPlugin, PluginContext, PromptContext
from services.tools.base import Tool

_STICKER_FREQUENCY_PROMPTS: dict[str, str] = {
    "rarely": (
        "【表情包规则 — 颜文字强制配图】\n"
        "硬性规则：如果你的回复中使用了颜文字（如 (≧▽≦)/ (*^▽^*) (｡･ω･｡) (╥﹏╥) 等）"
        "或括号动作描写（如（笑）（叹气）（心虚地挠挠脸）），"
        "必须同时调用 send_sticker 发送一个表情包。这不是可选的——颜文字=表情包。\n\n"
        "【表情包触发模式：逐条评估】\n"
        "对于没有颜文字的消息，按以下标准逐条判断：\n"
        "评分标准（0-10）：\n"
        "  你的消息表达了情绪（开心/惊讶/感动/无语/得意/害羞/生气/委屈）+3~5\n"
        "  你的消息有强烈的语气（撒娇/吐槽/感叹/嘲讽/欢呼）+2~4\n"
        "  当前话题轻松日常（不是严肃讨论或技术问题）+1~2\n"
        "  刚发过表情包（上一条或上上条） -3\n"
        "总评分 ≥ 8 时才额外调用 send_sticker，不要为发而发。"
    ),
    "normal": (
        "【表情包规则 — 颜文字强制配图】\n"
        "硬性规则：如果你的回复中使用了颜文字（如 (≧▽≦)/ (*^▽^*) (｡･ω･｡) (╥﹏╥) 等）"
        "或括号动作描写（如（笑）（叹气）（心虚地挠挠脸）），"
        "必须同时调用 send_sticker 发送一个表情包。这不是可选的——颜文字=表情包。\n\n"
        "【表情包触发模式：逐条评估】\n"
        "对于没有颜文字的消息，按以下标准逐条判断：\n"
        "评分标准（0-10）：\n"
        "  你的消息表达了情绪（开心/惊讶/感动/无语/得意/害羞/生气/委屈）+3~5\n"
        "  你的消息有强烈的语气（撒娇/吐槽/感叹/嘲讽/欢呼）+2~4\n"
        "  当前话题轻松日常（不是严肃讨论或技术问题）+1~2\n"
        "  刚发过表情包（上一条或上上条） -2\n"
        "总评分 ≥ 6 时额外调用 send_sticker，觉得合适就发。"
    ),
    "frequently": (
        "【表情包规则 — 每条消息默认配图】\n"
        "你是{name}，表情包就是你的第二语言！\n\n"
        "默认规则：每次回复都必须调用 send_sticker 发送一个表情包。\n"
        "像说话要带语气一样自然——回复和表情包是一体的。\n\n"
        "只有一种情况可以不发：你的回复是纯技术解答或严肃事实陈述。\n"
        "其他所有情况——闲聊、吐槽、感叹、打招呼、接话、撒娇——统统要发！\n\n"
        "颜文字强制规则：如果用了颜文字（(≧▽≦)/ 等）或动作描写（（笑）等），\n"
        "必须同时调用 send_sticker。不发就是事故，没有例外。\n\n"
        "不要想太多，看到合适的就发，看不到也随便挑一个发。\n"
        "发了再说——表情包永远不嫌多，发错了也比不发强！"
    ),
}


class StickerPlugin(AmadeusPlugin):
    name = "sticker"
    description = "表情包工具：保存、发送、管理表情包及图片描述"
    version = "1.0.1"
    priority = 40

    def __init__(self) -> None:
        super().__init__()
        self._sticker_store = None
        self._vision_client = None
        self._image_cache = None
        self._superusers: set[str] = set()
        self._sticker_frequency: str = "normal"

    async def on_startup(self, ctx: PluginContext) -> None:
        import nonebot
        self._sticker_store = ctx.sticker_store
        self._vision_client = ctx.vision_client
        self._image_cache = ctx.image_cache
        self._superusers = set(ctx.config.admins.keys()) | nonebot.get_driver().config.superusers
        self._sticker_frequency = ctx.config.sticker.frequency

    def register_tools(self) -> list[Tool]:
        if self._sticker_store is None:
            return []
        from services.tools.sticker_tools import (
            DescribeImageTool,
            ManageStickerTool,
            SaveStickerTool,
            SendStickerTool,
        )
        tools: list[Tool] = [
            SaveStickerTool(self._sticker_store, self._superusers),
            SendStickerTool(self._sticker_store),
            ManageStickerTool(self._sticker_store, self._superusers),
        ]
        if self._vision_client is not None and self._image_cache is not None:
            tools.append(DescribeImageTool(self._vision_client, self._image_cache))
        return tools

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        # Sticker frequency prompt (static — part of personality)
        freq_prompt = _STICKER_FREQUENCY_PROMPTS.get(self._sticker_frequency)
        if freq_prompt and self._sticker_store is not None:
            formatted = freq_prompt.format(name=ctx.identity.name)
            ctx.add_block(text=formatted, label="表情包规则", position="static")

        # Sticker library view (stable — changes when stickers added/removed)
        if self._sticker_store is not None:
            view = self._sticker_store.format_prompt_view()
            if view:
                ctx.add_block(text=view, label="表情包库", position="stable")
