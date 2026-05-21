# Omubot ConversationArchive 本地对话归档改造跟踪

本文用于长期跟踪 Omubot 本地对话归档、增量扫描游标、留存策略和证据保护的设计、实施、验收与风险。目标是把当前散落在 `MessageLog.query_recent()` 上的黑话、表达学习、状态板、压缩等读取路径，逐步收口到一个可审计、可增量扫描、可安全清理的系统服务底座。

## 当前状态

- 状态：Phase 0 已完成；Phase 1 已完成；Phase 2 backfill 原语已完成；Phase 3 已完成；Phase 4 dry-run 原语已完成；等待人工端到端验收
- 当前阶段：Phase 3 - 黑话/表达抽取 cursor 迁移已完成，进入人工验收
- 核心目标：新增系统服务级对话归档底座，首期只做消息事件流、扫描游标、扫描审计、留存策略、证据引用
- 默认路线：先兼容现有 `MessageLog` 行为，黑话/表达抽取扫描优先走 cursor；清理先 dry-run，长期稳定后再讨论物理删除
- 暂不改动：NapCat、当前运行时行为、现有 `group_messages` 表物理删除、黑话/表达业务语义、私聊备忘录业务表、群词频统计业务表

## 当前现状

当前 `MessageLog` 是 `services/memory/message_log.py` 中的单表 SQLite 服务，核心表为 `group_messages`：

```text
id INTEGER PRIMARY KEY AUTOINCREMENT
group_id TEXT
role TEXT
speaker TEXT
content_text TEXT
content_json TEXT
message_id INTEGER
created_at REAL
```

现有接口：

- `record()`：写入群聊/助手消息。
- `query_recent(group_id, limit)`：按 `created_at DESC LIMIT` 取最近窗口，再反转为时间正序。
- `list_group_ids()`：列出非 `session:%` 的群。
- `record_session_msg()`：把私聊写成 `group_id="session:<session_id>"`。
- `query_for_compact(group_id, before=...)`：给压缩读取指定时间前的历史。

当前主要消费者：

| 消费者 | 当前读取方式 | 主要问题 |
| --- | --- | --- |
| 黑话 daily review | `query_recent()` | 每次复核重复扫描最近窗口，缺少已扫描进度 |
| 黑话手动抽取 | `query_recent()` | 手动多次触发会重复扫描同一批消息 |
| 表达学习手动抽取 | `query_recent()` | 与黑话一样缺少增量游标 |
| 状态板 | `query_recent()` | 只需要近期状态，不应阻塞历史清理 |
| 群消息 Admin API | `query_recent()` | 查看最近消息即可，需兼容 |
| 私聊上下文 | `query_recent("session:<id>")` | 私聊与群聊复用 `group_id` 前缀约定 |
| 对话压缩 | `query_for_compact()` | 按时间读取历史，当前不物理删除 raw rows |
| health | `COUNT(*) FROM group_messages` | 随数据增长会变慢，可后续改为归档统计 |

注意：当前正常压缩链路只读取原文生成摘要，不删除 `group_messages`；但 Admin 黑话调试路径存在直接 `DELETE FROM group_messages` 的例外。后续文档和实现应避免写成“仓库内绝对无删除”，应表述为“正常归档链路首期不物理删除”。

## 参考资料

| 类型 | 名称 | 本轮启发 |
| --- | --- | --- |
| 本地代码 | Rasa `/private/tmp/omubot-archive-research-20260512/rasa` | tracker store 使用事件序列与事件 ID 回放状态，适合增量 cursor |
| 本地代码 | Synapse `/private/tmp/omubot-archive-research-20260512/synapse` | `stream_ordering`、房间级 retention purge job、按房间避免并发清理 |
| 本地代码 | Mattermost `/private/tmp/omubot-archive-research-20260512/mattermost` | 全局/团队/频道 retention policy 与批量删除任务 |
| 本地代码 | Zulip `/private/tmp/omubot-archive-research-20260512/zulip` | message retention 先归档再清理，强调可恢复与关联对象处理 |
| 本地代码 | Letta `/private/tmp/omubot-archive-research-20260512/letta` | 消息 `sequence_id` 与 memory block 分层，避免把原文全部塞进 prompt |
| 本地代码 | LangMem `/private/tmp/omubot-archive-research-20260512/langmem` | 后台延迟处理、namespace 隔离、记忆抽取不要在热路径同步执行 |
| 论文 | Generative Agents | observation -> reflection -> retrieval，历史经历流应能被周期性反思 |
| 论文 | MemGPT / Letta | core memory 与 archival memory 分层，动态档案不等于原文库 |

参考链接：

- Rasa Tracker Store: https://rasa.com/docs/reference/integrations/tracker-stores/
- Synapse Message Retention: https://matrix-org.github.io/synapse/develop/message_retention_policies.html
- Mattermost Data Retention: https://docs.mattermost.com/administration-guide/comply/data-retention-policy.html
- Zulip Message Retention: https://zulip.com/help/message-retention-policy
- Generative Agents: https://arxiv.org/abs/2304.03442
- MemGPT: https://arxiv.org/abs/2310.08560
- Letta memory blocks: https://docs.letta.com/guides/core-concepts/memory/memory-blocks/
- LangMem delayed processing: https://langchain-ai.github.io/langmem/guides/delayed_processing/

## 核心设计决策

| 日期 | 决策 | 原因 |
| --- | --- | --- |
| 2026-05-12 | `ConversationArchive` 是系统服务，不是插件 | 消息归档是黑话、表达、统计、备忘录等多个能力的共享底座 |
| 2026-05-12 | 首期只建 5 张核心表 | 采纳审计意见，避免把 segment、词频统计、备忘录业务过早混入底座 |
| 2026-05-12 | `created_at` 继续使用 REAL epoch | 与旧 `group_messages` 兼容，降低迁移和 `query_for_compact()` 摩擦 |
| 2026-05-12 | 主游标使用 `message_pk`，辅以 `last_created_at` 和小窗口回看 | SQLite 写入顺序足够稳定；回看用于防异步晚入库和未来多写入口边界 |
| 2026-05-12 | `scanner_version` / `params_hash` 变化不自动全量重扫 | 避免一次配置变更触发高成本历史扫描；改为标记需要人工选择重扫范围 |
| 2026-05-12 | 首期清理只 dry-run，不物理删除 | 当前 MessageLog 正常链路不删原文，真实清理必须先建立可观测安全边界 |
| 2026-05-12 | 缺少必需 scanner cursor 时阻塞清理 | 新增扫描器未跑过时不能假定历史已消费 |
| 2026-05-12 | `message_refs` 不作为唯一安全来源 | 清理前还需要能从黑话/表达等业务 evidence 表重建或校验证据引用 |
| 2026-05-12 | memo 业务表拆到独立服务 | 备忘录有独立隐私、目录、任务状态语义，不应污染归档底座 schema |
| 2026-05-13 | `message_pk` 按全局稀疏序列处理 | pk 不是每群连续，cursor 扫描必须按当前 chat 的下一批消息取，不能使用 `last_pk + limit` |
| 2026-05-13 | `messages.db` 使用 DELETE journal | 避免 Docker/宿主审计出现 deleted WAL 视图分裂，保持本地 sqlite 对账一致 |

## 首期数据结构

首期只实现 5 张表。`conversation_segments`、`conversation_term_stats`、`memo_*` 均不进入 Phase 1 schema。

### `conversation_messages`

原始消息事件流，替代或包裹旧 `group_messages`。

```sql
CREATE TABLE conversation_messages (
    message_pk          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_type           TEXT    NOT NULL,  -- group / session
    chat_id             TEXT    NOT NULL,  -- group_id 或 session_id，不带 session: 前缀
    legacy_group_id     TEXT    NOT NULL,  -- 兼容旧接口：群号或 session:<id>
    role                TEXT    NOT NULL,
    speaker             TEXT,
    content_text        TEXT,
    content_json        TEXT,
    platform_message_id INTEGER,
    created_at          REAL    NOT NULL,
    ingested_at          REAL    NOT NULL,
    meta_json           TEXT
);
```

首期索引：

```sql
CREATE INDEX idx_conv_msg_chat_time
    ON conversation_messages(chat_type, chat_id, created_at);
CREATE INDEX idx_conv_msg_chat_pk
    ON conversation_messages(chat_type, chat_id, message_pk);
CREATE INDEX idx_conv_msg_legacy_time
    ON conversation_messages(legacy_group_id, created_at);
CREATE INDEX idx_conv_msg_platform
    ON conversation_messages(chat_type, chat_id, platform_message_id);
```

说明：

- `legacy_group_id` 用于兼容 `query_recent("123")` 和 `query_recent("session:abc")`。
- `platform_message_id` 允许为空；私聊 `record_session_msg()` 目前没有平台 message id。
- 暂不设置平台消息唯一约束，避免历史脏数据或 bot/用户双写导致迁移失败；去重留给扫描器幂等逻辑。
- 兼容 `MessageLog` 的 `query_recent()`、`list_group_ids()`、`query_for_compact()` 首期仍读旧 `group_messages` 表；新 `conversation_messages` 用于 cursor 扫描和未来归档能力。这样 Admin 现有调试删除旧表临时消息时，兼容读取不会从新表把旧消息“复活”。

### `conversation_scan_cursors`

记录每个扫描器在每个 chat 上的增量消费进度。

```sql
CREATE TABLE conversation_scan_cursors (
    scanner_name       TEXT    NOT NULL,
    chat_type          TEXT    NOT NULL,
    chat_id            TEXT    NOT NULL,
    scope_key          TEXT    NOT NULL DEFAULT 'chat',
    required           INTEGER NOT NULL DEFAULT 1,
    last_message_pk    INTEGER NOT NULL DEFAULT 0,
    last_created_at    REAL    NOT NULL DEFAULT 0,
    scanner_version    TEXT    NOT NULL DEFAULT '',
    params_hash        TEXT    NOT NULL DEFAULT '',
    status             TEXT    NOT NULL DEFAULT 'active',
    updated_at         REAL    NOT NULL,
    meta_json          TEXT,
    PRIMARY KEY (scanner_name, chat_type, chat_id, scope_key)
);
```

首期索引：

```sql
CREATE INDEX idx_conv_cursor_chat
    ON conversation_scan_cursors(chat_type, chat_id, required, status);
```

语义：

- `scope_key='chat'` 表示按单群/单私聊消费。
- 未来如出现跨群 global 扫描，可用 `scope_key='global:<pool_id>'`，但清理安全水位仍按每个 source chat 计算。
- `required=1` 的 scanner 参与最小安全水位；`required=0` 的 scanner 只做观测，不阻塞清理。
- `status='needs_rescan'` 表示版本或参数变化后需要人工选择重扫范围；默认不自动全量重扫，也不会自动恢复增量 cursor。

### `conversation_scan_runs`

记录扫描审计，方便排查“扫了多少、提取多少、为什么失败”。

```sql
CREATE TABLE conversation_scan_runs (
    run_id                   TEXT PRIMARY KEY,
    scanner_name             TEXT    NOT NULL,
    chat_type                TEXT    NOT NULL,
    chat_id                  TEXT    NOT NULL,
    scope_key                TEXT    NOT NULL DEFAULT 'chat',
    from_message_pk          INTEGER NOT NULL,
    to_message_pk            INTEGER NOT NULL,
    backtrack_from_message_pk INTEGER NOT NULL,
    scanned_count            INTEGER NOT NULL DEFAULT 0,
    extracted_count          INTEGER NOT NULL DEFAULT 0,
    filtered_count           INTEGER NOT NULL DEFAULT 0,
    saved_count              INTEGER NOT NULL DEFAULT 0,
    status                   TEXT    NOT NULL,
    error                    TEXT,
    started_at               REAL    NOT NULL,
    finished_at              REAL,
    meta_json                TEXT
);
```

首期索引：

```sql
CREATE INDEX idx_conv_runs_scanner_time
    ON conversation_scan_runs(scanner_name, started_at);
CREATE INDEX idx_conv_runs_chat_time
    ON conversation_scan_runs(chat_type, chat_id, started_at);
```

### `conversation_retention_policies`

按群/私聊配置留存策略。默认策略首期是“不物理删除”。

```sql
CREATE TABLE conversation_retention_policies (
    chat_type          TEXT    NOT NULL,
    chat_id            TEXT    NOT NULL,
    cleanup_enabled    INTEGER NOT NULL DEFAULT 0,
    keep_raw_forever   INTEGER NOT NULL DEFAULT 1,
    raw_retention_days INTEGER,
    compact_after_days INTEGER,
    media_policy       TEXT    NOT NULL DEFAULT 'metadata_only',
    updated_at         REAL    NOT NULL,
    updated_by         TEXT,
    reason             TEXT,
    meta_json          TEXT,
    PRIMARY KEY (chat_type, chat_id)
);
```

语义：

- 未显式配置的群/私聊等同于 `cleanup_enabled=0`、`keep_raw_forever=1`。
- `media_policy` 首期只记录意图；当前 `group_messages` 不保存真实媒体文件，只有文本/JSON 元数据。
- 私聊永久留存必须显式 opt-in；不能因为未来 memo 需求默认永久保留所有私聊。

### `conversation_message_refs`

业务证据引用表。它是清理保护和审计辅助，不是唯一安全来源。

```sql
CREATE TABLE conversation_message_refs (
    ref_id           TEXT PRIMARY KEY,
    message_pk       INTEGER NOT NULL,
    ref_owner        TEXT NOT NULL,  -- slang / style / memo / admin 等
    ref_type         TEXT NOT NULL,  -- evidence / observation / source / hold 等
    external_table   TEXT,
    external_id      TEXT,
    snapshot_text    TEXT,
    snapshot_json    TEXT,
    created_at       REAL NOT NULL,
    expires_at       REAL,
    meta_json        TEXT
);
```

首期索引：

```sql
CREATE INDEX idx_conv_refs_message
    ON conversation_message_refs(message_pk);
CREATE INDEX idx_conv_refs_owner_external
    ON conversation_message_refs(ref_owner, external_table, external_id);
```

语义：

- 写入业务 evidence 后应同步写 ref；但清理不能只信 ref。
- dry-run 前应支持从黑话/表达 evidence 表重建 refs，或至少校验业务表是否仍引用将被清理的消息。
- `snapshot_text` / `snapshot_json` 用于未来真删 raw rows 后仍保留最小审计证据；首期不触发真删。

## 游标与扫描规则

扫描器窗口语义：

```text
backtrack_from_pk = max(0, last_message_pk - backtrack_window)
from_pk = last_message_pk
end_pk = 当前 chat 最大 message_pk
WHERE chat_type = ? AND chat_id = ? AND message_pk > from_pk AND message_pk <= end_pk
ORDER BY message_pk ASC
```

默认 `backtrack_window=50`。当前实现会把 `backtrack_from_pk` 写入 `conversation_scan_runs` 供审计和后续重扫使用，但首期业务抽取只消费 `message_pk > last_message_pk` 的新消息，避免同一窗口反复触发黑话/表达计数。后续若启用真实回看消费，扫描器必须先具备基于业务 key、平台 message id、raw hash 或 evidence key 的幂等去重。

首次启用 cursor 时不会全量重扫历史，而是 bootstrap 到最近 `limit` 条归档消息：

```text
如果没有 cursor 且 max_message_pk > limit:
    from_pk = max_message_pk - limit
    end_pk = max_message_pk
```

这样迁移后不会突然把大群全部历史丢给 LLM。

更新 cursor 的条件：

- 本次扫描完成且业务写入/过滤结果已落库。
- `last_message_pk` 更新为 `end_pk`。
- `last_created_at` 更新为本次范围内最大 `created_at`。
- 如果 `scanner_version` 或 `params_hash` 与记录不一致，只更新 `status='needs_rescan'` 或写入 run meta，不自动重置到 0。
- `needs_rescan` 或 cursor 非 active 时退回旧 `query_recent()` 最近窗口；当前实现会写一条 `status='legacy_fallback'` 的 `conversation_scan_runs` 便于审计，但 `can_advance=false`，不会推进 cursor。

首期必需 scanner registry 建议放在代码/配置中，不单独建第 6 张表。初始建议：

| scanner | 何时 required | 说明 |
| --- | --- | --- |
| `slang_manual_extract` | 黑话学习对该群启用时 | 手动抽取 cursor，参与清理安全水位 |
| `slang_daily_review` | 黑话 daily review 对该群启用时 | 后台复核 cursor，参与清理安全水位 |
| `style_manual_extract` | 表达学习对该群启用时 | 手动抽取 cursor，参与清理安全水位 |
| `archive_compact` | 未来真删 raw 前需要 segment 覆盖时 | Phase 5 前不启用 |
| `state_board` | 不 required | 只读近期窗口，不阻塞历史清理 |
| `admin_recent_view` | 不 required | 只是 Web 查看近期消息 |

## 留存与 dry-run 清理规则

首期只做 dry-run，不实际删除。

最小安全水位：

```text
required_cursors = 当前 chat 所有 required 且 active 的 scanner cursor
如果任一 required scanner 缺 cursor：blocked = true
如果任一 required scanner status = needs_rescan：blocked = true
min_safe_pk = min(cursor.last_message_pk)
```

dry-run 候选消息必须同时满足：

- `cleanup_enabled=1`。
- `keep_raw_forever=0`。
- `created_at` 早于 `raw_retention_days` cutoff。
- `message_pk <= min_safe_pk`。
- 不存在未过期 `conversation_message_refs`。
- 业务 evidence 校验没有引用该消息，或已能生成最小 `snapshot_text/snapshot_json`。

dry-run 输出必须包含：

- 将被清理的数量和 pk/time 范围。
- 阻塞原因：无策略、永久保留、缺 required cursor、需要重扫、有 evidence ref、有业务 evidence 引用。
- 每个 required scanner 的 cursor 状态。
- 是否存在需要人工确认的私聊/群聊永久留存变更。

## 阶段计划

| 阶段 | 项目 | 状态 | 验收 |
| --- | --- | --- | --- |
| Phase 0 | 追踪文档与交叉引用 | 已完成 | 新文档存在，黑话/表达 tracker 指向 ConversationArchive，维护日志记录仅文档归档 |
| Phase 1 | 兼容型 `ConversationArchive` 服务 | 已实现 | `record/query_recent/list_group_ids/record_session_msg/query_for_compact` 行为与 `MessageLog` 一致 |
| Phase 1 | 5 张核心表创建 | 已实现 | init 可创建 messages/cursors/runs/policies/refs，不创建 segments/term_stats/memo 表 |
| Phase 1 | 旧 `MessageLog` 兼容包装 | 已实现 | `services.memory.message_log.MessageLog` 继承 `ConversationArchive`，现有消费者无需改代码 |
| Phase 2 | 旧 `group_messages` backfill | 已实现原语 | init 会幂等 backfill；旧表保留；新旧查询一致测试已覆盖 |
| Phase 2 | 双写/补齐策略 | 已实现原语 | 旧表优先；archive-side 写失败只记录错误，后续 backfill 可补齐 |
| Phase 3 | 黑话抽取迁移到 cursor | 已实现 | manual 使用 `slang_manual_extract` cursor，daily review 使用 `slang_daily_review` cursor；archive 不可用时退回最近窗口 |
| Phase 3 | 表达抽取迁移到 cursor | 已实现 | 手动抽取使用 `style_manual_extract` cursor，不再重复扫同一历史范围；保留最近窗口 fallback |
| Phase 4a | 审计修正收口 | 已实现 | `legacy_fallback` 有 scan run；抽取 evidence 写 refs；可从 slang/style evidence 回填 refs；health 暴露 archive/legacy 差异 |
| Phase 4 | 留存策略与 dry-run | 已实现原语 | 可报告清理候选与阻塞原因，不实际删除 raw rows；尚未接 Admin；真实清理前必须先跑业务 refs 同步或等价校验 |
| Phase 5 | segment 与真实清理再评估 | 暂缓 | dry-run 长期稳定后再设计 `conversation_segments` 和物理清理 |
| Phase 6 | 群词频统计插件接入 | 暂缓 | 独立业务表设计，不进入归档首期 schema |
| Phase 7 | 私聊备忘录插件接入 | 暂缓 | 独立 `services/memo`，通过 refs 引用归档消息 |

## 与现有模块的关系

### 黑话模块

当前黑话 daily/manual 抽取已优先使用 `ConversationArchive` cursor。兼容说明：

- `slang_manual_extract` 使用 `ConversationArchive` cursor 读取手动抽取增量范围。
- `slang_daily_review` 使用独立 cursor，避免手动抽取推进 daily review 进度。
- 如果 MessageLog 对象没有 archive 能力、archive 读取失败或 cursor 标记为 `needs_rescan`，会退回旧 `query_recent()` 最近窗口；真实 archive 的 `needs_rescan` fallback 会留下 `legacy_fallback` scan run。
- 黑话抽取证据会尽量写入 `conversation_message_refs`；旧数据可通过 `slang_observations.group_id + message_id` 回填 refs。
- stoplist、pending key、global scan 等现有语义不变。
- 如果 cursor 缺失，清理 dry-run 阻塞，不影响黑话现有功能。

### 表达学习

当前表达学习手动抽取已优先使用 `ConversationArchive` cursor。兼容说明：

- `style_manual_extract` 使用 `ConversationArchive` cursor 读取增量范围。
- 如果 MessageLog 对象没有 archive 能力、archive 读取失败或 cursor 标记为 `needs_rescan`，会退回旧 `query_recent()` 最近窗口；真实 archive 的 `needs_rescan` fallback 会留下 `legacy_fallback` scan run。
- 默认群隔离和 global 表达池语义保持不变。
- 表达抽取证据会尽量写入 `conversation_message_refs`；旧数据可通过 `style_evidence.group_id + message_id` 回填 refs。
- 动态风格档案仍由 `StyleStore` 管理，不写入归档底座。

### 状态板和 Admin 最近消息

状态板和 Admin 最近消息只需要近期窗口，不参与历史清理安全水位。迁移后它们可以继续使用兼容 `query_recent()`。

### 私聊备忘录

备忘录插件后续应是独立服务：

- `ConversationArchive` 只提供原始消息引用和私聊 opt-in 留存策略。
- `memo_collections`、`memo_items`、`memo_sources` 等业务表放到 `services/memo`。
- 私聊永久留存必须显式开启，不能默认保留所有私聊原文。

## 评估

### 采纳的审计建议

- 首期从 8 张表裁剪为 5 张核心表。
- `created_at` 统一保留 REAL epoch。
- 清理必须先 dry-run，缺 required cursor 默认阻塞。
- `message_refs` 不是唯一安全来源，必须考虑业务 evidence 表。
- memo 拆成独立服务。
- `segments` 和 `term_stats` 延后。
- 补充索引规划。

### 保留的设计判断

- `message_pk` 仍是主游标。SQLite 写入顺序足够稳定，真正风险是异步晚入库和未来多写入口，因此用 `last_created_at` 与回看窗口增强鲁棒性，而不是放弃 pk cursor。
- 首期不追求强双写事务。旧表写入优先保当前运行时稳定，新表写失败记录日志，由 backfill 修复。
- 默认不物理删除。只有 dry-run 结果长期稳定，并且人工确认策略后，才进入真实清理。

### 优点

- 解决黑话/表达重复扫描最近窗口的问题。
- 为未来清理历史提供可解释安全水位。
- 支持群级和私聊级留存策略。
- 为黑话、表达、未来备忘录保留证据引用能力。
- 符合 Omubot 三层架构：归档是系统服务，业务能力从服务读取。

### 代价

- 复杂度明显高于 148 行 `MessageLog`。
- 后续实现需要迁移、兼容、游标、审计、dry-run 多条链路。
- 清理逻辑如果过早启用会有误删风险，因此首期必须禁止真删。

## 风险跟踪

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 首期 schema 过度设计 | 中 | 只建 5 张表，segments/term_stats/memo 延后 |
| 游标漏扫异步晚入库消息 | 中 | pk cursor + `last_created_at` + 50 条回看窗口 + 扫描器幂等 |
| 新增 scanner 从未运行导致误删 | 高 | required scanner 缺 cursor 时 dry-run 阻塞 |
| refs 与业务 evidence 不一致 | 高 | 抽取热路径尽量写 refs；真实清理前先从 slang/style evidence 回填/校验 refs；当前 dry-run 不物理删除 |
| 旧表/新表双写不一致 | 中 | 旧表优先保证现有行为；新表失败记录日志；backfill 幂等补齐 |
| deleted WAL 导致宿主 sqlite 看不到 bot 写入 | 中 | `ConversationArchive.init()` 将 `messages.db` 切到 `journal_mode=DELETE` 与 `synchronous=FULL` |
| LLM segment 摘要丢证据 | 高 | Phase 5 前不建 segment、不真删；有 evidence 的 raw rows 优先保留 |
| 私聊隐私风险 | 高 | 私聊长期保留必须显式 opt-in；memo 独立服务 |
| DB 体积持续增长 | 中 | 先建立 dry-run 可观测性，再讨论默认保留天数和真删 |

## 验证计划

当前 Phase 0 文档验证：

```bash
test -f docs/conversation-archive-implementation-tracker.md
rg -n "ConversationArchive|conversation_messages|conversation_scan_cursors|dry-run|message_refs" docs/conversation-archive-implementation-tracker.md
rg -n "ConversationArchive" docs/style-learning-implementation-tracker.md docs/slang-module-implementation-tracker.md maintenance-log.md
```

当前已通过的代码验证：

```bash
source ./scripts/dev/env.sh
uv run pytest tests/test_conversation_archive_store.py tests/test_message_log.py -q
uv run pytest tests/test_conversation_archive_store.py tests/test_message_log.py tests/test_admin_api.py -q
uv run pytest tests/test_group_timeline.py tests/test_state_board.py -q
uv run pytest tests/test_client.py -q
uv run pytest tests/test_group_timeline.py tests/test_state_board.py tests/test_client.py -q
uv run pytest tests/test_admin_api.py tests/test_style_api.py tests/test_style_plugin.py -q
uv run pytest tests/test_slang_plugin.py tests/test_slang_store.py -q
uv run pytest tests/test_style_api.py tests/test_style_plugin.py tests/test_slang_plugin.py tests/test_slang_store.py -q
uv run ruff check services/conversation_archive services/memory/message_log.py tests/test_conversation_archive_store.py
```

后续黑话/表达迁移阶段应追加：

```bash
source ./scripts/dev/env.sh
uv run pytest tests/test_slang_plugin.py tests/test_style_api.py tests/test_style_plugin.py -q
uv run ruff check services/conversation_archive services/memory services/slang services/style plugins/slang plugins/style tests
```

Phase 3 迁移已通过的验证：

```bash
source ./scripts/dev/env.sh
uv run pytest tests/test_conversation_archive_store.py tests/test_style_api.py tests/test_slang_plugin.py -q
uv run pytest tests/test_admin_api.py tests/test_slang_store.py tests/test_style_plugin.py -q
uv run pytest tests/test_message_log.py tests/test_group_timeline.py tests/test_state_board.py tests/test_client.py -q
uv run pytest tests/test_style_store.py tests/test_style_extractor.py tests/test_slang_semantic_reviewer.py tests/test_slang_drift_reviewer.py -q
uv run ruff check services/conversation_archive services/memory/message_log.py admin/routes/api/style.py admin/routes/api/slang.py plugins/slang/plugin.py services/slang/daily_reviewer.py tests/test_conversation_archive_store.py tests/test_style_api.py tests/test_slang_plugin.py
```

## 人工介入点

以下动作必须人工确认后才能执行：

- 启用任何真实物理清理。
- 将某个群或私聊设为永久保留 raw。
- 对 `scanner_version` / `params_hash` 变化后的历史范围进行重扫。
- 将私聊备忘录接入长期留存。
- 让 segment 覆盖后的 raw rows 进入可删除范围。

## 更新日志

- 2026-05-13：Phase 4a 审计修正收口：`needs_rescan` / 非 active cursor 的真实 archive fallback 会写入 `legacy_fallback` scan run 但不推进 cursor；黑话/表达抽取保存证据后会尽量写 `conversation_message_refs`；新增从 `slang_observations` / `style_evidence` 回填 refs 的独立 helper；System health 增加 messages archive 与 legacy 行数差异观测。仍未启用真实物理清理，`needs_rescan` 仍需人工决定重扫/重置策略。
- 2026-05-13：Phase 3 运行验收热修：修复 `message_pk` 全局稀疏导致 cursor 按 `last_pk + limit` 卡住的问题，改为按当前 chat 的下一批 N 条消息读取；backfill 先检查 `legacy_row_id` 是否已存在，避免重复 init 消耗 autoincrement；`messages.db` 切到 DELETE journal，避免宿主/容器 deleted WAL 视图分裂；daily/manual 抽取取消时会把 active scan run 标记为 abandoned。实机验证：`/style/extract/run` 第一轮 archive 扫描 1 条并推进 cursor，第二轮扫描 0；`slang_manual_extract` 自动抽取先消费同一增量后，手动两轮均为 0；`PRAGMA integrity_check=ok`，容器无 `messages.db-wal (deleted)` fd。重建了 bot，未重启 NapCat。
- 2026-05-12：Phase 3 黑话/表达 cursor 迁移落地：新增 archive scan batch helper，`style_manual_extract`、`slang_manual_extract`、`slang_daily_review` 优先读取 `ConversationArchive` cursor；无 archive 能力、读取失败或 cursor 需要人工重扫时自动退回旧 `query_recent()` 最近窗口。首次启用 cursor 只 bootstrap 最近 `limit` 条，不全量重扫历史；普通聊天、状态板、Admin 最近消息、client 压缩仍走兼容 `MessageLog` 接口。未启用真实清理，未重启 bot / NapCat。
- 2026-05-12：Phase 1 / Phase 2 / Phase 4 后端原语落地：新增 `services/conversation_archive`，实现 5 张核心表、旧 `group_messages` 兼容双写、init 幂等 backfill、扫描 cursor、scan run 审计、retention policy、message refs、dry-run cleanup；`MessageLog` 改为兼容包装，现有调用接口不变。兼容读取首期仍读旧表，避免 Admin 旧表调试删除后新表数据复现。当前未迁移黑话/表达扫描路径，未接 Admin，未启用真实清理。验证：`tests/test_conversation_archive_store.py tests/test_message_log.py tests/test_admin_api.py` 73 passed，`tests/test_group_timeline.py tests/test_state_board.py tests/test_client.py` 117 passed，`tests/test_style_api.py tests/test_style_plugin.py tests/test_slang_plugin.py tests/test_slang_store.py` 78 passed，相关 ruff 通过。
- 2026-05-12：创建 ConversationArchive 长期追踪文档；采纳二次审计意见，将首期 schema 裁剪为 messages/cursors/runs/policies/refs 五张核心表；明确 `created_at` 继续使用 REAL epoch、主游标为 `message_pk` + 回看窗口、缺 required cursor 阻塞 dry-run 清理、`message_refs` 不作为唯一安全来源；segments、词频统计、私聊备忘录均延后为独立阶段。本轮仅文档归档，不改运行时代码、不迁移 DB、不重启 bot。
