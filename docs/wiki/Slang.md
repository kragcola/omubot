# 群内黑话

黑话系统用于让 Omubot 学习群内约定用语、网络梗和临时语境词。它不是长期记忆卡片的一部分，而是独立的“群内语境知识”。

当前实现采用：

```
services/slang  →  稳定存储、抽取、审核、治理
plugins/slang   →  消息接入、定时任务、Prompt 注入、工具注册
admin/slang     →  Web 审核控制台与设置面板
```

## 能力边界

- 默认按群隔离：A 群黑话不会自动注入 B 群。
- 候选默认需要审核：`candidate` 不进入 Prompt。
- `approved` 才可注入当前群 Prompt。
- `muted` 和 `expired` 不再注入。
- AI 自动通过的词条仍是 `approved`，但会在 `source/meta` 中标记，方便管理员复核。
- v3 默认保持轻依赖，不引入 embedding、FAISS、BM25、jieba、numpy。

## 生命周期

| 状态 | 说明 | 是否注入 Prompt |
|------|------|----------------|
| `candidate` | 候选，等待审核 | 否 |
| `approved` | 已批准，可用于当前群语境 | 是 |
| `muted` | 静音，不再学习/注入 | 否 |
| `expired` | 过期保留，用于追溯 | 否 |

## 存储

数据库路径：`storage/slang.db`

核心表：

| 表 | 说明 |
|----|------|
| `slang_terms` | 主词条、释义、别名、群/全局作用域、状态、置信度 |
| `slang_observations` | 群聊证据、来源用户、消息片段 |
| `slang_pending_candidates` | 未达到最小出现次数的观察中候选 |
| `slang_extraction_runs` | 抽取与每日 AI 复核运行日志 |
| `slang_settings` | Web 可修改的运行设置 |
| `slang_term_revisions` | v3 修订历史，记录动作前后快照 |
| `slang_drift_reviews` | v3 语义漂移审核队列 |

## 运行流程

### 消息观察

`SlangPlugin.on_message` 只处理群聊文本：

1. 检查学习开关和群白名单。
2. 匹配已知 `approved/candidate` 词条。
3. 更新出现次数、独立用户和最近出现时间。

### 批量抽取

`SlangPlugin.on_tick` 按设置间隔从近期群聊中抽取候选：

1. `SlangExtractor` 使用现有 LLMClient 生成候选 JSON。
2. stoplist、短词、纯符号、普通称呼等会被过滤。
3. 未达到 `candidate_min_count` 的候选先进入观察中。
4. 达到阈值后进入 `candidate`，等待管理员审核。

### 每日 AI 复核

每日定点任务可结合搜索结果识别公网梗：

1. 按北京时间 `daily_ai_review_time` 触发。
2. 从近期群聊抽取疑似黑话/梗。
3. 可调用 `web_search` 辅助判断公网含义。
4. 搜索失败时只允许入候选，不自动通过。
5. 高置信且开关允许时写为 `approved + source=ai_auto_review`。
6. AI 通过词条在 Web 中标记为待人工复核。

### 存量候选池复核

每日 AI 复核只覆盖新抽取窗口，历史 `candidate` 池需要单独的 backlog reviewer 收敛。`SlangBacklogReviewer` 会按置信度和游标分页处理现有候选：

1. 从 `status='candidate'` 的存量词条中按 batch 取一批。
2. 可调用 `web_search` 辅助判断公网含义。
3. LLM 判定为群内黑话时升级为 `approved`。
4. 判定为普通公网词、噪声或不应学习时转为 `muted`。
5. 证据不足时保持 `candidate`，等待下一轮或人工处理。
6. `meta:backlog_review_state` 保存进度、游标、已处理数、approved/muted/kept 统计。

2026-05-16 修复后，backlog reviewer 不再每分钟无限重启。它复用 `daily_ai_review_times` 形成 daily slot，清空本 slot 后写入 `meta:last_backlog_review_slot`；同一 slot 再次 tick 会跳过。若一次 tick 只处理到半途，不会锁 slot，下个 tick 会继续同一轮。管理员执行 reset 时会同时清除 slot 记录，允许立刻重跑。

## v3 质量治理

v3 的重点不是继续扩大自动学习，而是治理学错后的风险。

### 修订历史

以下动作会写入 `slang_term_revisions`：

- 人工创建/编辑
- 批准、静音、过期
- AI 自动通过
- 管理员真实通过、否决、退回候选
- 合并重复项
- 语义漂移检测和处理

Admin Web 的词条详情抽屉会展示“修订记录 / 证据链”。

### 语义漂移

当已批准词条再次被抽取到明显冲突的新释义时，不会覆盖主词条，而是进入 `slang_drift_reviews`。

管理员可选择：

| 动作 | 结果 |
|------|------|
| 采纳新释义 | 更新主词条释义和置信度 |
| 保留旧释义 | 关闭漂移项，主词条不变 |
| 转成别名 | 把新写法并入别名 |
| 静音漂移 | 将主词条静音，停止注入 |

漂移项本身不会进入 Prompt，只有采纳后才影响主词条。

## Prompt 注入与工具查询

Prompt 注入仍采用动态块：

```
PromptContext.add_block(label="群内黑话", position="dynamic")
```

注入排序：

1. 当前对话直接命中的词条。
2. 当前群高置信活跃词。
3. 已批准全局词条。

`max_injected_terms` 和 `max_prompt_chars` 控制注入规模；`min_inject_confidence` 可过滤低置信 approved 词条。

v3 新增 `slang_lookup` 工具：

- 只查询当前群与全局 `approved` 词条。
- 无群上下文时只返回全局词条。
- 可通过 `lookup_tool_enabled` 关闭。
- 目标是减少 Prompt 常驻长度，让 LLM 在需要时按需查询。

## Admin Web

入口：`/admin/slang`

页面能力：

- 待审核、AI 审核、已批准、语义漂移、全部队列切换。
- 搜索、群筛选、作用域、置信度筛选。
- 手动抽取、跨群候选扫描、主动创建黑话。
- 批量批准、静音、过期、删除观察记录。
- 词条详情编辑、AI 复核、合并重复项、置信度重算。
- 观察中候选、抽取运行日志、质量治理和修订记录。
- 存量候选池 AI 清理进度、手动运行和重置。
- 结构化设置面板，不需要编辑 raw JSON。

## API

主要 Admin API：

| 接口 | 说明 |
|------|------|
| `GET /api/admin/slang/summary` | 指标摘要 |
| `GET /api/admin/slang/terms` | 分页词条列表 |
| `POST /api/admin/slang/terms/create` | 手动创建词条 |
| `POST /api/admin/slang/terms/bulk` | 批量审核 |
| `POST /api/admin/slang/terms/merge` | 合并重复项 |
| `GET /api/admin/slang/pending` | 观察中候选 |
| `GET /api/admin/slang/extract/runs` | 抽取运行日志 |
| `POST /api/admin/slang/extract/run` | 手动抽取 |
| `POST /api/admin/slang/global/scan` | 跨群候选扫描 |
| `GET/POST /api/admin/slang/settings` | 设置读取/保存 |
| `GET /api/admin/slang/backlog-review/status` | backlog reviewer 进度 |
| `POST /api/admin/slang/backlog-review/run` | 立即运行一批或一轮 backlog reviewer |
| `POST /api/admin/slang/backlog-review/reset` | 重置 backlog reviewer 游标与 slot |
| `GET /api/admin/slang/terms/{id}/revisions` | v3 修订记录 |
| `GET /api/admin/slang/drift` | v3 语义漂移列表 |
| `POST /api/admin/slang/drift/{id}/accept` | 采纳新释义 |
| `POST /api/admin/slang/drift/{id}/reject` | 保留旧释义 |
| `POST /api/admin/slang/drift/{id}/alias` | 转成别名 |
| `POST /api/admin/slang/drift/{id}/mute` | 静音漂移 |

## 关键设置

| 设置 | 默认 | 说明 |
|------|------|------|
| `learning_enabled` | `true` | 是否学习候选 |
| `injection_enabled` | `true` | 是否注入 Prompt |
| `review_required` | `true` | 候选是否需要审核 |
| `candidate_min_count` | `2` | 进入候选前的最小出现次数 |
| `max_injected_terms` | `8` | 最大注入词条数 |
| `max_prompt_chars` | `1200` | Prompt 黑话块最大字符 |
| `group_allowlist` | `[]` | 空表示所有群可学习 |
| `stoplist` | `[]` | 永不学习词 |
| `daily_ai_review_enabled` | `true` | 每日 AI 识别 |
| `daily_ai_auto_approve_enabled` | `false` | 是否允许 AI 自动通过 |
| `backlog_review_enabled` | `true` | 是否复核历史积压的 candidate 池 |
| `backlog_review_batch_size` | `50` | backlog reviewer 每批处理数量 |
| `backlog_review_min_confidence` | `0.0` | backlog reviewer 处理候选的最低置信度 |
| `drift_detection_enabled` | `true` | 是否启用语义漂移检测 |
| `lookup_tool_enabled` | `true` | 是否注册黑话查询工具 |
| `semantic_backend` | `ngram` | v3.5 预留；默认轻量后端 |

## 与表达学习的边界

黑话负责“这个词在群里是什么意思”，表达学习负责“这个场景大家通常怎么说”。例如：

- “超舟”这类词义、别名、群内梗，属于黑话。
- “有人发成果时先短促夸一句再补一个具体点”，属于表达学习。

表达学习不写入 `slang_terms`，黑话也不负责动态风格档案。两者可以同时注入 Prompt，但标签和职责分开，避免把词义治理和说话方式混在一起。

## v3.5 预留

`semantic_backend` 目前只实际使用轻量 `ngram` 策略。embedding / FAISS 等重依赖应作为后续可选增强，不进入默认 Docker。
