from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from services.slang import SlangDriftAssessment, SlangSettings, SlangStore


class _FakeDriftReviewer:
    def __init__(self, verdict: str, confidence: float = 0.9, reason: str = "test") -> None:
        self.verdict = verdict
        self.confidence = confidence
        self.reason = reason
        self.calls: list[dict[str, object]] = []

    async def review_drift(self, **kwargs):
        self.calls.append(kwargs)
        return SlangDriftAssessment(
            verdict=self.verdict,  # type: ignore[arg-type]
            confidence=self.confidence,
            reason=self.reason,
            reviewed=True,
        )


@pytest.mark.asyncio
async def test_slang_store_merges_similar_variant_into_existing(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        original = await store.upsert_candidate(
            term="凤笑梦bot",
            meaning="群里的 bot",
            group_id="100",
            user_id="u1",
            min_count=1,
        )
        variant = await store.upsert_candidate(
            term="凤笑梦bot啊",
            meaning="群里的 bot",
            group_id="100",
            user_id="u2",
            min_count=1,
        )
        assert original is not None
        assert variant == original
        term = await store.get_term(original)
        assert term is not None
        assert "凤笑梦bot啊" in term.aliases
        assert term.meta.get("normalizer_similar_existing") is True
        assert term.meta.get("normalization_cluster_id")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_lifecycle_and_group_isolation(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        term_a = await store.upsert_candidate(
            term="猫饼",
            meaning="群里用来形容离谱但可爱的操作",
            aliases=["猫猫饼"],
            group_id="100",
            user_id="u1",
            raw_text="这也太猫饼了",
            confidence=0.55,
            reason="test",
        )
        term_b = await store.upsert_candidate(
            term="猫饼",
            meaning="另一个群里的不同含义",
            group_id="200",
            user_id="u2",
            raw_text="猫饼来了",
            confidence=0.5,
            reason="test",
        )

        assert term_a is not None
        assert term_b is not None
        assert term_a != term_b
        assert await store.build_prompt_block(group_id="100", conversation_text="猫饼") == ""

        await store.set_status(term_a, "approved")
        block_a = await store.build_prompt_block(group_id="100", conversation_text="猫饼")
        block_b = await store.build_prompt_block(group_id="200", conversation_text="猫饼")
        assert "猫饼" in block_a
        assert "离谱但可爱" in block_a
        assert "另一个群" not in block_a
        assert block_b == ""

        await store.set_status(term_a, "muted")
        assert await store.build_prompt_block(group_id="100", conversation_text="猫饼") == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_prompt_block_only_injects_direct_approved_hits(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        await store.create_term(
            term="本群米线",
            meaning="当前群的离谱可爱操作",
            scope="group",
            group_id="100",
            status="approved",
        )
        await store.create_term(
            term="云猫",
            meaning="另一个已批准词条",
            scope="group",
            group_id="100",
            status="approved",
        )
        await store.create_term(
            term="候选词",
            meaning="还在候选",
            scope="group",
            group_id="100",
            status="candidate",
        )
        await store.create_term(
            term="静音词",
            meaning="已静音",
            scope="group",
            group_id="100",
            status="muted",
        )
        await store.create_term(
            term="过期词",
            meaning="已过期",
            scope="group",
            group_id="100",
            status="expired",
        )

        block = await store.build_prompt_block(group_id="100", conversation_text="今天本群米线了")
        assert "本群米线" in block
        assert "云猫" not in block
        assert block.startswith("以下是当前群本轮上下文命中的已批准黑话")

        assert await store.build_prompt_block(group_id="100", conversation_text="候选词") == ""
        assert await store.build_prompt_block(group_id="100", conversation_text="静音词") == ""
        assert await store.build_prompt_block(group_id="100", conversation_text="过期词") == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_global_terms_can_be_closed_per_group(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        await store.create_term(
            term="本群米线",
            meaning="当前群的离谱可爱操作",
            scope="group",
            group_id="100",
            status="approved",
        )
        await store.create_term(
            term="全局猫饼",
            meaning="所有群通用的解释",
            scope="global",
            status="approved",
        )

        default_block = await store.build_prompt_block(group_id="100", conversation_text="全局猫饼")
        assert "所有群通用" in default_block
        default_lookup = await store.lookup_terms(group_id="100", query="全局猫饼")
        assert [term.scope for term in default_lookup] == ["global"]

        settings = await store.load_settings()
        settings.global_excluded_group_ids = ["100"]
        await store.save_settings(settings)

        closed_block = await store.build_prompt_block(group_id="100", conversation_text="全局猫饼")
        assert closed_block == ""
        closed_lookup = await store.lookup_terms(group_id="100", query="全局猫饼")
        assert closed_lookup == []
        local_block = await store.build_prompt_block(group_id="100", conversation_text="本群米线")
        assert "当前群的离谱可爱操作" in local_block

        other_group_block = await store.build_prompt_block(group_id="200", conversation_text="全局猫饼")
        assert "所有群通用" in other_group_block
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_summary_splits_candidate_review_state(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        await store.create_term(
            term="未审候选",
            meaning="还没经过 AI 复核",
            scope="group",
            group_id="100",
            status="candidate",
        )
        await store.create_term(
            term="观察不足",
            meaning="AI 看过但证据不足",
            scope="group",
            group_id="100",
            status="candidate",
            meta={
                "candidate_reviewed": True,
                "candidate_review_approved": False,
                "candidate_review_state": "observing",
                "review_decision": "observe_more",
            },
        )
        rejected = await store.create_term(
            term="AI否决",
            meaning="AI 明确认为不是黑话",
            scope="group",
            group_id="100",
            status="muted",
            meta={
                "candidate_reviewed": True,
                "candidate_review_approved": False,
                "candidate_review_state": "rejected",
                "ai_rejected": True,
            },
        )
        await store.create_term(
            term="已审建议通过",
            meaning="AI 建议通过但还留在候选队列",
            scope="group",
            group_id="100",
            status="candidate",
            meta={
                "candidate_reviewed": True,
                "candidate_review_approved": True,
                "candidate_review_state": "suggested",
                "review_decision": "suggested_approve",
            },
        )
        await store.create_term(
            term="已审失败",
            meaning="AI 复核失败",
            scope="group",
            group_id="100",
            status="candidate",
            meta={
                "candidate_reviewed": False,
                "candidate_review_failed": True,
                "candidate_review_state": "failed",
            },
        )

        summary = await store.summary()
        assert summary["candidate_count"] == 2
        assert summary["candidate_total_count"] == 4
        assert summary["candidate_reviewed_count"] == 2
        assert summary["candidate_unreviewed_count"] == 2
        assert summary["candidate_review_approved_count"] == 1
        assert summary["candidate_review_rejected_count"] == 1
        assert summary["candidate_review_kept_count"] == 1
        assert summary["candidate_review_failed_count"] == 1

        rejected_terms, rejected_total = await store.list_terms(review_filter="candidate_ai_rejected")
        assert rejected_total == 1
        assert rejected_terms[0].term == "AI否决"
        assert rejected_terms[0].status == "muted"

        returned = await store.return_ai_reviewed_term_to_candidate(rejected.term_id)
        assert returned is not None
        assert returned.status == "candidate"
        assert returned.source == "admin_returned"
        assert returned.meta["review_decision"] == "returned_to_candidate"
        assert "ai_rejected" not in returned.meta
        assert "candidate_review_state" not in returned.meta

        rejected_terms, rejected_total = await store.list_terms(review_filter="candidate_ai_rejected")
        assert rejected_total == 0
        unreviewed_terms, unreviewed_total = await store.list_terms(review_filter="candidate_ai_unreviewed")
        assert unreviewed_total == 3
        assert {item.term for item in unreviewed_terms} >= {"未审候选", "AI否决", "已审失败"}

        observing_terms, observing_total = await store.list_terms(review_filter="observe_more")
        assert observing_total == 1
        assert observing_terms[0].term == "观察不足"

        failed_terms, failed_total = await store.list_terms(review_filter="review_failed")
        assert failed_total == 1
        assert failed_terms[0].term == "已审失败"

        approved_terms, approved_total = await store.list_terms(review_filter="candidate_ai_approved")
        assert approved_total == 1
        assert approved_terms[0].term == "已审建议通过"

        unreviewed_terms, unreviewed_total = await store.list_terms(review_filter="candidate_ai_unreviewed")
        assert unreviewed_total == 3
        assert {item.term for item in unreviewed_terms} >= {"未审候选", "AI否决", "已审失败"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_ai_rejected_muted_reobserves_without_immediate_revival(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        rejected = await store.create_term(
            term="潜力词",
            meaning="AI 曾经否决的候选",
            scope="group",
            group_id="100",
            status="muted",
            meta={"ai_rejected": True, "candidate_review_state": "rejected"},
        )

        result = await store.upsert_candidate(
            term="潜力词",
            meaning="再次出现",
            group_id="100",
            user_id="u1",
            message_id=101,
            raw_text="潜力词",
        )
        assert result == rejected.term_id
        repeated = await store.upsert_candidate(
            term="潜力词",
            meaning="重复消息",
            group_id="100",
            user_id="u1",
            message_id=101,
            raw_text="潜力词",
        )
        assert repeated == rejected.term_id

        term = await store.get_term(rejected.term_id)
        assert term is not None
        assert term.status == "muted"
        assert term.meta["rejected_reobserve_count"] == 1
        assert term.meta["rejected_reobserve_users"] == ["u1"]
        assert term.meta["rejected_reobserve_message_ids"] == ["101"]
        unreviewed, total = await store.list_terms(review_filter="candidate_ai_unreviewed")
        assert total == 0
        assert unreviewed == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_ai_rejected_muted_revives_after_three_reobservations(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        rejected = await store.create_term(
            term="复现词",
            meaning="AI 曾经否决的候选",
            scope="group",
            group_id="100",
            status="muted",
            meta={"ai_rejected": True, "candidate_review_state": "rejected", "review_decision": "denied"},
        )
        for idx in range(3):
            await store.upsert_candidate(
                term="复现词",
                meaning="再次出现",
                group_id="100",
                user_id="u1",
                message_id=200 + idx,
                raw_text="复现词",
            )

        term = await store.get_term(rejected.term_id)
        assert term is not None
        assert term.status == "candidate"
        assert term.source == "ai_reject_reobserved"
        assert term.meta["revived_from_ai_reject"] is True
        assert term.meta["revival_reason"] == "reobserved_after_ai_reject"
        assert term.meta["rejected_reobserve_count"] == 3
        assert "ai_rejected" not in term.meta
        assert "candidate_review_state" not in term.meta
        assert "candidate_reviewed" not in term.meta
        unreviewed, total = await store.list_terms(review_filter="candidate_ai_unreviewed")
        assert total == 1
        assert unreviewed[0].term_id == rejected.term_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_ai_rejected_muted_revives_after_two_users(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        rejected = await store.create_term(
            term="双人词",
            meaning="AI 曾经否决的候选",
            scope="group",
            group_id="100",
            status="muted",
            meta={"ai_rejected": True, "candidate_review_state": "rejected"},
        )
        await store.upsert_candidate(term="双人词", meaning="再次出现", group_id="100", user_id="u1", message_id=301)
        await store.upsert_candidate(term="双人词", meaning="再次出现", group_id="100", user_id="u2", message_id=302)

        term = await store.get_term(rejected.term_id)
        assert term is not None
        assert term.status == "candidate"
        assert term.meta["rejected_reobserve_user_count"] == 2
        assert set(term.meta["rejected_reobserve_users"]) == {"u1", "u2"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_manual_muted_and_stoplisted_terms_do_not_reobserve(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        manual = await store.create_term(
            term="人工静音",
            meaning="人工静音项",
            scope="group",
            group_id="100",
            status="muted",
            meta={"manual": True},
        )
        ai_rejected = await store.create_term(
            term="停用词",
            meaning="AI 曾经否决的候选",
            scope="group",
            group_id="100",
            status="muted",
            meta={"ai_rejected": True, "candidate_review_state": "rejected"},
        )
        stoplisted_by_existing_term = await store.create_term(
            term="旧停用主词",
            meaning="AI 曾经否决的候选",
            aliases=["旧停用别名"],
            scope="group",
            group_id="100",
            status="muted",
            meta={"ai_rejected": True, "candidate_review_state": "rejected"},
        )
        settings = await store.load_settings()
        settings.stoplist = ["停用词", "旧停用主词"]
        await store.save_settings(settings)

        await store.upsert_candidate(term="人工静音", meaning="再次出现", group_id="100", user_id="u1", message_id=401)
        await store.upsert_candidate(
            term="停用词",
            meaning="再次出现",
            group_id="100",
            user_id="u1",
            message_id=402,
            settings=settings,
        )
        alias_result = await store.upsert_candidate(
            term="旧停用别名",
            meaning="再次出现",
            group_id="100",
            user_id="u1",
            message_id=403,
            settings=settings,
        )
        assert alias_result is None

        manual_term = await store.get_term(manual.term_id)
        stopped_term = await store.get_term(ai_rejected.term_id)
        stoplisted_by_existing_term = await store.get_term(stoplisted_by_existing_term.term_id)
        assert manual_term is not None
        assert stopped_term is not None
        assert stoplisted_by_existing_term is not None
        assert manual_term.status == "muted"
        assert stopped_term.status == "muted"
        assert stoplisted_by_existing_term.status == "muted"
        assert "rejected_reobserve_count" not in manual_term.meta
        assert "rejected_reobserve_count" not in stopped_term.meta
        assert "rejected_reobserve_count" not in stoplisted_by_existing_term.meta
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_backfills_ai_rejected_reobserve_counters(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        rejected = await store.create_term(
            term="旧复现词",
            meaning="AI 曾经否决的候选",
            scope="group",
            group_id="100",
            status="muted",
            meta={"ai_rejected": True, "candidate_review_state": "rejected"},
        )
        await store.record_observation(
            rejected.term_id, group_id="100", user_id="u1", message_id=501, raw_text="旧复现词",
        )
        await store.record_observation(
            rejected.term_id, group_id="100", user_id="u1", message_id=502, raw_text="旧复现词",
        )
        await store.record_observation(
            rejected.term_id, group_id="100", user_id="u1", message_id=503, raw_text="旧复现词",
        )

        result = await store.backfill_ai_rejected_reobserve_meta()
        assert result["checked"] == 1
        assert result["revived"] == 1
        assert result["updated"] == 1
        again = await store.backfill_ai_rejected_reobserve_meta()
        assert again == {"checked": 0, "updated": 0, "revived": 0}

        term = await store.get_term(rejected.term_id)
        assert term is not None
        assert term.status == "candidate"
        assert term.meta["rejected_reobserve_count"] == 3
        assert term.meta["rejected_reobserve_backfill_version"] == 1
        assert term.meta["revived_from_ai_reject"] is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_matches_alias_and_dedupes_message_hits(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        term_id = await store.upsert_candidate(
            term="猫饼",
            meaning="测试释义",
            aliases=["猫猫饼"],
            group_id="100",
            user_id="u1",
            message_id=1,
            raw_text="猫饼",
        )
        assert term_id is not None
        matches = await store.find_matching_terms(group_id="100", text="今天又猫猫饼了")
        assert [term.term_id for term in matches] == [term_id]

        await store.record_hit(term_id, group_id="100", user_id="u2", message_id=2, raw_text="猫猫饼")
        await store.record_hit(term_id, group_id="100", user_id="u2", message_id=2, raw_text="猫猫饼")
        term = await store.get_term(term_id)
        assert term is not None
        assert term.usage_count == 2
        assert term.unique_user_count == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_requires_stronger_boundaries_for_short_ascii_matches(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        term = await store.create_term(
            term="abc",
            meaning="短缩写",
            scope="group",
            group_id="100",
            status="approved",
        )
        assert await store.find_matching_terms(group_id="100", text="zabcx") == []
        matches = await store.find_matching_terms(group_id="100", text="abc! 继续")
        assert [item.term_id for item in matches] == [term.term_id]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_stoplist_fully_disables_existing_terms(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        term = await store.create_term(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            aliases=["猫猫饼"],
            scope="group",
            group_id="100",
            status="approved",
        )
        assert await store.find_matching_terms(group_id="100", text="猫猫饼")
        assert await store.build_prompt_block(group_id="100", conversation_text="猫饼")
        assert await store.lookup_terms(group_id="100", query="猫饼")

        settings = await store.load_settings()
        settings.stoplist = ["猫饼"]
        await store.save_settings(settings)

        assert await store.find_matching_terms(group_id="100", text="猫猫饼") == []
        assert await store.build_prompt_block(group_id="100", conversation_text="猫饼") == ""
        assert await store.lookup_terms(group_id="100", query="猫饼") == []

        # The term remains in storage so removing it from stoplist restores behavior.
        stored = await store.get_term(term.term_id)
        assert stored is not None
        assert stored.status == "approved"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_stoplist_alias_blocks_existing_terms_and_intake(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        term = await store.create_term(
            term="project sekai",
            meaning="音游",
            aliases=["P J S K"],
            scope="group",
            group_id="100",
            status="approved",
        )
        settings = await store.load_settings()
        settings.stoplist = ["pjsk"]
        await store.save_settings(settings)

        assert await store.find_matching_terms(group_id="100", text="P J S K") == []
        assert await store.build_prompt_block(group_id="100", conversation_text="P J S K") == ""
        assert await store.lookup_terms(group_id="100", query="project sekai") == []

        blocked_candidate = await store.upsert_candidate(
            term="world link",
            meaning="另一个简称",
            aliases=["P J S K"],
            group_id="100",
            raw_text="world link 也叫 P J S K",
            settings=settings,
        )
        assert blocked_candidate is None

        blocked_ai = await store.upsert_ai_approved_term(
            term="world link",
            meaning="另一个简称",
            aliases=["P J S K"],
            group_id="100",
            raw_text="world link 也叫 P J S K",
            settings=settings,
        )
        assert blocked_ai is None

        with pytest.raises(ValueError, match="stoplisted"):
            await store.create_term(
                term="world link",
                meaning="另一个简称",
                aliases=["P J S K"],
                scope="group",
                group_id="100",
                status="approved",
            )

        stored = await store.get_term(term.term_id)
        assert stored is not None
        assert stored.status == "approved"
        terms, total = await store.list_terms(group_id="100")
        assert total == 1
        assert [item.term_id for item in terms] == [term.term_id]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_lists_review_items_newest_first(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        old_time = "2026-05-12T01:00:00+08:00"
        new_time = "2026-05-12T02:00:00+08:00"
        older = await store.create_term(
            term="旧待审",
            meaning="旧的待审项",
            scope="group",
            group_id="100",
            status="candidate",
            confidence=0.99,
        )
        newer = await store.create_term(
            term="新观察",
            meaning="新的观察项",
            scope="group",
            group_id="100",
            status="approved",
            confidence=0.1,
        )
        db = store._require_db()
        await db.execute(
            "UPDATE slang_terms SET updated_at = ?, last_seen_at = ? WHERE term_id = ?",
            (old_time, old_time, older.term_id),
        )
        await db.execute(
            "UPDATE slang_terms SET updated_at = ?, last_seen_at = ? WHERE term_id = ?",
            (new_time, new_time, newer.term_id),
        )
        await store.upsert_candidate(
            term="旧 pending",
            meaning="旧观察",
            group_id="100",
            confidence=0.99,
            min_count=2,
            observed_count=1,
        )
        await store.upsert_candidate(
            term="新 pending",
            meaning="新观察",
            group_id="100",
            confidence=0.1,
            min_count=2,
            observed_count=1,
        )
        await db.execute(
            "UPDATE slang_pending_candidates SET count = 99, confidence = 0.99, last_seen_at = ? WHERE term = ?",
            (old_time, "旧 pending"),
        )
        await db.execute(
            "UPDATE slang_pending_candidates SET count = 1, confidence = 0.1, last_seen_at = ? WHERE term = ?",
            (new_time, "新 pending"),
        )
        await db.commit()

        terms, _total = await store.list_terms(group_id="100")
        assert [item.term for item in terms[:2]] == ["新观察", "旧待审"]
        pending, _pending_total = await store.list_pending(group_id="100")
        assert [item.term for item in pending[:2]] == ["新 pending", "旧 pending"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_settings_cache_refreshes_across_instances(tmp_path):
    db_path = tmp_path / "slang.db"
    primary = SlangStore(db_path)
    secondary = SlangStore(db_path)
    await primary.init()
    await secondary.init()
    try:
        initial = await primary.load_settings()
        assert initial.lookup_tool_enabled is True

        await secondary.save_settings(
            SlangSettings(
                candidate_min_count=7,
                lookup_tool_enabled=False,
                daily_ai_auto_approve_enabled=True,
            )
        )

        refreshed = await primary.load_settings()
        assert refreshed.candidate_min_count == 7
        assert refreshed.lookup_tool_enabled is False
        assert refreshed.daily_ai_auto_approve_enabled is True
    finally:
        await primary.close()
        await secondary.close()


@pytest.mark.asyncio
async def test_slang_store_term_snapshot_cache_refreshes_after_term_writes(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        first = await store.create_term(
            term="旧猫饼",
            meaning="旧梗",
            scope="group",
            group_id="100",
            status="approved",
        )
        initial = await store.find_matching_terms(group_id="100", text="今天又旧猫饼了")
        assert [term.term_id for term in initial] == [first.term_id]

        await store.set_status(first.term_id, "muted")
        assert await store.build_prompt_block(group_id="100", conversation_text="旧猫饼") == ""

        second = await store.create_term(
            term="新猫饼",
            meaning="新梗",
            scope="group",
            group_id="100",
            status="approved",
        )
        refreshed = await store.find_matching_terms(group_id="100", text="今天又新猫饼了")
        assert [term.term_id for term in refreshed] == [second.term_id]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_record_hits_batches_multiple_terms(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        term_a = await store.create_term(
            term="猫饼",
            meaning="离谱但可爱",
            scope="group",
            group_id="100",
            status="approved",
        )
        term_b = await store.create_term(
            term="云猫",
            meaning="远程围观猫猫",
            scope="group",
            group_id="100",
            status="approved",
        )

        changed = await store.record_hits(
            [term_a.term_id, term_b.term_id, term_a.term_id],
            group_id="100",
            user_id="u2",
            message_id=99,
            raw_text="猫饼和云猫都来了",
            context="猫饼和云猫都来了",
        )
        assert changed == 2

        repeated = await store.record_hits(
            [term_a.term_id, term_b.term_id],
            group_id="100",
            user_id="u2",
            message_id=99,
            raw_text="猫饼和云猫都来了",
            context="猫饼和云猫都来了",
        )
        assert repeated == 0

        refreshed_a = await store.get_term(term_a.term_id)
        refreshed_b = await store.get_term(term_b.term_id)
        assert refreshed_a is not None and refreshed_a.usage_count == 1
        assert refreshed_b is not None and refreshed_b.usage_count == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_buffers_low_frequency_candidates_and_stoplist(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        settings = SlangSettings(candidate_min_count=2, stoplist=["普通词"])
        blocked = await store.upsert_candidate(
            term="普通词",
            meaning="不应学习",
            group_id="100",
            raw_text="普通词",
            settings=settings,
        )
        assert blocked is None

        first = await store.upsert_candidate(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            group_id="100",
            user_id="u1",
            raw_text="猫饼第一次出现",
            min_count=settings.candidate_min_count,
            observed_count=1,
            settings=settings,
        )
        assert first is None
        pending, pending_total = await store.list_pending(group_id="100")
        assert pending_total == 1
        assert pending[0].count == 1

        promoted = await store.upsert_candidate(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            group_id="100",
            user_id="u2",
            raw_text="猫饼第二次出现",
            min_count=settings.candidate_min_count,
            observed_count=1,
            settings=settings,
        )
        assert promoted is not None
        pending, pending_total = await store.list_pending(group_id="100")
        assert pending == []
        assert pending_total == 0
        term = await store.get_term(promoted)
        assert term is not None
        assert term.usage_count == 2
        assert term.status == "candidate"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_merges_alias_collision_into_existing_candidate(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        existing_id = await store.upsert_candidate(
            term="project sekai",
            meaning="游戏全称",
            aliases=["プロセカ"],
            group_id="100",
            user_id="u1",
            raw_text="project sekai",
            confidence=0.72,
        )
        assert existing_id is not None

        collided_id = await store.upsert_candidate(
            term="pjsk",
            meaning="常见缩写",
            aliases=["project sekai"],
            group_id="100",
            user_id="u2",
            raw_text="pjsk",
            confidence=0.75,
        )
        assert collided_id == existing_id

        terms, total = await store.list_terms(group_id="100")
        assert total == 1
        assert len(terms) == 1
        assert "pjsk" in terms[0].aliases
        assert "project sekai" not in terms[0].aliases
        assert terms[0].usage_count >= 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_promotes_pending_into_existing_without_losing_aliases(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        settings = SlangSettings(candidate_min_count=2)
        first = await store.upsert_candidate(
            term="pjsk",
            meaning="project sekai 的群内简称",
            aliases=["啤酒烧烤"],
            group_id="100",
            user_id="u2",
            raw_text="pjsk 第一次出现",
            min_count=2,
            observed_count=1,
            settings=settings,
        )
        assert first is None

        existing = await store.create_term(
            term="project sekai",
            meaning="游戏全称",
            aliases=["pjsk"],
            scope="group",
            group_id="100",
            status="candidate",
            confidence=0.68,
        )
        existing_id = existing.term_id

        promoted = await store.upsert_candidate(
            term="pjsk",
            meaning="project sekai 的群内简称",
            aliases=["啤酒烧烤"],
            group_id="100",
            user_id="u3",
            raw_text="pjsk 第二次出现",
            min_count=2,
            observed_count=1,
            settings=settings,
        )
        assert promoted == existing_id

        pending, pending_total = await store.list_pending(group_id="100")
        assert pending_total == 0
        assert pending == []

        term = await store.get_term(existing_id)
        assert term is not None
        assert "pjsk" in term.aliases
        assert "啤酒烧烤" in term.aliases
        assert term.usage_count >= 2
        assert term.unique_user_count >= 2

        observations = await store.list_observations(existing_id)
        raw_texts = [item.raw_text for item in observations]
        assert any("pjsk 第一次出现" in text for text in raw_texts)
        assert any("pjsk 第二次出现" in text for text in raw_texts)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_v2_bulk_merge_global_stats_and_runs(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        target_id = await store.upsert_candidate(term="猫饼", meaning="离谱但可爱", group_id="100", user_id="u1")
        source_id = await store.upsert_candidate(term="猫猫饼", meaning="离谱但可爱", group_id="100", user_id="u2")
        assert target_id is not None
        assert source_id is not None

        bulk = await store.bulk_set_status([target_id, source_id], "approved")
        assert bulk == {"requested": 2, "changed": 2}
        target = await store.get_term(target_id)
        assert target is not None
        assert target.confidence >= 0.8

        merged = await store.merge_terms(target_id=target_id, source_ids=[source_id])
        assert merged is not None
        assert "猫猫饼" in merged.aliases
        expired = await store.get_term(source_id)
        assert expired is not None
        assert expired.status == "expired"
        assert expired.meta["merged_into"] == target_id
        assert len(await store.list_observations(target_id)) >= 2

        for group_id in ["200", "300", "400"]:
            created = await store.upsert_candidate(
                term="云吸猫饼",
                meaning="远程围观离谱但可爱的事情",
                group_id=group_id,
                user_id=f"u{group_id}",
                confidence=0.6,
            )
            assert created is not None
        global_result = await store.scan_global_candidates(min_groups=3)
        assert global_result["created"] == 1
        global_terms, _total = await store.list_terms(scope="global", status="candidate")
        assert len(global_terms) == 1
        assert global_terms[0].meta["global_candidate"] is True

        recomputed = await store.recompute_confidence(target_id)
        assert recomputed is not None
        assert "confidence_signals" in recomputed.meta

        run_id = await store.start_extraction_run(group_count=1)
        await store.finish_extraction_run(
            run_id,
            scanned_messages=12,
            extracted_terms=3,
            promoted_candidates=1,
        )
        runs = await store.list_extraction_runs()
        assert runs[0].run_id == run_id
        assert runs[0].status == "success"

        stats = await store.stats(days=7)
        assert stats["review"]["total_terms"] >= 4
        assert stats["injection"]["global_candidates"] == 1
        assert stats["popular_terms"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_global_scan_ignores_alias_only_collisions(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        created_a = await store.upsert_candidate(
            term="pjsk",
            meaning="A 群把它当游戏简称",
            group_id="100",
            confidence=0.7,
        )
        created_b = await store.upsert_candidate(
            term="ptt",
            meaning="B 群另一个无关说法",
            aliases=["pjsk"],
            group_id="200",
            confidence=0.7,
        )
        assert created_a is not None
        assert created_b is not None

        result = await store.scan_global_candidates(min_groups=2)
        assert result["created"] == 0
        global_terms, total = await store.list_terms(scope="global")
        assert total == 0
        assert global_terms == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_pending_merge_prefilters_but_keeps_alias_collisions(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        unrelated_id = await store.upsert_candidate(
            term="云狗",
            meaning="无关候选",
            group_id="100",
            user_id="u1",
            raw_text="云狗 第一次出现",
            confidence=0.5,
            min_count=2,
            observed_count=1,
        )
        alias_pending_id = await store.upsert_candidate(
            term="离谱猫",
            meaning="猫饼别名",
            aliases=["猫饼"],
            group_id="100",
            user_id="u2",
            raw_text="离谱猫 第一次出现",
            confidence=0.8,
            min_count=2,
            observed_count=1,
        )
        assert unrelated_id is None
        assert alias_pending_id is None

        existing = await store.create_term(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            scope="group",
            group_id="100",
            status="candidate",
            confidence=0.7,
        )
        existing_id = existing.term_id
        assert existing_id is not None

        merged = await store.upsert_candidate(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            group_id="100",
            confidence=0.8,
        )
        assert merged == existing_id
        term = await store.get_term(existing_id)
        assert term is not None
        assert "离谱猫" in term.aliases
        pending, total = await store.list_pending(group_id="100")
        assert total == 1
        assert pending[0].term == "云狗"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_pending_merge_uses_normalized_alias_key_index(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        first = await store.upsert_candidate(
            term="world link",
            meaning="project sekai 的活动简称",
            aliases=["P J S K"],
            group_id="100",
            user_id="u1",
            raw_text="world link 第一次出现",
            confidence=0.8,
            min_count=2,
            observed_count=1,
        )
        assert first is None

        existing = await store.create_term(
            term="pjsk",
            meaning="音游 project sekai",
            scope="group",
            group_id="100",
            status="candidate",
            confidence=0.7,
        )

        merged = await store.upsert_candidate(
            term="pjsk",
            meaning="音游 project sekai",
            group_id="100",
            confidence=0.8,
        )
        assert merged == existing.term_id
        refreshed = await store.get_term(existing.term_id)
        assert refreshed is not None
        assert "world link" in refreshed.aliases
        assert "P J S K" not in refreshed.aliases
        pending, total = await store.list_pending(group_id="100")
        assert pending == []
        assert total == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_rebuilds_pending_key_index_for_legacy_rows(tmp_path):
    db_path = tmp_path / "slang.db"
    store = SlangStore(db_path)
    await store.init()
    try:
        db = store._require_db()
        now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        await db.execute(
            """INSERT INTO slang_pending_candidates
               (pending_id, term_key, term, meaning, aliases_json, group_id, confidence,
                count, unique_users_json, evidence, reason, repeat_policy, first_seen_at,
                last_seen_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "pending_legacy",
                "worldlink",
                "world link",
                "project sekai 的活动简称",
                json.dumps(["P J S K"], ensure_ascii=False),
                "100",
                0.8,
                1,
                json.dumps(["u1"], ensure_ascii=False),
                "world link 第一次出现",
                "legacy_seed",
                "understand_only",
                now,
                now,
                "{}",
            ),
        )
        await db.execute("DELETE FROM slang_pending_candidate_keys WHERE pending_id = ?", ("pending_legacy",))
        await db.commit()
    finally:
        await store.close()

    reopened = SlangStore(db_path)
    await reopened.init()
    try:
        existing = await reopened.create_term(
            term="pjsk",
            meaning="音游 project sekai",
            scope="group",
            group_id="100",
            status="candidate",
            confidence=0.7,
        )
        merged = await reopened.upsert_candidate(
            term="pjsk",
            meaning="音游 project sekai",
            group_id="100",
            confidence=0.8,
        )
        assert merged == existing.term_id
        refreshed = await reopened.get_term(existing.term_id)
        assert refreshed is not None
        assert "world link" in refreshed.aliases
        pending, total = await reopened.list_pending(group_id="100")
        assert pending == []
        assert total == 0
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_slang_store_lists_and_abandons_stale_daily_review_runs(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        run_id = await store.start_extraction_run(group_count=1, meta={"kind": "daily_ai_review"})
        db = store._require_db()
        stale_started_at = (
            datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(minutes=20)
        ).isoformat(timespec="seconds")
        await db.execute(
            "UPDATE slang_extraction_runs SET started_at = ? WHERE run_id = ?",
            (stale_started_at, run_id),
        )
        await db.commit()

        stale_runs = await store.list_stale_extraction_runs(
            kind="daily_ai_review",
            stale_before_iso=(
                datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(minutes=10)
            ).isoformat(timespec="seconds"),
        )
        assert [run.run_id for run in stale_runs] == [run_id]

        await store.abandon_extraction_run(run_id, reason="stale_daily_ai_review_recovered")
        runs = await store.list_extraction_runs(limit=5)
        assert runs[0].run_id == run_id
        assert runs[0].status == "abandoned"
        assert runs[0].error == "stale_daily_ai_review_recovered"
        assert runs[0].meta["abandoned"] is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_create_manual_terms(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        term = await store.create_term(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            aliases=["猫猫饼"],
            scope="group",
            group_id="100",
            status="approved",
            repeat_policy="allow_rephrase",
            notes="人工录入",
            evidence="这也太猫饼了",
        )
        assert term.source == "manual"
        assert term.status == "approved"
        assert term.confidence >= 0.8
        assert term.meta["manual"] is True
        assert "猫猫饼" in term.aliases
        observations = await store.list_observations(term.term_id)
        assert observations[0].reason == "manual_evidence"

        block = await store.build_prompt_block(group_id="100", conversation_text="猫饼")
        assert "猫饼" in block
        with pytest.raises(ValueError, match="already exists"):
            await store.create_term(
                term="猫猫饼",
                meaning="重复别名",
                scope="group",
                group_id="100",
            )

        global_term = await store.create_term(
            term="全局猫饼",
            meaning="所有群都适用的解释",
            scope="global",
            status="candidate",
            confidence=0.4,
        )
        assert global_term.scope == "global"
        assert global_term.group_id == ""
        assert global_term.status == "candidate"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_ai_approved_terms_and_human_review(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        term_id = await store.upsert_ai_approved_term(
            term="猫饼",
            meaning="网络梗，用来形容离谱但可爱的操作",
            aliases=["猫猫饼"],
            group_id="100",
            user_id="u1",
            raw_text="这个猫饼太典了",
            confidence=0.9,
            reason="搜索结果和群内证据一致",
            meta={
                "search_queries": ["猫饼 是什么梗"],
                "search_evidence": "猫饼相关梗解释",
                "group_evidence": "这个猫饼太典了",
            },
        )
        assert term_id is not None
        term = await store.get_term(term_id)
        assert term is not None
        assert term.status == "approved"
        assert term.source == "ai_auto_review"
        assert term.meta["ai_approved"] is True
        assert term.meta["human_reviewed"] is False

        block = await store.build_prompt_block(group_id="100", conversation_text="猫饼")
        assert "猫饼" in block

        ai_terms, total = await store.list_terms(review_filter="needs_human_review")
        assert total == 1
        assert ai_terms[0].term_id == term_id

        reviewed = await store.mark_human_reviewed(term_id)
        assert reviewed is not None
        assert reviewed.status == "approved"
        assert reviewed.meta["human_reviewed"] is True

        reviewed_terms, reviewed_total = await store.list_terms(review_filter="human_reviewed")
        assert reviewed_total == 1
        assert reviewed_terms[0].term_id == term_id

        denied = await store.deny_ai_reviewed_term(term_id)
        assert denied is not None
        assert denied.status == "muted"
        assert denied.meta["review_decision"] == "denied"

        returned = await store.return_ai_reviewed_term_to_candidate(term_id)
        assert returned is not None
        assert returned.status == "candidate"
        assert returned.source == "admin_returned"
        assert returned.meta["review_decision"] == "returned_to_candidate"
        assert "ai_approved" not in returned.meta
        assert "ai_rejected" not in returned.meta
        assert "candidate_review_state" not in returned.meta
        assert "candidate_review_approved" not in returned.meta
        assert "human_reviewed" not in returned.meta

        unreviewed_terms, unreviewed_total = await store.list_terms(review_filter="candidate_ai_unreviewed")
        assert unreviewed_total == 1
        assert unreviewed_terms[0].term_id == term_id
        assert await store.build_prompt_block(group_id="100", conversation_text="猫饼") == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_v3_drift_reviews_revisions_and_min_inject_confidence(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        store.set_drift_reviewer(_FakeDriftReviewer("real_drift", reason="核心指代变化"))
        settings = SlangSettings(drift_detection_enabled=True, drift_min_confidence=0.6, min_inject_confidence=0.9)
        await store.save_settings(settings)
        term = await store.create_term(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            scope="group",
            group_id="100",
            status="approved",
            confidence=0.82,
        )

        drift_result = await store.upsert_candidate(
            term="猫饼",
            meaning="群里用来称呼一个固定成员的昵称",
            group_id="100",
            user_id="u2",
            raw_text="猫饼今天又迟到了",
            confidence=0.91,
            reason="出现了与旧释义冲突的新解释",
            settings=settings,
        )
        assert drift_result == term.term_id

        unchanged = await store.get_term(term.term_id)
        assert unchanged is not None
        assert unchanged.meaning == "群里说离谱但可爱的操作"
        assert await store.build_prompt_block(group_id="100", conversation_text="猫饼") == ""

        drift_reviews, drift_total = await store.list_drift_reviews()
        assert drift_total == 1
        assert drift_reviews[0].old_meaning == "群里说离谱但可爱的操作"
        assert drift_reviews[0].new_meaning == "群里用来称呼一个固定成员的昵称"
        assert drift_reviews[0].meta["drift_semantic_verdict"] == "real_drift"

        accepted = await store.accept_drift_review(drift_reviews[0].drift_id)
        assert accepted is not None
        assert accepted.meaning == "群里用来称呼一个固定成员的昵称"
        assert accepted.confidence >= 0.91

        revisions = await store.list_revisions(term.term_id)
        actions = [revision.action for revision in revisions]
        assert "drift_detected" in actions
        assert "drift_accept" in actions

        settings.min_inject_confidence = 0.95
        await store.save_settings(settings)
        assert await store.build_prompt_block(group_id="100", conversation_text="猫饼") == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_v3_reject_and_alias_drift_review(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        store.set_drift_reviewer(_FakeDriftReviewer("real_drift", reason="核心指代变化"))
        settings = SlangSettings(drift_detection_enabled=True, drift_min_confidence=0.6)
        term = await store.create_term(
            term="云猫",
            meaning="远程围观猫猫相关内容",
            scope="group",
            group_id="100",
            status="approved",
            confidence=0.9,
        )
        await store.upsert_candidate(
            term="云猫",
            meaning="某个成员的新称呼",
            aliases=["云猫猫"],
            group_id="100",
            confidence=0.88,
            raw_text="云猫来了",
            settings=settings,
        )
        drift_reviews, _total = await store.list_drift_reviews()
        assert len(drift_reviews) == 1

        aliased = await store.alias_drift_review(drift_reviews[0].drift_id)
        assert aliased is not None
        assert "云猫猫" in aliased.aliases
        processed, processed_total = await store.list_drift_reviews(status="aliased")
        assert processed_total == 1
        assert processed[0].drift_id == drift_reviews[0].drift_id

        await store.upsert_candidate(
            term="云猫",
            meaning="另一种完全不同的新释义",
            group_id="100",
            confidence=0.9,
            raw_text="云猫不是远程围观",
            settings=settings,
        )
        open_reviews, _open_total = await store.list_drift_reviews()
        assert open_reviews
        assert await store.reject_drift_review(open_reviews[0].drift_id) is True
        rejected, rejected_total = await store.list_drift_reviews(status="rejected")
        assert rejected_total == 1
        assert rejected[0].term_id == term.term_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_drift_gate_suppresses_same_meaning(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        reviewer = _FakeDriftReviewer("same_meaning", reason="同义改写")
        store.set_drift_reviewer(reviewer)
        settings = SlangSettings(drift_detection_enabled=True, drift_min_confidence=0.6)
        term = await store.create_term(
            term="没米",
            meaning="没有钱或没有资源（如积分、虚拟货币），与“米”作为钱的代称相关",
            scope="group",
            group_id="100",
            status="approved",
            confidence=0.82,
        )

        result = await store.upsert_candidate(
            term="没米",
            meaning="没钱或没资源的意思",
            group_id="100",
            confidence=0.9,
            raw_text="没米了",
            reason="同义短释义",
            settings=settings,
        )
        assert result == term.term_id

        refreshed = await store.get_term(term.term_id)
        assert refreshed is not None
        assert refreshed.meaning == term.meaning
        drift_reviews, drift_total = await store.list_drift_reviews()
        assert drift_reviews == []
        assert drift_total == 0
        revisions = await store.list_revisions(term.term_id)
        assert revisions[0].action == "drift_suppressed"
        assert revisions[0].meta["drift_semantic_verdict"] == "same_meaning"
        assert len(reviewer.calls) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_drift_gate_unclear_fails_closed(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        store.set_drift_reviewer(_FakeDriftReviewer("unclear", confidence=0.9, reason="证据不足"))
        settings = SlangSettings(drift_detection_enabled=True, drift_min_confidence=0.6)
        term = await store.create_term(
            term="云猫",
            meaning="远程围观猫猫相关内容",
            scope="group",
            group_id="100",
            status="approved",
            confidence=0.82,
        )

        result = await store.upsert_candidate(
            term="云猫",
            meaning="另一个看不清的新解释",
            group_id="100",
            confidence=0.9,
            raw_text="云猫来了",
            reason="证据不足",
            settings=settings,
        )
        assert result == term.term_id

        refreshed = await store.get_term(term.term_id)
        assert refreshed is not None
        assert refreshed.meaning == term.meaning
        _reviews, drift_total = await store.list_drift_reviews()
        assert drift_total == 0
        revisions = await store.list_revisions(term.term_id)
        assert revisions[0].action == "drift_suppressed"
        assert revisions[0].meta["drift_semantic_verdict"] == "unclear"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_drift_gate_without_reviewer_fails_closed(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        settings = SlangSettings(drift_detection_enabled=True, drift_min_confidence=0.6)
        term = await store.create_term(
            term="没米",
            meaning="没有钱或没有资源",
            scope="group",
            group_id="100",
            status="approved",
            confidence=0.82,
        )

        result = await store.upsert_candidate(
            term="没米",
            meaning="固定成员的新称呼",
            group_id="100",
            confidence=0.9,
            raw_text="没米来了",
            settings=settings,
        )

        assert result == term.term_id
        refreshed = await store.get_term(term.term_id)
        assert refreshed is not None
        assert refreshed.meaning == term.meaning
        _reviews, drift_total = await store.list_drift_reviews()
        assert drift_total == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_drift_gate_alias_candidate_merges_alias_only(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        store.set_drift_reviewer(_FakeDriftReviewer("alias_candidate", confidence=0.92, reason="只是别名"))
        settings = SlangSettings(drift_detection_enabled=True, drift_min_confidence=0.6)
        term = await store.create_term(
            term="project sekai",
            meaning="音乐游戏 Project SEKAI",
            aliases=["pjsk"],
            scope="group",
            group_id="100",
            status="approved",
            confidence=0.82,
        )

        result = await store.upsert_candidate(
            term="pjsk",
            meaning="世界计划玩家说的游戏简称",
            aliases=["啤酒烧烤"],
            group_id="100",
            confidence=0.9,
            raw_text="pjsk真好玩",
            reason="别名使用",
            settings=settings,
        )
        assert result == term.term_id

        refreshed = await store.get_term(term.term_id)
        assert refreshed is not None
        assert refreshed.meaning == term.meaning
        assert "啤酒烧烤" in refreshed.aliases
        _reviews, drift_total = await store.list_drift_reviews()
        assert drift_total == 0
        revisions = await store.list_revisions(term.term_id)
        assert revisions[0].action == "candidate_update"
        assert any(revision.action == "drift_alias_candidate" for revision in revisions)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_replays_open_drift_reviews(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
        settings = SlangSettings(drift_detection_enabled=True, drift_min_confidence=0.6)
        store.set_drift_reviewer(_FakeDriftReviewer("real_drift", reason="核心指代变化"))
        await store.create_term(
            term="没米",
            meaning="没有钱或没有资源",
            scope="group",
            group_id="100",
            status="approved",
            confidence=0.82,
        )
        await store.upsert_candidate(
            term="没米",
            meaning="一种完全不同的新释义",
            group_id="100",
            confidence=0.9,
            raw_text="没米了",
            settings=settings,
        )
        open_reviews, drift_total = await store.list_drift_reviews()
        assert drift_total == 1

        store.set_drift_reviewer(_FakeDriftReviewer("same_meaning", reason="回放判断为同义"))
        dry_run = await store.replay_open_drift_reviews(apply=False)
        assert dry_run["reviewed"] == 1
        assert dry_run["closed_same_meaning"] == 1
        still_open, _total = await store.list_drift_reviews()
        assert still_open[0].drift_id == open_reviews[0].drift_id

        applied = await store.replay_open_drift_reviews(apply=True)
        assert applied["closed_same_meaning"] == 1
        closed, closed_total = await store.list_drift_reviews(status="rejected")
        assert closed_total == 1
        assert closed[0].meta["semantic_replay"] is True
        assert closed[0].meta["drift_semantic_verdict"] == "same_meaning"
    finally:
        await store.close()
