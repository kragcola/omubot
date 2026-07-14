"""Omubot 插件清单与版本工具。

PluginManifest 是 canonical PluginManifestV3 的兼容导出。
parse_semver / check_version 提供 SemVer 版本解析与约束检查。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SEMVER_PATTERN = (
    r"^(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z0-9._]+)?(?:\+[a-zA-Z0-9._]+)?$"
)
_SEMVER_RE = re.compile(_SEMVER_PATTERN)
_OPTIONAL_SEMVER_PATTERN = (
    r"^(?:$|\d+\.\d+\.\d+(?:-[a-zA-Z0-9._]+)?(?:\+[a-zA-Z0-9._]+)?)$"
)
_VERSION_CONSTRAINT_PATTERN = (
    r"^(?:(?:>=|<=|>|<|==|\^|~)\s*)?"
    r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9._]+)?(?:\+[a-zA-Z0-9._]+)?$"
)
_VERSION_CONSTRAINT_RE = re.compile(_VERSION_CONSTRAINT_PATTERN)
_DEPENDENCY_CONSTRAINT_PATTERN = (
    r"^(?:\*|(?:(?:>=|<=|>|<|==|\^|~)\s*)?"
    r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9._]+)?(?:\+[a-zA-Z0-9._]+)?)$"
)
_PLUGIN_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

DependencyConstraint = Annotated[
    str,
    Field(pattern=_DEPENDENCY_CONSTRAINT_PATTERN),
]


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PluginDisplayName(_ManifestModel):
    zh: str = Field(min_length=1)
    en: str = Field(min_length=1)


class PluginConfigSpec(_ManifestModel):
    defaults: str = Field(min_length=1)
    schema_path: str = Field(alias="schema", min_length=1)
    apply_mode: Literal["hot", "read_only", "restart_required"]
    restart_required_fields: list[str]


class PluginStoreSpec(_ManifestModel):
    visibility: Literal["internal", "local", "marketplace_ready"]
    marketplace_id: str


ManifestPermission = Literal[
    "message",
    "prompt",
    "reply",
    "tick",
    "tool",
    "command",
    "admin",
    "storage",
    "network",
    "lifecycle",
]


class PluginManifestV3(_ManifestModel):
    """Canonical typed contract for a manifest v3 payload."""

    manifest_version: Literal[3]
    name: str = Field(min_length=1, pattern=_PLUGIN_NAME_PATTERN)
    display_name: PluginDisplayName
    description: str = Field(min_length=1)
    version: str = Field(min_length=1, pattern=_SEMVER_PATTERN)
    priority: int
    tier: Literal["system", "user"]
    toggle_policy: Literal["locked", "runtime", "restart_required"]
    category: Literal["core", "memory", "expression", "tool", "pipeline", "ops"]
    permissions: list[ManifestPermission]
    capabilities: list[str]
    author: str = Field(min_length=1)
    min_omubot_version: str = Field(pattern=_OPTIONAL_SEMVER_PATTERN)
    config: PluginConfigSpec
    store: PluginStoreSpec
    dependencies: dict[str, DependencyConstraint] = Field(default_factory=dict)
    required_dependencies: dict[str, DependencyConstraint] = Field(default_factory=dict)
    optional_dependencies: dict[str, DependencyConstraint] = Field(default_factory=dict)
    min_omu_version: str | None = None
    capability_only: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_min_version(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical = payload.get("min_omubot_version")
        legacy = payload.get("min_omu_version")
        if canonical is None and legacy is not None:
            payload["min_omubot_version"] = legacy
        elif legacy is not None and canonical != legacy:
            raise ValueError(
                "min_omu_version conflicts with min_omubot_version"
            )
        legacy_dependencies = payload.get("dependencies", {})
        required_dependencies = payload.get("required_dependencies", {})
        if isinstance(legacy_dependencies, dict) and isinstance(
            required_dependencies,
            dict,
        ):
            payload["required_dependencies"] = {
                **legacy_dependencies,
                **required_dependencies,
            }
        return payload

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if _SEMVER_RE.fullmatch(value) is None:
            raise ValueError("version must be a SemVer value")
        return value

    @field_validator("min_omubot_version")
    @classmethod
    def _validate_min_omubot_version(cls, value: str) -> str:
        if value and _SEMVER_RE.fullmatch(value) is None:
            raise ValueError("min_omubot_version must be empty or a SemVer value")
        return value

    @field_validator(
        "dependencies",
        "required_dependencies",
        "optional_dependencies",
    )
    @classmethod
    def _validate_dependency_constraints(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        for name, constraint in value.items():
            if constraint != "*" and _VERSION_CONSTRAINT_RE.fullmatch(constraint) is None:
                raise ValueError(
                    f"dependency {name!r} has an invalid version constraint"
                )
        return value


def parse_plugin_manifest_data(payload: object) -> PluginManifestV3:
    """Validate an in-memory manifest v3 payload without filesystem checks."""

    return PluginManifestV3.model_validate(payload)


class PluginManifestError(ValueError):
    """Raised when a manifest file cannot satisfy the v3 contract."""


def load_plugin_manifest(
    path: str | Path,
    *,
    expected_name: str | None = None,
) -> PluginManifestV3:
    """Load one manifest file and validate its package identity."""

    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = parse_plugin_manifest_data(payload)
    except Exception as exc:
        raise PluginManifestError(
            f"invalid plugin manifest {manifest_path}: {exc}"
        ) from exc
    if expected_name is not None and manifest.name != expected_name:
        raise PluginManifestError(
            "plugin manifest name does not match package identity: "
            f"manifest={manifest.name} expected={expected_name}"
        )
    defaults_path, schema_path = resolve_manifest_config_paths(
        manifest_path,
        manifest,
    )
    _validate_manifest_config_contract(
        manifest,
        defaults_path=defaults_path,
        schema_path=schema_path,
    )
    return manifest


def resolve_manifest_config_paths(
    manifest_path: str | Path,
    manifest: PluginManifestV3,
) -> tuple[Path, Path]:
    """Resolve declared config files without allowing package path escape."""

    package_dir = Path(manifest_path).parent.resolve()

    def resolve_member(declared: str, *, label: str) -> Path:
        relative_path = Path(declared)
        if relative_path.is_absolute():
            raise PluginManifestError(f"{label} path must be relative: {declared}")
        resolved = (package_dir / relative_path).resolve()
        try:
            resolved.relative_to(package_dir)
        except ValueError as exc:
            raise PluginManifestError(
                f"{label} path escapes plugin package: {declared}"
            ) from exc
        if not resolved.is_file():
            raise PluginManifestError(f"{label} file does not exist: {resolved}")
        return resolved

    return (
        resolve_member(manifest.config.defaults, label="config defaults"),
        resolve_member(manifest.config.schema_path, label="config schema"),
    )


def _validate_manifest_config_contract(
    manifest: PluginManifestV3,
    *,
    defaults_path: Path,
    schema_path: Path,
) -> None:
    try:
        defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PluginManifestError(
            f"plugin config JSON is invalid for {manifest.name}: {exc}"
        ) from exc
    if not isinstance(defaults, dict):
        raise PluginManifestError("plugin config defaults must be an object")
    if defaults.get("schema_version") != 1:
        raise PluginManifestError("plugin config defaults require schema_version=1")
    if defaults.get("plugin") != manifest.name:
        raise PluginManifestError(
            "plugin config defaults identity mismatch: "
            f"manifest={manifest.name} defaults={defaults.get('plugin')}"
        )
    values = defaults.get("values")
    if not isinstance(values, dict):
        raise PluginManifestError("plugin config defaults values must be an object")
    if not isinstance(schema, dict):
        raise PluginManifestError("plugin config schema must be an object")
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        raise PluginManifestError(
            "plugin config schema requires type=object and object properties"
        )
    validate_plugin_config_values(
        schema,
        values,
        plugin_name=manifest.name,
    )

    default_paths = _nested_value_paths(values)
    schema_paths = _nested_schema_paths(schema)
    missing_defaults = sorted(
        set(manifest.config.restart_required_fields) - default_paths
    )
    missing_schema = sorted(
        set(manifest.config.restart_required_fields) - schema_paths
    )
    if missing_defaults or missing_schema:
        raise PluginManifestError(
            "restart_required_fields are missing from config contract: "
            f"defaults={missing_defaults} schema={missing_schema}"
        )


def validate_plugin_config_values(
    schema: dict[str, object],
    values: dict[str, object],
    *,
    plugin_name: str = "",
) -> None:
    """Validate plugin values against the declared JSON Schema."""

    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(values)
    except ValidationError as exc:
        label = f" for {plugin_name}" if plugin_name else ""
        path = ".".join(str(part) for part in exc.absolute_path)
        location = f" at {path}" if path else ""
        raise PluginManifestError(
            "plugin config schema validation failed"
            f"{label}{location}: {exc.message}"
        ) from exc
    except SchemaError as exc:
        label = f" for {plugin_name}" if plugin_name else ""
        raise PluginManifestError(
            f"plugin config schema is invalid{label}: {exc.message}"
        ) from exc


def _nested_value_paths(value: dict[str, object], *, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.add(path)
        if isinstance(child, dict):
            paths.update(_nested_value_paths(child, prefix=path))
    return paths


def _nested_schema_paths(schema: dict[str, object], *, prefix: str = "") -> set[str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return set()
    paths: set[str] = set()
    for key, child in properties.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        paths.add(path)
        if isinstance(child, dict):
            paths.update(_nested_schema_paths(child, prefix=path))
    return paths


PluginManifest = PluginManifestV3


def parse_semver(version: str) -> tuple[int, int, int]:
    """解析 SemVer 字符串为 (major, minor, patch) 三元组。

    预发布标识（-alpha.1）和构建元数据（+build）被忽略。
    无效版本返回 (0, 0, 0)。
    """
    m = _SEMVER_RE.match(version.strip())
    if m is None:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def check_version(actual: str, constraint: str) -> bool:
    """检查 actual 版本是否满足 constraint 约束。

    支持的约束操作符：
    - ">=1.2.0", ">1.0.0", "<=2.0.0", "<2.0.0", "==1.0.0"
    - "^1.2.3" — 兼容版本（>=1.2.3, <2.0.0）
    - "~1.2.3" — 近似版本（>=1.2.3, <1.3.0）
    - "*" — 任意版本
    - 纯版本号（如 "1.2.3"）— 等效于 ">=1.2.3"
    """
    constraint = constraint.strip()
    actual_t = parse_semver(actual)

    if constraint == "*":
        return True

    # 操作符前缀匹配
    op_match = re.match(r"^(>=|<=|>|<|==|\^|~)\s*(\S.*)", constraint)
    if op_match:
        op = op_match.group(1)
        ver_str = op_match.group(2).strip()
    else:
        op = ">="
        ver_str = constraint

    req_t = parse_semver(ver_str)

    if op == "==":
        return actual_t == req_t
    if op == ">=":
        return actual_t >= req_t
    if op == "<=":
        return actual_t <= req_t
    if op == ">":
        return actual_t > req_t
    if op == "<":
        return actual_t < req_t
    if op == "^":
        # ^1.2.3: >=1.2.3, <2.0.0 (unless major is 0, then ^0.x.y pins minor)
        upper = (0, req_t[1] + 1, 0) if req_t[0] == 0 else (req_t[0] + 1, 0, 0)
        return actual_t >= req_t and actual_t < upper
    if op == "~":
        # ~1.2.3: >=1.2.3, <1.3.0
        upper = (req_t[0], req_t[1] + 1, 0)
        return actual_t >= req_t and actual_t < upper

    return False
