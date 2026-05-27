from __future__ import annotations

from pathlib import Path

import pytest

from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot, PersonaRuntime
from services.persona.writer import PersonaDraftWriter


@pytest.fixture
def short_term() -> ShortTermMemory:
    return ShortTermMemory()


@pytest.fixture
def group_timeline() -> GroupTimeline:
    return GroupTimeline()


@pytest.fixture
def identity_snapshot() -> IdentitySnapshot:
    """Lightweight identity for unit tests that need ``ctx.identity``."""
    return IdentitySnapshot(
        id="test",
        name="Bot",
        personality="I am a bot.",
        proactive="Proactive rules.",
    )


def _build_persona_runtime(tmp_path: Path) -> PersonaRuntime:
    from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults

    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)
    writer.pending_freeze("fengxiaomeng")
    runtime = PersonaRuntime(persona_root=persona_root, defaults_dir=defaults)
    runtime.load("fengxiaomeng")
    runtime.bind_bot_self_id("999")
    return runtime


@pytest.fixture
def persona_runtime(tmp_path: Path) -> PersonaRuntime:
    """Real PersonaRuntime fed by a freshly imported+frozen minimal persona."""
    return _build_persona_runtime(tmp_path)
