#!/usr/bin/env python3
"""Batch-merge alias collisions detected by slang_alias_collision_report.py.

Reads the collision report JSON, then for each pair calls store.merge_terms()
with the suggested target. Collisions that would create new conflicts are
skipped gracefully (SlangCollisionError).

Usage:
    uv run python scripts/dev/slang_batch_merge_collisions.py
    uv run python scripts/dev/slang_batch_merge_collisions.py --dry-run
    uv run python scripts/dev/slang_batch_merge_collisions.py --status approved
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.dev._bot_guard import assert_bot_stopped  # noqa: E402
from services.slang.errors import SlangCollisionError  # noqa: E402
from services.slang.store import SlangStore  # noqa: E402


async def run(*, db_path: Path, dry_run: bool, status_filter: str) -> None:
    from scripts.dev.slang_alias_collision_report import (
        _detect_collisions,
        _load_terms,
        _suggest_merge_target,
    )

    terms = _load_terms(db_path)
    collisions = _detect_collisions(terms)
    if status_filter:
        collisions = [
            (a, b, shared) for a, b, shared in collisions
            if a.status == status_filter or b.status == status_filter
        ]

    print(f"Collision pairs to process: {len(collisions)}")
    if not collisions:
        return

    store = SlangStore(db_path=db_path)
    await store.init()

    merged = 0
    skipped_collision = 0
    skipped_missing = 0
    errors = 0

    for a, b, shared in collisions:
        target_id = _suggest_merge_target(a, b)
        source_id = b.term_id if target_id == a.term_id else a.term_id

        if dry_run:
            print(f"  [dry-run] merge {source_id} -> {target_id} (shared: {shared})")
            merged += 1
            continue

        try:
            result = await store.merge_terms(target_id=target_id, source_ids=[source_id])
            if result is None:
                skipped_missing += 1
            else:
                merged += 1
        except SlangCollisionError as e:
            skipped_collision += 1
            print(f"  [skip] {source_id} -> {target_id}: {e}")
        except Exception as e:
            errors += 1
            print(f"  [error] {source_id} -> {target_id}: {e}")

    print(f"\nResults: merged={merged} skipped_collision={skipped_collision} "
          f"skipped_missing={skipped_missing} errors={errors}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--db", type=Path,
                        default=ROOT / "storage" / "slang.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="print merge plan without executing")
    parser.add_argument("--status", default="",
                        help="only merge pairs where at least one term has this status")
    parser.add_argument("--force", action="store_true",
                        help="bypass the live-bot guard (dangerous; can corrupt SQLite)")
    args = parser.parse_args()
    if not args.dry_run:
        assert_bot_stopped(action="batch-merge slang collisions", force=args.force)
    asyncio.run(run(db_path=args.db, dry_run=args.dry_run, status_filter=args.status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
