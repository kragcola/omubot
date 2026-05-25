from __future__ import annotations

import pytest

from scripts.dev.seed_catchphrase_pool import (
    candidates_from_episodes,
    extract_catchphrase_phrases,
    seed_catchphrase_pool,
)
from services.episodic import EpisodeStore
from services.learning_normalizer import LearningNormalizerStore


@pytest.fixture
async def episode_store(tmp_path):
    store = EpisodeStore(str(tmp_path / "episodic.db"))
    await store.init()
    yield store
    await store.close()


async def _candidate_episode(store: EpisodeStore, *, phrase: str = "懂了懂了", group_id: str = "100") -> None:
    episode = await store.create_episode(
        situation="x",
        action_taken=phrase,
        group_id=group_id,
        confidence=0.8,
    )
    await store.transition_state(
        episode.episode_id,
        new_state="candidate",
        actor="test",
        reason="seed test",
    )


def test_extract_catchphrase_phrases_filters_summary_and_noise() -> None:
    phrases = extract_catchphrase_phrases("用户情绪偏低，应该避免说教。懂了懂了！[CQ:image] https://x.test")

    assert phrases == ["懂了懂了"]


@pytest.mark.asyncio
async def test_candidates_skip_episodes_without_group(episode_store: EpisodeStore) -> None:
    await _candidate_episode(episode_store, group_id="")
    episodes = await episode_store.list_episodes(state_filter="candidate")

    candidates, extracted, skipped_no_group = candidates_from_episodes(episodes, limit=30)

    assert extracted == 1
    assert candidates == []
    assert skipped_no_group == 1


@pytest.mark.asyncio
async def test_seed_catchphrase_pool_empty_episode_store_returns_zero(tmp_path) -> None:
    episode_db = tmp_path / "episodic.db"
    normalizer_db = tmp_path / "normalizer.db"
    store = EpisodeStore(str(episode_db))
    await store.init()
    await store.close()

    result = await seed_catchphrase_pool(
        episode_db=episode_db,
        normalizer_db=normalizer_db,
        dry_run=True,
    )

    assert result.scanned_episodes == 0
    assert result.selected == 0
    assert result.written == 0

    normalizer = LearningNormalizerStore(normalizer_db)
    await normalizer.init()
    try:
        _, total = await normalizer.list_clusters(domain="catchphrase")
        assert total == 0
    finally:
        await normalizer.close()


@pytest.mark.asyncio
async def test_seed_catchphrase_pool_dry_run_does_not_write(tmp_path, episode_store: EpisodeStore) -> None:
    await _candidate_episode(episode_store, phrase="稳稳的")

    result = await seed_catchphrase_pool(
        episode_db=tmp_path / "episodic.db",
        normalizer_db=tmp_path / "normalizer.db",
        dry_run=True,
    )

    assert result.scanned_episodes == 1
    assert result.selected == 1
    assert result.written == 0

    normalizer = LearningNormalizerStore(tmp_path / "normalizer.db")
    await normalizer.init()
    try:
        _, total = await normalizer.list_clusters(domain="catchphrase")
        assert total == 0
    finally:
        await normalizer.close()


@pytest.mark.asyncio
async def test_seed_catchphrase_pool_writes_catchphrase_domain(tmp_path, episode_store: EpisodeStore) -> None:
    await _candidate_episode(episode_store, phrase="懂了懂了")

    result = await seed_catchphrase_pool(
        episode_db=tmp_path / "episodic.db",
        normalizer_db=tmp_path / "normalizer.db",
    )

    assert result.selected == 1
    assert result.written == 1

    normalizer = LearningNormalizerStore(tmp_path / "normalizer.db")
    await normalizer.init()
    try:
        clusters, total = await normalizer.list_clusters(domain="catchphrase", group_id="100")
        assert total == 1
        assert clusters[0].canonical_text == "懂了懂了"
        assert clusters[0].meta["profile"] == "catchphrase"
        items = await normalizer.list_cluster_items(clusters[0].cluster_id)
        assert items[0].source_table == "episode"
        assert items[0].meta["source_field"] == "action_taken"
    finally:
        await normalizer.close()


@pytest.mark.asyncio
async def test_seed_catchphrase_pool_is_idempotent_for_same_episode_source(
    tmp_path,
    episode_store: EpisodeStore,
) -> None:
    await _candidate_episode(episode_store, phrase="懂了懂了")

    first = await seed_catchphrase_pool(
        episode_db=tmp_path / "episodic.db",
        normalizer_db=tmp_path / "normalizer.db",
    )
    second = await seed_catchphrase_pool(
        episode_db=tmp_path / "episodic.db",
        normalizer_db=tmp_path / "normalizer.db",
    )

    assert first.written == 1
    assert second.written == 0
    assert second.skipped_existing == 1

    normalizer = LearningNormalizerStore(tmp_path / "normalizer.db")
    await normalizer.init()
    try:
        clusters, total = await normalizer.list_clusters(domain="catchphrase", group_id="100")
        assert total == 1
        items = await normalizer.list_cluster_items(clusters[0].cluster_id)
        assert len(items) == 1
        assert items[0].count == 1
    finally:
        await normalizer.close()
