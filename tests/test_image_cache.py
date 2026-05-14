"""Image cache module tests."""

import base64
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from services.media.image_cache import ImageCache

try:
    import pyvips  # type: ignore[import-untyped]  # noqa: F401
    _HAS_LIBVIPS = True
except (ImportError, OSError):
    _HAS_LIBVIPS = False

_requires_libvips = pytest.mark.skipif(
    not _HAS_LIBVIPS,
    reason="libvips not installed (brew install vips); image cache tests need pyvips",
)


@pytest.fixture
def cache(tmp_path: Path) -> ImageCache:
    return ImageCache(cache_dir=tmp_path, max_dimension=256)


def _mock_session(resp: AsyncMock) -> Mock:
    """Create a mock aiohttp session where .get() returns an async context manager.

    aiohttp's session.get() is a sync call returning a _RequestContextManager,
    which is an async context manager. We replicate that here.
    """
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = Mock()
    session.get = Mock(return_value=cm)
    return session


def _write_test_image(path: Path) -> None:
    path.write_bytes(_JPEG_BYTES)


_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 128 + b"jpeg-payload-a"
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128 + b"png-payload-a"
_WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 128 + b"webp-payload-a"


class TestSaveAndLoad:
    @_requires_libvips
    async def test_save_downloads_and_caches(self, cache: ImageCache, tmp_path: Path) -> None:
        """save() should download image, resize, and store to disk."""
        buf = _JPEG_BYTES

        mock_resp = AsyncMock()
        mock_resp.read = AsyncMock(return_value=buf)
        mock_resp.status = 200

        session = _mock_session(mock_resp)

        ref = await cache.save(session, url="http://example.com/img.jpg", file_id="abc123def456")

        assert ref is not None
        assert ref["path"].endswith(".jpg")
        assert ref["media_type"] == "image/jpeg"
        assert Path(ref["path"]).exists()

        # Verify two-level directory structure
        assert "/ab/" in ref["path"] or "\\ab\\" in ref["path"]

    @_requires_libvips
    async def test_save_transparent_png_preserves_alpha(self, cache: ImageCache) -> None:
        buf = _PNG_BYTES

        mock_resp = AsyncMock()
        mock_resp.read = AsyncMock(return_value=buf)
        mock_resp.status = 200

        session = _mock_session(mock_resp)

        ref = await cache.save(session, url="http://example.com/alpha.png", file_id="alpha123")

        assert ref is not None
        assert ref["path"].endswith(".png")
        assert ref["media_type"] == "image/png"
        assert Path(ref["path"]).exists()

    @_requires_libvips
    async def test_save_transparent_webp_preserves_alpha(self, cache: ImageCache) -> None:
        buf = _WEBP_BYTES

        mock_resp = AsyncMock()
        mock_resp.read = AsyncMock(return_value=buf)
        mock_resp.status = 200

        session = _mock_session(mock_resp)

        ref = await cache.save(session, url="http://example.com/alpha.webp", file_id="webp123")

        assert ref is not None
        assert ref["path"].endswith(".png")
        assert ref["media_type"] == "image/png"
        assert Path(ref["path"]).exists()

    async def test_save_returns_none_on_download_failure(self, cache: ImageCache) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 404

        session = _mock_session(mock_resp)

        ref = await cache.save(session, url="http://example.com/missing.jpg", file_id="deadbeef")
        assert ref is None

    async def test_save_skips_if_cached(self, cache: ImageCache, tmp_path: Path) -> None:
        """If file_id already exists on disk, return existing ref without downloading."""
        subdir = tmp_path / "ab"
        subdir.mkdir()
        cached_file = subdir / "abc123.jpg"
        _write_test_image(cached_file)

        mock_session = AsyncMock()
        ref = await cache.save(mock_session, url="http://example.com/img.jpg", file_id="abc123")

        assert ref is not None
        mock_session.get.assert_not_called()

    async def test_load_as_base64(self, cache: ImageCache, tmp_path: Path) -> None:
        subdir = tmp_path / "ab"
        subdir.mkdir()
        img_path = subdir / "abc123.jpg"
        _write_test_image(img_path)

        ref = {"type": "image_ref", "path": str(img_path), "media_type": "image/jpeg"}
        block = await cache.load_as_base64(ref)

        assert block is not None
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "image/jpeg"
        assert len(block["source"]["data"]) > 0

    async def test_load_as_base64_png_media_type(self, cache: ImageCache, tmp_path: Path) -> None:
        subdir = tmp_path / "ab"
        subdir.mkdir()
        img_path = subdir / "abc123.png"
        _write_test_image(img_path)

        ref = {"type": "image_ref", "path": str(img_path), "media_type": "image/png"}
        block = await cache.load_as_base64(ref)

        assert block is not None
        assert block["source"]["media_type"] == "image/png"

    async def test_load_as_base64_missing_file(self, cache: ImageCache) -> None:
        ref = {"type": "image_ref", "path": "/nonexistent/file.jpg", "media_type": "image/jpeg"}
        block = await cache.load_as_base64(ref)
        assert block is None

    @_requires_libvips
    async def test_resize_respects_max_dimension(self, cache: ImageCache, tmp_path: Path) -> None:
        """Images larger than max_dimension should be scaled down."""
        import pyvips  # type: ignore[import-untyped]

        img: Any = pyvips.Image.black(2000, 1000).copy(interpretation="srgb")  # pyright: ignore[reportOptionalMemberAccess,reportCallIssue]
        buf = img.jpegsave_buffer(Q=80)

        mock_resp = AsyncMock()
        mock_resp.read = AsyncMock(return_value=buf)
        mock_resp.status = 200

        session = _mock_session(mock_resp)

        ref = await cache.save(session, url="http://example.com/big.jpg", file_id="bigimg001")
        assert ref is not None

        saved: Any = pyvips.Image.new_from_file(ref["path"])
        assert max(saved.width, saved.height) <= 256


class TestCleanup:
    async def test_cleanup_removes_old_files(self, cache: ImageCache, tmp_path: Path) -> None:
        subdir = tmp_path / "ab"
        subdir.mkdir()

        old_file = subdir / "old.jpg"
        new_file = subdir / "new.jpg"
        _write_test_image(old_file)
        _write_test_image(new_file)

        old_mtime = time.time() - 3600 * 25
        import os

        os.utime(old_file, (old_mtime, old_mtime))

        await cache.cleanup(max_age=timedelta(hours=24))

        assert not old_file.exists()
        assert new_file.exists()

    async def test_cleanup_removes_empty_subdirs(self, cache: ImageCache, tmp_path: Path) -> None:
        subdir = tmp_path / "cd"
        subdir.mkdir()
        old_file = subdir / "only.jpg"
        _write_test_image(old_file)

        old_mtime = time.time() - 3600 * 25
        import os

        os.utime(old_file, (old_mtime, old_mtime))

        await cache.cleanup(max_age=timedelta(hours=24))

        assert not old_file.exists()
        assert not subdir.exists()
