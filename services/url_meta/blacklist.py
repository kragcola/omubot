"""URL metadata fetch denylist."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse

_BLOCKED_LABELS = {"admin", "auth", "bank", "banking", "finance", "login", "pay", "payment", "wallet"}
_PRIVATE_SUFFIXES = (".corp", ".home.arpa", ".internal", ".lan", ".local")


def is_blocked_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return True
    if host == "localhost" or host.endswith(_PRIVATE_SUFFIXES):
        return True
    try:
        ip = ip_address(host)
    except ValueError:
        labels = set(host.split("."))
        return bool(labels & _BLOCKED_LABELS)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
