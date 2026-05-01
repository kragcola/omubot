"""Tests that bot's own messages are correctly included in history loading."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from nonebot.adapters.onebot.v11.bot import Bot

from plugins.history_loader import load_group_history
from services.memory.timeline import GroupTimeline


def _make_bot(messages: list[dict[str, Any]]) -> MagicMock:
    """Build a mock Bot whose call_api returns history messages."""
    bot = MagicMock(spec=Bot)
    bot.call_api = AsyncMock(return_value={"messages": messages})
    return bot


def _msg(user_id: int, text: str, nickname: str = "user", msg_id: int = 1) -> dict[str, Any]:
    return {
        "sender": {"user_id": user_id, "nickname": nickname},
        "message": [{"type": "text", "data": {"text": text}}],
        "message_id": msg_id,
    }


@pytest.fixture
def timeline() -> GroupTimeline:
    return GroupTimeline()


@pytest.mark.asyncio
async def test_bot_messages_classified_as_assistant(
    timeline: GroupTimeline, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bot's own messages should be role=assistant in the timeline."""
    bot_id = "123456"
    messages = [
        _msg(999, "hello", "Alice", 1),
        _msg(123456, "hi Alice!", "Bot", 2),
        _msg(999, "how are you?", "Alice", 3),
    ]

    mock_bot = _make_bot(messages)

    # Mock aiohttp.ClientSession for image downloads (not used here, but needed for context manager)
    mock_session = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: mock_session_ctx)

    await load_group_history(
        bot=mock_bot,  # type: ignore[arg-type]
        group_ids=["100"],
        timeline=timeline,
        count=30,
        bot_self_id=bot_id,
    )

    turns = timeline.get_turns("100")
    pending = timeline.get_pending("100")
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["content"] == "hi Alice!"
    assert len(pending) == 1
    assert pending[0]["role"] == "user"


@pytest.mark.asyncio
async def test_bot_messages_produce_valid_turns(timeline: GroupTimeline) -> None:
    """Timeline with bot messages should produce valid alternating-role turns."""
    timeline.add("100", role="user", speaker="Alice(999)", content="hello")
    timeline.add("100", role="assistant", content="hi!")
    timeline.add("100", role="user", speaker="Alice(999)", content="how are you?")

    turns = timeline.get_turns("100")
    pending = timeline.get_pending("100")
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["content"] == "hi!"
    assert len(pending) == 1
    assert pending[0]["role"] == "user"


@pytest.mark.asyncio
async def test_consecutive_assistant_turns_preserved(timeline: GroupTimeline) -> None:
    """Consecutive assistant messages (from history reload) are stored as separate turns."""
    timeline.add("100", role="user", speaker="Alice(999)", content="hello")
    timeline.add("100", role="assistant", content="hi!")
    timeline.add("100", role="assistant", content="how can I help?")
    timeline.add("100", role="user", speaker="Alice(999)", content="thanks")

    turns = timeline.get_turns("100")
    pending = timeline.get_pending("100")
    assert len(turns) == 3
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["content"] == "hi!"
    assert turns[2]["role"] == "assistant"
    assert turns[2]["content"] == "how can I help?"
    assert len(pending) == 1
    assert pending[0]["content"] == "thanks"


@pytest.mark.asyncio
async def test_no_bot_self_id_all_user(
    timeline: GroupTimeline, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without bot_self_id, ALL messages become user role (the bug scenario)."""
    messages = [
        _msg(999, "hello", "Alice", 1),
        _msg(123456, "hi Alice!", "Bot", 2),
        _msg(999, "how are you?", "Alice", 3),
    ]

    mock_bot = _make_bot(messages)

    mock_session = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: mock_session_ctx)

    await load_group_history(
        bot=mock_bot,  # type: ignore[arg-type]
        group_ids=["100"],
        timeline=timeline,
        count=30,
        bot_self_id="",
    )

    turns = timeline.get_turns("100")
    pending = timeline.get_pending("100")
    assert len(turns) == 0
    assert len(pending) == 3
    assert all(m["role"] == "user" for m in pending)


@pytest.mark.asyncio
async def test_api_error_handled_gracefully(
    timeline: GroupTimeline, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When call_api raises ActionFailed, the group is skipped without crashing."""
    from nonebot.adapters.onebot.v11.exception import ActionFailed

    mock_bot = MagicMock(spec=Bot)
    mock_bot.call_api = AsyncMock(side_effect=ActionFailed(info={"wording": "no such group"}))

    mock_session = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: mock_session_ctx)

    await load_group_history(
        bot=mock_bot,  # type: ignore[arg-type]
        group_ids=["100"],
        timeline=timeline,
        count=30,
        bot_self_id="",
    )

    turns = timeline.get_turns("100")
    pending = timeline.get_pending("100")
    assert len(turns) == 0
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_empty_messages_noop(
    timeline: GroupTimeline, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty message list should be a no-op."""
    mock_bot = _make_bot([])

    mock_session = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: mock_session_ctx)

    await load_group_history(
        bot=mock_bot,  # type: ignore[arg-type]
        group_ids=["100"],
        timeline=timeline,
        count=30,
        bot_self_id="",
    )

    turns = timeline.get_turns("100")
    pending = timeline.get_pending("100")
    assert len(turns) == 0
    assert len(pending) == 0
