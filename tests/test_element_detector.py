
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.types import MessageContext
from plugins.element_detector import ElementDetector, ElementRule
from plugins.element_detector.plugin import ElementDetectorPlugin
from services.memory.timeline import GroupTimeline


def test_detect_simple_match() -> None:
    rules = [ElementRule(pattern="早安|早上好", reply="哦哈哟，{nickname}！")]
    detector = ElementDetector(rules)
    result = detector.detect("大家早上好呀", nickname="小明", user_id="123")
    assert result is not None
    assert result.reply_template == "哦哈哟，小明！"
    assert result.use_llm is False


def test_detect_named_groups() -> None:
    rules = [ElementRule(pattern=r"叫我\s*(?P<name>.+)", reply="好的{nickname}，以后叫你{name}！")]
    detector = ElementDetector(rules)
    result = detector.detect("以后叫我小可爱吧", nickname="小红", user_id="456")
    assert result is not None
    assert "小可爱" in result.reply_template


def test_detect_no_match() -> None:
    rules = [ElementRule(pattern="晚安", reply="晚安～")]
    detector = ElementDetector(rules)
    result = detector.detect("大家早上好", nickname="小明", user_id="123")
    assert result is None


def test_detect_first_match_wins() -> None:
    rules = [
        ElementRule(pattern="Hello", reply="first"),
        ElementRule(pattern="Hello", reply="second"),
    ]
    detector = ElementDetector(rules)
    result = detector.detect("Hello world", nickname="x", user_id="1")
    assert result is not None
    assert result.reply_template == "first"


def test_detect_empty_rules() -> None:
    detector = ElementDetector([])
    assert detector.detect("anything", nickname="x", user_id="1") is None


def test_detect_invalid_pattern_skipped() -> None:
    rules = [
        ElementRule(pattern="***invalid[[", reply="bad"),
        ElementRule(pattern="hello", reply="good"),
    ]
    detector = ElementDetector(rules)
    result = detector.detect("hello", nickname="x", user_id="1")
    assert result is not None
    assert result.reply_template == "good"


def test_detect_match_placeholder() -> None:
    rules = [ElementRule(pattern=r"hello", reply="{nickname} saw: {match}")]
    detector = ElementDetector(rules)
    result = detector.detect("hello world", nickname="Alice", user_id="99")
    assert result is not None
    assert result.reply_template == "Alice saw: hello"


def test_detect_use_llm_flag() -> None:
    rules = [ElementRule(pattern="test", reply="generate something", use_llm=True)]
    detector = ElementDetector(rules)
    result = detector.detect("test message", nickname="x", user_id="1")
    assert result is not None
    assert result.use_llm is True
    assert result.reply_template == "generate something"


def test_detect_named_group_with_llm() -> None:
    rules = [ElementRule(
        pattern=r"(?P<content>.+?)是这样的",
        reply="用户说「{match}」。生成反差回复。",
        use_llm=True,
    )]
    detector = ElementDetector(rules)
    result = detector.detect("前线的战士是这样的", nickname="小红", user_id="456")
    assert result is not None
    assert result.use_llm is True
    assert "前线的战士是这样的" in result.reply_template


@pytest.mark.asyncio
async def test_record_and_send_reply_enqueues_without_waiting_when_scheduler_available() -> None:
    plugin = ElementDetectorPlugin()
    plugin._humanizer = SimpleNamespace(delay=AsyncMock())  # type: ignore[attr-defined]
    timeline = GroupTimeline()
    plugin._timeline = timeline  # type: ignore[attr-defined]
    loop = asyncio.get_running_loop()
    send_done: asyncio.Future[float] = loop.create_future()
    plugin._scheduler = SimpleNamespace(enqueue_group_text=AsyncMock(return_value=send_done))  # type: ignore[attr-defined]

    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    ctx = MessageContext(
        session_id="group_123",
        group_id="123",
        user_id="456",
        content="hello",
        raw_message={},
        bot=bot,
    )

    await plugin._record_and_send_reply(ctx, "123", "对")

    plugin._scheduler.enqueue_group_text.assert_awaited_once_with(  # type: ignore[attr-defined]
        "123",
        "对",
        description="element_detector",
    )
    plugin._humanizer.delay.assert_not_awaited()  # type: ignore[attr-defined]
    bot.send_group_msg.assert_not_awaited()
    assert timeline.get_turns("123")[-1]["visible_state"] == "pending"

    send_done.set_result(0.1)
    await asyncio.sleep(0)

    assert timeline.get_turns("123")[-1]["visible_state"] == "complete"
