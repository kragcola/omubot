"""JSON API: protocol health and safe OneBot capability probes."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

_STATIC_CAPABILITIES = [
    {
        "key": "history_messages",
        "label": "历史消息",
        "status": "unchecked",
        "detail": "需要协议端支持 get_group_msg_history，默认不主动调用。",
    },
    {
        "key": "image_message",
        "label": "图片消息",
        "status": "configured",
        "detail": "Omubot 通过 OneBot 消息段接收，具体发送能力由协议端决定。",
    },
    {
        "key": "voice_message",
        "label": "语音消息",
        "status": "unchecked",
        "detail": "当前探测不会发送测试语音，避免污染群聊。",
    },
    {
        "key": "group_admin",
        "label": "群管理",
        "status": "unchecked",
        "detail": "不执行踢人/禁言等破坏性探测。",
    },
    {
        "key": "poke",
        "label": "戳一戳",
        "status": "unchecked",
        "detail": "不主动触发交互式动作。",
    },
]

_COMPATIBILITY_CHECKLIST = [
    {
        "key": "onebot_v11_http_ws",
        "label": "OneBot v11 HTTP / WS",
        "napcat": "supported",
        "llonebot": "compatible",
        "detail": "Omubot 默认按 OneBot v11 事件与 API 使用，NapCat 为主适配目标。",
    },
    {
        "key": "login_info_group_list",
        "label": "登录信息 / 群列表",
        "napcat": "supported",
        "llonebot": "compatible",
        "detail": "健康探测会安全调用 get_login_info 与 get_group_list。",
    },
    {
        "key": "group_history",
        "label": "群历史消息",
        "napcat": "conditional",
        "llonebot": "conditional",
        "detail": "历史加载依赖 get_group_msg_history，具体可用性由协议端与账号权限决定。",
    },
    {
        "key": "media_receive",
        "label": "图片 / 表情接收",
        "napcat": "supported",
        "llonebot": "compatible",
        "detail": "Omubot 主要依赖 OneBot message segment 与 URL/file 字段处理媒体。",
    },
    {
        "key": "destructive_actions",
        "label": "群管理破坏性动作",
        "napcat": "manual",
        "llonebot": "manual",
        "detail": "禁言、踢人等动作不在系统页自动探测，只由工具调用或管理员显式操作触发。",
    },
    {
        "key": "interactive_actions",
        "label": "戳一戳 / 语音等交互动作",
        "napcat": "unchecked",
        "llonebot": "unchecked",
        "detail": "为避免污染群聊，当前只做兼容清单提示，不主动发送测试事件。",
    },
]


def create_protocol_router(
    *,
    config: Any = None,
    ctx: Any = None,
    bot: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _trace_store() -> Any:
        return getattr(ctx, "protocol_trace", None) if ctx is not None else None

    def _connection_history() -> Any:
        return getattr(ctx, "protocol_connections", None) if ctx is not None else None

    def _bots() -> list[Any]:
        bots: list[Any] = []
        try:
            import nonebot

            runtime_bots = nonebot.get_bots()
            if isinstance(runtime_bots, dict):
                bots.extend(runtime_bots.values())
        except Exception:
            pass
        if bot is not None and bot not in bots:
            bots.append(bot)
        ctx_bot = getattr(ctx, "bot", None) if ctx is not None else None
        if ctx_bot is not None and ctx_bot not in bots:
            bots.append(ctx_bot)
        return bots

    def _base_payload() -> dict[str, Any]:
        napcat = getattr(config, "napcat", None)
        bots = _bots()
        self_ids = [str(getattr(item, "self_id", "") or "") for item in bots]
        connection = _record_connection_snapshot(
            connected_bots=len(bots),
            self_ids=self_ids,
            source="protocol_health",
        )
        return {
            "adapter": "napcat",
            "api_url": getattr(napcat, "api_url", ""),
            "connected_bots": len(bots),
            "checked_at": time.time(),
            "compatibility": _COMPATIBILITY_CHECKLIST,
            "connection": connection,
        }

    def _record_connection_snapshot(
        *,
        connected_bots: int,
        self_ids: list[str],
        source: str,
        error: str = "",
    ) -> dict[str, Any]:
        history = _connection_history()
        if history is not None and hasattr(history, "record_snapshot"):
            return history.record_snapshot(
                connected_bots=connected_bots,
                self_ids=self_ids,
                source=source,
                error=error,
            )
        status = "connected" if connected_bots else "disconnected"
        return {
            "current_status": status,
            "connected_bots": connected_bots,
            "self_ids": self_ids,
            "changed_at": 0.0,
            "last_seen_at": time.time(),
            "disconnected_since": 0.0,
            "last_recovery_seconds": None,
            "last_error": error,
            "event_count": 0,
        }

    def _record_connection_error(error: str, *, source: str) -> dict[str, Any]:
        history = _connection_history()
        if history is not None and hasattr(history, "record_error"):
            return history.record_error(error, source=source)
        connection = _base_payload()["connection"]
        connection["last_error"] = error
        return connection

    async def _call_safe(runtime_bot: Any, method_name: str) -> tuple[bool, str, Any]:
        method = getattr(runtime_bot, method_name, None)
        if method is None:
            return False, "method_missing", None
        try:
            result = await method()
            return True, "ok", result
        except Exception as e:
            return False, str(e), None

    @router.get("/protocol/health")
    async def protocol_health():
        payload = _base_payload()
        trace = _trace_store()
        if trace is not None and hasattr(trace, "summary"):
            payload["trace_summary"] = trace.summary()
        payload["capabilities"] = [
            {
                "key": "bot_connection",
                "label": "Bot 连接",
                "status": "ok" if payload["connected_bots"] else "failed",
                "detail": f"{payload['connected_bots']} 个 Bot 连接",
            },
            *_STATIC_CAPABILITIES,
        ]
        return payload

    @router.get("/protocol/compatibility")
    async def protocol_compatibility():
        return {
            "adapter": "napcat",
            "fallback_target": "llonebot",
            "items": _COMPATIBILITY_CHECKLIST,
        }

    @router.get("/protocol/traces")
    async def protocol_traces(limit: int = 30):
        trace = _trace_store()
        if trace is None or not hasattr(trace, "as_payload"):
            return {
                "summary": {"total": 0, "ok": 0, "failed": 0, "pending": 0, "avg_elapsed_ms": 0.0},
                "traces": [],
                "max_items": 0,
            }
        return trace.as_payload(limit=limit)

    @router.get("/protocol/connections")
    async def protocol_connections(limit: int = 20):
        history = _connection_history()
        if history is None or not hasattr(history, "as_payload"):
            payload = _base_payload()
            return {
                "summary": payload["connection"],
                "events": [],
                "max_items": 0,
            }
        return history.as_payload(limit=limit)

    @router.post("/protocol/probe")
    async def protocol_probe():
        payload = _base_payload()
        bots = _bots()
        if not bots:
            payload["capabilities"] = [
                {
                    "key": "bot_connection",
                    "label": "Bot 连接",
                    "status": "failed",
                    "detail": "当前 NoneBot 没有已连接 Bot。",
                },
                *_STATIC_CAPABILITIES,
            ]
            return payload

        runtime_bot = bots[0]
        login_ok, login_detail, login_result = await _call_safe(runtime_bot, "get_login_info")
        group_ok, group_detail, group_result = await _call_safe(runtime_bot, "get_group_list")
        group_count = len(group_result) if isinstance(group_result, list) else 0
        probe_errors = [detail for ok, detail in ((login_ok, login_detail), (group_ok, group_detail)) if not ok]
        if probe_errors:
            payload["connection"] = _record_connection_error("; ".join(probe_errors), source="protocol_probe")

        payload["capabilities"] = [
            {
                "key": "bot_connection",
                "label": "Bot 连接",
                "status": "ok",
                "detail": f"self_id={getattr(runtime_bot, 'self_id', '') or 'unknown'}",
            },
            {
                "key": "login_info",
                "label": "登录信息",
                "status": "ok" if login_ok else "failed",
                "detail": login_detail if not login_ok else str(login_result or {})[:160],
            },
            {
                "key": "group_list",
                "label": "群列表",
                "status": "ok" if group_ok else "failed",
                "detail": f"{group_count} 个群" if group_ok else group_detail,
            },
            *_STATIC_CAPABILITIES,
        ]
        return payload

    return router
