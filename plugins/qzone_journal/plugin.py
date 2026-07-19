"""Manifest V3 adapter for QZone Journal."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from kernel.types import AdminRoute, AmadeusPlugin, PluginContext
from plugins.qzone_journal.advanced_fiction_context import (
    build_advanced_fiction_context,
    build_review_provenance,
)
from plugins.qzone_journal.composer import JournalComposer
from plugins.qzone_journal.delivery import (
    BUILTIN_WIRE_PROFILE,
    DeliveryConfig,
    DeliveryGateError,
    JournalDelivery,
)
from plugins.qzone_journal.public_projection import (
    ProjectionError,
    project_factual_event,
)
from plugins.qzone_journal.selector import (
    SELECTION_REASON_CODES,
    CandidateEvent,
    JournalSelector,
    SelectionDecision,
    rank_accepted,
)
from plugins.qzone_journal.store import (
    DayDraftBudgetExceededError,
    InvalidDraftTransitionError,
    JournalDraft,
    JournalStore,
    validate_review_fields_for_compose,
)
from plugins.qzone_journal.transport import (
    NapCatCredentialSource,
    QZoneTransport,
    UnverifiedWireProfileError,
    WireProfile,
)
from plugins.qzone_journal.wire_profiles import load_wire_profile

_CST = ZoneInfo("Asia/Shanghai")
_UIN_RE = re.compile(r"^\d{5,16}$")
_API_LIST_MAX_LIMIT = 100
# Closed-safe durable metric source codes only (never UIN/cookie/Bearer text).
_METRIC_SAFE_SOURCES = frozenset({
    "event_replan",
    "dream_reflection",
    "schedule_generator",
})
_ALLOWED_DRAFT_STATUSES = frozenset({
    "pending_review",
    "approved",
    "dispatching",
    "published",
    "rejected",
    "failed",
    "unknown",
})
_HEALTH_COUNT_KEYS = (
    "pending_review",
    "approved",
    "dispatching",
    "published",
    "rejected",
    "failed",
    "unknown",
)


class PluginConfig(BaseModel):
    enabled: bool = False
    dry_run: bool = True
    allow_live_publish: bool = False
    allowed_live_uins: list[str] = Field(default_factory=list)
    advanced_enabled: bool = False
    manual_review: Literal[True] = True
    max_posts_per_day: int = Field(default=1, ge=1, le=3)
    max_drafts_per_tick: int = Field(default=1, ge=1, le=3)
    max_drafts_per_day: int = Field(default=1, ge=1, le=3)
    salience_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    allowed_sources: list[str] = Field(default_factory=lambda: [
        "event_replan",
        "dream_reflection",
        "schedule_generator",
    ])
    wire_profile_id: str = "qzone-text-v1-unverified"

    @field_validator("allowed_live_uins", mode="before")
    @classmethod
    def _normalize_allowed_live_uins(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, (list, tuple, set)):
            items = list(value)
        else:
            raise ValueError("allowed_live_uins must be a list of digit strings")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in items:
            uin = str(item or "").strip()
            if not uin:
                continue
            if _UIN_RE.fullmatch(uin) is None:
                raise ValueError(
                    "allowed_live_uins entries must be unique digit strings"
                )
            if uin in seen:
                continue
            seen.add(uin)
            normalized.append(uin)
        return normalized

    @field_validator("allowed_sources", mode="before")
    @classmethod
    def _normalize_allowed_sources(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("allowed_sources must be a list of known source names")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            source = str(item or "").strip()
            if not source or source not in _METRIC_SAFE_SOURCES:
                raise ValueError("allowed_sources contains an unsupported source name")
            if source in seen:
                continue
            seen.add(source)
            normalized.append(source)
        return normalized


class ConfirmPublishedBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)
    external_post_id: str | None = Field(default=None, max_length=120)


class ConfirmNotPublishedBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class ApproveDraftBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    approval_scope: Literal["dry_run", "live"] = "dry_run"


class RejectDraftBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)

    @field_validator("note")
    @classmethod
    def _require_non_empty_note(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("operator note is required")
        return cleaned


class RecomposeDraftBody(BaseModel):
    """Optional operator wording guidance only — never evidence/fact source."""

    operator_guidance: str | None = Field(default=None, max_length=500)


class QZoneJournalPlugin(AmadeusPlugin):
    name = "qzone_journal"
    description = "把 Living Persona 中值得记录的事实事件整理为待审 QQ 空间日志"
    version = "0.8.2"
    priority = 155

    def __init__(self, config: PluginConfig | None = None) -> None:
        super().__init__()
        self._provided_config = config
        self._config = config or PluginConfig()
        self._store: JournalStore | None = None
        self._composer: JournalComposer | None = None
        self._selector: JournalSelector | None = None
        self._ctx: PluginContext | None = None
        self._bot: Any = None
        self._transport: QZoneTransport | None = None
        self._wire_profile: WireProfile = BUILTIN_WIRE_PROFILE
        self._tick_lock = asyncio.Lock()
        # Process-lifetime secret-free selection counters (reason → int).
        self._selection_decisions: dict[str, int] = {
            code: 0 for code in sorted(SELECTION_REASON_CODES)
        }

    async def on_startup(self, ctx: PluginContext) -> None:
        if self._provided_config is None:
            from kernel.config import load_plugin_config

            self._config = load_plugin_config(
                "plugins/qzone_journal/config.default.json",
                PluginConfig,
            )
        if not self._config.enabled:
            return
        wire_profile = load_wire_profile(self._config.wire_profile_id)
        self._wire_profile = wire_profile
        store = JournalStore(
            ctx.storage_dir / "qzone_journal.db",
            max_posts_per_day=self._config.max_posts_per_day,
            max_drafts_per_day=self._config.max_drafts_per_day,
        )
        await store.init()
        self._store = store
        llm_call = getattr(ctx.llm_client, "_call", None)
        if not callable(llm_call):
            async def fallback_call(_request: Any) -> dict[str, str]:
                return {"text": ""}

            llm_call = fallback_call
        self._composer = JournalComposer(llm_call)
        self._selector = JournalSelector(
            allowed_sources=set(self._config.allowed_sources),
            salience_threshold=self._config.salience_threshold,
            advanced_enabled=self._config.advanced_enabled,
        )
        self._ctx = ctx
        self._bot = getattr(ctx, "bot", None)
        self._transport = QZoneTransport()

    async def on_tick(self, ctx: PluginContext) -> None:
        if not self._config.enabled:
            return
        store = self._store
        composer = self._composer
        selector = self._selector
        if store is None or composer is None or selector is None:
            return
        async with self._tick_lock:
            schedule = getattr(getattr(ctx, "schedule_store", None), "current", None)
            today = datetime.now(_CST).date()
            day_narrative = ""
            if schedule is not None and str(getattr(schedule, "date", "")) == today.isoformat():
                day_narrative = str(getattr(schedule, "day_narrative", "") or "")
            arc_store = getattr(ctx, "story_arc_store", None)
            load_active = getattr(arc_store, "load_active", None)
            arc = load_active() if callable(load_active) else None
            events = list(getattr(arc, "last_events", []) or []) if arc is not None else []

            # Day draft budget is evaluated before any LLM call. Hard-gate and
            # adapter reasons still fire so budget does not mask closed codes.
            occupied = await store.count_occupied_drafts_for_event_date(today)
            day_budget = int(self._config.max_drafts_per_day)
            remaining_budget = max(0, day_budget - occupied)

            recent_summaries = await store.list_recent_source_summaries(limit=32)
            accepted: list[SelectionDecision] = []
            # Same-tick dedupe keys are registered only after hard-gate accept so
            # a raw event that adapts but fails subject/privacy cannot suppress a
            # later public-fiction twin with the same source/date/stable_id.
            # Order: adapter → hard gate → same-tick/persistent dedupe → budget → rank.
            seen_dedupe_keys: set[str] = set()
            for raw in events:
                adapted = self._adapt_raw_event(raw, arc=arc, today=today)
                if adapted.reason != "accept" or adapted.candidate is None:
                    await self._note_selection(
                        adapted.reason,
                        source=adapted.source,
                    )
                    continue
                candidate = adapted.candidate
                gate = selector.evaluate(
                    candidate,
                    today=today,
                    recent_summaries=recent_summaries,
                    day_narrative=day_narrative,
                )
                if not gate.accepted:
                    await self._note_selection(
                        gate.reason,
                        source=candidate.source,
                    )
                    continue
                # Same-tick + persistent dedupe only for hard-gate-accepted candidates.
                if candidate.dedupe_key in seen_dedupe_keys:
                    await self._note_selection(
                        "reject_duplicate_dedupe",
                        source=candidate.source,
                    )
                    continue
                if await store.get_by_dedupe_key(candidate.dedupe_key) is not None:
                    await self._note_selection(
                        "reject_duplicate_dedupe",
                        source=candidate.source,
                    )
                    continue
                seen_dedupe_keys.add(candidate.dedupe_key)
                accepted.append(gate)

            if remaining_budget <= 0:
                for item in accepted:
                    source = (
                        item.candidate.source
                        if item.candidate is not None
                        else item.source
                    )
                    await self._note_selection(
                        "reject_day_draft_budget",
                        source=source,
                    )
                return

            ranked = rank_accepted(accepted)
            slots = min(
                remaining_budget,
                int(self._config.max_drafts_per_tick),
            )
            composed = 0
            for decision in ranked:
                candidate = decision.candidate
                if candidate is None:
                    continue
                if composed >= slots:
                    await self._note_selection(
                        "reject_out_ranked",
                        source=candidate.source,
                    )
                    continue
                # Re-check day budget immediately before LLM (within tick lock).
                occupied_now = await store.count_occupied_drafts_for_event_date(
                    candidate.event_date,
                )
                if occupied_now >= day_budget:
                    await self._note_selection(
                        "reject_day_draft_budget",
                        source=candidate.source,
                    )
                    continue
                # External race: re-check persistent dedupe immediately before compose.
                if await store.get_by_dedupe_key(candidate.dedupe_key) is not None:
                    await self._note_selection(
                        "reject_duplicate_dedupe",
                        source=candidate.source,
                    )
                    continue
                # Isolate review-field validation failures so one poison
                # candidate cannot abort later clean candidates in the same tick.
                try:
                    # Validate known review fields with store safety rules
                    # before any LLM / advanced-context build.
                    validate_review_fields_for_compose(
                        stable_id=candidate.stable_id,
                        subject_kind=candidate.subject_kind,
                        privacy=candidate.privacy,
                        salience=candidate.salience,
                        source_summary=candidate.summary,
                    )
                    advanced_context = build_advanced_fiction_context(
                        advanced_enabled=self._config.advanced_enabled,
                        arc=arc,
                        candidate=candidate,
                    )
                    provenance = build_review_provenance(
                        arc=arc,
                        advanced_context_included=bool(advanced_context.strip()),
                        public_projection=getattr(
                            candidate, "public_projection", None
                        ),
                    )
                    validate_review_fields_for_compose(
                        stable_id=candidate.stable_id,
                        subject_kind=candidate.subject_kind,
                        privacy=candidate.privacy,
                        salience=candidate.salience,
                        source_summary=candidate.summary,
                        provenance=provenance,
                    )
                    content = await composer.compose(
                        candidate,
                        day_narrative=day_narrative,
                        advanced_fiction_context=advanced_context,
                    )
                    await store.enqueue(
                        dedupe_key=candidate.dedupe_key,
                        event_date=candidate.event_date,
                        source=candidate.source,
                        content=content,
                        stable_id=candidate.stable_id,
                        subject_kind=candidate.subject_kind,
                        privacy=candidate.privacy,
                        salience=candidate.salience,
                        source_summary=candidate.summary,
                        provenance=provenance,
                    )
                except DayDraftBudgetExceededError:
                    # Store-level atomic budget refusal: no false accept/draft_created.
                    await self._note_selection(
                        "reject_day_draft_budget",
                        source=str(getattr(candidate, "source", "") or ""),
                    )
                    continue
                except ValueError:
                    await self._note_selection(
                        "reject_review_field",
                        source=str(getattr(candidate, "source", "") or ""),
                    )
                    await self._record_metric(
                        "qzone_draft_rejected",
                        source=str(getattr(candidate, "source", "") or ""),
                        reason="reject_review_field",
                    )
                    # Do not consume a compose slot: try next ranked candidate.
                    continue
                composed += 1
                await self._note_selection("accept", source=candidate.source)
                await self._record_metric("qzone_draft_created", source=candidate.source)

    async def on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        if not self._config.enabled:
            return
        self._ctx = ctx
        self._bot = bot

    async def on_shutdown(self, ctx: PluginContext) -> None:
        del ctx
        store = self._store
        self._store = None
        transport = self._transport
        self._transport = None
        self._wire_profile = BUILTIN_WIRE_PROFILE
        self._composer = None
        self._selector = None
        self._ctx = None
        self._bot = None
        if store is not None:
            await store.close()
        if transport is not None:
            await transport.aclose()

    def register_admin_routes(self) -> list[AdminRoute]:
        router = APIRouter()

        @router.get("/drafts")
        async def list_drafts(
            status: str | None = Query(default=None),
            limit: int = Query(default=50, ge=1, le=_API_LIST_MAX_LIMIT),
            offset: int = Query(default=0, ge=0),
        ) -> dict[str, Any]:
            store = self._require_store()
            if status is not None:
                status_key = str(status).strip()
                if status_key not in _ALLOWED_DRAFT_STATUSES:
                    raise HTTPException(
                        status_code=422,
                        detail=f"unsupported draft status: {status_key}",
                    )
                status = status_key
            try:
                total = await store.count(status=status)
                rows = await store.list(status=status, limit=limit, offset=offset)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {
                "drafts": [self._serialize_draft(row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(rows) < total,
            }

        @router.get("/drafts/{draft_id}")
        async def get_draft(draft_id: str) -> dict[str, Any]:
            draft = await self._require_store().get(draft_id)
            if draft is None:
                raise HTTPException(status_code=404, detail="draft not found")
            return self._serialize_draft(draft)

        @router.get("/drafts/{draft_id}/audit")
        async def get_draft_audit(draft_id: str) -> dict[str, Any]:
            store = self._require_store()
            draft = await store.get(draft_id)
            if draft is None:
                raise HTTPException(status_code=404, detail="draft not found")
            review_decisions = await store.list_review_decisions(draft_id=draft_id)
            manual_resolutions = await store.list_manual_resolutions(draft_id=draft_id)
            return {
                "draft_id": draft_id,
                "review_decisions": review_decisions,
                "manual_resolutions": manual_resolutions,
            }

        @router.get("/drafts/{draft_id}/revisions")
        async def get_draft_revisions(draft_id: str) -> dict[str, Any]:
            store = self._require_store()
            draft = await store.get(draft_id)
            if draft is None:
                raise HTTPException(status_code=404, detail="draft not found")
            try:
                history = await store.list_revisions(draft_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="draft not found") from exc
            root_id = draft.revision_root_id or draft.draft_id
            return {
                "revision_root_id": root_id,
                "revisions": [self._serialize_draft(row) for row in history],
            }

        @router.post("/{draft_id}/approve")
        async def approve_draft(
            draft_id: str,
            body: ApproveDraftBody | None = None,
        ) -> dict[str, Any]:
            note = body.note if body is not None else None
            approval_scope = body.approval_scope if body is not None else "dry_run"
            if (
                approval_scope == "live"
                and not bool(self._live_publish_gate().get("ready"))
            ):
                raise HTTPException(
                    status_code=409,
                    detail="live approval gate is not ready",
                )
            try:
                draft = await self._require_store().approve(
                    draft_id,
                    note=note,
                    approval_scope=approval_scope,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="draft not found") from exc
            except InvalidDraftTransitionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return self._serialize_draft(draft)

        @router.post("/{draft_id}/reject")
        async def reject_draft(
            draft_id: str,
            body: RejectDraftBody,
        ) -> dict[str, Any]:
            try:
                draft = await self._require_store().reject(
                    draft_id,
                    reason="admin_review_rejected",
                    note=body.note,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="draft not found") from exc
            except InvalidDraftTransitionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return self._serialize_draft(draft)

        @router.post("/{draft_id}/recompose")
        async def recompose_draft_route(
            draft_id: str,
            body: RecomposeDraftBody | None = None,
        ) -> dict[str, Any]:
            guidance = body.operator_guidance if body is not None else None
            try:
                draft = await self.recompose_draft(
                    draft_id,
                    operator_guidance=guidance,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="draft not found") from exc
            except InvalidDraftTransitionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except DayDraftBudgetExceededError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return self._serialize_draft(draft)

        @router.post("/{draft_id}/dry-run")
        async def dry_run_draft(draft_id: str) -> dict[str, Any]:
            try:
                result = await self._delivery(dry_run=True).deliver(draft_id)
            except (DeliveryGateError, UnverifiedWireProfileError, RuntimeError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if not isinstance(result, dict):
                raise HTTPException(status_code=409, detail="dry-run did not return a descriptor")
            await self._record_metric("qzone_publish_dry_run")
            return result

        @router.post("/{draft_id}/publish")
        async def publish_draft(draft_id: str) -> dict[str, Any]:
            if self._config.dry_run:
                raise HTTPException(
                    status_code=409,
                    detail="QZone Journal is configured for dry-run only",
                )
            try:
                result = await self._delivery(dry_run=False).deliver(draft_id)
            except (DeliveryGateError, UnverifiedWireProfileError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Exception as exc:
                draft = await self._require_store().get(draft_id)
                if draft is not None and draft.status == "unknown":
                    await self._record_metric("qzone_publish_unknown")
                raise HTTPException(
                    status_code=502,
                    detail=f"QZone publish failed: {type(exc).__name__}",
                ) from exc
            if isinstance(result, dict):
                raise HTTPException(
                    status_code=409,
                    detail="live publish did not return a draft state",
                )
            await self._record_metric("qzone_publish_succeeded")
            return {
                "draft_id": result.draft_id,
                "status": result.status,
            }

        @router.post("/{draft_id}/confirm-published")
        async def confirm_published_draft(
            draft_id: str,
            body: ConfirmPublishedBody,
        ) -> dict[str, Any]:
            try:
                draft = await self._require_store().confirm_published(
                    draft_id,
                    note=body.note,
                    external_post_id=body.external_post_id,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="draft not found") from exc
            except InvalidDraftTransitionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return self._serialize_draft(draft)

        @router.post("/{draft_id}/confirm-not-published")
        async def confirm_not_published_draft(
            draft_id: str,
            body: ConfirmNotPublishedBody,
        ) -> dict[str, Any]:
            try:
                draft = await self._require_store().confirm_not_published(
                    draft_id,
                    note=body.note,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="draft not found") from exc
            except InvalidDraftTransitionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return self._serialize_draft(draft)

        @router.get("/health")
        async def health() -> dict[str, Any]:
            counts = {key: 0 for key in _HEALTH_COUNT_KEYS}
            if self._store is not None:
                stats = await self._store.stats()
                by_status = stats.get("by_status", {})
                for key in counts:
                    counts[key] = int(by_status.get(key, 0) or 0)
            # Process-lifetime counters only: closed reason → int. Never include
            # summary/stable_id/QQ-like identifiers or secrets.
            selection_decisions = {
                code: int(self._selection_decisions.get(code, 0) or 0)
                for code in sorted(SELECTION_REASON_CODES)
            }
            selection_total = sum(selection_decisions.values())
            selection_accepted = selection_decisions.get("accept", 0)
            selection_summary = {
                "scope": "process_lifetime",
                "total": selection_total,
                "accepted": selection_accepted,
                "rejected": selection_total - selection_accepted,
                "acceptance_rate": (
                    selection_accepted / selection_total if selection_total else 0.0
                ),
            }
            return {
                "configured_enabled": self._config.enabled,
                "dry_run": self._config.dry_run,
                "live_allowed": self._config.allow_live_publish,
                "advanced_enabled": self._config.advanced_enabled,
                "manual_review": True,
                "wire_profile_id": self._config.wire_profile_id,
                "profile_validated": bool(self._wire_profile.validated),
                "salience_threshold": self._config.salience_threshold,
                "allowed_sources": list(self._config.allowed_sources),
                "max_drafts_per_tick": int(self._config.max_drafts_per_tick),
                "max_drafts_per_day": int(self._config.max_drafts_per_day),
                "counts": counts,
                "selection_decisions": selection_decisions,
                "selection_summary": selection_summary,
                "live_publish_gate": self._live_publish_gate(),
            }

        return [AdminRoute(path="/qzone-journal", router=router)]

    def _live_publish_gate(self) -> dict[str, Any]:
        """Structured operator-facing gate; never inspects credentials."""
        reasons: list[dict[str, str]] = []

        def add(code: str, message: str) -> None:
            reasons.append({"code": code, "message": message})

        if not self._config.enabled:
            add("configured_disabled", "插件未启用，禁止真实发布")
        if self._config.dry_run:
            add("dry_run_enabled", "当前为 dry-run 模式，禁止真实发布")
        if not self._config.allow_live_publish:
            add("live_publish_disallowed", "未打开 allow_live_publish 开关")
        # Built-in profile is permanently unvalidated. Custom profiles are
        # distinct runtime objects loaded only from conforming real captures.
        profile = self._wire_profile
        if profile.profile_id != self._config.wire_profile_id:
            add("unknown_wire_profile", "未知线缆配置，禁止真实发布")
        if not bool(profile.validated):
            add("profile_not_validated", "线缆配置尚未验证，禁止真实发布")
        if not self._config.allowed_live_uins:
            add("allowed_live_uins_empty", "未配置允许真实发布的 UIN 白名单")
        bot = self._bot or getattr(self._ctx, "bot", None)
        if bot is None:
            add("bot_disconnected", "机器人未连接，禁止真实发布")
        if self._transport is None:
            add("transport_unavailable", "发布传输层不可用")
        return {"ready": len(reasons) == 0, "reasons": reasons}

    def _adapt_raw_event(
        self,
        raw: Any,
        *,
        arc: Any,
        today: Any,
    ) -> SelectionDecision:
        """Map a raw arc event into a CandidateEvent with a closed reason.

        Adapter failures use explicit closed codes so every raw input maps to
        the frozen reason enum (R1/R8).
        """
        if not isinstance(raw, dict):
            return SelectionDecision(reason="reject_adapter_unparseable", source="")
        event_date = str(raw.get("date", "") or "")
        if event_date != today.isoformat():
            return SelectionDecision(
                reason="reject_adapter_unparseable",
                source=str(raw.get("source", "") or "").strip(),
            )
        source = str(raw.get("source", "") or "").strip()
        summary = str(raw.get("summary", "") or "").strip()
        if not source or not summary:
            return SelectionDecision(
                reason="reject_adapter_unparseable",
                source=source,
            )
        # Producer-owned contract (v0.6): subject_kind / privacy / salience must
        # be explicit on the raw event. Never invent from source or arc.scope.
        subject_kind = str(raw.get("subject_kind", "") or "").strip()
        privacy = str(raw.get("privacy", "") or "").strip()
        if not subject_kind or not privacy:
            return SelectionDecision(
                reason="reject_missing_subject_privacy",
                source=source,
            )
        if "salience" not in raw:
            return SelectionDecision(
                reason="reject_adapter_unparseable",
                source=source,
            )
        raw_salience = raw.get("salience")
        # bool is a subclass of int; reject it before float() coerces True→1.0.
        if isinstance(raw_salience, bool) or not isinstance(raw_salience, (int, float)):
            return SelectionDecision(
                reason="reject_adapter_unparseable",
                source=source,
            )
        salience = float(raw_salience)
        if not math.isfinite(salience):
            return SelectionDecision(
                reason="reject_adapter_unparseable",
                source=source,
            )
        # Finite out-of-range stays a CandidateEvent so selector returns
        # reject_salience_out_of_range (selector ownership of range gate).
        arc_id = str(getattr(arc, "arc_id", "active") or "active")
        stable_id = str(
            raw.get("event_id", raw.get("stable_id", "")) or ""
        ).strip() or f"{arc_id}:{source}:{event_date}"

        public_projection = None
        projected_summary = summary
        if subject_kind == "factual":
            # Part C: raw factual summary + internal identities must be projected
            # to approved public labels before CandidateEvent construction.
            try:
                validated = project_factual_event(
                    raw_summary=summary,
                    projection_input=raw.get("public_projection"),
                )
            except (ProjectionError, ValueError, TypeError):
                return SelectionDecision(
                    reason="reject_public_projection",
                    source=source,
                )
            public_projection = validated
            projected_summary = validated.projected_summary
        elif raw.get("public_projection") is not None:
            # Non-factual events must not carry projection payloads.
            return SelectionDecision(
                reason="reject_public_projection",
                source=source,
            )

        try:
            candidate = CandidateEvent(
                source=source,
                event_date=today,
                stable_id=stable_id,
                subject_kind=subject_kind,
                privacy=privacy,
                salience=salience,
                summary=projected_summary,
                public_projection=public_projection,
            )
        except ValueError:
            return SelectionDecision(
                reason="reject_public_projection",
                source=source,
            )
        return SelectionDecision(
            reason="accept",
            candidate=candidate,
            source=source,
        )

    def _candidate_from_event(
        self,
        raw: Any,
        *,
        arc: Any,
        today: Any,
    ) -> CandidateEvent | None:
        """Backward-compatible adapter used by older tests/helpers."""
        decision = self._adapt_raw_event(raw, arc=arc, today=today)
        return decision.candidate if decision.reason == "accept" else None

    def _require_store(self) -> JournalStore:
        if self._store is None:
            raise HTTPException(status_code=503, detail="QZone Journal is disabled")
        return self._store

    async def recompose_draft(
        self,
        draft_id: str,
        *,
        operator_guidance: str | None = None,
    ) -> JournalDraft:
        """Explicit operator recompose: new revision row, never mutates source.

        Cheap preflight (status, lineage tip, non-empty source_summary) runs
        before composer/LLM. Store.create_revision revalidates atomically after
        compose so concurrent races still fail closed.

        Factual: body equals verified projected summary.
        Fiction/self: LLM wording only; guidance is scrubbed and not persisted
        as provenance fact.
        """
        store = self._require_store()
        composer = self._composer
        if composer is None:
            raise RuntimeError("QZone Journal composer unavailable")
        source = await store.get(draft_id)
        if source is None:
            raise KeyError(draft_id)
        # Preflight before any LLM work (store rechecks inside create_revision).
        if source.status not in ("pending_review", "rejected"):
            raise InvalidDraftTransitionError(
                f"cannot recompose QZone draft from {source.status}"
            )
        history = await store.list_revisions(source.draft_id)
        has_successor = any(
            item.supersedes_draft_id == source.draft_id for item in history
        )
        if has_successor:
            raise InvalidDraftTransitionError(
                "source draft already has a successor revision"
            )
        verified = (source.source_summary or "").strip()
        if not verified:
            raise ValueError(
                "source draft has no verified source_summary for recompose"
            )
        # Compose outside the store transaction so cancel during LLM does not
        # hold locks; store.create_revision is still cancel-safe on insert.
        new_content = await composer.recompose(
            subject_kind=source.subject_kind,
            source=source.source,
            verified_summary=verified,
            previous_content=source.content,
            operator_guidance=operator_guidance,
        )
        # Provenance + source_summary inherited inside store (no override).
        return await store.create_revision(
            source.draft_id,
            content=new_content,
        )

    def _delivery(self, *, dry_run: bool) -> JournalDelivery:
        store = self._require_store()
        profile = self._wire_profile
        if profile.profile_id != self._config.wire_profile_id:
            raise DeliveryGateError(
                f"unknown QZone wire profile: {self._config.wire_profile_id}"
            )
        bot = self._bot or getattr(self._ctx, "bot", None)
        if bot is None:
            raise DeliveryGateError("NapCat bot is not connected")
        transport = self._transport
        if transport is None:
            raise DeliveryGateError("QZone transport is unavailable")
        return JournalDelivery(
            config=DeliveryConfig(
                enabled=self._config.enabled,
                dry_run=dry_run or self._config.dry_run,
                allow_live_publish=self._config.allow_live_publish,
                allowed_live_uins=tuple(self._config.allowed_live_uins),
            ),
            store=store,
            credential_source=NapCatCredentialSource(bot=bot),
            transport=transport,
            profile=profile,
        )

    @staticmethod
    def _closed_metric_source(source: str = "") -> str:
        """Map any configured/raw source to a closed-safe durable metric code.

        Only event_replan / dream_reflection / schedule_generator may persist;
        every other value becomes ``unknown``. Never pass through UIN/cookie/
        Bearer/assignment-shaped text.
        """
        cleaned = str(source or "").strip()
        if cleaned in _METRIC_SAFE_SOURCES:
            return cleaned
        return "unknown"

    async def _note_selection(self, reason: str, *, source: str = "") -> None:
        """Record a closed selection reason in-process and as a durable metric.

        Metadata is limited to reason + closed-safe source (no summary/stable_id/secrets).
        """
        code = str(reason or "").strip()
        if code not in SELECTION_REASON_CODES:
            code = "reject_adapter_unparseable"
        self._selection_decisions[code] = int(self._selection_decisions.get(code, 0)) + 1
        await self._record_metric(
            "qzone_selection_decision",
            reason=code,
            source=self._closed_metric_source(source),
        )

    async def _record_metric(self, metric_key: str, **metadata: Any) -> None:
        store = getattr(self._ctx, "block_trace_store", None)
        record = getattr(store, "record_runtime_metric", None)
        if not callable(record):
            return
        # Closed-safe source for durable selection/reject metrics only.
        if (
            metric_key in {"qzone_selection_decision", "qzone_draft_rejected"}
            and "source" in metadata
        ):
            metadata = {
                **metadata,
                "source": self._closed_metric_source(
                    str(metadata.get("source", "") or "")
                ),
            }
        try:
            record_fn = cast(Callable[..., Awaitable[Any]], record)
            await record_fn(metric_key=metric_key, metadata=metadata)
        except Exception:
            return

    @staticmethod
    def _serialize_draft(draft: JournalDraft) -> dict[str, Any]:
        provenance: dict[str, Any] | None = None
        if draft.provenance_json:
            try:
                parsed = json.loads(draft.provenance_json)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                provenance = parsed
        review = {
            "stable_id": draft.stable_id,
            "subject_kind": draft.subject_kind,
            "privacy": draft.privacy,
            "salience": draft.salience,
            "source_summary": draft.source_summary,
            "approval_scope": draft.approval_scope,
            "provenance": provenance,
        }
        return {
            "draft_id": draft.draft_id,
            "dedupe_key": draft.dedupe_key,
            "event_date": draft.event_date.isoformat(),
            "source": draft.source,
            "content": draft.content,
            "status": draft.status,
            "publish_date": draft.publish_date.isoformat() if draft.publish_date else None,
            "external_post_id": draft.external_post_id,
            "last_error_code": draft.last_error_code,
            "created_at": draft.created_at,
            "updated_at": draft.updated_at,
            "stable_id": draft.stable_id,
            "subject_kind": draft.subject_kind,
            "privacy": draft.privacy,
            "salience": draft.salience,
            "source_summary": draft.source_summary,
            "revision_root_id": draft.revision_root_id or draft.draft_id,
            "revision": int(draft.revision or 1),
            "supersedes_draft_id": draft.supersedes_draft_id,
            "approval_scope": draft.approval_scope,
            "review": review,
        }


__all__ = ["PluginConfig", "QZoneJournalPlugin"]
