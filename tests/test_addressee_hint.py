from __future__ import annotations

from types import SimpleNamespace

from services.llm.addressee_hint import AddresseeHintDetector
from services.name_registry import NameVariationRegistry


def _registry() -> NameVariationRegistry:
    registry = NameVariationRegistry()
    registry.update_from_event("100", 1, "小明", "")
    registry.update_from_event("100", 2, "小红", "红总")
    return registry


def test_addressee_hint_prefers_reply_sender() -> None:
    detector = AddresseeHintDetector(_registry())

    result = detector.detect(
        group_id="100",
        trigger=SimpleNamespace(extra={"reply_sender_id": "2"}, mode="at_mention"),
        fallback_user_id="1",
        bot_self_id="999",
    )

    assert result is not None
    assert result.qq == 2
    assert result.provenance == "reply_trigger"


def test_addressee_hint_uses_at_trigger_sender() -> None:
    detector = AddresseeHintDetector(_registry())

    result = detector.detect(
        group_id="100",
        trigger=SimpleNamespace(extra={}, mode="at_mention", target_user_id="1"),
        fallback_user_id="2",
        bot_self_id="999",
    )

    assert result is not None
    assert result.qq == 1
    assert result.provenance == "at_trigger"


def test_addressee_hint_falls_back_to_last_speaker() -> None:
    detector = AddresseeHintDetector(_registry())

    result = detector.detect(
        group_id="100",
        trigger=None,
        fallback_user_id="2",
        bot_self_id="999",
    )

    assert result is not None
    assert result.qq == 2
    assert result.provenance == "last_speaker"


def test_addressee_hint_builds_text() -> None:
    detector = AddresseeHintDetector(_registry())
    result = detector.detect(
        group_id="100",
        trigger=None,
        fallback_user_id="2",
        bot_self_id="999",
    )

    assert result is not None
    assert detector.build_hint(result) == "[当前你在回复：红总（QQ: 2）]"
