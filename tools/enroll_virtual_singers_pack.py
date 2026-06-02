#!/usr/bin/env python3
"""Build Chinese/Japanese virtual singer CCIP series packs.

The roster lives in ``tools/virtual_singers_roster.py``.  We intentionally split
only by broad work: 中V and 日V.  Crypton singers that already exist in the PJSK
pack are enrolled again as home/original singers with ``vocaloid_*`` ids so the
bot can distinguish original VOCALOID imagery from PJSK imagery.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA}
SAMPLE_MAX = 256
VALID_RELATIONS = {"self", "friend", "known"}
SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")

MOEGIRL_API = "https://zh.moegirl.org.cn/api.php"
UTAITEDB_ARTISTS_API = "https://utaitedb.net/api/artists"

# Exact page-title allowlist.  Do not fall back to blind opensearch first hits:
# short names such as "苍穹" and "ONE" easily resolve to unrelated works.
MOEGIRL_TITLES: dict[str, list[str]] = {
    "luo_tianyi": ["洛天依"],
    "yan_he": ["言和"],
    "yuezheng_ling": ["乐正绫"],
    "yuezheng_longya": ["乐正龙牙"],
    "zhiyu_moke": ["徵羽摩柯"],
    "mo_qingxian": ["墨清弦"],
    "xingchen": ["星尘(平行四界)"],
    "hai_yi": ["海伊"],
    "cang_qiong": ["苍穹(平行四界)"],
    "chi_yu": ["赤羽"],
    "shi_an": ["诗岸"],
    "mu_xin": ["牧心"],
    "yongye_minus": ["永夜Minus"],
    "xia_yuyao": ["夏语遥"],
    "xin_hua": ["心华"],
    "dongfang_zhizi": ["东方栀子"],
    "gumi": ["GUMI from Vocaloid"],
    "ia": ["IA"],
    "kafu": ["可不"],
    "sekai": ["星界"],
    "rime": ["里命"],
    "coko": ["狐子"],
    "haru": ["羽累"],
    "v_flower": ["v flower"],
    "kyomachi_seika": ["京町精华"],
    "tsuina_chan": ["追傩酱"],
    "vocaloid_hatsune_miku": ["初音未来"],
    "vocaloid_megurine_luka": ["巡音流歌"],
    "vocaloid_meiko": ["MEIKO"],
    "vocaloid_kaito": ["KAITO"],
}

ROMAN_ALIASES: dict[str, list[str]] = {
    "luo_tianyi": ["Luo Tianyi"],
    "yan_he": ["Yanhe", "Yan He"],
    "yuezheng_ling": ["Yuezheng Ling"],
    "yuezheng_longya": ["Yuezheng Longya"],
    "zhiyu_moke": ["Zhiyu Moke"],
    "mo_qingxian": ["Mo Qingxian"],
    "xingchen": ["Stardust", "Xingchen"],
    "hai_yi": ["Haiyi", "Hai Yi"],
    "cang_qiong": ["Cangqiong"],
    "chi_yu": ["Chiyu", "Chi Yu"],
    "shi_an": ["Shian", "Shi An"],
    "mu_xin": ["Muxin", "Mu Xin"],
    "yongye_minus": ["Minus"],
    "xia_yuyao": ["Xia Yuyao"],
    "xin_hua": ["Xin Hua"],
    "dongfang_zhizi": ["Dongfang Zhizi"],
    "kamui_gakupo": ["Kamui Gakupo", "Gackpoid"],
    "otomachi_una": ["Otomachi Una"],
    "kasane_teto": ["Kasane Teto"],
    "yuzuki_yukari": ["Yuzuki Yukari"],
    "kizuna_akari": ["Kizuna Akari"],
    "kaai_yuki": ["Kaai Yuki"],
    "nekomura_iroha": ["Nekomura Iroha"],
    "tohoku_zunko": ["Tohoku Zunko"],
    "tohoku_kiritan": ["Tohoku Kiritan"],
    "kotonoha_akane": ["Kotonoha Akane"],
    "kotonoha_aoi": ["Kotonoha Aoi"],
    "koharu_rikka": ["Koharu Rikka"],
    "tsurumaki_maki": ["Tsurumaki Maki"],
    "natsuki_karin": ["Natsuki Karin"],
    "hanakuma_chifuyu": ["Hanakuma Chifuyu"],
    "kyomachi_seika": ["Kyomachi Seika"],
    "tsuina_chan": ["Tsuina-chan", "Tsuina Chan"],
    "vocaloid_hatsune_miku": ["Hatsune Miku", "Miku"],
    "vocaloid_kagamine_rin": ["Kagamine Rin", "Rin"],
    "vocaloid_kagamine_len": ["Kagamine Len", "Len"],
    "vocaloid_megurine_luka": ["Megurine Luka", "Luka"],
}

HOME_VOCALOID_IDS = {
    "hatsune_miku": "vocaloid_hatsune_miku",
    "kagamine_rin": "vocaloid_kagamine_rin",
    "kagamine_len": "vocaloid_kagamine_len",
    "megurine_luka": "vocaloid_megurine_luka",
    "meiko": "vocaloid_meiko",
    "kaito": "vocaloid_kaito",
}


@dataclass(frozen=True)
class CharacterDef:
    character_id: str
    name: str
    aliases: list[str]
    source_id: str
    query_names: list[str]
    context_label: str


@dataclass(frozen=True)
class PackDef:
    pack: str
    work: str
    characters: list[CharacterDef]


def slug(value: str) -> str:
    s = SLUG_RE.sub("_", value.strip()).strip("_")
    return s or "character"


def relation(value: str, *, default: str = "known") -> str:
    value = str(value or "").strip()
    return value if value in VALID_RELATIONS else default


def normalize_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())


def dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def load_roster(path: Path) -> tuple[list[CharacterDef], list[CharacterDef]]:
    spec = importlib.util.spec_from_file_location("virtual_singers_roster", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load roster from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    zh_chars = [
        _character_def(
            zh=zh,
            jp=jp,
            cid=cid,
            context_label=_zh_context_label(cid),
            prefer_jp_name=False,
            home_vocaloid=False,
        )
        for zh, jp, cid, _tier, _notes in module.ZH_V
    ]
    ja_chars = [
        _character_def(
            zh=zh,
            jp=jp,
            cid=cid,
            context_label=_ja_context_label(cid),
            prefer_jp_name=True,
            home_vocaloid=False,
        )
        for zh, jp, cid, _tier, _notes in module.JA_V
    ]
    ja_chars.extend(
        _character_def(
            zh=zh,
            jp=jp,
            cid=cid,
            context_label="日V / Crypton 本家",
            prefer_jp_name=True,
            home_vocaloid=True,
        )
        for zh, jp, cid, _tier, _notes in module.ALREADY_ENROLLED
    )
    _ensure_unique([*zh_chars, *ja_chars])
    return zh_chars, ja_chars


def _zh_context_label(cid: str) -> str:
    if cid in {"luo_tianyi", "yan_he", "yuezheng_ling", "yuezheng_longya", "zhiyu_moke", "mo_qingxian"}:
        return "中V / Vsinger"
    if cid in {"xingchen", "hai_yi", "cang_qiong", "chi_yu", "shi_an", "mu_xin", "yongye_minus"}:
        return "中V / 五维介质"
    if cid == "xia_yuyao":
        return "中V / E-CAPSULE"
    if cid in {"xin_hua", "dongfang_zhizi"}:
        return "中V / 中文虚拟歌姬"
    return "中V"


def _ja_context_label(cid: str) -> str:
    if cid in {"gumi", "kamui_gakupo", "lily", "otomachi_una", "mayu", "v_flower"}:
        return "日V / VOCALOID"
    if cid in {"ia", "one"}:
        return "日V / 1st Place"
    if cid in {
        "kasane_teto",
        "yuzuki_yukari",
        "kizuna_akari",
        "kaai_yuki",
        "nekomura_iroha",
        "tohoku_zunko",
        "tohoku_kiritan",
        "kotonoha_akane",
        "kotonoha_aoi",
        "koharu_rikka",
        "tsurumaki_maki",
        "natsuki_karin",
        "hanakuma_chifuyu",
        "kyomachi_seika",
        "tsuina_chan",
    }:
        return "日V / AHS・TOKYO6"
    if cid in {"kafu", "sekai", "rime", "coko", "haru"}:
        return "日V / KAMITSUBAKI 音乐的同位体"
    return "日V"


def _character_def(
    *,
    zh: str,
    jp: str,
    cid: str,
    context_label: str,
    prefer_jp_name: bool,
    home_vocaloid: bool,
) -> CharacterDef:
    character_id = HOME_VOCALOID_IDS[cid] if home_vocaloid else cid
    name = jp if prefer_jp_name else zh
    aliases = dedupe([
        zh,
        jp,
        *ROMAN_ALIASES.get(character_id, []),
        *ROMAN_ALIASES.get(cid, []),
        cid.replace("_", " "),
    ])
    query_names = dedupe([
        jp,
        zh,
        *ROMAN_ALIASES.get(character_id, []),
        *ROMAN_ALIASES.get(cid, []),
        cid.replace("_", " "),
    ])
    return CharacterDef(
        character_id=character_id,
        name=name,
        aliases=aliases,
        source_id=character_id,
        query_names=query_names,
        context_label=context_label,
    )


def _ensure_unique(characters: list[CharacterDef]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in characters:
        if item.character_id in seen:
            duplicates.append(item.character_id)
        seen.add(item.character_id)
    if duplicates:
        raise SystemExit(f"duplicate character_id: {duplicates}")


def image_response(resp: requests.Response) -> tuple[bytes, str] | None:
    content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if resp.status_code != 200 or not content_type.startswith("image/") or len(resp.content) < 1024:
        return None
    return resp.content, content_type


def download_image(sess: requests.Session, url: str) -> tuple[bytes, str] | None:
    try:
        resp = sess.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException:
        return None
    return image_response(resp)


def moegirl_page_image(sess: requests.Session, title: str) -> str | None:
    try:
        resp = sess.get(
            MOEGIRL_API,
            params={
                "action": "query",
                "format": "json",
                "prop": "pageimages",
                "pithumbsize": "1000",
                "titles": title,
            },
            headers=HEADERS,
            timeout=12,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
    except (requests.RequestException, ValueError):
        return None
    for page in pages.values():
        if isinstance(page, dict):
            source = page.get("thumbnail", {}).get("source")
            if source:
                return str(source)
    return None


def artist_names(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("name", "defaultName"):
        value = item.get(key)
        if value:
            names.append(str(value))
    raw_names = item.get("names")
    if isinstance(raw_names, list):
        for raw in raw_names:
            if isinstance(raw, dict) and raw.get("value"):
                names.append(str(raw["value"]))
            elif raw:
                names.append(str(raw))
    return dedupe(names)


def utaitedb_main_image(sess: requests.Session, character: CharacterDef) -> str | None:
    targets = {normalize_name(value) for value in [character.name, *character.aliases, *character.query_names]}
    seen_ids: set[int] = set()
    for query in character.query_names[:5]:
        try:
            resp = sess.get(
                UTAITEDB_ARTISTS_API,
                params={"query": query, "maxResults": 8, "fields": "MainPicture,Names"},
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except (requests.RequestException, ValueError):
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            artist_id = item.get("id")
            if isinstance(artist_id, int):
                if artist_id in seen_ids:
                    continue
                seen_ids.add(artist_id)
            names = {normalize_name(value) for value in artist_names(item)}
            picture = item.get("mainPicture") if isinstance(item.get("mainPicture"), dict) else {}
            url = picture.get("urlOriginal") or picture.get("urlThumb")
            if url and targets.intersection(names):
                return str(url)
    return None


def gather_images(sess: requests.Session, character: CharacterDef) -> list[tuple[str, bytes, str]]:
    selected: list[tuple[str, bytes, str]] = []
    urls: list[tuple[str, str]] = []

    ut_url = utaitedb_main_image(sess, character)
    if ut_url:
        urls.append(("utaitedb", ut_url))

    for title in MOEGIRL_TITLES.get(character.source_id, MOEGIRL_TITLES.get(character.character_id, [])):
        mg_url = moegirl_page_image(sess, title)
        if mg_url:
            urls.append((f"moegirl_{slug(title)}", mg_url))

    seen_urls: set[str] = set()
    for source, url in urls:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        image = download_image(sess, url)
        if image is None:
            continue
        data, content_type = image
        ext = ".png" if content_type == "image/png" else ".jpg"
        filename = f"{character.character_id}_{source}{ext}"
        selected.append((filename, data, content_type))
    return selected


def character_payloads(characters: list[CharacterDef]) -> list[dict[str, Any]]:
    return [
        {
            "character_id": item.character_id,
            "name": item.name,
            "aliases": item.aliases,
            "context_label": item.context_label,
        }
        for item in characters
    ]


def land_charpack(zip_b64: str, pack_dir: str, out_dir: Path) -> Path:
    raw = base64.b64decode(zip_b64)
    target = out_dir / pack_dir
    if target.exists():
        raise SystemExit(f"{target} already exists; move it away before rebuilding")
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if any(n.startswith("/") or ".." in n for n in names):
            raise SystemExit("unsafe path in built pack")
        if not any(n.startswith(f"{pack_dir}/") for n in names):
            raise SystemExit("built pack has unexpected layout")
        zf.extractall(out_dir)
    return target


def sample_jpeg(data: bytes) -> bytes | None:
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    image.thumbnail((SAMPLE_MAX, SAMPLE_MAX))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


def embed_image(
    sess: requests.Session,
    sidecar: str,
    filename: str,
    data: bytes,
    content_type: str,
    timeout: int,
) -> list[float]:
    resp = sess.post(
        f"{sidecar.rstrip('/')}/embed",
        files={"image": (filename, data, content_type)},
        timeout=timeout,
    )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SystemExit(f"sidecar /embed returned non-json response: HTTP {resp.status_code}") from exc
    if resp.status_code >= 400:
        raise SystemExit(f"sidecar /embed failed for {filename}: {payload.get('detail') or resp.text[:200]}")
    vector = payload.get("embedding")
    if not isinstance(vector, list) or not vector:
        raise SystemExit(f"sidecar /embed returned empty embedding for {filename}")
    return [float(v) for v in vector]


def matches_prefix(filename: str, prefix: str) -> bool:
    name = Path(filename or "").name
    if name == prefix:
        return True
    return any(name.startswith(f"{prefix}{sep}") for sep in ("_", "-", "."))


def build_pack_via_embed(
    *,
    sidecar: str,
    pack: PackDef,
    files: list[tuple[str, tuple[str, bytes, str]]],
    timeout: int,
) -> dict[str, Any]:
    import numpy as np

    sess = requests.Session()
    embed_timeout = max(120, min(timeout, 600))
    vectors: dict[str, Any] = {}
    manifest_characters: list[dict[str, Any]] = []
    samples_by_character: dict[str, list[bytes]] = {}
    total_embedded = 0

    image_files = [(payload[0], payload[1], payload[2]) for _, payload in files]
    for character in pack.characters:
        matched = [
            (name, data, content_type)
            for name, data, content_type in image_files
            if matches_prefix(name, character.character_id)
        ]
        if not matched:
            raise SystemExit(f"no images matched {character.character_id}")
        embedded_vectors = [
            embed_image(sess, sidecar, name, data, content_type, embed_timeout)
            for name, data, content_type in matched
        ]
        vectors[character.character_id] = np.mean(np.asarray(embedded_vectors, dtype=np.float32), axis=0).astype(
            np.float32
        )
        samples = [
            sample
            for sample in (sample_jpeg(data) for _, data, _ in matched[:3])
            if sample is not None
        ]
        samples_by_character[character.character_id] = samples
        total_embedded += len(embedded_vectors)
        manifest_characters.append({
            "character_id": character.character_id,
            "name": character.name,
            "embedding_key": character.character_id,
            "aliases": character.aliases,
            "context_label": character.context_label,
        })

    manifest = {
        "pack": pack.pack,
        "series": pack.pack,
        "work": pack.work,
        "relation_default": "known",
        "characters": manifest_characters,
    }
    npz_buf = io.BytesIO()
    np.savez(npz_buf, **vectors)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        root = f"{pack.pack}.charpack"
        zf.writestr(f"{root}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(f"{root}/embeddings.npz", npz_buf.getvalue())
        for cid, samples in samples_by_character.items():
            for idx, sample in enumerate(samples):
                zf.writestr(f"{root}/samples/{cid}/{idx}.jpg", sample)

    return {
        "charpack_zip_b64": base64.b64encode(zip_buf.getvalue()).decode("ascii"),
        "pack_dir": f"{pack.pack}.charpack",
        "pack": pack.pack,
        "series": pack.pack,
        "character_count": len(pack.characters),
        "embedded": total_embedded,
        "total": len(files),
        "samples": sum(len(samples) for samples in samples_by_character.values()),
        "dim": int(next(iter(vectors.values())).size),
        "api_version": "embed-fallback",
    }


def build_pack(
    *,
    sidecar: str,
    out_dir: Path,
    pack: PackDef,
    files: list[tuple[str, tuple[str, bytes, str]]],
    timeout: int,
) -> Path:
    characters_json = json.dumps(character_payloads(pack.characters), ensure_ascii=False)
    resp = requests.post(
        f"{sidecar.rstrip('/')}/build-series-pack",
        data={
            "pack_name": pack.pack,
            "series": pack.pack,
            "work": pack.work,
            "relation_default": "known",
            "characters_json": characters_json,
        },
        files=files,
        timeout=timeout,
    )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SystemExit(f"sidecar returned non-json response: HTTP {resp.status_code}") from exc
    if resp.status_code == 404:
        print(f"{pack.pack}: sidecar lacks /build-series-pack; falling back to /embed-driven packaging")
        payload = build_pack_via_embed(sidecar=sidecar, pack=pack, files=files, timeout=timeout)
        return land_charpack(payload["charpack_zip_b64"], payload["pack_dir"], out_dir)
    if resp.status_code >= 400:
        raise SystemExit(f"sidecar build failed for {pack.pack}: {payload.get('detail') or resp.text[:200]}")
    return land_charpack(payload["charpack_zip_b64"], payload["pack_dir"], out_dir)


def collect_pack_files(pack: PackDef) -> list[tuple[str, tuple[str, bytes, str]]]:
    sess = requests.Session()
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    missing: list[str] = []
    for character in pack.characters:
        images = gather_images(sess, character)
        if not images:
            missing.append(character.character_id)
            print(f"{pack.pack:20s} {character.character_id:24s} images=0 MISSING")
            continue
        for filename, data, content_type in images:
            files.append(("images", (filename, data, content_type)))
        sources = ",".join(Path(filename).stem.removeprefix(f"{character.character_id}_") for filename, _, _ in images)
        print(f"{pack.pack:20s} {character.character_id:24s} images={len(images)} sources={sources}")
    if missing:
        raise SystemExit(f"{pack.pack} missing images for: {missing}")
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description="Build virtual singer character packs")
    ap.add_argument("--roster", type=Path, default=Path("tools/virtual_singers_roster.py"))
    ap.add_argument("--sidecar", default="http://localhost:8620")
    ap.add_argument("--out", type=Path, default=Path("config/character_packs"))
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    zh_chars, ja_chars = load_roster(args.roster)
    packs = [
        PackDef(pack="zh_virtual_singers", work="中V", characters=zh_chars),
        PackDef(pack="ja_virtual_singers", work="日V", characters=ja_chars),
    ]
    total_images = 0
    for pack in packs:
        files = collect_pack_files(pack)
        total_images += len(files)
        print(f"\n{pack.pack}: characters={len(pack.characters)} images={len(files)}")
        if not args.dry_run:
            path = build_pack(sidecar=args.sidecar, out_dir=args.out, pack=pack, files=files, timeout=args.timeout)
            print(f"wrote {path}")
            print(f"embeddings: {(path / 'embeddings.npz').stat().st_size} bytes\n")
    print(f"done packs={len(packs)} images={total_images}")


if __name__ == "__main__":
    main()
