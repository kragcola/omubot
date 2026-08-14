"""Pure URL boundary shared by governed public-HTTP tools."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOSTS = frozenset({"localhost", "host.docker.internal", "napcat"})
_PROXY_DNS_NET = ipaddress.ip_network("198.18.0.0/15")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MAX_PUBLIC_URL_LENGTH = 8192


class UnsafePublicUrl(ValueError):
    """The URL cannot be used by a public-network tool."""


class UnsafePublicResolution(OSError):
    """DNS output violates the public-network boundary."""


@dataclass(frozen=True, slots=True)
class NormalizedPublicUrl:
    url: str
    origin: str
    hostname: str
    port: int


@dataclass(frozen=True, slots=True)
class PublicHttpTextResponse:
    status_code: int
    text: str
    final_url: str
    truncated: bool


def is_allowed_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_proxy_dns_net: bool,
) -> bool:
    if (
        allow_proxy_dns_net
        and isinstance(address, ipaddress.IPv4Address)
        and address in _PROXY_DNS_NET
    ):
        return True
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_allowed_public_address(
                address.ipv4_mapped,
                allow_proxy_dns_net=allow_proxy_dns_net,
            )
        if address.sixtofour is not None or address.teredo is not None:
            return False
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_private
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    return address.is_global


def normalize_public_http_url(
    value: str,
    *,
    allow_proxy_dns_net: bool = True,
) -> NormalizedPublicUrl:
    original = str(value or "")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in original):
        raise UnsafePublicUrl("invalid public HTTP URL")
    if len(original) > MAX_PUBLIC_URL_LENGTH:
        raise UnsafePublicUrl("public HTTP URL is too long")
    raw = original.strip()
    if not raw or "\\" in raw:
        raise UnsafePublicUrl("invalid public HTTP URL")
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        raw_hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise UnsafePublicUrl("invalid public HTTP URL") from exc
    if scheme not in _ALLOWED_SCHEMES or not parsed.netloc or not raw_hostname:
        raise UnsafePublicUrl("invalid public HTTP URL")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafePublicUrl("public HTTP URL must not contain credentials")
    if parsed.netloc.endswith(":") or port == 0:
        raise UnsafePublicUrl("invalid public HTTP port")
    if "%" in raw_hostname:
        raise UnsafePublicUrl("public HTTP URL hostname is ambiguous")

    try:
        address = ipaddress.ip_address(raw_hostname)
    except ValueError:
        try:
            hostname = raw_hostname.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError as exc:
            raise UnsafePublicUrl("invalid public HTTP hostname") from exc
        labels = hostname.split(".")
        if (
            not hostname
            or len(hostname) > 253
            or any(not _DNS_LABEL_RE.fullmatch(label) for label in labels)
        ):
            raise UnsafePublicUrl("invalid public HTTP hostname") from None
        if hostname in _BLOCKED_HOSTS or hostname.endswith(".localhost"):
            raise UnsafePublicUrl("blocked public HTTP hostname") from None
    else:
        if not is_allowed_public_address(
            address,
            allow_proxy_dns_net=False,
        ):
            raise UnsafePublicUrl("blocked public HTTP address")
        hostname = address.compressed

    default_port = 443 if scheme == "https" else 80
    resolved_port = default_port if port is None else port
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    netloc = (
        host_for_netloc
        if resolved_port == default_port
        else f"{host_for_netloc}:{resolved_port}"
    )
    normalized_url = urlunsplit(
        (scheme, netloc, parsed.path or "/", parsed.query, "")
    )
    return NormalizedPublicUrl(
        url=normalized_url,
        origin=f"{scheme}://{netloc}",
        hostname=hostname,
        port=resolved_port,
    )


class PublicAddressResolver(AbstractResolver):
    """Resolve once, validate every answer, then pin those answers for connect."""

    def __init__(
        self,
        *,
        lookup: Callable[..., Awaitable[list[tuple[Any, ...]]]] | None = None,
        allow_proxy_dns_net: bool = True,
    ) -> None:
        self._lookup = lookup
        self._allow_proxy_dns_net = bool(allow_proxy_dns_net)

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        literal_input = literal is not None
        if literal_input:
            addresses = [literal]
        else:
            lookup_family = family if family else socket.AF_UNSPEC
            if self._lookup is None:
                infos = await asyncio.get_running_loop().getaddrinfo(
                    host,
                    port,
                    family=lookup_family,
                    type=socket.SOCK_STREAM,
                )
            else:
                infos = await self._lookup(
                    host,
                    port,
                    family=lookup_family,
                    type=socket.SOCK_STREAM,
                )
            addresses = []
            for info in infos:
                sockaddr = info[4]
                if not isinstance(sockaddr, tuple) or not sockaddr:
                    raise UnsafePublicResolution(
                        "public DNS returned an invalid address"
                    )
                address_text = str(sockaddr[0])
                if "%" in address_text:
                    raise UnsafePublicResolution(
                        "public DNS returned a scoped address"
                    )
                try:
                    addresses.append(ipaddress.ip_address(address_text))
                except ValueError as exc:
                    raise UnsafePublicResolution(
                        "public DNS returned an invalid address"
                    ) from exc

        if not addresses:
            raise UnsafePublicResolution("public DNS returned no addresses")
        if any(
            not is_allowed_public_address(
                address,
                allow_proxy_dns_net=(
                    self._allow_proxy_dns_net and not literal_input
                ),
            )
            for address in addresses
        ):
            raise UnsafePublicResolution(
                "public DNS returned a non-public address"
            )

        results: list[ResolveResult] = []
        seen: set[str] = set()
        for address in addresses:
            text = address.compressed
            if text in seen:
                continue
            seen.add(text)
            results.append(
                {
                    "hostname": host,
                    "host": text,
                    "port": port,
                    "family": (
                        socket.AF_INET
                        if isinstance(address, ipaddress.IPv4Address)
                        else socket.AF_INET6
                    ),
                    "proto": socket.IPPROTO_TCP,
                    "flags": 0,
                }
            )
        return results

    async def close(self) -> None:
        return None


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


async def fetch_public_text(
    url: str,
    *,
    timeout_seconds: float,
    follow_redirects: bool,
    headers: dict[str, str],
    max_bytes: int,
    max_redirects: int = 5,
    allow_proxy_dns_net: bool = True,
) -> PublicHttpTextResponse:
    if max_bytes <= 0 or max_redirects < 0:
        raise ValueError("public HTTP limits are invalid")
    current = normalize_public_http_url(
        url,
        allow_proxy_dns_net=allow_proxy_dns_net,
    )
    allowed_origin = current.origin
    resolver = PublicAddressResolver(
        allow_proxy_dns_net=allow_proxy_dns_net,
    )
    connector = aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=False,
        family=socket.AF_UNSPEC,
    )
    timeout = aiohttp.ClientTimeout(total=max(1.0, float(timeout_seconds)))
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
        ) as session:
            redirect_count = 0
            while True:
                async with session.get(
                    current.url,
                    headers=headers,
                    allow_redirects=False,
                ) as response:
                    location = response.headers.get("Location")
                    if (
                        follow_redirects
                        and response.status in _REDIRECT_STATUSES
                        and location
                    ):
                        if redirect_count >= max_redirects:
                            raise UnsafePublicUrl(
                                "public HTTP redirect limit exceeded"
                            )
                        candidate = normalize_public_http_url(
                            urljoin(current.url, location),
                            allow_proxy_dns_net=allow_proxy_dns_net,
                        )
                        if candidate.origin != allowed_origin:
                            raise UnsafePublicUrl(
                                "public HTTP redirect crossed the authorized origin"
                            )
                        current = candidate
                        redirect_count += 1
                        continue

                    chunks: list[bytes] = []
                    remaining = max_bytes + 1
                    while remaining > 0:
                        chunk = await response.content.read(remaining)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    payload = b"".join(chunks)
                    truncated = len(payload) > max_bytes
                    payload = payload[:max_bytes]
                    charset = response.charset or "utf-8"
                    try:
                        text = payload.decode(charset, errors="replace")
                    except LookupError:
                        text = payload.decode("utf-8", errors="replace")
                    return PublicHttpTextResponse(
                        status_code=int(response.status),
                        text=text,
                        final_url=current.url,
                        truncated=truncated,
                    )
    except aiohttp.ClientConnectorError as exc:
        if isinstance(exc.os_error, UnsafePublicResolution):
            raise UnsafePublicUrl(
                "public HTTP DNS resolution was not public"
            ) from exc
        raise


__all__ = [
    "MAX_PUBLIC_URL_LENGTH",
    "NormalizedPublicUrl",
    "PublicAddressResolver",
    "PublicHttpTextResponse",
    "UnsafePublicResolution",
    "UnsafePublicUrl",
    "fetch_public_text",
    "is_allowed_public_address",
    "normalize_public_http_url",
]
