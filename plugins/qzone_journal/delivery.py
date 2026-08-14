"""Safe application service for QZone Journal review and delivery."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from plugins.qzone_journal.transport import WireProfile

BUILTIN_WIRE_PROFILE = WireProfile(
    profile_id="qzone-text-v1-unverified",
    endpoint=(
        "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/"
        "cgi-bin/emotion_cgi_publish_v6"
    ),
    validated=False,
    content_field="con",
    uin_field="hostuin",
    static_form_fields={
        "format": "json",
        "feedversion": "1",
        "ver": "1",
        "ugc_right": "1",
    },
)


class DeliveryPreDispatchError(RuntimeError):
    """Delivery stopped before the QZone publish provider was invoked."""


class DeliveryGateError(DeliveryPreDispatchError):
    """Local policy rejected delivery before any irreversible network call."""


class DeliveryPostDispatchError(RuntimeError):
    """Delivery failed after the provider invocation became ambiguous."""


class _UnverifiedPublishResponseError(RuntimeError):
    """The provider response did not prove that publication succeeded."""


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    enabled: bool = False
    dry_run: bool = True
    allow_live_publish: bool = False
    allowed_live_uins: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        raw = self.allowed_live_uins
        if isinstance(raw, str):
            values: Sequence[str] = [raw]
        else:
            values = list(raw or ())
        normalized: list[str] = []
        seen: set[str] = set()
        for item in values:
            uin = str(item or "").strip()
            if not uin or uin in seen:
                continue
            seen.add(uin)
            normalized.append(uin)
        object.__setattr__(self, "allowed_live_uins", tuple(normalized))


@dataclass(frozen=True, slots=True)
class JournalDraft:
    draft_id: str
    text: str
    status: str
    approval_scope: str = "dry_run"
    source: str = ""
    subject_kind: str = ""
    privacy: str = ""
    source_summary: str = ""


def _as_delivery_draft(value: Any) -> JournalDraft:
    return JournalDraft(
        draft_id=str(getattr(value, "draft_id", "") or ""),
        text=str(
            getattr(value, "text", getattr(value, "content", "")) or ""
        ),
        status=str(getattr(value, "status", "") or ""),
        approval_scope=str(getattr(value, "approval_scope", "") or ""),
        source=str(getattr(value, "source", "") or ""),
        subject_kind=str(getattr(value, "subject_kind", "") or ""),
        privacy=str(getattr(value, "privacy", "") or ""),
        source_summary=str(getattr(value, "source_summary", "") or ""),
    )


def _credential_uin(credentials: Any) -> str:
    if isinstance(credentials, dict):
        return str(credentials.get("uin", "") or "").strip()
    return str(getattr(credentials, "uin", "") or "").strip()


class JournalDelivery:
    """Coordinate local gates, ephemeral credentials and outbox transitions."""

    def __init__(
        self,
        *,
        config: DeliveryConfig,
        store: Any,
        credential_source: Any,
        transport: Any,
        profile: WireProfile = BUILTIN_WIRE_PROFILE,
    ) -> None:
        self._config = config
        self._store = store
        self._credential_source = credential_source
        self._transport = transport
        self._profile = profile

    async def deliver(self, draft_id: str) -> JournalDraft | dict[str, Any]:
        current_raw = await self._await_pre_dispatch(
            self._store.get(draft_id),
            safe_message="QZone journal lookup failed before dispatch",
        )
        if current_raw is None:
            raise DeliveryGateError("QZone journal draft was not found")
        current = _as_delivery_draft(current_raw)
        if current.status == "published":
            return current
        if current.status != "approved":
            raise DeliveryGateError("QZone journal draft is not approved")
        # Tip CAS before descriptor construction, credentials, or transport.
        tip_fn = getattr(self._store, "is_lineage_tip", None)
        if not callable(tip_fn):
            raise DeliveryGateError("QZone journal store lacks is_lineage_tip")
        tip_call = cast(Callable[[str], Awaitable[bool]], tip_fn)
        is_tip = await self._await_pre_dispatch(
            tip_call(draft_id),
            safe_message="QZone journal lineage check failed before dispatch",
        )
        if not bool(is_tip):
            raise DeliveryGateError(
                "cannot deliver QZone draft: not lineage tip"
            )
        if not self._config.enabled:
            raise DeliveryGateError("QZone Journal is disabled")

        if self._config.dry_run:
            if not bool(getattr(self._profile, "validated", False)):
                describe = getattr(self._transport, "describe", None)
                if not callable(describe):
                    raise DeliveryGateError(
                        "QZone transport has no profile-only dry-run"
                    )
                describe_fn = cast(Callable[..., dict[str, Any]], describe)
                return describe_fn(profile=self._profile, text=current.text)
            credentials = await self._await_pre_dispatch(
                self._credential_source.acquire(),
                safe_message="QZone credentials are unavailable before dispatch",
            )
            try:
                return self._transport.dry_run(
                    profile=self._profile,
                    credentials=credentials,
                    text=current.text,
                )
            except DeliveryPreDispatchError:
                raise
            except Exception as exc:
                raise DeliveryPreDispatchError(
                    "QZone dry-run failed before dispatch"
                ) from exc

        if not self._config.allow_live_publish:
            raise DeliveryGateError("live QZone publish is not allowed")
        if not bool(getattr(self._profile, "validated", False)):
            raise DeliveryGateError("QZone wire profile is not validated")
        if current.approval_scope != "live":
            raise DeliveryGateError(
                "QZone journal draft lacks live approval scope"
            )
        if (
            not current.source
            or current.subject_kind not in {"self", "fiction", "factual"}
            or current.privacy != "public"
        ):
            raise DeliveryGateError(
                "QZone journal draft failed live authenticity validation"
            )
        if (
            current.subject_kind == "self"
            and current.source in {"dream_reflection", "schedule_generator"}
        ):
            raise DeliveryGateError(
                "QZone journal draft failed live authenticity validation"
            )
        if (
            current.subject_kind == "fiction"
            and not current.text.startswith("虚构故事里，")
        ):
            raise DeliveryGateError(
                "QZone journal draft failed live authenticity validation"
            )
        if (
            current.subject_kind == "factual"
            and (
                not current.source_summary.strip()
                or current.text.strip() != current.source_summary.strip()
            )
        ):
            raise DeliveryGateError(
                "QZone journal draft failed live authenticity validation"
            )

        credentials = await self._await_pre_dispatch(
            self._credential_source.acquire(),
            safe_message="QZone credentials are unavailable before dispatch",
        )
        uin = _credential_uin(credentials)
        allowed = set(self._config.allowed_live_uins)
        if not allowed or uin not in allowed:
            raise DeliveryGateError(
                "live QZone publish UIN is not on the allowed_live_uins allowlist"
            )

        claimed_raw = await self._claim_with_phase(draft_id)
        if claimed_raw is None:
            raise DeliveryGateError("QZone journal draft could not be claimed")
        claimed = _as_delivery_draft(claimed_raw)

        try:
            response = await self._transport.publish(
                profile=self._profile,
                credentials=credentials,
                text=claimed.text,
            )
            if (
                not isinstance(response, dict)
                or str(response.get("status", "") or "") != "published"
            ):
                raise _UnverifiedPublishResponseError(
                    "QZone publish response was not explicitly successful"
                )
            remote_id = str(
                response.get("remote_id", response.get("tid", "")) or ""
            )
            published = await self._mark_published(
                draft_id,
                remote_id=remote_id,
            )
        except BaseException as exc:
            reason = (
                "unverified_publish_response"
                if isinstance(exc, _UnverifiedPublishResponseError)
                else self._safe_error_reason(exc)
            )
            cancelled_during_cleanup = await self._mark_unknown_shielded(
                draft_id,
                reason=reason,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            if cancelled_during_cleanup:
                raise asyncio.CancelledError() from exc
            if isinstance(exc, DeliveryPostDispatchError):
                raise
            raise DeliveryPostDispatchError(
                "QZone publish outcome is unknown after dispatch"
            ) from exc
        return _as_delivery_draft(published)

    async def reject(self, draft_id: str, *, reason: str) -> JournalDraft:
        rejected = await self._store.reject(draft_id, reason=reason)
        return _as_delivery_draft(rejected)

    async def list(self, *, status: str | None = None) -> list[JournalDraft]:
        rows = await self._store.list(status=status)
        return [_as_delivery_draft(row) for row in rows]

    async def _claim(self, draft_id: str) -> Any:
        claim = getattr(self._store, "claim", None)
        if callable(claim):
            claim_fn = cast(Callable[[str], Awaitable[Any]], claim)
            return await claim_fn(draft_id)
        claim_for_publish = getattr(self._store, "claim_for_publish", None)
        if not callable(claim_for_publish):
            raise RuntimeError("QZone journal store has no publish claim method")
        claim_fn = cast(Callable[[str], Awaitable[Any]], claim_for_publish)
        return await claim_fn(draft_id)

    async def _claim_with_phase(self, draft_id: str) -> Any:
        try:
            return await self._claim(draft_id)
        except BaseException as exc:
            try:
                current_raw, cancelled_during_inspection = (
                    await self._await_shielded(self._store.get(draft_id))
                )
                current = (
                    _as_delivery_draft(current_raw)
                    if current_raw is not None
                    else None
                )
            except BaseException as inspect_exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise exc from inspect_exc
                if isinstance(inspect_exc, asyncio.CancelledError):
                    raise asyncio.CancelledError() from exc
                raise DeliveryPostDispatchError(
                    "QZone journal claim outcome requires reconciliation"
                ) from exc
            if current is not None and current.status == "dispatching":
                try:
                    cancelled_during_cleanup = await self._mark_unknown_shielded(
                        draft_id,
                        reason="claim_outcome_unknown",
                    )
                except BaseException as cleanup_exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise exc from cleanup_exc
                    if isinstance(cleanup_exc, asyncio.CancelledError):
                        raise asyncio.CancelledError() from exc
                    raise DeliveryPostDispatchError(
                        "QZone journal claim outcome requires reconciliation"
                    ) from cleanup_exc
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if cancelled_during_inspection or cancelled_during_cleanup:
                    raise asyncio.CancelledError() from exc
                raise DeliveryPostDispatchError(
                    "QZone journal claim outcome requires reconciliation"
                ) from exc
            if isinstance(exc, asyncio.CancelledError):
                raise
            if cancelled_during_inspection:
                raise asyncio.CancelledError() from exc
            if current is None or current.status != "approved":
                raise DeliveryPostDispatchError(
                    "QZone journal claim outcome requires reconciliation"
                ) from exc
            raise DeliveryPreDispatchError(
                "QZone journal claim failed before dispatch"
            ) from exc

    @staticmethod
    async def _await_pre_dispatch(
        awaitable: Awaitable[Any],
        *,
        safe_message: str,
    ) -> Any:
        try:
            return await awaitable
        except asyncio.CancelledError:
            raise
        except DeliveryPreDispatchError:
            raise
        except Exception as exc:
            raise DeliveryPreDispatchError(safe_message) from exc

    async def _mark_published(self, draft_id: str, *, remote_id: str) -> Any:
        method = self._store.mark_published
        parameters = inspect.signature(method).parameters
        if "external_post_id" in parameters:
            return await method(draft_id, external_post_id=remote_id)
        return await method(draft_id, remote_id=remote_id or None)

    @staticmethod
    async def _await_shielded(
        awaitable: Awaitable[Any],
    ) -> tuple[Any, bool]:
        task = asyncio.ensure_future(awaitable)
        cancelled_during_wait = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.done() and task.cancelled():
                    raise
                cancelled_during_wait = True
        try:
            return task.result(), cancelled_during_wait
        except BaseException as exc:
            if cancelled_during_wait:
                raise asyncio.CancelledError() from exc
            raise

    async def _mark_unknown_shielded(
        self,
        draft_id: str,
        *,
        reason: str,
    ) -> bool:
        _, cancelled_during_cleanup = await self._await_shielded(
            self._store.mark_unknown(draft_id, reason=reason)
        )
        return cancelled_during_cleanup

    @staticmethod
    def _safe_error_reason(exc: BaseException) -> str:
        return type(exc).__name__


__all__ = [
    "BUILTIN_WIRE_PROFILE",
    "DeliveryConfig",
    "DeliveryGateError",
    "DeliveryPostDispatchError",
    "DeliveryPreDispatchError",
    "JournalDelivery",
    "JournalDraft",
]
