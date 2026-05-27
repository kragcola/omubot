from __future__ import annotations

from services.scheduler_hawkes import HawkesCache, HawkesOfflineRefresher


class _MessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["100", "200"]

    async def query_recent(self, group_id: str, limit: int = 500):  # type: ignore[no-untyped-def]
        base = 1_000.0 if group_id == "100" else 2_000.0
        return [
            {"role": "user", "created_at": base - 30},
            {"role": "user", "created_at": base - 20},
            {"role": "assistant", "created_at": base - 10},
        ][:limit]


async def test_hawkes_offline_refresher_updates_cache(tmp_path) -> None:
    cache = HawkesCache(tmp_path / "hawkes.db")
    refresher = HawkesOfflineRefresher(
        message_log=_MessageLog(),
        cache=cache,
        window_s=120,
    )

    updated = await refresher.run_once(now=1_000.0)

    assert updated == 2
    snapshot = cache.load("100", max_age_s=9_999_999_999)
    assert snapshot is not None
    assert snapshot.message_count == 2
