"""JSON API: plugins — plugin list, tools, commands."""

from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from services.plugin_index import PluginIndexService
from services.version import VERSION

PLUGIN_API_VERSION = 3
PLUGIN_LAYOUT_VERSION = 2
SYSTEM_PLUGIN_WHITELIST = frozenset({"chat", "history_loader", "vision"})
LEGACY_BLOCK_REASON = "legacy_single_file_detected"


def create_plugins_router(
    *,
    bus: Any = None,
    tool_registry: Any = None,
    plugin_state_store: Any = None,
    plugin_config_store: Any = None,
    plugin_root: str | Path = "plugins",
) -> APIRouter:
    router = APIRouter()
    index_service = PluginIndexService(plugin_root)

    def _frontend_build_id() -> str:
        explicit = str(os.environ.get("FRONTEND_BUILD_ID") or "").strip()
        if explicit:
            return explicit
        index_path = Path("admin/static/index.html")
        if not index_path.is_file():
            return "missing"
        try:
            digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
            return digest[:12]
        except Exception:
            return "unknown"

    def _normalize_tier(name: str, tier: str) -> str:
        if name in SYSTEM_PLUGIN_WHITELIST:
            return "system"
        return "user"

    def _normalize_toggle_policy(name: str, toggle_policy: str, tier: str) -> str:
        if name in SYSTEM_PLUGIN_WHITELIST or tier == "system":
            return "locked"
        if toggle_policy == "restart_required":
            return "restart_required"
        return "runtime"

    def _normalized_identity(name: str, tier: str, toggle_policy: str) -> tuple[str, str]:
        normalized_tier = _normalize_tier(name, tier)
        normalized_policy = _normalize_toggle_policy(name, toggle_policy, normalized_tier)
        return normalized_tier, normalized_policy

    def _is_entry_under_plugin_root(entry: dict[str, Any]) -> bool:
        root = Path(plugin_root)
        normalized_prefix = f"{root.name}/"
        for key in ("relative_entry", "relative_manifest", "relative_signature"):
            value = str(entry.get(key) or "").replace("\\", "/")
            if value and (value.startswith(normalized_prefix) or value.startswith("plugins/")):
                return True
        for key in ("entry_path", "manifest_path", "signature_path", "package_path"):
            raw = str(entry.get(key) or "").strip()
            if not raw:
                continue
            try:
                if Path(raw).resolve().is_relative_to(root.resolve()):
                    return True
            except Exception:
                continue
        return False

    def _legacy_entries(index_payload: dict[str, Any]) -> list[dict[str, Any]]:
        entries = list(index_payload.get("entries") or [])
        return [
            entry for entry in entries
            if str(entry.get("kind") or "").startswith("legacy_")
            and _is_entry_under_plugin_root(entry)
        ]

    def _plugin_meta_payload(index_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot = index_payload
        if snapshot is None:
            try:
                snapshot = index_service.build_index(bus=bus)
            except Exception:
                snapshot = {"entries": [], "summary": {}}
        legacy_entries = _legacy_entries(snapshot)
        return {
            "plugin_api_version": PLUGIN_API_VERSION,
            "plugin_layout_version": PLUGIN_LAYOUT_VERSION,
            "build_commit": str(os.environ.get("GIT_COMMIT", "unknown")),
            "frontend_build_id": _frontend_build_id(),
            "omubot_version": VERSION,
            "legacy_detected": bool(legacy_entries),
            "legacy_single_file_detected": bool(legacy_entries),
            "legacy_plugins": [str(entry.get("name") or "") for entry in legacy_entries if entry.get("name")],
            "summary": dict(snapshot.get("summary") or {}),
            "plugin_root": str(snapshot.get("plugin_root") or plugin_root),
        }

    def _legacy_block_payload(meta: dict[str, Any], *, include_plugins: bool = False) -> dict[str, Any]:
        payload = {
            "blocked": True,
            "blocked_reason": LEGACY_BLOCK_REASON,
            "error": "检测到旧版根目录单文件插件，插件中心已阻断。请先迁移目录插件并重启。",
            "meta": meta,
        }
        if include_plugins:
            payload["plugins"] = []
        return payload

    def _allows(plugin: Any, permission: str) -> bool:
        permissions = list(getattr(plugin, "permissions", []) or [])
        return not permissions or permission in permissions

    def _safe_dict(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _plugin_commands(plugin: Any) -> list[dict[str, Any]]:
        if not _allows(plugin, "command"):
            return []
        commands = []
        try:
            for cmd in plugin.register_commands():
                gates: list[str] = []
                if getattr(cmd, "admin_only", False):
                    gates.append("admin")
                if getattr(cmd, "private_only", False):
                    gates.append("private")
                commands.append({
                    "plugin": getattr(plugin, "name", "unknown"),
                    "name": cmd.name,
                    "description": cmd.description,
                    "usage": getattr(cmd, "usage", ""),
                    "permission": ",".join(gates) if gates else "public",
                })
        except Exception:
            return []
        return commands

    def _plugin_tools(plugin: Any) -> list[dict[str, Any]]:
        if not _allows(plugin, "tool"):
            return []
        tools = []
        try:
            for tool in plugin.register_tools():
                tools.append({
                    "plugin": getattr(plugin, "name", "unknown"),
                    **tool.to_openai_tool(),
                })
        except Exception:
            return []
        return tools

    def _health_by_name() -> dict[str, dict[str, Any]]:
        if bus is None or not hasattr(bus, "plugin_health"):
            return {}
        try:
            return {item.get("name", ""): _normalize_health_payload(item) for item in bus.plugin_health()}
        except Exception:
            return {}

    def _health_display(state: str) -> tuple[str, str, str]:
        if state == "disabled":
            return "disabled", "已停用", "default"
        if state == "healthy":
            return "healthy", "健康", "success"
        if state == "permission_limited":
            return "permission_limited", "按权限运行", "info"
        if state == "throttled":
            return "throttled", "已保护", "warning"
        if state == "degraded":
            return "degraded", "需关注", "warning"
        return state or "unknown", "状态未知", "error"

    def _normalize_health_payload(health: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(health or {})
        state = str(payload.get("state") or ("disabled" if payload.get("enabled") is False else "healthy"))
        display_state, display_label, display_type = _health_display(state)
        payload.setdefault("display_state", display_state)
        payload.setdefault("display_label", display_label)
        payload.setdefault("display_type", display_type)
        return payload

    def _persistent_enabled(name: str) -> bool | None:
        if plugin_state_store is None or not hasattr(plugin_state_store, "get"):
            return None
        try:
            return plugin_state_store.get(name)
        except Exception:
            return None

    def _default_for_schema(schema: Any) -> Any:
        if not isinstance(schema, dict):
            return None
        if "default" in schema:
            return deepcopy(schema["default"])

        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            schema_type = next(
                (item for item in schema_type if item != "null"),
                schema_type[0] if schema_type else None,
            )

        if schema_type == "object":
            defaults: dict[str, Any] = {}
            properties = schema.get("properties", {})
            if isinstance(properties, dict):
                for key, child_schema in properties.items():
                    default = _default_for_schema(child_schema)
                    if default is not None:
                        defaults[key] = default
            return defaults
        if schema_type == "array":
            return []
        if schema_type == "boolean":
            return False
        if schema_type == "string":
            return ""
        return None

    def _merge_defaults(defaults: Any, values: Any) -> Any:
        if isinstance(defaults, dict) and isinstance(values, dict):
            merged = deepcopy(defaults)
            for key, value in values.items():
                merged[key] = _merge_defaults(merged.get(key), value)
            return merged
        return deepcopy(values) if values is not None else deepcopy(defaults)

    def _settings_payload(plugin: Any) -> dict[str, Any]:
        name = str(getattr(plugin, "name", "unknown") or "unknown")
        raw_tier = str(getattr(plugin, "tier", "user") or "user")
        raw_policy = str(getattr(plugin, "toggle_policy", "runtime") or "runtime")
        tier, toggle_policy = _normalized_identity(name, raw_tier, raw_policy)
        config_spec = dict(getattr(plugin, "config_spec", {}) or {})
        if (
            tier == "system"
            or toggle_policy == "locked"
            or str(config_spec.get("apply_mode") or "") == "read_only"
        ):
            return {
                "schema": {},
                "values": {},
                "defaults": {},
                "effective_values": {},
                "updated_at": 0.0,
                "path": str(getattr(plugin_config_store, "path", "")) if plugin_config_store is not None else "",
                "default_path": "",
                "schema_path": "",
                "has_saved_values": False,
                "apply_mode": "read_only",
                "requires_restart": False,
                "restart_required_fields": [],
            }
        schema = dict(getattr(plugin, "settings_schema", {}) or {})
        entry = {"values": {}, "defaults": {}, "effective_values": {}, "updated_at": 0.0}
        if plugin_config_store is not None and hasattr(plugin_config_store, "get_entry"):
            try:
                entry = plugin_config_store.get_entry(getattr(plugin, "name", "unknown"))
            except Exception:
                entry = {"values": {}, "defaults": {}, "effective_values": {}, "updated_at": 0.0}

        if not schema and isinstance(entry.get("schema"), dict):
            schema = dict(entry.get("schema") or {})
        values = entry.get("values", {})
        if not isinstance(values, dict):
            values = {}
        defaults = entry.get("defaults")
        if not isinstance(defaults, dict):
            defaults = {}
        if not defaults and schema:
            inferred_defaults = _default_for_schema(schema)
            defaults = inferred_defaults if isinstance(inferred_defaults, dict) else {}
        effective_values = entry.get("effective_values")
        if not isinstance(effective_values, dict) or (not effective_values and (defaults or values)):
            effective_values = _merge_defaults(defaults, values)
        return {
            "schema": schema,
            "values": values,
            "defaults": defaults,
            "effective_values": effective_values if isinstance(effective_values, dict) else {},
            "updated_at": entry.get("updated_at", 0.0),
            "path": (
                str(entry.get("path") or getattr(plugin_config_store, "path", ""))
                if plugin_config_store is not None
                else ""
            ),
            "default_path": str(entry.get("default_path") or ""),
            "schema_path": str(entry.get("schema_path") or ""),
            "has_saved_values": bool(values),
            "apply_mode": str(config_spec.get("apply_mode") or "hot"),
            "requires_restart": str(config_spec.get("apply_mode") or "hot") == "restart_required",
            "restart_required_fields": list(config_spec.get("restart_required_fields") or []),
        }

    def _settings_config_status(settings: dict[str, Any], *, locked: bool, legacy_blocked: bool = False) -> str:
        if legacy_blocked:
            return "legacy_blocked"
        if locked or str(settings.get("apply_mode") or "") == "read_only":
            return "read_only"
        schema = settings.get("schema")
        if isinstance(schema, dict) and schema:
            return "ready"
        return "missing_schema"

    def _is_locked(plugin: Any) -> bool:
        if plugin is None:
            return False
        name = str(getattr(plugin, "name", "unknown") or "unknown")
        raw_tier = str(getattr(plugin, "tier", "user") or "user")
        raw_policy = str(getattr(plugin, "toggle_policy", "runtime") or "runtime")
        tier, toggle_policy = _normalized_identity(name, raw_tier, raw_policy)
        return tier == "system" or toggle_policy == "locked"

    def _plugin_payload(
        plugin: Any,
        health: dict[str, Any] | None = None,
        *,
        package: dict[str, Any] | None = None,
        legacy_blocked: bool = False,
    ) -> dict[str, Any]:
        name = getattr(plugin, "name", "unknown")
        raw_tier = str(getattr(plugin, "tier", "user") or "user")
        raw_policy = str(getattr(plugin, "toggle_policy", "runtime") or "runtime")
        tier, toggle_policy = _normalized_identity(name, raw_tier, raw_policy)
        locked = tier == "system" or toggle_policy == "locked"
        settings = _settings_payload(plugin)
        config_status = _settings_config_status(settings, locked=locked, legacy_blocked=legacy_blocked)
        payload = {
            "name": name,
            "display_name": _safe_dict(getattr(plugin, "display_name", {})),
            "description": getattr(plugin, "description", ""),
            "version": getattr(plugin, "version", "0.0.0"),
            "priority": getattr(plugin, "priority", 10),
            "enabled": getattr(plugin, "enabled", True),
            "persistent_enabled": _persistent_enabled(name),
            "author": getattr(plugin, "author", ""),
            "category": getattr(plugin, "category", "general"),
            "tier": tier,
            "toggle_policy": toggle_policy,
            "locked": locked,
            "permissions": list(getattr(plugin, "permissions", []) or []),
            "capabilities": list(getattr(plugin, "capabilities", []) or []),
            "config_spec": _safe_dict(getattr(plugin, "config_spec", {})),
            "store": _safe_dict(getattr(plugin, "store", {})),
            "configurable": config_status == "ready",
            "config_status": config_status,
            "min_omubot_version": getattr(plugin, "min_omubot_version", ""),
            "hook_budget_ms": getattr(plugin, "hook_budget_ms", 5000),
            "package": package,
        }
        if health is not None:
            payload["health"] = health
        return payload

    def _capability_payload(entry: dict[str, Any], *, legacy_blocked: bool = False) -> dict[str, Any]:
        name = str(entry.get("name") or "unknown")
        raw_tier = str(entry.get("tier") or "system")
        raw_policy = str(entry.get("toggle_policy") or "locked")
        tier, toggle_policy = _normalized_identity(name, raw_tier, raw_policy)
        locked = tier == "system" or toggle_policy == "locked"
        config_status = _settings_config_status({}, locked=locked, legacy_blocked=legacy_blocked)
        return {
            "name": name,
            "display_name": _safe_dict(entry.get("display_name") or {}),
            "description": str(entry.get("description") or "系统能力声明"),
            "version": str(entry.get("version") or "0.0.0"),
            "priority": int(entry.get("priority") or 100),
            "enabled": True,
            "persistent_enabled": None,
            "author": "Omubot",
            "category": str(entry.get("category") or "core"),
            "tier": tier,
            "toggle_policy": toggle_policy,
            "locked": locked,
            "permissions": [],
            "capabilities": list(entry.get("capabilities") or []),
            "config_spec": {"apply_mode": "read_only", "restart_required_fields": []},
            "store": _safe_dict(entry.get("store") or {}),
            "configurable": False,
            "config_status": config_status,
            "min_omubot_version": str(entry.get("min_omubot_version") or ""),
            "hook_budget_ms": 0,
            "package": entry,
            "health": {
                "name": str(entry.get("name") or "unknown"),
                "enabled": True,
                "state": "healthy",
                "display_state": "healthy",
                "display_label": "健康",
                "display_type": "success",
                "calls": 0,
                "errors": 0,
            },
            "capability_only": True,
        }

    @router.get("/plugins/meta")
    async def plugin_meta():
        try:
            snapshot = index_service.build_index(bus=bus)
            return _plugin_meta_payload(snapshot)
        except Exception as e:
            fallback = _plugin_meta_payload({})
            fallback["error"] = str(e)
            return fallback

    @router.get("/plugins")
    async def list_plugins(include_system: bool = False):
        if bus is None:
            return {"plugins": []}

        try:
            index_payload = index_service.build_index(bus=bus)
            meta = _plugin_meta_payload(index_payload)
            if bool(meta.get("legacy_detected")):
                return _legacy_block_payload(meta, include_plugins=True)

            plugins = []
            health_map = _health_by_name()
            index_entries = {
                item["name"]: item
                for item in index_payload["entries"]
            }
            included_names: set[str] = set()
            for p in getattr(bus, "plugins", []):
                if not include_system and _is_locked(p):
                    continue
                included_names.add(getattr(p, "name", ""))
                plugins.append(_plugin_payload(
                    p,
                    health_map.get(getattr(p, "name", "")),
                    package=index_entries.get(getattr(p, "name", "")),
                ))
            if include_system:
                for entry in index_entries.values():
                    if entry.get("name") in included_names:
                        continue
                    tier, _ = _normalized_identity(
                        str(entry.get("name") or ""),
                        str(entry.get("tier") or "user"),
                        str(entry.get("toggle_policy") or "runtime"),
                    )
                    if tier == "system":
                        plugins.append(_capability_payload(entry))
            return {"plugins": plugins, "meta": meta}
        except Exception as e:
            return {"plugins": [], "error": str(e)}

    @router.get("/plugins/index")
    async def plugin_index():
        try:
            payload = index_service.build_index(bus=bus)
            payload["meta"] = _plugin_meta_payload(payload)
            return payload
        except Exception as e:
            return {
                "summary": {
                    "indexed_count": 0,
                    "loaded_count": 0,
                    "not_loaded_count": 0,
                    "local_only": True,
                    "manifest_missing_count": 0,
                    "manifest_invalid_count": 0,
                    "compatibility_issue_count": 0,
                    "external_source_count": 0,
                    "warning_count": 0,
                    "ready_to_load_count": 0,
                    "review_required_count": 0,
                    "blocked_count": 0,
                    "attention_count": 0,
                    "signature_verified_count": 0,
                    "signature_issue_count": 0,
                    "unsigned_external_count": 0,
                },
                "install_policy": {
                    "mode": "local_only",
                    "remote_install_enabled": False,
                    "detail": "当前只索引本地插件包。",
                },
                "plugin_root": str(plugin_root),
                "entries": [],
                "meta": _plugin_meta_payload({}),
                "error": str(e),
            }

    @router.get("/plugins/store")
    async def plugin_store():
        try:
            payload = index_service.build_index(bus=bus)
            payload["store_policy"] = {
                "mode": "local_read_only",
                "remote_install_enabled": False,
                "detail": "插件商店首版只展示本地包、来源和兼容状态，不执行远程安装。",
            }
            payload["meta"] = _plugin_meta_payload(payload)
            return payload
        except Exception as e:
            return {
                "summary": {"indexed_count": 0, "loaded_count": 0},
                "entries": [],
                "store_policy": {
                    "mode": "local_read_only",
                    "remote_install_enabled": False,
                    "detail": "插件商店首版只读。",
                },
                "meta": _plugin_meta_payload({}),
                "error": str(e),
            }

    @router.get("/plugins/health")
    async def plugin_health():
        if bus is None or not hasattr(bus, "plugin_health"):
            return {"plugins": []}
        try:
            return {"plugins": [_normalize_health_payload(item) for item in bus.plugin_health()]}
        except Exception as e:
            return {"plugins": [], "error": str(e)}

    @router.get("/plugins/state")
    async def plugin_state():
        if plugin_state_store is None or not hasattr(plugin_state_store, "as_payload"):
            return {"version": 1, "plugins": {}, "path": ""}
        try:
            return plugin_state_store.as_payload()
        except Exception as e:
            return {"version": 1, "plugins": {}, "path": "", "error": str(e)}

    @router.get("/plugins/{name}")
    async def get_plugin(name: str):
        if bus is None:
            return {"error": "PluginBus not available"}

        try:
            index_payload = index_service.build_index(bus=bus)
            meta = _plugin_meta_payload(index_payload)
            if bool(meta.get("legacy_detected")):
                return _legacy_block_payload(meta)

            plugin = bus.get_plugin(name)
            if plugin is None:
                entry = next((item for item in index_payload.get("entries", []) if item.get("name") == name), None)
                if entry is not None:
                    tier, _ = _normalized_identity(
                        str(entry.get("name") or ""),
                        str(entry.get("tier") or "user"),
                        str(entry.get("toggle_policy") or "runtime"),
                    )
                else:
                    tier = "user"
                if entry is not None and tier == "system":
                    return {
                        **_capability_payload(entry),
                        "dependencies": {},
                        "settings_schema": {},
                        "config_spec": {"apply_mode": "read_only", "restart_required_fields": []},
                        "store": _safe_dict(entry.get("store") or {}),
                        "settings": {
                            "schema": {},
                            "values": {},
                            "defaults": {},
                            "effective_values": {},
                            "updated_at": 0.0,
                            "apply_mode": "read_only",
                            "requires_restart": False,
                            "restart_required_fields": [],
                        },
                        "commands": [],
                        "tools": [],
                    }
                return {"error": f"Plugin '{name}' not found"}
            return {
                **_plugin_payload(
                    plugin,
                    _health_by_name().get(name),
                    package=next((item for item in index_payload.get("entries", []) if item.get("name") == name), None),
                ),
                "dependencies": dict(getattr(plugin, "dependencies", {})),
                "settings_schema": dict(getattr(plugin, "settings_schema", {}) or {}),
                "config_spec": _safe_dict(getattr(plugin, "config_spec", {})),
                "store": _safe_dict(getattr(plugin, "store", {})),
                "settings": _settings_payload(plugin),
                "commands": _plugin_commands(plugin),
                "tools": _plugin_tools(plugin),
            }
        except Exception as e:
            return {"error": str(e)}

    @router.get("/plugins/{name}/settings")
    async def get_plugin_settings(name: str):
        if bus is None:
            return {"error": "PluginBus not available"}

        try:
            index_payload = index_service.build_index(bus=bus)
            meta = _plugin_meta_payload(index_payload)
            if bool(meta.get("legacy_detected")):
                return _legacy_block_payload(meta)

            plugin = bus.get_plugin(name)
            if plugin is None:
                entry = next((item for item in index_payload.get("entries", []) if item.get("name") == name), None)
                if entry is not None:
                    tier, _ = _normalized_identity(
                        str(entry.get("name") or ""),
                        str(entry.get("tier") or "user"),
                        str(entry.get("toggle_policy") or "runtime"),
                    )
                else:
                    tier = "user"
                if entry is not None and tier == "system":
                    return {
                        "plugin": name,
                        "schema": {},
                        "values": {},
                        "defaults": {},
                        "effective_values": {},
                        "updated_at": 0.0,
                        "apply_mode": "read_only",
                        "requires_restart": False,
                        "restart_required_fields": [],
                    }
                return {"error": f"Plugin '{name}' not found"}
            return {"plugin": name, **_settings_payload(plugin)}
        except Exception as e:
            return {"error": str(e)}

    @router.post("/plugins/{name}/settings")
    async def set_plugin_settings(name: str, request: Request):
        if bus is None:
            return {"ok": False, "error": "PluginBus not available"}
        if plugin_config_store is None or not hasattr(plugin_config_store, "set_values"):
            return {"ok": False, "error": "Plugin config store not available"}

        try:
            index_payload = index_service.build_index(bus=bus)
            meta = _plugin_meta_payload(index_payload)
        except Exception:
            meta = _plugin_meta_payload({})
        if bool(meta.get("legacy_detected")):
            payload = _legacy_block_payload(meta)
            return {"ok": False, **payload}

        plugin = bus.get_plugin(name)
        if plugin is None:
            return {"ok": False, "error": f"Plugin '{name}' not found"}
        config_spec = dict(getattr(plugin, "config_spec", {}) or {})
        if _is_locked(plugin) or str(config_spec.get("apply_mode") or "") == "read_only":
            return {"ok": False, "error": "系统级插件配置只读"}

        body = await request.json()
        values = body.get("values") if isinstance(body, dict) else None
        if not isinstance(values, dict):
            return {"ok": False, "error": "values must be an object"}

        try:
            plugin_config_store.set_values(name, values)
            settings = _settings_payload(plugin)
            return {
                "ok": True,
                "plugin": name,
                "applied": settings.get("apply_mode") == "hot",
                "requires_restart": bool(settings.get("requires_restart")),
                "restart_required_fields": settings.get("restart_required_fields", []),
                "settings": settings,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @router.post("/plugins/{name}/state")
    async def set_plugin_state(name: str, request: Request):
        if bus is None or not hasattr(bus, "set_plugin_enabled"):
            return {"ok": False, "error": "PluginBus not available"}

        try:
            index_payload = index_service.build_index(bus=bus)
            meta = _plugin_meta_payload(index_payload)
        except Exception:
            meta = _plugin_meta_payload({})
        if bool(meta.get("legacy_detected")):
            payload = _legacy_block_payload(meta)
            return {"ok": False, **payload}

        body = await request.json()
        enabled = bool(body.get("enabled"))
        plugin = bus.get_plugin(name)
        if plugin is None:
            return {"ok": False, "error": f"Plugin '{name}' not found"}
        if not enabled and _is_locked(plugin):
            return {"ok": False, "error": "系统级插件无法关闭"}
        if not bus.set_plugin_enabled(name, enabled):
            return {"ok": False, "error": f"Plugin '{name}' not found"}

        if plugin_state_store is not None and hasattr(plugin_state_store, "set_enabled") and not _is_locked(plugin):
            try:
                plugin_state_store.set_enabled(name, enabled)
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"插件状态已切换，但持久化失败: {e}",
                }

        if tool_registry is not None and hasattr(tool_registry, "clear"):
            try:
                tool_registry.clear()
                for tool in bus.collect_tools():
                    tool_registry.register(tool)
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"插件状态已切换，但工具注册表刷新失败: {e}",
                }

        return {
            "ok": True,
            "plugin": _plugin_payload(plugin, _health_by_name().get(name)) if plugin else None,
        }

    @router.get("/tools")
    async def list_tools():
        if bus is None and tool_registry is None:
            return {"tools": []}

        try:
            if bus is not None:
                tools = []
                for plugin in getattr(bus, "plugins", []):
                    if not getattr(plugin, "enabled", True):
                        continue
                    tools.extend(_plugin_tools(plugin))
                return {"tools": tools}

            openai_tools = tool_registry.to_openai_tools()
            return {"tools": [{"plugin": "", **tool} for tool in openai_tools]}
        except Exception as e:
            return {"tools": [], "error": str(e)}

    @router.get("/commands")
    async def list_commands():
        if bus is None:
            return {"commands": []}

        try:
            commands = []
            for plugin in getattr(bus, "plugins", []):
                if not getattr(plugin, "enabled", True):
                    continue
                commands.extend(_plugin_commands(plugin))
            return {"commands": commands}
        except Exception as e:
            return {"commands": [], "error": str(e)}

    return router
