# Omubot 知识库模块审计表

审计日期：2026-05-08

审计范围：`plugins/knowledge/`、`services/knowledge/`、`admin/routes/api/knowledge.py`、`admin/frontend/src/views/knowledge/KnowledgeView.vue`、`tests/test_knowledge.py`、相关启动与 Prompt 注入链路。

## 一、结论摘要

| 维度 | 当前状态 | 证据 | 结论 |
| --- | --- | --- | --- |
| 核心检索服务 | 可用 | `services/knowledge/__init__.py` 提供 `reload()` 与 `retrieve()`；`tests/test_knowledge.py` 10 个测试通过 | 倒排索引检索本体可工作 |
| 插件注入链路 | 条件可用 | `KnowledgePlugin.on_startup()` 读取配置并构建 `KnowledgeBase`；`on_pre_prompt()` 注入 `dynamic` PromptBlock | 只有插件配置 `enabled=true` 且重启后才会注入 |
| 默认启用状态 | 默认关闭 | `plugins/knowledge/config.default.json` 中 `enabled=false` | 新部署默认不会向聊天注入知识库 |
| Admin 搜索页 | 当前断裂 | Admin API 读取 `ctx.knowledge_base`，但插件未写入该字段；API 调用 `knowledge_base.search()`，服务实际只有 `retrieve()` | `/admin/knowledge` 多数情况下只能显示 0 或空结果 |
| 文档覆盖范围 | 只扫一级 Markdown | `KnowledgeBase.reload()` 使用 `docs_dir.glob("*.md")` | `docs/wiki/`、`docs/audits/` 等子目录不会进入索引 |
| Prompt 缓存友好性 | 已改善 | `KnowledgePlugin` 注入 `position="dynamic"`；DeepSeek native 下动态块进入 tail metadata | 不污染 DeepSeek 稳定 system 前缀 |
| Web 配置治理 | 已具备基础 | `plugin.json` manifest v3；`config.default.json` 与 `config.schema.json` 存在 | 可在插件中心配置，但需重启生效 |

## 二、当前工作流拆解表

| 阶段 | 入口/触发 | 输入 | 核心处理 | 输出 | 当前问题 | 建议 |
| --- | --- | --- | --- | --- | --- | --- |
| 1. 插件发现 | `bot.py` 调用 `PluginBus.discover_plugins("plugins")` | `plugins/knowledge/plugin.py` 与 `plugin.json` | 目录插件被扫描，实例化 `KnowledgePlugin`，manifest 覆盖 name、权限、配置规格等元数据 | 插件注册到 PluginBus，优先级为 8 | 依赖自动发现，未在 `bot.py` 显式注册；本身没问题 | 保持目录插件发现即可 |
| 2. 配置读取 | `KnowledgePlugin.on_startup()` | `plugins/knowledge/config.default.json` 与 `storage/plugins/config/knowledge.json` 覆盖 | `load_plugin_config("plugins/knowledge/config.default.json", _Cfg)` 合并默认值与运行时覆盖 | 得到 `enabled / dir / max_chunks` | 默认 `enabled=false`，用户容易误以为知识库页会自动工作 | Web 配置页说明“启用后需重启才会注入聊天” |
| 3. 索引构建 | 插件启动且 `enabled=true` | `cfg.dir`，默认 `docs` | `KnowledgeBase(docs_dir=cfg.dir).reload()` 清空旧索引，扫描一级 `*.md`，按 `##` 标题分块 | `_chunks` 与 `_index` 内存索引 | 只扫描一级 Markdown，不递归；无法索引 `docs/wiki`、`docs/audits` | 增加可配置 `recursive=true` 或明确 UI 提示只扫一级目录 |
| 4. 分词 | `KnowledgeBase._add_to_index()` | 每个 Markdown chunk 文本 | `_tokenize()` 生成中文单字、中文二元组、英文词 | token -> chunk_id 倒排表 | 无 stopword、无权重归一、无标题加权；短 query 容易误命中 | 后续加入 ngram scorer、标题权重、停用词 |
| 5. 聊天触发 | LLM 主流程构建 prompt 前 | `PromptContext.conversation_text` | `PluginBus.fire_on_pre_prompt()` 调用有 `prompt` 权限的插件 | 收集插件贡献的 `PromptBlock` | 若知识库插件未启用或无文本，直接跳过 | 保持当前轻量策略 |
| 6. 知识检索 | `KnowledgePlugin.on_pre_prompt()` | 最近对话文本 `conversation_text` | `KnowledgeBase.retrieve(query, top_k=max_chunks)` 计算 token 交集分数并排序 | 最多 `max_chunks` 个 chunk 内容 | 没有来源、标题、分数，LLM 只能看到正文 | 返回结构化结果：source、title、score、content |
| 7. Prompt 注入 | 检索结果非空 | chunk 内容列表 | 用 `\n---\n` 拼接，`label="知识库"`，`position="dynamic"` | 动态 PromptBlock | 对 Anthropic/OpenAI 会进入 system 动态块；对 DeepSeek native 会进入本轮 tail metadata | 当前位置正确；可增加 token 长度上限 |
| 8. DeepSeek V4 路径 | `deepseek_native_main=true` | `plugin_dynamic` | `LLMClient` 不把 dynamic 放入 system，而是追加到最后 user turn 的 `<turn_meta>` | 知识库不破坏稳定 system hash | 已符合前缀缓存优化目标 | 保持知识检索结果 `dynamic`，不要改回 `static` |
| 9. Admin API 统计 | `GET /api/admin/knowledge` | `knowledge_base` 参数 | 若对象存在则调用 `reload()` 得到 count | `{"entry_count": count, "results": []}` | `ctx.knowledge_base` 当前没有被知识库插件设置，通常为 `None` | 插件启动时设置 `ctx.knowledge_base = self._kb` 或 API 从 bus 查插件 |
| 10. Admin API 搜索 | `GET /api/admin/knowledge?q=...` | 空格切分后的关键词 | 代码尝试调用 `knowledge_base.search(keywords)` | 预期返回 content/source | `KnowledgeBase` 没有 `search()`；实际检索 API 是 `retrieve()` | 修 API 调用 `retrieve()`，或给 `KnowledgeBase` 增加 `search()` 兼容方法 |
| 11. Admin 前端 | `/admin/knowledge` | API 返回的 `entry_count/results` | 展示 4 个指标卡、搜索框、结果列表 | 用户可抽查命中内容 | 前端文案说“最多 20 条”，后端真实插件检索 `max_chunks` 默认 3；Admin API 断裂导致结果空 | 先修 API，再把页面定位改成“搜索输入 -> 结果核对” |
| 12. 测试覆盖 | `tests/test_knowledge.py` | 临时 docs 目录 | 覆盖空目录、缺失目录、分块、中文/英文检索、top_k | 10 tests passed | 未覆盖 Admin API、插件 on_startup/on_pre_prompt、ctx 暴露、递归扫描 | 增加 API 与插件集成测试 |

## 三、数据结构与接口表

| 对象/接口 | 当前字段/方法 | 用途 | 局限 |
| --- | --- | --- | --- |
| `KnowledgeBase._chunks` | `dict[str, tuple[str, str]]` | 保存 chunk_id 到 `(title, content)` | `retrieve()` 只返回 content，title/source 没有外露 |
| `KnowledgeBase._index` | `dict[str, set[str]]` | token 到 chunk_id 倒排索引 | 无权重、无分数归一、无更新时间 |
| `KnowledgeBase.reload()` | 返回 chunk 数 | 重建索引 | 每次 Admin 请求都 reload 会有额外 IO |
| `KnowledgeBase.retrieve(query, top_k)` | 返回 `list[str]` | 聊天注入使用 | 不返回 source/score，Admin 难以审计结果 |
| `GET /api/admin/knowledge` | `entry_count/results` | Admin 统计与搜索 | 目前调用不存在的 `search()`；且没有错误外显 |
| `plugins/knowledge/config.default.json` | `enabled/dir/max_chunks` | 插件配置默认值 | 缺少递归、文件类型、最大 chunk 字符数、最小分数 |
| `plugins/knowledge/config.schema.json` | JSON Schema | Web 配置渲染 | 只有基础字段，缺少说明、推荐值、风险提示 |

## 四、风险与优先级表

| 优先级 | 问题 | 影响 | 证据 | 修复建议 |
| --- | --- | --- | --- | --- |
| P0 | Admin 知识库页与运行时知识库实例断链 | 用户以为知识库为空或搜索不可用 | `admin/__init__.py` 读取 `ctx.knowledge_base`；`KnowledgePlugin` 未设置该字段 | `on_startup()` 中 `ctx.knowledge_base = self._kb`，或 Admin API 通过 bus 获取 `knowledge` 插件实例 |
| P0 | Admin API 调用不存在的 `search()` | 即使传入 `KnowledgeBase` 也无法返回结果 | `admin/routes/api/knowledge.py` 使用 `hasattr(..., "search")`；本地探针 `has_search=False` | 改为 `retrieve(q, top_k=20)`；长期新增结构化 `search()` |
| P1 | 只扫描一级 Markdown | 子目录文档无法进入知识库 | `reload()` 使用 `glob("*.md")`；当前 `docs` 一级 9 个 md，二级合计 22 个 md | 增加 `recursive` 配置，默认可先保持 false 并在 UI 明示 |
| P1 | 检索结果无来源和分数 | 难排查误命中，也难在 Web 核对 | `retrieve()` 返回 `list[str]` | 返回 `KnowledgeHit(content, source, title, score)` 或 dict |
| P1 | Admin 请求每次 reload | 文档多后页面慢，且读写行为混杂 | API 中每次请求都 `knowledge_base.reload()` | 拆分 `GET stats` 与 `POST reload`，搜索不自动 reload |
| P2 | 默认关闭但页面没有解释 | 新手打开知识库页看到 0 不知道原因 | `config.default.json enabled=false` | 页面显示插件启用状态与配置入口 |
| P2 | 文档仍提 `/knowledge` 命令 | 误导用户以为有命令检索 | `README.md`、`CHANGELOG.md` 提及 `/knowledge` | 更新文档或补命令 |
| P2 | 缺少 prompt 注入集成测试 | 未来可能误把知识库改成 `static` 影响缓存 | 当前测试只覆盖 `KnowledgeBase` | 增加 `KnowledgePlugin.on_pre_prompt()` 测试，断言 `position="dynamic"` |

## 五、建议改进路线

| 阶段 | 目标 | 改动 | 验收 |
| --- | --- | --- | --- |
| Step 1 | 修复 Admin 可观测性 | 暴露运行时 `KnowledgeBase`；Admin API 改用 `retrieve()` 或新增 `search()` | `/api/admin/knowledge?q=配置` 能返回结果 |
| Step 2 | 统一搜索结果结构 | 引入 `KnowledgeHit`：content、source、title、score、chunk_id | Web 能显示来源和命中分数 |
| Step 3 | 减少不必要 reload | 搜索不自动 reload；新增手动 reload 接口 | 页面刷新统计和搜索响应更稳定 |
| Step 4 | 扩展索引配置 | 增加 recursive、include_globs、exclude_globs、max_chunk_chars、min_score | 插件配置页可解释每项影响 |
| Step 5 | 提升检索质量 | 使用已有 `SimilarityProvider` ngram 后端做 rerank，不引入 embedding 默认依赖 | 不改变 Docker 轻量默认，但降低误命中 |
| Step 6 | 完善测试 | 覆盖 Admin API、插件启动、Prompt 注入、DeepSeek tail metadata | 知识库链路不再只靠服务单测保障 |

## 六、已执行验证

| 验证项 | 命令/探针 | 结果 |
| --- | --- | --- |
| 知识库服务单测 | `.venv/bin/pytest tests/test_knowledge.py -q` | `10 passed` |
| 当前服务接口能力 | Python 探针检查 `KnowledgeBase` 方法 | `has_reload=True`、`has_retrieve=True`、`has_search=False` |
| 当前一级 docs 索引规模 | `KnowledgeBase("docs").reload()` | `93` 个 chunk |
| 一级/二级 Markdown 数量 | `find docs -maxdepth ... -name '*.md'` | 一级 `9` 个，二级 `22` 个 |

## 七、总体判断

当前知识库不是重型 RAG，而是一个轻量、可关闭、对 Prompt 缓存相对友好的关键词检索注入模块。它的服务层本体简单可靠，但 Admin 可观测链路明显落后于运行时实现：实例没有暴露给 Admin，接口还在调用旧式 `search()`。优先修 P0 两项后，知识库页面才能真正承担“搜索输入 -> 结果核对”的职责。
