# 06 — 配置系统

## 位置

所有配置模型和加载逻辑位于 `kernel/config.py`（~370 行）。这是框架的配置中枢。

```python
from kernel.config import BotConfig, KernelConfig, load_config
# 或
from kernel.config import BotConfig, GroupConfig, LLMConfig, VisionConfig  # 子模型
```

## 三层优先级

```
TOML 文件  <  环境变量  <  CLI 参数
   ↓              ↓           ↓
 config.toml    APP_XXX    --xxx=value
```

## 配置模型清单（23 个类）

### 内核

| 模型 | 字段 | 默认值 |
|------|------|--------|
| `KernelConfig` | `plugin_dirs`, `disabled_plugins`, `max_hook_time_ms` | `["plugins"]`, `[]`, `5000` |

### LLM

| 模型 | 说明 |
|------|------|
| `LLMConfig` | `base_url`, `api_key`, `model`, `max_tokens` |
| `ContextConfig` | `max_context_tokens` (默认 1M) |
| `UsageConfig` | `enabled`, `slow_threshold_s` |

### 群聊

| 模型 | 说明 |
|------|------|
| `GroupConfig` | `history_load_count`, `allowed_groups`, `debounce_seconds`, `batch_size`, `at_only`, `blocked_users`, `privacy_mask`, `overrides` |
| `GroupOverride` | 单群覆盖（`at_only`, `debounce_seconds`, `batch_size`, `history_load_count`, `blocked_users`），`None` = 使用全局值 |
| `ResolvedGroupConfig` | `resolve(group_id)` 返回的扁平配置（set 类型 blocked_users） |

### 记忆 / 压缩 / Dream

| 模型 | 说明 |
|------|------|
| `MemoConfig` | `dir`, `user_max_chars`, `group_max_chars`, `index_max_lines`, `history_enabled` |
| `CompactConfig` | `ratio`, `compress_ratio`, `max_failures`, 缓存告警阈值 |
| `DreamConfig` | `enabled`, `interval_hours`, `max_rounds` |

### 视觉 / 表情包 / 日程 / 好感度

| 模型 | 说明 |
|------|------|
| `VisionConfig` | `enabled`, `max_images_per_message`, `max_dimension`, `cache_dir`, `qwen` |
| `QwenVLConfig` | Qwen VL 小模型配置 |
| `StickerConfig` | `enabled`, `storage_dir`, `max_count`, `frequency` |
| `ScheduleConfig` | `enabled`, `storage_dir`, `generate_at_hour`, `mood_anomaly_chance`, `mood_refresh_minutes` |
| `AffectionConfig` | `enabled`, `storage_dir`, `score_increment`, `daily_cap` |

### 其他

| 模型 | 说明 |
|------|------|
| `ThinkerConfig` | `enabled`, `max_tokens` |
| `ElementDetectionConfig` | `enabled`, `rules: list[ElementRule]` |
| `ElementRule` | `pattern`, `reply`, `description`, `use_llm` |
| `AntiDetectConfig` | `enabled`, `min_delay`, `max_delay`, `char_delay` |
| `LogConfig` / `LogChannelConfig` | 日志目录 + stderr 频道开关 |
| `SoulConfig` | 人设目录 |
| `NapcatConfig` | NapCat HTTP API 地址 |

## BotConfig（根模型）

```python
class BotConfig(BaseModel):
    kernel: KernelConfig = KernelConfig()
    anti_detect: AntiDetectConfig = AntiDetectConfig()
    llm: LLMConfig = LLMConfig()
    log: LogConfig = LogConfig()
    memo: MemoConfig = MemoConfig()
    compact: CompactConfig = CompactConfig()
    dream: DreamConfig = DreamConfig()
    soul: SoulConfig = SoulConfig()
    group: GroupConfig = GroupConfig()
    napcat: NapcatConfig = NapcatConfig()
    vision: VisionConfig = VisionConfig()
    sticker: StickerConfig = StickerConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    affection: AffectionConfig = AffectionConfig()
    thinker: ThinkerConfig = ThinkerConfig()
    element_detection: ElementDetectionConfig = ElementDetectionConfig()
    admins: dict[str, str] = {}
    allowed_private_users: list[int] = []
    admin_token: str = ""
```

## GroupOverride 与 resolve()

```python
class GroupOverride(BaseModel):
    blocked_users: list[int] = []
    at_only: bool | None = None         # None = 使用全局值
    debounce_seconds: float | None = None
    batch_size: int | None = None
    history_load_count: int | None = None

class GroupConfig(BaseModel):
    ...
    def resolve(self, group_id: int) -> ResolvedGroupConfig:
        """合并全局默认值与单群覆盖，返回扁平配置。"""
```

在 `config.toml` 中覆盖：

```toml
[group.overrides."100001"]
at_only = true
debounce_seconds = 10.0
blocked_users = [123456]
```

## 环境变量映射

| 环境变量 | 对应 dotted key |
|----------|----------------|
| `LLM_BASE_URL` | `llm.base_url` |
| `LLM_API_KEY` | `llm.api_key` |
| `LLM_MODEL` | `llm.model` |
| `NAPCAT_API_URL` | `napcat.api_url` |
| `ADMIN_TOKEN` | `admin_token` |
| `QWEN_VL_API_KEY` | `vision.qwen.api_key` |
| `QWEN_VL_BASE_URL` | `vision.qwen.base_url` |
| `QWEN_VL_MODEL` | `vision.qwen.model` |

## CLI 参数

```bash
python bot.py --llm-model=claude-opus-4-7 --llm-api-key=sk-xxx
```

CLI 参数 → 环境变量 `_CLI_*` → dotted key 写入配置字典。

## load_config()

```python
def load_config(
    config_path: str | None = None,
    cli_overrides: dict[str, str] | None = None,
) -> BotConfig: ...
```

合并顺序：
1. Pydantic 默认值
2. TOML 文件（`config.toml` 或 `BOT_CONFIG_PATH` 环境变量指定）
3. 环境变量（`_ENV_MAP`）
4. `_CLI_*` 环境变量（bot.py argparse 写入）
5. `cli_overrides` 参数

## 配置注入

`bot.py` 启动时构建 `PluginContext`，将 `BotConfig` 注入：

```python
config = load_config()
ctx = PluginContext(config=config, ...)
await bus.fire_on_startup(ctx)
```

## 向后兼容

旧 `src/` 路径已于 2026-05-01 清理移除。所有配置模型和加载器统一从 `kernel.config` 导入。
