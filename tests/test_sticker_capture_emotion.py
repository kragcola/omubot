from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from kernel.config import GroupConfig, GroupOverride
from kernel.types import MessageContext
from plugins.history_loader import _extract_content
from plugins.sticker import StickerPlugin
from services.media.image_cache import ImageCache
from services.media.sticker_capture import DEFAULT_STICKER_USAGE_HINT, emit_emotion_tag
from services.media.sticker_store import StickerStore

_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x11" * 64 + b"sticker-emotion"


def _msg_ctx(*, segments: list[dict]) -> MessageContext:
    return MessageContext(
        session_id="group_12345",
        group_id="12345",
        user_id="888",
        content="",
        raw_message={"segments": segments},
        message_id=42,
        bot=MagicMock(),
        allow_speaking=False,
        group_presence_mode="silent_learn",
    )


def _sticker_group_config(group_id: int = 12345) -> GroupConfig:
    return GroupConfig(
        allowed_groups=[group_id],
        overrides={group_id: GroupOverride(presence_mode="silent_learn")},
    )


def _make_plugin(store: StickerStore) -> StickerPlugin:
    plugin = StickerPlugin()
    plugin._sticker_store = store
    plugin._group_config = _sticker_group_config()
    plugin._vision_client = object()
    plugin._image_cache = MagicMock()
    return plugin


def _mock_session(response_data: bytes) -> AsyncMock:
    resp = AsyncMock()
    resp.status = 200
    resp.read = AsyncMock(return_value=response_data)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)

    session = AsyncMock()
    session.get = MagicMock(return_value=cm)
    return session


async def test_emit_emotion_tag_updates_store_with_normalized_hint(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sticker_id, _ = store.add(_JPEG_DATA, "测试表情", DEFAULT_STICKER_USAGE_HINT, source="auto")
    vision = MagicMock()
    vision.describe_image = AsyncMock(return_value="  开心接梗时发。\n")

    tag = await emit_emotion_tag(store, sticker_id, image_data=_JPEG_DATA, vision_client=vision)

    assert tag == "开心接梗时发"
    assert store.get(sticker_id)["usage_hint"] == "开心接梗时发"  # type: ignore[index]


async def test_emit_emotion_tag_keeps_fallback_without_vision_client(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sticker_id, _ = store.add(_JPEG_DATA, "测试表情", DEFAULT_STICKER_USAGE_HINT, source="auto")

    tag = await emit_emotion_tag(store, sticker_id, image_data=_JPEG_DATA, vision_client=None)

    assert tag == DEFAULT_STICKER_USAGE_HINT
    assert store.get(sticker_id)["usage_hint"] == DEFAULT_STICKER_USAGE_HINT  # type: ignore[index]


async def test_emit_emotion_tag_does_not_override_existing_non_fallback_by_default(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sticker_id, _ = store.add(_JPEG_DATA, "测试表情", "已经很好用", source="auto")
    vision = MagicMock()
    vision.describe_image = AsyncMock(return_value="委屈求安慰时发")

    tag = await emit_emotion_tag(store, sticker_id, image_data=_JPEG_DATA, vision_client=vision)

    assert tag == "已经很好用"
    vision.describe_image.assert_not_awaited()


async def test_emit_emotion_tag_overwrite_true_replaces_existing_hint(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sticker_id, _ = store.add(_JPEG_DATA, "测试表情", "已经很好用", source="auto")
    vision = MagicMock()
    vision.describe_image = AsyncMock(return_value="委屈求安慰时发")

    tag = await emit_emotion_tag(
        store,
        sticker_id,
        image_data=_JPEG_DATA,
        vision_client=vision,
        overwrite=True,
    )

    assert tag == "委屈求安慰时发"
    assert store.get(sticker_id)["usage_hint"] == "委屈求安慰时发"  # type: ignore[index]


async def test_auto_learn_paths_emit_emotion_tag(tmp_path: Path, monkeypatch) -> None:
    sticker_store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    plugin = _make_plugin(sticker_store)
    cached = tmp_path / "silent_learn.jpg"
    cached.write_bytes(_JPEG_DATA)

    async def fake_cache(_self, _bot, _seg, *, file_id):
        return cached

    monkeypatch.setattr(StickerPlugin, "_ensure_segment_cached", fake_cache)
    seg = {
        "type": "image",
        "data": {"file": "stk_abc.jpg", "url": "http://example.com/x.jpg", "sub_type": 1, "summary": "[群友表情]"},
    }
    with patch("plugins.sticker.plugin.emit_emotion_tag", new_callable=AsyncMock) as sticker_emit:
        consumed = await plugin.on_message(_msg_ctx(segments=[seg]))
    assert consumed is False
    sticker_emit.assert_awaited_once()

    image_cache = ImageCache(cache_dir=tmp_path / "image_cache")

    def fake_process(data: bytes, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"type": "image_ref", "path": str(path), "media_type": "image/jpeg"}

    image_cache._process_and_save = fake_process  # type: ignore[method-assign]
    with patch("plugins.history_loader.plugin.emit_emotion_tag", new_callable=AsyncMock) as history_emit:
        await _extract_content(
            [{
                "type": "image",
                "data": {
                    "url": "http://example.com/new.jpg",
                    "file": "img123",
                    "sub_type": 1,
                    "summary": "[新表情]",
                },
            }],
            _mock_session(b"\xff\xd8\xff\xe0" + b"\x22" * 64 + b"history-sticker"),
            image_cache,
            sticker_store,
            vision_client=object(),
            learn_new_stickers=True,
        )
    history_emit.assert_awaited_once()
