"""Local OneBot request tracing for protocol diagnostics."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any


class ProtocolTraceStore:
    """In-memory rolling trace of OneBot API calls.

    The trace id is local to Omubot. It does not alter OneBot payloads, which
    keeps protocol behavior stable while still giving Admin an echo-like handle
    for each request.
    """

    def __init__(self, max_items: int = 80) -> None:
        self._max_items = max(10, int(max_items))
        self._items: deque[dict[str, Any]] = deque(maxlen=self._max_items)
        self._counter = 0
        self._wrapped_bot_ids: set[int] = set()

    def wrap_bot(self, bot: Any) -> bool:
        """Wrap bot.call_api once. Returns True when a new wrapper is installed."""
        if bot is None or not hasattr(bot, "call_api"):
            return False
        bot_id = id(bot)
        if bot_id in self._wrapped_bot_ids or getattr(bot, "_omubot_protocol_trace_wrapped", False):
            return False

        original = bot.call_api

        async def _traced_call_api(action: str, **params: Any) -> Any:
            trace_id = self.start(action, params=params, self_id=str(getattr(bot, "self_id", "")))
            try:
                result = await original(action, **params)
                self.finish(trace_id, ok=True)
                return result
            except Exception as exc:
                self.finish(trace_id, ok=False, error=str(exc))
                raise

        bot.call_api = _traced_call_api  # type: ignore[method-assign]
        bot._omubot_protocol_trace_wrapped = True  # type: ignore[attr-defined]
        self._wrapped_bot_ids.add(bot_id)
        return True

    def start(self, action: str, *, params: dict[str, Any] | None = None, self_id: str = "") -> str:
        self._counter += 1
        trace_id = f"ob_{int(time.time() * 1000):x}_{self._counter:x}"
        now = time.time()
        self._items.appendleft({
            "trace_id": trace_id,
            "action": str(action or "unknown"),
            "status": "pending",
            "ok": None,
            "started_at": now,
            "finished_at": 0.0,
            "elapsed_ms": 0.0,
            "self_id": self_id,
            "params": self._safe_params(params or {}),
            "error": "",
        })
        return trace_id

    def finish(self, trace_id: str, *, ok: bool, error: str = "") -> None:
        now = time.time()
        for item in self._items:
            if item.get("trace_id") != trace_id:
                continue
            started_at = float(item.get("started_at", now) or now)
            item["status"] = "ok" if ok else "failed"
            item["ok"] = ok
            item["finished_at"] = now
            item["elapsed_ms"] = round((now - started_at) * 1000, 2)
            item["error"] = str(error or "")[:240]
            return

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        return list(self._items)[: max(1, min(int(limit), self._max_items))]

    def summary(self) -> dict[str, Any]:
        items = list(self._items)
        done = [item for item in items if item.get("status") != "pending"]
        failed = [item for item in items if item.get("status") == "failed"]
        pending = [item for item in items if item.get("status") == "pending"]
        ok = [item for item in items if item.get("status") == "ok"]
        avg_ms = 0.0
        if done:
            avg_ms = round(sum(float(item.get("elapsed_ms", 0.0) or 0.0) for item in done) / len(done), 2)
        return {
            "total": len(items),
            "ok": len(ok),
            "failed": len(failed),
            "pending": len(pending),
            "avg_elapsed_ms": avg_ms,
            "wrapped_bots": len(self._wrapped_bot_ids),
            "last_error": failed[0]["error"] if failed else "",
        }

    def as_payload(self, limit: int = 30) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "traces": self.recent(limit=limit),
            "max_items": self._max_items,
        }

    @staticmethod
    def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in params.items():
            key_str = str(key)
            if any(secret in key_str.lower() for secret in ("token", "secret", "password", "key")):
                redacted[key_str] = "***"
            elif isinstance(value, bytes):
                redacted[key_str] = f"<bytes:{len(value)}>"
            else:
                text = str(value)
                redacted[key_str] = text[:160] + ("..." if len(text) > 160 else "")
        return redacted


class ProtocolConnectionHistory:
    """In-memory rolling history of protocol connection state changes."""

    def __init__(self, max_items: int = 40) -> None:
        self._max_items = max(10, int(max_items))
        self._events: deque[dict[str, Any]] = deque(maxlen=self._max_items)
        self._current_status = "unknown"
        self._connected_bots = 0
        self._self_ids: list[str] = []
        self._changed_at = 0.0
        self._last_seen_at = 0.0
        self._disconnected_since = 0.0
        self._last_recovery_seconds: float | None = None
        self._last_error = ""
        self._counter = 0

    def record_snapshot(
        self,
        *,
        connected_bots: int,
        self_ids: list[str] | tuple[str, ...] | None = None,
        source: str = "snapshot",
        error: str = "",
    ) -> dict[str, Any]:
        """Record a safe connection snapshot and append an event on changes/errors."""
        now = time.time()
        connected_count = max(0, int(connected_bots or 0))
        status = "connected" if connected_count > 0 else "disconnected"
        ids = sorted({str(item) for item in (self_ids or []) if str(item)})
        previous_status = self._current_status
        changed = status != previous_status
        recovery_seconds: float | None = None

        if status == "connected":
            if previous_status == "disconnected" and self._disconnected_since:
                recovery_seconds = round(now - self._disconnected_since, 2)
                self._last_recovery_seconds = recovery_seconds
            self._disconnected_since = 0.0
        elif changed:
            self._disconnected_since = now

        if changed or not self._changed_at:
            self._changed_at = now

        self._current_status = status
        self._connected_bots = connected_count
        self._self_ids = ids
        self._last_seen_at = now

        clean_error = str(error or "")[:240]
        if clean_error:
            self._last_error = clean_error

        if changed or clean_error:
            self._append_event(
                kind="state" if changed else "error",
                status=status,
                previous_status=previous_status,
                connected_bots=connected_count,
                self_ids=ids,
                source=source,
                error=clean_error,
                recovery_seconds=recovery_seconds,
                occurred_at=now,
            )
        return self.summary()

    def record_connected(self, bot: Any, *, source: str = "on_bot_connect") -> dict[str, Any]:
        self_id = str(getattr(bot, "self_id", "") or "")
        return self.record_snapshot(connected_bots=1, self_ids=[self_id] if self_id else [], source=source)

    def record_disconnected(
        self,
        bot: Any = None,
        *,
        source: str = "on_bot_disconnect",
        error: str = "",
    ) -> dict[str, Any]:
        self_id = str(getattr(bot, "self_id", "") or "")
        return self.record_snapshot(
            connected_bots=0,
            self_ids=[self_id] if self_id else self._self_ids,
            source=source,
            error=error,
        )

    def record_error(self, error: str, *, source: str = "protocol") -> dict[str, Any]:
        return self.record_snapshot(
            connected_bots=self._connected_bots,
            self_ids=self._self_ids,
            source=source,
            error=error,
        )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._events)[: max(1, min(int(limit), self._max_items))]

    def summary(self) -> dict[str, Any]:
        return {
            "current_status": self._current_status,
            "connected_bots": self._connected_bots,
            "self_ids": list(self._self_ids),
            "changed_at": self._changed_at,
            "last_seen_at": self._last_seen_at,
            "disconnected_since": self._disconnected_since,
            "last_recovery_seconds": self._last_recovery_seconds,
            "last_error": self._last_error,
            "event_count": len(self._events),
        }

    def as_payload(self, limit: int = 20) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "events": self.recent(limit=limit),
            "max_items": self._max_items,
        }

    def _append_event(
        self,
        *,
        kind: str,
        status: str,
        previous_status: str,
        connected_bots: int,
        self_ids: list[str],
        source: str,
        error: str,
        recovery_seconds: float | None,
        occurred_at: float,
    ) -> None:
        self._counter += 1
        self._events.appendleft({
            "event_id": f"pc_{int(occurred_at * 1000):x}_{self._counter:x}",
            "kind": kind,
            "status": status,
            "previous_status": previous_status,
            "connected_bots": connected_bots,
            "self_ids": self_ids,
            "source": str(source or "unknown"),
            "error": error,
            "recovery_seconds": recovery_seconds,
            "occurred_at": occurred_at,
        })


ApiCall = Callable[..., Awaitable[Any]]
