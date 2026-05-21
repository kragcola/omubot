"""Group slang learning service."""

from services.slang.backlog_reviewer import SlangBacklogReviewer
from services.slang.drift_reviewer import SlangDriftReviewer
from services.slang.errors import (
    SlangCollisionError,
    SlangCrossScopeMergeError,
    SlangDatabaseCorruptError,
)
from services.slang.extractor import SlangExtractor
from services.slang.store import SlangStore, normalize_term
from services.slang.types import (
    RepeatPolicy,
    SlangDriftReview,
    SlangExtraction,
    SlangExtractionRun,
    SlangObservation,
    SlangPendingCandidate,
    SlangScope,
    SlangSettings,
    SlangStatus,
    SlangTerm,
    SlangTermRevision,
)

__all__ = [
    "RepeatPolicy",
    "SlangBacklogReviewer",
    "SlangCollisionError",
    "SlangCrossScopeMergeError",
    "SlangDatabaseCorruptError",
    "SlangDriftReview",
    "SlangDriftReviewer",
    "SlangExtraction",
    "SlangExtractionRun",
    "SlangExtractor",
    "SlangObservation",
    "SlangPendingCandidate",
    "SlangScope",
    "SlangSettings",
    "SlangStatus",
    "SlangStore",
    "SlangTerm",
    "SlangTermRevision",
    "normalize_term",
]
