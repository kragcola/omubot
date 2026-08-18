"""Credential-authenticated Admin operator actions for Agent Runtime v2."""

from __future__ import annotations

from collections.abc import Sequence

from services.agent_runtime.admin_actions import (
    OfflineAdminActionsV1,
    OperatorIdentityV1,
    WorldbookProposalCommitterV1,
)
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.operator_auth import OperatorAuthorizationStoreV1
from services.agent_runtime.reconciliation import ReconciliationAdapter
from services.memory.governance_store import MemoryGovernanceStore
from services.worldbook.governance_store import WorldbookGovernanceStore


class AdminOperatorAuthenticationError(PermissionError):
    """Raised when an HTTP operator cannot be authenticated from durable state."""


class AdminOperatorActionsFactoryV1:
    """Create request-scoped actions from an explicit named credential.

    The factory deliberately knows nothing about browser sessions. Its caller
    must present an operator id and credential on every sensitive request.
    """

    def __init__(
        self,
        *,
        runtime_source: AgentRuntimeLedger,
        memory_source: MemoryGovernanceStore,
        worldbook_source: WorldbookGovernanceStore | None = None,
        worldbook_committer: WorldbookProposalCommitterV1 | None = None,
        operator_source: OperatorAuthorizationStoreV1,
        reconciliation_adapters: Sequence[ReconciliationAdapter] = (),
    ) -> None:
        if runtime_source is None:
            raise ValueError("runtime source is required")
        if memory_source is None:
            raise ValueError("memory source is required")
        if operator_source is None:
            raise ValueError("operator source is required")
        self._runtime_source = runtime_source
        self._memory_source = memory_source
        self._worldbook_source = worldbook_source
        self._worldbook_committer = worldbook_committer
        self._operator_source = operator_source
        self._reconciliation_adapters = tuple(reconciliation_adapters)

    async def authenticate(
        self,
        *,
        operator_id: str,
        credential: str,
    ) -> OfflineAdminActionsV1:
        """Authenticate one request and bind each action to current exact ACLs."""

        try:
            authorized = await self._operator_source.authenticate(
                operator_id=operator_id,
                credential=credential,
            )
        except ValueError as exc:
            raise AdminOperatorAuthenticationError(
                "operator authentication failed"
            ) from exc
        if authorized is None:
            raise AdminOperatorAuthenticationError("operator authentication failed")

        async def authorize_resource(scope: str, resource_ref: str) -> bool:
            return await self._operator_source.allows(
                authorized,
                scope=scope,
                resource_ref=resource_ref,
            )

        return OfflineAdminActionsV1(
            runtime_source=self._runtime_source,
            memory_source=self._memory_source,
            worldbook_source=self._worldbook_source,
            worldbook_committer=self._worldbook_committer,
            operator=OperatorIdentityV1(
                operator_id=authorized.operator_id,
                granted_scopes=authorized.granted_scopes,
            ),
            reconciliation_adapters=self._reconciliation_adapters,
            resource_authorizer=authorize_resource,
        )


__all__ = ["AdminOperatorActionsFactoryV1", "AdminOperatorAuthenticationError"]
