#!/usr/bin/env python3
"""Re-caption sticker usage_hint values with vision-generated emotion tags."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.config import load_config, load_plugin_config  # noqa: E402
from scripts.dev._bot_guard import assert_bot_stopped  # noqa: E402
from services.media.sticker_capture import (  # noqa: E402
    DEFAULT_STICKER_USAGE_HINT,
    emit_emotion_tag,
    sticker_media_type,
)
from services.media.sticker_store import StickerStore  # noqa: E402
from services.media.vision import VisionClient  # noqa: E402


class _StickerConfig(BaseModel):
    storage_dir: str = "storage/stickers"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.json", help="Bot config path (json or toml).")
    parser.add_argument("--storage-dir", default="", help="Override sticker storage dir.")
    parser.add_argument("--limit", type=int, default=0, help="Max stickers to process (0 = all).")
    parser.add_argument("--only-fallback", action="store_true", help="Only recaption fallback usage hints.")
    parser.add_argument("--dry-run", action="store_true", help="Print proposed tags without writing.")
    parser.add_argument("--force", action="store_true", help="Bypass the host-side bot-running guard.")
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    if not args.dry_run:
        assert_bot_stopped(action="re-caption sticker usage hints", force=args.force)

    cfg = load_config(config_path=args.config)
    if not cfg.vision.enabled or not cfg.vision.qwen.api_key.strip():
        print("vision client is disabled or missing api_key", file=sys.stderr)
        return 2

    sticker_cfg = load_plugin_config("plugins/sticker/config.default.json", _StickerConfig)
    store = StickerStore(storage_dir=args.storage_dir or sticker_cfg.storage_dir)
    vision = VisionClient(
        base_url=cfg.vision.qwen.base_url,
        api_key=cfg.vision.qwen.api_key,
        model=cfg.vision.qwen.model,
    )
    rows = list(store.list_all().items())
    if args.limit > 0:
        rows = rows[:args.limit]

    updated = 0
    skipped = 0
    failed = 0
    for sticker_id, entry in rows:
        current_hint = str(entry.get("usage_hint") or "").strip()
        if args.only_fallback and current_hint and current_hint != DEFAULT_STICKER_USAGE_HINT:
            skipped += 1
            continue
        path = store.resolve_path(sticker_id)
        if path is None or not path.exists():
            failed += 1
            print(f"{sticker_id}\tmissing_file")
            continue
        try:
            tag = await emit_emotion_tag(
                store,
                sticker_id,
                image_data=path.read_bytes(),
                vision_client=vision,
                media_type=sticker_media_type(path),
                overwrite=True,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            failed += 1
            print(f"{sticker_id}\terror\t{exc}")
            continue
        if tag == current_hint:
            skipped += 1
        else:
            updated += 1
        print(f"{sticker_id}\t{current_hint or '-'}\t{tag}")

    print(f"done updated={updated} skipped={skipped} failed={failed} total={len(rows)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
