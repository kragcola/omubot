"""Regression tests for StickerPlugin.on_message silent-learn capture.

Verifies the silent-steal path that was lost in the 2026-05-21 recovery
(commit 3477163) and restored on 2026-05-24:
  - active mode (allow_speaking=True) does NOT trigger capture
  - silent_learn mode WITH sticker-like image triggers capture
  - silent_learn + sticker_mode=off does NOT trigger capture
  - non-sticker image segments are ignored
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kernel.config import GroupConfig, GroupOverride
from kernel.types import MessageContext
from plugins.sticker import StickerPlugin
from services.media.sticker_store import StickerStore

_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"silent-steal-test"


def _msg_ctx(
    *,
    group_id: str = "12345",
    allow_speaking: bool = False,
    presence_mode: str = "silent_learn",
    segments: list[dict] | None = None,
) -> MessageContext:
    raw_message = {"segments": segments or []}
    return MessageContext(
        session_id=f"group_{group_id}",
        group_id=group_id,
        user_id="888",
        content="",
        raw_message=raw_message,
        message_id=42,
        bot=MagicMock(),
        allow_speaking=allow_speaking,
        group_presence_mode=presence_mode,
    )


def _make_plugin(sticker_store: StickerStore, group_cfg: GroupConfig | None) -> StickerPlugin:
    plugin = StickerPlugin()
    plugin._sticker_store = sticker_store
    plugin._group_config = group_cfg
    plugin._image_cache = MagicMock()
    return plugin


@pytest.fixture
def sticker_store(tmp_path: Path) -> StickerStore:
    return StickerStore(storage_dir=str(tmp_path / "stickers"))


def _silent_learn_group_config(group_id: int, sticker_mode: str = "inherit") -> GroupConfig:
    cfg = GroupConfig(
        allowed_groups=[group_id],
        overrides={
            group_id: GroupOverride(
                presence_mode="silent_learn",
                sticker_mode=sticker_mode,  # type: ignore[arg-type]
            ),
        },
    )
    return cfg


async def test_silent_safe_flag_is_set() -> None:
    """silent_safe must be True so message bus invokes on_message in silent_learn mode."""
    plugin = StickerPlugin()
    assert plugin.silent_safe is True


async def test_active_mode_does_not_capture(
    sticker_store: StickerStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allow_speaking=True (active group) should skip silent-learn path entirely."""
    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345))
    # Even with a sticker-like segment, active mode should skip
    seg = {"type": "image", "data": {"file": "abc", "url": "x", "sub_type": 1}}

    add_called = MagicMock()
    monkeypatch.setattr(sticker_store, "add", add_called)

    ctx = _msg_ctx(allow_speaking=True, presence_mode="active", segments=[seg])
    consumed = await plugin.on_message(ctx)

    assert consumed is False
    add_called.assert_not_called()


async def test_silent_learn_captures_sticker_segment(
    sticker_store: StickerStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """silent_learn + sticker-like segment writes to store with stolen_silent_learn source."""
    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345))

    # Write a fake cached image file and route _ensure_segment_cached to it
    cached = tmp_path / "fake_cached.jpg"
    cached.write_bytes(_JPEG_DATA)

    async def fake_cache(_self, _bot, _seg, *, file_id):
        return cached

    monkeypatch.setattr(StickerPlugin, "_ensure_segment_cached", fake_cache)

    seg = {
        "type": "image",
        "data": {
            "file": "stk_abc.jpg",
            "url": "http://example.com/x.jpg",
            "sub_type": 1,  # sticker
            "summary": "[群友表情]",
        },
    }
    ctx = _msg_ctx(segments=[seg])

    before = set(sticker_store._index.keys())
    consumed = await plugin.on_message(ctx)
    after = set(sticker_store._index.keys())

    assert consumed is False
    new_ids = after - before
    assert len(new_ids) == 1, "Exactly one sticker should have been captured"
    new_id = next(iter(new_ids))
    assert sticker_store._index[new_id]["source"] == "stolen_silent_learn"


async def test_silent_learn_with_sticker_mode_off_skips(
    sticker_store: StickerStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """sticker_mode=off must veto capture even in silent_learn mode."""
    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345, sticker_mode="off"))

    cached = tmp_path / "fake_cached.jpg"
    cached.write_bytes(_JPEG_DATA)

    async def fake_cache(_self, _bot, _seg, *, file_id):
        return cached

    monkeypatch.setattr(StickerPlugin, "_ensure_segment_cached", fake_cache)
    add_called = MagicMock()
    monkeypatch.setattr(sticker_store, "add", add_called)

    seg = {
        "type": "image",
        "data": {"file": "stk_abc.jpg", "url": "x", "sub_type": 1, "summary": "[xx]"},
    }
    ctx = _msg_ctx(segments=[seg])

    consumed = await plugin.on_message(ctx)

    assert consumed is False
    add_called.assert_not_called()


async def test_silent_learn_non_sticker_image_skipped(
    sticker_store: StickerStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regular photo (no sub_type / summary) is not captured."""
    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345))

    add_called = MagicMock()
    monkeypatch.setattr(sticker_store, "add", add_called)

    seg = {"type": "image", "data": {"file": "photo.jpg", "url": "http://x"}}
    ctx = _msg_ctx(segments=[seg])

    consumed = await plugin.on_message(ctx)

    assert consumed is False
    add_called.assert_not_called()


async def test_silent_learn_no_segments_returns_quickly(
    sticker_store: StickerStore,
) -> None:
    """Empty segments must not raise."""
    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345))
    ctx = _msg_ctx(segments=[])
    consumed = await plugin.on_message(ctx)
    assert consumed is False


async def test_silent_learn_caps_images_per_message(
    sticker_store: StickerStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_SILENT_STEAL_MAX_IMAGES caps captures to 2 even if message has 3 stickers."""
    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345))

    # Three distinct sticker bodies
    cached_paths = []
    for i in range(3):
        p = tmp_path / f"fake_{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 64 + f"steal-{i}".encode())
        cached_paths.append(p)

    iter_paths = iter(cached_paths)

    async def fake_cache(_self, _bot, _seg, *, file_id):
        return next(iter_paths)

    monkeypatch.setattr(StickerPlugin, "_ensure_segment_cached", fake_cache)

    segs = [
        {
            "type": "image",
            "data": {"file": f"stk_{i}", "url": "x", "sub_type": 1, "summary": f"[{i}]"},
        }
        for i in range(3)
    ]
    ctx = _msg_ctx(segments=segs)

    before = len(sticker_store._index)
    await plugin.on_message(ctx)
    after = len(sticker_store._index)

    assert after - before == 2, "Cap must be 2 even when 3 stickers are present"


async def test_on_tick_handles_no_pending_retries(
    sticker_store: StickerStore,
) -> None:
    """on_tick with empty queue is a no-op."""
    from kernel.types import PluginContext

    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345))
    await plugin.on_tick(PluginContext())  # must not raise


async def test_queue_retry_drops_oldest_at_cap(
    sticker_store: StickerStore,
) -> None:
    """At _MAX_PENDING_RETRIES, the oldest retry is evicted."""
    from plugins.sticker.plugin import _MAX_PENDING_RETRIES

    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345))

    for i in range(_MAX_PENDING_RETRIES + 5):
        seg = {"type": "image", "data": {"file": f"file_{i}", "url": "x"}}
        ctx = _msg_ctx(group_id=str(i + 100000), segments=[seg])
        plugin._queue_retry(ctx, seg, file_id=f"file_{i}")

    assert len(plugin._pending_retries) == _MAX_PENDING_RETRIES
    assert len(plugin._pending_retry_keys) == _MAX_PENDING_RETRIES
