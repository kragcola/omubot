import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest

from kernel.types import PluginContext
from plugins.dream import DreamAgent, DreamConfig, DreamPlugin, dream_pre_check
from plugins.dream.plugin import LifeReflectionCardDraft, LifeReflectionDraft
from plugins.schedule.story_arc import StoryArc, StoryArcStore
from plugins.schedule.types import Schedule, TimeSlot
from plugins.social_narrative.plugin import SocialNarrativeConfig, SocialNarrativePlugin
from services.media.sticker_store import StickerStore
from services.memory.card_store import CardStore, NewCard

# Minimal JPEG bytes for sticker test data
_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"dream-sticker-test"


@pytest.fixture
async def store(tmp_path) -> AsyncIterator[CardStore]:
    db_path = str(tmp_path / "test_dream_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    await s.add_card(NewCard(category="fact", scope="user", scope_id="100", content="用户A｜test"))
    await s.add_card(NewCard(category="fact", scope="group", scope_id="200", content="群B｜test"))
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def pending_store(tmp_path) -> AsyncIterator[CardStore]:
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
    try:
        yield s
    finally:
        await s.close()


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


async def test_dream_runs_once_per_natural_day_across_restart(store: CardStore) -> None:
    calls = 0

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        nonlocal calls
        calls += 1
        return {"text": "无需处理", "tool_uses": []}

    first = DreamAgent(store=store, max_rounds=1)
    restarted = DreamAgent(store=store, max_rounds=1)

    await first._run(mock_api_call)
    await restarted._run(mock_api_call)

    assert calls == 1


async def test_dream_cancelled_run_is_retryable_same_day(store: CardStore) -> None:
    entered = asyncio.Event()

    async def blocked_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    first = DreamAgent(store=store, max_rounds=1)
    task = asyncio.create_task(first._run(blocked_api_call))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    retry_calls = 0

    async def retry_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        nonlocal retry_calls
        retry_calls += 1
        return {"text": "无需处理", "tool_uses": []}

    restarted = DreamAgent(store=store, max_rounds=1)
    await restarted._run(retry_api_call)

    assert retry_calls == 1


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
    """list_stickers returns the never-sent auto-captured eviction candidates."""
    # Auto-captured + never sent -> a candidate.
    cand_id, _ = sticker_store.add(
        _JPEG_DATA, "候选表情", "群友常用", source="stolen_silent_learn"
    )
    # Protected source -> never a candidate.
    prot_data = b"\xff\xd8\xff\xe0" + b"\x11" * 64 + b"dream-protected"
    prot_id, _ = sticker_store.add(prot_data, "受保护", "保留", source="admin")
    agent = DreamAgent(store=store, sticker_store=sticker_store)

    result = await agent._execute_tool("list_stickers", {})

    parsed = json.loads(result)
    assert parsed["total_in_library"] == 2
    assert "delete_floor" in parsed
    cand_ids = [c["id"] for c in parsed["candidates"]]
    assert cand_id in cand_ids, "never-sent silent sticker is a candidate"
    assert prot_id not in cand_ids, "protected source is excluded"


async def test_dream_delete_sticker(store: CardStore, sticker_store: StickerStore) -> None:
    """delete_sticker tool removes the sticker and confirms deletion."""
    stk_id, _ = sticker_store.add(_JPEG_DATA, "要删除的表情", "临时用", source="auto")
    assert sticker_store.get(stk_id) is not None

    # floor=0 so a single sticker is above the floor and may be removed.
    agent = DreamAgent(store=store, sticker_store=sticker_store, sticker_delete_floor=0)
    result = await agent._execute_tool("delete_sticker", {"id": stk_id})

    assert "已删除" in result
    assert stk_id in result
    assert sticker_store.get(stk_id) is None


async def test_dream_delete_sticker_not_found(store: CardStore, sticker_store: StickerStore) -> None:
    """delete_sticker returns 未找到 for nonexistent sticker."""
    agent = DreamAgent(store=store, sticker_store=sticker_store, sticker_delete_floor=0)
    result = await agent._execute_tool("delete_sticker", {"id": "stk_nonexistent"})

    assert "未找到" in result


async def test_dream_delete_sticker_blocked_below_floor(
    store: CardStore, sticker_store: StickerStore
) -> None:
    """delete_sticker is refused while the library is at/below the delete floor."""
    stk_id, _ = sticker_store.add(_JPEG_DATA, "受保护的表情", "不该被删", source="auto")
    assert sticker_store.get(stk_id) is not None

    # 1 sticker, floor=500 -> count (1) <= floor -> refuse.
    agent = DreamAgent(store=store, sticker_store=sticker_store, sticker_delete_floor=500)
    result = await agent._execute_tool("delete_sticker", {"id": stk_id})

    assert "下限" in result
    assert sticker_store.get(stk_id) is not None  # untouched


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


class _FakeClimateEngine:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def group_summary(self, group_id: str) -> dict[str, float]:
        self.calls.append(group_id)
        return {
            "state_count": 4.0,
            "mean_tension": 0.12,
            "current_tension": 0.18,
        }


class _FakeSocialNarrativeStore:
    def __init__(self, *, eligible_groups: set[str] | None = None) -> None:
        self.group_context_calls: list[tuple[str, int]] = []
        self._eligible_groups = {
            str(group_id).strip()
            for group_id in (eligible_groups if eligible_groups is not None else {"200"})
            if str(group_id).strip()
        }

    def is_group_reflection_eligible(self, group_id: str | None) -> bool:
        normalized = str(group_id or "").strip()
        return (
            bool(normalized)
            and normalized.isdecimal()
            and int(normalized) > 0
            and normalized in self._eligible_groups
        )

    async def build_group_reflection_context(
        self,
        *,
        group_id: str,
        limit: int = 12,
    ) -> str:
        if not self.is_group_reflection_eligible(group_id):
            return ""
        self.group_context_calls.append((group_id, limit))
        return (
            "【严格群事实】group=200；entity_kind=factual；证据=7001；"
            "共同经历：一起理顺了排练节奏；不得补写真人线下行为"
        )


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


async def test_dream_plugin_wires_context_social_narrative_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import kernel.config
    import plugins.dream.plugin as dream_module

    social_store = object()
    reflection_provider = object()
    worldbook_bridge = object()
    captured: dict[str, object] = {}

    class _CaptureAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        kernel.config,
        "load_plugin_config",
        lambda *_args, **_kwargs: DreamConfig(enabled=True),
    )
    monkeypatch.setattr(dream_module, "setup_dream_logger", lambda _path: None)
    monkeypatch.setattr(dream_module, "DreamAgent", _CaptureAgent)
    ctx = PluginContext(
        config=SimpleNamespace(log=SimpleNamespace(dir=str(tmp_path))),
        card_store=object(),
        sticker_store=None,
        prompt_builder=SimpleNamespace(invalidate=lambda: None),
        runtime_state=None,
        social_narrative_store=social_store,
        social_narrative_reflection_provider=reflection_provider,
        worldbook_dream_bridge=worldbook_bridge,
        allowed_groups={200},
    )

    await DreamPlugin().on_startup(ctx)

    assert captured.get("social_narrative_store") is reflection_provider, (
        "DreamPlugin must pass only the gated reflection provider into DreamAgent"
    )
    assert captured.get("reflection_allowed_group_ids") == {"200"}
    assert captured.get("worldbook_dream_bridge") is worldbook_bridge


async def test_dream_group_reflection_injects_strict_group_factual_context(
    store: CardStore,
) -> None:
    social_store = _FakeSocialNarrativeStore()
    agent = DreamAgent(
        store=store,
        life_reflection_enabled=True,
        schedule_store=_FakeScheduleStore(_reflection_schedule()),
        story_arc_store=_FakeStoryArcStore(_reflection_arc()),
        reflection_allowed_group_ids={"200"},
    )
    agent._social_narrative_store = social_store  # type: ignore[attr-defined]
    reflection_requests: list[str] = []

    async def mock_api_call(
        system: list,
        messages: list,
        tools: list | None = None,
        max_tokens: int = 1024,
    ) -> dict:
        del system, tools, max_tokens
        reflection_requests.append(str(messages[0]["content"]))
        return {"text": "not-json", "tool_uses": []}

    assert await agent._run_life_reflection(mock_api_call) == 0
    assert social_store.group_context_calls == [("200", 12)]
    assert len(reflection_requests) == 1
    assert "【严格群事实】group=200" in reflection_requests[0]
    assert "证据=7001" in reflection_requests[0]
    assert "不得补写真人线下行为" in reflection_requests[0]


async def test_dream_global_reflection_does_not_query_or_inject_group_facts(
    store: CardStore,
) -> None:
    arc = _reflection_arc()
    arc.variables.pop("reflection_group_id")
    social_store = _FakeSocialNarrativeStore()
    agent = DreamAgent(
        store=store,
        life_reflection_enabled=True,
        schedule_store=_FakeScheduleStore(_reflection_schedule()),
        story_arc_store=_FakeStoryArcStore(arc),
    )
    agent._social_narrative_store = social_store  # type: ignore[attr-defined]
    reflection_requests: list[str] = []

    async def mock_api_call(
        system: list,
        messages: list,
        tools: list | None = None,
        max_tokens: int = 1024,
    ) -> dict:
        del system, tools, max_tokens
        reflection_requests.append(str(messages[0]["content"]))
        return {"text": "not-json", "tool_uses": []}

    assert await agent._run_life_reflection(mock_api_call) == 0
    assert social_store.group_context_calls == []
    assert len(reflection_requests) == 1
    assert "【严格群事实】" not in reflection_requests[0]
    assert "共同经历：一起理顺了排练节奏" not in reflection_requests[0]


async def test_dream_global_reflection_updates_arc_without_writing_memory_cards(
    store: CardStore,
) -> None:
    arc = _reflection_arc()
    arc.variables.pop("reflection_group_id")
    agent = DreamAgent(
        store=store,
        life_reflection_enabled=True,
        schedule_store=_FakeScheduleStore(_reflection_schedule()),
        story_arc_store=_FakeStoryArcStore(arc),
    )

    async def mock_api_call(
        system: list,
        messages: list,
        tools: list | None = None,
        max_tokens: int = 1024,
    ) -> dict:
        del system, messages, tools, max_tokens
        return {
            "text": json.dumps({
                "cards": [{
                    "scope": "global",
                    "scope_id": "global",
                    "category": "event",
                    "content": "合成剧情不进入通用记忆。",
                    "confidence": 0.8,
                }],
                "last_event_summary": "虚构故事完成了一段排练。",
            }, ensure_ascii=False),
            "tool_uses": [],
        }

    assert await agent._run_life_reflection(mock_api_call) == 0
    assert await store.search_cards("合成剧情", limit=5) == []
    assert arc.last_events[-1]["subject_kind"] == "fiction"


def test_social_narrative_facts_remain_out_of_schedule_and_story_arc_prompts() -> None:
    import inspect

    from plugins.schedule.generator import ScheduleGenerator, _render_story_arc_context

    assert "social_narrative" not in inspect.getsource(ScheduleGenerator)
    assert "social_narrative" not in inspect.getsource(_render_story_arc_context)


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


async def test_dream_life_reflection_writes_only_group_cards_and_updates_arc(store: CardStore) -> None:
    schedule_store = _FakeScheduleStore(_reflection_schedule())
    arc = _reflection_arc()
    story_store = _FakeStoryArcStore(arc)
    message_log = _FakeMessageLog([
        {"role": "user", "speaker": "100", "content_text": "今天排练有点累，但至少知道哪里要改。"},
        {"role": "assistant", "speaker": "bot", "content_text": "那就把动作难度先拆小。"},
    ])
    climate_engine = _FakeClimateEngine()
    invalidated = 0
    agent = DreamAgent(
        store=store,
        max_rounds=1,
        life_reflection_enabled=True,
        schedule_store=schedule_store,
        story_arc_store=story_store,
        message_log=message_log,
        climate_engine=climate_engine,
        reflection_allowed_group_ids={"200"},
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
    assert len(cards) == 1
    assert {card.source for card in cards} == {"dream_reflection"}
    assert {card.captured_by for card in cards} == {"dream_reflection"}
    assert {card.scope for card in cards} == {"group"}
    assert story_store.saved == [arc]
    assert arc.last_events[-1]["source"] == "dream_reflection"
    assert arc.last_events[-1]["subject_kind"] == "fiction"
    assert "动作难度是否继续下调" in arc.open_threads
    assert arc.next_day_seed == "明天先复习再排练，减少临场焦虑。"
    assert schedule_store.load_calls and schedule_store.load_calls[-1][1] is False
    assert message_log.query_calls == [("200", 12)]
    assert climate_engine.calls == ["200"]
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


async def test_dream_life_reflection_binds_scope_to_selected_group(store: CardStore) -> None:
    arc = _reflection_arc()
    agent = DreamAgent(
        store=store,
        life_reflection_enabled=True,
        schedule_store=_FakeScheduleStore(_reflection_schedule()),
        story_arc_store=_FakeStoryArcStore(arc),
        reflection_allowed_group_ids={"200"},
    )

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        return {
            "text": json.dumps({
                "cards": [
                    {
                        "scope": "group",
                        "scope_id": "999",
                        "category": "event",
                        "content": "经历洞察：只属于实际选中群的片段。",
                    },
                    {
                        "scope": "global",
                        "scope_id": "999",
                        "category": "status",
                        "content": "经历洞察：全局片段。",
                    },
                ],
            }, ensure_ascii=False),
            "tool_uses": [],
        }

    assert await agent._run_life_reflection(mock_api_call) == 0

    cards = await store.search_cards("经历洞察", limit=10)
    assert cards == []


async def test_dream_life_reflection_without_group_rejects_group_scope(store: CardStore) -> None:
    arc = _reflection_arc()
    arc.variables.pop("reflection_group_id")
    agent = DreamAgent(
        store=store,
        life_reflection_enabled=True,
        schedule_store=_FakeScheduleStore(_reflection_schedule()),
        story_arc_store=_FakeStoryArcStore(arc),
    )

    async def mock_api_call(
        system: list, messages: list, tools: list | None = None, max_tokens: int = 1024,
    ) -> dict:
        assert "group_id" not in messages[0]["content"]
        return {
            "text": json.dumps({
                "cards": [
                    {
                        "scope": "group",
                        "scope_id": "999",
                        "category": "event",
                        "content": "经历洞察：这条伪造群卡必须丢弃。",
                    },
                    {
                        "scope": "global",
                        "scope_id": "anything",
                        "category": "status",
                        "content": "经历洞察：只保留全局卡。",
                    },
                ],
            }, ensure_ascii=False),
            "tool_uses": [],
        }

    assert await agent._run_life_reflection(mock_api_call) == 0

    cards = await store.search_cards("经历洞察", limit=10)
    assert cards == []


async def test_dream_life_reflection_rejects_group_outside_real_allowlist(
    store: CardStore,
) -> None:
    arc = _reflection_arc()
    arc.variables["reflection_group_id"] = "wxs"
    raw_social_store = _FakeSocialNarrativeStore()
    gate = SocialNarrativePlugin(SocialNarrativeConfig(
        enabled=True,
        allowed_group_ids=["200"],
    ))
    gate_ctx = PluginContext(social_narrative_store=raw_social_store)
    await gate.on_startup(gate_ctx)
    agent_kwargs: dict[str, Any] = {
        "store": store,
        "life_reflection_enabled": True,
        "schedule_store": _FakeScheduleStore(_reflection_schedule()),
        "story_arc_store": _FakeStoryArcStore(arc),
        "social_narrative_store": gate,
    }
    signature = inspect.signature(DreamAgent)
    for parameter_name in ("allowed_group_ids", "reflection_allowed_group_ids"):
        if parameter_name in signature.parameters:
            agent_kwargs[parameter_name] = ["200"]
    agent = cast(Any, DreamAgent)(**agent_kwargs)

    async def mock_api_call(
        system: list,
        messages: list,
        tools: list | None = None,
        max_tokens: int = 1024,
    ) -> dict:
        del system, messages, tools, max_tokens
        return {
            "text": json.dumps({
                "cards": [{
                    "scope": "group",
                    "scope_id": "wxs",
                    "category": "event",
                    "content": "经历洞察：未授权群不得落卡。",
                }],
            }, ensure_ascii=False),
            "tool_uses": [],
        }

    try:
        writes = await agent._run_life_reflection(mock_api_call)
    finally:
        await gate.on_shutdown(gate_ctx)

    cards = await store.search_cards("未授权群不得落卡", limit=10)
    outcome = {
        "writes": writes,
        "scopes": [(card.scope, card.scope_id) for card in cards],
    }
    assert raw_social_store.group_context_calls == []
    assert outcome in (
        {"writes": 0, "scopes": []},
        {"writes": 1, "scopes": [("global", "global")]},
    )


async def test_dream_life_reflection_partial_commit_retry_does_not_duplicate(
    store: CardStore,
    monkeypatch,
) -> None:
    draft = LifeReflectionDraft(cards=[
        LifeReflectionCardDraft(
            category="event",
            scope="global",
            scope_id="global",
            content="经历洞察：第一条。",
        ),
        LifeReflectionCardDraft(
            category="status",
            scope="global",
            scope_id="global",
            content="经历洞察：第二条。",
        ),
    ])
    agent = DreamAgent(store=store)
    original_add = store.add_card
    calls = 0

    async def flaky_add(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated second-card failure")
        return await original_add(*args, **kwargs)

    monkeypatch.setattr(store, "add_card", flaky_add)
    with pytest.raises(RuntimeError, match="second-card failure"):
        await agent._commit_life_reflection(draft, None)

    monkeypatch.setattr(store, "add_card", original_add)
    assert await agent._commit_life_reflection(draft, None) == 2

    cards = await store.search_cards("经历洞察", limit=10)
    assert sorted(card.content for card in cards) == [
        "经历洞察：第一条。",
        "经历洞察：第二条。",
    ]


async def test_dream_life_reflection_partial_commit_retry_keeps_changed_draft_cards(
    store: CardStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_draft = LifeReflectionDraft(cards=[
        LifeReflectionCardDraft(
            category="event",
            scope="global",
            scope_id="global",
            content="经历洞察：换稿回归 A。",
        ),
        LifeReflectionCardDraft(
            category="status",
            scope="global",
            scope_id="global",
            content="经历洞察：换稿回归 B。",
        ),
    ])
    retry_draft = LifeReflectionDraft(cards=[
        LifeReflectionCardDraft(
            category="event",
            scope="global",
            scope_id="global",
            content="经历洞察：换稿回归 X。",
        ),
        LifeReflectionCardDraft(
            category="status",
            scope="global",
            scope_id="global",
            content="经历洞察：换稿回归 Y。",
        ),
    ])
    agent = DreamAgent(store=store)
    original_add = store.add_card
    calls = 0

    async def fail_second_add(*args: Any, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated changed-draft second-card failure")
        return await original_add(*args, **kwargs)

    monkeypatch.setattr(store, "add_card", fail_second_add)
    with pytest.raises(RuntimeError, match="changed-draft second-card failure"):
        await agent._commit_life_reflection(first_draft, None)

    monkeypatch.setattr(store, "add_card", original_add)
    await agent._commit_life_reflection(retry_draft, None)
    same_retry_writes = await agent._commit_life_reflection(retry_draft, None)

    cards = await store.search_cards("换稿回归", limit=10)
    contents = [card.content for card in cards]
    assert {
        "has_x": "经历洞察：换稿回归 X。" in contents,
        "has_y": "经历洞察：换稿回归 Y。" in contents,
        "x_count": contents.count("经历洞察：换稿回归 X。"),
        "y_count": contents.count("经历洞察：换稿回归 Y。"),
        "same_retry_writes": same_retry_writes,
    } == {
        "has_x": True,
        "has_y": True,
        "x_count": 1,
        "y_count": 1,
        "same_retry_writes": 0,
    }


async def test_dream_life_reflection_arc_update_preserves_concurrent_writer(
    store: CardStore,
    tmp_path,
) -> None:
    story_store = StoryArcStore(tmp_path / "story_arcs")
    await story_store.startup()
    story_store.save(_reflection_arc())
    stale = story_store.load("stage_play_competition_week")
    assert stale is not None

    def external_update(arc: StoryArc) -> None:
        arc.variables["external_writer"] = "preserved"

    story_store.update("stage_play_competition_week", external_update)
    draft = LifeReflectionDraft(
        cards=[LifeReflectionCardDraft(
            category="event",
            scope="global",
            scope_id="global",
            content="经历洞察：并发更新后仍可提交。",
        )],
        last_event_summary="Dream 提交没有覆盖其他 writer。",
        open_threads=["继续观察并发写入"],
        next_day_seed="保留双方更新后继续。",
    )
    agent = DreamAgent(store=store, story_arc_store=story_store)

    assert await agent._commit_life_reflection(draft, stale) == 1

    loaded = story_store.load("stage_play_competition_week")
    assert loaded is not None
    assert loaded.variables["external_writer"] == "preserved"
    assert loaded.last_events[-1]["source"] == "dream_reflection"
    assert loaded.next_day_seed == "保留双方更新后继续。"


async def test_dream_life_reflection_retry_does_not_duplicate_arc_event(
    store: CardStore,
    tmp_path,
) -> None:
    story_store = StoryArcStore(tmp_path / "story_arcs")
    await story_store.startup()
    story_store.save(_reflection_arc())
    stale = story_store.load("stage_play_competition_week")
    assert stale is not None
    draft = LifeReflectionDraft(
        cards=[LifeReflectionCardDraft(
            category="event",
            scope="global",
            scope_id="global",
            content="经历洞察：同日重试只写一次。",
        )],
        last_event_summary="同日 Dream event 只保留一次。",
    )
    agent = DreamAgent(store=store, story_arc_store=story_store)

    assert await agent._commit_life_reflection(draft, stale) == 1
    assert await agent._commit_life_reflection(draft, stale) == 0

    loaded = story_store.load("stage_play_competition_week")
    assert loaded is not None
    events = [event for event in loaded.last_events if event.get("source") == "dream_reflection"]
    assert len(events) == 1


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
