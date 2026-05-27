"""Inbound/outbound loop guard for bot-to-bot conversations."""

from __future__ import annotations

import time
from collections import deque


class BotPairLoopGuard:
    def __init__(
        self,
        *,
        self_id: str = "",
        known_other_bots: dict[str, list[str]] | None = None,
        max_per_minute: int = 3,
        cooldown_seconds: int = 60,
    ) -> None:
        self._self_id = str(self_id or "").strip()
        self._known_other_bots = {
            str(group_id).strip(): {str(peer_id).strip() for peer_id in peer_ids if str(peer_id).strip()}
            for group_id, peer_ids in (known_other_bots or {}).items()
            if str(group_id).strip()
        }
        self._max_per_minute = max(1, int(max_per_minute))
        self._cooldown_seconds = max(1, int(cooldown_seconds))
        self._events: dict[tuple[str, tuple[str, str]], deque[float]] = {}
        self._cooldowns: dict[tuple[str, tuple[str, str]], float] = {}

    def bind_self_id(self, self_id: str) -> None:
        self._self_id = str(self_id or "").strip()

    def is_known_peer(self, group_id: str, peer_id: str) -> bool:
        if not self._self_id:
            return False
        gid = str(group_id or "").strip()
        pid = str(peer_id or "").strip()
        if not gid or not pid or pid == self._self_id:
            return False
        return pid in self._known_other_bots.get(gid, set())

    def is_suppressed(self, group_id: str, peer_id: str, *, now: float | None = None) -> bool:
        key = self._pair_key(group_id, peer_id)
        if key is None:
            return False
        self._prune(key, now=now)
        until = self._cooldowns.get(key, 0.0)
        if until <= (now if now is not None else time.monotonic()):
            self._cooldowns.pop(key, None)
            return False
        return True

    def record_inbound(self, group_id: str, sender_id: str, *, now: float | None = None) -> bool:
        return self._record(group_id, sender_id, now=now)

    def record_outbound(self, group_id: str, target_id: str, *, now: float | None = None) -> bool:
        return self._record(group_id, target_id, now=now)

    def _record(self, group_id: str, peer_id: str, *, now: float | None = None) -> bool:
        key = self._pair_key(group_id, peer_id)
        if key is None:
            return False
        moment = float(now if now is not None else time.monotonic())
        self._prune(key, now=moment)
        history = self._events.setdefault(key, deque())
        history.append(moment)
        if len(history) > self._max_per_minute:
            self._cooldowns[key] = moment + self._cooldown_seconds
        return True

    def _pair_key(self, group_id: str, peer_id: str) -> tuple[str, tuple[str, str]] | None:
        gid = str(group_id or "").strip()
        pid = str(peer_id or "").strip()
        if not gid or not self.is_known_peer(gid, pid):
            return None
        left, right = sorted((self._self_id, pid))
        pair = (left, right)
        return gid, pair

    def _prune(self, key: tuple[str, tuple[str, str]], *, now: float | None = None) -> None:
        moment = float(now if now is not None else time.monotonic())
        history = self._events.get(key)
        if history is None:
            return
        cutoff = moment - 60.0
        while history and history[0] < cutoff:
            history.popleft()
        if not history:
            self._events.pop(key, None)
        until = self._cooldowns.get(key, 0.0)
        if until and until <= moment:
            self._cooldowns.pop(key, None)
