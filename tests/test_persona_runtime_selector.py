"""B3.1 — services/persona/runtime_selector.PersonaRuntimeSelector.

Per-turn v1↔v2 selector for the runtime cutover. The selector is a pure
synchronous function over (cfg, bundle, group_id); it never raises and never
performs IO. Eight scenarios are locked here:

1. flag off → v1
2. runtime_groups empty → v1
3. group listed + bundle ok → v2
4. group not listed → v1
5. private chat (group_id=None) → v1
6. bundle missing → v1 fallback (counter.v1_fallback)
7. compile error → v1 fallback (counter.last_error populated)
8. cancel-path D2 — wait_for(timeout=0) does not corrupt counter
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kernel.config import PersonaV2Config
from services.persona import (
    PersonaRuntimeBundle,
    PersonaRuntimeSelector,
    join_static_blocks,
    load_pending_freeze,
)
from services.persona.compiler import CompileResult
from services.persona.writer import PersonaDraftWriter
from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults

GROUP_ID = "993065015"


def _import_and_freeze(tmp_path: Path) -> tuple[Path, Path, PersonaRuntimeBundle]:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)
    writer.pending_freeze("fengxiaomeng")
    bundle = load_pending_freeze(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    assert bundle is not None and bundle.ok
    return persona_root, defaults, bundle


def _broken_bundle(bundle: PersonaRuntimeBundle) -> PersonaRuntimeBundle:
    """Return a bundle whose compile_result.ok is False (simulated)."""
    broken = CompileResult(
        persona_id=bundle.compile_result.persona_id,
        mode=bundle.compile_result.mode,
        prompt_blocks=(),
        ok=False,
        warnings=(),
        errors=("simulated_compile_error",),
    )
    return PersonaRuntimeBundle(
        persona_id=bundle.persona_id,
        schema_version=bundle.schema_version,
        source_sha256=bundle.source_sha256,
        compile_result=broken,
        pending_freeze_dir=bundle.pending_freeze_dir,
        warnings=bundle.warnings,
        errors=("simulated_compile_error",),
    )


def test_flag_off_returns_v1(tmp_path: Path) -> None:
    _, _, bundle = _import_and_freeze(tmp_path)
    cfg = PersonaV2Config(
        runtime_consume=False,
        runtime_groups=[GROUP_ID],
        persona_id="fengxiaomeng",
    )
    selector = PersonaRuntimeSelector(
        cfg=cfg, bundle=bundle, v2_static_text=join_static_blocks(bundle)
    )
    selection = selector.resolve_for_group(GROUP_ID)

    assert selection.use_v2 is False
    assert selection.fallback_reason == "flag_off"
    assert selector.counter.v1_default == 1
    assert selector.counter.v2 == 0
    assert selector.counter.v1_fallback == 0


def test_runtime_groups_empty_returns_v1(tmp_path: Path) -> None:
    _, _, bundle = _import_and_freeze(tmp_path)
    cfg = PersonaV2Config(
        runtime_consume=True,
        runtime_groups=[],
        persona_id="fengxiaomeng",
    )
    selector = PersonaRuntimeSelector(
        cfg=cfg, bundle=bundle, v2_static_text=join_static_blocks(bundle)
    )
    selection = selector.resolve_for_group(GROUP_ID)

    assert selection.use_v2 is False
    assert selection.fallback_reason == "group_not_listed"
    assert selector.counter.v1_default == 1


def test_group_match_returns_v2(tmp_path: Path) -> None:
    _, _, bundle = _import_and_freeze(tmp_path)
    v2_text = join_static_blocks(bundle)
    cfg = PersonaV2Config(
        runtime_consume=True,
        runtime_groups=[GROUP_ID],
        persona_id="fengxiaomeng",
    )
    selector = PersonaRuntimeSelector(
        cfg=cfg, bundle=bundle, v2_static_text=v2_text
    )
    selection = selector.resolve_for_group(GROUP_ID)

    assert selection.use_v2 is True
    assert selection.fallback_reason == ""
    assert selection.v2_static_text == v2_text
    assert v2_text  # v2 static text非空
    assert selector.counter.v2 == 1
    assert selector.counter.last_reason == "v2"


def test_group_not_match_returns_v1(tmp_path: Path) -> None:
    _, _, bundle = _import_and_freeze(tmp_path)
    cfg = PersonaV2Config(
        runtime_consume=True,
        runtime_groups=[GROUP_ID],
        persona_id="fengxiaomeng",
    )
    selector = PersonaRuntimeSelector(
        cfg=cfg, bundle=bundle, v2_static_text=join_static_blocks(bundle)
    )
    selection = selector.resolve_for_group("111")

    assert selection.use_v2 is False
    assert selection.fallback_reason == "group_not_listed"
    assert selector.counter.v1_default == 1


def test_private_chat_returns_v1(tmp_path: Path) -> None:
    _, _, bundle = _import_and_freeze(tmp_path)
    cfg = PersonaV2Config(
        runtime_consume=True,
        runtime_groups=[GROUP_ID],
        persona_id="fengxiaomeng",
    )
    selector = PersonaRuntimeSelector(
        cfg=cfg, bundle=bundle, v2_static_text=join_static_blocks(bundle)
    )
    selection = selector.resolve_for_group(None)

    assert selection.use_v2 is False
    assert selection.fallback_reason == "private_chat"
    assert selector.counter.v1_default == 1


def test_bundle_missing_returns_v1_fallback() -> None:
    cfg = PersonaV2Config(
        runtime_consume=True,
        runtime_groups=[GROUP_ID],
        persona_id="fengxiaomeng",
    )
    selector = PersonaRuntimeSelector(cfg=cfg, bundle=None, v2_static_text="")
    selection = selector.resolve_for_group(GROUP_ID)

    assert selection.use_v2 is False
    assert selection.fallback_reason == "bundle_missing"
    assert selector.counter.v1_fallback == 1
    assert selector.counter.v2 == 0


def test_compile_error_returns_v1_fallback(tmp_path: Path) -> None:
    _, _, bundle = _import_and_freeze(tmp_path)
    broken = _broken_bundle(bundle)
    cfg = PersonaV2Config(
        runtime_consume=True,
        runtime_groups=[GROUP_ID],
        persona_id="fengxiaomeng",
    )
    selector = PersonaRuntimeSelector(cfg=cfg, bundle=broken, v2_static_text="")
    selection = selector.resolve_for_group(GROUP_ID)

    assert selection.use_v2 is False
    assert selection.fallback_reason == "compile_error"
    assert selector.counter.v1_fallback == 1
    assert "simulated_compile_error" in selector.counter.last_error


@pytest.mark.asyncio
async def test_resolve_cancel_does_not_corrupt(tmp_path: Path) -> None:
    _, _, bundle = _import_and_freeze(tmp_path)
    cfg = PersonaV2Config(
        runtime_consume=True,
        runtime_groups=[GROUP_ID],
        persona_id="fengxiaomeng",
    )
    selector = PersonaRuntimeSelector(
        cfg=cfg, bundle=bundle, v2_static_text=join_static_blocks(bundle)
    )

    async def _wrap() -> None:
        # resolve_for_group is sync but we wrap so wait_for can cancel.
        selector.resolve_for_group(GROUP_ID)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_wrap(), timeout=0)

    # Counter must remain pristine — cancel happened before any resolve mutation.
    assert selector.counter.v2 == 0
    assert selector.counter.v1_default == 0
    assert selector.counter.v1_fallback == 0
    assert selector.counter.last_reason == ""
    assert selector.counter.last_error == ""
