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


def test_manifest_grants_message_and_tick_permissions() -> None:
    """The silent-steal path runs through bus.fire_on_message / fire_on_tick,
    which both gate on _has_permission(plugin, "message"|"tick").  If the
    manifest omits either, on_message/on_tick are never called and the whole
    silent capture path is dead in production while unit tests (which call the
    hooks directly) still pass.  Regression for the 2026-06-09 finding that
    permissions were ["prompt","tool","storage"] only — silent capture never
    ran since manifest v2 permission gating landed.
    """
    import json

    manifest = json.loads((Path("plugins/sticker/plugin.json")).read_text(encoding="utf-8"))
    perms = set(manifest.get("permissions", []))
    # on_message hook -> "message"; on_tick retry hook -> "tick";
    # on_pre_prompt -> "prompt"; register_tools -> "tool".
    assert "message" in perms, "on_message gated out by bus without 'message' permission"
    assert "tick" in perms, "on_tick retry gated out by bus without 'tick' permission"
    assert "prompt" in perms
    assert "tool" in perms


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
    """allow_speaking=True (active group, bot is speaking) should skip silent capture."""
    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345))
    # Even with a sticker-like segment, the speaking turn should skip
    seg = {"type": "image", "data": {"file": "abc", "url": "x", "sub_type": 1}}

    add_called = MagicMock()
    monkeypatch.setattr(sticker_store, "add", add_called)

    ctx = _msg_ctx(allow_speaking=True, presence_mode="active", segments=[seg])
    consumed = await plugin.on_message(ctx)

    assert consumed is False
    add_called.assert_not_called()


async def test_active_mode_quiet_captures_sticker(
    sticker_store: StickerStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """active group in a quiet turn (allow_speaking=False) now captures stickers too."""
    plugin = _make_plugin(sticker_store, None)

    cached = tmp_path / "active_cached.jpg"
    cached.write_bytes(_JPEG_DATA)

    async def fake_cache(_self, _bot, _seg, *, file_id):
        return cached

    monkeypatch.setattr(StickerPlugin, "_ensure_segment_cached", fake_cache)

    seg = {
        "type": "image",
        "data": {
            "file": "stk_active.jpg",
            "url": "http://example.com/a.jpg",
            "sub_type": 1,
            "summary": "[群友表情]",
        },
    }
    ctx = _msg_ctx(allow_speaking=False, presence_mode="active", segments=[seg])

    before = set(sticker_store._index.keys())
    consumed = await plugin.on_message(ctx)
    after = set(sticker_store._index.keys())

    assert consumed is False
    new_ids = after - before
    assert len(new_ids) == 1, "active quiet turn should capture the sticker"
    assert sticker_store._index[next(iter(new_ids))]["source"] == "stolen_silent_learn"


async def test_off_mode_does_not_capture(
    sticker_store: StickerStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """presence_mode=off must never capture, even in a quiet turn."""
    plugin = _make_plugin(sticker_store, None)
    seg = {"type": "image", "data": {"file": "abc", "url": "x", "sub_type": 1, "summary": "[x]"}}

    add_called = MagicMock()
    monkeypatch.setattr(sticker_store, "add", add_called)

    ctx = _msg_ctx(allow_speaking=False, presence_mode="off", segments=[seg])
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


async def test_ensure_cached_prefers_llm_client_session(
    sticker_store: StickerStore,
) -> None:
    """_ensure_segment_cached must download via llm_client._session.

    Regression for the 2026-06-09 finding: the adapter session
    (bot.adapter.session) is a functools.partial wrapper, not a real
    aiohttp ClientSession, so image_cache.save() silently returned None and
    every silent steal failed.  The proven path (the one chat/plugin.py uses)
    is ctx.llm_client._session — it must be tried first.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    plugin = _make_plugin(sticker_store, None)

    real_session = object()  # the llm_client aiohttp session
    partial_session = object()  # adapter's functools.partial-like wrapper

    plugin._ctx = SimpleNamespace(llm_client=SimpleNamespace(_session=real_session))
    plugin._image_cache = MagicMock()
    plugin._image_cache.save = AsyncMock(
        return_value={"type": "image_ref", "path": "/tmp/x.jpg", "media_type": "image/jpeg"},
    )

    bot = SimpleNamespace(adapter=SimpleNamespace(session=partial_session))
    seg = {"type": "image", "data": {"file": "abc.jpg", "url": "https://x/y.jpg"}}

    await plugin._ensure_segment_cached(bot, seg, file_id="abc.jpg")

    # The download must use the llm_client session, never the adapter wrapper.
    plugin._image_cache.save.assert_awaited_once()
    used_session = plugin._image_cache.save.call_args.args[0]
    assert used_session is real_session, "must prefer llm_client._session over adapter.session"


# ---------------------------------------------------------------------------
# Part A: recurrence quality gate
# ---------------------------------------------------------------------------


def test_recurrence_gate_requires_min_occurrences() -> None:
    from plugins.sticker.plugin import _RecurrenceGate

    gate = _RecurrenceGate(min_occurrences=3, window_seconds=1000.0)
    assert gate.should_learn("f1", now=0.0) is False  # 1st sighting
    assert gate.should_learn("f1", now=1.0) is False  # 2nd
    assert gate.should_learn("f1", now=2.0) is True   # 3rd -> crosses gate


def test_recurrence_gate_window_prunes_stale_sightings() -> None:
    from plugins.sticker.plugin import _RecurrenceGate

    gate = _RecurrenceGate(min_occurrences=3, window_seconds=100.0)
    assert gate.should_learn("f1", now=0.0) is False
    assert gate.should_learn("f1", now=50.0) is False
    # 3rd sighting at 200 is outside the 100s window of the first two ->
    # both pruned, only this one remains, so the gate stays closed.
    assert gate.should_learn("f1", now=200.0) is False
    assert gate.should_learn("f1", now=250.0) is False  # 2 within window
    assert gate.should_learn("f1", now=290.0) is True   # 3 within [190, 290]


def test_recurrence_gate_resolved_stops_counting() -> None:
    from plugins.sticker.plugin import _RecurrenceGate

    gate = _RecurrenceGate(min_occurrences=2, window_seconds=1000.0)
    assert gate.should_learn("f1", now=0.0) is False
    assert gate.should_learn("f1", now=1.0) is True
    gate.mark_resolved("f1")
    # Resolved within window: further sightings do not re-trigger.
    assert gate.should_learn("f1", now=2.0) is False
    assert gate.should_learn("f1", now=3.0) is False


def test_recurrence_gate_min_one_passes_immediately() -> None:
    from plugins.sticker.plugin import _RecurrenceGate

    gate = _RecurrenceGate(min_occurrences=1, window_seconds=1000.0)
    assert gate.should_learn("f1", now=0.0) is True


def test_recurrence_gate_seen_lru_eviction_bounded() -> None:
    from plugins.sticker.plugin import _MAX_TRACKED_FILE_IDS, _RecurrenceGate

    gate = _RecurrenceGate(min_occurrences=5, window_seconds=10_000.0)
    for i in range(_MAX_TRACKED_FILE_IDS + 200):
        gate.should_learn(f"f{i}", now=float(i))
    assert len(gate._seen) <= _MAX_TRACKED_FILE_IDS


async def test_on_message_gate_blocks_one_off_sticker(
    sticker_store: StickerStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sticker seen fewer than min_occurrences times is never downloaded/added."""
    plugin = _make_plugin(sticker_store, _silent_learn_group_config(12345))
    from plugins.sticker.plugin import _RecurrenceGate
    plugin._recurrence_gate = _RecurrenceGate(min_occurrences=3, window_seconds=1000.0)

    calls = {"n": 0}

    async def fake_cache(self, bot, seg, *, file_id):
        calls["n"] += 1
        return None

    monkeypatch.setattr(StickerPlugin, "_ensure_segment_cached", fake_cache)

    seg = {"type": "image", "data": {"file": "oneoff.jpg", "sub_type": "1", "url": "x"}}
    before = len(sticker_store._index)
    # Two sightings: below the gate, must NOT even attempt download.
    await plugin.on_message(_msg_ctx(segments=[seg]))
    await plugin.on_message(_msg_ctx(segments=[seg]))
    assert calls["n"] == 0, "below threshold must not download"
    assert len(sticker_store._index) == before


