from __future__ import annotations

import time

from services.scheduler_hawkes import HawkesCache, HawkesSnapshot, estimate_rho_from_times, snapshot_from_times


def test_estimate_rho_increases_for_bursty_times() -> None:
    now = 1_000.0
    sparse = [now - 1_700, now - 1_000, now - 100]
    bursty = [now - 50, now - 40, now - 30, now - 20, now - 10]

    assert estimate_rho_from_times(bursty, now=now) > estimate_rho_from_times(sparse, now=now)
    assert 0.0 <= estimate_rho_from_times(bursty, now=now) < 1.0


def test_hawkes_cache_round_trip_and_max_age(tmp_path) -> None:
    cache = HawkesCache(tmp_path / "hawkes.db")
    snapshot = HawkesSnapshot("100", rho=0.8, message_count=5, window_s=3600, updated_at=time.time())

    cache.upsert(snapshot)

    loaded = cache.load("100", max_age_s=60)
    assert loaded is not None
    assert loaded.rho == 0.8
    assert cache.load("100", max_age_s=-1) is None


def test_snapshot_from_times_counts_recent_only() -> None:
    now = 1_000.0
    snapshot = snapshot_from_times("g", [now - 10, now - 20, now - 5000], now=now, window_s=60)

    assert snapshot.group_id == "g"
    assert snapshot.message_count == 2
