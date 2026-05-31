"""Tests for BirthdayGreeter (calendar_context member birthday wishes)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.calendar_context.birthday_greeter import BirthdayGreeter

_TZ = timezone(timedelta(hours=8))
# Use the real "today" key so the >7-day sent_log cleanup never prunes it.
_TODAY_KEY = datetime.now(_TZ).strftime("%Y-%m-%d")


@pytest.fixture
def greeter(tmp_path: Path) -> BirthdayGreeter:
    return BirthdayGreeter(tmp_path / "member_birthdays.json")


@pytest.fixture
def mock_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    return bot


def _pin_today(greeter: BirthdayGreeter, mmdd: str, day_key: str = _TODAY_KEY) -> None:
    greeter._today_mmdd = lambda: mmdd  # type: ignore[method-assign]
    greeter._today_key = lambda: day_key  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# CRUD + persistence
# ---------------------------------------------------------------------------


def test_add_member_persists(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    g = BirthdayGreeter(path)
    g.add_member("123", "小明", "03-15", ["984198159"])
    raw = json.loads(path.read_text("utf-8"))
    assert raw["members"][0] == {
        "qq": "123",
        "name": "小明",
        "birthday_mmdd": "03-15",
        "groups": ["984198159"],
    }
    # Reload from disk reflects the same member.
    assert BirthdayGreeter(path).members[0]["qq"] == "123"


def test_add_member_upsert_same_qq(greeter: BirthdayGreeter) -> None:
    greeter.add_member("123", "旧名", "01-01", ["g1"])
    greeter.add_member("123", "新名", "03-15", ["g2"])
    assert len(greeter.members) == 1
    assert greeter.members[0]["name"] == "新名"
    assert greeter.members[0]["birthday_mmdd"] == "03-15"
    assert greeter.members[0]["groups"] == ["g2"]


def test_remove_member(greeter: BirthdayGreeter) -> None:
    greeter.add_member("123", "小明", "03-15", ["g1"])
    assert greeter.remove_member("123") is True
    assert greeter.members == []


def test_remove_member_missing(greeter: BirthdayGreeter) -> None:
    assert greeter.remove_member("999") is False


# ---------------------------------------------------------------------------
# check_and_greet — core matching + send
# ---------------------------------------------------------------------------


async def test_greet_on_birthday(greeter: BirthdayGreeter, mock_bot: MagicMock) -> None:
    greeter.add_member("123", "小明", "03-15", ["984198159"])
    _pin_today(greeter, "03-15")

    greeted = await greeter.check_and_greet(mock_bot)

    assert greeted == ["123"]
    mock_bot.send_group_msg.assert_awaited_once()
    kwargs = mock_bot.send_group_msg.await_args.kwargs
    assert kwargs["group_id"] == 984198159
    # sent_log records the qq under today's key.
    assert greeter.sent_log[_TODAY_KEY] == ["123"]


async def test_no_greet_when_not_birthday(greeter: BirthdayGreeter, mock_bot: MagicMock) -> None:
    greeter.add_member("123", "小明", "03-15", ["g1"])
    _pin_today(greeter, "07-20")

    greeted = await greeter.check_and_greet(mock_bot)

    assert greeted == []
    mock_bot.send_group_msg.assert_not_awaited()


async def test_dedup_same_day(greeter: BirthdayGreeter, mock_bot: MagicMock) -> None:
    greeter.add_member("123", "小明", "03-15", ["111"])
    _pin_today(greeter, "03-15")

    first = await greeter.check_and_greet(mock_bot)
    second = await greeter.check_and_greet(mock_bot)

    assert first == ["123"]
    assert second == []  # already greeted today
    mock_bot.send_group_msg.assert_awaited_once()


async def test_greet_multiple_groups(greeter: BirthdayGreeter, mock_bot: MagicMock) -> None:
    greeter.add_member("123", "小明", "03-15", ["111", "222"])
    _pin_today(greeter, "03-15")

    await greeter.check_and_greet(mock_bot)

    assert mock_bot.send_group_msg.await_count == 2
    sent_groups = {call.kwargs["group_id"] for call in mock_bot.send_group_msg.await_args_list}
    assert sent_groups == {111, 222}


async def test_send_failure_still_records_greeted(greeter: BirthdayGreeter, mock_bot: MagicMock) -> None:
    """A failed send for one group must not crash the run; the member is still
    marked greeted so a retry next tick doesn't double-send to working groups."""
    greeter.add_member("123", "小明", "03-15", ["111"])
    _pin_today(greeter, "03-15")
    mock_bot.send_group_msg.side_effect = RuntimeError("network")

    greeted = await greeter.check_and_greet(mock_bot)

    assert greeted == ["123"]
    assert greeter.sent_log[_TODAY_KEY] == ["123"]


# ---------------------------------------------------------------------------
# sent_log cleanup
# ---------------------------------------------------------------------------


async def test_old_sent_log_cleaned(greeter: BirthdayGreeter, mock_bot: MagicMock) -> None:
    # Seed an old log entry (well beyond the 7-day window) directly.
    greeter._data["sent_log"] = {"2020-01-01": ["999"]}
    greeter.add_member("123", "小明", "03-15", ["g1"])
    _pin_today(greeter, "03-15")

    await greeter.check_and_greet(mock_bot)

    assert "2020-01-01" not in greeter.sent_log  # pruned (>7d)
    assert _TODAY_KEY in greeter.sent_log


# ---------------------------------------------------------------------------
# wish text generation
# ---------------------------------------------------------------------------


async def test_wish_fallback_without_llm(greeter: BirthdayGreeter) -> None:
    text = await greeter._generate_wish("小明", None)
    assert text == "小明，生日快乐！🎂"


async def test_wish_uses_llm_text(greeter: BirthdayGreeter) -> None:
    llm = MagicMock()
    llm._call = AsyncMock(return_value={"text": "小明生日快乐呀，今天要开开心心的！"})
    text = await greeter._generate_wish("小明", llm)
    assert text == "小明生日快乐呀，今天要开开心心的！"


async def test_wish_llm_failure_falls_back(greeter: BirthdayGreeter) -> None:
    llm = MagicMock()
    llm._call = AsyncMock(side_effect=RuntimeError("boom"))
    text = await greeter._generate_wish("小明", llm)
    assert text == "小明，生日快乐！🎂"


async def test_corrupt_file_resets_to_default(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", "utf-8")
    g = BirthdayGreeter(path)
    assert g.members == []
    assert g.sent_log == {}
