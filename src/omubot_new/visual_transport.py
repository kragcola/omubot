"""Bounded current-turn image transport for N5 V2.

URL fetching is unavailable unless a caller supplies both an allowlisted host
and explicit resolver/transport ports. The transport must pin the socket to
the resolver result; ordinary ``httpx`` clients are intentionally not accepted
because their internal DNS lookup cannot be proven to use the checked address.
The byte path is local and request-scoped.
"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import itertools
import math
import warnings
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Final, Literal, Protocol, cast
from urllib.parse import SplitResult, urlsplit

from PIL import Image, UnidentifiedImageError

from .types import OperationError, StickerMediaType, VisualOwner

MAX_IMAGE_BYTES: Final = 8 * 1024 * 1024
MAX_IMAGES: Final = 2
MAX_IMAGE_PIXELS: Final = 20_000_000
MAX_IMAGE_DIMENSION: Final = 8_000
MAX_FETCH_CONCURRENCY: Final = 2
MAX_CLOSE_WAIT: Final = 0.5
SUPPORTED_MEDIA_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/webp"})
SUPPORTED_ASSET_MEDIA_TYPES: Final = SUPPORTED_MEDIA_TYPES | {"image/gif"}
type MediaType = Literal["image/jpeg", "image/png", "image/webp"]
type Body = bytes | AsyncIterable[bytes]
type CloseCallback = Callable[[], Awaitable[object] | object]


@dataclass(frozen=True, slots=True)
class VisualLimits:
    """Hard per-request limits. Callers may lower them but never raise them."""

    max_images: int = MAX_IMAGES
    max_bytes: int = MAX_IMAGE_BYTES
    max_pixels: int = MAX_IMAGE_PIXELS
    max_dimension: int = MAX_IMAGE_DIMENSION
    max_concurrency: int = MAX_FETCH_CONCURRENCY
    connect_timeout: float = 2.0
    read_timeout: float = 8.0
    total_timeout: float = 10.0

    def __post_init__(self) -> None:
        bounds = (
            ("max_images", self.max_images, 1, MAX_IMAGES),
            ("max_bytes", self.max_bytes, 1, MAX_IMAGE_BYTES),
            ("max_pixels", self.max_pixels, 1, MAX_IMAGE_PIXELS),
            ("max_dimension", self.max_dimension, 1, MAX_IMAGE_DIMENSION),
            ("max_concurrency", self.max_concurrency, 1, MAX_FETCH_CONCURRENCY),
        )
        for name, value, minimum, maximum in bounds:
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} exceeds the hard budget")
        for name, value, maximum in (
            ("connect_timeout", self.connect_timeout, 2.0),
            ("read_timeout", self.read_timeout, 8.0),
            ("total_timeout", self.total_timeout, 10.0),
        ):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"{name} exceeds the hard budget")


@dataclass(frozen=True, slots=True)
class VisualUrlCandidate:
    """Request-local URL hint bound to the current event owner."""

    owner: VisualOwner
    url: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.owner) is not VisualOwner:
            raise TypeError("owner must be a VisualOwner")
        if type(self.url) is not str or not self.url:
            raise ValueError("image URL must be a non-empty string")
        if self.url != self.url.strip():
            raise ValueError("image URL must not contain surrounding whitespace")


@dataclass(frozen=True, slots=True)
class ImageBytes:
    """Validated, request-local image bytes with decoded dimensions."""

    owner: VisualOwner
    media_type: MediaType
    data: bytes = field(repr=False)
    width: int
    height: int

    def __post_init__(self) -> None:
        if type(self.owner) is not VisualOwner:
            raise TypeError("owner must be a VisualOwner")
        if self.media_type not in SUPPORTED_MEDIA_TYPES:
            raise ValueError("unsupported image media type")
        if type(self.data) is not bytes or not self.data or len(self.data) > MAX_IMAGE_BYTES:
            raise ValueError("image data exceeds the byte budget")
        if type(self.width) is not int or type(self.height) is not int:
            raise TypeError("image dimensions must be integers")
        if not 1 <= self.width <= MAX_IMAGE_DIMENSION or not 1 <= self.height <= MAX_IMAGE_DIMENSION:
            raise ValueError("image dimensions exceed the hard budget")
        if self.width * self.height > MAX_IMAGE_PIXELS:
            raise ValueError("image pixels exceed the hard budget")


@dataclass(frozen=True, slots=True)
class MediaUnavailable:
    """Safe non-throwing result for one unavailable image."""

    reason: str
    owner: VisualOwner | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.reason) is not str or not self.reason or "?" in self.reason:
            raise ValueError("media failure reason must be a safe code")


type FetchResult = ImageBytes | MediaUnavailable


@dataclass(frozen=True, slots=True)
class ManagedImageBytes:
    """HTTP-bounded raw import bytes; the managed asset owner decodes the format."""

    owner: VisualOwner
    media_type: StickerMediaType
    data: bytes = field(repr=False)


type AssetFetchResult = ManagedImageBytes | MediaUnavailable


@dataclass(frozen=True, slots=True)
class FetchTimeout:
    connect: float
    read: float
    total: float


@dataclass(frozen=True, slots=True)
class ControlledHTTPResponse:
    """Response evidence returned by an explicitly pinned transport."""

    status_code: int
    headers: Mapping[str, str]
    body: Body = field(repr=False)
    connected_ip: str
    redirected: bool = False
    close: CloseCallback | None = field(default=None, repr=False)


class AddressResolver(Protocol):
    async def resolve(self, host: str, port: int) -> Sequence[str]: ...


class PinnedHTTPTransport(Protocol):
    pins_resolved_ip: bool

    async def fetch(
        self,
        url: str,
        *,
        resolved_ip: str,
        request_timeout: FetchTimeout,
    ) -> ControlledHTTPResponse: ...


class _PixelBudget:
    def __init__(self, maximum: int) -> None:
        self._maximum = maximum
        self._used = 0
        self._lock = asyncio.Lock()

    async def reserve(self, pixels: int) -> bool:
        async with self._lock:
            if self._used + pixels > self._maximum:
                return False
            self._used += pixels
            return True


class VisualTransport:
    """Validate controlled bytes and optionally consume a pinned HTTPS URL."""

    def __init__(
        self,
        allowed_hosts: Iterable[str] | None = None,
        *,
        limits: VisualLimits | None = None,
    ) -> None:
        self.limits = limits or VisualLimits()
        self.allowed_hosts = frozenset(
            _normalize_allowlist_host(host) for host in (allowed_hosts or ())
        )

    def validate_bytes(
        self,
        owner: VisualOwner,
        data: bytes,
        content_type: str,
        *,
        content_length: int | None = None,
    ) -> FetchResult:
        """Validate bytes from a separately controlled source without I/O."""

        return self._validate_payload(owner, data, content_type, content_length=content_length)

    async def fetch_url(
        self,
        candidate: VisualUrlCandidate,
        *,
        resolver: AddressResolver | None = None,
        transport: PinnedHTTPTransport | None = None,
        _pixel_budget: _PixelBudget | None = None,
    ) -> FetchResult:
        return cast(FetchResult, await self._fetch_url(
            candidate, resolver=resolver, transport=transport, _pixel_budget=_pixel_budget,
            managed_asset=False))

    async def _fetch_url(
        self, candidate: VisualUrlCandidate, *, resolver: AddressResolver | None,
        transport: PinnedHTTPTransport | None, _pixel_budget: _PixelBudget | None,
        managed_asset: bool,
    ) -> FetchResult | ManagedImageBytes:
        parsed = _parse_https_url(candidate.url)
        if isinstance(parsed, MediaUnavailable):
            return MediaUnavailable(parsed.reason, owner=candidate.owner)
        host, port = parsed
        if not self.allowed_hosts or host not in self.allowed_hosts:
            return MediaUnavailable("host_not_allowed", owner=candidate.owner)
        if resolver is None or transport is None:
            return MediaUnavailable("url_fetch_unavailable", owner=candidate.owner)
        if getattr(transport, "pins_resolved_ip", False) is not True:
            return MediaUnavailable("transport_not_pinned", owner=candidate.owner)
        try:
            async with asyncio.timeout(self.limits.connect_timeout):
                resolved = await resolver.resolve(host, port)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return MediaUnavailable("timeout", owner=candidate.owner)
        except Exception:
            return MediaUnavailable("dns_unavailable", owner=candidate.owner)
        addresses = _validated_resolver_addresses(resolved)
        if not addresses:
            return MediaUnavailable("address_rejected", owner=candidate.owner)

        expected_ip = addresses[0]
        response: ControlledHTTPResponse | None = None
        timeout = FetchTimeout(
            self.limits.connect_timeout,
            self.limits.read_timeout,
            self.limits.total_timeout,
        )
        try:
            async with asyncio.timeout(self.limits.total_timeout):
                async with asyncio.timeout(self.limits.connect_timeout):
                    response = await transport.fetch(
                        candidate.url,
                        resolved_ip=expected_ip,
                        request_timeout=timeout,
                    )
                result = await self._consume_response(
                    candidate.owner, response, expected_ip=expected_ip, managed_asset=managed_asset)
                if isinstance(result, ImageBytes) and _pixel_budget is not None:
                    if not await _pixel_budget.reserve(result.width * result.height):
                        return MediaUnavailable("pixel_budget", owner=candidate.owner)
                return result
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return MediaUnavailable("timeout", owner=candidate.owner)
        except Exception:
            return MediaUnavailable("transport_failed", owner=candidate.owner)
        finally:
            if response is not None:
                await _close_response(response)

    async def fetch_many(
        self,
        candidates: Iterable[VisualUrlCandidate],
        *,
        resolver: AddressResolver | None = None,
        transport: PinnedHTTPTransport | None = None,
    ) -> tuple[FetchResult, ...]:
        """Fetch at most two same-turn candidates with bounded concurrency."""
        return cast(tuple[FetchResult, ...], await self._fetch_many(
            candidates, resolver=resolver, transport=transport, managed_assets=False))

    async def fetch_asset_many(
        self, candidates: Iterable[VisualUrlCandidate], *, resolver: AddressResolver | None = None,
        transport: PinnedHTTPTransport | None = None,
    ) -> tuple[AssetFetchResult, ...]:
        """Use the same pinned acquisition for explicit managed assets, without Model decode."""
        return cast(tuple[AssetFetchResult, ...], await self._fetch_many(
            candidates, resolver=resolver, transport=transport, managed_assets=True))

    async def _fetch_many(
        self, candidates: Iterable[VisualUrlCandidate], *, resolver: AddressResolver | None,
        transport: PinnedHTTPTransport | None, managed_assets: bool,
    ) -> tuple[FetchResult | ManagedImageBytes, ...]:

        # Never exhaust an untrusted or accidentally infinite iterable just
        # to discover that it exceeds the two-image request budget.
        items = tuple(itertools.islice(candidates, self.limits.max_images + 1))
        if not items:
            return ()
        if len(items) > self.limits.max_images:
            return tuple(MediaUnavailable("too_many_images", owner=item.owner) for item in items)
        context = (items[0].owner.scope, items[0].owner.event_id, items[0].owner.turn_id)
        if any(
            (item.owner.scope, item.owner.event_id, item.owner.turn_id) != context
            for item in items[1:]
        ):
            return tuple(MediaUnavailable("mixed_owner_context", owner=item.owner) for item in items)

        semaphore = asyncio.Semaphore(min(self.limits.max_concurrency, len(items)))
        pixel_budget = None if managed_assets else _PixelBudget(self.limits.max_pixels)

        async def one(item: VisualUrlCandidate) -> FetchResult | ManagedImageBytes:
            async with semaphore:
                return await self._fetch_url(
                    item,
                    resolver=resolver,
                    transport=transport,
                    _pixel_budget=pixel_budget,
                    managed_asset=managed_assets,
                )

        tasks = [asyncio.create_task(one(item)) for item in items]
        try:
            return tuple(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _consume_response(
        self,
        owner: VisualOwner,
        response: ControlledHTTPResponse,
        *,
        expected_ip: str,
        managed_asset: bool = False,
    ) -> FetchResult | ManagedImageBytes:
        if response.redirected or 300 <= response.status_code < 400:
            return MediaUnavailable("redirect_rejected", owner=owner)
        if response.status_code != 200:
            return MediaUnavailable("http_status", owner=owner)
        try:
            connected = ipaddress.ip_address(response.connected_ip)
            expected = ipaddress.ip_address(expected_ip)
        except ValueError:
            return MediaUnavailable("connection_not_pinned", owner=owner)
        if connected != expected:
            return MediaUnavailable("connection_not_pinned", owner=owner)
        if not _is_public_address(connected):
            return MediaUnavailable("connected_address_rejected", owner=owner)
        encoding = _header(response.headers, "content-encoding")
        if encoding is not None and encoding.strip().lower() != "identity":
            return MediaUnavailable("response_encoding", owner=owner)
        content_type = _header(response.headers, "content-type")
        if content_type is None:
            return MediaUnavailable("content_type_missing", owner=owner)
        content_type = content_type.split(";", 1)[0].strip().lower()
        allowed = SUPPORTED_ASSET_MEDIA_TYPES if managed_asset else SUPPORTED_MEDIA_TYPES
        if content_type not in allowed:
            return MediaUnavailable("unsupported_media_type", owner=owner)
        content_length = _content_length(response.headers)
        if content_length == -1:
            return MediaUnavailable("content_length_invalid", owner=owner)
        if content_length is not None and content_length > self.limits.max_bytes:
            return MediaUnavailable("body_too_large", owner=owner)
        async with asyncio.timeout(self.limits.read_timeout):
            data, reason = await _read_bounded(response.body, self.limits.max_bytes)
        if data is None:
            return MediaUnavailable(reason, owner=owner)
        if content_length is not None and content_length != len(data):
            return MediaUnavailable("content_length_mismatch", owner=owner)
        if managed_asset:
            if not data:
                return MediaUnavailable("body_too_large", owner=owner)
            return ManagedImageBytes(owner, cast(StickerMediaType, content_type), data)
        return self._validate_payload(owner, data, content_type, content_length=content_length)

    def _validate_payload(
        self,
        owner: VisualOwner,
        data: bytes,
        content_type: str,
        *,
        content_length: int | None,
    ) -> FetchResult:
        if type(owner) is not VisualOwner:
            raise TypeError("owner must be a VisualOwner")
        if type(data) is not bytes:
            return MediaUnavailable("body_not_bytes", owner=owner)
        if content_length is not None and content_length != len(data):
            return MediaUnavailable("content_length_mismatch", owner=owner)
        if not data or len(data) > self.limits.max_bytes:
            return MediaUnavailable("body_too_large", owner=owner)
        if type(content_type) is not str:
            return MediaUnavailable("unsupported_media_type", owner=owner)
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type not in SUPPORTED_MEDIA_TYPES:
            return MediaUnavailable("unsupported_media_type", owner=owner)
        inspected = _decode_image(data, media_type, self.limits)
        if isinstance(inspected, str):
            return MediaUnavailable(inspected, owner=owner)
        width, height = inspected
        return ImageBytes(
            owner=owner,
            media_type=media_type,  # type: ignore[arg-type]
            data=data,
            width=width,
            height=height,
        )


async def _read_bounded(body: Body, maximum: int) -> tuple[bytes | None, str]:
    if type(body) is bytes:
        return (None, "body_too_large") if len(body) > maximum else (body, "")
    stream = cast(AsyncIterable[bytes], body)
    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in stream:
            if type(chunk) is not bytes:
                return None, "body_not_bytes"
            total += len(chunk)
            if total > maximum:
                return None, "body_too_large"
            chunks.append(chunk)
    except asyncio.CancelledError:
        raise
    except Exception:
        return None, "body_read_failed"
    return b"".join(chunks), ""


async def _close_response(response: ControlledHTTPResponse) -> None:
    callback = response.close or getattr(response.body, "aclose", None)
    if callback is None:
        return
    try:
        result = callback()
        if inspect.isawaitable(result):
            async with asyncio.timeout(MAX_CLOSE_WAIT):
                await result
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise OperationError("media_cleanup_failed") from exc


def _decode_image(data: bytes, media_type: str, limits: VisualLimits) -> tuple[int, int] | str:
    expected_format = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[media_type]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
                if image.format != expected_format:
                    return "magic_mismatch"
                if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                    return "animated_unsupported"
                width, height = image.size
                if width > limits.max_dimension or height > limits.max_dimension:
                    return "dimension_limit"
                if width * height > limits.max_pixels:
                    return "pixel_budget"
                image.verify()
            with Image.open(BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
                if image.format != expected_format:
                    return "magic_mismatch"
                image.load()
                return width, height
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        IndexError,
        TypeError,
    ):
        return "invalid_image"


def _normalize_allowlist_host(host: str) -> str:
    if type(host) is not str:
        raise TypeError("allowed host must be a string")
    normalized = host.strip().lower().rstrip(".")
    if not normalized or any(token in normalized for token in ("/", "?", "#", "*", ":")):
        raise ValueError("allowed host must be an exact hostname")
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("allowed host is not a valid hostname") from exc


def _parse_https_url(value: str) -> tuple[str, int] | MediaUnavailable:
    try:
        parsed: SplitResult = urlsplit(value)
        host_value = parsed.hostname
        port = parsed.port
    except ValueError:
        return MediaUnavailable("invalid_url")
    if parsed.scheme.lower() != "https":
        return MediaUnavailable("https_required")
    if host_value is None or not host_value or parsed.username is not None or parsed.password is not None:
        return MediaUnavailable("invalid_url")
    if port not in (None, 443):
        return MediaUnavailable("port_not_allowed")
    try:
        literal_address = ipaddress.ip_address(host_value)
    except ValueError:
        literal_address = None
    if literal_address is not None and not _is_public_address(literal_address):
        return MediaUnavailable("address_rejected")
    try:
        return _normalize_allowlist_host(host_value), 443
    except (TypeError, ValueError):
        return MediaUnavailable("invalid_url")


def _validated_resolver_addresses(values: Sequence[str]) -> tuple[str, ...]:
    if not values:
        return ()
    addresses: list[str] = []
    for value in values:
        if type(value) is not str:
            return ()
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return ()
        if not _is_public_address(address):
            return ()
        text = str(address)
        if text not in addresses:
            addresses.append(text)
    return tuple(addresses)


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
        and not address.is_reserved
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if type(key) is str and key.lower() == wanted and type(value) is str:
            return value
    return None


def _content_length(headers: Mapping[str, str]) -> int | None:
    value = _header(headers, "content-length")
    if value is None:
        return None
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError):
        return -1
    return parsed if parsed >= 0 else -1


__all__ = [
    "AddressResolver",
    "ControlledHTTPResponse",
    "FetchResult",
    "FetchTimeout",
    "ImageBytes",
    "MAX_FETCH_CONCURRENCY",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_DIMENSION",
    "MAX_IMAGE_PIXELS",
    "MAX_IMAGES",
    "MediaType",
    "MediaUnavailable",
    "PinnedHTTPTransport",
    "SUPPORTED_MEDIA_TYPES",
    "VisualLimits",
    "VisualTransport",
    "VisualUrlCandidate",
]
