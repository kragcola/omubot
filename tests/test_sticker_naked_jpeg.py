"""Tests for naked-JPEG (missing JFIF APP0) normalization.

Tencent rejects JPEGs whose SOI is not followed by an APPn segment. These
helpers/paths re-insert a standard JFIF APP0 so saved stickers stay sendable.
"""

from __future__ import annotations

from services.media.jpeg_util import ensure_jfif_app0, is_naked_jpeg
from services.media.sticker_store import StickerStore

# Naked JPEG: SOI immediately followed by DQT (ff db) — no APP segment.
_NAKED_JPEG = b"\xff\xd8\xff\xdb\x00\x43" + b"\x00" * 64 + b"naked-payload"
# Standard JFIF JPEG: SOI followed by APP0 (ff e0).
_JFIF_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32 + b"jfif-payload"
# EXIF JPEG: SOI followed by APP1 (ff e1).
_EXIF_JPEG = b"\xff\xd8\xff\xe1\x00\x10Exif\x00" + b"\x00" * 32 + b"exif-payload"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"png"


def test_is_naked_jpeg_detection() -> None:
    assert is_naked_jpeg(_NAKED_JPEG) is True
    assert is_naked_jpeg(_JFIF_JPEG) is False
    assert is_naked_jpeg(_EXIF_JPEG) is False
    assert is_naked_jpeg(_PNG) is False
    assert is_naked_jpeg(b"") is False


def test_ensure_jfif_app0_inserts_for_naked() -> None:
    fixed = ensure_jfif_app0(_NAKED_JPEG)
    # now starts with SOI + APP0 (ff d8 ff e0)
    assert fixed[:4] == b"\xff\xd8\xff\xe0"
    assert fixed[4:6] == b"\x00\x10"  # APP0 length 16
    assert fixed[6:11] == b"JFIF\x00"
    # original image data preserved after the injected 18-byte segment
    assert fixed[20:] == _NAKED_JPEG[2:]
    assert not is_naked_jpeg(fixed)


def test_ensure_jfif_app0_noop_for_standard() -> None:
    assert ensure_jfif_app0(_JFIF_JPEG) == _JFIF_JPEG
    assert ensure_jfif_app0(_EXIF_JPEG) == _EXIF_JPEG
    assert ensure_jfif_app0(_PNG) == _PNG


def test_store_add_normalizes_naked_jpeg(tmp_path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    try:
        sid, is_new = store.add(_NAKED_JPEG, "desc", "hint")
        assert is_new is True
        # stored bytes are the normalized (sendable) version
        path = store.resolve_path(sid)
        assert path is not None
        stored = path.read_bytes()
        assert stored[:4] == b"\xff\xd8\xff\xe0"
        assert not is_naked_jpeg(stored)
        # id is the hash of the normalized bytes (re-adding the naked original
        # dedupes to the same id)
        sid2, is_new2 = store.add(_NAKED_JPEG, "d2", "h2")
        assert sid2 == sid
        assert is_new2 is False
    finally:
        store.close()


def test_store_renormalize_repairs_legacy_naked(tmp_path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    try:
        # Simulate a legacy naked sticker already on disk + in the index by
        # writing the file and DB row with the naked bytes' own hash.
        import hashlib

        naked_id = "stk_" + hashlib.sha256(_NAKED_JPEG).hexdigest()[:8]
        naked_file = f"{naked_id}.jpg"
        (store.storage_dir / naked_file).write_bytes(_NAKED_JPEG)
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with store._db:  # type: ignore[attr-defined]
            store._db.execute(  # type: ignore[attr-defined]
                "INSERT INTO stickers (sticker_id, file, description, usage_hint, "
                "ocr_text, source, send_count, last_sent, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (naked_id, naked_file, "legacy", "hint", "", "stolen", 7, None, now),
            )
        store._index[naked_id] = {  # type: ignore[attr-defined]
            "file": naked_file, "description": "legacy", "usage_hint": "hint",
            "ocr_text": "", "source": "stolen", "send_count": 7,
            "last_sent": None, "created_at": now,
        }

        # dry-run reports it without changing anything
        preview = store.renormalize_naked_jpegs(dry_run=True)
        assert len(preview) == 1
        assert preview[0][0] == naked_id
        assert (store.storage_dir / naked_file).exists()

        repaired = store.renormalize_naked_jpegs()
        assert len(repaired) == 1
        old_id, new_id = repaired[0]
        assert old_id == naked_id
        assert new_id != old_id
        # old file gone, new file present + sendable, metadata preserved
        assert not (store.storage_dir / naked_file).exists()
        entry = store.get(new_id)
        assert entry is not None
        assert entry["send_count"] == 7
        assert entry["description"] == "legacy"
        fixed = store.resolve_path(new_id).read_bytes()  # type: ignore[union-attr]
        assert not is_naked_jpeg(fixed)
        # idempotent: a second pass repairs nothing
        assert store.renormalize_naked_jpegs() == []
    finally:
        store.close()
