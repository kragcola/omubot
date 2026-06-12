from __future__ import annotations

import pytest

from kernel.config import ArbiterConfig
from services.llm.arbiter import ArbiterClient, PendingMessage
from services.llm.usage import UsageTracker


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.status = 200

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _ResponseContext:
    def __init__(self, response: object) -> None:
        self._response = response

    async def __aenter__(self) -> object:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _Session:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, *, json: object = None, headers: object = None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        response = self._responses.pop(0)
        return _ResponseContext(response)


def _config(**kwargs: object) -> ArbiterConfig:
    cfg = ArbiterConfig(enabled=True)
    payload = cfg.model_dump(mode="python")
    payload.update(kwargs)
    return ArbiterConfig.model_validate(payload)


def _payload(content: str) -> dict[str, object]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }


@pytest.mark.asyncio
async def test_arbiter_completeness_returns_result(tmp_path) -> None:
    tracker = UsageTracker(db_path=str(tmp_path / "usage.db"))
    await tracker.init()
    session = _Session([_Response(_payload('{"complete": true, "confidence": 0.91}'))])
    client = ArbiterClient(_config(), session, usage_tracker=tracker)  # type: ignore[arg-type]

    result = await client.judge_completeness(
        [PendingMessage(content="别睡", user_id="42", timestamp=1.0)],
        user_id="42",
        group_id="100",
    )

    assert result.complete is True
    assert result.confidence == pytest.approx(0.91)
    rows = await tracker.query_raw("SELECT call_type, group_id, error FROM llm_calls")
    assert rows[0]["call_type"] == "arbiter"
    assert rows[0]["group_id"] == "100"
    assert rows[0]["error"] is None
    await tracker.close()


@pytest.mark.asyncio
async def test_arbiter_completeness_timeout_fallback() -> None:
    class _TimeoutSession:
        def post(self, url: str, *, json: object = None, headers: object = None):
            del url, json, headers

            class _Ctx:
                async def __aenter__(self):
                    raise TimeoutError

                async def __aexit__(self, exc_type, exc, tb) -> bool:
                    return False

            return _Ctx()

    client = ArbiterClient(_config(timeout_ms=100), _TimeoutSession())  # type: ignore[arg-type]
    result = await client.judge_completeness(
        [PendingMessage(content="别睡", user_id="42", timestamp=1.0)],
        user_id="42",
        group_id="100",
    )
    assert result.fallback is True
    assert result.complete is True


@pytest.mark.asyncio
async def test_arbiter_completeness_invalid_json_fallback() -> None:
    session = _Session([_Response(_payload("not json"))])
    client = ArbiterClient(_config(), session)  # type: ignore[arg-type]

    result = await client.judge_completeness(
        [PendingMessage(content="别睡", user_id="42", timestamp=1.0)],
        user_id="42",
        group_id="100",
    )

    assert result.fallback is True
    assert result.complete is True


@pytest.mark.asyncio
async def test_arbiter_interruption_returns_result() -> None:
    session = _Session([_Response(_payload('{"action": "abort_unsent", "reason": "new info"}'))])
    client = ArbiterClient(_config(), session)  # type: ignore[arg-type]

    result = await client.judge_interruption(
        already_sent=["好呀"],
        unsent=["再说一句"],
        new_messages=["不是这个"],
        user_id="42",
        group_id="100",
    )

    assert result.action == "abort_unsent"
    assert result.reason == "new info"
    assert result.fallback is False


def test_max_tokens_fits_reason_json() -> None:
    """Regression: _MAX_TOKENS must leave room to close the JSON object even when
    the model writes a free-text "reason"/"correction_type" field. 48 truncated
    mid-string (~3.4% invalid_json), wasting the call and DM-spamming the admin."""
    from services.llm.arbiter import _MAX_TOKENS

    assert _MAX_TOKENS >= 96


def test_interruption_prompt_covers_repeated_calls() -> None:
    """Regression: the interruption prompt must instruct abort_unsent for a user
    repeatedly @-ing / re-calling the bot (the three-"emu" defect), so the burst
    folds into one unified reply instead of per-message greeting echoes."""
    from services.llm.arbiter import _INTERRUPTION_SYSTEM_PROMPT

    assert "abort_unsent" in _INTERRUPTION_SYSTEM_PROMPT
    assert "重复呼叫" in _INTERRUPTION_SYSTEM_PROMPT
    assert "统一回复" in _INTERRUPTION_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_arbiter_correction_returns_result() -> None:
    session = _Session([_Response(_payload('{"needs_correction": true, "correction_type": "amend"}'))])
    client = ArbiterClient(_config(), session)  # type: ignore[arg-type]

    result = await client.judge_correction(
        bot_reply="不是这样",
        new_message="我是说来烤",
        user_id="42",
        group_id="100",
    )

    assert result.needs_correction is True
    assert result.correction_type == "amend"
    assert result.fallback is False


def test_arbiter_config_defaults() -> None:
    cfg = ArbiterConfig()
    assert cfg.enabled is False
    assert cfg.timeout_ms == 500
    assert cfg.completeness_confidence_threshold == pytest.approx(0.8)
    assert cfg.completeness_poll_interval_s == pytest.approx(0.3)
    assert cfg.completeness_max_wait_s == pytest.approx(5.0)
    assert cfg.interruption_enabled is True
    assert cfg.correction_enabled is True
    assert cfg.correction_window_s == pytest.approx(30.0)
    assert cfg.runtime_groups == []


@pytest.mark.asyncio
async def test_arbiter_disabled_skips_call() -> None:
    session = _Session([_Response(_payload('{"complete": false, "confidence": 0.1}'))])
    client = ArbiterClient(ArbiterConfig(), session)  # type: ignore[arg-type]

    completeness = await client.judge_completeness(
        [PendingMessage(content="别睡", user_id="42", timestamp=1.0)],
        user_id="42",
        group_id="100",
    )
    interruption = await client.judge_interruption(
        already_sent=["a"],
        unsent=["b"],
        new_messages=["c"],
        user_id="42",
        group_id="100",
    )
    correction = await client.judge_correction(
        bot_reply="reply",
        new_message="msg",
        user_id="42",
        group_id="100",
    )

    assert completeness.fallback is True
    assert interruption.fallback is True
    assert interruption.action == "continue"
    assert correction.fallback is True
    assert session.calls == []
