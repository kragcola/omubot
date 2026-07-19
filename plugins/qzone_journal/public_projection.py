"""Closed-template public projection for QZone factual Part C events.

By construction (v0.7):
- Producers select a closed ``public_template_id`` compatible with claim classes.
- Public text is rendered only from that template plus validated generic labels.
- Raw narrative is hash-bound source material only; it never contributes text to
  ``projected_summary``, CandidateEvent, composer, provenance, or UI.
- Internal identity refs are transformation-only and never public-serialized.

Store and provenance reuse ``validate_public_projection_metadata`` so forged or
loosely typed projection blobs cannot be persisted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from plugins.qzone_journal.public_safety import (
    QQ_LIKE_RE,
    contains_secret_or_id_assignment,
    scrub_public_text,
)

PUBLIC_PROJECTION_SCHEMA_VERSION = 1
PUBLIC_PROJECTION_POLICY_ID = "qzone-factual-public-v1"

# Closed claim classes allowed on the public QZone boundary.
ALLOWED_CLAIM_CLASSES: frozenset[str] = frozenset(
    {
        "social_public_event",
        "attendance",
        "milestone",
    }
)

# Closed generic public labels only. Real nicknames cannot be injected.
GENERIC_PUBLIC_LABELS: frozenset[str] = frozenset(
    {
        "一位朋友",
        "一位伙伴",
        "一位同伴",
        "朋友甲",
        "朋友乙",
        "朋友丙",
        "伙伴甲",
        "伙伴乙",
        "伙伴丙",
        "同伴甲",
        "同伴乙",
        "同伴丙",
    }
)

_ALIAS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_POLICY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
# Internal entity key shapes that must never appear as public labels or meta.
_INTERNAL_KEY_SHAPE_RE = re.compile(
    r"(?i)^(?:user|group|entity|person|qq|uin|member)[:#/]"
)
_INTERNAL_KEY_TOKEN_RE = re.compile(
    r"(?i)\b(?:user|group|entity|person|qq|uin|member)[:#/][A-Za-z0-9._-]+"
)

_MAX_ALIASES = 8
_MAX_CLAIM_CLASSES = 8
_PUBLIC_META_TOP_KEYS = frozenset(
    {
        "schema_version",
        "policy_id",
        "public_template_id",
        "source_event_hash",
        "applied_claim_classes",
        "aliases",
    }
)
_PUBLIC_ALIAS_KEYS = frozenset(
    {
        "alias_id",
        "public_label",
        "policy_id",
        "allowed_claim_classes",
    }
)


@dataclass(frozen=True, slots=True)
class PublicTemplateSpec:
    """Closed public text template (code-owned; never free-form narrative)."""

    template_id: str
    claim_classes: frozenset[str]
    arity: int
    pattern: str  # positional {0}, {1}, ... only


# Minimal conservative templates for the three closed claim classes.
PUBLIC_TEMPLATES: dict[str, PublicTemplateSpec] = {
    "social_public_event_solo_v1": PublicTemplateSpec(
        template_id="social_public_event_solo_v1",
        claim_classes=frozenset({"social_public_event"}),
        arity=1,
        pattern="今天和{0}一起参加了公开活动",
    ),
    "social_public_event_duo_v1": PublicTemplateSpec(
        template_id="social_public_event_duo_v1",
        claim_classes=frozenset({"social_public_event"}),
        arity=2,
        pattern="{0}和{1}一起参加了公开活动",
    ),
    "attendance_solo_v1": PublicTemplateSpec(
        template_id="attendance_solo_v1",
        claim_classes=frozenset({"attendance"}),
        arity=1,
        pattern="{0}到场了",
    ),
    "attendance_duo_v1": PublicTemplateSpec(
        template_id="attendance_duo_v1",
        claim_classes=frozenset({"attendance"}),
        arity=2,
        pattern="{0}和{1}到场了",
    ),
    "milestone_solo_v1": PublicTemplateSpec(
        template_id="milestone_solo_v1",
        claim_classes=frozenset({"milestone"}),
        arity=1,
        pattern="和{0}一起达成了一个里程碑",
    ),
    "milestone_duo_v1": PublicTemplateSpec(
        template_id="milestone_duo_v1",
        claim_classes=frozenset({"milestone"}),
        arity=2,
        pattern="和{0}、{1}一起达成了一个里程碑",
    ),
}


class ProjectionError(ValueError):
    """Fail-closed projection rejection (safe, non-echoing message)."""


@dataclass(frozen=True, slots=True)
class InternalIdentityRef:
    """Producer-internal identity. Transformation-only — never public-serialize."""

    entity_key: str
    surface: str

    def to_public_dict(self) -> dict[str, Any]:
        raise TypeError("InternalIdentityRef cannot be serialized to public metadata")

    def public_metadata(self) -> dict[str, Any]:
        raise TypeError("InternalIdentityRef cannot be serialized to public metadata")

    def to_dict(self) -> dict[str, Any]:
        raise TypeError("InternalIdentityRef cannot be serialized to public metadata")


@dataclass(frozen=True, slots=True)
class PublicAliasView:
    """Safe public alias metadata after projection validation."""

    alias_id: str
    public_label: str
    policy_id: str
    allowed_claim_classes: tuple[str, ...]

    def public_metadata(self) -> dict[str, Any]:
        return {
            "alias_id": self.alias_id,
            "public_label": self.public_label,
            "policy_id": self.policy_id,
            "allowed_claim_classes": list(self.allowed_claim_classes),
        }


@dataclass(frozen=True, slots=True)
class ValidatedPublicProjection:
    """Immutable safe projection attached to factual CandidateEvent only."""

    schema_version: int
    policy_id: str
    public_template_id: str
    source_event_hash: str
    applied_claim_classes: tuple[str, ...]
    aliases: tuple[PublicAliasView, ...]
    projected_summary: str

    def public_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "policy_id": self.policy_id,
            "public_template_id": self.public_template_id,
            "source_event_hash": self.source_event_hash,
            "applied_claim_classes": list(self.applied_claim_classes),
            "aliases": [alias.public_metadata() for alias in self.aliases],
        }


def compute_source_event_hash(
    *,
    raw_summary: str,
    claim_classes: list[str],
    public_template_id: str,
    projection_bindings: list[tuple[int, str, str, str, str]],
) -> str:
    """Canonical ordered projection binding for source-event integrity.

    Each binding is ``(position, entity_key, surface, alias_id, public_label)``
    in render order. Bindings are **not** sorted independently: reassignment or
    reorder changes the hash. Raw narrative is hash-bound only (never public).
    """
    bindings_payload = [
        {
            "position": int(position),
            "entity_key": str(entity_key),
            "surface": str(surface),
            "alias_id": str(alias_id),
            "public_label": str(public_label),
        }
        for position, entity_key, surface, alias_id, public_label in projection_bindings
    ]
    payload = {
        "v": 1,
        "summary": str(raw_summary or ""),
        "claim_classes": sorted(str(c) for c in claim_classes),
        "public_template_id": str(public_template_id or ""),
        "bindings": bindings_payload,
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _reject(code: str) -> NoReturn:
    raise ProjectionError(f"public projection rejected: {code}")


def _require_mapping(value: Any, *, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _reject(code)
    return value  # type: ignore[return-value]


def _require_exact_int(value: Any, *, code: str) -> int:
    # bool is a subclass of int; reject it explicitly.
    if type(value) is not int:
        _reject(code)
    return value


def _require_nonempty_str(value: Any, *, code: str, max_len: int = 128) -> str:
    if type(value) is not str:
        _reject(code)
    text = value.strip()
    if not text or len(text) > max_len:
        _reject(code)
    return text


def _parse_closed_claim_list(
    value: Any,
    *,
    code: str,
    require_nonempty: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        _reject(code)
    if require_nonempty and not value:
        _reject(code)
    if len(value) > _MAX_CLAIM_CLASSES:
        _reject(code)
    claims: list[str] = []
    seen: set[str] = set()
    for item in value:
        if type(item) is not str:
            _reject(code)
        claim = item.strip()
        if not claim or claim not in ALLOWED_CLAIM_CLASSES:
            _reject(code)
        if claim in seen:
            _reject(code)
        seen.add(claim)
        claims.append(claim)
    return claims


def _is_unsafe_generic_label(label: str) -> bool:
    if label not in GENERIC_PUBLIC_LABELS:
        return True
    if contains_secret_or_id_assignment(label):
        return True
    if QQ_LIKE_RE.search(label):
        return True
    if _INTERNAL_KEY_SHAPE_RE.search(label) or _INTERNAL_KEY_TOKEN_RE.search(label):
        return True
    scrubbed = scrub_public_text(label)
    return scrubbed != label or scrubbed == "[redacted]"


def render_public_summary(
    *,
    public_template_id: str,
    public_labels: list[str] | tuple[str, ...],
) -> str:
    """Render closed template with validated generic labels only."""
    template = PUBLIC_TEMPLATES.get(public_template_id)
    if template is None:
        _reject("public_template_id")
    labels = list(public_labels)
    if len(labels) != template.arity:
        _reject("template_arity")
    for label in labels:
        if type(label) is not str or _is_unsafe_generic_label(label):
            _reject("unsafe_public_label")
    try:
        rendered = template.pattern.format(*labels)
    except (IndexError, KeyError, ValueError):
        _reject("template_render")
    cleaned = " ".join(str(rendered).split()).strip()
    if not cleaned:
        _reject("empty_projected_summary")
    scrubbed = scrub_public_text(cleaned)
    if not scrubbed or scrubbed == "[redacted]" or "[redacted]" in scrubbed:
        _reject("unsafe_projected_summary")
    if contains_secret_or_id_assignment(scrubbed) or QQ_LIKE_RE.search(scrubbed):
        _reject("unsafe_projected_summary")
    if _INTERNAL_KEY_TOKEN_RE.search(scrubbed):
        _reject("internal_key_leak")
    return scrubbed


def validate_public_projection_metadata(value: Any) -> dict[str, Any]:
    """Strict, shared validator for persisted / review public projection meta.

    Exact types only (``bool`` is not ``int``), closed policy/schema/claims/
    templates, nonempty bounded lists, generic-label allowlist, unique alias
    ids and labels, per-alias claim coverage, no silent truncation, unknown
    keys and internal refs rejected.
    """
    if not isinstance(value, Mapping):
        raise ValueError("provenance.public_projection must be an object")
    # Reject unknown keys (no silent drop).
    unknown = set(str(key) for key in value) - _PUBLIC_META_TOP_KEYS
    if unknown:
        raise ValueError(
            "public_projection contains unsupported keys: "
            + ", ".join(sorted(unknown))
        )
    lowered = {str(key).strip().lower() for key in value}
    forbidden = lowered & {
        "entity_key",
        "internal_ref",
        "internal_refs",
        "surface",
        "surfaces",
        "raw_summary",
        "projected_summary",
        "raw",
        "identities",
        "cookie",
        "token",
        "secret",
        "user_id",
        "group_id",
        "uin",
    }
    if forbidden:
        raise ValueError("public_projection contains forbidden keys")

    try:
        schema_version = _require_exact_int(
            value.get("schema_version"),
            code="schema_version",
        )
    except ProjectionError as exc:
        raise ValueError("public_projection.schema_version must be an integer") from exc
    if schema_version != PUBLIC_PROJECTION_SCHEMA_VERSION:
        raise ValueError("unsupported public_projection.schema_version")

    try:
        policy_id = _require_nonempty_str(
            value.get("policy_id"),
            code="policy_id",
            max_len=64,
        )
    except ProjectionError as exc:
        raise ValueError("public_projection.policy_id is invalid") from exc
    if _POLICY_ID_RE.fullmatch(policy_id) is None or policy_id != PUBLIC_PROJECTION_POLICY_ID:
        raise ValueError("public_projection.policy_id is invalid")

    try:
        public_template_id = _require_nonempty_str(
            value.get("public_template_id"),
            code="public_template_id",
            max_len=64,
        )
    except ProjectionError as exc:
        raise ValueError("public_projection.public_template_id is invalid") from exc
    if (
        _TEMPLATE_ID_RE.fullmatch(public_template_id) is None
        or public_template_id not in PUBLIC_TEMPLATES
    ):
        raise ValueError("public_projection.public_template_id is invalid")
    template = PUBLIC_TEMPLATES[public_template_id]

    raw_hash = value.get("source_event_hash")
    if type(raw_hash) is not str:
        raise ValueError("public_projection.source_event_hash is invalid")
    source_hash = raw_hash.strip().lower()
    if _HASH_RE.fullmatch(source_hash) is None:
        raise ValueError("public_projection.source_event_hash is invalid")

    try:
        claims = _parse_closed_claim_list(
            value.get("applied_claim_classes"),
            code="claim_classes",
            require_nonempty=True,
        )
    except ProjectionError as exc:
        raise ValueError("public_projection.applied_claim_classes is invalid") from exc
    if not set(claims) <= set(template.claim_classes):
        raise ValueError("public_projection template incompatible with claim classes")

    aliases_raw = value.get("aliases")
    if not isinstance(aliases_raw, list) or not aliases_raw:
        raise ValueError("public_projection.aliases must be a non-empty list")
    if len(aliases_raw) > _MAX_ALIASES:
        raise ValueError("public_projection.aliases exceeds bound")
    if len(aliases_raw) != template.arity:
        raise ValueError("public_projection.aliases arity mismatch")

    aliases: list[dict[str, Any]] = []
    seen_alias_ids: set[str] = set()
    seen_labels: set[str] = set()
    for entry in aliases_raw:
        if not isinstance(entry, Mapping):
            raise ValueError("public_projection.aliases entry must be an object")
        unknown_alias = set(str(key) for key in entry) - _PUBLIC_ALIAS_KEYS
        if unknown_alias:
            raise ValueError(
                "public_projection.aliases contains unsupported keys: "
                + ", ".join(sorted(unknown_alias))
            )
        try:
            alias_id = _require_nonempty_str(
                entry.get("alias_id"),
                code="alias_id",
                max_len=64,
            )
            public_label = _require_nonempty_str(
                entry.get("public_label"),
                code="public_label",
                max_len=64,
            )
            entry_policy = _require_nonempty_str(
                entry.get("policy_id"),
                code="policy_id",
                max_len=64,
            )
            allowed = _parse_closed_claim_list(
                entry.get("allowed_claim_classes"),
                code="allowed_claim_classes",
                require_nonempty=True,
            )
        except ProjectionError as exc:
            raise ValueError("public_projection.aliases entry invalid") from exc
        if _ALIAS_ID_RE.fullmatch(alias_id) is None:
            raise ValueError("public_projection.aliases alias_id invalid")
        if entry_policy != PUBLIC_PROJECTION_POLICY_ID:
            raise ValueError("public_projection.aliases policy_id invalid")
        if alias_id in seen_alias_ids:
            raise ValueError("public_projection.aliases duplicate alias_id")
        if public_label in seen_labels:
            raise ValueError("public_projection.aliases duplicate public_label")
        if _is_unsafe_generic_label(public_label):
            raise ValueError("public_projection.aliases public_label not allowed")
        for claim in claims:
            if claim not in allowed:
                raise ValueError(
                    "public_projection.aliases missing claim coverage"
                )
        seen_alias_ids.add(alias_id)
        seen_labels.add(public_label)
        aliases.append(
            {
                "alias_id": alias_id,
                "public_label": public_label,
                "policy_id": entry_policy,
                "allowed_claim_classes": allowed,
            }
        )

    return {
        "schema_version": schema_version,
        "policy_id": policy_id,
        "public_template_id": public_template_id,
        "source_event_hash": source_hash,
        "applied_claim_classes": claims,
        "aliases": aliases,
    }


def project_factual_event(
    *,
    raw_summary: str,
    projection_input: Any,
) -> ValidatedPublicProjection:
    """Validate producer projection input and emit a safe public projection.

    Raises ``ProjectionError`` (ValueError) on any fail-closed condition.
    Raw summary is never copied into projected_summary.
    """
    if projection_input is None:
        _reject("absent")
    payload = _require_mapping(projection_input, code="malformed")

    # Reject unknown top-level keys on the producer input (no silent drop).
    allowed_input_keys = frozenset(
        {
            "schema_version",
            "policy_id",
            "public_template_id",
            "claim_classes",
            "source_event_hash",
            "identities",
        }
    )
    unknown = set(str(key) for key in payload) - allowed_input_keys
    if unknown:
        _reject("unknown_keys")

    schema_version = _require_exact_int(
        payload.get("schema_version"),
        code="schema_version",
    )
    if schema_version != PUBLIC_PROJECTION_SCHEMA_VERSION:
        _reject("schema_version")

    policy_id = _require_nonempty_str(payload.get("policy_id"), code="policy_id")
    if _POLICY_ID_RE.fullmatch(policy_id) is None:
        _reject("policy_id")
    if policy_id != PUBLIC_PROJECTION_POLICY_ID:
        _reject("policy_id")

    public_template_id = _require_nonempty_str(
        payload.get("public_template_id"),
        code="public_template_id",
        max_len=64,
    )
    if (
        _TEMPLATE_ID_RE.fullmatch(public_template_id) is None
        or public_template_id not in PUBLIC_TEMPLATES
    ):
        _reject("public_template_id")
    template = PUBLIC_TEMPLATES[public_template_id]

    summary = str(raw_summary or "")
    if not summary.strip():
        _reject("empty_summary")
    if contains_secret_or_id_assignment(summary):
        _reject("secret_or_id")
    if QQ_LIKE_RE.search(summary):
        _reject("qq_like")

    claim_classes = _parse_closed_claim_list(
        payload.get("claim_classes"),
        code="claim_classes",
        require_nonempty=True,
    )
    if not set(claim_classes) <= set(template.claim_classes):
        _reject("template_claim_mismatch")

    source_hash = _require_nonempty_str(
        payload.get("source_event_hash"),
        code="source_hash",
        max_len=64,
    )
    if _HASH_RE.fullmatch(source_hash) is None:
        _reject("source_hash")

    identities_raw = payload.get("identities")
    if not isinstance(identities_raw, list) or not identities_raw:
        _reject("identities")
    identities: list[Any] = list(identities_raw)
    if len(identities) > _MAX_ALIASES:
        _reject("identities")
    if len(identities) != template.arity:
        _reject("template_arity")

    bindings: list[tuple[InternalIdentityRef, PublicAliasView]] = []
    surface_to_labels: dict[str, set[str]] = {}
    entity_keys_set: set[str] = set()
    identity_pairs: list[tuple[str, str]] = []
    projection_bindings: list[tuple[int, str, str, str, str]] = []
    ordered_labels: list[str] = []
    seen_alias_ids: set[str] = set()
    seen_labels: set[str] = set()

    for position, entry in enumerate(identities):
        entry_map = _require_mapping(entry, code="identity_entry")
        # Identities may only carry public alias fields + internal_ref.
        allowed_entry_keys = frozenset(
            {
                "internal_ref",
                "alias_id",
                "public_label",
                "allowed_claim_classes",
            }
        )
        if set(str(k) for k in entry_map) - allowed_entry_keys:
            _reject("unknown_keys")

        ref_raw = entry_map.get("internal_ref")
        ref_map = _require_mapping(ref_raw, code="internal_ref")
        if set(str(k) for k in ref_map) - {"entity_key", "surface"}:
            _reject("unknown_keys")
        entity_key = _require_nonempty_str(
            ref_map.get("entity_key"),
            code="entity_key",
            max_len=96,
        )
        surface = _require_nonempty_str(
            ref_map.get("surface"),
            code="surface",
            max_len=64,
        )
        if contains_secret_or_id_assignment(surface) or QQ_LIKE_RE.search(surface):
            _reject("unsafe_surface")
        if entity_key in entity_keys_set:
            _reject("duplicate_entity")
        entity_keys_set.add(entity_key)

        alias_id = _require_nonempty_str(entry_map.get("alias_id"), code="alias_id")
        if _ALIAS_ID_RE.fullmatch(alias_id) is None:
            _reject("alias_id")
        if alias_id in seen_alias_ids:
            _reject("duplicate_alias_id")
        seen_alias_ids.add(alias_id)

        public_label = _require_nonempty_str(
            entry_map.get("public_label"),
            code="public_label",
            max_len=64,
        )
        if _is_unsafe_generic_label(public_label):
            _reject("unsafe_public_label")
        if public_label in seen_labels:
            _reject("duplicate_public_label")
        # Label must not equal or embed the private surface nickname.
        if surface == public_label or surface in public_label:
            _reject("unsafe_public_label")
        seen_labels.add(public_label)

        allowed = _parse_closed_claim_list(
            entry_map.get("allowed_claim_classes"),
            code="allowed_claim_classes",
            require_nonempty=True,
        )
        for claim in claim_classes:
            if claim not in allowed:
                _reject("claim_not_allowed_for_alias")

        ref = InternalIdentityRef(entity_key=entity_key, surface=surface)
        alias = PublicAliasView(
            alias_id=alias_id,
            public_label=public_label,
            policy_id=policy_id,
            allowed_claim_classes=tuple(sorted(allowed)),
        )
        bindings.append((ref, alias))
        identity_pairs.append((entity_key, surface))
        projection_bindings.append(
            (position, entity_key, surface, alias_id, public_label)
        )
        ordered_labels.append(public_label)
        surface_to_labels.setdefault(surface, set()).add(public_label)

    for surface, labels in surface_to_labels.items():
        if len(labels) > 1:
            _reject("ambiguous_identity")
        count = sum(1 for pair in identity_pairs if pair[1] == surface)
        if count > 1:
            _reject("ambiguous_identity")

    # Declared surfaces must appear in the raw hash-bound summary (integrity).
    for surface in (pair[1] for pair in identity_pairs):
        if surface and surface not in summary:
            _reject("unmatched_identity")

    expected_hash = compute_source_event_hash(
        raw_summary=summary,
        claim_classes=claim_classes,
        public_template_id=public_template_id,
        projection_bindings=projection_bindings,
    )
    if source_hash != expected_hash:
        _reject("source_hash_mismatch")

    # By construction: render closed template only — never copy raw narrative.
    projected = render_public_summary(
        public_template_id=public_template_id,
        public_labels=ordered_labels,
    )
    for surface in (pair[1] for pair in identity_pairs):
        if surface and surface in projected:
            _reject("surface_leak")
    for ref, _alias in bindings:
        if ref.entity_key in projected:
            _reject("internal_key_leak")

    aliases = tuple(alias for _ref, alias in bindings)
    validated = ValidatedPublicProjection(
        schema_version=PUBLIC_PROJECTION_SCHEMA_VERSION,
        policy_id=policy_id,
        public_template_id=public_template_id,
        source_event_hash=source_hash,
        applied_claim_classes=tuple(sorted(claim_classes)),
        aliases=aliases,
        projected_summary=projected,
    )
    # Persist-shape must pass the same strict metadata validator used by store.
    validate_public_projection_metadata(validated.public_metadata())
    return validated


def is_validated_public_projection(value: Any) -> bool:
    return isinstance(value, ValidatedPublicProjection)


__all__ = [
    "ALLOWED_CLAIM_CLASSES",
    "GENERIC_PUBLIC_LABELS",
    "PUBLIC_PROJECTION_POLICY_ID",
    "PUBLIC_PROJECTION_SCHEMA_VERSION",
    "PUBLIC_TEMPLATES",
    "InternalIdentityRef",
    "ProjectionError",
    "PublicAliasView",
    "PublicTemplateSpec",
    "ValidatedPublicProjection",
    "compute_source_event_hash",
    "is_validated_public_projection",
    "project_factual_event",
    "render_public_summary",
    "validate_public_projection_metadata",
]
