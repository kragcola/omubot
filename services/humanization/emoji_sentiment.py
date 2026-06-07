"""QQ message-reaction emoji → sentiment polarity mapping.

NapCat message-reaction notices carry an ``emoji_code`` (the QQ classic face
ID as a string, e.g. "171" = 点赞). This module maps those IDs to a coarse
sentiment so reaction events can feed directional mood nudges.

IDs and names are grounded in the authoritative table in ``kernel/qq_face.py``
(NOT the speculative IDs in the 2026-05-27 prerequisites doc, which were wrong:
that doc claimed 76=👍 but 76 is actually 彩虹). Unknown codes default to a weak
positive signal because actively reacting is itself a participation signal.
"""

from __future__ import annotations

from typing import Literal

Polarity = Literal["positive", "negative", "neutral"]

# QQ face ID (str) → (polarity, intensity 0.0-1.0, label). Labels mirror
# kernel/qq_face.py for traceability.
EMOJI_SENTIMENT: dict[str, tuple[Polarity, float, str]] = {
    # --- strong positive ---
    "171": ("positive", 0.9, "点赞"),
    "78": ("positive", 0.8, "强"),
    "228": ("positive", 0.9, "比心"),
    "227": ("positive", 0.8, "崇拜"),
    "290": ("positive", 0.7, "牛啊"),
    "86": ("positive", 0.8, "爱你"),
    "89": ("positive", 0.7, "爱情"),
    "326": ("positive", 0.7, "OK"),
    "214": ("positive", 0.7, "666"),
    "318": ("positive", 0.7, "666"),
    "229": ("positive", 0.7, "庆祝"),
    "144": ("positive", 0.7, "喝彩"),
    "42": ("positive", 0.6, "鼓掌"),
    "77": ("positive", 0.6, "拥抱"),
    "62": ("positive", 0.6, "玫瑰"),
    "285": ("positive", 0.6, "真好"),
    "111": ("positive", 0.6, "帅"),
    # --- weak positive ---
    "146": ("positive", 0.5, "笑哭"),
    "241": ("positive", 0.5, "狂笑"),
    "20": ("positive", 0.4, "偷笑"),
    "13": ("positive", 0.4, "呲牙"),
    "28": ("positive", 0.4, "憨笑"),
    "21": ("positive", 0.3, "可爱"),
    "4": ("positive", 0.3, "得意"),
    "12": ("positive", 0.3, "调皮"),
    "14": ("positive", 0.2, "微笑"),
    "298": ("positive", 0.4, "啵啵"),
    # --- strong negative ---
    "322": ("negative", 0.9, "翻白眼"),
    "22": ("negative", 0.8, "白眼"),
    "85": ("negative", 0.8, "差劲"),
    "299": ("negative", 0.8, "嫌弃"),
    "11": ("negative", 0.8, "发怒"),
    "219": ("negative", 0.8, "发怒"),
    "233": ("negative", 0.8, "生气"),
    "31": ("negative", 0.8, "咒骂"),
    "312": ("negative", 0.7, "NO"),
    "297": ("negative", 0.7, "拒绝"),
    "265": ("negative", 0.7, "辣眼睛"),
    "79": ("negative", 0.6, "弱"),
    "18": ("negative", 0.6, "抓狂"),
    # --- weak negative ---
    "272": ("negative", 0.5, "呵呵哒"),
    "276": ("negative", 0.4, "无语"),
    "63": ("negative", 0.4, "凋谢"),
    "66": ("negative", 0.4, "心碎"),
    "225": ("negative", 0.4, "心碎"),
    "36": ("negative", 0.3, "衰"),
    # --- neutral / ambiguous ---
    "0": ("neutral", 0.0, "惊讶"),
    "32": ("neutral", 0.0, "疑问"),
    "268": ("neutral", 0.0, "问号脸"),
    "271": ("neutral", 0.0, "吃瓜"),
    "269": ("neutral", 0.0, "暗中观察"),
    "270": ("neutral", 0.0, "emm"),
    "245": ("neutral", 0.0, "哦"),
    "242": ("neutral", 0.0, "面无表情"),
    "278": ("neutral", 0.0, "面无表情"),
}

# Unknown reaction codes: a weak positive participation signal.
_UNKNOWN_DEFAULT: tuple[Polarity, float] = ("positive", 0.2)


def classify_reaction_sentiment(emoji_code: str) -> tuple[Polarity, float]:
    """Return ``(polarity, intensity)`` for a QQ message-reaction emoji code.

    Unknown / empty codes fall back to a weak positive signal: a user bothering
    to react at all is mild engagement.
    """
    if not emoji_code:
        return _UNKNOWN_DEFAULT
    entry = EMOJI_SENTIMENT.get(emoji_code)
    if entry is not None:
        return entry[0], entry[1]
    return _UNKNOWN_DEFAULT
