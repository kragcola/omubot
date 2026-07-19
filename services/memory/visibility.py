"""Memory card visibility helpers (distinct from storage scope).

Storage scope (``user`` / ``group`` / ``global``) answers "who owns this card".
Visibility answers "where may automatic recall surface it".

Fail-closed defaults:
- Missing / unknown visibility on a user card → treat as private for
  cross-scope (group) automatic recall.
- Private chat facts never enter group automatic recall.
- same_group facts require matching origin_group_id (or pool membership).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_VALID_VISIBILITIES = frozenset(("private", "same_group", "global"))


def normalize_visibility(value: str | None) -> str | None:
    if value is None:
        return None
    vis = str(value).strip().lower()
    if not vis:
        return None
    if vis not in _VALID_VISIBILITIES:
        return None
    return vis


def default_visibility_for_extraction(*, group_id: str | None) -> str:
    """Write-time default: group conversation → same_group; private → private."""
    return "same_group" if group_id else "private"


def card_visible_in_group_recall(
    card: Any,
    *,
    group_id: str,
    speaker_user_id: str,
    group_pool_ids: Iterable[str] | None = None,
) -> bool:
    """Whether a card may enter automatic group-chat recall for *speaker*.

    Rules:
    - group-scope cards: only when scope_id is the current group or a pool id.
    - global-scope cards: always (existing behaviour).
    - user-scope cards: only the current speaker's cards with visibility that
      explicitly allows this group (same_group + matching origin, or global).
      Missing visibility / private / other origin → excluded (fail closed).
    """
    scope = str(getattr(card, "scope", "") or "")
    scope_id = str(getattr(card, "scope_id", "") or "")
    pools = {str(p) for p in (group_pool_ids or ()) if p}
    pools.add(str(group_id))

    if scope == "global":
        return True
    if scope == "group":
        return scope_id in pools
    if scope != "user":
        return False

    # User-scope: never merge all user cards — speaker + visibility only.
    if scope_id != str(speaker_user_id):
        return False
    subject = getattr(card, "subject_user_id", None)
    if subject is not None and str(subject).strip() and str(subject) != str(speaker_user_id):
        return False

    visibility = normalize_visibility(getattr(card, "visibility", None))
    if visibility is None or visibility == "private":
        return False
    if visibility == "global":
        return True
    # same_group
    origin = getattr(card, "origin_group_id", None)
    if origin is None or not str(origin).strip():
        return False
    return str(origin) in pools


def filter_cards_for_group_recall(
    cards: list[Any],
    *,
    group_id: str,
    speaker_user_id: str,
    group_pool_ids: Iterable[str] | None = None,
) -> list[Any]:
    """Filter *cards* for automatic group recall (stable order preserved)."""
    return [
        c
        for c in cards
        if card_visible_in_group_recall(
            c,
            group_id=group_id,
            speaker_user_id=speaker_user_id,
            group_pool_ids=group_pool_ids,
        )
    ]
