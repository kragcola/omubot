import re

from kernel.manifest import PluginManifestV3


def test_plugin_manifest_schema_exposes_version_constraint_patterns() -> None:
    schema = PluginManifestV3.model_json_schema(by_alias=True)
    properties = schema.get("properties", {})

    version_pattern = properties.get("version", {}).get("pattern")
    assert isinstance(version_pattern, str) and version_pattern, (
        "properties.version.pattern must be a non-empty string; "
        f"got {version_pattern!r}"
    )
    assert re.fullmatch(version_pattern, "1.2.3") is not None
    assert re.fullmatch(version_pattern, "1.2") is None

    min_version_pattern = properties.get("min_omubot_version", {}).get("pattern")
    assert isinstance(min_version_pattern, str) and min_version_pattern, (
        "properties.min_omubot_version.pattern must be a non-empty string; "
        f"got {min_version_pattern!r}"
    )
    assert re.fullmatch(min_version_pattern, "") is not None
    assert re.fullmatch(min_version_pattern, "1.2.3") is not None
    assert re.fullmatch(min_version_pattern, "1.2") is None

    for field_name in (
        "required_dependencies",
        "optional_dependencies",
        "dependencies",
    ):
        additional_properties = properties.get(field_name, {}).get(
            "additionalProperties", {}
        )
        constraint_pattern = (
            additional_properties.get("pattern")
            if isinstance(additional_properties, dict)
            else None
        )
        assert isinstance(constraint_pattern, str) and constraint_pattern, (
            f"properties.{field_name}.additionalProperties.pattern must be a "
            f"non-empty string; got {constraint_pattern!r}"
        )
        assert re.fullmatch(constraint_pattern, ">=1.2.3") is not None
        assert re.fullmatch(constraint_pattern, "*") is not None
        assert re.fullmatch(constraint_pattern, ">=1.2") is None
