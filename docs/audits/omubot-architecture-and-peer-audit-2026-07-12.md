# Omubot 架构与同类 Bot 对比审计（2026-07-12）

> 状态：审计完成，待按优先级分别立项
> 审计基线：`/Volumes/OmubotDisk/omubot` 当前工作树与当前 Docker 运行态
> 审计范围：核心消息链、插件体系、LLM/Prompt、调度、数据、Admin、部署拓扑，以及 AstrBot、Koishi、NoneBot2、LangBot 横向对比
> 范围裁定：项目仍处于开发阶段。本报告不把网络暴露、默认口令、密钥治理等安全项纳入中短期架构排序；安全问题另行处理。
> 旧报告关系：本报告取代 2026-05-07 三层架构审计作为当前架构判断基线，但不抹去旧报告的历史价值。

## 1. 结论

Omubot 已经不是普通的 NoneBot 插件集合，而是一个围绕 QQ 群长期陪伴场景形成的、功能高度产品化的模块化单体。

它的领先点不是平台适配数量或插件市场，而是：

- 群聊寻址、引用和回复义务能够贯穿整条回复链。
- 主动发言不是简单概率触发，而是确定规则、话题状态、RWS、Hawkes、EOT、Arbiter 等多层决策。
- Persona、关系记忆、情节记忆、表达学习、Dialogue Climate 已经形成持续人格系统。
- BlockTrace、LLM Usage、插件健康、运行时错误、协议探测和 Admin 诊断深度较高。

当前主要风险也不是“功能不够”，而是能力持续堆入四个中心模块：

- `services/llm/client.py`：6310 行，113 个方法。
- `services/scheduler.py`：2381 行，72 个方法。
- `kernel/router.py`：2267 行，42 个顶层函数。
- `plugins/chat/plugin.py`：1866 行，实际承担系统组合根。

因此当前最准确的判断是：

> 功能先进、产品差异化明确；模块目录丰富，但运行时依赖图仍偏单体，架构消化速度已经落后于功能增长速度。

中短期不应全面重写，也不应微服务化。唯一合理主线是：先建立稳定的回复管线和插件运行契约，再逐步削薄四个中心模块。

## 2. 当前运行架构

```text
QQ / NapCat / OneBot v11
  -> NoneBot Adapter
  -> kernel.router
       - 原始事件采集
       - 群访问与 presence
       - 寻址、引用、回复义务
       - PluginBus.on_message
       - 图片、表情、角色识别渲染
       - GroupTimeline 顺序提交
  -> GroupChatScheduler
       - 强触发规则
       - topic block / RWS / EOT / Hawkes / Arbiter
       - coalesce / interruption / mute / retry
  -> LLMClient
       - Thinker / instruction gate
       - PromptProvider / PromptBudget / BlockTrace
       - memory / knowledge / slang / style / persona
       - provider dispatch / tool loop / compact
       - guardrails / humanization / segmentation
  -> delivery / outbound group guard
  -> NapCat send_group_msg

同一 qq-bot 进程
  - PluginBus 与 23 个目录插件/系统能力包
  - Admin FastAPI + Vue SPA
  - 多个 SQLite store 与后台周期任务

独立进程
  - ccip-sidecar：角色识别与 embedding 计算
  - pmubot + socket proxies：容器管理与更新
```

关键入口：

- Router 与生命周期注册：`kernel/router.py:1308`
- 群消息主入口：`kernel/router.py:1474`
- Scheduler 主聊天流程：`services/scheduler.py:2130`
- LLM 总入口：`services/llm/client.py:4531`
- PluginBus 钩子调度：`kernel/bus.py:254`
- ChatPlugin 系统装配：`plugins/chat/plugin.py:1057`

## 3. 已经做得较好的部分

### 3.1 强寻址和回复义务契约

`AddressingContext`、`ReplyObligation` 与 `TriggerContext` 显式保留原始文本、昵称剥离文本、@目标、引用发送者和 `must/should/may/never`。这比依赖临时布尔值更稳定，也避免 Scheduler 或 LLM 丢失必须回复的硬证据。

证据：`kernel/types.py:276`、`kernel/types.py:296`、`kernel/types.py:311`。

### 3.2 主动发言决策层次清楚

@、纠正、追问等硬信号优先确定性处理；只有灰区消息才进入概率和角色判断。这条原则应保留，不应为了“统一 Agent”把所有决策再次外包给单次 LLM。

证据：`services/scheduler.py:688`。

### 3.3 并发和顺序一致性有专门保护

Router 使用每群 ingest lock，保证慢图片渲染不会被后到文本越过；Scheduler 又使用每群 chat lock 串行化生成。对于群聊语义连续性，这是必要设计。

证据：`kernel/router.py:940`、`kernel/router.py:1718`、`services/scheduler.py:2136`。

### 3.4 插件治理已经超过普通事件总线

PluginBus 具备逐插件耗时、错误、慢调用、健康状态和 burst 后软隔离，不只是依次调用回调。

证据：`kernel/bus.py:42`、`kernel/bus.py:674`、`kernel/bus.py:722`。

### 3.5 Prompt 与人格系统具有产品差异化

Prompt block、provider bus、预算、trace、Persona v2、memory、episode、slang、style 与 climate 已形成较完整的上下文编排体系。和通用 Bot 框架相比，这部分是 Omubot 最值得保留的能力资产。

### 3.6 可观测和管理能力较成熟

Admin 已覆盖运行健康、群策略、Provider、插件、记忆、知识、学习、BlockTrace、备份和协议。当前约 242 个测试文件、2229 个测试函数，也说明核心行为有一定回归保护。

## 4. 架构问题清单

| ID | 问题 | 当前影响 | 时间级别 |
| --- | --- | --- | --- |
| A1 | 四个核心 God Objects 持续膨胀 | 每次回复功能修改都扩大回归面 | 短期必须止增，中期拆分 |
| A2 | `PluginContext` 是 94 字段可变 Service Locator | 依赖、类型和初始化顺序不透明 | 短期冻结，中期替换 |
| A3 | 三层依赖规则已经被穿透 | 插件不可真正替换，kernel 承担业务 | 中期解决 |
| A4 | 插件启停生命周期不完整 | “关闭”可能只关闭部分行为 | 短期必须解决 |
| A5 | 插件钩子与工具执行无单次硬超时 | 单个挂起扩展可阻塞整条管线 | 短期必须解决 |
| A6 | 插件依赖 fail-open | 缺依赖时半功能启动、延迟失败 | 短期必须解决 |
| A7 | 回复阶段缺少统一数据契约 | 多个模块竞争最终决策权 | 短期必须建立契约 |
| A8 | SQLite 数据面碎片化 | migration、锁、备份、retention 不统一 | 中期解决 |
| A9 | 有效配置没有单一真相源 | Admin、文档和运行态容易漂移 | 短期必须解决 |
| A10 | 观测数据库缺少容量治理 | trace/message/slang 持续增长 | 中期解决 |
| A11 | Admin 出现 God module | 页面与 API 修改成本上升 | 中期按触达拆分 |
| A12 | 类型债削弱模块契约 | 跨模块错误依赖测试偶然覆盖 | 短期设新代码门禁，中期偿还 |
| A13 | 后台任务共享单事件循环 | 慢周期任务可能影响回复延迟 | 中期解决 |
| A14 | OneBot 细节渗入核心 | 未来多平台复用成本高 | 明确后置 |
| A15 | 插件生态仍是内部模块系统 | 不适合直接开放远程市场 | 明确后置 |

## 5. 短期必须解决：1-4 周

短期目标不是大拆文件，而是阻止继续恶化并修正运行契约。以下五项应作为唯一短期架构主线。

### S1. 给插件钩子和工具执行增加硬超时，并让关键依赖 fail-closed

当前 `PluginBus._safe_call()` 直接 `await coro`，只有返回后才检查预算；`ToolRegistry.call()` 同样直接等待工具执行。永久挂起时，现有 slow-call 统计和 soft isolation 都无法生效。

短期应完成：

- 按 hook 类型设置 deadline，并把 timeout 记入现有插件健康数据。
- 工具执行设置独立 timeout，超时返回稳定错误而不是拖住 tool loop。
- 为取消路径增加回归测试，确保取消后不污染 timeline、工具副作用或插件状态。
- 同名工具注册不再静默覆盖，至少记录冲突并拒绝不明确的后注册项。
- 把插件依赖拆为 `required_dependencies` 与 `optional_dependencies`；关键依赖缺失时拒绝启动插件。

证据：`kernel/bus.py:608`、`kernel/bus.py:674`、`kernel/bus.py:707`、`services/tools/registry.py:16`、`services/tools/registry.py:44`。

### S2. 修正插件启停语义

Admin 切换插件状态后已经会 `clear + collect_tools()` 刷新 ToolRegistry；当前不准确的部分不是“旧工具仍然注册”，而是插件已经启动的后台任务、数据库、网络资源和初始化状态不会随 `enabled` 完整停止或重新建立。

在完整动态生命周期实现前，不能继续把“只切 hook flag”展示为完整热关闭。

短期方案：

- 真正支持资源停止和重新启动的插件标为 `runtime`。
- 其余统一标为 `restart_required`，状态接口不得假装已经完整热应用。
- 系统能力保持 `locked`。
- Admin 明确显示“保存后重启生效”，不制造局部关闭假象。

证据：`kernel/bus.py:136`、`admin/routes/api/plugins.py:680`、`admin/routes/api/plugins.py:713`。

### S3. 建立统一 `ReplyRun` 与固定阶段

当前 Thinker、instruction gate、semantic gate、Arbiter、RWS、Humanizer、Guardrail 和 segmentation 主要靠调用顺序与共享状态组合。

建议定义一个贯穿单次回复的对象：

```text
ReplyRun
  input / addressing / obligation
  participation_decision
  prompt_inputs / prompt_trace
  generation_result / tool_calls
  guardrail_result
  delivery_plan / delivery_result
```

固定阶段：

```text
Ingress
  -> Addressing
  -> ParticipationPolicy
  -> ContextAssembly
  -> Generation
  -> PostProcess
  -> Delivery
  -> SideEffects
```

短期只要求新功能必须选择所属阶段并通过 `ReplyRun` 传递信号；不要求一次迁移全部旧逻辑。

验收：新增回复机制不再直接向 Router、Scheduler、LLMClient 三处同时塞状态字段。

### S4. 冻结 `PluginContext` 并建立类型化能力入口

立即规定：除修复现有问题外，不再向 94 字段 `PluginContext` 增加新服务。

新能力通过较小的领域接口进入：

```text
ConversationServices
MemoryServices
PersonaServices
MediaServices
OperationsServices
```

短期不迁移全部旧字段，只要求新代码走类型化入口，并对这些新接口启用 Pyright 0 error 门禁。

证据：`kernel/types.py:150`。

### S5. 建立 `EffectiveConfigSnapshot`

统一解析并展示：

```text
defaults
  -> main config
  -> environment
  -> group policy
  -> plugin runtime override
  -> effective runtime value
```

Admin、诊断脚本和项目状态文档都应读取同一份 effective snapshot，至少能够回答“值是什么、来自哪里、是否需重启”。这能直接减少 tracker 与运行态互相矛盾的问题。

短期验收：不再依靠读取 `config.default.json` 判断现网开关状态。

## 6. 中期应该解决：1-3 个月

### M1. 提取独立 Composition Root

ChatPlugin 不应继续创建视觉、记忆、Persona、日程、情感、图谱、LLM 和 Scheduler 等全部系统服务。应把装配迁到独立 bootstrap/composition 模块，ChatPlugin 只保留聊天能力生命周期。

这一步是后续削薄所有 God Objects 的前置。

### M2. 按管线阶段削薄 Router、Scheduler、LLMClient

推荐顺序：

1. Router 只保留协议转换、入口门禁和 pipeline dispatch。
2. Scheduler 只保留群级状态机、参与决策和并发控制。
3. LLMClient 只保留 Provider 调用、工具循环和生成协议。
4. Guardrail、Humanizer、Prompt、Compact、Delivery 分别成为独立阶段服务。

不要以“把大文件机械切成多个文件”作为完成标准。完成标准是依赖方向和状态所有权发生变化。

### M3. 建立统一数据目录与迁移账本

当前约 21 个 SQLite 数据库、54 处直接连接。中期应建立：

- `DatabaseCatalog`
- 每库 schema owner 与 `user_version`
- 统一 migration ledger
- 统一连接、WAL、timeout、checkpoint 和 close 语义
- retention 与 backup profile
- Admin 数据库健康和容量视图

不要求立即合库，也不要求切 PostgreSQL。

### M4. 建立后台任务 Supervisor

把任务分类为：

- inline：必须参与本轮回复。
- deferred：回复完成后异步执行。
- periodic：统一 supervisor 管理。
- heavy：进程池或 sidecar。

Dream、学习审核、Hawkes refresh、备份、清理等不应继续各自创建无统一生命周期的后台任务。

### M5. 按触达拆 Admin God Modules

`learning_pipeline.py`、`GroupsView.vue`、`ConfigView.vue` 已达到 God module 规模。中期采用增量规则：每次触达大模块时必须抽出一个 feature slice、composable 或 service；不做一次性全后台重写。

### M6. 建立分区类型门禁

不要求一次清空全仓 Pyright 债。优先要求：

- 新 pipeline contract 0 error。
- 新 composition root 0 error。
- `kernel/` 新增或修改边界代码 0 error。
- 旧 Router/Scheduler/LLMClient 通过 adapter 逐步迁移。

## 7. 明确后置或不建议当前实施

### 7.1 不做微服务化重写

当前主要问题是进程内职责和所有权不清，不是服务数量不足。拆成网络服务只会把共享状态、SQLite 和事件顺序问题变成分布式问题。

### 7.2 不急于切 PostgreSQL

当前单机规模下 SQLite 足够。先统一 migration、retention、连接与备份，再根据实际并发和多实例需求决定数据库。

### 7.3 不急于做远程插件市场

插件生命周期、依赖、能力边界和 SDK 尚未稳定。此时开放第三方安装只会把内部结构债扩大到生态层。

### 7.4 不把跨平台作为当前主线

OneBot 锁定是真问题，但当前产品定位仍是 QQ 群陪伴。新代码应减少 CQ/OneBot 渗透，真正的 Telegram、Discord 或官方 QQ 适配应等核心管线稳定后再立项。

### 7.5 不为架构整洁强行引入向量数据库

真实 embedding 应由检索质量需求驱动，而不是架构审计驱动。BM25/ngram 足够的路径继续使用现有实现。

### 7.6 不替换 NoneBot2

NoneBot2 仍提供成熟的 Python adapter/driver/event 基座。Omubot 的结构债位于其上层自建编排，不是更换底层框架能解决的问题。

## 8. 同类 Bot 对比

| 维度 | Omubot | AstrBot | Koishi | NoneBot2 | LangBot |
| --- | --- | --- | --- | --- | --- |
| 定位 | 长期人格与群聊社会智能产品 | AI/Agent 原生一体化平台 | 通用 TS Bot 插件操作系统 | Python 异步 Bot 内核 | 生产型 AI/Agent Bot 平台 |
| 平台适配 | QQ / OneBot 为主 | 多 IM 官方支持 | 多平台生态最强 | adapter 生态成熟 | 多平台、多 bot、多 pipeline |
| 插件生态 | 本地 23 个能力包 | 官方称 1000+ | 官方称 3000+ | 注册表约 900 | 官方称数百个 |
| LLM/Agent | 深度定制群聊链 | Agent Runner、MCP、Skills、Subagent | 核心不内置，靠插件 | 核心不内置，业务自建 | Agent、MCP、Skills、Sandbox |
| 人格/关系/情节 | 当前最强项 | 有 Persona/知识库，关系动力学较浅 | 依赖插件 | 无内置 | 有 Prompt/RAG，社会状态较浅 |
| 管理端 | 诊断深、产品定制强 | 完整 WebUI | 通用控制台最成熟 | 核心无完整 WebUI | 完整生产管理面 |
| 工程隔离 | 模块化单体、同进程插件 | 有 Agent sandbox 路线 | 同进程插件 | 同进程插件 | 独立插件进程 + Agent sandbox |

借鉴方向：

- AstrBot：Agent Runner 边界、Trace 和配置体验。
- Koishi：插件依赖、服务注入、控制台与市场治理。
- NoneBot2：继续利用 adapter/driver/event 内核，不重复造协议框架。
- LangBot：插件进程边界、pipeline 多实例与重任务 sandbox。

不应照搬：

- 不用插件数量衡量 Omubot 成熟度。
- 不把人格与群聊决策退化成通用 Agent pipeline。
- 不因 LangBot 支持独立进程就立即把所有 Omubot 状态拆散。

官方资料：

- AstrBot：https://github.com/AstrBotDevs/AstrBot
- Koishi：https://koishi.chat/zh-CN/guide/
- NoneBot2：https://nonebot.dev/docs/
- LangBot：https://docs.langbot.app/en/insight/features

## 9. 建议路线图

```text
0-1 周
  - 冻结 PluginContext 和四个 God Objects 的新增职责
  - 冻结 ReplyRun / pipeline stage 设计
  - 盘点插件 toggle 与依赖语义

1-4 周
  - ReplyRun 最小接线
  - hook 硬超时
  - required/optional dependency
  - toggle_policy 纠正
  - EffectiveConfigSnapshot

1-2 月
  - 独立 Composition Root
  - 类型化领域服务入口
  - Router / Scheduler 第一轮职责迁移
  - DatabaseCatalog 与 migration ledger

2-3 月
  - LLMClient 分阶段迁移
  - BackgroundTaskSupervisor
  - Admin God module 增量拆分
  - 新边界 Pyright 门禁
```

## 10. 最终优先级

如果中短期只能做有限数量的架构项目，按以下顺序：

1. 插件钩子/工具硬超时、取消和依赖契约。
2. 插件启停真实语义。
3. `ReplyRun` 与回复阶段契约。
4. `EffectiveConfigSnapshot`。
5. Composition Root 与类型化服务入口。
6. Router、Scheduler、LLMClient 渐进削薄。
7. DatabaseCatalog、迁移和后台任务治理。

其中 1-4 是短期必须项；5-7 是中期应该项。其余问题均不应抢占这条主线。

## 11. 审计证据与限制

本轮使用当前工作树和当前运行态作为事实源，旧 tracker 标题仅作历史参考。审计期间未修改运行配置、未重启容器、未触碰 NapCat。

本报告是架构决策基线，不代表问题已经立项或实现。任何涉及 Router、Scheduler、LLMClient、PluginContext 的正式重构，都应先建立独立 tracker、迁移清单、回归矩阵和回滚路径。
