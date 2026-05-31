#!/usr/bin/env python3
"""Build a .charpack from reference images.

Layout: pass a root dir whose immediate subdirectories are characters, each
holding 5-10 reference images:

    refs/
      emu/        (凤笑梦 立绘/截图)
        a.png b.png ...
      friend_x/
        ...

Embeddings are extracted by the ccip-sidecar /embed endpoint (the CCIP model
lives only in the sidecar — this tool borrows it over HTTP). One mean vector
per character is written; the sidecar identifies by nearest difference.

Usage:
  uv run --with numpy --with requests python tools/build_character_pack.py refs/ \
      --name mychars --relation self \
      --sidecar http://localhost:8620 --out config/character_packs

  (numpy/requests are dev-only deps for this build tool — they are NOT in the
  bot runtime venv, which stays free of the CCIP stack by design. `--with`
  installs them ephemerally.)

The manifest's `relation` is an initial value; the per-bot source of truth is
the omubot character_recognition.db (admin-editable) — scan_and_sync only seeds
relation from the manifest when the character_id is new.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import requests

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _embed_image(sidecar: str, path: Path) -> np.ndarray | None:
    try:
        with path.open("rb") as fh:
            resp = requests.post(
                f"{sidecar.rstrip('/')}/embed",
                files={"image": (path.name, fh, "image/jpeg")},
                timeout=30,
            )
        resp.raise_for_status()
        vec = np.asarray(resp.json().get("embedding") or [], dtype=np.float32).reshape(-1)
        return vec if vec.size else None
    except (requests.RequestException, ValueError) as exc:
        print(f"  ! embed failed for {path.name}: {exc}", file=sys.stderr)
        return None


def build(root: Path, *, name: str, relation: str, sidecar: str, out_dir: Path) -> Path:
    characters: list[dict[str, object]] = []
    vectors: dict[str, np.ndarray] = {}
    subdirs = sorted(p for p in root.iterdir() if p.is_dir())
    if not subdirs:
        raise SystemExit(f"no character subdirectories under {root}")

    for sub in subdirs:
        images = sorted(p for p in sub.iterdir() if p.suffix.lower() in _IMAGE_EXT)
        if not images:
            print(f"  - {sub.name}: no images, skipped", file=sys.stderr)
            continue
        embeds = [v for v in (_embed_image(sidecar, img) for img in images) if v is not None]
        if not embeds:
            print(f"  - {sub.name}: all embeds failed, skipped", file=sys.stderr)
            continue
        mean_vec = np.mean(np.stack(embeds), axis=0).astype(np.float32)
        cid = sub.name
        vectors[cid] = mean_vec
        characters.append({
            "character_id": cid,
            "name": cid,
            "embedding_key": cid,
            "relation": relation,
            "aliases": [],
        })
        print(f"  + {cid}: {len(embeds)}/{len(images)} images -> dim {mean_vec.size}")

    if not characters:
        raise SystemExit("no characters embedded; aborting")

    pack_dir = out_dir / f"{name}.charpack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    np.savez(pack_dir / "embeddings.npz", **vectors)
    manifest = {"pack": name, "relation_default": relation, "characters": characters}
    (pack_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return pack_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a .charpack from reference images")
    ap.add_argument("root", type=Path, help="dir of per-character subdirectories")
    ap.add_argument("--name", required=True, help="pack name (-> <name>.charpack)")
    ap.add_argument("--relation", default="known", choices=["self", "friend", "known"])
    ap.add_argument("--sidecar", default="http://localhost:8620", help="ccip-sidecar base url")
    ap.add_argument("--out", type=Path, default=Path("config/character_packs"))
    args = ap.parse_args()

    pack = build(args.root, name=args.name, relation=args.relation,
                 sidecar=args.sidecar, out_dir=args.out)
    print(f"\nwrote {pack} ({(pack / 'embeddings.npz').stat().st_size} bytes npz)")
    print("reload sidecar: it auto-detects the new pack on next /identify or /health")


if __name__ == "__main__":
    main()
