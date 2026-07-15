"""Group-scoped collision guard between approved slang and generic hints."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from typing import Any, Protocol, cast

from services.homophone.interpreter import HomophoneEvidence, HomophoneInterpretation


class _FindMatchingTerms(Protocol):
    async def __call__(
        self,
        *,
        group_id: str,
        text: str,
        include_candidates: bool,
    ) -> list[Any]: ...


async def filter_approved_slang_conflicts(
    interpretation: HomophoneInterpretation,
    *,
    group_id: str | None,
    store_getter: Callable[[], Any] | None,
) -> HomophoneInterpretation:
    """Remove surfaces owned by approved group slang from an interpretation.

    Store failures fail closed because the generic meaning must not override a
    group-specific meaning merely because its source of truth is unavailable.
    """

    if not interpretation.changed or not group_id:
        return interpretation
    if store_getter is None:
        return _unchanged(interpretation.original_text)
    try:
        store = store_getter()
        find_matching_terms = getattr(store, "find_matching_terms", None)
        if store is None or not callable(find_matching_terms):
            return _unchanged(interpretation.original_text)
        terms = await cast(_FindMatchingTerms, find_matching_terms)(
            group_id=group_id,
            text=interpretation.original_text,
            include_candidates=False,
        )
    except Exception:
        return _unchanged(interpretation.original_text)

    blocked_surfaces: set[str] = set()
    for term in terms:
        aliases = getattr(term, "aliases", ()) or ()
        names = (getattr(term, "term", ""), *aliases)
        blocked_surfaces.update(_surface_key(str(name)) for name in names)
    kept = tuple(
        item
        for item in interpretation.evidence
        if _surface_key(item.source_text) not in blocked_surfaces
    )
    return _from_evidence(interpretation.original_text, kept)


def _surface_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _from_evidence(
    original_text: str,
    evidence: tuple[HomophoneEvidence, ...],
) -> HomophoneInterpretation:
    if not evidence:
        return _unchanged(original_text)
    interpreted_text = original_text
    for item in reversed(evidence):
        interpreted_text = (
            interpreted_text[:item.start]
            + item.interpreted_text
            + interpreted_text[item.end:]
        )
    return HomophoneInterpretation(
        original_text=original_text,
        interpreted_text=interpreted_text,
        confidence=min(item.confidence for item in evidence),
        evidence=evidence,
    )


def _unchanged(original_text: str) -> HomophoneInterpretation:
    return HomophoneInterpretation(
        original_text=original_text,
        interpreted_text=original_text,
        confidence=0.0,
    )
