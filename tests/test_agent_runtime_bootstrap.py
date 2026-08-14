"""RED contracts for default-off Agent Runtime v2 bootstrap composition."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import APIRouter

import admin as admin_module
from bootstrap import application as application_module
from services.agent_runtime.activation import ProductionActivationProfileV1
from services.agent_runtime.invocation_store import TrustedInvocationStoreV1
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.operator_auth import OperatorAuthorizationStoreV1
from services.agent_runtime.rollout_readiness import (
    ACTIVATION_GATE_NAMES,
    ROLLBACK_GATE_NAMES,
)
from services.memory.governance_store import MemoryGovernanceStore
from services.tools.datetime_tool import DateTimeTool
from services.tools.registry import ToolRegistry
from services.worldbook.governance_store import WorldbookGovernanceStore

_INJECTED_CONTEXT_FIELDS = (
    "agent_runtime_host_ingress",
    "agent_runtime_assembly",
    "agent_runtime_query",
    "memory_governance_query",
    "worldbook_governance_query",
    "agent_runtime_readiness",
    "agent_runtime_operator_action_factory",
)
_SOURCE_SCHEMA_VERSIONS = {
    "runtime": 2,
    "memory": 1,
    "worldbook": 1,
    "operator": 1,
    "invocation": 2,
}


class _RecordingLLM:
    def __init__(self) -> None:
        self.dispatcher: Any = None
        self.dispatcher_calls: list[Any] = []

    def set_runtime_tool_dispatcher(self, dispatcher: Any | None) -> None:
        self.dispatcher = dispatcher
        self.dispatcher_calls.append(dispatcher)


class _BootstrapContext:
    injected_field_writes: list[tuple[str, Any]]
    agent_runtime_host_ingress: Any | None
    agent_runtime_assembly: Any | None
    agent_runtime_query: Any | None
    memory_governance_query: Any | None
    worldbook_governance_query: Any | None
    agent_runtime_readiness: Any | None
    agent_runtime_operator_action_factory: Any | None

    def __init__(
        self,
        *,
        agent_runtime_settings: Any,
        registry: ToolRegistry,
        llm_client: _RecordingLLM,
    ) -> None:
        object.__setattr__(self, "injected_field_writes", [])
        self.config = SimpleNamespace(agent_runtime=agent_runtime_settings)
        self.tool_registry = registry
        self.registry = registry
        self.tools = registry
        self.llm_client = llm_client
        self.storage_accesses = 0
        for field in _INJECTED_CONTEXT_FIELDS:
            object.__setattr__(self, field, None)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _INJECTED_CONTEXT_FIELDS:
            self.injected_field_writes.append((name, value))
        object.__setattr__(self, name, value)

    @property
    def storage(self) -> Any:
        self.storage_accesses += 1
        raise AssertionError("default-off Agent Runtime bootstrap must not resolve storage")


def _lifecycle_factory() -> Callable[..., Any]:
    factory = getattr(
        application_module,
        "create_agent_runtime_composition_lifecycle",
        None,
    )
    assert callable(factory), (
        "bootstrap.application.create_agent_runtime_composition_lifecycle "
        "must be implemented"
    )
    return factory


def _new_lifecycle(ctx: _BootstrapContext, *, repo_root: Path) -> Any:
    lifecycle = _lifecycle_factory()(ctx, repo_root=repo_root)
    assert callable(getattr(lifecycle, "start", None)), "bootstrap lifecycle must expose async start()"
    assert callable(getattr(lifecycle, "stop", None)), "bootstrap lifecycle must expose async stop()"
    return lifecycle


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
        restore_evidence_ref="restore:agent-runtime-bootstrap:20260814",
        rollback_evidence_ref="rollback:agent-runtime-bootstrap:20260814",
    )


def _settings(
    tmp_path: Path,
    *,
    source_digests: dict[str, str],
) -> SimpleNamespace:
    sources: dict[str, SimpleNamespace] = {}
    for name, schema_version in _SOURCE_SCHEMA_VERSIONS.items():
        database = tmp_path / "storage" / f"agent-runtime-{name}.db"
        backup = tmp_path / "storage" / "backups" / f"agent-runtime-{name}.bak"
        sources[name] = _source(
            db_path=str(database.relative_to(tmp_path)),
            expected_schema_version=schema_version,
            backup_path=str(backup.relative_to(tmp_path)),
            backup_sha256=source_digests[name],
        )
    return SimpleNamespace(
        enabled=True,
        worker_id="agent-runtime-bootstrap-worker",
        max_workers=1,
        principal_scopes=("time:read",),
        allowed_target_refs=("onebot:group:123:message:789",),
        runtime=sources["runtime"],
        memory=sources["memory"],
        worldbook=sources["worldbook"],
        operator=sources["operator"],
        invocation=sources["invocation"],
    )


def _write_pinned_dark_attestation(settings: Any, tmp_path: Path) -> None:
    path = tmp_path / "storage/agent-runtime/rollout-attestation.json"
    settings.attestation = SimpleNamespace(
        manifest_path=str(path.relative_to(tmp_path)),
        manifest_sha256="sha256:" + "0" * 64,
    )
    profile = ProductionActivationProfileV1.from_settings(settings, repo_root=tmp_path)
    assert profile is not None
    def unassessed(names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        return {
            gate: {
                "status": "not_assessed",
                "reason": "unknown",
                "evidence_at": None,
                "evidence_ref": None,
            }
            for gate in names
        }
    manifest = {
        "contract_version": "agent_runtime_rollout_attestation.v1",
        "schema_version": 1,
        "profile_fingerprint": profile.attestation_profile_fingerprint(),
        "activation": unassessed(ACTIVATION_GATE_NAMES),
        "rollback": unassessed(ROLLBACK_GATE_NAMES),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(payload)
    settings.attestation.manifest_sha256 = "sha256:" + hashlib.sha256(payload).hexdigest()


async def _prepared_settings(tmp_path: Path) -> SimpleNamespace:
    paths = {
        name: tmp_path / "storage" / f"agent-runtime-{name}.db"
        for name in _SOURCE_SCHEMA_VERSIONS
    }
    stores = (
        AgentRuntimeLedger(paths["runtime"]),
        MemoryGovernanceStore(paths["memory"]),
        WorldbookGovernanceStore(paths["worldbook"]),
        OperatorAuthorizationStoreV1(paths["operator"]),
        TrustedInvocationStoreV1(paths["invocation"]),
    )
    for store in stores:
        await store.init()
    for store in reversed(stores):
        await store.close()

    digests: dict[str, str] = {}
    for name, path in paths.items():
        backup = tmp_path / "storage" / "backups" / f"agent-runtime-{name}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
        digests[name] = "sha256:" + hashlib.sha256(backup.read_bytes()).hexdigest()
    settings = _settings(tmp_path, source_digests=digests)
    _write_pinned_dark_attestation(settings, tmp_path)
    return settings


def _unready_settings(tmp_path: Path) -> SimpleNamespace:
    digests: dict[str, str] = {}
    for name in _SOURCE_SCHEMA_VERSIONS:
        backup = tmp_path / "storage" / "backups" / f"agent-runtime-{name}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(f"backup-evidence:{name}".encode())
        digests[name] = "sha256:" + hashlib.sha256(backup.read_bytes()).hexdigest()
    settings = _settings(tmp_path, source_digests=digests)
    _write_pinned_dark_attestation(settings, tmp_path)
    return settings


def _assert_context_is_detached(ctx: _BootstrapContext) -> None:
    for field in _INJECTED_CONTEXT_FIELDS:
        assert getattr(ctx, field) is None, f"{field} must be cleared or restored"


def _readiness_status(report: Any) -> Any:
    if isinstance(report, Mapping):
        return report.get("status")
    return getattr(report, "status", None)


@pytest.mark.asyncio
async def test_default_off_bootstrap_does_not_resolve_storage_or_publish_runtime_services(
    tmp_path: Path,
) -> None:
    llm = _RecordingLLM()
    ctx = _BootstrapContext(
        agent_runtime_settings=SimpleNamespace(enabled=False),
        registry=ToolRegistry(),
        llm_client=llm,
    )

    lifecycle = _new_lifecycle(ctx, repo_root=tmp_path)
    _assert_context_is_detached(ctx)
    assert ctx.injected_field_writes == []
    assert llm.dispatcher_calls == []

    await lifecycle.start()
    await lifecycle.stop()

    assert ctx.storage_accesses == 0
    assert not (tmp_path / "storage").exists()
    _assert_context_is_detached(ctx)
    assert ctx.injected_field_writes == []
    assert llm.dispatcher_calls == []


@pytest.mark.asyncio
async def test_preflight_ready_bootstrap_publishes_fail_closed_composition_and_tears_it_down(
    tmp_path: Path,
) -> None:
    settings = await _prepared_settings(tmp_path)
    registry = ToolRegistry()
    tool = DateTimeTool()
    registry.register(tool)
    llm = _RecordingLLM()
    ctx = _BootstrapContext(
        agent_runtime_settings=settings,
        registry=registry,
        llm_client=llm,
    )

    lifecycle = _new_lifecycle(ctx, repo_root=tmp_path)
    _assert_context_is_detached(ctx)
    assert llm.dispatcher is None

    await lifecycle.start()
    readiness = ctx.agent_runtime_readiness
    try:
        assembly = ctx.agent_runtime_assembly
        host_ingress = ctx.agent_runtime_host_ingress
        dispatcher: Any = llm.dispatcher

        assert assembly is not None
        assert host_ingress is not None
        assert dispatcher is not None
        assert dispatcher is getattr(assembly, "dispatcher", None)
        assert ctx.agent_runtime_query is not None
        assert ctx.memory_governance_query is not None
        assert ctx.worldbook_governance_query is not None
        assert readiness is not None
        assert callable(getattr(readiness, "dark_readiness", None))
        assert callable(
            getattr(ctx.agent_runtime_operator_action_factory, "authenticate", None)
        )
        assert {
            field for field, value in ctx.injected_field_writes if value is not None
        } >= set(_INJECTED_CONTEXT_FIELDS)

        invocation = await host_ingress.record_onebot_message(
            group_id="123",
            user_id="456",
            message_id="789",
        )
        results = await dispatcher.dispatch_tool_uses(
            invocation_id=invocation.invocation_id,
            tool_uses=(
                {"id": "tool-use-without-worker", "name": tool.name, "arguments": {}},
            ),
            bot=None,
        )
        assert results == ['{"code":"worker_not_ready","status":"rejected"}']
    finally:
        await lifecycle.stop()

    _assert_context_is_detached(ctx)
    assert llm.dispatcher is None
    assert llm.dispatcher_calls[-1] is None
    assert _readiness_status(await readiness.dark_readiness()) == "not_ready"


@pytest.mark.asyncio
async def test_unready_enabled_bootstrap_fails_before_context_injection_or_source_creation(
    tmp_path: Path,
) -> None:
    settings = _unready_settings(tmp_path)
    llm = _RecordingLLM()
    ctx = _BootstrapContext(
        agent_runtime_settings=settings,
        registry=ToolRegistry(),
        llm_client=llm,
    )
    lifecycle = _new_lifecycle(ctx, repo_root=tmp_path)

    with pytest.raises(Exception) as raised:
        await lifecycle.start()

    error = raised.value
    assert isinstance(error, RuntimeError) or type(error).__name__ == "ProductionActivationNotReadyError"
    _assert_context_is_detached(ctx)
    assert ctx.injected_field_writes == []
    assert llm.dispatcher_calls == []
    for name in _SOURCE_SCHEMA_VERSIONS:
        assert not (tmp_path / getattr(settings, name).db_path).exists()


@pytest.mark.asyncio
async def test_bootstrap_shutdown_finishes_source_cleanup_before_dispatcher_detach_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = await _prepared_settings(tmp_path)
    llm = _RecordingLLM()
    ctx = _BootstrapContext(
        agent_runtime_settings=settings,
        registry=ToolRegistry(),
        llm_client=llm,
    )
    lifecycle = _new_lifecycle(ctx, repo_root=tmp_path)
    await lifecycle.start()
    assembly = ctx.agent_runtime_assembly
    assert assembly is not None

    close_entered = asyncio.Event()
    allow_close = asyncio.Event()
    original_close = type(assembly).close

    async def blocked_close(instance: Any) -> None:
        close_entered.set()
        await allow_close.wait()
        await original_close(instance)

    monkeypatch.setattr(type(assembly), "close", blocked_close)
    shutdown = asyncio.create_task(lifecycle.stop())
    await close_entered.wait()
    assert llm.dispatcher is assembly.dispatcher
    assert ctx.agent_runtime_assembly is assembly

    shutdown.cancel()
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert llm.dispatcher is assembly.dispatcher

    allow_close.set()
    with pytest.raises(asyncio.CancelledError):
        await shutdown

    _assert_context_is_detached(ctx)
    assert llm.dispatcher is None


@pytest.mark.asyncio
async def test_bootstrap_passes_only_explicit_runtime_services_to_admin_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = await _prepared_settings(tmp_path)
    llm = _RecordingLLM()
    ctx = _BootstrapContext(
        agent_runtime_settings=settings,
        registry=ToolRegistry(),
        llm_client=llm,
    )
    lifecycle = _new_lifecycle(ctx, repo_root=tmp_path)
    await lifecycle.start()
    captured: dict[str, Any] = {}

    def capture_api_router(**dependencies: Any) -> APIRouter:
        captured.update(dependencies)
        return APIRouter()

    monkeypatch.setattr(admin_module, "create_api_router", capture_api_router)
    try:
        admin_module.create_admin_router(ctx)
        assert captured["runtime_query"] is ctx.agent_runtime_query
        assert captured["memory_query"] is ctx.memory_governance_query
        assert captured["worldbook_query"] is ctx.worldbook_governance_query
        assert captured["readiness"] is ctx.agent_runtime_readiness
        assert (
            captured["operator_action_factory"]
            is ctx.agent_runtime_operator_action_factory
        )
    finally:
        await lifecycle.stop()


@pytest.mark.asyncio
async def test_bootstrap_dark_rehearsal_never_self_attests_activation_or_rollback(
    tmp_path: Path,
) -> None:
    settings = await _prepared_settings(tmp_path)
    ctx = _BootstrapContext(
        agent_runtime_settings=settings,
        registry=ToolRegistry(),
        llm_client=_RecordingLLM(),
    )
    lifecycle = _new_lifecycle(ctx, repo_root=tmp_path)
    await lifecycle.start()
    readiness = ctx.agent_runtime_readiness
    assert readiness is not None
    try:
        dark = await readiness.dark_readiness()
        activation = await readiness.activation_readiness()
        rollback = await readiness.rollback_readiness()

        assert dark["status"] == "ready"
        assert activation["status"] == "not_ready"
        assert activation["activation_authorized"] is False
        assert all(
            gate["status"] == "not_assessed"
            for gate in activation["gates"].values()
        )
        assert rollback["status"] == "not_ready"
        assert all(
            gate["status"] == "not_assessed"
            for gate in rollback["gates"].values()
        )
    finally:
        await lifecycle.stop()
