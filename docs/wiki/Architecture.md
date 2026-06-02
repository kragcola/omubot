# 架构

## 三层模型

```text
QQ ←→ NapCat (WS/HTTP) ←→ NoneBot2
                         └── Omubot
                              ├── Kernel:  PluginBus · 类型契约 · 插件发现
                              ├── Services: LLM · Scheduler · Memory · Knowledge · Slang · Style · Archive
                              └── Plugins: 23 个本地包/能力包
```

当前插件层包含 19 个用户运行时插件，以及 4 个系统锁定能力包：`chat`、`context`、`history_loader`、`vision`。系统级能力由 manifest v3 标记为 `tier=system`、`toggle_policy=locked`，Web 和 API 都不能关闭。

## 运行拓扑

```text
QQ <-> NapCat <-> bot(NoneBot2 + Omubot)
                    ├── Admin API / SPA
                    ├── storage/*.db
                    └── CharacterRecognizer -> ccip-sidecar

pmubot（可选） -> socket-proxy / watchtower / docker compose control plane
```

当前默认运行栈除了 `napcat` 与 `bot`，还包含：

- `ccip-sidecar`：角色识别与角色包构建服务；
- `pmubot`：多 bot / 运维控制平面；
- `socket-proxy*` + `watchtower`：容器级读写与更新支撑。

### Kernel 层 (`kernel/`)

插件总线、类型定义、配置加载、插件发现。不 import 任何服务或插件。

- `PluginBus` — 钩子调度（`on_startup`、`on_message`、`on_pre_prompt`、`on_post_reply`、`on_tick` 等），按 priority 稳定排序，单插件异常隔离。
- `router.py` — NoneBot 消息路由，串联命令、群聊调度、私聊直连和回复发送。
- `types.py` — `AmadeusPlugin`、`PluginContext`、`MessageContext`、`PromptContext`、`Command`、`Tool` 等接口类型。
- `config.py` — `BotConfig`、LLM profiles、群访问策略、插件配置加载和 JSON/TOML 兼容。
- `manifest.py` — manifest v3、本地插件索引、版本兼容、治理状态与签名校验字段。

### Services 层 (`services/`)

系统服务可互相 import，不 import 插件。插件通过 `PluginContext` 获取服务引用。

| 服务 | 文件/目录 | 职责 |
|------|-----------|------|
| LLM Client | `services/llm/client.py` | 多 Provider 调用、SSE 流、工具循环、上下文压缩 |
| Provider/Profile | `services/llm/` + `kernel/config.py` | 任务级 profile 解析、DeepSeek/OpenAI/Anthropic 兼容 |
| Scheduler | `services/scheduler.py` | 群聊 debounce + batch 调度、并发与发送节奏 |
| Reply Workflow | `services/reply_workflow.py` | 回复门控、分段、发送队列与链路编排 |
| Timeline/MessageLog | `services/memory/` | 群消息持久化、近期窗口、短期状态与压缩输入 |
| ConversationArchive | `services/conversation_archive/` | 消息事件流、scanner cursor、scan run、证据引用、留存 dry-run |
| CardStore | `services/memory/card_store.py` | SQLite 记忆卡片（7 类 3 作用域） |
| Knowledge | `services/knowledge/` | Markdown 文档索引、SQLite 持久索引、BM25/ngram 检索 |
| Knowledge Graph | `services/knowledge_graph/` | 从记忆/文档派生实体关系事实与证据链 |
| StickerStore | `services/media/sticker_store.py` | SHA256 去重表情包库 |
| Vision / Character Recognition | `services/media/` | Qwen VL 图片描述、角色注册表、识别缓存、sidecar HTTP client |
| Slang | `services/slang/` | 黑话存储、候选生命周期、AI 复核、backlog reviewer、漂移治理 |
| Style | `services/style/` | 表达样本、证据、反馈、动态风格档案与回滚 |
| Learning Normalizer | `services/learning_normalizer/` | 黑话/表达抽取前的低信号与边界规范化 |
| Group / Reply Planning | `services/group/`、`services/reply_planner/`、`services/scheduler_*` | 多话题理解、回复调度、人性化探针与 replay/RWS 分支 |
| Admin Events | `services/admin_events.py` | SSE 事件流、运行态通知 |
| Health/Trace | `admin/routes/api/` + services | 服务健康、协议连接、trace、runtime error store |

### Plugins 层 (`plugins/`)

插件只 import 内核类型和系统服务。运行时发现只加载 `plugins/<name>/plugin.py` 目录插件；根目录单文件插件只会进入本地索引治理队列并标记为 legacy，不再作为可加载插件。

重点插件关系：

- `chat` 负责核心聊天入口、LLM 调用和 tool loop。
- `context` 负责统一动态上下文，把 memory/doc/graph 打包为 `上下文资料`。
- `memo` 继续负责记忆卡片 CRUD 和工具，但默认不再重复直接注入动态 Prompt。
- `knowledge` 负责文档知识源扫描和检索；生产聊天默认扫描 `docs/knowledge`。
- `slang` 是黑话薄插件，业务逻辑在 `services/slang`。
- `style` 是表达学习薄插件，业务逻辑在 `services/style`。

## 消息流（群聊）

```text
QQ 消息 → NapCat → NoneBot → router.py
  ├── 访问策略 / presence_mode 解析
  ├── CommandDispatcher.dispatch()        ← /debug, /version, /plugins 等
  ├── _render_message()                   ← 图片描述 + 表情包解析 + 角色识别
  ├── EchoPlugin.on_message()             ← 复读检测
  ├── SlangPlugin.on_message()            ← 黑话命中与观察记录
  ├── MessageLog / ConversationArchive    ← 原始消息与归档事件流
  └── scheduler.notify()
        ├── @bot / active group → 触发回复
        └── 普通消息 → debounce/batch
              └── thinker / reply_gate
                    └── LLM.chat()
                          ├── ContextPlugin.on_pre_prompt()  ← memory/doc/graph
                          ├── SlangPlugin.on_pre_prompt()    ← 当前群黑话
                          ├── StylePlugin.on_pre_prompt()    ← 表达习惯参考
                          └── Tool loop
```

未获得主动发言权限的群会按 `presence_mode` 处理：`active` 可回复，`silent_learn` 只允许显式开启的学习能力读取，`off` 完全忽略群聊。

## 消息流（私聊）

```text
私聊消息 → router.py → CommandDispatcher
  └── LLM.chat()  ← 无群聊 thinker 调度，直接回复
```

## 视觉与角色识别

当前图片链路不再只有 VL 描述：

1. `router.py` 会先做图片下载、缓存和基础富描述。
2. 若 `vision.character_recognition.enabled=true`，bot 会调用 `CharacterRecognizer`。
3. `CharacterRecognizer` 通过 HTTP 请求 `ccip-sidecar` 的 `/identify` 或 `/identify-multi`。
4. sidecar 返回候选角色后，bot 本地再补齐 `relation`、`name`、`work`、`context_label`。
5. 结果会写入 `storage/character_recognition.db` 的缓存表，并参与最终 prompt 渲染。

角色 embedding 与样例属于 `config/character_packs/*.charpack/`；`self/friend/known` 则是 per-bot 关系语义，保存在 bot 本地 registry。

## Prompt 与上下文

### LLMRequest spine — 统一调用契约

所有 LLM 调用必须构造 `LLMRequest`（定义在 `services/llm/llm_request.py`），通过 `LLMClient._call()` 进入统一调度。`LLMRequest` 将 system prompt 分为三段：

| 段 | 字段 | 语义 | 缓存特性 |
| ------ | ------ | ------ | ------ |
| static | `static_blocks` | 跨所有调用不变（身份 prompt、共享前缀） | 最高缓存优先级 |
| stable | `stable_blocks` | 偶尔变化（群 profile、插件 stable 块） | 中等缓存优先级 |
| dynamic | `dynamic_blocks` | 每轮变化（心情、好感度、当前时间） | 不缓存 |

三段按固定顺序拼接（static → stable → dynamic），调用方无法重排。

### 缓存断点注入（spine 单一来源）

Anthropic prompt cache 限制每个请求最多 4 个 `cache_control: ephemeral` 标记。Spine 是唯一注入点——调用方预先放在 dict 块上的 `cache_control` 会被剥离，由 `apply_cache_breakpoints()` 按 per-task profile 重新注入。

每个 `LLMTask` 在 `TASK_CACHE_PROFILES` 中有显式配置；下面只示意代表性任务，完整列表以 `services/llm/llm_request.py` 为准：

```python
TASK_CACHE_PROFILES = {
    "main": TaskCacheProfile(system_breakpoints=3, message_breakpoint=True),
    "thinker": TaskCacheProfile(system_breakpoints=2),
    "slang": TaskCacheProfile(system_breakpoints=2),
    "memo": TaskCacheProfile(system_breakpoints=1),
    # ... style / persona_import / birthday_wish / graph_review / episode_review ...
}
```

断点预算计算：`system_budget = min(profile.system_breakpoints, 4 - tools_slot - message_slot)`

放置策略（outer-first, end-of-segment）：按 static → stable → dynamic 顺序，在每段的最后一个 block 上打标记。Static 总是占第一槽（变化最少），dynamic 在预算收紧时先牺牲。

新插件只需 `LLMRequest(task=”my_task”)` 即自动享受缓存策略，无需任何 cache 相关代码。

### 动态上下文注入

动态上下文现在优先由 `ContextPlugin` 接管：

- `memory_card` 来自 `CardStore`。
- `doc_chunk` 来自文档知识库。
- `graph_fact` 来自知识图谱。
- 统一打包为 `上下文资料`，避免 `MemoPlugin` 与 `KnowledgePlugin` 重复注入。

黑话与表达学习仍是独立动态块：

- 黑话解释”这个词是什么意思”，并可通过 `slang_lookup` 按需查询。
- 表达学习提示”这个场景通常怎么说”，不替代人设，不照抄群友原话。

## Admin 架构

Admin API 位于 `admin/routes/api/`，前端位于 `admin/frontend/src/`。当前主控台已统一 Calm Ops / 雾青控制台风格，覆盖 Dashboard、Logs、Groups、Login、Config、System、Slang 等页面。

管理端关键能力：

- 配置：结构化表单、保存前 diff、审计记录、快照回滚。
- 系统：服务健康、LLM Provider、协议连接、trace、runtime error store、阈值告警。
- 群管理：群 profile、访问策略、工具 allow/block、presence mode、审计记录。
- 插件中心：用户插件、系统能力、本地插件包索引、治理队列。
- 角色识别：角色列表、系列聚合、pack 构建、角色包合并、缓存命中与 sidecar 健康。
- 学习管线：黑话、表达、memory、episode 等统一观察与处理入口。
- Slang/Style/Knowledge：黑话治理、表达学习、知识库调试与评测。
