import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.memory.state_board import (
    GroupStateBoard,
    GroupStateSnapshot,
    clean_text,
    extract_bigrams,
    extract_nick,
    extract_qq,
)


def test_snapshot_to_prompt_text() -> None:
    snap = GroupStateSnapshot(
        active_users="帆(123)、某用户(456)",
        recent_topics="音游、表情包",
        message_frequency="活跃（过去5分钟 12 条消息）",
        recent_mentions="帆 2 分钟前 @了你",
    )
    text = snap.to_prompt_text()
    assert "【当前群聊状态】" in text
    assert "最近活跃：帆(123)、某用户(456)" in text
    assert "近期话题：音游、表情包" in text
    assert "消息频率：活跃（过去5分钟 12 条消息）" in text
    assert "最近@你：帆 2 分钟前 @了你" in text


def test_snapshot_defaults() -> None:
    snap = GroupStateSnapshot()
    text = snap.to_prompt_text()
    assert "暂无" in text
    assert "无" in text


def test_extract_qq() -> None:
    assert extract_qq("用户(123456789)") == "123456789"
    assert extract_qq("plain_name") == "plain_name"


def test_extract_nick() -> None:
    assert extract_nick("用户(123456789)") == "用户"
    assert extract_nick("plain_name") == "plain_name"


def test_clean_text() -> None:
    assert clean_text("[CQ:image,file=abc]你好世界") == "你好世界"
    assert clean_text("hello 你好 world") == "你好"
    assert clean_text(None) == ""
    assert clean_text("") == ""


def test_extract_bigrams() -> None:
    bigrams = extract_bigrams("音游分数很高")
    assert "音游" in bigrams
    assert "分数" in bigrams


@pytest.mark.asyncio
async def test_query_state_empty() -> None:
    msg_log = MagicMock()
    msg_log.query_recent = AsyncMock(return_value=[])
    board = GroupStateBoard(message_log=msg_log, bot_self_id="999")
    snap = await board.query_state("123")
    assert snap.active_users == "暂无"
    assert snap.recent_topics == "暂无显著话题"
    assert snap.message_frequency == "暂无消息"
    assert snap.recent_mentions == "无"


def _make_row(role, speaker, content_text, created_at=None, message_id="1"):
    return {
        "role": role,
        "speaker": speaker,
        "content_text": content_text or "",
        "message_id": message_id,
        "created_at": created_at or time.time(),
    }


@pytest.mark.asyncio
async def test_derive_active_users() -> None:
    rows = [
        _make_row("user", "帆帆(123)", "你好"),
        _make_row("user", "某人(456)", "大家好"),
        _make_row("assistant", "", "嗯嗯"),
        _make_row("user", "帆帆(123)", "再次发言"),  # duplicate, should be skipped
    ]
    board = GroupStateBoard(message_log=MagicMock(), bot_self_id="999")
    result = board._derive_active_users(rows)
    assert "帆帆(123)" in result
    assert "某人(456)" in result


@pytest.mark.asyncio
async def test_derive_frequency() -> None:
    now = time.time()
    rows = [
        _make_row("user", "A(1)", "msg1", now - 10),
        _make_row("user", "B(2)", "msg2", now - 30),
        _make_row("user", "C(3)", "msg3", now - 60),
        _make_row("user", "D(4)", "msg4", now - 120),
        _make_row("user", "E(5)", "msg5", now - 180),
        _make_row("user", "F(6)", "msg6", now - 200),
        _make_row("user", "G(7)", "msg7", now - 250),
        _make_row("user", "H(8)", "msg8", now - 280),
    ]
    board = GroupStateBoard(message_log=MagicMock(), bot_self_id="999")
    result = board._derive_frequency(rows)
    assert "活跃" in result


@pytest.mark.asyncio
async def test_derive_frequency_cold() -> None:
    now = time.time()
    rows = [
        _make_row("user", "A(1)", "msg1", now - 600),
    ]
    board = GroupStateBoard(message_log=MagicMock(), bot_self_id="999")
    result = board._derive_frequency(rows)
    assert result == "暂无消息"


@pytest.mark.asyncio
async def test_derive_topics() -> None:
    rows = [
        _make_row("user", "A(1)", "今天天气真好适合出去玩"),
        _make_row("user", "B(2)", "天气确实不错"),
        _make_row("user", "C(3)", "出去玩很开心"),
    ]
    board = GroupStateBoard(message_log=MagicMock(), bot_self_id="999")
    result = board._derive_topics(rows)
    assert "天气" in result or "出去玩" in result or result == "暂无显著话题"


@pytest.mark.asyncio
async def test_derive_topics_too_few() -> None:
    rows = [
        _make_row("user", "A(1)", "hi"),
        _make_row("user", "B(2)", "yo"),
    ]
    board = GroupStateBoard(message_log=MagicMock(), bot_self_id="999")
    result = board._derive_topics(rows)
    assert result == "暂无显著话题"


@pytest.mark.asyncio
async def test_derive_mentions() -> None:
    now = time.time()
    rows = [
        _make_row("user", "帆帆(123)", "@999 你好", now - 120),
        _make_row("user", "某人(456)", "普通消息", now - 60),
    ]
    board = GroupStateBoard(message_log=MagicMock(), bot_self_id="999")
    result = board._derive_mentions(rows)
    assert "帆帆" in result
    assert "某人" not in result


@pytest.mark.asyncio
async def test_derive_mentions_no_self_id() -> None:
    rows = [
        _make_row("user", "帆帆(123)", "@999 你好"),
    ]
    board = GroupStateBoard(message_log=MagicMock(), bot_self_id="")
    result = board._derive_mentions(rows)
    assert result == "无"
