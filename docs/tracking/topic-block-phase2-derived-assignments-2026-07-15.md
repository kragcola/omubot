# 进阶话题块 Phase 2：版本化归属派生层

## Objective

在 Phase 1 append-only `research_message_event` 之上建立可重跑、可审计的派生层：为每个 raw event 保存版本化 `topic_assignment`、跨进程稳定 `block_uuid`、归属证据，以及支持多 raw event 组成一个逻辑 utterance 的 `utterance_membership`。任何 Phase 2 失败都不得阻断或改写 Phase 1 原始采集。

## Status

- mode: task
- phase: Phase 2 / derived assignment layer
- status: verified_pending_deploy
- started_at: 2026-07-15
- current_step: 实现、独立复审、全量回归与生产离线快照已通过；准备精确提交与 bot-only 部署
- deployment: pending
- implementation_commit: pending

## Recovered Evidence

- Phase 1 已部署，生产 `/app/storage/research_events.db` 当前 `user_version=1`、`quick_check=ok`，2026-07-15 只读审计为 102 rows、5 runs、0 duplicate event UID。
- raw 表只有 `research_message_event`；旧 `topic_corpus.db` 仍保存进程内 `b1/b2` 快照，但不是跨重启研究主键。
- 在线 `TopicBlockTracker` 已完成 L0-L3 边模型、reply message reverse lookup、线性打分、activity decay 与 reservoir；Phase 2 应复用其语义，不另造第二套归属算法。
- `TopicBlockTracker` 当前只返回 `TopicBlock`，没有结构化 reason/score/margin；Phase 2 需要 additive evidence API，不能破坏 `observe()` 既有调用契约。

## Scope

1. 保持 Phase 1 `research_events.db user_version=1` 和 `research_message_event` 完全不变；Phase 2 只读 raw snapshot。
2. 新增独立 `research_topic_assignments.db user_version=1`，保存 assignment run、stable block identity、versioned topic assignment 与 utterance membership。
3. 为 tracker 增加结构化 attribution evidence，同时保持 `observe()` 返回值和在线行为不变。
4. 增加确定性 projector：按 raw event 稳定顺序重放，使用 UUIDv5 将本次 block seed 映射为稳定 `block_uuid`。
5. 同一 `algorithm_version` 重跑必须幂等；不同版本可并存，不覆盖旧 assignment。
6. 提供显式离线 runner：对明确 raw snapshot/cutoff 原子投影；runner 失败不影响正在运行的 Phase 1 capture。
7. 本阶段不接 ingress、scheduler 或启动自动任务；需要更新派生层时显式重跑 CLI。

## Data Contract

- `assignment_run`
  - `run_uuid` primary key
  - `algorithm_version`, `algorithm_config_hash`, `input_digest`, `input_cutoff`
  - `started_at`, `completed_at`, `status`, `event_count`, `assignment_count`
  - unique `(algorithm_version, input_digest)`，同一输入快照重复运行复用既有完成结果

- `topic_block_identity`
  - `block_uuid` primary key
  - `algorithm_version`, `group_id`, `seed_event_uid`, `created_at`
  - unique `(algorithm_version, group_id, seed_event_uid)`
- `topic_assignment`
  - primary key `(event_uid, algorithm_version)`
  - `block_uuid`, `assigned_at`, `reason`, nullable `score`, nullable `runner_up_margin`
  - nullable `reply_predecessor_event_uid`, `evidence_json`
  - append-only: conflict means duplicate/verification，禁止 update 覆盖
- `utterance_membership`
  - primary key `(event_uid, algorithm_version)`
  - `utterance_uuid`, `ordinal`, `assigned_at`, `reason`
  - unique `(utterance_uuid, algorithm_version, ordinal)`
  - 同一 utterance 可有多条 raw event；raw event 本身不合并、不删除

## Acceptance Gates

- [x] RED 测试先以目标行为缺失失败，而不是导入或测试环境错误。
- [x] 独立派生库 schema v1、0600、quick_check=ok；future schema 被拒绝且不降级、不删表。
- [x] runner 前后 raw row count、schema、user_version 和内容 digest 完全不变。
- [x] 同一 raw stream + 同一参数版本在重启/重跑后得到相同 `block_uuid`、assignment 和 utterance UUID。
- [x] 同一 `(event_uid, algorithm_version)` 冲突写不覆盖首条结果。
- [x] 新算法版本可为同一 event 写第二份 assignment，两版本同时可查。
- [x] reason/score/margin/reply predecessor evidence 可读取；强边与线性打分场景均覆盖。
- [x] 多条快速连续同 actor event 可映射到一个 utterance，插话者或超窗事件开启新 utterance。
- [x] 同版本出现不同 block/evidence 的冲突重跑必须 fail-closed，不能静默 `INSERT OR IGNORE`。
- [x] assignment、block identity、membership 和 completed run 元数据按一个事务提交；中途异常全部回滚。
- [x] runner 只读 raw；派生失败不影响 Phase 1 raw capture。
- [x] 定向测试、topic/research 回归、Ruff、targeted Pyright、全量 pytest 通过。
- [ ] 部署仅 rebuild/recreate `qq-bot`；NapCat 不 restart/recreate/down。
- [ ] 生产 raw 仍为 `user_version=1`、102 条既有 raw event 全部保留；独立派生库产生对应可审计 assignment/membership。

## Assumption Ledger

- stable 的定义是：同一 algorithm version、同一有序 raw stream 的 block UUID 跨重启和幂等重跑稳定；算法版本或参数变化允许产生新 block UUID。
- algorithm version 必须包含相关 TopicBlock 参数和 utterance gap 的确定性 fingerprint，避免同版本混入不同语义。
- Phase 2 只对固定 cutoff 的 raw snapshot 做离线研究投影，不替换在线 scheduler 当前内存 tracker，也不删除 legacy `topic_corpus.db`。
- raw snapshot 按稳定字段排序并计算 digest；物理插入顺序不影响结果。
- 无文本事件仍参与 reply/@/speaker/time 结构归属；不虚构图片或媒体正文。

## Implemented

- 新增独立 `TopicAssignmentStore` schema v1：`assignment_run`、`topic_block_identity`、`topic_assignment`、`utterance_membership`，具备复合 FK、append-only 冲突验证、批次预检和事务原子性。
- 新增显式离线 `TopicAssignmentRunner` / CLI；raw 只用 `mode=ro`，raw/derived 路径或 inode 相同会在打开连接前拒绝，CLI 默认路径锚定仓库根目录。
- `TopicBlockTracker.observe_with_evidence()` 以 additive API 暴露 reason/score/margin/reply evidence，旧 `observe()` 返回合同和在线归属顺序不变。
- projector 按真实时刻过滤/排序带时区 timestamp；reply lookup 只看已重放历史，未来复用 `message_id` 不可覆盖真实前驱。
- `algorithm_version` 指纹包含 tracker 参数、evidence schema、utterance schema 与 `causal-message-id-index-v2` projector schema；当前生产参数版本为 `topic-block-l0l3-v1-4d15cafcdf72`。
- restore preflight 同时验证 Phase 2 业务 schema 与 migration ledger name/checksum；双重取消期间 rollback 被 shield，同一 store 不遗留开放事务。

## Review And Snapshot Evidence

- 独立 reviewer 最终结论：`0 Critical / 0 Important / 0 deployment blocker`；复核聚合 `160 passed`。
- 全量 pytest：`3401 passed / 17 skipped / 174 warnings`；warnings 为既有 aiohttp/NoneBot deprecation 与 aiosqlite event-loop-close 收尾。
- 相关跨模块：`105 passed`；定向 Ruff 通过；targeted Pyright `0 errors`；`git diff --check` 通过。
- 全仓 Ruff 仍被用户现有 coursework/research/IPv6 文件的 177 项既有问题阻断；本任务未修改这些文件。
- 固定 cutoff `2026-07-14T16:12:00.740197+00:00`：raw `user_version=1`、quick_check=ok、102 rows、14 columns；input digest `sha256:c88a986096042609e21bed6730b6c2b08663adefa606f2bc4b54ab3f73e369c1`。
- 新版首跑：102 assignments、102 memberships、22 blocks、run `948a9b4a-9a74-530c-9121-0c4a9bb1aafe`、status=committed；二跑新增 0、status=duplicate。
- 结果分布：62 linear、22 new block、18 reply edge；21 条 reply 中 18 条命中已采集前驱，3 条引用位于采集窗口外；10 个多成员 utterance，最大 2 条；assignment/run/block/membership 四类孤儿均为 0。

## Rollback

1. 停止调用 Phase 2 CLI；无需重启 bot 或 NapCat。
2. 独立派生库可保留审计；确需回退时删除或恢复 `research_topic_assignments.db`，不触碰 raw DB。
3. Phase 1 的 `research_event_capture.enabled`、allowlist、secret 和运行态 metrics 均不因 Phase 2 改变。

## Test Ledger

| ID | Command | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| P2-R1 | `pytest ...::test_runner_reply_lookup_never_uses_future_reused_message_id` | RED: `new_block/event-3`; GREEN: reply 指向 `event-1` | reply 索引必须随重放因果更新 | 2026-07-15 |
| P2-R2 | `pytest ...::test_runner_cutoff_compares_iso_timestamps_as_instants` | RED: 2 rows；GREEN: 1 row | cutoff 必须按 aware datetime 比较 | 2026-07-15 |
| P2-R3 | `pytest ...::test_runner_rejects_raw_and_derived_alias_before_touching_raw` | RED: 进入 migration 后失败；GREEN: 打开连接前拒绝且 raw 状态不变 | raw/derived 必须 fail-fast 隔离 | 2026-07-15 |
| P2-R4 | `pytest tests/test_run_topic_assignment_tool.py -q` | RED: cwd-relative；GREEN: repo-root absolute defaults | CLI 可从任意 cwd 调用 | 2026-07-15 |
| P2-R5 | `pytest ...::test_runner_treats_equivalent_snapshot_from_later_cutoff_as_duplicate` | RED: `ProjectionConflictError`；GREEN: duplicate | cutoff 是 provenance，不是相同 digest 的冲突语义 | 2026-07-15 |
| P2-R6 | `pytest ...::test_restore_plan_blocks_phase2_current_schema_without_migration_ledger` | RED: restore plan `can_apply=true`；GREEN: schema_mismatch | current-v1 restore 必须验证 migration ledger | 2026-07-15 |
| P2-R7 | `pytest ...::test_double_cancel_cannot_leave_projection_transaction_open` | RED: `cannot start a transaction within a transaction`；GREEN: 后续 commit 成功 | rollback 必须 shield cancellation | 2026-07-15 |
| P2-R8 | `pytest ...::test_runner_records_completion_after_projection_finishes` | RED: completed_at 提前约 22ms；GREEN: 不早于 projector 返回 | run 完成时间不可在投影前采样 | 2026-07-15 |
| P2-V1 | `pytest -q` | 3401 passed, 17 skipped | 全量通过；既有 warnings 非阻断 | 2026-07-15 |
| P2-V2 | Phase 2/topic/storage/restore 聚合 | 105 passed；reviewer 160 passed | 跨模块与独立复审通过 | 2026-07-15 |
| P2-V3 | scoped Ruff + targeted Pyright + `git diff --check` | clean / 0 errors / clean | 静态与 diff 门禁通过 | 2026-07-15 |
| P2-V4 | CLI 对固定生产快照运行两次 | 102 committed；二跑 duplicate/0 insert | 确定性与幂等闭环 | 2026-07-15 |

## Next Step

精确提交 Phase 2 文件；随后只 rebuild/recreate `qq-bot`，在新容器内对 live raw 固定 cutoff 运行 CLI，验证 derived DB、raw 不变量、bot 状态与 NapCat 未变化。
