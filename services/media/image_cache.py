"""Disk-based image cache: download, resize, store, load, cleanup.

Storage layout uses two-level hash directories (first 2 chars of file_id)
to prevent single-directory I/O degradation:

    storage/image_cache/ab/abc123def456.jpg
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from services.memory.types import ImageRefBlock

_L = logger.bind(channel="debug")

_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=15)
_MAX_CONCURRENT_DOWNLOADS = 8


class ImageCache:
    def __init__(self, cache_dir: Path | str, max_dimension: int = 768) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_dim = max_dimension
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT_DOWNLOADS)

    def _path_for(self, file_id: str) -> Path:
        """Return the expected cache path for a given file_id (legacy default)."""
        bucket = file_id[:2]
        return self._dir / bucket / f"{file_id}.jpg"

    def _find_cached(self, file_id: str) -> Path | None:
        """Find any cached file for *file_id*, regardless of extension."""
        bucket = self._dir / file_id[:2]
        if not bucket.exists():
            return None
        for f in bucket.iterdir():
            if f.is_file() and f.stem == file_id:
                return f
        return None

    async def save(
        self,
        session: aiohttp.ClientSession,
        url: str,
        file_id: str,
    ) -> ImageRefBlock | None:
        """Download image, resize, cache to disk. Returns None on failure.

        If file_id already exists on disk, returns existing ref (cache hit).
        """
        if len(file_id) < 2:
            _L.warning("image file_id too short | file_id={!r}", file_id)
            return None

        path = self._path_for(file_id)

        # Cache hit — file already downloaded (check all extensions)
        cached = self._find_cached(file_id)
        if cached is not None:
            _L.debug("image cache hit | file_id={}", file_id)
            media_type = self._media_type_for_suffix(cached.suffix)
            return ImageRefBlock(type="image_ref", path=str(cached), media_type=media_type)

        async with self._sem:
            t0 = time.perf_counter()
            try:
                async with session.get(url, timeout=_DOWNLOAD_TIMEOUT) as resp:
                    if resp.status != 200:
                        _L.warning("image download failed | url={} status={}", url, resp.status)
                        return None
                    data = await resp.read()
            except Exception:
                _L.warning("image download error | url={}", url, exc_info=True)
                return None
            dl_ms = (time.perf_counter() - t0) * 1000

            t1 = time.perf_counter()
            try:
                ref = await asyncio.to_thread(self._process_and_save, data, path)
            except Exception:
                _L.warning("image processing error | file_id={}", file_id, exc_info=True)
                return None
            proc_ms = (time.perf_counter() - t1) * 1000

            _L.debug(
                "image save | file_id={} size={}KB download={:.0f}ms process={:.0f}ms",
                file_id, len(data) // 1024, dl_ms, proc_ms,
            )
            return ref

    def _process_and_save(self, data: bytes, path: Path) -> ImageRefBlock:
        """Resize image and save to disk. Animated GIFs are preserved as-is."""
        # Animated GIF: pyvips only loads the first frame, so save original bytes
        if data[:6] in (b"GIF87a", b"GIF89a"):
            gif_path = path.with_suffix(".gif")
            gif_path.parent.mkdir(parents=True, exist_ok=True)
            gif_path.write_bytes(data)
            return ImageRefBlock(type="image_ref", path=str(gif_path), media_type="image/gif")

        import pyvips

        img: Any = pyvips.Image.new_from_buffer(data, "")

        # Resize if needed
        max_side = max(img.width, img.height)
        if max_side > self._max_dim:
            scale = self._max_dim / max_side
            img = img.resize(scale)

        has_alpha = self._image_has_alpha(img)
        suffix = ".png" if has_alpha else ".jpg"
        save_path = path.with_suffix(suffix)

        # Save
        save_path.parent.mkdir(parents=True, exist_ok=True)
        if has_alpha:
            img.pngsave(str(save_path), strip=True)
            media_type = "image/png"
        else:
            img.jpegsave(str(save_path), Q=80, strip=True)
            media_type = "image/jpeg"

        return ImageRefBlock(type="image_ref", path=str(save_path), media_type=media_type)

    async def load_as_base64(self, ref: ImageRefBlock | dict[str, Any]) -> dict[str, Any] | None:
        """Read image from disk and return an Anthropic image content block.

        Returns None if the file no longer exists (expired/cleaned up).
        """
        path = Path(ref["path"])
        if not path.exists():
            return None

        t0 = time.perf_counter()
        data, b64 = await asyncio.to_thread(self._read_and_encode, path)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        media_type = self._media_type_for_suffix(path.suffix.lower())
        _L.debug(
            "image load_base64 | file={} size={}KB elapsed={:.0f}ms",
            path.name, len(data) // 1024, elapsed_ms,
        )
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64,
            },
        }

    @staticmethod
    def _read_and_encode(path: Path) -> tuple[bytes, str]:
        data = path.read_bytes()
        return data, base64.b64encode(data).decode("ascii")

    @staticmethod
    def _media_type_for_suffix(suffix: str) -> str:
        if suffix == ".gif":
            return "image/gif"
        if suffix == ".png":
            return "image/png"
        if suffix == ".webp":
            return "image/webp"
        return "image/jpeg"

    @staticmethod
    def _image_has_alpha(img: Any) -> bool:
        if hasattr(img, "hasalpha"):
            try:
                return bool(img.hasalpha())
            except Exception:
                return False
        try:
            bands = int(getattr(img, "bands", 0) or 0)
            interpretation = str(getattr(img, "interpretation", "") or "").lower()
            return bands == 2 or bands == 4 or "alpha" in interpretation
        except Exception:
            return False

    async def cleanup(self, max_age: timedelta = timedelta(hours=24)) -> None:
        """Delete cached images older than max_age. Remove empty subdirectories."""
        t0 = time.perf_counter()
        removed = await asyncio.to_thread(self._cleanup_sync, max_age)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if removed:
            _L.info("image_cache cleanup | removed={} elapsed={:.0f}ms", removed, elapsed_ms)

    def _cleanup_sync(self, max_age: timedelta) -> int:
        cutoff = time.time() - max_age.total_seconds()
        removed = 0

        for subdir in self._dir.iterdir():
            if not subdir.is_dir():
                continue
            for f in subdir.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            # Remove empty bucket directory
            if subdir.is_dir() and not any(subdir.iterdir()):
                subdir.rmdir()

        return removed
