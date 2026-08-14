"""RED contracts for the fail-closed Agent Runtime v2 production composition."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from kernel.types import Tool, ToolContext, ToolEffect, ToolSpec
from services.agent_runtime.activation import ProductionActivationProfileV1
from services.agent_runtime.invocation_store import AuthoritativeTriggerV1
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.operator_auth import OperatorAuthorizationStoreV1
from services.llm.client import LLMClient
from services.memory.governance_store import MemoryGovernanceStore
from services.tools.registry import ToolRegistry
from services.worldbook.governance_store import WorldbookGovernanceStore

_ACTIVATION_GATES = (
    "production_principal_construction",
    "trusted_invocation_persistence",
    "trusted_invocation_reconstruction",
    "operator_identity",
    "operator_authentication",
    "production_runtime_source_path",
    "production_memory_source_path",
    "llm_client_wiring",
    "tool_registry_wiring",
    "bootstrap_wiring",
    "single_worker_gate",
    "exclusive_recovery_gate",
    "worldbook_governance_source_path",
    "worldbook_single_world_gate",
    "worldbook_authoritative_reread",
    "worldbook_reducer_verifier",
    "worldbook_no_dual_truth",
)
_ROLLBACK_GATES = (
    "dark_code_removal",
    "production_database_rollback",
    "production_migration_rollback",
    "production_runtime_wiring_rollback",
)


def _api() -> Any:
    module_name = "services.agent_runtime.production"
    if importlib.util.find_spec(module_name) is None:
        pytest.fail("Agent Runtime v2 production composition module is required")
    module = importlib.import_module(module_name)
    for name in (
        "ProductionRuntimeAssemblyV1",
        "ProductionActivationNotReadyError",
        "compose_production_runtime_from_settings",
    ):
        assert getattr(module, name, None) is not None, f"{name} is required"
    return module


class _ReadTool(Tool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "read_runtime_state"

    @property
    def description(self) -> str:
        return "Read an attested runtime value."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            owner="test",
            description=self.description,
            input_schema=self.parameters,
            output_schema=self.parameters,
            effect=ToolEffect.READ,
            required_scopes=("runtime:read",),
        )

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        self.calls += 1
        return {"value": int(kwargs["value"])}


class _LeaseReplacingReadTool(_ReadTool):
    def __init__(self) -> None:
        super().__init__()
        self.on_first_call: Callable[[], Awaitable[None]] | None = None

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        result = await super().execute(ctx, **kwargs)
        if self.calls == 1:
            callback = self.on_first_call
            assert callback is not None
            await callback()
        return result


class _RecordingLLM:
    def __init__(self) -> None:
        self.dispatcher: Any = None

    def set_runtime_tool_dispatcher(self, dispatcher: Any | None) -> None:
        self.dispatcher = dispatcher


class _LegacyRegistry:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, name: str, arguments: str, ctx: ToolContext) -> str:
        self.calls += 1
        return "legacy execution must not happen"


class _CapturingDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def dispatch_tool_uses(
        self,
        *,
        invocation_id: str | None,
        tool_uses: tuple[dict[str, Any], ...],
        bot: Any,
    ) -> list[str]:
        self.calls.append(
            {
                "invocation_id": invocation_id,
                "tool_uses": tool_uses,
                "bot": bot,
            }
        )
        return ["Tool error: trusted invocation is required"]


def _source(
    *,
    db_path: str,
    expected_schema_version: int,
    backup_path: str,
    backup_sha256: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        db_path=db_path,
        expected_schema_version=expected_schema_version,
        backup_path=backup_path,
        backup_sha256=backup_sha256,
        restore_evidence_ref="restore:production-composition:20260814",
        rollback_evidence_ref="rollback:production-composition:20260814",
    )


def _settings(
    tmp_path: Path,
    *,
    source_digests: dict[str, str],
) -> SimpleNamespace:
    versions = {
        "runtime": 2,
        "memory": 1,
        "worldbook": 1,
        "operator": 1,
        "invocation": 2,
    }
    sources: dict[str, SimpleNamespace] = {}
    for name, version in versions.items():
        database = tmp_path / "storage" / f"production-{name}.db"
        backup = tmp_path / "storage" / "backups" / f"production-{name}.bak"
        sources[name] = _source(
            db_path=str(database.relative_to(tmp_path)),
            expected_schema_version=version,
            backup_path=str(backup.relative_to(tmp_path)),
            backup_sha256=source_digests[name],
        )
    return SimpleNamespace(
        enabled=True,
        worker_id="agent-runtime-worker-1",
        max_workers=1,
        principal_scopes=("runtime:read",),
        allowed_target_refs=("onebot:group:123:message:789",),
        runtime=sources["runtime"],
        memory=sources["memory"],
        worldbook=sources["worldbook"],
        operator=sources["operator"],
        invocation=sources["invocation"],
    )


def _attestor(
    gates: tuple[str, ...],
    *,
    blocked_gate: str | None = None,
) -> Any:
    async def attest() -> dict[str, dict[str, Any]]:
        evidence_at = datetime.now(UTC) - timedelta(seconds=1)
        return {
            gate: {
                "status": "not_ready" if gate == blocked_gate else "ready",
                "reason": "missing_requirement" if gate == blocked_gate else "verified",
                "evidence_at": evidence_at,
            }
            for gate in gates
        }

    return attest


def _ready_activation_attestor() -> Any:
    return _attestor(_ACTIVATION_GATES)


def _ready_rollback_attestor() -> Any:
    return _attestor(_ROLLBACK_GATES)


def _write_pinned_attestation(
    settings: Any,
    tmp_path: Path,
    *,
    blocked_activation_gate: str | None = None,
    blocked_rollback_gate: str | None = None,
) -> None:
    path = tmp_path / "storage/agent-runtime/rollout-attestation.json"
    settings.attestation = SimpleNamespace(
        manifest_path=str(path.relative_to(tmp_path)),
        manifest_sha256="sha256:" + "0" * 64,
    )
    profile = ProductionActivationProfileV1.from_settings(settings, repo_root=tmp_path)
    assert profile is not None
    evidence_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace(
        "+00:00", "Z"
    )
    manifest = {
        "contract_version": "agent_runtime_rollout_attestation.v1",
        "schema_version": 1,
        "profile_fingerprint": profile.attestation_profile_fingerprint(),
        "activation": {
            gate: {
                "status": (
                    "not_ready" if gate == blocked_activation_gate else "ready"
                ),
                "reason": (
                    "missing_requirement"
                    if gate == blocked_activation_gate
                    else "verified"
                ),
                "evidence_at": evidence_at,
                "evidence_ref": f"evidence:production-composition:{gate}:20260814",
            }
            for gate in _ACTIVATION_GATES
        },
        "rollback": {
            gate: {
                "status": (
                    "not_ready" if gate == blocked_rollback_gate else "ready"
                ),
                "reason": (
                    "missing_requirement"
                    if gate == blocked_rollback_gate
                    else "verified"
                ),
                "evidence_at": evidence_at,
                "evidence_ref": f"evidence:production-composition:{gate}:20260814",
            }
            for gate in _ROLLBACK_GATES
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(payload)
    settings.attestation = SimpleNamespace(
        manifest_path=str(path.relative_to(tmp_path)),
        manifest_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


async def _prepared_settings(
    tmp_path: Path,
    *,
    blocked_activation_gate: str | None = None,
    blocked_rollback_gate: str | None = None,
) -> SimpleNamespace:
    paths = {
        name: tmp_path / "storage" / f"production-{name}.db"
        for name in ("runtime", "memory", "worldbook", "operator", "invocation")
    }
    stores = (
        AgentRuntimeLedger(paths["runtime"]),
        MemoryGovernanceStore(paths["memory"]),
        WorldbookGovernanceStore(paths["worldbook"]),
        OperatorAuthorizationStoreV1(paths["operator"]),
    )
    from services.agent_runtime.invocation_store import TrustedInvocationStoreV1

    invocation_store = TrustedInvocationStoreV1(paths["invocation"])
    for store in (*stores, invocation_store):
        await store.init()
    for store in reversed((*stores, invocation_store)):
        await store.close()

    digests: dict[str, str] = {}
    for name, path in paths.items():
        backup = tmp_path / "storage" / "backups" / f"production-{name}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
        digests[name] = "sha256:" + hashlib.sha256(backup.read_bytes()).hexdigest()
    settings = _settings(tmp_path, source_digests=digests)
    _write_pinned_attestation(
        settings,
        tmp_path,
        blocked_activation_gate=blocked_activation_gate,
        blocked_rollback_gate=blocked_rollback_gate,
    )
    return settings


@pytest.mark.asyncio
async def test_disabled_composition_returns_none_without_resolving_sources(
    tmp_path: Path,
) -> None:
    api = _api()

    assembly = await api.compose_production_runtime_from_settings(
        SimpleNamespace(enabled=False),
        repo_root=tmp_path,
        registry=object(),
    )

    assert assembly is None
    assert not (tmp_path / "storage").exists()


@pytest.mark.asyncio
async def test_unready_preflight_cannot_open_or_create_a_runtime_source(
    tmp_path: Path,
) -> None:
    api = _api()
    backup_root = tmp_path / "storage" / "backups"
    digests: dict[str, str] = {}
    for name in ("runtime", "memory", "worldbook", "operator", "invocation"):
        backup = backup_root / f"production-{name}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(f"backup:{name}".encode())
        digests[name] = "sha256:" + hashlib.sha256(backup.read_bytes()).hexdigest()
    settings = _settings(tmp_path, source_digests=digests)
    _write_pinned_attestation(settings, tmp_path)

    with pytest.raises(api.ProductionActivationNotReadyError):
        await api.compose_production_runtime_from_settings(
            settings,
            repo_root=tmp_path,
            registry=ToolRegistry(),
        )

    assert not (tmp_path / settings.runtime.db_path).exists()


@pytest.mark.asyncio
async def test_composition_wires_a_fail_closed_dispatcher_without_legacy_fallback(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    registry = ToolRegistry()
    tool = _ReadTool()
    registry.register(tool)
    llm = _RecordingLLM()
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=registry,
        llm_client=llm,
    )
    try:
        assert isinstance(assembly, api.ProductionRuntimeAssemblyV1)
        assert llm.dispatcher is assembly.dispatcher
        assert assembly.runtime is not None
        assert assembly.memory is not None
        assert assembly.worldbook is not None
        assert assembly.operators is not None
        assert assembly.invocations is not None
        assert (
            assembly.create_admin_operator_actions_factory().__class__.__name__
            == "AdminOperatorActionsFactoryV1"
        )

        without_invocation = await assembly.dispatcher.dispatch_tool_uses(
            invocation_id=None,
            tool_uses=(
                {"id": "tool-use-1", "name": tool.name, "arguments": {"value": 7}},
            ),
            bot=None,
        )
        assert without_invocation == [
            '{"code":"trusted_invocation_required","status":"rejected"}'
        ]

        host_ingress = assembly.create_host_trigger_ingress()
        invocation = await host_ingress.record_onebot_message(
            group_id="123",
            user_id="456",
            message_id="789",
        )
        record = await assembly.invocations.get(invocation.invocation_id)
        assert record is not None
        assert record.registry_generation == registry.snapshot_catalog()[0]
        without_worker = await assembly.dispatcher.dispatch_tool_uses(
            invocation_id=record.invocation_id,
            tool_uses=(
                {"id": "tool-use-2", "name": tool.name, "arguments": {"value": 8}},
            ),
            bot=None,
        )
        assert without_worker == [
            '{"code":"worker_not_ready","status":"rejected"}'
        ]
        assert tool.calls == 0
    finally:
        await assembly.close()


@pytest.mark.asyncio
async def test_production_admin_factory_registers_offline_onebot_reconciliation(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=ToolRegistry(),
    )
    try:
        factory = assembly.create_admin_operator_actions_factory()
        adapters = tuple(factory._reconciliation_adapters)

        assert len(adapters) == 1
        assert adapters[0].adapter_id == "onebot-manual-attestation-v1"
        assert adapters[0].offline_only is True
        assert adapters[0].idempotent is True
    finally:
        await assembly.close()


@pytest.mark.asyncio
async def test_llm_bridge_uses_selected_dispatcher_when_no_invocation_exists() -> None:
    client = object.__new__(LLMClient)
    legacy = _LegacyRegistry()
    dispatcher = _CapturingDispatcher()
    client_any = cast(Any, client)
    client_any._tools = legacy
    client_any._runtime_tool_dispatcher = dispatcher
    bot = object()

    results = await client._dispatch_tool_uses(
        tool_uses=(
            SimpleNamespace(id="tool-use-3", name="read_runtime_state", input={"value": 9}),
        ),
        tool_ctx=ToolContext(bot=bot, user_id="456", group_id="123"),
        runtime_invocation_id=None,
    )

    assert results == ["Tool error: trusted invocation is required"]
    assert legacy.calls == 0
    assert dispatcher.calls == [
        {
            "invocation_id": None,
            "tool_uses": (
                {
                    "id": "tool-use-3",
                    "name": "read_runtime_state",
                    "arguments": {"value": 9},
                },
            ),
            "bot": bot,
        }
    ]


@pytest.mark.asyncio
async def test_selected_dispatcher_disables_post_reply_legacy_sticker_path() -> None:
    client = object.__new__(LLMClient)
    client._runtime_tool_dispatcher = object()

    sent = await client._send_post_reply_sticker_if_needed(
        reply="legacy sticker must not execute",
        thinker_decision=None,
        session_id="group_123",
        group_id="123",
        user_id="456",
        turn_id="turn-1",
        ctx=None,
        already_sent=False,
    )

    assert sent is False


@pytest.mark.asyncio
async def test_worker_lease_is_durable_exclusive_and_token_bound(tmp_path: Path) -> None:
    from services.agent_runtime.invocation_store import TrustedInvocationStoreV1

    path = tmp_path / "worker-lease.db"
    first_store = TrustedInvocationStoreV1(path)
    second_store = TrustedInvocationStoreV1(path)
    await first_store.init()
    await second_store.init()
    t0 = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    try:
        first = await first_store.acquire_worker_lease(
            worker_id="agent-runtime-worker-1",
            lease_ttl_seconds=30,
            now=t0,
        )
        assert first is not None
        assert await first_store.has_worker_lease(first, now=t0)
        assert (
            await second_store.acquire_worker_lease(
                worker_id="agent-runtime-worker-2",
                lease_ttl_seconds=30,
                now=t0 + timedelta(seconds=1),
            )
            is None
        )

        renewed = await first_store.renew_worker_lease(
            first,
            lease_ttl_seconds=30,
            now=t0 + timedelta(seconds=2),
        )
        assert renewed is not None
        assert not await first_store.has_worker_lease(
            first,
            now=t0 + timedelta(seconds=3),
        )
        assert await first_store.has_worker_lease(
            renewed,
            now=t0 + timedelta(seconds=3),
        )
        assert await second_store.acquire_worker_lease(
            worker_id="agent-runtime-worker-2",
            lease_ttl_seconds=30,
            now=t0 + timedelta(seconds=33),
        ) is not None
        assert not await first_store.release_worker_lease(renewed)
    finally:
        await second_store.close()
        await first_store.close()


@pytest.mark.asyncio
async def test_worker_start_requires_attested_activation_and_rollback_before_lease(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(
        tmp_path,
        blocked_activation_gate="worldbook_no_dual_truth",
    )
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=ToolRegistry(),
    )
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    try:
        with pytest.raises(api.ProductionActivationNotReadyError):
            await assembly.start_worker(now=now)

        assert assembly._worker_lease is None
        alternate = await assembly.invocations.acquire_worker_lease(
            worker_id="agent-runtime-worker-2",
            lease_ttl_seconds=30,
            now=now,
        )
        assert alternate is not None
        assert await assembly.invocations.release_worker_lease(alternate)
    finally:
        await assembly.close()


@pytest.mark.asyncio
async def test_worker_start_rechecks_profile_backup_integrity_before_lease(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    _write_pinned_attestation(settings, tmp_path)
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=ToolRegistry(),
    )
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    try:
        backup = tmp_path / settings.runtime.backup_path
        backup.write_bytes(b"tampered-after-composition")

        with pytest.raises(api.ProductionActivationNotReadyError):
            await assembly.start_worker(now=now)

        assert assembly._worker_lease is None
        assert assembly.dispatcher._worker_lease is None
        alternate = await assembly.invocations.acquire_worker_lease(
            worker_id="agent-runtime-worker-2",
            lease_ttl_seconds=30,
            now=now,
        )
        assert alternate is not None
        assert await assembly.invocations.release_worker_lease(alternate)
    finally:
        await assembly.close()


@pytest.mark.asyncio
async def test_pinned_profile_attestation_is_automatically_bound_to_worker_start(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    _write_pinned_attestation(settings, tmp_path)
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=ToolRegistry(),
    )
    try:
        activation = await assembly.readiness.activation_readiness()
        rollback = await assembly.readiness.rollback_readiness()
        assert activation["status"] == "ready"
        assert activation["activation_authorized"] is True
        assert rollback["status"] == "ready"

        assert await assembly.start_worker(now=datetime.now(UTC)) == []
        assert assembly._worker_lease is not None
    finally:
        await assembly.close()


@pytest.mark.asyncio
async def test_pinned_profile_attestation_cannot_be_overridden_by_callbacks(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    _write_pinned_attestation(settings, tmp_path)

    with pytest.raises(
        ValueError,
        match=r"attestation.*overridden|overridden.*attestation",
    ):
        await api.compose_production_runtime_from_settings(
            settings,
            repo_root=tmp_path,
            registry=ToolRegistry(),
            activation_attestor=_ready_activation_attestor(),
            rollback_attestor=_ready_rollback_attestor(),
        )


@pytest.mark.asyncio
async def test_unpinned_profile_cannot_be_composed(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    profile = ProductionActivationProfileV1.from_settings(settings, repo_root=tmp_path)
    assert profile is not None

    with pytest.raises(ValueError, match=r"digest-pinned|attestation"):
        await api.compose_production_runtime(
            replace(profile, attestation=None),
            registry=ToolRegistry(),
        )


@pytest.mark.asyncio
async def test_tampered_pinned_attestation_cannot_acquire_a_worker_lease(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    _write_pinned_attestation(settings, tmp_path)
    settings.attestation.manifest_path = "storage/agent-runtime/rollout-attestation.json"
    (tmp_path / settings.attestation.manifest_path).write_text("{}", encoding="utf-8")
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=ToolRegistry(),
    )
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    try:
        activation = await assembly.readiness.activation_readiness()
        assert activation["status"] == "not_ready"
        assert all(
            gate["status"] == "not_assessed"
            and gate["reason"] == "attestation_failed"
            for gate in activation["gates"].values()
        )
        assert "rollout-attestation.json" not in json.dumps(activation, sort_keys=True)

        with pytest.raises(api.ProductionActivationNotReadyError):
            await assembly.start_worker(now=now)

        assert assembly._worker_lease is None
        assert assembly.dispatcher._worker_lease is None
        alternate = await assembly.invocations.acquire_worker_lease(
            worker_id="agent-runtime-worker-2",
            lease_ttl_seconds=30,
            now=now,
        )
        assert alternate is not None
        assert await assembly.invocations.release_worker_lease(alternate)
    finally:
        await assembly.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocked_activation_gate", "blocked_rollback_gate"),
    [
        pytest.param(
            "worldbook_no_dual_truth",
            None,
            id="partial-activation",
        ),
        pytest.param(
            None,
            "production_database_rollback",
            id="partial-rollback",
        ),
    ],
)
async def test_worker_start_rejects_partial_or_invalid_attestation_before_lease(
    tmp_path: Path,
    blocked_activation_gate: str | None,
    blocked_rollback_gate: str | None,
) -> None:
    api = _api()
    settings = await _prepared_settings(
        tmp_path,
        blocked_activation_gate=blocked_activation_gate,
        blocked_rollback_gate=blocked_rollback_gate,
    )
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=ToolRegistry(),
    )
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    try:
        with pytest.raises(api.ProductionActivationNotReadyError):
            await assembly.start_worker(now=now)

        assert assembly._worker_lease is None
        assert assembly.dispatcher._worker_lease is None
        alternate = await assembly.invocations.acquire_worker_lease(
            worker_id="agent-runtime-worker-2",
            lease_ttl_seconds=30,
            now=now,
        )
        assert alternate is not None
        assert await assembly.invocations.release_worker_lease(alternate)
    finally:
        await assembly.close()


@pytest.mark.asyncio
async def test_worker_start_propagates_attestation_cancellation_without_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)

    async def cancelled_manifest_attestation(_self: Any) -> dict[str, Any]:
        raise asyncio.CancelledError()

    from services.agent_runtime.rollout_attestation import ProfileBoundRolloutAttestorV1

    monkeypatch.setattr(
        ProfileBoundRolloutAttestorV1,
        "activation_attestation",
        cancelled_manifest_attestation,
    )
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=ToolRegistry(),
    )
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    try:
        with pytest.raises(asyncio.CancelledError):
            await assembly.start_worker(now=now)

        assert assembly._worker_lease is None
        assert assembly.dispatcher._worker_lease is None
        alternate = await assembly.invocations.acquire_worker_lease(
            worker_id="agent-runtime-worker-2",
            lease_ttl_seconds=30,
            now=now,
        )
        assert alternate is not None
        assert await assembly.invocations.release_worker_lease(alternate)
    finally:
        await assembly.close()


@pytest.mark.asyncio
async def test_worker_lease_opens_dispatch_only_while_exclusive_recovery_is_held(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    registry = ToolRegistry()
    tool = _ReadTool()
    registry.register(tool)
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=registry,
    )
    try:
        recovered = await assembly.start_worker(now=datetime.now(UTC))
        assert recovered == []

        trigger = AuthoritativeTriggerV1.from_onebot_message(
            group_id="123",
            user_id="456",
            message_id="789",
            session_id="group_123",
            registry_generation=1,
            granted_scopes=("runtime:read",),
            allowed_target_refs=("onebot:group:123:message:789",),
        )
        record = await assembly.invocations.record(trigger)
        results = await assembly.dispatcher.dispatch_tool_uses(
            invocation_id=record.invocation_id,
            tool_uses=(
                {"id": "tool-use-4", "name": tool.name, "arguments": {"value": 8}},
            ),
            bot=None,
        )
        assert json.loads(results[0]) == {
            "output": {"value": 8},
            "status": "succeeded",
        }
        assert tool.calls == 1

        await assembly.stop_worker()
        after_stop = await assembly.dispatcher.dispatch_tool_uses(
            invocation_id=record.invocation_id,
            tool_uses=(
                {"id": "tool-use-5", "name": tool.name, "arguments": {"value": 9}},
            ),
            bot=None,
        )
        assert after_stop == ['{"code":"worker_not_ready","status":"rejected"}']
        assert tool.calls == 1
    finally:
        await assembly.close()


@pytest.mark.asyncio
async def test_dispatcher_rechecks_exact_lease_before_each_tool_use(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    registry = ToolRegistry()
    tool = _LeaseReplacingReadTool()
    registry.register(tool)
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=registry,
    )
    replacement = None
    try:
        await assembly.start_worker(now=datetime.now(UTC))

        async def replace_worker_lease() -> None:
            nonlocal replacement
            replacement = await assembly.invocations.acquire_worker_lease(
                worker_id="agent-runtime-worker-replacement",
                lease_ttl_seconds=30,
                now=datetime.now(UTC) + timedelta(seconds=60),
            )
            assert replacement is not None

        tool.on_first_call = replace_worker_lease
        trigger = AuthoritativeTriggerV1.from_onebot_message(
            group_id="123",
            user_id="456",
            message_id="789",
            session_id="group_123",
            registry_generation=1,
            granted_scopes=("runtime:read",),
            allowed_target_refs=("onebot:group:123:message:789",),
        )
        record = await assembly.invocations.record(trigger)

        results = await assembly.dispatcher.dispatch_tool_uses(
            invocation_id=record.invocation_id,
            tool_uses=(
                {"id": "tool-use-lease-1", "name": tool.name, "arguments": {"value": 1}},
                {"id": "tool-use-lease-2", "name": tool.name, "arguments": {"value": 2}},
            ),
            bot=None,
        )

        assert json.loads(results[0]) == {
            "output": {"value": 1},
            "status": "succeeded",
        }
        assert results[1] == '{"code":"worker_not_ready","status":"rejected"}'
        assert tool.calls == 1
    finally:
        if replacement is not None:
            await assembly.invocations.release_worker_lease(replacement)
        await assembly.close()


@pytest.mark.asyncio
async def test_worker_shutdown_releases_lease_before_cancellation_escapes(
    tmp_path: Path,
) -> None:
    api = _api()
    settings = await _prepared_settings(tmp_path)
    assembly = await api.compose_production_runtime_from_settings(
        settings,
        repo_root=tmp_path,
        registry=ToolRegistry(),
    )
    await assembly.start_worker(now=datetime.now(UTC))
    lease = assembly._worker_lease
    assert lease is not None
    release_started = asyncio.Event()
    allow_release = asyncio.Event()
    original_release = assembly.invocations.release_worker_lease

    async def delayed_release(current: Any) -> bool:
        release_started.set()
        await allow_release.wait()
        return await original_release(current)

    cast(Any, assembly.invocations).release_worker_lease = delayed_release
    stop_task = asyncio.create_task(assembly.stop_worker())
    await asyncio.wait_for(release_started.wait(), timeout=1.0)
    stop_task.cancel()
    allow_release.set()

    with pytest.raises(asyncio.CancelledError):
        await stop_task
    assert not await assembly.invocations.has_worker_lease(lease)
    await assembly.close()
