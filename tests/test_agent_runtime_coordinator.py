"""Behavior contracts for the dark Agent Runtime coordinator."""

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.agent_runtime.coordinator as coordinator_module
import services.tools.http_api as http_api_module
import services.tools.web_fetch as web_fetch_module
import services.tools.web_search as web_search_module
from kernel.config import GroupAccessConfig
from kernel.types import (
    Tool,
    ToolApproval,
    ToolConcurrency,
    ToolContext,
    ToolEffect,
    ToolRetryPolicy,
    ToolSpec,
)
from services.agent_runtime.executor import (
    EffectExecutor,
    ExecutionConcurrencyGate,
    TrustedToolContext,
)
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.policy import ApprovalGrant, PolicyGate, RuntimePrincipal, canonical_args_digest
from services.group.outbound_access_guard import OutboundGroupAccessGuard
from services.media.sticker_store import StickerStore
from services.tools.group_admin import MuteUserTool, SendGroupMsgTool
from services.tools.http_api import HttpApiTool
from services.tools.interaction_tools import QQInteractionTool
from services.tools.registry import ToolRegistry
from services.tools.safe_http import PublicHttpTextResponse
from services.tools.sticker_tools import SendStickerTool
from services.tools.web_fetch import WebFetchTool
from services.tools.web_search import WebSearchTool


class _ExternalTool(Tool):
    def __init__(self) -> None:
        self.executed = False

    @property
    def name(self) -> str:
        return "external_publish"

    @property
    def description(self) -> str:
        return "external publish"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            owner="test",
            description=self.description,
            input_schema=self.parameters,
            effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
            required_scopes=("external:write",),
        )

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        return "published"


class _ReadTool(_ExternalTool):
    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            owner="test",
            description=self.description,
            input_schema=self.parameters,
            effect=ToolEffect.READ,
        )


class _SlowReadTool(_ReadTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            owner="test",
            description=self.description,
            input_schema=self.parameters,
            effect=ToolEffect.READ,
            retry_policy=ToolRetryPolicy.SAFE_TRANSIENT,
            timeout_ms=1,
        )

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        await asyncio.sleep(0.03)
        return "late"


class _FailingExternalTool(_ExternalTool):
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        raise RuntimeError("provider-secret")


class _FlakyReadTool(_ReadTool):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.execute_count = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            owner="test",
            description=self.description,
            input_schema=self.parameters,
            effect=ToolEffect.READ,
            retry_policy=ToolRetryPolicy.SAFE_TRANSIENT,
        )

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        self.execute_count += 1
        if self.execute_count == 1:
            raise TimeoutError("transient timeout")
        return "recovered"


class _UnsafeKeyedTool(_ReadTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            owner="test",
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {"resource_id": {"type": "string"}},
                "required": ["resource_id"],
                "additionalProperties": False,
            },
            effect=ToolEffect.READ,
            concurrency=ToolConcurrency.KEYED_SERIAL,
            concurrency_key_template="resource:{resource_id.__class__}",
        )


class _ObservedLock(asyncio.Lock):
    def __init__(self) -> None:
        super().__init__()
        self.acquire_attempted = asyncio.Event()

    async def acquire(self) -> Literal[True]:
        self.acquire_attempted.set()
        await super().acquire()
        return True


class _ObservedConcurrencyGate(ExecutionConcurrencyGate):
    def __init__(self) -> None:
        self.lock = _ObservedLock()

    def lock_for(
        self,
        concurrency: ToolConcurrency,
        concurrency_key: str,
    ) -> asyncio.Lock | None:
        return self.lock


async def test_coordinator_maps_external_approval_wait_into_run_state(
    tmp_path,
) -> None:
    coordinator_type = getattr(coordinator_module, "RunCoordinator", None)
    assert coordinator_type is not None
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    tool = _ExternalTool()
    registry = ToolRegistry()
    registry.register(tool)
    executor = EffectExecutor(ledger=ledger, policy_gate=PolicyGate())
    coordinator = coordinator_type(
        ledger=ledger,
        registry=registry,
        executor=executor,
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-coordinator",
        granted_scopes=("external:write",),
        allowed_target_refs=("external:user-coordinator",),
    )

    started = await coordinator.start_run(
        run_id="run-coordinator",
        trigger_type="message",
        trigger_ref="qq:message:coordinator",
        principal=principal,
        session_id="group:1",
        group_id="1",
    )
    execution = await coordinator.execute_tool(
        run_id=started.run_id,
        call_id="call-coordinator",
        step_id="step-coordinator",
        tool_name=tool.name,
        principal=principal,
        arguments={"text": "hello"},
        target_ref="external:user-coordinator",
        trusted_context=TrustedToolContext(
            user_id="user-coordinator",
            group_id="1",
            session_id="group:1",
        ),
        worker_id="worker-coordinator",
    )
    run = await ledger.get_run(started.run_id)
    call = await ledger.get_tool_call("call-coordinator")
    await ledger.close()

    assert started.status == "running"
    assert started.registry_generation == 1
    assert execution.decision.outcome == "require_approval"
    assert execution.result is None
    assert tool.executed is False
    assert call is not None and call.status == "approval_pending"
    assert run is not None and run.status == "waiting_approval"


async def test_coordinator_resumes_same_call_after_bound_approval(tmp_path) -> None:
    resume_approved = getattr(
        coordinator_module.RunCoordinator,
        "resume_approved_tool",
        None,
    )
    assert callable(resume_approved)
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    tool = _ExternalTool()
    registry = ToolRegistry()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-resume",
        granted_scopes=("external:write",),
        allowed_target_refs=("external:user-resume",),
    )
    arguments = {"text": "approved text"}
    await coordinator.start_run(
        run_id="run-resume",
        trigger_type="message",
        trigger_ref="qq:message:resume",
        principal=principal,
    )
    await coordinator.execute_tool(
        run_id="run-resume",
        call_id="call-resume",
        step_id="step-resume",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        target_ref="external:user-resume",
        trusted_context=TrustedToolContext(user_id="user-resume"),
        worker_id="worker-resume",
    )
    approval = ApprovalGrant(
        approval_ref_digest="sha256:resume-approval",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=tool.spec.name,
        tool_version=tool.spec.version,
        effect=tool.spec.effect,
        args_digest=canonical_args_digest(arguments),
        target_ref="external:user-resume",
        expires_at="2099-01-01T00:00:00+00:00",
    )

    execution = await coordinator.resume_approved_tool(
        run_id="run-resume",
        call_id="call-resume",
        principal=principal,
        arguments=arguments,
        approval=approval,
        trusted_context=TrustedToolContext(
            user_id="user-resume",
            approval_ref="approval:runtime-secret",
        ),
        worker_id="worker-resume",
        approval_actor="approver",
    )
    run = await ledger.get_run("run-resume")
    call = await ledger.get_tool_call("call-resume")
    events = await ledger.list_events(run_id="run-resume")
    await ledger.close()

    assert execution.decision.outcome == "allow"
    assert execution.result is not None
    assert execution.result.status == "succeeded"
    assert tool.executed is True
    assert run is not None and run.status == "running"
    assert call is not None and call.status == "succeeded"
    assert call.approval_ref_digest == "sha256:resume-approval"
    assert [event.event_type for event in events].count("approval_granted") == 1


async def test_coordinator_governs_onebot_approval_and_unknown_outcome(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = MuteUserTool({"admin"})
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = "onebot:group:20002:member:30003:mute"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="admin-principal",
        granted_scopes=("onebot:group:moderate",),
        allowed_target_refs=(target_ref,),
    )
    arguments = {"user_id": "30003", "duration": 60}
    bot = AsyncMock()
    await coordinator.start_run(
        run_id="run-onebot-approval",
        trigger_type="message",
        trigger_ref="qq:message:onebot-approval",
        principal=principal,
        group_id="20002",
    )

    waiting = await coordinator.execute_tool(
        run_id="run-onebot-approval",
        call_id="call-onebot-approval",
        step_id="step-onebot-approval",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        trusted_context=TrustedToolContext(
            bot=bot,
            user_id="admin",
            group_id="20002",
        ),
        worker_id="worker-onebot-approval",
    )
    bot.set_group_ban.assert_not_awaited()
    approval = ApprovalGrant(
        approval_ref_digest="sha256:onebot-approval",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=tool.spec.name,
        tool_version=tool.spec.version,
        effect=tool.spec.effect,
        args_digest=canonical_args_digest(arguments),
        target_ref=target_ref,
        expires_at="2099-01-01T00:00:00+00:00",
    )

    succeeded = await coordinator.resume_approved_tool(
        run_id="run-onebot-approval",
        call_id="call-onebot-approval",
        principal=principal,
        arguments=arguments,
        approval=approval,
        trusted_context=TrustedToolContext(
            bot=bot,
            user_id="admin",
            group_id="20002",
        ),
        worker_id="worker-onebot-approval-2",
        approval_actor="approver",
    )
    bot.set_group_ban.assert_awaited_once_with(
        group_id=20002,
        user_id=30003,
        duration=60,
    )

    failing_bot = AsyncMock()
    failing_bot.set_group_ban.side_effect = RuntimeError("provider outcome unknown")
    await coordinator.start_run(
        run_id="run-onebot-unknown",
        trigger_type="message",
        trigger_ref="qq:message:onebot-unknown",
        principal=principal,
        group_id="20002",
    )
    unknown = await coordinator.execute_tool(
        run_id="run-onebot-unknown",
        call_id="call-onebot-unknown",
        step_id="step-onebot-unknown",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        approval=ApprovalGrant(
            approval_ref_digest="sha256:onebot-unknown",
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            tool_name=tool.spec.name,
            tool_version=tool.spec.version,
            effect=tool.spec.effect,
            args_digest=canonical_args_digest(arguments),
            target_ref=target_ref,
            expires_at="2099-01-01T00:00:00+00:00",
        ),
        trusted_context=TrustedToolContext(
            bot=failing_bot,
            user_id="admin",
            group_id="20002",
        ),
        worker_id="worker-onebot-unknown",
    )
    unknown_run = await ledger.get_run("run-onebot-unknown")
    unknown_call = await ledger.get_tool_call("call-onebot-unknown")
    await ledger.close()

    assert waiting.decision.outcome == "require_approval"
    assert succeeded.result is not None and succeeded.result.status == "succeeded"
    assert unknown.result is not None and unknown.result.status == "unknown"
    assert unknown_run is not None and unknown_run.status == "waiting_external"
    assert unknown_call is not None and unknown_call.status == "unknown"


async def test_coordinator_governs_reaction_approval_and_live_group_policy(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = QQInteractionTool("reaction")
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = "onebot:group:100:message:9001:reaction:66"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="reaction-principal",
        granted_scopes=("onebot:interaction:reaction",),
        allowed_target_refs=(target_ref,),
    )
    arguments = {"message_id": "9001", "emoji_code": "66"}

    allowed_call_api = AsyncMock(return_value={"status": "ok"})
    allowed_bot = SimpleNamespace(self_id="bot", call_api=allowed_call_api)
    OutboundGroupAccessGuard(
        GroupAccessConfig(mode="whitelist", whitelist=[100])
    ).wrap_bot(allowed_bot)
    trusted_allowed = TrustedToolContext(
        bot=allowed_bot,
        user_id="200",
        group_id="100",
        extra={
            "humanization_profile": "performance",
            "onebot_message_refs": ["onebot:group:100:message:9001"],
        },
    )
    await coordinator.start_run(
        run_id="run-reaction-approval",
        trigger_type="message",
        trigger_ref="onebot:group:100:message:9001",
        principal=principal,
        group_id="100",
    )
    waiting = await coordinator.execute_tool(
        run_id="run-reaction-approval",
        call_id="call-reaction-approval",
        step_id="step-reaction-approval",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        trusted_context=trusted_allowed,
        worker_id="worker-reaction-approval",
    )
    allowed_call_api.assert_not_awaited()
    succeeded = await coordinator.resume_approved_tool(
        run_id="run-reaction-approval",
        call_id="call-reaction-approval",
        principal=principal,
        arguments=arguments,
        approval=ApprovalGrant(
            approval_ref_digest="sha256:reaction-approval",
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            tool_name=tool.spec.name,
            tool_version=tool.spec.version,
            effect=tool.spec.effect,
            args_digest=canonical_args_digest(arguments),
            target_ref=target_ref,
            expires_at="2099-01-01T00:00:00+00:00",
        ),
        trusted_context=trusted_allowed,
        worker_id="worker-reaction-approved",
        approval_actor="approver",
    )
    allowed_call_api.assert_awaited_once_with(
        "set_msg_emoji_like",
        message_id=9001,
        emoji_id="66",
    )

    blocked_call_api = AsyncMock(return_value={"status": "unexpected"})
    blocked_bot = SimpleNamespace(self_id="bot", call_api=blocked_call_api)
    OutboundGroupAccessGuard(
        GroupAccessConfig(mode="whitelist", whitelist=[999])
    ).wrap_bot(blocked_bot)
    await coordinator.start_run(
        run_id="run-reaction-blocked",
        trigger_type="message",
        trigger_ref="onebot:group:100:message:9001",
        principal=principal,
        group_id="100",
    )
    blocked = await coordinator.execute_tool(
        run_id="run-reaction-blocked",
        call_id="call-reaction-blocked",
        step_id="step-reaction-blocked",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        approval=ApprovalGrant(
            approval_ref_digest="sha256:reaction-blocked",
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            tool_name=tool.spec.name,
            tool_version=tool.spec.version,
            effect=tool.spec.effect,
            args_digest=canonical_args_digest(arguments),
            target_ref=target_ref,
            expires_at="2099-01-01T00:00:00+00:00",
        ),
        trusted_context=TrustedToolContext(
            bot=blocked_bot,
            user_id="200",
            group_id="100",
            extra=trusted_allowed.extra,
        ),
        worker_id="worker-reaction-blocked",
    )

    failing_call_api = AsyncMock(side_effect=RuntimeError("provider unknown"))
    failing_bot = SimpleNamespace(self_id="bot", call_api=failing_call_api)
    OutboundGroupAccessGuard(
        GroupAccessConfig(mode="whitelist", whitelist=[100])
    ).wrap_bot(failing_bot)
    await coordinator.start_run(
        run_id="run-reaction-unknown",
        trigger_type="message",
        trigger_ref="onebot:group:100:message:9001",
        principal=principal,
        group_id="100",
    )
    unknown = await coordinator.execute_tool(
        run_id="run-reaction-unknown",
        call_id="call-reaction-unknown",
        step_id="step-reaction-unknown",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        approval=ApprovalGrant(
            approval_ref_digest="sha256:reaction-unknown",
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            tool_name=tool.spec.name,
            tool_version=tool.spec.version,
            effect=tool.spec.effect,
            args_digest=canonical_args_digest(arguments),
            target_ref=target_ref,
            expires_at="2099-01-01T00:00:00+00:00",
        ),
        trusted_context=TrustedToolContext(
            bot=failing_bot,
            user_id="200",
            group_id="100",
            extra=trusted_allowed.extra,
        ),
        worker_id="worker-reaction-unknown",
    )
    blocked_call = await ledger.get_tool_call("call-reaction-blocked")
    unknown_call = await ledger.get_tool_call("call-reaction-unknown")
    unknown_run = await ledger.get_run("run-reaction-unknown")
    await ledger.close()

    assert waiting.decision.outcome == "require_approval"
    assert succeeded.result is not None
    assert succeeded.result.status == "succeeded"
    assert blocked.result is not None
    assert blocked.result.status == "failed_terminal"
    assert blocked_call is not None and blocked_call.status == "failed_terminal"
    blocked_call_api.assert_not_awaited()
    assert unknown.result is not None and unknown.result.status == "unknown"
    assert unknown_call is not None and unknown_call.status == "unknown"
    assert unknown_run is not None and unknown_run.status == "waiting_external"
    failing_call_api.assert_awaited_once_with(
        "set_msg_emoji_like",
        message_id=9001,
        emoji_id="66",
    )


async def test_coordinator_projects_group_policy_refusal_as_terminal_not_unknown(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = SendGroupMsgTool({"admin"})
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    original_call_api = AsyncMock(return_value={"status": "unexpected"})
    bot = SimpleNamespace(self_id="bot", call_api=original_call_api)
    OutboundGroupAccessGuard(
        GroupAccessConfig(mode="whitelist", whitelist=[100])
    ).wrap_bot(bot)

    async def send_group_msg(**kwargs: Any) -> Any:
        return await bot.call_api("send_group_msg", **kwargs)

    bot.send_group_msg = send_group_msg
    target_ref = "onebot:group:200:message"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="admin",
        granted_scopes=("onebot:group:message",),
        allowed_target_refs=(target_ref,),
    )
    arguments = {"group_id": "200", "message": "blocked before provider"}
    approval = ApprovalGrant(
        approval_ref_digest="sha256:group-policy-refusal",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=tool.spec.name,
        tool_version=tool.spec.version,
        effect=tool.spec.effect,
        args_digest=canonical_args_digest(arguments),
        target_ref=target_ref,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    await coordinator.start_run(
        run_id="run-group-policy-refusal",
        trigger_type="message",
        trigger_ref="onebot:group:100:message:9001",
        principal=principal,
        group_id="100",
    )

    execution = await coordinator.execute_tool(
        run_id="run-group-policy-refusal",
        call_id="call-group-policy-refusal",
        step_id="step-group-policy-refusal",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        approval=approval,
        trusted_context=TrustedToolContext(
            bot=bot,
            user_id="admin",
            group_id="100",
        ),
        worker_id="worker-group-policy-refusal",
    )
    call = await ledger.get_tool_call("call-group-policy-refusal")
    run = await ledger.get_run("run-group-policy-refusal")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_terminal"
    assert execution.result.error is not None
    assert execution.result.error.code == "group_policy_denied"
    assert call is not None and call.status == "failed_terminal"
    assert run is not None and run.status == "running"
    original_call_api.assert_not_awaited()


async def test_coordinator_rejects_untrusted_reaction_before_tool_call(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = QQInteractionTool("reaction")
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="reaction-principal",
        granted_scopes=("onebot:interaction:reaction",),
        allowed_target_refs=(
            "onebot:group:100:message:9001:reaction:66",
        ),
    )
    original_call_api = AsyncMock()
    bot = SimpleNamespace(self_id="bot", call_api=original_call_api)
    OutboundGroupAccessGuard(
        GroupAccessConfig(mode="whitelist", whitelist=[100])
    ).wrap_bot(bot)
    await coordinator.start_run(
        run_id="run-reaction-untrusted",
        trigger_type="message",
        trigger_ref="onebot:group:100:message:9002",
        principal=principal,
        group_id="100",
    )

    with pytest.raises(ValueError, match="not trusted"):
        await coordinator.execute_tool(
            run_id="run-reaction-untrusted",
            call_id="call-reaction-untrusted",
            step_id="step-reaction-untrusted",
            tool_name=tool.name,
            principal=principal,
            arguments={"message_id": "9001", "emoji_code": "66"},
            trusted_context=TrustedToolContext(
                bot=bot,
                user_id="200",
                group_id="100",
                extra={
                    "humanization_profile": "performance",
                    "onebot_message_refs": [
                        "onebot:group:100:message:9002"
                    ],
                },
            ),
            worker_id="worker-reaction-untrusted",
        )
    call = await ledger.get_tool_call("call-reaction-untrusted")
    await ledger.close()

    assert call is None
    original_call_api.assert_not_awaited()


async def test_coordinator_sticker_resume_uses_durable_resolved_id(
    tmp_path,
) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    first_id, _ = store.add(
        b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"first",
        "挥手告别",
        "适合说再见",
    )
    second_id, _ = store.add(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + b"second",
        "生气皱眉",
        "表达不满",
    )
    assert store.search_by_intent("告别", top_k=1) == [first_id]

    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = SendStickerTool(store)
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = f"onebot:group:100:sticker:{first_id}:send"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="sticker-principal",
        granted_scopes=("onebot:sticker:send",),
        allowed_target_refs=(target_ref,),
    )
    bot = MagicMock()
    bot.send_group_msg = AsyncMock(return_value=None)
    bot.send_private_msg = AsyncMock(return_value=None)
    arguments = {"intent": "告别"}
    trusted_context = TrustedToolContext(
        bot=bot,
        user_id="200",
        group_id="100",
        session_id="group_100",
    )
    await coordinator.start_run(
        run_id="run-sticker-durable",
        trigger_type="message",
        trigger_ref="onebot:group:100:message:9001",
        principal=principal,
        group_id="100",
    )
    waiting = await coordinator.execute_tool(
        run_id="run-sticker-durable",
        call_id="call-sticker-durable",
        step_id="step-sticker-durable",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        trusted_context=trusted_context,
        worker_id="worker-sticker-durable",
    )
    call_before = await ledger.get_tool_call("call-sticker-durable")
    assert call_before is not None and call_before.target_ref == target_ref
    bot.send_group_msg.assert_not_awaited()

    assert store.update(
        first_id,
        description="无关内容",
        usage_hint="无关场景",
    )
    assert store.update(
        second_id,
        description="挥手告别",
        usage_hint="适合说再见",
    )
    assert store.search_by_intent("告别", top_k=1) == [second_id]

    fake_segment = MagicMock()
    with patch(
        "nonebot.adapters.onebot.v11.MessageSegment.image",
        return_value=fake_segment,
    ):
        succeeded = await coordinator.resume_approved_tool(
            run_id="run-sticker-durable",
            call_id="call-sticker-durable",
            principal=principal,
            arguments=arguments,
            approval=ApprovalGrant(
                approval_ref_digest="sha256:sticker-durable",
                principal_kind=principal.kind,
                principal_id=principal.principal_id,
                tool_name=tool.spec.name,
                tool_version=tool.spec.version,
                effect=tool.spec.effect,
                args_digest=canonical_args_digest(arguments),
                target_ref=target_ref,
                expires_at="2099-01-01T00:00:00+00:00",
            ),
            trusted_context=trusted_context,
            worker_id="worker-sticker-approved",
            approval_actor="approver",
        )
    call_after = await ledger.get_tool_call("call-sticker-durable")
    await ledger.close()
    store.close()

    assert waiting.decision.outcome == "require_approval"
    assert succeeded.result is not None
    assert succeeded.result.status == "succeeded"
    assert succeeded.result.structured_output == f"已发送 {first_id}"
    assert call_after is not None and call_after.target_ref == target_ref
    bot.send_group_msg.assert_awaited_once()


async def test_coordinator_sticker_provider_failure_and_cancel_are_unknown(
    tmp_path,
) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sticker_id, _ = store.add(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + b"sticker",
        "开心",
        "开心时使用",
    )
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = SendStickerTool(store)
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = f"onebot:group:100:sticker:{sticker_id}:send"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="sticker-principal",
        granted_scopes=("onebot:sticker:send",),
        allowed_target_refs=(target_ref,),
    )
    arguments = {"sticker_id": sticker_id}

    def approval(digest: str) -> ApprovalGrant:
        return ApprovalGrant(
            approval_ref_digest=digest,
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            tool_name=tool.spec.name,
            tool_version=tool.spec.version,
            effect=tool.spec.effect,
            args_digest=canonical_args_digest(arguments),
            target_ref=target_ref,
            expires_at="2099-01-01T00:00:00+00:00",
        )

    failing_bot = MagicMock()
    failing_bot.send_group_msg = AsyncMock(
        side_effect=RuntimeError("provider outcome unknown")
    )
    failing_context = TrustedToolContext(
        bot=failing_bot,
        user_id="200",
        group_id="100",
    )
    await coordinator.start_run(
        run_id="run-sticker-provider-failure",
        trigger_type="message",
        trigger_ref="onebot:group:100:message:9001",
        principal=principal,
        group_id="100",
    )
    with patch(
        "nonebot.adapters.onebot.v11.MessageSegment.image",
        return_value=MagicMock(),
    ):
        failed = await coordinator.execute_tool(
            run_id="run-sticker-provider-failure",
            call_id="call-sticker-provider-failure",
            step_id="step-sticker-provider-failure",
            tool_name=tool.name,
            principal=principal,
            arguments=arguments,
            approval=approval("sha256:sticker-provider-failure"),
            trusted_context=failing_context,
            worker_id="worker-sticker-provider-failure",
        )

    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def blocking_send(**_kwargs: Any) -> None:
        send_started.set()
        await release_send.wait()

    blocking_bot = MagicMock()
    blocking_bot.send_group_msg = AsyncMock(side_effect=blocking_send)
    await coordinator.start_run(
        run_id="run-sticker-cancel",
        trigger_type="message",
        trigger_ref="onebot:group:100:message:9002",
        principal=principal,
        group_id="100",
    )
    with patch(
        "nonebot.adapters.onebot.v11.MessageSegment.image",
        return_value=MagicMock(),
    ):
        task = asyncio.create_task(
            coordinator.execute_tool(
                run_id="run-sticker-cancel",
                call_id="call-sticker-cancel",
                step_id="step-sticker-cancel",
                tool_name=tool.name,
                principal=principal,
                arguments=arguments,
                approval=approval("sha256:sticker-cancel"),
                trusted_context=TrustedToolContext(
                    bot=blocking_bot,
                    user_id="200",
                    group_id="100",
                ),
                worker_id="worker-sticker-cancel",
            )
        )
        await send_started.wait()
        task.cancel()
        task.cancel()
        release_send.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    failed_call = await ledger.get_tool_call(
        "call-sticker-provider-failure"
    )
    failed_run = await ledger.get_run("run-sticker-provider-failure")
    cancelled_call = await ledger.get_tool_call("call-sticker-cancel")
    cancelled_run = await ledger.get_run("run-sticker-cancel")
    sticker = store.get(sticker_id)
    await ledger.close()
    store.close()

    assert failed.result is not None and failed.result.status == "unknown"
    assert failed_call is not None and failed_call.status == "unknown"
    assert failed_run is not None and failed_run.status == "waiting_external"
    assert cancelled_call is not None and cancelled_call.status == "unknown"
    assert cancelled_run is not None
    assert cancelled_run.status == "waiting_external"
    assert sticker is not None and sticker["send_count"] == 0


async def test_coordinator_rechecks_live_registry_generation_after_wait(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _ReadTool("read_before_registry_change")
    registry.register(tool)
    gate = _ObservedConcurrencyGate()
    await gate.lock.acquire()
    gate.lock.acquire_attempted.clear()
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(
            ledger=ledger,
            policy_gate=PolicyGate(),
            concurrency_gate=gate,
        ),
    )
    principal = RuntimePrincipal(kind="service", principal_id="runtime")
    await coordinator.start_run(
        run_id="run-registry-change",
        trigger_type="recovery",
        trigger_ref="runtime:registry-change",
        principal=principal,
    )
    task = asyncio.create_task(
        coordinator.execute_tool(
            run_id="run-registry-change",
            call_id="call-registry-change",
            step_id="step-registry-change",
            tool_name=tool.name,
            principal=principal,
            arguments={"text": "hello"},
            trusted_context=TrustedToolContext(user_id="runtime"),
            worker_id="worker-registry-change",
        )
    )
    await gate.lock.acquire_attempted.wait()
    registry.register(_ReadTool("read_added_during_wait"))
    gate.lock.release()

    execution = await task
    call = await ledger.get_tool_call("call-registry-change")
    await ledger.close()

    assert execution.decision.outcome == "deny"
    assert execution.decision.reason == "registry_generation_mismatch"
    assert execution.result is None
    assert tool.executed is False
    assert call is not None and call.status == "denied"


async def test_coordinator_cancellation_before_dispatch_cancels_run_and_call(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _ReadTool("read_cancelled_before_dispatch")
    registry.register(tool)
    gate = _ObservedConcurrencyGate()
    await gate.lock.acquire()
    gate.lock.acquire_attempted.clear()
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(
            ledger=ledger,
            policy_gate=PolicyGate(),
            concurrency_gate=gate,
        ),
    )
    principal = RuntimePrincipal(kind="service", principal_id="runtime")
    await coordinator.start_run(
        run_id="run-cancelled-before-dispatch",
        trigger_type="recovery",
        trigger_ref="runtime:cancelled-before-dispatch",
        principal=principal,
    )
    task = asyncio.create_task(
        coordinator.execute_tool(
            run_id="run-cancelled-before-dispatch",
            call_id="call-cancelled-before-dispatch",
            step_id="step-cancelled-before-dispatch",
            tool_name=tool.name,
            principal=principal,
            arguments={"text": "hello"},
            trusted_context=TrustedToolContext(user_id="runtime"),
            worker_id="worker-cancelled-before-dispatch",
        )
    )
    await gate.lock.acquire_attempted.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.lock.release()

    call = await ledger.get_tool_call("call-cancelled-before-dispatch")
    run = await ledger.get_run("run-cancelled-before-dispatch")
    await ledger.close()

    assert tool.executed is False
    assert call is not None and call.status == "cancelled"
    assert run is not None and run.status == "cancelled"


async def test_coordinator_maps_retryable_timeout_to_waiting_retry(tmp_path) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _SlowReadTool("slow_read")
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(kind="service", principal_id="runtime")
    await coordinator.start_run(
        run_id="run-waiting-retry",
        trigger_type="recovery",
        trigger_ref="runtime:waiting-retry",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-waiting-retry",
        call_id="call-waiting-retry",
        step_id="step-waiting-retry",
        tool_name=tool.name,
        principal=principal,
        arguments={"text": "hello"},
        trusted_context=TrustedToolContext(user_id="runtime"),
        worker_id="worker-waiting-retry",
    )
    run = await ledger.get_run("run-waiting-retry")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_retryable"
    assert run is not None and run.status == "waiting_retry"


async def test_coordinator_maps_external_unknown_to_waiting_external(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _FailingExternalTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="unknown-user",
        granted_scopes=("external:write",),
        allowed_target_refs=("external:unknown-user",),
    )
    arguments = {"text": "hello"}
    approval = ApprovalGrant(
        approval_ref_digest="sha256:unknown-approval",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=tool.spec.name,
        tool_version=tool.spec.version,
        effect=tool.spec.effect,
        args_digest=canonical_args_digest(arguments),
        target_ref="external:unknown-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    await coordinator.start_run(
        run_id="run-waiting-external",
        trigger_type="message",
        trigger_ref="qq:message:waiting-external",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-waiting-external",
        call_id="call-waiting-external",
        step_id="step-waiting-external",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        target_ref="external:unknown-user",
        approval=approval,
        trusted_context=TrustedToolContext(
            user_id="unknown-user",
            approval_ref="approval:runtime-secret",
        ),
        worker_id="worker-waiting-external",
    )
    run = await ledger.get_run("run-waiting-external")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "unknown"
    assert run is not None and run.status == "waiting_external"


class _BoundLocalWriteTool(Tool):
    def __init__(self) -> None:
        self.executed = False
        self.last_ctx: ToolContext | None = None

    @property
    def name(self) -> str:
        return "bound_local_write"

    @property
    def description(self) -> str:
        return "bound local write"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "scope_id": {"type": "string"},
            },
            "required": ["scope", "scope_id"],
            "additionalProperties": False,
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            owner="test",
            description=self.description,
            input_schema=self.parameters,
            effect=ToolEffect.WRITE_LOCAL,
            required_scopes=("memory:write",),
            concurrency=ToolConcurrency.KEYED_SERIAL,
            concurrency_key_template="",
            binding_required=True,
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> Any:
        from kernel.types import ToolInvocationBinding

        scope = str(arguments.get("scope") or "").strip()
        scope_id = str(arguments.get("scope_id") or "").strip()
        if scope != "user" or scope_id != str(ctx.user_id).strip():
            raise ValueError("bound write scope rejected")
        target = f"memory:{scope}:{scope_id}"
        return ToolInvocationBinding(target_ref=target, concurrency_key=target)

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        self.last_ctx = ctx
        return "written"


class _BoundApprovalTool(_BoundLocalWriteTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            owner="test",
            description=self.description,
            input_schema=self.parameters,
            effect=ToolEffect.WRITE_LOCAL,
            required_scopes=("memory:write",),
            approval=ToolApproval.ALWAYS,
            concurrency=ToolConcurrency.KEYED_SERIAL,
            binding_required=True,
        )


class _BoundRetryableTool(_BoundLocalWriteTool):
    def __init__(self) -> None:
        super().__init__()
        self.execute_count = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            owner="test",
            description=self.description,
            input_schema=self.parameters,
            effect=ToolEffect.WRITE_LOCAL,
            required_scopes=("memory:write",),
            retry_policy=ToolRetryPolicy.SAFE_TRANSIENT,
            concurrency=ToolConcurrency.KEYED_SERIAL,
            binding_required=True,
        )

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.execute_count += 1
        if self.execute_count == 1:
            raise TimeoutError("transient")
        return await super().execute(ctx, **kwargs)


async def test_coordinator_revalidates_binding_before_approval_resume(tmp_path) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _BoundApprovalTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="runtime-user",
        granted_scopes=("memory:write",),
        allowed_target_refs=("memory:user:10001", "memory:user:99999"),
    )
    arguments = {"scope": "user", "scope_id": "10001"}
    await coordinator.start_run(
        run_id="run-bound-approval",
        trigger_type="message",
        trigger_ref="qq:message:bound-approval",
        principal=principal,
    )
    await coordinator.execute_tool(
        run_id="run-bound-approval",
        call_id="call-bound-approval",
        step_id="step-bound-approval",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        trusted_context=TrustedToolContext(user_id="10001"),
        worker_id="worker-bound-approval-1",
    )
    approval = ApprovalGrant(
        approval_ref_digest="sha256:bound-approval",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=tool.spec.name,
        tool_version=tool.spec.version,
        effect=tool.spec.effect,
        args_digest=canonical_args_digest(arguments),
        target_ref="memory:user:10001",
        expires_at="2099-01-01T00:00:00+00:00",
    )

    with pytest.raises(ValueError, match="binding changed while waiting"):
        await coordinator.resume_approved_tool(
            run_id="run-bound-approval",
            call_id="call-bound-approval",
            principal=principal,
            arguments=arguments,
            approval=approval,
            trusted_context=TrustedToolContext(user_id="99999"),
            worker_id="worker-bound-approval-2",
            approval_actor="approver",
        )

    waiting_run = await ledger.get_run("run-bound-approval")
    waiting_call = await ledger.get_tool_call("call-bound-approval")
    assert tool.executed is False
    assert waiting_run is not None and waiting_run.status == "waiting_approval"
    assert waiting_call is not None and waiting_call.status == "approval_pending"
    assert waiting_call.approval_ref_digest == ""

    execution = await coordinator.resume_approved_tool(
        run_id="run-bound-approval",
        call_id="call-bound-approval",
        principal=principal,
        arguments=arguments,
        approval=approval,
        trusted_context=TrustedToolContext(user_id="10001"),
        worker_id="worker-bound-approval-3",
        approval_actor="approver",
    )
    final_call = await ledger.get_tool_call("call-bound-approval")
    await ledger.close()
    assert execution.result is not None and execution.result.status == "succeeded"
    assert tool.executed is True
    assert final_call is not None and final_call.status == "succeeded"


async def test_coordinator_revalidates_binding_before_retry_resume(tmp_path) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _BoundRetryableTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="runtime-user",
        granted_scopes=("memory:write",),
        allowed_target_refs=("memory:user:10001", "memory:user:99999"),
    )
    arguments = {"scope": "user", "scope_id": "10001"}
    await coordinator.start_run(
        run_id="run-bound-retry",
        trigger_type="message",
        trigger_ref="qq:message:bound-retry",
        principal=principal,
    )
    first = await coordinator.execute_tool(
        run_id="run-bound-retry",
        call_id="call-bound-retry",
        step_id="step-bound-retry",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        trusted_context=TrustedToolContext(user_id="10001"),
        worker_id="worker-bound-retry-1",
    )
    assert first.result is not None
    assert first.result.status == "failed_retryable"

    with pytest.raises(ValueError, match="binding changed while waiting"):
        await coordinator.resume_retryable_tool(
            run_id="run-bound-retry",
            call_id="call-bound-retry",
            principal=principal,
            arguments=arguments,
            trusted_context=TrustedToolContext(user_id="99999"),
            worker_id="worker-bound-retry-2",
        )

    waiting_run = await ledger.get_run("run-bound-retry")
    waiting_call = await ledger.get_tool_call("call-bound-retry")
    assert tool.execute_count == 1
    assert waiting_run is not None and waiting_run.status == "waiting_retry"
    assert waiting_call is not None and waiting_call.status == "failed_retryable"

    execution = await coordinator.resume_retryable_tool(
        run_id="run-bound-retry",
        call_id="call-bound-retry",
        principal=principal,
        arguments=arguments,
        trusted_context=TrustedToolContext(user_id="10001"),
        worker_id="worker-bound-retry-3",
    )
    final_call = await ledger.get_tool_call("call-bound-retry")
    await ledger.close()
    assert execution.result is not None and execution.result.status == "succeeded"
    assert tool.execute_count == 2
    assert final_call is not None and final_call.status == "succeeded"


async def test_coordinator_uses_tool_binding_for_durable_target_and_key(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _BoundLocalWriteTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-10001",
        granted_scopes=("memory:write",),
        allowed_target_refs=("memory:user:10001",),
    )
    bot = object()
    await coordinator.start_run(
        run_id="run-bound-write",
        trigger_type="message",
        trigger_ref="qq:message:bound-write",
        principal=principal,
        session_id="user:10001",
    )

    execution = await coordinator.execute_tool(
        run_id="run-bound-write",
        call_id="call-bound-write",
        step_id="step-bound-write",
        tool_name=tool.name,
        principal=principal,
        arguments={"scope": "user", "scope_id": "10001"},
        target_ref="",
        trusted_context=TrustedToolContext(
            bot=bot,
            user_id="10001",
            session_id="user:10001",
        ),
        worker_id="worker-bound-write",
    )
    call = await ledger.get_tool_call("call-bound-write")
    await ledger.close()

    assert execution.decision.outcome == "allow"
    assert execution.result is not None
    assert execution.result.status == "succeeded"
    assert tool.executed is True
    assert call is not None
    assert call.target_ref == "memory:user:10001"
    assert call.concurrency_key == "memory:user:10001"
    assert tool.last_ctx is not None
    assert tool.last_ctx.user_id == "10001"
    assert tool.last_ctx.principal_id == "user-10001"
    assert tool.last_ctx.bot is bot


async def test_coordinator_rejects_caller_target_mismatch_before_tool_call(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _BoundLocalWriteTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-10001",
        granted_scopes=("memory:write",),
        allowed_target_refs=(
            "memory:user:10001",
            "memory:user:spoofed",
        ),
    )
    await coordinator.start_run(
        run_id="run-bound-mismatch",
        trigger_type="message",
        trigger_ref="qq:message:bound-mismatch",
        principal=principal,
    )

    with pytest.raises(ValueError, match="target_ref does not match"):
        await coordinator.execute_tool(
            run_id="run-bound-mismatch",
            call_id="call-bound-mismatch",
            step_id="step-bound-mismatch",
            tool_name=tool.name,
            principal=principal,
            arguments={"scope": "user", "scope_id": "10001"},
            target_ref="memory:user:spoofed",
            trusted_context=TrustedToolContext(user_id="10001"),
            worker_id="worker-bound-mismatch",
        )
    call = await ledger.get_tool_call("call-bound-mismatch")
    await ledger.close()

    assert call is None
    assert tool.executed is False


async def test_coordinator_persists_canonical_web_fetch_origin(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        captured["url"] = url
        captured.update(kwargs)
        return PublicHttpTextResponse(
            status_code=200,
            text="public docs",
            final_url="https://example.com/docs",
            truncated=False,
        )

    monkeypatch.setattr(web_fetch_module, "fetch_public_text", fake_fetch)
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = WebFetchTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = "network:web-origin:https://example.com"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-web-fetch",
        granted_scopes=("network:fetch",),
        allowed_target_refs=(target_ref,),
    )
    await coordinator.start_run(
        run_id="run-web-fetch",
        trigger_type="message",
        trigger_ref="qq:message:web-fetch",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-web-fetch",
        call_id="call-web-fetch",
        step_id="step-web-fetch",
        tool_name=tool.name,
        principal=principal,
        arguments={"url": "HTTPS://Example.COM:443/docs?q=1#fragment"},
        target_ref="",
        trusted_context=TrustedToolContext(user_id="user-web-fetch"),
        worker_id="worker-web-fetch",
    )
    call = await ledger.get_tool_call("call-web-fetch")
    await ledger.close()

    assert execution.decision.outcome == "allow"
    assert execution.result is not None
    assert execution.result.status == "succeeded"
    assert execution.result.structured_output == "public docs"
    assert call is not None
    assert call.target_ref == target_ref
    assert call.concurrency_key == ""
    assert captured["url"] == "HTTPS://Example.COM:443/docs?q=1#fragment"


async def test_coordinator_persists_and_gates_http_api_get_origin(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    async def fake_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        del url, kwargs
        nonlocal executed
        executed = True
        return PublicHttpTextResponse(
            200,
            '{"ok": true}',
            "https://api.example.com/data",
            False,
        )

    monkeypatch.setattr(http_api_module, "fetch_public_text", fake_fetch, raising=False)
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = HttpApiTool(allowed_methods=["GET"])
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = "network:http-api-origin:https://api.example.com"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-http-get",
        granted_scopes=("network:http-api:read",),
        allowed_target_refs=(target_ref,),
    )
    await coordinator.start_run(
        run_id="run-http-get",
        trigger_type="message",
        trigger_ref="qq:message:http-get",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-http-get",
        call_id="call-http-get",
        step_id="step-http-get",
        tool_name=tool.name,
        principal=principal,
        arguments={
            "method": "GET",
            "url": "HTTPS://API.Example.COM:443/data?q=1#fragment",
            "headers": {"Accept": "application/json"},
        },
        target_ref="",
        trusted_context=TrustedToolContext(user_id="user-http-get"),
        worker_id="worker-http-get",
    )
    call = await ledger.get_tool_call("call-http-get")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "succeeded"
    assert executed is True
    assert call is not None
    assert call.target_ref == target_ref


async def test_coordinator_records_http_api_get_timeout_as_retryable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timeout_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        del url, kwargs
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(http_api_module, "fetch_public_text", timeout_fetch, raising=False)
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = HttpApiTool(allowed_methods=["GET"])
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = "network:http-api-origin:https://api.example.com"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-http-timeout",
        granted_scopes=("network:http-api:read",),
        allowed_target_refs=(target_ref,),
    )
    await coordinator.start_run(
        run_id="run-http-timeout",
        trigger_type="message",
        trigger_ref="qq:message:http-timeout",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-http-timeout",
        call_id="call-http-timeout",
        step_id="step-http-timeout",
        tool_name=tool.name,
        principal=principal,
        arguments={"method": "GET", "url": "https://api.example.com/data"},
        trusted_context=TrustedToolContext(user_id="user-http-timeout"),
        worker_id="worker-http-timeout",
    )
    run = await ledger.get_run("run-http-timeout")
    call = await ledger.get_tool_call("call-http-timeout")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_retryable"
    assert run is not None and run.status == "waiting_retry"
    assert call is not None and call.status == "failed_retryable"


async def test_coordinator_rejects_http_api_unsafe_header_before_tool_call(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = HttpApiTool(allowed_methods=["GET"])
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = "network:http-api-origin:https://api.example.com"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-http-header",
        granted_scopes=("network:http-api:read",),
        allowed_target_refs=(target_ref,),
    )
    await coordinator.start_run(
        run_id="run-http-header",
        trigger_type="message",
        trigger_ref="qq:message:http-header",
        principal=principal,
    )

    with pytest.raises(ValueError, match="header"):
        await coordinator.execute_tool(
            run_id="run-http-header",
            call_id="call-http-header",
            step_id="step-http-header",
            tool_name=tool.name,
            principal=principal,
            arguments={
                "method": "GET",
                "url": "https://api.example.com/data",
                "headers": {"Authorization": "Bearer model-secret"},
            },
            trusted_context=TrustedToolContext(user_id="user-http-header"),
            worker_id="worker-http-header",
        )
    call = await ledger.get_tool_call("call-http-header")
    await ledger.close()

    assert call is None


async def test_coordinator_records_web_fetch_timeout_as_retryable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timeout_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        del url, kwargs
        raise TimeoutError("transport timed out")

    monkeypatch.setattr(web_fetch_module, "fetch_public_text", timeout_fetch)
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = WebFetchTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = "network:web-origin:https://example.com"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-web-timeout",
        granted_scopes=("network:fetch",),
        allowed_target_refs=(target_ref,),
    )
    await coordinator.start_run(
        run_id="run-web-timeout",
        trigger_type="message",
        trigger_ref="qq:message:web-timeout",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-web-timeout",
        call_id="call-web-timeout",
        step_id="step-web-timeout",
        tool_name=tool.name,
        principal=principal,
        arguments={"url": "https://example.com/docs"},
        target_ref="",
        trusted_context=TrustedToolContext(user_id="user-web-timeout"),
        worker_id="worker-web-timeout",
    )
    run = await ledger.get_run("run-web-timeout")
    call = await ledger.get_tool_call("call-web-timeout")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_retryable"
    assert execution.result.error is not None
    assert execution.result.error.code == "tool_timeout"
    assert run is not None and run.status == "waiting_retry"
    assert call is not None and call.status == "failed_retryable"


async def test_coordinator_records_web_fetch_safety_rejection_as_terminal(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        del url, kwargs
        raise web_fetch_module.UnsafePublicUrl("DNS resolution was not public")

    monkeypatch.setattr(web_fetch_module, "fetch_public_text", reject_fetch)
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = WebFetchTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = "network:web-origin:https://example.com"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-web-safety",
        granted_scopes=("network:fetch",),
        allowed_target_refs=(target_ref,),
    )
    await coordinator.start_run(
        run_id="run-web-safety",
        trigger_type="message",
        trigger_ref="qq:message:web-safety",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-web-safety",
        call_id="call-web-safety",
        step_id="step-web-safety",
        tool_name=tool.name,
        principal=principal,
        arguments={"url": "https://example.com/docs"},
        target_ref="",
        trusted_context=TrustedToolContext(user_id="user-web-safety"),
        worker_id="worker-web-safety",
    )
    run = await ledger.get_run("run-web-safety")
    call = await ledger.get_tool_call("call-web-safety")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_terminal"
    assert execution.result.error is not None
    assert execution.result.error.retryable is False
    assert run is not None and run.status == "running"
    assert call is not None and call.status == "failed_terminal"


async def test_coordinator_records_web_search_provider_failure_as_retryable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_search(query: str, max_results: int) -> list[dict[str, str]]:
        del query, max_results
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(web_search_module, "_ddg_search_sync", fail_search)
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = WebSearchTool(mode="ddg")
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    target_ref = "network:web-search"
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-web-search",
        granted_scopes=("network:search",),
        allowed_target_refs=(target_ref,),
    )
    await coordinator.start_run(
        run_id="run-web-search",
        trigger_type="message",
        trigger_ref="qq:message:web-search",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-web-search",
        call_id="call-web-search",
        step_id="step-web-search",
        tool_name=tool.name,
        principal=principal,
        arguments={"query": "runtime docs"},
        target_ref="",
        trusted_context=TrustedToolContext(user_id="user-web-search"),
        worker_id="worker-web-search",
    )
    run = await ledger.get_run("run-web-search")
    call = await ledger.get_tool_call("call-web-search")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_retryable"
    assert execution.result.error is not None
    assert execution.result.error.code == "tool_timeout"
    assert run is not None and run.status == "waiting_retry"
    assert call is not None and call.status == "failed_retryable"


async def test_coordinator_rejects_web_fetch_caller_target_mismatch_before_tool_call(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = WebFetchTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-web-spoof",
        granted_scopes=("network:fetch",),
        allowed_target_refs=(
            "network:web-origin:https://example.com",
            "network:web-origin:https://attacker.example",
        ),
    )
    await coordinator.start_run(
        run_id="run-web-spoof",
        trigger_type="message",
        trigger_ref="qq:message:web-spoof",
        principal=principal,
    )

    with pytest.raises(ValueError, match="target_ref does not match"):
        await coordinator.execute_tool(
            run_id="run-web-spoof",
            call_id="call-web-spoof",
            step_id="step-web-spoof",
            tool_name=tool.name,
            principal=principal,
            arguments={"url": "https://example.com/docs"},
            target_ref="network:web-origin:https://attacker.example",
            trusted_context=TrustedToolContext(user_id="user-web-spoof"),
            worker_id="worker-web-spoof",
        )
    call = await ledger.get_tool_call("call-web-spoof")
    await ledger.close()

    assert call is None


async def test_coordinator_gates_canonical_web_fetch_origin_with_principal_allowlist(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    async def fake_fetch(url: str, **kwargs: Any) -> PublicHttpTextResponse:
        del url, kwargs
        nonlocal executed
        executed = True
        return PublicHttpTextResponse(200, "unexpected", "https://example.com/", False)

    monkeypatch.setattr(web_fetch_module, "fetch_public_text", fake_fetch)
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = WebFetchTool()
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-web-denied",
        granted_scopes=("network:fetch",),
        allowed_target_refs=("network:web-origin:https://allowed.example",),
    )
    await coordinator.start_run(
        run_id="run-web-denied",
        trigger_type="message",
        trigger_ref="qq:message:web-denied",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-web-denied",
        call_id="call-web-denied",
        step_id="step-web-denied",
        tool_name=tool.name,
        principal=principal,
        arguments={"url": "https://example.com/docs"},
        target_ref="",
        trusted_context=TrustedToolContext(user_id="user-web-denied"),
        worker_id="worker-web-denied",
    )
    call = await ledger.get_tool_call("call-web-denied")
    await ledger.close()

    assert execution.decision.outcome == "deny"
    assert execution.decision.reason == "target_out_of_scope"
    assert execution.result is None
    assert executed is False
    assert call is not None
    assert call.target_ref == "network:web-origin:https://example.com"
    assert call.status == "denied"


async def test_coordinator_rejects_unsafe_concurrency_key_template(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _UnsafeKeyedTool("unsafe_keyed_read")
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(kind="service", principal_id="runtime")
    await coordinator.start_run(
        run_id="run-unsafe-key",
        trigger_type="recovery",
        trigger_ref="runtime:unsafe-key",
        principal=principal,
    )

    with pytest.raises(ValueError, match="unsafe concurrency key template"):
        await coordinator.execute_tool(
            run_id="run-unsafe-key",
            call_id="call-unsafe-key",
            step_id="step-unsafe-key",
            tool_name=tool.name,
            principal=principal,
            arguments={"resource_id": "resource-1"},
            trusted_context=TrustedToolContext(user_id="runtime"),
            worker_id="worker-unsafe-key",
        )
    call = await ledger.get_tool_call("call-unsafe-key")
    await ledger.close()

    assert call is None
    assert tool.executed is False


async def test_coordinator_retries_same_retryable_call(tmp_path) -> None:
    resume_retryable = getattr(
        coordinator_module.RunCoordinator,
        "resume_retryable_tool",
        None,
    )
    assert callable(resume_retryable)
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    tool = _FlakyReadTool("flaky_read")
    registry.register(tool)
    coordinator = coordinator_module.RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    principal = RuntimePrincipal(kind="service", principal_id="runtime")
    arguments = {"text": "retry me"}
    await coordinator.start_run(
        run_id="run-retry",
        trigger_type="recovery",
        trigger_ref="runtime:retry",
        principal=principal,
    )
    first = await coordinator.execute_tool(
        run_id="run-retry",
        call_id="call-retry",
        step_id="step-retry",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        trusted_context=TrustedToolContext(user_id="runtime"),
        worker_id="worker-retry-1",
    )

    second = await coordinator.resume_retryable_tool(
        run_id="run-retry",
        call_id="call-retry",
        principal=principal,
        arguments=arguments,
        trusted_context=TrustedToolContext(user_id="runtime"),
        worker_id="worker-retry-2",
    )
    run = await ledger.get_run("run-retry")
    call = await ledger.get_tool_call("call-retry")
    await ledger.close()

    assert first.result is not None
    assert first.result.status == "failed_retryable"
    assert second.result is not None
    assert second.result.status == "succeeded"
    assert second.result.structured_output == "recovered"
    assert tool.execute_count == 2
    assert run is not None and run.status == "running"
    assert call is not None and call.status == "succeeded"
    assert call.attempt_count == 2
