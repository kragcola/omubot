"""CLI entry point for Persona Source Importer Part A."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compiler import compile_persona_dry_run
from .writer import PersonaDraftWriter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import persona source.md into v2 draft files.")
    parser.add_argument("persona_id", help="Persona id or namespace, e.g. fengxiaomeng or fengxiaomeng-v2")
    parser.add_argument("--root", default="config/persona", help="Persona root directory")
    parser.add_argument("--defaults", default="config/persona/_defaults/v2", help="Defaults template directory")
    parser.add_argument("--source", default="", help="Explicit source.md path")
    parser.add_argument("--strict", action="store_true", help="Do not write draft when validation has errors")
    parser.add_argument("--no-write", action="store_true", help="Build and print report without writing .draft")
    parser.add_argument("--pending-freeze", action="store_true", help="Copy .draft to _pending_freeze after import")
    parser.add_argument(
        "--compile-dry-run",
        action="store_true",
        help="Compile .draft into prompt blocks without runtime cutover",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    writer = PersonaDraftWriter(persona_root=args.root, defaults_dir=args.defaults)
    result = writer.import_source(
        args.persona_id,
        source_path=Path(args.source) if args.source else None,
        strict=args.strict,
        write=not args.no_write,
    )
    payload = {
        "ok": not result.report.has_errors,
        "persona_id": result.persona_id,
        "report": result.report.to_dict(),
    }
    if args.pending_freeze and not args.no_write:
        payload["pending_freeze"] = writer.pending_freeze(result.persona_id)
    if args.compile_dry_run and not args.no_write:
        payload["compile"] = compile_persona_dry_run(
            result.persona_id,
            persona_root=args.root,
            defaults_dir=args.defaults,
        ).to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if result.report.has_errors and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
