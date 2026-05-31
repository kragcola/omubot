from __future__ import annotations

from services.llm.sentinel_registry import apply_guardrails


def test_sparkle_watcher_warns_on_sparkles_without_rewriting() -> None:
    result = apply_guardrails("今天心情好✨", config=None)

    assert result.passed is True
    assert result.text == "今天心情好✨"
    assert any(hit.name == "sparkle_symbol_watcher" for hit in result.hits)


def test_sparkle_watcher_warns_on_star_without_rewriting() -> None:
    result = apply_guardrails("哇嚯☆好厉害", config=None)

    assert result.passed is True
    assert result.text == "哇嚯☆好厉害"
    assert any(hit.name == "sparkle_symbol_watcher" for hit in result.hits)


def test_sparkle_watcher_ignores_plain_text() -> None:
    result = apply_guardrails("今天天气真好", config=None)

    assert result.passed is True
    assert result.text == "今天天气真好"
    assert all(hit.name != "sparkle_symbol_watcher" for hit in result.hits)
