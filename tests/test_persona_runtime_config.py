"""B1.1 — Persona v2 runtime cutover flags default + load behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.config import BotConfig, PersonaV2Config, load_config


def test_default_persona_v2_flags_all_off() -> None:
    cfg = BotConfig()
    assert cfg.persona_v2.runtime_consume is False
    assert cfg.persona_v2.shadow_compare is False
    assert cfg.persona_v2.runtime_groups == []
    assert cfg.persona_v2.fallback_on_compile_error is True
    assert cfg.persona_v2.persona_id == "default"


def test_persona_v2_from_dict_overrides() -> None:
    sub = PersonaV2Config.model_validate(
        {
            "runtime_consume": True,
            "runtime_groups": ["111", "222"],
            "shadow_compare": True,
            "fallback_on_compile_error": False,
            "persona_id": "fengxiaomeng-v2",
        }
    )
    assert sub.runtime_consume is True
    assert sub.runtime_groups == ["111", "222"]
    assert sub.shadow_compare is True
    assert sub.fallback_on_compile_error is False
    assert sub.persona_id == "fengxiaomeng-v2"


def test_persona_v2_runtime_groups_coerce_int_to_string() -> None:
    sub = PersonaV2Config.model_validate({"runtime_groups": [111, 222]})
    assert sub.runtime_groups == ["111", "222"]


def test_persona_v2_runtime_groups_strip_blank_entries() -> None:
    sub = PersonaV2Config.model_validate({"runtime_groups": ["111", "  ", "", " 222 "]})
    assert sub.runtime_groups == ["111", "222"]


def test_load_config_without_persona_v2_section_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        '[llm]\nbase_url = "https://example.com"\n', encoding="utf-8"
    )

    cfg = load_config(config_path=str(toml_path))
    assert cfg.persona_v2.runtime_consume is False
    assert cfg.persona_v2.runtime_groups == []
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
                "runtime_consume = false",
                'runtime_groups = ["123456", "789012"]',
                "shadow_compare = true",
                "fallback_on_compile_error = true",
                'persona_id = "fengxiaomeng-v2"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(toml_path))
    assert cfg.persona_v2.runtime_consume is False
    assert cfg.persona_v2.runtime_groups == ["123456", "789012"]
    assert cfg.persona_v2.shadow_compare is True
    assert cfg.persona_v2.fallback_on_compile_error is True
    assert cfg.persona_v2.persona_id == "fengxiaomeng-v2"
