"""RED behavior tests for Entity Identity v1 public contract.

Expected public API (services.memory.entity_identity):
- EntityRef immutable value object
- make_entity_ref(...)
- entity_ref_from_surface(...)
- parse_entity_key(...)

These tests intentionally target the contract before production code exists.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Pure contract: make_entity_ref / EntityRef
# ---------------------------------------------------------------------------


def test_platform_user_key_stable_across_display_changes() -> None:
    from services.memory.entity_identity import make_entity_ref

    ref = make_entity_ref(
        kind="user",
        scope="user",
        scope_id="123456",
        display="小明",
        platform_id="123456",
    )
    assert ref.entity_key == "user:qq:123456"
    assert ref.kind == "user"
    assert ref.display == "小明"

    renamed = make_entity_ref(
        kind="user",
        scope="user",
        scope_id="123456",
        display="阿明",
        platform_id="123456",
        aliases=("小明",),
    )
    assert renamed.entity_key == "user:qq:123456"
    assert renamed.entity_key == ref.entity_key
    assert renamed.display == "阿明"


def test_group_kind_maps_platform_id_to_group_qq_key() -> None:
    from services.memory.entity_identity import make_entity_ref

    ref = make_entity_ref(
        kind="group",
        scope="group",
        scope_id="9988",
        display="测试群",
        platform_id="9988",
    )
    assert ref.entity_key == "group:qq:9988"
    assert ref.kind == "group"
    assert ref.scope == "group"
    assert ref.scope_id == "9988"


def test_concept_surface_keys_include_scope_boundaries() -> None:
    from services.memory.entity_identity import entity_ref_from_surface

    in_a = entity_ref_from_surface(subject="小明", scope="group", scope_id="A")
    in_b = entity_ref_from_surface(subject="小明", scope="group", scope_id="B")
    same_a = entity_ref_from_surface(subject="小明", scope="group", scope_id="A")
    normalized_a = entity_ref_from_surface(
        subject="  小明  ", scope="group", scope_id="A"
    )

    assert in_a.entity_key != in_b.entity_key
    assert in_a.entity_key == same_a.entity_key
    assert in_a.entity_key == normalized_a.entity_key
    # Scope boundary must appear in both keys so cross-scope collision is impossible.
    assert "A" in in_a.entity_key
    assert "B" in in_b.entity_key
    assert in_a.kind == "concept"
    assert in_b.kind == "concept"
    assert in_a.entity_key.startswith("concept:")
    assert in_b.entity_key.startswith("concept:")


def test_bare_digit_surface_is_scoped_concept_not_platform_user() -> None:
    """Bare digits are ambiguous: scoped concept, never user:qq without a prefix."""
    from services.memory.entity_identity import entity_ref_from_surface

    bare = entity_ref_from_surface(
        subject="123456", scope="group", scope_id="g1"
    )
    nickname = entity_ref_from_surface(
        subject="阿明", scope="group", scope_id="g1"
    )

    assert bare.kind == "concept"
    assert bare.entity_key.startswith("concept:")
    assert "g1" in bare.entity_key
    assert bare.entity_key != "user:qq:123456"
    assert not bare.entity_key.startswith("user:qq:")

    # Nickname without an explicit platform prefix remains a scoped concept.
    assert nickname.kind == "concept"
    assert nickname.entity_key != "user:qq:123456"
    assert "g1" in nickname.entity_key
    assert nickname.entity_key.startswith("concept:")


def test_explicit_user_prefix_resolves_to_platform_user_and_strips_leading_zeros() -> None:
    """Only explicit 用户 prefix identifies a platform user; leading zeros strip."""
    from services.memory.entity_identity import entity_ref_from_surface

    padded = entity_ref_from_surface(
        subject="用户  001234", scope="group", scope_id="g1"
    )
    compact = entity_ref_from_surface(
        subject="用户123456", scope="group", scope_id="g1"
    )

    assert padded.kind == "user"
    assert padded.entity_key == "user:qq:1234"
    assert compact.kind == "user"
    assert compact.entity_key == "user:qq:123456"


def test_explicit_group_prefix_resolves_to_platform_group_and_strips_leading_zeros() -> None:
    """Only explicit 群 prefix identifies a platform group; leading zeros strip."""
    from services.memory.entity_identity import entity_ref_from_surface

    ref = entity_ref_from_surface(
        subject="群  009988", scope="group", scope_id="g1"
    )
    assert ref.kind == "group"
    assert ref.entity_key == "group:qq:9988"


def test_make_entity_ref_canonicalizes_platform_id_leading_zeros() -> None:
    """make_entity_ref strips leading zeros on numeric platform_id for user/group."""
    from services.memory.entity_identity import make_entity_ref

    user = make_entity_ref(
        kind="user",
        scope="user",
        scope_id="001234",
        display="x",
        platform_id="001234",
    )
    group = make_entity_ref(
        kind="group",
        scope="group",
        scope_id="009988",
        display="测试群",
        platform_id="009988",
    )
    assert user.entity_key == "user:qq:1234"
    assert group.entity_key == "group:qq:9988"


def test_punctuation_only_surface_yields_stable_scoped_concept() -> None:
    """Non-empty punctuation-only surfaces must not raise; key is scoped & stable."""
    from services.memory.entity_identity import (
        entity_ref_from_surface,
        parse_entity_key,
    )

    in_a = entity_ref_from_surface(
        subject="!!!", scope="group", scope_id="g1"
    )
    same_a = entity_ref_from_surface(
        subject="!!!", scope="group", scope_id="g1"
    )
    in_b = entity_ref_from_surface(
        subject="!!!", scope="group", scope_id="g2"
    )

    assert in_a.kind == "concept"
    assert in_a.entity_key.startswith("concept:")
    assert in_a.entity_key == same_a.entity_key
    assert in_a.entity_key != in_b.entity_key
    assert "g1" in in_a.entity_key
    assert "g2" in in_b.entity_key

    parsed = parse_entity_key(in_a.entity_key)
    assert parsed is not None
    assert parsed.kind == "concept"


def test_empty_concept_display_raises_value_error() -> None:
    """Truly empty concept display remains invalid."""
    from services.memory.entity_identity import (
        entity_ref_from_surface,
        make_entity_ref,
    )

    with pytest.raises(ValueError):
        entity_ref_from_surface(subject="", scope="group", scope_id="g1")

    with pytest.raises(ValueError):
        entity_ref_from_surface(subject="   ", scope="group", scope_id="g1")

    with pytest.raises(ValueError):
        make_entity_ref(
            kind="concept",
            scope="group",
            scope_id="g1",
            display="",
        )


def test_aliases_and_storage_refs_dedup_preserve_order_and_are_tuples() -> None:
    from services.memory.entity_identity import EntityRef, make_entity_ref

    ref = make_entity_ref(
        kind="user",
        scope="user",
        scope_id="123456",
        display="小明",
        platform_id="123456",
        aliases=["小明", "阿明", "小明", "阿明", "帆"],
        storage_refs=["card:1", "memo:x", "card:1", "graph:y", "memo:x"],
    )

    assert isinstance(ref, EntityRef)
    assert isinstance(ref.aliases, tuple)
    assert isinstance(ref.storage_refs, tuple)
    assert ref.aliases == ("小明", "阿明", "帆")
    assert ref.storage_refs == ("card:1", "memo:x", "graph:y")

    with pytest.raises((AttributeError, TypeError)):
        ref.display = "被改了"  # type: ignore[misc]

    with pytest.raises((AttributeError, TypeError)):
        ref.entity_key = "user:qq:0"  # type: ignore[misc]


def test_parse_entity_key_accepts_v1_forms_rejects_legacy_and_malformed() -> None:
    from services.memory.entity_identity import (
        entity_ref_from_surface,
        make_entity_ref,
        parse_entity_key,
    )

    user_parsed = parse_entity_key("user:qq:123456")
    group_parsed = parse_entity_key("group:qq:9988")
    concept_key = entity_ref_from_surface(
        subject="小明", scope="group", scope_id="A"
    ).entity_key
    concept_parsed = parse_entity_key(concept_key)

    assert user_parsed is not None
    assert user_parsed.kind == "user"
    assert group_parsed is not None
    assert group_parsed.kind == "group"
    assert concept_parsed is not None
    assert concept_parsed.kind == "concept"

    # Round-trip identity for platform keys produced by the factory.
    built = make_entity_ref(
        kind="user",
        scope="user",
        scope_id="123456",
        display="x",
        platform_id="123456",
    )
    rebuilt = parse_entity_key(built.entity_key)
    assert rebuilt is not None
    assert rebuilt.kind == "user"

    # Legacy ContextProvenance-style forms and garbage must not raise; return None.
    assert parse_entity_key("user:123:preference") is None
    assert parse_entity_key("user:123:fact") is None
    assert parse_entity_key("group:456:note") is None
    assert parse_entity_key("") is None
    assert parse_entity_key("not-a-key") is None
    assert parse_entity_key("user:") is None
    assert parse_entity_key("user:qq:") is None
    assert parse_entity_key(":qq:1") is None


def test_entity_ref_exposes_required_fields() -> None:
    from services.memory.entity_identity import make_entity_ref

    ref = make_entity_ref(
        kind="user",
        scope="user",
        scope_id="99",
        display="测试",
        platform_id="99",
        aliases=(),
        storage_refs=(),
    )
    for field in (
        "kind",
        "scope",
        "scope_id",
        "entity_key",
        "display",
        "aliases",
        "storage_refs",
    ):
        assert hasattr(ref, field), f"EntityRef missing field {field!r}"


# ---------------------------------------------------------------------------
# Integration RED (public constructors only; skip if import surface missing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_context_provenance_entity_key_for_user_card(tmp_path) -> None:
    """User card provenance entity_key is user:qq:<scope_id>; category stays metadata."""
    pytest.importorskip("services.memory.entity_identity")

    from services.context import ContextService, MemoryContextSource
    from services.memory.card_store import CardStore, NewCard

    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        card_id = await store.add_card(
            NewCard(
                category="preference",
                scope="user",
                scope_id="123456",
                content="喜欢被叫阿明",
            )
        )
        service = ContextService([MemoryContextSource(store)])
        hits = await service.search("阿明", user_id="123456", top_k=5)
        hit = next(item for item in hits if item.id == card_id)

        assert hit.provenance is not None
        assert hit.provenance.entity_key == "user:qq:123456"
        # Category must not be baked into the entity identity key.
        assert "preference" not in hit.provenance.entity_key
        assert hit.metadata.get("category") == "preference"
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Graph integration RED: automatic KnowledgeGraph identity metadata + GraphContext
# Public submit_fact_candidate signature is unchanged — callers do not pass keys.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_fact_candidate_auto_persists_entity_identity_metadata(
    tmp_path,
) -> None:
    """promote_directly facts auto-attach v1 subject/object entity keys in metadata."""
    from services.knowledge_graph import GraphFact, KnowledgeGraphService
    from services.memory.entity_identity import entity_ref_from_surface

    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        expected_object_key = entity_ref_from_surface(
            subject="音游", scope="group", scope_id="g1"
        ).entity_key
        assert expected_object_key.startswith("concept:group:g1:")

        fact = await graph.submit_fact_candidate(
            subject="用户123456",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"type": "fixture", "id": "card_auto_identity"},
            scope="group",
            scope_id="g1",
            promote_directly=True,
        )
        assert isinstance(fact, GraphFact)
        meta = dict(getattr(fact, "metadata", None) or {})
        assert meta.get("entity_identity_version") == 1
        assert meta.get("subject_entity_key") == "user:qq:123456"
        assert meta.get("object_entity_key") == expected_object_key

        # Persistence: list/get path must round-trip the same keys.
        listed = await graph.list_relationships(limit=20)
        match = next(
            item
            for item in listed
            if item.get("fact_id") == getattr(fact, "fact_id", None)
        )
        listed_meta = dict(match.get("metadata") or {})
        assert listed_meta.get("entity_identity_version") == 1
        assert listed_meta.get("subject_entity_key") == "user:qq:123456"
        assert listed_meta.get("object_entity_key") == expected_object_key
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_hit_exposes_entity_keys_from_auto_metadata(
    tmp_path,
) -> None:
    """GraphContextSource hit prefers subject entity key; exposes keys at top level."""
    from services.context import ContextService, GraphContextSource
    from services.knowledge_graph import GraphFact, KnowledgeGraphService
    from services.memory.entity_identity import entity_ref_from_surface

    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        expected_object_key = entity_ref_from_surface(
            subject="音游", scope="group", scope_id="g1"
        ).entity_key

        fact = await graph.submit_fact_candidate(
            subject="用户123456",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"type": "fixture", "id": "card_graph_hit"},
            scope="group",
            scope_id="g1",
            promote_directly=True,
        )
        assert isinstance(fact, GraphFact)

        service = ContextService([GraphContextSource(graph)])
        hits = await service.search("音游", group_id="g1", top_k=5)
        assert hits
        hit = next(item for item in hits if item.id == fact.fact_id)

        assert hit.provenance is not None
        assert hit.provenance.entity_key == "user:qq:123456"

        # Top-level observability fields (not only nested under metadata.metadata).
        assert hit.metadata.get("subject_entity_key") == "user:qq:123456"
        assert hit.metadata.get("object_entity_key") == expected_object_key
        # Existing graph fields retained for observability.
        assert hit.metadata.get("subject") == "用户123456"
        assert hit.metadata.get("predicate") == "喜欢"
        assert hit.metadata.get("object") == "音游"
        assert hit.metadata.get("graph_hop") == 0
        assert hit.metadata.get("direct_match") is True
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_legacy_relationship_without_metadata_derives_entity_key() -> None:
    """Backward compat: missing identity metadata still yields a hit with derived key."""
    from services.context import ContextService, GraphContextSource
    from services.memory.entity_identity import entity_ref_from_surface

    class _FakeGraph:
        async def list_relationships(self, *, limit: int = 200) -> list[dict]:
            del limit
            # Deliberately no metadata / no entity keys — legacy relationship shape.
            return [
                {
                    "fact_id": "gf_legacy_no_meta",
                    "subject": "无关用户",
                    "predicate": "提到",
                    "object": "测试词",
                    "confidence": 0.85,
                    "status": "active",
                    "source": "legacy_fixture",
                    "scope": "group",
                    "scope_id": "g1",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "supersedes": None,
                    "evidence": [],
                }
            ]

    expected_subject_key = entity_ref_from_surface(
        subject="无关用户", scope="group", scope_id="g1"
    ).entity_key

    service = ContextService([GraphContextSource(_FakeGraph())])
    hits = await service.search("测试词", group_id="g1", top_k=5)
    assert hits, "legacy relationship without metadata must still return a hit"
    hit = next(item for item in hits if item.id == "gf_legacy_no_meta")
    assert hit.provenance is not None
    assert hit.provenance.entity_key == expected_subject_key
    assert hit.provenance.entity_key.startswith("concept:group:g1:")
    # Must not raise; must not keep the legacy scope:scope_id:subject:predicate form.
    assert hit.provenance.entity_key.count(":") >= 2
    assert not hit.provenance.entity_key.endswith(":提到")


@pytest.mark.asyncio
async def test_graph_multi_hop_excludes_surface_collision_without_entity_key_overlap() -> None:
    """Multi-hop may expand via shared hub entity keys; surface-name collisions stay out.

    Two direct facts for surface 小明 carry subject_entity_key user:qq:1 and a
    shared hub object key. A bridge sharing the hub may expand. Another fact also
    displays subject 小明 but carries user:qq:2 with no canonical-key overlap and
    must not enter results merely because the surface matches.
    """
    from services.context import ContextService, GraphContextSource
    from services.memory.entity_identity import entity_ref_from_surface

    hub_key = entity_ref_from_surface(
        subject="中枢节点", scope="group", scope_id="g1"
    ).entity_key
    bridge_object_key = entity_ref_from_surface(
        subject="目标资料", scope="group", scope_id="g1"
    ).entity_key
    collision_object_key = entity_ref_from_surface(
        subject="金鱼", scope="group", scope_id="g1"
    ).entity_key

    def _rel(
        *,
        fact_id: str,
        subject: str,
        predicate: str,
        object_: str,
        subject_entity_key: str,
        object_entity_key: str,
    ) -> dict:
        return {
            "fact_id": fact_id,
            "subject": subject,
            "predicate": predicate,
            "object": object_,
            "confidence": 0.9,
            "status": "active",
            "source": "collision_fixture",
            "scope": "group",
            "scope_id": "g1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "supersedes": None,
            "evidence": [],
            "metadata": {
                "entity_identity_version": 1,
                "subject_entity_key": subject_entity_key,
                "object_entity_key": object_entity_key,
            },
            # Top-level keys for hop expansion / observability when present.
            "subject_entity_key": subject_entity_key,
            "object_entity_key": object_entity_key,
        }

    relationships = [
        _rel(
            fact_id="gf_direct_theme",
            subject="小明",
            predicate="关注主题",
            object_="中枢节点",
            subject_entity_key="user:qq:1",
            object_entity_key=hub_key,
        ),
        _rel(
            fact_id="gf_direct_collect",
            subject="小明",
            predicate="收藏资料",
            object_="中枢节点",
            subject_entity_key="user:qq:1",
            object_entity_key=hub_key,
        ),
        _rel(
            fact_id="gf_bridge_hub",
            subject="中枢节点",
            predicate="桥接",
            object_="目标资料",
            subject_entity_key=hub_key,
            object_entity_key=bridge_object_key,
        ),
        # Surface collision: same display 小明, different identity, no key overlap.
        _rel(
            fact_id="gf_collision_other_user",
            subject="小明",
            predicate="养了",
            object_="金鱼",
            subject_entity_key="user:qq:2",
            object_entity_key=collision_object_key,
        ),
    ]

    class _FakeGraph:
        async def list_relationships(self, *, limit: int = 200) -> list[dict]:
            return relationships[:limit]

    # Query matches the two direct predicates; omit 小明 so the collision fact
    # is not a direct lexical hit and can only enter via multi-hop expansion.
    service = ContextService([GraphContextSource(_FakeGraph(), max_hops=2)])
    hits = await service.search("关注主题 收藏资料", group_id="g1", top_k=8)
    hit_ids = {hit.id for hit in hits}

    assert "gf_direct_theme" in hit_ids
    assert "gf_direct_collect" in hit_ids
    # Bridge may expand because it shares the hub entity key with the seeds.
    assert "gf_bridge_hub" in hit_ids
    # Collision fact must not enter solely because surface subject is 小明.
    assert "gf_collision_other_user" not in hit_ids
    assert all("金鱼" not in hit.content for hit in hits)


# ---------------------------------------------------------------------------
# Remaining active-fact write paths: approval + supersede rename continuity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_candidate_persists_entity_identity_metadata(
    tmp_path,
) -> None:
    """Approval of a pending candidate must stamp v1 subject/object entity keys."""
    from services.knowledge_graph import GraphCandidate, GraphFact, KnowledgeGraphService
    from services.memory.entity_identity import entity_ref_from_surface

    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        expected_object_key = entity_ref_from_surface(
            subject="音游", scope="group", scope_id="g1"
        ).entity_key
        assert expected_object_key.startswith("concept:group:g1:")

        candidate = await graph.submit_fact_candidate(
            subject="用户123456",
            predicate="喜欢",
            object="音游",
            confidence=0.9,
            source="test",
            evidence={"type": "fixture", "id": "card_approve_identity"},
            scope="group",
            scope_id="g1",
        )
        assert isinstance(candidate, GraphCandidate)
        assert await graph.list_relationships() == []

        fact = await graph.approve_candidate(candidate.candidate_id)
        assert isinstance(fact, GraphFact)
        meta = dict(getattr(fact, "metadata", None) or {})
        assert meta.get("entity_identity_version") == 1
        assert meta.get("subject_entity_key") == "user:qq:123456"
        assert meta.get("object_entity_key") == expected_object_key
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_supersede_preserves_subject_entity_key_across_display_rename(
    tmp_path,
) -> None:
    """Supersede may rename display surfaces but must keep subject entity continuity.

    Replacement object_entity_key updates to the new object surface; GraphContext
    hits expose provenance.entity_key for the preserved subject; superseded fact
    stays out of active hits.
    """
    from services.context import ContextService, GraphContextSource
    from services.knowledge_graph import GraphFact, KnowledgeGraphService
    from services.memory.entity_identity import entity_ref_from_surface

    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        expected_shanghai_key = entity_ref_from_surface(
            subject="上海", scope="group", scope_id="g1"
        ).entity_key
        assert expected_shanghai_key.startswith("concept:group:g1:")

        old = await graph.submit_fact_candidate(
            subject="用户123456",
            predicate="居住地",
            object="杭州",
            confidence=0.9,
            source="test",
            evidence={"type": "fixture", "id": "card_supersede_old"},
            scope="group",
            scope_id="g1",
            promote_directly=True,
        )
        assert isinstance(old, GraphFact)
        old_meta = dict(getattr(old, "metadata", None) or {})
        assert old_meta.get("subject_entity_key") == "user:qq:123456"

        replacement = await graph.supersede_relationship(
            old.fact_id,
            subject="阿明",
            predicate="居住地",
            object="上海",
            confidence=0.95,
            source="memory_update",
            evidence={
                "type": "fixture",
                "id": "card_supersede_new",
                "quote": "阿明现在住在上海",
            },
            note="display rename + city update",
        )
        assert isinstance(replacement, GraphFact)
        assert replacement.subject == "阿明"
        assert replacement.object == "上海"

        rep_meta = dict(getattr(replacement, "metadata", None) or {})
        # Subject renamed on the surface, but platform identity must not change.
        assert rep_meta.get("entity_identity_version") == 1
        assert rep_meta.get("subject_entity_key") == "user:qq:123456"
        assert rep_meta.get("object_entity_key") == expected_shanghai_key

        service = ContextService([GraphContextSource(graph)])
        hits = await service.search("阿明 居住地 上海", group_id="g1", top_k=8)
        assert any(hit.id == replacement.fact_id for hit in hits)
        assert all(hit.id != old.fact_id for hit in hits)

        hit = next(item for item in hits if item.id == replacement.fact_id)
        assert hit.provenance is not None
        assert hit.provenance.entity_key == "user:qq:123456"
        assert hit.metadata.get("subject_entity_key") == "user:qq:123456"
        assert hit.metadata.get("object_entity_key") == expected_shanghai_key
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_supersede_explicit_platform_subject_reassigns_entity_key(
    tmp_path,
) -> None:
    """Explicit platform subject change must reassign identity, not preserve old key.

    Display-only rename (阿明) keeps user:qq:123456; switching subject surface to
    用户999 means a different platform subject and must stamp user:qq:999.
    """
    from services.context import ContextService, GraphContextSource
    from services.knowledge_graph import GraphFact, KnowledgeGraphService
    from services.memory.entity_identity import entity_ref_from_surface

    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        expected_object_key = entity_ref_from_surface(
            subject="上海", scope="group", scope_id="g1"
        ).entity_key

        old = await graph.submit_fact_candidate(
            subject="用户123456",
            predicate="居住地",
            object="杭州",
            confidence=0.9,
            source="test",
            evidence={"type": "fixture", "id": "card_supersede_reassign_old"},
            scope="group",
            scope_id="g1",
            promote_directly=True,
        )
        assert isinstance(old, GraphFact)
        old_meta = dict(getattr(old, "metadata", None) or {})
        assert old_meta.get("subject_entity_key") == "user:qq:123456"

        replacement = await graph.supersede_relationship(
            old.fact_id,
            subject="用户999",
            predicate="居住地",
            object="上海",
            confidence=0.95,
            source="memory_update",
            evidence={
                "type": "fixture",
                "id": "card_supersede_reassign_new",
                "quote": "用户999现在住在上海",
            },
            note="explicit platform subject reassignment",
        )
        assert isinstance(replacement, GraphFact)
        assert replacement.subject == "用户999"
        assert replacement.object == "上海"

        rep_meta = dict(getattr(replacement, "metadata", None) or {})
        assert rep_meta.get("entity_identity_version") == 1
        # Must reassign — not keep the superseded fact's platform identity.
        assert rep_meta.get("subject_entity_key") == "user:qq:999"
        assert rep_meta.get("subject_entity_key") != "user:qq:123456"
        assert rep_meta.get("object_entity_key") == expected_object_key

        service = ContextService([GraphContextSource(graph)])
        hits = await service.search("用户999 居住地 上海", group_id="g1", top_k=8)
        assert any(hit.id == replacement.fact_id for hit in hits)
        assert all(hit.id != old.fact_id for hit in hits)

        hit = next(item for item in hits if item.id == replacement.fact_id)
        assert hit.provenance is not None
        assert hit.provenance.entity_key == "user:qq:999"
        assert hit.metadata.get("subject_entity_key") == "user:qq:999"
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_supersede_legacy_fact_without_metadata_derives_subject_from_old(
    tmp_path,
) -> None:
    """Pre-v1 active fact with empty metadata: supersede derives subject from old.

    Nickname rename to 阿明 must not invent concept:...阿明; continuity comes from
    the legacy subject surface 用户123456 → user:qq:123456.
    """
    from services.context import ContextService, GraphContextSource
    from services.knowledge_graph import (
        GraphFact,
        KnowledgeGraphService,
        KnowledgeGraphStore,
    )
    from services.memory.entity_identity import entity_ref_from_surface

    db_path = tmp_path / "legacy_graph.db"
    store = KnowledgeGraphStore(db_path)
    await store.init()
    try:
        old = await store.add_fact(
            subject="用户123456",
            predicate="居住地",
            object="杭州",
            confidence=0.9,
            source="legacy_fixture",
            evidence={"type": "fixture", "id": "card_legacy_no_meta"},
            status="active",
            scope="group",
            scope_id="g1",
            metadata={},
        )
        assert isinstance(old, GraphFact)
        assert dict(getattr(old, "metadata", None) or {}) == {}
    finally:
        await store.close()

    graph = KnowledgeGraphService(db_path)
    await graph.init()
    try:
        expected_object_key = entity_ref_from_surface(
            subject="上海", scope="group", scope_id="g1"
        ).entity_key
        # Nickname-only surface without platform prefix is a scoped concept.
        concept_aming = entity_ref_from_surface(
            subject="阿明", scope="group", scope_id="g1"
        ).entity_key
        assert concept_aming.startswith("concept:")

        replacement = await graph.supersede_relationship(
            old.fact_id,
            subject="阿明",
            predicate="居住地",
            object="上海",
            confidence=0.95,
            source="memory_update",
            evidence={
                "type": "fixture",
                "id": "card_legacy_supersede_new",
                "quote": "阿明现在住在上海",
            },
            note="legacy pre-v1 continuity via old subject surface",
        )
        assert isinstance(replacement, GraphFact)
        assert replacement.subject == "阿明"
        assert replacement.object == "上海"

        rep_meta = dict(getattr(replacement, "metadata", None) or {})
        assert rep_meta.get("entity_identity_version") == 1
        # Derive from old subject 用户123456, not from rename surface 阿明.
        assert rep_meta.get("subject_entity_key") == "user:qq:123456"
        assert rep_meta.get("subject_entity_key") != concept_aming
        assert not str(rep_meta.get("subject_entity_key") or "").startswith(
            "concept:"
        )
        assert rep_meta.get("object_entity_key") == expected_object_key

        service = ContextService([GraphContextSource(graph)])
        hits = await service.search("阿明 居住地 上海", group_id="g1", top_k=8)
        assert any(hit.id == replacement.fact_id for hit in hits)
        assert all(hit.id != old.fact_id for hit in hits)

        hit = next(item for item in hits if item.id == replacement.fact_id)
        assert hit.provenance is not None
        assert hit.provenance.entity_key == "user:qq:123456"
        assert hit.metadata.get("subject_entity_key") == "user:qq:123456"
    finally:
        await graph.close()


# ---------------------------------------------------------------------------
# Punctuation-only non-empty surfaces: write / approve / retrieval integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_directly_punctuation_subject_persists_parseable_concept_key(
    tmp_path,
) -> None:
    """promote_directly with subject !!! must succeed as GraphFact with v1 identity."""
    from services.knowledge_graph import GraphFact, KnowledgeGraphService
    from services.memory.entity_identity import parse_entity_key

    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        fact = await graph.submit_fact_candidate(
            subject="!!!",
            predicate="表示",
            object="警告",
            confidence=0.9,
            source="test",
            evidence={"type": "fixture", "id": "card_punct_promote"},
            scope="group",
            scope_id="g1",
            promote_directly=True,
        )
        assert isinstance(fact, GraphFact)
        assert fact.subject == "!!!"
        assert fact.predicate == "表示"
        assert fact.object == "警告"

        meta = dict(getattr(fact, "metadata", None) or {})
        assert meta.get("entity_identity_version") == 1
        subject_key = str(meta.get("subject_entity_key") or "")
        parsed = parse_entity_key(subject_key)
        assert parsed is not None
        assert parsed.kind == "concept"
        assert subject_key.startswith("concept:group:g1:")
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_approve_candidate_punctuation_object_persists_parseable_concept_key(
    tmp_path,
) -> None:
    """Pending candidate with object ... must approve without crashing; key parseable."""
    from services.knowledge_graph import GraphCandidate, GraphFact, KnowledgeGraphService
    from services.memory.entity_identity import parse_entity_key

    graph = KnowledgeGraphService(tmp_path / "graph.db")
    await graph.init()
    try:
        candidate = await graph.submit_fact_candidate(
            subject="系统",
            predicate="输出",
            object="...",
            confidence=0.9,
            source="test",
            evidence={"type": "fixture", "id": "card_punct_approve"},
            scope="group",
            scope_id="g1",
        )
        assert isinstance(candidate, GraphCandidate)
        assert candidate.object == "..."
        assert await graph.list_relationships() == []

        fact = await graph.approve_candidate(candidate.candidate_id)
        assert isinstance(fact, GraphFact)
        assert fact.subject == "系统"
        assert fact.predicate == "输出"
        assert fact.object == "..."

        meta = dict(getattr(fact, "metadata", None) or {})
        assert meta.get("entity_identity_version") == 1
        object_key = str(meta.get("object_entity_key") or "")
        parsed = parse_entity_key(object_key)
        assert parsed is not None
        assert parsed.kind == "concept"
        assert object_key.startswith("concept:group:g1:")
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_graph_context_search_tolerates_punctuation_fact_without_metadata() -> None:
    """Punctuation-only fact without metadata must not drop the whole graph source.

    Two direct seeds matching query 稳定事实 force hop expansion so
    _graph_entity_keys runs on the punctuation row (no metadata), reproducing
    the prior poison path while the good fact remains returned.
    """
    from services.context import ContextService, GraphContextSource
    from services.memory.entity_identity import entity_ref_from_surface

    good_subject_key = entity_ref_from_surface(
        subject="稳定事实", scope="group", scope_id="g1"
    ).entity_key
    good_object_key = entity_ref_from_surface(
        subject="可靠结果", scope="group", scope_id="g1"
    ).entity_key
    seed2_object_key = entity_ref_from_surface(
        subject="支撑节点", scope="group", scope_id="g1"
    ).entity_key

    def _rel(
        *,
        fact_id: str,
        subject: str,
        predicate: str,
        object_: str,
        metadata: dict | None = None,
        subject_entity_key: str | None = None,
        object_entity_key: str | None = None,
    ) -> dict:
        row: dict = {
            "fact_id": fact_id,
            "subject": subject,
            "predicate": predicate,
            "object": object_,
            "confidence": 0.9,
            "status": "active",
            "source": "punct_poison_fixture",
            "scope": "group",
            "scope_id": "g1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "supersedes": None,
            "evidence": [],
        }
        if metadata is not None:
            row["metadata"] = metadata
        if subject_entity_key is not None:
            row["subject_entity_key"] = subject_entity_key
        if object_entity_key is not None:
            row["object_entity_key"] = object_entity_key
        return row

    relationships = [
        _rel(
            fact_id="gf_stable_good",
            subject="稳定事实",
            predicate="说明",
            object_="可靠结果",
            metadata={
                "entity_identity_version": 1,
                "subject_entity_key": good_subject_key,
                "object_entity_key": good_object_key,
            },
            subject_entity_key=good_subject_key,
            object_entity_key=good_object_key,
        ),
        # Second direct seed so max_hops expansion runs and evaluates all rows.
        _rel(
            fact_id="gf_stable_seed2",
            subject="稳定事实",
            predicate="关联",
            object_="支撑节点",
            metadata={
                "entity_identity_version": 1,
                "subject_entity_key": good_subject_key,
                "object_entity_key": seed2_object_key,
            },
            subject_entity_key=good_subject_key,
            object_entity_key=seed2_object_key,
        ),
        # Punctuation-only surfaces, deliberately no identity metadata.
        _rel(
            fact_id="gf_punct_poison",
            subject="!!!",
            predicate="噪声",
            object_="...",
        ),
    ]

    class _FakeGraph:
        async def list_relationships(self, *, limit: int = 200) -> list[dict]:
            return relationships[:limit]

    service = ContextService([GraphContextSource(_FakeGraph(), max_hops=2)])
    hits = await service.search("稳定事实", group_id="g1", top_k=8)

    hit_ids = {hit.id for hit in hits}
    assert "gf_stable_good" in hit_ids
    # Good direct hits survive; punctuation row may or may not rank in, but
    # must not wipe the graph source via an uncaught exception.
    assert any("稳定事实" in hit.content for hit in hits)

    recent = service.recent(limit=1)
    assert recent, "ContextService must record the search"
    # Graph source must not be dropped: no recorded source exception.
    assert not recent[-1].get("error"), (
        f"graph source must not be poisoned by punctuation fact; "
        f"got error={recent[-1].get('error')!r}"
    )


@pytest.mark.asyncio
async def test_graph_context_malformed_top_level_key_defers_to_valid_nested_canonical(
) -> None:
    """Malformed top-level ghost key must not hide a valid nested metadata key.

    GraphContext entity-key resolution must examine all candidates. A broken
    top-level subject_entity_key must lose to nested metadata that carries a
    valid noncanonical platform key; the hit must expose the canonical form.
    """
    from services.context import ContextService, GraphContextSource
    from services.memory.entity_identity import entity_ref_from_surface

    expected_object_key = entity_ref_from_surface(
        subject="音游", scope="group", scope_id="g1"
    ).entity_key
    assert expected_object_key.startswith("concept:group:g1:")

    class _FakeGraph:
        async def list_relationships(self, *, limit: int = 200) -> list[dict]:
            del limit
            return [
                {
                    "fact_id": "gf_malformed_top_valid_nested",
                    "subject": "阿明",
                    "predicate": "喜欢",
                    "object": "音游",
                    "confidence": 0.9,
                    "status": "active",
                    "source": "malformed_top_fixture",
                    "scope": "group",
                    "scope_id": "g1",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "supersedes": None,
                    "evidence": [],
                    # Ghost top-level key: non-empty but unparseable.
                    "subject_entity_key": "broken:key",
                    "metadata": {
                        "entity_identity_version": 1,
                        # Valid but noncanonical platform key (leading zeros).
                        "subject_entity_key": "user:qq:000123",
                        "object_entity_key": expected_object_key,
                    },
                }
            ]

    service = ContextService([GraphContextSource(_FakeGraph())])
    hits = await service.search("音游", group_id="g1", top_k=5)
    assert hits, "graph fact with valid nested identity must still return a hit"
    hit = next(item for item in hits if item.id == "gf_malformed_top_valid_nested")

    assert hit.provenance is not None
    # Nested valid wins over top-level invalid; parse_entity_key canonicalizes.
    assert hit.provenance.entity_key == "user:qq:123"
    assert hit.metadata.get("subject_entity_key") == "user:qq:123"
    assert hit.metadata.get("object_entity_key") == expected_object_key
    # Must not fall back to surface-derived concept for 阿明, and must not keep
    # the padded noncanonical form or the broken top-level ghost.
    assert hit.provenance.entity_key != "user:qq:000123"
    assert hit.provenance.entity_key != "broken:key"
    assert not str(hit.provenance.entity_key).startswith("concept:")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blank_role,blank_value",
    [
        ("subject", ""),
        ("subject", "   "),
        ("object", ""),
        ("object", "   "),
    ],
    ids=[
        "blank_subject_empty",
        "blank_subject_whitespace",
        "blank_object_empty",
        "blank_object_whitespace",
    ],
)
async def test_graph_context_search_tolerates_blank_subject_or_object_without_metadata(
    blank_role: str,
    blank_value: str,
) -> None:
    """Corrupt blank subject/object rows must not poison the graph source.

    Legacy/corrupt graph rows may carry empty or whitespace subject/object even
    though normal writes reject them. With max_hops=2 and two direct seeds,
    hop evaluation inspects every row via _graph_entity_keys. Entity-key
    derivation for the blank role must be skipped so normal hits survive and
    ContextService does not record graph:ValueError.
    """
    from services.context import ContextService, GraphContextSource
    from services.memory.entity_identity import entity_ref_from_surface

    good_subject_key = entity_ref_from_surface(
        subject="稳定事实", scope="group", scope_id="g1"
    ).entity_key
    good_object_key = entity_ref_from_surface(
        subject="可靠结果", scope="group", scope_id="g1"
    ).entity_key
    seed2_object_key = entity_ref_from_surface(
        subject="支撑节点", scope="group", scope_id="g1"
    ).entity_key

    def _rel(
        *,
        fact_id: str,
        subject: str,
        predicate: str,
        object_: str,
        metadata: dict | None = None,
        subject_entity_key: str | None = None,
        object_entity_key: str | None = None,
    ) -> dict:
        row: dict = {
            "fact_id": fact_id,
            "subject": subject,
            "predicate": predicate,
            "object": object_,
            "confidence": 0.9,
            "status": "active",
            "source": "blank_role_poison_fixture",
            "scope": "group",
            "scope_id": "g1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "supersedes": None,
            "evidence": [],
        }
        if metadata is not None:
            row["metadata"] = metadata
        if subject_entity_key is not None:
            row["subject_entity_key"] = subject_entity_key
        if object_entity_key is not None:
            row["object_entity_key"] = object_entity_key
        return row

    # Corrupt active row: blank subject or object, deliberately no identity metadata.
    if blank_role == "subject":
        corrupt = _rel(
            fact_id="gf_blank_role_poison",
            subject=blank_value,
            predicate="噪声",
            object_="残留对象",
        )
    else:
        corrupt = _rel(
            fact_id="gf_blank_role_poison",
            subject="残留主体",
            predicate="噪声",
            object_=blank_value,
        )

    relationships = [
        _rel(
            fact_id="gf_blank_good",
            subject="稳定事实",
            predicate="说明",
            object_="可靠结果",
            metadata={
                "entity_identity_version": 1,
                "subject_entity_key": good_subject_key,
                "object_entity_key": good_object_key,
            },
            subject_entity_key=good_subject_key,
            object_entity_key=good_object_key,
        ),
        # Second direct seed so max_hops expansion runs and evaluates all rows.
        _rel(
            fact_id="gf_blank_seed2",
            subject="稳定事实",
            predicate="关联",
            object_="支撑节点",
            metadata={
                "entity_identity_version": 1,
                "subject_entity_key": good_subject_key,
                "object_entity_key": seed2_object_key,
            },
            subject_entity_key=good_subject_key,
            object_entity_key=seed2_object_key,
        ),
        corrupt,
    ]

    class _FakeGraph:
        async def list_relationships(self, *, limit: int = 200) -> list[dict]:
            return relationships[:limit]

    service = ContextService([GraphContextSource(_FakeGraph(), max_hops=2)])
    hits = await service.search("稳定事实", group_id="g1", top_k=8)

    hit_ids = {hit.id for hit in hits}
    assert "gf_blank_good" in hit_ids
    assert "gf_blank_seed2" in hit_ids
    # Good direct hits survive; corrupt blank-role row may be omitted or returned
    # with an empty key for the blank role, but must not wipe the graph source.
    assert any("稳定事实" in hit.content for hit in hits)

    recent = service.recent(limit=1)
    assert recent, "ContextService must record the search"
    error = recent[-1].get("error") or ""
    assert not error, (
        f"graph source must not be poisoned by blank {blank_role} fact; "
        f"got error={error!r}"
    )
    assert "graph:ValueError" not in error
