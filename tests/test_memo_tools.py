"""Tests for CardLookupTool and CardUpdateTool."""

import pytest

from services.memory.card_store import CardStore, NewCard
from services.tools.context import ToolContext
from services.tools.memo_tools import CardLookupTool, CardUpdateTool


@pytest.fixture
async def store_with_data(tmp_path) -> CardStore:
    db_path = str(tmp_path / "test_tool_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    await s.add_card(NewCard(category="fact", scope="user", scope_id="123456", content="喜欢 Go"))
    await s.add_card(NewCard(category="preference", scope="user", scope_id="123456", content="偏好被称呼为明哥"))
    await s.add_card(NewCard(category="fact", scope="user", scope_id="789012", content="和 @123456 互怼"))
    await s.add_card(NewCard(category="fact", scope="group", scope_id="987654", content="@123456 活跃"))
    return s


async def test_lookup_by_scope(store_with_data: CardStore) -> None:
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, scope="user", scope_id="123456")
    assert "喜欢 Go" in result
    assert "明哥" in result


async def test_lookup_by_query(store_with_data: CardStore) -> None:
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, query="互怼")
    assert "789012" in result or "互怼" in result


async def test_lookup_by_scope_and_category(store_with_data: CardStore) -> None:
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, scope="user", scope_id="123456", category="preference")
    assert "明哥" in result
    assert "preference" in result.lower() or "偏好" in result


async def test_lookup_not_found(store_with_data: CardStore) -> None:
    tool = CardLookupTool(store_with_data)
    ctx = ToolContext(user_id="123456")
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


async def test_update_card_content(store_with_data: CardStore) -> None:
    cards = await store_with_data.get_entity_cards("user", "123456")
    cid = cards[0].card_id
    tool = CardUpdateTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, action="update", card_id=cid, content="修改后的内容")
    assert "已更新" in result
    card = await store_with_data.get_card(cid)
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
    assert old.status == "superseded"


async def test_update_expire_card(store_with_data: CardStore) -> None:
    cards = await store_with_data.get_entity_cards("user", "123456")
    cid = cards[0].card_id
    tool = CardUpdateTool(store_with_data)
    ctx = ToolContext(user_id="123456")
    result = await tool.execute(ctx, action="expire", card_id=cid)
    assert "已过期" in result
    card = await store_with_data.get_card(cid)
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
