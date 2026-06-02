from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from services.media.character_pack_manifest import (
    character_aliases,
    character_embedding_key,
    character_id,
    effective_character_relation,
    effective_character_work,
    iter_manifest_characters,
    normalize_relation,
)

_L = logger.bind(channel="debug")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class _SinglePack:
    pack_dir: Path
    manifest: dict[str, Any]
    character: dict[str, Any]
    character_id: str
    embedding_key: str
    work: str | None
    relation: str
    name: str


def _series_slug(work: str) -> str:
    slug = _SLUG_RE.sub("_", work.strip()).strip("_").lower()
    if slug:
        return slug[:80]
    digest = hashlib.sha256(work.encode("utf-8")).hexdigest()[:10]
    return f"series_{digest}"


def _pack_slug(value: str) -> str:
    slug = _SLUG_RE.sub("_", value.strip()).strip("_")
    if not slug:
        raise ValueError("pack_name is required")
    return slug


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _npz_has_member(npz_path: Path, key: str) -> bool:
    try:
        with zipfile.ZipFile(npz_path) as zf:
            return f"{key}.npy" in set(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def _collect_single_packs(packs_path: Path, *, require_work: bool = True) -> list[_SinglePack]:
    packs: list[_SinglePack] = []
    for manifest_path in sorted(packs_path.glob("*.charpack/manifest.json")):
        manifest = _load_manifest(manifest_path)
        if manifest is None:
            continue
        characters = iter_manifest_characters(manifest)
        if len(characters) != 1:
            continue
        item = characters[0]
        cid = character_id(item)
        work = effective_character_work(manifest, item)
        embedding_key = character_embedding_key(item)
        npz_path = manifest_path.parent / "embeddings.npz"
        if not cid or (require_work and not work) or not embedding_key or not _npz_has_member(npz_path, embedding_key):
            continue
        packs.append(
            _SinglePack(
                pack_dir=manifest_path.parent,
                manifest=manifest,
                character=item,
                character_id=cid,
                embedding_key=embedding_key,
                work=work,
                relation=effective_character_relation(manifest, item),
                name=str(item.get("name") or cid).strip() or cid,
            )
        )
    return packs


def _collect_existing_series(packs_path: Path) -> dict[str, list[tuple[Path, set[str]]]]:
    by_work: dict[str, list[tuple[Path, set[str]]]] = {}
    for manifest_path in sorted(packs_path.glob("*.charpack/manifest.json")):
        manifest = _load_manifest(manifest_path)
        if manifest is None:
            continue
        characters = iter_manifest_characters(manifest)
        if len(characters) <= 1:
            continue
        ids = {character_id(item) for item in characters if character_id(item)}
        npz_path = manifest_path.parent / "embeddings.npz"
        if not all(_npz_has_member(npz_path, character_embedding_key(item)) for item in characters):
            continue
        works = {effective_character_work(manifest, item) for item in characters}
        for work in works:
            if work and ids:
                by_work.setdefault(work, []).append((manifest_path.parent, ids))
    return by_work


def _unique_archive_dest(archive_root: Path, pack_dir: Path) -> Path:
    dest = archive_root / pack_dir.name
    if not dest.exists():
        return dest
    stem = pack_dir.name.removesuffix(".charpack")
    for idx in range(1, 1000):
        candidate = archive_root / f"{stem}.{idx}.charpack"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"too many archived copies for {pack_dir.name}")


def _archive_packs(packs: list[_SinglePack], archive_root: Path) -> int:
    archive_root.mkdir(parents=True, exist_ok=True)
    archived = 0
    for pack in packs:
        if not pack.pack_dir.exists():
            continue
        dest = _unique_archive_dest(archive_root, pack.pack_dir)
        shutil.move(str(pack.pack_dir), str(dest))
        archived += 1
    return archived


def _copy_npz_member(src_npz: Path, src_key: str, dst_zf: zipfile.ZipFile, dst_key: str) -> None:
    with zipfile.ZipFile(src_npz) as src_zf:
        dst_zf.writestr(f"{dst_key}.npy", src_zf.read(f"{src_key}.npy"))


def _copy_samples(pack: _SinglePack, target_samples_dir: Path) -> int:
    copied = 0
    source = pack.pack_dir / "samples"
    if not source.is_dir():
        return 0
    target_samples_dir.mkdir(parents=True, exist_ok=True)
    for sample in sorted(source.glob("*.jpg")):
        shutil.copy2(sample, target_samples_dir / sample.name)
        copied += 1
    return copied


def _write_series_pack(
    packs_path: Path,
    slug: str,
    work: str,
    group: list[_SinglePack],
    *,
    series: str | None = None,
    relation_default: str = "known",
) -> Path:
    target = packs_path / f"{slug}.charpack"
    tmp = packs_path / f"{slug}.charpack.tmp"
    if target.exists():
        raise FileExistsError(f"target pack already exists: {target}")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    rel_default = normalize_relation(relation_default)
    characters: list[dict[str, Any]] = []
    for pack in group:
        entry: dict[str, Any] = {
            "character_id": pack.character_id,
            "name": pack.name,
            "embedding_key": pack.character_id,
            "aliases": character_aliases(pack.character),
        }
        if pack.relation != rel_default:
            entry["relation"] = pack.relation
        characters.append(entry)

    manifest = {
        "pack": slug,
        "series": series or slug,
        "work": work,
        "relation_default": rel_default,
        "characters": characters,
    }
    (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with zipfile.ZipFile(tmp / "embeddings.npz", "w", zipfile.ZIP_DEFLATED) as dst_zf:
        for pack in group:
            _copy_npz_member(pack.pack_dir / "embeddings.npz", pack.embedding_key, dst_zf, pack.character_id)

    for pack in group:
        _copy_samples(pack, tmp / "samples" / pack.character_id)

    tmp.rename(target)
    return target


def merge_selected_character_packs(
    packs_dir: str | Path,
    *,
    character_ids: list[str],
    pack_name: str,
    series: str = "",
    work: str = "",
    relation_default: str = "known",
) -> dict[str, Any]:
    """Merge selected complete one-character packs into one series pack.

    This is the manual/admin sibling of ``auto_merge_series_packs``. It still
    refuses ambiguous or unsafe sources, but lets the admin provide a missing
    shared work for legacy single-character packs.
    """
    packs_path = Path(packs_dir)
    if not packs_path.exists():
        raise ValueError("character packs directory does not exist")

    requested = [str(cid or "").strip() for cid in character_ids if str(cid or "").strip()]
    if len(requested) != len(set(requested)):
        raise ValueError("duplicate character_id in selection")
    if len(requested) < 2:
        raise ValueError("select at least two characters")

    slug = _pack_slug(pack_name)
    series_slug = _pack_slug(series) if series.strip() else slug
    if (packs_path / f"{slug}.charpack").exists():
        raise FileExistsError(f"target pack already exists: {slug}.charpack")

    invalid_multi: dict[str, str] = {}
    requested_set = set(requested)
    for manifest_path in sorted(packs_path.glob("*.charpack/manifest.json")):
        manifest = _load_manifest(manifest_path)
        if manifest is None:
            continue
        characters = iter_manifest_characters(manifest)
        if len(characters) == 1:
            continue
        for item in characters:
            cid = character_id(item)
            if cid in requested_set:
                invalid_multi[cid] = manifest_path.parent.name

    singles = _collect_single_packs(packs_path, require_work=False)
    singles_by_id: dict[str, _SinglePack] = {}
    duplicate_singles: set[str] = set()
    for pack in singles:
        if pack.character_id in singles_by_id:
            duplicate_singles.add(pack.character_id)
        singles_by_id[pack.character_id] = pack

    selected: list[_SinglePack] = []
    for cid in requested:
        if cid in invalid_multi:
            raise ValueError(f"character {cid!r} is already in multi-character pack {invalid_multi[cid]}")
        if cid in duplicate_singles:
            raise ValueError(f"character {cid!r} has multiple single-character packs")
        pack = singles_by_id.get(cid)
        if pack is None:
            raise ValueError(f"character {cid!r} is not backed by a complete single-character pack")
        selected.append(pack)

    final_work = work.strip()
    if not final_work:
        source_works = {pack.work for pack in selected if pack.work}
        if len(source_works) == 1 and all(pack.work for pack in selected):
            final_work = str(next(iter(source_works)))
        else:
            raise ValueError("work is required when selected characters do not share one non-empty work")

    target = _write_series_pack(
        packs_path,
        slug,
        final_work,
        selected,
        series=series_slug,
        relation_default=relation_default,
    )
    archived = _archive_packs(selected, packs_path / ".merged" / slug)
    _L.info(
        "series pack manually merged | pack={} work={} characters={} archived={}",
        target.name,
        final_work,
        len(selected),
        archived,
    )
    return {
        "pack": slug,
        "series": series_slug,
        "work": final_work,
        "character_count": len(selected),
        "characters": [pack.character_id for pack in selected],
        "archived": archived,
    }


def auto_merge_series_packs(packs_dir: str | Path) -> dict[str, int]:
    """Merge safe one-character packs that share the same non-empty work.

    The bot runtime deliberately does not depend on numpy. ``embeddings.npz`` is
    a zip of ``.npy`` arrays, so this migrator copies members directly and lets
    the sidecar load the merged pack later.
    """
    packs_path = Path(packs_dir)
    if not packs_path.exists():
        return {"groups": 0, "merged": 0, "archived": 0, "characters": 0, "skipped": 0}

    singles = _collect_single_packs(packs_path)
    groups: dict[str, list[_SinglePack]] = {}
    for pack in singles:
        if pack.work:
            groups.setdefault(pack.work, []).append(pack)

    existing_series = _collect_existing_series(packs_path)
    stats = {"groups": 0, "merged": 0, "archived": 0, "characters": 0, "skipped": 0}
    for work, group in sorted(groups.items(), key=lambda item: item[0]):
        if len(group) < 2:
            continue
        stats["groups"] += 1
        group_ids = {pack.character_id for pack in group}
        existing = existing_series.get(work, [])
        complete = next(((path, ids) for path, ids in existing if group_ids.issubset(ids)), None)
        slug = _series_slug(work)
        archive_root = packs_path / ".merged" / slug
        if complete is not None:
            stats["archived"] += _archive_packs(group, archive_root)
            stats["characters"] += len(group)
            continue
        if existing:
            stats["skipped"] += len(group)
            _L.warning(
                "series pack auto-merge skipped partial existing pack | work={} singles={} existing={}",
                work,
                len(group),
                [path.name for path, _ids in existing],
            )
            continue
        try:
            target = _write_series_pack(packs_path, slug, work, group)
        except (OSError, zipfile.BadZipFile, KeyError, RuntimeError) as exc:
            stats["skipped"] += len(group)
            _L.warning("series pack auto-merge failed | work={} err={}", work, exc)
            continue
        stats["merged"] += 1
        stats["characters"] += len(group)
        stats["archived"] += _archive_packs(group, archive_root)
        _L.info("series pack auto-merged | pack={} work={} characters={}", target.name, work, len(group))

    return stats
