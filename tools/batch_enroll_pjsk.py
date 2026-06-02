#!/usr/bin/env python3
"""Batch-enroll all Project Sekai characters into CCIP — multi-form.

Each PJSK character appears in three visual forms that matter for chat
recognition:
  1. 正比立绘    — normal-proportion 2D card art   (Category:Cutouts of <Full Name>)
  2. 表情包 Q 版 — chibi / SD                       (<Given>-chibi-circle.png)
  3. My Sekai 3D — "tofu" 3D render                 (MySEKAI <given> front/back/left/right.png)

Empirically (sidecar CCIP difference matrix, 2026-06-01) a single character's
art + chibi + frontal-3D cluster tightly (pairwise diff <=0.12) while different
characters stay >=0.36 apart, so mean-pooling all forms into ONE centroid does
*not* dilute. Pooling the 3D side/back profiles in additionally rescues those
(art-only centroid misses them at ~0.22; pooled lands ~0.15) without bringing
other characters near the 0.1785 threshold. So we pull every reachable form and
hand them all to /build-pack as one character.

Sourcing notes:
  - Images come from Sekaipedia (MediaWiki) -> static.wikitide.net. Hotlink
    protection requires a Referer header (cutouts 403 without it).
  - The 20 human characters expose deterministic given-name files for chibi/3D.
    The 6 Virtual Singers do NOT (their chibi/3D are unit-prefixed, e.g.
    "MySEKAI 25ji miku front"); we attempt the plain name and silently skip
    the misses — their abundant cutouts already recognize all 2D forms.

Usage:
  uv run --with requests python tools/batch_enroll_pjsk.py \
      --admin http://localhost:8081 --token admin --per-char 10
  # pilot a few, re-enrolling even if already present:
  uv run --with requests python tools/batch_enroll_pjsk.py --force \
      --only tenma_tsukasa,azusawa_kohane,yoisaki_kanade
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time

import requests

WIKI_API = "https://www.sekaipedia.org/w/api.php"
WIKI_REFERER = "https://www.sekaipedia.org/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA}

MYSEKAI_VIEWS = ("front", "left", "right", "back")  # 3D tofu orientations
PJSK_WORK = "プロジェクトセカイ カラフルステージ！"  # series/出处, shared by all PJSK chars

PJSK_CONTEXT_LABELS = {
    "aoyagi_toya": "Project SEKAI / Vivid BAD SQUAD",
    "asahina_mafuyu": "Project SEKAI / 25時、ナイトコードで。",
    "azusawa_kohane": "Project SEKAI / Vivid BAD SQUAD",
    "fengxiaomeng": "Project SEKAI / Wonderlands×Showtime",
    "hanasato_minori": "Project SEKAI / MORE MORE JUMP!",
    "hatsune_miku": "Project SEKAI / Virtual Singer",
    "hinomori_shiho": "Project SEKAI / Leo/need",
    "hinomori_shizuku": "Project SEKAI / MORE MORE JUMP!",
    "hoshino_ichika": "Project SEKAI / Leo/need",
    "kagamine_len": "Project SEKAI / Virtual Singer",
    "kagamine_rin": "Project SEKAI / Virtual Singer",
    "kaito": "Project SEKAI / Virtual Singer",
    "kamishiro_rui": "Project SEKAI / Wonderlands×Showtime",
    "kiritani_haruka": "Project SEKAI / MORE MORE JUMP!",
    "kusanagi_nene": "Project SEKAI / Wonderlands×Showtime",
    "megurine_luka": "Project SEKAI / Virtual Singer",
    "meiko": "Project SEKAI / Virtual Singer",
    "mochizuki_honami": "Project SEKAI / Leo/need",
    "momoi_airi": "Project SEKAI / MORE MORE JUMP!",
    "shinonome_akito": "Project SEKAI / Vivid BAD SQUAD",
    "shinonome_ena": "Project SEKAI / 25時、ナイトコードで。",
    "shiraishi_an": "Project SEKAI / Vivid BAD SQUAD",
    "tenma_saki": "Project SEKAI / Leo/need",
    "tenma_tsukasa": "Project SEKAI / Wonderlands×Showtime",
    "xiaoshanruixi": "Project SEKAI / 25時、ナイトコードで。",
    "yoisaki_kanade": "Project SEKAI / 25時、ナイトコードで。",
}

# (sekaipedia full name, 中文名, character_id, relation, given-name for chibi/3D)
# Already enrolled (skip unless --force): Otori Emu=凤笑梦, Akiyama Mizuki=晓山瑞希.
ROSTER = [
    # Leo/need
    ("Hoshino Ichika", "星乃一歌", "hoshino_ichika", "known", "ichika"),
    ("Tenma Saki", "天马咲希", "tenma_saki", "known", "saki"),
    ("Mochizuki Honami", "望月穗波", "mochizuki_honami", "known", "honami"),
    ("Hinomori Shiho", "日野森志步", "hinomori_shiho", "known", "shiho"),
    # MORE MORE JUMP!
    ("Hanasato Minori", "花里实乃理", "hanasato_minori", "known", "minori"),
    ("Kiritani Haruka", "桐谷遥", "kiritani_haruka", "known", "haruka"),
    ("Momoi Airi", "桃井爱莉", "momoi_airi", "known", "airi"),
    ("Hinomori Shizuku", "日野森雫", "hinomori_shizuku", "known", "shizuku"),
    # Vivid BAD SQUAD
    ("Azusawa Kohane", "小豆泽心羽", "azusawa_kohane", "known", "kohane"),
    ("Shiraishi An", "白石杏", "shiraishi_an", "known", "an"),
    ("Shinonome Akito", "东云彰人", "shinonome_akito", "known", "akito"),
    ("Aoyagi Toya", "青柳冬弥", "aoyagi_toya", "known", "toya"),
    # Wonderlands×Showtime (Emu already enrolled)
    ("Tenma Tsukasa", "天马司", "tenma_tsukasa", "known", "tsukasa"),
    ("Kusanagi Nene", "草薙宁宁", "kusanagi_nene", "known", "nene"),
    ("Kamishiro Rui", "神代类", "kamishiro_rui", "known", "rui"),
    # 25時、Nightcord (Mizuki already enrolled)
    ("Yoisaki Kanade", "宵崎奏", "yoisaki_kanade", "known", "kanade"),
    ("Asahina Mafuyu", "朝比奈真冬", "asahina_mafuyu", "known", "mafuyu"),
    ("Shinonome Ena", "东云绘名", "shinonome_ena", "known", "ena"),
    # Virtual Singers (chibi/3D are unit-prefixed -> plain-name attempt, skip misses)
    ("Hatsune Miku", "初音未来", "hatsune_miku", "known", "miku"),
    ("Kagamine Rin", "镜音铃", "kagamine_rin", "known", "rin"),
    ("Kagamine Len", "镜音连", "kagamine_len", "known", "len"),
    ("Megurine Luka", "巡音流歌", "megurine_luka", "known", "luka"),
    ("MEIKO", "MEIKO", "meiko", "known", "meiko"),
    ("KAITO", "KAITO", "kaito", "known", "kaito"),
]


# --- external fetches: curl with Referer (hotlink protection) -----------------

def _curl_json(url: str) -> dict:
    r = subprocess.run(
        ["curl", "-s", "-m", "30", "-A", UA, "-e", WIKI_REFERER, url],
        capture_output=True, text=True, timeout=40,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"curl failed ({r.returncode}) for {url[:80]}")
    return json.loads(r.stdout)


def _curl_bytes(url: str, dest: str) -> bool:
    r = subprocess.run(
        ["curl", "-s", "-m", "25", "-A", UA, "-e", WIKI_REFERER, "-o", dest, url],
        capture_output=True, timeout=35,
    )
    return r.returncode == 0


def _api_get(params: dict) -> dict:
    import urllib.parse
    return _curl_json(WIKI_API + "?" + urllib.parse.urlencode(params))


def resolve_urls(titles: list[str]) -> list[str]:
    """Resolve File: titles to direct image URLs via imageinfo (batched).

    Silently drops titles that don't exist (missing -1 pages) — used for the
    optional chibi/3D forms where Virtual Singers lack plain-name files."""
    urls: list[str] = []
    for i in range(0, len(titles), 25):
        batch = titles[i:i + 25]
        data = _api_get({
            "action": "query", "titles": "|".join(batch),
            "prop": "imageinfo", "iiprop": "url", "format": "json",
        })
        for p in data.get("query", {}).get("pages", {}).values():
            ii = p.get("imageinfo")
            if ii and ii[0].get("url"):
                urls.append(ii[0]["url"])
    return urls


def cutout_urls(full_name: str, limit: int) -> list[str]:
    """正比立绘: plain card cutouts (exclude trained/gacha/event variants)."""
    data = _api_get({
        "action": "query", "list": "categorymembers",
        "cmtitle": f"Category:Cutouts of {full_name}",
        "cmlimit": "60", "cmtype": "file", "format": "json",
    })
    titles = [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
    plain = [t for t in titles
             if not any(x in t.lower() for x in ("trained", "gacha", "event"))]
    return resolve_urls((plain or titles)[:limit])


def chibi_urls(given: str) -> list[str]:
    """表情包 Q 版: the per-character chibi circle render (capitalized given)."""
    return resolve_urls([f"File:{given.capitalize()}-chibi-circle.png"])


def mysekai_urls(given: str) -> list[str]:
    """My Sekai 3D 豆腐人: front/left/right/back tofu renders (lowercase given)."""
    return resolve_urls([f"File:MySEKAI {given} {v}.png" for v in MYSEKAI_VIEWS])


def collect_images(full_name: str, given: str, per_char: int) -> dict[str, list]:
    """Gather all reachable forms. Returns {form: [(name, bytes), ...]}."""
    import contextlib
    import os
    import tempfile

    def _download(urls: list[str]) -> list[tuple[str, bytes]]:
        out: list[tuple[str, bytes]] = []
        for idx, u in enumerate(urls):
            name = u.rsplit("/", 1)[-1] or f"img_{idx}.png"
            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            try:
                if _curl_bytes(u, path) and os.path.getsize(path) > 1024:
                    with open(path, "rb") as f:
                        out.append((name, f.read()))
            finally:
                with contextlib.suppress(OSError):
                    os.remove(path)
        return out

    return {
        "cutout": _download(cutout_urls(full_name, per_char)),
        "chibi": _download(chibi_urls(given)),
        "mysekai": _download(mysekai_urls(given)),
    }


# --- admin API ----------------------------------------------------------------

def make_session(admin: str, token: str) -> requests.Session:
    """Log in for the HMAC-signed admin_session cookie (the only accepted auth)."""
    s = requests.Session()
    s.headers.update(HEADERS)
    r = s.post(f"{admin.rstrip('/')}/api/admin/login", json={"token": token}, timeout=15)
    if not r.ok or not r.json().get("ok"):
        raise SystemExit(f"admin login failed: {r.status_code} {r.text[:150]}")
    return s


def enroll(sess: requests.Session, admin: str, cid: str, name: str, relation: str,
           images: list[tuple[str, bytes]], work: str = PJSK_WORK) -> dict:
    files = [("images", (n, data, "image/png")) for n, data in images]
    form = {
        "character_id": cid,
        "name": name,
        "relation": relation,
        "work": work,
        "context_label": PJSK_CONTEXT_LABELS.get(cid, work),
    }
    r = sess.post(f"{admin.rstrip('/')}/api/admin/characters/build",
                  data=form, files=files, timeout=240)
    try:
        return r.json()
    except ValueError:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}


def existing_ids(sess: requests.Session, admin: str) -> set[str]:
    try:
        r = sess.get(f"{admin.rstrip('/')}/api/admin/characters", timeout=15)
        return {c["character_id"] for c in r.json().get("characters", [])}
    except (requests.RequestException, ValueError, KeyError):
        return set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin", default="http://localhost:8081")
    ap.add_argument("--token", default="admin")
    ap.add_argument("--per-char", type=int, default=10, help="max cutouts per char")
    ap.add_argument("--sleep", type=float, default=1.5, help="seconds between chars")
    ap.add_argument("--only", default="", help="comma-separated character_ids to limit to")
    ap.add_argument("--force", action="store_true", help="re-enroll even if already present")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    sess = make_session(args.admin, args.token)
    already = existing_ids(sess, args.admin)
    print(f"already enrolled: {sorted(already)}\n")

    ok, skip, fail = 0, 0, 0
    for full_name, zh, cid, rel, given in ROSTER:
        if only and cid not in only:
            continue
        if cid in already and not args.force:
            print(f"[skip] {cid} ({zh}) already enrolled (use --force to redo)")
            skip += 1
            continue
        print(f"[{cid}] {zh} ({full_name}) ...")
        try:
            forms = collect_images(full_name, given, args.per_char)
            imgs = [item for form_imgs in forms.values() for item in form_imgs]
            counts = {k: len(v) for k, v in forms.items()}
            print(f"  forms: cutout={counts['cutout']} chibi={counts['chibi']} "
                  f"mysekai={counts['mysekai']} (total {len(imgs)})")
            if not imgs:
                print("  ! downloaded 0 images, skip")
                fail += 1
                continue
            res = enroll(sess, args.admin, cid, zh, rel, imgs)
            if res.get("error"):
                print(f"  ✗ enroll error: {res['error']}")
                fail += 1
            else:
                print(f"  ✓ embedded {res.get('embedded')}/{res.get('total')} "
                      f"samples={res.get('samples')} sync={res.get('sync')}")
                ok += 1
        except (requests.RequestException, RuntimeError, ValueError, subprocess.SubprocessError) as e:
            print(f"  ✗ error: {e}")
            fail += 1
        time.sleep(args.sleep)

    print(f"\n=== done: enrolled={ok} skipped={skip} failed={fail} ===")


if __name__ == "__main__":
    main()
