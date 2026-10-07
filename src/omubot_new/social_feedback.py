"""Typed, body-free social observations from the authenticated OneBot ingress.

Conversation owns retention and permission proofs. This module parses source
facts and projects the approved reaction nudges; it performs no I/O or state writes.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from .types import OperationError, Scope


@dataclass(frozen=True, slots=True)
class GroupSocialNotice:
    scope: Scope
    event_id: str
    actor_id: str
    observed_at: float
    kind: Literal["reaction", "poke", "ban"]
    target_id: str = ""
    message_id: str = ""
    emoji_code: str = ""
    removed: bool = False


def parse_group_social_notice(
    payload: Mapping[str, object], *, expected_bot_id: str
) -> GroupSocialNotice | None:
    if payload.get("post_type") != "notice":
        return None
    if str(payload.get("self_id", "")) != expected_bot_id:
        raise OperationError("onebot_identity_mismatch")
    kind = str(payload.get("notice_type", ""))
    if kind == "notify" and payload.get("sub_type") == "poke":
        notice_kind: Literal["reaction", "poke", "ban"] = "poke"
        actor, target = payload.get("user_id"), payload.get("target_id")
    elif kind == "group_ban" and payload.get("sub_type") == "ban":
        duration = payload.get("duration")
        if type(duration) is not int or duration <= 0:
            return None
        notice_kind = "ban"
        actor, target = payload.get("operator_id"), payload.get("user_id")
    elif kind in {"group_msg_emoji_like", "message_reaction", "group_message_reaction"}:
        notice_kind = "reaction"
        actor, target = payload.get("user_id"), ""
    else:
        return None
    group, timestamp = payload.get("group_id"), payload.get("time")
    if (
        type(group) not in (str, int)
        or not str(group).strip()
        or type(actor) not in (str, int)
        or not str(actor).strip()
        or not isinstance(timestamp, (float, int))
        or isinstance(timestamp, bool)
        or not math.isfinite(timestamp)
    ):
        raise OperationError("invalid_social_notice")
    message_id = str(payload.get("message_id", ""))
    code = str(payload.get("emoji_code", payload.get("emoji_id", "")))
    removed = payload.get("sub_type") in {"remove", "unset"}
    if notice_kind == "reaction":
        likes = cast(list[object] | None, payload.get("likes"))
        if isinstance(likes, list) and len(likes) == 1 and isinstance(likes[0], dict):
            like = cast(dict[str, object], likes[0])
            code = str(like.get("emoji_id", ""))
            removed = like.get("count") == 0
        if not message_id or not code:
            raise OperationError("invalid_social_notice")
    elif type(target) not in (str, int) or not str(target).strip():
        raise OperationError("invalid_social_notice")
    fields = [
        expected_bot_id,
        str(group),
        str(actor),
        timestamp,
        notice_kind,
        str(target),
        message_id,
        code,
        removed,
    ]
    identity = "notice:" + hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()
    return GroupSocialNotice(
        Scope(bot_id=expected_bot_id, group_id=str(group)),
        identity,
        str(actor),
        float(timestamp),
        notice_kind,
        str(target),
        message_id,
        code,
        removed,
    )


_EMOJI_SENTIMENT: dict[str, tuple[str, float, str]] = {
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


def reaction_sentiment(code: str) -> tuple[str, float]:
    polarity, intensity, _ = _EMOJI_SENTIMENT.get(code, ("positive", 0.2, "unknown"))
    return polarity, intensity


def notice_nudge(notice: GroupSocialNotice) -> tuple[float, float]:
    """Legacy directional valence/tension increments before cap and linear decay."""
    if notice.removed:
        return 0.0, 0.0
    if notice.kind == "poke":
        return 0.0, 0.04
    if notice.kind != "reaction":
        return 0.0, 0.0
    polarity, intensity = reaction_sentiment(notice.emoji_code)
    if polarity == "negative":
        return -intensity * 0.12, intensity * 0.06
    if polarity == "positive":
        return intensity * (0.05 if notice.emoji_code not in _EMOJI_SENTIMENT else 0.10), 0.0
    return 0.0, 0.0


def message_sentiment(text: str) -> Literal["positive", "negative", "neutral"]:
    """Explicit feedback phrases only; no model, guessed confidence or broad word polarity."""
    compact = text.strip().strip("。！!，, ")
    if compact in {"别说话", "闭嘴", "别回了", "说错了", "不对", "不好"}:
        return "negative"
    if compact in {"谢谢", "谢谢你", "说得好", "说得对", "赞", "不错", "哈哈", "喜欢"}:
        return "positive"
    return "neutral"
