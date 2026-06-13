#!/usr/bin/env python3
"""Build the Project SEKAI CCIP series pack with auditable training stats.

This is the series-pack successor to ``batch_enroll_pjsk.py``.  It builds the
current 26-character ``project_sekai.charpack`` in one pass, including the two
early self/friend characters that were merged into the series pack later.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import time
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

_PJSK_SPEC = importlib.util.spec_from_file_location(
    "batch_enroll_pjsk",
    Path(__file__).resolve().with_name("batch_enroll_pjsk.py"),
)
if _PJSK_SPEC is None or _PJSK_SPEC.loader is None:
    raise RuntimeError("cannot load batch_enroll_pjsk.py")
pjsk = importlib.util.module_from_spec(_PJSK_SPEC)
_PJSK_SPEC.loader.exec_module(pjsk)

PACK_NAME = "project_sekai"
STAMP_RE = re.compile(r"^File:Stamp\d+\.png$")
REQUESTED_FORM_BUCKETS = ("full_body", "normal_proportion", "chibi", "expression")
SAMPLE_MAX = 256
API_RETRIES = 3
API_RETRY_BACKOFF = 1.2


def api_get(params: dict[str, str]) -> dict[str, Any]:
    url = pjsk.WIKI_API + "?" + urllib.parse.urlencode(params)
    last_output = ""
    for attempt in range(1, API_RETRIES + 1):
        result = subprocess.run(
            ["curl", "-s", "-m", "30", "-A", pjsk.UA, "-e", pjsk.WIKI_REFERER, url],
            capture_output=True,
            text=True,
            timeout=40,
        )
        last_output = result.stdout[:200]
        if result.returncode == 0 and result.stdout.lstrip().startswith("{"):
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass
        if attempt < API_RETRIES:
            time.sleep(API_RETRY_BACKOFF * attempt)
    raise RuntimeError(f"Sekaipedia API returned non-json response for {url[:120]}: {last_output!r}")


def resolve_urls(titles: list[str]) -> list[str]:
    urls: list[str] = []
    for idx in range(0, len(titles), 25):
        batch = titles[idx:idx + 25]
        data = api_get({
            "action": "query",
            "titles": "|".join(batch),
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json",
        })
        for page in data.get("query", {}).get("pages", {}).values():
            image_info = page.get("imageinfo")
            if image_info and image_info[0].get("url"):
                urls.append(str(image_info[0]["url"]))
    return urls


def cutout_urls(full_name: str, limit: int) -> list[str]:
    data = api_get({
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:Cutouts of {full_name}",
        "cmlimit": "60",
        "cmtype": "file",
        "format": "json",
    })
    titles = [str(item["title"]) for item in data.get("query", {}).get("categorymembers", [])]
    plain = [
        title
        for title in titles
        if not any(term in title.lower() for term in ("trained", "gacha", "event"))
    ]
    return resolve_urls((plain or titles)[:limit])


def chibi_urls(given: str) -> list[str]:
    return resolve_urls([f"File:{given.capitalize()}-chibi-circle.png"])


def mysekai_urls(given: str) -> list[str]:
    titles = [f"File:MySEKAI {given} {view}.png" for view in pjsk.MYSEKAI_VIEWS]
    return resolve_urls(titles)


@dataclass(frozen=True)
class CharacterDef:
    full_name: str
    name: str
    character_id: str
    relation: str
    given: str


ROSTER: list[CharacterDef] = [
    CharacterDef("Hoshino Ichika", "星乃一歌", "hoshino_ichika", "known", "ichika"),
    CharacterDef("Tenma Saki", "天马咲希", "tenma_saki", "known", "saki"),
    CharacterDef("Mochizuki Honami", "望月穗波", "mochizuki_honami", "known", "honami"),
    CharacterDef("Hinomori Shiho", "日野森志步", "hinomori_shiho", "known", "shiho"),
    CharacterDef("Hanasato Minori", "花里实乃理", "hanasato_minori", "known", "minori"),
    CharacterDef("Kiritani Haruka", "桐谷遥", "kiritani_haruka", "known", "haruka"),
    CharacterDef("Momoi Airi", "桃井爱莉", "momoi_airi", "known", "airi"),
    CharacterDef("Hinomori Shizuku", "日野森雫", "hinomori_shizuku", "known", "shizuku"),
    CharacterDef("Azusawa Kohane", "小豆泽心羽", "azusawa_kohane", "known", "kohane"),
    CharacterDef("Shiraishi An", "白石杏", "shiraishi_an", "known", "an"),
    CharacterDef("Shinonome Akito", "东云彰人", "shinonome_akito", "known", "akito"),
    CharacterDef("Aoyagi Toya", "青柳冬弥", "aoyagi_toya", "known", "toya"),
    CharacterDef("Tenma Tsukasa", "天马司", "tenma_tsukasa", "known", "tsukasa"),
    CharacterDef("Otori Emu", "凤笑梦", "fengxiaomeng", "self", "emu"),
    CharacterDef("Kusanagi Nene", "草薙宁宁", "kusanagi_nene", "known", "nene"),
    CharacterDef("Kamishiro Rui", "神代类", "kamishiro_rui", "known", "rui"),
    CharacterDef("Yoisaki Kanade", "宵崎奏", "yoisaki_kanade", "known", "kanade"),
    CharacterDef("Asahina Mafuyu", "朝比奈真冬", "asahina_mafuyu", "known", "mafuyu"),
    CharacterDef("Shinonome Ena", "东云绘名", "shinonome_ena", "known", "ena"),
    CharacterDef("Akiyama Mizuki", "晓山瑞希", "xiaoshanruixi", "friend", "mizuki"),
    CharacterDef("Hatsune Miku", "初音未来", "hatsune_miku", "known", "miku"),
    CharacterDef("Kagamine Rin", "镜音铃", "kagamine_rin", "known", "rin"),
    CharacterDef("Kagamine Len", "镜音连", "kagamine_len", "known", "len"),
    CharacterDef("Megurine Luka", "巡音流歌", "megurine_luka", "known", "luka"),
    CharacterDef("MEIKO", "MEIKO", "meiko", "known", "meiko"),
    CharacterDef("KAITO", "KAITO", "kaito", "known", "kaito"),
]


def cache_paths(cache_dir: Path, url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.bin", cache_dir / f"{digest}.json"


def read_cached(cache_dir: Path | None, url: str) -> bytes | None:
    if cache_dir is None:
        return None
    data_path, meta_path = cache_paths(cache_dir, url)
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        return data_path.read_bytes()
    except OSError:
        return None


def write_cached(cache_dir: Path | None, url: str, data: bytes) -> None:
    if cache_dir is None:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        data_path, meta_path = cache_paths(cache_dir, url)
        data_path.write_bytes(data)
        meta_path.write_text(json.dumps({"url": url}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def download_url(url: str, cache_dir: Path | None) -> bytes | None:
    cached = read_cached(cache_dir, url)
    if cached is not None:
        return cached

    import contextlib
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        if pjsk._curl_bytes(url, path) and os.path.getsize(path) > 1024:
            data = Path(path).read_bytes()
            write_cached(cache_dir, url, data)
            return data
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    return None


def stamp_file_titles(full_name: str) -> list[str]:
    title = f"List of {full_name} stamps"
    data = api_get({
        "action": "query",
        "prop": "images",
        "titles": title,
        "imlimit": "500",
        "format": "json",
    })
    titles: list[str] = []
    for page in data.get("query", {}).get("pages", {}).values():
        for item in page.get("images", []) or []:
            raw = str(item.get("title") or "")
            if STAMP_RE.match(raw):
                titles.append(raw)
    return titles


def unique_stamp_titles(characters: list[CharacterDef]) -> dict[str, list[str]]:
    by_character = {character.character_id: stamp_file_titles(character.full_name) for character in characters}
    counts: dict[str, int] = {}
    for titles in by_character.values():
        for title in titles:
            counts[title] = counts.get(title, 0) + 1
    return {
        cid: [title for title in titles if counts.get(title, 0) == 1]
        for cid, titles in by_character.items()
    }


def file_urls(titles: list[str]) -> list[str]:
    return resolve_urls(titles)


def gather_character_images(
    character: CharacterDef,
    *,
    per_char: int,
    stamps: int,
    stamp_titles_by_id: dict[str, list[str]],
    cache_dir: Path | None,
) -> list[tuple[str, bytes, str]]:
    selected: list[tuple[str, bytes, str]] = []
    source_urls: list[tuple[str, str]] = []

    source_urls.extend(
        (f"cutout_{idx:02d}", url)
        for idx, url in enumerate(cutout_urls(character.full_name, per_char))
    )
    source_urls.extend((f"chibi_{idx:02d}", url) for idx, url in enumerate(chibi_urls(character.given)))
    source_urls.extend(
        (f"mysekai_{view}", url)
        for view, url in zip(pjsk.MYSEKAI_VIEWS, mysekai_urls(character.given), strict=False)
    )
    stamp_titles = stamp_titles_by_id.get(character.character_id, [])[:stamps]
    source_urls.extend(
        (f"stamp_{Path(title).stem.lower()}", url)
        for title, url in zip(stamp_titles, file_urls(stamp_titles), strict=False)
    )

    for source, url in source_urls:
        data = download_url(url, cache_dir)
        if data is None:
            continue
        selected.append((f"{character.character_id}_{source}.png", data, source))
    return selected


def source_forms(source: str) -> tuple[str, ...]:
    if source.startswith("cutout_"):
        return ("full_body", "normal_proportion")
    if source.startswith("chibi_"):
        return ("chibi",)
    if source.startswith("stamp_"):
        return ("expression",)
    if source.startswith("mysekai_"):
        return ("normal_proportion",)
    return ("other",)


def training_stats(images: list[tuple[str, bytes, str]]) -> dict[str, Any]:
    forms: dict[str, int] = {}
    sources: list[str] = []
    for _filename, _data, source in images:
        sources.append(source)
        for form in source_forms(source):
            forms[form] = forms.get(form, 0) + 1
    return {
        "image_count": len(images),
        "forms": forms,
        "sources": sources,
        "missing_forms": [form for form in REQUESTED_FORM_BUCKETS if forms.get(form, 0) < 1],
    }


def character_payloads(stats_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for character in ROSTER:
        payload: dict[str, Any] = {
            "character_id": character.character_id,
            "name": character.name,
            "relation": character.relation,
            "context_label": pjsk.PJSK_CONTEXT_LABELS.get(character.character_id, pjsk.PJSK_WORK),
            "training_stats": stats_by_id[character.character_id],
        }
        payloads.append(payload)
    return payloads


def land_charpack(zip_b64: str, pack_dir: str, out_dir: Path) -> Path:
    raw = base64.b64decode(zip_b64)
    target = out_dir / pack_dir
    if target.exists():
        raise SystemExit(f"{target} already exists; move it away before rebuilding")
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if any(name.startswith("/") or ".." in name for name in names):
            raise SystemExit("unsafe path in built pack")
        if not any(name.startswith(f"{pack_dir}/") for name in names):
            raise SystemExit("built pack has unexpected layout")
        zf.extractall(out_dir)
    return target


def build_pack(
    *,
    sidecar: str,
    out_dir: Path,
    files: list[tuple[str, tuple[str, bytes, str]]],
    stats_by_id: dict[str, dict[str, Any]],
    timeout: int,
) -> Path:
    resp = requests.post(
        f"{sidecar.rstrip('/')}/build-series-pack",
        data={
            "pack_name": PACK_NAME,
            "series": PACK_NAME,
            "work": pjsk.PJSK_WORK,
            "relation_default": "known",
            "characters_json": json.dumps(character_payloads(stats_by_id), ensure_ascii=False),
        },
        files=files,
        timeout=timeout,
    )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SystemExit(f"sidecar returned non-json response: HTTP {resp.status_code}") from exc
    if resp.status_code >= 400:
        raise SystemExit(f"sidecar build failed: {payload.get('detail') or resp.text[:200]}")
    return land_charpack(payload["charpack_zip_b64"], payload["pack_dir"], out_dir)


def collect_pack_files(
    *,
    per_char: int,
    stamps: int,
    cache_dir: Path | None,
) -> tuple[list[tuple[str, tuple[str, bytes, str]]], dict[str, dict[str, Any]]]:
    stamp_titles_by_id = unique_stamp_titles(ROSTER)
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    stats_by_id: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    seen_ids: set[str] = set()

    for character in ROSTER:
        if character.character_id in seen_ids:
            raise SystemExit(f"duplicate character_id: {character.character_id}")
        seen_ids.add(character.character_id)

        images = gather_character_images(
            character,
            per_char=per_char,
            stamps=stamps,
            stamp_titles_by_id=stamp_titles_by_id,
            cache_dir=cache_dir,
        )
        if not images:
            missing.append(character.character_id)
            print(f"{character.character_id:22s} images=0 MISSING")
            continue
        for filename, data, _source in images:
            files.append(("images", (filename, data, "image/png")))
        stats = training_stats(images)
        stats_by_id[character.character_id] = stats
        missing_forms = stats["missing_forms"]
        print(
            f"{character.character_id:22s} images={stats['image_count']:2d} "
            f"forms={stats['forms']} missing_forms={','.join(missing_forms) or '-'}"
        )

    if missing:
        raise SystemExit(f"{PACK_NAME} missing images for: {missing}")
    return files, stats_by_id


def main() -> None:
    ap = argparse.ArgumentParser(description="Build project_sekai.charpack with training_stats")
    ap.add_argument("--sidecar", default="http://localhost:8620")
    ap.add_argument("--out", type=Path, default=Path("config/character_packs"))
    ap.add_argument("--per-char", type=int, default=10)
    ap.add_argument("--stamps", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--image-cache-dir", type=Path, default=Path(".cache/character-pack-images/project_sekai"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files, stats_by_id = collect_pack_files(
        per_char=args.per_char,
        stamps=args.stamps,
        cache_dir=args.image_cache_dir,
    )
    print(f"\n{PACK_NAME}: characters={len(ROSTER)} images={len(files)}")
    if args.dry_run:
        return

    path = build_pack(
        sidecar=args.sidecar,
        out_dir=args.out,
        files=files,
        stats_by_id=stats_by_id,
        timeout=args.timeout,
    )
    print(f"wrote {path}")
    print(f"embeddings: {(path / 'embeddings.npz').stat().st_size} bytes")


if __name__ == "__main__":
    main()
