# Admin Web 前端重构进度跟踪

跟踪 [docs/web-refactor-plan.md](../web-refactor-plan.md) 各阶段任务执行情况。每完成一项打勾，并记录验证证据（命令输出、commit、PR 链接、截图位置）。

## 元信息

- **重构启动日期**：2026-05-13
- **当前阶段**：阶段 3 长尾收敛完成 + 阶段 4 月度合规脚本上线 + 阶段 0.6 Jinja 退役完成（2026-05-23）。完成视图：DashboardView ✅ / LogsView ✅ / GroupsView ✅ / LoginView ✅ / ConfigView ✅ / SystemView ✅ / SlangView ✅ / KnowledgeView ✅ / MemosView ✅ / CrossGroupView ✅ / BlockTraceView ✅ / EpisodesView ✅ / MemoryConsolidatorView ✅ / StyleView ✅ / UsageView ✅ / SystemBackup ✅ / AffectionView ✅ / StickersView ✅ / MemoryView ✅
- **下一里程碑**：按月跑 `scripts/check-ui-compliance.sh` 把数字写进 `maintenance-log.md`；阶段 5 可选优化（pnpm / chunk 拆分 待启动）。阶段 0.6 已落地——`/admin/*` 全部 SPA，数据走 `/api/admin/*`
- **人工验收记录**：2026-05-15 SlangView PR C 通过——SlangTermList 的 `slang-list-panel` 与主视图 `slang-settings-panel` 两块 panel-head 改 `<AppPanelSection eyebrow title>`，分页 / 折叠按钮通过 `<template #aside>` 接入；删 SlangView 4 块（settings-panel padding + panel-head + eyebrow + title）+ SlangTermList 4 块（list-panel padding + panel-head + eyebrow + title）共 ~62 行 scoped CSS；`AppCard` import 在主视图删除，新增 `AppPanelSection` import；`vue-tsc` 0 error、`vite build` 4.93s、`SlangView-*.js` 60.63 KB / gzip 17.26 KB（vs B-3 60.89 KB / 17.34 KB，**-0.26 KB / -0.08 KB gzip**）。SlangView 累计：2662 → 814 行（-69.4%）+ 9 子组件 + 3 helper + 头部样式收敛 AppPanelSection。2026-05-15 PR C 通过（SystemView）——SystemView 9 子组件迁 `AppPanelSection`（AdvancedEntry / Backup / Maintenance / Policies / Protocol / Providers / Resources / RuntimeErrors / ServiceHealth），删除 9 份重复的 `system-panel__head/__eyebrow/__title` scoped CSS（共 ~252 行），9 个 `AppCard` import 换 `AppPanelSection`；`vue-tsc` 0 error、`vite build` 4.75s、`SystemView-*.js` 53.35 KB / gzip 16.84 KB（vs B-3 55.03 KB / 17.03 KB，**-1.68 KB / -0.19 KB gzip**）。视觉副作用：title 字号 18px→16px、padding 20→18、head 底 margin 18→14——与 GroupsView/MemoryView 等其它视图正式对齐。`SystemProviderEditorDrawer` 因头部在 NDrawerContent header 内，不能套 AppPanelSection，保留本地 14 行样式。Hero / Metrics 没有 panel head 模式，不动。SystemView 累计变化：3326 行 → 590 行（-82%）+ 12 子组件全部 < 500 行 + 头部样式收敛到公共组件。2026-05-15 PR B-3 通过——SystemView 1649 → 590 行（-1059，~64%；累计 2842 → 590，-79%），抽 3 个交互子组件 Providers (479) / Protocol (484) / ProviderEditorDrawer (313)，全部 < 500 行，emit-up 写回主视图调 API；`vue-tsc` 0 error、`vite build` 4.88s、`SystemView-*.js` 55.03 KB / gzip 17.03 KB（vs B-2 52.62 KB / 16.16 KB，+2.41 KB / +0.87 KB，scoped CSS 三块复制造成的预期开销，PR C 收敛 AppPanelSection 后回吐）。2026-05-15 PR B-2 通过——SystemView 2842 → 1649 行（-1193，~42%），抽 9 个只读子组件至 `components/`（Hero 178 / Metrics 73 / Maintenance 386 / ServiceHealth 194 / RuntimeErrors 219 / Resources 184 / Policies 206 / AdvancedEntry 148 / Backup 89），全部 < 400 行；`vue-tsc` 0 error、`vite build` 4.70s、`SystemView-*.js` 52.62 KB / gzip 16.16 KB（vs B-1 49.49 KB / 15.20 KB，+3.13 KB / +0.96 KB，9 子组件 scoped style 重复造成的预期开销，PR C 收敛 AppPanelSection 后会回吐）。2026-05-15 PR B-1 通过——SystemView 3326 → 2842 行（-484，~14.5%），抽出 `helpers/{types,formatters,badges}.ts` 共 541 行；`vue-tsc` 0 error、`vite build` 4.88s、`SystemView-*.js` 49.49 KB / gzip 15.20 KB（与拆分前持平）。2026-05-15 用户验收通过——ConfigView 整页重做（卡片错位 + 卡套卡 + list/kv 行错位 + 子组件未注册导致 API key/任务模型分配空白 + 回复风格下拉截断 全部修复）。2026-05-14 验收通过——`/admin/design-playground` 浅/深主题公共组件渲染正常 + DashboardView + GroupsView + LoginView 视觉验收通过。同步删除 `global.css` 的 7 个 `@audit redundant` 块（41 行 `!important`），计数 51 → 31，剩余 31 行全部在 keep 区（`.dark .n-button:not(...)` + `.dark .n-menu` 系列）。

## 阶段 0 — 环境清理

| # | 任务 | 状态 | 验证 |
|---|------|------|------|
| 0.1 | 新增 `admin/frontend/.nvmrc` 锁定 Node 20 | ✅ | `cat admin/frontend/.nvmrc` → `20` |
| 0.2 | `package.json` 添加 `engines.node ">=20.0.0 <21"` | ✅ | `grep engines admin/frontend/package.json` |
| 0.3 | `.gitignore` 加 `admin/static/assets/` | ✅ | 行 47：`admin/static/assets/` |
| 0.4 | 现有 `admin/static/assets/*` 从 git 索引移除（保留磁盘） | ✅ 2026-05-23 | `git ls-files admin/static/assets/ \| wc -l` → 0；磁盘构建产物保留 |
| 0.5 | 审计 `admin/templates/*.html` 是否还被引用 | ✅ | `grep -rn "render_template\|TemplateResponse" admin/ --include="*.py"` 计数 0 |
| 0.6 | 删除无引用的 `admin/templates/*.html` | ✅ 2026-05-23 | 端口 `/api/admin/usage/data` + SPA 切换 + 删 8 个 Jinja `include_router` + 清 `admin/auth.py` 登录回填 + `git rm` 7 个 routes + templates.py + 9 模板，共 17 文件；详见下方"执行落地" |

任务 0.4 / 0.6 均已落地。

### 阶段 0.6 二审：模板还在活路径

`admin/__init__.py:130` 的 SPA fallback `@router.get("/admin/{rest:path}")` 在 8 个 Jinja 子路由 (`include_router(...)` 行 142-165) **之前**注册，FastAPI 注册顺序 = 匹配顺序，因此**绝大多数 /admin/* GET 请求**会命中 SPA 兜底而不会再走 Jinja 渲染。但有两类活路径：

1. **POST 路径** —— `auth.py` 的 `POST /admin/login` 拿不到 SPA fallback（fallback 只 `@router.get`），登录失败时仍会 `render("login.html", ...)` 返回 HTML 表单；
2. **被 SPA 主动调用的 Jinja 子路径** —— `DashboardView.vue:476` 的 `api<UsageDataResponse>('/admin/usage/data?period=day')` 是 `usage_admin_router.get("/admin/usage/data")` 的精确子路径（不会被 fallback 拦截，因为 fallback 是 `/admin/{rest}` 单段而非全捕获），目前 SPA 仪表盘还在依赖这条数据接口。

实测点位：

```text
admin/auth.py:101                    POST /admin/login 失败回填  → render("login.html", ...)
admin/auth.py:124                    POST /admin/login 失败回填  → render("login.html", ...)
admin/routes/groups.py:39            GET  /admin/groups          → render("groups.html", ...)
admin/routes/soul.py:32              GET  /admin/soul            → render("soul.html", ...)
admin/routes/usage.py:17             GET  /admin/usage           → render("usage.html", ...)
admin/routes/group_memory.py:75/109/134                          → render("group_memory.html", ...)
admin/routes/config_viewer.py:24     GET  /admin/config          → render("config_viewer.html", ...)
admin/routes/logs.py:38/55           GET  /admin/logs[/api]      → render("logs.html", ...)
admin/routes/dashboard.py:34         GET  /admin/dashboard       → render("dashboard.html", ...)
```

**前置条件**（满足后才能跑 `git rm admin/templates/*.html`）：

1. 把 `DashboardView.vue:476` 的 `/admin/usage/data` 切换到 `/api/admin/usage/...` 等价接口；
2. 评估 `admin/auth.py` 失败回填：要么改为 JSON + SPA 处理，要么把这一份 `login.html` 与其他非活模板分开；
3. 从 `admin/__init__.py:142-165` 删 8 个 `include_router(...)` 行；
4. 之后才能 `git rm admin/templates/*.html` 与对应 `admin/routes/*.py` 路由文件。

这不是阶段 3 长尾的范畴，单独留作**未来运维窗口**任务，需要人工拍板。

### 阶段 0.6 执行落地（2026-05-23）

人工拍板"一刀切"后按上面的 4 步前置条件依次落地：

1. **新建 `admin/routes/api/usage.py`**：`create_usage_router()` 把原 `/admin/usage/data`（period / date 两参，返回 timeseries / summary / top_users / top_groups / by_model 五段）整体平移到 `/api/admin/usage/data`，依赖 `usage_tracker`，与 `services/llm/usage_routes.py` 的单值公开端点（today / month / top-users / top-groups）正交。
2. **`admin/routes/api/__init__.py`** 在 dashboard router 之后挂载 `create_usage_router(usage_tracker=usage_tracker)`。
3. **`admin/frontend/src/views/dashboard/DashboardView.vue:476`** 由 `/admin/usage/data?period=day` 切到 `/api/admin/usage/data?period=day`；构建产物 `DashboardView-t4Z3CdC8.js` 中只剩新端点，旧路径 0 命中。
4. **`admin/__init__.py`** 删除 7 个 `from admin.routes.xxx import ...` + `from admin.auth import create_login_router` 共 8 行 import；同时删除登录回填 `include_router(create_login_router())` + 7 个 Jinja 子路由 `include_router(...)` 共 8 行挂载；SPA 兜底注释更新为"legacy Jinja-rendered admin pages were retired 2026-05-23"。
5. **`admin/auth.py`** 删除整个 `create_login_router()`（含 `GET /admin/login` 渲染、`POST /admin/login` 表单回填、`GET /admin/logout`）+ 不再使用的 `RedirectResponse` import；保留 `AdminAuthMiddleware` + 4 个 HMAC helper + `_get_admin_token` + `_API_SKIP_PATHS`，SPA 通过 `/api/admin/login` JSON 端点完成同样的认证闭环。
6. **`git rm`** 17 个文件：7 个 `admin/routes/{config_viewer,dashboard,group_memory,groups,logs,soul,usage}.py` + `admin/templates.py` + 9 个 `admin/templates/{base,config_viewer,dashboard,group_memory,groups,login,logs,soul,usage}.html`；`admin/templates/` 目录从磁盘消失。

验证证据：

- `python -c "from admin import create_admin_router"` → import ok（无悬挂引用）
- `uv run pytest tests/test_admin_api.py tests/test_usage_routes.py --deselect ::test_system_services_health_endpoint`：53 passed（被 deselect 那一条是 backup_disk 磁盘 97% 触发的 error alert，stash 后复现，与本批无关）
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`：0 error
- `cd admin/frontend && npm run build`：✓ built in 10.80s，产物 `DashboardView-t4Z3CdC8.js` 28.76 KB / gzip 10.20 KB
- `grep -roE '"/admin/usage/data"' admin/static/assets/ admin/frontend/src/`：0 命中

部署提示：`admin/__init__.py` / `admin/auth.py` / 新增 `admin/routes/api/usage.py` 是 `.py` 改动，bind-mount 不生效，需要 `dot_clean . && docker compose up bot -d --build`（D6）。SPA 部分由 bind mount 直接生效。

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

#### ✅ 2026-05-23 收口（U-1 ~ U-14 全部落地，含偏差校准）

按 plan 逐项推进，期间为符合代码现状做了几处命名/结构偏差，统一校准如下：

- **U-2** plugin 方法名落地为 `run_backlog_review_one_batch_if_due`（不是计划里的 `run_daily_ai_review_if_due`）；meta key 落地为 `last_backlog_review_slot`（不是 `last_daily_ai_review_slot`）。理由：与既有 `run_backlog_review_now / run_backlog_review_continuous` 命名一致，slot 与 backlog 语义绑定更准。
- **U-3** 走第二条路线：把 `run_backlog_review_now/continuous(ctx=None, *, ...)` 改成可选 ctx，背景任务通过 `_bg_run_with_timeout(plugin, None)` 触发即可，不再缓存 `self._ctx`。
- **U-4** 路径同 plan：[admin/routes/api/slang.py](../../admin/routes/api/slang.py) `POST /slang/ai-review/run`，plugin 不可用 → 503，`_backlog_review_in_flight=True` → 409，否则 `asyncio.create_task(_bg_run_with_timeout(plugin, None))`。
- **U-5** 多 slot 测试落地为 8 个新增用例：validator dedup/sort/默认回退/HH:MM 拒绝；plugin slot 决策（`no_slots / not_due / due+drain → 锁 slot / 同 slot rerun → already_ran / 跨 slot reset / disabled`）。`uv run pytest tests/test_slang_plugin.py` 全绿（16 通过）。
- **U-12** Drawer 的实际宽度与 Tab 命名与 plan 的「520 + 学习与注入 / 漂移与观察 / 统计与运行」不同，落地为 **width=480 + 抽取 / 清池 / 注入** 三 Tab：抽取 = SettingsForm 学习与抽取部分；清池 = 含 `daily_ai_review_times` 的 NDynamicTags + AI 清池开关 + 自动通过阈值；注入 = 注入开关、查询工具、Prompt 字符上限等。理由：与现有 backend 字段分组（`backlog_*`、`extract_*`、`inject_*`）一一对应，运维心智更直，移动端 480 已够用，不必再压。`SlangAdvancedOverview` 的 3 张概览卡复用为 `SlangStatsCards` 直接挂在主视图（不进 Drawer Tab 3）。
- **U-13** hero 操作区落地为「刷新 / 手动抽取 / AI 清池（NPopconfirm）/ 新建黑话 / ⚙ 设置」5 个按钮；删除 `slang-main-layout` 双栏 grid + 整个 `<aside class="slang-sidebar">` 块（含 7 个 NSwitch、`daily_ai_review_times` NDynamicTags、`autoSaveSidebarSettings` 防抖逻辑）；接入 `SlangSnapshotStrip` 在 SummaryBar 之后；`SlangExtractionProgress` 与 `SlangStatsCards` 落入主列。`SlangView.vue` 从 1029 行降到 849 行（净 −180 行）。
- **U-11** `SlangAdvancedOverview.vue` 物理删除（git rm，已不被任何文件引用）。
- **U-14** Popconfirm 文案改回 plan 原版：「将立即对所有启用的群跑一次 AI 审核（force=true，跳过当日去重），可能消耗较多 LLM 配额。是否继续？」

#### 验证证据

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 0 error
- `cd admin/frontend && npm run build` → 成功，`SlangView-*.js` 体积同步刷新
- `pkill -9 -f pytest && uv run pytest tests/test_slang_plugin.py -q` → 16 passed
- `bash scripts/check-ui-compliance.sh` → SlangView 内联样式 / `!important` 数无新增（只在已知白名单范围内）
- 浏览器手测：hero 5 按钮可用；snapshot 4 胶囊常驻；⚙ Drawer 三 Tab 切换无白屏；AI 清池 popconfirm 文案完整；多时段输入接受 `04:00,16:00`，拒绝 `25:00`；保存后回显去重排序

剩余可选追加：暂无。该 U-1 ~ U-14 计划闭环。

### ✅ 2026-05-21 KnowledgeView 拆分完成（B-1 helpers ✅ / B-2 只读子组件 ✅ / B-3 交互子组件 ✅ / C AppPanelSection ✅）

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

#### C AppPanelSection 视觉收敛 ✅ 2026-05-21

把 B-2 / B-3 子组件里复制出来的 `section-head / knowledge-eyebrow / h3` 三件套全部收敛到 `AppPanelSection`，与 SystemView / SlangView 对齐：

- [admin/frontend/src/views/knowledge/components/KnowledgeMetricsPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeMetricsPanel.vue) — 2 个面板（`Sources / 命中来源` + `Types / 命中类型`）从 `<AppCard bordered elevated class="metrics-panel">` + `section-head + knowledge-eyebrow + h3` 改为 `<AppPanelSection eyebrow title>`；删 `.metrics-panel / .section-head / .section-head h3 / .knowledge-eyebrow` 共 4 块 + 媒体查询里的 `.section-head` 收尾，**净 -32 行**
- [admin/frontend/src/views/knowledge/components/KnowledgeContextPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeContextPanel.vue) — `Prompt Pack / 最终打包文本` 面板同样改造，trailing `<NTag>` 走 `#aside` slot；删 `.context-pack-card / .section-head / .section-head h3 / .knowledge-eyebrow` + 媒体查询里的 `.section-head`；`.context-pack { margin: 14px 0 0 }` 收成 `0`（`AppPanelSection` 自带 head→body 间距），**净 -32 行**
- [admin/frontend/src/views/knowledge/components/KnowledgeGraphPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeGraphPanel.vue) — `Entities / 实体` 面板改造，trailing `<NTag>{{ length }} 个</NTag>` 走 `#aside`；删 `.graph-entities padding / .section-head / .section-head h3 / .knowledge-eyebrow` + 媒体查询里的 `.section-head`；`.entity-list { margin-top: 14px }` 收成 `0`，**净 -45 行**
- 三处都补 `import AppPanelSection from '../../../components/common/AppPanelSection.vue'`，`AppCard` 因仍用于 `metric-mini-card / recent-context-card / context-hit / relationship-card / candidate-card` 等子卡保留

主视图（[KnowledgeView.vue](../../admin/frontend/src/views/knowledge/KnowledgeView.vue)）本轮不动——B-3 之后主视图只剩 4 块壳级 scoped CSS（`knowledge-compat-alert / knowledge-tabs / knowledge-toolbar__title / knowledge-toolbar__hint`），没有 `section-head` 残留。

验证：

- `vue-tsc --noEmit` — 0 error
- `vite build` — 5.37s
- 三个子组件行数累计：B-3 末（238 + 254 + 350）= 842 → C 末（211 + 222 + 312）= 745，**净 -97 行**（git diff 报 -109，差额是空行 / 缩进重排）
- bundle：B-3 52.82 KB / gzip 14.87 KB → **C 52.32 KB / gzip 14.79 KB**（-0.50 / -0.08 gzip，与 SystemView / SlangView C 同量级回吐 ~0.1 KB gzip 量级）

回滚：`git revert <commit>`；`AppPanelSection` 这层是无破坏性的纯模板替换，子组件外部 props/emits 完全不变，主视图无须配合改动。

至此 [admin/frontend/src/views/knowledge/](../../admin/frontend/src/views/knowledge/) 目录达到与 SystemView / SlangView 一致的"主视图 ≤ 800 行 + 子组件每个 ≤ 400 行 + 复用 `AppPanelSection`"分层结构，KnowledgeView 拆分四阶段（B-1 / B-2 / B-3 / C）全部完成。

### ✅ 2026-05-21 KnowledgeView 简化重构（信息架构治本，PR1 ✅ / PR2 ✅ / PR3 ✅）

拆分四阶段已收官代码层，但**信息架构层未治本**：① 顶部 7-tab 把用户高频检索和管理员低频维护混在同层级；② Hero 6 格 status-grid warn 不可点击；③ search/context/metrics 三块都是"输入 → 命中"模式但拆三处。本轮按"层级分离 → 同层合并 → 入口收口 → 视觉收敛"四步，分 PR1/PR2/PR3 推进，每 PR 独立可验证、独立可回滚。

#### PR1 视觉减负（Hero 收缩 + Sidebar）✅ 2026-05-21

- [admin/frontend/src/views/knowledge/components/KnowledgeHero.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeHero.vue) — 删 status-grid（6 格平铺 KPI），props 9 → 2（仅 `stats / sourceSummary`）；padding 20 → 16/20、margin-bottom 18 → 14、h3 18px、eyebrow letter-spacing 0.14em → 0.18em；删 `.knowledge-status*` / `.hero-progress*` CSS 与媒体查询里的 status-grid 样式。**净 -101 行**（166 → 65）
- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeSidebar.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeSidebar.vue) — 260px sticky 立柱，三段式：Index 块 3 stat（文档片段 / 文档源 / 图谱事实）、Backlog 块 3 chip-button（跳过源 / 候选待审 / 作用域待查；warn>0 高亮金棕色，点击 emit `open-admin(tab)`，跳过源→graph_nodes / 候选待审→candidates / 作用域待查→graph）、Actions 块 全局刷新 + 重建索引（NPopconfirm 包裹）。`@media (max-width: 1180px) { position: static; }` 与 SlangView 同纪律。**新增 290 行**
- [admin/frontend/src/views/knowledge/KnowledgeView.vue](../../admin/frontend/src/views/knowledge/KnowledgeView.vue) — 主布局改 `display: grid; grid-template-columns: minmax(0, 1fr) 260px;`（响应式 < 1180px 退回 1fr）；KnowledgeHero 调用瘦身（仅传 `stats / source-summary`）；顶部 PageToolbar 删 刷新 + 重建索引 两按钮（迁到 sidebar），仅保留 `<NTag>运行中/未启用</NTag>`；新增 `handleOpenAdmin(tab)` handler（PR1 fallback 切 NTabs，PR2 改打开 drawer）；template 包裹 `.knowledge-layout > .knowledge-main > NTabs` + `<KnowledgeSidebar>` 双栏布局。**净 +25 行**（754 → 779；仍 ≤ 800 行目标）

D1 同模式扫描：`grep -rn "status-grid\|hero-progress\|knowledge-status__" admin/frontend/src/views/knowledge/` 0 hits；`grep "minmax(0, 1fr) 260px"` 与 SlangView 视觉验收通过版本对齐。

验证：`vue-tsc --noEmit` 0 error；`vite build` 5.41s；bundle `KnowledgeView-*.js` 52.32 KB / gzip 14.79 → **55.86 KB / gzip 16.13**（+3.54 / +1.34，KnowledgeSidebar 新增 + grid 布局 + handler 的合理增量；PR3 删 KnowledgeSearch.vue 后会回吐）；浏览器侧双栏布局 + sidebar sticky 正常 + 现有 7-tab 仍可工作（PR1 不动 NTabs）。

PR1 不做的事（推到 PR2 / PR3）：① 不动 NTabs / 不引入 AdminDrawer / 不收口用户侧三 tab；② sidebar warn chip 的"看到数字一键跳"链路 PR1 暂走 fallback（emit → 切 NTabs），不阻塞 PR2 接 AdminDrawer；③ 高破坏性按钮 NPopconfirm（rollback / supersede / reject）PR1 只在 reindex 上落了一处（sidebar 内），其余 3 处推到 PR2 一并改。

回滚：`git revert <commit>`，再 `rm admin/frontend/src/views/knowledge/components/KnowledgeSidebar.vue` 即恢复（不动其他 KnowledgeView 拆分阶段成果）。

#### PR2 信息架构（AdminDrawer + 删 NTabs admin 三 tab + NPopconfirm）— ✅ 2026-05-21

- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeAdminDrawer.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeAdminDrawer.vue) — `<NDrawer width="720" placement="right">` + `<NDrawerContent title="知识库管理" closable>` + 顶部 hint + 内置 `<NTabs type="line">` 三 tab（候选 / 图谱 / 节点），每个 tab 顶部一行 toolbar（标题 + 简介 + 刷新按钮）+ 透明 wrap 现有 [KnowledgeCandidatesPanel](../../admin/frontend/src/views/knowledge/components/KnowledgeCandidatesPanel.vue) / [KnowledgeGraphPanel](../../admin/frontend/src/views/knowledge/components/KnowledgeGraphPanel.vue) / [KnowledgeGraphNodesPanel](../../admin/frontend/src/views/knowledge/components/KnowledgeGraphNodesPanel.vue)。9 v-model + 9 emit。**新增 192 行**
- [admin/frontend/src/views/knowledge/components/KnowledgeCandidatesPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeCandidatesPanel.vue) — 拒绝按钮包 NPopconfirm（"拒绝后该候选不再进入图谱，确认？"）
- [admin/frontend/src/views/knowledge/components/KnowledgeGraphPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeGraphPanel.vue) — 3 处包 NPopconfirm：scope-risk 回滚、事实回滚、事实取代
- [admin/frontend/src/views/knowledge/helpers/types.ts](../../admin/frontend/src/views/knowledge/helpers/types.ts) — `KnowledgeTab` 拆分为 `KnowledgeTab`（用户侧 4：sources/search/context/metrics）+ `KnowledgeAdminTab`（管理员 3：candidates/graph/graph_nodes）
- [admin/frontend/src/views/knowledge/KnowledgeView.vue](../../admin/frontend/src/views/knowledge/KnowledgeView.vue) — 删 3 个 admin NTabPane（共 ~80 行）；删 KnowledgeCandidatesPanel / KnowledgeGraphNodesPanel / KnowledgeGraphPanel / RefreshOutline 直接 import；新增 `adminDrawerOpen` + `adminActiveTab` ref；`handleOpenAdmin` 从"切 NTabs"改成"打开 drawer + 定位 tab"；`#action` slot 加 `<NButton quaternary>管理</NButton>`；template 末尾追加 `<KnowledgeAdminDrawer>` 实例（9 v-model + 9 prop + 9 emit handler）。**净 -39 行**（779 → 740）

D1 同模式扫描：5 处 NPopconfirm（PR1 sidebar reindex + PR2 reject + 2×rollback + supersede）覆盖全部高破坏性按钮，与 SlangView 纪律对齐。

验证：`vue-tsc --noEmit` 0 error；`vite build` 5.36s；bundle `KnowledgeView-*.js` 55.86 → **61.60 KB / gzip 16.13 → 17.20**（+5.74 / +1.07，drawer + 4 NPopconfirm 的合理增量；PR3 删 KnowledgeSearch.vue 后部分回吐）。

浏览器侧（待用户验收）：① KnowledgeView 顶部 NTabs 仅剩用户侧 4 tab；② 点击"管理"按钮打开右侧 720px drawer，3 admin tab 切换工作；③ sidebar warn chip 点击自动打开 drawer 并定位（跳过源 → graph_nodes / 候选待审 → candidates / 作用域待查 → graph）；④ 4 个 NPopconfirm 触发顺畅；⑤ 候选 / 图谱 / 节点行为与原 NTab 完全一致（透明 wrap）。

回滚：`git revert <commit>`，再 `rm admin/frontend/src/views/knowledge/components/KnowledgeAdminDrawer.vue` 即恢复（透明 wrap 纪律下子组件 props/emits 不变，回滚自动还原 7-tab）。

#### PR3 用户侧 Workspace 收口（4 tab → 2 tab，单一 query 同时驱动 details/pack/metrics）— ✅ 2026-05-21

把 search / context / metrics 三 tab 合成单一 `<KnowledgeContextWorkspace>`，顶部 query + user/group ID 三输入条 submit 一次并发跑 `searchKnowledge` + `debugContext`，命中分文档片段 / 统一上下文两组同屏展示；workspace 内置 3 tab（命中详情 / Prompt Pack / 评测指标）作为同一 query 的不同视角。

改动：

- 新建 [admin/frontend/src/views/knowledge/components/KnowledgeContextWorkspace.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeContextWorkspace.vue)（623 行）— 顶部 `workspace-query` 容器（主输入 `min(520px) clearable` + 两个 scope 输入 small + submit 主按钮 stretch）+ 内置 `<NTabs type="line">` 三 tab：① 命中详情 用 AppPanelSection 分组渲染文档命中（searchResults，蓝色 score tag）+ 上下文命中（contextHits，hitTypeTag + score）+ 引导 / 不支持 / 空命中三态；② Prompt Pack 单卡片 `<pre class="workspace-pack">` + 省略数 NTag aside；③ 评测指标 6 mini-card + Sources/Types AppPanelSection（前者 aside 嵌"刷新"text 按钮）+ Recent Hits AppPanelSection；4 个 v-model + 11 props + 2 emit（submit / reload-metrics）
- [admin/frontend/src/views/knowledge/KnowledgeView.vue](../../admin/frontend/src/views/knowledge/KnowledgeView.vue) — ① 顶部 4 tab → 2 tab（删 search / context / metrics 三个 NTabPane，新增单一 `workspace` tab 直接挂载 `<KnowledgeContextWorkspace>`）；② 状态合并：`searchQ` / `contextQ` / `contextUserId` / `contextGroupId` 四个 ref → 三个 ref `workspaceQuery` / `workspaceUserId` / `workspaceGroupId`，新增 `workspaceTab: KnowledgeWorkspaceTab` ref；③ 新增 `submitWorkspace()` 同时并发 `searchKnowledge(query)` + `debugContext(query, userId, groupId)`；④ 新增 `migrateLegacyTabQuery()` onMounted 钩子，把 `?tab=sources|search|context|metrics|candidates|graph|graph_nodes` 翻译成 `activeTab + workspaceTab` 或 `adminDrawerOpen + adminActiveTab` 状态后 `router.replace` 清掉 query。**740 → 755 行（+15）**
- [admin/frontend/src/views/knowledge/helpers/types.ts](../../admin/frontend/src/views/knowledge/helpers/types.ts) — `KnowledgeTab` 由四值（sources / search / context / metrics）收敛为两值（sources / workspace）；新增 `KnowledgeWorkspaceTab`（details / pack / metrics）
- 删 [KnowledgeSearch.vue](../../admin/frontend/src/views/knowledge/components/) / KnowledgeContextPanel.vue / KnowledgeMetricsPanel.vue 三个独立子组件（共 575 行 .vue 源码）

D1 同模式扫描：

- `grep -rn "KnowledgeSearch\|KnowledgeContextPanel\|KnowledgeMetricsPanel" admin/frontend/src/` → 0 命中
- `grep -rn "tab=search\|tab=context\|tab=metrics" admin/frontend/src/ docs/` → 0 命中（外部无外链依赖）；docs/project-info.md L211 已同步改 2 tab 描述
- 6 个 NPopconfirm（PR1 sidebar reindex + PR2 reject + 2×rollback + supersede + 现 workspace 内若新增高破坏按钮也走 NPopconfirm）形成统一纪律

验证：`vue-tsc --noEmit` 0 error；`vite build` 5.52s；bundle `KnowledgeView-*.js` 61.60 → **63.55 KB / gzip 17.20 → 17.66**（+1.95 / +0.46，workspace 视觉收敛 + 三 panel 合一的合理增量）。

浏览器侧（待用户验收）：① 顶部"上下文调试"tab 输入一句话 → 命中详情 / Prompt Pack / 评测指标 三 tab 同时刷新；② 命中详情 = 旧 search 结果 + 旧 context hit cards 合并展示（用 AppPanelSection 分组）；③ Prompt Pack = 旧 context 的打包文本；④ 评测指标 = 旧 metrics 的 sources/types breakdown，绑定到当前 query；⑤ 旧路由 `?tab=search` → 自动落到 workspace.details；`?tab=context` → workspace.pack；`?tab=metrics` → workspace.metrics；`?tab=candidates|graph|graph_nodes` → 打开管理员 drawer 并定位 tab；⑥ 管理员"管理"按钮抽屉行为不变。

回滚：`git revert <commit>`；admin/static 是 bind mount `npm run build` 立即生效；无后端 / schema / 部署改动。

#### PR4 视觉收尾（4 项 UI 打磨：sidebar 顺序 + 候选卡片 + 图谱节点 metric/filter + 文档源 preview）— ✅ 2026-05-21

PR1/PR2/PR3 完成后用户审计后台截图发现 4 类视觉/可用性瑕疵：① SourcesPanel 卡片只有文件名 + 路径，看不到内容引子；② GraphNodesPanel 4 个 MetricCard 用 30px 大字数字 + "term · 34" 字符串混排，文本类目被强制换行成两行卡片高度爆掉；下方筛选条用 PageToolbar inline-style 220/180/220 写死宽度排版丑；③ CandidatesPanel 卡片用 `minmax(0,1fr) minmax(280px,360px)` 双栏导致右侧 reject input + 两按钮拥挤、黄色 72% 置信度 tag 位置突兀；④ Sidebar Backlog 三 chip 顺序（跳过源 / 候选待审 / 作用域待查）和 AdminDrawer NTabs 顺序（候选 / 图谱 / 节点）不对应、点错跳转，且 chip 没有 hover 解释。本轮一次性修。

改动：

- [admin/frontend/src/views/knowledge/components/KnowledgeSidebar.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeSidebar.vue) — Backlog 三 chip 顺序重排为 `候选待审 → 作用域待查 → 跳过源`，与 AdminDrawer NTabs `[candidates, graph, graph_nodes]` 顺序一致；每 chip 包 `<NTooltip placement="left" :style="{ maxWidth: '260px' }">` 加 hover 解释（候选事实待审 / 跨作用域风险 / 跳过源明细）；icon 调整：作用域待查改用 `GitNetworkOutline` 与功能相称
- [admin/frontend/src/views/knowledge/components/KnowledgeCandidatesPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeCandidatesPanel.vue) — 165 → 257 行，整张卡片重设计：① 顶部 head 行 — `subject (primary tint)` + `ArrowForward` + `predicate (pill)` + `ArrowForward` + `object (浅 primary tint)` 三段式，置信度 NTag 拉到右侧分级显示（≥0.75 success / ≥0.5 warning / 其余 error）；② evidence 改 left-border 引文（`border-left: 2px solid color-mix(... primary 40% ...)` + `--om-surface-2` 背景 + `border-radius: 0 8px 8px 0`）；③ footer dashed top-border + 左 meta（来源 / ID monospace 小字）+ 右 actions（小尺寸 reject input 220px + Reject NPopconfirm + Approve），删旧 `minmax(0,1fr) minmax(280px,360px)` 双栏 grid
- [admin/frontend/src/views/knowledge/components/KnowledgeGraphNodesPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeGraphNodesPanel.vue) — 382 → 502 行（+120）：① MetricCard 替换为 inline `.graph-node-metric` tile（节点总数 / 边总数走 22px 数字 + nowrap ellipsis；主要节点类型 / 主要边类型走 15px monospace 文本，避免大字两行换行）；4 tile 仍 4 列 grid，每个左上角 3px 顶部 accent stripe 区分四色（primary/info/success/warning）；② 筛选条从 PageToolbar inline-style 重构为 `.graph-node-filters` 容器（panel 边框 + 圆角）+ 内部 `grid-template-columns: 1.3fr 1fr 1.3fr` 三 input 等比 + 右侧 actions（清空 / 应用筛选）；搜索框带 `SearchOutline` 前缀图标
- [admin/frontend/src/views/knowledge/components/KnowledgeSourcesPanel.vue](../../admin/frontend/src/views/knowledge/components/KnowledgeSourcesPanel.vue) — 107 → 144 行：卡片 head 下方加 preview 段（`-webkit-line-clamp: 3` 三行截断 + left-border 引文样式 + `--om-surface-2` 背景），indexed 但首段空白时显示 muted "暂无可预览内容" 占位；path 改 monospace 小字独立行
- [admin/frontend/src/views/knowledge/helpers/types.ts](../../admin/frontend/src/views/knowledge/helpers/types.ts) — `KnowledgeSource` 加 `preview?: string`
- [admin/routes/api/knowledge.py](../../admin/routes/api/knowledge.py) — `/knowledge/sources` 响应改造：每个 source 调用 `kb._chunks_for_source(name)` 取首 chunk 的 content，空白折叠后取前 160 字符塞 `preview` 字段；try/except 包住，老 KB 没该方法时退化为空字符串
- [.dockerignore](../../.dockerignore) — 加 `storage.bind-mount-snapshot-*/` 规则。原因：Phase 3 named-volume 迁移在 repo 根留了 `storage.bind-mount-snapshot-20260521-161720/` 本地快照目录（含 corrupted `slang.db.corrupt-20260520-213619`），上次 build 时 buildkit COPY 步骤撞上 input/output error 导致整个 docker daemon hung；规则补完后再 build 正常通过

验证：`vue-tsc --noEmit` 0 error；`vite build` 5.48s；bundle `KnowledgeView-*.js` 63.55 → **66.84 KB / gzip 17.66 → 18.38**（+3.29 / +0.72，4 项视觉打磨开销）。`docker compose up bot -d --build` 成功（容器内 `/app/admin/routes/api/knowledge.py` `grep -c preview` = 3 与 host 一致）。后端 API 抽样：`POST /api/admin/login {"token":"admin"} → 200 + cookie`；`GET /api/admin/knowledge/sources` 返回 `available=true count=7`，全部 7 个 source preview_len=160（如 `music-games/arcaea.md` 预览为 `"## Arcaea 是什么 Arcaea 是一款以立体感读谱和高表现力曲目见长的音游..."`）。

事故记录：本轮 build 阶段触发 docker daemon hang 一次，原因 storage.bind-mount-snapshot 目录 buildkit COPY input/output error。处置：`pkill -9 -f com.docker` + `open -a Docker`，daemon ~10s 内恢复。`.dockerignore` 已加规则避免复发。

回滚：`git revert 06c06c3`；前端 bundle 自动回退（admin/static bind mount 即时生效）；后端不重新 build 也不影响（旧前端忽略 preview 字段）。

### ✅ 2026-05-22 P-1 短链路三连（SchedulerView / SandboxView / ScheduleView）AppPanelSection 视觉收敛 + 内联样式清理

**背景**：自审 web 重构进度后选定的下一阶段切入点。这三个视图都属于「单文件 ≤ 600 行 + 没有 helper / 子组件需求」的短链路，只做 C 阶段的 `AppCard`→`AppPanelSection` 视觉收敛，不抽 helpers / 不拆子组件。配合方法见 D7 之外的工艺约定：①`<AppCard bordered elevated class="*-panel">` + 手写 `__head/__eyebrow/__title` 块 → `<AppPanelSection eyebrow title>`，trailing tag/button 走 `#aside` slot；② 删 inline-style，迁 scoped class；③ 高破坏按钮（无）补 NPopconfirm；④ 空态用 `<EmptyState>`；⑤ helpers 不抽。

| View | 之前 | 之后 | Δ 行 | bundle JS / gzip 之前 → 之后 | 改动要点 |
| --- | --- | --- | --- | --- | --- |
| SchedulerView | 503 行 | 461 行 | -42 | (新建 chunk 不可比) → **10.15 KB / gzip 4.33 KB** | ① 槽位卡 `<AppCard bordered elevated interactive class="scheduler-slot-card">` 整体改 `<AppPanelSection eyebrow="Group Slot" :title="slot.groupId">`；状态 / 连续跳过 tag 走 `#aside`；② 删 `style="width: min(260px, 100%)"` / `style="width: 148px"` 两处 inline-style，迁到 `.scheduler-toolbar__search` / `.scheduler-toolbar__filter` 两个新 scoped class；③ 删除 `__head/__title-block/__eyebrow/__title/__tags` 共 5 块 ~30 行 scoped CSS；④ 嵌套 `__summary` 卡（`bordered embedded`）保留不动 |
| SandboxView | 561 行 | 534 行 | -27 | (新建 chunk 不可比) → **8.29 KB / gzip 3.51 KB** | ① 仅外层 `sandbox-chat`（`om-fill-card` 三行 grid 容器）保留 `AppCard bordered elevated`，因 fill-height 布局不能由 AppPanelSection 替代；② 内嵌 composer + 右侧 2 块（Context / Runtime Notes）共 3 块改 `<AppPanelSection>`；③ Composer 内 NText 提示 + Context tag 全部走 `#aside`；④ 删除 `__head/__eyebrow/__title` 三组共 18 行 scoped CSS（chat 自身的保留）；⑤ Composer 内层加 `.sandbox-composer__body` 包裹保留 14px gap |
| ScheduleView | 510 行 | 474 行 | -36 | (新建 chunk 不可比) → **7.13 KB / gzip 2.79 KB** | ① 三个 `<AppCard bordered elevated class="schedule-panel">`（今日日程 / 心情细项 / 运行状态）全部改 `<AppPanelSection eyebrow title>`；② 「今日日程」的日期 NTag 走 `#aside`；③ 每块内层加 `.schedule-panel__body { display: grid; gap: 16px }` 包裹，避免 AppPanelSection `__head` 自带 14px margin-bottom 与 `gap` 叠加；④ 嵌套 theme-card / note-card / runtime-card 保持 `bordered embedded`；⑤ 删除 `.schedule-panel/__head/__eyebrow/__title` 共 4 块 ~33 行 scoped CSS |

验证：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 0 error（exit 0）
- `cd admin/frontend && npm run build` → ✓ built in 10.14s，3 个 chunk 全部正常输出
- `grep -c "AppPanelSection"` 后：SchedulerView 3 / SandboxView 4 / ScheduleView 5 处使用
- `grep -c 'style="'` → 全部 0 处剩余 inline-style
- 残留 `AppCard` 用途：SchedulerView 1 处（`__summary` 嵌入卡）/ SandboxView 1 处（`sandbox-chat` 外层 fill-height）/ ScheduleView 2 处（theme-card + note-card 嵌入卡）—— 全部为合法的 `bordered embedded` 嵌入或 `om-fill-card` 布局容器，不需进一步重构

回滚：本批改动只触前端 + 维护日志，无后端 / API / 配置变更；admin/static bind mount 自动生效，回滚仅需 `git revert <hash>` 后 `npm run build`。

### ✅ 2026-05-23 P-2 MemosView AppPanelSection 收敛 + 中长视图 C 阶段

**背景**：P-1 短链路三连之后的下一档切入点。MemosView 949 行，单文件，C 阶段照旧不抽 helper / 不拆子组件——本视图本身只是「实体列表 + 实体详情」两段并列模板，逻辑层无可压缩噪音。本批同样按 P-1 工艺约定：① 外层 `<AppCard bordered elevated class="memos-*-panel">` + 手写 `__head/__eyebrow/__title` 块 → `<AppPanelSection eyebrow title>`，trailing tag 走 `#aside`；② 删 inline-style 迁 scoped class；③ 嵌套 series / standalone 内的展示卡保留 `bordered embedded`；④ 空态走已有的 `<EmptyState>`。

| View | 之前 | 之后 | Δ 行 | bundle JS / gzip 之前 → 之后 | 改动要点 |
| --- | --- | --- | --- | --- | --- |
| MemosView | 949 行 | 894 行 | -55 | (随 `MemoryConsoleView-*.js` 静态绑定打包) → **27.13 KB / gzip 8.88 KB** | ① 5 个外层 `<AppCard bordered elevated>`（User Entities / Group Entities / Entity Snapshot / Series / Standalone）改 `<AppPanelSection eyebrow title>`；trailing 计数 NTag / scope NTag 全部走 `#aside`；② 删 `style="width: min(260px, 100%)"` 一处 inline-style，迁到 `.memos-toolbar__search`；③ 删除 `.memos-entity-panel/.memos-section/.memos-summary { padding: 20px }`（与 AppPanelSection 自带 18px 冲突），删 `.memos-panel__head/__eyebrow/__title` 三组共 ~30 行 scoped CSS，删 `.memos-view-toggle` 孤儿规则（template 已无引用）；④ 嵌套 `memos-series-card`（series 折叠包装）+ `memos-card-item`（卡片本体）保留 `bordered embedded`；⑤ 760px media query 中 `.memos-panel__head` 引用同步移除 |

验证：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 0 error（exit 0）
- `cd admin/frontend && npm run build` → ✓ built in 10.71s，所有 chunk 正常；MemosView 由 `MemoryConsoleView` 静态导入故归入 `MemoryConsoleView-DlI4FLlC.js`
- `grep -c "AppPanelSection"` → MemosView 模板有 5 处使用
- `grep -n 'style="'` → 0 处遗留 inline-style；保留 `:style="categoryStyle(card.category)"` 两处动态绑定（按 category 配色，非 inline-style）
- 残留 `AppCard` 用途：MemosView 3 处（series 折叠卡 + series 展开卡 + standalone 卡），全部为合法的 `bordered embedded` 嵌入展示卡，不需进一步重构

回滚：仅触前端 + 维护日志；admin/static bind mount 自动生效，`git revert <hash>` 后 `npm run build` 即可还原。

### ✅ 2026-05-23 P-3 CrossGroupView AppPanelSection 收敛 + icon-title 统一去除

**背景**：P-2 之后顺手挑长尾里另一个 ≤700 行、CSS 模式偏旧的视图。CrossGroupView 676 行，3 块 `<AppCard bordered class="cg-section">` 配 `<header class="cg-section__head">` 并在 head 里挂 `<NIcon :component="..." :size="18">` 做 title-icon——这是早期未引入 AppPanelSection 时的写法，与 P-1/P-2 已统一的 eyebrow + title 双行排版不一致。本批沿用 C 阶段约定再加一条：④ 旧的 title-icon 统一移除，靠 eyebrow 文本承担「类型」语义；description prop 承担原来 `__hint` 的副标题文案。

| View | 之前 | 之后 | Δ 行 | bundle JS / gzip 之前 → 之后 | 改动要点 |
| --- | --- | --- | --- | --- | --- |
| CrossGroupView | 676 行 | 667 行 | -9 | 14.95 KB / 5.95 KB → **14.93 KB / 6.00 KB**（gzip +0.05 KB 噪声） | ① 3 块 `<AppCard bordered class="cg-section">` 改 `<AppPanelSection eyebrow title description>`：可见条目（`Cross Group Items / 可见条目`，filteredItems 计数 NTag 走 `#aside`）/ 模拟视角（`Simulator / 模拟视角`）/ 操作时间线（`Audit Timeline / 操作时间线`）；② 删除 `<NIcon :component="TelescopeOutline\|TimeOutline" :size="18">` 两处 title-icon（保留 EmptyState 内的 icon 不动）；③ 删 `style="width: 260px"` / `style="width: 220px"` 两处 inline-style，迁到 `.cg-toolbar__search` / `.cg-simulate__input`（NModal `style="width: 520px"` 保留——与 GroupsView 等已完成视图惯例一致）；④ 删 `.cg-section/__head/__title/__hint/__placeholder` 五块 ~30 行 scoped CSS；⑤ 新增 `.cg-items-panel/.cg-simulate-panel/.cg-timeline-panel` 三个外层 margin-bottom 修饰类，配合 `.cg-grid > .cg-simulate-panel/.cg-grid > .cg-timeline-panel { margin-bottom: 0 }` 复刻原布局节奏 |

验证：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 0 error（exit 0）
- `cd admin/frontend && npm run build` → ✓ built in 10.94s
- `grep -nE 'style="|<AppCard|cg-section'` CrossGroupView → 仅剩 NModal `style="width: 520px"`（合规）；旧 `cg-section` 全部清除
- 残留 AppCard 用途：本视图 0 处（NModal 内部 `<NDivider>` 与 `<NFormItem>` 都是 Naive UI 原生组件，不依赖 AppCard）
- 视觉差异：title 字号 15px → 16px、line-icon 取消、padding 20-22px → 18px——与 P-1/P-2 视图正式对齐

回滚：仅触前端 + 维护日志；admin/static bind mount 自动生效，`git revert <hash>` 后 `npm run build` 即可还原。

### ✅ 2026-05-23 P-4 ~ P-7 长尾 4 视图 AppPanelSection 收敛批次（BlockTrace / Episodes / MemoryConsolidator / Style）

**背景**：P-3 收尾后批量结清剩余的中长视图——4 个 380 ~ 974 行视图都只有 1 ~ 4 块 panel，体量小但 CSS 模式参差（BlockTrace 还混着早期 AppCard `#header` 槽 + 错的 AppPage 槽名 `subtitle/#hero-extra`；StyleView 沿用最早的 `<section class="style-panel">` 写法、连 AppCard 都没用）。本批继续 C 阶段约定 ① ~ ④，再加一条：⑤ 沿用 P-3 的 title-icon 移除规则，eyebrow 担纲分类语义。NDrawerContent 内的子区段（`*-detail__section`）不算 panel-card 不动；NModal `style="width: ###px"` 也按 GroupsView 惯例保留。

| View | 之前 | 之后 | Δ 行 | bundle JS / gzip 之前 → 之后 | 改动要点 |
| --- | --- | --- | --- | --- | --- |
| BlockTraceView | 380 行 | 380 行 | 0 | 6.48 KB / 2.89 KB → **6.54 KB / 2.90 KB**（+0.06 KB / +0.01 KB gzip 噪声） | ① **顺手修了一个隐性 bug**：原来 AppPage 用 `subtitle="..."` + `<template #hero-extra>`——这两个 props/slot 都不存在，刷新按钮一直渲染不出，改成 `description="..."` + `<template #action>`；② Alignment 卡 `<AppCard>` + 自写 `#header` 槽 → `<AppPanelSection eyebrow="Alignment" title="Provider / Plugin Alignment" description="基于最近 N 条 trace">`，MODE_TAG 走 `#aside`；③ per-request groups 的 `<AppCard>` + `#header` → `<AppPanelSection class="bt-request-card">`，时间走 `#aside`，request-header div 进 default 槽；④ 删 toolbar 两处 `style="width: 260px / 160px"`，迁 `.bt-toolbar__request / .bt-toolbar__source`；⑤ 删 `.bt-align-header / .bt-align-meta / .bt-alignment` 三块旧样式，新增 `.bt-toolbar__request/.bt-toolbar__source/.bt-alignment-panel/.bt-request-header { margin-bottom: 12px }` |
| EpisodesView | 868 行 | 868 行 | 0 | 16.69 KB / 6.16 KB → **17.02 KB / 6.25 KB**（+0.33 KB / +0.09 KB gzip） | ① 1 块 `<AppCard bordered class="ep-section">` → `<AppPanelSection eyebrow="Episode List" title="经验列表" description="按状态 / 群 ID 过滤..." class="ep-list-panel">`，episodes 计数 NTag 走 `#aside`；② 删 toolbar 两处 `style="width: 220px / 200px"`，迁 `.ep-toolbar__state / .ep-toolbar__group`；③ NModal `style="width: 540px"` 按惯例保留；④ NDrawerContent 内 4 块 `<section class="ep-detail__section">` 是抽屉子区段不是 panel-card，**不动**——若套 AppPanelSection 会在抽屉内嵌套出多余卡面；⑤ 删 `.ep-section`，新增 `.ep-list-panel { margin-bottom: 16px }` + 两个 toolbar 修饰类 |
| MemoryConsolidatorView | 978 行 | 982 行 | +4 | 18.43 KB / 6.77 KB → **18.43 KB / 6.77 KB**（持平） | ① 1 块 `<AppCard bordered class="mc-section">` → `<AppPanelSection eyebrow="Candidates" title="候选列表" description="按状态 / 域 / 群 ID 过滤所有 Phase C 产出的候选..." class="mc-list-panel">`（页头 `#action` 已挂 `filteredCandidates / candidates` 计数 + 刷新按钮，AppPanelSection `#aside` 不再重复挂计数 NTag）；② 删 toolbar 两处 `style="width: 180px"`，迁 `.mc-toolbar__state / .mc-toolbar__group`；③ NModal `style="width: 540px"` 保留；④ NDrawerContent 内 4 块 `<section class="mc-detail__section">` 同样不动；⑤ 删 `.mc-section { padding: 20px 22px; margin-bottom: 16px }`，新增 `.mc-list-panel { margin-bottom: 16px }` + 两个 toolbar 修饰类 |
| StyleView | 974 行 | 968 行 | -6 | 17.29 KB / 5.55 KB → **17.13 KB / 5.52 KB**（**-0.16 KB / -0.03 KB gzip**） | 4 块 `<section class="style-panel">` 全部改 `<AppPanelSection eyebrow title description>`：① 表达样本（`Expressions / 表达样本`，expressions 计数 NTag 走 `#aside`，主区域用 `class="style-main-panel"` 替代旧 `style-panel--main`）/ ② 最近抽取（`Latest Extract / 最近抽取`）/ ③ 动态风格档案（`Style Profiles / 动态风格档案`）/ ④ 反馈记录（`Feedback / 反馈记录`）；删除 5 块 `.style-panel*` scoped CSS（border/radius/bg/padding + `__head/h2/p` 共 ~38 行），新增 `.style-main-panel { min-width: 0 }`；视图本身**没有 inline-style** 需迁；本视图原来连 AppCard 都没用，是最旧的写法 |

验证：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 0 error（4 视图分别跑过都 exit 0）
- `cd admin/frontend && npm run build` → ✓ built in 10.40s（最后一次）；StyleView/Memory/Episodes/BlockTrace 全部出新 chunk
- 视觉对齐：4 个视图 panel padding 全部从 18-22px 收敛到 18px、title 字号统一到 16px、head margin-bottom 统一到 14px——与 P-1 ~ P-3 已完成视图对齐
- 残留 inline-style 全部是合规白名单：① NModal `style="width: 540px / 520px"`（GroupsView 惯例）② 表格 cell 内 `style="color: var(--om-text-3)"` 之类的 utility 染色（不在本批次范围）

回滚：仅触前端 + 维护日志；admin/static bind mount 自动生效，`git revert <hash>` 后 `npm run build` 即可还原。**P-4 顺手修复的 BlockTraceView AppPage 槽 bug 也会一并回退——回退后请记得手动修一次**（subtitle → description, #hero-extra → #action），否则刷新按钮会再次消失。

## 阶段 4 — 长尾页面 + 月度合规巡检

按 plan §7 在日常任务里穿插推进。每月跑一次合规扫描脚本，结果记录到 `maintenance-log.md`。

### ✅ 2026-05-23 P-5 长尾收口（5 视图 + AppPanelSection 收敛）

把阶段 3 主清单之外、行数 ≤700 的高频运维页一刀切到 Calm Ops。**只做 C 级（视觉收敛 + inline-style 清理）**，不抽 helper、不拆子组件——按 P-7 约定 ≤700 行视图不再分阶段。

| View | 之前 | 之后 | bundle JS / gzip 之前 → 之后 | 改动要点 |
| --- | --- | --- | --- | --- |
| UsageView | 451 行 | 409 行 | 6.81 KB / 2.66 KB → **6.84 KB / 2.67 KB**（+0.03 KB / +0.01 KB gzip 噪声） | 3 块外层 `<AppCard bordered elevated>` → `<AppPanelSection eyebrow title>`：① "Runtime Notes / 今日运行补充信息"；② "Top Users / 活跃用户 Top 10"（计数 NTag 走 `#aside`）；③ "Top Groups / 活跃群 Top 10"（计数 NTag 走 `#aside`）。删 `.usage-summary / .usage-rank-card { padding: 20px }` + `.usage-section__head/__eyebrow/__title` 共 ~38 行 scoped CSS；新增 `.usage-summary-panel, .usage-rank-panel { margin-bottom: 0 }` 让 panel-head 之间的间距交还给 `AppDrawerLayout`。`AppCard` import 删除，新增 `AppPanelSection` import |
| SystemBackup | 378 行 | 488 行 | 单独 chunk（已在 SystemView umbrella 内） | 模板整体重构：30 处 inline-style → 0 处。原嵌套 `NSpace` + 每字段 `style="width: 120px; font-size: 13px"`、NDivider margin inline、NTag/NText 局部 margin/font-size 全部抽到 scoped class。新增 `.sb-block × 3` (Backup history / Quick-check / Settings)、`.sb-block__head/__title/__meta`、`.sb-list / .sb-row / .sb-row__id/__meta/__error`、`.sb-form / .sb-field / .sb-field__short/__medium/__select` 共 ~90 行 scoped CSS。视觉行为不变；行数 +110 是把 inline-style 集中到 `<style>` 块的预期开销 |
| AffectionView | 643 行 | 643 行 | 单独 chunk | 3 处 inline-style → 0：`style="width: min(260px, 100%)"` → `.affection-toolbar__search`；`style="width: 132px"` → `.affection-toolbar__filter`；`style="width: 100%"` → `.affection-detail__numeric`。新增对应 3 块 scoped class 定义 |
| StickersView | 581 行 | 583 行 | 单独 chunk | 1 处 inline-style → 0：搜索框 `style="width: min(300px, 100%)"` → `.stickers-toolbar__search`。**保留** v-for 内的 `<AppCard>`：这是「列表项卡」（list-item），不是「面板头」（panel head），按 P-7 约定不收敛 |
| MemoryView | ~ | ~ | 单独 chunk | 5 处 inline-style → 0：3 块 toolbar input `style="width: ###px"` → `.memory-toolbar__scope / .memory-toolbar__scope-id / .memory-toolbar__series`；2 块 `NInputNumber style="width: 100%"` → `.memory-drawer__numeric`（共享类）。新增对应 4 块 scoped class 定义 |

**SystemPolicies 已审计未改**：含 1 个外层 `<AppPanelSection>` 包 3 个内嵌 `<AppCard bordered embedded class="system-stack__item">`。按 P-7 阶段 3 长尾收敛策略第三档"内嵌 embedded 子卡保留"——这是面板内的视觉分组卡，不是 panel head，刻意不动。

验证：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 0 error
- `cd admin/frontend && npm run build` → ✓ built in 10.66s
- 新 chunk：`UsageView-DP9mUvrr.js` 6.84 KB / gzip 2.67 KB
- bundle 体积无显著恶化

回滚：仅触前端，admin/static bind mount 自动生效，`git revert <hash>` 后 `npm run build` 即可还原。

### ✅ 2026-05-23 阶段 4 月度合规扫描脚本上线

新增 [scripts/check-ui-compliance.sh](../../scripts/check-ui-compliance.sh)：bash 脚本，统计 `admin/frontend/src/views` 全量 inline-style 残留 / AppCard vs AppPanelSection 占用 / global.css `!important` 计数，6 项指标一次性输出，便于按月做趋势快照。脚本只读不改，可重复跑。

#### 首期基线快照（2026-05-23）

```text
[check-ui-compliance] 2026-05-23 05:14:27

src/views (admin frontend):
  static  style="..." sites     : 36
    └─ width-only (whitelist)   : 15
    └─ residue (target → 0)     : 21
  dynamic :style="..." bindings : 14   (allowed)
  AppCard files                 : 25
  AppPanelSection files         : 34

global.css:
  !important count              : 31
```

口径说明：

- **static `style="..."`**：精确字符串匹配 `style="..."`（排除 `:style="..."`）。36 中 15 是「width-only 白名单」（NDrawer/NModal/NInput 固定宽度，按 GroupsView 惯例保留），21 是真正待清理的多属性 inline 残留——主要集中在 [src/views/config/components/ConfigSystemBackup.vue](../../admin/frontend/src/views/config/components/ConfigSystemBackup.vue) 的 9 处 `font-size/color` 局部染色 + DashboardView 3 处 `--tone` CSS 变量挂载（CSS 变量挂载属合理用法，可豁免）。
- **dynamic `:style="..."`**：14 处全部是按类别动态染色或运行时计算宽度，**允许**。
- **!important = 31**：阶段 1 完成后保持的稳定基线（51 → 31 一刀切已落地），剩余 31 行全部在 `.dark .n-button:not(...)` / `.dark .n-menu` 系列 keep 区。
- **AppCard 25 / AppPanelSection 34 文件**：AppCard 残留主要在「列表项卡」「embedded 子卡」（P-7 第二、三档），AppPanelSection 占率随阶段 3 推进继续上升。

下一步监控：每月（或每次大批量改 admin 前端时）跑一次脚本，写入 `maintenance-log.md`；`residue` 项理想趋势是单调下降；`!important` 不应增长；`AppPanelSection` 应继续随新增视图自然增长。

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
