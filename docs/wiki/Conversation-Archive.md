# 对话归档

ConversationArchive 是 Omubot 的本地对话归档底座，用来把历史消息、增量扫描游标、扫描审计、证据引用和留存策略收口到一个可审计的系统服务。

它不是聊天业务插件，也不是长期记忆卡片；它是黑话、表达学习、统计、压缩和未来留存清理共同依赖的底层消息事件流。

## 当前状态

- Phase 0-4 原语已完成，等待人工端到端验收。
- 首期兼容现有 `MessageLog.query_recent()` 行为，不改变热路径回复语义。
- 黑话/表达抽取优先使用 scanner cursor 增量扫描。
- 清理只提供 dry-run 原语，物理删除仍需人工验收后再打开。
- 证据引用可从业务 evidence 重建，避免清理前丢失审计来源。

## 为什么需要归档底座

旧 `MessageLog` 主要提供 `group_messages` 最近窗口：

```text
record()
query_recent(group_id, limit)
query_for_compact(group_id, before=...)
list_group_ids()
```

这对聊天可用，但对后台学习任务有几个问题：

- 黑话和表达抽取每次重复扫最近窗口。
- 手动抽取多次触发会重复消费同一批消息。
- 清理历史消息前无法确认所有必需 scanner 已消费。
- 业务 evidence 和原始消息之间缺少统一引用表。

ConversationArchive 的目标是引入 message stream + scanner cursor，让后台任务可增量、可审计、可恢复。

## 核心表

首期只实现 5 张核心表：

| 表 | 说明 |
| --- | --- |
| `conversation_messages` | 原始消息事件流，带 `message_pk`、chat 类型、legacy group id、平台消息 id |
| `conversation_scan_cursors` | 每个 scanner 在每个 chat 上的消费进度 |
| `conversation_scan_runs` | 扫描运行审计：扫了多少、保存多少、为什么失败 |
| `conversation_retention_policies` | 按群/私聊配置留存策略，默认不物理删除 |
| `conversation_message_refs` | 业务 evidence 到原始消息的引用 |

暂不把 segment、词频统计、备忘录业务表塞进底座。业务表继续留在各自服务中。

## Scanner Cursor

scanner cursor 以 `(scanner_name, chat_type, chat_id, scope_key)` 为主键。

当前使用者：

| Scanner | 用途 |
| --- | --- |
| `slang_manual_extract` | 黑话手动抽取 |
| `style_manual_extract` | 表达学习手动抽取 |

游标使用 `message_pk` 作为主进度，并保留 `last_created_at` 和小窗口回看。`message_pk` 是全局稀疏序列，不是每群连续编号，所以扫描必须按当前 chat 取下一批消息，不能用 `last_pk + limit` 推断范围。

如果 scanner 版本或参数变化，不会自动全量重扫；cursor 会进入需要人工重扫的状态，避免一次配置变更触发高成本历史扫描。

## 证据引用

`conversation_message_refs` 用于把业务 evidence 和原始消息关联起来：

- 黑话 observation / AI review evidence。
- 表达样本 evidence。
- 未来知识图谱或统计任务的来源消息。

清理前不能只看 refs 表。因为早期业务数据可能尚未写 refs，系统需要能从黑话/表达等业务 evidence 重建或校验证据引用。

## 留存策略

首期默认策略：

- `cleanup_enabled=false`
- `keep_raw_forever=true`
- 不物理删除 `group_messages` 或 `conversation_messages`

dry-run 会检查：

- 必需 scanner 是否都有 cursor。
- cursor 是否 active，是否存在 `needs_rescan`。
- 业务 evidence 是否能建立安全引用。
- 当前策略下理论可清理多少 raw row。

只有 dry-run 和人工验收稳定后，才应讨论物理删除。

## 与现有 MessageLog 的关系

当前 `MessageLog` 仍兼容旧接口：

- Admin 最近消息继续可读。
- 私聊仍可通过 `session:<id>` legacy group id 兼容。
- 对话压缩仍可读取旧历史窗口。
- 黑话/表达抽取优先走 archive cursor，失败时退回最近窗口。

这意味着 ConversationArchive 是渐进式底座，不是一次性替换所有消息读取路径。

## 运维注意

- `messages.db` 使用 DELETE journal，方便宿主机和 Docker 侧审计 SQLite 状态。
- 清理前必须先跑 dry-run，不要直接删除 raw rows。
- 新增学习类 scanner 时，应先登记 cursor 与 scan run，再考虑参与留存安全水位。
- 如果 Admin 显示 cursor 需要重扫，应人工选择范围，不应让系统自动全量重扫历史。
