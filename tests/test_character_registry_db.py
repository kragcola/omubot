from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from services.media.character_recognizer import CharacterRecognizer
from services.media.character_registry_db import CharacterRegistryDB
from services.media.recognition_cache import RecognitionCache


class _StubRecognizer(CharacterRecognizer):
    calls: int = 0

    async def _request_identify(  # type: ignore[override]
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> dict[str, object] | None:
        del image_data, media_type
        type(self).calls += 1
        return {
            "matched": True,
            "character_id": "emu",
            "character_name": "remote-name",
            "difference": 0.02,
            "threshold": 0.18,
            "source": "ccip-sidecar",
        }


@pytest.mark.asyncio
async def test_registry_scan_sync_and_admin_edit_survives_resync(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    pack = packs / "p.charpack"
    pack.mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps({"characters": [{"character_id": "emu", "name": "凤笑梦", "relation": "self"}]}),
        encoding="utf-8",
    )
    db = CharacterRegistryDB(str(tmp_path / "c.db"))
    await db.init()
    try:
        r1 = await db.scan_and_sync(str(packs))
        assert r1 == {"packs": 1, "inserted": 1, "skipped": 1} or r1["inserted"] == 1
        # admin edits relation; re-sync must NOT overwrite it
        assert await db.update("emu", relation="friend") is True
        await db.scan_and_sync(str(packs))
        row = await db.get("emu")
        assert row is not None and row["relation"] == "friend"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_registry_scan_sync_inherits_relation_default(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    pack = packs / "series.charpack"
    pack.mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps({
            "relation_default": "friend",
            "characters": [{"character_id": "emu", "name": "凤笑梦"}],
        }),
        encoding="utf-8",
    )
    db = CharacterRegistryDB(str(tmp_path / "c.db"))
    await db.init()
    try:
        await db.scan_and_sync(str(packs))
        row = await db.get("emu")
        assert row is not None
        assert row["relation"] == "friend"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_recognition_cache_short_circuits_sidecar(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    pack = packs / "series.charpack"
    pack.mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps({
            "work": "中V",
            "characters": [{
                "character_id": "emu",
                "name": "星尘",
                "relation": "known",
                "context_label": "中V / 五维介质",
            }],
        }),
        encoding="utf-8",
    )
    cache = RecognitionCache(str(tmp_path / "c.db"))
    await cache.init()
    try:
        rec = _StubRecognizer(
            base_url="http://127.0.0.1:8620",
            packs_dir=packs,
            recognition_cache=cache,
            multi_char_enabled=False,  # test single-char L2 cache path
        )
        _StubRecognizer.calls = 0
        r1_list = await rec.identify(b"img-bytes")
        assert len(r1_list) == 1
        r1 = r1_list[0]
        assert r1.character_id == "emu"
        assert r1.work == "中V"
        assert r1.context_label == "中V / 五维介质"
        assert _StubRecognizer.calls == 1
        # second identify of same bytes → served from L2 cache, no sidecar call
        r2_list = await rec.identify(b"img-bytes")
        assert len(r2_list) == 1
        r2 = r2_list[0]
        assert r2.cache_hit is True
        assert r2.work == "中V"
        assert r2.context_label == "中V / 五维介质"
        assert _StubRecognizer.calls == 1
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_recognition_cache_hit_enriches_legacy_coarse_work_from_manifest(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    pack = packs / "series.charpack"
    pack.mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps({
            "work": "中V",
            "characters": [{
                "character_id": "emu",
                "name": "星尘",
                "relation": "known",
                "context_label": "中V / 五维介质",
            }],
        }),
        encoding="utf-8",
    )
    cache = RecognitionCache(str(tmp_path / "c.db"))
    await cache.init()
    try:
        image_data = b"cached-image"
        await cache.put(
            hashlib.sha256(image_data).hexdigest(),
            character_id="emu",
            character_name="星尘",
            relation="known",
            work="中V",
            context_label=None,
        )
        rec = _StubRecognizer(
            base_url="http://127.0.0.1:8620",
            packs_dir=packs,
            recognition_cache=cache,
            multi_char_enabled=False,
        )
        _StubRecognizer.calls = 0
        result = await rec.identify(image_data)

        assert len(result) == 1
        assert result[0].cache_hit is True
        assert result[0].work == "中V"
        assert result[0].context_label == "中V / 五维介质"
        assert _StubRecognizer.calls == 0
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_registry_db_cancel_path_leaves_no_partial_row(tmp_path: Path) -> None:
    """D2: cancelling an update mid-flight must not leave a half-written row."""
    db = CharacterRegistryDB(str(tmp_path / "c.db"))
    await db.init()
    try:
        packs = tmp_path / "packs"
        pack = packs / "p.charpack"
        pack.mkdir(parents=True)
        (pack / "manifest.json").write_text(
            json.dumps({"characters": [{"character_id": "emu", "name": "E", "relation": "known"}]}),
            encoding="utf-8",
        )
        await db.scan_and_sync(str(packs))

        async def slow_update() -> None:
            await db.update("emu", relation="friend")

        task = asyncio.create_task(slow_update())
        await asyncio.sleep(0)  # let it start
        task.cancel()
        with pytest.raises((asyncio.CancelledError,)):
            await task
        # DB must still be readable and relation is either old or fully new — never corrupt
        row = await db.get("emu")
        assert row is not None
        assert row["relation"] in ("known", "friend")
    finally:
        await db.close()
