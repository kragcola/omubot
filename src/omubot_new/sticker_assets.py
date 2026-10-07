"""Resolve approved N5 sticker metadata to bounded local image bytes.

The resolver has one storage boundary: a configured local directory.  Callers
provide only the catalog identity (``sticker_id`` and its entry revision),
never a path or URL.  All filesystem work runs in a worker thread so a slow
filesystem cannot pause the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from pathlib import Path
from typing import Final, cast

from .stickers import MAX_STICKER_BYTES, ResolvedStickerAsset, StickerCatalog, StickerEntry
from .types import StickerImage, StickerMediaType

__all__ = [
    "ApprovedStickerAssetResolver",
    "ResolvedStickerAsset",
    "asset_filename",
]


_MIME_SUFFIX: Final[dict[str, str]] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_NOFOLLOW: Final[int] = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY: Final[int] = getattr(os, "O_DIRECTORY", 0)
_HAS_DIR_FD: Final[bool] = os.open in getattr(os, "supports_dir_fd", ())


def asset_filename(entry: StickerEntry) -> str:
    """Return the only filename accepted for one catalog entry.

    The name is derived entirely from audited metadata.  Keeping this mapping
    in one place prevents a caller from selecting a suffix or path of its own.
    """

    if type(entry) is not StickerEntry:
        raise TypeError("entry must be a StickerEntry")
    suffix = _MIME_SUFFIX.get(entry.mime_type)
    if suffix is None:
        raise ValueError("mime_type is unsupported")
    return f"{entry.content_hash}{suffix}"


def _open_asset(root: Path, filename: str) -> tuple[int, int] | None:
    """Open a regular asset under a pinned, non-symlink root directory."""

    # The fallback path cannot provide the same no-follow + pinned-directory
    # guarantee.  Refuse it rather than turning a platform gap into a TOCTOU
    # file read.
    if _NOFOLLOW == 0 or not _HAS_DIR_FD:
        return None

    root_descriptor: int | None = None
    descriptor: int | None = None
    try:
        root_metadata = os.lstat(root)
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
            return None
        root_descriptor = os.open(str(root), os.O_RDONLY | _NOFOLLOW | _DIRECTORY)
        opened_root = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or opened_root.st_dev != root_metadata.st_dev
            or opened_root.st_ino != root_metadata.st_ino
        ):
            return None

        metadata = os.lstat(root / filename)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return None

        descriptor = os.open(
            filename,
            os.O_RDONLY | _NOFOLLOW,
            dir_fd=root_descriptor,
        )
        return_value = (root_descriptor, descriptor)
        root_descriptor = None
        descriptor = None
        return return_value
    except OSError:
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except OSError:
                pass


def _read_asset_bytes(
    root: Path,
    filename: str,
    expected_size: int,
    max_bytes: int,
    expected_hash: str,
) -> bytes | None:
    """Read one opened regular file and validate its size and digest.

    The descriptor is pinned before reading.  A path replacement after open
    therefore cannot make this operation read a different file; the bytes are
    still checked against the catalog hash before returning.
    """

    if expected_size < 1 or expected_size > max_bytes:
        return None

    opened = _open_asset(root, filename)
    if opened is None:
        return None
    root_descriptor, descriptor = opened
    handle = None
    owned_descriptor: int | None = descriptor
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None
        if metadata.st_size != expected_size or metadata.st_size > max_bytes:
            return None

        handle = os.fdopen(descriptor, "rb")
        owned_descriptor = None
        raw = handle.read(max_bytes + 1)
        if len(raw) != expected_size or len(raw) > max_bytes:
            return None
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            return None
        return raw
    except (OSError, ValueError):
        return None
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        if owned_descriptor is not None:
            try:
                os.close(owned_descriptor)
            except OSError:
                pass
        try:
            os.close(root_descriptor)
        except OSError:
            pass


class ApprovedStickerAssetResolver:
    """Resolve only approved catalog entries from a fixed local directory."""

    def __init__(
        self,
        asset_dir: object,
        catalog: object,
        *,
        max_bytes: int = MAX_STICKER_BYTES,
    ) -> None:
        if not isinstance(asset_dir, (Path, str)):
            raise TypeError("asset_dir must be a local path")
        if isinstance(asset_dir, str) and "://" in asset_dir:
            raise ValueError("asset_dir must be a local path")
        if not isinstance(catalog, StickerCatalog):
            raise TypeError("catalog must be a StickerCatalog")
        if type(max_bytes) is not int or max_bytes < 1 or max_bytes > MAX_STICKER_BYTES:
            raise ValueError("max_bytes is outside the sticker budget")
        # ``absolute`` does not follow symlinks.  The worker validates the
        # actual root directory again immediately before opening it.
        self._asset_dir = Path(asset_dir).absolute()
        self._catalog = catalog
        self._max_bytes = max_bytes

    async def resolve(
        self, sticker_id: str, catalog_revision: int
    ) -> ResolvedStickerAsset | None:
        """Resolve an exact approved identity, or return ``None`` fail-closed."""

        if type(sticker_id) is not str or type(catalog_revision) is not int:
            return None
        if catalog_revision < 1:
            return None

        try:
            entry = self._catalog.get(sticker_id)
            if (
                entry is None
                or entry.status != "approved"
                or entry.catalog_revision != catalog_revision
            ):
                return None
            filename = asset_filename(entry)
        except (TypeError, ValueError, KeyError):
            return None

        try:
            raw = await asyncio.to_thread(
                _read_asset_bytes,
                self._asset_dir,
                filename,
                entry.byte_size,
                self._max_bytes,
                entry.content_hash,
            )
        except OSError:
            # Expected filesystem failures are candidate misses.  Cancellation
            # is a BaseException in supported Python versions and is preserved.
            return None
        if raw is None:
            return None

        # Re-check catalog ownership after the worker returns.  Revocation or
        # replacement during a slow read must never produce a stale asset.
        try:
            current = self._catalog.get(sticker_id)
        except (TypeError, ValueError):
            return None
        if current is None or current != entry or current.status != "approved":
            return None

        try:
            image = StickerImage(
                data=raw,
                media_type=cast(StickerMediaType, entry.mime_type),
            )
        except (TypeError, ValueError):
            return None
        return ResolvedStickerAsset(sticker_id, catalog_revision, image)
