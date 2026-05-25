from __future__ import annotations

import asyncio

from kernel.config import GroupConfig
from kernel.types import TriggerContext
from services.identity import Identity
from services.memory.timeline import GroupTimeline
from services.scheduler import GroupChatScheduler


def _make_identity() -> Identity:
    return Identity(id="test", name="测试", personality="测试人设", proactive="积极参与群聊")


class _FakeIdentityMgr:
    def resolve(self) -> Identity:
        return _make_identity()


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, **kwargs) -> str | None:  # type: ignore[override]
        self.calls.append(kwargs)
        return None


def _make_scheduler(llm: _FakeLLM) -> GroupChatScheduler:
    return GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        identity_mgr=_FakeIdentityMgr(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0, batch_size=100),
    )


async def test_at_force_reply_keeps_self_target() -> None:
    llm = _FakeLLM()
    scheduler = _make_scheduler(llm)

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", extra={"addressee_self": True}),
    )
    await asyncio.sleep(0.1)

    assert llm.calls[0]["force_reply"] is True
    await scheduler.close()


async def test_at_force_reply_tightens_non_self_target() -> None:
    llm = _FakeLLM()
    scheduler = _make_scheduler(llm)

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", extra={"addressee_self": False}),
    )
    await asyncio.sleep(0.1)

    assert llm.calls[0]["force_reply"] is False
    await scheduler.close()


async def test_video_always_keeps_force_reply() -> None:
    llm = _FakeLLM()
    scheduler = _make_scheduler(llm)

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="视频分享", mode="video_always", extra={"addressee_self": False}),
    )
    await asyncio.sleep(0.1)

    assert llm.calls[0]["force_reply"] is True
    await scheduler.close()
