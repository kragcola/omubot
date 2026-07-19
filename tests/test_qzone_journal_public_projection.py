"""QZone Journal v0.7 public projection contract (factual Part C).

By-construction closed-template projection. Assertion-based RED → GREEN for
offline public-alias projection. No live QZone HTTP, credentials, Docker,
NapCat, or validated profile mutation.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Import helpers (delayed so missing module is a precise assertion, not skip)
# ---------------------------------------------------------------------------


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        missing = str(exc.name or "")
        if missing and (missing == name or name.startswith(f"{missing}.")):
            return None
        raise


def _req(module_name: str, symbol: str) -> Any:
    module = _optional_module(module_name)
    assert module is not None, f"{module_name} must exist for v0.7 public projection"
    value = getattr(module, symbol, None)
    assert value is not None, f"{module_name} must expose {symbol}"
    return value


def _projection_mod() -> ModuleType:
    module = _optional_module("plugins.qzone_journal.public_projection")
    assert module is not None, "plugins.qzone_journal.public_projection must exist"
    return module


def _canonical_source_hash(
    *,
    raw_summary: str,
    claim_classes: list[str],
    public_template_id: str,
    projection_bindings: list[tuple[int, str, str, str, str]],
) -> str:
    """Mirror of domain hash: ordered (position, entity, surface, alias, label)."""
    payload = {
        "v": 1,
        "summary": raw_summary,
        "claim_classes": sorted(claim_classes),
        "public_template_id": public_template_id,
        "bindings": [
            {
                "position": int(position),
                "entity_key": str(entity_key),
                "surface": str(surface),
                "alias_id": str(alias_id),
                "public_label": str(public_label),
            }
            for position, entity_key, surface, alias_id, public_label in projection_bindings
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _valid_projection_input(
    *,
    raw_summary: str = "今天和小明一起完成了公开排练",
    entity_key: str = "user:10001",
    surface: str = "小明",
    public_label: str = "一位朋友",
    alias_id: str = "alias-friend-a",
    policy_id: str = "qzone-factual-public-v1",
    claim_classes: list[str] | None = None,
    allowed_claim_classes: list[str] | None = None,
    public_template_id: str = "social_public_event_solo_v1",
    source_event_hash: str | None = None,
    identities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    claims = list(claim_classes or ["social_public_event"])
    allowed = list(allowed_claim_classes or ["social_public_event", "attendance", "milestone"])
    if identities is None:
        identities = [
            {
                "internal_ref": {
                    "entity_key": entity_key,
                    "surface": surface,
                },
                "alias_id": alias_id,
                "public_label": public_label,
                "allowed_claim_classes": allowed,
            }
        ]
    projection_bindings = [
        (
            index,
            str(entry["internal_ref"]["entity_key"]),
            str(entry["internal_ref"]["surface"]),
            str(entry["alias_id"]),
            str(entry["public_label"]),
        )
        for index, entry in enumerate(identities)
    ]
    digest = source_event_hash or _canonical_source_hash(
        raw_summary=raw_summary,
        claim_classes=claims,
        public_template_id=public_template_id,
        projection_bindings=projection_bindings,
    )
    return {
        "schema_version": 1,
        "policy_id": policy_id,
        "public_template_id": public_template_id,
        "claim_classes": claims,
        "source_event_hash": digest,
        "identities": identities,
    }


def _candidate(
    *,
    subject_kind: str = "self",
    privacy: str = "public",
    summary: str = "今天把卡住很久的排练段落理顺了",
    stable_id: str = "arc-main:stage-2",
    salience: float = 0.8,
    public_projection: Any = None,
    source: str = "event_replan",
) -> Any:
    CandidateEvent = _req("plugins.qzone_journal.selector", "CandidateEvent")
    kwargs: dict[str, Any] = {
        "source": source,
        "event_date": date(2026, 7, 15),
        "stable_id": stable_id,
        "subject_kind": subject_kind,
        "privacy": privacy,
        "salience": salience,
        "summary": summary,
    }
    try:
        return CandidateEvent(**kwargs, public_projection=public_projection)
    except TypeError:
        if public_projection is not None:
            raise
        return CandidateEvent(**kwargs)


# ---------------------------------------------------------------------------
# 1. Typed public alias / projection contracts
# ---------------------------------------------------------------------------


def test_public_projection_module_exposes_typed_contracts() -> None:
    mod = _projection_mod()
    for name in (
        "InternalIdentityRef",
        "PublicAliasView",
        "ValidatedPublicProjection",
        "project_factual_event",
        "PUBLIC_PROJECTION_SCHEMA_VERSION",
        "ALLOWED_CLAIM_CLASSES",
        "GENERIC_PUBLIC_LABELS",
        "PUBLIC_TEMPLATES",
        "validate_public_projection_metadata",
        "render_public_summary",
        "compute_source_event_hash",
    ):
        assert getattr(mod, name, None) is not None, name


def test_internal_identity_ref_is_transformation_only_not_serializable() -> None:
    InternalIdentityRef = _req(
        "plugins.qzone_journal.public_projection",
        "InternalIdentityRef",
    )
    ref = InternalIdentityRef(entity_key="user:10001", surface="小明")
    assert ref.entity_key == "user:10001"
    assert ref.surface == "小明"
    for method in ("to_public_dict", "public_metadata", "to_dict"):
        fn = getattr(ref, method, None)
        if fn is None:
            continue
        with pytest.raises(TypeError):
            fn()
    as_dict = dataclasses.asdict(ref)
    assert "entity_key" in as_dict


def test_validated_projection_public_metadata_shape_and_immutability() -> None:
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw_summary = "今天和小明一起完成了公开排练"
    projection_input = _valid_projection_input(raw_summary=raw_summary)
    result = project_factual_event(
        raw_summary=raw_summary,
        projection_input=projection_input,
    )
    assert result is not None
    meta = result.public_metadata()
    assert meta["schema_version"] == 1
    assert meta["public_template_id"] == "social_public_event_solo_v1"
    assert "source_event_hash" in meta
    assert meta["source_event_hash"] == projection_input["source_event_hash"]
    assert "applied_claim_classes" in meta
    assert "social_public_event" in meta["applied_claim_classes"]
    aliases = meta["aliases"]
    assert isinstance(aliases, list) and len(aliases) == 1
    alias = aliases[0]
    for key in ("alias_id", "public_label", "policy_id", "allowed_claim_classes"):
        assert key in alias, key
    assert alias["alias_id"] == "alias-friend-a"
    assert alias["public_label"] == "一位朋友"
    assert alias["policy_id"] == "qzone-factual-public-v1"
    blob = json.dumps(meta, ensure_ascii=False)
    assert "entity_key" not in blob
    assert "user:10001" not in blob
    assert "internal_ref" not in blob
    assert "小明" not in blob
    # By construction: closed template text only — not free-form raw rewrite.
    assert result.projected_summary == "今天和一位朋友一起参加了公开活动"
    assert "小明" not in result.projected_summary
    assert "排练" not in result.projected_summary  # raw narrative must not leak
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        result.projected_summary = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Positive closed-template render + self/fiction regression
# ---------------------------------------------------------------------------


def test_project_factual_event_closed_template_render_duo() -> None:
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "小明和阿花一起彩排；小明负责报幕"
    projection_input = _valid_projection_input(
        raw_summary=raw,
        public_template_id="social_public_event_duo_v1",
        identities=[
            {
                "internal_ref": {"entity_key": "user:1", "surface": "小明"},
                "alias_id": "alias-a",
                "public_label": "伙伴甲",
                "allowed_claim_classes": ["social_public_event"],
            },
            {
                "internal_ref": {"entity_key": "user:2", "surface": "阿花"},
                "alias_id": "alias-b",
                "public_label": "伙伴乙",
                "allowed_claim_classes": ["social_public_event"],
            },
        ],
    )
    result = project_factual_event(raw_summary=raw, projection_input=projection_input)
    assert result.projected_summary == "伙伴甲和伙伴乙一起参加了公开活动"
    assert "小明" not in result.projected_summary
    assert "阿花" not in result.projected_summary
    assert "彩排" not in result.projected_summary
    assert "报幕" not in result.projected_summary


def test_compute_source_event_hash_binds_ordered_entity_alias_position() -> None:
    """Reassignment / reorder must change hash (not independent multiset sort)."""
    compute_source_event_hash = _req(
        "plugins.qzone_journal.public_projection",
        "compute_source_event_hash",
    )
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "小明和阿花一起彩排"
    base_bindings = [
        (0, "user:1", "小明", "alias-a", "伙伴甲"),
        (1, "user:2", "阿花", "alias-b", "伙伴乙"),
    ]
    # Same multiset of identities/aliases, but labels swapped across entities.
    swapped_bindings = [
        (0, "user:1", "小明", "alias-b", "伙伴乙"),
        (1, "user:2", "阿花", "alias-a", "伙伴甲"),
    ]
    # Same pairs, different render order (positions changed).
    reordered_bindings = [
        (0, "user:2", "阿花", "alias-b", "伙伴乙"),
        (1, "user:1", "小明", "alias-a", "伙伴甲"),
    ]
    kwargs = {
        "raw_summary": raw,
        "claim_classes": ["social_public_event"],
        "public_template_id": "social_public_event_duo_v1",
    }
    base_hash = compute_source_event_hash(
        **kwargs, projection_bindings=base_bindings
    )
    swapped_hash = compute_source_event_hash(
        **kwargs, projection_bindings=swapped_bindings
    )
    reordered_hash = compute_source_event_hash(
        **kwargs, projection_bindings=reordered_bindings
    )
    assert base_hash != swapped_hash
    assert base_hash != reordered_hash
    assert swapped_hash != reordered_hash

    # Forgery: keep original hash after alias reassignment → reject.
    identities_swapped = [
        {
            "internal_ref": {"entity_key": "user:1", "surface": "小明"},
            "alias_id": "alias-b",
            "public_label": "伙伴乙",
            "allowed_claim_classes": ["social_public_event"],
        },
        {
            "internal_ref": {"entity_key": "user:2", "surface": "阿花"},
            "alias_id": "alias-a",
            "public_label": "伙伴甲",
            "allowed_claim_classes": ["social_public_event"],
        },
    ]
    forged = _valid_projection_input(
        raw_summary=raw,
        public_template_id="social_public_event_duo_v1",
        identities=identities_swapped,
        source_event_hash=base_hash,  # original binding hash, not swapped
    )
    with pytest.raises(ValueError):
        project_factual_event(raw_summary=raw, projection_input=forged)

    # Honest swapped binding with matching hash is accepted (and rebinds labels).
    honest = _valid_projection_input(
        raw_summary=raw,
        public_template_id="social_public_event_duo_v1",
        identities=identities_swapped,
    )
    result = project_factual_event(raw_summary=raw, projection_input=honest)
    assert result.projected_summary == "伙伴乙和伙伴甲一起参加了公开活动"
    assert result.source_event_hash == swapped_hash


def test_selector_accepts_self_and_fiction_without_projection() -> None:
    JournalSelector = _req("plugins.qzone_journal.selector", "JournalSelector")
    selector = JournalSelector(
        allowed_sources={"event_replan", "dream_reflection", "schedule_generator"},
        salience_threshold=0.7,
    )
    assert selector.select(_candidate(subject_kind="self")) is not None
    assert selector.select(_candidate(subject_kind="fiction")) is not None


def test_selector_accepts_factual_only_with_validated_projection() -> None:
    JournalSelector = _req("plugins.qzone_journal.selector", "JournalSelector")
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    selector = JournalSelector(
        allowed_sources={"event_replan", "dream_reflection", "schedule_generator"},
        salience_threshold=0.7,
    )
    raw = "今天和小明一起完成了公开排练"
    validated = project_factual_event(
        raw_summary=raw,
        projection_input=_valid_projection_input(raw_summary=raw),
    )
    accepted = selector.select(
        _candidate(
            subject_kind="factual",
            summary=validated.projected_summary,
            public_projection=validated,
            salience=0.9,
        )
    )
    assert accepted is not None
    assert accepted.subject_kind == "factual"
    assert accepted.summary == validated.projected_summary
    assert "小明" not in accepted.summary


def test_direct_factual_candidate_without_projection_is_rejected() -> None:
    """Bare factual CandidateEvent is constructible but selector-rejected."""
    JournalSelector = _req("plugins.qzone_journal.selector", "JournalSelector")
    CandidateEvent = _req("plugins.qzone_journal.selector", "CandidateEvent")
    # Constructible without projection (bare factual).
    event = CandidateEvent(
        source="event_replan",
        event_date=date(2026, 7, 15),
        stable_id="bare-factual",
        subject_kind="factual",
        privacy="public",
        salience=0.99,
        summary="真人线下行为注入",
        public_projection=None,
    )
    selector = JournalSelector(
        allowed_sources={"event_replan"},
        salience_threshold=0.7,
    )
    decision = selector.evaluate(event)
    assert decision.accepted is False
    assert decision.reason == "reject_public_projection"


# ---------------------------------------------------------------------------
# 2b. Codex counterexamples — must REJECT (by-construction)
# ---------------------------------------------------------------------------


def test_partial_alias_cannot_leave_unbound_real_nickname_in_output() -> None:
    """Counterexample 1: raw 小明、阿花 with only 小明 declared must not leak 阿花.

    Old heuristic path ACCEPTED and emitted ``一位朋友、阿花一起彩排`` by free-form
    substitution. Closed-template path never copies raw narrative:
    - solo + one declared surface may accept, but projected text is template-only
      (no 阿花, no free-form 彩排 wording);
    - duo template with only one identity entry is arity-rejected.
    """
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "小明、阿花一起彩排"
    # Solo template: by-construction render — raw tokens must not appear.
    solo = project_factual_event(
        raw_summary=raw,
        projection_input=_valid_projection_input(
            raw_summary=raw,
            surface="小明",
            public_label="一位朋友",
            public_template_id="social_public_event_solo_v1",
        ),
    )
    assert solo.projected_summary == "今天和一位朋友一起参加了公开活动"
    for leak in ("小明", "阿花", "彩排", "、"):
        assert leak not in solo.projected_summary
    # Duo template with only one identity entry → arity reject.
    with pytest.raises(ValueError) as exc_info:
        project_factual_event(
            raw_summary=raw,
            projection_input={
                "schema_version": 1,
                "policy_id": "qzone-factual-public-v1",
                "public_template_id": "social_public_event_duo_v1",
                "claim_classes": ["social_public_event"],
                "source_event_hash": "0" * 64,
                "identities": [
                    {
                        "internal_ref": {"entity_key": "user:1", "surface": "小明"},
                        "alias_id": "alias-a",
                        "public_label": "一位朋友",
                        "allowed_claim_classes": ["social_public_event"],
                    }
                ],
            },
        )
    assert "384801062" not in str(exc_info.value)
    assert "阿花" not in str(exc_info.value)


def test_arbitrary_chinese_nickname_cannot_be_public_label() -> None:
    """Counterexample 2: public_label=阿花 for raw 今天和小明一起彩排 must reject."""
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "今天和小明一起彩排"
    with pytest.raises(ValueError):
        project_factual_event(
            raw_summary=raw,
            projection_input=_valid_projection_input(
                raw_summary=raw,
                surface="小明",
                public_label="阿花",  # real nickname, not closed generic label
            ),
        )


@pytest.mark.asyncio
async def test_store_rejects_forged_public_projection_metadata(
    tmp_path: Path,
) -> None:
    """Counterexample 3: store must not accept forged / loosely typed meta."""
    JournalStore = _req("plugins.qzone_journal.store", "JournalStore")
    validate = _req(
        "plugins.qzone_journal.public_projection",
        "validate_public_projection_metadata",
    )
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        forged_cases: list[dict[str, Any]] = [
            {
                # schema_version=True accepted by old int() coercion
                "schema_version": True,
                "policy_id": "qzone-factual-public-v1",
                "public_template_id": "social_public_event_solo_v1",
                "source_event_hash": "a" * 64,
                "applied_claim_classes": ["social_public_event"],
                "aliases": [
                    {
                        "alias_id": "a1",
                        "public_label": "一位朋友",
                        "policy_id": "qzone-factual-public-v1",
                        "allowed_claim_classes": ["social_public_event"],
                    }
                ],
            },
            {
                "schema_version": 1,
                "policy_id": "arbitrary-policy",
                "public_template_id": "social_public_event_solo_v1",
                "source_event_hash": "a" * 64,
                "applied_claim_classes": ["social_public_event"],
                "aliases": [
                    {
                        "alias_id": "a1",
                        "public_label": "一位朋友",
                        "policy_id": "arbitrary-policy",
                        "allowed_claim_classes": ["social_public_event"],
                    }
                ],
            },
            {
                "schema_version": 1,
                "policy_id": "qzone-factual-public-v1",
                "public_template_id": "social_public_event_solo_v1",
                "source_event_hash": "a" * 64,
                "applied_claim_classes": ["private_relationship"],
                "aliases": [
                    {
                        "alias_id": "a1",
                        "public_label": "一位朋友",
                        "policy_id": "qzone-factual-public-v1",
                        "allowed_claim_classes": ["private_relationship"],
                    }
                ],
            },
            {
                "schema_version": 1,
                "policy_id": "qzone-factual-public-v1",
                "public_template_id": "social_public_event_solo_v1",
                "source_event_hash": "a" * 64,
                "applied_claim_classes": [],
                "aliases": [
                    {
                        "alias_id": "a1",
                        "public_label": "一位朋友",
                        "policy_id": "qzone-factual-public-v1",
                        "allowed_claim_classes": ["social_public_event"],
                    }
                ],
            },
            {
                "schema_version": 1,
                "policy_id": "qzone-factual-public-v1",
                "public_template_id": "social_public_event_solo_v1",
                "source_event_hash": "a" * 64,
                "applied_claim_classes": ["social_public_event"],
                "aliases": [
                    {
                        "alias_id": "a1",
                        "public_label": "阿花",
                        "policy_id": "qzone-factual-public-v1",
                        "allowed_claim_classes": ["social_public_event"],
                    }
                ],
            },
            {
                "schema_version": 1,
                "policy_id": "qzone-factual-public-v1",
                "public_template_id": "social_public_event_duo_v1",
                "source_event_hash": "a" * 64,
                "applied_claim_classes": ["social_public_event"],
                "aliases": [
                    {
                        "alias_id": "dup-id",
                        "public_label": "伙伴甲",
                        "policy_id": "qzone-factual-public-v1",
                        "allowed_claim_classes": ["social_public_event"],
                    },
                    {
                        "alias_id": "dup-id",
                        "public_label": "伙伴乙",
                        "policy_id": "qzone-factual-public-v1",
                        "allowed_claim_classes": ["social_public_event"],
                    },
                ],
            },
        ]
        for forged in forged_cases:
            with pytest.raises(ValueError):
                validate(forged)
            with pytest.raises(ValueError):
                await store.enqueue(
                    dedupe_key=f"qzone_event_forged_{id(forged)}",
                    event_date=date(2026, 7, 15),
                    source="event_replan",
                    content="今天和一位朋友一起参加了公开活动",
                    stable_id=f"forged-{id(forged)}",
                    subject_kind="factual",
                    privacy="public",
                    salience=0.9,
                    source_summary="今天和一位朋友一起参加了公开活动",
                    provenance={
                        "schema_version": 2,
                        "arc_id": "arc-main",
                        "arc_revision": 1,
                        "arc_stage": "live",
                        "advanced_context_included": False,
                        "fiction_partner_entity_ids": [],
                        "public_projection": forged,
                    },
                )
    finally:
        await store.close()


def test_raw_narrative_never_contributes_text_to_projected_summary() -> None:
    """Even with full alias coverage, free-form raw wording must not appear."""
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "小明、阿花一起彩排"
    projection_input = _valid_projection_input(
        raw_summary=raw,
        public_template_id="social_public_event_duo_v1",
        identities=[
            {
                "internal_ref": {"entity_key": "user:1", "surface": "小明"},
                "alias_id": "alias-a",
                "public_label": "伙伴甲",
                "allowed_claim_classes": ["social_public_event"],
            },
            {
                "internal_ref": {"entity_key": "user:2", "surface": "阿花"},
                "alias_id": "alias-b",
                "public_label": "伙伴乙",
                "allowed_claim_classes": ["social_public_event"],
            },
        ],
    )
    result = project_factual_event(raw_summary=raw, projection_input=projection_input)
    assert result.projected_summary == "伙伴甲和伙伴乙一起参加了公开活动"
    for token in ("小明", "阿花", "彩排", "、"):
        assert token not in result.projected_summary


# ---------------------------------------------------------------------------
# 3. Negative collision / adversarial cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id,mutate",
    [
        pytest.param(
            "absent-projection",
            lambda _raw, _inp: None,
            id="absent-projection",
        ),
        pytest.param(
            "malformed-not-mapping",
            lambda _raw, _inp: "not-a-mapping",
            id="malformed-not-mapping",
        ),
        pytest.param(
            "chinese-nickname-leakage",
            lambda raw, inp: {
                **inp,
                "identities": [
                    {
                        **inp["identities"][0],
                        "public_label": "朋友小明",
                    }
                ],
                "source_event_hash": _canonical_source_hash(
                    raw_summary=raw,
                    claim_classes=list(inp["claim_classes"]),
                    public_template_id=inp["public_template_id"],
                    projection_bindings=[
                        (0, "user:10001", "小明", "alias-friend-a", "朋友小明"),
                    ],
                ),
            },
            id="chinese-nickname-leakage",
        ),
        pytest.param(
            "reidentifying-narrative-unmatched-surface",
            lambda raw, _inp: _valid_projection_input(
                raw_summary=raw,
                surface="不存在的人",
                entity_key="user:10001",
            ),
            id="unmatched-surface",
        ),
        pytest.param(
            "qq-like-identifier",
            lambda _raw, _inp: _valid_projection_input(
                raw_summary="今天和 384801062 一起彩排",
                surface="384801062",
                public_label="一位朋友",
            ),
            id="qq-like-in-raw",
        ),
        pytest.param(
            # Duo template arity requires two identities; one binding → reject.
            "partial-alias-duo-arity-mismatch",
            lambda _raw, _inp: {
                "schema_version": 1,
                "policy_id": "qzone-factual-public-v1",
                "public_template_id": "social_public_event_duo_v1",
                "claim_classes": ["social_public_event"],
                "source_event_hash": "0" * 64,
                "identities": [
                    {
                        "internal_ref": {"entity_key": "user:1", "surface": "小明"},
                        "alias_id": "alias-a",
                        "public_label": "伙伴甲",
                        "allowed_claim_classes": ["social_public_event"],
                    }
                ],
            },
            id="partial-unmatched-alias",
        ),
        pytest.param(
            "forged-hash",
            lambda raw, inp: {
                **inp,
                "source_event_hash": "0" * 64,
            },
            id="forged-hash",
        ),
        pytest.param(
            "forbidden-claim-class",
            lambda raw, _inp: _valid_projection_input(
                raw_summary=raw,
                claim_classes=["private_relationship"],
                allowed_claim_classes=["private_relationship", "social_public_event"],
            ),
            id="forbidden-claim-class",
        ),
        pytest.param(
            "claim-not-in-alias-allowed",
            lambda raw, _inp: _valid_projection_input(
                raw_summary=raw,
                claim_classes=["milestone"],
                allowed_claim_classes=["attendance"],
                public_template_id="milestone_solo_v1",
            ),
            id="claim-not-allowed-for-alias",
        ),
        pytest.param(
            "internal-key-shaped-public-label",
            lambda raw, _inp: _valid_projection_input(
                raw_summary=raw,
                public_label="user:10001",
            ),
            id="internal-key-shaped-label",
        ),
        pytest.param(
            "ambiguous-duplicate-surfaces",
            lambda _raw, _inp: {
                "schema_version": 1,
                "policy_id": "qzone-factual-public-v1",
                "public_template_id": "social_public_event_duo_v1",
                "claim_classes": ["social_public_event"],
                "source_event_hash": _canonical_source_hash(
                    raw_summary="小明到场了",
                    claim_classes=["social_public_event"],
                    public_template_id="social_public_event_duo_v1",
                    projection_bindings=[
                        (0, "user:1", "小明", "alias-a", "伙伴甲"),
                        (1, "user:2", "小明", "alias-b", "伙伴乙"),
                    ],
                ),
                "identities": [
                    {
                        "internal_ref": {"entity_key": "user:1", "surface": "小明"},
                        "alias_id": "alias-a",
                        "public_label": "伙伴甲",
                        "allowed_claim_classes": ["social_public_event"],
                    },
                    {
                        "internal_ref": {"entity_key": "user:2", "surface": "小明"},
                        "alias_id": "alias-b",
                        "public_label": "伙伴乙",
                        "allowed_claim_classes": ["social_public_event"],
                    },
                ],
            },
            id="ambiguous-identity",
        ),
        pytest.param(
            "secret-assignment-in-summary",
            lambda _raw, _inp: _valid_projection_input(
                raw_summary="cookie=abc123 和朋友彩排",
                surface="朋友",
                public_label="一位朋友",
            ),
            id="secret-in-raw",
        ),
        pytest.param(
            "missing-template-id",
            lambda raw, inp: {
                k: v for k, v in inp.items() if k != "public_template_id"
            },
            id="missing-template-id",
        ),
        pytest.param(
            "bool-schema-version",
            lambda raw, inp: {**inp, "schema_version": True},
            id="bool-schema-version",
        ),
    ],
)
def test_project_factual_event_negative_collisions(
    case_id: str,
    mutate: Any,
) -> None:
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "今天和小明一起完成了公开排练"
    if case_id == "qq-like-identifier":
        raw = "今天和 384801062 一起彩排"
    elif case_id == "partial-alias-duo-arity-mismatch":
        raw = "小明和阿花一起彩排"
    elif case_id == "secret-assignment-in-summary":
        raw = "cookie=abc123 和朋友彩排"
    elif case_id == "ambiguous-duplicate-surfaces":
        raw = "小明到场了"
    base = _valid_projection_input(raw_summary=raw)
    projection_input = mutate(raw, base)
    with pytest.raises(ValueError) as exc_info:
        project_factual_event(raw_summary=raw, projection_input=projection_input)
    message = str(exc_info.value).lower()
    assert "384801062" not in str(exc_info.value)
    assert "abc123" not in str(exc_info.value)
    assert message


# ---------------------------------------------------------------------------
# 4. Adapter fail-closed for factual without safe projection
# ---------------------------------------------------------------------------


def test_adapt_raw_event_factual_requires_validated_projection() -> None:
    from plugins.qzone_journal.plugin import QZoneJournalPlugin

    plugin = QZoneJournalPlugin()
    today = date(2026, 7, 15)
    arc = SimpleNamespace(arc_id="arc-main", scope="group")
    raw_summary = "今天和小明一起完成了公开排练"
    good = {
        "date": today.isoformat(),
        "source": "event_replan",
        "summary": raw_summary,
        "subject_kind": "factual",
        "privacy": "public",
        "salience": 0.9,
        "event_id": "factual-good",
        "public_projection": _valid_projection_input(raw_summary=raw_summary),
    }
    decision = plugin._adapt_raw_event(good, arc=arc, today=today)
    assert decision.reason == "accept"
    assert decision.candidate is not None
    assert decision.candidate.subject_kind == "factual"
    assert decision.candidate.summary == "今天和一位朋友一起参加了公开活动"
    assert "小明" not in decision.candidate.summary
    assert decision.candidate.public_projection is not None

    bad = {**good, "public_projection": None}
    del bad["public_projection"]
    bad_decision = plugin._adapt_raw_event(bad, arc=arc, today=today)
    assert bad_decision.reason == "reject_public_projection"
    assert bad_decision.candidate is None

    forged = {
        **good,
        "public_projection": {
            **good["public_projection"],
            "source_event_hash": "f" * 64,
        },
    }
    forged_decision = plugin._adapt_raw_event(forged, arc=arc, today=today)
    assert forged_decision.reason == "reject_public_projection"
    assert forged_decision.candidate is None


def test_adapt_self_and_fiction_remain_projection_optional() -> None:
    from plugins.qzone_journal.plugin import QZoneJournalPlugin

    plugin = QZoneJournalPlugin()
    today = date(2026, 7, 15)
    arc = SimpleNamespace(arc_id="arc-main", scope="fiction")
    for subject in ("self", "fiction"):
        decision = plugin._adapt_raw_event(
            {
                "date": today.isoformat(),
                "source": "event_replan",
                "summary": "虚构伙伴一起彩排",
                "subject_kind": subject,
                "privacy": "public",
                "salience": 0.9,
                "event_id": f"{subject}-ok",
            },
            arc=arc,
            today=today,
        )
        assert decision.reason == "accept", subject
        assert decision.candidate is not None
        assert decision.candidate.subject_kind == subject
        assert decision.candidate.public_projection is None


# ---------------------------------------------------------------------------
# 5. Store / provenance: safe public projection only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_accepts_factual_only_with_safe_projection_provenance(
    tmp_path: Path,
) -> None:
    JournalStore = _req("plugins.qzone_journal.store", "JournalStore")
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "今天和小明一起完成了公开排练"
    validated = project_factual_event(
        raw_summary=raw,
        projection_input=_valid_projection_input(raw_summary=raw),
    )
    provenance = {
        "schema_version": 2,
        "arc_id": "arc-main",
        "arc_revision": 1,
        "arc_stage": "live",
        "advanced_context_included": False,
        "fiction_partner_entity_ids": [],
        "public_projection": validated.public_metadata(),
    }
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        draft = await store.enqueue(
            dedupe_key="qzone_event_factual_ok_001",
            event_date=date(2026, 7, 15),
            source="event_replan",
            content=validated.projected_summary,
            stable_id="factual-ok",
            subject_kind="factual",
            privacy="public",
            salience=0.9,
            source_summary=validated.projected_summary,
            provenance=provenance,
        )
        assert draft.subject_kind == "factual"
        assert draft.provenance_json is not None
        parsed = json.loads(draft.provenance_json)
        assert parsed["schema_version"] == 2
        assert "public_projection" in parsed
        assert parsed["public_projection"]["public_template_id"] == (
            "social_public_event_solo_v1"
        )
        blob = draft.provenance_json
        assert "小明" not in blob
        assert "user:10001" not in blob
        assert "entity_key" not in blob
        assert "internal_ref" not in blob
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_rejects_factual_without_projection_and_poison_keys(
    tmp_path: Path,
) -> None:
    JournalStore = _req("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="qzone_event_factual_bare",
                event_date=date(2026, 7, 15),
                source="event_replan",
                content="真人线下行为",
                stable_id="factual-bare",
                subject_kind="factual",
                privacy="public",
                salience=0.9,
                source_summary="真人线下行为",
                provenance={
                    "schema_version": 1,
                    "arc_id": "arc-main",
                    "arc_revision": 0,
                    "arc_stage": None,
                    "advanced_context_included": False,
                    "fiction_partner_entity_ids": [],
                },
            )
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="qzone_event_poison_prov",
                event_date=date(2026, 7, 15),
                source="event_replan",
                content="safe content",
                stable_id="poison",
                subject_kind="factual",
                privacy="public",
                salience=0.9,
                source_summary="safe content",
                provenance={
                    "schema_version": 2,
                    "arc_id": "arc-main",
                    "arc_revision": 0,
                    "arc_stage": None,
                    "advanced_context_included": False,
                    "fiction_partner_entity_ids": [],
                    "public_projection": {
                        "schema_version": 1,
                        "policy_id": "qzone-factual-public-v1",
                        "public_template_id": "social_public_event_solo_v1",
                        "source_event_hash": "a" * 64,
                        "applied_claim_classes": ["social_public_event"],
                        "aliases": [],
                    },
                    "entity_key": "user:10001",
                },
            )
        draft = await store.enqueue(
            dedupe_key="qzone_event_fiction_ok",
            event_date=date(2026, 7, 15),
            source="event_replan",
            content="虚构伙伴彩排",
            stable_id="fiction-ok",
            subject_kind="fiction",
            privacy="public",
            salience=0.9,
            source_summary="虚构伙伴彩排",
            provenance={
                "schema_version": 1,
                "arc_id": "arc-main",
                "arc_revision": 1,
                "arc_stage": "live",
                "advanced_context_included": False,
                "fiction_partner_entity_ids": ["partner-a"],
            },
        )
        assert draft.subject_kind == "fiction"
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="qzone_event_unknown_schema",
                event_date=date(2026, 7, 15),
                source="event_replan",
                content="x",
                stable_id="bad-schema",
                subject_kind="self",
                privacy="public",
                salience=0.8,
                source_summary="x",
                provenance={
                    "schema_version": 99,
                    "arc_id": "arc-main",
                    "arc_revision": 0,
                    "arc_stage": None,
                    "advanced_context_included": False,
                    "fiction_partner_entity_ids": [],
                },
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_rejects_non_integer_provenance_schema_versions(
    tmp_path: Path,
) -> None:
    """Top-level provenance schema dispatch must not coerce bool/float."""
    JournalStore = _req("plugins.qzone_journal.store", "JournalStore")
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "今天和小明一起完成了公开排练"
    validated = project_factual_event(
        raw_summary=raw,
        projection_input=_valid_projection_input(raw_summary=raw),
    )
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="qzone_event_bool_provenance_schema",
                event_date=date(2026, 7, 15),
                source="event_replan",
                content="公开虚构事件",
                stable_id="bool-schema",
                subject_kind="fiction",
                privacy="public",
                salience=0.8,
                source_summary="公开虚构事件",
                provenance={
                    "schema_version": True,
                    "arc_id": "arc-main",
                    "arc_revision": 0,
                    "arc_stage": None,
                    "advanced_context_included": False,
                    "fiction_partner_entity_ids": [],
                },
            )

        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="qzone_event_float_provenance_schema",
                event_date=date(2026, 7, 15),
                source="event_replan",
                content=validated.projected_summary,
                stable_id="float-schema",
                subject_kind="factual",
                privacy="public",
                salience=0.9,
                source_summary=validated.projected_summary,
                provenance={
                    "schema_version": 2.0,
                    "arc_id": "arc-main",
                    "arc_revision": 0,
                    "arc_stage": None,
                    "advanced_context_included": False,
                    "fiction_partner_entity_ids": [],
                    "public_projection": validated.public_metadata(),
                },
            )
    finally:
        await store.close()


def test_build_review_provenance_includes_safe_projection_for_factual() -> None:
    build_review_provenance = _req(
        "plugins.qzone_journal.advanced_fiction_context",
        "build_review_provenance",
    )
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "今天和小明一起完成了公开排练"
    validated = project_factual_event(
        raw_summary=raw,
        projection_input=_valid_projection_input(raw_summary=raw),
    )
    arc = SimpleNamespace(arc_id="arc-main", stage="live", revision=2, partner_states={})
    provenance = build_review_provenance(
        arc=arc,
        advanced_context_included=False,
        public_projection=validated,
    )
    assert provenance["schema_version"] == 2
    assert "public_projection" in provenance
    assert provenance["public_projection"]["public_template_id"] == (
        "social_public_event_solo_v1"
    )
    blob = json.dumps(provenance, ensure_ascii=False)
    assert "小明" not in blob
    assert "user:10001" not in blob
    fiction_prov = build_review_provenance(arc=arc, advanced_context_included=False)
    assert fiction_prov["schema_version"] == 1
    assert "public_projection" not in fiction_prov


@pytest.mark.asyncio
async def test_composer_sees_only_projected_summary() -> None:
    JournalComposer = _req("plugins.qzone_journal.composer", "JournalComposer")
    project_factual_event = _req(
        "plugins.qzone_journal.public_projection",
        "project_factual_event",
    )
    raw = "今天和小明一起完成了公开排练"
    validated = project_factual_event(
        raw_summary=raw,
        projection_input=_valid_projection_input(raw_summary=raw),
    )
    captured: list[Any] = []

    async def llm_probe(request: Any) -> dict[str, str]:
        captured.append(request)
        return {"text": "今天和一位朋友一起参加了公开活动。"}

    composer = JournalComposer(llm_probe)
    candidate = _candidate(
        subject_kind="factual",
        summary=validated.projected_summary,
        public_projection=validated,
        salience=0.9,
    )
    body = await composer.compose(candidate)
    assert body == validated.projected_summary
    assert "小明" not in body
    assert captured == [], "factual composer must not call LLM"


# ---------------------------------------------------------------------------
# 6. Version + validated gate
# ---------------------------------------------------------------------------


def test_qzone_version_is_0_8_2() -> None:
    from plugins.qzone_journal.plugin import QZoneJournalPlugin

    assert QZoneJournalPlugin.version == "0.8.2"
    manifest = json.loads(
        Path("plugins/qzone_journal/plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == "0.8.2"


def test_builtin_wire_profile_remains_unvalidated() -> None:
    from plugins.qzone_journal.delivery import BUILTIN_WIRE_PROFILE

    assert BUILTIN_WIRE_PROFILE.validated is False


def test_selection_reason_includes_public_projection_reject() -> None:
    codes = _req("plugins.qzone_journal.selector", "SELECTION_REASON_CODES")
    assert "reject_public_projection" in codes


def test_cjk_heuristic_helpers_removed() -> None:
    """v0.7 must not keep free-form CJK unbound-name guessing helpers."""
    mod = _projection_mod()
    for name in (
        "_find_unbound_private_surfaces",
        "_cjk_name_prefix",
        "_token_after_conj_is_bound",
        "_NARRATIVE_STOPWORDS",
        "_SOCIAL_CONJ_RE",
    ):
        assert getattr(mod, name, None) is None, f"{name} must be removed"
