"""Canonical effective-admin identity resolution."""

from __future__ import annotations

from typing import Any

_UNSET = object()


def _add_ids(target: set[str], raw: Any) -> None:
    if isinstance(raw, dict):
        values = raw.keys()
    elif isinstance(raw, (set, list, tuple, frozenset)):
        values = raw
    elif raw in (None, ""):
        return
    else:
        values = (raw,)
    target.update(str(value) for value in values if value not in (None, ""))


def effective_admin_ids(
    source: Any = None,
    *,
    driver_config: Any = _UNSET,
) -> set[str]:
    """Merge project admins and NoneBot superusers into one canonical set."""
    values: set[str] = set()
    config = getattr(source, "config", None)
    _add_ids(values, getattr(config, "admins", None))
    _add_ids(values, getattr(source, "admins", None))

    resolved_driver_config = driver_config
    if resolved_driver_config is _UNSET:
        try:
            import nonebot

            resolved_driver_config = nonebot.get_driver().config
        except Exception:
            resolved_driver_config = None
    _add_ids(values, getattr(resolved_driver_config, "superusers", None))
    _add_ids(values, getattr(resolved_driver_config, "SUPERUSERS", None))
    return values


def is_effective_admin(
    user_id: Any,
    source: Any = None,
    *,
    driver_config: Any = _UNSET,
) -> bool:
    """Return whether ``user_id`` belongs to the canonical admin set."""
    if user_id in (None, ""):
        return False
    return str(user_id) in effective_admin_ids(
        source,
        driver_config=driver_config,
    )


__all__ = ["effective_admin_ids", "is_effective_admin"]
