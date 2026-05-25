import pytest

from services.style import NewStyleExpression, StyleStore


@pytest.fixture
async def store(tmp_path) -> StyleStore:
    s = StyleStore(tmp_path / "style.db")
    await s.init()
    yield s
    await s.close()


async def _approved_expression(
    store: StyleStore,
    *,
    situation: str = "大家在轻松吐槽",
    style: str,
    mood_fit: float = 0.5,
    persona_fit: float = 0.5,
    confidence: float = 0.7,
) -> str:
    expression = await store.upsert_expression(
        NewStyleExpression(
            situation=situation,
            style=style,
            group_id="100",
            confidence=confidence,
            mood_fit=mood_fit,
            persona_fit=persona_fit,
            output_policy="allow_use",
        )
    )
    await store.set_status(expression.expression_id, "approved")
    return expression.expression_id


@pytest.mark.asyncio
async def test_style_relevance_mood_fit_changes_order(store: StyleStore) -> None:
    low_mood = await _approved_expression(
        store,
        style="低能量时先轻轻接住，再短句回应",
        mood_fit=0.1,
        confidence=0.9,
    )
    high_mood = await _approved_expression(
        store,
        style="高能量时先明亮接梗，再快速补一句",
        mood_fit=0.9,
        confidence=0.7,
    )

    expressions = await store.get_prompt_expressions(
        group_id="100",
        conversation_text="刚才大家在轻松吐槽",
        max_items=2,
        mood_fit_target=0.9,
    )

    assert [item.expression_id for item in expressions] == [high_mood, low_mood]


@pytest.mark.asyncio
async def test_style_relevance_persona_fit_changes_order(store: StyleStore) -> None:
    generic = await _approved_expression(
        store,
        style="泛用地先附和再回应",
        persona_fit=0.2,
        confidence=0.9,
    )
    persona_fit = await _approved_expression(
        store,
        style="更贴近凤笑梦地先压低力度再接话",
        persona_fit=0.95,
        confidence=0.8,
    )

    expressions = await store.get_prompt_expressions(
        group_id="100",
        conversation_text="刚才大家在轻松吐槽",
        max_items=2,
        persona_fit_target=1.0,
    )

    assert [item.expression_id for item in expressions] == [persona_fit, generic]


@pytest.mark.asyncio
async def test_style_relevance_default_keeps_legacy_ranking(store: StyleStore) -> None:
    higher_confidence = await _approved_expression(
        store,
        style="高置信但 fit 普通的接话方式",
        mood_fit=0.1,
        persona_fit=0.1,
        confidence=0.95,
    )
    lower_confidence = await _approved_expression(
        store,
        style="低置信但 fit 很高的接话方式",
        mood_fit=1.0,
        persona_fit=1.0,
        confidence=0.7,
    )

    expressions = await store.get_prompt_expressions(
        group_id="100",
        conversation_text="刚才大家在轻松吐槽",
        max_items=2,
    )

    assert [item.expression_id for item in expressions] == [higher_confidence, lower_confidence]


@pytest.mark.asyncio
async def test_style_relevance_fit_does_not_recall_unrelated_expression(store: StyleStore) -> None:
    await _approved_expression(
        store,
        situation="有人分享晚饭",
        style="轻声回应饭菜口感和香气",
        mood_fit=1.0,
        persona_fit=1.0,
        confidence=0.99,
    )

    block = await store.build_prompt_block(
        group_id="100",
        conversation_text="刚才大家在轻松吐槽",
        mood_fit_target=1.0,
        persona_fit_target=1.0,
    )

    assert block == ""
