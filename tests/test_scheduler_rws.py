from __future__ import annotations

import asyncio

from kernel.config import GroupConfig, HumanizationConfig
from services.identity import Identity
from services.memory.timeline import GroupTimeline
from services.scheduler import GroupChatScheduler
from services.scheduler_eot import EOTCache


def _identity() -> Identity:
    return Identity(id="test", name="测试", personality="测试人设", proactive="积极参与群聊")


class _IdentityMgr:
    def resolve(self) -> Identity:
        return _identity()


class _LLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return None


async def test_rws_shadow_records_explanation_without_changing_decision() -> None:
    llm = _LLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        identity_mgr=_IdentityMgr(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0),
        humanization_config=HumanizationConfig(rws_shadow=True),
    )

    scheduler.notify("100")
    await asyncio.sleep(0.05)

    slot = scheduler.get_all_slots()["100"]
    assert len(llm.calls) == 1
    assert slot["last_rws"] is not None
    assert slot["last_response_class"] == "full_reply"
    await scheduler.close()


async def test_rws_primary_can_skip_legacy_probability_fire() -> None:
    llm = _LLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        identity_mgr=_IdentityMgr(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=0.4, planner_smooth=0),
        humanization_config=HumanizationConfig(rws_primary=True, rws_threshold=0.9),
    )

    scheduler.notify("100")
    await asyncio.sleep(0.05)

    slot = scheduler.get_all_slots()["100"]
    assert len(llm.calls) == 0
    assert slot["last_rws"] is not None
    assert slot["last_response_class"] == "silence"
    await scheduler.close()


async def test_rws_eot_reserves_prefetch_quota_before_background_refresh() -> None:
    llm = _LLM()
    timeline = GroupTimeline()
    timeline.add("100", role="user", content="现在该接话吗", speaker="user")
    eot_cache = EOTCache(ttl_s=10, min_interval_s=30)
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=timeline,
        identity_mgr=_IdentityMgr(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=0.0, planner_smooth=0),
        humanization_config=HumanizationConfig(rws_shadow=True, rws_eot=True),
        eot_cache=eot_cache,
    )

    scheduler.notify("100")
    assert eot_cache.can_call("100") is False

    scheduler.notify("100")
    assert eot_cache.can_call("100") is False
    await asyncio.sleep(0)
    await scheduler.close()
