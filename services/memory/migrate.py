"""One-shot migration from old .md memos to typed SQLite cards."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from services.memory.card_store import CardStore, NewCard
from services.memory.memo_store import PENDING_HEADER, parse_memo

if TYPE_CHECKING:
    pass

_BULLET_RE = re.compile(r"^-\s+(.+)")


async def migrate_md_to_cards(md_base_dir: str, card_store: CardStore) -> int:
    """Read old .md memo files, create fact cards, rename originals to .md.migrated.

    Returns total number of cards created. Skips files that already have a
    .md.migrated sibling (idempotent).
    """
    base = Path(md_base_dir)
    if not base.exists():
        logger.info("migration skipped: base dir {} does not exist", base)
        return 0

    created = 0

    for subdir_name, scope in [("users", "user"), ("groups", "group")]:
        subdir = base / subdir_name
        if not subdir.exists():
            continue
        for md_file in sorted(subdir.glob("*.md")):
            migrated_marker = md_file.with_suffix(".md.migrated")
            if migrated_marker.exists():
                logger.debug("migration skip (already migrated): {}", md_file)
                continue

            try:
                text = md_file.read_text(encoding="utf-8")
                num = md_file.stem
                memo_id = f"{scope}_{num}"
                memo = parse_memo(memo_id, text)

                # Main body content → one fact card
                body = memo.body.strip()
                if body:
                    await card_store.add_card(NewCard(
                        category="fact",
                        scope=scope,
                        scope_id=num,
                        content=_truncate(body, 500),
                        confidence=0.7,
                        source="migration",
                    ))
                    created += 1

                # Each pending bullet → one fact card (lower confidence)
                if PENDING_HEADER in body:
                    pending_start = body.index(PENDING_HEADER)
                    pending_section = body[pending_start + len(PENDING_HEADER):]
                    for line in pending_section.splitlines():
                        m = _BULLET_RE.match(line.strip())
                        if m:
                            note = m.group(1).strip()
                            if note and len(note) > 3:
                                await card_store.add_card(NewCard(
                                    category="fact",
                                    scope=scope,
                                    scope_id=num,
                                    content=_truncate(note, 300),
                                    confidence=0.6,
                                    source="migration",
                                ))
                                created += 1

                # Rename original to mark as migrated
                md_file.rename(migrated_marker)
                logger.info("migrated: {} -> {} cards", md_file.name, 1)

            except Exception:
                logger.warning("migration failed for {}: ", md_file, exc_info=True)

    if created:
        logger.info("migration complete: {} cards created from {}", created, md_base_dir)
    return created


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
