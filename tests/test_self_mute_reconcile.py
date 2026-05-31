from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from nonebot.adapters.onebot.v11.exception import ActionFailed

from kernel.config import GroupConfig, SelfMuteConfig
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot
from services.scheduler import GroupChatScheduler, _GroupSlot


class _Runtime:
    def identity_snapshot(self) -> IdentitySnapshot:
        return IdentitySnapshot(id="bot", name="bot", personality="p", proactive="on")


class _LLM:
    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        del kwargs
        return None


def _scheduler(
    *,
    self_mute_config: SelfMuteConfig | None = None,
    bot: object | None = None,
    inventory: dict[str, object] | None = None,
) -> GroupChatScheduler:
    scheduler = GroupChatScheduler(
        llm=cast(Any, _LLM()),
        timeline=GroupTimeline(),
        persona_runtime=_Runtime(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0.0),
        self_mute_config=self_mute_config,
        group_inventory_getter=lambda: inventory or {},
    )
    if bot is None:
        bot = SimpleNamespace(self_id="1", send_group_msg=AsyncMock(), get_group_member_info=AsyncMock())
    scheduler.set_bot(cast(Any, bot))
    scheduler._slots["100"] = _GroupSlot()
    return scheduler


@pytest.mark.asyncio
async def test_reconcile_marks_muted_when_server_says_muted() -> None:
    bot = SimpleNamespace(
        self_id="1",
        send_group_msg=AsyncMock(),
        get_group_member_info=AsyncMock(return_value={"shut_up_timestamp": 4_102_444_800}),
    )
    scheduler = _scheduler(
        self_mute_config=SelfMuteConfig(reconcile_enabled=True, reconcile_interval_seconds=300),
        bot=bot,
        inventory={"100": {}},
    )

    await scheduler._reconcile_self_mute_once()

    assert scheduler.is_muted("100") is True
    state = scheduler.get_mute_state()
    assert state["100"]["source"] == "reconcile"
    await scheduler.close()


@pytest.mark.asyncio
async def test_reconcile_clears_muted_when_server_says_unmuted() -> None:
    bot = SimpleNamespace(
        self_id="1",
        send_group_msg=AsyncMock(),
        get_group_member_info=AsyncMock(return_value={"shut_up_timestamp": 0}),
    )
    scheduler = _scheduler(
        self_mute_config=SelfMuteConfig(reconcile_enabled=True, reconcile_interval_seconds=300),
        bot=bot,
        inventory={"100": {}},
    )
    scheduler.mute("100", source="event")

    await scheduler._reconcile_self_mute_once()

    assert scheduler.is_muted("100") is False
    assert scheduler.get_mute_state() == {}
    await scheduler.close()


@pytest.mark.asyncio
async def test_action_failed_reverse_marks_muted() -> None:
    error = ActionFailed(info={"wording": "forbidden", "retcode": 1200})
    bot = SimpleNamespace(
        self_id="1",
        send_group_msg=AsyncMock(side_effect=error),
        get_group_member_info=AsyncMock(return_value={"shut_up_timestamp": 0}),
    )
    scheduler = _scheduler(
        self_mute_config=SelfMuteConfig(
            reconcile_enabled=False,
            action_failed_reverse_mark=True,
            action_failed_retcodes=[1200],
        ),
        bot=bot,
        inventory={"100": {}},
    )

    result = await scheduler._send_to_group("100", "test")

    assert result == 0.0
    assert scheduler.is_muted("100") is True
    assert scheduler.get_mute_state()["100"]["source"] == "action_failed"
    await scheduler.close()


@pytest.mark.asyncio
async def test_reconcile_loop_cancel_path_is_clean() -> None:
    bot = SimpleNamespace(
        self_id="1",
        send_group_msg=AsyncMock(),
        get_group_member_info=AsyncMock(return_value={"shut_up_timestamp": 0}),
    )
    scheduler = _scheduler(
        self_mute_config=SelfMuteConfig(reconcile_enabled=True, reconcile_interval_seconds=3600),
        bot=bot,
        inventory={"100": {}},
    )

    task = asyncio.create_task(scheduler._reconcile_self_mute_loop())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert scheduler.get_mute_state() == {}
    await scheduler.close()
