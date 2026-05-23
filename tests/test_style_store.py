import pytest

from services.style import NewStyleExpression, StyleStore, normalize_style_key


@pytest.fixture
async def store(tmp_path) -> StyleStore:
    s = StyleStore(tmp_path / "style.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_style_store_init_creates_tables(store: StyleStore) -> None:
    cursor = await store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='style_expressions'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["name"] == "style_expressions"
    cursor = await store._db.execute("PRAGMA journal_mode")
    journal = await cursor.fetchone()
    assert journal[0] == "delete"
    cursor = await store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='style_observations'"
    )
    row = await cursor.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_style_store_upserts_expression_with_evidence_and_revision(store: StyleStore) -> None:
    expression = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="短促附和再接一点明亮反应",
            group_id="100",
            risk_tags=["sarcasm", "sarcasm"],
            output_policy="transform",
        ),
        evidence={
            "group_id": "100",
            "speaker": "Alice(1)",
            "raw_text": "这也太离谱了吧",
            "context": "Alice(1): 这也太离谱了吧",
            "source_type": "human",
            "message_id": 42,
        },
        actor="test",
    )

    assert expression.expression_id.startswith("sty_")
    assert expression.scope == "group"
    assert expression.group_id == "100"
    assert expression.status == "pending"
    assert expression.risk_tags == ["sarcasm"]
    assert expression.output_policy == "transform"

    evidence = await store.list_evidence(expression.expression_id)
    assert len(evidence) == 1
    assert evidence[0].raw_text == "这也太离谱了吧"

    revisions = await store.list_revisions(expression.expression_id)
    assert len(revisions) == 1
    assert revisions[0].action == "create"


@pytest.mark.asyncio
async def test_style_store_record_observation_dedupes_by_message_and_trigger(store: StyleStore) -> None:
    expression = await store.upsert_expression(
        NewStyleExpression(
            situation="有人连续吐槽",
            style="短促接梗但不抢话",
            group_id="100",
        )
    )

    first = await store.record_observation(
        expression.expression_id,
        message_id="req_1",
        trigger_type="expression_inject",
        group_id="100",
        scope="group",
        meta={"candidate_id": "pbc_1"},
    )
    duplicate = await store.record_observation(
        expression.expression_id,
        message_id="req_1",
        trigger_type="expression_inject",
        group_id="100",
        scope="group",
    )
    other_trigger = await store.record_observation(
        expression.expression_id,
        message_id="req_1",
        trigger_type="profile_inject",
        group_id="100",
        scope="group",
    )

    assert first is True
    assert duplicate is False
    assert other_trigger is True
    cursor = await store._db.execute(
        "SELECT COUNT(*) AS cnt FROM style_observations WHERE expression_id = ?",
        (expression.expression_id,),
    )
    row = await cursor.fetchone()
    assert row["cnt"] == 2


@pytest.mark.asyncio
async def test_style_store_dedupes_same_group_and_merges_risk_policy(store: StyleStore) -> None:
    first = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="短促附和再接一点明亮反应",
            group_id="100",
            confidence=0.4,
            risk_tags=["sarcasm"],
            output_policy="allow_use",
        )
    )
    second = await store.upsert_expression(
        NewStyleExpression(
            situation=" 大家 在 轻松吐槽！",
            style="短促附和，再接一点明亮反应",
            group_id="100",
            confidence=0.8,
            risk_tags=["profanity"],
            output_policy="observe_only",
        )
    )

    assert second.expression_id == first.expression_id
    assert second.count == 2
    assert second.confidence == 0.8
    assert second.risk_tags == ["sarcasm", "profanity"]
    assert second.output_policy == "observe_only"

    expressions, total = await store.list_expressions(group_id="100")
    assert total == 1
    assert len(expressions) == 1


@pytest.mark.asyncio
async def test_style_store_keeps_groups_isolated(store: StyleStore) -> None:
    left = await store.upsert_expression(
        NewStyleExpression(situation="有人分享成果", style="先开心再说具体喜欢的点", group_id="100")
    )
    right = await store.upsert_expression(
        NewStyleExpression(situation="有人分享成果", style="先开心再说具体喜欢的点", group_id="200")
    )

    assert left.expression_id != right.expression_id
    expressions, total = await store.list_expressions(group_id="100")
    assert total == 1
    assert expressions[0].group_id == "100"


@pytest.mark.asyncio
async def test_style_store_global_scope_dedupes_across_source_groups(store: StyleStore) -> None:
    first = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="短促附和再转成符合人设的回应",
            scope="global",
            group_id="100",
        ),
        evidence={"group_id": "100", "raw_text": "这也太离谱了吧"},
    )
    second = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="短促附和再转成符合人设的回应",
            scope="global",
            group_id="200",
        ),
        evidence={"group_id": "200", "raw_text": "真绷不住了"},
    )

    assert second.expression_id == first.expression_id
    assert second.scope == "global"
    assert second.group_id == "global"
    assert second.count == 2

    evidence = await store.list_evidence(second.expression_id)
    assert {item.group_id for item in evidence} == {"100", "200"}


@pytest.mark.asyncio
async def test_style_store_list_expressions_supports_sort_modes(store: StyleStore) -> None:
    newer = await store.upsert_expression(
        NewStyleExpression(
            situation="有人分享成果",
            style="先开心再说具体喜欢的点",
            group_id="100",
            confidence=0.7,
        )
    )
    older = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和再接一点明亮反应",
            group_id="100",
            confidence=0.95,
        )
    )
    await store._db.execute(  # type: ignore[union-attr]
        "UPDATE style_expressions SET updated_at = ? WHERE expression_id = ?",
        ("2026-05-12T10:00:00+08:00", older.expression_id),
    )
    await store._db.execute(  # type: ignore[union-attr]
        "UPDATE style_expressions SET updated_at = ? WHERE expression_id = ?",
        ("2026-05-13T10:00:00+08:00", newer.expression_id),
    )
    await store._db.commit()  # type: ignore[union-attr]

    default_items, _ = await store.list_expressions(group_id="100", sort="default")
    time_items, _ = await store.list_expressions(group_id="100", sort="time")

    assert default_items[0].expression_id == older.expression_id
    assert time_items[0].expression_id == newer.expression_id


@pytest.mark.asyncio
async def test_style_store_status_update_records_revision(store: StyleStore) -> None:
    expression = await store.upsert_expression(
        NewStyleExpression(situation="有人分享成果", style="先开心再说具体喜欢的点", group_id="100")
    )

    ok = await store.set_status(expression.expression_id, "approved", actor="tester", reason="looks good")
    assert ok
    updated = await store.get_expression(expression.expression_id)
    assert updated is not None
    assert updated.status == "approved"

    revisions = await store.list_revisions(expression.expression_id)
    assert [item.action for item in revisions] == ["update", "create"]
    assert revisions[0].actor == "tester"
    assert revisions[0].reason == "looks good"


@pytest.mark.asyncio
async def test_style_store_builds_prompt_block_for_relevant_approved_only(store: StyleStore) -> None:
    approved = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            group_id="100",
            confidence=0.8,
            output_policy="allow_use",
        )
    )
    await store.set_status(approved.expression_id, "approved")
    pending = await store.upsert_expression(
        NewStyleExpression(
            situation="有人分享成果",
            style="先表达开心，再说一个具体喜欢的点",
            group_id="100",
            confidence=0.9,
        )
    )
    await store.set_status(pending.expression_id, "pending")
    observe = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="只观察这个风险口癖，不主动模仿",
            group_id="100",
            confidence=0.9,
            output_policy="observe_only",
        )
    )
    await store.set_status(observe.expression_id, "approved")

    block = await store.build_prompt_block(
        group_id="100",
        conversation_text="刚才大家在轻松吐槽这件事",
    )

    assert "【表达习惯参考】" in block
    assert "大家在轻松吐槽" in block
    assert "有人分享成果" not in block
    assert "只观察这个风险口癖" not in block


@pytest.mark.asyncio
async def test_style_store_prompt_block_respects_global_scope_flag(store: StyleStore) -> None:
    global_expr = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            scope="global",
            group_id="100",
            confidence=0.8,
        )
    )
    await store.set_status(global_expr.expression_id, "approved")

    closed = await store.build_prompt_block(
        group_id="200",
        conversation_text="大家在轻松吐槽",
        include_global=False,
    )
    opened = await store.build_prompt_block(
        group_id="200",
        conversation_text="大家在轻松吐槽",
        include_global=True,
    )

    assert closed == ""
    assert "大家在轻松吐槽" in opened


@pytest.mark.asyncio
async def test_style_store_prompt_block_transforms_risk_tagged_expression(store: StyleStore) -> None:
    expression = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="理解脏话里的情绪强度",
            group_id="100",
            confidence=0.9,
            risk_tags=["profanity"],
            output_policy="allow_use",
        )
    )
    await store.set_status(expression.expression_id, "approved")

    block = await store.build_prompt_block(
        group_id="100",
        conversation_text="大家在轻松吐槽",
    )

    assert "输出时按凤笑梦人设和当前心情转译" in block
    assert "不要原样复刻" in block


@pytest.mark.asyncio
async def test_style_store_records_expression_feedback_and_adjusts_confidence(store: StyleStore) -> None:
    expression = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            group_id="100",
            confidence=0.6,
        )
    )

    updated = await store.record_expression_feedback(
        expression.expression_id,
        rating="positive",
        actor="tester",
        reason="回复更自然",
    )

    assert updated is not None
    assert updated.confidence > expression.confidence
    assert updated.count == expression.count + 1
    feedback, total = await store.list_feedback(target_type="expression", target_id=expression.expression_id)
    assert total == 1
    assert feedback[0].rating == "positive"
    revisions = await store.list_revisions(expression.expression_id)
    assert revisions[0].action == "feedback_positive"


@pytest.mark.asyncio
async def test_style_store_generates_enabled_profile_and_rollback(store: StyleStore) -> None:
    first = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            group_id="100",
            confidence=0.8,
        )
    )
    await store.set_status(first.expression_id, "approved")
    first_profile = await store.generate_profile(group_id="100", actor="tester", enable=True)

    second = await store.upsert_expression(
        NewStyleExpression(
            situation="有人分享成果",
            style="先表达开心，再说一个具体喜欢的点",
            group_id="100",
            confidence=0.9,
        )
    )
    await store.set_status(second.expression_id, "approved")
    second_profile = await store.generate_profile(group_id="100", actor="tester", enable=True)
    draft_profile = await store.generate_profile(group_id="100", actor="tester", enable=False)

    assert first_profile.version == 1
    assert second_profile.version == 2
    assert draft_profile.version == 3
    current = await store.get_enabled_profiles(group_id="100")
    assert [item.profile_id for item in current] == [second_profile.profile_id]
    block = await store.build_profile_prompt_block(group_id="100")
    assert "【当前动态风格档案】" in block
    assert "v2" in block

    rolled_back = await store.rollback_profile(group_id="100", actor="tester")

    assert rolled_back is not None
    assert rolled_back.profile_id == first_profile.profile_id
    current_after = await store.get_enabled_profiles(group_id="100")
    assert [item.profile_id for item in current_after] == [first_profile.profile_id]


@pytest.mark.asyncio
async def test_style_store_summary_counts_statuses_and_risk_tags(store: StyleStore) -> None:
    first = await store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="理解尖锐吐槽但输出时转译",
            group_id="100",
            risk_tags=["profanity"],
            output_policy="transform",
        )
    )
    await store.upsert_expression(
        NewStyleExpression(situation="有人分享成果", style="先开心再说具体喜欢的点", group_id="200")
    )
    await store.set_status(first.expression_id, "approved")

    summary = await store.summary()
    assert summary["total"] == 2
    assert summary["pending"] == 1
    assert summary["approved"] == 1
    assert summary["group_count"] == 2
    assert summary["risk_tagged"] == 1


def test_style_expression_validation_and_normalization() -> None:
    assert normalize_style_key(" 大家 在 轻松吐槽！") == normalize_style_key("大家在轻松吐槽")
    with pytest.raises(ValueError, match="group_id"):
        NewStyleExpression(situation="x", style="y")
    with pytest.raises(ValueError, match="Invalid output_policy"):
        NewStyleExpression(situation="x", style="y", group_id="1", output_policy="copy")  # type: ignore[arg-type]
