from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from services.humanization import REGISTER_LABEL_SLOT, RegisterClassifier, create_humanization_state_bus
from services.system_module import Scope


def _scope(session_id: str = "group_100", *, group_id: str = "100", turn_id: str = "t1") -> Scope:
    return Scope(session_id=session_id, group_id=group_id, user_id="u1", turn_id=turn_id)


class _FakeLLM:
    def __init__(self, payload: dict[str, Any] | str) -> None:
        self.payload = payload
        self.requests: list[Any] = []

    async def _call(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)
        return {"text": text}


class _FailingLLM:
    async def _call(self, request: Any) -> dict[str, Any]:
        raise RuntimeError("boom")


class _SlowLLM:
    async def _call(self, request: Any) -> dict[str, Any]:
        await asyncio.sleep(60)
        return {"text": '{"label":"playful","confidence":1.0}'}


@pytest.mark.asyncio
async def test_register_classifier_happy_path_writes_runtime_state() -> None:
    llm = _FakeLLM({
        "label": "playful",
        "confidence": 0.82,
        "reason": "大家在接梗",
        "evidence": "这下有节目了",
    })
    classifier = RegisterClassifier(llm)
    bus = create_humanization_state_bus()

    decision = await classifier.classify_and_write(
        [{"speaker": "alice", "content_text": "这下有节目了"}],
        bus=bus,
        scope=_scope(),
    )

    snapshot = bus.get(REGISTER_LABEL_SLOT, scope=_scope())
    assert decision.label == "playful"
    assert decision.confidence == 0.82
    assert snapshot is not None
    assert snapshot.value["label"] == "playful"
    assert snapshot.value["window_size"] == 1
    assert llm.requests[0].task == "thinker"


@pytest.mark.asyncio
async def test_register_classifier_llm_failure_defaults_neutral() -> None:
    classifier = RegisterClassifier(_FailingLLM())

    decision = await classifier.classify([{"role": "user", "content": "先别闹，认真说"}])

    assert decision.label == "neutral"
    assert decision.confidence == 0.0
    assert decision.window_size == 1


@pytest.mark.asyncio
async def test_register_classifier_rejects_invalid_json_and_label() -> None:
    classifier = RegisterClassifier(_FakeLLM('```json\n{"label":"feral","confidence":0.7}\n```'))

    decision = await classifier.classify([{"speaker": "bob", "content_text": "坏了"}])

    assert decision.label == "neutral"
    assert decision.confidence == 0.7


@pytest.mark.asyncio
async def test_register_classifier_isolates_multiple_sessions() -> None:
    bus = create_humanization_state_bus()
    first = RegisterClassifier(_FakeLLM({"label": "quiet", "confidence": 0.6}))
    second = RegisterClassifier(_FakeLLM({"label": "serious", "confidence": 0.9}))

    await first.classify_and_write(
        [{"speaker": "a", "content_text": "有点累，轻一点"}],
        bus=bus,
        scope=_scope("group_100", group_id="100"),
    )
    await second.classify_and_write(
        [{"speaker": "b", "content_text": "这个问题要认真处理"}],
        bus=bus,
        scope=_scope("group_200", group_id="200"),
    )

    assert bus.get(REGISTER_LABEL_SLOT, scope=_scope("group_100", group_id="100")).value["label"] == "quiet"  # type: ignore[union-attr]
    assert bus.get(REGISTER_LABEL_SLOT, scope=_scope("group_200", group_id="200")).value["label"] == "serious"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_register_classifier_cancel_path_does_not_dirty_write() -> None:
    classifier = RegisterClassifier(_SlowLLM())
    bus = create_humanization_state_bus()
    task = asyncio.create_task(
        classifier.classify_and_write(
            [{"speaker": "alice", "content_text": "快接一下这个梗"}],
            bus=bus,
            scope=_scope(),
        )
    )

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert bus.get(REGISTER_LABEL_SLOT, scope=_scope()) is None


@pytest.mark.asyncio
async def test_register_classifier_clamps_confidence_and_uses_five_message_window() -> None:
    classifier = RegisterClassifier(_FakeLLM({"label": "affectionate", "confidence": 2.5}))

    decision = await classifier.classify([
        {"speaker": "u1", "content_text": "0"},
        {"speaker": "u1", "content_text": "1"},
        {"speaker": "u1", "content_text": "2"},
        {"speaker": "u1", "content_text": "3"},
        {"speaker": "u1", "content_text": "4"},
        {"speaker": "u1", "content_text": "5"},
    ])

    assert decision.label == "affectionate"
    assert decision.confidence == 1.0
    assert decision.window_size == 5
