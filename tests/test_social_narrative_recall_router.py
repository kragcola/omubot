from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast

from nonebot.adapters.onebot.v11 import GroupRecallNoticeEvent, NoticeEvent

from kernel.types import PluginContext

RecallInvalidator = Callable[[PluginContext, NoticeEvent], Awaitable[None]]


class _SocialNarrativeStore:
    def __init__(self) -> None:
        self.invalidations: list[tuple[str, str | int]] = []

    async def invalidate_evidence(
        self,
        *,
        group_id: str,
        evidence_message_id: str | int,
    ) -> int:
        self.invalidations.append((group_id, evidence_message_id))
        return 1


def _recall_invalidator() -> RecallInvalidator:
    router = importlib.import_module("kernel.router")
    helper = getattr(router, "_invalidate_social_narrative_recall", None)
    assert callable(helper), (
        "kernel.router must expose an async _invalidate_social_narrative_recall "
        "helper for notice routing"
    )
    return cast(RecallInvalidator, helper)


def _ctx(store: _SocialNarrativeStore) -> PluginContext:
    return cast(PluginContext, SimpleNamespace(social_narrative_store=store))


async def test_group_recall_invalidates_matching_social_narrative_evidence() -> None:
    store = _SocialNarrativeStore()
    event = GroupRecallNoticeEvent(
        time=1,
        self_id=42,
        post_type="notice",
        notice_type="group_recall",
        user_id=10001,
        group_id=123456,
        operator_id=10002,
        message_id=9988,
    )

    await _recall_invalidator()(_ctx(store), event)

    assert store.invalidations == [("123456", 9988)]


async def test_non_recall_notice_is_exact_noop() -> None:
    store = _SocialNarrativeStore()
    event = NoticeEvent.model_validate({
        "time": 1,
        "self_id": 42,
        "post_type": "notice",
        "notice_type": "message_reactions_updated",
        "group_id": 123456,
        "message_id": 9988,
    })

    await _recall_invalidator()(_ctx(store), event)

    assert store.invalidations == []
