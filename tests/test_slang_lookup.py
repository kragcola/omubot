from __future__ import annotations

import aiohttp

from services.llm.slang_lookup import SlangLookupClient


class _FakeTerm:
    def __init__(self, term: str, meaning: str, confidence: float = 0.9) -> None:
        self.term = term
        self.meaning = meaning
        self.confidence = confidence


class _FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str]] = []

    async def lookup_terms(self, *, group_id: str | None, query: str, limit: int, min_confidence: float):
        self.calls.append((group_id, query))
        if query.lower() == "op":
            return [_FakeTerm("op", "原作/过强", 0.88)]
        return []


async def test_slang_lookup_prefers_local_store() -> None:
    store = _FakeStore()
    client = SlangLookupClient(store_getter=lambda: store)

    result = await client.lookup("op", group_id="100")

    assert result is not None
    assert result.source == "local_db"
    assert result.explanation == "原作/过强"
    assert store.calls == [("100", "op")]


async def test_slang_lookup_caches_local_hits() -> None:
    store = _FakeStore()
    client = SlangLookupClient(store_getter=lambda: store)

    first = await client.lookup("op", group_id="100")
    second = await client.lookup("op", group_id="100")

    assert first == second
    assert store.calls == [("100", "op")]


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    def get(self, _url: str, params: dict[str, str]):
        self.calls += 1
        assert params["word"]
        return _FakeResponse(self.payload)


async def test_slang_lookup_uses_tianapi_when_local_misses() -> None:
    session = _FakeSession({
        "code": 200,
        "result": {"word": "awsl", "explain": "啊我死了/太可爱了"},
    })
    client = SlangLookupClient(
        store_getter=lambda: _FakeStore(),
        api_key="k",
        session=session,  # type: ignore[arg-type]
    )

    result = await client.lookup("awsl", group_id="100")

    assert result is not None
    assert result.source == "tianapi"
    assert "可爱" in result.explanation
    assert session.calls == 1


class _ErrorSession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, _url: str, params: dict[str, str]):
        self.calls += 1
        raise aiohttp.ClientError("boom")


async def test_slang_lookup_circuit_breaker_opens_after_failures() -> None:
    session = _ErrorSession()
    client = SlangLookupClient(
        store_getter=lambda: _FakeStore(),
        api_key="k",
        session=session,  # type: ignore[arg-type]
        circuit_breaker_threshold=2,
        circuit_breaker_cooldown_s=60,
    )

    first = await client.lookup("m1", group_id="100")
    second = await client.lookup("m2", group_id="100")
    third = await client.lookup("m3", group_id="100")

    assert first is None and second is None and third is None
    assert session.calls == 2
