from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any

from tools import enroll_virtual_singers_pack as pack


@dataclass
class _FakeResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class _FallbackSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> _FakeResponse:
        self.calls.append(url)
        if url == "https://direct.example/image.png":
            return _FakeResponse(200, {"content-type": "text/html"}, b"<html>not image</html>")
        return _FakeResponse(200, {"content-type": "image/png"}, b"\x89PNG" + b"a" * 2048)


class _NoNetworkSession:
    def get(self, url: str, **_: Any) -> _FakeResponse:
        raise AssertionError(f"unexpected network call for {url}")


def test_download_image_uses_fallback_and_reuses_cached_candidate(tmp_path) -> None:
    candidates = ("https://direct.example/image.png", "https://fallback.example/image.png")

    first_session = _FallbackSession()
    first = pack.download_image(first_session, candidates, cache_dir=tmp_path)

    assert first is not None
    assert first[1] == "image/png"
    assert first_session.calls == [
        "https://direct.example/image.png",
        "https://direct.example/image.png",
        "https://direct.example/image.png",
        "https://fallback.example/image.png",
    ]

    second = pack.download_image(_NoNetworkSession(), candidates, cache_dir=tmp_path)

    assert second == first


def test_download_image_with_referer_sets_header_and_reuses_cache(tmp_path) -> None:
    class RefererSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            self.calls.append((url, dict(kwargs.get("headers") or {})))
            return _FakeResponse(200, {"content-type": "image/png"}, b"\x89PNG" + b"a" * 2048)

    url = pack.RefererImageUrl(
        url="https://media.example.test/thumb/character.png",
        referer="https://wiki.example.test/character",
    )
    session = RefererSession()

    first = pack.download_image(session, url, cache_dir=tmp_path)

    assert first == (b"\x89PNG" + b"a" * 2048, "image/png")
    assert session.calls == [
        (
            "https://media.example.test/thumb/character.png",
            {
                "User-Agent": pack.UA,
                "Referer": "https://wiki.example.test/character",
            },
        )
    ]

    second = pack.download_image(_NoNetworkSession(), url, cache_dir=tmp_path)

    assert second == first


def test_download_zip_image_extracts_member_and_reuses_cache(tmp_path) -> None:
    class ZipSession:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, **_: Any) -> _FakeResponse:
            self.calls.append(url)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as zf:
                zf.writestr("images/demo.png", b"\x89PNG" + b"a" * 2048)
            return _FakeResponse(200, {"content-type": "application/zip"}, payload.getvalue())

    session = ZipSession()
    first = pack.download_zip_image(session, "https://example.test/images.zip", "images/demo.png", cache_dir=tmp_path)

    assert first is not None
    assert first[1] == "image/png"
    assert session.calls == ["https://example.test/images.zip"]

    second = pack.download_zip_image(
        _NoNetworkSession(),
        "https://example.test/images.zip",
        "images/demo.png",
        cache_dir=tmp_path,
    )

    assert second == first


def test_download_zip_image_can_select_image_by_index(tmp_path) -> None:
    class ZipSession:
        def get(self, _url: str, **_: Any) -> _FakeResponse:
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as zf:
                zf.writestr("readme.txt", "not an image")
                zf.writestr("first.png", b"\x89PNG" + b"a" * 2048)
                zf.writestr("second.jpg", b"\xff\xd8" + b"b" * 2048)
            return _FakeResponse(200, {"content-type": "application/zip"}, payload.getvalue())

    image = pack.download_zip_image(ZipSession(), "https://example.test/images.zip", "#image:1", cache_dir=tmp_path)

    assert image == (b"\xff\xd8" + b"b" * 2048, "image/jpeg")


def test_download_zip_image_reuses_cached_zip_for_different_members(tmp_path) -> None:
    class ZipSession:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, **_: Any) -> _FakeResponse:
            self.calls.append(url)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as zf:
                zf.writestr("first.png", b"\x89PNG" + b"a" * 2048)
                zf.writestr("second.png", b"\x89PNG" + b"b" * 2048)
            return _FakeResponse(200, {"content-type": "application/zip"}, payload.getvalue())

    session = ZipSession()

    first = pack.download_zip_image(session, "https://example.test/images.zip", "first.png", cache_dir=tmp_path)
    second = pack.download_zip_image(session, "https://example.test/images.zip", "second.png", cache_dir=tmp_path)

    assert first == (b"\x89PNG" + b"a" * 2048, "image/png")
    assert second == (b"\x89PNG" + b"b" * 2048, "image/png")
    assert session.calls == ["https://example.test/images.zip"]


def test_download_cropped_image_crops_source_and_reuses_cache(tmp_path) -> None:
    from PIL import Image

    class ImageSession:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, **_: Any) -> _FakeResponse:
            self.calls.append(url)
            image = Image.effect_noise((80, 60), 80).convert("RGB")
            payload = io.BytesIO()
            image.save(payload, format="PNG")
            return _FakeResponse(200, {"content-type": "image/png"}, payload.getvalue())

    session = ImageSession()

    first = pack.download_cropped_image(
        session,
        "https://example.test/source.png",
        (10, 5, 50, 35),
        cache_dir=tmp_path,
    )

    assert first is not None
    assert first[1] == "image/png"
    cropped = Image.open(io.BytesIO(first[0]))
    assert cropped.size == (40, 30)
    assert session.calls == ["https://example.test/source.png"]

    second = pack.download_cropped_image(
        _NoNetworkSession(),
        "https://example.test/source.png",
        (10, 5, 50, 35),
        cache_dir=tmp_path,
    )

    assert second == first


def test_download_cropped_image_rejects_invalid_crop(tmp_path) -> None:
    from PIL import Image

    class ImageSession:
        def get(self, _url: str, **_: Any) -> _FakeResponse:
            image = Image.effect_noise((80, 60), 80).convert("RGB")
            payload = io.BytesIO()
            image.save(payload, format="PNG")
            return _FakeResponse(200, {"content-type": "image/png"}, payload.getvalue())

    image = pack.download_cropped_image(
        ImageSession(),
        "https://example.test/source.png",
        (10, 5, 500, 35),
        cache_dir=tmp_path,
    )

    assert image is None


def test_official_zip_allowlist_adds_targeted_chibi_and_expression_sources() -> None:
    assert pack.source_form("official_chibi_ahs_sd_kaai_yuki_zip") == "chibi"
    assert pack.source_form("official_expression_tokyo6_rikka_angry_winter_zip") == "expression"

    assert pack.ZIP_IMAGE_URLS["kaai_yuki"] == [
        (
            "official_chibi_ahs_sd_kaai_yuki_zip",
            pack.AHS_SD_CHARACTER_ZIP,
            "ahs_sdcharactor/kaaiyuki.png",
        )
    ]
    assert pack.ZIP_IMAGE_URLS["tsurumaki_maki"] == [
        (
            "official_chibi_ahs_sd_tsurumaki_maki_zip",
            pack.AHS_SD_CHARACTER_ZIP,
            "ahs_sdcharactor/tsurumakimaki.png",
        )
    ]
    assert any(member == "#image:6" for _source, _zip_url, member in pack.ZIP_IMAGE_URLS["koharu_rikka"])
    assert any(member == "#image:14" for _source, _zip_url, member in pack.ZIP_IMAGE_URLS["natsuki_karin"])
    assert any(member == "#image:23" for _source, _zip_url, member in pack.ZIP_IMAGE_URLS["hanakuma_chifuyu"])


def test_tokyo6_line_sticker_allowlist_adds_chibi_sources() -> None:
    expected = {
        "koharu_rikka": (
            "official_chibi_line_tokyo6_characters_25516967_648064406",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/648064406/android/sticker.png?v=1",
        ),
        "natsuki_karin": (
            "official_chibi_line_tokyo6_characters_25516967_648064412",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/648064412/android/sticker.png?v=1",
        ),
        "hanakuma_chifuyu": (
            "official_chibi_line_tokyo6_characters_25516967_648064408",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/648064408/android/sticker.png?v=1",
        ),
    }

    for character_id, (source, url) in expected.items():
        assert pack.source_form(source) == "chibi"
        assert dict(pack.EXTRA_IMAGE_URLS[character_id])[source] == url


def test_crypton_line_sticker_expression_allowlist_uses_single_character_sources() -> None:
    expected = {
        "vocaloid_hatsune_miku": "official_expression_line_crypton_1349_miku_24346",
        "vocaloid_kagamine_rin": "official_expression_line_crypton_1349_rin_24347",
        "vocaloid_kagamine_len": "official_expression_line_crypton_1349_len_24348",
        "vocaloid_megurine_luka": "official_expression_line_crypton_1349_luka_24349",
        "vocaloid_meiko": "official_expression_line_crypton_1349_meiko_24355",
        "vocaloid_kaito": "official_expression_line_crypton_1349_kaito_24352",
    }

    for character_id, source in expected.items():
        assert pack.source_form(source) == "expression"
        urls = dict(pack.EXTRA_IMAGE_URLS[character_id])
        url = urls[source]
        assert isinstance(url, str)
        assert url.startswith("https://stickershop.line-scdn.net/stickershop/v1/sticker/")


def test_ahs_line_sticker_expression_allowlist_adds_tsurumaki_maki() -> None:
    source = "official_expression_line_ahs_maki_14308849_375929074"

    assert pack.source_form(source) == "expression"
    urls = dict(pack.EXTRA_IMAGE_URLS["tsurumaki_maki"])
    assert urls[source] == (
        "https://stickershop.line-scdn.net/stickershop/v1/sticker/375929074/android/sticker.png?v=1"
    )


def test_ahs_line_sticker_expression_allowlist_adds_kaai_yuki() -> None:
    source = "official_expression_line_ahs_yuki_1125044_5105717"

    assert pack.source_form(source) == "expression"
    urls = dict(pack.EXTRA_IMAGE_URLS["kaai_yuki"])
    assert urls[source] == "https://stickershop.line-scdn.net/stickershop/v1/sticker/5105717/android/sticker.png?v=1"


def test_vocalomakets_line_sticker_expression_allowlist_adds_yukari_and_akari() -> None:
    expected = {
        "yuzuki_yukari": (
            "official_expression_line_vocalomakets_yukari_1018051_796527",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/796527/android/sticker.png?v=1",
        ),
        "kizuna_akari": (
            "official_expression_line_vocalomakets_akari_6622254_153063566",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/153063566/android/sticker.png?v=1",
        ),
    }

    for character_id, (source, url) in expected.items():
        assert pack.source_form(source) == "expression"
        assert dict(pack.EXTRA_IMAGE_URLS[character_id])[source] == url


def test_internet_and_mtk_line_sticker_expression_allowlist_adds_gumi_and_una() -> None:
    expected = {
        "gumi": (
            "official_expression_line_internet_gumi_1485874_17915994",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/17915994/iPhone/sticker@2x.png?v=1",
        ),
        "otomachi_una": (
            "official_expression_line_mtk_una_1396541_15392026",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/15392026/android/sticker.png?v=1",
        ),
    }

    for character_id, (source, url) in expected.items():
        assert pack.source_form(source) == "expression"
        assert dict(pack.EXTRA_IMAGE_URLS[character_id])[source] == url


def test_line_sticker_allowlists_add_remaining_chibi_sources() -> None:
    expected = {
        "gumi": (
            "official_chibi_line_internet_gumi_1485874_17915992",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/17915992/iPhone/sticker@2x.png?v=1",
        ),
        "otomachi_una": (
            "official_chibi_line_mtk_una_1396541_15392046",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/15392046/android/sticker.png?v=1",
        ),
        "yuzuki_yukari": (
            "official_chibi_line_vocalomakets_yukari_1018051_796519",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/796519/android/sticker.png?v=1",
        ),
        "kizuna_akari": (
            "official_chibi_line_vocalomakets_akari_6622254_153063560",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/153063560/android/sticker.png?v=1",
        ),
        "kamui_gakupo": (
            "official_chibi_line_internet_gackpoid_12823915_339791279",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/339791279/android/sticker.png?v=1",
        ),
    }

    for character_id, (source, url) in expected.items():
        assert pack.source_form(source) == "chibi"
        assert dict(pack.EXTRA_IMAGE_URLS[character_id])[source] == url


def test_gackpoid_line_sticker_allowlist_adds_expression_source() -> None:
    source = "official_expression_line_internet_gackpoid_12823915_339791316"

    assert pack.source_form(source) == "expression"
    assert dict(pack.EXTRA_IMAGE_URLS["kamui_gakupo"])[source] == (
        "https://stickershop.line-scdn.net/stickershop/v1/sticker/339791316/android/sticker.png?v=1"
    )


def test_official_normal_proportion_allowlist_adds_standing_art_sources() -> None:
    expected = {
        "kafu": (
            "official_sheet_kamitsubaki_kafu_standing_202309",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2023/09/c153a574a67714bccd2d44c863830597-1920x2711.jpg",
        ),
        "gumi": (
            "official_sheet_gumi_aivoice2_standing",
            "https://www.ssw.co.jp/products/talk/aivoice2_gumi/images/aiv2gm_nbg.png",
        ),
        "kamui_gakupo": (
            "official_sheet_gackpoid_v3_native",
            "https://www.ssw.co.jp/products/vocal3/gackpoid/i/index_image/n_b.jpg",
        ),
        "lily": (
            "official_sheet_lily_v3_front",
            "https://www.ssw.co.jp/products/vocal3/lily/images/v3lily_front_b.jpg",
        ),
        "otomachi_una": (
            "official_sheet_una_aivoice2_standing",
            "https://otomachiuna.jp/wp-content/uploads/2025/01/AIVOICE2ウナ_立ち絵.png",
        ),
        "yuzuki_yukari": (
            "official_sheet_vocalomakets_yukari_aivoice2_config",
            "https://vocalomakets.com/wp-content/uploads/2025/09/yukari.jpg",
        ),
        "kizuna_akari": (
            "official_sheet_vocalomakets_akari_aivoice2_config",
            "https://vocalomakets.com/wp-content/uploads/2025/09/akari.jpg",
        ),
        "kyomachi_seika": (
            "official_sheet_seika_town_character_hp1",
            "https://kyomachi-seika.jp/wp-content/uploads/2014/07/HP1-187x300.png",
        ),
        "sekai": (
            "official_sheet_piapro_sekai_standing",
            "https://piapro.jp/pages/character_images/character/ch_img_sekai.jpg",
        ),
        "haru": (
            "official_sheet_piapro_haru_standing",
            "https://piapro.jp/pages/character_images/character/ch_img_haru.jpg",
        ),
        "one": (
            "official_sheet_piapro_one_standing",
            "https://piapro.jp/pages/character_images/character/ch_img_one.jpg",
        ),
        "mayu": (
            "official_sheet_piapro_mayu_standing",
            "https://piapro.jp/pages/character_images/character/ch_img_mayu.jpg",
        ),
        "kaai_yuki": (
            "official_sheet_ahs_press_yuki_vocaloid4_illust",
            "https://www.ah-soft.com/images/press/vocaloid/vocaloid4_yuki_illust.jpg",
        ),
        "nekomura_iroha": (
            "official_sheet_ahs_press_iroha_synthv2_illust",
            "https://www.ah-soft.com/images/press/synth-v/synth-v2_iroha_illust.jpg",
        ),
        "tohoku_zunko": (
            "official_sheet_ahs_press_zunko_voicepeak_illust",
            "https://www.ah-soft.com/images/press/voice/voicepeak_zunko_illust.png",
        ),
        "tohoku_kiritan": (
            "official_sheet_ahs_press_kiritan_voicepeak_illust",
            "https://www.ah-soft.com/images/press/voice/voicepeak_kiritan_illust.png",
        ),
        "tsuina_chan": (
            "official_sheet_ahs_press_tsuina_synthv2_illust",
            "https://www.ah-soft.com/images/press/synth-v/synth-v2_tsuina_illust.png",
        ),
    }

    for character_id, (source, url) in expected.items():
        assert pack.source_form(source) == "normal_proportion"
        assert dict(pack.EXTRA_IMAGE_URLS[character_id])[source] == url


def test_moegirl_reviewed_sheet_allowlist_adds_normal_proportion_sources() -> None:
    expected = {
        "xia_yuyao": (
            "moegirl_sheet_xia_yuyao_cd02_v02",
            "https://img.moegirl.org.cn/common/thumb/f/fc/Cd02_v02.jpg/934px-Cd02_v02.jpg",
        ),
        "dongfang_zhizi": (
            "moegirl_sheet_dongfang_zhizi_blossom_standing",
            "https://img.moegirl.org.cn/common/thumb/f/ff/东方栀子Blossom立绘.png/557px-东方栀子Blossom立绘.png",
        ),
    }

    for character_id, (source, url) in expected.items():
        assert pack.source_form(source) == "normal_proportion"
        assert dict(pack.EXTRA_IMAGE_URLS[character_id])[source] == url


def test_dongfangzhizi_official_site_avatar_adds_chibi_only() -> None:
    source = "official_chibi_dongfangzhizi_voicebank_site_avatar_20251225"
    url = "https://dongfangzhizi.top/wp-content/uploads/2025/12/Image_16002726312696.png"
    sources = dict(pack.EXTRA_IMAGE_URLS["dongfang_zhizi"])

    assert pack.source_form(source) == "chibi"
    assert sources[source] == url


def test_dongfangzhizi_bilibili_management_face_adds_expression_source() -> None:
    source = "official_expression_bilibili_space_62351857_era_shine_face"
    sources = dict(pack.EXTRA_IMAGE_URLS["dongfang_zhizi"])

    assert pack.source_form(source) == "expression"
    assert sources[source] == "https://i1.hdslb.com/bfs/face/d64b28af735d4cd0c872cdb12cc34edc97186561.jpg"


def test_synthvwiki_sheet_sources_count_as_normal_proportion() -> None:
    expected = {
        "chi_yu": (
            "synthvwiki_sheet_chiyu_medium2050_concept",
            "https://static.wikia.nocookie.net/synthv/images/d/d4/Medium_2050_chiyu.jpg/revision/latest?cb=20201126150549",
        ),
        "mu_xin": (
            "synthvwiki_sheet_muxin_game_concept",
            "https://static.wikia.nocookie.net/synthv/images/b/b6/Muxin_game_concept.jpg/revision/latest?cb=20200715124135",
        ),
    }

    for character_id, (source, url) in expected.items():
        assert pack.source_form(source) == "normal_proportion"
        assert dict(pack.SYNTHV_WIKI_IMAGE_URLS[character_id])[source] == url


def test_vcpedia_sheet_sources_count_as_normal_proportion_with_referer() -> None:
    expected = {
        "cang_qiong": (
            "vcpedia_sheet_cangqiong_character_art",
            "https://media.vcpedia.cn/thumb/b/bd/%E8%8B%8D%E7%A9%B9.png/800px-%E8%8B%8D%E7%A9%B9.png",
            "https://vcpedia.cn/%E8%8B%8D%E7%A9%B9",
        ),
        "shi_an": (
            "vcpedia_sheet_shian_character_art",
            "https://media.vcpedia.cn/thumb/4/4e/%E8%AF%97%E5%B2%B8%E7%AB%8B%E7%BB%98.png/800px-%E8%AF%97%E5%B2%B8%E7%AB%8B%E7%BB%98.png",
            "https://vcpedia.cn/%E8%AF%97%E5%B2%B8",
        ),
    }

    for character_id, (source, url, referer) in expected.items():
        assert pack.source_form(source) == "normal_proportion"
        image_url = dict(pack.EXTRA_IMAGE_URLS[character_id])[source]

        assert isinstance(image_url, pack.RefererImageUrl)
        assert image_url.url == url
        assert image_url.referer == referer


def test_rejected_synthvwiki_sheet_candidates_are_not_allowlisted() -> None:
    cang_sources = dict(pack.SYNTHV_WIKI_IMAGE_URLS["cang_qiong"])
    shian_sources = dict(pack.SYNTHV_WIKI_IMAGE_URLS["shi_an"])
    cang_crop_sources = {source for source, _url, _crop in pack.CROPPED_IMAGE_URLS["cang_qiong"]}
    shian_crop_sources = {source for source, _url, _crop in pack.CROPPED_IMAGE_URLS["shi_an"]}

    assert "synthvwiki_sheet_cangqiong_genesis_crystal_reference" not in cang_sources
    assert "synthvwiki_sheet_shian_game_concept" not in shian_sources
    assert "synthvwiki_sheet_cangqiong_genesis_crystal_reference" not in cang_crop_sources
    assert "synthvwiki_sheet_shian_game_concept" not in shian_crop_sources


def test_synthvwiki_cropped_concept_sources_count_as_chibi_not_normal() -> None:
    expected = {
        "cang_qiong": (
            "synthvwiki_chibi_crop_cangqiong_concept_front",
            "https://static.wikia.nocookie.net/synthv/images/7/72/Cangqiong_concept.png/revision/latest?cb=20190825202255",
            (824, 0, 1948, 1151),
        ),
        "shi_an": (
            "synthvwiki_chibi_crop_shian_concept_front",
            "https://static.wikia.nocookie.net/synthv/images/5/5f/Shian_concept.png/revision/latest?cb=20190825202705",
            (802, 0, 1896, 1184),
        ),
    }

    for character_id, item in expected.items():
        source, _url, _crop = item

        assert pack.source_form(source) == "chibi"
        assert item in pack.CROPPED_IMAGE_URLS[character_id]


def test_nekomura_iroha_ahs_official_face_crop_adds_expression_source() -> None:
    source = "official_expression_crop_ahs_iroha_v2_official_face"
    image_url = pack.RefererImageUrl(
        url="https://www.ah-soft.com/images/products/vocaloid/iroha/iroha_official.jpg",
        referer="https://www.ah-soft.com/vocaloid/iroha_v2/",
    )
    item = (source, image_url, (0, 0, 120, 104))

    assert pack.source_form(source) == "expression"
    assert item in pack.CROPPED_IMAGE_URLS["nekomura_iroha"]


def test_nekomura_iroha_ahs_vocaloid4_press_crop_adds_chibi_source() -> None:
    source = "official_chibi_crop_ahs_iroha_vocaloid4_press_red_sd"
    item = (
        source,
        "https://www.ah-soft.com/images/press/vocaloid/vocaloid4_iroha_illust.jpg",
        (1180, 120, 1880, 760),
    )

    assert pack.source_form(source) == "chibi"
    assert item in pack.CROPPED_IMAGE_URLS["nekomura_iroha"]


def test_mayu_atpress_exit_tunes_crop_adds_chibi_source() -> None:
    source = "press_release_chibi_crop_atpress_exit_tunes_mayu_strap_sitting_20121205"
    item = (
        source,
        "https://www.atpress.ne.jp/releases/32052/3_3.jpg",
        (762, 359, 872, 524),
    )

    assert pack.source_form(source) == "chibi"
    assert item in pack.CROPPED_IMAGE_URLS["mayu"]


def test_rime_package_source_counts_as_normal_proportion() -> None:
    source = "official_sheet_rime_package"

    assert pack.source_form(source) == "normal_proportion"
    assert dict(pack.EXTRA_IMAGE_URLS["rime"])[source] == (
        "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/07/rime_package_1.png"
    )


def test_lily_animove_official_goods_adds_chibi_source() -> None:
    source = "official_chibi_animove_lily_goods_sd_20130401"

    assert pack.source_form(source) == "chibi"
    assert dict(pack.EXTRA_IMAGE_URLS["lily"])[source] == (
        "https://animove.jp/lily/goods/2013/04/01/130401_sd.jpg"
    )


def test_selected_official_portrait_sources_count_as_expression() -> None:
    expected = {
        "kafu": "official_profile_kafu_about",
        "sekai": "official_profile_sekai_about",
        "coko": "official_profile_coko_about1",
        "haru": "official_profile_haru_about",
        "mayu": "official_profile_mayu_loves",
    }

    for character_id, source in expected.items():
        assert pack.source_form(source) == "expression"
        assert source in dict(pack.EXTRA_IMAGE_URLS[character_id])

    assert pack.source_form("official_profile_rime_about") == "profile_art"


def test_findme_kamitsubaki_plush_allowlist_adds_selected_chibi_sources() -> None:
    expected = {
        "kafu": (
            "official_chibi_findme_kafu_1st_anniv_plush_mascot",
            "https://cdn.shopify.com/s/files/1/0551/7692/1261/products/kafu_1stanv_item.jpg?v=1658397524",
        ),
        "sekai": (
            "official_chibi_findme_sekai_1st_anniv_plush_mascot",
            "https://cdn.shopify.com/s/files/1/0551/7692/1261/products/sekai_1stanv_item.jpg?v=1658397546",
        ),
        "rime": (
            "official_chibi_findme_rime_niconico2023_plush_front",
            "https://cdn.shopify.com/s/files/1/0551/7692/1261/files/Rime_Front.jpg?v=1683187820",
        ),
        "coko": (
            "official_chibi_findme_coko_niconico2023_plush_front",
            "https://cdn.shopify.com/s/files/1/0551/7692/1261/files/Koko_Front.jpg?v=1683187795",
        ),
    }

    for character_id, (source, url) in expected.items():
        assert pack.source_form(source) == "chibi"
        assert dict(pack.EXTRA_IMAGE_URLS[character_id])[source] == url


def test_rejected_findme_kamitsubaki_plush_candidates_are_not_allowlisted() -> None:
    haru_sources = dict(pack.EXTRA_IMAGE_URLS["haru"])
    selected_sources = {
        source
        for character_id in ("kafu", "sekai", "rime", "coko")
        for source, _url in pack.EXTRA_IMAGE_URLS[character_id]
    }

    assert "official_chibi_findme_haru_niconico2023_plush_front" not in haru_sources
    assert "official_chibi_findme_haru_niconico2023_plush_back" not in haru_sources
    assert not any(
        source.startswith("official_chibi_findme_") and source.endswith("_back")
        for source in selected_sources
    )


def test_teto_line_sticker_allowlist_adds_chibi_and_expression() -> None:
    urls = dict(pack.EXTRA_IMAGE_URLS["kasane_teto"])

    chibi_source = "official_chibi_line_twindrill_teto_stamp1_9246166_237904694"
    expression_source = "official_expression_line_twindrill_teto_stamp1_9246166_237904695"

    assert pack.source_form(chibi_source) == "chibi"
    assert pack.source_form(expression_source) == "expression"
    assert urls[chibi_source] == "https://stickershop.line-scdn.net/stickershop/v1/sticker/237904694/android/sticker.png?v=1"
    assert urls[expression_source] == (
        "https://stickershop.line-scdn.net/stickershop/v1/sticker/237904695/android/sticker.png?v=1"
    )


def test_1stplace_and_gyroid_line_sticker_allowlists_add_chibi_and_expression() -> None:
    expected = {
        "ia": (
            "official_chibi_line_1stplace_ia_vol1_1381883_14927590",
            "official_expression_line_1stplace_ia_vol1_1381883_14927591",
            "14927590",
            "14927591",
        ),
        "one": (
            "official_chibi_line_1stplace_one_vol1_1830278_25943272",
            "official_expression_line_1stplace_one_vol1_1830278_25943273",
            "25943272",
            "25943273",
        ),
        "v_flower": (
            "official_chibi_line_gyroid_vflower_hanachang_1292567_11849125",
            "official_expression_line_gyroid_vflower_hanachang_1292567_11849127",
            "11849125",
            "11849127",
        ),
    }

    for character_id, (chibi_source, expression_source, chibi_id, expression_id) in expected.items():
        urls = dict(pack.EXTRA_IMAGE_URLS[character_id])

        assert pack.source_form(chibi_source) == "chibi"
        assert pack.source_form(expression_source) == "expression"
        assert urls[chibi_source] == (
            f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{chibi_id}/android/sticker.png?v=1"
        )
        assert urls[expression_source] == (
            f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{expression_id}/android/sticker.png?v=1"
        )


def test_kamitsubaki_gif_frame_allowlist_adds_isotope_expression_sources() -> None:
    expected = {
        "kafu": (
            "official_expression_kamitsubaki_gif4_kafu_frame20",
            "https://www.mediafire.com/file/p83f04kl9e8z0rj/01_KAFU.gif/file",
            20,
        ),
        "sekai": (
            "official_expression_kamitsubaki_gif2_sekai_frame28",
            "https://www.mediafire.com/file/0hs4qw13sf2myht/02_SEKAI.gif/file",
            28,
        ),
        "rime": (
            "official_expression_kamitsubaki_gif4_rime_frame40",
            "https://www.mediafire.com/file/xiky8yxzpjw2mze/03_RIME.gif/file",
            40,
        ),
        "coko": (
            "official_expression_kamitsubaki_gif3_coko_frame36",
            "https://www.mediafire.com/file/9u6qe0txswlcg92/04_COKO.gif/file",
            36,
        ),
    }

    for character_id, item in expected.items():
        source, url, frame_index = item

        assert pack.source_form(source) == "expression"
        assert item in pack.ANIMATED_FRAME_IMAGE_URLS[character_id]
        assert url.startswith("https://www.mediafire.com/file/")
        assert frame_index >= 0


def test_1stplace_ia_official_visual_adds_full_body_source() -> None:
    source = "official_full_ia_aria_mob_main_202010"

    assert pack.source_form(source) == "full_body"
    assert dict(pack.EXTRA_IMAGE_URLS["ia"])[source] == (
        "https://ia-aria.com/wp-content/uploads/2020/10/mob_main_202010_-1.jpg"
    )


def test_vsinger_api_zhiyu_moke_cover_adds_full_body_source() -> None:
    source = "official_full_vsinger_api_zhiyu_moke_cover_0"

    assert pack.source_form(source) == "full_body"
    assert dict(pack.EXTRA_IMAGE_URLS["zhiyu_moke"])[source] == (
        "https://res.vsinger.com/images/ec7b130d2e36888de23c91191223c64d.png"
    )


def test_vsinger_api_luo_tianyi_q_model_cover_adds_chibi_source() -> None:
    source = "official_chibi_vsinger_api_luo_tianyi_q_model_cover"

    assert pack.source_form(source) == "chibi"
    assert dict(pack.EXTRA_IMAGE_URLS["luo_tianyi"])[source] == (
        "https://res.vsinger.com/images/cffa18b20ce29010364f33ae94c6a8b0.jpg"
    )


def test_luminous_vsinger_archived_sticker_allowlist_adds_zh_chibi_and_expression_sources() -> None:
    expected = {
        "luo_tianyi": [
            (
                "archived_expression_luminous_vsinger_luo_tianyi_13th_bayinqixiang_meili",
                "expression",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E6%B4%9B%E5%A4%A9%E4%BE%9D13%E5%91%A8%E5%B9%B4%E5%85%AB%E9%9F%B3%E5%A5%87%E5%93%8D%E8%A1%A8%E6%83%85%E5%8C%85/%E7%BE%8E%E4%B8%BD.png",
            ),
        ],
        "yan_he": [
            (
                "archived_expression_luminous_vsinger_yan_he_12th_ku_ku",
                "expression",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E8%A8%80%E5%92%8C12%E5%91%A8%E5%B9%B4%E7%BA%AA%E5%BF%B5%E8%A1%A8%E6%83%85%E5%8C%85/%E5%93%AD%E5%93%AD.png",
            ),
            (
                "archived_chibi_luminous_vsinger_yan_he_12th_xingfen",
                "chibi",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E8%A8%80%E5%92%8C12%E5%91%A8%E5%B9%B4%E7%BA%AA%E5%BF%B5%E8%A1%A8%E6%83%85%E5%8C%85/%E5%85%B4%E5%A5%8B.png",
            ),
        ],
        "yuezheng_ling": [
            (
                "archived_expression_luminous_vsinger_yuezheng_ling_10th_xianglingguang_zan",
                "expression",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E4%B9%90%E6%AD%A3%E7%BB%AB%E5%8D%81%E5%91%A8%E5%B9%B4%E5%90%91%E7%BB%AB%E5%85%89%E8%A1%A8%E6%83%85%E5%8C%85/%E8%B5%9E.png",
            ),
            (
                "archived_chibi_luminous_vsinger_yuezheng_ling_10th_xianglingguang_laile",
                "chibi",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E4%B9%90%E6%AD%A3%E7%BB%AB%E5%8D%81%E5%91%A8%E5%B9%B4%E5%90%91%E7%BB%AB%E5%85%89%E8%A1%A8%E6%83%85%E5%8C%85/%E6%9D%A5%E4%BA%86.png",
            ),
        ],
        "yuezheng_longya": [
            (
                "archived_expression_luminous_vsinger_yuezheng_longya_6th_lb01",
                "expression",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E4%B9%90%E6%AD%A3%E9%BE%99%E7%89%99%E5%85%AD%E5%91%A8%E5%B9%B4/LB01.png",
            ),
            (
                "archived_chibi_luminous_vsinger_yuezheng_longya_6th_lb13",
                "chibi",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E4%B9%90%E6%AD%A3%E9%BE%99%E7%89%99%E5%85%AD%E5%91%A8%E5%B9%B4/LB13.png",
            ),
        ],
        "zhiyu_moke": [
            (
                "archived_expression_luminous_vsinger_zhiyu_moke_6th_lq18",
                "expression",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E5%BE%B5%E7%BE%BD%E6%91%A9%E6%9F%AF%E5%85%AD%E5%91%A8%E5%B9%B4/LQ18.png",
            ),
            (
                "archived_chibi_luminous_vsinger_zhiyu_moke_6th_lq15",
                "chibi",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E5%BE%B5%E7%BE%BD%E6%91%A9%E6%9F%AF%E5%85%AD%E5%91%A8%E5%B9%B4/LQ15.png",
            ),
        ],
        "mo_qingxian": [
            (
                "archived_expression_luminous_vsinger_mo_qingxian_7th_ku_ku",
                "expression",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E5%A2%A8%E6%B8%85%E5%BC%A6%E4%B8%83%E5%91%A8%E5%B9%B4%E8%A1%A8%E6%83%85%E5%8C%85/%E5%93%AD%E5%93%AD.png",
            ),
            (
                "archived_chibi_luminous_vsinger_mo_qingxian_7th_guaiqiao",
                "chibi",
                "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E5%A2%A8%E6%B8%85%E5%BC%A6%E4%B8%83%E5%91%A8%E5%B9%B4%E8%A1%A8%E6%83%85%E5%8C%85/%E4%B9%96%E5%B7%A7.png",
            ),
        ],
    }

    for character_id, items in expected.items():
        urls = dict(pack.EXTRA_IMAGE_URLS[character_id])
        for source, form, url in items:
            assert pack.source_form(source) == form
            assert urls[source] == url


def test_rejected_luminous_vsinger_sticker_candidates_are_not_allowlisted() -> None:
    urls = dict(pack.EXTRA_IMAGE_URLS["zhiyu_moke"])

    assert not any("zhiyu_moke_7th" in source for source in urls)
    assert not any("qizhounian" in source for source in urls)


def test_bilibili_emote_allowlist_adds_xingchen_and_haiyi_chibi_expression_sources() -> None:
    expected = {
        "xingchen": {
            "official_chibi_bilibili_emote_pkg264_4391": (
                "chibi",
                "https://i0.hdslb.com/bfs/emote/fd8aa275d5d91cdf71410bc1a738415fd6e2ab86.png",
            ),
            "official_expression_bilibili_emote_pkg264_4401": (
                "expression",
                "https://i0.hdslb.com/bfs/emote/1337af7b041c3c061d3d725d27a6655795d7d9ee.png",
            ),
        },
        "hai_yi": {
            "official_chibi_bilibili_emote_pkg441_7648": (
                "chibi",
                "https://i0.hdslb.com/bfs/emote/c548b527593a3d21e5082c26abbf61c63701f11a.png",
            ),
            "official_expression_bilibili_emote_pkg441_7659": (
                "expression",
                "https://i0.hdslb.com/bfs/emote/20db290d7183e19cbf74563ef2c3434ca387ce95.png",
            ),
        },
    }

    for character_id, items in expected.items():
        urls = dict(pack.EXTRA_IMAGE_URLS[character_id])
        for source, (form, url) in items.items():
            assert pack.source_form(source) == form
            assert urls[source] == url


def test_rejected_bilibili_emote_candidates_are_not_bulk_allowlisted() -> None:
    xingchen_sources = dict(pack.EXTRA_IMAGE_URLS["xingchen"])
    haiyi_sources = dict(pack.EXTRA_IMAGE_URLS["hai_yi"])

    assert sorted(source for source in xingchen_sources if "bilibili_emote_pkg264" in source) == [
        "official_chibi_bilibili_emote_pkg264_4391",
        "official_expression_bilibili_emote_pkg264_4401",
    ]
    assert sorted(source for source in haiyi_sources if "bilibili_emote_pkg441" in source) == [
        "official_chibi_bilibili_emote_pkg441_7648",
        "official_expression_bilibili_emote_pkg441_7659",
    ]
    assert not any("4393" in source for source in xingchen_sources)
    assert not any("4396" in source for source in xingchen_sources)
    assert not any("7656" in source for source in haiyi_sources)
    assert not any("7657" in source for source in haiyi_sources)


def test_medium5_qicheng_bilibili_emote_allowlist_adds_remaining_sources() -> None:
    expected = {
        "cang_qiong": {
            "official_expression_bilibili_emote_pkg7996_109372": (
                "expression",
                "https://i0.hdslb.com/bfs/garb/95c79b5b91f8f2adf8d4f494a24a213604190d24.png",
            ),
        },
        "chi_yu": {
            "official_chibi_bilibili_emote_pkg7995_109347": (
                "chibi",
                "https://i0.hdslb.com/bfs/garb/a782499ff153db60ccde7c0a98e1ce4fb6c7de3f.png",
            ),
            "official_expression_bilibili_emote_pkg7995_109362": (
                "expression",
                "https://i0.hdslb.com/bfs/garb/5f9a7d559008f1b25a713cb0f4d9da199644f13a.png",
            ),
        },
        "shi_an": {
            "official_expression_bilibili_emote_pkg7996_109374": (
                "expression",
                "https://i0.hdslb.com/bfs/garb/6e86d39d56a0c9ff1f07632dfb15cb0499c1c474.png",
            ),
        },
        "mu_xin": {
            "official_chibi_bilibili_emote_pkg3863_53979": (
                "chibi",
                "https://i0.hdslb.com/bfs/garb/b83b81cea5d2df9031b5c8e2623ab6ce751a0703.png",
            ),
            "official_expression_bilibili_emote_pkg7996_109384": (
                "expression",
                "https://i0.hdslb.com/bfs/garb/fda941c100d8cdaf7287e472224c535c2ffd944d.png",
            ),
        },
        "yongye_minus": {
            "official_chibi_bilibili_emote_pkg3863_53963": (
                "chibi",
                "https://i0.hdslb.com/bfs/garb/e8190a3d018aa466ecb34d33bccc0bf8f9f5e999.png",
            ),
            "official_expression_bilibili_emote_pkg7996_109370": (
                "expression",
                "https://i0.hdslb.com/bfs/garb/98f8ddb062c1b86e232e36a6960457990a1e8dda.png",
            ),
        },
    }

    for character_id, items in expected.items():
        urls = dict(pack.EXTRA_IMAGE_URLS[character_id])
        for source, (form, url) in items.items():
            assert pack.source_form(source) == form
            assert urls[source] == url


def test_rejected_medium5_qicheng_emote_candidates_are_not_bulk_allowlisted() -> None:
    expected_sources = {
        "cang_qiong": ["official_expression_bilibili_emote_pkg7996_109372"],
        "chi_yu": [
            "official_chibi_bilibili_emote_pkg7995_109347",
            "official_expression_bilibili_emote_pkg7995_109362",
        ],
        "shi_an": ["official_expression_bilibili_emote_pkg7996_109374"],
        "mu_xin": [
            "official_chibi_bilibili_emote_pkg3863_53979",
            "official_expression_bilibili_emote_pkg7996_109384",
        ],
        "yongye_minus": [
            "official_chibi_bilibili_emote_pkg3863_53963",
            "official_expression_bilibili_emote_pkg7996_109370",
        ],
    }

    for character_id, allowed in expected_sources.items():
        sources = dict(pack.EXTRA_IMAGE_URLS[character_id])
        qicheng_sources = [
            source
            for source in sources
            if "bilibili_emote_pkg3863" in source
            or "bilibili_emote_pkg3911" in source
            or "bilibili_emote_pkg7995" in source
            or "bilibili_emote_pkg7996" in source
        ]
        assert sorted(qicheng_sources) == sorted(allowed)

    assert not any(
        "bilibili_emote_pkg" in source for source, _url in pack.EXTRA_IMAGE_URLS["dongfang_zhizi"]
    )
    assert not any("109345" in source for source, _url in pack.EXTRA_IMAGE_URLS["cang_qiong"])
    assert not any("109383" in source for source, _url in pack.EXTRA_IMAGE_URLS["mu_xin"])


def test_voicemith_line_sticker_allowlist_adds_xia_yuyao_chibi_and_expression_sources() -> None:
    expected = {
        "official_chibi_line_voicemith_ecapsule_xia_yuyao3_5077007_98039968": (
            "chibi",
            "98039968",
        ),
        "official_expression_line_voicemith_ecapsule_xia_yuyao3_5077007_98039985": (
            "expression",
            "98039985",
        ),
    }
    urls = dict(pack.EXTRA_IMAGE_URLS["xia_yuyao"])

    for source, (form, sticker_id) in expected.items():
        assert pack.source_form(source) == form
        assert urls[source] == (
            f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/android/sticker.png?v=1"
        )


def test_rejected_xia_yuyao_line_sticker_candidates_are_not_bulk_allowlisted() -> None:
    urls = dict(pack.EXTRA_IMAGE_URLS["xia_yuyao"])
    line_sources = [source for source in urls if source.startswith("official_") and "line_voicemith_ecapsule" in source]

    assert sorted(line_sources) == [
        "official_chibi_line_voicemith_ecapsule_xia_yuyao3_5077007_98039968",
        "official_expression_line_voicemith_ecapsule_xia_yuyao3_5077007_98039985",
    ]
    assert not any("98039950" in source for source in urls)
    assert not any("98039951" in source for source in urls)
    assert not any("98039973" in source for source in urls)


def test_line_creator_xin_hua_sticker_allowlist_adds_chibi_and_expression_sources() -> None:
    expected = {
        "line_creator_chibi_facio_yumei_xin_hua_1245282_9950512": (
            "chibi",
            "9950512",
        ),
        "line_creator_expression_facio_yumei_xin_hua_1245282_9950513": (
            "expression",
            "9950513",
        ),
    }
    urls = dict(pack.EXTRA_IMAGE_URLS["xin_hua"])

    for source, (form, sticker_id) in expected.items():
        assert pack.source_form(source) == form
        assert urls[source] == (
            f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/android/sticker.png?v=1"
        )


def test_rejected_xin_hua_line_sticker_candidates_are_not_bulk_allowlisted() -> None:
    urls = dict(pack.EXTRA_IMAGE_URLS["xin_hua"])
    line_sources = [source for source in urls if source.startswith("line_creator_") and "xin_hua_1245282" in source]

    assert sorted(line_sources) == [
        "line_creator_chibi_facio_yumei_xin_hua_1245282_9950512",
        "line_creator_expression_facio_yumei_xin_hua_1245282_9950513",
    ]
    assert not any("1245282_1245282" in source for source in urls)
    assert not any("9950514" in source for source in urls)
    assert not any("9950524" in source for source in urls)
    assert not any("9950550" in source for source in urls)


def test_1stplace_ia_official_profile_art_stabilizes_centroid_without_bucket_credit() -> None:
    source = "official_profile_ia_05_jacket"

    assert pack.source_form(source) == "profile_art"
    assert dict(pack.EXTRA_IMAGE_URLS["ia"])[source] == (
        "https://ia-aria.com/wp-content/uploads/2024/01/IA05_jk-scaled.jpg"
    )


def test_sss_line_sticker_allowlist_adds_tohoku_chibi_and_expression() -> None:
    expected = {
        "tohoku_zunko": (
            "official_chibi_line_sss_zunko_1000978_95596",
            "official_expression_line_sss_zunko_1000978_95597",
            "95596",
            "95597",
        ),
        "tohoku_kiritan": (
            "official_chibi_line_sss_kiritan_1120252_4912144",
            "official_expression_line_sss_kiritan_1120252_4912145",
            "4912144",
            "4912145",
        ),
    }

    for character_id, (chibi_source, expression_source, chibi_id, expression_id) in expected.items():
        urls = dict(pack.EXTRA_IMAGE_URLS[character_id])

        assert pack.source_form(chibi_source) == "chibi"
        assert pack.source_form(expression_source) == "expression"
        assert urls[chibi_source] == (
            f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{chibi_id}/android/sticker.png?v=1"
        )
        assert urls[expression_source] == (
            f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{expression_id}/android/sticker.png?v=1"
        )


def test_seika_and_tsuina_line_sticker_allowlists_add_chibi_and_expression() -> None:
    expected = {
        "kyomachi_seika": (
            "official_chibi_line_seika_town_70th_31420910_787267833",
            "official_expression_line_seika_town_70th_31420910_787267834",
            "787267833",
            "787267834",
        ),
        "tsuina_chan": (
            "official_chibi_line_tsuina_project_vol1_15284033_398851390",
            "official_expression_line_tsuina_project_vol1_15284033_398851391",
            "398851390",
            "398851391",
        ),
    }

    for character_id, (chibi_source, expression_source, chibi_id, expression_id) in expected.items():
        urls = dict(pack.EXTRA_IMAGE_URLS[character_id])

        assert pack.source_form(chibi_source) == "chibi"
        assert pack.source_form(expression_source) == "expression"
        assert urls[chibi_source] == (
            f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{chibi_id}/android/sticker.png?v=1"
        )
        assert urls[expression_source] == (
            f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{expression_id}/android/sticker.png?v=1"
        )


def test_moegirl_file_image_urls_keeps_redirect_fallback(monkeypatch) -> None:
    class Session:
        def get(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
            payload = (
                b'{"query":{"pages":{"1":{"title":"File:Demo_Image.png",'
                b'"imageinfo":[{"url":"https://img.example/demo.png","mime":"image/png"}]}}}}'
            )
            return _FakeResponse(200, {"content-type": "application/json"}, payload)

    monkeypatch.setattr(pack, "moegirl_gallery_source", lambda _title, _index: "moegirl_full_01")

    urls = pack.moegirl_file_image_urls(Session(), ["File:Demo_Image.png"], start_index=1)

    assert urls == [
        (
            "moegirl_full_01",
            ("https://img.example/demo.png", "https://zh.moegirl.org.cn/Special:Redirect/file/Demo_Image.png"),
        )
    ]


def test_moegirl_gallery_source_treats_official_outfit_as_full_body() -> None:
    source = pack.moegirl_gallery_source("File:Minus公式服.png", 3)

    assert source == "moegirl_full_03"
    assert pack.source_form(source) == "full_body"


def test_gather_images_uses_exact_title_gallery_for_ja_character(monkeypatch, tmp_path) -> None:
    class ImageSession:
        def get(self, url: str, **_: Any) -> _FakeResponse:
            assert url in {"https://img.example/page.png", "https://img.example/gallery.png"}
            return _FakeResponse(200, {"content-type": "image/png"}, b"\x89PNG" + b"a" * 2048)

    character = pack.CharacterDef(
        character_id="ia",
        name="IA",
        aliases=["IA"],
        source_id="ia",
        query_names=["IA"],
        context_label="日V / 1st Place",
    )
    calls: list[str] = []

    monkeypatch.setattr(pack, "utaitedb_main_image", lambda _sess, _character: None)
    monkeypatch.setattr(pack, "moegirl_page_image", lambda _sess, title: "https://img.example/page.png")
    monkeypatch.setitem(pack.EXTRA_IMAGE_URLS, "ia", [])

    def gallery(_sess: Any, title: str, _character: pack.CharacterDef, *, start_index: int) -> list[tuple[str, str]]:
        calls.append(f"{title}:{start_index}")
        return [("moegirl_profile_01", "https://img.example/gallery.png")]

    monkeypatch.setattr(pack, "moegirl_gallery_urls", gallery)

    images = pack.gather_images(ImageSession(), character, cache_dir=tmp_path)

    assert calls == ["IA:1"]
    assert [item[0] for item in images] == [
        "ia_moegirl_IA.png",
        "ia_moegirl_profile_01.png",
    ]


def test_gather_images_uses_exact_moegirl_file_allowlist(monkeypatch, tmp_path) -> None:
    class ImageSession:
        def get(self, url: str, **_: Any) -> _FakeResponse:
            assert url == "https://img.example/dongfang.png"
            return _FakeResponse(200, {"content-type": "image/png"}, b"\x89PNG" + b"a" * 2048)

    character = pack.CharacterDef(
        character_id="dongfang_zhizi",
        name="东方栀子",
        aliases=["东方栀子"],
        source_id="dongfang_zhizi",
        query_names=["东方栀子"],
        context_label="中V / 中文虚拟歌姬",
    )

    monkeypatch.setattr(pack, "utaitedb_main_image", lambda _sess, _character: None)
    monkeypatch.setattr(pack, "moegirl_page_image", lambda _sess, _title: None)
    monkeypatch.setattr(pack, "moegirl_gallery_urls", lambda *_args, **_kwargs: [])
    monkeypatch.setitem(pack.EXTRA_IMAGE_URLS, "dongfang_zhizi", [])
    monkeypatch.setattr(
        pack,
        "moegirl_file_image_urls",
        lambda _sess, titles, *, start_index: [("moegirl_full_extra", "https://img.example/dongfang.png")]
        if titles == pack.MOEGIRL_EXTRA_FILES["dongfang_zhizi"] and start_index == 0
        else [],
    )

    images = pack.gather_images(ImageSession(), character, cache_dir=tmp_path)

    assert [item[0] for item in images] == ["dongfang_zhizi_moegirl_full_extra.png"]
