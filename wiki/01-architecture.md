# 01 — 架构概览

## 三层模型

```
┌─────────────────────────────────────────────┐
│  插件层 (Plugins)                            │
│  affection, schedule, echo, sticker, chat   │
│  可插拔、可替换、可第三方开发                    │
├─────────────────────────────────────────────┤
│  系统服务层 (System Services)                  │
│  LLM, Memory, Image, Sticker, Tools         │
│  可复用能力，对插件暴露接口                      │
├─────────────────────────────────────────────┤
│  内核层 (Kernel)                              │
│  PluginBus + 类型契约 + 配置系统                 │
│  零 I/O，零外部依赖，框架 ABI                   │
└─────────────────────────────────────────────┘
```

### 内核层

只做一件事：**调度**。PluginBus 管理插件的注册、生命周期和钩子调度。内核不 import 任何服务或插件模块，所有交互通过 Context 类型完成。

核心原则：
- **零 I/O**：不读文件、不访问网络、不操作数据库
- **类型契约**：Context 类型是各层之间的接口，改动即 ABI 变更
- **异常隔离**：单个插件崩溃不影响其他插件
- **优先级排序**：同一钩子的插件按 priority 顺序执行

### 系统服务层

封装外部资源（LLM API、SQLite、文件系统、图片处理），向上暴露接口。服务之间允许相互引用，但内核不 import 服务。

典型服务：
- `LLMClient` — Anthropic-compatible API 调用
- `PromptBuilder` — System prompt 构建与缓存
- `CardStore` — 长期记忆卡片存储
- `MessageLog` — 消息持久化
- `ImageCache` — 图片下载/缩放/缓存
- `StickerStore` — 表情包库
- `ToolRegistry` — 工具注册与分发

### 插件层

可插拔的功能模块。每个插件是一个继承 `AmadeusPlugin` 的类，通过 8 个钩子与框架交互。

## 数据流

```
QQ 消息
  │
  ▼
NapCat (WebSocket)
  │
  ▼
NoneBot2 (事件分发)
  │
  ├─→ fire_on_message(ctx)      ← 消息消费（短路）
  │     └─→ 返回 True → 停止处理
  │
  ├─→ fire_on_pre_prompt(ctx)   ← 构建 system prompt
  │     └─→ ctx.add_block(...)
  │
  ├─→ LLM 调用 + 工具循环
  │
  ├─→ fire_on_thinker_decision(ctx) ← 通知 thinker 结果
  │
  ├─→ fire_on_post_reply(ctx)   ← 回复后副作用
  │
  └─→ fire_on_tick(ctx)         ← 定时任务 (~1min)
```

## 8 个管线钩子

| 钩子 | 方向 | 返回值 | 用途 |
|------|------|--------|------|
| `on_startup` | 内核→插件 | None | 初始化资源 |
| `on_shutdown` | 内核→插件 | None | 清理资源（逆序） |
| `on_message` | 内核→插件 | bool | 消息消费（短路） |
| `on_thinker_decision` | 内核→插件 | None | 通知（只读） |
| `on_pre_prompt` | 内核→插件 | None（add_block） | 构建 prompt |
| `on_post_reply` | 内核→插件 | None | 副作用 |
| `register_tools` | 内核←插件 | list[Tool] | 注册工具 |
| `on_tick` | 内核→插件 | None | 定时任务 |

## 优先级系统

```
0         — 核心（ChatPlugin，不可卸载）
1-9       — 基础设施工具
10-49     — 业务插件（好感度、日程、记忆）
50-99     — 辅助业务
100-199   — 后台任务
200-299   — 管线拦截（echo、检测）
300+      — 第三方/实验性
```

数字越小越先执行。同优先级按注册顺序（稳定排序）。

## 设计原则

1. **内核不依赖外部**：内核只做调度，不 import 服务/插件模块
2. **插件通过 Context 通信**：插件之间不直接引用，通过共享服务通信
3. **单个插件不阻塞全局**：异常隔离，hook 出错只打日志
4. **约定优于配置**：插件是 `plugin.py` 中的 `AmadeusPlugin` 子类
5. **渐进式迁移**：新旧代码可共存，Phase 1 的 kernel 包零侵入
