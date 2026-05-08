# Admin Web UI 风格规范

本文针对 `admin/frontend` 当前管理端 SPA 的样式现状，给出一套统一的视觉方向、组件规则和页面美化建议。目标不是“单页好看”，而是让后续所有页面都长在同一套设计语言里。

## 1. 现状判断

当前管理端已经有不错的基础骨架：

- 统一的侧栏 + 顶栏 + 内容区结构，见 [admin/frontend/src/layouts/normal/index.vue](../admin/frontend/src/layouts/normal/index.vue)
- 基础页面容器 `AppPage` / `AppCard` 已经存在，见 [admin/frontend/src/components/common/AppPage.vue](../admin/frontend/src/components/common/AppPage.vue) 和 [admin/frontend/src/components/common/AppCard.vue](../admin/frontend/src/components/common/AppCard.vue)
- 已经有 UnoCSS shortcut 和 Naive UI 主题入口，见 [admin/frontend/uno.config.ts](../admin/frontend/uno.config.ts) 和 [admin/frontend/src/stores/app.ts](../admin/frontend/src/stores/app.ts)

但当前 UI 还存在明显的不统一：

- 颜色系统过薄。除了主色 `#316C72` 外，缺少明确的背景层、边框层、强调层、状态层，导致页面主要依赖 Naive UI 默认视觉。
- 页面大量使用内联样式，间距、字号、圆角、容器高度不一致，代表文件包括：
  - [DashboardView.vue](../admin/frontend/src/views/dashboard/DashboardView.vue)
  - [GroupsView.vue](../admin/frontend/src/views/groups/GroupsView.vue)
  - [LogsView.vue](../admin/frontend/src/views/logs/LogsView.vue)
  - [SandboxView.vue](../admin/frontend/src/views/sandbox/SandboxView.vue)
  - [MemoryView.vue](../admin/frontend/src/views/memory/MemoryView.vue)
- 信息层级不够明确。卡片、筛选条、数据表、空状态、详情抽屉在很多页面里都“长得差不多”，用户很难一眼分清主次。
- 深色模式主要靠补丁式覆盖，见 [global.css](../admin/frontend/src/styles/global.css)。能用，但没有形成完整的浅/深色双主题体系。
- 品牌感弱。登录页、侧栏 logo、仪表盘都更像默认后台，而不是“Omubot 控制台”。

## 2. 统一风格定义

### 风格关键词

统一采用：

- 冷静
- 技术型
- 有一点陪伴感
- 不花哨，但不死板

推荐风格命名：

- `Calm Ops`
- 中文可称为“雾青控制台风格”

它应当表达出：

- 这是一个长期运行中的 Bot 控制台
- 它偏工具型，但不是生硬的运维面板
- 它比默认企业后台更柔和，有自己的角色气质

### 主视觉方向

保留当前主色 `#316C72`，向“雾青 + 暖灰”发展，不要改成常见的蓝紫系。

建议色板：

- 主色 `#316C72`
- 主色 hover `#3C7B82`
- 主色 active `#274E53`
- 强调浅底 `#E6F1F2`
- 页面背景 `#EEF2F4`
- 卡片背景 `#FFFFFF`
- 一级文字 `#1F2A30`
- 二级文字 `#607078`
- 分割线 `#D9E1E5`
- 成功 `#2E8F6B`
- 警告 `#C58A2B`
- 危险 `#B84C5C`
- 信息 `#4D7892`

深色模式不要纯黑，建议：

- 页面背景 `#10171A`
- 面板背景 `#162025`
- 卡片背景 `#1A262C`
- 高亮底 `#22323A`
- 一级文字 `#E7F0F2`
- 二级文字 `#9CB0B8`
- 分割线 `#2A3940`

### 字体与字号

当前字体使用系统字体，能用，但建议明确层级，不要继续依赖默认字号。

规范：

- 页面标题 `24/600`
- 区块标题 `18/600`
- 卡片标题 `16/600`
- 正文 `14/400`
- 辅助文字 `12/400`
- 数据大数字 `28/700`
- 等宽内容统一使用日志/ID/命令场景

### 圆角、阴影、边框

建议统一成这一套：

- 页面大卡片圆角 `16px`
- 普通卡片/表单/抽屉内容块圆角 `12px`
- 标签/小块圆角 `999px` 或 `8px`
- 一级卡片阴影轻，悬浮时再加深
- 边框统一 1px，不要粗细混用

阴影建议：

- 默认卡片：`0 8px 24px rgba(23, 42, 48, 0.06)`
- hover 卡片：`0 12px 32px rgba(23, 42, 48, 0.10)`

### 间距体系

目前很多页面 `8/12/16/24` 都在混用，但没有形成规则。建议以后只用以下节奏：

- `4` 微间距
- `8` 标签/小控件
- `12` 表单项内部
- `16` 区块内部常规间距
- `24` 卡片内容主间距
- `32` 页面模块间距

## 3. 页面骨架规范

### 总体布局

统一为：

1. 左侧导航
2. 顶部工具条
3. 页面标题区
4. 页面内容区

但页面标题区不要再只是“一条细色条 + 标题”，建议升级为：

- 标题
- 一行页面说明
- 右侧操作区

`AppPage` 应承担统一标题区样式，不允许每页自己重新造页头。

### 页面背景

当前 [AppPage.vue](../admin/frontend/src/components/common/AppPage.vue) 使用纯色背景，建议升级为“浅灰底 + 微弱渐变”：

- 浅色模式：从 `#EEF2F4` 到 `#F7FAFB`
- 深色模式：从 `#10171A` 到 `#132027`

这样整个后台会更有层次，但不会喧宾夺主。

### 内容容器

内容区只保留两种容器：

- `Surface Card`
- `Embedded Panel`

规则：

- 页面主内容用 `Surface Card`
- 卡片内二级块用 `Embedded Panel`
- 不要直接在空白背景上堆零散按钮和文字

## 4. 组件统一规范

### KPI 卡片

适用页面：

- 仪表盘
- 系统
- 用量统计
- 知识库

统一规则：

- 左上是标签
- 中间是大数字
- 右上是状态 icon 或 tag
- 下方可选辅助说明

不要只放一个 `NStatistic` 就结束。KPI 卡片应该更有“总览”感。

### 筛选条

适用页面：

- 记忆管理
- 群管理
- 日志
- 插件

统一规则：

- 使用一个独立的筛选工具条容器
- 左侧放筛选条件
- 右侧放操作按钮
- 高度统一
- 所有输入控件优先使用中号，不建议过多 `size="small"`

当前很多页面的筛选栏偏碎，尤其 [MemoryView.vue](../admin/frontend/src/views/memory/MemoryView.vue) 和 [LogsView.vue](../admin/frontend/src/views/logs/LogsView.vue)。

### 数据表

统一规则：

- 表头背景与页面背景分层明显
- 行 hover 有轻微高亮
- 重要列使用标签/徽章，不要纯文本堆砌
- 操作列按钮尽量图标化或弱化
- 表格上方必须有总数、筛选或说明，不要裸表格直接出现

### 抽屉

当前抽屉可用，但内容普遍太“直给”。建议统一为：

- 顶部：标题 + 副标题
- 中部：2 到 4 个分组块
- 底部：固定操作栏

尤其是：

- [GroupsView.vue](../admin/frontend/src/views/groups/GroupsView.vue)
- [MemoryView.vue](../admin/frontend/src/views/memory/MemoryView.vue)
- [PluginsView.vue](../admin/frontend/src/views/plugins/PluginsView.vue)

### Tag / 状态色

统一语义，不允许每页自由发挥：

- `success` 表示运行正常、启用、已连接
- `warning` 表示待关注、受限、暂停
- `error` 表示异常、关闭、危险动作
- `info` 表示说明性分类
- `default` 只用于中性信息

### 空状态

当前空状态多是“暂无数据”一行字，太弱。统一改为：

- 图标
- 标题
- 一句解释
- 可选引导按钮

适用所有列表页、日志页、知识库搜索结果、沙盒会话空白态。

### 动效

保留当前页面切换动画，但统一节奏：

- 卡片 hover：`160ms`
- 抽屉/弹层：`220ms`
- 页面切换：`240ms`

不要新增夸张弹跳或大位移动画。

## 5. 页面级美化建议

### 登录页

当前 [LoginView.vue](../admin/frontend/src/views/login/LoginView.vue) 太基础，像临时表单。

建议：

- 改为双层构图：中间登录卡片 + 背后大面积雾青渐变
- Logo 不要只是一枚 `O`，至少加一句产品副标题
- 登录卡片上方展示 “Omubot 控制台 / Bot Runtime Console”
- 输入框、按钮、说明文字形成完整节奏

目标效果：

- 一打开就有“这是一个正式产品入口”的感觉

### 仪表盘

当前 [DashboardView.vue](../admin/frontend/src/views/dashboard/DashboardView.vue) 信息价值有，但视觉偏素。

建议：

- 顶部增加一块“运行总览 Hero”
- 4 个指标卡统一加 icon、趋势说明或状态脚注
- 实时日志卡做成“监控面板”风格，而不是普通卡片
- 系统状态卡补充更多摘要项，避免右半边太空

### 群管理

当前 [GroupsView.vue](../admin/frontend/src/views/groups/GroupsView.vue) 功能能用，但视觉还是典型数据表页。

建议：

- 表格上方加摘要卡：群数量、启用 `@` 回复的群数、黑名单用户总数
- 详情抽屉改成三段式：
  - 概览
  - 实时状态
  - 最近消息时间线
- 最近消息不要只是灰块列表，建议做成简版聊天时间线

### 记忆管理

当前 [MemoryView.vue](../admin/frontend/src/views/memory/MemoryView.vue) 已经有结构，但比较技术化。

建议：

- 筛选条独立成工具栏
- 系列头做成可折叠分组行时，强化层级样式
- “新建卡片”抽屉加入字段分组和帮助文案
- 分类 tag 的颜色语义固定，不要只区分一两个类别

### 日志页

当前 [LogsView.vue](../admin/frontend/src/views/logs/LogsView.vue) 很接近运维工具，但还没形成完整风格。

建议：

- 让实时流和文件查看形成左右双栏中的主次关系
- 日志区做成终端面板观感，统一等宽字体、行高、状态色
- 顶部增加连接状态、刷新状态、实时条数摘要
- “暂停/清屏/筛选”做成工具条按钮组

### 沙盒页

当前 [SandboxView.vue](../admin/frontend/src/views/sandbox/SandboxView.vue) 是功能型原型感最强的一页。

建议：

- 聊天气泡更圆润，区分 bot/user 角色
- 聊天区加顶部标题和会话说明
- 右侧设置面板放入卡片，不要裸露在页面上
- 默认空状态加入引导说明

### 插件页

当前 [PluginsView.vue](../admin/frontend/src/views/plugins/PluginsView.vue) 已经可用，但卡片比较扁平。

建议：

- 插件卡片加入摘要：版本、状态、指令数、工具数
- 详情抽屉中的指令块和工具块做成统一的信息卡
- 插件状态与优先级用更明确的视觉层级区分

## 6. 强制统一规则

以后新增页面或重构页面，统一遵守以下规则：

### 视觉规则

- 只允许使用这一套主色体系，不再引入新的品牌主色
- 不允许页面私自定义大面积纯黑、纯蓝、纯紫背景
- 圆角统一用 `8 / 12 / 16`
- 间距统一用 `8 / 12 / 16 / 24 / 32`

### 组件规则

- 页面必须通过 `AppPage` 承载
- 普通内容块必须通过统一容器组件承载
- 筛选栏必须有统一外观
- 数据表必须有统一的上方工具区或摘要区
- 抽屉必须包含标题区、内容区、底部操作区

### 代码规则

- 尽量消除大量 `style=""` 内联样式
- 将通用视觉样式沉淀到：
  - `uno.config.ts`
  - `global.css`
  - 公共组件
- 不允许每个页面自己定义一套按钮、卡片、空状态

## 7. 建议新增的公共组件

为了把风格真正统一下来，建议补这几个组件：

- `PageHero.vue`
- `PageToolbar.vue`
- `MetricCard.vue`
- `SectionCard.vue`
- `EmptyState.vue`
- `StateBadge.vue`
- `LogPanel.vue`

这样后面每页只要拼装，不要继续各写各的容器和间距。

## 8. 首批落地优先级

建议按这个顺序做：

1. 抽离全局 token
   - 整理 `uno.config.ts`
   - 整理 `global.css`
   - 扩充 `stores/app.ts` 的 Naive UI theme overrides
2. 重做基础公共块
   - `AppPage`
   - `AppCard`
   - 新增 `PageToolbar` / `MetricCard` / `EmptyState`
3. 优先改最能体现气质的页面
   - 登录页
   - 仪表盘
   - 系统页
   - 日志页
4. 再统一数据页
   - 群管理
   - 记忆管理
   - 插件
   - 知识库

## 9. 额外注意

`admin/static` 里出现了一批 `._*` 文件，这是 macOS AppleDouble 产物，不属于前端视觉问题，但建议清理掉，避免静态目录污染。

