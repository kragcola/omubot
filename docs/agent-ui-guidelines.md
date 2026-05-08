# Omubot Agent 协作与 UI 工作准则

本文将外部 Skill 中真正适合 Omubot 的部分提炼为一套项目内可执行规则，服务于三类任务：

- 管理端 Web 页面重构与统一美化
- 旧 wiki / 维护日志 / 项目代码的审计与信息提取
- Agent 在现有代码库上的增量修改

本文不是通用设计教材，也不是外部 Skill 的镜像拷贝。它只保留对 Omubot 当前工作最有帮助的规则。

项目内对应 Skill：

- 路径：`.claude/skills/omubot-admin-console/`
- 显式调用：`$omubot-admin-console`
- 适用任务：管理端页面重构、旧 wiki / 代码审计、Omubot 增量修改

Codex 对应镜像：

- 仓库源包：`codex-skills/omubot-admin-console/`
- 全局安装目标：`~/.codex/skills/omubot-admin-console/`
- 仓库内安装脚本：`scripts/install-codex-skill.sh`

## 1. 采用范围

本项目建议吸收以下外部能力：

- `karpathy-guidelines`
  作用：约束 Agent 不乱假设、不乱扩写、不乱改无关代码。
- `web-design-engineer`
  作用：提升管理端页面重构时的设计判断和前端交付质量。
- `kb-retriever`
  作用：改进旧文档、wiki、日志、接口文件的分层检索方式。

暂不作为当前重点：

- `gpt-image-2`
  适合做海报、视觉概念图、登录页氛围图，不是当前管理端重构主线。
- `web-video-presentation`
  适合做录屏演示和视频化网页，不是当前后台产品工作的核心。

## 2. Agent 行为准则

### 2.1 先理解，再动手

- 不要替用户偷偷补全需求。
- 如果一个决定会影响实现方向、数据结构、页面信息架构或交互语义，应先明确假设。
- 如果代码里已有明确模式，应优先延续现有模式，不要为了“更优雅”另起一套。

### 2.2 简单优先

- 只实现当前明确需要的能力。
- 不为一次性页面抽象复杂的配置系统。
- 不为了“未来可能复用”提前拆过多层。
- 如果一个页面改造可以靠现有公共组件完成，就不要再造新框架。

### 2.3 外科式修改

- 只改与当前任务直接相关的文件。
- 不顺手重排无关样式、文案或结构。
- 不删除自己没有充分理解的旧逻辑。
- 如果发现无关问题，可以记录，但不要借题发挥扩大改动范围。

### 2.4 用可验证结果收口

每次修改都要有完成定义：

- 前端页面改造：至少通过 `vue-tsc` 和生产构建。
- 交互修复：要能说明修复前后的行为差异。
- 审计类任务：要能指出结论来自哪些文件，而不是只给印象判断。

### 2.5 自动更新维护日志

当一次任务产生了后续会话需要依赖的持久变更时，Agent 应在同一轮内同步更新 [maintenance-log.md](../maintenance-log.md)。

典型触发场景：

- 部署、运行时、配置、路由、API、存储等行为被改动
- 管理端页面阶段性里程碑已落地，值得作为交接进度记录
- 项目流程、Skill、协作规则被修改，后续 Codex / Claude 会按新规则工作

更新要求：

- 按时间倒序写在顶部附近
- 延续现有结构：标题、变更类型、内容、影响范围、交接/部署说明
- 如果本轮只是阅读、调研、答疑，没有形成持久仓库改动，可以不记日志

## 3. 管理端 UI 工作流

### 3.1 先看上下文，不从空气中设计

开始任何管理端页面改造前，先读这些现有上下文：

- 风格规范：[docs/admin-ui-style-guide.md](./admin-ui-style-guide.md)
- 全局样式与主题变量：[admin/frontend/src/styles/global.css](../admin/frontend/src/styles/global.css)
- 主题入口：[admin/frontend/src/stores/app.ts](../admin/frontend/src/stores/app.ts)
- 公共页面组件：
  - [AppPage.vue](../admin/frontend/src/components/common/AppPage.vue)
  - [AppCard.vue](../admin/frontend/src/components/common/AppCard.vue)
  - [MetricCard.vue](../admin/frontend/src/components/common/MetricCard.vue)
  - [EmptyState.vue](../admin/frontend/src/components/common/EmptyState.vue)
  - [PageToolbar.vue](../admin/frontend/src/components/common/PageToolbar.vue)

如果要改已有页面，还要先看已经完成统一风格的页面作为参照：

- [LoginView.vue](../admin/frontend/src/views/login/LoginView.vue)
- [DashboardView.vue](../admin/frontend/src/views/dashboard/DashboardView.vue)
- [SystemView.vue](../admin/frontend/src/views/system/SystemView.vue)
- [LogsView.vue](../admin/frontend/src/views/logs/LogsView.vue)
- [GroupsView.vue](../admin/frontend/src/views/groups/GroupsView.vue)
- [MemoryView.vue](../admin/frontend/src/views/memory/MemoryView.vue)
- [PluginsView.vue](../admin/frontend/src/views/plugins/PluginsView.vue)
- [KnowledgeView.vue](../admin/frontend/src/views/knowledge/KnowledgeView.vue)
- [UsageView.vue](../admin/frontend/src/views/usage/UsageView.vue)

### 3.2 设计决定要先说清

在写页面代码前，先用短文本确认本页的设计决定：

- 页面角色：总览页、列表页、详情页、编辑页，还是监控页
- 信息层级：Hero、指标卡、工具条、主内容、详情抽屉各自承担什么
- 视觉语气：保持 `Calm Ops / 雾青控制台风格`
- 组件复用：优先复用哪些公共组件

不需要写成长篇方案，但不能直接一边写代码一边临时决定信息结构。

### 3.3 管理端页面统一骨架

Omubot 管理端默认采用以下结构：

1. `AppPage` 负责统一页头、标题、描述和右侧操作区
2. `MetricCard` 负责指标摘要
3. `PageToolbar` 负责筛选和主操作
4. `AppCard` 负责主内容面板与抽屉内分组块
5. `EmptyState` 负责空数据、空结果、加载失败后的弱引导状态

常见映射规则：

- 总览指标：`MetricCard`
- 列表筛选：`PageToolbar`
- 主要表格或卡片区域：`AppCard bordered elevated`
- 卡片内二级区块：`AppCard bordered embedded`
- 搜索无结果：`EmptyState compact`

### 3.4 页面设计反模式

以下模式不应再出现在新改造页面中：

- 直接堆 Naive UI 默认卡片，不建立自己的层级
- 大量内联样式，导致间距、字号、圆角失控
- 一页内混用多套交互语义和状态色
- 只写“暂无数据”一行字，不提供解释
- 为了“好看”强上紫蓝渐变、玻璃拟态泛滥、夸张阴影
- 用 emoji 代替图标
- 编造统计数据、假 logo、假结果内容

### 3.5 视觉约束

管理端不是营销页，设计目标应是：

- 冷静
- 技术型
- 有一点陪伴感
- 克制但不单调

额外约束：

- 保持当前主色体系，不切到蓝紫 SaaS 模板风
- 优先让信息更清楚，而不是让页面更花
- 深色模式要延续现有 token，不单独补丁式临时覆盖
- 所有新页面必须同时考虑桌面和窄屏布局

### 3.6 页面完成检查表

一个页面改造完成前，至少确认：

- 是否复用了现有公共组件，而不是重复造轮子
- 是否有明确的 Hero、主内容和空状态层级
- 是否减少了旧页面的大量内联样式
- 是否保留并强化了原有业务语义，而不是只换皮
- 是否在浅色和深色模式下都可读
- 是否在窄屏下不出现明显布局塌陷

## 4. 旧文档与代码审计工作流

### 4.1 信息源优先级

做项目理解、架构审计或功能排查时，优先按这个顺序读取：

1. 总体文档
   - [docs/architecture.md](./architecture.md)
   - [docs/operations.md](./operations.md)
   - [docs/project-info.md](./project-info.md)
   - [docs/setup-guide.md](./setup-guide.md)
2. 旧 wiki
   - [docs/wiki/Home.md](./wiki/Home.md)
   - [docs/wiki/Architecture.md](./wiki/Architecture.md)
   - [docs/wiki/Configuration.md](./wiki/Configuration.md)
   - [docs/wiki/Plugins.md](./wiki/Plugins.md)
3. 接口与后端实现
   - `admin/routes/api/`
   - `services/`
   - `plugins/`
4. 前端页面与公共组件
   - `admin/frontend/src/views/`
   - `admin/frontend/src/components/common/`

### 4.2 分层检索，不整库暴读

审计资料时采用分层检索：

1. 先定位最可能相关的目录和文件
2. 再读目录级说明或汇总文档
3. 再进入具体接口、页面、插件或日志文件
4. 只读与当前问题直接相关的片段
5. 汇总结论时带上来源文件

不要一开始就把整份长文档或整批代码文件全部读完。

### 4.3 审计输出格式

审计类结果至少应包含：

- 当前结论
- 结论依据
- 仍不确定的点
- 建议继续看哪些文件

如果是问题排查，还应区分：

- 已确认的问题
- 高概率风险
- 仅为猜测的可能性

## 5. 实施建议

### 5.1 对外部 Skill 的实际采纳方式

建议按“吸收规则，不直接照搬全文”的方式使用：

- 从 `karpathy-guidelines` 吸收行为约束
- 从 `web-design-engineer` 吸收页面工作流、设计系统声明和反俗套规则
- 从 `kb-retriever` 吸收旧资料检索方式和来源意识

不建议：

- 把外部 Skill 整段复制进项目系统提示
- 在管理端页面里追求过度展示型、演示型设计
- 为了使用 Skill 而脱离当前技术栈和组件体系

### 5.2 对 Omubot 的具体落地建议

后续继续改造管理端时，默认按这个顺序推进：

1. 先统一信息结构
2. 再统一公共组件复用
3. 再处理视觉层级和细节样式
4. 最后补移动端、深色模式和状态态

如果后续需要把这套规则进一步产品化，可以在项目内继续拆成两份文档：

- 一份偏工程协作：Agent 行为与审计规则
- 一份偏设计实施：管理端 UI 组件与页面模式

当前阶段，本文已足够作为 Omubot 的精简采纳版准则。
