"""Current-event HTTPS image bytes with a checked IP and original TLS identity."""

from __future__ import annotations

import asyncio
import hashlib
import socket
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from time import monotonic
from typing import Literal

import httpx

from .network_logging import redact_httpx_request_urls
from .onebot_ws import OneBotDirectImage, OneBotMessageView
from .types import (
    ConversationScope,
    Event,
    OperationError,
    QuotedImageLease,
    QuotedImageProof,
    QuotedVisualSource,
    VisualOwner,
    VisualSource,
)
from .visual_transport import (
    AddressResolver,
    ControlledHTTPResponse,
    FetchTimeout,
    ImageBytes,
    ManagedImageBytes,
    PinnedHTTPTransport,
    VisualTransport,
    VisualUrlCandidate,
)


class SystemAddressResolver:
    async def resolve(self, host: str, port: int) -> Sequence[str]:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        return tuple(dict.fromkeys(str(address[4][0]) for address in addresses))


class NativePinnedHTTPTransport:
    """Each fetch owns its connection, so an IP cannot reuse another host's TLS."""

    pins_resolved_ip = True

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        redact_httpx_request_urls()
        self._transport = transport

    async def fetch(
        self, url: str, *, resolved_ip: str, request_timeout: FetchTimeout
    ) -> ControlledHTTPResponse:
        return await self.request(
            "GET", url, resolved_ip=resolved_ip, request_timeout=request_timeout,
        )

    async def request(
        self, method: Literal["GET", "POST"], url: str, *, resolved_ip: str,
        request_timeout: FetchTimeout, headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
    ) -> ControlledHTTPResponse:
        """Send one checked request; callers own input and external-action authorization."""
        original = httpx.URL(url)
        pinned = original.copy_with(host=resolved_ip)
        authority = original.netloc.decode("ascii")
        client = httpx.AsyncClient(
            trust_env=False, follow_redirects=False, transport=self._transport,
            timeout=httpx.Timeout(
                request_timeout.read, connect=request_timeout.connect,
                pool=request_timeout.connect, write=request_timeout.connect,
            ),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )
        try:
            request = client.build_request(
                method, pinned, content=content,
                headers={**(headers or {}), "Host": authority, "Accept-Encoding": "identity"},
                extensions={"sni_hostname": original.host},
            )
            response = await client.send(request, stream=True)
            # HTTPX decodes a whole raw chunk before yielding aiter_bytes().
            # Reject encoding before that allocation can bypass the byte cap.
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                await response.aclose()
                raise OperationError("media_response_encoding")
            network = response.extensions.get("network_stream")
            if network is None:
                await response.aclose()
                raise OperationError("media_peer_unavailable")
            peer = network.get_extra_info("server_addr")
            if not isinstance(peer, tuple) or not peer or not isinstance(peer[0], str):
                await response.aclose()
                raise OperationError("media_peer_unavailable")
        except BaseException:
            await client.aclose()
            raise

        async def close() -> None:
            try:
                await response.aclose()
            finally:
                await client.aclose()

        return ControlledHTTPResponse(
            status_code=response.status_code,
            headers=response.headers,
            body=response.aiter_bytes(),
            connected_ip=peer[0],
            redirected=response.is_redirect,
            close=close,
        )


class CurrentEventVisualResolver:
    """Only parser-owned, transient URL hints from direct current images are read."""

    def __init__(
        self, allowed_hosts: Iterable[str], *, resolver: AddressResolver | None = None,
        transport: PinnedHTTPTransport | None = None,
        read_message: Callable[[ConversationScope, str], Awaitable[OneBotMessageView]] | None = None,
    ) -> None:
        self.read_message = read_message
        self.visual = VisualTransport(allowed_hosts)
        self.resolver = resolver or SystemAddressResolver()
        self.transport = transport or NativePinnedHTTPTransport()

    async def resolve(
        self, event: Event, owners: tuple[VisualOwner, ...], *, turn_id: str, revision: int
    ) -> tuple[VisualSource, ...]:
        candidates = self._direct_candidates(event, owners, turn_id=turn_id)
        results = await self.visual.fetch_many(
            candidates, resolver=self.resolver, transport=self.transport
        )
        return tuple(
            VisualSource(result.owner, result.data, result.media_type, event.user_id)
            for result in results if isinstance(result, ImageBytes)
        )

    async def resolve_assets(
        self, event: Event, owners: tuple[VisualOwner, ...], *, turn_id: str, revision: int,
    ) -> tuple[VisualSource, ...]:
        """Explicit managed import acquires raw bytes through this same pinned owner."""
        candidates = self._direct_candidates(event, owners, turn_id=turn_id)
        results = await self.visual.fetch_asset_many(
            candidates, resolver=self.resolver, transport=self.transport,
        )
        return tuple(
            VisualSource(result.owner, result.data, result.media_type, event.user_id)
            for result in results if isinstance(result, ManagedImageBytes)
        )

    @staticmethod
    def _direct_candidates(
        event: Event, owners: tuple[VisualOwner, ...], *, turn_id: str,
    ) -> tuple[VisualUrlCandidate, ...]:
        urls = dict(event.current_image_urls)
        candidates: list[VisualUrlCandidate] = []
        for owner in owners:
            if (
                owner.scope != event.scope or owner.event_id != event.event_id
                or owner.message_id != event.message_id or owner.turn_id != turn_id
                or owner.source_kind != "direct"
            ):
                raise OperationError("invalid_visual_owner")
            if owner.segment_index in urls:
                candidates.append(VisualUrlCandidate(owner, urls[owner.segment_index]))
        return tuple(candidates)

    async def resolve_quote(
        self, lease: QuotedImageLease, *,
        read_message: Callable[[ConversationScope, str], Awaitable[OneBotMessageView]] | None = None,
    ) -> tuple[QuotedVisualSource, ...]:
        """Read exact retained human pixels; current owner gates every await.

        The reader is the actual HTTP/WS sender's get_msg method. Its URL hint
        is transient; no get_image path is opened or converted into an URL.
        """
        return await self._resolve_quote(lease, read_message=read_message, managed_assets=False)

    async def resolve_quote_assets(
        self, lease: QuotedImageLease, *,
        read_message: Callable[[ConversationScope, str], Awaitable[OneBotMessageView]] | None = None,
    ) -> tuple[QuotedVisualSource, ...]:
        """Raw retained assets use an explicit import-purpose lease, without Model decode."""
        return await self._resolve_quote(lease, read_message=read_message, managed_assets=True)

    async def _resolve_quote(
        self, lease: QuotedImageLease, *, managed_assets: bool,
        read_message: Callable[[ConversationScope, str], Awaitable[OneBotMessageView]] | None,
    ) -> tuple[QuotedVisualSource, ...]:
        reader = read_message or self.read_message
        if reader is None:
            raise OperationError("quoted_image_reader_unavailable")
        await lease.assert_current()
        source, owner = lease.source, lease.owner
        if owner.source_kind != "reply" or owner.scope != source.scope:
            raise OperationError("invalid_quoted_source")
        async with asyncio.timeout(min(lease.deadline, source.expires_at) - monotonic()):
            view = await reader(source.scope, source.message_id)
            await lease.assert_current()
            if (view.scope != source.scope or view.message_id != source.message_id
                    or view.sender_user_id != source.author_id):
                raise OperationError("invalid_quoted_source")
            selected: list[tuple[OneBotDirectImage, str]] = []
            for index, token_digest in source.image_bindings:
                image = next((image for image in view.direct_images if image.segment_index == index), None)
                if image is None or hashlib.sha256(image.token.encode()).hexdigest() != token_digest:
                    raise OperationError("quoted_image_binding_mismatch")
                if image.url_hint:
                    selected.append((image, token_digest))
            if not selected:
                raise OperationError("media_unavailable")
            await lease.assert_current()
            candidates = [VisualUrlCandidate(owner, image.url_hint) for image, _ in selected
                          if image.url_hint is not None]
            if managed_assets:
                results = await self.visual.fetch_asset_many(
                    candidates, resolver=self.resolver, transport=self.transport)
            else:
                results = await self.visual.fetch_many(
                    candidates, resolver=self.resolver, transport=self.transport)
            await lease.assert_current()
            resolved = tuple(
                QuotedVisualSource(
                    source=VisualSource(owner, result.data, result.media_type, source.author_id),
                    proof=QuotedImageProof(source, image.segment_index, token_digest,
                                           hashlib.sha256(result.data).hexdigest()),
                )
                for (image, token_digest), result in zip(selected, results, strict=True)
                if isinstance(result, (ManagedImageBytes, ImageBytes))
            )
            if not resolved:
                raise OperationError("media_unavailable")
            return resolved
