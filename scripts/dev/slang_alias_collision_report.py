#!/usr/bin/env python3
"""Report alias collisions in storage/slang.db.

A collision is two distinct slang_terms in the same scope (and same group, for
group-scoped) whose normalized term/alias key sets overlap.  These rows would
have been deduplicated had `find_existing()` looked up the candidate's aliases
at insert time.

Usage:
    uv run python scripts/dev/slang_alias_collision_report.py
    uv run python scripts/dev/slang_alias_collision_report.py --db storage/slang.db
    uv run python scripts/dev/slang_alias_collision_report.py --json > collisions.json
    uv run python scripts/dev/slang_alias_collision_report.py --status approved

--status filters to pairs where AT LEAST ONE term has the given status.

The report is read-only.  It emits a suggested merge plan but never writes;
use `/admin/slang` merge UI or admin API to apply it after review.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "storage" / "slang.db"


def _normalize(value: str) -> str:
    """Match the lightweight normalize used by services.slang.store."""
    if not value:
        return ""
    out = []
    for ch in str(value).strip():
        if ch.isspace():
            continue
        out.append(ch.lower())
    return "".join(out)


@dataclass
class TermView:
    term_id: str
    term: str
    aliases: list[str]
    status: str
    scope: str
    group_id: str
    confidence: float
    usage_count: int
    keys: set[str] = field(default_factory=set)


def _load_terms(db_path: Path) -> list[TermView]:
    if not db_path.exists():
        raise SystemExit(f"slang.db not found at {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT term_id, term, term_key, aliases_json, status, scope,
                  group_id, confidence, usage_count
           FROM slang_terms""",
    ).fetchall()
    conn.close()

    terms: list[TermView] = []
    for row in rows:
        try:
            aliases = json.loads(row["aliases_json"] or "[]")
        except json.JSONDecodeError:
            aliases = []
        keys = {_normalize(row["term"]), *(_normalize(a) for a in aliases)}
        keys.discard("")
        if not keys:
            continue
        terms.append(
            TermView(
                term_id=row["term_id"],
                term=row["term"],
                aliases=list(aliases),
                status=row["status"],
                scope=row["scope"],
                group_id=row["group_id"] or "",
                confidence=float(row["confidence"] or 0.0),
                usage_count=int(row["usage_count"] or 0),
                keys=keys,
            )
        )
    return terms


def _detect_collisions(terms: list[TermView]) -> list[tuple[TermView, TermView, set[str]]]:
    """Bucket terms by (scope, group) then pairwise compare key sets."""
    bucket: dict[tuple[str, str], list[TermView]] = defaultdict(list)
    for term in terms:
        bucket_key = (term.scope, "" if term.scope == "global" else term.group_id)
        bucket[bucket_key].append(term)

    collisions: list[tuple[TermView, TermView, set[str]]] = []
    for items in bucket.values():
        # Index keys → terms inside this bucket so we don't run O(n^2) for
        # pathological groups; only pairs that share at least one key are
        # considered.
        index: dict[str, list[int]] = defaultdict(list)
        for idx, term in enumerate(items):
            for key in term.keys:
                index[key].append(idx)
        seen_pairs: set[tuple[str, str]] = set()
        for key_terms in index.values():
            if len(key_terms) < 2:
                continue
            for a, b in combinations(sorted(set(key_terms)), 2):
                term_a, term_b = items[a], items[b]
                pair_key = tuple(sorted([term_a.term_id, term_b.term_id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                shared = term_a.keys & term_b.keys
                if shared:
                    collisions.append((term_a, term_b, shared))
    return collisions


def _suggest_merge_target(a: TermView, b: TermView) -> str:
    """Heuristic: prefer approved > candidate > muted, then higher confidence,
    then higher usage_count, then term_id stable tiebreak."""
    rank = {"approved": 3, "candidate": 2, "expired": 1, "muted": 0}
    score_a = (rank.get(a.status, 0), a.confidence, a.usage_count, -ord(a.term_id[-1]))
    score_b = (rank.get(b.status, 0), b.confidence, b.usage_count, -ord(b.term_id[-1]))
    return a.term_id if score_a >= score_b else b.term_id


def _format_text(collisions: list[tuple[TermView, TermView, set[str]]]) -> str:
    if not collisions:
        return "no alias collisions detected"
    by_scope: dict[tuple[str, str], list[tuple[TermView, TermView, set[str]]]] = defaultdict(list)
    for a, b, shared in collisions:
        scope_key = (a.scope, "" if a.scope == "global" else a.group_id)
        by_scope[scope_key].append((a, b, shared))

    lines: list[str] = []
    summary_total = len(collisions)
    summary_approved = sum(
        1 for a, b, _ in collisions if "approved" in {a.status, b.status}
    )
    lines.append(
        f"slang alias collisions: {summary_total} pairs"
        f" ({summary_approved} involve approved)"
    )
    lines.append("")
    for (scope, group_id), pairs in sorted(by_scope.items()):
        header = f"## scope={scope}" + (f" group={group_id}" if scope == "group" else "")
        lines.append(f"{header}  pairs={len(pairs)}")
        for a, b, shared in pairs:
            target = _suggest_merge_target(a, b)
            other = b.term_id if target == a.term_id else a.term_id
            shared_disp = ",".join(sorted(shared))
            lines.append(
                f"  - shared=[{shared_disp}]"
            )
            lines.append(
                f"      A: {a.term_id} term={a.term!r} status={a.status}"
                f" conf={a.confidence:.2f} usage={a.usage_count}"
                f" aliases={a.aliases}"
            )
            lines.append(
                f"      B: {b.term_id} term={b.term!r} status={b.status}"
                f" conf={b.confidence:.2f} usage={b.usage_count}"
                f" aliases={b.aliases}"
            )
            lines.append(f"      suggest merge: {other} -> {target}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_json(collisions: list[tuple[TermView, TermView, set[str]]]) -> str:
    payload = []
    for a, b, shared in collisions:
        target = _suggest_merge_target(a, b)
        payload.append(
            {
                "scope": a.scope,
                "group_id": a.group_id if a.scope == "group" else "",
                "shared_keys": sorted(shared),
                "term_a": {
                    "term_id": a.term_id, "term": a.term, "aliases": a.aliases,
                    "status": a.status, "confidence": a.confidence,
                    "usage_count": a.usage_count,
                },
                "term_b": {
                    "term_id": b.term_id, "term": b.term, "aliases": b.aliases,
                    "status": b.status, "confidence": b.confidence,
                    "usage_count": b.usage_count,
                },
                "suggested_target": target,
                "suggested_source": b.term_id if target == a.term_id else a.term_id,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"path to slang.db (default: {DEFAULT_DB})")
    parser.add_argument("--status", default="",
                        help="filter: show pairs where at least one term has this status")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of human report")
    args = parser.parse_args()

    terms = _load_terms(args.db)
    collisions = _detect_collisions(terms)
    if args.status:
        collisions = [
            (a, b, shared) for a, b, shared in collisions
            if a.status == args.status or b.status == args.status
        ]
    output = _format_json(collisions) if args.json else _format_text(collisions)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
