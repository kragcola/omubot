"""Named operator authentication and exact resource ACL contracts."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _api() -> Any:
    module_name = "services.agent_runtime.operator_auth"
    if importlib.util.find_spec(module_name) is None:
        pytest.fail("OperatorAuthorizationStoreV1 is required for production activation")
    module = importlib.import_module(module_name)
    store_type = getattr(module, "OperatorAuthorizationStoreV1", None)
    assert isinstance(store_type, type), "OperatorAuthorizationStoreV1 is required"
    return store_type


@pytest.mark.asyncio
async def test_operator_authentication_returns_only_store_backed_scopes_and_resources(
    tmp_path: Path,
) -> None:
    store_type = _api()
    store = store_type(tmp_path / "operator.db")
    await store.init()
    try:
        await store.provision(
            operator_id="ops-alice",
            credential="correct-horse-battery-staple-012345",
            granted_scopes=("runtime:tool:approve", "memory:candidate:decide"),
            resource_grants=(
                ("runtime:tool:approve", "tool_call:call-001"),
                ("memory:candidate:decide", "memory_candidate:candidate-001"),
            ),
        )

        authorized = await store.authenticate(
            operator_id="ops-alice",
            credential="correct-horse-battery-staple-012345",
        )
        rejected = await store.authenticate(
            operator_id="ops-alice",
            credential="wrong-credential-012345678901234567890",
        )

        assert authorized is not None
        assert authorized.operator_id == "ops-alice"
        assert authorized.granted_scopes == (
            "memory:candidate:decide",
            "runtime:tool:approve",
        )
        assert await store.allows(
            authorized,
            scope="runtime:tool:approve",
            resource_ref="tool_call:call-001",
        )
        assert not await store.allows(
            authorized,
            scope="runtime:tool:approve",
            resource_ref="tool_call:call-002",
        )
        assert rejected is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_provision_replaces_stale_acl_entries_and_disabled_operator_fails_closed(
    tmp_path: Path,
) -> None:
    store_type = _api()
    store = store_type(tmp_path / "operator.db")
    await store.init()
    try:
        token = "operator-token-012345678901234567890123"
        await store.provision(
            operator_id="ops-bob",
            credential=token,
            granted_scopes=("runtime:tool:reconcile",),
            resource_grants=(("runtime:tool:reconcile", "tool_call:legacy"),),
        )
        await store.provision(
            operator_id="ops-bob",
            credential=token,
            granted_scopes=("runtime:tool:reconcile",),
            resource_grants=(("runtime:tool:reconcile", "tool_call:current"),),
            enabled=False,
        )

        assert await store.authenticate(operator_id="ops-bob", credential=token) is None

        await store.provision(
            operator_id="ops-bob",
            credential=token,
            granted_scopes=("runtime:tool:reconcile",),
            resource_grants=(("runtime:tool:reconcile", "tool_call:current"),),
        )
        authorized = await store.authenticate(operator_id="ops-bob", credential=token)

        assert authorized is not None
        assert not await store.allows(
            authorized,
            scope="runtime:tool:reconcile",
            resource_ref="tool_call:legacy",
        )
        assert await store.allows(
            authorized,
            scope="runtime:tool:reconcile",
            resource_ref="tool_call:current",
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_resource_acl_rejects_wildcards_and_ungranted_scopes(tmp_path: Path) -> None:
    store_type = _api()
    store = store_type(tmp_path / "operator.db")
    await store.init()
    try:
        with pytest.raises(ValueError, match=r"resource|wildcard|invalid"):
            await store.provision(
                operator_id="ops-cara",
                credential="operator-token-012345678901234567890123",
                granted_scopes=("runtime:tool:approve",),
                resource_grants=(("runtime:tool:approve", "tool_call:*"),),
            )

        await store.provision(
            operator_id="ops-cara",
            credential="operator-token-012345678901234567890123",
            granted_scopes=("runtime:tool:approve",),
            resource_grants=(("runtime:tool:approve", "tool_call:call-777"),),
        )
        authorized = await store.authenticate(
            operator_id="ops-cara",
            credential="operator-token-012345678901234567890123",
        )

        assert authorized is not None
        assert not await store.allows(
            authorized,
            scope="runtime:tool:reconcile",
            resource_ref="tool_call:call-777",
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_close_finishes_required_cleanup_before_propagating_cancellation(
    tmp_path: Path,
) -> None:
    store_type = _api()
    store = store_type(tmp_path / "operator.db")
    await store.init()

    close_started = asyncio.Event()
    release_close = asyncio.Event()
    original_connection = store._db
    assert original_connection is not None

    class BlockingConnection:
        async def close(self) -> None:
            close_started.set()
            await release_close.wait()
            await original_connection.close()

    store._db = BlockingConnection()
    close_task = asyncio.create_task(store.close())
    await asyncio.wait_for(close_started.wait(), timeout=1.0)
    close_task.cancel()
    release_close.set()

    with pytest.raises(asyncio.CancelledError):
        await close_task
    assert store._db is None
    await store.close()
