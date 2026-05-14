# 搭建教程

> 从零搭建 Omubot QQ 机器人。预计耗时：30-60 分钟。

## 前置要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| Docker + Docker Compose | 最新版 | 容器化运行 |
| Python + uv | 3.12+ | 本地开发/测试 |
| QQ 号 | 任意 | Bot 身份（建议用小号，有风控风险） |

## 第一步：获取代码

```bash
git clone https://github.com/RoggeOhta/amadeus-in-shell.git
cd amadeus-in-shell
```

## 第二步：配置环境变量

复制模板并填写：

```bash
cp .env.example config/.env
```

编辑 `config/.env`，填写以下必填项：

```env
# NapCat WebSocket 地址（用容器名，无需修改）
ONEBOT_WS_URLS='["ws://napcat:3001"]'

# NoneBot 超级用户（必须改，填自己的 QQ 号）
SUPERUSERS='["你的QQ号"]'

# DeepSeek API Key（必须改）
LLM_API_KEY=sk-your-deepseek-api-key

# Admin 面板登录密码（必须改）
ADMIN_TOKEN=your-secret-token
```

> 其他 LLM 提供商只要支持 Anthropic Messages API 格式即可使用。设置 `LLM_BASE_URL` 和 `LLM_MODEL` 切换。

## 第三步：配置 Bot

```bash
cp config.example.toml config/config.toml
```

编辑 `config/config.toml`（legacy 兼容格式，首次在 `/admin/config` 保存后会生成 `config/config.json`），关键项：

```toml
[llm]
api_key = "sk-your-deepseek-api-key"   # 或通过环境变量 LLM_API_KEY
base_url = "https://api.deepseek.com/anthropic"
model = "deepseek-v4-flash"

[admins]
"你的QQ号" = "管理员"

[group]
allowed_groups = [984198159, 993065015]  # legacy 兼容字段，老群会继续保持 active

[group.presence]
default_mode = "silent_learn"           # 新群默认静默学习，不主动说话
```

群聊访问门禁现在单独放在 `config/group-policy.json`，也可以在 `/admin/groups` 的“群聊门禁”卡片里编辑：

```json
{
  "mode": "whitelist",
  "whitelist": [984198159, 993065015],
  "blacklist": [],
  "log_dropped": true
}
```

白名单模式下，白名单群开启、其余关闭；黑名单模式下，黑名单群关闭、其余开启。如果你要把新群先放在“只学习不发言”的状态，保持 `presence.default_mode = "silent_learn"` 就行；需要某个群主动发言时，再到 `/admin/groups` 里把该群的参与模式切成 `active`。

## 第四步：启动 NapCat 并扫码登录

```bash
docker compose up napcat -d
```

打开浏览器访问 `http://localhost:6099/webui` → 点击"登录" → 用 Bot QQ 号扫码。

> 扫码后 NapCat 会持久化 device fingerprint 到 `napcat/data/`。**以后断线只需 `docker compose restart napcat`，不要 `down`+`up`**，否则 fingerprint 变化触发腾讯反欺诈。

## 第五步：启动 Bot

```bash
docker compose up bot -d --build
```

查看日志确认启动成功：

```bash
docker compose logs bot -f
```

看到以下输出表示成功：

```
========== Bot 启动 ==========
[LLM] model=deepseek-v4-flash ...
NoneBot is initializing...
Running NoneBot...
Uvicorn running on http://0.0.0.0:8080
Bot <你的QQ号> connected
```

## 第六步：验证

1. **私聊测试**：用你的 QQ 给 Bot 发 "你好"
2. **群聊测试**：在群里 @bot 发消息
3. **Admin 面板**：浏览器访问 `http://localhost:8081/admin/`，用 `ADMIN_TOKEN` 登录

## 下一步：定制人设

编辑 `soul/identity.md` 设定角色人格，`soul/instruction.md` 设定行为规则。

```bash
vim soul/identity.md
vim soul/instruction.md
docker compose restart bot   # 重启生效
```

关键格式：
- `identity.md` 中 `## 插话方式` 段落控制主动插话规则（有这个标题 = 允许主动插话）
- `instruction.md` 控制回复风格、分段发送、工具使用偏好等

## 目录结构速览

```
amadeus-in-shell/
├── bot.py                  # 入口：加载配置 → 初始化 NoneBot → 注册插件
├── config.example.toml     # 业务配置模板
├── .env.example            # 环境变量模板
├── pyproject.toml          # Python 项目配置 + NoneBot 设置
├── Dockerfile              # 多阶段构建
├── docker-compose.yml
├── config/                 # 运行时配置（gitignore，从模板复制）
│   ├── config.json         #   主配置（JSON，管理端保存目标）
│   ├── config.toml         #   legacy 兼容配置（可选保留）
│   ├── .env                #   环境变量
│   └── soul/               #   人设文件
│       ├── identity.md     #     角色定义
│       └── instruction.md  #     行为指令
├── soul/                   # 人设模板
│   ├── identity.example.md
│   └── instruction.example.md
├── kernel/                 # 内核层（PluginBus、类型、配置）
├── services/               # 系统服务层（LLM、记忆、时间线、媒体）
├── plugins/                # 插件层（14 个可开关插件）
├── admin/                  # 管理面板
├── docs/                   # 项目文档
├── wiki/                   # 框架开发文档
├── storage/                # 运行时数据（日志、数据库、缓存）
└── tests/                  # 测试
```

## 开发指南

### 本地运行

```bash
# 先确保 NapCat 在 Docker 中运行
docker compose up napcat -d

# 本地安装依赖
uv sync

# 直接运行（方便调试）
uv run python bot.py
```

### macOS 外置盘 exFAT 开发（可选）

如果你的仓库放在 `exFAT` 外置盘上，建议改用外置盘里的 sparseimage 工作区，避免 `.venv` 外链、`uv` 家目录缓存、AppleDouble 元数据和 Codex 可写根不匹配问题。若 APFS 镜像在该外置盘上无法挂载，可设置 `OMUBOT_WORKSPACE_FS='JHFS+'` 使用 HFS+ 兜底。

```bash
# 1. 在外置盘上创建并挂载工作区（只需第一次加 --create）
./scripts/dev/mount-workspace.sh --create

# 2. 把仓库复制到新挂载点后进入新路径
cd ~/OmubotWorkspace/omubot

# 3. 创建仓库内真实 .venv，并把 uv/pip 缓存收口到 .cache/
./scripts/dev/bootstrap.sh

# 4. 检查当前环境是否仍然越界到 ~/venvs 或 ~/.cache
source ./scripts/dev/env.sh
./scripts/dev/doctor.sh
```

说明：

- 这套流程只影响本机开发环境，不影响 Docker 部署和其他下载用户。
- 默认挂载点建议使用 `~/OmubotWorkspace` 这类用户可写目录；映像文件本身仍在外置盘，主要容量不占主硬盘。
- `doctor` 如果提示有 `._*` AppleDouble 文件，可运行 `./scripts/cleanup-appledouble.sh` 清理。
- 容器内 `.venv` 逻辑保持不变；这套方案只收口宿主机开发环境。

### 运行测试

```bash
uv run pytest                    # 全部测试
uv run pytest tests/test_xxx.py  # 单个文件
uv run ruff check                 # Lint
uv run pyright                   # 类型检查
```

### 添加新插件

**目录插件**：

```python
# plugins/my_tool/plugin.py
from kernel.types import AmadeusPlugin
from services.tools.base import Tool

class MyTool(Tool):
    name = "my_tool"
    description = "我的工具"
    # ...

class MyToolPlugin(AmadeusPlugin):
    name = "my_tool"
    description = "我的工具插件"
    version = "1.0.0"
    priority = 10

    def register_tools(self):
        return [MyTool()]
```

放到 `plugins/<name>/plugin.py`，并补齐 `plugin.json / config.default.json / config.schema.json` 后，Bot 重启时 `discover_plugins()` 自动发现。

创建目录骨架：

```bash
mkdir -p plugins/my_plugin
touch plugins/my_plugin/plugin.py
```

可选添加 `plugin.json` 覆盖元数据：

```json
{
    "name": "my_plugin",
    "version": "1.0.0",
    "priority": 50,
    "enabled": true,
    "dependencies": {"chat": ">=1.0.0"}
}
```

然后在 `plugin.py` 中继承 `AmadeusPlugin`，实现需要的钩子即可。

### 可用钩子

| 钩子 | 签名 | 用途 |
|------|------|------|
| `on_startup` | `(ctx: PluginContext)` | 初始化资源 |
| `on_shutdown` | `(ctx: PluginContext)` | 清理资源 |
| `on_bot_connect` | `(ctx: PluginContext, bot)` | Bot 连接后 |
| `on_message` | `(ctx: MessageContext) -> bool` | 消息到达，返回 True 消费 |
| `on_thinker_decision` | `(ctx: ThinkerContext)` | Thinker 决策后 |
| `on_pre_prompt` | `(ctx: PromptContext)` | 追加 prompt block |
| `on_post_reply` | `(ctx: ReplyContext)` | 回复后副作用 |
| `register_tools` | `() -> list[Tool]` | 注册工具 |
| `register_commands` | `() -> list[Command]` | 注册命令 |
| `register_admin_routes` | `() -> list[AdminRoute]` | 注册管理页面 |
| `on_tick` | `(ctx: PluginContext)` | 定时任务（~60s） |

## 常见问题

### Q: NapCat 扫码后一直转圈？

换用手机 QQ 扫码。如仍失败，清除浏览器缓存或换 Chrome 无痕窗口。

### Q: Bot 不说话？

按顺序检查：
1. `docker compose logs bot --tail=30` — 有没有 RED ERROR
2. NapCat WebUI → 确认 QQ 在线
3. `config/.env` 中 `LLM_API_KEY` 是否有效
4. 确认在群里 @bot

### Q: 怎么备份？

```bash
# 备份关键数据
tar czf backup-$(date +%Y%m%d).tar.gz \
  napcat/data/ \
  storage/ \
  config/
```

### Q: 怎么迁移到另一台机器？

1. 新机器上 clone 代码
2. 复制 `napcat/data/`、`storage/`、`config/`（含 `config.json/.env/soul`）
3. `docker compose up -d`
4. 如果 NapCat 要求重新扫码，在新机器上扫码即可（指指纹变了）

### Q: 能不能不用 Docker？

可以。NapCat 有桌面版，Bot 可直接 `uv run python bot.py` 本地运行。但 Docker 更方便管理，推荐使用。
