"""Single local application composition and authenticated protocol entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import stat
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import monotonic
from typing import Literal, cast
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import JsonValue, ValidationError

from omubot_new.actions import Actions
from omubot_new.adapters import (
    AnthropicModel,
    OfflineModel,
    OfflineSender,
    OfflineThinker,
    OneBotSender,
    parse_event,
)
from omubot_new.affection import AffectionService
from omubot_new.archive import ArchiveService, ArchiveSourceInput, ArchiveSourceRecord
from omubot_new.archive_spool import EncryptedTextSpool, EncryptedTextSpoolError, spool_binding_id
from omubot_new.canon_loader import load_canon_registry
from omubot_new.character_actions import CharacterActions
from omubot_new.character_assets import CharacterAssetOwner, CharacterAssetReceipt, InstalledCharacterAsset
from omubot_new.character_identity import CharacterIdentityService
from omubot_new.character_pack_client import MAX_BATCH_BYTES, CcipPackClient
from omubot_new.character_recognition import (
    AnimeTraceClient,
    CcipClient,
    CharacterRecognitionService,
    load_reference_pack,
)
from omubot_new.character_reference import CharacterReferenceMutationReceipt, CharacterReferenceOwner
from omubot_new.climate import ClimateEngine
from omubot_new.config import (
    MAX_MODEL_SYSTEM_CHARS,
    PERSONA_SOURCE_REF,
    Config,
    Credentials,
    ModelProfile,
    load_config,
    load_task_credentials,
    normalize_model_endpoint,
)
from omubot_new.context_observation import ContextObservationSnapshot, context_observation_snapshot
from omubot_new.conversation import Conversation
from omubot_new.domain_learning import (
    DomainLearningCandidate,
    DomainLearningFailure,
    DomainLearningResult,
    DomainLearningService,
    ExtractionRunDiagnostic,
    ObservationCandidateRef,
    SlangValue,
    StyleValue,
)
from omubot_new.dream_runtime import ManualDreamRunner
from omubot_new.food import FoodOwner
from omubot_new.graph import GraphAlias, GraphKind, GraphRelation, GraphService
from omubot_new.graph_runtime import GraphExtractionRunner
from omubot_new.http_api import HttpApiOwner
from omubot_new.instances import initialize_instance, load_instance
from omubot_new.journal import JournalDraft, JournalOwner
from omubot_new.journal_compose import JournalComposer, public_statement
from omubot_new.journal_publish import JournalPublisher, JournalPublishGate, UnvalidatedQZonePublisher
from omubot_new.journal_qzone import QZoneHttpPublisher
from omubot_new.knowledge import KnowledgeChunkPointer, KnowledgeService, KnowledgeSource
from omubot_new.learning_autopilot import AutoApplyReport, AutoCandidateRef, LearningAutoApplyRunner
from omubot_new.memory import (
    MatterState,
    MemoryCandidate,
    MemoryFact,
    MemoryService,
    SelfAliasResolution,
    self_nickname_command,
)
from omubot_new.memory_extractor import MemoryExtractionBundle
from omubot_new.memory_runtime import MemoryExtractionReport, MemoryExtractionRunner
from omubot_new.model_catalog import CatalogError, discover_models
from omubot_new.model_secrets import read_model_secret, save_model_secret
from omubot_new.models_chat import ChatModel
from omubot_new.models_responses import ResponsesModel
from omubot_new.napcat import NapCatClient, NapCatError
from omubot_new.napcat_container import NapCatContainer
from omubot_new.native_media import CurrentEventVisualResolver
from omubot_new.official_calendar import OfficialCalendar
from omubot_new.onebot_interactions import OneBotInteractions, interaction_transport_identity
from omubot_new.onebot_ws import ReverseOneBotSender
from omubot_new.persona import PersonaCompileError, PersonaCompiler, PersonaSourceImporter
from omubot_new.policy import Policy
from omubot_new.qq_delivery import QQDelivery
from omubot_new.qq_ownership import QQAccountOwnership
from omubot_new.recent_history import (
    OneBotRequest,
    RecentHistoryOwner,
    RecentHistoryRequest,
    RecentHistoryView,
)
from omubot_new.release_management import BackupReceipt, NativeUpdatePreparation, SourceRelease
from omubot_new.research_events import ResearchDeriveRequest, ResearchEvents, load_research_key
from omubot_new.retrieval import RetrievalService
from omubot_new.rich_messages import AtSegment, TextSegment
from omubot_new.runtime import Turn
from omubot_new.schedule_life import PersonaRolePoints, ScheduleDayRecord, ScheduleLife
from omubot_new.schedule_runtime import DailyScheduleRunner, ScheduleRunReport
from omubot_new.search import SearXNGClient, search_destination
from omubot_new.settings import SettingsService, resolve_saved, validate_document
from omubot_new.slang_review import SlangReviewReport, SlangReviewRunner
from omubot_new.social import SocialExperienceService
from omubot_new.social_feedback import parse_group_social_notice
from omubot_new.sticker_description import StickerDescriptionRunner
from omubot_new.sticker_store import StickerMetadata, StickerStore
from omubot_new.stickers import MAX_STICKER_BYTES, StickerEntry
from omubot_new.store import SettingsPersonaReceipt, Store, StoreConnection, drain_on_cancel
from omubot_new.story import DreamProposalRecord, StoryArcInput, StoryArcRecord, StoryArcStore, StoryCommit
from omubot_new.storylet_loader import load_storylet_registry
from omubot_new.storylet_runtime import StoryletDailyRunner, StoryletRunReport
from omubot_new.tools import Tools
from omubot_new.types import (
    Event,
    KnowledgeVisibilityRef,
    LearningVisibilityRef,
    Message,
    ModelPort,
    ModelRequest,
    OperationError,
    PrivateScope,
    QQScopeKey,
    QuotedVisualResolverPort,
    Scope,
    SenderPort,
    StickerMediaType,
    StickerSenderPort,
    StrictModel,
    VisibilityMaterial,
    VisualResolverPort,
)
from omubot_new.web_contracts import (
    AggregateHealthSnapshot,
    AuthResponse,
    CharacterPackAssetView,
    CharacterPackBuildUpdate,
    CharacterPackRestoreUpdate,
    CharacterReferenceBackupView,
    CharacterReferenceMergeRequest,
    CharacterReferenceMutationView,
    CharacterReferenceRestoreRequest,
    CharacterReferenceSaveRequest,
    CharacterReferenceStatusView,
    ContactConsentRequest,
    ContactConsentResponse,
    ContactConsentView,
    CoreRestartRequest,
    CoreRestartResponse,
    CoreRuntime,
    DiagnosticsSnapshot,
    DomainLearningCandidateView,
    DomainLearningFailurePageView,
    DomainLearningFailureView,
    DomainLearningRetryRequest,
    DomainLearningRetryView,
    DreamDecisionRequest,
    DreamProposalPageView,
    DreamProposalView,
    DreamProposeRequest,
    EpisodeDecayRequest,
    EpisodeManagementView,
    EpisodePromptStateRequest,
    ExtractionRunDiagnosticPageView,
    ExtractionRunDiagnosticView,
    FamiliarityAdjustmentRequest,
    FamiliarityView,
    GraphAliasProposeRequest,
    GraphAliasView,
    GraphExtractRequest,
    GraphExtractSuggestionView,
    GraphExtractView,
    GraphObjectPageView,
    GraphObjectRequest,
    GraphObservationView,
    GraphProjectionView,
    GraphRelationProposeRequest,
    GraphRelationView,
    GraphReviewRequest,
    GraphSelfFactObjectRequest,
    GraphSelfFactProposeRequest,
    GraphSelfFactReviewRequest,
    GraphSelfFactSourceView,
    GraphSelfFactView,
    GraphWalkRequest,
    GroupBoardView,
    HealthComponent,
    HealthResponse,
    JournalApprovalRequest,
    JournalCreateRequest,
    JournalDecisionRequest,
    JournalDeliveryPageView,
    JournalDeliveryView,
    JournalDraftView,
    JournalDryRunRequest,
    JournalDryRunView,
    JournalFactualPreviewRequest,
    JournalFictionPreviewRequest,
    JournalFictionPreviewView,
    JournalHeadPageView,
    JournalHeadView,
    JournalPublicConsentPageView,
    JournalPublishRequest,
    JournalResolveRequest,
    JournalRevisionRequest,
    JournalStatusView,
    KnowledgeActivationRequest,
    KnowledgeChunkView,
    KnowledgeDocumentView,
    KnowledgeImportRequest,
    KnowledgeRemoveRequest,
    KnowledgeReviewRequest,
    KnowledgeSearchView,
    KnowledgeSourcePageView,
    KnowledgeSourceView,
    LearningAutoApplyReceiptPageView,
    LearningAutoApplyReceiptView,
    LearningAutoApplyReportView,
    LearningAutoApplyRollbackRequest,
    LearningAutoApplyStatusView,
    MemoryApplyRequest,
    MemoryCandidatePageView,
    MemoryCandidateView,
    MemoryCardCategory,
    MemoryCardClassificationRequest,
    MemoryCardClearRequest,
    MemoryCardMetadataView,
    MemoryCardQueryView,
    MemoryCorrectionRequest,
    MemoryDisableFactRequest,
    MemoryFactPageView,
    MemoryFactView,
    MemoryHotFactView,
    MemoryHotPreviewView,
    MemoryMatterPageView,
    MemoryMatterProposalRequest,
    MemoryMatterReplaceRequest,
    MemoryMatterTransitionRequest,
    MemoryMatterView,
    MemoryResolveConflictRequest,
    MemoryRetrievalDiagnosticRequest,
    MemoryRetrievalDiagnosticView,
    MemoryReviewRequest,
    ModelAccessRequest,
    ModelCatalogRequest,
    ModelCatalogResponse,
    ModelCheckResponse,
    NapcatConnectRequest,
    NapcatLogs,
    NapcatRuntime,
    NapcatStatus,
    NativeBackupRecord,
    NativeManualPlanView,
    NativePlanRequest,
    NativePreparationRequest,
    NativePreparationView,
    NativeReleaseInputs,
    NativeReleaseStatusView,
    ObservationJobPageView,
    ObservationJobView,
    ObservationPoolPageView,
    ObservationPoolView,
    ObservationReviewRequest,
    OfflineChatRequest,
    OfflineChatResponse,
    PersonaPreviewIssue,
    PersonaPreviewRequest,
    PersonaPreviewResponse,
    PolicySnapshot,
    PolicyWriteRequest,
    QQBatchBeginRequest,
    QQBatchEndRequest,
    QQBotBatchProof,
    QQDeliveryView,
    QQPauseRequest,
    QQResumeRequest,
    QQTestBatchView,
    QQWaitingTargetView,
    RevisionResponse,
    RwsFeedbackStatusView,
    SettingsRollbackRequest,
    SettingsSnapshot,
    SettingsWriteRequest,
    SlangGovernanceRevokeRequest,
    SlangGovernanceSuggestionPageView,
    SlangGovernanceSuggestionView,
    SlangReviewStatusView,
    SocialProgressView,
    SocialRetryRequest,
    StatusSnapshot,
    StickerCatalogView,
    StickerDescriptionView,
    StickerEntryView,
    StickerImportRequest,
    StickerMetadataRequest,
    StickerMetadataView,
    StickerMutationView,
    StickerTargetRequest,
    StoryArcCreateRequest,
    StoryArcListView,
    StoryArcView,
    StyleFeedbackRequest,
    StyleManagementView,
    StyleObjectRequest,
    StyleProfileRequest,
    TokenRequest,
    TopicEdgeDiagnosticsSnapshot,
    VisibilityGrantRequest,
    VisibilityMutationResponse,
    VisibilityOptionsView,
    VisibilityOptionView,
    VisibilityRevokeRequest,
    VisibilitySnapshot,
)
from omubot_new.web_fetch import WebFetchOwner
from omubot_new.worldbook import DreamValidator, StoryletEngine


def web_response(value: StrictModel) -> JSONResponse:
    return JSONResponse(value.model_dump(mode="json", by_alias=True), headers={"Cache-Control": "no-store"})


def knowledge_source_view(source: KnowledgeSource) -> KnowledgeSourceView:
    return KnowledgeSourceView.model_validate(asdict(source))


def memory_candidate_view(candidate: MemoryCandidate) -> MemoryCandidateView:
    return MemoryCandidateView(
        kind="fact",
        candidate_id=candidate.candidate_id,
        subject_id=candidate.subject_id,
        predicate=candidate.predicate,
        value=candidate.value,
        action=candidate.action,
        target_fact_id=candidate.target_fact_id,
        source_ids=list(candidate.source_ids),
        candidate_revision=candidate.candidate_revision,
        status=candidate.status,
        conflict_set_id=candidate.conflict_set_id,
        skip_reason=candidate.skip_reason,
        suggestion_reason=candidate.suggestion_reason,
        observed_at=candidate.observed_at,
        valid_from=candidate.valid_from,
        valid_to=candidate.valid_to,
        created_at=candidate.created_at,
    )


def domain_learning_candidate_view(
    candidate: DomainLearningCandidate,
    *,
    social: SocialProgressView | None = None,
) -> DomainLearningCandidateView:
    value: JsonValue
    if isinstance(candidate.value, SlangValue):
        value = cast(JsonValue, {
            "term": candidate.value.term,
            "meaning": candidate.value.meaning,
            "aliases": list(candidate.value.aliases),
        })
    elif isinstance(candidate.value, StyleValue):
        value = cast(JsonValue, {
            "situation": candidate.value.situation,
            "style": candidate.value.style,
            "output_policy": candidate.value.output_policy,
            "risk_tags": list(candidate.value.risk_tags),
        })
    else:
        value = cast(JsonValue, {
            "situation": candidate.value.situation,
            "observed_context": candidate.value.observed_context,
            "action_taken": candidate.value.action_taken,
            "outcome_signal": candidate.value.outcome_signal,
            "reflection": candidate.value.reflection,
        })
    return DomainLearningCandidateView(
        kind=candidate.domain,
        candidate_id=candidate.candidate_id,
        candidate_revision=candidate.candidate_revision,
        status=candidate.review_status,
        application_status=candidate.application_status,
        episode_state=candidate.episode_state,
        source_ids=[candidate.source_id],
        created_at=candidate.created_at,
        value=value,
        social=social,
    )


def domain_learning_failure_view(
    failure: DomainLearningFailure,
) -> DomainLearningFailureView:
    return DomainLearningFailureView(
        result_id=failure.result_id,
        source_id=failure.source_id,
        domain=failure.domain,
        extractor_version=failure.extractor_version,
        source_revision=failure.source_revision,
        error_code=failure.error_code,
        failure_revision=failure.failure_revision,
        created_at=failure.created_at,
        status=failure.status,
    )


def extraction_run_diagnostic_view(
    run: ExtractionRunDiagnostic,
) -> ExtractionRunDiagnosticView:
    return ExtractionRunDiagnosticView(
        run_id=run.run_id,
        source_id=run.source_id,
        source_revision=run.source_revision,
        status=run.status,
        stage=run.stage,
        error_code=run.error_code,
        started_at=run.started_at,
        updated_at=run.updated_at,
        finished_at=run.finished_at,
        missing_domains=list(run.missing_domains),
    )


def domain_learning_retry_view(
    result: DomainLearningResult, *, failure_revision: int
) -> DomainLearningRetryView:
    if result.domain != "slang" or result.result_status == "no_evidence":
        raise OperationError("invalid_domain_learning_result")
    if result.result_status == "failed":
        if result.error_code is None or result.failure_revision != failure_revision:
            raise OperationError("invalid_domain_learning_result")
        error_code = cast(Literal["slang_key_collision", "slang_stoplisted"], result.error_code)
    else:
        if result.error_code is not None:
            raise OperationError("invalid_domain_learning_result")
        error_code = None
    return DomainLearningRetryView(
        result_id=result.result_id,
        domain="slang",
        status=result.result_status,
        error_code=error_code,
        failure_revision=failure_revision,
        candidate_count=len(result.candidate_ids),
    )


def _memory_candidate_cursor(
    *, group_id: str, status: str | None, fact_after: str | None, domain_after: str | None
) -> str:
    payload = json.dumps(
        {"group_id": group_id, "status": status, "fact": fact_after, "domain": domain_after},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _parse_memory_candidate_cursor(
    after: str | None, *, group_id: str, status: str | None
) -> tuple[str | None, str | None]:
    if after is None:
        return None, None
    try:
        if type(after) is not str or not after or len(after) > 512:
            raise ValueError
        encoded = after + "=" * (-len(after) % 4)
        payload = base64.b64decode(encoded, altchars=b"-_", validate=True)
        value: object = json.loads(payload.decode("utf-8"))
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        # Before the unified endpoint, fact pagination exposed its raw
        # candidate id as the cursor. Keep accepting it as the fact anchor.
        return after, None
    if not isinstance(value, dict):
        return after, None
    cursor = cast(dict[str, object], value)
    if not {"group_id", "status", "fact", "domain"}.issubset(cursor):
        return after, None
    if (
        set(cursor) != {"group_id", "status", "fact", "domain"}
        or cursor["group_id"] != group_id
        or cursor["status"] != status
        or any(
            item is not None and (not isinstance(item, str) or not item)
            for item in (cursor["fact"], cursor["domain"])
        )
    ):
        raise OperationError("invalid_memory_cursor") from None
    fact_cursor = cursor["fact"]
    domain_cursor = cursor["domain"]
    return (
        None if fact_cursor is None else cast(str, fact_cursor),
        None if domain_cursor is None else cast(str, domain_cursor),
    )


def memory_fact_view(fact: MemoryFact) -> MemoryFactView:
    if fact.status == "active":
        status = "active"
    elif fact.status == "disabled":
        status = "disabled"
    else:
        raise OperationError("invalid_memory_fact")
    return MemoryFactView(
        fact_id=fact.fact_id,
        subject_id=fact.subject_id,
        predicate=fact.predicate,
        value=fact.value,
        source_ids=list(fact.source_ids),
        fact_revision=fact.fact_revision,
        status=status,
        observed_at=fact.observed_at,
        valid_from=fact.valid_from,
        valid_to=fact.valid_to,
        applied_at=fact.applied_at,
    )


def memory_card_metadata_view(metadata: tuple[MemoryFact, str | None, int]) -> MemoryCardMetadataView:
    fact, category, revision = metadata
    return MemoryCardMetadataView(
        fact=memory_fact_view(fact), category=cast(MemoryCardCategory | None, category),
        classification_revision=revision,
    )


def story_arc_view(arc: StoryArcRecord) -> StoryArcView:
    return StoryArcView(
        arc_id=arc.arc_id,
        role=arc.role,
        title=arc.title,
        stage=arc.stage,
        status=arc.status,
        revision=arc.revision,
        group_ids=list(arc.group_ids),
    )


def dream_proposal_view(record: DreamProposalRecord) -> DreamProposalView:
    proposal = record.proposal_json
    return DreamProposalView.model_validate(
        {
            "proposal_id": record.proposal_id,
            "group_id": record.group_id,
            "target_arc_id": record.target_arc_id,
            "target_arc_revision": record.target_arc_revision,
            "created_at": record.created_at,
            "kind": proposal["kind"],
            "summary": proposal["summary"],
            "payload": proposal["payload"],
            "decision_status": record.decision_status,
            "reason": record.reason,
            "committed_event_id": record.committed_event_id,
            "committed_at": record.committed_at,
        }
    )


def _dream_source_fingerprint(config: Config) -> str:
    selected = config.for_task("dream")
    material = {
        "schema": 1,
        "bot_id": config.bot_id,
        "groups": sorted(config.worldbook_allowed_groups),
        "profile": selected.selected_model.model_dump(mode="json"),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "dream-v1-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def configured_model(profile: ModelProfile, key: str, client: httpx.AsyncClient) -> ModelPort:
    if profile.api_format == "anthropic":
        return AnthropicModel(
            client,
            profile.endpoint,
            key,
            max_tokens=profile.max_output_tokens,
            temperature=profile.temperature,
            vision_enabled=profile.vision_enabled,
        )
    if profile.api_format == "openai_responses":
        return ResponsesModel(
            client,
            profile.endpoint,
            key,
            max_tokens=profile.max_output_tokens,
            temperature=profile.temperature,
            reasoning_effort=profile.reasoning_effort,
            vision_enabled=profile.vision_enabled,
        )
    return ChatModel(
        client,
        profile.endpoint,
        key,
        deepseek=profile.api_format == "deepseek",
        max_tokens=profile.max_output_tokens,
        temperature=profile.temperature,
        reasoning_effort=profile.reasoning_effort,
        thinking=profile.thinking,
        token_parameter=profile.token_parameter or "max_completion_tokens",
        vision_enabled=profile.vision_enabled,
    )


@dataclass
class CoreRestart:
    """CLI-owned, one-shot handoff; contains no commands or user-selected paths."""

    stop: Callable[[], None]
    prepare: Callable[[Config], Credentials]
    candidate: tuple[Config, Credentials, int] | None = None
    previous_revision: int | None = None
    last_error: str = ""
    requested_receipt: SettingsPersonaReceipt | None = None
    startup_receipt: tuple[SettingsPersonaReceipt, Literal["applied", "rejected"]] | None = None
    ready: Callable[[], Awaitable[None]] | None = None

    @property
    def pending(self) -> bool:
        return self.candidate is not None or self.startup_receipt is not None

    async def shutdown(self) -> None:
        # Starlette invokes this only after the acceptance response has been sent.
        self.stop()


def _schedule_persona_points(config: Config) -> PersonaRolePoints:
    """Pass bounded character cues to planning, never the full Persona prompt."""

    def cues(texts: list[str]) -> tuple[str, ...]:
        result: list[str] = []
        for text in texts:
            for raw_line in text.splitlines():
                line = raw_line.strip().lstrip("-• ").strip()
                if line and len(line) <= 160 and line not in result:
                    result.append(line)
                if len(result) >= 8:
                    return tuple(result)
        return tuple(result)

    compiled = config.compiled_persona()
    identity_sources = [config.persona_name]
    trait_sources: list[str] = []
    if compiled is not None and compiled.blocks:
        identity_sources.extend(block.content for block in compiled.blocks if block.role == "identity")
        trait_sources.extend(block.content for block in compiled.blocks if block.role == "expression")
    else:
        trait_sources.append(config.persona_instructions)
    points = PersonaRolePoints(
        identity=cues(identity_sources),
        traits=cues(trait_sources),
    )
    if not points.identity and not points.traits:
        raise OperationError("schedule_persona_unavailable")
    return points


def create_app(
    config: Config,
    credentials: Credentials,
    *,
    model: ModelPort | None = None,
    memory_model: ModelPort | None = None,
    dream_model: ModelPort | None = None,
    vision_model: ModelPort | None = None,
    sender: SenderPort | None = None,
    sticker_sender: StickerSenderPort | None = None,
    thinker: ModelPort | None = None,
    dev_web_bypass: bool = False,
    reverse_ws: bool = False,
    napcat_container: NapCatContainer | None = None,
    core_restart: CoreRestart | None = None,
    pinned_revision: int | None = None,
    retrieval: RetrievalService | None = None,
    visual_resolver: VisualResolverPort | None = None,
    quoted_visual_resolver: QuotedVisualResolverPort | None = None,
    search_client: SearXNGClient | None = None,
    web_fetch_owner: WebFetchOwner | None = None,
    http_api_owner: HttpApiOwner | None = None,
    ccip_client: CcipClient | None = None,
    ccip_pack_client: CcipPackClient | None = None,
    animetrace_client: AnimeTraceClient | None = None,
    history_request: OneBotRequest | None = None,
    journal_model: ModelPort | None = None,
    journal_publish_candidate: QZoneHttpPublisher | None = None,
    qq_registry_dir: Path | None = None,
) -> FastAPI:
    config = Config.model_validate(config.model_dump())
    if pinned_revision is None:
        config = resolve_saved(config)
    if dev_web_bypass and config.mode != "offline":
        raise ValueError("development web bypass requires offline mode")
    credentials.validate(live=config.mode == "live")
    if journal_publish_candidate is not None and type(journal_publish_candidate) is not QZoneHttpPublisher:
        raise ValueError("journal candidate must be the trusted runtime CGI adapter")
    secret_directory = Path(config.db_path).resolve().parent
    store = Store(Path(config.db_path), instance_id=config.instance_id, bot_id=config.bot_id)
    settings = SettingsService(store, config)
    if core_restart is not None:
        async def record_runtime_ready() -> None:
            if core_restart.startup_receipt is None:
                return
            receipt, decision = core_restart.startup_receipt
            await settings.record_runtime_decision(
                decision, actor=receipt.actor, revision=receipt.saved_revision,
                request_id=cast(str, receipt.request_id),
            )
            core_restart.startup_receipt = None
            assert conversation is not None
            conversation.runtime.accepting = True

        core_restart.ready = record_runtime_ready
    policy = Policy(store, bot_id=config.bot_id, mode=config.mode)
    memory_service = MemoryService(
        store, policy, self_nickname_enabled=config.self_nickname_enabled,
        self_nickname_groups=tuple(config.self_nickname_groups),
    )
    domain_learning_service = DomainLearningService(store, policy)
    knowledge_service = KnowledgeService(store, policy)
    graph_service = GraphService(store, policy, knowledge_service, memory=memory_service)
    sticker_assets = StickerStore(store, policy)
    sticker_descriptions: StickerDescriptionRunner | None = None
    character_service = (
        CharacterIdentityService(store, policy) if config.character_teaching_enabled else None
    )
    social_enabled = bool(
        config.worldbook_enabled
        and config.worldbook_social_evidence_enabled
        and config.worldbook_allowed_groups
    )
    social_experience_service = (
        SocialExperienceService(
            store,
            policy,
            enabled=True,
            social_evidence_enabled=True,
            episode_query_rerank_enabled=config.episode_query_rerank_enabled,
            allowed_groups=tuple(config.worldbook_allowed_groups),
        )
        if social_enabled
        else None
    )
    affection_service = AffectionService(store, policy, ArchiveService(store, policy))
    retrieval_service = retrieval or RetrievalService(
        memory_service, query_planner_enabled=config.retrieval_query_planner_enabled,
        cross_group_sharing_enabled=config.cross_group_sharing_enabled,
        knowledge=knowledge_service,
        graph=graph_service if config.retrieval_query_planner_enabled else None,
        personal_graph_enabled=config.retrieval_query_planner_enabled,
    )
    actions = Actions(store, policy, prices=config.model_prices)
    search_port = search_client or SearXNGClient(config.search_endpoint or None)
    if search_client is not None and search_client.destination != (
        search_destination(config.search_endpoint) if config.search_endpoint else None
    ):
        raise ValueError("search adapter destination does not match configuration")
    fetch_port = web_fetch_owner or WebFetchOwner(config.web_fetch_hosts)
    if fetch_port.allowed_hosts != frozenset(config.web_fetch_hosts):
        raise ValueError("web fetch adapter hosts do not match configuration")

    api_port = http_api_owner or HttpApiOwner(config.http_api_hosts)
    if api_port.allowed_hosts != frozenset(config.http_api_hosts):
        raise ValueError("HTTP API adapter hosts do not match configuration")

    extra_specs = list((search_port.tool_spec(),) if (
        search_port.available and "network.search" in config.tool_capabilities
    ) else ())
    if fetch_port.available and "network.fetch" in config.tool_capabilities:
        extra_specs.append(fetch_port.tool_spec())
    if api_port.available:
        extra_specs.extend(spec for spec in api_port.tool_specs()
                           if spec.requested_capabilities <= frozenset(config.tool_capabilities))
    tools = Tools(
        timezone=config.timezone,
        extra_specs=extra_specs,
        approved_capabilities=frozenset({"clock.read", "network.search", "network.fetch",
                                         "network.http.read", "network.http.write"}),
    )
    video_tools = Tools(
        timezone=config.timezone,
        extra_specs=[api_port.video_step_spec(),
                     *(spec for spec in api_port.tool_specs() if spec.id == "http.get")] if (
            config.video_metadata_enabled and api_port.available
            and "network.http.read" in config.tool_capabilities
        ) else [],
        approved_capabilities=frozenset({"clock.read", "network.http.read"}),
    )
    current_visual_resolver = visual_resolver
    current_quoted_visual_resolver = quoted_visual_resolver
    character_reference_owner = (
        CharacterReferenceOwner(config.character_reference_path, bot_id=config.bot_id)
        if config.character_reference_path else None
    )
    reference_pack = (
        load_reference_pack(config.character_reference_path)
        if config.character_recognition_enabled and config.character_reference_path else None
    )
    if reference_pack is not None and reference_pack.bot_id != config.bot_id:
        raise ValueError("character reference pack does not belong to this bot")
    ccip_port = (
        ccip_client or CcipClient(config.ccip_endpoint or None)
        if config.character_recognition_enabled else None
    )
    anime_port = (
        animetrace_client or AnimeTraceClient(
            config.animetrace_endpoint or None, model=config.animetrace_model or None,
        ) if config.character_recognition_enabled else None
    )
    if ccip_client is not None and ccip_client.identify_destination() != (
        config.ccip_endpoint.rstrip("/") + "/identify-multi" if config.ccip_endpoint else None
    ):
        raise ValueError("CCIP adapter destination does not match configuration")
    if animetrace_client is not None and (
        animetrace_client.destination != (config.animetrace_endpoint or None)
        or animetrace_client.model != (config.animetrace_model or None)
    ):
        raise ValueError("AnimeTrace adapter destination/model does not match configuration")
    character_actions = CharacterActions(
        actions, recognition=CharacterRecognitionService(ccip_port, reference_pack) if ccip_port else None,
        animetrace=anime_port,
    ) if config.character_recognition_enabled else None
    pack_client = (ccip_pack_client or CcipPackClient(config.ccip_endpoint or None)
                   if character_reference_owner is not None else None)
    if ccip_pack_client is not None and ccip_pack_client.destination != (
        config.ccip_endpoint.rstrip("/") + "/build-series-pack" if config.ccip_endpoint else None
    ):
        raise ValueError("CCIP pack adapter destination does not match configuration")
    cookie_name = "omubot_session_" + config.instance_id
    sessions: dict[str, float] = {}
    admin_sessions: dict[str, float] = {}
    admin_cookie = cookie_name + "_admin"
    development_session = secrets.token_urlsafe(32) if dev_web_bypass else ""
    login_attempts: deque[float] = deque(maxlen=10)
    napcat_clients: dict[str, NapCatClient] = {}
    napcat_lock = asyncio.Lock()
    conversation: Conversation | None = None
    memory_archive: ArchiveService | None = None
    history_owner: RecentHistoryOwner | None = None
    history_seed_tasks: dict[str, asyncio.Task[None]] = {}
    memory_spool: EncryptedTextSpool | None = None
    research_events: ResearchEvents | None = None
    research_spool: EncryptedTextSpool | None = None
    research_storage_error = ""
    memory_runner: MemoryExtractionRunner | None = None
    graph_extraction_runner: GraphExtractionRunner | None = None
    auto_apply_runner: LearningAutoApplyRunner | None = None
    slang_review_runner: SlangReviewRunner | None = None
    story_reader: StoryArcStore | None = None
    journal_owner: JournalOwner | None = None
    journal_composer: JournalComposer | None = None
    journal_publisher: JournalPublisher | None = None
    journal_storage_error = ""
    journal_recovered_deliveries = 0
    dream_runner: ManualDreamRunner | None = None
    memory_wakeup = asyncio.Event()
    websocket_connected = False
    feedback_ingress_eligible = False
    last_heartbeat = 0.0
    reverse_sender = ReverseOneBotSender(config.bot_id, config.send_timeout) if reverse_ws else None
    qq_transport: OneBotSender | ReverseOneBotSender | None = None
    started = False
    last_error = ""
    generation_id = secrets.token_hex(16)
    boot_time = monotonic()
    mutations = 0
    mutation_epoch = 0

    async def close_napcat(token: str) -> None:
        client = napcat_clients.pop(token, None)
        if client is not None:
            await client.close()

    async def prune_napcat() -> None:
        while True:
            await asyncio.sleep(15)
            async with napcat_lock:
                for token in list(napcat_clients):
                    if admin_sessions.get(token, 0) <= monotonic():
                        await close_napcat(token)

    async def clear_recorded_memory_spools() -> None:
        """Clear only spool paths explicitly retained in validated settings history."""
        try:
            active_spool = (config.memory_capture_storage_paths()[0] if (
                config.memory_capture_enabled or config.journal_enabled and config.memory_spool_dir
            ) else None)
            seen: set[Path] = set()
            for revision in await store.settings_versions():
                document = await store.settings_version(revision)
                if document is None:
                    raise ValueError("memory_spool_cleanup_failed")
                # Revisions without storage have nothing to clean, even when
                # unrelated historical settings no longer meet current limits.
                if (document.get("memory_spool_dir", "") == ""
                        and document.get("memory_key_file", "") == ""):
                    continue
                recorded = validate_document(config, document)
                if not recorded.memory_spool_dir or not recorded.memory_key_file:
                    continue
                spool_path, key_path = recorded.memory_capture_storage_paths()
                if active_spool is not None and spool_path == active_spool:
                    continue
                if spool_path in seen:
                    continue
                seen.add(spool_path)
                spool = EncryptedTextSpool(
                    spool_path,
                    key_path,
                    binding_id=spool_binding_id(recorded.bot_id, recorded.db_path, spool_path, key_path),
                )
                await asyncio.to_thread(spool.clear)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ValueError("memory_spool_cleanup_failed") from None

    async def offer_role_life_contact(source: ScheduleDayRecord | StoryCommit) -> None:
        """Observe the contact hook separately from the already committed business result.

        The offer only registers a bounded local candidate. Its model/send lifecycle
        belongs to Conversation and never delays a Schedule or Story response.
        """
        settings = config.proactive_contact
        if settings is None or not settings.enabled or conversation is None:
            return
        if isinstance(source, StoryCommit) and source.duplicate:
            return
        app.state.contact_hook_observed = True
        try:
            if isinstance(source, ScheduleDayRecord):
                await conversation.offer_schedule_contact(source)
            else:
                await conversation.offer_story_contact(
                    Scope(bot_id=source.event.bot_id, group_id=source.event.group_id),
                    source.arc.arc_id, source.arc.revision,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The primary commit has succeeded. Preserve it and expose the hook
            # failure rather than relabel it as a failed primary operation.
            app.state.contact_hook_last_error = (
                exc.code if isinstance(exc, OperationError) else "contact_hook_failed"
            )
            app.state.contact_hook_failure_count += 1
        else:
            app.state.contact_hook_last_error = ""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        nonlocal conversation, started, memory_archive, memory_spool, memory_runner, history_owner
        nonlocal story_reader, dream_runner, auto_apply_runner, slang_review_runner, sticker_descriptions
        nonlocal journal_owner, graph_extraction_runner
        nonlocal journal_composer, journal_publisher, journal_storage_error, journal_recovered_deliveries
        nonlocal research_events, research_spool, research_storage_error
        nonlocal current_visual_resolver, current_quoted_visual_resolver
        nonlocal qq_transport
        async with AsyncExitStack() as cleanup:
            await store.open()
            cleanup.push_async_callback(store.close)
            if journal_publish_candidate is not None:
                cleanup.push_async_callback(journal_publish_candidate.client.aclose)
            research_events = ResearchEvents(store, policy)
            if config.research_enabled:
                try:
                    spool_path, key_path = config.research_storage_paths()
                    key = await asyncio.to_thread(load_research_key, key_path)
                    research_spool = EncryptedTextSpool(
                        spool_path, key_path, require_cleaner=True,
                        binding_id=spool_binding_id(config.bot_id, config.db_path, spool_path, key_path),
                    )
                    await asyncio.to_thread(research_spool.purge_expired)
                    if not await asyncio.to_thread(research_spool.cleaner_available):
                        raise EncryptedTextSpoolError("deadline_cleaner_unavailable")
                    research_events = ResearchEvents(
                        store, policy, enabled=True, groups=config.research_groups,
                        spool=research_spool, key=key,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Phase1 research storage cannot stop the ordinary chat application.
                    research_storage_error = "research_storage_unavailable"
                    research_spool = None
            cleanup.push_async_callback(research_events.close)
            _app.state.research_events = research_events
            async def stop_history_seeds() -> None:
                for task in history_seed_tasks.values():
                    task.cancel()
                await asyncio.gather(*history_seed_tasks.values(), return_exceptions=True)
            await sticker_assets.open()
            cleanup.push_async_callback(sticker_assets.close)
            storylet_engine = (
                StoryletEngine(
                    load_storylet_registry(secret_directory / "config" / "worldbook" / "storylets"),
                    enabled=True,
                    allowed_groups=tuple(config.worldbook_allowed_groups),
                )
                if (
                    config.worldbook_enabled
                    and config.worldbook_storylet_enabled
                    and config.worldbook_schedule_projection_enabled
                    and config.worldbook_allowed_groups
                )
                else None
            )

            def authorize_storylet(_db: StoreConnection) -> bool:
                return (
                    config.worldbook_enabled
                    and config.worldbook_storylet_enabled
                    and config.worldbook_schedule_projection_enabled
                )

            dream_enabled = bool(
                config.worldbook_enabled
                and config.worldbook_dream_proposal_enabled
                and config.worldbook_allowed_groups
            )
            dream_source = _dream_source_fingerprint(config) if dream_enabled else None

            def authorize_admin_story(_db: StoreConnection) -> bool:
                return bool(config.worldbook_enabled and config.worldbook_allowed_groups)

            def authorize_dream(_db: StoreConnection) -> bool:
                return dream_enabled

            def authorize_social(
                db: StoreConnection, scope: Scope, experience_id: str
            ) -> bool:
                if social_experience_service is None:
                    return False
                social_experience_service.assert_current_transaction(
                    db, scope, experience_id
                )
                return True

            story_reader = (
                StoryArcStore(
                    store,
                    enabled=True,
                    assembled=True,
                    authorize_admin=authorize_admin_story,
                    authorize_storylet=authorize_storylet if storylet_engine is not None else None,
                    authorize_social=authorize_social if social_enabled else None,
                    approved_storylet_engine=storylet_engine,
                    authorize_dream=authorize_dream if dream_enabled else None,
                    approved_dream_validator=(
                        DreamValidator(
                            enabled=True,
                            allowed_groups=tuple(config.worldbook_allowed_groups),
                        )
                        if dream_enabled
                        else None
                    ),
                    approved_dream_source_fingerprint=dream_source,
                )
                if (config.worldbook_enabled and config.worldbook_allowed_groups)
                else None
            )
            canon_chat_enabled = bool(
                config.worldbook_enabled
                and config.worldbook_chat_projection_enabled
                and config.worldbook_allowed_groups
            )
            canon_schedule_enabled = bool(
                config.worldbook_enabled
                and config.worldbook_schedule_projection_enabled
                and config.worldbook_allowed_groups
            )
            canon_registry = (
                load_canon_registry(
                    secret_directory / "config" / "worldbook" / "canon",
                    enabled=True,
                    allowed_groups=tuple(config.worldbook_allowed_groups),
                )
                if canon_chat_enabled or canon_schedule_enabled
                else None
            )
            schedule_life = (
                ScheduleLife(
                    store,
                    story=story_reader,
                    enabled=True,
                    allowed_groups=tuple(config.worldbook_allowed_groups),
                    default_timezone=config.timezone,
                    storylet_registry_fingerprint=(
                        storylet_engine.registry.fingerprint if storylet_engine is not None else None
                    ),
                    storylet_groups=(storylet_engine.allowed_groups if storylet_engine is not None else ()),
                )
                if (
                    config.worldbook_enabled
                    and config.worldbook_schedule_projection_enabled
                    and config.worldbook_allowed_groups
                )
                else None
            )
            climate_engine = ClimateEngine(store=store) if config.climate_mode != "off" else None
            official_calendar = (
                OfficialCalendar(secret_directory / "cache" / "official-calendar")
                if config.mode == "live"
                and (config.climate_mode != "off" or bool(config.group_calendar_events))
                else None
            )
            if reverse_sender is not None:
                cleanup.push_async_callback(reverse_sender.close)
            await settings.start(pinned_revision)
            await clear_recorded_memory_spools()
            model_port = model
            sender_port = sender

            def make_model(selected: Config) -> ModelPort:
                profile = selected.selected_model
                key = credentials.model_keys.get(selected.active_model, "")
                if selected.active_model == config.for_task("reply").active_model:
                    key = key or credentials.model_key
                if not key.strip():
                    raise ValueError("missing model profile credential")
                client = httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=config.model_timeout
                )
                cleanup.push_async_callback(client.aclose)
                return configured_model(profile, key, client)

            if model_port is None:
                model_port = (
                    OfflineModel() if config.mode == "offline" else make_model(config.for_task("reply"))
                )
            cleanup.push_async_callback(model_port.close)
            thinker_port = thinker
            if config.thinker_enabled and thinker_port is None:
                if config.mode == "offline":
                    thinker_port = OfflineThinker()
                elif config.for_task("thinker").active_model == config.for_task("reply").active_model:
                    thinker_port = model_port
                else:
                    thinker_port = make_model(config.for_task("thinker"))
            if thinker_port is not None and thinker_port is not model_port:
                cleanup.push_async_callback(thinker_port.close)
            if dream_enabled and story_reader is not None and dream_source is not None:
                dream_port = dream_model
                if dream_port is None and config.mode == "live":
                    dream_config = config.for_task("dream")
                    if dream_config.active_model == config.for_task("reply").active_model:
                        dream_port = model_port
                    elif (
                        thinker_port is not None
                        and dream_config.active_model == config.for_task("thinker").active_model
                    ):
                        dream_port = thinker_port
                    else:
                        dream_port = make_model(dream_config)
                if dream_port is not None:
                    if dream_port is not model_port and dream_port is not thinker_port:
                        cleanup.push_async_callback(dream_port.close)
                    dream_runner = ManualDreamRunner(
                        story_reader,
                        actions,
                        policy,
                        dream_port,
                        model_config=config.for_task("dream"),
                        allowed_groups=tuple(config.worldbook_allowed_groups),
                        source_fingerprint=dream_source,
                    )
            memory_port: ModelPort | None = None
            if config.memory_capture_enabled or config.graph_extraction_enabled:
                memory_config = config.for_task("memory")
                if memory_model is not None:
                    memory_port = memory_model
                elif config.mode == "offline":
                    memory_port = model_port
                elif memory_config.active_model == config.for_task("reply").active_model:
                    memory_port = model_port
                elif (
                    thinker_port is not None
                    and memory_config.active_model == config.for_task("thinker").active_model
                ):
                    memory_port = thinker_port
                else:
                    memory_port = make_model(memory_config)
                if memory_port is not model_port and memory_port is not thinker_port:
                    cleanup.push_async_callback(memory_port.close)
            if config.memory_capture_enabled or config.journal_enabled:
                try:
                    spool_path, key_path = config.memory_capture_storage_paths()
                    metadata = os.lstat(spool_path)
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise EncryptedTextSpoolError("invalid_encrypted_text_spool")
                    memory_spool = EncryptedTextSpool(
                        spool_path,
                        key_path,
                        require_cleaner=True,
                        binding_id=spool_binding_id(config.bot_id, config.db_path, spool_path, key_path),
                    )
                    # Startup immediately verifies the pre-created key and applies TTL.
                    await asyncio.to_thread(memory_spool.purge_expired)
                    if not await asyncio.to_thread(memory_spool.cleaner_available):
                        raise EncryptedTextSpoolError("deadline_cleaner_unavailable")
                except asyncio.CancelledError:
                    raise
                except (OSError, ValueError, EncryptedTextSpoolError):
                    if config.memory_capture_enabled:
                        raise ValueError("memory_capture_storage_unavailable") from None
                    memory_spool = None
                    journal_storage_error = "journal_archive_unavailable"
                if memory_spool is not None:
                    memory_archive = ArchiveService(store, policy, text_spool=memory_spool)
            if config.memory_capture_enabled:
                assert memory_archive is not None
                memory_service.bind_self_nickname_archive(memory_archive)
                cleanup.callback(memory_service.unbind_self_nickname_archive, memory_archive)
                assert memory_port is not None
                memory_config = config.for_task("memory")
                auto_apply_runner = LearningAutoApplyRunner(
                    memory_archive, memory_service, domain_learning_service, actions, memory_port,
                    actor=config.bot_id, provider=memory_config.policy_provider,
                    model_name=memory_config.model, timeout=config.model_timeout,
                    enabled=config.learning_auto_apply_enabled,
                    allowed_groups=config.learning_auto_apply_groups,
                    allowed_domains=config.learning_auto_apply_domains,
                )
                active_auto_apply = auto_apply_runner

                slang_review_runner = SlangReviewRunner(
                    memory_archive, domain_learning_service, actions, memory_port,
                    actor=config.bot_id, provider=memory_config.policy_provider,
                    model_name=memory_config.model, enabled=config.slang_machine_review_enabled,
                    allowed_groups=config.memory_capture_groups, timeout=config.model_timeout,
                )
                active_slang_review = slang_review_runner
                cleanup.callback(active_slang_review.disable)
                observation_archive = memory_archive

                async def observe_and_prepare_source(
                    source: ArchiveSourceRecord, bundle: MemoryExtractionBundle,
                ) -> None:
                    text = await observation_archive.read_local_text(
                        config.bot_id, source.source_id, source.scope,
                    )
                    observation = await domain_learning_service.observe_before_seal(
                        actor=config.bot_id, scope=source.scope, source_id=source.source_id,
                        expected_source_revision=source.source_revision, text=text,
                    )
                    _app.state.learning_observations_last_write = observation
                    if config.slang_machine_review_enabled:
                        _app.state.slang_review_last_report = await active_slang_review.review_source(
                            source, text,
                        )
                    if config.learning_auto_apply_enabled:
                        await active_auto_apply.prepare_source(source, bundle)

                async def apply_learned_candidates(
                    source: ArchiveSourceRecord, candidates: tuple[AutoCandidateRef, ...]
                ) -> AutoApplyReport:
                    observation = await domain_learning_service.aggregate_after_candidates(
                        actor=config.bot_id, scope=source.scope, source_id=source.source_id,
                        expected_source_revision=source.source_revision,
                        candidates=tuple(
                            ObservationCandidateRef(ref.candidate_id, ref.candidate_revision)
                            for ref in candidates if ref.domain in {"slang", "style"}
                        ),
                    )
                    _app.state.learning_observations_last_write = observation
                    report = await active_auto_apply.process_source(
                        source, candidates
                    )
                    _app.state.learning_auto_apply_last_report = report
                    return report

                memory_runner = MemoryExtractionRunner(
                    memory_archive,
                    memory_service,
                    actions,
                    memory_port,
                    actor=config.bot_id,
                    provider=memory_config.policy_provider,
                    model_name=memory_config.model,
                    timeout=config.model_timeout,
                    timezone=ZoneInfo(config.timezone),
                    allowed_groups=tuple(config.memory_capture_groups),
                    extraction_domains=tuple(config.memory_extraction_domains),
                    domain_learning=domain_learning_service,
                    before_seal=observe_and_prepare_source,
                    after_candidates=apply_learned_candidates,
                    after_source=(
                        active_auto_apply.forget_source if config.learning_auto_apply_enabled else None
                    ),
                )
            else:
                memory_config = config.for_task("memory")
                # Historical receipt governance needs no spool and cannot invoke this port.
                auto_apply_runner = LearningAutoApplyRunner(
                    ArchiveService(store, policy), memory_service, domain_learning_service,
                    actions, model_port,
                    actor=config.bot_id, provider=memory_config.policy_provider,
                    model_name=memory_config.model, enabled=False,
                )
            sticker_sender_port = sticker_sender
            if sender_port is None:
                if config.mode == "offline":
                    sender_port = OfflineSender()
                elif reverse_sender is not None:
                    sender_port = reverse_sender
                    sticker_sender_port = reverse_sender
                else:
                    onebot_sender = OneBotSender(
                        httpx.AsyncClient(trust_env=False, follow_redirects=False),
                        config.onebot_endpoint, credentials.onebot, config.bot_id,
                    )
                    sender_port = onebot_sender
                    sticker_sender_port = onebot_sender
            if sticker_sender_port is not None and sticker_sender_port is not sender_port:
                cleanup.push_async_callback(sticker_sender_port.close)
            cleanup.push_async_callback(sender_port.close)
            # Register only the account proved by the read-only transport probe.
            # A failed HTTP identity check must not reserve a permanent binding.
            http_online = True
            if isinstance(sender_port, OneBotSender):
                await sender_port.verify_identity()
                http_online = await sender_port.probe_online()
            if isinstance(sender_port, OneBotSender | ReverseOneBotSender):
                qq_transport = sender_port
                ownership = QQAccountOwnership(
                    config.bot_id, config.instance_id, await store.qq_database_id(), store.path,
                    registry_dir=qq_registry_dir,
                )
                delivery = QQDelivery(store, config.bot_id, config.qq_delivery_limits, ownership=ownership)
                await delivery.open(defer_ownership=isinstance(sender_port, ReverseOneBotSender))
                # This early callback also covers partial startup. At shutdown
                # Actions drains transport before its owner releases this lock.
                cleanup.push_async_callback(delivery.close)
                actions.bind_qq_delivery(
                    delivery, lambda governed_sender=sender_port: governed_sender.connection_generation,
                )
                if not http_online:
                    await actions.hold_qq_transport("qq_offline")
            if current_visual_resolver is None and config.visual_url_hosts and config.mode == "live":
                read_message = (
                    sender_port.get_msg
                    if isinstance(sender_port, OneBotSender | ReverseOneBotSender) else None
                )
                native_visual = CurrentEventVisualResolver(config.visual_url_hosts, read_message=read_message)
                current_visual_resolver = native_visual
                if current_quoted_visual_resolver is None and read_message is not None:
                    current_quoted_visual_resolver = native_visual
            cleanup.push_async_callback(search_port.close)
            cleanup.push_async_callback(fetch_port.close)
            cleanup.push_async_callback(api_port.close)
            if ccip_port is not None:
                cleanup.push_async_callback(ccip_port.close)
            if pack_client is not None:
                cleanup.push_async_callback(pack_client.close)
            if anime_port is not None:
                cleanup.push_async_callback(anime_port.close)
            cleanup.push_async_callback(tools.close)
            cleanup.push_async_callback(video_tools.close)
            cleanup.push_async_callback(actions.close)
            interactions: OneBotInteractions | None = None
            interaction_ready: Callable[[], None] | None = None
            if any(capability.startswith("onebot.") for capability in config.tool_capabilities):
                if isinstance(sender_port, ReverseOneBotSender):
                    if sender_port.bot_id != config.bot_id:
                        raise ValueError("interaction sender bot does not match configuration")

                    def reverse_interaction_ready() -> None:
                        assert isinstance(sender_port, ReverseOneBotSender)
                        if not sender_port.ready:
                            raise OperationError("onebot_not_ready")

                    interaction_ready = reverse_interaction_ready

                    interactions = OneBotInteractions(
                        actions, bot_id=config.bot_id,
                        transport_identity=interaction_transport_identity(
                            config.instance_id, config.bot_id, None,
                        ),
                        request=sender_port.request_envelope, transport_ready=interaction_ready,
                    )
                elif isinstance(sender_port, OneBotSender):
                    if (sender_port.bot_id != config.bot_id
                            or sender_port.endpoint.rstrip("/") != config.onebot_endpoint.rstrip("/")):
                        raise ValueError("interaction sender identity does not match configuration")
                    interactions = OneBotInteractions(
                        actions, bot_id=config.bot_id,
                        transport_identity=interaction_transport_identity(
                            config.instance_id, config.bot_id, sender_port.endpoint,
                        ),
                        request=sender_port.request_envelope,
                    )
                elif config.mode == "live":
                    raise ValueError("enabled interactions require an existing OneBot transport owner")
            conversation = Conversation(
                config,
                store,
                policy,
                actions,
                model_port,
                sender_port,
                tools,
                thinker=thinker_port,
                climate_engine=climate_engine,
                official_calendar=official_calendar,
                schedule_life=schedule_life,
                story=story_reader,
                canon=canon_registry if canon_chat_enabled else None,
                retrieval=retrieval_service,
                memory=memory_service,
                characters=character_service,
                character_actions=character_actions,
                onebot_interactions=interactions,
                food=FoodOwner(store, policy, settings=settings) if config.food_enabled else None,
                affection=affection_service,
                social=social_experience_service,
                domain_learning=domain_learning_service,
                visual_resolver=current_visual_resolver,
                quoted_visual_resolver=current_quoted_visual_resolver,
                sticker_catalog=sticker_assets.catalog,
                sticker_asset_resolver=sticker_assets,
                sticker_store=sticker_assets,
                sticker_sender=sticker_sender_port,
                video_tools=video_tools,
                research_events=research_events,
            )
            schedule_runner: DailyScheduleRunner | None = None
            if schedule_life is not None and config.mode == "live":
                schedule_config = config.for_task("schedule")
                schedule_port = (
                    model_port
                    if schedule_config.active_model == config.for_task("reply").active_model
                    else thinker_port
                    if thinker_port is not None
                    and schedule_config.active_model == config.for_task("thinker").active_model
                    else make_model(schedule_config)
                )
                if schedule_port is not model_port and schedule_port is not thinker_port:
                    cleanup.push_async_callback(schedule_port.close)
                schedule_runner = DailyScheduleRunner(
                    schedule_life,
                    actions,
                    policy,
                    schedule_port,
                    budget=conversation.budget,
                    model_config=schedule_config,
                    persona=_schedule_persona_points(config),
                    allowed_groups=tuple(config.worldbook_allowed_groups),
                    canon_registry=(
                        canon_registry if canon_schedule_enabled else None
                    ),
                )
            storylet_runner = (
                StoryletDailyRunner(
                    schedule_life,
                    story_reader,
                    storylet_engine,
                    bot_id=config.bot_id,
                )
                if (
                    config.mode == "live"
                    and schedule_runner is not None
                    and schedule_life is not None
                    and story_reader is not None
                    and storylet_engine is not None
                )
                else None
            )
            conversation.start()
            if config.journal_enabled:
                journal_owner = JournalOwner(
                    store, policy, story_reader, archive=memory_archive,
                    fiction_groups=tuple(config.worldbook_allowed_groups) if config.worldbook_enabled else (),
                )
                if story_reader is not None:
                    selected_journal = config.for_task("journal")
                    journal_port = journal_model
                    if journal_port is None:
                        if (config.mode == "offline" or
                                selected_journal.active_model == config.for_task("reply").active_model):
                            journal_port = model_port
                        elif (thinker_port is not None and
                              selected_journal.active_model == config.for_task("thinker").active_model):
                            journal_port = thinker_port
                        elif (memory_port is not None and
                              selected_journal.active_model == config.for_task("memory").active_model):
                            journal_port = memory_port
                        else:
                            journal_port = make_model(selected_journal)
                    if (journal_port is not model_port and journal_port is not thinker_port
                            and journal_port is not memory_port):
                        cleanup.push_async_callback(journal_port.close)
                    journal_composer = JournalComposer(
                        journal_owner, actions, journal_port, provider=selected_journal.policy_provider,
                        model_name=selected_journal.model, budget=conversation.budget, enabled=True,
                    )
                journal_publisher = JournalPublisher(
                    journal_owner, actions, journal_publish_candidate or UnvalidatedQZonePublisher(),
                    gate=JournalPublishGate(
                        enabled=True, allowed_groups=tuple(config.journal_allowed_groups),
                        allow_live_publish=config.journal_allow_live_publish,
                        allowed_live_uins=tuple(config.journal_allowed_live_uins),
                    ),
                )
                journal_recovered_deliveries = await journal_publisher.recover_interrupted()
            if "onebot.history" in config.tool_capabilities:
                if history_request is not None:
                    if config.mode != "offline":
                        raise ValueError("injected history transport requires offline mode")
                    history_port = history_request
                    history_identity = interaction_transport_identity(
                        config.instance_id, config.bot_id, None,
                    )
                    history_ready = None
                elif isinstance(sender_port, OneBotSender | ReverseOneBotSender):
                    history_port = sender_port.request_envelope
                    history_identity = interaction_transport_identity(
                        config.instance_id, config.bot_id,
                        sender_port.endpoint if isinstance(sender_port, OneBotSender) else None,
                    )
                    history_ready = (interaction_ready
                                     if isinstance(sender_port, ReverseOneBotSender) else None)
                else:
                    raise ValueError("enabled history requires the existing OneBot transport")
                history_owner = RecentHistoryOwner(
                    actions, ArchiveService(store, policy), request=history_port,
                    transport_identity=history_identity,
                    allowed_groups=tuple(config.memory_capture_groups),
                    bot_ids=frozenset(config.known_bot_ids), external=config.mode == "live",
                    transport_ready=history_ready,
                )
            if core_restart is not None and core_restart.startup_receipt is not None:
                conversation.runtime.accepting = False
            cleanup.push_async_callback(conversation.close)
            cleanup.push_async_callback(stop_history_seeds)
            if config.graph_extraction_enabled:
                assert memory_port is not None
                graph_extraction_runner = GraphExtractionRunner(
                    graph_service, actions, memory_port, conversation.budget, config,
                    enabled=True,
                    targets=tuple(Scope(bot_id=config.bot_id, group_id=group)
                                  for group in config.graph_extraction_groups),
                )
                cleanup.push_async_callback(graph_extraction_runner.close)
            vision_config = config.for_task("vision")
            if vision_config.selected_model.vision_enabled:
                vision_port = vision_model
                if vision_port is None:
                    vision_port = (model_port
                                   if vision_config.active_model == config.for_task("reply").active_model
                                   else OfflineModel() if config.mode == "offline"
                                   else make_model(vision_config))
                if vision_port is not model_port and vision_port is not thinker_port:
                    cleanup.push_async_callback(vision_port.close)
                sticker_descriptions = StickerDescriptionRunner(
                    sticker_assets, actions, vision_config, vision_port, conversation.budget,
                )
                cleanup.push_async_callback(sticker_descriptions.close)
            _app.state.health_stopping = False
            _app.state.schedule_task = None
            _app.state.memory_task = None
            _app.state.rws_feedback_task = None
            _app.state.domain_revocation_task = None
            _app.state.contact_hook_observed = False
            _app.state.contact_hook_last_error = ""
            _app.state.contact_hook_failure_count = 0
            _app.state.schedule_last_report = None
            _app.state.storylet_last_report = None
            _app.state.memory_last_report = None
            _app.state.domain_revocation_observed = False
            _app.state.rws_feedback_observed = False
            _app.state.schedule_last_error = ""
            _app.state.storylet_last_error = ""
            _app.state.memory_last_error = ""
            _app.state.domain_revocation_last_error = ""
            _app.state.storylet_health_enabled = storylet_runner is not None
            _app.state.rws_feedback_last_error = ""
            _app.state.rws_feedback_settled_count = 0
            _app.state.rws_feedback_rewarded_count = 0
            if config.rws_feedback_enabled:
                active_feedback = conversation.rws_feedback

                async def feedback_loop() -> None:
                    nonlocal feedback_ingress_eligible
                    while True:
                        if (last_heartbeat > 0 and monotonic() - last_heartbeat >= 60
                                and feedback_ingress_eligible):
                            feedback_ingress_eligible = False
                            active_feedback.connect_ingress(False)
                            active_feedback.connect_notice_ingress(False)
                            _app.state.rws_feedback_last_error = "ingress_heartbeat_expired"
                        try:
                            measurements = await active_feedback.settle_due()
                        except OperationError as exc:
                            feedback_ingress_eligible = False
                            active_feedback.connect_ingress(False)
                            active_feedback.connect_notice_ingress(False)
                            _app.state.rws_feedback_last_error = exc.code
                        else:
                            _app.state.rws_feedback_observed = True
                            _app.state.rws_feedback_settled_count += len(measurements)
                            _app.state.rws_feedback_rewarded_count += sum(
                                item.reward is not None for item in measurements
                            )
                        await asyncio.sleep(5)

                feedback_task = asyncio.create_task(feedback_loop())
                _app.state.rws_feedback_task = feedback_task

                async def stop_feedback_loop() -> None:
                    feedback_task.cancel()
                    await asyncio.gather(feedback_task, return_exceptions=True)
                    active_feedback.connect_ingress(False)
                    active_feedback.connect_notice_ingress(False)

                cleanup.push_async_callback(stop_feedback_loop)
            if schedule_runner is not None:

                async def schedule_loop() -> None:
                    while True:
                        now = datetime.now(UTC)
                        try:
                            report = await schedule_runner.run_due(now)
                            _app.state.schedule_last_report = report
                            _app.state.schedule_last_error = ""
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            _app.state.schedule_last_error = (
                                exc.code if isinstance(exc, OperationError) else "schedule_runtime_failed"
                            )
                        else:
                            for day in report.committed:
                                scope = Scope(bot_id=day.bot_id, group_id=day.group_id)
                                if scope in report.attempted and scope not in report.reused:
                                    await offer_role_life_contact(day)
                        if storylet_runner is not None:
                            try:
                                storylet_report = await storylet_runner.run_due(now)
                                _app.state.storylet_last_report = storylet_report
                                _app.state.storylet_last_error = ""
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                _app.state.storylet_last_error = (
                                    exc.code if isinstance(exc, OperationError) else "storylet_runtime_failed"
                                )
                            else:
                                for commit in storylet_report.committed:
                                    await offer_role_life_contact(commit)
                        await asyncio.sleep(900)

                schedule_task = asyncio.create_task(schedule_loop())
                _app.state.schedule_task = schedule_task

                async def stop_schedule_loop() -> None:
                    schedule_task.cancel()
                    await asyncio.gather(schedule_task, return_exceptions=True)

                cleanup.push_async_callback(stop_schedule_loop)

            async def domain_revocation_loop() -> None:
                while True:
                    try:
                        await domain_learning_service.reconcile_pending_source_revocations(limit=32)
                        await domain_learning_service.expire_episodes(limit=32)
                        await memory_service.expire_matters(limit=32)
                        _app.state.domain_revocation_observed = True
                        _app.state.domain_revocation_last_error = ""
                    except asyncio.CancelledError:
                        raise
                    except OperationError as exc:
                        _app.state.domain_revocation_last_error = exc.code
                    except Exception:
                        _app.state.domain_revocation_last_error = "domain_revocation_failed"
                    await asyncio.sleep(60)

            domain_revocation_task = asyncio.create_task(domain_revocation_loop())
            _app.state.domain_revocation_task = domain_revocation_task

            async def stop_domain_revocation_loop() -> None:
                domain_revocation_task.cancel()
                await asyncio.gather(domain_revocation_task, return_exceptions=True)

            cleanup.push_async_callback(stop_domain_revocation_loop)
            if memory_runner is not None and memory_spool is not None:
                active_memory_runner = memory_runner
                active_memory_spool = memory_spool

                async def memory_loop() -> None:
                    while True:
                        try:
                            _app.state.memory_last_report = await active_memory_runner.run_once()
                            _app.state.memory_last_error = ""
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            _app.state.memory_last_error = "memory_runtime_failed"
                        try:
                            await asyncio.to_thread(active_memory_spool.purge_expired)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            _app.state.memory_last_error = "memory_spool_cleanup_failed"
                        try:
                            await asyncio.wait_for(memory_wakeup.wait(), timeout=60)
                        except TimeoutError:
                            pass
                        memory_wakeup.clear()

                memory_task = asyncio.create_task(memory_loop())
                _app.state.memory_task = memory_task

                async def stop_memory_loop() -> None:
                    memory_task.cancel()
                    await asyncio.gather(memory_task, return_exceptions=True)

                cleanup.push_async_callback(stop_memory_loop)
            cleanup.callback(auto_apply_runner.disable)
            reaper = asyncio.create_task(prune_napcat())
            started = True
            try:
                yield
            finally:
                started = False
                _app.state.health_stopping = True
                reaper.cancel()
                await asyncio.gather(reaper, return_exceptions=True)
                async with napcat_lock:
                    for token in list(napcat_clients):
                        await close_napcat(token)
                sessions.clear()
                admin_sessions.clear()

    app = FastAPI(title="Omubot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def restart_admission(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        nonlocal mutations, mutation_epoch
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)
        if core_restart is not None and core_restart.pending:
            return JSONResponse({"error": "runtime_pending"}, status_code=409)
        mutations += 1
        mutation_epoch += 1
        try:
            return await call_next(request)
        finally:
            mutations -= 1

    def bearer(value: str | None, expected: str) -> bool:
        return value is not None and secrets.compare_digest(value, "Bearer " + expected)

    def reject_browser_write(request: Request) -> None:
        if request.headers.get("origin") is not None or request.headers.get("sec-fetch-site") not in {
            None,
            "none",
        }:
            raise HTTPException(403, "cross_origin_denied")

    def require_admin(request: Request) -> None:
        reject_browser_write(request)
        if not bearer(request.headers.get("authorization"), credentials.admin):
            raise HTTPException(401, "unauthorized")

    async def body(request: Request, *, max_bytes: int = 65536) -> bytes:
        result = bytearray()
        async for chunk in request.stream():
            if len(result) + len(chunk) > max_bytes:
                raise HTTPException(413, "input_limit")
            result.extend(chunk)
        return bytes(result)

    def web_session_token(request: Request) -> str:
        return development_session if dev_web_bypass else request.cookies.get(admin_cookie, "")

    def development_access(request: Request) -> bool:
        if not dev_web_bypass:
            return False
        require_same_origin(request)
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
            host = request.url.hostname
            local_host = host == "localhost" or (host is not None and ipaddress.ip_address(host).is_loopback)
        except ValueError:
            local = local_host = False
        if not local or not local_host:
            raise HTTPException(403, "development_loopback_required")
        admin_sessions[development_session] = float("inf")
        return True

    def require_status(request: Request) -> None:
        if development_access(request):
            return
        session = request.cookies.get(cookie_name, "")
        expires = sessions.get(session, 0)
        if expires <= monotonic():
            sessions.pop(session, None)
        admin_expiry = admin_sessions.get(request.cookies.get(admin_cookie, ""), 0)
        if not (
            bearer(request.headers.get("authorization"), credentials.status)
            or expires > monotonic()
            or admin_expiry > monotonic()
        ):
            raise HTTPException(401, "unauthorized")

    def require_same_origin(request: Request) -> None:
        if request.headers.get("origin") not in {
            None,
            str(request.base_url).rstrip("/"),
        } or request.headers.get("sec-fetch-site") not in {None, "none", "same-origin"}:
            raise HTTPException(403, "cross_origin_denied")

    def require_web_admin(request: Request, *, write: bool = False) -> None:
        if development_access(request):
            return
        require_same_origin(request)
        if write and request.headers.get("x-omubot-request") != "1":
            raise HTTPException(403, "request_header_required")
        token = request.cookies.get(admin_cookie, "")
        expires = admin_sessions.get(token, 0)
        if expires <= monotonic():
            admin_sessions.pop(token, None)
            raise HTTPException(401, "unauthorized")

    @app.exception_handler(OperationError)
    async def operation_error(_request: Request, exc: OperationError) -> JSONResponse:
        nonlocal last_error
        last_error = exc.code
        status = (
            409
            if exc.code
            in {
                "conflict",
                "qq_revision_conflict", "qq_unknown_unreviewed", "qq_review_mismatch",
                "qq_write_in_flight", "qq_clock_inconsistent", "qq_connection_changed",
                "qq_write_cleanup_pending", "qq_test_batch_requires_idle_hold",
                "qq_test_batch_reused", "qq_test_batch_mismatch", "qq_test_batch_missing",
                "qq_test_batch_exhausted",
                "journal_revision_conflict", "journal_operation_conflict", "journal_invalid_transition",
                "journal_event_already_drafted", "journal_source_changed", "journal_source_unavailable",
                "journal_review_conflict", "journal_factual_source_deferred",
                "journal_composition_duplicate", "journal_composition_conflict",
                "journal_day_draft_budget", "journal_day_publish_budget", "journal_publication_conflict",
                "journal_source_expired", "journal_payload_changed", "journal_factual_body_changed",
                "journal_public_consent_conflict",
                "duplicate",
                "idempotency_conflict",
                "revision_conflict",
                "visibility_revision_conflict",
                "visibility_expired",
                "stale_visibility",
                "stale_sticker_revision", "stale_sticker_asset", "sticker_operation_conflict",
                "sticker_id_already_exists", "invalid_sticker_state",
                "stale_learning_visibility",
                "style_not_consumable",
                "character_reference_identity_conflict",
                "character_reference_contract_mismatch",
                "character_artifact_busy", "character_artifact_changed",
                "character_artifact_reference_mismatch",
                "graph_revision_conflict",
                "graph_review_required",
                "graph_object_revoked",
                "graph_payload_conflict",
                "stale_graph_projection",
                "graph_entity_kind_conflict",
                "stale_memory_fact",
                "memory_fact_not_applied",
                "candidate_not_reviewable",
                "conflict_pending",
                "correction_still_pending",
                "candidate_not_conflict_pending",
                "candidate_not_resolvable",
                "candidate_expired",
                "candidate_not_approved",
                "candidate_not_applicable",
                "matter_not_applicable", "matter_expired",
                "domain_learning_candidate_not_reviewable",
                "episode_management_not_current", "invalid_episode_transition", "episode_expired",
                "domain_learning_failure_revision_conflict",
                "domain_learning_retry_decision_mismatch",
                "domain_learning_retry_source_unavailable",
                "source_revision_conflict",
                "fact_not_active",
                "fact_not_current",
                "stale_memory_card_fact",
                "source_revoked",
                "stale_style_context",
                "style_item_disabled",
                "style_no_usable_items",
                "target_suppressed",
                "duplicate_active_fact",
                "correction_stale",
                "future_effect_not_due",
                "story_main_ambiguous",
                "story_arc_conflict",
                "dream_decision_finalized",
                "dream_arc_revision_conflict",
                "dream_decision_stale",
                "dream_rejected",
                "dream_proposal_conflict",
                "dream_decision_conflict",
                "knowledge_revision_conflict",
                "knowledge_source_removed",
                "knowledge_review_required",
                "knowledge_source_changed",
                "knowledge_chunk_changed",
                "domain_observation_job_stale",
                "domain_observation_job_not_reviewable",
                "slang_suggestion_revoked",
                "character_reference_busy",
                "character_reference_file_changed",
                "character_reference_backup_changed",
                "backup_destination_exists", "backup_changed", "release_changed", "release_config_changed",
            }
            else 404
            if exc.code
            in {
                "settings_version_not_found",
                "character_reference_character_missing",
                "candidate_not_found",
                "domain_learning_candidate_not_found",
                "episode_management_not_found",
                "domain_learning_failure_not_found",
                "fact_not_found",
                "style_item_not_found",
                "visibility_not_found",
                "style_profile_not_found",
                "dream_proposal_missing",
                "journal_draft_not_found",
                "journal_delivery_not_found", "journal_public_consent_unavailable",
                "dream_arc_missing",
                "knowledge_source_not_found",
                "graph_object_not_found",
                "domain_observation_job_not_found",
                "slang_suggestion_not_found",
            }
            else 422
            if exc.code
            in {
                "invalid_settings",
                "invalid_sticker_input", "invalid_sticker_identifier", "invalid_sticker_revision",
                "invalid_journal_body", "invalid_journal_hash", "invalid_journal_actor",
                "invalid_journal_scope", "invalid_journal_source", "invalid_journal_operation",
                "invalid_journal_draft", "invalid_journal_limit",
                "invalid_journal_delivery", "journal_public_consent_invalid",
                "invalid_journal_cursor",
                "journal_public_projection_invalid",
                "journal_public_event_invalid", "journal_approval_scope_invalid",
                "journal_resolution_invalid",
                "journal_resolution_receipt_required",
                "invalid_sticker_bytes", "invalid_sticker_media_type",
                "invalid_sticker_media_magic_mismatch", "invalid_sticker_media_animated_unsupported",
                "invalid_sticker_media_dimension_limit", "invalid_sticker_media_pixel_budget",
                "invalid_sticker_media_invalid_image",
                "invalid_visibility_grant",
                "invalid_learning_visibility",
                "invalid_knowledge_visibility",
                "visibility_budget_exceeded",
                "invalid_character_reference_pack",
                "invalid_character_reference_file",
                "invalid_character_reference_backup",
                "invalid_character_reference_merge",
                "invalid_character_artifact_file", "invalid_character_artifact_backup",
                "invalid_character_batch_sources", "invalid_character_public_image",
                "invalid_character_crop", "character_batch_too_large",
                "character_crop_too_large", "character_source_hash_mismatch",
                "invalid_release_config", "invalid_release_manifest", "invalid_release_file",
                "invalid_release_sha", "backup_invalid", "release_instance_mismatch",
                "invalid_memory_cursor",
                "invalid_memory_page_limit",
                "invalid_memory_status",
                "invalid_memory_card_category", "invalid_memory_card_revision",
                "invalid_memory_card_query", "invalid_memory_search_limit",
                "invalid_memory_decision",
                "invalid_memory_matter_limit", "invalid_memory_matter_state",
                "matter_subject_mismatch", "matter_observed_at_mismatch",
                "invalid_domain_learning_decision",
                "invalid_domain_learning_reason",
                "invalid_domain_learning_revision",
                "invalid_domain_learning_cursor",
                "invalid_domain_learning_limit",
                "invalid_domain_learning_status",
                "invalid_episode_decay", "invalid_self_alias_surface", "invalid_self_alias_time",
                "invalid_dream_cursor",
                "invalid_dream_page_limit",
                "invalid_knowledge_limit",
                "invalid_knowledge_cursor",
                "invalid_knowledge_query",
                "invalid_knowledge_budget",
                "knowledge_query_budget_exceeded",
                "invalid_graph_identifier",
                "invalid_graph_kind",
                "invalid_graph_page",
                "invalid_graph_alias",
                "invalid_graph_review",
                "invalid_graph_budget",
                "invalid_graph_seeds",
                "graph_self_fact_confirmation_required",
                "graph_target_value_mismatch",
                "graph_self_fact_api_required",
            }
            else 403
            if exc.code
            in {
                "denied",
                "source_author_mismatch",
                "qq_scope_mismatch", "qq_account_mismatch",
                "visibility_denied",
                "visibility_personal_source",
                "story_scope_denied",
                "journal_scope_denied",
                "journal_live_gate_closed", "journal_disabled",
                "dream_scope_denied",
                "dream_authorization_denied",
                "dream_authorization_required",
                "character_reference_public_confirmation_required",
                "character_public_approval_required",
                "character_reference_bot_mismatch",
            }
            else 503
        )
        return JSONResponse({"error": exc.code}, status_code=status)

    @app.exception_handler(ValidationError)
    async def validation_error(_request: Request, _exc: ValidationError) -> JSONResponse:
        return JSONResponse({"error": "invalid_input"}, status_code=422)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> JSONResponse:
        return web_response(
            HealthResponse(
                ready=started
                and not policy.transport_fault
                and not (core_restart is not None and core_restart.pending),
                generation=generation_id,
            )
        )

    web_root = Path(__file__).with_name("web_static")
    app.mount("/web", StaticFiles(directory=web_root, check_dir=False), name="web")

    @app.get("/", response_class=HTMLResponse)
    async def page() -> HTMLResponse:
        try:
            document = (web_root / "index.html").read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise HTTPException(503, "web_build_missing") from exc
        return HTMLResponse(
            document,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/session", response_model=AuthResponse)
    async def login(request: Request) -> Response:
        origin = request.headers.get("origin")
        if origin is not None and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "cross_origin_denied")
        now = monotonic()
        while login_attempts and now - login_attempts[0] > 60:
            login_attempts.popleft()
        if len(login_attempts) >= 10:
            raise HTTPException(429, "login_rate_limit")
        login_attempts.append(now)
        data = TokenRequest.model_validate_json(await body(request))
        if not secrets.compare_digest(data.token, credentials.status):
            raise HTTPException(401, "unauthorized")
        for token, expiry in list(sessions.items()):
            if expiry <= now:
                del sessions[token]
        if len(sessions) >= 64:
            del sessions[next(iter(sessions))]
        session = secrets.token_urlsafe(32)
        sessions[session] = now + 900
        response = JSONResponse({"authenticated": True})
        response.set_cookie(cookie_name, session, httponly=True, samesite="strict", max_age=900)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/logout", response_model=AuthResponse)
    async def logout(request: Request) -> Response:
        if request.headers.get("origin") not in {None, str(request.base_url).rstrip("/")}:
            raise HTTPException(403, "cross_origin_denied")
        sessions.pop(request.cookies.get(cookie_name, ""), None)
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(cookie_name)
        return response

    @app.get("/api/status", response_model=StatusSnapshot)
    async def status(request: Request) -> JSONResponse:
        require_status(request)
        result = await store.status()
        result.update(
            {
                "dev_web_bypass": dev_web_bypass,
                "mode": config.mode,
                "model_config": config.model_status(),
                "instance": {"id": config.instance_id, "name": config.instance_name, "bot_id": config.bot_id},
                "policy_revision": await policy.revision(),
                "config_revision": hashlib.sha256(config.model_dump_json().encode()).hexdigest()[:12],
                "queue_depth": conversation.runtime.queued if conversation else 0,
                "connection": "connected"
                if websocket_connected and last_heartbeat > 0 and monotonic() - last_heartbeat < 60
                else ("isolated" if config.mode == "offline" else "disconnected"),
                "onebot_api_ready": reverse_sender.ready if reverse_sender is not None else False,
                "last_error": last_error,
                "runtime_errors": conversation.failures if conversation else 0,
                "transport_fault": policy.transport_fault,
                "active_transports": len(policy.active_transports),
                "active_sessions": len(conversation.runtime.active) if conversation else 0,
                "interruptions": conversation.runtime.interrupted if conversation else 0,
                "model_budget": cast(JsonValue, conversation.budget.snapshot()) if conversation else {},
            }
        )
        return web_response(StatusSnapshot.model_validate(result))

    @app.get("/api/diagnostics", response_model=DiagnosticsSnapshot)
    async def diagnostics(request: Request) -> JSONResponse:
        require_status(request)
        result = await store.diagnostics()
        result["participation"] = (
            cast(JsonValue, conversation.participation_snapshot()) if conversation is not None else []
        )
        return web_response(DiagnosticsSnapshot.model_validate(result))

    @app.get("/api/admin/diagnostics/health", response_model=AggregateHealthSnapshot)
    async def aggregate_health(request: Request) -> JSONResponse:
        require_web_admin(request)
        stopping = not started or app.state.health_stopping

        def component(
            name: Literal["schedule", "storylet", "memory", "rws_feedback", "domain_revocation"],
            task: asyncio.Task[None] | None, *, enabled: bool,
            report_present: bool, error: str, failure_count: int | None = None,
        ) -> HealthComponent:
            state: Literal["healthy", "degraded", "unknown", "disabled", "stopping"]
            code: Literal["disabled", "no_report", "observed", "owner_error", "report_failed",
                          "task_unavailable", "task_finished", "task_cancelled", "stopping"]
            if stopping:
                state, code = "stopping", "stopping"
            elif not enabled:
                state, code = "disabled", "disabled"
            elif task is None:
                state, code = "unknown", "task_unavailable"
            elif task.cancelled():
                state, code = "degraded", "task_cancelled"
            elif task.done():
                state, code = "degraded", "task_finished"
            elif error:
                state, code = "degraded", "owner_error"
            elif failure_count:
                state, code = "degraded", "report_failed"
            elif not report_present:
                state, code = "unknown", "no_report"
            else:
                state, code = "healthy", "observed"
            return HealthComponent(component=name, state=state, code=code,
                                   report_present=report_present, failure_count=failure_count)

        schedule_report: ScheduleRunReport | None = app.state.schedule_last_report
        storylet_report: StoryletRunReport | None = app.state.storylet_last_report
        memory_report: MemoryExtractionReport | None = app.state.memory_last_report
        rows = [
            component("schedule", app.state.schedule_task, enabled=app.state.schedule_task is not None,
                      report_present=schedule_report is not None, error=app.state.schedule_last_error,
                      failure_count=len(schedule_report.failed) if schedule_report is not None else None),
            component("storylet", app.state.schedule_task, enabled=app.state.storylet_health_enabled,
                      report_present=storylet_report is not None, error=app.state.storylet_last_error,
                      failure_count=len(storylet_report.failed) if storylet_report is not None else None),
            component("memory", app.state.memory_task, enabled=app.state.memory_task is not None,
                      report_present=memory_report is not None, error=app.state.memory_last_error,
                      failure_count=len(memory_report.errors) if memory_report is not None else None),
            component("rws_feedback", app.state.rws_feedback_task, enabled=config.rws_feedback_enabled,
                      report_present=app.state.rws_feedback_observed,
                      error=app.state.rws_feedback_last_error),
            component("domain_revocation", app.state.domain_revocation_task, enabled=True,
                      report_present=app.state.domain_revocation_observed,
                      error=app.state.domain_revocation_last_error),
        ]
        # Event-driven commit hooks have no ticker/task. This row only reports
        # their observed local handoff, not contact model or recipient health.
        contact_enabled = config.proactive_contact is not None and config.proactive_contact.enabled
        contact_state: Literal["healthy", "degraded", "unknown", "disabled", "stopping"]
        contact_code: Literal["disabled", "no_report", "observed", "owner_error", "stopping"]
        contact_observed = app.state.contact_hook_observed or (
            conversation is not None and conversation.contact_hook_observed)
        contact_error = app.state.contact_hook_last_error or (
            conversation.contact_hook_error if conversation is not None else "")
        contact_failures = app.state.contact_hook_failure_count + (
            conversation.contact_hook_errors if conversation is not None else 0)
        if stopping:
            contact_state, contact_code = "stopping", "stopping"
        elif not contact_enabled:
            contact_state, contact_code = "disabled", "disabled"
        elif contact_error:
            contact_state, contact_code = "degraded", "owner_error"
        elif contact_observed:
            contact_state, contact_code = "healthy", "observed"
        else:
            contact_state, contact_code = "unknown", "no_report"
        rows.append(HealthComponent(
            component="contact_hooks", state=contact_state, code=contact_code,
            report_present=contact_observed,
            failure_count=contact_failures,
        ))
        states = {row.state for row in rows}
        overall: Literal["healthy", "degraded", "unknown", "disabled", "stopping"] = (
            "stopping" if stopping else "degraded" if "degraded" in states
            else "unknown" if "unknown" in states or config.mode == "offline"
            else "healthy" if "healthy" in states else "disabled"
        )
        return web_response(AggregateHealthSnapshot(
            state=overall, observed_at=datetime.now(UTC).isoformat(), components=rows,
        ))

    @app.get("/api/admin/diagnostics/context-observations", response_model=ContextObservationSnapshot)
    async def context_observations(request: Request) -> JSONResponse:
        require_web_admin(request)
        if not config.context_observation_enabled:
            return web_response(context_observation_snapshot((), enabled=False))
        if conversation is None:
            return JSONResponse({"error": "context_observation_unavailable"}, status_code=503)
        return web_response(conversation.context_observation_snapshot())

    @app.get("/api/admin/graph/health", response_model=GraphObservationView)
    async def graph_health(request: Request, group_id: str) -> JSONResponse:
        require_web_admin(request)
        if not config.graph_observation_enabled:
            return web_response(GraphObservationView(enabled=False, report=None))
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        report = await graph_service.health_snapshot(actor="web-admin", scope=scope)
        return web_response(GraphObservationView(enabled=True, report=report))

    @app.get("/api/admin/diagnostics/group-state", response_model=GroupBoardView)
    async def group_state(request: Request, group_id: str) -> JSONResponse:
        require_web_admin(request)
        if conversation is None:
            raise OperationError("stopping")
        snapshot = await conversation.group_state_snapshot(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
        )
        return web_response(GroupBoardView.model_validate_json(json.dumps(asdict(snapshot))))

    @app.get("/api/admin/diagnostics/topic-edges", response_model=TopicEdgeDiagnosticsSnapshot)
    async def topic_edge_diagnostics(request: Request) -> JSONResponse:
        """Read bounded topic attribution evidence for an authenticated admin."""
        require_web_admin(request)
        edges = conversation.topic_edge_snapshot() if conversation is not None else []
        return web_response(
            TopicEdgeDiagnosticsSnapshot.model_validate({"topic_edges": cast(JsonValue, edges)})
        )

    @app.post("/api/admin/persona/preview", response_model=PersonaPreviewResponse)
    async def preview_persona(request: Request) -> JSONResponse:
        """Compile an admin draft without saving or activating it."""
        require_web_admin(request, write=True)
        data = PersonaPreviewRequest.model_validate_json(await body(request))
        imported = PersonaSourceImporter().import_markdown(
            data.source_markdown, source_ref=PERSONA_SOURCE_REF, required=True
        )
        preview_issues = [
            PersonaPreviewIssue(code=issue.code, line=issue.line, message=issue.message)
            for issue in imported.issues[:16]
        ]
        compiled = None
        if not preview_issues:
            try:
                compiled = PersonaCompiler(max_system_chars=MAX_MODEL_SYSTEM_CHARS).compile(
                    imported.require_source()
                )
            except PersonaCompileError as exc:
                preview_issues.append(PersonaPreviewIssue(code="compile_error", line=1, message=str(exc)))
        preview = PersonaPreviewResponse(
            valid=not preview_issues,
            source_ref=PERSONA_SOURCE_REF,
            source_hash=imported.source_hash,
            version=compiled.version if compiled is not None else None,
            system=compiled.system if compiled is not None else "",
            issues=preview_issues,
        )
        return web_response(preview)

    @app.post("/admin/session", response_model=AuthResponse)
    async def admin_login(request: Request) -> Response:
        require_same_origin(request)
        if request.headers.get("x-omubot-request") != "1":
            raise HTTPException(403, "request_header_required")
        now = monotonic()
        while login_attempts and now - login_attempts[0] > 60:
            login_attempts.popleft()
        if len(login_attempts) >= 10:
            raise HTTPException(429, "login_rate_limit")
        login_attempts.append(now)
        data = TokenRequest.model_validate_json(await body(request))
        if not secrets.compare_digest(data.token, credentials.admin):
            raise HTTPException(401, "unauthorized")
        for token, expiry in list(admin_sessions.items()):
            if expiry <= now:
                del admin_sessions[token]
        if len(admin_sessions) >= 64:
            del admin_sessions[next(iter(admin_sessions))]
        token = secrets.token_urlsafe(32)
        admin_sessions[token] = now + 900
        response = JSONResponse({"authenticated": True}, headers={"Cache-Control": "no-store"})
        response.set_cookie(admin_cookie, token, httponly=True, samesite="strict", max_age=900)
        return response

    @app.post("/admin/logout", response_model=AuthResponse)
    async def admin_logout(request: Request) -> Response:
        if not development_access(request):
            require_same_origin(request)
            if request.headers.get("x-omubot-request") != "1":
                raise HTTPException(403, "request_header_required")
        token = web_session_token(request)
        admin_sessions.pop(token, None)
        async with napcat_lock:
            await close_napcat(token)
        response = JSONResponse({"authenticated": False}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(admin_cookie)
        return response

    @app.exception_handler(NapCatError)
    async def napcat_error(_request: Request, exc: NapCatError) -> JSONResponse:
        return JSONResponse(
            {"detail": "napcat_" + exc.code}, status_code=502, headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/admin/napcat/connect", response_model=NapcatStatus)
    async def napcat_connect(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        data = NapcatConnectRequest.model_validate_json(await body(request))
        token = web_session_token(request)
        async with napcat_lock:
            require_web_admin(request, write=True)
            if token not in napcat_clients and len(napcat_clients) >= 8:
                raise HTTPException(429, "busy")
            candidate = NapCatClient(data.endpoint, data.token, data.totp_code)
            try:
                await candidate.connect()
                state = await candidate.status()
                require_web_admin(request, write=True)
            except BaseException:
                await candidate.close()
                raise
            await close_napcat(token)
            napcat_clients[token] = candidate
            return web_response(NapcatStatus.model_validate(dict(connected=True, **state)))

    @app.get("/api/admin/napcat/runtime", response_model=NapcatRuntime)
    async def napcat_runtime(request: Request) -> JSONResponse:
        require_web_admin(request)
        if napcat_container is None:
            return web_response(NapcatRuntime())
        async with napcat_lock:
            require_web_admin(request)
            state = await napcat_container.state()
            require_web_admin(request)
            return web_response(NapcatRuntime.model_validate(state))

    @app.post("/api/admin/napcat/start", response_model=NapcatStatus)
    async def napcat_start(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        if napcat_container is None:
            raise HTTPException(409, "napcat_runtime_disabled")
        async with napcat_lock:
            require_web_admin(request, write=True)
            token = web_session_token(request)
            if token not in napcat_clients and len(napcat_clients) >= 8:
                raise HTTPException(429, "busy")
            await napcat_container.control("start")
            deadline = monotonic() + 15
            while True:
                require_web_admin(request, write=True)
                candidate = napcat_container.client()
                try:
                    await candidate.connect()
                    state = await candidate.status()
                    require_web_admin(request, write=True)
                except NapCatError as exc:
                    await candidate.close()
                    if exc.code != "upstream_unavailable":
                        raise
                    if monotonic() >= deadline:
                        raise NapCatError("container_not_ready") from None
                    await asyncio.sleep(0.5)
                    continue
                except BaseException:
                    await candidate.close()
                    raise
                await close_napcat(token)
                napcat_clients[token] = candidate
                return web_response(NapcatStatus.model_validate(dict(connected=True, **state)))

    @app.post("/api/admin/napcat/stop", response_model=NapcatRuntime)
    async def napcat_stop(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        if napcat_container is None:
            raise HTTPException(409, "napcat_runtime_disabled")
        async with napcat_lock:
            require_web_admin(request, write=True)
            state = await napcat_container.control("stop")
            for token, client in list(napcat_clients.items()):
                if client.endpoint == napcat_container.endpoint:
                    await close_napcat(token)
            require_web_admin(request, write=True)
            return web_response(NapcatRuntime.model_validate(state))

    async def napcat_state(request: Request, *, refresh: bool = False) -> JSONResponse:
        require_web_admin(request, write=refresh)
        async with napcat_lock:
            require_web_admin(request, write=refresh)
            client = napcat_clients.get(web_session_token(request))
            if client is None:
                return web_response(NapcatStatus())
            if refresh:
                await client.refresh_qr()
            state = await client.status()
            require_web_admin(request, write=refresh)
            return web_response(NapcatStatus.model_validate(dict(connected=True, **state)))

    @app.get("/api/admin/napcat/status", response_model=NapcatStatus)
    async def napcat_status(request: Request) -> JSONResponse:
        return await napcat_state(request)

    @app.post("/api/admin/napcat/refresh", response_model=NapcatStatus)
    async def napcat_refresh(request: Request) -> JSONResponse:
        return await napcat_state(request, refresh=True)

    @app.get("/api/admin/napcat/logs", response_model=NapcatLogs)
    async def napcat_logs(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        async with napcat_lock:
            require_web_admin(request, write=True)
            client = napcat_clients.get(web_session_token(request))
            if client is None:
                raise HTTPException(409, "napcat_not_connected")
            lines = await client.logs()
            require_web_admin(request, write=True)
            return web_response(
                NapcatLogs(lines=lines, detail="最近一次采样；可能含 NapCat 缓存，采样间隙可能遗漏。")
            )

    @app.post("/api/admin/napcat/disconnect", response_model=NapcatStatus)
    async def napcat_disconnect(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        async with napcat_lock:
            require_web_admin(request, write=True)
            await close_napcat(web_session_token(request))
        return web_response(NapcatStatus())

    @app.put("/api/admin/model-key", response_model=ModelCheckResponse)
    async def save_key(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        data = ModelAccessRequest.model_validate_json(await body(request))
        snapshot = await settings.snapshot()
        if data.expected_revision != snapshot["revision"]:
            raise HTTPException(409, "revision_conflict")
        saved = validate_document(config, cast(dict[str, JsonValue], snapshot["config"]))
        if data.profile not in saved.models or not data.api_key:
            raise HTTPException(422, "invalid_input")
        try:
            save_model_secret(secret_directory, data.profile, saved.models[data.profile], data.api_key)
        except (ValueError, OSError):
            raise HTTPException(400, "secret_save_failed") from None
        return web_response(ModelCheckResponse(ok=True, detail="运行密钥已保存；环境变量优先，重启后生效。"))

    @app.post("/api/admin/model-check", response_model=ModelCheckResponse)
    async def check_model(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        data = ModelAccessRequest.model_validate_json(await body(request))
        snapshot = await settings.snapshot()
        if data.expected_revision != snapshot["revision"]:
            raise HTTPException(409, "revision_conflict")
        selected = validate_document(config, cast(dict[str, JsonValue], snapshot["config"]))
        if data.profile not in selected.models:
            raise HTTPException(422, "invalid_input")
        profile = selected.models[data.profile]
        try:
            key = (
                data.api_key
                or os.environ.get(profile.api_key_env, "")
                or read_model_secret(secret_directory, data.profile, profile)
            )
            if not key:
                raise ValueError("missing key")
            async with (
                asyncio.timeout(30),
                httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client,
            ):
                port = configured_model(profile, key, client)
                try:
                    reply = await port.request(
                        ModelRequest(
                            model=profile.model,
                            tools=[],
                            messages=[Message(role="user", content="Connection test. Reply with OK only.")],
                        )
                    )
                    if not reply.text.strip() or reply.tool_call:
                        raise ValueError("invalid probe reply")
                finally:
                    await port.close()
        except (ValueError, OSError, OperationError, httpx.HTTPError, TimeoutError):
            return web_response(
                ModelCheckResponse(ok=False, detail="验证失败：检查地址、运行密钥、模型ID及服务可用性。")
            )
        return web_response(
            ModelCheckResponse(ok=True, detail="真实模型已返回文本；本次未发送聊天历史或QQ消息。")
        )

    @app.post("/api/admin/models", response_model=ModelCatalogResponse)
    async def model_catalog(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        data = ModelCatalogRequest.model_validate_json(await body(request))
        key = data.api_key.strip()
        if not key:
            snapshot = await settings.snapshot()
            if data.expected_revision != snapshot["revision"]:
                raise HTTPException(409, "revision_conflict")
            selected = validate_document(config, cast(dict[str, JsonValue], snapshot["config"]))
            if data.profile is None or data.profile not in selected.models:
                raise HTTPException(422, "catalog_key_required")
            profile = selected.models[data.profile]
            if profile.api_format != data.api_format or profile.endpoint != normalize_model_endpoint(
                data.api_format, data.endpoint
            ):
                raise HTTPException(422, "catalog_destination_mismatch")
            try:
                key = os.environ.get(profile.api_key_env, "") or read_model_secret(
                    secret_directory, data.profile, profile
                )
            except (ValueError, OSError):
                raise HTTPException(422, "catalog_key_required") from None
            if not key:
                raise HTTPException(422, "catalog_key_required")
        try:
            async with asyncio.timeout(12):
                catalog = await discover_models(data.api_format, data.endpoint, key)
        except CatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400, headers={"Cache-Control": "no-store"})
        except TimeoutError:
            return JSONResponse(
                {"error": "catalog_timeout"}, status_code=504, headers={"Cache-Control": "no-store"}
            )
        return web_response(
            ModelCatalogResponse(
                models=catalog.models,
                source_url=catalog.source_url,
                pages=catalog.pages,
                truncated=catalog.truncated,
                display_names=catalog.display_names,
            )
        )

    def qq_scope(update: QQPauseRequest) -> QQScopeKey | None:
        if update.account_id != config.bot_id:
            raise OperationError("qq_account_mismatch")
        if update.scope is None:
            return None
        if update.scope.bot_id != config.bot_id:
            raise OperationError("qq_scope_mismatch")
        return update.scope.key

    def qq_batch_view() -> QQTestBatchView | None:
        owner = actions.qq_delivery
        batch = None if owner is None else owner.test_batch_status()
        return None if batch is None else QQTestBatchView(
            batch_id=batch.batch_id, scope_key=batch.scope_key, write_limit=batch.write_limit,
            expires_in_seconds=max(0.0, batch.deadline - monotonic()),
            committed_cost=batch.committed_cost, ended=batch.ended, reason=batch.reason,
        )

    async def qq_view(scope_key: QQScopeKey | None = None) -> QQDeliveryView:
        owner = actions.qq_delivery
        if owner is None:
            return QQDeliveryView(available=False, limits=config.qq_delivery_limits)
        snapshot = await owner.refresh(scope_key)
        return QQDeliveryView(
            available=True, limits=config.qq_delivery_limits, snapshot=snapshot,
            local_held=owner.held(scope_key), local_hold_reason=owner.local_hold_reason(scope_key),
            connection_ready=qq_transport is not None and qq_transport.ready,
            waiting=owner.pending_count, in_flight=bool(snapshot.unsettled_action_ids),
            waiting_targets=[QQWaitingTargetView(scope_key=key, waiting=count)
                             for key, count in owner.pending_counts.items()],
            test_batch=qq_batch_view(),
        )

    @app.get("/api/qq-test-batch", response_model=QQBotBatchProof)
    async def qq_test_batch_proof(request: Request) -> JSONResponse:
        require_status(request)
        owner = actions.qq_delivery
        batch = qq_batch_view()
        return web_response(QQBotBatchProof(
            account_id=config.bot_id, available=owner is not None,
            held=owner is None or owner.held(None if batch is None else batch.scope_key),
            connection_ready=qq_transport is not None and qq_transport.ready, test_batch=batch,
        ))

    async def verify_qq_online() -> bool:
        if qq_transport is None:
            raise OperationError("qq_delivery_unavailable")
        if not qq_transport.ready:
            await actions.hold_qq_transport("qq_transport_unconfirmed")
            return False
        try:
            await qq_transport.verify_identity()
            online = await qq_transport.probe_online()
        except OperationError:
            await actions.hold_qq_transport("qq_transport_unconfirmed")
            raise
        if not online:
            await actions.hold_qq_transport("qq_offline")
        return online

    @app.get("/api/admin/qq-delivery", response_model=QQDeliveryView)
    async def qq_delivery_status(
        request: Request, kind: Literal["group", "private"] | None = None, target_id: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        if (kind is None) != (target_id is None):
            raise HTTPException(422, "exact_target_required")
        scope_key = None
        if kind is not None and target_id is not None:
            scope = (Scope(bot_id=config.bot_id, group_id=target_id) if kind == "group"
                     else PrivateScope(bot_id=config.bot_id, kind="private", private_user_id=target_id))
            scope_key = scope.key
        return web_response(await qq_view(scope_key))

    @app.post("/api/admin/qq-delivery/pause", response_model=QQDeliveryView)
    async def qq_delivery_pause(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = QQPauseRequest.model_validate_json(await body(request))
        scope_key = qq_scope(update)
        await actions.pause_qq(
            scope_key=scope_key, expected_revision=update.expected_revision,
            reason=update.reason, actor="web-admin",
        )
        return web_response(await qq_view(scope_key))

    @app.post("/api/admin/qq-delivery/resume", response_model=QQDeliveryView)
    async def qq_delivery_resume(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = QQResumeRequest.model_validate_json(await body(request))
        scope_key = qq_scope(update)
        await actions.resume_qq(
            scope_key=scope_key, expected_revision=update.expected_revision,
            reason=update.reason, actor="web-admin",
            reviewed_unknown_action_ids=tuple(update.reviewed_unknown_action_ids),
            verify_online=verify_qq_online, test_batch_id=update.test_batch_id,
        )
        return web_response(await qq_view(scope_key))

    @app.post("/api/admin/qq-delivery/batch/begin", response_model=QQDeliveryView)
    async def qq_delivery_begin_batch(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = QQBatchBeginRequest.model_validate_json(await body(request))
        if update.account_id != config.bot_id or update.scope.bot_id != config.bot_id:
            raise OperationError("qq_account_mismatch")
        await actions.begin_qq_test_batch(
            batch_id=update.batch_id, scope_key=update.scope.key, role="bot",
            duration_seconds=update.duration_seconds, actor="web-admin",
        )
        return web_response(await qq_view(update.scope.key))

    @app.post("/api/admin/qq-delivery/batch/end", response_model=QQDeliveryView)
    async def qq_delivery_end_batch(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = QQBatchEndRequest.model_validate_json(await body(request))
        if update.account_id != config.bot_id:
            raise OperationError("qq_account_mismatch")
        if update.scope is not None and update.scope.bot_id != config.bot_id:
            raise OperationError("qq_scope_mismatch")
        await actions.end_qq_test_batch(
            reason=update.reason, actor="web-admin", batch_id=update.batch_id,
        )
        return web_response(await qq_view(None if update.scope is None else update.scope.key))

    native_preparation = NativeUpdatePreparation(store)
    native_config_path = secret_directory / "config.toml"
    native_archives_path = secret_directory / "releases"
    native_backups_path = secret_directory / "backups"

    def native_available() -> bool:
        return (config.instance_id != "standalone" and store.path.name == "state.sqlite3"
                and native_config_path.is_file() and not native_config_path.is_symlink())

    def require_native_available() -> None:
        if not native_available():
            raise HTTPException(503, "native_instance_required")
        if native_archives_path.is_symlink() or native_backups_path.is_symlink():
            raise HTTPException(422, "invalid_native_directory")

    def scan_native_files() -> tuple[list[str], list[NativeBackupRecord], bool, bool]:
        archives: list[str] = []
        backups: list[NativeBackupRecord] = []
        if native_archives_path.is_dir() and not native_archives_path.is_symlink():
            with os.scandir(native_archives_path) as entries:
                for entry in entries:
                    if (entry.is_file(follow_symlinks=False) and entry.name.endswith(".zip")
                            and len(entry.name) <= 125):
                        archives.append(entry.name)
                        if len(archives) == 65:
                            break
        if native_backups_path.is_dir() and not native_backups_path.is_symlink():
            with os.scandir(native_backups_path) as entries:
                for entry in entries:
                    identity = entry.name.removeprefix("native-")
                    if (not entry.name.startswith("native-") or len(identity) != 32
                            or any(char not in "0123456789abcdef" for char in identity)
                            or not entry.is_dir(follow_symlinks=False)):
                        continue
                    receipt = Path(entry.path) / "receipt.json"
                    if receipt.is_file() and not receipt.is_symlink():
                        backups.append(NativeBackupRecord(
                            preparation_id=identity,
                            receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
                        ))
                        if len(backups) == 65:
                            break
        return sorted(archives[:64]), backups[:64], len(archives) > 64, len(backups) > 64

    def native_releases(data: NativeReleaseInputs) -> tuple[SourceRelease, SourceRelease | None]:
        require_native_available()
        if (data.prior_name is None) != (data.prior_sha256 is None):
            raise HTTPException(422, "native_prior_release_pair_required")
        target = SourceRelease.inspect(
            native_archives_path / data.target_name, expected_sha256=data.target_sha256,
        )
        prior = (SourceRelease.inspect(
            native_archives_path / data.prior_name, expected_sha256=data.prior_sha256,
        ) if data.prior_name is not None and data.prior_sha256 is not None else None)
        return target, prior

    def native_preparation_view(
        identity: str, receipt: BackupReceipt, *, schema_compatible: bool = False,
    ) -> NativePreparationView:
        plan = native_preparation.manual_plan(receipt, schema_compatible=schema_compatible)
        return NativePreparationView(
            preparation_id=identity, receipt_sha256=receipt.receipt_sha256,
            database_sha256=receipt.database_sha256, config_sha256=receipt.config_sha256,
            schema_version=receipt.schema, settings_revision=receipt.settings_revision,
            policy_revision=receipt.policy_revision, target_sha256=receipt.target_release.sha256,
            declared_prior_sha256=receipt.current_release.sha256 if receipt.current_release else None,
            plan=NativeManualPlanView(
                preparation_ready=plan.preparation_ready, blockers=list(plan.blockers),
                update_steps=list(plan.update_steps), rollback_steps=list(plan.rollback_steps),
                limitations=list(plan.limitations),
            ),
        )

    @app.get("/api/admin/releases", response_model=NativeReleaseStatusView)
    async def native_release_status(request: Request) -> JSONResponse:
        require_web_admin(request)
        snapshot = SettingsSnapshot.model_validate(await settings.snapshot())
        archives, backups, archives_truncated, backups_truncated = await asyncio.to_thread(scan_native_files)
        try:
            label = version("omubot-new")
        except PackageNotFoundError:
            label = "unrecorded"
        available = native_available()
        config_sha = (await asyncio.to_thread(
            lambda: hashlib.sha256(native_config_path.read_bytes()).hexdigest()
        ) if available else None)
        return web_response(NativeReleaseStatusView(
            managed_instance=available, instance_id=config.instance_id, bot_id=config.bot_id,
            version_label=label, config_sha256=config_sha,
            settings_revision=snapshot.revision, policy_revision=await policy.revision(),
            source_archives=archives, backups=backups,
            archives_truncated=archives_truncated, backups_truncated=backups_truncated,
        ))

    @app.post("/api/admin/releases/prepare", response_model=NativePreparationView)
    async def prepare_native_release(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        data = NativePreparationRequest.model_validate_json(await body(request))
        if not data.backup_scope_understood:
            raise HTTPException(403, "native_backup_scope_confirmation_required")
        target, prior = await asyncio.to_thread(native_releases, data)
        native_backups_path.mkdir(mode=0o700, exist_ok=True)
        receipt = await native_preparation.prepare(
            config_path=native_config_path,
            destination=native_backups_path / ("native-" + data.preparation_id),
            expected_config_sha256=data.expected_config_sha256,
            expected_settings_revision=data.expected_settings_revision,
            expected_policy_revision=data.expected_policy_revision,
            target_release=target, current_release=prior,
        )
        return web_response(await asyncio.to_thread(native_preparation_view, data.preparation_id, receipt))

    @app.post("/api/admin/releases/plan", response_model=NativePreparationView)
    async def inspect_native_plan(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        data = NativePlanRequest.model_validate_json(await body(request))
        target, prior = await asyncio.to_thread(native_releases, data)
        backup_directory = native_backups_path / ("native-" + data.preparation_id)
        if backup_directory.is_symlink():
            raise HTTPException(422, "invalid_native_directory")
        receipt = await asyncio.to_thread(
            native_preparation.inspect_backup, backup_directory,
            expected_receipt_sha256=data.receipt_sha256, target_release=target, current_release=prior,
        )
        return web_response(await asyncio.to_thread(
            native_preparation_view, data.preparation_id, receipt, schema_compatible=data.schema_compatible,
        ))

    @app.get("/api/admin/runtime", response_model=CoreRuntime)
    async def read_core_runtime(request: Request) -> JSONResponse:
        require_web_admin(request)
        snapshot = SettingsSnapshot.model_validate(await settings.snapshot())
        return web_response(
            CoreRuntime(
                instance_id=config.instance_id,
                generation=generation_id,
                uptime_seconds=max(0, monotonic() - boot_time),
                managed=core_restart is not None,
                pending=core_restart is not None and core_restart.pending,
                revision=snapshot.revision,
                effective_revision=snapshot.effective_revision,
                restart_required=snapshot.restart_required,
                active_sessions=len(conversation.runtime.active) if conversation else 0,
                queue_depth=conversation.runtime.queued if conversation else 0,
                last_restart_error=core_restart.last_error if core_restart else "",
            )
        )

    @app.post("/api/admin/runtime/restart", response_model=CoreRestartResponse)
    async def restart_core(request: Request, background: BackgroundTasks) -> JSONResponse:
        require_web_admin(request, write=True)
        update = CoreRestartRequest.model_validate_json(await body(request))
        if core_restart is None:
            raise HTTPException(503, "runtime_unmanaged")
        if conversation is None:
            raise HTTPException(409, "runtime_busy")
        # Shares the admission lock with every HTTP/WS submission, including its
        # database awaits. Never close an active turn or race an in-flight claim.
        async with conversation.submit_lock:
            if core_restart.pending:
                raise HTTPException(409, "runtime_pending")
            if mutations > 1:
                raise HTTPException(409, "runtime_busy")
            observed_mutation_epoch = mutation_epoch
            snapshot = SettingsSnapshot.model_validate(await settings.snapshot())
            if update.generation != generation_id or update.expected_revision != snapshot.revision:
                raise HTTPException(409, "runtime_stale")
            if (
                mutations > 1
                or mutation_epoch != observed_mutation_epoch
                or conversation.runtime.active
                or conversation.runtime.queued
            ):
                raise HTTPException(409, "runtime_busy")
            try:
                selected = validate_document(
                    config, cast(dict[str, JsonValue], snapshot.config.model_dump(mode="json"))
                )
                selected_credentials = core_restart.prepare(selected)
                selected_credentials.validate(live=selected.mode == "live")
            except (ValueError, OSError, OperationError):
                raise HTTPException(422, "runtime_config_invalid") from None
            core_restart.previous_revision = snapshot.effective_revision
            receipt = await settings.record_runtime_decision(
                "requested", actor="web-admin", revision=snapshot.revision,
                request_id=generation_id + ":" + secrets.token_hex(16),
            )
            if mutations > 1 or mutation_epoch != observed_mutation_epoch:
                await settings.record_runtime_decision(
                    "rejected", actor=receipt.actor, revision=receipt.saved_revision,
                    request_id=cast(str, receipt.request_id),
                )
                raise HTTPException(409, "runtime_busy")
            core_restart.requested_receipt = receipt
            core_restart.candidate = selected, selected_credentials, snapshot.revision
            conversation.runtime.accepting = False
            background.add_task(core_restart.shutdown)
            response = web_response(CoreRestartResponse(accepted=True, generation=generation_id))
            response.status_code = 202
            return response

    @app.get("/api/admin/config", response_model=SettingsSnapshot)
    async def read_settings(request: Request) -> JSONResponse:
        require_web_admin(request)
        return web_response(SettingsSnapshot.model_validate(await settings.snapshot()))

    @app.put("/api/admin/config", response_model=SettingsSnapshot)
    async def write_settings(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = SettingsWriteRequest.model_validate_json(await body(request))
        result = await settings.save(
            cast(dict[str, JsonValue], update.config.model_dump(mode="json")), update.expected_revision,
            actor="web-admin",
        )
        return web_response(SettingsSnapshot.model_validate(result))

    @app.post("/api/admin/config/rollback", response_model=SettingsSnapshot)
    async def rollback_settings(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = SettingsRollbackRequest.model_validate_json(await body(request))
        result = await settings.rollback(update.target_revision, update.expected_revision, actor="web-admin")
        return web_response(SettingsSnapshot.model_validate(result))

    def story_scope(group_id: str) -> Scope:
        if not config.worldbook_enabled or group_id not in config.worldbook_allowed_groups:
            raise OperationError("story_scope_denied")
        if story_reader is None:
            raise OperationError("story_unassembled")
        return Scope(bot_id=config.bot_id, group_id=group_id)

    def journal_scope(group_id: str) -> Scope:
        if not config.journal_enabled or group_id not in config.journal_allowed_groups:
            raise OperationError("journal_scope_denied")
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        if journal_owner is None:
            raise OperationError("journal_source_unavailable")
        return scope

    def journal_view(draft: JournalDraft) -> JournalDraftView:
        return JournalDraftView.model_validate({
            "draft_id": draft.draft_id, "root_id": draft.root_id, "revision": draft.revision,
            "source_event_id": draft.source_event_id, "source_hash": draft.source_hash,
            "body_hash": draft.body_hash, "content_hash": draft.content_hash, "state": draft.state,
            "created_at": draft.created_at, "group_id": draft.scope.group_id, "body": draft.body,
            "supersedes_draft_id": draft.supersedes_draft_id, "is_tip": draft.is_tip,
            "review": asdict(draft.review) if draft.review is not None else None,
            "content_kind": draft.content_kind,
        })

    @app.get("/api/admin/journal", response_model=JournalHeadPageView)
    async def list_journal(
        request: Request, group_id: str, limit: int = 64, cursor: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = journal_scope(group_id)
        assert journal_owner is not None
        page = await journal_owner.list_heads(actor="web-admin", scope=scope, limit=limit, cursor=cursor)
        return web_response(JournalHeadPageView(
            items=[JournalHeadView.model_validate(asdict(head)) for head in page.items],
            has_more=page.has_more,
            next_cursor=page.next_cursor,
        ))

    @app.get("/api/admin/journal/status", response_model=JournalStatusView)
    async def journal_status(request: Request, group_id: str) -> JSONResponse:
        require_web_admin(request)
        scope = journal_scope(group_id)
        assert journal_owner is not None and journal_publisher is not None
        owner = journal_owner
        await store.transaction(lambda db: owner.authorize_transaction(db, "web-admin", scope))
        live_available = True
        try:
            journal_publisher.assert_live_ready(scope)
        except OperationError as exc:
            if exc.code != "journal_live_gate_closed":
                raise
            live_available = False
        validation = (journal_publish_candidate.runtime_validation
                      if journal_publish_candidate is not None else None)
        return web_response(JournalStatusView(
            enabled=True, fiction_available=(config.worldbook_enabled and story_reader is not None
                                            and journal_composer is not None
                                            and group_id in config.worldbook_allowed_groups),
            archive_available=memory_archive is not None, storage_error=journal_storage_error,
            allow_live_publish=config.journal_allow_live_publish, live_available=live_available,
            wire_validated=journal_publisher.port.wire_validated,
            external_transport=journal_publisher.port.is_external,
            validation_profile=validation.profile_id if validation is not None else None,
            validated_account_digest=(hashlib.sha256(validation.account_id.encode()).hexdigest()
                                      if validation else None),
            recovered_deliveries=journal_recovered_deliveries,
        ))

    @app.get("/api/admin/journal/consents", response_model=JournalPublicConsentPageView)
    async def journal_consents(
        request: Request, group_id: str, limit: int = 64, cursor: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = journal_scope(group_id)
        assert journal_owner is not None
        page = await journal_owner.list_public_consents(
            actor="web-admin", scope=scope, limit=limit, cursor=cursor,
        )
        return web_response(JournalPublicConsentPageView.model_validate({
            "items": [asdict(item) for item in page.items], "has_more": page.has_more,
            "next_cursor": page.next_cursor,
        }))

    @app.get("/api/admin/journal/deliveries", response_model=JournalDeliveryPageView)
    async def journal_deliveries(
        request: Request, group_id: str, limit: int = 64, cursor: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = journal_scope(group_id)
        assert journal_publisher is not None
        items, has_more, next_cursor = await journal_publisher.list_deliveries(
            actor="web-admin", scope=scope, limit=limit, cursor=cursor,
        )
        return web_response(JournalDeliveryPageView(items=[JournalDeliveryView.model_validate(asdict(item))
                                                          for item in items], has_more=has_more,
                                                  next_cursor=next_cursor))

    @app.get("/api/admin/journal/deliveries/{delivery_id}", response_model=JournalDeliveryView)
    async def journal_delivery(request: Request, delivery_id: str, group_id: str) -> JSONResponse:
        require_web_admin(request)
        scope = journal_scope(group_id)
        assert journal_publisher is not None
        result = await journal_publisher.read_delivery(
            actor="web-admin", scope=scope, delivery_id=delivery_id
        )
        return web_response(JournalDeliveryView.model_validate(asdict(result)))

    @app.post("/api/admin/journal/preview-fiction", response_model=JournalFictionPreviewView)
    async def preview_journal_fiction(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = JournalFictionPreviewRequest.model_validate_json(await body(request))
        scope = journal_scope(update.group_id)
        story_scope(update.group_id)
        if journal_composer is None:
            raise OperationError("journal_source_unavailable")
        draft, decisions = await journal_composer.preview(
            actor="web-admin", scope=scope, operation_id=update.operation_id
        )
        return web_response(JournalFictionPreviewView.model_validate({
            "draft": journal_view(draft).model_dump() if draft is not None else None,
            "decisions": [asdict(decision) for decision in decisions],
        }))

    @app.post("/api/admin/journal/preview-factual", response_model=JournalDraftView)
    async def preview_journal_factual(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = JournalFactualPreviewRequest.model_validate_json(await body(request))
        scope = journal_scope(update.group_id)
        assert journal_owner is not None
        return web_response(journal_view(await journal_owner.preview_factual(
            actor="web-admin", scope=scope, consent_source_ids=update.consent_source_ids,
            operation_id=update.operation_id,
        )))

    @app.post("/api/admin/journal/publish", response_model=JournalDeliveryView)
    async def publish_journal(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = JournalPublishRequest.model_validate_json(await body(request))
        scope = journal_scope(update.group_id)
        assert journal_publisher is not None
        result = await journal_publisher.publish(
            actor="web-admin", scope=scope, draft_id=update.draft_id,
            expected_body_hash=update.expected_body_hash, expected_source_hash=update.expected_source_hash,
            expected_content_hash=update.expected_content_hash,
            operation_id=update.operation_id, mode=update.mode,
        )
        return web_response(JournalDeliveryView.model_validate(asdict(result)))

    @app.post("/api/admin/journal/resolve", response_model=JournalDeliveryView)
    async def resolve_journal(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = JournalResolveRequest.model_validate_json(await body(request))
        scope = journal_scope(update.group_id)
        assert journal_publisher is not None
        result = await journal_publisher.resolve_unknown(
            actor="web-admin", scope=scope, delivery_id=update.delivery_id,
            outcome=update.outcome, receipt=update.receipt,
        )
        return web_response(JournalDeliveryView.model_validate(asdict(result)))

    @app.get("/api/admin/journal/{draft_id}", response_model=JournalDraftView)
    async def read_journal(request: Request, draft_id: str, group_id: str) -> JSONResponse:
        require_web_admin(request)
        scope = journal_scope(group_id)
        assert journal_owner is not None
        return web_response(journal_view(await journal_owner.read(
            actor="web-admin", scope=scope, draft_id=draft_id,
        )))

    @app.post("/api/admin/journal", response_model=JournalDraftView)
    async def create_journal(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = JournalCreateRequest.model_validate_json(await body(request))
        scope = journal_scope(update.group_id)
        assert journal_owner is not None
        return web_response(journal_view(await journal_owner.create(
            actor="web-admin", scope=scope, source_event_id=update.source_event_id,
            body=update.body, operation_id=update.operation_id,
        )))

    @app.post("/api/admin/journal/revise", response_model=JournalDraftView)
    async def revise_journal(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = JournalRevisionRequest.model_validate_json(await body(request))
        scope = journal_scope(update.group_id)
        assert journal_owner is not None
        return web_response(journal_view(await journal_owner.revise(
            actor="web-admin", scope=scope, draft_id=update.draft_id, body=update.body,
            expected_body_hash=update.expected_body_hash, expected_source_hash=update.expected_source_hash,
            operation_id=update.operation_id,
        )))

    @app.post("/api/admin/journal/approve", response_model=JournalDraftView)
    async def approve_journal(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = JournalApprovalRequest.model_validate_json(await body(request))
        scope = journal_scope(update.group_id)
        assert journal_owner is not None
        return web_response(journal_view(await journal_owner.approve(
            actor="web-admin", scope=scope, draft_id=update.draft_id,
            expected_body_hash=update.expected_body_hash, expected_source_hash=update.expected_source_hash,
            operation_id=update.operation_id, approval_scope=update.approval_scope,
        )))

    @app.post("/api/admin/journal/reject", response_model=JournalDraftView)
    async def reject_journal(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = JournalDecisionRequest.model_validate_json(await body(request))
        scope = journal_scope(update.group_id)
        assert journal_owner is not None
        return web_response(journal_view(await journal_owner.reject(
            actor="web-admin", scope=scope, draft_id=update.draft_id,
            expected_body_hash=update.expected_body_hash, expected_source_hash=update.expected_source_hash,
            operation_id=update.operation_id,
        )))

    @app.post("/api/admin/journal/dry-run", response_model=JournalDryRunView)
    async def dry_run_journal(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = JournalDryRunRequest.model_validate_json(await body(request))
        scope = journal_scope(update.group_id)
        assert journal_owner is not None
        result = await journal_owner.dry_run(
            actor="web-admin", scope=scope, draft_id=update.draft_id,
            expected_body_hash=update.expected_body_hash, expected_source_hash=update.expected_source_hash,
        )
        return web_response(JournalDryRunView.model_validate(asdict(result)))

    def social_enabled_for(scope: Scope) -> bool:
        return bool(
            social_experience_service is not None
            and story_reader is not None
            and config.worldbook_enabled
            and config.worldbook_social_evidence_enabled
            and scope.group_id in config.worldbook_allowed_groups
        )

    def social_disabled_view() -> SocialProgressView:
        return SocialProgressView(
            capture_status="disabled",
            story_status="disabled",
            experience_id=None,
            error_code=None,
            retryable=False,
        )

    async def social_progress_for_episode(
        scope: Scope,
        episode_id: str,
        *,
        attempt: bool = False,
    ) -> SocialProgressView:
        if not social_enabled_for(scope):
            return social_disabled_view()
        assert social_experience_service is not None
        assert story_reader is not None

        if attempt:
            try:
                experience = await social_experience_service.capture_from_episode(
                    actor="web-admin", scope=scope, episode_id=episode_id
                )
            except OperationError as exc:
                if exc.code == "social_experience_invalidated":
                    capture_status, experience_id = (
                        await social_experience_service.read_capture_status(
                            actor="web-admin", scope=scope, episode_id=episode_id
                        )
                    )
                    if capture_status != "invalidated" or experience_id is None:
                        raise OperationError("invalid_social_experience") from exc
                    story_committed = await story_reader.has_social_experience(
                        scope, experience_id
                    )
                    return SocialProgressView(
                        capture_status="invalidated",
                        story_status="committed" if story_committed else "pending",
                        experience_id=experience_id,
                        error_code=None,
                        retryable=False,
                    )
                return SocialProgressView(
                    capture_status="failed",
                    story_status="waiting_for_capture",
                    experience_id=None,
                    error_code=exc.code,
                    retryable=True,
                )
            else:
                experience_id = experience.experience_id
        else:
            capture_status, experience_id = await social_experience_service.read_capture_status(
                actor="web-admin", scope=scope, episode_id=episode_id
            )
            if capture_status == "missing":
                return SocialProgressView(
                    capture_status="pending",
                    story_status="waiting_for_capture",
                    experience_id=None,
                    error_code=None,
                    retryable=True,
                )
            if experience_id is None:
                raise OperationError("invalid_social_experience")
            if capture_status == "invalidated":
                story_committed = await story_reader.has_social_experience(
                    scope, experience_id
                )
                return SocialProgressView(
                    capture_status="invalidated",
                    story_status="committed" if story_committed else "pending",
                    experience_id=experience_id,
                    error_code=None,
                    retryable=False,
                )

        story_committed = await story_reader.has_social_experience(scope, experience_id)
        if story_committed:
            return SocialProgressView(
                capture_status="captured",
                story_status="committed",
                experience_id=experience_id,
                error_code=None,
                retryable=False,
            )
        if not attempt:
            return SocialProgressView(
                capture_status="captured",
                story_status="pending",
                experience_id=experience_id,
                error_code=None,
                retryable=True,
            )
        try:
            commit = await story_reader.commit_social_experience(scope, experience_id)
        except OperationError as exc:
            capture_status, current_id = await social_experience_service.read_capture_status(
                actor="web-admin", scope=scope, episode_id=episode_id
            )
            if capture_status == "invalidated":
                if current_id is None:
                    raise OperationError("invalid_social_experience") from exc
                story_committed = await story_reader.has_social_experience(
                    scope, current_id
                )
                return SocialProgressView(
                    capture_status="invalidated",
                    story_status="committed" if story_committed else "pending",
                    experience_id=current_id,
                    error_code=None,
                    retryable=False,
                )
            if capture_status != "active":
                return SocialProgressView(
                    capture_status="pending",
                    story_status="waiting_for_capture",
                    experience_id=None,
                    error_code=exc.code,
                    retryable=True,
                )
            return SocialProgressView(
                capture_status="captured",
                story_status="pending",
                experience_id=experience_id,
                error_code=exc.code,
                retryable=True,
            )
        await offer_role_life_contact(commit)
        return SocialProgressView(
            capture_status="captured",
            story_status="committed",
            experience_id=experience_id,
            error_code=None,
            retryable=False,
        )

    @app.get("/api/admin/story/arcs", response_model=StoryArcListView)
    async def list_story_arcs(request: Request, group_id: str) -> JSONResponse:
        require_web_admin(request)
        scope = story_scope(group_id)
        assert story_reader is not None
        try:
            arcs = await story_reader.visible_stack(scope)
        except OperationError as exc:
            if exc.code != "story_main_missing":
                raise
            arcs = ()
        return web_response(StoryArcListView(items=[story_arc_view(arc) for arc in arcs]))

    @app.post("/api/admin/story/arcs", response_model=StoryArcView)
    async def create_story_main_arc(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = StoryArcCreateRequest.model_validate_json(await body(request))
        scope = story_scope(update.group_id)
        assert story_reader is not None
        try:
            existing = await story_reader.visible_stack(scope)
        except OperationError as exc:
            if exc.code != "story_main_missing":
                raise
            existing = ()
        if existing and existing[0].arc_id != update.arc_id:
            raise OperationError("story_main_ambiguous")
        arc = await story_reader.register_arc(
            StoryArcInput(
                bot_id=config.bot_id,
                arc_id=update.arc_id,
                role="main",
                group_ids=(scope.group_id,),
                title=update.title,
                stage=update.stage,
                author="web-admin",
            ),
            require_unique_main=True,
        )
        return web_response(story_arc_view(arc))

    @app.get("/api/admin/dream/proposals", response_model=DreamProposalPageView)
    async def list_dream_proposals(
        request: Request, group_id: str, limit: int = 32, after: str | None = None
    ) -> JSONResponse:
        require_web_admin(request)
        scope = story_scope(group_id)
        assert story_reader is not None
        page = await story_reader.list_dream_proposals_page(scope, limit=limit, after=after)
        return web_response(
            DreamProposalPageView(
                items=[dream_proposal_view(item) for item in page.proposals],
                next_cursor=page.next_cursor,
            )
        )

    @app.post("/api/admin/dream/propose", response_model=DreamProposalView)
    async def propose_dream(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = DreamProposeRequest.model_validate_json(await body(request))
        scope = story_scope(update.group_id)
        if dream_runner is None:
            raise OperationError(
                "dream_model_unavailable_offline" if config.mode == "offline" else "dream_runtime_unassembled"
            )
        return web_response(dream_proposal_view(await dream_runner.propose(scope, update.arc_id)))

    @app.post("/api/admin/dream/accept", response_model=DreamProposalView)
    async def accept_dream(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = DreamDecisionRequest.model_validate_json(await body(request))
        scope = story_scope(update.group_id)
        assert story_reader is not None
        record = await story_reader.read_dream_proposal(scope, update.proposal_id)
        if record.decision_status == "rejected":
            raise OperationError("dream_rejected")
        if record.decision_status == "pending":
            record = await story_reader.decide_dream_proposal(scope, update.proposal_id)
        if record.decision_status == "validated":
            commit = await story_reader.commit_dream_proposal(scope, update.proposal_id)
            record = await story_reader.read_dream_proposal(scope, update.proposal_id)
            await offer_role_life_contact(commit)
        return web_response(dream_proposal_view(record))

    @app.post("/api/admin/dream/reject", response_model=DreamProposalView)
    async def reject_dream(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = DreamDecisionRequest.model_validate_json(await body(request))
        scope = story_scope(update.group_id)
        assert story_reader is not None
        record = await story_reader.reject_dream_proposal(scope, update.proposal_id)
        return web_response(dream_proposal_view(record))

    def sticker_metadata_view(value: StickerMetadata | StickerEntry) -> StickerMetadataView:
        return StickerMetadataView(
            description=value.description, usage_hint=value.usage_hint, ocr_text=value.ocr_text,
            intent_tags=list(value.intent_tags), affect_tags=list(value.affect_tags),
        )

    def sticker_metadata_input(value: StickerMetadataView) -> StickerMetadata:
        return StickerMetadata(value.description, value.usage_hint, value.ocr_text,
                               tuple(value.intent_tags), tuple(value.affect_tags))

    @app.get("/api/admin/stickers", response_model=StickerCatalogView)
    async def list_stickers(request: Request, group_id: str) -> JSONResponse:
        require_web_admin(request)
        listing = await sticker_assets.list_entries(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
        )
        return web_response(StickerCatalogView(
            group_id=group_id, revision=listing.revision,
            description_available=sticker_descriptions is not None,
            entries=[StickerEntryView(
                **sticker_metadata_view(entry).model_dump(), sticker_id=entry.sticker_id,
                content_hash=entry.content_hash, mime_type=cast(StickerMediaType, entry.mime_type),
                byte_size=entry.byte_size,
                entry_revision=entry.catalog_revision, status=entry.status,
            ) for entry in listing.entries],
        ))

    @app.get("/api/admin/stickers/{sticker_id}/image")
    async def sticker_image(
        request: Request, sticker_id: str, group_id: str, expected_revision: int,
    ) -> Response:
        require_web_admin(request)
        _, image = await sticker_assets.read_asset(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
            sticker_id=sticker_id, expected_revision=expected_revision,
        )
        return Response(image.data, media_type=image.media_type,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/api/admin/stickers/import", response_model=StickerMutationView)
    async def import_sticker(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = StickerImportRequest.model_validate_json(await body(
            request, max_bytes=((MAX_STICKER_BYTES + 2) // 3) * 4 + 16384,
        ))
        try:
            data = base64.b64decode(update.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise OperationError("invalid_sticker_bytes") from exc
        receipt = await sticker_assets.import_asset(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            operation_id=update.operation_id, expected_revision=update.expected_revision,
            sticker_id=update.sticker_id, data=data, content_type=update.content_type,
            metadata=sticker_metadata_input(update.metadata),
        )
        return web_response(StickerMutationView(**asdict(receipt)))

    @app.post("/api/admin/stickers/metadata", response_model=StickerMutationView)
    async def update_sticker_metadata(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = StickerMetadataRequest.model_validate_json(await body(request))
        receipt = await sticker_assets.update(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            operation_id=update.operation_id, expected_revision=update.expected_revision,
            sticker_id=update.sticker_id, metadata=sticker_metadata_input(update.metadata),
        )
        return web_response(StickerMutationView(**asdict(receipt)))

    @app.post("/api/admin/stickers/describe", response_model=StickerDescriptionView)
    async def describe_sticker(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = StickerTargetRequest.model_validate_json(await body(request))
        runner = sticker_descriptions
        if runner is None:
            raise OperationError("sticker_description_unavailable")
        draft = await runner.describe(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            operation_id=update.operation_id, sticker_id=update.sticker_id,
            expected_revision=update.expected_revision,
            turn=Turn(id=update.operation_id, generation=0, deadline=monotonic() + config.total_timeout),
        )
        return web_response(StickerDescriptionView(
            operation_id=draft.operation_id, sticker_id=draft.entry.sticker_id,
            catalog_revision=draft.catalog_revision, entry_revision=draft.entry.catalog_revision,
            metadata=sticker_metadata_view(draft.metadata),
        ))

    @app.post("/api/admin/stickers/{operation}", response_model=StickerMutationView)
    async def change_sticker(request: Request, operation: Literal["approve", "revoke"]) -> JSONResponse:
        require_web_admin(request, write=True)
        update = StickerTargetRequest.model_validate_json(await body(request))
        mutate = sticker_assets.approve if operation == "approve" else sticker_assets.revoke
        receipt = await mutate(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            operation_id=update.operation_id, expected_revision=update.expected_revision,
            sticker_id=update.sticker_id,
        )
        return web_response(StickerMutationView(**asdict(receipt)))

    async def character_reference_status(
        backup: CharacterReferenceBackupView | None = None,
    ) -> CharacterReferenceStatusView:
        owner = character_reference_owner
        pack = await asyncio.to_thread(owner.read) if owner is not None else None
        runtime_revision = reference_pack.revision if reference_pack is not None else None
        metadata = ""
        if pack is not None:
            value = asdict(pack)
            value.pop("revision")
            metadata = json.dumps({"schema_version": 1, **value}, ensure_ascii=False, indent=2)
        return CharacterReferenceStatusView(
            configured=owner is not None, saved_revision=pack.revision if pack is not None else None,
            runtime_revision=runtime_revision,
            restart_required=bool(pack is not None and pack.revision != runtime_revision),
            metadata_json=metadata, ccip_assembled=bool(ccip_port and reference_pack),
            animetrace_assembled=bool(anime_port and anime_port.available), backup=backup,
        )

    @app.get("/api/admin/characters/reference", response_model=CharacterReferenceStatusView)
    async def read_character_reference(request: Request) -> JSONResponse:
        require_web_admin(request)
        return web_response(await character_reference_status())

    def character_asset_owner(pack_name: str) -> CharacterAssetOwner:
        if character_reference_owner is None:
            raise OperationError("character_reference_unconfigured")
        return CharacterAssetOwner(character_reference_owner.path.parent, pack_name=pack_name)

    def character_asset_view(
        pack_name: str, installed: InstalledCharacterAsset | None,
        receipt: CharacterAssetReceipt | None = None,
    ) -> CharacterPackAssetView:
        return CharacterPackAssetView(
            pack_name=pack_name, revision=installed.revision if installed else None,
            character_ids=list(installed.character_ids) if installed else [],
            request_sha256=installed.request_sha256 if installed else None,
            artifact_sha256=installed.artifact_sha256 if installed else None,
            artifact_model=installed.artifact_model if installed else None,
            artifact_registry_version=installed.artifact_registry_version if installed else None,
            changed=receipt.changed if receipt else False,
            backup_name=receipt.backup_path.name if receipt and receipt.backup_path else None,
        )

    @app.get("/api/admin/characters/packs/{pack_name}", response_model=CharacterPackAssetView)
    async def read_character_asset(request: Request, pack_name: str) -> JSONResponse:
        require_web_admin(request)
        owner = character_asset_owner(pack_name)
        assert character_reference_owner is not None
        reference = await asyncio.to_thread(character_reference_owner.read)
        installed = await asyncio.to_thread(owner.read_for_reference, reference)
        return web_response(character_asset_view(pack_name, installed))

    @app.post("/api/admin/characters/packs/build", response_model=CharacterPackAssetView)
    async def build_character_asset(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = CharacterPackBuildUpdate.model_validate_json(await body(
            request, max_bytes=4 * ((MAX_BATCH_BYTES + 2) // 3) + 4 * 1024 * 1024,
        ))
        owner = character_asset_owner(update.batch.pack_name)
        try:
            images = {key: base64.b64decode(value, validate=True)
                      for key, value in update.images_base64.items()}
        except (binascii.Error, ValueError):
            raise OperationError("invalid_character_public_image") from None
        builder = CharacterActions(actions, pack_client=pack_client, pack_assets=owner,
                                   reference_owner=character_reference_owner)
        receipt = await builder.build_reference_pack(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            request_id=update.operation_id, request=update.batch, images=images,
            expected_revision=update.expected_revision,
        )
        return web_response(character_asset_view(update.batch.pack_name, receipt.installation, receipt))

    @app.post("/api/admin/characters/packs/restore", response_model=CharacterPackAssetView)
    async def restore_character_asset(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = CharacterPackRestoreUpdate.model_validate_json(await body(request))
        owner = character_asset_owner(update.pack_name)
        assert character_reference_owner is not None
        receipt = await drain_on_cancel(asyncio.create_task(asyncio.to_thread(
            owner.restore, owner.directory / update.backup_name,
            reference_owner=character_reference_owner, expected_revision=update.expected_revision,
        )))
        return web_response(character_asset_view(update.pack_name, receipt.installation, receipt))

    async def character_metadata_mutation_result(
        result: CharacterReferenceMutationReceipt,
    ) -> CharacterReferenceMutationView:
        publication = result.publication
        backup = (CharacterReferenceBackupView(
            name=publication.backup_path.name, revision=publication.previous_revision,
        ) if publication.backup_path is not None and publication.previous_revision is not None else None)
        return CharacterReferenceMutationView(
            status=await character_reference_status(backup),
            committed_revision=publication.saved_pack.revision, operation=result.operation,
            changed=result.changed, selected_ids=list(result.selected_ids),
            added_count=result.added_count, existing_count=result.existing_count,
            input_sha256=result.input_sha256, series=result.series,
        )

    @app.post("/api/admin/characters/reference/import", response_model=CharacterReferenceMutationView)
    async def import_character_metadata(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = CharacterReferenceSaveRequest.model_validate_json(await body(request, max_bytes=1024 * 1024))
        owner = character_reference_owner
        if owner is None:
            raise OperationError("character_reference_unconfigured")
        if not update.public_reference_metadata:
            raise OperationError("character_reference_public_confirmation_required")
        result = await drain_on_cancel(asyncio.create_task(asyncio.to_thread(
            owner.import_metadata, update.metadata_json.encode("utf-8"),
            expected_revision=update.expected_revision,
        )))
        return web_response(await character_metadata_mutation_result(result))

    @app.post("/api/admin/characters/reference/merge", response_model=CharacterReferenceMutationView)
    async def merge_character_metadata(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = CharacterReferenceMergeRequest.model_validate_json(await body(request))
        owner = character_reference_owner
        if owner is None:
            raise OperationError("character_reference_unconfigured")
        if not update.public_reference_metadata:
            raise OperationError("character_reference_public_confirmation_required")
        result = await drain_on_cancel(asyncio.create_task(asyncio.to_thread(
            owner.merge_metadata, character_ids=tuple(update.character_ids), series=update.series,
            work=update.work, expected_revision=update.expected_revision,
        )))
        return web_response(await character_metadata_mutation_result(result))

    @app.post("/api/admin/characters/reference/save", response_model=CharacterReferenceStatusView)
    async def save_character_reference(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = CharacterReferenceSaveRequest.model_validate_json(await body(request, max_bytes=1024 * 1024))
        owner = character_reference_owner
        if owner is None:
            raise OperationError("character_reference_unconfigured")
        if not update.public_reference_metadata:
            raise OperationError("character_reference_public_confirmation_required")
        receipt = await drain_on_cancel(asyncio.create_task(asyncio.to_thread(
            owner.save, update.metadata_json.encode("utf-8"), expected_revision=update.expected_revision,
        )))
        backup = (CharacterReferenceBackupView(
            name=receipt.backup_path.name, revision=receipt.previous_revision,
        ) if receipt.backup_path is not None and receipt.previous_revision is not None else None)
        return web_response(await character_reference_status(backup))

    @app.post("/api/admin/characters/reference/restore", response_model=CharacterReferenceStatusView)
    async def restore_character_reference(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = CharacterReferenceRestoreRequest.model_validate_json(await body(request))
        owner = character_reference_owner
        if owner is None:
            raise OperationError("character_reference_unconfigured")
        receipt = await drain_on_cancel(asyncio.create_task(asyncio.to_thread(
            owner.restore, owner.path.parent / update.backup_name, expected_revision=update.expected_revision,
            backup_revision=update.backup_revision,
        )))
        backup = (CharacterReferenceBackupView(
            name=receipt.backup_path.name, revision=receipt.previous_revision,
        ) if receipt.backup_path is not None and receipt.previous_revision is not None else None)
        return web_response(await character_reference_status(backup))

    @app.get("/api/admin/knowledge/sources", response_model=KnowledgeSourcePageView)
    async def list_knowledge_sources(
        request: Request, group_id: str, limit: int = 32, after: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        items, cursor = await knowledge_service.list_sources(
            scope=Scope(bot_id=config.bot_id, group_id=group_id), actor="web-admin",
            limit=limit, after=after,
        )
        return web_response(KnowledgeSourcePageView(
            items=[knowledge_source_view(item) for item in items], next_cursor=cursor,
        ))

    @app.get("/api/admin/knowledge/sources/{source_id}", response_model=KnowledgeSourceView)
    async def get_knowledge_source(request: Request, source_id: str, group_id: str) -> JSONResponse:
        require_web_admin(request)
        source = await knowledge_service.source(
            scope=Scope(bot_id=config.bot_id, group_id=group_id), actor="web-admin", source_id=source_id,
        )
        return web_response(knowledge_source_view(source))

    @app.get("/api/admin/knowledge/sources/{source_id}/document", response_model=KnowledgeDocumentView)
    async def get_knowledge_document(
        request: Request, source_id: str, group_id: str, expected_revision: int,
    ) -> JSONResponse:
        require_web_admin(request)
        source, document = await knowledge_service.read_document(
            scope=Scope(bot_id=config.bot_id, group_id=group_id), actor="web-admin",
            source_id=source_id, expected_revision=expected_revision,
        )
        return web_response(KnowledgeDocumentView(source=knowledge_source_view(source), document=document))

    @app.post("/api/admin/knowledge/import", response_model=KnowledgeSourceView)
    async def import_knowledge(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = KnowledgeImportRequest.model_validate_json(await body(request, max_bytes=2 * 1024 * 1024))
        source = await knowledge_service.reindex(
            update.document, scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            actor="web-admin", expected_revision=update.expected_revision,
        )
        return web_response(knowledge_source_view(source))

    @app.post("/api/admin/knowledge/rebuild", response_model=KnowledgeSourceView)
    async def rebuild_knowledge(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = KnowledgeRemoveRequest.model_validate_json(await body(request))
        source = await knowledge_service.rebuild(
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id), actor="web-admin",
            source_id=update.source_id, expected_revision=update.expected_revision,
        )
        return web_response(knowledge_source_view(source))

    @app.post("/api/admin/knowledge/review", response_model=KnowledgeSourceView)
    async def review_knowledge(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = KnowledgeReviewRequest.model_validate_json(await body(request))
        source = await knowledge_service.review(
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id), actor="web-admin",
            source_id=update.source_id, expected_revision=update.expected_revision,
            decision=update.decision, non_personal_document=update.non_personal_document,
        )
        return web_response(knowledge_source_view(source))

    @app.post("/api/admin/knowledge/activation", response_model=KnowledgeSourceView)
    async def activate_knowledge(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = KnowledgeActivationRequest.model_validate_json(await body(request))
        source = await knowledge_service.set_active(
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id), actor="web-admin",
            source_id=update.source_id, expected_revision=update.expected_revision, active=update.active,
        )
        return web_response(knowledge_source_view(source))

    @app.post("/api/admin/knowledge/remove", response_model=KnowledgeSourceView)
    async def remove_knowledge(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = KnowledgeRemoveRequest.model_validate_json(await body(request))
        source = await knowledge_service.remove(
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id), actor="web-admin",
            source_id=update.source_id, expected_revision=update.expected_revision,
        )
        return web_response(knowledge_source_view(source))

    def graph_object_view(item: GraphRelation | GraphAlias) -> GraphRelationView | GraphAliasView:
        if isinstance(item, GraphRelation):
            return GraphRelationView.model_validate(item, from_attributes=True)
        return GraphAliasView.model_validate(item, from_attributes=True)

    @app.get("/api/admin/knowledge/search", response_model=KnowledgeSearchView)
    async def search_knowledge(request: Request, group_id: str, query: str, limit: int = 5) -> JSONResponse:
        require_web_admin(request)
        hits = await knowledge_service.search(
            query, scope=Scope(bot_id=config.bot_id, group_id=group_id), actor="web-admin", limit=limit,
        )
        return web_response(KnowledgeSearchView(items=[
            KnowledgeChunkView(source=KnowledgeChunkPointer.from_hit(hit), title=hit.title, body=hit.body)
            for hit in hits
        ]))

    @app.get("/api/admin/graph/objects", response_model=GraphObjectPageView)
    async def list_graph_objects(
        request: Request, group_id: str, kind: GraphKind = "relation", limit: int = 32,
        after: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        page = await graph_service.list_objects(
            kind, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
            limit=limit, after=after,
        )
        return web_response(GraphObjectPageView(
            items=[graph_object_view(item) for item in page.items], next_cursor=page.next_cursor,
        ))

    @app.post("/api/admin/graph/extract", response_model=GraphExtractView)
    async def extract_graph_document(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphExtractRequest.model_validate_json(await body(request))
        if not config.graph_extraction_enabled:
            return web_response(GraphExtractView(
                state="disabled", relations=[], suggestions=[], discarded_below_threshold=0,
            ))
        if graph_extraction_runner is None:
            raise HTTPException(503, "stopping")
        result = await graph_extraction_runner.run_once(
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id), actor="web-admin",
            pointer=update.source, run_id=update.run_id, vocabulary=update.vocabulary,
        )
        return web_response(GraphExtractView(
            state=result.state,
            relations=[GraphRelationView.model_validate(row, from_attributes=True)
                       for row in result.relations],
            suggestions=[GraphExtractSuggestionView(
                relation_id=item.relation.relation_id, confidence=item.confidence,
                evidence=item.evidence, evidence_start_char=item.evidence_start_char,
            ) for item in result.suggestions],
            discarded_below_threshold=result.discarded_below_threshold,
        ))

    @app.get("/api/admin/graph/self-source", response_model=GraphSelfFactSourceView)
    async def read_graph_self_source(request: Request, group_id: str, fact_id: str) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        pointer = await memory_service.fact_pointer(fact_id, actor="web-admin", scope=scope)

        def read_source(db: StoreConnection) -> GraphSelfFactSourceView:
            db.execute("BEGIN")
            fact = memory_service.assert_fact_pointer_transaction(
                db, actor="web-admin", scope=scope, pointer=pointer,
            )
            return GraphSelfFactSourceView(source=pointer, predicate=fact.predicate, value=fact.value)

        return web_response(await store.transaction(read_source))

    @app.get("/api/admin/graph/self-relations/{relation_id}", response_model=GraphSelfFactView)
    async def read_graph_self_relation(request: Request, relation_id: str, group_id: str) -> JSONResponse:
        require_web_admin(request)
        item = await graph_service.read_self_fact_relation(
            relation_id, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
        )
        return web_response(GraphSelfFactView.model_validate(item, from_attributes=True))

    @app.post("/api/admin/graph/self-relations", response_model=GraphSelfFactView)
    async def propose_graph_self_relation(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphSelfFactProposeRequest.model_validate_json(await body(request))
        item = await graph_service.propose_self_fact_relation(
            update.value, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(GraphSelfFactView.model_validate(item, from_attributes=True))

    @app.post("/api/admin/graph/self-review", response_model=GraphSelfFactView)
    async def review_graph_self_relation(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphSelfFactReviewRequest.model_validate_json(await body(request))
        item = await graph_service.review_self_fact_relation(
            update.relation_id, actor="web-admin",
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision, decision=update.decision,
            self_non_sensitive_fact=update.self_non_sensitive_fact,
            non_personal_target=update.non_personal_target,
        )
        return web_response(GraphSelfFactView.model_validate(item, from_attributes=True))

    @app.post("/api/admin/graph/self-apply", response_model=GraphSelfFactView)
    async def apply_graph_self_relation(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphSelfFactObjectRequest.model_validate_json(await body(request))
        item = await graph_service.apply_self_fact_relation(
            update.relation_id, actor="web-admin",
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(GraphSelfFactView.model_validate(item, from_attributes=True))

    @app.post("/api/admin/graph/self-revoke", response_model=GraphSelfFactView)
    async def revoke_graph_self_relation(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphSelfFactObjectRequest.model_validate_json(await body(request))
        item = await graph_service.revoke_self_fact_relation(
            update.relation_id, actor="web-admin",
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(GraphSelfFactView.model_validate(item, from_attributes=True))

    @app.post("/api/admin/graph/relations", response_model=GraphRelationView)
    async def propose_graph_relation(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphRelationProposeRequest.model_validate_json(await body(request))
        item = await graph_service.propose_relation(
            update.value, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(graph_object_view(item))

    @app.post("/api/admin/graph/aliases", response_model=GraphAliasView)
    async def propose_graph_alias(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphAliasProposeRequest.model_validate_json(await body(request))
        item = await graph_service.propose_alias(
            update.value, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(graph_object_view(item))

    @app.post("/api/admin/graph/review", response_model=GraphRelationView | GraphAliasView)
    async def review_graph_object(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphReviewRequest.model_validate_json(await body(request))
        item = await graph_service.review(
            update.kind, update.object_id, actor="web-admin",
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision, decision=update.decision,
        )
        return web_response(graph_object_view(item))

    @app.post("/api/admin/graph/apply", response_model=GraphRelationView | GraphAliasView)
    async def apply_graph_object(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphObjectRequest.model_validate_json(await body(request))
        item = await graph_service.apply(
            update.kind, update.object_id, actor="web-admin",
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(graph_object_view(item))

    @app.post("/api/admin/graph/revoke", response_model=GraphRelationView | GraphAliasView)
    async def revoke_graph_object(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = GraphObjectRequest.model_validate_json(await body(request))
        item = await graph_service.revoke(
            update.kind, update.object_id, actor="web-admin",
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(graph_object_view(item))

    @app.post("/api/admin/graph/walk", response_model=GraphProjectionView)
    async def walk_graph(request: Request) -> JSONResponse:
        require_web_admin(request)
        update = GraphWalkRequest.model_validate_json(await body(request))
        projection = await graph_service.walk(
            update.seeds, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            max_depth=update.max_depth, max_nodes=update.max_nodes, max_edges=update.max_edges,
            deadline_ms=update.deadline_ms, limit=update.limit,
        )
        return web_response(GraphProjectionView(
            scope=projection.scope, reader_id=projection.reader_id,
            relations=[
                GraphRelationView.model_validate(item, from_attributes=True) for item in projection.relations
            ],
            aliases=[
                GraphAliasView.model_validate(item, from_attributes=True) for item in projection.aliases
            ],
            paths=[list(path) for path in projection.paths], stop_reason=projection.stop_reason,
            nodes_visited=projection.nodes_visited, edges_examined=projection.edges_examined,
        ))

    def rws_feedback_status() -> RwsFeedbackStatusView:
        if conversation is None:
            raise HTTPException(503, "stopping")
        feedback = conversation.rws_feedback
        return RwsFeedbackStatusView(
            enabled=feedback.enabled,
            bandit_enabled=feedback.enabled and feedback.bandit_enabled,
            ingress_connected=feedback.ingress_connected,
            pending_count=feedback.pending_count,
            last_error=conversation.rws_feedback_error or app.state.rws_feedback_last_error,
            settled_count=app.state.rws_feedback_settled_count,
            rewarded_count=app.state.rws_feedback_rewarded_count,
        )

    @app.get("/api/admin/rws/feedback/status", response_model=RwsFeedbackStatusView)
    async def get_rws_feedback_status(request: Request) -> JSONResponse:
        require_web_admin(request)
        return web_response(rws_feedback_status())

    @app.post("/api/admin/rws/feedback/disable", response_model=RwsFeedbackStatusView)
    async def disable_rws_feedback(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        if conversation is None:
            raise HTTPException(503, "stopping")
        async with policy.dispatch_boundary:
            conversation.rws_feedback.set_enabled(False)
        return web_response(rws_feedback_status())

    def slang_review_status() -> SlangReviewStatusView:
        report: SlangReviewReport | None = getattr(app.state, "slang_review_last_report", None)
        return SlangReviewStatusView(
            enabled=slang_review_runner is not None and slang_review_runner.enabled,
            allowed_groups=list(config.memory_capture_groups),
            last_report=None if report is None else {
                "reviewed": report.reviewed, "deferred": report.deferred, "failed": report.failed,
            },
        )

    @app.get("/api/admin/memory/slang-review/status", response_model=SlangReviewStatusView)
    async def get_slang_review_status(request: Request) -> JSONResponse:
        require_web_admin(request)
        return web_response(slang_review_status())

    @app.post("/api/admin/memory/slang-review/disable", response_model=SlangReviewStatusView)
    async def disable_slang_review(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        if slang_review_runner is not None:
            slang_review_runner.disable()
        return web_response(slang_review_status())

    @app.get("/api/admin/memory/slang-governance", response_model=SlangGovernanceSuggestionPageView)
    async def list_slang_governance(request: Request, group_id: str, limit: int = 50) -> JSONResponse:
        require_web_admin(request)
        items = await domain_learning_service.list_slang_governance_suggestions(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id), limit=limit,
        )
        return web_response(SlangGovernanceSuggestionPageView(items=[
            SlangGovernanceSuggestionView(
                suggestion_id=item["suggestion_id"], revision=item["revision"], code=item["code"],
                details={key: value for key, value in item.items()
                         if key not in {"suggestion_id", "revision", "code"}},
            ) for item in items
        ]))

    @app.post("/api/admin/memory/slang-governance/revoke", response_model=RevisionResponse)
    async def revoke_slang_governance(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = SlangGovernanceRevokeRequest.model_validate_json(await body(request))
        await domain_learning_service.revoke_slang_governance_suggestion(
            update.suggestion_id, actor="web-admin",
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(RevisionResponse(revision=update.expected_revision + 1))

    @app.get("/api/admin/memory/observations", response_model=ObservationPoolPageView)
    async def list_learning_observations(
        request: Request, group_id: str, limit: int = 32, after: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        page = await domain_learning_service.list_observation_pools(
            scope=Scope(bot_id=config.bot_id, group_id=group_id), actor="web-admin", limit=limit, after=after,
        )
        return web_response(ObservationPoolPageView(
            items=[ObservationPoolView.model_validate_json(json.dumps(asdict(item))) for item in page.pools],
            next_cursor=page.next_cursor,
        ))

    @app.get("/api/admin/memory/observation-jobs", response_model=ObservationJobPageView)
    async def list_learning_observation_jobs(
        request: Request, group_id: str, limit: int = 32, after: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        if not 1 <= limit <= 32:
            raise HTTPException(422, "invalid_domain_learning_limit")
        items = await domain_learning_service.list_observation_jobs(
            scope=Scope(bot_id=config.bot_id, group_id=group_id), actor="web-admin",
            limit=limit + 1, after=after,
        )
        return web_response(ObservationJobPageView(
            items=[
                ObservationJobView.model_validate_json(json.dumps(asdict(item))) for item in items[:limit]
            ],
            next_cursor=items[limit - 1].job_id if len(items) > limit else None,
        ))

    @app.post("/api/admin/memory/observation-review", response_model=ObservationJobView)
    async def review_learning_observation(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = ObservationReviewRequest.model_validate_json(await body(request))
        item = await domain_learning_service.settle_observation_review(
            update.job_id, scope=Scope(bot_id=config.bot_id, group_id=update.group_id), actor="web-admin",
            expected_revision=update.expected_revision, verdict=update.verdict,
        )
        return web_response(ObservationJobView.model_validate_json(json.dumps(asdict(item))))

    @app.get("/api/admin/memory/matters", response_model=MemoryMatterPageView)
    async def list_memory_matters(
        request: Request, group_id: str, subject_id: str | None = None,
        state: MatterState | None = None, limit: int = 32, after: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        page = await memory_service.list_matters(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
            subject_id=subject_id, state=state, limit=limit, after=after,
        )
        return web_response(MemoryMatterPageView(
            items=[MemoryMatterView.model_validate(asdict(item)) for item in page.items],
            next_cursor=page.next_cursor,
        ))

    @app.get("/api/admin/memory/matters/{matter_id}", response_model=MemoryMatterView)
    async def read_memory_matter(request: Request, matter_id: str, group_id: str) -> JSONResponse:
        require_web_admin(request)
        item = await memory_service.read_matter(
            matter_id, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
        )
        if item is None:
            raise OperationError("matter_not_found")
        return web_response(MemoryMatterView.model_validate(asdict(item)))

    @app.post("/api/admin/memory/matters/propose", response_model=MemoryMatterView)
    async def propose_memory_matter(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryMatterProposalRequest.model_validate_json(await body(request))
        item = await memory_service.propose_matter(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            **update.model_dump(exclude={"group_id"}),
        )
        return web_response(MemoryMatterView.model_validate(asdict(item)))

    @app.post("/api/admin/memory/matters/replace", response_model=MemoryMatterView)
    async def replace_memory_matter(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryMatterReplaceRequest.model_validate_json(await body(request))
        item = await memory_service.replace_matter(
            update.matter_id, actor="web-admin",
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            **update.model_dump(exclude={"group_id", "matter_id"}),
        )
        return web_response(MemoryMatterView.model_validate(asdict(item)))

    @app.post("/api/admin/memory/matters/review", response_model=MemoryMatterView)
    async def review_memory_matter(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryMatterTransitionRequest.model_validate_json(await body(request))
        item = await memory_service.review_matter(
            update.matter_id, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision, decision=update.decision,
        )
        return web_response(MemoryMatterView.model_validate(asdict(item)))

    @app.post("/api/admin/memory/matters/apply", response_model=MemoryMatterView)
    async def apply_memory_matter(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryMatterTransitionRequest.model_validate_json(await body(request))
        item = await memory_service.apply_matter(
            update.matter_id, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(MemoryMatterView.model_validate(asdict(item)))

    @app.post("/api/admin/memory/matters/complete", response_model=MemoryMatterView)
    async def complete_memory_matter(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryMatterTransitionRequest.model_validate_json(await body(request))
        item = await memory_service.complete_matter(
            update.matter_id, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision,
        )
        return web_response(MemoryMatterView.model_validate(asdict(item)))

    @app.post("/api/admin/memory/matters/cancel", response_model=MemoryMatterView)
    async def cancel_memory_matter(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryMatterTransitionRequest.model_validate_json(await body(request))
        item = await memory_service.cancel_matter(
            update.matter_id, actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_revision=update.expected_revision, reason=update.reason,
        )
        return web_response(MemoryMatterView.model_validate(asdict(item)))

    @app.get("/api/admin/memory/candidates", response_model=MemoryCandidatePageView)
    async def list_memory_candidates(
        request: Request,
        group_id: str,
        limit: int = 32,
        after: str | None = None,
        status: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        selected_status = None if status in {None, "all"} else status
        fact_after, domain_after = _parse_memory_candidate_cursor(
            after, group_id=group_id, status=selected_status
        )
        fact_page = await memory_service.list_candidates_page(
            actor="web-admin",
            scope=scope,
            limit=limit,
            after=fact_after,
            status=selected_status,
        )
        domain_page = (
            await domain_learning_service.list_candidates_page(
                actor="web-admin",
                scope=scope,
                limit=limit,
                after=domain_after,
                status=cast(
                    Literal["pending", "approved", "applied", "rejected", "withdrawn"] | None,
                    selected_status,
                ),
            )
            if selected_status
            in {None, "pending", "approved", "applied", "rejected", "withdrawn"}
            else None
        )
        combined: list[tuple[float, str, MemoryCandidate | DomainLearningCandidate]] = [
            (item.created_at, item.candidate_id, item) for item in fact_page.candidates
        ]
        if domain_page is not None:
            combined.extend(
                (item.created_at, item.candidate_id, item)
                for item in domain_page.candidates
            )
        combined.sort(key=lambda item: (item[0], item[1]), reverse=True)
        visible = combined[:limit]
        visible_facts = [
            item[2] for item in visible if isinstance(item[2], MemoryCandidate)
        ]
        visible_domains = [
            item[2] for item in visible if isinstance(item[2], DomainLearningCandidate)
        ]
        fact_next = (
            visible_facts[-1].candidate_id
            if visible_facts
            else fact_page.next_cursor
            if not fact_page.candidates
            else fact_after
        )
        domain_next = (
            visible_domains[-1].candidate_id
            if visible_domains
            else domain_page.next_cursor
            if domain_page is not None and not domain_page.candidates
            else domain_after
        )
        has_more = (
            len(combined) > limit
            or fact_page.next_cursor is not None
            or (domain_page is not None and domain_page.next_cursor is not None)
        )
        candidate_views: list[MemoryCandidateView | DomainLearningCandidateView] = []
        for _created_at, _candidate_id, item in visible:
            if isinstance(item, MemoryCandidate):
                candidate_views.append(memory_candidate_view(item))
                continue
            social = None
            if (
                item.domain == "episode"
                and item.application_status == "applied"
                and item.applied_object_id is not None
            ):
                social = await social_progress_for_episode(
                    scope, item.applied_object_id
                )
            candidate_views.append(domain_learning_candidate_view(item, social=social))
        return web_response(
            MemoryCandidatePageView(
                items=candidate_views,
                next_cursor=(
                    _memory_candidate_cursor(
                        group_id=group_id,
                        status=selected_status,
                        fact_after=fact_next,
                        domain_after=domain_next,
                    )
                    if has_more
                    else None
                ),
            )
        )

    def auto_apply_status() -> LearningAutoApplyStatusView:
        report: AutoApplyReport | None = getattr(app.state, "learning_auto_apply_last_report", None)
        return LearningAutoApplyStatusView(
            enabled=auto_apply_runner.enabled if auto_apply_runner is not None else False,
            allowed_groups=config.learning_auto_apply_groups,
            allowed_domains=config.learning_auto_apply_domains,
            last_report=(
                LearningAutoApplyReportView(
                    considered=report.considered, applied=report.applied, kept=report.kept,
                    recovered=report.recovered, errors=list(report.errors),
                )
                if report is not None else None
            ),
        )

    @app.get("/api/admin/memory/auto-apply/status", response_model=LearningAutoApplyStatusView)
    async def read_auto_apply_status(request: Request) -> JSONResponse:
        require_web_admin(request)
        return web_response(auto_apply_status())

    @app.post("/api/admin/memory/auto-apply/disable", response_model=LearningAutoApplyStatusView)
    async def disable_auto_apply(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        if auto_apply_runner is not None:
            auto_apply_runner.disable()
        return web_response(auto_apply_status())

    @app.get("/api/admin/memory/auto-apply/receipts", response_model=LearningAutoApplyReceiptPageView)
    async def list_auto_apply_receipts(
        request: Request, group_id: str, limit: int = 32, after: int = 0,
    ) -> JSONResponse:
        require_web_admin(request)
        if auto_apply_runner is None:
            raise OperationError("auto_apply_unavailable")
        page = await auto_apply_runner.list_receipts(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
            limit=limit, after=after,
        )
        return web_response(LearningAutoApplyReceiptPageView(
            items=[LearningAutoApplyReceiptView(audit_id=item.audit_id, **asdict(item.receipt))
                   for item in page.items],
            next_cursor=page.next_cursor,
        ))

    @app.post("/api/admin/memory/auto-apply/rollback", response_model=LearningAutoApplyReceiptView)
    async def rollback_auto_apply(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = LearningAutoApplyRollbackRequest.model_validate_json(await body(request))
        if auto_apply_runner is None:
            raise OperationError("auto_apply_unavailable")
        receipt = await auto_apply_runner.rollback(
            update.receipt_id, actor="web-admin",
            scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            expected_object_revision=update.expected_object_revision, reason=update.reason,
        )
        return web_response(LearningAutoApplyReceiptView(**asdict(receipt)))

    @app.get("/api/admin/memory/domain-failures", response_model=DomainLearningFailurePageView)
    async def list_memory_domain_failures(
        request: Request,
        group_id: str,
        limit: int = 32,
        after: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        page = await domain_learning_service.list_failures_page(
            actor="web-admin", scope=scope, limit=limit, after=after
        )
        return web_response(
            DomainLearningFailurePageView(
                items=[domain_learning_failure_view(item) for item in page.failures],
                next_cursor=page.next_cursor,
            )
        )

    @app.get(
        "/api/admin/memory/extraction-run-diagnostics",
        response_model=ExtractionRunDiagnosticPageView,
    )
    async def list_memory_extraction_run_diagnostics(
        request: Request,
        group_id: str,
        limit: int = 32,
        after: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        page = await domain_learning_service.list_extraction_run_diagnostics_page(
            actor="web-admin", scope=scope, limit=limit, after=after
        )
        return web_response(
            ExtractionRunDiagnosticPageView(
                items=[extraction_run_diagnostic_view(item) for item in page.runs],
                next_cursor=page.next_cursor,
            )
        )

    @app.post("/api/admin/memory/domain-failures/retry", response_model=DomainLearningRetryView)
    async def retry_memory_domain_failure(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = DomainLearningRetryRequest.model_validate_json(await body(request))
        if memory_runner is None:
            raise OperationError("domain_learning_retry_unavailable")
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        result = await memory_runner.retry_slang_failure(
            review_actor="web-admin",
            scope=scope,
            result_id=update.result_id,
            expected_failure_revision=update.failure_revision,
            reason=update.reason,
        )
        return web_response(
            domain_learning_retry_view(result, failure_revision=update.failure_revision)
        )

    @app.get("/api/admin/memory/hot", response_model=MemoryHotPreviewView)
    async def preview_hot_memory(
        request: Request,
        group_id: str,
        subject_id: str,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        result = await memory_service.search_hot_facts(
            actor="web-admin", scope=scope, subject_ids=(subject_id,)
        )
        return web_response(
            MemoryHotPreviewView(
                items=[
                    MemoryHotFactView(
                        fact_id=fact.fact_id,
                        subject_id=fact.subject_id,
                        predicate=fact.predicate,
                        value=fact.value,
                        source_ids=list(fact.source_ids),
                        fact_revision=fact.fact_revision,
                        observed_at=fact.observed_at,
                        valid_from=fact.valid_from,
                        valid_to=fact.valid_to,
                        applied_at=fact.applied_at,
                    )
                    for fact in result.facts
                ],
                truncated=result.truncated,
            )
        )

    @app.get("/api/admin/memory/cards", response_model=MemoryCardQueryView)
    async def query_memory_cards(
        request: Request, group_id: str, query: str = "",
        category: str | None = None, limit: int = 32,
    ) -> JSONResponse:
        require_web_admin(request)
        result = await memory_service.query_cards(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
            query=query, category=category, limit=limit,
        )
        return web_response(MemoryCardQueryView(
            items=[memory_card_metadata_view((card.fact, card.category, card.classification_revision))
                   for card in result.cards],
            total_active=result.total_active, matched_active=result.matched_active,
        ))

    @app.get("/api/admin/memory/cards/{fact_id}", response_model=MemoryCardMetadataView)
    async def read_memory_card_metadata(
        fact_id: str, request: Request, group_id: str,
    ) -> JSONResponse:
        require_web_admin(request)
        metadata = await memory_service.read_card_metadata(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id), fact_id=fact_id,
        )
        if metadata is None:
            raise OperationError("fact_not_found")
        return web_response(memory_card_metadata_view(metadata))

    @app.post("/api/admin/memory/cards/classify", response_model=RevisionResponse)
    async def classify_memory_card(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryCardClassificationRequest.model_validate_json(await body(request))
        revision = await memory_service.classify_card(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            fact_id=update.fact_id, expected_fact_revision=update.expected_fact_revision,
            expected_classification_revision=update.expected_classification_revision,
            category=update.category,
        )
        return web_response(RevisionResponse(revision=revision))

    @app.post("/api/admin/memory/cards/clear", response_model=RevisionResponse)
    async def clear_memory_card(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryCardClearRequest.model_validate_json(await body(request))
        revision = await memory_service.clear_classification(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            fact_id=update.fact_id, expected_fact_revision=update.expected_fact_revision,
            expected_classification_revision=update.expected_classification_revision,
        )
        return web_response(RevisionResponse(revision=revision))

    @app.get("/api/admin/memory/facts", response_model=MemoryFactPageView)
    async def list_memory_facts(
        request: Request,
        group_id: str,
        limit: int = 32,
        after: str | None = None,
        subject_id: str | None = None,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        page = await memory_service.list_active_facts_page(
            actor="web-admin",
            scope=scope,
            subject_id=subject_id,
            limit=limit,
            after=after,
        )
        return web_response(
            MemoryFactPageView(
                items=[memory_fact_view(item) for item in page.facts],
                next_cursor=page.next_cursor,
            )
        )

    @app.get("/api/admin/memory/facts/{fact_id}", response_model=MemoryFactView)
    async def read_memory_fact(
        fact_id: str,
        request: Request,
        group_id: str,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        fact = await memory_service.read_fact(fact_id, actor="web-admin", scope=scope)
        if fact is None:
            raise OperationError("fact_not_found")
        return web_response(memory_fact_view(fact))

    @app.post("/api/admin/memory/correction", response_model=MemoryCandidateView)
    async def propose_memory_correction(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryCorrectionRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        candidate = await memory_service.propose_fact_correction(
            actor="web-admin",
            scope=scope,
            fact_id=update.fact_id,
            expected_fact_revision=update.expected_fact_revision,
            source_id=update.source_id,
            subject_id=update.subject_id,
            predicate=update.predicate,
            value=update.value,
        )
        return web_response(memory_candidate_view(candidate))

    @app.post("/api/admin/memory/resolve-conflict", response_model=MemoryCandidateView)
    async def resolve_memory_conflict(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryResolveConflictRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        candidate = await memory_service.read_candidate(update.candidate_id, actor="web-admin", scope=scope)
        if candidate is None:
            raise OperationError("candidate_not_found")
        resolved = await memory_service.resolve_supersede_conflict(
            update.candidate_id,
            actor="web-admin",
            expected_revision=update.expected_revision,
            expected_target_revision=update.expected_target_revision,
            reason=update.reason,
        )
        return web_response(memory_candidate_view(resolved))

    @app.post("/api/admin/memory/disable", response_model=MemoryFactView)
    async def disable_memory_fact(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryDisableFactRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        fact = await memory_service.disable_fact(
            update.fact_id,
            actor="web-admin",
            scope=scope,
            expected_revision=update.expected_revision,
            reason=update.reason,
        )
        return web_response(memory_fact_view(fact))

    @app.post(
        "/api/admin/memory/retrieval-diagnostic",
        response_model=MemoryRetrievalDiagnosticView,
    )
    async def diagnose_memory_retrieval(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryRetrievalDiagnosticRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        context = await retrieval_service.retrieve_context(
            actor="web-admin",
            scope=scope,
            retrieve_mode="hybrid",
            query=update.query,
            hot_subject_ids=() if update.subject_id is None else (update.subject_id,),
        )
        return web_response(MemoryRetrievalDiagnosticView.model_validate(context.to_dict()))

    @app.get("/api/admin/memory/style", response_model=StyleManagementView)
    async def read_style_management(
        request: Request, group_id: str, after: str | None = None
    ) -> JSONResponse:
        require_web_admin(request)
        snapshot = await domain_learning_service.read_style_management(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id), after=after
        )
        return web_response(StyleManagementView.model_validate_json(json.dumps(asdict(snapshot))))

    @app.post("/api/admin/memory/style/feedback", response_model=StyleManagementView)
    async def style_feedback(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = StyleFeedbackRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        await policy.check("web-admin", scope, "memory.retrieve")
        await domain_learning_service.record_style_feedback(
            actor="web-admin",
            scope=scope,
            object_id=update.object_id,
            expected_revision=update.expected_revision,
            feedback_id=update.feedback_id,
            rating=update.rating,
        )
        return web_response(
            StyleManagementView.model_validate_json(json.dumps(
                asdict(await domain_learning_service.read_style_management(actor="web-admin", scope=scope))
            ))
        )

    @app.post("/api/admin/memory/style/disable", response_model=StyleManagementView)
    async def disable_style(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = StyleObjectRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        await policy.check("web-admin", scope, "memory.retrieve")
        await domain_learning_service.disable_style(
            actor="web-admin",
            scope=scope,
            object_id=update.object_id,
            expected_revision=update.expected_revision,
        )
        return web_response(
            StyleManagementView.model_validate_json(json.dumps(
                asdict(await domain_learning_service.read_style_management(actor="web-admin", scope=scope))
            ))
        )

    @app.post("/api/admin/memory/style/profile", response_model=StyleManagementView)
    async def change_style_profile(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = StyleProfileRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        await policy.check("web-admin", scope, "memory.retrieve")
        await domain_learning_service.change_style_profile(
            actor="web-admin",
            scope=scope,
            expected_revision=update.expected_revision,
            action=update.action,
            profile_id=update.profile_id,
        )
        return web_response(
            StyleManagementView.model_validate_json(json.dumps(
                asdict(await domain_learning_service.read_style_management(actor="web-admin", scope=scope))
            ))
        )

    @app.get("/api/admin/memory/familiarity", response_model=FamiliarityView)
    async def read_familiarity(request: Request, group_id: str, subject_id: str) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        projection = await affection_service.read_current(
            actor="web-admin", scope=scope, subject_id=subject_id,
        )
        return web_response(FamiliarityView(
            scope=scope, subject_id=projection.subject_id, revision=projection.revision,
            score=projection.score, tier=projection.tier,
            valid_contributions=len(projection.contributions),
            enabled_for_chat=config.affection_enabled and group_id in config.affection_groups,
            admin_adjustment=projection.adjustment.requested_score if projection.adjustment else None,
        ))

    @app.post("/api/admin/memory/familiarity/adjust", response_model=FamiliarityView)
    async def adjust_familiarity(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = FamiliarityAdjustmentRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        projection = await affection_service.adjust_current(
            actor="web-admin", scope=scope, subject_id=update.subject_id, score=update.score,
            expected_revision=update.expected_revision, operation_id=update.operation_id,
        )
        return web_response(FamiliarityView(
            scope=scope, subject_id=projection.subject_id, revision=projection.revision,
            score=projection.score, tier=projection.tier,
            valid_contributions=len(projection.contributions),
            enabled_for_chat=config.affection_enabled and update.group_id in config.affection_groups,
            admin_adjustment=projection.adjustment.requested_score if projection.adjustment else None,
        ))

    @app.get("/api/admin/memory/self-alias", response_model=SelfAliasResolution)
    async def read_self_alias(request: Request, group_id: str, surface: str,
                              at: float | None = None) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        return web_response(await memory_service.resolve_self_alias(
            actor="web-admin", scope=scope, surface=surface, at=at,
        ))

    @app.get("/api/admin/memory/episode", response_model=EpisodeManagementView)
    async def read_episode_management(request: Request, group_id: str,
                                      candidate_id: str) -> JSONResponse:
        require_web_admin(request)
        view = await domain_learning_service.read_episode_management(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=group_id),
            candidate_id=candidate_id,
        )
        return web_response(EpisodeManagementView.model_validate(asdict(view)))

    @app.post("/api/admin/memory/episode/decay", response_model=EpisodeManagementView)
    async def set_episode_decay(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = EpisodeDecayRequest.model_validate_json(await body(request))
        view = await domain_learning_service.set_episode_decay(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            candidate_id=update.candidate_id,
            expected_candidate_revision=update.expected_candidate_revision,
            expected_object_revision=update.expected_object_revision,
            decay_at=update.decay_at, reason=update.reason,
        )
        return web_response(EpisodeManagementView.model_validate(asdict(view)))

    @app.post("/api/admin/memory/episode/state", response_model=EpisodeManagementView)
    async def transition_episode_prompt_state(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = EpisodePromptStateRequest.model_validate_json(await body(request))
        view = await domain_learning_service.transition_episode_prompt_state(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            candidate_id=update.candidate_id,
            expected_candidate_revision=update.expected_candidate_revision,
            expected_object_revision=update.expected_object_revision,
            action=update.action, reason=update.reason,
        )
        return web_response(EpisodeManagementView.model_validate(asdict(view)))

    @app.post(
        "/api/admin/memory/review",
        response_model=MemoryCandidateView | DomainLearningCandidateView,
    )
    async def review_memory_candidate(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryReviewRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        if update.kind == "fact":
            if update.decision == "candidate":
                raise OperationError("invalid_memory_decision")
            candidate = await memory_service.read_candidate(
                update.candidate_id, actor="web-admin", scope=scope
            )
            if candidate is None:
                raise OperationError("candidate_not_found")
            reviewed = await memory_service.review(
                update.candidate_id,
                actor="web-admin",
                expected_revision=update.expected_revision,
                decision=update.decision,
            )
            return web_response(memory_candidate_view(reviewed))
        if update.decision == "withdrawn":
            raise OperationError("invalid_domain_learning_decision")
        if not update.reason or not update.reason.strip():
            raise OperationError("invalid_domain_learning_reason")
        reviewed_domain = await domain_learning_service.review(
            update.candidate_id,
            actor="web-admin",
            scope=scope,
            expected_revision=update.expected_revision,
            decision=update.decision,
            reason=update.reason,
            expected_domain=update.kind,
        )
        return web_response(domain_learning_candidate_view(reviewed_domain))

    @app.post(
        "/api/admin/memory/apply",
        response_model=MemoryCandidateView | DomainLearningCandidateView,
    )
    async def apply_memory_candidate(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = MemoryApplyRequest.model_validate_json(await body(request))
        scope = Scope(bot_id=config.bot_id, group_id=update.group_id)
        if update.kind == "fact":
            candidate = await memory_service.read_candidate(
                update.candidate_id, actor="web-admin", scope=scope
            )
            if candidate is None:
                raise OperationError("candidate_not_found")
            applied = await memory_service.apply(
                update.candidate_id,
                actor="web-admin",
                expected_revision=update.expected_revision,
            )
            return web_response(memory_candidate_view(applied))
        applied_domain = await domain_learning_service.apply(
            update.candidate_id,
            actor="web-admin",
            scope=scope,
            expected_revision=update.expected_revision,
            expected_domain=update.kind,
        )
        social = None
        if update.kind == "episode" and applied_domain.application_status == "applied":
            if applied_domain.applied_object_id is None:
                social = SocialProgressView(
                    capture_status="failed",
                    story_status="waiting_for_capture",
                    experience_id=None,
                    error_code="episode_identity_missing",
                    retryable=False,
                )
            else:
                social = await social_progress_for_episode(
                    scope, applied_domain.applied_object_id, attempt=True
                )
        return web_response(domain_learning_candidate_view(applied_domain, social=social))

    @app.post(
        "/api/admin/memory/social/retry",
        response_model=DomainLearningCandidateView,
    )
    async def retry_social_story(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = SocialRetryRequest.model_validate_json(await body(request))
        scope = story_scope(update.group_id)
        if not social_enabled_for(scope):
            raise OperationError("social_evidence_disabled")
        candidate = await domain_learning_service.read_candidate(
            update.candidate_id, actor="web-admin", scope=scope
        )
        if candidate is None:
            raise OperationError("domain_learning_candidate_not_found")
        if (
            candidate.domain != "episode"
            or candidate.application_status != "applied"
            or candidate.applied_object_id is None
        ):
            raise OperationError("candidate_not_applicable")
        social = await social_progress_for_episode(
            scope, candidate.applied_object_id, attempt=True
        )
        return web_response(domain_learning_candidate_view(candidate, social=social))

    @app.get("/api/admin/policy", response_model=PolicySnapshot)
    async def read_policy(request: Request) -> JSONResponse:
        require_web_admin(request)
        revision, grants, visibility = await policy.complete_snapshot()
        return web_response(
            PolicySnapshot.model_validate(
                {
                    "revision": revision,
                    "grants": [g.model_dump(mode="json") for g in grants],
                    "visibility_grants": list(visibility),
                    "cross_group_sharing_enabled": config.cross_group_sharing_enabled,
                    "model_config": config.model_status(),
                    "bot_id": config.bot_id,
                    "thinker_enabled": config.thinker_enabled,
                    "mode": config.mode,
                    "sticker_description_available": sticker_descriptions is not None,
                    "diagnostic_commands_enabled": config.diagnostic_commands_enabled,
                    "diagnostic_commands_groups": config.diagnostic_commands_groups,
                    "private_conversation_enabled": config.private_conversation_enabled,
                    "private_conversation_peers": config.private_conversation_peers,
                    "tool_destinations": [
                        {"tool_id": str(schema["name"]), "destination": spec.destination}
                        for schema in tools.schemas()
                        if (spec := tools.spec(str(schema["name"]))).destination is not None
                    ],
                    "request_destination_tools": [
                        str(schema["name"]) for schema in tools.schemas()
                        if tools.spec(str(schema["name"])).destination_resolver is not None
                    ],
                }
            )
        )

    @app.put("/api/admin/policy", response_model=RevisionResponse)
    async def write_policy(request: Request) -> dict[str, int]:
        require_web_admin(request, write=True)
        update = PolicyWriteRequest.model_validate_json(await body(request))
        revision = await policy.replace(update.grants, update.expected_revision, actor="web-admin")
        return {"revision": revision}

    @app.get("/api/admin/policy/visibility", response_model=VisibilitySnapshot)
    async def read_visibility_policy(request: Request) -> JSONResponse:
        require_web_admin(request)
        revision, grants = await policy.visibility_snapshot()
        return web_response(VisibilitySnapshot(
            revision=revision, cross_group_sharing_enabled=config.cross_group_sharing_enabled,
            visibility_grants=list(grants),
        ))

    @app.get("/api/admin/policy/contact", response_model=ContactConsentView)
    async def read_contact_consents(request: Request) -> JSONResponse:
        require_web_admin(request)
        snapshot = await policy.contact_snapshot()
        return web_response(ContactConsentView(
            revision=snapshot.revision, consents=list(snapshot.consents),
            runtime_settings=config.proactive_contact,
        ))

    @app.put("/api/admin/policy/contact", response_model=ContactConsentResponse)
    async def write_contact_consent(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = ContactConsentRequest.model_validate_json(await body(request))
        revision, consent = await policy.set_contact_consent(
            actor="web-admin", kind=update.kind, subject_id=update.subject_id,
            enabled=update.enabled, expected_revision=update.expected_revision,
        )
        return web_response(ContactConsentResponse(revision=revision, consent=consent))

    @app.get("/api/admin/policy/visibility/options", response_model=VisibilityOptionsView)
    async def read_visibility_options(
        request: Request, group_id: str, material_type: VisibilityMaterial,
    ) -> JSONResponse:
        require_web_admin(request)
        scope = Scope(bot_id=config.bot_id, group_id=group_id)
        options: list[VisibilityOptionView] = []
        if material_type == "knowledge":
            sources, next_cursor = await knowledge_service.list_sources(
                actor="web-admin", scope=scope, limit=64,
            )
            partial = next_cursor is not None
            for source in sources:
                if (source.status == "active" and source.review_status == "approved"
                        and source.reviewed_content_revision == source.content_revision):
                    options.append(VisibilityOptionView(
                        ref=KnowledgeVisibilityRef(
                            source_id=source.source_id, source_revision=source.revision,
                            content_revision=source.content_revision, index_version=source.index_version,
                        ), label=source.title[:128], summary=source.source_label[:256],
                    ))
        else:
            objects = await domain_learning_service.list_applied(
                actor="web-admin", scope=scope, domain=material_type,
            )
            partial = len(objects) > 64
            for item in objects[:64]:
                candidate = await domain_learning_service.read_candidate(
                    item.candidate_id, actor="web-admin", scope=scope,
                )
                if candidate is None:
                    raise OperationError("stale_learning_visibility")
                result = await domain_learning_service.read_result(
                    candidate.result_id, actor="web-admin", scope=scope,
                )
                if result is None:
                    raise OperationError("stale_learning_visibility")
                if isinstance(item.value, SlangValue):
                    label, summary = item.value.term, item.value.meaning
                else:
                    value = cast(StyleValue, item.value)
                    if value.output_policy == "observe_only":
                        continue
                    label, summary = value.situation, value.style
                options.append(VisibilityOptionView(
                    ref=LearningVisibilityRef(
                        material_type=material_type, object_id=item.object_id,
                        object_revision=item.revision, source_id=item.source_id,
                        source_revision=result.source_revision, applied_event_id=item.applied_event_id,
                    ), label=label[:128], summary=summary[:256],
                ))
        return web_response(VisibilityOptionsView(
            source_scope=scope, material_type=material_type, items=options, partial=partial,
        ))

    @app.post("/api/admin/policy/visibility/grant", response_model=VisibilityMutationResponse)
    async def grant_visibility_policy(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = VisibilityGrantRequest.model_validate_json(await body(request))
        source_owner = (knowledge_service if update.material_type == "knowledge"
                        else domain_learning_service)
        revision, grant = await policy.grant_visibility(
            actor="web-admin", grant_id=update.grant_id, source_scope=update.source_scope,
            target_scope=update.target_scope, material_type=update.material_type,
            object_refs=update.object_refs,
            non_personal_projection_confirmed=update.non_personal_projection_confirmed,
            expires_at=update.expires_at, expected_policy_revision=update.expected_policy_revision,
            expected_grant_revision=update.expected_grant_revision,
            source_preflight=source_owner.assert_visibility_grant_transaction,
        )
        return web_response(VisibilityMutationResponse(revision=revision, visibility_grant=grant))

    @app.post("/api/admin/policy/visibility/revoke", response_model=VisibilityMutationResponse)
    async def revoke_visibility_policy(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = VisibilityRevokeRequest.model_validate_json(await body(request))
        revision, grant = await policy.revoke_visibility(
            actor="web-admin", grant_id=update.grant_id,
            expected_policy_revision=update.expected_policy_revision,
            expected_grant_revision=update.expected_grant_revision,
        )
        return web_response(VisibilityMutationResponse(revision=revision, visibility_grant=grant))

    @app.post("/api/admin/offline-chat", response_model=OfflineChatResponse)
    async def offline_chat(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        if config.mode != "offline":
            raise HTTPException(404, "not_found")
        data = OfflineChatRequest.model_validate_json(await body(request))
        result = await accept_payload(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": config.bot_id,
                "group_id": data.group_id,
                "user_id": data.user_id,
                "message_id": secrets.token_hex(16),
                "message": data.text,
            }
        )
        replies = conversation.offline_replies.pop(result["request_id"], []) if conversation else []
        return web_response(OfflineChatResponse.model_validate({**result, "replies": replies}))

    @app.put("/internal/policy", response_model=RevisionResponse)
    async def update_policy(request: Request) -> dict[str, int]:
        require_admin(request)
        update = PolicyWriteRequest.model_validate_json(await body(request))
        revision = await policy.replace(update.grants, update.expected_revision, actor="local-admin")
        return {"revision": revision}

    async def capture_journal_consent(event: Event) -> str | None:
        """Consume exact purpose commands before conversation or generic learning."""
        nonlocal last_error
        if not event.text.startswith("公开日志同意："):
            return None
        if (not isinstance(event.scope, Scope) or event.user_id == config.bot_id
                or event.user_id in config.known_bot_ids):
            return "ignored"
        if (not config.journal_enabled or event.scope.group_id not in config.journal_allowed_groups
                or config.group_mode_for(event.scope.group_id) == "off"):
            last_error = "journal_scope_denied"
            return last_error
        if journal_owner is None or memory_archive is None:
            last_error = "journal_archive_unavailable"
            return last_error
        try:
            public_statement(event.text)
            if (not event.message_id or not event.rich_segments
                    or any(type(segment) is not TextSegment for segment in event.rich_segments)):
                raise OperationError("journal_public_event_invalid")
            now = datetime.now(UTC).timestamp()
            if event.event_time is None or not now - 86400 <= event.event_time <= now:
                raise OperationError("journal_source_expired")
            await policy.check(
                event.user_id, event.scope, "tool.invoke:http.post", "qzone", "journal-text", True
            )
            source = await memory_archive.capture_text(
                event.user_id,
                ArchiveSourceInput(
                    scope=event.scope, event_id=event.event_id, source_kind="human_message",
                    speaker_kind="human", speaker_id=event.user_id,
                    observed_at=float(event.event_time), platform_message_id=event.message_id,
                ),
                event.text,
            )
            await journal_owner.register_public_consent(
                event, source_id=source.source_id, expected_source_revision=source.source_revision,
            )
        except OperationError as exc:
            last_error = exc.code
            return exc.code
        return "journal_consent_registered"

    async def capture_memory_source(event: Event) -> None:
        nonlocal last_error
        if not config.memory_capture_enabled:
            return
        if not isinstance(event.scope, Scope):
            return
        if memory_archive is None or memory_spool is None:
            return
        if event.scope.group_id not in config.memory_capture_groups:
            return
        if config.group_mode_for(event.scope.group_id) == "off":
            return
        if event.user_id == config.bot_id or event.user_id in config.known_bot_ids:
            return
        now = datetime.now(UTC).timestamp()
        if event.event_time is None or not now - 86_400 <= event.event_time <= now + 300:
            return
        nickname = None
        if config.self_nickname_enabled and event.scope.group_id in config.self_nickname_groups:
            try:
                nickname = self_nickname_command(event)
            except OperationError as exc:
                if exc.code != "self_nickname_manual_required":
                    raise
                last_error = exc.code
        if nickname is None and (event.mentioned or event.mention_targets or event.reply_to):
            return
        if not event.rich_segments:
            return
        if nickname is not None:
            # The parser already proved this exact Bot mention and literal
            # self-nickname command. Keep the full evidence text for apply.
            if any(type(segment) not in {AtSegment, TextSegment} for segment in event.rich_segments):
                return
            text = event.text
        else:
            if any(type(segment) is not TextSegment for segment in event.rich_segments):
                return
            text = "".join(segment.text for segment in event.rich_segments if type(segment) is TextSegment)
        if not text.strip():
            return
        try:
            source = await memory_archive.capture_text(
                config.bot_id,
                ArchiveSourceInput(
                    scope=event.scope,
                    event_id=event.event_id,
                    source_kind="human_message",
                    speaker_kind="human",
                    observed_at=float(event.event_time),
                    platform_message_id=event.message_id,
                    speaker_id=event.user_id,
                ),
                text,
            )
            try:
                if nickname is not None:
                    fact_id, fact_revision = await memory_service.prepare_self_nickname_target(
                        event, actor=event.user_id, target_id=event.user_id,
                        source_id=source.source_id, expected_source_revision=source.source_revision,
                    )
                    await memory_service.apply_self_nickname(
                        event, actor=event.user_id, target_id=event.user_id,
                        source_id=source.source_id, expected_source_revision=source.source_revision,
                        expected_fact_id=fact_id, expected_fact_revision=fact_revision,
                    )
            except OperationError as exc:
                last_error = exc.code
            finally:
                memory_wakeup.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not attach event text, paths, or exception details to status/logs.
            last_error = "memory_capture_failed"

    async def capture_research_ingress(payload: object, *, source: Literal["live", "offline"]) -> None:
        if research_events is not None:
            await research_events.observe_wire_inbound(
                payload, source=source, known_bot_ids=config.known_bot_ids,
            )

    @app.get("/api/admin/research/status")
    async def research_status(request: Request) -> JSONResponse:
        require_web_admin(request)
        assert research_events is not None
        return JSONResponse({
            "configured_enabled": config.research_enabled,
            "available": research_events.enabled,
            "storage_error": research_storage_error,
            "cleaner_available": (await asyncio.to_thread(research_spool.cleaner_available)
                                  if research_spool is not None else False),
            "counters": asdict(research_events.counters), "pending": research_events.pending,
        })

    @app.post("/api/admin/research/derive")
    async def research_derive(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        update = ResearchDeriveRequest.model_validate_json(await body(request))
        assert research_events is not None
        return JSONResponse(await research_events.derive(
            actor="web-admin", scope=Scope(bot_id=config.bot_id, group_id=update.group_id),
            version=update.version, parameters=update.parameters,
        ))

    @app.post("/api/admin/history/recent", response_model=RecentHistoryView)
    async def read_recent_history(request: Request) -> JSONResponse:
        require_web_admin(request, write=True)
        if history_owner is None or conversation is None:
            raise OperationError("history_not_enabled")
        update = RecentHistoryRequest.model_validate_json(await body(request))
        snapshot = await history_owner.read(actor="web-admin", request=update)
        restored = await conversation.restore_recent_history(snapshot.events)
        return web_response(RecentHistoryView(
            snapshot_id=snapshot.snapshot_id, scope=snapshot.scope,
            received_count=snapshot.received_count, excluded_count=snapshot.excluded_count,
            restored_count=restored, checkpoint_status=snapshot.checkpoint.status,
            cursor_marker=snapshot.checkpoint.cursor_marker,
        ))

    async def initialize_recent_history(event: Event) -> None:
        if (history_owner is not None and isinstance(event.scope, Scope)
                and event.scope.group_id in history_owner.allowed_groups
                and config.group_mode_for(event.scope.group_id) != "off"):
            # Initialize each group once after a verified current ingress. This
            # also works for reverse WS without blocking its identity handshake.
            await policy.check(event.user_id, event.scope, "message.read")
            async def seed() -> None:
                assert history_owner is not None and conversation is not None
                try:
                    snapshot = await history_owner.read(actor=config.bot_id, request=RecentHistoryRequest(
                        group_id=event.scope.group_id, operation_id="startup_" + secrets.token_hex(12),
                    ))
                    retained = tuple(old for old in snapshot.events if not (
                        old.message_id == event.message_id and old.user_id == event.user_id
                        and old.event_time == event.event_time))
                    await conversation.restore_recent_history(retained)
                except OperationError as exc:
                    app.state.history_last_error = exc.code
            task = history_seed_tasks.get(event.scope.group_id)
            if task is None:
                task = asyncio.create_task(seed())
                history_seed_tasks[event.scope.group_id] = task
            await asyncio.shield(task)

    async def accept_payload(payload: object) -> dict[str, str]:
        if conversation is None:
            raise OperationError("stopping")
        await capture_research_ingress(payload, source="offline")
        if isinstance(payload, dict) and cast(dict[str, object], payload).get("post_type") == "notice":
            notice = parse_group_social_notice(
                cast(dict[str, object], payload), expected_bot_id=config.bot_id,
            )
            if notice is None:
                return {"request_id": "", "state": "ignored"}
            observed = await conversation.observe_notice(notice)
            return {"request_id": notice.event_id, "state": "observed" if observed else "ignored"}
        event = parse_event(cast(object, payload), expected_bot_id=config.bot_id)
        journal_state = await capture_journal_consent(event)
        if journal_state is not None:
            return {"request_id": event.event_id, "state": journal_state}
        await initialize_recent_history(event)
        await capture_memory_source(event)
        result = await conversation.submit(event)
        # Client disconnect does not detach/cancel the supervised session workers.
        state = await asyncio.shield(result)
        return {"request_id": event.event_id, "state": state}

    @app.post("/offline/events")
    async def offline_event(request: Request) -> dict[str, str]:
        require_admin(request)
        if config.mode != "offline":
            raise HTTPException(404, "not_found")
        try:
            payload: object = json.loads(await body(request))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "invalid_input") from exc
        return await accept_payload(payload)

    @app.websocket("/onebot/events")
    async def onebot_events(socket: WebSocket) -> None:
        nonlocal websocket_connected, last_heartbeat, last_error, feedback_ingress_eligible
        # Prevent a browser from using platform credentials as a cross-origin websocket session.
        if (
            socket.headers.get("origin") is not None
            or not bearer(
                socket.headers.get("authorization"),
                credentials.ingress,
            )
            or websocket_connected
            or (reverse_ws and socket.headers.get("x-self-id") != config.bot_id)
        ):
            await socket.close(code=1008)
            return
        websocket_connected = True
        feedback_ingress_eligible = True
        notifications: set[asyncio.Task[None]] = set()
        generation: int | None = None
        probe: asyncio.Task[None] | None = None

        async def verify_peer() -> None:
            nonlocal last_error
            if reverse_sender is None:
                return
            try:
                await reverse_sender.verify_identity()
                online = await reverse_sender.probe_online()
                if not online:
                    await actions.hold_qq_transport("qq_offline")
                if actions.qq_delivery is not None:
                    actions.qq_delivery.confirm_ownership(config.bot_id)
            except (OperationError, TimeoutError, RuntimeError, WebSocketDisconnect) as exc:
                last_error = exc.code if isinstance(exc, OperationError) else "onebot_not_ready"
                await socket.close(code=1008)

        async def observe(result: asyncio.Future[str]) -> None:
            # OneBot event streams have no application acknowledgements. Sending a
            # custom state frame would be interpreted as an API command by NapCat.
            await asyncio.shield(result)

        try:
            await socket.accept()
            if reverse_sender is not None:
                generation = reverse_sender.attach(socket.send_json)
                probe = asyncio.create_task(verify_peer())
            while True:
                raw = await socket.receive_text()
                if len(raw.encode()) > 65536:
                    await socket.close(code=1009)
                    return
                payload: object = json.loads(raw)
                if isinstance(payload, dict):
                    event = cast(dict[str, JsonValue], payload)
                    if reverse_sender is not None and "post_type" not in event and "echo" in event:
                        reverse_sender.receive(event)
                        # Keep reading while startup verifies identity and online
                        # status. The delivery owner remains closed to writes until
                        # both read-only responses have been consumed.
                        continue
                    if str(event.get("self_id")) != config.bot_id:
                        raise OperationError("wrong_bot")
                    if "is_admin" in event or "capabilities" in event:
                        raise OperationError("forged_privilege")
                    sender_info = event.get("sender")
                    if isinstance(sender_info, dict) and (
                        "is_admin" in sender_info or "capabilities" in sender_info
                    ):
                        raise OperationError("forged_privilege")
                    if event.get("post_type") == "meta_event":
                        if event.get("meta_event_type") == "heartbeat":
                            last_heartbeat = monotonic()
                            bridge_status = event.get("status")
                            if (actions.qq_delivery is not None and isinstance(bridge_status, dict)
                                    and bridge_status.get("online") is False):
                                await actions.hold_qq_transport("qq_offline")
                            if conversation is not None and feedback_ingress_eligible:
                                ready = reverse_sender is None or reverse_sender.ready
                                conversation.rws_feedback.connect_ingress(ready)
                                conversation.rws_feedback.connect_notice_ingress(ready)
                        continue
                if conversation is None:
                    raise OperationError("stopping")
                try:
                    if reverse_sender is not None and not reverse_sender.ready:
                        raise OperationError("onebot_not_ready")
                    await capture_research_ingress(cast(object, payload), source="live")
                    if (isinstance(payload, dict)
                            and cast(dict[str, object], payload).get("post_type") == "notice"):
                        notice = parse_group_social_notice(
                            cast(dict[str, object], payload), expected_bot_id=config.bot_id,
                        )
                        if notice is not None:
                            await conversation.observe_notice(notice)
                        continue
                    incoming = parse_event(cast(object, payload), expected_bot_id=config.bot_id)
                    if await capture_journal_consent(incoming) is not None:
                        continue
                    if len(notifications) >= config.queue_capacity + config.max_active_sessions:
                        raise OperationError("busy")
                    await initialize_recent_history(incoming)
                    await capture_memory_source(incoming)
                    result = await conversation.submit(incoming)
                except OperationError as exc:
                    if exc.code not in {
                        "unsupported_event",
                        "unsupported_message",
                        "unsupported_scope",
                        "denied",
                        "offline",
                        "revoked",
                        "duplicate",
                        "busy",
                        "onebot_not_ready",
                    }:
                        raise
                    # An unsupported or denied message is not a transport failure.
                    last_error = exc.code
                    if (isinstance(payload, dict)
                            and cast(dict[str, JsonValue], payload).get("post_type") == "message"
                            and cast(dict[str, JsonValue], payload).get("message_type") != "private"):
                        feedback_ingress_eligible = False
                        conversation.rws_feedback.connect_ingress(False)
                        conversation.rws_feedback.connect_notice_ingress(False)
                        app.state.rws_feedback_last_error = "ingress_message_gap"
                    continue
                notification = asyncio.create_task(observe(result))
                notifications.add(notification)
                notification.add_done_callback(notifications.discard)
        except WebSocketDisconnect:
            pass
        except (ValueError, ValidationError, OperationError) as exc:
            last_error = exc.code if isinstance(exc, OperationError) else "invalid_input"
            await socket.close(code=1008)
        finally:
            # Release ownership synchronously: cancellation during cleanup must
            # not leave every subsequent reconnect permanently rejected.
            if probe is not None:
                probe.cancel()
            for notification in notifications:
                notification.cancel()
            if reverse_sender is not None and generation is not None:
                reverse_sender.detach(generation)
                if actions.qq_delivery is not None and started:
                    actions.qq_delivery.hold_local("onebot_disconnected")
            websocket_connected = False
            feedback_ingress_eligible = False
            if conversation is not None:
                conversation.rws_feedback.connect_ingress(False)
                conversation.rws_feedback.connect_notice_ingress(False)
            last_heartbeat = 0.0
            await asyncio.gather(
                *notifications, *([probe] if probe is not None else []), return_exceptions=True
            )
            if (reverse_sender is not None and generation is not None
                    and actions.qq_delivery is not None and started):
                await actions.hold_qq_transport("onebot_disconnected")

    return app


def run_core(
    config: Config,
    credentials: Credentials,
    *,
    port: int,
    dev_web_bypass: bool = False,
    reverse_ws: bool = False,
    napcat_container: NapCatContainer | None = None,
) -> None:
    """Rebuild one core at a time on its original address; no hot reload or daemon."""
    config = resolve_saved(Config.model_validate(config.model_dump()))
    revision: int | None = None
    fallback: tuple[Config, Credentials, int] | None = None
    failure = ""
    startup_receipt: tuple[SettingsPersonaReceipt, Literal["applied", "rejected"]] | None = None
    while True:
        control = CoreRestart(
            stop=lambda: None,
            prepare=lambda selected: load_task_credentials(
                Path(selected.db_path).resolve().parent, selected, reverse_ws=reverse_ws
            ),
            last_error=failure,
            startup_receipt=startup_receipt,
        )
        app = create_app(
            config,
            credentials,
            dev_web_bypass=dev_web_bypass,
            reverse_ws=reverse_ws,
            napcat_container=napcat_container,
            core_restart=control,
            pinned_revision=revision,
        )
        class CoreServer(uvicorn.Server):
            def __init__(self, server_config: uvicorn.Config, control: CoreRestart) -> None:
                super().__init__(server_config)
                self.control = control

            async def startup(self, sockets: list[socket.socket] | None = None) -> None:
                await super().startup(sockets=sockets)
                if self.started:
                    assert self.control.ready is not None
                    try:
                        await self.control.ready()
                    except OperationError:
                        await self.shutdown(sockets=sockets)
                        self.started = False
                        raise SystemExit(3) from None

        server = CoreServer(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                workers=1,
                access_log=False,
                ws_max_size=65536,
                timeout_graceful_shutdown=10,
            ), control,
        )
        control.stop = partial(setattr, server, "should_exit", True)
        try:
            server.run()
        except SystemExit as exc:
            # Uvicorn exits with 3 on lifespan/bind startup failure. Do not
            # swallow user termination, normal exits, or a failed fallback.
            if exc.code != 3 or server.started or fallback is None:
                raise
        if not server.started and fallback is not None:
            assert control.startup_receipt is not None
            startup_receipt = control.startup_receipt[0], "rejected"
            config, credentials, revision = fallback
            fallback = None
            failure = "runtime_start_failed"
            continue
        if control.candidate is None:
            return
        assert control.previous_revision is not None
        assert control.requested_receipt is not None
        fallback = config, credentials, control.previous_revision
        config, credentials, revision = control.candidate
        startup_receipt = control.requested_receipt, "applied"
        failure = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated Omubot rewrite on loopback")
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--live", action="store_true", help="explicit live launch; requires grants and secrets"
    )
    parser.add_argument(
        "--dev-web-bypass",
        action="store_true",
        help="skip web login on loopback in offline development",
    )
    parser.add_argument(
        "--reverse-ws", action="store_true", help="use authenticated reverse OneBot WS for API calls"
    )
    parser.add_argument("--port", type=int)
    parser.add_argument(
        "--napcat-container",
        type=Path,
        help="private descriptor of one pre-provisioned development NapCat container",
    )
    parser.add_argument("--instance", type=Path, help="independent runtime directory")
    parser.add_argument("--init-instance", type=Path, help="create a new offline instance; never overwrite")
    parser.add_argument("--name", default="default", help="name of a new instance")
    parser.add_argument("--bot-id", default="10001", help="platform bot ID for a new instance")
    parser.add_argument("--check-config", action="store_true", help="validate and show safe selected profile")
    args = parser.parse_args()
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.init_instance and (args.instance or args.config or args.live or args.check_config):
        parser.error("initialization cannot be combined with launch/configuration options")
    if args.instance and args.config:
        parser.error("choose either --instance or --config")
    try:
        if args.init_instance:
            root = initialize_instance(args.init_instance, args.name, args.port or 18081, args.bot_id)
            print(json.dumps({"created": str(root), "mode": "offline"}))
            return
        config = (
            load_instance(args.instance, live=args.live)
            if args.instance
            else load_config(args.config, live=args.live)
        )
        config = resolve_saved(config)
    except OperationError as exc:
        parser.error(f"cannot load saved configuration: {exc.code}")
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(part) for part in error["loc"]) or "config"
            for error in exc.errors(include_input=False, include_context=False)
        )
        parser.error(f"invalid configuration fields: {fields}; see config.example.toml")
    except (ValueError, OSError):
        parser.error("cannot read valid configuration; check path and TOML syntax")
    if args.check_config:
        print(json.dumps(config.model_status(), ensure_ascii=True))
        return
    if args.dev_web_bypass and args.live:
        parser.error("--dev-web-bypass cannot be combined with --live")
    credentials = load_task_credentials(
        Path(config.db_path).resolve().parent, config, reverse_ws=args.reverse_ws
    )
    run_core(
        config,
        credentials,
        port=args.port or config.listen_port,
        dev_web_bypass=args.dev_web_bypass,
        reverse_ws=args.reverse_ws,
        napcat_container=NapCatContainer.load(args.napcat_container) if args.napcat_container else None,
    )


if __name__ == "__main__":
    main()
