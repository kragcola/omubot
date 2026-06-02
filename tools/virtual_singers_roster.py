#!/usr/bin/env python3
r"""Virtual Singer（虚拟歌姬）character roster for CCIP enrollment.

只区分 中V / 日V，不做引擎细分。每个条目含 notability tier
供试点分批。

Image sourcing: 萌娘百科 (zh.moegirl.org.cn)、VOCALOID Wiki
(vocaloid.fandom.com)、Danbooru 官方立绘 tag。

Crypton 6 人（初音ミク/鏡音リン/鏡音レン/巡音ルカ/MEIKO/KAITO）
已通过 PJSK 录入（work=プロジェクトセカイ），列在 ``ALREADY_ENROLLED``
供参考。如需独立 VOCALOID centroid，必须使用不同的 character_id；
``tools/enroll_virtual_singers_pack.py`` 会把它们 remap 为 ``vocaloid_*``。

.. attention::
   试点 3-5 个 tier-1 角色，验证识别质量后再全量。
"""

from __future__ import annotations

type Entry = tuple[str, str, str, int, str]  # (name_zh, name_jp|en, cid, tier, notes)

# ============================================================================
# Work — broad category only
# ============================================================================
ZH_V_WORK = "中V"
JA_V_WORK = "日V"

# ============================================================================
# 中V
# ============================================================================

# Vsinger / 上海禾念 (VOCALOID)
_ZH_VSINGER: list[Entry] = [
    ("洛天依",   "洛天依",    "luo_tianyi",        1, "灰发+中国结+碧色裙，中V 绝对 icon"),
    ("言和",     "言和",      "yan_he",            1, "白发短发+中性西装，中V 第二位"),
    ("乐正绫",   "乐正绫",    "yuezheng_ling",     1, "赤发双马尾+赤瞳，乐正集团大小姐"),
    ("乐正龙牙", "乐正龙牙",  "yuezheng_longya",   2, "乐正绫兄，白发+眼镜+执事装"),
    ("徵羽摩柯", "徵羽摩柯",  "zhiyu_moke",        2, "蓝发正太+贝雷帽，程序员人设"),
    ("墨清弦",   "墨清弦",    "mo_qingxian",       2, "紫发御姐，学霸人设"),
]

# 五维介质 Quadimension (Synthesizer V)
_ZH_QUADIMENSION: list[Entry] = [
    ("星尘",     "星尘",      "xingchen",          1, "银灰长发+四角星发饰，超高人气"),
    ("海伊",     "海伊",      "hai_yi",            2, "蓝发+水手服"),
    ("苍穹",     "苍穹",      "cang_qiong",        2, "银发+蓝瞳+科技感装束"),
    ("赤羽",     "赤羽",      "chi_yu",            2, "赤发+赤瞳+凤凰意象"),
    ("诗岸",     "诗岸",      "shi_an",            2, "绿发+森系少女，2024 年刊黑马 top5"),
    ("牧心",     "牧心",      "mu_xin",            3, "金发+白袍，太阳神意象"),
    ("永夜Minus","永夜Minus", "yongye_minus",      3, "黑发+哥特装，Quadimension 最晚加入"),
]

# 飞天胶囊 E-CAPSULE (Synthesizer V / UTAU) — 台湾
_ZH_ECAPSULE: list[Entry] = [
    ("夏语遥",   "夏語遥",    "xia_yuyao",         2, "棕发+发饰，台湾 SynthV，首款中文 SynthV AI"),
]

# 其他 中V
_ZH_OTHER: list[Entry] = [
    ("心华",     "心華",      "xin_hua",           2, "淡紫双马尾+学生装，台湾 VOCALOID"),
    ("东方栀子", "東方栀子",  "dongfang_zhizi",    3, "中国初代 UTAU，赤发+中国风"),
]

ZH_V: list[Entry] = _ZH_VSINGER + _ZH_QUADIMENSION + _ZH_ECAPSULE + _ZH_OTHER

# ============================================================================
# 日V (excluding Crypton 6 — already enrolled via PJSK)
# ============================================================================

# Internet Co. (VOCALOID)
_JA_INTERNET: list[Entry] = [
    ("GUMI",        "GUMI",         "gumi",             1, "绿发+护目镜，Megpoid，日V top"),
    ("神威がくぽ",  "神威がくぽ",   "kamui_gakupo",     2, "紫长发+武士刀，Gackpoid/茄"),
    ("Lily",        "Lily",         "lily",             3, "金发+蓝瞳+偶像装"),
    ("音街ウナ",    "音街ウナ",     "otomachi_una",     2, "绿短双马尾+鲷鱼烧发饰"),
]

# 1st Place (VOCALOID / CeVIO)
_JA_1STPLACE: list[Entry] = [
    ("IA",          "IA",           "ia",               1, "白长发+赤瞳，IA -ARIA ON THE PLANETES-"),
    ("ONE",         "ONE",          "one",              3, "IA 的妹妹，橙发+短发"),
]

# AH-Software / TOKYO6 (VOCALOID / SynthV / VOICEPEAK / VOICEROID)
_JA_AHS_TOKYO6: list[Entry] = [
    ("重音テト",   "重音テト",     "kasane_teto",      1,
     "赤发双钻头+赤瞳，UTAU→SynthV→VOICEPEAK，2024-25 年最火之一"),
    ("結月ゆかり", "結月ゆかり",   "yuzuki_yukari",    2, "紫长发+月形发饰，VOICEROID→VOCALOID"),
    ("紲星あかり", "紲星あかり",   "kizuna_akari",     3, "结月ゆかり姐妹机，赤发"),
    ("歌愛ユキ",   "歌愛ユキ",     "kaai_yuki",        2, "小学生+粉短发，可爱系代表（「强风大背头」原唱）"),
    ("猫村いろは", "猫村いろは",   "nekomura_iroha",   3, "猫耳+粉发+和服，Hello Kitty 联名"),
    ("東北ずん子", "東北ずん子",   "tohoku_zunko",     2, "绿长发+ずんだ餅发饰，东北应援角色"),
    ("東北きりたん","東北きりたん","tohoku_kiritan",   3, "ずん子妹，银发+短发+辣妹装"),
    ("琴葉茜",     "琴葉茜",       "kotonoha_akane",   3, "VOICEROID 关西腔，棕发+马尾"),
    ("琴葉葵",     "琴葉葵",       "kotonoha_aoi",     3, "VOICEROID 标准语，茜的妹妹，蓝发"),
    ("小春六花",   "小春六花",     "koharu_rikka",     2, "粉棕发+高马尾，TOKYO6 SynthV"),
    ("弦巻マキ",   "弦巻マキ",     "tsurumaki_maki",   2, "棕发+短发，TOKYO6 SynthV 日英双语"),
    ("夏色花梨",   "夏色花梨",     "natsuki_karin",    2, "金发双马尾，TOKYO6 SynthV"),
    ("花隈千冬",   "花隈千冬",     "hanakuma_chifuyu", 2, "黑发+白发挑染，TOKYO6 SynthV"),
    ("京町セイカ", "京町セイカ",   "kyomachi_seika",   3, "黑发+巫女装，TOKYO6 SynthV"),
    ("追儺ちゃん", "追儺ちゃん",   "tsuina_chan",      3, "鬼娘+角+红白巫女风，AHS SynthV"),
]

# 神椿 KAMITSUBAKI — 音乐的同位体 (CeVIO AI / VOICEPEAK)
_JA_KAMITSUBAKI: list[Entry] = [
    ("可不",       "可不",         "kafu",             1, "白长发+紫瞳+口罩，花譜同位体，CeVIO AI，现象级人气"),
    ("星界",       "星界",         "sekai",            2, "银长发+蓝瞳，異世界情緒同位体，CeVIO AI"),
    ("裏命",       "裏命",         "rime",             2, "紫发+异色瞳，理芽同位体，CeVIO AI"),
    ("狐子",       "狐子",         "coko",             2, "赤发+狐耳，幸祜同位体，CeVIO AI"),
    ("羽累",       "羽累",         "haru",             3, "绿短发+运动装，春猿火同位体，CeVIO AI"),
]

# EXIT TUNES / 其他
_JA_OTHER: list[Entry] = [
    ("MAYU",        "MAYU",         "mayu",             3, "粉发+双马尾+病娇人设，EXIT TUNES"),
    ("v flower",    "v flower",     "v_flower",         1, "白短发+中性嗓音，摇滚系代表"),
]

JA_V: list[Entry] = (
    _JA_INTERNET + _JA_1STPLACE + _JA_AHS_TOKYO6 + _JA_KAMITSUBAKI + _JA_OTHER
)

# ============================================================================
# Already enrolled — reference only, DO NOT re-enroll with same character_id.
# The virtual singer enrollment script remaps these to vocaloid_* ids.
# ============================================================================
ALREADY_ENROLLED: list[Entry] = [
    ("初音未来", "初音ミク", "hatsune_miku",   1, "PJSK 已录入"),
    ("镜音铃",   "鏡音リン", "kagamine_rin",   1, "PJSK 已录入"),
    ("镜音连",   "鏡音レン", "kagamine_len",   1, "PJSK 已录入"),
    ("巡音流歌", "巡音ルカ", "megurine_luka",  1, "PJSK 已录入"),
    ("MEIKO",    "MEIKO",    "meiko",          2, "PJSK 已录入"),
    ("KAITO",    "KAITO",    "kaito",          2, "PJSK 已录入"),
]

# ===========================================================================
# Flat master roster
# ===========================================================================
ROSTER: list[Entry] = [
    (zh, jp, cid, tier, ZH_V_WORK) for zh, jp, cid, tier, _ in ZH_V
] + [
    (zh, jp, cid, tier, JA_V_WORK) for zh, jp, cid, tier, _ in JA_V
]

# ===========================================================================
# Stats
# ===========================================================================
def _print_summary() -> None:
    t1 = [e for e in ROSTER if e[3] == 1]
    t2 = [e for e in ROSTER if e[3] == 2]
    t3 = [e for e in ROSTER if e[3] == 3]
    t1_zh = [e for e in t1 if e[-1] == ZH_V_WORK]
    t1_ja = [e for e in t1 if e[-1] == JA_V_WORK]

    print(f"中V: {len(ZH_V)}  |  日V: {len(JA_V)}  |  已录入: {len(ALREADY_ENROLLED)}  |  合计待录入: {len(ROSTER)}")
    print(f"Tier 1: {len(t1)} (中 {len(t1_zh)} / 日 {len(t1_ja)})  |  Tier 2: {len(t2)}  |  Tier 3: {len(t3)}")
    print()
    print("=== Tier 1 (建议试点 3-5) ===")
    for zh, jp, cid, _tier, cat, notes in (
        [(z, j, c, t, ZH_V_WORK, n) for z, j, c, t, n in ZH_V if t == 1]
        + [(z, j, c, t, JA_V_WORK, n) for z, j, c, t, n in JA_V if t == 1]
    ):
        print(f"  [{cat}] {zh} ({jp}) — {cid} — {notes}")
    print()
    print("=== Tier 2 ===")
    for zh, jp, cid, _tier, cat, notes in (
        [(z, j, c, t, ZH_V_WORK, n) for z, j, c, t, n in ZH_V if t == 2]
        + [(z, j, c, t, JA_V_WORK, n) for z, j, c, t, n in JA_V if t == 2]
    ):
        print(f"  [{cat}] {zh} ({jp}) — {cid} — {notes}")
    print()
    print("=== Tier 3 ===")
    for zh, jp, cid, _tier, cat, notes in (
        [(z, j, c, t, ZH_V_WORK, n) for z, j, c, t, n in ZH_V if t == 3]
        + [(z, j, c, t, JA_V_WORK, n) for z, j, c, t, n in JA_V if t == 3]
    ):
        print(f"  [{cat}] {zh} ({jp}) — {cid} — {notes}")


if __name__ == "__main__":
    _print_summary()
