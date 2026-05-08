from __future__ import annotations

import pytest

from services.slang import SlangSettings, SlangStore


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
        assert await store.build_prompt_block(group_id="100", conversation_text="猫饼") == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slang_store_v3_drift_reviews_revisions_and_min_inject_confidence(tmp_path):
    store = SlangStore(tmp_path / "slang.db")
    await store.init()
    try:
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
