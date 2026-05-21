"""JSON API: config — structured editor with JSON primary / TOML compatibility."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from fastapi import APIRouter, Request
from pydantic import BaseModel, ValidationError

from kernel.config import BotConfig
from services.config_audit import ConfigAuditStore
from services.config_backup import ConfigBackupStore

_SECRET_KEYWORDS = ("api_key", "token", "secret", "password")
_MISSING = object()
_FIELD_DISPLAY_KEYS = (
    "display_label",
    "help",
    "example",
    "recommended",
    "risk_level",
    "restart_hint",
)


def _to_label(name: str) -> str:
    return name.replace("_", " ")


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return f"{value[:1]}***{value[-1:]}"
    return f"{value[:3]}***{value[-2:]}"


def _is_secret_path(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in _SECRET_KEYWORDS)


def _is_union(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin is Union or origin is UnionType


def _unwrap_annotation(annotation: Any) -> Any:
    ann = annotation
    while True:
        origin = get_origin(ann)
        if origin is None:
            return ann
        if origin is Annotated:
            args = get_args(ann)
            ann = args[0] if args else ann
            continue
        if _is_union(ann):
            args = [arg for arg in get_args(ann) if arg is not type(None)]
            if len(args) == 1:
                ann = args[0]
                continue
        return ann


def _base_scalar_kind(annotation: Any, path: str) -> tuple[str, dict[str, Any]]:
    ann = _unwrap_annotation(annotation)
    origin = get_origin(ann)
    if origin is Literal:
        options = [item for item in get_args(ann)]
        return "select", {"options": options}
    if ann is bool:
        return "switch", {}
    if ann in (int, float):
        return "number", {"number_type": "int" if ann is int else "float"}
    if ann is str:
        return "text", {"secret": _is_secret_path(path)}
    return "json", {}


def _build_field_schema(
    *,
    key: str,
    path: str,
    annotation: Any,
    description: str | None,
    required: bool,
    field_info: Any | None = None,
) -> dict[str, Any]:
    ann = _unwrap_annotation(annotation)
    origin = get_origin(ann)

    schema: dict[str, Any] = {
        "key": key,
        "path": path,
        "label": _to_label(key),
        "description": description or "",
        "required": required,
    }
    extra = getattr(field_info, "json_schema_extra", None) or {}
    if isinstance(extra, dict):
        for display_key in _FIELD_DISPLAY_KEYS:
            value = extra.get(display_key)
            if value is not None:
                schema[display_key] = value

    if isinstance(ann, type) and issubclass(ann, BaseModel):
        children: list[dict[str, Any]] = []
        for child_key, child_info in ann.model_fields.items():
            child_path = f"{path}.{child_key}"
            children.append(_build_field_schema(
                key=child_key,
                path=child_path,
                annotation=child_info.annotation,
                description=child_info.description,
                required=child_info.is_required(),
                field_info=child_info,
            ))
        schema["kind"] = "object"
        schema["children"] = children
        return schema

    if origin is list:
        args = get_args(ann)
        item_ann = args[0] if args else Any
        item_kind, extras = _base_scalar_kind(item_ann, f"{path}[]")
        if item_kind not in {"switch", "number", "text", "select"}:
            schema["kind"] = "json"
            return schema
        schema["kind"] = "list"
        schema["item_kind"] = item_kind
        schema.update(extras)
        return schema

    if origin is dict:
        args = get_args(ann)
        key_ann = args[0] if len(args) > 0 else str
        value_ann = args[1] if len(args) > 1 else Any
        if key_ann is not str:
            schema["kind"] = "json"
            return schema
        value_kind, extras = _base_scalar_kind(value_ann, f"{path}.*")
        if value_kind not in {"switch", "number", "text", "select"}:
            schema["kind"] = "json"
            return schema
        schema["kind"] = "kv"
        schema["value_kind"] = value_kind
        schema.update(extras)
        return schema

    kind, extras = _base_scalar_kind(ann, path)
    schema["kind"] = kind
    schema.update(extras)
    return schema


def _build_schema() -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for key, info in BotConfig.model_fields.items():
        fields.append(_build_field_schema(
            key=key,
            path=key,
            annotation=info.annotation,
            description=info.description,
            required=info.is_required(),
            field_info=info,
        ))
    return fields


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


def _read_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        payload = tomllib.load(fh)
    if not isinstance(payload, dict):
        raise ValueError("配置 TOML 必须是对象")
    return payload


def _resolve_source_data(target_json: Path) -> tuple[dict[str, Any], str, bool]:
    legacy_toml = target_json.with_suffix(".toml")
    if target_json.is_file():
        return _read_json(target_json), "json", False
    if legacy_toml.is_file():
        return _read_toml(legacy_toml), "legacy", True
    return {}, "json", False


def _default_audit_path(target_json: Path) -> Path:
    normalized = target_json.as_posix().replace("\\", "/")
    if normalized.endswith("config/config.json"):
        return target_json.parent.parent / "storage" / "config" / "config-audit.json"
    return target_json.parent / "config-audit.json"


def _default_backup_path(target_json: Path) -> Path:
    normalized = target_json.as_posix().replace("\\", "/")
    if normalized.endswith("config/config.json"):
        return target_json.parent.parent / "storage" / "config" / "config-backups.json"
    return target_json.parent / "config-backups.json"


def _get_value(values: dict[str, Any], dotted_path: str) -> Any:
    node: Any = values
    for part in dotted_path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _collect_secret_masks(schema: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, str]:
    masks: dict[str, str] = {}

    def walk(node: dict[str, Any]) -> None:
        kind = node.get("kind")
        if kind == "object":
            for child in node.get("children", []):
                walk(child)
            return
        if kind == "text" and node.get("secret"):
            raw = _get_value(values, str(node.get("path", "")))
            if isinstance(raw, str) and raw:
                masks[str(node["path"])] = _mask_secret(raw)

    for item in schema:
        walk(item)
    return masks


def _serialize_error_path(loc: tuple[Any, ...]) -> str:
    cleaned = [str(part) for part in loc if part != "__root__"]
    return ".".join(cleaned)


def _preview_value(path: str, value: Any) -> str:
    if value is _MISSING:
        return "未设置"
    if value is None:
        return "null"
    if _is_secret_path(path):
        if isinstance(value, str):
            return _mask_secret(value)
        return "***"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if len(value) > 160:
            return f"{value[:157]}..."
        return value
    rendered = json.dumps(value, ensure_ascii=False)
    return rendered if len(rendered) <= 200 else f"{rendered[:197]}..."


def _build_change(path: str, change_type: str, before: Any, after: Any) -> dict[str, Any]:
    return {
        "path": path,
        "top_level": path.split(".", 1)[0] if path else "",
        "change_type": change_type,
        "secret": _is_secret_path(path),
        "before_display": _preview_value(path, before),
        "after_display": _preview_value(path, after),
    }


def _diff_values(before: Any, after: Any, *, path: str = "") -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    if before is _MISSING:
        changes.append(_build_change(path, "added", before, after))
        return changes
    if after is _MISSING:
        changes.append(_build_change(path, "removed", before, after))
        return changes

    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before) | set(after))
        for key in keys:
            next_path = f"{path}.{key}" if path else str(key)
            changes.extend(_diff_values(
                before.get(key, _MISSING),
                after.get(key, _MISSING),
                path=next_path,
            ))
        return changes

    if before != after:
        changes.append(_build_change(path, "changed", before, after))
    return changes


def _summarize_changes(changes: list[dict[str, Any]]) -> dict[str, Any]:
    added = sum(1 for item in changes if item.get("change_type") == "added")
    removed = sum(1 for item in changes if item.get("change_type") == "removed")
    changed = sum(1 for item in changes if item.get("change_type") == "changed")
    top_levels = sorted({str(item.get("top_level") or "") for item in changes if item.get("top_level")})
    return {
        "total": len(changes),
        "added": added,
        "removed": removed,
        "changed": changed,
        "top_level_count": len(top_levels),
        "top_levels": top_levels[:16],
    }


def _validate_incoming_payload(body: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    mode = str(body.get("mode", "structured") or "structured")

    if mode == "advanced":
        raw_json = str(body.get("raw_json", "")).strip()
        if not raw_json:
            raise ValueError("高级模式内容不能为空")
        incoming = json.loads(raw_json)
    else:
        incoming = body.get("values", {})

    if not isinstance(incoming, dict):
        raise ValueError("配置数据必须是对象")

    model = BotConfig.model_validate(incoming)
    values = model.model_dump(mode="python")
    return mode, incoming, values


def _serialize_config_payload(
    *,
    schema: list[dict[str, Any]],
    target_json_path: Path,
    values: dict[str, Any],
    format_mode: str,
    migration_pending: bool,
) -> dict[str, Any]:
    return {
        "path": str(target_json_path),
        "format_mode": format_mode,
        "migration_pending": migration_pending,
        "editor": {
            "schema": schema,
            "values": values,
            "secret_masks": _collect_secret_masks(schema, values),
        },
        "advanced": {
            "raw_json": json.dumps(values, ensure_ascii=False, indent=2),
        },
    }


def _write_config_json(target_json_path: Path, values: dict[str, Any]) -> None:
    target_json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target_json_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target_json_path)


def create_config_router(*, config_path: str = "config/config.json") -> APIRouter:
    router = APIRouter()
    schema = _build_schema()
    target_json_path = _normalize_json_path(config_path)
    audit_store = ConfigAuditStore(_default_audit_path(target_json_path))
    backup_store = ConfigBackupStore(_default_backup_path(target_json_path))

    @router.get("/config")
    async def get_config():
        raw_data, format_mode, migration_pending = _resolve_source_data(target_json_path)
        model = BotConfig.model_validate(raw_data)
        values = model.model_dump(mode="python")
        return _serialize_config_payload(
            schema=schema,
            target_json_path=target_json_path,
            values=values,
            format_mode=format_mode,
            migration_pending=migration_pending,
        )

    @router.post("/config/preview")
    async def preview_config(request: Request):
        body = await request.json()
        try:
            mode, _incoming, values = _validate_incoming_payload(body if isinstance(body, dict) else {})
            current_raw, _format_mode, _migration_pending = _resolve_source_data(target_json_path)
            current_values = BotConfig.model_validate(current_raw).model_dump(mode="python")
            changes = _diff_values(current_values, values)
            summary = _summarize_changes(changes)
            return {
                "ok": True,
                "mode": mode,
                "summary": summary,
                "changes": changes[:120],
                "advanced": {
                    "raw_json": json.dumps(values, ensure_ascii=False, indent=2),
                },
            }
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"JSON 解析失败: {exc}"}
        except ValidationError as exc:
            field_errors = []
            for err in exc.errors():
                loc = err.get("loc", ())
                field_errors.append({
                    "path": _serialize_error_path(tuple(loc)),
                    "message": err.get("msg", "字段校验失败"),
                })
            return {"ok": False, "error": "配置校验失败", "field_errors": field_errors}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"预览失败: {exc}"}

    @router.get("/config/history")
    async def config_history(limit: int = 8):
        safe_limit = min(max(limit, 1), 20)
        return audit_store.as_payload(limit=safe_limit)

    @router.get("/config/backups")
    async def config_backups(limit: int = 8):
        safe_limit = min(max(limit, 1), 20)
        return backup_store.as_payload(limit=safe_limit)

    @router.post("/config")
    async def save_config(request: Request):
        body = await request.json()

        try:
            mode, _incoming, values = _validate_incoming_payload(body if isinstance(body, dict) else {})
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"JSON 解析失败: {exc}"}
        except ValidationError as exc:
            field_errors = []
            for err in exc.errors():
                loc = err.get("loc", ())
                field_errors.append({
                    "path": _serialize_error_path(tuple(loc)),
                    "message": err.get("msg", "字段校验失败"),
                })
            return {"ok": False, "error": "配置校验失败", "field_errors": field_errors}
        except Exception as exc:
            return {"ok": False, "error": f"保存失败: {exc}"}

        current_raw, _format_mode, _migration_pending = _resolve_source_data(target_json_path)
        current_values = BotConfig.model_validate(current_raw).model_dump(mode="python")
        changes = _diff_values(current_values, values)
        summary = _summarize_changes(changes)

        _write_config_json(target_json_path, values)
        audit_entry = audit_store.append(
            config_path=str(target_json_path),
            mode=mode,
            summary=summary,
            changes=changes,
        )
        backup_entry = backup_store.append(
            config_path=str(target_json_path),
            values=values,
            trigger="save",
            mode=mode,
            summary=summary,
            note="配置保存后快照",
        )

        return {
            "ok": True,
            **_serialize_config_payload(
                schema=schema,
                target_json_path=target_json_path,
                values=values,
                format_mode="json",
                migration_pending=False,
            ),
            "diff": {
                "summary": summary,
                "changes": changes[:120],
            },
            "audit_entry": audit_entry,
            "backup_entry": backup_entry,
            "message": "配置已保存为 JSON。部分配置需重启后生效。",
        }

    @router.post("/config/restore")
    async def restore_config(request: Request):
        body = await request.json()
        payload = body if isinstance(body, dict) else {}
        backup_id = str(payload.get("backup_id", "")).strip()
        if not backup_id:
            return {"ok": False, "error": "缺少 backup_id"}

        try:
            backup_values = backup_store.get_values(backup_id)
            values = BotConfig.model_validate(backup_values).model_dump(mode="python")
        except KeyError:
            return {"ok": False, "error": "指定快照不存在"}
        except Exception as exc:
            return {"ok": False, "error": f"读取快照失败: {exc}"}

        current_raw, _format_mode, _migration_pending = _resolve_source_data(target_json_path)
        current_values = BotConfig.model_validate(current_raw).model_dump(mode="python")
        changes = _diff_values(current_values, values)
        summary = _summarize_changes(changes)

        if summary["total"] > 0:
            backup_store.append(
                config_path=str(target_json_path),
                values=current_values,
                trigger="pre_restore",
                mode="restore",
                summary=summary,
                note=f"恢复 {backup_id} 前自动备份",
                source_backup_id=backup_id,
            )

        _write_config_json(target_json_path, values)
        audit_entry = audit_store.append(
            config_path=str(target_json_path),
            mode="restore",
            summary=summary,
            changes=changes,
        )
        backup_entry = backup_store.append(
            config_path=str(target_json_path),
            values=values,
            trigger="restore",
            mode="restore",
            summary=summary,
            note=f"从快照 {backup_id} 恢复后的配置",
            source_backup_id=backup_id,
        )

        return {
            "ok": True,
            **_serialize_config_payload(
                schema=schema,
                target_json_path=target_json_path,
                values=values,
                format_mode="json",
                migration_pending=False,
            ),
            "diff": {
                "summary": summary,
                "changes": changes[:120],
            },
            "audit_entry": audit_entry,
            "backup_entry": backup_entry,
            "message": "已恢复配置快照。部分配置需重启后生效。",
        }

    return router
