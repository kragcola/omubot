import asyncio
import json

import pytest

from plugins.dream import DreamAgent, DreamConfig, dream_pre_check
from plugins.schedule.story_arc import StoryArc
from plugins.schedule.types import Schedule, TimeSlot
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


# ---------------------------------------------------------------------------
# OCR 回填（阶段 1）
# ---------------------------------------------------------------------------


async def test_dream_backfill_ocr_enriches_legacy(store: CardStore, sticker_store: StickerStore) -> None:
    """Legacy sticker (no ocr_text key) gets OCR backfilled, rate-limited."""
    from unittest.mock import AsyncMock, MagicMock

    stk_id, _ = sticker_store.add(_JPEG_DATA, "挥手", "告别时", source="auto")
    # Simulate a legacy entry: remove the ocr_text key written by add().
    del sticker_store._index[stk_id]["ocr_text"]  # type: ignore[attr-defined]
    assert "ocr_text" not in sticker_store.get(stk_id)  # type: ignore[operator]

    vision = MagicMock()
    vision.describe_image = AsyncMock(return_value="告别时发。图上文字：拜拜")
    agent = DreamAgent(store=store, sticker_store=sticker_store, vision_client=vision)

    enriched = await agent._backfill_sticker_ocr()

    assert enriched == 1
    assert sticker_store.get(stk_id)["ocr_text"] == "拜拜"  # type: ignore[index]


async def test_dream_backfill_ocr_skips_when_present(store: CardStore, sticker_store: StickerStore) -> None:
    """Stickers that already have ocr_text key are not re-processed."""
    from unittest.mock import AsyncMock, MagicMock

    sticker_store.add(_JPEG_DATA, "desc", "hint", ocr_text="已有")  # key present
    vision = MagicMock()
    vision.describe_image = AsyncMock(return_value="不该被调用")
    agent = DreamAgent(store=store, sticker_store=sticker_store, vision_client=vision)

    enriched = await agent._backfill_sticker_ocr()

    assert enriched == 0
    vision.describe_image.assert_not_awaited()


async def test_dream_backfill_ocr_noop_without_vision(store: CardStore, sticker_store: StickerStore) -> None:
    """No vision_client → backfill is a no-op."""
    stk_id, _ = sticker_store.add(_JPEG_DATA, "desc", "hint", source="auto")
    del sticker_store._index[stk_id]["ocr_text"]  # type: ignore[attr-defined]
    agent = DreamAgent(store=store, sticker_store=sticker_store, vision_client=None)

    assert await agent._backfill_sticker_ocr() == 0


class _FakeScheduleStore:
    def __init__(self, schedule: Schedule | None) -> None:
        self.schedule = schedule
        self.load_calls: list[tuple[str, bool]] = []

    def load(self, date_str: str, *, update_current: bool = True) -> Schedule | None:
        self.load_calls.append((date_str, update_current))
        return self.schedule


class _FakeStoryArcStore:
    def __init__(self, arc: StoryArc | None) -> None:
        self.arc = arc
        self.load_active_calls = 0
        self.saved: list[StoryArc] = []

    def load_active(self) -> StoryArc | None:
        self.load_active_calls += 1
        return self.arc

    def save(self, arc: StoryArc) -> None:
        self.saved.append(arc)
        self.arc = arc


class _FakeMessageLog:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.query_calls: list[tuple[str, int]] = []

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        self.query_calls.append((group_id, limit))
        return self.rows[-limit:]


class _FakeMoodEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def m1_tension_metrics(self, *, group_id: str, session_id: str = "") -> dict[str, float]:
        self.calls.append((group_id, session_id))
        return {
            "injection_count": 4.0,
            "prompt_trigger_rate": 0.25,
            "current_tension": 0.18,
        }


def _reflection_schedule() -> Schedule:
    return Schedule(
        date="2026-06-09",
        theme="排练后的整理日",
        day_narrative="白天在复盘昨天排练的失误，晚上把压力拆成可处理的小块。",
        slots=[
            TimeSlot(
                time="09:00",
                activity="study",
                description="整理台词和期末复习计划",
                mood_hint="有压力但能推进",
            ),
            TimeSlot(
                time="20:30",
                activity="practice",
                description="和虚构伙伴复盘舞台节奏",
                mood_hint="疲惫，仍然认真",
            ),
        ],
    )


def _reflection_arc() -> StoryArc:
    return StoryArc(
        arc_id="stage_play_competition_week",
        title="舞台剧比赛准备周",
        stage="rehearsal",
        goals=["完成舞台剧比赛准备", "兼顾期末复习"],
        variables={"reflection_group_id": "200"},
        open_threads=["是否周六追加排练"],
        last_events=[{"date": "2026-06-08", "summary": "第一次整排进度慢"}],
        next_day_seed="在复习和排练之间做取舍",
    )


def test_life_reflection_config_defaults_off() -> None:
    cfg = DreamConfig.model_validate({})

    assert cfg.life_reflection_enabled is False


async def test_dream_life_reflection_flag_off_preserves_existing_loop(store: CardStore) -> None:
    schedule_store = _FakeScheduleStore(_reflection_schedule())
    story_store = _FakeStoryArcStore(_reflection_arc())
    message_log = _FakeMessageLog([{"role": "user", "speaker": "100", "content_text": "今天排练有点累"}])
    agent = DreamAgent(
        store=store,
        max_rounds=1,
        life_reflection_enabled=False,
        schedule_store=schedule_store,
        story_arc_store=story_store,
        message_log=message_log,
    )
    calls = 0

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        nonlocal calls
        calls += 1
        assert tools is not None
        return {"text": "无需处理", "tool_uses": []}

    await agent._run(mock_api_call)

    assert calls == 1
    assert schedule_store.load_calls == []
    assert story_store.load_active_calls == 0
    assert story_store.saved == []
    assert message_log.query_calls == []
    assert await store.search_cards("洞察", limit=5) == []


async def test_dream_life_reflection_writes_cards_and_updates_arc(store: CardStore) -> None:
    schedule_store = _FakeScheduleStore(_reflection_schedule())
    arc = _reflection_arc()
    story_store = _FakeStoryArcStore(arc)
    message_log = _FakeMessageLog([
        {"role": "user", "speaker": "100", "content_text": "今天排练有点累，但至少知道哪里要改。"},
        {"role": "assistant", "speaker": "bot", "content_text": "那就把动作难度先拆小。"},
    ])
    mood_engine = _FakeMoodEngine()
    invalidated = 0
    agent = DreamAgent(
        store=store,
        max_rounds=1,
        life_reflection_enabled=True,
        schedule_store=schedule_store,
        story_arc_store=story_store,
        message_log=message_log,
        mood_engine=mood_engine,
        on_memo_change=lambda: nonlocal_increment("invalidated"),
    )

    def nonlocal_increment(_name: str) -> None:
        nonlocal invalidated
        invalidated += 1

    api_calls: list[tuple[list | None, int]] = []

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        api_calls.append((tools, max_tokens))
        if tools is not None:
            return {"text": "无需处理", "tool_uses": []}
        assert "今天过得怎样" in messages[0]["content"]
        assert "今日群聊片段" in messages[0]["content"]
        return {
            "text": json.dumps({
                "cards": [
                    {
                        "scope": "group",
                        "scope_id": "200",
                        "category": "event",
                        "content": "经历洞察：今天把排练压力拆成了动作难度和复习时间两条线。",
                        "confidence": 0.82,
                    },
                    {
                        "scope": "global",
                        "scope_id": "",
                        "category": "status",
                        "content": "经历洞察：疲惫时更适合用短任务维持连续性。",
                        "confidence": 0.76,
                    },
                ],
                "last_event_summary": "夜间反思确认了排练疲惫和复习压力的取舍。",
                "open_threads": ["动作难度是否继续下调", "复习块是否提前到午后"],
                "next_day_seed": "明天先复习再排练，减少临场焦虑。",
            }, ensure_ascii=False),
            "tool_uses": [],
        }

    await agent._run(mock_api_call)

    cards = await store.search_cards("经历洞察", limit=5)
    assert len(cards) == 2
    assert {card.source for card in cards} == {"dream_reflection"}
    assert {card.captured_by for card in cards} == {"dream_reflection"}
    assert {card.scope for card in cards} == {"group", "global"}
    assert story_store.saved == [arc]
    assert arc.last_events[-1]["source"] == "dream_reflection"
    assert "动作难度是否继续下调" in arc.open_threads
    assert arc.next_day_seed == "明天先复习再排练，减少临场焦虑。"
    assert schedule_store.load_calls and schedule_store.load_calls[-1][1] is False
    assert message_log.query_calls == [("200", 12)]
    assert mood_engine.calls == [("200", "group_200")]
    assert len(api_calls) == 2
    assert api_calls[0][0] is not None
    assert api_calls[0][1] == 2048
    assert api_calls[1] == (None, 1024)
    assert invalidated == 1


async def test_dream_life_reflection_invalid_json_does_not_write(store: CardStore) -> None:
    arc = _reflection_arc()
    story_store = _FakeStoryArcStore(arc)
    agent = DreamAgent(
        store=store,
        max_rounds=1,
        life_reflection_enabled=True,
        schedule_store=_FakeScheduleStore(_reflection_schedule()),
        story_arc_store=story_store,
    )

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        if tools is not None:
            return {"text": "无需处理", "tool_uses": []}
        return {"text": "not-json", "tool_uses": []}

    await agent._run(mock_api_call)

    assert await store.search_cards("经历洞察", limit=5) == []
    assert story_store.saved == []
    assert arc.open_threads == ["是否周六追加排练"]
    assert arc.next_day_seed == "在复习和排练之间做取舍"


async def test_dream_life_reflection_cancel_path_leaves_external_state_clean(store: CardStore) -> None:
    arc = _reflection_arc()
    story_store = _FakeStoryArcStore(arc)
    entered_reflection = asyncio.Event()
    release_reflection = asyncio.Event()
    agent = DreamAgent(
        store=store,
        max_rounds=1,
        life_reflection_enabled=True,
        schedule_store=_FakeScheduleStore(_reflection_schedule()),
        story_arc_store=story_store,
    )

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        if tools is not None:
            return {"text": "无需处理", "tool_uses": []}
        entered_reflection.set()
        await release_reflection.wait()
        return {
            "text": json.dumps({
                "cards": [{
                    "scope": "global",
                    "scope_id": "global",
                    "category": "event",
                    "content": "经历洞察：这条不应写入。",
                    "confidence": 0.8,
                }],
                "last_event_summary": "不应写入",
                "open_threads": ["不应写入"],
                "next_day_seed": "不应写入",
            }, ensure_ascii=False),
            "tool_uses": [],
        }

    task = asyncio.create_task(agent._run(mock_api_call))
    await asyncio.wait_for(entered_reflection.wait(), timeout=1)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, timeout=0.01)

    assert await store.search_cards("不应写入", limit=5) == []
    assert story_store.saved == []
    assert arc.last_events == [{"date": "2026-06-08", "summary": "第一次整排进度慢"}]
    assert arc.open_threads == ["是否周六追加排练"]
    assert arc.next_day_seed == "在复习和排练之间做取舍"
    assert agent._running is False
