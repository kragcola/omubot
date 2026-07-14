# 进阶话题块上线前优化

## Objective

把现有话题块研究采集从 `scheduler.notify()` 的窄入口升级为独立、可回滚、可审计的原始事件层，为后续情感动力学分析和版本化话题归属提供可信底座，同时保持当前在线 `TopicBlockTracker`、coalesce、presence 与 legacy `topic_corpus.db` 行为不变。

## Status

- mode: none
- phase: Phase 1 / raw research event layer
- status: complete
- started_at: 2026-07-12
- deployment: deployed to two development/test groups; runtime metrics endpoint deployed 2026-07-13
- current_step: Phase 1 production row audit complete; passive monitoring remains enabled

## Confirmed Evidence

- 精确窗口 `2026-06-25 01:48:42` 至 `2026-07-12 11:14:20`：原始群事件 281,048 条，legacy `topic_corpus` 仅 279 条。
- 280,751 条位于 `silent_learn` 群，在 `kernel/router.py` 的 scheduler 前提前返回。
- active 实时事件 297 条；coalesce 将其中 180 条压为 168 批，另丢弃 6 条。
- legacy corpus 为 `human=279 / ai=0`；AI 成功发送路径未接采集。
- 三个运行 epoch 至少复用 52 个全局 `block_id` 槽位；当前 `b1...` 进程内计数不能作为跨重启研究主键。
- 2026-07-13 只读生产审计确认 raw DB 共 13 行（`inbound=4 / outbound=9`），全部来自开发测试群 `993065015` 的两个旧 run；另一测试群 `984198159` 为 0，当前容器启动后的新 run 为 0。
- 13 行均为 `source=live`，无重复 `event_uid`、伪名格式异常或方向/actor 错配；唯一 reply 边可在库内命中前驱，@ JSON 全部有效。

## Phase 1 Scope

1. 新增独立 append-only `research_message_event` SQLite 存储。
2. 在 canonical group ingress、presence/plugin/coalesce 过滤之前记录逐条原始入站事件。
3. 在 OneBot `send_group_msg` 成功返回后记录 AI 出站事件。
4. 每条事件包含幂等 `event_uid`、进程 `run_id`、平台事件时间、摄入时间、方向、actor/source、消息与回复/@边。
5. 使用显式研究群 allowlist；空 allowlist 不采集，避免开关误开后全群落盘。
6. 提供 received/persisted/duplicate/dropped/error 可观测计数。
7. 保留旧 `TopicBlockTracker`、`topic_corpus.db`、scheduler 归属和 coalescer 行为，不做迁移或删除。

## Out Of Scope

- 不在 Phase 1 重写 L0-L3 归属算法。
- 不让历史加载伪装成 live；历史事件后续使用独立 `source=history` 接口。
- 不触碰 NapCat；部署仅允许重建/重启 `qq-bot`。
- 不把原始群号、QQ 标识或正文导出到仓库。

## Phase 2 Interface Reservation

- `topic_assignment`: `event_uid`, stable `block_uuid`, `algorithm_version`, `assigned_at`。
- assignment evidence: reason, score, runner-up margin, reply predecessor edge。
- `utterance_membership`: 支持一个逻辑 utterance 对应多个 raw event，但不覆盖原始事件。
- 话题归属允许重跑并版本化，不回写/改写 `research_message_event`。

## Acceptance Gates

- [x] RED 测试先失败，失败原因是缺少目标行为而非测试语法/导入环境错误。
- [x] 同一 `event_uid` 重复写入只保留一行，并计入 duplicate。
- [x] 两次进程启动使用不同 `run_id`，事件身份不依赖 `b1/b2`。
- [x] `silent_learn` 与 active 入站都在 coalesce 之前逐条采集。
- [x] 图片/非文本消息仍有 content type 事件，不因空 plaintext 被省略。
- [x] AI 事件只在 OneBot 成功返回后记录；失败/静音不伪造出站。
- [x] 功能默认关闭且受 allowlist 限制；关闭后对现有行为零影响。
- [x] 存储错误被吞并并计数，不中断群消息处理或发送。
- [x] 定向测试、相关 router/scheduler/topic tests、ruff 与 targeted pyright 通过。
- [x] 有明确回滚步骤、部署证据与运行态残留风险说明。
- [x] 全局 OneBot 群出站守卫在 protocol trace 前安装，非 active 白名单群 fail-closed。
- [x] 两个开发群部署配置与 HMAC secret 生效；只重建 `qq-bot`，NapCat 运行身份不变。
- [x] 首批生产 raw rows 已完成只读 schema、方向、run、content、reply/@ 与幂等完整性审计。
- [x] 运行中可通过鉴权只读 API 检查 received/persisted/duplicate/dropped/write_error/pending/error，无需停机打印 close metrics。

## Rollback

1. 设置 `research_event_capture.enabled=false`。
2. 重启 bot 使配置生效；不重建或重启 NapCat。
3. 新库为独立 side-channel，可保留供审计；确需清理时在 bot 停止后删除配置指定 DB/WAL/SHM 文件。
4. legacy `topic_corpus.db` 和在线话题块继续按原逻辑工作，无数据迁移依赖。
5. 仅回滚 2026-07-13 指标 API 时，将 `omubot-bot:pre-research-metrics-20260713` 重标为 latest，再 `docker compose up -d --no-deps --force-recreate bot`；不得操作 NapCat。

## Test Ledger

| ID | Command | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| T1 | `source ./scripts/dev/env.sh && uv run pytest -q tests/test_research_event_store.py` | RED: 5 failed，目标 API 不存在；GREEN: 5 passed | append-only store、全字段往返、AI outbound、重开读取、幂等和指标契约成立。 | 2026-07-12 |
| T2 | 同上，加入 `actor_id` 契约 | RED: 5 failed，构造器不接受 `actor_id`；GREEN: 5 passed | 人类与 AI 均有可链接伪名 actor 标识。 | 2026-07-12 |
| T3 | `source ./scripts/dev/env.sh && uv run pytest -q tests/test_research_event_recorder.py` | RED: 3 failed，Recorder 不存在；GREEN: 3 passed | 有界队列、close drain、queue-full 非阻塞丢弃、写异常隔离成立。 | 2026-07-12 |
| T4 | `source ./scripts/dev/env.sh && uv run pytest -q tests/test_research_event_capture_wiring.py` | RED: 9 failed；GREEN: 9 passed | config、router、scheduler 与 plugin lifecycle wiring 成立。 | 2026-07-12 |
| T5 | `source ./scripts/dev/env.sh && uv run pytest -q tests/test_research_event_store_schema.py` | RED: 4 failed / 1 passed；GREEN: 5 passed | DB 0600、`user_version=1`、固定表名、nullable message_id 与 quick_check 成立。 | 2026-07-12 |
| T6 | 七份 `tests/test_research_event_*.py` | 最终 39 passed | privacy、allowlist、platform time、batch、None message id、取消恢复、init cleanup、CQ 结构化全部 GREEN。 | 2026-07-12 |
| T7 | `pytest` topic/corpus/router/coalesce 相关五文件 | 42 passed | 旧 TopicBlockTracker、legacy corpus 与 coalesce 无行为回归。 | 2026-07-12 |
| T8 | `pytest tests/test_scheduler.py tests/test_scheduler_chat_lock.py` | 首轮 1 个随机概率测试恰好 15 次触界失败；该例单独连续两次通过，完整 suite 重跑 85 passed | 既有随机测试抖动，非本改动回归。 | 2026-07-12 |
| T9 | config/backup + plugin/types | 47 passed + 47 passed | 默认配置可加载；sensitive backup 登记与 PluginContext 契约通过。 | 2026-07-12 |
| T10 | targeted Ruff/Pyright + 临时 DB smoke | Ruff passed；targeted Pyright 0 errors；rows=2、persisted=2、duplicate=1、mode=0600、quick_check=ok、user_version=1 | 静态、结构、运行时、负向、幂等证据齐全。全仓 Pyright 仍有 368 个既有跨模块/缺依赖错误，与本改动无关。 | 2026-07-12 |
| T11 | `tests/test_outbound_group_access_guard.py` + dynamic Bot smoke | RED 35 failed；GREEN 35 passed；运行态首次部署暴露动态 `__getattr__` marker 误判后新增 RED 为 1 failed / 35 passed，根修后 36 passed；guard/trace 均安装且幂等 | 任意 `send_group_*`、generic 群发送、坏 group ID、插件直发和动态 typed API 都在公共 `call_api` 边界受控；同根 protocol trace marker 一并修复。 | 2026-07-12 |
| T12 | 合并部署前回归 + Docker runtime | 408 passed；Ruff passed；targeted Pyright 0 errors；日志出现 capture armed / outbound guard installed / protocol trace installed / OneBot connected；DB `0600`、quick_check=ok、user_version=1 | 最终镜像通过结构、行为与运行态门禁。公开 silent 群 38 条入站期间 reply=0、scheduler send=0、Traceback=0。 | 2026-07-12 |
| T13 | 容器内 immutable/query-only SQLite schema + aggregate audit | DB 12,288 B、WAL 4,152 B、SHM 32,768 B；`user_version=1`、`quick_check=ok`；13 rows=`inbound 4 / outbound 9`，2 runs，唯一 reply predecessor 命中，duplicate/伪名/方向异常均 0 | 首批生产 raw rows 结构和完整性通过；13 行均早于当前容器启动，不能冒充当前进程 metrics。Phase 2 assignment/utterance 表仍未实现。 | 2026-07-13 |
| T14 | `tests/test_research_event_admin_api.py` RED→GREEN + admin regression + Docker runtime | RED 8 failed（404）；GREEN 10 passed；research+admin 60 passed；research suites 50 passed；Ruff passed；Pyright 0；未登录 401、登录后 200 healthy，8 metrics 全 0 | 关闭运行期指标只能在 shutdown 读取的监控缺口；动态 ctx、disabled/unavailable/error/degraded、异常不泄漏和统一鉴权契约成立。 | 2026-07-13 |

## Prelaunch Risk Matrix

- P0 complete: router 在 self-message 排除后、presence/blocked/pair-guard 前逐原始事件非阻塞入队。
- P0 complete: scheduler 仅在 `send_group_msg` 成功后记录；失败重试、无 bot、空消息不伪造 outbound。
- P0 complete: Capture 自身 fail-closed allowlist；inbound/outbound 均受约束。
- P0 complete: plugin 初始化失败不影响 bot，shutdown 可在调用方取消后恢复 drain/close。
- P0 complete: 部署 secret 驱动 HMAC；event/group/actor/@ 标识均伪名化，CQ 传输码不进入研究正文。
- P0 complete: store schema version 1、固定 raw 表名、DB 0600、`PRAGMA quick_check=ok`。
- P0 complete: 单 writer 有界队列，真实 `append_many` 单事务批写，公开 dropped/error/pending 指标。
- P0 complete: `OutboundGroupAccessGuard` 在共享 `bot.call_api` 边界按最终 active group policy fail-closed；生日 tick、管理员工具、插件直发、typed API 均不能越过公开群门禁。
- P1 explicit boundary: Phase 1 只保证 scheduler 主 LLM 文本出站；echo、工具、贴纸、生日等直接发送路径后续统一到发送观察器。
- P1 explicit boundary: history loader 暂不接入，schema 保留 `source=history`，不得把 live 覆盖描述为历史全量。
- P2 reserved: versioned `topic_assignment`、stable `block_uuid`、reason/score/margin 与 `utterance_membership`。

## Next Step

Phase 1 已完成首次生产行审计和运行期指标闭环。生产 raw DB 现有 13 行，均来自旧 run；2026-07-13 09:51 新容器启动后两个 active 群尚无自然消息，鉴权状态 API 因此为 healthy 且八项全 0。继续被动监控即可，任何 dropped/write/store error 应先停采集排查；`pending` 是正常批处理瞬态，不能单独判故障。Phase 2 仍按预留接口实现 versioned topic assignment/stable block UUID，不改写 raw event；本轮未启动该阶段。

## Mid-Term Phase 0 Follow-Up

2026-07-12 中期架构 Phase 0 修复了在线 `TopicBlockTracker` 三个纯内存状态错误：全冷却后新块脱离 canonical registry、容量治理早于当前 arrival apply、reservoir revive 双重 activity bump。研究事件 schema、allowlist、coalesce、L1 权重、衰减参数和 Phase 2 预留均未改变。新增 6 cases / 13 events 的仓库内纯合成确定性回放，只证明状态不变量；真实 graph-F/F1、错并错拆率和阈值调优仍等待双标注数据。部署只替换 qq-bot，公开群保持 silent_learn，NapCat 未操作。详见 `docs/tracking/architecture-mid-term-phase0-2026-07-12.md`。
