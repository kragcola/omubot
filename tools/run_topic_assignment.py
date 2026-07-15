#!/usr/bin/env python3
"""Run the offline Phase 2 topic-assignment projection."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.group.topic_assignment_runner import TopicAssignmentRunner  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Project raw research events into versioned topic assignments.",
    )
    parser.add_argument(
        "--raw-db",
        default=str(ROOT / "storage" / "research_events.db"),
        help="Phase 1 raw research database (opened read-only).",
    )
    parser.add_argument(
        "--derived-db",
        default=str(ROOT / "storage" / "research_topic_assignments.db"),
        help="Independent Phase 2 derived database.",
    )
    parser.add_argument(
        "--cutoff",
        default="",
        help="Optional inclusive ingested_at ISO-8601 cutoff.",
    )
    parser.add_argument(
        "--utterance-gap-seconds",
        type=float,
        default=2.5,
        help="Gap for contiguous same-actor logical utterances.",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    result = await TopicAssignmentRunner(
        raw_db_path=args.raw_db,
        derived_db_path=args.derived_db,
        utterance_gap_seconds=args.utterance_gap_seconds,
        input_cutoff=args.cutoff,
    ).run()
    return asdict(result)


def main() -> int:
    args = _parser().parse_args()
    payload = asyncio.run(_run(args))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
