# 配置

## 配置文件

| 文件 | 用途 | 谁读取 |
|------|------|--------|
| `config/.env` | NoneBot 框架层 + LLM 环境变量 | `nonebot.init()` |
| `config/config.json` | Bot 业务层主配置，Admin Web 保存目标 | `kernel/config.py` |
| `config/config.toml` | Legacy 业务配置，兼容读取 | `kernel/config.py` |

配置加载优先级：**CLI 参数 > `_CLI_*` 环境变量 > 环境变量 > 配置文件 > Pydantic 默认值**。

配置文件解析规则：

- 默认优先读取 `config/config.json`。
- 如果 JSON 不存在，会兼容读取 `config/config.toml`。
- Admin 配置页保存时会写出 `config/config.json`。
- 从 TOML 迁移到 JSON 不会自动删除旧 TOML。

## config/.env（框架层）

```env
ENVIRONMENT=prod
SUPERUSERS=["384801062", "1416930401"]
# 当前 Docker 部署使用 NapCat websocketClients 反连 Bot；
# 不再需要在 .env 中启用 ONEBOT_WS_URLS 正向连接。
ANTHROPIC_API_KEY=sk-xxx
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-flash
VISION_API_KEY=sk-xxx
VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VISION_MODEL=qwen-vl-plus
ADMIN_TOKEN=your-secret-token
```

## config/config.json / config.toml（业务层）

JSON 主配置示例：

```json
{
  "llm": {
    "api_key": "sk-xxx",
    "base_url": "https://api.deepseek.com/anthropic",
    "model": "deepseek-v4-flash",
    "max_tokens": 1024
  },
  "group": {
    "allowed_groups": [984198159, 993065015],
    "at_only": false,
    "debounce_seconds": 5.0,
    "batch_size": 10,
    "privacy_mask": true
  },
  "vision": {
    "enabled": true,
    "max_images_per_message": 5
  },
  "sticker": {
    "enabled": true,
    "frequency": "frequently"
  },
  "affection": {
    "enabled": true
  },
  "dream": {
    "enabled": true,
    "interval_hours": 24
  },
  "schedule": {
    "enabled": true
  },
  "log": {
    "channels": {
      "message_in": true,
      "message_out": true,
      "thinking": true,
      "system": true,
      "mood": true,
      "affection": true
    }
  }
}
```

已有 `config/config.toml` 仍可继续读取。管理端“配置”页会按 `BotConfig` 递归生成结构化控件，并提供高级 JSON 兜底。

配置页当前还提供两层运维辅助：

- 保存前变更预览：调用 `/api/admin/config/preview`，展示服务端校验后的规范化 diff
- 最近保存审计：从 `storage/config/config-audit.json` 读取最近几次落盘摘要，敏感字段只显示遮罩值
- 可恢复配置快照：从 `storage/config/config-backups.json` 与 `storage/config/backups/` 读取最近快照摘要，并通过 `/api/admin/config/restore` 执行回滚；Web 端只显示摘要，不展示快照里的 secret 明文

## LLM Profiles

Omubot 现在支持“定义”和“任务映射”分离的 Provider 管理：

- `llm.profiles`：保存各个 provider profile 的定义
- `llm.default_profile`：主聊天任务默认使用哪个 profile
- `llm.task_profiles`：`main / thinker / compact / slang / vision` 分别映射到哪个 profile
- `/admin/system` → `LLM Provider`：可热切换任务映射，也可在“定义管理”抽屉里结构化编辑 profile

示例：

```json
{
  "llm": {
    "api_format": "anthropic",
    "base_url": "https://api.deepseek.com/anthropic",
    "api_key": "sk-main",
    "model": "deepseek-v4-flash",
    "max_tokens": 2048,
    "default_profile": "main",
    "profiles": {
      "main": {
        "api_format": "anthropic",
        "base_url": "https://api.deepseek.com/anthropic",
        "api_key": "sk-main",
        "model": "deepseek-v4-flash",
        "max_tokens": 2048,
        "capabilities": ["chat", "tools", "thinking"]
      },
      "slang": {
        "api_format": "openai",
        "base_url": "https://api.openai.example/v1",
        "api_key": "sk-slang",
        "model": "gpt-4o-mini",
        "max_tokens": 1024,
        "capabilities": ["chat", "json", "compact"]
      }
    },
    "task_profiles": {
      "main": "main",
      "thinker": "main",
      "compact": "main",
      "slang": "slang",
      "vision": "main"
    }
  }
}
```

说明：

- `main` profile 会同步 legacy `llm.api_format / base_url / api_key / model / max_tokens` 根字段，保证旧配置和新 profile 体系兼容
- 删除某个非 `main` profile 后，引用它的任务映射会自动回退到当前 `default_profile`
- API Key 在 Web 端默认只显示遮罩值；替换或清空需要在“定义管理”里显式操作

## 单群覆盖

```json
{
  "group": {
    "overrides": {
      "123456789": {
        "at_only": true,
        "debounce_seconds": 10.0,
        "batch_size": 20,
        "blocked_users": [123456]
      }
    }
  }
}
```

## 人设文件

| 文件 | 内容 |
|------|------|
| `config/soul/identity.md` | 角色定义（姓名、性格、插话方式） |
| `config/soul/instruction.md` | 行为指令（回复风格、工具使用、格式规则） |

修改后 `docker compose restart bot` 即可生效。

## 黑话设置

群内黑话设置不写入 `config/config.json`，由 `storage/slang.db` 中的 `slang_settings` 保存，并通过 `/admin/slang` 结构化设置面板修改。

常用项：

| 设置 | 说明 |
|------|------|
| `learning_enabled` | 是否学习群聊候选 |
| `injection_enabled` | 是否注入已批准黑话 |
| `candidate_min_count` | 候选进入审核前的最小出现次数 |
| `group_allowlist` | 允许学习的群；空表示所有群 |
| `stoplist` | 永不学习词 |
| `daily_ai_review_enabled` | 每日 AI 识别 |
| `daily_ai_auto_approve_enabled` | 是否允许 AI 自动通过 |
| `drift_detection_enabled` | v3 语义漂移检测 |
| `lookup_tool_enabled` | 是否注册 `slang_lookup` 工具 |

## 管理员

- `SUPERUSERS` 环境变量：框架层超级用户
- `config/config.json` → `admins`：业务层管理员；旧 TOML `[admins]` 仍兼容读取
- `/debug`、`/plugins` 需要管理员权限

## 轻量语义检索

记忆检索的轻量语义增强写在主配置 `memory.semantic` 下：

```json
{
  "memory": {
    "semantic": {
      "enabled": true,
      "backend": "ngram"
    }
  }
}
```

字段说明：

| 字段 | 默认 | 说明 |
|------|------|------|
| `memory.semantic.enabled` | `false` | 是否启用 RetrievalGate 的轻量语义匹配兜底 |
| `memory.semantic.backend` | `ngram` | 语义后端：`ngram` 或 `embedding` |

当前实现规则：

- `enabled=false` 时，记忆检索保持原有“全量 / 定期刷新 / 关键词匹配 / 最小提示”四层逻辑。
- `enabled=true` 时，如果关键词匹配没有命中，会追加一次“轻量语义匹配”。
- 默认 `ngram` 后端无额外依赖，可直接用于 Docker。
- `embedding` 目前仍是 optional extra 预留；未安装时会安全降级回 `ngram`，不会中断对话链路。

更详细的运行说明见 [Semantic-Retrieval](Semantic-Retrieval)。
