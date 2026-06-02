#!/usr/bin/env python3
"""Build a .charpack from reference images.

Layout: pass a root dir whose immediate subdirectories are characters, each
holding 5-10 reference images:

    refs/
      emu/        (凤笑梦 立绘/截图)
        a.png b.png ...
      friend_x/
        ...

Embeddings are extracted by the ccip-sidecar /build-series-pack endpoint (the
CCIP model and numpy live only in the sidecar). One mean vector per character
is written; the sidecar identifies by nearest difference.

Usage:
  uv run --with requests python tools/build_character_pack.py refs/ \
      --name mychars --relation self --work "My Series" \
      --sidecar http://localhost:8620 --out config/character_packs

  (requests is a dev-only dep for this build tool. The bot runtime venv stays
  free of the CCIP/numpy stack by design.)

The manifest's `relation` is an initial value; the per-bot source of truth is
the omubot character_recognition.db (admin-editable) — scan_and_sync only seeds
relation from the manifest when the character_id is new.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import zipfile
from pathlib import Path

import requests

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _land_charpack(zip_b64: str, pack_dir: str, out_dir: Path) -> Path:
    raw = base64.b64decode(zip_b64)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if any(n.startswith("/") or ".." in n for n in names):
            raise SystemExit("unsafe path in built pack")
        if not any(n.startswith(f"{pack_dir}/") for n in names):
            raise SystemExit("built pack has unexpected layout")
        zf.extractall(out_dir)
    return out_dir / pack_dir


def build(
    root: Path,
    *,
    name: str,
    relation: str,
    sidecar: str,
    out_dir: Path,
    work: str = "",
    series: str = "",
) -> Path:
    characters: list[dict[str, object]] = []
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    subdirs = sorted(p for p in root.iterdir() if p.is_dir())
    if not subdirs:
        raise SystemExit(f"no character subdirectories under {root}")

    for sub in subdirs:
        images = sorted(p for p in sub.iterdir() if p.suffix.lower() in _IMAGE_EXT)
        if not images:
            print(f"  - {sub.name}: no images, skipped")
            continue
        cid = sub.name
        characters.append({
            "character_id": cid,
            "name": cid,
            "aliases": [],
        })
        for image in images:
            files.append(("images", (f"{cid}_{image.name}", image.read_bytes(), "application/octet-stream")))
        print(f"  + {cid}: {len(images)} images")

    if not characters:
        raise SystemExit("no characters with images; aborting")

    resp = requests.post(
        f"{sidecar.rstrip('/')}/build-series-pack",
        data={
            "pack_name": name,
            "series": series,
            "work": work,
            "relation_default": relation,
            "characters_json": json.dumps(characters, ensure_ascii=False),
        },
        files=files,
        timeout=240,
    )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SystemExit(f"sidecar returned non-json response: HTTP {resp.status_code}") from exc
    if resp.status_code >= 400:
        raise SystemExit(f"sidecar build failed: {payload.get('detail') or resp.text[:200]}")
    return _land_charpack(payload["charpack_zip_b64"], payload["pack_dir"], out_dir)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a .charpack from reference images")
    ap.add_argument("root", type=Path, help="dir of per-character subdirectories")
    ap.add_argument("--name", required=True, help="pack name (-> <name>.charpack)")
    ap.add_argument("--relation", default="known", choices=["self", "friend", "known"])
    ap.add_argument("--work", default="", help="series/work display name inherited by characters")
    ap.add_argument("--series", default="", help="stable series slug; defaults to pack name")
    ap.add_argument("--sidecar", default="http://localhost:8620", help="ccip-sidecar base url")
    ap.add_argument("--out", type=Path, default=Path("config/character_packs"))
    args = ap.parse_args()

    pack = build(args.root, name=args.name, relation=args.relation,
                 sidecar=args.sidecar, out_dir=args.out, work=args.work, series=args.series)
    print(f"\nwrote {pack} ({(pack / 'embeddings.npz').stat().st_size} bytes npz)")
    print("reload sidecar: it auto-detects the new pack on next /identify or /health")


if __name__ == "__main__":
    main()
