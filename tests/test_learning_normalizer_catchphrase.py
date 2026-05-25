from __future__ import annotations

import pytest

from services.learning_normalizer import LearningNormalizerStore, normalize_key


@pytest.fixture
async def store(tmp_path) -> LearningNormalizerStore:
    normalizer = LearningNormalizerStore(tmp_path / "ln.db")
    await normalizer.init()
    yield normalizer
    await normalizer.close()


@pytest.mark.asyncio
async def test_catchphrase_domain_defaults_to_catchphrase_profile(store: LearningNormalizerStore) -> None:
    result = await store.attach_candidate(
        domain="catchphrase",
        scope="group",
        group_id="100",
        raw_text="坏了～坏了！！",
        source_table="catchphrase_pool",
        source_id="c1",
    )

    cluster = await store.get_cluster(result.cluster_id)
    assert cluster is not None
    assert cluster.domain == "catchphrase"
    assert cluster.meta["profile"] == "catchphrase"
    assert result.normalized_key == normalize_key("坏了～坏了！！", "catchphrase")


@pytest.mark.asyncio
async def test_catchphrase_reuse_stats_counts_auto_merge(store: LearningNormalizerStore) -> None:
    first = await store.attach_candidate(
        domain="catchphrase",
        scope="group",
        group_id="100",
        raw_text="坏了～坏了！！",
        source_table="catchphrase_pool",
        source_id="c1",
    )
    second = await store.attach_candidate(
        domain="catchphrase",
        scope="group",
        group_id="100",
        raw_text="坏了 坏了",
        source_table="catchphrase_pool",
        source_id="c2",
    )

    stats = await store.reuse_stats(domain="catchphrase", scope="group", group_id="100")

    assert first.cluster_id == second.cluster_id
    assert second.auto_merged is True
    assert stats["cluster_count"] == 1
    assert stats["item_rows"] == 2
    assert stats["total_items"] == 2
    assert stats["auto_merged_items"] == 1
    assert stats["reused_items"] == 1
    assert stats["reuse_rate"] == 0.5


@pytest.mark.asyncio
async def test_catchphrase_domain_isolated_from_slang(store: LearningNormalizerStore) -> None:
    catchphrase = await store.attach_candidate(
        domain="catchphrase",
        scope="group",
        group_id="100",
        raw_text="坏了坏了",
        source_table="catchphrase_pool",
        source_id="c1",
    )
    slang = await store.attach_candidate(
        domain="slang",
        scope="group",
        group_id="100",
        raw_text="坏了坏了",
        source_table="slang_terms",
        source_id="s1",
    )

    catchphrase_clusters, catchphrase_total = await store.list_clusters(domain="catchphrase", group_id="100")
    slang_clusters, slang_total = await store.list_clusters(domain="slang", group_id="100")

    assert catchphrase.cluster_id != slang.cluster_id
    assert catchphrase_total == 1
    assert slang_total == 1
    assert catchphrase_clusters[0].domain == "catchphrase"
    assert slang_clusters[0].domain == "slang"
