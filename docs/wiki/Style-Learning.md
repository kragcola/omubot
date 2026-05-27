# 表达学习

表达学习用于让 Omubot 学习“在某类场景里怎么说话”，例如短句节奏、接话方式、夸奖/吐槽/安慰的表达习惯。它不负责解释词义，也不自动改写核心人设。

## 职责边界

| 能力 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| 记忆卡片 | 用户偏好、群内事实、长期互动记录 | 说话风格模板 |
| 群内黑话 | 群内词义、梗、别名、语境解释 | 回复节奏和表达方式 |
| 表达学习 | 场景化说法、接话节奏、动态风格档案 | 自动修改 `config/persona/<id>/source.md` |

表达学习可以记录风险表达，但运行时必须按人设转译。核心原则是“理解真实语言生态，但不照抄，不改变凤笑梦核心人设”。

## 当前实现

```text
services/style  →  表达样本、证据、反馈、动态风格档案
plugins/style   →  Prompt 注入与 bot 回复弱信号采集
admin/style     →  表达学习控制台
```

当前状态：

- Phase 0-6 初版已完成，等待人工端到端验收。
- `/admin/style` 可查看样本、证据、档案、反馈和摘要指标。
- 手动抽取优先使用 `ConversationArchive` scanner cursor；archive 不可用时退回最近消息窗口。
- 动态风格档案可生成、启用、禁用、回滚。
- bot 回复只采集中性弱信号，不会无条件把自己的回复学成风格。

## 数据模型

表达学习使用独立 SQLite 存储，核心对象包括：

| 对象 | 说明 |
| --- | --- |
| 表达样本 | `situation` + `style`，带 scope、状态、置信度、计数 |
| 证据 | 来源消息、speaker、上下文片段、human/bot 来源类型 |
| 修订记录 | 审核、反馈、档案生成、启停和回滚留痕 |
| 反馈 | positive/negative/neutral 信号与原因 |
| 动态风格档案 | 按群或 global 汇总出的短 Prompt profile |

作用域：

- 默认 `scope=group`，按群隔离。
- `global_enabled_group_ids` 中的群可共同参与 global 表达池。
- evidence 继续记录真实来源群，便于审计。

## 运行流程

### 手动抽取

1. 管理员在 `/admin/style` 触发抽取。
2. 后端按 `style_manual_extract` scanner cursor 增量扫描消息。
3. `StyleExtractor` 抽取“当 X 时，可以 Y”的表达候选。
4. 与黑话命中重叠的候选会被过滤，避免把词义当表达。
5. 候选写入 pending 或在显式允许时自动 approved。

### Prompt 注入

`StylePlugin.on_pre_prompt()` 会注入少量相关 approved 表达：

```text
【表达习惯参考】
以下只用于调整本轮说话方式，不要照抄，不要改变核心人设。
- 当大家轻松吐槽时，可以用短促附和 + 一点明亮反应。
```

默认限制：

- `max_items=3`
- `max_chars=800`
- `min_confidence=0.45`
- `observe_only` 不注入
- 带 `risk_tags` 的表达强制提示转译

### 动态风格档案

管理员可从 approved 表达生成短风格档案：

- 档案不会写入 `identity.md` 或 `instruction.md`。
- 档案有 version/status，可启用、禁用、回滚。
- 档案 Prompt 明确不能改变核心人设。
- 每次生成和切换都会留下反馈/修订记录。

## Admin Web

入口：`/admin/style`

页面能力：

- 摘要：样本数、待审数、档案版本、反馈指标。
- 样本队列：待审、已通过、已拒绝、已静音。
- 证据与修订：查看来源消息、风险标签、输出策略。
- 手动抽取：按群扫描并展示 scanned/extracted/saved。
- 档案管理：生成、启用、禁用、回滚。
- 反馈：对表达或 bot 回复标记好/坏。

主要 API：

| 接口 | 说明 |
| --- | --- |
| `GET /api/admin/style/summary` | 表达学习摘要 |
| `GET /api/admin/style/expressions` | 表达样本列表 |
| `POST /api/admin/style/expressions/{id}/approve` | 通过样本 |
| `POST /api/admin/style/expressions/{id}/reject` | 拒绝样本 |
| `POST /api/admin/style/expressions/{id}/mute` | 静音样本 |
| `POST /api/admin/style/extract/run` | 手动抽取表达候选 |
| `GET /api/admin/style/profiles` | 动态风格档案列表 |
| `POST /api/admin/style/profiles/generate` | 生成档案 |
| `POST /api/admin/style/profiles/{id}/enable` | 启用档案 |
| `POST /api/admin/style/profiles/{id}/disable` | 禁用档案 |
| `POST /api/admin/style/profiles/rollback` | 回滚上一版档案 |

## 配置

配置来源：

```text
plugins/style/config.default.json
storage/plugins/config/style.json
```

常用项：

| 设置 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用表达学习 Prompt 注入 |
| `max_items` | `3` | 每轮最多注入表达参考条数 |
| `max_chars` | `800` | 表达参考块最大字符数 |
| `min_confidence` | `0.45` | 注入表达样本最低置信度 |
| `profile_enabled` | `true` | 是否启用动态风格档案 |
| `profile_max_chars` | `900` | 风格档案最大字符数 |
| `collect_bot_replies` | `true` | 是否采集 bot 回复弱信号 |
| `global_enabled_group_ids` | `[]` | 参与 global 表达池的群 ID |

## 验收重点

- 抽取出的内容是表达习惯，不是黑话词条、事实记忆或人设改写命令。
- 启用档案后，bot 更贴近群节奏，但仍像凤笑梦。
- 禁用档案后，回复回到 persona v2 prompt + memory + slang + approved 表达参考。
- 风险表达可以被学习，但输出必须转译，不能原样复刻。
- 默认群隔离符合预期；只有配置的群读取 global 表达池。

## 激活清单

首次启用表达学习数据采集的步骤：

1. 确认 `plugins/style/plugin.json` 的 `permissions` 包含 `"reply"`（v0.1.0+ 已默认包含）。
2. 确认 `storage/plugins/config/style.json` 中 `collect_bot_replies: true`（默认已开启）。
3. 如需跨群共享表达池，在 `global_enabled_group_ids` 中填入目标群 ID。
4. 重启 bot（`docker compose restart bot`）。
5. 在 `/admin/style` 触发一次手动抽取，确认 scanned > 0。
6. 等待 bot 回复后，在 `/admin/style` 的反馈列表中确认弱信号出现。
