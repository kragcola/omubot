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
    "max_tokens": 1024,
    "api_format": "anthropic",
    "default_profile": "main"
  },
  "group": {
    "access": {
      "mode": "whitelist",
      "whitelist": [984198159, 993065015],
      "blacklist": [],
      "log_dropped": true
    },
    "presence": {
      "default_mode": "active"
    },
    "at_only": false,
    "debounce_seconds": 5.0,
    "batch_size": 10,
    "tools_enabled": true,
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
- `llm.task_profiles`：为不同调用任务映射 provider profile；常见任务包括 `main / thinker / compact / reply_gate / vision / slang / style / memo / persona_import / birthday_wish`
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
      "slang_review": "slang",
      "slang_drift": "slang",
      "reply_gate": "main",
      "vision": "main"
    }
  }
}
```

说明：

- `main` profile 会同步 legacy `llm.api_format / base_url / api_key / model / max_tokens` 根字段，保证旧配置和新 profile 体系兼容
- 删除某个非 `main` profile 后，引用它的任务映射会自动回退到当前 `default_profile`
- `slang_review` 和 `slang_drift` 如果没有单独 profile，会回退到 `slang` 或 `default_profile`
- `reply_gate` 如果没有单独 profile，会回退到 `thinker`
- API Key 在 Web 端默认只显示遮罩值；替换或清空需要在“定义管理”里显式操作
- 完整任务名以 `services/llm/llm_request.py` 的 `LLMTask` 为单一真相源；Admin System 页任务列表与它保持同步

## 视觉与角色识别

视觉主配置位于 `vision`，其中角色识别配置位于 `vision.character_recognition`：

```json
{
  "vision": {
    "enabled": true,
    "max_images_per_message": 5,
    "character_recognition": {
      "enabled": false,
      "sidecar_url": "http://host.docker.internal:8620",
      "packs_dir": "config/character_packs",
      "timeout_seconds": 5.0,
      "animetrace_enabled": true,
      "animetrace_model": "anime_model_lovelive",
      "animetrace_timeout_seconds": 8.0,
      "multi_char_enabled": true,
      "auto_merge_series_packs": true
    }
  }
}
```

字段说明：

| 字段 | 默认 | 说明 |
|------|------|------|
| `vision.enabled` | `true` | 是否启用多模态图片描述 |
| `vision.max_images_per_message` | `5` | 单条消息最多处理几张图片 |
| `vision.character_recognition.enabled` | `false` | 是否启用角色识别链路 |
| `sidecar_url` | `http://host.docker.internal:8620` | `ccip-sidecar` 服务地址 |
| `packs_dir` | `config/character_packs` | 角色包目录 |
| `timeout_seconds` | `5.0` | sidecar 请求超时 |
| `animetrace_enabled` | `true` | 是否并行使用 AnimeTrace 辅助作品判断 |
| `multi_char_enabled` | `true` | 是否调用 sidecar `/identify-multi` |
| `auto_merge_series_packs` | `true` | 启动前自动归并同 `work` 的安全单角色包 |

说明：

- `config/character_packs/*.charpack/` 属于运行时数据，默认不进入 Git。
- `/admin/characters` 可执行单角色录入、系列 pack 构建、角色包合并和重扫。
- 关闭 `vision.character_recognition.enabled` 后，整条识别链路会旁路回普通图片描述。

## 群访问与单群画像

群配置现在分两层：

- `group.access`：控制哪些群可以主动发言和调用工具。
- `group.presence` / `group.overrides`：控制默认参与模式和单群 profile。

参与模式：

| 模式 | 含义 |
| --- | --- |
| `active` | 可主动回复，可按配置调用工具 |
| `silent_learn` | 不主动回复；只允许显式开启的学习能力读取 |
| `off` | 完全忽略群聊 |

访问策略示例：

```json
{
  "group": {
    "access": {
      "mode": "whitelist",
      "whitelist": [984198159, 993065015],
      "blacklist": [],
      "log_dropped": true
    },
    "presence": {
      "default_mode": "active"
    }
  }
}
```

单群覆盖示例：

```json
{
  "group": {
    "overrides": {
      "123456789": {
        "presence_mode": "active",
        "at_only": true,
        "talk_value": 0.25,
        "planner_smooth": 4.0,
        "debounce_seconds": 10.0,
        "batch_size": 20,
        "blocked_users": [123456],
        "allowed_tools": ["lookup_cards", "slang_lookup"],
        "blocked_tools": ["web_search"],
        "tools_enabled": true,
        "reply_style": "default",
        "custom_prompt": "",
        "sticker_mode": "inherit",
        "slang_enabled": true
      }
    }
  }
}
```

`allowed_tools` 和 `blocked_tools` 最终会相减，blocked 优先生效。被访问策略拦截的群默认 `tools_enabled=false`；如果单群显式开启 `slang_enabled` 且 presence 不是 `off`，可用于 `silent_learn`。

相关 Admin 能力：

- `/admin/groups` 可查看群运行态、recent messages 和 profile 自定义状态。
- `GET/POST /api/admin/groups/{group_id}/profile` 读写单群 profile。
- `DELETE /api/admin/groups/{group_id}/profile` 重置单群覆盖。
- profile 保存会写入审计记录，便于回溯群策略变更。

## 人设文件

> 2026-05-27 C 系列 v2 only 切换后：原 `config/soul/identity.md` + `instruction.md` 已退役；现走 v2 source.md 单文件。

| 路径 | 内容 |
|------|------|
| `config/persona/<persona_id>/source.md` | v2 单文件 source（YAML front matter + markdown 章节）|
| `config/persona/<persona_id>/freeze/` | importer 编译产物（多块 prompt yaml + `_persona_runtime.json`）|
| `config/config.json` 顶层 `persona_v2.persona_id` | 选择 active persona |

修改路径：admin SPA「人设管理」面板 → 编辑 source.md → import → freeze → 热重载。`config.json` 改 `persona_v2.persona_id` 后 `docker compose restart bot` 即可生效。

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
| `backlog_review_enabled` | 是否复核存量 `candidate` 候选池 |
| `backlog_review_batch_size` | backlog reviewer 每批处理数量 |
| `backlog_review_min_confidence` | backlog reviewer 处理候选的最低置信度 |
| `drift_detection_enabled` | v3 语义漂移检测 |
| `lookup_tool_enabled` | 是否注册 `slang_lookup` 工具 |

`backlog_review_enabled` 用于处理历史积压 candidate。2026-05-16 修复后，backlog reviewer 受每日 slot 幂等闸门控制：同一个 `daily_ai_review_times` slot 清空后不会在下一个 tick 立刻重启；重置 backlog review 会同时清掉 `last_backlog_review_slot`。

## 表达学习设置

表达学习由 `plugins/style/config.default.json` 和 `storage/plugins/config/style.json` 合并控制：

| 设置 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用表达学习 Prompt 注入 |
| `max_items` | `3` | 每轮最多注入表达参考条数 |
| `max_chars` | `800` | 表达参考块最大字符数 |
| `min_confidence` | `0.45` | 注入表达样本最低置信度 |
| `profile_enabled` | `true` | 是否启用动态风格档案 |
| `profile_max_chars` | `900` | 风格档案 Prompt 最大字符数 |
| `collect_bot_replies` | `true` | 是否采集 bot 回复弱信号 |
| `global_enabled_group_ids` | `[]` | 参与 global 表达池的群 ID |

表达学习只保存动态表达档案和样本，不会自动修改 `config/persona/<id>/source.md`（v2 only 切换后唯一人设源）。

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
