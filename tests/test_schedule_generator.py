"""Tests for ScheduleGenerator parsing and prompt building."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from plugins.schedule import generator as generator_module
from plugins.schedule.generator import (
    PersonaScheduleBrief,
    ScheduleGenerator,
    _extract_text,
    _parse_schedule,
    _render_continuity_context,
    _render_persona_schedule_brief,
    _render_reflection_insight_context,
    _render_story_arc_context,
    update_story_arc_after_schedule,
)
from plugins.schedule.plugin import DialogueClimateConfig, ScheduleConfig
from plugins.schedule.store import ScheduleStore
from plugins.schedule.story_arc import FictionPartnerProfile, FictionPartnerState, StoryArc
from plugins.schedule.types import Schedule, TimeSlot


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 6, 8, 9, 30, tzinfo=tz)


class RollingDateTime(datetime):
    current = datetime(2026, 6, 8, 9, 30)

    @classmethod
    def now(cls, tz=None):
        return cls(
            cls.current.year,
            cls.current.month,
            cls.current.day,
            cls.current.hour,
            cls.current.minute,
            tzinfo=tz,
        )


@dataclass(frozen=True)
class FakeMemoryCard:
    category: str
    content: str
    source: str = ""
    scope: str = ""
    scope_id: str = ""


class FakeMemoryCardStore:
    def __init__(self, cards: list[FakeMemoryCard]) -> None:
        self.cards = cards
        self.calls: list[tuple[str, int]] = []

    async def search_cards(self, query: str, *, limit: int = 10):
        self.calls.append((query, limit))
        return self.cards[:limit]


class FakeStoryArcStore:
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


class FakePartnerStateStore:
    def __init__(self) -> None:
        self.calls = 0
        self.saved: list[FictionPartnerState] = []

    def ensure_cards(self, profiles: Sequence[FictionPartnerProfile]) -> list[FictionPartnerState]:
        self.calls += 1
        return [
            FictionPartnerState(
                entity_id=profile.entity_id,
                display_name=profile.display_name,
                pinned_profile=profile.pinned_profile,
                mood="亢奋但压力大",
                availability="busy",
                current_state="正在为舞台剧比赛调整动作",
                constraints=list(profile.constraints),
            )
            for profile in profiles
        ]

    def save(self, state: FictionPartnerState) -> None:
        self.saved.append(state)


def _make_yesterday_schedule() -> Schedule:
    return Schedule(
        date="2026-06-07",
        day_narrative="昨天排练中途失误，但最后大家重新把节奏找了回来。",
        theme="排练复盘日",
        generated_at="2026-06-07T02:00:00+08:00",
        slots=[
            TimeSlot(
                time="09:00",
                activity="practice",
                mood_hint="低落",
                location="凤凰奇幻乐园",
                description="在舞台边反复确认走位，第一次合练时节奏明显乱掉",
            ),
            TimeSlot(
                time="15:00",
                activity="social",
                mood_hint="振作",
                location="后台",
                description="和大家围成一圈，把失败的部分拆成能重来的小段",
            ),
        ],
    )


async def _capture_generate_call(
    tmp_path,
    monkeypatch,
    *,
    persona_driven_enabled: bool | None = None,
    persona_brief: PersonaScheduleBrief | None = None,
    yesterday_schedule: Schedule | None = None,
    memory_card_store: FakeMemoryCardStore | None = None,
    story_arc_enabled: bool | None = None,
    story_arc_store: FakeStoryArcStore | None = None,
    partner_state_store: FakePartnerStateStore | None = None,
    fiction_partner_profiles: tuple[FictionPartnerProfile, ...] = (),
    event_replan_enabled: bool | None = None,
):
    monkeypatch.setattr(generator_module, "datetime", FixedDateTime)
    schedule_key_parts = ["schedule-default" if persona_driven_enabled is None else f"persona-{persona_driven_enabled}"]
    if story_arc_enabled is not None:
        schedule_key_parts.append(f"arc-{story_arc_enabled}")
    if event_replan_enabled is not None:
        schedule_key_parts.append(f"event-{event_replan_enabled}")
    if story_arc_store is not None and story_arc_enabled is None:
        schedule_key_parts.append("arc-store-unused")
    schedule_dir = tmp_path / "-".join(schedule_key_parts)
    schedule_dir.mkdir()
    store = ScheduleStore(storage_dir=str(schedule_dir))
    if yesterday_schedule is not None:
        store.save(yesterday_schedule)
        store.load("2026-06-07")
    kwargs: dict[str, Any] = {"store": store, "identity_name": "凤晓梦"}
    if persona_driven_enabled is not None:
        kwargs["persona_driven_enabled"] = persona_driven_enabled
    if persona_brief is not None:
        kwargs["persona_brief"] = persona_brief
    if memory_card_store is not None:
        kwargs["memory_card_store"] = memory_card_store
    if story_arc_enabled is not None:
        kwargs["story_arc_enabled"] = story_arc_enabled
    if story_arc_store is not None:
        kwargs["story_arc_store"] = story_arc_store
    if partner_state_store is not None:
        kwargs["partner_state_store"] = partner_state_store
    if fiction_partner_profiles:
        kwargs["fiction_partner_profiles"] = fiction_partner_profiles
    if event_replan_enabled is not None:
        kwargs["event_replan_enabled"] = event_replan_enabled
    generator = ScheduleGenerator(**kwargs)
    captured: dict[str, Any] = {}

    async def api_call(system, messages, tools=None, max_tokens=None):
        captured["system"] = system
        captured["messages"] = messages
        captured["tools"] = tools
        captured["max_tokens"] = max_tokens
        return {
            "text": json.dumps({
                "date": "2026-06-08",
                "theme": "测试日程",
                "day_narrative": "用于测试的稳定日程",
                "slots": [
                    {
                        "time": "08:00",
                        "activity": "study",
                        "description": "在教室整理笔记",
                        "mood_hint": "专注",
                        "location": "教室",
                    },
                ],
            }),
        }

    await generator._generate(api_call)
    captured["saved_bytes"] = (schedule_dir / "2026-06-08.json").read_bytes()
    captured["store_current_date"] = store.current.date if store.current else None
    return captured


class TestExtractText:
    def test_extracts_text_field(self):
        """_call_api returns {"text": "...", ...} — flat text field."""
        result = {"text": "hello world", "tool_uses": [], "thinking_blocks": []}
        assert _extract_text(result) == "hello world"

    def test_empty_text_falls_back_to_thinking(self):
        result = {
            "text": "",
            "thinking_blocks": [
                {"type": "thinking", "thinking": "thought content"},
            ],
        }
        assert _extract_text(result) == "thought content"

    def test_empty_returns_empty(self):
        assert _extract_text({"text": ""}) == ""


class TestPersonaDrivenScheduleFlag:
    def test_config_defaults_persona_driven_off(self):
        cfg = ScheduleConfig.model_validate({})
        assert cfg.persona_driven_enabled is False

    def test_config_defaults_dialogue_climate_m1_off(self):
        cfg = ScheduleConfig.model_validate({})
        assert isinstance(cfg.dialogue_climate, DialogueClimateConfig)
        assert cfg.dialogue_climate.m1_enabled is False

    def test_config_defaults_event_replan_off(self):
        cfg = ScheduleConfig.model_validate({})
        assert cfg.event_replan_enabled is False

    def test_config_accepts_event_replan_override(self):
        cfg = ScheduleConfig.model_validate({"event_replan_enabled": True})
        assert cfg.event_replan_enabled is True

    def test_config_accepts_dialogue_climate_m1_override(self):
        cfg = ScheduleConfig.model_validate({"dialogue_climate": {"m1_enabled": True}})
        assert cfg.dialogue_climate.m1_enabled is True

    async def test_flag_off_keeps_generate_prompt_identical(self, tmp_path, monkeypatch):
        default_call = await _capture_generate_call(tmp_path, monkeypatch)
        explicit_off_call = await _capture_generate_call(
            tmp_path,
            monkeypatch,
            persona_driven_enabled=False,
        )

        assert explicit_off_call == default_call

    async def test_flag_off_does_not_consume_persona_brief(self, tmp_path, monkeypatch):
        default_call = await _capture_generate_call(tmp_path, monkeypatch)
        off_with_brief_call = await _capture_generate_call(
            tmp_path,
            monkeypatch,
            persona_driven_enabled=False,
            persona_brief=PersonaScheduleBrief(
                identity="WxS 成员、宫益坂女子学园二年级",
                traits=("亮", "快"),
                known_facts=("伙伴关系（天马司、草薙宁宁、神代类、朝比奈真冬）",),
            ),
        )

        assert off_with_brief_call == default_call

    async def test_flag_off_does_not_load_recent_memory_cards(self, tmp_path, monkeypatch):
        memory_store = FakeMemoryCardStore([
            FakeMemoryCard(category="event", content="昨天排练失误后答应今天补练"),
        ])

        await _capture_generate_call(
            tmp_path,
            monkeypatch,
            persona_driven_enabled=False,
            yesterday_schedule=_make_yesterday_schedule(),
            memory_card_store=memory_store,
        )

        assert memory_store.calls == []

    async def test_flag_on_injects_short_persona_brief(self, tmp_path, monkeypatch):
        call = await _capture_generate_call(
            tmp_path,
            monkeypatch,
            persona_driven_enabled=True,
            persona_brief=PersonaScheduleBrief(
                identity="Wonderlands×Showtime 的成员，凤凰奇幻乐园守护者；宫益坂女子学园二年级。",
                traits=("亮：情绪明亮", "快：行动很快"),
                known_facts=("伙伴关系（天马司、草薙宁宁、神代类、朝比奈真冬）、祖父是动机源头。",),
                partner_context="天马司、草薙宁宁、神代类、朝比奈真冬是稳定的虚构伙伴关系。",
            ),
        )

        user_text = call["messages"][0]["content"]
        assert "【persona 日程短要点】" in user_text
        assert "Wonderlands×Showtime" in user_text
        assert "宫益坂女子学园二年级" in user_text
        assert "天马司、草薙宁宁、神代类、朝比奈真冬" in user_text
        assert "fiction" in user_text
        assert "factual" in user_text
        assert "## 1. 是谁" not in user_text
        assert "禁说事实" not in user_text

    def test_persona_brief_render_is_bounded(self):
        text = _render_persona_schedule_brief(
            PersonaScheduleBrief(
                identity="身份" * 500,
                traits=("亮", "快"),
                known_facts=("已知事实",),
                partner_context="伙伴近况",
            ),
        )

        assert len(text) <= 700

    def test_continuity_context_render_is_bounded(self):
        cards = [
            FakeMemoryCard(category="event", content="昨天排练失误后约好今天先做低难度复盘。" * 80),
            FakeMemoryCard(category="status", content="司今天上午可能会迟到，需要先和宁宁对台词。" * 80),
        ]
        yesterday = Schedule(
            date="2026-06-07",
            day_narrative="昨天排练中途失误，但最后大家重新把节奏找了回来。" * 80,
            theme="排练复盘日",
            generated_at="2026-06-07T02:00:00+08:00",
            slots=[
                TimeSlot(
                    time="09:00",
                    activity="practice",
                    mood_hint="低落",
                    location="凤凰奇幻乐园",
                    description="在舞台边反复确认走位，第一次合练时节奏明显乱掉" * 80,
                ),
            ],
        )

        text = _render_continuity_context(yesterday, cards)

        assert len(text) <= 900
        assert "不要重复昨日主题" in text

    def test_reflection_insight_context_render_is_bounded(self):
        cards = [
            FakeMemoryCard(
                category="event",
                content="经历洞察：昨天排练后团队把动作拆小，晓梦对伙伴受挫更敏感。" * 80,
                source="dream_reflection",
                scope="group",
                scope_id="1001",
            ),
        ]

        text = _render_reflection_insight_context(cards)

        assert len(text) <= 700
        assert "【昨日经历洞察】" in text
        assert "不生成真人线下行为" in text

    async def test_flag_on_injects_yesterday_and_recent_memory_context(self, tmp_path, monkeypatch):
        memory_store = FakeMemoryCardStore([
            FakeMemoryCard(category="event", content="昨天排练失误后约好今天先做低难度复盘。"),
            FakeMemoryCard(category="status", content="司今天上午可能会迟到，需要先和宁宁对台词。"),
        ])
        call = await _capture_generate_call(
            tmp_path,
            monkeypatch,
            persona_driven_enabled=True,
            persona_brief=PersonaScheduleBrief(identity="WxS 成员"),
            yesterday_schedule=_make_yesterday_schedule(),
            memory_card_store=memory_store,
        )

        user_text = call["messages"][0]["content"]
        assert memory_store.calls == [("", 5)]
        assert "【跨天连续与最近记忆】" in user_text
        assert "昨日日程摘要" in user_text
        assert "排练复盘日" in user_text
        assert "昨天排练中途失误" in user_text
        assert "昨天排练失误后约好今天先做低难度复盘" in user_text
        assert "不要重复昨日主题" in user_text
        assert "跨天因果" in user_text
        assert call["store_current_date"] == "2026-06-08"

    async def test_flag_on_three_day_chain_keeps_themes_distinct_and_causal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(generator_module, "datetime", RollingDateTime)
        schedule_dir = tmp_path / "three-day-schedule"
        schedule_dir.mkdir()
        store = ScheduleStore(storage_dir=str(schedule_dir))
        memory_store = FakeMemoryCardStore([
            FakeMemoryCard(category="event", content="昨天排练失误后约好今天先做低难度复盘。"),
        ])
        generator = ScheduleGenerator(
            store=store,
            identity_name="凤晓梦",
            persona_driven_enabled=True,
            persona_brief=PersonaScheduleBrief(identity="WxS 成员"),
            memory_card_store=memory_store,
        )
        prompts: list[str] = []
        themes = ["开局低落复盘", "低难度重整", "并肩推进"]
        narratives = [
            "昨天排练失误留下了点低落，今天先安静复盘。",
            "承接昨天的低难度复盘，把台词节奏重新拢回来。",
            "接住昨天找回的节奏，和伙伴把舞台动作推进。",
        ]

        async def api_call(system, messages, tools=None, max_tokens=None):
            del system, tools, max_tokens
            prompts.append(messages[0]["content"])
            index = len(prompts) - 1
            return {
                "text": json.dumps({
                    "date": RollingDateTime.now(generator_module.CST).strftime("%Y-%m-%d"),
                    "theme": themes[index],
                    "day_narrative": narratives[index],
                    "slots": [
                        {
                            "time": "08:00",
                            "activity": "practice",
                            "description": narratives[index],
                            "mood_hint": "专注" if index else "低落",
                            "location": "凤凰奇幻乐园",
                        },
                    ],
                }),
            }

        for day in (8, 9, 10):
            RollingDateTime.current = datetime(2026, 6, day, 9, 30)
            await generator._generate(api_call)

        saved = [
            store.load("2026-06-08", update_current=False),
            store.load("2026-06-09", update_current=False),
            store.load("2026-06-10", update_current=False),
        ]
        saved_themes = [schedule.theme for schedule in saved if schedule is not None]

        assert len(saved_themes) == 3
        assert len(set(saved_themes)) == 3
        assert memory_store.calls == [("", 5), ("", 5), ("", 5)]
        assert "昨日主题：开局低落复盘" in prompts[1]
        assert "昨日主题：低难度重整" in prompts[2]
        assert all("不要重复昨日主题" in prompt for prompt in prompts)
        assert saved[1] is not None
        assert "承接昨天的低难度复盘" in saved[1].day_narrative
        assert saved[2] is not None
        assert "接住昨天找回的节奏" in saved[2].day_narrative


class TestEventReplanScheduleGenerator:
    async def test_event_replan_flag_off_does_not_read_reflection_cards(self, tmp_path, monkeypatch):
        memory_store = FakeMemoryCardStore([
            FakeMemoryCard(
                category="event",
                content="经历洞察：昨天排练后团队开始担心动作安全。",
                source="dream_reflection",
            ),
        ])

        default_call = await _capture_generate_call(tmp_path, monkeypatch)
        off_call = await _capture_generate_call(
            tmp_path,
            monkeypatch,
            memory_card_store=memory_store,
            event_replan_enabled=False,
        )

        assert off_call == default_call
        assert memory_store.calls == []

    async def test_event_replan_flag_on_injects_dream_reflection_insights(self, tmp_path, monkeypatch):
        memory_store = FakeMemoryCardStore([
            FakeMemoryCard(
                category="event",
                content="经历洞察：昨天排练后团队意识到要先稳住低难度动作。",
                source="dream_reflection",
                scope="group",
                scope_id="1001",
            ),
            FakeMemoryCard(
                category="event",
                content="普通闲聊：午饭想吃什么。",
                source="manual",
            ),
        ])

        call = await _capture_generate_call(
            tmp_path,
            monkeypatch,
            memory_card_store=memory_store,
            event_replan_enabled=True,
        )

        user_text = call["messages"][0]["content"]
        assert memory_store.calls == [("经历洞察", 3)]
        assert "【昨日经历洞察】" in user_text
        assert "昨天排练后团队意识到要先稳住低难度动作" in user_text
        assert "普通闲聊" not in user_text
        assert "不生成真人线下行为" in user_text


class TestStoryArcSchedule:
    def test_story_arc_context_render_includes_partner_states_and_redline(self):
        arc = StoryArc(
            arc_id="stage_play_competition_week",
            title="舞台剧比赛准备周",
            stage="preparation",
            goals=["完成舞台剧比赛准备"],
            active_conflicts=["排练时间不足", "期末考试压力升高"],
            variables={"exam_pressure": 0.7, "rehearsal_progress": 0.35},
            partner_states={
                "tenma_tsukasa": {
                    "kind": "fiction",
                    "display_name": "天马司",
                    "mood": "亢奋但压力大",
                    "availability": "busy",
                    "current_state": "正在为舞台剧比赛调整动作",
                },
            },
            next_day_seed="在复习和排练之间做取舍",
        )

        text = _render_story_arc_context(arc)

        assert "【当前剧情弧】" in text
        assert "舞台剧比赛准备周" in text
        assert "主线内做每日变奏" in text
        assert "天马司" in text
        assert "fiction，不是真人 factual" in text
        assert "不要生成或暗示任何真人线下行为" in text

    def test_story_arc_context_render_includes_active_replan_constraints(self):
        arc = StoryArc(
            arc_id="stage_play_competition_week",
            title="舞台剧比赛准备周",
            event_budget={
                "active_replan_constraints": [
                    {
                        "constraint": "天马司轻微扭伤，接下来只做低难度站位和台词复盘，不重写历史。",
                        "remaining_days": 3,
                    },
                ],
            },
        )

        text = _render_story_arc_context(arc)

        assert "近端剧情约束" in text
        assert "remaining_days=3" in text
        assert "低难度站位" in text
        assert "不重写历史" in text

    async def test_story_arc_flag_off_does_not_read_arc_store(self, tmp_path, monkeypatch):
        story_store = FakeStoryArcStore(StoryArc(arc_id="stage_play_competition_week"))

        default_call = await _capture_generate_call(tmp_path, monkeypatch)
        off_call = await _capture_generate_call(
            tmp_path,
            monkeypatch,
            story_arc_enabled=False,
            story_arc_store=story_store,
        )

        assert off_call == default_call
        assert story_store.load_active_calls == 0
        assert story_store.saved == []

    async def test_story_arc_flag_on_injects_active_arc_and_partner_states(self, tmp_path, monkeypatch):
        arc = StoryArc(
            arc_id="stage_play_competition_week",
            title="舞台剧比赛准备周",
            stage="preparation",
            goals=["完成舞台剧比赛准备"],
            active_conflicts=["排练时间不足", "期末考试压力升高"],
            variables={"exam_pressure": 0.7, "rehearsal_progress": 0.35},
        )
        story_store = FakeStoryArcStore(arc)
        partner_store = FakePartnerStateStore()
        profiles = (
            FictionPartnerProfile(
                entity_id="tenma_tsukasa",
                display_name="天马司",
                pinned_profile="W×S 成员，舞台中心感强。",
                constraints=("kind=fiction",),
            ),
        )

        call = await _capture_generate_call(
            tmp_path,
            monkeypatch,
            story_arc_enabled=True,
            story_arc_store=story_store,
            partner_state_store=partner_store,
            fiction_partner_profiles=profiles,
        )

        user_text = call["messages"][0]["content"]
        assert story_store.load_active_calls == 1
        assert partner_store.calls == 1
        assert "【当前剧情弧】" in user_text
        assert "舞台剧比赛准备周" in user_text
        assert "不要每天随机新主题" in user_text
        assert "天马司" in user_text
        assert arc.partner_states["tenma_tsukasa"]["kind"] == "fiction"
        assert len(story_store.saved) >= 2
        assert story_store.arc is not None
        assert story_store.arc.last_events[-1]["date"] == "2026-06-08"
        assert "测试日程" in story_store.arc.last_events[-1]["summary"]
        assert "排练时间不足" in story_store.arc.next_day_seed

    def test_story_arc_update_after_schedule_advances_variables(self):
        arc = StoryArc(
            arc_id="stage_play_competition_week",
            active_conflicts=["排练时间不足"],
            variables={
                "deadline_days_left": 6,
                "exam_pressure": 0.7,
                "rehearsal_progress": 0.35,
                "team_morale": 0.6,
            },
        )
        schedule = Schedule(
            date="2026-06-08",
            theme="舞台剧复盘日",
            day_narrative="排练和复习之间出现真实取舍。",
            generated_at="2026-06-08T02:00:00+08:00",
            slots=[
                TimeSlot(
                    time="09:00",
                    activity="practice",
                    mood_hint="专注",
                    location="凤凰奇幻乐园",
                    description="先把低难度动作重新排一遍",
                ),
            ],
        )

        update_story_arc_after_schedule(arc, schedule)

        assert arc.last_events[-1]["theme"] == "舞台剧复盘日"
        assert "排练和复习之间" in arc.last_events[-1]["summary"]
        assert arc.next_day_seed.startswith("承接 2026-06-08")
        assert arc.variables["deadline_days_left"] == 5
        assert arc.variables["exam_pressure"] == 0.73
        assert arc.variables["rehearsal_progress"] == 0.43
        assert arc.variables["team_morale"] == 0.62
        assert arc.event_budget["generated_days"] == 1

    def test_story_arc_update_after_schedule_decays_replan_constraints(self):
        arc = StoryArc(
            arc_id="stage_play_competition_week",
            active_conflicts=["伙伴轻微扭伤后的低难度站位"],
            event_budget={
                "active_replan_constraints": [
                    {
                        "constraint": "接下来只做低难度站位和台词复盘，不重写历史。",
                        "remaining_days": 2,
                    },
                ],
            },
        )
        schedule = Schedule(
            date="2026-06-08",
            theme="低难度复盘",
            day_narrative="团队承接突发情况，先稳住排练节奏。",
            generated_at="2026-06-08T02:00:00+08:00",
            slots=[
                TimeSlot(
                    time="09:00",
                    activity="practice",
                    mood_hint="担心",
                    location="凤凰奇幻乐园",
                    description="把原动作拆成更小的低难度片段",
                ),
            ],
        )

        update_story_arc_after_schedule(arc, schedule)
        assert arc.event_budget["active_replan_constraints"][0]["remaining_days"] == 1

        schedule.date = "2026-06-09"
        update_story_arc_after_schedule(arc, schedule)
        assert "active_replan_constraints" not in arc.event_budget

    async def test_story_arc_seven_day_chain_updates_two_fiction_partners(self, tmp_path, monkeypatch):
        monkeypatch.setattr(generator_module, "datetime", RollingDateTime)
        schedule_dir = tmp_path / "seven-day-schedule"
        schedule_dir.mkdir()
        store = ScheduleStore(storage_dir=str(schedule_dir))
        arc = StoryArc(
            arc_id="stage_play_competition_week",
            title="舞台剧比赛准备周",
            stage="preparation",
            goals=["完成舞台剧比赛准备", "兼顾期末复习"],
            active_conflicts=["排练时间不足", "期末考试压力升高"],
            variables={
                "deadline_days_left": 7,
                "exam_pressure": 0.55,
                "rehearsal_progress": 0.2,
                "team_morale": 0.5,
            },
            partner_states={
                "tenma_tsukasa": {
                    "kind": "fiction",
                    "display_name": "天马司",
                    "current_state": "刚开始排练",
                    "mood": "兴奋",
                    "availability": "normal",
                    "recent_events": [],
                    "constraints": ["kind=fiction"],
                },
                "kusanagi_nene": {
                    "kind": "fiction",
                    "display_name": "草薙宁宁",
                    "current_state": "担心动作完成度",
                    "mood": "谨慎",
                    "availability": "normal",
                    "recent_events": [],
                    "constraints": ["kind=fiction"],
                },
            },
        )
        story_store = FakeStoryArcStore(arc)
        partner_store = FakePartnerStateStore()
        generator = ScheduleGenerator(
            store=store,
            identity_name="凤晓梦",
            story_arc_enabled=True,
            story_arc_store=story_store,
            partner_state_store=partner_store,
        )

        async def api_call(system, messages, tools=None, max_tokens=None):
            del system, tools, max_tokens
            day = RollingDateTime.now(generator_module.CST).day
            return {
                "text": json.dumps({
                    "date": RollingDateTime.now(generator_module.CST).strftime("%Y-%m-%d"),
                    "theme": f"舞台剧推进第{day - 7}天",
                    "day_narrative": "排练进度和期末复习被迫互相让步。",
                    "slots": [
                        {
                            "time": "08:00",
                            "activity": "study",
                            "description": "先在教室完成一段期末复习",
                            "mood_hint": "紧张",
                            "location": "教室",
                        },
                        {
                            "time": "16:00",
                            "activity": "practice",
                            "description": "和伙伴把舞台动作拆成更稳的小段",
                            "mood_hint": "专注",
                            "location": "凤凰奇幻乐园",
                        },
                    ],
                }),
            }

        for day in range(8, 15):
            RollingDateTime.current = datetime(2026, 6, day, 9, 30)
            await generator._generate(api_call)

        assert story_store.arc is not None
        assert len(story_store.arc.last_events) == 6
        assert story_store.arc.variables["deadline_days_left"] == 0
        assert story_store.arc.variables["rehearsal_progress"] > 0.7
        tsukasa = story_store.arc.partner_states["tenma_tsukasa"]
        nene = story_store.arc.partner_states["kusanagi_nene"]
        assert tsukasa["kind"] == "fiction"
        assert nene["kind"] == "fiction"
        assert tsukasa["recent_events"]
        assert nene["recent_events"]
        assert tsukasa["current_state"] != "刚开始排练"
        assert nene["current_state"] != "担心动作完成度"
        assert {state.entity_id for state in partner_store.saved} >= {"tenma_tsukasa", "kusanagi_nene"}


class TestParseSchedule:
    def test_parses_valid_json(self):
        json_str = """{
            "date": "2026-04-29",
            "theme": "排练日",
            "day_narrative": "忙碌而充实的一天",
            "slots": [
                {"time": "08:00", "activity": "rest", "mood_hint": "困倦", "location": "家里"},
                {"time": "12:00", "activity": "meal", "mood_hint": "放松", "location": "食堂"}
            ]
        }"""
        schedule = _parse_schedule(json_str, "2026-04-29")
        assert schedule is not None
        assert schedule.date == "2026-04-29"
        assert schedule.theme == "排练日"
        assert schedule.day_narrative == "忙碌而充实的一天"
        assert len(schedule.slots) == 2
        assert schedule.slots[0].time == "08:00"
        assert schedule.slots[0].activity == "rest"

    def test_parses_code_fenced_json(self):
        json_str = """```json
{
    "date": "2026-04-29",
    "theme": "测试",
    "day_narrative": "",
    "slots": [{"time": "08:00", "activity": "rest", "mood_hint": "困倦", "location": ""}]
}
```"""
        schedule = _parse_schedule(json_str, "2026-04-29")
        assert schedule is not None
        assert schedule.theme == "测试"

    def test_parses_generic_fenced_json(self):
        json_str = """```
{
    "date": "2026-04-29",
    "theme": "测试",
    "day_narrative": "",
    "slots": [{"time": "08:00", "activity": "rest", "mood_hint": "困倦", "location": ""}]
}
```"""
        schedule = _parse_schedule(json_str, "2026-04-29")
        assert schedule is not None
        assert schedule.theme == "测试"

    def test_invalid_json_returns_none(self):
        assert _parse_schedule("not json at all", "2026-04-29") is None

    def test_missing_slots_returns_none(self):
        json_str = '{"date": "2026-04-29", "theme": "test", "day_narrative": ""}'
        assert _parse_schedule(json_str, "2026-04-29") is None

    def test_missing_optional_fields_defaulted(self):
        json_str = """{
            "date": "2026-04-29",
            "slots": [{"time": "08:00", "activity": "rest", "mood_hint": "困倦"}]
        }"""
        schedule = _parse_schedule(json_str, "2026-04-29")
        assert schedule is not None
        assert schedule.theme == ""
        assert schedule.day_narrative == ""
        assert schedule.slots[0].location == ""
