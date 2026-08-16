"""Authoritative OneBot host ingress for Agent Runtime v2.

The host owns the raw OneBot identity.  This adapter converts that identity
into an immutable invocation before a selected Runtime v2 dispatcher can see a
model turn; it never derives identity from prompt text or a browser session.
"""

from __future__ import annotations

import hashlib
import json

from loguru import logger

from services.agent_runtime.invocation_store import (
    AuthoritativeTriggerV1,
    TrustedInvocationRecordV1,
    TrustedInvocationStoreV1,
)
from services.tools.registry import ToolRegistry

_L = logger.bind(channel="agent_runtime_ingress")


def _opaque_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AuthoritativeHostTriggerIngressV1:
    """Persist a trusted invocation from one concrete inbound OneBot message."""

    def __init__(
        self,
        *,
        invocations: TrustedInvocationStoreV1,
        registry: ToolRegistry,
        granted_scopes: tuple[str, ...],
        allowed_target_refs: tuple[str, ...],
    ) -> None:
        if not isinstance(invocations, TrustedInvocationStoreV1):
            raise TypeError("invocations must be a TrustedInvocationStoreV1")
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        self._invocations = invocations
        self._registry = registry
        self._granted_scopes = tuple(granted_scopes)
        self._allowed_target_refs = tuple(allowed_target_refs)

    async def record_onebot_message(
        self,
        *,
        group_id: str | None,
        user_id: str,
        message_id: str,
    ) -> TrustedInvocationRecordV1:
        """Persist and return the immutable record for this host message."""

        registry_generation, _catalog = self._registry.snapshot_catalog()
        session_id = f"group_{group_id}" if group_id else f"private_{user_id}"
        trigger = AuthoritativeTriggerV1.from_onebot_message(
            group_id=group_id,
            user_id=user_id,
            message_id=message_id,
            session_id=session_id,
            registry_generation=registry_generation,
            granted_scopes=self._granted_scopes,
            allowed_target_refs=self._allowed_target_refs,
        )
        record = await self._invocations.record(trigger)
        _L.info(
            "trusted invocation witness | invocation_id={} registry_generation={} "
            "scope_digest={} target_digest={} trigger_digest={}",
            record.invocation_id,
            record.registry_generation,
            _opaque_digest(record.granted_scopes),
            _opaque_digest(record.allowed_target_refs),
            _opaque_digest(record.trigger_ref),
        )
        return record


__all__ = ["AuthoritativeHostTriggerIngressV1"]
