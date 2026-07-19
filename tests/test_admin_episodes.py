"""Admin API contracts for explicit Episode prompt enablement."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.routes.api.episodes import create_episodes_router
from services.episodic.store import EpisodeStore


@pytest.fixture
async def episode_store(tmp_path):
    store = EpisodeStore(str(tmp_path / "episodic.db"))
    await store.init()
    try:
        yield store
    finally:
        await store.close()


def _client(store: EpisodeStore) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_episodes_router(ctx=SimpleNamespace(episode_store=store)),
        prefix="/api/admin",
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_enable_approved_episode_closes_prompt_recall_loop(
    episode_store: EpisodeStore,
) -> None:
    episode = await episode_store.create_episode(
        situation="部署排障",
        reflection="先检查日志再修改配置",
        group_id="g1",
        confidence=0.8,
        source="consolidator",
    )
    await episode_store.transition_state(
        episode.episode_id,
        new_state="candidate",
        actor="consolidator",
    )
    await episode_store.transition_state(
        episode.episode_id,
        new_state="approved",
        actor="admin",
    )

    response = _client(episode_store).post(
        f"/api/admin/episodes/{episode.episode_id}/enable",
        json={"reason": "允许进入历史反思 prompt"},
    )

    assert response.status_code == 200
    assert response.json()["new_state"] == "enabled_for_prompt"
    refreshed = await episode_store.get_episode(episode.episode_id)
    assert refreshed is not None
    assert refreshed.episode_state == "enabled_for_prompt"
    recalled = await episode_store.list_for_recall(group_id="g1")
    assert [item.episode_id for item in recalled] == [episode.episode_id]


@pytest.mark.asyncio
async def test_set_decay_success_normalizes_and_returns_decay_at(
    episode_store: EpisodeStore,
) -> None:
    episode = await episode_store.create_episode(
        situation="decay me",
        reflection="set expiry",
        group_id="g1",
        confidence=0.8,
    )
    await episode_store.transition_state(episode.episode_id, new_state="candidate")
    await episode_store.transition_state(episode.episode_id, new_state="approved")
    await episode_store.transition_state(
        episode.episode_id, new_state="enabled_for_prompt",
    )

    response = _client(episode_store).post(
        f"/api/admin/episodes/{episode.episode_id}/decay",
        json={
            "decay_at": "2026-10-01T00:00:00Z",
            "reason": "sunset after festival",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["episode_id"] == episode.episode_id
    assert body["decay_at"] == "2026-10-01T08:00:00+08:00"

    refreshed = await episode_store.get_episode(episode.episode_id)
    assert refreshed is not None
    assert refreshed.decay_at == "2026-10-01T08:00:00+08:00"


@pytest.mark.asyncio
async def test_set_decay_clear_with_empty_string(episode_store: EpisodeStore) -> None:
    episode = await episode_store.create_episode(
        situation="clear decay",
        group_id="g1",
        confidence=0.7,
        decay_at="2026-12-01T12:00:00+08:00",
    )
    response = _client(episode_store).post(
        f"/api/admin/episodes/{episode.episode_id}/decay",
        json={"decay_at": "", "reason": "no longer expires"},
    )
    assert response.status_code == 200
    assert response.json()["decay_at"] == ""
    refreshed = await episode_store.get_episode(episode.episode_id)
    assert refreshed is not None
    assert refreshed.decay_at == ""


@pytest.mark.asyncio
async def test_set_decay_missing_field_returns_400(episode_store: EpisodeStore) -> None:
    episode = await episode_store.create_episode(situation="x", group_id="g1")
    response = _client(episode_store).post(
        f"/api/admin/episodes/{episode.episode_id}/decay",
        json={"reason": "forgot decay_at"},
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False


@pytest.mark.asyncio
async def test_set_decay_invalid_naive_returns_400(episode_store: EpisodeStore) -> None:
    episode = await episode_store.create_episode(situation="x", group_id="g1")
    response = _client(episode_store).post(
        f"/api/admin/episodes/{episode.episode_id}/decay",
        json={"decay_at": "2026-10-01T00:00:00"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_set_decay_unknown_episode_returns_404(episode_store: EpisodeStore) -> None:
    response = _client(episode_store).post(
        "/api/admin/episodes/ep_does_not_exist/decay",
        json={"decay_at": "2026-10-01T00:00:00+08:00"},
    )
    assert response.status_code == 404
    assert response.json()["ok"] is False
