"""Read-only loader for reviewed, structured Storylet JSON configuration."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
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
from .story import LifeUpdate, PartnerUpdate
from .types import OperationError
from .worldbook import Storylet, StoryletRegistry, StoryletSeverity

_MAX_STORYLET_FILES = 64
_MAX_DIRECTORY_ITEMS = 128
_MAX_FILE_BYTES = 128 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024
_MAX_STORYLETS = 256
_MAX_SOURCE_CHARS = 256
_MAX_PACK_CHARS = 64
_PACK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_ROOT_FIELDS = frozenset({"schema_version", "registry_version", "storylets"})
_STORYLET_FIELDS = frozenset(
    {
        "storylet_id",
        "title",
        "target_arc_id",
        "conditions",
        "consequence",
        "once",
        "cooldown_steps",
        "delay_steps",
        "severity",
        "recovery_steps",
        "cost",
        "required_evidence",
        "priority",
        "summary",
        "metadata",
    }
)
_REQUIRED_STORYLET_FIELDS = frozenset({"storylet_id", "title", "metadata"})
_METADATA_FIELDS = frozenset({"source", "reviewed", "pack"})
_CONDITION_FIELDS = frozenset(
    {
        "min_step",
        "after_step",
        "arc_revision",
        "in_recovery",
        "var_gte",
        "var_lte",
        "var_eq",
        "has_thread",
        "missing_thread",
    }
)
_SEVERITIES = frozenset({"daily", "tension", "setback", "recovery"})
_EFFECT_FIELDS = frozenset(
    {
        "variable_deltas",
        "open_threads",
        "resolve_threads",
        "stage",
        "arc_status",
        "life_updates",
        "partner_updates",
    }
)
_FORBIDDEN_MARKERS = frozenset(
    {
        "factual",
        "factual_commit",
        "social",
        "social_evidence",
        "real_person",
        "real_name",
        "user_id",
        "person_id",
        "speaker_id",
        "persona_canon",
        "native_canon",
        "canon_write",
        "canon_override",
        "private_message",
        "private_chat",
        "private_evidence",
    }
)


def _invalid_schema() -> OperationError:
    return OperationError("invalid_storylet_schema")


def _validate_source(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_SOURCE_CHARS:
        raise OperationError("invalid_storylet_provenance")
    if value != value.strip() or "\\" in value or ":" in value or "%" in value:
        raise OperationError("invalid_storylet_provenance")
    if any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value):
        raise OperationError("invalid_storylet_provenance")
    if not value.startswith("config/"):
        raise OperationError("invalid_storylet_provenance")
    path_part, separator, fragment = value.partition("#")
    if separator and (not fragment or "#" in fragment):
        raise OperationError("invalid_storylet_provenance")
    if any(part in {"", ".", ".."} for part in path_part.split("/")):
        raise OperationError("invalid_storylet_provenance")
    return value


def _validate_metadata(value: object) -> tuple[str, str]:
    if type(value) is not dict:
        raise OperationError("invalid_storylet_provenance")
    metadata = cast(dict[str, object], value)
    if frozenset(metadata) != _METADATA_FIELDS or metadata.get("reviewed") is not True:
        raise OperationError("invalid_storylet_provenance")
    source = _validate_source(metadata.get("source"))
    pack = metadata.get("pack")
    if not isinstance(pack, str) or _PACK_ID.fullmatch(pack) is None:
        raise OperationError("invalid_storylet_provenance")
    return source, pack


def _validate_conditions(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise _invalid_schema()
    conditions = cast(dict[str, object], value)
    if set(conditions) - _CONDITION_FIELDS:
        raise _invalid_schema()
    for name in ("min_step", "after_step", "arc_revision"):
        if name in conditions:
            condition_value = conditions[name]
            if type(condition_value) is not int or condition_value < 0:
                raise _invalid_schema()
    if "in_recovery" in conditions and type(conditions["in_recovery"]) is not bool:
        raise _invalid_schema()
    for name in ("var_gte", "var_lte", "var_eq"):
        if name not in conditions:
            continue
        values = conditions[name]
        if type(values) is not dict:
            raise _invalid_schema()
        mapped = cast(dict[str, object], values)
        if name != "var_eq":
            for expected in mapped.values():
                if isinstance(expected, bool) or not isinstance(expected, (int, float)):
                    raise _invalid_schema()
                if not math.isfinite(float(expected)) or abs(float(expected)) > 1_000_000_000:
                    raise _invalid_schema()
    for name in ("has_thread", "missing_thread"):
        if name not in conditions:
            continue
        values = conditions[name]
        if type(values) is not list:
            raise _invalid_schema()
        sequence = cast(list[object], values)
        if any(type(item) is not str for item in sequence):
            raise _invalid_schema()
    return conditions


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        mapped = cast(Mapping[object, object], value)
        return any(
            str(key).strip().casefold() in _FORBIDDEN_MARKERS
            or _contains_forbidden(item)
            for key, item in mapped.items()
        )
    if isinstance(value, list | tuple):
        sequence = cast(list[object] | tuple[object, ...], value)
        return any(_contains_forbidden(item) for item in sequence)
    if isinstance(value, str):
        return value.strip().casefold() in _FORBIDDEN_MARKERS
    return False


def _effect_identifier(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid_schema()
    result = value.strip()
    if (
        not result
        or len(result) > 128
        or any(ord(char) < 32 for char in result)
        or any(char in result for char in ("/", "\\", "*", "?"))
    ):
        raise _invalid_schema()
    return result


def _validate_consequence(value: dict[str, object]) -> None:
    if _contains_forbidden(value) or set(value) - _EFFECT_FIELDS:
        raise _invalid_schema()
    if "variable_deltas" in value:
        raw_deltas = value["variable_deltas"]
        if type(raw_deltas) is not dict:
            raise _invalid_schema()
        deltas = cast(dict[str, object], raw_deltas)
        if len(deltas) > 32:
            raise _invalid_schema()
        for name, delta in deltas.items():
            key = name.strip()
            if (
                not key
                or len(key) > 64
                or any(ord(char) < 32 for char in key)
                or "/" in key
                or "\\" in key
                or isinstance(delta, bool)
                or not isinstance(delta, int | float)
                or not math.isfinite(float(delta))
                or abs(float(delta)) > 1_000_000_000
            ):
                raise _invalid_schema()
    for name in ("open_threads", "resolve_threads"):
        if name not in value:
            continue
        raw_threads = value[name]
        if type(raw_threads) is not list or len(cast(list[object], raw_threads)) > 32:
            raise _invalid_schema()
        for item in cast(list[object], raw_threads):
            _effect_identifier(item)
    for name in ("stage", "arc_status"):
        if name not in value:
            continue
        raw_text = value[name]
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise _invalid_schema()
        text_value = raw_text.strip()
        if (
            len(text_value) > 64
            or any(ord(char) < 32 for char in text_value)
            or "/" in text_value
            or "\\" in text_value
            or (name == "arc_status" and text_value not in {"active", "closed"})
        ):
            raise _invalid_schema()
    if "life_updates" in value:
        updates = value["life_updates"]
        if type(updates) is not list or len(cast(list[object], updates)) > 16:
            raise _invalid_schema()
        seen_keys: set[str] = set()
        for raw_update in cast(list[object], updates):
            if type(raw_update) is not dict:
                raise _invalid_schema()
            fields = cast(dict[str, object], raw_update)
            if (
                set(fields) - {"key", "value", "ttl_hours", "ttl_seconds"}
                or "key" not in fields
                or "value" not in fields
            ):
                raise _invalid_schema()
            try:
                update = LifeUpdate(
                    key=cast(str, fields["key"]),
                    value=fields["value"],
                    ttl_hours=cast(float | None, fields.get("ttl_hours")),
                    ttl_seconds=cast(float | None, fields.get("ttl_seconds")),
                )
            except OperationError as exc:
                raise _invalid_schema() from exc
            if update.key in seen_keys:
                raise _invalid_schema()
            seen_keys.add(update.key)
    if "partner_updates" in value:
        updates = value["partner_updates"]
        if type(updates) is not list or len(cast(list[object], updates)) > 16:
            raise _invalid_schema()
        seen_entities: set[str] = set()
        for raw_update in cast(list[object], updates):
            if type(raw_update) is not dict:
                raise _invalid_schema()
            fields = cast(dict[str, object], raw_update)
            allowed_fields = {
                "entity_id",
                "mood",
                "availability",
                "current_state",
                "constraints",
                "note",
                "event_note",
            }
            if set(fields) - allowed_fields or "entity_id" not in fields:
                raise _invalid_schema()
            try:
                update = PartnerUpdate(
                    entity_id=cast(str, fields["entity_id"]),
                    mood=cast(str | None, fields.get("mood")),
                    availability=cast(str | None, fields.get("availability")),
                    current_state=cast(str | None, fields.get("current_state")),
                    constraints=cast(list[str], fields.get("constraints", [])),
                    note=cast(str | None, fields.get("note")),
                    event_note=cast(str | None, fields.get("event_note")),
                )
            except OperationError as exc:
                raise _invalid_schema() from exc
            if update.entity_id in seen_entities:
                raise _invalid_schema()
            seen_entities.add(update.entity_id)


def _parse_storylet(value: object) -> Storylet:
    if type(value) is not dict:
        raise _invalid_schema()
    raw = cast(dict[str, object], value)
    if set(raw) - _STORYLET_FIELDS or not _REQUIRED_STORYLET_FIELDS.issubset(raw):
        raise _invalid_schema()

    source_ref, source_pack = _validate_metadata(raw.get("metadata"))
    conditions = _validate_conditions(raw.get("conditions", {}))
    consequence = raw.get("consequence", {})
    if type(consequence) is not dict:
        raise _invalid_schema()
    consequence_values = cast(dict[str, object], consequence)
    _validate_consequence(consequence_values)

    once = raw.get("once", False)
    if type(once) is not bool:
        raise _invalid_schema()
    for name in ("cooldown_steps", "delay_steps", "recovery_steps"):
        value_at_name = raw.get(name, 0)
        if type(value_at_name) is not int or value_at_name < 0:
            raise _invalid_schema()
    severity = raw.get("severity", "daily")
    if not isinstance(severity, str) or severity not in _SEVERITIES:
        raise _invalid_schema()
    cost = raw.get("cost", 1)
    if type(cost) is int:
        if cost < 1:
            raise _invalid_schema()
    elif type(cost) is dict:
        mapped_cost = cast(dict[str, object], cost)
        if (
            set(mapped_cost) != {"events"}
            or type(mapped_cost["events"]) is not int
            or mapped_cost["events"] < 1
        ):
            raise _invalid_schema()
    else:
        raise _invalid_schema()
    evidence = raw.get("required_evidence", [])
    if type(evidence) is not list:
        raise _invalid_schema()
    evidence_items = cast(list[object], evidence)
    if any(type(item) is not str for item in evidence_items):
        raise _invalid_schema()
    priority = raw.get("priority", 100)
    if type(priority) is not int or not -1000 <= priority <= 1000:
        raise _invalid_schema()

    for name in ("storylet_id", "title"):
        if not isinstance(raw[name], str):
            raise _invalid_schema()
    target_arc_id = raw.get("target_arc_id", "")
    summary = raw.get("summary", "")
    if not isinstance(target_arc_id, str) or not isinstance(summary, str):
        raise _invalid_schema()

    try:
        return Storylet(
            storylet_id=cast(str, raw["storylet_id"]),
            title=cast(str, raw["title"]),
            target_arc_id=target_arc_id,
            conditions=conditions,
            consequence=consequence_values,
            once=once,
            cooldown_steps=cast(int, raw.get("cooldown_steps", 0)),
            delay_steps=cast(int, raw.get("delay_steps", 0)),
            severity=cast(StoryletSeverity, severity),
            recovery_steps=cast(int, raw.get("recovery_steps", 0)),
            cost=cast(int | Mapping[str, object], cost),
            required_evidence=tuple(cast(str, item) for item in evidence_items),
            priority=priority,
            summary=summary,
            source_ref=source_ref,
            source_pack=source_pack,
        )
    except OperationError as exc:
        raise _invalid_schema() from exc


def load_storylet_registry(directory: str | Path) -> StoryletRegistry:
    """Load a bounded schema-v1 Storylet registry from an absolute config path.

    Files are read only. Each rule carries a reviewed local provenance
    reference; provenance is fingerprinted by ``StoryletRegistry`` and is not
    opened or interpreted as executable content.
    """
    path = absolute_directory(directory, error_code="invalid_storylet_directory")
    directory_stat = safe_directory_status(
        path, error_code="invalid_storylet_directory"
    )
    storylets: list[Storylet] = []
    registry_version = 1
    if directory_stat is None:
        return StoryletRegistry(version=registry_version, storylets=())

    json_files = scan_json_files(
        path,
        max_directory_items=_MAX_DIRECTORY_ITEMS,
        max_json_files=_MAX_STORYLET_FILES,
        too_large_code="storylet_directory_too_large",
        directory_error_code="invalid_storylet_directory",
        file_error_code="invalid_storylet_file",
    )

    total_bytes = 0
    seen_ids: set[str] = set()
    selected_version: int | None = None
    for file_path, metadata in json_files:
        raw = read_bounded_file(
            file_path,
            metadata,
            max_bytes=_MAX_FILE_BYTES,
            error_code="invalid_storylet_file",
        )
        total_bytes += len(raw)
        if total_bytes > _MAX_TOTAL_BYTES:
            raise OperationError("storylet_directory_too_large")
        document = decode_json_object(
            raw,
            json_error_code="invalid_storylet_json",
            schema_error_code="invalid_storylet_schema",
        )
        if frozenset(document) != _ROOT_FIELDS:
            raise _invalid_schema()
        if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
            raise _invalid_schema()
        version = document.get("registry_version")
        if type(version) is not int or version < 1:
            raise _invalid_schema()
        if selected_version is None:
            selected_version = version
        elif selected_version != version:
            raise OperationError("storylet_registry_version_mismatch")
        raw_storylets = document.get("storylets")
        if type(raw_storylets) is not list:
            raise _invalid_schema()
        entries = cast(list[object], raw_storylets)
        if len(storylets) + len(entries) > _MAX_STORYLETS:
            raise OperationError("storylet_registry_too_large")
        for raw_storylet in entries:
            storylet = _parse_storylet(raw_storylet)
            if storylet.storylet_id in seen_ids:
                raise OperationError("duplicate_storylet_id")
            seen_ids.add(storylet.storylet_id)
            storylets.append(storylet)

    ensure_directory_stable(
        path,
        directory_stat,
        error_code="invalid_storylet_directory",
    )

    if selected_version is not None:
        registry_version = selected_version
    return StoryletRegistry(version=registry_version, storylets=storylets)


__all__ = ["load_storylet_registry"]
