#!/usr/bin/env python3
"""Compatibility CLI for the shared repository plugin inventory gate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check_plugin_manifests import plugin_inventory_violations  # noqa: E402


def _inventory_items(plugin_root: Path) -> list[Path]:
    return [path for _violation, path in plugin_inventory_violations(plugin_root)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Omubot plugin directory layout")
    parser.add_argument("--plugin-root", default="plugins", help="Plugin root directory")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on legacy files")
    args = parser.parse_args()

    plugin_root = Path(args.plugin_root)
    violations = _inventory_items(plugin_root)
    if not violations:
        print(f"[plugin-layout] ok: {plugin_root} contains no inventory violations")
        return 0

    print(f"[plugin-layout] inventory violations detected under {plugin_root}:")
    for item in violations:
        print(f" - {item}")
    print(
        "[plugin-layout] migrate to plugins/<name>/plugin.py + plugin.json + "
        "config.default.json + config.schema.json",
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
