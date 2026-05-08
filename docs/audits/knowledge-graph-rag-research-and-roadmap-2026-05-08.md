# Omubot 知识库与知识图谱研究审计及路线图

审计日期：2026-05-08

审计目标：基于当前 Omubot 知识库实现，结合论文和成熟知识库/知识图谱项目源码，给出可学习机制、修改建议、长期发展方向、比对清单与后续审计方案。本轮不是只读项目介绍，而是拉取成熟项目到本地并抽样阅读关键实现。

## 一、结论摘要

| 结论 | 当前证据 | 建议 |
| --- | --- | --- |
| 当前 Omubot 知识库是轻量倒排索引，不是 RAG/知识图谱系统 | `services/knowledge/__init__.py` 只维护 `_chunks` 与 `_index`；`retrieve()` 返回 `list[str]` | 先补结构化命中结果与 Admin 可观测性，再升级检索质量 |
| 运行时注入链路比 Admin 搜索链路更接近可用 | `KnowledgePlugin.on_pre_prompt()` 用 `retrieve()` 注入 `dynamic` 块；Admin API 仍在找不存在的 `search()` | P0 修 Admin API 和运行时实例暴露 |
| 当前最大短板不是“没有向量库”，而是没有文档模型、命中元数据、检索评估和上下文预算 | 无 `KnowledgeDocument/Chunk/Hit`，无 source/score，搜索每次 reload | 先建立检索契约，再接 BM25、rerank、graph、vector |
| 成熟项目共同点是“检索结果必须可解释、可组合、可评估” | LlamaIndex `NodeWithScore`、Neo4j `RetrieverResultItem`、Haystack `DocumentStore` 都把内容、分数、元数据拆开 | Omubot 应先引入 `KnowledgeHit`，让 Web 和 Prompt 注入共用同一套结果 |
| 知识图谱路线有价值，但不应第一步就上 Neo4j/GraphRAG 全家桶 | GraphRAG/LightRAG/HippoRAG 都有抽取、图存储、社区/关系检索、预算控制等复杂链路 | 推荐 SQLite/NetworkX 原型优先，Neo4j/向量库作为可选后端 |

## 二、本地审计快照

| 项目 | 本地路径 | Commit | 审计重点 |
| --- | --- | --- | --- |
| Microsoft GraphRAG | `/private/tmp/omubot-kb-audit/graphrag` | `0da2a4d` | local/global/DRIFT 查询工厂、社区报告、上下文构造 |
| LightRAG | `/private/tmp/omubot-kb-audit/LightRAG` | `964f26e` | `QueryParam.mode`、KG/vector 混合检索、实体关系抽取与 token 预算 |
| HippoRAG | `/private/tmp/omubot-kb-audit/HippoRAG` | `d437bfb` | OpenIE、实体/事实/段落编码、Personalized PageRank rerank |
| LlamaIndex | `/private/tmp/omubot-kb-audit/llama_index` | `79cddb5` | `BaseRetriever`、`NodeWithScore`、retriever/query engine/synthesizer 分层 |
| Haystack | `/private/tmp/omubot-kb-audit/haystack` | `8defe4f` | `DocumentStore` 协议、BM25 retriever、pipeline/ranker 分层 |
| Neo4j GraphRAG Python | `/private/tmp/omubot-kb-audit/neo4j-graphrag-python` | `80533ad` | retriever hierarchy、hybrid/text2cypher/vector result types |

## 三、论文与机制证据表

| 论文/方向 | 关键机制 | 对 Omubot 的启发 | 不建议直接照搬的点 |
| --- | --- | --- | --- |
| GraphRAG, From Local to Global | 从文档抽取实体/关系图，做社区检测和社区报告；查询分 local/global 两类 | 群聊 bot 的知识库需要支持“局部事实问答”和“全局概览总结”两种模式 | 全量 LLM 抽取和社区检测成本高，不适合作为默认启动路径 |
| LightRAG | 以 local/global/hybrid/mix/naive 多模式组织 KG 与向量检索，并统一 token 预算 | Omubot 可引入轻量 query router：短事实走 local，概览走 global，普通聊天走 naive/BM25 | 不应默认引入多存储后端；先做接口，不绑定依赖 |
| HippoRAG | OpenIE 提取实体和三元组，分别编码 passage/entity/fact，再用 PPR 重排 | 对“谁和谁有什么关系、群内梗从哪里来”很适合；可用于长期记忆和知识库融合 | OpenIE 批处理昂贵，且需要缓存、纠错、回滚和人工审计 |
| RAPTOR | 对文本块递归聚类并生成摘要树，查询时检索不同层级节点 | 文档多时可先生成章节/文档摘要，降低上下文塞入成本 | 摘要会成为新知识源，必须保存版本和来源，避免不可追溯 |
| Self-RAG | 模型在生成过程中学习是否检索、是否支持、是否足够 | Omubot 可以先实现低成本“是否需要知识库”的检索门控和回答自检 | 训练式 Self-RAG 不现实；只借鉴反思标签思想 |
| CRAG | 检索后评估结果质量，必要时纠正或补充检索 | 可给知识库命中加“可信/不确定/需联网”状态，减少错注入 | 不应让 bot 自动联网扩大答案，除非用户配置允许 |
| Lost in the Middle | 长上下文中模型对中间内容利用较差，开头和结尾信息更易被用到 | 知识片段需要按重要度压缩和摆放；DeepSeek 模式下继续放 tail metadata | 不应盲目增加 top_k，更多片段可能反而降低可用性 |
| HyDE | 用 LLM 生成假设答案/文档，再用该文本做检索 | 可以用于低频“深搜”按钮或 Admin 调试，不适合每轮聊天默认触发 | 会增加成本和延迟，也可能把错误假设带进检索 |

## 四、当前 Omubot 工作流与差距

| 阶段 | 当前实现 | 证据 | 问题 | 优先修复 |
| --- | --- | --- | --- | --- |
| 文档读取 | 只扫描 `docs_dir.glob("*.md")` | `services/knowledge/__init__.py:63` | 不递归，`docs/wiki`、`docs/audits` 等不会进索引 | P1 增加 `recursive/include_globs/exclude_globs` |
| 切块 | 按 `##` 标题切 chunk | `services/knowledge/__init__.py:129-155` | 无最大长度、无 parent/child、无 source metadata | P1 引入 `KnowledgeChunk` |
| 分词 | 中文单字/二元组加英文词 | `services/knowledge/__init__.py:9-24` | 无 stopword、无 BM25、无标题权重 | P2 引入 BM25/ngram scorer |
| 检索 | token 交集计数排序 | `services/knowledge/__init__.py:89-103` | 只返回正文，不返回来源、分数、chunk_id | P0/P1 引入 `KnowledgeHit` |
| 插件注入 | `on_pre_prompt()` 检索最近对话并加入 `dynamic` block | `plugins/knowledge/plugin.py:48-59` | Prompt 注入链路可用，但不可观测 | P1 记录最近命中与 token 预算 |
| 默认配置 | `enabled=false`、`dir=docs`、`max_chunks=3` | `plugins/knowledge/config.default.json` | 新手容易误以为知识库页自动工作 | P1 在 Web 提示启用状态和重启需求 |
| Admin 搜索 | `GET /knowledge` 尝试调用 `search()` | `admin/routes/api/knowledge.py:28-35` | 服务没有 `search()`，通常返回空结果 | P0 修为 `retrieve/search_hits` |
| 运行时实例 | API 依赖传入 `knowledge_base` | `admin/routes/api/__init__.py` | 插件未把 `_kb` 暴露给 Admin | P0 通过 ctx 或 bus 暴露 |
| 上下文缓存 | 知识结果是 `dynamic` | `plugins/knowledge/plugin.py:55-59` | 这点是正确的，DeepSeek 模式不应改回 static | 保持 |

## 五、成熟项目源码比对清单

| 能力 | Omubot 当前 | GraphRAG | LightRAG | HippoRAG | LlamaIndex/Haystack/Neo4j | Omubot 可学习点 |
| --- | --- | --- | --- | --- | --- | --- |
| 结果对象 | `list[str]` | `SearchResult` 带 context records/token 统计 | query context 组合实体、关系、chunks | `QuerySolution` 带 docs/scores | `NodeWithScore`、`Document`、`RetrieverResultItem` | 必须先有 `KnowledgeHit(content, source, score, metadata)` |
| 检索分层 | 无 | local/global/basic/drift | local/global/hybrid/naive/mix | dense/fact/graph fallback | retriever/query engine/ranker 分离 | 引入 `KnowledgeRetriever` 接口和 query mode |
| 文档存储 | 内存 dict | text_units/entities/relationships/reports | KV/vector/graph/doc status 多 namespace | chunk/entity/fact embedding stores + graph | DocumentStore protocol | 建立文档、chunk、索引状态表 |
| 图结构 | 无 | entity/relationship/community | graph storage + vector storage | OpenIE triples + igraph | Neo4j graph retrievers | 先用 SQLite/NetworkX 做可审计图原型 |
| 重排 | 无 | 上下文构造阶段按实体/关系/社区权重 | rerank 与 token budget | PPR + dense fallback | ranker/postprocessor | P2 可先做 BM25 + diversity + sentence window |
| 上下文预算 | 只靠 `max_chunks` | `max_context_tokens` | entity/relation/total token budget | top_k + rerank | synthesizer/postprocessor | 用 token 预算替代单纯片段数量 |
| 全局总结 | 无 | community reports map-reduce | global mode | 图上事实聚合 | summary/tree indices | 对长文档加“文档摘要/章节摘要”层 |
| 质量评估 | 无 | llm_calls/token/callbacks | cache/status/logging | recall/QA eval | callbacks/tracing | 加离线 golden queries 和 Admin 命中核对 |
| 更新删除 | reload 全量重建 | indexing output artifacts | doc status, incremental insert/delete | delete docs with graph cleanup | duplicate policy/delete API | 增量索引和版本化快照是中长期必需 |

## 六、可学习机制拆解

| 来源 | 源码证据 | 机制 | 改造建议 |
| --- | --- | --- | --- |
| GraphRAG | `query/factory.py` 中 local/global/drift engine 分开创建 | 查询模式不是一个统一 top_k，而是按任务选择不同上下文构造器 | Omubot 增加 `mode=auto|keyword|summary|graph|hybrid`，先不改变默认 |
| GraphRAG | `local_search/search.py` 先 `build_context()` 再生成回答 | 检索和生成分离，context records 可回调观察 | KnowledgePlugin 只负责拿 `KnowledgeHit` 并注入，Admin 复用同一结果 |
| GraphRAG | `global_search/search.py` map-reduce community reports | 全局性问题不靠塞更多 chunks，而靠摘要层 | 为 docs 建文档/目录摘要表，用于“这份文档讲什么” |
| LightRAG | `QueryParam.mode` 支持 local/global/hybrid/naive/mix | 同一知识库可以有多种检索路径 | Omubot 加轻量 QueryRouter，不让所有问题走同一 scorer |
| LightRAG | `max_entity_tokens/max_relation_tokens/max_total_tokens` | 上下文预算分桶，不同内容竞争预算 | Omubot 的 `max_chunks` 升级为 `max_context_tokens` 和 per-section budget |
| LightRAG | entity/relation 重建逻辑保留 source_id/file_path | 抽取结果必须可追溯到源 chunk | 图谱节点、关系、摘要都必须记录 source chunk ids |
| HippoRAG | `openie_openai.py` NER 后再抽 triple | 知识图谱构建应分步骤缓存，便于修复中间产物 | 如果做图谱，拆成 `extract_entities`、`extract_relations`、`approve`、`publish` |
| HippoRAG | `retrieve()` 无 facts 时 fallback dense retrieval | 图检索不是万能，必须有失败降级 | Omubot graph mode 无命中时回退 BM25/keyword |
| LlamaIndex | `BaseRetriever.retrieve()` 返回 `NodeWithScore` | retriever 输出标准化，后续 postprocessor/synthesizer 可替换 | `KnowledgeHit` 是下一阶段最关键抽象 |
| Haystack | `DocumentStore` 定义 count/filter/write/delete | 数据层先抽协议，检索器依赖协议而非文件路径 | `KnowledgeStore` 应支持 stats/filter/reindex/delete |
| Neo4j GraphRAG | `RetrieverResultItem(content, metadata)` | graph 查询也要输出可被 LLM 消费的上下文和调试元数据 | 不论 backend 多复杂，最后都统一为 `KnowledgeHit` |

## 七、目标架构建议

如果不被当前框架约束，理想知识库可拆成 7 层。即便未来大重构，也建议保持这 7 层边界清晰。

| 层 | 职责 | 第一版轻量实现 | 进阶实现 |
| --- | --- | --- | --- |
| Source Registry | 管理文档源、版本、权限、更新时间 | 扫描本地 Markdown，保存 source hash | Web 上传、目录 watcher、远程同步 |
| Parser | 把源文件解析成结构化文档 | Markdown heading parser | PDF/HTML/图片 OCR/代码文档解析 |
| Chunker | 切块并保留父子关系 | heading chunk + max chars | semantic chunk、sentence window、parent-child |
| Store | 持久化 document/chunk/index 状态 | SQLite | SQLite + vector store + graph store |
| Retriever | 多路召回 | keyword/BM25/ngram | vector/hybrid/graph/Text2Cypher |
| Ranker/Packer | 重排与上下文打包 | score + diversity + token budget | cross-encoder、PPR、lost-in-middle aware packing |
| Governance | 可观测、评估、回滚、审核 | Admin 搜索核对、golden queries | 抽取审核、图谱编辑、自动评测 dashboard |

## 八、推荐发展路线

| 阶段 | 目标 | 关键改动 | 验收指标 |
| --- | --- | --- | --- |
| P0 断链修复 | 让当前知识库页真实可用 | Admin API 改用 `retrieve()` 或新增 `search_hits()`；插件启动后暴露 KB 实例；搜索不吞错 | `/api/admin/knowledge?q=配置` 返回非空结构化结果 |
| P1 检索契约 | 统一运行时和 Web 的知识命中对象 | 新增 `KnowledgeDocument/KnowledgeChunk/KnowledgeHit`；返回 source/title/score/chunk_id | Web 能显示来源、分数、命中文本和 chunk 标识 |
| P1 索引治理 | 让文档范围和索引状态可解释 | 支持 recursive/include/exclude/max_chunk_chars；保存 last_indexed_at/hash/chunk_count | Admin 能看到哪些文件被索引、哪些被跳过 |
| P2 质量提升 | 不引入重依赖也提升命中质量 | BM25 或 ngram scorer、标题权重、stoplist、min_score、diversity rerank | golden queries 的 top3 命中率提升 |
| P2 上下文打包 | 降低错注入和长上下文浪费 | token budget、source 去重、句窗/父块补全、结果压缩 | 每轮知识注入 token 可控，误命中更少 |
| P3 轻量语义 | 可选语义增强但不默认重依赖 | 接入 `SimilarityProvider`，支持 optional embedding backend | 未安装 embedding 时安全降级 |
| P3 摘要层 | 支持全局性问题 | 建文档摘要、章节摘要、目录摘要；摘要版本可回滚 | “某文档总体讲什么”无需塞大量 chunks |
| P4 轻量知识图谱 | 支持实体关系查询 | SQLite/NetworkX 存 entity/relation/evidence；人工审核发布 | Web 可查实体、关系、来源证据 |
| P5 GraphRAG/LightRAG 后端 | 面向大规模文档和复杂问答 | 可选 graph/vector backend；query router 支持 local/global/hybrid | 大文档集下 global 问题表现优于 keyword baseline |
| P6 知识治理平台 | 把知识库变成可维护系统 | 抽取队列、冲突检测、过期提醒、golden eval、回滚 | 每次索引或模型变更都有可比较评测报告 |

## 九、Omubot 可重构版本接口草案

```python
from dataclasses import dataclass, field
from typing import Any, Literal

@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    source_path: str
    title: str
    content_hash: str
    updated_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    document_id: str
    title: str
    text: str
    start_line: int | None = None
    end_line: int | None = None
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class KnowledgeHit:
    chunk_id: str
    content: str
    score: float
    source: str
    title: str
    retriever: str
    metadata: dict[str, Any] = field(default_factory=dict)

class KnowledgeRetriever:
    def retrieve(
        self,
        query: str,
        *,
        mode: Literal["auto", "keyword", "semantic", "graph", "hybrid"] = "auto",
        top_k: int = 5,
        token_budget: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[KnowledgeHit]:
        ...
```

设计要点：

| 要点 | 原因 |
| --- | --- |
| `KnowledgeHit` 必须是所有后端统一出口 | Admin、Prompt 注入、评测、日志都能复用 |
| `retriever` 字段必须保留 | 方便比较 keyword/BM25/vector/graph 的效果 |
| `metadata` 允许扩展 | GraphRAG/Neo4j 可放 entity/path/cypher，BM25 可放 matched_terms |
| `source/title/score` 不可省略 | 用户和开发者需要解释为什么命中 |
| `token_budget` 优先于 `max_chunks` | 避免 top_k 看似少但 chunk 过长 |

## 十、比对清单

后续每引入一个知识库或图谱方案，都按这张表审计，避免“看起来很强但不可维护”。

| 审计项 | 必查问题 | Omubot 当前状态 | 目标状态 |
| --- | --- | --- | --- |
| Ingestion | 支持哪些文件？是否递归？是否记录 hash？ | 一级 Markdown | 可配置路径、递归、hash、跳过原因 |
| Chunking | chunk 是否有 source、line、parent？ | 无 source 外露 | chunk 具备完整元数据 |
| Indexing | 全量还是增量？失败能否恢复？ | 全量 reload | 增量索引，有状态表 |
| Retrieval | 支持哪些模式？是否有分数？ | keyword 交集，无分数外露 | keyword/BM25/semantic/graph 可插拔 |
| Rerank | 是否能降低误命中？ | 无 | 标题权重、BM25、diversity、可选 rerank |
| Context Packing | 是否按 token 预算？是否考虑位置效应？ | `max_chunks` | token budget + tail metadata + source 去重 |
| Graph | 节点/关系如何抽取、审核、回滚？ | 无 | 抽取队列和 evidence-based graph |
| Observability | 能否解释每次命中？ | Admin 断链 | 最近命中、score、source、tokens |
| Evaluation | 有无 golden queries？ | 无 | recall@k、MRR、人工通过率 |
| Cost | 每次查询/索引消耗多少？ | 很低但效果有限 | 每个 backend 记录 token/耗时/cache |
| Safety | 是否会注入未审核内容？ | 本地 docs | 外部来源需审核和来源展示 |
| Web UX | 用户能否一眼知道知识库是否启用？ | 不够清晰 | 启用状态、索引状态、搜索核对优先 |

## 十一、详细审计方案

| 审计阶段 | 操作 | 产物 | 通过标准 |
| --- | --- | --- | --- |
| A. Baseline | 固定当前 `KnowledgeBase` 对 30 个查询的 top3 结果 | `storage/evals/knowledge/baseline.jsonl` | 每条有 query、hits、source、人工标签 |
| B. 数据范围 | 扫描 docs 目录一级/递归文件数、跳过文件、chunk 数 | 索引覆盖报告 | 覆盖率可解释，非 Markdown 跳过有原因 |
| C. 检索质量 | 比较 keyword、BM25、ngram、semantic top_k | recall@1/3/5、MRR、误命中率 | 新方案显著优于 baseline 才合并 |
| D. Prompt 效果 | 对相同 query 比较注入前后回答 | 人工评分和引用正确率 | 注入知识不增加明显幻觉 |
| E. 缓存影响 | DeepSeek/OpenAI/Anthropic 下记录 system hash 和 tail metadata | cache hit/miss 与 prompt 结构报告 | 知识结果不污染稳定 system prefix |
| F. 图谱候选 | 抽样 20 个文档 chunk 做 entity/relation 提取 | triples + evidence 审核表 | 80% 以上关系可追溯且可读 |
| G. Web 验收 | Admin 搜索、重建索引、命中详情、配置提示 | 操作录屏或验收表 | 新用户无需读文档即可知道怎么用 |
| H. 回归测试 | 单元、API、插件、Prompt 注入测试 | pytest/build 输出 | 知识链路不再只靠手工观察 |

## 十二、P0/P1 改造任务拆解

| 任务 | 文件 | 改动 | 风险 |
| --- | --- | --- | --- |
| Admin 搜索恢复 | `admin/routes/api/knowledge.py` | 改为调用 `retrieve()` 或 `search_hits()`，返回错误信息而不是吞异常 | 低 |
| 暴露运行时 KB | `plugins/knowledge/plugin.py`、`admin/__init__.py` 或 bus API | `ctx.knowledge_base = self._kb` 或 Admin 从 bus 查插件实例 | 中，需确认 ctx 字段 |
| 结构化命中 | `services/knowledge/__init__.py` | 新增 `KnowledgeHit` 与 `search()`，保留 `retrieve()` 兼容 | 低 |
| 递归索引 | `services/knowledge/__init__.py`、`plugins/knowledge/config.schema.json` | 增加 recursive/include/exclude | 低 |
| Web 搜索核对 | `admin/frontend/src/views/knowledge/KnowledgeView.vue` | 结果显示 source/title/score/chunk | 中 |
| 测试补齐 | `tests/test_knowledge.py`、`tests/test_admin_api.py` | 覆盖 API、插件启动、PromptBlock dynamic | 低 |

## 十三、长期重构方向

| 方向 | 适用场景 | 技术路线 | 判断标准 |
| --- | --- | --- | --- |
| 轻量本地知识库 | 文档少、主人自用、低维护 | SQLite + BM25/ngram + metadata | 快、稳、无重依赖 |
| 可选语义检索 | 文档中等、问法变化大 | embedding extra + vector backend adapter | recall 明显提升且成本可控 |
| 轻量知识图谱 | 群梗、人物、设定、关系查询 | SQLite/NetworkX entity-relation-evidence | 可审核、可回滚、来源清楚 |
| GraphRAG 模式 | 文档多、全局总结和跨文档问题多 | entity graph + community reports + local/global router | global 问答明显优于摘要检索 |
| Agentic RAG | 查询复杂、需要多轮检索和自检 | query planner + retrieve/evaluate/retry | 延迟可接受，错误率显著下降 |

我建议实际推进顺序是：先轻量本地知识库变强，再做可选语义，最后才做知识图谱和 GraphRAG。这样即使未来重构，默认系统也不会因为重依赖和自动抽取失控而变难维护。

## 十四、可证明有效的验收指标

| 指标 | 定义 | 目标 |
| --- | --- | --- |
| `recall@3` | golden query 的正确 source 是否在 top3 | P2 后较当前 baseline 提升至少 20% |
| `MRR` | 正确结果排名倒数均值 | P2 后提升 |
| `false_positive_rate` | top3 中明显无关结果比例 | P2 后下降 |
| `context_tokens` | 每轮注入知识 token | 有上限且可观测 |
| `source_coverage` | 被索引文档数/目标文档数 | 可解释，递归后显著提升 |
| `admin_search_success` | Admin 搜索 API 返回结构化结果比例 | P0 后 100% 不白空、不吞错 |
| `cache_prefix_stability` | 知识命中变化时 stable system hash 是否变化 | DeepSeek native 下不变 |
| `human_accept_rate` | 人工标注命中有用比例 | 作为上线门禁 |

## 十五、参考来源

论文与文档：

| 来源 | 链接 |
| --- | --- |
| GraphRAG: From Local to Global | https://arxiv.org/abs/2404.16130 |
| Microsoft GraphRAG 文档 | https://microsoft.github.io/graphrag/ |
| LightRAG | https://arxiv.org/abs/2410.05779 |
| LightRAG GitHub | https://github.com/HKUDS/LightRAG |
| HippoRAG | https://arxiv.org/abs/2405.14831 |
| HippoRAG GitHub | https://github.com/OSU-NLP-Group/HippoRAG |
| RAPTOR | https://arxiv.org/abs/2401.18059 |
| Self-RAG | https://arxiv.org/abs/2310.11511 |
| Corrective RAG | https://arxiv.org/abs/2401.15884 |
| HyDE | https://arxiv.org/abs/2212.10496 |
| Lost in the Middle | https://arxiv.org/abs/2307.03172 |
| LlamaIndex GitHub | https://github.com/run-llama/llama_index |
| Haystack GitHub | https://github.com/deepset-ai/haystack |
| Neo4j GraphRAG Python | https://github.com/neo4j/neo4j-graphrag-python |

本地代码证据：

| 证据 | 路径 |
| --- | --- |
| Omubot 当前倒排索引 | `services/knowledge/__init__.py` |
| Omubot 知识库插件注入 | `plugins/knowledge/plugin.py` |
| Omubot Admin 知识库 API | `admin/routes/api/knowledge.py` |
| GraphRAG 查询工厂 | `/private/tmp/omubot-kb-audit/graphrag/packages/graphrag/graphrag/query/factory.py` |
| GraphRAG local/global search | `/private/tmp/omubot-kb-audit/graphrag/packages/graphrag/graphrag/query/structured_search/` |
| LightRAG QueryParam 与 KG query | `/private/tmp/omubot-kb-audit/LightRAG/lightrag/base.py`、`operate.py` |
| HippoRAG OpenIE 与 PPR | `/private/tmp/omubot-kb-audit/HippoRAG/src/hipporag/` |
| LlamaIndex retriever/NodeWithScore | `/private/tmp/omubot-kb-audit/llama_index/llama-index-core/llama_index/core/` |
| Haystack DocumentStore/BM25 | `/private/tmp/omubot-kb-audit/haystack/haystack/` |
| Neo4j retriever result | `/private/tmp/omubot-kb-audit/neo4j-graphrag-python/src/neo4j_graphrag/` |
