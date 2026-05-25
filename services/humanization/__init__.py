"""Humanization runtime contract and state-bus wiring."""

from services.humanization.classifier import (
    RegisterClassifier,
    RegisterDecision,
    RegisterLabel,
)
from services.humanization.contract import (
    AFFECTION_FAMILIARITY_SLOT,
    AFFECTION_STAGE_SLOT,
    CLOCK_CURRENT_SLOT,
    HUMANIZATION_CONTRACT,
    HUMANIZATION_MODULE_ID,
    LAST_METRICS_SLOT,
    MOOD_CURRENT_SLOT,
    REGISTER_LABEL_SLOT,
    REGISTER_RECENT_USED_SLOT,
    STICKER_RECENT_USED_SLOT,
    THINKER_LAST_DECISION_SLOT,
)
from services.humanization.mood_classifier import (
    MoodClassifier,
    MoodDecision,
    MoodLabel,
    MoodSignals,
)
from services.humanization.scorer import (
    HumanizationScore,
    StylometricScorer,
)
from services.humanization.state import (
    create_humanization_state_bus,
    humanization_source,
)

__all__ = [
    "AFFECTION_FAMILIARITY_SLOT",
    "AFFECTION_STAGE_SLOT",
    "CLOCK_CURRENT_SLOT",
    "HUMANIZATION_CONTRACT",
    "HUMANIZATION_MODULE_ID",
    "LAST_METRICS_SLOT",
    "MOOD_CURRENT_SLOT",
    "REGISTER_LABEL_SLOT",
    "REGISTER_RECENT_USED_SLOT",
    "STICKER_RECENT_USED_SLOT",
    "THINKER_LAST_DECISION_SLOT",
    "HumanizationScore",
    "MoodClassifier",
    "MoodDecision",
    "MoodLabel",
    "MoodSignals",
    "RegisterClassifier",
    "RegisterDecision",
    "RegisterLabel",
    "StylometricScorer",
    "create_humanization_state_bus",
    "humanization_source",
]
