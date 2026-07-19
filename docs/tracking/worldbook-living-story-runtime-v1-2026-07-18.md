# Worldbook Living Story Runtime v1

> 状态：complete · 2026-07-18 · Stage 0 源码实现、终审修复与离线验收完成；默认关闭；未部署

## Objective

把现有“Persona + 日程 + 单 StoryArc + Dream 反思”升级为可审计、可回滚、可长期连续的分层世界书运行时，使角色能够：

1. 在没有群聊刺激时仍保持自己的日常生活和目标；
2. 拥有主线、副线、未解决问题和可恢复的长期故事；
3. 只依据有作用域和证据的群聊事实与群友交融；
4. 在聊天与日程中按需调用原生虚构世界 Canon；
5. 通过事件账本和因果链保持长期连续；
6. 通过 Storylet 与 Drama Manager 产生有条件、有代价、有后果、有恢复的冲突。

## Fixed architecture decisions

### 1. Persona Canon

- 保存身份、价值观、硬规则、稳定表达边界。
- runtime read-only；群聊、Dream、Schedule 均不可直接改写。
- 继续以 Persona v2 freeze 为身份真值，不复制第二份 Persona 数据库。

### 2. Native World Canon

- 保存原生世界中的实体、地点、组织、规则、时间线、别名和实体关系。
- 只从版本化配置源加载；不得由聊天或 Dream 自动写入。
- 使用关键词、别名、正则和可选语义分数触发；不全量常驻 Prompt。

### 3. Life State

- 保存当前地点、活动、待办、欲望、顾虑、心情、精力、可用性和临时约束。
- 每条状态带 source、confidence、scope、updated_at、decay_at。
- 高频状态与 Canon、历史记忆分离。

### 4. Social Evidence

- 复用现有 Social Narrative factual 数据，不新建不受控事实库。
- 必须携带 group_id、user_id、message/evidence ref、时间和 privacy。
- 只允许当前群/当前用户作用域进入聊天投影。
- 后台 Schedule 不注入群友事实；Dream 只能生成 proposal，不能把真人线下行为写成事实。

### 5. Story Ledger

- 复用并扩展 StoryArcStore，避免第二个冲突账本。
- 支持一个 main arc、多个 side arc 和 ambient thread。
- Arc 保存 stage、goals、variables、open_threads、deadlines、causal_links、participants、event history、revision。
- 所有状态变化通过事件 reducer；禁止每日固定数值递增。

### 6. Storylet Registry

- Storylet 是结构化候选事件，而不是自由 Prompt 片段。
- 至少保存 conditions、saliency、severity、once、cooldown、delay、cost、consequence、recovery、required_evidence、scope。
- 条件判断和重复控制由代码执行；LLM 只负责候选事件的自然语言表现。

### 7. Drama Manager

- 负责候选排序、冲突升级、事件预算、代价和恢复窗口。
- 单 Arc 的重大 setback 保持有界；连续冲突必须允许恢复。
- 不负责伪造事实、修改 Canon 或单独裁定故事质量。

### 8. Reflection / Dream proposal gate

- Dream 输出分为 proposal、validated、committed。
- Dream 可提出 Arc replan、reflection 和 fiction event proposal。
- factual、Persona Canon、Native World Canon 永不由 Dream 自动晋升。

### 9. Prompt Projection

- 每轮只投影相关 Canon、Life State、活动 Arc、当前场景、当前群 Social Evidence 和今日短期叙事方向。
- 每块带 source、scope、priority、token/character budget、evidence refs 和 trace。
- 聊天路径通过 PromptProviderBus 注入；Schedule 使用同一 projection service 的无社交证据视图。

## Runtime flow

    input/query
      -> scoped trigger and retrieval
      -> Canon/Life/Arc/Social candidate blocks
      -> Storylet conditions + Drama Manager
      -> bounded Prompt Projection + trace
      -> LLM generation
      -> validated event extraction
      -> event reducer
      -> Story Ledger/Life State revision
      -> episodic record / Dream proposal

## Feature gates

- worldbook.enabled: global runtime gate, default false.
- worldbook.chat_projection_enabled: chat PromptProvider gate, default false.
- worldbook.schedule_projection_enabled: Schedule projection gate, default false.
- worldbook.storylet_enabled: Storylet/Drama selection gate, default false.
- worldbook.dream_proposal_enabled: Dream proposal bridge, default false.
- worldbook.social_evidence_enabled: current-scope factual social bridge, default false.

关闭总开关时不得：读取 Canon registry、改变 Prompt、选择 Storylet、改写 Arc/Life State、增加后台任务或改变现有 Schedule/Dream 行为。

## Implementation workstreams

### A. Domain and persistence

- 新增 worldbook models、validation、atomic JSON/YAML loader 和 store。
- Canon immutable；mutable state 使用 revision/CAS 或等价乐观并发保护。
- 所有 scope、privacy、evidence、TTL 字段 fail-closed。

### B. Trigger, projection and provider

- 实现 keyword/alias/regex trigger、级联实体命中、稳定排序和总预算。
- 实现 chat PromptProvider 和 schedule projection adapter。
- 记录候选、命中原因、预算裁剪和 evidence refs。

### C. Story ledger, Storylet and drama

- 扩展 StoryArc 为 main/side/ambient stack。
- 实现 event-driven reducer，替代固定变量增长。
- 实现 once/cooldown/delay/severity/event budget/recovery。

### D. Social/Dream bridges

- Social 只读且严格当前 scope。
- Dream 仅写 proposal；validation 前不得进入 factual 或 Canon。

### E. Runtime/config/docs/tests

- bootstrap/config 接线，默认关闭。
- 迁移清单、维护日志、结构/语义/负向/运行时测试。

## Implementation result

- 新增 `services/worldbook/`：领域模型、配置、atomic/CAS store、Canon/Storylet registry、触发器、Story Ledger adapter、Drama Manager、event reducer、Dream proposal bridge、Prompt Projection、PromptProvider 与 runtime facade。
- 新增 `plugins/worldbook/`：manifest、默认配置、schema 与生命周期接线；插件 priority 45，在 Chat/ProviderBus 完成后、Dream 之前启动。
- 新增 version-controlled authoring 文档与 Canon/Storylet JSON Schema；两个 registry 初始为空，不预装未经核验的世界事实或事件。
- StoryArc 兼容扩展 `arc_role/stack_order/status/deadlines/causal_links/event_history`；旧 JSON 使用默认值，无 bulk migration。
- Schedule gate 开时独立 provision 共享 StoryArcStore，不依赖 legacy `schedule.story_arc_enabled`；使用显式 main arc、共享无 Social Evidence 投影和 committed event reducer；多个 legacy arc 无显式 main 时 fail-closed，不回退 mtime；gate 关时保留旧行为。
- Storylet runtime 使用真实 step 与 ledger/social evidence，once/cooldown/delay/setback budget 通过 StoryArcStore 原子 `update` 持久化；持久化失败时 fail-closed，不把未记账事件投影进 Prompt。
- committed event reducer 校验 `event.arc_id`，使用不随可读 history 淘汰的精确 event-id 集保证长期重放幂等；可读 `last_events/event_history` 仍保持有界。
- Dream gate 开时只保存确定性 proposal，不写卡、不直接更新 Arc、不晋升 factual/Canon；gate 关时保留旧行为。
- CardLookup query 同模式隐私缺陷一并修复：只检索当前 user、当前 group 和 global，二次校验 scope_id；其他 user/group 不可见。
- 未改 Admin route/menu/frontend；未改 `config/config.json`；未触 QZone/NapCat/Docker/凭据/数据库/远端状态。

## Acceptance

1. Canon 不可通过 runtime mutation API 改写。
2. 世界条目只在关键词、别名、正则或实体级联命中时进入投影。
3. 总预算和分层预算可预测；高优先级原子块不可被截成半条事实。
4. group A Social Evidence 在 group B、私聊或后台 Schedule 中不可见。
5. 无 evidence ref 的 factual social item fail-closed。
6. 主线与副线可以同时加载，排序稳定；不存在按 mtime 随机抢主线。
7. Storylet once/cooldown/delay 可重复运行且确定；重大 setback 不突破预算。
8. 变量只由 committed event reducer 修改，不再因“过了一天”固定增长。
9. Dream proposal 不得直接修改 factual、Persona Canon 或 Native World Canon。
10. worldbook.enabled=false 时，相对本轮开始时已部署的 QZone v0.8.2 baseline，Worldbook 不改变 Prompt、Schedule、Dream 行为。
11. 所有投影块可从 trace 追溯到 source/scope/evidence。
12. 不触碰 QZone live gate、NapCat、凭据或远端状态。

## Final audit disposition

只读终审返回 **0 Critical / 11 Important**。逐条复现后的处置如下：

| # | 结论 | 处置 |
| --- | --- | --- |
| 1 | 误报 | Schedule 不再读取 `dream_reflection`、Dream 不再写合成 global memory 是已部署的 QZone v0.8.2 真实性修复，不属于 Worldbook gate 泄漏，保留不回退。 |
| 2 | confirmed | Worldbook Schedule gate 独立 provision/注入 StoryArcStore。 |
| 3 | confirmed | Storylet 使用真实 step/evidence，并在成功原子持久化 budget 后才投影。 |
| 4 | confirmed | Social Evidence 严格当前 group + 当前 user，保留 privacy/confidence，private/cross-scope 拒绝。 |
| 5 | confirmed | Life State 强制 source/scope/privacy/updated_at/TTL，过期、缺元数据或不可信重标记 fail-closed。 |
| 6 | confirmed | 多 legacy arc 无显式 main 时不调用会走 mtime 的 `ensure_seeded()` 回退。 |
| 7 | confirmed | `always_active` 不再构成 Canon 激活路径，authoring schema 固定为 false。 |
| 8 | confirmed | 未知/拼错 Storylet condition fail-closed。 |
| 9 | confirmed | reducer 精确 committed-id 集不随 history 或 512 阈值淘汰。 |
| 10 | confirmed | `event.arc_id` 与目标 Arc 不一致时拒绝 mutation。 |
| 11 | confirmed | EventProposal domain/store 只接受 proposal 状态与允许来源，递归拒绝 factual/Canon payload。 |

Codex 首轮验收另外拒收了两个二阶缺口：`committed_event_ids` 原 512 上限仍会让最早事件重放，以及 Storylet budget 持久化失败后仍返回候选。两项均补 RED 并修为精确长期幂等与 persist-success-before-project。

## Boundaries

- 保留当前 dirty worktree；禁止 reset、checkout、clean、git add -A。
- 同一文件/模块只允许一个 writer；Grok 内部并发不得重叠写入。
- 不部署、不提交、不推送、不真实发布。
- 不自动迁移现有生产数据；新 store 为空时必须安全降级。
- 不把多 Agent 社会模拟接入 QQ 热路径。
- 不启用 BUILTIN_WIRE_PROFILE.validated，不更改 QZone approval/live 配置。

## Verification matrix

| Layer | Required evidence |
| --- | --- |
| RED/GREEN | 新行为先有可观察失败，再最小实现通过 |
| Structural | schema、registry、arc stack、trace 字段完整且可 round-trip |
| Semantic | Canon/Life/Social/Arc 不串层；冲突有代价和恢复 |
| Negative | 跨群泄漏、无 evidence factual、Dream 事实晋升、重复 Storylet 均拒绝 |
| Compatibility | 总开关关闭的 Prompt/Schedule/Dream 回归 |
| Static | focused ruff、pyright、JSON/YAML/schema 检查 |
| Runtime | 离线组装真实 runtime，确认 provider 注册/关闭态与投影结果 |
| Collision | 现有 StoryArc/Dream/Social/QZone 真实性合同不回归 |

## Test Ledger

| Time | Experiment | Actual result | Conclusion |
| --- | --- | --- | --- |
| 2026-07-18 | repository recovery | ACTIVE 原为 none；目标目录存在大量既有 dirty changes，schedule/dream 有重叠 | 采用新模块优先、最小接线、逐文件 diff 审查；禁止清理工作树 |
| 2026-07-18 | architecture freeze | 九层运行时、六个 feature gate、社会事实与 fiction 隔离、默认关闭 | 可以派发边界清晰的并行实现包 |
| 2026-07-18 | Grok top-level implementation | 首次 503 自动恢复；落地 12 个 worldbook core modules、plugin scaffold 与 StoryArc 扩展；两次 context compaction 后重复读、未产出测试/接线，进程按 checkpoint 停止 | 保留 Grok 实质代码贡献，不把未完成接线计作完成 |
| 2026-07-18 | Grok focused rebuild | 再次 503 自动恢复；窄任务包连续读取约 5 分钟仍无落盘，退出码 130 主动停止 | 剩余测试/接线显式收回 Codex 主线，未并发竞写 |
| 2026-07-18 | Codex RED/GREEN | 依次复现并关闭 reducer 重放、Dream nested Canon/factual、Life TTL、social cross-scope/metadata/privacy、Storylet 合同、plugin manifest/order、Schedule projection/event reducer/main arc、global memory scope、config bounds 等 13 类缺口 | 领域边界、运行接线、关闭态与负向合同完成 |
| 2026-07-18 | pre-final-review focused collision suite | Worldbook 40 passed；Schedule/Dream/QZone authenticity/plugin contracts 163 passed；plugin manifest config cluster 23 passed | 证明初始实现可运行，但不替代后续只读终审 |
| 2026-07-18 | same-pattern scope scan | production `search_cards` 位点为 CardStore、CardLookup、Dream maintenance tool、Schedule；Schedule 改 global，CardLookup 改 user/group/global + scope_id；Dream 为后台维护工具、不进入 chat/Schedule projection | 两个用户可见热路径的无 scope 检索已关闭 |
| 2026-07-18 | pre-final-review full repository run | **4882 passed / 17 skipped / 186 warnings** | 单测全绿仍未发现 11 项语义/接线缺口，因此追加独立只读终审 |
| 2026-07-18 | read-only final review | **0 Critical / 11 Important**；逐条复现为 1 误报、10 confirmed | 不接受原“complete”结论，进入第二轮 RED/GREEN |
| 2026-07-18 | Grok core audit repair | 两个 focused Grok 会话均遇 503 后恢复；修复 Storylet/Social/Life/trigger/conditions/reducer/proposal；首轮自报 53 passed | Codex 独立 probe 仍发现 512-id 淘汰与 persist-failure Storylet 泄漏，拒收并发回同一 workstream |
| 2026-07-18 | Codex integration repair | RED 复现 Schedule gate 未 provision store、多个 legacy arc 落回 mtime；2 项 GREEN | Worldbook Schedule gate 与 legacy StoryArc gate 解耦，主线缺失 fail-closed |
| 2026-07-18 | Grok focused correction + Codex acceptance | 取消 committed-id 语义淘汰；Storylet budget 改原子 update 且 persist fail-closed；Codex 修正一条 Python 3.13 event-loop 测试隔离问题 | 两次 Codex 拒收均已闭环 |
| 2026-07-18 | final focused acceptance | Worldbook + integration **58 passed**；Schedule/Dream/QZone/plugin collision **132 passed**；Ruff clean；targeted Pyright **0 errors / 0 warnings**；JSON/schema/gates/diff-check clean | 终审 11 项与二阶缺口全部有回归证据 |
| 2026-07-18 | final full repository acceptance | **4900 passed / 17 skipped / 186 warnings**，零失败 | Stage 0 source acceptance 最终通过 |
| 2026-07-18 | real offline runtime probe | 真实 PromptProviderBus 注册 worldbook；Canon query 产 1 个 world scope candidate，evidence=`canon:place.stage`；Schedule/Dream bridge 绑定；disabled 配置不建目录/不注册 provider | 结构与单测外的实际生命周期/关闭态成立 |

## Rollback

1. 保持总开关默认关闭可立即熄火。
2. 源码回滚仅移除本 tracker/migration 列出的 worldbook 新文件和最小接线 hunks。
3. 新 store 使用独立目录；未部署前无生产数据回滚。
4. 若未来部署，bot-only rebuild/recreate；永不 restart/recreate NapCat。
5. QZone 继续沿用 v0.8.2 live-locked 配置，不纳入本模块回滚。

## Next step

完成。本轮停在 Stage 0：所有 gate 默认 false，未部署。若用户另行授权，下一步只能按迁移清单进入 Stage 1 离线 fixture/shadow projection，再决定是否在指定开发群开启 chat projection；不得直接全量启用或联动 QZone live。
