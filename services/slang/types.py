"""Shared types for the group slang system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from services.cross_group import CrossGroupVisibility

SlangStatus = Literal["candidate", "approved", "muted", "expired"]
SlangScope = Literal["group", "global"]
RepeatPolicy = Literal["understand_only", "allow_rephrase", "allow_use"]
SemanticBackend = Literal["ngram", "embedding"]

VALID_STATUSES: set[str] = {"candidate", "approved", "muted", "expired"}
VALID_SCOPES: set[str] = {"group", "global"}
VALID_REPEAT_POLICIES: set[str] = {"understand_only", "allow_rephrase", "allow_use"}

DEFAULT_DAILY_AI_REVIEW_TIMES: tuple[str, ...] = ("04:00", "16:00")


class SlangSettings(BaseModel):
    """Runtime-editable settings for slang learning and injection."""

    learning_enabled: bool = True
    injection_enabled: bool = True
    review_required: bool = True
    max_injected_terms: int = Field(default=8, ge=1, le=30)
    # Cap how many *non-direct-hit* approved terms can ride along the prompt
    # block as group background context. Direct-hit terms are not capped here;
    # they are bounded only by `max_injected_terms` total.  Set to 0 to inject
    # only terms whose surface form appears in the current conversation.
    max_indirect_inject_terms: int = Field(default=0, ge=0, le=30)
    extract_interval_minutes: int = Field(default=30, ge=1, le=24 * 60)
    candidate_min_count: int = Field(default=2, ge=1, le=50)
    group_allowlist: list[str] = Field(default_factory=list)
    repeat_policy: RepeatPolicy = "understand_only"
    extraction_batch_limit: int = Field(default=80, ge=10, le=500)
    auto_promote_global_enabled: bool = False
    global_promote_min_groups: int = Field(default=3, ge=2, le=20)
    bulk_page_size: int = Field(default=50, ge=10, le=200)
    stats_days: int = Field(default=14, ge=1, le=120)
    stoplist: list[str] = Field(default_factory=list)
    max_prompt_chars: int = Field(default=1200, ge=300, le=6000)
    daily_ai_review_enabled: bool = True
    daily_ai_review_times: list[str] = Field(default_factory=lambda: list(DEFAULT_DAILY_AI_REVIEW_TIMES))
    daily_ai_review_search_enabled: bool = True
    daily_ai_auto_approve_enabled: bool = False
    daily_ai_auto_approve_min_confidence: float = Field(default=0.82, ge=0.0, le=1.0)
    daily_ai_max_terms_per_group: int = Field(default=5, ge=1, le=30)
    daily_ai_recent_message_limit: int = Field(default=200, ge=20, le=1000)
    backlog_review_enabled: bool = True
    backlog_review_batch_size: int = Field(default=50, ge=10, le=200)
    backlog_review_min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    backlog_review_min_usage_count: int = Field(default=3, ge=1, le=20)
    backlog_review_search_enabled: bool = True
    backlog_auto_approve_enabled: bool = False
    backlog_auto_approve_min_confidence: float = Field(default=0.82, ge=0.0, le=1.0)
    backlog_kept_streak_limit: int = Field(default=2, ge=1, le=10)
    backlog_local_evidence_count: int = Field(default=5, ge=0, le=20)
    backlog_threshold_gating_enabled: bool = True
    drift_detection_enabled: bool = True
    drift_min_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    drift_semantic_gate_enabled: bool = True
    drift_age_out_days: int = Field(default=14, ge=0, le=180)
    lookup_tool_enabled: bool = True
    min_inject_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    semantic_backend: SemanticBackend = "ngram"

    @field_validator("group_allowlist", mode="before")
    @classmethod
    def _normalize_allowlist(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @field_validator("stoplist", mode="before")
    @classmethod
    def _normalize_stoplist(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @field_validator("daily_ai_review_times", mode="before")
    @classmethod
    def _normalize_daily_ai_review_times(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return list(DEFAULT_DAILY_AI_REVIEW_TIMES)
        if isinstance(value, str):
            raw_items: list[str] = [
                part.strip()
                for part in value.replace("，", ",").split(",")
                if part.strip()
            ]
        elif isinstance(value, (list, tuple)):
            raw_items = [str(item).strip() for item in value if str(item).strip()]
        else:
            return list(DEFAULT_DAILY_AI_REVIEW_TIMES)
        seen: set[str] = set()
        normalized: list[str] = []
        for item in raw_items:
            if ":" not in item:
                raise ValueError(f"daily_ai_review_times entry must be HH:MM, got {item!r}")
            hour_text, minute_text = item.split(":", 1)
            try:
                hour = int(hour_text)
                minute = int(minute_text)
            except ValueError as err:
                raise ValueError(f"daily_ai_review_times entry must be HH:MM, got {item!r}") from err
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                raise ValueError(f"daily_ai_review_times entry out of range: {item!r}")
            canonical = f"{hour:02d}:{minute:02d}"
            if canonical in seen:
                continue
            seen.add(canonical)
            normalized.append(canonical)
        if not normalized:
            return list(DEFAULT_DAILY_AI_REVIEW_TIMES)
        normalized.sort()
        return normalized

    @field_validator("semantic_backend")
    @classmethod
    def _validate_semantic_backend(cls, value: str) -> str:
        backend = str(value or "ngram").strip().lower()
        return backend if backend in {"ngram", "embedding"} else "ngram"

    def allows_group(self, group_id: str | None) -> bool:
        if not group_id:
            return False
        return not self.group_allowlist or str(group_id) in self.group_allowlist


@dataclass
class SlangTerm:
    term_id: str
    term: str
    meaning: str
    aliases: list[str]
    scope: SlangScope
    group_id: str
    confidence: float
    status: SlangStatus
    usage_count: int
    unique_users: list[str]
    first_seen_at: str
    last_seen_at: str
    created_at: str
    updated_at: str
    source: str = "extractor"
    repeat_policy: RepeatPolicy = "understand_only"
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    last_inferred_at: str | None = None
    cross_group_visible: bool = False
    cross_group_visibility: CrossGroupVisibility = "none"
    cross_group_enabled_by: str = ""
    cross_group_enabled_at: str = ""
    cross_group_enabled_for_groups: list[str] = field(default_factory=list)
    cross_group_enabled_reason: str = ""

    @property
    def unique_user_count(self) -> int:
        return len(self.unique_users)


@dataclass
class SlangObservation:
    observation_id: str
    term_id: str
    group_id: str
    user_id: str
    raw_text: str
    context: str
    observed_at: str
    reason: str = ""
    message_id: int | None = None


@dataclass
class SlangExtraction:
    term: str
    meaning: str
    aliases: list[str] = field(default_factory=list)
    evidence: str = ""
    confidence: float = 0.3
    reason: str = ""
    repeat_policy: RepeatPolicy = "understand_only"


@dataclass
class SlangPendingCandidate:
    pending_id: str
    term: str
    meaning: str
    aliases: list[str]
    group_id: str
    confidence: float
    count: int
    unique_users: list[str]
    evidence: str
    reason: str
    repeat_policy: RepeatPolicy
    first_seen_at: str
    last_seen_at: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SlangExtractionRun:
    run_id: str
    started_at: str
    finished_at: str | None
    status: str
    group_count: int
    scanned_messages: int
    extracted_terms: int
    promoted_candidates: int
    error: str
    duration_ms: int
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SlangTermRevision:
    revision_id: str
    term_id: str
    action: str
    actor: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str
    created_at: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SlangDriftReview:
    drift_id: str
    term_id: str
    term: str
    group_id: str
    old_meaning: str
    new_meaning: str
    aliases: list[str]
    evidence: str
    confidence: float
    reason: str
    status: str
    created_at: str
    updated_at: str
    meta: dict[str, Any] = field(default_factory=dict)
