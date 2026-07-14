# 现有插件声明与运行契约审计

## Objective

审计全部现有插件包的声明格式、代码类属性、运行时加载、依赖解析、启停策略、配置契约和 Admin 本地索引是否一致，确认从旧简化 `plugin.json` 向 manifest v3 演进后是否仍有遗漏、双重真值或运行时假合规，并给出分级整改清单。

## Status

- mode: task
- status: audit_complete_partially_remediated
- started_at: 2026-07-13 CST
- completed_at: 2026-07-13 CST
- current_step: 审计完成；Phase A 与第二轮重叠的 correctness/owner 项已部分整改
- next_step: 另立 canonical tracker 处理 ManifestV3 parser/schema/runtime version gate、剩余依赖与 task lifecycle 等平台合同余项

## Post-Audit Remediation Note (2026-07-14)

第二轮整改已覆盖本审计的部分重叠项：canonical system identity、Food 的 optional `web_search` 与 governance state、History owner 决策、Schedule 对 `calendar_context` 的 required dependency，以及 capability health 不再静态伪健康。但本审计不能标记 fully remediated：共享 ManifestV3 parser/schema、runtime `min_omubot_version` gate、旧 `kernel/manifest.py` 收敛、其余 optional dependency、Food/Memo task owner、Vision service probe 与 CI 合同仍独立开放。下方原始 finding 保留审计时事实，不据第二轮整改清零。

## Why This Audit Exists

- 当前 wiki 宣称“所有插件清单统一使用 manifest v3”，并列出 `manifest_version`、`display_name`、`tier`、`toggle_policy`、`config`、`store` 等字段。
- `docs/architecture.md` 与 `docs/setup-guide.md` 仍展示旧简化格式，并把 `plugin.json` 描述为可选覆盖层。
- 实际 `PluginBus` 还支持 `dependencies`、`required_dependencies`、`optional_dependencies` 等运行字段；`kernel/manifest.py` 的 `PluginManifest` 数据类仍是早期分发模型。
- 2026-07-12 S1/S2 已收紧 runtime/restart-required/locked 与 required/optional dependency 语义；2026-07-13 M5 只抽取 toggle transaction、pending UI 与原子工具表。现有插件声明需要确认是否全部与这些真实生命周期一致。

## Audit Scope

1. **声明格式**：全部 `plugins/*/plugin.json` 的字段、类型、枚举、命名、版本和目录名一致性。
2. **代码双重真值**：manifest 与 `plugin.py` 类属性的 name/version/priority/dependencies/capabilities/启停策略是否冲突。
3. **加载契约**：`PluginBus.discover_plugins()`、manifest overlay、依赖拓扑、缺失/不兼容依赖和旧单文件插件行为。
4. **生命周期与启停**：`locked`、`runtime`、`restart_required` 是否符合资源初始化、后台任务、工具注册和 shutdown 真实能力。
5. **配置契约**：`config.default.json`、`config.schema.json`、manifest `config` 声明和运行时 override 是否对齐。
6. **Admin/索引**：插件列表、本地包索引、治理状态和持久 target state 是否反映真实运行状态。

## Out Of Scope

- 本阶段不批量重写 `plugin.json`，不改插件业务逻辑，不调整默认启用状态。
- 不借审计修改 Admin UI、人格、群策略、工具权限或插件功能。
- 不重启/recreate bot 或 NapCat，不发送测试消息。
- 仅在发现可复现的声明/加载缺陷后提出整改方案；实现另行审批。

## Completion Definition

- [x] 建立实际 loader 接受字段、优先级和 fail-open/fail-closed 行为的唯一真值表。
- [x] 盘点全部插件包并输出逐插件合规矩阵。
- [x] 核对 manifest、类属性、配置文件、Admin/index 与运行态启停语义。
- [x] Findings 按 Critical / Important / Minor 排序并提供文件证据。
- [x] 区分“文档过时”“声明缺失但有兼容默认”“会导致真实运行错误”三类问题。
- [x] 给出最小迁移方案、验证门禁与回滚方式，等待用户批准后再实施。

## Parallel Work Ledger

| Workstream | Owner | Scope | State |
| --- | --- | --- | --- |
| P1 manifest inventory | `/root/midterm_closeout_docs_audit/m6_deploy_surface_review` | 全量 `plugin.json` 字段/类型/文件配套矩阵 | completed after repeated 429 reconnect |
| P2 loader contract | root | 文档与 `PluginBus`/manifest parser/tests 的真实契约 | completed |
| P3 runtime lifecycle | `/root/review_m4_m5_lifecycle` | 类属性、依赖、工具/任务/启停策略与 Admin toggle | completed after repeated 429 reconnect |
| P4 history/doc drift | `/root/plugin_manifest_history_audit` | 追踪格式演进来源与过时文档口径 | completed after 429 reconnect |

## Initial Evidence

| ID | Evidence | Initial conclusion |
| --- | --- | --- |
| A0 | `docs/wiki/Plugins.md` 宣称 manifest v3 为统一格式；`docs/architecture.md`、`docs/setup-guide.md` 仍给出旧简化格式 | 文档存在双口径，不能直接拿任一文档当 loader 真值 |
| A1 | `kernel/manifest.py::PluginManifest` 仍只有早期分发字段；`kernel/bus.py::_apply_manifest` 接受更宽的运行字段 | “Manifest 数据类”与“运行时 JSON overlay”不是同一 schema，存在命名与治理漂移风险 |
| A2 | 7 月 S1/S2/M5 相关测试已覆盖 `restart_required`、required/optional dependencies 与原子 ToolRegistry | 审计必须看实际资源生命周期，不能只做 JSON schema lint |

## Format Evolution Timeline

| 时间 / commit | 变化 | 当前影响 |
| --- | --- | --- |
| 2026-05-01 `4ca914e` | 引入早期 `PluginManifest` 数据类 | 该类至今基本停在旧字段集 |
| 2026-05-01 `968413c` | 为单文件插件加入简化 JSON sidecar | 旧示例只有 name/version/description/priority/author/dependencies，无版本字段 |
| 2026-05-07（后汇入 `653b7b3`） | manifest v2 中间阶段：category/permissions/settings/capabilities/min version | v2 只有维护日志可还原，没有独立 commit |
| 2026-05-08 `653b7b3` | manifest v3、目录插件、JSON config、tier/toggle/config/store、旧单文件 blocked | 当前正式声明格式的来源 |
| 2026-05-24 `8e1c336` | v1.5.0 CHANGELOG 汇总 | 文档误写未实现的 `provides → consumes` |
| 2026-06-02 `43045a4` | wiki 插件清单刷新 | 其 runtime 策略表随后被 7 月改动淘汰 |
| 2026-07-12（当前工作区改动） | 10 插件由 runtime 改 restart_required；新增 required/optional fail-closed 依赖 | manifest 版本仍为 3，但公开 schema/文档/API 未同步完整 |
| 2026-07-13 M5 | 抽取 PluginToggleService、pending state、原子工具注册 | 没有再次修改 manifest 版本 |

用户记忆对应两次演进：5 月 8 日是“声明格式升级为 v3”，7 月 12 日是“v3 内启停与依赖语义加固”。

## P4 Documentation / Contract Drift

### Important — 项目同时保留三套互相冲突的开发规范

- `docs/wiki/Plugins.md` 展示 v3 主体，但仍把全部 19 个用户插件写为 runtime；当前实际为 9 runtime + 10 restart_required + 4 locked。
- `docs/architecture.md` 与 `docs/setup-guide.md` 仍展示 5 月 1 日简化格式，包含顶层 `enabled` 与旧 `dependencies`，并把 `plugin.json` 描述为可选。
- `docs/architecture.md` 的发现/依赖段仍声称加载根目录单文件、缺失依赖仅 warning、required cycle 回退 priority；当前实现已目录化且 required 依赖 fail-closed。
- `docs/wiki/Plugin-Development.md` 只教旧 `dependencies`；CHANGELOG 声称的 `provides/consumes` 从未被实现。
- `docs/wiki/Architecture.md` 把 v3 parser/index/governance/signature 归到 `kernel/manifest.py`，实际主体在 `PluginBus` 与 `services/plugin_index.py`。

### Recommended canonical format

- 不升 v4；定义“加固后的 manifest v3”，由一个共享 parser 同时供 Bus、Index、Admin 与 CI 使用。
- 仓库内插件必须有 `plugin.json`；无 manifest 只作为外部/旧插件兼容，不作为正式包合规状态。
- 必填并验证 version/name/display_name/description/SemVer/priority/tier/toggle/category/permissions/capabilities/author/min version/config/store。
- 新声明统一使用 `required_dependencies` / `optional_dependencies`；`dependencies` 仅保留为 required alias。
- 顶层 `enabled` 不再作为正式 v3 字段；持久启停目标由 `PluginStateStore` 管理。
- 明确 `toggle_policy` 是插件生命周期策略，`config.apply_mode` 是配置应用策略。
- 保留兼容层：旧 dependencies alias、旧 min_omu_version alias、空 permissions allow-all、显式注册读取 sidecar、旧单文件只索引 blocked、非白名单 system/locked 防提权降级。

## P1 Current Manifest Inventory

### Cleared baseline

- 23/23 `plugin.json` 均可解析为 object，`manifest_version` 都是整数 `3`。
- 23/23 目录名与 manifest `name` 相等；全部 version 为 SemVer；priority/type/枚举/display_name/permissions/capabilities/config/store 均无类型异常或未知顶层字段。
- 23/23 均指向同目录 `config.default.json` 与 `config.schema.json`，文件全部存在且可解析；default 均为 schema_version=1、plugin=name、values object；schema 均为 object/properties object。
- 所有 `restart_required_fields` dotted path 当前都同时存在于 default/schema；旧检查点中的 schedule dialogue_climate 四字段缺失在本次静态扫描中未复现，当前工作区已对齐。
- 22 个可加载目录插件的 class name/version/priority 与 manifest 全部一致；`vision` 是唯一预期的 manifest-only system capability。
- strict layout 通过：plugins 根目录没有 legacy single-file plugin。

### Distribution

| Dimension | Counts |
| --- | --- |
| tier | system 4 / user 19 |
| toggle_policy | locked 4 / restart_required 10 / runtime 9 |
| category | core 4 / memory 5 / expression 4 / tool 7 / pipeline 1 / ops 2 |
| store.visibility | internal 4 / local 7 / marketplace_ready 12 |

### Confirmed anomaly — `food` required dependency 只存在于类属性（并入 P3 food dependency finding 计数）

- `FoodPlugin.dependencies = {"web_search": ">=0.1.0"}`，但 `plugins/food/plugin.json` 不声明任何依赖。
- runtime overlay 因字段缺失会保留类属性，所以当前启动依赖仍有效；本地包索引、离线审计、未来打包器和 manifest-only 工具则看不到这条 required contract。
- 当前 23 个 manifest 没有任何一个使用 `dependencies`、`required_dependencies` 或 `optional_dependencies`，说明 7 月依赖格式尚未真正落到正式包声明。

### Minor — `context` manifest 依赖基类 fallback 补元数据

- `plugins/context/plugin.json` 缺 `min_omubot_version` 与 `author`；loader 分别保留基类空字符串和 `Omubot` 默认值，因此当前不阻断运行。
- 正式仓库插件不应依赖 fallback 才满足统一静态元数据；该缺口也让 manifest-only 消费者与 loaded instance 的字段完整度不同。

### Minor — wiki 的 sticker 精确版本漂移

- 10 个 toggle policy 的表格漂移已计入前述 Important“多套规范冲突”，此处不重复计数。
- wiki 的 sticker 版本为 `1.1.6`，manifest 已为 `1.2.0`。

## P2 Loader Contract — Confirmed Findings

### Important — manifest v3 目前没有运行时 schema 门禁

- 仓库没有 manifest schema 文件；`manifest_version` 不被 `PluginBus._apply_manifest()` 或 `PluginIndexService` 校验。
- loader 只对白名单字段执行 `setattr`，不检查 required 字段、类型、枚举、目录名与 `name` 一致性，也不拒绝未知字段。
- `PluginIndexService` 只要求顶层是 JSON object；字段错误仍会得到 `manifest_status=ok`，可能把运行时加载失败显示为普通“待接入”。
- 内存复现：`enabled: "false"` 保留为 truthy `str`；`priority: "high"` 保留为 `str` 并在注册排序时触发 `TypeError`；`manifest_version: 999` 与未知字段均被静默忽略。

### Important — 最低 Omubot 版本只在索引展示，不阻止运行时加载

- `min_omubot_version` 会覆盖实例属性，但 `discover_plugins()` / `register()` 不执行版本门禁。
- 内存复现中声明 `min_omubot_version=999.0.0` 的插件仍成功注册且 enabled=true。
- `PluginIndexService` 对未加载的不兼容包标 blocked、对已加载包只标 attention；因此“当前不应接入运行时”不是 runtime guarantee。

### Important — 公开 `PluginManifest` 数据类已与 v3/运行时 overlay 分叉

- `kernel.manifest.PluginManifest` 仍输出旧字段和 `min_omu_version`，缺少 `manifest_version`、`display_name`、`tier`、`toggle_policy`、`config`、`store`、required/optional dependencies。
- 该类仍从 `kernel.__init__` 导出，但仓库没有实例化调用；开发者若用 `to_dict()` 生成清单，会产出非 v3 且 runtime 忽略旧 `min_omu_version`。
- 类文档写“类属性是运行时真实值”，而 `PluginBus` 实际以 JSON overlay 覆盖类属性，权威源描述相反。

### Important — 配置声明存在三套路径/默认语义

- `PluginConfigStore` 固定读取 `<plugin>/config.default.json` 与 `config.schema.json`，忽略 manifest 中自定义的 `config.defaults/schema`。
- `EffectiveConfigSnapshot` 读取 manifest `config.defaults`，缺失 `apply_mode` 时默认 `restart_required`。
- Admin settings 通过固定路径的 store 读取，缺失 `apply_mode` 时默认 `hot`。
- 当前插件如果都使用 canonical 文件名与显式 apply_mode，问题会被掩盖；一旦声明省略或改路径，runtime/Admin/effective snapshot 会产生不同真值。

### Important — identity 与重复名称没有结构门禁

- discovery 在加载前按目录名查重，manifest 随后可把实例 `name` 改成另一名称；`register()` 不拒绝重复运行时名称。
- 内存复现可同时注册两个同名插件；依赖解析只能检测重复后退回 priority 排序，无法恢复唯一身份。

### Important — required/optional dependency 新语义没有进入完整声明/API 契约

- `AmadeusPlugin` 只声明 legacy `dependencies`；`required_dependencies` / `optional_dependencies` 由 `_apply_manifest` 动态挂属性，类型边界无法检查。
- manifest v3 wiki 示例没有列出三类依赖字段，`kernel.manifest.PluginManifest` 也只有单一 `dependencies`。
- Admin 插件详情只序列化 `dependencies`，不返回 required/optional maps；M5 已实现的 fail-closed 依赖关系无法在插件中心完整审计。
- 当前 loader 把 legacy `dependencies` 与 `required_dependencies` 合并为 required，若开发者误以为旧字段是弱依赖，会得到真实启停阻断。

### Important — `context` 在 Bus 与 Admin 的系统白名单不一致

- `PluginBus` 白名单为 `chat/context/history_loader/vision`；Admin route 本地常量只有 `chat/history_loader/vision`。
- 只读 TestClient 复现：Bus 将 `context` 规范化为 system/locked，Admin API 又把它重算为 user/runtime、`locked=false`，会在普通用户插件列表暴露错误启停语义。
- 关闭请求最终仍被 Bus 拒绝，安全边界没有绕过；但 `PluginToggleService` 把拒绝误报成 `Plugin 'context' not found`，状态展示和错误契约都不真实。
- manifest 中任意其他非白名单 `tier=system` / `toggle_policy=locked` 也会被静默降为 user/runtime；系统能力白名单应由单一内核契约提供。

## P3 Runtime Lifecycle Findings

### Important — `food → web_search` 被错误建模成 required，且依赖恢复不可逆

- `FoodPlugin.dependencies={"web_search": ">=0.1.0"}` 会被 loader 当 required；但 food 默认 `search_enabled=false`、有本地食物库，缺 web_search 时 `_try_search()` 也正常返回 None，语义应为 optional。
- 复现：初始 food/search 均 enabled；关闭 web_search 后 dependency resolver 把 food 写成 disabled；重新开启 web_search 后 dependency_blocked 清除，但 food.enabled 仍为 false，不会自动恢复。
- 这让一个 runtime 插件的开关产生对 restart_required 插件的进程内不可逆连带状态；需要同时修依赖分类与“dependency recovered”状态恢复契约。

### Important — `food` / `memo` 存在无 owner 的 fire-and-forget task

- food 拒绝反馈路径与 memo reply extraction 都直接 `asyncio.create_task()`，只保留 set + discard callback。
- 两个插件都没有覆盖 `on_shutdown` 做 cancel/await；callback 也不读取 task exception。
- 任务可能在插件/底层 store 关闭期间继续发送或写入，并产生未回收异常。应交给 `BackgroundTaskSupervisor` 明确 owner，或实现 cancel+gather shutdown。

### Important — 多条真实 optional dependency 仍只靠 priority / shared ctx

建议显式声明：knowledge→context、memo→context、schedule→calendar_context、dream→calendar_context、style→slang。当前代码都有 None/fallback 路径，因此不是 required；但初始化顺序与能力接管仍应进入 optional dependency 图，而不是靠 priority 数值隐式维持。

### Important — `vision` capability 被 Admin 恒定伪装为 enabled/healthy

- `vision` 没有 `plugin.py`，不是 PluginBus 生命周期插件；真实 `VisionClient` 属 system service，是否可用取决于 API key/运行 probe。
- Index/Admin 对 capability-only entry 无条件返回 enabled=true、healthy，未绑定 `ctx.vision_client` 或 service health。
- 当前“系统能力声明”与“真实能力健康”混为一体；应由运行服务 probe 注入 availability/health。

### Important — `food.on_startup()` 直接改治理字段 `self.enabled`

- food 是唯一在插件代码里直接 `self.enabled = cfg.enabled` 的实际插件，绕过 PluginToggleService、PluginStateStore 与 Bus health 更新。
- config false 时可能出现实例 disabled，但 startup_succeeded、health、persistent target 仍按启动前状态记录的双重真值。
- 插件内部功能开关应使用 `_enabled`；插件治理 enabled 只能由统一状态入口决定。

### Decision — `history_loader` 的 locked 属产品策略，不是生命周期必需

- 它只在 `on_bot_connect` 做一次回填，没有长期 task/store/client，失败也降级返回；从资源生命周期看 `restart_required` 足够。
- 若产品要求历史回填永远强制执行，则应明确它是不可配置的核心启动阶段；否则 locked 会阻止用户在下一次重启选择跳过回填。此项需产品裁定，不先按 bug 修改。

### Cleared / Second-Round Erratum

- **勘误（第二轮审计）**：原结论“9 个 declared runtime 插件可安全热切”只检查了 plugin.py 自身资源，不足以代表完整 capability。`affection` 的 engine/store 由 `ChatRuntime` 在插件外构造并直接交给 LLM，runtime disable 后 thinker 关系输入仍活跃；详见 `existing-plugin-role-command-bug-audit-2026-07-13.md::I-01`。因此该清白结论撤回，今后必须沿 composition root 与直接消费者审计。
- 除 history_loader 的产品策略项外，其余 restart_required 插件在 plugin adapter 层均有静态 command、loop/tick、store、工具/异步状态或 ctx 注入等理由；但 `schedule` 也存在 ChatRuntime 外部构造资源不受 persisted plugin state 完整控制的双真值，不能再仅凭 adapter 生命周期判定“重启后完整关闭”。
- chat/context 的 locked 符合核心装配；vision 的问题是健康呈现，不是应改为普通 runtime 插件。

### Recommended lifecycle gates

1. runtime 插件禁止拥有 task/store/persistent client/register_commands，除非实现显式 enable/disable hooks 和行为测试。
2. manifest 必须显式声明 required/optional；CI 对 class 属性与 ctx producer-consumer 做差异检查。
3. 所有 create_task 必须有 Supervisor owner，或 shutdown cancel+gather。
4. capability-only 的 enabled/health 必须来自真实 service probe。
5. 依赖 disable/recover 必须有状态恢复回归，尤其 runtime dependency → restart_required dependent。

## Final Verdict

- Critical: **0**
- Important: **13**（合并重复证据后的独立整改项）
- Minor: **2**（`context` 静态元数据 fallback、wiki sticker 精确版本）
- Product decision: **1**（history_loader 保持 locked，还是改 restart_required）

当前 23 个正式 manifest 本身不是“普遍损坏”：它们全部是 v3、结构与配套配置基线通过。风险集中在两层：

1. **平台合同没有真正执行 v3**：无共享 schema/parser，版本、类型、identity、最低版本、config path 和 dependency/API 均有分叉。
2. **少数现有插件存在真实跨层错误**：context Admin 身份、food dependency/recovery/self-enabled、food/memo task ownership、vision capability health、隐式 optional dependencies。

## Proposed Remediation Order

### Phase A — 当前 correctness 反例（建议先做）

1. 单一导出 system plugin identity，修复 Admin 漏 `context`、错误显示与 `not found` 误报。
2. 将 food→web_search 改为 manifest `optional_dependencies`，并补 dependency disable/recover 状态测试；避免 dependent 被永久写 false。
3. food 内部配置改用 `_enabled`，不再直接写 governance `self.enabled`。
4. 为上述三项补 RED/GREEN，不改变其他插件策略。

### Phase B — manifest v3 真正成为可执行合同

1. 建立共享 `PluginManifestV3` parser/model，Bus、Index、Admin、CI 共用；生成 JSON Schema。
2. 对仓库内插件强制 manifest_version=3、必填字段、类型/枚举、SemVer、目录名=name、唯一 name、config/store path 与 restart fields。
3. 统一 `min_omubot_version` runtime gate；保留 `min_omu_version` 只作为显式 legacy alias。
4. 收敛或替换旧 `kernel.manifest.PluginManifest`，不再导出会生成非 v3 的模型。
5. 统一 PluginConfigStore / EffectiveConfig / Admin 的 config path 与 apply_mode 默认值。

### Phase C — 生命周期与依赖补全

1. food/memo fire-and-forget task 迁入 Supervisor owner，或实现 cancel+gather shutdown。
2. 写入 optional dependency：knowledge→context、memo→context、schedule→calendar_context、dream→calendar_context、style→slang。
3. Admin/API/前端完整暴露 required/optional dependency。
4. vision capability health 改由真实 service probe 提供，停止常量 healthy。
5. 由用户裁定 history_loader locked/restart_required。

### Phase D — 文档与门禁收口

1. 同步 architecture/setup/wiki/plugin-development/CHANGELOG，删除旧简化示例与不存在的 provides/consumes。
2. 更新 4/10/9 策略表与 sticker 版本。
3. CI 新增 manifest schema、class/manifest parity、依赖、runtime lifecycle 与 capability health 门禁。

## Verification And Rollback For Implementation

- 每个 Phase 独立 RED/GREEN、独立文件 ownership、独立回滚；不批量一次改完 23 个清单。
- 最低门禁：malformed manifest、duplicate/name mismatch、future min version、context Admin、food dependency recovery、task cancellation、vision availability。
- 继续保留 legacy aliases 与旧单文件 blocked 行为；仓库内正式插件才执行 strict v3。
- 当前审计本身只有 tracker/ACTIVE 文档变更；取消整改可将 ACTIVE 切回 `mode: none`，无需 runtime/数据库/容器回滚。

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| T0 | 文档、`kernel/manifest.py`、`kernel/bus.py`、`services/plugin_index.py` 分层读取 | wiki v3、旧简化示例、legacy data class 与 runtime overlay 四者字段集不一致 | 必须以运行时代码+复现建立真值，不能只 lint wiki 示例 | 2026-07-13 |
| T1 | 全仓查找 manifest/schema 与相关测试 | 只有各插件配置 schema，没有 plugin manifest schema；测试未覆盖 manifest_version/required fields/type/name invariant | v3 是约定式格式，缺自动门禁 | 2026-07-13 |
| T2 | 内存调用 `_apply_manifest` 注入 version=999、string enabled/priority、错 tier/policy 与未知字段 | version/unknown 被忽略；enabled/priority 不转换；非白名单 system/locked 降为 user/runtime；注册 string priority 抛 TypeError | malformed object 可绕过索引 parse-ok 并污染/阻断 runtime | 2026-07-13 |
| T3 | 注册两个相同 `name` 的插件实例 | 两个实例均进入 Bus | name 唯一性未在 register 边界 fail-closed | 2026-07-13 |
| T4 | 注册 `min_omubot_version=999.0.0` 插件 | 注册成功、enabled=true | 最低版本声明不构成 runtime gate | 2026-07-13 |
| T5 | `uv run pytest -q tests/test_plugin_bus.py tests/test_plugin_toggle_policy.py` | 74 passed | 现有启停/依赖实现基线为绿；缺口是 manifest 校验与跨层契约未被这些测试覆盖 | 2026-07-13 |
| T6 | `uv run pytest -q tests/test_admin_api.py tests/test_effective_config_snapshot.py -k 'plugin or effective_config'` | 18 passed / 37 deselected | 当前 happy-path Admin/index/effective config 回归为绿，不否定白名单与省略字段反例 | 2026-07-13 |
| T7 | standalone TestClient 注册 `context` 后读取 Admin payload 并请求关闭 | Bus=system/locked；Admin=user/runtime/locked=false；关闭返回 `Plugin 'context' not found`，enabled 仍 true | 安全边界未绕过，但 Admin 身份与错误契约真实漂移 | 2026-07-13 |
| T8 | 注册 `web_search` + FoodPlugin，关闭再开启 web_search | food 随关闭变 false；依赖恢复后 food 仍 false，dependency_blocked 已清除 | required 分类错误与不可逆 dependent disable 均可复现 | 2026-07-13 |
| T9 | `.venv/bin/python scripts/check_plugin_layout.py --strict` | `[plugin-layout] ok` | 当前没有 legacy 根目录单文件插件 | 2026-07-13 |

## Next Session Starts Here

审计已完成且已部分整改，不要重新盘点 23 个 manifest，也不要重做 M3-M6。下一步应为剩余 Phase B/C/D 平台合同建立独立 canonical tracker；不得把第二轮 10I/4M/3D 收口解释为本审计全部完成。
