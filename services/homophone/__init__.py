"""Conservative request-time homophone interpretation helpers."""

from services.homophone.interpreter import (
    HomophoneEvidence,
    HomophoneInterpretation,
    build_homophone_hint,
    format_homophone_hint,
    interpret_homophones,
)
from services.homophone.slang_guard import filter_approved_slang_conflicts

__all__ = [
    "HomophoneEvidence",
    "HomophoneInterpretation",
    "build_homophone_hint",
    "filter_approved_slang_conflicts",
    "format_homophone_hint",
    "interpret_homophones",
]
