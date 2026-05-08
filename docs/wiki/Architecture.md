# 架构

## 三层模型

```
QQ ←→ NapCat (WS) ←→ NoneBot2
                      └── Omubot 框架
                           ├── Kernel:  PluginBus · 类型 · 发现
                           ├── Services: LLM · Scheduler · Timeline · Slang
                           └── Plugins: 19 个可开关插件
```

### Kernel 层 (`kernel/`)

插件总线、类型定义、插件发现。不 import 任何服务或插件。

- `PluginBus` — 钩子调度（on_startup, on_message, on_pre_prompt, on_post_reply, on_tick），依赖拓扑排序
- `router.py` — NoneBot 消息路由（私聊 priority=10，群聊 priority=1）
- `types.py` — `AmadeusPlugin`, `PluginContext`, `MessageContext`, `Command`, `CommandContext` 等接口类型

### Services 层 (`services/`)

系统服务，可互相 import，不 import 插件。

| 服务 | 文件 | 职责 |
|------|------|------|
| LLM Client | `services/llm/client.py` | Anthropic 兼容 SSE 流式调用，工具循环，上下文压缩 |
| Scheduler | `services/scheduler.py` | Debounce + Batch 双模式群聊调度 |
| Timeline | `services/memory/timeline.py` | 追加式消息时间线 + pending 缓冲区 |
| CardStore | `services/memory/card_store.py` | SQLite 记忆卡片（7 类 3 作用域） |
| StickerStore | `services/media/sticker_store.py` | SHA256 去重表情包库 |
| SlangStore | `services/slang/store.py` | 群内黑话存储、候选生命周期、修订历史、语义漂移治理 |
| SlangExtractor | `services/slang/extractor.py` | 轻量 LLM 候选抽取，不引入重型 NLP 依赖 |
| SlangDailyReviewer | `services/slang/daily_reviewer.py` | 每日 AI 搜索复核与 AI 通过标记 |
| CommandDispatcher | `services/command.py` | /slash 命令解析与路由 |
| Thinker | `services/llm/thinker.py` | 轻量 LLM 预判 (reply/wait/search) |

### Plugins 层 (`plugins/`)

19 个独立插件，只 import 内核类型 + 系统服务。黑话系统采用“服务层 + 薄插件”结构：`services/slang` 承担稳定能力，`plugins/slang` 只接消息、定时任务、Prompt 和工具注册。

## 消息流（群聊）

```
QQ 消息 → NapCat → NoneBot → router.py
  ├── EchoPlugin.on_message()    ← 复读检测 (priority=200)
  ├── CommandDispatcher.dispatch() ← /debug, /version 等
  ├── _render_message()          ← 图片描述 + 表情包解析
  ├── SlangPlugin.on_message()   ← 黑话命中与观察记录
  ├── timeline.add()             ← 写入时间线
  └── scheduler.notify()
        ├── @bot → 立即触发
        └── 普通消息 → debounce/batch
              └── thinker.think()  ← reply/wait/search
                    └── LLM.chat() ← Tool loop (max 5 rounds)
```

## 消息流（私聊）

```
私聊消息 → router.py → CommandDispatcher
  └── LLM.chat()  ← 无 thinker，直接回复
```

## Prompt 缓存

4 个断点利用 Anthropic 的 prompt caching：
1. `tools[-1]` — 工具定义
2. System block 1 — 人格 + 行为指令
3. System block 2 — 索引 + 记忆
4. `messages[near-end]` — 最近消息

黑话采用动态 PromptBlock：`SlangPlugin.on_pre_prompt()` 只注入当前群和全局已批准词条，并受 `max_injected_terms`、`max_prompt_chars`、`min_inject_confidence` 控制。v3 另注册 `slang_lookup` 工具，允许 LLM 在需要时按需查询当前群黑话，避免无限扩大常驻 Prompt。
