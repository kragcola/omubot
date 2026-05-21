from __future__ import annotations

import pytest

from services.learning_normalizer import LearningNormalizerStore, normalize_key, score_similarity


def test_normalize_key_folds_common_noise() -> None:
    assert normalize_key("P.J.S.K!!", "slang") == "pjsk"
    assert normalize_key(" 大家 在 轻松吐槽！", "style") == "大家在轻松吐槽"
    assert normalize_key("啊啊啊啊啊", "slang") == "啊啊"


def test_rapidfuzz_similarity_and_short_token_guard() -> None:
    assert score_similarity("凤笑梦bot", "凤笑梦bot啊", "slang").score >= 0.92
    assert score_similarity("ab", "ac", "slang").score < 0.92


@pytest.mark.asyncio
async def test_store_exact_and_group_isolation(tmp_path) -> None:
    store = LearningNormalizerStore(tmp_path / "ln.db")
    await store.init()
    try:
        first = await store.attach_candidate(
            domain="slang",
            scope="group",
            group_id="100",
            raw_text="P.J.S.K",
            source_table="slang_terms",
            source_id="a",
            profile="slang",
        )
        second = await store.attach_candidate(
            domain="slang",
            scope="group",
            group_id="100",
            raw_text="p j s k",
            source_table="slang_terms",
            source_id="b",
            profile="slang",
        )
        other_group = await store.attach_candidate(
            domain="slang",
            scope="group",
            group_id="200",
            raw_text="p j s k",
            source_table="slang_terms",
            source_id="c",
            profile="slang",
        )
        assert first.cluster_id == second.cluster_id
        assert second.auto_merged is True
        assert other_group.cluster_id != first.cluster_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_lock_split_and_undo(tmp_path) -> None:
    store = LearningNormalizerStore(tmp_path / "ln.db")
    await store.init()
    try:
        first = await store.attach_candidate(
            domain="style",
            scope="group",
            group_id="100",
            raw_text="有人分享成果 先开心再说具体喜欢的点",
            source_table="style_expressions",
            source_id="a",
            profile="style",
        )
        second = await store.attach_candidate(
            domain="style",
            scope="group",
            group_id="100",
            raw_text="有人分享成果，先开心再说具体喜欢的点！",
            source_table="style_expressions",
            source_id="b",
            profile="style",
        )
        assert first.cluster_id == second.cluster_id
        ok = await store.lock_cluster(first.cluster_id, "成果回应", actor="test")
        assert ok
        items = await store.list_cluster_items(first.cluster_id)
        split_cluster = await store.split_item(items[0].item_id, actor="test")
        assert split_cluster
        clusters, _ = await store.list_clusters(domain="style", group_id="100")
        assert len(clusters) >= 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_can_undo_auto_merge_into_new_cluster(tmp_path) -> None:
    store = LearningNormalizerStore(tmp_path / "ln.db")
    await store.init()
    try:
        first = await store.attach_candidate(
            domain="style",
            scope="group",
            group_id="100",
            raw_text="有人分享成果 先开心再说具体喜欢的点",
            source_table="style_expressions",
            source_id="a",
            profile="style",
        )
        second = await store.attach_candidate(
            domain="style",
            scope="group",
            group_id="100",
            raw_text="有人分享成果，先开心再说具体喜欢的点！",
            source_table="style_expressions",
            source_id="b",
            profile="style",
        )
        assert first.cluster_id == second.cluster_id

        revisions = await store.list_cluster_revisions(first.cluster_id, action="auto_merge")
        assert revisions
        assert await store.undo_revision(revisions[0].revision_id, actor="test")

        items = await store.list_cluster_items(first.cluster_id)
        assert {item.raw_text for item in items} == {"有人分享成果 先开心再说具体喜欢的点"}
        clusters, _ = await store.list_clusters(domain="style", group_id="100")
        assert len(clusters) == 2
    finally:
        await store.close()
