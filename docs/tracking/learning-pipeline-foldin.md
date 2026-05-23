# 学习管道 v3 — Fold-in 追踪文档

> 上一版 v2.1 已收口 (`learning-pipeline.md` + `learning-pipeline-execution.md`)。本文档承接 v2.1 之后的「老页面回收 + 单页融合」工作，独立追踪，避免污染已关闭的 v2.1 文档。

## §0 定位

- **代号**：Learning Pipeline v3 — Fold-in
- **范围**：admin/frontend SPA 的「学习与记忆」分组
- **目标**：把 `slang / style / cross-group / episodes / memory-consolidator` 5 个老成熟页面的全部能力，**无功能损失**地折叠进 `LearningView.vue`，并把侧边栏的入口删掉
- **不做**：后端 API 重写（PR-E 单独切片，与 fold-in 解耦），表格/审核 host 重构（v2.1 已完成）

---

## §1 背景与触发

v2.1 已经把 5 个名词的「候选/审核/入库/命中/归档」5 阶段统一到 `/learning`，但有两个遗留问题：

1. **页面成熟度断层**：`SlangView` 经过多轮调教，配置抽屉、拖动队列、AI 清理 popconfirm、漂移卡、AnnotateBar 等都已经打磨过；而 `/learning` 的同名 noun 视图仍是简陋表格 + ReviewDrawer。
2. **导航繁杂**：「学习与记忆」分组里 8 个入口，slang/style/cross-group/episodes/memory-consolidator 5 个都和 `/learning` 重复，用户摇摆于「老页面（功能全）」和「新管道（视图统一）」之间。

v3 的触发点是用户原话 **「不要跳转专门旧页面，所以内容都要在新管线中整合完成。」** —— 不是把老页面藏起来当 deeplink 备份，而是把它们的所有交互**搬进**新管道，老路由变成 redirect。

### 已被否决的 v1~v4 方案

| 版本 | 思路 | 否决原因 |
|------|------|----------|
| v1 | 把老页面拆成 Section 折叠注入 | 抽象层多、维护重 |
| v2 | 主 tab + 子 tab 双层切换 | 「主 tab 候选/子 tab 候选」语义冲突 |
| v3 | Section 隐藏 embeddedMode | 复用代码但 props 爆炸 |
| v4 | Triage + Manage 两栏，Manage 走 deeplink 跳老页面 | 用户明确否决 deeplink |

v5（即本方案）：**单轴 + 三槽**。

---

## §2 设计目标 · 非目标

### 目标

- G1 主轴只剩**一个** —— 5 阶段（候选/审核/入库/命中/归档）
- G2 名词差异通过**正交三槽**（toolbar 槽 / side panel 槽 / drawer host 槽）暴露，不进入主 tab
- G3 老页面的全部交互（设置抽屉、漂移、AI 清理、批量、统计卡、抽取进度）落在槽内，**0 跳转**
- G4 老路由 `/slang /style /cross-group /episodes /memory-consolidator` 全部 redirect 到 `/learning?noun=...&stage=...`
- G5 「学习与记忆」分组退化为 3 项：`学习管道 / 知识库 / BlockTrace`

### 非目标

- N1 **不重写后端**。每个 noun 的 `/api/admin/<noun>/...` 维持原样，直到 PR-E 才考虑统一
- N2 **不动审核流程**。`LearningReviewHost` 已是 v2.1 终态
- N3 **不做新功能**。本轮只做「物理位置变更 + 视觉风格归一」

---

## §3 单轴吸收 + 三槽设计

```
┌─ Hero（学习管道总览）──────────────────────────────────────────┐
│  [一键抽取] [刷新]                                              │
└────────────────────────────────────────────────────────────────┘

┌─ StageStrip ──────────────────────────────────────────────────┐
│  候选池  待审  入库  命中  归档     ← 唯一主轴                  │
└────────────────────────────────────────────────────────────────┘

┌─ PageToolbar ─────────────────────────────────────────────────┐
│  [全部|黑话|风格|经验|记忆|事实|关系]   ← noun 选择器           │
│  群号 · 时间窗 · 排序 · ⟨NounToolbarSlot⟩ ← 槽 1               │
└────────────────────────────────────────────────────────────────┘

┌─ Snapshot + 列表 ──────────┐ ┌─ ⟨NounSidePanelSlot⟩ ──────────┐
│  阶段统计 / 命中卡 /       │ │  漂移卡 / 抽取进度 /           │
│  LearningTable             │ │  统计卡 / 阻塞列表 /           │
│                            │ │  noun 设置入口                 │
└────────────────────────────┘ └────────────────────────────────┘

⟨NounDrawerHost⟩ ← 槽 3：Settings / Detail / Create 抽屉挂载点
```

### 三槽语义

| 槽 | 位置 | 用途 | 何时显示 |
|----|------|------|----------|
| **NounToolbarSlot** | PageToolbar 右侧延伸 | 名词专属的快捷操作（AI 清理、新建条目、阈值切换等） | `activeNoun !== 'all'` |
| **NounSidePanelSlot** | 主列表右侧 / 下方 | 名词专属的统计、进度、漂移、健康度等只读卡片 | `activeNoun !== 'all'` |
| **NounDrawerHost** | 页面尾部 teleport 挂载点 | 名词专属的抽屉（设置、详情、创建） | 由槽内组件按需打开 |

**核心约束**：三槽内容**不能**再有「候选/待审/入库」这种与主轴冲突的语义切换。出现冲突时，老页面的子 tab 必须被吸收进主轴或舍弃。

### 槽组件契约（接口骨架，PR-A 落地）

```ts
// admin/frontend/src/views/learning/components/NounSlotProps.ts
export interface NounSlotContext {
  noun: LearningNounKey | 'all'
  stage: LearningStageKey
  group: string
  date: LearningDateFilter
  refresh: () => void
}
```

每个 noun 的 slot 实现就是一个 Vue SFC，接收 `NounSlotContext` 作为 prop。LearningView 只做 `<component :is="resolveSlot('toolbar', activeNoun)" :ctx="slotContext" />` 的派发。

---

## §4 noun → 槽内容对照表

| noun | NounToolbarSlot | NounSidePanelSlot | NounDrawerHost |
|------|-----------------|-------------------|-----------------|
| **slang** | AI 清理 popconfirm · 新建黑话 · 设置 · 漂移开关 | SlangSnapshotStrip · SlangStatsCards · SlangBacklogProgress · SlangExtractionProgress · SlangDriftCard | SlangSettingsDrawer · SlangCreateDrawer · SlangDetailDrawer |
| **style** | 风格批次重新计算 · 设置 | StyleStatsCards · StyleProfileSnapshot | StyleProfileDrawer（如有） |
| **episode** | 反思批次触发 · 反思配置 | EpisodeStatsCards · EpisodeRecallSnapshot | EpisodeDetailDrawer |
| **memory** (consolidator 候选) | 触发整合 · 阈值设置 | ConsolidatorPipelineCards · 候选数量曲线 | ConsolidatorDetailDrawer |
| **fact** | （v2.1 占位，无槽） | （v2.1 占位） | — |
| **graph_relation** | （v2.1 占位） | （v2.1 占位） | — |
| **cross-group** | ⚠️ 不是 noun，是 slang 的子视图，作为 SlangSidePanel 内的开关 | — | — |
| **all** | 不渲染槽 | 显示「选择具体名词查看详情」EmptyState | — |

**说明**：cross-group 不再是独立 noun，它是「黑话的可见域过滤器」。原 `/cross-group` 路由 redirect 到 `/learning?noun=slang&scope=cross`。

---

## §5 路由收敛清单（迁移表，对应 D3）

| 旧路由 | 新行为 | 处理方式 |
|--------|--------|----------|
| `/slang` | → `/learning?noun=slang` | redirect |
| `/style` | → `/learning?noun=style` | redirect |
| `/cross-group` | → `/learning?noun=slang&scope=cross` | redirect |
| `/episodes` | → `/learning?noun=episode` | redirect |
| `/memory-consolidator` | → `/learning?noun=memory` | redirect |
| `/learning` | 保留 | — |
| `/memory` | 保留（与 `noun=memory` 候选不冲突，前者是已入库浏览） | — |
| `/knowledge` | 保留 | — |
| `/block-trace` | 保留 | — |

旧组件文件命运：

- `views/slang/SlangView.vue` → 删除（其子组件迁移到 `views/learning/slots/slang/`）
- `views/slang/components/*` → 复用，移动到 `views/learning/slots/slang/components/`（git mv，保留 blame）
- `views/style/StyleView.vue` → 拆出 `slots/style/StyleSidePanel.vue`，老文件删除
- `views/episodes/EpisodesView.vue` → 同上
- `views/memory-consolidator/MemoryConsolidatorView.vue` → 同上
- `views/cross-group/CrossGroupView.vue` → 拆出 cross 过滤逻辑到 slang slot 内，老文件删除

SideMenu 「学习与记忆」分组（[admin/frontend/src/layouts/components/SideMenu.vue:46-59](admin/frontend/src/layouts/components/SideMenu.vue#L46-L59)）最终形态：

```ts
{
  type: 'group', label: '学习与记忆', key: 'learning',
  children: [
    { label: '学习管道', key: '/learning', ... },
    { label: '知识库',   key: '/knowledge', ... },
    { label: 'BlockTrace', key: '/block-trace', ... },
  ],
}
```

---

## §6 PR 切片

| PR | 范围 | 风险 | 验收 |
|----|------|------|------|
| **PR-A** 槽骨架 | 在 LearningView 引入 `NounToolbarSlot / NounSidePanelSlot / NounDrawerHost` 三个空壳组件 + `slotContext` 派发；任何 noun 都先渲染空槽（不影响现有 5 阶段表格） | 低（纯加法） | vue-tsc 0 错误，老 5 阶段交互无回归，空槽在 `noun !== 'all'` 时显示「即将到来」骨架 |
| **PR-B** slang 折入 | 把 `views/slang/components/*` git mv 到 `views/learning/slots/slang/`，实现 SlangToolbarSlot / SlangSidePanelSlot / SlangDrawerHost；`/learning?noun=slang` 全功能可用 | 中（数据流改造） | 可用功能：AI 清理 / 新建 / 设置 / 漂移 / 抽取进度 / 统计卡 / 详情抽屉 |
| **PR-C** 其他 4 noun 折入 | style / episode / memory / cross-group → slot；老视图文件删除 | 中 | `/learning?noun=style/episode/memory` 各自的统计卡 + 抽屉可用 |
| **PR-D** 路由 redirect + 菜单清理 | router/index.ts 5 条 redirect；SideMenu 收敛到 3 项 | 低 | 旧链接不 404，访问 `/slang` 自动跳 `/learning?noun=slang` |
| **PR-E** 后端列表 API 收敛（可延后） | `/api/admin/learning/items` 已经是统一入口，本 PR 是把零散 list API 标记为 deprecated 并补全字段 | 低 | 后端兼容；不阻塞前端发布 |

PR 之间的依赖：A → B → (C 与 D 并行) → E。每个 PR 单独可发布、可回滚。

---

## §7 验证矩阵

每个 PR 必须通过的检查：

| 检查项 | 命令 / 路径 |
|--------|-------------|
| TS 类型 | `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` |
| 构建 | `cd admin/frontend && npm run build` |
| 老路径 redirect | 浏览器手动访问 `/slang /style /cross-group /episodes /memory-consolidator`（PR-D 之后） |
| 主轴回归 | `/learning` 5 阶段切换、刷新、抽取按钮（每 PR 必跑） |
| noun 槽 | `/learning?noun=<n>` 工具栏槽 / 侧栏槽 / 抽屉槽各点一遍（PR-B/C 之后） |
| pyright + pytest | 后端没动，仅 PR-E 跑；其它 PR 跳过 |

D6：bind-mount，前端 PR 只需 `npm run build`，无需 `docker compose up bot --build`。

---

## §8 风险与回滚

| 风险 | 概率 | 缓解 |
|------|------|------|
| 老页面有未发现的隐藏交互（例如某个旧 prop 控制的回调） | 中 | PR-B/C 前先 `grep -rn "from '../slang'"` 找出所有外部引用 |
| Drawer teleport 在 keepAlive 路由下漏卸载 | 低 | NounDrawerHost 在 onBeforeRouteLeave 强制 close |
| redirect 丢失 query | 低 | router 的 redirect 用函数式而非字符串，透传 query |
| 用户书签/分享链接断裂 | 中 | 老路由保留 redirect，至少 1 个版本周期 |

回滚：每个 PR 都是独立 commit，`git revert <sha>` 即可。SideMenu 改动是单 commit，回滚 1 行恢复旧菜单。

---

## §9 实施日志

| 时间 | PR | 状态 | 备注 |
|------|----|----|------|
| 2026-05-23 | 文档 | ✅ 落地 | v5 plan 写入本文件 |
| 2026-05-23 | PR-A | ✅ 落地 | `305325a` 三槽骨架 + `slotContext` + `learning-body` 双栏；vue-tsc 0 errors，LearningView chunk 25.84 KB |
| 2026-05-23 | PR-B | ✅ 落地 | `5783795` slang 折入：`useSlangConsole` 抽离、SlangFoldInProvider 通过 `Teleport defer` 将 toolbar / 主表 / side 三槽内容投递进 LearningView 的 mount target；SlangView 改为消费 composable；vue-tsc 0 errors，LearningView 33.72 KB / useSlangConsole 独立 chunk 69.86 KB；stage→queueMode 映射：candidate→candidate / review→ai_rejected / approved→approved / archived→all / hits→fallback LearningTable |
| — | PR-C | ✅ 落地 | `58a17aa` style / episode / memory 折入 — 三套 `slots/<noun>/` 自包含 console（state.ts + Provider + Toolbar + Side + Main + Drawer），LearningView 派发四槽（slang/style/episode/memory）；旧 StyleView / EpisodesView / MemoryConsolidatorView 暂保留作为 PR-D redirect 来源；vue-tsc 0 errors；build OK，LearningView chunk 80.52 KB（含三套 console state） |
| — | PR-D | ✅ 落地 | `f75370f` router 5 条 redirect（`/slang /style /cross-group /episodes /memory-consolidator` → `/learning?noun=...`，透传 query；cross-group 额外注入 scope=cross）；SideMenu 「学习与记忆」分组从 8 项收敛到 3 项（学习管道 / 知识库 / BlockTrace）；删除 5 个旧 view 文件（SlangView / StyleView / EpisodesView / MemoryConsolidatorView / CrossGroupView），slang 子目录 components/composables/helpers 保留供 slots/slang 复用；vue-tsc 0 errors；build OK，旧 5 个独立 chunk 全部消失，LearningView chunk 149.28 KB（gzip 42.34 KB） |
| 2026-05-23 | LearningTable 视觉重设计 | ✅ 落地 | `dfe9d97` 把 `/learning?noun=all` 的 7 列 NDataTable 换成行卡片列表：左侧 3px 状态色侧栏（success/pending/rejected/neutral 由 `statusTone(status)` 派生）+ CSS Grid `head tail / meta tail` 双轴布局 + chip 行（群/时间/置信）+ hover affordance（border-strong + chevron 右移 2px）+ 6× NSkeleton loading + mobile 单列堆叠；emit 契约 `openDetail / reviewItem / loadMore` 不变，LearningView 零改动；vue-tsc 0 errors；build OK，LearningView chunk 149.95 KB（gzip 42.59 KB，相对 PR-D +0.67 KB 纯 CSS） |
| 2026-05-23 | LearningTable 第 2/3 次重设计 | ✅ 落地 | `f58b444` 26px 单行密集表（被否：信息密度高但无审美） → `8bbe608` Bento 4 列卡片网格（`auto-fill, minmax(300px, 1fr)`，每卡 124px 高，4 行结构 kind+title+content_full clamp 2 + 群/状态 chip + 操作按钮，左 3px stripe，hover translateX 4px，色态 chip `color-mix 14%`）—— 三次迭代均在「全部 = 跨 noun 列表」前提下调密度/卡片，仍被否 |
| 2026-05-23 | `noun=all` 仪表盘范式切换 | ✅ 落地 | 新增 `components/AllOverviewDashboard.vue`（~680 行）：**§1** Hero KPI 速览 4 tile（候选/待审/入库/命中，tone 左边 + 26px tabular-nums） + **§2** 6 个 noun 模块卡（3×2 grid，每卡含 5 阶段迷你柱状图 36px bar + 总数 + 最近样本 + 待审/已入库/暂无积压 chip + tabindex=0 可键盘点击） + **§3** 信息速递 12 行 30px 流式列表（time/kind/title/group/conf 5 列，kind 按 statusTone 着色）；emit `selectNoun / selectStage / openItem` 三路由分别接 `updateNoun / jumpToNounStage / openItemDetail`；LearningView 仅加 `isAllNoun` 闸口与 `<AllOverviewDashboard v-if="isAllNoun"/>` —— 原 `learning-snapshot` 与 `learning-body` 加 `v-if="!isAllNoun"`，fold-in 4 noun 行为零改动；LearningTable 回退至 `f58b444` 26px 单行版（仅 fact/graph_relation 占位用）；vue-tsc 0 errors；build OK，LearningView chunk **156.33 KB（gzip 44.78 KB）** |
| 2026-05-23 | 信息速递 词条式重做 | ✅ 落地 | `e546629` `AllOverviewDashboard.vue` 信息速递 §3 从「2 列 × 18 行 × 11.5px 紧凑表」改回「单列 × 12 行 × 主流字号词条卡」 |
| 2026-05-23 | 信息速递 双卡仪表盘底排 | ✅ 落地 | `AllOverviewDashboard.vue` 底排引入 `<section class="ov-bottom">` 7fr/5fr 双栏 —— 左 Live Feed + 右「群活跃榜」并列；Feed row 从 52px 双行改为 36px 单行（dot/title/meta/chip/time 5 列 inline，title `max-width: 18ch` 限宽避免空白）；新增 `groupRanking` computed 按 group_id 聚合 props.items 取 top 8（序号 1/2/3 warn/info/success 着色 + 6px 横向 bar 按 topNoun 着色 + count）；≤1100px 双栏堆叠回单列；vue-tsc 0 errors；build OK，LearningView chunk **158.67 KB（gzip 45.62 KB，相对词条版 +1.47 KB ≈ rank computed + bar 样式）** |
| 2026-05-23 | slang 槽双轴根除 | ✅ 落地 | `6275be7` 用户截图 + 原话「子tab选择黑话，目前tab栏跳转黑话逻辑是错的。你不是说不要双tab吗？」—— 诊断：stage→queueMode 映射本身正确（review→ai_rejected / approved→approved / archived→all / hits→null），违反 §3 单轴的是 `SlangSummaryBar` 三个 count 按钮 + 漂移 pill 的 `switch-queue-mode` emit，与 `SlangQueueToolbar` 内部 `.slang-control-strip__segments` 5 段 tab —— 子 tab 切换不反向同步父 stage strip，造成「stage strip 显示已批准 / 主面板已是已否决」的视觉错位。修复：`SlangQueueToolbar.vue` + `SlangSummaryBar.vue` 各加 `embedded?: boolean` prop —— ① QueueToolbar `.slang-control-strip__segments` 加 `v-if="!props.embedded"` 整段退场；② SummaryBar count 按钮 + drift pill 用 `<component :is="props.embedded ? 'span' : 'button'">` 动态切换标签，click handler 走 `onSwitchMode()` 包裹双重保护；③ `SlangMainPane.vue` 给两个子组件传 `:embedded="true"`，移除 `@switch-queue-mode` 事件桥接，从 `useSlangConsoleInject()` 解构里删 `setQueueMode`（已不再使用）。stage→queueMode 映射 0 改动；vue-tsc 0 errors；build OK，LearningView chunk **159.05 KB（gzip 45.66 KB，相对上一版 +0.38 KB ≈ embedded 条件渲染分支）** |
| 2026-05-23 | noun=memory 数据源修正 + inventory stage 默认 date=all | ✅ 落地 | 用户截图「显示入库 26 记忆，为什么记忆不显示？其他栏的记忆也有这个问题。请全面排查」—— 全面排查命中两个独立 bug。**Bug A** `noun=memory` 槽数据源错挂：StageStrip 的「记忆 26」来自 `memory_cards` 表（live container 26 active），但 `MemoryFoldInProvider.fetchCandidates()` 调用 `/api/admin/memory_consolidator/candidates`，对应 `consolidator_candidates` 表 0 行 → 主面板永远「暂无候选」。修复：`LearningView.nounTakesMain` 排除 memory，让 LearningTable 接管主面板（消费 `/api/admin/learning/items?noun=memory`，正确接 memory_cards）；`MemoryFoldInProvider` 移除 `<Teleport :to="mainPaneTarget">` 与 `MemoryMainPane` import + `showMainPane` computed —— 保留 toolbar / side / drawer 槽提供 consolidator pipeline 辅助信息；`MemorySidePanelContent` 加 `Consolidator Pipeline` header + hint，`MemoryToolbarContent` chip 行加 `consolidator 候选` label + 「全部 consolidator 域」—— 让 5 张 0 数值 MetricCard 不再被误读为 memory 本体指标。**Bug B** counts 与 items 的 date 过滤不对称：`_collect_memory_counts` 是库存快照（无 date 过滤），但 `_collect_memory_items` 带 `_date_filter("created_at", date)` —— 默认 `today` 时切到 `approved/archived` 这种**库存语义**阶段会出现「卡 26 / 列表 0」错位（同样影响 slang 504 / style 1 等所有 noun）。修复：`LearningView.selectStage` / `jumpToNounStage` 切到 `approved/archived` 时强制 `date='all'`，与既有 `hits → today` 对偶；URL 显式 `?date=today` 仍优先（用户意图）。后端 0 改动；`MemoryMainPane.vue` 留作 dead code（PR-E 阶段 fact/graph_relation 槽可复用）。vue-tsc 0 errors；build OK，LearningView chunk **157.42 KB（gzip 45.47 KB，相对上一版 −1.63 KB ≈ MemoryMainPane DataTable 死码下沉到独立 chunk）** |
| 2026-05-23 | LearningTable 行卡列表 → 卡片网格 | ✅ 落地 | 用户截图 + 原话「统一卡片格式，不要使用这样简陋的列表」—— 上一版 noun=memory 数据源修复后让 LearningTable 接管 memory 主面板，26px 单行密集表（`f58b444` 时代视觉）与同页 fold-in 槽 + AllOverviewDashboard 模块卡视觉语言冲突。重写：`LearningTable.vue` 容器改 `grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))`（与 AllOverviewDashboard `.ov-modules__grid` 同源），单卡 12/14px padding + 10px 圆角 + 3px 状态色侧栏（success/pending/rejected/neutral 由 `statusTone` 派生）+ 三段结构（head: kind chip + status 文本 / title: `-webkit-line-clamp: 2` 13.5px / foot: dashed 分隔线 + group / time / conf chip + hover 浮出「审核 / 详情」22px 按钮）；tabindex=0 + role=button + Enter/Space → click，hover translateY(-1px) + shadow-sm + border-strong；加载态从 18×26px NSkeleton 单行换成 8 张 4 段 skeleton 卡。emit 契约 `openDetail / reviewItem / loadMore` 0 改动，LearningView 零改动；mobile ≤720px 单列堆叠 + actions 常驻。vue-tsc 0 errors；build OK，LearningView chunk **157.20 KB（gzip 45.41 KB，相对上一版 −0.22 KB ≈ 行容器 CSS / 卡片样式抵消）** |
| 2026-05-23 | toolbar 跨 noun 统一 + 词条卡片视觉同源 | ✅ 落地 | 用户截图 + 原话「第一，统一管线页面全部词条卡片格式。单行使用类黑话，多行使用类风格。第二，如图，每个子页面tab栏都不同，尽量统一。将设置配置挪到总学习管道总览那里」—— 命中两个独立违和点。**Part 1 toolbar 收敛**：四槽 toolbar 长度严重不齐（slang 5 项 [刷新/抽取/AI 清池/新建/设置] / style 5 项 [scope/sort/刷新/抽取/生成档案] / episode 1 项 [刷新] / memory 6 项 [chips/刷新]），「刷新」与 LearningView header `刷新` 重复，「设置」是黑话 retention/promotion 全局配置不该埋在槽内。修复：`LearningView.vue` 在 `#action` 槽顶部加 `<span id="learning-action-extra" />` 作为跨 noun Teleport target；`SlangToolbarContent.vue` 用 `<Teleport to="#learning-action-extra" defer>` 投递「黑话设置」NButton 到 LearningView 顶栏（仅 `noun=slang` 时挂载，复用 `useSlangConsoleInject()` 的 `settingsDrawerVisible` ref，不引入额外路由）；四槽统一删除「刷新」按钮（含 import / loading destructure / 模板按钮 / 多余 watcher），交由父级负责；`EpisodeToolbarContent` 1 项刷新 → 改为 `Consolidator 周期写入；置信度 ≥ 0.6 自动晋升 candidate` 提示行（episode 槽是只读快照，无主动操作）。**Part 2 词条卡片视觉同源**：单行变体 `SlangTermList.vue` + 多行变体 `StyleMainPane.vue` 与 LearningTable 卡片视觉语言三套并存。修复：两者各加 `statusTone()` 派生（slang: approved→success / candidate→pending / expired→rejected / muted→neutral；style: approved→success / pending→pending / rejected,muted→rejected）+ `--{tone}` modifier class；CSS 改 10px 圆角 + 3px 状态色 `::before` 侧栏（top/bottom 14px gutter，opacity success/pending=1 / rejected=0.85 / neutral=0.45）+ hover `translateY(-1px)` + `var(--om-shadow-sm)` + `var(--om-border-strong)` + `color-mix(var(--om-surface-2) 35%, var(--om-surface-solid))`；slang 行 padding 10/14/10/18 同步 checkbox 负 margin -10/-5/-10/-18；style article meta 加 dashed top border。Episode `EpisodeMainPane.vue` 仍是 NDataTable（带分页/列排序/scroll-x，220px 操作列），结构性差异大，本批次不迁移，留作后续；emit 契约 / 业务 handler / queueMode 映射 / SlangTermList grid columns / Style normalizer actions / SlangTermList bulk bar 全部 0 改动。vue-tsc 0 errors；build OK，LearningView chunk **156.83 KB（gzip 45.46 KB，相对上一版 −0.37 KB ≈ 移除 4 个 RefreshOutline import + 4 段 NButton 模板抵消 Teleport target span）** |
| 2026-05-23 | LearningTable 卡片网格 → 单行紧凑行（同源黑话） | ✅ 落地 | 用户截图 + 原话「这是什么？记忆卡片格式没统一。我很无语」—— 上一版声称「单行使用类黑话」但 `LearningTable.vue`（`noun=memory` 主面板组件）仍是 3 列卡片网格，而 memory item 几乎全是 ≤ 30 字单句，每张卡 120px+ 高密度严重不足且与同页 SlangTermList 单行版视觉割裂。重写：`.lt-grid`（3 列 minmax(320px, 1fr)）→ `.lt-list`（单列 grid gap 8px）；`<article.lt-card>` 三段结构 → `<div.lt-row>` 7 列 grid `auto / 1fr / auto / auto / 50px / auto / auto`（kind chip / content / group / time / conf / status / actions），content `white-space: nowrap` + ellipsis 长内容靠 `title` 兜底；padding 10/14/10/18 与 3px `::before` 侧栏 opacity success/pending=1 / rejected=0.85 / neutral=0.45 + hover translateY(-1px) + shadow-sm 与 `SlangTermList.slang-term-row` 完全同源；conf 退化为 16px 高 info-tinted badge；actions hover 浮出（保留 22px ghost 按钮）；skeleton 4 段卡 → 单行 3 段（kind / content / time）；响应式 ≤1100px 隐藏 group / ≤720px 隐藏 time+conf+actions 强制 opacity=1（与 SlangTermList 1100/1000/640 节奏对齐）。emit 契约 `openDetail / reviewItem / loadMore` / props / `statusTone() formatConfidence() formatTime() shortGroup()` 全部 0 改动；LearningView 0 改动；多行变体（StyleMainPane）保持卡片不变。vue-tsc 0 errors；build OK，LearningView chunk **156.66 KB（gzip 45.44 KB，相对 `889eb68` 几乎不变 ≈ DOM 简化抵消 7 列 grid CSS）** |
| 2026-05-23 | stage→noun-mode 跨 4 noun 全栈映射对齐（5 处 mismatch） | ✅ 落地 | 用户截图 + 原话「待审-黑话页面怎么出现的是已否决，你还是没解决对应问题。通篇全查，别让我再审查到类似问题/pua」—— D1 同模式扫描 4 noun × 5 stage = 20 cell 对照后端 SQL，命中 5 处 mismatch：① slang `review`：`'ai_rejected'` → `'pending_human_review'`（后端 `status='approved' AND ai_reviewed AND NOT human_reviewed`）；② slang `archived`：`'all'` → `'archived'`（后端 `status IN ('muted','expired')`）；③ episode `candidate`：`'dry_run'` → `'candidate'`；④ episode `review`：`'candidate'` → `'approved'`；⑤ episode `approved`：`'approved'` → `'enabled_for_prompt'`。style/memory 直接透传 stage 字符串无翻译层，天然对齐。实施：后端 [services/slang/store.py:1839-1840](../../services/slang/store.py#L1839-L1840) 新增 `review_filter='archived_only'` 分支（`status IN ('muted','expired')`，因 admin API 单 status 无法一次表达 muted+expired）；前端 `SlangQueueMode` union 扩 `'pending_human_review'` + `'archived'`；`useSlangConsole.buildParams` 加两条翻译分支；`SlangFoldInProvider.stageToQueueMode` + `episode/state.ts.stageToEpisodeState` 映射修正。约束：非嵌入 `/slang` 路由的 5 段 SlangQueueToolbar 不纳入新模式，黑话独立路由交互不变。回归测试（D2）：`tests/test_slang_store.py.test_slang_store_archived_only_filter` 验证 muted/expired/approved 三态 → archived_only 只返回前两条。pytest 16 passed（15→16）；vue-tsc 0 errors；build OK，LearningView chunk **156.83 KB（gzip 45.47 KB，相对上一版 ≈持平）** |
| — | PR-E | ⏳ 待开始 | 后端 list API 收敛 |

每个 PR 完成时填入：commit sha、vue-tsc 输出、build 输出、回归点。

---

## §10 与 v2.1 的关系

v2.1 (`learning-pipeline.md`) 已交付的不变量，本 v3 不得破坏：

- LearningStageKey 5 值不变
- LearningNounKey 6 值不变
- `/api/admin/learning/pipeline` 与 `/api/admin/learning/items` 契约不变
- LearningReviewHost 是审核唯一入口
- StageStrip 视觉规范保留

v3 是 v2.1 的**外延扩展**：v2.1 把 5 个名词整合到一条管道，v3 把成熟页面的特殊能力**挂回**这条管道，并删掉旧入口。
