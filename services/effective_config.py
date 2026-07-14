"""Read-only, explainable view of Omubot's effective configuration."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from kernel.config import _CLI_MAP, _ENV_MAP, BotConfig, GroupAccessConfig
from kernel.manifest import (
    load_plugin_manifest,
    resolve_manifest_config_paths,
    validate_plugin_config_values,
)
from services.plugin_config import read_plugin_override_payload

_ENVIRONMENT_PATHS = dict(_ENV_MAP)
_CLI_ENVIRONMENT_PATHS = {
    f"_CLI_{argument.upper()}": path for argument, path in _CLI_MAP.items()
}
_SECRET_FIELDS = {
    "api_key",
    "token",
    "secret",
    "password",
    "private_key",
    "credential",
    "cookie",
}
_SECRET_SUFFIXES = tuple(_SECRET_FIELDS)


@dataclass(frozen=True)
class ConfigSource:
    kind: str
    name: str
    location: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "name": self.name, "location": self.location}


@dataclass(frozen=True)
class EffectiveConfigEntry:
    path: str
    value: Any
    source: ConfigSource
    source_chain: tuple[ConfigSource, ...]
    restart_requirement: str
    apply_mode: str
    secret: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": self.path,
            "secret": self.secret,
            "source": self.source.to_dict(),
            "source_chain": [source.to_dict() for source in self.source_chain],
            "restart_requirement": self.restart_requirement,
            "requires_restart": self.restart_requirement in {"required", "recommended"},
            "apply_mode": self.apply_mode,
        }
        if self.secret:
            present = self.value not in (None, "", [], {})
            payload["present"] = present
            payload["mask"] = "********" if present else ""
        else:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True)
class EffectiveConfigSnapshot:
    config_path: Path
    group_policy_path: Path
    entries: tuple[EffectiveConfigEntry, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "config_path": str(self.config_path),
            "group_policy_path": str(self.group_policy_path),
            "precedence": [
                "default",
                "main_config",
                "environment",
                "group_policy",
                "plugin_default",
                "plugin_override",
            ],
            "entries": [entry.to_dict() for entry in self.entries],
            "warnings": list(self.warnings),
        }


def _read_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    else:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"configuration must be an object: {path}")
    return payload


def _resolve_config_path(config_path: str | Path, root: Path) -> Path:
    configured = Path(config_path)
    if not configured.is_absolute():
        configured = root / configured
    if configured.is_file():
        return configured
    if configured.suffix.lower() == ".json":
        legacy = configured.with_suffix(".toml")
        return legacy if legacy.is_file() else configured
    if configured.suffix.lower() == ".toml":
        primary = configured.with_suffix(".json")
        return primary if primary.is_file() else configured
    primary = configured.with_suffix(".json")
    if primary.is_file():
        return primary
    legacy = configured.with_suffix(".toml")
    return legacy if legacy.is_file() else primary


def _deep_set(target: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _flatten(value: Any, *, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten(child, prefix=path))
        return flattened
    return {prefix: value} if prefix else {}


def _is_secret_path(path: str) -> bool:
    return any(
        segment in _SECRET_FIELDS or segment.endswith(_SECRET_SUFFIXES)
        for segment in path.lower().split(".")
    )


def _source(kind: str, name: str, location: str | Path) -> ConfigSource:
    return ConfigSource(kind=kind, name=name, location=str(location))


def _model_class(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        for candidate in get_args(annotation):
            nested = _model_class(candidate)
            if nested is not None:
                return nested
    return None


def _restart_hints(
    model_cls: type[BaseModel],
    *,
    prefix: str = "",
    inherited: str = "required",
) -> dict[str, str]:
    hints: dict[str, str] = {}
    for name, field in model_cls.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        hint = str(extra.get("restart_hint") or inherited)
        hints[path] = hint
        nested = _model_class(field.annotation)
        if nested is not None:
            hints.update(_restart_hints(nested, prefix=path, inherited=hint))
    return hints


def _hint_for_path(path: str, hints: Mapping[str, str]) -> str:
    candidate = path
    while candidate:
        if candidate in hints:
            return hints[candidate]
        candidate = candidate.rsplit(".", 1)[0] if "." in candidate else ""
    return "required"


def _main_entries(
    *,
    config_path: Path,
    environment: Mapping[str, str],
    warnings: list[str],
) -> list[EffectiveConfigEntry]:
    raw = _read_mapping(config_path)
    merged = deepcopy(raw)
    environment_sources: dict[str, ConfigSource] = {}
    for env_name, path in {**_ENVIRONMENT_PATHS, **_CLI_ENVIRONMENT_PATHS}.items():
        if env_name not in environment:
            continue
        _deep_set(merged, path, environment[env_name])
        environment_sources[path] = _source("environment", env_name, "environment")

    config = BotConfig.model_validate(merged)
    group_policy_path = config_path.parent / "group-policy.json"
    group_policy_source: ConfigSource | None = None
    if group_policy_path.is_file():
        try:
            policy_raw = _read_mapping(group_policy_path)
            policy_payload = policy_raw.get("access", policy_raw)
            policy = GroupAccessConfig.model_validate(policy_payload)
            config.group.access = policy
            group_policy_source = _source(
                "group_policy",
                group_policy_path.name,
                group_policy_path,
            )
        except Exception as exc:
            warnings.append(f"ignored invalid group policy {group_policy_path}: {exc}")

    defaults = _flatten(BotConfig().model_dump(mode="json"))
    explicit_main = _flatten(raw)
    effective = _flatten(config.model_dump(mode="json"))
    default_source = _source("default", "BotConfig", "kernel.config.BotConfig")
    main_source = _source("main_config", config_path.name, config_path)
    restart_hints = _restart_hints(BotConfig)
    entries: list[EffectiveConfigEntry] = []

    for path, value in sorted(effective.items()):
        source_path = path
        if path.startswith("llm.profiles.main.") and path not in explicit_main:
            legacy_path = "llm." + path.removeprefix("llm.profiles.main.")
            if legacy_path in explicit_main or legacy_path in environment_sources:
                source_path = legacy_path
        chain: list[ConfigSource] = []
        if path in defaults:
            chain.append(default_source)
        if source_path in explicit_main:
            chain.append(main_source)
        if source_path in environment_sources:
            chain.append(environment_sources[source_path])
        if group_policy_source is not None and path.startswith("group.access."):
            chain.append(group_policy_source)
        if not chain:
            chain.append(default_source)
        entries.append(
            EffectiveConfigEntry(
                path=path,
                value=value,
                source=chain[-1],
                source_chain=tuple(chain),
                restart_requirement=_hint_for_path(path, restart_hints),
                apply_mode="startup",
                secret=_is_secret_path(path),
            )
        )
    return entries


def _plugin_values(path: Path) -> dict[str, Any]:
    payload = _read_mapping(path)
    values = payload.get("values", payload)
    return values if isinstance(values, dict) else {}


def _plugin_restart_requirement(
    *,
    apply_mode: str,
    field_path: str,
    restart_required_fields: set[str],
) -> str:
    if apply_mode == "hot":
        return "none"
    if apply_mode == "read_only":
        return "not_applicable"
    if apply_mode == "restart_required":
        return (
            "required"
            if any(
                field_path == declared
                or field_path.startswith(f"{declared}.")
                for declared in restart_required_fields
            )
            else "none"
        )
    return "required"


def _plugin_entries(root: Path, warnings: list[str]) -> list[EffectiveConfigEntry]:
    entries: list[EffectiveConfigEntry] = []
    plugins_root = root / "plugins"
    if not plugins_root.is_dir():
        return entries
    for manifest_path in sorted(plugins_root.glob("*/plugin.json")):
        try:
            manifest = load_plugin_manifest(
                manifest_path,
                expected_name=manifest_path.parent.name,
            )
            plugin_name = manifest.name
            default_path, schema_path = resolve_manifest_config_paths(
                manifest_path,
                manifest,
            )
            override_path = root / "storage" / "plugins" / "config" / f"{plugin_name}.json"
            defaults = _plugin_values(default_path) if default_path.is_file() else {}
            override_payload = read_plugin_override_payload(
                override_path,
                plugin_name=plugin_name,
            )
            overrides = dict(override_payload.get("values", {}))
            effective = _deep_merge(defaults, overrides)
            validate_plugin_config_values(
                _read_mapping(schema_path),
                effective,
                plugin_name=plugin_name,
            )
            flat_defaults = _flatten(defaults)
            flat_overrides = _flatten(overrides)
            apply_mode = manifest.config.apply_mode
            restart_required_fields = set(
                manifest.config.restart_required_fields
            )
            default_source = _source("plugin_default", plugin_name, default_path)
            override_source = _source("plugin_override", plugin_name, override_path)
            for field_path, value in sorted(_flatten(effective).items()):
                chain: list[ConfigSource] = []
                if field_path in flat_defaults:
                    chain.append(default_source)
                if field_path in flat_overrides:
                    chain.append(override_source)
                if not chain:
                    continue
                full_path = f"plugins.{plugin_name}.{field_path}"
                entries.append(
                    EffectiveConfigEntry(
                        path=full_path,
                        value=value,
                        source=chain[-1],
                        source_chain=tuple(chain),
                        restart_requirement=_plugin_restart_requirement(
                            apply_mode=apply_mode,
                            field_path=field_path,
                            restart_required_fields=restart_required_fields,
                        ),
                        apply_mode=apply_mode,
                        secret=_is_secret_path(field_path),
                    )
                )
        except Exception as exc:
            warnings.append(f"ignored invalid plugin config {manifest_path}: {exc}")
    return entries


def build_effective_config_snapshot(
    *,
    config_path: str | Path,
    project_root: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> EffectiveConfigSnapshot:
    """Build a read-only snapshot without changing the existing load/save path."""
    root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
    resolved_config = _resolve_config_path(config_path, root)
    active_environment = dict(os.environ) if environment is None else dict(environment)
    warnings: list[str] = []
    entries = _main_entries(
        config_path=resolved_config,
        environment=active_environment,
        warnings=warnings,
    )
    entries.extend(_plugin_entries(root, warnings))
    entries.sort(key=lambda entry: entry.path)
    return EffectiveConfigSnapshot(
        config_path=resolved_config,
        group_policy_path=resolved_config.parent / "group-policy.json",
        entries=tuple(entries),
        warnings=tuple(warnings),
    )
