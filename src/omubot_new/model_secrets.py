"""Instance-local model API key storage with destination-bound reads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Final, cast

from .config import ModelProfile

_STORE_NAME: Final = "model-secrets.json"
_STORE_VERSION: Final = 1
_MAX_STORE_BYTES: Final = 64 * 1024
_MAX_KEY_LENGTH: Final = 4096
_MAX_NAME_LENGTH: Final = 64
_PROFILE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_NOFOLLOW: Final[int] = cast(int, getattr(os, "O_NOFOLLOW", 0))


def _invalid_store() -> ValueError:
    return ValueError("invalid_model_secret_store")


def _invalid_secret() -> ValueError:
    return ValueError("invalid_model_secret")


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _validate_name(name: str) -> None:
    if (
        type(name) is not str
        or not name
        or len(name) > _MAX_NAME_LENGTH
        or _PROFILE_NAME.fullmatch(name) is None
    ):
        raise _invalid_secret()


def _validate_key(key: str) -> None:
    if (
        type(key) is not str
        or not key
        or not key.strip()
        or key != key.strip()
        or len(key) > _MAX_KEY_LENGTH
        or not key.isascii()
        or _contains_control(key)
    ):
        raise _invalid_secret()


def _destination_fingerprint(name: str, profile: ModelProfile) -> str:
    identity = [name, profile.api_format, profile.endpoint, profile.api_key_env]
    encoded = json.dumps(identity, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _directory_path(directory: Path, *, create: bool) -> Path | None:
    try:
        root = Path(directory)
        if root.is_symlink():
            raise _invalid_store()
        if root.exists():
            if not root.is_dir():
                raise _invalid_store()
            return root
        if not create:
            return None
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or not root.is_dir():
            raise _invalid_store()
        return root
    except ValueError:
        raise
    except (OSError, TypeError):
        raise _invalid_store() from None


def _decode_store(raw: bytes) -> dict[str, dict[str, str]]:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _invalid_store() from None
    if not isinstance(document, dict):
        raise _invalid_store()
    value = cast(dict[str, object], document)
    if set(value) != {"version", "profiles"} or type(value.get("version")) is not int:
        raise _invalid_store()
    if value["version"] != _STORE_VERSION or not isinstance(value.get("profiles"), dict):
        raise _invalid_store()

    raw_profiles = cast(dict[object, object], value["profiles"])
    profiles: dict[str, dict[str, str]] = {}
    for raw_name, raw_record in raw_profiles.items():
        if not isinstance(raw_name, str) or _PROFILE_NAME.fullmatch(raw_name) is None:
            raise _invalid_store()
        if not isinstance(raw_record, dict):
            raise _invalid_store()
        record = cast(dict[str, object], raw_record)
        if set(record) != {"fingerprint", "key"}:
            raise _invalid_store()
        fingerprint = record.get("fingerprint")
        key = record.get("key")
        if not isinstance(fingerprint, str) or _FINGERPRINT.fullmatch(fingerprint) is None:
            raise _invalid_store()
        if not isinstance(key, str):
            raise _invalid_store()
        try:
            _validate_key(key)
        except ValueError:
            raise _invalid_store() from None
        profiles[raw_name] = {"fingerprint": fingerprint, "key": key}
    return profiles


def _read_store(directory: Path) -> dict[str, dict[str, str]]:
    path = directory / _STORE_NAME
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return {}
    except OSError:
        raise _invalid_store() from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise _invalid_store()
    if metadata.st_size > _MAX_STORE_BYTES:
        raise _invalid_store()

    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_STORE_BYTES:
            raise _invalid_store()
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(_MAX_STORE_BYTES + 1)
    except FileNotFoundError:
        return {}
    except ValueError:
        raise
    except OSError:
        raise _invalid_store() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(raw) > _MAX_STORE_BYTES:
        raise _invalid_store()
    return _decode_store(raw)


def _encode_store(profiles: dict[str, dict[str, str]]) -> bytes:
    try:
        encoded = json.dumps(
            {"version": _STORE_VERSION, "profiles": profiles},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii") + b"\n"
    except (TypeError, UnicodeError, ValueError):
        raise _invalid_store() from None
    if len(encoded) > _MAX_STORE_BYTES:
        raise _invalid_store()
    return encoded


def _write_store(directory: Path, content: bytes) -> None:
    path = directory / _STORE_NAME
    if path.is_symlink():
        raise _invalid_store()
    descriptor: int | None = None
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".model-secrets-", dir=directory)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise _invalid_store()
        os.replace(temporary, path)
        temporary = None
    except ValueError:
        raise
    except OSError:
        raise _invalid_store() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def save_model_secret(directory: Path, name: str, profile: ModelProfile, key: str) -> None:
    """Atomically save one key under its profile and destination fingerprint."""
    _validate_name(name)
    _validate_key(key)
    root = _directory_path(directory, create=True)
    if root is None:
        raise _invalid_store()
    profiles = _read_store(root)
    profiles[name] = {"fingerprint": _destination_fingerprint(name, profile), "key": key}
    _write_store(root, _encode_store(profiles))


def read_model_secret(directory: Path, name: str, profile: ModelProfile) -> str:
    """Return a key only when the saved profile destination still matches."""
    _validate_name(name)
    root = _directory_path(directory, create=False)
    if root is None:
        return ""
    profiles = _read_store(root)
    record = profiles.get(name)
    if record is None or record["fingerprint"] != _destination_fingerprint(name, profile):
        return ""
    return record["key"]
