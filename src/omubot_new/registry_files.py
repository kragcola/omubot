"""Shared bounded and no-follow file primitives for immutable registries."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import cast

from .types import OperationError

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class _DuplicateJSONKey(ValueError):
    pass


def absolute_directory(value: str | Path, *, error_code: str) -> Path:
    try:
        raw = os.fspath(value)
        if type(raw) is not str or not raw or "\x00" in raw:
            raise OperationError(error_code)
        path = Path(raw)
        if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
            raise OperationError(error_code)
        return path
    except (OSError, TypeError, ValueError):
        raise OperationError(error_code) from None


def same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def safe_directory_status(
    path: Path, *, error_code: str
) -> os.stat_result | None:
    """Return None only when an absolute path safely lacks a component."""
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current = current / part
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise OperationError(error_code)

        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OperationError(error_code)
        if _DIRECTORY:
            descriptor = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISDIR(opened.st_mode) or not same_file(opened, metadata):
                    raise OperationError(error_code)
                metadata = opened
            finally:
                os.close(descriptor)
        return metadata
    except OperationError:
        raise
    except OSError:
        raise OperationError(error_code) from None


def scan_json_files(
    path: Path,
    *,
    max_directory_items: int,
    max_json_files: int,
    too_large_code: str,
    directory_error_code: str,
    file_error_code: str,
) -> list[tuple[Path, os.stat_result]]:
    """Return sorted regular JSON files after bounded, symlink-safe scanning."""
    try:
        children: list[Path] = []
        for child in path.iterdir():
            children.append(child)
            if len(children) > max_directory_items:
                raise OperationError(too_large_code)
        children.sort(key=lambda child: child.name)
    except OperationError:
        raise
    except OSError:
        raise OperationError(directory_error_code) from None

    json_files: list[tuple[Path, os.stat_result]] = []
    for child in children:
        try:
            metadata = os.lstat(child)
        except OSError:
            raise OperationError(file_error_code) from None
        if stat.S_ISLNK(metadata.st_mode):
            raise OperationError(file_error_code)
        if child.suffix != ".json":
            if not stat.S_ISREG(metadata.st_mode):
                raise OperationError(file_error_code)
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OperationError(file_error_code)
        json_files.append((child, metadata))
        if len(json_files) > max_json_files:
            raise OperationError(too_large_code)
    return json_files


def read_bounded_file(
    path: Path,
    observed: os.stat_result,
    *,
    max_bytes: int,
    error_code: str,
) -> bytes:
    descriptor: int | None = None
    try:
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_size < 0
            or observed.st_size > max_bytes
        ):
            raise OperationError(error_code)
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not same_file(opened, observed)
            or opened.st_size < 0
            or opened.st_size > max_bytes
        ):
            raise OperationError(error_code)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(max_bytes + 1)
            after = os.fstat(handle.fileno())
        if (
            len(raw) > max_bytes
            or len(raw) != opened.st_size
            or not same_file(after, opened)
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise OperationError(error_code)
        return raw
    except OperationError:
        raise
    except (OSError, ValueError):
        raise OperationError(error_code) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def decode_json_object(
    raw: bytes,
    *,
    json_error_code: str,
    schema_error_code: str,
) -> dict[str, object]:
    """Decode one strict UTF-8 JSON object and reject duplicate keys/constants."""

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJSONKey()
            result[key] = value
        return result

    def reject_constant(_: str) -> object:
        raise ValueError("invalid_json_constant")

    try:
        text = raw.decode("utf-8", errors="strict")
        decoded = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJSONKey,
        RecursionError,
        ValueError,
    ):
        raise OperationError(json_error_code) from None
    if not isinstance(decoded, dict):
        raise OperationError(schema_error_code)
    return cast(dict[str, object], decoded)


def ensure_directory_stable(
    path: Path,
    observed: os.stat_result,
    *,
    error_code: str,
) -> None:
    current = safe_directory_status(path, error_code=error_code)
    if current is None or not same_file(current, observed):
        raise OperationError(error_code)


__all__ = [
    "absolute_directory",
    "decode_json_object",
    "ensure_directory_stable",
    "read_bounded_file",
    "safe_directory_status",
    "same_file",
    "scan_json_files",
]
