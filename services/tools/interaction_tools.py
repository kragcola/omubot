"""QQ interaction outbound tools for NapCat-specific actions."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

from services.tools.base import Tool
from services.tools.context import ToolContext

_ERROR_DISABLED = "当前拟人化档位未开启 QQ 出站交互"
_POKE_PARAMS = {"type": "object", "properties": {"user_id": {"type": "string", "description": "目标用户 QQ 号"}, "group_id": {"type": "string", "description": "群号；群聊中可省略"}}, "required": ["user_id"]}  # noqa: E501
_REACT_PARAMS = {"type": "object", "properties": {"message_id": {"type": "string", "description": "目标消息 ID"}, "emoji_code": {"type": "string", "description": "QQ 表情编码"}}, "required": ["message_id", "emoji_code"]}  # noqa: E501
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
        scope = str(ctx.group_id or ctx.extra.get("group_id") or "global")
        payload = {"message_id": int(str(kwargs["message_id"]).strip()), "emoji_id": str(kwargs["emoji_code"]).strip()}
        err = await _call_reserved(
            ctx, [("react_out_group", scope, 60.0, 3)], "set_msg_emoji_like", payload,
            limited="表情回应过于频繁，已跳过", failed="表情回应发送失败",
        )
        return err or "已添加表情回应"


def build_interaction_tools(
    *, resolved_humanization: Any | None = None, profile: str | None = None, passive: bool = False
) -> list[Tool]:
    return [
        QQInteractionTool(action) for action in ("poke", "reaction")
        if _registration_enabled(resolved_humanization, action, profile, passive)
    ]
