"""Bounded, path-free candidate selection for N5 sticker replies.

This module owns only immutable sticker metadata and an in-memory catalog.  It
does not read files, resolve URLs, inspect incoming media, send messages, or
persist user content.  A later sender owner may resolve an approved
``sticker_id`` through its own controlled storage boundary.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Final, Literal, Protocol

from .types import ConversationScope, Scope, StickerImage

StickerStatus = Literal["approved", "pending", "revoked"]

MAX_STICKER_BYTES: Final = 8 * 1024 * 1024
MAX_CATALOG_ENTRIES: Final = 256
MAX_QUERY_CHARS: Final = 512
MAX_SEARCH_RESULTS: Final = 32
MAX_TAGS: Final = 16
MAX_TAG_CHARS: Final = 32
MAX_SCOPE_CHARS: Final = 256
MAX_TEXT_CHARS: Final = 512
SUPPORTED_MIME_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
# Initial replayable engineering default; the future config owner may tune it.
DEFAULT_SCORE_FLOOR: Final = 0.50

_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HASH_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_PATTERN: Final = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)


def sticker_scope_key(scope: ConversationScope) -> str:
    """Canonical exact group identity; delimiters inside IDs cannot collide."""
    if not isinstance(scope, Scope):
        raise ValueError("sticker scope must be a group")
    return json.dumps(scope.key, ensure_ascii=False, separators=(",", ":"))


def _require_text(value: object, name: str, *, max_chars: int, allow_empty: bool = False) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if len(value) > max_chars:
        raise ValueError(f"{name} is too long")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{name} must be trimmed")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} contains control characters")


def _validate_tags(value: Iterable[str], name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of strings")
    values: list[str] = []
    for index, tag in enumerate(value):
        if index >= MAX_TAGS:
            raise ValueError(f"{name} has too many items")
        _require_text(tag, f"{name}[{index}]", max_chars=MAX_TAG_CHARS)
        values.append(unicodedata.normalize("NFKC", tag).casefold())
    if len(values) != len(set(values)):
        raise ValueError(f"{name} contains duplicates")
    return tuple(values)


def _validate_scopes(value: Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError("allowed_scopes must be an iterable of strings")
    values: list[str] = []
    for index, scope in enumerate(value):
        if index >= MAX_TAGS:
            raise ValueError("allowed_scopes has too many items")
        _require_text(scope, f"allowed_scopes[{index}]", max_chars=MAX_SCOPE_CHARS)
        if "*" in scope:
            raise ValueError("allowed_scopes must contain exact scopes")
        values.append(scope)
    if len(values) != len(set(values)):
        raise ValueError("allowed_scopes contains duplicates")
    return tuple(values)


def _validate_id(value: object, name: str = "sticker_id") -> str:
    if type(value) is not str or _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be an opaque identifier")
    return value


def _validate_scope(value: str | None) -> str | None:
    if value is None:
        return None
    _require_text(value, "scope", max_chars=MAX_SCOPE_CHARS)
    if "*" in value:
        raise ValueError("scope must be exact")
    return value


def _normalise_filter_tags(value: Iterable[str], name: str) -> frozenset[str]:
    return frozenset(_validate_tags(value, name))


def _tokens(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return frozenset(_TOKEN_PATTERN.findall(normalized))


def _overlap_ratio(requested: frozenset[str], available: frozenset[str]) -> float:
    if not requested:
        return 0.0
    return len(requested & available) / len(requested)


@dataclass(frozen=True, slots=True)
class StickerEntry:
    """Audited metadata for one candidate; it intentionally has no path field."""

    sticker_id: str
    content_hash: str
    mime_type: str
    byte_size: int
    description: str = ""
    usage_hint: str = ""
    intent_tags: tuple[str, ...] = ()
    affect_tags: tuple[str, ...] = ()
    allowed_scopes: tuple[str, ...] = ()
    status: StickerStatus = "approved"
    catalog_revision: int = 1
    ocr_text: str = ""

    def __post_init__(self) -> None:
        _validate_id(self.sticker_id)
        if type(self.content_hash) is not str or _HASH_PATTERN.fullmatch(self.content_hash) is None:
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
        if self.mime_type not in SUPPORTED_MIME_TYPES:
            raise ValueError("mime_type is unsupported")
        if type(self.byte_size) is not int or self.byte_size < 1 or self.byte_size > MAX_STICKER_BYTES:
            raise ValueError("byte_size is outside the media budget")
        _require_text(self.description, "description", max_chars=MAX_TEXT_CHARS, allow_empty=True)
        _require_text(self.usage_hint, "usage_hint", max_chars=MAX_TEXT_CHARS, allow_empty=True)
        _require_text(self.ocr_text, "ocr_text", max_chars=MAX_TEXT_CHARS, allow_empty=True)
        if (not self.description and not self.usage_hint and not self.ocr_text
                and not self.intent_tags and not self.affect_tags):
            raise ValueError("sticker requires searchable metadata")
        _validate_tags(self.intent_tags, "intent_tags")
        _validate_tags(self.affect_tags, "affect_tags")
        _validate_scopes(self.allowed_scopes)
        if self.status not in {"approved", "pending", "revoked"}:
            raise ValueError("status is unsupported")
        if type(self.catalog_revision) is not int or self.catalog_revision < 1:
            raise ValueError("catalog_revision must be positive")


@dataclass(frozen=True, slots=True)
class StickerMatch:
    """A candidate plus the deterministic, auditable selection components."""

    entry: StickerEntry
    score: float
    lexical_relevance: float
    intent_tag_match: float
    affect_fit: float
    novelty_fit: float

    @property
    def sticker_id(self) -> str:
        return self.entry.sticker_id


@dataclass(frozen=True, slots=True)
class ResolvedStickerAsset:
    """Current approved catalog identity paired with validated opaque bytes."""

    sticker_id: str
    catalog_revision: int
    image: StickerImage


class ApprovedStickerAssetResolver(Protocol):
    """Resolve only the exact approved metadata key requested by Conversation."""

    async def resolve(
        self, sticker_id: str, catalog_revision: int
    ) -> ResolvedStickerAsset | None: ...


class StickerCatalog:
    """Small in-memory catalog with fail-closed deterministic selection."""

    def __init__(
        self,
        entries: Iterable[StickerEntry] = (),
        *,
        score_floor: float = DEFAULT_SCORE_FLOOR,
        max_entries: int = MAX_CATALOG_ENTRIES,
    ) -> None:
        if type(max_entries) is not int or max_entries < 1 or max_entries > MAX_CATALOG_ENTRIES:
            raise ValueError("max_entries is outside the catalog budget")
        if type(score_floor) not in {int, float} or not math.isfinite(float(score_floor)):
            raise ValueError("score_floor must be finite")
        if not 0 <= score_floor <= 1:
            raise ValueError("score_floor must be between zero and one")
        if isinstance(entries, (str, bytes)):
            raise TypeError("entries must contain StickerEntry values")
        self._score_floor = float(score_floor)
        self._max_entries = max_entries
        self._entries: dict[str, StickerEntry] = {}
        self._tombstones: set[str] = set()
        self._revision = 0
        for entry in entries:
            self.add(entry)

    @property
    def score_floor(self) -> float:
        return self._score_floor

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def size(self) -> int:
        return len(self._entries)

    def get(self, sticker_id: str) -> StickerEntry | None:
        """Return metadata only; no resource is opened or resolved."""

        _validate_id(sticker_id)
        return self._entries.get(sticker_id)

    def restore_snapshot(self, entries: Iterable[StickerEntry], *, revision: int,
                         tombstones: Iterable[str] = ()) -> None:
        """Atomically replace the projection from an authoritative durable epoch."""
        if type(revision) is not int or revision < 0:
            raise ValueError("snapshot revision must be nonnegative")
        restored: dict[str, StickerEntry] = {}
        for entry in entries:
            if type(entry) is not StickerEntry or entry.sticker_id in restored:
                raise ValueError("invalid catalog snapshot")
            if len(restored) >= self._max_entries or entry.catalog_revision > revision:
                raise ValueError("invalid catalog snapshot")
            restored[entry.sticker_id] = entry
        dead = {_validate_id(identifier) for identifier in tombstones}
        dead.update(entry.sticker_id for entry in restored.values() if entry.status == "revoked")
        self._entries, self._tombstones, self._revision = restored, dead, revision

    def add(self, entry: StickerEntry) -> None:
        """Add one entry without importing, reading, or validating a media path."""

        if type(entry) is not StickerEntry:
            raise TypeError("entry must be a StickerEntry")
        sticker_id = entry.sticker_id
        if sticker_id in self._entries or sticker_id in self._tombstones:
            raise ValueError("sticker_id_already_exists")
        if len(self._entries) >= self._max_entries:
            raise ValueError("catalog_is_full")
        self._entries[sticker_id] = entry
        self._revision += 1

    def revoke(self, sticker_id: str) -> None:
        """Revoke a candidate and retain its ID as a non-reusable tombstone."""

        _validate_id(sticker_id)
        entry = self._entries.get(sticker_id)
        if entry is None:
            raise KeyError("unknown_sticker")
        if entry.status == "revoked":
            return
        self._revision += 1
        self._entries[sticker_id] = replace(
            entry,
            status="revoked",
            catalog_revision=self._revision,
        )
        self._tombstones.add(sticker_id)

    def search(
        self,
        query: str,
        *,
        intent_tags: Iterable[str] = (),
        affect_tags: Iterable[str] = (),
        scope: str | None = None,
        recent_sticker_ids: Iterable[str] = (),
        expected_revision: int | None = None,
        limit: int = 5,
    ) -> tuple[StickerMatch, ...]:
        """Return approved candidates above the fixed quality floor.

        The method is deterministic for the same catalog revision and inputs.
        A revision mismatch or an empty query fails closed with no candidates.
        """

        if type(query) is not str:
            raise TypeError("query must be a string")
        if len(query) > MAX_QUERY_CHARS:
            raise ValueError("query is too long")
        if type(limit) is not int or limit < 1 or limit > MAX_SEARCH_RESULTS:
            raise ValueError("limit is outside the search budget")
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 0
        ):
            raise ValueError("expected_revision must be nonnegative")
        if expected_revision is not None and expected_revision != self._revision:
            return ()

        exact_scope = _validate_scope(scope)
        requested_intent = _normalise_filter_tags(intent_tags, "intent_tags")
        requested_affect = _normalise_filter_tags(affect_tags, "affect_tags")
        recent = self._recent_ids(recent_sticker_ids)
        query_tokens = _tokens(query)
        if not query.strip() or not query_tokens:
            return ()

        matches: list[StickerMatch] = []
        for entry in self._entries.values():
            if not self._selectable(entry, exact_scope):
                continue
            entry_tokens = _tokens(
                " ".join(
                    (
                        entry.description,
                        entry.usage_hint,
                        entry.ocr_text,
                        *entry.intent_tags,
                        *entry.affect_tags,
                    )
                )
            )
            lexical_relevance = len(query_tokens & entry_tokens) / len(query_tokens)
            intent_tag_match = _overlap_ratio(requested_intent, frozenset(entry.intent_tags))
            affect_fit = _overlap_ratio(requested_affect, frozenset(entry.affect_tags))
            novelty_fit = 0.0 if entry.sticker_id in recent else 1.0
            score = (
                0.55 * lexical_relevance
                + 0.20 * intent_tag_match
                + 0.15 * affect_fit
                + 0.10 * novelty_fit
            )
            if score < self._score_floor:
                continue
            matches.append(
                StickerMatch(
                    entry=entry,
                    score=score,
                    lexical_relevance=lexical_relevance,
                    intent_tag_match=intent_tag_match,
                    affect_fit=affect_fit,
                    novelty_fit=novelty_fit,
                )
            )

        matches.sort(key=lambda match: (-match.score, -match.entry.catalog_revision, match.sticker_id))
        return tuple(matches[:limit])

    def select(
        self,
        query: str,
        *,
        intent_tags: Iterable[str] = (),
        affect_tags: Iterable[str] = (),
        scope: str | None = None,
        recent_sticker_ids: Iterable[str] = (),
        expected_revision: int | None = None,
    ) -> StickerMatch | None:
        """Return one approved candidate, or ``None`` when the floor is unmet."""

        matches = self.search(
            query,
            intent_tags=intent_tags,
            affect_tags=affect_tags,
            scope=scope,
            recent_sticker_ids=recent_sticker_ids,
            expected_revision=expected_revision,
            limit=1,
        )
        return matches[0] if matches else None

    @staticmethod
    def _recent_ids(values: Iterable[str]) -> frozenset[str]:
        if isinstance(values, (str, bytes)):
            raise TypeError("recent_sticker_ids must be an iterable of IDs")
        identifiers: list[str] = []
        for index, value in enumerate(values):
            if index >= MAX_SEARCH_RESULTS:
                raise ValueError("recent_sticker_ids has too many items")
            identifiers.append(_validate_id(value, "recent_sticker_id"))
        return frozenset(identifiers)

    @staticmethod
    def _selectable(entry: StickerEntry, scope: str | None) -> bool:
        if entry.status != "approved":
            return False
        return not entry.allowed_scopes or scope in entry.allowed_scopes
