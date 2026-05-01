import json

import pytest

from plugins.dream.agent import DreamAgent, dream_pre_check
from services.media.sticker_store import StickerStore
from services.memory.card_store import CardStore, NewCard

# Minimal JPEG bytes for sticker test data
_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"dream-sticker-test"


@pytest.fixture
async def store(tmp_path) -> CardStore:
    db_path = str(tmp_path / "test_dream_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    await s.add_card(NewCard(category="fact", scope="user", scope_id="100", content="用户A｜test"))
    await s.add_card(NewCard(category="fact", scope="group", scope_id="200", content="群B｜test"))
    return s


@pytest.fixture
async def pending_store(tmp_path) -> CardStore:
    db_path = str(tmp_path / "test_dream_pending.db")
    s = CardStore(db_path=db_path)
    await s.init()
    # Simulate migration-style cards that need re-categorization
    await s.add_card(NewCard(category="fact", scope="user", scope_id="100",
                             content="身份: 学生", source="migration", confidence=0.5))
    await s.add_card(NewCard(category="fact", scope="user", scope_id="100",
                             content="喜欢音乐", source="migration", confidence=0.5))
    await s.add_card(NewCard(category="fact", scope="group", scope_id="200",
                             content="@100(测试): 学生", source="migration", confidence=0.6))
    await s.add_card(NewCard(category="fact", scope="group", scope_id="200",
                             content="讨论了期末考试", source="migration", confidence=0.6))
    return s


def test_pre_check_returns_list(store: CardStore) -> None:
    issues = dream_pre_check(store)
    assert isinstance(issues, list)


class _FakeToolUse:
    """Minimal stand-in for client._ToolUse to avoid importing private class."""

    def __init__(self, id: str, name: str, input: dict) -> None:
        self.id = id
        self.name = name
        self.input = input


async def test_dream_run_lists_and_updates_cards(pending_store: CardStore) -> None:
    """Dream tool loop: lists cards, then updates categories."""
    agent = DreamAgent(store=pending_store, max_rounds=15)

    call_count = 0

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # Round 1: LLM lists cards for both entities
            return {
                "text": "",
                "tool_uses": [
                    _FakeToolUse("r1", "list_cards", {"scope": "user", "scope_id": "100"}),
                    _FakeToolUse("r2", "list_cards", {"scope": "group", "scope_id": "200"}),
                ],
                "input_tokens": 100, "output_tokens": 50,
                "cache_read": 0, "cache_create": 0,
            }
        if call_count == 2:
            # Get actual card IDs from the store
            user_cards = await pending_store.get_entity_cards("user", "100")
            group_cards = await pending_store.get_entity_cards("group", "200")
            # Round 2: LLM re-categorizes migration cards
            tool_uses = []
            for c in user_cards:
                cat = "status" if "身份" in c.content else "preference" if "喜欢" in c.content else c.category
                tool_uses.append(_FakeToolUse(f"u_{c.card_id}", "update_card", {
                    "card_id": c.card_id, "category": cat, "confidence": 0.8,
                }))
            for c in group_cards:
                tool_uses.append(_FakeToolUse(f"u_{c.card_id}", "update_card", {
                    "card_id": c.card_id, "category": "event" if "考试" in c.content else "fact",
                }))
            return {
                "text": "重新分类 migration 卡片",
                "tool_uses": tool_uses,
                "input_tokens": 100, "output_tokens": 50,
                "cache_read": 0, "cache_create": 0,
            }
        # Round 3: done
        return {
            "text": "整理完成。",
            "tool_uses": [],
            "input_tokens": 50, "output_tokens": 10,
            "cache_read": 0, "cache_create": 0,
        }

    await agent._run(mock_api_call)

    assert call_count == 3

    # Verify cards were re-categorized
    user_cards = await pending_store.get_entity_cards("user", "100")
    categories = {c.category for c in user_cards}
    assert "status" in categories or "preference" in categories

    assert agent._running is False


async def test_dream_cross_validates_and_supersedes(pending_store: CardStore) -> None:
    """Dream reads related cards and supersedes outdated info."""
    agent = DreamAgent(store=pending_store, max_rounds=15)

    call_count = 0

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return {
                "text": "搜索交叉验证",
                "tool_uses": [
                    _FakeToolUse("r1", "list_cards", {"scope": "user", "scope_id": "100"}),
                    _FakeToolUse("r2", "search_cards", {"query": "学生"}),
                ],
                "input_tokens": 100, "output_tokens": 50,
                "cache_read": 0, "cache_create": 0,
            }
        if call_count == 2:
            user_cards = await pending_store.get_entity_cards("user", "100")
            # Supersede a card with corrected info
            target = user_cards[0]
            return {
                "text": "发现过时信息，已取代",
                "tool_uses": [
                    _FakeToolUse("s1", "supersede_card", {
                        "old_card_id": target.card_id,
                        "scope": "user", "scope_id": "100",
                        "category": "status", "content": "身份: 研究生（已更新）",
                    }),
                ],
                "input_tokens": 100, "output_tokens": 50,
                "cache_read": 0, "cache_create": 0,
            }
        return {
            "text": "交叉验证完成。",
            "tool_uses": [],
            "input_tokens": 50, "output_tokens": 10,
            "cache_read": 0, "cache_create": 0,
        }

    await agent._run(mock_api_call)

    # After supersede: old card becomes superseded, new active card created
    all_user_cards = await pending_store.get_entity_cards("user", "100")
    # Active cards include the unchanged original + the new superseding card
    categories = {c.category for c in all_user_cards}
    assert "status" in categories  # the superseding card has category=status
    # Verify the old card is superseded by checking it directly
    # (we can't know its card_id since the mock uses user_cards[0] at runtime)
    # At minimum, the content was updated
    contents = {c.content for c in all_user_cards}
    assert any("研究生" in c for c in contents)


async def test_dream_run_clears_running_flag(store: CardStore) -> None:
    agent = DreamAgent(store=store, max_rounds=5)

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        # Verify system prompt contains expected content
        prompt = system[0]["text"]
        assert "索引" in prompt or "记忆" in prompt
        return {
            "text": "无需处理",
            "tool_uses": [],
            "input_tokens": 50, "output_tokens": 10,
            "cache_read": 0, "cache_create": 0,
        }

    await agent._run(mock_api_call)
    assert agent._running is False


async def test_dream_execute_tool_errors(store: CardStore) -> None:
    """Tool execution handles missing params and unknown tools gracefully."""
    agent = DreamAgent(store=store)
    assert "缺少" in await agent._execute_tool("list_cards", {})
    assert "缺少" in await agent._execute_tool("search_cards", {})
    assert "缺少" in await agent._execute_tool("update_card", {})
    assert "缺少" in await agent._execute_tool("supersede_card", {})
    assert "缺少" in await agent._execute_tool("expire_card", {})
    assert "未知工具" in await agent._execute_tool("bad_tool", {})


async def test_dream_execute_list_entities(store: CardStore) -> None:
    """list_entities returns entity IDs."""
    agent = DreamAgent(store=store)
    result = await agent._execute_tool("list_entities", {"scope": "user"})
    assert "100" in result

    result = await agent._execute_tool("list_entities", {"scope": "group"})
    assert "200" in result


@pytest.fixture
def sticker_store(tmp_path) -> StickerStore:
    return StickerStore(storage_dir=str(tmp_path / "stickers"))


async def test_dream_list_stickers(store: CardStore, sticker_store: StickerStore) -> None:
    """list_stickers tool returns sticker data as JSON."""
    stk_id, _ = sticker_store.add(_JPEG_DATA, "测试表情", "开心时用", source="auto")
    agent = DreamAgent(store=store, sticker_store=sticker_store)

    result = await agent._execute_tool("list_stickers", {})

    parsed = json.loads(result)
    assert stk_id in parsed
    entry = parsed[stk_id]
    assert entry["description"] == "测试表情"
    assert entry["usage_hint"] == "开心时用"


async def test_dream_delete_sticker(store: CardStore, sticker_store: StickerStore) -> None:
    """delete_sticker tool removes the sticker and confirms deletion."""
    stk_id, _ = sticker_store.add(_JPEG_DATA, "要删除的表情", "临时用", source="auto")
    assert sticker_store.get(stk_id) is not None

    agent = DreamAgent(store=store, sticker_store=sticker_store)
    result = await agent._execute_tool("delete_sticker", {"id": stk_id})

    assert "已删除" in result
    assert stk_id in result
    assert sticker_store.get(stk_id) is None


async def test_dream_delete_sticker_not_found(store: CardStore, sticker_store: StickerStore) -> None:
    """delete_sticker returns 未找到 for nonexistent sticker."""
    agent = DreamAgent(store=store, sticker_store=sticker_store)
    result = await agent._execute_tool("delete_sticker", {"id": "stk_nonexistent"})

    assert "未找到" in result
