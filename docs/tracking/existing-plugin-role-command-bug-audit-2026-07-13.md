# 现有插件角色、指令体系与功能缺陷第二轮审计

## Objective

在上一轮 manifest/生命周期审计基础上，重新判断 23 个插件包或能力包是否仍应作为插件存在，识别服务层合并后只剩空壳、重复编排或错误所有权的插件；审计指令注册、消息拦截和帮助/权限路径是否统一；并以可复现证据寻找插件功能 bug。

## Status

- mode: task
- status: audit_complete_remediation_verification_in_progress
- started_at: 2026-07-13 CST
- completed_at: 2026-07-13 CST
- current_step: 用户已批准全部整改；10 Important、4 Minor、3 Decision 的实现与 targeted 合同已 GREEN
- next_step: 由 `existing-plugin-remediation-2026-07-13.md` 完成 full/static/frontend/zero-outbound 与 bot-only 部署门禁

## Post-Audit Remediation Note (2026-07-14)

本审计的 10 Important、4 Minor、3 Decision 已全部映射到整改 tracker 并完成实现；当前 21 个 PluginBus 运行时插件，加 `history_loader`/`vision` 两个 manifest-only 能力包。最终状态须等全量门禁和 bot-only 部署验收后才能改为 `audit_complete_remediated`。本说明不改写下方原始 finding、RED 或审计时的 127 passed 历史证据。

## Prior Audit Assessment

上一轮 `existing-plugin-manifest-audit-2026-07-13.md` **按 manifest/loader 声明合同本身质量较高**，以下维度覆盖充分：

- 23/23 manifest 静态结构与配置配套；
- loader/index/Admin 声明合同；
- PluginBus 内 toggle_policy、依赖和插件自身声明的 task/lifecycle；
- manifest v3 格式演进与文档漂移。

但“生命周期覆盖充分”不能扩大解释为真实 capability 生命周期已经覆盖。第二轮已经反证上一轮的一个 cleared 结论：`affection` 与 `schedule` 的 engine/store/mood/generator 由 `ChatRuntime` 在插件 adapter 之外构造，PluginBus state 并不能完整关闭它们。因此上一轮“9 个 runtime 插件可安全热切”应加入明确勘误：**只检查 plugin.py 内资源不足以判断 capability 是否可热切，必须沿 composition root、LLM/Router 直接消费者和 ctx publication 继续追 owner。**

但它没有完整回答：

1. 服务层已经承接主体逻辑后，每个 plugin.py 是否仍承担必要 adapter/生命周期角色。
2. 哪些能力应是 composition/service、capability descriptor 或 router，而不是 PluginBus 插件。
3. `register_commands()`、CommandDispatcher、`on_message` 手写命令解析、工具调用和直接路由是否形成重复入口。
4. 各插件业务功能、异常路径、状态恢复和边界输入是否存在独立 bug。

## Audit Scope

### R1 Plugin Necessity / Ownership

- 对 23 个包逐一分类：保留完整插件、保留薄 adapter、迁入 service/composition、改 capability descriptor、合并到其他插件、退役候选。
- 检查 plugin.py 代码量、hook/command/tool/admin route、服务构造、store/task 所有权、跨插件 ctx 写入与实际业务主体位置。
- 区分“薄插件是正确 adapter”与“只剩重复壳层”。

### R2 Command / Entry Consistency

- 盘点所有 `register_commands()`、CommandDispatcher 注册、`on_message` 对 `/xxx` 的手写判断、router 内建命令、工具触发和 Admin 操作入口。
- 检查名称/alias 冲突、帮助生成、权限门禁、参数解析、群聊/私聊语义、runtime toggle 后残留、错误返回和未知子命令行为。
- 判断应统一到声明式 Command，还是保留 on_message 事件拦截。

### R3 Functional Bug Hunt

- 基于源码、现有测试、运行日志和最小内存/TestClient 复现寻找实际 bug。
- 每个 bug 必须包含症状、稳定复现、根因、影响面与现有测试为什么未覆盖。
- 不以代码味道、潜在风险或产品偏好冒充 bug。

## Out Of Scope

- 不修改、删除、合并任何插件；不改指令、默认配置或 UI。
- 不重启/recreate bot 或 NapCat，不发送测试消息，不写生产数据库。
- 不重复上一轮 23 份 manifest 格式盘点；只引用已确认结论。
- 修复和重构必须在本轮审计后单独审批。

## Executive Verdict

- 当前是 **22 个可加载插件 + 1 个 manifest-only capability (`vision`)**。
- **没有应立即整包删除的插件。** 服务主体迁到 `services/` 后，`chat/context/memo/knowledge/slang/style` 等仍承担 PluginBus hook、命令/工具、配置、启停、store/task 或 provider adapter；“薄”是正确形态，不等于冗余。
- `vision` 已经完成“删除伪插件、保留 capability descriptor”的正确迁移，不应重新创建 `VisionPlugin`。
- 当前核心问题不是包数量，而是：composition-owned capability 不受 plugin state 控制、系统层反向 import plugin domain、Dream 聚合无关 cron、命令 fast path 时序与 command registry 生命周期错误。
- 综合架构与 correctness 去重后：**0 Critical / 10 Important / 4 Minor / 3 Decision**。其中纯功能 bug 的核心集合经独立复审确认；架构 finding 不冒充线上故障，单独标注 ownership/contract 影响。

## 23-Package Role Matrix

| 包 | 当前真实角色 | 审计判断 | 建议边界 |
| --- | --- | --- | --- |
| `affection` | hooks/tool adapter；engine/store 由 ChatRuntime 构造 | 保留薄 adapter | engine/store/config 迁 canonical service owner；先修 plugin state 双真值 |
| `bilibili` | QQ card/URL 解析、HTTP/cache/vision、TriggerContext | 保留并薄化 | 通用 video metadata 合流 `services/url_meta`；插件保留 QQ adapter 与 trigger |
| `calendar_context` | dataset + CalendarContextService + birthday greeter | 保留 provider 插件 | 成为唯一日历能力，吸收 legacy `schedule/calendar.py`；生日 tick 归还本 owner |
| `chat` | locked core identity、群消息 hook、核心命令、ChatRuntime bridge | 必须保留并薄化 | `/debug`、`/authority` 迁 ops commands；修正“拥有全部服务生命周期”的过时注释 |
| `context` | ContextService 配置/takeover、统一检索 prompt、task cleanup | 必须保留薄 adapter | 保持 memory/doc/graph 聚合边界，不与 memo/knowledge 合包 |
| `datetime` | 39 行 ToolRegistry adapter | 保留薄 adapter | 注入 calendar capability，停止 tool service import legacy schedule calendar |
| `debug_commands` | `/plugins`、`/version` | 保留并扩充 | 吸收 Chat 的调试/权限命令，可后续更名 `ops_commands` |
| `dream` | DreamAgent lifecycle + 多种全局 tick | 保留 Dream adapter，显著薄化 | DreamAgent 独立 service；生日与 memory consolidation 归各 owner；发布 typed runtime handle |
| `echo` | 独立群消息 interceptor | 保留完整插件 | helper 下沉 kernel/service，避免 Router import plugin；不与 element 合并 |
| `element_detector` | 独立规则/LLM interceptor | 保留完整插件 | 当前规模合理；只需统一 slash-command bypass |
| `food` | command + follow-up hook + preference/search/recommendation monolith | 保留插件，迁业务 service | 抽 `FoodRecommendationService` 与 preference repository，plugin 只留入口 adapter |
| `group_admin` | 权限化群管理 tools | 保留薄 adapter | 独立权限/toggle 边界有价值，不并入通用 HTTP 工具 |
| `history_loader` | locked one-shot `on_bot_connect` 回填 | 产品决策，不立即删 | 强制核心则迁 RuntimeConnectionPipeline/capability stage；可选则保留并改 restart_required |
| `http_api` | allowlist HTTP tool adapter | 保留薄 adapter | 与 web_fetch 语义不同，不合并 |
| `knowledge` | KnowledgeService lifecycle/source export + legacy prompt fallback | 保留薄 adapter | Knowledge 是 source，Context 是 aggregator，不能合包 |
| `memo` | CardStore tools、fallback prompt、reply extraction | 保留薄 adapter | MemoExtractor 继续下沉 `services/memory`；Context takeover 后写入/tool 仍必要 |
| `schedule` | schedule/mood/domain adapter；资源由 ChatRuntime 构造 | 保留，优先单一 owner | 收敛 calendar；共享 mood/schedule API 服务化；start/stop 同 owner |
| `slang` | service-backed store/learning/tick/tool；ProviderBus 接管 prompt | 保留 adapter | Provider 迁移正确；抽共享 learning coordinator 给 tick/Admin |
| `sticker` | store tools、silent learning/retry/tick/prompt | 保留 adapter | 可继续抽 capture coordinator，插件入口仍完整有效 |
| `style` | StyleStore lifecycle、reply capture、periodic extract；ProviderBus 接管 prompt | 保留 adapter | 抽 extraction coordinator，移除 plugin→Admin 反向依赖 |
| `vision` | manifest-only system capability；真实 client 在 composition | 保持 descriptor | enabled/health 应绑定 `ctx.vision_client` probe，不创建伪插件 |
| `web_fetch` | 39 行网页抓取 tool adapter | 保留薄 adapter | 独立配置与 runtime toggle 足以构成插件边界 |
| `web_search` | 39 行搜索 tool adapter | 保留薄 adapter | Food 只是 optional consumer，不把搜索并入 Food |

### Role Conclusion

本轮所谓“合并”均是**职责迁移而非整包删除**：Chat commands → debug/ops、legacy calendar → calendar_context、Bilibili metadata → url_meta、Dream 无关 tick → Calendar/Memory owners。真正可能从 PluginBus 退到 capability/stage 的只有 `history_loader`，仍需产品裁定；`vision` 已完成此类迁移。

## Command Entry Matrix

当前文本命令主体已经统一到声明式 `CommandDispatcher`，不存在第二套 NoneBot `on_command` 框架：

| Provider | 根命令 | 子命令 | 门禁 |
| --- | --- | --- | --- |
| `chat` | `authority`、`debug` | `debug save/send/split` | 两个根命令均 admin；群/私聊 |
| `food` | `吃什么`、`food` | `help/search/like/dislike/location/info` | search admin；info private；其余公开 |
| `debug_commands` | `plugins`、`version` | 无 | plugins admin；version 公开 |

静态扫描得到 6 root / 7 root aliases / 9 subcommands / 20 sub-aliases，当前冲突为 0。Food feedback、Element、Echo、Bilibili、Slang、Sticker 属事件入口，应继续使用 `on_message`；需要统一的是**已知 slash command 在访问/静默门禁之后、所有有副作用 plugin hooks 之前执行**。

剩余公开群的零出站约束未被命令绕过：Router 在 command dispatch 之前已对 blocked/off/silent_learn/mute 群返回，只运行 `silent_safe` hooks；全仓也无直接 `on_command` matcher 旁路。

## Findings

### Critical

无。

### Important

#### I-01 — Composition-owned capability 不受 plugin state 完整控制

- `bootstrap/chat_runtime.py:591-700` 按 plugin config 直接构造 schedule/affection store、engine、generator，不读取 PluginBus enabled。
- `bootstrap/chat_runtime.py:925-949` 把 affection engine 与 mood getter 直接交给 LLM；`services/llm/client.py:4483-4543` 直接消费。
- 纯内存复现：关闭真实 Affection/Schedule plugins 后，两者 prompt hooks 均消失，但同一 LLM 仍输出 relation text 与 `mood_fit=0.74`。
- Affection 是 runtime toggle 后部分能力残留；Schedule 是 persisted false + restart 后仍构造资源、保留直接消费者。ScheduleGenerator 还由 plugin `start()`、ChatRuntimeAssembly `stop()`，owner 分裂被 `tests/test_schedule_generator_ownership.py` 固化。
- 影响：插件中心的“关闭”不是 capability 关闭；上一轮“runtime 可安全热切”结论需勘误。

#### I-02 — 系统层反向依赖 plugin domain，可插拔边界名存实亡

- `services/tools/affection_tools.py` import affection plugin engine；`services/tools/datetime_tool.py` import legacy schedule calendar；`services/humanization/qq_interactions.py` import schedule mood；`kernel/router.py` import echo helper。
- `calendar_context` 已存在，但 ChatRuntime/datetime/generator/mood 等仍使用 `plugins/schedule/calendar.py`。
- 这不是立即功能故障，但会使“关闭/替换插件”无法成为真正模块边界，并直接违反 `docs/architecture.md` 的 kernel/services 不依赖 plugins 约束。

#### I-03 — DreamPlugin 是无关能力的总 cron 宿主

- `plugins/dream/plugin.py:999-1060` 的 tick 同时驱动 birthday greeter 与 generic memory consolidator。
- Dream 自身关闭或 startup failure 会连带停止日历问候与记忆整理；这些能力与 DreamAgent 的 manifest/toggle 不同域。
- 应把 greeter 归还 `calendar_context`，consolidator 归独立 lifecycle owner；Dream 只保留 DreamAgent。

#### I-04 — DreamAgent 正在运行，但 Admin 永久显示 unavailable

- ChatRuntime 在 `bootstrap/chat_runtime.py:818-823` 固定发布 `ctx.dream=None`；DreamPlugin 只写 `self._dream_agent`（`plugins/dream/plugin.py:957-982`），从未回填 ctx。
- Admin 在完整 plugin startup 后才安装，却读取 `ctx.dream`（`admin/__init__.py:54`、`admin/routes/api/dream.py:16-35`）。
- 当前 runtime override 为 `dream.enabled=true`，`dream_2026-07-13.log` 证明 17:33 实际完成 cycle；已认证只读 `GET /api/admin/dream` 仍返回 HTTP 200 `{"available":false}`。
- 影响：状态与手动触发控制面永久失联。

#### I-05 — CommandDispatcher 是 startup 前的一次性陈旧快照

- `bootstrap/application.py:386-395` 在 PluginBus startup 前构造 dispatcher；`services/command.py:24-37` 只收集一次，dispatch 时也不校验 owner enabled。
- Debug config 到 `plugins/debug_commands/plugin.py:39-62` 的 startup 才加载；关闭 `/plugins`、`/version` 后，Admin 动态声明为空，但旧 dispatcher 仍实际执行 `/version`。
- Food config `enabled=false`、plugin startup failure、以及 runtime 关闭 `web_search` 导致 food dependency-blocked 后，旧 `/吃什么` 仍可执行。
- `PluginToggleService` 只事务刷新 tools，不刷新 commands。

#### I-06 — 群命令先按普通消息跑 hooks，再进入 dispatcher

- Router 在 `kernel/router.py:1501-1558` 先 `bus.fire_on_message`，到 `:1629-1650` 才 dispatch。
- 稳定复现：反馈窗口内 `/food 不喜欢 香菜` 被 Food 消费；`/debug 我也要吃饭吗` 被默认 Element 规则消费并回复“对”。Echo 已显式跳过 slash，是正确对照。
- 未被消费的 `/debug 显示当前状态` 也会先调用 register-classifier LLM，并写 register/willingness/memory signals；当前配置该 classifier 已开启。私聊直接 dispatch，不存在该污染。
- 修复位置必须在 access/silent/mute 门禁之后、所有有副作用 hooks 之前，不能破坏公开群零出站守卫。

#### I-07 — Affection 合法配置 `score_increment=0` 每轮除零

- Schema `plugins/affection/config.schema.json:14-22` 明确允许 0；ChatRuntime 原样构造 engine。
- `plugins/affection/engine.py:48-59` 执行 `daily_cap / score_increment`。
- 纯内存复现得到 `ZeroDivisionError`；每轮 `on_post_reply` 失败，好感/familiarity 停止更新，并可能进入 PluginBus hook cooldown。

#### I-08 — Bilibili ep/ss 被检测为支持，但没有解析/增强路径

- `has_bilibili_link()` 在 `plugins/bilibili/plugin.py:175-185` 接受 ep/ss；`extract_video_id()` `:157-172` 只返回 BV/av。
- 输入 bangumi ep 链接得到 detector=true、video_id=None，最终没有 summary/TriggerContext 注入。
- 现有测试只测 detector true，没有 ep/ss `on_message` 端到端。

#### I-09 — Food 否定口味被反向当正向偏好

- `_parse_exclusions()` `plugins/food/plugin.py:486-563` 不识别裸“不辣”；`_extract_taste_filter()` `:565-578` 仍抽到“辣”，`:627-635` 再正向筛选。
- 5 项库（3 辣、2 清淡）输入“换一个不辣的”，实际候选只剩 3 个辣食。

#### I-10 — Food 不校验 LLM 选择是否属于候选/是否违反排除

- `plugins/food/plugin.py:826-857` 只判空并清洗模型文本，不校验候选 membership、recent、dislike 或 exclusion，随后直接记录并返回。
- 候选库不含“宇宙石头汤”时，fake LLM 返回该词，实际仍被返回和写入 recent。
- 该问题与 I-09 根因、测试和修复不同，不合并计数。

### Minor

#### M-01 — Echo 同文窗口过期后不再重开

`plugins/echo/plugin.py:121-130` 只在文本变化时 reset；同文过期或已 echoed 后直接 return。时间序列 100/101/500/501/502 在 threshold=3、window=300 下始终不复读，直到出现不同文本。

#### M-02 — 群内 `/debug save` 取决于图片段顺序

`kernel/router.py:143-167` 在命令文本前遇到 image 等非 text/at/reply segment 会返回 None。`[text('/debug save'), image]` 可识别，`[image, text('/debug save')]` 与 `[reply,image,text]` 不可识别；私聊无此差异。与 Chat handler 支持“图片与命令同消息”的合同冲突。

#### M-03 — 子命令参数只正确处理字面空格

`services/command.py:106-111` 用任意 whitespace 的 `split()` 匹配子命令，却用 `if " " in args` 取参数。Tab/换行分隔会匹配到 subcommand 但把 sub_args 置空。

#### M-04 — 命令元数据与控制面仍不完整

- Admin 只展示 6 个根命令，遗漏 aliases、9 个 subcommands、继承门禁与 passthrough；前端不展示已返回 permission。
- `Command.pattern` 无消费者，`format_help()` 不传播 root admin/private 标记。
- HistoryLoader 用 `"/debug" in text` 手写过滤，误伤 `/debugger` 或正文内嵌文本。
- Dispatcher admin guard 只认 `config.admins`，部分工具内部却还合并 NoneBot `SUPERUSERS`，后者在进入 handler 前已被拒绝。

### Decision

#### D-01 — HistoryLoader 保持插件还是迁 connection stage

若历史回填是强制核心初始化，应迁 `RuntimeConnectionPipeline` 并作为 capability/status；若允许用户下一次重启跳过，则保留插件并改 restart_required。当前 locked 不是资源生命周期必需，而是产品策略。

#### D-02 — 未知根命令是否继续交给 LLM

当前未知 `/xxx` 返回 False，群聊/私聊都会继续进入普通 LLM；Food 未知子命令报错，Debug 未知文本按设计 passthrough。应明确这是自然语言兼容策略还是应统一拒绝/提示。

#### D-03 — `/food search` 是 session toggle 还是持久设置

聊天指令只改进程内 `_search_enabled`，Admin 配置写持久 override 且标记 restart_required；聊天显示“已开启”后重启会恢复文件值。两种控制面需选择同一语义或明确区分。

## Remediation Order

### Phase A — Correctness（建议先批）

1. Command fast path 移到 access/silent/mute 门禁之后、plugin hooks 之前；补 Food/Element/Chat 状态污染回归。
2. Command registry 改为 startup 后原子构建，并在 toggle/dependency transaction 中同步替换；dispatch 再校验 owner state。
3. 修 Affection `score_increment > 0` schema/model/engine 防御。
4. 补 Bilibili ep/ss resolver 或删除虚假的 detector 支持。
5. Food 建立结构化 exclusions，并对 LLM 结果做候选 membership + constraints 校验，失败走本地 fallback。
6. DreamPlugin 发布/回收 typed DreamAgent handle，修 Admin status/trigger。
7. 修 Echo window reset、group image command extraction 与 whitespace 参数解析。

### Phase B — Ownership / Thin Adapters

1. 让 ChatRuntime 使用统一 effective capability state，affection/schedule config 与 plugin state 不再双真值。
2. ScheduleGenerator start/stop 收归同一 lifecycle component。
3. domain 类型从 plugins 下沉 services；移除 kernel/services → plugins 反向 imports，并让 `calendar_context` 成唯一日历 API。
4. Dream birthday/memory ticks 归还 CalendarContext/MemoryConsolidator owner。
5. Chat `/debug`、`/authority` 迁 debug/ops command plugin；Slang/Style 抽共享 learning coordinator。

### Phase C — Product / Docs

1. 裁定 HistoryLoader stage/plugin 形态、未知根命令策略、`/food search` 持久语义。
2. Admin 命令 API 展示 aliases/subcommands/继承门禁，注册冲突改 fail-fast/health error。
3. 修 README/wiki 的 Food 权限、群消息真实顺序、Chat lifecycle 注释，并在上一轮审计写入 safe-hot-toggle erratum。

## Completion Definition

- [x] 23 个包完成“存在价值/所有权”分类与证据。
- [x] 全部指令入口完成统一矩阵与冲突/旁路审计。
- [x] 功能 bug findings 均有稳定复现或明确日志/测试证据。
- [x] 对上一轮审计给出覆盖质量与新增盲区评价。
- [x] Findings 按 Critical / Important / Minor / Decision 排序。
- [x] 给出保留、合并、退役和修复的分阶段建议，等待用户审批。

## Parallel Work Ledger

| Workstream | Owner | Scope | State |
| --- | --- | --- | --- |
| R1 plugin necessity | `/root/plugin_role_value_audit_replacement`（replaces `/root/plugin_manifest_history_audit`） | 23 包角色、服务层主体、保留/合并/退役矩阵 | completed after replacement reconnect |
| R2 command consistency | `/root/plugin_command_entry_audit_replacement`（replaces `/root/midterm_closeout_docs_audit/m6_deploy_surface_review`） | CommandDispatcher/register_commands/on_message/router/tool 入口 | completed after replacement reconnect |
| R3 bug hunt | `/root/plugin_function_bug_audit_replacement`（replaces `/root/review_m4_m5_lifecycle`） | 高置信功能 bug、复现、根因、测试盲区 | completed after replacement reconnect |
| R4 integration | root | prior audit 评价、跨线冲突消解、最终严重度与方案 | completed |
| R5 independent review | `/root/plugin_audit_independent_review` | 复核上一轮覆盖结论、本轮证据/严重度与重复计数 | completed |

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| B0 | 恢复 ACTIVE + 上一轮审计 + 架构/插件/指令文档索引 | 上一轮已完成声明与生命周期，但未形成 plugin necessity、全指令入口和功能 bug 矩阵 | 另建第二轮 tracker，不覆盖上一轮证据 | 2026-07-13 |
| B1 | Continuity recovery + `list_agents` + `git status --short` | 桌面默认 cwd 不是仓库；按 AGENTS 切回 `/Volumes/OmubotDisk/omubot`。原 R1/R2/R3 均重复遭遇 429，已按 canonical workstream 重建 replacement 并保留独立 scope | 不在 root 静默吸收子线；只在主线核实 composition/plugin 边界并整合结果 | 2026-07-13 |
| B2 | `ChatPlugin`、`ChatRuntimeAssembly`、`build_plugin_bus()` 与 Phase 1 migration 对照 | Chat 跨域资源构造/清理已迁入 runtime assembly，但 ChatPlugin 仍提供插件 identity、`on_message`、`/authority`/`/debug`、behavior callbacks 及 assembly start/close/commit bridge | `chat` 应保留薄 PluginBus adapter；顶部“Owns all system service lifecycle”注释已过时，不能据服务迁移判定退役 | 2026-07-13 |
| B3 | 纯内存按生产顺序构造 `PluginBus -> CommandDispatcher -> fire_on_startup`，分别注入 startup failure 与 `DebugCommandConfig(..._enabled=False)` | startup-failed 插件已 `enabled=False`，但旧命令仍匹配并执行；debug 配置关闭两命令后，dispatcher 仍保留 `plugins/version` 及 aliases。R3 独立复现还确认 `/version` 实际返回 True 并发送版本文本 | 命令表是启动前一次性快照；当前 `debug_commands` 注册开关失效，且所有启动后 disabled/failed 插件均可能残留命令。列为 Important 功能 bug | 2026-07-13 |
| B4 | 纯内存按群路由顺序执行 plugin `on_message` 再 dispatch：`/food 不喜欢 香菜`（有 pending feedback）与 `/debug 我也要吃饭吗`（默认 element 规则） | Food hook 返回 True 且同文直接 dispatch 本可命中 dislike handler；Element hook 返回 True 并实际发送“对”。R2 replacement 独立复现得到相同结果；Echo 已显式跳过 slash | 群聊命令在消费型插件之后处理，合法声明式命令可被 Food/Element 抢先吞掉；应把 command fast path 前移，事件拦截仅处理非命令文本 | 2026-07-13 |
| B5 | 真实 Affection/Schedule plugins + fake engines/LLMClient 纯内存切换 | disable 后两插件 prompt hooks 均消失，但 LLMClient 仍返回 affection 关系文本与 mood fit=0.74；ChatRuntime 源码按插件 config 构造服务，不读取 Bus enabled | affection 是 runtime toggle 双真值；schedule 是重启后仍部分启用的双真值。上一轮“runtime 插件可安全热切”清白结论遗漏了 plugin 外部 owner | 2026-07-13 |
| B6 | 现有 `command/food/echo/bilibili/affection` 聚焦测试 + R3 无副作用复现 | root 聚焦基线 127 passed，但稳定复现：Affection 合法 0 增量除零、Bilibili ep/ss 检测真却无 ID/summary/trigger、Food 否定反筛与模型越界、Echo 同文过期窗口不重开 | 当前测试主要覆盖 happy path，不能据全绿否定功能 bug；R3 独立确认核心功能 finding | 2026-07-13 |
| B7 | 当前配置 + `ChatPlugin.on_message('/debug 显示当前状态')` 纯内存复现 | active config 的 register classifier 已开启且 runtime_groups 空=全群；群命令先额外执行 classifier LLM，并写 register/willingness/memory relation state，随后才 dispatch；私聊无此路径 | 群聊 slash command 不仅可能被抢占，还会污染 humanization 状态并产生额外 LLM 成本；应统一 command fast path 顺序 | 2026-07-13 |
| B8 | Dream 源码接线 + 当前容器只读证据 + 已认证 `GET /api/admin/dream` | runtime override `dream.enabled=true`，`dream_2026-07-13.log` 显示 17:33 实际完成 Dream cycle；但 endpoint 返回 HTTP 200 `{"available":false}`。ChatRuntime 固定 `ctx.dream=None`，DreamPlugin 只写 `self._dream_agent` | Dream agent 未发布给 Admin，运行能力与控制面永久断线；列入 Important owner/adapter bug | 2026-07-13 |

## Verification Summary

- 现有聚焦测试：`tests/test_command.py tests/test_food_plugin.py tests/test_echo.py tests/test_bilibili.py tests/test_affection.py` → **127 passed**。
- R2 相关命令基线 → **39 passed**；上述命令缺陷均由无副作用内存复现补出。
- 纯内存/测试 double 复现覆盖 command snapshot、Food/Element 抢命令、Chat classifier 状态污染、affection/schedule toggle residual、Affection zero、Bilibili ep/ss、Food 两缺陷、Echo window、image segment order、whitespace args。
- 只读 runtime 证据：Dream 当日 cycle 日志存在；认证 GET `/api/admin/dream` 返回 `available=false`。
- 未修改插件源码、配置、Admin UI、数据库或容器；未重启 bot/NapCat，未发送群消息。

## Next Session Starts Here

本审计已完成并获批整改。继续从 `docs/tracking/existing-plugin-remediation-2026-07-13.md` 的 verification/deployment gate 收口；不要重跑 23 包盘点，也不要把第一轮 manifest 平台合同余项误计为本轮已完成。
