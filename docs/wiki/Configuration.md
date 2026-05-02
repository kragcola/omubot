# 配置

## 双配置文件

| 文件 | 用途 | 谁读取 |
|------|------|--------|
| `config/.env` | NoneBot 框架层 + LLM 环境变量 | `nonebot.init()` |
| `config/config.toml` | Bot 业务层 | `kernel/config.py` |

优先级：**CLI 参数 > 环境变量 > TOML**

## config/.env（框架层）

```env
ENVIRONMENT=prod
SUPERUSERS=["384801062", "1416930401"]
ONEBOT_WS_URLS=["ws://napcat:3001"]
ANTHROPIC_API_KEY=sk-xxx
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-flash
VISION_API_KEY=sk-xxx
VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VISION_MODEL=qwen-vl-plus
ADMIN_TOKEN=your-secret-token
```

## config/config.toml（业务层）

```toml
[llm]
api_key = "sk-xxx"
base_url = "https://api.deepseek.com/anthropic"
model = "deepseek-v4-flash"
max_tokens = 1024

[context]
max_tokens = 1000000
compact_ratio = 0.7

[group]
allowed_groups = [984198159, 993065015]
at_only = false
debounce_seconds = 5.0
batch_size = 10
privacy_mask = true

[vision]
enabled = true
max_images_per_message = 5

[sticker]
enabled = true
frequency = "frequently"

[affection]
enabled = true

[dream]
enabled = true
interval_hours = 24

[schedule]
enabled = true

[log.channels]
message_in = true
message_out = true
thinking = true
system = true
mood = true
affection = true
```

## 单群覆盖

```toml
[group.overrides."123456789"]
at_only = true
debounce_seconds = 10.0
batch_size = 20
blocked_users = [123456]
```

## 人设文件

| 文件 | 内容 |
|------|------|
| `config/soul/identity.md` | 角色定义（姓名、性格、插话方式） |
| `config/soul/instruction.md` | 行为指令（回复风格、工具使用、格式规则） |

修改后 `docker compose restart bot` 即可生效。

## 管理员

- `SUPERUSERS` 环境变量：框架层超级用户
- `config/config.toml` → `[admins]`：业务层管理员
- `/debug`、`/plugins` 需要管理员权限
