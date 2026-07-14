"""Public export contracts for the canonical plugin manifest API."""

from __future__ import annotations

import kernel
import kernel.manifest as manifest_module

_CANONICAL_MANIFEST_EXPORTS = (
    "PluginManifestV3",
    "PluginManifestError",
    "load_plugin_manifest",
    "parse_plugin_manifest_data",
    "resolve_manifest_config_paths",
)


def test_kernel_exports_canonical_manifest_api() -> None:
    export_problems: dict[str, str] = {}

    for name in _CANONICAL_MANIFEST_EXPORTS:
        actual = getattr(kernel, name, None)
        expected = getattr(manifest_module, name)
        if actual is None:
            export_problems[name] = "missing from top-level kernel"
        elif actual is not expected:
            export_problems[name] = "not the canonical kernel.manifest object"

    assert export_problems == {}


def test_kernel_legacy_plugin_manifest_aliases_v3() -> None:
    assert getattr(kernel, "PluginManifest", None) is manifest_module.PluginManifestV3
