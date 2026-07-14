#!/usr/bin/env python3
"""Validate repository plugin manifests without importing plugin code."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kernel.manifest import (  # noqa: E402
    PluginManifestV3,
    check_version,
    load_plugin_manifest,
)
from kernel.version import VERSION  # noqa: E402


def plugin_inventory_violations(plugin_root: Path) -> list[tuple[str, Path]]:
    """Return repository plugin entrypoints that bypass the manifest inventory."""
    if not plugin_root.is_dir():
        return []

    violations: list[tuple[str, Path]] = []
    for item in sorted(plugin_root.iterdir()):
        if item.is_file() and item.suffix == ".py" and item.name != "__init__.py":
            violations.append(("legacy_root", item))
            continue
        if item.is_file() and item.suffix in {".toml", ".json"}:
            stem = item.stem
            package_root = plugin_root / stem
            if (package_root / "plugin.py").is_file() or (
                package_root / "plugin.json"
            ).is_file():
                continue
            violations.append(("legacy_root", item))

    for plugin_path in sorted(plugin_root.glob("*/plugin.py")):
        if not (plugin_path.parent / "plugin.json").is_file():
            violations.append(("missing_manifest", plugin_path))
    return violations


def _plugin_class_metadata(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(
            isinstance(base, ast.Name) and base.id == "AmadeusPlugin"
            for base in node.bases
        ):
            continue
        metadata: dict[str, Any] = {}
        for statement in node.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            if isinstance(statement, ast.Assign):
                targets = statement.targets
                value_node = statement.value
            else:
                targets = [statement.target]
                value_node = statement.value
            if value_node is None:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id not in {
                    "name",
                    "description",
                    "version",
                    "priority",
                    "dependencies",
                    "required_dependencies",
                    "optional_dependencies",
                }:
                    continue
                try:
                    metadata[target.id] = ast.literal_eval(value_node)
                except (ValueError, TypeError):
                    continue
        return metadata
    raise ValueError(f"no AmadeusPlugin subclass found in {path}")


def _required_cycle_nodes(
    manifests: dict[str, PluginManifestV3],
) -> set[str]:
    graph = {
        name: set(manifest.dependencies) | set(manifest.required_dependencies)
        for name, manifest in manifests.items()
    }
    state: dict[str, int] = {}
    stack: list[str] = []
    stack_positions: dict[str, int] = {}
    cycles: set[str] = set()

    def visit(name: str) -> None:
        state[name] = 1
        stack_positions[name] = len(stack)
        stack.append(name)
        for dependency in graph.get(name, set()):
            if dependency not in graph:
                continue
            if state.get(dependency, 0) == 0:
                visit(dependency)
            elif state.get(dependency) == 1:
                cycles.update(stack[stack_positions[dependency]:])
        stack.pop()
        stack_positions.pop(name, None)
        state[name] = 2

    for name in graph:
        if state.get(name, 0) == 0:
            visit(name)
    return cycles


def validate_repository(root: Path, *, omubot_version: str) -> list[str]:
    errors: list[str] = []
    plugin_root = root / "plugins"
    for violation, path in plugin_inventory_violations(plugin_root):
        if violation == "missing_manifest":
            errors.append(
                f"{path.parent.name}: plugin.py requires sibling plugin.json"
            )
        else:
            errors.append(
                "legacy root-level plugin file: "
                f"{path.relative_to(root).as_posix()}"
            )

    schema_path = root / "schemas" / "plugin-manifest-v3.schema.json"
    expected_schema = PluginManifestV3.model_json_schema(by_alias=True)
    try:
        actual_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"schema unreadable: {schema_path}: {exc}")
    else:
        if actual_schema != expected_schema:
            errors.append("checked-in plugin manifest schema is out of sync")

    manifests: dict[str, PluginManifestV3] = {}
    manifest_paths = sorted(plugin_root.glob("*/plugin.json"))
    for manifest_path in manifest_paths:
        package_name = manifest_path.parent.name
        try:
            manifest = load_plugin_manifest(
                manifest_path,
                expected_name=package_name,
            )
        except ValueError as exc:
            errors.append(f"{package_name}: {exc}")
            continue
        if manifest.name in manifests:
            errors.append(f"duplicate plugin manifest name: {manifest.name}")
            continue
        manifests[manifest.name] = manifest
        if manifest.min_omubot_version and not check_version(
            omubot_version,
            manifest.min_omubot_version,
        ):
            errors.append(
                f"{manifest.name}: requires Omubot {manifest.min_omubot_version}, "
                f"current is {omubot_version}"
            )

        plugin_path = manifest_path.parent / "plugin.py"
        if manifest.capability_only:
            continue
        if not plugin_path.is_file():
            errors.append(
                f"{manifest.name}: runtime plugin requires plugin.py or capability_only=true"
            )
            continue
        try:
            class_metadata = _plugin_class_metadata(plugin_path)
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append(f"{manifest.name}: {exc}")
            continue
        for field_name in ("name", "description", "version", "priority"):
            class_value = class_metadata.get(field_name)
            manifest_value = getattr(manifest, field_name)
            if class_value != manifest_value:
                errors.append(
                    f"{manifest.name}: class/manifest {field_name} mismatch "
                    f"class={class_value!r} manifest={manifest_value!r}"
                )
        class_required = {
            **dict(class_metadata.get("dependencies") or {}),
            **dict(class_metadata.get("required_dependencies") or {}),
        }
        manifest_required = {
            **manifest.dependencies,
            **manifest.required_dependencies,
        }
        class_declares_required = any(
            field_name in class_metadata
            for field_name in ("dependencies", "required_dependencies")
        )
        if class_declares_required and class_required != manifest_required:
            errors.append(
                f"{manifest.name}: class/manifest required_dependencies mismatch"
            )
        class_optional = dict(class_metadata.get("optional_dependencies") or {})
        if (
            "optional_dependencies" in class_metadata
            and class_optional != manifest.optional_dependencies
        ):
            errors.append(
                f"{manifest.name}: class/manifest optional_dependencies mismatch"
            )

    names = set(manifests)
    for name, manifest in manifests.items():
        required = {**manifest.dependencies, **manifest.required_dependencies}
        optional = dict(manifest.optional_dependencies)
        for dependency_name in required:
            optional.pop(dependency_name, None)
        for dependency_name in {*required, *optional}:
            if dependency_name == name:
                errors.append(f"{name}: self dependency is not allowed")
            elif dependency_name not in names:
                errors.append(
                    f"{name}: dependency target does not exist: {dependency_name}"
                )
    cycle_nodes = _required_cycle_nodes(manifests)
    if cycle_nodes:
        errors.append(
            f"required dependency cycle detected: {', '.join(sorted(cycle_nodes))}"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--omubot-version", default=VERSION)
    args = parser.parse_args()
    root = args.root.resolve()
    errors = validate_repository(root, omubot_version=args.omubot_version)
    if errors:
        for error in errors:
            print(f"[plugin-manifest] {error}", file=sys.stderr)
        return 1
    manifest_count = len(list((root / "plugins").glob("*/plugin.json")))
    print(f"validated {manifest_count} plugin manifests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
