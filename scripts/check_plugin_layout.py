#!/usr/bin/env python3
"""Fail fast when legacy root-level plugin files are present."""

from __future__ import annotations

import argparse
from pathlib import Path


def _legacy_items(plugin_root: Path) -> list[Path]:
    legacy: list[Path] = []
    if not plugin_root.is_dir():
        return legacy

    for item in sorted(plugin_root.iterdir()):
        if item.is_file() and item.suffix == ".py" and not item.name.startswith("__"):
            legacy.append(item)
        if item.is_file() and item.suffix in {".toml", ".json"}:
            stem = item.stem
            if (plugin_root / stem / "plugin.py").is_file() or (plugin_root / stem / "plugin.json").is_file():
                continue
            if item.name in {"__init__.py", "__init__.json"}:
                continue
            legacy.append(item)
    return legacy


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Omubot plugin directory layout")
    parser.add_argument("--plugin-root", default="plugins", help="Plugin root directory")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on legacy files")
    args = parser.parse_args()

    plugin_root = Path(args.plugin_root)
    legacy = _legacy_items(plugin_root)
    if not legacy:
        print(f"[plugin-layout] ok: {plugin_root} contains no legacy root-level plugin files")
        return 0

    print(f"[plugin-layout] legacy layout detected under {plugin_root}:")
    for item in legacy:
        print(f" - {item}")
    print(
        "[plugin-layout] migrate to plugins/<name>/plugin.py + plugin.json + "
        "config.default.json + config.schema.json",
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
