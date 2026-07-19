# Memory Hot-path Conflict-Aware Write Policy v1 Migration (2026-07-16)

> 状态：implemented in code, not deployed.
> 范围：MemoExtractor 从无条件 active-card ADD 升级为受约束的
> `add / reinforce / supersede / skip`；新增 durable evidence observations；
> `CardStore.supersede_card` / `reinforce` 支持取消安全事务与 provenance kwargs。
> 不 redesign 整体记忆架构；不部署；不触 NapCat/QZone/Admin SPA。

## Old → New Mapping

| 面 | 旧 | 新 |
|----|----|----|
| Extractor 输出 | 仅 `[category] content` 行，全部 `add_card` | 优先 JSONL `category/content/action/target_card_id`（最多 3 条）；旧 bracket 行仍视为 `add` |
| 决策上下文 | LLM 只看本轮对话 | prompt 展示当前用户 top-24 active；全量 active 仅作确定性 duplicate 防重 |
| 重复偏好 | 每次新 active 卡 | exact/高阈值 ngram duplicate 可强制 `add→reinforce`；包含扩展不误合并 |
| 事实更新 | 无法热路径 supersede（Dream 离线补偿） | prompt-window 同 category target + user-message 更新线索/词汇证据 → 原子 supersede |
| 证据链 | 新卡 provenance 常缺 `source_msg_id` | add/reinforce/supersede 透传 `source_message_id`，`captured_by=memo_extractor` |
| 证据表 | 无 | additive `memory_card_observations`（幂等 partial unique） |
| 开关 | 无 | `MemoConfig.write_policy_enabled`（默认 true；false=legacy add-only） |
| 插件版本 | memo `1.1.5` | memo `1.1.6` |

## Key Contract

1. **Scope**：v1 一律 `scope=user` / `scope_id=user_id`；不自动写 group/global；禁止跨用户 target。
2. **target 校验**：`target_card_id` 必须在本轮 prompt 实际展示的 top-24 active 列表内、同用户、同 category；不得引用第 25+ 张隐藏卡。
3. **duplicate**：纯 helper（normalize + char-ngram Jaccard），阈值 `DUPLICATE_SIMILARITY_THRESHOLD=0.72`；全量 user active 参与确定性防重，**不用** `CardStore.find_similar` 作语义证明；非 exact 包含关系不直接记 1.0。
4. **supersede 信号**：只接受当前 user message 的强更新线索或受限时间线索，且必须与新 content 有实质词汇重叠；LLM content 不得自授权。
5. **原子性**：同实例 CardStore writers 共用 write lock；`supersede_card` 在 `BEGIN IMMEDIATE` 事务内条件失效旧卡、插新卡与 observation；cancel/异常/并发 loser rollback，无双 active。
6. **reinforce+obs**：提供 evidence/source 时 conf 更新与 observation 同事务；同 `(card_id, source_message_id, decision)` 幂等。
7. **计数**：`MemoExtractor.stats` / `get_write_stats()` → `add/reinforce/supersede/skip/invalid`。
8. **兼容**：Dream / CardUpdateTool 等调用可不传 provenance，并可在相同 scope/scope_id 内纠正 category；跨 owner scope 拒绝。MemoExtractor 自身继续强制同 category target。

## Storage / Compatibility

- **Additive table** in `storage/memory_cards.db`：
  - `memory_card_observations(observation_id PK, card_id, decision, source_message_id, evidence_text, observed_at, captured_by, meta_json)`
  - indexes：`card_id`、`source_message_id`
  - unique partial：`(card_id, source_message_id, decision) WHERE source_message_id IS NOT NULL`
- 现有 `memory_cards` 列与 RetrievalGate active-only 语义不变；历史卡不 big-bang 重写。
- 无 admin frontend；schema_contracts 未为 memory_cards 增加强制 contract（catalog 仅注册 DB 路径）。

## Key Files

| 路径 | 变更 |
|------|------|
| `plugins/memo/plugin.py` | write policy extractor、config 字段、version 1.1.6、post_reply provenance |
| `plugins/memo/config.default.json` | `write_policy_enabled: true` |
| `plugins/memo/config.schema.json` | boolean field |
| `plugins/memo/plugin.json` | version + restart_required_fields |
| `services/memory/write_policy.py` | **NEW** pure helpers |
| `services/memory/card_store.py` | observations + atomic supersede/reinforce |
| `bootstrap/chat_runtime.py` | `MemoExtractor(..., config=memo_cfg)` |
| `tests/test_memo_extractor_write_policy.py` | **NEW** RED→GREEN suite |
| `tests/test_card_store.py` | observations / atomicity extensions |

## Deployment / Observation

本 packet **未部署**。后续部署只允许 recreate bot，永不 recreate NapCat。上线后建议观察：

1. extractor 写入后 active 卡增长率是否下降（reinforce 占比上升）。
2. supersede 是否仅在显式更新场景出现；是否出现双 active。
3. `memory_card_observations` 增长与 source_message_id 覆盖率。
4. `write_policy_enabled=false` 时是否回到 add-only。

## Rollback

1. 将 `write_policy_enabled` 设为 `false` 并 restart bot → legacy add-only（保留已有 observation/supersede 数据）。
2. 代码回滚：还原 Key Files 表中的 hunks；删除 `services/memory/write_policy.py` 与本 migration 对应测试。
3. 数据：observations 表为 additive，可不删；若需清理仅删除 `memory_card_observations`（不影响 cards）。
4. 不删除历史 cards、不 touch NapCat、不改 live DB 除非维护窗口。

## Explicit Non-goals

- PPR / GraphRAG / 全量 LongMemEval-LoCoMo 下载或 LLM judge。
- 自动 promote consolidator fact；放开无审核 graph/episode 写入。
- Admin SPA observation 浏览器；历史卡片 big-bang 重写。
- 部署、commit/push、QZone、NapCat、容器 recreate。
