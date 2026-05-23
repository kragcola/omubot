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
| — | PR-C | ⏳ 待提交 | style / episode / memory 折入 — 三套 `slots/<noun>/` 自包含 console（state.ts + Provider + Toolbar + Side + Main + Drawer），LearningView 派发四槽（slang/style/episode/memory）；旧 StyleView / EpisodesView / MemoryConsolidatorView 暂保留作为 PR-D redirect 来源；vue-tsc 0 errors；build OK，LearningView chunk 80.52 KB（含三套 console state） |
| — | PR-D | ⏳ 待提交 | router 5 条 redirect（`/slang /style /cross-group /episodes /memory-consolidator` → `/learning?noun=...`，透传 query）；SideMenu 「学习与记忆」分组从 8 项收敛到 3 项（学习管道 / 知识库 / BlockTrace）；删除 5 个旧 view 文件（SlangView / StyleView / EpisodesView / MemoryConsolidatorView / CrossGroupView），slang 子目录 components/composables/helpers 保留供 slots/slang 复用；vue-tsc 0 errors；build OK |
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
