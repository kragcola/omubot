"""RED behavior tests for governed SQLite EntityAliasStore.

Expected public API (services.memory.entity_alias_store):
- EntityAlias dataclass
- AliasObservationResult (outcome, alias, conflicting_entity_key)
- EntityAliasStore(db_path) with async init/close/observe/resolve/supersede/revoke/list_aliases

These tests intentionally target the contract before production code exists.
They use a real temp SQLite DB and assert externally observable behavior only.
"""

from __future__ import annotations

import pytest

from services.similarity import normalize_text_key

# ---------------------------------------------------------------------------
# Fixed ISO instants — all validity assertions use explicit observed_at / at
# ---------------------------------------------------------------------------

T0 = "2026-01-01T10:00:00+00:00"
T1 = "2026-01-01T11:00:00+00:00"
T2 = "2026-01-01T12:00:00+00:00"
T3 = "2026-01-01T13:00:00+00:00"
T_MID = "2026-01-01T11:30:00+00:00"  # between T1 and T2 for historical resolve
T_LATE = "2026-01-02T00:00:00+00:00"


@pytest.fixture
async def store(tmp_path):
    from services.memory.entity_alias_store import EntityAliasStore

    path = tmp_path / "entity_aliases.db"
    s = EntityAliasStore(db_path=str(path))
    await s.init()
    try:
        yield s
    finally:
        await s.close()


def _norm(surface: str) -> str:
    return normalize_text_key(surface)


# ---------------------------------------------------------------------------
# 1. Scope isolation: same surface, different groups → independent rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observe_creates_active_row_and_group_scopes_are_independent(store) -> None:
    from services.memory.entity_alias_store import EntityAliasStore

    assert isinstance(store, EntityAliasStore)

    r1 = await store.observe(
        entity_key="user:qq:1",
        alias="小明",
        scope="group",
        scope_id="g1",
        confidence=0.7,
        source="mention",
        observed_at=T0,
    )
    assert r1.outcome == "created"
    assert r1.alias is not None
    assert r1.alias.entity_key == "user:qq:1"
    assert r1.alias.alias_surface == "小明"
    assert r1.alias.alias_norm == _norm("小明")
    assert r1.alias.scope == "group"
    assert r1.alias.scope_id == "g1"
    assert r1.alias.status == "active"
    assert r1.alias.confidence == 0.7
    assert r1.alias.source == "mention"
    assert r1.alias.valid_from == T0
    assert r1.alias.valid_to is None
    assert r1.conflicting_entity_key is None

    resolved_g1 = await store.resolve(
        alias="小明",
        scope="group",
        scope_id="g1",
        at=T0,
    )
    assert resolved_g1 == "user:qq:1"

    r2 = await store.observe(
        entity_key="user:qq:2",
        alias="小明",
        scope="group",
        scope_id="g2",
        confidence=0.6,
        source="mention",
        observed_at=T0,
    )
    assert r2.outcome == "created"
    assert r2.alias is not None
    assert r2.alias.entity_key == "user:qq:2"
    assert r2.alias.scope_id == "g2"
    # Different scope → different deterministic alias_id
    assert r2.alias.alias_id != r1.alias.alias_id

    assert await store.resolve(alias="小明", scope="group", scope_id="g1", at=T0) == "user:qq:1"
    assert await store.resolve(alias="小明", scope="group", scope_id="g2", at=T0) == "user:qq:2"
    # Cross-group must not leak
    assert await store.resolve(alias="小明", scope="group", scope_id="g3", at=T0) is None


# ---------------------------------------------------------------------------
# 2. Deterministic alias_id + refresh / confidence max / dedupe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alias_id_deterministic_and_repeated_observe_refreshes_one_row(store) -> None:
    first = await store.observe(
        entity_key="user:qq:10",
        alias="阿明",
        scope="group",
        scope_id="g1",
        confidence=0.5,
        source="mention",
        observed_at=T0,
    )
    assert first.outcome == "created"
    assert first.alias is not None
    alias_id = first.alias.alias_id
    assert alias_id  # non-empty deterministic id
    assert first.alias.created_at == T0
    assert first.alias.updated_at == T0

    second = await store.observe(
        entity_key="user:qq:10",
        alias="阿明",
        scope="group",
        scope_id="g1",
        confidence=0.9,
        source="mention",
        observed_at=T1,
    )
    assert second.outcome == "refreshed"
    assert second.alias is not None
    assert second.alias.alias_id == alias_id
    assert second.alias.confidence == 0.9  # max(0.5, 0.9)
    assert second.alias.status == "active"
    assert second.alias.created_at == T0  # preserved
    assert second.alias.updated_at == T1

    third = await store.observe(
        entity_key="user:qq:10",
        alias="阿明",
        scope="group",
        scope_id="g1",
        confidence=0.4,  # lower than current max — must not decrease
        source="mention",
        observed_at=T2,
    )
    assert third.outcome == "refreshed"
    assert third.alias is not None
    assert third.alias.alias_id == alias_id
    assert third.alias.confidence == 0.9
    assert third.alias.updated_at == T2

    # Still a single active row for this entity+scope+norm
    rows = await store.list_aliases(
        scope="group",
        scope_id="g1",
        entity_key="user:qq:10",
        status="active",
    )
    assert len(rows) == 1
    assert rows[0].alias_id == alias_id
    assert rows[0].confidence == 0.9

    # Same entity+scope+norm always yields the same alias_id (determinism)
    fourth = await store.observe(
        entity_key="user:qq:10",
        alias="  阿明  ",  # surface may differ; norm is the identity key
        scope="group",
        scope_id="g1",
        confidence=0.5,
        source="mention",
        observed_at=T3,
    )
    assert fourth.alias is not None
    assert fourth.alias.alias_id == alias_id
    assert fourth.alias.alias_norm == _norm("阿明")


# ---------------------------------------------------------------------------
# 3. Sticky collision: same scope/norm claimed by different entity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collision_makes_both_ambiguous_and_blocks_silent_reactivation(store) -> None:
    a = await store.observe(
        entity_key="user:qq:1",
        alias="小明",
        scope="group",
        scope_id="g1",
        confidence=0.8,
        source="mention",
        observed_at=T0,
    )
    assert a.outcome == "created"
    assert await store.resolve(alias="小明", scope="group", scope_id="g1", at=T0) == "user:qq:1"

    b = await store.observe(
        entity_key="user:qq:2",
        alias="小明",
        scope="group",
        scope_id="g1",
        confidence=0.9,
        source="mention",
        observed_at=T1,
    )
    assert b.outcome == "collision"
    assert b.conflicting_entity_key == "user:qq:1"
    assert b.alias is not None
    assert b.alias.entity_key == "user:qq:2"
    assert b.alias.status == "ambiguous"

    # Existing claim also sticky-ambiguous
    existing_rows = await store.list_aliases(
        scope="group",
        scope_id="g1",
        entity_key="user:qq:1",
    )
    assert len(existing_rows) == 1
    assert existing_rows[0].status == "ambiguous"

    # Resolve must not pick a winner
    assert await store.resolve(alias="小明", scope="group", scope_id="g1", at=T1) is None
    assert await store.resolve(alias="小明", scope="group", scope_id="g1", at=T_LATE) is None

    # Ordinary observe cannot silently reactivate either side
    r_a = await store.observe(
        entity_key="user:qq:1",
        alias="小明",
        scope="group",
        scope_id="g1",
        confidence=0.99,
        source="mention",
        observed_at=T2,
    )
    assert r_a.outcome == "ignored_collision"
    assert r_a.alias is not None
    assert r_a.alias.status == "ambiguous"

    r_b = await store.observe(
        entity_key="user:qq:2",
        alias="小明",
        scope="group",
        scope_id="g1",
        confidence=0.99,
        source="mention",
        observed_at=T3,
    )
    assert r_b.outcome == "ignored_collision"
    assert r_b.alias is not None
    assert r_b.alias.status == "ambiguous"

    assert await store.resolve(alias="小明", scope="group", scope_id="g1", at=T3) is None

    # Both rows remain non-active
    all_rows = await store.list_aliases(scope="group", scope_id="g1", status="ambiguous")
    entity_keys = {row.entity_key for row in all_rows}
    assert entity_keys == {"user:qq:1", "user:qq:2"}
    active = await store.list_aliases(scope="group", scope_id="g1", status="active")
    assert active == []


# ---------------------------------------------------------------------------
# 4. supersede rename: close old, activate new, historical resolve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supersede_closes_old_alias_and_historical_resolve(store) -> None:
    created = await store.observe(
        entity_key="user:qq:5",
        alias="小明",
        scope="group",
        scope_id="g1",
        confidence=0.7,
        source="mention",
        observed_at=T0,
    )
    assert created.outcome == "created"
    old_id = created.alias.alias_id

    # Live under the old name until rename at T2
    assert await store.resolve(alias="小明", scope="group", scope_id="g1", at=T1) == "user:qq:5"

    new_alias = await store.supersede(
        entity_key="user:qq:5",
        old_alias="小明",
        new_alias="阿明",
        scope="group",
        scope_id="g1",
        confidence=0.8,
        source="admin",
        observed_at=T2,
    )
    assert new_alias is not None
    assert new_alias.entity_key == "user:qq:5"
    assert new_alias.alias_surface == "阿明"
    assert new_alias.alias_norm == _norm("阿明")
    assert new_alias.status == "active"
    assert new_alias.valid_from == T2
    assert new_alias.valid_to is None
    assert new_alias.alias_id != old_id

    old_rows = await store.list_aliases(
        scope="group",
        scope_id="g1",
        entity_key="user:qq:5",
        status="superseded",
    )
    assert len(old_rows) == 1
    old = old_rows[0]
    assert old.alias_id == old_id
    assert old.alias_norm == _norm("小明")
    assert old.status == "superseded"
    assert old.valid_from == T0
    assert old.valid_to == T2

    # Current resolve: old surface gone, new surface maps to entity
    assert await store.resolve(alias="小明", scope="group", scope_id="g1", at=T3) is None
    assert await store.resolve(alias="阿明", scope="group", scope_id="g1", at=T3) == "user:qq:5"

    # Historical: at an instant between old.valid_from and old.valid_to
    assert await store.resolve(alias="小明", scope="group", scope_id="g1", at=T_MID) == "user:qq:5"
    # Before new valid_from, new alias is not yet active
    assert await store.resolve(alias="阿明", scope="group", scope_id="g1", at=T_MID) is None


# ---------------------------------------------------------------------------
# 5. revoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_ends_current_resolve_and_records_valid_to(store) -> None:
    created = await store.observe(
        entity_key="user:qq:7",
        alias="小七",
        scope="group",
        scope_id="g1",
        confidence=0.6,
        source="mention",
        observed_at=T0,
    )
    assert created.outcome == "created"
    assert await store.resolve(alias="小七", scope="group", scope_id="g1", at=T1) == "user:qq:7"

    revoked = await store.revoke(
        entity_key="user:qq:7",
        alias="小七",
        scope="group",
        scope_id="g1",
        observed_at=T2,
    )
    assert revoked is not None
    assert revoked.status == "revoked"
    assert revoked.valid_to == T2
    assert revoked.valid_from == T0
    assert revoked.alias_id == created.alias.alias_id

    assert await store.resolve(alias="小七", scope="group", scope_id="g1", at=T3) is None
    # Historical before revoke still resolves
    assert await store.resolve(alias="小七", scope="group", scope_id="g1", at=T1) == "user:qq:7"

    rows = await store.list_aliases(
        scope="group",
        scope_id="g1",
        entity_key="user:qq:7",
        status="revoked",
    )
    assert len(rows) == 1
    assert rows[0].status == "revoked"
    assert rows[0].valid_to == T2


# ---------------------------------------------------------------------------
# 6. Resolver normalizes spacing / punctuation / case; unknown → None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_uses_existing_normalizer_and_unknown_is_none(store) -> None:
    await store.observe(
        entity_key="user:qq:9",
        alias="XiaoMing",
        scope="group",
        scope_id="g1",
        confidence=0.8,
        source="mention",
        observed_at=T0,
    )

    # Spacing / punctuation / case fold through normalize_text_key
    assert await store.resolve(
        alias="  xiao ming  ",
        scope="group",
        scope_id="g1",
        at=T0,
    ) == "user:qq:9"
    assert await store.resolve(
        alias="XIAO-MING",
        scope="group",
        scope_id="g1",
        at=T0,
    ) == "user:qq:9"
    assert await store.resolve(
        alias="XiaoMing!",
        scope="group",
        scope_id="g1",
        at=T0,
    ) == "user:qq:9"

    # Unknown surface
    assert await store.resolve(
        alias="完全不存在的称呼",
        scope="group",
        scope_id="g1",
        at=T0,
    ) is None

    # Ambiguous (collision) returns None — covered in collision test; reinstate briefly
    await store.observe(
        entity_key="user:qq:99",
        alias="XiaoMing",
        scope="group",
        scope_id="g1",
        confidence=0.5,
        source="mention",
        observed_at=T1,
    )
    assert await store.resolve(alias="XiaoMing", scope="group", scope_id="g1", at=T1) is None


# ---------------------------------------------------------------------------
# 7. Validation: invalid keys / alias_norm / scope / confidence → ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observe_rejects_invalid_inputs_with_value_error(store) -> None:
    # Invalid entity key
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="not-a-valid-key",
            alias="小明",
            scope="group",
            scope_id="g1",
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="",
            alias="小明",
            scope="group",
            scope_id="g1",
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )

    # Empty / punctuation-only alias (normalizer yields empty)
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="   ",
            scope="group",
            scope_id="g1",
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="!!!",
            scope="group",
            scope_id="g1",
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="...，。",
            scope="group",
            scope_id="g1",
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )

    # Invalid scope
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="planet",
            scope_id="g1",
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )

    # Missing scope_id for group / user scopes
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="group",
            scope_id="",
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="user",
            scope_id="",
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="group",
            scope_id=None,  # type: ignore[arg-type]
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )

    # Invalid confidence
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="group",
            scope_id="g1",
            confidence=-0.1,
            source="mention",
            observed_at=T0,
        )
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="group",
            scope_id="g1",
            confidence=1.1,
            source="mention",
            observed_at=T0,
        )
    with pytest.raises(ValueError):
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="group",
            scope_id="g1",
            confidence=float("nan"),
            source="mention",
            observed_at=T0,
        )


# ---------------------------------------------------------------------------
# 8. Persistence across close/reopen; list_aliases filters + bound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistence_survives_reopen_and_list_aliases_filters_bounded(tmp_path) -> None:
    from services.memory.entity_alias_store import EntityAliasStore

    db_path = str(tmp_path / "persist_aliases.db")
    store = EntityAliasStore(db_path=db_path)
    await store.init()
    try:
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="group",
            scope_id="g1",
            confidence=0.7,
            source="mention",
            observed_at=T0,
        )
        await store.observe(
            entity_key="user:qq:2",
            alias="小红",
            scope="group",
            scope_id="g1",
            confidence=0.6,
            source="mention",
            observed_at=T0,
        )
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="group",
            scope_id="g2",
            confidence=0.5,
            source="mention",
            observed_at=T0,
        )
        await store.observe(
            entity_key="user:qq:3",
            alias="小蓝",
            scope="user",
            scope_id="u3",
            confidence=0.4,
            source="admin",
            observed_at=T0,
        )
        # Revoke one for status filter
        await store.revoke(
            entity_key="user:qq:2",
            alias="小红",
            scope="group",
            scope_id="g1",
            observed_at=T1,
        )
    finally:
        await store.close()

    reopened = EntityAliasStore(db_path=db_path)
    await reopened.init()
    try:
        assert await reopened.resolve(
            alias="小明",
            scope="group",
            scope_id="g1",
            at=T_LATE,
        ) == "user:qq:1"
        assert await reopened.resolve(
            alias="小红",
            scope="group",
            scope_id="g1",
            at=T_LATE,
        ) is None

        by_scope = await reopened.list_aliases(scope="group", scope_id="g1")
        assert {r.entity_key for r in by_scope} == {"user:qq:1", "user:qq:2"}

        by_entity = await reopened.list_aliases(entity_key="user:qq:1")
        assert len(by_entity) == 2  # g1 + g2
        assert all(r.entity_key == "user:qq:1" for r in by_entity)

        by_status = await reopened.list_aliases(
            scope="group",
            scope_id="g1",
            status="revoked",
        )
        assert len(by_status) == 1
        assert by_status[0].entity_key == "user:qq:2"

        active_g1 = await reopened.list_aliases(
            scope="group",
            scope_id="g1",
            status="active",
        )
        assert len(active_g1) == 1
        assert active_g1[0].entity_key == "user:qq:1"

        # Bounded listing — must not return more than limit
        for i in range(8):
            await reopened.observe(
                entity_key=f"user:qq:{100 + i}",
                alias=f"昵称{i}",
                scope="group",
                scope_id="g_bound",
                confidence=0.5,
                source="mention",
                observed_at=T0,
            )
        limited = await reopened.list_aliases(
            scope="group",
            scope_id="g_bound",
            limit=3,
        )
        assert len(limited) == 3
        # Default / high limit still bounded to a sane cap if implementation enforces one;
        # at minimum limit=3 must be respected.
        unlimited_ish = await reopened.list_aliases(
            scope="group",
            scope_id="g_bound",
            limit=100,
        )
        assert len(unlimited_ish) == 8
    finally:
        await reopened.close()


# ---------------------------------------------------------------------------
# 9. Multi-source on same entity+alias: no duplicate; meta.sources union
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_source_dedupes_and_preserves_sources_list_in_meta(store) -> None:
    first = await store.observe(
        entity_key="user:qq:20",
        alias="小明",
        scope="group",
        scope_id="g1",
        confidence=0.5,
        source="mention",
        observed_at=T0,
    )
    assert first.outcome == "created"
    assert first.alias is not None
    alias_id = first.alias.alias_id

    second = await store.observe(
        entity_key="user:qq:20",
        alias="小明",
        scope="group",
        scope_id="g1",
        confidence=0.6,
        source="admin",
        observed_at=T1,
    )
    assert second.outcome == "refreshed"
    assert second.alias is not None
    assert second.alias.alias_id == alias_id
    assert second.alias.confidence == 0.6

    rows = await store.list_aliases(
        scope="group",
        scope_id="g1",
        entity_key="user:qq:20",
        status="active",
    )
    assert len(rows) == 1
    meta = rows[0].meta
    assert isinstance(meta, dict)
    sources = meta.get("sources")
    assert sources is not None
    assert set(sources) == {"mention", "admin"}

    # Primary source field may remain the original or latest; sources list is the contract.
    assert "mention" in sources
    assert "admin" in sources


# ---------------------------------------------------------------------------
# 10. Dataclass field surface (API shape smoke)
# ---------------------------------------------------------------------------


def test_entity_alias_and_observation_result_field_surface() -> None:
    """Import-time shape check for the public dataclasses."""
    from dataclasses import fields

    from services.memory.entity_alias_store import AliasObservationResult, EntityAlias

    alias_names = {f.name for f in fields(EntityAlias)}
    assert alias_names == {
        "alias_id",
        "entity_key",
        "alias_norm",
        "alias_surface",
        "scope",
        "scope_id",
        "confidence",
        "source",
        "status",
        "valid_from",
        "valid_to",
        "created_at",
        "updated_at",
        "meta",
    }

    result_names = {f.name for f in fields(AliasObservationResult)}
    assert result_names == {"outcome", "alias", "conflicting_entity_key"}


def test_entity_alias_store_exposes_required_methods() -> None:
    from services.memory.entity_alias_store import EntityAliasStore

    for name in (
        "init",
        "close",
        "observe",
        "resolve",
        "supersede",
        "revoke",
        "list_aliases",
    ):
        assert hasattr(EntityAliasStore, name), f"EntityAliasStore missing {name}"
        assert callable(getattr(EntityAliasStore, name))
