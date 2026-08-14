"""Sticker tools: save, send, and manage stickers in the library."""

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from kernel.types import (
    ToolApproval,
    ToolConcurrency,
    ToolEffect,
    ToolExecutionError,
    ToolIdempotency,
    ToolInvocationBinding,
    ToolRetryPolicy,
    ToolSpec,
)
from services.humanization import STICKER_RECENT_USED_SLOT, humanization_source
from services.media.sticker_store import StickerStore
from services.system_module import Scope
from services.tools.base import Tool
from services.tools.context import ToolContext

_RECENT_STICKER_LIMIT = 12
_RECENT_STICKER_TTL = timedelta(minutes=30)
_STICKER_ID_RE = re.compile(r"^stk_[0-9a-f]{8}$")
_SEND_STICKER_TARGET_RE = re.compile(
    r"^onebot:(group|user):([1-9][0-9]{0,31}):"
    r"sticker:(stk_[0-9a-f]{8}):send$"
)


class SaveStickerTool(Tool):
    """Save an image from the conversation to the sticker library."""

    def __init__(self, store: StickerStore, superusers: set[str]) -> None:
        self._store = store
        self._superusers = superusers

    @property
    def name(self) -> str:
        return "save_sticker"

    @property
    def description(self) -> str:
        return (
            "收录一张对话中的图片到你的表情包库。"
            "image_tag 使用图片旁边的 «img:N» 标签。"
            "两种场景可以调用：① 管理员明确要求收录时，把管理员QQ号填入 requested_by；"
            "② 你自己觉得这张表情有趣、好用、符合你的性格、且清楚使用场景时，主动收录，"
            "此时 requested_by 留空。"
            "只在你完全理解图片含义、清楚使用场景时才调用，不要收录看不懂或低质的图。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_tag": {
                    "type": "string",
                    "description": "对话中图片的标签，如 img:3",
                },
                "description": {
                    "type": "string",
                    "description": "表情包内容描述",
                },
                "usage_hint": {
                    "type": "string",
                    "description": "适合使用该表情包的场景说明",
                },
                "requested_by": {
                    "type": "string",
                    "description": "发起请求的用户QQ号（管理员要求收录时必填；你自己主动收录时留空）",
                },
            },
            "required": ["image_tag", "description", "usage_hint"],
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=dict(self.parameters),
            owner="sticker",
            effect=ToolEffect.WRITE_LOCAL,
            required_scopes=("sticker:write",),
            approval=ToolApproval.POLICY,
            idempotency=ToolIdempotency.RECONCILE_ONLY,
            retry_policy=ToolRetryPolicy.NEVER,
            concurrency=ToolConcurrency.KEYED_SERIAL,
            binding_required=True,
            data_classification=("local_sticker",),
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        image_tag = str(arguments.get("image_tag") or "").strip()
        image_tags = ctx.extra.get("image_tags")
        if not isinstance(image_tags, Mapping) or image_tag not in image_tags:
            raise ValueError("save_sticker image_tag must exist in trusted image_tags")
        requested_by = str(arguments.get("requested_by") or "").strip()
        trusted_user = str(ctx.user_id or "").strip()
        if requested_by and requested_by != trusted_user:
            raise ValueError(
                "save_sticker requested_by must equal trusted user_id when nonempty"
            )
        target = "sticker:library"
        return ToolInvocationBinding(target_ref=target, concurrency_key=target)

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        image_tag: str = kwargs["image_tag"]
        description: str = kwargs["description"]
        usage_hint: str = kwargs["usage_hint"]
        requested_by: str = str(kwargs.get("requested_by") or "")

        user_is_admin = bool(ctx.user_id) and ctx.user_id in self._superusers
        requested_is_admin = bool(requested_by) and requested_by in self._superusers

        # Source resolution:
        #   - explicit admin caller, or group chat where the request came from admin
        #     -> trusted "admin" save
        #   - bot proactive (no named requester): bot decided it likes the sticker
        #     -> "stolen"
        #   - a named non-admin requester is rejected (don't let group members
        #     coax the bot into saving arbitrary images on command)
        if user_is_admin or (not ctx.user_id and requested_is_admin):
            source = "admin"
        elif requested_is_admin or not requested_by:
            source = "stolen"
        else:
            return "只有管理员可以指名要求收录表情包"

        tag_map: dict[str, str] = ctx.extra.get("image_tags", {})
        path_str = tag_map.get(image_tag)
        if not path_str:
            return f"图片标签不存在: {image_tag}（可用标签: {', '.join(tag_map) or '无'}）"

        path = Path(path_str)
        if not path.exists():
            return f"图片文件已过期: {image_tag}"

        image_data = path.read_bytes()

        try:
            stk_id, is_new = self._store.add(image_data, description, usage_hint, source=source)
        except ValueError as e:
            return f"无法收录: {e}"

        if not is_new:
            return f"表情包已存在: {stk_id}"

        timeline = ctx.extra.get("timeline")
        if timeline and ctx.group_id:
            timeline.add(
                ctx.group_id,
                role="user",
                speaker="【系统】",
                content=f"新增表情包 «表情包:{stk_id}» {description} | {usage_hint}",
            )

        return f"{stk_id} 已收录"


class ManageStickerTool(Tool):
    """Update or delete stickers in the library."""

    def __init__(self, store: StickerStore, superusers: set[str]) -> None:
        self._store = store
        self._superusers = superusers

    @property
    def name(self) -> str:
        return "manage_sticker"

    @property
    def description(self) -> str:
        return (
            "仅管理员可管理表情包库：更新表情包的描述/场景说明，或删除表情包。"
            "必须从对话上下文识别是谁在要求操作，将其QQ号填入 requested_by。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sticker_id": {
                    "type": "string",
                    "description": "���情包 ID，如 stk_a1b2c3d4",
                },
                "action": {
                    "type": "string",
                    "enum": ["update", "delete"],
                    "description": "操作类型",
                },
                "requested_by": {
                    "type": "string",
                    "description": "发起请求的用户QQ号（群聊中从消息上下文提取）",
                },
                "description": {
                    "type": "string",
                    "description": "新的内容描述（仅 update 时使用）",
                },
                "usage_hint": {
                    "type": "string",
                    "description": "新的场景说明（仅 update 时使��）",
                },
            },
            "required": ["sticker_id", "action", "requested_by"],
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=dict(self.parameters),
            owner="sticker",
            effect=ToolEffect.WRITE_LOCAL,
            required_scopes=("sticker:write",),
            approval=ToolApproval.POLICY,
            idempotency=ToolIdempotency.RECONCILE_ONLY,
            retry_policy=ToolRetryPolicy.NEVER,
            concurrency=ToolConcurrency.KEYED_SERIAL,
            binding_required=True,
            data_classification=("local_sticker",),
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        sticker_id = str(arguments.get("sticker_id") or "").strip()
        if not sticker_id:
            raise ValueError("manage_sticker requires sticker_id")
        requested_by = str(arguments.get("requested_by") or "").strip()
        trusted_user = str(ctx.user_id or "").strip()
        if not trusted_user or trusted_user not in self._superusers:
            raise ValueError("manage_sticker requires a trusted superuser")
        if requested_by != trusted_user:
            raise ValueError(
                "manage_sticker requested_by must equal trusted user_id"
            )
        target = f"sticker:{sticker_id}"
        return ToolInvocationBinding(target_ref=target, concurrency_key=target)

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        sticker_id: str = kwargs["sticker_id"]
        action: str = kwargs["action"]
        trusted_user = str(ctx.user_id or "").strip()
        if not trusted_user or trusted_user not in self._superusers:
            return "只有管理员可以管理表情包"

        if action == "delete":
            if self._store.remove(sticker_id):
                return f"{sticker_id} 已删除"
            return f"表情包不存在: {sticker_id}"

        if action == "update":
            description: str | None = kwargs.get("description")
            usage_hint: str | None = kwargs.get("usage_hint")
            if description is None and usage_hint is None:
                return "请提供 description 或 usage_hint"
            if self._store.update(sticker_id, description, usage_hint):
                return f"{sticker_id} 已更新"
            return f"表情包不存在: {sticker_id}"

        return f"未知操作: {action}"


class SendStickerTool(Tool):
    """Send a sticker from the library as a standalone image message."""

    def __init__(self, store: StickerStore, runtime_state: Any = None) -> None:
        self._store = store
        self._runtime_state = runtime_state

    @property
    def name(self) -> str:
        return "send_sticker"

    @property
    def description(self) -> str:
        return (
            "发送一张表情包（作为单独的图片消息）。"
            "可二选一：给 sticker_id 精确发送某张；或给 intent 用文字描述想要的表情"
            "（如「告别」「开心」「无语」），由系统按语义检索库里最贴合的一张发送。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sticker_id": {
                    "type": "string",
                    "description": "表情包 ID，如 stk_a1b2c3d4（与 intent 二选一）",
                },
                "intent": {
                    "type": "string",
                    "description": "想发送的表情意图/情绪，如「挥手告别」「开心」（与 sticker_id 二选一，按语义检索）",
                },
            },
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=dict(self.parameters),
            owner="sticker",
            effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
            required_scopes=("onebot:sticker:send",),
            approval=ToolApproval.ALWAYS,
            idempotency=ToolIdempotency.RECONCILE_ONLY,
            retry_policy=ToolRetryPolicy.NEVER,
            concurrency=ToolConcurrency.KEYED_SERIAL,
            binding_required=True,
            data_classification=("qq_sticker",),
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        if ctx.bot is None:
            raise ValueError("send_sticker requires a trusted OneBot bot")
        recipient_ref = self._recipient_ref(ctx)
        if str(ctx.target_ref or "").strip():
            sticker_id = self._sticker_id_from_durable_target(
                ctx,
                arguments,
            )
            target = str(ctx.target_ref).strip()
            return ToolInvocationBinding(
                target_ref=target,
                concurrency_key=f"{recipient_ref}:sticker-send",
            )
        sticker_id = self._resolve_sticker_id(arguments)
        target = f"{recipient_ref}:sticker:{sticker_id}:send"
        return ToolInvocationBinding(
            target_ref=target,
            concurrency_key=f"{recipient_ref}:sticker-send",
        )

    def _resolve_sticker_id(
        self,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> str:
        sticker_id = str(arguments.get("sticker_id") or "").strip()
        if sticker_id:
            if _STICKER_ID_RE.fullmatch(sticker_id) is None:
                raise ValueError("send_sticker sticker_id is invalid")
        else:
            intent = str(arguments.get("intent") or "").strip()
            if (
                not intent
                or len(intent) > 200
                or any(ord(character) < 32 for character in intent)
            ):
                raise ValueError("send_sticker intent is invalid")
            matches = self._store.search_by_intent(intent, top_k=1)
            if not matches:
                raise ValueError("send_sticker intent did not resolve")
            sticker_id = str(matches[0] or "").strip()
            if _STICKER_ID_RE.fullmatch(sticker_id) is None:
                raise ValueError("send_sticker resolver returned an invalid id")
        if self._store.resolve_path(sticker_id) is None:
            raise ValueError("send_sticker resolved sticker does not exist")
        return sticker_id

    def _sticker_id_from_durable_target(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> str:
        target = str(ctx.target_ref or "").strip()
        match = _SEND_STICKER_TARGET_RE.fullmatch(target)
        if match is None:
            raise ValueError("send_sticker durable target is invalid")
        recipient_ref = f"onebot:{match.group(1)}:{match.group(2)}"
        if recipient_ref != self._recipient_ref(ctx):
            raise ValueError("send_sticker durable recipient changed")
        sticker_id = match.group(3)
        explicit_id = str(arguments.get("sticker_id") or "").strip()
        if explicit_id and explicit_id != sticker_id:
            raise ValueError("send_sticker durable sticker changed")
        if self._store.resolve_path(sticker_id) is None:
            raise ValueError("send_sticker durable sticker no longer exists")
        return sticker_id

    @classmethod
    def _recipient_ref(cls, ctx: ToolContext) -> str:
        if str(ctx.group_id or "").strip():
            group_id = cls._positive_qq_id(ctx.group_id, field="group_id")
            return f"onebot:group:{group_id}"
        user_id = cls._positive_qq_id(ctx.user_id, field="user_id")
        return f"onebot:user:{user_id}"

    @staticmethod
    def _positive_qq_id(value: Any, *, field: str) -> str:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a positive QQ id")
        text = str(value or "").strip()
        if (
            not text
            or len(text) > 32
            or not text.isascii()
            or not text.isdigit()
            or int(text) <= 0
        ):
            raise ValueError(f"{field} must be a positive QQ id")
        return str(int(text))

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        import base64 as _b64

        sticker_id: str = str(kwargs.get("sticker_id") or "").strip()
        intent: str = str(kwargs.get("intent") or "").strip()

        if not ctx.bot:
            return "Bot 不可用"

        if str(ctx.target_ref or "").strip():
            try:
                sticker_id = self._sticker_id_from_durable_target(ctx, kwargs)
            except ValueError as exc:
                raise ToolExecutionError(
                    code="sticker_durable_target_invalid",
                    safe_message="The durable sticker target is invalid",
                    external_effect_started=False,
                ) from exc

        # Resolve by intent (semantic search) when no explicit id is given.
        if not sticker_id and intent:
            matches = self._store.search_by_intent(intent, top_k=1)
            if not matches:
                return f"没有匹配「{intent}」的表情包"
            sticker_id = matches[0]
        if not sticker_id:
            return "请提供 sticker_id 或 intent"

        file_path = self._store.resolve_path(sticker_id)
        if file_path is None:
            if ctx.run_id:
                raise ToolExecutionError(
                    code="sticker_not_found",
                    safe_message="The resolved sticker is unavailable",
                    external_effect_started=False,
                )
            return f"表情包不存在: {sticker_id}"

        from nonebot.adapters.onebot.v11 import MessageSegment

        # Read file as base64 — napcat container can't access bot's filesystem.
        # Must use base64 because bot and napcat are in separate Docker containers.
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
            b64_data = _b64.b64encode(raw).decode()
        except OSError as e:
            logger.error("send_sticker read failed for {}: {}", sticker_id, e)
            if ctx.run_id:
                raise ToolExecutionError(
                    code="sticker_read_failed",
                    safe_message="The resolved sticker could not be read",
                    external_effect_started=False,
                ) from e
            return f"读取表情包失败: {sticker_id}"

        # sub_type=1 → QQ sticker display. Must use snake_case per OneBot v11 spec.
        try:
            img_seg = MessageSegment.image(file=f"base64://{b64_data}")
            img_seg.data["sub_type"] = 1
            img_seg.data["summary"] = "[动画表情]"
        except Exception as e:
            if ctx.run_id:
                raise ToolExecutionError(
                    code="sticker_segment_failed",
                    safe_message="The sticker message could not be prepared",
                    external_effect_started=False,
                ) from e
            raise

        try:
            if ctx.group_id:
                await ctx.bot.send_group_msg(group_id=int(ctx.group_id), message=img_seg)
            else:
                await ctx.bot.send_private_msg(user_id=int(ctx.user_id), message=img_seg)
        except Exception as e:
            if ctx.run_id:
                logger.error(
                    "send_sticker send failed | id={} error_type={}",
                    sticker_id,
                    type(e).__name__,
                )
                raise
            logger.error("send_sticker send failed for {}: {}", sticker_id, e)
            return f"发送失败: {sticker_id}"

        self._store.record_send(sticker_id)
        self._record_recent_sticker(ctx, sticker_id)
        logger.info("[send_sticker ok] id={}", sticker_id)
        return f"已发送 {sticker_id}"

    def _record_recent_sticker(self, ctx: ToolContext, sticker_id: str) -> None:
        if self._runtime_state is None:
            return
        session_id = ctx.session_id or (f"group_{ctx.group_id}" if ctx.group_id else f"private_{ctx.user_id}")
        if not session_id:
            return
        scope = Scope(session_id=session_id, group_id=ctx.group_id, user_id=ctx.user_id)
        existing: list[str] = []
        try:
            snapshot = self._runtime_state.get(STICKER_RECENT_USED_SLOT, scope=scope)
            value = getattr(snapshot, "value", None)
            raw_items = value.get("sticker_ids", []) if isinstance(value, dict) else []
            if isinstance(raw_items, list):
                existing = [str(item).strip() for item in raw_items if str(item).strip()]
        except Exception:
            existing = []
        merged = [sticker_id, *existing]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in merged:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
            if len(deduped) >= _RECENT_STICKER_LIMIT:
                break
        try:
            self._runtime_state.set(
                STICKER_RECENT_USED_SLOT,
                {
                    "sticker_ids": deduped,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                scope=scope,
                source=humanization_source("send_sticker:recent_used"),
                confidence=1.0,
                decay_at=datetime.now() + _RECENT_STICKER_TTL,
            )
        except Exception:
            logger.debug("send_sticker recent-state write skipped | id={}", sticker_id)
