"""Fail-closed group outbound access guard for OneBot API calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from kernel.config import GroupAccessConfig, GroupConfig

_CallApi = Callable[..., Awaitable[Any]]
_GENERIC_SEND_ACTIONS = frozenset({"send_msg", "send_forward_msg"})


class OutboundGroupAccessGuard:
    """Apply the configured group access policy at the shared send boundary."""

    def __init__(
        self,
        policy: GroupAccessConfig | GroupConfig,
        *,
        is_group_muted: Callable[[str], bool] | None = None,
    ) -> None:
        self._policy = policy
        self._is_group_muted = is_group_muted
        self._wrapped_bot_ids: set[int] = set()

    def wrap_bot(self, bot: Any) -> bool:
        """Wrap ``bot.call_api`` once so every group send path is checked."""
        if bot is None or not hasattr(bot, "call_api"):
            return False
        bot_id = id(bot)
        if bot_id in self._wrapped_bot_ids or vars(bot).get(
            "_omubot_outbound_group_access_guard_wrapped", False
        ):
            return False

        original: _CallApi = bot.call_api

        async def _guarded_call_api(action: str, **params: Any) -> Any:
            if self._targets_group(str(action), params):
                group_id = self._validated_group_id(params.get("group_id"))
                if group_id is None or not self._allows_group(group_id):
                    if self._log_dropped():
                        logger.warning(
                            "group outbound blocked | action={} group={}",
                            action,
                            self._safe_group_label(params.get("group_id")),
                        )
                    raise PermissionError(
                        f"OneBot group outbound blocked by group policy: action={action}"
                    )
            return await original(action, **params)

        bot.call_api = _guarded_call_api
        bot._omubot_outbound_group_access_guard_wrapped = True
        self._wrapped_bot_ids.add(bot_id)
        return True

    @staticmethod
    def _targets_group(action: str, params: dict[str, Any]) -> bool:
        normalized = action.strip().lower()
        if normalized.startswith("send_group_") or normalized.startswith("_send_group_"):
            return True
        if normalized not in _GENERIC_SEND_ACTIONS:
            return False
        return (
            str(params.get("message_type", "")).strip().lower() == "group"
            or "group_id" in params
        )

    def _allows_group(self, group_id: int) -> bool:
        if isinstance(self._policy, GroupConfig):
            statically_allowed = self._policy.allows_active_group(group_id)
        else:
            statically_allowed = self._policy.allows_group(group_id)
        if not statically_allowed:
            return False
        if self._is_group_muted is None:
            return True
        try:
            return not bool(self._is_group_muted(str(group_id)))
        except Exception:
            logger.exception(
                "group outbound mute check failed; blocking send | group={}",
                group_id,
            )
            return False

    def _log_dropped(self) -> bool:
        if isinstance(self._policy, GroupConfig):
            return self._policy.access.log_dropped
        return self._policy.log_dropped

    @staticmethod
    def _validated_group_id(value: Any) -> int | None:
        if isinstance(value, (bool, dict, list, tuple, set)):
            return None
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            group_id = int(raw)
        except (TypeError, ValueError):
            return None
        return group_id if group_id > 0 else None

    @staticmethod
    def _safe_group_label(value: Any) -> str:
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            return str(value)[:32]
        return "invalid"
