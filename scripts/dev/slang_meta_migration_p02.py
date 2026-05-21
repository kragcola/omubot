#!/usr/bin/env python3
"""Phase 13 P0-2: One-time migration to backfill ai_reviewed_at / ai_review_source / ai_review_decision.

Usage:
    # Dry-run on backup (recommended first)
    uv run python scripts/dev/slang_meta_migration_p02.py --db /tmp/slang_backup.db --dry-run

    # Production run
    uv run python scripts/dev/slang_meta_migration_p02.py --db storage/slang.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.dev._bot_guard import assert_bot_stopped  # noqa: E402

MIGRATION_1_BACKLOG = """\
UPDATE slang_terms
SET meta_json = json_set(
    meta_json,
    '$.ai_reviewed_at',
        COALESCE(json_extract(meta_json, '$.backlog_review.reviewed_at'), updated_at, created_at),
    '$.ai_review_source', 'backlog',
    '$.ai_review_decision',
        CASE
            WHEN json_extract(meta_json, '$.backlog_review.approved') = 1 THEN 'approved'
            WHEN json_extract(meta_json, '$.backlog_review.approved') = 0
              AND status = 'muted'
              AND EXISTS (
                SELECT 1 FROM slang_term_revisions r
                WHERE r.term_id = slang_terms.term_id AND r.action = 'backlog_review:mute'
              ) THEN 'rejected'
            ELSE 'kept'
        END
)
WHERE meta_json LIKE '%"backlog_review":%'
  AND meta_json NOT LIKE '%"ai_reviewed_at": "%'
  AND meta_json NOT LIKE '%"ai_reviewed_at":"%'
"""

MIGRATION_2_DAILY = """\
UPDATE slang_terms
SET meta_json = json_set(
    meta_json,
    '$.ai_review_source', 'daily',
    '$.ai_review_decision', 'approved'
)
WHERE (meta_json LIKE '%"ai_approved": true%' OR meta_json LIKE '%"ai_approved":true%')
  AND meta_json NOT LIKE '%"ai_review_source": "%'
  AND meta_json NOT LIKE '%"ai_review_source":"%'
"""

MIGRATION_3_HUMAN_REVIEWED = """\
UPDATE slang_terms
SET meta_json = json_set(meta_json, '$.human_reviewed', json('true'))
WHERE status = 'approved'
  AND meta_json NOT LIKE '%"human_reviewed": true%'
  AND meta_json NOT LIKE '%"human_reviewed":true%'
  AND source != 'ai_auto_review'
  AND meta_json NOT LIKE '%"ai_review_decision": "approved"%'
  AND meta_json NOT LIKE '%"ai_review_decision":"approved"%'
  AND meta_json NOT LIKE '%"ai_approved": true%'
  AND meta_json NOT LIKE '%"ai_approved":true%'
"""

COUNT_BACKLOG = """\
SELECT
    SUM(CASE
        WHEN json_extract(meta_json, '$.backlog_review.approved') = 1 THEN 1
        ELSE 0
    END) AS backlog_approved,
    SUM(CASE
        WHEN json_extract(meta_json, '$.backlog_review.approved') = 0
          AND status = 'muted'
          AND EXISTS (
            SELECT 1 FROM slang_term_revisions r
            WHERE r.term_id = slang_terms.term_id AND r.action = 'backlog_review:mute'
          ) THEN 1
        ELSE 0
    END) AS backlog_rejected,
    SUM(CASE
        WHEN json_extract(meta_json, '$.backlog_review.approved') = 0
          AND NOT (status = 'muted'
            AND EXISTS (
              SELECT 1 FROM slang_term_revisions r
              WHERE r.term_id = slang_terms.term_id AND r.action = 'backlog_review:mute'
            )) THEN 1
        WHEN json_extract(meta_json, '$.backlog_review.approved') = 1
          AND 0 THEN 1
        ELSE 0
    END) AS backlog_kept
FROM slang_terms
WHERE meta_json LIKE '%"backlog_review":%'
  AND meta_json NOT LIKE '%"ai_reviewed_at": "%'
  AND meta_json NOT LIKE '%"ai_reviewed_at":"%'
"""

COUNT_DAILY = """\
SELECT COUNT(*) AS daily_approved
FROM slang_terms
WHERE (meta_json LIKE '%"ai_approved": true%' OR meta_json LIKE '%"ai_approved":true%')
  AND meta_json NOT LIKE '%"ai_review_source": "%'
  AND meta_json NOT LIKE '%"ai_review_source":"%'
"""

COUNT_HUMAN = """\
SELECT COUNT(*) AS human_reviewed
FROM slang_terms
WHERE status = 'approved'
  AND meta_json NOT LIKE '%"human_reviewed": true%'
  AND meta_json NOT LIKE '%"human_reviewed":true%'
  AND source != 'ai_auto_review'
  AND meta_json NOT LIKE '%"ai_review_decision": "approved"%'
  AND meta_json NOT LIKE '%"ai_review_decision":"approved"%'
  AND meta_json NOT LIKE '%"ai_approved": true%'
  AND meta_json NOT LIKE '%"ai_approved":true%'
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill ai_reviewed_at / ai_review_source / ai_review_decision")
    parser.add_argument("--db", required=True, help="Path to slang.db")
    parser.add_argument("--dry-run", action="store_true", help="Count affected rows without modifying")
    parser.add_argument("--force", action="store_true",
                        help="bypass the live-bot guard (dangerous; can corrupt SQLite)")
    args = parser.parse_args()

    if not args.dry_run:
        assert_bot_stopped(action="run slang meta migration P02", force=args.force)

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")

    if args.dry_run:
        print("=== DRY RUN ===\n")

        row = conn.execute(COUNT_BACKLOG).fetchone()
        print("Migration 1 (backlog):")
        print(f"  approved:  {row[0] or 0}")
        print(f"  rejected:  {row[1] or 0}")
        print(f"  kept:      {row[2] or 0}")
        print(f"  total:     {(row[0] or 0) + (row[1] or 0) + (row[2] or 0)}")

        row = conn.execute(COUNT_DAILY).fetchone()
        print("\nMigration 2 (daily):")
        print(f"  approved:  {row[0] or 0}")

        row = conn.execute(COUNT_HUMAN).fetchone()
        print("\nMigration 3 (human_reviewed):")
        print(f"  to mark:   {row[0] or 0}")

        print("\nNo changes made.")
    else:
        print("=== PRODUCTION RUN ===\n")

        cur = conn.execute(MIGRATION_1_BACKLOG)
        print(f"Migration 1 (backlog): {cur.rowcount} rows updated")

        cur = conn.execute(MIGRATION_2_DAILY)
        print(f"Migration 2 (daily):   {cur.rowcount} rows updated")

        cur = conn.execute(MIGRATION_3_HUMAN_REVIEWED)
        print(f"Migration 3 (human):   {cur.rowcount} rows updated")

        conn.commit()
        print("\nAll migrations committed.")

    conn.close()


if __name__ == "__main__":
    main()
