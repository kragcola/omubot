# Omubot Admin Web UI/UX 全量审计（2026-05-29）

> 状态：进行中
> 审计方式：`ui-ux-pro-max` 设计基线 + Omubot 现有设计规范 + `admin/frontend` / `admin/routes/api` 静态审阅
> 审计范围：管理端 Web（Vue 3 SPA）、布局、导航、公共组件、页面视图、交互结构、视觉一致性、信息架构、可访问性、实现一致性

## 1. 审计目标

- 以项目现有 `Calm Ops / 雾青控制台` 方向为主，不脱离 Omubot 已建立的后台风格
- 使用 `ui-ux-pro-max` 作为全过程分析基线，而不是仅凭主观印象评估
- 输出可持续扩写的审计文档，记录：
  - 当前优点
  - 结构性问题
  - 页面级问题
  - 组件层问题
  - API 与前端契合度问题
  - 优先级建议

## 2. 当前进度

- [x] 建立审计文档
- [x] 生成 `ui-ux-pro-max` 设计系统基线
- [x] 盘点前端信息架构与页面 inventory
- [x] 盘点公共组件、布局、主题与样式入口
- [x] 审阅关键页面实现
- [ ] 审阅相关 admin API 契合度
- [x] 输出按严重度排序的 findings
- [x] 输出分阶段改进建议

## 3. 审计日志

### 2026-05-29 轮次 1

- 已创建审计文档，后续发现将持续写入本文件
- 已确认本轮将同时采用：
  - `ui-ux-pro-max` skill 生成外部设计/UX 基线
  - `omubot-admin-console` 规则约束项目内审计口径
  - 项目本地设计文档 `docs/agent-ui-guidelines.md` 与 `docs/admin-ui-style-guide.md`

### 2026-05-29 轮次 2

- 已完成 `ui-ux-pro-max` 基线检索：
  - `--design-system`: `admin dashboard ops console technical calm data-dense vue`
  - `--domain ux`: `dashboard accessibility information hierarchy filters tables drawers charts admin`
  - `--domain chart`: `real-time operational dashboard trend comparison status timeline admin console`
  - `--domain typography`: `technical calm dashboard admin console precise companionable`
  - `--domain style`: `technical calm admin console minimal data dense`
  - `--stack vue`: `layout responsive forms tables state management navigation`
- 已完成前端 inventory 初扫：
  - `admin/frontend/src/views/**/*.vue`: 109 个 Vue 视图文件
  - `admin/frontend/src/components/common/*.vue`: 17 个公共组件
  - `admin/routes/api/*.py`: 多域 API，覆盖 dashboard / config / groups / plugins / knowledge / learning / system / logs 等
- 已初步识别以下高价值风险：
  - 页面新旧版本并存，信息架构存在“已软下线但仍可路由访问”的割裂
  - 公共骨架已成型，但全站仍残留较多内联样式和局部私有交互语义
  - 可访问性建设不均衡，重点集中在登录页和少数复杂组件，尚未形成全站统一约束
  - 页面与导航层级有一定收敛，但仍存在 route、menu、旧页面文件三套事实并行的问题

## 4. 预置结论占位

> 以下章节将在审计过程中逐步回填。

### 4.0 基线与 Inventory

#### `ui-ux-pro-max` 基线摘要

- 推荐模式：`Data-Dense + Drill-Down`
- 推荐风格：`Data-Dense Dashboard`
- 推荐重点：
  - KPI 卡
  - 数据表与筛选
  - summary -> detail drill-down
  - 轻量 hover / row highlight / loading state
  - 不要花哨装饰
  - 强调 WCAG AA 与高对比度

#### 与 Omubot 现有方向的关系

- 一致：
  - 数据密度高
  - 技术气质
  - 强调总览到细节
  - 不适合营销页化设计
- 不直接照搬：
  - `ui-ux-pro-max` 默认偏蓝色 dashboard
  - Omubot 已建立 `雾青控制台` 色系，应继续保留项目自有语气

#### 当前 inventory 快照

- 视图文件：109 个 `*.vue`
- 公共组件：17 个 `admin/frontend/src/components/common/*.vue`
- 管理 API 文件：36 个 `admin/routes/api/*.py`
- Router 中显式动态导入页面：19 个主路由页面
- 前端视图内直接调用 `/api/admin/*` 的命中：54 处

### 4.1 整体结论

当前 Omubot Admin Web 已经脱离“临时后台”的阶段，具备明确的设计语言、主题 token、统一页头/卡片/空状态骨架，并且在登录页、仪表盘、系统页、知识库、插件中心、学习管线等关键界面上，已经形成比较鲜明的 `Calm Ops / 雾青控制台` 产品感。

但从全量审计角度看，它还没有完全进入“设计系统稳定期”。主要问题不是“审美不够”，而是：

- 统一骨架已经建立，但全站实现一致性还不够
- 新旧页面/旧入口/软下线路由并存，信息架构存在历史层叠
- 页面级局部实现仍有不少“点状自定义”，削弱了可维护性和可访问性
- 管理端复杂度增长很快，但导航、层级和交互约束还没有完全跟上

如果把当前状态定位为阶段判断：

- 视觉方向：`B+`
- 公共骨架与主题能力：`A-`
- 页面一致性：`B`
- 信息架构收口度：`B-`
- 可访问性工程化程度：`C+`
- 长期可维护性：`B-`

### 4.2 Findings

以下 findings 按严重度排序，后续继续补充。

#### P1. 信息架构存在“路由仍在线、导航已隐藏、旧文件仍保留”的并行真相

- 结论：
  管理端当前不是单一信息架构，而是“现行主路径 + 软下线路径 + 历史文件残留”叠加。对用户而言，这会造成功能位置不稳定；对开发者而言，这会增加维护和统一风格时的判断成本。
- 证据：
  - 路由仍保留 `/usage`、`/schedule`、`/scheduler` 等页面入口：[admin/frontend/src/router/index.ts](/Volumes/OmubotDisk/omubot/admin/frontend/src/router/index.ts:13)
  - 侧边栏主导航并未呈现这些路由，而是把这些能力语义并入“系统”或其他新主线：[admin/frontend/src/layouts/components/SideMenu.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/layouts/components/SideMenu.vue:27)
  - `activeKey` 通过手工映射把 `/usage`、`/schedule`、`/scheduler` 强行归到 `/system`，说明 IA 已经发生迁移，但路由层未完全收口：[admin/frontend/src/layouts/components/SideMenu.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/layouts/components/SideMenu.vue:65)
  - 学习管线已启用 `v2` 视图，但旧版 `LearningView.vue` 仍留在仓库中：[admin/frontend/src/router/index.ts](/Volumes/OmubotDisk/omubot/admin/frontend/src/router/index.ts:82)
  - `groups` 目录下仍存在 `GroupsView.vue.new` 暂存文件，说明页面演进痕迹未清理：[admin/frontend/src/views/groups/GroupsView.vue.new](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/groups/GroupsView.vue.new:1)
- 影响：
  - 用户不容易建立稳定心智模型
  - 页面重构时容易误判哪个文件是“当前真相”
  - 导航、路由、文档之间更容易继续漂移

#### P1. 公共设计系统已建立，但全站仍有较多局部内联样式和页面私有语义，削弱了一致性

- 结论：
  `AppPage / AppCard / MetricCard / PageToolbar / EmptyState` 这套骨架已经是项目优势，但站内仍有至少 62 处 `style`/`:style` 使用，说明样式系统尚未完全收口到 token 和公共组件层。
- 证据：
  - 公共骨架完成度较高：[admin/frontend/src/components/common/AppPage.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/components/common/AppPage.vue:21)、[AppCard.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/components/common/AppCard.vue:1)、[MetricCard.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/components/common/MetricCard.vue:1)、[PageToolbar.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/components/common/PageToolbar.vue:1)、[EmptyState.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/components/common/EmptyState.vue:1)
  - `AppPage` 已提供统一 hero、title、description、action 和 content surface：[admin/frontend/src/components/common/AppPage.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/components/common/AppPage.vue:22)
  - 但全站 `style`/`:style` 仍有 62 处命中，分布在 `groups`、`dashboard`、`config`、`learning`、`knowledge` 等页面
  - 典型残留包括固定宽度、颜色注入、图表尺寸、Tag 颜色、Tooltip 宽度等
- 影响：
  - 页面视觉细节靠局部补丁维持
  - 深浅色一致性和后续全站调整成本变高
  - 容易出现“同类组件在不同页长得不完全一样”

#### P1. 登录页和少数关键页可访问性较好，但全站无障碍策略尚未系统化

- 结论：
  登录页是当前可访问性实现最成熟的代表，但全站整体仍偏“局部有意识，系统未成形”。
- 证据：
  - 登录页提供了 `role="alert"` 的明文传输警告、`role="status"` 的 Caps Lock 提示，以及明确表单 label：[admin/frontend/src/views/login/LoginView.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/login/LoginView.vue:207)
  - `ui-ux-pro-max` 的 UX 基线明确提示：颜色不能作为唯一信息、错误消息需要被宣告、图标按钮需要可访问名称、应考虑 skip link 和键盘导航
  - 全站可访问性相关显式标记命中仅 13 处，说明覆盖范围有限
  - `AppPage` 返回按钮是裸 `button`，但没有额外的可访问名称增强；大量 icon/button 组合未见统一 `aria-label` 约束：[admin/frontend/src/components/common/AppPage.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/components/common/AppPage.vue:39)
- 影响：
  - 键盘、读屏和低视力用户的体验稳定性不足
  - 页面越复杂，语义缺失风险越高
  - 后续如果做更强的治理台和实时控制台，这会成为明显短板

#### P2. 导航层级已收敛为三大组，但深层页面的“所在位置感”仍偏弱

- 结论：
  侧边栏主导航已经比较清楚，但对 detail-heavy 页面和 drill-down 路径来说，仍缺少更明确的层级提示机制。
- 证据：
  - 侧边栏只提供三组主导航：[admin/frontend/src/layouts/components/SideMenu.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/layouts/components/SideMenu.vue:27)
  - `ui-ux-pro-max` 基线在 drill-down analytics 模式下建议提供 breadcrumb / context preservation / clear back navigation
  - 当前 `AppPage` 主要通过 title 和可选 back 按钮表达层级，但没有全站级 breadcrumb 或 context strip：[admin/frontend/src/components/common/AppPage.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/components/common/AppPage.vue:37)
- 影响：
  - 插件详情、知识库管理抽屉、学习管线编辑等多层操作更依赖用户记忆
  - 深层操作返回和上下文保持更多靠单页内部实现

#### P2. 视图 inventory 较大，复杂能力区分布广，但“哪些是现役页、哪些是中间态/辅助页”缺少显式治理

- 结论：
  当前前端视图总量已经达到较大规模，若不进行分层治理，后续审计、重构和 onboarding 成本会持续升高。
- 证据：
  - `admin/frontend/src/views/**/*.vue` 共有 109 个文件
  - `learning` 目录下同时存在旧路径、`slots/`、`v2/` 三类结构
  - `memory` 路径中 `MemoryConsoleView.vue` 与 `MemoryView.vue` 为组合关系，说明已出现“容器页 + 旧子页”并行形态：[admin/frontend/src/views/memory/MemoryConsoleView.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/memory/MemoryConsoleView.vue:2)
- 影响：
  - 新人很难快速识别“现在该改哪里”
  - 视觉重构容易漏掉旧但仍在使用的组件
  - 产物和试验页界线不够清晰

#### P3. 页面基础骨架表现优秀，是后续全站收口的最大资产

- 结论：
  当前管理端最值得保护和放大的，是已经做成“项目级共识”的骨架和 token 层。
- 证据：
  - `app.ts` 已把浅/深色主题、语义色、阴影、边框、表格色层集中到一个统一入口：[admin/frontend/src/stores/app.ts](/Volumes/OmubotDisk/omubot/admin/frontend/src/stores/app.ts:1)
  - `global.css` 已形成较完整的 CSS 变量层，包括页面渐变、surface、text、border、shadow：[admin/frontend/src/styles/global.css](/Volumes/OmubotDisk/omubot/admin/frontend/src/styles/global.css:1)
  - 登录页是风格统一、品牌感和安全提示都较完整的正例：[admin/frontend/src/views/login/LoginView.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/login/LoginView.vue:149)
  - `App.vue` 已把主题、layout 切换、keepAlive 和过渡动画统一接线：[admin/frontend/src/App.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/App.vue:24)
- 影响：
  - 后续收口工作更适合“继续统一”和“清理分叉”，而不是推翻重做
  - 设计系统已经足以支撑下一阶段全站治理

### 4.3 页面级观察

#### 登录页

- 优点：
  - 视觉品牌感最完整
  - 安全提醒、失败节流、Caps Lock 提示比较细致
  - 左右双栏的信息组织清晰，兼顾品牌说明与登录操作
- 风险：
  - 品牌说明区使用 `aria-hidden="true"`，信息不会被读屏读取；如果这是纯装饰可接受，但其中包含实质产品说明，需要确认是否有意为之

#### 仪表盘

- 优点：
  - 已具备运营控制台气质，不再像默认模板
  - 具备 hero、状态摘要、学习收录、时间线、日志等多层内容
- 风险：
  - 页面复杂度高，局部样式注入和私有视觉模式较多，未来是最容易继续长歪的页面之一

#### 系统页

- 优点：
  - 后端能力非常强，前端已经在向“总控制台”收敛
  - provider、protocol、services health、runtime errors 都已进入一个页面域
- 风险：
  - 页面承载内容非常重，需要持续防止“所有内容平铺在一个层级”

#### 知识库 / 学习管线 / 插件中心

- 优点：
  - 这三类页面已经明显超出传统 CRUD 后台，具备产品性和控制台属性
  - 都有成为全站设计锚点的潜力
- 风险：
  - 深层操作、抽屉、状态和过滤过多，更依赖后续 breadcrumb/context strip/clear state language

### 4.4 组件级观察

#### `AppPage`

- 结论：
  是当前全站最关键的统一器，负责 hero、标题、说明、返回、内容 surface，方向正确。
- 注意点：
  - 目前主要解决视觉一致性，尚未承担 breadcrumb / skip-link / page landmark 等更强的语义能力

#### `AppCard`

- 结论：
  成功把 surface / embedded / elevated / interactive 几种容器语义压缩成少量模式，适合作为后续清理内联样式的落点。

#### `MetricCard`

- 结论：
  已经形成稳定视觉语言，适合继续扩为更多页面的总览摘要统一入口。

#### `PageToolbar`

- 结论：
  作为筛选条骨架是成功的，但全站仍有很多筛选/操作布局没有完全收敛到它。

#### `EmptyState`

- 结论：
  解决了“只写暂无数据一行字”的旧问题，是当前设计系统里完成度较高的部件。

### 4.5 信息架构与导航

#### 当前主结构

- 日常
- 学习与记忆
- 设置与维护

这是合理的一级分组，符合后台控制台的主任务流。

#### 当前问题

- 二级深层路径缺少统一 context 机制
- 软下线路由仍可访问，但未在导航上明确声明状态
- 历史页面和新主线的边界主要存在于开发者认知中，还没有完全变成产品层面的显式规则

### 4.6 可访问性与交互一致性

#### 来自 `ui-ux-pro-max` 的高优先级检查项

- 不能只靠颜色传达状态
- 图标按钮需要 accessible name
- 错误信息需要 `aria-live` / `role=alert`
- 表格需要考虑移动端横向滚动或替代呈现
- 深层导航应考虑 breadcrumb / skip link / heading hierarchy

#### 当前观察

- 登录页是正例
- 全站仍未看到统一 skip link
- heading hierarchy 存在分散实现，尚未见到项目级约束
- icon-only / custom button 的可访问名称仍需系统排查
- 实时和图表型组件后续应加入更明确的 pause / data table / legend 辅助策略

### 4.7 前后端契合度

#### 总体判断

前后端在“能力覆盖”上是匹配的，甚至前端很多复杂页面已经能充分吃到后端能力；当前主要问题不在“接口缺失”，而在：

- 同一页面承担的接口域过多，页面语义容易过重
- 兼容旧接口的降级路径比较多，说明前端同时服务于多个后端现实
- 某些页面本质上更像“小型控制台”而不是传统单页，后续需要更清晰的层级化交互

#### 代表性观察

##### Dashboard

- 后端接口本身语义清晰，聚焦总览数据与 cache pipeline：[admin/routes/api/dashboard.py](/Volumes/OmubotDisk/omubot/admin/routes/api/dashboard.py:1)
- 前端仪表盘职责总体匹配“运营总览页”
- 风险：
  - 仪表盘内部子区块越来越多，后续必须持续防止“把其他页面内容搬进首页”

##### Groups

- `groups` API 不只是列表，还承载 policy、profile、humanization、tool gating 等大量配置域：[admin/routes/api/groups.py](/Volumes/OmubotDisk/omubot/admin/routes/api/groups.py:1)
- 这与前端页面“群管理控制台”的定位相符，但也解释了它为什么复杂度持续走高
- 风险：
  - 一页过重，最容易出现信息过载、表单分段不清和移动端压力

##### Knowledge

- `knowledge` 页同时消费知识库、上下文调试、图谱关系、候选审核、节点统计等多类 API：[admin/frontend/src/views/knowledge/KnowledgeView.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/knowledge/KnowledgeView.vue:163)
- 后端也提供了大量兼容和降级路径：[admin/routes/api/knowledge.py](/Volumes/OmubotDisk/omubot/admin/routes/api/knowledge.py:1)
- 风险：
  - 页面产品性强，但也更需要清晰区分“用户工作区”和“管理员治理区”

##### Learning

- `learning_pipeline` 后端已经是聚合型 orchestration API，不是单一 CRUD：[admin/routes/api/learning_pipeline.py](/Volumes/OmubotDisk/omubot/admin/routes/api/learning_pipeline.py:1)
- 前端 `LearningViewV2` 也显式把页面拆成 `dashboard / pipeline / settings` 三视图：[admin/frontend/src/views/learning/v2/LearningViewV2.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/learning/v2/LearningViewV2.vue:1)
- 这是良好的方向，说明前后端都在向“控制台化”收敛
- 风险：
  - 自动化运行、轮询、抽屉编辑等行为分散，后续需要统一运行态反馈模式

##### Plugins

- 插件中心后端已经不仅是插件列表，还包括 meta、index、store、governance：[admin/routes/api/plugins.py](/Volumes/OmubotDisk/omubot/admin/routes/api/plugins.py:1)
- 前端 `PluginsView` 也已经是“插件中心 + 治理台”而不只是开关页
- 风险：
  - 对新用户来说功能面太广，需要更明确的模式提示和视图切换语义

##### Config / System

- `config` 和 `system` 两个域都很强，且都在接近“运维中心”：[admin/routes/api/config.py](/Volumes/OmubotDisk/omubot/admin/routes/api/config.py:1)、[admin/routes/api/system.py](/Volumes/OmubotDisk/omubot/admin/routes/api/system.py:1)
- 当前分工大体合理：
  - `config` 偏结构化配置编辑
  - `system` 偏运行态、provider、protocol、health
- 风险：
  - 对用户来说二者边界仍可能模糊，后续可通过更明确的 page copy 和 entry guidance 强化区分

### 4.8 工程实现一致性

#### 优点

- 鉴权检查集中在 `auth` store 和 router guard，方向正确：[admin/frontend/src/stores/auth.ts](/Volumes/OmubotDisk/omubot/admin/frontend/src/stores/auth.ts:1)、[admin/frontend/src/router/index.ts](/Volumes/OmubotDisk/omubot/admin/frontend/src/router/index.ts:190)
- API client 已统一处理 `401` 失效回退：[admin/frontend/src/api/client.ts](/Volumes/OmubotDisk/omubot/admin/frontend/src/api/client.ts:1)
- SSE 做成了模块级单例，避免每页重复创建连接：[admin/frontend/src/composables/useSSE.ts](/Volumes/OmubotDisk/omubot/admin/frontend/src/composables/useSSE.ts:43)
- `App.vue` 把主题、layout、登录分流、keepAlive 统一起来：[admin/frontend/src/App.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/App.vue:24)
- `reset.css` 已修正整站高度链路，是 SPA 后台的关键基础：[admin/frontend/src/styles/reset.css](/Volumes/OmubotDisk/omubot/admin/frontend/src/styles/reset.css:1)

#### 风险

##### P2. 实时与轮询策略仍较分散

- 结论：
  SSE 虽然已抽象，但很多运行态页面仍各自维护 `setInterval` / `setTimeout` / polling 节奏，导致体验和实现习惯不完全统一。
- 证据：
  - 单例 SSE 是统一的：[admin/frontend/src/composables/useSSE.ts](/Volumes/OmubotDisk/omubot/admin/frontend/src/composables/useSSE.ts:43)
  - 但 `dashboard`、`groups`、`learning`、`slang`、`playground` 等页仍有各自的 timer / polling 逻辑
- 影响：
  - 状态刷新反馈不一致
  - 更容易出现重复请求、节奏漂移和页面间行为差异

##### P2. KeepAlive 策略已接线，但页面是否适合缓存更多依赖人工 meta 配置

- 结论：
  当前 `KeepAlive` 接法合理，但是否缓存完全依赖 route meta，人为维护成本较高。
- 证据：
  - `App.vue` 中通过 `curRoute.meta.keepAlive` 控制缓存：[admin/frontend/src/App.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/App.vue:38)
  - Router 中多个复杂页面都被标记为 `keepAlive: true`：[admin/frontend/src/router/index.ts](/Volumes/OmubotDisk/omubot/admin/frontend/src/router/index.ts:7)
- 影响：
  - 复杂页如果状态过重，缓存体验和刷新语义可能逐步变得不可预测

### 4.9 响应式与布局策略

#### 总体判断

响应式不是当前后台的短板。代码层大量存在 `minmax(0, 1fr)`、`min-width: 0`、`flex-wrap` 和移动端断点，说明团队对后台窄屏问题是有意识的。

#### 优点

- `AppPage`、`PageToolbar`、抽屉 header、关键页面都提供了明确断点策略
- 多数重页面都用了 grid + breakpoint 收缩，而不是只靠 Naive UI 默认自适应
- 表格型或标签型区域中，已经能看到 `overflow-x` 和横向滚动处理，例如学习管线切换器等

#### 风险

##### P2. 响应式策略是“页面自己会写”，还不是“系统自动保证”

- 结论：
  当前响应式质量依赖每个页面作者自己写布局规则，而不是由公共布局模式兜底。
- 证据：
  - 代码中存在大量页面私有 `@media` 与 grid 断点
  - 说明页面级适配做了很多，但统一布局抽象还不够强
- 影响：
  - 个别新页面如果没沿用现有范式，更容易再次出现断点塌陷

### 4.10 状态管理与交互模式

#### 总体判断

全站全局状态并不混乱，Pinia 使用克制，说明没有掉进“所有状态都上全局”的陷阱。当前主要问题是局部工作流页状态量很大，更需要模块内结构治理。

#### 观察

- `app` / `auth` 这类真正的全局状态已经被正确收敛到 store
- 复杂页面更多采用页面内 `ref/computed`，这对后台控制台是可接受的
- 但 `knowledge`、`groups`、`config`、`plugins`、`learning` 这类页面的单文件状态规模已经很高，后续更适合继续拆 composable 或子视图容器

### 4.11 页面优先级矩阵

#### P0：结构治理先行

- `groups`
  - 原因：页面最重、配置域最杂、抽屉和表单分层压力最大
  - 代表证据：[admin/frontend/src/views/groups/GroupsView.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/groups/GroupsView.vue:720)
- `config`
  - 原因：信息密度极高，承担结构化配置编辑、预览、审计、备份等多职责
- `dashboard`
  - 原因：首页最容易继续膨胀，必须守住“总览页”边界

#### P1：控制台化深化

- `knowledge`
  - 原因：用户工作区与治理工作区并存，最需要层级化和模式清晰化
  - 代表证据：[admin/frontend/src/views/knowledge/KnowledgeView.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/knowledge/KnowledgeView.vue:160)
- `plugins`
  - 原因：插件中心已是多模式产品页，需要更强的 mode language
  - 代表证据：[admin/frontend/src/views/plugins/PluginsView.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/plugins/PluginsView.vue:352)
- `system`
  - 原因：运行态信息很强，但需要继续守住信息层级
  - 代表证据：[admin/frontend/src/views/system/SystemView.vue](/Volumes/OmubotDisk/omubot/admin/frontend/src/views/system/SystemView.vue:148)
- `learning`
  - 原因：v2 已经成型，适合继续作为“新范式示范区”

#### P2：后续整理

- `usage`
- `schedule`
- `scheduler`
- `sandbox`
- `replay`
- `playground`

这些页面更多是体系收口之后跟进统一，而不是先动它们。

### 4.12 分期整改路线图

#### Phase A：真相收口

- 清理 `GroupsView.vue.new`
- 明确 `LearningView.vue` 与 `LearningViewV2.vue` 的保留策略
- 为软下线页面建立统一声明：
  - 已隐藏但可直达
  - 兼容入口
  - 待删除
- 对侧边栏、router、文档做一轮 IA 对齐

#### Phase B：设计系统强化

- 把高频内联样式替换为：
  - 公共组件 prop
  - token class
  - 小范围语义类
- 为以下模式建立统一方案：
  - 详情页 context strip
  - 抽屉 header / footer
  - 表格上方 summary 区
  - 状态带 / 风险带 / 运行态提示条

#### Phase C：可访问性与行为统一

- 建立 admin 前端最小 a11y checklist
- 统一 icon-only button 的 `aria-label`
- 为全站引入 skip link / page landmark 方案
- 为实时图表与流式区域补：
  - pause / resume
  - legend
  - data-table fallback

#### Phase D：运行态交互治理

- 把 polling / timer / SSE 更新策略形成统一约定
- 为 keepAlive 页面建立缓存准则
- 为复杂控制台页沉淀 composable 拆分规则

### 4.13 文档验收标准

本审计文档达到“可验收”应满足：

- 能说明当前 Admin Web 的真实状态，不是泛泛美术点评
- 能区分：
  - 视觉问题
  - 信息架构问题
  - 工程实现问题
  - 前后端契合问题
- 能指出优先处理顺序
- 能为后续重构提供明确入口，而不是只留下抽象建议

基于当前内容，这份文档已经满足“第一版可验收”的要求；后续若继续扩充，最值得加深的是：

- 页面逐页 checklist
- 组件复用覆盖率
- 图标按钮与 heading hierarchy 的专项清单
- 移动端和键盘导航的实测结果

### 4.14 最终结论

Omubot Admin Web 当前最大的价值，不是“已经很完整”，而是“已经有了一套足够清晰的方向，可以继续治理而不是推翻重做”。

从 `ui-ux-pro-max` 的外部基线看，它符合数据密集、drill-down、控制台型产品的核心特征；从项目自身的 `Calm Ops / 雾青控制台` 规范看，它也已经建立了有辨识度的本地设计语言。当前阶段真正需要的不是换风格，而是：

- 收口历史分叉
- 强化系统一致性
- 把局部优秀实现升格为全站约束

如果后续按本审计建议推进，这套后台有机会从“做得不错的项目后台”进一步升级成“结构清晰、产品感成熟、可长期演进的控制台系统”。

### 4.15 后续建议

#### 第一阶段：治理收口

- 清理 `.vue.new`、旧版 view、实验页和软下线页面的状态定义
- 建立“现役页 / 兼容页 / playground / 历史残留”的目录或文档标记
- 对导航、路由、文档做一次真相对齐

#### 第二阶段：设计系统加固

- 把高频内联样式收口到 token、公共组件或小型语义类
- 为表格、抽屉、详情页、状态条建立更清晰的复用模式
- 给深层详情页加 breadcrumb 或 context strip 方案

#### 第三阶段：可访问性与交互一致性治理

- 建立 admin 前端最小 a11y checklist
- 对 icon button / custom tab / alert / status / heading hierarchy 做专项审计
- 为实时图表和 streaming 区块增加 pause、legend、fallback 语义

#### 第四阶段：页面优先级建议

- 优先收口：`groups`、`config`、`dashboard`
- 次优先：`knowledge`、`plugins`、`learning`
- 低优先：`playground`、旧兼容页、软下线页的整理
