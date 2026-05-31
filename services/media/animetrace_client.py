"""AnimeTrace client — online recognition of known anime/galgame characters.

Complements the CCIP sidecar (which handles original/registered characters
like the bot itself): AnimeTrace's online DB covers a huge set of published
anime characters with no local reference images needed.

Contract (verified live 2026-06-01):
  POST https://api.animetrace.com/v1/search
  JSON body: {model, is_multi, base64, ai_detect}
  -> {code, data: [{box, box_id, character: [{character, work}]}], ai, trace_id}
  code 0 = ok; code 17737 = rate limited; others = error.

Pure external HTTP — no model, no numpy — so it lives in omubot, not the
sidecar. Any failure (rate limit, timeout, network) degrades silently to None
so the main render pipeline falls back to CCIP/VL.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import aiohttp
from loguru import logger

_L = logger.bind(channel="debug")

_API_URL = "https://api.animetrace.com/v1/search"
_RATE_LIMITED = 17737
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.animetrace.com/",
    "Origin": "https://www.animetrace.com",
}


@dataclass(frozen=True)
class AnimeTraceMatch:
    character_name: str
    work: str
    box_id: str | None = None


class AnimeTraceClient:
    def __init__(
        self,
        *,
        model: str = "anime_model_lovelive",
        timeout_seconds: float = 8.0,
        api_url: str = _API_URL,
    ) -> None:
        self._model = model
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._api_url = api_url

    async def identify(
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> AnimeTraceMatch | None:
        """Return the top character match, or None on no-match / rate-limit /
        any failure (caller falls back to CCIP/VL)."""
        del media_type  # AnimeTrace takes base64, type-agnostic
        body = {
            "model": self._model,
            "is_multi": 1,
            "base64": base64.b64encode(image_data).decode("ascii"),
            "ai_detect": 0,
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(self._api_url, json=body, headers=_HEADERS) as resp,
            ):
                if resp.status >= 400:
                    _L.warning("animetrace HTTP {}", resp.status)
                    return None
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            _L.warning("animetrace request failed: {}", exc)
            return None
        return self._parse(payload)

    @staticmethod
    def _parse(payload: object) -> AnimeTraceMatch | None:
        if not isinstance(payload, dict):
            return None
        code = payload.get("code")
        if code == _RATE_LIMITED:
            _L.warning("animetrace rate limited (17737) — degrading")
            return None
        if code != 0:
            return None
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None
        # First detected box, first (highest-confidence) character candidate.
        for item in data:
            if not isinstance(item, dict):
                continue
            chars = item.get("character")
            if not isinstance(chars, list) or not chars:
                continue
            first = chars[0]
            if not isinstance(first, dict):
                continue
            name = str(first.get("character") or "").strip()
            work = str(first.get("work") or "").strip()
            if not name:
                continue
            box_id = item.get("box_id")
            return AnimeTraceMatch(
                character_name=name,
                work=work,
                box_id=str(box_id) if box_id else None,
            )
        return None
