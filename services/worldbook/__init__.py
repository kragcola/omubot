"""Worldbook Living Story Runtime — gated layered world-info service.

Default-off. When ``WorldbookConfig.enabled`` is false, callers must not
construct stores, register providers, select storylets, or mutate state.
"""

from __future__ import annotations

from services.worldbook.config import WorldbookConfig, worldbook_config_from_mapping
from services.worldbook.domain import (
    CanonEntry,
    CanonMutationError,
    Confidence,
    EventProposal,
    EventRecord,
    FictionCommitRecord,
    LifeState,
    LifeStateItem,
    PrivacyLevel,
    ProjectionBlock,
    ProjectionTrace,
    ProposalDecision,
    ProposalProcessResult,
    SocialEvidenceRef,
    SocialStoryCommitResult,
    SourceMeta,
    StoryLedgerView,
    Storylet,
    deterministic_social_story_event_id,
)
from services.worldbook.runtime import WorldbookRuntime, build_worldbook_runtime

__all__ = [
    "CanonEntry",
    "CanonMutationError",
    "Confidence",
    "EventProposal",
    "EventRecord",
    "FictionCommitRecord",
    "LifeState",
    "LifeStateItem",
    "PrivacyLevel",
    "ProjectionBlock",
    "ProjectionTrace",
    "ProposalDecision",
    "ProposalProcessResult",
    "SocialEvidenceRef",
    "SocialStoryCommitResult",
    "SourceMeta",
    "StoryLedgerView",
    "Storylet",
    "WorldbookConfig",
    "WorldbookRuntime",
    "build_worldbook_runtime",
    "deterministic_social_story_event_id",
    "worldbook_config_from_mapping",
]
