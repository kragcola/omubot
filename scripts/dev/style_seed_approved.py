#!/usr/bin/env python3
"""Style 数据激活脚本 (A0.4)。

目的
----
Style 插件的 `collect_bot_replies` 默认开启，但 `global_enabled_group_ids` 默认
为空，且新装/新群的 `style_expressions` 表里没有 `approved` 行——所以即便链路
正确，prompt 也注入不出任何风格示例。

本脚本提供三步可幂等操作：

1. **enable** — 把 group_id 写入 `storage/plugins/config/style.json` 的
   `global_enabled_group_ids` 列表，并保证 `collect_bot_replies=true`。
2. **seed**   — 在 `storage/style.db` 中按 `(situation, style)` upsert 一组
   保守的种子表达，并直接置为 `approved`，让插件可以立刻注入。
3. **approve** — 把指定 group_id 下所有 `pending` 表达批量晋升到 `approved`，
   适用于"已经在悄悄收集，但缺一次手动审核"的群。

种子内容刻意保持中性——目的是验证链路而不是给 bot 强加风格。需要业务化
风格请走 admin UI 审核流程，不要无脑用本脚本批量铺。

Usage
-----
``uv run python scripts/dev/style_seed_approved.py status --group-id 123456789``
``uv run python scripts/dev/style_seed_approved.py enable --group-id 123456789``
``uv run python scripts/dev/style_seed_approved.py seed   --group-id 123456789``
``uv run python scripts/dev/style_seed_approved.py approve --group-id 123456789``
``uv run python scripts/dev/style_seed_approved.py all     --group-id 123456789``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.dev._bot_guard import assert_bot_stopped  # noqa: E402
from services.style.store import (  # noqa: E402
    NewStyleExpression,
    StyleStore,
)

CONFIG_OVERRIDE_PATH = ROOT / "storage" / "plugins" / "config" / "style.json"
DB_DEFAULT_PATH = ROOT / "storage" / "style.db"

SEED_EXPRESSIONS: tuple[dict[str, str], ...] = (
    {
        "situation": "用户提了一个开放性问题且语气轻松",
        "style": "先用一句话给出主线判断，再补一两点佐证，避免长段排比",
    },
    {
        "situation": "用户表达情绪而不是要答案",
        "style": "先承认对方的感受，再轻量回应，不要立刻给建议",
    },
    {
        "situation": "用户的提问里夹带明显的调侃或玩笑",
        "style": "用同等克制的玩笑回应，但保留一句正经收尾",
    },
    {
        "situation": "用户问的事实/数据型问题",
        "style": "直接给数字或定义，必要时附一行来源类型，不展开科普",
    },
)


def _load_override() -> dict:
    if CONFIG_OVERRIDE_PATH.is_file():
        try:
            text = CONFIG_OVERRIDE_PATH.read_text(encoding="utf-8")
        except OSError as e:
            raise SystemExit(f"failed to read {CONFIG_OVERRIDE_PATH}: {e}") from e
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{CONFIG_OVERRIDE_PATH} is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise SystemExit(f"{CONFIG_OVERRIDE_PATH} root must be an object")
        return data
    return {}


def _save_override(payload: dict) -> None:
    CONFIG_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    CONFIG_OVERRIDE_PATH.write_text(text, encoding="utf-8")


def cmd_enable(group_id: str) -> int:
    payload = _load_override()
    values = payload.get("values")
    if not isinstance(values, dict):
        values = {}
    groups = values.get("global_enabled_group_ids", [])
    if not isinstance(groups, list):
        groups = []
    cleaned = [str(g).strip() for g in groups if str(g).strip()]
    changed = False
    if group_id not in cleaned:
        cleaned.append(group_id)
        changed = True
    if values.get("collect_bot_replies") is not True:
        values["collect_bot_replies"] = True
        changed = True
    values["global_enabled_group_ids"] = cleaned
    payload["plugin"] = "style"
    payload["schema_version"] = payload.get("schema_version", 1)
    payload["values"] = values

    if not changed and CONFIG_OVERRIDE_PATH.is_file():
        print(f"[enable] no change ({CONFIG_OVERRIDE_PATH})")
        print(f"[enable] global_enabled_group_ids = {cleaned}")
        return 0

    _save_override(payload)
    print(f"[enable] wrote {CONFIG_OVERRIDE_PATH}")
    print(f"[enable] global_enabled_group_ids = {cleaned}")
    print(f"[enable] collect_bot_replies      = {values['collect_bot_replies']}")
    print("[enable] reminder: docker compose restart bot")
    return 0


async def _open_store(db_path: Path) -> StyleStore:
    store = StyleStore(db_path=str(db_path))
    await store.init()
    return store


async def _async_seed(group_id: str, db_path: Path, *, dry_run: bool) -> int:
    store = await _open_store(db_path)
    try:
        created = 0
        reinforced = 0
        approved_existing = 0
        for spec in SEED_EXPRESSIONS:
            new = NewStyleExpression(
                situation=spec["situation"],
                style=spec["style"],
                scope="group",
                group_id=group_id,
                status="approved",
                confidence=0.7,
                source="seed_script",
                output_policy="allow_use",
                persona_fit=0.6,
                mood_fit=0.6,
            )
            existing = await store.find_duplicate(new)
            if dry_run:
                if existing is None:
                    created += 1
                else:
                    if existing.status != "approved":
                        approved_existing += 1
                    else:
                        reinforced += 1
                continue
            result = await store.upsert_expression(
                new,
                actor="seed_script",
                reason="A0.4 style seed",
            )
            if existing is None:
                created += 1
            elif existing.status != "approved":
                await store.set_status(
                    result.expression_id,
                    "approved",
                    actor="seed_script",
                    reason="A0.4 promote seed to approved",
                )
                approved_existing += 1
            else:
                reinforced += 1
        verb = "would" if dry_run else "did"
        print(
            f"[seed] {verb}: created={created} approved-existing={approved_existing} "
            f"reinforced={reinforced} (group={group_id} db={db_path})"
        )
        return 0
    finally:
        await store.close()


def cmd_seed(group_id: str, db_path: Path, dry_run: bool) -> int:
    return asyncio.run(_async_seed(group_id, db_path, dry_run=dry_run))


async def _async_approve(group_id: str, db_path: Path, *, dry_run: bool) -> int:
    store = await _open_store(db_path)
    try:
        pending, _ = await store.list_expressions(
            scope="group",
            group_id=group_id,
            status="pending",
            limit=500,
        )
        if not pending:
            print(f"[approve] nothing pending in group={group_id}")
            return 0
        promoted = 0
        for expr in pending:
            if dry_run:
                promoted += 1
                continue
            ok = await store.set_status(
                expr.expression_id,
                "approved",
                actor="seed_script",
                reason="A0.4 batch approve pending",
            )
            if ok:
                promoted += 1
        verb = "would approve" if dry_run else "approved"
        print(f"[approve] {verb} {promoted}/{len(pending)} pending → approved (group={group_id})")
        return 0
    finally:
        await store.close()


def cmd_approve(group_id: str, db_path: Path, dry_run: bool) -> int:
    return asyncio.run(_async_approve(group_id, db_path, dry_run=dry_run))


async def _async_status(group_id: str, db_path: Path) -> int:
    store = await _open_store(db_path)
    try:
        approved, _ = await store.list_expressions(
            scope="group", group_id=group_id, status="approved", limit=200,
        )
        pending, _ = await store.list_expressions(
            scope="group", group_id=group_id, status="pending", limit=200,
        )
        rejected, _ = await store.list_expressions(
            scope="group", group_id=group_id, status="rejected", limit=200,
        )
        muted, _ = await store.list_expressions(
            scope="group", group_id=group_id, status="muted", limit=200,
        )
        print(f"[status] group={group_id} db={db_path}")
        print(f"  approved : {len(approved)}")
        print(f"  pending  : {len(pending)}")
        print(f"  rejected : {len(rejected)}")
        print(f"  muted    : {len(muted)}")
    finally:
        await store.close()
    payload = _load_override()
    values = payload.get("values", {}) if isinstance(payload, dict) else {}
    groups = values.get("global_enabled_group_ids", []) if isinstance(values, dict) else []
    collect = values.get("collect_bot_replies") if isinstance(values, dict) else None
    print(f"[status] override: collect_bot_replies={collect} groups={groups}")
    return 0


def cmd_status(group_id: str, db_path: Path) -> int:
    return asyncio.run(_async_status(group_id, db_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "command",
        choices=["enable", "seed", "approve", "status", "all"],
        help="操作子命令",
    )
    parser.add_argument("--group-id", required=True, help="目标群 ID")
    parser.add_argument("--db", default=str(DB_DEFAULT_PATH), help="style.db 路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写库")
    parser.add_argument("--force", action="store_true",
                        help="bypass the live-bot guard (dangerous; can corrupt SQLite)")
    args = parser.parse_args(argv)

    group_id = args.group_id.strip()
    if not group_id:
        print("error: --group-id is required and must be non-empty", file=sys.stderr)
        return 2

    db_path = Path(args.db)

    write_commands = {"seed", "approve", "all"}
    if args.command in write_commands and not args.dry_run:
        assert_bot_stopped(action=f"run style {args.command}", force=args.force)

    if args.command == "enable":
        return cmd_enable(group_id)
    if args.command == "seed":
        return cmd_seed(group_id, db_path, dry_run=args.dry_run)
    if args.command == "approve":
        return cmd_approve(group_id, db_path, dry_run=args.dry_run)
    if args.command == "status":
        return cmd_status(group_id, db_path)
    if args.command == "all":
        rc = cmd_enable(group_id)
        if rc != 0:
            return rc
        rc = cmd_seed(group_id, db_path, dry_run=args.dry_run)
        if rc != 0:
            return rc
        return cmd_approve(group_id, db_path, dry_run=args.dry_run)
    return 2


if __name__ == "__main__":
    sys.exit(main())
