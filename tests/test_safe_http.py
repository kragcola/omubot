"""Security contracts for public HTTP resolution and redirects."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest
from aiohttp.client_reqrep import ConnectionKey

import services.tools.safe_http as safe_http


async def test_public_resolver_rejects_mixed_public_private_answers() -> None:
    resolver_type = getattr(safe_http, "PublicAddressResolver", None)
    assert resolver_type is not None

    async def lookup(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        del args, kwargs
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    resolver = resolver_type(lookup=lookup, allow_proxy_dns_net=False)
    with pytest.raises(OSError, match="non-public") as exc_info:
        await resolver.resolve("example.com", 443, socket.AF_UNSPEC)

    assert type(exc_info.value).__name__ == "UnsafePublicResolution"


async def test_public_resolver_returns_the_validated_lookup_addresses() -> None:
    resolver_type = getattr(safe_http, "PublicAddressResolver", None)
    assert resolver_type is not None
    calls: list[tuple[str, int]] = []

    async def lookup(
        host: str,
        port: int,
        *args: Any,
        **kwargs: Any,
    ) -> list[tuple[Any, ...]]:
        del args, kwargs
        calls.append((host, port))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        ]

    resolver = resolver_type(lookup=lookup, allow_proxy_dns_net=False)
    result = await resolver.resolve("example.com", 443, socket.AF_UNSPEC)

    assert calls == [("example.com", 443)]
    assert [item["host"] for item in result] == ["93.184.216.34"]
    assert [item["hostname"] for item in result] == ["example.com"]


async def test_proxy_fake_ip_is_allowed_only_as_a_domain_resolution() -> None:
    async def lookup(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        del args, kwargs
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 443)),
        ]

    resolver = safe_http.PublicAddressResolver(
        lookup=lookup,
        allow_proxy_dns_net=True,
    )
    domain_result = await resolver.resolve(
        "example.com",
        443,
        socket.AF_UNSPEC,
    )

    assert [item["host"] for item in domain_result] == ["198.18.0.1"]
    with pytest.raises(OSError, match="non-public"):
        await resolver.resolve("198.18.0.1", 443, socket.AF_UNSPEC)


class _FakeContent:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, size: int) -> bytes:
        chunk = self._body[:size]
        self._body = self._body[size:]
        return chunk


class _ChunkedFakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, size: int) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks[0][:size]
        self._chunks[0] = self._chunks[0][size:]
        if not self._chunks[0]:
            self._chunks.pop(0)
        return chunk


class _CancellingFakeContent:
    async def read(self, size: int) -> bytes:
        del size
        raise asyncio.CancelledError


class _FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        content: (
            _FakeContent | _ChunkedFakeContent | _CancellingFakeContent | None
        ) = None,
        charset: str | None = "utf-8",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = content or _FakeContent(body)
        self.charset = charset
        self.exited = False

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args
        self.exited = True


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.exited = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args
        self.exited = True

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append((url, kwargs))
        return self._responses.pop(0)


class _FakeConnector:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


async def test_public_fetch_rejects_cross_origin_redirect_before_second_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = safe_http.fetch_public_text
    session = _FakeSession(
        [
            _FakeResponse(
                302,
                headers={"Location": "http://127.0.0.1/admin"},
            )
        ]
    )
    connector_args: dict[str, Any] = {}

    def connector_factory(**kwargs: Any) -> _FakeConnector:
        connector_args.update(kwargs)
        return _FakeConnector(**kwargs)

    monkeypatch.setattr(safe_http.aiohttp, "TCPConnector", connector_factory)
    monkeypatch.setattr(
        safe_http.aiohttp,
        "ClientSession",
        lambda **kwargs: session,
    )

    with pytest.raises(safe_http.UnsafePublicUrl):
        await fetch(
            "https://example.com/start",
            timeout_seconds=5,
            follow_redirects=True,
            headers={"User-Agent": "test"},
            max_bytes=1024,
        )

    assert len(session.requests) == 1
    assert session.requests[0][1]["allow_redirects"] is False
    assert connector_args["use_dns_cache"] is False
    assert isinstance(
        connector_args["resolver"],
        safe_http.PublicAddressResolver,
    )


async def test_public_fetch_translates_unsafe_dns_connector_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = ConnectionKey(
        host="example.com",
        port=443,
        is_ssl=True,
        ssl=True,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )
    connector_error = safe_http.aiohttp.ClientConnectorDNSError(
        key,
        safe_http.UnsafePublicResolution(
            "public DNS returned a non-public address"
        ),
    )

    class _DnsFailureSession(_FakeSession):
        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            self.requests.append((url, kwargs))
            raise connector_error

    session = _DnsFailureSession([])
    monkeypatch.setattr(
        safe_http.aiohttp,
        "TCPConnector",
        lambda **kwargs: _FakeConnector(**kwargs),
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "ClientSession",
        lambda **kwargs: session,
    )

    with pytest.raises(safe_http.UnsafePublicUrl, match="DNS"):
        await safe_http.fetch_public_text(
            "https://example.com/data",
            timeout_seconds=5,
            follow_redirects=False,
            headers={},
            max_bytes=64,
        )

    assert session.exited is True


async def test_public_fetch_follows_only_same_origin_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = safe_http.fetch_public_text
    session = _FakeSession(
        [
            _FakeResponse(302, headers={"Location": "/final"}),
            _FakeResponse(200, body=b"safe body"),
        ]
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "TCPConnector",
        lambda **kwargs: _FakeConnector(**kwargs),
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "ClientSession",
        lambda **kwargs: session,
    )

    response = await fetch(
        "https://example.com/start",
        timeout_seconds=5,
        follow_redirects=True,
        headers={"User-Agent": "test"},
        max_bytes=1024,
    )

    assert [url for url, _kwargs in session.requests] == [
        "https://example.com/start",
        "https://example.com/final",
    ]
    assert response.status_code == 200
    assert response.text == "safe body"
    assert response.final_url == "https://example.com/final"


async def test_public_fetch_reads_stream_until_eof_within_byte_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                content=_ChunkedFakeContent([b"first ", b"second", b""]),
            )
        ]
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "TCPConnector",
        lambda **kwargs: _FakeConnector(**kwargs),
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "ClientSession",
        lambda **kwargs: session,
    )

    response = await safe_http.fetch_public_text(
        "https://example.com/data",
        timeout_seconds=5,
        follow_redirects=False,
        headers={"User-Agent": "test"},
        max_bytes=64,
    )

    assert response.text == "first second"
    assert response.truncated is False


async def test_public_fetch_enforces_byte_cap_across_stream_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(
        [_FakeResponse(200, content=_ChunkedFakeContent([b"abcd", b"efgh"]))]
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "TCPConnector",
        lambda **kwargs: _FakeConnector(**kwargs),
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "ClientSession",
        lambda **kwargs: session,
    )

    response = await safe_http.fetch_public_text(
        "https://example.com/data",
        timeout_seconds=5,
        follow_redirects=False,
        headers={},
        max_bytes=6,
    )

    assert response.text == "abcdef"
    assert response.truncated is True


@pytest.mark.parametrize(
    ("follow_redirects", "headers"),
    [
        (False, {"Location": "/final"}),
        (True, {}),
    ],
)
async def test_public_fetch_returns_unfollowed_redirect_response(
    monkeypatch: pytest.MonkeyPatch,
    follow_redirects: bool,
    headers: dict[str, str],
) -> None:
    session = _FakeSession([_FakeResponse(302, headers=headers, body=b"redirect body")])
    monkeypatch.setattr(
        safe_http.aiohttp,
        "TCPConnector",
        lambda **kwargs: _FakeConnector(**kwargs),
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "ClientSession",
        lambda **kwargs: session,
    )

    response = await safe_http.fetch_public_text(
        "https://example.com/start",
        timeout_seconds=5,
        follow_redirects=follow_redirects,
        headers={},
        max_bytes=64,
    )

    assert len(session.requests) == 1
    assert response.status_code == 302
    assert response.text == "redirect body"


async def test_public_fetch_falls_back_to_utf8_for_unknown_charset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(
        [_FakeResponse(200, body="你好".encode(), charset="unknown-charset")]
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "TCPConnector",
        lambda **kwargs: _FakeConnector(**kwargs),
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "ClientSession",
        lambda **kwargs: session,
    )

    response = await safe_http.fetch_public_text(
        "https://example.com/data",
        timeout_seconds=5,
        follow_redirects=False,
        headers={},
        max_bytes=64,
    )

    assert response.text == "你好"


async def test_public_fetch_propagates_cancellation_and_closes_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(200, content=_CancellingFakeContent())
    session = _FakeSession([response])
    monkeypatch.setattr(
        safe_http.aiohttp,
        "TCPConnector",
        lambda **kwargs: _FakeConnector(**kwargs),
    )
    monkeypatch.setattr(
        safe_http.aiohttp,
        "ClientSession",
        lambda **kwargs: session,
    )

    with pytest.raises(asyncio.CancelledError):
        await safe_http.fetch_public_text(
            "https://example.com/data",
            timeout_seconds=5,
            follow_redirects=False,
            headers={},
            max_bytes=64,
        )

    assert response.exited is True
    assert session.exited is True


@pytest.mark.parametrize(
    ("url", "normalized_url", "origin"),
    [
        (
            "HTTPS://Example.COM:443/docs?q=1#fragment",
            "https://example.com/docs?q=1",
            "https://example.com",
        ),
        (
            "http://example.com:8080",
            "http://example.com:8080/",
            "http://example.com:8080",
        ),
        (
            "https://例子.测试/路径",
            "https://xn--fsqu00a.xn--0zwm56d/路径",
            "https://xn--fsqu00a.xn--0zwm56d",
        ),
    ],
)
def test_public_url_normalization_is_canonical(
    url: str,
    normalized_url: str,
    origin: str,
) -> None:
    normalized = safe_http.normalize_public_http_url(url)

    assert normalized.url == normalized_url
    assert normalized.origin == origin


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:not-a-port/",
        "https://example.com:/",
        "https://example.com:0/",
        "https://example.com:65536/",
        "https://example.com/data\n",
        "https://example.com/data\x7f",
        "https://exa mple.com/",
        "https://example..com/",
        "https://_service.example.com/",
        f"https://{'a' * 64}.example.com/",
        f"https://{'a' * 250}.com/",
    ],
)
def test_public_url_normalization_rejects_malformed_host_or_port(url: str) -> None:
    with pytest.raises(safe_http.UnsafePublicUrl):
        safe_http.normalize_public_http_url(url)


def test_public_url_normalization_rejects_oversized_url() -> None:
    url = "https://example.com/" + "a" * 8192

    with pytest.raises(safe_http.UnsafePublicUrl, match="too long"):
        safe_http.normalize_public_http_url(url)
