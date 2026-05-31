"""Cross-group visibility helpers for the bool -> enum migration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CrossGroupVisibility = Literal["none", "opt_out", "opt_in"]

_VISIBILITY_NONE = 0
_VISIBILITY_OPT_OUT = 1
_VISIBILITY_OPT_IN = 2
_VALID_VISIBILITIES: frozenset[str] = frozenset(("none", "opt_out", "opt_in"))


def visibility_from_db(value: Any) -> CrossGroupVisibility:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        raw = _VISIBILITY_NONE
    if raw == _VISIBILITY_OPT_IN:
        return "opt_in"
    if raw == _VISIBILITY_OPT_OUT:
        return "opt_out"
    return "none"


def visibility_to_db(visibility: CrossGroupVisibility) -> int:
    if visibility == "opt_in":
        return _VISIBILITY_OPT_IN
    if visibility == "opt_out":
        return _VISIBILITY_OPT_OUT
    return _VISIBILITY_NONE


def legacy_cross_group_visible(visibility: CrossGroupVisibility) -> bool:
    return visibility != "none"


def resolve_cross_group_visibility(
    *,
    visible: bool | None = None,
    visibility: CrossGroupVisibility | None = None,
) -> CrossGroupVisibility:
    if visibility is not None:
        normalized = str(visibility or "").strip().lower()
        if normalized not in _VALID_VISIBILITIES:
            raise ValueError(f"invalid cross_group_visibility: {visibility!r}")
        return normalized  # type: ignore[return-value]
    return "opt_out" if bool(visible) else "none"


def cross_group_allows_viewer(
    *,
    viewer_group_id: str,
    owner_group_id: str,
    scope: str,
    visibility: CrossGroupVisibility,
    enabled_for_groups: list[str] | tuple[str, ...] = (),
) -> bool:
    viewer = str(viewer_group_id or "").strip()
    owner = str(owner_group_id or "").strip()
    normalized_scope = str(scope or "group").strip().lower()
    if normalized_scope == "global":
        return True
    if normalized_scope != "group":
        return False
    if viewer and viewer == owner:
        return True
    if visibility == "opt_out":
        return True
    if visibility != "opt_in" or not viewer:
        return False
    allowed = {str(group_id).strip() for group_id in enabled_for_groups if str(group_id).strip()}
    return viewer in allowed


def cross_group_where(
    *,
    group_id_col: str = "group_id",
    scope_col: str = "scope",
    visibility_col: str = "cross_group_visible",
    enabled_groups_col: str = "cross_group_enabled_for_groups",
) -> str:
    """SQL fragment: own group OR global OR enum-based cross-group visibility.

    Returns a parenthesized WHERE clause with two `?` placeholders for the
    requesting group_id: one for same-group access, one for opt-in group lists.
    """
    return (
        f"({scope_col} = 'global' "
        f"OR ({scope_col} = 'group' AND {group_id_col} = ?) "
        f"OR ({scope_col} = 'group' AND {visibility_col} = {_VISIBILITY_OPT_OUT}) "
        f"OR ({scope_col} = 'group' AND {visibility_col} = {_VISIBILITY_OPT_IN} "
        f"AND instr(COALESCE({enabled_groups_col}, '[]'), '\"' || ? || '\"') > 0))"
    )


@dataclass(frozen=True)
class CrossGroupQuery:
    """Composable cross-group visibility filter.

    `where_sql` is a parenthesized SQL fragment with two `?` placeholders for
    the viewer's group_id. `params` repeats the viewer group_id so callers can
    keep using positional SQL parameters.
    """

    where_sql: str
    params: tuple[Any, ...]


def apply_cross_group_filter(
    *,
    viewer_group_id: str,
    group_id_col: str = "group_id",
    scope_col: str = "scope",
) -> CrossGroupQuery:
    """§10 protocol: build a viewer-aware cross-group WHERE fragment.

    Caller pattern:
        cgq = apply_cross_group_filter(viewer_group_id=g)
        sql = f"SELECT ... WHERE status='approved' AND {cgq.where_sql}"
        await db.execute(sql, [*cgq.params, ...])
    """
    return CrossGroupQuery(
        where_sql=cross_group_where(group_id_col=group_id_col, scope_col=scope_col),
        params=(viewer_group_id, viewer_group_id),
    )
