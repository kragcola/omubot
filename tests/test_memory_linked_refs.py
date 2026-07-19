"""RED behavior tests for Episode linked refs public contract.

Expected public API (services.memory.linked_refs):
- LinkedMemoryRef immutable value object (kind, target_id, canonical)
- make_linked_ref(kind=, target_id=)
- parse_linked_ref(value)
- normalize_linked_refs(values, preserve_legacy=True) -> tuple[str, ...]
- linked_ref_evidence(values) -> tuple/list of deduplicated canonical strings

These tests intentionally target the contract before production code exists.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# LinkedMemoryRef / make_linked_ref — typed kinds
# ---------------------------------------------------------------------------


def test_entity_user_target_canonicalizes_via_entity_identity() -> None:
    """entity target user:qq:00123 becomes entity:user:qq:123 (leading zeros strip)."""
    from services.memory.linked_refs import LinkedMemoryRef, make_linked_ref

    ref = make_linked_ref(kind="entity", target_id="user:qq:00123")
    assert isinstance(ref, LinkedMemoryRef)
    assert ref.kind == "entity"
    assert ref.canonical == "entity:user:qq:123"
    # target_id is the post-canonicalization entity key (without entity: prefix)
    assert ref.target_id == "user:qq:123"


def test_entity_scoped_concept_key_round_trips() -> None:
    """Scoped concept keys produced by Entity Identity round-trip as entity refs."""
    from services.memory.entity_identity import entity_ref_from_surface
    from services.memory.linked_refs import make_linked_ref, parse_linked_ref

    concept = entity_ref_from_surface(subject="小明", scope="group", scope_id="A")
    ref = make_linked_ref(kind="entity", target_id=concept.entity_key)

    assert ref.kind == "entity"
    assert ref.canonical == f"entity:{concept.entity_key}"
    assert ref.target_id == concept.entity_key

    parsed = parse_linked_ref(ref.canonical)
    assert parsed is not None
    assert parsed.kind == "entity"
    assert parsed.target_id == concept.entity_key
    assert parsed.canonical == ref.canonical


@pytest.mark.parametrize(
    ("kind", "target_id", "canonical"),
    [
        ("card", "card_1", "card:card_1"),
        ("fact", "fact_42", "fact:fact_42"),
        ("episode", "ep_abc", "episode:ep_abc"),
        ("message", "msg-9", "message:msg-9"),
    ],
)
def test_typed_non_empty_ids_produce_kind_colon_id(
    kind: str, target_id: str, canonical: str
) -> None:
    from services.memory.linked_refs import make_linked_ref, parse_linked_ref

    ref = make_linked_ref(kind=kind, target_id=target_id)
    assert ref.kind == kind
    assert ref.target_id == target_id
    assert ref.canonical == canonical

    parsed = parse_linked_ref(canonical)
    assert parsed is not None
    assert parsed.kind == kind
    assert parsed.target_id == target_id
    assert parsed.canonical == canonical


def test_message_pk_accepts_positive_int_and_numeric_string_strips_leading_zeros() -> None:
    from services.memory.linked_refs import make_linked_ref, parse_linked_ref

    as_int = make_linked_ref(kind="message_pk", target_id=42)
    as_str = make_linked_ref(kind="message_pk", target_id="00042")
    as_plain = make_linked_ref(kind="message_pk", target_id="7")

    assert as_int.kind == "message_pk"
    assert as_int.canonical == "message_pk:42"
    assert as_int.target_id in (42, "42")

    assert as_str.canonical == "message_pk:42"
    assert as_str.target_id in (42, "42")

    assert as_plain.canonical == "message_pk:7"

    parsed = parse_linked_ref("message_pk:00099")
    assert parsed is not None
    assert parsed.kind == "message_pk"
    assert parsed.canonical == "message_pk:99"


# ---------------------------------------------------------------------------
# parse_linked_ref
# ---------------------------------------------------------------------------


def test_parse_accepts_compact_string_and_object_forms() -> None:
    from services.memory.linked_refs import parse_linked_ref

    from_str = parse_linked_ref("card:card_1")
    assert from_str is not None
    assert from_str.kind == "card"
    assert from_str.target_id == "card_1"
    assert from_str.canonical == "card:card_1"

    # Object form uses type/id keys (LLM / JSON card style).
    from_obj = parse_linked_ref({"type": "card", "id": "card_1"})
    assert from_obj is not None
    assert from_obj.kind == "card"
    assert from_obj.target_id == "card_1"
    assert from_obj.canonical == "card:card_1"

    from_obj_fact = parse_linked_ref({"type": "fact", "id": "f9"})
    assert from_obj_fact is not None
    assert from_obj_fact.canonical == "fact:f9"

    from_obj_entity = parse_linked_ref({"type": "entity", "id": "user:qq:00123"})
    assert from_obj_entity is not None
    assert from_obj_entity.canonical == "entity:user:qq:123"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        ":",
        "unknown:x",
        "card:",  # empty target after kind
        "card:   ",  # whitespace-only target
        123,
        3.14,
        True,
        ["card:card_1"],
        {"kind": "card", "id": "card_1"},  # wrong key: type required, not kind
        {"type": "card"},  # missing id
        {"type": "nope", "id": "x"},
        {"type": "", "id": "x"},
        {"id": "card_1"},  # missing type
        {},
    ],
)
def test_parse_returns_none_for_invalid_empty_unknown_or_wrong_types(value: object) -> None:
    from services.memory.linked_refs import parse_linked_ref

    assert parse_linked_ref(value) is None


def test_bare_legacy_string_parses_as_legacy_kind() -> None:
    from services.memory.linked_refs import parse_linked_ref

    ref = parse_linked_ref("mem_1")
    assert ref is not None
    assert ref.kind == "legacy"
    assert ref.target_id == "mem_1"
    assert ref.canonical == "legacy:mem_1"

    # Explicit legacy: prefix is also accepted and canonical.
    explicit = parse_linked_ref("legacy:mem_1")
    assert explicit is not None
    assert explicit.kind == "legacy"
    assert explicit.target_id == "mem_1"
    assert explicit.canonical == "legacy:mem_1"


# ---------------------------------------------------------------------------
# normalize_linked_refs
# ---------------------------------------------------------------------------


def test_normalize_dedupes_first_seen_drops_invalid_canonicalizes_typed() -> None:
    from services.memory.linked_refs import normalize_linked_refs

    values = [
        "card:card_1",
        {"type": "card", "id": "card_1"},  # duplicate of first
        "fact:f1",
        "",  # invalid
        None,  # invalid
        "unknown:x",  # invalid
        "entity:user:qq:00123",  # canonicalize zeros
        "message_pk:0007",
        "card:card_1",  # again
        "episode:ep1",
    ]
    out = normalize_linked_refs(values, preserve_legacy=True)
    assert isinstance(out, tuple)
    assert out == (
        "card:card_1",
        "fact:f1",
        "entity:user:qq:123",
        "message_pk:7",
        "episode:ep1",
    )


def test_normalize_preserve_legacy_true_keeps_bare_storage_form() -> None:
    from services.memory.linked_refs import normalize_linked_refs

    out = normalize_linked_refs(
        ["mem_1", "legacy:mem_1", "card:c1", "mem_1"],
        preserve_legacy=True,
    )
    # Bare legacy storage kept as mem_1; explicit legacy: and bare share identity
    # for dedupe, first-seen bare form wins.
    assert out == ("mem_1", "card:c1")


def test_normalize_preserve_legacy_false_stores_legacy_prefix() -> None:
    from services.memory.linked_refs import normalize_linked_refs

    out = normalize_linked_refs(
        ["mem_1", "legacy:mem_2", "card:c1"],
        preserve_legacy=False,
    )
    assert out == ("legacy:mem_1", "legacy:mem_2", "card:c1")


def test_normalize_default_preserve_legacy_is_true() -> None:
    from services.memory.linked_refs import normalize_linked_refs

    out = normalize_linked_refs(["mem_1"])
    assert out == ("mem_1",)


# ---------------------------------------------------------------------------
# linked_ref_evidence
# ---------------------------------------------------------------------------


def test_linked_ref_evidence_always_canonical_and_deduped() -> None:
    from services.memory.linked_refs import linked_ref_evidence

    values = [
        "mem_1",
        "legacy:mem_1",  # same identity as bare mem_1
        "card:card_1",
        {"type": "card", "id": "card_1"},
        "entity:user:qq:00123",
        "message_pk:0007",
        "",  # drop
        "unknown:x",  # drop
    ]
    evidence = linked_ref_evidence(values)
    # Evidence always uses canonical form, including legacy:mem_1.
    assert list(evidence) == [
        "legacy:mem_1",
        "card:card_1",
        "entity:user:qq:123",
        "message_pk:7",
    ]
    assert len(list(evidence)) == len(set(list(evidence)))


# ---------------------------------------------------------------------------
# Whitespace / zero / negative invalid targets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    ["entity", "card", "fact", "message_pk", "message", "episode"],
)
def test_whitespace_only_targets_are_invalid(kind: str) -> None:
    from services.memory.linked_refs import make_linked_ref, parse_linked_ref

    with pytest.raises((ValueError, TypeError)):
        make_linked_ref(kind=kind, target_id="   ")

    with pytest.raises((ValueError, TypeError)):
        make_linked_ref(kind=kind, target_id="")

    assert parse_linked_ref(f"{kind}:   ") is None
    assert parse_linked_ref(f"{kind}:") is None
    assert parse_linked_ref({"type": kind, "id": "  "}) is None
    assert parse_linked_ref({"type": kind, "id": ""}) is None


@pytest.mark.parametrize("bad_pk", [0, -1, -99, "0", "-1", "00", "-007"])
def test_message_pk_zero_and_negative_are_invalid(bad_pk: object) -> None:
    from services.memory.linked_refs import make_linked_ref, parse_linked_ref

    with pytest.raises((ValueError, TypeError)):
        make_linked_ref(kind="message_pk", target_id=bad_pk)  # type: ignore[arg-type]

    if isinstance(bad_pk, (str, int)):
        assert parse_linked_ref(f"message_pk:{bad_pk}") is None


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_linked_memory_ref_is_immutable() -> None:
    from services.memory.linked_refs import LinkedMemoryRef, make_linked_ref

    ref = make_linked_ref(kind="card", target_id="card_1")
    assert isinstance(ref, LinkedMemoryRef)

    with pytest.raises((AttributeError, TypeError)):
        ref.kind = "fact"  # type: ignore[misc]

    with pytest.raises((AttributeError, TypeError)):
        ref.target_id = "other"  # type: ignore[misc]

    with pytest.raises((AttributeError, TypeError)):
        ref.canonical = "card:other"  # type: ignore[misc]
