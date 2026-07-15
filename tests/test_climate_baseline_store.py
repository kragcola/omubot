from __future__ import annotations

import asyncio
import importlib
import sqlite3
import threading

import pytest

from services.dialogue_climate.dynamics import ClimateEngine
from services.dialogue_climate.state import ClimateState


def _module():
    try:
        return importlib.import_module("services.dialogue_climate.baseline_store")
    except ModuleNotFoundError:
        return None


@pytest.mark.asyncio
async def test_start_rejects_future_schema_without_downgrading(tmp_path) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version=2")

    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)

    with pytest.raises(RuntimeError, match="newer than this runtime"):
        await store.start()

    with sqlite3.connect(db_path) as conn:
        [version] = conn.execute("PRAGMA user_version").fetchone()
    assert version == 2


@pytest.mark.asyncio
async def test_start_rejects_current_version_with_wrong_schema_fingerprint(tmp_path) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE climate_baselines ("
            "group_id TEXT NOT NULL, user_id TEXT NOT NULL, "
            "baseline_energy REAL NOT NULL)"
        )
        conn.execute("PRAGMA user_version=1")

    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)

    with pytest.raises(RuntimeError, match="schema fingerprint mismatch"):
        await store.start()


@pytest.mark.asyncio
async def test_start_rejects_current_version_when_schema_is_missing(tmp_path) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version=1")

    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)

    with pytest.raises(RuntimeError, match="schema fingerprint mismatch"):
        await store.start()

    with sqlite3.connect(db_path) as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'climate_baselines'"
        ).fetchone()
    assert table is None


@pytest.mark.asyncio
async def test_version_zero_revision_schema_is_adopted_and_counter_resumes(tmp_path) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE climate_baselines ("
            "group_id TEXT NOT NULL, user_id TEXT NOT NULL, "
            "baseline_energy REAL NOT NULL, baseline_valence REAL NOT NULL, "
            "baseline_openness REAL NOT NULL, revision INTEGER NOT NULL, "
            "updated_at REAL NOT NULL, PRIMARY KEY (group_id, user_id))"
        )
        conn.execute(
            "INSERT INTO climate_baselines VALUES ('g1', 'u1', 0.2, 0.3, 0.4, 7, 1000.0)"
        )

    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)
    await store.start()
    store.stage(
        group_id="g1",
        user_id="u1",
        state=ClimateState(baseline_energy=0.9),
        updated_at=1000.0,
    )
    await store.flush()
    await store.close()

    with sqlite3.connect(db_path) as conn:
        [version] = conn.execute("PRAGMA user_version").fetchone()
        row = conn.execute(
            "SELECT baseline_energy, revision, updated_at FROM climate_baselines "
            "WHERE group_id = 'g1' AND user_id = 'u1'"
        ).fetchone()
    assert version == 1
    assert row == pytest.approx((0.9, 8, 1000.0))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("baseline_energy", "NaN"),
        ("baseline_valence", float("inf")),
        ("baseline_openness", -0.01),
        ("baseline_energy", 1.01),
        ("updated_at", "NaN"),
        ("updated_at", float("inf")),
    ),
)
async def test_load_rejects_non_finite_or_out_of_range_rows(
    tmp_path,
    column: str,
    value: object,
) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    store = module.ClimateBaselineStore(
        db_path=db_path,
        flush_interval_s=3600,
        wall_clock=lambda: 1001.0,
    )
    await store.start()
    store.stage(
        group_id="g1",
        user_id="u1",
        state=ClimateState(),
        updated_at=1000.0,
    )
    await store.flush()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE climate_baselines SET {column} = ? "
            "WHERE group_id = 'g1' AND user_id = 'u1'",
            (value,),
        )

    assert store.load(group_id="g1", user_id="u1") is None
    await store.close()


@pytest.mark.parametrize(
    ("state", "updated_at"),
    (
        (ClimateState(baseline_energy=float("nan")), 1000.0),
        (ClimateState(baseline_valence=float("inf")), 1000.0),
        (ClimateState(baseline_openness=-0.01), 1000.0),
        (ClimateState(baseline_energy=1.01), 1000.0),
        (ClimateState(), float("nan")),
        (ClimateState(), float("inf")),
    ),
)
def test_stage_rejects_non_finite_or_out_of_range_snapshot(
    tmp_path,
    state: ClimateState,
    updated_at: float,
) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    store = module.ClimateBaselineStore(db_path=str(tmp_path / "climate_baselines.db"))

    with pytest.raises(ValueError, match="invalid climate baseline snapshot"):
        store.stage(group_id="g1", user_id="u1", state=state, updated_at=updated_at)

    assert store.pending_count == 0


@pytest.mark.asyncio
async def test_baseline_store_round_trip_restores_only_slow_baselines(tmp_path) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)
    await store.start()
    store.stage(
        group_id="g1",
        user_id="u1",
        state=ClimateState(
            energy=0.95,
            tension=0.8,
            baseline_energy=0.7,
            baseline_valence=0.4,
            baseline_openness=0.6,
        ),
        updated_at=1000.0,
    )
    await store.flush()
    await store.close()

    restored_store = module.ClimateBaselineStore(
        db_path=db_path,
        flush_interval_s=3600,
        wall_clock=lambda: 1001.0,
    )
    engine = ClimateEngine(m2_enabled=True)
    engine.set_baseline_store(restored_store)

    restored = engine.resolve(group_id="g1", user_id="u1", now_ts=10.0)

    assert restored.energy == pytest.approx(0.7)
    assert restored.valence == pytest.approx(0.4)
    assert restored.openness == pytest.approx(0.6)
    assert restored.tension == 0.0
    await restored_store.close()


@pytest.mark.asyncio
async def test_cancelled_flush_requeues_pending_baseline(tmp_path, monkeypatch) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    store = module.ClimateBaselineStore(
        db_path=str(tmp_path / "climate_baselines.db"),
        flush_interval_s=3600,
    )
    store.stage(group_id="g1", user_id="u1", state=ClimateState())

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_write(_rows):
        started.set()
        await release.wait()

    monkeypatch.setattr(store, "_write_rows", slow_write)
    task = asyncio.create_task(store.flush())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.pending_count == 1
    release.set()
    await store.close()


@pytest.mark.asyncio
async def test_cancelled_to_thread_writer_cannot_overwrite_newer_baseline(
    tmp_path,
    monkeypatch,
) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)
    original_write = store._write_rows_sync
    first_started = threading.Event()
    first_finished = threading.Event()
    release_first = threading.Event()
    calls_lock = threading.Lock()
    call_count = 0

    def ordered_write(rows) -> None:
        nonlocal call_count
        with calls_lock:
            call_count += 1
            call = call_count
        if call == 1:
            first_started.set()
            release_first.wait(timeout=5.0)
        try:
            original_write(rows)
        finally:
            if call == 1:
                first_finished.set()

    monkeypatch.setattr(store, "_write_rows_sync", ordered_write)
    store.stage(
        group_id="g1",
        user_id="u1",
        state=ClimateState(baseline_energy=0.1),
        updated_at=1.0,
    )
    first_flush = asyncio.create_task(store.flush())
    assert await asyncio.to_thread(first_started.wait, 5.0)

    first_flush.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_flush

    store.stage(
        group_id="g1",
        user_id="u1",
        state=ClimateState(baseline_energy=0.9),
        updated_at=2.0,
    )
    await store.flush()
    release_first.set()
    assert await asyncio.to_thread(first_finished.wait, 5.0)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT baseline_energy, updated_at FROM climate_baselines "
            "WHERE group_id = 'g1' AND user_id = 'u1'"
        ).fetchone()
    assert row == pytest.approx((0.9, 2.0))
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_commits_last", (True, False))
async def test_persistent_revision_orders_equal_timestamp_cancelled_writers(
    tmp_path,
    monkeypatch,
    old_commits_last: bool,
) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)
    original_write = store._write_rows_sync
    old_started = threading.Event()
    old_finished = threading.Event()
    release_old = threading.Event()
    calls_lock = threading.Lock()
    call_count = 0

    def ordered_write(rows) -> None:
        nonlocal call_count
        with calls_lock:
            call_count += 1
            call = call_count
        if call == 1:
            old_started.set()
            release_old.wait(timeout=5.0)
        try:
            original_write(rows)
        finally:
            if call == 1:
                old_finished.set()

    monkeypatch.setattr(store, "_write_rows_sync", ordered_write)
    store.stage(
        group_id="g1",
        user_id="u1",
        state=ClimateState(baseline_energy=0.1),
        updated_at=1.0,
    )
    old_flush = asyncio.create_task(store.flush())
    assert await asyncio.to_thread(old_started.wait, 5.0)
    old_flush.cancel()
    with pytest.raises(asyncio.CancelledError):
        await old_flush

    store.stage(
        group_id="g1",
        user_id="u1",
        state=ClimateState(baseline_energy=0.9),
        updated_at=1.0,
    )
    if old_commits_last:
        await store.flush()
        release_old.set()
        assert await asyncio.to_thread(old_finished.wait, 5.0)
    else:
        release_old.set()
        assert await asyncio.to_thread(old_finished.wait, 5.0)
        await store.flush()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT baseline_energy, updated_at, revision FROM climate_baselines "
            "WHERE group_id = 'g1' AND user_id = 'u1'"
        ).fetchone()
    assert row == pytest.approx((0.9, 1.0, 2))
    await store.close()


@pytest.mark.asyncio
async def test_close_rejects_late_stage_and_drains_every_accepted_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)
    original_write = store._write_rows
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    async def slow_write(rows) -> None:
        write_started.set()
        await release_write.wait()
        await original_write(rows)

    monkeypatch.setattr(store, "_write_rows", slow_write)
    store.stage(
        group_id="g1",
        user_id="accepted",
        state=ClimateState(baseline_energy=0.7),
        updated_at=1.0,
    )
    close_task = asyncio.create_task(store.close())
    await write_started.wait()

    try:
        with pytest.raises(RuntimeError, match="closing or closed"):
            store.stage(
                group_id="g1",
                user_id="late",
                state=ClimateState(baseline_energy=0.9),
                updated_at=2.0,
            )
    finally:
        release_write.set()
        await close_task

    assert store.pending_count == 0
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id, baseline_energy FROM climate_baselines ORDER BY user_id"
        ).fetchall()
    assert rows == [("accepted", 0.7)]


@pytest.mark.asyncio
async def test_close_is_single_flight_and_waiter_cancellation_does_not_cancel_drain(
    tmp_path,
    monkeypatch,
) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)
    original_write = store._write_rows
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    async def slow_write(rows) -> None:
        write_started.set()
        await release_write.wait()
        await original_write(rows)

    monkeypatch.setattr(store, "_write_rows", slow_write)
    store.stage(
        group_id="g1",
        user_id="u1",
        state=ClimateState(baseline_energy=0.8),
        updated_at=1.0,
    )
    first_waiter = asyncio.create_task(store.close())
    await write_started.wait()
    second_waiter = asyncio.create_task(store.close())
    await asyncio.sleep(0)
    assert not second_waiter.done()

    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter
    await asyncio.sleep(0)
    assert not second_waiter.done()

    release_write.set()
    await second_waiter

    assert store.pending_count == 0
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT baseline_energy, updated_at FROM climate_baselines "
            "WHERE group_id = 'g1' AND user_id = 'u1'"
        ).fetchone()
    assert row == pytest.approx((0.8, 1.0))


@pytest.mark.asyncio
async def test_start_is_single_flight_with_one_schema_worker(tmp_path, monkeypatch) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    store = module.ClimateBaselineStore(
        db_path=str(tmp_path / "climate_baselines.db"),
        flush_interval_s=3600,
    )
    original_ensure = store._ensure_schema
    first_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    calls_lock = threading.Lock()
    call_count = 0

    def slow_ensure() -> None:
        nonlocal call_count
        with calls_lock:
            call_count += 1
            call = call_count
        if call == 1:
            first_started.set()
        else:
            second_started.set()
        release.wait(timeout=5.0)
        original_ensure()

    monkeypatch.setattr(store, "_ensure_schema", slow_ensure)
    first_start = asyncio.create_task(store.start())
    assert await asyncio.to_thread(first_started.wait, 5.0)
    second_start = asyncio.create_task(store.start())

    try:
        assert not await asyncio.to_thread(second_started.wait, 0.2)
    finally:
        release.set()
        await asyncio.gather(first_start, second_start, return_exceptions=True)

    assert call_count == 1
    await store.close()


@pytest.mark.asyncio
async def test_close_waits_for_inflight_start_schema_worker(tmp_path, monkeypatch) -> None:
    module = _module()
    assert module is not None, "ClimateBaselineStore module must exist"
    db_path = str(tmp_path / "climate_baselines.db")
    store = module.ClimateBaselineStore(db_path=db_path, flush_interval_s=3600)
    original_ensure = store._ensure_schema
    schema_started = threading.Event()
    schema_finished = threading.Event()
    release_schema = threading.Event()

    def slow_ensure() -> None:
        schema_started.set()
        release_schema.wait(timeout=5.0)
        original_ensure()
        schema_finished.set()

    monkeypatch.setattr(store, "_ensure_schema", slow_ensure)
    start_waiter = asyncio.create_task(store.start())
    assert await asyncio.to_thread(schema_started.wait, 5.0)
    close_waiter = asyncio.create_task(store.close())

    try:
        close_returned_early = False
        try:
            await asyncio.wait_for(asyncio.shield(close_waiter), timeout=0.05)
            close_returned_early = True
        except TimeoutError:
            pass
        assert not close_returned_early
        assert not schema_finished.is_set()
        with sqlite3.connect(db_path) as conn:
            table_before_close = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'climate_baselines'"
            ).fetchone()
        assert table_before_close is None
    finally:
        release_schema.set()
        await asyncio.gather(start_waiter, close_waiter)

    assert schema_finished.is_set()
    with sqlite3.connect(db_path) as conn:
        [version] = conn.execute("PRAGMA user_version").fetchone()
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'climate_baselines'"
        ).fetchone()
    assert version == 1
    assert table == ("climate_baselines",)
