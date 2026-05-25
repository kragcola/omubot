"""Unified learning normalization layer for slang, style, and future plugins."""

from services.learning_normalizer.normalize import (
    NormalizationProfile,
    NormalizationScore,
    extract_features,
    fingerprint_key,
    normalize_key,
    score_similarity,
)
from services.learning_normalizer.store import (
    LearningNormalizerCluster,
    LearningNormalizerItem,
    LearningNormalizerPromptCandidate,
    LearningNormalizerRevision,
    LearningNormalizerStore,
    NormalizationResult,
    get_default_store,
)

__all__ = [
    "LearningNormalizerCluster",
    "LearningNormalizerItem",
    "LearningNormalizerPromptCandidate",
    "LearningNormalizerRevision",
    "LearningNormalizerStore",
    "NormalizationProfile",
    "NormalizationResult",
    "NormalizationScore",
    "extract_features",
    "fingerprint_key",
    "get_default_store",
    "normalize_key",
    "score_similarity",
]
