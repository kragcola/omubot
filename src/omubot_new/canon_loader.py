"""Read-only loader for reviewed, fixed Canon JSON configuration.

Provenance is checked as a local audit reference and bound to the immutable
registry fingerprint. It is never used as model prompt content.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from .registry_files import (
    absolute_directory,
    decode_json_object,
    ensure_directory_stable,
    read_bounded_file,
    safe_directory_status,
    scan_json_files,
)
from .types import OperationError
from .worldbook import CanonEntry, CanonKind, CanonRegistry

_MAX_CANON_FILES = 64
_MAX_DIRECTORY_ITEMS = 128
_MAX_CANON_FILE_BYTES = 128 * 1024
_MAX_CANON_TOTAL_BYTES = 1024 * 1024
_MAX_CANON_ENTRIES = 256
_MAX_SOURCE_CHARS = 256
_MAX_PACK_CHARS = 64
_PACK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_ENTRY_FIELDS = frozenset(
    {
        "entry_id",
        "title",
        "text",
        "version",
        "kind",
        "keywords",
        "aliases",
        "regexes",
        "entity_ids",
        "priority",
        "always_active",
        "metadata",
    }
)
_REQUIRED_ENTRY_FIELDS = frozenset({"entry_id", "title", "text", "metadata"})
_PROVENANCE_FIELDS = frozenset({"source", "reviewed", "pack"})


def _validate_source(value: object, kind: object) -> None:
    if not isinstance(value, str) or not value or len(value) > _MAX_SOURCE_CHARS:
        raise OperationError("invalid_canon_provenance")
    if value != value.strip() or "\\" in value or ":" in value or "%" in value:
        raise OperationError("invalid_canon_provenance")
    if any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value):
        raise OperationError("invalid_canon_provenance")
    if not value.startswith("config/"):
        raise OperationError("invalid_canon_provenance")
    path_part, separator, fragment = value.partition("#")
    if separator and (not fragment or "#" in fragment):
        raise OperationError("invalid_canon_provenance")
    if any(part in {"", ".", ".."} for part in path_part.split("/")):
        raise OperationError("invalid_canon_provenance")
    if kind == "persona_canon" and not path_part.startswith("config/persona/"):
        raise OperationError("invalid_canon_provenance")


def _validate_provenance(value: object, kind: object) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise OperationError("invalid_canon_provenance")
    metadata = cast(dict[str, object], value)
    if frozenset(metadata) != _PROVENANCE_FIELDS or metadata.get("reviewed") is not True:
        raise OperationError("invalid_canon_provenance")
    _validate_source(metadata.get("source"), kind)
    pack = metadata.get("pack")
    if not isinstance(pack, str) or _PACK_ID.fullmatch(pack) is None:
        raise OperationError("invalid_canon_provenance")
    return cast(str, metadata["source"]), pack


def _parse_entry(value: object) -> CanonEntry:
    if not isinstance(value, dict):
        raise OperationError("invalid_canon_schema")
    raw = cast(dict[str, object], value)
    if set(raw) - _ENTRY_FIELDS or not _REQUIRED_ENTRY_FIELDS.issubset(raw):
        raise OperationError("invalid_canon_schema")
    kind = raw.get("kind", "native_canon")
    source_ref, source_pack = _validate_provenance(raw.get("metadata"), kind)
    if "always_active" in raw and raw["always_active"] is not False:
        raise OperationError("invalid_canon_schema")

    entry_id = raw.get("entry_id")
    title = raw.get("title")
    text = raw.get("text")
    if not isinstance(entry_id, str) or not isinstance(title, str) or not isinstance(text, str):
        raise OperationError("invalid_canon_schema")
    version = raw.get("version", 1)
    priority = raw.get("priority", 100)
    if type(version) is not int or version < 1:
        raise OperationError("invalid_canon_schema")
    if type(priority) is not int or not -1000 <= priority <= 1000:
        raise OperationError("invalid_canon_schema")

    trigger_values: dict[str, tuple[str, ...]] = {}
    for name in ("keywords", "aliases", "regexes", "entity_ids"):
        triggers = raw.get(name, [])
        if not isinstance(triggers, list):
            raise OperationError("invalid_canon_schema")
        trigger_values[name] = tuple(cast(list[str], triggers))
    if not any(trigger_values.values()):
        raise OperationError("invalid_canon_schema")
    if not isinstance(kind, str):
        raise OperationError("invalid_canon_schema")

    return CanonEntry(
        entry_id=entry_id,
        title=title,
        text=text,
        version=version,
        kind=cast(CanonKind, kind),
        keywords=trigger_values["keywords"],
        aliases=trigger_values["aliases"],
        regexes=trigger_values["regexes"],
        entity_ids=trigger_values["entity_ids"],
        priority=priority,
        source_ref=source_ref,
        source_pack=source_pack,
    )

def load_canon_registry(
    directory: str | Path,
    *,
    registry_version: int = 1,
    enabled: bool = False,
    allowed_groups: Sequence[str] = (),
) -> CanonRegistry:
    """Load sorted, bounded schema-v1 Canon JSON files into an immutable registry.

    Missing directories are treated as an empty registry only after each
    existing path component has been confirmed to be a real directory. The
    provenance metadata is validated and bound to the registry fingerprint;
    the referenced source is not opened or interpreted as instructions.
    """
    path = absolute_directory(directory, error_code="invalid_canon_directory")
    directory_stat = safe_directory_status(path, error_code="invalid_canon_directory")
    entries: list[CanonEntry] = []
    if directory_stat is not None:
        json_files = scan_json_files(
            path,
            max_directory_items=_MAX_DIRECTORY_ITEMS,
            max_json_files=_MAX_CANON_FILES,
            too_large_code="canon_directory_too_large",
            directory_error_code="invalid_canon_directory",
            file_error_code="invalid_canon_file",
        )

        total_bytes = 0
        seen_ids: set[str] = set()
        for file_path, metadata in json_files:
            raw = read_bounded_file(
                file_path,
                metadata,
                max_bytes=_MAX_CANON_FILE_BYTES,
                error_code="invalid_canon_file",
            )
            total_bytes += len(raw)
            if total_bytes > _MAX_CANON_TOTAL_BYTES:
                raise OperationError("canon_directory_too_large")
            document = decode_json_object(
                raw,
                json_error_code="invalid_canon_json",
                schema_error_code="invalid_canon_schema",
            )
            if set(document) != {"schema_version", "entries"}:
                raise OperationError("invalid_canon_schema")
            if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
                raise OperationError("invalid_canon_schema")
            raw_entries = document.get("entries")
            if not isinstance(raw_entries, list):
                raise OperationError("invalid_canon_schema")
            file_entries = cast(list[object], raw_entries)
            if len(entries) + len(file_entries) > _MAX_CANON_ENTRIES:
                raise OperationError("canon_registry_too_large")
            for raw_entry in file_entries:
                entry = _parse_entry(raw_entry)
                if entry.entry_id in seen_ids:
                    raise OperationError("duplicate_canon_entry_id")
                seen_ids.add(entry.entry_id)
                entries.append(entry)

        ensure_directory_stable(
            path,
            directory_stat,
            error_code="invalid_canon_directory",
        )

    return CanonRegistry(
        registry_version=registry_version,
        entries=entries,
        enabled=enabled,
        allowed_groups=allowed_groups,
    )
