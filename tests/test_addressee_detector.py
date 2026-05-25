import asyncio
from collections.abc import Sequence

import pytest

from services.group import AddresseeDetector, AddresseeResult


@pytest.mark.asyncio
async def test_adapter_target_wins() -> None:
    detector = AddresseeDetector(bot_ids=("42",))

    result = await detector.detect({"target_id": "100", "message": "[CQ:at,qq=42] hi"})

    assert result == AddresseeResult(target_id="100", confidence=0.98, source="adapter")


@pytest.mark.asyncio
async def test_adapter_mentioned_bot_uses_bot_id() -> None:
    detector = AddresseeDetector(bot_ids=("42",))

    result = await detector.detect({"additional_config": {"mentioned_bot": True}})

    assert result == AddresseeResult(target_id="42", confidence=0.96, source="adapter")


@pytest.mark.asyncio
async def test_regex_detects_bot_name_call() -> None:
    detector = AddresseeDetector(bot_ids=("42",), bot_names=("小梦",))

    result = await detector.detect({"text": "小梦，帮我看看这个"})

    assert result == AddresseeResult(target_id="42", confidence=0.82, source="regex")


@pytest.mark.asyncio
async def test_quote_sender_detected_before_at() -> None:
    detector = AddresseeDetector(bot_ids=("42",))

    result = await detector.detect({"reply_sender_id": "100", "message": "[CQ:at,qq=42] 补一句"})

    assert result == AddresseeResult(target_id="100", confidence=0.72, source="quote")


@pytest.mark.asyncio
async def test_cq_at_detected() -> None:
    detector = AddresseeDetector()

    result = await detector.detect({"message": "[CQ:at,qq=42] 在吗"})

    assert result == AddresseeResult(target_id="42", confidence=0.9, source="at")


@pytest.mark.asyncio
async def test_no_target_returns_none() -> None:
    detector = AddresseeDetector(bot_ids=("42",), bot_names=("小梦",))

    result = await detector.detect({"text": "大家看看这个"})

    assert result == AddresseeResult(target_id=None, confidence=0.0, source="none")


@pytest.mark.asyncio
async def test_cancel_during_cascade_does_not_poison_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = AddresseeDetector(bot_ids=("42",))

    async def boom(
        message: object,
        bot_ids: Sequence[str],
        bot_names: Sequence[str],
    ) -> AddresseeResult:
        raise asyncio.CancelledError

    monkeypatch.setattr(detector, "_adapter_layer", boom)
    with pytest.raises(asyncio.CancelledError):
        await detector.detect({"message": "[CQ:at,qq=42] hi"})

    monkeypatch.setattr(
        detector,
        "_adapter_layer",
        AddresseeDetector._adapter_layer.__get__(detector, AddresseeDetector),
    )
    result = await detector.detect({"message": "[CQ:at,qq=42] hi"})

    assert result == AddresseeResult(target_id="42", confidence=0.9, source="at")
