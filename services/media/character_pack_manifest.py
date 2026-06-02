from __future__ import annotations

from pathlib import Path
from typing import Any

VALID_RELATIONS = {"self", "friend", "known"}


def normalize_relation(value: object, *, default: str = "known") -> str:
    raw = str(value or "").strip()
    fallback = default if default in VALID_RELATIONS else "known"
    return raw if raw in VALID_RELATIONS else fallback


def manifest_pack(manifest: dict[str, Any], *, fallback: str) -> str:
    return str(manifest.get("pack") or fallback).strip() or fallback


def manifest_series(manifest: dict[str, Any], *, fallback: str) -> str:
    return str(manifest.get("series") or manifest.get("pack") or fallback).strip() or fallback


def manifest_relation_default(manifest: dict[str, Any]) -> str:
    return normalize_relation(manifest.get("relation_default"), default="known")


def effective_character_relation(manifest: dict[str, Any], item: dict[str, Any]) -> str:
    return normalize_relation(item.get("relation"), default=manifest_relation_default(manifest))


def effective_character_work(manifest: dict[str, Any], item: dict[str, Any]) -> str | None:
    work = str(item.get("work") or manifest.get("work") or "").strip()
    return work or None


def character_aliases(item: dict[str, Any]) -> list[str]:
    aliases = item.get("aliases")
    if not isinstance(aliases, list):
        return []
    return [str(alias).strip() for alias in aliases if str(alias).strip()]


def character_id(item: dict[str, Any]) -> str:
    return str(item.get("character_id") or "").strip()


def character_embedding_key(item: dict[str, Any]) -> str:
    cid = character_id(item)
    return str(item.get("embedding_key") or cid).strip()


def iter_manifest_characters(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    characters = manifest.get("characters") or []
    if not isinstance(characters, list):
        return []
    return [item for item in characters if isinstance(item, dict)]


def manifest_file_pack_name(manifest_file: Path) -> str:
    return manifest_file.parent.name.removesuffix(".charpack")
