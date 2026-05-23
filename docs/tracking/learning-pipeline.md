# 学习管道总览页（/learning）方案

> 状态：**v2.1（执行风险审查后修订）** · 最后更新：2026-05-23 · 作者：Codex（与用户协同）
> v1 → v2：用户决议 Q1~Q6（C/A/B/A/A/A）+ gpt + deepseek 双独立审计 4 阻塞项 / 6 条件项已全部落地。Q4 由 A 细化为 **A2（按 `memory_cards.status` 做只读视角，不改 CardStore）**。修订条目见 §19。
> v2 → v2.1：第三轮代码侧执行风险审查发现 5 项（1 阻塞 / 2 高 / 2 中），全部经 grep/Read 核实属实并落地。修订条目见 §22；PR 顺序见 §23；验证矩阵增量见 §24。**§22 与 §19 冲突时以 §22 为准**。
> 关联：[web-refactor.md](./web-refactor.md) · [agent-ui-guidelines.md](../agent-ui-guidelines.md) · [admin-ui-style-guide.md](../admin-ui-style-guide.md)

---

## 0. 文档定位

本文是 `/learning` 页（学习管道总览）的**设计与实施方案**，供审计后再决定是否进入 PR 阶段。
不是实施记录、不是已落地项；任何「待办」「TODO」语义在此文档结束前不进 maintenance-log。

审计通过的标志：用户在末尾 §13 各开放问题处给出明确回答，并同意阶段切片顺序。

---

## 1. 背景

### 1.1 现状

`admin/frontend` SideMenu「日常」分组共 12 项，其中与「学习与记忆」直接相关的有 5 项：

| 路由 | 中文 | noun 维度 | 当前主要数据 |
| --- | --- | --- | --- |
| `/slang` | 群内黑话 | slang | candidate / approved / muted / expired + AI 子态 |
| `/style` | 表达方式 | style | pending / approved / rejected / muted（条目）+ profile |
| `/episodes` | 经验反思 | episode | dry_run / candidate / approved / enabled_for_prompt / disabled |
| `/memory-consolidator` | 记忆候选 | 5 domain（fact/slang/style/episode/graph_relation） | dry_run / queued / approved / rejected |
| `/memory` | 记忆 | memory file | `.md` 文件系统（无候选态） |

5 个页面**共享同一个心智模型**——抽取 → 候选 → 审核 → 入库 → 命中观测 → 失效归档——但用户每次都得切 5 个页面才知道「整个学习管道现在在哪一步」。

### 1.2 用户问题（2026-05-23 对话）

> 整个学习与记忆是不是线性或能够一定程度折返的线性工作流？参考一下达芬奇软件那种工作流可视化设计会不会对用户友好一些。

确认结论：**是线性 + 折返工作流**，DaVinci Resolve 的 Page Tab（Media → Edit → Color → Fairlight → Deliver）形态适用，但不能照搬。

### 1.3 数据流（语义版）

```
群消息
  └─→ 抽取（plugin / consolidator）
        └─→ 候选池（pending / candidate / queued / dry_run）
              ├─→ AI 初审（slang 专属，可选）
              └─→ 人工审核
                    ├─→ 入库（approved / enabled_for_prompt）
                    │     └─→ 命中观测（slang_observations · 今日）
                    │           ├─→ 折返：mute / disable
                    │           └─→ 折返：撤回到候选
                    └─→ 否决（rejected / muted / expired）
                          └─→ 折返：重新激活（manual）
```

折返路径**已有 API 全部支持**（`set_status` / `return_to_candidate` / `enable_for_prompt`），不需后端新增。

---

## 2. 设计目标 / 非目标

### 2.1 目标

1. **新增** `/learning` 总览页，**不重写**现有 5 个页面。
2. 用 DaVinci-style **横向阶段卡**把"线性工作流 + 折返"具象化。
3. 阶段 × noun 双轴筛选；「候选池」首屏可见所有 noun 同台。
4. 行级**内联审核**（复用 SlangView / StyleView 现成审核 Drawer），不切页处理。
5. 与 Dashboard 互补：Dashboard 答「**今天该做什么**」，`/learning` 答「**管道现在在哪一步**」。

### 2.2 非目标

- 不合并 / 替换现有 5 个页面（用户肌肉记忆不破坏）。
- 不重写各 noun 的审核详情逻辑（直接复用其 Drawer / 表单组件）。
- 不引图表库（sparkline / 阶段卡均手写 SVG/CSS）。
- 不动 Dashboard（方案 D 后续独立讨论，不与本方案绑定）。
- 不重构 SideMenu 顶级分组（仅在「学习与记忆」组顶部加一项 `/learning`）。

---

## 3. 工作流模型（5 阶段）

### 3.1 阶段定义（语义口径）

| # | 阶段名 | 中文 | 语义 | 折返来源 |
| --- | --- | --- | --- | --- |
| 1 | candidate | 候选池 | 抽取产物，尚未进入审核流 | 阶段 3 撤回 / 阶段 5 重激活 |
| 2 | review | 待审 | AI 初审通过或人工已介入但未拍板 | — |
| 3 | approved | 入库 | 已通过审核、已激活进入 Prompt / 命中观测 | 阶段 4 撤回（→ candidate） |
| 4 | hits | 命中（今日） | 已入库且**今天**被命中至少 1 次 | 阶段 3 的子集，仅展示口径 |
| 5 | archived | 归档 | mute / expired / rejected / disabled，已不参与 Prompt | — |

> **命中口径**（用户决定 2026-05-23）：**只看今日命中**，按 `created_at LIKE 'YYYY-MM-DD%'` 或对应表的 `observed_at`。
> 7 日均值不在本页展示，留给 Dashboard 学习时间线（方案 D）。

### 3.2 各 noun → 阶段映射

| noun | 1 候选 | 2 待审 | 3 入库 | 4 命中（今日） | 5 归档 |
| --- | --- | --- | --- | --- | --- |
| slang | `status='candidate'` 且 NOT `under_observation` | `ai_pending_review_count`（AI 通过、人工未拍板） | `status='approved'` | `slang_observations WHERE observed_at LIKE today` | `status IN ('muted','expired')` |
| style | `status='pending'`（条目） | 同上（无 AI 子态） | `status='approved'` | _无独立命中表_ → 显示 `--` | `status IN ('rejected','muted')` |
| episode | `state='candidate'`（episodic） | `state='approved'` 但未 `enabled_for_prompt` | `state='enabled_for_prompt'` | _无独立命中表_ → 显示 `--` | `state='disabled'` |
| consolidator（5 domain） | `state IN ('dry_run','queued')` | _无中间态_ → 计入候选 | `state='approved'` | _其入库后流向各 noun_ → 显示 `--` | `state='rejected'` |

> **关于命中显示空值**：style / episode / consolidator 当前没有命中观测表。
> 阶段卡数字按现有数据如实展示，**不造假**：style 命中显示 `--` 而非 `0`；后续若加观测表再补口径。
> 用户在 §13.Q1 处确认「候选池先全包含」→ consolidator 的 5 domain 与 slang/style/episode/memory **同台展示在阶段 1**。

### 3.3 折返路径（已有 API）

| 折返 | API（已存在） |
| --- | --- |
| 入库 → 候选 | `POST /api/admin/slang/items/:id/return-to-candidate` 等 |
| 入库 → 归档（mute） | `PATCH /api/admin/{noun}/items/:id` set_status='muted' |
| 归档 → 候选（重激活） | `PATCH ... set_status='candidate'` |
| 待审 → 入库 | 各 noun 现有审核 API |
| 待审 → 归档 | 现有「拒绝/否决」API |

**本方案不新增任何 set_status 类 API**；所有写操作走现有路由。

---

## 4. 信息架构（IA）

### 4.1 SideMenu 调整（仅 1 项新增）

```
日常
  仪表盘                  /
  人设编辑                /soul
  群管理                  /groups
  ...
  📊 学习管道总览        /learning              ← 新增
  群内黑话                /slang
  表达方式                /style
  跨群可见                /cross-group
  经验反思                /episodes
  记忆候选                /memory-consolidator
  ...
设置与维护
  ...
```

> 不在本方案重组「日常」12 项的分组结构（避免与方案 D 阶段 1 绑定）。
> `/learning` 排在 `/slang` 上方一行，作为该子区域的入口。

### 4.2 路由 / URL 状态

`/learning` 单一路由，状态全部走 URL query：

```
/learning?stage=candidate|review|approved|hits|archived&noun=all|slang|style|episode|memory|fact|graph_relation&group=<id>
```

- 默认 `stage=candidate`、`noun=all`、`group` 留空（全群）
- URL 状态可 deeplink：Dashboard 点击「黑话待审 12 条」 → `/learning?stage=review&noun=slang`
- 浏览器前进/后退保留筛选历史（用 `router.replace` 还是 `push` 见 §6.4）

---

## 5. 页面详细设计

### 5.1 总体骨架

```
┌─ /learning  · 学习管道总览 ───────────────────────────────────────┐
│  AppPage hero                                                     │
│    title=学习管道总览  eyebrow=Learning Pipeline                  │
│    description=候选 → 审核 → 入库 → 命中 → 归档 的同一管道       │
│    #action  [刷新] [一键抽取] [AI 清池(NPopconfirm)]              │
│                                                                   │
│  ┌─ 阶段卡条 (StageStrip) ──────────────────────────────────────┐│
│  │  [1 候选 23⚠] → [2 待审 18⚠] → [3 入库 847] → [4 命中 76]    ││
│  │                                                → [5 归档 12] ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                   │
│  ┌─ 当前阶段：候选池（domain filter + table） ─────────────────┐│
│  │  ◉ 全部  ○ 黑话  ○ 风格  ○ 经验  ○ 记忆候选  ○ memory       ││
│  │  群: [全部 ▼]  时间: [今天 ▼]  排序: [最新 ▼]              ││
│  │                                                              ││
│  │  类型 | 内容             | 来源群 | 时间   | 状态  | 操作   ││
│  │  ----+------------------+-------+--------+-------+--------- ││
│  │  黑话 | 猫饼            | 群100 | 14:23 | 候选  | 审核 详情 ││
│  │  风格 | 「绝绝子」高频  | 群200 | 14:18 | 候选  | 审核 详情 ││
│  │  经验 | 在群100承诺早睡 | 群100 | 14:10 | 候选  | 审核 详情 ││
│  │  fact | 康师傅是泡面厂  | 群300 | 13:55 | dry_run| 审核 详情││
│  │  ...                                                         ││
│  │  [ 加载更多（每次 30 行，懒加载） ]                          ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 Hero 区

复用 `AppPage` + 现有规范：

- `title`：「学习管道总览」
- `eyebrow`：「Learning Pipeline」
- `description`：「候选 → 审核 → 入库 → 命中 → 归档 的同一管道，按阶段处理。」
- `#action` 三个按钮：
  - `刷新`（NIcon RefreshOutline，secondary）
  - `一键抽取`（NPopconfirm 二次确认；调 `POST /api/admin/learning/extract-all`，**待审计：本接口要不要做？见 §13.Q3**）
  - `AI 清池`（NPopconfirm；调现有 `POST /api/admin/slang/run-backlog-review`，仅 slang）

### 5.3 阶段卡条 `<StageStrip>`（核心 UI）

新建组件 `views/learning/components/StageStrip.vue`。

#### 视觉规格

```
高度 80px · 横向 5 卡 · gap 12px · 卡间用 ›（chevron）连接
─────────────────────────────────────────────────────────────
当前阶段卡：
  border: 2px solid rgb(var(--primary-color))
  bottom border 4px solid var(--om-info)
  background: color-mix(...primary 8%, transparent)

非当前阶段卡：
  border: 1px solid var(--om-border)
  background: var(--om-surface-2)
  hover: border-color var(--om-border-strong)
```

#### 卡内布局

```
┌────────────────────┐
│ STAGE 01           │  ← eyebrow（10px / 0.18em / uppercase）
│ 候选池          23 │  ← title 14px bold + count 22px tabular
│ ⚠ 黑话 12 风格 8…  │  ← 拆分明细（11px text-3）
└────────────────────┘
```

`count` 数字 hover → tooltip 显示该阶段各 noun 拆分详情。

#### 行为

- 点击卡片 → 切换 `?stage=...`
- 当前阶段卡不可点（cursor: default）
- 数字为 0 时整张卡置灰（`opacity: 0.55`），仍可点
- 移动端（width < 768px）：横向滚动（overflow-x: auto），不强行 wrap

### 5.4 Noun 筛选器（NRadioGroup）

- 默认 `all`，候选池阶段额外显示 `fact / graph_relation`（consolidator 的 2 个非 noun domain）
- 切 noun → 仅过滤当前阶段的列表，不切阶段
- 同步到 `?noun=`

### 5.5 群组筛选 + 时间筛选

- 群组：NSelect，options 来源 `/api/admin/groups` 已有接口
- 时间：枚举 `今天 / 7 天 / 30 天 / 全部`；命中阶段强制锁定「今天」（disabled）
- 排序：`最新 / 置信度 / 来源群`

### 5.6 数据列表（统一表）

#### 表格列（5 阶段共用 schema）

| 列 | 宽度 | 数据源 |
| --- | --- | --- |
| 类型 | 64px | hardcoded `noun` 标签 |
| 内容 | 1fr | slang.term / style.fragment / episode.title / mc.payload_summary |
| 来源群 | 80px | 各表 `group_id`（global → "—"） |
| 时间 | 96px | `created_at` |
| 状态 | 80px | StateBadge，按阶段细分 |
| 操作 | 132px | `[审核] [详情] [⋮]` |

> `内容`列前 60 字符显示，hover NTooltip 全文。

#### 操作语义

| 按钮 | 行为 |
| --- | --- |
| 审核 | 弹 NDrawer width=480，挂载对应 noun 的 `<{Noun}ReviewDrawer>`（见 §7） |
| 详情 | `router.push('/{noun}?id={id}')` 深链跳现有页面 |
| ⋮ | NDropdown：折返动作（`撤回到候选` / `直接 mute` / `永久归档`），按当前 stage 显示子集 |

#### 空态

- 阶段卡为 0 → 表格区显示 `<EmptyState>`，文案按阶段切换（如「候选池目前没有积压」）

### 5.7 折返动作（hover affordance）

入库 / 命中阶段的行 hover 显示「撤回到候选 / 停用」icon button；归档阶段显示「重新激活 / 永久删除」。
全部走现有 API，前端只是 UI 编排。

### 5.8 Loading / Error 态

- 首次加载：`<NSpin :show=loading>` 包整个 layout，骨架阶段卡（5 张灰色矩形）+ 表格 skeleton 5 行
- 单个 noun 接口失败 → 该 noun 在阶段卡数字处显示 `?` 配 NTooltip "数据暂不可用"，**不影响其他 noun**
- 全量失败 → AppPage description 处显示 ErrorBanner

---

## 6. 后端 API 契约

### 6.1 新增聚合接口

**`GET /api/admin/learning/pipeline`**

聚合 5 阶段 × N noun 的计数，单次请求渲染 StageStrip。

请求：
```
?group=<id|empty>
&date=today|7d|30d|all   （仅影响阶段 1/2 的 created_at 过滤；阶段 4 强制 today）
```

响应：
```json
{
  "as_of": "2026-05-23T14:30:00+08:00",
  "stages": {
    "candidate": {
      "total": 23,
      "by_noun": {
        "slang": 12,
        "style": 8,
        "episode": 1,
        "fact": 1,
        "graph_relation": 1,
        "memory": 0
      }
    },
    "review":     { "total": 18, "by_noun": { "slang": 14, "style": 4, ... } },
    "approved":   { "total": 847, "by_noun": { ... } },
    "hits":       { "total": 76, "by_noun": { "slang": 76, "style": null, "episode": null } },
    "archived":   { "total": 12, "by_noun": { ... } }
  },
  "warnings": []
}
```

> `by_noun` 中 `null` 表示该 noun 在该阶段**没有可用口径**（如 style 的 hits）；前端显示 `--`。
> `0` 表示有口径但当前为空。两者必须区分。

### 6.2 新增列表接口

**`GET /api/admin/learning/items`**

请求：
```
?stage=candidate|review|approved|hits|archived
&noun=all|slang|style|episode|memory|fact|graph_relation
&group=<id|empty>
&date=today|7d|30d|all
&sort=newest|confidence|group
&limit=30
&cursor=<opaque>
```

响应：
```json
{
  "items": [
    {
      "id": "slang-12345",
      "noun": "slang",
      "kind_label": "黑话",
      "content": "猫饼",
      "content_full": "猫饼 = 群里说离谱但可爱的操作",
      "group_id": "100",
      "created_at": "2026-05-23T14:23:11+08:00",
      "status": "candidate",
      "status_label": "候选",
      "confidence": 0.78,
      "deep_link": "/slang?id=12345",
      "review_drawer": "slang"
    },
    ...
  ],
  "next_cursor": "...",
  "has_more": true
}
```

后端实现策略：**按 noun fan-out 查询、归并排序后切片**。第一版可以接受 N+1（5 noun × 一次 SQL），上线后再做 union all 优化。

### 6.3 写接口

**全部复用现有路由**，不新增。前端通过 `review_drawer` 字段决定挂哪个 Drawer：

| `review_drawer` | 现有 Drawer / 路由前缀 |
| --- | --- |
| `slang` | SlangView 内的 ReviewDrawer · `/api/admin/slang/...` |
| `style` | StyleView 内的 ReviewDrawer · `/api/admin/style/...` |
| `episode` | EpisodesView 审核 form · `/api/admin/episodes/...` |
| `consolidator` | MemoryConsolidatorView 审核 form · `/api/admin/memory-consolidator/...` |
| `memory` | 直接深链跳 `/memory?file=...`（memory 没有审核态） |

### 6.4 缓存与刷新

- 阶段卡 30 秒 polling（`setInterval` + visibility API 暂停）
- 单条审核成功 → 乐观更新阶段卡 count（-1 当前 stage、+1 目标 stage）
- 「刷新」按钮强制重拉 pipeline + items
- URL 状态切换用 `router.replace`（避免每次切阶段都进 history）

---

## 7. 复用 vs 新建组件

| 类型 | 已有可复用 | 本方案新建 |
| --- | --- | --- |
| 容器 | `AppPage` `AppCard` `AppPanelSection` `EmptyState` `PageToolbar` | — |
| 数据展示 | `StateBadge` `MetricCard` | `<StageStrip>` `<StageCard>` |
| 列表 | `<NDataTable>`（Naive UI 自带） | `<LearningTable>`（薄包装，统一空态/loading） |
| 抽屉 | `<NDrawer>` | `<UnifiedReviewDrawer>` ← 见下 |
| 折返动作 | 现有 API | `<RowActionMenu>`（NDropdown 包） |

### 7.1 `<UnifiedReviewDrawer>` 设计

新建一个**编排层**组件，**不重写**任何审核逻辑：

```vue
<UnifiedReviewDrawer
  v-model:show="drawerOpen"
  :item="selectedItem"
  @reviewed="onReviewed"
/>
```

内部根据 `item.review_drawer` 字段动态加载：

```ts
const DrawerByNoun = {
  slang: defineAsyncComponent(() => import('../../slang/components/SlangReviewDrawer.vue')),
  style: defineAsyncComponent(() => import('../../style/components/StyleReviewDrawer.vue')),
  episode: defineAsyncComponent(() => import('../../episodes/components/EpisodeReviewDrawer.vue')),
  consolidator: defineAsyncComponent(() => import('../../memory-consolidator/components/CandidateReviewDrawer.vue')),
}
```

> **前置依赖**：SlangView / StyleView / EpisodesView / MemoryConsolidatorView 当前各自把审核 UI 写在视图主文件内。
> 实施阶段 0 需先把它们抽成 `*ReviewDrawer.vue` 子组件（**纯结构提取，零逻辑改动**），主视图改为 import + 挂载。
> 这是阶段 0 的全部工作，**单独 1 个 PR**。

### 7.2 状态管理

`/learning` 不新增 store；筛选状态走 URL，列表数据 + 阶段卡数据用 `ref`，本页生命周期。
跨页跳转后回来重拉，不做 SPA 内缓存。

---

## 8. 实施切片（每段独立可发布）

| 阶段 | 改动范围 | 工程量 | PR 数 | 风险 |
| --- | --- | --- | --- | --- |
| **0** | SlangView/StyleView/EpisodesView/MemoryConsolidatorView 的审核 Drawer 抽出（纯重构，无功能变化） | 1 天 | 1 | 中（需各 noun 现有测试通过） |
| **1** | 后端 `GET /api/admin/learning/pipeline` 聚合接口 + 单测 | 0.5 天 | 1 | 低 |
| **2** | 前端 `/learning` 路由 + `<StageStrip>` + 后端聚合接口接通 | 1 天 | 1 | 低 |
| **3** | 后端 `GET /api/admin/learning/items` + 前端 LearningTable | 1 天 | 1 | 低 |
| **4** | `<UnifiedReviewDrawer>` 接通审核 API + 折返菜单 | 1 天 | 1 | 中（需各 noun 审核回调一致） |
| **5** | SideMenu 加项 + 上线 | 0.2 天 | 1 | 零 |

总计 **~5 工作日**、**6 个 PR**，每段独立可 revert。
任何一段不通过审计即停在该阶段，已落地部分依然可用。

---

## 9. 验证矩阵

### 9.1 自动化

- 阶段 0：`uv run pytest tests/test_slang_plugin.py tests/test_style_*.py tests/test_episodes_*.py tests/test_memory_consolidator_*.py`（已有套件全过）
- 阶段 1：新增 `tests/test_admin_api_learning_pipeline.py`，覆盖
  - 5 阶段计数正确
  - `by_noun.null` vs `by_noun=0` 区分
  - group/date 过滤
  - noun 缺数据时 warnings 字段
- 阶段 3：新增 `tests/test_admin_api_learning_items.py`，覆盖
  - 5 阶段 × all noun 切片
  - cursor 翻页
  - sort 三种顺序
- 前端：`./node_modules/.bin/vue-tsc --noEmit` + `npm run build`

### 9.2 手动 Golden Path

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | 进入 `/learning` 默认页 | 阶段卡 5 张全显示，候选池阶段加亮，表格首页 30 行 |
| 2 | 切换阶段 → 待审 | URL `?stage=review`，表格切换，阶段卡当前态切换 |
| 3 | 切换 noun → 黑话 | 表格只显黑话，阶段卡数字保持原值（noun 是表格筛选不是阶段筛选） |
| 4 | 行内点「审核」→ 通过 | Drawer 关闭，行从候选阶段消失，入库阶段 +1 |
| 5 | 入库阶段行 hover → 撤回 | 行回到候选阶段 |
| 6 | Dashboard pendingItems 点击 → 跳 `/learning?stage=review&noun=slang` | 直接进入对应阶段 + noun |
| 7 | 全部接口 502 | 阶段卡数字显 `?` + tooltip，表格显 ErrorBanner |
| 8 | style 命中显示 | `--`（不是 0） |

### 9.3 D 系列条款合规

| 条款 | 应用 |
| --- | --- |
| D1 同模式扫描 | 抽 Drawer 时 grep 全仓 `Drawer\|审核\|set_status` 确保不遗漏点位 |
| D2 cancel-path | 后端 pipeline / items 接口要在长查询时被 `wait_for` 取消而不污染状态 → 单测必含 |
| D3 迁移清单 | SideMenu 改动列入「旧→新」表（仅 1 行：新增 `/learning`） |
| D4 完成证据 | 每阶段 PR 描述需含：① 同模式扫描结果 ② curl 实测响应 ③ 回滚 git revert hash |
| D5 pytest 防孤儿 | 跑全量前 `pkill -9 -f pytest` |
| D6 admin SPA 同步 | 阶段 0/2/3/4/5 涉及 .py → 需 `dot_clean . && docker compose up bot -d --build` |
| D7 部署前 git hygiene | 每段 deploy 前 `git stash list && git status -uno` |

---

## 10. 风险与回滚

| 风险 | 概率 | 影响 | 缓解 |
| --- | --- | --- | --- |
| 阶段 0 重构破坏现有审核交互 | 中 | 高 | PR 0 只做结构提取，不改逻辑；现有测试套必过 |
| `<UnifiedReviewDrawer>` 与 noun 子组件 prop/emit 不兼容 | 中 | 中 | PR 0 同步约定一致接口（`item` in、`reviewed` out） |
| 聚合接口慢（5 noun × 多 SQL） | 低 | 中 | 接受 N+1 上线；后续按需做 union all + 指标观测 |
| style/episode 命中口径缺失被用户误读为「没在用」 | 中 | 低 | 显示 `--` 不是 0；hover tooltip 解释 |
| consolidator 5 domain 进同一表造成视觉拥挤 | 中 | 低 | noun filter 分类 + 加载更多分页 |
| 用户更习惯老页面，`/learning` 沦为摆设 | 中 | 低 | Dashboard pendingItems 改为深链跳 `/learning`，把流量引过来 |

### 回滚

每阶段独立 commit，回滚单位 = `git revert <hash>`：

- 阶段 5 → 移除 SideMenu 加项
- 阶段 4 → 退回阶段 3 的只读列表
- 阶段 3 → 退回阶段 2 的只阶段卡
- 阶段 2 → 退回阶段 1 的只后端
- 阶段 1 → 删除 `/api/admin/learning/pipeline`
- 阶段 0 → 把 Drawer 抽出 revert 回 inline；这步**最痛**，所以阶段 0 的 PR 必须有充分测试

---

## 11. 与方案 D（Dashboard Command Center）的关系

互补，不冲突，不互为前置：

| 维度 | 方案 D · Dashboard | 方案 E · /learning |
| --- | --- | --- |
| 回答的问题 | 今天该做什么？ | 学习管道现在在哪一步？ |
| 入口 | 每次登录第一屏 | SideMenu 主动点入 / Dashboard 深链 |
| 数据视角 | 全系统聚合（含运行态、群、节奏） | 仅学习管道（5 noun） |
| 折返支持 | 列表只读 + 深链跳 | 行内折返 + 内联审核 |
| 工程量 | 5 阶段 / ~7 天 | 6 PR / ~5 天 |

**联动点**：Dashboard `pendingItems` 现状是 `goTo('/slang')`，方案 E 落地后改为 `goTo('/learning?stage=review&noun=slang')`，实现「Dashboard 是入口、`/learning` 是处理台」的链路。

---

## 12. 文件清单（落地后）

新建：
```
admin/frontend/src/views/learning/
  LearningView.vue                        # 主视图
  components/
    StageStrip.vue                        # 5 阶段卡条
    StageCard.vue                         # 单个阶段卡
    LearningTable.vue                     # 统一表格
    UnifiedReviewDrawer.vue               # Drawer 编排层
    RowActionMenu.vue                     # 折返菜单
admin/routes/api/learning_pipeline.py     # 新接口
tests/test_admin_api_learning_pipeline.py
tests/test_admin_api_learning_items.py
```

修改：
```
admin/frontend/src/router/index.ts        # +/learning 路由
admin/frontend/src/layouts/components/SideMenu.vue  # +1 项
admin/frontend/src/views/dashboard/DashboardView.vue # pendingItems goTo 改深链
admin/frontend/src/views/{slang,style,episodes,memory-consolidator}/  # 阶段 0 抽 Drawer
admin/__init__.py                         # 注册 learning_pipeline 路由
```

不动：
- 5 个 noun 主视图的业务逻辑
- 各 noun 的 CRUD / set_status / AI 审核 API
- Dashboard 整体结构（仅改深链 1 处）

---

## 13. 待审计的开放问题（请用户确认后再实施）

> 用户已确认（2026-05-23）：
> ✅ Q-A 命中口径 = 仅今日
> ✅ Q-B 候选池范围 = 全包含 consolidator 5 domain

剩余问题：

### Q1 · 阶段 0 重构是否可单独 ship？

阶段 0（抽 Drawer）本身不带任何用户可见变化，只是把同一段 Vue 模板从主文件挪到子文件。
是否可以**独立 PR 先 merge**，让线上跑一周观察各 noun 审核稳定性，再启动阶段 1？

- 选项 A：是，独立 ship + 观察 7 天
- 选项 B：否，阶段 0~5 整体一组 PR 串行评审
- 选项 C：阶段 0 + 阶段 1 合并为一个「准备 PR」（前后端骨架）

### Q2 · `一键抽取` 按钮要不要做？

Hero 区设计了「一键抽取」按钮（同时触发 slang / style / episode / consolidator 抽取）。
当前各 noun 抽取节奏不一致（slang 有定时器、consolidator 是 dream agent 周期），手动一键可能与定时冲突。

- 选项 A：做，加 NPopconfirm 警示「可能消耗较多 LLM 配额且与定时任务并发」
- 选项 B：不做，仅保留各 noun 页内的「手动抽取」入口
- 选项 C：先不做，预留按钮位置，留待方案落地后看用户行为再决定

### Q3 · 命中阶段（4）的 noun 范围

slang 有 `slang_observations` 表，命中数据真实可查。
style / episode / consolidator 当前**没有**命中观测表。

- 选项 A：仅 slang 显示数字，其他 noun 显示 `--`（默认方案）
- 选项 B：扩展 style/episode 增加观测表（额外工程，超出本方案）
- 选项 C：把命中阶段缩成「slang only」标签，避免 `--` 引起困惑

### Q4 · `/memory` 的归宿

`/memory` 是 .md 文件库，没有候选/审核态。
是否纳入 `/learning`？

- 选项 A：纳入候选池阶段，行级显示「未归档/已归档」（轻量，无审核操作）
- 选项 B：不纳入，`/learning` 只覆盖 4 个有状态机的 noun（slang/style/episode/consolidator）
- 选项 C：纳入「入库」阶段（已存在的 .md 算入库），仅做计数不做行操作

### Q5 · 对 Dashboard 的反向修改

阶段 5 上线后，Dashboard `pendingItems` 的深链是否同步改？这是把 `/learning` 引流的关键。

- 选项 A：阶段 5 同 PR 改 Dashboard 深链
- 选项 B：分开做，等 `/learning` 跑稳一周后再切流量
- 选项 C：保持 Dashboard 旧深链不动，让用户自己发现 `/learning`（不推荐）

### Q6 · 文档归档

本方案审计通过后是否：
- 选项 A：保留为 `docs/tracking/learning-pipeline.md` 持续更新（推荐）
- 选项 B：合并入 `docs/tracking/web-refactor.md` 作为新 section
- 选项 C：作为一次性方案文档，落地后归档至 `docs/migrations/`

---

## 14. 审计记录

| 日期 | 审计人 | 决议 | 备注 |
| --- | --- | --- | --- |
| 2026-05-23 | 用户 | **通过 v1** | Q1=C / Q2=A / Q3=B / Q4=A / Q5=A / Q6=A |
| 2026-05-23 | gpt | **需修订后再进入 PR** | 见 §17；关键阻塞为 `/memory` 口径、style/episode 命中写入点、API 注册与路由清单 |

### 14.1 用户决议详情（2026-05-23）

| # | 问题 | 选项 | 含义 |
| --- | --- | --- | --- |
| Q1 | 阶段 0 是否独立 ship | **C** | 阶段 0 + 阶段 1 合并为一个「准备 PR」（前后端骨架） |
| Q2 | 「一键抽取」按钮 | **A** | 做，加 NPopconfirm 警示「可能消耗较多 LLM 配额且与定时任务并发」 |
| Q3 | 命中阶段 noun 范围 | **B** | **扩展 style/episode 增加观测表**（追加工程量，纳入本方案） |
| Q4 | `/memory` 是否纳入 | **A** | 纳入候选池阶段，行级「未归档/已归档」标签，无审核操作 |
| Q5 | Dashboard 反向修改 | **A** | 阶段 5 同 PR 改 Dashboard pendingItems 深链 |
| Q6 | 文档归档 | **A** | 保留为 `docs/tracking/learning-pipeline.md` 持续更新 |

---

## 15. 审计后修订条目（v1 → 实施版）

> 本节列出 Q1~Q6 决议对前述设计的具体修改。**§3~§12 的细节按下述条目读，原文不再回改避免审计 diff 丢失。**

### 15.1 Q1=C · 切片合并

§8「实施切片」原 6 个 PR 调整为 **5 个 PR**：

| 新阶段 | 改动范围 | 工程量 | 风险 |
| --- | --- | --- | --- |
| **0+1（准备 PR）** | 抽 Drawer + 后端 `/pipeline` 聚合接口 + 单测 | 1.5 天 | 中 |
| **2** | 前端 `/learning` 路由 + `<StageStrip>` + 接通聚合接口 | 1 天 | 低 |
| **3** | 后端 `/items` + 前端 LearningTable | 1 天 | 低 |
| **4** | `<UnifiedReviewDrawer>` + 折返菜单 + 一键抽取按钮（Q2） | 1.2 天 | 中 |
| **5** | SideMenu 加项 + Dashboard 深链同步（Q5） + 上线 | 0.3 天 | 低 |

总工程量：~5 天 → 维持，PR 数 6 → 5。

### 15.2 Q2=A · 「一键抽取」加入 PR 4

- 路由：`POST /api/admin/learning/extract-all`（new in PR 4）
- 行为：并发触发 slang / style / episode / consolidator 的现有 `run_manual_extract`；不阻塞 UI，返回各 noun run_id
- NPopconfirm 文案：
  > 将并发触发 4 类抽取（黑话 / 风格 / 经验 / 记忆候选），可能与各自定时任务并发，消耗较多 LLM 配额。是否继续？
- 失败语义：单个 noun 失败不影响其余；返回体 `{"results": {"slang": {"ok": true, "run_id": "..."}, "style": {"ok": false, "error": "..."}, ...}}`
- 单测：`tests/test_admin_api_learning_extract_all.py`（新增），覆盖部分失败 + 全失败 + 全成功

### 15.3 Q3=B · style/episode 增加命中观测表（**最大工程变更**）

§3.2 原口径「style/episode 命中显示 `--`」**作废**；改为：

#### 15.3.1 新增表：`style_observations`

```sql
CREATE TABLE IF NOT EXISTS style_observations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  style_id      TEXT NOT NULL,
  scope         TEXT NOT NULL,           -- 'group' | 'global'
  group_id      TEXT NOT NULL DEFAULT '',
  observed_at   TEXT NOT NULL,           -- ISO 8601
  trigger_type  TEXT NOT NULL,           -- 'prompt_inject' | 'profile_apply'
  message_id    TEXT,                    -- 触发该命中的消息（可空）
  meta          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_style_obs_today
  ON style_observations(observed_at, style_id);
CREATE INDEX IF NOT EXISTS idx_style_obs_scope
  ON style_observations(scope, group_id, observed_at);
```

#### 15.3.2 新增表：`episode_observations`

```sql
CREATE TABLE IF NOT EXISTS episode_observations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id    TEXT NOT NULL,
  scope         TEXT NOT NULL,
  group_id      TEXT NOT NULL DEFAULT '',
  observed_at   TEXT NOT NULL,
  trigger_type  TEXT NOT NULL,           -- 'prompt_inject' | 'reflection_cite'
  message_id    TEXT,
  meta          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_episode_obs_today
  ON episode_observations(observed_at, episode_id);
CREATE INDEX IF NOT EXISTS idx_episode_obs_scope
  ON episode_observations(scope, group_id, observed_at);
```

#### 15.3.3 写入点（同模式扫描，D1）

参考 slang 现有 `record_observation` 的注入位置（services/slang/store.py），在以下点位补写：

| Noun | 写入点（待 grep 确认） | 触发条件 |
| --- | --- | --- |
| style | `services/style/injector.py` 注入 prompt 时 | profile/expression 进入 system block |
| episode | `services/episodic/injector.py` 注入 prompt 时 | episode 进入 prompt 或 reflection cite |

**实施门槛**：先 grep `slang_observations` 全仓使用模式 → 找到对应 style/episode 的同模式位点 → 一并补写。**不写入 = 命中数永远 0，等同于 Q3-A**。

#### 15.3.4 PR 切分

Q3-B 体量较大，拆出 **新增 PR 0.5「观测表迁移」**（在 PR 0+1 与 PR 2 之间）：

- 内容：建表迁移 + 注入器写入逻辑 + 单测
- 工程量：~1 天
- 风险：中（注入器是热路径，需性能测试不能阻塞）
- 性能要求：写入异步化（`asyncio.create_task` 后台 fire），不阻塞 prompt 构建

**修订后总工程量：~5 天 → ~6 天**，PR 数 5 → **6**。

#### 15.3.5 §6.1 聚合接口响应字段更新

```json
"hits": {
  "total": 76 + 24 + 8,
  "by_noun": {
    "slang": 76,
    "style": 24,        // 不再 null
    "episode": 8,       // 不再 null
    "memory": null,     // memory 仍无观测语义，保持 null
    "fact": null,
    "graph_relation": null
  }
}
```

§5.6 表格命中阶段不再显示 `--`（slang/style/episode），仅 memory/fact/graph_relation 保持 `--`。

### 15.4 Q4=A · `/memory` 纳入候选池

§3.2 噬补 1 行：

| memory | `_storage/memories/*.md` 中带 `<!-- pending -->` 标记的文件 | _无_ | 已存在的 `.md` 文件 | _不显示_ | 已删除的 `.md` |

#### 15.4.1 实现细节

- 候选阶段：扫 `storage/memories/*.md`，带 pending block 的算候选（已有 `append_memo` 写入逻辑）
- 入库阶段：所有 `.md` 文件计数（已有 listing 逻辑）
- 命中：memory 是 prompt 全注入，无观测语义 → `null`
- 行内操作：仅「详情」（深链 `/memory?file=...`），无「审核」按钮（用 `review_drawer = null` 表示）
- 折返：归档 = 物理删除文件 + `.bak` 保留（已有逻辑），UI 仅展示，不在 `/learning` 提供删除入口

#### 15.4.2 §6.1 聚合接口响应

```json
"by_noun": {
  ...
  "memory": {
    "candidate": 3,    // pending 块条数
    "approved": 17,    // .md 文件数
    "review": 0,
    "hits": null,
    "archived": 0      // 不计 .bak
  }
}
```

### 15.5 Q5=A · Dashboard 深链同 PR 改

PR 5 范围扩充：

- `admin/frontend/src/views/dashboard/DashboardView.vue` 修改 4 处 `pendingItems[].route`：
  - `/slang` → `/learning?stage=review&noun=slang`（slang-candidate / slang-ai-review）
  - `/style` → `/learning?stage=review&noun=style`（style-pending）
- `primaryShortcut.route` 同步改深链
- 验证：手点 Dashboard 4 个 todo item，每个落到 `/learning` 对应 stage+noun

### 15.6 Q6=A · 文档持续更新

- 本文件 `docs/tracking/learning-pipeline.md` 持续作为唯一权威方案
- 每个 PR merge 后回写本文件 §16「实施日志」（新增节，见 15.7）
- 不合并入 web-refactor.md；不归档至 migrations/

### 15.7 修订后 PR 顺序总览

| PR | 名称 | 内容 |
| --- | --- | --- |
| 1 | 准备：抽 Drawer + 后端聚合 | 阶段 0+1 合并 |
| 2 | 观测表迁移 | style/episode_observations + 注入器写入 |
| 3 | 前端骨架 | `/learning` 路由 + StageStrip |
| 4 | 列表 | 后端 `/items` + LearningTable |
| 5 | 审核 + 一键抽取 | UnifiedReviewDrawer + extract-all 接口 |
| 6 | SideMenu + Dashboard 同步 | 加项 + 4 处 Dashboard 深链 + 上线 |

总工程量：**~6 工作日**、**6 PR**，每段独立可 revert。

---

## 16. 实施日志（PR 落地后回写，初始为空）

| PR | 状态 | 落地日期 | commit | 同模式扫描结果 | 验证证据 | 回滚 hash |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | _未启动_ | — | — | — | — | — |
| 2 | _未启动_ | — | — | — | — | — |
| 3 | _未启动_ | — | — | — | — | — |
| 4 | _未启动_ | — | — | — | — | — |
| 5 | _未启动_ | — | — | — | — | — |
| 6 | _未启动_ | — | — | — | — | — |

---

## 17. gpt 独立审计报告（2026-05-23）

### 17.0 审计结论

审计人：**gpt**

结论：`/learning` 的产品方向成立，尤其是「候选 → 审核 → 入库 → 命中 → 归档」阶段条把 slang / style / episode / consolidator 的共同心智模型拉到同一页，符合当前 Admin Calm Ops 风格和近期 Web 重构方向。但当前 §15 实施版**不能直接进入 PR**，需要先修订 3 个阻塞项和 7 个条件项。

审计范围：

- 已读 wiki / 高层文档：`docs/wiki/Home.md`、`docs/wiki/Architecture.md`、`docs/wiki/Configuration.md`、`docs/wiki/Plugins.md`、`docs/wiki/Knowledge-System.md`、`docs/wiki/Style-Learning.md`、`docs/architecture.md`、`docs/project-info.md`、`docs/operations.md`、`docs/admin-ui-style-guide.md`。
- 已读近期日志：`maintenance-log.md` 2026-05-23 多条 admin/frontend、Jinja 退役、SlangView 收口记录。
- 已对照实现：`admin/frontend/src/router/index.ts`、`admin/frontend/src/layouts/components/SideMenu.vue`、`admin/routes/api/learning.py`、`admin/routes/api/__init__.py`、`admin/routes/api/{slang,style,episodes,memory_consolidator,memory}.py`、`services/{slang,style,episodic,memory_consolidator,memory}/`、`services/block_trace/{slang_provider,style_provider,episode_provider}.py`、`plugins/chat/plugin.py`。

### 17.1 阻塞项

| 严重度 | 发现 | 证据 | 影响 | 修订要求 |
| --- | --- | --- | --- | --- |
| 阻塞 | §15.4 `/memory` 纳入方式仍按旧 `.md` 文件库设计，且写成 `_storage/memories/*.md` pending 标记。当前主路径已经是 `CardStore` / `memory_cards.db`，Admin `/memory` 走 `/api/admin/memory/cards`。 | `docs/project-info.md` 写明 `memory_cards.db` 是类型化记忆卡片，旧 `storage/memories/*.md` 已迁移；`admin/routes/api/memory.py` 只暴露 `/memory/cards`；`services/memory/card_store.py` 用 `memory_cards.status` 列表查询。 | 如果照 §15.4 实施，聚合接口会扫错存储，候选/入库数字不可信，深链也会落到旧浏览语义。 | Q4=A 需要重写：`memory` 只能按 `memory_cards.status='active'/'expired'` 做只读入库/归档视角，或重新改为不纳入 `/learning`；不得扫描 `.md` 作为第一版数据源。 |
| 阻塞 | §15.3 写入点引用 `services/style/injector.py`、`services/episodic/injector.py`，但当前仓库没有这些文件；实际活跃注入路径是 `PromptProviderBus` 的 `StyleProvider` / `EpisodeProvider`。 | `plugins/chat/plugin.py` 注册 `StyleProvider`、`EpisodeProvider` 且 `provider_bus.mode = "active"`；`plugins/style/plugin.py` 在 provider 存在时 `_provider_superseded=True` 并跳过 `on_pre_prompt`；`services/block_trace/style_provider.py` 和 `episode_provider.py` 生成 PromptBlockCandidate。 | Q3-B 的 style/episode 命中观测若按文档点位开发，会找不到落点或漏掉生产路径，命中阶段会长期错误。 | 观测表 PR 必须改为同模式扫描 provider path：优先在 `StyleProvider.provide` / `EpisodeProvider.provide` 或对应 store 方法记录命中，并补 fallback `StylePlugin.on_pre_prompt` 路径；单测覆盖 provider_bus active。 |
| 阻塞 | §12 注册文件写 `admin/__init__.py`，但当前 JSON API 聚合在 `admin/routes/api/__init__.py`；memory-consolidator API 前缀实际为 `/api/admin/memory_consolidator`，不是 `/api/admin/memory-consolidator`。 | `admin/routes/api/__init__.py` include 各 `create_*_router`；`admin/routes/api/memory_consolidator.py` 使用 `APIRouter(prefix="/memory_consolidator")`。 | PR 文件清单和接口契约会误导实现，导致路由未挂载或前端调用 404。 | §6.3 / §12 统一改成真实 API 前缀和注册文件：新增 router 应在 `admin/routes/api/__init__.py` include；consolidator 路由文档使用下划线前缀。 |

### 17.2 条件项

| 严重度 | 发现 | 证据 | 建议 |
| --- | --- | --- | --- |
| 高 | `extract-all` 被 §6.3 写成“不新增写接口”，又在 §15.2 / §15.7 加回实施切片；状态已修订但风险未完全展开。 | 现有手动抽取入口分散：`POST /api/admin/slang/extract/run`、`POST /api/admin/style/extract/run`、`POST /api/admin/memory_consolidator/runs` / `/reflect`；episode 不是独立 extractor。 | 将 `extract-all` 定义为编排接口而不是业务接口，必须显式列出每个 noun 的调用目标、并发锁、部分失败语义、LLM 配额提示和后台任务可观察 run_id。 |
| 高 | `<UnifiedReviewDrawer>` 抽象比文档估计更重：slang 已有 `SlangDetailDrawer`，style 当前没有抽屉而是列表内按钮，episodes / memory-consolidator 的审核逻辑仍在主视图内。 | `SlangView.vue` 已 import `SlangDetailDrawer`；`StyleView.vue` 直接调用 `/style/expressions/{id}/status`；`EpisodesView.vue`、`MemoryConsolidatorView.vue` 的详情/审核逻辑在视图内。 | PR 1 需要先做“审核组件盘点表”，逐 noun 定义 props/emit，不要默认四个 `*ReviewDrawer.vue` 都只是纯搬模板。style 可能应先做轻量 `StyleReviewPanel`，避免强行抽 Drawer。 |
| 中 | 方案把 `stage` 作为 URL query 但又规定切换用 `router.replace`；黄金路径要求浏览器前进/后退保留筛选历史，两者冲突。 | §4.2 写“浏览器前进/后退保留筛选历史”；§6.4 写“URL 状态切换用 `router.replace`”。 | 区分交互：阶段/noun 切换用 `router.push` 才能进 history；轮询刷新、表格分页、内部 count 更新可用 `replace`。 |
| 中 | `hits` 语义需要防重复计数，尤其 style profile block 可能一次包含多条 expression，episode block 一次包含多个 episode。 | `StyleProvider` 生成 profile 与 expression 两类 block；`EpisodeProvider` 的 `metadata` 仅有 `episode_count`，`evidence_refs` 才有 episode_id 列表。 | 观测表 schema 增加去重键或唯一约束，例如 `(expression_id, message_id, trigger_type)` / `(episode_id, message_id, trigger_type)`；无 message_id 时用 request_id 或 prompt trace id。 |
| 中 | 文档的时间过滤写“仅影响阶段 1/2”，但用户期望 `/learning` 观测管道整体状态；approved/archived 是否受 date 过滤需要明确。 | §6.1 `date` 说明只影响 candidate/review，§5.5 时间筛选却是全局控件。 | 第一版建议：阶段卡默认展示全量库存；列表时间筛选仅过滤列表；命中阶段固定 today。若阶段卡也受 date 影响，UI 必须显示“库存/窗口”两个口径。 |
| 中 | 当前 `/api/admin/learning/today` 已服务 Dashboard 今日学习模块，新 `/api/admin/learning/pipeline` 不应替代它。 | `admin/routes/api/learning.py` 现有 `/learning/today` 聚合 slang/style/stickers。 | 新 router 可放同文件或 `learning_pipeline.py`，但要保留 `/learning/today` 响应结构和测试，避免 Dashboard 回归。 |
| 低 | SideMenu 新增项需要同步 `activeKey`，否则 `/learning` 作为普通 route 可用但折叠高亮/分组心智不完整。 | `SideMenu.vue` 直接返回 `route.path`，新项存在即可高亮；无需特殊 redirect。 | §12 保留 SideMenu 改动即可，图标建议用已有 `AnalyticsOutline` 或新增更贴近 pipeline 的 icon。 |

### 17.3 已确认可行项

- `/learning` 作为 SPA 路由可直接加入 `admin/frontend/src/router/index.ts`；实际访问 URL 为 `/admin/learning`，文档内写 `/learning` 作为 SPA 内路由是可接受的。
- StageStrip / LearningTable / RowActionMenu 与现有 `AppPage`、`AppPanelSection`、`EmptyState`、`StateBadge` 组件体系相容，不需要引入新图表库。
- slang / style / episodes / memory_consolidator 都已有基本审核或状态迁移 API，可作为 `/learning` 行操作的底座；但具体 endpoint 名称需按真实 API 修订。
- 当前 `admin/routes/api/learning.py` 的 `/learning/today` 已采用 best-effort 聚合模式，新 `/pipeline` 可沿用“单 noun 失败不拖垮全页”的错误隔离策略。

### 17.4 建议修订后的 PR 顺序

| PR | 建议状态 | 内容调整 |
| --- | --- | --- |
| 1 | 保留但改名 | “准备：接口口径 + Drawer/Panel 盘点 + pipeline 只读计数”。先修 §17.1 的路由/文件清单，再写 `/pipeline`。 |
| 2 | 保留 | “观测表迁移”必须改为 provider_bus active 路径，新增 store 方法、去重约束和 provider 单测。 |
| 3 | 保留 | 前端骨架只接 `/pipeline`，不放写操作；StageStrip 先跑只读。 |
| 4 | 保留 | `/items` 先覆盖 slang/style/episode/consolidator；`memory` 只有在 Q4 口径重写后再纳入。 |
| 5 | 拆分可选 | `UnifiedReviewDrawer` 与 `extract-all` 风险不同，若 PR 过大应拆成 5a 审核/折返、5b 一键抽取。 |
| 6 | 保留 | SideMenu + Dashboard 深链，上线前必须手测 4 个 Dashboard todo item。 |

### 17.5 必改清单

进入 PR 前先完成以下文档修订：

1. 重写 §15.4：删除 `.md pending` / `_storage/memories` 口径，改成 `CardStore` 口径或重新选择 Q4。
2. 重写 §15.3.3：把 style/episode 命中写入点从不存在的 injector 文件改为 `services/block_trace/style_provider.py`、`services/block_trace/episode_provider.py`、`plugins/style/plugin.py` fallback，并补去重策略。
3. 修正 §6.3 / §12：API 前缀、注册文件、consolidator 下划线路由全部对齐实现。
4. 修正 §6.4：明确 `push` / `replace` 使用边界，避免与浏览器历史验收互相矛盾。
5. 给 `extract-all` 增加明确编排目标、并发锁和部分失败响应 schema。
6. 把 PR 1 的“纯 Drawer 抽出”改为“审核组件盘点 + 最小可复用接口”，避免低估 style/episode/consolidator 的差异。

---

## 18. deepseek 独立审计报告（2026-05-23）

### 18.0 审计说明

审计人：**deepseek**

审计范围：§0–§16（不含 §17）。独立形成判断，不受已有审计影响。

已对阅以下实现文件：

| 文件 | 核实内容 |
| --- | --- |
| `services/block_trace/style_provider.py` | StyleProvider.provide 是生产注入路径 |
| `services/block_trace/episode_provider.py` | EpisodeProvider.provide 是生产注入路径 |
| `services/memory/card_store.py` | CardStore 是当前 memory 主存储 |
| `admin/routes/api/memory.py` | 当前 memory API 全部走 `/memory/cards` |
| `admin/routes/api/__init__.py` | learning router 已注册 |
| `admin/routes/api/memory_consolidator.py` | prefix 使用 `/memory_consolidator`（下划线） |
| `admin/frontend/src/views/slang/components/SlangDetailDrawer.vue` | slang 审核 Drawer 已存在 |
| `admin/frontend/src/views/dashboard/DashboardView.vue` | 当前深链走 `/slang`、`/style` |
| `services/slang/store.py` | `record_observation` 已有，可作同模式参照 |

### 18.1 总体结论

方案方向成立。"候选 → 审核 → 入库 → 命中 → 归档"五阶段模型成功统一了 slang/style/episode/consolidator/memory 五个 noun 的共同心智模型，`/learning` 作为总览页的定位与 Dashboard（"今天该做什么"）互补而非冲突。实施切片粒度合理，每个 PR 独立可 revert。

但 §15 修订后的实施版存在 **4 个阻塞项** 和 **6 个条件项**，必须在进入 PR 前修订。

### 18.2 阻塞项（不改会导致上线即错）

| # | 严重度 | 发现 | 证据 | 修订要求 |
| --- | --- | --- | --- | --- |
| B1 | **阻塞** | §15.4 的 `/memory` 纳入方式基于旧 `.md` 文件库，写成 `_storage/memories/*.md` 带 `pending` 标记。但当前生产路径已是 `CardStore` / `memory_cards.db`。 | `admin/routes/api/memory.py` 仅暴露 `/memory/cards`，无一行的 `.md` 文件操作。`services/memory/card_store.py:201` 是当前唯一活跃 store。 | §15.4 全文重写：删除所有 `.md` 扫描逻辑。memory 的"候选"改为 `memory_cards.status='pending'`（若 CardStore 有该状态）或直接设为不纳入。入库 = `status='active'`。归档 = `status='expired'`。**如 CardStore 无 pending 状态，将 Q4 改为选项 B（不纳入），不得伪造口径。** |
| B2 | **阻塞** | §15.3.3 的命中观测写入点写的是 `services/style/injector.py`、`services/episodic/injector.py`。这两个文件**不存在**。 | `ls services/style/` 无 injector.py。`plugins/style/plugin.py` 在 `_provider_superseded=True` 时跳过注入。实际生产路径是 `services/block_trace/style_provider.py:47` `StyleProvider.provide` 和 `services/block_trace/episode_provider.py:107` `EpisodeProvider.provide`。 | §15.3.3 全部重写：写入点改为 `StyleProvider.provide` / `EpisodeProvider.provide`。需区分两类 block：profile block 含多条 expression、expression block 含单条。去重键使用 `(expression_id/episode_id, message_id, trigger_type)`。单测覆盖 provider_bus active 路径。 |
| B3 | **阻塞** | §12 写成 `admin/__init__.py` 注册 `learning_pipeline` 路由，但实际注册入口是 `admin/routes/api/__init__.py`。且 memory-consolidator 前缀是 `/memory_consolidator`（下划线），不是 `/memory-consolidator`（连字符）。 | `admin/routes/api/__init__.py:66-67` `from admin.routes.api.learning import create_learning_router`。`admin/routes/api/memory_consolidator.py:30` `prefix="/memory_consolidator"`。 | §6.3 / §12 全部修正：API 注册文件改为 `admin/routes/api/__init__.py`。consolidator 路由全部改用 `/memory_consolidator`。 |
| B4 | **阻塞** | §6.4 同时写"URL 状态切换用 `router.replace`"和"浏览器前进/后退保留筛选历史"，两者不可兼得。 | §4.2："浏览器前进/后退保留筛选历史"。§6.4："URL 状态切换用 `router.replace`"。`replace` 不进 history，恢复历史时不会恢复筛选状态。 | 区分使用场景：阶段/noun 切换用 `push`（进 history）；轮询刷新、分页加载用 `replace`。§4.2 + §6.4 统一修订。 |

### 18.3 条件项（建议修订后再进 PR）

| # | 严重度 | 发现 | 证据 | 建议 |
| --- | --- | --- | --- | --- |
| C1 | **高** | `extract-all` 接口在 §6.3 明确说"不新增写接口"，但在 §15.2 又被加入 PR 4。语义矛盾，且 §15.2 的并发风险未充分展开。 | §6.3："全部复用现有路由，不新增。" §15.2："路由：`POST /api/admin/learning/extract-all`（new in PR 4）"。 | 删除 §6.3 中"不新增写接口"声明。给 `extract-all` 定义：每个 noun 的调用目标、并发锁（防止 double-fire）、部分失败返回 schema、LLM 配额提示何时弹。 |
| C2 | **高** | `<UnifiedReviewDrawer>` 假设四个 noun 都有同构的 `*ReviewDrawer.vue`。实际：slang 已有 `SlangDetailDrawer`；style 没有 Drawer 而是列表内按钮；episodes/consolidator 审核逻辑仍在主视图内。 | `SlangView.vue:16` import `SlangDetailDrawer`。`StyleView.vue` 直接 patch `/style/expressions/{id}/status`。 | PR 1 先做"审核组件盘点表"：逐 noun 定义当前审核 UI 形态、props、emit。style 可能只需 `StyleReviewPanel`（非 Drawer），不必强行抽成 Drawer 统一形态。 |
| C3 | **中** | §5.5 时间筛选为全局控件（今天/7天/30天/全部），但 §6.1 定义 `date` 参数只过滤 candidate + review 阶段。approved/archived 是否受 date 影响未明确，UI 与 API 契约脱节。 | §5.5：时间筛选为全局 NSelect。§6.1："仅影响阶段 1/2 的 created_at 过滤"。 | 第一版：阶段卡只展示全量库存（不受 date 过滤）；时间筛选仅影响列表。阶段卡如需同样受过滤，必须显示"库存 vs 窗口"双口径，避免用户误读。 |
| C4 | **中** | style profile block 一次可含多条 expression，episode block 一次可含多个 episode。若不做去重，一次 prompt 注入会为同一条 expression/episode 产生多条 observation，命中数字虚高。 | `StyleProvider.provide` 生成 profile 和 expression 两类 block。`EpisodeProvider` 的 `evidence_refs` 才含 episode_id 列表。 | 观测表增加唯一约束：`UNIQUE(expression_id, message_id, trigger_type)` / `UNIQUE(episode_id, message_id, trigger_type)`。无 message_id 时 fallback 为 prompt request_id。 |
| C5 | **中** | `/api/admin/learning/today` 已存在并服务 Dashboard 今日学习模块，新 `/api/admin/learning/pipeline` 可能与它路径或职责冲突。 | `admin/routes/api/__init__.py:153` `router.include_router(create_learning_router(ctx=ctx))`。`admin/routes/api/learning.py` 有 `/learning/today`。 | 新接口可放同文件或新文件，但必须保留 `/learning/today` 的响应结构和测试。建议新接口放 `admin/routes/api/learning_pipeline.py` 独立文件。 |
| C6 | **低** | §8 说"阶段 0 是纯 Drawer 抽出"，但 style 没有 Drawer、episodes/consolidator 审核逻辑在主视图内。实际工作比"纯结构提取"重。 | 见 C2 证据。 | 阶段 0 改名为"审核组件盘点 + 接口口径对齐"，先产出盘点表再动手。 |

### 18.4 已确认可行项

| 确认项 | 核实结果 |
| --- | --- |
| `/learning` 路由可加入现有 SPA router | `admin/frontend/src/router/index.ts` 已含 `/slang`、`/style`、`/episodes`、`/memory` 等路由，新增无冲突 |
| StageStrip 与现有组件体系相容 | `AppPage`、`AppPanelSection`、`StateBadge` 等均已在多个视图复用 |
| `/admin/learning` URL 前缀 | 后端 router 统一挂载在 `/api/admin` 下，前端 SPA 内路由为 `/learning`，实际访问为 `/admin/learning`，与现有 12 项一致 |
| slang 审核 Drawer 已独立 | `SlangDetailDrawer.vue` 已存在，可直接被 UnifiedReviewDrawer 挂载 |
| API 注册入口正确 | `admin/routes/api/__init__.py` 已 include learning router |
| consolidator 前缀为下划线 | `/memory_consolidator` 而非 `/memory-consolidator` |
| Dashboard 深链可直接改 | `DashboardView.vue` 已有 `goTo('/slang')` / `goTo('/style')`，改为 `/learning?stage=review&noun=slang` 是 1 行替换 |

### 18.5 建议修订后的 PR 顺序

| PR | 内容调整 |
| --- | --- |
| 1 | **"准备：审核组件盘点 + 接口口径对齐 + pipeline 只读聚合"**。先产出 4 noun 的审核 UI 盘点表 + memory 数据源确认 |
| 2 | **"观测表迁移"** 改为 provider_bus active 路径写入，含去重约束和 provider 单测 |
| 3 | 前端骨架：`/learning` 路由 + StageStrip 只读 |
| 4 | 列表：后端 `/items` + LearningTable（memory 口径对齐后纳入） |
| 5 | 审核 + 折返 + extract-all（若风险高可拆 5a/5b） |
| 6 | SideMenu + Dashboard 深链同步 + 上线 |

### 18.6 必改文档清单

进入 PR 前先修订以下段落：

1. **§15.4 全文**：删除 `.md` 口径，改为 CardStore 或改为不纳入（Q4 重新选择）
2. **§15.3.3 全文**：写入点从 `injector.py` 改为 `StyleProvider.provide` / `EpisodeProvider.provide`，补去重策略
3. **§6.3 / §12**：API 前缀、注册文件、consolidator 路由全部对齐实现
4. **§6.4**：明确 `push` vs `replace` 使用边界
5. **§6.3 + §15.2**：`extract-all` 接口定义统一（删除"不新增写接口"声明，展开并发风险）
6. **§8 阶段 0**：改为"审核组件盘点"，不称"纯 Drawer 抽出"

---

## 19. v2 修订条目（落地版，覆盖 §15）

> 本节是 **v1 → v2 的实施口径修订**，由用户决议（2026-05-23）+ gpt 审计 + deepseek 审计共同驱动。
> **§15 保留为历史记录；以下条目为冲突时的权威定义。**
> 进入 PR 实施时，请按 §19 + §20（修订后 PR 顺序）执行，§5/§6/§8/§12/§15 中与本节冲突的描述以本节为准。

### 19.0 修订纲要

| 修订点 | 来源 | 原 v1 口径 | v2 口径 | 影响 |
| --- | --- | --- | --- | --- |
| Q4 细化 | 用户决议 | A：纳入 `/memory`，扫 `.md` | **A2**：纳入但只读，按 `memory_cards.status` 映射 | §15.4 重写 |
| Memory 数据源 | B1 | `_storage/memories/*.md` + pending 块 | `memory_cards` 表 | §15.4 重写 |
| Style/Episode 命中写入点 | B2 | `services/style/injector.py`、`services/episodic/injector.py`（不存在） | `services/block_trace/style_provider.py`、`services/block_trace/episode_provider.py` 的 `provide()` | §15.3.3 重写 |
| API 注册文件 | B3 | `admin/__init__.py` | `admin/routes/api/__init__.py` | §12 修正 |
| Consolidator 路由前缀 | B3 | `/memory-consolidator` | `/memory_consolidator`（下划线） | §6.3 修正 |
| URL 历史栈语义 | B4 | 全部 `router.replace` | 阶段/noun 切换 `push`，刷新/分页 `replace` | §6.4 修正 |
| extract-all 编排语义 | C1 | 「不新增写接口」+ 又新增 | **明确为编排接口**，含并发锁 + 部分失败 schema | §6.3 + §15.2 修正 |
| 审核组件抽象 | C2 | UnifiedReviewDrawer 假设 4 noun 同构 | **审核组件盘点表**先行；style 不强抽 Drawer | §15.1 + §8 修正 |
| 时间过滤口径 | C3 | 全局控件，但只过滤 candidate/review | 阶段卡=全量库存（不受时间影响）；列表受 date 过滤；命中固定 today | §5.5 + §6.1 |
| 命中去重 | C4 | 无去重约束 | observations 表加 UNIQUE(target_id, message_id, trigger_type) | §15.3 |
| `/learning/today` 兼容 | C5 | 未提及兼容 | 新接口走 `learning_pipeline.py` 独立文件，保留 today 接口与测试 | §6.1 + §12 |
| Q3-B 实施门槛 | gpt + deepseek | 直接补观测表 | **观测写入异步 fire-and-forget** + provider_bus active 路径单测 | §15.3 强化 |

### 19.1 Q4=A2 · Memory 只读纳入（重写 §15.4）

#### 19.1.1 数据源真相

- **核实结果**：`admin/routes/api/memory.py` 全部接口走 `/memory/cards`；`services/memory/card_store.py` 的 `memory_cards` 表 status 默认 `'active'`；现有 status 值：`'active'` / `'expired'` / `'superseded'`，**无 `'pending'`**
- **结论**：CardStore 现状不支持「候选」语义；不动 CardStore 核心存储的前提下，memory 在 `/learning` 中只能做**只读视角**

#### 19.1.2 阶段映射

| `/learning` 阶段 | memory 数据来源 | 行为 |
| --- | --- | --- |
| 候选（candidate） | _无_ | 在阶段卡上对 memory noun 显示 `--`，不计入候选总数 |
| 待审（review） | _无_ | 同上 |
| 入库（approved） | `memory_cards.status='active'` count | 阶段卡显示 active 卡片数；筛选 noun=memory 时列表展示活跃卡片 |
| 命中（hits） | _无观测语义_ | 显示 `--`（memory 是 prompt 全注入，无逐条 hit 计数） |
| 归档（archived） | `memory_cards.status='expired'` count | 阶段卡显示过期卡片数 |

#### 19.1.3 行内操作

- **无审核按钮**（无 candidate）
- **无折返按钮**（不在 `/learning` 内做 status 翻转，避免越界改 CardStore）
- **仅「详情」深链**：跳 `/memory?card_id={id}`（需 `MemoryView` 支持该 query 参数滚到对应卡）
- 折返通过 `/memory` 原页面完成，不在 `/learning` 内重复

#### 19.1.4 §6.1 聚合接口响应（修订）

```json
"by_noun": {
  ...
  "memory": {
    "candidate": null,    // 显式 null，非 0
    "review": null,
    "approved": 17,       // status='active' count
    "hits": null,
    "archived": 5         // status='expired' count
  }
}
```

前端遇 `null` 渲染为 `--`，不渲染为 `0`，避免与「真的 0 条」混淆。

### 19.2 阻塞 B2 · Style/Episode 命中写入点（重写 §15.3.3）

#### 19.2.1 真实写入点（grep 验证）

| Noun | 文件 | 函数 | 触发条件 |
| --- | --- | --- | --- |
| style | `services/block_trace/style_provider.py` | `StyleProvider.provide(ctx)` | 该 provider 产出 `PromptBlockCandidate` 且最终被 `provider_bus` 选中注入 prompt |
| episode | `services/block_trace/episode_provider.py` | `EpisodeProvider.provide(ctx)` | 同上 |

**关键**：`provider_bus.mode='active'` 时，`plugins/style/plugin.py` 的 `_provider_superseded=True` 会跳过 `on_pre_prompt` —— 必须在 provider 路径写入。

#### 19.2.2 同模式扫描结果（D1 要求）

```text
services/slang/store.py:
  - record_observation(...)        # 公共 API
  - 行 1035 / 1075 / 1280 / 1412 / 1584  # 调用点（slang_provider 等多处）
services/block_trace/slang_provider.py:
  - 已调用 SlangStore.record_observation
```

仿此模式，新增：

- `services/style/store.py` 增加 `record_observation(expression_id, message_id, trigger_type, group_id, scope, meta)`
- `services/episodic/store.py` 增加 `record_observation(episode_id, message_id, trigger_type, group_id, scope, meta)`
- `services/block_trace/style_provider.py` 在 `provide()` 末尾、产出 PromptBlockCandidate 之后调用 `style_store.record_observation`
- `services/block_trace/episode_provider.py` 同理

#### 19.2.3 异步写入要求（C4 + 性能）

写入必须 **fire-and-forget**：

```python
asyncio.create_task(
    style_store.record_observation(...)
)
```

理由：`provide()` 是 prompt 构建热路径，DB 写延迟不能阻塞 LLM 调用。失败仅日志告警，不抛出。

#### 19.2.4 表 schema（修订 §15.3.1 / §15.3.2）

```sql
CREATE TABLE IF NOT EXISTS style_observations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  expression_id TEXT NOT NULL,            -- 单条 expression（profile block 拆分写多条）
  scope         TEXT NOT NULL,            -- 'group' | 'global'
  group_id      TEXT NOT NULL DEFAULT '',
  observed_at   TEXT NOT NULL,
  trigger_type  TEXT NOT NULL,            -- 'profile_inject' | 'expression_inject'
  message_id    TEXT NOT NULL DEFAULT '', -- request_id fallback when message_id absent
  meta          TEXT NOT NULL DEFAULT '{}',
  UNIQUE(expression_id, message_id, trigger_type) ON CONFLICT IGNORE
);
CREATE INDEX IF NOT EXISTS idx_style_obs_today ON style_observations(observed_at, expression_id);
CREATE INDEX IF NOT EXISTS idx_style_obs_scope ON style_observations(scope, group_id, observed_at);

CREATE TABLE IF NOT EXISTS episode_observations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id    TEXT NOT NULL,            -- evidence_refs 中每个 episode_id 拆分一条
  scope         TEXT NOT NULL,
  group_id      TEXT NOT NULL DEFAULT '',
  observed_at   TEXT NOT NULL,
  trigger_type  TEXT NOT NULL,            -- 'episode_inject' | 'reflection_cite'
  message_id    TEXT NOT NULL DEFAULT '',
  meta          TEXT NOT NULL DEFAULT '{}',
  UNIQUE(episode_id, message_id, trigger_type) ON CONFLICT IGNORE
);
CREATE INDEX IF NOT EXISTS idx_episode_obs_today ON episode_observations(observed_at, episode_id);
CREATE INDEX IF NOT EXISTS idx_episode_obs_scope ON episode_observations(scope, group_id, observed_at);
```

**去重策略（C4）**：UNIQUE 约束防止 profile block 一对多导致 hits 虚高；冲突静默忽略（同一 prompt 重复 inject 同一 expression 只算一次）。

#### 19.2.5 单测要求

`tests/test_style_provider_observations.py`、`tests/test_episode_provider_observations.py`：

- provider_bus active 路径下 `provide()` → observation 落库
- profile block 含 3 条 expression → 3 条 observation
- 同 expression 同 message_id 重复 → UNIQUE 生效，仍 1 条
- DB 写失败（mock 抛异常）→ provide 不抛，仅 log warning

### 19.3 阻塞 B3 · API 注册路径修正（修订 §6.3 / §12）

#### 19.3.1 路由前缀真相

| 接口 | v1 文档写法 | 实际 / v2 修正 |
| --- | --- | --- |
| memory_consolidator | `/api/admin/memory-consolidator` | `/api/admin/memory_consolidator`（下划线） |
| learning pipeline 新接口 | 文件位置不明 | **新建 `admin/routes/api/learning_pipeline.py`**（与 `learning.py` 并列，保留 `/learning/today`） |
| 注册入口 | `admin/__init__.py` | `admin/routes/api/__init__.py` 加 `from admin.routes.api.learning_pipeline import create_learning_pipeline_router` |

#### 19.3.2 修订后接口清单

| 路径 | 文件 | 用途 |
| --- | --- | --- |
| `GET /api/admin/learning/today` | `admin/routes/api/learning.py`（保留） | Dashboard 今日学习模块（不动） |
| `GET /api/admin/learning/pipeline` | `admin/routes/api/learning_pipeline.py`（新增） | StageStrip 5 阶段计数 |
| `GET /api/admin/learning/items` | 同上（新增） | LearningTable 列表 |
| `POST /api/admin/learning/extract-all` | 同上（新增） | 编排接口，详见 §19.5 |

### 19.4 阻塞 B4 · URL 历史栈语义（修订 §6.4）

| 操作 | 方法 | 进 history? |
| --- | --- | --- |
| 切换阶段（Tab） | `router.push` | ✅ |
| 切换 noun | `router.push` | ✅ |
| 时间筛选切换 | `router.push` | ✅ |
| 列表分页/排序 | `router.replace` | ❌ |
| 轮询自动刷新 / count 数字更新 | 不动 URL | — |
| 内联审核 Drawer 开关 | 不动 URL | — |

理由：阶段/noun 是用户期待的「页面状态」，应可前进/后退；列表细粒度交互不应污染 history。

### 19.5 条件 C1 · extract-all 编排接口（修订 §6.3 + §15.2）

#### 19.5.1 性质

`POST /api/admin/learning/extract-all` 是**编排接口**，不是新业务接口。它内部并发调用现有 4 个抽取入口：

| Noun | 实际调用目标 |
| --- | --- |
| slang | `POST /api/admin/slang/extract/run`（内部直接调 SlangPlugin.run_manual_extract） |
| style | `POST /api/admin/style/extract/run` |
| episode | _无独立 extractor；跳过此 noun 或调 reflection_ |
| memory_consolidator | `POST /api/admin/memory_consolidator/runs` 或 `/reflect` |

#### 19.5.2 并发与失败语义

```python
async def extract_all() -> dict:
    if not _try_acquire_lock():
        return {"ok": False, "error": "already_running", "lock_held_by": _lock_holder}

    tasks = {
        "slang":        asyncio.create_task(_run_slang()),
        "style":        asyncio.create_task(_run_style()),
        "consolidator": asyncio.create_task(_run_consolidator()),
        # episode skipped: no manual extractor
    }
    results = {}
    for noun, task in tasks.items():
        try:
            results[noun] = await asyncio.wait_for(task, timeout=120)
        except Exception as e:
            results[noun] = {"ok": False, "error": str(e)}

    _release_lock()
    return {"ok": True, "results": results}
```

- **进程级锁**：`_extract_all_lock = asyncio.Lock()` 模块级，防止 double-fire
- **超时**：每个 noun 单独 120s，单 noun 卡死不拖累其余
- **部分失败**：单 noun 失败不影响其余；返回 `{"results": {"slang": {"ok": true, "run_id": "..."}, "style": {"ok": false, "error": "..."}}}`
- **NPopconfirm 文案**：
  > 将并发触发 3 类抽取（黑话 / 风格 / 记忆候选），可能与各自定时任务并发，消耗较多 LLM 配额。是否继续？

#### 19.5.3 §6.3「不新增写接口」声明作废

v1 §6.3 曾写「全部复用现有路由，不新增」；v2 明确声明：**`extract-all` 是编排接口，属于新增写接口**。这与"不新建业务表/不新建业务路由"的原则不冲突——它仅做调用编排。

### 19.6 条件 C2 · 审核组件盘点（修订 §15.1 + §8）

#### 19.6.1 现状盘点（PR 1 的硬产出）

| Noun | 现有审核 UI 形态 | 复用难度 |
| --- | --- | --- |
| slang | `SlangView.vue` 引入 `SlangDetailDrawer.vue`（Drawer + 字段编辑） | **低**：Drawer 已独立，可直接挂载 |
| style | `StyleView.vue` 列表内按钮直接 PATCH `/style/expressions/{id}/status` | **中**：需新建轻量 `StyleReviewPanel.vue`（非 Drawer） |
| episode | `EpisodesView.vue` 详情/审核逻辑在主视图内 | **高**：需先抽出 `EpisodeReviewPanel.vue` |
| memory_consolidator | `MemoryConsolidatorView.vue` 同 episode | **高**：同上 |
| memory | _无审核_ | _N/A_ |

#### 19.6.2 PR 1 调整

PR 1 不再叫"纯 Drawer 抽出"，改名 **「审核组件盘点 + 接口口径对齐 + pipeline 只读聚合」**：

1. 产出本节 19.6.1 表的代码侧确认（grep 验证每条声明）
2. 修订 §6.1 接口契约（已在本节完成）
3. 实现 `/learning/pipeline` 只读聚合接口
4. **不抽 Drawer**，留给后续 PR

#### 19.6.3 PR 5 调整（审核 + 折返）

不强求 `<UnifiedReviewDrawer>` 一统四 noun。改为**多态 review 容器**：

```vue
<!-- LearningReviewHost.vue -->
<NDrawer v-model:show="open" :width="reviewer.drawerWidth ?? 480">
  <component :is="reviewer.component" v-bind="reviewer.props" @done="onDone" />
</NDrawer>
```

`reviewer` 由当前选中 row 决定：

| Row 来源 | reviewer.component |
| --- | --- |
| slang | `SlangDetailDrawer`（已存在，直接复用） |
| style | `StyleReviewPanel`（PR 5 新建） |
| episode | `EpisodeReviewPanel`（PR 5 新建） |
| consolidator | `ConsolidatorReviewPanel`（PR 5 新建） |
| memory | _无（行只有"详情"）_ |

避免「为统一抽象牺牲已有差异」。

### 19.7 条件 C3 · 时间过滤口径（修订 §5.5 + §6.1）

| 控件 | 是否受时间过滤 |
| --- | --- |
| 顶部 5 阶段卡（candidate / review / approved / hits / archived 计数） | ❌ **全量库存**，不随时间筛选变化 |
| 命中阶段卡数字 | ✅ 固定 today 一条口径，不随筛选切换 |
| 列表（LearningTable） | ✅ candidate/review 受 `date` 过滤；approved/archived 受 `date` 过滤 created_at；hits 固定 today |
| Hero 上方今日 KPI | ✅ 固定 today |

UI 文案：阶段卡 hover 提示「全量库存」；列表筛选区写「时间筛选仅影响列表，不影响阶段计数」。避免双口径误读。

### 19.8 条件 C5 · `/learning/today` 兼容

- **不动** `admin/routes/api/learning.py` 的 `/learning/today`（Dashboard 依赖）
- 新接口全部进 `admin/routes/api/learning_pipeline.py`
- 兼容性单测：`tests/test_admin_api_learning_today_compat.py` 校验响应 schema 不变

### 19.9 条件 C6 · 阶段 0 改名

§8 阶段 0 改为 **「审核组件盘点 + 接口口径对齐」**，产出物：

1. §19.6.1 表的最终版（每条带文件路径 + 行号验证）
2. `/learning/pipeline` 只读接口落地
3. memory CardStore 数据源确认（status 真实值清单）

不在阶段 0 抽 Drawer。

---

## 20. v2 修订后 PR 顺序（覆盖 §15.7 / §16）

| PR | 名称 | 内容 | 工程量 | 风险 |
| --- | --- | --- | --- | --- |
| 1 | 准备：审核盘点 + 接口对齐 + pipeline 只读 | §19.6.1 表 + `/learning/pipeline` 只读 + memory CardStore 数据源确认 + 单测 | 1.5 天 | 中 |
| 2 | 观测表迁移 | style/episode_observations + provider 路径 fire-and-forget 写入 + UNIQUE 约束 + provider_bus active 单测 | 1.5 天 | 中（热路径） |
| 3 | 前端骨架 | `/learning` 路由 + StageStrip + 接通 `/pipeline` 只读 + URL push/replace 边界 | 1 天 | 低 |
| 4 | 列表 | 后端 `/learning/items` + LearningTable + memory 只读视图 | 1 天 | 低 |
| 5 | 审核 + 折返 + extract-all | LearningReviewHost 多态容器 + 新建 3 个 ReviewPanel + extract-all 编排 + 进程锁 | 1.5 天 | 中 |
| 6 | SideMenu + Dashboard 深链 + 上线 | SideMenu 加项 + Dashboard 4 处 pendingItems 改深链 + 手测 golden path | 0.5 天 | 低 |

**总工程量：~7 工作日 / 6 PR**（v1: 5 天 → v2 含 B1/B2 修订 + C2 拆 panel 后约 7 天，工程量增长 40%，但避免 4 个 BugPR）。

每 PR 仍独立可 revert。`§16 实施日志`继续作为回写表，PR 落地后填。

---

## 21. v2 验证矩阵（覆盖 §9）

新增/调整的关键验证点：

| 验证项 | 方法 | 来源 |
| --- | --- | --- |
| memory CardStore 真实 status 值清单 | `sqlite3 storage/memory_cards.db "SELECT DISTINCT status FROM memory_cards;"` | B1 |
| memory 阶段卡 candidate/review/hits 渲染 `--` 而非 `0` | 前端单测 + 视觉确认 | A2 |
| StyleProvider observation 写入 | `tests/test_style_provider_observations.py` | B2 + C4 |
| EpisodeProvider observation 写入 | `tests/test_episode_provider_observations.py` | B2 + C4 |
| profile block 3 expression → 3 obs（不是 1） | 同上 | C4 |
| 同 (expression_id, message_id, trigger_type) 重复 → 仍 1 条 | 同上 | C4 |
| provider observation 写失败 → provide() 不抛 | 同上（mock store 抛 OSError） | 性能 |
| `/learning/today` 兼容性 | `tests/test_admin_api_learning_today_compat.py` | C5 |
| `extract-all` 进程锁 | 并发调用两次 → 第二次返回 `already_running` | C1 |
| `extract-all` 部分失败 | mock 单 noun 抛异常 → 其余仍返回 ok | C1 |
| URL push/replace 边界 | 手测：切阶段后浏览器后退能回 | B4 |
| 阶段卡不受时间筛选 | 手测：切 7d/30d → 阶段数字不变；列表数字变 | C3 |
| Dashboard 深链 4 处 | 手点 4 个 pendingItems → 落到 `/learning?stage=review&noun=x` 对应位置 | Q5 |

---

## 22. v2.1 修订条目（执行风险审查后修订）

> 本节是 **v2 → v2.1** 的第三轮修订，触发于 2026-05-23 第三方代码侧执行风险审查。
> 审查发现 5 项执行风险（1 阻塞 / 2 高 / 2 中），全部已核实属实。
> **§22 与 §19 冲突时以 §22 为准**（审查覆盖更新的代码事实）。

### 22.0 修订纲要

| # | 严重度 | 来源 | 原 v2 口径 | v2.1 口径 |
| --- | --- | --- | --- | --- |
| F1 | **阻塞** | 命中记录点错位（services/llm/client.py:2017-2029） | provider.provide() 末尾写 observation | **改在 PromptBudgetManager.process() 内**：accepted 写正常 trigger；trimmed 写 `*_trimmed` 后缀 trigger（L3 已在 v2.1 一并落地，详见 §22.1.3）；rejected 不计 |
| F2 | **高** | candidate→PromptBlock 丢 source refs（provider_bus.py:95） | UNIQUE 用 (expression_id, message_id, trigger_type) | **PR2 前先扩 PromptBlock 携带 source_ids，或 budget manager 改吃 PromptBlockCandidate** |
| F3 | **高** | `/memory?card_id=` 不被支持（MemoryConsoleView.vue:14-24） | `/memory?card_id={id}` 深链 | 改 `/memory?view=manage&card_id={id}` + MemoryView 实现定位/打开 Drawer，**或行只跳页不承诺定位** |
| F4 | **中** | 响应 schema 反掉（§6.1 vs §19.1.4） | `by_noun.memory = {stage:value}` 对象 | **保持逐 stage 标量**：每个 stages[stage].by_noun.memory 是 number 或 null |
| F5 | **中** | extract-all 锁释放脆 | 顺序 wait_for + 手动 release | **try/finally 释放锁** + per-noun `_run_with_timeout` 封装 + `gather(return_exceptions=True)` |

### 22.1 F1（阻塞）· 命中只对 accepted 块计入

#### 22.1.1 真实链路（grep 验证）

```text
services/llm/client.py:2017-2029
  bus.run_active(qctx)            # 产 PromptBlock 列表
    └─ provider.provide() 已被调用
  prompt_ctx.blocks.extend(provider_blocks)
  budget_manager.process(blocks)  # 这里才决定 accepted/trimmed/rejected
```

**结论**：在 `provide()` 末尾写 observation = 给被裁掉的也记 hit，命中数虚高。**§19.2.2 / §19.2.3 的 fire-and-forget 写入点作废**。

#### 22.1.2 v2.1 写入点

观测必须在 `PromptBudgetManager.process()` 内、且仅对 `decision == "accepted"` 的 block 写入：

```python
# services/block_trace/budget_manager.py
for position in ("static", "stable", "dynamic"):
    ...
    for b in buckets[position]:
        ...
        if remaining >= char_count:
            decision = "accepted"
            surviving.append(b)
            used += char_count
            # ↓ v2.1 新增：accepted 才记 observation
            self._fire_observation(b, request_id=request_id, group_id=group_id)
        elif remaining > 0:
            decision = "trimmed"
            # trimmed: 仍写 observation，但 trigger_type 加 `_trimmed` 后缀
            #   （v2.1 决议见 22.1.3，L3 已在本期一并落地）
            ...
        else:
            decision = "rejected"
            # rejected: 不记
            ...
```

`_fire_observation` 根据 `b.source` 路由到对应 store：

| `block.source` | 调用 |
| --- | --- |
| `"slang"` | 不动（已有 SlangProvider 内部记，**v2.1 不双写**——见 22.1.4） |
| `"style"` | `style_store.record_observation(...)` 异步 task |
| `"episode"` | `episode_store.record_observation(...)` 异步 task |
| 其他 | 跳过 |

#### 22.1.3 trimmed 是否计入

**v2.1 决议**：**计入，但带 `_trimmed` 后缀的 trigger_type**（`profile_inject_trimmed` / `expression_inject_trimmed` / `episode_inject_trimmed` / `prompt_inject_trimmed:`）。

> **执行口径变更**（自 L3 起）：原 v2.1 草案第一版口径为「trimmed 不计入」，理由是 trimmed block 文本被截短、下游 LLM 可能无法识别完整语义。但实施时发现：trimmed 是 **预算压力下的真实"曝光"**，丢弃它会让 hits 阶段无法追踪「资源紧张时哪些 noun 受挤压」这一关键信号。L3 决议改为**计入但打上 `_trimmed` 后缀**，前端可以通过 trigger_type 区分「完整命中」vs「裁剪命中」，hits 阶段保持真实预算压力可观测。
>
> rejected（预算耗尽完全不入 prompt）仍**不计入**。

后续若发现 `_trimmed` observation 噪声过大，可在前端增加默认折叠或筛选项，无需回退表结构。

#### 22.1.4 slang 现状不双写

slang 已在 `SlangProvider`（services/block_trace/slang_provider.py）内部调用 `SlangStore.record_observation`，但同样存在「provide 写 = 把被裁的也记」的隐患。**v2.1 范围内不动 slang**（避免一次改太多），但记入 §22.6 的「后续待办」让下次同模式扫描带上。

#### 22.1.5 单测调整

`tests/test_style_provider_observations.py` / `tests/test_episode_provider_observations.py` 改为：

- 测试目标改为 `PromptBudgetManager.process()`
- accepted block → 1 条 observation（trigger_type 不加后缀）
- trimmed block → **1 条** observation，trigger_type 加 `_trimmed` 后缀（v2.1 + L3 联合决议，见 22.1.3）
- rejected block → 0 条 observation
- mock store 抛异常 → process 不抛，仅 log warning

### 22.2 F2（高）· source refs 链路

#### 22.2.1 现状（grep 验证）

| 类型 | 字段 |
| --- | --- |
| `PromptBlockCandidate`（services/block_trace/types.py:20） | `candidate_id`, `source`, `provider`, `evidence_refs`, `metadata`, ... |
| `PromptBlock`（kernel/types.py） | 仅 `text`, `label`, `position`, `priority`, `source`, `provider` |
| `provider_bus.run_active`（services/block_trace/provider_bus.py:95） | candidate → PromptBlock 时丢 `evidence_refs` |
| `PromptBlockTrace.evidence_refs` 在 budget_manager.py:105 | 写死 `()` 空元组 |

**问题**：budget manager 拿到 PromptBlock 时已经无法回溯到「这块是哪条 expression / episode」，§22.1.2 的 `_fire_observation` 没法工作。

#### 22.2.2 v2.1 解法（PR 2 必须先做）

**方案 A（推荐）**：让 budget manager 直接吃 `PromptBlockCandidate`，输出 `(surviving_blocks, accepted_decisions)`。

```python
# services/block_trace/budget_manager.py
def process(
    self,
    candidates: list[PromptBlockCandidate],   # ← 改入参
    *,
    request_id: str,
    ...
) -> tuple[list[PromptBlock], list[AcceptedDecision]]:
    ...
    accepted_decisions = [
        AcceptedDecision(
            source=c.source,
            evidence_refs=c.evidence_refs,
            metadata=c.metadata,
            char_count=c.char_count,
        )
        for c in candidates
        if decisions[c.candidate_id] == "accepted"
    ]
    return surviving, accepted_decisions
```

调用侧（services/llm/client.py:2017）调整：

```python
provider_candidates = await bus.run_all(qctx)  # 不再 run_active
surviving, accepted = self._budget_manager.process(provider_candidates, ...)
prompt_ctx.blocks.extend(surviving)
self._record_accepted_observations(accepted, request_id=req_id, group_id=group_id)
```

**方案 B（备选）**：扩展 `PromptBlock` 加 `evidence_refs: tuple[str, ...] = ()`。改动面更大（kernel.types 公共类型），但兼容性好。

**v2.1 选 A**：改动局限在 services/block_trace/* 与 client.py 单点；PromptBlock 公共类型不动，前端无影响。

#### 22.2.3 PR 切片调整

PR 2 拆为：

- **PR 2a（前置）**：`PromptBudgetManager.process` 改吃 PromptBlockCandidate，返回 (blocks, accepted_decisions)；调用侧 client.py 同步改；保持 trace 行为不变
- **PR 2b**：style/episode `record_observation` + observations 表迁移 + 在 process 内对 accepted 调用

PR 2a 是纯重构，不引入新表；PR 2b 才上业务。**两者必须按序合入**，不能并行。

#### 22.2.4 PromptBlockCandidate 字段映射

| Provider | 必须填 | 用途 |
| --- | --- | --- |
| StyleProvider | `evidence_refs=(expression_id, ...)` 单 expression block；profile block 写多个 expression_id | 用于 process 阶段写多条 observation |
| EpisodeProvider | `evidence_refs=(episode_id, ...)` 单 episode block；多 episode block 写多个 episode_id | 同上 |

**确认**：`provide()` 内必须填 `evidence_refs`。PR 2a 前置先 grep 现状是否填了，没填的 provider 在 PR 2a 顺手补。

### 22.3 F3（高）· memory 深链

#### 22.3.1 现状（验证）

`MemoryConsoleView.vue:14-24` 用 `watchEffect` 在 view 不是 manage|browse 时 `router.replace({ name: 'memory', query: { view: 'browse' } })`——**整个 query 重置为只剩 view，card_id 会被丢弃**。即使加上 `view=manage`，下层 MemoryView.vue 也未实现 card_id 定位逻辑。

#### 22.3.2 v2.1 决议

**采用「行只跳页不承诺定位」**（保守路径）：

| 入口 | v2 写法 | v2.1 写法 |
| --- | --- | --- |
| `/learning` memory 行「详情」按钮 | `/memory?card_id={id}` | `/memory?view=manage`（不带 card_id） |
| 前端文案 | 「跳转到该卡片」 | 「在记忆页打开管理视图」 |

**理由**：v2.1 范围 6 PR / 7 天预算内，新增「MemoryView 实现 card_id 定位 + 打开 Drawer」是单独工作（涉及 MemoryView 的列表滚动、目标卡高亮、Drawer 路由感知 3 处改动）。把它**降级到 §22.6 后续 PR**，本期不交付。

如果用户后续要求精确定位，再单独开 PR：在 `MemoryConsoleView.vue` 把 `query: { view: 'browse' }` 改为 merge（`{ ...route.query, view: 'browse' }`），并让 `MemoryView` 监听 `route.query.card_id` 滚到对应 card + 自动打开 Drawer。

#### 22.3.3 §19.1.3 / §19.1.4 修正

§19.1.3 第 3 条「`/memory?card_id={id}`」→ **改为「`/memory?view=manage`」**。
§19.1.4 不变（response schema 见 §22.4）。

### 22.4 F4（中）· response schema 反掉

#### 22.4.1 现状（验证）

§6.1（v1 原契约，line 309-335）：

```json
{
  "stages": {
    "candidate": { "total": 23, "by_noun": { "slang": 12, "style": 8, ... } },
    "review":    { "total": 18, "by_noun": { "slang": 14, "style": 4, ... } },
    "approved":  { "total": 847, "by_noun": { ... } },
    "hits":      { "total": 76, "by_noun": { "slang": 76, "style": null, "episode": null } },
    "archived":  { "total": 12, "by_noun": { ... } }
  }
}
```

**结构**：`stages[stage].by_noun[noun] = number | null`。

§19.1.4（v2 写法）：

```json
"by_noun": {
  "memory": { "candidate": null, "approved": 17, "archived": 5, ... }
}
```

**结构反了**：noun 在外、stage 在内，前端按 v1 契约取 `stages.candidate.by_noun.memory` 会拿到对象而不是数字。

#### 22.4.2 v2.1 修正

`stages[stage].by_noun.memory` 仍然是标量（number 或 null），按 stage 分散填：

```json
{
  "stages": {
    "candidate": { "by_noun": { "memory": null, "slang": 12, ... } },
    "review":    { "by_noun": { "memory": null, ... } },
    "approved":  { "by_noun": { "memory": 17, ... } },
    "hits":      { "by_noun": { "memory": null, ... } },
    "archived":  { "by_noun": { "memory": 5, ... } }
  }
}
```

§19.1.4 的对象写法**整段作废**。前端单测验：`stages.candidate.by_noun.memory === null && stages.approved.by_noun.memory === 17`。

### 22.5 F5（中）· extract-all 锁稳定性

#### 22.5.1 v2.1 重写

```python
# admin/routes/api/learning_pipeline.py
import asyncio
from typing import Any

_extract_all_lock = asyncio.Lock()
_NOUN_TIMEOUT = 120  # seconds per noun

async def _run_with_timeout(noun: str, coro) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(coro, timeout=_NOUN_TIMEOUT)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout", "noun": noun}
    except asyncio.CancelledError:
        # 关键:被取消时仍要返回结果,不能让异常逃出 gather
        return {"ok": False, "error": "cancelled", "noun": noun}
    except Exception as e:
        return {"ok": False, "error": str(e), "noun": noun}

async def extract_all() -> dict[str, Any]:
    if _extract_all_lock.locked():
        return {"ok": False, "error": "already_running"}

    async with _extract_all_lock:                      # ← 用 async with,保证释放
        nouns = {
            "slang":        _run_slang(),
            "style":        _run_style(),
            "consolidator": _run_consolidator(),
        }
        results = await asyncio.gather(
            *(_run_with_timeout(name, coro) for name, coro in nouns.items()),
            return_exceptions=False,                   # 已经被 _run_with_timeout 收住
        )
        return {
            "ok": True,
            "results": dict(zip(nouns.keys(), results, strict=True)),
        }
```

关键改动：

1. `async with _extract_all_lock` 替代手动 `try_acquire / release`——请求中途取消、未捕获异常都能保证锁释放
2. 每个 noun 自包 timeout，并发执行（gather）替代顺序 wait_for，单 noun 卡死不阻塞其他启动
3. `_run_with_timeout` 自包所有异常（含 `CancelledError`），保证 gather 拿到 `dict` 而不是 raise
4. `return_exceptions=False` 因为 `_run_with_timeout` 已经把异常转成结果

#### 22.5.2 单测调整

`tests/test_admin_api_learning_extract_all.py`：

- 并发调用 2 次 → 第二次返回 `already_running`
- 第一次完成后（锁释放）再调用 → 正常
- 单 noun mock 抛 RuntimeError → 该 noun 结果 `{"ok": False}`，其余仍 `ok=True`
- 单 noun 模拟 sleep(200) → 该 noun 结果 `{"error": "timeout"}`，其余正常返回（gather 不被卡 200s）
- 整个 extract_all coroutine 被外部 cancel → 锁仍释放（验证 async with 行为）

### 22.6 后续待办（不进 v2.1 PR，2026-05-23 已完成）

| 编号 | 项 | 状态 | 触发条件 / 完成证据 |
| --- | --- | --- | --- |
| L1 | slang observation 改在 budget manager accepted 后写（去除 SlangProvider 内部直接 record） | 已完成 | 见 `docs/tracking/learning-pipeline-execution.md` L1 |
| L2 | MemoryView 支持 `?card_id=` 定位 + 自动打开 Drawer | 已完成 | 见 `docs/tracking/learning-pipeline-execution.md` L2 |
| L3 | trimmed block 也计 hits | 已完成 | 见 `docs/tracking/learning-pipeline-execution.md` L3 |
| L4 | extract-all 加进度 SSE / run_id 查询 | 已完成 | 见 `docs/tracking/learning-pipeline-execution.md` L4 |

---

## 23. v2.1 修订后 PR 顺序（覆盖 §20）

| PR | 名称 | 内容 | 工程量 | 风险 |
| --- | --- | --- | --- | --- |
| 1 | 准备：审核盘点 + 接口对齐 + pipeline 只读 | §19.6.1 表 + `/learning/pipeline` 只读 + memory CardStore 数据源确认 + response schema 按 §22.4 | 1.5 天 | 中 |
| **2a** | **budget manager 吃 candidate** | process 入参改 PromptBlockCandidate，返回 (blocks, accepted)；client.py 同步改；现有 trace 行为不变 | **1 天** | **中（热路径）** |
| 2b | 观测表 + accepted 写入 | style/episode `record_observation` + observations 表迁移 + UNIQUE + budget manager 内部对 accepted 触发 fire-and-forget 写 | 1 天 | 中 |
| 3 | 前端骨架 | `/learning` 路由 + StageStrip + URL push/replace 边界 | 1 天 | 低 |
| 4 | 列表 | `/learning/items` + LearningTable + memory 行只读跳 `/memory?view=manage` | 1 天 | 低 |
| 5 | 审核 + 折返 + extract-all | LearningReviewHost 多态容器 + 3 个 ReviewPanel + extract-all 按 §22.5.1 实现 | 1.5 天 | 中 |
| 6 | SideMenu + Dashboard 深链 + 上线 | SideMenu 加项 + Dashboard 4 处 pendingItems 改深链 + 手测 | 0.5 天 | 低 |

**总工程量：~7.5 工作日 / 7 PR**（v2: 7 天 / 6 PR → v2.1: 7.5 天 / 7 PR，+0.5 天源于 PR 2 拆 a/b）。

---

## 24. v2.1 验证矩阵增量（覆盖 §21）

| 验证项 | 方法 | 来源 |
| --- | --- | --- |
| accepted 块写 1 条 observation（trigger 不加后缀） | budget manager 单测，mock store | F1 |
| trimmed 块写 1 条 observation（trigger 加 `_trimmed` 后缀） | 同上（L3 落地后口径，见 §22.1.3） | F1 + L3 |
| rejected 块写 0 条 observation | 同上 | F1 |
| `provide()` 内不再直接写 observation | grep `record_observation` 调用点应只在 budget manager + slang_provider（L1 已折入 v2.1） | F1 |
| budget manager 入参为 PromptBlockCandidate | 类型注解 + pyright | F2 |
| `accepted_decisions[i].evidence_refs` 非空 | budget manager 单测 | F2 |
| StyleProvider 产出的 candidate.evidence_refs 含 expression_id | grep + 单测 | F2 |
| EpisodeProvider 产出的 candidate.evidence_refs 含 episode_id | 同上 | F2 |
| memory 行点击跳 `/memory?view=manage`（不带 card_id） | 前端单测 | F3 |
| `stages.{stage}.by_noun.memory` 是 number 或 null | API 响应 schema 单测 | F4 |
| extract-all 锁释放（async with） | 模拟 inner coroutine 抛异常，验证锁可再次获取 | F5 |
| extract-all 单 noun 卡死不影响其他 | mock 单 noun sleep(200)，gather 在 ~120s 内返回 | F5 |
| extract-all 被取消时锁释放 | 外部 cancel 整个 task，验证锁状态 | F5 |
