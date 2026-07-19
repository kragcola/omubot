"""Tests for CardLookupTool and CardUpdateTool."""

from collections.abc import AsyncIterator

import pytest

from services.memory.card_store import CardStore, NewCard
from services.tools.context import ToolContext
from services.tools.memo_tools import CardLookupTool, CardUpdateTool


@pytest.fixture
async def store_with_data(tmp_path) -> AsyncIterator[CardStore]:
    db_path = str(tmp_path / "test_tool_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    await s.add_card(NewCard(category="fact", scope="user", scope_id="123456", content="喜欢 Go"))
    await s.add_card(NewCard(category="preference", scope="user", scope_id="123456", content="偏好被称呼为明哥"))
    await s.add_card(NewCard(category="fact", scope="user", scope_id="789012", content="和 @123456 互怼"))
    await s.add_card(NewCard(category="fact", scope="group", scope_id="987654", content="@123456 活跃"))
    try:
        yield s
    finally:
        await s.close()


async def test_lookup_by_scope(store_with_data: CardStore) -> None:
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, scope="user", scope_id="123456")
    assert "喜欢 Go" in result
    assert "明哥" in result


async def test_lookup_by_explicit_scope_rejects_other_user_and_group(
    store_with_data: CardStore,
) -> None:
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456", group_id="987654")

    other_user = await tool.execute(ctx, scope="user", scope_id="789012")
    other_group = await tool.execute(ctx, scope="group", scope_id="111111")

    assert "和 @123456 互怼" not in other_user
    assert "无权" in other_user
    assert "无权" in other_group


async def test_lookup_by_query(store_with_data: CardStore) -> None:
    """Caller 123456 can query a card they own (fixture: 喜欢 Go)."""
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, query="喜欢 Go")
    assert "喜欢 Go" in result
    assert "未找到" not in result


async def test_lookup_by_query_excludes_other_users_cards(
    store_with_data: CardStore,
) -> None:
    """Query mode must not return cards owned by a different user."""
    unique_token = "FOREIGN_ONLY_TOKEN_a9f3c2e1b7d4"
    foreign_content = f"private foreign memo {unique_token}"
    await store_with_data.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="789012",
            content=foreign_content,
        )
    )
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, query=unique_token)
    # Tool may echo the query string; foreign card body must never appear.
    assert foreign_content not in result
    # Ownership token must not appear in returned card lines (exclude query echo).
    body_lines = result.splitlines()[1:]
    body = "\n".join(body_lines)
    assert unique_token not in body
    assert foreign_content not in body


async def test_lookup_by_query_includes_current_group_and_global_but_not_other_group(
    store_with_data: CardStore,
) -> None:
    token = "SCOPED_QUERY_TOKEN_74ce19"
    current_group = f"current group {token}"
    other_group = f"other group {token}"
    global_content = f"global fact {token}"
    await store_with_data.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="987654",
            content=current_group,
        )
    )
    await store_with_data.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="111111",
            content=other_group,
        )
    )
    await store_with_data.add_card(
        NewCard(
            category="fact",
            scope="global",
            scope_id="global",
            content=global_content,
        )
    )
    tool = CardLookupTool(store_with_data)

    result = await tool.execute(
        ToolContext(user_id="123456", group_id="987654"),
        query=token,
    )

    assert current_group in result
    assert global_content in result
    assert other_group not in result


async def test_lookup_by_query_rejects_noncanonical_global_scope_id(
    store_with_data: CardStore,
) -> None:
    token = "NONCANONICAL_GLOBAL_TOKEN_ba0193"
    foreign_global = f"foreign global namespace {token}"
    await store_with_data.add_card(
        NewCard(
            category="fact",
            scope="global",
            scope_id="legacy-other-global",
            content=foreign_global,
        )
    )

    result = await CardLookupTool(store_with_data).execute(
        ToolContext(user_id="123456", group_id="987654"),
        query=token,
    )

    assert foreign_global not in result


async def test_lookup_by_scope_and_category(store_with_data: CardStore) -> None:
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, scope="user", scope_id="123456", category="preference")
    assert "明哥" in result
    assert "preference" in result.lower() or "偏好" in result


async def test_lookup_not_found(store_with_data: CardStore) -> None:
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="999999")
    result = await tool.execute(ctx, scope="user", scope_id="999999")
    assert "暂无记录" in result


async def test_lookup_no_params(store_with_data: CardStore) -> None:
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx)
    assert "请提供" in result


async def test_update_add_card(store_with_data: CardStore) -> None:
    tool = CardUpdateTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, action="add", scope="user", scope_id="123456",
                                category="fact", content="新测试内容")
    assert "已添加" in result
    cards = await store_with_data.get_entity_cards("user", "123456")
    contents = {c.content for c in cards}
    assert "新测试内容" in contents


async def test_update_tool_rejects_cross_scope_and_foreign_card_mutations(
    store_with_data: CardStore,
) -> None:
    foreign = (
        await store_with_data.get_entity_cards("user", "789012")
    )[0]
    tool = CardUpdateTool(store_with_data)
    ctx = ToolContext(user_id="123456", group_id="987654")

    add_other_user = await tool.execute(
        ctx,
        action="add",
        scope="user",
        scope_id="789012",
        category="fact",
        content="poison user",
    )
    add_other_group = await tool.execute(
        ctx,
        action="add",
        scope="group",
        scope_id="111111",
        category="fact",
        content="poison group",
    )
    add_global = await tool.execute(
        ctx,
        action="add",
        scope="global",
        scope_id="global",
        category="fact",
        content="poison global",
    )
    update_foreign = await tool.execute(
        ctx,
        action="update",
        card_id=foreign.card_id,
        content="mutated foreign",
    )
    expire_foreign = await tool.execute(
        ctx,
        action="expire",
        card_id=foreign.card_id,
    )
    supersede_foreign = await tool.execute(
        ctx,
        action="supersede",
        card_id=foreign.card_id,
        scope="user",
        scope_id="789012",
        category="fact",
        content="superseded foreign",
    )

    assert "无权" in add_other_user
    assert "无权" in add_other_group
    assert "无权" in add_global
    assert "无权" in update_foreign
    assert "无权" in expire_foreign
    assert "无权" in supersede_foreign
    unchanged = await store_with_data.get_card(foreign.card_id)
    assert unchanged is not None
    assert unchanged.content == foreign.content
    assert unchanged.status == "active"


async def test_update_card_content(store_with_data: CardStore) -> None:
    cards = await store_with_data.get_entity_cards("user", "123456")
    cid = cards[0].card_id
    tool = CardUpdateTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, action="update", card_id=cid, content="修改后的内容")
    assert "已更新" in result
    card = await store_with_data.get_card(cid)
    assert card is not None
    assert card.content == "修改后的内容"


async def test_update_supersede_card(store_with_data: CardStore) -> None:
    cards = await store_with_data.get_entity_cards("user", "123456")
    cid = cards[0].card_id
    tool = CardUpdateTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, action="supersede", card_id=cid,
                                scope="user", scope_id="123456",
                                category="fact", content="取代后的内容")
    assert "已取代" in result
    old = await store_with_data.get_card(cid)
    assert old is not None
    assert old.status == "superseded"


async def test_update_expire_card(store_with_data: CardStore) -> None:
    cards = await store_with_data.get_entity_cards("user", "123456")
    cid = cards[0].card_id
    tool = CardUpdateTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, action="expire", card_id=cid)
    assert "已过期" in result
    card = await store_with_data.get_card(cid)
    assert card is not None
    assert card.status == "expired"


async def test_lookup_tool_schema() -> None:
    s = CardStore(db_path="/tmp/unused_test.db")
    tool = CardLookupTool(s)
    schema = tool.parameters
    assert "scope" in schema["properties"]
    assert "scope_id" in schema["properties"]
    assert "query" in schema["properties"]


async def test_update_tool_schema() -> None:
    s = CardStore(db_path="/tmp/unused_test.db")
    tool = CardUpdateTool(s)
    schema = tool.parameters
    assert "action" in schema["properties"]
    assert schema["required"] == ["action"]


async def test_tool_descriptions_match_enforced_scope_boundaries() -> None:
    s = CardStore(db_path="/tmp/unused_test.db")
    lookup = CardLookupTool(s)
    update = CardUpdateTool(s)

    assert "当前用户" in lookup.description
    assert "当前群" in lookup.description
    assert update.parameters["properties"]["scope"]["enum"] == ["user", "group"]
    assert "当前用户" in update.description
    assert "当前群" in update.description
