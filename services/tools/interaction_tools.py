"""QQ interaction outbound tools for NapCat-specific actions."""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any

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
from services.tools.base import Tool
from services.tools.context import ToolContext

_ERROR_DISABLED = "当前拟人化档位未开启 QQ 出站交互"
_POKE_PARAMS = {"type": "object", "properties": {"user_id": {"type": "string", "description": "目标用户 QQ 号"}, "group_id": {"type": "string", "description": "群号；群聊中可省略"}}, "required": ["user_id"]}  # noqa: E501
_REACT_PARAMS = {"type": "object", "properties": {"message_id": {"type": "string", "description": "目标消息 ID"}, "emoji_code": {"type": "string", "description": "QQ 表情编码"}}, "required": ["message_id", "emoji_code"]}  # noqa: E501
_ONEBOT_MESSAGE_REF_RE = re.compile(
    r"^onebot:(?:group|user):[1-9][0-9]{0,31}:message:[1-9][0-9]{0,31}$"
)
_BUCKETS: dict[tuple[str, str], deque[tuple[float, object]]] = defaultdict(deque)


def _reserve(rules: list[tuple[str, str, float, int]]) -> tuple[object, list[tuple[str, str]]] | None:
    now = time.monotonic()
    token = object()
    keys: list[tuple[str, str]] = []
    for kind, scope, window, limit in rules:
        key = (kind, scope)
        q = _BUCKETS[key]
        while q and now - q[0][0] >= window:
            q.popleft()
        if len(q) >= limit:
            _release(token, keys)
            return None
        q.append((now, token))
        keys.append(key)
    return token, keys


def _release(token: object, keys: list[tuple[str, str]]) -> None:
    for key in keys:
        q = _BUCKETS.get(key)
        if q:
            _BUCKETS[key] = deque(item for item in q if item[1] is not token)


def reset_interaction_tool_limits() -> None:
    _BUCKETS.clear()


def _value(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _flag(obj: Any, action: str) -> bool | None:
    for name in (f"qq_interactions_{action}_outbound_enabled", f"{action}_outbound_enabled"):
        value = _value(obj, name)
        if value is not None:
            return bool(value)
    return None


def _passive(extra: dict[str, Any]) -> bool:
    return bool(extra.get("qq_interaction_passive") or extra.get("interaction_passive")
                or extra.get("trigger_mode") == "qq_interaction")


def _allowed(ctx: ToolContext, action: str) -> str | None:
    explicit = _flag(ctx.extra, action)
    resolved = ctx.extra.get("resolved_humanization") or ctx.extra.get("humanization")
    if explicit is None and resolved is not None:
        explicit = _flag(resolved, action)
    if explicit is not None:
        return None if explicit else _ERROR_DISABLED
    profile = str(ctx.extra.get("humanization_profile") or ctx.extra.get("profile") or "").strip().lower()
    if profile == "performance" or (profile == "balanced" and _passive(ctx.extra)):
        return None
    return _ERROR_DISABLED


def _registration_enabled(resolved: Any | None, action: str, profile: str | None, passive: bool) -> bool:
    explicit = _flag(resolved, action) if resolved is not None else None
    if explicit is not None:
        return explicit
    mode = str(profile or "").strip().lower()
    return mode == "performance" or (mode == "balanced" and passive)


async def _call_reserved(
    ctx: ToolContext,
    rules: list[tuple[str, str, float, int]],
    api: str,
    payload: dict[str, Any],
    *,
    limited: str,
    failed: str,
) -> str | None:
    reserved = _reserve(rules)
    if reserved is None:
        return limited
    token, keys = reserved
    try:
        await ctx.bot.call_api(api, **payload)
    except asyncio.CancelledError:
        _release(token, keys)
        raise
    except Exception:
        _release(token, keys)
        if ctx.run_id:
            raise
        return failed
    return None


class QQInteractionTool(Tool):
    def __init__(self, action: str) -> None:
        self._action = action

    @property
    def name(self) -> str:
        return "poke_user" if self._action == "poke" else "react_to_message"

    @property
    def description(self) -> str:
        if self._action == "poke":
            return "向指定 QQ 用户发送一次戳一戳。只在高亲密度、低频、合适时机使用。"
        return "给一条 QQ 消息添加表情回应。适合低频表达轻量态度。"

    @property
    def parameters(self) -> dict[str, Any]:
        return _POKE_PARAMS if self._action == "poke" else _REACT_PARAMS

    @property
    def spec(self) -> ToolSpec:
        scope = (
            "onebot:interaction:poke"
            if self._action == "poke"
            else "onebot:interaction:reaction"
        )
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=dict(self.parameters),
            owner="qq_interaction",
            effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
            required_scopes=(scope,),
            approval=ToolApproval.ALWAYS,
            idempotency=ToolIdempotency.RECONCILE_ONLY,
            retry_policy=ToolRetryPolicy.NEVER,
            concurrency=ToolConcurrency.KEYED_SERIAL,
            binding_required=True,
            data_classification=("qq_interaction",),
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        if ctx.bot is None:
            raise ValueError(f"{self.name} requires a trusted OneBot bot")
        if error := _allowed(ctx, self._action):
            raise ValueError(error)
        if self._action == "reaction":
            message_id = self._positive_id(
                arguments.get("message_id"),
                field="message_id",
            )
            emoji_code = self._positive_id(
                arguments.get("emoji_code"),
                field="emoji_code",
            )
            message_ref = self._trusted_message_ref(ctx, message_id)
            target = f"{message_ref}:reaction:{emoji_code}"
            return ToolInvocationBinding(
                target_ref=target,
                concurrency_key=target,
            )
        user_id = self._positive_id(arguments.get("user_id"), field="user_id")
        trusted_group = str(ctx.group_id or "").strip()
        claimed_group = str(arguments.get("group_id") or "").strip()
        if trusted_group:
            group_id = self._positive_id(trusted_group, field="group_id")
            if claimed_group:
                claimed = self._positive_id(claimed_group, field="group_id")
                if claimed != group_id:
                    raise ValueError("poke_user group_id must match trusted group")
            target = f"onebot:group:{group_id}:user:{user_id}:poke"
        else:
            if claimed_group:
                raise ValueError("private poke_user cannot claim a group_id")
            target = f"onebot:user:{user_id}:poke"
        return ToolInvocationBinding(target_ref=target, concurrency_key=target)

    @classmethod
    def _trusted_message_ref(
        cls,
        ctx: ToolContext,
        message_id: str,
    ) -> str:
        if str(ctx.group_id or "").strip():
            group_id = cls._positive_id(ctx.group_id, field="group_id")
            expected = f"onebot:group:{group_id}:message:{message_id}"
        else:
            user_id = cls._positive_id(ctx.user_id, field="user_id")
            expected = f"onebot:user:{user_id}:message:{message_id}"
        raw_refs = ctx.extra.get("onebot_message_refs")
        if not isinstance(raw_refs, (list, tuple, set, frozenset)):
            raise ValueError("trusted OneBot message refs are missing")
        trusted_refs: set[str] = set()
        for item in raw_refs:
            message_ref = str(item or "").strip()
            if _ONEBOT_MESSAGE_REF_RE.fullmatch(message_ref) is None:
                raise ValueError("trusted OneBot message refs are invalid")
            trusted_refs.add(message_ref)
        if expected not in trusted_refs:
            raise ValueError("message_id is not trusted for this run")
        return expected

    @staticmethod
    def _positive_id(value: Any, *, field: str) -> str:
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
        if err := _allowed(ctx, self._action):
            return err
        if not ctx.bot:
            return "Bot 不可用"
        if self._action == "poke":
            user_id = str(kwargs["user_id"]).strip()
            group_id = str(kwargs.get("group_id") or ctx.group_id or "").strip()
            rules = [("poke_out_user", user_id, 300.0, 1)]
            if group_id:
                rules.append(("poke_out_group", group_id, 60.0, 2))
            payload: dict[str, Any] = {"user_id": int(user_id)}
            if group_id:
                payload["group_id"] = int(group_id)
            err = await _call_reserved(ctx, rules, "send_poke", payload, limited="戳一戳过于频繁，已跳过", failed="戳一戳发送失败")  # noqa: E501
            return err or f"已戳 {user_id}"
        trusted_group_id = str(ctx.group_id or "").strip()
        if trusted_group_id:
            trusted_group_id = self._positive_id(
                trusted_group_id,
                field="group_id",
            )
            self._assert_group_outbound_allowed(ctx, trusted_group_id)
        scope = str(trusted_group_id or ctx.extra.get("group_id") or "global")
        payload = {"message_id": int(str(kwargs["message_id"]).strip()), "emoji_id": str(kwargs["emoji_code"]).strip()}
        err = await _call_reserved(
            ctx, [("react_out_group", scope, 60.0, 3)], "set_msg_emoji_like", payload,
            limited="表情回应过于频繁，已跳过", failed="表情回应发送失败",
        )
        return err or "已添加表情回应"

    @staticmethod
    def _assert_group_outbound_allowed(
        ctx: ToolContext,
        group_id: str,
    ) -> None:
        try:
            checker = vars(ctx.bot).get(
                "_omubot_assert_group_outbound_allowed"
            )
        except TypeError:
            checker = None
        if not callable(checker):
            if not ctx.run_id:
                return
            raise ToolExecutionError(
                code="onebot_group_guard_unavailable",
                safe_message="OneBot group policy guard is unavailable",
                external_effect_started=False,
            )
        try:
            checker(int(group_id), action="set_msg_emoji_like")
        except Exception as exc:
            if not ctx.run_id:
                raise
            raise ToolExecutionError(
                code="onebot_group_policy_denied",
                safe_message="OneBot group policy rejected the reaction",
                external_effect_started=False,
            ) from exc


def build_interaction_tools(
    *, resolved_humanization: Any | None = None, profile: str | None = None, passive: bool = False
) -> list[Tool]:
    return [
        QQInteractionTool(action) for action in ("poke", "reaction")
        if _registration_enabled(resolved_humanization, action, profile, passive)
    ]
