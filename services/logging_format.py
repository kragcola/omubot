"""Helpers for dynamic Loguru formats that contain untrusted message text."""

from __future__ import annotations


def escape_loguru_message(message: str) -> str:
    """Escape message text before a colorized dynamic format is parsed."""

    return (
        str(message)
        .replace("\\", "\\\\")
        .replace("<", "\\<")
        .replace("{", "{{")
        .replace("}", "}}")
    )


__all__ = ["escape_loguru_message"]
