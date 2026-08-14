"""Authoritative trigger persistence and trusted context reconstruction contracts."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest


def _api() -> tuple[type[Any], type[Any]]:
    module_name = "services.agent_runtime.invocation_store"
    if importlib.util.find_spec(module_name) is None:
        pytest.fail("TrustedInvocationStoreV1 is required for production activation")
    module = importlib.import_module(module_name)
    trigger_type = getattr(module, "AuthoritativeTriggerV1", None)
    store_type = getattr(module, "TrustedInvocationStoreV1", None)
    assert isinstance(trigger_type, type), "AuthoritativeTriggerV1 is required"
    assert isinstance(store_type, type), "TrustedInvocationStoreV1 is required"
    return trigger_type, store_type


def _trigger(trigger_type: type[Any]) -> Any:
    return trigger_type.from_onebot_message(
        group_id="20002",
        user_id="10001",
        message_id="30003",
        session_id="group_20002",
        registry_generation=17,
        allowed_target_refs=(
            "network:web-search",
            "affection:group:20002:user:10001",
        ),
    )


@pytest.mark.asyncio
async def test_authoritative_message_trigger_round_trips_to_a_trusted_context(
    tmp_path: Path,
) -> None:
    trigger_type, store_type = _api()
    store = store_type(tmp_path / "invocations.db")
    await store.init()
    try:
        record = await store.record(_trigger(trigger_type))
        reconstructed = await store.reconstruct(record.invocation_id, bot="trusted-bot")

        assert record.trigger_type == "message"
        assert record.trigger_ref == "onebot:group:20002:message:30003"
        assert record.registry_generation == 17
        assert reconstructed.principal.kind == "onebot_user"
        assert reconstructed.principal.principal_id == "10001"
        assert reconstructed.principal.allowed_target_refs == (
            "affection:group:20002:user:10001",
            "network:web-search",
        )
        assert reconstructed.trusted_context.bot == "trusted-bot"
        assert reconstructed.trusted_context.user_id == "10001"
        assert reconstructed.trusted_context.group_id == "20002"
        assert reconstructed.trusted_context.session_id == "group_20002"
        assert reconstructed.trusted_context.extra == {
            "onebot_message_refs": ("onebot:group:20002:message:30003",)
        }
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_same_authoritative_trigger_is_idempotent_but_conflicting_claim_is_rejected(
    tmp_path: Path,
) -> None:
    trigger_type, store_type = _api()
    store = store_type(tmp_path / "invocations.db")
    await store.init()
    try:
        first = await store.record(_trigger(trigger_type))
        same = await store.record(_trigger(trigger_type))
        conflicting = _trigger(trigger_type)
        conflicting = trigger_type.from_onebot_message(
            group_id="20002",
            user_id="10001",
            message_id="30003",
            session_id="group_20002",
            registry_generation=18,
            allowed_target_refs=("network:web-search",),
        )

        assert same == first
        with pytest.raises(ValueError, match=r"conflict|different|immutable"):
            await store.record(conflicting)
    finally:
        await store.close()


def test_trigger_constructor_rejects_noncanonical_identity_and_wildcard_target() -> None:
    trigger_type, _store_type = _api()

    with pytest.raises(ValueError, match=r"group_id|positive|invalid"):
        trigger_type.from_onebot_message(
            group_id="not-a-group",
            user_id="10001",
            message_id="30003",
            session_id="group_20002",
            registry_generation=1,
            allowed_target_refs=("network:web-search",),
        )
    with pytest.raises(ValueError, match=r"target|wildcard|invalid"):
        trigger_type.from_onebot_message(
            group_id="20002",
            user_id="10001",
            message_id="30003",
            session_id="group_20002",
            registry_generation=1,
            allowed_target_refs=("network:*",),
        )


@pytest.mark.asyncio
async def test_close_completes_cleanup_before_cancellation_escapes(tmp_path: Path) -> None:
    _trigger_type, store_type = _api()
    store = store_type(tmp_path / "invocations.db")
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


@pytest.mark.asyncio
async def test_cancelled_worker_lease_acquisition_cleans_committed_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trigger_type, store_type = _api()
    path = tmp_path / "invocations.db"
    first = store_type(path)
    second = store_type(path)
    await first.init()
    await second.init()
    db = first._db
    assert db is not None
    original_commit = db.commit
    commit_finished = asyncio.Event()
    allow_commit_return = asyncio.Event()
    commit_calls = 0

    async def commit_after_persisting_first_lease() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls != 1:
            await original_commit()
            return
        await original_commit()
        commit_finished.set()
        await allow_commit_return.wait()

    monkeypatch.setattr(db, "commit", commit_after_persisting_first_lease)
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    acquire_task = asyncio.create_task(
        first.acquire_worker_lease(
            worker_id="agent-runtime-worker-1",
            lease_ttl_seconds=30,
            now=now,
        )
    )
    try:
        await asyncio.wait_for(commit_finished.wait(), timeout=1.0)
        acquire_task.cancel()
        allow_commit_return.set()

        with pytest.raises(asyncio.CancelledError):
            await acquire_task

        replacement = await second.acquire_worker_lease(
            worker_id="agent-runtime-worker-2",
            lease_ttl_seconds=30,
            now=now,
        )
        assert replacement is not None
        assert await second.release_worker_lease(replacement)
    finally:
        allow_commit_return.set()
        if not acquire_task.done():
            acquire_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await acquire_task
        await second.close()
        await first.close()


@pytest.mark.asyncio
async def test_cancelled_worker_lease_renewal_cleans_committed_rotated_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trigger_type, store_type = _api()
    path = tmp_path / "invocations.db"
    first = store_type(path)
    second = store_type(path)
    await first.init()
    await second.init()
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    initial = await first.acquire_worker_lease(
        worker_id="agent-runtime-worker-1",
        lease_ttl_seconds=30,
        now=now,
    )
    assert initial is not None
    db = first._db
    assert db is not None
    original_commit = db.commit
    commit_finished = asyncio.Event()
    allow_commit_return = asyncio.Event()
    commit_calls = 0

    async def commit_after_persisting_rotated_lease() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls != 1:
            await original_commit()
            return
        await original_commit()
        commit_finished.set()
        await allow_commit_return.wait()

    monkeypatch.setattr(db, "commit", commit_after_persisting_rotated_lease)
    renew_task = asyncio.create_task(
        first.renew_worker_lease(
            initial,
            lease_ttl_seconds=30,
            now=now + timedelta(seconds=1),
        )
    )
    try:
        await asyncio.wait_for(commit_finished.wait(), timeout=1.0)
        renew_task.cancel()
        allow_commit_return.set()

        with pytest.raises(asyncio.CancelledError):
            await renew_task

        replacement = await second.acquire_worker_lease(
            worker_id="agent-runtime-worker-2",
            lease_ttl_seconds=30,
            now=now + timedelta(seconds=1),
        )
        assert replacement is not None
        assert await second.release_worker_lease(replacement)
    finally:
        allow_commit_return.set()
        if not renew_task.done():
            renew_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await renew_task
        await second.close()
        await first.close()
