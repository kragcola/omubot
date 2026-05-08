# 维护日志

> 按时间倒序记录部署、配置变更、故障处理等运维事件。

---

## 2026-05-08 Context Knowledge System 评测闸门推进

**变更类型**：backend / tests / docs

**内容**：

- 按追踪文档未完成清单继续推进 Phase 5，先补 ContextPlugin 接管前的自动评测地基。
- 新增 `services/context/eval.py`：
  - `ContextEvalCase / ContextHitExpectation / ContextEvalResult / ContextEvalSummary`
  - 支持从 JSON fixture 加载 query 用例
  - 输出命中率、漏召、禁入误召、重复注入和 Prompt pack 长度
- 新增 `tests/fixtures/context_eval/basic.json`：
  - 覆盖 `memory_card / doc_chunk / graph_fact` 三类上下文命中
  - 标注不应命中的禁入内容，作为 Prompt 接管前安全闸门
- 新增 `tests/test_context_eval.py`：
  - 验证正常 memory/doc/graph 召回通过
  - 验证漏召、禁入内容、重复注入会被评测结果标红
- 新增 DeepSeek native stable prefix 回归测试：
  - 动态上下文变化只进入 tail metadata
  - system stable prefix 不随本轮上下文变化

**验证**：

- `.venv/bin/pytest tests/test_context_service.py tests/test_context_eval.py tests/test_prompt.py -q` 通过，`17 passed`
- `.venv/bin/ruff check services/context/eval.py services/context/__init__.py tests/test_context_eval.py tests/test_prompt.py` 通过

**交接说明**：

- `ContextPlugin` 仍保持默认关闭；本轮只是补“可证明不回归”的评测闸门。
- 下一步应把真实群聊、私聊、知识库问题继续沉淀到 `tests/fixtures/context_eval/`，再做旧 Memo/Knowledge 注入与新 ContextPlugin 注入的正式对比。

---

## 2026-05-08 ContextPlugin 接管前评测守卫

**变更类型**：backend / tests / docs

**内容**：

- 继续按 Context Knowledge System 未完成清单推进 P0 接管前评测。
- 扩充 `tests/fixtures/context_eval/basic.json`：
  - 新增无关问题案例，验证无关 query 不应误召 memory/doc/graph，也不应注入禁入内容。
- 新增 `tests/test_context_plugin.py`：
  - 使用真实 `PluginBus.fire_on_pre_prompt()` 路径，覆盖 manifest 权限检查与插件优先级。
  - 验证 ContextPlugin 接管时只出现一个“上下文资料”动态块。
  - 验证接管时旧 `KnowledgePlugin` 的“知识库”动态块和 `MemoPlugin` 的“记忆卡片”动态块不会重复注入。
  - 验证关闭接管后旧 Memo/Knowledge 动态注入路径可恢复，保留回滚能力。

**验证**：

- `.venv/bin/pytest tests/test_context_eval.py tests/test_context_plugin.py tests/test_prompt.py -q` 通过，`16 passed`
- `.venv/bin/ruff check services/context/eval.py services/context/__init__.py tests/test_context_eval.py tests/test_context_plugin.py tests/test_prompt.py` 通过

**交接说明**：

- 当前已经有“接管不重复、关闭可回滚”的自动守卫。
- `ContextPlugin` 仍不建议默认开启；下一步应继续补真实群聊、私聊、知识库 query fixture，并观察 Prompt pack 长度与漏召情况。

---

## 2026-05-08 主人场景上下文评测扩充

**变更类型**：tests / docs

**内容**：

- 继续扩充 ContextPlugin 接管前评测覆盖。
- 新增 `tests/fixtures/context_eval/owner_scenarios.json`：
  - 私聊用户记忆：验证私聊可召回用户记忆。
  - 群聊记忆：验证群聊可召回当前群记忆。
  - 作用域隔离：同关键词同时存在于用户、当前群、其他群时，不允许串 scope。
  - 文档知识：验证知识库文档 chunk 可独立召回。
  - 图谱事实：验证派生 graph fact 可召回。
  - 无关问题：验证无关 query 不为了填充 Prompt 误召旧资料。
- 扩展 `tests/test_context_eval.py`，新增主人场景评测测试和隔离断言。

**验证**：

- `.venv/bin/pytest tests/test_context_eval.py -q` 通过，`3 passed`
- `.venv/bin/ruff check tests/test_context_eval.py services/context/eval.py` 通过

**交接说明**：

- 当前 fixture 是可公开的脱敏/合成主人场景，不包含真实聊天记录。
- 下一步如果要进一步提高上线信心，应从实际群聊/私聊中提取脱敏 query，并记录 pack 长度、漏召和误召趋势。

---

## 2026-05-08 Context Knowledge System P2 执行

**变更类型**：backend / frontend / plugins / docs / tests

**内容**：

- 知识库索引持久化：
  - 新增 `services/knowledge/store.py`
  - 新增 `KnowledgeIndexStore`
  - SQLite 表：`knowledge_sources`、`knowledge_chunks`
  - `KnowledgeService` 支持 `index_db_path`
  - `KnowledgePlugin` 默认使用 `storage/knowledge_index.db`
  - 重启后可从 SQLite 恢复 chunk/source 索引
  - `reindex()` 按 source hash 复用未变化文件，只重切变化 source
- 知识库配置补齐：
  - `plugins/knowledge/config.default.json` 新增 `index_db_path`
  - `plugins/knowledge/config.schema.json` 增加 Web 可配置说明
- 上下文指标 API：
  - `ContextService` 最近检索快照新增 `hit_type_counts`、`hit_source_counts`、`duplicate_count`、`pack_chars`、`omitted_count`
  - 新增 `ContextService.metrics()`
  - 新增 `/api/admin/context/metrics`
- Admin Web：
  - `/admin/knowledge` 新增 `评测指标` 页签
  - 展示最近查询数、Miss 率、平均/最大 Prompt Pack、重复率、省略命中、命中来源、命中类型和最近查询列表
  - 知识库页头新增 `SQLite 索引 / 内存索引` 状态标签
- 文档：
  - 更新 `docs/wiki/Knowledge-System.md`
  - 更新 Context Knowledge System 追踪文档，标记 P2 完成

**验证**：

- `.venv/bin/pytest tests/test_knowledge.py tests/test_context_service.py tests/test_admin_api.py -k "knowledge or context" -q` 通过，`21 passed, 45 deselected`
- `.venv/bin/ruff check services/knowledge services/context tests/test_knowledge.py tests/test_context_service.py admin/routes/api/context.py admin/routes/api/knowledge.py plugins/knowledge/plugin.py` 通过
- `.venv/bin/python -m py_compile services/knowledge/*.py services/context/*.py admin/routes/api/context.py plugins/knowledge/plugin.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过，Vite 仅输出第三方 `#__PURE__` 注释提示

**交接说明**：

- 当前仍是“运行时内存 BM25/ngram 检索 + SQLite 持久索引”的轻量方案，不引入向量库。
- SQLite 持久索引用于更快恢复和增量 reindex，不改变知识库检索排序算法。
- 真实脱敏 query 仍需要继续沉淀到 fixture，指标面板会随着真实对话积累更有价值。

---

## 2026-05-08 Context Knowledge System P1 全量执行

**变更类型**：backend / frontend / plugins / docs / tests

**内容**：

- 正式启用 `ContextPlugin` 动态上下文接管：
  - `plugins/context/config.default.json` 默认 `enabled=true`
  - 默认 `takeover_dynamic_prompt=true`
  - `MemoPlugin` 保留稳定全局索引、提取和工具职责，不再重复注入实体记忆动态块
  - `KnowledgePlugin` 保留知识库服务和工具入口，不再重复注入文档 chunk 动态块
- 接入图谱自动候选抽取：
  - `services/knowledge_graph/extractor.py` 从占位边界升级为轻量确定性抽取器
  - `KnowledgeGraphService.extract_from_context_hits()` 从 `memory_card/doc_chunk` 中抽取 subject/predicate/object
  - `ContextPlugin` 在本轮 Prompt pack 完成后提交抽取结果，避免影响同一轮 Prompt
  - 高置信自动 active，中置信 pending，低置信忽略
  - 新增 active fact / pending candidate 去重，避免每轮重复写入
- 增强图谱证据链与回滚治理：
  - Graph fact 返回 evidence 列表
  - 新增 relationship detail / rollback / supersede API
  - rollback 会撤销当前 fact；如果当前 fact 取代了旧 fact，会恢复旧 fact
  - supersede 会创建新 active fact，并把旧 fact 标记为 superseded
- Web 知识库图谱页增强：
  - active fact 卡片显示证据、来源、fact_id、取代关系
  - 支持填写备注回滚事实
  - 支持用新的 subject/predicate/object 取代事实
- 更新 `docs/wiki/Knowledge-System.md` 和 Context Knowledge System 追踪文档，说明默认接管、自动抽取、证据链与回滚规则。

**验证**：

- `.venv/bin/pytest tests/test_knowledge_graph.py tests/test_context_plugin.py tests/test_admin_api.py -k "knowledge_graph or context_plugin or graph" -q` 通过，`11 passed, 47 deselected`
- `.venv/bin/pytest tests/test_context_service.py tests/test_context_eval.py tests/test_context_plugin.py tests/test_prompt.py tests/test_knowledge_graph.py tests/test_admin_api.py -k "context or knowledge_graph or graph or prompt" -q` 通过，`32 passed, 45 deselected`
- `.venv/bin/ruff check services/knowledge_graph tests/test_knowledge_graph.py plugins/context/plugin.py tests/test_context_plugin.py admin/routes/api/knowledge.py plugins/chat/plugin.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过，Vite 仅输出第三方 `#__PURE__` 注释提示

**交接说明**：

- 如果生产环境需要临时回滚动态上下文接管，可在插件配置中关闭 `ContextPlugin.enabled` 或 `takeover_dynamic_prompt`。
- 当前抽取器是轻量确定性规则，不是 LLM 抽取器；复杂事实仍需要后续增强或人工治理。
- 本轮没有迁移 `CardStore`，知识图谱仍是派生层，不替代记忆卡片或文档知识库。

---

## 2026-05-08 Context Eval 增加 Prompt Pack 长度预算

**变更类型**：backend / tests / docs

**内容**：

- 为 ContextPlugin 接管前评测补充“上下文长度预算”。
- `ContextEvalCase` 新增 `max_pack_chars`，用于描述某个案例期望实际注入的上下文长度上限。
- `ContextEvalResult` 新增：
  - `max_pack_chars`
  - `pack_budget_exceeded`
- `ContextEvalSummary` 新增 `pack_budget_violations`。
- `tests/fixtures/context_eval/basic.json` 与 `owner_scenarios.json` 为每个案例增加 pack 长度基线。
- `tests/test_context_eval.py` 新增失败路径：当 pack 实际长度超过 `max_pack_chars` 时，评测结果必须失败。

**验证**：

- `.venv/bin/pytest tests/test_context_eval.py -q` 通过，`4 passed`
- `.venv/bin/ruff check services/context/eval.py tests/test_context_eval.py` 通过

**交接说明**：

- `max_chars` 仍是打包硬截断预算；`max_pack_chars` 是评测期望预算，用来发现“没超系统上限但已经比预期膨胀”的回归。
- 后续真实脱敏 query 进入 fixture 时，应同步设定合理的 `max_pack_chars`，避免 ContextPlugin 接管后 Prompt 成本悄悄上涨。

---

## 2026-05-08 知识库导入指导文档

**变更类型**：docs

**内容**：

- 新增 `docs/wiki/Knowledge-System.md`，作为当前知识库使用与导入指南。
- 文档说明：
  - 文档知识库、记忆卡片、知识图谱三者的关系
  - 当前知识库配置结构与默认行为
  - Markdown 索引规则：只扫描 `.md`，按 `##` 二级标题切 chunk
  - BM25/ngram 轻量检索规则
  - 推荐导入目录、写作模板、不推荐写法
  - Web 中重建索引、搜索核对、上下文调试的操作流程
  - 常见问题排查清单
- 更新 `docs/wiki/_Sidebar.md`，增加“知识库”入口。

**验证**：

- 本轮为文档更新，已检查 wiki 侧栏链接和文档关键章节。

**交接说明**：

- 推荐把日常知识资料放到 `docs/knowledge/`，并将知识库插件 `dir` 配为 `docs/knowledge`，避免默认递归 `docs` 时把审计报告、开发计划等内部文档混入日常聊天知识库。

---

## 2026-05-08 知识库图谱加载失败审计与兼容修复

**变更类型**：frontend / tests / ops-audit

**问题**：

- `/admin/knowledge` 显示“图谱信息加载失败”。
- 审计 `docker logs qq-bot` 确认当前运行容器对新版接口返回 404：
  - `GET /api/admin/knowledge/stats`
  - `GET /api/admin/knowledge/sources`
  - `GET /api/admin/knowledge/graph/entities`
  - `GET /api/admin/knowledge/graph/relationships`
  - `GET /api/admin/knowledge/graph/candidates`
- 结论：运行容器后端仍是旧版本，新前端已经请求新版知识/图谱 API，属于前后端版本错位，不是图谱数据库或 SQL 损坏。

**修复**：

- `/admin/knowledge` 增加旧后端兼容降级：
  - `/knowledge/stats` 404 时降级到旧 `/knowledge` 统计
  - `/knowledge/search` 404 时降级到旧 `/knowledge?q=...`
  - `/context/search`、`/knowledge/graph/*` 404 时不再弹“加载失败”，改为展示“当前后端还没有新版接口，请重建/重启 Bot”
- 后端测试补充：
  - 图谱服务缺失时 graph API 返回 `available=false`，不应 500
  - 图谱服务存在时能返回 entities / relationships / candidates

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（Vite 仅输出第三方 `#__PURE__` 注释提示）
- `.venv/bin/pytest tests/test_admin_api.py -k "knowledge_graph or knowledge_api" -q` 通过，`3 passed, 46 deselected`

**交接说明**：

- 兼容修复只避免误报和白屏，不会让旧容器凭空拥有图谱 API。
- 要启用完整图谱、上下文调试和新版知识库来源管理，仍需重建/重启 `qq-bot` 容器。

---

## 2026-05-08 知识库 Web 治理台落地

**变更类型**：frontend / docs / tests

**内容**：

- `/admin/knowledge` 从单一关键词搜索页升级为知识系统治理台：
  - `文档源`：展示来源文件、路径、索引状态、chunk 数、source hash、跳过原因，并支持重建索引
  - `搜索核对`：调用结构化搜索接口，展示 title/source/chunk_id/score/content
  - `上下文调试`：输入本轮消息、用户 ID、群 ID，展示 memory/doc/graph 命中和最终 Prompt pack
  - `图谱关系`：展示 active graph facts 与实体列表
  - `候选队列`：展示 pending graph candidates，支持通过和拒绝
- 页面视觉保持 `Calm Ops / 雾青控制台` 风格：
  - 顶部改为紧凑状态总览，不再堆大 KPI 卡
  - 主内容按治理任务分页，减少重复信息和首屏噪声
  - 空状态补充下一步说明，避免只显示“暂无数据”
- 本轮只改 Web 展示和操作入口，不改变后端知识库、ContextService、GraphService 的数据语义。

**影响范围**：`admin/frontend/src/views/knowledge/KnowledgeView.vue`、`admin/static/assets/*`、`docs/superpowers/plans/2026-05-08-omubot-context-knowledge-system.md`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（Vite 仅输出第三方 `#__PURE__` 注释提示）
- `.venv/bin/pytest tests/test_knowledge.py tests/test_context_service.py tests/test_knowledge_graph.py tests/test_admin_api.py -k "knowledge or context" -q` 通过，`18 passed, 45 deselected`

**交接说明**：

- 现在可以先用 `/admin/knowledge` 的“上下文调试”页做 ContextPlugin 开启前人工评测。
- 图谱自动抽取和标准化检索评测集仍未实现；候选队列目前依赖后端已有 candidate 数据。
- 如果页面仍显示旧版，需要重启 Vite dev server 或刷新已构建静态资源缓存。

---

## 2026-05-08 Context Knowledge System 后端落地

**变更类型**：backend / plugins / docs / tests

**内容**：

- 新增进度追踪文档 `docs/superpowers/plans/2026-05-08-omubot-context-knowledge-system.md`。
- 修复知识库 Admin 断链：
  - `KnowledgeBase` 升级为结构化 `KnowledgeService`，保留 `retrieve()` 兼容旧调用
  - 新增 `KnowledgeHit`，返回 `content/source/title/score/chunk_id`
  - Admin API 懒解析运行时知识库实例，不再依赖启动快照
- 新增 `services/context`：
  - `ContextHit / ContextService / MemoryContextSource / KnowledgeContextSource`
  - 新增 `/api/admin/context/search` 与 `/api/admin/context/recent`
  - 调试出口可解释 memory/doc/graph 三类命中
- 新增 opt-in 系统插件 `plugins/context`：
  - 默认关闭，避免立即替换生产 Prompt 注入
  - 开启后可由 ContextPlugin 接管动态上下文；Memo/Knowledge 保留旧路径回滚
- 新增轻量 SQLite 知识图谱底座：
  - `services/knowledge_graph`
  - 支持高置信事实自动 active、中置信候选审核、低置信忽略
  - 新增知识图谱实体、关系、候选队列 Admin API

**验证**：

- `.venv/bin/pytest tests/test_card_store.py tests/test_retrieval.py tests/test_memo_tools.py tests/test_knowledge.py tests/test_context_service.py tests/test_knowledge_graph.py tests/test_prompt.py -q` 通过，`111 passed`
- `.venv/bin/pytest tests/test_admin_api.py -k "memory or knowledge or context" -q` 通过，`3 passed, 44 deselected`
- `.venv/bin/pytest tests/test_plugin_bus.py -q` 通过，`45 passed`
- `.venv/bin/python -m py_compile services/knowledge/*.py services/context/*.py services/knowledge_graph/*.py plugins/context/*.py plugins/knowledge/plugin.py plugins/memo/plugin.py plugins/chat/plugin.py admin/routes/api/context.py admin/routes/api/knowledge.py` 通过

**交接说明**：

- 本轮没有迁移 `memory_cards`，也没有改变 `CardStore` 作为生产记忆权威存储的地位。
- `ContextPlugin` 默认关闭，当前生产聊天仍沿用 MemoPlugin/KnowledgePlugin 的旧注入路径；后续开启前建议先用 `/api/admin/context/search` 对典型群聊查询做命中评测。
- 图谱服务已建库和 API，但尚未接入自动抽取与前端治理页。

---

## 2026-05-08 知识库模块工作流审计

**变更类型**：docs / audit

**内容**：

- 新增 [知识库模块审计表](docs/audits/knowledge-module-audit-2026-05-08.md)，按阶段拆解当前知识库工作流：
  - 插件发现、配置读取、索引构建、分词、聊天触发、检索、Prompt 注入、DeepSeek V4 tail metadata、Admin API、前端页面、测试覆盖
  - 明确区分“服务层检索可用”和“Admin 搜索链路断裂”
- 审计确认：
  - `KnowledgeBase` 只有 `retrieve()`，没有 Admin API 当前期待的 `search()`
  - `KnowledgePlugin` 当前未把内部 `_kb` 挂到 `ctx.knowledge_base`
  - Admin 知识库页多数情况下只能看到空统计或空结果
  - 当前索引只扫描 `docs` 一级 Markdown，不递归索引 `docs/wiki`、`docs/audits`
- 给出 P0/P1/P2 风险表与后续修复路线。

**验证**：

- `.venv/bin/pytest tests/test_knowledge.py -q` 通过，`10 passed`
- Python 探针确认 `KnowledgeBase`：`has_reload=True`、`has_retrieve=True`、`has_search=False`
- Python 探针确认当前 `docs` 一级索引为 `93` 个 chunk

**交接说明**：

- 本轮只做审计留档，未修改知识库运行代码。
- 后续建议优先修复 Admin API 与运行时 `KnowledgeBase` 实例断链。

## 2026-05-08 配置页新手友好改版

**变更类型**：frontend / backend / docs / tests

**内容**：

- `/admin/config` 从“全量配置编辑器”改为“主人设置向导 + 高级维护区”：
  - 顶部 4 个大指标卡收口为紧凑状态条，显示保存状态、配置路径、解析模式和重启提示
  - 首屏新增 5 个任务卡：模型与 API、群聊回复、回复节奏、连接与视觉、权限与私聊
  - 点击任务卡后进入对应设置面板，顶部说明“适合谁改 / 改了影响什么 / 生效建议”
- 配置字段展示增强：
  - `ConfigFieldEditor` 支持中文展示名、帮助说明、示例、推荐值、风险等级和重启提示
  - API Key、Admin Token、NapCat 地址、白名单等高风险字段会显示明确提示
  - 前端先用本地字段文案映射兜底，后续可逐步迁移到后端 schema
- 保存体验调整：
  - “预览变更”从高级区前移到保存按钮旁
  - 保存确认会提示变更数量、涉及模块、高风险字段和重启建议
  - 保存成功后如涉及建议/必须重启字段，会提示在线重启 Bot
- 高级维护区收口：
  - 默认折叠
  - 拆为完整配置、高级 JSON、备份恢复、保存审计四块
  - 低频字段会递归保留在完整配置中，不会因为同模块有常用字段就被整体隐藏
- 后端配置 schema 增加可选展示字段透传能力：
  - `display_label / help / example / recommended / risk_level / restart_hint`
  - 当前接口保持兼容，旧前端和旧字段不受影响

**影响范围**：`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/config/ConfigFieldEditor.vue`、`admin/frontend/src/views/config/types.ts`、`admin/routes/api/config.py`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（Vite 仅输出第三方 PURE 注释提示）
- `.venv/bin/python -m py_compile admin/routes/api/config.py` 通过
- `.venv/bin/pytest tests/test_config_loader.py tests/test_admin_api.py -k config -q` 通过，`29 passed, 41 deselected`

**交接说明**：

- 本轮未改变配置文件格式、保存 API、预览 API、备份恢复 API 的业务语义。
- Raw JSON 仍可用于兜底，但默认不再作为新手主流程。
- 构建时 `scripts/cleanup-appledouble.sh` 清理了 AppleDouble 伴生文件；如开发服务器仍显示旧页面，请重启 Vite dev server。

## 2026-05-08 插件中心视觉重设计与权限状态文案修复

**变更类型**：frontend / backend / tests

**内容**：

- 修复 Vite 开发环境插件详情深链刷新白屏：
  - `admin/frontend/vite.config.ts` 增加 `/admin/plugins/` 前缀 SPA fallback
  - `/admin/plugins/element_detector?tab=settings` 这类动态路由刷新时会返回前端 `index.html`，不再被代理到后端
  - 移除 Vue Router 中“浏览器刷新时强制回仪表盘”的旧逻辑，插件配置深链刷新后会留在原页面
- 二次压实插件中心视觉：
  - 插件卡片由固定 2 列大卡改为自适应紧凑网格，卡片最小高度从大面板收口为高密度操作卡
  - 卡片按钮改为成组布局并加宽，启停开关固定在右侧，避免按钮在大卡中显得过小
  - 插件描述改为单行截断，版本、工具数、命令数保留为紧凑信息行
- 二次整改配置页：
  - 对象数组配置（如 `element_detector.rules`）改为全宽设置区，不再让左侧说明占据半屏
  - 规则卡片内字段使用高密度网格，移动端自动回退为单列
- 插件中心首页从大指标卡改为紧凑摘要条，默认聚焦用户插件、可配置数量、需关注项和系统锁定项。
- 插件卡片改为两列“书架卡”布局，统一按钮尺寸、底部操作区、状态标签和描述截断，减少空白与按钮不协调问题。
- 插件详情页重排为返回按钮、详情头、分段标签和内容面板；配置页增加设置状态条、结构化规则编辑器和固定保存栏。
- `permission_limited` 不再作为用户可见错误状态：
  - `PluginBus.plugin_health()` 增加 `display_state / display_label / display_type`
  - Admin 插件 API 对健康 payload 做展示字段归一化
  - 前端将权限门控显示为“按权限运行”，高级健康页保留原始状态与权限跳过次数

**影响范围**：`kernel/bus.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`tests/test_plugin_bus.py`、`tests/test_admin_api.py`

**验证**：

- `.venv/bin/python -m py_compile kernel/bus.py admin/routes/api/plugins.py` 通过
- `.venv/bin/pytest tests/test_plugin_bus.py tests/test_admin_api.py -k plugin -q` 通过，`58 passed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（最近一次构建通过，Vite 仅输出第三方 PURE 注释提示）
- `curl http://localhost:5173/admin/plugins/element_detector?tab=settings` 本地探测超时；如使用 Vite dev server，需要重启 dev server 让 `vite.config.ts` 生效

**交接说明**：

- `health.state` 仍保留原始诊断值；Web 展示请优先使用 `display_label / display_type`。
- `permission_denials` 和 `last_permission_denied` 只作为详情健康页诊断信息，不应在插件卡片中作为异常主状态展示。

## 2026-05-08 插件全目录化与插件中心配置体验修复

**变更类型**：feature / backend / frontend / plugins / docs / tests

**内容**：

- 取消根目录单文件插件：
  - `chat / datetime / debug_commands / echo / group_admin / history_loader / http_api / web_fetch / web_search` 全部迁移为 `plugins/<name>/plugin.py`
  - 所有运行时插件补齐 `__init__.py / plugin.json / config.default.json / config.schema.json`
  - `vision` 改为 `plugins/vision/plugin.json` 系统能力包，只读展示，不作为可启停插件
  - `PluginBus.discover_plugins()` 不再加载根目录 `.py` 单文件
  - `PluginIndexService` 将旧根目录单文件标记为 `legacy_single_file_unsupported` blocked
- 插件配置标准化补齐：
  - 新增时间、调试指令、复读、群管理、HTTP API、网页抓取、网页搜索的 Web 配置 schema
  - 要素察觉 `rules` 改为结构化对象数组 schema，Web 不再只能编辑裸 JSON
  - 插件配置统一保存到 `storage/plugins/config/<name>.json`
- 插件中心体验修复：
  - `/admin/plugins` 默认只展示用户插件
  - “显示系统插件”作为弱化高级入口；系统卡片固定显示“系统级 / 锁定 / 不可关闭”，无关闭开关
  - 用户插件卡片增加 `详情` 与 `配置` 两个清晰入口
  - `/admin/plugins/:name?tab=settings` 可直达配置页
  - 详情页左上角新增“返回插件中心”，并拆为 `概览 / 配置 / 命令工具 / 健康 / 包来源`
- Admin API 行为同步：
  - `GET /api/admin/plugins` 默认隐藏系统插件，`include_system=true` 才返回系统级和系统能力包
  - 系统级或 `read_only` 插件的 settings API 返回空 schema，保存请求会拒绝

**影响范围**：`plugins/*`、`kernel/bus.py`、`services/plugin_index.py`、`services/tools/*`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`docs/wiki/Plugins.md`、`docs/wiki/Plugin-Development.md`、`docs/architecture.md`、`docs/project-info.md`

**验证**：

- `.venv/bin/python -m py_compile ...` 通过
- `.venv/bin/python` 对真实 `plugins/` 执行发现：运行插件 `19` 个，`vision` 为系统 capability
- `.venv/bin/pytest tests/test_plugin_bus.py tests/test_admin_api.py tests/test_config_loader.py -k plugin -q` 通过，`55 passed`
- `.venv/bin/pytest tests/test_echo.py tests/test_history_self_messages.py tests/test_history_sticker.py -q` 通过，`33 passed`
- `.venv/bin/pytest tests/test_bilibili.py tests/test_element_detector.py tests/test_echo.py tests/test_sticker_store.py tests/test_slang_plugin.py tests/test_history_self_messages.py tests/test_history_sticker.py -q` 通过，`142 passed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `scripts/cleanup-appledouble.sh` 已在构建后清理伴生文件

**交接说明**：

- 旧根目录 `plugins/<name>.py` 不再是迁移目标；后续新增插件必须使用目录插件格式。
- 系统插件仍由后端强锁定，前端隐藏只是体验层收口，不是安全边界。
- 本轮尚未开放远程插件安装，插件商店仍是本地只读治理入口。

## 2026-05-08 插件规范化 Phase 0 与插件中心

**变更类型**：feature / backend / frontend / plugins / docs / tests

**内容**：

- 插件规范化地基落地：
  - `bilibili / dream / element_detector / food / knowledge / memo / sticker` 迁移为目录插件
  - `affection / schedule / slang` 补齐 `config.default.json` 与 `config.schema.json`
  - 所有有配置插件统一使用 `config.default.json` + `storage/plugins/config/<name>.json`
  - 旧插件 TOML 不再作为主配置路径读取
- Manifest v3 接入：
  - `plugin.json` 增加中文/英文名、系统/用户级、启停策略、分类、权限、能力、配置规格、商店元数据
  - `PluginBus` 会为显式注册插件和自动发现插件统一应用 manifest
  - 系统级或 `toggle_policy=locked` 插件无法被运行时关闭
- 后端插件治理扩展：
  - `PluginConfigStore` 改为 per-plugin JSON 覆盖文件，并保留旧 `plugin-config.json` 只读迁移 fallback
  - Admin 插件 API 返回 `effective_values / locked / tier / toggle_policy / config_spec / store`
  - 新增只读 `GET /api/admin/plugins/store`
  - 关闭系统级插件时 API 返回 `系统级插件无法关闭`，且不写入 `plugin-state.json`
- Web 插件中心上线：
  - 主侧栏恢复 `插件` 入口
  - `/admin/plugins` 拆为 `用户插件 / 系统插件 / 插件商店 / 治理队列`
  - 插件卡片显示中文名、英文名、插件 ID、版本、健康、工具数、命令数和配置状态
  - `/admin/plugins/:name` 提供插件详情与 JSON Schema 自动配置表单
  - 插件商店首版只读展示本地包与未来市场字段，不提供远程安装
- 文档同步：
  - `docs/wiki/Plugins.md` 更新为 manifest v3、JSON 配置、插件中心与只读商店现状

**影响范围**：`kernel/types.py`、`kernel/bus.py`、`kernel/config.py`、`services/plugin_config.py`、`services/plugin_index.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`admin/frontend/src/layouts/components/SideMenu.vue`、`admin/frontend/src/router/index.ts`、`plugins/*`、`docs/wiki/Plugins.md`

**验证**：

- `.venv/bin/pytest tests/test_config_loader.py tests/test_bilibili.py tests/test_sticker_store.py tests/test_slang_plugin.py tests/test_plugin_bus.py tests/test_admin_api.py -q` 通过，`209 passed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `.venv/bin/python` 对真实 `plugins/` 目录执行发现与索引：发现运行插件 `19` 个，本地索引 `20` 个，含 `vision` 系统能力卡

**交接说明**：

- `vision` 继续是系统服务，不进入普通插件启停流；插件中心只作为系统能力卡展示。
- Web 保存插件配置只写 `storage/plugins/config/<name>.json`，不会修改仓库内默认配置。
- 当前没有开放远程插件安装；插件商店接口是本地只读索引，为未来生态预留。

## 2026-05-08 DeepSeek V4 原生模式接入

**变更类型**：feature / backend / frontend / llm / tests / ops

**内容**：

- LLM Provider 体系新增原生 `deepseek` 路径：
  - `kernel/config.py` 扩展 `llm.api_format`，支持 `deepseek`
  - `services/llm/providers/deepseek.py` 新增 DeepSeek 原生 `/chat/completions` provider
  - 默认请求启用 `stream=true` 与 `stream_options.include_usage=true`
  - 解析 `prompt_cache_hit_tokens / prompt_cache_miss_tokens / completion_tokens_details.reasoning_tokens`
  - 兼容回退读取 `prompt_tokens_details.cached_tokens`
- DeepSeek V4 reasoning / replay 链路修正：
  - 原生 provider 会在 tool-call assistant 历史上回放 `reasoning_content`
  - 缺失 reasoning 时会自动补占位值，避免 DeepSeek thinking/tool loop 因非法 payload 报错
  - 最近一轮 replay token 规模与 sanitizer 介入状态会被记录到运行时观测
- Prompt 结构为 V4 前缀缓存做了专项重排：
  - `KnowledgePlugin` 的检索结果从 `static` 改为 `dynamic`
  - DeepSeek native 模式下，`state_board` 与所有动态 PromptBlock 不再进入稳定 system 前缀
  - 这些高频变化信息会被拼到当前 user turn 尾部的 `<turn_meta>` 块中
  - `plugin_static / plugin_stable` 继续留在 system prompt 中，保护稳定前缀缓存
- 压缩与 user scope 调整：
  - DeepSeek V4 主聊天路径使用更晚的 compact 阈值 `0.88`
  - 请求级 `user_id` 改为稳定哈希：群聊 `grp_*`，私聊 `dm_*`，后台任务 `sys_*`
  - 不再把原始 QQ 号或群号直接发给 DeepSeek 原生接口
- Usage 与系统观测扩展：
  - `services/llm/usage.py` 为 `llm_calls` 增加 `provider_kind / prompt_cache_hit_tokens / prompt_cache_miss_tokens / reasoning_replay_tokens`
  - 旧库会在 `init()` 时自动 `ALTER TABLE` 补齐字段
  - `admin/routes/api/providers.py` 与 `SystemView.vue` 现在可显示：
    - 当前 provider mode：`native / native-beta / anthropic-compat / openai-compat`
    - 最近一轮 cache hit%
    - 最近一轮 reasoning replay tokens
    - 最近一次 payload sanitizer 是否介入
  - Provider 测试接口现在会回传 usage 摘要与当前运行模式

**影响范围**：`kernel/config.py`、`services/llm/provider.py`、`services/llm/providers/{anthropic,openai,deepseek}.py`、`services/llm/client.py`、`services/llm/prompt_builder.py`、`services/llm/usage.py`、`plugins/knowledge.py`、`admin/routes/api/providers.py`、`admin/frontend/src/views/system/SystemView.vue`、`tests/test_call_api.py`、`tests/test_client.py`、`tests/test_config_loader.py`、`tests/test_usage.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_call_api.py tests/test_prompt.py tests/test_usage.py tests/test_client.py tests/test_config_loader.py tests/test_admin_api.py` 通过，`140 passed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 这轮只新增了 DeepSeek V4 原生路径，没有删除现有 `anthropic` 与 `openai` provider；兼容端点仍可继续使用。
- 运行时是否真正走 `native`，取决于对应 profile 的 `api_format=deepseek` 与 base URL；旧的 `api.deepseek.com/anthropic` 仍会在系统页显示为 `anthropic-compat`。

## 2026-05-07 Web 端“主人优先”精简改版

**变更类型**：feature / frontend / ux / docs

**内容**：

- 主侧栏收口为高频入口：
  - 保留 `仪表盘 / 人设编辑 / 群管理 / 记忆 / 表情包 / 群内黑话 / 知识库 / 配置 / 系统 / 日志`
  - 移出主导航：`日程心情 / 用量统计 / 好感度 / 调度器 / 插件 / 沙盒`
  - 隐藏页能力未删除，改由 `系统` 页中的“高级工具”入口进入
- 仪表盘改为唯一日常首页：
  - 合并原 `日程心情` 的主要职责
  - 增加待处理事项区，聚合黑话待审核、AI 待人工复核、NapCat 异常、重启建议和关键服务告警
  - 首屏收口为运行状态、下一段节奏、当前心情、待处理事项、关键日志和完整当日日程
- 配置页收口：
  - 常用模块默认直出，低频模块转入“高级设置”
  - JSON 兜底、变更预览、快照恢复、保存审计和保存说明默认折叠
- 系统页收口：
  - 首屏继续聚焦健康、连接、异常、资源和运维建议
  - Provider 深度管理、协议探测、备份与隐藏页面深链统一下沉到高级区
- 黑话页收口：
  - 首屏仅保留核心审核队列和筛选主流程
  - 热门排行、抽取运行记录、学习设置、漂移治理和观察中候选默认折叠
- 记忆体系调整：
  - `MemoryConsoleView` 默认进入浏览视图
  - 用户实体详情直接补充关系画像摘要，替代单独进入好感度页
  - `/affection` 路由改为兼容跳转到 `/memory?view=browse`
- 日志页调整：
  - 强化“实时流优先”视图
  - 历史文件列表降为次级入口，不再让用户先做来源选择

**影响范围**：`admin/frontend/src/layouts/components/SideMenu.vue`、`admin/frontend/src/router/index.ts`、`admin/frontend/src/views/dashboard/DashboardView.vue`、`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/system/SystemView.vue`、`admin/frontend/src/views/slang/SlangView.vue`、`admin/frontend/src/views/memory/*`、`admin/frontend/src/views/groups/GroupsView.vue`、`admin/frontend/src/views/logs/LogsView.vue`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- 前端构建前已自动执行 `bash ../../scripts/cleanup-appledouble.sh`

**交接说明**：

- 这轮没有删除低频页面功能，而是把它们从主人主流程中移走，并统一改为高级入口。
- 当前仓库的 `admin/frontend/src/*` 在 git 状态中表现为未跟踪文件；本次改动已实际写入工作区并完成构建验证，但后续若要提交，需要先确认该仓库的前端源码跟踪策略。

## 2026-05-07 三层架构审计报告改写为最新版并留档

**变更类型**：docs / audit

**内容**：

- 新增归档文档：`docs/audits/omubot-three-layer-architecture-audit-2026-05-07.md`
- 将旧版《Omubot 三层架构审计报告》按当前仓库真实状态重写为 2026-05-07 更新版
- 保留仍然成立的底层判断：
  - `kernel / services / plugins` 三层边界
  - `PluginBus` 中心地位
  - 8 个主钩子
  - 类继承式 `AmadeusPlugin`
- 修正已经过时的结论：
  - Provider 治理已升级为 profile / task profile / 热切换 / 后台编辑
  - Admin Web 已不再只是基础面板
  - 轻量语义检索、知识库、黑话治理、群 Profile、协议健康与插件治理已形成新能力层
- 明确仍未改变的短板：
  - 仍无 IoC、YAML Workflow、进程级插件隔离、重型向量 RAG、图谱记忆、标准迁移账本

**影响范围**：`docs/audits/omubot-three-layer-architecture-audit-2026-05-07.md`

**交接说明**：

- 后续如果再引用“三层架构审计”，应优先引用这份 2026-05-07 更新版，而不是沿用旧结论直接判断当前项目状态。

## 2026-05-07 Phase 2 Provider 多样性：profile 定义编辑器与细粒度管理收口

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- Providers API 扩展：
  - `admin/routes/api/providers.py` 新增 `POST /api/admin/providers/definitions`
  - 支持结构化保存 `llm.profiles`，并处理 `api_key_mode = keep / replace / clear`
  - 保存时会同步修正 `default_profile / task_profiles`，删除旧 profile 后自动把失效任务映射回退到当前默认 profile
  - `main` profile 会继续同步 legacy `llm.api_format / base_url / api_key / model / max_tokens` 根字段，保持旧配置兼容
- 运行时热生效：
  - Provider 定义保存后会立即刷新运行中的 `LLMClient` 任务 profile 映射，不需要额外重启
  - 原有 `POST /api/admin/providers/selection` 也改为统一走持久化后配置模型，减少运行态和落盘态漂移
- 系统页 Provider 面板收口：
  - `SystemView.vue` 在原有“默认 profile 热切换 + 连通性测试”基础上新增“定义管理”抽屉
  - 抽屉支持新增 / 删除 / 编辑 profile，配置 API 格式、Base URL、Model、Max Tokens、能力声明和 API Key 处理方式
  - 保存后自动刷新当前 Provider 面板，不再需要手动回到配置文件里改 JSON
- 文档与路线图同步：
  - `docs/wiki/Configuration.md` 新增 `LLM Profiles` 说明，解释 `llm.profiles / default_profile / task_profiles` 的关系
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 2 尾项标记为完成，并把剩余内容归类为真实运行反馈微调或 optional extra

**影响范围**：`admin/routes/api/providers.py`、`admin/frontend/src/views/system/SystemView.vue`、`tests/test_admin_api.py`、`docs/wiki/Configuration.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "provider or protocol"` 通过，8 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh` 已由前端构建前自动执行

**交接说明**：

- 现在系统页已经同时具备“任务映射热切换”和“profile 定义编辑”，本轮 Phase 2 的具体尾项已收口。
- 后续若还要继续做 Provider 相关工作，优先级应转向真实模型运营反馈，例如默认 profile 选择策略、文案优化和更细的可观测性，而不是再补基础编辑能力。

## 2026-05-07 Phase 6 群 Profile：工具矩阵、屏蔽用户编辑与策略审计历史

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- 群配置模型扩展：
  - `kernel/config.py` 为群配置新增 `allowed_tools / blocked_tools`
  - `ResolvedGroupConfig` 现在会解析群级工具 allow/block 结果，并继续保留 `tools_enabled / sticker_mode / slang_enabled` 的原有语义
- 运行时工具过滤接线：
  - `services/llm/client.py` 在原有“工具总开关 + 贴纸/黑话特殊过滤”之外，新增按群工具名单过滤
  - 当某群配置了允许名单时，只保留名单内工具；屏蔽名单始终优先
- Groups API 升级：
  - `admin/routes/api/groups.py` 新增 `GET /api/admin/groups/{group_id}/profile`
  - 返回当前群策略、工具目录和最近审计记录
  - 保存/恢复群策略时会写入 `storage/groups/group-profile-audit.json`
  - 审计记录包含动作类型、变更字段和 before/after 摘要
- Groups 页面重构收口：
  - `GroupsView.vue` 现在把群详情拆成基础配置、额外屏蔽用户、工具矩阵、实时状态、最近消息、策略审计历史
  - `blocked_users` 改为可视化标签编辑器，并明确区分“当前群额外屏蔽”和“全局屏蔽”
  - 工具矩阵按插件分组，支持 `继承 / 允许 / 屏蔽`
  - 策略历史支持直接回看最近变更
- 路线图同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 6 尾项标记为完成
  - 当前焦点切到 Phase 2 尾项：profile 定义编辑器与更细粒度 Provider 管理

**影响范围**：`kernel/config.py`、`services/llm/client.py`、`services/group_profile_audit.py`、`admin/routes/api/groups.py`、`admin/routes/api/__init__.py`、`admin/frontend/src/views/groups/GroupsView.vue`、`tests/test_config_loader.py`、`tests/test_client.py`、`tests/test_admin_api.py`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_admin_api.py tests/test_client.py -k "group"` 通过，16 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh` 已执行
- `find admin/static -name '._*' -print | wc -l` 结果为 `0`

**交接说明**：

- 这一步完成的是“群级工具治理”和“可回看审计”，不是全局 Provider 编辑器；下一步可以转去做 Phase 2 尾项。
- `blocked_users` 仍保持“全局 + 群级额外名单”的并集语义，没有改成可在群级反向移除全局屏蔽。

## 2026-05-07 Phase 3 插件治理：插件软隔离/限流收口

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- `PluginBus` 增加软隔离冷却：
  - `kernel/bus.py` 在高频 Hook 链路上新增轻量软隔离策略
  - 同一插件在短窗口内连续报错或连续超出 Hook budget 时，会进入短时冷却
  - 冷却期间会临时跳过 `on_message / on_pre_prompt / on_post_reply / on_thinker_decision / on_tick`
  - 不做进程级卸载，不改插件 ABI；目标是先降低异常插件对总线的连带拖累
- 插件健康快照细化：
  - 新增 `suppressed_calls`、`cooldown_reason`、`cooldown_remaining_seconds`、`error_burst_count`、`slow_burst_count`
  - 插件页与 API 可以直接看到“正在冷却”“最近被抑制的 Hook”“慢调用/异常爆发来源”
- 系统健康接线：
  - `services/health.py` 的 PluginBus 检查新增 `throttled_plugins` 与 `suppressed_calls`
  - 顶层阈值告警会把“已有插件进入软隔离”视为需要关注的运行事件
- 插件页治理状态补强：
  - `PluginsView.vue` 的健康标签与治理统计区新增冷却/抑制可视化
  - Hook 明细里新增每个 Hook 的抑制次数，方便区分“插件慢”还是“插件已被总线临时降载”
- 路线图同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 3 尾项标记为完成
  - 当前焦点切换到 Phase 6 尾项：群级工具矩阵、blocked users 编辑器和群策略审计历史

**影响范围**：`kernel/bus.py`、`services/health.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`tests/test_plugin_bus.py`、`tests/test_admin_api.py`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_plugin_bus.py tests/test_admin_api.py -k "plugin or services_health"` 通过，51 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `find admin/static -name '._*' -print | wc -l` 结果为 `0`

**交接说明**：

- 这一步做的是“软隔离”而不是热卸载/进程沙箱，优先目标是控制异常插件的爆发面和排障可见性。
- 如果后续真实运行中仍遇到单插件长期拖垮总线，再评估更重的进程级隔离，但当前默认路线仍保持轻量。

## 2026-05-07 Phase 1 稳定性地基：健康告警降噪与策略化门槛

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- 顶层健康告警阈值化：
  - `services/health.py` 为 `LLM / PluginBus / Runtime Errors / NapCat / Protocol Trace / SQLite / Memory / Slang` 增加顶部告警判定门槛
  - 顶层 `alerts` 不再机械镜像所有 warning/error，而是只保留达到阈值的高优先级异常
  - 新增 `policy` 摘要，说明当前采用 thresholded 模式，并统计被折叠的轻量提醒数量
- 维护窗口建议同步降噪：
  - `maintenance_window` 不再直接根据所有服务 warning/error 触发
  - 现在只根据阈值后的高优先级告警判断是否建议进入维护窗口或是否建议重启验证
- 系统页说明增强：
  - `SystemView.vue` 新增“折叠轻量提醒”提示和阈值说明文案
  - 顶部告警区更安静，但下方“服务级健康”仍保留完整 warning/error 细节，方便人工审查回退链路
- 文档与路线图同步：
  - `docs/project-info.md` 说明系统页采用“两层健康口径”
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 1 告警策略尾项标记为完成，并把焦点切到 Phase 3 插件限流/隔离策略

**影响范围**：`services/health.py`、`admin/frontend/src/views/system/SystemView.vue`、`tests/test_admin_api.py`、`docs/project-info.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "system"` 通过，6 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `find admin/static -name '._*' -print | wc -l` 结果为 `0`

**交接说明**：

- 这一步完成的是“顶层告警降噪”，不是隐藏细节；完整 warning/error 仍保留在服务级健康卡中。
- 下一步建议继续 Phase 3 尾项，补插件限流/隔离策略，减少异常插件对总线的连带影响。

## 2026-05-07 Phase 8 运维体验：维护窗口提示、健康告警与重启影响说明

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- 服务级健康聚合升级：
  - `services/health.py` 在原有 `services` 列表与 summary 之外，新增 `alerts` 与 `maintenance_window`
  - 告警会按服务状态自动生成高优先级摘要，并给出下一步处理动作
  - 维护窗口摘要会给出“是否建议进入维护窗口”、原因、处理顺序和重启建议
- 系统页收口：
  - `SystemView.vue` 新增“运维建议”区，统一展示维护窗口判断、当前健康告警和重启影响说明
  - 保留原有服务级健康、关键错误、资源、协议探测和备份区，不重做整体信息架构
- 全局重启提示同步：
  - `RestartBotButton.vue` 改为多段式确认文案，不再只提示“短暂中断”
  - 会明确说明连接中断、配置/插件/协议改动生效边界，以及 Docker 自动拉起/手工启动的注意点
- 路线图与说明同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 8 第三刀标记为完成
  - `docs/project-info.md` 同步系统页职责说明

**影响范围**：`services/health.py`、`admin/routes/api/system.py`、`admin/frontend/src/views/system/SystemView.vue`、`admin/frontend/src/components/common/RestartBotButton.vue`、`tests/test_admin_api.py`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`docs/project-info.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "system"` 通过，6 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 这一步已经把 Phase 8 的“维护窗口/告警/重启影响”补齐，但告警阈值仍偏静态。
- 下一步建议回到 Phase 1 尾项，继续做健康告警降噪、分级门槛和长期运行误报控制。

## 2026-05-07 Phase 8 运维体验：配置回滚向导与基础备份

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- 配置快照与恢复链路：
  - 新增 `services/config_backup.py`，用 `storage/config/config-backups.json` 管理最近可恢复快照元数据
  - 实际快照内容写入 `storage/config/backups/`，用于真实恢复，不在 Web 端直接暴露原始配置值
  - `admin/routes/api/config.py` 新增 `/api/admin/config/backups` 与 `/api/admin/config/restore`
  - 保存配置后会自动生成“保存快照”；执行恢复时会先生成“恢复前备份”，再写入“恢复结果”快照
- Admin 配置页：
  - `ConfigView.vue` 新增“可恢复配置快照”面板，展示时间、来源、模块命中、快照大小和恢复按钮
  - 恢复动作会先确认当前草稿、再确认覆盖当前 `config/config.json`，成功后同步刷新变更预览与最近审计
  - 页面只展示快照摘要，不会把 secret 明文渲染到前端
- 类型、测试与计划同步：
  - `admin/frontend/src/views/config/types.ts` 扩展 backup / restore 类型
  - `tests/test_admin_api.py` 新增配置快照保存、恢复与 secret 不泄露回归
  - `docs/wiki/Configuration.md` 与 `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 同步更新，Phase 8 第二刀收口

**影响范围**：`services/config_backup.py`、`admin/routes/api/config.py`、`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/config/types.ts`、`tests/test_admin_api.py`、`docs/wiki/Configuration.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "config"` 通过，4 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `find admin/static -name '._*' -print | wc -l` 结果为 `0`

**交接说明**：

- 现在配置页已经具备“预览 diff + 审计记录 + 可恢复快照”三段运维链路。
- 下一步可继续 Phase 8 第三刀，补维护窗口提示、健康告警和更清晰的重启影响说明。

## 2026-05-07 Phase 8 运维体验：配置变更审计与保存前 diff 预览

**变更类型**：feature / backend / frontend / docs / ops

**内容**：

- 配置预览与审计链路：
  - 新增 `services/config_audit.py`，以 `storage/config/config-audit.json` 保存最近配置落盘摘要
  - `admin/routes/api/config.py` 新增 `/api/admin/config/preview` 与 `/api/admin/config/history`
  - 保存前会基于服务端校验后的 `BotConfig` 规范化结构计算 diff；保存成功后写入审计记录
  - 审计记录只保留字段路径、变更类型和遮罩后的 before/after 展示，不把 secret 明文写进历史
- Admin 配置页：
  - `ConfigView.vue` 新增“查看变更”按钮
  - 新增“保存前变更预览”面板，展示新增/移除/修改统计、涉及模块和逐字段差异
  - 新增“最近保存审计”面板，回看最近几次配置落盘摘要
  - 保存操作现在会先走一次预览校验，再让用户确认写入条目数，减少误改直接落盘
- 类型与文档同步：
  - `admin/frontend/src/views/config/types.ts` 扩展 diff / audit 类型
  - `docs/wiki/Configuration.md` 增补配置页预览和审计说明
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 8 第一刀标记为已完成，并把下一焦点切到回滚向导与基础备份

**影响范围**：`services/config_audit.py`、`admin/routes/api/config.py`、`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/config/types.ts`、`tests/test_admin_api.py`、`docs/wiki/Configuration.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "config"` 通过，3 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 当前审计记录是轻量摘要，不等价于完整配置备份；下一步仍需补 Phase 8 的回滚向导和保存快照。
- Secret 字段在预览和审计里默认遮罩展示，但原始 `config/config.json` 仍按实际值正常落盘。

## 2026-05-07 Phase 7 本地插件生态：plugin.sig 签名与来源校验预留

**变更类型**：feature / backend / frontend / docs / governance

**内容**：

- 本地插件 detached attestation 预留：
  - `services/plugin_index.py` 新增可选 `plugin.sig` / `xxx.sig` 识别
  - 当前支持轻量 JSON 校验结构：`scheme=sha256`、`entry_sha256`、`manifest_sha256`、`signer`、`key_id`、`signed_at`、`source.origin`、`source.entry_path`
  - 索引会校验入口文件、`plugin.json` 指纹以及来源声明是否和当前本地路径一致
- 插件索引与治理增强：
  - 每个本地插件包新增 `signature_status`、`source_attestation_status`、`signature_signer`、`relative_signature` 等字段
  - `summary` 新增 `signature_verified_count`、`signature_issue_count`、`unsigned_external_count`
  - 签名或来源声明异常时，已加载插件会标记为 `attention`，未加载插件会直接进入 `blocked`
- Admin 插件页：
  - 本地插件索引横条新增“已校验 / 签名问题”计数
  - 治理队列与插件卡片支持显示签名状态
  - 插件详情“本地包索引”区新增签名路径、签名方案、签名人、来源声明、声明入口、签名时间等信息
- 文档同步：
  - `docs/wiki/Plugins.md` 补充 `plugin.sig` 和索引校验说明
  - `docs/architecture.md` 补充目录插件中的 `plugin.sig` 预留说明
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 7 标记为“基本收口”，并把下一焦点切到 Phase 8 运维体验

**影响范围**：`services/plugin_index.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`tests/test_admin_api.py`、`docs/wiki/Plugins.md`、`docs/architecture.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "plugin"` 通过，4 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 这不是远程安装或自动执行机制，只是本地插件包的轻量签名/来源校验预留。
- 当前默认仍坚持 `local_only` 策略；后续若继续增强签名格式，也必须保持“不从 Web 直接下载并执行未知代码”的边界。

## 2026-05-07 Phase 7 本地插件生态：未加载包治理队列

**变更类型**：feature / backend / frontend / docs / governance

**内容**：

- 本地插件索引治理语义补强：
  - `services/plugin_index.py` 为每个本地插件包新增 `governance_status / governance_label / action_hint`
  - 状态固定为 `healthy / attention / ready / review / blocked`
  - `summary` 新增 `not_loaded_count / ready_to_load_count / review_required_count / blocked_count / attention_count`
  - 已加载但缺清单、来源待确认或版本不兼容的插件不再只给 warning，而是明确标记为“需关注”
  - 未加载插件会区分“可接入运行时”“来源待确认”“已阻塞”，减少目录里藏问题的情况
- Admin 插件页治理队列：
  - `/admin/plugins` 顶部本地插件索引横条补充未加载、阻塞、待确认计数
  - 新增“本地包治理队列”卡片，集中展示未加载、来源待确认、版本不兼容以及已加载但仍需治理的插件包
  - 每个条目显示形态、入口路径、来源状态、清单状态、兼容状态和行动建议；已加载项可直接跳转到插件详情继续治理
- 文档与路线图同步：
  - `docs/wiki/Plugins.md` 增补本地插件索引、治理状态和 `GET /api/admin/plugins/index` 的说明
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 7 的“未加载本地包治理”和“兼容告警优化”标记为已完成，并把下一焦点切到签名/来源校验预留

**影响范围**：`services/plugin_index.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`tests/test_admin_api.py`、`docs/wiki/Plugins.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "plugin"` 通过，4 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 本轮仍坚持 Phase 7 的安全边界：只识别本地插件包，不开放 Web 端远程下载安装与执行未知代码。
- 当前已具备“发现本地包存在但未加载”与“给出治理建议”的能力；真正的签名格式与来源校验策略仍留在下一刀。

## 2026-05-07 Phase 5 轻量语义增强：质量守卫与系统可视化收口

**变更类型**：feature / backend / frontend / quality / observability

**内容**：

- 黑话质量守卫收口：
  - 新增 `services/slang/quality.py`，统一沉淀噪声 term、泛化释义、alias 清洗等轻量质量判断
  - `SlangExtractor` 改为复用共享质量守卫，继续过滤低信号候选，并额外挡掉“一个梗 / 一种说法”这类无效释义
  - `SlangDailyReviewer` 在 AI 复核写库前也走同一套判断；如果 AI 把释义改坏，会自动回退到 extractor 原始释义，避免把泛化结果写进 approved 词条
- 记忆语义指标可视化：
  - `services/health.py` 的 `Memory` 服务项补充 `queries / hits / hit_rate / fallbacks / errors / last_error`
  - Admin 系统页 `Memory` 服务卡新增紧凑指标标签，直接展示 semantic backend、命中数、回退数与最近错误
- 文档与计划同步：
  - `docs/wiki/Semantic-Retrieval.md` 更新系统页可视指标说明
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 5 调整为“基本收口”，并把当前焦点切到 Phase 7 本地插件生态起步

**影响范围**：`services/slang/quality.py`、`services/slang/extractor.py`、`services/slang/daily_reviewer.py`、`services/health.py`、`admin/frontend/src/views/system/SystemView.vue`、`tests/test_slang_plugin.py`、`tests/test_admin_api.py`、`docs/wiki/Semantic-Retrieval.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_plugin.py tests/test_admin_api.py tests/test_retrieval.py tests/test_client.py tests/test_config_loader.py tests/test_similarity.py` 通过，136 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 本轮没有引入 embedding、FAISS 或新重依赖；`embedding` 仍保持 optional extra 安全 stub。
- Phase 5 现已完成默认轻量路线的主要能力，后续只剩可选 embedding 真正实现，不作为默认栈阻塞项。
- 下一步按路线图进入 Phase 7：本地插件包索引、来源校验与兼容版本检查。

## 2026-05-07 Phase 4 协议韧性：mock 协议测试

**变更类型**：test / protocol / observability

**内容**：

- 补强协议 mock 回归测试：
  - 无 Bot 场景：`/api/admin/protocol/health` 与 `/protocol/probe` 稳定返回 disconnected / failed，不抛异常
  - 缺方法场景：Bot 只有 `get_login_info` 时，`group_list` capability 标为 failed，并记录 `method_missing`
  - 协议失败场景：`get_login_info` 与 `get_group_list` 抛错时，probe 保持 200 响应，连接历史记录 `protocol_probe` 错误事件
  - trace 场景：`ProtocolTraceStore` 记录成功/失败调用、最小容量钳制、失败摘要与敏感参数脱敏
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 4 mock 协议测试标记为已完成
  - 下一步 Now 队列切换到 Phase 6 群 Profile

**影响范围**：`tests/test_admin_api.py`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check tests/test_admin_api.py admin/routes/api/protocol.py services/protocol_trace.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "protocol_mock or protocol_trace_mock or protocol_trace_store or provider_and_protocol"` 通过，5 passed
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，142 passed
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 本轮只补协议契约测试，未改生产协议路由代码；当前实现已满足 mock 失败降级契约。
- Phase 4 已基本收口，后续只在实际协议端差异出现时补兼容项。
- 下一步按计划进入 Phase 6 群 Profile，建立每群工具/风格/主动插话/表情/黑话策略配置。

---

## 2026-05-07 Phase 2 Provider 深化：profile 热切换

**变更类型**：feature / backend / frontend / runtime-config

**内容**：

- Provider 选择 API：
  - 新增 `POST /api/admin/providers/selection`
  - 支持保存默认 profile 与 `main / thinker / compact / slang / vision` 任务映射
  - 请求会校验 profile 是否存在，避免写入无效映射
  - 保存时只补丁式更新 `llm.default_profile` 与 `llm.task_profiles`，并写入 JSON 配置
- 运行时热切换：
  - `LLMClient` 新增 `set_task_profiles()`
  - 保存成功后立即更新运行中的任务 profile，不需要重建 aiohttp session，也不清空会话
  - `main` 任务跟随默认 profile，并同步更新 LLMClient 的主模型连接参数
- Admin 系统页：
  - LLM Provider 卡新增默认 profile 选择器和“应用热切换”按钮
  - 任务 profile 从只读小卡升级为紧凑选择器
  - 显示“运行中 / 待应用”状态，保存后刷新 Provider 概览和限流状态
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 2 profile 热切换标记为已完成
  - 下一步 Now 队列切换到 Phase 4 mock 协议测试

**影响范围**：`admin/routes/api/providers.py`、`admin/routes/api/__init__.py`、`services/llm/client.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check admin/routes/api/providers.py admin/routes/api/__init__.py services/llm/client.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "provider_selection or provider_and_protocol"` 通过，2 passed
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，139 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 本轮只做 profile 选择和任务映射热切换，不做 profile 定义编辑器；新增/修改 base_url、api_key、model 仍建议走配置页。
- 保存会写入 `config/config.json`；如果当前只存在 legacy TOML，会生成 JSON 主配置，不删除 TOML。
- 下一步按计划补 Phase 4 mock 协议测试，确保协议健康、probe、trace 和兼容清单契约可回归。

---

## 2026-05-07 Phase 1 稳定性补强：关键错误聚合

**变更类型**：feature / backend / frontend / observability

**内容**：

- 新增运行期关键错误聚合：
  - `RuntimeErrorStore` 在内存中滚动记录 `WARNING / ERROR / CRITICAL`
  - 按 level、channel、message 生成 signature，聚合同类问题的次数、首次出现和最近出现时间
  - loguru SSE sink 在推送实时日志的同时写入错误聚合，且增加重复安装保护
- Admin API / 服务健康：
  - 新增 `/api/admin/system/errors`
  - `/api/admin/services/health` 新增 `Runtime Errors` 服务项
  - 系统健康能区分无错误、warning、error/critical 等状态
- Admin 系统页：
  - 新增“关键错误”面板
  - 展示 error/warning 数、唯一问题数、滚动容量和最近错误分组
  - 无关键错误时显示紧凑空状态，保留日志页作为深度排查入口
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 1 关键错误聚合标记为已完成
  - 下一步 Now 队列切换到 Phase 2 profile 热切换

**影响范围**：`services/errors.py`、`admin/routes/api/events.py`、`admin/routes/api/system.py`、`services/health.py`、`kernel/types.py`、`bot.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/errors.py admin/routes/api/events.py admin/routes/api/system.py services/health.py kernel/types.py bot.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "system_runtime_errors or system_services_health"` 通过，2 passed
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，138 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 关键错误聚合是内存滚动诊断，不持久化到数据库；长期追溯仍以普通日志文件为准。
- loguru sink 现在也会接收未打 channel 的 warning/error，用于关键错误面板和 SSE 摘要；普通 DEBUG/INFO 仍只推送带 channel 的运行日志。
- 下一步按计划进入 Phase 2 profile 热切换，需要评估保存结构化配置后是热重载还是提示硬重启。

---

## 2026-05-07 Phase 2 Provider 深化：分 profile rate limit

**变更类型**：feature / backend / frontend / observability

**内容**：

- LLMClient 新增 profile 维度限流状态：
  - 每个 resolved profile 独立记录调用数、成功数、失败数、限流数、快失败数、最近任务、最近错误、最近成功时间、最近限流时间和冷却剩余时间
  - 某个 profile 被 429 后只给该 profile 设置冷却，不污染其他 profile
  - 冷却期内同 profile 请求快失败，避免低优先级任务长时间占用流程
  - thinker/slang 等辅助任务沿用原有降级逻辑；main profile 仍由现有私聊/群聊外层重试兜底
- ChatPlugin：
  - 启动时把 `task -> profile name` 映射传给 LLMClient，用于诊断和限流隔离
- Admin API / Web：
  - `/api/admin/providers` 返回 `rate_limits`
  - Provider profile 行新增限流状态 tag，可见 ready、冷却和历史限流次数
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 2 分 profile rate limit 标记为已完成
  - 下一步 Now 队列切换到 Phase 1 关键错误聚合

**影响范围**：`services/llm/client.py`、`plugins/chat.py`、`admin/routes/api/providers.py`、`admin/routes/api/__init__.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_client.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/llm/client.py plugins/chat.py admin/routes/api/providers.py admin/routes/api/__init__.py tests/test_client.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，137 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 本轮不改配置文件语义，不新增 rate limit 配置项；策略使用现有 `RATE_LIMIT_BASE_DELAY` 与最大 60 秒冷却。
- profile 热切换仍未实现，下一步按计划先做 Phase 1 关键错误聚合。

---

## 2026-05-07 Phase 4 协议韧性：历史连接记录

**变更类型**：feature / backend / frontend / observability

**内容**：

- 新增协议连接历史：
  - `ProtocolConnectionHistory` 记录当前状态、连接 Bot 数、`self_id`、最近变化时间、最近确认时间、断连起点、恢复耗时和最近错误
  - `on_bot_connect` 自动记录连接恢复
  - 如果当前 NoneBot Driver 支持 `on_bot_disconnect`，断开时自动记录断连事件
  - `/api/admin/protocol/health` 与服务健康聚合会做安全快照校准，避免只依赖生命周期钩子
- Admin API：
  - 新增 `/api/admin/protocol/connections`
  - `/api/admin/protocol/health` 和 `/api/admin/protocol/probe` 返回 `connection` 摘要
  - 协议探测中登录信息/群列表失败会记录为连接历史错误事件
- Admin 系统页：
  - 协议卡新增“连接历史”面板
  - 展示连接/断开状态、Bot 数、最近变化、最近确认、上次恢复耗时、最近错误和最近事件列表
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 4 历史连接记录标记为已完成
  - 下一步 Now 队列切换到 Phase 2 分 profile rate limit

**影响范围**：`services/protocol_trace.py`、`kernel/types.py`、`kernel/router.py`、`bot.py`、`admin/routes/api/protocol.py`、`services/health.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/protocol_trace.py services/health.py kernel/types.py kernel/router.py bot.py admin/routes/api/protocol.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，136 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 连接历史是内存滚动记录，用于运行期诊断；Bot 进程重启后历史会清空，但维护日志和普通日志仍保留长期信息。
- 本轮不做自动重连、不做协议切换、不替换 NapCat。

---

## 2026-05-07 Phase 2 Provider 测试 + Phase 4 协议兼容清单

**变更类型**：feature / backend / frontend / observability

**内容**：

- Provider 连通性诊断：
  - 新增 `POST /api/admin/providers/{name}/test`
  - 系统页 LLM Provider 卡支持逐个 profile 手动测试
  - 测试请求为显式点击触发，不在页面加载时自动调用外部模型
  - 结果显示耗时、成功/失败和短文本预览/错误摘要
- 协议韧性补强：
  - `/api/admin/protocol/health` 与 `/api/admin/protocol/probe` 返回 NapCat / LLOneBot 兼容清单
  - 新增 `/api/admin/protocol/compatibility`
  - 系统页协议卡新增只读兼容检查表，区分支持、兼容、条件支持、手动确认和未探测
  - 继续保持安全探测策略，不主动发群消息、不测试禁言/踢人/戳一戳等会污染群聊的动作

**影响范围**：`admin/routes/api/providers.py`、`admin/routes/api/protocol.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check admin/routes/api/providers.py admin/routes/api/protocol.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，135 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- Provider 测试按钮用于维护窗口内手动确认 profile 可用性；后续 Phase 2 仍可继续做 profile 热切换与分 profile rate limit。
- 协议兼容清单是排查指引，不代表自动切换到 LLOneBot；NapCat 仍是默认协议端。

---

## 2026-05-07 Phase 1 请求追踪 + Phase 2 分任务 Provider Profile

**变更类型**：feature / backend / frontend / observability

**内容**：

- Phase 1：OneBot 请求 echo/追踪
  - 新增 `services/protocol_trace.py`
  - Bot 连接后自动包装 `bot.call_api`
  - 每次 OneBot API 调用生成本地 `ob_*` 追踪号，记录 action、耗时、成功/失败、错误摘要和脱敏参数
  - 新增 `/api/admin/protocol/traces`
  - `/api/admin/protocol/health` 返回 `trace_summary`
  - 服务健康聚合新增 `Protocol Trace` 服务项
  - 系统页协议卡新增“请求 Echo 追踪”摘要与最近请求列表
- Phase 2：Provider 分任务 profile
  - `LLMConfig` 新增 `task_profiles`
  - 新增 `profile_name_for_task()` 与 `resolve_task_profile()`
  - 默认支持 `main / thinker / compact / slang / vision` 任务映射
  - `ChatPlugin` 启动时把任务 profile 传入 `LLMClient`
  - `LLMClient` 新增 `_call_thinker / _call_compact / _call_slang`
  - thinker 决策、上下文 compact、黑话抽取分别走对应任务 profile
  - `/api/admin/providers` 返回任务到 profile 的映射，系统页 LLM Provider 卡展示任务矩阵

**影响范围**：`services/protocol_trace.py`、`services/health.py`、`kernel/config.py`、`kernel/types.py`、`kernel/router.py`、`bot.py`、`plugins/chat.py`、`services/llm/client.py`、`services/slang/extractor.py`、`admin/routes/api/protocol.py`、`admin/routes/api/providers.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_config_loader.py`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/protocol_trace.py services/health.py kernel/types.py kernel/router.py kernel/config.py bot.py plugins/chat.py services/llm/client.py services/slang/extractor.py admin/routes/api/protocol.py admin/routes/api/providers.py tests/test_admin_api.py tests/test_config_loader.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_admin_api.py tests/test_plugin_bus.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py` 通过，135 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 追踪号是 Omubot 本地 echo，不修改 OneBot 协议 payload；用于排查 API 调用链路，不影响协议端兼容性。
- `vision` 任务映射已在配置/API 中预留，但当前视觉仍使用既有 Qwen VL 配置，不强行切到聊天 LLM profile。
- Phase 2 后续可继续做 profile 测试按钮、热切换和分 profile rate limit 策略。

---

## 2026-05-07 Phase 1 稳定性补强：服务级健康聚合

**变更类型**：feature / backend / frontend / observability

**内容**：

- 新增 `services/health.py`：
  - 聚合 LLM、PluginBus、NapCat、SQLite、Memory、Slang 六类服务状态
  - SQLite 使用只读式 `quick_check` 思路检查 `messages.db / memory_cards.db / slang.db`
  - Memory 汇总记忆卡片、消息日志、短期会话可用性
  - Slang 汇总候选、已批准、观察中数量；未初始化时给出 warning 而不阻塞页面
  - PluginBus 汇总启用数、异常数、慢调用数和权限拦截数
- Admin API：
  - `/api/admin/services/health` 返回统一服务健康快照
  - `create_system_router()` 接收 `config`，用于 LLM 与 NapCat 配置判定
- Admin 系统页：
  - 新增“服务级健康”面板
  - 展示整体状态、需关注数量，以及每个服务的状态、指标和诊断说明
  - 保留原资源、Provider、协议探测与备份卡片结构，不重做系统页信息架构

**影响范围**：`services/health.py`、`admin/routes/api/system.py`、`admin/routes/api/__init__.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/health.py admin/routes/api/system.py admin/routes/api/__init__.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_plugin_bus.py` 通过，66 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 本轮只做安全只读健康聚合，不做主动发消息、协议破坏性探测或远程模型连通性测试。
- Phase 1 后续还剩请求 echo/追踪与关键错误聚合，可继续接在协议韧性与日志聚合上。

---

## 2026-05-07 插件治理 Phase 3 继续收口：配置持久化与 Hook 预算

**变更类型**：feature / architecture / frontend

**内容**：

- 新增插件私有配置持久化：
  - 新增 `services/plugin_config.py`
  - Bot 启动时创建 `storage/plugins/plugin-config.json` 对应的 `PluginConfigStore`
  - `PluginContext` 暴露 `plugin_config_store`，供插件后续按需读取 Admin 配置
- Admin API 扩展：
  - `/api/admin/plugins/{name}` 返回 `settings`，包含 schema、保存值、合并默认值后的 effective values、保存路径和更新时间
  - 新增 `GET /api/admin/plugins/{name}/settings`
  - 新增 `POST /api/admin/plugins/{name}/settings`
- Admin 插件页：
  - 插件详情抽屉的 `settings_schema` 从只读 JSON 升级为结构化编辑区
  - 支持开关、文本、数字、枚举、字符串列表和 JSON 兜底字段
  - 支持未保存提示、撤销和保存配置
- Hook 耗时预算：
  - `AmadeusPlugin` 新增 `hook_budget_ms`
  - `PluginBus` 支持从 class / manifest 读取预算
  - 超预算 Hook 记录 `slow_calls`、`last_slow_hook` 和 per-hook 慢调用统计，Admin 插件页可见

**影响范围**：`services/plugin_config.py`、`kernel/types.py`、`kernel/bus.py`、`bot.py`、`admin/__init__.py`、`admin/routes/api/__init__.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_plugin_bus.py`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/plugin_config.py kernel/types.py kernel/bus.py bot.py admin/__init__.py admin/routes/api/__init__.py admin/routes/api/plugins.py tests/test_admin_api.py tests/test_plugin_bus.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_plugin_bus.py tests/test_admin_api.py` 通过，65 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 已保存的插件配置不会自动热重载所有插件内部状态；插件需要在启动或自己的控制逻辑中读取 `ctx.plugin_config_store.get(plugin.name)`。
- Phase 3 还剩“插件限流/隔离策略”作为后续增强；按路线表下一步更建议转入 Phase 1 服务级健康聚合。

---

## 2026-05-07 生态路线图固化与插件治理 Phase 3 收口

**变更类型**：feature / architecture / frontend / docs

**内容**：

- 新增阶段计划表：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`
  - 按 Phase 1-8 记录已完成、进行中、待做和后置阶段
  - 将 Phase 3 拆为启停状态持久化、权限门禁、配置 schema 展示、插件配置保存、hook 预算和限流/隔离
- 插件启停状态持久化：
  - 新增 `services/plugin_state.py`
  - Admin 切换插件启停后写入 `storage/plugins/plugin-state.json`
  - Bot 启动时先回放持久状态，再应用 `kernel.disabled_plugins` 静态禁用兜底
- 插件权限门禁：
  - `PluginBus` 对 manifest v2 `permissions` 做兼容式门禁
  - 旧插件未声明 permissions 时继续放行
  - 显式声明 permissions 的插件只允许对应 `message / prompt / reply / tick / tool / command / admin` 能力
  - 健康快照新增 `permission_denials`
- Admin API / Web：
  - `/api/admin/plugins/state` 返回持久化状态文件视图
  - `/api/admin/plugins/{name}/state` 切换状态时同步持久化
  - 插件详情抽屉展示持久状态、权限拦截次数和 `settings_schema`
  - 插件工具/命令列表遵守 manifest v2 权限声明

**影响范围**：`docs/superpowers/plans/*`、`services/plugin_state.py`、`kernel/bus.py`、`bot.py`、`admin/__init__.py`、`admin/routes/api/__init__.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`admin/static`、`tests/test_plugin_bus.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check ...` 本轮相关文件通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_plugin_bus.py tests/test_call_api.py tests/test_client.py tests/test_admin_api.py tests/test_similarity.py` 通过，130 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh` 构建后清理 99 个 AppleDouble 文件，`admin/static` 已无 `._*`

**交接说明**：

- Phase 3 仍剩“插件配置保存”和“hook 预算/限流策略”未做；下一步建议转入 Phase 1 服务级健康聚合，或继续把插件配置 schema 做成可编辑控件。
- 运行时启停不强制卸载插件内部已启动的后台任务；这类插件仍需要硬重启收口。

---

## 2026-05-07 Omubot 生态借鉴式扩展地基落地

**变更类型**：feature / architecture / backend / frontend

**内容**：

- 稳定性地基：
  - 新增 `services/storage/sqlite.py`，统一 SQLite 连接 PRAGMA：WAL、NORMAL synchronous、foreign_keys、busy_timeout
  - `SlangStore`、`CardStore`、`MessageLog` 接入共享 SQLite helper，降低长期运行时写入锁和连接策略不一致风险
- Provider 多样性：
  - `LLMConfig` 新增 `api_format`、`default_profile`、`profiles`
  - 新增 `LLMProfile` / `LLMCapability`，旧 `llm.base_url/api_key/model/max_tokens` 自动映射为 `main`
  - `LLMClient.call_api` 改为走 `services/llm/provider.py` provider registry，支持 Anthropic 与 OpenAI SSE profile
  - `ChatPlugin` 按 `config.llm.default_profile` 初始化主 LLM client
- 插件治理：
  - `AmadeusPlugin` manifest v2 元数据扩展：`category`、`permissions`、`settings_schema`、`capabilities`、`min_omubot_version`
  - `PluginBus` 新增运行时启停、hook 调用/耗时/异常健康快照
  - `ToolRegistry` 增加 `clear()`，插件启停后可刷新工具注册表
  - Admin API 新增 `/api/admin/plugins/health` 与 `/api/admin/plugins/{name}/state`
- 协议与 Provider 可观测：
  - Admin API 新增 `/api/admin/providers`
  - Admin API 新增 `/api/admin/protocol/health` 与 `/api/admin/protocol/probe`
  - 协议探测仅做安全只读能力检查，不发送消息、不执行群管理动作
- 轻量语义增强：
  - 新增 `services/similarity.py`，提供默认 ngram similarity 与 embedding 安全 stub
  - 黑话 store 的 normalize/ngram 相似度改为复用统一 provider
  - `BotConfig.memory.semantic` 预留默认关闭的语义增强配置
- Admin Web：
  - 系统页新增 LLM Provider 与协议能力概览/探测卡片
  - 插件页显示 category、permissions、health、hook 统计，并支持运行时启停

**影响范围**：`kernel/config.py`、`kernel/bus.py`、`kernel/types.py`、`services/llm/*`、`services/storage/*`、`services/similarity.py`、`services/slang/store.py`、`services/memory/*`、`admin/routes/api/*`、`admin/frontend/src/views/system/SystemView.vue`、`admin/frontend/src/views/plugins/PluginsView.vue`、`admin/static`

**验证**：

- `python -m py_compile ...` 核心后端文件通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check ...` 本轮相关后端/测试文件通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_plugin_bus.py tests/test_call_api.py tests/test_client.py tests/test_admin_api.py tests/test_similarity.py` 通过，129 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 插件启停目前是运行时治理入口；`kernel.disabled_plugins` 仍用于启动时禁用。
- Provider profiles 默认不改变旧配置行为；需要多模型时在 `llm.profiles` 中显式增加 profile。
- embedding/FAISS 仍未引入，`memory.semantic.enabled=false` 与 `backend="ngram"` 是默认轻量路径。

---

## 2026-05-07 项目 Wiki 同步黑话 v3 与配置/部署口径

**变更类型**：docs / wiki

**内容**：

- 新增 `docs/wiki/Slang.md`：
  - 记录群内黑话的服务层/插件层/Admin Web 分层
  - 补充状态生命周期、SQLite 表、每日 AI 复核、v3 修订历史、语义漂移、`slang_lookup` 工具、API 与关键设置
  - 明确 v3 默认保持轻依赖，embedding/FAISS 放在 v3.5 可选增强
- 更新 Wiki 入口和旧口径：
  - `_Sidebar.md` 增加“群内黑话”
  - `Home.md` 更新核心特性、插件数量、Admin 面板能力和版本号
  - `Architecture.md` 增加 `services/slang`、`SlangPlugin` 消息路径、动态 Prompt 与工具查询说明
  - `Plugins.md` 更新 19 个插件列表，并补充常见工具与 `slang_lookup`
  - `Configuration.md` 同步 `config/config.json` 主配置、TOML 兼容读取、Admin 配置页保存口径和黑话设置
  - `Deployment.md` 同步 `--no-deps bot` 重建规则、NapCat WS 端口、`storage/slang.db`
  - `Stickers.md` 同步 JSON 主配置路径

**影响范围**：`docs/wiki/*` 与后续项目说明文档阅读口径

**验证**：

- `rg` 检查 Wiki 中已无旧插件数量、旧 WS 端口、旧版本号等主要过期口径
- `bash scripts/cleanup-appledouble.sh` 清理文档编辑产生的 AppleDouble 文件

---

## 2026-05-07 bot 定向重建部署黑话 v3

**变更类型**：deployment

**内容**：

- 按用户要求定向重建并替换 `bot` 服务：
  - 使用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot`
  - `qq-bot` 已重新创建并启动
  - `napcat` 未重建、未重启，保持原运行实例
- 容器内确认已包含黑话 v3 前端资源：
  - `admin/static/assets/SlangView-BEjC26cy.js`
  - `admin/static/assets/SlangView-FgExca7P.css`
  - `admin/static/assets/index-CZtMB5rv.js`

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 新实例 Up，`napcat` 仍为原实例 Up 22 hours
- `docker logs --tail 120 qq-bot` 显示服务启动完成、OneBot 已连接、`Bot 就绪`
- `docker exec qq-bot find admin/static -name '._*'` 未发现静态目录 AppleDouble 文件
- `GET /admin/slang` 返回 SPA HTML，入口指向 `assets/index-CZtMB5rv.js?v=1778110341`
- `bash scripts/cleanup-appledouble.sh` 清理日志写入后产生的 AppleDouble 文件

---

## 2026-05-07 黑话系统 v3 质量治理与工具化查询

**变更类型**：feature / backend / frontend

**内容**：

- 黑话服务层新增 v3 质量治理能力：
  - `slang_term_revisions` 记录词条创建、编辑、AI 通过、人工复核、合并和漂移治理的前后快照
  - `slang_drift_reviews` 承接已批准词条的冲突新释义，处理前不覆盖主词条、不进入 Prompt
  - `SlangSettings` 增加漂移检测、漂移最低置信度、查询工具、注入最低置信度和 `semantic_backend` 预留项
- Admin API 新增：
  - `GET /api/admin/slang/terms/{id}/revisions`
  - `GET /api/admin/slang/drift`
  - `POST /api/admin/slang/drift/{id}/accept|reject|alias|mute`
- `plugins/slang` 新增 `slang_lookup` 工具：
  - 只查询当前群与全局的已批准词条
  - 无群上下文时只返回全局词条
  - 工具关闭时执行会返回已关闭提示
- `/admin/slang` 增加“语义漂移”治理队列、质量治理侧栏、修订记录/证据链详情区和 v3 结构化设置。
- v3 仍保持轻依赖策略；未引入 embedding、FAISS、BM25、jieba、numpy。

**影响范围**：`services/slang`、`plugins/slang`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`tests/test_slang_*`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过，19 passed
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh` 构建后追加清理 98 个 AppleDouble 文件；测试/日志写入后复查再清理 3 个和 1 个

**交接说明**：

- 运行中容器若需要立即使用 v3 前端与 API，需要后续定向重建 `bot`；不要触碰 `napcat`。
- v3.5 的 embedding/FAISS 仍只是 `semantic_backend` 预留，默认 Docker 不安装也不加载。

---

## 2026-05-07 Phase 5 轻量语义增强首轮收口

**变更类型**：feature / backend / docs

**内容**：

- `RetrievalGate` 正式接入 `SimilarityProvider`：
  - 保留原有“全量 / 周期刷新 / 关键词匹配 / 最小提示”四层 gate
  - 当关键词未命中且 `memory.semantic.enabled=true` 时，追加“轻量语义匹配”兜底
  - 支持 `memory.semantic.backend = ngram | embedding`
- 语义后端安全降级：
  - 默认 `ngram` 后端可直接使用，无额外依赖
  - 若配置 `embedding` 但未安装实现，运行时会自动回退到 `ngram`
  - 记录 queries / hits / fallbacks / errors / last_error，避免静默失败
- 系统健康补充：
  - `services/health.py` 的 `Memory` 服务项新增语义检索状态摘要
  - 能看出是否启用、当前生效后端以及是否发生降级
- 黑话质量增强补第一刀：
  - `SlangExtractor` 过滤“释义基本等于原词”的低信号候选
  - 减少 LLM 输出空泛定义时污染候选池
- Wiki 补充 optional extra 口径：
  - `docs/wiki/Configuration.md` 增加 `memory.semantic` 配置说明
  - 新增 `docs/wiki/Semantic-Retrieval.md`
  - `_Sidebar.md` 加入“轻量语义检索”入口

**影响范围**：`services/memory/retrieval.py`、`services/health.py`、`plugins/chat.py`、`services/slang/extractor.py`、`tests/test_retrieval.py`、`tests/test_slang_plugin.py`、`docs/wiki/Configuration.md`、`docs/wiki/Semantic-Retrieval.md`、`docs/wiki/_Sidebar.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_retrieval.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_config_loader.py` 通过，88 passed

**交接说明**：

- 这轮完成的是 Phase 5 的第一段主链，重点是“让语义增强真正进入记忆检索运行路径，并且可降级、可观察”。
- Phase 5 仍未完全结束；后续继续收口黑话常识对比增强和更细的 semantic metrics Web 可视化。

---

## 2026-05-07 Phase 6 群 Profile 落地

**变更类型**：feature / backend / frontend

**内容**：

- 为 `group` 配置链路新增每群 Profile 字段：
  - `reply_style`
  - `custom_prompt`
  - `tools_enabled`
  - `sticker_mode`
  - `slang_enabled`
- 新增群 Profile 持久化接口：
  - `POST /api/admin/groups/{group_id}/profile`
  - `DELETE /api/admin/groups/{group_id}/profile`
  - 保存目标统一写入 `config/config.json`，兼容从 legacy TOML 读取
  - 与全局默认相同的值会自动回退为继承，避免把群覆盖配置写死
- 运行时立即生效：
  - `LLMClient` 读取每群 Profile，向 prompt 注入群聊回复偏好，并按群过滤工具
  - `StickerPlugin` 按群贴纸策略决定是否注入贴纸规则
  - `SlangPlugin` 按群黑话开关决定是否学习、抽取、每日 AI 复核和注入
- `/admin/groups` 抽屉升级为模块化群策略编辑台：
  - 保留群列表、实时状态、最近消息
  - 新增每群风格、主动节奏、工具、贴纸、黑话、附加提示词的结构化控件
  - 支持“恢复全局默认”“重置草稿”“保存群策略”

**影响范围**：`kernel/config.py`、`admin/routes/api/groups.py`、`admin/routes/api/__init__.py`、`services/llm/client.py`、`plugins/chat.py`、`plugins/sticker.py`、`plugins/slang/plugin.py`、`services/slang/daily_reviewer.py`、`admin/frontend/src/views/groups/GroupsView.vue`、`tests/test_config_loader.py`、`tests/test_admin_api.py`、`tests/test_client.py`、`tests/test_slang_plugin.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_admin_api.py tests/test_client.py tests/test_slang_plugin.py` 通过，104 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 输出 `0`

**交接说明**：

- 这轮完成的是群 Profile 第一版闭环，已经覆盖“保存配置 -> 立即生效 -> Web 编辑”主路径。
- 更细粒度的工具权限矩阵、`blocked_users` 编辑器和群策略审计历史仍留在后续阶段。

---

## 2026-05-07 Vite dev /admin/slang 刷新白屏修复

**变更类型**：fix / frontend-dev

**内容**：

- 修复 `http://localhost:5173/admin/slang` 刷新白屏且未回仪表盘的问题：
  - 原因是 `admin/frontend/vite.config.ts` 的 `SPA_ROUTES` 漏掉 `/admin/slang`
  - Vite dev server 刷新该路径时没有返回开发模式 SPA 入口，客户端路由守卫无法执行
- 同步修复带 query 页面刷新匹配问题：
  - dev proxy bypass 从完整 `req.url` 改为按 `pathname` 判断
  - `/admin/memory?view=browse` 这类地址刷新也能返回 SPA 入口

**影响范围**：`admin/frontend/vite.config.ts`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（prebuild 清理 3 个 AppleDouble 文件）
- `bash scripts/cleanup-appledouble.sh` 构建后追加清理 96 个 AppleDouble 文件
- `GET http://localhost:5173/admin/slang` 返回 Vite dev SPA HTML，入口为 `/admin/static/src/main.ts`
- `GET http://localhost:5173/admin/memory?view=browse` 返回 Vite dev SPA HTML
- `GET http://localhost:5173/admin/static/src/main.ts` 返回 `200 text/javascript`

---

## 2026-05-07 bot 定向重建部署黑话筛选栏与刷新回仪表盘

**变更类型**：deployment

**内容**：

- 按用户要求自动定向重建并替换 `bot` 服务：
  - 使用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot`
  - `qq-bot` 已重新创建并启动
  - `napcat` 未重建、未重启，保持原运行实例
- 容器内确认已包含最新前端资源：
  - `admin/static/assets/SlangView-DlRSHrNr.js`
  - `admin/static/assets/SlangView-gq1ISRBZ.css`
  - `admin/static/assets/index-BctfOx0m.js`

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 新实例已 Up，`napcat` 仍为原实例 Up 22 hours
- `docker logs --tail 90 qq-bot` 显示服务启动完成、OneBot 已连接、`Bot 就绪`
- `docker exec qq-bot find admin/static -name '._*'` 未发现静态目录 AppleDouble 文件
- `GET /admin/static/assets/SlangView-DlRSHrNr.js` 返回 `200 text/javascript`
- `GET /admin/static/assets/index-BctfOx0m.js` 返回 `200 text/javascript`
- `GET /admin/slang` 入口指向 `assets/index-BctfOx0m.js?v=1778108807`

---

## 2026-05-07 黑话筛选栏一体化与刷新回仪表盘

**变更类型**：fix / frontend

**内容**：

- `/admin/slang` 筛选栏从分散工具条重做为一体化 `slang-control-strip`：
  - 审核队列改为连续分段按钮，保留 `待审核 / AI 审核 / 已批准 / 全部` 与数量徽标
  - 搜索、群、作用域、置信度和操作按钮统一收进同一片简约控制条
  - 移除队列按钮副标题，降低视觉噪音
- 前端路由增加一次性刷新判断：
  - 浏览器刷新非仪表盘页面时自动回到 `/admin/`
  - 站内侧栏切换和普通路由跳转不受影响

**影响范围**：`admin/frontend/src/views/slang/SlangView.vue`、`admin/frontend/src/router/index.ts`、`admin/static`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（最终构建前自动清理 2 个 AppleDouble 文件）
- `bash scripts/cleanup-appledouble.sh` 构建后追加清理 96 个 AppleDouble 文件

---

## 2026-05-07 bot 定向重建部署黑话队列与记忆导航修复

**变更类型**：deployment

**内容**：

- 按用户要求定向重建并替换 `bot` 服务：
  - 使用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot`
  - `qq-bot` 已重新创建并启动
  - `napcat` 未重建、未重启，保持原运行实例
- 容器内确认已包含最新前端资源：
  - `admin/static/assets/SlangView-BPxFxJma.js`
  - `admin/static/assets/SlangView-CWHmGDOq.css`
  - `admin/static/assets/MemoryConsoleView-B3Pok4gv.js`
  - `admin/static/assets/MemoryConsoleView-Cf5mUD0F.css`

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 新实例已 Up，`napcat` 仍为原实例 Up 22 hours
- `docker logs --tail 80 qq-bot` 显示服务启动完成、OneBot 已连接、`Bot 就绪`
- `docker exec qq-bot find admin/static -name '._*'` 未发现静态目录 AppleDouble 文件
- `GET /admin/static/assets/SlangView-BPxFxJma.js` 返回 `200 text/javascript`
- `GET /admin/static/assets/MemoryConsoleView-B3Pok4gv.js` 返回 `200 text/javascript`

---

## 2026-05-07 黑话审核栏队列化与记忆页同组导航修复

**变更类型**：fix / frontend

**内容**：

- `/admin/slang` 筛选栏从“状态下拉 + AI 来源下拉”改为审核队列按钮组：
  - `待审核` 对应候选词条
  - `AI 审核` 对应 AI 已通过但待人工复核的 approved 词条
  - `已批准` 对应可注入词表
  - `全部` 用于总览完整词表
- 黑话列表请求参数统一由 `queueMode` 派生，删除状态与复核来源互相改值的 watcher，避免筛选条件打架。
- 修复 `MemoryConsoleView` 在 KeepAlive 后仍监听全局路由的问题：
  - 只在当前路由为 `memory` 时补齐 `view=manage`
  - 离开记忆页后不再把同“数据”分组的页面切换抢回 `/memory`
- 侧栏菜单增加防御性跳转处理，避免重复点击当前完整路由触发无意义导航。

**影响范围**：`admin/frontend/src/views/slang/SlangView.vue`、`admin/frontend/src/views/memory/MemoryConsoleView.vue`、`admin/frontend/src/layouts/components/SideMenu.vue`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（最终构建前自动清理 98 个 AppleDouble 文件）
- `bash scripts/cleanup-appledouble.sh` 构建后追加清理 97 个 AppleDouble 文件

---

## 2026-05-07 bot 定向重建部署 Slang 设置修复

**变更类型**：deployment

**内容**：

- 按用户要求定向重建并替换 `bot` 服务：
  - 使用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot`
  - `qq-bot` 已重新创建并启动
  - `napcat` 未重建、未重启，保持原运行实例
- 容器内确认已包含最新 Slang 前端资源：
  - `admin/static/assets/SlangView-Des9ufmn.js`
  - `admin/static/assets/SlangView-D-neoPjG.css`

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 新实例已 Up，`napcat` 仍为原实例 Up 21 hours
- `docker exec qq-bot ls admin/static/assets | grep SlangView` 可见最新 Slang chunk
- `docker logs --tail 80 qq-bot` 显示服务启动完成、OneBot 已连接、`slang store initialized`

---

## 2026-05-07 Slang 设置保存后新字段清空修复

**变更类型**：fix / frontend

**内容**：

- 修复 `/admin/slang` 保存设置后“每日 AI 识别”模块新字段被清空的问题：
  - 原因是保存成功后前端直接用接口返回的 `data.settings` 整体替换本地 `settings`
  - 当运行中后端暂未返回新字段，或返回体缺少新字段时，开关会变成 `undefined`，数值输入框会显示空
- 新增前端 `defaultSlangSettings` 与 `mergeSettings()`：
  - 加载设置与保存设置后都按“默认值 + 当前本地值 + 接口返回值”合并
  - 保留 `daily_ai_review_enabled`、`daily_ai_review_search_enabled`、`daily_ai_auto_approve_enabled` 等开关状态
  - 对数值字段做兜底转换，避免空值导致输入框被清空

**影响范围**：`admin/frontend/src/views/slang/SlangView.vue`、`admin/static`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（prebuild 清理 2 个 AppleDouble 文件；构建后再次清理 96 个 AppleDouble 文件）

---

## 2026-05-07 黑话每日 AI 搜索识别与 AI 通过

**变更类型**：feature / backend / frontend / tests

**内容**：

- `services/slang` 新增每日 AI 复核能力：
  - 新增 `SlangDailyReviewer`，先用现有 LLM 抽取候选，再复用 `web_search` 搜索“是什么梗 / 梗含义”，最后由 LLM 二次复核
  - 新增设置：每日识别开关、执行时间、搜索辅助开关、AI 自动通过开关、自动通过最低置信度、每日每群入库上限、每日扫描消息数
  - AI 通过词条不新增状态，仍写为 `status="approved"`，同时标记 `source="ai_auto_review"` 与 `meta.ai_approved=true`
  - 搜索失败时只降级为候选 / 观察中，不会自动通过
- `plugins/slang` 的 `on_tick` 增加每日定点任务：
  - 使用 `meta:last_daily_ai_review_date` 保证同一天只跑一次
  - 每日任务与现有间隔抽取并存，不替代原 v2/v2.5 抽取链路
- `/api/admin/slang/*` 增加 AI 复核筛选与动作：
  - 列表支持 `review_filter=ai_approved / needs_human_review / human_reviewed`
  - 新增 `human-approve`、`deny`、`return-candidate` 操作
  - “真实通过”只改人工复核元数据；“否决”会静音词条，避免反复学回
- `/admin/slang` 增加 AI 通过管理体验：
  - 指标卡显示 AI 通过数与待人工复核数
  - 列表与抽屉显示 AI 通过、待复核、人工确认标签
  - 抽屉展示 AI 理由、群内证据、搜索查询和搜索证据
  - 设置区新增“每日 AI 识别”模块，保持结构化控件，不使用 raw JSON

**影响范围**：`services/slang/*`、`plugins/slang/plugin.py`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`tests/test_slang_store.py`、`tests/test_slang_plugin.py`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过（15 passed）
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（prebuild 清理 2 个 AppleDouble 文件；构建后再次清理 96 个 AppleDouble 文件）

---

## 2026-05-07 Slang Web 主动构建黑话入口

**变更类型**：feature / backend / frontend / tests / deployment

**内容**：

- `services/slang` 新增 `create_term()`，用于 Admin Web 手动创建黑话词条：
  - 支持群内 / 全局作用域、状态、置信度、别名、复述策略、备注和示例证据
  - 手动创建来源标记为 `source="manual"`，`meta.manual=true`
  - 直接批准的词条置信度下限保持为 `0.8`
  - 群内词条要求填写 `group_id`，重复 term / alias 会返回明确错误
- `/api/admin/slang/terms/create` 新增结构化创建接口，不复用抽取候选缓冲逻辑
- `/admin/slang` 页头新增“新建黑话”按钮：
  - 打开同风格抽屉填写术语、释义、别名、作用域、群号、状态、置信度、复述策略、示例与备注
  - 创建成功后自动刷新摘要、统计、群列表和当前词条列表，并切换到对应筛选
- 已重新构建前端静态资源并定向重建 `bot`，`napcat` 未重建、未重启

**影响范围**：`services/slang/store.py`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`tests/test_slang_store.py`、`tests/test_admin_api.py`、`admin/static`、运行中的 `qq-bot` 容器

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过（11 passed）
- `cd admin/frontend && npm run build` 通过（构建前自动清理 104 个 AppleDouble 文件）
- 容器内 API router 已包含 `/api/admin/slang/terms/create`
- 登录后空 payload 调用 `/api/admin/slang/terms/create` 返回 `{"ok":false,"error":"term cannot be empty"}`，确认路由可达且未写入数据
- `docker compose ps`：`qq-bot` 已重新创建并启动，`napcat` 保持原运行实例

---

## 2026-05-07 Admin 静态资源路由修复，Slang 白屏恢复

**变更类型**：fix / backend / deployment

**内容**：

- 排查 `/admin/slang` 白屏：
  - `GET /admin/static/assets/index-*.js` 与 `GET /admin/static/assets/SlangView-*.js` 返回 `200 text/html`，内容是 SPA `index.html`
  - 浏览器因此把 HTML 当作 JavaScript module 加载，导致页面直接白屏
- 修复 `admin/__init__.py` 静态资源路由：
  - 在 SPA history fallback 前新增显式 `GET /admin/static/{asset_path:path}` 文件响应
  - 静态文件存在时返回 `FileResponse`，缺失时返回真正 `404`
  - 保留原 `StaticFiles` mount，但不再依赖其在 `APIRouter.include_router` 下的行为
- 追加缓存恢复措施：
  - SPA HTML 与静态资源响应增加 `Cache-Control: no-store`
  - SPA HTML 增加 `Clear-Site-Data: "cache"`
  - SPA HTML 为入口 JS/CSS 自动追加 `?v=<index mtime>`
  - 重新构建前端，入口文件变为 `index-CLzXCTQg.js`，Slang 页面 chunk 变为 `SlangView-D0HzJKfr.js`，避开浏览器此前缓存的坏模块
- 已仅重建并替换 `bot` 服务，`napcat` 未重建、未重启

**影响范围**：`admin/__init__.py`、运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- 本地 TestClient：`/admin/static/assets/SlangView-bLHe8fEH.js` 返回 `200 text/javascript`，缺失资源返回 `404`
- 部署后 curl：
  - `/admin/slang` 返回 `Cache-Control: no-store`、`Clear-Site-Data: "cache"`，入口指向 `/admin/static/assets/index-CLzXCTQg.js?v=1778102840`
  - `/admin/static/assets/index-CLzXCTQg.js?v=1778102840` 返回 `200 text/javascript`
  - `/admin/static/assets/SlangView-D0HzJKfr.js` 返回 `200 text/javascript`
  - `/admin/static/assets/not-found.js` 返回 `404`
- `docker compose ps`：`qq-bot` 已重新创建并启动，`napcat` 保持原运行实例

---

## 2026-05-07 Slang API 404 导致列表加载失败排查与 bot 定向重建

**变更类型**：fix / deployment

**内容**：

- 排查 `/admin/slang` 显示“黑话列表加载失败”：
  - `docker logs qq-bot` 显示 `/api/admin/slang/summary`、`/groups`、`/terms`、`/settings`、`/stats`、`/pending`、`/extract/runs` 全部返回 `404`
  - 容器内确认旧运行镜像缺少 `admin.routes.api.slang` 与 `services.slang`，原因是前端静态资源已更新，但 `qq-bot` 后端容器尚未重建
- 已仅重建并替换 `bot` 服务：
  - 首次 `docker compose up -d --build --no-deps bot` 被 Docker buildx 本机权限文件阻塞
  - 改用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot` 成功
  - `napcat` 未重建、未重启，保持原运行实例
- 重建后容器内确认 slang 路由已注册，浏览器接口不再 404

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 已重新创建并启动，`napcat` 仍 Up 20 hours
- 容器内 `admin.routes.api.slang` 与 `services.slang` 均可导入
- 容器内 API router 已包含 `/api/admin/slang/terms`、`/summary`、`/stats`、`/pending` 等 slang 路由
- 登录后请求 `GET /api/admin/slang/terms?page=1&page_size=50&status=candidate` 返回 `200`，响应 `{"terms":[],"total":0,...}`

---

## 2026-05-07 群内黑话系统 v2 / v2.5 增强落地

**变更类型**：feature / backend / frontend / tests

**内容**：

- `services/slang` 增强为 v2/v2.5 能力层：
  - 新增低频候选缓冲、抽取运行日志、批量状态操作、观察记录批量删除、词条合并、跨群 global 候选扫描、统计汇总与置信度重算
  - `candidate_min_count` 现在真正控制候选进入审核列表；未达阈值的候选进入“观察中”
  - `normalize_term` 扩展全半角、Markdown 标点和常见符号归一；新增 stoplist 与 muted 二次过滤
  - Prompt 注入增加 `max_prompt_chars` 限制，并优先当前对话命中、群内高置信词，再补全局词
- `plugins/slang` 保持薄插件定位：
  - 手动/定时抽取会记录 `slang_extraction_runs`
  - 抽取时按批次消息估算出现次数，并传入 `candidate_min_count`
  - 可选自动跨群提升仍只生成 global candidate，不自动批准
- `/api/admin/slang/*` 新增 v2/v2.5 管理接口：
  - `POST /terms/bulk`、`POST /terms/merge`、`POST /global/scan`、`GET /stats`
  - `GET /extract/runs`、`GET /pending`、`POST /terms/{id}/recompute-confidence`
- 管理端 `/admin/slang` 升级为审核控制台：
  - 新增统计卡、群活跃排行、最近抽取记录、观察中候选、跨群扫描、作用域筛选
  - 列表支持多选与批量批准/静音/过期/删除观察记录
  - 抽屉增加置信度来源摘要、重算入口和“合并到主词条”
  - 设置区新增跨群提升、批量页大小、统计窗口、stoplist、Prompt 字符上限等结构化控件

**影响范围**：`services/slang/*`、`plugins/slang/plugin.py`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`tests/test_slang_store.py`、`tests/test_slang_plugin.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过（9 passed）
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（最终构建前自动清理 97 个 AppleDouble 文件）

---

## 2026-05-07 群内黑话系统 v1 落地

**变更类型**：feature / backend / frontend / tests

**内容**：

- 新增 `services/slang` 系统服务层，提供黑话类型、SQLite 存储、候选生命周期、Prompt 注入文本生成与轻量 LLM 抽取器
- 新增 `plugins/slang` 薄插件接入消息管线：
  - `on_message` 记录已知黑话命中
  - `on_tick` 按设置批量抽取候选
  - `on_pre_prompt` 只注入当前群已批准黑话
- 新增 `/api/admin/slang/*` 管理接口，支持摘要、分页列表、详情、审核状态切换、结构化设置和手动抽取
- 新增管理端 `/admin/slang` 页面与侧栏“群内黑话”入口，提供指标、筛选、候选审核、抽屉编辑和学习/注入设置
- 默认保持审核优先，不引入 `jieba / sentence-transformers / faiss / rank-bm25` 等重依赖，不修改内核钩子和人设文件

**影响范围**：`services/slang/*`、`plugins/slang/*`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`admin/frontend/src/router/index.ts`、`admin/frontend/src/layouts/components/SideMenu.vue`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过（4 passed）
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

---

## 2026-05-07 重启按钮失败排查与接口缺失提示增强

**变更类型**：fix / frontend / deployment

**内容**：

- 排查“点击重启 Bot 显示失败”：
  - 线上 `qq-bot` 容器运行的是旧代码，`/api/admin/system/restart` 路由不存在，实测返回 `404`
  - 已执行 `docker compose up -d --build bot` 重建并重启 `bot`（`napcat` 未重建、持续运行）
  - 重建后实测 `POST /api/admin/system/restart` 返回 `200`，并可触发容器自动拉起
- 前端重启按钮错误提示增强：
  - `404` 时明确提示“运行中的 Bot 不支持重启接口，请先重建并重启容器”
  - `401` 时提示登录状态失效
  - 其他异常仍显示通用失败

**影响范围**：`admin/frontend/src/components/common/RestartBotButton.vue`、运行中 `qq-bot` 容器

**验证**：

- `curl -X POST /api/admin/system/restart`：重建前 `404`，重建后 `200`
- `docker compose ps`：`qq-bot` 已重建并自动拉起，`napcat` 保持原运行实例

---

## 2026-05-07 配置 JSON 空白兜底与页面级重启入口统一

**变更类型**：fix / frontend

**内容**：

- 撤销顶栏全局重启入口，恢复为页面内操作：`header` 不再放“重启 Bot”按钮
- 新增可复用组件 `RestartBotButton` 并按配置页样式统一接入以下页面 action 区：
  - `仪表盘`、`用量统计`、`日志`、`系统`、`人设编辑`
- 配置页增强“JSON 空内容”兜底：
  - 当接口 `schema` 为空时，自动切换到高级 JSON 模式并显示明确提示
  - 增加“顶层配置”分组，保证非 object 根字段不被隐藏
  - 当 `editor.values` 为空但 `advanced.raw_json` 有内容时，自动回填可编辑值
  - 结构化 schema 缺失时禁止关闭高级模式，避免再次出现“页面看起来无内容”

**影响范围**：`admin/frontend/src/layouts/normal/header/index.vue`、`admin/frontend/src/components/common/RestartBotButton.vue`、`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/dashboard/DashboardView.vue`、`admin/frontend/src/views/usage/UsageView.vue`、`admin/frontend/src/views/logs/LogsView.vue`、`admin/frontend/src/views/system/SystemView.vue`、`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过（含 AppleDouble 自动清理钩子）

---

## 2026-05-07 Config 路径显示统一与全局重启入口

**变更类型**：fix / frontend

**内容**：

- 配置页展示路径增加前端兜底规范化：即使接口仍返回 `.toml` 路径，页面统一显示为对应 `.json` 目标路径
- 顶栏新增全局“重启 Bot”按钮（带二次确认与状态提示），所有页面都可直接触发 `/api/admin/system/restart`

**影响范围**：`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/layouts/normal/header/index.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-07 Admin 顶栏去标签 + Config 结构化编辑 + 一键重启

**变更类型**：refactor / frontend / backend / docs / tests / deployment

**内容**：

- 管理端顶部多标签体系下线：
  - 顶栏移除 `AppTab` 区域，正文可视面积增大
  - 删除 tabs 运行链路（`stores/tabs.ts`、`AppTab.vue`、`TabContextMenu.vue`）
  - 路由切换缓存改为基于 `route.meta.keepAlive` 的单路由视图模式
- 配置中心改造为结构化编辑器：
  - `GET /api/admin/config` 升级为结构化模型返回（`format_mode`、`migration_pending`、`editor.schema`、`editor.values`、`advanced.raw_json`）
  - `POST /api/admin/config` 支持 `structured` 与 `advanced` 两种保存模式，统一 `BotConfig` 校验并返回字段级错误
  - 前端 `ConfigView` 改为“分组模块 + 字段控件（switch/input/number/list/kv/json）”，不再默认原文直出
  - Secret 字段默认部分遮罩展示，可按需进入编辑
- 配置格式迁移口径调整：
  - 运行时配置默认路径切到 `config/config.json`，并保留 `config.toml` 兼容读取
  - legacy TOML 首次保存后写出 JSON 主文件，不删除原 TOML
- 新增配置页一键重启：
  - `POST /api/admin/system/restart` 新增，确认后延迟退出进程，适配 Docker 自动拉起
  - `/admin/config` 页右上角新增“重启 Bot”按钮（带二次确认）
- Docker 与文档口径同步：
  - `docker-compose.yml` 调整为 `./config:/app/config:rw`，确保 Web 保存配置可持久化
  - `README.md`、`docs/setup-guide.md`、`docs/project-info.md` 同步补充 JSON 主格式与 legacy 兼容说明

**影响范围**：`admin/frontend/src/App.vue`、`admin/frontend/src/layouts/normal/header/index.vue`、`admin/frontend/src/router/index.ts`、`admin/frontend/src/views/config/*`、`admin/routes/api/config.py`、`admin/routes/api/system.py`、`kernel/config.py`、`docker-compose.yml`、`README.md`、`docs/setup-guide.md`、`docs/project-info.md`、`tests/test_admin_api.py`、`tests/test_config_loader.py`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit`
- `npm run build`
- `pytest tests/test_config_loader.py`
- `pytest tests/test_admin_api.py -k "config or system"`

---

## 2026-05-07 Sticker 分页、System 状态修复、Dashboard 时序重排

**变更类型**：fix / frontend / backend / tests / deployment

**内容**：

- `Stickers` 页面新增分页机制：当素材数量超过阈值时自动分页，并在列表顶部与底部同时显示页码按钮（含快速跳转）
- `System` API 修复 NapCat 连通状态误报：
  - `/api/admin/health` 改为动态检查已连接 bot，不再使用路由初始化时的静态引用
  - 返回 `connected_bots` 便于排查连接态
- `System` API 增加资源统计 fallback：
  - 无 `psutil` 依赖时使用标准库与 `/proc` 提供 CPU/内存/磁盘/进程信息
  - 活跃会话统计优先读取 `ShortTermMemory._store`，兼容旧 `_messages` 结构
- `Dashboard` 修复“下一段节奏不刷新”：
  - 改为基于当前时间实时重排全量日程，不再只看前 4 段固定切片
  - 已过时段自动灰显，未到达时段优先置顶，日程列表展示完整全天条目
- `Dashboard` 实时日志收口：
  - 过滤卡片相关日志项，避免干扰主监控视图
  - 展示总条数限制为最近 10 条
- 新增系统接口回归测试，覆盖动态健康判定与会话统计

**影响范围**：`admin/frontend/src/views/stickers/StickersView.vue`、`admin/frontend/src/views/dashboard/DashboardView.vue`、`admin/routes/api/system.py`、`admin/routes/api/__init__.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "system or soul"` 通过
- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过（已刷新 `admin/static` 产物）

---

## 2026-05-06 Soul 保存目标文件名明确化与 bot 重建

**变更类型**：fix / frontend / backend / tests / deployment

**内容**：

- Soul 保存成功与同步失败提示改为完整路径文案：`config/soul/identity.md` 与 `config/soul/instruction.md`，避免旧 `SKILL.md` 文案或浏览器缓存误导
- `/api/admin/soul/save` 的返回 `message` 同步改为双文件完整路径，不再出现 `SKILL.md` 保存口径
- 后端测试补充“即使目录中已有旧 `SKILL.md`，保存也不得改写它，且返回消息不得包含 `SKILL.md`”的断言
- 重新执行前端构建，生成新 Soul 产物 `SoulView-CRq2001F.js`
- 已执行 `docker compose up -d --build bot` 重建并重启 `qq-bot`，`napcat` 保持运行未重建

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`、`admin/routes/api/soul.py`、`tests/test_admin_api.py`、`admin/static`、`maintenance-log.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k soul` 通过
- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过；构建前后已清理 AppleDouble 文件
- 容器内确认加载新 `SoulView-CRq2001F.js`，且 `docker compose ps` 显示 `napcat` 仍为原运行实例、`qq-bot` 已重建启动

**交接说明**：`config/soul/SKILL.md` 若仍存在，是旧版保存遗留文件；当前 API、运行时与 Web 保存目标均已回到双文件。未自动删除该文件，避免误删历史内容。

---

## 2026-05-06 Soul 回归双文件编辑与 AI 人设规则文档

**变更类型**：fix / backend / frontend / docs

**内容**：

- Soul 管理端保留当前结构化编辑器与顶部节点切换设计，但保存目标回归为 `config/soul/identity.md` 与 `config/soul/instruction.md`
- `/api/admin/soul` 固定返回 legacy 双文件编辑模型，`/api/admin/soul/save` 不再生成 `SKILL.md`，保存成功后热重载 `identity.md`
- 运行时启动与 prompt 指令加载统一使用双文件：身份读取 `identity.md`，行为规则读取 `instruction.md`
- `services.identity` 保留对导入内容 YAML frontmatter 的兼容解析，但注释与测试命名不再称其为 SKILL.md 运行时格式
- 新增双文件人设生成规则文档，说明用户如何让 AI 把外部资料整理成现有双文件
- Web 人设页新增规则入口，可直接查看该规则文档；SPA 与旧 Jinja 备用页文案均移除自动迁移/写入 SKILL 的交付口径
- `identity.md` 一级标题下、第一个 `##` 前的内容稳定映射到 Web 顶部“简述”，避免保存后在结构化编辑器里漂移

**影响范围**：`admin/routes/api/soul.py`、`admin/routes/soul.py`、`admin/templates/soul.html`、`admin/frontend/src/views/soul/SoulView.vue`、`services/identity.py`、`services/llm/prompt_builder.py`、`plugins/chat.py`、`docs/ai-persona-generation-rules.md`、`tests/test_admin_api.py`、`tests/test_identity.py`、`tests/test_prompt.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_identity.py tests/test_prompt.py` 通过
- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过；预构建钩子已按既有规则清理 AppleDouble 文件，`admin/static` 按既有 Vite 输出刷新
- 构建与验证后手动执行 `bash scripts/cleanup-appledouble.sh` 收口，确保项目工作区范围内不残留 AppleDouble 文件

**交接说明**：本条 supersede 旧的“人设文件支持 SKILL.md 格式”运行时口径；后续若引入外部 SKILL，只按文档手动转换为双文件，不恢复自动读取或自动迁移。

---

## 2026-05-06 Soul AI 人设规则子页面与小标题编辑修复

**变更类型**：fix / frontend / docs

**内容**：

- 将人设规则入口从 API 弹窗改为同风格 SPA 子页面 `/admin/soul/persona-guide`，避免后端未重启时规则文档无法查看
- 规则文档重命名为 `docs/ai-persona-generation-rules.md`，页面与按钮文案改为“AI 人设生成规则”，不再使用旧的转换类称呼作为产品文案
- 子页面使用 `AppPage`、`MetricCard`、`AppCard` 呈现项目文档内容，保持 Calm Ops 管理端风格
- `SoulView` 的块级 `###` 小标题改为可编辑输入框，人物名、关系名、场景名都可在 Web 内直接修改；空小标题保存时不会写出 `###`
- 后端结构解析扩展为识别 `###` 到 `######` 小标题，兼容更多 AI 生成文档习惯，保存时统一规范成 `###`
- 左侧菜单在 `/soul/persona-guide` 子路由下继续高亮“人设编辑”

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`、`admin/frontend/src/views/soul/SoulPersonaGuideView.vue`、`admin/frontend/src/router/index.ts`、`admin/frontend/src/layouts/components/SideMenu.vue`、`admin/frontend/vite.config.ts`、`admin/routes/api/soul.py`、`docs/ai-persona-generation-rules.md`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_prompt.py` 通过
- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过；预构建钩子清理 AppleDouble 文件，`admin/static` 按既有 Vite 输出刷新

---

## 2026-05-06 Soul 编辑块合并与 Markdown 标记清洗

**变更类型**：fix / frontend / backend

**内容**：

- 人设编辑页按小标题对连续块进行展示合并：一个小标题下的段落、列表、表格会显示在同一张编辑卡片内，减少“标题说明”和“列表规则”被拆开的割裂感
- 小标题输入改为作用于整组内容，保存时仍只写出一次 `### 小标题`
- Soul API 在解析与保存结构化字段时清洗常见 Markdown 标记：`**加粗**`、反引号、链接、引用符、误入的标题/列表前缀等不会直接出现在 Web 输入框里
- 后端测试补充了 `**祖父**`、反引号文本和更深小标题的清洗断言

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`、`admin/routes/api/soul.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_prompt.py` 通过
- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过；预构建钩子清理 AppleDouble 文件，`admin/static` 按既有 Vite 输出刷新

---

## 2026-05-06 Soul 页头操作按钮等宽收敛

**变更类型**：fix / frontend

**内容**：

- Soul 页头操作区按钮统一为 `34px` 高度、圆角胶囊形态和一致的最小宽度
- “刷新结构 / 重置草稿 / AI 人设生成规则 / 保存并同步”在并排时保持视觉尺寸一致，主按钮仅通过颜色突出
- 状态标签维持轻量高度，避免与操作按钮混成一排时显得大小参差

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过；预构建钩子清理 AppleDouble 文件，`admin/static` 按既有 Vite 输出刷新

---

## 2026-05-06 Soul 保存提示文件名兜底修正

**变更类型**：fix / frontend / deployment

**内容**：

- Soul 结构化编辑页保存成功提示不再原样展示后端返回的旧 `message`，统一显示 `identity.md / instruction.md 已保存`
- 避免旧容器或缓存返回 `SKILL.md 已保存` 时误导管理员判断实际保存目标
- 说明本轮后端源码改动需要重建 `bot` 镜像；单纯 `docker compose restart bot` 不会更新容器内 Python 代码
- `.dockerignore` 补充递归 AppleDouble 忽略规则，并排除 `admin/frontend/` 源码目录，Docker 镜像继续只携带已构建的 `admin/static`
- `scripts/cleanup-appledouble.sh` 不再跳过 `__pycache__`，避免 `._*.pyc` 残留阻塞 Docker buildx 读取构建上下文

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`、`.dockerignore`、`scripts/cleanup-appledouble.sh`、`admin/static`

**验证**：

- 已由顶部 “Soul 保存目标文件名明确化与 bot 重建” 条目补充验证与部署结果

---

## 2026-05-06 Soul 横条紧凑化与页面滚动回归

**变更类型**：fix / frontend

**内容**：

- Soul 顶部节点横条从横向滚动列表改为自动换行的紧凑网格按钮，节点全部直接显示在页面内，不再左右拖动
- 进一步合并节点：
  - `基础信息`、`插话方式`、`身份概览` 合并为首个 `基础与身份` 节点
  - 行为规则从细分节点收敛为 `回复规则`、`表达素材`、`群聊与人格`、`日常工具记忆` 等复合节点
- 取消下方配置区、列表编辑器、表格编辑器和 textarea 的内部滚动限制，滚动交还给 AppPage 页面主体，避免鼠标悬停在输入框或编辑区时抢滚轮

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 节点语义合并与长章节分片

**变更类型**：fix / frontend

**内容**：

- 修正 Soul 顶部横条节点过度按章节切分的问题：
  - 人设正文按 `身份概览 / 性格与成长 / 关系与边界 / 语气表达` 聚合相似章节
  - 行为规则按 `底线与禁区 / 回复与分段 / 表情包策略 / 场景话术 / 人格稳固 / 群聊理解 / 日常与搜索 / 工具与记忆` 聚合
  - 超长章节不再整章塞进一个节点，而是按内部 block 权重拆成多个片段节点
- `SoulView` 节点结构从“完整章节列表”扩展为“章节片段列表”，仅影响前端展示，不改变保存时的 `editor` 数据结构与后端接口
- 当前节点配置区增加受控最大高度和内部滚动，避免某个节点把整页拉成多屏，同时短节点会和相近内容合并显示

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 顶部节点编辑器空白与页签切换修复

**变更类型**：fix / frontend

**内容**：

- 审计当前 Soul 空白态后，收敛为顶部横条节点编辑器的显式渲染结构：
  - `AppEditorShell` 只承担顶部工具栏
  - 节点横条与当前节点配置面板改为 `soul-editor` 内的显式兄弟块
  - 当前节点面板增加 `:key="currentNode.id"`，点击节点后强制按节点重挂载配置区
  - 增加“没有可切换的配置节点”兜底，避免接口结构异常时只剩空卡片
- 修复顶部应用页签切换链路：
  - `AppTab` 从 `NTab @click` 改为受控 `NTabs @update:value`
  - 关闭当前页签后主动跳转到新的 active tab，避免只更新 store 不更新路由

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`、`admin/frontend/src/layouts/components/AppTab.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 左侧导航废弃，改为顶部横条节点编辑器

**变更类型**：fix / frontend

**内容**：

- 废弃 `SoulView` 左侧章节导航方案，不再保留 sticky、fixed、floating、docked、scroll spy 或锚点滚动定位逻辑
- `Soul` 编辑区改为顶部横条节点切换：
  - 固定包含 `基础信息` 与 `插话方式`
  - 人设正文与行为规则按章节权重自动分组为若干节点
  - 点击横条节点只切换当前配置内容，不再驱动页面滚动
- 单次只渲染当前节点下的配置区，长列表、长键值表和长文本块使用受控高度，避免单个节点把页面拉得过长
- 回退上一轮仅为悬浮目录新增的 `AppPage surface="plain"` 公共接口，`AppPage` 恢复默认单一内容壳

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`、`admin/frontend/src/components/common/AppPage.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

**交接说明**：此前 `Soul 左栏由原生 sticky 改为受控悬浮 rail` 记录已被本条 supersede；后续不要再恢复左侧 sticky/fixed/floating 导航方案

---

## 2026-05-06 Soul 左栏由原生 sticky 改为受控悬浮 rail

**变更类型**：fix / frontend

**内容**：

- `AppPage` 新增轻量内容壳模式 `surface="plain"`，允许页面内容直接落在 `data-page-scroll-root` 中，不再强制包裹大面积玻璃卡片
- `SoulView` 放弃原生 `position: sticky` 左栏方案，改为单一三态悬浮目录：
  - `resting`：章节区尚未进入吸附线，左栏保持自然文档流位置
  - `floating`：进入章节区后，左栏固定在页面滚动根可视区内
  - `docked`：接近章节区底部时，左栏停靠在 rail 底部，不再继续上滑或越界
- 目录定位只绑定 `AppPage` 的 `data-page-scroll-root`，并通过 `ResizeObserver + requestAnimationFrame` 同步 `left / top / width / max-height`
- 保留 `scrollIntoView + scroll-margin-top + IntersectionObserver` 的章节跳转与高亮逻辑，但不再让这些逻辑参与左栏定位
- 目录桌面宽度调整为 `304px`，中等桌面收敛为 `272px`，`<=980px` 退化为正文前普通卡片

**影响范围**：`admin/frontend/src/components/common/AppPage.vue`、`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

**交接说明**：此前多轮 sticky/fixed 混合方案已被本条 supersede；后续若继续增强 Soul 左栏，应沿用 `surface="plain" + 受控悬浮 rail`，不要再回退到原生 sticky 试错

---

## 2026-05-06 Soul fixed 导航回退为单一 sticky rail

**变更类型**：fix / frontend

**内容**：

- 对 Soul 左栏导航做清理式重构，移除最近几轮叠加出的 `fixed + ResizeObserver + 几何定位` 方案
- 恢复为单一 sticky rail 架构：
  - `soul-nav-rail` 负责吸附
  - `soul-nav` 只负责卡片视觉与最大高度裁切
  - `soul-nav__body` 继续作为唯一可滚动的目录列表区
- 左栏默认桌面宽度提升到 `288px`，中等桌面收敛到 `256px`，`<=980px` 才退化为普通单栏目录
- 保留 `data-page-scroll-root + scrollIntoView + IntersectionObserver` 的章节联动主链，不再保留 fixed 定位相关状态与观察器

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

**交接说明**：此前 `Soul 左栏导航改为 fixed 覆盖架构` 记录已被本条 supersede，后续不应再恢复 fixed 方案

---

## 2026-05-06 Soul 左栏导航改为 fixed 覆盖架构

**变更类型**：fix / frontend

**内容**：

- 放弃此前多轮 `sticky` 方案，改为固定式导航壳：
  - `soul-nav-rail` 继续留在布局中负责占位
  - 真正可见的 `soul-nav` 在桌面宽度下使用 `position: fixed`
  - 通过 `navRailElement.getBoundingClientRect()` 实时同步导航卡的 `left / top / width / height`
- 新增 `ResizeObserver` 监听 rail 与页面滚动根尺寸，避免侧栏折叠、窗口尺寸变化后 fixed 导航错位
- 保留左栏头部固定、目录列表区内部滚动；桌面下不再依赖 sticky 约束链，因此不会被页面滚动一起卷走

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 左栏内部滚动与悬浮壳分离

**变更类型**：fix / frontend

**内容**：

- 修复 Soul 左侧目录在悬浮状态下“内容继续往上卷，标题消失”的问题
- 根因是此前将整张 `soul-nav` 卡片作为滚动容器，滚轮落在左栏时会把目录卡片内部整体卷动
- 结构调整为：
  - `soul-nav-rail` 负责 sticky 悬浮
  - `soul-nav` 负责卡片壳与头部固定
  - 新增 `soul-nav__body` 作为唯一可滚动的目录列表区
- 窄屏降级时同时关闭 `soul-nav__body` 内部滚动，恢复为普通静态目录块

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 悬浮目录断点与 rail 架构修正

**变更类型**：fix / frontend

**内容**：

- 修复 Soul 左侧目录在常规桌面/笔记本宽度下滚动后消失的问题
- 根因是 `SoulView` 在 `max-width: 1180px` 时会显式关闭 sticky，导致部分笔记本宽度直接退化为普通随页滚动目录
- 结构上将 sticky 能力从 `soul-nav` 卡片本体挪到 `soul-nav-rail` 外层容器，目录卡片仅负责内部滚动与视觉样式
- 响应式阈值收紧：
  - `<=1280px` 仅缩窄 rail 宽度
  - `<=980px` 才退化为单栏普通目录卡片

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 章节点击跳转恢复

**变更类型**：fix / frontend

**内容**：

- 修复 Soul 页面左侧目录点击后右侧不跳转的问题
- `SoulView` 的章节点击从“手算 scrollTop”回退为原生 `scrollIntoView`，继续配合章节锚点上的 `scroll-margin-top` 控制停靠位置
- 页面滚动根选择收紧为“必须是真正可滚动的 `data-page-scroll-root` 容器”，避免误绑到存在但当前不可滚动的祖先节点

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 单滚动模型回退与左侧悬浮目录重构

**变更类型**：fix / frontend

**内容**：

- 回退 `Soul` 页面此前“左栏 sticky + 右栏独立滚动”的双滚动域方案，恢复为 `AppPage` 内容区单一滚动模型
- `AppPage` 内容主体新增稳定 DOM 标记 `data-page-scroll-root`，供页面级目录联动查找真实滚动根
- `SoulView` 的章节点击与 `IntersectionObserver` 改为统一绑定页面主体滚动容器，不再绑定右栏容器或 `window`
- 左侧目录改为 rail + sticky card 结构：
  - rail 负责占位，避免覆盖正文
  - 目录卡根据页面主体可视高度裁切，自身可滚动
  - 窄屏下退化为正文前的普通卡片，不再固定到底部或遮挡内容

**影响范围**：`admin/frontend/src/components/common/AppPage.vue`、`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

**交接说明**：Soul 页后续若再做目录增强，应继续复用 `data-page-scroll-root` 作为唯一滚动根，避免重新引入右栏独立滚动

---

## 2026-05-06 Soul 悬浮目录与右侧独立滚动修复

**变更类型**：fix / frontend

**内容**：

- 针对 Soul 页面“点击目录后左栏被一起顶走”的问题，改为双滚动域：
  - 左栏 `soul-nav` 保持悬浮目录（sticky）
  - 右栏 `soul-editor` 改为独立滚动容器（`overflow-y: auto` + `max-height: 100dvh`）
- `SoulView` 滚动根探测优先绑定右栏编辑容器 `editorScrollContainer`，目录点击与滚动高亮不再驱动整页滚动
- 保留窄屏单栏降级：`<=1180px` 时关闭右栏独立滚动与左栏悬浮，避免移动端遮挡

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 左侧章节导航可视区固定修复

**变更类型**：fix / frontend

**内容**：

- 修复 Soul 页面点击章节后左侧导航被整体滚出可视区的问题
- `soul-console` 容器补充 `min-height: 0` 与 `align-items: start`，稳定双栏布局滚动上下文
- `soul-nav` 增加 `position: sticky; top: 0`，并启用独立纵向滚动，确保右侧章节跳转时左侧目录持续可见
- 移动端断点（`<=1180px`）下关闭 sticky，避免单栏模式下导航遮挡内容

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 章节导航点击失效回归修复

**变更类型**：fix / frontend

**内容**：

- 修复 `admin/frontend/src/views/soul/SoulView.vue` 导航点击偶发“无跳转/错跳转”问题：
  - 为章节导航引入前端唯一 `anchorId`（`persona-*` / `instruction-*`），避免 legacy 模式下人设与规则章节 `section.id` 重复导致锚点映射冲突
  - `bindSectionElement` 改为稳定函数引用缓存，降低重渲染时 ref 回调抖动对锚点绑定的影响
  - `scrollToSection` 改为容器感知滚动：优先对探测到的滚动容器执行 `scrollTo`，无容器时回退 `window.scrollTo`
  - 新增锚点与 ref 绑定清理逻辑，章节刷新后剔除失效绑定，避免 observer/映射残留
- 保持后端接口、`SoulEditorPayload` 语义与页面业务字段不变，仅修复导航联动实现细节

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-06 Soul 章节导航 Scroll Spy 联动修复

**变更类型**：fix / frontend

**内容**：

- 修复 `admin/frontend/src/views/soul/SoulView.vue` 左侧章节导航与右侧内容锚点脱耦问题：
  - 导航锚点从组件 `ref` 改为原生 `<section>` 锚点容器 + function ref 绑定
  - 点击导航改为滚动到真实 DOM 锚点（`scrollIntoView`）
- 引入 TOC scroll spy：
  - 使用 `IntersectionObserver` 监听章节锚点可视状态
  - 滚动时自动更新 `currentSectionId`，左侧高亮跟随视区变化
- 引入滚动容器自适配：
  - 自动探测最近可滚动祖先作为 observer `root`
  - 根据容器内边距动态计算 `scroll-margin-top` 偏移，避免硬编码
- 生命周期稳定性增强：
  - 在 `loadSoul`、`resetDraft`、章节列表变化、窗口尺寸变化时重建 observer
  - `onBeforeUnmount` 主动断开 observer，避免残留监听

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

**交接说明**：后续若新增章节类型，只要继续包在 `soul-section-anchor` 锚点容器内，目录联动可自动生效

---

## 2026-05-06 Docker 定向重建 bot（未触碰 napcat）

**变更类型**：ops / deploy

**内容**：

- 按要求仅对 `docker compose` 中的 `bot` 服务执行定向重建与重建容器
- 实际执行方式：
  - 使用 `docker compose up -d --build --no-deps bot`
  - 显式避免触发依赖服务，因此 `napcat` 未被重启或重建
- 构建过程中遇到 macOS AppleDouble 影子文件导致 Docker build context 报错：
  - 首次卡在 `._maintenance-log.md`
  - 二次卡在 `admin/__pycache__/.___init__.cpython-313.pyc`
- 已在重建前补做 AppleDouble 清理，随后成功完成 `bot` 镜像重建与容器替换

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 保持原状态

**交接说明**：本次重建后 `qq-bot` 已重新启动；若后续再次出现同类 Docker 构建失败，优先检查仓库中的 `._*` 文件

---

## 2026-05-06 Soul 结构页加载失败诊断增强

**变更类型**：fix / docs

**内容**：

- 修正 `admin/frontend/src/views/soul/SoulView.vue` 的加载失败呈现：
  - 不再把 `/api/admin/soul` 请求失败伪装成“Legacy / 0 章节 / 已同步”
  - 新增接口形状校验，区分新版 editor model、旧版响应、404、500、401 等错误
  - 在前端直接提示“前端已更新、后端未重启”这类混合部署问题
- 新增空状态与指标卡的错误文案联动，方便浏览器侧快速判断故障原因
- 本地确认：当前仓库里的新版 `/api/admin/soul` 在测试环境下能正常返回完整结构；截图中的“无法加载结构”高概率是运行中的 bot 还未加载新版 Soul API

**影响范围**：`admin/frontend/src/views/soul/SoulView.vue`

**交接说明**：如果浏览器仍提示 Soul API 旧版或缺失，需要在部署新静态资源后重启 bot，使前后端版本一致

---

## 2026-05-06 Codex 自动更新维护日志规则

**变更类型**：docs / process

**内容**：

- 将“持久变更后同步更新 `maintenance-log.md`”写入 Codex skill：
  - `codex-skills/omubot-admin-console/SKILL.md`
- 同步镜像到 Claude skill，避免两端协作规则漂移：
  - `.claude/skills/omubot-admin-console/SKILL.md`
- 在 `docs/agent-ui-guidelines.md` 增补维护日志自动更新规则，明确触发条件：
  - 部署、运行时、配置、路由、API、存储等行为变化
  - 管理端阶段性里程碑
  - Skill / 流程 / 协作规则更新
- 约定：若任务仅为阅读、调研、答疑且未形成持久仓库改动，可不写维护日志

**影响范围**：`codex-skills/`、`.claude/skills/`、`docs/agent-ui-guidelines.md`

**交接说明**：后续 Codex 在本仓库完成符合触发条件的任务时，应在同一轮内同步更新 `maintenance-log.md`

---

## 2026-05-06 管理端审计总结 + Web 重构进度记录 + Codex Skill 接入

**变更类型**：docs / process

**内容**：

- 完成管理端 SPA 审计，确认当前重点为“统一新 Web 页面风格与信息层级”，而不是补主流程功能
- 沉淀 `Calm Ops / 雾青控制台风格` 规范，形成：
  - `docs/admin-ui-style-guide.md`
  - `docs/agent-ui-guidelines.md`
- 已记录会话交接文档：`docs/session-handoff.md`
- 当前已完成统一重构页面：
  - Login、Dashboard
  - System、Logs
  - Groups、Memory
  - Plugins、Knowledge、Usage
- 建议下一批优先继续：
  - Scheduler
  - Sandbox
  - Memos
- 新增并接入项目内 Skill：
  - `.claude/skills/omubot-admin-console/`
  - `codex-skills/omubot-admin-console/`
  - `scripts/install-codex-skill.sh`
- 已确认本机 Codex 全局目录存在 `~/.codex/skills/omubot-admin-console/`

**影响范围**：`docs/`、`.claude/skills/`、`codex-skills/`、`scripts/install-codex-skill.sh`

**交接说明**：下一会话优先阅读 `docs/session-handoff.md`，可直接恢复管理端重构与审计上下文

---

## 2026-05-06 card_series 独立表 + admin/static volume 挂载

**变更类型**：feature / infra

**内容**：

- 新增 `card_series` 表：规范化卡片分组，`memory_cards` 加 `series_id` 外键
- `_backfill_food_series()`：CardStore.init() 自动迁移旧 food 插件卡片到系列
- food 插件 `_add_preference` 修复：创建 `food_pref:{user_id}` 系列
- food 插件 `_record_served` 系列感知：`food_served:{user_id}` 系列 + 20 张上限
- `find_similar()` / `reinforce()` 新增：前缀匹配 + confidence 增量
- MemoryView 重写：单 NDataTable 内嵌系列折叠行（SeriesHeaderRow + CardRow）
- MemosView 修复：`Promise.allSettled` 防级联失败、`NCollapse` v-model 改 writable ref
- admin API：`GET /memory/series` 全量系列端点、`_card_to_dict` 加 `series_id`
- `docker-compose.yml`：bot 服务添加 `./admin/static:/app/admin/static:ro` volume 挂载

**影响范围**：`services/memory/card_store.py`、`plugins/food.py`、`admin/routes/api/memory.py`、`admin/frontend/src/views/`、`docker-compose.yml`

**部署注意**：加 volume 挂载后，前端更新只需 `npm run build` + `docker compose restart bot`，无需重建容器

---

## 2026-05-06 Vue 3 SPA 控制台前端

**变更类型**：feature

**内容**：

- 新增 `admin/frontend/` Vue 3 + TypeScript + Naive UI + UnoCSS 前端项目
- 17 个页面：仪表盘、用量统计、沙盒、人设编辑、日程心情、记忆管理、好感度、表情包、知识库、Memo、群管理、插件、调度器、配置、系统、日志、登录
- 56 个 JSON API 端点（`/api/admin/*`），含 SSE 实时推送
- Vite 开发服务器（5173）+ 生产构建到 `admin/static/`
- 后端 SPA fallback：所有 `/admin/*` 路由返回 `index.html`

**修复项**：

- P0：`auth.checkAuth()` 启动调用 + `router.beforeEach` 守卫
- P1：6 个 TypeScript 类型错误
- P2：`AppPage.showHeader` 默认值改为 `true`
- P3：清理未使用导入、switch/case 作用域
- P4：后端 SPA fallback 路由顺序
- P5：favicon 双重路径
- 暗色主题：NButton、NMenu、NCard 等组件 CSS 变量覆盖
- 晚期绑定：stickers/memos 路由添加 `ctx` 回退

**影响范围**：`admin/` 目录，不影响 bot 消息管线

**回滚方案**：删除 `admin/frontend/` 和 `admin/static/`，恢复 `admin/__init__.py` 原始版本

---

## 2026-05-05 群聊 @mention 引用回复机制（方案 B：Prompt + 元数据注入）

**变更类型**：feature

**内容**：

**调研阶段**：

- 研究 6 篇论文/项目：W2W (Le et al. 2019)、Inoue et al. (2025)、Multi-Party Hangover (Penzo et al. 2024)、MUCA (Mao et al. 2024)、MaiBot Planner/Replyer、OpenClaw
- 核心结论：LLM 在收件人识别上准确率仅 80.9%（随机基线 80.1%），假阴性为主——框架应提供 out-of-band 信号（Who/Which message），LLM 做语言决策

**Phase 1 — 触发元数据传递**：

- `services/memory/timeline.py`：`TimelineMessage` 新增 `trigger_reason` + `trigger_target` 字段；新增 `add_pending_trigger(group_id, reason, message_id, target_user_id)` 方法；`merge_user_contents()` 渲染触发标记为 `«触发原因: ... | 来自 QQ=xxx | 消息ID=yyy»`（元数据，非消息体）
- `services/scheduler.py`：`_do_chat()` 传递 `target_user_id=trigger.target_user_id` 给 `add_pending_trigger()`

**Phase 2 — 框架级 @mention 注入**：

- `services/scheduler.py`：`_do_chat()` 中 @mention 触发时，框架自动在第一条流式分段前拼接 `[CQ:reply,id=X]`（引用回复），后续分段正常发送
- 实现方式：`first_segment` 标志 + `reply_prefix` 默认参数绑定（ruff B023 兼容）
- 非流式回退：`on_segment` 未被调用时，在 `reply` 前直接拼接前缀
- 移除 `[CQ:at,qq=X]`——引用回复已能识别目标，不需要重复 @

**Phase 3 — 指令更新**：

- `config/soul/instruction.md`：@提及决策规则从单行说明扩展为结构化规则块（何时应该 @ / 何时不需要 @ / 与亲昵称呼的关系）

**调试修复**（3 轮）：

- 触发标记顺序：`append()` → `insert(0, msg)`（标记必须在用户消息之前）
- 重复 `«msg:mid»` 标签：触发标记是元数据，不渲染 `«msg:mid»`
- 流式分段恢复：@mention 时 `on_segment=None` 导致整条回复不切分 → 改为 `first_segment` 模式

**影响范围**：`services/scheduler.py`、`services/memory/timeline.py`、`config/soul/instruction.md`

**验证**：35/35 scheduler 测试通过，bot 已重建部署

**回滚方案**：`git revert` 即可；instruction.md 为 volume mount，删除新增规则块即回退

---

## 2026-05-05 FoodPlugin 群聊引用回复 + 版本提升

**变更类型**：feature

**内容**：
- `plugins/food.py`：新增 `_send_reply()` 辅助方法，群聊场景自动在回复前拼接 `[CQ:reply,id=X][CQ:at,qq=X]`，解决多人同时 `/吃什么` 时回复混在一起的问题
- 覆盖全部 8 个 handler：`_handle_eat`（含首次提示/空结果）、`_feedback_recommend`（拒绝重新推荐，从 `on_message` 传入 `message_id`）、`_handle_like`、`_handle_dislike`、`_handle_location`、`_handle_info`、`_handle_search_toggle`、`_handle_food_help`
- 私聊不受影响（`cmd_ctx.group_id` 为 None 时不加前缀）
- 移除各 handler 中不再需要的 `Message` 局部导入

**版本**：FoodPlugin 0.1.4 → 0.1.5，bot 1.2.5 → 1.2.6

**影响范围**：FoodPlugin 全部命令回复

---

## 2026-05-05 调度器重构：TriggerContext 统一触发器 + 概率参数调优

**变更类型**：refactor + fix

**内容**：

**Phase 1-2 — TriggerContext 统一触发器模型**：
- `kernel/types.py`：新增 `TriggerContext` dataclass（reason/mode/target_message_id/target_user_id/extra），替代 ad-hoc 的 `video_hint` dict + `force_reply` bool + `is_at` flag；`MessageContext` 新增 `trigger` 字段
- `services/memory/timeline.py`：`TimelineMessage` 新增 `trigger_reason` 字段；新增 `add_pending_trigger()` 方法，将触发原因写入 pending buffer 而非 transient `user_content`
- `plugins/bilibili.py`：设置 `ctx.trigger = TriggerContext(...)` 替代 `_bilibili_reply`
- `kernel/router.py`：对 @ 消息构造 `TriggerContext(mode="at_mention")`，传递给 scheduler
- `services/scheduler.py`：`_GroupSlot` 用 `trigger: TriggerContext` 替代 `video_hint` + `force_reply`；`notify()` 在所有 skip 路径清除 `slot.trigger = None`（**修复 video_hint 泄漏 bug**）；概率计算从 `trigger.extra` 取值

**Phase 3 — Thinker 强制回复守卫**：
- `services/llm/client.py`：thinker 前加 `and not force_reply` 守卫，@ 触发和 video_always 跳过 thinker 决策，保证必回复且省 token

**Phase 4 — QQ 引用回复**：
- `services/scheduler.py`：`_send_to_group()` 检测 `[CQ:reply,id=X]` 前缀并记录日志
- `services/memory/timeline.py`：`add_pending_trigger()` 传入 `message_id`，渲染为 `«msg:id»` 标签供 LLM 使用
- `config/soul/instruction.md` 已有 CQ reply/at 格式文档，LLM 可直接输出

**概率参数调优**：
- `config/talk_schedule.json`：全部时段乘数上限从 1.2 降到 0.7
- `services/scheduler.py`：autonomous 高兴趣 time_mult 覆盖从 `1.0` 改为 `max(time_mult, 0.7)`（保底不封顶）
- `services/scheduler.py`：interest blend 地板从 `0.3 + 0.7×interest` 改为 `0.1 + 0.9×interest`（低兴趣视频从 ~28% 降到 ~12%）

**测试**：
- `tests/test_scheduler.py`：35 个测试全部迁移到 `TriggerContext` 参数，零回归

**影响范围**：scheduler、timeline、bilibili plugin、router、llm client、talk_schedule 配置

---

## 2026-05-05 文档更新：插件开发指南 + wiki 修订

**变更类型**：docs

**内容**：
- 新增 `docs/wiki/Plugin-Development.md`：插件开发完整教程（插件形态、最简结构、钩子生命周期、命令注册含门禁字段、RichCommandContext 使用、工具注册、Prompt 注入、消息拦截、定时任务、最佳实践 7 条）
- 更新 `docs/wiki/Plugins.md`：重写"命令注册"章节——门禁字段表格、子命令示例、RichCommandContext、format_help()、完整示例链接
- 更新 `docs/wiki/Commands.md`：全部命令表格含权限列、`/debug` 详解、门禁字段文档
- 更新 `docs/wiki/_Sidebar.md`：新增"插件开发"链接

**影响范围**：仅 wiki 文档，无代码变更

---

## 2026-05-05 指令系统重构：统一门禁、自动帮助、RichCommandContext

**变更类型**：refactor

**内容**：
- `kernel/types.py`：新增 `RichCommandContext`（携带 `plugin_ctx` 全部服务，handler 不再需要 `self._ctx` 间接访问）；`Command` 新增 5 个元数据字段——`admin_only`、`private_only`、`require_args`、`hidden`、`passthrough_unknown`；新增 `format_help()` 自动生成帮助文本（含门禁标注）
- `services/command.py`：重写 `_dispatch_cmd`——统一门禁层（admin/private/args 在调用 handler 前自动检查）、未知子命令检测（列出可用子命令）、`passthrough_unknown` 支持 `/debug <text>` 透传至 LLM；`dispatch()` 签名新增 `plugin_ctx` 参数；修复尾部空格误触"未知子命令"bug
- `kernel/router.py`：两处 `dispatch()` 调用传入 `plugin_ctx`
- `plugins/food.py`：删除 `_require_private` 方法（17 行）；handler 去除手动 args 校验和 ctx None 检查；`_handle_food_help` 改用 `format_help()` 动态生成；`/food search` 新增 `admin_only` 保护；`/food like/dislike/location` 新增 `require_args`；首次提示加上"（本消息只显示一次）"
- `plugins/chat.py`：`/debug` 设为 `admin_only` + `passthrough_unknown`；3 个子命令 handler 删除手动 admin 检查；`/debug split` 新增 `require_args`
- `plugins/debug_commands.py`：`/plugins` 设为 `admin_only`；handler 删除手动 admin 检查 + ctx None 检查

**影响范围**：`kernel/types.py`、`services/command.py`、`kernel/router.py`、`plugins/food.py`、`plugins/chat.py`、`plugins/debug_commands.py`

**验证**：ruff check 零新增；677 passed, 8 预存失败，零回归；bot 已重启验证

**回滚方案**：`CommandContext` 保留未删除，且 `RichCommandContext` 是其超集；回退到旧 `CommandDispatcher` 即可，所有 Command 注册向后兼容（新字段默认值与原行为一致）

---

## 2026-05-05 人格文件支持 SKILL.md 格式

**变更类型**：enhancement

**内容**：
- `services/identity.py`：`parse_identity()` 兼容 SKILL.md 格式（YAML frontmatter + Markdown body）。检测 `---` frontmatter 提取 name/description 元数据，body 部分沿用原有 `# 标题` + `## 插话方式` 解析逻辑。新增 `_strip_frontmatter()` 工具函数。`Identity` 模型新增 `description` 字段
- `services/llm/prompt_builder.py`：`load_instruction()` 优先读取 `config/soul/SKILL.md`（剥离 frontmatter 后返回 body），回退到 `instruction.md`
- `admin/routes/soul.py`：Web 编辑器支持双模式——SKILL.md 存在时展示单文件编辑器（name/description 表单 + body 文本域），否则展示旧双编辑器
- `admin/templates/soul.html`：新增 SKILL.md 模式 UI
- `plugins/chat.py`：启动时优先加载 `SKILL.md` 作为 identity 文件
- 新增 8 个测试（6 个 parse_identity SKILL.md 解析 + 2 个 load_instruction SKILL.md）

**影响范围**：`services/identity.py`、`services/llm/prompt_builder.py`、`admin/routes/soul.py`、`admin/templates/soul.html`、`plugins/chat.py`

**验证**：ruff check 零新增；677 passed, 8 预存失败，零回归

**回滚方案**：删除 `config/soul/SKILL.md` 即可回退到旧 identity.md + instruction.md 双文件模式；代码向后兼容旧格式

---

## 2026-05-05 FoodPlugin thinking 参数透传修复 (v0.1.4)

**变更类型**：bugfix

**内容**：
- 第一次尝试：移除 `thinking={"type": "disabled"}` → TypeError 解决，但 deepseek-v4-flash 默认开启 thinking，64 tokens 被 thinking 吃光 → `extract_text` 返回空 → "脑袋空空了"
- 最终方案：将 `thinking` 参数从 `LLMClient._call` → `call_api` 完整透传：
  - `call_api()` 新增 `thinking: dict | None = None`，非空时写入 `body["thinking"]`
  - `LLMClient._call()` 新增 `thinking` 参数并转发给 `call_api`
  - `plugins/food.py` 两处恢复 `thinking={"type": "disabled"}`，同时 `max_tokens` 从 64 提升到 128 作为安全余量
- 根因：`call_api` 是直接构建 Anthropic 请求的独立函数，不经过 Provider 抽象层；Provider 的 `build_request` 虽支持 `thinking`，但 food plugin 走 `_call → call_api` 路径

**影响范围**：`plugins/food.py`、`services/llm/client.py`

**验证**：ruff check 零新增；bot 已重建部署

**回滚方案**：移除 `thinking` 参数，改为增大 `max_tokens` 到 512 以上以容纳默认 thinking 消耗

---

## 2026-05-05 FoodPlugin 食物库上线 + Web 搜索开关

**变更类型**：feature

**内容**：
- **食物库**（`plugins/food_library.json`）：1094 条食物条目，覆盖 16 个品类、56 个品牌
  - 10 个标签字段：name / taste / region / available_time / category / staple / meat_veg / cooking_method / temperature / brand
  - staple 从 8 种扩展为 12 种：汉堡/披萨/三明治/糕点/面点 独立分类（汉堡不再归类为"面包"）
  - available_time 从 36 条扩展至 189 条精确标注：早餐(97) / 下午茶(39) / 夜宵(53)
  - 品牌覆盖：麦当劳、肯德基、海底捞、太二、费大厨、西贝等 56 个连锁品牌
- **食物库筛选逻辑**（`plugins/food.py`）：
  - `_filter_food_library()`：按时段→排除品牌→口味偏好→最近排除→用户偏好 五层过滤，最多返回 40 条给 LLM
  - `_parse_exclusions()`：从"不要麦当劳""不吃面"等自然语言提取结构化排除条件（brand/staple/taste/category）
  - `_format_library_items()`：格式化为 `食物名 [品牌 | 口味 | 分类 | 主食 | 烹饪 | 温度]`
- **Web 搜索开关**（默认关闭）：
  - `_search_enabled = False`：/吃什么 跳过搜索，直接从食物库筛选后由 LLM 选择
  - `/food search on|off` 运行时切换，`/food info` 显示当前状态
  - 开启后恢复 Web 搜索 → 食物库 fallback 的双路径
- **版本**：FoodPlugin 0.1.2 → 0.1.3

**影响范围**：`plugins/food.py`、`plugins/food_library.json`（新增）

**验证**：ruff check 零新增；pytest 690 passed（9 预存失败与食物插件无关）

**回滚方案**：`git revert` 即可；或 `/food search on` 恢复旧的 Web 搜索路径

---

## 2026-05-04 B站 JSON 卡片多 URL 遍历 + 无 scheme URL 修复

**变更类型**：bugfix

**内容**：
- **JSON 卡片多 URL 遍历**（`plugins/bilibili.py`）：
  - `_extract_bilibili_json_info()` 改为收集全部候选 URL（`_all_urls`），不止取第一个
  - `on_message()` 遍历全部 URL 逐个尝试解析，任一成功即停止
  - 根因：QQ 小程序卡片 `detail_1` 同时包含 `url`（QQ 小程序页）和 `share_url`（b23.tv 短链），旧代码只取第一个 → 拿到无用的 QQ 页面 URL → 解析失败 → 回退搜索 → 搜索也失败
  - 效果："萍儿的低皮质醇" → `m.q.qq.com/a/s/...`（无用）跳过，`b23.tv/qmz0jXy?...`（b23.tv）成功解析 → `BV1jGRgB4EZr`
- **无 scheme URL 归一化**（`plugins/bilibili.py`）：
  - `_resolve_urls_to_vid()` 对不含 `://` 的 URL（如 `m.q.qq.com/a/s/...`）自动补 `https://` 前缀
  - 根因：QQ 小程序卡片 URL 不含协议头，`url.startswith("http")` 为 False → HTTP 重定向跟踪永不触发
- **调试日志增强**：
  - 列出全部候选 URL（`urls=N` + 逐个 `url[i]=...`）
  - 无 URL 时打印 `detail_1`/`meta`/`data` 的 keys 以诊断数据格式
  - JSON 原始数据截断从 500 字符扩展到 2000 字符

**影响范围**：`plugins/bilibili.py`（bilibili 1.1.1 → 1.1.2）

**回滚方案**：`git revert` 即可

---

---

## 2026-05-03 B站搜索匹配修复：前置标识词惩罚 + qqdocurl 跳转

**变更类型**：bugfix

**内容**：
- **前置标识词不匹配惩罚**（`plugins/bilibili.py`）：
  - 新增 `_extract_first_word()` — 提取关键词首个有意义的词（2-3 字，跳过括号/标点）作为身份标识
  - `_title_match_score()` 字符集模糊匹配分支：若前置标识词不在候选标题中，得分 × 0.3
  - 根因：字符集匹配给"的低皮质醇"（通用描述）和"萍儿/豹"（关键标识）同等权重 → "萍儿的低皮质醇"误匹配到"豹的低皮质醇~"
  - 效果：误匹配 "萍儿→豹" 从 0.21 降至 0.064，正确匹配不受影响
- **qqdocurl 通用跳转跟随**（`plugins/bilibili.py`）：
  - `_resolve_urls_to_vid()` 新增通用 HTTP 重定向跟随：`http(s)` 开头的未知短链自动跟随跳转，从最终 URL 提取 BV 号
  - 根因：QQ 小程序卡片通过 `qqdocurl` 跳转到 B站，旧代码只处理 b23.tv 一种短链，不认识 qqdocurl → URL 解析失败 → 回退到搜索
  - 效果：有 `qqdocurl` 的卡片可通过 HTTP 重定向解出 BV 号，根本不需要搜索（100% 准确）

**影响范围**：`plugins/bilibili.py`

**验证**：659 passed, 8 预存失败，零回归

**回滚方案**：`git revert` 即可

---

## 2026-05-03 Thinker 启用 + B站关键词配置化 + image_ref 修复

**变更类型**：bugfix + enhancement

**内容**：
- **Thinker 启用**：`config.toml` 新增 `[thinker]` 段（`enabled = true`）。多阶段流水线代码（Phase 1-4）虽已实现但 ThinkerConfig.enabled 默认为 false，导致预回复思考从未在生产中运行。启用后每次回复前 Thinker 预判 action/thought/sticker/tone，主 LLM 收到 `【你决定说话：...】【sticker: yes/no】【tone: ...】` 指令后再生成回复。
- **image_ref 过滤修复**（`services/llm/client.py`）：Thinker 调用前过滤图片块，只保留 text 类型。根因：Thinker 调用在 `resolve_image_refs()` 之前，消息中的 `image_ref` 内部类型直接发给 Anthropic API → 400 unknown variant。
- **B站兴趣关键词配置化**（`plugins/bilibili.py` + `plugins/bilibili.toml`）：`_HIGH_INTEREST`/`_MEDIUM_INTEREST`/`_LOW_INTEREST` 关键词列表和 `_INTEREST_LLM_FALLBACK` 阈值从模块级常量迁移至 TOML 配置文件。`BilibiliConfig` 新增 4 个字段，`evaluate_interest()` 支持参数传入关键词，`on_startup` 从配置读取。修改关键词或阈值只需 `restart`，无需 rebuild。

**影响范围**：`config/config.toml`、`services/llm/client.py`、`plugins/bilibili.py`、`plugins/bilibili.toml`

**验证**：rebuild 后启动正常，Thinker 过滤逻辑生效

**回滚方案**：`[thinker].enabled = false` 关闭 Thinker；git revert 恢复关键词硬编码

---

## 2026-05-03 多阶段流水线架构 — Phase 1-4 实施

**变更类型**：enhancement

**内容**：
- **Phase 1 — 接线 Thinker**：
  - `services/llm/client.py`: `chat()` 中主 LLM 调用前先调 `think()` (wait → return None; reply → 注入 thought 到 system prompt)
  - 新增 `_build_thinker_mood_text()` / `_build_thinker_affection_text()` 辅助方法
  - `__init__` 新增 `mood_getter` 参数
  - `ReplyContext` 填充 `thinker_action` / `thinker_thought`
  - `plugins/chat.py`: 传递 `mood_getter` lambda 给 LLMClient
- **Phase 2 — sticker 决策移入 Thinker**：
  - `services/llm/thinker.py`: `ThinkDecision` 新增 `sticker: bool` 和 `tone: str` 字段
  - thinker prompt 新增表情包决策和语气决策指令
  - `parse_think_output()` 解析新字段
  - `client.py`: thinker block 注入 `sticker: yes/no` 和 `tone: 元气/日常/安慰/认真` 指令
- **Phase 3 — instruction.md 重排序**：
  - 新增「底线规则速查」放在文件最前（长度控制、禁止括号、sticker 后规则、禁用 Markdown）
  - 表情包章节前移（原在末尾）
  - 记忆系统、工具使用移至末尾（有工具定义辅助）
- **Phase 4 — `_clean_reply()` 增强**：
  - 新增 `_STICKER_NARRATION_RE` 匹配"已发送表情包""表情包补上啦""表情包来啦"等
  - `_STAGE_ACTION_CHARS` 扩展 "发送补"
  - `_clean_reply()` 增加空行/纯叙述行过滤

**影响范围**：回复生成全流程

**回滚方案**：git revert

---

## 2026-05-03 DeepSeek thinking blocks 400 修复（回归）

**变更类型**：bugfix

**内容**：
- `plugins/dream.py`: Dream Agent 工具循环中未保留 thinking_blocks → API 400
- `services/llm/client.py`: Compaction 工具循环中未保留 thinking_blocks → API 400
- 修复方式：两处 assistant_content 构建前追加 `result.get("thinking_blocks", [])`
- 主聊天流程（`client.py:1217-1225`）已正确保留，本次补全其余两个 API 调用路径
- 根因：DeepSeek thinking mode 要求 thinking blocks（含 signature）必须在后续请求中原样回传

**影响范围**：Dream Agent 第二轮起、compaction 有工具调用时 → 400 错误中断

**回滚方案**：git revert

---

## 2026-05-03 B站兴趣评分强化 + 调度器阈值联动修复

**变更类型**：bugfix

**内容**：
- **兴趣关键词扩展**（`plugins/bilibili.py`）：
  - `_HIGH_INTEREST` 新增 Project Sekai 全组/角色：25时、nightcord、ニーゴ、25ji、vivid bad squad、vbs、ビビバス、more more jump、mmj、モモジャン、leo/need、レオニ、各角色名（宵崎、朝比奈、東雲、花里、白石 等）、rin/len/luka/リン/レン/ルカ、缤纷舞台、プロジェクトセカイ、mmd、3d、blender
  - `_MEDIUM_INTEREST` 新增：手书、手描き、描いてみた
- **LLM 评估门槛与合并策略修正**：`_INTEREST_LLM_FALLBACK` 0.2→0.6；LLM 评分与关键词评分取 max（`interest = max(interest, llm_score)`）而非替换
  - 根因：添加更多关键词后关键词评分反而可能低于纯 LLM 评估（0.85），旧代码用 LLM 分直接覆盖关键词分导致退步
- **调度器兴趣公式改为混合下限**（`services/scheduler.py`）：`threshold *= interest_score` → `threshold = base_talk_value * (0.3 + 0.7 * interest_score)`
  - 效果：0.05→0.335、0.55→0.685、0.85→0.895、1.0→1.0，高兴趣分不再被乘法过度放大
- **高兴趣视频免时段抑制**：interest ≥0.6 时 `time_mult = 1.0`，bot 对其真正关心的内容无论时段都回复

**影响范围**：`plugins/bilibili.py`、`services/scheduler.py`

**验证**：659 passed, 8 预存失败，零回归

**回滚方案**：`git revert` 即可

---

## 2026-05-03 B站小程序卡片复读误触发修复

**变更类型**：bugfix

**内容**：
- `plugins/echo.py`：`build_echo_key()` 新增 `json` 类型 segment 处理，从 JSON data 中提取 `prompt`/`desc` 字段生成差异化 key
  - 根因：多个不同 B站 mini-program 转发卡片的 segment type 均为 `json`，旧代码只生成 `[json]` 固定 key → 即使内容不同的视频也被识别为"同一条消息"→ 第三个转发即触发复读
  - 修复后：`[json:视频标题/prompt 文本]`，不同视频的卡片各自独立
- JSON 解析异常保护：空字符串、无效 JSON 均静默回退

**影响范围**：`plugins/echo.py`

**验证**：659 passed, 8 预存失败，零回归

**回滚方案**：`git revert` 即可

---

## 2026-05-03 多阶段流水线架构方案（调研文档）

**变更类型**：docs

**内容**：
- 新建 `docs/superpowers/plans/2026-05-03-multi-stage-pipeline.md`
- 归档四阶段实施建议：接线 Thinker → sticker 决策移入 Thinker → instruction.md 重排序 → 后处理增强
- 未改代码，仅调研结论与方案

---

## 2026-05-03 回复质量修复：禁止括号动作描述 + 禁止提及表情包发送

**变更类型**：bugfix

**内容**：
- **指令强化**（`config/soul/instruction.md`）：
  - send_sticker 规则扩展：明确列出禁止的表述（"（已发送表情包）""表情包补上啦"等），规定发送后若无话可说就 pass_turn
  - 新增"括号动作描述"禁令："（揉眼睛）""（好困）""（笑瘫.jpg）""（身体好沉）"等括号舞台提示一律禁止；状态通过语气传达，不通过括号里的字面描述
- **代码安全网**（`services/llm/client.py`）：
  - 新增 `_strip_stage_direction()` 函数：用正则匹配并移除中文括号（）+半角括号()中的动作/状态描述
  - 新增 `_clean_reply()` 统一清洗管线：markdown strip + stage direction strip
  - 所有回复路径（`if not tool_uses` 和 `tool loop exhausted`）均改用 `_clean_reply`
  - 正则区分动作括号与颜文字（(≧▽≦)、(◕‿◕)、(｡･ω･｡) 保留不删）；区分动作括号与自然语中括号（"我姐姐（就是上次那个）" 保留不删）
  - 动作关键词覆盖：困累饿躺趴揉打眨伸爬走跑跳坐站睡抱推拉哭笑叹捂挥滚晃闹踢踹蹲跪等

**影响范围**：`config/soul/instruction.md`、`services/llm/client.py`

**验证**：16 条测试用例全部通过（动作括号正确移除、颜文字保留、自然语括号保留）；659 passed, 8 预存失败

**回滚方案**：`git revert` 即可

---

## 2026-05-03 B站搜索误匹配修复

**变更类型**：bugfix

**内容**：
- **搜索不再盲取第一项**（`plugins/bilibili.py`）：`_search_video()` 改取前10条结果，用 `_title_match_score()` 逐条评分后选最高分
  - 根因：QQ 小程序卡片分享 B站视频时只有标题没有 BV ID，bot 通过搜索匹配。对含常见词的短标题（如"遮阳伞汽水"），B站搜索把练习室镜面排在第1、原曲排在第2，盲取 `items[0]` 导致错误匹配
- **评分函数 `_title_match_score()`**：关键词子串匹配基础分 + 练习/教程信号惩罚（自用、镜面、扒舞、喊拍等 -0.15）+ 原曲信号奖励（pjsk、mmj、live、MV 等 +0.05）
- **JSON 卡片额外提取 URL**：`_extract_bilibili_json_info()` 新增提取 `url`/`qqdocurl` 字段，可直接解析 BV ID 跳过搜索
- **新增 `_resolve_urls_to_vid()`**：从 URL 解析 b23.tv 短链或完整 bilibili 链接

**影响范围**：`plugins/bilibili.py`

**验证**：遮阳伞汽水搜索 5 条结果中 MMJ 原曲 0.876 > 练习室镜面 0.436；659 passed, 8 预存失败

**回滚方案**：`git revert` 即可

---

## 2026-05-03 网页搜索修复：DuckDuckGo 更换包 + 新增 Bing API

**变更类型**：bugfix

**内容**：
- **更换搜索后端**（`services/tools/web_search.py`）：重写为双后端架构
  - 主后端：Bing Web Search API（设置 `SEARCH_API_KEY` 环境变量时启用，返回 JSON，稳定可靠）
  - 回退：DuckDuckGo（`ddgs` 包 v9.14.1，已在 Docker 容器验证可用）
  - 根因：`duckduckgo-search` v8.1.1 对数据中心 IP 返回空结果（DDG 反爬拦截），且该包已更名为 `ddgs`
- **依赖更新**（`pyproject.toml`）：`duckduckgo-search>=8.1.1` → `ddgs>=9.0.0`
- **配置**（`kernel/config.py`）：`_ENV_MAP` 新增 `SEARCH_API_KEY`

**影响范围**：`services/tools/web_search.py`、`pyproject.toml`、`kernel/config.py`

**验证**：Docker 内中英文查询均返回正确结果；659 passed, 8 预存失败

**回滚方案**：`git revert` 即可

---

## 2026-05-03 Memo 组件修复：user_msg 传递 + 缓存失效 + Dream 首轮

**变更类型**：bugfix

**内容**：
- **`user_msg` 传递**（`kernel/types.py`）：`ReplyContext` 新增 `user_msg: str = ""` 字段；`client.py` 两处 `fire_on_post_reply` 构造点传入 `content_text(user_content)`；`memo.py` 使用 `ctx.user_msg` 替代硬编码 `""`
  - 根因：MemoExtractor 只能看到 bot 回复，看不到用户说了什么（"用户: \n助手: ..."），提取质量严重受损
- **提取后缓存失效**（`plugins/memo.py`）：`on_post_reply` 的 done callback 中清空 `_index_cache` + 调用 `_retrieval.invalidate_entity(scope, scope_id)`
  - 根因：新卡片写入后 RetrievalGate 返回陈旧内容，最多 5 分钟 TTL 到期才恢复
- **删除死代码**（`plugins/memo.py`）：移除 `_content_text()`（~11行，从未被调用）
- **DreamAgent 首轮立即执行**（`plugins/dream.py`）：`_loop()` 在 `while True` sleep 前先 `await self._run()`
  - 根因：默认 interval_hours=24 时需要等 24 小时才首次整理，改为启动即运行

**影响范围**：`kernel/types.py`、`services/llm/client.py`、`plugins/memo.py`、`plugins/dream.py`

**验证**：659 passed, 8 预存失败，零回归

**回滚方案**：`git revert` 即可

---

## 2026-05-03 颜文字强制表情包功能修复

**变更类型**：bugfix

**内容**：
- `services/llm/client.py`：恢复 kaomoji→sticker 强制执行逻辑（~20行），在 `if not tool_uses:` 块中检测颜文字后注入强制 sticker 轮次
  - 根因：2026-05-03"移除独立 Thinker + 合并 Sticker 强制执行"重构中删除了 ~23 行 enforcement 代码，`_text_has_kaomoji()` 定义但从未调用
- 版本：bot 1.2.1 → 1.2.2，chat 插件 1.1.4 → 1.1.5，sticker 插件 1.1.1 → 1.1.2

**影响范围**：`services/llm/client.py`、`plugins/chat.py`、`plugins/sticker.py`、`pyproject.toml`

**验证**：659 passed, 8 预存失败，零回归

**回滚方案**：`git revert` 即可

---

## 2026-05-03 Dockerfile 构建源修改 + Apple Double 防护

**变更类型**：infra

**内容**：
- **Dockerfile**：`COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv` → `RUN pip install uv`
  - 根因：ghcr.io 在国内网络不可达（`i/o timeout`），改用 PyPI 安装 uv
- **Apple Double 防护**：
  - `~/.zshrc`：新增 `export COPYFILE_DISABLE=1`，阻止 macOS 在非 APFS 卷上生成 `._*` 资源分支文件
  - `CLAUDE.md`：rebuild 命令改为 `dot_clean . && docker compose up bot -d --build`

**影响范围**：`Dockerfile`、`~/.zshrc`、`CLAUDE.md`

**回滚方案**：恢复 Dockerfile 原 `COPY --from` 行；取消 `COPYFILE_DISABLE` 环境变量

---

## 2026-05-03 句尾从句标点剥离修复

**变更类型**：bugfix

**内容**：
- `_split_naturally()` 在 `_smart_chunk` 切分后新增从句标点剥离：`c.rstrip(_TRAILING_CLAUSE)` — 移除段尾的 `，；：、,;:`
- 根因：`_smart_chunk` 在从句标点处切分时，标点留在前一段末尾（如"虽然我主要玩烤和邦邦，"），导致这条独立 QQ 消息末尾挂着无意义的连接符
- 句末标点（`。！？～`）保留——它们承载语气信息；从句标点（`，；：、`）仅在连续文本中有连接作用，独立成段时剥离
- 测试更新：`test_mid_sentence_merge` 断言 `result[0] == "恋爱捉迷藏配上AI修复"`（移除末尾逗号）

**影响范围**：`services/llm/client.py`（新增 `_TRAILING_CLAUSE` 常量 + 一行 rstrip）、`tests/test_client.py`（调整 1 个断言）

**验证**：659 passed, 8 预存失败（libvips + sticker），零回归

**回滚方案**：`git revert` 即可

---

## 2026-05-03 文本分段算法重写：回溯式标点优先级切分

**变更类型**：refactor + bugfix

**内容**：
- **算法重写**（`services/llm/client.py`）：删除 `_split_on_sentence_end` + `_split_long_on_comma`（~50行），替换为 `_smart_chunk`（~55行）——回溯式标点优先级切分
  - 优先级1：在 `。！？～…` 句末标点后切分
  - 优先级2：在 `，；：、` 从句边界后切分
  - 优先级3：中文字符边界（保护英文单词完整性，不撕开 "AI" 等）
  - 优先级4：硬切（最后手段，基本不触发）
  - 标点留在段尾而非推到段首
  - 内置尾段合并（< `_MIN_CHUNK` = 6 的尾段合并到前一段）
- **`～` 升级为句末标点**：从仅用于 `\n` 合并判断升级为一级切分点，与 `。！？` 同级
- **`/debug split` 误输入保护**（`plugins/chat.py`）：`_handle_debug` 现在检测纯 ASCII 小写首词是否为已知子命令，否则提示可用子命令而非送 LLM
- **测试**：`tests/test_client.py` 新增 4 个测试（段首无标点、英文完整性、尾段合并、精确回归），共 13 个 split 测试
- **版本**：bot 1.2.0 → 1.2.1，chat 插件 1.1.3 → 1.1.4

**影响范围**：`services/llm/client.py`、`plugins/chat.py`、`tests/test_client.py`、`pyproject.toml`

**验证**：659 passed, 8 预存失败（libvips + sticker），零回归

**回滚方案**：`git revert` 即可

---

## 2026-05-03 文本分段修复：句中断行合并 + /debug split 子命令

**变更类型**：bugfix + feature

**内容**：
- **句中断行合并**（`services/llm/client.py`）：新增 `_SENTENCE_ENDING` 字符集（`。！？～…」』）\"!?~)`），`\n` 从硬分段边界降级为软提示——仅当上一行末尾有句末标点时才切分，句内换行直接合并。修复「感觉像在看超高清\n的童话舞台剧！」被切成孤儿碎片的问题
- **`_MIN_CHUNK` 提升**：3 → 6，避免 4-5 字短片段逃脱合并逻辑
- **超长句语义切分**：`_split_on_sentence_end` 的硬字符切分（`chunk[i:i+MAX]`）替换为 `_split_long_on_comma`（逗号层级语义切分），避免把合并后的完整句子重新撕成碎片
- **指令更新**（`config/soul/instruction.md`）：分段指导从「换行即分段」改为「一个完整想法写完后再换行，不要在句子中途强行换行」
- **`/debug split` 子命令**（`plugins/chat.py`）：新增 `_handle_debug_split` handler，实时测试 `_split_naturally()` 分段效果，别名 `/debug 分段`/`/debug 分割`
- **测试**：`tests/test_client.py` 新增 `TestSplitNaturally`（9 个测试），覆盖句中断行合并、句末标点切分、`---cut---` 分隔符、长句语义切分、`_MIN_CHUNK` 合并等场景
- **版本**：bot 1.1.1 → 1.2.0

**影响范围**：`services/llm/client.py`、`config/soul/instruction.md`、`plugins/chat.py`、`tests/test_client.py`、`pyproject.toml`

**回滚方案**：`git revert` 即可

---

## 2026-05-03 B站插件回复模式 + HTML 标签修复 + 兴趣评估

**变更类型**：feature + bugfix

**内容**：
- 新增 4 种视频回复模式（`plugins/bilibili.toml` → `reply_mode`）：
  - `mood`（默认）：跟随主 bot 心情/时段概率，不改 scheduler 行为
  - `always`：检测到视频即强制回复，绕过 proactive/at_only/概率/interval 全部限制
  - `dedicated`：使用独立概率 `bilibili_talk_value`（默认 0.8），心情/时段乘数照常
  - `autonomous`：在 dedicated 基础上 × 兴趣分（关键词匹配 bot 人设，高分视频回复率更高）
- 新增 `evaluate_interest()` 函数：三级关键词表（高/中/低权重），匹配视频标题计算 0-1 兴趣分
- 数据流：bilibili.on_message() → raw_message["_bilibili_reply"] hint → router 提取 → scheduler.notify(video_hint=...)
- Scheduler 适配：notify() 新增可选 video_hint 参数，"always" 模式类似 @ 直接 fire
- 修复 B 站搜索 API 返回 HTML 标签（`<em class="keyword">`）导致 loguru 格式解析崩溃
- 修复 plain_text 被覆盖导致用户原文丢失
- 新增 15 个测试（7 兴趣评估 + 4 reply hint + 9 scheduler 集成），总计 88 passed

**影响范围**：`plugins/bilibili.py`、`plugins/bilibili.toml`、`services/scheduler.py`、`kernel/router.py`、`tests/test_bilibili.py`、`tests/test_scheduler.py`

**回滚方案**：`reply_mode = "mood"` 即恢复原行为

---

## 2026-05-03 B站视频链接识别插件

**变更类型**：新插件

**内容**：
- 新增 `plugins/bilibili.py`：BilibiliPlugin（priority=190），在消息管线中拦截B站视频链接并注入视频摘要
- 识别格式：BV号、av号、b23.tv短链接、bilibili.com/video/ 完整链接、番剧ep/ss链接
- b23.tv短链接自动跟随HTTP重定向解析真实URL
- 通过 bilibili-api-python 获取视频信息（标题、时长、播放量、UP主、简介、分区）
- 封面图下载后通过 Qwen VL (VisionClient) 描述画面内容
- 摘要注入到消息segments中，`_render_message` 自然包含视频上下文
- 本地缓存视频信息（默认3600秒），避免重复API请求
- 新增配置 `plugins/bilibili.toml`：enabled / cache_ttl / cover_timeout
- 新增依赖 `bilibili-api-python>=17.0.0`
- 新增33个单元测试（URL匹配、摘要格式、插件集成、缓存、降级处理）

**影响范围**：
- 群聊中发送B站视频链接时，bot能理解视频内容再回复
- API故障或封面下载失败时静默降级，不阻断消息流
- 返回 False 不消费消息，正常流继续走 scheduler

**回滚方案**：将 `plugins/bilibili.toml` 中 `enabled = false` 即可禁用插件

## 2026-05-03 — 调度器日志可见性修复 + 心情缓存修复

- **类型**：bugfix
- **操作人**：Claude Code (assisted)
- **问题**：
  1. scheduler 频道的 skip 决策日志（prob skip / interval too short / at_only 等）使用 `logger.debug()`，被 NoneBot 的 `default_filter`（默认只放行 INFO+）拦截，开启 `scheduler = true` 后仍不可见
  2. `mood_getter` lambda 只读 `mood_engine._cache`，重启后首次聊天触发前缓存为空 → 心情乘数始终 1.0，心情系统未实际介入概率调度
- **修复**：
  - `services/scheduler.py`：5 处调度决策日志从 `_L.debug()` 提升为 `_L.info()`
  - `plugins/chat.py`：`mood_getter` lambda 改为主动调用 `mood_engine.evaluate(schedule)`，确保首次访问即计算心情（evaluate 自带 15 分钟缓存）
- **影响范围**：`services/scheduler.py`、`plugins/chat.py`
- **测试**：ruff 通过，26/26 scheduler 测试通过
- **回滚**：`git revert` 即可

---

## 2026-05-03 — 群聊延迟优化：概率调度 + 移除独立 Thinker + 合并 Sticker 强制执行

- **类型**：performance + refactor
- **操作人**：Claude Code (assisted)
- **背景**：群聊回复链路耗时 17-22s，根因三步串行 LLM 调用（Thinker ~3s + 主回复 ~3-5s + Sticker 强制执行 ~5s）+ 固定 debounce 5s。参考 MaiBot 的概率调度设计。
- **变更内容**：
  - **概率调度替代 debounce**（`services/scheduler.py`）：非@消息不再每条触发，改为 `talk_value` 概率（默认 0.3=30%）+ `planner_smooth` 最小间隔（默认 3s）。连续跳过 3 次后阈值翻倍，5 次后强制回复。删除 `_debounce()` 方法。
  - **移除独立 Thinker**（`services/llm/client.py`）：删除 ~55 行 think() 调用块。原 Thinker 的 reply/wait/search 决策由 LLM 工具调用（`pass_turn`）自然接管。`ThinkerConfig.enabled` 默认值改为 `False`。`services/llm/thinker.py` 文件保留（向后兼容）。
  - **合并 Sticker 强制执行**（`services/llm/client.py`）：删除 ~23 行 kaomoji 检测 + 强制 sticker 轮次。LLM 可在主回复轮次中自然调用 `send_sticker`。
  - **配置层**（`kernel/config.py`）：`GroupConfig`/`GroupOverride`/`ResolvedGroupConfig` 新增 `talk_value`、`planner_smooth` 字段。`config.example.toml` 同步更新。
- **版本**：bot 1.1.0→1.1.1，chat 插件 1.1.2→1.1.3
- **效果**：延迟从 17-22s 降至 ~3-5s；非@消息回复频率大幅降低，减少无效插话。
- **测试**：21 个 scheduler 测试重写适配新调度逻辑，ruff 通过，pyright 无新增错误
- **回滚**：`git revert` 即可

---

## 2026-05-03 — 心情系统 × 概率调度联动

- **类型**：feature
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `services/scheduler.py`：新增 `mood_getter` 回调参数（可选），新增 `_get_mood_multiplier()` 方法，用心情三维度（valence 正负面、energy 精力、openness 开放度）计算 talk_value 乘数（范围 [0.25, 2.0]）。好心情更爱插话，坏心情更沉默。@ 消息和管理员命令不受影响。
  - `plugins/chat.py`：创建 scheduler 时注入 mood_getter lambda，从 `ctx.mood_engine._cache` 读取当前心情。
  - 心情乘数公式：`mood_factor = 0.4×openness + 0.3×energy + 0.3×(valence+1)/2`，`mult = 0.25 + 1.75×mood_factor`
- **版本**：bot 1.1.0→1.1.1，chat 插件 1.1.2→1.1.3
- **影响范围**：`services/scheduler.py`、`plugins/chat.py`、`tests/test_scheduler.py`（+5 个心情测试，共 26 个）
- **测试**：ruff check 通过，26/26 scheduler 测试通过，582/590 全量（8 个预存失败与本次无关）
- **回滚**：`git revert` 即可

---

## 2026-05-03 — 多级命令支持 (sub-commands)

- **类型**：feature
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 框架层：`Command` 数据类新增 `sub_commands: list[Command]` 字段，`CommandDispatcher.dispatch()` 支持递归子命令匹配，未命中子命令时回退到父 handler
  - `/debug` 注册 `save`（别名: 保存/收录/添加表情）和 `send`（别名: 发/发送）两个子命令，替代原有 ad-hoc 关键词匹配
  - 修复 debug 模式空文本回退 "…" 问题，增加 debug 回复日志
- **版本**：bot 1.0.7→1.1.0，chat 插件 1.1.1→1.1.2
- **影响范围**：`kernel/types.py`、`services/command.py`、`plugins/chat.py`
- **回滚**：`git revert` 即可

---

## 2026-05-02 — 插件配置迁移至插件目录

- **类型**：重构
- **操作人**：Claude Code (assisted)
- **变更内容**：将 6 个插件的配置从中央 `config.toml` 迁移至各插件目录下的同名 `.toml` 文件（`plugins/<name>.toml`）。Config Pydantic 模型同时从 `kernel/config.py` 搬至插件 `.py` 文件，新增 `load_plugin_config()` 工具函数统一加载。ChatPlugin 现在从插件 TOML 读取配置创建服务对象。
- **迁移清单**：sticker、memo、schedule、affection、dream、element_detection
- **影响范围**：
  - 新增 6 个 `plugins/*.toml`
  - 修改：`kernel/config.py`、`kernel/__init__.py`、`plugins/chat.py`、`bot.py`、`config.example.toml`、`config/config.toml`
  - 修改 6 个插件 `.py` 文件（新增 Config 模型 + 更新 on_startup）
- **回滚**：`git revert` 即可，注意恢复后需同步 `config.toml` 中对应段落

---

## 2026-05-02 — 启用要素察觉 + 修复 identity 引用

- **类型**：bugfix + feature enablement
- **操作人**：Claude Code (assisted)
- **问题与根因**：`ElementDetectorPlugin` 从未触发，因为 `config.toml` 和 `config.example.toml` 均无 `[element_detection]` 段落，`rules` 为空导致 `on_startup` 中 `self._detector = None`。此外 `element_detector.py:79` 引用了不存在的 `ctx.identity_mgr`（应为 `ctx.identity`）。
- **修复**：
  - `config.example.toml`：新增 `[element_detection]` 段落，含 2 条示例规则（感叹词检测 + 番剧询问检测）
  - `config/config.toml`：同上
  - `plugins/element_detector.py`：`ctx.identity_mgr` → `ctx.identity`，移除不必要的 `.resolve()` 调用
- **影响范围**：`config.example.toml`、`config/config.toml`、`plugins/element_detector.py`
- **测试**：ruff check 通过，9 个 element_detector 测试全过，启动日志确认 `element detection enabled | rules=2`
- **回滚**：git revert 即可

---

## 2026-05-02 — 补全 NoneBot NICKNAME 配置，修复适配器层昵称检测

- **类型**：bugfix (配置缺陷)
- **操作人**：Claude Code (assisted)
- **问题与根因**：`config/.env` 缺少 `NICKNAME`（NoneBot 标准配置键），导致适配器层 `_check_nickname()` 永远不触发。虽然 `router.py` 中有自定义 `BOT_NICKNAMES` 匹配（任意位置皆可检测），但 NoneBot 层的昵称剥离和 `to_me` 标记完全缺失。
- **修复**：
  - `config/.env`：新增 `NICKNAME` 行，值与 `BOT_NICKNAMES` 保持一致
  - `plugins/chat.py`：优先从 `nonebot.get_driver().config.nickname` 读取昵称列表，`BOT_NICKNAMES` 仅作 fallback
- **影响范围**：`config/.env`、`plugins/chat.py`
- **测试**：ruff check 通过，pytest 通过
- **回滚**：git revert 即可

---

## 2026-05-02 — 移除重启后自动触发群聊回复

- **类型**：bugfix
- **操作人**：Claude Code (assisted)
- **问题与根因**：每次 bot 容器重启后，历史加载器回填近期消息到 timeline，随后 `router.py` 调用 `scheduler.trigger()` 强制触发一次回复。由于每次加载的近期历史相同，LLM 收到相同上下文后产生同话题重复发言。多次重载 → 多次重复。
- **修复**：移除 `kernel/router.py` 中 `is_first_connect` 后的 `scheduler.trigger()` 调用。Bot 重启后静默加载历史作为上下文，等待新的群消息（@/debounce/batch）自然触发回复。
- **影响范围**：`kernel/router.py`（移除 5 行）
- **测试**：ruff check 通过，pytest 通过（20 个 scheduler 测试全过，预存失败与本次无关）
- **回滚**：git revert 即可

---

## 2026-05-02 — 好感度群聊归因修复：调度器传入 user_id

- **类型**：bugfix
- **操作人**：Claude Code (assisted)
- **问题与根因**：群聊中好感度始终为 0。`GroupChatScheduler._do_chat()` 硬编码 `user_id=""`，导致好感度引擎无法将互动归因到任何用户。
- **修复**：
  - `services/scheduler.py`：`_GroupSlot` 新增 `last_user_id` 字段，`notify()` 接收并存储 `user_id`，`_do_chat()` 使用存储的 user_id 而非空字符串
  - `kernel/router.py`：两处 `scheduler.notify()` 调用传入 `user_id=str(event.user_id)`
- **影响范围**：`services/scheduler.py`、`kernel/router.py`、`plugins/affection/plugin.py`（1.0.1→1.0.2）
- **验证**：ruff check 通过，pytest 通过（预存失败与本次无关）
- **回滚**：git revert 即可

---

## 2026-05-02 — 调试保存表情包三项修复：mface 识别、Qwen VL 限流、历史加载干扰

- **类型**：bugfix
- **操作人**：Claude Code (assisted)
- **问题与根因**：
  1. **mface（QQ 商城表情）无法识别**：NoneBot OneBot v11 适配器不识别 NapCat 的 `mface` 段类型，可能将其转为纯文本（如 `[星星眼]`），导致 `seg.type == "mface"` 永远不匹配。只能收到 3 张普通图片。
  2. **Qwen VL 连续调用失败**：`/debug 保存四张表情` 触发 4 次连续 vision API 调用，硅基流动对快速连续请求限流，第 2-3 次调用超时返回空错误。
  3. **Bot 重启后历史加载触发正常回复**：历史加载器回填旧消息（含之前的 `/debug` 输出文本）进入 timeline，触发 thinker/chat 流程产生多余回复。
- **修复**：
  - `plugins/chat.py`：mface 检测增加 `market_face` 类型 + 从 `event.raw_message` 解析 `[mface:...]`/`[market_face:...]` CQ 码兜底；增加 segment 类型扫描日志；连续 vision 调用间增加 1.5s 延迟避免限流
  - `plugins/history_loader.py`：新增 `_contains_debug_command()` 跳过含 `/debug` 的历史消息
  - `services/media/vision.py`：错误日志增加异常类型名，不再只显示空字符串
  - `bot.py`：VisionClient timeout 10s → 15s
- **影响范围**：`plugins/chat.py`（1.0.3→1.0.4）、`plugins/history_loader.py`（1.0.0→1.0.1）、`services/media/vision.py`、`bot.py`
- **测试**：ruff check 通过，pytest 通过（预存失败与本次无关）
- **回滚**：git revert 即可

## 2026-05-02 — 图像描述提升至系统层 + 接入硅基流动 Qwen3-VL

- **类型**：enhancement (架构变更)
- **操作人**：Claude Code (assisted)
- **变更内容**：
  1. **VisionPlugin 删除**：`plugins/vision.py` → `services/media/vision.py`（系统服务层，与 image_cache 同级）。VisionClient 现在由 `bot.py` 在启动时根据 `api_key` 是否填写自动初始化，不在插件总线中注册。
  2. **配置简化**：`QwenVLConfig` 移除 `enabled` 字段——api_key 非空即启用，留空即关闭。无需额外开关。
  3. **接入硅基流动 VLM**：`config.toml` 配置 `Qwen/Qwen3-VL-30B-A3B-Instruct` 模型（API: siliconflow.cn），DeepSeek V4 本身不支持多模态，现在由 Qwen VL 先描述图片再传给主模型。
  4. **StickerPlugin 1.0.1 → 1.0.2**：`format_prompt_view()` 新增 `[动图]`/`[静态]` 格式标签，摘除未使用的 loguru 导入。
  5. **config.example.toml**：补上 `[vision.qwen]` 示例段落。
- **影响范围**：`services/media/vision.py`（新）、`plugins/vision.py`（删）、`bot.py`、`kernel/config.py`、`config.example.toml`、`plugins/sticker.py`、`config/config.toml`
- **版本**：bot 1.0.4 → 1.0.5
- **测试**：ruff check 通过，pytest 通过（8 个预存失败与本次无关）
- **回滚**：git revert + 恢复 `config.toml` 的 `[vision.qwen]` 为空值

## 2026-05-02 — 表情包发送全线修复：sub_type 蛇形命名 + /debug 直接调度 + 指令防抖取消

- **类型**：bugfix (critical) + enhancement
- **操作人**：Claude Code (assisted)
- **问题与根因**：
  1. **表情包发送全线失败（retcode=1200）**：`SendStickerTool` 使用驼峰 `subType=1` 设置 QQ 贴图类型，但 OneBot v11 协议要求蛇形 `sub_type`。NapCat 静默忽略未知 key，导致表情包始终作为普通图片发送。
  2. **Docker 容器文件系统隔离**：bot 和 napcat 在不同容器，napcat 无法读取 bot 的 `/app/storage/` 路径。改为 base64 编码内联传输。
  3. **`/debug` 指令触发 LLM 工具循环但 DeepSeek V4 幻觉**：LLM 不认识 `send_sticker`，总是返回 `pass_turn`。新增直接调度路径绕过 LLM。
  4. **指令被复读插件检测**：`EchoPlugin` 未过滤 `/` 开头的消息。
  5. **`/debug` 处理期间 thinker 仍触发**：上一条消息的 debounce 计时器在 `/debug` 到达时已启动，到期后 thinker 照常运行。
- **修复**：
  - `services/tools/sticker_tools.py`：`subType` → `sub_type`（蛇形命名）+ `summary=[动画表情]` + base64 编码 + 异常保护 record_send
  - `plugins/chat.py`：新增 `_debug_direct_dispatch()` 直接实例化 `SendStickerTool` 绕过 LLM；关键词从 `startswith` 改为 `in` 匹配，覆盖 gif/动图/贴图 等
  - `plugins/echo.py`：跳过以 `/` 开头的消息
  - `kernel/router.py`：命令匹配成功后调用 `ctx.scheduler.cancel_debounce(group_id)` 取消待处理的 thinker 触发
  - 参考旧项目 `amadeus-in-shell` 确认正确模式为 `sub_type=1` + `summary=[动画表情]`
- **影响范围**：`services/tools/sticker_tools.py`、`plugins/chat.py`、`plugins/echo.py`、`kernel/router.py`
- **测试**：`tests/test_sticker_tools.py` 更新以验证 `sub_type` 和 `summary` 设置；ruff check + pytest 通过（9 个预存失败与本次无关）
- **回滚**：git revert 即可

## 2026-05-02 — 表情包强制执行循环修复

- **类型**：bugfix
- **操作人**：Claude Code (assisted)
- **根因**：`chat()` 中颜文字→表情包强制执行逻辑（line 1135）在 LLM 不调用 `send_sticker` 时反复触发。`_sticker_sent` 始终为 False，每轮都检测到 kaomoji → 强制执行 → LLM 仍不发 → 再强制执行，直到 MAX_TOOL_ROUNDS=5 耗尽。日志中出现 4 次连续 enforcement 事件。
- **修复**：强制执行一次后立即设置 `_sticker_sent = True`，阻止后续轮次再次触发。
- **附加**：StickerPlugin 版本 1.0.0 → 1.0.1
- **影响范围**：`services/llm/client.py`（一行）、`plugins/sticker.py`（版本号）
- **回滚**：git revert 即可

## 2026-05-02 — /debug 模式重构：支持工具执行

- **类型**：enhancement
- **操作人**：Claude Code (assisted)
- **问题**：`/debug` 调用 `_call()` 裸 API，无工具循环，导致 `send_sticker` 等工具无法执行。用户无法用 `/debug` 调试表情包等功能。
- **修复**：重写 `_handle_debug` 为完整工具循环（镜像 `chat()` 的工具执行逻辑），LLM 可调用任何已注册工具。同时明确 system 指令："你是调试助手，直接执行用户的指令"。
- **变更**：
  - `plugins/chat.py`：`_handle_debug` 从单轮 `_call()` 改为工具循环（最多5轮），支持 `pass_turn`、`send_sticker` 等全部工具
  - 新增 imports: `asyncio`, `json`, `_PASS_TURN_TOOL`, `_strip_markdown`, `_to_anthropic_tools`, `ToolContext`
  - ChatPlugin 版本 1.0.2 → 1.0.3
- **影响范围**：`plugins/chat.py`
- **回滚**：git revert 即可

---

## 2026-05-02 — 分段根本修复：force_reply 绕过分段逻辑

- **类型**：bugfix (critical)
- **操作人**：Claude Code (assisted)
- **根因**：`chat()` 方法中 `force_reply=True` 时完全跳过 `_split_naturally()`，直接 `segments=[reply]`。而 scheduler 对所有 `is_at=True` 的消息（@提及、昵称称呼）都设 `force_reply=True`，导致这些消息永远不分段发送。
- **修复**：移除两处 `force_reply` 的分段绕过（工具循环内 + 工具循环耗尽后），所有回复统一走 `_split_naturally`。`force_reply` 现在仅跳过 thinker（在 line 1041 处理），不再影响分段。
- **附加**：ChatPlugin 版本 1.0.1 → 1.0.2
- **影响范围**：`services/llm/client.py`（两处 force_reply 分支移除）、`plugins/chat.py`（版本号）
- **测试**：ruff check 通过，pytest 577/587 通过（10 个预存失败与本次无关）
- **回滚**：git revert 即可

## 2026-05-02 — 日志频道恢复 + 分段修复 + 插件日志增强

- **类型**：bugfix + enhancement
- **操作人**：Claude Code (assisted)
- **变更**：
  - **日志频道默认值**：`LogChannelConfig.system` 默认改为 `True`（多个插件使用 system 频道输出必要信息）
  - **config.toml 频道开关**：启用 message_in、message_out、thinking、mood、affection、schedule、system（之前全部为 false 导致日志全被过滤）
  - **好感度插件日志**：`AffectionPlugin` 新增 INFO 级别日志（on_pre_prompt 记录用户好感度层级和分数，on_post_reply 记录互动后的分数变化），版本 1.0.0 → 1.0.1
  - **记忆提取器日志**：`MemoPlugin` 卡片提取成功日志从 DEBUG 提升到 INFO，版本 1.0.0 → 1.0.1
  - **日程插件**：版本 1.0.0 → 1.0.1
  - **ChatPlugin**：补上版本号 1.0.1
  - **分段修复（services/llm/client.py）**：
    - `_split_on_sentence_end` 增加硬字符上限强制切分，防止无标点长句成为单条超长消息
    - `---cut---` 检测从子字符串匹配改为逐行精确匹配，防止嵌入文本误触发
    - `_split_naturally` 尾段合并仅对纯标点片段生效，避免将硬切分的内容片段错误并回
    - `on_segment=None` 且多分段时不再静默丢弃前面分段，改为合并返回完整文本
- **影响范围**：`kernel/config.py`（LogChannelConfig.system 默认）、`config/config.toml`（频道开关）、`plugins/affection/plugin.py`（日志+版本）、`plugins/memo.py`（日志级别+版本）、`plugins/schedule/plugin.py`（版本）、`plugins/chat.py`（版本号）、`services/llm/client.py`（分段逻辑）
- **测试**：ruff check 通过，pytest 577/587 通过（10 个预存失败与本次无关）
- **回滚**：git revert 即可

## 2026-05-02 — 记忆与好感度数据迁移：从 amadeus-in-shell 同步

- **类型**：data migration
- **操作人**：Claude Code (assisted)
- **变更**：
  - **memory_cards.db**：从 amadeus-in-shell 复制 6 张卡片（4 张 user/1416930401 + 2 张 group/984198159, 993065015），schema 完全一致无需转换
  - **affection/1416930401.json**：从 amadeus-in-shell 复制好感度数据（score=12, total_interactions=15），JSON 格式兼容
  - storage 目录是 Docker volume mount，复制后即时生效无需重建
- **影响范围**：`storage/memory_cards.db`、`storage/affection/1416930401.json`
- **回滚**：从旧项目重新复制或删除这两个文件即可

## 2026-05-02 — Soul 迁移：从 amadeus-in-shell 同步角色配置

- **类型**：config
- **操作人**：Claude Code (assisted)
- **变更**：
  - **identity.md**：从 `amadeus-in-shell/soul/identity.md` 完整复制，143 行详细 Emu 角色设定，含 `# 凤笑梦 (Emu Otori)` 标题 + `## 插话方式` 章节（解析器可正确提取 proactive 规则，不再 fallback 到内置默认身份）
  - **instruction.md**：从 `amadeus-in-shell/soul/instruction.md` 适配，核心差异：
    - 记忆系统：`recall_memo`/`update_memo` → `lookup_cards`/`update_cards`（适配 CardStore）
    - 图片：移除所有 `describe_image` 引用（omubot 在消息渲染阶段自动通过 Qwen VL 描述图片）
    - 保留全部人格规则：回复风格、场景差分（7 模式）、语气污染、分段发送、日常心情、角色生日、群聊上下文理解、保密规则、稳固人格、工具使用、主动搜索、表情包
  - **根因**：旧 identity.md 无 `# Title` 行，解析器返回 None → fallback 到 `_builtin_default()` 其中 `proactive=None` → Scheduler 跳过所有群消息
- **影响范围**：`config/soul/identity.md`、`config/soul/instruction.md`
- **回滚**：从 git 恢复旧 soul 文件即可

## 2026-05-02 — v1.0.1 修复：@提及回复 + Thinker + 指令别名

- **类型**：bugfix + feature
- **操作人**：Claude Code (assisted)
- **变更**：
  - **指令别名系统**：`Command` dataclass 新增 `aliases: list[str]` 字段，`CommandDispatcher._load()` 将别名一并索引
  - **`/plugins` 多入口**：新增 `/p`、`/plg`、`/插件` 三个别名
  - **全部插件开发者签名**：`AmadeusPlugin` 基类 `author` 默认值改为 `"kragcola"`
  - **修复 @提及不回复**：`scheduler.notify()` 的 `proactive is None` 守卫现在仅在非 @ 消息时生效，@ 消息始终触发回复
  - **修复 force_reply 语义过载**：`client.chat()` 中 `force_reply=True` 不再注入调试块或剥离心情/好感度块（那是 `/debug` 的职责，不应影响普通 @ 回复）
  - **版本升级**：omubot → 1.0.1，debug_commands 插件 → 1.1.0
- **影响范围**：`kernel/types.py`（Command 别名、author 默认）、`services/command.py`（别名索引）、`services/scheduler.py`（守卫条件）、`services/llm/client.py`（移除 force_reply 调试卷入）、`plugins/debug_commands.py`（别名注册、作者、版本）、`services/version.py`（版本号）、`pyproject.toml`（版本号）、`CHANGELOG.md`
- **测试**：ruff check 通过，pytest 通过（排除 libvips 预存失败）
- **回滚**：git revert 即可

## 2026-05-01 — 服务层指令系统：CommandDispatcher + /debug 迁移

- **类型**：feature
- **操作人**：Claude Code (assisted)
- **变更**：
  - **新增 `services/command.py`**：`CommandDispatcher` 服务，从 PluginBus 收集命令注册表，解析 `/command args` 前缀并分发执行
  - **新增 `CommandContext`**：`kernel/types.py` 新增 dataclass，作为命令 handler 的标准入参
  - **迁移 `/debug`**：从 `kernel/router.py` 硬编码（`_check_debug_prefix` 函数 + `_DEBUG_PREFIX` 常量）迁移至 `plugins/chat.py` → `ChatPlugin.register_commands()` 注册
  - **消息流集成**：私聊和群聊均在 LLM 处理前检查命令（群聊在 interceptor 之后、scheduler 之前；私聊在 render 之后、chat 之前）
  - **扩展性**：任何插件实现 `register_commands()` 返回 `Command` 实例即可注册新指令
- **影响范围**：router.py 移除 ~25 行硬编码，新增 dispatcher 集成；bot.py 新增 1 行初始化；chat.py 新增 ~65 行命令注册+handler
- **测试**：547/547 通过，lint 干净
- **回滚**：git revert 即可恢复旧 `/debug` 硬编码行为

## 2026-05-01 — Phase 7a: 单文件插件 + plugin.json 清单

- **类型**：refactor
- **操作人**：Claude Code (assisted)
- **变更**：
  - **PluginBus 侧车 .json 支持**：`discover_plugins()` Pass 2 为单文件插件自动拾取同名 `.json`，`_load_plugin_module` 统一 manifest 解析
  - **8 个插件转为单文件**：echo、element_detector、history_loader、dream、memo、vision、chat、sticker 从子目录迁为 `plugins/<name>.py`，合并所有辅助模块
  - **保留目录形态**：affection (4 文件)、schedule (7 文件) 因复杂度保持目录
  - **plugin.json 清单**：全部 10 个插件创建清单文件（8 个侧车 + 2 个目录内），sticker 声明 `"vision": ">=1.0.0"` 依赖
  - **import 路径更新**：bot.py (8 处)、kernel/router.py (1 处)、plugins/chat.py (1 处)、5 个测试文件
- **影响范围**：插件层结构变更，内核 API 不变，服务层不受影响
- **测试**：547/547 通过（排除 libvips 和 e2e 预存失败）
- **回滚**：git revert 即可，旧子目录需手动恢复

## 2026-05-01 — 开源准备：config/ 隔离 + 人格解耦 + 仓库推送

- **类型**：refactor + devops
- **操作人**：Claude Code (assisted)
- **变更**：
  - **config/ 目录隔离**：
    - 创建 `config/` 目录，将 `.env` 移入，`config.toml` 和 `config/soul/` 均在此目录下
    - `kernel/config.py`：`SoulConfig.dir` 默认值 `"soul"` → `"config/soul"`，`load_config()` 默认路径 `"config/config.toml"`
    - `docker-compose.yml`：env_file 和 volumes 路径全部更新
    - `.gitignore`：一条 `config/` 规则替代原有分散列举，新增 `._*` 防 Apple Double 文件
    - `.claude/settings.json`：hook 匹配模式更新为 `config/(config\.toml|soul/|\.env)`
    - Admin 路由默认值同步更新
  - **人格硬编码解耦**：
    - `services/llm/thinker.py`：`THINKER_SYSTEM_PROMPT` 使用 `{name}` 占位符，`think()` 新增 `identity_name` 参数
    - `services/llm/client.py`：调试模式提示移除 "凤笑梦"，传 `identity.name` 给 thinker
    - `plugins/schedule/generator.py`：`_SCHEDULE_SYSTEM_PROMPT` 重写为通用模板，移除 W×S 具体设定，`ScheduleGenerator` 接受 `identity_name`
    - `plugins/schedule/calendar.py`：新增 `set_self_name()`/`get_self_name()`，`is_self_birthday` 可配置
    - `plugins/schedule/mood.py`：生日检测改用 `is_self_birthday`
    - `plugins/sticker/plugin.py`："frequently" 提示使用 `{name}` 占位符
    - `admin/templates.py`：`admin_title` → `"Omubot Admin"`
    - `plugins/chat/plugin.py`：启动时调用 `set_self_name()` 并传 `identity_name`
    - 测试文件更新：通用示例名替代 "凤笑梦"、QQ 号
  - **文件清理**：删除 `_omubot_public_api.py`、`rewrite-plan.md`
  - **Git 仓库重建**：`rm -rf .git && git init`，全新干净历史，推送至 `github.com/kragcola/omubot`
  - **文档更新**：CLAUDE.md、README.md、docs/setup-guide.md、docs/operations.md、docs/architecture.md、wiki/05-services.md 路径全部同步
- **影响**：项目可安全开源 — 所有个人配置隔离在 `config/`（gitignored），源代码零硬编码人格引用
- **测试**：578 passed, 9 failed（6 libvips + 3 sticker git mismatch 预存），零回归
- **Lint**：ruff all checks passed

## 2026-05-01 — 重建 Docker 部署文件

- **类型**：infra
- **操作人**：Claude Code (assisted)
- **变更**：
  - 重建 `Dockerfile`：多阶段构建（builder + runtime），python:3.12-slim + libvips，uv 管理依赖
  - 重建 `docker-compose.yml`：napcat + bot 双容器，端口 8081:8080，volumes 挂载 storage/soul/config.toml/.env
  - 重建 `config.example.toml`：完整 16 节配置模板（含 schedule/affection/log.channels 节，旧版缺失）
  - 更新 `.dockerignore`：排除 storage/tests/wiki/*.md/.gitignore
- **影响**：项目恢复 Docker 部署能力；新操作员可从零 `docker compose up -d --build` 启动

## 2026-05-01 — src/ 耦合彻底清理 + 垫片删除 + 文档全线更新

- **类型**：refactor + docs
- **操作人**：Claude Code (assisted)
- **变更**：
  - 消除 `omubot/kernel/`、`omubot/services/`、`omubot/plugins/`、`omubot/admin/` 中全部 20 处 `from src.` 导入耦合
  - 修复 12 个测试文件的导入路径：`src.config` → `kernel.config`，`src.identity.models` → `services.identity`，私有符号 `_call_api` → `call_api` 等
  - `bot.py` 最后一个 `from src.config_loader` 改为 `from kernel.config`
  - `kernel/config.py` 默认值 `plugin_dirs: ["src/plugins"]` → `["plugins"]`
  - `pyproject.toml` ruff 路径修正
  - **删除 `src/` 目录**（28 个垫片文件）— 零个 `from src.` 导入残留
  - **删除 `旧内容待删/` 目录**（旧 amadeus 内容）
  - **更新维护日志 (maintenance-log.md)**：记录本次清理
  - **文档全线更新**：
    - CLAUDE.md — 命令、路径、配置引用修正
    - docs/architecture.md — `omubot/` → 根级目录，`src/` → 新路径，PluginBus 发现路径修正
    - docs/project-info.md — 命令速查、lint 路径、TUI 命令修正
    - docs/setup-guide.md — 目录结构移除 `src/` 和 `omubot/`，导入范例修正
    - wiki/02-kernel-api.md — 导入路径、plugin_dirs 默认值、向后兼容说明更新
    - wiki/04-plugin-guide.md — 示例导入路径修正
    - wiki/05-services.md — 服务迁移状态更新为已完成
    - wiki/06-config.md — 导入路径、默认值、向后兼容说明更新
    - wiki/07-tools.md — 模块路径修正
    - wiki/08-migration.md — 状态表更新（Phase 5/6 完成），`ruff check src/` 修正
    - wiki/README.md — 项目结构更新为扁平化布局，版本状态更新
- **影响**：项目完全独立，`kernel/`/`services/`/`plugins/`/`admin/` 四目录零耦合；文档与代码完全一致
- **测试**：578 passed, 9 failed（6 libvips + 3 sticker_tools git stash 预存），零回归
- **Lint**：ruff all checks passed
- **类型检查**：121 预存错误（非本次引入）

## 2026-05-01 — 工作区迁移：omubot 扁平化到根目录

- **类型**：refactor（工作区重组）
- **操作人**：Claude Code (assisted)
- **变更**：
  - 将 `omubot/kernel/`, `omubot/services/`, `omubot/plugins/`, `omubot/admin/`, `omubot/wiki/`, `omubot/docs/` 移动到根目录
  - 旧的 amadeus-in-shell 内容（Dockerfile, README.md, config.toml, soul/, storage/, napcat/, scripts/ 等）移入 `旧内容待删/`
  - `pyproject.toml`：更新 NoneBot 插件路径 `plugins.chat`，扩展 `known-first-party`
  - `bot.py`：更新 `discover_plugins` 路径，导入路径从 `omubot.` 改为直接导入
  - 全局替换 `from omubot.` → `from `（所有 .py 文件）
  - 根目录 `__init__.py` 重命名为 `_omubot_public_api.py`（避免包冲突）
  - `tests/test_client.py`：修复 mock patch 路径
  - `src/` 保留在根目录因 omubot 代码尚未完全迁移，仍耦合
- **影响**：工作区根目录现仅包含 omubot 重构内容 + 必要的 `src/`/`tests/`/`bot.py`；旧内容隔离在 `旧内容待删/`
- **测试**：581 passed, 6 failed（6 个 libvips 预存失败），零回归

## 2026-05-01 — 文档全面更新 + 搭建教程

- **类型**：docs
- **操作人**：Claude Code (assisted)
- **变更**：
  - `omubot/docs/architecture.md`：完整重写，新增 Omubot 三层架构、PluginBus 机制、插件发现流程、14 个插件一览、plugin.json 规范、开发钩子参考
  - `omubot/docs/project-info.md`：新增三层模型说明、14 个插件表、配置完整列表；更新存储路径、API 端点、命令速查
  - `omubot/docs/setup-guide.md`：新文件，从零搭建教程（6 步，预计 30-60 分钟），含开发指南、添加新插件范例、常见问题
  - `omubot/rewrite-plan.md`：更新追踪表 7.1/7.2 状态，新增当前状态总结段落，更新最后更新时间
- **影响**：外部人员可按 setup-guide 独立搭建；架构文档反映最新三层框架设计
- **Docker 验证**：构建成功，bot 正常启动，admin 面板正常（303 重定向至登录页）

## 2026-05-01 — Phase 7.1-7.2：单文件插件发现 + plugin.json 解析

- **类型**：feature
- **操作人**：Claude Code (assisted)
- **变更**：
  - `PluginBus.discover_plugins()` 重构为两轮扫描：Pass 1 子目录（优先），Pass 2 独立 `.py` 文件（跳过 `__init__`，同名时目录优先）
  - 新增 `_load_plugin_module()` 和 `_apply_manifest()` 辅助方法，消除重复代码
  - `plugin.json` 解析：若插件目录下有 `plugin.json`，解析后用字段（name, version, description, priority, enabled, dependencies）覆盖实例属性
  - `bot.py`：移除 5 个单文件插件（DateTime, GroupAdmin, HttpApi, WebFetch, WebSearch）的手动 import 和 register，改为 `_bus.discover_plugins("omubot/plugins")` 自动发现
- **影响**：单文件插件无需在 `bot.py` 中手动注册；plugin.json 可独立于代码更新元数据
- **测试**：581 passed（6 个 libvips 预存失败），零回归

## 2026-05-01 — Phase 6：Admin Panel 迁移到 omubot/admin/

- **类型**：refactor
- **操作人**：Claude Code (assisted)
- **变更**：
  - 新建 `omubot/admin/`：17 个文件从 `src/admin/` 复制，内部 import 更新为 `omubot.admin.*`
  - `create_admin_router()` 重构：改为接受 `PluginContext`，从 ctx 解构所需服务引用（usage_tracker, msg_log, config.group, card_store, admins 等）
  - `auth.py`：`_get_admin_token()` 移除对 `src.config_loader` 的依赖，只从环境变量读取
  - `bot.py`：提前设置 `ctx.bot_start_time`；从 `omubot.admin` 导入并挂载 admin router + `AdminAuthMiddleware`
  - `ChatPlugin`：移除 ~16 行 admin 挂载代码
  - `src/admin/`：`__init__.py`、`auth.py`、`templates.py` 改为 shim re-export
- **影响**：Admin 面板从 `src/` 迁出，成为 `omubot/` 下的系统服务；路由挂载权从 ChatPlugin 移至 `bot.py`
- **测试**：581 passed（6 个 libvips 预存失败），零回归

## 2026-05-01 — 零散事项：tick 循环基础设施 + ScheduleGenerator 移入 SchedulePlugin

- **类型**：refactor
- **操作人**：Claude Code (assisted)
- **变更**：
  - `PluginBus` 新增 `start_tick_loop(ctx, interval=60)` / `stop_tick_loop()` 方法，后台 asyncio 循环驱动 `fire_on_tick`
  - `router.py` 首次连接时启动 tick 循环（`bus.start_tick_loop(ctx)`）；移除 `datetime`/`ZoneInfo` 无用 import
  - `SchedulePlugin` 新增 `on_bot_connect`（启动 ScheduleGenerator + 缺失日程即时生成）和 `on_shutdown`（停止生成器）
  - `router.py` 移除 ~12 行日程启动代码，逻辑完整迁移至 SchedulePlugin
- **影响**：`fire_on_tick` 现在有生产环境驱动循环，插件可开始实现 `on_tick`；ScheduleGenerator 生命周期完全由 SchedulePlugin 管理
- **测试**：581 passed（6 个 libvips 预存失败），零回归

## 2026-05-01 — Phase 5 完成：全部插件切出 + PromptBuilder 精简

- **类型**：refactor（里程碑）
- **操作人**：Claude Code (assisted)
- **变更**：
  - 5.9 AffectionPlugin: `on_pre_prompt` + `on_post_reply` 钩子
  - 5.10 SchedulePlugin（新）: `on_pre_prompt` 注入心情块
  - 5.11 MemoPlugin: `on_pre_prompt`（全局索引 stable + 实体记忆 dynamic）+ `on_post_reply`（记忆提取）
  - 5.12 StickerPlugin: `on_pre_prompt`（表情包规则 static + 库视图 stable）
  - 5.8 HistoryLoaderPlugin（新）: `on_bot_connect` 钩子，群聊历史加载
  - 5.13 DreamPlugin（新）: `on_startup` 创建 DreamAgent，`on_bot_connect` 启动，`on_shutdown` 停止
  - 5.14 VisionPlugin（新）: `on_startup` 创建 VisionClient
  - 框架增强: `AmadeusPlugin.on_bot_connect` 钩子；`PluginContext.memo_extractor` 字段
  - `PromptBuilder` 精简：从 ~310 行缩减到 ~130 行，只保留 static identity + state_board
  - `client.py`：`PromptBlock.position` 分派（static/stable 获得 cache_control）
  - `ChatPlugin`：移除 ~60 行 VisionClient/DreamAgent/MemoExtractor 创建代码
- **影响**：Phase 5 全部 14 个插件完成切出；所有业务逻辑通过插件钩子驱动
- **测试**：579 passed，零回归

## 2026-05-01 — Phase 5a：框架增强（enabled/dependencies/commands/admin routes/manifest）

- **类型**：feature
- **操作人**：Claude Code (assisted)
- **变更**：
  - `AmadeusPlugin` 新增 `enabled`、`dependencies` 字段
  - 新增 `Command`、`AdminRoute` 类型 + `register_commands()`、`register_admin_routes()` 方法
  - `PluginBus._safe_call()` 检查 `plugin.enabled`；新增 `collect_commands()`、`collect_admin_routes()`
  - 新增 `PluginBus._resolve_dependencies()`：Kahn 拓扑排序 + 版本检查 + 循环依赖降级
  - 新增 `omubot/kernel/manifest.py`：`PluginManifest` + SemVer 约束解析器（`== >= > <= < ^ ~ *`）
- **测试**：581 passed，零回归

## 2026-05-01 — Phase 5.2-5.7：工具类插件切出 + ElementDetector

- **类型**：refactor
- **操作人**：Claude Code (assisted)
- **变更**：
  - 5.2 ElementDetectorPlugin: 目录插件，`on_message` 拦截器，搬入 detector 逻辑
  - 5.3 DateTimePlugin: 单文件，`register_tools`
  - 5.4 WebSearchPlugin: 单文件，`register_tools`
  - 5.5 WebFetchPlugin: 单文件，`register_tools`
  - 5.6 HttpApiPlugin: 单文件，`register_tools`
  - 5.7 GroupAdminPlugin: 单文件，`register_tools`
  - `ChatPlugin` 移除对应工具的初始化代码
- **测试**：581 passed，零回归

## 2026-05-01 — Phase 5.1: EchoPlugin 切出

- **操作人**：Claude Code (assisted)
- **变更**：
  - 新建 `omubot/plugins/echo/plugin.py`：EchoPlugin（priority=200，`on_message` 拦截器），将 EchoTracker 和 build_echo_key 移入
  - `MessageContext` 新增 `bot`、`nickname` 字段（供拦截器发送消息）
  - `router.py`：echo_key 构建移至 `fire_on_message` 之前，echo 检测逻辑移除；修复 SIM102 lint
  - `ChatPlugin`：移除 EchoTracker 初始化
  - `bot.py`：注册 EchoPlugin
  - `src/plugins/echo.py` → 兼容 shim
- **影响**：echo 复读检测现在通过 PluginBus 钩子运行，与 router 解耦
- **测试**：24 echo 测试 + 581 全量测试通过，零回归，lint 通过

---
## 2026-05-01 — Phase 4 完成：ChatPlugin 适配到 PluginBus

- **类型**：架构重构（里程碑）
- **操作人**：Claude Code (assisted)
- **变更**：
  - 新建 `omubot/kernel/router.py` (~520 行) — NoneBot 事件 → PluginBus 桥接：消息渲染、群聊监听、私聊处理、禁言监听
  - 新建 `omubot/plugins/chat/plugin.py` (~270 行) — ChatPlugin（priority=0），`on_startup` 中初始化全部系统服务存入 PluginContext，`on_shutdown` 清理
  - `src/plugins/chat/__init__.py` → 兼容 shim（~11 行）
  - `bot.py` 新增 PluginContext / PluginBus / ChatPlugin 注册 / setup_routers 调用
  - `LLMClient.__init__` 新增 `bus` 参数；`chat()` 中触发 `fire_on_pre_prompt`（收集 plugin_blocks）和 `fire_on_post_reply`（副作用通知）
  - `PromptBuilder.build_blocks()` 新增 `plugin_blocks` 参数
  - `PluginContext` 新增 `bus` 字段
- **影响**：系统通过 PluginBus 运转；bot.py 负责组装，ChatPlugin 负责初始化，router 负责事件路由；后续 Phase 5 可直接切出独立插件
- **测试结果**：581 passed，7 预存在失败，零回归，零 lint
- **下一步**：Phase 5（逐个切出插件：Echo → ElementDetector → 工具类 → Affection → Schedule → Memo）

## 2026-05-01 — Phase 3 完成：系统服务迁移（16 模块，6 批次）

- **类型**：架构重构（里程碑）
- **操作人**：Claude Code (assisted)
- **变更**：
  - Batch 1（零依赖）：`message_log`、`types`、`card_store`、`migrate`、`image_cache`、`sticker_store`、`usage`、`humanizer`、`tools/context`、`tools/registry`
  - Batch 2：`timeline`、`short_term`
  - Batch 3：`identity`（Identity + IdentityManager 合并）、`state_board`、`retrieval`
  - Batch 4：`prompt_builder`（原 prompt.py）、`thinker`
  - Batch 5：`llm/client`（1578 行，最大单文件）
  - Batch 6：`scheduler`
  - 全部旧位置替换为兼容 shim（`from omubot.services.xxx import *`），旧 import 路径仍可用
- **迁移模式**：cp + sed 批量替换 import 路径 → 重命名 `_` 前缀标识符（Python `import *` 限制）→ 修复测试 patch 目标路径 → ruff check --fix → pytest 验证
- **测试结果**：581 passed，7 预存在失败（image_cache × 6 + mood × 1），零回归

## 2026-05-01 — Phase 2 完成：配置系统重组

- **类型**：架构重构（里程碑）
- **操作人**：Claude Code (assisted)
- **变更**：
  - 新建 `omubot/kernel/config.py`（~370 行）— 所有 Pydantic 配置模型（23 个类）+ `KernelConfig`（plugin_dirs, disabled_plugins, max_hook_time_ms）+ `load_config()` 函数
  - `src/config.py` → 兼容 shim（从 `omubot.kernel.config` re-export 全部模型）
  - `src/config_loader.py` → 兼容 shim（从 `omubot.kernel.config` re-export `load_config`）
  - `omubot/kernel/__init__.py` 和 `omubot/__init__.py` 新增配置类型导出
- **向后兼容**：所有 `from src.config import ...` 和 `from src.config_loader import ...` 导入路径不变，现有代码无需修改
- **新增 `KernelConfig`**：`plugin_dirs=["src/plugins"]`、`disabled_plugins=[]`、`max_hook_time_ms=5000`，已作为 `BotConfig.kernel` 子字段
- **测试结果**：581 passed，7 预存在失败（image_cache × 6 + mood × 1），零回归，零 lint
- **下一步**：Phase 3（系统服务迁移）或 Phase 4（ChatPlugin 适配）

## 2026-05-01 — Phase 1 完成 + 项目 Wiki 创建

- **类型**：架构重构（里程碑）
- **操作人**：Claude Code (assisted)
- **Phase 1 交付物**：
  - `omubot/kernel/types.py`（~320 行）— 6 种 Context 类型、AmadeusPlugin 基类（8 钩子）、PromptBlock、Tool ABC、ToolContext、Identity、Content/TextBlock/ImageRefBlock
  - `omubot/kernel/bus.py`（~250 行）— PluginBus 调度器：注册/卸载/发现、8 种 fire_* 方法、异常隔离 _safe_call、目录扫描 discover_plugins
  - `tests/test_kernel_types.py`（36 tests）— Context/Block/Tool/Plugin/Identity 全覆盖
  - `tests/test_plugin_bus.py`（37 tests）— 注册排序/生命周期/消息管线/prompt 收集/工具收集/tick/异常隔离/插件发现
  - `omubot/__init__.py`、`omubot/kernel/__init__.py` — 包入口，导出全部公开 API
- **测试结果**：73 new passed，0 lint errors，581 已有测试零回归
- **Wiki**：创建 `omubot/wiki/` 目录，10 个文档覆盖架构/内核 API/Context 类型/插件开发/系统服务/配置/工具/迁移/术语/FAQ
- **下一步**：Phase 2（配置系统重组）或 Phase 3（系统服务迁移）



## 2026-05-01 — Omubot 重写计划启动

- **类型**：架构重构
- **操作人**：Claude Code (assisted)
- **背景**：amadeus-in-shell 项目功能持续增加（好感度、日程、表情包、记忆卡片、检索门控、视觉、梦境……），`src/plugins/chat/__init__.py` 已膨胀至 ~1000 行单体，`LLMClient` 和 `PromptBuilder` 直接依赖 7-10 个子系统。每次加新功能都需要改动核心文件，维护成本日益上升。
- **目标**：设计一套插件框架（PluginBus），将可插拔功能以独立插件形式挂载，每个插件只通过 1-3 个管线钩子与核心通信，彻底解耦。
- **参考项目**：
  - **MaiBot**（旧 bot）：组件注册模型（Action/Command/Tool/EventHandler）+ 事件管道（ON_MESSAGE → ON_PLAN → AFTER_LLM → POST_SEND）+ 插件目录扫描 + @register_plugin 装饰器 + `_manifest.json` 元数据
  - **MCDReforged**：单线程 TaskExecutor + 事件驱动 + 热重载 + 插件独立存储
  - **pluggy**：hookspec/hookimpl 装饰器模式 + 异常隔离
  - **wphooks**：WordPress 风格 action/filter 双钩子范式
- **已完成**：
  - 全项目 57 个 .py 文件审计，功能分为 6 类（Framework API / Core / Plugin / Admin / Config / Support）
  - 设计草案 `AmadeusPlugin` 基类 + `PluginBus` 调度器 + 6 种 Context 类型
  - 定义 8 个管线钩子：`on_startup` / `on_shutdown` / `on_message` / `on_thinker_decision` / `on_pre_prompt` / `on_post_reply` / `register_tools` / `on_tick`
  - 规划 14 个插件（7 个业务插件 + 7 个工具插件）
  - 完成新目录结构设计（`omubot/` 包，~50 个文件，5 层架构）
  - 创建 `Omubot/` 目录，生成 [feature-classification.md](Omubot/feature-classification.md) 功能分类文档 + 迁移映射速查表
  - 确定三层架构（内核 → 系统服务 → 插件），对标鸿蒙 OS 分层模型
  - 完成 [rewrite-plan.md](Omubot/rewrite-plan.md) 详细实施方案（8 个 Phase，含操作提示词）
- **设计原则**：
  - 钩子默认串行（by priority），与 async/await 自然契合
  - 异常隔离：单个插件崩溃不影响其他插件
  - 消息消费短路：on_message 返回 True 即停止后续处理
  - 单进程架构（不引入 MaiBot 式 IPC），保持调试简单
  - 核心插件 priority 0-99，业务插件 100-199，可选插件 200+
- **分阶段实施计划**：

	| 阶段 | 内容 | 影响 |
	| --- | --- | --- |
	| Phase 1 | 创建 `omubot/plugin_bus.py`，chat 插件内部重构为使用 bus | 零行为变化 |
	| Phase 2 | 切出 echo + element_detector（只有 on_message 钩子） | 验证模式 |
	| Phase 3 | 切出 affection + schedule（有 on_pre_prompt + on_post_reply） | 核心解耦 |
	| Phase 4 | 切出 sticker + memo + dream + vision | 完成解耦 |
	| Phase 5 | 插件热重载、独立配置 schema、目录扫描自动发现 | 锦上添花 |
- **回滚方案**：每阶段独立 PR，出问题只回滚该阶段；PluginBus 通过 feature flag 控制是否启用

### Phase 1 完成 (2026-05-01)

- **新增文件**：
  - `omubot/__init__.py` — 包入口，导出全部公开 API
  - `omubot/kernel/__init__.py` — 内核层入口
  - `omubot/kernel/types.py` — 类型系统（~320 行）：6 种 Context 类型、AmadeusPlugin 基类（8 个钩子）、PromptBlock、Tool ABC（从 src/tools/base.py 提升）、ToolContext、Identity、Content/TextBlock/ImageRefBlock（从 src/memory/types.py 提升）
  - `omubot/kernel/bus.py` — PluginBus 调度器（~250 行）：注册/卸载/发现、8 种 fire_* 调度方法、异常隔离 _safe_call、目录扫描 discover_plugins
  - `tests/test_kernel_types.py` — 36 个测试：覆盖所有 Context/Block/Tool/Plugin/Identity
  - `tests/test_plugin_bus.py` — 37 个测试：覆盖注册排序/生命周期/消息管线/prompt 收集/工具收集/tick/异常隔离/插件发现
- **测试结果**：73 passed（+73），0 lint 错误
- **未变更**：`bot.py`、所有现有 `src/` 文件——零回归，581 个已有测试仍然通过


---

## 2026-05-01 — 启动历史加载修复

- **类型**：bug fix
- **操作人**：Claude Code (assisted)
- **问题**：每次重启后 `load_history failed | group=984198159`、`load_history failed | group=993065015`，启动时无法从 NapCat 拉取群历史消息
- **根因**：`history_loader.py` 通过 `POST {napcat_url}/get_group_msg_history` 裸 HTTP 调用 NapCat，但 NapCat 的 OneBot HTTP server 未启用（`onebot11_384801062.json` 中 `httpServers: []`），端口 29300 连接被拒
- **修复**：
  - 重写 `load_group_history()` 接受 `bot: Bot` 参数，改用 `bot.call_api("get_group_msg_history", ...)` 通过已有 WebSocket 连接调用
  - `_load_one_group()` 用 `ActionFailed` 异常处理替代 `retcode` 检查
  - 修改 `chat/__init__.py` 调用点，传入 `bot=bot` 替代 `napcat_url`
  - 重写 `tests/test_history_self_messages.py`：Mock `bot.call_api` 替代 `aiohttp.ClientSession.post`，新增 API 异常和空消息测试
- **效果**：启动后成功加载群历史（993065015: 23条, 984198159: 29条），群聊上下文立即可用

## 2026-05-01 — @mention 不回复修复

- **类型**：bug fix
- **操作人**：Claude Code (assisted)
- **问题**：@bot 消息仍然被 thinker 判定为 wait，导致不回复
- **根因**：`GroupChatScheduler._do_chat()` 调用 `_llm.chat()` 时未传 `force_reply`，thinker 对 @mention 也执行了 wait 判断
- **修复**：
  - `_GroupSlot` 新增 `force_reply: bool` 标记
  - `notify(is_at=True)` 时设为 True
  - `_fire()` 捕获标记并传入 `_do_chat(force_reply=...)`
  - `_do_chat()` 传递 `force_reply` 给 `_llm.chat()`，绕过 thinker wait

## 2026-05-01 — 第三期：检索门控（Retrieval Gating）

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：Phase 2 后每轮对话将实体所有活跃卡片全量注入 system prompt，O(n) 膨胀。大部分卡片与当前话题无关，浪费上下文窗口
- **变更内容**：
  - 新增 `src/memory/retrieval.py`：`RetrievalGate` 类，4 级门控策略（全量→周期刷新→关键词→最小提示），~170 行
  - 修改 `src/llm/prompt.py`：`PromptBuilder` 新增 `retrieval_gate`/`session_id`/`conversation_text` 参数，memo block 双路径（门控/旧缓存），新增 `rewind_retrieval_turn()`
  - 修改 `src/llm/client.py`：调用前提取对话文本（群聊 `get_recent_text()`，私聊直接用 user_content），传入 `session_id`/`conversation_text`；thinker wait 后调用 `rewind_retrieval_turn()`
  - 修改 `src/memory/group_timeline.py`：新增 `get_recent_text(group_id, last_n=3)` 拼接最近 N 轮对话文本
  - 修改 `src/plugins/chat/__init__.py`：创建 `RetrievalGate(card_store=card_store, refresh_interval=10)`，传入 PromptBuilder
  - 新增 `tests/test_retrieval.py`：25 个测试覆盖 4 级门控 / 关键词提取 / 缓存失效 / turn 回退 / 作用域隔离 / 会话上限
- **4 级门控策略**：

  | 级别 | 触发条件 | 行为 |
  | --- | --- | --- |
  | 全量检索 | 新会话首轮 / 每 10 轮周期刷新 | 注入全部卡片（缓存，不重复查 DB） |
  | 关键词检索 | 对话文本关键词匹配到卡片 | 注入匹配卡片（上限 10 张） |
  | 最小提示 | 以上都不满足但有卡片 | 提示卡片数量 + 建议用 `lookup_cards` 工具 |
  | 空 | 实体无卡片 | 空字符串 |
- **thinker wait 回退**：thinker 判定 wait 时 `rewind_retrieval_turn()` 回退 turn_count，避免空消耗全量检索配额
- **影响范围**：memo block 从固定内容变为按需注入，首轮后 token 消耗降低约 80%；LLM 可通过 `lookup_cards` 工具主动查询未注入卡片
- **测试结果**：523 passed（+25），7 failed（6 libvips 预存 + 1 flaky mood）
- **回滚方案**：`PromptBuilder(retrieval_gate=None)` 即可回退旧行为

---

## 2026-05-01 — 第二期：类型化记忆卡片（CardStore）

- **类型**：重构
- **操作人**：Claude Code (assisted)
- **背景**：
  - 旧 MemoStore 用 `.md` 文件存储记忆，纯文本无结构，存在 6 个已诊断问题（群聊 memo/nickname 错乱）
  - 借鉴 KokoroMemo 的卡片设计，用 SQLite 存储有类型、有作用域、支持取代关系的记忆卡片
- **变更内容**：
  - 新增 `src/memory/card_store.py`：核心存储层，SQLite + aiosqlite，14 列 schema，Card/NewCard 数据类，12 个公共 API
  - 新增 `src/memory/migrate.py`：一次性 MD→卡片迁移，6 张卡片从 3 个旧 `.md` 文件，幂等，源文件改为 `.md.migrated`
  - 新增 `src/memory/memo_extractor.py`：每轮对话后提取 `[category] 内容` 格式的新事实卡片
  - 新增 `src/memory/state_board.py`：群聊状态板，从 MessageLog SQLite 推导活跃用户/话题/频率/@提及
  - 修改 `src/llm/prompt.py`：memo_store → card_store，stable block 用 `build_global_index()`（计数式索引），memo block 用 `build_entity_prompt()`（`[类别] 内容` 格式），6 块布局
  - 修改 `src/llm/client.py`：`append_memo` → `add_card`（scope/scope_id/category/content），compact 系统提示词重写
  - 修改 `src/llm/dream.py`：6 个新 LLM 工具（list/search/update/supersede/expire cards + list entities），系统提示词重写为分类→去重→交叉验证→取代工作流
  - 修改 `src/tools/memo_tools.py`：RecallMemoTool → CardLookupTool，UpdateMemoTool → CardUpdateTool（add/update/supersede/expire）
  - 修改 `src/plugins/chat/__init__.py`：MemoStore → CardStore(db_path="storage/memory_cards.db")，全部依赖链切换
  - 修改 `src/admin/__init__.py`：memo_store 参数 → card_store
  - 新增 `tests/test_card_store.py`：~17 个测试覆盖 CRUD/supersede/索引输出/迁移
  - 重写 `tests/test_memo_tools.py`：11 个测试覆盖 CardLookupTool/CardUpdateTool
  - 重写 `tests/test_dream.py`：10 个测试覆盖 CardStore 版 DreamAgent
  - 修改 `tests/test_client.py`、`tests/test_e2e_live.py`、`tests/test_prompt.py`：适配 CardStore
  - 删除 `src/memory/memo_store.py`、`tests/test_memo_store.py`
  - 新增 `src/llm/thinker.py`、`src/memory/state_board.py`、`tests/test_state_board.py`（第一期合并带入）
- **卡片模型**：
  - 7 类：preference(偏好)/boundary(边界)/relationship(关系)/event(事件)/promise(承诺)/fact(事实)/status(状态)
  - 3 作用域：user/group/global，confidence 0.0-1.0，supersedes 取代边
- **迁移结果**：`1416930401.md` → 3 cards，`984198159.md` → 1 card，`993065015.md` → 1 card，共 6 张（全部 fact 类别，等待 Dream Agent 首次运行后重新分类）
- **影响范围**：所有记忆相关路径（prompt 构建、compact、dream、工具调用、extractor、admin debug）全部切换至 CardStore；prompt 格式从全文 memo body 变为 `[类别] 内容`；全局索引从文本 mention 变为计数式 `用户 @QQ: 偏好×1 事实×3`
- **测试结果**：481 passed（+2），7 failed（6 个 libvips 预存，1 个 flaky 无关）
- **回滚方案**：git revert 到 9f2e72e，旧 `.md.migrated` 文件可手动改回 `.md`

## 2026-05-01 — GIF 动画表情保存为静态图修复 + 上下文追踪修复（项目侧）

- **类型**：Bug 修复
- **操作人**：Claude Code (assisted)
- **背景**：
  - Bot 收录 GIF 动画表情时，pyvips 只加载第一帧并保存为 JPEG（静态图）
  - StickerStore._detect_format() 显式拒绝 GIF
  - Thinker wait 时 last_input_tokens 被重置为 0，丢失上下文追踪
- **变更内容**：
  - 修改 `src/memory/image_cache.py`：新增 `_find_cached()`（多扩展名缓存命中），`_process_and_save()` 检测 GIF magic bytes → 保存原始字节为 `.gif` 跳过 pyvips
  - 修改 `src/sticker/store.py`：`_detect_format()` 接受 GIF 返回 `"gif"`
  - 修改 `src/llm/client.py`：Thinker wait 时用实际 input_tokens 代替 0
- **影响范围**：新收录的 GIF 表情包可保留动画；缓存命中支持多扩展名；上下文追踪更准确
- **注意**：client.py 的上下文追踪修复后因"问题与项目无关"被回退 — 此条目仅记录 GIF 修复

---

## 2026-04-30 — 第一期：群聊状态板（Group State Board）

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：
  - Bot 在群聊中缺乏对"当前正在发生什么"的结构化认知，完全依赖原始对话历史
  - 导致上下文跟踪差、称呼混乱、回复时机不当
  - 借鉴 KokoroMemo 的热记忆/状态板设计，采用轻量级规则方案
- **变更内容**：
  - 新增 `src/memory/state_board.py`：`GroupStateBoard` 类，基于规则从 `MessageLog` SQLite 推导线活跃用户、近期话题（二元组频率）、消息频率、@提及
  - 修改 `src/llm/prompt.py`：`build_blocks()` 返回值从 5 块扩展为 6 块 `[static, mood, state_board, affection, stable, memo]`；新增 `_build_state_board()` 方法
  - 修改 `src/llm/client.py`：将状态板文本注入 thinker 的 mood_text，使 reply/wait 决策感知群聊状态
  - 修改 `src/plugins/chat/__init__.py`：实例化并注入 `GroupStateBoard`，bot 连接时更新 `bot_self_id`
  - 新增 `tests/test_state_board.py`：14 个测试覆盖快照格式化、QQ 解析、文本清洗、二元组提取、活跃用户/频率/话题/@提及推导
- **影响范围**：群聊 prompt 中新增 `【当前群聊状态】` 块；thinker 决策可感知群聊活跃度；私聊不受影响（空块）
- **设计原则**：无新外部依赖、无额外 LLM API 调用、从现有 MessageLog 读取、cache_control: ephemeral

---

## 2026-04-30 — 群聊图片自动描述 + Loguru 格式错误修复

- **类型**：Bug 修复 + 功能恢复
- **操作人**：Claude Code (assisted)
- **背景**：
  1. 群聊图片只显示 `«图片»` 占位符，LLM 无法在上下文中看到图片内容，必须先调用 `describe_image` 工具才能理解
  2. NapCat 发送 `[json:data={...}]` 消息时，loguru stderr colorizer 将 JSON 中的花括号解析为 format field 导致 `ValueError: unmatched '{' in format spec`
- **变更内容**：
  - `_render_message()`：群聊图片下载后移除 `«图片»` 占位符，改为通过 Qwen VL 自动描述（与私聊一致）；动画表情（`sub_type=1`）仍显示 `«动画表情»`
  - `bot.py` `_channel_format()`：对 `record['message']` 中的 `{`/`}` 做转义（`{{`/`}}`），loguru colorizer 解析后自动还原为单花括号
- **影响范围**：群聊图片现在自动携带描述文本供 LLM 理解；`[json:...]` 消息不再触发日志格式异常

---

## 2026-04-30 — @提及绕过 thinker + 缓存告警排除 0 轮调用

- **类型**：Bug 修复 + 逻辑优化
- **操作人**：Claude Code (assisted)
- **背景**：
  1. 用户 @bot 后，thinker 决定 wait（沉默），导致提及必回复失效。根因：thinker 收到的群聊消息不包含 is_at 信息
  2. 缓存命中率告警频繁误报。根因：0 轮调用（直接回复）命中率 ~19%（仅静态系统块命中），多轮调用 ~60-80%，混合采样拉低平均值
- **变更内容**：
  - `_GroupSlot` 新增 `force_reply` 字段；`notify(is_at=True)` 设置该标志；`_do_chat()` 传入 `_llm.chat(force_reply=True)` 绕过 thinker
  - `UsageTracker._check_alerts()` 排除 `tool_rounds == 0` 的调用，仅多轮调用参与缓存命中率采样
  - 冷启动检查移至 tool_rounds 过滤之前，确保首次调用正确消费
- **影响范围**：@提及必定回复；缓存告警仅在多轮调用命中率异常低时触发

---

## 2026-04-30 — 群聊隐私遮掩

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：群聊与私聊共享同一份好感度记忆，在公开场合暴露对用户的深入了解会显得不自然。需要在群聊中模拟公私场合区别，但记忆本身保持共通。
- **变更内容**：
  - `AffectionEngine.build_affection_block()` 新增 `in_group` 参数：群聊中隐藏好感度分数、使用模糊 tier 标签（"不太熟"/"有点面熟"等）、注入社交距离指令（"不要主动暴露深入了解"）、隐藏昵称偏好和心情加成
  - `PromptBuilder.build_blocks()` 根据 `group_id is not None` 自动推导 `in_group`
  - 新增 `privacy_mask` 配置开关（`GroupConfig` / `ResolvedGroupConfig`，默认 true），允许对特定群关闭遮掩
  - `LLMClient.chat()` → `PromptBuilder.build_blocks()` 链路传递 `privacy_mask` 参数
  - `GroupChatScheduler._do_chat()` 解析群配置后传入
- **影响范围**：仅群聊。`privacy_mask=false` 或私聊时行为与旧版一致。私聊中深度询问仍可触发完整记忆。
- **回滚方案**：`[group].privacy_mask = false` 或在群覆盖中关闭。

---

## 2026-04-30 — 表情包频率上调

- **类型**：参数调整
- **操作人**：Claude Code (assisted)
- **背景**：主动表情包发送偏保守，需更符合元气二次元角色设定。
- **变更内容**（`src/llm/prompt.py` `frequently` 档）：
  - 触发阈值 ≥4 → ≥2（消息表达任意情绪即触发）
  - 移除连发惩罚（原 -1）
  - 新增"随口接话 +1~2"评分项
  - 态度：宁可多发不要错过
- **影响范围**：rebuild 后生效。

---
## 2026-04-30 — 要素察觉功能框架（含 LLM 模式）

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：群聊中某些触发词适合用预设回复快速响应（如"早安""晚安"），类似复读机制在 LLM 调度前拦截。后期扩展 LLM 模式支持反差吐槽等需要生成能力的场景。
- **变更内容**：
  - 新增 `ElementRule` / `ElementDetectionConfig` 配置模型（`src/config.py`），`ElementRule` 含 `use_llm` 字段
  - 新增 `ElementDetector` 插件（`src/plugins/element_detector.py`）：静态模板替换 + LLM 模式分发
  - 在 `collect_group_context` 中接入：复读检测之后、timeline 写入之前触发
  - LLM 模式：`reply` 字段作为 system prompt 指令，调用 `_llm._call` 生成回复（绕过 thinker/心情）
  - 配置 `[element_detection]`，静态规则示例 `这X神了↔这X拉了`、`我也要X吗→对`，LLM 规则 `X是这样的→反差吐槽`
  - 测试 9 条（`tests/test_element_detector.py`）
- **LLM 模式注意事项**：system prompt 需保持简短（~200 字符），角色描述过长会触发内心独白而非直接回复；只用 `result["text"]` 不用 thinking_blocks。
- **影响范围**：仅群聊。enabled=false 或 rules=[] 时无开销。
- **回滚方案**：设置 `[element_detection].enabled = false`。

---

## 2026-04-30 — 防检测人性化延迟

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：账号因发送消息过于规律被腾讯风控。需要在每次发消息前插入随机延迟模拟人类打字节奏。
- **变更内容**：
  - 新增 `AntiDetectConfig` 配置（`src/config.py`）：enabled / min_delay / max_delay / char_delay
  - 新增 `Humanizer` 单例模块（`src/anti_detect.py`）：`delay(text)` 方法按消息长度随机等待
  - 覆盖所有群聊发送路径：scheduler `_send_to_group`、echo/要素察觉/表情包发送
  - 管理员 `SendGroupMsgTool` 不加延迟
  - 配置 `[anti_detect]`（`config.toml` / `config.example.toml`）
- **默认参数**：基础延迟 0.5s–3.0s + 每字符 20ms
- **影响范围**：所有群聊和私聊消息发送。enabled=false 完全关闭。
- **回滚方案**：`anti_detect.enabled = false`。

---
## 2026-04-30 — Prompt 缓存命中率优化（P0–P3）

- **类型**：性能优化
- **操作人**：Claude Code (assisted)
- **背景**：排查发现 DeepSeek V4 Flash 缓存命中率 ~74%，波动剧烈（19%–87%）。根因四层：MemoExtractor 每轮 invalidate entity_block → 私聊每轮 cache MISS；mood/affection block 无 cache_control 且夹在两个缓存块之间；entity_block 混合稳定内容（索引/sticker）与高频内容（memo body）；thinker 调用无缓存标记且 tokens 未记入统计。
- **变更内容**：

  **P0 — 移除 MemoExtractor 即时 invalidate**（`src/memory/memo_extractor.py`）
  - 删除 `self._prompt.invalidate(user_id=user_id)`。memo 照常写入但 entity_block 缓存不清空，下次同用户调用可命中。Memo 数据推迟到 compaction 或 Dream Agent 整理时刷新。
  
  **P1 — mood_block / affection_block 加 cache_control**（`src/llm/prompt.py` → `_build_mood_block` / `_build_affection_block`）
  - 两个块均添加 `"cache_control": {"type": "ephemeral"}`。各自按自然频率变化（mood ~15 分钟、affection 按互动累积），DeepSeek 可在各自窗口内独立复用。
  
  **P2 — entity_block 拆分**（`src/llm/prompt.py` → `build_blocks`）
  - 原 entity_block 拆为 stable_block（全局索引 + sticker 视图，缓存键 `__stable__`，几乎永久命中）和 memo_block（群/用户 memo body，缓存键按 entity，compaction 时刷新）。
  - 测试更新（`tests/test_prompt.py`）：block 数量 4→5，断言适配新布局。
  
  **P3 — Thinker 缓存 + 用量追踪**（`src/llm/thinker.py` + `src/llm/client.py`）
  - thinker system block 加 `cache_control`；`ThinkDecision` 新增 `usage` 字段返回 token 数据。
  - `chat()` 中 thinker 调用独立记录为 `call_type="thinker"` 行（含 input/cache/output tokens），替换原来的全零占位。

- **影响范围**：rebuild 后生效。缓存命中率预计从 ~74% 升至 ~85%+；usage 统计新增 thinker 行类型；私聊连续对话 entity_block 可稳定命中；stable_block 几乎永不 miss。
- **回滚方案**：`git revert` 相关提交。

---

## 2026-04-30 — 稳固人格 & /debug 管理员限制

- **类型**：功能新增 + 安全加固
- **操作人**：Claude Code (assisted)
- **变更内容**：

  **1. 稳固人格**（`soul/instruction.md`）
  - 新增「稳固人格 — 拒绝被随意操控」章节。非管理员试图操控 bot 行为（改说话方式、命令式称呼等）时，根据当前心情值选择性回应：心情好配合玩一下但不改变、心情差拒绝或怼回去、偶尔叛逆故意做错。区分真伪请求（自然自我介绍 vs 命令式操控）。
  
  **2. /debug 管理员限制**（`src/plugins/chat/__init__.py`）
  - 新增 `_admins` 模块级变量，`handle_private_chat` 中 /debug 检测后校验管理员身份。非管理员使用 /debug 时前缀静默剥离、消息按普通对话处理，日志记录 warning。

- **影响范围**：restart 生效（soul 文件 mount） + rebuild 生效（debug 限制代码）。bot 不再盲从群友的行为指令；/debug 仅管理员可用。
- **回滚方案**：`git checkout soul/instruction.md` 或 `git revert` 相关提交。

---

## 2026-04-30 — debug 模式重复发送修复 & Markdown 代码层剥离 & Docker 时区修复

- **类型**：Bug 修复（3 项）
- **操作人**：Claude Code (assisted)
- **变更内容**：

  **1. debug 模式消息重复发送**
  - 根因：`force_reply` 路径中 `on_segment` 回调发送了一次消息，随后调用方 `private_chat.finish()` 再发送一次。两处在 `client.py` 的 force_reply 分支均存在此问题。
  - 修复（`src/llm/client.py`）：移除两处 force_reply 路径中的 `on_segment` 调用，仅由 `finish()` 发送单条回复。

  **2. LLM 回复仍含 Markdown 格式**
  - 根因：`soul/instruction.md` 已明确禁止 Markdown，debug 模式也注入了格式约束，但 DeepSeek 偶尔忽略。代码层无兜底剥离。
  - 修复（`src/llm/client.py`）：新增 `_strip_markdown()` 函数 + 8 个正则模式（bold/h2/list/olist/inline-code/fence/strikethrough），在两处 reply 提取点（tool loop 内和 tool loop 耗尽后）调用。跳过 italic（单 `*` 在东亚文字中太常见易误伤）。

  **3. Docker 容器时区为 UTC**
  - 根因：`docker-compose.yml` 中 napcat 和 bot 均未设 `TZ` 环境变量，容器默认 UTC。日志时间戳显示前一天（如 CST 4/30 凌晨 → 日志显示 4/29）。
  - 修复（`docker-compose.yml`）：napcat 和 bot 服务均添加 `TZ=Asia/Shanghai` 环境变量。

- **影响范围**：rebuild 后生效。debug 模式消息不再重复；LLM 回复中的 `**加粗**`、`# 标题`、\`代码\`、```代码块```、`- 列表` 等格式自动剥离；日志时间戳正确显示 CST。
- **回滚方案**：`git revert` 相关提交。

---
## 2026-04-30 — 颜文字→表情包程序化强制执行 & 主动记忆提取系统

- **类型**：Bug 修复 + 功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：

  **1. 颜文字→表情包程序化强制执行**
  - 根因：system prompt 中的"逐条自评打分"模式依赖 LLM 自觉执行，deepseek-v4-flash 在专注于写作时经常忘记同时调用 send_sticker。用户反馈「(≧▽≦)/」等颜文字发了但表情包没跟上。
  - 修复（双层方案）：
    - **Prompt 层**（`src/llm/prompt.py`）：三种频率模式的 prompt 全部重构，将「颜文字强制配图」提升为第一条硬性规则（位置在评分系统之前），明确标注"这不是可选的——颜文字=表情包"
    - **代码层**（`src/llm/client.py`）：新增 kaomoji 正则检测（`_KAOMOJI_RE`），当 LLM 返回的文本含颜文字但未调用 send_sticker 时，自动注入强制 sticker 选择轮次（追加 system 指令 + continue 工具循环），不再依赖 LLM 自觉
  - 工作机制：LLM 忘发 → 代码检测到颜文字 → 强制追加一轮 tool call → LLM 只需选表情包发送（不再需要自我评分）

  **2. 主动记忆提取系统（MemoExtractor）**
  - 根因：memo 仅在上下文压缩（compact）时写入，而压缩只在 token 超阈值时触发。私聊对话短、永远不触发压缩 → `storage/memories/users/` 目录完全为空 → bot 每次都是"脑袋空空的"。旧 MaiBot 使用 HippoMemorizer（定时批量处理），但有一致性延迟。
  - 修复（新增 `src/memory/memo_extractor.py`）：
    - 每次对话回合结束后，异步（fire-and-forget）调用轻量 LLM（max_tokens=256）提取用户事实
    - 提取到的事实通过 `MemoStore.append()` 写入用户备忘录的「待整理」区
    - Dream Agent 后续整理时可合并去重
    - 下次对话时 `recall_memo` 立即可查
  - 比旧项目更好的点：
    - **即时性**：每轮对话后立即提取（旧 MaiBot 是定时批量，有延迟）
    - **轻量**：单次 256 token 输出，不增加用户感知延迟（后台异步）
    - **精准**：只提取当前轮次的新事实，不重复扫描历史
    - **渐进**：记忆随对话自然累积，而非等压缩触发
  - 接线位置：`src/plugins/chat/__init__.py` 创建 MemoExtractor 实例，传入 LLMClient；`src/llm/client.py` → `chat()` 在返回回复前启动后台提取任务

- **影响范围**：rebuild 后生效。颜文字表情包现在有代码兜底保证发送；私聊记忆从零变为每轮自动记录。群聊记忆仍依赖压缩（后续可扩展 extractor 支持群聊）。
- **回滚方案**：`git revert` 相关提交。

---
## 2026-04-30 — 模拟思考后续修复 & 日志花括号转义 & 自动识图错误可见性

- **类型**：Bug 修复（3 项）
- **操作人**：Claude Code (assisted)
- **变更内容**：

  **1. Thinker 导致不再主动发表情包**
  - 根因：thinker 的 `[思考] ...` 被注入为 user 角色消息，盖过 system prompt 中的表情包频率规则（"总评分 ≥ 4 时调用 send_sticker"），LLM 严格按字面执行 thinking 指令而忽略 sticker 规则
  - 修复（`src/llm/client.py`）：thought 从 user 消息改为追加到 system blocks（`[思考指引] + 注意：仍需遵循所有指令包括表情包使用规则`），让 system 层两条指令并列生效

  **2. 日志花括号 KeyError**
  - 根因：`tool_call` / `tool_result` 日志的 JSON 字符串含 `{` `}` 花括号，被 loguru Handler #3（NoneBot 默认 handler）的格式解析器误读为格式字段，抛出 `KeyError: '"image_tag"'` 等错误
  - 修复（`src/llm/client.py`）：对 JSON 字符串做花括号转义（`{` → `{{`，`}` → `}}`），避免被下游 handler 二次解析

  **3. 自动识图静默失败**
  - 根因：`_render_message()` 中的 `_describe_one()` 用 `except Exception: pass` 静默吞掉所有异常，Qwen VL 自动识图失败时既无日志也无法诊断
  - 修复（`src/plugins/chat/__init__.py`）：失败时打 WARNING 日志（含文件路径）；将 `_vision_client` 全局变量改为使用函数参数 `vision_client` 传入

- **影响范围**：rebuild 后生效。表情包恢复主动发送；日志不再出现 KeyError；图片识别失败时可见具体原因。
- **回滚方案**：`git revert` 相关提交。

---
## 2026-04-29 — 预回复思考阶段（模拟思考）

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `src/llm/thinker.py`：新增预回复思考模块。`ThinkDecision` 数据类（action: reply/wait/search + thought），`THINKER_SYSTEM_PROMPT` 指导 LLM 快速判断下一步行动（6 条决策原则），`parse_think_output()` 解析 JSON 输出，`think()` 异步函数执行思考 LLM 调用
  - `src/config.py`：新增 `ThinkerConfig`（enabled=true, max_tokens=256），`BotConfig` 新增 `thinker` 字段
  - `config.toml` / `config.example.toml`：新增 `[thinker]` 配置节
  - `src/llm/client.py`：`LLMClient.__init__` 新增 `thinker_enabled` / `thinker_max_tokens` 参数；`chat()` 在工具循环之前调用 thinker，wait 返回 None（等同 pass_turn），reply/search 将 thought 注入消息列表
  - `src/plugins/chat/__init__.py`：LLMClient 构造传入 thinker 配置
- **背景**：bot 在短时间内连续回复多条消息（拆成多个事件分别回复），缺乏「说话前先想一下」的机制。借鉴旧 MaiBot Planner→Replyer 架构，在 LLM 生成回复之前先用轻量调用判断下一步行动：回复、沉默、或搜索。
- **影响范围**：rebuild 后生效。每次回复前额外一次轻量 LLM 调用（max_tokens=256），约增加 0.5-1.5s 延迟。wait 决策可减少不必要的回复，降低 token 消耗。`[thinker].enabled = false` 可关闭该功能。

---
## 2026-04-29 — 日志频道过滤系统

- **类型**：功能增强
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `src/config.py`：新增 `LogChannelConfig`（12 bool 字段），`LogConfig` 新增 `channels` 字段
  - `config.toml` / `config.example.toml`：新增 `[log.channels]` 节，默认开启 6 个关键频道（message_in/out/thinking/mood/affection/schedule），其余关闭
  - `bot.py`：`_quiet_filter` 改为 `_make_channel_filter()` 闭包，根据 `LogChannelConfig` 开关过滤 stderr 日志；ERROR 始终放行，文件日志不受影响
  - 全项目 16 个源文件打日志频道标签（`logger.bind(channel="...")`）：message_in、message_out、thinking、mood、affection、schedule、scheduler、usage、compact、system、debug、dream
- **背景**：`docker compose logs` 输出大量调试信息（matcher noise、调度器决策、token 用量等），人眼难以提取关键信息。用户要求按重要性分级，默认 ERROR 级别 + 6 个关键频道可见。
- **影响范围**：重启后生效。stderr 输出大幅减少，默认只看到收/发消息、工具调用、心情、好感度、日程和所有 ERROR。文件日志 `storage/logs/bot_*.log` 不受影响（始终全部 DEBUG）。无需重建镜像（restart 即可）。

---
- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/schedule/calendar.py`：`DayContext` / `BirthdayEntry` 数据类 + `get_day_context()` 函数。硬编码 2026 年中国法定节假日（7 个节日共 40 天）、6 个调休上班日、15 个不放假的特殊节日（七夕/圣诞等）、世界计划全 26 位角色生日
  - `src/schedule/__init__.py`：导出 `DayContext`、`BirthdayEntry`、`get_day_context`
  - `src/schedule/generator.py`：`_SCHEDULE_SYSTEM_PROMPT` 新增日期类型指引（上学日/周末/节假日/调休日/角色生日），`_generate()` 用户消息改为包含 `DayContext` 详细信息
  - `src/schedule/mood.py`：`_compute()` 新增第 5 步「日期类型心情加成」——节假日 +0.15 valence +0.1 energy，调休日 -0.05 valence，角色生日 +0.1 valence +0.1 openness，自己生日额外 +0.15 valence +0.1 energy。`build_mood_block()` 新增生日/特殊节日提示文本
  - `src/tools/datetime_tool.py`：`execute()` 返回内容附加节假日/特殊日/生日信息
  - `soul/instruction.md`：新增「角色生日」小节，指导 LLM 在角色生日当天自然庆祝
  - 测试：`tests/test_calendar.py`（35 个测试）全部通过
- **背景**：日程生成完全不感知真实日期（周一至周五全在上学、节假日也在上课、角色生日无人提及）。用户要求结合真实日期但保留虚拟感。
- **影响范围**：rebuild 后生效。心情系统自动感知节假日/生日并调整情绪；日程生成 prompt 包含日期类型指引；`get_datetime` 工具返回特殊日期信息；mood_block 约增加 0~150 chars（仅特殊日期时）。无需新增配置项——日历数据硬编码，每年更新一次即可。
- **回滚方案**：`git revert` 相关提交。
---
## 2026-04-29 — 称呼与好感度系统

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/affection/` 模块：`models.py`（AffectionProfile 数据类，score/tier/mood_bonus/suffix）、`store.py`（JSON 文件持久化，`storage/affection/{user_id}.json`）、`engine.py`（好感度计算、昵称解析、affection_block 文本构建）、`__init__.py`
  - 新增 `src/tools/affection_tools.py`：`set_nickname` 工具，LLM 可在用户说"叫我xx"时调用
  - `src/config.py` 新增 `AffectionConfig`（enabled/score_increment=0.8/daily_cap=10.0）
  - `src/llm/prompt.py`：`PromptBuilder` 新增 `affection_engine` 参数，`build_blocks()` 返回 4 个 block（static/mood/affection/entity），affection_block 每次刷新不缓存
  - `src/plugins/chat/__init__.py`：初始化 AffectionStore/AffectionEngine，注册 SetNicknameTool，传入 PromptBuilder 和 LLMClient
  - `src/llm/client.py`：`LLMClient` 新增 `affection_engine` 参数，`chat()` 中每次 LLM 调用前记录互动
  - `config.toml` / `config.example.toml` 新增 `[affection]` 配置节
  - `soul/instruction.md` 新增「你的日常与心情」节：心情对说话影响、不可主动提及日程的规则
  - 测试：`tests/test_affection.py`（32 个测试）全部通过，`tests/test_prompt.py` 更新 block 索引
- **规则**：每次互动 +0.8 分，日上限 10.0 分，新用户 0 分起；好感度 ≥ 60 时 affection_block 注入"对他态度更温和"；称呼优先级：自定义 > 群名片 > QQ昵称 > QQ号
- **影响范围**：build 后生效。好感度数据存储在 `storage/affection/`，affection_block 约 150-250 chars 注入 system prompt。总开关 `[affection].enabled = false` 可关闭。
- **回滚方案**：`git revert` 相关提交，或设 `[affection].enabled = false`

---
## 2026-04-29 — 清理 soul 中的日文

- **类型**：配置变更
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `soul/identity.md`：`わんだほーい☆` → `哇嚯☆`（5 处），`鳳えむ` → `凤笑梦`，昵称列表移除 `えむ`/`鳳えむ`
  - `soul/instruction.md`：`わんだほーい` → `哇嚯`（6 处）
  - `src/schedule/mood.py`：心情提示中的 `わんだほーい` → `哇嚯`（2 处）
- **背景**：用户检查测试群聊日志，要求 bot 输出中不出现日文。
- **影响范围**：soul 文件 volume mount，restart 即生效；mood.py 需 rebuild。

---
## 2026-04-29 — 模拟日程系统

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/schedule/` 模块：`types.py`（Schedule/TimeSlot/MoodProfile 数据类）、`store.py`（JSON 持久化+内存缓存）、`generator.py`（每日凌晨 2:00 通过 LLM 生成日程 JSON）、`mood.py`（MoodEngine 实时心情计算 + 19 个 mood_hint→MoodProfile 预设 + 9 个心情→行为 prompt 映射 + 20% 反常情绪机制）、`__init__.py`（导出）
  - `src/config.py` 新增 `ScheduleConfig`（enabled/storage_dir/generate_at_hour/mood_anomaly_chance/mood_refresh_minutes）
  - `src/llm/prompt.py`：`PromptBuilder` 新增 `schedule_store`/`mood_engine` 参数，`build_blocks()` 在 static_block 和 entity_block 之间插入非缓存的 `mood_block`（当前时间+活动+心情+行为指引+反泄漏规则），entity_block 缓存结构从 `list[blocks]` 改为单 `dict`
  - `src/plugins/chat/__init__.py`：`_init()` 初始化 ScheduleStore/MoodEngine/ScheduleGenerator；`_on_connect()` 加载当日日程+启动后台生成循环；`_shutdown()` 停止生成循环；DateTimeTool 注册时传入 schedule_store
  - `src/tools/datetime_tool.py`：`DateTimeTool` 新增可选 `schedule_store` 参数，返回当前时间时附加「你正在：xxx」上下文
  - `soul/instruction.md` 新增「你的日常与心情」节：心情对说话影响、不可主动提及日程的规则
  - `config.toml` / `config.example.toml` 新增 `[schedule]` 配置节
  - 测试：`tests/test_schedule_store.py`（12 个）、`tests/test_schedule_generator.py`（8 个）、`tests/test_mood.py`（23 个）——共 43 个测试全部通过
  - **Bug 修复**：`_lookup_base()` 原先直接返回 `_MOOD_BASE` 字典中的 MoodProfile 对象，`_compute()` 对其原地修改导致模块级预设被污染（后续调用看到前一次的反常情绪理由）。改为每次返回新 MoodProfile 副本。
- **背景**：用户希望 bot 像"过着真实一天"的人，语气/情绪随日程自然变化而非随机切换。详见计划文档。
- **影响范围**：rebuild 后生效。心情系统在每次 LLM 调用时实时计算（15 分钟缓存窗口），mood_block 约 200 chars 注入 system prompt。日程生成每天 1 次 LLM 调用（~2300 token），几乎无额外成本。总开关 `[schedule].enabled = false` 可完全关闭。
- **回滚方案**：`git revert` 相关提交，或设 `[schedule].enabled = false`

---
## 2026-04-29 — 主动搜索行为指令增强

- **类型**：配置变更
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `soul/instruction.md`：重写「工具使用」→「主动搜索」小节，新增核心原则"不知道就查，不要猜"
  - 明确触发场景：不认识图片角色/作品/人物、不了解的新梗/热点/术语、不确定的事实性问题、听不懂的群话题
  - 搜索策略：多关键词并行 web_search、不够时 web_fetch 深入、矛盾时以权威来源为准
  - 搜索后回应：用自己的语气自然输出，不要"根据搜索结果……"句式；搜不到坦诚说但保持元气
- **背景**：bot 与群友交流时常因不认识图片角色或不了解话题而哑火。虽然 web_search/web_fetch 工具一直可用，但缺少明确的行为指令驱动主动使用。
- **影响范围**：`docker compose restart bot` 即刻生效，无需 rebuild

---
## 2026-04-29 — 群聊复读功能

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/plugins/echo.py`：`EchoTracker` 类 + `build_echo_key()` 函数。按群跟踪消息重复次数，5 分钟内同一内容出现 3 次触发复读
  - `build_echo_key()` 从 OneBot 原始消息段构建 key，覆盖文本/表情包(image:sub_type:md5)/QQ表情(face)/@。同一表情包重发（相同 MD5）可被识别为重复
  - 5% 概率不参与复读，改为发送"打断复读！"
  - 连续"打断复读！"消息触发打断链："打断复读！" → "打断打断复读！" → "打断打断打断复读！"...
  - `src/plugins/chat/__init__.py`：在 `collect_group_context` 中插入快速路径，复读命中后 cancel_debounce + 记录用户消息到 timeline + 记录 echo 到 timeline，然后 return，不触发 LLM
  - `src/llm/scheduler.py`：新增 `cancel_debounce()` 方法，复读命中时取消待处理的 debounce 任务并重置计数器，防止 LLM 在复读后自顾自说话
  - `tests/test_echo.py`：24 个测试用例（14 个 EchoTracker + 10 个 build_echo_key）
- **背景**：用户要求新增 QQ 群传统复读功能。初版仅支持纯文本，用户反馈表情包也应可复读、复读后 bot 不应继续说话。二版通过 `build_echo_key` 覆盖表情包/图片（基于 NapCat 提供的 MD5 去重），通过 `cancel_debounce` 防止后续 LLM 触发。
- **影响范围**：仅群聊生效，私聊不影响。复读命中后 bot 直接发送消息并 return，不调用 scheduler，零 token 消耗。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — 表情包主动发送频率 + sub_type 修正

- **类型**：功能新增 + Bug 修复
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `src/config.py` → `StickerConfig` 新增 `frequency` 字段（`"rarely"` / `"normal"` / `"frequently"`），默认 `"normal"`
  - `src/llm/prompt.py` → 新增 `_STICKER_FREQUENCY_PROMPTS` 字典，`PromptBuilder` 接受 `sticker_frequency` 参数并在 system prompt 中注入对应频率的行为指令
  - `src/plugins/chat/__init__.py` → 初始化 `PromptBuilder` 时传入 `bot_config.sticker.frequency`
  - `src/tools/sticker_tools.py` → `SendStickerTool` 中 `"subType"` 修正为 `"sub_type"`（OneBot v11 snake_case 标准），新增 `"summary": "[动画表情]"`（QQ 据此区分表情尺寸）。根因：旧 MaiBot-Napcat-Adapter 使用 `"subtype": 1` 全小写格式，当前代码用了不被识别的驼峰格式
  - `tests/test_sticker_tools.py` → 更新 subType 断言为 `sub_type` + `summary`
  - `soul/instruction.md` → 表情包「发送原则」重写，新增何时发送、如何选择、流程等详细指引
  - `config.toml` / `config.example.toml` → 新增 `frequency = "frequently"` / `frequency = "normal"`
- **背景**：bot 被 @ 或要求时才会发表情，不会主动使用。用户希望 bot 像二次元角色一样在对话中自然甩表情包。初版按固定频率（每 N 轮）设计，用户反馈"欸——好狡猾这种话天然适配表情包为什么不发"，改为逐条评估：每条消息独立打分，超过阈值就发，频率设置只改变阈值高低而非间隔。表情包以普通图片尺寸显示是因为 `subType` 字段名不被 NapCat 识别。
- **影响范围**：rebuild 后生效。`frequency` 改变 LLM 使用 `send_sticker` 的倾向——`rarely` 保守，`frequently` 积极甩表情包。
- **回滚方案**：设 `frequency = "rarely"` 或 `enabled = false` 关闭表情包系统。

---
## 2026-04-29 — Qwen VL 表情包识别 + 偷取表情包

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/vision/client.py`：`VisionClient` 通过 OpenAI 兼容 API 调用 Qwen2.5-VL-7B 小模型描述图片内容
  - `src/config.py` 新增 `QwenVLConfig`（enabled/base_url/api_key/model），挂载在 `VisionConfig.qwen` 下
  - `config.toml` 新增 `[vision.qwen]` 配置节，启用 Qwen VL 并配置 DashScope API
  - `src/config_loader.py` 新增 `QWEN_VL_API_KEY`/`QWEN_VL_BASE_URL`/`QWEN_VL_MODEL` 环境变量映射
  - `src/plugins/chat/__init__.py`：`_render_message()` 下载图片后自动调用 Qwen VL 生成文字描述，注入 `«图片N: 描述»` 到消息中，让文本模型 DeepSeek 也能"看到"图片
  - `src/tools/sticker_tools.py`：
    - 新增 `DescribeImageTool`（describe_image），LLM 可主动请求详细查看某张图片
    - `SaveStickerTool` 权限放宽：`requested_by` 改为可选——bot 主动偷取时留空直接收录（source="stolen"），用户请求时仍需管理员权限（source="admin"）
  - `soul/instruction.md` 表情包节重写：新增「识别图片内容」「偷取表情包」小节，教导 LLM 主动发现、识别、收录群友发的表情包
- **背景**：DeepSeek V4 不支持视觉，bot 无法理解图片/表情包，也无法主动收藏。通过 Qwen VL 小模型代为描述图片，既便宜（~$0.0001/张）又快速。
- **影响范围**：rebuild 后生效。群聊和私聊中的图片会自动附带文字描述，bot 能看懂表情包并主动偷取喜欢的。注意：需要配置有效的 DashScope API key（`[vision.qwen].api_key` 或 `QWEN_VL_API_KEY` 环境变量）。
- **回滚方案**：`git revert` 相关提交，或设 `[vision.qwen].enabled = false` 关闭。

---
## 2026-04-29 — 群内黑话主动学习机制

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `soul/instruction.md` 记忆系统新增「群内黑话学习」节，指引 LLM 主动识别、记录和使用群内黑话
  - 识别规则：多人反复使用的不熟悉词汇、有人直接解释的词、非标准汉语但有明确语义的词、音游/游戏术语
  - 记录格式：`- **词汇** (N次): 在这个群语境下的含义`，写入群备忘录 `### 群内惯用词`
  - 使用原则：已记录的可以自然使用，不确定的先不用，不堆砌
- **背景**：旧 MaiBot 的 442 条惯用词 + 186 条惯用表达已迁移到群备忘录，bot 重启后 LLM 能在 system prompt 中看到。但缺少主动学习新黑话的指令。
- **影响范围**：bot 重启后生效，LLM 将在群聊中主动捕捉新词汇并记录到群备忘录

---
## 2026-04-29 — 修复换行不分段 + DeepSeek thinking blocks 400

- **类型**：Bug 修复（2 个）
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - **换行不分段**：`_split_naturally()` 改为 `\n` 硬切分（每行一条消息），仅 < 4 字的极短行合并到邻居。原先算法把同一段内所有行拼到 45 字才切，导致 LLM 写的多行被合并成一条。
  - **DeepSeek thinking blocks 400**：`_call_api()` 新增 `thinking` 和 `thinking_delta` 事件捕获，返回值新增 `thinking_blocks`。`chat()` 构建 assistant 消息时，将 thinking blocks 原样插入 content 头部。根因是 DeepSeek V4 thinking mode 要求第二轮 API 调用必须把第一轮返回的 thinking block 传回，否则 400。
  - `_call_api()` 新增 400 错误响应体日志（`logger.error("API {} | body={}")`），方便后续排查。
- **影响范围**：工具调用（get_datetime 等）恢复正常；私聊/群聊多行回复正确拆分。rebuild 后生效。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — 自然分句发送（仿真人逐条回复）

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `src/llm/client.py`：新增 `_split_naturally()` / `_split_on_sentence_end()` / `_split_long_on_comma()`，按优先级自动切分 LLM 回复：
    1. `\n\n`（段落空行）→ 必定切分
    2. `\n`（换行）→ 主切分点，每行一个想法
    3. `。！？` → 单行超 45 字时在此切分
    4. `,，;；:：、` → 单句仍超 45 字时最后手段
  - 参数：`_MAX_CHUNK=45`、`_MIN_CHUNK=4`、段间延迟 1.2s
  - 保留 `---cut---` 显式切分机制
  - 修复末尾标点（`。！？`）孤立成段的 bug：`_split_on_sentence_end` 末尾标点自动回贴、合并逻辑对标点不加 `\n`
  - `soul/instruction.md`「分段发送」节更新：引导 LLM 每行写一个想法、每条控制在 2~3 行/40 字以内
- **影响范围**：群聊和私聊 LLM 回复均自动逐条发送，模拟真人连发。rebuild 后生效。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — 昵称提及检测 + @/昵称必回复

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `.env` 新增 `BOT_NICKNAMES` 环境变量，包含 bot 所有昵称（凤笑梦、emu、笑梦、姆、姆姆、凤同学、Emu、凤、凤えむ、えむ）
  - `src/plugins/chat/__init__.py`：`collect_group_context()` 新增文本昵称扫描，消息中含 bot 昵称时视为 `is_at=True`，强制触发 LLM 调用
  - `soul/identity.md`：`## 插话方式` 重构，将 @提及、回复、叫名字/昵称升级为「必须回复」（不可 pass_turn），其余场景为「视情况回复」
  - `src/admin/auth.py`：修复 form token 类型标注导致 pyright 报错
- **影响范围**：群聊中提及 bot 昵称将立即触发回复（等同原生 @）。rebuild 后生效。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — Admin Dashboard 实现 + 端口修正

- **类型**：部署 / 配置变更
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 Admin Dashboard（`src/admin/`），基于 Jinja2 + htmx + Chart.js（CDN），6 个页面：总览、用量统计、群聊管理、配置查看、Soul 编辑、日志查看
  - 认证方式：`admin_token`（config.toml）或 `ADMIN_TOKEN` 环境变量 → HMAC 签名 Cookie
  - `BotConfig` 新增 `admin_token` 字段，`config_loader.py` 新增 `ADMIN_TOKEN` env var 映射
  - `MessageLog` 新增 `query_recent()` 方法
  - Docker Compose bot 服务新增 `8081:8080` 端口映射（宿主机 8080 被 Calibre 占用）
  - `docker-compose.yml` 从无端口暴露改为 `ports: ["8081:8080"]`
  - `pyproject.toml` 新增 `jinja2`、`python-multipart` 依赖，ruff 排除 `._*` 文件
- **影响范围**：bot rebuild 后生效，访问 `http://localhost:8081/admin/` 即可进入管理面板。默认 token 为 `admin`，建议通过环境变量设置强密码。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — 凤笑梦 soul 文件重构

- **类型**：配置变更
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 基于三份参考文档（`凤笑梦参考/` 目录下）重构 `soul/identity.md` 和 `soul/instruction.md`
  - 参考文档：《凤笑梦_角色扮演知识谱.md》《凤笑梦_语料风格包.md》《凤笑梦_剧情文案原文出处索引.md》
  - `identity.md`：完全重写，新增「基础身份」表格、「一句话定义」、「性格结构」（表层/深层/行动力/核心驱动力/成长主轴）、「人际关系」（司/宁宁/类/真冬/家庭）、「语气与说话方式」（核心公式/口头禅用法/严肃模式）、「像与不像」判据；保留「插话方式」章节
  - `instruction.md`：完全重写，新增「场景差分」（日常/兴奋/安慰/低落/解释/邀请/对象差分）、「必须避免的语气污染」；保留分段发送(---cut---)、群聊上下文理解、工具使用、记忆系统、表情包规则
  - 新增群备忘录分区支持：`### 群内惯用词`、`### 惯用表达`（配合之前的惯用词迁移数据）
- **影响范围**：bot 重启后即刻生效，角色扮演精度大幅提升
- **回滚方案**：`git checkout soul/identity.md soul/instruction.md`

---
## 2026-04-29 — 惯用词与表达迁移：旧 MaiBot → 新 bot 群备忘录

- **类型**：数据迁移
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 从旧 MaiBot 数据库 (`/Users/kragcola/MaiM-with-u/MaiBot/data/MaiBot.db`) 提取全部惯用词和惯用表达
  - 写入新 bot 群备忘录 `storage/memories/groups/993065015.md` 和 `984198159.md`
  - 群 993065015（烤）：442 条惯用词 + 186 条惯用表达
  - 群 984198159（测试）：1 条惯用词 + 1 条惯用表达
  - `config.toml` → `[memo].group_max_chars` 从 500 提升至 60000（容纳大量惯用词）
  - 创建 `storage/memories/index.md` 索引文件
- **数据来源**：旧 MaiBot SQLite → `jargon` 表（筛选 `is_jargon=1, count≥2`）+ `expression` 表（筛选 `checked=1, rejected=0`）
- **chat_id 映射**：通过逆向 MaiBot 哈希算法 (`md5("qq_{群号}")`) 确认 `0469082337...` → 群 993065015、`77b74300...` → 群 984198159
- **影响范围**：bot 启动后，群聊 system prompt 将自动加载对应群的惯用词/表达，提升角色扮演的语境贴合度
- **回滚方案**：删除 `storage/memories/groups/` 下的 .md 文件，恢复 `group_max_chars = 500`

---
## 2026-04-29 — 新 bot 初始化部署

**背景**：用本项目（amadeus-in-shell）替换原有的 MaiBot（v7.3.5），全新部署。

**旧 maibot 参考数据**（提取自 `/Users/kragcola/MaiM-with-u/`）：

| 项目 | 值 |
| --- | --- |
| 旧 bot QQ | 384801062（昵称：emu不吃小杯面） |
| 旧 bot 框架 | MaiBot v7.3.5 + MaiBot-Napcat-Adapter + LPMM |
| 旧 bot 活跃群 | 984198159（测试）、993065015（烤） |
| 旧 bot 部署日 | 2026-01-16，累计 28,769 请求 |
| 旧 bot 模型 | deepseek-v3 (planner) + qwen3-30b (replyer) + qwen3-vl-30 (vision) |
| 旧 LLM 端点 | DeepSeek v1 + SiliconFlow（均为 OpenAI 格式） |
| 人设变迁 | 牧濑红莉栖 → 凤笑梦（旧 bot 后来也已改为 Emu） |

**新 bot 配置概要**：

| 项目 | 值 |
| --- | --- |
| 人设 | 凤笑梦 (Emu Otori) — Wonderlands×Showtime |
| LLM | DeepSeek V4 Flash（Anthropic 兼容端点，1M 上下文） |
| API 端点 | `https://api.deepseek.com/anthropic` |
| 管理员 QQ | 1416930401（工丿囗） |
| 部署方式 | Docker Compose（NapCat + Bot） |
| NapCat 版本 | mlikiowa/napcat-docker:v4.15.0 |
| Git remote | `github.com/RoggeOhta/amadeus-in-shell` |

**关键架构差异**：

- LLM API 从 OpenAI 格式 (`/v1`) 改为 Anthropic 兼容格式 (`/anthropic`)，支持原生 tool_use + cache_control
- 不再使用 Planner+Replyer 分离架构，改为单一 LLM 调用 + Tool loop（最多 5 轮）
- 记忆系统从 LPMM 知识图谱改为 .md 备忘录 + GroupTimeline
- 主动插话从 `talk_value` 概率值改为 `## 插话方式` 规则 + `pass_turn` 工具
- 不再内置错别字生成器，靠 prompt 控制风格
- 部署从本地 Python 进程改为 Docker Compose

**部署前 checklist**：

- [ ] `.env` — SUPERUSERS、ONEBOT_WS_URLS、LLM_* 环境变量已配置
- [ ] `config.toml` — LLM 接入、群聊参数、vision/sticker/dream 已配置
- [ ] `soul/identity.md` — 凤笑梦人设已编写（从旧 MaiBot `personality` 字段迁移）
- [ ] `soul/instruction.md` — 行为指令已调整
- [ ] NapCat WebUI (`:6099`) 扫码登录新 QQ 号
- [ ] 目标 QQ 群（984198159、993065015）测试 @bot 回复、主动插话、工具调用

**部署步骤**：

```bash
docker compose up napcat -d    # 先起 NapCat，扫码登录
docker compose up bot -d       # 再起 Bot
```

**注意事项**：

- NapCat 容器**必须用 `restart`，禁止 `down`+`up`**（device fingerprint 变 = 触发 QQ 风控）
- NapCat 持久化目录：`napcat/config/`（配置）、`napcat/data/`（QQ session/device fingerprint）
- Bot 的 `soul/` 和 `.env` 通过 volume mount 注入，修改后 `docker compose restart bot` 生效
- `storage/` 目录持久化所有运行数据（用量库、消息库、日志、记忆、图片缓存、表情包）
- 旧 MaiBot 数据保留在 `/Users/kragcola/MaiM-with-u/MaiBot/data/`，如需迁移表情包或记忆可从此提取

---
## 模板 — 日常维护
### 部署记录
```markdown
## YYYY-MM-DD — <标题>
- **类型**：部署 / 配置变更 / 故障处理 / 升级
- **操作人**：
- **变更内容**：
- **影响范围**：
- **回滚方案**：
- **验证结果**：
```
### 故障记录
```markdown
## YYYY-MM-DD — <故障标题>
- **发现时间**：
- **现象**：
- **根因**：
- **处理步骤**：
- **恢复时间**：
- **后续措施**：
```
---
## 快速排查命令
```bash
# 查看 Bot 日志
docker compose logs bot --tail=100 -f
# 查看 NapCat 日志
docker compose logs napcat --tail=100 -f
# 检查容器状态
docker compose ps
# 用量 TUI
uv run python -m src.llm.usage_cli tui day
# 用量 API
curl http://localhost:8080/usage/summary/today
# 重启 Bot（人设/配置变更后）
docker compose restart bot
# 重建 Bot（代码/依赖变更后）
docker compose up bot -d --build
# 重启 NapCat（断线/风控后）
docker compose restart napcat
# 进入 Bot 容器
docker compose exec bot .venv/bin/python -c "..."
# 检查 storage 目录
ls -la storage/logs/ storage/usage.db storage/messages.db
```

## 2026-05-08 — 在线重启说明澄清
- 调整 Admin Web 与 `/api/admin/system` 的重启说明文案，明确“在线重启”只会重启当前 Bot 进程，不会重建 Docker 镜像。
- 系统页与重启弹窗新增“适合在线重启 / 需要先重建镜像”提示，避免把配置重载和代码更新混为一谈。
- 重启成功提示同步改为提醒：改了代码、依赖或 Dockerfile 时应执行 `docker compose build bot && docker compose up -d bot`。

## 2026-05-08 — 插件中心协议探针 + Legacy 阻断 + 白名单锁定
- 插件 API 新增 `GET /api/admin/plugins/meta`，返回 `plugin_api_version`、`plugin_layout_version`、`build_commit`、`frontend_build_id`、`legacy_detected` 等运行时元信息，用于快速判断“代码已更新但容器未更新”。
- 插件中心接入 legacy 阻断：检测到 `plugins/` 根目录旧版单文件插件时，`/api/admin/plugins` 与插件详情/设置/启停接口直接返回阻断状态，避免出现“看起来可配但实际协议不兼容”的假状态。
- 插件列表与详情补齐 `config_status`（`ready|missing_schema|read_only|legacy_blocked`），并持续输出 `tier/locked/configurable`。
- 系统级插件策略硬化为白名单：仅 `chat`、`history_loader`、`vision` 可被视为 system/locked；其他插件即使声明 `tier=system` 也会降级为 user，防止误锁。
- `PluginBus` 与 `bot.py` 同步修复 manifest 应用链路：显式注册插件改为从 `plugin.py` 导入，确保运行时能稳定读取 `plugin.json` 元数据。
- Docker 构建新增插件布局门禁：`scripts/check_plugin_layout.py --strict`，发现 legacy 根目录插件文件时构建直接失败，避免旧格式再次进入生产镜像。

## 2026-05-08 — 知识库与知识图谱研究审计
- 新增 `docs/audits/knowledge-graph-rag-research-and-roadmap-2026-05-08.md`，基于 Omubot 当前知识库实现、论文与成熟项目源码，整理知识库/RAG/知识图谱改造路线。
- 本地审计快照固定在 `/private/tmp/omubot-kb-audit`，覆盖 Microsoft GraphRAG、LightRAG、HippoRAG、LlamaIndex、Haystack、Neo4j GraphRAG Python。
- 审计结论明确当前 P0 是修复 Admin 知识库搜索断链与结构化命中结果，而不是直接引入重型向量库或 Neo4j。
