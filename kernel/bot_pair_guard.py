"""Inbound/outbound loop guard for bot-to-bot conversations.

S1 (2026-05-31): the guard no longer requires the peer to be a *known* bot.
The previous design only built a pair key when ``is_known_peer`` was true, so
an unregistered peer bot was never counted and never suppressed — the root
cause of the recurring bot↔bot @ loop. Now any peer is tracked per-pair over a
60s sliding window, and suppression keys off **direction alternations** (a
back-and-forth ping-pong: out→in→out→…), not raw message count. This cleanly
separates "bot↔bot 对刷" (dense alternation) from "用户连发" (same-direction,
zero alternation), so a human burst never trips it. ``known_other_bots`` is
kept only as a *stricter-threshold* hint, no longer a gating precondition.
"""

from __future__ import annotations

import time
from collections import deque

# direction markers stored alongside each event timestamp
_DIR_IN = "in"  # peer → me
_DIR_OUT = "out"  # me → peer


class BotPairLoopGuard:
    def __init__(
        self,
        *,
        self_id: str = "",
        known_other_bots: dict[str, list[str]] | None = None,
        max_per_minute: int = 3,
        cooldown_seconds: int = 60,
        loop_alt_threshold: int = 10,
        known_peer_alt_threshold: int = 6,
    ) -> None:
        self._self_id = str(self_id or "").strip()
        self._known_other_bots = {
            str(group_id).strip(): {str(peer_id).strip() for peer_id in peer_ids if str(peer_id).strip()}
            for group_id, peer_ids in (known_other_bots or {}).items()
            if str(group_id).strip()
        }
        # max_per_minute kept for backward-compat config; alternation thresholds drive S1.
        self._max_per_minute = max(1, int(max_per_minute))
        self._cooldown_seconds = max(1, int(cooldown_seconds))
        self._loop_alt_threshold = max(2, int(loop_alt_threshold))
        self._known_peer_alt_threshold = max(2, int(known_peer_alt_threshold))
        # per-pair sliding window of (timestamp, direction)
        self._events: dict[tuple[str, tuple[str, str]], deque[tuple[float, str]]] = {}
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
        return self._record(group_id, sender_id, _DIR_IN, now=now)

    def record_outbound(self, group_id: str, target_id: str, *, now: float | None = None) -> bool:
        return self._record(group_id, target_id, _DIR_OUT, now=now)

    def _record(self, group_id: str, peer_id: str, direction: str, *, now: float | None = None) -> bool:
        key = self._pair_key(group_id, peer_id)
        if key is None:
            return False
        moment = float(now if now is not None else time.monotonic())
        self._prune(key, now=moment)
        history = self._events.setdefault(key, deque())
        history.append((moment, direction))
        # Suppress on a back-and-forth ping-pong: count direction flips in the
        # window. A human burst (same direction repeated) yields 0 flips.
        threshold = (
            self._known_peer_alt_threshold
            if self.is_known_peer(group_id, peer_id)
            else self._loop_alt_threshold
        )
        if self._count_alternations(history) >= threshold:
            self._cooldowns[key] = moment + self._cooldown_seconds
        return True

    @staticmethod
    def _count_alternations(history: deque[tuple[float, str]]) -> int:
        alt = 0
        prev: str | None = None
        for _, direction in history:
            if prev is not None and direction != prev:
                alt += 1
            prev = direction
        return alt

    def _pair_key(self, group_id: str, peer_id: str) -> tuple[str, tuple[str, str]] | None:
        gid = str(group_id or "").strip()
        pid = str(peer_id or "").strip()
        # S1: any peer is tracked; only self and empties are excluded. Not gated
        # on is_known_peer anymore (that was the bot-loop blind spot).
        if not gid or not self._self_id or not pid or pid == self._self_id:
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
        while history and history[0][0] < cutoff:
            history.popleft()
        if not history:
            self._events.pop(key, None)
        until = self._cooldowns.get(key, 0.0)
        if until and until <= moment:
            self._cooldowns.pop(key, None)
