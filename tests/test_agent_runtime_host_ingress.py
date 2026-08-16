"""Host-trigger ingress contracts for Agent Runtime v2 production activation."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from kernel.config import GroupConfig
from kernel.types import TriggerContext
from services.agent_runtime.invocation_store import TrustedInvocationStoreV1
from services.llm.arbiter import PendingMessage
from services.memory.timeline import GroupTimeline
from services.scheduler import GroupChatScheduler, _GroupSlot
from services.tools.registry import ToolRegistry


def _api() -> Any:
    module_name = "services.agent_runtime.host_ingress"
    if importlib.util.find_spec(module_name) is None:
        pytest.fail("AuthoritativeHostTriggerIngressV1 is required for production activation")
    module = importlib.import_module(module_name)
    ingress_type = getattr(module, "AuthoritativeHostTriggerIngressV1", None)
    assert isinstance(ingress_type, type), "AuthoritativeHostTriggerIngressV1 is required"
    return ingress_type


@pytest.mark.asyncio
async def test_host_ingress_persists_exact_group_and_private_onebot_identity(
    tmp_path: Path,
) -> None:
    ingress_type = _api()
    store = TrustedInvocationStoreV1(tmp_path / "trusted-invocations.db")
    registry = ToolRegistry()
    await store.init()
    try:
        ingress = ingress_type(
            invocations=store,
            registry=registry,
            granted_scopes=("runtime:read",),
            allowed_target_refs=("network:web-search",),
        )

        group_receipt = await ingress.record_onebot_message(
            group_id="20002",
            user_id="10001",
            message_id="30003",
        )
        private_receipt = await ingress.record_onebot_message(
            group_id=None,
            user_id="10001",
            message_id="30004",
        )

        generation, _catalog = registry.snapshot_catalog()
        group = await store.get(group_receipt.invocation_id)
        private = await store.get(private_receipt.invocation_id)
        assert group is not None
        assert private is not None
        assert group_receipt == group
        assert private_receipt == private
        assert group.trigger_ref == "onebot:group:20002:message:30003"
        assert group.principal_id == "10001"
        assert group.session_id == "group_20002"
        assert group.registry_generation == generation
        assert group.granted_scopes == ("runtime:read",)
        assert group.allowed_target_refs == ("network:web-search",)
        assert private.trigger_ref == "onebot:user:10001:message:30004"
        assert private.group_id == ""
        assert private.session_id == "private_10001"
    finally:
        await store.close()


class _Identity:
    proactive = "积极参与群聊"


class _PersonaRuntime:
    def identity_snapshot(self) -> _Identity:
        return _Identity()


class _RecordingLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        return None


class _GatedLLM(_RecordingLLM):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.second_started = asyncio.Event()

    async def chat(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            self.first_started.set()
            await self.release_first.wait()
        else:
            self.second_started.set()
        return None


def _scheduler(llm: _RecordingLLM) -> GroupChatScheduler:
    return GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_PersonaRuntime(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0, batch_size=100),
    )


@pytest.mark.asyncio
async def test_scheduler_forwards_runtime_invocation_id_for_normal_and_merged_fires() -> None:
    llm = _RecordingLLM()
    scheduler = _scheduler(llm)
    try:
        scheduler.notify("20002", runtime_invocation_id="inv_normal_123")
        await asyncio.sleep(0.1)

        assert llm.calls[0]["runtime_invocation_id"] == "inv_normal_123"

        merged = TriggerContext(
            reason="有人@了你",
            mode="at_mention",
            extra={"runtime_invocation_id": "inv_merged_456"},
        )
        scheduler._slots.setdefault("20003", _GroupSlot())
        scheduler._fire("20003", block_trigger=merged)
        await asyncio.sleep(0.1)

        assert llm.calls[1]["runtime_invocation_id"] == "inv_merged_456"
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_scheduler_never_reuses_a_skipped_runtime_invocation_id() -> None:
    llm = _RecordingLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_PersonaRuntime(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=0.0, planner_smooth=0, batch_size=100),
    )
    try:
        scheduler.notify("20002", runtime_invocation_id="inv_skipped_123")
        await asyncio.sleep(0.1)
        assert llm.calls == []

        scheduler._fire("20002")
        await asyncio.sleep(0.1)
        assert llm.calls[0]["runtime_invocation_id"] is None
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_scheduler_block_fire_never_leaks_its_prior_scalar_invocation_id() -> None:
    llm = _RecordingLLM()
    scheduler = _scheduler(llm)
    try:
        slot = scheduler._slots.setdefault("20002", _GroupSlot())
        slot.runtime_invocation_id = "inv_stale_123"
        scheduler._fire(
            "20002",
            block_trigger=TriggerContext(
                reason="arbiter block",
                mode="at_mention",
                extra={"runtime_invocation_id": "inv_current_456"},
            ),
        )
        await asyncio.sleep(0.1)
        scheduler._fire("20002")
        await asyncio.sleep(0.1)

        assert [call["runtime_invocation_id"] for call in llm.calls] == [
            "inv_current_456",
            None,
        ]
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_scheduler_pending_latest_missing_invocation_never_reuses_prior_id() -> None:
    llm = _GatedLLM()
    scheduler = _scheduler(llm)
    try:
        scheduler.notify("20002", runtime_invocation_id="inv_inflight_000")
        await asyncio.wait_for(llm.first_started.wait(), timeout=1.0)

        scheduler.notify(
            "20002",
            trigger=TriggerContext(reason="补充一条", mode="directed_followup"),
            runtime_invocation_id="inv_prior_123",
        )
        scheduler.notify(
            "20002",
            trigger=TriggerContext(reason="最后一条无可信 ID", mode="directed_followup"),
            runtime_invocation_id=None,
        )
        assert [message.runtime_invocation_id for message in scheduler._slots["20002"].pending_during_generation] == [
            "inv_prior_123",
            None,
        ]

        llm.release_first.set()
        await asyncio.wait_for(llm.second_started.wait(), timeout=1.0)

        assert [call["runtime_invocation_id"] for call in llm.calls] == [
            "inv_inflight_000",
            None,
        ]
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_scheduler_arbiter_merge_keeps_each_block_anchor_invocation_id() -> None:
    scheduler = _scheduler(_RecordingLLM())
    try:
        slot = scheduler._slots.setdefault("20002", _GroupSlot())
        slot.trigger = TriggerContext(reason="有人@了你", mode="at_mention")
        triggers = scheduler._build_block_triggers(
            "20002",
            [
                PendingMessage(
                    content="第一块",
                    user_id="10001",
                    timestamp=1.0,
                    target_message_id=30001,
                    block_id="block-a",
                    runtime_invocation_id="inv_block_a",
                ),
                PendingMessage(
                    content="第二块",
                    user_id="10002",
                    timestamp=2.0,
                    target_message_id=30002,
                    block_id="block-b",
                    runtime_invocation_id="inv_block_b",
                ),
            ],
        )

        assert [item.extra["runtime_invocation_id"] for item in triggers] == [
            "inv_block_a",
            "inv_block_b",
        ]
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_scheduler_arbiter_merge_never_borrows_prior_id_for_untrusted_final_host() -> None:
    """The last merged @ is the host boundary for Runtime authority."""
    scheduler = _scheduler(_RecordingLLM())
    try:
        slot = scheduler._slots.setdefault("20002", _GroupSlot())
        slot.trigger = TriggerContext(reason="有人@了你", mode="at_mention")
        triggers = scheduler._build_block_triggers(
            "20002",
            [
                PendingMessage(
                    content="早一条可信 @",
                    user_id="10001",
                    timestamp=1.0,
                    target_message_id=30001,
                    block_id="block-a",
                    runtime_invocation_id="inv_prior_123",
                ),
                PendingMessage(
                    content="最后一条无可信 ID",
                    user_id="10002",
                    timestamp=2.0,
                    target_message_id=30002,
                    block_id="block-a",
                    runtime_invocation_id=None,
                ),
            ],
        )

        assert len(triggers) == 1
        assert "runtime_invocation_id" not in triggers[0].extra
    finally:
        await scheduler.close()
