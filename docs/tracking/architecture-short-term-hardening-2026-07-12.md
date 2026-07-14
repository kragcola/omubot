# Omubot 短期架构加固执行追踪

## Objective

执行 2026-07-12 架构审计确认的五项短期工作：为插件 hook 与工具执行建立硬超时和依赖契约；修正插件启停真实语义；建立 `ReplyRun` 最小主链；冻结 `PluginContext` 并引入类型化领域能力入口；建立可追溯来源的 `EffectiveConfigSnapshot`。

## Status

- mode: none
- phase: complete
- started_at: 2026-07-12
- completed_at: 2026-07-12
- current_step: 五项全部实现、复审、回归并完成运行态验证
- deployment: deployed to `qq-bot` image `b94a17dfc08a` / container `50b3e58a0e7a`; NapCat unchanged
- source_audit: `docs/audits/omubot-architecture-and-peer-audit-2026-07-12.md`
- parallel_recovery: 原代理多次 429；保留同一 target 恢复，最终实现、RED 证据与两轮独立复审均已交付，无遗留 reconnecting scope。

## Scope

### S1 Hook / Tool Deadline + Dependency Contract

- PluginBus 按 hook 类型执行硬超时。
- 超时进入现有 health / burst / soft-isolation 统计。
- 外层任务取消必须继续传播，不得被误记成插件超时。
- ToolRegistry 工具执行硬超时，同名工具冲突不再静默覆盖。
- 插件依赖区分 required / optional；required 缺失、禁用或版本不兼容时 fail-closed。

### S2 Plugin Toggle Truth

- 盘点所有 user/runtime 插件的后台任务、DB、网络和 startup 初始化。
- 仅完整支持资源停启的插件保留 runtime toggle。
- 其余改为 restart_required，Admin state API 不得假装已热应用。
- ToolRegistry 候选完整验证后原子替换，失败恢复全 Bus 状态。

### S3 ReplyRun Minimum Vertical Slice

- 定义类型化 `ReplyRun`、阶段枚举和阶段记录。
- 先接普通群回复与主动群回复的共享最小字段。
- 接入现有 BlockTrace 或等价可观测出口，不改变回复决策。
- 不在本阶段拆 Router / Scheduler / LLMClient 大文件。

### S4 Freeze PluginContext + Typed Capability Bundles

- `PluginContext` 不再新增单项 service locator 字段。
- 新增最小类型化领域 bundle，优先 Conversation / Runtime / Persona。
- 新代码只通过 bundle 取新增能力；旧字段保持兼容，不做一次性迁移。
- 新 contracts targeted Pyright 0 error。

### S5 EffectiveConfigSnapshot

- 汇总 defaults、主配置、环境、group policy、plugin override 的有效值与来源。
- Admin 提供只读 effective snapshot，不泄露 secret。
- snapshot 能明确字段来源和是否需要重启。
- 现有保存路径和运行配置解析行为保持不变。

## Out Of Scope

- 不做微服务化、PostgreSQL、消息队列、远程插件市场或跨平台适配。
- 不进行 Router / Scheduler / LLMClient 全量拆分。
- 不清空全仓 Pyright 债。
- 不借架构任务修改人格、话题块、学习或群聊产品行为。
- 不重启或 recreate NapCat。

## Acceptance Gates

- [x] 每个行为切片先有明确 RED，再做最小 GREEN。
- [x] S1 具备 hook timeout、tool timeout、取消传播、依赖 fail-closed 回归。
- [x] S2 的 runtime / restart_required 状态与真实资源生命周期一致。
- [x] S3 普通与主动回复路径都携带同一 ReplyRun 契约，输出行为不变。
- [x] S4 新 capability contract targeted Pyright 0 error，PluginContext 字段不继续增长。
- [x] S5 snapshot 对配置来源可解释，secret 只返回 mask/存在性，不返回原值。
- [x] 定向测试、相关回归、Ruff、targeted Pyright 通过。
- [x] 运行态 smoke 只重建 qq-bot，NapCat identity 不变。
- [x] `git diff --check` 通过；维护日志记录实际落地与未完成项。

## Parallel Ownership

- `architecture_deadlines`: `kernel/bus.py`、`services/tools/registry.py` 及其新增/相关测试。
- `effective_config_snapshot`: 新 snapshot service、Admin 只读 API 及独立测试；不得改 `kernel/bus.py`。
- `architecture_contract_design`: 只读审计 S2/S3/S4 接线位置和迁移风险，不编辑文件。
- root: tracker、S2/S3/S4 实现、集成、回归、文档和部署决策。

## Test Ledger

| ID | Slice | Command | RED | GREEN | Conclusion |
| --- | --- | --- | --- | --- | --- |
| T1 | S1 hook timeout | `pytest tests/test_plugin_bus.py tests/test_tools.py` | hook 预算只统计慢调用；取消吞噬可越过预算；内部 TimeoutError 被误分类风险 | 93 passed（与 T2/T3 合跑） | hard deadline、外层取消传播、内部 TimeoutError 区分、health timeout/soft-isolation 成立；生命周期默认无 deadline，显式 budget 可选。 |
| T2 | S1 tool timeout/conflict | 同 T1 | 工具无 deadline；同名覆盖；取消吞噬可延迟副作用 | 93 passed | 默认 30s + per-tool override；duplicate fail-closed；候选 registry 原子替换。 |
| T3 | S1 dependency contract | 同 T1 | required 缺失/禁用/版本/循环与 provider startup failure 未完整 fail-closed | 93 passed | legacy dependencies 视为 required；optional 可降级；required cycle/provider failure/runtime bypass 全覆盖。 |
| T4 | S2 toggle truth | `pytest tests/test_plugin_toggle_policy.py tests/test_admin_api.py -k plugin` + JSON/Ruff/Pyright | state API 缺 `applied` 且直接关闭实例；10 个资源插件 restart set 为空 | 15 passed；23 manifests JSON valid；Ruff 0；Pyright 0 | restart_required 只持久化 pending state，不热改实例/刷新工具；10 个资源插件纠正，9 runtime + 4 locked 保留。 |
| T5 | S3 ReplyRun | `pytest tests/test_reply_run.py tests/test_scheduler.py tests/test_scheduler_chat_lock.py tests/test_scheduler_rws.py tests/test_arbiter_scheduler.py` + targeted Ruff/Pyright | `ToolContext.extra` 无 `reply_run`；随后 outcome 为 None | 106 passed；Ruff 0；Pyright 0 | proactive/triggered 共用 metadata-only run；stream/non-stream delivery 分阶段记录；cancelled/failed/skipped/completed 终态可观测；research send 未改。 |
| T6 | S4 typed bundles | `pytest tests/test_plugin_capabilities.py` + targeted Ruff/Pyright | `AttributeError: PluginContext has no attribute service_capabilities` | 3 passed；Ruff 0；Pyright 0 | Conversation/Runtime/Persona 只读 bundle 从 legacy fields 即时构造；late capture/guard assignment 可见；dataclass fields 不增长。 |
| T7 | S5 config snapshot | `pytest tests/test_effective_config_snapshot.py tests/test_config_loader.py tests/test_admin_api.py` + targeted Ruff/Pyright + real config/API smoke | import/KeyError/404/secret 漏脱敏等逐切片 RED | 77 passed；Ruff 0；Pyright 0；真实主配置 534 条等价，API 666 entries / GET 200 / POST 405 | default/main/env/group policy/plugin default+override 来源链成立；secret 仅 present+mask；未改 load/save。 |
| T8 | baseline integration | `source ./scripts/dev/env.sh && PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest -q tests/test_plugin_bus.py tests/test_tools.py tests/test_admin_api.py -q` | baseline | passed | S1/S2/S5 相关既有回归基线全绿。 |
| T9 | deployment identity baseline | `docker inspect ... qq-bot napcat && docker compose ps` | baseline | passed | qq-bot `ef3b0120...` / restart 0；NapCat `19f6cf13...` / created 2026-06-22 / restart 0；全部核心容器 running，sidecar/watchtower healthy。 |
| T10 | independent review fixes | 7 个定点 RED + 两轮只读复审 | boot-disabled runtime 未初始化；假 DELIVERED；partial startup 不 cleanup；派生 config 来源错；registry/依赖闭包非事务 | 7 passed；无 Critical/Important 遗留 | runtime warm init、真实发送信号、失败 startup cleanup、派生来源、全 Bus toggle 事务已关闭。 |
| T11 | final integration | 全量 pytest + Ruff + targeted Pyright + diff check | 一次既有概率 mood case 抖动 16/30，单独复跑通过 | 2865 passed / 17 skipped；Ruff passed；Pyright 0；diff check passed | 五项与共享 research/outbound dirty 基线兼容。 |
| T12 | runtime smoke | container/import/hash/API/log/policy checks | BuildKit lease 损坏；legacy builder 又受失效 8890 代理阻断 | 白名单派生镜像 20/20 SHA256；imports ok；API GET 200/POST 405/666 entries；插件 startup/dependency failure=0 | 仅替换 qq-bot；3 个抽样公开群均 silent_learn 且发送计数 0；NapCat 身份不变。 |

## Rollback

- S1：恢复 PluginBus / ToolRegistry 旧调用方式与依赖解析；新增测试可随同回退。
- S2：恢复原 manifest toggle policy 与 state API 行为。
- S3/S4：移除新 contracts 和 adapter 接线；旧上下文与调用链保留兼容。
- S5：移除只读 snapshot API/service；不影响现有配置保存和加载。
- 任何运行态回滚只允许重启/重建 `qq-bot`，不得操作 NapCat。

## Preserved Work

- 进阶话题块 Phase 1 已部署，继续等待开发群自然流量复核 research DB；本任务不修改采集 schema、allowlist 或 TopicBlockTracker。
- 集成冲突基线：`kernel/types.py` 已有 `research_event_capture` / `outbound_group_access_guard` 用户改动；`kernel/config.py`、Router、Scheduler、ChatPlugin 共有 231 行 research capture / outbound guard 未提交改动。S3/S4/S5 必须在这些改动之上增量接线，不得覆盖或还原。
- 角色包剩余缺口继续保留在独立 tracker，本任务不接管。

## Next Step

本任务完成。恢复独立的进阶话题块 Phase 1 自然流量监控；架构中期项继续以审计文档为输入另行立项。本次旧镜像保留为 `omubot-bot:pre-arch-hardening-20260712`，回滚只替换 qq-bot，不操作 NapCat。
