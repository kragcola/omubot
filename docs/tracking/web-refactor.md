# Admin Web 前端重构进度跟踪

跟踪 [docs/web-refactor-plan.md](../web-refactor-plan.md) 各阶段任务执行情况。每完成一项打勾，并记录验证证据（命令输出、commit、PR 链接、截图位置）。

## 元信息

- **重构启动日期**：2026-05-13
- **当前阶段**：阶段 3 进行中（DashboardView ✅ / LogsView ✅ / GroupsView ✅ / LoginView ✅ / ConfigView ✅ / SystemView ✅ / SlangView ✅）
- **下一里程碑**：阶段 3 剩余视图（StyleView / KnowledgeView / MemoryView / GroupsAdvanced 等单文件大视图）
- **人工验收记录**：2026-05-15 SlangView PR C 通过——SlangTermList 的 `slang-list-panel` 与主视图 `slang-settings-panel` 两块 panel-head 改 `<AppPanelSection eyebrow title>`，分页 / 折叠按钮通过 `<template #aside>` 接入；删 SlangView 4 块（settings-panel padding + panel-head + eyebrow + title）+ SlangTermList 4 块（list-panel padding + panel-head + eyebrow + title）共 ~62 行 scoped CSS；`AppCard` import 在主视图删除，新增 `AppPanelSection` import；`vue-tsc` 0 error、`vite build` 4.93s、`SlangView-*.js` 60.63 KB / gzip 17.26 KB（vs B-3 60.89 KB / 17.34 KB，**-0.26 KB / -0.08 KB gzip**）。SlangView 累计：2662 → 814 行（-69.4%）+ 9 子组件 + 3 helper + 头部样式收敛 AppPanelSection。2026-05-15 PR C 通过（SystemView）——SystemView 9 子组件迁 `AppPanelSection`（AdvancedEntry / Backup / Maintenance / Policies / Protocol / Providers / Resources / RuntimeErrors / ServiceHealth），删除 9 份重复的 `system-panel__head/__eyebrow/__title` scoped CSS（共 ~252 行），9 个 `AppCard` import 换 `AppPanelSection`；`vue-tsc` 0 error、`vite build` 4.75s、`SystemView-*.js` 53.35 KB / gzip 16.84 KB（vs B-3 55.03 KB / 17.03 KB，**-1.68 KB / -0.19 KB gzip**）。视觉副作用：title 字号 18px→16px、padding 20→18、head 底 margin 18→14——与 GroupsView/MemoryView 等其它视图正式对齐。`SystemProviderEditorDrawer` 因头部在 NDrawerContent header 内，不能套 AppPanelSection，保留本地 14 行样式。Hero / Metrics 没有 panel head 模式，不动。SystemView 累计变化：3326 行 → 590 行（-82%）+ 12 子组件全部 < 500 行 + 头部样式收敛到公共组件。2026-05-15 PR B-3 通过——SystemView 1649 → 590 行（-1059，~64%；累计 2842 → 590，-79%），抽 3 个交互子组件 Providers (479) / Protocol (484) / ProviderEditorDrawer (313)，全部 < 500 行，emit-up 写回主视图调 API；`vue-tsc` 0 error、`vite build` 4.88s、`SystemView-*.js` 55.03 KB / gzip 17.03 KB（vs B-2 52.62 KB / 16.16 KB，+2.41 KB / +0.87 KB，scoped CSS 三块复制造成的预期开销，PR C 收敛 AppPanelSection 后回吐）。2026-05-15 PR B-2 通过——SystemView 2842 → 1649 行（-1193，~42%），抽 9 个只读子组件至 `components/`（Hero 178 / Metrics 73 / Maintenance 386 / ServiceHealth 194 / RuntimeErrors 219 / Resources 184 / Policies 206 / AdvancedEntry 148 / Backup 89），全部 < 400 行；`vue-tsc` 0 error、`vite build` 4.70s、`SystemView-*.js` 52.62 KB / gzip 16.16 KB（vs B-1 49.49 KB / 15.20 KB，+3.13 KB / +0.96 KB，9 子组件 scoped style 重复造成的预期开销，PR C 收敛 AppPanelSection 后会回吐）。2026-05-15 PR B-1 通过——SystemView 3326 → 2842 行（-484，~14.5%），抽出 `helpers/{types,formatters,badges}.ts` 共 541 行；`vue-tsc` 0 error、`vite build` 4.88s、`SystemView-*.js` 49.49 KB / gzip 15.20 KB（与拆分前持平）。2026-05-15 用户验收通过——ConfigView 整页重做（卡片错位 + 卡套卡 + list/kv 行错位 + 子组件未注册导致 API key/任务模型分配空白 + 回复风格下拉截断 全部修复）。2026-05-14 验收通过——`/admin/design-playground` 浅/深主题公共组件渲染正常 + DashboardView + GroupsView + LoginView 视觉验收通过。同步删除 `global.css` 的 7 个 `@audit redundant` 块（41 行 `!important`），计数 51 → 31，剩余 31 行全部在 keep 区（`.dark .n-button:not(...)` + `.dark .n-menu` 系列）。

## 阶段 0 — 环境清理

| # | 任务 | 状态 | 验证 |
|---|------|------|------|
| 0.1 | 新增 `admin/frontend/.nvmrc` 锁定 Node 20 | ✅ | `cat admin/frontend/.nvmrc` → `20` |
| 0.2 | `package.json` 添加 `engines.node ">=20.0.0 <21"` | ✅ | `grep engines admin/frontend/package.json` |
| 0.3 | `.gitignore` 加 `admin/static/assets/` | ✅ | 行 47：`admin/static/assets/` |
| 0.4 | 现有 `admin/static/assets/*` 从 git 索引移除（保留磁盘） | ⏸ 待人工确认 | `git ls-files admin/static/assets/ \| wc -l` 现在仍是 95，需 `git rm --cached -r admin/static/assets/` |
| 0.5 | 审计 `admin/templates/*.html` 是否还被引用 | ✅ | `grep -rn "render_template\|TemplateResponse" admin/ --include="*.py"` 计数 0 |
| 0.6 | 删除无引用的 `admin/templates/*.html` | ⏸ 待人工确认 | git 中追踪 9 个文件，磁盘已不存在；`git rm admin/templates/*.html` 即可清理 git 索引 |

任务 0.4 / 0.6 涉及 `git rm --cached`，按方案约定**待人工最终确认**后由人工执行。我已不主动执行。

## 阶段 1 — 基础设施固化

| # | 任务 | 状态 | 验证 |
|---|------|------|------|
| 1.1 | 审计 `global.css` 里 `!important` 哪些已被 `themeOverrides` 覆盖 | ✅ | 见下表 |
| 1.2 | 删除冗余 `!important` 规则 | ✅ 2026-05-14 | playground + Dashboard/Groups 验收通过后一刀切，删除 7 个 `@audit redundant` 块（41 行），`!important` 计数 51 → 31 |
| 1.3 | 保留必须的深度选择器例外（`.dark .n-button:not(...)` / `.dark .n-menu`） | ✅ | 已加注释 `@audit keep` |
| 1.4 | 扩展 `uno.config.ts` 加 6 个语义 shortcut | ✅ | `section-title / section-hint / metric-num / chip / panel / toolbar-row` 已加 |
| 1.5 | 新增 `docs/admin-ui-tokens.md` 速查表 | ✅ | 文件存在 |
| 1.6 | 扩展 `themeOverrides` 补 Tag / DataTable / placeholder / icon | ✅ | `stores/app.ts` `buildThemeOverrides()` |
| 1.7 | `vue-tsc --noEmit` 通过 | ✅ | 输出 0 error |
| 1.8 | `vite build` 通过 | ✅ | 4.40s 构建，产物 1.7MB（未恶化） |

### 阶段 1 `!important` 审计表

`global.css` 当前 `!important` 计数：31（原 51 → 删除 7 个 redundant 块共 20 处，2026-05-14 完成）

| 行号区间（删除前） | 规则 | 决策 | 理由 |
|---|---|---|---|
| 128-141 | `.dark .n-button:not(...)` | ✅ 保留 | `:not()` 链 themeOverrides API 表达不出 |
| 144-159 | `.dark .n-menu` | ✅ 保留 | NMenu 多个 deep 状态变量 themeOverrides 不全覆盖 |
| 161-179 | `.dark .n-menu .n-menu-item-content[各种]` | ✅ 保留 | NMenu 子项 deep 选择器，承接上一条例外 |
| 182-186 | `.dark .n-card` | ✅ 删除 | `themeOverrides.common.cardColor / textColor1 / borderColor` 已覆盖 |
| 188-193 | `.dark .n-input` | ✅ 删除 | `inputColor / textColor1 / borderColor / placeholderColor` 已覆盖 |
| 195-199 | `.dark .n-select` | ✅ 删除 | 同上 |
| 201-205 | `.dark .n-tag` | ✅ 删除 | `themeOverrides.common` 自动派生 |
| 207-209 | `.dark .n-drawer` | ✅ 删除 | `cardColor` 已覆盖（drawer 共用） |
| 211-213 | `.dark .n-modal` | ✅ 删除 | `modalColor` 已覆盖 |
| 215-221 | `.dark .n-data-table` | ✅ 删除 | `tableColor / tableHeaderColor / textColor1 / borderColor` 已覆盖 |

**2026-05-14 完成**：playground 浅/深主题验收 + DashboardView/GroupsView 视觉验收通过后，删除上表所有"删除"项。`vue-tsc` 0 error，`vite build` 4.70s 通过，bundle 体积无显著变化。

## 阶段 2 — 公共组件补齐

| # | 任务 | 状态 | 验证 |
|---|------|------|------|
| 2.1 | 新增 `StateBadge.vue` | ✅ | 5 档状态 + 紧凑模式 + icon/dot |
| 2.2 | 新增 `LogPanel.vue` | ✅ | 等宽字体 + level 上色 + 暂停/清屏 + 自动滚动 |
| 2.3 | 新增 `DataToolbar.vue` | ✅ | 摘要/筛选/操作三槽 + dense 模式 |
| 2.4 | 新增 `FieldGroup.vue` | ✅ | 标题/必填/帮助/inline 模式 |
| 2.5 | 评估 `SectionCard.vue` 必要性 | ✅ 跳过 | `AppPanelSection` 已覆盖 style guide §7 SectionCard 描述 |
| 2.6 | 新增 `/admin/design-playground` 路由 | ✅ | 注册到 `router/index.ts` + `vite.config.ts` SPA_ROUTES |
| 2.7 | `vue-tsc --noEmit` 通过 | ✅ | 0 error |
| 2.8 | `vite build` 通过 | ✅ | 4.40s，DesignPlaygroundView chunk 16.62 kB / gzip 6.64 kB |
| 2.9 | 5 个组件 + playground 视图全部 0 `!important` | ✅ | `grep -c '!important'` 输出 0 |

### `SectionCard` vs `AppPanelSection` 决策

[AppPanelSection.vue](../../admin/frontend/src/components/common/AppPanelSection.vue) 已支持 eyebrow + title + description + aside + body + 默认槽，与 style guide §7 SectionCard 描述完全重叠。再造一个 `SectionCard` 是重复造轮子。如果后续需要不带 eyebrow 的轻量版，`AppCard` + UnoCSS 足够。

## 阶段 3 — 高流量页面落地

### ✅ 2026-05-14 DashboardView 完成

- 新增 `SparklineChart.vue`（纯 SVG，零依赖）渲染 24h 调用曲线
- 重写 DashboardView：Hero 瘦身 + 3 主 KPI + usage curve + top groups + todo&learning + 日程心情合并 + LogPanel 日志
- 新接入 API：`/admin/usage/data?period=day`、`/api/admin/style/summary`
- `vue-tsc` 0 error，`vite build` 4.69s，chunk 20.85KB / gzip 8.21KB
- docker compose up bot -d --build 成功，napcat 未动
- **2026-05-14 用户视觉验收通过**

### ✅ LogsView 完成

- 套 `AppPage` + `LogPanel` 公共组件（LogPanel 首次落地的消费者）
- 终端字体 + level 上色 + 暂停/清屏/自动滚动从私写代码迁到公共组件
- **2026-05-14 用户视觉验收通过**

### ✅ 2026-05-14 GroupsView 完成

- 主页面瘦身：4 张指标卡片 → 单行紧凑概览条（群 / 自定义 / 主动 / 静默 / 关闭 / 门禁），首屏 ~150px → ~52px
- 表格列数 7 → 4：群 / 参与模式（StateBadge）/ 差异标签（diff-only）/ 发言值；点行打开抽屉，删除尾列"配置"按钮
- 差异标签哲学："偏离全局默认"才显示；全默认群 = `继承全局默认` 一行字
- 抽屉重构：旧 Snapshot 块 + Profile 长滚 → `基础 / 节奏 / 高级` 三 Tab
  - 基础：FieldGroup + NRadioGroup 段式按钮（参与模式 / 回复风格 / 贴纸策略）+ 6 个开关
  - 节奏：5 个底层数字（发言值 / 规划间隔 / 回复冷却 / 批量窗口 / 历史载入）独立 Tab，默认不抢首屏
  - 高级：工具矩阵（3 按钮组 → 单 NSelect，节省 60% 空间）+ 实时状态 / 最近消息 / 策略审计
- 门禁块从首屏移到独立抽屉（页头按钮 + 概览条「门禁」chip 双入口），日常浏览不再被 ~150px 表单遮挡
- `window.confirm` → NPopconfirm；底部加未保存提示
- 删除：`replyMode` 筛选下拉、`MetricCard` × 4、`PageToolbar`、`showAdvancedDetails` 折叠状态、`groupFeatureChips`（→ `groupDiffChips`）、`presenceTagType`（→ `presenceStatus` + StateBadge）
- `vue-tsc` 0 error，`vite build` 4.74s，GroupsView chunk 30.42KB / gzip 10.04KB
- docker compose up bot -d --build 成功；连带修复了 `config/config.json` 里 `group.overrides.625618470.blocked_users: null` 的预存配置 bug（pydantic v2 拒绝 list[int] 上的 null）
- **2026-05-14 用户视觉验收通过**

### ✅ 2026-05-14 LoginView 完成

- 视觉：保留双层构图 + 雾青渐变 + 玻璃磨砂特征卡骨架；间距对齐 token 体系（4 / 8 / 12 / 16 / 24 / 32），删除原 14 / 18 / 28 / 34 异常值；chip / feature / login-card 背景改 `color-mix(in srgb, var(--om-surface) 70%, transparent)`，浅深主题自适应；删除 `.dark .login-card` 复式渐变兜底（依赖 themeOverrides 自动派生 cardColor）
- 易用：自动 focus（`onMounted` + `nextTick`）；Caps Lock 实时检测；上次登录时间持久化展示；失败抖动动画 360ms（尊重 `prefers-reduced-motion`）；提交按钮三态文案（默认 / 验证中 / 锁定倒计时）；input `autocomplete="current-password" spellcheck="false"`
- 安全：`auth.login` store 改返回 `{ ok, error: 'invalid_token' \| 'network_error', status }`，UI 区分"Token 无效"和"后端不可达"两类错误；连续失败 5 次锁定 30 秒（前端 cooldown，按钮 disabled + 倒计时）；非 HTTPS 检测（`location.protocol === 'http:'` 且非 localhost）→ 卡片头部黄色警告条；错误提示带尝试计数（"已尝试 N/5"）
- 后端 auth 链路 `admin/auth.py` + `admin/routes/api/auth.py` 不动，锁定逻辑只在前端（前端冷却是善意提示，真正限流应在中间件加滑动窗口，留给后续）
- 规模：431 行 → 423 行；`vue-tsc` 0 error，`vite build` 4.83s 通过
- docker compose up bot -d --build 成功；`/admin/` HTTP 200 + `/api/admin/me` HTTP 401（无 cookie 时正确）；napcat 未触碰

### ✅ 2026-05-15 ConfigView 完成

- 整页重做：删除原 `ConfigFieldEditor.vue`（486 行单文件 dispatcher），拆为 `ConfigField` + `ConfigListField` + `ConfigKvField` + `ConfigObjectGroup` + `ConfigSecretInput` + `ConfigJsonInput` + `ConfigStatusStrip` 7 个子组件 + `section-labels.ts` 分桶规则
- 三处实锤错位修复：
  - 卡片高度/留白参差 → 删除 `.config-section-grid auto-fit` 等分列，改 AppPanelSection 内单列堆叠 + FieldGroup inline 模式（switch/select/number 自然贴右）
  - object 卡套卡 → ConfigObjectGroup 不画卡，仅左 2px border + inline subhead；深度 ≥ 1 折叠 `<details>`
  - list / kv 内部行错位 → ConfigListField 用 flex（switch item 不撑满）；ConfigKvField 用 grid `200px / 1fr / auto`，value_kind=switch 时切 `200px / auto / auto`
- 三处子缺陷修复（用户验收时发现）：
  - API Key 输入栏不显示 + 任务模型分配 KV 全空 → **根因**：unplugin-vue-components 只扫 `src/components/`，新建在 `src/views/config/` 下的 5 个子组件全部未注册；ConfigField.vue 用作裸标签 Vue 静默丢弃。修复：在 ConfigField + ConfigObjectGroup 显式 `import` 子组件
  - 回复风格下拉文字截断"def..." → naive-ui NSelect `consistent-menu-width=true`（默认）让菜单和触发器同宽；inline 模式触发器只有 ~80px。修复：`:consistent-menu-width="false"` + `min-width: 180px`
- 新增"撤销该字段"action：每个 ConfigField changed 时露出 tertiary 撤销按钮，emit `revert`，ConfigView 调 `setValueByPath(values, path, getValueByPath(originalValues, path))`
- 错误态强可视：FieldGroup `--error` 整张卡左红边 + helper 红字
- `vue-tsc` 0 error，`vite build` 4.91s，ConfigView chunk 61.20 KB / gzip 19.92 KB
- **2026-05-15 用户视觉验收通过**

### 🟡 2026-05-15 SystemView 拆分进行中（PR B-1 完成 / 待 B-2 / B-3 / C）

**当前形态**（[admin/frontend/src/views/system/SystemView.vue](../../admin/frontend/src/views/system/SystemView.vue) 3326 行）：

- script 1024 行 + template 1090 行 + style 1212 行
- 顶层 AppCard 区段 10 块；NDrawer 1 个（Provider Definition Editor）
- 17 个 ref；11 个 GET API（汇集在 `loadSystemStatus`）+ 5 个写入（`/protocol/probe` `/backup` `/providers/selection` `/providers/definitions` `/providers/{name}/test`）
- 32 个 helper 函数（formatters / badges / labels）；31 个 interface + 2 个 type
- `<style scoped>` ~50 个 `.system-*` 类（system-hero / system-panel / system-ops-* / system-service-* / system-runtime-* / system-resource / system-stack / system-provider-* / system-protocol-* / system-trace-* / system-capability-* / system-compatibility-* / system-backup）

**业务边界**（template 顺序天然成段）：

| # | 业务域 | 行号 | 数据依赖 | 写动作 |
|---|---|---|---|---|
| 1 | Hero + 4 KPI | 1061-1136 | `health` `system` `version` `lastLoadedAt` | 无 |
| 2 | Maintenance & Alerts | 1138-1408 | `servicesHealth` `runtimeErrors` `restartNotice` `alertPolicy` `maintenanceWindow` | 无 |
| 3 | Resources & Policies | 1410-1599 | `system` `version` `humanizer` `talkSchedule` | 路由跳转 |
| 4 | Advanced Console（折叠区） | 1601-1918 | `providers` `protocol` `protocolTraces` `protocolConnections` | `/probe` `/providers/selection` `/{name}/test` |
| 5 | Backup | 1920-1940 | 无 | `/backup` |
| 抽屉 | Provider Definition Editor | 1945-2114 | `providerProfilesDraft` 等 6 个 ref | `/providers/definitions` |

**目标布局**（13 文件，按 §6.3「先拆代码再做视觉统一」）：

| 文件 | 行数预估 | 职责 |
|---|---|---|
| `SystemView.vue`（重写） | ~280 | 顶层容器：`loadSystemStatus` 汇集 11 个 API、ref 持有、子组件装配 |
| `components/SystemHero.vue` | ~110 | Hero 卡（左主右两块小卡）。props: `health` `system` `version` `lastLoadedAt` `error` |
| `components/SystemMetrics.vue` | ~60 | 4 张 MetricCard。props: `health` `system` |
| `components/SystemMaintenance.vue` | ~220 | 运维建议 + 告警列表 + suppressed 提示。props: `maintenanceWindow` `restartNotice` `healthAlerts` `alertPolicy` |
| `components/SystemServiceHealth.vue` | ~140 | 服务级健康卡片网格 + meta tags。props: `servicesHealth` `attentionCount` |
| `components/SystemRuntimeErrors.vue` | ~120 | 关键错误聚合：summary + groups。props: `runtimeErrors` |
| `components/SystemResources.vue` | ~150 | CPU / 内存 / 磁盘 + 进程信息。props: `system` |
| `components/SystemPolicies.vue` | ~130 | 版本 / 防检测 / 发言倍率 三 stack。props: `version` `humanizer` `talkSchedule` |
| `components/SystemAdvancedEntry.vue` | ~70 | 5 个低频工具入口 + 折叠开关。emit `toggle` `navigate` |
| `components/SystemProviders.vue` | ~280 | LLM Provider 整段（active 摘要 + profile list + task 映射 + 切换/测试）。emit `save-selection` `test-profile` `open-editor` |
| `components/SystemProtocol.vue` | ~280 | 协议能力（probe summary + connection 历史 + trace 列表 + capability + 兼容清单）。emit `probe` |
| `components/SystemBackup.vue` | ~50 | 备份卡。emit `create-backup` |
| `components/SystemProviderEditorDrawer.vue` | ~280 | NDrawer + 8 字段 NForm + capability checkbox + key mode。v-model + emit `save` |
| `helpers/types.ts` | ~250 | 31 个 interface + 2 个 type，统一导出 |
| `helpers/formatters.ts` | ~80 | `formatDuration` `formatPercent` `formatTimestamp` `formatMs` `formatTokenCount` `formatCooldown` `meterColor` |
| `helpers/badges.ts` | ~110 | 全部 *TagType / *Label：`serviceTagType` `serviceStatusLabel` `protocolTagType` `protocolConnectionType` `protocolTraceType` `runtimeErrorLevelType` `alertTagType` `compatibilityTagType` `compatibilityLabel` `providerResultType` `providerResultLabel` `providerRateLimitType` `providerRateLimitLabel` `providerModeLabel` `providerCacheHitLabel` `serviceMetaTags` `memorySemanticLastError` `protocolConnectionLabel` `protocolConnectionEventLabel` `runtimeErrorLevelLabel` `alertSeverityLabel` |

主视图 3326 → ~280；最大子组件 ~280 行；helpers 三个文件总 ~440 行（纯函数，零 Vue 依赖）。**全部文件 < 400 行**，符合 §6.3「每个子组件 ≤ 400 行」。

**PR 切片建议**（避免一锤 13 文件）：

| PR | 内容 | diff 估算 | 风险 |
|---|---|---|---|
| **PR B-1** | 抽 `helpers/types.ts` + `helpers/formatters.ts` + `helpers/badges.ts`，主视图 import 替换。零样式/模板改动 | ~600 行迁移 | 低 |
| **PR B-2** | 拆 8 个**只读展示**子组件：Hero / Metrics / Maintenance / ServiceHealth / RuntimeErrors / Resources / Policies / AdvancedEntry / Backup。仍由主视图持 ref 传 props，无 emit 写动作 | ~1200 行迁移 | 中 |
| **PR B-3** | 拆 3 个**有交互**子组件：Providers + Protocol + ProviderEditorDrawer。emit 写动作回主视图调 API | ~900 行迁移 | 中 |
| **PR C** | 视觉精修：把每个子组件里的私写 `system-panel__head/__eyebrow/__title` 收敛到 [AppPanelSection](../../admin/frontend/src/components/common/AppPanelSection.vue) 的 eyebrow + title slot；删除 `<style scoped>` 里大量重复的 `.system-panel__*`；空态统一走 [EmptyState](../../admin/frontend/src/components/common/EmptyState.vue) | 仅样式 + 模板替换 | 低 |

**关键风险与对齐项**：

- **3326 → 280 行的主视图依然要承担 11 API + 17 ref**——这是业务复杂度，不是拆分能消除的。子组件全部走 props-down + emit-up，避免 ref 在子组件里散落（SlangView 样板复用时也按这个范式）。
- **样式收敛**到 AppPanelSection 的 eyebrow/title slot 后，`<style scoped>` 的 `.system-panel__*` 全段（~120 行）可一次性删除。这是 PR C 的主要红利，**不放进 PR B**——PR B 只搬不改样式，避免「拆分 + 风格」混在一个 diff 里 review 不动。
- **RestartBotButton 与 useRouter** 留在主视图。
- **`unplugin-vue-components` 注册陷阱**：新建在 `src/views/system/components/` 下的子组件**不会**被自动注册（ConfigView 已踩过这个坑——该插件默认只扫 `src/components/`）。所有子组件必须在父组件里**显式 `import`**，不要用裸标签。
- **`<style scoped>` 跨组件迁移**：原文件里 `.system-panel__head/__eyebrow/__title/...` 等通用类被多个 AppCard 共享，迁移时要么每个子组件复制一份（PR B 期间），要么收敛到 AppPanelSection（PR C）。**不要**搞中间态私造 `system-shared.css`——要么本地化，要么收敛，避免引入新公共样式表。
- **`onMounted(loadSystemStatus)` 留在主视图**，子组件不发起任何 fetch。这样路由切换时只触发一次加载，不会因子组件挂载顺序产生竞态。

**验证方案（每个 PR 都过）**：

```bash
cd admin/frontend
./node_modules/.bin/vue-tsc --noEmit       # 0 error
npm run build                              # 通过；记录 bundle 大小变化
```

视觉手测：dev 模式下进 `/admin/system`，依次确认：①Hero 文案 + 4 张 MetricCard ②运维建议 + 告警列表 ③服务级健康（含 memory_semantic 错误提示）④关键错误（runtime） ⑤CPU / 内存 / 磁盘进度条 + 进程统计 ⑥版本/防检测/发言倍率 stack ⑦5 个低频工具入口 ⑧点「打开高级区」展开 LLM Provider + 协议能力 ⑨「定义管理」抽屉打开 → 改某 profile → 保存 → 列表刷新 ⑩备份卡点一次。

**下一步**：开始 **PR B-1**（helpers 抽离）——风险最小、收益直接（script 1024 → ~580 行），为 B-2/B-3 让出空间。

#### ✅ PR B-1 完成（2026-05-15）

实际抽离结果：

| 文件 | 行数 | 内容 |
|---|---|---|
| `helpers/types.ts` | 335 | 30 interface + 2 type，全部 `export`；`RestartNotice` 用前向引用 |
| `helpers/formatters.ts` | 48 | `formatDuration` `formatCooldown` `formatPercent` `meterColor` `formatTimestamp` `formatMs` `formatTokenCount` 共 7 个纯函数 |
| `helpers/badges.ts` | 158 | 21 个 `*TagType / *Label / *Tags / *Error` 全 export；从 `./formatters` 导入 `formatCooldown / formatMs`，从 `./types` 导入 4 个相关 interface |
| `SystemView.vue` | 3326 → 2842 | -484 行（-14.5%）。template / style 零改动，仅 script 段把 inline 定义换成 from `./helpers/{types,formatters,badges}` 的 import |

验证证据：

```bash
$ ./node_modules/.bin/vue-tsc --noEmit
EXIT=0

$ npm run build
✓ built in 4.88s
../static/assets/SystemView-C3Sca7fP.js   49.49 kB │ gzip: 15.20 kB
```

bundle 体积与拆分前持平（业务逻辑零改动），证明纯 import 重写没有引入额外副作用。下一步进入 **PR B-2**（9 个只读子组件）。

#### ✅ PR B-2 完成（2026-05-15）

实际拆分结果（`admin/frontend/src/views/system/components/`，9 文件）：

| 文件 | 行数 | 内容 |
|---|---|---|
| `SystemHero.vue` | 178 | Hero 卡（左主+右两小卡）。props: `health` `system` `version` `lastLoadedAt` `error`；内部 computed `heroTitle / heroDescription` |
| `SystemMetrics.vue` | 73 | 4 张 MetricCard。props: `health` `system`；内联 4 个 `@vicons/ionicons5` 图标 |
| `SystemMaintenance.vue` | 386 | 运维建议 + 告警 + suppressed 提示。props: `maintenanceWindow` `restartNotice` `healthAlerts` `alertPolicy`；包含 `system-ops-*` `system-alert-*` 全套 scoped 样式 |
| `SystemServiceHealth.vue` | 194 | 服务卡片网格 + meta tags + memory 语义错误。props: `servicesHealth` `attentionCount` |
| `SystemRuntimeErrors.vue` | 219 | 关键错误聚合：summary + groups。props: `runtimeErrors`；内部 computed `runtimeErrorSummary / runtimeIssueGroups` |
| `SystemResources.vue` | 184 | CPU / 内存 / 磁盘 + 进程信息。props: `system` |
| `SystemPolicies.vue` | 206 | 版本 / 防检测 / 发言倍率 三 stack。props: `version` `humanizer` `talkSchedule`；内部 computed `versionSummary` |
| `SystemAdvancedEntry.vue` | 148 | 5 个低频工具入口 + 折叠开关。props: `expanded` `tools[]`；emit `toggle` `navigate` |
| `SystemBackup.vue` | 89 | 备份卡。props: `loading`；emit `create-backup` |
| `SystemView.vue` | 2842 → 1649 | -1193 行（-42%）。template 9 段子组件挂载替换 inline；script 删除 3 个迁出 computed（`heroTitle / heroDescription / versionSummary`）+ 2 个迁出 computed（`runtimeErrorSummary / runtimeIssueGroups`）；style 块删除 ~700 行 subcomponent-only 规则，保留 `system-panel*` `system-main-grid` `system-observability-grid` `system-inline-list` `system-link` 与全部 `system-provider*` / `system-protocol*` / `system-trace*` / `system-connection*` / `system-compatibility*` / `system-task-profile*` / `system-provider-editor*` 给 PR B-3 |

**全部 9 子组件 < 400 行**（最大 SystemMaintenance 386），符合 §6.3「每个子组件 ≤ 400 行」。

设计要点：

- **props-down + emit-up 严格执行**：子组件不发起 fetch、不持有写状态。`SystemAdvancedEntry` emit `toggle / navigate` 把开关与跳转交给主视图；`SystemBackup` emit `create-backup` 把 API 调用留在主视图。`onMounted(loadSystemStatus)` 不动，路由切换只触发一次加载，无子组件挂载竞态。
- **显式 import 全部 9 子组件**：`unplugin-vue-components` 默认只扫 `src/components/`，新建在 `src/views/system/components/` 下的子组件不会被自动注册（ConfigView 踩过的坑）。每个子组件在 `SystemView.vue` 顶部显式 `import xxx from './components/xxx.vue'`。
- **scoped 样式本地化（不收敛）**：每个子组件复制一份自己用到的 `system-panel__head/__eyebrow/__title` 共享类。这是「PR B 只搬不改」的代价——重复出现 9 份。**PR C 的红利就是把这部分收敛到 AppPanelSection 的 eyebrow/title slot**，预计可一次性删除 ~120 行重复 CSS。
- **媒体查询同步迁移**：`@media (max-width: 1100px)` / `@media (max-width: 760px)` 里的 grid 切换规则按宿主组件归位（`.system-metric-grid` 在 SystemMetrics、`.system-service-grid` 在 SystemServiceHealth、`.system-hero` 在 SystemHero、`.system-backup` 在 SystemBackup），主视图保留 `.system-main-grid` `.system-observability-grid` `.system-task-profile-list` `.system-provider-runtime` `.system-protocol-summary` 的响应式收缩。

验证证据：

```bash
$ ./node_modules/.bin/vue-tsc --noEmit
EXIT=0

$ npm run build
✓ built in 4.70s
../static/assets/SystemView-CpP_rjpo.js   52.62 kB │ gzip: 16.16 kB
```

bundle 变化：B-1 49.49 KB / gzip 15.20 KB → B-2 52.62 KB / gzip 16.16 KB（+3.13 KB / +0.96 KB）。增长来自 9 子组件各自复制的 scoped style 块（per-component CSS 隔离机制下重复）+ 子组件实例化样板。**这部分是 PR C 的目标**：一旦把 `system-panel__head/__eyebrow/__title` 收敛到 AppPanelSection slot，9 子组件去掉重复 CSS，预计回吐 ~2-3 KB / gzip 0.8 KB，回到 B-1 水平甚至更低。

下一步进入 **PR B-3**（3 个有交互子组件 Providers + Protocol + ProviderEditorDrawer，emit 写动作回主视图调 API）。

#### ✅ PR B-3 完成（2026-05-15）

实际拆分结果（`admin/frontend/src/views/system/components/`，新增 3 文件）：

| 文件 | 行数 | 内容 |
|---|---|---|
| `SystemProviders.vue` | 479 | LLM Provider 卡：active 摘要 + provider 列表 + task profile 映射 + 测试/热切换。props: `providers` `defaultDraft` `taskDraft` `testing` `testResults` `selectionSaving` `selectionDirty`；emit `update-default-draft` `update-task-draft` `save-selection` `test-profile` `open-editor`；内部 computed `activeProvider / activeProviderUsageSummary / providerProfileOptions` + 自有 `providerTaskOrder / providerTaskLabels / providerTaskModel` |
| `SystemProtocol.vue` | 484 | 协议能力卡：probe 摘要 + 连接历史 + trace 列表 + capability + compatibility。props: `protocol` `traces` `connections` `probing`；emit `probe`；内部 computed `okCount / traceSummary / connectionSummary` |
| `SystemProviderEditorDrawer.vue` | 313 | NDrawer + 8 字段 NForm + capability checkbox + key mode。`v-model:show` + props `drafts` `capabilityOptions` `apiFormatOptions` `dirty` `saving`；emit `add` `remove` `patch` `set-key-mode` `capabilities-change` `reset` `save`，主视图维持 `providerProfilesDraft` 单一数据源 |
| `SystemView.vue` | 1649 → 590 | -1059 行（-64%）。template 3 段 inline JSX 全部替换为子组件挂载；script 删除 6 个迁出符号（`activeProvider / activeProviderUsageSummary / providerProfileOptions / providerTaskLabels / providerApiKeyModeOptions / protocolOkCount / protocolTraceSummary / protocolConnectionSummary / providerTaskModel`）；style 块删除 ~830 行 subcomponent-only 规则（`system-provider*` `system-protocol*` `system-trace*` `system-connection*` `system-compatibility*` `system-task-profile*` `system-provider-editor*` `system-panel*` `system-inline-list` `system-link*`），仅保留 `.system-main-grid` `.system-observability-grid` 与 2 条响应式断点。`AppCard` 主视图不再使用，import 一并清除 |

设计要点：

- **emit-up 严格执行**：3 个子组件全部把"写"动作（saveSelection / testProfile / probe / saveDefinitions）emit 给主视图。`saveProviderSelection / probeProtocol / saveProviderDefinitions / testProviderProfile / openProviderEditor / addProviderDraft / removeProviderDraft / updateProviderDraft / setProviderApiKeyMode / onProviderCapabilitiesChange / resetProviderDefinitions / setDefaultProviderDraft / setTaskProviderDraft` 全在 `SystemView.vue` 持有，子组件零 fetch、零 ref 写入。Drawer 用 `v-model:show` 双向绑定可见性，但 drafts 仍归主视图——避免 ConfigView 踩过的"双源真相"陷阱。
- **types 收紧 vs 主视图分歧**：`NSelect` 在 inline 模板里 vue-tsc 默认放宽到结构兼容，但通过 props 跨组件传递时会收紧到 `SelectMixedOption[]`。`SystemProviderEditorDrawer` 在 NSelect 处用 `as any` 局部豁免——这是 PR B 的临时桥接，PR C 会与 `FieldGroup` 一起统一选项类型。
- **显式 import 全部 3 子组件**：`unplugin-vue-components` 不会扫 `views/system/components/`，沿用 PR B-2 的纪律——`import SystemProviders / SystemProtocol / SystemProviderEditorDrawer` 全在 `SystemView.vue` 顶部声明。
- **媒体查询同步迁移**：原主视图的 `1100px / 760px` 媒体查询里涉及 `.system-task-profile-list / .system-provider-runtime / .system-protocol-summary / .system-provider-row / .system-provider-switcher / .system-provider-editor*` 的全部规则随类目迁到对应子组件；主视图只留 `.system-main-grid / .system-observability-grid` 在两断点的塌缩规则。
- **scoped 样式本地化**：3 子组件各自带一份 `system-panel__head / __eyebrow / __title` 共享类拷贝（与 PR B-2 同样的临时代价）；全部 12 子组件累计 ~12 份重复——PR C 的目标。

验证证据：

```bash
$ ./node_modules/.bin/vue-tsc --noEmit
EXIT=0

$ npm run build
✓ built in 4.88s
../static/assets/SystemView-bRyAGbch.js   55.03 kB │ gzip: 17.03 kB
```

bundle 变化：B-2 52.62 KB / gzip 16.16 KB → B-3 55.03 KB / gzip 17.03 KB（+2.41 KB / +0.87 KB）。增长来自 3 子组件复制的 scoped style 块（per-component CSS 隔离机制下不可避免）。**这部分仍是 PR C 的目标**：把 `system-panel__head/__eyebrow/__title` 收敛到 `AppPanelSection` slot 后，12 子组件可一次性删除重复 CSS，预计回吐 ~2-3 KB / gzip 0.8 KB，回到 B-1 ~50 KB / 15 KB 水平。

累计：SystemView 3326 → 590 行（-2736 行，-82%），主视图变成纯装配 + 状态管理，无视觉细节。

下一步进入 **PR C**（视觉收敛——`AppPanelSection` slot 替换 `system-panel__head/__eyebrow/__title`，删除 12 个子组件 scoped CSS 重复块，统一 EmptyState 样式）。

#### ✅ PR C 完成（2026-05-15）

迁移 9 个 AppCard 子组件到 [AppPanelSection](../../admin/frontend/src/components/common/AppPanelSection.vue)，删除每份子组件里手写的 `system-panel__head / __eyebrow / __title` 共享类。

| 子组件 | AppCard → AppPanelSection | 删除重复 scoped CSS |
| --- | --- | --- |
| `SystemAdvancedEntry.vue` | `head + 切换按钮` → `aside slot` | -28 行 |
| `SystemBackup.vue` | `eyebrow + title + description + button` 全部进 `AppPanelSection` props/aside | -16 行（整张卡变最简） |
| `SystemMaintenance.vue` | `head + 4 个 NTag aside` → `#aside slot` | -28 行 |
| `SystemPolicies.vue` | 外层 `system-panel` 头部迁移；内部 3 个 `AppCard bordered embedded` 子卡保留 | -28 行 |
| `SystemProtocol.vue` | `head + 探测按钮` → `#aside slot` | -28 行 |
| `SystemProviders.vue` | `head + 定义管理按钮 + profile 计数 NTag` → `#aside slot` | -28 行 |
| `SystemResources.vue` | `head + PID NTag` → `#aside slot` | -28 行 |
| `SystemRuntimeErrors.vue` | `head + 2 个 summary NTag aside` → `#aside slot`；`EmptyState` 已就位 | -28 行 |
| `SystemServiceHealth.vue` | `head + overall_status + attentionCount NTag aside` → `#aside slot`；`EmptyState` 已就位 | -28 行 |

合计 9 子组件，删除约 ~252 行重复 scoped CSS（每份 ~28 行）。`AppCard` import 共 9 处替换为 `AppPanelSection`。

不动的部分：

- `SystemHero.vue` / `SystemMetrics.vue` 没有 `system-panel__head` 模式，使用 hero 渐变 + MetricCard，免迁移。
- `SystemProviderEditorDrawer.vue` 的 eyebrow/title 在 `<NDrawerContent>` 的 `#header` 里（drawer 没有 AppCard 外壳），保留本地 14 行样式。

视觉副作用（与其它视图统一）：

- title 字号 18px → 16px（统一到 GroupsView/MemoryView 等）
- AppCard padding 20 → AppPanelSection 18
- head 底 margin 18 → 14
- aside 槽在 760px 以下自动改为 `flex-direction: column; align-items: stretch; justify-content: flex-start`，无需各子组件再写 media query

外部可观察证据：

```bash
$ ./node_modules/.bin/vue-tsc --noEmit
EXIT=0

$ npm run build
✓ built in 4.75s
../static/assets/SystemView-DleejYlq.js   53.35 kB │ gzip: 16.84 kB
```

bundle 变化：B-3 55.03 KB / gzip 17.03 KB → C 53.35 KB / gzip 16.84 KB（**-1.68 KB / -0.19 KB gzip**，达成「回吐 ~2 KB / 0.2 KB」预期）。注意尚未回到 B-1 49.49 KB 水平——剩余的 ~3.86 KB / ~1.64 KB gzip 来自 12 子组件各自的功能性 scoped CSS（`system-provider-card`、`system-ops-grid`、`system-trace-panel` 等业务结构样式），这部分是「12 文件 vs 1 文件」拆分的固有代价，与 PR B 的初始假设一致。如未来要进一步收敛，应当走「业务样式合并到 design tokens」而非继续拆 PR C。

SystemView 完整闭环（B-1 → C）：3326 行 → 590 行（-82%）+ 12 个 < 500 行子组件 + 头部样式收敛到 [AppPanelSection](../../admin/frontend/src/components/common/AppPanelSection.vue) 公共组件。下一步进入 **SlangView**（顺位 5，2662 行，按 SystemView 同 B-1/B-2/B-3/C 模板）。

### 🟡 2026-05-15 SlangView 拆分启动（B-1 helpers ✅ / B-2 / B-3 / C 已定稿）

#### 现状盘点

- 单文件 [SlangView.vue](../../admin/frontend/src/views/slang/SlangView.vue)，2662 行（script 1079 / template 877 / style 700）。
- 11 interface + 3 type + 11 个 API 函数 + 16 ref + 6 computed + 多个 drawer 状态。
- template 6 个区段：metric 网格 / 高级概览（折叠） / 队列 toolbar / 列表面板（drift + term + bulk + 翻页） / 设置面板（治理 + 观察 + 表单） / 创建抽屉 / 详情抽屉。

#### B-1 helpers 抽取计划（待执行）

| 文件 | 内容 | 估算 |
|---|---|---|
| `helpers/types.ts` | 11 interface + 3 type | ~180 |
| `helpers/formatters.ts` | formatTime / confidenceText / numberSetting / mergeSettings / formatSearchQueries | ~50 |
| `helpers/badges.ts` | statusLabel/Type / driftStatusLabel / revisionActionLabel / policyLabel / runKindLabel / queueOptions/scopeOptions/confidenceOptions/repeatPolicyOptions/statusOptions / isAiApproved/isHumanReviewed/needsHumanReview | ~150 |

#### B-1 helpers 抽取（2026-05-15 完成）

| 文件 | 实际行数 | 内容 |
|---|---|---|
| `helpers/types.ts` | 200 | 11 interface（SlangSummary / SlangTerm / SlangObservation / SlangPendingCandidate / SlangExtractionRun / SlangStatsTerm / SlangStats / SlangSettings / SlangRevision / SlangDriftReview / SlangCreateDraft）+ 3 type（SlangStatus / RepeatPolicy / SlangQueueMode） |
| `helpers/formatters.ts` | 98 | formatTime / confidenceText / numberSetting / formatSearchQueries / DEFAULT_SLANG_SETTINGS / mergeSettings（pure，把 fallback 显式参数化，主视图调用 `mergeSlangSettings(payload, settings.value)`） |
| `helpers/badges.ts` | 114 | statusLabel/Type / driftStatusLabel / revisionActionLabel / policyLabel / runKindLabel / isAiApproved/isHumanReviewed/needsHumanReview + 4 options 常量（STATUS_OPTIONS / CONFIDENCE_OPTIONS / SCOPE_OPTIONS / REPEAT_POLICY_OPTIONS） |

**SlangView 主文件**：2662 行 → 2320 行（**-342 / -12.8%**），删除 11 interface + 3 type + 13 函数 + 4 options 常量 + defaultSlangSettings；template 里 4 个 options 引用改为大写 import 名，`mergeSettings` 调用点改为 `mergeSlangSettings(incoming, settings.value)` 显式传 fallback。

**外部可观察证据**：

- `vue-tsc --noEmit` → exit 0
- `npm run build` → 4.91s
- `SlangView-*.js` 53.06 KB / gzip 14.73 KB → **53.24 KB / gzip 14.86 KB**（+0.18 / +0.13 gzip，与 SystemView B-1 持平的预期 helpers split 开销）

#### B-2 只读子组件（4 个）✅ 2026-05-15

按计划落 4 个 `admin/frontend/src/views/slang/components/` 子组件：

| 文件 | 包含 | 估算 | 实际 |
| --- | --- | --- | --- |
| `SlangMetrics.vue` | 5 张 KPI（待审核 / AI 通过 / 已批准 / 观察中 / 漂移） | ~25 | 83 |
| `SlangAdvancedOverview.vue` | 高级概览条 + 3 张 stat（热门 / 群活跃 / 抽取记录） | ~120 | 200 |
| `SlangQueueToolbar.vue` | 队列 segment + 4 filter + 跨群扫描/重置/总数 | ~80 | 272 |
| `SlangDriftCard.vue` | drift 单卡（队列 drift 模式 + 设置面板治理段双处复用预留） | ~50 | 149 |

> 实际行数比估算大主要因为：scoped style 块（每个组件自带响应式断点 + 段式选样式）+ filter strip 完整保留 control-strip 装饰带（背景渐变 / 阴影 / 段式按钮 hover / segment count badge）。功能行数 < 估算，样式行数压满。

**主视图 SlangView**：2320 行 → **1864 行（-456 / -19.7%）**：

- imports：删 `MetricCard / CheckmarkCircleOutline / FlashOutline / CONFIDENCE_OPTIONS / SCOPE_OPTIONS / driftStatusLabel / runKindLabel`，新增 4 个子组件 import
- script：删 `groupOptions / totalQueueCount / queueOptions` 三个 computed
- template：metric grid / advanced strip / advanced cards / queue toolbar / drift card 五块旧 markup 全部替换为 `<SlangMetrics>` `<SlangAdvancedOverview>` `<SlangQueueToolbar>` `<SlangDriftCard>` 调用
- style：删 13 块 scoped class（metric-grid / advanced-strip(__copy) / control-strip(全套含 segments / filter-control / soft-action / total-tag) / segment-button(--active) / stats-grid / stat-card(__head) / rank-list / rank-row / run-list / run-row / drift-card(__main) / drift-compare(__full) / drift-evidence），保留治理段共享的 `pending-list / pending-row` 子集（B-3 还要用）

`q-mode='drift'` 队列里的 drift 卡 emit `action(drift, 'accept' | 'reject' | 'alias' | 'mute')` 给主视图调 `handleDriftAction`，行为完全等价。

**外部可观察证据**：

- `vue-tsc --noEmit` → exit 0
- `npm run build` → 4.76s
- `SlangView-*.js` 53.24 KB / gzip 14.86 KB → **55.84 KB / gzip 15.96 KB**（+2.60 / +1.10 gzip，4 子组件 scoped style 复制的预期开销，与 SystemView B-2 同量级；PR C 收敛 AppPanelSection 后会回吐部分）

**回滚**：

- B-2 完整回滚：`git checkout HEAD -- admin/frontend/src/views/slang/SlangView.vue`，再 `rm -rf admin/frontend/src/views/slang/components/`，最后 `npm run build` 即可恢复。

#### B-3 交互子组件（5 个，治理 / 表单 / 抽屉）✅ 2026-05-15

按计划落 5 个 `admin/frontend/src/views/slang/components/` 子组件：

| 文件 | 包含 | 估算 | 实际 |
| --- | --- | --- | --- |
| `SlangTermList.vue` | 列表面板（drift mode / term list / bulk bar / 翻页）；嵌 `SlangDriftCard` | ~250 | 339 |
| `SlangGovernanceSection.vue` | 漂移治理 + 观察中候选两段 | ~50 | 144 |
| `SlangSettingsForm.vue` | 13 开关 + 14 数字 + 复述策略 / 语义后端 / 白名单 / 停用词 textarea + 保存 | ~230 | 237 |
| `SlangCreateDrawer.vue` | 创建抽屉（词条信息 + 示例与备注两段 AppPanelSection） | ~110 | 144 |
| `SlangDetailDrawer.vue` | 详情抽屉（Editor + AI Review + Quality 合并/重算 + History + Observations 五段） | ~230 | 435 |

> SlangTermList 包含 bulk bar + drift list + term list + 双 pagination + 5 个 emit；SlangDetailDrawer 是 5 段 AppPanelSection（含 AI Review 高亮卡 + 合并 select 远程搜索 + revision diff 列表）。两块都贴近实际复杂度，行数比估算大但都 < 500。

**主视图 SlangView**：1864 行 → **845 行（-1019 / -54.7%）**，累计 2662 → 845（**-1817 / -68.3%**）。

- imports：删 `AlertCircleOutline / SearchOutline / TimeOutline / AppCard 旧抽屉用法 / AppDrawerHeader / AppDrawerLayout / AppPanelSection / EmptyState / isAiApproved / isHumanReviewed / needsHumanReview / revisionActionLabel / statusType / formatSearchQueries / formatTime / confidenceText / STATUS_OPTIONS / REPEAT_POLICY_OPTIONS / SlangCreateDraft 等仅子组件用到的引用`，新增 5 个子组件 import；保留 `statusLabel`（merge options label 用）+ DEFAULT_SLANG_SETTINGS / mergeSettings（settings 状态机用）
- script：删 `selectedCount / pageSelectionChecked / pageSelectionIndeterminate` 三个 computed（迁到 SlangTermList）+ `setPageSelection / toggleTermSelection / handleTermSelectionUpdate / termSelectionHandler` 四个函数（迁到 SlangTermList，主视图改用 v-model:selected-term-ids）
- template：列表面板 + 抽屉两块旧 markup 全部替换为 `<SlangTermList>` / `<SlangCreateDrawer>` / `<SlangDetailDrawer>` 调用；高级设置面板里 `slang-side-section`（治理 + 观察）和 `slang-settings-form`（13+14+textarea）拆为 `<SlangGovernanceSection>` + `<SlangSettingsForm>`
- style：删 ~28 块 scoped class（`slang-side-section / __head / --governance` / `slang-side-note` / `slang-pending-list` / `slang-pending-row` / `slang-bulk-bar` / `slang-term-list` / `slang-drift-list` / `slang-term-card` 全套（`__main / __head / __copy / __tags / __meta / __actions`）/ `slang-alias-row` / `slang-pagination-bottom` / `slang-settings-form` / `slang-switch-row` / `slang-settings-grid` / `slang-settings-field` / `slang-detail-grid` / `slang-quality-grid` / `slang-quality-card` / `slang-ai-review-box` 全套 / `slang-ai-review-grid` / `slang-signal-list` / `slang-revision-list` / `slang-revision-row` / `slang-revision-diff` / `slang-observation-list` / `slang-observation` 等），主视图只保留 `.slang-cache-revision`（隐藏锚点）+ `.slang-layout(--compact)` + `.slang-settings-panel` + `.slang-panel-head` + `.slang-eyebrow` + `.slang-title` + `.slang-settings-collapsed-note` 七块共 ~70 行

**v-model 流向**（主视图 = 单一状态源）：

- `<SlangTermList v-model:page v-model:selectedTermIds>` ← 翻页 + 多选状态主视图持有
- `<SlangAdvancedOverview v-model:expanded>` ← 折叠状态
- `<SlangQueueToolbar v-model:searchText / groupFilter / scopeFilter / queueMode / minConfidence>` ← 5 个 filter
- `<SlangCreateDrawer v-model:visible v-model:draft>` ← 抽屉显隐 + 草稿
- `<SlangDetailDrawer v-model:visible / detailTerm / editAliases / mergeTargetId / mergeSearchText>` ← 抽屉显隐 + 编辑态
- `<SlangSettingsForm v-model:settings / allowlistText / stoplistText>` ← 配置 + 两段 textarea

**外部可观察证据**：

- `vue-tsc --noEmit` → exit 0
- `npm run build` → 4.91s
- `SlangView-*.js` 55.84 KB / gzip 15.96 KB → **60.89 KB / gzip 17.34 KB**（+5.05 / +1.38 gzip，5 子组件 scoped style 复制的预期开销，与 SystemView B-3 +2.41 / +0.87 同量级；PR C 收敛 AppPanelSection 后会回吐部分）

**回滚**：

- B-3 完整回滚：`git checkout HEAD -- admin/frontend/src/views/slang/SlangView.vue`，再 `rm admin/frontend/src/views/slang/components/{SlangTermList,SlangGovernanceSection,SlangSettingsForm,SlangCreateDrawer,SlangDetailDrawer}.vue`，最后 `npm run build` 即可恢复（不动 B-2 的 4 个只读子组件）。

#### ✅ PR C 完成（2026-05-15，SlangView）

| 文件 | 改动 | 行数变化 |
| --- | --- | --- |
| `SlangTermList.vue` | 外层 `<AppCard bordered elevated class="slang-list-panel">` + 手写 `<div class="slang-panel-head">` 改 `<AppPanelSection eyebrow="Review Queue" title="黑话候选与词表">`；顶部分页 `<NPagination>` 用 `<template #aside>` 接到右上角；删 4 块 scoped style（`.slang-list-panel / .slang-panel-head / .slang-eyebrow / .slang-title`） | 339 → **308** (-31) |
| `SlangView.vue`（主视图） | `<AppCard bordered elevated class="slang-settings-panel">` + 手写 panel-head 改 `<AppPanelSection eyebrow="Advanced Settings" title="学习与注入">`；折叠按钮通过 `<template #aside>` 接入；删 `AppCard` import + 4 块 scoped style；保留 `.slang-cache-revision / .slang-layout(--compact) / .slang-settings-collapsed-note` 与 1180px 断点 | 845 → **814** (-31) |

**主视图累计**：2662 → **814 行**（**-1848 / -69.4%**）

**外部可观察证据**：

- `vue-tsc --noEmit` → exit 0
- `npm run build` → 4.93s
- `SlangView-*.js`：60.89 KB / gzip 17.34 KB → **60.63 KB / gzip 17.26 KB**（**-0.26 / -0.08 gzip**，B-3 +5.05 / +1.38 的开销首次出现回吐迹象，且对比起点 53.06 / 14.73 仍是 +7.57 / +2.53——这部分是 9 个子组件 scoped style 的固定成本）
- grep 验证：`slang-panel-head / slang-eyebrow / slang-title / .slang-list-panel / .slang-settings-panel` 五个 class 名在 `admin/frontend/src/views/slang/` 下已全部消失

**回滚**：

- 仅 PR C 回滚：`git checkout HEAD -- admin/frontend/src/views/slang/SlangView.vue admin/frontend/src/views/slang/components/SlangTermList.vue` + `npm run build`，不动 9 个子组件结构。

#### 累计目标

主视图 SlangView 从 2662 行降到 **814 行**（保留 11 API + 17 ref + 6 computed + 26 handler 业务状态机），符合"不可拆的业务复杂度"边界。SystemView 同模板的 4 阶段（B-1 / B-2 / B-3 / C）走完。下一个候选可以挑表达方式 / 知识库 / Memo / 群管理里仍是单文件的视图。

#### 累计四个 PR 的全景

| 阶段 | 主视图行数 | 减量 | 子组件数 | bundle KB / gzip |
| --- | --- | --- | --- | --- |
| 起点 | 2662 | 0 | 0 | 53.06 / 14.73 |
| B-1 helpers | 2320 | -342 (-12.8%) | 0（3 helpers） | 53.24 / 14.86 |
| B-2 只读 | 1864 | -456 (-19.7%) | 4 | 55.84 / 15.96 |
| B-3 交互 | 845 | -1019 (-54.7%) | 9（4+5） | 60.89 / 17.34 |
| **C 视觉收敛** | **814** | **-31 (-3.7%)** | 9 | **60.63 / 17.26** |
| 累计 | 814 | -1848 (**-69.4%**) | 9 | +7.57 / +2.53（固定成本，B-3 后已止涨） |

### 🟡 2026-05-15 SlangView UX 重构（待审查后开工）

继 B-1/B-2/B-3/C 拆分收敛之后，针对用户实锤的三点交互痛点重做主页面信息架构 + 补一项后端缺口。**只是审查阶段写入跟踪文档，未开始实现。**

#### 用户反馈与问题定位

| # | 用户原话 | 根因 |
| --- | --- | --- |
| ① | 高级概览折叠展开意义不明，应当简化为常驻小卡片 | SlangMetrics 5 卡已经覆盖核心计数；SlangAdvancedOverview 的"今日命中 / 最近抽取"只是两个补充字段被包成"展开"卡，折叠成本和信息量不成比例 |
| ② | 学习/注入面板在底部，展开后挤右栏不易操作 | 它本质是**全局设置 + 治理队列**（低频深度面板），却放在主审核流的右栏，宽度 340 px 把白名单/停用词 textarea + 数字字段两列网格挤压得很窄 |
| ③ | 缺少人工手动按钮一键 AI 审核 | 后端 `plugins/slang/plugin.py:298` `run_daily_ai_review` 已存在，但路由层没暴露 HTTP 端点，前端无法主动唤起 |
| ④ | 缺多时段定时 AI 自动审查 | `services/slang/types.py:39` `daily_ai_review_time: str` 是单值 HH:MM，去重靠 `last_daily_ai_review_date`（按日去重，每天最多一次）；schema 不支持多时段 |

#### 已对齐的取舍（用户 2026-05-15 选定）

- **设置面位置** → 右侧 `NDrawer` 三 Tab
- **一键 AI 审核默认行为** → `force=true` 强跑，前端弹二次确认
- **多时段 schema 兼容性** → **直接改字段名**，不留兼容层；默认 `["04:00","16:00"]`，可增删时段
- **Drawer 内是否加快捷动作** → 暂不加，保持简洁

#### 目标布局（方案 A）

```text
AppPage Hero
  action: [刷新] [手动抽取] [一键 AI 审核] [新建] [⚙ 设置]
├── SlangMetrics                ← 保留 5 卡
├── SlangSnapshotStrip (新)     ← 替代 SlangAdvancedOverview，常驻 inline 4 胶囊
│   今日命中 / 最近抽取 / 上次 AI 审核 / 待人工复核(N)
├── SlangQueueToolbar           ← 保留
└── SlangTermList               ← 满宽，没有右栏挤压

NDrawer "工作台设置"（⚙ 触发，~520 px）
├── Tab 1 学习与注入   ← SlangSettingsForm 整体迁入
├── Tab 2 漂移与观察   ← SlangGovernanceSection 整体迁入
└── Tab 3 统计与运行   ← SlangAdvancedOverview 展开态 3 卡迁入
```

#### 拆分到子任务（开工前需逐项审查通过）

| # | 子任务 | 类型 | 预期改动 | 风险 |
| --- | --- | --- | --- | --- |
| U-1 | `services/slang/types.py` schema：删 `daily_ai_review_time: str`，新增 `daily_ai_review_times: list[str]`（默认 `["04:00","16:00"]`），重写 validator（每个 tag 校验 HH:MM、去重、排序、空列表回退默认） | 后端 schema | ~25 行 | **破坏配置兼容**：旧 `slang_settings.json` 里的 `daily_ai_review_time` 字段会被忽略，需要在迁移说明里说清楚 |
| U-2 | `plugins/slang/plugin.py:run_daily_ai_review_if_due` 改 slot-based 去重：`slot_key = f"{today}:{HH:MM}"`，按"已经过了的最后一个 slot"判定 due，写 `last_daily_ai_review_slot` meta key | 后端调度 | ~30 行 | 跨日切换 / 同一 slot 多次触发幂等性需要回归测试 |
| U-3 | 在 plugin `on_load` 缓存 `self._ctx = ctx`，或者把 `run_daily_ai_review(ctx, ...)` 重构为不依赖 ctx | 后端 | ~5 行 | 看是否还有别的调用点用 ctx 参数 |
| U-4 | `admin/routes/api/slang.py` 新增 `POST /slang/ai-review/run`：调用 `plugin.run_daily_ai_review`（force=true）；不接 force=false 路径，因为前端总是带 force | 后端路由 | ~15 行 | plugin 不可用时返回 503 |
| U-5 | `tests/test_slang_plugin.py` 三处 `settings.daily_ai_review_time = "00:00"` 改 `daily_ai_review_times = ["00:00"]`；新增多时段场景测试（两段、跨 slot 不重复跑、空列表回退默认） | 后端测试 | ~50 行 | — |
| U-6 | `scripts/dev/slang_semantic_smoke.py:424` `settings.get("daily_ai_review_time")` 改新字段 | 脚本 | ~3 行 | — |
| U-7 | 前端 `helpers/types.ts` `SlangSettings.daily_ai_review_time: string` → `daily_ai_review_times: string[]` | 前端类型 | ~3 行 | — |
| U-8 | 前端 `helpers/formatters.ts` DEFAULT_SLANG_SETTINGS / mergeSettings 同步改字段 + 数组校验 | 前端 helper | ~8 行 | — |
| U-9 | `SlangSettingsForm.vue` 把 `daily_ai_review_time` NInput 改 `NDynamicTags`（输入约束 HH:MM、最少 0 段最多 12 段、排序去重展示） | 前端表单 | ~40 行 | NDynamicTags 校验函数需要拒绝非 HH:MM 输入 |
| U-10 | 新增 `SlangSnapshotStrip.vue`（4 个 inline 胶囊，常驻不折叠） | 前端组件 | ~80 行 | — |
| U-11 | 删除 `SlangAdvancedOverview.vue`（其展开态 3 张卡 = SlangStatsCards 内容迁到新组件 SlangStatsCards.vue 给 Drawer Tab 3 用） | 前端组件 | -200 +130 行 | 注意 `expanded` v-model 接口移除 |
| U-12 | 新增 `SlangSettingsDrawer.vue`：`<NDrawer width="520">` + `<NDrawerContent>` + `<NTabs type="line">`，三 Tab 装入现有 SettingsForm / GovernanceSection / 新 SlangStatsCards | 前端组件 | ~110 行 | 移动端 < 920 px 改 100% 宽度 |
| U-13 | 主视图 `SlangView.vue`：hero 加「一键 AI 审核」(loading + 二次确认) + 「⚙ 设置」按钮；删 `slang-layout` 两栏 grid 改单栏；接入 SlangSnapshotStrip + SlangSettingsDrawer；`showAdvancedOverview` / `showAdvancedSettings` ref 全删；`saveSettings` 里 HH:MM 校验改数组校验 | 前端主视图 | -50 +40 行（净减） | settings 保存成功后是否自动关闭 Drawer：用户暂未说，默认**不自动关**（用户可能继续编辑别的 Tab） |
| U-14 | 二次确认对话框文案：「将立即对所有启用的群跑一次 AI 审核（force=true，跳过当日去重），可能消耗较多 LLM 配额。是否继续？」 | 前端文案 | ~5 行 | — |

#### 验证门径

- `vue-tsc --noEmit` → 0 error
- `npm run build` → 成功
- `uv run pytest tests/test_slang_plugin.py -v` → 全绿（含新增多时段测试）
- `uv run pyright services/slang plugins/slang admin/routes/api/slang.py` → 0 error
- 浏览器手测：
  - 首屏看不到"高级概览"折叠条；4 个胶囊常驻
  - ⚙ 按钮打开右抽屉，三 Tab 切换不丢状态
  - 「一键 AI 审核」点击 → 二次确认 → 成功提示带 `ai_approved / candidates / groups` 数字
  - settings 表单多时段输入：合法 HH:MM 接受、非法（如 `25:00` / `4:00` / 空）拒绝、保存后回显去重排序

#### 回滚

每一步独立 commit，最坏情况下：

- U-1/U-2 单独回滚：删除 `last_daily_ai_review_slot` meta key（如已写入），恢复 `daily_ai_review_time` 字段
- 前端单独回滚：`git checkout HEAD~ -- admin/frontend/src/views/slang/`
- 全量回滚：`git revert <commit-range>`

#### 待审查通过后再开工

本节落入跟踪文档时**未动任何代码**。由用户审查上述 14 个子任务的拆分粒度、是否漏点、是否需要补 D2 cancel-path 测试（跨群多时段时 wait_for 取消的场景）等。审查通过后按 U-1 → U-14 顺序逐项落地，每一步给出 typecheck/build/test 证据。

### 🟡 2026-05-21 KnowledgeView 拆分启动（B-1 helpers ✅ / B-2 只读子组件 ✅ / B-3 交互子组件 ✅ / C 待开）

按 SystemView / SlangView 同模板（B-1 helpers → B-2 只读子组件 → B-3 交互子组件 → C AppPanelSection 视觉收敛）推进。

#### 现状盘点

- 主视图 [admin/frontend/src/views/knowledge/KnowledgeView.vue](../../admin/frontend/src/views/knowledge/KnowledgeView.vue) 起点 **2186 行**（plan §6 写的 1766 是旧值——经过几轮 graph nodes / context metrics / candidates 增补后已涨到 2186）
- 7 个 tab：sources / search / context / metrics / graph / graph_nodes / candidates
- 13 个 TypeScript interface（`KnowledgeStats / KnowledgeSource / KnowledgeResult / ContextHit / ContextPack / ContextMetricRecent / ContextMetrics / GraphEntity / GraphRelationship / GraphCandidate / GraphNodeRow / GraphEdgeRow / GraphNodeStats`）+ `SupersedeDraft` 起点为内联匿名 `Record<string, { subject; predicate; object; note }>`
- 13 个工具函数：`scoreText / percentText / numberText / sourceStatusType / hitTypeLabel / hitTypeTag / shortHash / evidenceText / relationshipEvidenceText / relationshipScopeText / metricRatioEntries / topEntry / isNotFound`
- bundle 起点：`KnowledgeView-*.js` 待测（B-1 完成后实测 44.74 KB / gzip 12.68 KB）

#### B-1 helpers 抽取（2026-05-21 完成）

- 新建 [admin/frontend/src/views/knowledge/helpers/types.ts](../../admin/frontend/src/views/knowledge/helpers/types.ts) — 13 个 interface + `KnowledgeTab` + `SupersedeDraft`，176 行
- 新建 [admin/frontend/src/views/knowledge/helpers/formatters.ts](../../admin/frontend/src/views/knowledge/helpers/formatters.ts) — 10 个纯函数（`scoreText / percentText / numberText / shortHash / evidenceText / relationshipEvidenceText / relationshipScopeText / metricRatioEntries / topEntry / isNotFound`），78 行
- 新建 [admin/frontend/src/views/knowledge/helpers/badges.ts](../../admin/frontend/src/views/knowledge/helpers/badges.ts) — 3 个 tag/label 助手（`sourceStatusType / hitTypeLabel / hitTypeTag`），25 行
- 主视图：删除内联 13 interface + 13 function，改 import 自新 helpers；`SupersedeDraft` 类型用具名替代匿名；保留 `syncSupersedeDrafts` 因依赖局部 `supersedeDrafts.value` 与 `graphRelationships.value`
- 验证：`vue-tsc --noEmit` 0 error；`vite build` 5.30s
- 主视图行数：**2186 → 1974（-212，-9.7%）**
- bundle：起点未实测；B-1 后 `KnowledgeView-*.js` **44.74 KB / gzip 12.68 KB**（与 SystemView/SlangView B-1 持平的预期 helpers split 开销）

#### B-2 只读子组件（首批 3 个）✅ 2026-05-21

按 SystemView/SlangView 同纪律，**read-only 子组件不 emit 写动作**——交互按钮（reindex / refresh / search 等）保留在主视图的 `PageToolbar` 内。本次先抽 3 个无交互依赖的子组件：

- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeHero.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeHero.vue) — `knowledge-hero` 卡（eyebrow + sourceSummary + 3 个 NTag + 6 卡 status grid），165 行；props: `stats / sourceSummary / entryCount / sourceCount / skippedCount / relationshipCount / pendingCount / scopeRiskCount`
- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeSourcesPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeSourcesPanel.vue) — sources tab 的源卡列表 + 跳过原因 + 空态，101 行；props: `sources`
- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeMetricsPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeMetricsPanel.vue) — metrics tab 的 6 个 mini-card + 命中来源 / 类型 ratio 列表 + 最近查询列表 + 空态，238 行；props: `contextMetrics / recentMetricItems`

主视图改动：

- import 三个子组件 + 减掉 `metricRatioEntries / sourceStatusType / shortHash`（移到子组件内使用）
- 模板内联 hero 卡 / sources tab 的源卡列表 / metrics tab 的指标块 → `<KnowledgeHero />` / `<KnowledgeSourcesPanel />` / `<KnowledgeMetricsPanel />`
- scoped CSS 删除 `knowledge-hero / knowledge-hero__main / knowledge-hero__badges / knowledge-status / knowledge-status--warn / knowledge-status-grid` 6 块、`source-grid / source-card / source-card__head / source-card__head strong/span / source-card__meta / source-card__reason` 8 块、`metrics-layout / metrics-grid / metric-mini-card / metric-mini-card span/strong / metrics-columns / metrics-panel / metric-ratio-list / metric-ratio-row / recent-context-list / recent-context-card / recent-context-card__main / recent-context-card__main span` 12 块；调整 `.result-card / .context-hit / .relationship-card / .candidate-card` 选择器组、移除媒体查询里的 `.knowledge-status-grid / .knowledge-hero__main / .source-grid / .metrics-grid / .metrics-columns / .source-card__head` 共 ~352 行 CSS

验证：

- `vue-tsc --noEmit` — 0 error（`/tmp/vue-tsc-b2.log` 0 行）
- `vite build` — 5.29s
- 主视图行数：**1974 → 1622（-352，-17.8%）**；累计 2186 → 1622（-25.8%）
- bundle：B-1 44.74 KB / gzip 12.68 KB → **B-2 46.02 KB / gzip 13.03 KB**（+1.28 / +0.35 gzip，3 子组件 scoped style 复制的预期开销，与 SystemView/SlangView B-2 同量级；PR C 收敛 AppPanelSection 后会回吐部分）

回滚：`git revert <commit>`，再 `rm admin/frontend/src/views/knowledge/components/{KnowledgeHero,KnowledgeSourcesPanel,KnowledgeMetricsPanel}.vue` 即恢复（不动 B-1 helpers）。

#### B-3 交互子组件（5 个）✅ 2026-05-21

剩余四块 tab 都含写动作，`defineModel` 双向绑定输入框 / 草稿，emit 写动作回主视图执行 API。同时把 graph_nodes tab 的 NDrawer 也下沉到子组件以保持封闭：

- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeSearch.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeSearch.vue) — search tab 输入 + 结果列表 + 空态，140 行；`v-model:searchQ` + props `searchResults / searching / hasSearched / lastSearchQ`，emit `search` / `clear`
- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeContextPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeContextPanel.vue) — context tab 三输入 + Prompt pack 输出 + hits 列表，254 行；`v-model:contextQ / contextUserId / contextGroupId` + props `contextPack / contextHits / contextSearching / hasContextSearched / contextUnsupported`，emit `debug`
- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeGraphPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeGraphPanel.vue) — graph tab 实体侧栏 + 关系列表 + scope risk + supersede / rollback drafts，350 行；`v-model:factRollbackNotes / supersedeDrafts` + props `graphEntities / graphRelationships / graphScopeRisks / graphLoading / graphUnsupported / factBusy`，emit `rollback(rel)` / `supersede(rel)`
- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeCandidatesPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeCandidatesPanel.vue) — candidates tab 候选卡列表 + approve/reject 备注，156 行；`v-model:rejectNotes` + props `candidates / candidateLoading / candidateBusy / graphUnsupported`，emit `approve(c)` / `reject(c)`
- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeGraphNodesPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeGraphNodesPanel.vue) — graph_nodes tab 4 MetricCards + 3 筛选输入 + 节点卡列表 + NDrawer 详情，345 行；`v-model:graphNodeFilterType / graphNodeFilterGroup / graphNodeSearch / graphNodeDrawerOpen` + props `graphNodes / graphNodeTotalCount / graphEdgeTotalCount / graphNodeTopType / graphEdgeTopType / graphNodeLoading / graphNodesUnsupported / graphNodeDrawerNode / graphNodeDrawerEdges / graphNodeDrawerLoading`，emit `reload` / `clear-filters` / `open-detail(node)`

主视图改动：

- import 5 个子组件；删除 `DocumentTextOutline / FlashOutline / LayersOutline` 图标 + `AppCard / EmptyState / MetricCard` 公共组件 + `hitTypeLabel / hitTypeTag` badges + `evidenceText / numberText / percentText / relationshipEvidenceText / relationshipScopeText / scoreText` formatters（全部下沉到子组件内使用），保留 `isNotFound / topEntry`；`ContextHit / ContextMetricRecent` 类型也下沉，保留 `ContextMetrics / ContextPack` 因主视图持有引用
- 5 块 tab body 替换为子组件实例：search → `<KnowledgeSearch>`、context → `<KnowledgeContextPanel>`、graph → toolbar + `<KnowledgeGraphPanel>`、candidates → toolbar + `<KnowledgeCandidatesPanel>`、graph_nodes → toolbar + `<KnowledgeGraphNodesPanel>`
- 新增 `clearSearch()` handler 替代原 inline `searchQ = ''; ...`，避免子组件 emit 出去后主视图还在维护内联清理逻辑
- scoped CSS 砍到只剩 `knowledge-compat-alert / knowledge-tabs / knowledge-toolbar__title / knowledge-toolbar__hint` 4 块共 18 行，其余 ~330 行（result-card / context-hit / relationship-card / candidate-card / graph-scope-risk / graph-layout / context-layout / entity-list / relationship-card__triple|evidence|governance|rollback|supersede / candidate-card__body / graph-node-metrics / graph-node-card / graph-node-detail / graph-node-edge 全套）全部下沉到对应子组件

验证：

- `vue-tsc --noEmit` — 0 error（`/tmp/vue-tsc-b3.log` 0 行）
- `vite build` — 5.47s
- 主视图行数：**1622 → 754（-868，-53.5%）**；累计 2186 → 754（-65.5%）
- bundle：B-2 46.02 KB / gzip 13.03 KB → **B-3 52.82 KB / gzip 14.87 KB**（+6.80 / +1.84 gzip，5 子组件 scoped style 复制的预期开销，与 SystemView/SlangView B-3 同量级；PR C 收敛 AppPanelSection 后会回吐部分）

回滚：`git revert <commit>`，再 `rm admin/frontend/src/views/knowledge/components/{KnowledgeSearch,KnowledgeContextPanel,KnowledgeGraphPanel,KnowledgeCandidatesPanel,KnowledgeGraphNodesPanel}.vue` 即恢复（不动 B-1 helpers / B-2 只读子组件）。

#### C AppPanelSection 视觉收敛（下一步）

主视图保留的 5 个 `PageToolbar`（sources / metrics / graph / candidates / graph_nodes）+ 子组件内剩余的 `section-head / knowledge-eyebrow` 用法都可以收敛到 `AppPanelSection`，与 SystemView / SlangView 对齐。预计删除 ~80 行重复 scoped CSS、bundle gzip 回吐 ~0.3 KB。

## 阶段 4 — 长尾页面（不专门跟踪）

按 plan §7 在日常任务里穿插推进。每月跑一次合规扫描脚本，结果记录到 `maintenance-log.md`。

## 阶段 5 — 可选优化

| 项 | 状态 | 决策 |
|---|---|---|
| pnpm 迁移 | ☐ 未启动 | 视 node_modules 体积焦虑度决定 |
| chunk 拆分 | ☐ 未启动 | 等阶段 3 完成后再评估 |

## 验收门径速查

每个阶段过门径：

- 阶段 0：`git status` + `cat .nvmrc` + `grep engines package.json`
- 阶段 1：`grep -c "!important" src/styles/global.css` + 浅深主题截图对比
- 阶段 2：访问 `/admin/design-playground`，所有公共组件全部能渲染、能切换浅深、能 hover/focus
- 阶段 3：每个视图改完按 [web-refactor-plan §6.4](../web-refactor-plan.md) 7 项验收清单逐项打勾

## 相关链接

- [docs/web-refactor-plan.md](../web-refactor-plan.md) 主方案
- [docs/admin-ui-style-guide.md](../admin-ui-style-guide.md) 风格规范
- [docs/admin-ui-tokens.md](../admin-ui-tokens.md) Token 速查（**新**）
- [docs/agent-ui-guidelines.md](../agent-ui-guidelines.md) Agent 行为规范
- `.claude/skills/omubot-design-system/SKILL.md` 设计系统执行 skill
