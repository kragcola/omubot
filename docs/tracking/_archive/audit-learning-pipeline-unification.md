# 学习管线统一化审计 — Web 重构前置调研

> 状态：2026-05-28 审计完成
> 目标：删去黑话/表达/关系等页面分割，Web 只保留候选→归档的 tab 工作流；词条以标签显示便于统一管理；追加全局设置页面统一配置定时处理等功能
> 约束：各层级逻辑处理方式不变，只更改显示

---

## 1. 现有前端页面清单

| 页面 | 路径 | 职责 | 调用 API |
|------|------|------|----------|
| **Learning Console（统一学习台）** | `views/learning/LearningView.vue` | 5 阶段 StageStrip + 6 noun 切换 + 统一表格 | `/api/admin/learning/pipeline`, `/items`, `/extract-all` |
| **Slang Console（黑话独立台）** | `views/slang/SlangView.vue` | 独立 CRUD + 设置 + 提取 + AI 审核 + 漂移 | `/api/admin/slang/*`（30+ 端点） |
| **Memory Console（记忆台）** | `views/memory/MemoryView.vue` | 卡片 CRUD + 实体浏览 + 系列 | `/api/admin/memory/*` |
| **Memos（备忘浏览）** | `views/memos/MemosView.vue` | 实体维度浏览卡片 | `/api/admin/memos/*` |
| **Affection（关系台）** | `views/affection/AffectionView.vue` | 用户亲密度排行 + 详情 | `/api/admin/affection/*` |
| **Style（表达，嵌入 Learning）** | `views/learning/slots/style/` | 表达习惯 CRUD + 反馈 + 档案 | `/api/admin/style/*` |
| **Episode（情景，嵌入 Learning）** | `views/learning/slots/episode/` | 情景记忆浏览 + 审批 | `/api/admin/memory_consolidator/*` |

**问题**：Slang 有独立页面 + Learning 内嵌 slot 两套入口；Memory/Memos 有两个独立页面；Affection 完全独立。用户需要在 5+ 个页面间跳转管理本质上同一条管线的数据。

---

## 2. 后端 API 结构

### 2.1 统一层（已有）

| 端点 | 职责 |
|------|------|
| `GET /api/admin/learning/pipeline` | 聚合 6 个 noun 的阶段计数 |
| `GET /api/admin/learning/items` | 跨 noun 分页列表（stage/noun/group/date 过滤） |
| `POST /api/admin/learning/extract-all` | 并行触发 slang + style + consolidator 提取 |
| `GET /api/admin/learning/today` | 今日汇总 |

### 2.2 各 noun 独立层

| Noun | 路由前缀 | 端点数 | 独有功能 |
|------|----------|--------|----------|
| slang | `/api/admin/slang/` | 30+ | 漂移审核、全局扫描、AI 批量审核、设置面板 |
| style | `/api/admin/style/` | 14 | 档案编译/回滚/启用、反馈记录 |
| memory | `/api/admin/memory/` | 10 | 卡片 CRUD、实体、系列、GroupMemoryConfig |
| consolidator | `/api/admin/memory_consolidator/` | 7 | 干跑候选、反思管线、决策审批 |
| normalizer | `/api/admin/learning-normalizer/` | 5 | 去重聚类、锁定规范文本 |
| affection | `/api/admin/affection/` | 2 | 亲密度排行 |
| memos | `/api/admin/memos/` | 3 | 实体维度浏览 |

### 2.3 统一管线阶段模型（learning_pipeline.py 已定义）

```
candidate → review → approved → hits → archived
```

6 个 noun：`slang`, `style`, `episode`, `memory`, `fact`, `graph_relation`

---

## 3. 数据存储

| 存储 | 文件 | 核心表 | 阶段字段 |
|------|------|--------|----------|
| slang.db | `storage/slang.db` | `slang_terms`, `slang_observations`, `slang_pending_candidates` | `status`: candidate/approved/muted/expired |
| style.db | `storage/style.db` | `style_expressions`, `style_evidence`, `style_profiles` | `status`: pending/approved/rejected/muted |
| episodic.db | `storage/episodic.db` | `episodes`, `episode_observations` | `episode_state`: dry_run/candidate/approved/enabled_for_prompt/disabled |
| memory_cards.db | `storage/memory_cards.db` | `memory_cards`, `card_series` | `status`: active/superseded/expired |
| consolidator_candidates.db | `storage/consolidator_candidates.db` | `consolidator_candidates`, `consolidator_runs` | `state`: dry_run/queued/approved/rejected |
| learning_normalizer.db | `storage/learning_normalizer.db` | `learning_normalizer_clusters`, `_items` | `status`: open/locked/merged |
| affection/ | `storage/affection/*.json` | — | tier: stranger/acquaintance/friend/close_friend/... |

---

## 4. 未统一化问题清单

### 4.1 阶段命名不一致

| Noun | 候选 | 审核中 | 已批准 | 命中/活跃 | 归档/失效 |
|------|------|--------|--------|-----------|-----------|
| slang | `candidate` | (无显式) | `approved` | (usage_count) | `muted`/`expired` |
| style | `pending` | (无显式) | `approved` | (count) | `rejected`/`muted` |
| episode | `dry_run`/`candidate` | (无显式) | `approved` | `enabled_for_prompt` | `disabled` |
| memory | — | — | `active` | (last_seen_at) | `superseded`/`expired` |
| consolidator | `dry_run` | `queued` | `approved` | — | `rejected` |

**统一管线已做映射**（`learning_pipeline.py`），但各 noun 的原生状态字段名不同，前端 slot 组件各自处理映射逻辑。

### 4.2 设置分散

| 设置项 | 当前位置 | 管理方式 |
|--------|----------|----------|
| Slang 提取间隔/批量/阈值 | `slang_settings` 表 + `/api/admin/slang/settings` | Slang 独立设置抽屉 |
| Style 无独立设置 | 硬编码 | 无 UI |
| Memory GroupMemoryConfig | `config/group-memory.json` + `/api/admin/memory/config` | Memory 页面内 |
| 定时提取时间 | `SlangPlugin.on_tick` 硬编码 `extract_interval_minutes` | 无统一 UI |
| AI 审核时间槽 | `daily_ai_review_times` in slang_settings | Slang 设置抽屉 |
| Consolidator 触发 | 纯手动 | 无定时 |
| Affection 无设置 | 硬编码 | 无 UI |

**问题**：用户需要进入 3+ 个不同页面/抽屉才能配置完整管线的定时行为。

### 4.3 前端组件重复

| 功能 | Slang 独立页 | Learning 内嵌 slot | 差异 |
|------|-------------|-------------------|------|
| 词条列表 | `SlangTermList` | `SlangMainPane` | 列表字段/排序不同 |
| 详情抽屉 | `SlangDetailDrawer` | `SlangDrawerContent` | 布局不同 |
| 设置 | `SlangSettingsDrawer` | 无 | 独立页独有 |
| 提取进度 | `SlangExtractionProgress` | 无 | 独立页独有 |
| 漂移审核 | `SlangDriftCard` + `SlangGovernanceSection` | 无 | 独立页独有 |

Style/Episode/Memory 只有 Learning 内嵌 slot，无独立页面。

### 4.4 操作入口不统一

| 操作 | 当前入口 | 理想入口 |
|------|----------|----------|
| 批量审批 | Slang: `/slang/terms/bulk`; Style: 逐条 `/status`; Episode: `/decide` | 统一批量操作 |
| 手动提取 | Slang: `/slang/extract/run`; Style: `/style/extract/run`; Consolidator: `/memory_consolidator/runs` | 统一 extract-all（已有但不含 episode） |
| AI 审核 | 仅 Slang 有 `/slang/ai-review/run` | 统一 AI 审核 |
| 漂移检测 | 仅 Slang 有 `/slang/drift/*` | 统一漂移/过期管理 |
| 反馈记录 | Style: `/style/expressions/{id}/feedback`; 其他无 | 统一反馈机制 |

### 4.5 标签系统缺失

当前各 noun 的分类方式：

| Noun | 分类维度 | 当前实现 |
|------|----------|----------|
| slang | scope(group/global), group_id, repeat_policy | 字段过滤 |
| style | scope, group_id, situation, output_policy, risk_tags | 字段过滤 + JSON tags |
| episode | scope, group_id, situation | 字段过滤 |
| memory | scope, scope_id, category, series_id | 字段过滤 + 系列 |

**问题**：无统一标签体系。重构后需要一个跨 noun 的标签显示层，但底层分类逻辑不变。

---

## 5. 插件钩子与定时任务

### 5.1 钩子注册点

| 插件 | on_startup | on_message | on_pre_prompt | on_post_reply | on_tick |
|------|------------|------------|---------------|---------------|---------|
| SlangPlugin | ✅ | ✅ 命中记录 | ✅ 注入黑话块 | — | ✅ 提取/审核/漂移 |
| StylePlugin | ✅ | — | ✅ 注入风格块 | ✅ 弱信号收集 | — |
| MemoPlugin | ✅ | — | ✅ 注入记忆块 | ✅ 卡片提取 | — |
| AffectionPlugin | ✅ | — | ✅ 注入关系块 | ✅ 亲密度计分 | — |

### 5.2 定时任务现状

| 任务 | 触发机制 | 间隔 | 可配置性 |
|------|----------|------|----------|
| Slang 提取 | `on_tick` + monotonic timer | `extract_interval_minutes`（默认 60） | ✅ via slang_settings |
| Slang AI 审核 | `on_tick` + 时间槽匹配 | `daily_ai_review_times`（默认 ["03:00","15:00"]） | ✅ via slang_settings |
| Slang 漂移老化 | `on_tick` + 日期检查 | 每天 1 次 | ❌ 硬编码 |
| Style 提取 | 手动触发 | — | ❌ 无定时 |
| Memo 提取 | `on_post_reply` 自动 | 每次回复后 | ❌ 无开关 |
| Consolidator | 手动触发 | — | ❌ 无定时 |
| Affection 计分 | `on_post_reply` 自动 | 每次回复后 | ❌ 无开关 |

---

## 6. 重构方向建议

### 6.1 前端统一化

**删除**：
- `views/slang/` 独立页面（功能合并入 Learning Console）
- `views/memos/` 独立页面（合并入 Memory slot）
- `views/affection/` 独立页面（合并为 Learning Console 的一个 noun 或侧边栏）

**保留并增强**：
- `views/learning/LearningView.vue` 作为唯一入口
- 5 个 tab 对应 5 个阶段：候选 → 审核 → 已批准 → 命中 → 归档
- 每个 tab 内以标签（tag）形式显示词条的 noun 类型（黑话/表达/情景/记忆/事实/关系）
- 标签可过滤但不分页面

**新增**：
- `views/learning/LearningSettingsView.vue` — 统一设置页面
  - 定时提取配置（slang/style/consolidator 各自间隔）
  - AI 审核时间槽
  - 各 noun 的开关
  - GroupMemoryConfig
  - 漂移检测阈值

### 6.2 后端适配

**不改**：
- 各 noun 的独立 API 端点（逻辑不变）
- 各 noun 的存储结构（字段不变）
- 插件钩子逻辑

**需新增/改造**：
- `GET /api/admin/learning/settings` — 聚合所有 noun 的设置
- `POST /api/admin/learning/settings` — 统一保存
- `GET /api/admin/learning/schedules` — 返回所有定时任务状态
- `POST /api/admin/learning/schedules` — 统一配置定时任务
- `learning_pipeline.py` 的 `learning_items()` 可能需要扩展：支持标签过滤、批量操作代理

### 6.3 标签显示层

前端标签映射（纯显示，不改底层）：

| 底层字段 | 标签显示 |
|----------|----------|
| noun=slang | 🏷️ 黑话 |
| noun=style | 🏷️ 表达 |
| noun=episode | 🏷️ 情景 |
| noun=memory | 🏷️ 记忆 |
| noun=fact | 🏷️ 事实 |
| noun=graph_relation | 🏷️ 关系 |
| scope=group | 🏷️ 群级 |
| scope=global | 🏷️ 全局 |
| scope=user | 🏷️ 用户级 |

额外标签来源：
- slang: `repeat_policy` → 🏷️ 仅理解 / 🏷️ 可改写 / 🏷️ 可使用
- style: `output_policy` → 🏷️ 可使用 / 🏷️ 需转化 / 🏷️ 仅观察
- style: `risk_tags` → 🏷️ 各风险标签
- memory: `category` → 🏷️ 偏好 / 🏷️ 边界 / 🏷️ 事件 / 🏷️ 承诺 / ...
- episode: `situation` → 🏷️ 情景描述

---

## 7. 现有统一层的能力边界

`learning_pipeline.py` 已实现的统一能力：

| 能力 | 状态 | 备注 |
|------|------|------|
| 跨 noun 阶段计数 | ✅ | `learning_pipeline()` |
| 跨 noun 分页列表 | ✅ | `learning_items()` 支持 stage/noun/group/date/sort |
| 统一提取触发 | ✅ | `extract-all` 并行 slang+style+consolidator |
| 统一批量操作 | ❌ | 各 noun 批量端点不同 |
| 统一设置管理 | ❌ | 设置分散在 3+ 处 |
| 统一定时配置 | ❌ | 定时逻辑分散在插件 on_tick |
| 统一 AI 审核 | ❌ | 仅 slang 有 |
| 统一漂移/过期管理 | ❌ | 仅 slang 有 |
| 统一反馈机制 | ❌ | 仅 style 有 |
| 标签系统 | ❌ | 无 |

---

## 8. 接口兼容性矩阵

重构时需要确保以下接口不变（后端逻辑不改）：

| 接口 | 消费者 | 重构影响 |
|------|--------|----------|
| `SlangPlugin.on_message` 命中记录 | 运行时 | 无 |
| `SlangPlugin.on_tick` 定时提取 | 运行时 | 设置来源可能改为统一 API |
| `StylePlugin.on_pre_prompt` 注入 | 运行时 | 无 |
| `MemoPlugin.on_post_reply` 提取 | 运行时 | 无 |
| `AffectionPlugin.on_post_reply` 计分 | 运行时 | 无 |
| `PromptProviderBus` 注册 | 运行时 | 无 |
| `SlangStore` / `StyleStore` / `CardStore` | 后端 | 无 |
| 各 noun 独立 API 端点 | 前端 slot 组件 | 保留，但前端入口统一 |

---

## 9. 风险点

| 风险 | 影响 | 缓解 |
|------|------|------|
| Slang 独立页功能丰富（漂移/AI审核/全局扫描），合并后可能丢失 | 用户无法执行高级操作 | 在统一页面的 noun=slang 过滤下保留这些操作入口 |
| Memory/Memos 两个页面有不同视角（卡片 vs 实体） | 合并后需要两种浏览模式 | 统一页面内提供"按词条"和"按实体"两个 tab |
| Affection 是只读展示，与学习管线的"候选→归档"流程不匹配 | 强行塞入 tab 工作流不自然 | 作为侧边栏/辅助面板而非主 tab |
| 设置统一后各 noun 的设置粒度不同 | 统一设置页面可能过于复杂 | 按 noun 分 section，折叠展示 |
| 定时任务统一配置需要改 plugin on_tick 的设置读取源 | 需要后端改动 | 统一设置 API 写入各自的 settings store，plugin 读取不变 |
