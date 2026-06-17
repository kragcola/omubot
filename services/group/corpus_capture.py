"""Topic-block research corpus capture (explicit, opt-in, reversible).

A pure side-channel sink for offline research on human-vs-AI affective
dynamics. When ``topic_block.corpus_capture_enabled`` is true, every observed
group message is persisted together with its topic-block attribution and a
snapshot of the block parameters. This is the missing bridge that makes the
in-memory ``TopicBlockTracker`` state recoverable after the process exits.

Design guarantees (so it can be turned off with zero residue):
- Writes ONLY to its own ``storage/topic_corpus.db``. Never touches
  ``messages.db``, the reply pipeline, prompt cache prefixes, or NapCat.
- Disabled by default. When disabled, ``CorpusCapture`` is never constructed
  and the scheduler hook returns immediately (no I/O, no import cost).
- Best-effort and failure-swallowing: a capture error never affects dispatch.
- Speaker QQ ids are SHA256-hashed (with salt) by default; plaintext only if
  explicitly configured. Raw text is stored in full (needed for offline
  Chinese sentiment scoring + H2 compensation analysis).

To fully revert: set the flag false (or remove the config block) and delete
``storage/topic_corpus.db``. The code itself is an isolated module.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass

from loguru import logger

_L = logger.bind(channel="debug")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS topic_corpus (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id              TEXT    NOT NULL,
    message_id            INTEGER,
    block_id              TEXT,
    role                  TEXT    NOT NULL,           -- 'human' | 'ai'
    speaker               TEXT,                       -- hashed QQ (or plaintext if configured)
    text                  TEXT,
    reply_to_message_id   INTEGER,
    reply_to_speaker      TEXT,                       -- hashed (same scheme as speaker)
    at_targets            TEXT,                       -- JSON array of hashed ids
    reply_to_self         INTEGER NOT NULL DEFAULT 0, -- 1 if replying to the bot
    at_self               INTEGER NOT NULL DEFAULT 0, -- 1 if @-addressing the bot
    block_activity        REAL,                       -- block-param snapshot at observe time
    block_participants    INTEGER,                    -- participant count in block
    block_bot_involved    INTEGER NOT NULL DEFAULT 0,
    block_msg_count       INTEGER,                    -- messages in block so far
    captured_at           REAL    NOT NULL
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tc_group_block ON topic_corpus(group_id, block_id)",
    "CREATE INDEX IF NOT EXISTS idx_tc_group_time ON topic_corpus(group_id, captured_at)",
    "CREATE INDEX IF NOT EXISTS idx_tc_speaker ON topic_corpus(speaker)",
]

_INSERT = """
INSERT INTO topic_corpus
    (group_id, message_id, block_id, role, speaker, text,
     reply_to_message_id, reply_to_speaker, at_targets,
     reply_to_self, at_self,
     block_activity, block_participants, block_bot_involved, block_msg_count,
     captured_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass(frozen=True)
class CaptureRow:
    """One observed message + its topic-block attribution snapshot."""

    group_id: str
    message_id: int | None
    block_id: str
    role: str
    speaker: str
    text: str
    reply_to_message_id: int | None
    reply_to_speaker: str
    at_targets: tuple[str, ...]
    reply_to_self: bool
    at_self: bool
    block_activity: float
    block_participants: int
    block_bot_involved: bool
    block_msg_count: int


class CorpusCapture:
    """Synchronous, best-effort SQLite sink. Constructed only when enabled."""

    def __init__(
        self,
        db_path: str = "storage/topic_corpus.db",
        *,
        hash_speakers: bool = True,
        salt: str = "omubot-topic-corpus",
    ) -> None:
        self._db_path = db_path
        self._hash_speakers = hash_speakers
        self._salt = salt
        self._db: sqlite3.Connection | None = None

    def init(self) -> None:
        """Open the side-channel db and ensure schema. Best-effort."""
        try:
            self._db = sqlite3.connect(self._db_path, check_same_thread=False)
            self._db.execute(_CREATE_TABLE)
            for idx in _CREATE_INDEXES:
                self._db.execute(idx)
            self._db.commit()
            _L.info("topic corpus capture armed | db={}", self._db_path)
        except Exception:
            _L.exception("topic corpus capture init failed (capture disabled)")
            self._db = None

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.commit()
                self._db.close()
            except Exception:
                pass
            self._db = None

    def _anon(self, qq: str) -> str:
        """Hash a speaker id, or pass through if hashing is disabled."""
        if not qq:
            return ""
        if not self._hash_speakers:
            return qq
        return hashlib.sha256((self._salt + qq).encode("utf-8")).hexdigest()[:16]

    def capture(self, row: CaptureRow) -> None:
        """Persist one row. Never raises into the caller."""
        if self._db is None:
            return
        try:
            self._db.execute(
                _INSERT,
                (
                    row.group_id,
                    row.message_id,
                    row.block_id,
                    row.role,
                    self._anon(row.speaker),
                    row.text,
                    row.reply_to_message_id,
                    self._anon(row.reply_to_speaker),
                    json.dumps([self._anon(t) for t in row.at_targets], ensure_ascii=False),
                    1 if row.reply_to_self else 0,
                    1 if row.at_self else 0,
                    row.block_activity,
                    row.block_participants,
                    1 if row.block_bot_involved else 0,
                    row.block_msg_count,
                    time.time(),
                ),
            )
            self._db.commit()
        except Exception:
            _L.exception("topic corpus capture write failed (swallowed)")
