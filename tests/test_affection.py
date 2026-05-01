"""Tests for the affection & nickname system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.affection.engine import AffectionEngine
from plugins.affection.models import AffectionProfile
from plugins.affection.store import AffectionStore


def make_temp_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# AffectionProfile
# ---------------------------------------------------------------------------

class TestAffectionProfile:
    def test_tier_stranger(self) -> None:
        p = AffectionProfile(user_id="123", score=5.0)
        assert p.tier == "陌生人"

    def test_tier_acquaintance(self) -> None:
        p = AffectionProfile(user_id="123", score=25.0)
        assert p.tier == "熟人"

    def test_tier_friend(self) -> None:
        p = AffectionProfile(user_id="123", score=50.0)
        assert p.tier == "朋友"

    def test_tier_good_friend(self) -> None:
        p = AffectionProfile(user_id="123", score=70.0)
        assert p.tier == "好朋友"

    def test_tier_important(self) -> None:
        p = AffectionProfile(user_id="123", score=90.0)
        assert p.tier == "重要的人"

    def test_tier_boundaries(self) -> None:
        assert AffectionProfile(user_id="x", score=0.0).tier == "陌生人"
        assert AffectionProfile(user_id="x", score=19.999).tier == "陌生人"
        assert AffectionProfile(user_id="x", score=20.0).tier == "熟人"
        assert AffectionProfile(user_id="x", score=39.999).tier == "熟人"
        assert AffectionProfile(user_id="x", score=40.0).tier == "朋友"
        assert AffectionProfile(user_id="x", score=59.999).tier == "朋友"
        assert AffectionProfile(user_id="x", score=60.0).tier == "好朋友"
        assert AffectionProfile(user_id="x", score=79.999).tier == "好朋友"
        assert AffectionProfile(user_id="x", score=80.0).tier == "重要的人"
        assert AffectionProfile(user_id="x", score=100.0).tier == "重要的人"

    def test_mood_bonus_valence(self) -> None:
        assert AffectionProfile(user_id="x", score=0.0).mood_bonus_valence == 0.0
        assert AffectionProfile(user_id="x", score=25.0).mood_bonus_valence == 0.05
        assert AffectionProfile(user_id="x", score=50.0).mood_bonus_valence == 0.10
        assert AffectionProfile(user_id="x", score=70.0).mood_bonus_valence == 0.18
        assert AffectionProfile(user_id="x", score=90.0).mood_bonus_valence == 0.25

    def test_default_suffix(self) -> None:
        assert AffectionProfile(user_id="x", score=0.0).default_suffix == ""
        assert AffectionProfile(user_id="x", score=25.0).default_suffix == ""
        assert AffectionProfile(user_id="x", score=50.0).default_suffix == "君"
        assert AffectionProfile(user_id="x", score=80.0).default_suffix == "君"

    def test_score_not_clamped_in_profile(self) -> None:
        p = AffectionProfile(user_id="x", score=150.0)
        assert p.score == 150.0  # clamping happens in engine, not profile


# ---------------------------------------------------------------------------
# AffectionStore
# ---------------------------------------------------------------------------

class TestAffectionStore:
    def test_get_new_user_returns_default(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        profile = store.get("12345")
        assert profile.user_id == "12345"
        assert profile.score == 0.0
        assert profile.total_interactions == 0
        assert profile.custom_nickname == ""

    def test_save_and_get_roundtrip(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        profile = AffectionProfile(user_id="12345", score=42.0, total_interactions=10, custom_nickname="小明")
        store.save(profile)

        loaded = store.get("12345")
        assert loaded.user_id == "12345"
        assert loaded.score == 42.0
        assert loaded.total_interactions == 10
        assert loaded.custom_nickname == "小明"

    def test_save_persists_to_disk(self, tmp_path: Path) -> None:
        d = make_temp_dir(tmp_path, "affection")
        store = AffectionStore(storage_dir=str(d))
        profile = AffectionProfile(user_id="abc", score=55.0)
        store.save(profile)
        assert (d / "abc.json").exists()

    @pytest.mark.asyncio
    async def test_startup_loads_existing_files(self, tmp_path: Path) -> None:
        d = make_temp_dir(tmp_path, "affection")
        data = {
            "user_id": "111", "score": 30.0, "custom_nickname": "",
            "last_interaction": "", "total_interactions": 5,
            "first_interaction": "", "daily_count": 2,
            "daily_date": "2026-04-29", "preferred_suffix": "",
        }
        (d / "111.json").write_text(json.dumps(data), encoding="utf-8")

        store = AffectionStore(storage_dir=str(d))
        await store.startup()
        profile = store.get("111")
        assert profile.score == 30.0
        assert profile.total_interactions == 5

    def test_missing_file_returns_default(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        profile = store.get("nonexistent")
        assert profile.user_id == "nonexistent"
        assert profile.score == 0.0

    def test_malformed_json_handled_gracefully(self, tmp_path: Path) -> None:
        d = make_temp_dir(tmp_path, "affection")
        (d / "bad.json").write_text("not json", encoding="utf-8")
        store = AffectionStore(storage_dir=str(d))
        result = store._load_from_disk("bad")
        assert result is None


# ---------------------------------------------------------------------------
# AffectionEngine
# ---------------------------------------------------------------------------

class TestAffectionEngine:
    @pytest.mark.asyncio
    async def test_record_interaction_increments_score(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        await store.startup()
        engine = AffectionEngine(store, score_increment=1.0, daily_cap=20.0)

        profile = await engine.record_interaction("123")
        assert profile.score == 1.0
        assert profile.total_interactions == 1
        assert profile.daily_count == 1

    @pytest.mark.asyncio
    async def test_score_does_not_exceed_100(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        await store.startup()
        engine = AffectionEngine(store, score_increment=20.0, daily_cap=500.0)

        for _ in range(8):
            await engine.record_interaction("123")
        profile = store.get("123")
        assert profile.score == 100.0

    @pytest.mark.asyncio
    async def test_daily_cap_respected(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        await store.startup()
        engine = AffectionEngine(store, score_increment=5.0, daily_cap=15.0)

        for _ in range(10):
            await engine.record_interaction("123")
        profile = store.get("123")
        assert profile.score == 15.0
        assert profile.daily_count == 3  # 15 / 5 = 3

    @pytest.mark.asyncio
    async def test_daily_reset(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        await store.startup()
        engine = AffectionEngine(store, score_increment=5.0, daily_cap=50.0)

        await engine.record_interaction("123")
        profile = store.get("123")
        profile.daily_date = "2020-01-01"  # force old date
        profile.daily_count = 10
        store.save(profile)

        await engine.record_interaction("123")
        reloaded = store.get("123")
        assert reloaded.daily_count == 1  # reset

    def test_set_nickname_persists(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)

        engine.set_nickname("123", "小明")
        profile = store.get("123")
        assert profile.custom_nickname == "小明"

    def test_set_suffix(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)

        engine.set_suffix("123", "酱")
        assert store.get("123").preferred_suffix == "酱"

    def test_resolve_nickname_custom_first(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)
        engine.set_nickname("123", "司君")

        name = engine.resolve_nickname("123", group_card="群小明", qq_nickname="小明")
        assert name == "司君"

    def test_resolve_nickname_group_card_fallback(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)

        name = engine.resolve_nickname("123", group_card="群小明", qq_nickname="小明")
        assert name == "群小明"

    def test_resolve_nickname_qq_fallback(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)

        name = engine.resolve_nickname("123", group_card="", qq_nickname="小明")
        assert name == "小明"

    def test_resolve_nickname_user_id_fallback(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)

        name = engine.resolve_nickname("12345")
        assert name == "12345"

    def test_resolve_suffix_preferred_wins(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)
        engine.set_suffix("123", "酱")

        assert engine.resolve_suffix("123") == "酱"

    def test_resolve_suffix_falls_back_to_default(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)
        p = AffectionProfile(user_id="123", score=50.0)
        store.save(p)

        assert engine.resolve_suffix("123") == "君"

    def test_build_affection_block_new_user(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)

        block = engine.build_affection_block("999")
        assert "初次对话" in block
        assert "999" in block

    def test_build_affection_block_friend(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)
        p = AffectionProfile(user_id="123", score=50.0, total_interactions=20)
        store.save(p)

        block = engine.build_affection_block("123")
        assert "50/100" in block
        assert "朋友" in block
        assert "20 次" in block

    def test_build_affection_block_with_custom_nickname(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)
        engine.set_nickname("123", "司君")

        block = engine.build_affection_block("123")
        assert "司君" in block

    def test_build_affection_block_mood_boost_for_high_affection(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)
        p = AffectionProfile(user_id="123", score=75.0, total_interactions=50)
        store.save(p)

        block = engine.build_affection_block("123")
        assert "心情不太好" in block or "温和" in block

    def test_build_affection_block_no_mood_boost_for_low_affection(self, tmp_path: Path) -> None:
        store = AffectionStore(storage_dir=str(make_temp_dir(tmp_path, "affection")))
        engine = AffectionEngine(store)
        p = AffectionProfile(user_id="123", score=30.0, total_interactions=10)
        store.save(p)

        block = engine.build_affection_block("123")
        assert "心情不太好" not in block
