"""测试 config_loader：TOML 加载、环境变量覆盖、CLI 覆盖。"""

import json
from pathlib import Path

import pytest

from kernel.config import BotConfig, GroupConfig, GroupOverride, load_config

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _write_toml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_load_defaults_without_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 TOML 文件时应返回全默认值。"""
    # 确保 BOT_CONFIG_PATH 未设置，且默认 config.toml 不存在
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    cfg = load_config(config_path=None)

    assert isinstance(cfg, BotConfig)
    assert cfg.llm.base_url == "http://127.0.0.1:34567/v1"
    assert cfg.llm.api_key == "sk-placeholder"
    assert cfg.llm.model == "claude-sonnet-4-6"
    assert cfg.llm.max_tokens == 1024
    assert cfg.llm.api_format == "anthropic"
    assert cfg.llm.default_profile == "main"
    assert cfg.llm.resolve_profile("main").model == cfg.llm.model
    assert cfg.llm.context.max_context_tokens == 1_000_000
    assert cfg.compact.ratio == 0.7
    assert cfg.compact.compress_ratio == 0.5
    assert cfg.log.dir == "storage/logs"
    assert cfg.soul.dir == "config/soul"
    assert cfg.group.history_load_count == 30
    assert cfg.group.debounce_seconds == 5.0
    assert cfg.group.batch_size == 10
    assert cfg.scheduler.concurrency.global_llm_limit == 2
    assert cfg.scheduler.concurrency.first_segment_release is False
    assert cfg.reply_workflow.mode == "shadow"
    assert cfg.reply_workflow.semantic_force_threshold == 0.78
    assert cfg.reply_workflow.semantic_timeout_ms == 2200
    assert cfg.reply_workflow.semantic_max_chars == 48
    assert cfg.reply_workflow.directed_followup_window_s == 180.0
    assert cfg.reply_workflow.shadow_log_private is True
    assert cfg.reply_segmentation.max_segment_chars == 20
    assert cfg.reply_segmentation.max_send_segments == 0
    assert cfg.reply_segmentation.soft_max_send_segments == 0
    assert cfg.reply_segmentation.boundary_backend == "pysbd_hybrid"
    assert cfg.napcat.api_url == "http://localhost:29300"
    assert cfg.admins == {}


def test_load_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TOML 文件中的值应覆盖默认值。"""
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("NAPCAT_API_URL", raising=False)

    toml_file = tmp_path / "config.toml"
    _write_toml(
        toml_file,
        """
[admins]
"123456" = "管理员A"
"789012" = "管理员B"

[llm]
base_url = "http://custom-llm:8080/v1"
api_key = "sk-test-key"
model = "claude-opus-4"
max_tokens = 2048

[llm.context]
max_context_tokens = 100_000

[compact]
ratio = 0.5
compress_ratio = 0.3

[soul]
dir = "custom_soul"

[group]
history_load_count = 50
debounce_seconds = 3.0
batch_size = 5

[reply_segmentation]
max_segment_chars = 24
max_send_segments = 3
soft_max_send_segments = 9
soft_limit_notice = "先刹车一下☆"
boundary_backend = "local"
inter_segment_delay_s = 0.3

[scheduler.concurrency]
global_llm_limit = 4
first_segment_release = true

[reply_workflow]
mode = "semantic"
semantic_force_threshold = 0.82
semantic_timeout_ms = 500
semantic_max_chars = 42
directed_followup_window_s = 240.0
shadow_log_private = false

[napcat]
api_url = "http://napcat:29300"
""",
    )

    cfg = load_config(config_path=str(toml_file))

    assert cfg.llm.base_url == "http://custom-llm:8080/v1"
    assert cfg.llm.api_key == "sk-test-key"
    assert cfg.llm.model == "claude-opus-4"
    assert cfg.llm.max_tokens == 2048
    assert cfg.llm.context.max_context_tokens == 100_000
    assert cfg.compact.ratio == 0.5
    assert cfg.compact.compress_ratio == 0.3
    assert cfg.soul.dir == "custom_soul"
    assert cfg.group.history_load_count == 50
    assert cfg.group.debounce_seconds == 3.0
    assert cfg.group.batch_size == 5
    assert cfg.reply_segmentation.max_segment_chars == 24
    assert cfg.reply_segmentation.max_send_segments == 3
    assert cfg.reply_segmentation.soft_max_send_segments == 9
    assert cfg.reply_segmentation.soft_limit_notice == "先刹车一下☆"
    assert cfg.reply_segmentation.boundary_backend == "local"
    assert cfg.reply_segmentation.inter_segment_delay_s == 0.3
    assert cfg.scheduler.concurrency.global_llm_limit == 4
    assert cfg.scheduler.concurrency.first_segment_release is True
    assert cfg.reply_workflow.mode == "semantic"
    assert cfg.reply_workflow.semantic_force_threshold == 0.82
    assert cfg.reply_workflow.semantic_timeout_ms == 500
    assert cfg.reply_workflow.semantic_max_chars == 42
    assert cfg.reply_workflow.directed_followup_window_s == 240.0
    assert cfg.reply_workflow.shadow_log_private is False
    assert cfg.napcat.api_url == "http://napcat:29300"
    assert cfg.admins == {"123456": "管理员A", "789012": "管理员B"}


def test_reply_workflow_rules_mode_loads_as_shadow(tmp_path: Path) -> None:
    toml_file = tmp_path / "config.toml"
    _write_toml(
        toml_file,
        """
[reply_workflow]
mode = "rules"
""",
    )

    cfg = load_config(config_path=str(toml_file))

    assert cfg.reply_workflow.mode == "shadow"


def test_load_from_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON 文件应可作为主配置源加载。"""
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    json_file = tmp_path / "config.json"
    json_file.write_text(
        """
{
  "llm": {
    "base_url": "http://json-llm:8080/v1",
    "api_key": "json-key",
    "model": "json-model",
    "max_tokens": 4096
  },
  "group": {
    "history_load_count": 42
  }
}
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(json_file))
    assert cfg.llm.base_url == "http://json-llm:8080/v1"
    assert cfg.llm.api_key == "json-key"
    assert cfg.llm.model == "json-model"
    assert cfg.llm.max_tokens == 4096
    assert cfg.group.history_load_count == 42


def test_llm_profiles_resolve_with_legacy_fallback(tmp_path: Path) -> None:
    json_file = tmp_path / "config.json"
    json_file.write_text(
        """
{
  "llm": {
    "base_url": "https://legacy.example/anthropic",
    "api_key": "sk-legacy",
    "api_format": "anthropic",
    "model": "legacy-main",
    "max_tokens": 1024,
    "default_profile": "slang",
    "profiles": {
      "slang": {
        "api_format": "openai",
        "base_url": "https://openai.example/v1",
        "model": "slang-mini",
        "max_tokens": 512,
        "capabilities": ["chat", "json"]
      }
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(json_file))
    profile = cfg.llm.resolve_profile()
    assert profile.api_format == "openai"
    assert profile.base_url == "https://openai.example/v1"
    assert profile.api_key == "sk-legacy"
    assert profile.model == "slang-mini"
    assert profile.max_tokens == 512
    assert cfg.llm.resolve_profile("missing").model == "legacy-main"


def test_llm_task_profiles_select_named_profiles(tmp_path: Path) -> None:
    json_file = tmp_path / "config.json"
    json_file.write_text(
        """
{
  "llm": {
    "base_url": "https://legacy.example/anthropic",
    "api_key": "sk-legacy",
    "model": "legacy-main",
    "default_profile": "main",
    "task_profiles": {
      "thinker": "compact-mini",
      "slang": "slang-json"
    },
    "profiles": {
      "compact-mini": {"model": "small-thinker", "max_tokens": 256},
      "slang-json": {"api_format": "openai", "model": "slang-json-model"}
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(json_file))
    assert cfg.llm.profile_name_for_task("thinker") == "compact-mini"
    assert cfg.llm.profile_name_for_task("reply_gate") == "compact-mini"
    assert cfg.llm.resolve_task_profile("thinker").model == "small-thinker"
    assert cfg.llm.resolve_task_profile("reply_gate").model == "small-thinker"
    assert cfg.llm.resolve_task_profile("slang").api_format == "openai"
    assert cfg.llm.profile_name_for_task("slang_review") == "slang-json"
    assert cfg.llm.profile_name_for_task("slang_drift") == "slang-json"
    assert cfg.llm.profile_name_for_task("compact") == "main"


def test_llm_profile_accepts_deepseek_api_format(tmp_path: Path) -> None:
    json_file = tmp_path / "config.json"
    json_file.write_text(
        """
{
  "llm": {
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-deepseek",
    "model": "deepseek-v4-flash",
    "api_format": "deepseek",
    "profiles": {
      "main": {
        "api_format": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro"
      }
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(json_file))
    assert cfg.llm.api_format == "deepseek"
    assert cfg.llm.resolve_profile("main").api_format == "deepseek"


def test_env_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量应覆盖 TOML 文件中的值。"""
    toml_file = tmp_path / "config.toml"
    _write_toml(
        toml_file,
        """
[llm]
base_url = "http://from-toml/v1"
api_key = "sk-from-toml"
model = "model-from-toml"

[napcat]
api_url = "http://napcat-from-toml:29300"
""",
    )

    monkeypatch.setenv("LLM_BASE_URL", "http://from-env/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-from-env")
    monkeypatch.setenv("LLM_MODEL", "model-from-env")
    monkeypatch.setenv("NAPCAT_API_URL", "http://napcat-from-env:29300")

    cfg = load_config(config_path=str(toml_file))

    assert cfg.llm.base_url == "http://from-env/v1"
    assert cfg.llm.api_key == "sk-from-env"
    assert cfg.llm.model == "model-from-env"
    assert cfg.napcat.api_url == "http://napcat-from-env:29300"


def test_cli_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 覆盖应优先于环境变量。"""
    monkeypatch.setenv("LLM_BASE_URL", "http://from-env/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-from-env")
    monkeypatch.setenv("LLM_MODEL", "model-from-env")

    cfg = load_config(
        config_path=None,
        cli_overrides={
            "llm_base_url": "http://from-cli/v1",
            "llm_api_key": "sk-from-cli",
            "llm_model": "model-from-cli",
        },
    )

    assert cfg.llm.base_url == "http://from-cli/v1"
    assert cfg.llm.api_key == "sk-from-cli"
    assert cfg.llm.model == "model-from-cli"


def test_bot_config_path_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BOT_CONFIG_PATH 环境变量应指定配置文件路径。"""
    toml_file = tmp_path / "my_config.toml"
    _write_toml(
        toml_file,
        """
[llm]
api_key = "sk-from-bot-config-path"
""",
    )

    monkeypatch.setenv("BOT_CONFIG_PATH", str(toml_file))
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    cfg = load_config(config_path=None)

    assert cfg.llm.api_key == "sk-from-bot-config-path"


def test_default_config_toml_auto_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """config/ 下的 config.toml 应自动加载。"""
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    toml_file = config_dir / "config.toml"
    _write_toml(
        toml_file,
        """
[llm]
model = "auto-detected-model"
""",
    )

    cfg = load_config(config_path=None)

    assert cfg.llm.model == "auto-detected-model"


def test_usage_config_defaults():
    cfg = BotConfig()
    assert cfg.llm.usage.enabled is True
    assert cfg.llm.usage.slow_threshold_s == 60.0


def test_vision_config_defaults() -> None:
    from kernel.config import VisionConfig

    v = VisionConfig()
    assert v.enabled is True
    assert v.max_images_per_message == 5
    assert v.max_dimension == 768
    assert v.cache_dir == "storage/image_cache"
    assert v.cache_max_age_hours == 24


def test_vision_config_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    toml_file = tmp_path / "config.toml"
    _write_toml(
        toml_file,
        """
[vision]
enabled = false
max_images_per_message = 3
max_dimension = 512
cache_dir = "custom/cache"
cache_max_age_hours = 12
""",
    )
    cfg = load_config(config_path=str(toml_file))
    assert cfg.vision.enabled is False
    assert cfg.vision.max_images_per_message == 3
    assert cfg.vision.max_dimension == 512
    assert cfg.vision.cache_dir == "custom/cache"
    assert cfg.vision.cache_max_age_hours == 12


def test_memo_config_defaults() -> None:
    from plugins.memo import MemoConfig
    m = MemoConfig()
    assert m.dir == "storage/memories"
    assert m.user_max_chars == 300
    assert m.group_max_chars == 500
    assert m.index_max_lines == 200
    assert m.history_enabled is True


def test_plugin_config_json_default_and_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import BaseModel

    from kernel.config import load_plugin_config

    class DemoConfig(BaseModel):
        enabled: bool = True
        limit: int = 1

    monkeypatch.chdir(tmp_path)
    (tmp_path / "plugins/demo").mkdir(parents=True)
    (tmp_path / "storage/plugins/config").mkdir(parents=True)
    (tmp_path / "plugins/demo/config.default.json").write_text(
        json.dumps({"schema_version": 1, "plugin": "demo", "values": {"enabled": True, "limit": 3}}),
        encoding="utf-8",
    )
    (tmp_path / "storage/plugins/config/demo.json").write_text(
        json.dumps({"schema_version": 1, "plugin": "demo", "values": {"limit": 9}}),
        encoding="utf-8",
    )

    cfg = load_plugin_config("plugins/demo/config.default.json", DemoConfig)
    assert cfg.enabled is True
    assert cfg.limit == 9


def test_compact_config_defaults():
    from kernel.config import CompactConfig
    c = CompactConfig()
    assert c.ratio == 0.7
    assert c.compress_ratio == 0.5
    assert c.max_failures == 3
    assert c.cache_hit_warn == 90.0


def test_compact_config_rejects_invalid_ratio():
    from kernel.config import CompactConfig
    with pytest.raises(ValueError):
        CompactConfig(ratio=1.5)
    with pytest.raises(ValueError):
        CompactConfig(compress_ratio=0.0)


# ---------------------------------------------------------------------------
# GroupConfig.resolve() tests
# ---------------------------------------------------------------------------


class TestGroupConfigResolve:
    def test_group_defaults_are_active_when_access_is_open(self) -> None:
        cfg = GroupConfig()
        assert cfg.presence.default_mode == "active"
        assert cfg.allows_learning_group(999) is True
        assert cfg.allows_active_group(999) is True
        resolved = cfg.resolve(999)
        assert resolved.presence_mode == "active"
        assert resolved.access_allowed is True
        assert resolved.slang_enabled is True

    def test_legacy_allowed_groups_migrate_to_active_whitelist(self) -> None:
        cfg = GroupConfig(allowed_groups=[100, 200])
        assert cfg.access.mode == "whitelist"
        assert cfg.access.whitelist == [100, 200]
        assert cfg.allowed_groups == [100, 200]
        assert cfg.allows_learning_group(100) is True
        assert cfg.allows_active_group(100) is True
        assert cfg.resolve(100).presence_mode == "active"
        assert cfg.resolve(999).presence_mode == "off"
        assert cfg.allows_learning_group(999) is False
        assert cfg.allows_active_group(999) is False

    def test_group_policy_file_overrides_legacy_allowlist(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({
                "group": {
                    "allowed_groups": [100, 200],
                    "presence": {
                        "default_mode": "active",
                    },
                    "overrides": {
                        "999": {
                            "presence_mode": "silent_learn",
                            "slang_enabled": True,
                        },
                    },
                },
            }),
            encoding="utf-8",
        )
        policy_file = tmp_path / "group-policy.json"
        policy_file.write_text(
            json.dumps({
                "mode": "whitelist",
                "whitelist": [300],
                "blacklist": [200],
                "log_dropped": True,
            }),
            encoding="utf-8",
        )

        cfg = load_config(config_path=str(config_file))

        assert cfg.group.access.mode == "whitelist"
        assert cfg.group.access.whitelist == [300]
        assert cfg.group.active_access_allowed(200) is False
        assert cfg.group.active_access_allowed(100) is False
        assert cfg.group.resolve(100).presence_mode == "off"
        assert cfg.group.resolve(200).presence_mode == "off"
        assert cfg.group.resolve(300).presence_mode == "active"
        assert cfg.group.resolve(999).presence_mode == "silent_learn"
        assert cfg.group.resolve(999).slang_enabled is True
        assert cfg.group.allows_learning_group(999) is True

    def test_resolve_no_override(self) -> None:
        """No override for group — returns global defaults."""
        cfg = GroupConfig(
            debounce_seconds=5.0, batch_size=10, at_only=False,
            blocked_users=[100], allowed_tools=["lookup_cards"], blocked_tools=["web_search"], history_load_count=30,
            reply_style="steady", custom_prompt="保持冷静。",
            tools_enabled=False, sticker_mode="rarely", slang_enabled=False,
        )
        resolved = cfg.resolve(999)
        assert resolved.blocked_users == {100}
        assert resolved.allowed_tools == {"lookup_cards"}
        assert resolved.blocked_tools == {"web_search"}
        assert resolved.at_only is False
        assert resolved.debounce_seconds == 5.0
        assert resolved.batch_size == 10
        assert resolved.history_load_count == 30
        assert resolved.reply_style == "steady"
        assert resolved.custom_prompt == "保持冷静。"
        assert resolved.presence_mode == "active"
        assert resolved.access_allowed is True
        assert resolved.tools_enabled is False
        assert resolved.sticker_mode == "rarely"
        assert resolved.slang_enabled is False

    def test_resolve_full_override(self) -> None:
        """Override supplies all fields — all override values win."""
        cfg = GroupConfig(
            debounce_seconds=5.0, batch_size=10, blocked_users=[100], allowed_tools=["lookup_cards", "web_search"],
            overrides={
                123: GroupOverride(
                    blocked_users=[200], allowed_tools=["lookup_cards", "send_sticker"],
                    blocked_tools=["lookup_cards"], at_only=True,
                    debounce_seconds=10.0, batch_size=20, history_load_count=50,
                    reply_style="playful", custom_prompt="多接梗。",
                    tools_enabled=False, sticker_mode="off", slang_enabled=False,
                ),
            },
        )
        resolved = cfg.resolve(123)
        assert resolved.blocked_users == {100, 200}
        assert resolved.allowed_tools == {"send_sticker"}
        assert resolved.blocked_tools == {"lookup_cards"}
        assert resolved.at_only is True
        assert resolved.debounce_seconds == 10.0
        assert resolved.batch_size == 20
        assert resolved.history_load_count == 50
        assert resolved.reply_style == "playful"
        assert resolved.custom_prompt == "多接梗。"
        assert resolved.tools_enabled is False
        assert resolved.sticker_mode == "off"
        assert resolved.slang_enabled is False

    def test_resolve_partial_override_falls_back(self) -> None:
        """Override only sets at_only — rest falls back to global."""
        cfg = GroupConfig(
            debounce_seconds=5.0, batch_size=10,
            overrides={123: GroupOverride(at_only=True)},
        )
        resolved = cfg.resolve(123)
        assert resolved.at_only is True
        assert resolved.debounce_seconds == 5.0
        assert resolved.batch_size == 10
        assert resolved.history_load_count == 30

    def test_resolve_blocked_users_union(self) -> None:
        """blocked_users is the union of global and per-group lists."""
        cfg = GroupConfig(
            blocked_users=[1, 2],
            overrides={123: GroupOverride(blocked_users=[2, 3])},
        )
        resolved = cfg.resolve(123)
        assert resolved.blocked_users == {1, 2, 3}

    def test_resolve_tool_lists_override_and_fallback(self) -> None:
        cfg = GroupConfig(
            allowed_tools=["lookup_cards"],
            blocked_tools=["web_search"],
            overrides={
                123: GroupOverride(allowed_tools=["lookup_cards", "send_sticker"]),
                456: GroupOverride(blocked_tools=["send_group_msg"]),
            },
        )

        resolved_allow = cfg.resolve(123)
        assert resolved_allow.allowed_tools == {"lookup_cards", "send_sticker"}
        assert resolved_allow.blocked_tools == {"web_search"}

        resolved_block = cfg.resolve(456)
        assert resolved_block.allowed_tools == {"lookup_cards"}
        assert resolved_block.blocked_tools == {"send_group_msg"}

    def test_resolve_override_at_only_false_overrides_global_true(self) -> None:
        """Per-group at_only=False overrides global at_only=True."""
        cfg = GroupConfig(
            at_only=True,
            overrides={123: GroupOverride(at_only=False)},
        )
        resolved = cfg.resolve(123)
        assert resolved.at_only is False


def test_group_overrides_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TOML [group.overrides.<id>] sections parse into GroupConfig.overrides."""
    monkeypatch.delenv("BOT_CONFIG_PATH", raising=False)
    toml_file = tmp_path / "config.toml"
    _write_toml(
        toml_file,
        """
[group]
blocked_users = [100]
at_only = false

[group.overrides.100001]
blocked_users = [200, 300]
at_only = true
debounce_seconds = 10.0

[group.overrides.100002]
batch_size = 20
history_load_count = 50
reply_style = "gentle"
sticker_mode = "rarely"
slang_enabled = false
""",
    )
    cfg = load_config(config_path=str(toml_file))

    assert cfg.group.blocked_users == [100]
    assert cfg.group.at_only is False

    assert 100001 in cfg.group.overrides
    o1 = cfg.group.overrides[100001]
    assert o1.blocked_users == [200, 300]
    assert o1.at_only is True
    assert o1.debounce_seconds == 10.0
    assert o1.batch_size is None

    assert 100002 in cfg.group.overrides
    o2 = cfg.group.overrides[100002]
    assert o2.batch_size == 20
    assert o2.history_load_count == 50
    assert o2.reply_style == "gentle"
    assert o2.sticker_mode == "rarely"
    assert o2.slang_enabled is False
    assert o2.at_only is None
