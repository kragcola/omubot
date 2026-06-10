#!/usr/bin/env python3
"""Repair stored "naked" JPEG stickers (missing JFIF APP0) that QQ rejects.

Tencent's rich-media upload rejects JPEGs whose SOI is not followed by an APPn
segment (``rich media transfer failed``, retcode 1200). These were produced by
the vision image cache's ``jpegsave(strip=True)`` and then saved into the
sticker library. This rewrites each affected file with a standard JFIF APP0
segment and re-keys its row, preserving all metadata.

Storage is a docker named volume, so run this INSIDE the bot container:

    docker compose exec qq-bot .venv/bin/python -m scripts.dev.sticker_fix_naked_jpeg --dry-run
    docker compose exec qq-bot .venv/bin/python -m scripts.dev.sticker_fix_naked_jpeg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.config import load_plugin_config  # noqa: E402
from services.media.sticker_store import StickerStore  # noqa: E402


class _StickerConfig(BaseModel):
    storage_dir: str = "storage/stickers"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-dir", default="", help="Override sticker storage dir.")
    parser.add_argument("--dry-run", action="store_true", help="List affected stickers without writing.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    sticker_cfg = load_plugin_config("plugins/sticker/config.default.json", _StickerConfig)
    store = StickerStore(storage_dir=args.storage_dir or sticker_cfg.storage_dir)
    try:
        repaired = store.renormalize_naked_jpegs(dry_run=args.dry_run)
    finally:
        store.close()

    verb = "would repair" if args.dry_run else "repaired"
    print(f"{verb} {len(repaired)} naked JPEG sticker(s)")
    for old_id, new_id in repaired:
        rekey = "" if old_id == new_id else f" -> {new_id}"
        print(f"  {old_id}{rekey}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
