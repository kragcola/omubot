"""群管理工具：通过 OneBot API 执行群管理操作，需要 SUPERUSER 权限。"""

from collections.abc import Mapping
from typing import Any

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


def _check_auth(ctx: ToolContext, superusers: set[str]) -> str | None:
    """鉴权检查。返回 None 表示通过，否则返回错误信息。"""
    if not ctx.bot or not ctx.group_id:
        return "此操作仅在群聊中可用"
    if ctx.user_id not in superusers:
        return "权限不足: 仅管理员可执行此操作"
    return None


def _positive_qq_id(value: Any, *, field_name: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive QQ id")
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 32
        or not text.isascii()
        or not text.isdigit()
        or int(text) <= 0
    ):
        raise ValueError(f"{field_name} must be a positive QQ id")
    return str(int(text))


def _trusted_admin(ctx: ToolContext, superusers: set[str]) -> str:
    requester = str(ctx.user_id or "").strip()
    if ctx.bot is None:
        raise ValueError("group admin tool requires a trusted OneBot bot")
    if not requester or requester not in superusers:
        raise ValueError("group admin tool requires a trusted superuser")
    return requester


def _external_spec(
    tool: Tool,
    *,
    effect: ToolEffect,
    scope: str,
    classification: str,
) -> ToolSpec:
    return ToolSpec(
        name=tool.name,
        description=tool.description,
        input_schema=dict(tool.parameters),
        owner="group_admin",
        effect=effect,
        required_scopes=(scope,),
        approval=ToolApproval.ALWAYS,
        idempotency=ToolIdempotency.RECONCILE_ONLY,
        retry_policy=ToolRetryPolicy.NEVER,
        concurrency=ToolConcurrency.KEYED_SERIAL,
        binding_required=True,
        data_classification=(classification,),
    )


class MuteUserTool(Tool):
    def __init__(
        self,
        superusers: set[str],
        *,
        default_duration: int = 60,
        max_duration: int = 2592000,
    ) -> None:
        self._superusers = superusers
        self._default_duration = max(0, int(default_duration))
        self._max_duration = max(0, int(max_duration))

    @property
    def name(self) -> str:
        return "mute_user"

    @property
    def description(self) -> str:
        return "在群里禁言指定用户。duration=0 表示解除禁言。需要机器人是管理员且请求者是 SUPERUSER。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "要禁言的用户 QQ 号"},
                "duration": {
                    "type": "integer",
                    "description": f"禁言时长（秒），0=解除禁言，默认{self._default_duration}",
                    "default": self._default_duration,
                    "minimum": 0,
                    "maximum": self._max_duration,
                },
            },
            "required": ["user_id"],
        }

    @property
    def spec(self) -> ToolSpec:
        return _external_spec(
            self,
            effect=ToolEffect.EXTERNAL_REVERSIBLE,
            scope="onebot:group:moderate",
            classification="qq_group_state",
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        _trusted_admin(ctx, self._superusers)
        group_id = _positive_qq_id(ctx.group_id, field_name="group_id")
        user_id = _positive_qq_id(arguments.get("user_id"), field_name="user_id")
        target = f"onebot:group:{group_id}:member:{user_id}:mute"
        return ToolInvocationBinding(target_ref=target, concurrency_key=target)

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        if err := _check_auth(ctx, self._superusers):
            return err
        assert ctx.group_id is not None
        user_id: str = kwargs["user_id"]
        duration: int = int(kwargs.get("duration", self._default_duration))
        if self._max_duration and duration > self._max_duration:
            duration = self._max_duration
        await ctx.bot.set_group_ban(group_id=int(ctx.group_id), user_id=int(user_id), duration=duration)
        if duration == 0:
            return f"已解除 {user_id} 的禁言"
        return f"已禁言 {user_id} {duration}秒"


class SetTitleTool(Tool):
    def __init__(self, superusers: set[str]) -> None:
        self._superusers = superusers

    @property
    def name(self) -> str:
        return "set_title"

    @property
    def description(self) -> str:
        return "设置群成员的专属头衔。需要机器人是群主且请求者是 SUPERUSER。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "目标用户 QQ 号"},
                "title": {"type": "string", "description": "专属头衔内容，空字符串=清除头衔"},
            },
            "required": ["user_id", "title"],
        }

    @property
    def spec(self) -> ToolSpec:
        return _external_spec(
            self,
            effect=ToolEffect.EXTERNAL_REVERSIBLE,
            scope="onebot:group:title",
            classification="qq_group_state",
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        _trusted_admin(ctx, self._superusers)
        group_id = _positive_qq_id(ctx.group_id, field_name="group_id")
        user_id = _positive_qq_id(arguments.get("user_id"), field_name="user_id")
        target = f"onebot:group:{group_id}:member:{user_id}:title"
        return ToolInvocationBinding(target_ref=target, concurrency_key=target)

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        if err := _check_auth(ctx, self._superusers):
            return err
        assert ctx.group_id is not None
        user_id: str = kwargs["user_id"]
        title: str = kwargs["title"]
        await ctx.bot.set_group_special_title(
            group_id=int(ctx.group_id), user_id=int(user_id), special_title=title
        )
        return f"已设置 {user_id} 的头衔为 '{title}'" if title else f"已清除 {user_id} 的头衔"


class SendGroupMsgTool(Tool):
    def __init__(self, superusers: set[str]) -> None:
        self._superusers = superusers

    @property
    def name(self) -> str:
        return "send_group_msg"

    @property
    def description(self) -> str:
        return "主动向指定群发送一条消息。用于通知、提醒等场景。需要请求者是 SUPERUSER。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "目标群号"},
                "message": {"type": "string", "description": "要发送的消息内容"},
            },
            "required": ["group_id", "message"],
        }

    @property
    def spec(self) -> ToolSpec:
        return _external_spec(
            self,
            effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
            scope="onebot:group:message",
            classification="qq_message",
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        _trusted_admin(ctx, self._superusers)
        group_id = _positive_qq_id(arguments.get("group_id"), field_name="group_id")
        message = arguments.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ValueError("send_group_msg message must be 1..4000 characters")
        target = f"onebot:group:{group_id}:message"
        return ToolInvocationBinding(target_ref=target, concurrency_key=target)

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        if not ctx.bot:
            return "Bot 不可用"
        if ctx.user_id not in self._superusers:
            return "权限不足: 仅管理员可执行此操作"
        group_id: str = kwargs["group_id"]
        message: str = kwargs["message"]
        await ctx.bot.send_group_msg(group_id=int(group_id), message=message)
        return f"已发送消息到群 {group_id}"
