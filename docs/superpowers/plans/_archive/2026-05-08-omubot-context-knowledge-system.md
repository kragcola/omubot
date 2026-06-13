# Omubot Context Knowledge System Progress

日期：2026-05-08

## 项目目标

把现有记忆卡片、文档知识库和未来知识图谱统一到一个可观测的上下文系统里。第一原则是不破坏生产记忆：`CardStore` 继续作为用户/群记忆的权威存储，知识库只负责文档知识，知识图谱只做可重建的派生事实层。

## 不可破坏约束

- 不物理迁移 `memory_cards`，不改变 `CardStore.add_card/get_entity_cards/supersede_card/expire_card` 语义。
- `KnowledgeStore/KnowledgeService` 不承载用户/群记忆作用域。
- `ContextService` 只做统一检索、重排、打包和调试出口。
- 默认栈保持轻量：SQLite + 本地 BM25/ngram；不默认引入向量数据库、Neo4j、FAISS、Redis。
- Kernel 不 import `services/context`、`services/knowledge`、`services/knowledge_graph`。
- 图谱事实必须可回滚、可拒绝、可重建，并引用 `card_id` 或 `chunk_id` 证据。

## 当前决策

`CardStore` 不迁移，只通过 adapter 接入 `ContextService`。现有记忆卡片仍是生产记忆的权威来源；知识库和知识图谱是并列能力，不替代它。

## 阶段状态

| 阶段 | 状态 | 目标 | 验收标准 |
| --- | --- | --- | --- |
| Phase 0 | done | 修复现有 Knowledge Admin 断链，生成结构化命中 | `/api/admin/knowledge` 能返回 `content/source/title/score/chunk_id`，旧 `retrieve()` 仍可用 |
| Phase 1 | done | 新增 `ContextHit` 与 `ContextService`，统一 memory/doc 检索出口 | `/api/admin/context/search` 能同时解释 memory/doc 命中 |
| Phase 2 | done | 重构 `services/knowledge` 为轻量七层知识库 | 支持递归 Markdown、include/exclude、显式 reindex、BM25/ngram 检索 |
| Phase 3 | done | 新增 `ContextPlugin`，统一 Prompt 注入 | `ContextPlugin` 已默认启用并接管动态上下文；MemoPlugin 保留稳定索引和提取/工具职责，KnowledgePlugin 保留索引和工具入口 |
| Phase 4 | done | 新增轻量 SQLite 知识图谱与候选治理 | 高置信自动 active，中置信进候选，低置信忽略，不进入 Prompt |
| Phase 5 | done | 增加检索评测、上下文调试与回归指标 | Admin Web 已能解释 memory/doc/graph 命中；最小评测集、主人真实感脱敏评测集、接管/回滚守卫、pack 预算、图谱治理入口、历史 scope 风险提示和指标面板已建立 |

## 未完成清单与推荐顺序

这部分是后续推进的权威 backlog。原则：先评测和可观测，再接管 Prompt，再做自动抽取；不要在没有评测基线前把 ContextPlugin 默认打开。

| 优先级 | 状态 | 项目 | 说明 | 验收标准 |
| --- | --- | --- | --- | --- |
| P0 | done | 建立上下文评测集 | 已新增 `services/context/eval.py`、`tests/fixtures/context_eval/basic.json` 和 `tests/test_context_eval.py`，可输出命中率、漏召、禁入误召、重复注入、pack 长度 | `.venv/bin/pytest tests/test_context_eval.py -q` 通过；后续只需持续补真实案例 |
| P0 | done | ContextPlugin 接管前评测 | 已补自动接管/回滚守卫、主人场景 fixture 和 pack 长度预算：接管时只注入一个“上下文资料”动态块，旧“知识库/记忆卡片”动态块被压住；关闭接管后旧路径恢复；私聊/群聊/其他群作用域隔离已有回归覆盖。后续只需持续沉淀真实脱敏聊天案例 | 覆盖群聊、私聊、知识库问题、无关问题；确认不漏关键记忆、不重复注入、不明显扩 Prompt |
| P0 | done | DeepSeek stable prefix 回归 | 已新增单测验证动态上下文进入 tail metadata，不污染 stable system prefix | `tests/test_prompt.py::test_deepseek_native_dynamic_context_stays_out_of_stable_system_prefix` 通过 |
| P1 | done | ContextPlugin 正式接管 Prompt | `plugins/context/config.default.json` 已默认 `enabled=true` 且 `takeover_dynamic_prompt=true`；动态上下文统一由 `ContextPlugin` 注入，MemoPlugin 保留稳定全局索引和提取/工具职责，KnowledgePlugin 保留索引和工具入口 | Prompt 中只有一个“上下文资料”动态块；关闭接管后测试验证旧路径可恢复 |
| P1 | done | 图谱自动候选抽取 | `ContextPlugin` 在本轮 pack 完成后，从 `memory_card` 和 `doc_chunk` 命中中抽取 subject/predicate/object，并交给图谱治理阈值处理，不影响本轮 Prompt | 高置信自动 active；中置信 pending；低置信忽略；每条事实带 `card_id` 或 `chunk_id` evidence；重复事实去重 |
| P1 | done | 图谱证据链与回滚 UI | 后端 relationship API 返回 evidence；新增 rollback/supersede；Web 图谱关系卡展示证据、取代关系、回滚备注和取代表单 | 管理员能看到事实来源证据，并能回滚或取代 active fact |
| P2 | done | 知识库 SQLite 持久化 chunk 索引 | 新增 `KnowledgeIndexStore`，持久化 source/chunk/hash；`KnowledgePlugin` 默认使用 `storage/knowledge_index.db`；重启后可恢复索引，reindex 时只重建 hash 变化的 source | 修改单文件后只更新相关 source；重启后可从持久索引恢复 |
| P2 | done | Admin 评测与指标面板 | `ContextService.metrics()` 与 `/api/admin/context/metrics` 输出最近 query、miss rate、命中来源比例、Prompt pack 长度、重复率；`/admin/knowledge` 新增“评测指标”页签 | `/admin/knowledge` 有“评测指标”页签，显示最近 query、miss、来源比例、pack 长度、重复率 |
| P2 | done | 历史图谱 scope 风险收尾 | 新增 scope risk API 与 Web 提示，列出旧版本迁移后仍为 `global/global` 且带 memory evidence 的 active fact | 管理员能在图谱页看到风险数量与前 5 条风险事实，并可直接回滚 |
| P3 | todo | Optional embedding/vector backend | 只作为可选增强，不进入默认 Docker；用于同义表达召回 | 未安装时自动降级 ngram；安装后可在评测集上证明召回提升 |

## 当前不做

- 不把 `CardStore` 物理迁移到知识库。
- 不让 KnowledgeStore 承载用户/群记忆作用域。
- 不默认引入 Neo4j、FAISS、Redis、向量数据库。
- 不在缺少评测结果前默认开启 ContextPlugin 接管；P0 评测守卫通过后，P1 已改为默认接管。
- 不让低置信图谱候选进入 Prompt。

## 变更记录

### 2026-05-08

- 创建本进度追踪文档。
- 开始 Phase 0：修复 Knowledge Admin 与结构化命中。
- 完成 Phase 0：
  - `services/knowledge` 拆出 `types.py / chunking.py / retrievers.py / service.py`
  - 新增 `KnowledgeHit` 与 `search_hits()`
  - `retrieve()` 保持返回 `list[str]`
  - `KnowledgePlugin` 启动后把运行时实例挂到 `ctx.knowledge_base`
  - Admin API 改为懒解析 `ctx` / `bus` 中的运行时知识库实例
- 完成 Phase 1：
  - 新增 `services/context`
  - `MemoryContextSource` 只读适配 `CardStore`
  - `KnowledgeContextSource` 只读适配 `KnowledgeService`
  - `ContextService.search()` 和 `build_prompt_context()` 可统一返回、打包 memory/doc 命中
  - 新增 `/api/admin/context/search` 与 `/api/admin/context/recent`
- 完成 Phase 2 第一版：
  - Markdown 索引支持递归扫描
  - 支持 include/exclude globs
  - 支持 source hash、source status、skipped reason
  - 搜索使用无依赖 BM25/ngram scorer
  - 新增 `/api/admin/knowledge/stats`、`/sources`、`/search`、`POST /reindex`
- 推进 Phase 3：
  - 新增系统级 `plugins/context`
  - 默认关闭，避免直接替换当前生产 Prompt 行为
  - 开启且 `takeover_dynamic_prompt=true` 后，MemoPlugin 跳过实体动态记忆注入，KnowledgePlugin 跳过文档 chunk 直接注入，由 ContextPlugin 统一注入 `上下文资料`
- 完成 Phase 4 第一版：
  - 新增 `services/knowledge_graph`
  - SQLite 存储 graph facts、evidence、extraction candidates
  - 高置信候选自动写入 active fact；中置信进入候选；低置信忽略
  - 新增图谱 entities / relationships / candidates / approve / reject API
- 推进 Phase 5：
  - ContextService 保存最近检索快照
  - Admin API 可调试最终 pack 文本和 memory/doc/graph 命中列表
  - 当时前端知识库治理页、评测集和自动抽取仍未实现；后续记录已完成这些收口项
- 继续推进 Phase 5 Web 治理台：
  - `/admin/knowledge` 从单搜索页升级为五页签控制台
  - `文档源`：展示索引来源、chunk 数、hash、跳过原因，支持重建索引
  - `搜索核对`：展示结构化文档命中、score、chunk_id、source/title
  - `上下文调试`：输入消息、用户 ID、群 ID，展示 memory/doc/graph 命中和最终 Prompt pack
  - `图谱关系`：展示 active graph facts 和实体列表
  - `候选队列`：展示 pending candidates，支持通过/拒绝
  - 当前 Web 已可用于 ContextPlugin 上线前人工评测；后续已补最小评测集和主人真实感脱敏评测集
- 修复 Web 治理台运行版本错位提示：
  - 审计确认当前 `qq-bot` 容器对新版知识/图谱 API 返回 404，根因是运行后端落后于前端构建
  - 前端对新版 API 404 增加兼容降级，不再误报“图谱信息加载失败”
  - 页面会明确提示需要重建/重启 Bot 才能启用完整图谱 API
- 补充后续 backlog：
  - 新增“未完成清单与推荐顺序”章节
  - 当时明确 ContextPlugin 接管、图谱自动抽取、评测集、DeepSeek stable prefix、持久化索引等尚未完成；后续已按 backlog 逐项关闭
  - 明确“当前不做”的边界，防止后续误把 CardStore 迁入知识库
- 继续推进 Phase 5 自动评测闸门：
  - 新增 `services/context/eval.py`
  - 新增 `ContextEvalCase / ContextHitExpectation / ContextEvalResult / ContextEvalSummary`
  - 支持从 JSON fixture 加载评测用例
  - 支持统计 pass rate、required hit recall、forbidden violations、duplicate hits、avg pack chars
  - 新增 `tests/fixtures/context_eval/basic.json`，覆盖 memory/doc/graph 三类命中与禁入内容
  - 新增 `tests/test_context_eval.py`，验证评测集通过路径与漏召/误召/重复注入失败路径
  - 新增 DeepSeek native stable prefix 回归测试，验证动态上下文变化不会进入 system prefix
- 继续推进 ContextPlugin 接管前评测：
  - `tests/fixtures/context_eval/basic.json` 新增无关问题案例，验证无关 query 不应误召记忆、文档或图谱事实
  - 新增 `tests/test_context_plugin.py`
  - 覆盖真实 `PluginBus.fire_on_pre_prompt()` 路径、manifest 权限检查和插件优先级
  - 验证 ContextPlugin 接管时只出现一个“上下文资料”动态块，不重复出现旧“知识库 / 记忆卡片”动态块
  - 验证关闭 ContextPlugin 接管后，旧 Memo/Knowledge 动态注入路径可恢复，保留回滚能力
- 继续扩充主人场景评测：
  - 新增 `tests/fixtures/context_eval/owner_scenarios.json`
  - 覆盖私聊用户记忆、群聊记忆、其他群同关键词记忆、知识库文档、图谱事实和无关问题
  - 新增断言：私聊不串入群记忆，群聊不串入用户私密记忆或其他群记忆
  - 新增断言：无关 query 不为了填充 Prompt 误召旧资料
- 补充 Prompt pack 长度预算：
  - `ContextEvalCase` 新增 `max_pack_chars`
  - `ContextEvalResult` 新增 `max_pack_chars / pack_budget_exceeded`
  - `ContextEvalSummary` 新增 `pack_budget_violations`
  - `basic.json` 与 `owner_scenarios.json` 为每个场景增加 pack 长度基线
  - 新增失败测试，验证上下文 pack 超出预算时评测会失败
- 完成 P1 ContextPlugin 正式接管：
  - `plugins/context/config.default.json` 默认启用统一上下文注入和动态上下文接管
  - `ContextPlugin` 默认从本轮 memory/doc/graph 命中统一注入 `上下文资料`
  - `MemoPlugin` 在接管模式下停止直接注入实体记忆动态块，但继续注入稳定全局索引
  - `KnowledgePlugin` 在接管模式下停止直接注入文档 chunk，但继续保留知识库服务和工具入口
- 完成 P1 图谱自动候选抽取：
  - `services/knowledge_graph/extractor.py` 从占位边界升级为轻量确定性抽取器
  - `KnowledgeGraphService.extract_from_context_hits()` 从 `memory_card/doc_chunk` 抽取事实候选
  - 图谱写入增加重复 active fact / pending candidate 去重
  - ContextPlugin 在 Prompt pack 完成后异步提交抽取结果，保证抽取不会影响本轮 Prompt
- 完成 P1 图谱证据链与回滚治理：
  - Graph fact 返回 evidence 列表
  - 新增 relationship detail / rollback / supersede API
  - rollback 会撤销当前 fact；若当前 fact supersedes 旧 fact，则恢复旧 fact
  - Web 图谱关系卡展示证据、来源、取代关系，并提供回滚与取代表单
- 完成 P2 知识库 SQLite 持久化索引：
  - 新增 `services/knowledge/store.py`
  - 新增 `KnowledgeIndexStore`，持久化 `knowledge_sources` 与 `knowledge_chunks`
  - `KnowledgeService` 支持 `index_db_path`
  - `KnowledgePlugin` 默认配置 `storage/knowledge_index.db`
  - 重启后可从 SQLite 恢复 chunk/source 索引
  - 显式 `reindex()` 会按 source hash 复用未变化 source，仅重切变化文件
- 完成 P2 Admin 评测与指标面板：
  - `ContextService` 最近检索快照新增 type/source 分布、duplicate_count、pack_chars、omitted_count
  - 新增 `ContextService.metrics()`
  - 新增 `/api/admin/context/metrics`
  - `/admin/knowledge` 新增 `评测指标` 页签，展示最近查询数、Miss 率、平均/最大 Pack、重复率、命中来源和命中类型
- 修复 P1/P2 审计发现的问题：
  - 图谱事实新增 `scope/scope_id`，从 `memory_card` 抽取时继承用户/群作用域，`GraphContextSource` 按当前私聊/群聊作用域过滤，避免派生事实跨用户/跨群泄漏
  - `ContextPlugin` 图谱自动抽取改为后台任务，不再阻塞本轮 Prompt 构建
  - 知识库文档目录缺失时会清理 SQLite 持久索引，避免重启后恢复已删除 chunk
  - `ContextService.metrics()` 修正 source 异常计数，一次查询只记录一次，避免 Miss 率被异常 source 虚高
- 完成非 P3 收尾：
  - 新增 `tests/fixtures/context_eval/owner_realistic.json`，覆盖更贴近日常主人使用的脱敏问法
  - 新增 `/api/admin/knowledge/graph/scope-risks`，列出历史 `global/global` 且带 memory evidence 的 active graph fact
  - `/admin/knowledge` 图谱页新增“作用域待查”计数和风险提示，可直接回滚风险事实
  - 清理追踪文档中早期“尚未实现/未完成”的历史描述，避免与当前状态表冲突
- 完成知识库运行启用与索引修复：
  - 新增运行时覆盖 `storage/plugins/config/knowledge.json`，启用 `knowledge` 文档知识库
  - 修复 Markdown 重复 `##` 标题导致 `knowledge_chunks.chunk_id` 唯一键冲突的问题
  - chunk_id 改为 `source + section number + heading`，同一文件重复标题可正常索引
  - 已重建并重启 `bot` 容器，运行日志确认 `knowledge base loaded | dir=docs chunks=257`
- 完成生产知识库收口：
  - 新增 `docs/knowledge/omubot/architecture.md`、`knowledge-system.md`、`admin.md` 三份生产知识文档
  - `plugins/knowledge/config.default.json` 与运行时覆盖改为默认扫描 `docs/knowledge`
  - 默认排除草稿、归档和 macOS `._*.md` 资源叉文件
  - `services/knowledge/retrievers.py` 增加中文疑问停用词过滤，降低“的/什么/怎么”等低信息词误召
  - `ContextPlugin` 增加 DEBUG 级上下文打包摘要，便于确认 doc_chunk 是否命中

## 验证记录

- `.venv/bin/pytest tests/test_knowledge.py -q`：`10 passed`
- `.venv/bin/pytest tests/test_card_store.py tests/test_retrieval.py tests/test_memo_tools.py tests/test_knowledge.py tests/test_context_service.py tests/test_knowledge_graph.py tests/test_prompt.py -q`：`111 passed`
- `.venv/bin/pytest tests/test_admin_api.py -k "memory or knowledge or context" -q`：`3 passed, 44 deselected`
- `.venv/bin/pytest tests/test_plugin_bus.py -q`：`45 passed`
- `.venv/bin/python -m py_compile services/knowledge/*.py services/context/*.py services/knowledge_graph/*.py plugins/context/*.py plugins/knowledge/plugin.py plugins/memo/plugin.py plugins/chat/plugin.py admin/routes/api/context.py admin/routes/api/knowledge.py`：通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`：通过
- `cd admin/frontend && npm run build`：通过，Vite 仅输出第三方 `#__PURE__` 注释提示
- `.venv/bin/pytest tests/test_knowledge.py tests/test_context_service.py tests/test_knowledge_graph.py tests/test_admin_api.py -k "knowledge or context" -q`：`18 passed, 45 deselected`
- `.venv/bin/pytest tests/test_admin_api.py -k "knowledge_graph or knowledge_api" -q`：`3 passed, 46 deselected`
- `.venv/bin/pytest tests/test_context_service.py tests/test_context_eval.py tests/test_prompt.py -q`：`17 passed`
- `.venv/bin/ruff check services/context/eval.py services/context/__init__.py tests/test_context_eval.py tests/test_prompt.py`：通过
- `.venv/bin/pytest tests/test_context_eval.py tests/test_context_plugin.py tests/test_prompt.py -q`：`16 passed`
- `.venv/bin/ruff check services/context/eval.py services/context/__init__.py tests/test_context_eval.py tests/test_context_plugin.py tests/test_prompt.py`：通过
- `.venv/bin/pytest tests/test_context_eval.py -q`：`3 passed`
- `.venv/bin/ruff check tests/test_context_eval.py services/context/eval.py`：通过
- `.venv/bin/pytest tests/test_context_eval.py -q`：`4 passed`
- `.venv/bin/ruff check services/context/eval.py tests/test_context_eval.py`：通过
- `.venv/bin/pytest tests/test_knowledge_graph.py tests/test_context_plugin.py tests/test_admin_api.py -k "knowledge_graph or context_plugin or graph" -q`：`11 passed, 47 deselected`
- `.venv/bin/pytest tests/test_context_service.py tests/test_context_eval.py tests/test_context_plugin.py tests/test_prompt.py tests/test_knowledge_graph.py tests/test_admin_api.py -k "context or knowledge_graph or graph or prompt" -q`：`32 passed, 45 deselected`
- `.venv/bin/ruff check services/knowledge_graph tests/test_knowledge_graph.py plugins/context/plugin.py tests/test_context_plugin.py admin/routes/api/knowledge.py plugins/chat/plugin.py`：通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`：通过
- `cd admin/frontend && npm run build`：通过，Vite 仅输出第三方 `#__PURE__` 注释提示
- `.venv/bin/pytest tests/test_knowledge.py tests/test_context_service.py tests/test_admin_api.py -k "knowledge or context" -q`：`21 passed, 45 deselected`
- `.venv/bin/ruff check services/knowledge services/context tests/test_knowledge.py tests/test_context_service.py admin/routes/api/context.py admin/routes/api/knowledge.py plugins/knowledge/plugin.py`：通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`：通过
- `.venv/bin/python -m py_compile services/knowledge/*.py services/context/*.py admin/routes/api/context.py plugins/knowledge/plugin.py`：通过
- `cd admin/frontend && npm run build`：通过，Vite 仅输出第三方 `#__PURE__` 注释提示
- `.venv/bin/pytest tests/test_context_service.py tests/test_context_eval.py tests/test_context_plugin.py tests/test_knowledge.py tests/test_knowledge_graph.py tests/test_admin_api.py -k "context or knowledge or graph" -q`：`37 passed, 45 deselected`
- `.venv/bin/ruff check services/context services/knowledge services/knowledge_graph plugins/context/plugin.py tests/test_context_service.py tests/test_context_plugin.py tests/test_knowledge.py tests/test_knowledge_graph.py`：通过
- `.venv/bin/python -m py_compile services/knowledge_graph/*.py services/context/*.py services/knowledge/*.py plugins/context/plugin.py`：通过
- 手工复现：`用户123 --喜欢-> 音游` 这类由用户记忆派生的图谱事实只在 `user_id=123` 私聊召回，其他用户/其他群查询不再召回
- `.venv/bin/pytest tests/test_context_service.py tests/test_context_eval.py tests/test_context_plugin.py tests/test_knowledge.py tests/test_knowledge_graph.py tests/test_admin_api.py -k "context or knowledge or graph" -q`：`39 passed, 45 deselected`
- `.venv/bin/ruff check services/context services/knowledge services/knowledge_graph plugins/context/plugin.py admin/routes/api/knowledge.py tests/test_context_eval.py tests/test_knowledge_graph.py tests/test_admin_api.py`：通过
- `.venv/bin/python -m py_compile services/knowledge_graph/*.py admin/routes/api/knowledge.py`：通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`：通过
- `cd admin/frontend && npm run build`：通过，Vite 仅输出第三方 `#__PURE__` 注释提示
- `.venv/bin/pytest tests/test_knowledge.py -q`：`14 passed`
- `.venv/bin/python -m py_compile services/knowledge/chunking.py tests/test_knowledge.py`：通过
- `docker compose build bot`：通过，插件布局自检通过
- `docker compose up -d --no-deps --force-recreate bot`：通过
- `docker compose logs --tail=140 bot`：确认 `knowledge base loaded | dir=docs chunks=257`，Bot 就绪并连接 NapCat
- 容器内手工验证 `KnowledgeBase.reload()`：`257` chunks，`28` sources，`index_persisted=True`
- `.venv/bin/pytest tests/test_knowledge.py -q`：`17 passed`
- `.venv/bin/python -m py_compile services/knowledge/retrievers.py plugins/context/plugin.py tests/test_knowledge.py`：通过
- 本地手工验证 `docs/knowledge`：`3` sources / `15` chunks；`omubot的系统架构是什么` top1 命中 `omubot/architecture.md`
- `.venv/bin/pytest tests/test_knowledge.py tests/test_context_service.py tests/test_context_eval.py -q`：`28 passed`
- `.venv/bin/ruff check services/knowledge/retrievers.py plugins/context/plugin.py tests/test_knowledge.py`：通过
- `docker compose build bot`：通过，插件布局自检通过
- `docker compose up -d --no-deps --force-recreate bot`：通过
- `docker compose logs --tail=140 bot`：确认 `knowledge base loaded | dir=docs/knowledge chunks=15`，Bot 就绪并连接 NapCat
- 容器内手工验证 `KnowledgeBase.reload()`：`15` chunks，`3` sources，架构 query top1 命中 `omubot/architecture.md`

## 风险与回滚

- 当前 Prompt 注入重复风险已有回归测试覆盖：ContextPlugin 接管时只允许一个“上下文资料”动态块；关闭接管后旧路径可恢复。
- `KnowledgePlugin` 可能晚于 Admin Router 初始化，因此 Admin API 必须懒解析运行时实例，不能只读取启动时快照。
- 图谱阶段只能作为派生层上线，不能成为 CardStore 或 KnowledgeBase 的写入主路径。
- 图谱事实已补作用域隔离；历史 DB 中旧 facts 会通过迁移列默认标为 `global/global`，如其中包含由私有记忆错误生成的旧事实，需要在 Admin 图谱治理页回滚或清理。
- `ContextPlugin` 现已默认启用并接管动态上下文；若出现异常，可通过插件配置关闭 `enabled` 或 `takeover_dynamic_prompt` 回滚到旧 Memo/Knowledge 动态注入路径。
- 当前图谱已接入轻量确定性自动抽取器，但不是 LLM 抽取器；复杂事实仍需要未来更强抽取器或人工治理。
- 知识库前端已接入来源管理、图谱候选、图谱证据/回滚和上下文调试界面；当前已有最小自动评测集，但还缺少真实聊天案例扩充和命中质量曲线。
- 如果页面提示“当前运行后端还没有新版接口”，不是图谱数据损坏，而是容器尚未重建到包含新版 API 的后端。
