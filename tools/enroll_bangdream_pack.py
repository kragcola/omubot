#!/usr/bin/env python3
"""Build the BanG Dream! series character pack with balanced visual forms.

The goal is not "as many images as possible"; it is a balanced centroid:
normal-proportion art and chat/sticker-like images should both be represented.

Default sampling:
  - all 60: official full art + official list art + official thumb
  - Bestdori-backed 40: + one game trim, one LiveSD, one stamp
  - Ave Mujica 5: + one stamp

This lands a single bangdream.charpack via the ccip-sidecar /build-series-pack
endpoint. Generated pack data under config/character_packs is runtime data and
is intentionally not committed.
"""
from __future__ import annotations

import argparse
import base64
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
OFFICIAL_ASSET_BASE = (
    "https://bang-dream.com/wordpress/wp-content/themes/"
    "bangdream-portal/assets/webp/common/artist"
)
BESTDORI_ASSET_BASE = "https://bestdori.com/assets/jp"
BESTDORI_CARDS_API = "https://bestdori.com/api/cards/all.5.json"
SAMPLE_MAX = 256
VALID_RELATIONS = {"self", "friend", "known"}
SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class RosterEntry:
    character_id: str
    band_slug: str
    official_slug: str
    bestdori_id: int | None = None


BAND_LABELS = {
    "poppinparty": "BanG Dream! / Poppin'Party",
    "afterglow": "BanG Dream! / Afterglow",
    "pastel-palettes": "BanG Dream! / Pastel*Palettes",
    "roselia": "BanG Dream! / Roselia",
    "hello-happy-world": "BanG Dream! / Hello, Happy World!",
    "morfonica": "BanG Dream! / Morfonica",
    "raise-a-suilen": "BanG Dream! / RAISE A SUILEN",
    "mygo": "BanG Dream! / MyGO!!!!!",
    "avemujica": "BanG Dream! / Ave Mujica",
    "yumemita": "BanG Dream! / 夢限大みゅーたいぷ",
    "millsage": "BanG Dream! / Ma'cherie",
    "ikka-dumb-rock": "BanG Dream! / Mugendai Mewtype",
}


ROSTER: list[RosterEntry] = [
    RosterEntry("toyama_kasumi", "poppinparty", "toyama-kasumi", 1),
    RosterEntry("hanazono_tae", "poppinparty", "hanazono-tae", 2),
    RosterEntry("ushigome_rimi", "poppinparty", "ushigome-rimi", 3),
    RosterEntry("yamabuki_saya", "poppinparty", "yamabuki-saya", 4),
    RosterEntry("ichigaya_arisa", "poppinparty", "ichigaya-arisa", 5),
    RosterEntry("mitake_ran", "afterglow", "mitake-ran", 6),
    RosterEntry("aoba_moca", "afterglow", "aoba-moca", 7),
    RosterEntry("uehara_himari", "afterglow", "uehara-himari", 8),
    RosterEntry("udagawa_tomoe", "afterglow", "udagawa-tomoe", 9),
    RosterEntry("hazawa_tsugumi", "afterglow", "hazawa-tsugumi", 10),
    RosterEntry("maruyama_aya", "pastel-palettes", "maruyama-aya", 16),
    RosterEntry("hikawa_hina", "pastel-palettes", "hikawa-hina", 17),
    RosterEntry("shirasagi_chisato", "pastel-palettes", "shirasagi-chisato", 18),
    RosterEntry("yamato_maya", "pastel-palettes", "yamato-maya", 19),
    RosterEntry("wakamiya_eve", "pastel-palettes", "wakamiya-eve", 20),
    RosterEntry("minato_yukina", "roselia", "minato-yukina", 21),
    RosterEntry("hikawa_sayo", "roselia", "hikawa-sayo", 22),
    RosterEntry("imai_lisa", "roselia", "imai-lisa", 23),
    RosterEntry("udagawa_ako", "roselia", "udagawa-ako", 24),
    RosterEntry("shirokane_rinko", "roselia", "shirokane-rinko", 25),
    RosterEntry("tsurumaki_kokoro", "hello-happy-world", "tsurumaki-kokoro", 11),
    RosterEntry("seta_kaoru", "hello-happy-world", "seta-kaoru", 12),
    RosterEntry("kitazawa_hagumi", "hello-happy-world", "kitazawa-hagumi", 13),
    RosterEntry("matsubara_kanon", "hello-happy-world", "matsubara-kanon", 14),
    RosterEntry("okusawa_misaki", "hello-happy-world", "michelle", 15),
    RosterEntry("kurata_mashiro", "morfonica", "kurata-mashiro", 26),
    RosterEntry("kirigaya_toko", "morfonica", "kirigaya-toko", 27),
    RosterEntry("hiromachi_nanami", "morfonica", "hiromachi-nanami", 28),
    RosterEntry("futaba_tsukushi", "morfonica", "futaba-tsukushi", 29),
    RosterEntry("yashio_rui", "morfonica", "yashio-rui", 30),
    RosterEntry("wakana_rei", "raise-a-suilen", "layer", 31),
    RosterEntry("asahi_rokka", "raise-a-suilen", "lock", 32),
    RosterEntry("sato_masuki", "raise-a-suilen", "masking", 33),
    RosterEntry("nyubara_reona", "raise-a-suilen", "pareo", 34),
    RosterEntry("tamade_chiyu", "raise-a-suilen", "chu2", 35),
    RosterEntry("takamatsu_tomori", "mygo", "takamatsu-tomori", 36),
    RosterEntry("chihaya_anon", "mygo", "chihaya-anon", 37),
    RosterEntry("kaname_rana", "mygo", "kaname-rana", 38),
    RosterEntry("nagasaki_soyo", "mygo", "nagasaki-soyo", 39),
    RosterEntry("shiina_taki", "mygo", "shiina-taki", 40),
    RosterEntry("misumi_uika", "avemujica", "misumi-uika", 41),
    RosterEntry("wakaba_mutsumi", "avemujica", "wakaba-mutsumi", 42),
    RosterEntry("yahata_umiri", "avemujica", "yahata-umiri", 43),
    RosterEntry("yutenji_nyamu", "avemujica", "yutenji-nyamu", 44),
    RosterEntry("togawa_sakiko", "avemujica", "togawa-sakiko", 45),
    RosterEntry("nakamachi_arale", "yumemita", "nakamachi-arale"),
    RosterEntry("miyanaga_nonoka", "yumemita", "miyanaga-nonoka"),
    RosterEntry("minetsuki_ritsu", "yumemita", "minetsuki-ritsu"),
    RosterEntry("fuji_miyako", "yumemita", "fuji-miyako"),
    RosterEntry("sengoku_yuno", "yumemita", "sengoku-yuno"),
    RosterEntry("shiomi_hotaru", "millsage", "shiomi-hotaru"),
    RosterEntry("izawa_natsume", "millsage", "izawa-natsume"),
    RosterEntry("kotohira_nagi", "millsage", "kotohira-nagi"),
    RosterEntry("hamasaki_mahoro", "millsage", "hamasaki-mahoro"),
    RosterEntry("izumi_houka", "millsage", "izumi-houka"),
    RosterEntry("suga_raika", "ikka-dumb-rock", "suga-raika"),
    RosterEntry("mahashi_miku", "ikka-dumb-rock", "mahashi-miku"),
    RosterEntry("yakura_yomogi", "ikka-dumb-rock", "yakura-yomogi"),
    RosterEntry("umezato_chieri", "ikka-dumb-rock", "umezato-chieri"),
    RosterEntry("shinomiya_shizuku", "ikka-dumb-rock", "shinomiya-shizuku"),
]


def fetch_json(url: str) -> Any:
    resp = requests.get(url, headers=HEADERS, timeout=45)
    resp.raise_for_status()
    return resp.json()


def download_image(sess: requests.Session, url: str) -> tuple[bytes, str] | None:
    try:
        resp = sess.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException:
        return None
    content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if resp.status_code != 200 or not content_type.startswith("image/") or len(resp.content) < 1024:
        return None
    return resp.content, content_type


def official_urls(entry: RosterEntry) -> list[tuple[str, str, str]]:
    base = f"{OFFICIAL_ASSET_BASE}/{entry.band_slug}"
    slug = entry.official_slug
    return [
        ("normal", "official_full", f"{base}/img_full_{slug}_01.webp"),
        ("normal", "official_list", f"{base}/img_list_{slug}.webp"),
        ("expression", "official_thumb", f"{base}/img_thumb_{slug}_01.webp"),
    ]


def bestdori_trim_urls(cards: dict[str, Any], bestdori_id: int, scan: int) -> list[tuple[str, str, str]]:
    selected: list[tuple[str, str, str]] = []
    entries = [
        (int(card_id), card)
        for card_id, card in cards.items()
        if isinstance(card, dict) and card.get("characterId") == bestdori_id and card.get("resourceSetName")
    ]
    for _, card in sorted(entries, reverse=True):
        resource = str(card["resourceSetName"])
        selected.append((
            "normal",
            f"trim_{resource}",
            f"{BESTDORI_ASSET_BASE}/characters/resourceset/{resource}_rip/trim_normal.png",
        ))
        if len(selected) >= scan:
            break
    return selected


def bestdori_sd_urls(bestdori_id: int, limit: int) -> list[tuple[str, str, str]]:
    selected: list[tuple[str, str, str]] = []
    for idx in range(1, limit + 1):
        sd_id = f"sd{bestdori_id:03d}{idx:03d}"
        selected.append((
            "expression",
            f"livesd_{sd_id}",
            f"{BESTDORI_ASSET_BASE}/characters/livesd/{sd_id}_rip/sdchara.png",
        ))
    return selected


def bestdori_stamp_urls(bestdori_id: int, limit: int) -> list[tuple[str, str, str]]:
    return [
        (
            "expression",
            f"stamp_{bestdori_id:03d}{idx:03d}",
            f"{BESTDORI_ASSET_BASE}/stamp/01_rip/stamp_{bestdori_id:03d}{idx:03d}.png",
        )
        for idx in range(1, limit + 1)
    ]


def load_characters(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("characters_json must be a JSON array")
    return data


def slug(value: str) -> str:
    s = SLUG_RE.sub("_", value.strip()).strip("_")
    return s or "character"


def relation(value: str, *, default: str = "known") -> str:
    value = str(value or "").strip()
    return value if value in VALID_RELATIONS else default


def aliases(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def matches_prefix(filename: str, prefix: str) -> bool:
    name = Path(filename or "").name
    if name == prefix:
        return True
    return any(name.startswith(f"{prefix}{sep}") for sep in ("_", "-", "."))


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


def build_pack_via_embed(
    *,
    sidecar: str,
    characters: list[dict[str, Any]],
    files: list[tuple[str, tuple[str, bytes, str]]],
    timeout: int,
) -> dict[str, Any]:
    import numpy as np

    pack = "bangdream"
    work = "BanG Dream!"
    series = "bangdream"
    rel_default = relation("known")
    sess = requests.Session()
    embed_timeout = max(120, min(timeout, 600))

    vectors: dict[str, Any] = {}
    manifest_characters: list[dict[str, Any]] = []
    samples_by_character: dict[str, list[bytes]] = {}
    per_character: list[dict[str, Any]] = []
    total_embedded = 0
    seen: set[str] = set()

    image_files = [(payload[0], payload[1], payload[2]) for _, payload in files]
    for raw in characters:
        raw_cid = str(raw.get("character_id") or "").strip()
        if not raw_cid:
            raise SystemExit("each character must include character_id")
        cid = slug(raw_cid)
        if cid in seen:
            raise SystemExit(f"duplicate character_id: {cid}")
        seen.add(cid)

        prefix = str(raw.get("file_prefix") or raw_cid).strip() or cid
        matched = [
            (name, data, content_type)
            for name, data, content_type in image_files
            if matches_prefix(name, prefix)
        ]
        if not matched:
            raise SystemExit(f"no images matched character {cid} with prefix {prefix!r}")

        embedded_vectors = [
            embed_image(sess, sidecar, name, data, content_type, embed_timeout)
            for name, data, content_type in matched
        ]
        vectors[cid] = np.mean(np.asarray(embedded_vectors, dtype=np.float32), axis=0).astype(np.float32)
        samples = [
            sample
            for sample in (sample_jpeg(data) for _, data, _ in matched[:3])
            if sample is not None
        ]
        samples_by_character[cid] = samples
        total_embedded += len(embedded_vectors)

        entry: dict[str, Any] = {
            "character_id": cid,
            "name": str(raw.get("name") or cid).strip() or cid,
            "embedding_key": cid,
            "aliases": aliases(raw.get("aliases")),
        }
        context_label = str(raw.get("context_label") or "").strip()
        if context_label:
            entry["context_label"] = context_label
        raw_relation = str(raw.get("relation") or "").strip()
        if raw_relation:
            rel = relation(raw_relation, default=rel_default)
            if rel != rel_default:
                entry["relation"] = rel
        raw_work = str(raw.get("work") or "").strip()
        if raw_work and raw_work != work:
            entry["work"] = raw_work
        manifest_characters.append(entry)
        per_character.append({
            "character_id": cid,
            "embedded": len(embedded_vectors),
            "total": len(matched),
            "samples": len(samples),
        })

    manifest = {
        "pack": pack,
        "series": series,
        "work": work,
        "relation_default": rel_default,
        "characters": manifest_characters,
    }
    npz_buf = io.BytesIO()
    np.savez(npz_buf, **vectors)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        root = f"{pack}.charpack"
        zf.writestr(f"{root}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(f"{root}/embeddings.npz", npz_buf.getvalue())
        for cid, samples in samples_by_character.items():
            for idx, sample in enumerate(samples):
                zf.writestr(f"{root}/samples/{cid}/{idx}.jpg", sample)

    return {
        "charpack_zip_b64": base64.b64encode(zip_buf.getvalue()).decode("ascii"),
        "pack_dir": f"{pack}.charpack",
        "pack": pack,
        "series": series,
        "character_count": len(manifest_characters),
        "embedded": total_embedded,
        "total": len(files),
        "samples": sum(len(samples) for samples in samples_by_character.values()),
        "dim": int(next(iter(vectors.values())).size),
        "characters": per_character,
        "api_version": "embed-fallback",
    }


def build_pack(
    *,
    sidecar: str,
    out_dir: Path,
    characters: list[dict[str, Any]],
    files: list[tuple[str, tuple[str, bytes, str]]],
    timeout: int,
) -> Path:
    resp = requests.post(
        f"{sidecar.rstrip('/')}/build-series-pack",
        data={
            "pack_name": "bangdream",
            "series": "bangdream",
            "work": "BanG Dream!",
            "relation_default": "known",
            "characters_json": json.dumps(characters, ensure_ascii=False),
        },
        files=files,
        timeout=timeout,
    )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SystemExit(f"sidecar returned non-json response: HTTP {resp.status_code}") from exc
    if resp.status_code == 404:
        print("sidecar lacks /build-series-pack; falling back to /embed-driven packaging")
        payload = build_pack_via_embed(
            sidecar=sidecar,
            characters=characters,
            files=files,
            timeout=timeout,
        )
        return land_charpack(payload["charpack_zip_b64"], payload["pack_dir"], out_dir)
    if resp.status_code >= 400:
        raise SystemExit(f"sidecar build failed: {payload.get('detail') or resp.text[:200]}")
    return land_charpack(payload["charpack_zip_b64"], payload["pack_dir"], out_dir)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build bangdream.charpack from official/Bestdori images")
    ap.add_argument("--characters", type=Path, default=Path("docs/character-packs/bangdream-characters.json"))
    ap.add_argument("--sidecar", default="http://localhost:8620")
    ap.add_argument("--out", type=Path, default=Path("config/character_packs"))
    ap.add_argument("--card-trims", type=int, default=1)
    ap.add_argument("--card-scan", type=int, default=12)
    ap.add_argument("--sd", type=int, default=1)
    ap.add_argument("--stamps", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    characters = load_characters(args.characters)
    roster_by_id = {entry.character_id: entry for entry in ROSTER}
    for item in characters:
        cid = str(item.get("character_id") or "")
        roster_entry = roster_by_id.get(cid)
        if roster_entry is not None:
            item.setdefault("context_label", BAND_LABELS.get(roster_entry.band_slug, "BanG Dream!"))
    known_ids = {str(item.get("character_id") or "") for item in characters}
    missing = [entry.character_id for entry in ROSTER if entry.character_id not in known_ids]
    if missing:
        raise SystemExit(f"roster entries missing from characters_json: {missing}")
    if len(characters) != len(ROSTER):
        raise SystemExit(f"expected {len(ROSTER)} characters_json entries, got {len(characters)}")

    cards = fetch_json(BESTDORI_CARDS_API)
    sess = requests.Session()
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    total_normal = 0
    total_expression = 0

    for entry in ROSTER:
        candidates = official_urls(entry)
        if entry.bestdori_id and entry.bestdori_id <= 40:
            candidates.extend(bestdori_trim_urls(cards, entry.bestdori_id, args.card_scan))
            candidates.extend(bestdori_sd_urls(entry.bestdori_id, args.sd))
        if entry.bestdori_id:
            candidates.extend(bestdori_stamp_urls(entry.bestdori_id, args.stamps))

        per_kind = {"normal": 0, "expression": 0}
        per_source: list[str] = []
        trim_downloaded = 0
        for kind, source, url in candidates:
            if source.startswith("trim_") and trim_downloaded >= args.card_trims:
                continue
            image = download_image(sess, url)
            if image is None:
                continue
            data, content_type = image
            ext = ".png" if content_type == "image/png" else ".webp"
            filename = f"{entry.character_id}_{source}{ext}"
            files.append(("images", (filename, data, content_type)))
            per_kind[kind] += 1
            if source.startswith("trim_"):
                trim_downloaded += 1
            per_source.append(source)

        if per_kind["normal"] < 1 or per_kind["expression"] < 1:
            raise SystemExit(f"{entry.character_id} has unbalanced/empty forms: {per_kind}")
        total_normal += per_kind["normal"]
        total_expression += per_kind["expression"]
        print(
            f"{entry.character_id:22s} normal={per_kind['normal']} "
            f"expression={per_kind['expression']} sources={','.join(per_source)}"
        )

    print(f"\nimages={len(files)} normal={total_normal} expression={total_expression}")
    if args.dry_run:
        return

    pack = build_pack(
        sidecar=args.sidecar,
        out_dir=args.out,
        characters=characters,
        files=files,
        timeout=args.timeout,
    )
    print(f"\nwrote {pack}")
    print(f"embeddings: {(pack / 'embeddings.npz').stat().st_size} bytes")


if __name__ == "__main__":
    main()
