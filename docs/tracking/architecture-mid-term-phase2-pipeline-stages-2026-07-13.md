# Omubot 中期架构 Phase 2：回复管线阶段与核心模块削薄

> 状态：complete
> mode: task
> 开始：2026-07-13 CST
> 当前下一步：Phase 2 已完成并部署；恢复 `docs/tracking/advanced-topic-block-prelaunch-2026-07-12.md` 的自然流量监控。
> 阻塞：无。补充 final reviewer 因持续 429 未返回，已作为工具侧未完成记录，不替代首轮独立复审与逐项 RED/GREEN 证据。
> 回滚入口：按 stage adapter 切片回退 Phase 2 新模块和旧入口委托；不回退 Phase 1 Composition Root，不改 schema/config，不操作 NapCat。

## Resume Capsule

- objective: 让 Router 最终只保留协议转换、入口门禁和 pipeline dispatch；Scheduler 只保留群级状态机、参与决策与并发控制；LLMClient 只保留 provider 调用、工具循环与生成协议。
- next_step: Phase 2 完成；不要重做 pipeline extraction，转回话题块自然流量监控。
- baseline: Phase 2 已部署；全量 `2951 passed / 17 skipped`；qq-bot image `8578838d6c6c` / container `a4785cf15cb3` / restart=0；NapCat 未变化。
- do_not_redo: 不重做 Phase 0/1、同行/论文审计、research capture、outbound guard；不以机械拆文件作为完成标准。
- dirty_worktree: 大量用户既有改动；禁止 `git add -A`、禁止回退无关文件。

## Scope

1. 建立明确的 reply pipeline stage contract，优先复用现有 `ReplyRun` / `LLMRequest`，不再向四个 God Objects 增加跨域状态。
2. 以 adapter 渐进迁移 Router、Scheduler、LLMClient 职责，每个切片保持旧入口和运行行为。
3. 为新边界启用 targeted Pyright 0 error、取消/失败路径测试与独立复审。
4. 第一批只选择依赖方向能真实改变、可独立回滚的 stage；不追求一次拆完三个大文件。

## Out Of Scope

- DatabaseCatalog、migration ledger、retention/backup profile（后续 M3 独立阶段）。
- BackgroundTaskSupervisor（后续 M4 独立阶段）。
- Admin God module 拆分、前端改版。
- research schema、TopicBlock 算法/参数、群策略、人格与 prompt 内容调整。
- 微服务、PostgreSQL、远程插件市场、多平台适配或替换 NoneBot2。
- NapCat restart/recreate/down。

## Frozen Acceptance Principles

- Router 的 NoneBot decorator、matcher priority、ingest lock、silent_learn/outbound guard 与 protocol trace 顺序不变。
- Scheduler 的 per-group chat lock、coalesce、arbiter、ReplyRun terminal/delivery 语义不变。
- LLMClient 的 provider request、tool loop、streaming、usage accounting、guardrail/humanization observable behavior 不变。
- 新 stage 不直接依赖 NoneBot event、OneBot Bot 或完整 `PluginContext`，除非它本身就是协议 adapter。
- 外部取消必须传播；已取得所有权的资源/状态必须补偿；失败不得写入虚假 delivered/committed 状态。
- 每个切片先有具体 assertion RED，随后最小 GREEN；最终需独立复审无 Critical/Important。

## Frozen First-Batch Sequence

1. Router `RuntimeConnectionPipeline`：把 connect/disconnect decorator 变成纯 dispatch，业务状态由 `services/routing/connection_pipeline.py` 持有。
2. Scheduler `OutboundDeliveryPort`：只迁单次 humanizer + OneBot transport + research/pair-guard side effects，retry/mute/sent_event 仍由 Scheduler 所有。
3. LLM `VisibleReplyGuardrailStage`：只迁 visible guardrail orchestration；provider dispatch/spine 与 tool loop 保留在 LLMClient。

选择标准：每刀都有 typed input/output 或明确 port，改变依赖方向和状态 owner，并可单独回退。

## Verification Gates

- [x] Router / Scheduler / LLMClient 三条只读审计收齐。
- [x] 第一批 stage contract、owner、输入输出和失败语义冻结。
- [x] 新增迁移清单和行为回归矩阵。
- [x] 每个切片 RED/GREEN，取消与失败路径覆盖。
- [x] 扩大定向 pytest、targeted Ruff、targeted Pyright 0。
- [x] 全量 pytest 不低于 Phase 1 baseline，或差异有明确解释。
- [x] 独立复审首轮无 Critical，4 个 Important 全部逐项 RED/GREEN 关闭；无 outstanding Critical/Important 证据。
- [x] 仅替换 qq-bot；公开 silent 群零出站；NapCat 不变。

## Parallel Work Ledger

| Target | Scope | State |
| --- | --- | --- |
| `/root/phase1_application_contract_plan` | Router 职责与第一 extraction seam，只读 | completed after 429 reconnect |
| `/root/phase1_chat_contract_plan` | Scheduler 职责与第一 extraction seam，只读 | completed after repeated 429 reconnect |
| `/root/phase1_llm_character_red` | LLMClient 职责与第一 extraction seam，只读 | completed after 429 reconnect |
| `/root/phase2_connection_red` | Router Connection Pipeline failure/cancel RED | failed_after_30m; ordinary failure test survived, cancel scope later closed through review RED/GREEN |
| `/root/phase2_outbound_delivery_red` | Scheduler typed stage RED/GREEN、failure/cancel/best-effort | completed |
| `/root/phase2_guardrail_stage_red` | LLM typed stage/terminal tests；Phase 2 production 独立复审 | initial review completed after transient 429; found 4 Important |
| `/root/phase2_final_reviewer_rebuilt` | 用户明确授权重建的补充最终只读复核 | incomplete after repeated 429/target missing；未伪记成功，收口依据仍是首轮独立 findings + RED/GREEN/full suite |

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| P2-T0 | 连续性恢复：AGENTS/session/ACTIVE/Phase1 tracker/git status；读取架构审计 M2 | Phase1 complete；M2 推荐顺序为 Router -> Scheduler -> LLMClient；工作区仍脏 | Phase2 必须单独立项，不重做 Phase1，不混入 M3/M4/M5 | 2026-07-13 |
| P2-T1 | 三条原 canonical 代理并行只读审计 | 三条均收到临时 429，未产出审计结论 | 进入 30 分钟 reconnecting 恢复窗，禁止替换或主线接管 | 2026-07-13 |
| P2-T2 | 原 canonical 代理恢复 | Router/LLM/Scheduler 均由原 target 产出完整审计；无替代代理接管 | 第一批顺序冻结为 Connection -> OutboundDelivery -> VisibleGuardrail | 2026-07-13 |
| P2-T3 | Router connection delegation RED/GREEN | 2 RED；新增 `install_connection_handlers` 后 Router wrapper 测试 3 passed，Ruff/Pyright 0 | 建立 decorator 纯 dispatch seam，尚未接 production pipeline | 2026-07-13 |
| P2-T4 | RuntimeConnectionPipeline first-connect/reconnect/disconnect/name-registry/usage RED/GREEN | 各切片先 RED；累计 connection tests 9 passed，Ruff/Pyright 0 | 新 owner 已承接核心连接状态，failure/cancel/integration 待完成 | 2026-07-13 |
| P2-T5 | Turn interruption recovery | production/test edits完整；`phase2_connection_red` 暂时 missing，followup 返回 path not found | 按 30 分钟规则保留原 target，不重建、不由主线接管 failure test | 2026-07-13 |
| P2-T6 | LLM normal terminal stage delegation RED/GREEN | 先 1 RED（stage calls=0）；接线后 guardrail/drift 7 passed，Ruff/Pyright 0 | drift repair -> typed stage -> humanization 顺序保留；persona runtime bot name 来源不变 | 2026-07-13 |
| P2-T7 | LLM tool-exhausted terminal regression | 连续 5 tool rounds 后 stage 1 次、7 字段一致、最终 stage-cleaned；测试立即 GREEN | normal/tool-exhausted 已由同一 production adapter 覆盖 | 2026-07-13 |
| P2-T8 | Router Composition Root RED/GREEN | 2 RED（assembly 无 pipeline/未传 setup）；接线后 application/router/connection 21 passed，Ruff/Pyright 0 | Router 内嵌 connect/disconnect 已删除，正式入口注入 RuntimeConnectionPipeline | 2026-07-13 |
| P2-T9 | Scheduler OutboundDelivery RED/GREEN 与集成 | stage 15 passed；集成先 1 RED（fake calls=0），接线后相关 38 passed，Pyright 0 | 单次 humanizer/send/research/pair side effect 下沉；retry/mute/sent_event/metric 留 Scheduler | 2026-07-13 |
| P2-T10 | Phase 2 扩大回归 | application/router/scheduler/research/guardrail/tool-loop 353 passed | 三条 pipeline 扩大面无回归 | 2026-07-13 |
| P2-T11 | 全量与静态门禁 | full pytest `2948 passed / 17 skipped`；targeted Ruff clean；targeted Pyright 0；diff-check clean | 高于 Phase1 2926/17；full Ruff 的 177 项均来自无关 coursework/research/ipv6 脏文件 | 2026-07-13 |
| P2-T12 | 独立复审首轮 | 无 Critical；4 Important：connect cancel claim、stale disconnect owner、rate-limit uid、ReplyRun metric blocking；2 Minor | 4 Important 必须 RED/GREEN 后再部署；installer low-level guard 留 Minor | 2026-07-13 |
| P2-T13 | 4 Important RED/GREEN | 4 条均先目标断言 RED；修复后 connection/scheduler 核心 29 passed；新测试 Pyright 由 9 errors 修到 0；Ruff/Pyright/diff clean | 首连取消可重试、连接代际 owner 正确、retry uid 刷新、metric 不阻塞 cleanup | 2026-07-13 |
| P2-T14 | 复修后全量 | `2951 passed / 17 skipped`，仅既有 aiohttp/aiosqlite warnings | 复修后全量高于初次 Phase2 2948/17 | 2026-07-13 |
| P2-T15 | 白名单候选 image 准备（未切换） | 从 Phase1 image 仅覆盖 9 个 production 文件；9/9 SHA、py_compile、import smoke；candidate `8578838d6c6c` | rollback tag `omubot-bot:pre-midterm-phase2-20260713` 指向 `0f10a0e34f97`；运行态未变 | 2026-07-13 |
| P2-T16 | 部署与运行验收 | image `8578838d6c6c` / container `a4785cf15cb3` / restart=0；Application/OneBot/capture/guard/trace/Admin/9 SHA 全绿；公开 2 条 silent 入站，chat/send/error=0 | 只 force-recreate bot；NapCat `19f6cf13607c` / StartedAt / restart=0 不变 | 2026-07-13 |

## Next Session Starts Here

1. Phase 2 已完成，不重做 Router/Scheduler/LLM stage extraction 或 4 个 review Important。
2. 运行回滚只使用 `omubot-bot:pre-midterm-phase2-20260713` 替换 qq-bot，不操作 NapCat。
3. 下一主线恢复进阶话题块 Phase 1 自然流量监控；DatabaseCatalog/TaskSupervisor/Admin 拆分仍是后续独立阶段。
