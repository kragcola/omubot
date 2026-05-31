from __future__ import annotations

import pytest

from services.name_registry import MemberInfo, NameVariationRegistry


class _FakeBot:
    async def get_group_member_list(self, *, group_id: int) -> list[dict[str, object]]:
        assert group_id == 100
        return [
            {"user_id": 1, "nickname": "小明", "card": "阿明"},
            {"user_id": 2, "nickname": "小红", "card": ""},
        ]


@pytest.mark.asyncio
async def test_name_registry_refresh_and_lookup() -> None:
    registry = NameVariationRegistry()

    await registry.refresh(_FakeBot(), "100")

    assert registry.lookup_by_uid("100", 1) == MemberInfo(user_id=1, nickname="小明", card="阿明")
    assert registry.lookup_by_name("100", "阿明") == MemberInfo(user_id=1, nickname="小明", card="阿明")
    assert registry.lookup_by_name("100", "小红") == MemberInfo(user_id=2, nickname="小红", card="")


def test_name_registry_prefix_match_and_ambiguity() -> None:
    registry = NameVariationRegistry()
    registry.update_from_event("100", 1, "小明", "阿明")
    registry.update_from_event("100", 2, "小红", "")
    registry.update_from_event("100", 3, "小蓝", "")

    assert registry.lookup_by_name("100", "小红") == MemberInfo(user_id=2, nickname="小红", card="")
    assert registry.lookup_by_name("100", "阿") is None
    assert registry.lookup_by_name("100", "小") is None
    assert registry.lookup_by_name("100", "小蓝") == MemberInfo(user_id=3, nickname="小蓝", card="")


def test_name_registry_recent_speakers_and_known_bot() -> None:
    registry = NameVariationRegistry()
    registry.update_from_event("100", 1, "小明", "")
    registry.update_from_event("100", 2, "小红", "")
    registry.update_from_event("100", 1, "小明", "")

    recent = registry.recent_speakers("100", limit=2)

    assert [member.user_id for member in recent] == [1, 2]
    assert registry.is_known_bot("100", 42, {"100": ["42"]}) is True
    assert registry.is_known_bot("100", 43, {"100": ["42"]}) is False
