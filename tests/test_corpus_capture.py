"""Tests for topic-block research corpus capture (services/group/corpus_capture.py).

Covers: schema init, hashing on/off, role tagging, edges + block-param snapshot
persistence, and the disabled (no-op) path.
"""

from __future__ import annotations

import sqlite3

from services.group.corpus_capture import CaptureRow, CorpusCapture


def _row(**kw) -> CaptureRow:
    base = dict(
        group_id="g1",
        message_id=1001,
        block_id="b1",
        role="human",
        speaker="123456",
        text="你会不会嫌我烦啊，亲爱的",
        reply_to_message_id=None,
        reply_to_speaker="",
        at_targets=(),
        reply_to_self=False,
        at_self=False,
        block_activity=1.5,
        block_participants=2,
        block_bot_involved=False,
        block_msg_count=3,
    )
    base.update(kw)
    return CaptureRow(**base)


def test_capture_writes_row_and_hashes_speaker(tmp_path) -> None:
    db = str(tmp_path / "corpus.db")
    cap = CorpusCapture(db, hash_speakers=True, salt="s")
    cap.init()
    cap.capture(_row())
    cap.close()

    c = sqlite3.connect(db)
    rows = c.execute(
        "SELECT group_id, block_id, role, speaker, text, block_participants, "
        "block_bot_involved, block_msg_count FROM topic_corpus"
    ).fetchall()
    c.close()
    assert len(rows) == 1
    r = rows[0]
    assert r[0] == "g1" and r[1] == "b1" and r[2] == "human"
    assert r[3] != "123456"  # speaker hashed
    assert len(r[3]) == 16
    assert r[4] == "你会不会嫌我烦啊，亲爱的"  # full text preserved
    assert r[5] == 2 and r[6] == 0 and r[7] == 3  # block-param snapshot


def test_plaintext_speaker_when_hashing_disabled(tmp_path) -> None:
    db = str(tmp_path / "corpus.db")
    cap = CorpusCapture(db, hash_speakers=False)
    cap.init()
    cap.capture(_row(speaker="999"))
    cap.close()
    c = sqlite3.connect(db)
    (sp,) = c.execute("SELECT speaker FROM topic_corpus").fetchone()
    c.close()
    assert sp == "999"


def test_hash_is_stable_and_links_same_speaker(tmp_path) -> None:
    """Same QQ -> same hash, so per-speaker time series stays linkable."""
    db = str(tmp_path / "corpus.db")
    cap = CorpusCapture(db, hash_speakers=True, salt="s")
    cap.init()
    cap.capture(_row(message_id=1, speaker="42"))
    cap.capture(_row(message_id=2, speaker="42"))
    cap.capture(_row(message_id=3, speaker="7"))
    cap.close()
    c = sqlite3.connect(db)
    hashes = [h for (h,) in c.execute("SELECT speaker FROM topic_corpus ORDER BY message_id")]
    c.close()
    assert hashes[0] == hashes[1]  # same speaker links
    assert hashes[0] != hashes[2]  # different speaker differs


def test_edges_and_role_persisted(tmp_path) -> None:
    db = str(tmp_path / "corpus.db")
    cap = CorpusCapture(db, hash_speakers=True, salt="s")
    cap.init()
    cap.capture(
        _row(
            role="ai",
            reply_to_message_id=1001,
            reply_to_speaker="123456",
            at_targets=("888", "999"),
            reply_to_self=True,
            at_self=True,
        )
    )
    cap.close()
    c = sqlite3.connect(db)
    r = c.execute(
        "SELECT role, reply_to_message_id, reply_to_speaker, at_targets, "
        "reply_to_self, at_self FROM topic_corpus"
    ).fetchone()
    c.close()
    assert r[0] == "ai"
    assert r[1] == 1001
    assert r[2] and r[2] != "123456"  # reply speaker hashed
    assert r[3].startswith("[") and "888" not in r[3]  # at_targets hashed JSON
    assert r[4] == 1 and r[5] == 1


def test_capture_is_noop_without_init() -> None:
    """A capture before init (db is None) must not raise."""
    cap = CorpusCapture("unused.db")
    cap.capture(_row())  # no init() -> swallowed, no file created
