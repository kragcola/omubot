"""Tests for ScheduleGenerator parsing and prompt building."""

from __future__ import annotations

from plugins.schedule.generator import _parse_schedule
from services.llm.provider import extract_text


class TestExtractText:
    def test_extracts_text_field(self):
        """_call_api returns {"text": "...", ...} — flat text field."""
        result = {"text": "hello world", "tool_uses": [], "thinking_blocks": []}
        assert extract_text(result) == "hello world"

    def test_empty_text_returns_empty_even_with_thinking(self):
        """Thinking blocks must never leak into user-visible text."""
        result = {
            "text": "",
            "thinking_blocks": [
                {"type": "thinking", "thinking": "thought content"},
            ],
        }
        assert extract_text(result) == ""

    def test_empty_returns_empty(self):
        assert extract_text({"text": ""}) == ""


class TestParseSchedule:
    def test_parses_valid_json(self):
        json_str = """{
            "date": "2026-04-29",
            "theme": "排练日",
            "day_narrative": "忙碌而充实的一天",
            "slots": [
                {"time": "08:00", "activity": "起床", "mood_hint": "困倦", "location": "家里"},
                {"time": "12:00", "activity": "吃午饭", "mood_hint": "放松", "location": "食堂"}
            ]
        }"""
        schedule = _parse_schedule(json_str, "2026-04-29")
        assert schedule is not None
        assert schedule.date == "2026-04-29"
        assert schedule.theme == "排练日"
        assert schedule.day_narrative == "忙碌而充实的一天"
        assert len(schedule.slots) == 2
        assert schedule.slots[0].time == "08:00"
        assert schedule.slots[0].activity == "起床"

    def test_parses_code_fenced_json(self):
        json_str = """```json
{
    "date": "2026-04-29",
    "theme": "测试",
    "day_narrative": "",
    "slots": [{"time": "08:00", "activity": "起床", "mood_hint": "困倦", "location": ""}]
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
    "slots": [{"time": "08:00", "activity": "起床", "mood_hint": "困倦", "location": ""}]
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
            "slots": [{"time": "08:00", "activity": "起床", "mood_hint": "困倦"}]
        }"""
        schedule = _parse_schedule(json_str, "2026-04-29")
        assert schedule is not None
        assert schedule.theme == ""
        assert schedule.day_narrative == ""
        assert schedule.slots[0].location == ""
