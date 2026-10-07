"""Bounded, read-only model catalog discovery for configured providers."""

from __future__ import annotations

import asyncio
import json
import unicodedata
from dataclasses import dataclass
from typing import Final, cast
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from omubot_new.config import validate_endpoint

_MAX_RESPONSE_BYTES: Final = 1024 * 1024
_MAX_MODELS: Final = 1000
_MAX_MODEL_ID_LENGTH: Final = 200
_SUPPORTED_FORMATS: Final = frozenset({"anthropic", "openai_chat", "openai_responses", "deepseek"})
_FULL_ENDPOINT_SUFFIXES: Final = ("/chat/completions", "/responses", "/messages")


@dataclass(frozen=True)
class CatalogResult:
    models: list[str]
    source_url: str
    pages: int
    truncated: bool
    display_names: dict[str, str]


class CatalogError(Exception):
    """Safe error exposed by catalog discovery; it never contains provider data."""

    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _reject_control_characters(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _validate_inputs(api_format: str, endpoint: str, api_key: str) -> None:
    if type(api_format) is not str or api_format not in _SUPPORTED_FORMATS:
        raise CatalogError("unsupported_format")
    try:
        if type(endpoint) is not str:
            raise ValueError("invalid endpoint")
        validate_endpoint(endpoint)
    except (TypeError, ValueError):
        raise CatalogError("invalid_endpoint") from None
    if (
        type(api_key) is not str
        or not api_key.strip()
        or not api_key.isascii()
        or _reject_control_characters(api_key)
    ):
        raise CatalogError("invalid_api_key")


def _models_url(api_format: str, endpoint: str) -> str:
    """Turn either a provider base or a model request URL into its models URL."""
    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/")

    for suffix in _FULL_ENDPOINT_SUFFIXES:
        if path.endswith(suffix):
            prefix = path[: -len(suffix)].rstrip("/")
            model_path = f"{prefix}/models" if prefix else "/models"
            return urlunsplit((parsed.scheme, parsed.netloc, model_path, "", ""))

    if not path:
        path = "" if api_format == "deepseek" else "/v1"
    model_path = f"{path}/models" if path else "/models"
    return urlunsplit((parsed.scheme, parsed.netloc, model_path, "", ""))


async def _read_catalog(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> object:
    try:
        async with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            if 300 <= response.status_code < 400:
                raise CatalogError("redirect_denied")
            if not 200 <= response.status_code < 300:
                raise CatalogError("upstream_http")

            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > _MAX_RESPONSE_BYTES:
                        raise CatalogError("response_too_large")
                except ValueError:
                    pass

            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise CatalogError("response_too_large")
                body.extend(chunk)
    except CatalogError:
        raise
    except httpx.HTTPError:
        raise CatalogError("upstream_unavailable") from None

    try:
        return json.loads(bytes(body))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise CatalogError("invalid_response") from None


def _model_ids(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        raise CatalogError("invalid_response")
    payload_dict = cast(dict[str, object], payload)
    data = payload_dict.get("data")
    if not isinstance(data, list):
        raise CatalogError("invalid_response")

    identifiers: set[str] = set()
    for raw_item in cast(list[object], data):
        if not isinstance(raw_item, dict):
            raise CatalogError("invalid_response")
        item = cast(dict[str, object], raw_item)
        identifier = item.get("id")
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or identifier != identifier.strip()
            or len(identifier) > _MAX_MODEL_ID_LENGTH
            or _reject_control_characters(identifier)
        ):
            raise CatalogError("invalid_response")
        identifiers.add(identifier)
    return sorted(identifiers)


async def discover_models(api_format: str, endpoint: str, api_key: str) -> CatalogResult:
    """Read provider IDs, following supported cursors with explicit bounded completeness."""
    _validate_inputs(api_format, endpoint, api_key)
    url = _models_url(api_format, endpoint)
    if api_format == "anthropic":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
    else:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }

    identifiers: set[str] = set()
    names: dict[str, str] = {}
    seen_cursors: set[str] = set()
    next_url = url
    pages = 0
    truncated = False
    try:
        async with asyncio.timeout(12), httpx.AsyncClient(
            timeout=10.0, trust_env=False, follow_redirects=False,
        ) as client:
            while True:
                payload = await _read_catalog(client, next_url, headers)
                page_ids = _model_ids(payload)
                payload_dict = cast(dict[str, object], payload)
                pages += 1
                identifiers.update(page_ids)
                for item in cast(list[dict[str, object]], payload_dict["data"]):
                    display = item.get("display_name")
                    if (isinstance(display, str) and display.strip() and len(display) <= 200
                            and not _reject_control_characters(display)):
                        names[str(item["id"])] = display
                has_more = payload_dict.get("has_more", False)
                if type(has_more) is not bool:
                    raise CatalogError("invalid_response")
                if len(identifiers) > _MAX_MODELS:
                    truncated = True
                    break
                if not has_more:
                    break
                if api_format != "anthropic" or pages >= 10 or len(identifiers) >= _MAX_MODELS:
                    truncated = True
                    break
                cursor = payload_dict.get("last_id")
                if (not isinstance(cursor, str) or cursor not in page_ids or cursor in seen_cursors):
                    raise CatalogError("invalid_pagination")
                seen_cursors.add(cursor)
                next_url = url + "?" + urlencode({"after_id": cursor})
    except CatalogError:
        raise
    except (httpx.HTTPError, TimeoutError):
        raise CatalogError("upstream_unavailable") from None
    models = sorted(identifiers)[:_MAX_MODELS]
    return CatalogResult(models, url, pages, truncated, {key: names[key] for key in models if key in names})
