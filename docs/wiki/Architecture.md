# 架构

## 三层模型

```
QQ ←→ NapCat (WS) ←→ NoneBot2
                      └── Omubot 框架
                           ├── Kernel:  PluginBus · 类型 · 发现
                           ├── Services: LLM · Scheduler · Timeline
                           └── Plugins: 14 个可开关插件
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
| Timeline | `services/timeline.py` | 追加式消息时间线 + pending 缓冲区 |
| CardStore | `services/memory/card_store.py` | SQLite 记忆卡片（7 类 3 作用域） |
| StickerStore | `services/media/sticker_store.py` | SHA256 去重表情包库 |
| CommandDispatcher | `services/command.py` | /slash 命令解析与路由 |
| Thinker | `services/thinker.py` | 轻量 LLM 预判 (reply/wait/search) |

### Plugins 层 (`plugins/`)

14 个独立插件，只 import 内核类型 + 系统服务。

## 消息流（群聊）

```
QQ 消息 → NapCat → NoneBot → router.py
  ├── EchoPlugin.on_message()    ← 复读检测 (priority=200)
  ├── CommandDispatcher.dispatch() ← /debug, /version 等
  ├── _render_message()          ← 图片描述 + 表情包解析
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
