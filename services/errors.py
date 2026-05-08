"""Runtime warning/error aggregation for Admin diagnostics."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from typing import Any


class RuntimeErrorStore:
    """In-memory rolling aggregation of warning/error log records."""

    def __init__(self, max_events: int = 200, max_groups: int = 80) -> None:
        self._max_events = max(20, int(max_events))
        self._max_groups = max(10, int(max_groups))
        self._events: deque[dict[str, Any]] = deque(maxlen=self._max_events)
        self._groups: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._lock = threading.RLock()

    def record(
        self,
        *,
        level: str,
        message: str,
        channel: str = "",
        logger_name: str = "",
        ts: float | None = None,
    ) -> None:
        level_name = str(level or "INFO").upper()
        if level_name not in {"WARNING", "ERROR", "CRITICAL"}:
            return
        now = float(ts or time.time())
        clean_message = self._clean_message(message)
        clean_channel = str(channel or "")
        signature = self._signature(level_name, clean_channel, clean_message)

        with self._lock:
            self._counter += 1
            event = {
                "event_id": f"err_{int(now * 1000):x}_{self._counter:x}",
                "signature": signature,
                "level": level_name,
                "channel": clean_channel,
                "logger": str(logger_name or ""),
                "message": clean_message,
                "occurred_at": now,
            }
            self._events.appendleft(event)

            group = self._groups.get(signature)
            if group is None:
                group = {
                    "signature": signature,
                    "level": level_name,
                    "channel": clean_channel,
                    "logger": str(logger_name or ""),
                    "message": clean_message,
                    "count": 0,
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
                self._groups[signature] = group
            group["count"] = int(group.get("count", 0) or 0) + 1
            group["last_seen_at"] = now
            group["level"] = self._max_level(str(group.get("level", level_name)), level_name)
            group["channel"] = clean_channel or str(group.get("channel", ""))
            group["logger"] = str(logger_name or group.get("logger", ""))
            group["message"] = clean_message
            self._trim_groups()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[: max(1, min(int(limit), self._max_events))]

    def groups(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(
                self._groups.values(),
                key=lambda item: float(item.get("last_seen_at", 0.0) or 0.0),
                reverse=True,
            )
            return [dict(item) for item in items[: max(1, min(int(limit), self._max_groups))]]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
            unique_count = len(self._groups)
        warnings = sum(1 for item in events if item.get("level") == "WARNING")
        errors = sum(1 for item in events if item.get("level") in {"ERROR", "CRITICAL"})
        critical = sum(1 for item in events if item.get("level") == "CRITICAL")
        groups = self.groups(limit=1)
        last_error = next((item for item in events if item.get("level") in {"ERROR", "CRITICAL"}), None)
        last_warning = next((item for item in events if item.get("level") == "WARNING"), None)
        return {
            "total": len(events),
            "warnings": warnings,
            "errors": errors,
            "critical": critical,
            "unique": unique_count,
            "last_error": last_error or {},
            "last_warning": last_warning or {},
            "top_issue": groups[0] if groups else {},
        }

    def as_payload(self, *, event_limit: int = 20, group_limit: int = 12) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "groups": self.groups(limit=group_limit),
            "events": self.recent(limit=event_limit),
            "max_events": self._max_events,
            "max_groups": self._max_groups,
        }

    def _trim_groups(self) -> None:
        if len(self._groups) <= self._max_groups:
            return
        ordered = sorted(
            self._groups.items(),
            key=lambda item: float(item[1].get("last_seen_at", 0.0) or 0.0),
            reverse=True,
        )
        self._groups = dict(ordered[: self._max_groups])

    @staticmethod
    def _clean_message(message: str) -> str:
        text = " ".join(str(message or "").split())
        return text[:240] + ("..." if len(text) > 240 else "")

    @staticmethod
    def _signature(level: str, channel: str, message: str) -> str:
        digest = hashlib.sha1(f"{level}|{channel}|{message}".encode()).hexdigest()
        return digest[:16]

    @staticmethod
    def _max_level(left: str, right: str) -> str:
        priority = {"WARNING": 1, "ERROR": 2, "CRITICAL": 3}
        return left if priority.get(left, 0) >= priority.get(right, 0) else right
