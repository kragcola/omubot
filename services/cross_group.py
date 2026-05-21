"""Cross-group visibility SQL helpers for A2 migration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def cross_group_where(
    *,
    group_id_col: str = "group_id",
    scope_col: str = "scope",
) -> str:
    """SQL fragment: own group OR global OR cross-group visible.

    Returns a parenthesized WHERE clause with one `?` placeholder for the
    requesting group_id.
    """
    return (
        f"({scope_col} = 'global' "
        f"OR ({scope_col} = 'group' AND {group_id_col} = ?) "
        f"OR ({scope_col} = 'group' AND cross_group_visible = 1))"
    )


@dataclass(frozen=True)
class CrossGroupQuery:
    """Composable cross-group visibility filter.

    `where_sql` is a parenthesized SQL fragment with one `?` placeholder for
    the viewer's group_id. `params` is the tuple to splat alongside any other
    parameters when executing the composed query.
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
        params=(viewer_group_id,),
    )
