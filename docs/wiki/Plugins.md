# 插件

## 插件规范化状态

当前插件体系已经进入 manifest v3 + 目录插件 + JSON 配置契约阶段：

```text
plugins/<name>/
  __init__.py
  plugin.py
  plugin.json
  config.default.json
  config.schema.json
```

根目录单文件插件已取消运行时加载。`PluginBus.discover_plugins()` 只加载 `plugins/<name>/plugin.py`；若本地索引发现旧 `plugins/<name>.py` 或根目录 `<name>.json`，会标记为 `blocked: legacy_single_file_unsupported`，不会进入运行时。

运行时配置固定从 JSON 合并：

```text
plugins/<name>/config.default.json
storage/plugins/config/<name>.json
```

合并顺序为 `config.default.json` → Admin 保存的 runtime override → 环境/启动禁用项。旧 `plugins/*.toml` 与 `plugins/*/plugin.toml` 已不再作为主配置路径。

标准配置文件格式：

```json
{
  "schema_version": 1,
  "plugin": "sticker",
  "values": {}
}
```

`config.schema.json` 只描述 `values` 内字段，Admin Web 会根据后端返回的 `effective_values` 渲染配置表单。

## Manifest v3

所有插件清单统一使用 `plugin.json`：

```json
{
  "manifest_version": 3,
  "name": "slang",
  "display_name": { "zh": "群内黑话", "en": "Slang" },
  "description": "群内黑话：学习候选、审核后注入当前群语境",
  "version": "0.1.0",
  "tier": "system|user",
  "toggle_policy": "locked|runtime|restart_required",
  "category": "core|memory|expression|tool|pipeline|ops",
  "permissions": [],
  "capabilities": [],
  "min_omubot_version": "",
  "config": {
    "defaults": "config.default.json",
    "schema": "config.schema.json",
    "apply_mode": "hot|restart_required|read_only",
    "restart_required_fields": []
  },
  "store": {
    "visibility": "internal|local|marketplace_ready",
    "marketplace_id": ""
  }
}
```

系统级插件会被运行时锁定，Web 端和 API 都不能关闭。当前系统锁定能力包为 `chat`、`context`、`history_loader`、`vision`。插件中心默认隐藏系统能力，需要通过“显示系统插件”高级入口查看。

## 当前本地包清单（23 个）

| 插件包 | 版本 | 层级 | 启停策略 | 类别 | 功能 |
| --- | --- | --- | --- | --- | --- |
| `chat` | 1.1.25 | system | locked | core | 核心聊天：消息路由、LLM 调用、tool loop |
| `context` | 0.1.9 | system | locked | core | 统一上下文：memory/doc/graph 检索与动态 Prompt 打包 |
| `history_loader` | 1.1.2 | system | locked | core | 启动时加载群历史消息 |
| `vision` | 1.1.2 | system | locked | core | 图片描述能力；角色识别链路通过该系统能力接线 |
| `memo` | 1.1.5 | user | runtime | memory | 记忆卡片：7 类 3 作用域，检索门控与工具 |
| `knowledge` | 0.1.5 | user | runtime | memory | 文档知识库：Markdown 扫描、持久索引、检索调试 |
| `calendar_context` | 1.0.0 | user | runtime | memory | 日期上下文：节日/日历等时间语境 |
| `affection` | 1.1.2 | user | runtime | memory | 好感度系统：分数、昵称、态度调节 |
| `schedule` | 1.1.5 | user | runtime | memory | 模拟日程与心情状态 |
| `slang` | 0.1.17 | user | runtime | expression | 群内黑话：候选、审核、AI 复核、backlog、漂移治理 |
| `style` | 1.0.0 | user | runtime | expression | 表达学习：表达样本、动态风格档案、Prompt 注入 |
| `sticker` | 1.1.6 | user | runtime | expression | 表情包：保存、发送、管理与 OCR / 轻量语义检索 |
| `echo` | 1.1.2 | user | runtime | expression | 复读检测：5 分钟内同消息 3 次触发 |
| `web_search` | 1.1.1 | user | runtime | tool | 网页搜索，用于实时信息和 AI 复核 |
| `web_fetch` | 1.1.1 | user | runtime | tool | 网页内容抓取 |
| `datetime` | 1.1.1 | user | runtime | tool | 时间日期查询工具 |
| `http_api` | 1.1.1 | user | runtime | tool | 通用 HTTP API 调用 |
| `group_admin` | 1.1.1 | user | runtime | tool | 群管理工具（禁言、头衔、发消息） |
| `food` | 0.1.6 | user | runtime | tool | 饮食/点餐相关指令 |
| `bilibili` | 1.1.4 | user | runtime | tool | B 站链接解析与封面摘要 |
| `element_detector` | 1.1.3 | user | runtime | pipeline | 特殊消息元素检测 |
| `dream` | 1.1.3 | user | runtime | ops | 梦境整合：定期整理记忆、清理表情包 |
| `debug_commands` | 1.3.1 | user | runtime | ops | `/plugins`、`/version` 等调试指令 |

说明：

- “23 个”指本地 `plugins/*/plugin.json` 包/能力包数量。
- 日常可启停的是 19 个 user/runtime 插件。
- `vision` 与 `context` 属于系统能力包，不进入普通启停流。

## 本地插件索引与治理

Admin 插件页不只看“已加载插件”，还会额外扫描本地 `plugins/` 目录，生成一份仅本地可见的插件包索引，用来排查：

- 本地目录里有插件包，但没有被加载进运行时。
- `plugin.json` 缺失或损坏。
- 插件最低版本要求与当前 Omubot 版本不兼容。
- 插件入口来自符号链接或仓库外路径，需要人工确认来源。
- `plugin.sig` 声明与当前入口或 manifest hash 不一致。

索引接口：

- `GET /api/admin/plugins/index`
- `GET /api/admin/plugins/store`

返回内容包含：

- `summary`：本地包总数、已加载数、未加载数、阻塞数、待确认数、可接入数。
- `entries[]`：入口路径、清单路径、`plugin.sig` 路径、SHA256 指纹、来源状态、签名状态、版本兼容状态、治理状态和行动建议。
- `install_policy`：明确当前策略是 `local_only`。

可选的 `plugin.sig` 是本地 detached attestation 预留，不是远程安装机制。当前先支持：

- `scheme: "sha256"`
- `entry_sha256`
- `manifest_sha256`
- `signer` / `key_id` / `signed_at`
- `source.origin`
- `source.entry_path`

治理状态固定为：

| 状态 | 含义 |
| --- | --- |
| `healthy` | 已加载且没有额外治理告警 |
| `attention` | 已加载，但仍需要补清单、确认来源或处理版本告警 |
| `ready` | 本地包可读，尚未接入运行时 |
| `review` | 本地包来源需要人工确认 |
| `blocked` | 入口缺失、清单损坏或版本不兼容，当前不应接入运行时 |

当前策略明确为“只识别本地插件包，不允许 Web 端远程下载安装并执行未知代码”。这是 Omubot 插件生态的安全边界。

## Admin 插件中心

`/admin/plugins` 是插件中心主入口：

- `用户插件`：默认入口，只展示可日常管理的用户插件，支持中文名、英文名和插件 ID 搜索。
- `显示系统插件`：弱化高级入口，展示系统级锁定能力，固定标明“系统级 / 锁定 / 不可关闭”，不渲染启停开关。
- `插件商店`：只读展示本地包、来源、manifest、兼容状态和未来市场字段。
- `治理队列`：集中展示缺清单、来源待确认、版本不兼容、签名异常等问题。

插件详情页为 `/admin/plugins/<name>?tab=overview|settings|commands|health|source`，左上角有“返回插件中心”。若插件声明 `config.schema.json`，Web 会渲染结构化配置表单，并把覆盖写入 `storage/plugins/config/<name>.json`；对象数组类配置会按字段渲染，不再退回裸 JSON 文本框。

## 钩子生命周期

```text
on_startup → 加载配置，注册工具
     ↓
on_message → 每个消息（可拦截返回 True）
     ↓
on_pre_prompt → 注入 system prompt 块（dynamic / stable / static）
     ↓
LLM 工具循环 → 插件注册的工具可被 LLM 调用
     ↓
on_post_reply → 回复后的副作用（记录好感度、表达反馈等）
     ↓
on_tick → 定时触发（Dream、Slang reviewer 等）
```

## 工具注册

插件通过 `register_tools()` 返回 Tool 列表：

```python
class MyPlugin(AmadeusPlugin):
    def register_tools(self) -> list[Tool]:
        return [MyTool(), AnotherTool()]
```

当前常见工具包括：

| 工具 | 来源插件 | 用途 |
|------|----------|------|
| `web_search` | `web_search` | 搜索互联网，用于实时信息和黑话 AI 复核 |
| `web_fetch` | `web_fetch` | 抓取网页内容 |
| `send_sticker` | `sticker` | 按场景发送表情包 |
| `lookup_cards` / `append_memo` | `memo` | 查询和写入长期记忆卡片 |
| `slang_lookup` | `slang` | 按需查询当前群与全局已批准黑话 |

`slang_lookup` 只返回当前群和 global 作用域的 `approved` 词条；无群上下文时只返回 global。

## 命令注册

插件通过 `register_commands()` 声明式注册命令。`CommandDispatcher` 自动处理权限门禁、参数校验、未知子命令检测和帮助文本生成。

```python
from kernel.types import Command

def register_commands(self) -> list:
    return [
        Command(
            name="mycmd",
            handler=self._handle_mycmd,
            description="我的命令",
            usage="/mycmd <参数>",
            aliases=["mc"],
            require_args=True,
        ),
    ]
```

门禁字段：

| 字段 | 效果 |
|------|------|
| `admin_only=True` | 非管理员自动回复“无权限” |
| `private_only=True` | 群聊自动回复“请在私聊中使用此指令” |
| `require_args=True` | 无参数时自动回复 `usage` |
| `hidden=True` | 在 `format_help()` 中隐藏 |
| `passthrough_unknown=True` | 未知子命令不报错，透传给父 handler |

完整示例参见 [FoodPlugin](../../plugins/food/plugin.py)。
