"""B2.1 + B2.2 — services/persona/shadow.ShadowCompareEngine.

Shadow compare runs after on_bot_connect; flag-gated by
BotConfig.persona_v2.shadow_compare. The engine reads v1 ``static_block`` text
plus v2 ``_pending_freeze/<id>/``, computes a diff, and appends one JSON line
to ``storage/persona_shadow_diff.log``. It never raises.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kernel.config import PersonaV2Config
from services.identity import Identity
from services.persona import ShadowCompareEngine, ShadowDiffReport
from services.persona.writer import PersonaDraftWriter
from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults


def _v1_identity() -> Identity:
    return Identity(
        id="fengxiaomeng",
        name="凤笑梦",
        personality=(
            "你是凤笑梦——Wonderlands×Showtime 的成员。"
            "你不是 AI 或语言模型，你是活生生的凤笑梦，"
            "一个相信舞台可以改变人脸上的表情并愿意亲手推动它发生的女孩。"
        ),
        proactive="核心原则：像真人一样判断要不要说话。",
    )


def _v1_static_text(identity: Identity, instruction: str = "") -> str:
    parts = [identity.personality]
    if instruction:
        parts.append(instruction)
    if identity.proactive:
        parts.append(identity.proactive)
    return "\n\n".join(parts)


def _import_and_freeze(tmp_path: Path) -> tuple[Path, Path]:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)
    writer.pending_freeze("fengxiaomeng")
    return persona_root, defaults


@pytest.mark.asyncio
async def test_run_once_flag_off_returns_none_no_log(tmp_path: Path) -> None:
    persona_root, defaults = _import_and_freeze(tmp_path)
    log_path = tmp_path / "shadow.log"
    cfg = PersonaV2Config(shadow_compare=False, persona_id="fengxiaomeng")
    identity = _v1_identity()

    engine = ShadowCompareEngine(
        cfg=cfg,
        v1_static_text=_v1_static_text(identity),
        v1_identity=identity,
        log_path=log_path,
        persona_root=persona_root,
        defaults_dir=defaults,
    )
    report = await engine.run_once()

    assert report is None
    assert not log_path.exists()
    assert engine.counter.ok == 0
    assert engine.counter.divergent == 0
    assert engine.counter.error == 0


@pytest.mark.asyncio
async def test_run_once_happy_path_writes_jsonl(tmp_path: Path) -> None:
    persona_root, defaults = _import_and_freeze(tmp_path)
    log_path = tmp_path / "shadow.log"
    cfg = PersonaV2Config(shadow_compare=True, persona_id="fengxiaomeng")
    identity = _v1_identity()

    engine = ShadowCompareEngine(
        cfg=cfg,
        v1_static_text=_v1_static_text(identity),
        v1_identity=identity,
        log_path=log_path,
        persona_root=persona_root,
        defaults_dir=defaults,
    )
    report = await engine.run_once()

    assert isinstance(report, ShadowDiffReport)
    assert report.persona_id == "fengxiaomeng-v2"
    assert report.v1_signature
    assert report.compile_signature
    assert report.errors == ()
    assert log_path.exists()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["persona_id"] == "fengxiaomeng-v2"
    assert payload["ok"] is True
    assert "divergent_axes" in payload
    assert payload["v1_text_len"] > 0
    assert payload["v2_text_len"] > 0


@pytest.mark.asyncio
async def test_run_once_bundle_missing_increments_error(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    log_path = tmp_path / "shadow.log"

    cfg = PersonaV2Config(shadow_compare=True, persona_id="fengxiaomeng")
    identity = _v1_identity()
    engine = ShadowCompareEngine(
        cfg=cfg,
        v1_static_text=_v1_static_text(identity),
        v1_identity=identity,
        log_path=log_path,
        persona_root=persona_root,
        defaults_dir=defaults,
    )

    report = await engine.run_once()
    assert isinstance(report, ShadowDiffReport)
    assert report.errors == ("pending_freeze_dir_missing",)
    assert engine.counter.error == 1
    assert engine.counter.ok == 0
    assert engine.counter.last_error == "pending_freeze_dir_missing"

    payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["ok"] is False
    assert payload["errors"] == ["pending_freeze_dir_missing"]


@pytest.mark.asyncio
async def test_run_once_divergent_v1_only_admins_listed(tmp_path: Path) -> None:
    persona_root, defaults = _import_and_freeze(tmp_path)
    log_path = tmp_path / "shadow.log"
    cfg = PersonaV2Config(shadow_compare=True, persona_id="fengxiaomeng")
    identity = _v1_identity()

    engine = ShadowCompareEngine(
        cfg=cfg,
        v1_static_text=_v1_static_text(identity),
        v1_identity=identity,
        v1_admins={"1416930401": "工丿囗"},
        v1_proactive=identity.proactive or "",
        log_path=log_path,
        persona_root=persona_root,
        defaults_dir=defaults,
    )
    report = await engine.run_once()
    assert isinstance(report, ShadowDiffReport)
    assert "admins" in report.divergent_axes
    assert engine.counter.divergent == 1
    assert engine.counter.error == 0


@pytest.mark.asyncio
async def test_run_once_cancel_does_not_corrupt(tmp_path: Path) -> None:
    persona_root, defaults = _import_and_freeze(tmp_path)
    log_path = tmp_path / "shadow.log"
    cfg = PersonaV2Config(shadow_compare=True, persona_id="fengxiaomeng")
    identity = _v1_identity()

    engine = ShadowCompareEngine(
        cfg=cfg,
        v1_static_text=_v1_static_text(identity),
        v1_identity=identity,
        log_path=log_path,
        persona_root=persona_root,
        defaults_dir=defaults,
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(engine.run_once(), timeout=0)

    # No log row, no counter change, no half-written state.
    assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""
    assert engine.counter.ok == 0
    assert engine.counter.divergent == 0
    assert engine.counter.error == 0
    assert engine.counter.last_error == ""
    assert engine.counter.last_run_at == ""
