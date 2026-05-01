"""Tests for ScheduleStore JSON persistence and cache."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from plugins.schedule.store import ScheduleStore
from plugins.schedule.types import Schedule, TimeSlot

CST = ZoneInfo("Asia/Shanghai")


def _make_schedule(date_str: str = "2026-04-29") -> Schedule:
    return Schedule(
        date=date_str,
        day_narrative="test day",
        theme="测试日",
        generated_at="2026-04-29T02:00:00+08:00",
        slots=[
            TimeSlot(time="08:00", activity="起床", mood_hint="困倦", location="家里"),
            TimeSlot(time="12:00", activity="吃午饭", mood_hint="放松", location="食堂"),
            TimeSlot(time="18:00", activity="排练", mood_hint="专注", location="排练室"),
        ],
    )


class TestScheduleStore:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(storage_dir=tmp)
            schedule = _make_schedule()
            store.save(schedule)
            loaded = store.load("2026-04-29")
            assert loaded is not None
            assert loaded.date == "2026-04-29"
            assert loaded.theme == "测试日"
            assert loaded.day_narrative == "test day"
            assert len(loaded.slots) == 3
            assert loaded.slots[0].activity == "起床"

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(storage_dir=tmp)
            assert store.load("2099-01-01") is None

    def test_load_malformed_json_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(storage_dir=tmp)
            path = Path(tmp) / "2026-04-29.json"
            path.write_text("{not valid json")
            assert store.load("2026-04-29") is None

    def test_current_set_after_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(storage_dir=tmp)
            assert store.current is None
            schedule = _make_schedule()
            store.save(schedule)
            assert store.current is not None
            assert store.current.date == "2026-04-29"

    def test_current_set_after_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(storage_dir=tmp)
            schedule = _make_schedule()
            store.save(schedule)
            # Create fresh store to ensure it reads from disk
            store2 = ScheduleStore(storage_dir=tmp)
            loaded = store2.load("2026-04-29")
            assert loaded is not None
            assert store2.current is not None
            assert store2.current.date == "2026-04-29"

    def test_list_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(storage_dir=tmp)
            store.save(_make_schedule("2026-04-29"))
            store.save(_make_schedule("2026-04-28"))
            files = store.list_files()
            assert files == ["2026-04-29", "2026-04-28"]

    def test_roundtrip_preserves_all_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduleStore(storage_dir=tmp)
            schedule = _make_schedule()
            store.save(schedule)
            loaded = store.load("2026-04-29")
            assert loaded is not None
            assert loaded.day_narrative == schedule.day_narrative
            assert loaded.theme == schedule.theme
            assert loaded.generated_at == schedule.generated_at
            for orig, reloaded in zip(schedule.slots, loaded.slots, strict=True):
                assert orig.time == reloaded.time
                assert orig.activity == reloaded.activity
                assert orig.mood_hint == reloaded.mood_hint
                assert orig.location == reloaded.location


class TestScheduleCurrentSlot:
    def test_current_slot_exact_match(self):
        s = _make_schedule()
        now = datetime(2026, 4, 29, 8, 0, tzinfo=CST)
        slot = s.current_slot(now)
        assert slot is not None
        assert slot.activity == "起床"

    def test_current_slot_between(self):
        s = _make_schedule()
        now = datetime(2026, 4, 29, 10, 0, tzinfo=CST)
        slot = s.current_slot(now)
        assert slot is not None
        assert slot.activity == "起床"  # still in 08:00 slot

    def test_current_slot_after_last(self):
        s = _make_schedule()
        now = datetime(2026, 4, 29, 23, 0, tzinfo=CST)
        slot = s.current_slot(now)
        assert slot is not None
        assert slot.activity == "排练"  # last slot at 18:00

    def test_current_slot_before_first(self):
        s = _make_schedule()
        now = datetime(2026, 4, 29, 5, 0, tzinfo=CST)
        slot = s.current_slot(now)
        assert slot is None  # before first slot at 08:00

    def test_current_slot_empty_slots(self):
        s = Schedule(date="2026-04-29", day_narrative="", slots=[])
        now = datetime(2026, 4, 29, 12, 0, tzinfo=CST)
        assert s.current_slot(now) is None
