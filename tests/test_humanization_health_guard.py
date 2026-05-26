from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kernel.config import HumanizationConfig
from services.humanization.health_guard import (
    HumanizationHealthGuard,
    clear_degraded_groups,
    is_group_degraded,
)


@pytest.fixture(autouse=True)
def _clear_guard_state() -> None:
    clear_degraded_groups()


def _init_db(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE llm_calls (
                ts TEXT NOT NULL,
                group_id TEXT,
                prompt_cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
                prompt_cache_miss_tokens INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def _insert_usage(path: Path, group_id: str, hit: int, miss: int) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            """
            INSERT INTO llm_calls (
                ts, group_id, prompt_cache_hit_tokens, prompt_cache_miss_tokens
            ) VALUES (?, ?, ?, ?)
            """,
            (datetime.now(UTC).isoformat(), group_id, hit, miss),
        )


def test_health_guard_degrades_low_cache_hit_group(tmp_path: Path) -> None:
    db_path = tmp_path / "usage.db"
    _init_db(db_path)
    _insert_usage(db_path, "993065015", hit=70, miss=30)
    guard = HumanizationHealthGuard(db_path=db_path, now=lambda: 100.0)

    samples = guard.poll_once()

    assert samples[0].hit_rate == 0.7
    assert is_group_degraded("993065015") is True


def test_health_guard_recovers_after_sustained_healthy_hit_rate(tmp_path: Path) -> None:
    db_path = tmp_path / "usage.db"
    _init_db(db_path)
    _insert_usage(db_path, "993065015", hit=70, miss=30)
    now = 100.0
    guard = HumanizationHealthGuard(db_path=db_path, now=lambda: now)
    guard.poll_once()

    with sqlite3.connect(db_path) as con:
        con.execute("DELETE FROM llm_calls")
    _insert_usage(db_path, "993065015", hit=90, miss=10)
    now = 200.0
    guard.poll_once()
    assert is_group_degraded("993065015") is True

    now = 801.0
    guard.poll_once()
    assert is_group_degraded("993065015") is False


def test_health_guard_missing_db_does_not_degrade(tmp_path: Path) -> None:
    guard = HumanizationHealthGuard(db_path=tmp_path / "missing.db")

    assert guard.poll_once() == []
    assert is_group_degraded("993065015") is False


def test_performance_profile_uses_balanced_when_group_degraded(tmp_path: Path) -> None:
    db_path = tmp_path / "usage.db"
    _init_db(db_path)
    _insert_usage(db_path, "993065015", hit=70, miss=30)
    HumanizationHealthGuard(db_path=db_path, now=lambda: 100.0).poll_once()

    resolved = HumanizationConfig(profile="performance").resolve_profile(
        group_id="993065015"
    )

    assert resolved.streaming_segment_enabled is True
    assert resolved.pause_then_extend_enabled is True
    assert resolved.plan_then_utter_enabled is False
    assert resolved.disable_natural_split is True
