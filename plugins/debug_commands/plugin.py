"""DebugCommandPlugin: operational and runtime-inspection commands.

Admin-only slash commands for inspecting bot state at runtime.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from loguru import logger
from pydantic import BaseModel

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext
from services.admin_access import effective_admin_ids
from services.llm.llm_request import LLMRequest

_log = logger.bind(channel="command")


def _sanitize_debug_reply(
    text: str,
    *,
    fallback: str = "这次没有执行到可用工具。",
) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"(?i)\[?\s*pass[_\s-]*turn\s*\]?", "", cleaned)
    replacements = {
        "结束本轮": "",
        "内部原因": "原因",
        "本轮未执行工具": "这次没有执行到可用工具",
        "无法匹配任何工具": "现在没有合适的工具可用",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。；;：:、")
    cleaned = re.sub(r"([。！？!?]){2,}", r"\1", cleaned).strip()
    if cleaned == "这次没有执行到可用工具" or not cleaned:
        return fallback
    return cleaned


class DebugCommandConfig(BaseModel):
    """聊天内调试指令配置。"""

    plugins_command_enabled: bool = True
    version_command_enabled: bool = True
    max_reply_chars: int = 2000
    check_github_updates: bool = True


class DebugCommandPlugin(AmadeusPlugin):
    name = "debug_commands"
    description = "调试与运维指令：运行态检查、权限、插件与版本信息"
    version = "1.4.0"
    priority = 300  # After all business plugins, before third-party

    def __init__(self) -> None:
        super().__init__()
        self._ctx: PluginContext | None = None
        self._config = DebugCommandConfig()

    async def on_startup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._config = load_plugin_config("plugins/debug_commands/config.default.json", DebugCommandConfig)

    def register_commands(self) -> list:
        from kernel.types import Command
        commands: list[Command] = [
            Command(
                name="debug",
                handler=self._handle_debug,
                description="进入调试模式：跳过 thinker，注入实时状态数据，用纯文本回答",
                usage="/debug [可选问题]",
                admin_only=True,
                hidden=True,
                passthrough_unknown=True,
                sub_commands=[
                    Command(
                        name="save",
                        handler=self._handle_debug_save,
                        description="保存最近图片到表情包库",
                        usage="/debug save [描述]",
                        aliases=["保存", "收录", "添加表情"],
                    ),
                    Command(
                        name="send",
                        handler=self._handle_debug_send,
                        description="发送表情包（指定ID或随机）",
                        usage="/debug send [stk_id|gif]",
                        aliases=["发", "发送"],
                    ),
                    Command(
                        name="split",
                        handler=self._handle_debug_split,
                        description="测试文本分段效果",
                        usage="/debug split <文本>",
                        aliases=["分段", "分割"],
                        require_args=True,
                    ),
                ],
            ),
            Command(
                name="authority",
                handler=self._handle_authority,
                description="查看/设置用户指令权限等级（0-4），管理员专用",
                usage="/authority <QQ号> [0-4]  （省略等级=查询；用 reset 清除）",
                admin_only=True,
                hidden=True,
                aliases=["权限", "授权"],
            ),
        ]
        if self._config.plugins_command_enabled:
            commands.append(Command(
                name="plugins",
                handler=self._handle_plugins,
                description="列出所有已加载插件（名称、版本、开发者、简介）",
                usage="/plugins",
                aliases=["p", "plg", "插件"],
                admin_only=True,
            ))
        if self._config.version_command_enabled:
            commands.append(Command(
                name="version",
                handler=self._handle_version,
                description="查看 Omubot 版本并检查 GitHub 更新",
                usage="/version",
            ))
        return commands

    async def _handle_debug(self, cmd_ctx: Any) -> None:
        """Handle /debug command: admin-only debug mode with live state and tool execution."""
        from nonebot.adapters.onebot.v11 import Message

        from services.llm.client import (
            _PASS_TURN_TOOL,
            _build_debug_block,
            _strip_markdown,
            _to_anthropic_tools,
        )
        from services.tools.context import ToolContext

        ctx = self._ctx
        if ctx is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("系统未就绪"))
            return

        logger.info(
            "debug mode | user={} {}",
            cmd_ctx.user_id,
            "private" if cmd_ctx.is_private else f"group={cmd_ctx.group_id}",
        )

        sid = f"private_{cmd_ctx.user_id}" if cmd_ctx.is_private else f"group_{cmd_ctx.group_id}"
        user_content = cmd_ctx.args if cmd_ctx.args else "请显示当前系统状态摘要"
        has_command = bool(cmd_ctx.args)

        # Build tool context once
        tool_ctx_obj = ToolContext(
            bot=cmd_ctx.bot,
            user_id=str(cmd_ctx.user_id),
            group_id=str(cmd_ctx.group_id) if not cmd_ctx.is_private else None,
            session_id=sid,
        )

        # ---- LLM path ----
        if has_command:
            # Include sticker library so the LLM can pick sticker IDs
            sticker_view = ""
            if ctx.sticker_store is not None:
                sticker_view = ctx.sticker_store.format_prompt_view()
            system_blocks = [
                {"type": "text", "text": (
                    "你是工具执行器。用户指令=工具调用。\n"
                    "规则：绝不输出文字。你的每次回复必须是 tool_use，不能是 text。\n"
                    "发送表情→send_sticker(sticker_id)  查卡→lookup_cards\n"
                    "设置昵称→set_nickname  更新记忆→update_card  管理表情→manage_sticker\n"
                    "如果没有合适工具，就直接用自然语言说明当前做不到，并给出最短解释；不要描述内部流程。"
                )},
            ]
            if sticker_view:
                system_blocks.append({"type": "text", "text": sticker_view})
        else:
            # No command: full state dump for inspection
            _log.info("debug building state dump | user={}", cmd_ctx.user_id)
            debug_text = await asyncio.wait_for(
                _build_debug_block(
                    user_id=cmd_ctx.user_id,
                    session_id=sid,
                    mood_engine=ctx.mood_engine,
                    affection_engine=ctx.affection_engine,
                    schedule_store=ctx.schedule_store,
                    card_store=ctx.card_store,
                    short_term=ctx.short_term,
                    message_log=ctx.msg_log,
                ),
                timeout=15.0,
            )
            _log.info("debug state dump ready | chars={}", len(debug_text))
            system_blocks = [
                {"type": "text", "text": debug_text},
                {"type": "text", "text": (
                    "你是调试助手。基于上面的实时状态数据如实回答用户的问题。\n"
                    "格式约束：QQ 不支持 Markdown。禁止代码块、加粗、行内代码。使用纯文本。"
                )},
            ]
        messages: list[Any] = [{"role": "user", "content": user_content}]

        # Build tool definitions — all registered tools available
        tool_defs: list[dict[str, Any]] | None = None
        if not ctx.tool_registry.empty:
            tool_defs = _to_anthropic_tools(ctx.tool_registry.to_openai_tools())
        tool_defs = [*(tool_defs or []), _PASS_TURN_TOOL]
        _log.info(
            "debug tool_defs | count={} names={}",
            len(tool_defs), [t["name"] for t in tool_defs],
        )

        MAX_TOOL_ROUNDS = 5

        try:
            for _round_i in range(MAX_TOOL_ROUNDS):
                _log.info("debug API call round={}", _round_i)
                try:
                    debug_request = LLMRequest(
                        task="chat_private",
                        user_id=str(cmd_ctx.user_id or ""),
                        group_id=None if cmd_ctx.is_private else str(cmd_ctx.group_id or ""),
                        static_blocks=list(system_blocks),
                        user_messages=list(messages),
                        tools=tool_defs,
                        max_tokens=1024,
                        requires_capabilities=("chat", "tools"),
                    )
                    result = await asyncio.wait_for(
                        ctx.llm_client._call(debug_request),
                        timeout=60.0,
                    )
                except TimeoutError:
                    _log.error("debug API call timed out | round={}", _round_i)
                    await cmd_ctx.bot.send(cmd_ctx.event, Message("调试: API 调用超时 (60s)"))
                    return
                text: str = result.get("text", "")
                tool_uses: list[Any] = result.get("tool_uses", [])
                _log.info(
                    "debug API response | round={} text_len={} tool_count={} names={}",
                    _round_i, len(text), len(tool_uses),
                    [tu.name for tu in tool_uses],
                )

                # pass_turn: skip action, just reply with text (if any)
                if any(tu.name == "pass_turn" for tu in tool_uses):
                    used_tools = [tu for tu in tool_uses if tu.name != "pass_turn"]
                    if not used_tools:
                        reason = ""
                        for tu in tool_uses:
                            if tu.name == "pass_turn":
                                reason = tu.input.get("reason", "")
                        reply_text = _sanitize_debug_reply(
                            text.strip() or reason,
                            fallback="这次没有执行到可用工具。",
                        )
                        _log.info("debug pass_turn | reason={!r}", reason)
                        await ctx.humanizer.delay(reply_text)
                        await cmd_ctx.bot.send(cmd_ctx.event, Message(f"调试：{reply_text}"))
                        return

                if not tool_uses:
                    reply_text = _sanitize_debug_reply(
                        _strip_markdown(text or ""),
                        fallback="调试：这次没有产出可展示的结果。",
                    )
                    if reply_text.strip():
                        _log.info("debug reply | len={} text={!r}", len(reply_text), reply_text[:120])
                        await ctx.humanizer.delay(reply_text)
                        await cmd_ctx.bot.send(cmd_ctx.event, Message(reply_text))
                    return

                # Build assistant content with tool_use blocks
                assistant_content: list[dict[str, Any]] = []
                for tb in result.get("thinking_blocks", []):
                    assistant_content.append(tb)
                if text:
                    assistant_content.append({"type": "text", "text": text})
                for tu in tool_uses:
                    assistant_content.append({
                        "type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input,
                    })
                messages.append({"role": "assistant", "content": assistant_content})

                # Execute tools
                _log.info("debug executing tools | count={} names={}", len(tool_uses), [tu.name for tu in tool_uses])
                call_results = await asyncio.gather(
                    *[ctx.tool_registry.call(tu.name, json.dumps(tu.input), ctx=tool_ctx_obj)
                      for tu in tool_uses],
                    return_exceptions=True,
                )
                call_results = [
                    r if isinstance(r, str) else f"Tool error: {r}" for r in call_results
                ]
                _log.info(
                    "debug tool results | names={} results={}",
                    [tu.name for tu in tool_uses],
                    [r[:200] for r in call_results],
                )
                tool_results: list[dict[str, Any]] = []
                for tu, rtext in zip(tool_uses, call_results, strict=True):
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "content": rtext,
                    })
                messages.append({"role": "user", "content": tool_results})

            # Tool loop exhausted — final reply
            _log.debug("debug tool loop exhausted, calling final API")
            final_request = LLMRequest(
                task="chat_private",
                user_id=str(cmd_ctx.user_id or ""),
                group_id=None if cmd_ctx.is_private else str(cmd_ctx.group_id or ""),
                static_blocks=list(system_blocks),
                user_messages=list(messages),
                max_tokens=1024,
                requires_capabilities=("chat",),
            )
            result = await asyncio.wait_for(
                ctx.llm_client._call(final_request),
                timeout=60.0,
            )
            reply_text = _sanitize_debug_reply(
                _strip_markdown(result.get("text") or ""),
                fallback="调试：这次没有产出可展示的结果。",
            )
            if reply_text.strip():
                _log.info("debug reply (final) | len={} text={!r}", len(reply_text), reply_text[:120])
                await ctx.humanizer.delay(reply_text)
                await cmd_ctx.bot.send(cmd_ctx.event, Message(reply_text))
        except TimeoutError:
            _log.error("debug API call timed out (final)")
            await cmd_ctx.bot.send(cmd_ctx.event, Message("调试: API 调用超时 (60s)"))
        except Exception:
            logger.exception("debug command LLM call failed")
            await cmd_ctx.bot.send(cmd_ctx.event, Message("调试查询失败，请稍后重试"))

    async def _handle_authority(self, cmd_ctx: Any) -> None:
        """Handle /authority — view/set per-user instruction authority level (no LLM)."""
        from nonebot.adapters.onebot.v11 import Message

        ctx = self._ctx
        if ctx is None or ctx.llm_client is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("系统未就绪"))
            return
        store = getattr(ctx.llm_client, "_authority_store", None)
        gate = getattr(ctx.llm_client, "_instruction_gate", None)
        if store is None or gate is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("指令门禁未启用（instruction_gate.enabled=false）"))
            return

        parts = str(cmd_ctx.args or "").split()
        if not parts:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("用法：/authority <QQ号> [0-4|reset]"))
            return

        target = parts[0].strip()
        if not target.isdigit():
            await cmd_ctx.bot.send(cmd_ctx.event, Message("QQ号需为纯数字"))
            return

        admins = {admin_id: "管理员" for admin_id in effective_admin_ids(ctx)}
        # Query mode.
        if len(parts) == 1:
            level = gate.resolve_authority(target, admins, store.snapshot())
            source = "管理员" if target in admins else ("覆盖" if store.get(target) is not None else "默认")
            await cmd_ctx.bot.send(cmd_ctx.event, Message(f"用户 {target} 当前权限等级：{level}（{source}）"))
            return

        # Set / reset mode.
        value = parts[1].strip().lower()
        if value in {"reset", "clear", "默认"}:
            if target in admins:
                await cmd_ctx.bot.send(cmd_ctx.event, Message("管理员等级固定为 4，无法重置"))
                return
            cleared = store.clear(target)
            msg = f"已重置用户 {target} 的权限覆盖" if cleared else f"用户 {target} 本就无覆盖"
            await cmd_ctx.bot.send(cmd_ctx.event, Message(msg))
            return

        if not value.isdigit() or not (0 <= int(value) <= 4):
            await cmd_ctx.bot.send(cmd_ctx.event, Message("等级须为 0-4 的整数，或 reset"))
            return
        if target in admins:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("管理员等级固定为 4，无法调整"))
            return
        applied = store.set(target, int(value))
        await cmd_ctx.bot.send(cmd_ctx.event, Message(f"已设置用户 {target} 的权限等级为 {applied}"))

    async def _handle_debug_save(self, cmd_ctx: Any) -> None:
        """Handle /debug save — save recent image as sticker (no LLM)."""
        import re as _re_cq
        from pathlib import Path as _Path

        from nonebot.adapters.onebot.v11 import Message

        from services.tools.context import ToolContext
        from services.tools.sticker_tools import SaveStickerTool

        ctx = self._ctx
        if ctx is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("系统未就绪"))
            return

        store = ctx.sticker_store
        if store is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("表情包库未启用"))
            return

        admin_ids = effective_admin_ids(ctx)

        # 1. Extract images from the current message event
        image_paths: list[str] = []
        raw_msg = cmd_ctx.event.get_message()
        _seg_types: list[str] = []
        for seg in raw_msg:
            _seg_types.append(seg.type)
            if seg.type == "image":
                url = seg.data.get("url", "")
                file_uniq = seg.data.get("file", "")
                if url and ctx.image_cache:
                    try:
                        ref = await ctx.image_cache.save(
                            ctx.llm_client._session, url=url, file_id=file_uniq,
                        )
                        if ref is not None:
                            image_paths.append(ref["path"])
                    except Exception as e:
                        logger.warning("debug save: image download failed | url={} err={}", url[:80], e)
            elif seg.type in ("mface", "market_face"):
                key = seg.data.get("key") or seg.data.get("file_unique") or seg.data.get("id", "")
                summary = seg.data.get("summary", "")
                if key and ctx.sticker_store:
                    try:
                        resp = await cmd_ctx.bot.call_api("get_image", file=key)
                        file_data = resp.get("file", "")
                        if file_data.startswith("base64://"):
                            import base64 as _b64
                            raw = _b64.b64decode(file_data[len("base64://"):])
                        elif file_data.startswith("file://"):
                            p = _Path(file_data[len("file://"):])
                            raw = p.read_bytes() if p.exists() else b""
                        else:
                            p = _Path(file_data)
                            raw = p.read_bytes() if p.exists() else b""
                        if raw:
                            tmp_path = ctx.sticker_store.storage_dir / f"_tmp_mface_{key}.tmp"
                            tmp_path.write_bytes(raw)
                            image_paths.append(str(tmp_path))
                            logger.info(
                                "debug save: mface downloaded | key={} summary={!r} size={}",
                                key, summary, len(raw),
                            )
                    except Exception as e:
                        logger.warning(
                            "debug save: mface download failed | key={} summary={!r} err={}",
                            key, summary, e,
                        )

        raw_str = getattr(cmd_ctx.event, "raw_message", "")
        for m in _re_cq.finditer(r"\[(?:mface|market_face):([^\]]+)\]", raw_str):
            params = dict(p.split("=", 1) for p in m.group(1).split(",") if "=" in p)
            key = params.get("key") or params.get("file_unique") or params.get("id", "")
            summary = params.get("summary", "")
            if key and ctx.sticker_store:
                try:
                    resp = await cmd_ctx.bot.call_api("get_image", file=key)
                    file_data = resp.get("file", "")
                    if file_data.startswith("base64://"):
                        import base64 as _b64
                        raw = _b64.b64decode(file_data[len("base64://"):])
                    elif file_data.startswith("file://"):
                        p = _Path(file_data[len("file://"):])
                        raw = p.read_bytes() if p.exists() else b""
                    else:
                        p = _Path(file_data)
                        raw = p.read_bytes() if p.exists() else b""
                    if raw:
                        tmp_path = ctx.sticker_store.storage_dir / f"_tmp_mface_{key}.tmp"
                        tmp_path.write_bytes(raw)
                        image_paths.append(str(tmp_path))
                        logger.info(
                            "debug save: mface from raw_message | key={} summary={!r} size={}",
                            key, summary, len(raw),
                        )
                except Exception as e:
                    logger.warning(
                        "debug save: mface raw_message download failed | key={} err={}",
                        key, e,
                    )
        logger.info("debug save: segment scan | types={} image_count={}", _seg_types, len(image_paths))

        # 2. Fall back to timeline if no images in current message
        if not image_paths:
            group_id = str(cmd_ctx.group_id) if cmd_ctx.group_id else None
            if group_id and ctx.timeline:
                for msg in reversed(ctx.timeline.get_pending(group_id)):
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "image_ref":
                                p = block.get("path")
                                if p:
                                    image_paths.append(p)
                                    break
                    if image_paths:
                        break
                if not image_paths:
                    for turn in reversed(ctx.timeline.get_turns(group_id)):
                        content = turn.get("content", "")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "image_ref":
                                    p = block.get("path")
                                    if p:
                                        image_paths.append(p)
                                        break
                        if image_paths:
                            break

        if not image_paths:
            await cmd_ctx.bot.send(
                cmd_ctx.event,
                Message("未找到图片（请先发送图片再使用此命令，或将图片与命令放在同一条消息中）"),
            )
            return

        user_desc = cmd_ctx.args.strip()

        tool_ctx_obj = ToolContext(
            bot=cmd_ctx.bot,
            user_id=str(cmd_ctx.user_id),
            group_id=str(cmd_ctx.group_id) if cmd_ctx.group_id else None,
            session_id=f"group_{cmd_ctx.group_id}" if cmd_ctx.group_id else f"private_{cmd_ctx.user_id}",
        )
        results: list[str] = []
        for idx, image_path in enumerate(image_paths):
            tool_ctx_obj.extra["image_tags"] = {f"img:{idx + 1}": image_path}
            image_data = _Path(image_path).read_bytes()

            description: str | None = None
            usage_hint = "通用聊天表情"

            vision_client = ctx.vision_client
            if vision_client is not None:
                if idx > 0:
                    await asyncio.sleep(1.5)
                try:
                    desc = await vision_client.describe_image(image_data)
                    if desc:
                        description = desc
                        usage_hint = desc
                        logger.info("debug save_sticker vision desc | path={} desc={!r}", image_path, desc)
                except Exception as e:
                    logger.warning("debug save_sticker vision failed | path={} err={}", image_path, e)

            if description is None:
                description = "通用聊天表情"

            if user_desc:
                description = f"{user_desc}。{description}"
                usage_hint = user_desc

            tool = SaveStickerTool(store, admin_ids)
            result = await tool.execute(
                tool_ctx_obj,
                image_tag=f"img:{idx + 1}",
                description=description,
                usage_hint=usage_hint,
                requested_by=str(cmd_ctx.user_id),
            )
            results.append(result)
            logger.info("debug direct save_sticker | path={} result={}", image_path, result)

        summary = f"已处理 {len(image_paths)} 张图片：\n" + "\n".join(f"  {r}" for r in results)
        await cmd_ctx.bot.send(cmd_ctx.event, Message(summary))

    async def _handle_debug_send(self, cmd_ctx: Any) -> None:
        """Handle /debug send — send a sticker by ID or at random."""
        import random as _random
        import re as _re

        from nonebot.adapters.onebot.v11 import Message

        from services.tools.context import ToolContext
        from services.tools.sticker_tools import SendStickerTool

        ctx = self._ctx
        if ctx is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("系统未就绪"))
            return

        store = ctx.sticker_store
        if store is None or not store.list_all():
            await cmd_ctx.bot.send(cmd_ctx.event, Message("表情包库为空，无法发送"))
            return

        args = cmd_ctx.args.strip()
        tool_ctx_obj = ToolContext(
            bot=cmd_ctx.bot,
            user_id=str(cmd_ctx.user_id),
            group_id=str(cmd_ctx.group_id) if cmd_ctx.group_id else None,
            session_id=f"group_{cmd_ctx.group_id}" if cmd_ctx.group_id else f"private_{cmd_ctx.user_id}",
        )

        # stk_id in args → send specific
        match = _re.search(r"stk_[a-f0-9]{8}", args)
        if match:
            stk_id = match.group(0)
            tool = SendStickerTool(store, runtime_state=getattr(ctx, "runtime_state", None))
            result = await tool.execute(tool_ctx_obj, sticker_id=stk_id)
            logger.info("debug direct send_sticker (by id) | id={} result={}", stk_id, result)
            await cmd_ctx.bot.send(cmd_ctx.event, Message(f"[send_sticker] {result}"))
            return

        # Filter by format
        want_gif = any(kw in args for kw in ("gif", "GIF", "动图", "动态"))
        all_stickers = store.list_all()

        if want_gif:
            candidates = {sid: e for sid, e in all_stickers.items() if e.get("file", "").endswith(".gif")}
            if not candidates:
                await cmd_ctx.bot.send(cmd_ctx.event, Message("库中没有动图表情包"))
                return
        else:
            candidates = all_stickers

        stk_id = _random.choice(list(candidates.keys()))
        tool = SendStickerTool(store, runtime_state=getattr(ctx, "runtime_state", None))
        result = await tool.execute(tool_ctx_obj, sticker_id=stk_id)
        logger.info("debug direct send_sticker | id={} result={}", stk_id, result)
        await cmd_ctx.bot.send(cmd_ctx.event, Message(f"[send_sticker] {result}"))

    async def _handle_debug_split(self, cmd_ctx: Any) -> None:
        """Handle /debug split — test reply segmentation on arbitrary text."""
        from nonebot.adapters.onebot.v11 import Message

        from services.llm.segmentation import ReplySegmentationConfig, segment_reply

        text = cmd_ctx.args.strip()
        cfg = getattr(getattr(self._ctx, "config", None), "reply_segmentation", None)
        if not isinstance(cfg, ReplySegmentationConfig):
            cfg = ReplySegmentationConfig()
        result = segment_reply(text, cfg)
        lines = [
            f"输入: {text}",
            f"分段数: {len(result.segments)}",
            f"策略: {result.strategy}",
            f"切分原因: {', '.join(result.break_reasons) if result.break_reasons else '无'}",
            "---",
        ]
        for i, seg in enumerate(result.segments, 1):
            lines.append(f"[{i}] {seg.text}（{seg.reason}）")

        logger.info("debug split | input_len={} segments={}", len(text), len(result.segments))
        await cmd_ctx.bot.send(cmd_ctx.event, Message("\n".join(lines).strip()))


    async def _handle_plugins(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        bus = cmd_ctx.plugin_ctx.bus
        if bus is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("PluginBus 不可用"))
            return

        plugins = sorted(bus.plugins, key=lambda p: (not p.enabled, p.priority))
        if not plugins:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("（无已加载插件）"))
            return

        enabled_count = sum(1 for p in plugins if p.enabled)
        disabled_count = len(plugins) - enabled_count
        lines: list[str] = [f"插件列表（启用 {enabled_count} / 禁用 {disabled_count}）：", ""]
        for p in plugins:
            status = "启用" if p.enabled else "禁用"
            author = p.author if p.author else "—"
            desc = p.description if p.description else "—"
            lines.append(f"[{status}] [{p.name} v{p.version}] 开发者：{author}")
            lines.append(f"  简介：{desc}")

        reply = "\n".join(lines)
        if len(reply) > self._config.max_reply_chars:
            reply = reply[:self._config.max_reply_chars] + "\n…(截断)"

        await cmd_ctx.bot.send(cmd_ctx.event, Message(reply))
        _log.info("plugins listed | by={} count={}", cmd_ctx.user_id, len(plugins))

    async def _handle_version(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        from services.version import GITHUB_REPO, VERSION, fetch_latest_release, parse_semver

        local = parse_semver(VERSION)
        lines = [f"Omubot v{VERSION}", f"GitHub: https://github.com/{GITHUB_REPO}"]

        release = await fetch_latest_release() if self._config.check_github_updates else None
        if release is None:
            lines.append("")
            if self._config.check_github_updates:
                lines.append("（无法连接 GitHub，未检查更新）")
            else:
                lines.append("（已关闭 GitHub 更新检查）")
        else:
            remote_tag: str = release.get("tag_name", "unknown")
            remote_ver = parse_semver(remote_tag)
            published: str = release.get("published_at", "")[:10]
            body: str = release.get("body", "") or ""

            if remote_ver > local:
                lines.append(f"最新版本: {remote_tag}（发布于 {published}）")
                lines.append("**有可用更新！**")
                if body:
                    first_line = body.strip().split("\n")[0]
                    lines.append(f"更新摘要: {first_line}")
            elif remote_ver == local:
                lines.append(f"最新版本: {remote_tag}（发布于 {published}）")
                lines.append("已是最新版本")
            else:
                lines.append(f"GitHub 最新: {remote_tag}（发布于 {published}）")
                lines.append("本地版本领先（开发版）")

        reply = "\n".join(lines)
        if len(reply) > self._config.max_reply_chars:
            reply = reply[:self._config.max_reply_chars] + "\n…(截断)"
        await cmd_ctx.bot.send(cmd_ctx.event, Message(reply))
        _log.info("version checked | by={} local={}", cmd_ctx.user_id, VERSION)
