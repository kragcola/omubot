# Omubot 多层学习记忆研讨报告

日期：2026-05-17  
撰写：gpt  
范围：黑话、表达方式、知识库、记忆卡片、知识图谱与 learning normalizer 的耦合研讨

采样说明：本报告中的本地数据快照采样于 2026-05-17，本地工作区通过 `sqlite3` 只读查询获取，仅代表当时运行数据状态；bot 持续运行后数值可能变化。

## ⚠️ 基石达标补丁（必读，2026-05-18 第三轮复审追加）

> **本节目的**：本报告作为多层记忆框架的**基石设计**，第三轮复审（基于 2026 年代码与论文实证）评估为 7.4/10——形状对、方向对、决议有据，但**有 3 个动作项必须在 Phase A0 实施开始前完成**，否则基石不达标。
>
> 实施顺序的硬约束：**Patch P1 / P2 / P3 在 Phase A0.1 之前完成**，三件加起来 ≤ 半天工作量。

### Patch P1 — 接口契约草案（最严重缺口）

**问题**：本报告决议层完整（§ 6 / § 7），架构层完整（§ 4），迁移层完整（§ 8），但**没有接口契约层**。Phase B 的 BlockTraceBus、§ 8.3 步骤 1 的 SlangProvider / StyleProvider、Phase A.5 的 GraphWriter 三类核心接口连**签名雏形**都没有。Phase B 实施第一周会被这个坑住。

**动作**：在 Phase A0 开工前，向本报告追加 § 10 "关键接口契约草案"，至少含三段 Python `Protocol` / dataclass 雏形：

- `ContextProvider`（被 § 8.3 步骤 1 采用）— `async def fetch(viewer_group_id, query_context) -> list[PromptBlockCandidate]`
- `BlockTraceBus`（被 Phase B 采用）— 5 字段 trace 记录的 dataclass + 写入 / 查询 API
- `GraphWriter`（被 Phase A.5 采用）— 节点 / 边类型枚举 + `write_node` / `write_edge` / `apply_cross_group_filter` 签名

雏形不需要 final，但**必须存在**作为 Phase B/A.5 实施的对齐基线。验收：§ 10 三段 Protocol 写出 + 通过本报告 § 8.5 B6（决议追溯链）自检。

**状态（2026-05-19）**：已完成。见 § 10 "关键接口契约草案"。

### Patch P2 — indirect 行为回归先修（最现实缺口）

**问题**：§ 7.6 派生决议把 `SlangSettings.max_indirect_inject_terms=2` 标为 open_risk，**文档处理对了但 bug 还在生产里跑**。`docs/slang-module-implementation-tracker.md` 第 38 / 94 / 170 行的"只注入直命中"承诺仍与代码不一致。

**动作**：在 Phase A0 开工前，把默认值从 2 改回 0（5 行代码 + 1 行 changelog + 重跑 `tests/test_slang_plugin.py`）。这能把 open_risk 转成 green，且**不影响其它工作面**——因为 admin UI 上 0–30 仍然可调，未来 BlockTraceBus 落地后如果证据支持再调回 1 或 2。

> **取舍说明**：等 BlockTraceBus 后采 trace 再决议虽然更"科学"，但在等待期间**生产里默认走的是与承诺不一致的行为**。这违反基石"不挖坑"原则。先把默认改回承诺值是更小风险的选择。

验收：`max_indirect_inject_terms` 默认 = 0；`tests/test_slang_plugin.py` 全绿；§ 5 Phase A0 验收表中 indirect 默认值这一行从 `open_risk` 改为 `green`。

**状态（2026-05-19）**：已完成。后端 `SlangSettings.max_indirect_inject_terms` 默认值与前端 `DEFAULT_SLANG_SETTINGS.max_indirect_inject_terms` 均改为 `0`；新增默认 direct-only 回归测试。

### Patch P3 — A1.3 normalizer 写入现状立刻确认（最潜在缺口）

**问题**：§ 5 Phase A1.3 写"复核 slang 是否仍写入 LearningNormalizerStore；缺失则补齐"。基石原则上**每个未确认的状态都应该现在就 grep 确认**，而不是延后到实施期。"如果缺失"是一种延迟决策的措辞——基石阶段不应该有。

**动作**：在 Phase A0 开工前 grep `LearningNormalizerStore.attach_candidate.*domain.*slang` + 阅读 `services/slang/store.py` 的写入路径，确认现状二选一：

- **(a) 已存在**：A1.3 任务从 "复核 + 缺失则补齐" 改写为 "复核保持运行"，工作量从 ~2 小时降为 ~10 分钟。
- **(b) 不存在**：A1.3 任务从 "如果缺失" 改写为 "已确认缺失，本轮补齐"，工作量保持但**不再是延迟决策**。

验收：§ 5 Phase A1.3 措辞从条件式（"如果缺失"）改为陈述式（"已确认 X / Y"）。

**状态（2026-05-19）**：已完成。grep 与代码阅读确认：当前 `services/slang/` 运行时没有调用 `LearningNormalizerStore.attach_candidate(domain="slang")`；只有 `services/style/store.py` 的 style 写入链路接入 normalizer。A1.3 已改为"已确认缺失，本轮补齐 slang normalizer attach 路径"，不再保留条件式延迟决策。

### Patch 之外的小调整（非阻塞，建议但不硬要求）

以下三项作为 nice-to-have 列出，**不阻塞 Phase A0**，但建议在对应 phase 开工前各处理一次：

- **N1**（A2.5 增强）：A2.5 "写入路径强约束"目前是 4 条行为约束，schema 上没法阻止恶意/无意绕开。建议 store 层加 raise check + 单测覆盖（每个 store 一个 `test_cross_group_grant_cannot_bypass_admin` 测试）。
- **N2**（Phase A.5 拆子项）：Phase A.5 graph schema 当前还是单段描述，没有 sub-items。在 A.5 开工前应补出至少 5 个子任务（schema review / node 表 / edge 表 / 写入 API / ContextService 检索 prototype）。
- **N3**（A0/A1 trace 空窗补丁）：BlockTraceBus 在 Phase B 才落地，A0/A1 实施期 debug 仍要靠现有 admin/logs。建议 A0 阶段加临时 trace log——store 层每个写入 logger.info 写 `(operation, scope, group_id, term_id, source)` 五元组。0.5 天工作量。

### 基石达标判定

完成 P1 / P2 / P3 后，本报告作为多层记忆基石**达标**。N1 / N2 / N3 在对应 phase 开工前补齐则**优秀**。

**2026-05-19 判定**：P1 / P2 / P3 已执行，本报告作为 Phase A0 前置基石达标；A1.3 的实际补链路工作仍按 Phase A1 数据治理执行。

---

## 1. 结论摘要

Omubot 现在已经具备“多层学习材料并列进入 Prompt”的基础，但还没有形成接近真人长期记忆的“多层互相耦合”。当前更像是：

```text
知识/记忆/图谱 → ContextPlugin → 上下文资料
黑话          → SlangPlugin   → 群内黑话 + slang_lookup
表达方式      → StylePlugin   → 动态风格档案 + 表达习惯参考
```

这属于晚融合：各层在回复前并列摆到模型面前，让 LLM 自己临场整合。它有用，但不是完整的“观察 → 归档 → 反思 → 巩固 → 检索 → 行为更新”闭环。

建议后续目标不要叫“把三层都注入 prompt”，而应升级为：

```text
ConversationArchive 观察流
  → typed memory router 分类
  → semantic / episodic / procedural / slang / style 分层入库
  → reflection & consolidation 生成高层规则、关系和经验
  → unified retrieval planner 按场景召回
  → prompt budget manager 分配插入顺序和权重
  → feedback loop 反向更新记忆强度
```

一句话：当前 Omubot 有多个记忆仓库，但还缺“海马体/前额叶”式的统一调度与巩固层。

## 2. 外部研究与项目要点

### 2.1 Generative Agents：观察流 + 反思 + 计划

论文《Generative Agents: Interactive Simulacra of Human Behavior》提出的关键模式是：完整记录经验，用反思把低层观察合成为高层记忆，再动态检索用于计划和行为。论文摘要明确强调 observation、planning、reflection 对可信行为都关键。

来源：https://arxiv.org/abs/2304.03442

对 Omubot 的启发：

- 原始群聊不应只作为黑话/表达抽取输入，也应作为统一 observation stream。
- 黑话、表达、记忆卡片、图谱事实不应互相孤立，应允许从同一批观察中派生。
- 高层反思应重新写回记忆系统，而不是只停留在日志或一次性 prompt。

### 2.2 MemGPT / Letta：核心记忆与外部记忆分层

MemGPT 把 LLM 记忆管理类比操作系统虚拟内存：在有限上下文中管理不同速度/容量的记忆层。Letta 的 memory block 进一步把核心上下文拆为有 label、description、value、limit 的可编辑块，并强调 description 决定 agent 如何读写该块。

来源：https://arxiv.org/abs/2310.08560 ，https://docs.letta.com/guides/core-concepts/memory/memory-blocks

对 Omubot 的启发：

- `config/soul/*.md` 应视为只读核心人格，不被学习系统直接改写。
- 动态风格档案可以成为可回滚、有限字符数的 procedural memory block。
- 每个记忆块都需要明确用途描述、写入权限、字符预算和冲突策略。

### 2.3 Reflexion：失败/反馈转成语言记忆

Reflexion 的核心不是微调模型，而是把反馈转成自然语言反思，保存进 episodic memory，在下一次决策中使用。

来源：https://arxiv.org/abs/2303.11366

对 Omubot 的启发：

- 管理员反馈、用户纠正、冷场、误用黑话、过度复读，都应变成“下次怎么做”的反思记录。
- 表达学习不能只保存 `situation + style`，还要保存“为什么这次有用/无效”。
- bot 回复弱信号采集必须接通，否则无法形成自我修正循环。

### 2.4 LangMem：语义 / 情节 / 程序三类记忆

LangMem 文档把长期记忆分为 semantic、episodic、procedural：

来源：https://langchain-ai.github.io/langmem/concepts/conceptual_guide/

- semantic：事实、知识、偏好；
- episodic：过去发生过什么、当时怎么处理、为什么有效；
- procedural：系统行为和响应模式。

对 Omubot 的映射：

| 记忆类型 | Omubot 当前承载 | 当前缺口 |
| --- | --- | --- |
| Semantic | `docs/knowledge`、`memory_cards.db`、`knowledge_graph.db` | 检索统一但不含黑话/表达；图谱事实很少 |
| Episodic | `ConversationArchive`、message log、style/slang evidence | 缺少“成功/失败经验”结构化抽取 |
| Procedural | `config/soul/*.md`、style profile、group profile | style profile 未真正启用；反馈不能自动优化规则 |
| Lexical / Social Slang | `slang.db` | 与知识/表达只是弱耦合 |

### 2.5 Zep / Mem0：生产化图谱记忆与可审计上下文

Zep 强调 temporal knowledge graph，把持续对话和业务数据综合成带时间关系的图谱。Mem0 强调从对话中提取、巩固、检索显著信息，并用图记忆表达复杂关系。

来源：https://arxiv.org/abs/2501.13956 ，https://arxiv.org/abs/2504.19413

对 Omubot 的启发：

- 纯 BM25/ngram 适合轻量起步，但真人式记忆需要时间、关系、来源证据和置信度。
- `knowledge_graph.db` 不应只是 context 命中后的附属派生层，应该能接收来自对话、黑话、表达样本的实体关系。
- 图谱检索应支持“谁在什么群、何时、对哪个词/事件形成了什么关系”。

### 2.6 SillyTavern：世界书/角色书的触发和插入秩序

SillyTavern 的 World Info / Lorebook 是成熟的 prompt 管理经验：通过关键词或规则激活条目，按插入顺序、位置、上下文来源和 token 预算动态加入 prompt。

来源：https://docs.sillytavern.app/usage/core-concepts/worldinfo/

对 Omubot 的启发：

- Omubot 需要一个统一 prompt budget manager，而不是各插件各自决定是否注入。
- 黑话、知识、表达、角色档案都应有插入优先级和“离回复尾部的距离”策略。
- 触发不仅要靠关键词，还应支持递归触发、作用域、冷却、sticky、强制/弱提示。

## 3. 本地实现现状

### 3.1 已经做得不错的部分

1. `ContextPlugin` 统一打包 memory/doc/graph 为 `上下文资料`，避免 Memo 和 Knowledge 重复注入。
2. `SlangPlugin` 有完整的黑话链路：消息观察、候选、人工审核、backlog reviewer、prompt 注入、`slang_lookup`。
3. `StylePlugin` 已有表达样本、证据、反馈存储与 hook 代码雏形、动态风格档案和 prompt 注入设计；但运行时反馈回调仍需修通。
4. `ConversationArchive` 已经开始成为 scanner cursor 和 evidence ref 的底座。
5. `LearningNormalizerStore` 已经提供归一化聚类、拆分、锁定和回滚能力；当前可确认 style 写入路径。2026-05-19 基石补丁 P3 已确认：slang 有历史数据和测试覆盖，但运行时写入路径**尚未接入** `LearningNormalizerStore.attach_candidate(domain="slang")`，A1.3 应补齐而不是继续复核。

本地数据快照：

| 存储 | 当前观测 |
| --- | --- |
| `storage/knowledge_index.db` | 7 个 source，31 个 chunk |
| `storage/slang.db` | approved 237，candidate 632，muted 196，observations 4407 |
| `storage/style.db` | pending 20，rejected 1，approved 0，enabled profile 0 |
| `storage/learning_normalizer.db` | slang clusters 354 / items 449；style clusters 10 / items 28 |
| `storage/knowledge_graph.db` | active graph facts 1 |

### 3.2 关键风险

1. **三层没有真正统一检索**  
   `ContextService` 只聚合 memory/doc/graph；slang/style 仍各自独立注入。当前耦合点主要在 prompt，不在检索和巩固。

2. **表达层没有进入真实行为闭环**  
   当前 style 只有 pending/rejected，没有 approved 表达或 enabled profile，运行时基本没有可注入内容。`StylePlugin.on_post_reply()` 也受 `plugin.json` 缺少 `reply` 权限影响，弱信号采集不会在真实 bus 路径执行。

3. **黑话与表达职责边界有过滤，但没有反向学习**  
   表达抽取会过滤黑话，但黑话命中不会生成表达经验，表达 profile 也不会帮助黑话判断“这是词义还是语气/节奏”。

4. **知识图谱太弱**  
   当前 active graph fact 只有 1 条，图谱还不是长期记忆主干。它没有充分吸收对话、黑话、表达证据。

5. **缺少反思巩固任务**  
   系统有抽取、审核、注入，但缺少定期把一段时间的观察压缩成高层事实、关系、表达规则、失败经验的 consolidator。

6. **learning normalizer 的 slang 接入状态需要复核**  
   `learning_normalizer.db` 中已有 slang cluster/item，但当前代码审计只明确看到 style 写入 normalizer。后续应确认 slang 运行时是否仍写入，或补齐 attach 路径，避免把历史治理数据误当成当前闭环。

### 3.3 模块定位说明（2026-05-18 第三轮复审追加）

复审两轮后发现 style / knowledge 两个模块在多层记忆框架里的**定位**没说清。本节明确两者在框架中的角色：

#### style 模块

- **定位**：第三层（procedural memory）的承载——风格 / 表达习惯 / 弱信号反馈。
- **当前状态（2026-05-17 实测）**：代码完整、`on_pre_prompt` + `on_post_reply` 都已实现；但 `plugin.json` 缺 `reply` 权限导致 on_post_reply 从未被触达；`style_expressions` 表 0 approved；`style_profiles` 表 0 enabled —— **结构对、但运行时是死的**。
- **拖后腿风险**：低。原因是修复路径明确（A0.1 补 reply 权限 + A0.4 离线数据填充），半天工作量内可让第三层真正进入运行时。
- **多层框架中的角色**：承重柱。Phase A.5 时 style_expressions 写入会产出 graph edge；Phase D 时反思可基于 style profile 作为 procedural 输入。
- **修复动作落点**：[§ 5 Phase A0.1 + A0.4](#phase-a0--快速-p0-修复1-工作日)。

#### knowledge 模块

- **定位**：文档检索器（RAG），不是基石级"事实层"。
- **当前状态（2026-05-17 实测）**：默认 `enabled: false`；启用后做简单 top_k 检索注入；live DB `knowledge_index.db` 含 7 source / 31 chunk（手写文档），与 `knowledge_graph.db` 的 75 candidates / 1 active fact **不打通**（chunk 不写出 graph edge，graph candidates 不进 prompt）。
- **拖后腿风险**：中。**不是因为代码烂，而是因为定位错位**——现有 knowledge plugin 与多层记忆框架是**平行**关系而非**承上启下**关系，但首版本路线图把它和"事实层"混在一起讲。
- **多层框架中的角色**：**邻居模块**，不是承重柱。
  - 真正的"长期事实层"在 Phase F `declarative_facts` 表（远期，由 episode 凝练而来）。
  - 真正的"跨层语义索引"在 Phase A.5 `knowledge_graph` schema 扩展。
  - knowledge plugin 仍可作为用户配文档库时的 RAG 工具继续存在，但**与 Phase A-F 解耦**：是否启用是用户偏好，不影响多层框架推进。
- **悬挂问题**：knowledge_index ↔ knowledge_graph 数据互通缺失（详见 § 6 Q9 待研讨）。
- **修复动作落点**：[§ 6 Q9 待研讨](#6-研讨问题清单2026-05-18-拍板)；本轮不强制处理，以"邻居模块"身份继续运行。

## 4. 建议目标架构

### 4.1 分层模型

建议把 Omubot 的长期学习拆成 5 类 typed memory：

| 层 | 职责 | 当前对应 | 目标 |
| --- | --- | --- | --- |
| Observation | 原始经历流 | ConversationArchive / MessageLog | 所有学习从这里派生 |
| Semantic | 事实、设定、偏好 | Knowledge / CardStore / Graph | 统一检索，带来源与置信度 |
| Lexical | 黑话、别名、词义 | SlangStore | 解释“这是什么意思” |
| Procedural Style | 说话方式、接话策略 | StyleStore / style profile | 解释“这种场景怎么说” |
| Episodic Reflection | 成功/失败案例和反思 | 当前缺失 | 解释“上次为什么有效/无效” |

### 4.2 新增核心组件

```text
MemoryConsolidator
  - 输入：ConversationArchive 扫描窗口、slang/style/feedback/graph evidence
  - 输出：typed candidates
  - 行为：分类、去重、冲突检测、反思、写回

MemoryRouter
  - 决定一条观察应进入 semantic / lexical / procedural / episodic 哪些层
  - 可一条观察多写：例如“猫饼”既进入 slang，也附带一次 episode

UnifiedRetrievalPlanner
  - 输入当前消息、群 profile、是否 @、回复目标
  - 输出一个 retrieval plan：需要知识？黑话？表达？情节反思？

PromptBudgetManager
  - 统一控制插入顺序、字符预算、priority、sticky、冷却
  - 避免每个插件各自抢 prompt
```

### 4.3 回复前检索流程

```text
当前消息
  → QueryAnalyzer
      - 主题、实体、黑话候选、语气场景、是否求事实
  → UnifiedRetrievalPlanner
      - memory/doc/graph
      - slang direct hits + tool fallback
      - style approved + enabled profile
      - episodic reflections
  → PromptBudgetManager
      - 核心人格
      - 群 profile
      - 当前目标
      - 必要知识
      - 黑话解释
      - 表达/情节反思
  → LLM reply
  → PostReplyFeedback
      - 记录成功/失败/中性弱信号
```

## 5. 阶段路线图

### Phase A：修通现有闭环（拆为 A0/A1/A2/A3，原 6 项过载，2026-05-18 复审拆分）

> **2026-05-18 第二次拍板调整**：首版本曾把 6 项工作面压进单个 Phase A，包括 schema 迁移和大重构，违背"P0 修通现有闭环"的原意。本次按"P0 修复 → 数据治理 → 基础设施迁移 → schema 新建"的依赖链拆为 4 个独立 sub-phase。原 Phase A.1–A.6 的工作面**全部保留**，重新分配到 A0/A1/A2/A3。
>
> Phase A 内部依赖序列：A0（无依赖，可立刻起）→ A1（依赖 A0 的 update_term 闸）→ A2（独立基础设施工程）→ A3（依赖 A2 的隐私字段，依赖 Phase B BlockTraceBus 才能 enabled_for_prompt）。
>
> graph schema（原 A.5）整体延后到 **Phase A.5**（介于 Phase A 与 B 之间，原 Phase E 提前点），不在 Phase A 内部。

#### Phase A0 — 快速 P0 修复（~1 工作日）

可独立起，无依赖。四件事一天落地：

- **A0.1** 给 `plugins/style/plugin.json` 增加 `reply` 权限，补 bus 级回归测试，确保 `fire_on_post_reply` 能触达 `StylePlugin.on_post_reply`。（决议 7.2a 第一步）
- **A0.2** `services/slang/store.py` `update_term` + `merge_terms` 加碰撞校验 + 跨 scope/group 闸；详见 [`slang-collision-thinker-audit-2026-05-18.md` 阶段 A](slang-collision-thinker-audit-2026-05-18.md)。
- **A0.3** Thinker 决策注入直接命中摘要（≤200 字 system_text 末尾追加，遵守 `group_profile.slang_enabled` 守护）；详见同上阶段 C。
- **A0.4** **style 模块运行时数据填充**（决议 7.2a 第二步，2026-05-18 第三轮复审追加）：A0.1 修复 reply 权限后，style 仍因 `style_expressions` 表 0 approved + `style_profiles` 表 0 enabled 而**实际不进 prompt**。本步骤为离线动作（不动代码）：
  - 在 `/admin/style` 审核通过 ≥ 5 条高置信 style_expressions（`status='pending' → 'approved'`）。
  - 为至少 1 个活跃群生成并启用 `style_profiles` 记录（`status='enabled'`）。
  - 用 admin trace 验证下一轮回复中 style 块实际进入 prompt（label="动态风格档案" 或 "表达习惯参考"）。
  - **来源**：§ 3.1 现状分析（live DB 实测 0 approved / 0 enabled profile，是 style 第三层"在运行时是死的"的根本原因）+ § 8.2 ② "激活 style 可用数据"。

A0 不动 schema、不动迁移、不开新插件。A0.4 是离线 admin 动作，与 A0.1-A0.3 互不阻塞。

| A0 验收项 | 状态预期 |
| --- | --- |
| `plugins/style/plugin.json` 含 `reply` permission；fire_on_post_reply 能触达 on_post_reply | green（A0.1 落地后） |
| `update_term(yi, aliases=['甲'])` 当甲、乙同 scope 时 raise ValueError | green（A0.2 落地后） |
| `merge_terms(target=A群词, source=B群词)` raise ValueError | green（A0.2 落地后） |
| Thinker system_text 含 direct hit 摘要；`slang_enabled=False` 时跳过 | green（A0.3 落地后） |
| **`style_expressions` 表 ≥ 5 条 `status='approved'`** | **green（A0.4 落地后）** |
| **`style_profiles` 表 ≥ 1 条 `status='enabled'`** | **green（A0.4 落地后）** |
| **A0.1 完成后下一轮回复 admin trace 含 style 注入块** | **green（A0.4 端到端验证）** |
| **`max_indirect_inject_terms` 默认值** | **green（2026-05-19 P2 落地）** —— 后端与前端默认值均为 `0`，恢复 direct-only；未来若 BlockTraceBus 证据支持，可再显式调回 1 或 2 |

#### Phase A1 — 数据治理（~半天，依赖 A0.2）

- **A1.1** 修 `scripts/dev/slang_alias_collision_report.py` `--status` 口径（删除该 flag 或改为"至少一端符合"语义）。来源：§ 8.2.4c。
- **A1.2** 跑 collision report，admin 用现有 `merge_terms` API 逐对人工合并历史 72 对碰撞（24 对涉及 approved）。来源：§ 8.2.4c。
- **A1.3** 已确认 slang 运行时**未写入** `LearningNormalizerStore`；本轮补齐 candidate/term 写入 normalizer 的 attach 路径（保持 § 7.5 决议 5 的"normalizer 纯 domain"职责）。来源：§ 8.2.4a + § 7.5 + 2026-05-19 基石补丁 P3。

A1 是离线动作，不动产品代码。

| A1 验收项 | 状态预期 |
| --- | --- |
| `slang_alias_collision_report.py` 输出 `pairs=0`（或仅剩明确白名单） | green（落地后） |
| live DB approved 词条数稳定（合并源词条转 expired） | green（落地后） |
| `LearningNormalizerStore.attach_candidate(domain='slang')` 在新写入路径上被调用 | pending（A1.3 已确认缺失，待补齐后转 green） |

#### Phase A2 — 隐私字段全层迁移（~3-5 工作日，独立基础设施工程）

> **审计标注**：原 Phase A.6 把这块当作"加几个字段"是低估了。当前 4 个 schema (slang_terms / style_expressions / learning_normalizer_clusters / knowledge_graph_*) 各有自己的 scope/group_id 字段，但**完全没有** `cross_group_visibility_enabled / enabled_by / enabled_at / enabled_for_groups[]`。新增字段 + 历史回填 + 双跑兼容期是独立工程。

详细迁移计划见 § 8.4。Phase A2 的范围：

- **A2.1** schema 设计 review：4 张表的 alter 顺序、字段类型、索引、回填默认值。
- **A2.2** alter table + 历史数据回填（`cross_group_visibility_enabled=false`、`enabled_*` 全部 NULL）。
- **A2.3** 读取兼容期：所有现有 query 增加默认过滤；新代码用统一 helper `apply_cross_group_filter(query, viewer_group_id)`。
- **A2.4** admin UI 跨群启用审计页面：按 `enabled_at` 时间线查看 + 撤销启用 + 模拟以群 X 视角调试。
- **A2.5** 写入路径强约束：`cross_group_visibility=true` 不可被插件默认值 / 迁移脚本 / 自动促进路径覆盖；只能由 admin UI 显式启用。

A2 是 Phase A3 / Phase A.5 graph schema 的硬前置。

| A2 验收项 | 状态预期 |
| --- | --- |
| 4 张表 schema 完成 alter，含所有 cross_group_* 字段 | green（落地后） |
| 现有数据回填，默认 `cross_group_visibility_enabled=false` | green（落地后） |
| 任何写入路径不能绕过显式 admin 启用步骤设置 cross_group=true | green（落地后） |
| admin UI 启用 audit 页面 + 模拟视角调试 | green（落地后） |

#### Phase A3 — episode schema 与状态机（~2-3 工作日，依赖 A2）

- **A3.1** 设计 `episode` 层 schema（含 `decay_at` / `last_used_at` / `per_group_max_active` 上限 + § 7.5 隐私字段）。
- **A3.2** 实现 5 态状态机（详见 § 6 Q2 状态机说明）：`dry_run / candidate / approved / enabled_for_prompt / disabled`，每次状态变更记 revision。
- **A3.3** Consolidator 草稿 → admin queue：episode 进入 `dry_run` 后由 reflection 流程标记为 `candidate`（待 Phase D 落地），人工 approve 后转 `approved`，但默认**不进 prompt**。
- **A3.4** admin UI：列表查看 + 一键 disable + 显示衰减剩余 + 状态机当前态显示。

> **不**在 Phase A 内开 `enabled_for_prompt`。等到 Phase B BlockTraceBus 落地、能完整 trace 每条 episode 进 prompt 的原因后，admin 才能把状态机推进到 `enabled_for_prompt`。

| A3 验收项 | 状态预期 |
| --- | --- |
| episode 表存在 + 5 态状态机实现 | green（落地后） |
| consolidator dry-run 写 episode → admin 看到 candidate 列表 | green（落地后） |
| admin 能 approve 但不能直接 enabled_for_prompt（需 BlockTraceBus 才解锁） | green（落地后） |
| episode 仍**不进 prompt**（运行时空跑），等 Phase B 解锁 | open（设计上待 Phase B） |

### Phase B：统一 prompt 预算

目标：从“插件各自注入”升级到“统一编排”。

> **2026-05-18 复审注**：迁移分 3 步进行，详见 § 8.3 第 1 项。Phase B 完成 = 步骤 3 完成；中间状态（步骤 1/2）不算 Phase B 完成。

- 新增 `PromptBudgetManager`，插件最终只上报候选块和 priority（步骤 3 后）；中间步骤 1/2 期间插件 on_pre_prompt 注入与 BudgetManager 并存，详见 § 8.3。
- 支持 block 类型：core、stable、dynamic、tail、tool-hint。
- 每个块记录来源、scope、命中原因、字符数、被裁剪原因。

验收：

- 同一轮最多注入多少知识/黑话/表达有可解释预算。
- 高优先级 direct slang 不会被普通 doc chunk 挤掉。
- style 不会覆盖 core soul。

#### Phase B 前置审计（2026-05-20）

> **背景**：2026-05-19 应额外要求完成了 `cache_control` 注入下沉到 LLMRequest spine 的重构（`TaskCacheProfile` + `apply_cache_breakpoints` + ≤4 marker cap）。本次审计确认该重构**不等于 Phase B**，仅为 Phase B 的基础设施前置。

**spine 重构已完成的部分**（Phase B 地基）：

| 能力 | 状态 | 位置 |
|------|------|------|
| 单一 cache_control 注入点 | ✅ | `services/llm/llm_request.py:apply_cache_breakpoints` |
| 按 LLMTask 配置断点预算 | ✅ | `TaskCacheProfile` + `TASK_CACHE_PROFILES` 18 task 注册表 |
| ≤4 ephemeral marker 硬 cap | ✅ | spine 内部计数，上游预设被剥离 |
| segment 分类 static/stable/dynamic | ✅ | `_SEGMENT_STATIC/STABLE/DYNAMIC`，用于 cache 分段 |

**Phase B 仍需实现的核心**：

| 能力 | 差距 |
|------|------|
| `PromptBudgetManager` — 插件上报候选块 + priority，统一编排 | 不存在。插件仍通过 `on_pre_prompt` 直接注入文本 |
| block 类型语义（core/stable/dynamic/tail/tool-hint）用于**预算仲裁** | segment 仅用于 cache 分段，不做优先级裁剪 |
| 每块记录来源/scope/命中原因/字符数/被裁剪原因 | 不存在，无 trace |
| `BlockTraceBus` — 5 字段 trace 记录 + 写入/查询 API | 不存在 |
| 优先级仲裁（direct slang > doc chunk） | 不存在 |
| § 8.3 三步迁移（步骤 1: ContextProvider 并存 → 步骤 2: 双跑 → 步骤 3: 插件只上报） | 未开始 |

**结论**：spine 重构为 Phase B 铺好了"谁来放 cache marker"的地基，但 Phase B 的核心问题——"谁进 prompt、谁被裁剪、为什么"——尚未开始。Phase B 的前置依赖链：`A3(episode schema) → A.5(graph schema) → Phase B`。

### Phase C：MemoryConsolidator

目标：从观察中同时派生多个学习层。

- 以 `ConversationArchive` 为唯一扫描源。
- 一次扫描输出 typed candidates：fact、slang、style、episode、graph_relation。
- 用 `LearningNormalizerStore` 做跨候选去重和审计。
- 先 dry-run，把写入建议展示在 Admin，不自动落库。
- 每条写入建议必须携带 scope、privacy level、source refs 和可回滚策略。

验收：

- 同一条消息能同时生成“黑话候选 + 表达候选 + episode 反思”。
- 审计页能看到为什么分到某层、为什么没分到另一层。

### Phase D：Episodic Reflection

目标：补上真人式长期学习最缺的一层。

- 新建 `services/episodic/` 或扩展 `services/style` 的 feedback 为 episode。
- episode schema：
  - situation
  - observed_context
  - action_taken
  - outcome_signal
  - reflection
  - linked_memory_ids
  - confidence / decay / last_used_at
- 从管理员反馈和用户纠正中生成“下次怎么做”的反思。

验收：

- bot 被纠正一次后，后续同类场景能召回反思。
- episode 不直接改人格，只作为动态经验提示。

### Phase E：图谱成为长期记忆骨架

> **2026-05-18 拍板调整**：因 § 6 Q4 选定"graph 为跨层主索引"+ § 7.4 决议，**Phase E 已部分提前到 Phase A.5**（graph schema + 首批 edge 类型）。本节保留作为 graph 完整能力的远期目标，但 schema 设计 + 写入路径双写在 Phase A.5 即落地，不再等到所有其它 Phase 完成。

目标：让知识图谱承载跨层关系，而不是只有少量派生事实。

- graph fact 来源扩展到 slang/style/evidence/episode。
- 新增边类型：
  - term_used_in_group
  - style_applies_to_situation
  - user_corrected_bot_about
  - episode_supports_profile
  - doc_supports_fact
- 检索时图谱可补充相关实体、群、用户、事件链。

验收：

- 问一个词、一个人、一个群内事件时，系统能组合 slang + memory + episode + graph。

### Phase F：Episodic-to-Declarative 沉淀（长期记忆，远期）

> **2026-05-18 第三轮复审追加**：原路线图缺一个"长期记忆沉淀层"——episode 不会自然凝练为 declarative facts，意味着 bot 跑半年后会记得**很多具体事件**但没有提炼出**关于某人/某群的稳定认知**。本 Phase 补这一层，对应学术界 2026 年公认的 missing piece（[Position: Episodic Memory is the Missing Piece](https://arxiv.org/abs/2502.06975)）和 [Experience Compression Spectrum](https://arxiv.org/abs/2604.15877) 中 1,000×+ 压缩比的 declarative rules 层。
>
> **核心论点**：1000 条 episode 应该能凝练成几十条 declarative facts（"X 偏好 RPG 类游戏""X 习惯晚上活跃""该群忌讳催更"），让 bot 从"记得"升级到"懂"。

目标：让 bot 从经验中提炼稳定认知，而不是无限堆积 episode。

#### Phase F 前置依赖（硬要求）

- Phase D Episodic Reflection 已落地并跑过 ≥ 3 个月真实数据
- BlockTraceBus 已在 Phase B 落地（用于 F.6 回退路径）
- admin 工作量观察证明 episode 5 态状态机可持续运行（candidate → approved 不出现长期积压）
- 至少有 1 个 group 累积了 ≥ 200 条 enabled_for_prompt 状态的 episode（凝练才有素材）

不满足任何一条则 Phase F 不开。

#### Phase F.1 — declarative_facts 层 schema

- 设计 `declarative_facts` 表：
  - `fact_id` / `subject` (如 user_id / group_id) / `predicate` / `object` / `confidence`
  - `source_episode_ids` (JSON 列表，必须 ≥ 2 才允许凝练)
  - `source_evidence_count` / `source_time_span_days`
  - `fact_state` (沿用 5 态状态机：dry_run / candidate / approved / enabled_for_prompt / disabled)
  - `decay_at` (可选，默认 NULL = 永不过期；admin 可手动设)
  - `last_used_at` / `usage_count`
  - `revision` JSONB（含历史所有状态变更 + 凝练源 episode 链 + admin 操作记录）
  - § 7.5 跨群隐私字段全套（cross_group_visibility_enabled / enabled_by / enabled_at / enabled_for_groups）
- 索引：`(subject, predicate, fact_state)` + `(group_id, fact_state, decay_at)`

#### Phase F.2 — 凝练触发器

何时尝试凝练（满足任一即触发）：

- 同一 subject 累积 ≥ 5 条相关 episode 且时间跨度 ≥ 14 天
- 同一 subject 在 graph 上累积 ≥ 8 条同 predicate 类型的 edge
- admin 手动触发（提供 subject + predicate hint）

#### Phase F.3 — 凝练算法

- 输入：触发器命中的 episode 列表 + graph 邻居 edge + 该 subject 现有 declarative facts（用于一致性检查）
- LLM prompt：要求输出 typed fact 候选，每条必带：
  - `proposed_predicate` / `proposed_object`
  - `confidence` 0.0-1.0
  - `source_episode_ids`（必须从输入选，不能凭空）
  - `source_evidence_quotes`（每条 episode 抽出支持本 fact 的具体片段）
  - `contradicts_existing_facts`（如果与现有 fact 互斥，列出 fact_id）
- 输出格式强约束（JSON schema 校验失败则丢弃）
- **本步骤不写库**——只产出 candidates 进入 F.4 / F.5 流程

#### Phase F.4 — 冲突解决（最关键的一环）

- 如果新 candidate 与 enabled_for_prompt 状态的现有 fact 互斥：
  - 不自动覆盖，进 admin conflict queue
  - admin 看到对比页面：旧 fact + 证据 vs 新 candidate + 证据
  - admin 决策：保留旧 / 替换为新 / 合并（创建复合 fact）
- 如果新 candidate 与 candidate / approved 状态的 fact 互斥：
  - 自动 merge 到同一 admin review item
  - 等 admin 一次性决断

#### Phase F.5 — declarative fact 5 态状态机

沿用 § 6 Q2 episode 状态机的 5 态结构，但**进 enabled_for_prompt 的门槛更严**：

- `dry_run → candidate`：自动晋升条件 = LLM 凝练输出 + JSON schema 通过
- `candidate → approved`：admin 显式 approve（不允许自动）
- `approved → enabled_for_prompt`：admin 显式推进 + **本群至少 30 天连续无负反馈**（用 BlockTraceBus 数据验证）
- 任何状态 → `disabled`：admin 显式动作 / decay_at 到期 / F.6 回退路径触发

`enabled_for_prompt` 的 fact 才会进入 ContextService 检索；BudgetManager 给它一个独立的 layer 桶（不挤占 episode / lexical / style 预算）。

#### Phase F.6 — 回退路径（错凝练修复）

如果 admin 后来发现某条 enabled_for_prompt 的 fact 实际错了：

- admin 在 fact 详情页点 "rollback"
- 系统通过 BlockTraceBus 查出**所有引用过该 fact 的 PromptBlock trace**（最近 N 天）
- 列出受影响的 request_id 列表（admin 看到"该 fact 影响了过去 30 天的 47 次回复"）
- fact 状态强制转 `disabled`，标注 `disabled_reason='admin_rollback'`
- 可选：admin 选择是否对应清除/重新评估这些 request 触发的 episode

> **F.6 是 Phase F 不能没有 BlockTraceBus 的根本原因**——declarative fact 是后续所有对话的前提假设，如果它错了，必须能查出"这个错的前提影响了哪些回复"。

#### Phase F 验收

| F 验收项 | 状态预期 |
| --- | --- |
| `declarative_facts` 表 schema 完成 + 5 态状态机实现 | green（落地后） |
| 凝练触发器跑出 ≥ 1 条 candidate fact 进 admin queue | green（落地后） |
| 冲突 queue 能正确处理互斥 candidate | green（落地后） |
| F.6 rollback 路径能列出受影响 request_id 列表 | green（落地后） |
| 至少 1 条 fact 进入 enabled_for_prompt 状态并被 BudgetManager 召回 | green（落地后） |
| `enabled_for_prompt` 推进按钮在 BlockTraceBus 30 天数据之前**禁用** | green（机制层面强约束） |

#### Phase F 显式风险（必读）

1. **错凝练比错 episode 严重得多**：episode 有 decay 自然消失（30 天），fact 默认无 decay 永不过期；episode 影响单次回复，fact 影响后续所有相关对话。F.5 的多重门槛（admin approve + 30 天无负反馈）就是为了对冲这个风险。
2. **admin 工作量增加**：F.4 冲突 queue + F.5 双重 approve 都是新的人在回路环节。如果 Phase D 已经显示 admin 跟不上 episode review，Phase F 不应开。
3. **学术界尚未成熟**：[2502.06975](https://arxiv.org/abs/2502.06975) 是立场论文，不是工程范式；[2604.15877](https://arxiv.org/abs/2604.15877) 指出"missing diagonal" = 全行业未解。Phase F 是**前沿探索**，不是实施已知最佳实践。验收时应保留"实验性 feature"标签。
4. **隐私放大**：declarative fact 比 episode 更"具人格画像感"（"X 是 Y 类型的人"）。即使 cross_group_visibility=false，仅在原始群可见，仍可能被该群成员通过 bot 行为推断出。F.1 schema 应支持"sensitive=true"标志，sensitive fact 即便 enabled_for_prompt 也不进 system prompt 的可见层，只用作隐式背景。

## 6. 研讨问题清单（2026-05-18 拍板）

| # | 问题 | 选定方案 | 关键含义 |
| --- | --- | --- | --- |
| Q1 | `ContextService` 是否应该接管 slang/style？ | **接管全部检索** | SlangPlugin / StylePlugin 保留学习/抽取/审核能力，**降为 ContextService 的 provider**；插件本身不再独立 fire `on_pre_prompt` 注入。BudgetManager 只管预算和裁剪决策。 |
| Q2 | style profile 是否 procedural memory？episode 是否自动生成？ | **是 procedural；episode 自动从 dry_run 走到 candidate；进入 prompt 仍需 admin 显式启用** | episode schema 内置 5 态状态机（详见下方 Q2 状态机说明）：`dry_run / candidate / approved / enabled_for_prompt / disabled`。"自动启用"指 reflection 流水线**自动生成**草稿（dry_run → candidate），**不**指自动进 prompt。`enabled_for_prompt` 仅在 BlockTraceBus 落地后由 admin 推进，确保所有进 prompt 的 episode 都可 trace。schema 必须含 (a) `decay_at` 衰减字段、(b) `per_group_max_active` 上限以防一个糟糕反思支配整个 group、(c) `disabled_by_admin` 显式开关。 |
| Q3 | 黑话是否记录"伴随情绪/场景"？ | **合并到统一 fact graph**（依赖 Q4） | lexical 层不扩 meta；语义关系下沉到 graph，由 graph edge 表达 `term_used_in_context`、`term_implies_emotion` 等。 |
| Q4 | `knowledge_graph.db` 是否应成跨层主索引？ | **是跨层主索引** | 所有层写入时同步生成 graph node/edge；ContextService 内部以 graph 为入口检索；memory/slang/style/episode 是 graph 边的属性。**最大决议**——graph schema 设计成为 Phase A 关键路径，原 Phase E "图谱成为长期记忆骨架"提前到 Phase A-B 之间。 |
| Q5 | learning normalizer 是否支持跨 domain cluster？ | **纯 domain；跨 domain 走 graph** | normalizer 专注 attach_candidate(domain=...) 内部去重；graph 表达跨 domain 语义（slang term ↔ style expression ↔ episode reflection）。每条新写入双写：normalizer + graph edge。 |
| Q6 | global memory / 跨群写入是否硬门槛？ | **人工启用 + 全层适用 + 默认关闭** | 所有写入路径必须带 `scope` + `cross_group_visibility_enabled_by` + `enabled_at` + `enabled_for_groups[]`；默认 `cross_group_visibility=false`，不可被插件默认值覆盖；admin UI 必须能看到启用人/时间/范围。 |
| Q7 | 是否为每个 prompt block 打 trace？ | **全量 trace + admin UI 打开** | 每条 PromptBlock 进 prompt 时打 `trace_id`，记录 (a) 来源层 + provider、(b) 命中原因（direct hit / scope 优先 / graph edge）、(c) 源证据 ref（message_id / cluster_id / term_id / graph_edge_id）、(d) 过期状态 + decay 剩余、(e) BudgetManager 采纳 vs 裁剪决策。`BlockTraceBus` 作为 Phase B 必要交付，A0 决议立即受益。 |
| Q8 | 记忆是否应自行迭代为长期记忆？ | **是，但放入 Phase F 远期；不在本轮 Phase A-E 范围** | episode 不会自然凝练为 declarative facts，1000 条具体事件应能压缩为几十条稳定认知（"X 偏好 RPG""该群忌讳催更"）。学术认可为 missing piece（[Position: Episodic Memory is the Missing Piece](https://arxiv.org/abs/2502.06975)）+ Experience Compression Spectrum 1,000×+ 压缩比的 declarative rules 层。**前置依赖**：Phase D 跑过 ≥ 3 个月真实数据 + BlockTraceBus 已落地（用于 F.6 rollback 路径）+ admin 可承受 episode review 工作量。**显式风险**：错凝练比错 episode 严重得多（episode 有 decay 自然消失，fact 默认永不过期；fact 影响后续所有相关对话）。详见 § 5 Phase F。 |
| Q9 | knowledge_index（文档 chunks）应否产出 graph edge 进 knowledge_graph？ | **待研讨，本轮不拍板** | 当前 `knowledge_index.db`（7 source / 31 chunk）与 `knowledge_graph.db`（75 candidates / 1 active fact）**不打通**：chunks 检索结果只 `add_block(label="知识库")` 进 prompt，不写 graph edge；graph candidates 也不进 prompt。两条数据线平行存在但互不流通。本问题与 § 3.3 knowledge 模块定位说明绑定——knowledge plugin 在多层框架中是**邻居**而非承重柱，是否打通 chunk → graph edge 应等 Phase A.5 graph schema 落地后视实际需要再决议。详见 § 7.8。 |

### Q2 episode 5 态状态机（拍板细化）

> 本子节细化 Q2 决议，消除"自动启用"与"不进 prompt"之间的歧义。所有 episode 在 schema 中存为 `episode_state` 列，状态机如下：

| 状态 | 进入条件 | 出口 | 是否进 prompt | 说明 |
| --- | --- | --- | --- | --- |
| `dry_run` | reflection 流水线（Phase D）首次写入 | → `candidate`（dry-run 累计 N 条置信度 ≥ 阈值后自动转） | 否 | 仅 store 落库，admin 可查但默认隐藏 |
| `candidate` | dry_run 自动晋升 | → `approved`（admin 显式 approve）/ `disabled`（admin 显式拒绝） | 否 | 进入 admin review queue 等待人工 approve |
| `approved` | admin approve | → `enabled_for_prompt`（仅在 Phase B BlockTraceBus 落地后允许）/ `disabled`（admin 显式停用） | 否 | schema 完整、人工已审，但默认仍**不进 prompt**；等待 trace 基础设施 |
| `enabled_for_prompt` | admin 在 BlockTraceBus 可见时显式推进 | → `disabled`（admin 停用）/ `expired`（decay_at 到期自动转） | **是** | 唯一进 prompt 的状态；BudgetManager 仅消费此状态的 episode |
| `disabled` | admin 显式停用 / decay 到期（→ `expired` 是 disabled 的子类型） | （终态，可手动改回 approved 或更高） | 否 | 永久停用或暂时停用；admin UI 可见 |

**关键约束**：

- `enabled_for_prompt` **不可**自动从 `approved` 进入；必须 admin 在 admin UI 显式推进，且推进按钮在 Phase B BlockTraceBus 落地之前**禁用**。
- `dry_run → candidate` 是唯一允许的自动晋升边；其它状态变更全部要求 admin 显式动作或 decay 触发。
- 所有状态变更写 revision，含 `prev_state / new_state / actor / reason / timestamp`。
- BudgetManager 消费时 SQL 必须有 `WHERE episode_state = 'enabled_for_prompt' AND (decay_at IS NULL OR decay_at > now())`。

## 7. 研讨决议（2026-05-18 拍板）

本节原为"推荐决议"草稿，2026-05-18 与用户研讨后拍板，结合 § 6 Q1-Q7 决议得到下述 5 条最终决议。原草稿保留，旁附拍板后的最终表述。

### 7.1 决议 1 — 弱耦合现状

> **草稿**：承认当前是弱耦合，不把它宣传成真人式多层记忆。

**拍板（2026-05-18）**：保留草稿。补一条上下文："Phase A 落地 graph 主索引（Q4）后再讨论是否升级宣传口径；在此之前文档不得使用'多层记忆'作为已实现能力的宣传术语。"

### 7.2 决议 2 — style 第三层进入运行时（拆为 2a / 2b 并行）

> **草稿**：先修 style 反馈与 approved/profile，使第三层真正进入运行时。

**拍板（2026-05-18）**：因 Q2 选定"episode = procedural memory；自动从 dry_run 走到 candidate；进入 prompt 仍需 admin 显式启用"（详见 § 6 Q2 状态机），本决议拆为两个并行子项：

- **2a — 修 style 反馈回调**：plugins/style/plugin.json 增加 reply 权限；新增 bus 级测试验证 fire_on_post_reply → StylePlugin.on_post_reply；trace 验证 style_feedback 写入。落地于 Phase A0.1。
- **2b — 设计 episode 层 schema + 5 态状态机**：内置 `episode_state` 列（5 态）、`decay_at` / `last_used_at` / `per_group_max_active` 上限字段；admin UI 必须能列表查看 + approve / disable + 显示衰减剩余 + 状态机当前态。`enabled_for_prompt` 状态在 Phase B BlockTraceBus 落地之前**禁用**推进按钮；schema 落地后 episode 层默认仍**不进 prompt**。落地于 Phase A3。

两个子项依赖序列：2a 在 Phase A0 起；2b 依赖 Phase A2 隐私字段（Phase A3）。

### 7.3 决议 3 — PromptBudgetManager + BlockTraceBus 同步交付

> **草稿**：新增 PromptBudgetManager，统一三层注入顺序和预算。

**拍板（2026-05-18）**：因 Q1 选定"ContextService 接管检索"+ Q7 选定"全量 trace"，本决议改写为：

- 新增 **PromptBudgetManager**：只管预算 + 裁剪决策（不管检索；检索由 ContextService 走）。**迁移分 3 步进行**（详见 § 8.3 第 1 项）：步骤 1 provider 接口 + 双跑 trace；步骤 2 BudgetManager 接管裁剪（双路径仍并存）；步骤 3 关闭旧插件 on_pre_prompt，BudgetManager 成为唯一注入入口。每步独立可回滚。
- 同期交付 **BlockTraceBus**：每条进 prompt 的 PromptBlock 必带 trace_id，记录 §6 Q7 决议中的 5 类字段（来源 / 命中原因 / 源证据 / 过期状态 / 采纳裁剪决策）。BlockTraceBus 是步骤 1 的前置（用于双跑对齐验证）。
- BudgetManager + TraceBus 是 Phase B 的捆绑交付，不可拆分；Phase B 完成 = § 8.3 步骤 3 完成。

### 7.4 决议 4 — 下一阶段 = MemoryConsolidator + Episodic Reflection + Graph Edge Candidates

> **草稿**：把下一阶段目标定义为 MemoryConsolidator + Episodic Reflection，而不是继续堆更多独立插件。

**拍板（2026-05-18）**：因 Q4 选定"graph 主索引"+ Q5 选定"normalizer 纯 domain，跨 domain 走 graph"，本决议改写为：

- **MemoryConsolidator**：dry-run 输出 typed candidates，类型 = `{fact, slang, style, episode, graph_edge}`；其中 graph_edge 是新增类型，承载跨 domain 关系（如 slang_term ↔ style_expression、episode ↔ profile）。
- **Episodic Reflection**：写入 episode 层时双写 normalizer + graph edge（决议 7.5 决定写入路径双写）。
- **Phase E 提前**：原报告 § 5 Phase E "图谱成为长期记忆骨架"提前到 Phase A 与 Phase B 之间，作为 Phase A.5 — graph schema 设计与首批 edge 类型定义。

### 7.5 决议 5 — 隐私 / 跨群 / global 写入硬门槛（具体化）

> **草稿**：把隐私、群隔离和 global 写入授权作为多层记忆的硬门槛，不作为后置优化。

**拍板（2026-05-18）**：因 Q6 选定"人工启用 + 全层适用 + 默认关闭"，本决议具体化为以下硬约束（所有层 + Phase A 写入路径必须满足）：

1. 每条新写入记录必须带 `scope`（`group` / `global`）+ `cross_group_visibility_enabled`（默认 false）+ `enabled_by`（admin user_id）+ `enabled_at` + `enabled_for_groups[]`（白名单）。
2. `cross_group_visibility=true` 不可被插件默认值或迁移脚本覆盖；只能由 admin UI 显式启用。
3. `cross_group_visibility=false` 时即便 scope=global 也仅在原始群可见，全检索器需统一过滤。
4. admin UI 必须有审计页面：能按 `enabled_at` 时间线查看启用记录、能撤销启用。
5. 这 5 项作为 **Phase A2** 硬验收（§ 5），不通过则不能进 Phase A.5 graph schema。详细迁移计划见 § 8.4.2 / 8.4.3 / 8.4.4。

### 7.6 派生决议 — A0 indirect 默认值的处理路径（来自第二轮审计 § 2.5）

> 见 [`slang-collision-thinker-audit-2026-05-18.md` § 2.5 / 阶段 A0](slang-collision-thinker-audit-2026-05-18.md)

**首版本拍板（2026-05-18）**：用户决定"先采 prompt trace 再拍板"。决议 7.3 中 BlockTraceBus 落地后，A0 拍板自动获得证据；在此之前 SlangSettings.max_indirect_inject_terms 默认保持 2，tracker.md 的 3 条 direct-only 承诺不主动改写。

> **2026-05-18 第三轮复审更新**：本决议已被基石达标补丁 **Patch P2** 取代（见文档顶部 ⚠️ 段）。第三轮复审认定"等 BlockTraceBus 再决议"虽更科学，但等待期间生产里默认走的是与 tracker 承诺不一致的行为，违反基石"不挖坑"原则。Patch P2 决议在 Phase A0 开工前把默认值改回 0；trace 数据未来支持时再调回 1 或 2。Patch P2 落地即关闭本派生决议。

**状态（2026-05-19）**：Patch P2 已落地，本派生决议关闭。

### 7.7 派生决议 — Q8 长期记忆沉淀（Phase F 远期）

> 见 § 5 Phase F + § 6 Q8

**拍板（2026-05-18，第三轮复审）**：用户拍板"不考虑工程量，写入文档"。当前路线图缺一个长期记忆沉淀层，episode 不会自然凝练为 declarative facts，意味着 bot 跑半年后仍只是"记得很多具体事件"而没有"提炼出关于某人/某群的稳定认知"。

本决议结论：

- **方向认可**：episode → declarative fact 凝练是长期记忆能起作用的必要环节，对应学术界 2026 年公认的 missing piece（[2502.06975](https://arxiv.org/abs/2502.06975) 立场论文 + [2604.15877](https://arxiv.org/abs/2604.15877) Compression Spectrum 中 1,000×+ 压缩比层）。
- **工程化定位**：作为 Phase F 远期目标，**不**在 Phase A-E 范围内；前置依赖 Phase D 跑过 ≥ 3 个月真实数据 + BlockTraceBus 已落地。
- **风险标注**：Phase F 是前沿探索，不是实施已知最佳实践（行业未成熟）；错凝练比错 episode 严重得多（fact 默认无 decay + 影响后续所有对话），故 F.5 状态机进 enabled_for_prompt 的门槛比 episode 更严（需 30 天连续无负反馈），F.6 必须能 rollback 受影响的历史回复。
- **不阻塞当前**：Phase F 不影响基石达标补丁 / Phase A0-E 任何决议；仅作为路线图的远期延伸登记。

详细子任务（F.1 凝练 schema / F.2 触发器 / F.3 算法 / F.4 冲突 / F.5 状态机 / F.6 rollback）见 § 5 Phase F。

### 7.8 派生决议 — Q9 knowledge 模块定位与 chunk↔graph 互通（待研讨）

> 见 § 3.3 模块定位说明 + § 6 Q9

**拍板（2026-05-18，第三轮复审，"待研讨"性质）**：用户指出 style / knowledge 长期未验收、担心拖后腿。复审结论是：

- **style 模块**：结构基本对，运行时数据填充缺失。修复路径明确，已落到 § 5 Phase A0.4 + Phase A0 验收表新增 3 行 green 项。本子项已闭环。
- **knowledge 模块**：定位本身错位——它在多层框架中应是**邻居**而非承重柱。已落到 § 3.3 模块定位说明。但**knowledge_index ↔ knowledge_graph 数据互通缺失**是真实空挡，作为 Q9 待研讨。

本决议针对 knowledge 模块结论：

- **本轮不强制处理**：knowledge plugin 默认 `enabled: false`，不影响 Phase A-F 推进；以"邻居模块"身份继续运行。
- **Q9 待 Phase A.5 后视情决议**：Phase A.5 graph schema 落地后，再决定 knowledge_index 的 31 chunks 是否应批量产出 graph edge（如 `chunk_supports_fact` / `chunk_mentions_term` 等）。届时有具体 schema 可以对照，比现在凭空决议更合适。
- **不阻塞当前**：Q9 是设计空挡而非 bug；不放入任何 Patch / Phase A0-F 强制范围。
- **若用户配置 knowledge plugin enabled=true**：当前 RAG 行为继续工作，prompt 含 "知识库" label 块；但**不进 graph、不进 normalizer、不参与 episode 反思**，与多层框架完全平行。

> **不阻塞性原则**：style/knowledge 长期未验收的担忧通过 (a) Phase A0.4 闭环 style 数据填充 + (b) § 3.3 显式标注 knowledge 为邻居模块 + (c) Q9 登记 chunk↔graph 互通待研讨，三件齐做后**两者均不会成为多层框架的拖后腿点**——style 进入运行时活跃；knowledge 以邻居身份独立存在，与框架解耦。

## 8. 修复方案

### 8.1 P0 文档可信度修复

- 已补采样说明，避免把 live DB 数字误读成稳定指标。
- 已把外部研究小节和来源链接逐段绑定。
- 已把 `blackbox daily AI review` 修正为 `slang daily AI review`。
- 已收紧 `StylePlugin` 和 `LearningNormalizerStore` 的表述：区分“设计/存储存在”和“运行时闭环已接通”。
- **lexical 层 prompt 注入承诺与代码默认值已对齐**（2026-05-19 基石补丁 P2）：`docs/slang-module-implementation-tracker.md` 第 38 / 94 / 170 行三处明确承诺"prompt block 只注入直命中已批准黑话"；后端 `SlangSettings.max_indirect_inject_terms` 与前端 `DEFAULT_SLANG_SETTINGS.max_indirect_inject_terms` 均已改回 `0`。未来若 BlockTraceBus 证据支持 indirect 注入，再通过显式配置调整，不再把默认值悄悄设为 2。

验收标准：报告不再把历史数据、测试能力或设计意图误写为当前运行时能力。

### 8.2 P0 运行时闭环修复

1. 修复 style 反馈回调：
   - `plugins/style/plugin.json` 增加 `reply` 权限。
   - 新增 bus 级测试，验证 `fire_on_post_reply()` 能调用 `StylePlugin.on_post_reply()`。
   - 验证 `style_feedback` 能写入 `target_type='reply'`、`source='weak_signal'`。

2. 激活 style 可用数据：
   - 在 `/admin/style` 审核通过一批高质量 expression。
   - 生成并启用至少一个 group profile。
   - 用 trace 验证 `动态风格档案` / `表达习惯参考` 在相关上下文中实际注入。

3. 修复 slang daily AI review 契约：
   - 对齐文档、测试和当前 `SlangPlugin` 实现。
   - 若 daily review 已被 backlog reviewer 替代，文档应明确废弃/迁移关系。
   - 若 daily review 仍是目标能力，应恢复入口并补测试。

4. **lexical 层完整性 + 决策可见性**（2026-05-18 第二轮审计扩展，原条款只覆盖 4a 一项）：

   4a. 补齐 learning normalizer 的 slang 写入路径：
   - 2026-05-19 基石补丁 P3 已 grep 确认：当前运行时没有调用 `LearningNormalizerStore.attach_candidate(domain='slang')`。
   - 在 slang pending/term 写入路径补 attach。
   - 写入 meta 时保留 `normalization_cluster_id`、`normalization_item_id`、source refs。

   4b. 写入路径碰撞防御（**`update_term` + `merge_terms`**，详见 [`slang-collision-thinker-audit-2026-05-18.md` 阶段 A](slang-collision-thinker-audit-2026-05-18.md)）：
   - `find_existing` 增加 `exclude_term_ids: set[str]` 参数支持自身排除。
   - `update_term` 在改 `term` / `aliases` 时调 `find_existing` 校验，命中任何 status（含 muted/expired）一律 raise；首版本规划的 muted/expired 复用豁免已取消，避免与碰撞清理验收冲突。
   - `_maybe_create_drift_review` 的 `alias_candidate` 自动合并路径包 try/except，碰撞时降级为创建 drift_review 记录。
   - `merge_terms` 加两道闸：(1) target 与 source 必须同 `(scope, group_id)`，否则 raise；(2) 写 aliases_json 前同样调 `find_existing` 校验第三方碰撞。
   - 证据：[`store.py:2123 update_term`](../../services/slang/store.py#L2123) 当前无碰撞校验；[`store.py:2273 merge_terms`](../../services/slang/store.py#L2273) 当前无跨群闸；用临时库可复现 mirror 镜像写入。

   4c. 历史碰撞数据清理 + 工具口径修复：
   - `scripts/dev/slang_alias_collision_report.py` 当前 `--status approved` 是先 SQL 过滤后做 pairwise，只能看到 approved↔approved（1 对），漏掉 approved↔candidate / approved↔muted。修脚本：删 `--status` flag 或改为"至少一端符合"。
   - 跑全量报告→在 admin `/admin/slang` 用 `merge_terms` API 逐对人工合并；脚本输出已带 `suggested_target / suggested_source` 决策。
   - live DB 当前 72 对碰撞、24 对涉及 approved（2026-05-18 实测）。

   4d. **thinker 决策感知 lexical direct hit**（决策可见性）：
   - 当前 [`client.py:1709`](../../services/llm/client.py#L1709) thinker 决策点先于 [`client.py:1805`](../../services/llm/client.py#L1805) `fire_on_pre_prompt`，黑话 block 在后；thinker 不感知任何黑话上下文。
   - `LLMClient.__init__` 没有 `slang_store` 参数（[`client.py:960-985`](../../services/llm/client.py#L960)）；落地前需二选一：(C-1) ctor 显式注入；(C-2) 运行时 `bus.get_plugin("slang").store`。
   - thinker 调用前先做守护：`slang_store is None` 或 `group_profile.slang_enabled=False`（沿用 [`client.py:1277`](../../services/llm/client.py#L1277) 同样的判定）则跳过。
   - 否则算一次 `find_matching_terms(group_id, conversation_text, include_candidates=False)`，折成 ≤200 字摘要追加到 `system_text`。
   - **不**给 thinker 开 `slang_lookup` 工具——thinker 仅决策"是否说话"，工具调用属于回复模型职责。

### 8.3 P1 架构方案修复

> **2026-05-18 复审更新**：本节首版本的"先做 PromptBudgetManager，不要直接把 slang/style 塞进 ContextService"和 § 6 Q1 拍板（"ContextService 接管 slang/style 全部检索"）方向相反，造成路线图内部矛盾。本次澄清两者**不是互斥的是/否**，而是**有序的迁移序列**。第 1 项重写为 3 步迁移序列。

1. **ContextService 接管 slang/style 检索 + PromptBudgetManager 联合迁移**（迁移序列，**不可一步到位**）：

   - **步骤 1 — provider 接口 + 双跑 trace**：在 `services/context/sources.py` 增加 `SlangProvider` / `StyleProvider`，把 slang/style 的检索能力封装为 ContextService 数据源；**保留** SlangPlugin / StylePlugin 的现有 `on_pre_prompt` 注入路径**不动**。本步骤产出双路径并存：(a) ContextService 通过 provider 检索后**只 trace、不进 prompt**；(b) 旧路径保持当前行为。BlockTraceBus 同期落地（见步骤 2 前置依赖）。
   - **步骤 2 — PromptBudgetManager 接管裁剪决策**：BlockTraceBus 落地后，BudgetManager 把所有 PromptBlock 候选（来自插件 on_pre_prompt + ContextService provider）统一收集，按优先级 / 字符预算 / 插入位置裁剪。**此时仍允许双路径**——目的是让 trace 数据证明 ContextService 检索结果与插件直接注入结果对齐。
   - **步骤 3 — 关闭旧插件 on_pre_prompt 注入**：trace 数据证明对齐后，SlangPlugin / StylePlugin 的 `on_pre_prompt` hook 改为只产出 PromptBlock 候选交给 ContextService（不再独立注入）。BudgetManager 成为唯一注入入口。

   迁移期间任何步骤可独立回滚：步骤 1 失败回到无 provider 状态；步骤 2 失败回到插件直接注入 + 无统一裁剪；步骤 3 失败回到双路径并存。

   各 PromptBlock 候选的字段、trace 字段定义见 § 6 Q7 决议。

2. 再做 `MemoryConsolidator` dry-run：
   - 输入统一来自 `ConversationArchive`。
   - 输出 typed candidates：fact/slang/style/episode/graph_relation。
   - 默认只展示建议，不自动写生产库。
   - 每条建议带 scope、privacy、source refs、confidence、回滚方式。

3. 最后引入 `Episodic Reflection`：
   - 从管理员反馈、用户纠正、失败回复、成功接话中生成短反思。
   - 反思只作为动态经验提示，不修改 core soul。
   - 反思必须有 decay、last_used_at 和禁用/回滚入口。

### 8.4 P1 隐私与作用域修复

> **2026-05-18 复审更新**：本节首版本只列原则，未给迁移计划。审计指出当前 4 张表（slang_terms / style_expressions / learning_normalizer_clusters / knowledge_graph_*）各有自己的 scope/group_id 字段，但**完全没有** `cross_group_visibility_enabled / enabled_by / enabled_at / enabled_for_groups[]`，加这些字段是迁移工程不是小修。本节扩为完整迁移计划，对应 § 5 Phase A2。

#### 8.4.1 原则（保留首版）

- global memory 不应继承 slang/style 的宽松默认值。
- 跨群共享必须显式启用，并记录启用人、启用时间和影响范围。
- evidence 引用应只保存必要片段，敏感原文应支持脱敏或只存 ref。
- 图谱写入必须包含 scope/scope_id，检索时按群和用户上下文过滤。

#### 8.4.2 跨层 schema 统一（迁移计划）

四张表都需要新增以下统一字段（命名 / 类型 / 默认值一致）：

| 字段 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `cross_group_visibility_enabled` | INTEGER (0/1) | 0 | 是否允许跨群可见。**默认 0**，不可被插件 / 迁移 / 自动促进路径覆盖。 |
| `enabled_by` | TEXT | NULL | admin user_id（启用人）；`enabled=0` 时为 NULL |
| `enabled_at` | TEXT (ISO8601) | NULL | 启用时间戳；`enabled=0` 时为 NULL |
| `enabled_for_groups` | TEXT (JSON list) | `'[]'` | 白名单群 id 列表；空列表代表"全 global 可见"或仅原始群可见（取决于 enabled 状态） |
| `enabled_reason` | TEXT | `''` | admin 启用时填写的原因（追溯用） |

**已存在字段**保持不变（`scope`、`group_id` 在各表已有，无需改动）。新字段统一通过 `apply_cross_group_filter(query, viewer_group_id)` helper 在所有读取路径上生效。

#### 8.4.3 迁移顺序（Phase A2 实施）

按依赖反向迁移，每步独立可回滚：

1. **Step 1 — schema review**：写 ADR 固定字段命名 / 类型 / 索引。审核后冻结 schema。
2. **Step 2 — alter table**：四张表按依赖反向顺序 alter（先 `learning_normalizer_clusters` → `slang_terms` / `style_expressions` → `knowledge_graph_*`）。每张表 alter 含字段添加 + 默认值 + 索引（`(scope, cross_group_visibility_enabled)` 复合索引）。
3. **Step 3 — 历史数据回填**：单条 SQL `UPDATE ... SET cross_group_visibility_enabled = 0` （已有默认，但显式回填便于审计）。`enabled_*` 字段保持 NULL。
4. **Step 4 — 读取兼容期（双跑期 ~1 周）**：所有现有 query **保持不变**；新代码路径通过 `apply_cross_group_filter()` 写出过滤；监控两条路径的查询结果差异，预期为 0（因为所有历史数据 `enabled=0`，过滤后等价于现状）。
5. **Step 5 — 启用 enforcement**：双跑期 trace 显示无差异后，把 `apply_cross_group_filter` 接入到所有 SlangStore / StyleStore / NormalizerStore / GraphStore 的 query 路径上；旧路径下线。
6. **Step 6 — admin UI**：跨群启用审计页面（按 `enabled_at` 时间线列表 + 撤销启用 + 模拟以群 X 视角调试）。**Step 6 可与 Step 5 并行**。

#### 8.4.4 写入路径强约束（写入侧）

迁移完成后，写入路径必须满足以下约束：

- **不可由插件默认值覆盖**：插件 init 时不能写 `cross_group_visibility_enabled=1`。
- **不可由迁移脚本覆盖**：迁移脚本不能批量启用。
- **不可由自动促进路径（如 auto_promote_global）覆盖**：自动促进只改 `scope`，不动 `cross_group_visibility_enabled`。
- **唯一启用入口**：admin UI 的"启用跨群可见"按钮，强制要求 `enabled_by` + `enabled_reason`，写入 revision。

#### 8.4.5 隐私 evidence 保护（独立工作面）

evidence 引用脱敏不在本次迁移范围（与 cross_group 字段独立）。原条款保留作为后续工作：

- evidence 引用应只保存必要片段，敏感原文应支持脱敏或只存 ref。
- 这块依赖 ConversationArchive 的引用层语义；当 ConversationArchive 完整后再单独治理。

### 8.5 复审清单

> **2026-05-18 复审扩展**：首版本 5 条（A 组）只覆盖"事实性自校"——会防住"假宣传 / 缺来源 / 漏验收"这类错误。复审两轮暴露了**结构与一致性**层面的盲区（路线图内部矛盾、Phase 过载、状态机歧义、隐私迁移工程量误判、open_risk 静默），需要追加 B 组 6 条。第三轮基于代码与 2026 年论文实证补 B7（跟踪行业局限），共 7 条。

#### A 组 — 事实性自校（首版保留）

- [ ] **A1**：报告中的每个"已实现/已接入"都有代码路径或 DB 证据。
- [ ] **A2**：每个外部项目论断都有来源链接。
- [ ] **A3**：每个路线图阶段都有验收标准。
- [ ] **A4**：P0 修复完成前，不把 Omubot 宣称为"真人式多层记忆"。
- [ ] **A5**：P1 之前不启用任何自动跨群/global 写入。

#### B 组 — 结构与一致性自校（2026-05-18 复审追加，含第三轮 B7）

- [ ] **B1 — 决议自洽**：§ 6 / § 7 任意决议落地之前，搜本报告全文（不限 § 5/§ 7）确认**无方向相反**的表述；如有，先消除矛盾再实施。基线案例：§ 6 Q1（"ContextService 接管检索"）vs § 8.3 首版本（"不要塞进 ContextService"）—— 已通过 § 8.3 三步迁移序列消除（[`§ 8.3`](#83-p1-架构方案修复)）。
- [ ] **B2 — Phase 工作量上限**：单个 sub-phase 工作量 ≤ 5 工作日，且**不允许同时**包含三类工作中的两类以上（schema 迁移 / 新建 admin UI / 业务代码改写）。基线案例：原 Phase A 把 6 个工作面塞进 P0 → 拆为 A0/A1/A2/A3，每个 ≤ 5 天且类别单一（[`§ 5 Phase A`](#5-阶段路线图)）。
- [ ] **B3 — 状态字段必有完整状态表**：报告中任何"自动启用 / 自动晋升 / 默认进 prompt"等表述都必须配有完整状态表（含进入条件 / 出口条件 / 是否进 prompt / 强约束）；不允许仅用文字粗描。基线案例：episode "自动启用" → § 6 Q2 5 态状态机（[`§ 6 Q2 状态机`](#q2-episode-5-态状态机拍板细化)）。
- [ ] **B4 — 跨层 schema 修改硬要求**：任何动 ≥ 2 张表的 schema 修改必须有 (1) 字段命名 / 类型 / 默认值统一表、(2) ≥ 6 步迁移计划（含 schema review / alter / 回填 / 双跑兼容期 / enforcement / admin UI）、(3) 每步独立可回滚标识。基线案例：跨群隐私字段 → § 8.4.2 / 8.4.3 / 8.4.4。
- [ ] **B5 — 验收表三态区分**：每个 phase 的验收清单必须用 `green`（落地后达标）/ `pending`（依赖其它 phase）/ `open_risk`（决议悬而未决，必须显式列出）三态区分；不允许只列预期 green 项而隐藏 open_risk。基线案例：Phase A0 验收表中 `max_indirect_inject_terms` 默认值原为 `open_risk`，2026-05-19 P2 落地后改为 `green`（[`§ 5 Phase A0`](#phase-a0--快速-p0-修复1-工作日)）。
- [ ] **B6 — 决议追溯链**：每条 sub-phase 工作面必须能追溯到具体决议条目（§ 6 Q? / § 7 ?? / § 8.x.y）；不允许"凭空冒出来的工作面"。基线：Phase A0/A1/A2/A3 每项标注了对应决议或审计来源。
- [ ] **B7 — 跟踪行业局限**：每次复审应核对当前路线图是否在已知行业未解问题上踩雷；若是，在文档中显式标注"已知行业未解，不在本轮范围"，避免被误读为本项目自身设计缺陷。基线案例（2026-05-18）：[Experience Compression Spectrum (arXiv 2604.15877)](https://arxiv.org/abs/2604.15877) 综述 20+ 系统后指出"every system operates at a fixed, predetermined compression level — none supports adaptive cross-level compression"，称为 "missing diagonal"。Omubot Phase A-E 是固定 lexical / style / episode 分层，**该问题全行业未解**，不在 Phase A-E 范围；**Phase F（§ 5 + § 7.7）已显式登记为远期前沿探索**，对应 [Position: Episodic Memory is the Missing Piece (2502.06975)](https://arxiv.org/abs/2502.06975) 立场论文中 episode → semantic abstraction 缺失件论断。未来 Phase F 之后可参考 [SYNAPSE (2601.02744)](https://arxiv.org/abs/2601.02744) 的 spreading activation 与 [Memento (2508.16153)](https://arxiv.org/abs/2508.16153) 的 memory-augmented MDP 等 2026 前沿方向继续探索。

#### Meta — 本清单可演进

- [ ] **M1**：本 § 8.5 清单本身可随复审追加；任何复审发现的新错误类别（不在 A 组或 B 组覆盖范围内）必须沉淀为新条款，不留口头记忆。

## 10. 关键接口契约草案

> 2026-05-19 基石补丁 P1：本节是 Phase B / Phase A.5 的对齐基线，不是最终 API。落地代码可以调整命名，但必须保留这里的输入/输出语义和决议追溯链。

### 10.1 ContextProvider

来源决议：§ 6 Q1、§ 7.3、§ 8.3。目标是让 slang/style/knowledge/graph/episode 都以 provider 形式向 ContextService 上报候选块；provider 不直接写 prompt。

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

PromptLayer = Literal["core", "stable", "dynamic", "tail", "tool_hint"]
PromptSource = Literal["slang", "style", "memory", "knowledge", "graph", "episode", "declarative_fact"]

@dataclass(frozen=True)
class QueryContext:
    request_id: str
    viewer_group_id: str
    user_id: str = ""
    conversation_text: str = ""
    message_ids: tuple[int, ...] = ()
    locale: str = "zh-CN"
    max_candidates: int = 20

@dataclass(frozen=True)
class PromptBlockCandidate:
    candidate_id: str
    source: PromptSource
    provider: str
    layer: PromptLayer
    text: str
    priority: int
    scope: str
    group_id: str
    hit_reason: str
    evidence_refs: tuple[str, ...] = ()
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

class ContextProvider(Protocol):
    name: str

    async def fetch(
        self,
        viewer_group_id: str,
        query_context: QueryContext,
    ) -> list[PromptBlockCandidate]:
        """Return candidates only; do not mutate the prompt directly."""
```

### 10.2 BlockTraceBus

来源决议：§ 6 Q7、§ 7.3、§ 8.3。目标是每个进入 prompt 或被裁剪的候选都有可审计记录，支撑 BudgetManager、episode enable gate、Phase F rollback。

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

BudgetDecision = Literal["accepted", "trimmed", "rejected", "shadow_only"]

@dataclass(frozen=True)
class PromptBlockTrace:
    trace_id: str
    request_id: str
    task: str
    source: str
    provider: str
    candidate_id: str
    decision: BudgetDecision
    hit_reason: str
    evidence_refs: tuple[str, ...]
    token_estimate: int
    char_count: int
    position: str
    decay_state: str = ""
    budget_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

class BlockTraceBus(Protocol):
    async def record(self, trace: PromptBlockTrace) -> None:
        """Persist one prompt-block decision."""

    async def list_for_request(self, request_id: str) -> list[PromptBlockTrace]:
        """Inspect all prompt-block decisions for one request."""

    async def find_by_source_ref(
        self,
        *,
        source: str,
        source_id: str,
        limit: int = 100,
    ) -> list[PromptBlockTrace]:
        """Find affected prompt uses for rollback/debug flows."""
```

### 10.3 GraphWriter

来源决议：§ 6 Q3/Q4/Q5/Q6、§ 7.4、§ 7.5、§ 8.4。目标是 Phase A.5 把 slang/style/episode/fact 写入跨层 graph，同时统一跨群可见性过滤。

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

GraphNodeType = Literal["term", "style_expression", "episode", "fact", "user", "group", "document_chunk"]
GraphEdgeType = Literal[
    "term_used_in_context",
    "term_implies_emotion",
    "style_applies_to_situation",
    "episode_supports_profile",
    "doc_supports_fact",
    "user_corrected_bot_about",
]

@dataclass(frozen=True)
class GraphNodeDraft:
    node_type: GraphNodeType
    source_table: str
    source_id: str
    scope: str
    group_id: str
    label: str
    properties: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class GraphEdgeDraft:
    edge_type: GraphEdgeType
    from_node_id: str
    to_node_id: str
    scope: str
    group_id: str
    confidence: float
    evidence_refs: tuple[str, ...] = ()
    properties: dict[str, Any] = field(default_factory=dict)

class GraphWriter(Protocol):
    async def write_node(self, draft: GraphNodeDraft) -> str:
        """Create or update a graph node and return node_id."""

    async def write_edge(self, draft: GraphEdgeDraft) -> str:
        """Create or update a graph edge and return edge_id."""

    def apply_cross_group_filter(self, query: Any, viewer_group_id: str) -> Any:
        """Apply § 7.5 visibility rules to graph reads."""
```

### 10.4 追溯自检

| 契约 | 决议来源 | 首个落地阶段 | 验收 |
| --- | --- | --- | --- |
| `ContextProvider.fetch()` | § 6 Q1 / § 7.3 | Phase B step 1 | provider 只产候选，不直接注入 prompt |
| `PromptBlockCandidate` | § 6 Q7 / § 8.3 | Phase B step 1 | 每个候选有 source/provider/hit_reason/evidence_refs |
| `BlockTraceBus.record()` | § 6 Q7 / § 7.3 | Phase B step 1 | accepted/trimmed/rejected/shadow_only 全量可查 |
| `GraphWriter.write_node/write_edge()` | § 6 Q3-Q5 / § 7.4 | Phase A.5 | slang/style/episode/fact 可双写 graph |
| `apply_cross_group_filter()` | § 6 Q6 / § 7.5 / § 8.4 | Phase A2/A.5 | 默认跨群不可见，只能 admin 显式启用 |

## 11. 参考资料

- Generative Agents: Interactive Simulacra of Human Behavior — https://arxiv.org/abs/2304.03442
- MemGPT: Towards LLMs as Operating Systems — https://arxiv.org/abs/2310.08560
- Reflexion: Language Agents with Verbal Reinforcement Learning — https://arxiv.org/abs/2303.11366
- Position: Episodic Memory is the Missing Piece for Long-Term LLM Agents — https://arxiv.org/abs/2502.06975
- Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory — https://arxiv.org/abs/2504.19413
- Zep: A Temporal Knowledge Graph Architecture for Agent Memory — https://arxiv.org/abs/2501.13956
- LangMem Core Concepts — https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- Letta Memory Blocks — https://docs.letta.com/guides/core-concepts/memory/memory-blocks
- Mem0 Platform Overview — https://docs.mem0.ai/platform/overview
- Zep Understanding the Graph — https://help.getzep.com/v2/understanding-the-graph

### 2026 年实证调研补充（用于 B7 / 第三轮复审依据）

以下文献与代码于 2026-05-18 实际拉取并阅读（不是 ReadMe 转述），用于支撑 B7 "跟踪行业局限"条款：

- [Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers (2026.03 综述)](https://arxiv.org/abs/2603.07670)
- [Experience Compression Spectrum: Unifying Memory, Skills, and Rules in LLM Agents (2026.04，含 "missing diagonal" 论断)](https://arxiv.org/abs/2604.15877)
- [SYNAPSE: Episodic-Semantic Memory via Spreading Activation (2026.01)](https://arxiv.org/abs/2601.02744)
- [E-mem: Multi-agent Episodic Context Reconstruction (ICML 2026)](https://arxiv.org/abs/2601.21714)
- [AriGraph: KG World Models with Episodic Memory for LLM Agents](https://arxiv.org/abs/2407.04363)
- [Memento: Fine-tuning LLM Agents without Fine-tuning LLMs (memory-augmented MDP)](https://arxiv.org/abs/2508.16153)
- [Cognitive Memory in Large Language Models (sensory/short/long 三类记忆综述)](https://arxiv.org/abs/2504.02441)
- [kimjammer/Neuro 源码（chromadb-only + reflection 实证）](https://github.com/kimjammer/Neuro)
- [Open-LLM-VTuber 源码（v1.0 README 自承长期记忆暂时移除；agents/ 目录显示 mem0_llm.py 0 字节、Letta/Hume 外包模式）](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber)
- SillyTavern World Info — https://docs.sillytavern.app/usage/core-concepts/worldinfo/
