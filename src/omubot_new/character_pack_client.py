"""Finite reviewed public batches for the existing external CCIP pack protocol.

No image discovery, model loading or numpy dependency. The returned ZIP is bound
to one fixed request, inspected as data, and hashed locally. Legacy builds do not
declare model/registry identity; absent values remain unknown in the receipt.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import binascii
import hashlib
import json
import math
import stat
import struct
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath
from typing import Annotated, Literal, cast

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .character_recognition import CharacterReferencePack, _destination  # pyright: ignore[reportPrivateUsage]
from .registry_files import decode_json_object
from .types import OperationError, StrictModel
from .visual_transport import MAX_IMAGE_BYTES, MAX_IMAGE_DIMENSION, MAX_IMAGE_PIXELS

MAX_BATCH_IMAGES = 640
MAX_BATCH_CHARACTERS = 64
MAX_BATCH_BYTES = 32 * 1024 * 1024
MAX_BATCH_PIXELS = 512_000_000
MAX_ARTIFACT_BYTES = 40 * 1024 * 1024
MAX_BUILD_RESPONSE_BYTES = 56 * 1024 * 1024
_Slug = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
_Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_Text = Annotated[str, Field(min_length=1, max_length=512)]


class PublicCharacterSource(StrictModel):
    asset_id: _Slug
    character_id: _Slug
    source_uri: _Text
    license_assertion: _Text
    reviewed_by: Annotated[str, Field(min_length=1, max_length=64)]
    approved: Literal[True]
    public_visual_reference: Literal[True]
    image_sha256: _Hash
    crop: list[int] | None = Field(default=None, min_length=4, max_length=4)


class CharacterPackBuildRequest(StrictModel):
    pack_name: _Slug
    series: _Slug
    work: str = Field(max_length=160)
    reference_revision: _Hash
    sources: list[PublicCharacterSource] = Field(min_length=1, max_length=MAX_BATCH_IMAGES)


@dataclass(frozen=True, slots=True)
class _Upload:
    filename: str
    media_type: str
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedCharacterBatch:
    pack_name: str
    series: str
    work: str
    reference_revision: str
    reference_model: str
    reference_registry_version: str
    reference_identity_sha256: str
    character_ids: tuple[str, ...]
    character_counts: tuple[int, ...]
    characters_json: bytes
    provenance_json: bytes
    request_sha256: str
    uploads: tuple[_Upload, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class CharacterBuildArtifact:
    pack_name: str
    pack_dir: str
    character_ids: tuple[str, ...]
    request_sha256: str
    reference_revision: str
    reference_model: str
    reference_registry_version: str
    reference_identity_sha256: str
    provider: str
    artifact_sha256: str
    artifact_model: str | None
    artifact_registry_version: str | None
    dim: int
    provenance_json: bytes
    zip_data: bytes = field(repr=False)
    files: tuple[tuple[str, bytes], ...] = field(repr=False)
    runtime_model_match_verified: Literal[False] = False


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def reference_identity_sha256(reference: CharacterReferencePack, ids: tuple[str, ...]) -> str:
    entries = {entry.character_id: entry for entry in reference.characters}
    if not set(ids).issubset(entries):
        raise OperationError("character_reference_character_missing")
    return hashlib.sha256(_canonical([{"character_id": cid, "name": entries[cid].name,
        "aliases": list(entries[cid].aliases), "work": entries[cid].work} for cid in ids])).hexdigest()


def _pixels(raw: bytes, crop: list[int] | None, *, remaining_pixels: int = MAX_IMAGE_PIXELS,
            max_dimension: int = MAX_IMAGE_DIMENSION) -> tuple[bytes, str, int]:
    try:
        with Image.open(BytesIO(raw)) as image:
            if (image.format not in {"PNG", "JPEG", "WEBP"}
                    or not 1 <= image.width <= max_dimension
                    or not 1 <= image.height <= max_dimension
                    or image.width * image.height > MAX_IMAGE_PIXELS):
                raise OperationError("invalid_character_public_image")
            pixels = image.width * image.height
            if pixels > remaining_pixels:
                raise OperationError("character_batch_too_large")
            media = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[image.format]
            image.load()
            if crop is None:
                return raw, media, pixels
            x0, y0, x1, y1 = crop
            if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
                raise OperationError("invalid_character_crop")
            output = BytesIO()
            image.crop((x0, y0, x1, y1)).save(output, format="PNG")
            submitted = output.getvalue()
            if len(submitted) > MAX_IMAGE_BYTES:
                raise OperationError("character_crop_too_large")
            return submitted, "image/png", pixels
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise OperationError("invalid_character_public_image") from None


def prepare_character_batch(
    request: CharacterPackBuildRequest, images: dict[str, bytes],
    reference: CharacterReferencePack | None, *, actor: str,
) -> PreparedCharacterBatch:
    """Freeze explicit approved sources and actual cropped uploads before dispatch."""
    if reference is None or reference.revision != request.reference_revision:
        raise OperationError("revision_conflict")
    sources = request.sources
    if (len({source.asset_id for source in sources}) != len(sources)
            or set(images) != {source.asset_id for source in sources}):
        raise OperationError("invalid_character_batch_sources")
    if any(type(raw) is not bytes or not raw or len(raw) > MAX_IMAGE_BYTES for raw in images.values()):
        raise OperationError("invalid_character_public_image")
    total = sum(len(raw) for raw in images.values())
    if total > MAX_BATCH_BYTES:
        raise OperationError("character_batch_too_large")
    identities = {entry.character_id: entry for entry in reference.characters}
    ids = tuple(dict.fromkeys(source.character_id for source in sources))
    if len(ids) > MAX_BATCH_CHARACTERS or not set(ids).issubset(identities):
        raise OperationError("character_reference_character_missing")
    uploads: list[_Upload] = []
    provenance: list[dict[str, object]] = []
    pixels = 0
    for index, source in enumerate(sources):
        if (source.reviewed_by != actor or not source.source_uri.strip()
                or not source.license_assertion.strip()):
            raise OperationError("character_public_approval_required")
        try:
            uri = httpx.URL(source.source_uri)
        except httpx.InvalidURL:
            raise OperationError("character_public_approval_required") from None
        if uri.scheme not in {"http", "https"} or not uri.host or uri.userinfo:
            raise OperationError("character_public_approval_required")
        raw = images[source.asset_id]
        if hashlib.sha256(raw).hexdigest() != source.image_sha256:
            raise OperationError("character_source_hash_mismatch")
        submitted, media, decoded_pixels = _pixels(
            raw, source.crop, remaining_pixels=MAX_BATCH_PIXELS - pixels)
        pixels += decoded_pixels
        total += len(submitted) if source.crop is not None else 0
        if total > MAX_BATCH_BYTES:
            raise OperationError("character_batch_too_large")
        filename = f"c{ids.index(source.character_id)}_{index}." + media.removeprefix("image/")
        uploads.append(_Upload(filename, media, submitted))
        provenance.append({**source.model_dump(mode="json"),
                           "submitted_sha256": hashlib.sha256(submitted).hexdigest(), "filename": filename})
    characters = [{"character_id": cid, "name": identities[cid].name,
                   "aliases": list(identities[cid].aliases), "work": identities[cid].work,
                   "file_prefix": f"c{index}"} for index, cid in enumerate(ids)]
    character_json = _canonical(characters)
    provenance_json = _canonical(provenance)
    digest = hashlib.sha256(_canonical({"request": request.model_dump(mode="json"),
        "characters_sha256": hashlib.sha256(character_json).hexdigest(),
        "provenance_sha256": hashlib.sha256(provenance_json).hexdigest()})).hexdigest()
    counts = Counter(source.character_id for source in sources)
    return PreparedCharacterBatch(request.pack_name, request.series, request.work, reference.revision,
        reference.model, reference.registry_version, reference_identity_sha256(reference, ids),
        ids, tuple(counts[cid] for cid in ids),
        character_json, provenance_json, digest, tuple(uploads))


class _BuildCount(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    character_id: _Slug
    total: int = Field(ge=1, le=MAX_BATCH_IMAGES)
    embedded: int = Field(ge=1, le=MAX_BATCH_IMAGES)
    samples: int = Field(ge=1, le=3)


class _BuildResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    api_version: Literal["2026-06-01.v1"]
    charpack_zip_b64: str = Field(min_length=1, max_length=MAX_BUILD_RESPONSE_BYTES)
    pack_dir: str
    pack: str
    series: str
    character_count: int = Field(ge=1, le=MAX_BATCH_CHARACTERS)
    total: int = Field(ge=1, le=MAX_BATCH_IMAGES)
    embedded: int = Field(ge=1, le=MAX_BATCH_IMAGES)
    samples: int = Field(ge=1, le=MAX_BATCH_CHARACTERS * 3)
    dim: int = Field(ge=1, le=4096)
    characters: list[_BuildCount] = Field(min_length=1, max_length=MAX_BATCH_CHARACTERS)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    registry_version: str | None = Field(default=None, min_length=1, max_length=128)


def _zip_files(raw: bytes, *, max_files: int, max_bytes: int) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > max_files or sum(entry.file_size for entry in entries) > max_bytes:
                raise OperationError("character_artifact_too_large")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                        or str(path) != entry.filename or entry.filename in files or entry.is_dir()
                        or mode not in {0, stat.S_IFREG} or entry.flag_bits & 1
                        or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                    raise OperationError("invalid_character_artifact_zip")
                files[entry.filename] = archive.read(entry)
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError):
        raise OperationError("invalid_character_artifact_zip") from None
    return files


def _check_npy(raw: bytes, dim: int) -> None:
    """Inspect a finite float vector as bytes; never unpickle or run numpy."""
    try:
        if raw[:6] != b"\x93NUMPY" or raw[6:8] not in {b"\x01\x00", b"\x02\x00", b"\x03\x00"}:
            raise ValueError()
        width = 2 if raw[6] == 1 else 4
        offset = 8 + width
        size = int.from_bytes(raw[8:offset], "little")
        if not 1 <= size <= 4096 or len(raw) < offset + size:
            raise ValueError()
        parsed: object = ast.literal_eval(raw[offset:offset + size].decode("utf-8"))
        if type(parsed) is not dict:
            raise ValueError()
        header = cast(dict[str, object], parsed)
        shape = header.get("shape")
        if (set(header) != {"descr", "fortran_order", "shape"}
                or header["fortran_order"] is not False or type(shape) is not tuple
                or len(cast(tuple[object, ...], shape)) != 1
                or type(cast(tuple[object, ...], shape)[0]) is not int or shape != (dim,)
                or header["descr"] not in {"<f4", ">f4", "<f8", ">f8"}):
            raise ValueError()
        dtype = cast(str, header["descr"])
        item_size = int(dtype[-1])
        if len(raw) != offset + size + dim * item_size:
            raise ValueError()
        values = struct.unpack(dtype[0] + ("f" if item_size == 4 else "d") * dim, raw[offset + size:])
        if not all(math.isfinite(value) for value in values):
            raise ValueError()
    except (ValueError, SyntaxError, UnicodeDecodeError, TypeError, KeyError, struct.error):
        raise OperationError("invalid_character_embedding_structure") from None


def _validate_artifact(batch: PreparedCharacterBatch, raw: bytes, *, provider: str) -> CharacterBuildArtifact:
    payload = decode_json_object(raw, json_error_code="character_invalid_json",
                                 schema_error_code="character_invalid_build_response")
    try:
        result = _BuildResponse.model_validate(payload)
        archive = base64.b64decode(result.charpack_zip_b64, validate=True)
    except (ValidationError, binascii.Error, ValueError):
        raise OperationError("character_invalid_build_response") from None
    expected_counts = dict(zip(batch.character_ids, batch.character_counts, strict=True))
    counts = {entry.character_id: entry for entry in result.characters}
    if (result.pack != batch.pack_name or result.pack_dir != batch.pack_name + ".charpack"
            or result.series != batch.series or result.character_count != len(batch.character_ids)
            or len(counts) != len(result.characters) or set(counts) != set(expected_counts)
            or any((counts[cid].total, counts[cid].embedded) != (count, count)
                   for cid, count in expected_counts.items())
            or result.total != len(batch.uploads) or result.embedded != result.total
            or result.samples != sum(entry.samples for entry in result.characters)
            or result.model is not None and result.model != batch.reference_model
            or result.registry_version is not None
            and result.registry_version != batch.reference_registry_version):
        raise OperationError("character_build_identity_mismatch")
    if not archive or len(archive) > MAX_ARTIFACT_BYTES:
        raise OperationError("character_artifact_too_large")
    files = _zip_files(archive, max_files=2 + MAX_BATCH_CHARACTERS * 3, max_bytes=MAX_ARTIFACT_BYTES)
    prefix = result.pack_dir + "/"
    if any(not path.startswith(prefix) for path in files):
        raise OperationError("character_build_identity_mismatch")
    stripped = {path.removeprefix(prefix): data for path, data in files.items()}
    if not {"manifest.json", "embeddings.npz"}.issubset(stripped):
        raise OperationError("character_build_identity_mismatch")
    manifest = decode_json_object(stripped["manifest.json"], json_error_code="character_invalid_manifest",
                                  schema_error_code="character_invalid_manifest")
    expected = cast(list[dict[str, object]], json.loads(batch.characters_json))
    members = manifest.get("characters")
    if (manifest.get("pack") != batch.pack_name or manifest.get("series") != batch.series
            or manifest.get("work", "") != batch.work or manifest.get("relation_default") != "known"
            or not isinstance(members, list)):
        raise OperationError("character_build_identity_mismatch")
    entries = cast(list[object], members)
    if len(entries) != len(expected):
        raise OperationError("character_build_identity_mismatch")
    for entry, identity in zip(entries, expected, strict=True):
        if not isinstance(entry, dict):
            raise OperationError("character_build_identity_mismatch")
        member = cast(dict[str, object], entry)
        if (member.get("character_id") != identity["character_id"]
                or member.get("embedding_key") != identity["character_id"]
                or member.get("name") != identity["name"] or member.get("aliases") != identity["aliases"]
                or member.get("work", batch.work) != (identity["work"] or batch.work)
                or member.get("relation", "known") != "known"):
            raise OperationError("character_build_identity_mismatch")
    vectors = _zip_files(stripped["embeddings.npz"], max_files=MAX_BATCH_CHARACTERS,
                         max_bytes=MAX_BATCH_CHARACTERS * (4096 * 8 + 4108))
    if set(vectors) != {cid + ".npy" for cid in batch.character_ids}:
        raise OperationError("character_build_identity_mismatch")
    for data in vectors.values():
        _check_npy(data, result.dim)
    sample_counts: Counter[str] = Counter()
    for path in stripped:
        if path in {"manifest.json", "embeddings.npz"}:
            continue
        parts = PurePosixPath(path).parts
        if (len(parts) != 3 or parts[0] != "samples" or parts[1] not in counts
                or not parts[2].endswith(".jpg")):
            raise OperationError("character_build_identity_mismatch")
        sample_counts[parts[1]] += 1
        sample = stripped[path]
        # Existing sidecar stores thumbnails with longest edge at most 256.
        if len(sample) > MAX_IMAGE_BYTES or _pixels(sample, None, max_dimension=256)[1] != "image/jpeg":
            raise OperationError("character_build_identity_mismatch")
    if any(sample_counts[cid] != counts[cid].samples for cid in batch.character_ids):
        raise OperationError("character_build_identity_mismatch")
    return CharacterBuildArtifact(batch.pack_name, result.pack_dir, batch.character_ids, batch.request_sha256,
        batch.reference_revision, batch.reference_model, batch.reference_registry_version,
        batch.reference_identity_sha256, provider, hashlib.sha256(archive).hexdigest(), result.model,
        result.registry_version, result.dim, batch.provenance_json, archive, tuple(stripped.items()))


class CcipPackClient:
    """Existing multipart build-series-pack request to one explicit configured service."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 240,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        if type(timeout) not in {float, int} or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise OperationError("invalid_character_build_limits")
        self.timeout = timeout
        self.destination = _destination(base_url).rstrip("/") + "/build-series-pack" if base_url else None
        self.client = (httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False,
            trust_env=False, headers={"Accept": "application/json", "Accept-Encoding": "identity"})
            if self.destination is not None else None)

    @property
    def available(self) -> bool:
        return self.client is not None and not self.client.is_closed

    async def build(self, batch: PreparedCharacterBatch) -> CharacterBuildArtifact:
        if self.client is None or self.destination is None or self.client.is_closed:
            raise OperationError("character_service_unavailable")
        files = [("images", (entry.filename, entry.data, entry.media_type)) for entry in batch.uploads]
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream("POST", self.destination, files=files, data={
                    "pack_name": batch.pack_name, "series": batch.series, "work": batch.work,
                    "relation_default": "known", "characters_json": batch.characters_json.decode(),
                }) as response:
                    if response.status_code != 200:
                        raise OperationError("character_http_status")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise OperationError("character_response_encoding")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > MAX_BUILD_RESPONSE_BYTES:
                            raise OperationError("character_response_too_large")
                        raw.extend(chunk)
            return await asyncio.to_thread(_validate_artifact, batch, bytes(raw), provider=self.destination)
        except (TimeoutError, httpx.TimeoutException):
            raise OperationError("character_timeout") from None
        except httpx.HTTPError:
            raise OperationError("character_transport_failed") from None

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
