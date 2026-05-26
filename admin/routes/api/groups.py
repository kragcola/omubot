"""JSON API: groups — list, live state, recent messages, and per-group profile."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import re
import tomllib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, Query
from loguru import logger as _base_logger
from pydantic import BaseModel, Field, field_validator, model_validator

from kernel.config import (
    BotConfig,
    GroupAccessConfig,
    GroupOverride,
    GroupReplyStyle,
    GroupStickerMode,
    HumanizationProfile,
)
from services.group_profile_audit import GroupProfileAuditStore

_SPEAKER_QQ_RE = re.compile(r"\((\d+)\)$")
_log_admin = _base_logger.bind(channel="admin")
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
    "presence_mode",
    "humanization_profile",
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
    "presence_mode": "群参与模式",
    "humanization_profile": "拟人化档位",
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
    presence_mode: Literal["active", "silent_learn", "off"] | None = None
    humanization_profile: HumanizationProfile | None = None

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
    def _remove_tool_overlaps(self) -> GroupProfilePayload:
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


def _normalize_group_policy_path(config_path: str | None, fallback_base: Path) -> Path:
    if config_path:
        configured = Path(config_path)
        if configured.suffix.lower() in {".json", ".toml"}:
            return configured.parent / "group-policy.json"
        return configured / "group-policy.json"
    return fallback_base / "group-policy.json"


def _read_group_policy(path: Path, fallback_config: Any) -> GroupAccessConfig:
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                payload = data.get("access", data)
                if isinstance(payload, dict):
                    return GroupAccessConfig.model_validate(payload)
        except Exception:
            pass
    cfg = getattr(fallback_config, "group", fallback_config)
    if cfg is not None and hasattr(cfg, "access"):
        access = cfg.access
        if hasattr(access, "model_dump"):
            return GroupAccessConfig.model_validate(access.model_dump(mode="python"))
        if isinstance(access, dict):
            return GroupAccessConfig.model_validate(access)
    return GroupAccessConfig()


def _write_group_policy(path: Path, access: GroupAccessConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(access.model_dump(mode="python"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


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


def _normalized_override_values(
    payload: GroupProfilePayload,
    base: Any,
    *,
    effective_base: Any | None = None,
) -> dict[str, Any]:
    values = payload.model_dump(mode="python")
    compare_base = effective_base or base
    normalized: dict[str, Any] = {}
    for field, value in values.items():
        base_value = getattr(compare_base, field, None)
        if field in {"allowed_tools", "blocked_tools"}:
            value = _sorted_str_list(value)
            base_value = _sorted_str_list(base_value)
        elif field == "blocked_users":
            value = sorted({int(item) for item in (value or [])})
            base_value = sorted({int(item) for item in (base_value or [])})
            normalized[field] = value  # never None — GroupOverride.blocked_users is list[int]
            continue
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
        "presence_mode": str(group.get("presence_mode", "active") or "active"),
        "humanization_profile": str(group.get("humanization_profile", "custom") or "custom"),
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
    group_policy_path: str | None = None,
    profile_audit_store: GroupProfileAuditStore | None = None,
) -> APIRouter:
    router = APIRouter()
    target_json_path = _normalize_json_path(config_path)
    target_policy_path = _normalize_group_policy_path(group_policy_path, target_json_path.parent)
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

    def _global_humanization_profile() -> str:
        root = _config_root()
        humanization = getattr(root, "humanization", None) if root is not None else None
        return str(getattr(humanization, "profile", "custom") or "custom")

    def _resolve_config(gid: str) -> Any:
        cfg = _cfg()
        if cfg is None:
            return SimpleNamespace(
                access_allowed=True,
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
                presence_mode="active",
                humanization_profile=None,
            )
        return cfg.resolve(int(gid))

    def _override_for(gid: str) -> Any:
        cfg = _cfg()
        if cfg is None:
            return None
        return getattr(cfg, "overrides", {}).get(int(gid))

    def _group_payload(
        gid: str,
        *,
        group_name: str = "",
        inventory: dict[str, Any] | None = None,
        activity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = _resolve_config(gid)
        override = _override_for(gid)
        cfg = _cfg()
        item = inventory or {}
        member_count = item.get("member_count")
        max_member_count = item.get("max_member_count")
        group_remark = str(item.get("group_remark", "") or "")
        bot_card = str(item.get("self_card", "") or "")
        if not group_name:
            group_name = str(item.get("group_name", "") or "")
        act = activity or {}
        last_at = float(act.get("last_at") or 0.0)
        global_humanization_profile = _global_humanization_profile()
        effective_humanization_profile = str(
            getattr(resolved, "humanization_profile", None)
            or global_humanization_profile
        )
        return {
            "group_id": gid,
            "group_name": group_name,
            "member_count": int(member_count) if isinstance(member_count, int | float) else None,
            "max_member_count": int(max_member_count) if isinstance(max_member_count, int | float) else None,
            "group_remark": group_remark,
            "bot_card": bot_card,
            "last_message_at": last_at if last_at > 0 else None,
            "message_count_window": int(act.get("count_window") or 0),
            "user_message_count_window": int(act.get("user_count_window") or 0),
            "at_only": resolved.at_only,
            "talk_value": resolved.talk_value,
            "planner_smooth": resolved.planner_smooth,
            "debounce_seconds": resolved.debounce_seconds,
            "batch_size": resolved.batch_size,
            "history_load_count": resolved.history_load_count,
            "privacy_mask": resolved.privacy_mask,
            "access_allowed": bool(getattr(resolved, "access_allowed", True)),
            "presence_mode": str(getattr(resolved, "presence_mode", "active") or "active"),
            "humanization_profile": effective_humanization_profile,
            "global_humanization_profile": global_humanization_profile,
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

    async def _discover_groups() -> tuple[list[str], dict[str, str], dict[str, dict[str, Any]]]:
        group_ids: set[str] = set()
        group_names: dict[str, str] = {}
        inventory_acc: dict[str, dict[str, Any]] = {}

        cfg = _cfg()
        if cfg is not None:
            group_ids.update(str(gid) for gid in getattr(cfg, "overrides", {}))
            group_ids.update(str(gid) for gid in getattr(cfg, "allowed_groups", []) or [])
            access_cfg = getattr(cfg, "access", None)
            if access_cfg is not None:
                group_ids.update(str(gid) for gid in getattr(access_cfg, "whitelist", []) or [])
                group_ids.update(str(gid) for gid in getattr(access_cfg, "blacklist", []) or [])

        sched = _scheduler()
        if sched is not None and hasattr(sched, "get_all_slots"):
            with_groups = sched.get_all_slots()
            group_ids.update(str(gid) for gid in with_groups)

        log = _msg_log()
        if log is not None and hasattr(log, "list_group_ids"):
            with contextlib.suppress(Exception):
                group_ids.update(await log.list_group_ids())

        cached_inventory = getattr(ctx, "group_inventory", None) if ctx is not None else None
        if isinstance(cached_inventory, dict):
            for raw_gid, entry in cached_inventory.items():
                gid = str(raw_gid or "").strip()
                if not gid:
                    continue
                group_ids.add(gid)
                if isinstance(entry, dict):
                    inventory_acc[gid] = dict(entry)
                    group_name = str(entry.get("group_name", "") or "").strip()
                    if group_name:
                        group_names[gid] = group_name

        current_bot = _bot()
        if current_bot is not None and hasattr(current_bot, "get_group_list"):
            try:
                live_inventory: dict[str, dict[str, Any]] = {}
                for entry in await current_bot.get_group_list():
                    if not isinstance(entry, dict):
                        continue
                    gid = str(entry.get("group_id", "") or "").strip()
                    if not gid:
                        continue
                    group_ids.add(gid)
                    live_inventory[gid] = dict(entry)
                    inventory_acc[gid] = dict(entry)
                    group_name = str(entry.get("group_name", "") or "").strip()
                    if group_name:
                        group_names[gid] = group_name
                if ctx is not None:
                    ctx.group_inventory = live_inventory
            except Exception as exc:
                _log_admin.warning("failed to refresh live group list | error={}", exc)

        sorted_ids = sorted(
            group_ids,
            key=lambda gid: (0, int(gid)) if gid.isdigit() else (1, gid),
        )
        return sorted_ids, group_names, inventory_acc

    async def _load_activity_summary(window_seconds: int = 24 * 3600) -> dict[str, dict[str, Any]]:
        log = _msg_log()
        if log is None or not hasattr(log, "group_activity_summary"):
            return {}
        since = datetime.now().timestamp() - max(60, int(window_seconds))
        try:
            return await log.group_activity_summary(since=since)
        except Exception as exc:
            _log_admin.warning("failed to load group activity summary | error={}", exc)
            return {}

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
        group_ids, group_names, inventory = await _discover_groups()
        activity = await _load_activity_summary()
        for gid in group_ids:
            groups.append(
                _group_payload(
                    gid,
                    group_name=group_names.get(gid, ""),
                    inventory=inventory.get(gid),
                    activity=activity.get(gid),
                )
            )
        return {"groups": groups}

    @router.get("/groups/policy")
    async def get_group_policy():
        runtime_cfg = _cfg()
        policy = _read_group_policy(target_policy_path, runtime_cfg)
        return {
            "ok": True,
            "path": str(target_policy_path),
            "policy": policy.model_dump(mode="python"),
        }

    @router.post("/groups/policy")
    async def save_group_policy(payload: GroupAccessConfig):
        runtime_cfg = _cfg()
        policy = payload.model_copy(deep=True)
        try:
            _write_group_policy(target_policy_path, policy)
        except Exception as exc:
            return {"ok": False, "error": f"保存群门禁失败: {exc}"}

        if runtime_cfg is not None and hasattr(runtime_cfg, "access"):
            runtime_cfg.access = policy
            if hasattr(runtime_cfg, "_legacy_allowed_groups_as_active"):
                runtime_cfg._legacy_allowed_groups_as_active = False
        runtime_group = getattr(getattr(ctx, "config", None), "group", None) if ctx is not None else None
        if runtime_group is not None:
            runtime_group.access = policy
            if hasattr(runtime_group, "_legacy_allowed_groups_as_active"):
                runtime_group._legacy_allowed_groups_as_active = False

        group_ids, group_names, inventory = await _discover_groups()
        activity = await _load_activity_summary()
        groups = [
            _group_payload(
                gid,
                group_name=group_names.get(gid, ""),
                inventory=inventory.get(gid),
                activity=activity.get(gid),
            )
            for gid in group_ids
        ]
        return {
            "ok": True,
            "path": str(target_policy_path),
            "policy": policy.model_dump(mode="python"),
            "groups": groups,
            "message": "群门禁已保存并立即生效。",
        }

    @router.get("/groups/{group_id}/profile")
    async def get_group_profile(group_id: str):
        try:
            int(group_id)
        except ValueError:
            return {"ok": False, "error": "group_id 必须是数字"}

        group_ids, group_names, inventory = await _discover_groups()
        activity = await _load_activity_summary()
        group_name = group_names.get(group_id, "")
        if group_id not in group_ids:
            group_ids.append(group_id)
        return {
            "ok": True,
            "group": _group_payload(
                group_id,
                group_name=group_name,
                inventory=inventory.get(group_id),
                activity=activity.get(group_id),
            ),
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
        baseline_group = runtime_group
        if hasattr(runtime_group, "model_copy"):
            baseline_group = runtime_group.model_copy(deep=True)
            with contextlib.suppress(Exception):
                baseline_group.overrides.pop(gid, None)
        baseline_resolved = baseline_group.resolve(gid)
        payload_to_save = payload
        if not bool(getattr(baseline_resolved, "access_allowed", True)):
            learning_requested = payload.slang_enabled or payload.presence_mode in {"silent_learn", "active"}
            if learning_requested:
                payload_to_save = payload.model_copy(update={"presence_mode": "silent_learn", "slang_enabled": True})

        data = _read_config_base(target_json_path, runtime_root)
        model = BotConfig.model_validate(data)
        normalized = _normalized_override_values(
            payload_to_save,
            model.group,
            effective_base=baseline_resolved,
        )
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
        current_group = await _build_full_group_payload(group_id)
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
        current_group = await _build_full_group_payload(group_id)
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

    def _ensure_gid(group_id: str) -> int | None:
        try:
            return int(group_id)
        except ValueError:
            return None

    async def _refresh_group_inventory(gid: int) -> dict[str, Any] | None:
        """Refresh cached inventory for a single group after a mutation."""
        current_bot = _bot()
        if current_bot is None:
            return None
        try:
            info = await current_bot.get_group_info(group_id=int(gid), no_cache=True)
        except Exception as exc:
            _log_admin.warning("get_group_info failed | gid={} error={}", gid, exc)
            return None
        if not isinstance(info, dict):
            return None
        if ctx is not None:
            inventory = getattr(ctx, "group_inventory", None)
            if isinstance(inventory, dict):
                merged = dict(inventory.get(str(gid), {}))
                merged.update(info)
                inventory[str(gid)] = merged
                ctx.group_inventory = inventory
            else:
                ctx.group_inventory = {str(gid): dict(info)}
        return info

    async def _verify_bot_card(gid: int) -> str | None:
        """Re-query Napcat for the bot's actual in-group card (no_cache=True)."""
        current_bot = _bot()
        if current_bot is None:
            return None
        try:
            info = await current_bot.call_api(
                "get_group_member_info",
                group_id=int(gid),
                user_id=int(current_bot.self_id),
                no_cache=True,
            )
        except Exception as exc:
            _log_admin.warning("get_group_member_info failed | gid={} error={}", gid, exc)
            return None
        if not isinstance(info, dict):
            return None
        return str(info.get("card", "") or "")

    async def _verify_group_remark(gid: int) -> str | None:
        """Re-query Napcat for the bot-side group remark (no_cache=True)."""
        info = await _refresh_group_inventory(gid)
        if not isinstance(info, dict):
            return None
        return str(info.get("group_remark", "") or "")

    def _patch_inventory(gid: int, **patch: Any) -> None:
        if ctx is None:
            return
        inventory = getattr(ctx, "group_inventory", None)
        if not isinstance(inventory, dict):
            inventory = {}
        merged = dict(inventory.get(str(gid), {}))
        merged.update({key: value for key, value in patch.items() if value is not None})
        inventory[str(gid)] = merged
        ctx.group_inventory = inventory

    async def _build_full_group_payload(
        group_id: str,
        *,
        inventory_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Re-discover groups + activity so the response carries the full payload.

        ``inventory_override`` lets a mutation endpoint pass freshly verified
        fields (e.g. self_card from get_group_member_info(no_cache=True)) that
        should win over whatever ``_discover_groups`` reads back from the
        cached ``get_group_list`` response.
        """
        group_ids, group_names, inventory_map = await _discover_groups()
        if group_id not in group_ids:
            group_ids.append(group_id)
        activity = await _load_activity_summary()
        merged_inventory = dict(inventory_map.get(group_id, {}))
        if inventory_override:
            merged_inventory.update(
                {key: value for key, value in inventory_override.items() if value is not None}
            )
        return _group_payload(
            group_id,
            group_name=group_names.get(group_id, ""),
            inventory=merged_inventory,
            activity=activity.get(group_id),
        )

    @router.post("/groups/{group_id}/group-remark")
    async def set_group_remark_endpoint(group_id: str, payload: dict[str, Any]):
        gid = _ensure_gid(group_id)
        if gid is None:
            return {"ok": False, "error": "group_id 必须是数字"}
        new_remark = str(payload.get("group_remark", "") or "").strip()
        if len(new_remark) > 60:
            return {"ok": False, "error": "群备注过长"}
        current_bot = _bot()
        if current_bot is None:
            return {"ok": False, "error": "Bot 未连接"}
        try:
            await current_bot.call_api(
                "set_group_remark",
                group_id=gid,
                remark=new_remark,
            )
        except Exception as exc:
            _log_admin.warning("set_group_remark failed | gid={} error={}", gid, exc)
            return {"ok": False, "error": f"设置群备注失败: {exc}"}

        # Verify the mutation actually took effect by re-querying Napcat.
        # set_group_remark is bot-side only, so this should normally succeed
        # immediately, but we still want to detect silent failures.
        verified: str | None = None
        with contextlib.suppress(Exception):
            await asyncio.sleep(0.4)
            verified = await _verify_group_remark(gid)

        warning: str | None = None
        if verified is None:
            # Couldn't verify — patch with the requested value as a best effort
            # but warn the caller that we don't actually know what stuck.
            _patch_inventory(gid, group_remark=new_remark)
            warning = "已下发请求，但未能从 Napcat 确认备注是否生效。"
            override = {"group_remark": new_remark}
        else:
            _patch_inventory(gid, group_remark=verified)
            override = {"group_remark": verified}
            if verified != new_remark:
                warning = (
                    f"Bot 已接受请求，但 Napcat 实际备注是 “{verified or '空'}”，"
                    "可能被风控或长度限制截断。"
                )

        group_payload = await _build_full_group_payload(group_id, inventory_override=override)
        message = "已更新 Bot 端群备注。" if warning is None else warning
        return {
            "ok": True,
            "group": group_payload,
            "verified_value": verified,
            "warning": warning,
            "message": message,
        }

    @router.post("/groups/{group_id}/bot-card")
    async def set_bot_card(group_id: str, payload: dict[str, Any]):
        gid = _ensure_gid(group_id)
        if gid is None:
            return {"ok": False, "error": "group_id 必须是数字"}
        card = str(payload.get("card", "") or "").strip()
        if len(card) > 60:
            return {"ok": False, "error": "群名片过长"}
        current_bot = _bot()
        if current_bot is None:
            return {"ok": False, "error": "Bot 未连接"}
        try:
            await current_bot.call_api(
                "set_group_card",
                group_id=gid,
                user_id=int(current_bot.self_id),
                card=card,
            )
        except Exception as exc:
            _log_admin.warning("set_group_card failed | gid={} error={}", gid, exc)
            return {"ok": False, "error": f"设置群名片失败: {exc}"}

        # Verify the mutation actually took effect on QQ. set_group_card returns
        # success as long as Napcat accepts the request, but QQ may silently
        # reject it (account risk control, length limits, banned characters).
        # Re-query get_group_member_info with no_cache to read the truth.
        verified: str | None = None
        with contextlib.suppress(Exception):
            # Give Napcat / QQ a brief moment to apply the change. 0.4s is
            # enough for the common case without making the user wait.
            await asyncio.sleep(0.4)
            verified = await _verify_bot_card(gid)

        warning: str | None = None
        if verified is None:
            _patch_inventory(gid, self_card=card)
            warning = "已下发请求，但未能从 Napcat 确认群名片是否生效。"
            override = {"self_card": card}
        else:
            _patch_inventory(gid, self_card=verified)
            override = {"self_card": verified}
            if verified != card:
                warning = (
                    f"QQ 实际显示的群名片是 “{verified or '空'}”，"
                    "可能被风控、长度或字符限制拒绝。"
                )

        group_payload = await _build_full_group_payload(group_id, inventory_override=override)
        message = "已更新机器人的群名片。" if warning is None else warning
        return {
            "ok": True,
            "group": group_payload,
            "verified_value": verified,
            "warning": warning,
            "message": message,
        }

    @router.post("/groups/{group_id}/leave")
    async def leave_group(group_id: str, payload: dict[str, Any] | None = None):
        gid = _ensure_gid(group_id)
        if gid is None:
            return {"ok": False, "error": "group_id 必须是数字"}
        body = payload or {}
        if not bool(body.get("confirm", False)):
            return {"ok": False, "error": "缺少确认标记 confirm=true"}
        dismiss = bool(body.get("dismiss", False))
        current_bot = _bot()
        if current_bot is None:
            return {"ok": False, "error": "Bot 未连接"}
        try:
            await current_bot.call_api(
                "set_group_leave",
                group_id=gid,
                is_dismiss=dismiss,
            )
        except Exception as exc:
            _log_admin.warning(
                "set_group_leave failed | gid={} dismiss={} error={}", gid, dismiss, exc
            )
            return {"ok": False, "error": f"退群失败: {exc}"}

        if ctx is not None:
            inventory = getattr(ctx, "group_inventory", None)
            if isinstance(inventory, dict):
                inventory.pop(str(gid), None)
                ctx.group_inventory = inventory

        return {
            "ok": True,
            "group_id": group_id,
            "dismissed": dismiss,
            "message": "已解散该群。" if dismiss else "已退出该群。",
        }

    return router
