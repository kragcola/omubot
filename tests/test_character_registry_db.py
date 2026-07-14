from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from services.media.character_recognizer import CharacterRecognizer
from services.media.character_registry_db import CharacterRegistryDB
from services.media.recognition_cache import RecognitionCache
from services.storage import connect_sqlite


class _FailingSchemaConnection:
    def __init__(
        self,
        connection: Any,
        *,
        fail_on: str,
        error_factory: Any,
    ) -> None:
        self._connection = connection
        self._fail_on = fail_on
        self._error_factory = error_factory

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        if self._fail_on == "execute":
            raise self._error_factory()
        return await self._connection.execute(*args, **kwargs)

    async def executescript(self, *args: Any, **kwargs: Any) -> Any:
        if self._fail_on == "execute":
            raise self._error_factory()
        return await self._connection.executescript(*args, **kwargs)

    async def commit(self) -> None:
        if self._fail_on == "commit":
            raise self._error_factory()
        await self._connection.commit()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


async def _connection_is_closed(connection: Any) -> bool:
    try:
        cursor = await connection.execute("SELECT 1")
    except ValueError as exc:
        return "no active connection" in str(exc)
    await cursor.close()
    return False


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


@pytest.mark.parametrize(
    ("connect_target", "store_type"),
    [
        (
            "services.media.character_registry_db.connect_sqlite",
            CharacterRegistryDB,
        ),
        (
            "services.media.recognition_cache.connect_sqlite",
            RecognitionCache,
        ),
    ],
)
@pytest.mark.parametrize(
    ("fail_on", "error_factory", "expected_error"),
    [
        (
            "execute",
            lambda: RuntimeError("schema execution failed"),
            RuntimeError,
        ),
        ("commit", asyncio.CancelledError, asyncio.CancelledError),
    ],
)
@pytest.mark.asyncio
async def test_init_failure_closes_connected_db_and_clears_store_state(
    tmp_path: Path,
    connect_target: str,
    store_type: type[CharacterRegistryDB] | type[RecognitionCache],
    fail_on: str,
    error_factory: Any,
    expected_error: type[BaseException],
) -> None:
    """Schema failure/cancellation must not leave a live or retained connection."""
    real_connections: list[Any] = []

    async def failing_connect(*args: Any, **kwargs: Any) -> _FailingSchemaConnection:
        connection = await connect_sqlite(*args, **kwargs)
        real_connections.append(connection)
        return _FailingSchemaConnection(
            connection,
            fail_on=fail_on,
            error_factory=error_factory,
        )

    store = store_type(str(tmp_path / f"{store_type.__name__}-{fail_on}.db"))
    with (
        patch(connect_target, new=failing_connect),
        pytest.raises(expected_error),
    ):
        await store.init()

    assert len(real_connections) == 1, "connect_sqlite must succeed before schema failure"
    connection = real_connections[0]
    connection_closed = await _connection_is_closed(connection)
    try:
        assert store._db is None
        assert connection_closed
    finally:
        if not connection_closed:
            await connection.close()


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
