"""Tests for StickerConfig and StickerStore."""

import json
from pathlib import Path

import pytest

from plugins.sticker import StickerConfig
from services.media.sticker_store import StickerStore

# ---------------------------------------------------------------------------
# Minimal valid image byte sequences (magic bytes + padding for distinct hashes)
# ---------------------------------------------------------------------------

# JPEG: starts with ff d8 ff
_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"jpeg-payload-a"

# PNG: starts with 89 50 4e 47 0d 0a 1a 0a
_PNG_DATA = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + b"png-payload-a"

# Another JPEG with different content for dedup testing
_JPEG_DATA_B = b"\xff\xd8\xff\xe0" + b"\x01" * 64 + b"jpeg-payload-b"

# WebP: RIFF....WEBP
_WEBP_DATA = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64 + b"webp-payload"

# GIF: starts with GIF89a
_GIF_DATA = b"GIF89a" + b"\x00" * 64 + b"gif-payload"

# GIF87a variant
_GIF87_DATA = b"GIF87a" + b"\x00" * 64 + b"gif87-payload"

# Unknown format
_UNKNOWN_DATA = b"\x00\x01\x02\x03" + b"\xde\xad\xbe\xef" * 20


# ---------------------------------------------------------------------------
# Task 1: StickerConfig
# ---------------------------------------------------------------------------


def test_sticker_config_defaults() -> None:
    cfg = StickerConfig()
    assert cfg.enabled is True
    assert cfg.storage_dir == "storage/stickers"
    assert cfg.max_count == 300
    assert cfg.learn_min_occurrences == 3
    assert cfg.learn_window_hours == 24.0
    assert cfg.prompt_view_max == 100
    assert cfg.prompt_view_recent_slice == 20


def test_sticker_config_custom() -> None:
    cfg = StickerConfig(enabled=False, storage_dir="/tmp/stickers", max_count=50)
    assert cfg.enabled is False
    assert cfg.storage_dir == "/tmp/stickers"
    assert cfg.max_count == 50



# ---------------------------------------------------------------------------
# Task 2 + 3: StickerStore
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> StickerStore:
    return StickerStore(storage_dir=str(tmp_path / "stickers"))


# ------------------------------------------------------------------
# Init
# ------------------------------------------------------------------


def test_init_creates_directory(tmp_path) -> None:
    path = tmp_path / "new_stickers"
    assert not path.exists()
    StickerStore(storage_dir=str(path))
    assert path.is_dir()


def test_init_empty_index(store: StickerStore) -> None:
    assert store.list_all() == {}


def test_storage_dir_property(tmp_path) -> None:
    path = tmp_path / "stickers"
    s = StickerStore(storage_dir=str(path))
    assert s.storage_dir == path


def test_max_count_property(tmp_path) -> None:
    s = StickerStore(storage_dir=str(tmp_path / "s"), max_count=42)
    assert s.max_count == 42


# ------------------------------------------------------------------
# add() — success cases
# ------------------------------------------------------------------


def test_add_jpeg(store: StickerStore) -> None:
    sid, is_new = store.add(_JPEG_DATA, "测试JPEG", "开心时发", source="auto")
    assert sid.startswith("stk_")
    assert is_new is True
    entry = store.get(sid)
    assert entry is not None
    assert entry["description"] == "测试JPEG"
    assert entry["usage_hint"] == "开心时发"
    assert entry["source"] == "auto"
    assert entry["send_count"] == 0
    assert entry["last_sent"] is None
    assert "created_at" in entry
    assert entry["file"].endswith(".jpg")


def test_add_png(store: StickerStore) -> None:
    sid, is_new = store.add(_PNG_DATA, "测试PNG", "悲伤时发", source="admin")
    assert sid.startswith("stk_")
    assert is_new is True
    entry = store.get(sid)
    assert entry is not None
    assert entry["file"].endswith(".png")
    assert entry["source"] == "admin"


def test_add_webp(store: StickerStore) -> None:
    sid, is_new = store.add(_WEBP_DATA, "测试WebP", "任何时候", source="auto")
    assert sid.startswith("stk_")
    assert is_new is True
    entry = store.get(sid)
    assert entry is not None
    assert entry["file"].endswith(".webp")


def test_add_writes_file(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "desc", "hint")
    path = store.resolve_path(sid)
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == _JPEG_DATA


def test_add_source_admin(store: StickerStore) -> None:
    sid, is_new = store.add(_JPEG_DATA, "管理员添加", "正式使用", source="admin")
    assert is_new is True
    entry = store.get(sid)
    assert entry is not None
    assert entry["source"] == "admin"


# ------------------------------------------------------------------
# add() — deduplication
# ------------------------------------------------------------------


def test_add_dedup_same_image(store: StickerStore) -> None:
    sid1, is_new1 = store.add(_JPEG_DATA, "first", "hint1")
    sid2, is_new2 = store.add(_JPEG_DATA, "second", "hint2")
    assert sid1 == sid2
    assert is_new1 is True
    assert is_new2 is False
    # Metadata should still be from the first add
    entry = store.get(sid1)
    assert entry is not None
    assert entry["description"] == "first"


def test_add_two_different_images_are_distinct(store: StickerStore) -> None:
    sid1, _ = store.add(_JPEG_DATA, "a", "hint")
    sid2, _ = store.add(_JPEG_DATA_B, "b", "hint")
    assert sid1 != sid2
    assert len(store.list_all()) == 2


# ------------------------------------------------------------------
# add() — GIF accepted
# ------------------------------------------------------------------


def test_add_gif_accepted(store: StickerStore) -> None:
    """GIF stickers are now supported (animated GIFs preserved as-is)."""
    sid, is_new = store.add(_GIF_DATA, "gif", "hint")
    assert is_new
    assert sid.startswith("stk_")
    # Verify file was saved with .gif extension
    path = store.resolve_path(sid)
    assert path is not None
    assert path.suffix == ".gif"
    assert path.read_bytes() == _GIF_DATA


def test_add_gif87_accepted(store: StickerStore) -> None:
    """GIF87a format also supported."""
    sid, is_new = store.add(_GIF87_DATA, "gif87", "hint")
    assert is_new
    assert sid.startswith("stk_")
    path = store.resolve_path(sid)
    assert path is not None
    assert path.suffix == ".gif"


def test_add_unknown_format_rejected(store: StickerStore) -> None:
    with pytest.raises(ValueError):
        store.add(_UNKNOWN_DATA, "unknown", "hint")


# ------------------------------------------------------------------
# remove()
# ------------------------------------------------------------------


def test_remove_existing(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "desc", "hint")
    path = store.resolve_path(sid)
    assert path is not None and path.exists()

    result = store.remove(sid)
    assert result is True
    assert store.get(sid) is None
    assert not path.exists()


def test_remove_nonexistent(store: StickerStore) -> None:
    result = store.remove("stk_00000000")
    assert result is False


def test_remove_updates_list(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "desc", "hint")
    store.add(_PNG_DATA, "png", "hint")
    store.remove(sid)
    assert sid not in store.list_all()
    assert len(store.list_all()) == 1


# ------------------------------------------------------------------
# record_send()
# ------------------------------------------------------------------


def test_record_send_increments_count(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "desc", "hint")
    entry = store.get(sid)
    assert entry is not None
    assert entry["send_count"] == 0

    store.record_send(sid)
    entry = store.get(sid)
    assert entry is not None
    assert entry["send_count"] == 1

    store.record_send(sid)
    entry = store.get(sid)
    assert entry is not None
    assert entry["send_count"] == 2


def test_record_send_updates_last_sent(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "desc", "hint")
    entry = store.get(sid)
    assert entry is not None
    assert entry["last_sent"] is None

    store.record_send(sid)
    entry = store.get(sid)
    assert entry is not None
    last_sent = entry["last_sent"]
    assert last_sent is not None
    # Should be a valid ISO datetime string
    from datetime import datetime
    dt = datetime.fromisoformat(last_sent)
    assert dt.tzinfo is not None  # timezone-aware


def test_record_send_nonexistent_is_noop(store: StickerStore) -> None:
    # Should not raise
    store.record_send("stk_00000000")


# ------------------------------------------------------------------
# update()
# ------------------------------------------------------------------


def test_update_description(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "old desc", "old hint")
    assert store.update(sid, description="new desc")
    entry = store.get(sid)
    assert entry is not None
    assert entry["description"] == "new desc"
    assert entry["usage_hint"] == "old hint"


def test_update_usage_hint(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "desc", "old hint")
    assert store.update(sid, usage_hint="new hint")
    entry = store.get(sid)
    assert entry is not None
    assert entry["usage_hint"] == "new hint"
    assert entry["description"] == "desc"


def test_update_both(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "old", "old")
    assert store.update(sid, description="new desc", usage_hint="new hint")
    entry = store.get(sid)
    assert entry is not None
    assert entry["description"] == "new desc"
    assert entry["usage_hint"] == "new hint"


def test_update_nonexistent(store: StickerStore) -> None:
    assert not store.update("stk_nonexist", description="x")


def test_update_persists(tmp_path: Path) -> None:
    store1 = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sid, _ = store1.add(_JPEG_DATA, "old", "old")
    store1.update(sid, description="new")
    store2 = StickerStore(storage_dir=str(tmp_path / "stickers"))
    assert store2.get(sid)["description"] == "new"  # type: ignore[index]


# ------------------------------------------------------------------
# ocr_text 字段（阶段 1：OCR 入库）
# ------------------------------------------------------------------


def test_add_writes_ocr_text_field(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "desc", "hint", ocr_text="晚安")
    entry = store.get(sid)
    assert entry is not None
    assert entry["ocr_text"] == "晚安"


def test_add_default_ocr_text_empty(store: StickerStore) -> None:
    """旧调用不传 ocr_text → 默认空，key 仍存在（向后兼容）。"""
    sid, _ = store.add(_JPEG_DATA, "desc", "hint")
    entry = store.get(sid)
    assert entry is not None
    assert entry["ocr_text"] == ""


def test_update_ocr_text_only(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "desc", "hint")
    assert store.update(sid, ocr_text="打工人")
    entry = store.get(sid)
    assert entry is not None
    assert entry["ocr_text"] == "打工人"
    assert entry["description"] == "desc"
    assert entry["usage_hint"] == "hint"


def test_format_prompt_view_includes_ocr(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "挥手", "告别时", ocr_text="拜拜")
    view = store.format_prompt_view()
    assert "图上文字：拜拜" in view
    assert f"«表情包:{sid}»" in view


def test_format_prompt_view_omits_empty_ocr(store: StickerStore) -> None:
    store.add(_JPEG_DATA, "desc", "hint")  # ocr_text 默认空
    view = store.format_prompt_view()
    assert "图上文字" not in view


def test_format_prompt_view_stable_with_ocr(store: StickerStore) -> None:
    """prompt cache 稳定性：含 ocr 的库两次渲染一致。"""
    store.add(_JPEG_DATA, "desc", "hint", ocr_text="晚安")
    assert store.format_prompt_view() == store.format_prompt_view()


# ------------------------------------------------------------------
# lookup_by_hash()
# ------------------------------------------------------------------


def test_lookup_by_hash_found(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "desc", "hint")
    found = store.lookup_by_hash(_JPEG_DATA)
    assert found == sid


def test_lookup_by_hash_not_found(store: StickerStore) -> None:
    found = store.lookup_by_hash(_JPEG_DATA)
    assert found is None


def test_lookup_by_hash_different_content(store: StickerStore) -> None:
    store.add(_JPEG_DATA, "desc", "hint")
    found = store.lookup_by_hash(_JPEG_DATA_B)
    assert found is None


# ------------------------------------------------------------------
# format_prompt_view()
# ------------------------------------------------------------------


def test_format_prompt_view_empty(store: StickerStore) -> None:
    view = store.format_prompt_view()
    assert view == "当前表情包库为空"


def test_format_prompt_view_nonempty(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "开心大笑", "搞笑对话时", source="auto")
    view = store.format_prompt_view()
    assert "当前表情包库：" in view
    assert f"«表情包:{sid}»" in view
    assert "开心大笑" in view
    assert "搞笑对话时" in view


def test_format_prompt_view_excludes_volatile_fields(store: StickerStore) -> None:
    """send_count, last_sent, created_at must NOT appear in the prompt view."""
    store.add(_JPEG_DATA, "desc", "hint")
    store.record_send(store.lookup_by_hash(_JPEG_DATA))  # type: ignore[arg-type]

    view = store.format_prompt_view()
    assert "send_count" not in view
    assert "last_sent" not in view
    assert "created_at" not in view


def test_format_prompt_view_multiple(store: StickerStore) -> None:
    store.add(_JPEG_DATA, "JPEG贴", "任何时候", source="auto")
    store.add(_PNG_DATA, "PNG贴", "悲伤时", source="auto")
    view = store.format_prompt_view()
    lines = view.splitlines()
    # header + 2 entries
    assert len(lines) == 3


# ------------------------------------------------------------------
# Index persistence across reload
# ------------------------------------------------------------------


def test_index_persists_across_reload(tmp_path) -> None:
    """Create store, add sticker, reload from same dir, verify data."""
    storage = str(tmp_path / "stickers")

    store1 = StickerStore(storage_dir=storage)
    sid, _ = store1.add(_JPEG_DATA, "持久化测试", "重启验证", source="admin")
    store1.record_send(sid)

    # Create new instance on same directory
    store2 = StickerStore(storage_dir=storage)

    entry = store2.get(sid)
    assert entry is not None
    assert entry["description"] == "持久化测试"
    assert entry["usage_hint"] == "重启验证"
    assert entry["source"] == "admin"
    assert entry["send_count"] == 1
    assert entry["last_sent"] is not None

    # File should still be accessible
    path = store2.resolve_path(sid)
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == _JPEG_DATA


def test_index_persists_removal(tmp_path) -> None:
    storage = str(tmp_path / "stickers")

    store1 = StickerStore(storage_dir=storage)
    sid, _ = store1.add(_JPEG_DATA, "remove test", "hint")
    store1.remove(sid)

    store2 = StickerStore(storage_dir=storage)
    assert store2.get(sid) is None
    assert store2.list_all() == {}


# ---------------------------------------------------------------------------
# search_by_intent (BM25 over rich descriptions)
# ---------------------------------------------------------------------------


def test_search_by_intent_matches_description(store: StickerStore) -> None:
    bye, _ = store.add(_JPEG_DATA, "挥手告别", "适合说再见、拜拜的场景", ocr_text="拜拜")
    store.add(_PNG_DATA, "生气皱眉", "不满、抗议时使用", ocr_text="哼")
    assert store.search_by_intent("告别")[:1] == [bye]


def test_search_by_intent_matches_ocr(store: StickerStore) -> None:
    night, _ = store.add(_JPEG_DATA, "月亮图案", "睡前道晚安", ocr_text="晚安")
    store.add(_PNG_DATA, "生气皱眉", "抗议时使用", ocr_text="哼")
    assert night in store.search_by_intent("晚安")


def test_search_by_intent_empty_query(store: StickerStore) -> None:
    store.add(_JPEG_DATA, "挥手告别", "再见")
    assert store.search_by_intent("") == []
    assert store.search_by_intent("   ") == []


def test_search_by_intent_empty_library(store: StickerStore) -> None:
    assert store.search_by_intent("告别") == []


def test_search_by_intent_no_match(store: StickerStore) -> None:
    store.add(_JPEG_DATA, "挥手告别", "再见", ocr_text="拜拜")
    assert store.search_by_intent("量子色动力学") == []


def test_search_by_intent_top_k(store: StickerStore) -> None:
    store.add(_JPEG_DATA, "告别甲", "再见拜拜", ocr_text="拜拜")
    store.add(_PNG_DATA, "告别乙", "再见挥手", ocr_text="再见")
    store.add(_WEBP_DATA, "告别丙", "拜拜挥手", ocr_text="拜拜")
    assert len(store.search_by_intent("告别 再见 拜拜", top_k=2)) <= 2


def test_search_reflects_updates(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "无关描述", "无关场景")
    assert store.search_by_intent("晚安") == []
    store.update(sid, ocr_text="晚安")
    assert sid in store.search_by_intent("晚安")


def test_search_reflects_removal(store: StickerStore) -> None:
    sid, _ = store.add(_JPEG_DATA, "挥手告别", "再见", ocr_text="拜拜")
    assert sid in store.search_by_intent("告别")
    store.remove(sid)
    assert store.search_by_intent("告别") == []


# ---------------------------------------------------------------------------
# Legacy index.json -> SQLite migration
# ---------------------------------------------------------------------------


def _write_legacy_index(storage_dir: Path, stickers: dict) -> None:
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "index.json").write_text(
        json.dumps({"stickers": stickers}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_migrates_legacy_index(tmp_path: Path) -> None:
    storage = tmp_path / "stickers"
    _write_legacy_index(
        storage,
        {
            "stk_aaaaaaaa": {
                "file": "stk_aaaaaaaa.png",
                "description": "旧表情A",
                "usage_hint": "场景A",
                "source": "admin",
                "send_count": 3,
                "last_sent": "2026-01-01T00:00:00+00:00",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            "stk_bbbbbbbb": {
                "file": "stk_bbbbbbbb.jpg",
                "description": "旧表情B",
                "usage_hint": "场景B",
                "ocr_text": "晚安",
                "source": "auto",
                "send_count": 0,
                "last_sent": None,
                "created_at": "2026-01-02T00:00:00+00:00",
            },
        },
    )

    store = StickerStore(storage_dir=str(storage))
    assert len(store.list_all()) == 2
    entry_a = store.get("stk_aaaaaaaa")
    assert entry_a is not None
    assert entry_a["send_count"] == 3  # volatile field preserved
    # Pre-stage-1 entry had no ocr_text key -> stays key-absent ("never
    # attempted") so the Dream OCR backfill can still pick it up.
    assert "ocr_text" not in entry_a
    entry_b = store.get("stk_bbbbbbbb")
    assert entry_b is not None
    assert entry_b["ocr_text"] == "晚安"  # present field migrated
    assert store.search_by_intent("晚安") == ["stk_bbbbbbbb"]


def test_migration_is_idempotent(tmp_path: Path) -> None:
    storage = tmp_path / "stickers"
    _write_legacy_index(
        storage,
        {
            "stk_aaaaaaaa": {
                "file": "stk_aaaaaaaa.png",
                "description": "旧表情A",
                "usage_hint": "场景A",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        },
    )
    StickerStore(storage_dir=str(storage))
    # index.json left frozen as a rollback snapshot.
    assert (storage / "index.json").exists()
    store2 = StickerStore(storage_dir=str(storage))
    assert len(store2.list_all()) == 1  # not double-imported


def test_no_migration_when_no_legacy_index(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    assert store.list_all() == {}


def test_legacy_index_not_overwritten_by_new_adds(tmp_path: Path) -> None:
    storage = tmp_path / "stickers"
    _write_legacy_index(
        storage,
        {
            "stk_aaaaaaaa": {
                "file": "stk_aaaaaaaa.png",
                "description": "旧表情A",
                "usage_hint": "场景A",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        },
    )
    store = StickerStore(storage_dir=str(storage))
    store.add(_JPEG_DATA, "新表情", "新场景")
    # The frozen JSON snapshot must not absorb post-migration writes.
    raw = json.loads((storage / "index.json").read_text(encoding="utf-8"))
    assert list(raw["stickers"].keys()) == ["stk_aaaaaaaa"]


# ---------------------------------------------------------------------------
# Cross-thread safety (silent sticker learning runs add() in to_thread workers)
# ---------------------------------------------------------------------------


async def test_add_works_from_worker_thread(tmp_path: Path) -> None:
    """add() is invoked via asyncio.to_thread during silent sticker learning;
    the SQLite connection must tolerate cross-thread use (regression: the
    JSON->SQLite migration introduced thread-affinity that broke this)."""
    import asyncio

    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sid, is_new = await asyncio.to_thread(store.add, _JPEG_DATA, "desc", "hint", "auto")
    assert is_new
    assert store.get(sid) is not None
    # Reads from the loop thread still see the worker-thread write.
    assert sid in store.list_all()
    store.close()


# ---------------------------------------------------------------------------
# Part B1: max_count capacity eviction
# ---------------------------------------------------------------------------


def _distinct_jpeg(n: int) -> bytes:
    """A valid-magic JPEG whose payload makes its SHA unique per n."""
    return b"\xff\xd8\xff\xe0" + (f"payload-{n:06d}".encode()) + b"\x00" * 16


def test_add_evicts_lowest_send_count_silent_when_full(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"), max_count=3)
    a, _ = store.add(_distinct_jpeg(1), "a", "h", source="stolen_silent_learn")
    b, _ = store.add(_distinct_jpeg(2), "b", "h", source="stolen_silent_learn")
    c, _ = store.add(_distinct_jpeg(3), "c", "h", source="stolen_silent_learn")
    # b has been used; a and c never sent -> a (oldest, send_count 0) evicted first.
    store.record_send(b)
    store.record_send(c)
    d, is_new = store.add(_distinct_jpeg(4), "d", "h", source="stolen_silent_learn")
    assert is_new
    assert store.count == 3
    assert a not in store.list_all(), "lowest send_count + oldest must be evicted"
    assert {b, c, d} <= set(store.list_all())
    store.close()


def test_add_does_not_evict_protected_sources(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"), max_count=2)
    m, _ = store.add(_distinct_jpeg(1), "m", "h", source="migrated:v1:usage_1")
    adm, _ = store.add(_distinct_jpeg(2), "adm", "h", source="admin")
    # Library full of protected stickers: adding a silent one must not delete them.
    _, is_new = store.add(_distinct_jpeg(3), "s", "h", source="stolen_silent_learn")
    assert is_new
    assert m in store.list_all() and adm in store.list_all()
    # Soft cap does not force-delete protected content, so it may exceed max_count.
    assert store.count == 3
    store.close()


def test_eviction_prefers_silent_over_protected(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"), max_count=2)
    prot, _ = store.add(_distinct_jpeg(1), "p", "h", source="admin")
    sil, _ = store.add(_distinct_jpeg(2), "s", "h", source="stolen_silent_learn")
    new, _ = store.add(_distinct_jpeg(3), "n", "h", source="stolen_silent_learn")
    assert prot in store.list_all(), "protected sticker survives"
    assert sil not in store.list_all(), "silent sticker evicted instead"
    assert new in store.list_all()
    store.close()


# ---------------------------------------------------------------------------
# Part B3: eviction_candidates (Dream cleanup feed)
# ---------------------------------------------------------------------------


def test_eviction_candidates_only_never_sent_silent(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"), max_count=100)
    sent, _ = store.add(_distinct_jpeg(1), "sent", "h", source="stolen_silent_learn")
    never, _ = store.add(_distinct_jpeg(2), "never", "h", source="stolen_silent_learn")
    protected, _ = store.add(_distinct_jpeg(3), "prot", "h", source="migrated:v1:usage_1")
    store.record_send(sent)
    ids = [c["id"] for c in store.eviction_candidates(limit=50)]
    assert never in ids
    assert sent not in ids, "sent stickers are not eviction candidates"
    assert protected not in ids, "protected sources are never candidates"
    store.close()


def test_eviction_candidates_oldest_first_and_limit(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"), max_count=100)
    ids = []
    for i in range(5):
        sid, _ = store.add(_distinct_jpeg(i), f"s{i}", "h", source="stolen_silent_learn")
        ids.append(sid)
    cands = store.eviction_candidates(limit=3)
    assert len(cands) == 3
    # Oldest created_at first; insertion order here is ascending created_at.
    assert [c["id"] for c in cands] == ids[:3]
    store.close()


# ---------------------------------------------------------------------------
# Part B2: format_prompt_view truncation + cache stability
# ---------------------------------------------------------------------------


def test_prompt_view_truncates_to_max(tmp_path: Path) -> None:
    store = StickerStore(
        storage_dir=str(tmp_path / "stickers"),
        max_count=1000,
        prompt_view_max=10,
        prompt_view_recent_slice=5,
    )
    for i in range(40):
        store.add(_distinct_jpeg(i), f"s{i}", "h", source="stolen_silent_learn")
    view = store.format_prompt_view()
    body_lines = [ln for ln in view.splitlines() if ln.startswith("«表情包:")]
    assert len(body_lines) == 10, "view must cap at prompt_view_max"
    assert "共 40 张" in view.splitlines()[0]
    store.close()


def test_prompt_view_includes_used_and_recent(tmp_path: Path) -> None:
    store = StickerStore(
        storage_dir=str(tmp_path / "stickers"),
        max_count=1000,
        prompt_view_max=6,
        prompt_view_recent_slice=2,
    )
    ids = []
    for i in range(20):
        sid, _ = store.add(_distinct_jpeg(i), f"s{i}", "h", source="stolen_silent_learn")
        ids.append(sid)
    # An old sticker that got sent must stay visible despite not being recent.
    store.record_send(ids[0])
    view = store.format_prompt_view()
    assert ids[0] in view, "used sticker stays in view"
    assert ids[-1] in view, "most recent sticker is in view"
    store.close()


def test_prompt_view_member_cache_stable_across_sends(tmp_path: Path) -> None:
    store = StickerStore(
        storage_dir=str(tmp_path / "stickers"),
        max_count=1000,
        prompt_view_max=5,
        prompt_view_recent_slice=5,
    )
    ids = [store.add(_distinct_jpeg(i), f"s{i}", "h", source="stolen_silent_learn")[0] for i in range(5)]
    first = store.format_prompt_view()
    # Recording a send must NOT churn the member set (prompt cache stability).
    store.record_send(ids[0])
    second = store.format_prompt_view()
    assert {ln for ln in first.splitlines() if ln.startswith("«表情包:")} == {
        ln for ln in second.splitlines() if ln.startswith("«表情包:")
    }
    # Adding a sticker DOES invalidate the member set.
    store.add(_distinct_jpeg(999), "new", "h", source="stolen_silent_learn")
    assert store._view_members is None
    store.close()


def test_prompt_view_empty_library(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    assert store.format_prompt_view() == "当前表情包库为空"
    store.close()

