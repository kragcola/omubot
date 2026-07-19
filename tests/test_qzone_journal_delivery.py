"""RED application contracts for safe QZone Journal delivery orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from importlib import import_module
from typing import Any

import pytest

_DRAFT_ID = "journal-draft-20260715-001"
_TEXT = "今天把一直拖着的事情做完了 🌸"
_COOKIE = "uin=o0384801062; p_skey=fake-p-skey; skey=fake-skey"
_P_SKEY = "fake-p-skey"


def _delivery_api() -> Any:
    """Load the future delivery lazily so missing code is an assertion RED."""
    try:
        module = import_module("plugins.qzone_journal.delivery")
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "plugins.qzone_journal",
            "plugins.qzone_journal.delivery",
        }:
            raise
        module = None

    assert module is not None, (
        "plugins.qzone_journal.delivery must exist and implement the frozen "
        "safe JournalDelivery contract"
    )
    required = (
        "BUILTIN_WIRE_PROFILE",
        "DeliveryConfig",
        "DeliveryGateError",
        "JournalDelivery",
        "JournalDraft",
    )
    missing = [name for name in required if not hasattr(module, name)]
    assert not missing, f"QZone delivery public API is missing: {', '.join(missing)}"
    return module


@dataclass(frozen=True, slots=True)
class _Profile:
    profile_id: str = "validated-text-v1"
    validated: bool = True


class _Store:
    def __init__(
        self,
        api: Any,
        *,
        status: str = "approved",
        events: list[str] | None = None,
        is_tip: bool = True,
        approval_scope: str = "live",
    ) -> None:
        self._api = api
        self.status = status
        self.events = events if events is not None else []
        self.remote_id: str | None = None
        self.unknown_reason = ""
        self.rejection_reason = ""
        self.is_tip = is_tip
        self.approval_scope = approval_scope
        self.tip_calls: list[str] = []

    def _draft(self) -> Any:
        return self._api.JournalDraft(
            draft_id=_DRAFT_ID,
            text=_TEXT,
            status=self.status,
            approval_scope=self.approval_scope,
            source="event_replan",
            subject_kind="self",
            privacy="public",
            source_summary=_TEXT,
        )

    async def get(self, draft_id: str) -> Any:
        assert draft_id == _DRAFT_ID
        return self._draft()

    async def is_lineage_tip(self, draft_id: str) -> bool:
        assert draft_id == _DRAFT_ID
        self.tip_calls.append(draft_id)
        self.events.append("store.is_lineage_tip")
        return self.is_tip

    async def claim(self, draft_id: str) -> Any:
        assert draft_id == _DRAFT_ID
        assert self.status == "approved"
        self.events.append("store.claim")
        self.status = "dispatching"
        return self._draft()

    async def mark_published(self, draft_id: str, *, remote_id: str | None = None) -> Any:
        assert draft_id == _DRAFT_ID
        assert self.status == "dispatching"
        self.events.append("store.mark_published")
        self.status = "published"
        self.remote_id = remote_id
        return self._draft()

    async def mark_unknown(self, draft_id: str, *, reason: str) -> Any:
        assert draft_id == _DRAFT_ID
        assert self.status == "dispatching"
        self.events.append("store.mark_unknown")
        self.status = "unknown"
        self.unknown_reason = reason
        return self._draft()

    async def reject(self, draft_id: str, *, reason: str) -> Any:
        assert draft_id == _DRAFT_ID
        self.events.append("store.reject")
        self.status = "rejected"
        self.rejection_reason = reason
        return self._draft()

    async def list(self, *, status: str | None = None) -> list[Any]:
        draft = self._draft()
        if status is not None and status != draft.status:
            return []
        return [draft]


class _CredentialSource:
    def __init__(self, *, events: list[str] | None = None, error: BaseException | None = None) -> None:
        self.events = events if events is not None else []
        self.error = error
        self.calls = 0

    async def acquire(self) -> dict[str, Any]:
        self.calls += 1
        self.events.append("credential.acquire")
        if self.error is not None:
            raise self.error
        return {
            "uin": "384801062",
            "cookie_header": _COOKIE,
            "p_skey": _P_SKEY,
            "g_tk": 123456789,
        }


class _Transport:
    def __init__(
        self,
        *,
        events: list[str] | None = None,
        publish_error: BaseException | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.publish_error = publish_error
        self.describe_calls: list[dict[str, Any]] = []
        self.dry_run_calls: list[dict[str, Any]] = []
        self.http_calls: list[dict[str, Any]] = []

    def describe(self, *, profile: Any, text: str) -> dict[str, Any]:
        self.events.append("transport.describe")
        self.describe_calls.append({"profile": profile, "text": text})
        return {
            "profile_id": profile.profile_id,
            "validated": profile.validated,
            "content_chars": len(text),
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    def dry_run(self, *, profile: Any, credentials: Any, text: str) -> dict[str, Any]:
        self.events.append("transport.dry_run")
        self.dry_run_calls.append({
            "profile": profile,
            "credentials": credentials,
            "text": text,
        })
        return {
            "method": "POST",
            "host": "user.qzone.qq.com",
            "path": "/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6",
            "content_chars": len(text),
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    async def publish(self, *, profile: Any, credentials: Any, text: str) -> dict[str, Any]:
        self.events.append("transport.publish")
        self.http_calls.append({
            "profile": profile,
            "credentials": credentials,
            "text": text,
        })
        if self.publish_error is not None:
            raise self.publish_error
        return {"status": "published", "remote_id": "qzone-tid-fixture"}


def _config(
    api: Any,
    *,
    enabled: bool = True,
    dry_run: bool = False,
    allow_live_publish: bool = True,
    allowed_live_uins: list[str] | None = None,
) -> Any:
    if allowed_live_uins is None:
        allowed_live_uins = ["384801062"] if allow_live_publish and not dry_run else []
    return api.DeliveryConfig(
        enabled=enabled,
        dry_run=dry_run,
        allow_live_publish=allow_live_publish,
        allowed_live_uins=list(allowed_live_uins),
    )


def _delivery(
    api: Any,
    *,
    store: _Store,
    credentials: _CredentialSource,
    transport: _Transport,
    config: Any | None = None,
    profile: Any | None = None,
) -> Any:
    return api.JournalDelivery(
        config=config or _config(api),
        store=store,
        credential_source=credentials,
        transport=transport,
        profile=profile or _Profile(),
    )


async def test_approved_dry_run_uses_ephemeral_credentials_without_mutating_store() -> None:
    api = _delivery_api()
    store = _Store(api)
    credentials = _CredentialSource()
    transport = _Transport()
    delivery = _delivery(
        api,
        store=store,
        credentials=credentials,
        transport=transport,
        config=_config(api, dry_run=True),
    )

    descriptor = await delivery.deliver(_DRAFT_ID)

    assert credentials.calls == 1
    assert len(transport.dry_run_calls) == 1
    assert transport.http_calls == []
    assert store.status == "approved"
    serialized = json.dumps(descriptor, ensure_ascii=False, sort_keys=True)
    assert _TEXT not in serialized
    assert _COOKIE not in serialized
    assert _P_SKEY not in serialized


async def test_unverified_dry_run_is_profile_only_and_reads_no_credentials() -> None:
    api = _delivery_api()
    store = _Store(api)
    credentials = _CredentialSource()
    transport = _Transport()
    delivery = _delivery(
        api,
        store=store,
        credentials=credentials,
        transport=transport,
        config=_config(api, dry_run=True),
        profile=api.BUILTIN_WIRE_PROFILE,
    )

    descriptor = await delivery.deliver(_DRAFT_ID)

    assert descriptor["validated"] is False
    assert credentials.calls == 0
    assert len(transport.describe_calls) == 1
    assert transport.dry_run_calls == []
    assert transport.http_calls == []
    assert store.status == "approved"


async def test_unapproved_or_disabled_delivery_never_acquires_credentials() -> None:
    api = _delivery_api()
    cases = [
        (_Store(api, status="draft"), _config(api, enabled=True)),
        (_Store(api, status="approved"), _config(api, enabled=False)),
    ]

    for store, config in cases:
        credentials = _CredentialSource()
        transport = _Transport()
        delivery = _delivery(
            api,
            store=store,
            credentials=credentials,
            transport=transport,
            config=config,
        )
        with pytest.raises(api.DeliveryGateError):
            await delivery.deliver(_DRAFT_ID)
        assert credentials.calls == 0
        assert transport.dry_run_calls == []
        assert transport.http_calls == []


async def test_live_http_requires_all_four_gates_and_builtin_profile_is_unverified() -> None:
    api = _delivery_api()
    assert api.BUILTIN_WIRE_PROFILE.validated is False
    cases = [
        (_config(api, enabled=False), _Profile()),
        (_config(api, dry_run=True), _Profile()),
        (_config(api, allow_live_publish=False), _Profile()),
        (_config(api), api.BUILTIN_WIRE_PROFILE),
    ]

    for config, profile in cases:
        store = _Store(api)
        credentials = _CredentialSource()
        transport = _Transport()
        delivery = _delivery(
            api,
            store=store,
            credentials=credentials,
            transport=transport,
            config=config,
            profile=profile,
        )
        if config.dry_run:
            await delivery.deliver(_DRAFT_ID)
        else:
            with pytest.raises(api.DeliveryGateError):
                await delivery.deliver(_DRAFT_ID)
        assert transport.http_calls == []
        assert store.status == "approved"


async def test_live_success_claims_before_http_and_only_then_marks_published() -> None:
    api = _delivery_api()
    events: list[str] = []
    store = _Store(api, events=events)
    credentials = _CredentialSource(events=events)
    transport = _Transport(events=events)
    delivery = _delivery(api, store=store, credentials=credentials, transport=transport)

    result = await delivery.deliver(_DRAFT_ID)

    assert result.status == "published"
    assert store.status == "published"
    assert store.remote_id == "qzone-tid-fixture"
    assert events == [
        "store.is_lineage_tip",
        "credential.acquire",
        "store.claim",
        "transport.publish",
        "store.mark_published",
    ]


async def test_credential_failure_keeps_approved_and_never_claims_or_calls_http() -> None:
    api = _delivery_api()
    events: list[str] = []
    store = _Store(api, events=events)
    credentials = _CredentialSource(events=events, error=RuntimeError("credentials unavailable"))
    transport = _Transport(events=events)
    delivery = _delivery(api, store=store, credentials=credentials, transport=transport)

    with pytest.raises(RuntimeError, match="credentials unavailable"):
        await delivery.deliver(_DRAFT_ID)

    assert store.status == "approved"
    assert transport.http_calls == []
    assert events == ["store.is_lineage_tip", "credential.acquire"]


async def test_live_uin_allowlist_checked_after_credentials_before_claim_and_http() -> None:
    api = _delivery_api()
    cases = [
        [],
        ["999999999"],
    ]
    for allowed in cases:
        events: list[str] = []
        store = _Store(api, events=events)
        credentials = _CredentialSource(events=events)
        transport = _Transport(events=events)
        delivery = _delivery(
            api,
            store=store,
            credentials=credentials,
            transport=transport,
            config=_config(api, allowed_live_uins=allowed),
        )

        with pytest.raises(api.DeliveryGateError, match=r"allowlist|allowed|uin"):
            await delivery.deliver(_DRAFT_ID)

        assert store.status == "approved"
        assert transport.http_calls == []
        assert "store.claim" not in events
        assert events == ["store.is_lineage_tip", "credential.acquire"]


async def test_ambiguous_exception_after_dispatch_marks_unknown_not_approved() -> None:
    api = _delivery_api()
    events: list[str] = []
    store = _Store(api, events=events)
    credentials = _CredentialSource(events=events)
    transport = _Transport(events=events, publish_error=OSError("read timeout after dispatch"))
    delivery = _delivery(api, store=store, credentials=credentials, transport=transport)

    with pytest.raises(OSError, match="read timeout after dispatch"):
        await delivery.deliver(_DRAFT_ID)

    assert store.status == "unknown"
    assert "read timeout" in store.unknown_reason
    assert events[-1] == "store.mark_unknown"
    assert "store.mark_published" not in events


async def test_cancelled_after_dispatch_marks_unknown_then_propagates_cancellation() -> None:
    api = _delivery_api()
    events: list[str] = []
    store = _Store(api, events=events)
    credentials = _CredentialSource(events=events)
    transport = _Transport(events=events, publish_error=asyncio.CancelledError())
    delivery = _delivery(api, store=store, credentials=credentials, transport=transport)

    with pytest.raises(asyncio.CancelledError):
        await delivery.deliver(_DRAFT_ID)

    assert store.status == "unknown"
    assert store.unknown_reason
    assert events[-1] == "store.mark_unknown"
    assert "store.mark_published" not in events


async def test_transport_failed_or_ambiguous_marks_unknown_not_published() -> None:
    """Sanitized non-success dicts from transport must keep claim/unknown safety."""
    api = _delivery_api()

    class _NonSuccessTransport(_Transport):
        def __init__(self, payload: dict[str, Any], *, events: list[str] | None = None) -> None:
            super().__init__(events=events)
            self._payload = payload

        async def publish(self, *, profile: Any, credentials: Any, text: str) -> dict[str, Any]:
            self.events.append("transport.publish")
            self.http_calls.append({
                "profile": profile,
                "credentials": credentials,
                "text": text,
            })
            return dict(self._payload)

    for payload in (
        {"status": "failed", "reason": "cgi_error"},
        {"status": "ambiguous", "reason": "missing_remote_id"},
        {"status": "ambiguous", "reason": "html_body"},
        # HTTP-200-shaped but not explicitly published — never success.
        {"ok": True, "tid": "must-not-count"},
    ):
        events: list[str] = []
        store = _Store(api, events=events)
        credentials = _CredentialSource(events=events)
        transport = _NonSuccessTransport(payload, events=events)
        delivery = _delivery(api, store=store, credentials=credentials, transport=transport)

        with pytest.raises(RuntimeError, match="not explicitly successful"):
            await delivery.deliver(_DRAFT_ID)

        assert store.status == "unknown"
        assert "store.mark_published" not in events
        assert events[-1] == "store.mark_unknown"
        assert store.unknown_reason == "unverified_publish_response"


async def test_non_tip_approved_dry_run_never_builds_descriptor_or_touches_credentials() -> None:
    """v0.8.1 M3: approved non-tip fails closed before describe/credential/transport."""
    api = _delivery_api()
    from plugins.qzone_journal.store import InvalidDraftTransitionError

    events: list[str] = []
    store = _Store(api, status="approved", events=events, is_tip=False)
    credentials = _CredentialSource(events=events)
    transport = _Transport(events=events)
    delivery = _delivery(
        api,
        store=store,
        credentials=credentials,
        transport=transport,
        config=_config(api, dry_run=True),
        profile=api.BUILTIN_WIRE_PROFILE,
    )

    with pytest.raises((api.DeliveryGateError, InvalidDraftTransitionError)):
        await delivery.deliver(_DRAFT_ID)

    assert credentials.calls == 0
    assert transport.describe_calls == []
    assert transport.dry_run_calls == []
    assert transport.http_calls == []
    assert "transport.describe" not in events
    assert "credential.acquire" not in events
    assert store.status == "approved"


async def test_published_is_idempotent_and_reject_list_delegate_without_network() -> None:
    api = _delivery_api()
    published_store = _Store(api, status="published")
    published_credentials = _CredentialSource()
    published_transport = _Transport()
    published_delivery = _delivery(
        api,
        store=published_store,
        credentials=published_credentials,
        transport=published_transport,
    )

    await published_delivery.deliver(_DRAFT_ID)

    assert published_credentials.calls == 0
    assert published_transport.http_calls == []
    assert published_store.status == "published"

    managed_store = _Store(api)
    managed_credentials = _CredentialSource()
    managed_transport = _Transport()
    managed_delivery = _delivery(
        api,
        store=managed_store,
        credentials=managed_credentials,
        transport=managed_transport,
    )
    rejected = await managed_delivery.reject(_DRAFT_ID, reason="privacy review rejected")
    listed = await managed_delivery.list(status="rejected")

    assert rejected.status == "rejected"
    assert managed_store.rejection_reason == "privacy review rejected"
    assert [draft.draft_id for draft in listed] == [_DRAFT_ID]
    assert managed_credentials.calls == 0
    assert managed_transport.http_calls == []
