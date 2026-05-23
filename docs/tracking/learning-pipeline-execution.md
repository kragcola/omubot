# Learning Pipeline Execution Tracker

跟踪 `docs/tracking/learning-pipeline.md` v2.1 的实际落地。方案文档负责定义目标和约束；本文档只记录执行拆解、完成状态、验证证据和遗留风险。

## 元信息

- **方案版本**：`docs/tracking/learning-pipeline.md` v2.1
- **启动日期**：2026-05-23
- **当前阶段**：L 系列已完成；L1/L2/L3/L4 均已收口
- **执行原则**：
  - 每一步开始前，先在本文档细化任务和完成标准。
  - 每一步完成后，立即更新状态、改动文件和验证证据。
  - `§22` 覆盖 `§19`，`§23` 覆盖 `§20`，`§24` 作为增量验证矩阵。

## 总体进度

| 步骤 | 名称 | 状态 | 关键完成标准 |
| --- | --- | --- | --- |
| S0 | 创建执行追踪文档 | 已完成 | 本文档存在，并作为后续步骤状态源 |
| PR1 | 准备：审核盘点 + 接口对齐 + pipeline 只读 | 已完成 | `/learning/pipeline` 只读聚合，memory schema 按 v2.1 保持标量 |
| PR2a | budget manager 吃 candidate | 已完成 | `PromptBudgetManager.process` 入参为 `PromptBlockCandidate`，trace 行为不变 |
| PR2b | 观测表 + accepted 写入 | 已完成 | style/episode 仅 accepted block 写 observation |
| PR3 | 前端骨架 | 已完成 | `/learning` 路由、StageStrip、URL 状态边界 |
| PR4 | 列表 | 已完成 | `/learning/items` 与 memory 行跳 `/memory?view=manage` |
| PR5 | 审核 + 折返 + extract-all | 已完成 | extract-all 锁/timeout + production style runner + 轻量 ReviewHost |
| PR6 | SideMenu + Dashboard 深链 + 上线 | 已完成 | 入口、深链、最终验收完成 |
| L1 | slang accepted-only observation | 已完成 | slang prompt 注入命中只在 budget accepted 后写 observation |
| L2 | MemoryView `?card_id=` 定位 + 自动打开 Drawer | 已完成 | `/learning` memory 行可跳到 `/memory?view=manage&card_id=...` 并打开目标卡编辑抽屉 |
| L3 | trimmed block 也计 hits | 已完成 | 被预算裁剪为 trimmed 的 prompt block 也写入 observation，hits 不再只统计完整 accepted |
| L4 | extract-all 进度 SSE / run_id 查询 | 已完成 | 一键抽取返回 run_id，前端可看到各 noun 运行进度与最终结果 |

## 步骤记录

### S0 · 创建执行追踪文档

**开始时间**：2026-05-23

**任务拆解**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| S0.1 | 已完成 | 新建 `docs/tracking/learning-pipeline-execution.md` |
| S0.2 | 已完成 | 记录 v2.1 权威口径与 7 个 PR 阶段 |
| S0.3 | 已完成 | 后续 PR1 开始前在本文档补充 PR1 细化任务 |

**改动文件**：

- `docs/tracking/learning-pipeline-execution.md`

**验证证据**：

- `docs/tracking/learning-pipeline-execution.md` 已创建。
- 本文档已记录 v2.1 权威口径、7 个 PR 阶段和后续同步规则。

**遗留风险**：无。

### PR1 · 准备：审核盘点 + 接口对齐 + pipeline 只读

**开始时间**：2026-05-23

**任务拆解**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| PR1.1 | 已完成 | 盘点现有学习相关 API、memory 数据源、审核 UI 形态和测试位置 |
| PR1.2 | 已完成 | 新增或调整只读 `/api/admin/learning/pipeline`，不破坏现有 `/api/admin/learning/today` |
| PR1.3 | 已完成 | response schema 按 v2.1：`stages[stage].by_noun.memory` 是 `number|null` 标量 |
| PR1.4 | 已完成 | 记录审核组件盘点表，避免强行统一 Drawer |
| PR1.5 | 已完成 | 增加/调整后端测试，覆盖 pipeline schema 与 today 兼容 |
| PR1.6 | 已完成 | 运行最小验证并记录证据 |

**计划改动文件**：

- `admin/routes/api/learning.py` 或新增同目录 pipeline 路由文件（以现有模式为准）
- `admin/routes/api/__init__.py`（仅当需要注册新 router）
- `tests/` 下对应 admin API 测试
- `docs/tracking/learning-pipeline-execution.md`

**验证计划**：

- 相关 pytest：学习 API / admin API 测试
- 静态检查：如改类型较多，再跑 `uv run pyright` 相关范围

**当前风险**：

- 工作区已有 `maintenance-log.md` 修改与 `docs/tracking/learning-pipeline.md` 未跟踪文件，执行时不能覆盖。
- PR1 必须保持 `/api/admin/learning/today` 现有 Dashboard 契约不变。

**PR1.1 盘点结果**：

| 项 | 代码事实 | PR1 处理 |
| --- | --- | --- |
| 现有学习 API | `admin/routes/api/learning.py` 只提供 `GET /learning/today`；`admin/routes/api/__init__.py` 已注册 `create_learning_router(ctx=ctx)` | 在同 router 内新增 `GET /learning/pipeline`，不改 `/learning/today` 返回结构 |
| Dashboard 依赖 | `DashboardView.vue` 调用 `/api/admin/learning/today` | 保持兼容，PR1 测试覆盖旧端点仍返回 `slang/style/stickers` |
| memory 数据源 | `services/memory/card_store.py` 的 `CardStore.list_cards(status=...)` 读取 `memory_cards` SQLite 表 | pipeline 的 memory 计数只读 `CardStore`，不回退旧 `.md` 文件 |
| slang 审核 UI | `SlangTermList.vue` emit detail/status，`SlangDetailDrawer.vue` 已独立存在 | PR1 只记录，PR5 复用 |
| style 审核 UI | `StyleView.vue` 列表内直接调用 `/style/expressions/{id}/status` | PR1 只记录，PR5 新建轻量 ReviewPanel |
| episode 审核 UI | `EpisodesView.vue` 详情抽屉和 approve/disable/restore 逻辑在主视图内 | PR1 只记录，PR5 再抽 ReviewPanel |
| memory_consolidator 审核 UI | `MemoryConsolidatorView.vue` 详情、编辑、approve/reject 在主视图内 | PR1 只记录，PR5 再抽 ReviewPanel |
| 测试位置 | 当前没有 `/learning/today` 或 `/learning/pipeline` 专门测试 | PR1 新增学习 API 测试文件，覆盖 pipeline schema 与 today 兼容 |

**PR1.2 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/routes/api/learning_pipeline.py` | 新增 | 实现 `GET /learning/pipeline` 只读聚合；阶段卡为库存快照，`date` 参数暂不影响计数 |
| `admin/routes/api/__init__.py` | 编辑 | 注册 `create_learning_pipeline_router(ctx=ctx)` |
| `admin/routes/api/learning.py` | 未改 | 保持 Dashboard 依赖的 `/learning/today` 兼容 |

**PR1.2 验证证据**：

- `python -m py_compile admin/routes/api/learning_pipeline.py admin/routes/api/__init__.py` → 通过。

**PR1.3 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `tests/test_admin_api_learning_pipeline.py` | 新增 | 覆盖 `/api/admin/learning/pipeline` schema、memory 标量口径、`/api/admin/learning/today` 兼容 |

**PR1.3 验证证据**：

- `uv run pytest tests/test_admin_api_learning_pipeline.py -q` → 未进入测试；当前沙箱下 `uv` 触发 macOS `system-configuration` panic。
- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `2 passed in 0.76s`。

**PR1.4 审核组件盘点表（带代码证据）**：

| Noun | 当前审核 UI 形态 | 代码证据 | PR5 处理建议 |
| --- | --- | --- | --- |
| slang | 已有独立详情 Drawer，列表通过 emit 打开详情和状态动作 | `SlangView.vue:16` import `SlangDetailDrawer`；`SlangTermList.vue:29-35` emit `open-detail` / `quick-status` / `review-ai` | 直接复用现有 Drawer/emit 语义 |
| style | 无 Drawer；列表内按钮直接调用状态 API | `StyleView.vue:224-234` 调 `/api/admin/style/expressions/{id}/status`；`StyleView.vue:601-610` 三个状态按钮 | 新建轻量 `StyleReviewPanel`，不要强抽 Drawer |
| episode | 详情/审核动作在主视图内 | `EpisodesView.vue:104-123` 状态过滤和 action 状态；`EpisodesView.vue:505-525` 操作 modal 在主视图内 | PR5 再抽 `EpisodeReviewPanel` |
| memory_consolidator | 详情、payload 编辑、approve/reject 都在主视图内 | `MemoryConsolidatorView.vue:104-135` detail/decide 状态；`MemoryConsolidatorView.vue:224-309` 保存和 decide API 调用 | PR5 再抽 `ConsolidatorReviewPanel` |
| memory | 无审核态；只读活跃/归档视角 | `CardStore` 状态枚举为 `active/superseded/expired`，见 `services/memory/card_store.py:34-45` | `/learning` 行只给详情，PR4 跳 `/memory?view=manage` |

**PR1.4 完成结论**：

- 不做统一 Drawer 抽象；后续采用多态 ReviewHost。
- memory 无 `pending`，candidate/review/hits 在 pipeline 中必须是 `null`，approved/archived 才有数值口径。

**PR1.5 / PR1.6 完成记录**：

| 验证项 | 证据 |
| --- | --- |
| 新 pipeline 路由语法检查 | `python -m py_compile admin/routes/api/learning_pipeline.py admin/routes/api/__init__.py` → 通过 |
| 新增后端测试 | `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `2 passed in 0.75s` |
| Ruff 限定检查 | `.venv/bin/python -m ruff check admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → `All checks passed!` |
| `uv` 状态 | `uv run pytest tests/test_admin_api_learning_pipeline.py -q` 在当前沙箱触发 macOS `system-configuration` panic；本轮验证改用仓库 `.venv` |

**PR1 最终改动文件**：

- `admin/routes/api/learning_pipeline.py`
- `admin/routes/api/__init__.py`
- `tests/test_admin_api_learning_pipeline.py`
- `docs/tracking/learning-pipeline-execution.md`

### PR2a · budget manager 吃 candidate

**开始时间**：2026-05-23

**任务拆解**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| PR2a.1 | 已完成 | 盘点 `provider_bus`、`budget_manager`、`types`、`client.py` 当前链路和测试位置 |
| PR2a.2 | 已完成 | 定义 accepted decision 数据结构，保留 `evidence_refs` / `metadata` |
| PR2a.3 | 已完成 | `PromptBudgetManager.process` 入参改为 `PromptBlockCandidate`，输出 `(PromptBlock[], AcceptedDecision[])` |
| PR2a.4 | 已完成 | 调整 provider bus / client 调用侧，让预算裁剪前不丢 `evidence_refs` |
| PR2a.5 | 已完成 | 保持 trace 行为：accepted/trimmed/rejected 仍完整记录，`PromptBlockTrace.evidence_refs` 不再恒空 |
| PR2a.6 | 已完成 | 增加/调整 budget manager 测试，覆盖 accepted decisions 与 trace |
| PR2a.7 | 已完成 | 运行最小验证并记录证据 |

**计划改动文件**：

- `services/block_trace/types.py`
- `services/block_trace/provider_bus.py`
- `services/block_trace/budget_manager.py`
- `services/llm/client.py`
- `tests/` 下 block trace / budget manager 相关测试

**当前风险**：

- PR2a 触碰 LLM prompt 热路径，必须保持现有 prompt block 文本和 trace 决策语义不变。
- PR2a 不引入 observation 写入和新表；业务写入留到 PR2b。

**PR2a.1 盘点结果**：

| 项 | 当前代码事实 | PR2a 动作 |
| --- | --- | --- |
| candidate 类型 | `PromptBlockCandidate` 已有 `candidate_id/evidence_refs/metadata`，见 `services/block_trace/types.py` | 复用，不改 `kernel.types.PromptBlock` |
| refs 丢失点 | `provider_bus.run_active()` 把 candidate 转成 `PromptBlock`，丢 `evidence_refs` | active 路径改为返回 candidate 给 budget manager |
| budget manager | `process(blocks: list[PromptBlock]) -> list[PromptBlock]`，trace 内 `candidate_id` 随机生成，`evidence_refs=()` | 改为 `process(candidates: list[PromptBlockCandidate]) -> tuple[list[PromptBlock], list[AcceptedDecision]]` |
| client 调用 | `client.py` active 模式先 `run_active()` extend blocks，再统一 `budget_manager.process(prompt_ctx.blocks)` | provider candidates 单独走 budget；普通 plugin blocks 需转换为 synthetic candidate，确保原 prompt 不丢 |
| provider refs | `EpisodeProvider` 已填 `evidence_refs=tuple(episode_ids)`；`StyleProvider` 当前未填 | PR2a 顺手补 style refs 如能从 store 返回；否则保留空 refs 并在 PR2b 前补业务写入所需路径 |
| 测试 | `tests/test_block_trace.py` 覆盖 budget manager 旧入参；`tests/test_providers.py` 覆盖 `run_active()` | 更新测试，新增 accepted decision 和 trace refs 断言 |

**PR2a 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `services/block_trace/types.py` | 编辑 | 新增 `AcceptedDecision`，保留 candidate/source/provider/evidence_refs/metadata/group_id/scope |
| `services/block_trace/budget_manager.py` | 编辑 | `process()` 改吃 `PromptBlockCandidate`，返回 `(surviving_blocks, accepted_decisions)`；trace 使用原 candidate_id 和 evidence_refs |
| `services/llm/client.py` | 编辑 | provider active 模式在有 budget manager 时走 `run_all()` candidates；普通 plugin PromptBlock 转 synthetic candidate 后统一预算裁剪 |
| `services/style/store.py` | 编辑 | 新增 `build_prompt_block_with_refs()` / `build_profile_prompt_block_with_refs()`，旧方法保持兼容 |
| `services/block_trace/style_provider.py` | 编辑 | style candidate 填 `evidence_refs`；无新 store 方法时 fallback 旧方法 |
| `tests/test_block_trace.py` | 编辑 | budget manager 测试改用 candidate，断言 accepted decisions 和 trace refs |
| `tests/test_providers.py` | 编辑 | style provider 测试覆盖 profile/expression refs |

**PR2a 验证证据**：

- `.venv/bin/python -m ruff check services/block_trace/types.py services/block_trace/budget_manager.py services/block_trace/__init__.py services/block_trace/style_provider.py services/style/store.py services/llm/client.py tests/test_block_trace.py tests/test_providers.py` → `All checks passed!`
- `.venv/bin/python -m pytest tests/test_block_trace.py tests/test_providers.py -q` → `33 passed in 0.49s`
- `.venv/bin/pyright services/block_trace/budget_manager.py services/block_trace/style_provider.py services/style/store.py` → `0 errors`
- `.venv/bin/python -m py_compile services/block_trace/types.py services/block_trace/budget_manager.py services/block_trace/style_provider.py services/style/store.py services/llm/client.py` → 通过
- `.venv/bin/pyright services/llm/client.py` 仍有既有 `object` 属性访问类型债；本次新增的 `layer` 类型错误已修复，剩余报错不是本轮 PR2a 新增。

### PR2b · 观测表 + accepted 写入

**开始时间**：2026-05-23

**任务拆解**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| PR2b.1 | 已完成 | 盘点 StyleStore / EpisodeStore 现有 schema、init 迁移模式和 observation 写入测试位置 |
| PR2b.2 | 已完成 | 新增 style/episode observations 表，含去重约束 |
| PR2b.3 | 已完成 | 新增 `record_observation` 方法，写入失败不影响主流程 |
| PR2b.4 | 已完成 | BudgetManager 仅对 `accepted_decisions` fire-and-forget 写 observation；trimmed/rejected 不写 |
| PR2b.5 | 已完成 | slang 继续不双写，保留 L1 后续待办 |
| PR2b.6 | 已完成 | 增加 accepted/trimmed/rejected observation 单测 |
| PR2b.7 | 已完成 | 运行最小验证并记录证据 |

**计划改动文件**：

- `services/style/store.py`
- `services/episodic/store.py`
- `services/block_trace/budget_manager.py` 或 `services/llm/client.py`
- `tests/` 下 style/episode observation 或 block_trace 相关测试

**当前风险**：

- observation 写入必须在 accepted 后发生；trimmed/rejected 明确不计。
- `slang_provider` 仍有 provide 内部 record 的既有风险，本轮 v2.1 不改，避免双写。

**PR2b.1 盘点结果**：

| 项 | 代码事实 | PR2b 动作 |
| --- | --- | --- |
| StyleStore schema | `services/style/store.py` 顶部集中定义 `_CREATE_*_TABLE` 与 `_CREATE_INDEXES`，`init()` 内执行建表/索引和 `_ensure_column` | 新增 `_CREATE_OBSERVATIONS_TABLE` 和 observation indexes，`init()` 内建表 |
| EpisodeStore schema | `services/episodic/store.py` 顶部集中定义 episodes/revisions 表和 `_CREATE_INDEXES`，`init()` 内建表/索引 | 新增 `_CREATE_OBSERVATIONS_TABLE` 和 observation indexes，`init()` 内建表 |
| 现有 observation | slang 有 `record_observation`；style 只有 `style_evidence`，episode 只有 `last_used_at` | 给 style/episode 增 `record_observation`，按 v2.1 UNIQUE 去重 |
| 写入点 | PR2a 已提供 `AcceptedDecision`，含 source/evidence_refs/message fallback 所需 request_id | BudgetManager 对 accepted decisions fire-and-forget；source=slang 跳过 |
| 测试落点 | `tests/test_block_trace.py` 已覆盖 budget decisions；`tests/test_style_store.py` / `tests/test_episode.py` 可覆盖 store 方法 | 增加 store 去重测试 + budget accepted-only 测试 |

**PR2b.2-PR2b.6 当前实现记录（待 PR2b.7 验证）**：

| 子任务 | 已实施文件 | 细化完成标准 |
| --- | --- | --- |
| PR2b.2 | `services/style/store.py`、`services/episodic/store.py` | 新增 `style_observations` / `episode_observations` 表；包含 `observed_at` 与 `scope/group_id` 索引；UNIQUE 分别为 `(expression_id, message_id, trigger_type)` / `(episode_id, message_id, trigger_type)` |
| PR2b.3 | `services/style/store.py`、`services/episodic/store.py` | 新增 `record_observation(...)`；空 target 返回 `False`；重复写入 `INSERT OR IGNORE` 后返回 `False` |
| PR2b.4 | `services/block_trace/budget_manager.py`、`plugins/chat/plugin.py` | `PromptBudgetManager` 注入 style/episode store getter；仅遍历 accepted decisions；按 `source` 路由到 style/episode，异常只记录 warning，不影响 `process()` 返回 |
| PR2b.5 | `services/block_trace/budget_manager.py` | `source == "slang"` 不在本轮预算写入里处理，避免与既有 `SlangProvider` 双写 |
| PR2b.6 | `tests/test_block_trace.py`、`tests/test_style_store.py`、`tests/test_episode.py` | 覆盖 accepted 写 observation、trimmed/rejected 不写、写入异常不破坏主流程、store 去重约束 |

**PR2b.7 开始前验证任务拆解**：

| 验证项 | 命令 / 检查 | 完成标准 |
| --- | --- | --- |
| PR2b.7.1 | `.venv/bin/python -m ruff check ...` | PR2b 相关 Python 文件 Ruff 通过 |
| PR2b.7.2 | `.venv/bin/python -m pytest tests/test_block_trace.py tests/test_style_store.py tests/test_episode.py -q` | block trace + style store + episode store 测试通过 |
| PR2b.7.3 | `.venv/bin/pyright services/block_trace/budget_manager.py services/style/store.py services/episodic/store.py` | PR2b 直接改动后端类型检查通过，或明确记录既有非本轮债 |
| PR2b.7.4 | `.venv/bin/python -m py_compile ...` | PR2b 相关模块语法检查通过 |
| PR2b.7.5 | `rg "record_observation" services/block_trace services/style services/episodic services/slang` | 确认 style/episode provider 不在 `provide()` 末尾直接写；budget manager + slang_provider 为当前允许调用点 |

**PR2b 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `services/style/store.py` | 编辑 | 新增 `style_observations` 表、索引、`record_observation()`；prompt block refs 方法沿用 PR2a 结果 |
| `services/episodic/store.py` | 编辑 | 新增 `episode_observations` 表、索引、`record_observation()` |
| `services/block_trace/budget_manager.py` | 编辑 | 对 accepted decisions fire-and-forget 写 style/episode observations；trimmed/rejected 不写；source=slang 跳过 |
| `plugins/chat/plugin.py` | 编辑 | 初始化 `PromptBudgetManager` 时注入 style/episode store getter |
| `tests/test_block_trace.py` | 编辑 | 覆盖 accepted 写 observation、trimmed/rejected 不写、写入异常不破坏 process |
| `tests/test_style_store.py` | 编辑 | 覆盖 style observations 表存在与 UNIQUE 去重 |
| `tests/test_episode.py` | 编辑 | 覆盖 episode observations 表存在与 UNIQUE 去重 |

**PR2b 验证证据**：

- `.venv/bin/python -m ruff check services/block_trace/budget_manager.py services/style/store.py services/episodic/store.py plugins/chat/plugin.py tests/test_block_trace.py tests/test_style_store.py tests/test_episode.py` → `All checks passed!`
- `.venv/bin/python -m pytest tests/test_block_trace.py tests/test_style_store.py tests/test_episode.py -q` → `61 passed in 2.15s`
- `.venv/bin/pyright services/block_trace/budget_manager.py services/style/store.py services/episodic/store.py` → `0 errors, 0 warnings, 0 informations`
- `.venv/bin/python -m py_compile services/block_trace/budget_manager.py services/style/store.py services/episodic/store.py plugins/chat/plugin.py` → 通过
- `rg "record_observation" services/block_trace services/style services/episodic services/slang` → style/episode provider 无直接写入；style/episode observation 写入点在 `services/block_trace/budget_manager.py`；slang 仍保留既有 store/provider 链路，符合 v2.1 L1 后续待办口径。

**PR2b 遗留风险**：

- slang observation 仍可能在 provider 阶段记录被预算裁掉的 block，v2.1 已明确留作 L1 后续待办，本轮不双写也不迁移。

### PR3 · 前端骨架

**开始时间**：2026-05-23

**设计决定**：

| 项 | 决定 |
| --- | --- |
| 页面角色 | 管道总览骨架；本 PR 不交付列表、审核或写操作 |
| 信息层级 | `AppPage` hero → `StageStrip` 横向阶段卡 → `PageToolbar` 筛选 → 阶段/noun 快照面板 |
| 组件复用 | `AppPage`、`AppCard`、`PageToolbar`、`EmptyState`、Naive UI 基础控件 |
| URL 边界 | 点击阶段卡、noun、date 用 `router.push`；group 输入和非法 query 归一用 `router.replace` |
| 保持不变 | 不改 SideMenu（PR6）、不改 Dashboard 深链（PR6）、不接 `/learning/items`（PR4） |

**任务拆解**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| PR3.1 | 已完成 | 新增 `/learning` router 记录，meta title 对齐“学习管道总览” |
| PR3.2 | 已完成 | 新建 `LearningView.vue`，读取 `/api/admin/learning/pipeline` 并渲染总览骨架 |
| PR3.3 | 已完成 | 新建 `StageStrip.vue`，按 5 阶段渲染总数、noun 拆分、当前态、空态和横向滚动 |
| PR3.4 | 已完成 | 实现 query 归一、阶段/noun/date push，group replace，非法 `stage/noun/date` 自动收敛 |
| PR3.5 | 已完成 | 增加加载/错误/警告状态，不造假数据 |
| PR3.6 | 已完成 | 运行前端最小验证并记录证据 |

**计划改动文件**：

- `admin/frontend/src/router/index.ts`
- `admin/frontend/src/views/learning/LearningView.vue`
- `admin/frontend/src/views/learning/components/StageStrip.vue`
- `docs/tracking/learning-pipeline-execution.md`
- `maintenance-log.md`

**验证计划**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`
- `cd admin/frontend && npm run build`

**当前风险**：

- PR3 不应提前承诺 `/learning/items`、内联审核、SideMenu 入口或 Dashboard 深链。
- 新页面必须保持 Calm Ops 风格，不能引入新色板、内联样式或未验证的假数据。

**PR3.1-PR3.5 当前实现记录（待 PR3.6 验证）**：

| 子任务 | 已实施文件 | 细化完成标准 |
| --- | --- | --- |
| PR3.1 | `admin/frontend/src/router/index.ts` | 新增 `/learning` route，`name='learning'`，`meta.title='学习管道总览'` |
| PR3.2 | `admin/frontend/src/views/learning/LearningView.vue` | 使用 `AppPage` + `PageToolbar`；调用 `/api/admin/learning/pipeline`；展示真实 stage totals / by_noun |
| PR3.3 | `admin/frontend/src/views/learning/components/StageStrip.vue`、`admin/frontend/src/views/learning/types.ts` | 5 阶段卡、当前态、空态透明度、tooltip noun 拆分、横向滚动 |
| PR3.4 | `LearningView.vue` | 非法 query 归一；stage/noun/date 切换 `router.push`；group 输入 `router.replace`；hits 阶段 date 归一为 `today` |
| PR3.5 | `LearningView.vue` | `loading/error/warnings/as_of` 状态；不显示假列表，不接 PR4/PR5 能力 |

**PR3 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/frontend/src/router/index.ts` | 编辑 | 新增 `/learning` route，当前不加入 SideMenu |
| `admin/frontend/src/views/learning/LearningView.vue` | 新增 | `/learning` 总览骨架，读取 `/api/admin/learning/pipeline`，渲染 StageStrip、筛选和阶段/noun 快照 |
| `admin/frontend/src/views/learning/components/StageStrip.vue` | 新增 | 5 阶段横向卡条，支持当前态、空态、tooltip 拆分和点击切阶段 |
| `admin/frontend/src/views/learning/types.ts` | 新增 | 共享 stage/noun 类型，避免 SFC 中导出类型 |
| `admin/frontend/src/components.d.ts` | 编辑 | 构建工具补充 `NRadioGroup` / `NRadioButton` 自动组件声明 |
| `admin/static/index.html` | 编辑 | `npm run build` 更新 SPA 入口与 vendor icon hash |

**PR3 验证证据**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 通过。
- `cd admin/frontend && npm run build` → 成功；`LearningView-*.js` 8.43 KB gzip 3.81 KB；`LearningView-*.css` 4.09 KB gzip 1.07 KB。
- `rg -n "style=|!important|#[0-9A-Fa-f]{3,6}|linear-gradient|radial-gradient" admin/frontend/src/views/learning` → 无新增内联样式、`!important`、硬编码色值或渐变。

**PR3 遗留风险**：

- 未做浏览器手测；本轮仅通过类型检查和生产构建验证。SideMenu 入口、Dashboard 深链、列表与审核仍留给后续 PR。

### PR4 · 列表

**开始时间**：2026-05-23

**任务拆解**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| PR4.1 | 已完成 | 盘点各 noun 表结构、状态映射、deep_link 与 review_drawer 口径 |
| PR4.2 | 已完成 | 新增 `GET /api/admin/learning/items`，按 noun fan-out 查询、归并排序、cursor 分页 |
| PR4.3 | 已完成 | 更新 pipeline hits 计数读取 PR2b style/episode observations，继续保持 memory hits 为 `null` |
| PR4.4 | 已完成 | 新增/调整后端测试，覆盖 schema、stage/noun/date、memory `/memory?view=manage` 深链 |
| PR4.5 | 已完成 | 新建 `LearningTable.vue`，接入 `/learning/items`，支持排序、加载更多、详情跳转 |
| PR4.6 | 已完成 | `LearningView.vue` 接入 items 列表；阶段/noun/date/group 变化重置列表；排序用 `router.replace`，加载更多不进 history |
| PR4.7 | 已完成 | 运行后端 + 前端最小验证并记录证据 |

**PR4.1 盘点结果**：

| Noun | 数据源 | 阶段映射 | 详情/审核口径 |
| --- | --- | --- | --- |
| slang | `slang_terms` + `slang_observations` | candidate=`status='candidate'` 且非 AI keep；review=AI keep 但 human 未审；approved=`approved`；hits=今日 observations；archived=`muted/expired` | `deep_link=/slang?id=...`，`review_drawer=slang` |
| style | `style_expressions` + PR2b `style_observations` | candidate/review=`pending`；approved=`approved`；hits=今日 observations；archived=`rejected/muted` | `deep_link=/style?id=...`，PR5 再接审核面板 |
| episode | `episodes` + PR2b `episode_observations` | candidate=`candidate`；review=`approved`；approved=`enabled_for_prompt`；hits=今日 observations；archived=`disabled` | `deep_link=/episodes?id=...`，PR5 再接审核面板 |
| memory | `memory_cards` | approved=`active`；archived=`expired`；candidate/review/hits 无口径 | 仅详情，`deep_link=/memory?view=manage`，不带 `card_id` |
| consolidator | `consolidator_candidates` | candidate=`dry_run/queued`；approved=`approved`；archived=`rejected`；review/hits 无口径 | `deep_link=/memory-consolidator`，`review_drawer=consolidator` |

**计划改动文件**：

- `admin/routes/api/learning_pipeline.py`
- `tests/test_admin_api_learning_pipeline.py`
- `admin/frontend/src/views/learning/LearningView.vue`
- `admin/frontend/src/views/learning/components/LearningTable.vue`
- `admin/frontend/src/views/learning/types.ts`
- `admin/static/index.html`
- `docs/tracking/learning-pipeline-execution.md`
- `maintenance-log.md`

**验证计划**：

- `.venv/bin/python -m ruff check admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py`
- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q`
- `.venv/bin/python -m py_compile admin/routes/api/learning_pipeline.py`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`
- `cd admin/frontend && npm run build`

**当前风险**：

- `items` 第一版采用 fan-out + 内存归并，不做 SQL union-all 优化；分页 cursor 是合并列表 offset 的不透明编码。
- `/learning` 本轮只交付“详情”跳转，不接审核 Drawer 或折返菜单，避免提前进入 PR5。

**PR4.2-PR4.4 当前完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/routes/api/learning_pipeline.py` | 编辑 | 新增 `GET /learning/items`；支持 `stage/noun/group/date/sort/limit/cursor`；fan-out 查询 slang/style/episode/memory/consolidator 后归并排序 |
| `admin/routes/api/learning_pipeline.py` | 编辑 | style/episode hits 阶段读取 `style_observations` / `episode_observations`；memory hits 保持 `null` |
| `tests/test_admin_api_learning_pipeline.py` | 编辑 | 新增 items schema、memory deep link、style/episode hits、cursor 分页测试；调整 seed 覆盖 PR2b observation 表 |

**PR4 后端验证证据**：

- `.venv/bin/python -m ruff check admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → `All checks passed!`
- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `5 passed in 1.55s`
- `.venv/bin/python -m py_compile admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → 通过

**PR4.5-PR4.6 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/frontend/src/views/learning/types.ts` | 编辑 | 新增 `LearningItem`、`LearningItemsResponse`、`LearningSortKey` |
| `admin/frontend/src/views/learning/components/LearningTable.vue` | 新增 | NDataTable 薄包装，展示类型、内容、来源群、时间、状态、置信度与详情按钮；支持空态和加载更多 |
| `admin/frontend/src/views/learning/LearningView.vue` | 编辑 | 接入 `/api/admin/learning/items`；stage/noun/date/group/sort 变化重置列表；排序走 `router.replace`；详情跳现有页面 |
| `admin/frontend/src/components.d.ts` | 编辑 | 构建工具补充 `NRadioGroup` / `NRadioButton` 声明 |
| `admin/static/index.html` | 编辑 | `npm run build` 刷新 SPA 入口与 vendor icon hash |

**PR4 前端验证证据**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 通过
- `cd admin/frontend && npm run build` → 成功；`LearningView-*.js` 12.66 KB gzip 5.23 KB；`LearningView-*.css` 5.16 KB gzip 1.21 KB
- `rg -n "style=|!important|#[0-9A-Fa-f]{3,6}|linear-gradient|radial-gradient" admin/frontend/src/views/learning` → 无匹配

**PR4 遗留风险**：

- `/learning/items` 第一版 cursor 为合并列表 offset 编码，适合当前小流量后台；若数据量显著增长，再改 SQL union-all 或每 noun 独立 cursor。
- 加载更多不写入 URL；排序用 `router.replace`，阶段/noun/date 仍按 PR3 用 `router.push`。

### PR5a · 接手首轮：extract-all 编排 + 轻量审核抽屉

**开始时间**：2026-05-23

**接手结论**：

- PR1-PR4 的未提交改动经复跑验证为可用基线，不是坏半截。
- PR5 尚未开工；本轮先交付可独立验证的 PR5a，避免一次性强抽 3 个旧页面 ReviewPanel。

**任务拆解**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| PR5a.1 | 已完成 | 新增 `POST /api/admin/learning/extract-all`，使用模块级 `asyncio.Lock`、per-noun timeout、`gather` 并发和部分失败结果 |
| PR5a.2 | 已完成 | 新增 extract-all 单测，覆盖部分失败、并发锁、锁释放、单 noun timeout |
| PR5a.3 | 已完成 | `/learning` hero 加「一键抽取」确认按钮，调用 extract-all 后刷新 pipeline/items |
| PR5a.4 | 已完成 | `LearningTable` 增加「审核」操作；memory 行仍只有详情 |
| PR5a.5 | 已完成 | 新增 `LearningReviewHost.vue` 轻量审核抽屉，按 row 类型调用现有 slang/style/episode/consolidator 状态 API |
| PR5a.6 | 已完成 | 跑后端 + 前端最小验证并记录证据 |

**改动文件**：

- `admin/routes/api/learning_pipeline.py`
- `tests/test_admin_api_learning_pipeline.py`
- `admin/frontend/src/views/learning/LearningView.vue`
- `admin/frontend/src/views/learning/components/LearningTable.vue`
- `admin/frontend/src/views/learning/components/LearningReviewHost.vue`
- `admin/static/index.html`

**验证证据**：

- `.venv/bin/python -m ruff check admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → `All checks passed!`
- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `8 passed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 通过
- `cd admin/frontend && npm run build` → 成功；`LearningView-*.js` 20.39 KB gzip 7.55 KB，`LearningView-*.css` 5.92 KB gzip 1.33 KB
- `rg -n "style=|!important|#[0-9A-Fa-f]{3,6}|linear-gradient|radial-gradient" admin/frontend/src/views/learning` → 无匹配

**PR5b 待办 / 风险**：

- 当前 extract-all 支持 `ctx.learning_extract_runners`、`ctx.slang_plugin.run_manual_extract` 和 `ctx.memory_consolidator.run_once`；生产 style manual extractor 尚未抽成可复用 runner，没有 runner 时返回 `skipped=true`。
- `LearningReviewHost` 是轻量状态处理抽屉，未完整复用 SlangDetailDrawer，也未抽 Style/Episode/Consolidator 专项 ReviewPanel；复杂编辑仍应跳原页面。
- SideMenu 入口和 Dashboard 深链仍留给 PR6。

### PR5b · 补强：production style runner + ReviewHost 边界

**开始时间**：2026-05-23

**任务拆解**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| PR5b.1 | 已完成 | 将 `admin/routes/api/style.py` 的手动抽取主体抽成 `run_style_manual_extract(...)`，原 `/style/extract/run` 与 `/learning/extract-all` 共用同一实现 |
| PR5b.2 | 已完成 | `learning_pipeline._run_style_extract()` 在生产 ctx 下直接调用 style runner，不再默认 `skipped=true` |
| PR5b.3 | 已完成 | 给 extract-all 增加 style runner 接线单测，确认无测试 runner 时也走生产 helper |
| PR5b.4 | 已完成 | 收紧 `LearningReviewHost`：保留轻量状态处理，增加详情页跳转入口和边界说明，复杂编辑不在本抽屉里复制 |
| PR5b.5 | 已完成 | 运行 PR5b 后端 + 前端验证并回填结果 |

**设计决定**：

- 不把 StyleView / EpisodesView / MemoryConsolidatorView 的主视图逻辑硬搬进 `/learning`；PR5b 只补“可安全处理状态”的 ReviewHost。
- style 抽取不走内部 HTTP；抽取主体上提成模块函数，由原 API 和 learning 编排共享，避免双实现漂移。
- SideMenu 和 Dashboard 深链不混入 PR5b，留给 PR6 单独验收。

**PR5b.1-PR5b.3 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/routes/api/style.py` | 编辑 | 新增 `run_style_manual_extract(...)`，原 `/style/extract/run` 改为解析参数后调用该 helper |
| `admin/routes/api/learning_pipeline.py` | 编辑 | `_run_style_extract()` 调用 production style helper；缺 `msg_log/llm_client` 时由 helper 返回明确错误，不再无条件 skipped |
| `tests/test_admin_api_learning_pipeline.py` | 编辑 | 新增 `test_learning_style_extract_uses_production_runner` |

**验证证据**：

- `.venv/bin/python -m ruff check admin/routes/api/style.py admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → `All checks passed!`
- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `9 passed`
- `.venv/bin/python -m py_compile admin/routes/api/style.py admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → 通过

**PR5b.4 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/frontend/src/views/learning/components/LearningReviewHost.vue` | 编辑 | 抽屉底部增加关闭 / 打开原页面操作；`/learning` 保持轻量状态处理，复杂编辑回到原页面 |

**PR5b.4 验证证据**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 通过
- `rg -n "style=|!important|#[0-9A-Fa-f]{3,6}|linear-gradient|radial-gradient" admin/frontend/src/views/learning` → 无匹配

**PR5b 收口验证证据**：

- `.venv/bin/python -m ruff check admin/routes/api/style.py admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → `All checks passed!`
- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `9 passed`
- `cd admin/frontend && npm run build` → 成功；`LearningView-*.js` 20.73 KB gzip 7.66 KB，`LearningView-*.css` 5.92 KB gzip 1.33 KB

**PR5 最终遗留**：

- `LearningReviewHost` 仍是轻量状态处理抽屉，不承载完整编辑；复杂编辑通过「打开原页面」进入原 noun 页面。
- slang observation 仍按 v2.1 L1 后续待办，不在本 PR 中迁移到 budget accepted 写入。

### PR6 · SideMenu + Dashboard 深链 + 上线

**开始时间**：2026-05-23

**任务拆解**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| PR6.1 | 已完成 | SideMenu「日常」分组在黑话/表达前增加「学习管道」入口，路由 `/learning` 可高亮 |
| PR6.2 | 已完成 | Dashboard 待办项深链切到 `/learning`：黑话候选 → candidate/slang，AI 复核 → review/slang，表达待审 → review/style |
| PR6.3 | 已完成 | Dashboard 今日学习卡点击切到 `/learning` 对应阶段，保留表情包原入口 |
| PR6.4 | 已完成 | 更新 `maintenance-log.md` 与本文档，记录 PR6 完成项与验证证据 |
| PR6.5 | 已完成 | 跑前端类型检查、构建、learning/dashboard/sidebar 样式扫描和最终后端 smoke |

**设计决定**：

- 不重组 SideMenu 分组，仅在学习相关页面前插入一个总览入口。
- Dashboard 是入口，`/learning` 是处理台；待办项直接落到对应 stage+noun。
- 表情包不属于本期 learning pipeline noun，Dashboard 表情包卡继续跳 `/stickers`。

**PR6 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/frontend/src/layouts/components/SideMenu.vue` | 编辑 | 在「日常」分组的学习相关页面前新增「学习管道」入口，复用 `AnalyticsOutline`；现有 activeKey 规则可直接高亮 `/learning` |
| `admin/frontend/src/views/dashboard/DashboardView.vue` | 编辑 | 待办项、primary shortcut、今日学习黑话/风格卡改为 `/learning` 深链；表情包卡保留 `/stickers` |
| `admin/static/index.html` | 编辑 | `npm run build` 刷新 entry / vendor icon hash |
| `maintenance-log.md` | 编辑 | 新增 PR5b + PR6 收口交接记录 |

**PR6 验证证据**：

- `.venv/bin/python -m ruff check admin/routes/api/style.py admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → `All checks passed!`
- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `9 passed`
- `.venv/bin/python -m py_compile admin/routes/api/style.py admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → 通过
- `.venv/bin/pyright admin/routes/api/learning_pipeline.py admin/routes/api/style.py` → `0 errors`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 通过
- `cd admin/frontend && npm run build` → 成功；`LearningView-*.js` 20.73 KB gzip 7.66 KB，`DashboardView-*.js` 28.16 KB gzip 9.83 KB，entry `index-CryzHcyH.js`
- `rg -n "style=|!important|#[0-9A-Fa-f]{3,6}|linear-gradient|radial-gradient" admin/frontend/src/views/learning admin/frontend/src/layouts/components/SideMenu.vue` → 无匹配
- 同样扫描 `DashboardView.vue` 仍命中既有动态宽度 / tone style 与旧 `linear-gradient`；本轮只改路由深链，没有新增 Dashboard 样式。

**PR6 遗留风险**：

- 未启动浏览器做真实点击手测；本轮以类型检查、生产构建和代码路径核对收口。
- `LearningReviewHost` 仍是轻量状态处理抽屉，完整编辑继续跳原 noun 页面。
- slang observation 仍按 v2.1 L1 后续待办，不在本轮迁移到 budget accepted 写入。

### L1 · slang accepted-only observation

**开始时间**：2026-05-23

**目标**：

- 将 slang prompt 注入观测从“provider 产出/旧路径不可判定是否最终进入 prompt”的风险口径，收敛为和 style/episode 一致的 `PromptBudgetManager` accepted-only 写入。
- 保留 `SlangPlugin.on_message` 的群聊文本命中 `message_match` 观测，不改变学习候选、复核、工具查询或已有消息命中逻辑。

**任务拆解（开工前细化）**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| L1.1 | 已完成 | 盘点当前 slang provider / store / budget manager / ChatPlugin 接线与测试位置，确认本轮只移动 prompt 注入观测，不动 message_match |
| L1.2 | 已完成 | `SlangStore` 新增 `build_prompt_block_with_refs()`，返回实际进入 block 的 term_id refs；旧 `build_prompt_block()` 保持兼容 |
| L1.3 | 已完成 | `SlangProvider` 优先调用 with_refs 方法并填充 `PromptBlockCandidate.evidence_refs`；无新方法时 fallback 旧文本方法 |
| L1.4 | 已完成 | `PromptBudgetManager` 增加 `slang_store_getter`，仅对 accepted slang decisions 写 `record_observation(reason="prompt_inject")`；trimmed/rejected 不写 |
| L1.5 | 已完成 | `plugins/chat/plugin.py` 初始化 `PromptBudgetManager` 时注入 `ctx.slang_store` getter |
| L1.6 | 已完成 | 补充/调整 tests：provider refs、accepted 写 slang observation、trimmed/rejected 不写、异常隔离 |
| L1.7 | 已完成 | 运行最小验证：ruff、pytest block_trace/providers/slang_store、pyright 相关后端文件 |
| L1.8 | 已完成 | 更新本文档和 `maintenance-log.md`，记录完成项、验证证据和遗留风险 |

**设计边界**：

- 不改 `slang_observations` 表结构；本轮用现有 `reason/context/raw_text` 字段记录 prompt accepted 观测，避免引入迁移风险。
- 不回填历史数据；上线后新 prompt accepted 事件自然进入今日命中。
- 不改 `/learning` hits 查询；它继续读取 `slang_observations` 今日记录。

**L1.1 盘点结果**：

| 项 | 代码事实 | L1 处理 |
| --- | --- | --- |
| Provider | `services/block_trace/slang_provider.py` 只调用 `store.build_prompt_block()` 并返回一个 slang candidate，当前 `evidence_refs=()` | L1.3 改为优先调用 with_refs，candidate 带实际 term_id refs |
| Store | `services/slang/store.py` 的 `build_prompt_block()` 已按 max chars 截断实际注入 terms，但只返回文本 | L1.2 增加 `build_prompt_block_with_refs()`，复用同一截断逻辑返回 `(text, term_ids)` |
| BudgetManager | `services/block_trace/budget_manager.py` 已对 style/episode accepted decisions 写 observation，没有 slang store getter | L1.4 增加 slang 分支，只有 accepted decisions 写 `reason="prompt_inject"` |
| ChatPlugin 接线 | `plugins/chat/plugin.py` 初始化 `PromptBudgetManager` 时已注入 style/episode getter | L1.5 增加 `slang_store_getter=lambda: getattr(ctx, "slang_store", None)` |
| message_match | `plugins/slang/plugin.py:on_message` 仍对群聊文本命中调用 `record_hit(... reason="message_match")` | 保持不变；这是用户消息命中，不是 prompt 注入命中 |

**L1 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `services/slang/store.py` | 编辑 | 新增 `build_prompt_block_with_refs()`，返回实际写入 prompt 的 block 文本和 term_id refs；旧 `build_prompt_block()` 改为兼容包装 |
| `services/block_trace/slang_provider.py` | 编辑 | 优先调用 with_refs 方法并把 refs 填入 `PromptBlockCandidate.evidence_refs`；无新方法时 fallback 旧方法 |
| `services/block_trace/budget_manager.py` | 编辑 | 新增 `slang_store_getter` 与 accepted-only slang observation 写入；reason 为 `prompt_inject:<request_id>`，重复 refs 去重 |
| `plugins/chat/plugin.py` | 编辑 | 初始化 `PromptBudgetManager` 时注入 `ctx.slang_store` getter |
| `tests/test_providers.py` | 编辑 | 覆盖 `SlangProvider` candidate 带 refs |
| `tests/test_block_trace.py` | 编辑 | 覆盖 accepted slang 写 observation，trimmed/rejected slang 不写 |
| `tests/test_slang_store.py` | 编辑 | 覆盖 with_refs 返回 term_id 且旧方法文本兼容 |
| `maintenance-log.md` | 编辑 | 新增 L1 收口交接记录 |

**L1 验证证据**：

- `.venv/bin/python -m pytest tests/test_block_trace.py tests/test_providers.py tests/test_slang_store.py -q` → `52 passed`
- `.venv/bin/python -m ruff check services/block_trace/budget_manager.py services/block_trace/slang_provider.py services/slang/store.py plugins/chat/plugin.py tests/test_block_trace.py tests/test_providers.py tests/test_slang_store.py` → `All checks passed!`
- `.venv/bin/pyright services/block_trace/budget_manager.py services/block_trace/slang_provider.py` → `0 errors`
- `.venv/bin/python -m py_compile services/block_trace/budget_manager.py services/block_trace/slang_provider.py services/slang/store.py plugins/chat/plugin.py tests/test_block_trace.py tests/test_providers.py tests/test_slang_store.py` → 通过
- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `9 passed`
- `.venv/bin/python -m ruff check admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → `All checks passed!`

**L1 遗留风险**：

- `slang_observations` 表结构未新增 request_id 字段；本轮按设计边界使用 `reason="prompt_inject:<request_id>"` 表示 prompt accepted 观测。
- `SlangPlugin.on_message` 的 `message_match` 观测保留不变，因此 `/learning` slang hits 仍同时包含用户消息命中和 prompt accepted 注入观测；如后续需要拆分，可在 L2 增加 API 分桶展示。
- 未做浏览器真实点击手测；PR6 遗留的人工路径验收仍未完成。

### L2 · MemoryView `?card_id=` 定位 + 自动打开 Drawer

**开始时间**：2026-05-23

**目标**：

- `/learning` 的 memory 行不再只跳到 `/memory?view=manage`，而是带上目标 `card_id`。
- `/memory?view=manage&card_id=...` 进入管理视图后自动打开目标卡编辑 Drawer，降低“跳过去还要手找”的运营摩擦。
- 不改变 Memory 管理页既有列表、筛选、编辑保存、归档/恢复等业务行为。

**任务拆解（开工前细化）**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| L2.1 | 已完成 | 盘点 `MemoryConsoleView`、`MemoryView`、memory API、`/learning/items` memory deep link 和测试断言 |
| L2.2 | 已完成 | `MemoryConsoleView` 在规范化 `view` 与切换 browse/manage 时保留 `card_id` 等已有 query |
| L2.3 | 已完成 | `MemoryView` 监听 `route.query.card_id`，优先复用当前列表卡片；列表没有时调用既有 `GET /api/admin/memory/cards/{card_id}` 获取详情 |
| L2.4 | 已完成 | 命中目标卡后调用既有 `openEdit(card)` 自动打开 Drawer；缺失或 API 失败时给用户提示但不破坏页面加载 |
| L2.5 | 已完成 | `/api/admin/learning/items` memory 行 detail URL 改为 `/memory?view=manage&card_id=<card_id>` |
| L2.6 | 已完成 | 更新后端测试对 memory deep link 的断言，并补充前端类型安全所需的状态/guard |
| L2.7 | 已完成 | 运行后端测试、ruff、前端 `vue-tsc`、生产构建和相关样式扫描 |
| L2.8 | 已完成 | 回填本文档 L2 完成记录、验证证据、遗留风险，并更新 `maintenance-log.md` |

**设计边界**：

- 不新增 memory 列表过滤参数；定位只依赖现有 `GET /api/admin/memory/cards/{card_id}`。
- 不在 L2 中重构 Memory 管理页布局，也不搬运 `/learning` 的审核逻辑。
- 目标卡自动打开 Drawer 是核心验收；表格滚动/高亮如需大量 DOM 侵入，本轮不强做。

**计划改动文件**：

- `admin/frontend/src/views/memory/MemoryConsoleView.vue`
- `admin/frontend/src/views/memory/MemoryView.vue`
- `admin/routes/api/learning_pipeline.py`
- `tests/test_admin_api_learning_pipeline.py`
- `docs/tracking/learning-pipeline-execution.md`
- `maintenance-log.md`

**验证计划**：

- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q`
- `.venv/bin/python -m ruff check admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`
- `cd admin/frontend && npm run build`
- `rg -n "style=|!important|#[0-9A-Fa-f]{3,6}|linear-gradient|radial-gradient" admin/frontend/src/views/memory admin/frontend/src/views/learning`

**L2.1 盘点结果**：

| 项 | 代码事实 | L2 处理 |
| --- | --- | --- |
| MemoryConsole query | `MemoryConsoleView.vue` 对非法/缺失 `view` 执行 `router.replace({ query: { view: 'browse' } })`，`setView(view)` 也只保留 `view` | L2.2 改为 `{ ...route.query, view }`，避免丢 `card_id` |
| MemoryView Drawer | `MemoryView.vue` 已有 `openEdit(card?: Card)`、`drawerVisible`、`editingCard` 和表格行编辑按钮 | L2.3/L2.4 复用既有编辑入口，不新增 Drawer 结构 |
| 单卡 API | `admin/routes/api/memory.py` 已有 `GET /memory/cards/{card_id}`，返回 `_card_to_dict(card)` 或 `{error: ...}` | L2.3 在当前列表找不到目标卡时调用该 API |
| learning memory link | `_collect_memory_items()` 当前统一 `deep_link="/memory?view=manage"` | L2.5 改为追加 `card_id` query |
| 测试断言 | `test_learning_items_memory_deeplink_and_date_filter` 断言 deep link 不含 `card_id` | L2.6 改为断言携带 `card_id=mem_active` |

**L2.2 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/frontend/src/views/memory/MemoryConsoleView.vue` | 编辑 | `watchEffect` 默认 `view=browse` 与 `setView(view)` 均改为保留 `...route.query`，避免丢失 `card_id` |

**L2.3 / L2.4 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/frontend/src/views/memory/MemoryView.vue` | 编辑 | 新增 `route.query.card_id` watcher、当前列表查找、单卡 API fallback 与目标卡 Drawer 自动打开 |
| `admin/frontend/src/views/memory/MemoryView.vue` | 编辑 | 目标卡缺失时提示“未找到目标记忆卡片”，加载失败时提示错误；异步返回前校验当前 query，避免快速切换时打开旧卡 |

**L2.5 / L2.6 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/routes/api/learning_pipeline.py` | 编辑 | memory item deep link 从 `/memory?view=manage` 改为 `/memory?view=manage&card_id=<urlencoded card_id>` |
| `tests/test_admin_api_learning_pipeline.py` | 编辑 | `test_learning_items_memory_deeplink_and_date_filter` 改为断言 `card_id=mem_active` |

**L2 验证证据**：

- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `9 passed`
- `.venv/bin/python -m ruff check admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → `All checks passed!`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 通过
- `cd admin/frontend && npm run build` → 成功；`MemoryConsoleView-*.js` 27.62 KB gzip 8.98 KB，entry `index-EUIh6UKR.js`
- `rg -n "style=|!important|#[0-9A-Fa-f]{3,6}|linear-gradient|radial-gradient" admin/frontend/src/views/memory admin/frontend/src/views/learning` → 无匹配

**L2 遗留风险**：

- 未做浏览器真实点击手测；代码路径已保证 `/memory?view=manage&card_id=...` 自动调用既有编辑 Drawer。
- 表格行滚动/高亮未强做；本轮核心验收是自动打开目标卡，避免为 DOM 高亮引入额外侵入。

### L3 · trimmed block 也计 hits

**开始时间**：2026-05-23

**目标**：

- prompt budget 中被 `trimmed` 的 style/episode/slang block 也写入 observation，使 `/learning` hits 能反映“内容进入 prompt 但被压缩”的真实命中。
- 继续保持 `rejected` 不计 hits；完全没进入 prompt 的 block 不应被算作命中。
- 不回到 provider 阶段写 observation，避免重新引入 L1/PR2b 已修掉的“未经过 budget 也计 hits”问题。

**任务拆解（开工前细化）**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| L3.1 | 已完成 | 盘点 `PromptBudgetManager` decision/trace 结构、现有 accepted-only observation 写入与 tests 覆盖 |
| L3.2 | 已完成 | 将 observation 触发范围从 accepted decisions 扩展到 accepted + trimmed decisions，保留 rejected 不写 |
| L3.3 | 已完成 | observation trigger/reason 能区分 trimmed 与完整 accepted，便于后续排查 hits 来源 |
| L3.4 | 已完成 | 补充/调整 block_trace 测试：style/episode/slang trimmed 写 observation，rejected 不写，重复 refs 去重 |
| L3.5 | 已完成 | 运行后端热路径验证：pytest block_trace/providers/store 相关、ruff、pyright、py_compile |
| L3.6 | 已完成 | 回填本文档 L3 完成记录、验证证据、遗留风险，并更新 `maintenance-log.md` |

**设计边界**：

- 不改 observation 表结构；用现有 `trigger_type`/`reason` 字段记录 `prompt_inject_trimmed` 或等价标识。
- 不把 `rejected` 计入 hits；预算完全拒绝意味着该 evidence 没有进入 prompt。
- 不修改 `/learning` hits 聚合 SQL；它按 observation 表自然看到新增 trimmed 记录。

**计划改动文件**：

- `services/block_trace/budget_manager.py`
- `tests/test_block_trace.py`
- 可能涉及 `tests/test_providers.py` / store tests（以盘点为准）
- `docs/tracking/learning-pipeline-execution.md`
- `maintenance-log.md`

**验证计划**：

- `.venv/bin/python -m pytest tests/test_block_trace.py tests/test_providers.py tests/test_slang_store.py tests/test_style_store.py tests/test_episode.py -q`
- `.venv/bin/python -m ruff check services/block_trace/budget_manager.py tests/test_block_trace.py`
- `.venv/bin/pyright services/block_trace/budget_manager.py`
- `.venv/bin/python -m py_compile services/block_trace/budget_manager.py tests/test_block_trace.py`

**L3.1 盘点结果**：

| 项 | 代码事实 | L3 处理 |
| --- | --- | --- |
| 返回值 | `PromptBudgetManager.process()` 返回 `(surviving_blocks, accepted_decisions)`；调用侧 `services/llm/client.py` 目前丢弃 `_accepted_decisions` | 保持返回值 accepted-only，避免改变外部语义 |
| trimmed 行为 | remaining > 0 时生成裁剪后的 `PromptBlock` 加入 `surviving`，trace decision 为 `trimmed` | L3 将 trimmed 也纳入 observation 写入，因为它实际进入了 prompt |
| observation 写入 | `_fire_and_forget_observations(accepted_decisions, ...)` 只吃 accepted | 增加独立的 observation decision 列表，包含 accepted + trimmed |
| rejected 行为 | remaining == 0 时只记录 trace，不进入 `surviving` | 继续不写 observation |
| 去重 | slang 已对 refs `dict.fromkeys` 去重；style/episode 当前逐 ref 写，store UNIQUE 去重 | L3 不扩 schema；测试覆盖 slang 重复 refs，style/episode 以 trigger 区分 trimmed |

**L3.2-L3.4 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `services/block_trace/types.py` | 编辑 | `AcceptedDecision` 增加 `decision` 字段，默认 `accepted`，用于 observation 元数据标明预算结果 |
| `services/block_trace/budget_manager.py` | 编辑 | 新增 `observation_decisions`，accepted + trimmed 都进入 observation 写入；返回的 `accepted_decisions` 仍只含完整 accepted |
| `services/block_trace/budget_manager.py` | 编辑 | trimmed slang reason 记为 `prompt_inject_trimmed:<request_id>`；style/episode trigger 记为 `*_inject_trimmed`，meta 增加 `budget_decision` |
| `tests/test_block_trace.py` | 编辑 | 更新 trimmed/rejected observation 测试，并新增 slang/style/episode trimmed 全源覆盖 |

**L3 验证证据**：

- `.venv/bin/python -m pytest tests/test_block_trace.py tests/test_providers.py tests/test_slang_store.py tests/test_style_store.py tests/test_episode.py -q` → `94 passed`
- `.venv/bin/python -m ruff check services/block_trace/budget_manager.py services/block_trace/types.py tests/test_block_trace.py` → `All checks passed!`
- `.venv/bin/pyright services/block_trace/budget_manager.py services/block_trace/types.py` → `0 errors`
- `.venv/bin/python -m py_compile services/block_trace/budget_manager.py services/block_trace/types.py tests/test_block_trace.py` → 通过

**L3 遗留风险**：

- 不回填历史 trimmed；上线后新请求自然产生 trimmed observation。
- style/episode 的 trimmed trigger 会作为独立 `trigger_type` 写入，同一 request 中同一 ref 若同时完整/裁剪注入会被计为不同 observation；这是为了保留“进入 prompt 的两条 block”事实。

### L4 · extract-all 进度 SSE / run_id 查询

**开始时间**：2026-05-23

**目标**：

- `POST /api/admin/learning/extract-all` 不再只在所有 noun 完成后返回结果，而是创建 `run_id` 并提供进度查询。
- 前端 `/learning` 一键抽取后能显示 slang/style/consolidator 的运行态、完成/失败/跳过结果和最后更新时间。
- 保留现有并发锁、per-noun timeout 和部分失败语义；已有调用方仍可从 POST 返回体读取最终 `results`。

**任务拆解（开工前细化）**：

| 子任务 | 状态 | 完成标准 |
| --- | --- | --- |
| L4.1 | 已完成 | 盘点现有 extract-all 后端编排、前端按钮状态、API 测试与项目 SSE 工具 |
| L4.2 | 已完成 | 设计 run registry：`run_id`、status、per-noun status/result、started/updated/finished、错误信息与内存保留策略 |
| L4.3 | 已完成 | 后端 `POST /learning/extract-all` 创建 run 并返回 `run_id`；运行过程中逐 noun 更新进度，完成后保留结果 |
| L4.4 | 已完成 | 新增 `GET /learning/extract-all/{run_id}` 查询接口；如项目已有 SSE 模式可追加 SSE，否则先用 query polling 满足 L4 |
| L4.5 | 已完成 | 更新后端测试：返回 `run_id`、查询 running/completed/not_found、锁冲突语义不退化 |
| L4.6 | 已完成 | 前端 LearningView 接入 run progress：提交后轮询 run query，显示每个 noun 状态，完成/失败后刷新 pipeline/items |
| L4.7 | 已完成 | 运行后端与前端验证：pytest/ruff/pyright/vue-tsc/build/style scan |
| L4.8 | 已完成 | 回填本文档 L4 完成记录、验证证据、遗留风险，并更新 `maintenance-log.md` |

**设计边界**：

- L4 目标写作是 “SSE / run_id 查询”；为降低风险，优先交付 run_id 查询 + 前端轮询。只有在本仓已有可复用 SSE 工具时才补 SSE。
- run registry 先采用进程内内存结构，不引入新表；这是 admin 手动抽取的短生命周期运行态。
- 不改 slang/style/consolidator 各自 runner 的内部实现；只在 learning 编排层记录 per-noun 状态。

**计划改动文件**：

- `admin/routes/api/learning_pipeline.py`
- `tests/test_admin_api_learning_pipeline.py`
- `admin/frontend/src/views/learning/LearningView.vue`
- `admin/frontend/src/views/learning/types.ts`
- 可能涉及 `admin/frontend/src/views/learning/components/*`（以现有结构为准）
- `docs/tracking/learning-pipeline-execution.md`
- `maintenance-log.md`

**验证计划**：

- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q`
- `.venv/bin/python -m ruff check admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py`
- `.venv/bin/pyright admin/routes/api/learning_pipeline.py`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`
- `cd admin/frontend && npm run build`
- `rg -n "style=|!important|#[0-9A-Fa-f]{3,6}|linear-gradient|radial-gradient" admin/frontend/src/views/learning`

**L4.1 盘点结果**：

| 项 | 代码事实 | L4 处理 |
| --- | --- | --- |
| 后端编排 | `POST /learning/extract-all` 直接 await `_run_extract_all()`；内部 `asyncio.Lock` + `gather` 并发 slang/style/consolidator | L4 增加 run registry；同步模式保留，前端用异步模式 |
| 进度粒度 | 当前只在全部完成后返回 `results`，没有 per-noun running/pending 状态 | L4 run snapshot 增加 `nouns[noun].status/result/updated_at` |
| 前端按钮 | `LearningView.vue` 中 `runExtractAll()` 等 POST 完成后 toast，再刷新 pipeline/items | L4 改为 `wait:false` 启动 run，并轮询 `GET /learning/extract-all/{run_id}` |
| SSE 现状 | `admin/routes/api/events.py` 和 `useSSE.ts` 是全局 admin event stream，已有 log/group/cache/block_trace 事件 | 本轮不扩全局 SSE；交付 run_id query polling，满足 L4 “SSE / run_id 查询”的查询分支 |
| 测试 | 现有 extract-all tests 覆盖 partial failure、lock、timeout、production style runner | L4 调整结果 schema，新增 status query/not_found/async running 覆盖 |

**L4.2 设计决定**：

- run registry 使用模块级内存 dict，run_id 前缀 `learn_ext_`；只保留最近 20 条，避免长期增长。
- run status：`queued` / `running` / `completed` / `partial_failed` / `failed` / `not_found`。
- noun status：`pending` / `running` / `completed` / `skipped` / `failed` / `timeout` / `cancelled`。
- `POST /learning/extract-all` 支持 body `wait=false`；默认 `wait=true` 保持现有“请求返回最终 results”的兼容模式。

**L4.3-L4.5 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/routes/api/learning_pipeline.py` | 编辑 | 新增 `_extract_all_runs` 进程内 registry、active run guard、run/noun status snapshot、最近 20 条保留策略 |
| `admin/routes/api/learning_pipeline.py` | 编辑 | `POST /learning/extract-all` 支持 `wait=false` 异步启动；默认 `wait=true` 兼容旧同步结果 |
| `admin/routes/api/learning_pipeline.py` | 编辑 | 新增 `GET /learning/extract-all/{run_id}` 查询接口，not found 返回 `status=not_found` |
| `tests/test_admin_api_learning_pipeline.py` | 编辑 | 覆盖 partial failure status、锁冲突 active run_id、timeout noun status、async polling、status endpoint/not_found |

**L4 后端阶段验证证据**：

- `.venv/bin/python -m pytest tests/test_admin_api_learning_pipeline.py -q` → `11 passed`
- `.venv/bin/python -m ruff check admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → `All checks passed!`
- `.venv/bin/pyright admin/routes/api/learning_pipeline.py` → `0 errors`
- `.venv/bin/python -m py_compile admin/routes/api/learning_pipeline.py tests/test_admin_api_learning_pipeline.py` → 通过

**L4.6 完成记录**：

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `admin/frontend/src/views/learning/types.ts` | 编辑 | 新增 `LearningExtractRun`、run/noun status、result 类型 |
| `admin/frontend/src/views/learning/LearningView.vue` | 编辑 | 一键抽取 body 增加 `wait=false`；启动后轮询 `GET /learning/extract-all/{run_id}` |
| `admin/frontend/src/views/learning/LearningView.vue` | 编辑 | 增加 extract run 进度面板，展示 run_id、整体状态、slang/style/consolidator 三路状态和结果摘要 |
| `admin/frontend/src/views/learning/LearningView.vue` | 编辑 | run 完成、部分失败、跳过或 not_found 后停止轮询，并刷新 pipeline/items |

**L4 前端阶段验证证据**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` → 通过
- `rg -n "style=|!important|#[0-9A-Fa-f]{3,6}|linear-gradient|radial-gradient" admin/frontend/src/views/learning` → 无匹配
- `cd admin/frontend && npm run build` → 成功；`LearningView-*.js` 24.46 KB gzip 8.81 KB，`LearningView-*.css` 6.98 KB gzip 1.45 KB，entry `index-D_KAw0Od.js`

**L4 遗留风险**：

- 本轮选择 run_id query polling，未扩展全局 `/events` SSE；原因是现有 SSE 是 admin 全局事件流，L4 只需要一次性 run 私有进度，轮询更小、更可回滚。
- run registry 为进程内内存，服务重启后历史 run 查询会返回 `not_found`；适合 admin 手动抽取短生命周期进度，不承担审计存储职责。

## L 系列收口结论

- L1：slang prompt observation 已迁移为 budget accepted-only。
- L2：memory deep link 已支持 `card_id` 定位并自动打开 Drawer。
- L3：trimmed prompt block 已计入 hits observation，rejected 仍不计。
- L4：extract-all 已支持 `run_id` 查询和前端进度轮询。
