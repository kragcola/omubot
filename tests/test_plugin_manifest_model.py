import json
import subprocess
import sys
from pathlib import Path

import pytest

import kernel.manifest as manifest_module
import kernel.types as types_module
from scripts.check_plugin_manifests import validate_repository


def _valid_manifest_payload() -> dict[str, object]:
    return {
        "manifest_version": 3,
        "name": "sample",
        "display_name": {"zh": "示例", "en": "Sample"},
        "description": "A sample plugin manifest.",
        "version": "1.2.3",
        "priority": 100,
        "tier": "user",
        "toggle_policy": "runtime",
        "category": "tool",
        "permissions": [],
        "capabilities": [],
        "author": "Omubot",
        "min_omubot_version": "0.1.0",
        "config": {
            "defaults": "config.default.json",
            "schema": "config.schema.json",
            "apply_mode": "hot",
            "restart_required_fields": [],
        },
        "store": {"visibility": "local", "marketplace_id": ""},
    }


def _write_repository_manifest_schema(root: Path) -> None:
    schema_dir = root / "schemas"
    schema_dir.mkdir(parents=True)
    (schema_dir / "plugin-manifest-v3.schema.json").write_text(
        json.dumps(
            manifest_module.PluginManifestV3.model_json_schema(by_alias=True),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_parse_plugin_manifest_data_rejects_unknown_top_level_field() -> None:
    parser = getattr(
        manifest_module,
        "parse_plugin_manifest_data",
        lambda payload: payload,
    )
    payload = _valid_manifest_payload()
    payload["unexpected_field"] = "must be rejected"

    with pytest.raises(ValueError):
        parser(payload)


def test_parse_plugin_manifest_data_rejects_invalid_plugin_identity_name() -> None:
    payload = _valid_manifest_payload()
    payload["name"] = "Bad-Name"

    with pytest.raises(ValueError):
        manifest_module.parse_plugin_manifest_data(payload)


def test_parse_plugin_manifest_data_rejects_non_semver_version() -> None:
    parser = getattr(
        manifest_module,
        "parse_plugin_manifest_data",
        lambda payload: payload,
    )
    payload = _valid_manifest_payload()
    payload["version"] = "1.2"

    with pytest.raises(ValueError):
        parser(payload)


def test_parse_plugin_manifest_data_normalizes_legacy_min_omu_version() -> None:
    parser = getattr(
        manifest_module,
        "parse_plugin_manifest_data",
        lambda payload: payload,
    )
    payload = _valid_manifest_payload()
    del payload["min_omubot_version"]
    payload["min_omu_version"] = "1.2.3"

    try:
        parsed = parser(payload)
    except ValueError:
        actual = None
    else:
        actual = getattr(parsed, "min_omubot_version", None)

    assert actual == "1.2.3"


def test_repository_plugin_manifests_satisfy_canonical_contract() -> None:
    parser = getattr(
        manifest_module,
        "parse_plugin_manifest_data",
        lambda payload: payload,
    )
    repo_root = Path(__file__).resolve().parents[1]
    errors = {}

    for path in sorted(repo_root.glob("plugins/*/plugin.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            parser(payload)
        except ValueError as exc:
            errors[path.parent.name] = str(exc)

    assert errors == {}


def test_parse_plugin_manifest_data_rejects_non_semver_min_omubot_version() -> None:
    parser = getattr(
        manifest_module,
        "parse_plugin_manifest_data",
        lambda payload: payload,
    )
    payload = _valid_manifest_payload()
    payload["min_omubot_version"] = "1.2"

    with pytest.raises(ValueError):
        parser(payload)


def test_repository_schema_matches_plugin_manifest_v3_model() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "schemas" / "plugin-manifest-v3.schema.json"
    model = getattr(manifest_module, "PluginManifestV3", None)
    expected = {} if model is None else model.model_json_schema(by_alias=True)
    actual = (
        json.loads(schema_path.read_text(encoding="utf-8"))
        if schema_path.exists()
        else None
    )

    assert actual == expected


def test_parse_plugin_manifest_data_rejects_non_semver_dependency_constraint() -> None:
    parser = getattr(
        manifest_module,
        "parse_plugin_manifest_data",
        lambda payload: payload,
    )
    payload = _valid_manifest_payload()
    payload["required_dependencies"] = {"context": ">=1.2"}

    with pytest.raises(ValueError):
        parser(payload)


def test_parse_plugin_manifest_data_merges_legacy_required_dependencies() -> None:
    payload = _valid_manifest_payload()
    payload["dependencies"] = {"context": ">=1.0.0"}
    payload["required_dependencies"] = {"style": ">=1.0.0"}

    parsed = manifest_module.parse_plugin_manifest_data(payload)

    assert parsed.required_dependencies == {
        "context": ">=1.0.0",
        "style": ">=1.0.0",
    }


def test_plugin_manifest_public_alias_is_canonical_v3_model() -> None:
    canonical = getattr(manifest_module, "PluginManifestV3", None)
    legacy = getattr(manifest_module, "PluginManifest", None)

    assert canonical is not None
    assert legacy is canonical


def test_amadeus_plugin_dependency_maps_are_independent_empty_dicts() -> None:
    plugin = types_module.AmadeusPlugin()
    dependencies = getattr(plugin, "dependencies", None)
    required_dependencies = getattr(plugin, "required_dependencies", None)
    optional_dependencies = getattr(plugin, "optional_dependencies", None)

    assert dependencies == {}
    assert required_dependencies == {}
    assert optional_dependencies == {}
    assert dependencies is not required_dependencies
    assert dependencies is not optional_dependencies
    assert required_dependencies is not optional_dependencies


def test_repository_plugin_dependency_graph_matches_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    plugin_names = (
        "memo",
        "knowledge",
        "style",
        "datetime",
        "food",
        "schedule",
        "dream",
    )
    manifests = {
        name: json.loads(
            (repo_root / "plugins" / name / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        for name in plugin_names
    }
    memo_optional = manifests["memo"].get("optional_dependencies", {})
    knowledge_optional = manifests["knowledge"].get("optional_dependencies", {})
    style_optional = manifests["style"].get("optional_dependencies", {})
    datetime_optional = manifests["datetime"].get("optional_dependencies", {})
    food_optional = manifests["food"].get("optional_dependencies", {})
    schedule_required = manifests["schedule"].get("required_dependencies", {})
    dream_optional = manifests["dream"].get("optional_dependencies", {})

    assert memo_optional == {"context": ">=0.1.0"}
    assert knowledge_optional == {"context": ">=0.1.0"}
    assert style_optional == {"slang": ">=0.1.0"}
    assert datetime_optional == {"calendar_context": ">=1.0.0"}
    assert "web_search" in food_optional
    assert "calendar_context" in schedule_required
    assert "calendar_context" not in dream_optional


def test_plugin_manifest_ci_gate_validates_repository_manifests() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "check_plugin_manifests.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--root", str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "validated 24 plugin manifests" in result.stdout


def test_plugin_manifest_ci_gate_rejects_class_description_drift(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    schema_dir = tmp_path / "schemas"
    plugin_dir = tmp_path / "plugins" / "sample"
    schema_dir.mkdir(parents=True)
    plugin_dir.mkdir(parents=True)
    (schema_dir / "plugin-manifest-v3.schema.json").write_text(
        json.dumps(
            manifest_module.PluginManifestV3.model_json_schema(by_alias=True),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_payload = _valid_manifest_payload()
    manifest_payload["optional_dependencies"] = {"sample": ">=1.0.0"}
    (plugin_dir / "plugin.json").write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (plugin_dir / "config.default.json").write_text(
        json.dumps({"schema_version": 1, "plugin": "sample", "values": {}}),
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        "\n".join(
            (
                "from kernel.types import AmadeusPlugin",
                "",
                "class SamplePlugin(AmadeusPlugin):",
                '    name = "sample"',
                '    description = "stale class description"',
                '    version = "1.2.3"',
                "    priority = 100",
                "    optional_dependencies = {}",
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "check_plugin_manifests.py"),
            "--root",
            str(tmp_path),
            "--omubot-version",
            "99.0.0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "class/manifest description mismatch" in result.stderr
    assert "class/manifest optional_dependencies mismatch" in result.stderr


def test_validate_repository_rejects_plugin_module_without_sibling_manifest(
    tmp_path: Path,
) -> None:
    _write_repository_manifest_schema(tmp_path)
    orphan_dir = tmp_path / "plugins" / "orphan"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "plugin.py").write_text(
        "from kernel.types import AmadeusPlugin\n",
        encoding="utf-8",
    )

    errors = validate_repository(tmp_path, omubot_version="99.0.0")

    assert errors == [
        "orphan: plugin.py requires sibling plugin.json",
    ]


def test_plugin_manifest_cli_rejects_legacy_root_plugin_file(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    _write_repository_manifest_schema(tmp_path)
    plugin_root = tmp_path / "plugins"
    helper_dir = plugin_root / "shared"
    helper_dir.mkdir(parents=True)
    (plugin_root / "__init__.py").write_text("", encoding="utf-8")
    (helper_dir / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    legacy_path = plugin_root / "legacy_plugin.py"
    legacy_path.write_text("class LegacyPlugin: pass\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "check_plugin_manifests.py"),
            "--root",
            str(tmp_path),
            "--omubot-version",
            "99.0.0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "legacy root-level plugin file: plugins/legacy_plugin.py" in result.stderr
    assert "plugins/__init__.py" not in result.stderr
    assert "plugins/shared/helper.py" not in result.stderr


def test_typed_boundaries_workflow_runs_plugin_manifest_gate() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow_path = repo_root / ".github" / "workflows" / "typed-boundaries.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "uv run python scripts/check_plugin_manifests.py" in workflow


def test_typed_boundaries_workflow_runs_dependency_recovery_pytest_gate() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow_path = repo_root / ".github" / "workflows" / "typed-boundaries.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    required_fragments = (
        "uv run pytest",
        "tests/test_food_task_lifecycle_contract.py",
        "tests/test_memo_task_lifecycle_contract.py",
        "tests/test_plugin_manifest_lifecycle_health.py",
        "tests/test_schedule_generator.py",
        "tests/test_plugin_remediation_ownership_lifecycle.py",
        "tests/test_admin_api_learning_pipeline.py",
        "tests/test_application_composition.py",
        "tests/test_plugin_bus.py",
    )
    missing = [fragment for fragment in required_fragments if fragment not in workflow]

    assert missing == []
