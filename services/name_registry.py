"""Group member name registry for addressee/mention wiring."""

from __future__ import annotations

from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MemberInfo:
    user_id: int
    nickname: str
    card: str = ""

    def names(self) -> tuple[str, ...]:
        values: list[str] = []
        for raw in (self.card, self.nickname):
            value = str(raw or "").strip()
            if value and value not in values:
                values.append(value)
        return tuple(values)


class NameVariationRegistry:
    """Per-group registry of member names and recent speakers."""

    def __init__(self) -> None:
        self._groups: dict[str, dict[int, MemberInfo]] = {}
        self._recent_speakers: dict[str, deque[int]] = {}

    async def refresh(self, bot: Any, group_id: str) -> None:
        """Refresh a group's member list from OneBot."""
        if bot is None:
            return
        rows = await bot.get_group_member_list(group_id=int(group_id))
        members: dict[int, MemberInfo] = {}
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            user_id = _to_int(row.get("user_id"))
            if user_id <= 0:
                continue
            members[user_id] = MemberInfo(
                user_id=user_id,
                nickname=str(row.get("nickname", "") or "").strip(),
                card=str(row.get("card", "") or "").strip(),
            )
        if members:
            self._groups[str(group_id)] = members
            self._recent_speakers.setdefault(str(group_id), deque(maxlen=64))

    def update_from_event(self, group_id: str, user_id: int, nickname: str, card: str) -> None:
        gid = str(group_id)
        bucket = self._groups.setdefault(gid, {})
        bucket[int(user_id)] = MemberInfo(
            user_id=int(user_id),
            nickname=str(nickname or "").strip(),
            card=str(card or "").strip(),
        )
        recent = self._recent_speakers.setdefault(gid, deque(maxlen=64))
        with suppress(ValueError):
            recent.remove(int(user_id))
        recent.appendleft(int(user_id))

    def lookup_by_name(
        self,
        group_id: str,
        name: str,
        *,
        candidate_user_ids: list[int] | tuple[int, ...] | None = None,
    ) -> MemberInfo | None:
        query = str(name or "").strip()
        if len(query) < 1:
            return None
        members = self._candidate_members(group_id, candidate_user_ids=candidate_user_ids)
        for extractor in (_card_exact, _nickname_exact, _prefix_match):
            matched = extractor(members, query)
            if matched is not None:
                return matched
        return None

    def lookup_by_uid(self, group_id: str, user_id: int) -> MemberInfo | None:
        return self._groups.get(str(group_id), {}).get(int(user_id))

    def is_known_bot(self, group_id: str, user_id: int, known_bots: dict[str, list[str]]) -> bool:
        peer_ids = {str(raw).strip() for raw in known_bots.get(str(group_id), []) if str(raw).strip()}
        return str(int(user_id)) in peer_ids

    def recent_speakers(self, group_id: str, *, limit: int = 20) -> list[MemberInfo]:
        members = self._groups.get(str(group_id), {})
        recent = self._recent_speakers.get(str(group_id), ())
        out: list[MemberInfo] = []
        for user_id in list(recent)[: max(1, int(limit))]:
            member = members.get(int(user_id))
            if member is not None:
                out.append(member)
        return out

    def _candidate_members(
        self,
        group_id: str,
        *,
        candidate_user_ids: list[int] | tuple[int, ...] | None = None,
    ) -> list[MemberInfo]:
        members = self._groups.get(str(group_id), {})
        if not candidate_user_ids:
            return list(members.values())
        result: list[MemberInfo] = []
        for user_id in candidate_user_ids:
            member = members.get(int(user_id))
            if member is not None:
                result.append(member)
        return result


def _card_exact(members: list[MemberInfo], query: str) -> MemberInfo | None:
    hits = [member for member in members if member.card and member.card == query]
    return hits[0] if len(hits) == 1 else None


def _nickname_exact(members: list[MemberInfo], query: str) -> MemberInfo | None:
    hits = [member for member in members if member.nickname and member.nickname == query]
    return hits[0] if len(hits) == 1 else None


def _prefix_match(members: list[MemberInfo], query: str) -> MemberInfo | None:
    if len(query) < 2:
        return None
    hits = [
        member
        for member in members
        if any(name.startswith(query) for name in member.names())
    ]
    return hits[0] if len(hits) == 1 else None


def _to_int(value: object) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return 0
