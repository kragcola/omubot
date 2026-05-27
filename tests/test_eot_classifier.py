from __future__ import annotations

import pytest

from services.scheduler_eot import EOTCache, EOTClassifier, build_eot_request, parse_eot_output


def test_parse_eot_output_handles_direct_fenced_and_fallback() -> None:
    direct = parse_eot_output('{"probability":0.8,"reason":"点名"}')
    fenced = parse_eot_output('```json\n{"p":0.2,"reason":"热聊"}\n```')
    fallback = parse_eot_output("not json")

    assert direct.probability == 0.8
    assert fenced.probability == 0.2
    assert fallback.probability == 0.5
    assert fallback.parse_mode == "fallback"


def test_build_eot_request_uses_scheduler_task_and_recent_messages() -> None:
    req = build_eot_request(
        [{"role": "user", "content": f"m{i}"} for i in range(7)],
        group_id="100",
    )

    assert req.task == "scheduler_eot"
    assert req.group_id == "100"
    assert req.requires_capabilities == ("chat", "json")
    assert "m6" in str(req.user_messages)
    assert "m0" not in str(req.user_messages)


async def test_eot_classifier_uses_api_call() -> None:
    async def fake_call(request):  # type: ignore[no-untyped-def]
        assert request.task == "scheduler_eot"
        return {"text": '{"probability":0.73,"reason":"该接"}'}

    decision = await EOTClassifier(timeout_ms=500).classify(
        [{"role": "user", "content": "bot?"}],
        group_id="100",
        api_call=fake_call,
    )

    assert decision.probability == pytest.approx(0.73)


def test_eot_cache_ttl_and_rate_limit() -> None:
    cache = EOTCache(ttl_s=10, min_interval_s=5)
    decision = parse_eot_output('{"probability":0.6}')

    cache.put("g", decision, now=100)

    assert cache.get("g", now=105) is decision
    assert cache.get("g", now=111) is None
    cache.put("g", decision, now=200)
    assert cache.can_call("g", now=203) is False
    assert cache.can_call("g", now=206) is True


def test_eot_cache_reserve_call_blocks_duplicate_prefetch() -> None:
    cache = EOTCache(ttl_s=10, min_interval_s=5)

    assert cache.reserve_call("g", now=100) is True
    assert cache.can_call("g", now=104) is False
    assert cache.reserve_call("g", now=104) is False
    assert cache.reserve_call("g", now=106) is True
