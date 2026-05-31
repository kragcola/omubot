"""Input-side filter for upstream bot commands / receipts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FilterResult:
    should_drop: bool
    reason: str = ""


def should_drop_message(
    user_id: int,
    message_text: str,
    group_id: str,
    *,
    enabled: bool,
    known_other_bots: dict[str, list[str]],
    command_patterns: list[str],
) -> FilterResult:
    if not enabled:
        return FilterResult(False, "")
    peers = {str(raw).strip() for raw in known_other_bots.get(str(group_id), []) if str(raw).strip()}
    if str(int(user_id)) in peers:
        return FilterResult(True, "peer_bot_message")
    text = str(message_text or "").lstrip()
    for pattern in command_patterns:
        marker = str(pattern or "").strip()
        if marker and text.startswith(marker):
            return FilterResult(True, "upstream_command")
    return FilterResult(False, "")
