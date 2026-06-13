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
import hashlib
import importlib.util
import io
import json
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import quote

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA}
SAMPLE_MAX = 256
VALID_RELATIONS = {"self", "friend", "known"}
SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")
REQUESTED_FORM_BUCKETS = ("full_body", "normal_proportion", "chibi", "expression")


@dataclass(frozen=True)
class RefererImageUrl:
    url: str
    referer: str


ImageUrl = str | tuple[str, ...] | RefererImageUrl
ImageCandidate = str | RefererImageUrl
ZipImageUrl = tuple[str, str, str]
AnimatedFrameImageUrl = tuple[str, str, int]
CroppedImageUrl = tuple[str, ImageUrl, tuple[int, int, int, int]]
IMAGE_DOWNLOAD_TIMEOUT = 18
IMAGE_DOWNLOAD_RETRIES = 3
IMAGE_DOWNLOAD_RETRY_BACKOFF = 0.8
IMAGE_DOWNLOAD_INTERVAL = 0.35
ANIMATED_IMAGE_DOWNLOAD_TIMEOUT = 90
MOEGIRL_GALLERY_LIMIT = 8
MOEGIRL_GALLERY_MAX_PAGES = 5
MOEGIRL_GALLERY_BAD_RE = re.compile(
    r"(ambox|icon|logo|svg|youtube|wikipedia|disambig|nuvola|currentevent|flag|header|"
    r"大萌字|confusion|information|headphone|x logo|mp3|apps important|sound|配置要求|info)",
    re.IGNORECASE,
)
MOEGIRL_GALLERY_POSITIVE_RE = re.compile(
    r"(公式|声库|聲庫|人设|人設|形象|立绘|立繪|设计|設計|封面|三视图|三視圖|渲染|"
    r"vocaloid|synthesizer|ace|v3|v4|v5|ai|full)",
    re.IGNORECASE,
)
MOEGIRL_IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|webp)$", re.IGNORECASE)

MOEGIRL_API = "https://zh.moegirl.org.cn/api.php"
UTAITEDB_ARTISTS_API = "https://utaitedb.net/api/artists"


class HttpGetSession(Protocol):
    def get(self, *args: Any, **kwargs: Any) -> Any: ...

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
    "kamui_gakupo": ["Gackpoid"],
    "lily": ["Lily(VOCALOID)"],
    "otomachi_una": ["音街鳗"],
    "ia": ["IA"],
    "yuzuki_yukari": ["结月缘"],
    "kizuna_akari": ["绁星灯"],
    "kaai_yuki": ["歌爱雪"],
    "nekomura_iroha": ["猫村伊吕波"],
    "tohoku_zunko": ["东北俊子"],
    "tohoku_kiritan": ["东北切蒲英"],
    "koharu_rikka": ["小春六花"],
    "natsuki_karin": ["夏色花梨"],
    "hanakuma_chifuyu": ["花隈千冬"],
    "kafu": ["可不"],
    "sekai": ["星界"],
    "rime": ["里命"],
    "coko": ["狐子"],
    "haru": ["羽累"],
    "mayu": ["MAYU"],
    "v_flower": ["v flower"],
    "kyomachi_seika": ["京町精华"],
    "tsuina_chan": ["追傩酱"],
    "vocaloid_hatsune_miku": ["初音未来"],
    "vocaloid_megurine_luka": ["巡音流歌"],
    "vocaloid_meiko": ["MEIKO"],
    "vocaloid_kaito": ["KAITO"],
}

SYNTHV_WIKI_IMAGE_URLS: dict[str, list[tuple[str, str]]] = {
    "hai_yi": [
        ("synthvwiki_full_haiyi_transparent", "https://static.wikia.nocookie.net/synthv/images/e/e3/Haiyi_transparent.png/revision/latest?cb=20240427153544"),
        ("synthvwiki_full_haiyi_studio", "https://static.wikia.nocookie.net/synthv/images/d/da/Haiyi_2_full.jpg/revision/latest?cb=20200626213051"),
        ("synthvwiki_full_haiyi_ai", "https://static.wikia.nocookie.net/synthv/images/3/39/Haiyi_AI_transparent.png/revision/latest?cb=20241224140509"),
        (
            "synthvwiki_profile_haiyi_echoes",
            "https://static.wikia.nocookie.net/synthv/images/3/3a/Haiyi_-_Echoes_of_the_Sea_by_ATDan.png/revision/latest?cb=20241220083349",
        ),
    ],
    "cang_qiong": [
        ("synthvwiki_full_cangqiong_full", "https://static.wikia.nocookie.net/synthv/images/d/dd/Cangqiong_full.png/revision/latest?cb=20210818053612"),
        (
            "synthvwiki_full_cangqiong_transparent",
            "https://static.wikia.nocookie.net/synthv/images/1/10/Cangqiong_transparent.png/revision/latest?cb=20240427153103",
        ),
        ("synthvwiki_full_cangqiong_studio", "https://static.wikia.nocookie.net/synthv/images/2/2d/Cangqiong_2_full.jpg/revision/latest?cb=20200626212729"),
        (
            "synthvwiki_profile_cangqiong_mydreamtonics",
            "https://static.wikia.nocookie.net/synthv/images/0/0d/Cangqiong_My_Dreamtonics_Icon.png/revision/latest?cb=20250519065648",
        ),
    ],
    "chi_yu": [
        ("synthvwiki_full_chiyu_main", "https://static.wikia.nocookie.net/synthv/images/8/8a/%E8%B5%A4%E7%BE%BD.png/revision/latest?cb=20190424202346"),
        ("synthvwiki_full_chiyu_transparent", "https://static.wikia.nocookie.net/synthv/images/6/66/Chiyu_transparent.png/revision/latest?cb=20240427152402"),
        ("synthvwiki_full_chiyu_studio", "https://static.wikia.nocookie.net/synthv/images/8/85/Chiyu_2_full.jpg/revision/latest?cb=20200626212115"),
        ("synthvwiki_sheet_chiyu_medium2050_concept", "https://static.wikia.nocookie.net/synthv/images/d/d4/Medium_2050_chiyu.jpg/revision/latest?cb=20201126150549"),
    ],
    "shi_an": [
        ("synthvwiki_full_shian_official", "https://static.wikia.nocookie.net/synthv/images/f/f5/Shian_official.jpg/revision/latest?cb=20210818055515"),
        ("synthvwiki_full_shian_transparent", "https://static.wikia.nocookie.net/synthv/images/0/0e/Shian_transparent.png/revision/latest?cb=20240427152628"),
        ("synthvwiki_full_shian_studio", "https://static.wikia.nocookie.net/synthv/images/5/55/Shian_2_full.jpg/revision/latest?cb=20210818055806"),
    ],
    "mu_xin": [
        ("synthvwiki_profile_muxin_icon", "https://static.wikia.nocookie.net/synthv/images/3/33/Muxin_icon.jpg/revision/latest?cb=20200807034759"),
        (
            "synthvwiki_profile_muxin_mydreamtonics",
            "https://static.wikia.nocookie.net/synthv/images/e/e3/Muxin_My_Dreamtonics_Icon.png/revision/latest?cb=20250519065711",
        ),
        ("synthvwiki_full_muxin_ai_download", "https://static.wikia.nocookie.net/synthv/images/2/2d/MuxinAI_DL.png/revision/latest?cb=20251128093948"),
        (
            "synthvwiki_profile_muxin_ai2_mydreamtonics",
            "https://static.wikia.nocookie.net/synthv/images/0/06/Muxin_AI_2_My_Dreamtonics_Icon.png/revision/latest?cb=20251202041611",
        ),
        ("synthvwiki_full_muxin_character", "https://static.wikia.nocookie.net/synthv/images/c/c9/Muxin.png/revision/latest?cb=20260110153357"),
        ("synthvwiki_sheet_muxin_game_concept", "https://static.wikia.nocookie.net/synthv/images/b/b6/Muxin_game_concept.jpg/revision/latest?cb=20200715124135"),
    ],
}

# Full concept sheets can contain multiple characters.  Only use these entries
# through deterministic crop boxes that isolate the target chibi design.
CROPPED_IMAGE_URLS: dict[str, list[CroppedImageUrl]] = {
    "cang_qiong": [
        (
            "synthvwiki_chibi_crop_cangqiong_concept_front",
            "https://static.wikia.nocookie.net/synthv/images/7/72/Cangqiong_concept.png/revision/latest?cb=20190825202255",
            (824, 0, 1948, 1151),
        ),
    ],
    "shi_an": [
        (
            "synthvwiki_chibi_crop_shian_concept_front",
            "https://static.wikia.nocookie.net/synthv/images/5/5f/Shian_concept.png/revision/latest?cb=20190825202705",
            (802, 0, 1896, 1184),
        ),
    ],
    "nekomura_iroha": [
        (
            "official_chibi_crop_ahs_iroha_vocaloid4_press_red_sd",
            "https://www.ah-soft.com/images/press/vocaloid/vocaloid4_iroha_illust.jpg",
            (1180, 120, 1880, 760),
        ),
        (
            "official_expression_crop_ahs_iroha_v2_official_face",
            RefererImageUrl(
                url="https://www.ah-soft.com/images/products/vocaloid/iroha/iroha_official.jpg",
                referer="https://www.ah-soft.com/vocaloid/iroha_v2/",
            ),
            (0, 0, 120, 104),
        ),
    ],
    "mayu": [
        (
            "press_release_chibi_crop_atpress_exit_tunes_mayu_strap_sitting_20121205",
            "https://www.atpress.ne.jp/releases/32052/3_3.jpg",
            (762, 359, 872, 524),
        ),
    ],
}

# Exact file allowlist for pages where the generic gallery filter is too
# conservative but the page file names are unambiguous single-character assets.
MOEGIRL_EXTRA_FILES: dict[str, list[str]] = {
    "yongye_minus": [
        "File:Minus人設.png",
        "File:Minus公式服.png",
        "File:永夜Minus.jpg",
        "File:永夜Minus Synthesizer V AI声库封面.jpg",
        "File:永夜Minus Synthesizer V AI声库声线與配置要求.jpg",
    ],
    "dongfang_zhizi": [
        "File:Dongfangzhizi.jpg",
        "File:Dong fang zhi zi yuan she 1.jpg",
        "File:东方栀子Blossom立绘.png",
        "File:东方栀子Nectar.png",
        "File:东方栀子Shine.png",
        "File:东方栀子Era.png",
    ],
}

EXTRA_IMAGE_URLS: dict[str, list[tuple[str, ImageUrl]]] = {
    "cang_qiong": [
        (
            "vcpedia_sheet_cangqiong_character_art",
            RefererImageUrl(
                url="https://media.vcpedia.cn/thumb/b/bd/%E8%8B%8D%E7%A9%B9.png/800px-%E8%8B%8D%E7%A9%B9.png",
                referer="https://vcpedia.cn/%E8%8B%8D%E7%A9%B9",
            ),
        ),
        (
            "official_expression_bilibili_emote_pkg7996_109372",
            "https://i0.hdslb.com/bfs/garb/95c79b5b91f8f2adf8d4f494a24a213604190d24.png",
        ),
    ],
    "chi_yu": [
        (
            "official_chibi_bilibili_emote_pkg7995_109347",
            "https://i0.hdslb.com/bfs/garb/a782499ff153db60ccde7c0a98e1ce4fb6c7de3f.png",
        ),
        (
            "official_expression_bilibili_emote_pkg7995_109362",
            "https://i0.hdslb.com/bfs/garb/5f9a7d559008f1b25a713cb0f4d9da199644f13a.png",
        ),
    ],
    "shi_an": [
        (
            "vcpedia_sheet_shian_character_art",
            RefererImageUrl(
                url="https://media.vcpedia.cn/thumb/4/4e/%E8%AF%97%E5%B2%B8%E7%AB%8B%E7%BB%98.png/800px-%E8%AF%97%E5%B2%B8%E7%AB%8B%E7%BB%98.png",
                referer="https://vcpedia.cn/%E8%AF%97%E5%B2%B8",
            ),
        ),
        (
            "official_expression_bilibili_emote_pkg7996_109374",
            "https://i0.hdslb.com/bfs/garb/6e86d39d56a0c9ff1f07632dfb15cb0499c1c474.png",
        ),
    ],
    "mu_xin": [
        (
            "official_chibi_bilibili_emote_pkg3863_53979",
            "https://i0.hdslb.com/bfs/garb/b83b81cea5d2df9031b5c8e2623ab6ce751a0703.png",
        ),
        (
            "official_expression_bilibili_emote_pkg7996_109384",
            "https://i0.hdslb.com/bfs/garb/fda941c100d8cdaf7287e472224c535c2ffd944d.png",
        ),
    ],
    "yongye_minus": [
        (
            "official_chibi_bilibili_emote_pkg3863_53963",
            "https://i0.hdslb.com/bfs/garb/e8190a3d018aa466ecb34d33bccc0bf8f9f5e999.png",
        ),
        (
            "official_expression_bilibili_emote_pkg7996_109370",
            "https://i0.hdslb.com/bfs/garb/98f8ddb062c1b86e232e36a6960457990a1e8dda.png",
        ),
    ],
    "xingchen": [
        (
            "official_chibi_bilibili_emote_pkg264_4391",
            "https://i0.hdslb.com/bfs/emote/fd8aa275d5d91cdf71410bc1a738415fd6e2ab86.png",
        ),
        (
            "official_expression_bilibili_emote_pkg264_4401",
            "https://i0.hdslb.com/bfs/emote/1337af7b041c3c061d3d725d27a6655795d7d9ee.png",
        ),
    ],
    "hai_yi": [
        (
            "official_chibi_bilibili_emote_pkg441_7648",
            "https://i0.hdslb.com/bfs/emote/c548b527593a3d21e5082c26abbf61c63701f11a.png",
        ),
        (
            "official_expression_bilibili_emote_pkg441_7659",
            "https://i0.hdslb.com/bfs/emote/20db290d7183e19cbf74563ef2c3434ca387ce95.png",
        ),
    ],
    "luo_tianyi": [
        (
            "official_chibi_vsinger_api_luo_tianyi_q_model_cover",
            "https://res.vsinger.com/images/cffa18b20ce29010364f33ae94c6a8b0.jpg",
        ),
        (
            "archived_expression_luminous_vsinger_luo_tianyi_13th_bayinqixiang_meili",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E6%B4%9B%E5%A4%A9%E4%BE%9D13%E5%91%A8%E5%B9%B4%E5%85%AB%E9%9F%B3%E5%A5%87%E5%93%8D%E8%A1%A8%E6%83%85%E5%8C%85/%E7%BE%8E%E4%B8%BD.png",
        ),
    ],
    "yan_he": [
        (
            "archived_expression_luminous_vsinger_yan_he_12th_ku_ku",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E8%A8%80%E5%92%8C12%E5%91%A8%E5%B9%B4%E7%BA%AA%E5%BF%B5%E8%A1%A8%E6%83%85%E5%8C%85/%E5%93%AD%E5%93%AD.png",
        ),
        (
            "archived_chibi_luminous_vsinger_yan_he_12th_xingfen",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E8%A8%80%E5%92%8C12%E5%91%A8%E5%B9%B4%E7%BA%AA%E5%BF%B5%E8%A1%A8%E6%83%85%E5%8C%85/%E5%85%B4%E5%A5%8B.png",
        ),
    ],
    "yuezheng_ling": [
        (
            "archived_expression_luminous_vsinger_yuezheng_ling_10th_xianglingguang_zan",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E4%B9%90%E6%AD%A3%E7%BB%AB%E5%8D%81%E5%91%A8%E5%B9%B4%E5%90%91%E7%BB%AB%E5%85%89%E8%A1%A8%E6%83%85%E5%8C%85/%E8%B5%9E.png",
        ),
        (
            "archived_chibi_luminous_vsinger_yuezheng_ling_10th_xianglingguang_laile",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/%E4%B9%90%E6%AD%A3%E7%BB%AB%E5%8D%81%E5%91%A8%E5%B9%B4%E5%90%91%E7%BB%AB%E5%85%89%E8%A1%A8%E6%83%85%E5%8C%85/%E6%9D%A5%E4%BA%86.png",
        ),
    ],
    "yuezheng_longya": [
        (
            "archived_expression_luminous_vsinger_yuezheng_longya_6th_lb01",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E4%B9%90%E6%AD%A3%E9%BE%99%E7%89%99%E5%85%AD%E5%91%A8%E5%B9%B4/LB01.png",
        ),
        (
            "archived_chibi_luminous_vsinger_yuezheng_longya_6th_lb13",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E4%B9%90%E6%AD%A3%E9%BE%99%E7%89%99%E5%85%AD%E5%91%A8%E5%B9%B4/LB13.png",
        ),
    ],
    "zhiyu_moke": [
        (
            "official_full_vsinger_api_zhiyu_moke_cover_0",
            "https://res.vsinger.com/images/ec7b130d2e36888de23c91191223c64d.png",
        ),
        (
            "archived_expression_luminous_vsinger_zhiyu_moke_6th_lq18",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E5%BE%B5%E7%BE%BD%E6%91%A9%E6%9F%AF%E5%85%AD%E5%91%A8%E5%B9%B4/LQ18.png",
        ),
        (
            "archived_chibi_luminous_vsinger_zhiyu_moke_6th_lq15",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E5%BE%B5%E7%BE%BD%E6%91%A9%E6%9F%AF%E5%85%AD%E5%91%A8%E5%B9%B4/LQ15.png",
        ),
    ],
    "mo_qingxian": [
        (
            "archived_expression_luminous_vsinger_mo_qingxian_7th_ku_ku",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E5%A2%A8%E6%B8%85%E5%BC%A6%E4%B8%83%E5%91%A8%E5%B9%B4%E8%A1%A8%E6%83%85%E5%8C%85/%E5%93%AD%E5%93%AD.png",
        ),
        (
            "archived_chibi_luminous_vsinger_mo_qingxian_7th_guaiqiao",
            "https://img.lty.fun/images/VSINGER%E8%A1%A8%E6%83%85/bilibili%E5%A2%A8%E6%B8%85%E5%BC%A6%E4%B8%83%E5%91%A8%E5%B9%B4%E8%A1%A8%E6%83%85%E5%8C%85/%E4%B9%96%E5%B7%A7.png",
        ),
    ],
    "xia_yuyao": [
        (
            "moegirl_sheet_xia_yuyao_cd02_v02",
            "https://img.moegirl.org.cn/common/thumb/f/fc/Cd02_v02.jpg/934px-Cd02_v02.jpg",
        ),
        (
            "official_chibi_line_voicemith_ecapsule_xia_yuyao3_5077007_98039968",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/98039968/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_voicemith_ecapsule_xia_yuyao3_5077007_98039985",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/98039985/android/sticker.png?v=1",
        ),
    ],
    "xin_hua": [
        (
            "line_creator_chibi_facio_yumei_xin_hua_1245282_9950512",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/9950512/android/sticker.png?v=1",
        ),
        (
            "line_creator_expression_facio_yumei_xin_hua_1245282_9950513",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/9950513/android/sticker.png?v=1",
        ),
    ],
    "dongfang_zhizi": [
        (
            "moegirl_sheet_dongfang_zhizi_blossom_standing",
            "https://img.moegirl.org.cn/common/thumb/f/ff/东方栀子Blossom立绘.png/557px-东方栀子Blossom立绘.png",
        ),
        (
            "official_chibi_dongfangzhizi_voicebank_site_avatar_20251225",
            "https://dongfangzhizi.top/wp-content/uploads/2025/12/Image_16002726312696.png",
        ),
        (
            "official_expression_bilibili_space_62351857_era_shine_face",
            "https://i1.hdslb.com/bfs/face/d64b28af735d4cd0c872cdb12cc34edc97186561.jpg",
        ),
    ],
    "ia": [
        (
            "official_full_ia_aria_mob_main_202010",
            "https://ia-aria.com/wp-content/uploads/2020/10/mob_main_202010_-1.jpg",
        ),
        (
            "official_profile_ia_05_jacket",
            "https://ia-aria.com/wp-content/uploads/2024/01/IA05_jk-scaled.jpg",
        ),
        (
            "official_chibi_line_1stplace_ia_vol1_1381883_14927590",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/14927590/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_1stplace_ia_vol1_1381883_14927591",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/14927591/android/sticker.png?v=1",
        ),
    ],
    "gumi": [
        ("official_full_gumi_illust_01", "https://www.ssw.co.jp/products/vocal/megpoid/i/index_image/01_b.jpg"),
        ("official_full_gumi_illust_02", "https://www.ssw.co.jp/products/vocal/megpoid/i/index_image/02_b.jpg"),
        ("official_full_gumi_illust_03", "https://www.ssw.co.jp/products/vocal/megpoid/i/index_image/03_b.jpg"),
        (
            "official_sheet_gumi_aivoice2_standing",
            "https://www.ssw.co.jp/products/talk/aivoice2_gumi/images/aiv2gm_nbg.png",
        ),
        (
            "official_chibi_line_internet_gumi_1485874_17915992",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/17915992/iPhone/sticker@2x.png?v=1",
        ),
        (
            "official_expression_line_internet_gumi_1485874_17915994",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/17915994/iPhone/sticker@2x.png?v=1",
        ),
    ],
    "kamui_gakupo": [
        ("official_full_gackpoid_v4_main", "https://www.ssw.co.jp/products/vocaloid4/gackpoid/images/main.jpg"),
        ("official_profile_gackpoid_v4_top", "https://www.ssw.co.jp/products/vocaloid4/gackpoid/images/top.jpg"),
        ("official_full_gackpoid_v4_top_img", "https://www.ssw.co.jp/products/vocaloid4/gackpoid/images/top_img.png"),
        (
            "official_profile_gackpoid_v3_main11",
            "https://www.ssw.co.jp/products/vocal3/gackpoid/index_image/main11.jpg",
        ),
        (
            "official_profile_gackpoid_v3_main22",
            "https://www.ssw.co.jp/products/vocal3/gackpoid/index_image/main22.jpg",
        ),
        (
            "official_sheet_gackpoid_v3_native",
            "https://www.ssw.co.jp/products/vocal3/gackpoid/i/index_image/n_b.jpg",
        ),
        (
            "official_chibi_line_internet_gackpoid_12823915_339791279",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/339791279/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_internet_gackpoid_12823915_339791316",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/339791316/android/sticker.png?v=1",
        ),
    ],
    "lily": [
        ("official_full_vocaloid_lily", "https://rsc-net.vocaloid.com/assets/image_files/d1e4264af19b7301ebe827a3b47b294b/Lily_600.png"),
        ("official_full_lily_v3_main", "https://www.ssw.co.jp/products/vocal3/lily/images/main.png"),
        ("official_sheet_lily_v3_front", "https://www.ssw.co.jp/products/vocal3/lily/images/v3lily_front_b.jpg"),
        (
            "official_chibi_animove_lily_goods_sd_20130401",
            "https://animove.jp/lily/goods/2013/04/01/130401_sd.jpg",
        ),
    ],
    "kaai_yuki": [
        (
            "official_full_vocaloid_yuki",
            "https://rsc-net.vocaloid.com/assets/image_files/1587d130398c80574c97b5a2db353756/yukiNatural_600.png",
        ),
        ("official_full_yuki_v4_main", "https://www.ah-soft.com/images/products/vocaloid/v4yuki/mainimage.jpg"),
        ("official_full_yuki_v4_img", "https://www.ah-soft.com/images/products/vocaloid/v4yuki/img.jpg"),
        ("official_full_yuki_v4_sub", "https://www.ah-soft.com/images/products/vocaloid/v4yuki/subimage.gif"),
        (
            "official_sheet_ahs_press_yuki_vocaloid4_illust",
            "https://www.ah-soft.com/images/press/vocaloid/vocaloid4_yuki_illust.jpg",
        ),
        (
            "official_expression_line_ahs_yuki_1125044_5105717",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/5105717/android/sticker.png?v=1",
        ),
    ],
    "nekomura_iroha": [
        (
            "official_full_vocaloid_iroha",
            "https://rsc-net.vocaloid.com/assets/image_files/85b0f3459f37615c22a5130f09bb6a83/IrohaNatural_600v2.png",
        ),
        (
            "official_full_vocaloid_iroha_soft",
            "https://rsc-net.vocaloid.com/assets/image_files/7b13c1785b7a0829ca1c7865d246f4af/IrohaSoft_600v2.png",
        ),
        (
            "official_sheet_ahs_press_iroha_synthv2_illust",
            "https://www.ah-soft.com/images/press/synth-v/synth-v2_iroha_illust.jpg",
        ),
    ],
    "kasane_teto": [
        ("official_full_teto_new", "https://kasaneteto.jp/assets/download/illust-logo/official_illust_teto_new.png"),
        ("official_full_teto_sv", "https://kasaneteto.jp/assets/download/illust-logo/official_illust_teto_sv.png"),
        ("official_full_teto_sv2", "https://kasaneteto.jp/assets/download/illust-logo/official_illust_teto_sv2.png"),
        ("official_full_teto_2008", "https://kasaneteto.jp/assets/download/illust-logo/official_illust_teto.png"),
        ("official_sheet_teto_sv_3views", "https://kasaneteto.jp/assets/download/illust-logo/teto_sv_3views.jpg"),
        (
            "official_chibi_line_twindrill_teto_stamp1_9246166_237904694",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/237904694/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_twindrill_teto_stamp1_9246166_237904695",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/237904695/android/sticker.png?v=1",
        ),
    ],
    "tsurumaki_maki": [
        ("official_sheet_maki_settei1", "https://www.ah-soft.com/images/products/maki/settei-1.png"),
        ("official_sheet_maki_settei2", "https://www.ah-soft.com/images/products/maki/settei-2.png"),
        ("official_sheet_maki_settei3", "https://www.ah-soft.com/images/products/maki/settei-3.png"),
        ("official_full_maki_voice", "https://www.ah-soft.com/images/products/voice/maki/image.png"),
        (
            "official_expression_line_ahs_maki_14308849_375929074",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/375929074/android/sticker.png?v=1",
        ),
    ],
    "kotonoha_akane": [
        ("official_full_kotonoha_akane_aivoice", "https://aivoice.jp/wp-content/uploads/2022/02/akane_pic1.png"),
        ("official_expression_kotonoha_akane_happy", "https://aivoice.jp/wp-content/uploads/2022/02/akane_pic2.png"),
        ("official_expression_kotonoha_akane_angry", "https://aivoice.jp/wp-content/uploads/2022/02/akane_pic3.png"),
        ("official_expression_kotonoha_akane_sad", "https://aivoice.jp/wp-content/uploads/2022/02/akane_pic4.png"),
    ],
    "kotonoha_aoi": [
        ("official_full_kotonoha_aoi_aivoice", "https://aivoice.jp/wp-content/uploads/2022/02/aoi_pic1.png"),
        ("official_expression_kotonoha_aoi_happy", "https://aivoice.jp/wp-content/uploads/2022/02/aoi_pic2.png"),
        ("official_expression_kotonoha_aoi_angry", "https://aivoice.jp/wp-content/uploads/2022/02/aoi_pic3.png"),
        ("official_expression_kotonoha_aoi_sad", "https://aivoice.jp/wp-content/uploads/2022/02/aoi_pic4.png"),
    ],
    "otomachi_una": [
        (
            "official_full_una_v6_top",
            "https://www.ssw.co.jp/products/vocaloid6/otomachiuna/images/top_img.png",
        ),
        (
            "official_sheet_una_aivoice2_standing",
            "https://otomachiuna.jp/wp-content/uploads/2025/01/AIVOICE2ウナ_立ち絵.png",
        ),
        (
            "official_chibi_line_mtk_una_1396541_15392046",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/15392046/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_mtk_una_1396541_15392026",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/15392026/android/sticker.png?v=1",
        ),
    ],
    "yuzuki_yukari": [
        ("official_full_yukari_v4", "https://www.ah-soft.com/images/products/vocaloid/v4yukari/mainimage.jpg"),
        ("official_full_yukari_v3", "https://www.ah-soft.com/images/products/vocaloid/yukari/mainimage.jpg"),
        ("official_full_yukari_voice2", "https://www.ah-soft.com/images/products/voiceroid/yukari_2/mainimage.jpg"),
        (
            "official_sheet_vocalomakets_yukari_aivoice2_config",
            "https://vocalomakets.com/wp-content/uploads/2025/09/yukari.jpg",
        ),
        (
            "official_chibi_line_vocalomakets_yukari_1018051_796519",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/796519/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_vocalomakets_yukari_1018051_796527",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/796527/android/sticker.png?v=1",
        ),
    ],
    "kizuna_akari": [
        ("official_full_akari_v4_main", "https://www.ah-soft.com/images/products/vocaloid/v4akari/mainimage.jpg"),
        ("official_full_akari_v4_img", "https://www.ah-soft.com/images/products/vocaloid/v4akari/img.jpg"),
        ("official_full_akari_v4_sub", "https://www.ah-soft.com/images/products/vocaloid/v4akari/subimage.gif"),
        (
            "official_sheet_vocalomakets_akari_aivoice2_config",
            "https://vocalomakets.com/wp-content/uploads/2025/09/akari.jpg",
        ),
        (
            "official_chibi_line_vocalomakets_akari_6622254_153063560",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/153063560/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_vocalomakets_akari_6622254_153063566",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/153063566/android/sticker.png?v=1",
        ),
    ],
    "tohoku_zunko": [
        ("official_full_zunko_v4_main", "https://www.ah-soft.com/images/products/vocaloid/v4zunko/mainimage.jpg"),
        ("official_full_zunko_v4_img", "https://www.ah-soft.com/images/products/vocaloid/v4zunko/img.jpg"),
        ("official_full_zunko_v4_sub", "https://www.ah-soft.com/images/products/vocaloid/v4zunko/subimage.gif"),
        (
            "official_sheet_ahs_press_zunko_voicepeak_illust",
            "https://www.ah-soft.com/images/press/voice/voicepeak_zunko_illust.png",
        ),
        (
            "official_chibi_line_sss_zunko_1000978_95596",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/95596/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_sss_zunko_1000978_95597",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/95597/android/sticker.png?v=1",
        ),
    ],
    "tohoku_kiritan": [
        ("official_full_kiritan_header", "https://www.ah-soft.com/images/products/kiritan/header.png"),
        ("official_profile_kiritan_voicepeak", "https://www.ah-soft.com/images/products/voice/kiritan/voicepeak_kiritan_box.jpg"),
        ("official_profile_kiritan_cevio", "https://www.ah-soft.com/images/products/cevio/kiritan/cevio_kiritan_pkg.jpg"),
        (
            "official_sheet_ahs_press_kiritan_voicepeak_illust",
            "https://www.ah-soft.com/images/press/voice/voicepeak_kiritan_illust.png",
        ),
        (
            "official_chibi_line_sss_kiritan_1120252_4912144",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/4912144/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_sss_kiritan_1120252_4912145",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/4912145/android/sticker.png?v=1",
        ),
    ],
    "koharu_rikka": [
        ("official_full_rikka_visual", "https://tokyo6.tokyo/wp-content/uploads/2020/05/f1d28bbe8e83676c56b9b638be9afa84-769x1024.jpg"),
        ("official_sheet_rikka_design", "https://tokyo6.tokyo/wp-content/uploads/2022/03/78e6b400f77599caf149535cf58ee68c-1024x550.png"),
        (
            "official_chibi_line_tokyo6_characters_25516967_648064406",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/648064406/android/sticker.png?v=1",
        ),
    ],
    "natsuki_karin": [
        ("official_full_karin_main", "https://tokyo6.tokyo/wp-content/uploads/2021/05/a5aceb4c200a4d90108f8088066a5c77.png"),
        ("official_full_karin_visual", "https://tokyo6.tokyo/wp-content/uploads/2022/01/karin_image_illust-723x1024.jpg"),
        ("official_sheet_karin_design", "https://tokyo6.tokyo/wp-content/uploads/2022/01/karin_design-2-1024x550.png"),
        (
            "official_chibi_line_tokyo6_characters_25516967_648064412",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/648064412/android/sticker.png?v=1",
        ),
    ],
    "hanakuma_chifuyu": [
        ("official_full_chifuyu_main", "https://tokyo6.tokyo/wp-content/uploads/2021/05/6f745670a3b72d904aa84d929fe7d9c2.png"),
        ("official_full_chifuyu_visual", "https://tokyo6.tokyo/wp-content/uploads/2021/05/tihuyu-694x1024.jpg"),
        ("official_sheet_chifuyu_design", "https://tokyo6.tokyo/wp-content/uploads/2025/09/chihuyu_design-1024x550.png"),
        (
            "official_chibi_line_tokyo6_characters_25516967_648064408",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/648064408/android/sticker.png?v=1",
        ),
    ],
    "kyomachi_seika": [
        ("official_full_seika_header", "https://www.ah-soft.com/images/products/seika/header.png"),
        (
            "official_sheet_seika_town_character_hp1",
            "https://kyomachi-seika.jp/wp-content/uploads/2014/07/HP1-187x300.png",
        ),
        ("official_profile_seika_sv2", "https://www.ah-soft.com/images/products/synth-v/seika2/sv2_seika_pkg.jpg"),
        ("official_profile_seika_synthv", "https://www.ah-soft.com/images/products/synth-v/seika/synthesizerv_seika_box.jpg"),
        ("official_profile_seika_voicepeak", "https://www.ah-soft.com/images/products/voice/seika/voicepeak_seika_box.jpg"),
        (
            "official_chibi_line_seika_town_70th_31420910_787267833",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/787267833/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_seika_town_70th_31420910_787267834",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/787267834/android/sticker.png?v=1",
        ),
    ],
    "tsuina_chan": [
        ("official_full_tsuina_sv2_main", "https://www.ah-soft.com/images/products/synth-v/tsuina2/mainimage.jpg"),
        ("official_full_tsuina_sv2_img", "https://www.ah-soft.com/images/products/synth-v/tsuina2/img.jpg"),
        ("official_full_tsuina_v6_top", "https://www.ssw.co.jp/products/vocaloid6/tsuina/images/top_img.png"),
        ("official_full_tsuina_voiceroid", "https://www.ah-soft.com/images/products/voiceroid/tsuina/mainimage.png"),
        ("official_profile_tsuina_sv2_pkg", "https://www.ah-soft.com/images/products/synth-v/tsuina2/sv2_tsuina_pkg.jpg"),
        (
            "official_sheet_ahs_press_tsuina_synthv2_illust",
            "https://www.ah-soft.com/images/press/synth-v/synth-v2_tsuina_illust.png",
        ),
        (
            "official_chibi_line_tsuina_project_vol1_15284033_398851390",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/398851390/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_tsuina_project_vol1_15284033_398851391",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/398851391/android/sticker.png?v=1",
        ),
    ],
    "kafu": [
        ("official_full_kafu_kv", "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/06/kv_SP_kahu.png"),
        (
            "official_sheet_kamitsubaki_kafu_standing_202309",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2023/09/c153a574a67714bccd2d44c863830597-1920x2711.jpg",
        ),
        (
            "official_profile_kafu_about",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/06/whoiskafu_sp.jpeg",
        ),
        (
            "official_profile_kafu_package",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/06/package_box.png",
        ),
        (
            "official_chibi_findme_kafu_1st_anniv_plush_mascot",
            "https://cdn.shopify.com/s/files/1/0551/7692/1261/products/kafu_1stanv_item.jpg?v=1658397524",
        ),
    ],
    "sekai": [
        (
            "official_full_sekai_kv",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/06/kv_sp_sekai.png",
        ),
        (
            "official_profile_sekai_about",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/06/021c2f70b97fb0f23b31833be4ed92c7.jpg",
        ),
        (
            "official_profile_sekai_package",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/06/pack752_1014_sekai.jpg",
        ),
        (
            "official_sheet_piapro_sekai_standing",
            "https://piapro.jp/pages/character_images/character/ch_img_sekai.jpg",
        ),
        (
            "official_chibi_findme_sekai_1st_anniv_plush_mascot",
            "https://cdn.shopify.com/s/files/1/0551/7692/1261/products/sekai_1stanv_item.jpg?v=1658397546",
        ),
    ],
    "rime": [
        (
            "official_full_rime_visual",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/07/rime_visual_sp.jpg",
        ),
        (
            "official_profile_rime_about",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/07/rime_about_image_sp.jpg",
        ),
        (
            "official_profile_rime_profile",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/07/rime_profile_image.jpg",
        ),
        (
            "official_sheet_rime_package",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/07/rime_package_1.png",
        ),
        (
            "official_chibi_findme_rime_niconico2023_plush_front",
            "https://cdn.shopify.com/s/files/1/0551/7692/1261/files/Rime_Front.jpg?v=1683187820",
        ),
    ],
    "coko": [
        (
            "official_full_coko_kv",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/10/kv_SP_koko.png",
        ),
        (
            "official_profile_coko_about1",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/10/about_img1.jpg",
        ),
        (
            "official_profile_coko_product",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2022/10/koko_product_img1.jpg",
        ),
        (
            "official_chibi_findme_coko_niconico2023_plush_front",
            "https://cdn.shopify.com/s/files/1/0551/7692/1261/files/Koko_Front.jpg?v=1683187795",
        ),
    ],
    "haru": [
        (
            "official_full_haru_kv",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2023/11/haru_kv_sp.jpg",
        ),
        (
            "official_profile_haru_about",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2023/11/about_haru_img_sp.jpg",
        ),
        (
            "official_profile_haru_product1",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2023/11/product_haru-1.png",
        ),
        (
            "official_profile_haru_product2",
            "https://musical-isotope.kamitsubaki.jp/wp-content/uploads/2023/11/product_haru_02.png",
        ),
        (
            "official_sheet_piapro_haru_standing",
            "https://piapro.jp/pages/character_images/character/ch_img_haru.jpg",
        ),
    ],
    "mayu": [
        (
            "official_full_vocaloid_mayu",
            "https://rsc-net.vocaloid.com/assets/image_files/1059d8dcbca298af9a7bfb39c9e101dd/MAYU_600.png",
        ),
        (
            "official_profile_mayu_pkg",
            "https://rsc-net.vocaloid.com/assets/image_files/b4180bb8c1296a7123eb3918329ce733/topics_mayu_pkg.png",
        ),
        (
            "official_profile_mayu_loves",
            "https://rsc-net.vocaloid.com/assets/image_files/1c83f71c5efda994b2584fa8e72ede3d/topics_mayu_jkt_01.png",
        ),
        (
            "official_sheet_piapro_mayu_standing",
            "https://piapro.jp/pages/character_images/character/ch_img_mayu.jpg",
        ),
    ],
    "v_flower": [
        ("official_profile_vflower_v4second", "http://www.v-flower.jp/images/v4second.png"),
        ("official_full_vflower_v4_color", "http://www.v-flower.jp/images/v4_color.png"),
        ("official_sheet_vflower_special03", "http://www.v-flower.jp/images/special03.png"),
        ("official_sheet_vflower_special02", "http://www.v-flower.jp/images/special02.png"),
        (
            "official_chibi_line_gyroid_vflower_hanachang_1292567_11849125",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/11849125/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_gyroid_vflower_hanachang_1292567_11849127",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/11849127/android/sticker.png?v=1",
        ),
    ],
    "vocaloid_hatsune_miku": [
        ("official_full_piapro_miku", "https://piapro.net/images/ch_img_miku.png"),
        ("official_profile_piapro_miku_btn", "https://piapro.net/images/btn_miku.jpg"),
        ("official_profile_piapro_miku_btn2", "https://piapro.net/images/btn_miku_img_02.jpg"),
        (
            "official_expression_line_crypton_1349_miku_24346",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/24346/android/sticker.png?v=11",
        ),
    ],
    "vocaloid_kagamine_rin": [
        ("official_full_piapro_rin", "https://piapro.net/images/ch_img_rin.png"),
        ("official_profile_piapro_rin_btn", "https://piapro.net/images/btn_rin.jpg"),
        (
            "official_expression_line_crypton_1349_rin_24347",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/24347/android/sticker.png?v=11",
        ),
    ],
    "vocaloid_kagamine_len": [
        ("official_full_piapro_len", "https://piapro.net/images/ch_img_len.png"),
        ("official_profile_piapro_len_btn", "https://piapro.net/images/btn_len.jpg"),
        (
            "official_expression_line_crypton_1349_len_24348",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/24348/android/sticker.png?v=11",
        ),
    ],
    "vocaloid_megurine_luka": [
        ("official_full_piapro_luka", "https://piapro.net/images/ch_img_luka.png"),
        ("official_profile_piapro_luka_btn", "https://piapro.net/images/btn_luka.jpg"),
        (
            "official_expression_line_crypton_1349_luka_24349",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/24349/android/sticker.png?v=11",
        ),
    ],
    "vocaloid_meiko": [
        ("official_full_piapro_meiko", "https://piapro.net/images/ch_img_meikov3.png"),
        ("official_profile_piapro_meiko_btn", "https://piapro.net/images/btn_meikov3.jpg"),
        ("official_profile_piapro_meiko_btn2", "https://piapro.net/images/btn_meiko_img_02.jpg"),
        (
            "official_expression_line_crypton_1349_meiko_24355",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/24355/android/sticker.png?v=11",
        ),
    ],
    "vocaloid_kaito": [
        ("official_full_piapro_kaito", "https://piapro.net/images/ch_img_kaitov3.png?20210315"),
        ("official_profile_piapro_kaito_btn", "https://piapro.net/images/btn_kaitov3.jpg"),
        ("official_profile_piapro_kaito_btn2", "https://piapro.net/images/btn_kaito_img_02.jpg"),
        (
            "official_expression_line_crypton_1349_kaito_24352",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/24352/android/sticker.png?v=11",
        ),
    ],
    "one": [
        ("official_full_one_top", "https://one-aria.com/wp-content/uploads/2020/11/mob_main_202010_.jpg"),
        ("official_full_one_00", "https://one-aria.com/wp-content/uploads/2025/01/OИE00_JK-FIX-scaled.jpg"),
        ("official_full_one_10th", "https://one-aria.com/wp-content/uploads/2025/01/OИE10thキャンバスアート.jpg"),
        (
            "official_sheet_piapro_one_standing",
            "https://piapro.jp/pages/character_images/character/ch_img_one.jpg",
        ),
        ("official_profile_one_02", "https://one-aria.com/wp-content/uploads/2023/08/ONE_02_JK_960×1750-scaled.jpg"),
        ("official_profile_one_song", "https://one-aria.com/wp-content/uploads/2023/04/おねーちゃんにはナイショだよ！_960×1750-1-scaled.jpg"),
        (
            "official_chibi_line_1stplace_one_vol1_1830278_25943272",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/25943272/android/sticker.png?v=1",
        ),
        (
            "official_expression_line_1stplace_one_vol1_1830278_25943273",
            "https://stickershop.line-scdn.net/stickershop/v1/sticker/25943273/android/sticker.png?v=1",
        ),
    ],
}

ANIMATED_FRAME_IMAGE_URLS: dict[str, list[AnimatedFrameImageUrl]] = {
    "kafu": [
        (
            "official_expression_kamitsubaki_gif4_kafu_frame20",
            "https://www.mediafire.com/file/p83f04kl9e8z0rj/01_KAFU.gif/file",
            20,
        ),
    ],
    "sekai": [
        (
            "official_expression_kamitsubaki_gif2_sekai_frame28",
            "https://www.mediafire.com/file/0hs4qw13sf2myht/02_SEKAI.gif/file",
            28,
        ),
    ],
    "rime": [
        (
            "official_expression_kamitsubaki_gif4_rime_frame40",
            "https://www.mediafire.com/file/xiky8yxzpjw2mze/03_RIME.gif/file",
            40,
        ),
    ],
    "coko": [
        (
            "official_expression_kamitsubaki_gif3_coko_frame36",
            "https://www.mediafire.com/file/9u6qe0txswlcg92/04_COKO.gif/file",
            36,
        ),
    ],
}

OFFICIAL_EXPRESSION_PROFILE_SOURCES = {
    "official_profile_kafu_about",
    "official_profile_sekai_about",
    "official_profile_coko_about1",
    "official_profile_haru_about",
    "official_profile_mayu_loves",
}

MIKU_NT_OFFICIAL_ZIP = "https://sonicwire.com/images/sp/cv/mikunt/mikunt_images.zip"
RIN_LEN_V4X_OFFICIAL_ZIP = "https://sonicwire.com/images/sp/cv/rinlenv4x_images.zip"
RIN_LEN_NT_OFFICIAL_ZIP = "https://sonicwire.com/images/sp/cv/rinlennt/rinlennt_images.zip"
LUKA_V4X_OFFICIAL_ZIP = "https://sonicwire.com/images/sp/cv/lukav4x_images.zip"
MEIKO_V3_OFFICIAL_ZIP = "https://sonicwire.com/images/sp/cv/meikov3_images.zip"
KAITO_V3_OFFICIAL_ZIP = "https://sonicwire.com/images/sp/cv/kaitov3_images.zip"
AHS_SD_CHARACTER_ZIP = "https://www.ah-soft.com/images/press/ahs_sdcharactor.zip"
TOKYO6_RIKKA_SABUN_ZIP = "https://tokyo6.tokyo/koharu_rikka_sabun_files.zip"
TOKYO6_KARIN_SABUN_ZIP = "https://tokyo6.tokyo/natsuki_karin_sabun_files.zip"
TOKYO6_CHIFUYU_SABUN_ZIP = "https://tokyo6.tokyo/hanakuma_chifuyu_sabun_files.zip"
ZIP_IMAGE_URLS: dict[str, list[ZipImageUrl]] = {
    "kaai_yuki": [
        ("official_chibi_ahs_sd_kaai_yuki_zip", AHS_SD_CHARACTER_ZIP, "ahs_sdcharactor/kaaiyuki.png"),
    ],
    "kotonoha_akane": [
        ("official_sheet_aivoice2_akane_normal_zip", "https://aivoice.jp/material/aivoice2_akane1.zip", "#image:0"),
        (
            "official_chibi_aivoice2_akane_sd_zip",
            "https://aivoice.jp/material/aivoice2_akane_sd.zip",
            "#image:0",
        ),
    ],
    "kotonoha_aoi": [
        ("official_sheet_aivoice2_aoi_normal_zip", "https://aivoice.jp/material/aivoice2_aoi1.zip", "#image:0"),
        ("official_chibi_aivoice2_aoi_sd_zip", "https://aivoice.jp/material/aivoice2_aoi_sd.zip", "#image:0"),
    ],
    "vocaloid_hatsune_miku": [
        ("official_chibi_crypton_miku_nt_sd_zip", MIKU_NT_OFFICIAL_ZIP, "img_hatsune_miku_nt_sd.png"),
        ("official_sheet_crypton_miku_nt_settei_zip", MIKU_NT_OFFICIAL_ZIP, "settei_hatsune_miku_nt.jpg"),
    ],
    "vocaloid_kagamine_rin": [
        ("official_full_crypton_rin_v4x_zip", RIN_LEN_V4X_OFFICIAL_ZIP, "img_kagamine_rin_v4x.png"),
        ("official_chibi_crypton_rin_v4x_sd_zip", RIN_LEN_V4X_OFFICIAL_ZIP, "img_kagamine_rin_v4x_sd.png"),
        (
            "official_sheet_crypton_rin_nt_settei_zip",
            RIN_LEN_NT_OFFICIAL_ZIP,
            "rinlennt_images/settei_kagamine_rin_nt.jpg",
        ),
    ],
    "vocaloid_kagamine_len": [
        ("official_full_crypton_len_v4x_zip", RIN_LEN_V4X_OFFICIAL_ZIP, "img_kagamine_len_v4x.png"),
        ("official_chibi_crypton_len_v4x_sd_zip", RIN_LEN_V4X_OFFICIAL_ZIP, "img_kagamine_len_v4x_sd.png"),
        (
            "official_sheet_crypton_len_nt_settei_zip",
            RIN_LEN_NT_OFFICIAL_ZIP,
            "rinlennt_images/settei_kagamine_len_nt.jpg",
        ),
    ],
    "vocaloid_megurine_luka": [
        ("official_chibi_crypton_luka_v4x_sd_zip", LUKA_V4X_OFFICIAL_ZIP, "img_megurine_luka_v4x_sd.png"),
        ("official_sheet_crypton_luka_v4x_settei_zip", LUKA_V4X_OFFICIAL_ZIP, "settei_megurine_luka_v4x.jpg"),
    ],
    "vocaloid_meiko": [
        ("official_chibi_crypton_meiko_v3_sd_zip", MEIKO_V3_OFFICIAL_ZIP, "img_meiko_v3_sd.png"),
        ("official_sheet_crypton_meiko_v3_settei_zip", MEIKO_V3_OFFICIAL_ZIP, "settei_meiko_v3.jpg"),
    ],
    "vocaloid_kaito": [
        ("official_chibi_crypton_kaito_v3_sd_zip", KAITO_V3_OFFICIAL_ZIP, "img_kaito_v3_sd.png"),
        ("official_sheet_crypton_kaito_v3_settei_zip", KAITO_V3_OFFICIAL_ZIP, "settei_kaito_v3.jpg"),
    ],
    "koharu_rikka": [
        ("official_expression_tokyo6_rikka_angry_winter_zip", TOKYO6_RIKKA_SABUN_ZIP, "#image:6"),
        ("official_expression_tokyo6_rikka_joy_winter_zip", TOKYO6_RIKKA_SABUN_ZIP, "#image:7"),
        ("official_expression_tokyo6_rikka_sad_summer_zip", TOKYO6_RIKKA_SABUN_ZIP, "#image:11"),
        ("official_expression_tokyo6_rikka_angry_summer_zip", TOKYO6_RIKKA_SABUN_ZIP, "#image:13"),
        ("official_expression_tokyo6_rikka_blush_summer_zip", TOKYO6_RIKKA_SABUN_ZIP, "#image:15"),
    ],
    "tsurumaki_maki": [
        ("official_chibi_ahs_sd_tsurumaki_maki_zip", AHS_SD_CHARACTER_ZIP, "ahs_sdcharactor/tsurumakimaki.png"),
    ],
    "natsuki_karin": [
        ("official_expression_tokyo6_karin_angry_winter_zip", TOKYO6_KARIN_SABUN_ZIP, "#image:14"),
        ("official_expression_tokyo6_karin_cry_winter_zip", TOKYO6_KARIN_SABUN_ZIP, "#image:16"),
        ("official_expression_tokyo6_karin_blush_winter_zip", TOKYO6_KARIN_SABUN_ZIP, "#image:17"),
        ("official_expression_tokyo6_karin_smile_winter_zip", TOKYO6_KARIN_SABUN_ZIP, "#image:19"),
        ("official_expression_tokyo6_karin_calm_summer_zip", TOKYO6_KARIN_SABUN_ZIP, "#image:28"),
    ],
    "hanakuma_chifuyu": [
        ("official_expression_tokyo6_chifuyu_angry_winter_zip", TOKYO6_CHIFUYU_SABUN_ZIP, "#image:12"),
        ("official_expression_tokyo6_chifuyu_cry_winter_zip", TOKYO6_CHIFUYU_SABUN_ZIP, "#image:14"),
        ("official_expression_tokyo6_chifuyu_blush_winter_zip", TOKYO6_CHIFUYU_SABUN_ZIP, "#image:15"),
        ("official_expression_tokyo6_chifuyu_smile_winter_zip", TOKYO6_CHIFUYU_SABUN_ZIP, "#image:16"),
        ("official_expression_tokyo6_chifuyu_surprise_winter_zip", TOKYO6_CHIFUYU_SABUN_ZIP, "#image:23"),
    ],
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


def cache_paths(cache_dir: Path, url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.bin", cache_dir / f"{digest}.json"


def read_cached_image(cache_dir: Path | None, url: str) -> tuple[bytes, str] | None:
    if cache_dir is None:
        return None
    data_path, meta_path = cache_paths(cache_dir, url)
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        data = data_path.read_bytes()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    content_type = str(meta.get("content_type") or "").split(";", 1)[0].strip().lower()
    if not content_type.startswith("image/") or len(data) < 1024:
        return None
    return data, content_type


def write_cached_image(cache_dir: Path | None, url: str, data: bytes, content_type: str) -> None:
    if cache_dir is None:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        data_path, meta_path = cache_paths(cache_dir, url)
        data_path.write_bytes(data)
        meta_path.write_text(
            json.dumps({"url": url, "content_type": content_type}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        return


def read_cached_blob(cache_dir: Path | None, cache_key: str) -> bytes | None:
    if cache_dir is None:
        return None
    data_path, meta_path = cache_paths(cache_dir, cache_key)
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        data = data_path.read_bytes()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    content_type = str(meta.get("content_type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/zip" or len(data) < 1024:
        return None
    return data


def write_cached_blob(cache_dir: Path | None, cache_key: str, data: bytes, content_type: str) -> None:
    if cache_dir is None:
        return
    content_type = content_type.split(";", 1)[0].strip().lower()
    if content_type != "application/zip" or len(data) < 1024:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        data_path, meta_path = cache_paths(cache_dir, cache_key)
        data_path.write_bytes(data)
        meta_path.write_text(
            json.dumps({"url": cache_key, "content_type": content_type}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        return


def image_candidates(url: ImageUrl) -> tuple[ImageCandidate, ...]:
    if isinstance(url, (str, RefererImageUrl)):
        return (url,)
    return url


def image_candidate_url(candidate: ImageCandidate) -> str:
    if isinstance(candidate, RefererImageUrl):
        return candidate.url
    return candidate


def image_candidate_cache_key(candidate: ImageCandidate) -> str:
    if isinstance(candidate, RefererImageUrl):
        return f"{candidate.url}#referer:{candidate.referer}"
    return candidate


def image_candidate_headers(candidate: ImageCandidate) -> dict[str, str]:
    if not isinstance(candidate, RefererImageUrl):
        return HEADERS
    return {**HEADERS, "Referer": candidate.referer}


def download_image(
    sess: HttpGetSession,
    url: ImageUrl,
    *,
    cache_dir: Path | None,
) -> tuple[bytes, str] | None:
    candidates = image_candidates(url)
    for candidate in candidates:
        cached = read_cached_image(cache_dir, image_candidate_cache_key(candidate))
        if cached is not None:
            return cached
    for candidate in candidates:
        candidate_url = image_candidate_url(candidate)
        for attempt in range(1, IMAGE_DOWNLOAD_RETRIES + 1):
            try:
                resp = sess.get(
                    candidate_url,
                    headers=image_candidate_headers(candidate),
                    timeout=IMAGE_DOWNLOAD_TIMEOUT,
                )
            except requests.RequestException:
                if attempt < IMAGE_DOWNLOAD_RETRIES:
                    time.sleep(IMAGE_DOWNLOAD_RETRY_BACKOFF * attempt)
                    continue
                break
            image = image_response(resp)
            if image is not None:
                write_cached_image(cache_dir, image_candidate_cache_key(candidate), image[0], image[1])
                return image
            if attempt < IMAGE_DOWNLOAD_RETRIES:
                time.sleep(IMAGE_DOWNLOAD_RETRY_BACKOFF * attempt)
                continue
            break
    return None


def download_animated_frame(
    sess: HttpGetSession,
    url: str,
    frame_index: int,
    *,
    cache_dir: Path | None,
) -> tuple[bytes, str] | None:
    cache_key = f"{url}#frame:{frame_index}"
    cached = read_cached_image(cache_dir, cache_key)
    if cached is not None:
        return cached

    for attempt in range(1, IMAGE_DOWNLOAD_RETRIES + 1):
        try:
            resp = sess.get(url, headers=HEADERS, timeout=ANIMATED_IMAGE_DOWNLOAD_TIMEOUT)
        except requests.RequestException:
            if attempt < IMAGE_DOWNLOAD_RETRIES:
                time.sleep(IMAGE_DOWNLOAD_RETRY_BACKOFF * attempt)
                continue
            break
        content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if resp.status_code != 200 or not content_type.startswith("image/") or len(resp.content) < 1024:
            if attempt < IMAGE_DOWNLOAD_RETRIES:
                time.sleep(IMAGE_DOWNLOAD_RETRY_BACKOFF * attempt)
                continue
            break
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(resp.content))
            if frame_index < 0 or frame_index >= getattr(image, "n_frames", 1):
                return None
            image.seek(frame_index)
            frame = image.convert("RGBA")
            background = Image.new("RGBA", frame.size, (255, 255, 255, 255))
            background.alpha_composite(frame)
            buf = io.BytesIO()
            background.convert("RGB").save(buf, format="PNG")
        except Exception:
            if attempt < IMAGE_DOWNLOAD_RETRIES:
                time.sleep(IMAGE_DOWNLOAD_RETRY_BACKOFF * attempt)
                continue
            break
        data = buf.getvalue()
        if len(data) < 1024:
            return None
        write_cached_image(cache_dir, cache_key, data, "image/png")
        return data, "image/png"
    return None


def download_cropped_image(
    sess: HttpGetSession,
    url: ImageUrl,
    crop_box: tuple[int, int, int, int],
    *,
    cache_dir: Path | None,
) -> tuple[bytes, str] | None:
    candidates = image_candidates(url)
    crop_suffix = ",".join(str(value) for value in crop_box)
    for candidate in candidates:
        cached = read_cached_image(cache_dir, f"{image_candidate_cache_key(candidate)}#crop:{crop_suffix}")
        if cached is not None:
            return cached

    image = download_image(sess, url, cache_dir=cache_dir)
    if image is None:
        return None

    data, _content_type = image
    try:
        from PIL import Image

        source = Image.open(io.BytesIO(data)).convert("RGB")
        x0, y0, x1, y1 = crop_box
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0 or x1 > source.width or y1 > source.height:
            return None
        cropped = source.crop((x0, y0, x1, y1))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
    except Exception:
        return None

    cropped_data = buf.getvalue()
    if len(cropped_data) < 1024:
        return None
    for candidate in candidates:
        write_cached_image(
            cache_dir,
            f"{image_candidate_cache_key(candidate)}#crop:{crop_suffix}",
            cropped_data,
            "image/png",
        )
    return cropped_data, "image/png"


def zip_member_content_type(member: str) -> str | None:
    suffix = Path(member).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return None


def zip_image_members(zf: zipfile.ZipFile) -> list[str]:
    return [
        name
        for name in zf.namelist()
        if zip_member_content_type(name) is not None and not name.endswith("/")
    ]


def select_zip_image_member(zf: zipfile.ZipFile, member: str) -> str | None:
    if not member.startswith("#image:"):
        return member
    try:
        index = int(member.removeprefix("#image:"))
    except ValueError:
        return None
    images = zip_image_members(zf)
    if index < 0 or index >= len(images):
        return None
    return images[index]


def download_zip_image(
    sess: HttpGetSession,
    zip_url: str,
    member: str,
    *,
    cache_dir: Path | None,
) -> tuple[bytes, str] | None:
    cache_key = f"{zip_url}#{member}"
    cached = read_cached_image(cache_dir, cache_key)
    if cached is not None:
        return cached

    zip_cache_key = f"{zip_url}#zip"
    zip_data = read_cached_blob(cache_dir, zip_cache_key)
    if zip_data is not None:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                selected_member = select_zip_image_member(zf, member)
                if selected_member is None:
                    return None
                content_type = zip_member_content_type(selected_member)
                if content_type is None:
                    return None
                data = zf.read(selected_member)
        except (KeyError, zipfile.BadZipFile, OSError):
            zip_data = None
        else:
            if len(data) < 1024:
                return None
            write_cached_image(cache_dir, cache_key, data, content_type)
            return data, content_type

    for attempt in range(1, IMAGE_DOWNLOAD_RETRIES + 1):
        try:
            resp = sess.get(zip_url, headers=HEADERS, timeout=IMAGE_DOWNLOAD_TIMEOUT)
        except requests.RequestException:
            if attempt < IMAGE_DOWNLOAD_RETRIES:
                time.sleep(IMAGE_DOWNLOAD_RETRY_BACKOFF * attempt)
                continue
            break
        if resp.status_code != 200 or len(resp.content) < 1024:
            if attempt < IMAGE_DOWNLOAD_RETRIES:
                time.sleep(IMAGE_DOWNLOAD_RETRY_BACKOFF * attempt)
                continue
            break
        content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        write_cached_blob(cache_dir, zip_cache_key, resp.content, content_type)
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                selected_member = select_zip_image_member(zf, member)
                if selected_member is None:
                    return None
                content_type = zip_member_content_type(selected_member)
                if content_type is None:
                    return None
                data = zf.read(selected_member)
        except (KeyError, zipfile.BadZipFile, OSError):
            if attempt < IMAGE_DOWNLOAD_RETRIES:
                time.sleep(IMAGE_DOWNLOAD_RETRY_BACKOFF * attempt)
                continue
            break
        if len(data) < 1024:
            return None
        write_cached_image(cache_dir, cache_key, data, content_type)
        return data, content_type
    return None


def moegirl_page_image(sess: HttpGetSession, title: str) -> str | None:
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


def normalized_token(value: object) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff\u3040-\u30ff]+", "", str(value or "")).casefold()


def moegirl_gallery_source(file_title: str, index: int) -> str:
    if re.search(r"(三视图|三視圖|设定|設定|设计|設計|拆分|人设|人設|细部|細部)", file_title, re.IGNORECASE):
        kind = "sheet"
    elif re.search(r"(立绘|立繪|公式形象|形象|官方服|公式服|渲染|full)", file_title, re.IGNORECASE):
        kind = "full"
    else:
        kind = "profile"
    return f"moegirl_{kind}_{index:02d}"


def mediawiki_title_key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("_", " ")).strip().casefold()


def moegirl_file_image_urls(
    sess: HttpGetSession,
    file_titles: list[str],
    *,
    start_index: int,
) -> list[tuple[str, ImageUrl]]:
    fallback: list[tuple[str, str]] = [
        (
            moegirl_gallery_source(file_title, start_index + idx),
            f"https://zh.moegirl.org.cn/Special:Redirect/file/{quote(file_title.removeprefix('File:'))}",
        )
        for idx, file_title in enumerate(file_titles)
    ]
    if not file_titles:
        return []

    try:
        resp = sess.get(
            MOEGIRL_API,
            params={
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "iiprop": "url|mime",
                "titles": "|".join(file_titles),
            },
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
    except (requests.RequestException, ValueError):
        return [(source, url) for source, url in fallback]

    urls_by_title: dict[str, str] = {}
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        title = mediawiki_title_key(page.get("title"))
        image_info = page.get("imageinfo")
        if not title or not isinstance(image_info, list) or not image_info:
            continue
        first = image_info[0]
        if not isinstance(first, dict):
            continue
        url = first.get("url")
        mime = str(first.get("mime") or "").lower()
        if url and mime.startswith("image/"):
            urls_by_title[title] = str(url)

    out: list[tuple[str, ImageUrl]] = []
    for (source, fallback_url), file_title in zip(fallback, file_titles, strict=True):
        direct_url = urls_by_title.get(mediawiki_title_key(file_title))
        if direct_url and direct_url != fallback_url:
            out.append((source, (direct_url, fallback_url)))
        else:
            out.append((source, fallback_url))
    return out


def moegirl_gallery_urls(
    sess: HttpGetSession,
    title: str,
    character: CharacterDef,
    *,
    start_index: int,
) -> list[tuple[str, ImageUrl]]:
    keywords = [
        token
        for token in (
            normalized_token(value)
            for value in [
                character.name,
                *character.aliases,
                *character.query_names,
                character.character_id.replace("_", ""),
            ]
        )
        if len(token) >= 2 and token not in {"ai"}
    ]
    file_titles: list[str] = []
    continuation: dict[str, str] = {}
    for _page_no in range(MOEGIRL_GALLERY_MAX_PAGES):
        try:
            resp = sess.get(
                MOEGIRL_API,
                params={
                    "action": "query",
                    "format": "json",
                    "prop": "images",
                    "titles": title,
                    "imlimit": "500",
                    **continuation,
                },
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
            pages = payload.get("query", {}).get("pages", {})
        except (requests.RequestException, ValueError):
            return []

        for page in pages.values():
            if not isinstance(page, dict):
                continue
            for item in page.get("images", []) or []:
                if not isinstance(item, dict):
                    continue
                raw_title = str(item.get("title") or "")
                if not raw_title.startswith("File:"):
                    continue
                filename = raw_title.removeprefix("File:")
                normalized_filename = normalized_token(filename)
                if not MOEGIRL_IMAGE_EXT_RE.search(filename):
                    continue
                if MOEGIRL_GALLERY_BAD_RE.search(filename):
                    continue
                if not MOEGIRL_GALLERY_POSITIVE_RE.search(filename):
                    continue
                if not any(keyword in normalized_filename for keyword in keywords):
                    continue
                if raw_title not in file_titles:
                    file_titles.append(raw_title)
                if len(file_titles) >= MOEGIRL_GALLERY_LIMIT:
                    break
            if len(file_titles) >= MOEGIRL_GALLERY_LIMIT:
                break
        if len(file_titles) >= MOEGIRL_GALLERY_LIMIT:
            break
        next_continuation = payload.get("continue")
        if not isinstance(next_continuation, dict):
            break
        continuation = {str(key): str(value) for key, value in next_continuation.items()}

    return moegirl_file_image_urls(sess, file_titles, start_index=start_index)


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


def utaitedb_main_image(sess: HttpGetSession, character: CharacterDef) -> str | None:
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
            raw_picture = item.get("mainPicture")
            picture = raw_picture if isinstance(raw_picture, dict) else {}
            url = picture.get("urlOriginal") or picture.get("urlThumb")
            if url and targets.intersection(names):
                return str(url)
    return None


def gather_images(
    sess: HttpGetSession,
    character: CharacterDef,
    *,
    cache_dir: Path | None,
) -> list[tuple[str, bytes, str]]:
    selected: list[tuple[str, bytes, str]] = []
    urls: list[tuple[str, ImageUrl]] = []

    ut_url = utaitedb_main_image(sess, character)
    if ut_url:
        urls.append(("utaitedb", ut_url))

    for title in MOEGIRL_TITLES.get(character.source_id, MOEGIRL_TITLES.get(character.character_id, [])):
        mg_url = moegirl_page_image(sess, title)
        if mg_url:
            urls.append((f"moegirl_{slug(title)}", mg_url))
        urls.extend(moegirl_gallery_urls(sess, title, character, start_index=len(urls)))
    extra_files = MOEGIRL_EXTRA_FILES.get(character.source_id, MOEGIRL_EXTRA_FILES.get(character.character_id, []))
    if extra_files:
        urls.extend(moegirl_file_image_urls(sess, extra_files, start_index=len(urls)))

    zip_urls = ZIP_IMAGE_URLS.get(character.character_id, [])
    animated_frame_urls = ANIMATED_FRAME_IMAGE_URLS.get(character.character_id, [])
    cropped_urls = CROPPED_IMAGE_URLS.get(character.character_id, [])
    urls.extend(SYNTHV_WIKI_IMAGE_URLS.get(character.character_id, []))
    urls.extend(EXTRA_IMAGE_URLS.get(character.character_id, []))

    seen_urls: set[str] = set()
    for source, url in urls:
        candidate_keys = [image_candidate_cache_key(candidate) for candidate in image_candidates(url)]
        if all(candidate in seen_urls for candidate in candidate_keys):
            continue
        seen_urls.update(candidate_keys)
        if selected:
            time.sleep(IMAGE_DOWNLOAD_INTERVAL)
        image = download_image(sess, url, cache_dir=cache_dir)
        if image is None:
            continue
        data, content_type = image
        ext = ".png" if content_type == "image/png" else ".jpg"
        filename = f"{character.character_id}_{source}{ext}"
        selected.append((filename, data, content_type))

    for source, url, frame_index in animated_frame_urls:
        cache_key = f"{url}#frame:{frame_index}"
        if cache_key in seen_urls:
            continue
        seen_urls.add(cache_key)
        if selected:
            time.sleep(IMAGE_DOWNLOAD_INTERVAL)
        image = download_animated_frame(sess, url, frame_index, cache_dir=cache_dir)
        if image is None:
            continue
        data, content_type = image
        filename = f"{character.character_id}_{source}.png"
        selected.append((filename, data, content_type))

    for source, url, crop_box in cropped_urls:
        cache_key = f"{url}#crop:{','.join(str(value) for value in crop_box)}"
        if cache_key in seen_urls:
            continue
        seen_urls.add(cache_key)
        if selected:
            time.sleep(IMAGE_DOWNLOAD_INTERVAL)
        image = download_cropped_image(sess, url, crop_box, cache_dir=cache_dir)
        if image is None:
            continue
        data, content_type = image
        filename = f"{character.character_id}_{source}.png"
        selected.append((filename, data, content_type))

    for source, zip_url, member in zip_urls:
        cache_key = f"{zip_url}#{member}"
        if cache_key in seen_urls:
            continue
        seen_urls.add(cache_key)
        if selected:
            time.sleep(IMAGE_DOWNLOAD_INTERVAL)
        image = download_zip_image(sess, zip_url, member, cache_dir=cache_dir)
        if image is None:
            continue
        data, content_type = image
        ext = Path(member).suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
            ext = ".png" if content_type == "image/png" else ".jpg"
        filename = f"{character.character_id}_{source}{ext}"
        selected.append((filename, data, content_type))
    return selected


def source_form(source: str) -> str:
    if source.startswith("archived_expression"):
        return "expression"
    if source.startswith("archived_chibi"):
        return "chibi"
    if source.startswith("line_creator_expression"):
        return "expression"
    if source.startswith("line_creator_chibi"):
        return "chibi"
    if source.startswith("press_release_chibi"):
        return "chibi"
    if source.startswith("official_expression"):
        return "expression"
    if source in OFFICIAL_EXPRESSION_PROFILE_SOURCES:
        return "expression"
    if source.startswith("official_chibi"):
        return "chibi"
    if source.startswith("official_full"):
        return "full_body"
    if source.startswith("official_sheet"):
        return "normal_proportion"
    if source.startswith("official_profile"):
        return "profile_art"
    if source.startswith("moegirl_full"):
        return "full_body"
    if source.startswith("moegirl_sheet"):
        return "normal_proportion"
    if source.startswith("moegirl_profile"):
        return "profile_art"
    if source.startswith("synthvwiki_full"):
        return "full_body"
    if source.startswith("synthvwiki_chibi"):
        return "chibi"
    if source.startswith("synthvwiki_sheet"):
        return "normal_proportion"
    if source.startswith("synthvwiki_profile"):
        return "profile_art"
    if source.startswith("vcpedia_sheet"):
        return "normal_proportion"
    if source.startswith("utaitedb") or source.startswith("moegirl_"):
        return "profile_art"
    return "other"


def character_payloads(
    characters: list[CharacterDef],
    training_stats_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in characters:
        payload: dict[str, Any] = {
            "character_id": item.character_id,
            "name": item.name,
            "aliases": item.aliases,
            "context_label": item.context_label,
        }
        stats = training_stats_by_id.get(item.character_id)
        if stats is not None:
            payload["training_stats"] = stats
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
    training_stats_by_id: dict[str, dict[str, Any]],
    timeout: int,
) -> dict[str, Any]:
    np = cast(Any, __import__("numpy"))

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
        entry: dict[str, Any] = {
            "character_id": character.character_id,
            "name": character.name,
            "embedding_key": character.character_id,
            "aliases": character.aliases,
            "context_label": character.context_label,
        }
        stats = training_stats_by_id.get(character.character_id)
        if stats is not None:
            entry["training_stats"] = stats
        manifest_characters.append(entry)

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
    training_stats_by_id: dict[str, dict[str, Any]],
    timeout: int,
) -> Path:
    characters_json = json.dumps(character_payloads(pack.characters, training_stats_by_id), ensure_ascii=False)
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
        payload = build_pack_via_embed(
            sidecar=sidecar,
            pack=pack,
            files=files,
            training_stats_by_id=training_stats_by_id,
            timeout=timeout,
        )
        return land_charpack(payload["charpack_zip_b64"], payload["pack_dir"], out_dir)
    if resp.status_code >= 400:
        raise SystemExit(f"sidecar build failed for {pack.pack}: {payload.get('detail') or resp.text[:200]}")
    return land_charpack(payload["charpack_zip_b64"], payload["pack_dir"], out_dir)


def collect_pack_files(
    pack: PackDef,
    *,
    cache_dir: Path | None,
) -> tuple[list[tuple[str, tuple[str, bytes, str]]], dict[str, dict[str, Any]]]:
    sess = requests.Session()
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    training_stats_by_id: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for character in pack.characters:
        images = gather_images(sess, character, cache_dir=cache_dir)
        if not images:
            missing.append(character.character_id)
            print(f"{pack.pack:20s} {character.character_id:24s} images=0 MISSING")
            continue
        for filename, data, content_type in images:
            files.append(("images", (filename, data, content_type)))
        sources = [Path(filename).stem.removeprefix(f"{character.character_id}_") for filename, _, _ in images]
        form_counts: dict[str, int] = {}
        for source in sources:
            form = source_form(source)
            form_counts[form] = form_counts.get(form, 0) + 1
        training_stats_by_id[character.character_id] = {
            "image_count": len(images),
            "forms": form_counts,
            "sources": sources,
            "missing_forms": [form for form in REQUESTED_FORM_BUCKETS if form_counts.get(form, 0) < 1],
        }
        missing_forms = training_stats_by_id[character.character_id]["missing_forms"]
        print(
            f"{pack.pack:20s} {character.character_id:24s} images={len(images)} "
            f"missing_forms={','.join(missing_forms) or '-'} sources={','.join(sources)}"
        )
    if missing:
        raise SystemExit(f"{pack.pack} missing images for: {missing}")
    return files, training_stats_by_id


def main() -> None:
    ap = argparse.ArgumentParser(description="Build virtual singer character packs")
    ap.add_argument("--roster", type=Path, default=Path("tools/virtual_singers_roster.py"))
    ap.add_argument("--sidecar", default="http://localhost:8620")
    ap.add_argument("--out", type=Path, default=Path("config/character_packs"))
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--pack", choices=("all", "zh", "ja"), default="all")
    ap.add_argument("--image-cache-dir", type=Path, default=Path(".cache/character-pack-images/virtual_singers"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    zh_chars, ja_chars = load_roster(args.roster)
    packs = [
        PackDef(pack="zh_virtual_singers", work="中V", characters=zh_chars),
        PackDef(pack="ja_virtual_singers", work="日V", characters=ja_chars),
    ]
    if args.pack != "all":
        packs = [pack for pack in packs if pack.pack.startswith(args.pack)]
    total_images = 0
    for pack in packs:
        files, training_stats_by_id = collect_pack_files(pack, cache_dir=args.image_cache_dir)
        total_images += len(files)
        print(f"\n{pack.pack}: characters={len(pack.characters)} images={len(files)}")
        if not args.dry_run:
            path = build_pack(
                sidecar=args.sidecar,
                out_dir=args.out,
                pack=pack,
                files=files,
                training_stats_by_id=training_stats_by_id,
                timeout=args.timeout,
            )
            print(f"wrote {path}")
            print(f"embeddings: {(path / 'embeddings.npz').stat().st_size} bytes\n")
    print(f"done packs={len(packs)} images={total_images}")


if __name__ == "__main__":
    main()
