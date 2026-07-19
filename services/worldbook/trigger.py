"""Deterministic World Info trigger and retrieval."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from services.worldbook.domain import CanonEntry

SemanticScorer = Callable[[str, CanonEntry], float]


@dataclass(frozen=True, slots=True)
class TriggerHit:
    entry: CanonEntry
    reasons: tuple[str, ...]
    score: float

    @property
    def hit_reason(self) -> str:
        return "+".join(self.reasons) if self.reasons else "none"


def _compile_regex(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


class WorldInfoTrigger:
    """Keyword / alias / regex / cascade / optional semantic activation.

    Ordering is stable: ``(-score, -priority, entry_id)``.
    """

    def __init__(
        self,
        entries: Sequence[CanonEntry] | None = None,
        *,
        semantic_scorer: SemanticScorer | None = None,
        semantic_threshold: float = 0.55,
        cascade_depth: int = 2,
    ) -> None:
        self._entries = list(entries or [])
        self._semantic_scorer = semantic_scorer
        self._semantic_threshold = float(semantic_threshold)
        self._cascade_depth = max(0, int(cascade_depth))
        self._by_entity: dict[str, list[CanonEntry]] = {}
        for entry in self._entries:
            for entity in entry.entity_ids:
                self._by_entity.setdefault(entity, []).append(entry)

    def set_entries(self, entries: Sequence[CanonEntry]) -> None:
        self._entries = list(entries)
        self._by_entity = {}
        for entry in self._entries:
            for entity in entry.entity_ids:
                self._by_entity.setdefault(entity, []).append(entry)

    def activate(self, query: str) -> list[TriggerHit]:
        """Activate canon via keyword/alias/regex/semantic only.

        ``always_active`` is ignored for projection entry: Canon must enter
        through an explicit activation signal (or entity cascade).
        """
        text = str(query or "")
        if not text:
            return []
        hits: dict[str, TriggerHit] = {}

        for entry in self._entries:
            reasons: list[str] = []
            score = 0.0
            # always_active is intentionally not a projection activation path.
            lowered = text.casefold()
            for kw in entry.keywords:
                if kw and kw.casefold() in lowered:
                    reasons.append(f"keyword:{kw}")
                    score += 1.0
            for alias in entry.aliases:
                if alias and alias.casefold() in lowered:
                    reasons.append(f"alias:{alias}")
                    score += 1.2
            for pattern in entry.regexes:
                compiled = _compile_regex(pattern)
                if compiled is not None and compiled.search(text):
                    reasons.append(f"regex:{pattern}")
                    score += 1.5
            if self._semantic_scorer is not None and text:
                try:
                    semantic = float(self._semantic_scorer(text, entry))
                except Exception:
                    semantic = 0.0
                if semantic >= self._semantic_threshold:
                    reasons.append(f"semantic:{semantic:.3f}")
                    score += semantic
            if reasons:
                hits[entry.entry_id] = TriggerHit(
                    entry=entry,
                    reasons=tuple(reasons),
                    score=score + (entry.priority / 1000.0),
                )

        # Cascading entity activation: related entities of hit entries.
        activated_entities: set[str] = set()
        for hit in list(hits.values()):
            activated_entities.update(hit.entry.entity_ids)
            activated_entities.update(hit.entry.related_entities)

        depth = 0
        frontier = set(activated_entities)
        while frontier and depth < self._cascade_depth:
            next_frontier: set[str] = set()
            for entity in sorted(frontier):
                for entry in self._by_entity.get(entity, []):
                    if entry.entry_id in hits:
                        continue
                    # Only cascade when entity is related from an already-hit entry.
                    if not any(
                        entity in h.entry.related_entities
                        or entity in h.entry.entity_ids
                        for h in hits.values()
                    ):
                        continue
                    cascade_reasons = (f"cascade:{entity}",)
                    hits[entry.entry_id] = TriggerHit(
                        entry=entry,
                        reasons=cascade_reasons,
                        score=0.5 + (entry.priority / 1000.0),
                    )
                    next_frontier.update(entry.related_entities)
            frontier = next_frontier - activated_entities
            activated_entities |= next_frontier
            depth += 1

        return sorted(
            hits.values(),
            key=lambda h: (-h.score, -h.entry.priority, h.entry.entry_id),
        )


def budget_atomic_blocks(
    hits: Sequence[TriggerHit],
    *,
    budget_chars: int,
) -> tuple[list[TriggerHit], list[tuple[str, str]]]:
    """Accept whole atomic canon blocks until budget; never truncate mid-fact.

    Returns ``(accepted, decisions)`` where decisions are
    ``(entry_id, accepted|rejected:budget)``.
    """
    remaining = max(0, int(budget_chars))
    accepted: list[TriggerHit] = []
    decisions: list[tuple[str, str]] = []
    for hit in hits:
        size = len(hit.entry.text)
        if size <= remaining:
            accepted.append(hit)
            remaining -= size
            decisions.append((hit.entry.entry_id, "accepted"))
        else:
            decisions.append((hit.entry.entry_id, "rejected:budget"))
    return accepted, decisions


def optional_semantic_passthrough(
    scorer: Any | None,
) -> SemanticScorer | None:
    if scorer is None:
        return None
    if callable(scorer):
        return scorer  # type: ignore[return-value]
    return None
