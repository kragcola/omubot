# Omubot 中期架构 Phase 1：Composition Root 与生命周期所有权

> 状态：complete
> mode: task
> 最后更新：2026-07-13 CST
> 当前下一步：Phase 1 已完成并部署；后续只处理独立 pending 项，不继续扩大本阶段。
> 阻塞：无。最终独立复审无 Critical/Important。
> 回滚入口：逐切片回退 `bootstrap/`、`bot.py`、`plugins/chat/plugin.py` 与本 Phase 新增测试；不操作 NapCat。

## Resume Capsule

- objective: 把 `bot.py` 的进程级装配和 `ChatPlugin` 的跨域服务装配提取为显式生命周期所有者，同时保持现有运行行为。
- next_step: 无；Phase 1 代码、测试、复审、文档和部署均已完成。
- current_files: `bootstrap/application.py`, `bootstrap/chat_runtime.py`, `bot.py`, `kernel/router.py`, `kernel/types.py`, `plugins/chat/plugin.py` 及生命周期相关服务/测试。
- last_verified: 全量 `2926 passed / 17 skipped`；targeted Ruff/Pyright 0；qq-bot image `0f10a0e34f97` / container `e315d8be05ea` / restart=0；公开 silent 群 9 条自然入站、出站/调度发送/错误均为 0；NapCat 未变化。
- do_not_redo: 不重新审计同行/论文；不拆 Router、Scheduler、LLMClient、Admin 或数据库；不改采集 schema、话题块参数、群策略。
- rollback: Phase 1 不含 schema/config 迁移；保留旧装配入口直到新契约全绿，可按文件切片回退。

## Scope

1. 新建独立进程级 Composition Root，显式构造 `PluginContext`、PluginBus、基础设施、插件、Router 与 Admin。
2. 新建 chat runtime assembly，接管 `ChatPlugin` 当前跨域服务的构造、启动与清理。
3. 用 `ApplicationRuntime` 表达生命周期所有权，确保启动有序、关闭逆序。
4. 任一启动步骤失败时，只清理已经成功获得所有权的资源，并保留原异常。
5. 为新边界增加类型，不扩大 `PluginContext` 全量类型改造。

## Out Of Scope

- 不拆分 Router、Scheduler、LLMClient、Admin 内部实现。
- 不迁移数据库或改变数据库所有者语义。
- 不引入微服务、PostgreSQL、容器编排层或通用依赖注入框架。
- 不修改 research capture、outbound guard、TopicBlock 或 Dialogue Climate 行为。
- 不改变插件显式注册集合、发现机制、配置覆盖优先级与 locked plugin 规则。
- 不改变公开群 `silent_learn` fail-closed 策略，不重启或 recreate NapCat。

## Frozen Behavior Contracts

| ID | Contract | Current Evidence |
| --- | --- | --- |
| C1 | 显式插件集合与注册顺序保持不变，随后才执行目录发现 | `bot.py:231-263` |
| C2 | 持久化插件状态先应用，`kernel.disabled_plugins` 后应用；locked plugin 禁止被关闭 | `bot.py:267-280` |
| C3 | `ctx.bus` 与 `CommandDispatcher` 在 Router/Admin 安装前完成 | `bot.py:281-292` |
| C4 | Router 与 Admin 各注册一次；Admin API 取得的依赖必须来自已完成的服务装配 | `bot.py:287-292` 的注释与真实时序不一致，见 Known Risk R1 |
| C5 | Chat 默认 context service 先建立，随后 ContextPlugin 可用配置实例覆盖 | `tests/test_context_plugin.py:135` 起 |
| C6 | chat runtime 只在成功构造资源后取得其关闭所有权 | `plugins/chat/plugin.py:1057-1806` |
| C7 | shutdown 先停新工作生产者与 Scheduler，再关 LLM client 与底层 stores | 当前 `plugins/chat/plugin.py:1808-1866` 的 LLM-before-Scheduler 顺序需由 RED 契约纠正 |
| C8 | 部分启动失败按已成功启动步骤的逆序清理，清理失败不得吞掉原启动异常 | Phase 1 新契约 |
| C9 | PluginBus 已成功 startup 的插件按生命周期契约清理；ContextPlugin takeover 不回退 | `tests/test_plugin_bus.py`, `tests/test_context_plugin.py` |
| C10 | Application startup 保持 `PluginBus -> plugin tools -> BackupScheduler`；工具注册不得半提交 | `kernel/router.py:1323-1333`, `services/tools/registry.py` |
| C11 | Application shutdown 尝试 `tick loop -> BackupScheduler -> PluginBus` 全部清理，单项失败不阻断后续项 | 当前 `kernel/router.py:1335-1340` 不满足 |
| C12 | 首次连接的一次性初始化、guard/trace wrapper 顺序与 reconnect 行为不变 | `kernel/router.py:1344` 起 |
| C13 | Chat startup 发布 context 字段必须具备提交/回滚边界；失败或取消后后续插件不可见半成品 | 当前 `ChatPlugin.on_startup` 不满足 |
| C14 | 正常 shutdown 不恢复 Chat 默认 context service，保留 ContextPlugin takeover 语义 | `tests/test_context_plugin.py:135` 起 |
| C15 | Chat close 覆盖 character registry/cache 与 climate metric connections，且单项失败不阻断其余 finalizer | 当前 `ChatPlugin.on_shutdown` 不满足 |

## Planned Boundaries

### `bootstrap/application.py`

- 进程级 Composition Root。
- 构造基础 `PluginContext` 与进程级服务。
- 注册显式插件、执行 discovery、应用状态/config 覆盖。
- 安装 Router 与 Admin，确保 exactly-once。
- 返回显式 `ApplicationRuntime`，不在 import 边界隐藏资源所有权。
- `ApplicationRuntime.start()` 保持 `PluginBus -> atomic plugin tools -> BackupScheduler`。
- `ApplicationRuntime.stop()` 显式停止 tick loop，并隔离各关闭步骤的错误，保证后续清理仍执行。

### `bootstrap/chat_runtime.py`

- 只承接当前 ChatPlugin 中跨域 chat 服务的装配与清理。
- 按成功启动顺序记录清理动作，失败和正常 shutdown 共用逆序清理路径。
- `ChatPlugin` 保留消息 hooks、命令与插件身份，只委托 runtime startup/shutdown。
- 每个成功创建的可关闭资源立即登记 finalizer，每次发布 `ctx` 字段记录旧值；启动失败/取消时逆序清理并恢复旧字段。
- 正常 shutdown 只关闭本 assembly 所有资源，不回滚已被 ContextPlugin 合法替换的 `ctx.context_service`。

## Known Risks

### R1 Admin router currently snapshots incomplete startup state

- `bot.py:289-292` 在 NoneBot startup 前调用 `create_admin_router(_plugin_ctx)`。
- `admin/__init__.py:30-60` 会立即读取并闭包捕获 `usage_tracker`、`msg_log`、`card_store`、`scheduler`、`llm_client` 等引用。
- 这些服务多数直到 `ChatPlugin.on_startup` 才写入 `ctx`，所以“AFTER all services are wired”注释不成立，Admin 部分路由可能永久持有 `None`。
- Phase 1 不能机械搬运现状；必须以最小方式建立 typed lazy dependency provider，或把安全的 router factory 安装点移到服务装配完成后。最终选择必须由 exactly-once 与依赖可用性契约验证。
- `setup_routers()` 当前同时注册 lifecycle 和消息/事件 handlers；若选择启动后挂载 Admin，不应顺带大拆 Router。

只读复现：Admin router 创建后再注入 `ctx.usage_tracker`，`GET /api/admin/usage/data` 仍返回 `200 {"error":"Usage tracker not available"}`。

### R2 Application shutdown can strand owned resources

- `kernel/router.py:1335-1340` 中 `backup_scheduler.stop()` 一旦抛错，`bus.fire_on_shutdown()` 不会执行。
- `kernel/router.py` 在首次连接时启动 `bus.start_tick_loop()`，进程 shutdown 未显式调用 `stop_tick_loop()`。
- Phase 1 需要一个幂等或明确拒绝重复调用的 `ApplicationRuntime.stop()` 契约，并保证一个 cleanup 失败不会阻断剩余 cleanup。

### R3 Plugin tool collection is not transactional

- PluginBus 必须先 startup，因为 `ToolRegistry` 当前由 Chat startup 创建。
- 当前逐项 `register()` 遇到重复 tool name 时可能留下前半部分工具。
- Phase 1 只增加“基于现有 registry 的原子合并”最小能力；不得用 plugin tools 全量替换并清掉 Chat 核心工具。

### R4 Chat startup is non-transactional

- PluginBus 会隔离 Chat startup 异常并继续启动后续插件；当前 Chat startup 已创建的 DB/session/task 和已发布 `ctx` 字段不会自动回滚。
- Character registry/cache、UsageTracker、BlockTraceStore、LLMClient 等在构造到发布之间均有泄漏窗口。
- Usage API route 是不可回滚的全局副作用，必须推迟到成功提交并保证 exactly-once。

### R5 Chat shutdown order and coverage are incomplete

- 当前顺序为 `LLM -> Scheduler -> Coalescer`；正确依赖顺序应先 drain Coalescer，再停止 Scheduler，再关闭 LLM。
- 任一 close 异常会中止全部后续清理。
- `CharacterRegistryDB`、`RecognitionCache`、M1/M2 climate metric connections 当前未关闭。
- 建议顺序：Coalescer -> Scheduler -> ScheduleGenerator/Hawkes/HealthGuard -> ResearchCapture -> LLM -> climate metrics -> stores -> character DB/cache -> StickerStore。

### R6 Affection dependencies are wired too early

- `GroupMemoryConfig` 在 `AffectionEngine` 之后加载，因此当前条件注入取得 `None`。
- `AffectionEngine.set_runtime_state_bus()` 未接线。
- 这是已实证行为修正，但必须作为机械迁移后的独立切片，避免混淆提取回归。

### R7 Composition build has a legacy migration side effect

- `PluginStateStore` 构造为纯路径绑定，读取不写盘。
- `PluginConfigStore.__init__()` 会执行 legacy aggregate config 到 per-plugin config 的迁移写盘。
- 因此 `build_application()` 不能被文档或测试描述为纯函数；构造测试必须使用临时 `plugin_data_dir/plugin_root`，生产路径语义保持不变。

### R8 Chat startup publishes non-context global state

- 除约 58 个 `ctx` 字段外，startup 还调用 `set_humanizer()`、schedule `set_self_name()`，并直接向 NoneBot app `include_router()`。
- 前两项需要在 assembly commit/rollback 中记录和恢复全局旧值，或明确延后到全部资源成功后一次性提交。
- Usage router 必须延后到 commit，重复 startup 不得重复注册；机械提取不能漏掉这些非 `ctx` 副作用。

### R9 Process install has mixed duplicate behavior

- `install_loguru_sink()` 已用模块级 sink id 做幂等保护。
- `FastAPI.add_middleware(AdminAuthMiddleware)` 与 `include_router()` 没有本项目级重复安装保护。
- `install_application()` 应由 runtime 记录 installed 状态并对第二次调用 fail-fast；不依赖扫描 Starlette/FastAPI 内部列表猜测是否重复。

## Preserved Plugin Startup Order

```text
calendar_context, chat,
datetime, group_admin, http_api, web_fetch, web_search,
history_loader, context, knowledge, affection, schedule, food,
memo, sticker, slang, style, dream, bilibili, echo,
element_detector, debug_commands
```

Shutdown 必须完全反转。相同 priority 的 discovery 插件依赖目录字母序与稳定排序，不可改用无序集合。

## Acceptance Gates

- [x] 三条只读审计线收齐并与源码证据核对。
- [x] 每个行为切片先出现具体 assertion RED，再以最小代码转 GREEN。
- [x] 显式插件集合/顺序、发现顺序、状态覆盖与 locked plugin 契约通过。
- [x] Router/Admin exactly-once 契约通过。
- [x] chat runtime 启动顺序、逆序关闭与部分启动失败清理通过。
- [x] ContextPlugin takeover 回归通过。
- [x] 外部取消能传播且不产生虚假“成功关闭”状态。
- [x] targeted pytest、Ruff、targeted Pyright 与全量 pytest 通过。
- [x] 独立代码复审无 Critical/Important。
- [x] 仅 rebuild/recreate qq-bot；公开 silent 群出站为 0；NapCat 容器身份与 restart count 不变。

## Implementation Slices

1. `ApplicationRuntime` 通用生命周期基元：已完成首条 RED/GREEN，不接入口。
2. ToolRegistry 原子追加：保留 Chat 核心工具，候选校验失败零提交。
3. Process adapters：Bus startup/shutdown、plugin tools、Backup、tick cleanup、Admin commit。
4. `build_application()`：迁移 context/插件/状态策略构造，冻结 22-plugin 顺序；`bot.py` 仍是 host/CLI 壳。
5. Router lifecycle split：只把 startup/shutdown 委托给 runtime，消息/notice/connect 行为不搬家。
6. Admin 在全部 startup 成功后作为最后 commit 安装；不在本 Phase 把所有 Admin route 改写成 lazy provider。
7. ChatRuntime 机械提取：原样迁移构造体，ChatPlugin 保留 hooks/debug/callbacks。
8. ChatRuntime hardening：ctx 发布回滚、finalizer ledger、正确关闭顺序和资源覆盖。
9. Affection 的 GroupMemoryConfig/runtime-state-bus 接线作为独立行为修复切片。

每个切片必须有自己的 RED/GREEN 和可独立回退边界；前一切片未绿不得开始后一切片。

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| T0 | 连续性恢复：读取 AGENTS/session/ACTIVE/Phase 0 tracker，检查真实 Git 根与 dirty worktree | 真实工作区为 `/Volumes/OmubotDisk/omubot`；Phase 0 complete；核心文件含既有未提交变更 | Phase 1 必须增量编辑，不重跑 Phase 0，不覆盖 research/outbound/短期架构改动 | 2026-07-12 |
| T1 | `doctor.sh`；`pytest -q tests/test_plugin_bus.py tests/test_context_plugin.py`；相关文件 targeted Ruff | doctor 0 fail/0 warn；72 passed；Ruff passed | 当前 lifecycle/context 基线健康，后续新 RED 可归因于 Phase 1 契约 | 2026-07-12 |
| T2 | bot composition 只读审计与 Admin late-inject 无写入复现 | Admin late-inject 仍返回 tracker unavailable；另确认 backup stop 可阻断 bus shutdown、tick loop 无进程清理、tool merge 可半提交 | 这些是 Composition Root 生命周期所有权内的必测缺口，不扩大为 Router/ToolRegistry 重写 | 2026-07-12 |
| T3 | Chat assembly 只读资源/任务/ctx 发布审计 | 约 58 个 ctx 字段、13 类持久资源、4 类后台任务；确认 startup 无事务、shutdown 顺序/覆盖/异常隔离缺陷 | 机械提取与 lifecycle hardening 分切片；Affection 接线再单独修复 | 2026-07-12 |
| T4 | `ApplicationRuntime` 首条 RED/GREEN：声明序启动、失败逆序补偿、stop 错误隔离、stop 幂等 | RED 4 failed；最小实现后 4 passed；Ruff passed；Pyright 0 | 进程级通用生命周期所有权基元成立，尚未接入 bot/Router | 2026-07-12 |
| T5 | ApplicationRuntime + PluginBus + ContextPlugin + ToolRegistry 相关回归 | 105 passed；targeted Ruff passed；targeted Pyright 0 | 首切片未破坏现有 lifecycle/context/tool 行为 | 2026-07-12 |
| T6 | 显式 9 插件 + `discover_plugins('plugins')` 只读装配探针 | 有效顺序与冻结的 22-plugin 列表完全一致；`bus.started=False` | build 阶段可冻结顺序且不触发 plugin startup；后续测试仍须用临时 plugin config 路径 | 2026-07-12 |
| T7 | FastAPI in-memory lifespan 探针：startup callback 内 `include_router()` 后发首个请求 | `GET /late` 返回 200 | Admin 可作为 ApplicationRuntime startup 的最后 commit，无需全量改写成 lazy provider | 2026-07-12 |
| T8 | Coalescer/Scheduler/Research close 调用方向核对 | Coalescer.close 会 flush callback -> `scheduler.notify()`；Scheduler.close 取消任务并关 corpus capture；ResearchCapture.close 自带 shield/retry owner | Chat close 必须 Coalescer -> Scheduler -> Research -> LLM；Research 不再单独重复关 recorder/store | 2026-07-12 |
| T9 | ToolRegistry atomic merge RED/GREEN | RED 4 failed（明确缺少 merge_all）；GREEN 新测试 + 既有工具测试 33 passed；Ruff passed；Pyright 0 | 插件工具可在保留 Chat 核心工具的前提下原子追加，冲突零提交 | 2026-07-12 |
| T10 | Process composition adapters RED/GREEN | RED 7 failed（明确缺少 compose factory）；GREEN application/runtime/tool 相关 44 passed；Ruff passed；Pyright 0 | Bus -> tools -> Backup -> Admin startup 与 tick -> Backup -> tools -> Bus shutdown 契约成立，尚未接 bot/router | 2026-07-12 |
| T11 | ChatRuntimeAssembly 事务基元 RED/GREEN | RED 5 failed；GREEN runtime/context 相关 22 passed；Ruff passed；Pyright 0 | publish/rollback、finalizer 逆序、取消传播、错误隔离、幂等和 ContextPlugin takeover 契约成立，尚未迁移 ChatPlugin 构造体 | 2026-07-12 |
| T12 | PluginBus build/state RED/GREEN | RED 2 failed（明确缺少 build_plugin_bus）；GREEN build/composition/plugin policy 相关 83 passed；Ruff passed；Pyright 0 | 精确 22-plugin 顺序、unstarted 状态、persisted/config 覆盖和 locked fail-closed 成立 | 2026-07-12 |
| T13 | Router lifecycle seam RED/GREEN + process Composition Root 真接线 | RED 2 failed；GREEN process/router 相关 160 passed；Ruff/Pyright/py_compile 0；bot import smoke=22 plugins、started false、Admin routes before start 0 | `bot.py` 已改为 build/install 壳；Admin 延后到 runtime startup，旧消息/connect 路由未移动 | 2026-07-12 |
| T14 | Process build/install 集成契约 | 2 passed（existing behavior GREEN）；tmp storage/plugin data，真实 plugins 只读；Ruff passed | context/bus/stores/backup identity、22 plugins unstarted、Admin pre-start 0、install runtime 传参均成立 | 2026-07-12 |
| T15 | Process 独立复审 Important 修复 | 首轮 4 Important：partial-start cleanup、Backup task leak、stop cancel ownership、setup_routers mixed duplicate；分别 RED 后 38 + 69 passed，Ruff/Pyright 0 | 4 Important 全部关闭；Admin include partial failure保留 Minor，进程启动失败不可复用 | 2026-07-12 |
| T16 | Chat 独立复审与 production ownership/commit 修复 | 首轮 4 Important/2 Minor；shared close、borrowed owner、LLM publish、application commit 均有 RED；修复后核心 31 passed，扩大 chat/research/capability 111 passed，Ruff/Pyright 0 | 4 个运行时 Important 已关闭；构造体物理迁移仍为 Phase1 完成阻断，正在执行 | 2026-07-12 |
| T17 | Affection late wiring + ScheduleGenerator 唯一 owner RED/GREEN | Affection 2 RED；Schedule owner 1 RED；修复后相关 129 passed，Ruff/Pyright 0 | GroupMemoryConfig/runtime-state 在真实加载后注入；SchedulePlugin 只启动/使用，Chat assembly 唯一关闭 | 2026-07-12 |
| T18 | 最终 process/chat 独立复审与 Important 修复 | process 1 Important；chat 3 Important/1 Minor；分别补并发 start/stop、LLM 构造清理、character init rollback、research close ledger、commit 断点重试 RED | 复核后无 Critical/Important；保留 legacy Router 与 shutdown research ctx 指针两个非阻断 Minor | 2026-07-13 |
| T19 | Phase 1 扩大回归与最终静态门禁 | 扩大定向 393 passed；targeted Ruff clean；targeted Pyright 0；py_compile/diff-check clean | Composition/chat/research/outbound/capability/LLM/character 边界无回归 | 2026-07-13 |
| T20 | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest` | `2926 passed / 17 skipped`，高于 Phase 0 `2871 / 17`；仅既有 aiohttp 弃用与 retrieval thread 收尾 warning | 全量门禁通过 | 2026-07-13 |
| T21 | 白名单 staging 部署 | 从原 image `5bd0e04205cf` 仅覆盖 14 个 Phase 1 生产文件，逐文件 SHA256、py_compile、import smoke 通过；最终显式 Python entrypoint image `0f10a0e34f97` | 避免把脏工作区无关改动带入镜像；回滚标签 `omubot-bot:pre-midterm-phase1-20260712` | 2026-07-13 |
| T22 | 部署后运行验收 | OneBot/capture/guard/trace 全部就绪；Admin login=200，Usage API 非 unavailable；公开群 9 条 `silent_learn`，send/scheduler/error=0；qq restart=0；NapCat ID/StartedAt/restart 不变 | Phase 1 已上线并满足公开群零出站红线 | 2026-07-13 |

## Parallel Work Ledger

| Target | Scope | State |
| --- | --- | --- |
| `/root/phase1_bot_composition` | `bot.py` 进程装配与最小提取边界，只读 | completed |
| `/root/phase1_chat_assembly` | Chat runtime 服务所有权/启动关闭图，只读 | completed |
| `/root/phase1_contract_tests` | 首批 ApplicationRuntime RED 已完成；下一条 ToolRegistry atomic merge RED | failed_after_30m; canonical target missing after turn interruption |
| `/root/phase1_contract_tests_rebuilt` | ToolRegistry/Application/Chat ownership 与 commit RED | completed |
| `/root/phase1_application_contract_plan` | Process adapters/build_application 规划；ChatRuntimeAssembly RED 写入 | completed |
| `/root/phase1_chat_contract_plan` | ChatRuntime 最小 RED 切片规划，只读 | completed after transient 429 recovery |
| `/root/phase1_chat_implementation` | ChatPlugin -> ChatRuntimeAssembly 机械提取与生命周期加固；仅写两个 Chat 文件 | completed |
| `/root/phase1_llm_character_red` | LLM constructor 与 character DB init failure RED | completed |
| `/root/phase1_research_close_red` | ResearchCapture close error isolation RED | completed |

## Migration Checklist

见 `docs/migrations/architecture-mid-term-phase1-composition-root-2026-07-12.md`。

## Residual Non-Blocking Minors

- `kernel/router.py` 的 `runtime=None` legacy lifecycle 分支仍保留旧的非事务工具合并/cleanup 行为；生产入口始终传新 runtime，后续可删除或 deprecated。
- Chat shutdown 后 `ctx.research_event_capture` 仍短暂指向已关闭实例；只存在于 shutdown 窗口，不会泄漏或发送消息，后续清理 context 指针时一并处理。

## Next Session Starts Here

- Phase 1 已完成，不要重复 Composition Root / ChatRuntime 提取或复审。
- 如继续中期架构，另立 Phase 2 tracker；不得顺手修改 research schema、TopicBlock 参数、公开群策略或 NapCat。
