from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from tools import collect_character_pack_candidates as candidates


def _write_manifest(root: Path) -> None:
    pack_dir = root / "demo.charpack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pack": "demo",
                "work": "Demo",
                "characters": [
                    {
                        "character_id": "alpha",
                        "name": "Alpha",
                        "aliases": ["A"],
                        "context_label": "Demo / Alpha",
                        "training_stats": {
                            "image_count": 5,
                            "sources": ["official_full_alpha"],
                            "missing_forms": ["chibi", "expression"],
                        },
                    },
                    {
                        "character_id": "beta",
                        "name": "Beta",
                        "training_stats": {
                            "image_count": 6,
                            "sources": [],
                            "missing_forms": [],
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_load_gap_targets_reads_missing_forms_from_active_manifests(tmp_path: Path) -> None:
    _write_manifest(tmp_path)

    targets = candidates.load_gap_targets(tmp_path)

    assert targets == [
        candidates.GapTarget(
            pack="demo",
            manifest_path=str(tmp_path / "demo.charpack" / "manifest.json"),
            character_id="alpha",
            name="Alpha",
            aliases=("A",),
            context_label="Demo / Alpha",
            missing_forms=("chibi", "expression"),
            existing_sources=("official_full_alpha",),
        )
    ]


def test_load_gap_targets_overrides_stale_bangdream_context_from_roster(tmp_path: Path) -> None:
    pack_dir = tmp_path / "bangdream.charpack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pack": "bangdream",
                "work": "BanG Dream!",
                "characters": [
                    {
                        "character_id": "suga_raika",
                        "name": "須賀 蕾叶",
                        "context_label": "BanG Dream! / Mugendai Mewtype",
                        "training_stats": {
                            "sources": ["official_full"],
                            "missing_forms": ["chibi"],
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    targets = candidates.load_gap_targets(tmp_path)

    assert len(targets) == 1
    assert targets[0].context_label == "BanG Dream! / 一家Dumb Rock!"


def test_static_catalog_specs_finds_unbuilt_virtual_sources() -> None:
    target = candidates.GapTarget(
        pack="ja_virtual_singers",
        manifest_path="",
        character_id="gumi",
        name="GUMI",
        aliases=(),
        context_label="日V / VOCALOID",
        missing_forms=("expression",),
        existing_sources=(),
    )

    specs = candidates.static_catalog_specs(target)

    assert any(
        spec.source == "official_expression_line_internet_gumi_1485874_17915994"
        and spec.form == "expression"
        and spec.provider == "virtual_direct_catalog"
        for spec in specs
    )


def test_bangdream_mini_probe_generates_parallel_chibi_patterns() -> None:
    target = candidates.GapTarget(
        pack="bangdream",
        manifest_path="",
        character_id="shiomi_hotaru",
        name="汐見 蛍",
        aliases=(),
        context_label="BanG Dream! / Ma'cherie",
        missing_forms=("chibi",),
        existing_sources=(),
    )

    specs = candidates.bangdream_mini_probe_specs(target)

    assert len(specs) >= 3
    assert all(spec.form == "chibi" for spec in specs)
    assert any("img_chara-hotaru-shiomi.webp" in spec.url for spec in specs)


def test_seed_file_specs_only_accepts_current_missing_forms(tmp_path: Path) -> None:
    target = candidates.GapTarget(
        pack="demo",
        manifest_path="",
        character_id="alpha",
        name="Alpha",
        aliases=(),
        context_label="Demo / Alpha",
        missing_forms=("chibi",),
        existing_sources=(),
    )
    seed_file = tmp_path / "seeds.json"
    seed_file.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "character_id": "alpha",
                        "form": "chibi",
                        "provider": "trusted_image_search",
                        "source": "official_chibi_alpha",
                        "urls": ["https://assets.example/a.png", "https://assets.example/b.png"],
                        "page_url": "https://official.example/alpha",
                    },
                    {
                        "character_id": "alpha",
                        "form": "expression",
                        "url": "https://assets.example/wrong-form.png",
                    },
                    {
                        "character_id": "beta",
                        "form": "chibi",
                        "url": "https://assets.example/wrong-character.png",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    specs = candidates.seed_file_specs([target], [seed_file])

    assert [spec.url for spec in specs["alpha"]] == [
        "https://assets.example/a.png",
        "https://assets.example/b.png",
    ]
    assert all(spec.form == "chibi" for spec in specs["alpha"])
    assert all(spec.provider == "trusted_image_search" for spec in specs["alpha"])


def test_write_seed_template_lists_selected_gap_targets(tmp_path: Path) -> None:
    target = candidates.GapTarget(
        pack="demo",
        manifest_path="",
        character_id="alpha",
        name="Alpha",
        aliases=(),
        context_label="Demo / Alpha",
        missing_forms=("chibi", "expression"),
        existing_sources=(),
    )

    path = candidates.write_seed_template(tmp_path, [target])

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["candidates"] == [
        {
            "character_id": "alpha",
            "name": "Alpha",
            "pack": "demo",
            "missing_forms": ["chibi", "expression"],
            "form": "chibi",
            "source": "",
            "url": "",
            "page_url": "",
            "trust": "seed_review",
            "notes": "",
        }
    ]


class _Response:
    def __init__(self, status_code: int, content_type: str, content: bytes) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.content = content
        self.text = content.decode("utf-8", errors="ignore")


class _Session:
    def __init__(self, payloads: dict[str, _Response]) -> None:
        self.payloads = payloads

    def get(self, url: str, **_kwargs: Any) -> _Response:
        return self.payloads[url]


def _png_bytes(size: tuple[int, int] = (512, 384)) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", size, (120, 80, 40)).save(buf, format="PNG")
    return buf.getvalue()


def test_page_image_specs_extracts_only_requested_forms() -> None:
    target = candidates.GapTarget(
        pack="ja_virtual_singers",
        manifest_path="",
        character_id="lily",
        name="Lily",
        aliases=(),
        context_label="日V / VOCALOID",
        missing_forms=("expression",),
        existing_sources=(),
    )
    html = b"""
    <html><body>
      <img src="/img/lily_sd.png" alt="Lily SD chibi">
      <img src="/img/lily_face.png" alt="Lily expression face">
    </body></html>
    """

    specs = candidates.page_image_specs(
        _Session({"https://official.example/lily/": _Response(200, "text/html", html)}),
        target,
        "https://official.example/lily/",
        timeout=1,
        max_page_images=10,
    )

    assert [spec.form for spec in specs] == ["expression"]
    assert specs[0].url == "https://official.example/img/lily_face.png"


def test_page_image_specs_loose_mode_keeps_review_only_profile_art() -> None:
    target = candidates.GapTarget(
        pack="ja_virtual_singers",
        manifest_path="",
        character_id="kafu",
        name="可不",
        aliases=("KAFU",),
        context_label="日V / KAMITSUBAKI",
        missing_forms=("normal_proportion", "chibi"),
        existing_sources=(),
    )
    html = b"""
    <html><body>
      <img src="/wp-content/uploads/kafu_kv_PC.jpeg" alt="">
      <img src="/assets/logo.png" alt="KAFU logo">
    </body></html>
    """

    specs = candidates.page_image_specs(
        _Session({"https://official.example/kafu/": _Response(200, "text/html", html)}),
        target,
        "https://official.example/kafu/",
        timeout=1,
        max_page_images=10,
        loose=True,
    )

    assert len(specs) == 1
    assert specs[0].provider == "official_page_loose_scan"
    assert specs[0].trust == "official_page_loose_review"
    assert specs[0].form == "normal_proportion"
    assert "Weak text/form hint" in specs[0].notes


def test_domain_allowed_accepts_subdomains_only_for_trusted_domains() -> None:
    assert candidates.domain_allowed("https://www.goodsmile.com/ja/product/1", {"goodsmile.com"})
    assert candidates.domain_allowed("static.goodsmile.com", {"goodsmile.com"})
    assert not candidates.domain_allowed("evilgoodsmile.com", {"goodsmile.com"})


def test_image_search_queries_include_missing_form_hints() -> None:
    target = candidates.GapTarget(
        pack="bangdream",
        manifest_path="",
        character_id="nakamachi_arale",
        name="仲町 あられ",
        aliases=(),
        context_label="BanG Dream! / 夢限大みゅーたいぷ",
        missing_forms=("chibi",),
        existing_sources=(),
    )

    queries = candidates.image_search_queries(target)

    assert queries
    assert all(form == "chibi" for form, _query in queries)
    assert any("仲町 あられ" in query and "Q版" in query for _form, query in queries)
    assert any("仲町 あられ" in query and "ぬいぐるみ" in query for _form, query in queries)
    assert any("仲町 あられ" in query and "アクリルスタンド" in query for _form, query in queries)
    assert len(queries) >= 7


def test_bangdream_chibi_search_result_filter_blocks_known_non_chibi_families() -> None:
    assert candidates.bangdream_chibi_search_result_is_known_non_chibi(
        "https://bang-dream.com/wordpress/wp-content/themes/bangdream-portal/assets/webp/common/artist/millsage/img_full_shiomi-hotaru_01.webp",
        "https://bang-dream.com/artist/millsage/shiomi-hotaru/",
    )
    assert candidates.bangdream_chibi_search_result_is_known_non_chibi(
        "https://bang-dream-on.bushimo.jp/wordpress/wp-content/themes/bang-dream-on/assets/images/common/index/nav_hotaru-shiomi.webp",
        "https://bang-dream-on.bushimo.jp/character/millsage/shiomi-hotaru/",
    )
    assert not candidates.bangdream_chibi_search_result_is_known_non_chibi(
        "https://img.amiami.jp/images/product/main/253/GOODS-04673015.jpg",
        "https://www.amiami.jp/top/detail/detail?gcode=GOODS-04673015",
    )


def test_targets_for_specs_filters_report_targets_to_actual_specs() -> None:
    targets = [
        candidates.GapTarget(
            pack="bangdream",
            manifest_path="",
            character_id="alpha",
            name="Alpha",
            aliases=(),
            context_label="BanG Dream!",
            missing_forms=("chibi",),
            existing_sources=(),
        ),
        candidates.GapTarget(
            pack="zh_virtual_singers",
            manifest_path="",
            character_id="beta",
            name="Beta",
            aliases=(),
            context_label="中V",
            missing_forms=("chibi",),
            existing_sources=(),
        ),
    ]
    specs = [
        candidates.CandidateSpec(
            character_id="alpha",
            name="Alpha",
            pack="bangdream",
            form="chibi",
            provider="seed",
            source="alpha_chibi",
            url="https://assets.example/alpha.png",
        )
    ]

    assert candidates.targets_for_specs(targets, specs) == [targets[0]]
    assert candidates.targets_for_specs(targets, []) == []


def test_collect_one_writes_review_files_and_reuses_cache(tmp_path: Path) -> None:
    url = "https://assets.example/chibi.png"
    spec = candidates.CandidateSpec(
        character_id="alpha",
        name="Alpha",
        pack="demo",
        form="chibi",
        provider="unit",
        source="official_chibi_alpha",
        url=url,
        trust="official_page_review",
    )
    data = _png_bytes()

    class SessionFactory:
        calls = 0

        def get(self, request_url: str, **_kwargs: Any) -> _Response:
            assert request_url == url
            SessionFactory.calls += 1
            return _Response(200, "image/png", data)

    original_session = candidates.requests.Session
    try:
        candidates.requests.Session = SessionFactory  # type: ignore[assignment]
        first = candidates.collect_one(
            spec,
            out_dir=tmp_path / "out",
            cache_dir=tmp_path / "cache",
            timeout=1,
            min_dimension=64,
        )
        second = candidates.collect_one(
            spec,
            out_dir=tmp_path / "out2",
            cache_dir=tmp_path / "cache",
            timeout=1,
            min_dimension=64,
        )
    finally:
        candidates.requests.Session = original_session  # type: ignore[assignment]

    assert first.status == "accepted_for_review"
    assert first.width == 512
    assert Path(first.image_path).exists()
    assert Path(first.thumb_path).exists()
    assert second.reason == "cache"
    assert SessionFactory.calls == 1
