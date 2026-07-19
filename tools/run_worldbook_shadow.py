#!/usr/bin/env python3
"""CLI: offline Worldbook Stage-1 7-day shadow evaluator.

Never registers PromptProvider, never touches production storage, Docker,
NapCat, or QZone. Exit codes:

  0 — overall_verdict == pass
  1 — invariant failure / overall_verdict == fail
  2 — usage / IO / unexpected error
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.worldbook.shadow import run_worldbook_shadow_sync  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the offline Worldbook Living Story 7-day shadow against "
            "caller-supplied fixture/content/output roots."
        )
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=_REPO_ROOT / "tests" / "fixtures" / "worldbook" / "living_story_v1",
        help="Fixture directory (arcs, life_state, social, scenario, expected)",
    )
    parser.add_argument(
        "--content-root",
        type=Path,
        default=_REPO_ROOT / "config" / "worldbook",
        help="Content pack root containing canon/ and storylets/",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Caller-supplied output directory for shadow_report.json",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="Optional explicit work root (default: temp under output-root)",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep the temporary work root after the run",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent when printing summary to stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        report = run_worldbook_shadow_sync(
            fixture_root=args.fixture_root,
            content_root=args.content_root,
            output_root=args.output_root,
            work_root=args.work_root,
            keep_work=bool(args.keep_work),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"shadow_failed:{type(exc).__name__}:{exc}",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2

    summary = {
        "ok": report.ok,
        "overall_verdict": report.overall_verdict,
        "registry": {
            "canon_count": report.registry.get("canon_count"),
            "storylet_count": report.registry.get("storylet_count"),
        },
        "selected_storylet_ids": report.storylet_observations.get(
            "selected_storylet_ids"
        ),
        "setback_recovery": report.setback_recovery,
        "partner_state": {
            "distinct_entity_count": report.partner_state.get("distinct_entity_count"),
            "change_count": report.partner_state.get("change_count"),
            "entity_ids": report.partner_state.get("entity_ids"),
        },
        "life_state": {
            "item_count": report.life_state.get("item_count"),
            "story_ledger_item_count": report.life_state.get("story_ledger_item_count"),
            "story_ledger_keys": report.life_state.get("story_ledger_keys"),
            "story_ledger_missing_ttl": report.life_state.get(
                "story_ledger_missing_ttl"
            ),
        },
        "continuity_final": {
            "final_main_stage": report.continuity.get("final_main_stage"),
            "final_setback_flag": report.continuity.get("final_setback_flag"),
        },
        "invariant_checks": (report.invariants or {}).get("checks"),
        "report_essential_hash": (report.metadata or {}).get("report_essential_hash"),
        "report_path": str(Path(args.output_root).resolve() / "shadow_report.json"),
    }
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=max(0, int(args.indent)),
            sort_keys=True,
        )
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
