"""Behavioral slice: memory source/subject/visibility at write + safe group recall.

Frozen contract (G-MEM):
- Extracted facts preserve source conversation group_id when present, subject
  user/entity, visibility private|same_group|global, provenance and confidence.
- In a group, automatic recall may include the current speaker's same-group-visible
  user facts; it must exclude another user's facts, another group's facts and
  private-chat facts.
- Never merge all user cards into group recall.
- Existing cards without new metadata fail closed for cross-scope recall.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from plugins.memo.plugin import MemoConfig, MemoExtractor
from services.memory.card_store import CardStore, NewCard
from services.memory.retrieval import RetrievalGate


@pytest.fixture
async def card_store(tmp_path) -> AsyncIterator[CardStore]:
    store = CardStore(db_path=str(tmp_path / "visibility_cards.db"))
    await store.init()
    try:
        yield store
    finally:
        await store.close()


class FakeLLM:
    def __init__(self, text: str = "无") -> None:
        self.text = text
        self.calls: list[Any] = []

    async def __call__(self, request: Any) -> dict[str, str]:
        self.calls.append(request)
        return {"text": self.text}


# ---------------------------------------------------------------------------
# RED / GREEN: extractor write-time visibility (legacy + write-policy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_extractor_preserves_group_origin_and_same_group_visibility(
    card_store: CardStore,
) -> None:
    """Group extraction must keep user scope but attach origin_group + same_group."""
    llm = FakeLLM('[fact] 用户指出图片里的角色是高松灯')
    extractor = MemoExtractor(
        card_store=card_store,
        api_call=llm,
        config=MemoConfig(write_policy_enabled=False),
    )
    await extractor.extract_after_turn(
        user_id="u_speaker",
        group_id="g_alpha",
        user_msg="这是高松灯",
        bot_reply="好的我记住了",
        source_message_id="msg-1",
    )
    cards = await card_store.get_entity_cards("user", "u_speaker")
    assert len(cards) == 1, "legacy path must still write one user-scope card"
    card = cards[0]
    assert card.scope == "user"
    assert card.scope_id == "u_speaker"
    assert getattr(card, "origin_group_id", None) == "g_alpha", (
        "extracted fact must preserve source conversation group_id"
    )
    assert getattr(card, "visibility", None) == "same_group", (
        "group-sourced user fact must be same_group-visible, not private-only"
    )
    # New extractor cards require subject_user_id exactly equal to speaker.
    assert getattr(card, "subject_user_id", None) == "u_speaker"


@pytest.mark.asyncio
async def test_write_policy_extractor_preserves_group_origin_and_same_group_visibility(
    card_store: CardStore,
) -> None:
    llm = FakeLLM(
        '{"category":"fact","content":"用户指出图片角色是守岸人","action":"add"}'
    )
    extractor = MemoExtractor(
        card_store=card_store,
        api_call=llm,
        config=MemoConfig(write_policy_enabled=True),
    )
    await extractor.extract_after_turn(
        user_id="u_speaker",
        group_id="g_alpha",
        user_msg="这是守岸人",
        bot_reply="记下了",
        source_message_id="msg-wp-1",
    )
    cards = await card_store.get_entity_cards("user", "u_speaker")
    assert len(cards) == 1
    card = cards[0]
    assert card.scope == "user"
    assert getattr(card, "origin_group_id", None) == "g_alpha"
    assert getattr(card, "visibility", None) == "same_group"
    assert getattr(card, "subject_user_id", None) == "u_speaker"


@pytest.mark.asyncio
async def test_private_chat_extractor_marks_visibility_private(
    card_store: CardStore,
) -> None:
    llm = FakeLLM('[preference] 用户偏好被称呼为帆酱')
    extractor = MemoExtractor(
        card_store=card_store,
        api_call=llm,
        config=MemoConfig(write_policy_enabled=False),
    )
    await extractor.extract_after_turn(
        user_id="u_private",
        group_id=None,
        user_msg="叫我帆酱",
        bot_reply="好的帆酱",
    )
    cards = await card_store.get_entity_cards("user", "u_private")
    assert len(cards) == 1
    card = cards[0]
    assert getattr(card, "origin_group_id", None) in (None, "")
    assert getattr(card, "visibility", None) == "private"
    assert getattr(card, "subject_user_id", None) == "u_private"


# ---------------------------------------------------------------------------
# RED / GREEN: group recall isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_recall_includes_current_speaker_same_group_user_fact(
    card_store: CardStore,
) -> None:
    """Positive: same speaker + same origin group + same_group visibility."""
    await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="u_speaker",
            content="用户指出图片角色是高松灯",
            confidence=0.6,
            source="extractor",
            origin_group_id="g_alpha",  # type: ignore[call-arg]
            visibility="same_group",  # type: ignore[call-arg]
            subject_user_id="u_speaker",  # type: ignore[call-arg]
        )
    )
    await card_store.add_card(
        NewCard(
            category="event",
            scope="group",
            scope_id="g_alpha",
            content="本群正在讨论图片身份",
            confidence=0.7,
            source="manual",
        )
    )
    gate = RetrievalGate(card_store=card_store, refresh_interval=99)
    block = await gate.build_memo_block(
        "sess_g1",
        "u_speaker",
        "g_alpha",
    )
    assert "用户指出图片角色是高松灯" in block, (
        "group recall must surface current speaker same_group-visible user facts"
    )
    assert "本群正在讨论图片身份" in block


@pytest.mark.asyncio
async def test_group_recall_excludes_other_user_same_group_facts(
    card_store: CardStore,
) -> None:
    await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="u_other",
            content="其他用户的私密偏好：不吃香菜",
            confidence=0.8,
            source="extractor",
            origin_group_id="g_alpha",  # type: ignore[call-arg]
            visibility="same_group",  # type: ignore[call-arg]
            subject_user_id="u_other",  # type: ignore[call-arg]
        )
    )
    gate = RetrievalGate(card_store=card_store, refresh_interval=99)
    block = await gate.build_memo_block("sess_g2", "u_speaker", "g_alpha")
    assert "不吃香菜" not in block
    assert "其他用户的私密偏好" not in block


@pytest.mark.asyncio
async def test_group_recall_excludes_other_group_origin_facts(
    card_store: CardStore,
) -> None:
    await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="u_speaker",
            content="来自群B的事实：用户在群B当过管理员",
            confidence=0.8,
            source="extractor",
            origin_group_id="g_beta",  # type: ignore[call-arg]
            visibility="same_group",  # type: ignore[call-arg]
            subject_user_id="u_speaker",  # type: ignore[call-arg]
        )
    )
    gate = RetrievalGate(card_store=card_store, refresh_interval=99)
    block = await gate.build_memo_block("sess_g3", "u_speaker", "g_alpha")
    assert "群B当过管理员" not in block
    assert "来自群B的事实" not in block


@pytest.mark.asyncio
async def test_group_recall_excludes_private_chat_facts(
    card_store: CardStore,
) -> None:
    await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id="u_speaker",
            content="私聊偏好：叫我帆酱",
            confidence=0.9,
            source="extractor",
            origin_group_id=None,  # type: ignore[call-arg]
            visibility="private",  # type: ignore[call-arg]
            subject_user_id="u_speaker",  # type: ignore[call-arg]
        )
    )
    gate = RetrievalGate(card_store=card_store, refresh_interval=99)
    block = await gate.build_memo_block("sess_g4", "u_speaker", "g_alpha")
    assert "帆酱" not in block
    assert "私聊偏好" not in block


@pytest.mark.asyncio
async def test_group_recall_fail_closed_for_legacy_user_cards_without_visibility(
    card_store: CardStore,
) -> None:
    """Legacy cards lack visibility metadata → must not leak into group recall."""
    await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="u_speaker",
            content="旧卡无可见性元数据：用户喜欢音游",
            confidence=0.7,
            source="extractor",
        )
    )
    gate = RetrievalGate(card_store=card_store, refresh_interval=99)
    block = await gate.build_memo_block("sess_g5", "u_speaker", "g_alpha")
    assert "喜欢音游" not in block
    assert "旧卡无可见性" not in block


@pytest.mark.asyncio
async def test_private_chat_still_sees_own_legacy_user_cards(
    card_store: CardStore,
) -> None:
    """Fail-closed is only for cross-scope; private own-scope still sees legacy."""
    await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="u_speaker",
            content="旧卡无可见性元数据：用户喜欢音游",
            confidence=0.7,
            source="extractor",
        )
    )
    gate = RetrievalGate(card_store=card_store, refresh_interval=99)
    block = await gate.build_memo_block("sess_p1", "u_speaker", None)
    assert "喜欢音游" in block
