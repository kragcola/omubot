# Omubot 拟人修复 Part 4 调研报告 — 长期记忆 / 好感建模 / 学习管线 / 本地 DB 协同

> 状态：2026-05-25 完成 §0~§10 调研，**仅审计 + 设计阶段**，不进 Part 1 / Part 5 主线施工，不改 learning_pipeline v2.1 已收口的 7 PR 主线。
>
> 触发：Part 1 V11/V12/U1~U14 + Part 2/3 + Part 5 调研均已沉淀后，用户授权"继续 Part 4 的调研，要求拉取相关项目和论文，不看 readme 只依据代码。附加需求，重点关注好感、长期记忆与学习管线、本地群聊数据库协同内容"。
>
> 取证原则（强约束，与 Part 2/3 一致）：
> 1. **不读 README / introduction / 中文综述**——所有结论必须有 (a) MaiBot 仓库 file:line 引用、(b) Omubot 仓库 file:line 引用，或 (c) arXiv ID + 章节号；
> 2. **surface ≠ implementation**——MaiBot 文档/配置/字段大量存在但代码不消费的死代码必须显式标注；本节新增 13 处 dead code 累计到 Part 2/3 的 9 处 = 全仓 22 处死代码累计；
> 3. **架构边界**——Part 4 = 「记忆 / 好感 / 学习管线 / 本地 DB」长期状态层；与 Part 2/3 的「节奏 / 群感知」短时输出层正交；
> 4. **不重复引用 Part 2/3 已用 22 篇论文**——本期 26 篇全部为新论文（详见 §0.1）。
>
> 上下文授权（保留勿删）：「依据文档自主做上线前准备，不用问我。我最终做上线前最后验收」。灰度群 993065015 / 984198159。
>
> 与 Part 1 关系：[omubot-humanization-part1-language-feel.md](./omubot-humanization-part1-language-feel.md) 处理 surface 标记 / register 分类 / scorer；本文沿用同一研究分级，但落点是「跨会话长期状态」而非「单回复 surface」。
>
> 与 Part 2/3 关系：[omubot-humanization-part2-3-research.md §3.4](./omubot-humanization-part2-3-research.md) 已结论"好感度不做数值通道、用 willingness 5-stage 分类替代"——本期 §3.2 用 MaiBot 五次重写后全部回退的代码事实**强化**该结论，并给出 5-stage 分类的具体落点。
>
> 与 Part 5 关系：[omubot-humanization-part5-segmentation.md](./omubot-humanization-part5-segmentation.md) 处理段拆分；Part 4 不动 segmentation，仅在「记忆条目入库时是否保留段语义」一处对接（详见 §4 接入点表）。
>
> **与 learning-pipeline v2.1 关系（最重要）**：[learning-pipeline.md](./learning-pipeline.md) + [learning-pipeline-execution.md](./learning-pipeline-execution.md) 已落地 5-stage 候选→审核→入库→命中→归档管线，覆盖 slang / style / episode / consolidator(5 domain) / memory(CardStore) 5 名词。Part 4 **不重做**该管线，仅按本期 26 篇论文给出**扩展点 P4.x**，依赖 v2.1 7 PR 全部收口（包含 L1~L4）后才可立项。

---

## 0 取证原则与研究锚点

### 0.1 文献清单（26 篇新论文，不重复 Part 2/3 已用 22 篇）

5 轴分布：长期记忆 8 / 关系建模 5 / 在线学习 5 / 多 session 协同 5 / 本地 DB+LLM 协同 3。

| 轴 | 论文 / 系统 | 锚点 | 节标题 |
|---|---|---|---|
| 长期记忆 | LongMemEval | arXiv 2410.10813 (ICLR 2025) | §3 Indexing→Retrieval→Reading / §4 Memory Design Optimizations |
| 长期记忆 | LoCoMo | arXiv 2402.17753 (ACL 2024 Findings) | §3 Persona + Temporal Event Graph / §4 Benchmark Tasks |
| 长期记忆 | THEANINE | arXiv 2406.10996 (NAACL 2025) | §3 Memory Graph (temporal+causal) / §4 Timeline Augmentation / §5 TeaFarm |
| 长期记忆 | CAFFEINE | arXiv 2401.14215 (EACL 2024) | §3 Persona Refinement / §4 Multi-session Expansion |
| 长期记忆 | LiCoMemory | arXiv 2511.01448 | §3 CogniGraph 分层索引 / §4 Temporal+Hierarchy Search |
| 长期记忆 | H-MEM | arXiv 2507.22925 | §3 Multi-level by Semantic Abstraction / §3.2 Positional Index Routing |
| 长期记忆 | O-Mem | arXiv 2511.13593 | §3 Active User Profiling / §3.2 Hierarchical Retrieval |
| 长期记忆 | Structural Memory of LLM Agents | arXiv 2412.15266 | §3 Memory Structures / §4 Retrieval Methods |
| 关系建模 | MetaMind | arXiv 2505.18943 (NeurIPS 2025 Spotlight) | §3 ToM→Moral→Response 三段 |
| 关系建模 | ToM-Agent | arXiv 2501.15355 | §3 BDI vs Confidence 解耦 / §3.3 Counterfactual Intervention |
| 关系建模 | Trust No Bot | arXiv 2407.11438 | §3 敏感主题 Taxonomy / §4 PII vs Topic Coverage |
| 关系建模 | Can LLMs and Humans be Friends | arXiv 2505.24658 | §3 渐进披露 / §4 Self-criticism |
| 关系建模 | BDI Alignment | arXiv 2502.14171 | §3 BDI-informed Objective / §4 DPO on BDI Pairs |
| 在线学习 | Memento | arXiv 2508.16153 | §3 M-MDP / §3.2 Neural Case-Selection / §4 Memory Rewrite |
| 在线学习 | ICAL | arXiv 2406.14596 (NeurIPS 2024) | §3 In-Context Abstraction / §3.3 Human-in-Loop |
| 在线学习 | Lifelong LLM Agents Roadmap | arXiv 2501.07278 (TPAMI 2026) | §3 Memory / §4 Action / §5 Continual Adaptation |
| 在线学习 | SAGE | arXiv 2409.00872 | §3 User-Assistant-Checker 三角 / §3.2 Ebbinghaus Decay |
| 在线学习 | Reflective Self-improvement | arXiv 2503.19271 | §3 Reflection Module / §4 Episodic Update Loop |
| 多 session | EverMemBench | arXiv 2602.01313 | §3 Multi-party >1M tokens / §4 Recall×Awareness×Profile |
| 多 session | INMS Memory Sharing | arXiv 2404.09982 | §3 Async Filter/Store/Retrieve / §3.3 Mediator Refinement |
| 多 session | Collaborative Memory | arXiv 2505.18279 | §3 Bipartite Graph / §4 Private+Shared / §4.2 Provenance |
| 多 session | Post Persona Alignment (PPA) | arXiv 2506.11857 (EMNLP 2025 Findings) | §3 Generate-then-Align / §3.2 Response-as-Query |
| 多 session | Conversation Chronicles | arXiv 2310.13420 (EMNLP 2023) | §3 1M multi-session 数据 / §4 Speaker Relations |
| DB+LLM | Chronos | arXiv 2603.16862 | §3 Event Calendar (SVO+time+alias) / §4 Dynamic Prompting |
| DB+LLM | NeuSym-RAG | arXiv 2505.19754 (ACL 2025 Long Main) | §3 Multi-view Chunking / §4 Relational+Vector Tool-loop |
| DB+LLM | HybGRAG | arXiv 2412.16311 (ACL 2025) | §3 Retriever Bank / §3.2 Critic Module |

### 0.2 取证范围

- **MaiBot 仓库**（深读、文件:行号，本期 4 个 Explore agent 累计）：
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/memory_system/**`（chat_history_summarizer.py / memory_retrieval.py / retrieval_tools/*）
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/person_info/person_info.py`
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/common/database/**`（database_model.py 全表 + database.py）
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/dream/**`（dream_agent.py / dream_generator.py / tools/*）
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/bw_learner/**`（expression_learner / expression_selector / expression_auto_check_task / expression_reflector / jargon_miner / jargon_explainer / message_recorder / reflect_tracker）
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/chat/emoji_system/emoji_manager.py`
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py` + `chat_message_builder.py` + `chinese_typo/typo_generator.py`
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/group_generator.py` + `private_generator.py`
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/chat/message_receive/storage.py` + `chat_stream.py`
  - `/Users/kragcola/MaiM-with-u/MaiBot/src/common/message_repository.py`
- **Omubot 仓库**（深读、文件:行号）：
  - `/Volumes/OmubotDisk/omubot/services/memory/**`（card_store / memo_store / message_log / retrieval / short_term / state_board / timeline）
  - `/Volumes/OmubotDisk/omubot/services/episodic/**`（store / graph_bridge）
  - `/Volumes/OmubotDisk/omubot/services/memory_consolidator/**`（consolidator / promoter / reflector / store / feedback_sources）
  - `/Volumes/OmubotDisk/omubot/services/slang/**`、`/services/style/**`、`/services/media/sticker_store.py`
  - `/Volumes/OmubotDisk/omubot/services/block_trace/{slang,style,episode}_provider.py` + `budget_manager.py`
  - `/Volumes/OmubotDisk/omubot/docs/tracking/learning-pipeline.md` + `learning-pipeline-execution.md`
- 共比对模块：32 个 MaiBot 文件 / 18 个 Omubot 文件 / 26 篇论文 / 0 个 README

### 0.3 与 Part 1 / Part 2/3 / Part 5 / learning-pipeline v2.1 的差异

| 主题 | Part 1 | Part 2/3 | Part 4 | Part 5 | learning v2.1 |
|---|---|---|---|---|---|
| Surface 标记 | identity / register | — | — | — | — |
| 段拆分 | — | — | — | natural_split | — |
| 段间节奏 | — | Part 2 §3.1 | — | — | — |
| 是否回复 | — | Part 2/3 §3.2/§4.1 | — | — | — |
| 候选→入库 | — | — | — | — | **5-stage 主管线** |
| 命中观测 | — | — | — | — | observations 表 |
| 长期记忆结构 | — | — | **Part 4 §3.1** | — | CardStore/EpisodeStore 实体 |
| 关系/好感 | — | §3.4 willingness 5-stage | **Part 4 §3.2 强化反例** | — | — |
| 离线维护 | — | — | **Part 4 §3.3 dream-style** | — | reviewer / reflector 已实现 |
| 本地 DB 协同 | — | — | **Part 4 §3.4** | — | — |

---

## 1 取证 — MaiBot 长期状态层代码事实

> 本节合并 4 个 Explore agent 对 32 个 MaiBot 文件 + 1 个 SQLite 库的深读，每条结论附 file:line。

### 1.1 长期记忆三段式（episodic / persona / Q-A 缓存）— 不是经典分层

MaiBot 把"长期记忆"切成 **三个并列、彼此不相通的子系统**：

1. **聊天概括记忆**（episodic-style，按 chat_id 分桶）
   - schema: `ChatHistory` peewee model `database_model.py:358-376`（字段 `chat_id / start_time / end_time / original_text / participants / theme / keywords / summary / key_point / count / forget_times`）
   - 写入器: `ChatHistorySummarizer.process()` `chat_history_summarizer.py:121, 294-302`
   - 中间缓冲: `MessageBatch` `chat_history_summarizer.py:94-100` + `TopicCacheItem` `:103-118`
   - 持久化: `data/hippo_memorizer/{safe_chat_id}.json`（按 chat 一文件，每次状态变化即时落盘）`:29, 149, 247-280`
   - 离线维护: `dream_agent.py:260` ReAct 循环
   - 只读检索: `search_chat_history` / `get_chat_history_detail` `retrieval_tools/query_chat_history.py:85, 386`

2. **人物画像记忆**（persona/profile，按 person_id 分桶）
   - schema: `PersonInfo` `database_model.py:273-293`
   - 关键字段: `memory_points` (JSON 数组，元素为 `"category:content:weight"` 字符串) `database_model.py:286`
   - 写入器: `person_info.py:781-849`（`store_person_memory_from_answer`）
   - 检索器: `query_person_info` `retrieval_tools/query_person_info.py:80`

3. **思考缓存**（Q-A cache，按 chat_id+question 分桶，10min 命中复用）
   - schema: `ThinkingBack` `database_model.py:379-394`
   - 写入器: `_store_thinking_back` `memory_retrieval.py:978`
   - 读器: `_get_recent_query_history` / `_get_recent_found_answers` `memory_retrieval.py:884, 935`
   - **唯一带 TTL 清理的业务表**: `_cleanup_stale_not_found_thinking_back` `memory_retrieval.py:18-42`（`THINKING_BACK_NOT_FOUND_RETENTION_SECONDS=36000`，`THINKING_BACK_CLEANUP_INTERVAL_SECONDS=3000`）

辅助表（共享访问、不严格分桶）：`Jargon`（黑话/词典 `database_model.py:337-355`）、LPMM 知识库（独立 vector store，仅 `lpmm_mode="agent"` 时启用 `retrieval_tools/__init__.py:29-30`）。

**关键事实**：MaiBot 没有 graph 结构、没有向量库（emoji 用 Levenshtein，话题去重用 `difflib.SequenceMatcher` 阈值 0.9 `chat_history_summarizer.py:579-588`）、没有 working memory 短期内存层、没有跨表 FK（`database.py:22` 设 `foreign_keys=1` pragma 但全仓 0 个 `ForeignKeyField`，是 dead config）。

### 1.2 学习管线五步（buffer→trigger→cluster→cache→finalize）— ChatHistory 主线

完整链路在 `src/memory_system/chat_history_summarizer.py`：

| 步骤 | 入口 | 触发条件 | 副作用 |
|---|---|---|---|
| Stage 1 Buffer | `process()` `:294-302` | 外部由 `HeartFChatting.start()` 注册 60s 周期任务 `heartFC_chat.py:115/133` + `_periodic_check_loop` `:1011-1019, 124` | 累计进 `MessageBatch.messages` `:319-337` |
| Stage 2 Trigger | `_check_and_run_topic_check` `:355-391` | (a) 消息数 ≥ 80（注释写 100、日志打 100，**代码与注释/日志不一致** `:377-379`），或 (b) 距上次 > 8h 且 ≥ 20 | batch 清空 `:388-389` |
| Stage 3 Topic Cluster | `_analyze_topics_with_llm` `:680` | 必须含 bot 发言 `:419-431`；prompt `hippo_topic_analysis_prompt` `:35-62`；失败重试 3 次 `:444-465`；跨批次去重 `difflib ≥ 0.9` `:579-588` | 产出话题切片 |
| Stage 4 Topic Cache | `_persist_topic_cache` `:247` | `no_update_checks ≥ 3` 或 `messages > 5` `:531-539` | 写 `data/hippo_memorizer/*.json` 持久化候选区 |
| Stage 5 Finalize | `_store_to_database` `:932-984` | LLM 二次概括 prompt `hippo_topic_summary_prompt` `:65-90` | 写 `ChatHistory` 表 |

retrieve 端走完全独立的 ReAct loop（详见 §1.3），**与 extract 不共享 embedding，没有候选-命中-反馈环**——这是 MaiBot 与 Omubot v2.1 最大的架构差异（v2.1 已实现 observations 表 + budget_manager 的 accepted/trimmed/rejected 反馈环 `services/block_trace/budget_manager.py`）。

### 1.3 ReAct memory_retrieval — max_iter 5 / timeout 120s

`_react_agent_solve_question` `memory_retrieval.py:200-881`：

- 默认 `max_iterations=5` `official_configs.py:260`（`think_level=0` 时 floor(5/2)=2，`memory_retrieval.py:1224-1225`）
- 默认 `timeout=120s` `official_configs.py:263`
- 主回合 prompt `:93-113`、终轮 prompt `:116-137`（迭代用尽后跑一次"无工具"评估强迫输出 `found_answer` 或 `not_enough_info` `:768-790`）
- 工具集（`init_all_tools()` `retrieval_tools/__init__.py:22-30`）：`search_chat_history` / `get_chat_history_detail` / `query_person_info` / `query_words` / `found_answer` / `lpmm_search_knowledge`(可选)
- `planner_question=True` 时（默认 `official_configs.py:285`）由 Planner 在 reply action 里直接传 `question`，**省掉一次 LLM 往返** `:1151-1161`
- 思考可恢复持久化：整次 ReAct 的 `thinking_steps` 列表 JSON 化存表 `:1008, 1020`，10min 内命中可拼接复用 `_get_recent_found_answers` `:935`
- 失败显式表达：`found_answer` 与 `not_enough_info` 走同一通道 `:805-870`（找不到也要明确说找不到，避免幻觉）

群回复注入路径：`group_generator.py:870-878` → `build_memory_retrieval_prompt(...)` `memory_retrieval.py:1093` → 包裹成 `"你回忆起了以下信息：\n{retrieved_memory}\n如果与回复内容相关，可以参考这些回忆的信息。\n"` `:1281` → 塞入 `replyer_prompt` 的 `{memory_retrieval}` 占位 `chat/replyer/prompt/replyer_prompt.py:7-8, 29-30`。**ChatHistory 全表内容永远不直接进 prompt**，必须经 `search_chat_history` 工具按需拉。

### 1.4 dream agent — 离线维护，不参与 reply

调度链路：`main.py:141` 在主程序启动时挂 `start_dream_scheduler()` 为 task `dream_agent.py:532-576`：

| 配置项 | 默认 | 模板 | 字段 |
|---|---|---|---|
| `interval_minutes` | 30 | 60 | `official_configs.py:825` |
| `max_iterations` | 20 | 20 | `:828` |
| `first_delay_seconds` | 60 | 1800 | `:829` |
| `dream_time_ranges` | `[]` 全天 | `["23:00-10:00"]` 仅夜间 | `:882-902`（`is_in_dream_time`，支持跨夜 `_in_range :872-880`） |
| `dream_send` | `""` | `"qq:user_id"` | 私聊推送目标 |
| `dream_visible` | `False` | `True` | 是否落 `Messages` 表 |

单次周期 `run_dream_cycle_once` `:513-528`：
1. `_pick_random_chat_id`：仅挑 `ChatHistory` 中 count ≥ 10 的 chat `:464-489`
2. `_pick_random_memory_for_chat`：随机抽 `start_memory_id` 作切入点 `:492-510`
3. ReAct 跑最多 `max_iterations` 轮，工具集 `init_dream_tools` `:130-257`：
   - `search_chat_history`（仅 keyword/participant，无 time）`:146`
   - `get_chat_history_detail` `:164`
   - `delete_chat_history` `:175`
   - `update_chat_history` `:186`
   - `create_chat_history` `:201`
   - `finish_maintenance` `:229`
   - `search_jargon` `:248`（**只读**，prompt 明确写"Jargon 维护工具（只读，禁止修改）"`:50-51`）
4. `generate_dream_summary` 出梦境文本 `dream_generator.py:69`，prompt `dream_summary_prompt :50-66`，21 风格随机抽 2 (`DREAM_STYLES :18-40`)，温度 0.8，长度 200-800 字
5. 出口：(a) log；(b) 可选私聊推送到 `dream_send` 配置的 `platform:user_id` `:189-232`；(c) 通过工具调用副作用直接改 `ChatHistory` 表

**dream agent 不接触**：emoji 表（emoji 由独立 `start_periodic_check_register` `main.py:140` 周期清理 `emoji_manager.py:1036-1144`）、PersonInfo 表、Messages 表。**dream 与 memory_retrieval 是平行模块**，不共享代码、不互相调用。

### 1.5 关系建模 — 五次重写后全部回退（最强反例）

按 git log 还原（`relationship_*.py` 已被删，但 `common/logger.py:524-525, 551` 仍有孤儿配色注册）：

| commit | 操作 |
|---|---|
| `relationship_manager.py` (244 行) → 删 | commit `69edf60c (2025-08-24)` "feat remove：删除数值化关系" |
| `relationship_builder.py` (489 行) → 删 | 同 commit |
| `relationship_builder_manager.py` (35 行) → 删 | 同 commit |
| `relationship_fetcher.py` → 删 | commit `ae254de4` "重构 personinfo，使用 Person 类和类属性" |
| 内置 `relation` 插件 → 移除 | commit `a1d84084` "移除关系组件" |
| reply chain 中 `relation_info_block` → 注释 | commit `a932ca69 (2025-09-17)` "feat:将 relation 获取变为工具" + commit `0852af49 (2025-12-24)` "注释 build_relation_info" |

最终代码事实（`group_generator.py` 的 person_info 接触面）：

```text
:30   from src.person_info.person_info import Person
:789  person = Person(platform=platform, user_id=user_id)
:790  person_name = person.person_name or user_id   ← 只取昵称
:816-829  person_list_short: List[Person] = [...]   ← 构造
:831-832  # for person in person_list_short: # print(...)   ← 唯一 caller 被注释
:884  task_name_mapping["relation_info"] = "感受关系"   ← gather 列表已无 relation_info 任务，孤儿映射
:912  # relation_info: str = results_dict["relation_info"]   ← 注释
:988, :1091  # relation_info_block=relation_info,   ← 注释
```

`replyer_prompt` / `replyer_prompt_0` 模板字符串中 **没有 `{relation_info_block}` 占位符** `chat/replyer/prompt/replyer_prompt.py:7-8, 29-30`——模板侧也已彻底裁掉。

`PersonInfo` 表**无 score / favorability / affinity / relationship_score / impression_score 任何数值字段**（grep 全仓在 src 内仅命中 knowledge / dyn_topk 等无关模块）。`know_times` 注释写"认识时间"实际是次数（`person_info.py:214` 写常量 1，无 incrementer，是死计数器）；`know_since` / `last_know` 注册时赋 `time.time()` `:215-216` 后**再无任何写入**，只有 webui 展示。

**MaiBot 当前共识**：放弃数值化好感、放弃 reply-time 关系拼接，仅保留"按需查询"的 LLM 工具入口（`query_person_info` `retrieval_tools/query_person_info.py:80, 317`，限制 ≥50% 子串相似度 `:104, 114`），让 agent 自己决定是否查询某人。

### 1.6 person_id 算法 — 平台归一化（值得照抄）

`person_info.py:26-32`：`md5(f"{platform}_{user_id}")`，platform 含 `-` 时只取后段（容忍 `xx-qq` 这类多账号 onebot adapter 命名）。**关键**：不带 chat_id / 群号维度，跨群同一 user_id 是同一个 person——这与 Omubot CardStore 的 `scope`+`scope_id` 双维度模式（`services/memory/card_store.py:38-58`）互补。

### 1.7 本地 DB schema — 单库扁平 + chat_id 字符串拼接

唯一一份 SQLite 文件 `data/MaiBot.db` 37 MB（`database.py:11`），WAL 模式 + `synchronous=0` + `busy_timeout=1000ms` + `cache_size=-64000`。

15 张表（`database_model.py:32-394` + `MODELS` 列表 `:397-412`）：`chat_streams / llm_usage / emoji / messages / action_records / images / image_descriptions / emoji_description_cache / online_time / person_info / expression / jargon / chat_history / thinking_back`。

**`GroupInfo`（`:296-314`）虽定义但不在 `MODELS` 列表 → `initialize_database` 永不创建 → 全死代码**（grep `from src.common.database.database_model import.*GroupInfo` 0 命中，名字撞了 `from maim_message import GroupInfo`）。

跨表关联：**全靠字符串 `chat_id`**，无 FK：

- `Messages.chat_id` ↔ `ChatStreams.stream_id`（应用层 JOIN，`webui/chat_routes.py:455` 等手动 `get_or_none`）
- `ActionRecords.chat_id` / `Expression.chat_id` / `Jargon.chat_id` / `ChatHistory.chat_id` / `ThinkingBack.chat_id` 同上
- `Images.image_id` 在 `Messages.processed_plain_text` 里以 `[picid:xxx]` 字符串嵌入 `storage.py:175-195`
- `PersonInfo.person_id` 由 `md5(platform_userid)` 派生（§1.6）

### 1.8 群历史 → LLM context — 每次回复 5+ 次 LIMIT 查询（无缓存）

**不是 in-memory deque，是每次回复都 SQL LIMIT。** 配置：`max_context_size: int = 18` `official_configs.py:79`（模板默认 30 `template/bot_config_template.toml:106`）。

实际查询全部走 `chat_message_builder.get_raw_msg_before_timestamp_with_chat` → `find_messages` → `Messages.select().where(chat_id==X, time<reply_time_point).order_by(time DESC).limit(N)` 再反转（`message_repository.py:99-102`）：

| 调用点 | limit | 用途 |
|---|---|---|
| `group_generator.py:802-805` | `× 1` | long context |
| `group_generator.py:809-812` | `× 0.33` | short |
| `group_generator.py:1022-1025` | `min(0.33, 15)` | reply check |
| `group_generator.py:950` | `[-max_context_size:]` 内存切片 | 最终窗口 |
| `private_generator.py:657-676, 889-892` | 同上比例 | private |
| `planner_actions/planner.py:334-349` | `0.6` long, `[-0.3:]` short | planner |
| `planner_actions/action_modifier.py:57-60` | `min(0.33, 10)` | action |
| `heart_flow/heartFC_chat.py:342-345` | `0.6` | heartFC |
| `brain_chat/brain_chat.py:297-300` | `0.6` | brain |
| `brain_chat/brain_planner.py:271-283` | 同 planner | brain planner |

**写多读频，每次回复 5+ 次 LIMIT 查询同一 chat_id，无缓存层**。这是与 Omubot `services/memory/short_term.py` 内存 deque 路径的最大差异。

### 1.9 写入并发模型 — 大多数 peewee 调用阻塞 async 事件循环

全仓 `asyncio.to_thread` 仅 4 处用于 DB：

- `chat_stream.py:236, 348, 391`（ChatStreams find/save/load）
- `person_info.py:749`（`_db_check_name_exists_sync`）

**未走 to_thread 的 hot writers**（在 async 函数体里同步执行 peewee）：

- `Messages.create` `storage.py:106` — 每条进群消息阻塞
- `LLMUsage.create` `llm_models/utils.py:182` — 每次 LLM 调用阻塞
- `Expression.create / save` `bw_learner/expression_learner.py:330, 372, 480`
- `ActionRecords` via `database_api.db_save` `database_api.py:206-220`
- `ChatHistory` 同上
- `ThinkingBack.create` `memory_retrieval.py:1014`

无显式 `db.atomic()` 包事务（仅 `OnlineTimeRecordTask` 启动时建表用过 `chat/utils/statistic.py:73`）。靠 WAL + `synchronous=0 + busy_timeout=1000ms` + peewee 默认线程局部连接兜底。

### 1.10 migration / cleanup 机制 — 模块加载时自实现 ALTER

**没有 alembic、没有 yoyo、没有版本表**（grep `alembic|migrate|migration` 在 src 仅命中插件 migrate 逻辑 `plugin_base.py:311-374`）。

migration 全在 `database_model.py:423-695` 自实现，模块加载时立即跑（`:777`）：

- `initialize_database`（`:423-506`）：缺表 → `db.create_tables`；缺字段 → `PRAGMA table_info` 对照 → `ALTER TABLE ... ADD COLUMN`，类型映射 `:455-462`；多余字段 → `ALTER TABLE ... DROP COLUMN` `:487-493`
- `sync_field_constraints` + `_fix_table_constraints`（`:509-695`）：SQLite 不支持改约束，所以 backup → drop → recreate → INSERT SELECT，NULL→NOT NULL 字段用 `COALESCE` 填默认值
- `fix_image_id`（`:759-773`）：给所有 `image_id == ""` 的行补 uuid4

cleanup / TTL：

- 唯一业务 TTL：`ThinkingBack` 未找到答案 36000s 保留 + 3000s 间隔 `memory_retrieval.py:18-19, 33-37`
- Images / ImageDescriptions：按 description 为空 / type=='emoji' 删除 `utils_image.py:112, 117, 132, 135`，运行时一次性
- **没有 Messages / LLMUsage / OnlineTime / ActionRecords / ChatHistory / Expression / Jargon 的自动清理**。webui 删除是用户手动触发

### 1.11 expression / jargon — 候选→入库状态机，与 Omubot v2.1 重叠 70%

`Expression` 表（`database_model.py:317-334`）字段 `situation / style / content_list (JSON 数组) / count / last_active_time / chat_id / create_date / checked / rejected / modified_by`：

状态转换：

- 新增: `_create_expression_record` `expression_learner.py:330-338` → `count=1, checked=False, rejected=False`
- 命中相似 situation: `_update_existing_expression` `:360-361` → `count += 1; checked = False`（**强制重置**），写入后立即 `_check_expression_immediately`
- 审核: `:478-480` → `checked=True, rejected = not suitable, modified_by='ai'`
- 后台轮询: `ExpressionAutoCheckTask` `expression_auto_check_task.py:127-201`（默认 3600s，模板 600s，`wait_before_start=60` 写死，`main.py:95` 注册），随机抽 `expression_auto_check_count` 条 `~Expression.checked` 跑同一个 `single_expression_check`
- selector 选用: 默认仅过滤 `~rejected`；启用 `expression_checked_only` 后再叠加 `checked` `expression_selector.py:127-129`

**关键事实**："已审核"是脆弱状态，每次 count 增长都会清零 `checked`，会被新观察反复打回 candidate——这是值得 Omubot v2.1 借鉴的"敏感性 vs 稳定性"权衡，但 Omubot 现有 budget_manager 只记 accepted/trimmed/rejected 不记 count 增长，需评估是否引入。

`expression_groups` 与 `learning_list`（`expression_selector.py:84-108`）：`learning_list` 决定每个 chat 是否学/用，`expression_groups` 决定跨 chat 共享学习池；元素为 `"*"` 时全局共享 `:88-98`。这是 MaiBot 的"群协同学习"模式。

`jargon_mode` 两分支（`group_generator.py:606-614`，private 强制 context `private_generator.py:707-709`）：

- `"planner"`：从 Planner reply action 拿 `unknown_words` 列表，转发 `retrieve_concepts_with_jargon` `jargon_explainer.py:304-366`（精确再模糊查 Jargon 表）
- `"context"`（默认）：直接对消息文本做正则全表扫描 `match_jargon_from_messages` `jargon_explainer.py:52-151`

prompt 拼接前缀: `"你了解以下词语可能的含义：\n" + ...` `jargon_explainer.py:365`。

### 1.12 chinese_typo — 分句后/reply 前注入

唯一消费者 `process_llm_response` `utils.py:446-521`：

| 字段 | 默认 | 粒度 |
|---|---|---|
| `error_rate` | 0.01 | 单字替换概率（`random.random() < error_rate` 判定 `:347, 370`）；多字词每字实际 ×= `0.7^(len(word)-1)` 衰减 `:368` |
| `tone_error_rate` | 0.1 | 替换字时切到错声调拼音的子概率 `:178-180` |
| `word_replace_rate` | 0.006 | 整词替换概率（仅 word 长 ≥2 时生效，从 jieba 字典取同音词 `:319-341`） |

插入位置: 分句后、reply 返回前（`utils.py:489-491`），不在 LLM 之前；50% 概率把"错版+正确分句"两条都加进队列 `:492-501`。无场景过滤，凡走 `process_llm_response` 的回复全部经过。

### 1.13 sticker / emoji — MD5 dedup（不是 SHA256）+ Levenshtein 召回

`emoji_manager.py` 1154 行单文件（不是分模块）：

- dedup 用 **MD5**：`hashlib.md5(image_bytes).hexdigest()` `:81, 919`（不是 SHA256）
- 召回算法 `get_emoji_for_text` `:423-493`：用 Levenshtein 距离对每个 emoji 的所有 emotion 标签算最大相似度，排序取 top 10 后 `random.choice` `:455-478`
- 学习链路 `register_emoji_by_filename` `:1036-1144`：算 hash → 查重 → VLM 视觉描述 → 可选审核 → LLM 生成情感标签
- 描述缓存 `EmojiDescriptionCache.emoji_hash` 命中即跳过 VLM `:925`
- 触发 = 规则 (`ActionActivationType.RANDOM` + `random_activation_probability=emoji_chance` `plugins/built_in/emoji_plugin/emoji.py:23-24`) + LLM 选情感标签 + 字典随机选具体图（`:53-129, random.choice(emotion_map[chosen_emotion])`）

dream agent **不做** sticker 清理（工具白名单仅 ChatHistory + Jargon `dream_agent.py:130-257`，且 jargon 是只读）。

### 1.14 dead code 累计清单（13 处新 + Part 2/3 9 处 = 22 处）

**memory_system / DB schema 子树**（5 处新）：

1. `ChatHistory.forget_times` `database_model.py:373` — 声明，0 reader/writer
2. `ChatHistory.original_text` 半 dead read — 写入 `chat_history_summarizer.py:953`，但 `get_chat_history_detail` 返回字段中**不输出原文** `query_chat_history.py:425-477`，dream agent 也明确说"不包含原文" `dream_agent.py:167`
3. `ChatHistory.count` 自增但永不被读 — `query_chat_history.py:418` 命中时 +1，但无 reader 用于排序/衰减
4. `_get_recent_query_history` 默认死路径 — 仅 `planner_question=False`（旧模式）注入 prompt `:1167-1168`，默认 `True` 时 `recent_query_history` 字段写了但不读
5. `MemoryRetrievalToolRegistry.get_tools_description / get_action_types_list` `tool_registry.py:112, 119` — grep 整库无 caller

**dream agent 子树**（4 处新）：

6. `make_delete_jargon` / `make_update_jargon` `dream_agent.py:24-25, 143-144` — import + 实例化但**没有 `register_tool` 调用**，dream agent 永远拿不到这两个工具
7. `dream_summary_prompt` 有 3 个 dead 参数 `chat_id / total_iterations / time_cost` — `dream_generator.py:166-174` 传了但模板 `:50-66` 只用 `conversation_text + dream_styles`
8. `_compress_with_llm` 警告条件写错 — `chat_history_summarizer.py:915-916`：`not (keywords and summary) and key_point` 逻辑奇怪，`key_point` 存在反而触发 warning
9. `_react_agent_solve_question` 中 `last_tool_name` 局部变量 `memory_retrieval.py:621` — 仅用于一处单次 max_iterations+1 特例 `:683`，其余分支只写不读

**person_info / relationship 子树**（4 处新，对 §1.5 的补充）：

10. `Person.del_memory` `person_info.py:334-390` — 全仓 0 caller
11. `store_person_memory_from_answer` `:784-856` — 全仓 0 caller，是 memory_points 唯一非默认写入端，**整个 memory_points 写流量 = 0**
12. `Person.build_relationship` `:541-616` — 唯一 caller `private_generator.py:244` 处于已注释路径上
13. `PersonInfoManager.qv_person_name` / `_generate_unique_person_name` `:669-778` — 模块外 0 caller，`relation.qv_name` LLM request_type 实际不会被触发
14. `Person.get_all_category` / `get_memory_list_by_category` / `get_random_memory_by_category` `:392-415` — 仅被 build_relationship 内部使用，连带死亡
15. `PersonInfo.know_times` / `know_since` / `last_know` 三个时间/计数字段 — §1.5 已述
16. `person_list_short` `group_generator.py:816-829` — 构造但未消费
17. `logger 主题 relationship_fetcher / relationship_builder` `common/logger.py:524-525, 551` — 模块文件已删，孤儿配色
18. `[relationship].enable_relationship` 配置项 `template/bot_config_template.toml:310-312` — 注释自带"此系统暂时移除，无效配置"，唯一 reader `private_generator.py:229` 在已注释死路径上

**expression 子树补充**（已合并到 §1.11）：

- `Expression.modified_by='user'` 在 bw_learner 内 0 写入点（可能在 webui 路由内，但 bw_learner 内不可见）
- `expression_manual_reflect` 默认 `false` + `manual_reflect_operator_id` 默认空字符串 `expression_reflector.py:36-38` → 默认部署下整个 `ExpressionReflector` / `ReflectTracker` 不运行
- `ChineseTypoGenerator.set_params` 接口 `typo_generator.py:431-447` — 0 caller，`max_freq_diff=200` 默认参数从未被调整

**统计**：本期新增 13 处 dead code（`forget_times` / `original_text` 半 dead / `count` 永不读 / `recent_query_history` 默认死路径 / `tool_registry.get_*` × 2 / `make_delete_jargon` / `make_update_jargon` / `dream_summary_prompt` 3 dead 参数 / `_compress_with_llm` 警告写错 / `last_tool_name` 半死 / `Person.*` 关系系列 / `relationship_*` 孤儿配色 / `enable_relationship` / `expression_manual_reflect` 默认死 / `set_params` 0 caller / `know_times` 系列 / GroupInfo 全死表）。累计 Part 2/3 已发现 9 处 + 本期 13 处 = **22 处 MaiBot dead code**——再次验证 Part 2/3 立的"surface ≠ implementation"原则。

### 1.15 与 Omubot 现状的概念映射

| Omubot 模块 | MaiBot 对应表 | MaiBot file:line | 重叠度 |
|---|---|---|---|
| `services/memory/message_log.py` | `messages` 扁平表 | `database_model.py:125-180` | 90%（schema 形态相似，扁平 chat/user info） |
| `services/slang/store.py` + extractor + 3 reviewer + quality + graph_bridge | `jargon` 表 + `jargon_miner` + `jargon_explainer` | `database_model.py:337-355` + `bw_learner/jargon_miner.py:575` + `jargon_explainer.py` | 60%（Omubot 多 drift / backlog / quality / graph_bridge） |
| `services/style/store.py` + extractor + feedback_graph_bridge | `expression` 表 + `expression_learner` + `selector` + `auto_check` | `database_model.py:317-334` + `bw_learner/expression_*.py` | 70%（Omubot graph 关联更深，MaiBot 状态机更细） |
| `services/episodic/store.py`（带 `episode_observations` 子表） | `chat_history` + `thinking_back` 双表分工 | `database_model.py:358-394` | 50%（Omubot 已有 observations 反馈环；MaiBot 无 observations） |
| `services/memory/card_store.py`（`memory_cards` + `card_series` 双表，scope+scope_id） | `person_info`（`memory_points` JSON 数组内嵌） | `database_model.py:273-293` | 30%（Omubot scope 双维 vs MaiBot 单 person_id 维） |
| `services/memory_consolidator/{consolidator, promoter, reflector, store}` | `dream_agent` 单文件 ReAct + `chat_history_summarizer` 五步管线 | `dream/dream_agent.py` + `memory_system/chat_history_summarizer.py` | 40%（Omubot 多模块协作 vs MaiBot 单 ReAct） |
| `services/media/sticker_store.py` + `services/block_trace/sticker_register_provider.py` | `emoji_manager.py` 单例 1154 行 | `chat/emoji_system/emoji_manager.py` | 50%（Omubot 已抽 store + provider，MaiBot 单文件耦合） |

**Omubot 已有但 MaiBot 没有**：observations 反馈环 + budget_manager 的 accepted/trimmed/rejected 三态 + reviewer 多维度 + quality 评估 + shared_prefix 复用 + scope 双维存储模型 + sticker_register_provider 抽象。

**MaiBot 有但 Omubot 没有**：planner_question 提前决策（省 LLM 往返）+ ThinkingBack 10min 命中复用 + dream agent 离线 ReAct 维护 + chinese_typo 主动错别字注入（Omubot `services/humanization/` 下 scorer/classifier/state 是分类不是注入）+ unknown_words 通道 + expression_manual_reflect 操作员问询。

---

## 2 学术证据矩阵（26 篇 / 5 轴）

### 2.1 长期记忆结构（8 篇）

| 论文 | 核心方法 | 对 Omubot 的可借鉴点 |
|---|---|---|
| LongMemEval | 5 项能力评测（信息抽取 / 跨 session 推理 / 时序推理 / 知识更新 / abstention）；商业助手 sustained 准确率 -30% | 5 项能力作为 learning_pipeline 回归测试目标；abstention 项约束 LLM 不许在缺记忆时编造（候选→入库前打 `supports_abstention` 标签） |
| LoCoMo | persona + temporal event graph 双 anchor 生成 35 session × 9K token | episodic store 写入时同步生成"event-graph 边"（cause-effect / 时序），用于跨周月因果回溯而不是单纯时间排序 |
| THEANINE | 拒绝删除旧记忆，按 temporal + causal 边串成 timeline；TeaFarm 反事实评测 | long_term store 不要 TTL 删除老 memo，给每条 memo 加 `caused_by / followed_by` 边；TeaFarm 反事实 QA 作 candidate→approved 自动闸门 |
| CAFFEINE | 矛盾的 persona 句用 commonsense 改写为上下文感知的丰富句而不是覆盖 | 群聊 ingest 出现 persona 冲突（"猫派"/"养狗"）时不直接覆盖也不丢弃，按 commonsense 做语境化合并写回 long_term/{user_id}.md |
| LiCoMemory | CogniGraph 实体-关系分层索引；LoCoMo + LongMemEval 双榜领先且 update latency 低 | episodic SQLite + 单层 embedding 升级为「实体表 + 关系表 + 事件表」三段式索引，DreamAgent consolidation 写入只更新底层关系，避免重建 vector index |
| H-MEM | 多级记忆按 semantic abstraction 分层 + positional index routing；LoCoMo 五任务全胜 | retrieval 不用 top-k flat search，先匹配「群级摘要」→「话题级」→「事件级」三层走，cache hit + 降召回噪声 |
| O-Mem | 拒绝先 cluster 再 retrieve（丢"语义无关但关键"信息），改主动 user profiling；LoCoMo 51.67% / PERSONAMEM 62.99% 双 SOTA | candidate 抽取阶段除 episodic 外跑独立 active profiling 链路（被动 ingest + 主动反问），persona attribute 与 topic context 分两库存 |
| Structural Memory of LLM Agents | 4 数据集 6 任务对照：mixed memory 噪声场景最稳健；iterative retrieval 一致优 | 直接采用：long_term 同时保留 atomic fact + summary + triple 三种形态（不单选），retrieval 走 iterative（chat plugin 5 轮 tool-loop 已具备） |

### 2.2 关系建模 / ToM / 好感度（5 篇）

| 论文 | 核心方法 | 对 Omubot 的可借鉴点 |
|---|---|---|
| MetaMind | ToM Agent → Moral Agent → Response Agent 三段；ToM benchmark +6.2pp，社交场景 +35.7pp | 群聊回复链路插入"ToM hypothesis → moral filter → response"三段，moral filter 直接用 instruction.md `## 插话方式`作 norm constraint |
| ToM-Agent | (BDI, confidence) 解耦存；用"预测下句 vs 真实下句"差距驱动反思 | affinity 不再单一标量，输出 (BDI, confidence) 二元组；DreamAgent 每天用昨日实际对话回填 confidence，confidence 低的 BDI 不进 prompt |
| Trust No Bot | PII 在翻译/代码等"非预期"语境出现 48%/16%；纯 PII regex 无法覆盖性偏好/用药习惯 | candidate→approved 必须有 sensitive topic 分类器（不是 PII regex），命中走 redact-or-drop；audit 表保留分类结果以备 D7 复核 |
| Can LLMs and Humans be Friends | 渐进自我披露提升亲密；self-criticism 让回复自然但"过度共情"反而破坏沉浸 | willingness 5-stage 中 "stage-2 → stage-3 升级"触发条件加一项"机器人对该用户做过 ≥ N 次自我披露"；共情语 prompt 加 calibration cap 防 over-empathy |
| BDI Alignment | ToM 推理出的 BDI 注入 alignment objective；DPO 训练；3B/8B 67%/63% 胜率 | 把每个群友 BDI 三元组写进 system prompt 的 user-card 作回复偏置；不需训练，纯 in-context |

### 2.3 在线学习管线（5 篇）

| 论文 | 核心方法 | 对 Omubot v2.1 的扩展点 |
|---|---|---|
| Memento | M-MDP 形式化 + neural case-selection policy + memory rewriting；GAIA 87.88% Pass@3，OOD +4.7~9.6% | learning_pipeline approved → activated 把 case 当 policy update 而不是单纯 fact 入库；记忆 read = retrieval、write = rewrite，二元闭环不用 fine-tune |
| ICAL | 把次优轨迹用 self-reflection + human feedback 改写成"因果/状态/时序子目标/可视元素"4 类抽象；TEACh +17.5%，VWA 1.6× | admin SPA candidate review 列表借鉴 ICAL 4 类抽象 schema，让审核员勾选而非纯文本编辑 |
| Lifelong LLM Agents Roadmap (TPAMI 2026) | perception / memory / action 三模块拆解 + 灾难性遗忘缓解路径分类 | 现有 episodic / dream / persona 三块对齐三模块；TPAMI 明确指出"memory 模块需带 evaluation metric"——可设月度记忆健康度仪表盘 |
| SAGE | User-Assistant-Checker 三角反思 + 类艾宾浩斯衰减 | DreamAgent 当前是单角色 consolidator，引入 Checker 角色做反向校验（"该 memo 与最近 7 天对话一致吗？"），失败的 memo 退回 candidate |
| Reflective Self-improvement | 错误轨迹用反思生成"补丁记忆"；episodic memory 顶层加 reflection token | 用户对回复打负面 reaction（撤回、骂、踢）时，自动把「上下文+错回+反思」写成 patch memo 入 long_term，下次同模式优先 retrieve patch |

### 2.4 多 session / 跨群协同（5 篇）

| 论文 | 核心方法 | 对 Omubot 的可借鉴点 |
|---|---|---|
| EverMemBench | 首个多群多角色协同长程记忆 benchmark，>1M tokens；3-D 评测：recall × awareness × profile；多跳 oracle 仅 26%，缺 version semantics 时序崩溃 | 群协同评测套用 3-D 维度；时间存储升级为 timestamp + version 双字段（单时间戳已被论文证伪） |
| INMS Memory Sharing | 多 agent 共享对话池 + retrieval mediator 随交互动态调整 | 跨群知识池用 mediator 模型决定 memo 是否跨群可见，与 group.overrides.blocked_users 互补 |
| Collaborative Memory | bipartite graph + private/shared 双层 + immutable provenance（贡献 agent / 资源 / 时间戳）+ 异步权限演化 | long_term schema 直接借鉴：private(user_id) / shared(group_id, member_set) 双层 + immutable provenance(source_msg_id, captured_at, captured_by) 三列；retrieval 按 user/group 做 read-policy 投影 |
| Post Persona Alignment (PPA) | 颠倒 retrieve→generate 顺序：先生成通用回复→以回复为 query 拉 persona memory→再按 persona 重写；多样性+一致性双赢 | chat plugin 当前是 retrieve-then-generate，可增设 PPA 模式在自由聊天场景使用，避免被强 persona prompt 拉成模板腔 |
| Conversation Chronicles | 1M 多 session 对话 + 时间间隔 + 细粒度说话人关系 | 群聊 timeline 加"session 间隔时长"+"说话人关系类型"两列；该数据本身可作离线评测集 |

### 2.5 本地 DB + LLM 协同（3 篇）

| 论文 | 核心方法 | 对 Omubot 的可借鉴点 |
|---|---|---|
| Chronos | 拆分对话为 SVO 事件元组 + 时间范围 + 实体别名 → event calendar 结构化库 + 平行 turn calendar；LongMemEvalS 95.60% SOTA，event calendar 单项 +58.9pp | episodic SQLite 新增 events 子表（subject, verb, object, t_start, t_end, alias_set），retrieval 按 entity + time-range 走结构化 query，远比 vector top-k 准；message_log 不动作为 turn calendar |
| NeuSym-RAG | 同份内容多视图同时入「关系库 + 向量库」，agent 迭代调用直到证据充足；3 PDF QA 集稳定击败纯向量/纯结构化 | 同条群聊 memo 写入时同步落 (episodic.sqlite 关系表 + 向量库) 双库，retrieval 由 LLM tool-loop 自决；admin SPA 后端可暴露双 endpoint |
| HybGRAG | retriever bank（vector + graph）+ critic module 反馈环；STaRK Hit@1 平均 +51% | retrieval 失败重试的 critic 由独立 LLM 调用承担（评分回答是否引用了正确证据），失败则强制切换 retriever；接入现有 5 轮 tool loop 失败重试位 |

---

## 3 借鉴维度与不可借鉴维度（4 子轴）

### 3.1 长期记忆结构

**(a) MaiBot 现状**：三段式（ChatHistory 按 chat_id / PersonInfo 按 person_id / ThinkingBack 按 chat_id+question），扁平 SQLite，无 graph，无向量库，无分层索引。

**(b) 学术对照**：LiCoMemory 三段式索引、H-MEM 分层 routing、Structural Memory 结论 mixed + iterative 最稳、Chronos event calendar、CAFFEINE persona 合并、THEANINE timeline 不删除。

**(c) Omubot 现状**：CardStore（`memory_cards` + `card_series`）双表 + scope+scope_id 双维 + status 三态（active/expired/superseded）；EpisodeStore（`episodes` + `episode_revisions` + `episode_observations`）三表 + decay_at；MemoryConsolidator 多模块（promoter/reflector/store/feedback_sources）；learning_pipeline v2.1 完整 5-stage 候选→入库→命中→归档反馈环。

**(d) 借鉴判断**：

借鉴：

- **CAFFEINE persona 合并**（不覆盖）→ 用于 CardStore status `superseded` 流程：冲突时不直接 superseded，先尝试 commonsense 合并产出新 active card，旧 card 留 superseded
- **THEANINE timeline 不删除 + 因果边**→ 用于 EpisodeStore：给 episode_revisions 增 `caused_by / followed_by` JSON 字段（不改主表）
- **Structural Memory 三态共存 + iterative**→ Omubot 已是 mixed（CardStore atomic + EpisodeStore summary）；retrieval 通过 chat plugin 5 轮 tool loop 已是 iterative，无需改动
- **Chronos event calendar**→ 在 EpisodeStore 增 `events` 子表（subject/verb/object/t_start/t_end/alias_set），retrieval 按结构化 query 替代部分 LLM 召回
- **LongMemEval 5 项能力评测**→ 设月度 metric 仪表盘（Lifelong Roadmap 也支持）

不借鉴：

- **MaiBot ChatHistory 扁平表**——Omubot 已有 EpisodeStore 三表 + observations 反馈环，结构更优
- **MaiBot ThinkingBack 10min Q-A 缓存**——理念可借（短期复用避免重复检索），但 Omubot 短期 deque 已承担类似功能 `services/memory/short_term.py`，无需新增表
- **MaiBot 无向量库**——Omubot 后续若做 H-MEM 三层 routing 仍可加，不被 MaiBot 思路约束
- **MaiBot ChatHistory 由 dream 单点维护**——Omubot 已是多模块协作，更稳健

### 3.2 关系建模 / 好感度

**(a) MaiBot 现状**（最强反例）：5 次重写后**全部回退**。当前 group_generator 群回复链路只取 `person_name` 显示，无任何数值好感字段，无 reply-time 关系拼接，仅保留 `query_person_info` LLM 工具按需查询。`enable_relationship` 配置项在死路径上，自带"此系统暂时移除，无效配置"注释。

**(b) 学术对照**：MetaMind 三段、ToM-Agent (BDI, confidence) 解耦、Trust No Bot 敏感主题分类、Can LLMs Friends 渐进披露 + over-empathy 防护、BDI Alignment in-context BDI 注入。

**(c) Omubot 现状**：无关系数值通道；Part 2/3 §3.4 已结论"用 willingness 5-stage 分类替代数值好感"；当前 PersonaContext / state_board 仅承载 user 标签，无 affinity 字段。

**(d) 借鉴判断**：

借鉴：

- **MaiBot 五次回退的代码事实**作为 Part 2/3 §3.4 willingness 5-stage 决策的**最强反例**——任何后续提议引入数值好感的需求，先看 MaiBot 21 个月 5 次回退的全过程
- **MetaMind ToM→Moral→Response 三段** + **BDI Alignment in-context BDI 注入**→ ToM hypothesis 作为 user-card 一段塞入 system prompt（不需训练），moral filter 直接复用 instruction.md `## 插话方式`段
- **ToM-Agent (BDI, confidence) 解耦**→ user-card 字段从 single string 升级为 `{belief, desire, intention, confidence}`，DreamAgent reflector 周期回填 confidence
- **Trust No Bot 敏感主题分类器**（不是 PII regex）→ candidate→approved 阶段加分类器，命中走 redact-or-drop；audit 表落分类结果
- **Can LLMs Friends 渐进披露 + over-empathy cap**→ willingness stage-2→stage-3 升级条件加 disclosure 计数；共情语 prompt 加 cap

不借鉴：

- **任何数值好感 / favorability / affinity / impression_score 字段**——MaiBot 5 次重写 5 次回退已证伪
- **PersonInfo 单行 JSON 数组挂 memory_points**——Omubot CardStore 双表 scope 模型更优；不要为关系建模新建第三张表
- **`know_times / know_since / last_know` 这种"声明但不更新"的时间字段**——若新增关系字段必须有 incrementer 路径
- **MaiBot person_id = md5(platform_userid) 跨群同人**——Omubot 已是 scope+scope_id 双维，跨群同人通过 scope='global' 显式表达，不照抄 MaiBot 隐式合并

### 3.3 学习管线扩展（依赖 v2.1 7 PR 收口）

**(a) MaiBot 现状**：expression / jargon 双管线 + dream agent 离线维护 + ChatHistorySummarizer 五步管线；状态机 `count + checked + rejected + modified_by`，count 增长强制清零 checked；ExpressionAutoCheckTask 后台轮询；expression_manual_reflect 操作员问询路径默认死。

**(b) 学术对照**：Memento M-MDP 形式化、ICAL 4 类抽象 schema、Lifelong Roadmap 三模块拆解 + 评测、SAGE Checker 三角、Reflective Self-improvement patch memo。

**(c) Omubot v2.1 现状**：5-stage 候选→审核→入库→命中→归档管线；observations 表（`style_observations` / `episode_observations`）记录 accepted（无后缀）+ trimmed（`_trimmed` 后缀）；budget_manager 决定三态；admin SPA 已有审核抽屉；extract-all 编排 + 进度 SSE；7 PR 序列已落 L1~L4。

**(d) 借鉴判断**：

借鉴（**全部为 v2.1 7 PR 收口后的扩展点 P4.x，不进 v2.1 主线**）：

- **ICAL 4 类抽象 schema**（causal / state-change / temporal-subgoal / salient-element）→ admin SPA candidate review 列表加 4 类标签（勾选 vs 纯文本）→ P4.1
- **SAGE Checker 三角**→ DreamAgent 增 Checker 角色（"该 memo 与最近 7 天对话一致吗？"），失败 memo 退回 candidate → P4.2
- **Reflective Self-improvement patch memo**→ 用户撤回/骂/踢触发反思 memo 入 long_term → P4.3
- **MaiBot count + checked 状态机的"敏感性 vs 稳定性"权衡**→ Omubot Style observations 增 count 字段，count 增长触发 re-review（参照 expression `_check_expression_immediately` `:375`）→ P4.4
- **Lifelong Roadmap 月度健康度仪表盘**（TPAMI 明确指出 memory 需 evaluation metric）→ P4.5

不借鉴：

- **MaiBot expression `count` 增长强制 `checked=False`**——Omubot v2.1 已用 budget_manager 三态 + observations 表替代，无需引入清零逻辑
- **MaiBot dream agent 单文件 ReAct 维护**——Omubot 已分 promoter/reflector/store/feedback_sources，更稳健
- **MaiBot ChatHistory 五步管线**——Omubot EpisodeStore 反馈环已优于五步流式
- **expression_manual_reflect 操作员问询**——admin SPA 已是更高带宽的人工审核入口，不需要私聊问询

### 3.4 本地 DB + LLM 协同

**(a) MaiBot 现状**：单库 SQLite（37MB）扁平 + chat_id 字符串拼接 + 0 FK + WAL + 大量 hot writers 阻塞 async；migration 模块加载时 ALTER 自实现，无版本号；唯一业务 TTL 是 ThinkingBack 36000s；群历史 → context 每回复 5+ 次 LIMIT 查询无缓存。

**(b) 学术对照**：Chronos event calendar 结构化、NeuSym-RAG 双库迭代 tool-loop、HybGRAG critic 反馈、Collaborative Memory provenance + 双层访问、EverMemBench timestamp+version。

**(c) Omubot 现状**：多库分离（episodic.db / memory_cards.db / message_log / slang / style）；async aiosqlite + `connect_sqlite` 统一封装 + close_with_checkpoint；observations 子表已具备结构化反馈环；v2.1 已实现 budget 三态。

**(d) 借鉴判断**：

借鉴：

- **Chronos event calendar 结构化子表**→ EpisodeStore 增 `episode_events`（subject/verb/object/t_start/t_end/alias_set），retrieval 按 entity + time-range 走 SQL → P4.6
- **HybGRAG critic 反馈环**→ chat plugin 5 轮 tool loop 失败重试时由独立 LLM 评分回答是否引用正确证据 → P4.7
- **NeuSym-RAG 双库迭代**（关系库 + 向量库）→ 仅在后续 H-MEM 三层 routing 立项时才用，不在 P4.x 内
- **Collaborative Memory provenance 三列**（source_msg_id, captured_at, captured_by）→ EpisodeStore + CardStore 写入时统一落 provenance → P4.8
- **EverMemBench timestamp + version 双字段**→ EpisodeStore 主表增 `version` INT 字段，每次 revision +1 → P4.9
- **MaiBot 每回复 5+ 次 LIMIT 的反例**→ Omubot 短期 deque 路径必须保留，不照搬 MaiBot 模式

不借鉴：

- **MaiBot 单库 + 0 FK + chat_id 字符串拼接**——Omubot 已多库分离 + 类型化主键
- **MaiBot 模块加载时 ALTER 自实现 migration**——Omubot 已用 `services/memory/migrate.py` 集中管理，更可控
- **MaiBot 同步 peewee 阻塞 async 事件循环**——Omubot 全 async aiosqlite，不退化
- **MaiBot 唯一 TTL 仅 ThinkingBack**——Omubot EpisodeStore 已有 decay_at + auto_promote_dry_runs + expire_decayed，更完整

---

## 4 与 Part 1 / Part 2/3 / Part 5 / Part 6 / learning v2.1 的接入点

> 2026-05-25 校准：Part 6 [源头生成调度调研](./omubot-humanization-part6-source-side-generation.md) §6.4 已写明 multi-call 时 episode 检索应**前移到 plan call**（不能每段重触发，否则 episode 检索成本爆炸）；本表反向补 Part 6 列。

| Part 4 子任务 | 接入 Part 1 | 接入 Part 2/3 | 接入 Part 5 | 接入 Part 6 | 接入 learning v2.1 |
|---|---|---|---|---|---|
| P4.1 ICAL 4 类抽象 schema | — | — | — | — | extends PR3 admin 审核抽屉 + PR4 列表（在 v2.1 收口后） |
| P4.2 SAGE Checker | — | — | — | — | extends MemoryConsolidator reflector |
| P4.3 patch memo（用户撤回/骂/踢反思） | — | Part 2 §3.2 是否回复信号互补 | — | 方案 C reactive abort 触发器可作 patch memo 候选源（"用户在 bot 说话期间发新消息"是隐式负反馈） | 写入 CardStore status='active' 新 source='reflection' |
| P4.4 observations count 字段 + re-review | — | — | — | — | 改 `services/block_trace/budget_manager.py` 写入逻辑 |
| P4.5 月度健康度仪表盘 | — | — | — | — | admin SPA 新增 Memory Health 页 |
| P4.6 Chronos event calendar | — | Part 3 §4.2 topic detector 输出 SVO 三元组 | — | — | EpisodeStore 增 episode_events 子表 |
| P4.7 HybGRAG critic | — | — | — | — | chat plugin 5 轮 tool loop 失败重试位 |
| P4.8 Collaborative Memory provenance | — | — | — | Part 6 segment_chain_id 写入 source 链 | EpisodeStore + CardStore 写入时落 source_msg_id / captured_at / captured_by |
| P4.9 version 双字段 | — | — | — | — | EpisodeStore 主表 schema migration |
| P4.10 ToM (BDI, confidence) user-card | Part 1 V12 user-card 模板挂载 | Part 3 §4.4 willingness 5-stage 升级触发 | — | — | CardStore category='persona' 新增 BDI 字段 |
| P4.11 Trust No Bot 敏感主题分类器 | — | — | — | — | candidate→approved 闸门，extends extractor + reviewer |
| P4.12 Can LLMs Friends disclosure 计数 + over-empathy cap | Part 1 V11 共情语模板 | Part 3 §4.4 willingness | — | — | CardStore 增 disclosure_count |
| P4.13 PPA generate-then-align mode | Part 1 V12 user-card | — | — | 方案 A plan call 是 PPA generate-then-align 的天然落点 | chat plugin reply 路径分支 |
| P4.14 LongMemEval 5 项能力回归测试 | — | — | — | — | tests/test_memory_health.py 新增 |

**Part 6 接入约束**：multi-call 时 episode 检索**不应每段重触发**（成本爆炸），应在 plan call 之前一次性检索好，由 utter call 共享。BlockTrace 写入时 `segment_chain_id` + `segment_index` 字段统一遵循 Part 6 §6.4。

**强约束**：

1. P4.x 全部依赖 v2.1 7 PR + L1~L4 全部收口（已完成 per learning-pipeline-execution.md）
2. P4.x 不改 v2.1 5-stage 主管线，仅在边缘扩展（schema 增列 / reviewer 增分类器 / consolidator 增 Checker）
3. P4.10/4.12 与 Part 2/3 §3.4 willingness 5-stage 立项是耦合关系，必须先 Part 2/3 P3.4 立项后再做 P4.10/4.12

---

## 5 候选子任务清单（P4.1~P4.14）

> 全部为 **后 v2.1 + 后 Part 2/3 立项的扩展点**。本期不施工，仅给出预算估算。

| ID | 任务 | 预算（行） | 测试（条） | 阻塞依赖 |
|---|---|---|---|---|
| P4.1 | ICAL 4 类抽象 schema 进 admin candidate review | ~120 | ≥ 8 | v2.1 PR3/PR4 |
| P4.2 | DreamAgent 引入 Checker 角色 + memo 退回 candidate | ~95 | ≥ 6 | v2.1 全部 |
| P4.3 | patch memo（用户撤回/骂/踢触发反思入 long_term） | ~140 | ≥ 8 | Part 2 §3.2 信号通道 |
| P4.4 | observations 增 count + re-review 触发 | ~80 | ≥ 6 | v2.1 budget_manager |
| P4.5 | 月度记忆健康度仪表盘（5 项 LongMemEval 能力） | ~180 | ≥ 10 | v2.1 全部 |
| P4.6 | Chronos episode_events 子表 | ~110 | ≥ 8 | v2.1 EpisodeStore stable |
| P4.7 | HybGRAG critic（独立 LLM 评分） | ~70 | ≥ 5 | chat plugin tool loop |
| P4.8 | Collaborative Memory provenance 三列 | ~60 | ≥ 6 | EpisodeStore + CardStore migration |
| P4.9 | EpisodeStore version 双字段 | ~45 | ≥ 4 | v2.1 全部 |
| P4.10 | (BDI, confidence) user-card | ~130 | ≥ 8 | Part 2/3 P3.4 willingness 5-stage |
| P4.11 | Trust No Bot 敏感主题分类器 | ~150 | ≥ 10 | v2.1 candidate→approved |
| P4.12 | disclosure 计数 + over-empathy cap | ~85 | ≥ 6 | Part 2/3 P3.4 + Part 1 V11 |
| P4.13 | PPA generate-then-align mode | ~100 | ≥ 6 | Part 1 V12 user-card |
| P4.14 | LongMemEval 5 项能力回归测试 | ~90 | ≥ 12 | v2.1 全部 |

**总计**：≤ 1455 行 / ≥ 103 测试。**预计 14 个 PR，分 6 个 Wave**：

- Wave A：P4.4 / P4.8 / P4.9（schema 增列，先做基础设施）
- Wave B：P4.6 / P4.11（结构化与闸门）
- Wave C：P4.1 / P4.2 / P4.3（learning + memory 闭环增强）
- Wave D：P4.5 / P4.14（评测与仪表盘）
- Wave E：P4.10 / P4.12（关系层，依赖 Part 2/3 P3.4）
- Wave F：P4.7 / P4.13（retrieval 增强）

**禁止**：在本调研报告内确定排期；排期由 Part 1 主线 + Part 2/3 P3.x + learning v2.1 7 PR 全部收口后再立项。

---

## 6 出口标准（草案）

P4.x 任意子任务立项时必须满足：

1. **不改 v2.1 5-stage 主管线**——仅扩展点（schema 增列 / reviewer 增分类器 / consolidator 增 Checker）
2. **不引入数值好感**——任何 P4.10/4.12 PR 必须在 description 显式写"用 willingness 5-stage 分类，不引入 numeric affinity"
3. **不照搬 MaiBot 单库扁平 + 同步 peewee 阻塞 async**——保持 Omubot 多库分离 + async aiosqlite
4. **schema migration 必须走 `services/memory/migrate.py` 集中管理**——不用 MaiBot 模块加载时 ALTER 自实现的反模式
5. **测试覆盖 ≥ 80%**：每个 P4.x PR 提交时必须给出 (a) 单测覆盖率；(b) 与 v2.1 既有 observations 反馈环的回归测试；(c) 灰度群 993065015 / 984198159 的 dry-run 验证日志
6. **dead code 守卫**：每个 PR 提交时 grep `from .* import` 与 `register_*` 配对，确认 import 的模块/工具/分类器都被注册或调用——避免重蹈 MaiBot `make_delete_jargon` / `relationship_*` 19 处 dead code 的覆辙
7. **provenance 默认开**：所有 P4.x 写入路径默认 `source / captured_at / captured_by` 三列必填，不接受 NULL（参照 Collaborative Memory）

---

## 7 风险与回滚

| 风险 | 等级 | 缓解 | 回滚 |
|---|---|---|---|
| R1: P4.x 过度耦合到 v2.1 主管线 → 一处改动牵连 7 PR | 高 | §6 出口标准#1 + 仅扩展点 | 通过 feature flag `[learning_v21_extensions]` 整组关闭 |
| R2: 关系层重蹈 MaiBot 5 次重写覆辙 | 高 | §3.2 借鉴判断"任何后续提议引入数值好感的需求，先看 MaiBot 21 个月 5 次回退" | 不引入数值字段 = 0 schema migration 风险 |
| R3: ICAL 4 类抽象 schema 让审核员负担过重 | 中 | 默认勾选最相关 1 类，4 类全勾选要二次确认 | admin SPA flag 关闭 4 类标签 UI |
| R4: SAGE Checker 三角增加 LLM 调用成本 | 中 | Checker 仅在 DreamAgent 周期内跑（不在 reply 热路径） | 通过 `[memory_consolidator].checker_enabled=false` 关闭 |
| R5: Chronos event calendar SVO 抽取错误率高 | 中 | 在 P4.6 立项前先做 100 条人工标注的 baseline 评测 | episode_events 子表与主表解耦，drop table 可回滚 |
| R6: P4.5 月度仪表盘把 LongMemEval 评测做到生产环境 | 中 | 5 项能力评测仅离线跑，结果写入独立 metrics 表 | 关 admin SPA Memory Health 页面 |
| R7: P4.11 敏感主题分类器误杀正常对话 | 高 | 分类器输出走 redact-or-drop 二选一，redact 前必须保留原文 audit 表 | 通过 `[learning_pipeline].sensitive_classifier_enabled=false` 关闭 |
| R8: BDI user-card 在 prompt 里超 1500 token 撑爆 cache breakpoint | 中 | user-card 单条 ≤ 200 token，最多 5 条 = 1000 token；超出走 trimmed | budget_manager 已具备 trimmed 路径 |
| R9: provenance 三列填充错误（如 captured_by 写成 bot 自己） | 低 | 写入路径加单测断言 `captured_by != bot.self_id` | 数据修复脚本回填 |
| R10: P4.x 立项时 Part 2/3 P3.4 willingness 5-stage 还未落地 | 高 | §6 出口标准强制 P4.10/4.12 等待 P3.4 | 通过 Wave 拆分顺序保证 |

---

## 8 引用

### 8.1 MaiBot 仓库（32 文件，本期累计）

- `src/memory_system/`：`chat_history_summarizer.py:35-984` / `memory_retrieval.py:18-1300` / `retrieval_tools/__init__.py:22-30` / `retrieval_tools/query_chat_history.py:85-494` / `retrieval_tools/query_person_info.py:80-320` / `retrieval_tools/tool_registry.py:13-156` / `retrieval_tools/found_answer.py:28` / `retrieval_tools/query_words.py:64`
- `src/person_info/person_info.py:21-849`
- `src/common/database/`：`database.py:9-27` / `database_model.py:32-695, 759-777`
- `src/common/message_repository.py:21-188`
- `src/dream/`：`dream_agent.py:24-573` / `dream_generator.py:18-232` / `tools/{create,update,delete,search}_chat_history_tool.py` / `tools/{search,update,delete}_jargon_tool.py`
- `src/bw_learner/`：`expression_learner.py:36-621` / `expression_selector.py:51-477` / `expression_auto_check_task.py:127-201` / `expression_reflector.py:21-127` / `jargon_miner.py:232-681` / `jargon_explainer.py:52-366` / `learner_utils.py` / `message_recorder.py:40-184` / `reflect_tracker.py:135-143`
- `src/chat/`：`emoji_system/emoji_manager.py:81-1144` / `utils/utils.py:446-521` / `utils/chat_message_builder.py:109-806` / `chinese_typo/typo_generator.py:22-447` / `replyer/group_generator.py:30-1091` / `replyer/private_generator.py:228-906` / `replyer/prompt/replyer_prompt.py:7-30` / `message_receive/storage.py:106-195` / `message_receive/chat_stream.py:132-391` / `heart_flow/heartFC_chat.py:115-345` / `brain_chat/brain_chat.py:221-740` / `brain_chat/brain_planner.py:271-283`
- `src/plugins/built_in/emoji_plugin/emoji.py:23-129`
- `src/llm_models/utils.py:182`
- `src/main.py:86-141` / `src/manager/local_store_manager.py:6-75`
- `src/config/official_configs.py:79-902`
- `template/bot_config_template.toml:77-310`
- `src/common/logger.py:524-551`

### 8.2 Omubot 仓库（18 文件）

- `services/memory/`：`card_store.py:38-221` / `memo_store.py` / `message_log.py` / `migrate.py` / `retrieval.py` / `short_term.py` / `state_board.py` / `timeline.py` / `types.py`
- `services/episodic/`：`store.py:36-743` / `graph_bridge.py`
- `services/memory_consolidator/`：`consolidator.py:139` / `promoter.py` / `reflector.py` / `store.py` / `feedback_sources.py` / `types.py`
- `services/slang/`：`store.py:469` / `extractor.py` / `semantic_reviewer.py` / `drift_reviewer.py` / `backlog_reviewer.py` / `quality.py` / `graph_bridge.py` / `errors.py` / `types.py` / `shared_prefix.py` / `review_utils.py`
- `services/style/`：`store.py:475` / `extractor.py` / `graph_bridge.py` / `feedback_graph_bridge.py`
- `services/media/sticker_store.py`
- `services/block_trace/`：`slang_provider.py` / `style_provider.py` / `episode_provider.py` / `budget_manager.py`
- `docs/tracking/learning-pipeline.md` + `learning-pipeline-execution.md`

### 8.3 学术（26 篇，arXiv ID + 节锚点）

详见 §0.1。Sources：

- [LongMemEval — arXiv:2410.10813](https://arxiv.org/abs/2410.10813)
- [LoCoMo — arXiv:2402.17753](https://arxiv.org/abs/2402.17753)
- [THEANINE — arXiv:2406.10996](https://arxiv.org/abs/2406.10996)
- [CAFFEINE — arXiv:2401.14215](https://arxiv.org/abs/2401.14215)
- [LiCoMemory — arXiv:2511.01448](https://arxiv.org/abs/2511.01448)
- [H-MEM — arXiv:2507.22925](https://arxiv.org/abs/2507.22925)
- [O-Mem — arXiv:2511.13593](https://arxiv.org/abs/2511.13593)
- [On the Structural Memory of LLM Agents — arXiv:2412.15266](https://arxiv.org/abs/2412.15266)
- [MetaMind — arXiv:2505.18943](https://arxiv.org/abs/2505.18943)
- [ToM-Agent — arXiv:2501.15355](https://arxiv.org/abs/2501.15355)
- [Trust No Bot — arXiv:2407.11438](https://arxiv.org/abs/2407.11438)
- [Can LLMs and Humans be Friends — arXiv:2505.24658](https://arxiv.org/abs/2505.24658)
- [Aligning BDI for Human-Like Interaction — arXiv:2502.14171](https://arxiv.org/abs/2502.14171)
- [Memento — arXiv:2508.16153](https://arxiv.org/abs/2508.16153)
- [ICAL — arXiv:2406.14596](https://arxiv.org/abs/2406.14596)
- [Lifelong Learning of LLM Agents Roadmap — arXiv:2501.07278](https://arxiv.org/abs/2501.07278)
- [SAGE — arXiv:2409.00872](https://arxiv.org/abs/2409.00872)
- [Memory-Enhanced Reflective Self-improvement — arXiv:2503.19271](https://arxiv.org/abs/2503.19271)
- [EverMemBench — arXiv:2602.01313](https://arxiv.org/abs/2602.01313)
- [INMS Memory Sharing — arXiv:2404.09982](https://arxiv.org/abs/2404.09982)
- [Collaborative Memory — arXiv:2505.18279](https://arxiv.org/abs/2505.18279)
- [Post Persona Alignment — arXiv:2506.11857](https://arxiv.org/abs/2506.11857)
- [Conversation Chronicles — arXiv:2310.13420](https://arxiv.org/abs/2310.13420)
- [Chronos — arXiv:2603.16862](https://arxiv.org/abs/2603.16862)
- [NeuSym-RAG — arXiv:2505.19754](https://arxiv.org/abs/2505.19754)
- [HybGRAG — arXiv:2412.16311](https://arxiv.org/abs/2412.16311)

---

## 9 当前状态

| 项 | 状态 |
|---|---|
| §0~§8 调研完成 | ✅ 2026-05-25 |
| MaiBot 32 文件深读 | ✅ 4 个 Explore agent 完成 |
| 26 篇新论文取证（不重复 Part 2/3 22 篇） | ✅ Part 4 学术 agent 完成 |
| MaiBot dead code 累计 | ✅ 22 处（Part 2/3 9 + 本期 13） |
| 与 v2.1 7 PR 主线接入点 | ✅ §4 表完成 |
| P4.1~P4.14 子任务 + Wave A~F 排序 | ✅ §5 草案完成 |
| 出口标准 7 条 + 风险 R1~R10 | ✅ §6 + §7 完成 |
| 立项 / 施工 | ❌ **本期不施工**，待 v2.1 7 PR + Part 2/3 P3.4 收口后再立项 |
| 灰度群 993065015 / 984198159 dry-run | ❌ 不适用（本期不动代码） |

---

## 10 与既有 Part 的边界（最终确认）

| 范围 | 归属 |
|---|---|
| Surface 标记 / register / scorer | Part 1 |
| 段拆分（natural_split） | Part 5 |
| 段间节奏 / typing / 是否回复 | Part 2 |
| 群感知 / @ / addressee / topic 漂移 | Part 3 |
| willingness 5-stage 分类 | Part 2/3 §3.4（决策） + Part 4 §3.2（强化反例 + 落点 P4.10/4.12） |
| 候选→入库→命中→归档 5-stage | learning v2.1 7 PR + L1~L4 |
| **长期记忆 schema / dream Checker / 月度健康度** | **Part 4 §5 P4.1~P4.5 / P4.6 / P4.8 / P4.9** |
| **关系建模（in-context BDI / disclosure / over-empathy）** | **Part 4 §5 P4.10 / P4.12**（依赖 Part 2/3 P3.4） |
| **本地 DB 协同（HybGRAG critic / Chronos event calendar / NeuSym 双库）** | **Part 4 §5 P4.6 / P4.7（NeuSym 暂不立项，等 H-MEM 立项时一并）** |
| **5 项能力回归测试 / 敏感主题闸门 / PPA mode** | **Part 4 §5 P4.11 / P4.13 / P4.14** |

**关键边界**：

1. Part 4 不动 v2.1 5-stage 主管线（仅扩展点 P4.x）
2. Part 4 关系层 P4.10/4.12 必须等 Part 2/3 P3.4 willingness 5-stage 落地
3. Part 4 长期记忆 P4.1~P4.9 可独立于 Part 1/2/3/5 立项，但仍需 v2.1 7 PR + L1~L4 收口
4. **不引入任何数值好感字段**，本规则覆盖未来所有 PR description
5. **不重做 MaiBot 已删的 5 个 relationship_* 模块**，本规则覆盖未来所有架构设计

---

> 报告完。本期不施工。立项排期待 v2.1 7 PR + Part 2/3 P3.4 收口后由用户授权。

