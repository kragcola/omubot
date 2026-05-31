# 学习管线统一化 — 派发追踪文档

> 状态：2026-05-28 建立  
> 前置审计：[audit-learning-pipeline-unification.md](audit-learning-pipeline-unification.md)  
> 约束：各 noun 后端逻辑处理方式不变，只更改前端显示与管理入口  
> 安全约束：不改 source.md / instruction.md；不改插件运行时钩子逻辑

---

## 0. 目标一句话

删去黑话/表达/关系等独立页面分割，Web 只保留 Learning Console 作为唯一入口；词条以标签显示便于统一管理；追加全局设置页面统一配置定时处理等功能。

---

## 1. 当前状态快照

### 1.1 路由现状（已部分统一）

| 旧路由 | 当前行为 | 目标 |
|--------|----------|------|
| `/slang` | redirect → `/learning?noun=slang` | 保留 redirect |
| `/style` | redirect → `/learning?noun=style` | 保留 redirect |
| `/episodes` | redirect → `/learning?noun=episode` | 保留 redirect |
| `/memory-consolidator` | redirect → `/learning?noun=memory` | 保留 redirect |
| `/cross-group` | redirect → `/learning?noun=slang&scope=cross` | 保留 redirect |
| `/memos` | redirect → `/memory?view=browse` | 保留 redirect（已合入 Memory Console） |
| `/affection` | redirect → `/memory?view=browse` | 保留 redirect（已合入 Memory Console） |
| `/learning` | **LearningView.vue**（1130 行） | 增强：加设置入口 + 标签系统 |

### 1.2 侧边栏菜单

```
学习与记忆（group）
├── 学习管道  → /learning
├── 知识库    → /knowledge
├── BlockTrace → /block-trace
└── 反事实重放 → /replay/weekly
```

Slang/Memos/Affection 已无独立菜单项。**侧边栏不需要改动。**

### 1.3 待处理的遗留问题

1. **Slang 独立页面仍存在**：`views/slang/` 目录 14 个组件仍在磁盘上，虽然路由已 redirect，但代码未删除
2. **Learning 内嵌 slot 与 Slang 独立组件功能重复**：SlangMainPane vs SlangTermList、SlangDrawerContent vs SlangDetailDrawer
3. **Slang 高级功能（漂移/AI审核/设置）只在独立页组件中**：Learning slot 缺少这些入口
4. **无统一设置页面**：各 noun 设置分散
5. **无标签系统**：noun 类型靠 NounSwitcher 切换，不能跨 noun 混合浏览

---

## 2. 执行阶段总览

| 阶段 | 内容 | 风险 | 预估工作量 |
|------|------|------|-----------|
| **P1** | Slang 高级功能迁入 Learning slot | 低 | 中 |
| **P2** | 标签系统 + 跨 noun 混合浏览 | 低 | 中 |
| **P3** | 统一设置页面（前端 + 后端聚合 API） | 中 | 大 |
| **P4** | 清理遗留代码 + 验证 | 低 | 小 |

每阶段独立可验证、可回滚。

---

## 3. P1 — Slang 高级功能迁入 Learning Slot

### 3.1 目标

把 Slang 独立页面的 3 个高级功能搬入 Learning Console 的 slang slot，使 `/learning?noun=slang` 成为功能完整的唯一入口。

### 3.2 需要迁入的功能

| 功能 | 源组件（`views/slang/components/`） | 目标位置 |
|------|--------------------------------------|----------|
| 设置面板 | `SlangSettingsDrawer.vue` + `SlangSettingsForm.vue` | slot toolbar 或 side panel |
| 漂移审核 | `SlangDriftCard.vue` + `SlangGovernanceSection.vue` | slot side panel |
| AI 审核进度 | `SlangExtractionProgress.vue` + `SlangBacklogProgress.vue` | slot toolbar 或 main pane 顶部 |

### 3.3 实施步骤

**步骤 1**：读取现有 slot 接口定义

文件：[views/learning/slots/types.ts](../../admin/frontend/src/views/learning/slots/types.ts)

确认 `NounSlotContext` 接口支持哪些挂载点（toolbar / side-panel / drawer / main-pane）。

**步骤 2**：扩展 SlangToolbarContent

文件：[views/learning/slots/slang/SlangToolbarContent.vue](../../admin/frontend/src/views/learning/slots/slang/SlangToolbarContent.vue)

添加：
- "设置" 按钮 → 打开设置抽屉
- "AI 审核" 状态指示 + 手动触发按钮
- 提取进度条（复用 `SlangExtractionProgress` 逻辑）

**步骤 3**：扩展 SlangSidePanelContent

文件：[views/learning/slots/slang/SlangSidePanelContent.vue](../../admin/frontend/src/views/learning/slots/slang/SlangSidePanelContent.vue)

添加：
- 漂移审核卡片（复用 `SlangDriftCard` 逻辑）
- 治理概览（复用 `SlangGovernanceSection` 逻辑）

**步骤 4**：创建设置抽屉

在 `views/learning/slots/slang/` 下新建 `SlangSettingsPanel.vue`，从 `views/slang/components/SlangSettingsDrawer.vue` 提取核心逻辑。

调用的 API 不变：`GET/POST /api/admin/slang/settings`

**步骤 5**：验证

```bash
cd admin/frontend
./node_modules/.bin/vue-tsc --noEmit
npm run build
```

浏览器访问 `/learning?noun=slang`，确认：
- [ ] 设置面板可打开、可保存
- [ ] 漂移审核卡片显示正常
- [ ] AI 审核可手动触发、进度可见
- [ ] 提取进度条正常

### 3.4 回滚

```bash
git checkout -- admin/frontend/src/views/learning/slots/slang/
```

---

## 4. P2 — 标签系统 + 跨 Noun 混合浏览

### 4.1 目标

在 LearningView 的表格中，每条词条以标签（tag）形式显示其 noun 类型、scope、策略等属性。支持跨 noun 混合浏览（不切换 NounSwitcher 也能看到所有 noun 的条目）。

### 4.2 标签映射规则

**Noun 标签**（主分类，彩色）：

| noun 值 | 标签文本 | 颜色建议 |
|---------|----------|----------|
| slang | 黑话 | blue |
| style | 表达 | green |
| episode | 情景 | orange |
| memory | 记忆 | purple |
| fact | 事实 | cyan |
| graph_relation | 关系 | pink |

**Scope 标签**（辅助）：

| scope 值 | 标签文本 |
|----------|----------|
| group | 群级 |
| global | 全局 |
| user | 用户级 |

**策略标签**（noun-specific，灰色系）：

| 来源 | 标签 |
|------|------|
| slang.repeat_policy=understand_only | 仅理解 |
| slang.repeat_policy=rewrite | 可改写 |
| slang.repeat_policy=use | 可使用 |
| style.output_policy=use | 可使用 |
| style.output_policy=transform | 需转化 |
| style.output_policy=observe | 仅观察 |
| style.risk_tags | 各风险标签原样显示 |
| memory.category | 偏好/边界/事件/承诺/... |

### 4.3 实施步骤

**步骤 1**：创建标签渲染组件

新建 `views/learning/components/LearningItemTags.vue`

输入：`LearningItem`（已有类型，含 noun / scope / metadata）
输出：一组 `<n-tag>` 组件

**步骤 2**：修改 LearningTable

文件：[views/learning/components/LearningTable.vue](../../admin/frontend/src/views/learning/components/LearningTable.vue)

- 在表格列中添加"标签"列，渲染 `LearningItemTags`
- 当 NounSwitcher 选择 "all"（新增选项）时，显示所有 noun 的条目混合

**步骤 3**：扩展 NounSwitcher

文件：[views/learning/components/NounSwitcher.vue](../../admin/frontend/src/views/learning/components/NounSwitcher.vue)

添加 "全部" 选项（key: `'all'`），选中时不传 noun 过滤参数给 API。

**步骤 4**：后端适配（如需）

检查 `GET /api/admin/learning/items` 是否支持不传 noun 参数返回全部。

文件：[admin/routes/api/learning_pipeline.py](../../admin/routes/api/learning_pipeline.py)

当前签名中 `noun` 参数如果是 required，改为 optional（默认返回全部 noun）。

**步骤 5**：标签过滤

在 LearningTable 的 toolbar 区域添加标签过滤器：点击标签可快速过滤同类条目。

实现方式：前端过滤（数据量小）或 query 参数传递（数据量大时）。

**步骤 6**：验证

```bash
cd admin/frontend
./node_modules/.bin/vue-tsc --noEmit
npm run build
```

浏览器验证：
- [ ] NounSwitcher "全部" 模式显示混合条目
- [ ] 每条词条显示正确的 noun/scope/策略标签
- [ ] 点击标签可过滤
- [ ] 切换回单 noun 模式行为不变（回归）

### 4.4 回滚

```bash
git checkout -- admin/frontend/src/views/learning/components/
```

---

## 5. P3 — 统一设置页面

### 5.1 目标

新建一个设置页面（或 Learning Console 内的设置 tab），聚合所有 noun 的定时任务配置、提取参数、审核时间槽等，用户不再需要进入 3+ 个不同入口配置管线行为。

### 5.2 设置项清单

| 设置项 | 当前来源 | 读取 API | 写入 API |
|--------|----------|----------|----------|
| Slang 提取间隔 | `slang_settings` 表 | `GET /api/admin/slang/settings` | `POST /api/admin/slang/settings` |
| Slang AI 审核时间槽 | `slang_settings` 表 | 同上 | 同上 |
| Slang 漂移老化阈值 | 硬编码 | 无 | 无（P3 暴露） |
| Style 提取开关 | 无 | 无 | 无（P3 新增） |
| Consolidator 定时间隔 | 无 | 无 | 无（P3 新增） |
| Memory GroupMemoryConfig | `config/group-memory.json` | `GET /api/admin/memory/config` | `POST /api/admin/memory/config` |
| Affection 计分开关 | 无 | 无 | 无（P3 新增） |

### 5.3 后端实施

**步骤 1**：创建聚合设置 API

文件：`admin/routes/api/learning_pipeline.py`（追加到现有 router）

```python
@router.get("/settings")
async def get_learning_settings():
    """聚合所有 noun 的设置，返回统一结构。"""
    # 读取各 noun 的设置源
    slang_settings = await _read_slang_settings()
    memory_config = await _read_memory_config()
    return {
        "slang": slang_settings,
        "style": {"extract_enabled": ..., "extract_interval_minutes": ...},
        "consolidator": {"auto_enabled": ..., "interval_minutes": ...},
        "memory": memory_config,
        "affection": {"scoring_enabled": ...},
    }

@router.post("/settings")
async def save_learning_settings(request: Request):
    """统一保存。按 noun 分发到各自的 store。"""
    body = await request.json()
    # 分发写入各 noun 的 settings store
    ...
```

**步骤 2**：暴露当前硬编码的设置

对于 Style / Consolidator / Affection 当前无设置 API 的情况：

- 方案 A（推荐）：在 `learning_pipeline.py` 中新增一个轻量 `learning_settings.json` 文件作为统一设置存储，各插件 `on_tick` 读取该文件
- 方案 B：各插件各自新增 settings 表（工作量大，不推荐）

选择方案 A 时的存储路径：`storage/learning_settings.json`

```json
{
  "style": {
    "extract_enabled": true,
    "extract_interval_minutes": 120
  },
  "consolidator": {
    "auto_enabled": false,
    "interval_minutes": 360
  },
  "affection": {
    "scoring_enabled": true
  }
}
```

**步骤 3**：插件侧读取适配

各插件的 `on_tick` / `on_post_reply` 在执行前检查 `learning_settings.json` 中对应开关。

改动文件（仅加读取判断，不改核心逻辑）：
- `plugins/slang/plugin.py` — 已有 settings，不改
- `services/style/plugin_hooks.py`（或等价位置）— 加 `extract_enabled` 检查
- `services/memory_consolidator/` — 加 `auto_enabled` + `interval_minutes`
- `services/affection/` — 加 `scoring_enabled` 检查

**步骤 4**：定时任务状态 API

```python
@router.get("/schedules")
async def get_learning_schedules():
    """返回各 noun 定时任务的当前状态。"""
    return {
        "slang_extract": {"last_run": ..., "next_run": ..., "status": "idle"},
        "slang_ai_review": {"last_run": ..., "next_run": ..., "status": "idle"},
        "style_extract": {"last_run": ..., "next_run": ..., "status": "disabled"},
        "consolidator": {"last_run": ..., "next_run": ..., "status": "disabled"},
    }
```

### 5.4 前端实施

**步骤 1**：新建设置视图

文件：`views/learning/components/LearningSettingsPanel.vue`

布局：
```
┌─────────────────────────────────────────┐
│ 学习管线设置                    [保存]   │
├─────────────────────────────────────────┤
│ ▼ 黑话（Slang）                         │
│   提取间隔：[60] 分钟                    │
│   AI 审核时间槽：[03:00] [15:00] [+]    │
│   漂移老化天数：[30]                     │
├─────────────────────────────────────────┤
│ ▼ 表达（Style）                         │
│   自动提取：[开关]                       │
│   提取间隔：[120] 分钟                   │
├─────────────────────────────────────────┤
│ ▼ 记忆整合（Consolidator）              │
│   自动整合：[开关]                       │
│   整合间隔：[360] 分钟                   │
├─────────────────────────────────────────┤
│ ▼ 亲密度（Affection）                   │
│   自动计分：[开关]                       │
├─────────────────────────────────────────┤
│ ▼ 定时任务状态                          │
│   黑话提取  上次: 14:30  下次: 15:30 ●  │
│   AI 审核   上次: 03:00  下次: 15:00 ●  │
│   表达提取  未启用                  ○    │
│   记忆整合  未启用                  ○    │
└─────────────────────────────────────────┘
```

**步骤 2**：入口挂载

在 LearningView 的 toolbar 区域添加"设置"按钮（齿轮图标），点击打开设置面板（抽屉或全屏 modal）。

**步骤 3**：验证

```bash
cd admin/frontend
./node_modules/.bin/vue-tsc --noEmit
npm run build
```

浏览器验证：
- [ ] 设置面板正确加载各 noun 当前设置
- [ ] 修改后保存成功
- [ ] 定时任务状态实时显示
- [ ] 各插件 on_tick 尊重新设置（需 docker restart bot 后观察日志）

### 5.5 回滚

后端：
```bash
git checkout -- admin/routes/api/learning_pipeline.py
rm -f storage/learning_settings.json
```

前端：
```bash
git checkout -- admin/frontend/src/views/learning/
```

---

## 6. P4 — 清理遗留代码 + 验证

### 6.1 目标

删除已被 redirect 替代且功能已迁入 Learning slot 的独立页面代码。

### 6.2 可删除的文件/目录

| 路径 | 前提条件 |
|------|----------|
| `views/slang/` 整个目录 | P1 完成后，所有功能已在 Learning slot 中可用 |
| `views/memos/MemosView.vue` | 已 redirect 到 `/memory?view=browse` |
| `views/affection/AffectionView.vue` | 已 redirect 到 `/memory?view=browse` |

### 6.3 不可删除

| 路径 | 原因 |
|------|------|
| `views/slang/composables/` | 检查是否被 Learning slot 引用；如有引用则保留或迁移 |
| `views/slang/helpers/` | 同上 |
| 各 noun 的独立 API 路由文件 | 后端逻辑不变，仍被 slot 组件调用 |

### 6.4 实施步骤

**步骤 1**：依赖检查

```bash
cd admin/frontend
grep -r "views/slang" src/ --include="*.vue" --include="*.ts" | grep -v "node_modules"
grep -r "views/memos" src/ --include="*.vue" --include="*.ts" | grep -v "node_modules"
grep -r "views/affection" src/ --include="*.vue" --include="*.ts" | grep -v "node_modules"
```

确认无其他文件 import 这些组件。

**步骤 2**：删除

```bash
rm -rf admin/frontend/src/views/slang/
rm admin/frontend/src/views/memos/MemosView.vue
rm admin/frontend/src/views/affection/AffectionView.vue
```

如果 `views/slang/composables/` 或 `views/slang/helpers/` 被 slot 引用，先迁移到 `views/learning/slots/slang/` 下再删。

**步骤 3**：路由清理

文件：[admin/frontend/src/router/index.ts](../../admin/frontend/src/router/index.ts)

确认 `/slang`、`/memos`、`/affection` 的 redirect 规则仍保留（兼容旧书签），但不再 import 已删组件。

**步骤 4**：全量验证

```bash
cd admin/frontend
./node_modules/.bin/vue-tsc --noEmit
npm run build
```

浏览器全路径验证：
- [ ] `/learning` — 正常加载，所有 noun 可切换
- [ ] `/learning?noun=slang` — 设置/漂移/AI审核功能正常
- [ ] `/slang` — redirect 到 `/learning?noun=slang`
- [ ] `/memos` — redirect 到 `/memory?view=browse`
- [ ] `/affection` — redirect 到 `/memory?view=browse`
- [ ] `/memory` — Memory Console 正常
- [ ] 侧边栏所有链接可点击

### 6.5 回滚

```bash
git checkout -- admin/frontend/src/views/slang/
git checkout -- admin/frontend/src/views/memos/
git checkout -- admin/frontend/src/views/affection/
```

---

## 7. 接口兼容性检查清单

重构期间必须保证以下接口行为不变：

| 接口 | 消费者 | 验证方式 |
|------|--------|----------|
| `GET /api/admin/slang/settings` | SlangSettingsPanel（新） | curl 返回 200 |
| `POST /api/admin/slang/settings` | SlangSettingsPanel（新） | curl 写入后 GET 验证 |
| `GET /api/admin/learning/pipeline` | StageStrip | 页面加载正常 |
| `GET /api/admin/learning/items` | LearningTable | 表格数据正常 |
| `POST /api/admin/learning/extract-all` | toolbar 按钮 | 触发后进度可见 |
| `GET /api/admin/slang/drift/*` | SlangDriftCard（迁入） | 漂移数据正常 |
| `POST /api/admin/slang/ai-review/run` | toolbar 按钮（迁入） | 触发成功 |
| `GET /api/admin/memory/config` | LearningSettingsPanel | 设置面板加载 |
| 各插件 `on_tick` / `on_post_reply` | 运行时 | docker logs 无异常 |

---

## 8. 部署策略

### 8.1 每阶段部署方式

| 阶段 | 改动范围 | 部署命令 |
|------|----------|----------|
| P1 | 纯前端 | `cd admin/frontend && npm run build`（D6：bind mount 即生效） |
| P2 | 纯前端 + 可能改 1 行后端 | 前端 build；如改了 .py → `docker compose up bot -d --build` |
| P3 | 前端 + 后端 | `npm run build` + `docker compose up bot -d --build` |
| P4 | 纯前端删除 | `npm run build` |

### 8.2 灰度

无需灰度——admin SPA 只有 admin 本人使用，直接全量部署。

---

## 9. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Slang 高级功能迁入后遗漏某个 API 调用 | 中 | 功能不可用 | P1 验证清单逐项确认 |
| 跨 noun 混合浏览时数据量大导致前端卡顿 | 低 | 体验差 | 分页 + 默认单 noun 模式 |
| 统一设置写入后插件未读取 | 中 | 设置不生效 | P3 需 restart bot + 观察日志 |
| 删除 Slang 独立页后发现有未迁移的 composable | 低 | 构建失败 | P4 步骤 1 依赖检查 |
| `learning_settings.json` 文件损坏 | 极低 | 插件 fallback 到默认值 | 读取时 try/except + 默认值 |

---

## 10. 状态追踪

| 阶段 | 子步骤 | 状态 | 备注 |
|------|--------|------|------|
| P1 | 读取 slot 接口定义 | ✅ | 已确认 slot 完整包含所有功能 |
| P1 | 扩展 SlangToolbarContent | ✅ | 已有：extract/AI审核/settings/create |
| P1 | 扩展 SlangSidePanelContent | ✅ | 已有：stats/extraction/snapshot |
| P1 | 创建 SlangSettingsPanel | ✅ | 已有：SlangSettingsDrawer via SlangDrawerContent |
| P1 | 验证 | ✅ | 审计发现 P1 已在之前完成 |
| P2 | 创建 LearningItemTags | ✅ | `components/LearningItemTags.vue` |
| P2 | 修改 LearningTable | ✅ | 添加 tags 列 + grid 适配 |
| P2 | 扩展 NounSwitcher | ✅ | 已有 "all" 选项 |
| P2 | 后端适配（noun optional） | ✅ | 已支持 noun=all |
| P2 | 标签数据（后端） | ✅ | `_slang_tags/_style_tags/_episode_tags/_memory_tags` |
| P2 | 验证 | ✅ | vue-tsc + build 通过 |
| P3 | 聚合设置 API | ✅ | `GET/POST /api/admin/learning/settings` |
| P3 | learning_settings.json | ✅ | `services/learning_settings.py` 共享读取模块 |
| P3 | 插件侧读取适配 | ✅ | style on_tick + affection on_post_reply + dream on_tick(consolidator) |
| P3 | 定时任务状态 API | ✅ | `GET /api/admin/learning/schedules` |
| P3 | 前端设置面板 | ✅ | `LearningSettingsDrawer.vue` + 按钮入口 |
| P3 | 验证 | ✅ | vue-tsc + build + ruff + pyright 全通过 |
| P4 | 依赖检查 | ✅ | slang/ 为共享组件不可删；memos 被 memory 引用 |
| P4 | 删除遗留代码 | ✅ | 删除 `views/affection/`（唯一死代码） |
| P4 | 全量验证 | ✅ | vue-tsc + build + ruff 全通过 |

---

## 11. FAQ

**Q: 为什么不把 Memory Console 也合入 Learning？**

A: Memory Console（`/memory`）已经是独立的完整管理台，含卡片 CRUD + 实体浏览 + 系列管理 + Affection 视图。它的数据流（卡片生命周期）与 Learning 管线（候选→归档）有交集但不完全重合。强行合并会让 Learning Console 过于臃肿。保持两个入口：Learning 管理"学习产物流转"，Memory 管理"已入库的记忆资产"。

**Q: 为什么选方案 A（统一 JSON）而非方案 B（各自 settings 表）？**

A: 方案 B 需要给 Style/Consolidator/Affection 各自新增 SQLite 表 + CRUD API + migration，工作量是方案 A 的 3 倍。方案 A 一个 JSON 文件 + 一个聚合 API 即可，且各插件只需加一行读取判断。JSON 文件损坏时 fallback 到默认值，风险可控。

**Q: P1 完成后 Slang 独立页的 composables/helpers 怎么处理？**

A: 先检查 Learning slot 是否已经 import 了这些文件。如果是，P4 删除时保留（或迁移路径）。如果不是，说明 slot 有自己的实现，可以安全删除。

**Q: 标签系统会影响现有的 NounSwitcher 行为吗？**

A: 不会。NounSwitcher 仍然是主过滤器，标签是辅助过滤。只是新增一个 "全部" 选项让用户可以跨 noun 浏览。默认行为不变。
