"""Offline provider reconciliation contracts for Agent Runtime v2."""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest

from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.policy import RuntimePrincipal


def _reconciliation_api() -> Any:
    try:
        module = import_module("services.agent_runtime.reconciliation")
    except ModuleNotFoundError as exc:
        if exc.name != "services.agent_runtime.reconciliation":
            raise
        module = None
    assert module is not None, "Agent Runtime reconciliation service is missing"
    return module


async def _unknown_call(
    ledger: AgentRuntimeLedger,
    *,
    run_id: str,
    call_id: str,
    target_ref: str,
    owner: str = "test",
    tool_name: str = "external_effect",
) -> None:
    await ledger.create_run(
        run_id=run_id,
        trigger_type="recovery",
        trigger_ref=f"runtime:{run_id}",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.transition_run(run_id, to_status="running", actor="runtime")
    await ledger.create_tool_call(
        call_id=call_id,
        run_id=run_id,
        step_id=call_id,
        tool_name=tool_name,
        tool_version="1",
        owner=owner,
        effect="external_irreversible",
        principal_kind="service",
        principal_id="runtime",
        target_ref=target_ref,
        args_digest=f"sha256:{call_id}",
        idempotency_mode="reconcile_only",
        concurrency_mode="keyed_serial",
        concurrency_key=target_ref,
    )
    await ledger.transition_tool_call(
        call_id,
        to_status="ready",
        actor="policy",
    )
    await ledger.claim_tool_call(
        call_id,
        lease_owner="worker",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="worker",
    )
    await ledger.transition_tool_call(
        call_id,
        to_status="dispatching",
        actor="worker",
        expected_lease_owner="worker",
    )
    await ledger.transition_tool_call(
        call_id,
        to_status="unknown",
        actor="worker",
        error_code="provider_unknown",
        expected_lease_owner="worker",
    )
    await ledger.transition_run(
        run_id,
        to_status="waiting_external",
        actor="runtime",
    )


async def test_reconciliation_service_authorizes_adapter_and_receipt(
    tmp_path: Any,
) -> None:
    api = _reconciliation_api()
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    target_ref = "external:target:1"
    await _unknown_call(
        ledger,
        run_id="run-reconcile-service",
        call_id="call-reconcile-service",
        target_ref=target_ref,
    )
    adapter_calls: list[Any] = []

    class _Adapter:
        adapter_id = "test-provider"
        idempotent = True

        def supports(self, call: Any) -> bool:
            return call.owner == "test"

        async def reconcile(self, call: Any, command: Any) -> Any:
            adapter_calls.append((call, command))
            return api.ReconciliationReceipt(
                decision=command.decision,
                target_ref=call.target_ref,
                evidence_ref=command.evidence_ref,
                external_id=command.external_id,
            )

    coordinator = api.ReconciliationCoordinator(ledger=ledger)
    principal = RuntimePrincipal(
        kind="service",
        principal_id="operator-1",
        granted_scopes=("runtime:tool:reconcile",),
        allowed_target_refs=(target_ref,),
    )
    result = await coordinator.resolve_unknown(
        call_id="call-reconcile-service",
        decision="confirmed_succeeded",
        principal=principal,
        evidence_ref="operator:ticket:2001",
        operator_note="  provider dashboard confirmed delivery  ",
        external_id="provider-2001",
        adapter=_Adapter(),
    )

    class _MustNotRun:
        adapter_id = "test-provider"
        idempotent = True
        calls = 0

        def supports(self, call: Any) -> bool:
            return True

        async def reconcile(self, call: Any, command: Any) -> Any:
            self.calls += 1
            raise AssertionError("existing resolution must short-circuit")

    retry_adapter = _MustNotRun()
    same = await coordinator.resolve_unknown(
        call_id="call-reconcile-service",
        decision="confirmed_succeeded",
        principal=principal,
        evidence_ref="operator:ticket:2001",
        operator_note="provider dashboard confirmed delivery",
        external_id="provider-2001",
        adapter=retry_adapter,
    )
    with pytest.raises(ValueError, match="different reconciliation"):
        await coordinator.resolve_unknown(
            call_id="call-reconcile-service",
            decision="confirmed_not_applied",
            principal=principal,
            evidence_ref="operator:ticket:conflict",
            operator_note="conflicting outcome",
            adapter=retry_adapter,
        )
    run = await ledger.get_run("run-reconcile-service")
    events = await ledger.list_events(run_id="run-reconcile-service")
    await ledger.close()

    assert result.decision == "confirmed_succeeded"
    assert result.actor == "service:operator-1"
    assert result.evidence_ref == "operator:ticket:2001"
    assert result.note_digest.startswith("sha256:")
    assert same == result
    assert len(adapter_calls) == 1
    assert retry_adapter.calls == 0
    assert adapter_calls[0][1].operator_note == (
        "provider dashboard confirmed delivery"
    )
    assert run is not None and run.status == "running"
    serialized_events = str([event.metadata for event in events])
    assert "provider dashboard confirmed delivery" not in serialized_events


@pytest.mark.parametrize(
    "principal",
    [
        RuntimePrincipal(
            kind="service",
            principal_id="no-scope",
            allowed_target_refs=("external:target:2",),
        ),
        RuntimePrincipal(
            kind="service",
            principal_id="wrong-target",
            granted_scopes=("runtime:tool:reconcile",),
            allowed_target_refs=("external:other",),
        ),
    ],
)
async def test_reconciliation_service_denies_before_adapter_or_ledger(
    tmp_path: Any,
    principal: RuntimePrincipal,
) -> None:
    api = _reconciliation_api()
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    target_ref = "external:target:2"
    await _unknown_call(
        ledger,
        run_id="run-reconcile-denied",
        call_id="call-reconcile-denied",
        target_ref=target_ref,
    )

    class _Adapter:
        adapter_id = "test-provider"
        idempotent = True
        calls = 0

        def supports(self, call: Any) -> bool:
            return True

        async def reconcile(self, call: Any, command: Any) -> Any:
            self.calls += 1
            raise AssertionError("unauthorized adapter call")

    adapter = _Adapter()
    coordinator = api.ReconciliationCoordinator(ledger=ledger)
    with pytest.raises(PermissionError):
        await coordinator.resolve_unknown(
            call_id="call-reconcile-denied",
            decision="confirmed_not_applied",
            principal=principal,
            evidence_ref="operator:ticket:2002",
            operator_note="confirmed absent",
            adapter=adapter,
        )
    reconciliation = await ledger.get_tool_reconciliation(
        "call-reconcile-denied"
    )
    run = await ledger.get_run("run-reconcile-denied")
    await ledger.close()

    assert adapter.calls == 0
    assert reconciliation is None
    assert run is not None and run.status == "waiting_external"


async def test_onebot_manual_attestation_resolves_without_provider_replay(
    tmp_path: Any,
) -> None:
    api = _reconciliation_api()
    adapter_type = getattr(api, "OneBotManualAttestationAdapter", None)
    assert adapter_type is not None, "OneBot attestation adapter is missing"
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    target_ref = "onebot:group:100:message:9001:reaction:66"
    await _unknown_call(
        ledger,
        run_id="run-onebot-attestation",
        call_id="call-onebot-attestation",
        target_ref=target_ref,
        owner="qq_interaction",
        tool_name="react_to_message",
    )
    resolver = api.ReconciliationCoordinator(ledger=ledger)
    resolution = await resolver.resolve_unknown(
        call_id="call-onebot-attestation",
        decision="confirmed_not_applied",
        principal=RuntimePrincipal(
            kind="service",
            principal_id="onebot-operator",
            granted_scopes=("runtime:tool:reconcile",),
            allowed_target_refs=(target_ref,),
        ),
        evidence_ref="operator:ticket:onebot-2001",
        operator_note="NapCat logs and target message confirm no reaction",
        adapter=adapter_type(),
    )
    call = await ledger.get_tool_call("call-onebot-attestation")
    run = await ledger.get_run("run-onebot-attestation")
    await ledger.close()

    assert resolution.adapter_id == "onebot-manual-attestation-v1"
    assert resolution.decision == "confirmed_not_applied"
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "running"


async def test_reconciliation_rejects_nonidempotent_or_forged_adapter(
    tmp_path: Any,
) -> None:
    api = _reconciliation_api()
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    target_ref = "external:target:forgery"
    await _unknown_call(
        ledger,
        run_id="run-reconcile-forgery",
        call_id="call-reconcile-forgery",
        target_ref=target_ref,
    )
    principal = RuntimePrincipal(
        kind="service",
        principal_id="operator",
        granted_scopes=("runtime:tool:reconcile",),
        allowed_target_refs=(target_ref,),
    )
    resolver = api.ReconciliationCoordinator(ledger=ledger)

    class _NonIdempotent:
        adapter_id = "non-idempotent"
        idempotent = False

        def supports(self, call: Any) -> bool:
            return True

        async def reconcile(self, call: Any, command: Any) -> Any:
            raise AssertionError("non-idempotent adapter must not run")

    with pytest.raises(ValueError, match="idempotent"):
        await resolver.resolve_unknown(
            call_id="call-reconcile-forgery",
            decision="confirmed_succeeded",
            principal=principal,
            evidence_ref="operator:ticket:forgery",
            operator_note="confirmed",
            adapter=_NonIdempotent(),
        )

    class _ForgedReceipt:
        adapter_id = "forged-receipt"
        idempotent = True

        def supports(self, call: Any) -> bool:
            return True

        async def reconcile(self, call: Any, command: Any) -> Any:
            return api.ReconciliationReceipt(
                decision=command.decision,
                target_ref="external:other-target",
                evidence_ref=command.evidence_ref,
            )

    with pytest.raises(ValueError, match="target mismatch"):
        await resolver.resolve_unknown(
            call_id="call-reconcile-forgery",
            decision="confirmed_succeeded",
            principal=principal,
            evidence_ref="operator:ticket:forgery",
            operator_note="confirmed",
            adapter=_ForgedReceipt(),
        )
    reconciliation = await ledger.get_tool_reconciliation(
        "call-reconcile-forgery"
    )
    run = await ledger.get_run("run-reconcile-forgery")
    await ledger.close()

    assert reconciliation is None
    assert run is not None and run.status == "waiting_external"
