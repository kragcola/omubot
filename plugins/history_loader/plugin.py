"""HistoryLoaderPlugin: 群聊历史加载。

在 bot 连接后加载群聊历史消息到 timeline。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger
from nonebot.adapters.onebot.v11.bot import Bot

from kernel.qq_face import face_to_text
from kernel.types import AmadeusPlugin, PluginContext
from services.media.image_cache import ImageCache
from services.media.sticker_capture import (
    DEFAULT_STICKER_USAGE_HINT,
    emit_emotion_tag,
    is_sticker_like_segment,
    sticker_description_from_segment,
)
from services.media.sticker_store import StickerStore
from services.memory.timeline import GroupTimeline
from services.memory.types import Content, ContentBlock, ImageRefBlock, TextBlock

_L = logger.bind(channel="system")


def _contains_debug_command(segments: list[dict[str, Any]]) -> bool:
    """Return True if any segment contains a /debug command that should not be replayed."""
    for seg in segments:
        text = seg.get("data", {}).get("text", "")
        if "/debug" in text:
            return True
    return False


async def load_group_history(
    bot: Bot,
    group_ids: list[str],
    timeline: GroupTimeline,
    count: int = 30,
    bot_self_id: str = "",
    image_cache: ImageCache | None = None,
    sticker_store: StickerStore | None = None,
    vision_client: Any | None = None,
    learn_new_stickers: set[str] | None = None,
    counts: dict[str, int] | None = None,
) -> None:
    """通过 OneBot WebSocket 拉取多个群的历史消息。"""
    sticker_learn_groups = learn_new_stickers or set()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for gid in group_ids:
            gid_count = counts.get(gid, count) if counts else count
            try:
                await _load_one_group(
                    bot,
                    session,
                    gid,
                    timeline,
                    gid_count,
                    bot_self_id,
                    image_cache,
                    sticker_store,
                    vision_client,
                    gid in sticker_learn_groups,
                )
            except Exception:
                _L.warning("load_history failed | group={}", gid, exc_info=True)


async def _load_one_group(
    bot: Bot,
    session: aiohttp.ClientSession,
    group_id: str,
    timeline: GroupTimeline,
    count: int,
    bot_self_id: str = "",
    image_cache: ImageCache | None = None,
    sticker_store: StickerStore | None = None,
    vision_client: Any | None = None,
    learn_new_stickers: bool = False,
) -> None:
    from nonebot.adapters.onebot.v11.exception import ActionFailed

    try:
        data: dict[str, Any] = await bot.call_api(
            "get_group_msg_history",
            group_id=int(group_id),
            count=count,
        )
    except ActionFailed as e:
        _L.warning("get_group_msg_history error | group={} {}", group_id, e.info.get("wording", str(e)))
        return

    messages: list[dict[str, Any]] = data.get("messages", [])
    if not messages:
        return

    t0 = time.perf_counter()
    loaded = 0
    self_count = 0

    for msg in messages:
        sender: dict[str, Any] = msg.get("sender", {})
        user_id = str(sender.get("user_id", ""))
        nickname = sender.get("nickname", "") or sender.get("card", "") or user_id

        # Skip messages containing /debug commands — they are interactive admin
        # operations and should not re-trigger thinker/chat flows on restart.
        raw_segs = msg.get("message", [])
        if _contains_debug_command(raw_segs):
            continue

        content = await _extract_content(
            raw_segs,
            session,
            image_cache,
            sticker_store,
            vision_client=vision_client,
            learn_new_stickers=learn_new_stickers,
        )
        if not content:
            continue

        msg_id = msg.get("message_id")
        if bot_self_id and user_id == bot_self_id:
            timeline.add(group_id, role="assistant", content=content)
            self_count += 1
        else:
            timeline.add(
                group_id, role="user", speaker=f"{nickname}({user_id})",
                content=content, message_id=msg_id,
            )
        loaded += 1

    elapsed_ms = (time.perf_counter() - t0) * 1000
    _L.info(
        "history loaded | group={} messages={} self={} bot_self_id={} elapsed={:.0f}ms",
        group_id, loaded, self_count, bot_self_id, elapsed_ms,
    )


async def _extract_content(
    segments: list[dict[str, Any]],
    session: aiohttp.ClientSession,
    image_cache: ImageCache | None,
    sticker_store: StickerStore | None = None,
    *,
    vision_client: Any | None = None,
    learn_new_stickers: bool = False,
) -> Content:
    """Extract text, face, and image segments into a Content value."""
    text_parts: list[str] = []
    image_tasks: list[tuple[dict[str, Any], asyncio.Task[ImageRefBlock | None]]] = []

    for seg in segments:
        seg_type = seg.get("type", "")
        seg_data: dict[str, Any] = seg.get("data", {})

        if seg_type == "text":
            text_parts.append(seg_data.get("text", ""))
        elif seg_type == "face":
            face_id = seg_data.get("id", "")
            try:
                text_parts.append(face_to_text(int(face_id)))
            except (ValueError, TypeError):
                text_parts.append("«表情»")
        elif seg_type == "image" and image_cache is not None:
            url = seg_data.get("url", "")
            file_id = seg_data.get("file", "")
            if url and file_id:
                file_id = file_id.split(".")[0] if "." in file_id else file_id
                image_tasks.append(
                    (
                        seg,
                        asyncio.ensure_future(image_cache.save(session, url=url, file_id=file_id)),
                    )
                )
            else:
                text_parts.append("«图片»")
        elif seg_type == "image":
            text_parts.append("«图片»")

    images: list[ImageRefBlock] = []
    if image_tasks:
        t0 = time.perf_counter()
        results = await asyncio.gather(*(task for _seg, task in image_tasks), return_exceptions=True)
        for (seg, _task), r in zip(image_tasks, results, strict=False):
            if isinstance(r, BaseException) or r is None:
                text_parts.append("«图片»")
            else:
                if sticker_store is not None and image_cache is not None:
                    cached_path = Path(r["path"])
                    if cached_path.exists():
                        image_data = cached_path.read_bytes()
                        stk_id = sticker_store.lookup_by_hash(image_data)
                        if stk_id is not None:
                            sticker_path = sticker_store.resolve_path(stk_id)
                            if sticker_path is not None:
                                cached_path.unlink(missing_ok=True)
                                images.append(
                                    ImageRefBlock(
                                        type="image_ref",
                                        path=str(sticker_path),
                                        media_type=r["media_type"],
                                    )
                                )
                                _L.debug(
                                    "history image matched sticker | sticker_id={}", stk_id
                                )
                                continue
                        if learn_new_stickers and is_sticker_like_segment(seg):
                            description = sticker_description_from_segment(seg)
                            try:
                                stk_id, is_new = sticker_store.add(
                                    image_data,
                                    description,
                                    DEFAULT_STICKER_USAGE_HINT,
                                    source="history_loader_sticker_learn",
                                )
                            except ValueError as exc:
                                _L.debug(
                                    "history sticker learn skipped | reason={} file={}",
                                    exc,
                                    Path(r["path"]).name,
                                )
                            except Exception:
                                _L.warning(
                                    "history sticker learn failed | file={}",
                                    Path(r["path"]).name,
                                    exc_info=True,
                                )
                            else:
                                sticker_path = sticker_store.resolve_path(stk_id)
                                if sticker_path is not None:
                                    if is_new:
                                        await emit_emotion_tag(
                                            sticker_store,
                                            stk_id,
                                            image_data=image_data,
                                            vision_client=vision_client,
                                            media_type=r["media_type"],
                                        )
                                    cached_path.unlink(missing_ok=True)
                                    images.append(
                                        ImageRefBlock(
                                            type="image_ref",
                                            path=str(sticker_path),
                                            media_type=r["media_type"],
                                        )
                                    )
                                    if is_new:
                                        _L.info(
                                            "history sticker learned | sticker_id={} file={}",
                                            stk_id,
                                            Path(r["path"]).name,
                                        )
                                    continue
                images.append(r)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _L.debug(
            "history image batch | tasks={} ok={} elapsed={:.0f}ms",
            len(image_tasks), len(images), elapsed_ms,
        )

    text = "".join(text_parts).strip()

    if not images:
        return text

    blocks: list[ContentBlock] = []
    if text:
        blocks.append(TextBlock(type="text", text=text))
    blocks.extend(images)
    return blocks


class HistoryLoaderPlugin(AmadeusPlugin):
    name = "history_loader"
    description = "群聊历史加载：bot 连接后回填近期消息"
    version = "1.1.2"
    priority = 5  # Run early, after ChatPlugin but before other business plugins

    async def on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        try:
            group_list = await bot.get_group_list()
            group_ids = [str(g["group_id"]) for g in group_list]
            allowed = getattr(ctx, "allowed_groups", set())
            if allowed:
                group_ids = [gid for gid in group_ids if int(gid) in allowed]
        except Exception:
            logger.exception("failed to get group list for history loading")
            return

        group_cfg = getattr(ctx.config, "group", None)
        learn_sticker_groups: set[str] = set()
        if group_cfg is not None and hasattr(group_cfg, "resolve"):
            for gid in group_ids:
                try:
                    resolved = group_cfg.resolve(int(gid))
                except Exception:
                    continue
                if resolved is None:
                    continue
                if str(getattr(resolved, "presence_mode", "active") or "active") != "silent_learn":
                    continue
                if bool(getattr(group_cfg, "tools_enabled", True)) is False:
                    continue
                if bool(getattr(resolved, "tools_enabled", True)) is False:
                    continue
                if str(getattr(resolved, "sticker_mode", "inherit") or "inherit") == "off":
                    continue
                learn_sticker_groups.add(gid)

        _L.info("loading history | groups={}", len(group_ids))
        try:
            counts = {
                gid: ctx.config.group.resolve(int(gid)).history_load_count
                for gid in group_ids
            }
            await load_group_history(
                bot=bot,
                group_ids=group_ids,
                timeline=ctx.timeline,
                count=ctx.config.group.history_load_count,
                bot_self_id=bot.self_id,
                image_cache=ctx.image_cache if getattr(ctx, "vision_enabled", False) else None,
                sticker_store=ctx.sticker_store,
                vision_client=getattr(ctx, "vision_client", None),
                learn_new_stickers=learn_sticker_groups,
                counts=counts,
            )
        except Exception:
            logger.exception("failed to load group history")
