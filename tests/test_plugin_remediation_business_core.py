"""RED regressions for core plugin business remediation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plugins.affection.engine import AffectionEngine
from plugins.affection.store import AffectionStore
from plugins.echo import EchoTracker
from plugins.food.plugin import FoodPlugin
from services.memory.card_store import CardStore


@pytest.mark.asyncio
async def test_affection_zero_increment_is_a_valid_no_score_mode(tmp_path: Path) -> None:
    store = AffectionStore(storage_dir=str(tmp_path / "affection"))
    await store.startup()
    engine = AffectionEngine(store, score_increment=0.0, daily_cap=20.0)

    profile = None
    zero_division = None
    try:
        profile = await engine.record_interaction("123")
    except ZeroDivisionError as exc:
        zero_division = exc

    assert zero_division is None, "score_increment=0 must not divide by zero"
    assert profile is not None
    assert profile.score == 0.0
    assert profile.total_interactions == 1


@pytest.mark.asyncio
async def test_food_not_spicy_excludes_spicy_candidates(tmp_path: Path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = FoodPlugin()
        plugin._ctx = SimpleNamespace(
            card_store=store,
            llm_client=_FailingLLM(),
            tool_registry=None,
        )
        plugin._search_enabled = False
        plugin._food_library_max_items = 40
        plugin._food_library = [
            _food("麻辣香锅", "麻辣"),
            _food("辣子鸡", "香辣"),
            _food("水煮鱼", "麻辣"),
            _food("清粥", "清淡"),
            _food("鸡蛋羹", "清淡"),
        ]
        plugin._tutorial_shown.add("123")
        sent: list[str] = []

        async def capture_reply(cmd_ctx: Any, text: str) -> None:
            del cmd_ctx
            sent.append(text)

        plugin._send_reply = capture_reply  # type: ignore[method-assign]
        cmd_ctx = SimpleNamespace(
            user_id="123",
            args="换一个不辣的",
            is_private=False,
            group_id="456",
            event=SimpleNamespace(message_id=1),
            bot=SimpleNamespace(),
        )

        await plugin._handle_eat(cmd_ctx)

        assert sent[-1] in {"清粥", "鸡蛋羹"}, "不辣 must exclude every spicy candidate"
    finally:
        await store.close()


def _food(name: str, taste: str) -> dict[str, str]:
    return {
        "name": name,
        "brand": "",
        "taste": taste,
        "category": "主食",
        "staple": "米面",
        "cooking_method": "煮",
        "temperature": "热",
        "available_time": "不限",
    }


class _FailingLLM:
    async def _call(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        del args, kwargs
        raise RuntimeError("service busy")


@pytest.mark.parametrize(
    ("initial_times", "rollover_times"),
    [
        ([100.0, 101.0], [500.0, 501.0, 502.0]),
        ([100.0, 101.0, 102.0], [103.0, 104.0, 105.0]),
    ],
    ids=["expired-window", "completed-chain"],
)
def test_echo_same_text_rolls_over_to_a_fresh_chain(
    initial_times: list[float],
    rollover_times: list[float],
) -> None:
    tracker = EchoTracker(rand=lambda: 0.5)

    initial_results = [tracker.process("g1", "same", now) for now in initial_times]
    if len(initial_times) == 3:
        assert initial_results[-1] == "same"

    rollover_results = [tracker.process("g1", "same", now) for now in rollover_times]

    assert rollover_results == [None, None, "same"]
