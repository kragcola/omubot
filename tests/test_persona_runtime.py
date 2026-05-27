"""C1 — PersonaRuntime singleton."""

from __future__ import annotations

from pathlib import Path

from services.persona import IdentitySnapshot, PersonaRuntime
from services.persona.writer import PersonaDraftWriter
from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults


def _import_and_freeze(tmp_path: Path) -> tuple[Path, Path, PersonaDraftWriter]:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)
    writer.pending_freeze("fengxiaomeng")
    return persona_root, defaults, writer


def _runtime(persona_root: Path, defaults: Path) -> PersonaRuntime:
    return PersonaRuntime(persona_root=persona_root, defaults_dir=defaults)


def test_load_happy_path_populates_static_text(tmp_path: Path) -> None:
    persona_root, defaults, _ = _import_and_freeze(tmp_path)
    runtime = _runtime(persona_root, defaults)

    assert runtime.load("fengxiaomeng") is True
    assert runtime.loaded is True
    text = runtime.static_text
    assert text
    assert "凤笑梦" in text or "笑梦" in text
    assert "{bot_self_id}" in text
    assert runtime.last_error == ""


def test_load_returns_false_when_pending_freeze_missing(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    runtime = _runtime(persona_root, defaults)

    assert runtime.load("fengxiaomeng") is False
    assert runtime.loaded is False
    assert runtime.last_error == "pending_freeze_missing"
    assert runtime.static_text == ""


def test_bind_bot_self_id_substitutes_placeholder(tmp_path: Path) -> None:
    persona_root, defaults, _ = _import_and_freeze(tmp_path)
    runtime = _runtime(persona_root, defaults)
    runtime.load("fengxiaomeng")

    raw_text = runtime.static_text
    assert "{bot_self_id}" in raw_text

    runtime.bind_bot_self_id("384801062")
    assert runtime.bot_self_id == "384801062"
    bound_text = runtime.static_text
    assert "384801062" in bound_text
    assert "{bot_self_id}" not in bound_text

    runtime.bind_bot_self_id("")
    rebound_text = runtime.static_text
    assert "{bot_self_id}" in rebound_text


def test_swap_bundle_atomic_on_compile_failure(tmp_path: Path) -> None:
    persona_root, defaults, writer = _import_and_freeze(tmp_path)
    runtime = _runtime(persona_root, defaults)
    runtime.load("fengxiaomeng")
    good_text = runtime.static_text
    assert good_text

    persona_yaml = writer.pending_freeze_dir("fengxiaomeng") / "persona.yaml"
    persona_yaml.write_text("not: valid: yaml: [\n", encoding="utf-8")

    assert runtime.swap_bundle("fengxiaomeng") is False
    assert runtime.static_text == good_text
    assert runtime.last_error
    assert runtime.loaded is True


def test_identity_snapshot_returns_canonical_name(tmp_path: Path) -> None:
    persona_root, defaults, _ = _import_and_freeze(tmp_path)
    runtime = _runtime(persona_root, defaults)
    runtime.load("fengxiaomeng")

    snap = runtime.identity_snapshot()
    assert isinstance(snap, IdentitySnapshot)
    assert snap.id == "fengxiaomeng-v2"
    assert "凤笑梦" in snap.name


def test_identity_snapshot_default_when_unloaded(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    runtime = _runtime(persona_root, defaults)

    snap = runtime.identity_snapshot()
    assert snap.id == "default"
    assert snap.name == "bot"


def test_block_for_returns_compiled_block(tmp_path: Path) -> None:
    persona_root, defaults, _ = _import_and_freeze(tmp_path)
    runtime = _runtime(persona_root, defaults)
    runtime.load("fengxiaomeng")

    block = runtime.block_for("core.identity")
    assert block is not None
    assert block.module_id == "core.identity"
    assert block.text

    assert runtime.block_for("does.not.exist") is None


def test_group_profile_text_uses_resolver_when_provided(tmp_path: Path) -> None:
    class _Profile:
        def __init__(self) -> None:
            self.reply_style = "playful"
            self.custom_prompt = "多接梗，少说教。"

    def _resolver(gid: int) -> _Profile | None:
        return _Profile() if gid == 12345 else None

    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)
    writer.pending_freeze("fengxiaomeng")

    runtime = PersonaRuntime(
        persona_root=persona_root,
        defaults_dir=defaults,
        group_config_resolver=_resolver,
    )
    runtime.load("fengxiaomeng")

    text = runtime.group_profile_text(12345)
    assert "群聊回复偏好" in text
    assert "多接梗" in text

    assert runtime.group_profile_text(99999) == ""
    assert runtime.group_profile_text(None) == ""


def test_static_text_empty_when_unloaded(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    runtime = _runtime(persona_root, defaults)

    assert runtime.static_text == ""
    assert runtime.bundle is None


def test_swap_bundle_succeeds_on_clean_reload(tmp_path: Path) -> None:
    persona_root, defaults, writer = _import_and_freeze(tmp_path)
    runtime = _runtime(persona_root, defaults)
    runtime.load("fengxiaomeng")
    runtime.bind_bot_self_id("99999")
    first_text = runtime.static_text

    writer.pending_freeze("fengxiaomeng")
    assert runtime.swap_bundle("fengxiaomeng") is True
    assert runtime.bot_self_id == "99999"
    assert "99999" in runtime.static_text
    assert runtime.static_text == first_text
