#!/usr/bin/env python3
"""Batch-collect review candidates for character-pack bucket gaps.

This tool is intentionally a candidate-pool builder, not an enrollment writer.
It reads active charpack manifests, finds characters whose
``training_stats.missing_forms`` still contains required buckets, pulls many
candidate URLs in parallel, applies cheap structural filters, and writes JSON /
HTML review artifacts.  Approved sources should then be copied into the
deterministic enrollment scripts before rebuilding packs.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import io
import json
import re
import shutil
import sys
import time
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - bs4 is available in the project lock.
    BeautifulSoup = None  # type: ignore[assignment]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA}
IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|webp|gif)(?:[?#].*)?$", re.IGNORECASE)
SAFE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
REQUIRED_FORMS = ("full_body", "normal_proportion", "chibi", "expression")
PAGE_ASSET_BAD_RE = re.compile(
    r"(youtube|ytimg|avatar|banner|button|btn_|logo|icon|interface|header|footer|"
    r"twitter|facebook|instagram|line_|sns|loading|placeholder|sprite|bg_|background)",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff\u3040-\u30ff]+")
TRUSTED_SEARCH_DOMAINS: dict[str, set[str]] = {
    "bangdream": {
        "anime.bang-dream.com",
        "bang-dream.com",
        "bang-dream-on.bushimo.jp",
        "bushiroad-creative.com",
        "bushiroad-store.com",
        "goodsmile.com",
        "1999.co.jp",
        "amiami.jp",
    },
    "ja_virtual_singers": {
        "ah-soft.com",
        "aivoice.jp",
        "animove.jp",
        "ia-aria.com",
        "kamitsubaki.jp",
        "line-scdn.net",
        "mayusan.jp",
        "musical-isotope.kamitsubaki.jp",
        "piapro.net",
        "sonicwire.com",
        "ssw.co.jp",
        "store.line.me",
        "thinkr.jp",
        "vocaloid.com",
        "vocalomakets.com",
    },
    "zh_virtual_singers": {
        "dreamtonics.com",
        "ecapsule.co.jp",
        "line-scdn.net",
        "moegirl.org.cn",
        "quadimension.com",
        "res.vsinger.com",
        "static.wikia.nocookie.net",
        "store.line.me",
        "synthv.fandom.com",
        "synthesizerv.com",
        "vocanese.com",
        "vsinger.com",
    },
}
SEARCH_FORM_HINTS: dict[str, tuple[str, ...]] = {
    "chibi": ("SD", "Q版", "ちび", "ぬいぐるみ", "デフォルメ", "sticker"),
    "expression": ("表情", "差分", "LINEスタンプ", "sticker", "face"),
    "normal_proportion": ("立ち絵", "設定画", "standing", "公式画像"),
    "full_body": ("全身", "立ち絵", "official art"),
}
BANGDREAM_CHIBI_SEARCH_HINTS = (
    "ぬいぐるみ",
    "デフォルメ",
    "SD",
    "アクリルスタンド",
    "缶バッジ",
    "Q版",
    "ちび",
)
LOOSE_PAGE_SEEDS: dict[str, list[str]] = {
    "lily": [
        "https://www.ssw.co.jp/products/vocal3/lily/",
        "https://animove.jp/lily/goods/",
    ],
    "nekomura_iroha": [
        "https://www.ah-soft.com/synth-v/iroha/",
        "https://www.ah-soft.com/press/",
    ],
    "mayu": ["https://mayusan.jp/"],
    "kafu": ["https://musical-isotope.kamitsubaki.jp/product/kafu/"],
    "sekai": ["https://musical-isotope.kamitsubaki.jp/product/sekai/"],
    "rime": ["https://musical-isotope.kamitsubaki.jp/product/rime/"],
    "coko": ["https://musical-isotope.kamitsubaki.jp/product/coko/"],
    "haru": ["https://musical-isotope.kamitsubaki.jp/product/haru/"],
}


class SupportsGet(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any:
        ...


@dataclass(frozen=True)
class GapTarget:
    pack: str
    manifest_path: str
    character_id: str
    name: str
    aliases: tuple[str, ...]
    context_label: str
    missing_forms: tuple[str, ...]
    existing_sources: tuple[str, ...]


@dataclass(frozen=True)
class CandidateSpec:
    character_id: str
    name: str
    pack: str
    form: str
    provider: str
    source: str
    url: str
    page_url: str = ""
    trust: str = "review"
    notes: str = ""


@dataclass
class CandidateResult:
    character_id: str
    name: str
    pack: str
    form: str
    provider: str
    source: str
    url: str
    page_url: str = ""
    trust: str = "review"
    status: str = "rejected"
    reason: str = ""
    content_type: str = ""
    bytes: int = 0
    width: int = 0
    height: int = 0
    sha256: str = ""
    image_path: str = ""
    thumb_path: str = ""
    notes: str = ""
    flags: list[str] = field(default_factory=list)


def safe_name(value: str) -> str:
    return SAFE_RE.sub("_", value.strip()).strip("_") or "item"


def normalized_token(value: object) -> str:
    return TOKEN_RE.sub("", str(value or "")).casefold()


def target_keywords(target: GapTarget) -> list[str]:
    raw_values = [
        target.character_id,
        *target.character_id.split("_"),
        target.name,
        *target.aliases,
    ]
    keywords: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        token = normalized_token(value)
        if len(token) < 2 or token in seen or token in {"ai", "v", "the"}:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords


def domain_of(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    return host.lower().removeprefix("www.")


def domain_allowed(domain: str, allowed: set[str]) -> bool:
    host = domain_of(domain)
    if not host:
        return False
    return any(host == item or host.endswith(f".{item}") for item in allowed)


def trusted_domains_for(target: GapTarget) -> set[str]:
    return TRUSTED_SEARCH_DOMAINS.get(target.pack, set())


def bangdream_chibi_search_result_is_known_non_chibi(image_url: str, page_url: str) -> bool:
    value = " ".join((image_url, page_url)).casefold()
    return any(
        pattern in value
        for pattern in (
            "bangdream-portal/assets/webp/common/artist/",
            "img_full_",
            "img_list_",
            "img_thumb_",
            "assets/images/common/common/ogp",
            "bang-dream-on/assets/images/common/index/img_",
            "bang-dream-on/assets/images/common/index/nav_",
            "bang-dream-on/assets/images/common/character/img_",
            "bang-dream-on/assets/images/common/ogp.webp",
        )
    )


def active_manifest_paths(packs_dir: Path) -> list[Path]:
    return sorted(packs_dir.glob("*.charpack/manifest.json"))


def resolved_context_label(pack: str, character_id: str, fallback: str) -> str:
    if pack != "bangdream":
        return fallback
    try:
        from tools import enroll_bangdream_pack as bangdream
    except Exception:
        return fallback
    roster = {entry.character_id: entry for entry in bangdream.ROSTER}
    entry = roster.get(character_id)
    if entry is None:
        return fallback
    return bangdream.BAND_LABELS.get(entry.band_slug, fallback)


def load_gap_targets(
    packs_dir: Path,
    *,
    packs: set[str] | None = None,
    characters: set[str] | None = None,
    forms: set[str] | None = None,
) -> list[GapTarget]:
    targets: list[GapTarget] = []
    for manifest_path in active_manifest_paths(packs_dir):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pack = str(manifest.get("pack") or manifest_path.parent.stem.removesuffix(".charpack"))
        if packs and pack not in packs:
            continue
        for raw in manifest.get("characters", []) or []:
            if not isinstance(raw, dict):
                continue
            cid = str(raw.get("character_id") or raw.get("id") or "").strip()
            if not cid or (characters and cid not in characters):
                continue
            raw_stats = raw.get("training_stats")
            stats: dict[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
            missing = tuple(
                form
                for form in stats.get("missing_forms", []) or []
                if isinstance(form, str) and (forms is None or form in forms)
            )
            if not missing:
                continue
            aliases = tuple(str(item).strip() for item in raw.get("aliases", []) or [] if str(item).strip())
            sources = tuple(str(item) for item in stats.get("sources", []) or [])
            raw_context = str(raw.get("context_label") or raw.get("work") or manifest.get("work") or "")
            targets.append(
                GapTarget(
                    pack=pack,
                    manifest_path=str(manifest_path),
                    character_id=cid,
                    name=str(raw.get("name") or cid),
                    aliases=aliases,
                    context_label=resolved_context_label(pack, cid, raw_context),
                    missing_forms=missing,
                    existing_sources=sources,
                )
            )
    return targets


def source_form_virtual(source: str) -> str:
    from tools import enroll_virtual_singers_pack as virtual

    return virtual.source_form(source)


def source_form_bangdream(source: str) -> str:
    from tools import enroll_bangdream_pack as bangdream

    return bangdream.source_form(source)


def static_catalog_specs(target: GapTarget) -> list[CandidateSpec]:
    """Candidate URLs already known to enrollment scripts but not in manifest.

    This catches the common case where the script gained a source but the active
    pack has not yet been rebuilt.  It also gives reviewers a unified inventory
    of deterministic candidates across all current gaps.
    """
    specs: list[CandidateSpec] = []
    missing = set(target.missing_forms)
    existing = set(target.existing_sources)

    if target.pack in {"zh_virtual_singers", "ja_virtual_singers"}:
        from tools import enroll_virtual_singers_pack as virtual

        for source, url in virtual.EXTRA_IMAGE_URLS.get(target.character_id, []):
            form = source_form_virtual(source)
            if form in missing and source not in existing and isinstance(url, str):
                specs.append(
                    CandidateSpec(
                        character_id=target.character_id,
                        name=target.name,
                        pack=target.pack,
                        form=form,
                        provider="virtual_direct_catalog",
                        source=source,
                        url=url,
                        trust="known_catalog",
                        notes="Defined in enroll_virtual_singers_pack.py but absent from active manifest.",
                    )
                )
    if target.pack == "bangdream":
        from tools import enroll_bangdream_pack as bangdream

        roster = {entry.character_id: entry for entry in bangdream.ROSTER}
        entry = roster.get(target.character_id)
        if entry is not None:
            for _kind, source, url in [
                *bangdream.official_urls(entry),
                *bangdream.mini_anime_chibi_urls(entry),
            ]:
                form = source_form_bangdream(source)
                if form in missing and source not in existing:
                    specs.append(
                        CandidateSpec(
                            character_id=target.character_id,
                            name=target.name,
                            pack=target.pack,
                            form=form,
                            provider="bangdream_direct_catalog",
                            source=source,
                            url=url,
                            trust="known_catalog",
                            notes="Defined in enroll_bangdream_pack.py but absent from active manifest.",
                        )
                    )
    return specs


def seed_file_specs(targets: list[GapTarget], seed_files: list[Path]) -> dict[str, list[CandidateSpec]]:
    by_id = {target.character_id: target for target in targets}
    specs_by_id: dict[str, list[CandidateSpec]] = {target.character_id: [] for target in targets}
    for seed_file in seed_files:
        payload = json.loads(seed_file.read_text(encoding="utf-8"))
        raw_items = payload.get("candidates", []) if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            raise SystemExit(f"{seed_file} must be a JSON list or an object with candidates[]")
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            cid = str(raw.get("character_id") or "").strip()
            target = by_id.get(cid)
            if target is None:
                continue
            form = str(raw.get("form") or "").strip()
            if form not in target.missing_forms:
                continue
            raw_urls = raw.get("urls")
            url_values: list[object] = raw_urls if isinstance(raw_urls, list) else [raw.get("url")]
            for url_index, raw_url in enumerate(url_values):
                url = str(raw_url or "").strip()
                if not url:
                    continue
                source = str(raw.get("source") or f"seed_{safe_name(seed_file.stem)}_{index:03d}_{url_index:02d}")
                specs_by_id[cid].append(
                    CandidateSpec(
                        character_id=target.character_id,
                        name=target.name,
                        pack=target.pack,
                        form=form,
                        provider=str(raw.get("provider") or "seed_file"),
                        source=source,
                        url=url,
                        page_url=str(raw.get("page_url") or ""),
                        trust=str(raw.get("trust") or "seed_review"),
                        notes=str(raw.get("notes") or f"Loaded from {seed_file}"),
                    )
                )
    return specs_by_id


def bangdream_mini_probe_specs(target: GapTarget) -> list[CandidateSpec]:
    if target.pack != "bangdream" or "chibi" not in target.missing_forms:
        return []
    from tools import enroll_bangdream_pack as bangdream

    roster = {entry.character_id: entry for entry in bangdream.ROSTER}
    entry = roster.get(target.character_id)
    if entry is None:
        return []
    parts = [part for part in entry.official_slug.split("-") if part]
    variants = [
        entry.official_slug,
        bangdream.our_notes_slug(entry.official_slug),
        *(reversed(parts) if len(parts) == 2 else []),
    ]
    out: list[CandidateSpec] = []
    seen: set[str] = set()
    for variant in variants:
        if not variant or variant in seen:
            continue
        seen.add(variant)
        url = f"{bangdream.MINI_ANIME_ASSET_BASE}/{entry.band_slug}/img_chara-{variant}.webp"
        out.append(
            CandidateSpec(
                character_id=target.character_id,
                name=target.name,
                pack=target.pack,
                form="chibi",
                provider="bangdream_mini_anime_probe",
                source=f"official_chibi_bangdream_mini_probe_{safe_name(variant)}",
                url=url,
                trust="official_probe",
                notes="Pattern probe against official mini-anime asset tree; must be manually verified.",
            )
        )
    return out


PAGE_SEEDS: dict[str, list[str]] = {
    "lily": [
        "https://www.ssw.co.jp/products/vocal3/lily/",
        "https://animove.jp/lily/goods/",
    ],
    "nekomura_iroha": [
        "https://www.ah-soft.com/synth-v/iroha/",
        "https://www.ah-soft.com/press/",
    ],
    "mayu": ["https://mayusan.jp/"],
    "kafu": ["https://musical-isotope.kamitsubaki.jp/product/kafu/"],
    "sekai": ["https://musical-isotope.kamitsubaki.jp/product/sekai/"],
    "rime": ["https://musical-isotope.kamitsubaki.jp/product/rime/"],
    "coko": ["https://musical-isotope.kamitsubaki.jp/product/coko/"],
    "haru": ["https://musical-isotope.kamitsubaki.jp/product/haru/"],
    "luo_tianyi": ["https://vsinger.com/"],
    "yan_he": ["https://vsinger.com/"],
    "yuezheng_ling": ["https://vsinger.com/"],
    "yuezheng_longya": ["https://vsinger.com/"],
    "zhiyu_moke": ["https://vsinger.com/"],
    "mo_qingxian": ["https://vsinger.com/"],
}


def classify_form_from_text(text: str) -> str:
    value = text.casefold()
    if re.search(r"(chibi|sd|mini|deform|stamp|sticker|q版|q_model|ちび|デフォルメ)", value):
        return "chibi"
    if re.search(r"(expression|face|sabun|diff|表情|差分|angry|smile|sad|joy|cry|blush)", value):
        return "expression"
    if re.search(r"(standing|settei|setting|sheet|config|立ち絵|立绘|設定|设定|三视|三視)", value):
        return "normal_proportion"
    if re.search(r"(full|main|kv|visual|全身|立ち姿)", value):
        return "full_body"
    return "profile_art"


def page_asset_is_noise(url: str, text: str) -> bool:
    value = f"{url} {text}"
    return PAGE_ASSET_BAD_RE.search(value) is not None


def page_asset_has_target_hint(url: str, text: str, target: GapTarget) -> bool:
    normalized = normalized_token(f"{url} {text}")
    return any(keyword in normalized for keyword in target_keywords(target))


def page_image_specs(
    sess: SupportsGet,
    target: GapTarget,
    page_url: str,
    *,
    timeout: float,
    max_page_images: int,
    loose: bool = False,
) -> list[CandidateSpec]:
    try:
        resp = sess.get(page_url, headers=HEADERS, timeout=timeout)
    except requests.RequestException:
        return []
    content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if resp.status_code != 200 or "html" not in content_type:
        return []
    if BeautifulSoup is None:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    specs: list[CandidateSpec] = []
    seen: set[str] = set()
    for node in soup.find_all(["img", "a", "source"]):
        raw_url = node.get("src") or node.get("data-src") or node.get("href") or node.get("srcset")
        if not raw_url:
            continue
        first_url = str(raw_url).split(",", 1)[0].strip().split(" ", 1)[0]
        url = urljoin(page_url, first_url)
        if not IMAGE_EXT_RE.search(url):
            continue
        text = " ".join(
            str(node.get(attr) or "")
            for attr in ("alt", "title", "aria-label", "class", "id")
        )
        if page_asset_is_noise(url, text):
            continue
        text = f"{text} {url}"
        form = classify_form_from_text(text)
        has_form_hint = form in target.missing_forms
        if not has_form_hint:
            if not loose:
                continue
            form = target.missing_forms[0] if target.missing_forms else "profile_art"
        has_target_hint = page_asset_has_target_hint(url, text, target)
        if loose and not has_target_hint and not has_form_hint:
            continue
        if not has_target_hint and not loose:
            continue
        if url in seen:
            continue
        seen.add(url)
        provider = "official_page_loose_scan" if loose else "official_page_scan"
        trust = "official_page_loose_review" if loose else "official_page_review"
        notes = (
            "Extracted from configured official page seed with loose matching; must be visually reviewed."
            if loose
            else "Extracted from configured official page seed; must be visually reviewed."
        )
        source_prefix = "loose_page_scan" if loose else "page_scan"
        specs.append(
            CandidateSpec(
                character_id=target.character_id,
                name=target.name,
                pack=target.pack,
                form=form,
                provider=provider,
                source=f"official_{form}_{source_prefix}_{safe_name(Path(url).stem)[:48]}",
                url=url,
                page_url=page_url,
                trust=trust,
                notes=notes + ("" if has_target_hint and has_form_hint else " Weak text/form hint."),
            )
        )
        if len(specs) >= max_page_images:
            break
    return specs


def image_search_queries(target: GapTarget) -> list[tuple[str, str]]:
    names = [target.name, *target.aliases, target.character_id.replace("_", " ")]
    context_terms = [part.strip() for part in re.split(r"[/・|]", target.context_label) if part.strip()]
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for form in target.missing_forms:
        hints = (
            BANGDREAM_CHIBI_SEARCH_HINTS
            if target.pack == "bangdream" and form == "chibi"
            else SEARCH_FORM_HINTS.get(form, (form,))
        )
        for name in names[:3]:
            if not name:
                continue
            for hint in hints:
                query = " ".join([name, *context_terms[:2], hint, "公式"])
                normalized = normalized_token(query)
                if len(normalized) < 2 or normalized in seen:
                    continue
                seen.add(normalized)
                queries.append((form, query))
                max_queries = max(4, len(target.missing_forms) * 4)
                if target.pack == "bangdream" and form == "chibi":
                    max_queries = max(max_queries, 8)
                if len(queries) >= max_queries:
                    return queries
    return queries


def trusted_image_search_specs(
    target: GapTarget,
    *,
    max_results: int,
) -> list[CandidateSpec]:
    allowed = trusted_domains_for(target)
    if not allowed or max_results <= 0:
        return []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            from ddgs import DDGS
    except Exception:
        return []

    specs: list[CandidateSpec] = []
    seen_urls: set[str] = set()
    try:
        search = DDGS()
    except Exception:
        return []
    for form, query in image_search_queries(target):
        try:
            results = search.images(query, max_results=max_results)
        except Exception:
            continue
        for raw in results:
            if not isinstance(raw, dict):
                continue
            image_url = str(raw.get("image") or "").strip()
            page_url = str(raw.get("url") or "").strip()
            source_domain = domain_of(str(raw.get("source") or "") or page_url or image_url)
            if not image_url or image_url in seen_urls:
                continue
            if not domain_allowed(source_domain or image_url, allowed):
                continue
            if (
                target.pack == "bangdream"
                and form == "chibi"
                and bangdream_chibi_search_result_is_known_non_chibi(image_url, page_url)
            ):
                continue
            text = " ".join(str(raw.get(key) or "") for key in ("title", "source", "url", "image"))
            if page_asset_is_noise(image_url, text):
                continue
            seen_urls.add(image_url)
            specs.append(
                CandidateSpec(
                    character_id=target.character_id,
                    name=target.name,
                    pack=target.pack,
                    form=form,
                    provider="trusted_image_search",
                    source=f"review_{form}_image_search_{safe_name(source_domain)[:32]}_{len(specs):03d}",
                    url=image_url,
                    page_url=page_url,
                    trust="search_result_review",
                    notes=f"Trusted-domain image search result for query: {query}",
                )
            )
            if len(specs) >= max_results * max(1, len(target.missing_forms)):
                return specs
    return specs


def candidate_specs(
    target: GapTarget,
    sess: requests.Session,
    *,
    providers: set[str],
    seed_specs: dict[str, list[CandidateSpec]],
    timeout: float,
    max_page_images: int,
    max_search_results: int,
) -> list[CandidateSpec]:
    specs: list[CandidateSpec] = []
    if "seed" in providers:
        specs.extend(seed_specs.get(target.character_id, []))
    if "catalog" in providers:
        specs.extend(static_catalog_specs(target))
    if "bangdream-probe" in providers:
        specs.extend(bangdream_mini_probe_specs(target))
    if "page" in providers:
        for page_url in PAGE_SEEDS.get(target.character_id, []):
            specs.extend(
                page_image_specs(
                    sess,
                    target,
                    page_url,
                    timeout=timeout,
                    max_page_images=max_page_images,
                )
            )
    if "page-loose" in providers:
        for page_url in LOOSE_PAGE_SEEDS.get(target.character_id, PAGE_SEEDS.get(target.character_id, [])):
            specs.extend(
                page_image_specs(
                    sess,
                    target,
                    page_url,
                    timeout=timeout,
                    max_page_images=max_page_images,
                    loose=True,
                )
            )
    if "image-search" in providers:
        specs.extend(trusted_image_search_specs(target, max_results=max_search_results))

    deduped: list[CandidateSpec] = []
    seen: set[tuple[str, str, str]] = set()
    for spec in specs:
        key = (spec.character_id, spec.form, spec.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return deduped


def cache_path(cache_dir: Path, url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.bin", cache_dir / f"{digest}.json"


def read_cache(cache_dir: Path, url: str) -> tuple[bytes, str] | None:
    data_path, meta_path = cache_path(cache_dir, url)
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        data = data_path.read_bytes()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    content_type = str(meta.get("content_type") or "").split(";", 1)[0].strip().lower()
    if not content_type.startswith("image/"):
        return None
    return data, content_type


def write_cache(cache_dir: Path, url: str, data: bytes, content_type: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    data_path, meta_path = cache_path(cache_dir, url)
    data_path.write_bytes(data)
    meta_path.write_text(
        json.dumps({"url": url, "content_type": content_type}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_image(
    sess: requests.Session,
    url: str,
    *,
    cache_dir: Path,
    timeout: float,
) -> tuple[bytes, str, str]:
    cached = read_cache(cache_dir, url)
    if cached is not None:
        return cached[0], cached[1], "cache"
    resp = sess.get(url, headers=HEADERS, timeout=timeout)
    content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if resp.status_code != 200:
        raise ValueError(f"http_{resp.status_code}")
    if not content_type.startswith("image/"):
        raise ValueError(f"not_image:{content_type or 'unknown'}")
    data = resp.content
    if len(data) < 1024:
        raise ValueError("too_small_bytes")
    write_cache(cache_dir, url, data, content_type)
    return data, content_type, "network"


def image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def extension_for(content_type: str) -> str:
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    if content_type == "image/gif":
        return ".gif"
    return ".jpg"


def write_candidate_files(
    spec: CandidateSpec,
    data: bytes,
    content_type: str,
    out_dir: Path,
) -> tuple[str, str, int, int]:
    width, height = image_size(data)
    ext = extension_for(content_type)
    stem = f"{safe_name(spec.character_id)}__{safe_name(spec.form)}__{safe_name(spec.source)[:72]}"
    image_dir = out_dir / "images" / safe_name(spec.character_id)
    thumb_dir = out_dir / "thumbs" / safe_name(spec.character_id)
    image_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / f"{stem}{ext}"
    thumb_path = thumb_dir / f"{stem}.jpg"
    image_path.write_bytes(data)

    with Image.open(io.BytesIO(data)).convert("RGB") as image:
        image.thumbnail((220, 220))
        image.save(thumb_path, "JPEG", quality=84)
    return str(image_path), str(thumb_path), width, height


def collect_one(
    spec: CandidateSpec,
    *,
    out_dir: Path,
    cache_dir: Path,
    timeout: float,
    min_dimension: int,
) -> CandidateResult:
    result = CandidateResult(**asdict(spec))
    sess = requests.Session()
    try:
        data, content_type, source_kind = fetch_image(sess, spec.url, cache_dir=cache_dir, timeout=timeout)
        digest = hashlib.sha256(data).hexdigest()
        width, height = image_size(data)
        if width < min_dimension or height < min_dimension:
            result.reason = f"too_small_dimensions:{width}x{height}"
            result.content_type = content_type
            result.bytes = len(data)
            result.width = width
            result.height = height
            result.sha256 = digest
            return result
        image_path, thumb_path, width, height = write_candidate_files(spec, data, content_type, out_dir)
    except Exception as exc:
        result.reason = str(exc)
        return result

    result.status = "accepted_for_review"
    result.reason = source_kind
    result.content_type = content_type
    result.bytes = len(data)
    result.width = width
    result.height = height
    result.sha256 = digest
    result.image_path = image_path
    result.thumb_path = thumb_path
    if result.trust.endswith("review"):
        result.flags.append("manual_visual_review_required")
    if result.provider.endswith("probe"):
        result.flags.append("pattern_probe")
    return result


def mark_duplicates(results: list[CandidateResult]) -> None:
    first_by_hash: dict[str, CandidateResult] = {}
    for item in results:
        if item.status != "accepted_for_review" or not item.sha256:
            continue
        first = first_by_hash.get(item.sha256)
        if first is None:
            first_by_hash[item.sha256] = item
            continue
        item.status = "rejected"
        item.reason = f"duplicate_sha256:{first.character_id}/{first.source}"
        item.flags.append("duplicate")


def relpath(path: str, base: Path) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).relative_to(base))
    except ValueError:
        return path


def write_json_report(
    out_dir: Path,
    targets: list[GapTarget],
    specs: list[CandidateSpec],
    results: list[CandidateResult],
) -> Path:
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "target_count": len(targets),
        "candidate_count": len(specs),
        "accepted_count": sum(1 for item in results if item.status == "accepted_for_review"),
        "targets": [asdict(item) for item in targets],
        "candidates": [asdict(item) for item in results],
    }
    path = out_dir / "candidates.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_seed_template(out_dir: Path, targets: list[GapTarget]) -> Path:
    payload = {
        "note": "Fill url/source/page_url for candidates, then pass this file via --seed-file.",
        "candidates": [
            {
                "character_id": target.character_id,
                "name": target.name,
                "pack": target.pack,
                "missing_forms": list(target.missing_forms),
                "form": target.missing_forms[0] if target.missing_forms else "",
                "source": "",
                "url": "",
                "page_url": "",
                "trust": "seed_review",
                "notes": "",
            }
            for target in targets
        ],
    }
    path = out_dir / "seed-template.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_html_report(out_dir: Path, targets: list[GapTarget], results: list[CandidateResult]) -> Path:
    rows: list[str] = []
    for item in results:
        thumb = relpath(item.thumb_path, out_dir)
        thumb_html = (
            f'<a href="{html.escape(relpath(item.image_path, out_dir))}">'
            f'<img src="{html.escape(thumb)}"></a>'
            if thumb
            else ""
        )
        rows.append(
            "<tr>"
            f"<td>{thumb_html}</td>"
            f"<td>{html.escape(item.pack)}<br>{html.escape(item.character_id)}<br>{html.escape(item.name)}</td>"
            f"<td>{html.escape(item.form)}</td>"
            f"<td>{html.escape(item.status)}<br>{html.escape(item.reason)}</td>"
            f"<td>{html.escape(item.provider)}<br>{html.escape(item.source)}</td>"
            f"<td>{item.width}x{item.height}<br>{html.escape(item.content_type)}</td>"
            f"<td><a href=\"{html.escape(item.url)}\">source</a>"
            f"{'<br><a href=\"' + html.escape(item.page_url) + '\">page</a>' if item.page_url else ''}</td>"
            f"<td>{html.escape(', '.join(item.flags))}</td>"
            "</tr>"
        )
    target_rows = [
        f"<li><code>{html.escape(t.pack)}/{html.escape(t.character_id)}</code> "
        f"{html.escape(t.name)} missing: {html.escape(', '.join(t.missing_forms))}</li>"
        for t in targets
    ]
    content = f"""<!doctype html>
<meta charset="utf-8">
<title>Character Pack Candidate Review</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #17201d; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #d7dfdb; padding: 8px; vertical-align: top; font-size: 13px; }}
th {{ background: #eef4f1; text-align: left; }}
img {{ max-width: 160px; max-height: 160px; object-fit: contain; background: #f7faf8; }}
code {{ background: #eef4f1; padding: 1px 4px; border-radius: 4px; }}
</style>
<h1>Character Pack Candidate Review</h1>
<p>
Accepted candidates are only approved for review. Copy verified sources into enrollment scripts before rebuilding packs.
</p>
<h2>Targets</h2>
<ul>{''.join(target_rows)}</ul>
<h2>Candidates</h2>
<table>
<thead>
<tr>
<th>Preview</th><th>Character</th><th>Form</th><th>Status</th>
<th>Provider / Source</th><th>Image</th><th>Links</th><th>Flags</th>
</tr>
</thead>
<tbody>{''.join(rows)}</tbody>
</table>
"""
    path = out_dir / "index.html"
    path.write_text(content, encoding="utf-8")
    return path


def parse_csv(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    out: set[str] = set()
    for value in values:
        out.update(item.strip() for item in value.split(",") if item.strip())
    return out or None


def build_specs_for_targets(
    targets: list[GapTarget],
    *,
    providers: set[str],
    seed_files: list[Path],
    timeout: float,
    max_page_images: int,
    max_search_results: int,
    max_candidates_per_target: int,
) -> list[CandidateSpec]:
    specs: list[CandidateSpec] = []
    sess = requests.Session()
    seed_specs = seed_file_specs(targets, seed_files) if seed_files else {}
    for target in targets:
        target_specs = candidate_specs(
            target,
            sess,
            providers=providers,
            seed_specs=seed_specs,
            timeout=timeout,
            max_page_images=max_page_images,
            max_search_results=max_search_results,
        )
        if max_candidates_per_target > 0:
            target_specs = target_specs[:max_candidates_per_target]
        specs.extend(target_specs)
    return specs


def targets_for_specs(targets: list[GapTarget], specs: list[CandidateSpec]) -> list[GapTarget]:
    spec_targets = {(spec.pack, spec.character_id) for spec in specs}
    if not spec_targets:
        return []
    return [target for target in targets if (target.pack, target.character_id) in spec_targets]


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch collect review candidates for charpack missing form buckets")
    ap.add_argument("--packs-dir", type=Path, default=Path("config/character_packs"))
    ap.add_argument("--out", type=Path, default=Path(".workspace/character-pack-candidates/latest"))
    ap.add_argument("--cache-dir", type=Path, default=Path(".cache/character-pack-candidates"))
    ap.add_argument("--pack", action="append", help="Pack filter; comma-separated or repeated")
    ap.add_argument("--character", action="append", help="Character ID filter; comma-separated or repeated")
    ap.add_argument("--form", action="append", help="Missing form filter; comma-separated or repeated")
    ap.add_argument(
        "--provider",
        action="append",
        choices=("catalog", "bangdream-probe", "page", "page-loose", "image-search", "seed"),
        help="Provider filter; repeated. Default: all providers.",
    )
    ap.add_argument(
        "--seed-file",
        action="append",
        type=Path,
        default=[],
        help="JSON candidate seeds: list or {candidates:[{character_id, form, url|urls, source?}]}",
    )
    ap.add_argument("--max-targets", type=int, default=0)
    ap.add_argument("--max-candidates-per-target", type=int, default=24)
    ap.add_argument("--max-page-images", type=int, default=16)
    ap.add_argument("--max-search-results", type=int, default=4)
    ap.add_argument("--max-workers", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--min-dimension", type=int, default=64)
    ap.add_argument("--dry-run", action="store_true", help="Only write candidate specs, do not download images")
    ap.add_argument("--write-seed-template", action="store_true", help="Write seed-template.json for selected targets")
    ap.add_argument("--keep-out", action="store_true", help="Do not delete existing output directory first")
    args = ap.parse_args()

    providers: set[str] = set(args.provider or ("catalog", "bangdream-probe", "page"))
    if args.seed_file:
        providers.add("seed")
    targets = load_gap_targets(
        args.packs_dir,
        packs=parse_csv(args.pack),
        characters=parse_csv(args.character),
        forms=parse_csv(args.form),
    )
    if args.max_targets > 0:
        targets = targets[: args.max_targets]

    if args.out.exists() and not args.keep_out:
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.write_seed_template:
        template_path = write_seed_template(args.out, targets)
        print(f"targets={len(targets)} seed_template={template_path}")

    specs = build_specs_for_targets(
        targets,
        providers=providers,
        seed_files=args.seed_file,
        timeout=args.timeout,
        max_page_images=args.max_page_images,
        max_search_results=args.max_search_results,
        max_candidates_per_target=args.max_candidates_per_target,
    )

    if args.dry_run:
        path = args.out / "candidate-specs.json"
        path.write_text(json.dumps([asdict(item) for item in specs], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"targets={len(targets)} specs={len(specs)} wrote={path}")
        return

    results: list[CandidateResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = [
            executor.submit(
                collect_one,
                spec,
                out_dir=args.out,
                cache_dir=args.cache_dir,
                timeout=args.timeout,
                min_dimension=args.min_dimension,
            )
            for spec in specs
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: (item.pack, item.character_id, item.form, item.provider, item.source))
    mark_duplicates(results)
    report_targets = targets_for_specs(targets, specs) if providers == {"seed"} else targets
    json_path = write_json_report(args.out, report_targets, specs, results)
    html_path = write_html_report(args.out, report_targets, results)
    accepted = sum(1 for item in results if item.status == "accepted_for_review")
    print(
        f"targets={len(report_targets)} specs={len(specs)} accepted_for_review={accepted} "
        f"json={json_path} html={html_path}"
    )


if __name__ == "__main__":
    main()
