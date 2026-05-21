# Admin Web 前端重构执行方案

本文把 [admin-ui-style-guide.md](admin-ui-style-guide.md) 的规范转成可执行的工程计划：阶段、任务清单、PR 粒度、验收标准、回归策略。

范围只包含 `admin/frontend`，不涉及后端 API 变更。不涉及技术栈更换（继续用 Vue 3 + Vite + Naive UI + UnoCSS）。

## 1. 现状快照

### 1.1 规模

| 指标 | 数值 |
|------|------|
| 视图数 | 21 个 |
| Vue/TS 文件 | 53 个 |
| 源码总行数 | 26,415 行 |
| node_modules | 645 MB |
| 构建产物 | 1.6 MB |
| 已有公共组件 | 12 个（`src/components/common/`） |

### 1.2 健康度

已到位：

- token 系统完整 — [global.css](../admin/frontend/src/styles/global.css) 里已经有 `--om-bg`、`--om-surface`、`--om-text-1/2/3`、`--om-border`、`--om-shadow-*`、`--om-*-gradient`、`--om-success/warning/danger/info` 等完整变量，浅/深两套
- UnoCSS shortcuts 基础可用 — [uno.config.ts](../admin/frontend/uno.config.ts) 有 `card-border`、`auto-bg`、`text-highlight` 等
- 公共容器已有骨架 — `AppPage`、`AppCard`、`AppPanelSection`、`EmptyState`、`MetricCard`、`PageToolbar`
- AppPage 承担统一页头 — hero 区 + 标题/副标题/操作区已实现

待解决：

- 5 个视图超过 1500 行，是全部"内联样式堆砌"的重灾区：
  - [SystemView.vue](../admin/frontend/src/views/system/SystemView.vue) 3326 行
  - [SlangView.vue](../admin/frontend/src/views/slang/SlangView.vue) 3281 行
  - [ConfigView.vue](../admin/frontend/src/views/config/ConfigView.vue) 1937 行
  - [GroupsView.vue](../admin/frontend/src/views/groups/GroupsView.vue) 1833 行
  - [KnowledgeView.vue](../admin/frontend/src/views/knowledge/KnowledgeView.vue) 1766 行
- 深色模式靠 `!important` 打补丁 — [global.css:127-221](../admin/frontend/src/styles/global.css#L127-L221) 全部是 `.dark .n-xxx { --n-xxx: xxx !important }`，累计约 100 行，说明 Naive UI themeOverrides 在 [stores/app.ts](../admin/frontend/src/stores/app.ts) 里配置不完整
- 缺少 `SectionCard`、`StateBadge`、`LogPanel` 三个组件（style-guide 第 7 节列出）
- 构建产物进 git（`admin/static/assets/*.js`），每次构建都有大量 diff 噪音
- 没有 `.nvmrc` / `engines` 约束 Node 版本
- `admin/templates/` 还留着 9 个已经没用的 Jinja 模板

## 2. 目标与非目标

### 2.1 目标

1. 消除 5 个 1500+ 行视图的内联样式，拆出可复用子组件
2. 让 Naive UI themeOverrides 接管 90% 深色模式规则，`!important` 数量减少到 10 行以内
3. 每个新视图 5 分钟能按规范拼出来（PageHero → Toolbar → Section → Table/Form → Empty）
4. 构建产物体积不恶化，首屏 chunk 不超过现在的 1.1 倍

### 2.2 非目标

- 不换技术栈（Vue/Vite/Naive UI/UnoCSS 全部保留）
- 不改 API
- 不做微前端、SSR、i18n
- 不动全局路由结构
- 不一次性改完 21 个视图 — 明确分优先级，长尾穿插日常任务

## 3. 阶段 0：环境清理（约 1 天）

做在一个 PR 里，不涉及视图代码。

### 3.1 任务清单

1. **删除 [admin/templates/](../admin/templates/) 下所有 `*.html`** — `grep -r "render_template\|templates/" admin/routes/` 确认无引用后删除（实际需要由人工执行，Claude 配置已禁 rm）
2. **`.gitignore` 添加 `admin/static/assets/`** — 构建产物不再进 git
3. **`admin/static/assets/` 现有文件从 git 里移除** — `git rm -r --cached admin/static/assets/`
4. **新增 [admin/frontend/.nvmrc](../admin/frontend/.nvmrc)** — 固定 Node 版本（20 LTS）
5. **`package.json` 加 `engines.node`** — `">=20.0.0 <21"`
6. **更新 `scripts/deploy.sh`** — 部署前跑 `pnpm build` 生成产物到 `admin/static/`
7. **更新 [AGENTS.md](../AGENTS.md) 或 [CLAUDE.md](../CLAUDE.md)** — 声明"构建产物不进 git"

### 3.2 验收

- [ ] `git status` 不再因为构建产物刷屏
- [ ] `admin/templates/` 为空或不存在
- [ ] CI/本地构建产出物只在 `admin/static/` 本地存在
- [ ] `deploy.sh` 可单机跑通

### 3.3 回滚

git revert 单 PR 即可，无数据风险。

## 4. 阶段 1：基础设施固化（约 2-3 天）

目标：让所有页面都能直接用 token + Naive UI themeOverrides，不再需要私写深色模式 CSS。

### 4.1 任务清单

1. **扩展 [stores/app.ts](../admin/frontend/src/stores/app.ts) 的 themeOverrides**
   - 参照 [global.css:181-221](../admin/frontend/src/styles/global.css#L181-L221) 的 `.dark .n-card/.n-input/.n-select/.n-tag/.n-drawer/.n-modal/.n-data-table` 规则，转为 `darkThemeOverrides.Card`、`Input`、`Select`、`Tag`、`Drawer`、`Modal`、`DataTable` 字段
   - 同步浅色 themeOverrides（当前几乎为空）
2. **删除 [global.css](../admin/frontend/src/styles/global.css) 里对应的 `!important` 覆盖**，只保留：
   - `.dark .n-button:not(...)` 的基础按钮颜色（Naive 里不好用 themeOverrides 写 `:not` 选择器）
   - `.dark .n-menu` 的子项色（`NMenu` 的侧栏样式和 layout 强耦合）
   - 其余全部迁走
3. **补全 UnoCSS shortcuts** — [uno.config.ts](../admin/frontend/uno.config.ts) 加：
   ```
   'section-title', 'text-16 font-600 text-[var(--om-text-1)]'
   'section-hint',  'text-12 text-[var(--om-text-3)]'
   'metric-num',    'text-28 font-700 text-[var(--om-text-1)]'
   'chip',          'inline-flex items-center gap-4 px-8 py-2 rounded-999 text-12'
   'panel',         'rounded-12 bg-[var(--om-surface)] border card-border p-16'
   'toolbar-row',   'flex items-center justify-between gap-12 py-12'
   ```
4. **把色板 hex 固化成 UnoCSS theme colors** — 方便模板里用 `bg-success-soft` 这种类
5. **新增 [docs/admin-ui-tokens.md](admin-ui-tokens.md)** — token/shortcut 速查表，2 页以内

### 4.2 验收

- [ ] `grep -rn "!important" admin/frontend/src/styles/global.css | wc -l` ≤ 10
- [ ] 切浅/深主题时抽屉、模态、表格、标签无闪白或字色丢失
- [ ] 所有视图文件未改动，视觉回归对比无差异

### 4.3 回滚

themeOverrides 变更不影响 DOM 结构，出问题直接把 `!important` 规则粘回 global.css 即可。

## 5. 阶段 2：公共组件补齐（约 2-3 天）

补齐 style-guide 第 7 节点名但未落地的组件，且把现有组件的 API 稳定下来。

### 5.1 新增组件

| 组件 | 位置 | 职责 |
|------|------|------|
| `SectionCard.vue` | `components/common/` | 二级内容块容器：标题 + 说明 + 右侧操作 + 内容；替代各视图私写的 `n-card size="small"` |
| `StateBadge.vue` | `components/common/` | 带图标的状态徽章，props：`status: 'success' \| 'warning' \| 'error' \| 'info' \| 'default'`、`label?: string` |
| `LogPanel.vue` | `components/common/` | 终端面板外壳：等宽字体、等行高、可选暂停/清屏/自动滚动；供 LogsView、SandboxView、Slang AI review 复用 |
| `DataToolbar.vue` | `components/common/` | 数据表上方工具条：左侧筛选条件、右侧操作；替代每页自己拼的 `div.flex` |
| `FieldGroup.vue` | `components/common/` | 抽屉/表单里的字段分组，带标题 + 帮助文案，避免每页自己写 `<div class="mb-16">` |

### 5.2 稳定既有组件

- **`AppPage`** — 确认 slot 集合固定为 `header`/`title-prefix`/`title-suffix`/`action`/默认槽，TypeScript props 补全 `back`/`showHeader`/`title`/`description`/`eyebrow`
- **`MetricCard`** — 确认 props：`label`、`value`、`hint?`、`trend?: 'up' | 'down' | 'flat'`、`status?: StateBadgeStatus`、`icon?: string`。style-guide 要求的"左上标签、中间大数字、右上状态、下方辅助"全部支持
- **`EmptyState`** — 确认 props：`icon?`、`title`、`description?`、`actionLabel?`、`@action` 事件
- **`PageToolbar`** — 确认支持左右双槽和高度 52px

### 5.3 组件验收规则

每个公共组件必须满足：

- Storybook 风格的用例文件不强制，但 `docs/admin-ui-style-guide.md` 第 4 节要更新对应示例
- props 带 TypeScript 类型
- 不在组件里写 `!important`
- 支持浅/深色切换
- 不依赖全局 CSS 注入 — 组件自带 `<style scoped>`

### 5.4 不要做的事

- 不要把 Naive UI 组件二次封装（比如造一个 `AppButton` 包 `NButton`）— 直接用 NButton
- 不要把 UnoCSS shortcut 抽成组件 — token/shortcut 够用的地方就用它们

## 6. 阶段 3：高流量页面落地（约 1-2 周，可串可并）

### 6.1 顺序与理由

优先级基于"触达频率 × 现状丑陋度 × 重构收益"：

| 顺位 | 视图 | 行数 | 理由 |
|------|------|------|------|
| 1 | [DashboardView.vue](../admin/frontend/src/views/dashboard/DashboardView.vue) | 1043 | 每次登录都看；KPI 卡片规范的标杆；改完后其他页面抄 |
| 2 | [LogsView.vue](../admin/frontend/src/views/logs/LogsView.vue) | 606 | 排障高频；LogPanel 组件的首个消费者 |
| 3 | [GroupsView.vue](../admin/frontend/src/views/groups/GroupsView.vue) | 1833 | 数据表 + 抽屉的样板；改完 Style/Slang/Memory 能复制 |
| 4 | [SystemView.vue](../admin/frontend/src/views/system/SystemView.vue) | 3326 | 最大文件，先拆子组件再改样式（见 6.3） |
| 5 | [SlangView.vue](../admin/frontend/src/views/slang/SlangView.vue) | 3281 | 第二大；AI 评审 UI 是 LogPanel + SectionCard 的集大成 |
| 6 | [ConfigView.vue](../admin/frontend/src/views/config/ConfigView.vue) | 1937 | 表单重页，FieldGroup 的首个消费者 |
| 7 | [LoginView.vue](../admin/frontend/src/views/login/LoginView.vue) | 431 | 品牌感首屏；行数小但视觉权重大 |

### 6.2 每个视图的 PR 模板

一个视图改造拆成 2-3 个 PR：

**PR A：骨架迁移**（不改逻辑）

- 套 `AppPage`（如果没套）
- 第一屏内容用 `SectionCard` 或 `AppPanelSection` 包裹
- 工具栏换成 `PageToolbar` / `DataToolbar`
- 空状态换成 `EmptyState`
- 删除该视图里的内联 `style="padding:...; margin:...; font-size:..."`，改用 UnoCSS class 或 token
- 删除私写的容器 CSS，改用公共组件
- **不动** JS 逻辑、不动 API 调用、不动数据结构

验收：视觉回归（浅+深）+ 人工点一遍主要交互。

**PR B：子组件拆分**（只在 1000+ 行视图做）

- 把单文件里重复 3 次以上的块抽成 `views/xxx/components/XxxCard.vue`
- 把独立的弹层抽成 `views/xxx/components/XxxDrawer.vue`
- 拆完后主视图控制在 600 行以内
- **不动** 样式、不动 API

验收：git diff 看重命名为主、视觉零回归。

**PR C：视觉精修**（按 style-guide 第 5 节的页面级建议做）

- Dashboard：顶部 Hero、KPI 带 icon 和趋势、日志卡改监控面板观感
- Logs：左右双栏、终端字体、连接状态摘要
- Login：双层构图、品牌副标题

验收：style-guide 第 5 节对应段落的每条建议都落地。

### 6.3 SystemView / SlangView 特殊处理

这两个文件 3000+ 行，不能按"骨架迁移"一次搞完。先做**代码拆分再做视觉统一**：

1. 按业务子模块拆子组件 — SystemView 拆成 `SystemHealth`、`SystemMaintenance`、`SystemCache` 等；SlangView 拆成 `SlangOverview`、`SlangList`、`SlangReview`、`SlangDetail`
2. 每个子组件控制在 400 行以内
3. 拆完后每个子组件再按 6.2 的 PR A/C 走

### 6.4 验收模板

每个视图改造完要过以下清单（填在 PR 描述里）：

- [ ] 内联 `style="..."` 计数 ≤ 5 处（且每处有注释说明为什么不能用 class）
- [ ] 没有 `!important`
- [ ] 没有私写 `.xxx-card` / `.xxx-panel` 之类重复容器样式
- [ ] 浅/深色切换无闪烁、字色可读
- [ ] 屏幕宽 1280/1440/1920 + 900 窄屏都可用
- [ ] 主要交互人工点过（列表筛选、详情抽屉、保存表单）
- [ ] 控制台无新增 warning

## 7. 阶段 4：长尾页面渐进统一（穿插在日常任务）

剩余 14 个视图，不专门开重构周，而是**做到哪页顺手改哪页**。规则：

- 任何新功能 PR 如果改到一个视图，这个 PR 必须顺手把该视图改造到第 6.4 节验收清单合规
- 改造面积如果让 PR 超过 800 行 diff，拆成两个 PR（先 style refactor 再 feature）
- 2-3 个月内 21 个视图全部合规

### 7.1 排查脚本

可以加一个小脚本 `scripts/check-ui-compliance.sh`：

- `grep -rn 'style="' admin/frontend/src/views/ | wc -l` 输出各视图内联样式数
- `grep -rn '!important' admin/frontend/src/ | grep -v global.css | wc -l`
- 两个数随重构推进单调下降

## 8. 阶段 5：可选优化

### 8.1 从 npm 迁到 pnpm

- 动机：645 MB node_modules 压到约 200-300 MB；`pnpm-lock.yaml` 比 `package-lock.json` diff 友好
- 步骤：`pnpm import` → `rm -rf node_modules package-lock.json` → `pnpm install`（rm 步骤人工执行）
- 风险：低。pnpm 完全兼容 `package.json`
- 决策：独立 PR，不和其他阶段混

### 8.2 chunk 拆分

Vite `build.rollupOptions.output.manualChunks` 把 `naive-ui`、`vue`+`pinia`+`vue-router`、`@vicons` 拆成独立 chunk，首屏 chunk 体积可再压 30%+。

需要等阶段 3 做完，视图代码瘦身后拆 chunk 收益才显著。

### 8.3 组件库升级策略

- Naive UI 2.x 已经稳定，**不要**为了新版本主动升级
- Vite 7、TypeScript 5.8 等来了再升，按次要版本跟
- Vue 3.5 → 3.6 可升，Vue 4 来了再评估

## 9. 风险与应对

| 风险 | 应对 |
|------|------|
| 重构改坏了某个交互（尤其是抽屉/表单保存） | 每个 PR 必走第 6.4 节人工验收清单；合并前在 dev 环境点完主路径 |
| themeOverrides 改完深色模式局部丢字 | 阶段 1 改完后，逐个视图截浅/深两张图对比；保留 `!important` 兜底 1-2 周 |
| 3000+ 行视图拆子组件时 props 爆炸 | 子组件只接收必要数据 + emits，不传整个 store 引用；超过 8 个 props 的子组件重新拆 |
| 构建产物不进 git 后部署机没 Node | `scripts/deploy.sh` 里做 Node 版本检查并提示安装；长期上线用 CI 打镜像 |
| 长尾页面"顺手改"始终不落地 | 每月跑一次 7.1 的排查脚本，合规率放进 [maintenance-log.md](../maintenance-log.md) |

## 10. 进度跟踪

整个重构跟 `docs/tracking/` 下的跟踪文档风格保持一致。建议新增：

- [docs/tracking/web-refactor.md](tracking/web-refactor.md) — 跟踪各阶段任务勾选、PR 链接、验收截图入口

每完成一个阶段或一个视图改造，写一条进度到该文档；同时在 [maintenance-log.md](../maintenance-log.md) 追加日期条目。

## 11. 时间估算（单人）

| 阶段 | 人日 | 备注 |
|------|------|------|
| 阶段 0 环境清理 | 0.5-1 | 一个 PR |
| 阶段 1 基础设施 | 2-3 | 一到两个 PR |
| 阶段 2 公共组件 | 2-3 | 一个 PR（或按组件拆） |
| 阶段 3 高流量页面 | 8-12 | 每页 1-2 天，串行推进 |
| 阶段 4 长尾页面 | 穿插 | 不单独算时间，2-3 个月内自然完成 |
| 阶段 5 可选优化 | 1-2 | 看是否做 |

**最小可交付**：阶段 0 + 阶段 1 + 阶段 2 + DashboardView/LogsView/LoginView 三个视图，约 2 周可完成，即有明显视觉升级。

## 12. 开始前的前置动作

1. 本文提交到 `docs/web-refactor-plan.md`
2. 创建 [docs/tracking/web-refactor.md](tracking/web-refactor.md) 跟踪表
3. 在 [maintenance-log.md](../maintenance-log.md) 追加"启动 admin 前端重构"条目
4. 和使用方确认阶段 0 的 `admin/templates/` 删除、构建产物不进 git 两个决策
