"""Pydantic DTOs for the small, explicit HTTP management surface."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue, field_validator

from .block_trace import PromptBlockTrace
from .character_pack_client import MAX_BATCH_IMAGES, CharacterPackBuildRequest
from .config import (
    MAX_MODEL_SYSTEM_CHARS,
    MAX_PERSONA_SOURCE_CHARS,
    ClimateMode,
    ContactSettings,
    GroupCalendarEvent,
    GroupMode,
    GroupProfileOverride,
    PersonaMode,
    RwsMode,
    TaskName,
    validate_group_calendar_events,
    validate_group_mode_ids,
    validate_group_profile_ids,
    validate_journal_uins,
    validate_memory_group_ids,
    validate_worldbook_group_ids,
)
from .context_observation import ContextObservationSnapshot
from .element_rules import ElementRuleConfig
from .graph import AliasInput, GraphHealthSnapshot, RelationInput, SelfFactRelationInput, SelfFactSource
from .graph_extractor import GraphVocabulary
from .knowledge import KnowledgeChunkPointer, MarkdownSourceInput
from .memory import MemoryFactPointer, SelfAliasResolution
from .policy import ContactConsent
from .pricing import ModelPrice
from .recent_history import RecentHistoryRequest, RecentHistoryView
from .types import (
    ConversationScope,
    Grant,
    ModelUsage,
    QQDeliveryLimits,
    QQDeliverySnapshot,
    QQScopeKey,
    Scope,
    StrictModel,
    VisibilityGrant,
    VisibilityMaterial,
    VisibilityObjectRef,
)
from .visual_transport import MAX_IMAGE_BYTES


class GroupBoardTimelineView(StrictModel):
    role: str
    author_id: str
    event_id: str | None
    message_id: str
    source_ids: list[str]
    sources: list[list[str]]
    mention_targets: list[str]
    topic_id: str | None
    topic_parent_event_id: str | None
    topic_edge_kind: str
    age_s: float
    remaining_ttl_s: float


class GroupBoardMentionView(StrictModel):
    author_id: str
    count: int
    latest_age_s: float


class GroupBoardFrequencyView(StrictModel):
    count: int
    label: Literal["暂无消息", "冷清", "正常", "活跃"]
    window_seconds: float


class GroupBoardView(StrictModel):
    bot_id: str
    group_id: str
    policy_revision: int
    retention_seconds: float
    retained_count: int
    window_truncated: bool
    available_span_s: float
    expires_in_s: float | None
    active_users: list[str]
    recent_topics: list[str]
    message_frequency: GroupBoardFrequencyView
    recent_mentions: list[GroupBoardMentionView]
    timeline: list[GroupBoardTimelineView]


class JournalCreateRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    source_event_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=280)
    operation_id: str = Field(min_length=1, max_length=128)


class JournalDecisionRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    draft_id: str = Field(min_length=1, max_length=128)
    expected_body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str = Field(min_length=1, max_length=128)


class JournalApprovalRequest(JournalDecisionRequest):
    approval_scope: Literal["dry_run", "live"] = "dry_run"


class JournalRevisionRequest(JournalDecisionRequest):
    body: str = Field(min_length=1, max_length=280)


class JournalDryRunRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    draft_id: str = Field(min_length=1, max_length=128)
    expected_body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class JournalReviewView(StrictModel):
    actor: str
    decision: Literal["approve", "reject"]
    body_hash: str
    source_hash: str
    content_hash: str
    approval_scope: Literal["dry_run", "live"]
    created_at: float


class JournalHeadView(StrictModel):
    draft_id: str
    root_id: str
    revision: int
    source_event_id: str
    source_hash: str
    body_hash: str
    content_hash: str
    state: Literal["pending_review", "approved", "rejected"]
    created_at: float


class JournalHeadPageView(StrictModel):
    items: list[JournalHeadView]
    has_more: bool
    next_cursor: str | None


class JournalDraftView(JournalHeadView):
    group_id: str
    body: str
    supersedes_draft_id: str | None
    is_tip: bool
    review: JournalReviewView | None
    content_kind: Literal["fiction", "factual"]


class JournalStatusView(StrictModel):
    enabled: bool
    fiction_available: bool
    archive_available: bool
    storage_error: str
    allow_live_publish: bool
    live_available: bool
    wire_validated: bool
    external_transport: bool
    validation_profile: str | None
    validated_account_digest: str | None
    recovered_deliveries: int


class JournalPublicConsentView(StrictModel):
    source_id: str
    template_id: str
    labels: tuple[str, ...]
    label_index: int
    expires_at: float
    current: bool
    code: str


class JournalPublicConsentPageView(StrictModel):
    items: list[JournalPublicConsentView]
    has_more: bool
    next_cursor: str | None


class JournalFictionPreviewRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    operation_id: str = Field(min_length=1, max_length=80)


class JournalFactualPreviewRequest(JournalFictionPreviewRequest):
    consent_source_ids: tuple[str, ...] = Field(min_length=1, max_length=2)


class JournalSelectionView(StrictModel):
    source_event_id: str
    reason: str
    score: float | None


class JournalFictionPreviewView(StrictModel):
    draft: JournalDraftView | None
    decisions: list[JournalSelectionView]


class JournalPublishRequest(JournalDryRunRequest):
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str = Field(min_length=1, max_length=128)
    mode: Literal["dry_run", "live"] = "dry_run"


class JournalDeliveryView(StrictModel):
    delivery_id: str
    draft_id: str
    mode: Literal["dry_run", "live"]
    state: Literal["dispatching", "unknown", "published", "failed"]
    payload_hash: str
    receipt: str
    code: str
    wire_validated: bool
    external_transport: bool


class JournalDeliveryPageView(StrictModel):
    items: list[JournalDeliveryView]
    has_more: bool
    next_cursor: str | None


class JournalResolveRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    delivery_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["published", "failed"]
    receipt: str = Field(default="", max_length=128)


class JournalDryRunView(StrictModel):
    draft_id: str
    revision: int
    body: str
    body_hash: str
    source_hash: str
    content_hash: str
    scope_digest: str
    mode: Literal["dry_run"]


class StoryArcCreateRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    arc_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    stage: str = Field(default="active", min_length=1, max_length=64)


class StoryArcView(StrictModel):
    arc_id: str
    role: str
    title: str
    stage: str
    status: str
    revision: int
    group_ids: list[str]


class StoryArcListView(StrictModel):
    items: list[StoryArcView]


class DreamProposeRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    arc_id: str = Field(min_length=1, max_length=128)


class DreamDecisionRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    proposal_id: str = Field(min_length=1, max_length=128)


class DreamProposalView(StrictModel):
    proposal_id: str
    group_id: str
    target_arc_id: str
    target_arc_revision: int
    created_at: float
    kind: str
    summary: str
    payload: dict[str, JsonValue]
    decision_status: Literal["pending", "validated", "rejected"]
    reason: str | None
    committed_event_id: str | None
    committed_at: float | None


class DreamProposalPageView(StrictModel):
    items: list[DreamProposalView]
    next_cursor: str | None


class MemoryCandidateView(StrictModel):
    kind: Literal["fact"]
    candidate_id: str
    subject_id: str
    predicate: str
    value: str
    action: Literal["add", "reinforce", "supersede", "skip"]
    target_fact_id: str | None
    source_ids: list[str]
    candidate_revision: int
    status: str
    conflict_set_id: str | None
    skip_reason: str | None
    suggestion_reason: Literal[
        "stable_preference",
        "time_bounded_plan",
        "communication_boundary",
        "explicit_correction",
    ] | None
    observed_at: float | None
    valid_from: float | None
    valid_to: float | None
    created_at: float


class SocialProgressView(StrictModel):
    capture_status: Literal["disabled", "pending", "captured", "invalidated", "failed"]
    story_status: Literal["disabled", "waiting_for_capture", "pending", "committed"]
    experience_id: str | None
    error_code: str | None = Field(default=None, max_length=128)
    retryable: bool


class StyleValueView(StrictModel):
    situation: str
    style: str
    output_policy: Literal["allow_use", "transform", "observe_only"]
    risk_tags: list[str]


class StyleItemView(StrictModel):
    object_id: str
    revision: int
    source_id: str
    source_revision: int
    subject_id: str
    value: StyleValueView
    status: str


class StyleProfileView(StrictModel):
    profile_id: str
    version: int
    revision: int
    status: str
    content: str
    items: list[StyleItemView]


class StyleFeedbackView(StrictModel):
    feedback_id: str
    object_id: str
    object_revision: int
    rating: Literal["positive", "negative", "neutral"]
    actor: str


class StyleManagementView(StrictModel):
    revision: int
    items: list[StyleItemView]
    next_cursor: str | None
    profiles: list[StyleProfileView]
    feedback: list[StyleFeedbackView]


class StyleObjectRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    object_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)


class StyleFeedbackRequest(StyleObjectRequest):
    feedback_id: str = Field(min_length=1, max_length=128)
    rating: Literal["positive", "negative", "neutral"]


class StyleProfileRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=0)
    action: Literal["generate", "enable", "disable", "rollback"]
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)


class DomainLearningCandidateView(StrictModel):
    kind: Literal["slang", "style", "episode"]
    candidate_id: str
    candidate_revision: int
    status: Literal["pending", "approved", "rejected", "withdrawn"]
    application_status: Literal["not_applied", "applied", "disabled"]
    episode_state: Literal[
        "dry_run", "candidate", "approved", "enabled_for_prompt", "disabled"
    ] | None
    source_ids: list[str]
    created_at: float
    value: JsonValue
    social: SocialProgressView | None = None


class FamiliarityView(StrictModel):
    scope: Scope
    subject_id: str
    revision: int
    score: float
    tier: str
    valid_contributions: int
    enabled_for_chat: bool
    admin_adjustment: float | None = None


class FamiliarityAdjustmentRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=128)
    score: float = Field(ge=0, le=100, allow_inf_nan=False)
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")


class EpisodeManagementView(StrictModel):
    candidate_id: str
    object_id: str
    candidate_revision: int
    object_revision: int
    state: Literal["dry_run", "candidate", "approved", "enabled_for_prompt", "disabled"]
    decay_at: str
    last_used_at: float | None
    prompt_eligible: bool


class EpisodeDecayRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    candidate_id: str = Field(min_length=1, max_length=128)
    expected_candidate_revision: int = Field(ge=1)
    expected_object_revision: int = Field(ge=1)
    decay_at: str = Field(max_length=128)
    reason: str = Field(min_length=1, max_length=256)


class EpisodePromptStateRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    candidate_id: str = Field(min_length=1, max_length=128)
    expected_candidate_revision: int = Field(ge=1)
    expected_object_revision: int = Field(ge=1)
    action: Literal["disable", "approve_reopen", "enable"]
    reason: str = Field(min_length=1, max_length=256)



class DomainLearningFailureView(StrictModel):
    result_id: str
    source_id: str
    domain: Literal["fact", "slang", "style", "episode"]
    extractor_version: str
    source_revision: int = Field(ge=1)
    error_code: Literal[
        "slang_key_collision", "slang_stoplisted", "incomplete_after_deadline"
    ]
    failure_revision: int = Field(ge=1)
    created_at: float
    status: Literal["failed", "incomplete"]


class ExtractionRunDiagnosticView(StrictModel):
    run_id: str
    source_id: str
    source_revision: int = Field(ge=1)
    status: Literal["cancelled", "unknown", "abandoned"]
    stage: Literal["extracting", "fact", "slang", "style", "episode"]
    error_code: str = Field(max_length=64)
    started_at: float
    updated_at: float
    finished_at: float | None
    missing_domains: list[Literal["fact", "slang", "style", "episode"]]


class ExtractionRunDiagnosticPageView(StrictModel):
    items: list[ExtractionRunDiagnosticView]
    next_cursor: str | None


class DomainLearningFailurePageView(StrictModel):
    items: list[DomainLearningFailureView]
    next_cursor: str | None


class DomainLearningRetryRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    result_id: str = Field(min_length=1, max_length=128)
    failure_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=256)


class DomainLearningRetryView(StrictModel):
    result_id: str
    domain: Literal["slang"]
    status: Literal["failed", "candidates"]
    error_code: Literal["slang_key_collision", "slang_stoplisted"] | None
    failure_revision: int = Field(ge=1)
    candidate_count: int = Field(ge=0)


class MemoryCandidatePageView(StrictModel):
    items: list[MemoryCandidateView | DomainLearningCandidateView]
    next_cursor: str | None


class MemoryHotFactView(StrictModel):
    fact_id: str
    subject_id: str
    predicate: str
    value: str
    source_ids: list[str]
    fact_revision: int
    observed_at: float | None
    valid_from: float | None
    valid_to: float | None
    applied_at: float | None


class MemoryHotPreviewView(StrictModel):
    items: list[MemoryHotFactView]
    truncated: bool


class MemoryFactView(StrictModel):
    fact_id: str
    subject_id: str
    predicate: str
    value: str
    source_ids: list[str]
    fact_revision: int
    status: Literal["active", "disabled"]
    observed_at: float | None
    valid_from: float | None
    valid_to: float | None
    applied_at: float | None


class MemoryFactPageView(StrictModel):
    items: list[MemoryFactView]
    next_cursor: str | None


class MemoryMatterView(StrictModel):
    matter_id: str
    scope: Scope
    subject_id: str
    source_id: str
    source_revision: int
    summary: str
    condition: str | None
    state: Literal["candidate", "approved", "active", "completed", "cancelled", "expired"]
    revision: int
    observed_at: float | None
    due_at: float | None
    expires_at: float
    replaces_matter_id: str
    reason: str
    actor: str
    created_at: float
    updated_at: float
    purpose: Literal["inbound_reply"] = "inbound_reply"


class MemoryMatterPageView(StrictModel):
    items: list[MemoryMatterView]
    next_cursor: str | None


class MemoryMatterProposalRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    operation_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    expected_source_revision: int = Field(ge=1)
    subject_id: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=256)
    condition: str | None = Field(default=None, max_length=256)
    observed_at: float | None = None
    due_at: float | None = None
    expires_at: float


class MemoryMatterReplaceRequest(MemoryMatterProposalRequest):
    matter_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)


class MemoryMatterTransitionRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    matter_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    decision: Literal["approved", "rejected"] = "approved"
    reason: str = Field(default="cancelled", min_length=1, max_length=256)


MemoryCardCategory = Literal["preference", "boundary", "relationship", "event", "promise", "fact", "status"]


class MemoryCardMetadataView(StrictModel):
    fact: MemoryFactView
    category: MemoryCardCategory | None
    classification_revision: int = Field(ge=0)


class MemoryCardQueryView(StrictModel):
    items: list[MemoryCardMetadataView]
    total_active: int = Field(ge=0)
    matched_active: int = Field(ge=0)


class MemoryCardClearRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    fact_id: str = Field(min_length=1, max_length=128)
    expected_fact_revision: int = Field(ge=1)
    expected_classification_revision: int = Field(ge=0)


class MemoryCardClassificationRequest(MemoryCardClearRequest):
    category: MemoryCardCategory


class MemoryReviewRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    kind: Literal["fact", "slang", "style", "episode"] = "fact"
    decision: Literal["candidate", "approved", "rejected", "withdrawn"]
    reason: str | None = Field(default=None, max_length=256)


class MemoryApplyRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    kind: Literal["fact", "slang", "style", "episode"] = "fact"


class SocialRetryRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)


class MemoryCorrectionRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    fact_id: str = Field(min_length=1, max_length=128)
    expected_fact_revision: int = Field(ge=1)
    source_id: str = Field(min_length=1, max_length=128)
    subject_id: str = Field(min_length=1, max_length=64)
    predicate: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.:-]{0,63}$")
    value: str = Field(min_length=1, max_length=256)


class SlangGovernanceSuggestionView(StrictModel):
    suggestion_id: int
    revision: int
    code: Literal["real_drift_pending", "muting_suggestion"]
    details: dict[str, JsonValue]


class SlangGovernanceSuggestionPageView(StrictModel):
    items: list[SlangGovernanceSuggestionView]


class SlangGovernanceRevokeRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=128)
    suggestion_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)


class GraphRelationView(StrictModel):
    kind: Literal["relation"] = "relation"
    scope: Scope
    relation_id: str
    revision: int = Field(ge=1)
    subject_id: str
    predicate: str
    target_id: str
    source: KnowledgeChunkPointer
    review_status: Literal["pending", "approved", "rejected"]
    status: Literal["inactive", "active", "ambiguous", "revoked"]


class GraphAliasView(StrictModel):
    kind: Literal["alias"] = "alias"
    scope: Scope
    alias_id: str
    revision: int = Field(ge=1)
    entity_id: str
    alias: str
    source: KnowledgeChunkPointer
    review_status: Literal["pending", "approved", "rejected"]
    status: Literal["inactive", "active", "ambiguous", "revoked"]


class GraphObjectPageView(StrictModel):
    items: list[GraphRelationView | GraphAliasView]
    next_cursor: str | None


class GraphRelationProposeRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(default=0, ge=0)
    value: RelationInput


class GraphSelfFactSourceView(StrictModel):
    source: MemoryFactPointer
    predicate: str
    value: str


class GraphSelfFactView(StrictModel):
    kind: Literal["self_fact_relation"] = "self_fact_relation"
    scope: Scope
    relation_id: str
    revision: int = Field(ge=1)
    subject_id: str
    predicate: str
    target_id: str
    source: SelfFactSource
    review_status: Literal["pending", "approved", "rejected"]
    status: Literal["inactive", "active", "ambiguous", "revoked"]


class GraphSelfFactProposeRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(default=0, ge=0)
    value: SelfFactRelationInput


class GraphSelfFactObjectRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    relation_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
    expected_revision: int = Field(ge=1)


class GraphSelfFactReviewRequest(GraphSelfFactObjectRequest):
    decision: Literal["approved", "rejected"]
    self_non_sensitive_fact: bool = False
    non_personal_target: bool = False


class GraphExtractRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    source: KnowledgeChunkPointer
    run_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
    vocabulary: GraphVocabulary


class GraphExtractSuggestionView(StrictModel):
    relation_id: str
    confidence: float = Field(ge=0.60, le=0.85)
    evidence: str = Field(max_length=240)
    evidence_start_char: int = Field(ge=0)


class GraphExtractView(StrictModel):
    state: Literal["disabled", "proposed", "no_candidates", "already_attempted"]
    relations: list[GraphRelationView] = Field(max_length=2)
    suggestions: list[GraphExtractSuggestionView] = Field(max_length=2)
    discarded_below_threshold: int = Field(ge=0, le=2)


class GraphAliasProposeRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(default=0, ge=0)
    value: AliasInput


class GraphObjectRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    kind: Literal["relation", "alias"]
    object_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)


class GraphReviewRequest(GraphObjectRequest):
    decision: Literal["approved", "rejected"]


class GraphWalkRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    seeds: list[str] = Field(min_length=1, max_length=4)
    max_depth: int = Field(default=2, ge=1, le=2)
    max_nodes: int = Field(default=32, ge=1, le=32)
    max_edges: int = Field(default=64, ge=1, le=64)
    deadline_ms: int = Field(default=50, ge=1, le=50)
    limit: int = Field(default=8, ge=1, le=8)


class GraphProjectionView(StrictModel):
    scope: Scope
    reader_id: str
    relations: list[GraphRelationView]
    aliases: list[GraphAliasView]
    paths: list[list[str]]
    stop_reason: Literal["exhausted", "depth", "node_budget", "edge_budget", "time_budget", "result_budget"]
    nodes_visited: int = Field(ge=0)
    edges_examined: int = Field(ge=0)


class KnowledgeChunkView(StrictModel):
    source: KnowledgeChunkPointer
    title: str
    body: str = Field(max_length=8192)


class KnowledgeSearchView(StrictModel):
    items: list[KnowledgeChunkView]


class RwsFeedbackStatusView(StrictModel):
    enabled: bool
    bandit_enabled: bool
    ingress_connected: bool
    pending_count: int = Field(ge=0)
    last_error: str = Field(max_length=64)
    settled_count: int = Field(ge=0)
    rewarded_count: int = Field(ge=0)


class SlangReviewStatusView(StrictModel):
    enabled: bool
    allowed_groups: list[str]
    last_report: dict[str, int] | None


class ObservationSourceView(StrictModel):
    source_id: str
    source_revision: int
    subject_id: str


class ObservationCandidateView(StrictModel):
    candidate_id: str
    candidate_revision: int


class ObservationPoolView(StrictModel):
    domain: Literal["slang", "style"]
    pool_key: str
    count: int
    sources: list[ObservationSourceView]
    candidate_ids: list[str]
    output_policy: Literal["allow_use", "transform", "observe_only"] | None
    risk_tags: list[str]
    semantic_checkpoint: int
    backlog_checkpoint: int


class ObservationPoolPageView(StrictModel):
    items: list[ObservationPoolView]
    next_cursor: str | None


class ObservationJobView(StrictModel):
    job_id: str
    pool_key: str
    candidate: ObservationCandidateView
    object_id: str
    object_revision: int
    chain: Literal["semantic", "backlog"]
    threshold: int
    count: int
    sources: list[ObservationSourceView]
    revision: int
    status: str


class ObservationJobPageView(StrictModel):
    items: list[ObservationJobView]
    next_cursor: str | None


class ObservationReviewRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    verdict: Literal["approved", "rejected", "kept", "failed", "cancelled"]


class KnowledgeSourceView(StrictModel):
    scope: Scope
    source_id: str
    uploader_id: str
    source_label: str
    title: str
    content_hash: str
    revision: int
    content_revision: int
    review_status: Literal["pending", "approved", "rejected"]
    reviewed_content_revision: int | None
    review_actor: str | None
    status: Literal["inactive", "active", "removed"]
    apply_actor: str | None
    upload_policy_revision: int
    review_policy_revision: int | None
    apply_policy_revision: int | None
    index_version: str
    created_at: float
    updated_at: float


class KnowledgeSourcePageView(StrictModel):
    items: list[KnowledgeSourceView]
    next_cursor: str | None


class KnowledgeImportRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    document: MarkdownSourceInput


class KnowledgeReviewRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    decision: Literal["approved", "rejected"]
    non_personal_document: bool = False


class KnowledgeActivationRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    active: bool


class KnowledgeRemoveRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)


class KnowledgeDocumentView(StrictModel):
    source: KnowledgeSourceView
    document: MarkdownSourceInput


class LearningAutoApplyReportView(StrictModel):
    considered: int
    applied: int
    recovered: int
    kept: int
    errors: list[str]


class LearningAutoApplyStatusView(StrictModel):
    enabled: bool
    allowed_groups: list[str]
    allowed_domains: list[Literal["fact", "slang", "style"]]
    last_report: LearningAutoApplyReportView | None = None


class LearningAutoApplyReceiptView(StrictModel):
    audit_id: int | None = None
    receipt_id: str
    domain: Literal["fact", "slang", "style", "episode"]
    candidate_id: str
    candidate_revision: int
    object_id: str
    object_revision: int
    applied_event_id: str
    source_id: str
    source_revision: int
    policy_version: str
    state: Literal["applied", "rolled_back"]


class LearningAutoApplyReceiptPageView(StrictModel):
    items: list[LearningAutoApplyReceiptView]
    next_cursor: int | None = None


class LearningAutoApplyRollbackRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    receipt_id: str = Field(min_length=1, max_length=128)
    expected_object_revision: int = Field(ge=1)
    reason: Literal["admin_disabled"] = "admin_disabled"


class MemoryDisableFactRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    fact_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    reason: Literal["admin_disabled"] = "admin_disabled"


class MemoryResolveConflictRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    candidate_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    expected_target_revision: int = Field(ge=1)
    reason: Literal["verified_source"] = "verified_source"


class MemoryRetrievalDiagnosticRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    query: str = Field(default="", max_length=1024)
    subject_id: str | None = Field(default=None, min_length=1, max_length=64)


class MemoryRetrievalDiagnosticView(StrictModel):
    hot_hit_count: int
    cold_hit_count: int
    document_hit_count: int
    graph_hit_count: int = Field(default=0, ge=0)
    temporal_trace_version_count: int | None = Field(default=None, ge=0)
    cold_pack_state: str
    total_budget: int
    total_budget_used: int
    cold_budget_used: int
    policy_revision: int | None
    visibility_revision: int | None
    retrieve_mode: str
    omitted_reason_codes: list[str]
    reason_codes: list[str]

ApiFormat = Literal["openai_chat", "openai_responses", "anthropic", "deepseek"]
ConnectionState = Literal["isolated", "connected", "disconnected"]
RequestState = Literal[
    "accepted",
    "running",
    "queued",
    "succeeded",
    "cancelled_before_dispatch",
    "failed",
    "denied",
    "unknown",
    "superseded",
]
EditableField = Literal[
    "persona_name",
    "persona_instructions",
    "persona_mode",
    "persona_source_markdown",
    "group_modes",
    "group_profiles",
    "group_calendar_events",
    "proactive_contact",
    "onebot_token_env",
    "reply_segment_chars",
    "max_reply_segments",
    "active_model",
    "models",
    "task_models",
    "onebot_endpoint",
    "qq_delivery_limits",
    "timezone",
    "queue_capacity",
    "total_timeout",
    "reply_generation_timeout",
    "reply_admission_timeout",
    "reply_delivery_timeout",
    "model_timeout",
    "send_timeout",
    "history_ttl",
    "max_sessions",
    "tool_capabilities",
    "search_endpoint",
    "web_fetch_hosts",
    "http_api_hosts",
    "visual_url_hosts",
    "model_concurrency",
    "thinker_reserve",
    "max_active_sessions",
    "thinker_enabled",
    "mention_force_reply_enabled",
    "stream_reply_enabled",
    "planned_reply_enabled",
    "planned_reply_groups",
    "video_metadata_enabled",
    "video_metadata_groups",
    "url_titles_enabled",
    "url_titles_groups",
    "episode_query_rerank_enabled",
    "element_rules_enabled",
    "element_rules_groups",
    "element_custom_rules",
    "model_prices",
    "graph_extraction_enabled",
    "graph_extraction_groups",
    "followup_reply_enabled",
    "followup_reply_groups",
    "max_interruptions",
    "bot_pair_guard_enabled",
    "bot_pair_loop_alt_threshold",
    "bot_pair_known_alt_threshold",
    "bot_pair_cooldown_seconds",
    "known_bot_ids",
    "rws_mode",
    "rws_hawkes_enabled",
    "rws_feedback_enabled",
    "rws_bandit_enabled",
    "rws_threshold",
    "climate_mode",
    "willingness_enabled",
    "willingness_groups",
    "diagnostic_commands_enabled",
    "diagnostic_commands_groups",
    "research_enabled", "research_groups", "research_spool_dir", "research_key_file",
    "context_observation_enabled",
    "graph_observation_enabled",
    "cross_group_sharing_enabled",
    "private_conversation_enabled",
    "private_conversation_peers",
    "echo_enabled",
    "echo_groups",
    "food_enabled",
    "food_search_enabled",
    "food_search_group_overrides",
    "food_groups",
    "affection_enabled",
    "affection_groups",
    "worldbook_enabled",
    "worldbook_chat_projection_enabled",
    "worldbook_schedule_projection_enabled",
    "worldbook_storylet_enabled",
    "worldbook_dream_proposal_enabled",
    "worldbook_social_evidence_enabled",
    "worldbook_allowed_groups",
    "journal_enabled",
    "journal_allowed_groups",
    "journal_allow_live_publish",
    "journal_allowed_live_uins",
    "character_recognition_enabled",
    "character_recognition_groups",
    "ccip_endpoint",
    "character_reference_path",
    "animetrace_endpoint",
    "animetrace_model",
    "character_teaching_enabled",
    "character_teaching_groups",
    "self_nickname_enabled",
    "self_nickname_groups",
    "slang_machine_review_enabled",
    "learning_auto_apply_enabled",
    "learning_auto_apply_groups",
    "learning_auto_apply_domains",
    "retrieval_query_planner_enabled",
    "memory_capture_enabled",
    "memory_capture_groups",
    "memory_extraction_domains",
    "memory_spool_dir",
    "memory_key_file",
]


class EditableModelProfile(StrictModel):
    """The complete, secret-free profile document persisted by settings."""

    api_format: ApiFormat
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=200)
    api_key_env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,127}$")
    max_output_tokens: int = Field(ge=1, le=32768)
    temperature: float | None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None
    thinking: bool
    send_history: bool
    vision_enabled: bool
    token_parameter: Literal["max_tokens", "max_completion_tokens"] | None


class EditableConfig(StrictModel):
    """All fields accepted by the settings PUT endpoint."""

    persona_name: str = Field(default="", max_length=80)
    persona_instructions: str = Field(default="", max_length=6000)
    persona_mode: PersonaMode = "simple"
    persona_source_markdown: str = Field(default="", max_length=MAX_PERSONA_SOURCE_CHARS)
    group_modes: dict[str, GroupMode] = Field(max_length=128)
    group_profiles: dict[str, GroupProfileOverride] = Field(max_length=128)
    group_calendar_events: dict[str, list[GroupCalendarEvent]] = Field(max_length=128)
    proactive_contact: ContactSettings | None = None
    onebot_token_env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,127}$")
    reply_segment_chars: int = Field(ge=1, le=2000)
    max_reply_segments: int = Field(ge=1, le=5)
    active_model: str
    models: dict[str, EditableModelProfile] = Field(min_length=1, max_length=16)
    task_models: dict[TaskName, str]
    onebot_endpoint: str = Field(min_length=1)
    qq_delivery_limits: QQDeliveryLimits = Field(default_factory=QQDeliveryLimits)
    timezone: str = Field(min_length=1)
    queue_capacity: int = Field(ge=1, le=256)
    total_timeout: float = Field(gt=0, le=105)
    reply_generation_timeout: float = Field(default=30.0, gt=0, le=30)
    reply_admission_timeout: float = Field(default=30.0, gt=0, le=30)
    reply_delivery_timeout: float = Field(default=45.0, gt=0, le=45)
    model_timeout: float = Field(gt=0, le=60)
    send_timeout: float = Field(gt=0, le=30)
    history_ttl: float = Field(gt=0, le=3600)
    max_sessions: int = Field(ge=1, le=1024)
    tool_capabilities: list[str]
    search_endpoint: str = Field(default="", max_length=2048)
    web_fetch_hosts: list[str] = Field(default_factory=list, max_length=32)
    http_api_hosts: list[str] = Field(default_factory=list, max_length=32)
    visual_url_hosts: list[str] = Field(default_factory=list, max_length=32)
    model_concurrency: int = Field(ge=2, le=32)
    thinker_reserve: int = Field(ge=1)
    max_active_sessions: int = Field(ge=1, le=64)
    thinker_enabled: bool
    mention_force_reply_enabled: bool = True
    stream_reply_enabled: bool = False
    planned_reply_enabled: bool = False
    planned_reply_groups: list[str] = Field(default_factory=list, max_length=128)
    video_metadata_enabled: bool = False
    video_metadata_groups: list[str] = Field(default_factory=list, max_length=128)
    url_titles_enabled: bool = False
    url_titles_groups: list[str] = Field(default_factory=list)
    episode_query_rerank_enabled: bool = False
    element_rules_enabled: bool = False
    element_rules_groups: list[str] = Field(default_factory=list)
    element_custom_rules: list[ElementRuleConfig] = Field(default_factory=lambda: list[ElementRuleConfig]())
    model_prices: list[ModelPrice] = Field(default_factory=lambda: list[ModelPrice]())
    graph_extraction_enabled: bool = False
    graph_extraction_groups: list[str] = Field(default_factory=list, max_length=128)
    followup_reply_enabled: bool = False
    followup_reply_groups: list[str] = Field(default_factory=list, max_length=128)
    max_interruptions: int = Field(ge=0, le=16)
    bot_pair_guard_enabled: bool
    bot_pair_loop_alt_threshold: int = Field(ge=1, le=127)
    bot_pair_known_alt_threshold: int = Field(ge=1, le=127)
    bot_pair_cooldown_seconds: int = Field(ge=1, le=3600)
    known_bot_ids: list[str] = Field(max_length=128)
    rws_hawkes_enabled: bool = False
    rws_feedback_enabled: bool = False
    rws_bandit_enabled: bool = False
    rws_mode: RwsMode
    rws_threshold: float = Field(ge=0, le=1)
    climate_mode: ClimateMode = "off"
    willingness_enabled: bool = False
    willingness_groups: list[str] = Field(default_factory=list, max_length=128)
    diagnostic_commands_enabled: bool = False
    diagnostic_commands_groups: list[str] = Field(default_factory=list, max_length=128)
    research_enabled: bool = False
    research_groups: list[str] = Field(default_factory=list, max_length=128)
    research_spool_dir: str = Field(default="", max_length=4096)
    research_key_file: str = Field(default="", max_length=4096)
    context_observation_enabled: bool = False
    graph_observation_enabled: bool = False
    cross_group_sharing_enabled: bool = False
    private_conversation_enabled: bool = False
    private_conversation_peers: list[str] = Field(default_factory=list, max_length=128)
    echo_enabled: bool = False
    echo_groups: list[str] = Field(default_factory=list, max_length=128)
    food_enabled: bool = False
    food_search_enabled: bool = False
    food_search_group_overrides: dict[str, bool] = Field(default_factory=lambda: dict[str, bool]())
    food_groups: list[str] = Field(default_factory=list, max_length=128)
    affection_enabled: bool = False
    affection_groups: list[str] = Field(default_factory=list, max_length=128)
    worldbook_enabled: bool = False
    worldbook_chat_projection_enabled: bool = False
    worldbook_schedule_projection_enabled: bool = False
    worldbook_storylet_enabled: bool = False
    worldbook_dream_proposal_enabled: bool = False
    worldbook_social_evidence_enabled: bool = False
    worldbook_allowed_groups: list[str] = Field(default_factory=list, max_length=128)
    journal_enabled: bool = False
    journal_allowed_groups: list[str] = Field(default_factory=list, max_length=128)
    journal_allow_live_publish: bool = False
    journal_allowed_live_uins: list[str] = Field(default_factory=list, max_length=8)
    character_recognition_enabled: bool = False
    character_recognition_groups: list[str] = Field(default_factory=list, max_length=128)
    ccip_endpoint: str = Field(default="", max_length=2048)
    character_reference_path: str = Field(default="", max_length=4096)
    animetrace_endpoint: str = Field(default="", max_length=2048)
    animetrace_model: str = Field(default="", max_length=128)
    character_teaching_enabled: bool = False
    character_teaching_groups: list[str] = Field(default_factory=list, max_length=128)
    self_nickname_enabled: bool = False
    self_nickname_groups: list[str] = Field(default_factory=list, max_length=128)
    slang_machine_review_enabled: bool = False
    learning_auto_apply_enabled: bool = False
    learning_auto_apply_groups: list[str] = Field(default_factory=list, max_length=128)
    learning_auto_apply_domains: list[Literal["fact", "slang", "style"]] = Field(
        default_factory=lambda: ["fact", "slang", "style"], max_length=3
    )
    retrieval_query_planner_enabled: bool = False
    memory_capture_enabled: bool = False
    memory_capture_groups: list[str] = Field(default_factory=list, max_length=128)
    memory_extraction_domains: list[Literal["fact", "slang", "style", "episode"]] = Field(
        default_factory=lambda: ["fact", "slang", "style", "episode"], min_length=1, max_length=4
    )
    memory_spool_dir: str = Field(default="", max_length=4096)
    memory_key_file: str = Field(default="", max_length=4096)

    @field_validator("group_modes")
    @classmethod
    def exact_group_mode_ids(cls, value: dict[str, GroupMode]) -> dict[str, GroupMode]:
        return validate_group_mode_ids(value)

    @field_validator("group_profiles")
    @classmethod
    def exact_group_profile_ids(
        cls, value: dict[str, GroupProfileOverride]
    ) -> dict[str, GroupProfileOverride]:
        return validate_group_profile_ids(value)

    @field_validator("group_calendar_events")
    @classmethod
    def exact_group_calendar_ids(
        cls, value: dict[str, list[GroupCalendarEvent]]
    ) -> dict[str, list[GroupCalendarEvent]]:
        return validate_group_calendar_events(value)

    @field_validator("journal_allowed_live_uins")
    @classmethod
    def exact_journal_accounts(cls, value: list[str]) -> list[str]:
        return validate_journal_uins(value)

    @field_validator("worldbook_allowed_groups", "journal_allowed_groups")
    @classmethod
    def exact_worldbook_group_ids(cls, value: list[str]) -> list[str]:
        return validate_worldbook_group_ids(value)

    @field_validator(
        "memory_capture_groups", "learning_auto_apply_groups", "self_nickname_groups", "affection_groups",
        "willingness_groups", "food_groups", "echo_groups", "diagnostic_commands_groups",
        "character_recognition_groups", "planned_reply_groups",
        "followup_reply_groups", "graph_extraction_groups", "video_metadata_groups",
    )
    @classmethod
    def exact_memory_group_ids(cls, value: list[str]) -> list[str]:
        return validate_memory_group_ids(value)


class SettingsSnapshot(StrictModel):
    revision: int = Field(ge=1)
    effective_revision: int = Field(ge=1)
    restart_required: bool
    config: EditableConfig
    effective_config: EditableConfig
    changed_fields: list[EditableField]
    versions: list[int] = Field(min_length=1)


class SettingsWriteRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    config: EditableConfig


class SettingsRollbackRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    target_revision: int = Field(ge=1)


class PersonaPreviewRequest(StrictModel):
    source_markdown: str = Field(max_length=MAX_PERSONA_SOURCE_CHARS)


class PersonaPreviewIssue(StrictModel):
    code: str = Field(min_length=1, max_length=64)
    line: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=256)


class PersonaPreviewResponse(StrictModel):
    valid: bool
    source_ref: str = Field(min_length=1, max_length=128)
    source_hash: str = Field(min_length=1, max_length=128)
    version: str | None = Field(default=None, max_length=128)
    system: str = Field(default="", max_length=MAX_MODEL_SYSTEM_CHARS)
    issues: list[PersonaPreviewIssue] = Field(max_length=16)


class TaskBinding(StrictModel):
    profile: str
    api_format: ApiFormat
    model: str
    policy_provider: str


class ModelStatus(StrictModel):
    profile: str
    api_format: ApiFormat
    model: str
    policy_provider: str
    task_bindings: dict[TaskName, TaskBinding]


class ToolDestinationView(StrictModel):
    tool_id: str
    destination: str


class PolicySnapshot(StrictModel):
    sticker_description_available: bool = False
    revision: int = Field(ge=0)
    grants: list[Grant] = Field(max_length=256)
    model_status: ModelStatus = Field(alias="model_config")
    bot_id: str = Field(min_length=1, max_length=64)
    thinker_enabled: bool
    mode: Literal["offline", "live"]
    tool_destinations: list[ToolDestinationView] = Field(default_factory=lambda: list[ToolDestinationView]())
    request_destination_tools: list[str] = Field(default_factory=list)
    diagnostic_commands_enabled: bool = False
    diagnostic_commands_groups: list[str] = Field(default_factory=list)
    private_conversation_enabled: bool = False
    private_conversation_peers: list[str] = Field(default_factory=list)
    cross_group_sharing_enabled: bool = False
    visibility_grants: list[VisibilityGrant] = Field(
        default_factory=lambda: list[VisibilityGrant](), max_length=256,
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True, frozen=True)


class ContactConsentView(StrictModel):
    revision: int = Field(ge=0)
    consents: list[ContactConsent] = Field(max_length=256)
    runtime_settings: ContactSettings | None


class ContactConsentRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    kind: Literal["user", "group"]
    subject_id: str = Field(min_length=1, max_length=64)
    enabled: bool

    @field_validator("subject_id")
    @classmethod
    def exact_subject(cls, value: str) -> str:
        if any(character.isspace() for character in value) or "*" in value:
            raise ValueError("contact consent requires an exact target")
        return value


class ContactConsentResponse(StrictModel):
    revision: int = Field(ge=0)
    consent: ContactConsent


class VisibilitySnapshot(StrictModel):
    revision: int = Field(ge=0)
    cross_group_sharing_enabled: bool
    visibility_grants: list[VisibilityGrant] = Field(max_length=256)


class VisibilityOptionView(StrictModel):
    ref: VisibilityObjectRef
    label: str = Field(max_length=128)
    summary: str = Field(max_length=256)


class VisibilityOptionsView(StrictModel):
    source_scope: Scope
    material_type: VisibilityMaterial
    items: list[VisibilityOptionView] = Field(max_length=64)
    partial: bool


class VisibilityGrantRequest(StrictModel):
    expected_policy_revision: int = Field(ge=0)
    grant_id: str = Field(min_length=1, max_length=64)
    expected_grant_revision: int | None = Field(ge=1)
    source_scope: Scope
    target_scope: Scope
    material_type: VisibilityMaterial
    object_refs: tuple[VisibilityObjectRef, ...] = Field(min_length=1, max_length=64)
    non_personal_projection_confirmed: Literal[True]
    expires_at: float = Field(gt=0)


class VisibilityRevokeRequest(StrictModel):
    expected_policy_revision: int = Field(ge=0)
    grant_id: str = Field(min_length=1, max_length=64)
    expected_grant_revision: int = Field(ge=1)


class VisibilityMutationResponse(StrictModel):
    revision: int = Field(ge=0)
    visibility_grant: VisibilityGrant


class PolicyWriteRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    grants: list[Grant] = Field(max_length=256)


class RevisionResponse(StrictModel):
    revision: int = Field(ge=0)


class ActionStatus(StrictModel):
    key: str
    request_id: str
    action: str
    state: str
    revision: int = Field(ge=0)
    code: str


class DeliveryStatus(StrictModel):
    request_id: str
    total: int = Field(ge=1, le=8)
    sent: int = Field(ge=0, le=8)
    state: Literal[
        "complete",
        "partial_unknown",
        "unknown",
        "partial_failed",
        "not_sent",
        "partial",
        "pending",
    ]


class InstanceIdentity(StrictModel):
    id: str
    name: str
    bot_id: str


class ModelBudgetStatus(StrictModel):
    active: int = Field(ge=0)
    waiting: int = Field(ge=0)
    reply_active: int = Field(ge=0)
    thinker_active: int = Field(ge=0)


class StatusSnapshot(StrictModel):
    dev_web_bypass: bool = False
    actions: dict[str, int]
    requests: dict[str, int]
    storage_errors: int = Field(ge=0)
    recent: list[ActionStatus]
    deliveries: list[DeliveryStatus]
    mode: Literal["offline", "live"]
    model_status: ModelStatus = Field(alias="model_config")
    instance: InstanceIdentity
    policy_revision: int = Field(ge=0)
    config_revision: str
    queue_depth: int = Field(ge=0)
    connection: ConnectionState
    onebot_api_ready: bool = False
    last_error: str
    runtime_errors: int = Field(ge=0)
    transport_fault: bool
    active_transports: int = Field(ge=0)
    active_sessions: int = Field(ge=0)
    interruptions: int = Field(ge=0)
    model_budget: ModelBudgetStatus

    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True, frozen=True)


class UsageSummary(StrictModel):
    model_calls: int = Field(ge=0)
    reported_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(ge=0)


class TraceAction(StrictModel):
    key: str
    action: str
    state: str
    code: str
    task: str
    elapsed_ms: int | None
    first_delta_ms: int | None
    usage: ModelUsage | None
    provider: str | None = None
    model: str | None = None
    pricing: dict[str, JsonValue] | None = None


class RequestTrace(StrictModel):
    request_id: str
    state: str
    code: str
    actions: list[TraceAction]
    origin: Literal["conversation", "background"] = "conversation"
    block_traces: list[PromptBlockTrace] = Field(default_factory=lambda: list[PromptBlockTrace]())


class ParticipationSignalAvailability(StrictModel):
    name: Literal[
        "legacy_threshold",
        "eot_probability",
        "hawkes_rho",
        "outcome_ratio",
        "reward_feedback",
        "memory_familiarity",
        "willingness_phase",
        "info_gain",
        "skip_pressure",
        "mood_residual",
        "schedule_residual",
    ]
    status: Literal["available", "missing", "disabled"]


class ParticipationDiagnostic(StrictModel):
    instance_id: str = Field(min_length=1, max_length=64)
    bot_id: str = Field(min_length=1, max_length=64)
    group_id: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["force", "forbid", "gray"]
    stage_a_outcome: Literal["not_run", "complete", "hold", "error", "cancelled"]
    reason: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    history_denied: bool
    rws_mode: Literal["disabled", "shadow", "primary"]
    rws_score: float | None = Field(default=None, ge=0, le=1)
    rws_threshold: float = Field(ge=0, le=1)
    rws_state: Literal["not_evaluated", "scored", "neutral_no_weighted_signals"]
    semantic_repeat_shadow: Literal[
        "not_evaluated",
        "candidate",
        "clear",
        "no_topic",
        "no_success_receipt",
        "history_denied",
        "stream_skipped",
        "unavailable",
    ]
    semantic_repeat_score: float | None = Field(default=None, ge=0, le=1)
    signal_availability: list[ParticipationSignalAvailability] = Field(max_length=11)
    loop_fuse_reason: Literal["pair_loop_cooldown"] | None = None


class TopicMentionRouteDiagnostic(StrictModel):
    target_id: str = Field(min_length=1, max_length=64)
    source_event_ids: list[str] = Field(max_length=8)
    topic_ids: list[str] = Field(max_length=8)
    truncated: bool = False


class TopicEdgeDiagnostic(StrictModel):
    instance_id: str = Field(min_length=1, max_length=64)
    bot_id: str = Field(min_length=1, max_length=64)
    group_id: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=128)
    author_id: str = Field(min_length=1, max_length=64)
    message_id: str = Field(max_length=64)
    mention_targets: list[str] = Field(max_length=128)
    mention_routes: list[TopicMentionRouteDiagnostic] = Field(max_length=128)
    topic_id: str | None = Field(default=None, max_length=128)
    topic_parent_event_id: str | None = Field(default=None, max_length=128)
    topic_edge_kind: Literal[
        "root", "reply", "bot_receipt", "reframe", "unknown", "conflict", "unknown_source_expired"
    ]
    bot_involved: bool


class TopicEdgeDiagnosticsSnapshot(StrictModel):
    topic_edges: list[TopicEdgeDiagnostic] = Field(max_length=64)


class HealthComponent(StrictModel):
    component: Literal["schedule", "storylet", "memory", "rws_feedback", "domain_revocation", "contact_hooks"]
    state: Literal["healthy", "degraded", "unknown", "disabled", "stopping"]
    code: Literal["disabled", "no_report", "observed", "owner_error", "report_failed",
                  "task_unavailable", "task_finished", "task_cancelled", "stopping"]
    report_present: bool
    failure_count: int | None = Field(default=None, ge=0)


class AggregateHealthSnapshot(StrictModel):
    state: Literal["healthy", "degraded", "unknown", "disabled", "stopping"]
    observed_at: str
    components: list[HealthComponent] = Field(max_length=6)


class DiagnosticsSnapshot(StrictModel):
    usage: UsageSummary
    traces: list[RequestTrace]
    background_traces: list[RequestTrace] = Field(default_factory=lambda: list[RequestTrace](), max_length=20)
    participation: list[ParticipationDiagnostic] = Field(max_length=64)


class GraphObservationView(StrictModel):
    enabled: bool
    report: GraphHealthSnapshot | None


class NapcatConnectRequest(StrictModel):
    endpoint: str = Field(min_length=1, max_length=2048)
    token: str = Field(min_length=1, max_length=4096, repr=False)
    totp_code: str = Field(default="", pattern=r"^(?:[0-9]{6})?$")


class NapcatStatus(StrictModel):
    connected: bool = False
    logged_in: bool = False
    offline: bool = False
    phase: str = ""
    qr_url: str = ""


class NapcatLogs(StrictModel):
    lines: list[str]
    detail: str


class NapcatRuntime(StrictModel):
    available: bool = False
    phase: Literal["disabled", "running", "stopped"] = "disabled"
    oom_killed: bool = False


class ModelAccessRequest(StrictModel):
    profile: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: int = Field(ge=0)
    api_key: str = Field(default="", max_length=4096, repr=False)


class ModelCheckResponse(StrictModel):
    ok: bool
    detail: str


class ModelCatalogRequest(StrictModel):
    api_format: Literal["openai_chat", "openai_responses", "anthropic", "deepseek"]
    endpoint: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(default="", max_length=4096, repr=False)
    profile: str | None = Field(default=None, max_length=64)
    expected_revision: int | None = Field(default=None, ge=0)


class ModelCatalogResponse(StrictModel):
    models: list[str]
    source_url: str
    pages: int
    truncated: bool
    display_names: dict[str, str]


class TokenRequest(StrictModel):
    token: str = Field(max_length=256)


class AuthResponse(StrictModel):
    authenticated: bool


class QQWaitingTargetView(StrictModel):
    scope_key: QQScopeKey
    waiting: int = Field(ge=0)


class QQTestBatchView(StrictModel):
    batch_id: str
    scope_key: QQScopeKey
    write_limit: Literal[6, 12]
    expires_in_seconds: float = Field(ge=0)
    committed_cost: int = Field(ge=0)
    ended: bool
    reason: str


class QQDeliveryView(StrictModel):
    available: bool
    limits: QQDeliveryLimits
    snapshot: QQDeliverySnapshot | None = None
    local_held: bool = True
    local_hold_reason: str = ""
    connection_ready: bool = False
    waiting: int = Field(default=0, ge=0)
    waiting_targets: list[QQWaitingTargetView] = Field(default_factory=lambda: list[QQWaitingTargetView]())
    in_flight: bool = False
    test_batch: QQTestBatchView | None = None


class QQBotBatchProof(StrictModel):
    account_id: str
    available: bool
    held: bool
    connection_ready: bool
    test_batch: QQTestBatchView | None = None


class QQPauseRequest(StrictModel):
    account_id: str = Field(min_length=1, max_length=64)
    scope: ConversationScope | None = None
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=128)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a processing reason is required")
        return value


class QQResumeRequest(QQPauseRequest):
    reviewed_unknown_action_ids: list[str] = Field(default_factory=list, max_length=256)
    test_batch_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class QQBatchBeginRequest(StrictModel):
    account_id: str = Field(min_length=1, max_length=64)
    scope: ConversationScope
    batch_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    duration_seconds: float = Field(default=1200.0, gt=0, le=1200)


class QQBatchEndRequest(StrictModel):
    account_id: str = Field(min_length=1, max_length=64)
    scope: ConversationScope | None = None
    batch_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    reason: str = Field(default="admin_batch_ended", min_length=1, max_length=128)


class CoreRuntime(StrictModel):
    instance_id: str
    generation: str
    uptime_seconds: float
    managed: bool
    pending: bool
    revision: int
    effective_revision: int
    restart_required: bool
    active_sessions: int
    queue_depth: int
    last_restart_error: str


class CoreRestartRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    generation: str = Field(min_length=1, max_length=64)


class CoreRestartResponse(StrictModel):
    accepted: bool
    generation: str


class HealthResponse(StrictModel):
    ready: bool
    generation: str


class ErrorResponse(StrictModel):
    error: str


class OfflineChatRequest(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2000)


class OfflineChatResponse(StrictModel):
    request_id: str
    state: RequestState
    replies: list[str]


class RequestStatusResponse(StrictModel):
    request_id: str
    state: RequestState



class NativeBackupRecord(StrictModel):
    preparation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NativeReleaseStatusView(StrictModel):
    managed_instance: bool
    instance_id: str
    bot_id: str
    version_label: str
    running_release_sha256: str | None = None
    config_sha256: str | None = None
    settings_revision: int
    policy_revision: int
    source_archives: list[str] = Field(max_length=64)
    backups: list[NativeBackupRecord] = Field(max_length=64)
    archives_truncated: bool
    backups_truncated: bool


class NativeReleaseInputs(StrictModel):
    preparation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    target_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.zip$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.zip$")
    prior_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class NativePreparationRequest(NativeReleaseInputs):
    expected_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_settings_revision: int = Field(ge=1)
    expected_policy_revision: int = Field(ge=0)
    backup_scope_understood: bool = False


class NativePlanRequest(NativeReleaseInputs):
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_compatible: bool = False


class NativeManualPlanView(StrictModel):
    preparation_ready: bool
    blockers: list[str]
    update_steps: list[str]
    rollback_steps: list[str]
    limitations: list[str]


class NativePreparationView(StrictModel):
    preparation_id: str
    receipt_sha256: str
    database_sha256: str
    config_sha256: str
    schema_version: int
    settings_revision: int
    policy_revision: int
    target_sha256: str
    declared_prior_sha256: str | None
    plan: NativeManualPlanView

class StickerMetadataView(StrictModel):
    description: str = Field(default="", max_length=512)
    usage_hint: str = Field(default="", max_length=512)
    ocr_text: str = Field(default="", max_length=512)
    intent_tags: list[str] = Field(default_factory=list, max_length=16)
    affect_tags: list[str] = Field(default_factory=list, max_length=16)


class StickerEntryView(StickerMetadataView):
    sticker_id: str
    content_hash: str
    mime_type: Literal["image/jpeg", "image/png", "image/webp", "image/gif"]
    byte_size: int
    entry_revision: int
    status: Literal["pending", "approved", "revoked"]


class StickerCatalogView(StrictModel):
    group_id: str
    revision: int
    entries: list[StickerEntryView]
    description_available: bool


class StickerTargetRequest(StrictModel):
    group_id: str
    operation_id: str
    sticker_id: str
    expected_revision: int = Field(ge=0)


class StickerImportRequest(StickerTargetRequest):
    image_base64: str = Field(min_length=4, max_length=((8 * 1024 * 1024 + 2) // 3) * 4)
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    metadata: StickerMetadataView = Field(default_factory=StickerMetadataView)


class StickerMetadataRequest(StickerTargetRequest):
    metadata: StickerMetadataView


class StickerMutationView(StrictModel):
    operation_id: str
    catalog_revision: int
    sticker_id: str
    entry_revision: int
    status: Literal["pending", "approved", "revoked"]


class StickerDescriptionView(StrictModel):
    operation_id: str
    sticker_id: str
    catalog_revision: int
    entry_revision: int
    metadata: StickerMetadataView
    draft_only: Literal[True] = True


class CharacterReferenceBackupView(StrictModel):
    name: str
    revision: str


class CharacterPackBuildUpdate(StrictModel):
    group_id: str = Field(min_length=1, max_length=64)
    operation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    batch: CharacterPackBuildRequest
    images_base64: dict[str, Annotated[str, Field(max_length=4 * ((MAX_IMAGE_BYTES + 2) // 3))]] = Field(
        min_length=1, max_length=MAX_BATCH_IMAGES,
    )


class CharacterPackRestoreUpdate(StrictModel):
    pack_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    backup_name: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\\r\n]+$")
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class CharacterPackAssetView(StrictModel):
    pack_name: str
    revision: str | None
    character_ids: list[str]
    request_sha256: str | None
    artifact_sha256: str | None
    artifact_model: str | None
    artifact_registry_version: str | None
    runtime_model_match_verified: Literal[False] = False
    requires_reload: Literal[True] = True
    changed: bool = False
    backup_name: str | None = None


class CharacterReferenceStatusView(StrictModel):
    configured: bool
    saved_revision: str | None
    runtime_revision: str | None
    restart_required: bool
    metadata_json: str
    ccip_assembled: bool
    animetrace_assembled: bool
    backup: CharacterReferenceBackupView | None = None


class CharacterReferenceSaveRequest(StrictModel):
    metadata_json: str = Field(min_length=1, max_length=256 * 1024)
    expected_revision: str | None = Field(default=None, min_length=64, max_length=64)
    public_reference_metadata: bool = False


class CharacterReferenceRestoreRequest(StrictModel):
    backup_name: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\\r\n]+$")
    backup_revision: str = Field(min_length=64, max_length=64)
    expected_revision: str = Field(min_length=64, max_length=64)


class CharacterReferenceMergeRequest(StrictModel):
    character_ids: list[str] = Field(min_length=2, max_length=1024)
    series: str = Field(min_length=1, max_length=128)
    work: str = Field(min_length=1, max_length=160)
    expected_revision: str = Field(min_length=64, max_length=64)
    public_reference_metadata: bool = False


class CharacterReferenceMutationView(StrictModel):
    status: CharacterReferenceStatusView
    committed_revision: str
    operation: Literal["import", "merge"]
    changed: bool
    metadata_only: Literal[True] = True
    selected_ids: list[str]
    added_count: int
    existing_count: int
    input_sha256: str | None = None
    series: str | None = None


WEB_CONTRACTS: tuple[type[StrictModel], ...] = (
    CharacterPackBuildUpdate, CharacterPackRestoreUpdate, CharacterPackAssetView,
    GroupBoardView,
    JournalCreateRequest, JournalDecisionRequest, JournalRevisionRequest, JournalDryRunRequest,
    JournalDraftView, JournalHeadPageView, JournalDryRunView,
    JournalApprovalRequest, JournalStatusView, JournalPublicConsentPageView,
    JournalFictionPreviewRequest, JournalFactualPreviewRequest, JournalFictionPreviewView,
    JournalPublishRequest, JournalDeliveryView, JournalDeliveryPageView, JournalResolveRequest,
    StickerCatalogView, StickerImportRequest, StickerMetadataRequest, StickerTargetRequest,
    StickerMutationView, StickerDescriptionView,
    VisibilitySnapshot, VisibilityGrantRequest, VisibilityRevokeRequest, VisibilityMutationResponse,
    VisibilityOptionView, VisibilityOptionsView,
    ContextObservationSnapshot,
    GraphObservationView,
    NativeBackupRecord, NativeReleaseStatusView, NativeReleaseInputs, NativePreparationRequest,
    NativePlanRequest, NativeManualPlanView, NativePreparationView,
    CharacterReferenceStatusView,
    CharacterReferenceSaveRequest,
    CharacterReferenceRestoreRequest,
    CharacterReferenceMergeRequest,
    CharacterReferenceMutationView,
    StoryArcCreateRequest,
    StoryArcView,
    StoryArcListView,
    DreamProposeRequest,
    DreamDecisionRequest,
    DreamProposalView,
    DreamProposalPageView,
    MemoryCandidateView,
    SocialProgressView,
    DomainLearningCandidateView,
    EpisodeManagementView,
    FamiliarityView,
    FamiliarityAdjustmentRequest,
    EpisodeDecayRequest,
    EpisodePromptStateRequest,
    SelfAliasResolution,
    StyleManagementView,
    StyleObjectRequest,
    StyleFeedbackRequest,
    StyleProfileRequest,
    DomainLearningFailureView,
    DomainLearningFailurePageView,
    DomainLearningRetryRequest,
    DomainLearningRetryView,
    ExtractionRunDiagnosticView,
    ExtractionRunDiagnosticPageView,
    MemoryCandidatePageView,
    MemoryHotFactView,
    MemoryHotPreviewView,
    MemoryReviewRequest,
    MemoryApplyRequest,
    SocialRetryRequest,
    MemoryMatterView,
    MemoryMatterPageView,
    MemoryMatterProposalRequest,
    MemoryMatterReplaceRequest,
    MemoryMatterTransitionRequest,
    MemoryFactView,
    MemoryFactPageView,
    MemoryCardMetadataView, MemoryCardQueryView, MemoryCardClearRequest, MemoryCardClassificationRequest,
    MemoryCorrectionRequest,
    LearningAutoApplyStatusView,
    SlangGovernanceSuggestionView,
    SlangGovernanceSuggestionPageView,
    SlangGovernanceRevokeRequest,
    SlangReviewStatusView,
    RwsFeedbackStatusView,
    GraphRelationView,
    GraphAliasView,
    GraphObjectPageView,
    GraphRelationProposeRequest,
    GraphSelfFactSourceView,
    GraphSelfFactView,
    GraphSelfFactProposeRequest,
    GraphSelfFactObjectRequest,
    GraphSelfFactReviewRequest,
    GraphExtractRequest,
    GraphExtractView,
    GraphExtractSuggestionView,
    GraphAliasProposeRequest,
    GraphObjectRequest,
    GraphReviewRequest,
    GraphWalkRequest,
    GraphProjectionView,
    KnowledgeChunkView,
    KnowledgeSearchView,

    ObservationSourceView,
    ObservationCandidateView,
    ObservationPoolView,
    ObservationPoolPageView,
    ObservationJobView,
    ObservationJobPageView,
    ObservationReviewRequest,
    MarkdownSourceInput,
    KnowledgeSourceView,
    KnowledgeSourcePageView,
    KnowledgeImportRequest,
    KnowledgeReviewRequest,
    KnowledgeActivationRequest,
    KnowledgeRemoveRequest,
    KnowledgeDocumentView,
    LearningAutoApplyReportView,
    LearningAutoApplyReceiptView,
    LearningAutoApplyReceiptPageView,
    LearningAutoApplyRollbackRequest,
    MemoryDisableFactRequest,
    MemoryResolveConflictRequest,
    MemoryRetrievalDiagnosticRequest,
    MemoryRetrievalDiagnosticView,
    NapcatConnectRequest,
    NapcatStatus,
    NapcatLogs,
    NapcatRuntime,
    EditableModelProfile,
    EditableConfig,
    RecentHistoryRequest,
    RecentHistoryView,
    SettingsSnapshot,
    SettingsWriteRequest,
    SettingsRollbackRequest,
    PersonaPreviewRequest,
    PersonaPreviewIssue,
    PersonaPreviewResponse,
    TaskBinding,
    ModelStatus,
    PolicySnapshot,
    ContactConsentView,
    ContactConsentRequest,
    ContactConsentResponse,
    PolicyWriteRequest,
    RevisionResponse,
    ActionStatus,
    DeliveryStatus,
    InstanceIdentity,
    ModelBudgetStatus,
    StatusSnapshot,
    ParticipationSignalAvailability,
    ParticipationDiagnostic,
    TopicMentionRouteDiagnostic,
    TopicEdgeDiagnostic,
    TopicEdgeDiagnosticsSnapshot,
    DiagnosticsSnapshot,
    AggregateHealthSnapshot,
    ModelAccessRequest,
    ModelCheckResponse,
    ModelCatalogRequest,
    ModelCatalogResponse,
    TokenRequest,
    AuthResponse,
    HealthResponse,
    CoreRuntime,
    QQDeliveryView,
    QQBotBatchProof,
    QQPauseRequest,
    QQResumeRequest,
    QQBatchBeginRequest,
    QQBatchEndRequest,
    CoreRestartRequest,
    CoreRestartResponse,
    ErrorResponse,
    OfflineChatRequest,
    OfflineChatResponse,
    RequestStatusResponse,
)
