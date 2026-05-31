from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.llm.schedule_overshare_detector import detect, schedule_overshare_rule
from services.llm.sentinel_registry import GuardrailContext


def _config(*, enabled: bool = True, threshold: int = 2) -> object:
    return SimpleNamespace(
        schedule_overshare=SimpleNamespace(
            enabled=enabled,
            cumulative_threshold=threshold,
            bypass_patterns=["几点", "什么时候", "日程", "安排", "忙不忙", "在干嘛", "在做什么", "干啥呢"],
            leak_patterns=[
                r"\d{1,2}[：:]\d{2}",
                "上午",
                "下午",
                "晚上",
                "排练",
                "吃饭",
                "休息",
                "上课",
                "午饭",
                "晚饭",
            ],
        )
    )


def test_detect_bypasses_when_user_asks_schedule() -> None:
    result = detect("下午有排练", "你今天忙不忙")

    assert result.hit is False


def test_detect_hits_and_dampens_unsolicited_schedule_sentence() -> None:
    result = detect("对吧哈哈，我下午3:00还要排练呢。先聊这个。", "哈哈好搞笑")

    assert result.hit is True
    assert result.reason == "unsolicited_time_mention"
    assert result.dampened_text == "先聊这个。"


def test_detect_is_safe_without_time_clue() -> None:
    result = detect("我喜欢画画", "你喜欢什么")

    assert result.hit is False


def test_detect_uses_cumulative_threshold_reason() -> None:
    result = detect("晚上要休息了", "嗯嗯", session_count=2, cumulative_threshold=2)

    assert result.hit is True
    assert result.reason == "cumulative_threshold"


def test_schedule_overshare_rule_rewrites_when_enabled() -> None:
    ctx = GuardrailContext(
        user_message="哈哈好搞笑",
        session_count=0,
        config=_config(enabled=True),
    )

    result = schedule_overshare_rule("对吧哈哈，我下午3:00还要排练呢。先聊这个。", ctx)

    assert result.passed is True
    assert result.text == "先聊这个。"
    assert [hit.name for hit in result.hits] == ["schedule_overshare"]
    assert result.metadata["schedule_overshare_reason"] == "unsolicited_time_mention"


@pytest.mark.asyncio
async def test_schedule_overshare_cancel_path_does_not_dirty_session_count() -> None:
    counter = {"count": 0}

    async def _cancelled_before_commit() -> None:
        result = detect("晚上要休息了", "嗯嗯", session_count=counter["count"], cumulative_threshold=2)
        assert result.hit is True
        await asyncio.sleep(60)
        counter["count"] += 1  # pragma: no cover - must remain unreachable

    task = asyncio.create_task(_cancelled_before_commit())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert counter["count"] == 0
