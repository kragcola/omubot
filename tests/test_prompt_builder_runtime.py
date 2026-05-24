"""B3.2 — services/llm/prompt_builder.PromptBuilder runtime integration.

Locks the v1↔v2 turn-level routing through the selector:

1. selector unset → resolve_static_block returns _static_block (v1)
2. selector returns use_v2=False → returns _static_block (v1)
3. selector returns use_v2=True → returns v2 text wrapped in {"type":"text"}
4. build_blocks() first system block respects selector decision
"""

from __future__ import annotations

from typing import Any

import pytest

from kernel.config import PersonaV2Config
from services.identity import Identity
from services.llm.prompt_builder import PromptBuilder
from services.persona import PersonaRuntimeBundle
from services.persona.compiler import CompileResult
from services.persona.runtime_selector import PersonaRuntimeSelector


def _make_identity() -> Identity:
    return Identity(
        id="test", name="test", personality="V1 PERSONALITY TEXT", proactive=""
    )


def _make_builder() -> PromptBuilder:
    pb = PromptBuilder(instruction="V1 INSTRUCTION")
    pb.build_static(_make_identity(), bot_self_id="123")
    return pb


def _make_v2_selector(*, group_id: str, v2_text: str = "V2 STATIC TEXT") -> PersonaRuntimeSelector:
    cfg = PersonaV2Config(
        runtime_consume=True,
        runtime_groups=[group_id],
        persona_id="test",
    )
    bundle = PersonaRuntimeBundle(
        persona_id="test-v2",
        schema_version="1.0",
        source_sha256="abc",
        compile_result=CompileResult(ok=True, mode="runtime", persona_id="test-v2"),
        pending_freeze_dir=__import__("pathlib").Path("/tmp/notreal"),
    )
    return PersonaRuntimeSelector(cfg=cfg, bundle=bundle, v2_static_text=v2_text)


def test_resolve_static_block_no_selector_returns_v1() -> None:
    pb = _make_builder()
    block = pb.resolve_static_block(group_id="993065015")
    assert block["type"] == "text"
    assert "V1 PERSONALITY TEXT" in block["text"]


def test_resolve_static_block_selector_off_returns_v1() -> None:
    pb = _make_builder()
    cfg_off = PersonaV2Config(runtime_consume=False)
    selector = PersonaRuntimeSelector(cfg=cfg_off, bundle=None)
    pb.set_runtime_selector(selector)

    block = pb.resolve_static_block(group_id="993065015")
    assert "V1 PERSONALITY TEXT" in block["text"]
    assert selector.counter.v1_default == 1


def test_resolve_static_block_use_v2_returns_v2_text() -> None:
    pb = _make_builder()
    selector = _make_v2_selector(group_id="993065015", v2_text="V2 STATIC TEXT")
    pb.set_runtime_selector(selector)

    block = pb.resolve_static_block(group_id="993065015")
    assert block == {"type": "text", "text": "V2 STATIC TEXT"}
    assert selector.counter.v2 == 1


def test_resolve_static_block_v2_with_unmatched_group_returns_v1() -> None:
    pb = _make_builder()
    selector = _make_v2_selector(group_id="993065015")
    pb.set_runtime_selector(selector)

    block = pb.resolve_static_block(group_id="999")
    assert "V1 PERSONALITY TEXT" in block["text"]
    assert selector.counter.v1_default == 1


def test_resolve_static_block_private_chat_returns_v1() -> None:
    pb = _make_builder()
    selector = _make_v2_selector(group_id="993065015")
    pb.set_runtime_selector(selector)

    block = pb.resolve_static_block(group_id=None)
    assert "V1 PERSONALITY TEXT" in block["text"]
    assert selector.counter.v1_default == 1


def test_set_runtime_selector_none_disables_v2() -> None:
    pb = _make_builder()
    selector = _make_v2_selector(group_id="993065015")
    pb.set_runtime_selector(selector)
    block_v2 = pb.resolve_static_block(group_id="993065015")
    assert block_v2["text"] == "V2 STATIC TEXT"

    # Clear selector — turn falls back to v1
    pb.set_runtime_selector(None)
    block_v1 = pb.resolve_static_block(group_id="993065015")
    assert "V1 PERSONALITY TEXT" in block_v1["text"]


@pytest.mark.asyncio
async def test_build_blocks_first_block_uses_resolve(monkeypatch: Any) -> None:
    pb = _make_builder()
    selector = _make_v2_selector(group_id="993065015", v2_text="V2 STATIC TEXT")
    pb.set_runtime_selector(selector)

    blocks = await pb.build_blocks(
        user_id="u1",
        group_id="993065015",
        include_state_board=False,
    )
    assert blocks, "build_blocks returned empty list"
    assert blocks[0] == {"type": "text", "text": "V2 STATIC TEXT"}
