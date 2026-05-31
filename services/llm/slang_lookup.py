"""OOV slang lookup cascade: local store first, optional TianAPI fallback."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import aiohttp

from services.slang import normalize_term

_TIANAPI_URL = "https://apis.tianapi.com/hotword/index"


@dataclass(frozen=True, slots=True)
class SlangResult:
    term: str
    explanation: str
    source: Literal["local_db", "tianapi", "context_infer", "ask_user"]
    confidence: float


class SlangLookupClient:
    def __init__(
        self,
        *,
        store_getter: Any = None,
        api_key: str = "",
        timeout_ms: int = 500,
        daily_limit: int = 100,
        cache_size: int = 500,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_cooldown_s: int = 300,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._store_getter = store_getter
        self._api_key = str(api_key or "").strip()
        self._timeout_s = max(0.05, float(timeout_ms) / 1000.0)
        self._daily_limit = max(1, int(daily_limit))
        self._cache_size = max(1, int(cache_size))
        self._circuit_breaker_threshold = max(1, int(circuit_breaker_threshold))
        self._circuit_breaker_cooldown_s = max(1, int(circuit_breaker_cooldown_s))
        self._session = session
        self._cache: OrderedDict[str, SlangResult] = OrderedDict()
        self._daily_count = 0
        self._daily_window_key = self._current_day_key()
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    async def lookup(self, term: str, *, group_id: str | None = None) -> SlangResult | None:
        normalized = normalize_term(str(term or "").strip())
        if not normalized:
            return None
        cached = self._cache_get(normalized)
        if cached is not None:
            return cached
        local = await self._lookup_local(normalized, group_id=group_id)
        if local is not None:
            self._cache_set(normalized, local)
            return local
        remote = await self._lookup_tianapi(normalized)
        if remote is not None:
            self._cache_set(normalized, remote)
        return remote

    async def batch_lookup(
        self,
        terms: Sequence[str],
        *,
        group_id: str | None = None,
    ) -> dict[str, SlangResult | None]:
        results: dict[str, SlangResult | None] = {}
        for raw in terms:
            term = str(raw or "").strip()
            if not term or term in results:
                continue
            results[term] = await self.lookup(term, group_id=group_id)
        return results

    async def _lookup_local(self, term: str, *, group_id: str | None) -> SlangResult | None:
        if self._store_getter is None:
            return None
        try:
            store = self._store_getter()
        except Exception:
            return None
        if store is None or not hasattr(store, "lookup_terms"):
            return None
        try:
            terms = await store.lookup_terms(
                group_id=group_id,
                query=term,
                limit=1,
                min_confidence=0.0,
            )
        except Exception:
            return None
        if not terms:
            return None
        row = terms[0]
        explanation = str(getattr(row, "meaning", "") or "").strip()
        if not explanation:
            return None
        confidence = float(getattr(row, "confidence", 0.0) or 0.0)
        return SlangResult(
            term=str(getattr(row, "term", term) or term),
            explanation=explanation,
            source="local_db",
            confidence=max(0.0, min(1.0, confidence or 0.85)),
        )

    async def _lookup_tianapi(self, term: str) -> SlangResult | None:
        if not self._api_key:
            return None
        self._reset_daily_window_if_needed()
        if self._daily_count >= self._daily_limit:
            return None
        if self._circuit_open_until > time.monotonic():
            return None
        payload = {"key": self._api_key, "word": term}
        owned = self._session is None
        session = self._session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout_s))
        try:
            async with session.get(_TIANAPI_URL, params=payload) as resp:
                data = await resp.json(content_type=None)
        except Exception:
            self._record_remote_failure()
            if owned:
                await session.close()
            return None
        if owned:
            await session.close()
        self._daily_count += 1
        if int(data.get("code", 0) or 0) != 200:
            self._record_remote_failure()
            return None
        result = data.get("result")
        if not isinstance(result, dict):
            self._record_remote_failure()
            return None
        explanation = str(result.get("explain", "") or "").strip()
        if not explanation:
            self._record_remote_failure()
            return None
        self._consecutive_failures = 0
        return SlangResult(
            term=str(result.get("word", term) or term),
            explanation=explanation,
            source="tianapi",
            confidence=0.72,
        )

    def _record_remote_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._circuit_breaker_threshold:
            self._circuit_open_until = time.monotonic() + float(self._circuit_breaker_cooldown_s)
            self._consecutive_failures = 0

    def _cache_get(self, key: str) -> SlangResult | None:
        value = self._cache.get(key)
        if value is None:
            return None
        self._cache.move_to_end(key)
        return value

    def _cache_set(self, key: str, value: SlangResult) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    @staticmethod
    def _current_day_key() -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    def _reset_daily_window_if_needed(self) -> None:
        day_key = self._current_day_key()
        if day_key == self._daily_window_key:
            return
        self._daily_window_key = day_key
        self._daily_count = 0

