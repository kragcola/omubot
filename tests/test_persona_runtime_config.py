"""Persona v2 config schema — only ``persona_id`` survives the v2-only cutover."""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.config import BotConfig, PersonaV2Config, load_config


def test_default_persona_v2_uses_default_id() -> None:
    cfg = BotConfig()
    assert cfg.persona_v2.persona_id == "default"


def test_persona_v2_from_dict_overrides_id() -> None:
    sub = PersonaV2Config.model_validate({"persona_id": "fengxiaomeng-v2"})
    assert sub.persona_id == "fengxiaomeng-v2"


def test_load_config_without_persona_v2_section_uses_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        '[llm]\nbase_url = "https://example.com"\n', encoding="utf-8"
    )

    cfg = load_config(config_path=str(toml_path))
    assert cfg.persona_v2.persona_id == "default"


def test_load_config_with_persona_v2_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        "\n".join(
            [
                "[persona_v2]",
                'persona_id = "fengxiaomeng-v2"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(toml_path))
    assert cfg.persona_v2.persona_id == "fengxiaomeng-v2"
