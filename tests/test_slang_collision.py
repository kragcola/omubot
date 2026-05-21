"""Tests for alias collision detection in slang store update/merge paths."""

from __future__ import annotations

import pytest

from services.slang import SlangCollisionError, SlangCrossScopeMergeError, SlangStore


@pytest.fixture
async def store(tmp_path):
    s = SlangStore(tmp_path / "slang.db")
    await s.init()
    yield s
    await s.close()


async def _create_term(store: SlangStore, term: str, group_id: str, aliases: list[str] | None = None) -> str:
    result = await store.create_term(
        term=term,
        meaning=f"meaning of {term}",
        group_id=group_id,
        aliases=aliases or [],
    )
    return result.term_id


@pytest.mark.asyncio
async def test_update_term_detects_alias_collision(store: SlangStore) -> None:
    id_a = await _create_term(store, "摸鱼", "100")
    id_b = await _create_term(store, "划水", "100")

    with pytest.raises(SlangCollisionError) as exc_info:
        await store.update_term(id_b, aliases=["摸鱼"])

    assert exc_info.value.term_id == id_b
    assert exc_info.value.collides_with_id == id_a


@pytest.mark.asyncio
async def test_update_term_detects_term_rename_collision(store: SlangStore) -> None:
    await _create_term(store, "摸鱼", "100")
    id_b = await _create_term(store, "划水", "100")

    with pytest.raises(SlangCollisionError):
        await store.update_term(id_b, term="摸鱼")


@pytest.mark.asyncio
async def test_update_term_allows_self_collision(store: SlangStore) -> None:
    id_a = await _create_term(store, "摸鱼", "100", aliases=["划水"])
    result = await store.update_term(id_a, aliases=["划水", "偷懒"])
    assert result is True


@pytest.mark.asyncio
async def test_update_term_allows_unrelated_change(store: SlangStore) -> None:
    await _create_term(store, "摸鱼", "100")
    id_b = await _create_term(store, "划水", "100")
    result = await store.update_term(id_b, meaning="new meaning")
    assert result is True


@pytest.mark.asyncio
async def test_merge_terms_detects_collision_with_third_party(store: SlangStore) -> None:
    await _create_term(store, "摸鱼", "100")
    id_b = await _create_term(store, "划水", "100")
    await _create_term(store, "偷懒", "100")

    # Add "偷懒" as alias to B — bypass collision check by direct SQL since
    # create_term already blocks this. The point is merge should catch it.
    await store.update_term(id_b, aliases=["游泳"])

    # Now merge A+B: B's primary key "划水" becomes alias of A. No collision yet.
    # But let's set up a scenario where source's primary key collides with third party.
    # Simpler: create C="偷懒", then merge A+B where B has alias "偷懒" would collide.
    # Since create_term blocks alias collision, let's use a different approach:
    # Create terms without overlapping aliases, then merge produces collision.
    pass

    # Reset: use a clean scenario
    store2_id_a = await _create_term(store, "干饭", "200")
    store2_id_b = await _create_term(store, "吃饭", "200")
    await _create_term(store, "恰饭", "200")

    # Merge A+B: B's primary "吃饭" becomes alias of A. But "恰饭" is a separate term.
    # To trigger collision, B needs an alias that matches C's primary key.
    # We can't add "恰饭" as alias to B via update_term (collision check blocks it).
    # So we directly update the DB to simulate a pre-existing state.
    db = store._require_db()
    import json
    await db.execute(
        "UPDATE slang_terms SET aliases_json = ? WHERE term_id = ?",
        (json.dumps(["恰饭"], ensure_ascii=False), store2_id_b),
    )
    await db.commit()

    with pytest.raises(SlangCollisionError):
        await store.merge_terms(target_id=store2_id_a, source_ids=[store2_id_b])


@pytest.mark.asyncio
async def test_merge_terms_allows_internal_collision(store: SlangStore) -> None:
    id_a = await _create_term(store, "摸鱼", "100")
    id_b = await _create_term(store, "划水", "100")

    # Simulate pre-existing cross-aliases via direct DB (create_term blocks this).
    import json
    db = store._require_db()
    await db.execute(
        "UPDATE slang_terms SET aliases_json = ? WHERE term_id = ?",
        (json.dumps(["划水"], ensure_ascii=False), id_a),
    )
    await db.execute(
        "UPDATE slang_terms SET aliases_json = ? WHERE term_id = ?",
        (json.dumps(["摸鱼"], ensure_ascii=False), id_b),
    )
    await db.commit()

    result = await store.merge_terms(target_id=id_a, source_ids=[id_b])
    assert result is not None
    assert "划水" in result.aliases


@pytest.mark.asyncio
async def test_collision_across_groups_is_independent(store: SlangStore) -> None:
    await _create_term(store, "摸鱼", "100")
    id_b = await _create_term(store, "划水", "200")
    result = await store.update_term(id_b, aliases=["摸鱼"])
    assert result is True


@pytest.mark.asyncio
async def test_merge_terms_rejects_cross_group_source(store: SlangStore) -> None:
    """merge_terms must reject sources whose group_id differs from target.

    Decision 7.5: cross-scope merges would silently grant cross-group visibility
    that admins never explicitly enabled. Reject at the merge boundary.
    """
    target_id = await _create_term(store, "摸鱼", "100")
    cross_group_source_id = await _create_term(store, "划水", "200")

    with pytest.raises(SlangCrossScopeMergeError) as exc_info:
        await store.merge_terms(target_id=target_id, source_ids=[cross_group_source_id])

    assert exc_info.value.target_id == target_id
    assert exc_info.value.source_id == cross_group_source_id
    assert exc_info.value.target_group_id == "100"
    assert exc_info.value.source_group_id == "200"


@pytest.mark.asyncio
async def test_merge_terms_rejects_cross_scope_source(store: SlangStore) -> None:
    """A global-scope source cannot be merged into a group-scope target."""
    target_id = await _create_term(store, "摸鱼", "100")
    global_source = await store.create_term(
        term="划水",
        meaning="meaning of 划水",
        scope="global",
        group_id="",
    )

    with pytest.raises(SlangCrossScopeMergeError) as exc_info:
        await store.merge_terms(target_id=target_id, source_ids=[global_source.term_id])

    assert exc_info.value.target_scope == "group"
    assert exc_info.value.source_scope == "global"


@pytest.mark.asyncio
async def test_merge_terms_rejects_when_any_source_crosses_scope(store: SlangStore) -> None:
    """Even one cross-scope source in a multi-source merge must abort the whole merge."""
    target_id = await _create_term(store, "摸鱼", "100")
    same_group_source = await _create_term(store, "划水", "100")
    cross_group_source = await _create_term(store, "偷懒", "200")

    with pytest.raises(SlangCrossScopeMergeError):
        await store.merge_terms(
            target_id=target_id,
            source_ids=[same_group_source, cross_group_source],
        )

    # Same-group source must remain untouched (state-leak check).
    same_group = await store.get_term(same_group_source)
    assert same_group is not None
    assert same_group.status != "expired"
