#!/usr/bin/env python3
"""Slang 别名碰撞自动合并 (A1.2)。

`slang_batch_merge_collisions.py` 是一次性合并 — 合并完一轮就停。但 merge
本身会把别名集合扩大，可能让原本不冲突的两条记录在下一轮变成新冲突
(尤其在 alias 链：A↔B↔C 这种)。本脚本：

1. 反复跑 ``slang_alias_collision_report._detect_collisions`` 直到稳定
   (或达到 ``--max-iterations`` 上限)；
2. 对 "两边都是 approved" 的 pair 默认跳过 — approved 互合并属人审范畴
   ('--include-approved-pairs' 显式打开)；
3. 全程不修改数据库，输出"将要做什么"的结构化报告 (--dry-run 默认)；
   ``--apply`` 才真正调用 store.merge_terms()；
4. 每一轮把 merge 决策、跳过原因和遗留冲突写到 ``--log`` (JSON)，便于
   下次差异比对。

设计原则
--------
- **read-mostly 默认**：未带 ``--apply`` 时不会写库
- **可重入**：跑两次的最终结果相同 (idempotent on stable state)
- **可解释**：每个 skipped/merged 都附 reason
- **不替代 admin UI**：跨 status 边界 (例如 candidate ↔ approved) 默认依然
  套用 ``_suggest_merge_target`` 的规则；想要更细的策略改用 admin 合并 UI

Usage
-----
``uv run python scripts/dev/slang_collision_auto_merge.py``               # dry-run 报告
``uv run python scripts/dev/slang_collision_auto_merge.py --apply``       # 真正合并
``uv run python scripts/dev/slang_collision_auto_merge.py --apply --include-approved-pairs``
``uv run python scripts/dev/slang_collision_auto_merge.py --log out.json --apply``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.dev._bot_guard import assert_bot_stopped  # noqa: E402
from scripts.dev.slang_alias_collision_report import (  # noqa: E402
    TermView,
    _detect_collisions,
    _load_terms,
    _suggest_merge_target,
)
from services.slang.errors import SlangCollisionError  # noqa: E402
from services.slang.store import SlangStore  # noqa: E402

DEFAULT_DB = ROOT / "storage" / "slang.db"
DEFAULT_MAX_ITERATIONS = 6


@dataclass
class IterationStats:
    iteration: int
    candidate_pairs: int = 0
    merged: int = 0
    skipped_approved_pair: int = 0
    skipped_collision: int = 0
    skipped_missing: int = 0
    errors: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)
    leftovers: list[dict[str, Any]] = field(default_factory=list)


def _pair_summary(a: TermView, b: TermView, shared: set[str], target_id: str) -> dict[str, Any]:
    source_id = b.term_id if target_id == a.term_id else a.term_id
    return {
        "scope": a.scope,
        "group_id": a.group_id if a.scope == "group" else "",
        "shared": sorted(shared),
        "target": {
            "term_id": target_id,
            "term": a.term if target_id == a.term_id else b.term,
            "status": a.status if target_id == a.term_id else b.status,
        },
        "source": {
            "term_id": source_id,
            "term": b.term if target_id == a.term_id else a.term,
            "status": b.status if target_id == a.term_id else a.status,
        },
    }


def _filter_pairs(
    collisions: Iterable[tuple[TermView, TermView, set[str]]],
    *,
    status_filter: str,
    scope_filter: str,
    group_filter: str,
) -> list[tuple[TermView, TermView, set[str]]]:
    out: list[tuple[TermView, TermView, set[str]]] = []
    for a, b, shared in collisions:
        if status_filter and a.status != status_filter and b.status != status_filter:
            continue
        if scope_filter and a.scope != scope_filter:
            continue
        if group_filter and a.scope == "group" and group_filter not in (a.group_id, b.group_id):
            continue
        out.append((a, b, shared))
    return out


async def _run_one_iteration(
    *,
    store: SlangStore | None,
    db_path: Path,
    iteration: int,
    apply_changes: bool,
    include_approved_pairs: bool,
    status_filter: str,
    scope_filter: str,
    group_filter: str,
) -> IterationStats:
    terms = _load_terms(db_path)
    collisions = _detect_collisions(terms)
    collisions = _filter_pairs(
        collisions,
        status_filter=status_filter,
        scope_filter=scope_filter,
        group_filter=group_filter,
    )

    stats = IterationStats(iteration=iteration, candidate_pairs=len(collisions))
    if not collisions:
        return stats

    for a, b, shared in collisions:
        target_id = _suggest_merge_target(a, b)
        source_id = b.term_id if target_id == a.term_id else a.term_id
        summary = _pair_summary(a, b, shared, target_id)

        # Approved-vs-approved guard.
        if a.status == "approved" and b.status == "approved" and not include_approved_pairs:
            summary["action"] = "skipped"
            summary["reason"] = "approved↔approved pair (use --include-approved-pairs to merge)"
            stats.skipped_approved_pair += 1
            stats.leftovers.append(summary)
            continue

        if not apply_changes or store is None:
            summary["action"] = "would_merge"
            stats.actions.append(summary)
            stats.merged += 1
            continue

        try:
            result = await store.merge_terms(target_id=target_id, source_ids=[source_id])
        except SlangCollisionError as e:
            summary["action"] = "skipped"
            summary["reason"] = f"collision after merge: {e}"
            stats.skipped_collision += 1
            stats.leftovers.append(summary)
            continue
        except Exception as e:
            summary["action"] = "error"
            summary["reason"] = f"{type(e).__name__}: {e}"
            stats.errors += 1
            stats.leftovers.append(summary)
            continue

        if result is None:
            summary["action"] = "skipped"
            summary["reason"] = "target term missing (already merged?)"
            stats.skipped_missing += 1
            stats.leftovers.append(summary)
            continue

        summary["action"] = "merged"
        stats.actions.append(summary)
        stats.merged += 1

    return stats


async def run(
    *,
    db_path: Path,
    apply_changes: bool,
    include_approved_pairs: bool,
    max_iterations: int,
    status_filter: str,
    scope_filter: str,
    group_filter: str,
    log_path: Path | None,
) -> int:
    store: SlangStore | None = None
    if apply_changes:
        store = SlangStore(db_path=db_path)
        await store.init()

    iterations: list[IterationStats] = []
    try:
        for i in range(1, max_iterations + 1):
            stats = await _run_one_iteration(
                store=store,
                db_path=db_path,
                iteration=i,
                apply_changes=apply_changes,
                include_approved_pairs=include_approved_pairs,
                status_filter=status_filter,
                scope_filter=scope_filter,
                group_filter=group_filter,
            )
            iterations.append(stats)
            print(
                f"[iter {i}] pairs={stats.candidate_pairs} "
                f"{'would_merge' if not apply_changes else 'merged'}={stats.merged} "
                f"approved_skip={stats.skipped_approved_pair} "
                f"collision_skip={stats.skipped_collision} "
                f"missing={stats.skipped_missing} errors={stats.errors}"
            )
            # Stable state: nothing got merged this round → done.
            if stats.merged == 0:
                break
            # Dry-run mode does not actually merge, so we'd loop forever.
            if not apply_changes:
                break
    finally:
        if store is not None:
            await store.close()

    total_merged = sum(s.merged for s in iterations) if apply_changes else iterations[0].merged if iterations else 0
    final_leftover = iterations[-1].leftovers if iterations else []
    print(
        f"\n[summary] iterations={len(iterations)} "
        f"{'merged' if apply_changes else 'would_merge'}={total_merged} "
        f"final_unresolved={len(final_leftover)}"
    )

    if log_path is not None:
        payload = {
            "db_path": str(db_path),
            "apply": apply_changes,
            "include_approved_pairs": include_approved_pairs,
            "iterations": [
                {
                    "iteration": s.iteration,
                    "candidate_pairs": s.candidate_pairs,
                    "merged": s.merged,
                    "skipped_approved_pair": s.skipped_approved_pair,
                    "skipped_collision": s.skipped_collision,
                    "skipped_missing": s.skipped_missing,
                    "errors": s.errors,
                    "actions": s.actions,
                    "leftovers": s.leftovers,
                }
                for s in iterations
            ],
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[log] {log_path}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"slang.db path (default: {DEFAULT_DB})")
    parser.add_argument("--apply", action="store_true", help="实际写库；缺省为 dry-run")
    parser.add_argument(
        "--include-approved-pairs",
        action="store_true",
        help="允许合并 approved↔approved 对 (默认跳过留人工审核)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"最大迭代轮数 (default: {DEFAULT_MAX_ITERATIONS})",
    )
    parser.add_argument("--status", default="", help="只处理至少一边 status=该值的 pair")
    parser.add_argument("--scope", default="", help="只处理 scope=group / global")
    parser.add_argument("--group-id", default="", help="只处理涉及该 group_id 的 pair")
    parser.add_argument("--log", type=Path, default=None, help="将每轮决策写到该 JSON 文件")
    parser.add_argument(
        "--force",
        action="store_true",
        help="bypass the live-bot guard (dangerous; can corrupt SQLite)",
    )
    args = parser.parse_args(argv)

    if args.scope and args.scope not in {"group", "global"}:
        print("--scope 必须是 'group' 或 'global'", file=sys.stderr)
        return 2
    if args.max_iterations < 1:
        print("--max-iterations 必须 >= 1", file=sys.stderr)
        return 2

    if args.apply:
        assert_bot_stopped(action="auto-merge slang collisions", force=args.force)

    return asyncio.run(run(
        db_path=args.db,
        apply_changes=args.apply,
        include_approved_pairs=args.include_approved_pairs,
        max_iterations=args.max_iterations,
        status_filter=args.status,
        scope_filter=args.scope,
        group_filter=args.group_id,
        log_path=args.log,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
