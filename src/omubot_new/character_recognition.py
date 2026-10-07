"""Optional external recognition and application-owned reference identities."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .registry_files import (
    absolute_directory,
    decode_json_object,
    ensure_directory_stable,
    read_bounded_file,
    safe_directory_status,
)
from .types import OperationError, VisualOwner
from .visual_transport import MAX_IMAGE_BYTES, ImageBytes

_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_DETECTIONS = 32
_MAX_VECTOR_DIM = 4096
_Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S(?:[^\r\n]*\S)?$")]
_Coordinate = Annotated[float, Field(ge=0)]


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True, allow_inf_nan=False)


class _WireDetection(_WireModel):
    candidate_character_id: _Identifier | None = None
    difference: Annotated[float, Field(ge=0)] | None = None
    bbox: list[_Coordinate] | None = Field(default=None, min_length=4, max_length=4)
    crop_bbox: list[_Coordinate] | None = Field(default=None, min_length=4, max_length=4)


class _SinglePayload(_WireDetection):
    api_version: Literal["2026-06-01.v1"]
    registry_version: _Identifier


class _MultiPayload(_WireModel):
    api_version: Literal["2026-06-01.v1"]
    registry_version: _Identifier
    detection_count: int = Field(ge=0, le=_MAX_DETECTIONS)
    characters: list[_WireDetection] = Field(max_length=_MAX_DETECTIONS)


class _EmbeddingPayload(_WireModel):
    api_version: Literal["2026-06-01.v1"]
    model: _Identifier
    dim: int = Field(ge=1, le=_MAX_VECTOR_DIM)
    embedding: list[float] = Field(min_length=1, max_length=_MAX_VECTOR_DIM)


def _destination(value: str) -> str:
    try:
        url = httpx.URL(value)
        if (
            value != value.strip()
            or url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise ValueError()
        return str(url)
    except (ValueError, httpx.InvalidURL):
        raise OperationError("invalid_character_destination") from None


class _ConfiguredHTTP:
    """One fixed configured destination; no redirects, environment proxies or retries."""

    def __init__(
        self,
        destination: str | None,
        *,
        timeout: float,
        max_response_bytes: int,
        transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        if not 0 < timeout <= 30 or not 1 <= max_response_bytes <= _MAX_RESPONSE_BYTES:
            raise OperationError("invalid_character_http_limits")
        self.destination = _destination(destination) if destination is not None else None
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.client = (
            httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            )
            if self.destination is not None
            else None
        )

    @property
    def available(self) -> bool:
        """Configuration/lifecycle availability; this performs no service-health probe."""
        return self.client is not None and not self.client.is_closed

    def target(self, route: str) -> str | None:
        if self.destination is not None and route:
            return self.destination.rstrip("/") + route
        return self.destination

    async def request(
        self,
        route: str,
        *,
        image: ImageBytes | None = None,
        body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if self.client is None or self.destination is None or self.client.is_closed:
            raise OperationError("character_service_unavailable")
        url = self.target(route)
        assert url is not None
        files = {"image": ("image", image.data, image.media_type)} if image is not None else None
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream("POST", url, files=files, json=body) as response:
                    if response.status_code != 200:
                        raise OperationError("character_http_status")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise OperationError("character_response_encoding")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > self.max_response_bytes:
                            raise OperationError("character_response_too_large")
                        raw.extend(chunk)
            return decode_json_object(
                bytes(raw),
                json_error_code="character_invalid_json",
                schema_error_code="character_invalid_response",
            )
        except (TimeoutError, httpx.TimeoutException):
            raise OperationError("character_timeout") from None
        except httpx.HTTPError:
            raise OperationError("character_transport_failed") from None

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()


def _bbox(values: list[float] | None, image: ImageBytes) -> tuple[float, float, float, float] | None:
    if values is None:
        return None
    x0, y0, x1, y1 = values
    if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
        raise OperationError("character_invalid_response")
    return x0, y0, x1, y1


def _detection(value: _WireDetection, image: ImageBytes) -> CcipDetection:
    return CcipDetection(
        value.candidate_character_id,
        value.difference,
        _bbox(value.bbox, image),
        _bbox(value.crop_bbox, image),
    )


def _crop(image: ImageBytes, bounds: tuple[int, int, int, int]) -> ImageBytes:
    x0, y0, x1, y1 = bounds
    if any(type(value) is not int for value in bounds) or not (
        0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height
    ):
        raise OperationError("invalid_character_crop")
    output = BytesIO()
    with Image.open(BytesIO(image.data)) as original:
        original.crop(bounds).save(output, format="PNG")
    raw = output.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        raise OperationError("character_crop_too_large")
    return ImageBytes(image.owner, "image/png", raw, x1 - x0, y1 - y0)


@dataclass(frozen=True, slots=True)
class ReferenceCharacter:
    character_id: str
    name: str
    aliases: tuple[str, ...] = ()
    relation: str = "known"
    work: str = ""
    source: str = ""
    context_label: str = ""
    series: str = ""


@dataclass(frozen=True, slots=True)
class CharacterReferencePack:
    bot_id: str
    registry_version: str
    model: str
    threshold: float
    characters: tuple[ReferenceCharacter, ...]
    revision: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.threshold) or self.threshold <= 0:
            raise OperationError("invalid_character_reference_pack")
        if len({entry.character_id for entry in self.characters}) != len(self.characters):
            raise OperationError("invalid_character_reference_pack")


@dataclass(frozen=True, slots=True)
class CcipDetection:
    candidate_id: str | None
    difference: float | None
    bbox: tuple[float, float, float, float] | None = None
    crop_bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class CcipIdentification:
    registry_version: str
    detections: tuple[CcipDetection, ...]


@dataclass(frozen=True, slots=True)
class CcipEmbedding:
    model: str
    vector: tuple[float, ...]
    owner: VisualOwner
    image_sha256: str
    crop: tuple[int, int, int, int] | None


class CcipClient:
    """Legacy v1 CCIP wire client. Only the caller can configure its upload target."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 10,
        max_response_bytes: int = 256 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = _ConfiguredHTTP(
            base_url, timeout=timeout, max_response_bytes=max_response_bytes, transport=transport
        )

    @property
    def available(self) -> bool:
        return self._http.available

    def identify_destination(self, *, multi: bool = True) -> str | None:
        """The exact fixed POST destination used by this protocol operation."""
        return self._http.target("/identify-multi" if multi else "/identify")

    @property
    def embed_destination(self) -> str | None:
        return self._http.target("/embed")

    async def identify(self, image: ImageBytes, *, multi: bool = True) -> CcipIdentification:
        payload = await self._http.request("/identify-multi" if multi else "/identify", image=image)
        try:
            if multi:
                result = _MultiPayload.model_validate(payload)
                return CcipIdentification(
                    result.registry_version, tuple(_detection(item, image) for item in result.characters)
                )
            single = _SinglePayload.model_validate(payload)
            detections = (
                (_detection(single, image),)
                if single.candidate_character_id is not None or single.difference is not None
                else ()
            )
            return CcipIdentification(single.registry_version, detections)
        except ValidationError:
            raise OperationError("character_invalid_response") from None

    async def embed(
        self, image: ImageBytes, *, crop: tuple[int, int, int, int] | None = None
    ) -> CcipEmbedding:
        submitted = await asyncio.to_thread(_crop, image, crop) if crop is not None else image
        payload = await self._http.request("/embed", image=submitted)
        try:
            result = _EmbeddingPayload.model_validate(payload)
        except ValidationError:
            raise OperationError("character_invalid_response") from None
        if result.dim != len(result.embedding):
            raise OperationError("character_invalid_response")
        return CcipEmbedding(
            result.model, tuple(result.embedding), image.owner, hashlib.sha256(image.data).hexdigest(), crop
        )

    async def close(self) -> None:
        await self._http.close()


@dataclass(frozen=True, slots=True)
class CharacterMatch:
    detection: CcipDetection
    identity: ReferenceCharacter | None
    matched: bool


def match_reference_pack(
    pack: CharacterReferencePack, result: CcipIdentification
) -> tuple[CharacterMatch, ...]:
    if result.registry_version != pack.registry_version:
        raise OperationError("character_registry_version_mismatch")
    identities = {entry.character_id: entry for entry in pack.characters}
    matches: list[CharacterMatch] = []
    for detection in result.detections:
        identity = identities.get(detection.candidate_id) if detection.candidate_id is not None else None
        matched = (
            identity is not None
            and detection.difference is not None
            and detection.difference <= pack.threshold
        )
        matches.append(CharacterMatch(detection, identity if matched else None, matched))
    return tuple(matches)


class _PackCharacter(_WireModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    character_id: _Identifier
    name: str = Field(min_length=1, max_length=80)
    aliases: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=16
    )
    relation: Literal["self", "friend", "known"] = "known"
    work: str = Field(default="", max_length=160)
    source: str = Field(min_length=1, max_length=512)
    context_label: str = Field(default="", max_length=160)
    series: _Identifier | Literal[""] = ""


class _PackPayload(_WireModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    schema_version: Literal[1]
    bot_id: str = Field(min_length=1, max_length=64)
    registry_version: _Identifier
    model: _Identifier
    threshold: float = Field(gt=0)
    characters: list[_PackCharacter] = Field(min_length=1, max_length=1024)


def load_reference_pack(path: str | Path) -> CharacterReferencePack:
    """Read only this explicit public metadata file; never scan media or load NPZ/models."""
    target = Path(path)
    parent = absolute_directory(target.parent, error_code="invalid_character_reference_file")
    observed = safe_directory_status(parent, error_code="invalid_character_reference_file")
    if observed is None or target.suffix != ".json":
        raise OperationError("invalid_character_reference_file")
    try:
        metadata = target.lstat()
    except OSError:
        raise OperationError("invalid_character_reference_file") from None
    raw = read_bounded_file(
        target, metadata, max_bytes=256 * 1024, error_code="invalid_character_reference_file"
    )
    ensure_directory_stable(parent, observed, error_code="invalid_character_reference_file")
    return parse_reference_pack(raw)


def parse_reference_pack(raw: bytes) -> CharacterReferencePack:
    """Validate one explicit metadata document; its revision hashes metadata bytes only."""
    if type(raw) is not bytes or len(raw) > 256 * 1024:
        raise OperationError("invalid_character_reference_pack")
    payload = decode_json_object(
        raw,
        json_error_code="invalid_character_reference_pack",
        schema_error_code="invalid_character_reference_pack",
    )
    try:
        data = _PackPayload.model_validate(payload)
    except ValidationError:
        raise OperationError("invalid_character_reference_pack") from None
    return CharacterReferencePack(
        bot_id=data.bot_id,
        registry_version=data.registry_version,
        model=data.model,
        threshold=data.threshold,
        characters=tuple(
            ReferenceCharacter(
                item.character_id,
                item.name,
                tuple(item.aliases),
                item.relation,
                item.work,
                item.source,
                item.context_label,
                item.series,
            )
            for item in data.characters
        ),
        revision=hashlib.sha256(raw).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class CharacterRecognition:
    owner: VisualOwner
    image_sha256: str
    pack_revision: str
    matches: tuple[CharacterMatch, ...]
    action_key: str | None = None


class CharacterRecognitionService:
    """Stateless matching over an immutable per-bot pack; no enrollment or teaching writes."""

    def __init__(self, client: CcipClient, pack: CharacterReferencePack | None = None) -> None:
        self._client, self._pack = client, pack

    async def recognize(self, image: ImageBytes, *, multi: bool = True) -> CharacterRecognition:
        pack = self._require_pack(image)
        result = await self._client.identify(image, multi=multi)
        return CharacterRecognition(
            image.owner,
            hashlib.sha256(image.data).hexdigest(),
            pack.revision,
            match_reference_pack(pack, result),
        )

    async def embed_reference(
        self, image: ImageBytes, *, crop: tuple[int, int, int, int] | None = None
    ) -> CcipEmbedding:
        pack = self._require_pack(image)
        result = await self._client.embed(image, crop=crop)
        if result.model != pack.model:
            raise OperationError("character_embedding_model_mismatch")
        return result

    def _require_pack(self, image: ImageBytes) -> CharacterReferencePack:
        if self._pack is None:
            raise OperationError("character_reference_pack_unavailable")
        if self._pack.bot_id != image.owner.scope.bot_id:
            raise OperationError("character_scope_mismatch")
        return self._pack

    @property
    def available(self) -> bool:
        return self._pack is not None and self._client.available

    def identify_destination(self, *, multi: bool = True) -> str | None:
        return self._client.identify_destination(multi=multi)

    @property
    def embed_destination(self) -> str | None:
        return self._client.embed_destination

    @property
    def reference_revision(self) -> str | None:
        return self._pack.revision if self._pack is not None else None


@dataclass(frozen=True, slots=True)
class AnimeTraceCandidate:
    name: str
    work: str
    box_id: str | None


class _AnimeCharacter(_WireModel):
    character: str = Field(min_length=1, max_length=128)
    work: str = Field(max_length=160)


class _AnimeBox(_WireModel):
    box_id: str | int | None = None
    character: list[_AnimeCharacter] = Field(max_length=16)


class _AnimePayload(_WireModel):
    data: list[_AnimeBox] = Field(default_factory=list[_AnimeBox], max_length=_MAX_DETECTIONS)


class AnimeTraceClient:
    """Independent optional AnimeTrace upload target; candidates are never registered identities."""

    def __init__(
        self,
        api_url: str | None = None,
        *,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 8,
        max_response_bytes: int = 256 * 1024,
    ) -> None:
        if api_url is not None and (not model or model != model.strip() or len(model) > 128):
            raise OperationError("invalid_animetrace_model")
        self._model = model
        self._http = _ConfiguredHTTP(
            api_url, timeout=timeout, max_response_bytes=max_response_bytes, transport=transport
        )

    @property
    def available(self) -> bool:
        return self._http.available

    @property
    def destination(self) -> str | None:
        return self._http.target("")

    @property
    def model(self) -> str | None:
        return self._model

    async def identify(self, image: ImageBytes) -> tuple[AnimeTraceCandidate, ...]:
        payload = await self._http.request(
            "",
            body={
                "model": self._model,
                "is_multi": 1,
                "base64": base64.b64encode(image.data).decode("ascii"),
                "ai_detect": 0,
            },
        )
        code = payload.get("code")
        if type(code) is not int:
            raise OperationError("character_invalid_response")
        if code == 17737:
            raise OperationError("animetrace_rate_limited")
        if code != 0:
            raise OperationError("animetrace_api_error")
        try:
            result = _AnimePayload.model_validate(payload)
        except ValidationError:
            raise OperationError("character_invalid_response") from None
        return tuple(
            AnimeTraceCandidate(
                item.character[0].character,
                item.character[0].work,
                str(item.box_id) if item.box_id is not None else None,
            )
            for item in result.data
            if item.character
        )

    async def close(self) -> None:
        await self._http.close()
