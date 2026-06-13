from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools import enroll_bangdream_pack as pack


@dataclass
class _FakeResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


class _RetrySession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, _url: str, **_kwargs: Any) -> _FakeResponse:
        self.calls += 1
        if self.calls == 1:
            return _FakeResponse(503, {"content-type": "text/plain"}, b"retry")
        return _FakeResponse(200, {"content-type": "image/webp"}, b"RIFF" + b"a" * 2048)


class _NoNetworkSession:
    def get(self, _url: str, **_kwargs: Any) -> _FakeResponse:
        raise AssertionError("unexpected network call")


def test_our_notes_slug_reverses_late_band_name_order() -> None:
    assert pack.our_notes_slug("nakamachi-arale") == "arale-nakamachi"
    assert pack.our_notes_slug("shiomi-hotaru") == "hotaru-shiomi"
    assert pack.our_notes_slug("suga-raika") == "raika-suga"


def test_late_band_labels_keep_ikka_dumb_rock_distinct_from_yumemita() -> None:
    assert pack.BAND_LABELS["yumemita"] == "BanG Dream! / 夢限大みゅーたいぷ"
    assert pack.BAND_LABELS["ikka-dumb-rock"] == "BanG Dream! / 一家Dumb Rock!"


def test_our_notes_urls_only_apply_to_late_official_bands() -> None:
    late_entry = pack.RosterEntry("shiomi_hotaru", "millsage", "shiomi-hotaru")
    old_entry = pack.RosterEntry("toyama_kasumi", "poppinparty", "toyama-kasumi", 1)

    assert pack.our_notes_urls(old_entry) == []
    assert pack.our_notes_urls(late_entry) == [
        (
            "normal",
            "ournotes_index",
            "https://bang-dream-on.bushimo.jp/wordpress/wp-content/themes/"
            "bang-dream-on/assets/images/common/index/img_hotaru-shiomi.webp",
        ),
        (
            "normal",
            "ournotes_character_2",
            "https://bang-dream-on.bushimo.jp/wordpress/wp-content/themes/"
            "bang-dream-on/assets/images/common/character/img_hotaru-shiomi_2.webp",
        ),
    ]


def test_our_notes_sources_count_as_normal_proportion() -> None:
    assert pack.source_form("ournotes_index") == "normal_proportion"
    assert pack.source_form("ournotes_character_2") == "normal_proportion"


def test_mini_anime_chibi_urls_only_apply_to_ave_mujica() -> None:
    ave_entry = pack.RosterEntry("misumi_uika", "avemujica", "misumi-uika", 41)
    late_entry = pack.RosterEntry("nakamachi_arale", "yumemita", "nakamachi-arale")

    assert pack.mini_anime_chibi_urls(ave_entry) == [
        (
            "expression",
            "mini_anime_chibi_uika",
            "https://anime.bang-dream.com/bandorichan/wordpress/wp-content/themes/"
            "bandorichan_v0/assets/webp/common/character/avemujica/img_chara-uika.webp",
        )
    ]
    assert pack.mini_anime_chibi_urls(late_entry) == []


def test_mini_anime_chibi_sources_count_as_chibi() -> None:
    assert pack.source_form("mini_anime_chibi_uika") == "chibi"


def test_trusted_chibi_urls_only_add_reviewed_single_character_sources() -> None:
    expected = {
        "nakamachi_arale": (
            "trusted_chibi_amiami_goods_04673015_nakamachi_arale",
            "https://img.amiami.jp/images/product/main/253/GOODS-04673015.jpg",
        ),
        "miyanaga_nonoka": (
            "trusted_chibi_amiami_goods_04673016_miyanaga_nonoka",
            "https://img.amiami.jp/images/product/main/253/GOODS-04673016.jpg",
        ),
        "minetsuki_ritsu": (
            "trusted_chibi_amiami_goods_04673017_minetsuki_ritsu",
            "https://img.amiami.jp/images/product/main/253/GOODS-04673017.jpg",
        ),
        "fuji_miyako": (
            "trusted_chibi_amiami_goods_04673018_fuji_miyako",
            "https://img.amiami.jp/images/product/main/253/GOODS-04673018.jpg",
        ),
        "sengoku_yuno": (
            "trusted_chibi_amiami_goods_04673019_sengoku_yuno",
            "https://img.amiami.jp/images/product/main/253/GOODS-04673019.jpg",
        ),
    }

    for character_id, (source, url) in expected.items():
        entry = next(item for item in pack.ROSTER if item.character_id == character_id)

        assert pack.trusted_chibi_urls(entry) == [("expression", source, url)]
        assert pack.source_form(source) == "chibi"

    all_trusted_urls = [
        url
        for values in pack.TRUSTED_CHIBI_URLS.values()
        for _source, url in values
    ]
    assert not any("goodsmile_nui_ymmt" in url for url in all_trusted_urls)


def test_bestdori_trim_urls_skip_known_non_trim_resource_families() -> None:
    cards = {
        "100": {"characterId": 1, "resourceSetName": "res900001"},
        "101": {"characterId": 1, "resourceSetName": "bili_res001001"},
        "102": {"characterId": 1, "resourceSetName": "res001900"},
        "103": {"characterId": 1, "resourceSetName": "res001s01"},
        "104": {"characterId": 1, "resourceSetName": "res001099"},
    }

    assert pack.bestdori_trim_urls(cards, 1, 12) == [
        (
            "normal",
            "trim_res001099",
            "https://bestdori.com/assets/jp/characters/resourceset/"
            "res001099_rip/trim_normal.png",
        )
    ]


def test_download_image_retries_and_reuses_cache(tmp_path) -> None:
    url = "https://example.invalid/character.webp"
    first_session = _RetrySession()

    first = pack.download_image(first_session, url, cache_dir=tmp_path)

    assert first is not None
    assert first[1] == "image/webp"
    assert first_session.calls == 2

    second = pack.download_image(_NoNetworkSession(), url, cache_dir=tmp_path)

    assert second == first
