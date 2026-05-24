"""StickerPlugin: 表情包工具与 prompt 注入。

通过 on_pre_prompt 注入表情包使用规则和表情包库视图到 system prompt。
silent_learn 模式下通过 on_message 静默吸纳群友常用表情。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel

from kernel.types import AmadeusPlugin, MessageContext, PluginContext, PromptContext
from services.media.sticker_capture import (
    DEFAULT_STICKER_USAGE_HINT,
    is_sticker_like_segment,
    segment_value,
    sticker_description_from_segment,
)
from services.tools.base import Tool

_L = logger.bind(channel="system")
_SILENT_STEAL_MAX_IMAGES = 2
_MAX_PENDING_RETRIES = 100


@dataclass(frozen=True)
class _PendingStickerRetry:
    key: tuple[str, str, str]
    group_id: str
    message_id: str
    file_id: str
    segment: dict[str, Any]


class StickerConfig(BaseModel):
    """表情包系统配置。"""

    enabled: bool = True
    storage_dir: str = "storage/stickers"
    max_count: int = 200
    frequency: str = "frequently"

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
    version = "1.1.6"
    priority = 40
    silent_safe = True
    """on_message 只读取消息 + 写表情库，不发消息也不改 trigger，可在 silent_learn 群运行。"""

    def __init__(self) -> None:
        super().__init__()
        self._sticker_store = None
        self._vision_client = None
        self._image_cache = None
        self._superusers: set[str] = set()
        self._sticker_frequency: str = "normal"
        self._group_config = None
        self._ctx: PluginContext | None = None
        self._pending_retries: list[_PendingStickerRetry] = []
        self._pending_retry_keys: set[tuple[str, str, str]] = set()

    async def on_startup(self, ctx: PluginContext) -> None:
        import nonebot

        from kernel.config import load_plugin_config

        sticker_cfg = load_plugin_config("plugins/sticker/config.default.json", StickerConfig)
        self._sticker_store = ctx.sticker_store
        self._vision_client = ctx.vision_client
        self._image_cache = ctx.image_cache
        self._ctx = ctx
        self._superusers = set(ctx.config.admins.keys()) | nonebot.get_driver().config.superusers
        self._sticker_frequency = sticker_cfg.frequency
        self._group_config = getattr(ctx.config, "group", None)

    def register_tools(self) -> list[Tool]:
        if self._sticker_store is None:
            return []
        from services.tools.sticker_tools import (
            ManageStickerTool,
            SaveStickerTool,
            SendStickerTool,
        )
        return [
            SaveStickerTool(self._sticker_store, self._superusers),
            SendStickerTool(self._sticker_store),
            ManageStickerTool(self._sticker_store, self._superusers),
        ]

    async def on_message(self, ctx: MessageContext) -> bool:
        """silent_learn 模式群里，把群友发的表情静默吸进表情库。

        不消费消息（永远 return False）；只在 group_presence_mode == silent_learn
        且非主动发言态时启用——active 群走 SaveStickerTool 工具调用路径。
        """
        if (
            ctx.allow_speaking
            or ctx.group_presence_mode != "silent_learn"
            or not ctx.is_group
            or self._sticker_store is None
        ):
            return False
        group_id_int: int | None = None
        if self._group_config is not None and ctx.group_id:
            try:
                group_id_int = int(ctx.group_id)
                resolved = self._group_config.resolve(group_id_int)
            except Exception:
                group_id_int = None
                resolved = None
            if resolved is not None:
                if self._silent_sticker_learning_disabled(group_id_int):
                    return False
                if str(getattr(resolved, "sticker_mode", "inherit") or "inherit") == "off":
                    return False

        segments = ctx.raw_message.get("segments")
        if not segments:
            return False
        bot = ctx.bot
        if bot is None:
            return False

        saved = 0
        for seg in segments:
            if saved >= _SILENT_STEAL_MAX_IMAGES:
                break
            if not is_sticker_like_segment(seg):
                continue
            file_id = segment_value(seg, "file")
            path = await self._ensure_segment_cached(bot, seg, file_id=file_id)
            if path is None or not path.exists():
                self._queue_retry(ctx, seg, file_id=file_id)
                continue
            try:
                image_data = await asyncio.to_thread(path.read_bytes)
                description = sticker_description_from_segment(seg)
                sticker_id, is_new = await asyncio.to_thread(
                    self._sticker_store.add,
                    image_data,
                    description,
                    DEFAULT_STICKER_USAGE_HINT,
                    "stolen_silent_learn",
                )
            except ValueError as exc:
                _L.debug("silent sticker learn skipped | group={} reason={}", ctx.group_id, exc)
                continue
            except Exception:
                _L.warning(
                    "silent sticker learn failed | group={} file={}",
                    ctx.group_id, file_id, exc_info=True,
                )
                continue
            if is_new:
                saved += 1
                _L.info(
                    "silent sticker learned | group={} sticker_id={} file={}",
                    ctx.group_id,
                    sticker_id,
                    file_id,
                )
        return False

    def _silent_sticker_learning_disabled(self, group_id: int | None) -> bool:
        """Respect explicit tool shutdowns without treating silent access as disabled tools."""
        if self._group_config is None:
            return False
        if bool(getattr(self._group_config, "tools_enabled", True)) is False:
            return True
        overrides = getattr(self._group_config, "overrides", {}) or {}
        override = overrides.get(group_id) if group_id is not None else None
        if override is None and group_id is not None:
            override = overrides.get(str(group_id))
        return bool(getattr(override, "tools_enabled", None) is False)

    async def _ensure_segment_cached(self, bot: Any, seg: Any, *, file_id: str) -> Path | None:
        url = segment_value(seg, "url")
        clean_file_id = (file_id.split(".", 1)[0] if file_id else "").strip()
        if url and clean_file_id and self._image_cache is not None:
            session = getattr(getattr(bot, "adapter", None), "session", None)
            if session is None:
                session = getattr(bot, "_session", None)
            if session is None:
                llm_client = getattr(self._ctx, "llm_client", None)
                session = getattr(llm_client, "_session", None)
            if session is not None:
                ref = await self._image_cache.save(session, url=url, file_id=clean_file_id)
                if ref is not None:
                    return Path(str(ref["path"]))

        if file_id:
            try:
                resp = await bot.call_api("get_image", file=file_id)
            except Exception:
                return None
            file_path = ""
            if isinstance(resp, dict):
                file_path = str(resp.get("file") or "").strip()
            if file_path:
                path = Path(file_path)
                if path.exists():
                    return path
        return None

    def _queue_retry(self, ctx: MessageContext, seg: Any, *, file_id: str) -> None:
        clean_file_id = (file_id.split(".", 1)[0] if file_id else "").strip()
        if not clean_file_id:
            return

        group_id = str(ctx.group_id or "")
        message_id = str(ctx.message_id or "")
        key = (group_id, message_id, clean_file_id)
        if key in self._pending_retry_keys:
            return

        if len(self._pending_retries) >= _MAX_PENDING_RETRIES:
            old = self._pending_retries.pop(0)
            self._pending_retry_keys.discard(old.key)

        segment = {
            "type": "image",
            "data": {
                "file": file_id,
                "url": segment_value(seg, "url"),
                "sub_type": segment_value(seg, "sub_type"),
                "summary": segment_value(seg, "summary"),
            },
        }
        retry = _PendingStickerRetry(
            key=key,
            group_id=group_id,
            message_id=message_id,
            file_id=file_id,
            segment=segment,
        )
        self._pending_retries.append(retry)
        self._pending_retry_keys.add(key)
        _L.debug(
            "silent sticker retry queued | group={} message_id={} file={}",
            group_id,
            message_id,
            file_id,
        )

    async def on_tick(self, ctx: PluginContext) -> None:
        if not self._pending_retries or self._sticker_store is None:
            return

        bot = getattr(ctx, "bot", None)
        pending = self._pending_retries
        self._pending_retries = []
        self._pending_retry_keys.clear()

        if bot is None:
            for retry in pending:
                _L.debug(
                    "silent sticker retry dropped | group={} message_id={} file={} reason=no_bot",
                    retry.group_id,
                    retry.message_id,
                    retry.file_id,
                )
            return

        for retry in pending:
            path = await self._ensure_segment_cached(bot, retry.segment, file_id=retry.file_id)
            if path is None or not path.exists():
                _L.debug(
                    "silent sticker retry dropped | group={} message_id={} file={} reason=fetch_failed",
                    retry.group_id,
                    retry.message_id,
                    retry.file_id,
                )
                continue
            try:
                image_data = await asyncio.to_thread(path.read_bytes)
                sticker_id, is_new = await asyncio.to_thread(
                    self._sticker_store.add,
                    image_data,
                    sticker_description_from_segment(retry.segment),
                    DEFAULT_STICKER_USAGE_HINT,
                    "stolen_silent_retry",
                )
            except ValueError as exc:
                _L.debug(
                    "silent sticker retry dropped | group={} message_id={} file={} reason={}",
                    retry.group_id,
                    retry.message_id,
                    retry.file_id,
                    exc,
                )
                continue
            except Exception:
                _L.warning(
                    "silent sticker retry dropped | group={} message_id={} file={} reason=save_failed",
                    retry.group_id,
                    retry.message_id,
                    retry.file_id,
                    exc_info=True,
                )
                continue

            if is_new:
                _L.info(
                    "silent sticker retry learned | group={} message_id={} sticker_id={} file={}",
                    retry.group_id,
                    retry.message_id,
                    sticker_id,
                    retry.file_id,
                )
            else:
                _L.debug(
                    "silent sticker retry dropped | group={} message_id={} file={} reason=duplicate",
                    retry.group_id,
                    retry.message_id,
                    retry.file_id,
                )

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        frequency = self._sticker_frequency
        if ctx.group_id and self._group_config is not None:
            try:
                resolved = self._group_config.resolve(int(ctx.group_id))
            except Exception:
                resolved = None
            if resolved is not None:
                if not bool(getattr(resolved, "tools_enabled", True)):
                    return
                sticker_mode = str(getattr(resolved, "sticker_mode", "inherit") or "inherit")
                if sticker_mode == "off":
                    return
                if sticker_mode in _STICKER_FREQUENCY_PROMPTS:
                    frequency = sticker_mode

        # Sticker frequency prompt (static — part of personality)
        freq_prompt = _STICKER_FREQUENCY_PROMPTS.get(frequency)
        if freq_prompt and self._sticker_store is not None:
            formatted = freq_prompt.format(name=ctx.identity.name)
            ctx.add_block(text=formatted, label="表情包规则", position="static", priority=5, source="sticker")

        # Sticker library view (stable — changes when stickers added/removed)
        if self._sticker_store is not None:
            view = self._sticker_store.format_prompt_view()
            if view:
                ctx.add_block(text=view, label="表情包库", position="stable", priority=30, source="sticker")
