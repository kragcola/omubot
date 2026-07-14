"""Ownership contract for the Chat-created ScheduleGenerator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bootstrap.chat_runtime import ChatRuntimeAssembly, create_chat_runtime_assembly


class _ScheduleGeneratorProbe:
    def __init__(self) -> None:
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_chat_runtime_does_not_claim_schedule_generator_close_ownership() -> None:
    generator = _ScheduleGeneratorProbe()
    ctx = SimpleNamespace(schedule_gen=None)

    async def builder(assembly: ChatRuntimeAssembly) -> None:
        assembly.publish("schedule_gen", generator)

    assembly = create_chat_runtime_assembly(ctx, builder)
    await assembly.start()
    await assembly.close()
    await assembly.close()

    assert generator.stop_calls == 0
