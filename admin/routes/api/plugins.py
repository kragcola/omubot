"""JSON API: plugins — plugin list, tools, commands."""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from kernel.bus import SYSTEM_PLUGIN_NAMES
from kernel.manifest import validate_plugin_config_values
from services.plugin_index import PluginIndexService
from services.plugin_toggle import PluginToggleService
from services.version import VERSION

PLUGIN_API_VERSION = 3
PLUGIN_LAYOUT_VERSION = 2
LEGACY_BLOCK_REASON = "legacy_single_file_detected"


def create_plugins_router(
    *,
    ctx: Any = None,
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
        if name in SYSTEM_PLUGIN_NAMES:
            return "system"
        return "user"

    def _normalize_toggle_policy(name: str, toggle_policy: str, tier: str) -> str:
        if name in SYSTEM_PLUGIN_NAMES or tier == "system":
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

        def serialize_command(
            cmd: Any,
            *,
            inherited_admin: bool = False,
            inherited_private: bool = False,
        ) -> dict[str, Any]:
            admin_only = inherited_admin or bool(getattr(cmd, "admin_only", False))
            private_only = inherited_private or bool(getattr(cmd, "private_only", False))
            gates: list[str] = []
            if admin_only:
                gates.append("admin")
            if private_only:
                gates.append("private")
            subcommands = [
                serialize_command(
                    subcommand,
                    inherited_admin=admin_only,
                    inherited_private=private_only,
                )
                for subcommand in list(getattr(cmd, "sub_commands", []) or [])
            ]
            return {
                "plugin": getattr(plugin, "name", "unknown"),
                "name": cmd.name,
                "description": cmd.description,
                "usage": getattr(cmd, "usage", ""),
                "pattern": getattr(cmd, "pattern", ""),
                "aliases": list(getattr(cmd, "aliases", []) or []),
                "admin_only": admin_only,
                "private_only": private_only,
                "require_args": bool(getattr(cmd, "require_args", False)),
                "hidden": bool(getattr(cmd, "hidden", False)),
                "passthrough_unknown": bool(getattr(cmd, "passthrough_unknown", False)),
                "permission": ",".join(gates) if gates else "public",
                "subcommands": subcommands,
                "sub_commands": subcommands,
            }

        commands = []
        try:
            for cmd in plugin.register_commands():
                commands.append(serialize_command(cmd))
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
        if state == "running":
            return "running", "运行中", "info"
        return state or "unknown", "状态未知", "default"

    def _normalize_health_payload(health: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(health or {})
        state = str(payload.get("state") or ("disabled" if payload.get("enabled") is False else "healthy"))
        display_state, display_label, display_type = _health_display(state)
        payload.setdefault("display_state", display_state)
        payload.setdefault("display_label", display_label)
        payload.setdefault("display_type", display_type)
        return payload

    def _capability_health(name: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "enabled": True,
            "state": "unknown",
            "calls": 0,
            "errors": 0,
        }
        if name == "vision":
            client = getattr(ctx, "vision_client", None) if ctx is not None else None
            if client is None:
                payload.update({
                    "enabled": False,
                    "state": "disabled",
                    "meta": {"available": False},
                })
                return _normalize_health_payload(payload)

            try:
                snapshot = client.health_snapshot()
            except Exception as exc:
                snapshot = {
                    "available": True,
                    "status": "failed",
                    "calls": 0,
                    "errors": 1,
                    "last_error": f"{type(exc).__name__}: {exc}",
                }
            if not isinstance(snapshot, dict):
                snapshot = {}

            available = bool(snapshot.get("available", True))
            status = str(snapshot.get("status") or ("healthy" if available else "unavailable"))
            try:
                calls = max(0, int(snapshot.get("calls", 0) or 0))
            except (TypeError, ValueError):
                calls = 0
            try:
                errors = max(0, int(snapshot.get("errors", 0) or 0))
            except (TypeError, ValueError):
                errors = 0
            last_error = str(snapshot.get("last_error") or "")
            payload.update({
                "enabled": available,
                "state": (
                    "disabled"
                    if not available
                    else {
                        "failed": "degraded",
                        "degraded": "degraded",
                        "error": "degraded",
                        "healthy": "healthy",
                        "success": "healthy",
                    }.get(status, "unknown")
                ),
                "calls": calls,
                "errors": errors,
                "last_error": last_error,
                "meta": {
                    **snapshot,
                    "available": available,
                    "status": status,
                },
            })
            return _normalize_health_payload(payload)
        if name != "history_loader":
            return _normalize_health_payload(payload)

        pipeline = getattr(ctx, "connection_pipeline", None) if ctx is not None else None
        try:
            raw_status = getattr(pipeline, "history_backfill_status", None)
            if callable(raw_status):
                raw_status = raw_status()
        except Exception as exc:
            raw_status = {
                "status": "unavailable",
                "runs": 0,
                "last_error": f"{type(exc).__name__}: {exc}",
            }
        if not isinstance(raw_status, dict):
            raw_status = {
                "status": "unavailable",
                "runs": 0,
                "last_error": "",
            }

        stage_status = str(raw_status.get("status") or "unavailable")
        try:
            runs = max(0, int(raw_status.get("runs", 0) or 0))
        except (TypeError, ValueError):
            runs = 0
        last_error = str(raw_status.get("last_error") or "")
        payload.update({
            "state": {
                "success": "healthy",
                "failed": "degraded",
                "running": "running",
                "idle": "unknown",
            }.get(stage_status, "unknown"),
            "calls": runs,
            "errors": 1 if stage_status == "failed" else 0,
            "last_error": last_error,
            "meta": {
                "status": stage_status,
                "runs": runs,
                "last_error": last_error,
            },
        })
        return _normalize_health_payload(payload)

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

    def _flatten_settings(value: Any, *, prefix: str = "") -> dict[str, Any]:
        if isinstance(value, dict) and value:
            flattened: dict[str, Any] = {}
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                flattened.update(_flatten_settings(child, prefix=path))
            return flattened
        return {prefix: value} if prefix else {}

    def _settings_payload(plugin: Any) -> dict[str, Any]:
        name = str(getattr(plugin, "name", "unknown") or "unknown")
        raw_tier = str(getattr(plugin, "tier", "user") or "user")
        raw_policy = str(getattr(plugin, "toggle_policy", "runtime") or "runtime")
        tier, toggle_policy = _normalized_identity(name, raw_tier, raw_policy)
        config_spec = dict(getattr(plugin, "config_spec", {}) or {})
        if (
            tier == "system"
            or toggle_policy == "locked"
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
                "config_error": "",
            }
        schema = dict(getattr(plugin, "settings_schema", {}) or {})
        entry = {"values": {}, "defaults": {}, "effective_values": {}, "updated_at": 0.0}
        config_error = ""
        if plugin_config_store is not None and hasattr(plugin_config_store, "get_entry"):
            try:
                entry = plugin_config_store.get_entry(getattr(plugin, "name", "unknown"))
            except Exception as exc:
                config_error = str(exc)
                entry = {"values": {}, "defaults": {}, "effective_values": {}, "updated_at": 0.0}

        apply_mode = str(
            entry.get("apply_mode")
            or config_spec.get("apply_mode")
            or "restart_required"
        )
        entry_restart_fields = entry.get("restart_required_fields")
        restart_required_fields = (
            list(entry_restart_fields)
            if isinstance(entry_restart_fields, list)
            else list(config_spec.get("restart_required_fields") or [])
        )
        if apply_mode == "read_only":
            return {
                "schema": {},
                "values": {},
                "defaults": {},
                "effective_values": {},
                "updated_at": 0.0,
                "path": str(entry.get("path") or ""),
                "default_path": str(entry.get("default_path") or ""),
                "schema_path": str(entry.get("schema_path") or ""),
                "has_saved_values": False,
                "apply_mode": "read_only",
                "requires_restart": False,
                "restart_required_fields": restart_required_fields,
                "config_error": config_error,
            }

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
            "apply_mode": apply_mode,
            "requires_restart": apply_mode == "restart_required",
            "restart_required_fields": restart_required_fields,
            "config_error": config_error,
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

    def _dependency_payload(plugin: Any) -> dict[str, dict[str, Any]]:
        dependencies = _safe_dict(getattr(plugin, "dependencies", None))
        required = {
            **dependencies,
            **_safe_dict(getattr(plugin, "required_dependencies", None)),
        }
        optional = _safe_dict(getattr(plugin, "optional_dependencies", None))
        for dependency_name in required:
            optional.pop(dependency_name, None)
        return {
            "dependencies": dependencies,
            "required_dependencies": required,
            "optional_dependencies": optional,
        }

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
            **_dependency_payload(plugin),
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
        health = _capability_health(name)
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
            "enabled": bool(health.get("enabled", False)),
            "persistent_enabled": None,
            "author": "Omubot",
            "category": str(entry.get("category") or "core"),
            "tier": tier,
            "toggle_policy": toggle_policy,
            "locked": locked,
            "permissions": [],
            "capabilities": list(entry.get("capabilities") or []),
            "dependencies": _safe_dict(entry.get("dependencies")),
            "required_dependencies": _safe_dict(entry.get("required_dependencies")),
            "optional_dependencies": _safe_dict(entry.get("optional_dependencies")),
            "config_spec": {"apply_mode": "read_only", "restart_required_fields": []},
            "store": _safe_dict(entry.get("store") or {}),
            "configurable": False,
            "config_status": config_status,
            "min_omubot_version": str(entry.get("min_omubot_version") or ""),
            "hook_budget_ms": 0,
            "package": entry,
            "health": health,
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
        config_store = plugin_config_store
        assert config_store is not None

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
        previous_settings = _settings_payload(plugin)
        config_error = str(previous_settings.get("config_error") or "")
        if config_error:
            return {"ok": False, "error": config_error}
        if (
            _is_locked(plugin)
            or str(previous_settings.get("apply_mode") or "") == "read_only"
        ):
            return {"ok": False, "error": "系统级插件配置只读"}

        body = await request.json()
        values = body.get("values") if isinstance(body, dict) else None
        if not isinstance(values, dict):
            return {"ok": False, "error": "values must be an object"}

        previous_effective = dict(previous_settings.get("effective_values") or {})
        defaults = dict(previous_settings.get("defaults") or {})
        next_effective = _merge_defaults(defaults, values)
        schema = previous_settings.get("schema")
        if isinstance(schema, dict) and schema:
            try:
                validate_plugin_config_values(
                    schema,
                    next_effective,
                    plugin_name=name,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        previous_flat = _flatten_settings(previous_effective)
        next_flat = _flatten_settings(next_effective)
        changed_fields = {
            key
            for key in previous_flat.keys() | next_flat.keys()
            if previous_flat.get(key) != next_flat.get(key)
        }
        apply_mode = str(
            previous_settings.get("apply_mode") or "restart_required"
        )
        config_spec = dict(getattr(plugin, "config_spec", {}) or {})
        restart_fields_declared = "restart_required_fields" in config_spec
        if hasattr(config_store, "get_entry"):
            try:
                manifest_entry = config_store.get_entry(name)
            except Exception:
                manifest_entry = {}
            restart_fields_declared = restart_fields_declared or isinstance(
                manifest_entry.get("restart_required_fields"),
                list,
            )
        declared_restart_fields = {
            str(field)
            for field in previous_settings.get("restart_required_fields") or []
        }
        if apply_mode == "hot":
            hot_fields = set(changed_fields)
            pending_restart_fields: set[str] = set()
        elif apply_mode == "restart_required" and restart_fields_declared:
            pending_restart_fields = {
                field
                for field in changed_fields
                if any(
                    field == declared
                    or field.startswith(f"{declared}.")
                    for declared in declared_restart_fields
                )
            }
            hot_fields = changed_fields - pending_restart_fields
        else:
            hot_fields = set()
            pending_restart_fields = set(changed_fields)

        async def apply_runtime_settings(
            effective_values: dict[str, Any],
            fields: set[str],
        ) -> set[str]:
            hook = getattr(plugin, "apply_runtime_settings", None)
            if not callable(hook):
                raise RuntimeError(
                    f"Plugin '{name}' declares hot settings but has no runtime apply hook"
                )
            result = hook(
                dict(effective_values),
                changed_fields=frozenset(fields),
            )
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, (set, frozenset, list, tuple)):
                raise RuntimeError(
                    f"Plugin '{name}' runtime apply hook returned an invalid field set"
                )
            applied = {str(field) for field in result}
            if applied != fields:
                raise RuntimeError(
                    f"Plugin '{name}' did not apply all hot settings: "
                    f"expected={sorted(fields)} applied={sorted(applied)}"
                )
            return applied

        applied_fields: set[str] = set()
        if hot_fields:
            try:
                applied_fields = await apply_runtime_settings(next_effective, hot_fields)
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await apply_runtime_settings(previous_effective, hot_fields)
                return {"ok": False, "error": str(exc)}

        try:
            config_store.set_values(name, values)
        except Exception as exc:
            if applied_fields:
                with contextlib.suppress(Exception):
                    await apply_runtime_settings(previous_effective, applied_fields)
            return {"ok": False, "error": str(exc)}

        settings = _settings_payload(plugin)
        return {
            "ok": True,
            "plugin": name,
            "applied": bool(applied_fields),
            "applied_fields": sorted(applied_fields),
            "requires_restart": bool(pending_restart_fields),
            "restart_required_fields": sorted(pending_restart_fields),
            "settings": settings,
        }

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
        service = PluginToggleService(
            bus=bus,
            tool_registry=(
                tool_registry
                if tool_registry is not None and hasattr(tool_registry, "clear")
                else None
            ),
            plugin_state_store=(
                plugin_state_store
                if plugin_state_store is not None and hasattr(plugin_state_store, "set_enabled")
                else None
            ),
            is_locked=_is_locked,
            serialize_plugin=lambda plugin: _plugin_payload(
                plugin,
                _health_by_name().get(name),
            ),
        )
        return service.toggle(name, enabled)

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
