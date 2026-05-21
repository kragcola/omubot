"""Tests for scripts/dev/slang_alias_collision_report.py.

The `--status` flag must filter pairs where AT LEAST ONE term matches the
given status (post-pairwise filter), not pre-SQL-filter that drops the
counterpart and hides approved↔candidate / approved↔muted collisions.

This is the regression contract for `--status` semantics. If anyone reverts
to a pre-pairwise WHERE filter, this test fails.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev" / "slang_alias_collision_report.py"


def _seed_db(db_path: Path) -> None:
    """Create a minimal slang.db with three terms forming two collisions:

    A (approved) shares "摸鱼" with B (candidate)
    A (approved) shares "划水" with C (muted)
    B (candidate) and C (muted) do NOT share any keys

    Expected collision pairs:
      - (A, B): both share "摸鱼"
      - (A, C): both share "划水"
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE slang_terms (
            term_id TEXT PRIMARY KEY, term TEXT, term_key TEXT,
            aliases_json TEXT, status TEXT, scope TEXT, group_id TEXT,
            confidence REAL, usage_count INTEGER
        )"""
    )
    rows = [
        ("term_a", "摸鱼", "摸鱼", json.dumps(["划水"]), "approved", "group", "100", 0.9, 5),
        ("term_b", "摸鱼鱼", "摸鱼鱼", json.dumps(["摸鱼"]), "candidate", "group", "100", 0.5, 1),
        ("term_c", "划水王", "划水王", json.dumps(["划水"]), "muted", "group", "100", 0.3, 0),
    ]
    conn.executemany(
        "INSERT INTO slang_terms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _run_report(db_path: Path, *extra_args: str) -> dict:
    """Run the script with --json and return parsed payload."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db_path), "--json", *extra_args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_no_status_filter_finds_all_pairs(tmp_path: Path) -> None:
    db = tmp_path / "slang.db"
    _seed_db(db)
    pairs = _run_report(db)
    pair_keys = {tuple(sorted([p["term_a"]["term_id"], p["term_b"]["term_id"]])) for p in pairs}
    assert pair_keys == {("term_a", "term_b"), ("term_a", "term_c")}


def test_status_filter_keeps_pairs_where_at_least_one_side_matches(tmp_path: Path) -> None:
    """--status approved must keep both (A,B) and (A,C) since A is approved
    in both pairs. Pre-pairwise filtering would have dropped B and C entirely
    and missed approved↔candidate / approved↔muted collisions."""
    db = tmp_path / "slang.db"
    _seed_db(db)
    pairs = _run_report(db, "--status", "approved")
    pair_keys = {tuple(sorted([p["term_a"]["term_id"], p["term_b"]["term_id"]])) for p in pairs}
    assert pair_keys == {("term_a", "term_b"), ("term_a", "term_c")}


def test_status_filter_excludes_pairs_with_no_matching_side(tmp_path: Path) -> None:
    """--status candidate keeps only pairs where at least one side is candidate.
    Only (A, B) qualifies; (A, C) is approved↔muted and must be excluded."""
    db = tmp_path / "slang.db"
    _seed_db(db)
    pairs = _run_report(db, "--status", "candidate")
    pair_keys = {tuple(sorted([p["term_a"]["term_id"], p["term_b"]["term_id"]])) for p in pairs}
    assert pair_keys == {("term_a", "term_b")}


def test_status_filter_excludes_unrelated_status(tmp_path: Path) -> None:
    """--status expired matches no term; result must be empty."""
    db = tmp_path / "slang.db"
    _seed_db(db)
    pairs = _run_report(db, "--status", "expired")
    assert pairs == []


@pytest.mark.parametrize("filter_status", ["approved", "candidate", "muted"])
def test_status_filter_never_loses_pairs_a_no_status_run_finds(
    tmp_path: Path, filter_status: str
) -> None:
    """The post-pairwise filter must be a strict subset of the no-filter run.

    Pre-pairwise WHERE filtering could ADD pairs (e.g. include a candidate↔muted
    pair that disappears once we recompute key overlaps over the filtered subset),
    but the post-pairwise contract is: filtering only DROPS pairs.
    """
    db = tmp_path / "slang.db"
    _seed_db(db)
    all_pairs = {
        tuple(sorted([p["term_a"]["term_id"], p["term_b"]["term_id"]]))
        for p in _run_report(db)
    }
    filtered_pairs = {
        tuple(sorted([p["term_a"]["term_id"], p["term_b"]["term_id"]]))
        for p in _run_report(db, "--status", filter_status)
    }
    assert filtered_pairs <= all_pairs
