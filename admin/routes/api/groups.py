"""JSON API: groups — list, live state, recent messages, and per-group profile."""

from __future__ import annotations

import contextlib
import copy
import json
import re
import tomllib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from kernel.config import BotConfig, GroupOverride, GroupReplyStyle, GroupStickerMode
from services.group_profile_audit import GroupProfileAuditStore

_SPEAKER_QQ_RE = re.compile(r"\((\d+)\)$")
_EDITABLE_OVERRIDE_FIELDS = (
    "allowed_tools",
    "at_only",
    "blocked_tools",
    "talk_value",
    "planner_smooth",
    "debounce_seconds",
    "batch_size",
    "history_load_count",
    "reply_style",
    "custom_prompt",
    "tools_enabled",
    "sticker_mode",
    "slang_enabled",
)
_AUDIT_LABELS = {
    "blocked_users": "群内屏蔽用户",
    "allowed_tools": "允许工具",
    "blocked_tools": "屏蔽工具",
    "at_only": "回复模式",
    "talk_value": "发言值",
    "planner_smooth": "规划间隔",
    "debounce_seconds": "回复冷却",
    "batch_size": "批量窗口",
    "history_load_count": "历史载入",
    "reply_style": "回复风格",
    "custom_prompt": "群附加提示词",
    "tools_enabled": "工具总开关",
    "sticker_mode": "贴纸策略",
    "slang_enabled": "黑话系统",
}


class GroupProfilePayload(BaseModel):
    blocked_users: list[int] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)
    at_only: bool = False
    talk_value: float = Field(default=0.3, ge=0.0, le=1.0)
    planner_smooth: float = Field(default=3.0, ge=0.0, le=120.0)
    debounce_seconds: float = Field(default=5.0, ge=0.0, le=300.0)
    batch_size: int = Field(default=10, ge=1, le=100)
    history_load_count: int = Field(default=30, ge=1, le=200)
    reply_style: GroupReplyStyle = "default"
    custom_prompt: str = ""
    tools_enabled: bool = True
    sticker_mode: GroupStickerMode = "inherit"
    slang_enabled: bool = True

    @field_validator("custom_prompt")
    @classmethod
    def _normalize_prompt(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("blocked_users", mode="before")
    @classmethod
    def _normalize_blocked_users(cls, value: Any) -> list[int]:
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        normalized: list[int] = []
        for item in value:
            raw = str(item or "").strip()
            if not raw:
                continue
            normalized.append(int(raw))
        return sorted(set(normalized))

    @field_validator("allowed_tools", "blocked_tools", mode="before")
    @classmethod
    def _normalize_tool_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        names = {
            str(item or "").strip()
            for item in value
            if str(item or "").strip()
        }
        return sorted(names)

    @model_validator(mode="after")
    def _remove_tool_overlaps(self) -> "GroupProfilePayload":
        blocked = set(self.blocked_tools)
        if blocked:
            self.allowed_tools = [name for name in self.allowed_tools if name not in blocked]
        return self


def _normalize_json_path(config_path: str) -> Path:
    configured = Path(config_path)
    if configured.suffix.lower() == ".json":
        return configured
    if configured.suffix.lower() == ".toml":
        return configured.with_suffix(".json")
    return configured.with_suffix(".json")


def _read_json(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("配置 JSON 必须是对象")
    return payload


def _read_config_base(target_json_path: Path, fallback_config: Any) -> dict[str, Any]:
    if target_json_path.is_file():
        return _read_json(target_json_path)

    legacy_toml = target_json_path.with_suffix(".toml")
    if legacy_toml.is_file():
        with open(legacy_toml, "rb") as fh:
            payload = tomllib.load(fh)
        if isinstance(payload, dict):
            return payload

    if fallback_config is not None and hasattr(fallback_config, "model_dump"):
        return fallback_config.model_dump(mode="python")
    return {}


def _write_model(target_json_path: Path, model: BotConfig) -> None:
    values = model.model_dump(mode="python")
    target_json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target_json_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target_json_path)


def _override_has_values(override: GroupOverride | None) -> bool:
    if override is None:
        return False
    if getattr(override, "blocked_users", []):
        return True
    return any(getattr(override, field, None) is not None for field in _EDITABLE_OVERRIDE_FIELDS)


def _serialize_override(override: GroupOverride | None) -> dict[str, Any]:
    payload: dict[str, Any] = {field: None for field in _EDITABLE_OVERRIDE_FIELDS}
    payload["blocked_users"] = []
    if override is None:
        return payload
    payload["blocked_users"] = list(getattr(override, "blocked_users", []) or [])
    for field in _EDITABLE_OVERRIDE_FIELDS:
        payload[field] = getattr(override, field, None)
    return payload


def _apply_runtime_group_config(target: Any, source: Any) -> None:
    if target is None or source is None:
        return
    model_fields = getattr(source.__class__, "model_fields", {})
    for field_name in model_fields:
        setattr(target, field_name, copy.deepcopy(getattr(source, field_name)))


def _sorted_str_list(values: Any) -> list[str]:
    names = {
        str(item or "").strip()
        for item in (values or [])
        if str(item or "").strip()
    }
    return sorted(names)


def _normalized_override_values(payload: GroupProfilePayload, base: Any) -> dict[str, Any]:
    values = payload.model_dump(mode="python")
    normalized: dict[str, Any] = {}
    for field, value in values.items():
        base_value = getattr(base, field, None)
        if field in {"allowed_tools", "blocked_tools"}:
            value = _sorted_str_list(value)
            base_value = _sorted_str_list(base_value)
        elif field == "blocked_users":
            value = sorted({int(item) for item in (value or [])})
            base_value = sorted({int(item) for item in (base_value or [])})
        normalized[field] = None if value == base_value else value
    return normalized


def _preview_value(value: Any) -> Any:
    if isinstance(value, str):
        compact = value.strip()
        if len(compact) > 120:
            return compact[:117] + "..."
        return compact
    if isinstance(value, list):
        return value[:12]
    return value


def _profile_audit_snapshot(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "blocked_users": list(group.get("blocked_users", []) or []),
        "allowed_tools": list(group.get("allowed_tools", []) or []),
        "blocked_tools": list(group.get("blocked_tools", []) or []),
        "at_only": bool(group.get("at_only", False)),
        "talk_value": float(group.get("talk_value", 0.0) or 0.0),
        "planner_smooth": float(group.get("planner_smooth", 0.0) or 0.0),
        "debounce_seconds": float(group.get("debounce_seconds", 0.0) or 0.0),
        "batch_size": int(group.get("batch_size", 0) or 0),
        "history_load_count": int(group.get("history_load_count", 0) or 0),
        "reply_style": str(group.get("reply_style", "default") or "default"),
        "custom_prompt": str(group.get("custom_prompt", "") or ""),
        "tools_enabled": bool(group.get("tools_enabled", True)),
        "sticker_mode": str(group.get("sticker_mode", "inherit") or "inherit"),
        "slang_enabled": bool(group.get("slang_enabled", True)),
    }


def _build_profile_audit_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field, label in _AUDIT_LABELS.items():
        before_value = before.get(field)
        after_value = after.get(field)
        if before_value == after_value:
            continue
        changes.append({
            "field": field,
            "label": label,
            "before": _preview_value(before_value),
            "after": _preview_value(after_value),
        })
    return changes


def create_groups_router(
    *,
    config: Any = None,
    group_config: Any = None,
    message_log: Any = None,
    state_board: Any = None,
    scheduler: Any = None,
    tool_registry: Any = None,
    bus: Any = None,
    bot: Any = None,
    ctx: Any = None,
    config_path: str = "config/config.json",
    profile_audit_store: GroupProfileAuditStore | None = None,
) -> APIRouter:
    router = APIRouter()
    target_json_path = _normalize_json_path(config_path)
    default_storage_root = target_json_path.parent / "storage"
    storage_root = Path(getattr(ctx, "storage_dir", default_storage_root)) if ctx is not None else default_storage_root
    audit_store = profile_audit_store or GroupProfileAuditStore(storage_root / "groups" / "group-profile-audit.json")

    def _config_root() -> Any:
        if config is not None:
            return config
        return getattr(ctx, "config", None)

    def _cfg():
        if group_config is not None:
            return group_config
        root = _config_root()
        return getattr(root, "group", None) if root is not None else None

    def _msg_log():
        return message_log or getattr(ctx, "msg_log", None)

    def _state_board():
        return state_board or getattr(ctx, "state_board", None)

    def _scheduler():
        return scheduler or getattr(ctx, "scheduler", None)

    def _bot():
        return bot or getattr(ctx, "bot", None)

    def _bus():
        return bus or getattr(ctx, "bus", None)

    def _tool_registry():
        return tool_registry or getattr(ctx, "tool_registry", None)

    def _resolve_config(gid: str) -> Any:
        cfg = _cfg()
        if cfg is None:
            return SimpleNamespace(
                allowed_tools=set(),
                at_only=False,
                blocked_tools=set(),
                talk_value=0.0,
                planner_smooth=0.0,
                debounce_seconds=0.0,
                batch_size=0,
                history_load_count=0,
                privacy_mask=True,
                blocked_users=set(),
                reply_style="default",
                custom_prompt="",
                tools_enabled=True,
                sticker_mode="inherit",
                slang_enabled=True,
            )
        return cfg.resolve(int(gid))

    def _override_for(gid: str) -> Any:
        cfg = _cfg()
        if cfg is None:
            return None
        return getattr(cfg, "overrides", {}).get(int(gid))

    def _group_payload(gid: str, *, group_name: str = "") -> dict[str, Any]:
        resolved = _resolve_config(gid)
        override = _override_for(gid)
        cfg = _cfg()
        return {
            "group_id": gid,
            "group_name": group_name,
            "at_only": resolved.at_only,
            "talk_value": resolved.talk_value,
            "planner_smooth": resolved.planner_smooth,
            "debounce_seconds": resolved.debounce_seconds,
            "batch_size": resolved.batch_size,
            "history_load_count": resolved.history_load_count,
            "privacy_mask": resolved.privacy_mask,
            "blocked_users": sorted(int(item) for item in resolved.blocked_users),
            "global_blocked_users": sorted(int(item) for item in getattr(cfg, "blocked_users", []) or []),
            "allowed_tools": _sorted_str_list(getattr(resolved, "allowed_tools", set())),
            "blocked_tools": _sorted_str_list(getattr(resolved, "blocked_tools", set())),
            "global_allowed_tools": _sorted_str_list(getattr(cfg, "allowed_tools", []) or []),
            "global_blocked_tools": _sorted_str_list(getattr(cfg, "blocked_tools", []) or []),
            "reply_style": resolved.reply_style,
            "custom_prompt": resolved.custom_prompt,
            "tools_enabled": resolved.tools_enabled,
            "sticker_mode": resolved.sticker_mode,
            "slang_enabled": resolved.slang_enabled,
            "profile_override": _serialize_override(override),
            "profile_customized": _override_has_values(override),
        }

    def _tool_catalog() -> list[dict[str, Any]]:
        runtime_bus = _bus()
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        if runtime_bus is not None and hasattr(runtime_bus, "plugins"):
            for plugin in getattr(runtime_bus, "plugins", []) or []:
                if not getattr(plugin, "enabled", True):
                    continue
                permissions = list(getattr(plugin, "permissions", []) or [])
                if permissions and "tool" not in permissions:
                    continue
                try:
                    tools = plugin.register_tools()
                except Exception:
                    continue
                for tool in tools:
                    name = str(getattr(tool, "name", "") or "").strip()
                    if not name or name in seen:
                        continue
                    entries.append({
                        "name": name,
                        "plugin": str(getattr(plugin, "name", "unknown") or "unknown"),
                        "category": str(getattr(plugin, "category", "general") or "general"),
                        "description": str(getattr(tool, "description", "") or ""),
                    })
                    seen.add(name)
        if entries:
            return sorted(entries, key=lambda item: (item["plugin"], item["name"]))

        registry = _tool_registry()
        if registry is None or not hasattr(registry, "to_openai_tools"):
            return []
        with contextlib.suppress(Exception):
            for tool in registry.to_openai_tools():
                fn = tool.get("function", {}) if isinstance(tool, dict) else {}
                name = str(fn.get("name", "") or "").strip()
                if not name or name in seen:
                    continue
                entries.append({
                    "name": name,
                    "plugin": "runtime",
                    "category": "general",
                    "description": str(fn.get("description", "") or ""),
                })
                seen.add(name)
        return sorted(entries, key=lambda item: (item["plugin"], item["name"]))

    async def _discover_groups() -> tuple[list[str], dict[str, str]]:
        group_ids: set[str] = set()
        group_names: dict[str, str] = {}

        cfg = _cfg()
        if cfg is not None:
            group_ids.update(str(gid) for gid in getattr(cfg, "overrides", {}))
            group_ids.update(str(gid) for gid in getattr(cfg, "allowed_groups", []) or [])

        sched = _scheduler()
        if sched is not None and hasattr(sched, "get_all_slots"):
            with_groups = sched.get_all_slots()
            group_ids.update(str(gid) for gid in with_groups)

        log = _msg_log()
        if log is not None and hasattr(log, "list_group_ids"):
            with contextlib.suppress(Exception):
                group_ids.update(await log.list_group_ids())

        current_bot = _bot()
        if current_bot is not None and hasattr(current_bot, "get_group_list"):
            try:
                for item in await current_bot.get_group_list():
                    gid = str(item.get("group_id", ""))
                    if not gid:
                        continue
                    group_ids.add(gid)
                    group_names[gid] = str(item.get("group_name", ""))
            except Exception:
                pass

        return sorted(group_ids, key=lambda gid: int(gid)), group_names

    def _normalize_message(row: dict[str, Any]) -> dict[str, Any]:
        speaker = row.get("speaker") or ""
        user_id = ""
        if speaker:
            match = _SPEAKER_QQ_RE.search(str(speaker))
            if match:
                user_id = match.group(1)

        ts = row.get("created_at")
        timestamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, (int, float)) else ""

        return {
            "role": row.get("role", ""),
            "speaker": speaker,
            "user_id": user_id or ("bot" if row.get("role") == "assistant" else ""),
            "message": row.get("content_text", "") or "",
            "message_id": row.get("message_id"),
            "timestamp": timestamp,
        }

    @router.get("/groups")
    async def list_groups():
        groups: list[dict[str, Any]] = []
        group_ids, group_names = await _discover_groups()
        for gid in group_ids:
            groups.append(_group_payload(gid, group_name=group_names.get(gid, "")))
        return {"groups": groups}

    @router.get("/groups/{group_id}/profile")
    async def get_group_profile(group_id: str):
        try:
            int(group_id)
        except ValueError:
            return {"ok": False, "error": "group_id 必须是数字"}

        group_ids, group_names = await _discover_groups()
        group_name = group_names.get(group_id, "")
        if group_id not in group_ids:
            group_ids.append(group_id)
        return {
            "ok": True,
            "group": _group_payload(group_id, group_name=group_name),
            "tool_catalog": _tool_catalog(),
            "audit": audit_store.as_payload(group_id=group_id, limit=10),
        }

    @router.post("/groups/{group_id}/profile")
    async def save_group_profile(group_id: str, payload: GroupProfilePayload):
        try:
            gid = int(group_id)
        except ValueError:
            return {"ok": False, "error": "group_id 必须是数字"}

        runtime_group = _cfg()
        runtime_root = _config_root()
        if runtime_group is None:
            return {"ok": False, "error": "GroupConfig not available"}

        before = _profile_audit_snapshot(_group_payload(group_id))

        data = _read_config_base(target_json_path, runtime_root)
        model = BotConfig.model_validate(data)
        normalized = _normalized_override_values(payload, model.group)
        existing_override = model.group.overrides.get(gid, GroupOverride())
        merged_override = existing_override.model_copy(update=normalized)
        if _override_has_values(merged_override):
            model.group.overrides[gid] = merged_override
        else:
            model.group.overrides.pop(gid, None)

        try:
            _write_model(target_json_path, model)
        except Exception as exc:
            return {"ok": False, "error": f"保存群配置失败: {exc}"}

        _apply_runtime_group_config(runtime_group, model.group)
        _apply_runtime_group_config(getattr(runtime_root, "group", None), model.group)
        current_group = _group_payload(group_id)
        changes = _build_profile_audit_changes(before, _profile_audit_snapshot(current_group))
        audit_entry = audit_store.append(
            group_id=group_id,
            action="save",
            summary={
                "changed_fields": [item["field"] for item in changes],
                "changed_count": len(changes),
                "profile_customized": bool(current_group.get("profile_customized", False)),
            },
            changes=changes,
            group_name=str(current_group.get("group_name", "") or ""),
        )
        return {
            "ok": True,
            "path": str(target_json_path),
            "group": current_group,
            "audit_entry": audit_entry,
            "message": "群 Profile 已保存并立即生效。",
        }

    @router.delete("/groups/{group_id}/profile")
    async def reset_group_profile(group_id: str):
        try:
            gid = int(group_id)
        except ValueError:
            return {"ok": False, "error": "group_id 必须是数字"}

        runtime_group = _cfg()
        runtime_root = _config_root()
        if runtime_group is None:
            return {"ok": False, "error": "GroupConfig not available"}

        before = _profile_audit_snapshot(_group_payload(group_id))

        data = _read_config_base(target_json_path, runtime_root)
        model = BotConfig.model_validate(data)
        model.group.overrides.pop(gid, None)

        try:
            _write_model(target_json_path, model)
        except Exception as exc:
            return {"ok": False, "error": f"恢复全局默认失败: {exc}"}

        _apply_runtime_group_config(runtime_group, model.group)
        _apply_runtime_group_config(getattr(runtime_root, "group", None), model.group)
        current_group = _group_payload(group_id)
        changes = _build_profile_audit_changes(before, _profile_audit_snapshot(current_group))
        audit_entry = audit_store.append(
            group_id=group_id,
            action="reset",
            summary={
                "changed_fields": [item["field"] for item in changes],
                "changed_count": len(changes),
                "profile_customized": bool(current_group.get("profile_customized", False)),
            },
            changes=changes,
            group_name=str(current_group.get("group_name", "") or ""),
        )
        return {
            "ok": True,
            "path": str(target_json_path),
            "group": current_group,
            "audit_entry": audit_entry,
            "message": "已恢复为全局默认群策略。",
        }

    @router.get("/groups/{group_id}/state")
    async def group_state(group_id: str):
        board = _state_board()
        if board is None:
            return {"error": "StateBoard not available"}

        try:
            state = await board.query_state(group_id)
            return {
                "group_id": group_id,
                "active_users": state.active_users,
                "recent_topics": state.recent_topics,
                "message_frequency": state.message_frequency,
                "recent_mentions": state.recent_mentions,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @router.get("/groups/{group_id}/messages")
    async def group_messages(
        group_id: str,
        limit: int = Query(20, ge=1, le=100),
    ):
        log = _msg_log()
        if log is None:
            return {"messages": []}

        try:
            msgs = await log.query_recent(group_id, limit=limit)
            return {"messages": [_normalize_message(msg) for msg in msgs]}
        except Exception as exc:
            return {"messages": [], "error": str(exc)}

    return router
