#!/usr/bin/env python3
"""Seed catchphrase candidates from EpisodeStore into LearningNormalizer.

This is an offline rollout helper for humanization Part 1 / V7. It reads
approved-ish episode rows, extracts short conservative phrase candidates, and
attaches them to the normalizer with domain/profile ``catchphrase``.

The script never invents seed data. If the episode source is empty, the correct
result is selected=0 and written=0.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.dev._bot_guard import assert_bot_stopped  # noqa: E402
from services.episodic import Episode, EpisodeStore  # noqa: E402
from services.learning_normalizer import LearningNormalizerStore, normalize_key  # noqa: E402

DEFAULT_EPISODE_DB = ROOT / "storage" / "episodic.db"
DEFAULT_NORMALIZER_DB = ROOT / "storage" / "learning_normalizer.db"
SOURCE_TABLE = "episode"
EPISODE_STATES = ("enabled_for_prompt", "approved", "candidate")

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_CQ_RE = re.compile(r"\[CQ:[^\]]+\]", re.IGNORECASE)
_SPLIT_RE = re.compile(r"[\n\r。！？!?；;]+")
_SOFT_SPLIT_RE = re.compile(r"[，,、]+")
_LEADING_SPEAKER_RE = re.compile(r"^[^:：]{1,16}[:：]\s*")
_PUNCT_ONLY_RE = re.compile(r"^[\W_]+$", re.UNICODE)
_SUMMARY_HINTS = (
    "用户",
    "助手",
    "机器人",
    "bot",
    "Bot",
    "回复",
    "回应",
    "适合",
    "不适合",
    "应该",
    "避免",
    "总结",
    "偏好",
    "情绪",
    "场景",
    "上下文",
)


@dataclass(frozen=True, slots=True)
class CatchphraseSeedCandidate:
    raw_text: str
    episode_id: str
    group_id: str
    source_field: str
    episode_state: str


@dataclass(slots=True)
class CatchphraseSeedResult:
    scanned_episodes: int = 0
    extracted: int = 0
    selected: int = 0
    written: int = 0
    skipped_existing: int = 0
    skipped_no_group: int = 0
    dry_run: bool = False


def _clean_phrase(value: str) -> str:
    text = str(value or "").strip()
    text = _CQ_RE.sub("", text)
    text = _URL_RE.sub("", text)
    text = _LEADING_SPEAKER_RE.sub("", text)
    return text.strip(" \t\"'`~*_-=+()[]{}<>“”‘’「」『』《》【】")


def _looks_like_summary(text: str) -> bool:
    compact = text.strip()
    if not compact:
        return True
    if _PUNCT_ONLY_RE.match(compact):
        return True
    if _URL_RE.search(compact) or _CQ_RE.search(compact):
        return True
    if len(compact) < 2 or len(compact) > 28:
        return True
    if compact.isdigit():
        return True
    if any(hint in compact for hint in _SUMMARY_HINTS) and len(compact) >= 6:
        return True
    key = normalize_key(compact, "catchphrase")
    return not key or len(key) < 2


def extract_catchphrase_phrases(value: str) -> list[str]:
    """Extract conservative short phrase candidates from one text field."""
    phrases: list[str] = []
    seen: set[str] = set()
    for sentence in _SPLIT_RE.split(str(value or "")):
        parts = _SOFT_SPLIT_RE.split(sentence)
        for part in parts:
            text = _clean_phrase(part)
            if _looks_like_summary(text):
                continue
            key = normalize_key(text, "catchphrase")
            if key in seen:
                continue
            seen.add(key)
            phrases.append(text)
    return phrases


def _meta_text_sources(meta: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text:
                rows.append((path, text))
            return
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}" if path else str(key))
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(meta, "meta")
    return rows


def _episode_text_sources(episode: Episode) -> list[tuple[str, str]]:
    rows = [
        ("situation", episode.situation),
        ("observed_context", episode.observed_context),
        ("action_taken", episode.action_taken),
        ("outcome_signal", episode.outcome_signal),
        ("reflection", episode.reflection),
    ]
    rows.extend(_meta_text_sources(episode.meta))
    return [(field, text) for field, text in rows if str(text or "").strip()]


def candidates_from_episodes(episodes: list[Episode], *, limit: int) -> tuple[list[CatchphraseSeedCandidate], int, int]:
    candidates: list[CatchphraseSeedCandidate] = []
    seen_keys: set[str] = set()
    extracted = 0
    skipped_no_group = 0
    for episode in episodes:
        group_id = str(episode.group_id or "").strip()
        episode_candidates: list[CatchphraseSeedCandidate] = []
        for field, text in _episode_text_sources(episode):
            for phrase in extract_catchphrase_phrases(text):
                extracted += 1
                key = normalize_key(phrase, "catchphrase")
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                episode_candidates.append(CatchphraseSeedCandidate(
                    raw_text=phrase,
                    episode_id=episode.episode_id,
                    group_id=group_id,
                    source_field=field,
                    episode_state=episode.episode_state,
                ))
        if not group_id:
            skipped_no_group += len(episode_candidates)
            continue
        candidates.extend(episode_candidates)
        if len(candidates) >= limit:
            break
    return candidates[:limit], extracted, skipped_no_group


async def _candidate_exists(store: LearningNormalizerStore, candidate: CatchphraseSeedCandidate) -> bool:
    db = store._require_db()
    cursor = await db.execute(
        """SELECT 1 FROM learning_normalizer_items
           WHERE domain = ? AND source_table = ? AND source_id = ? AND raw_text = ?
           LIMIT 1""",
        ("catchphrase", SOURCE_TABLE, candidate.episode_id, candidate.raw_text),
    )
    return await cursor.fetchone() is not None


async def seed_catchphrase_pool(
    *,
    episode_db: Path,
    normalizer_db: Path,
    limit: int = 30,
    scan_limit: int = 300,
    dry_run: bool = False,
) -> CatchphraseSeedResult:
    result = CatchphraseSeedResult(dry_run=dry_run)
    episode_store = EpisodeStore(str(episode_db))
    normalizer_store = LearningNormalizerStore(normalizer_db)
    await episode_store.init()
    await normalizer_store.init()
    try:
        episodes = await episode_store.list_episodes(
            state_filter=list(EPISODE_STATES),
            limit=max(1, int(scan_limit)),
        )
        result.scanned_episodes = len(episodes)
        candidates, extracted, skipped_no_group = candidates_from_episodes(episodes, limit=max(1, int(limit)))
        result.extracted = extracted
        result.skipped_no_group = skipped_no_group
        result.selected = len(candidates)
        for candidate in candidates:
            if await _candidate_exists(normalizer_store, candidate):
                result.skipped_existing += 1
                continue
            if dry_run:
                continue
            await normalizer_store.attach_candidate(
                domain="catchphrase",
                profile="catchphrase",
                scope="group",
                group_id=candidate.group_id,
                raw_text=candidate.raw_text,
                source_table=SOURCE_TABLE,
                source_id=candidate.episode_id,
                meta={
                    "seed_script": "seed_catchphrase_pool.py",
                    "source_field": candidate.source_field,
                    "episode_state": candidate.episode_state,
                },
            )
            result.written += 1
        return result
    finally:
        await normalizer_store.close()
        await episode_store.close()


def _print_result(result: CatchphraseSeedResult) -> None:
    mode = "dry-run" if result.dry_run else "write"
    print(
        "[seed-catchphrase] "
        f"mode={mode} scanned_episodes={result.scanned_episodes} "
        f"extracted={result.extracted} selected={result.selected} "
        f"written={result.written} skipped_existing={result.skipped_existing} "
        f"skipped_no_group={result.skipped_no_group}"
    )
    if result.selected < 30:
        print(
            "[seed-catchphrase] source sample below target: "
            f"selected={result.selected}/30; no synthetic catchphrases were created"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episode-db", default=str(DEFAULT_EPISODE_DB), help="EpisodeStore sqlite path")
    parser.add_argument("--normalizer-db", default=str(DEFAULT_NORMALIZER_DB), help="LearningNormalizer sqlite path")
    parser.add_argument("--limit", type=int, default=30, help="maximum candidates to seed")
    parser.add_argument("--scan-limit", type=int, default=300, help="maximum episodes to scan")
    parser.add_argument("--dry-run", action="store_true", help="read and report without writing")
    parser.add_argument("--force", action="store_true", help="bypass live-bot SQLite guard for writes")
    args = parser.parse_args(argv)

    if args.limit <= 0:
        print("error: --limit must be positive", file=sys.stderr)
        return 2
    if args.scan_limit <= 0:
        print("error: --scan-limit must be positive", file=sys.stderr)
        return 2
    if not args.dry_run:
        assert_bot_stopped(action="seed catchphrase pool", force=args.force)

    result = asyncio.run(seed_catchphrase_pool(
        episode_db=Path(args.episode_db),
        normalizer_db=Path(args.normalizer_db),
        limit=args.limit,
        scan_limit=args.scan_limit,
        dry_run=args.dry_run,
    ))
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
