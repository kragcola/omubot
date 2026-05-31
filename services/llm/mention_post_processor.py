"""Convert literal @name mentions into CQ:at codes."""

from __future__ import annotations

import re

from services.name_registry import NameVariationRegistry

_MENTION_RE = re.compile(r"(?:(?<=^)|(?<=[\s,，。.!！？?:：;；(（\[]))@([A-Za-z0-9_\-\u4e00-\u9fff]{1,20})")


def process_mentions(
    reply_text: str,
    group_id: str,
    registry: NameVariationRegistry,
    *,
    bot_self_id: str,
    recent_speaker_limit: int = 20,
) -> str:
    recent = registry.recent_speakers(group_id, limit=recent_speaker_limit)
    recent_ids = [member.user_id for member in recent]

    def repl(match: re.Match[str]) -> str:
        name = str(match.group(1) or "").strip()
        if not name:
            return match.group(0)
        if name == "全体成员":
            return match.group(0).replace(f"@{name}", "[CQ:at,qq=all]")
        member = registry.lookup_by_name(group_id, name, candidate_user_ids=recent_ids)
        if member is None:
            member = registry.lookup_by_name(group_id, name)
        if member is None:
            return match.group(0)
        if str(member.user_id) == str(bot_self_id or ""):
            return match.group(0)
        return match.group(0).replace(f"@{name}", f"[CQ:at,qq={member.user_id}]")

    return _MENTION_RE.sub(repl, str(reply_text or ""))
