# Omubot 拟人 Part 6.5 — 跨代进化补充调研（Part 1 / 2-3 / 5 framing 审计 + 前沿补缺）

> 状态：2026-05-26 立项，**research-only，不进 execution 主线**。
>
> 触发：用户在 Part 6 v2.3 收口后回望前序 3 部，质疑「Part 1 / 2-3 / 5 是否只把同代产品（MaiBot）当目标，而非具备跨代进化的取景」。
>
> 立场：Part 4 v2-修订版 §0「自审纪律」与 §1.3「真代际差 vs 形式代际差」、Part 3.5 v3 §0「同代基线判定 2 条」已确立判定标尺；本版用**同一把尺**重新审视 Part 1 / 2-3 / 5，并补缺其 framing 缺位的跨代前沿支撑。
>
> 范围声明：本文**不修改** Part 1 / 2-3 / 5 的设计章节；只产出 ① framing 诊断 ② 跨代前沿索引 ③ 真/形式代际差判定 ④ 落地建议候选。任何回填动作（改写 Part 5 §0 触发段、Part 2-3 §3.4 sticker 借鉴段等）需用户单独点头才进 execution。

---

## 0. 自审纪律（继承 Part 4 v2-修订版 §0）

为避免再陷入"为创新而创新"或"为反 MaiBot 而反 MaiBot"，本版重申 4 条纪律：

1. **每项 framing 诊断必须落到具体行号引用**——不引用、不立论。
2. **跨代前沿论文入选必须满足 2 条**：① 在 Omubot 现有架构（kernel / services / RuntimeStateBus / RWS）有对应可挂载位 ② 解决 Part 1 / 2-3 / 5 已识别的具体痛点。论文 keyword 形式（HRL / SSM / contrastive）不构成入选理由。
3. **真代际差 vs 形式代际差判定按机制层挂载位**——不按数据来源、不按 metric 选择、不按论文 keyword 形式。
4. **本版不列 P6.x 派单**——所有"建议落地"项以 candidate 形式列出，由用户视严重度单独点头后进入对应 Part 的 execution 文档。

---

## 1. Part 1 / 2-3 / 5 framing 重新定性

### 1.1 判定标尺（继承 Part 4 v2-修订版 §1.3 + Part 3.5 v3 §0）

| 标记 | 同代框架（MaiBot 当目标） | 跨代框架（MaiBot 当下限） |
|---|---|---|
| 立论锚点 | "MaiBot 体感更好 / 我们要追平 MaiBot" | "MaiBot 缺 X 挂载位，整体架构做不到 Y" |
| 参数来源 | 直接 lift MaiBot 数值（split_strength=0.2/0.6/0.7、emoji_chance=0.6） | 引用前沿论文 / 结构化分布 / Omubot 已有数据 |
| 反例 | "MaiBot 借鉴：sticker library 加 emotion tag" | "MaiBot 自己都关掉了 → 这条路有问题" |
| 论证形态 | 现象差异表（MaiBot 体感 vs Omubot 现状） | 挂载位差异表（v2.1 信号源 → v2.x 决策点） |
| 跨代证据 | 无 / 仅 MaiBot 单点对比 | arXiv ID + 论文章节锚点 + Omubot 适配论证 |

### 1.2 Part 1 — `omubot-humanization-part1-language-feel.md` framing 诊断

**结论：no-comparison-framing**（既未把 MaiBot 当目标，也未把 MaiBot 当下限——大部分立论是从 Omubot 自身代码 + 前沿论文出发的）。

**支撑证据**：

- [omubot-humanization-part1-language-feel.md:147](./omubot-humanization-part1-language-feel.md#L147) "MaiBot 21 个月 5 次回退是反例"——把 MaiBot 当 anti-pattern dead-code witness，不是目标
- [omubot-humanization-part1-language-feel.md:605](./omubot-humanization-part1-language-feel.md#L605) "该方案已被 Part 2/3 §3.4 + Part 4 §3.2 共同推翻"——立论锚点是同 Omubot 内部的横向引用，不是 MaiBot 体感对照
- 附录 A.3 列了 8 篇角色扮演 LLM 论文（RoleLLM / CharacterEval / ChatHaruhi / Character-LLM / Chen 2024 综述 / PERSOMA / PCL / XiaoIce），但**正文消化为 0**——架了书架但没读

**真正缺位的跨代支撑**（4 个 cluster）：

| Cluster | Part 1 缺位现状 | 跨代前沿候选 |
|---|---|---|
| C1 — decoding-time persona-consistency | Part 1 design 全在 prompt + post-hoc filter 层，没有 decoding-time persona 注入 | SBS（arXiv 2410.10937 EMNLP 2024）/ APD（arXiv 2401.16723）/ DeCoRe（NAACL 2025）/ NeurIPS 2025 persona consistency framework |
| C2 — register classifier embedding | Part 1 register 5 档全靠 LLM prompt 自我指认，无独立 classifier head | Steering LLMs with Register Analysis（arXiv 2505.00679）/ Biber-style stylistic variation 框架 |
| C3 — sampler & mode collapse | Part 1 sampler 默认 top-p / temperature 不变，无 min-p / locally typical / annotation-based mitigation | min-p sampling（ICLR 2025 oral）/ Locally Typical Sampling（TACL 2023）/ Local and Global Decoding（EMNLP 2024 findings）/ Annotations Mitigate Mode Collapse（ICLR 2025） |
| C4 — persona drift detection | Part 1 没有"是否漂移了"的运行时指标，只有 register=playful/calm/sober 的离散标签 | PersonaGym（EMNLP 2025）/ Amadeus persona-aware contrastive learning / CoSER（arXiv 2502.09082）/ MAUVE Scores（JMLR） |

### 1.3 Part 2-3 — `omubot-humanization-part2-3-research.md` framing 诊断

**结论：mixed**（v2 新章节实质跨代——通过 RuntimeStateBus 挂载位论证；v1 老章节同代——参数 lift + 现象表）。

**跨代证据**（v2 新章节）：

- [omubot-humanization-part2-3-research.md:864-942](./omubot-humanization-part2-3-research.md#L864-L942) RuntimeStateBus slot 设计：`MOOD_CURRENT_SLOT` / `AFFECTION_STAGE_SLOT` 等单向写入槽位
- [omubot-humanization-part2-3-research.md:857-858](./omubot-humanization-part2-3-research.md#L857-L858) "所有渗透点都通过 RuntimeStateBus slot 单向流入决策"——挂载位论证形态
- [omubot-humanization-part2-3-research.md:421](./omubot-humanization-part2-3-research.md#L421) "MaiBot 没有任何 mood 数值 / affection 等级 / familiarity 计数的运行时通道；Omubot v2 想做必须自研，不能借鉴 MaiBot"——明示 floor / 必须自研

**同代证据**（残留）：

- [omubot-humanization-part2-3-research.md:440](./omubot-humanization-part2-3-research.md#L440) "v2 借鉴：sticker library 加 emotion tag"（紧接 MaiBot Levenshtein top-10 + random.choice 描述——是直接 borrow MaiBot 的算法形式）
- [omubot-humanization-part2-3-research.md:723](./omubot-humanization-part2-3-research.md#L723) "硬上限 8 段（与 MaiBot 模板一致）"——参数 lift
- [omubot-humanization-part2-3-research.md:833](./omubot-humanization-part2-3-research.md#L833) relation_info_block "MaiBot 自己都关掉了，证明这条路有问题"——MaiBot 当 dead-code witness（这条是健康的 floor-style）

**真正缺位的跨代支撑**（4 个 cluster）：

| Cluster | Part 2-3 缺位现状 | 跨代前沿候选 |
|---|---|---|
| C5 — Dialog Act schema | Part 2-3 没有结构化 DA 体系；speak/silent 是 binary，"如何说"没标签 | ISO 24617-2 / DAMSL / SWBD-MIDAS / MIDAS dialog act scheme（arXiv 1908.10023）/ Dual Process Masking DA recognition（EMNLP 2024 findings） |
| C6 — state-space mood backbone | mood RuntimeStateBus 只是 enum + 衰减函数；无 SSM / Mamba 类时序 backbone | DA-Mamba selective SSM dialogue engagement（arXiv 2509.17711）/ S4 / Mamba |
| C7 — episodic + working memory two-tier | EpisodeStore 是单层 SQL；无 in-context working memory 与 episodic store 的双层路由 | MemGPT（arXiv 2310.08560）/ Letta two-tier memory / UniMS-RAG（arXiv 2401.13256）|
| C8 — graph-attention addressee | 多人对话 addressee 推断仍走启发式 + @ tag；无 graph-induced attention | MADNet（EMNLP 2023 graph multi-party addressee）/ GIFT（ACL 2023 graph-induced fine-tuning） |
| C9 — hierarchical planner | 调度层 RWS 是单层加权；无显式 high-level policy / low-level action 拆分 | LDPP（AAAI 2025 hierarchical latent policy）/ Skeleton-of-Thought（ICLR 2024） |
| C10 — ToM-style audience model | 对方推理仅靠 episodic recall + persona 字段；无显式 belief / desire 建模 | EnigmaToM（ACL 2025 findings）/ Infusing ToM（arXiv 2509.22887） |

### 1.4 Part 5 — `omubot-humanization-part5-segmentation.md` framing 诊断

**结论：predominantly same-gen**（严重度最高的一部）。

**同代证据**（高密度）：

- [omubot-humanization-part5-segmentation.md:5](./omubot-humanization-part5-segmentation.md#L5) §0 触发段："用户观摩 MaiBot 的群内回复时观察到「自然不打断」的体感……Omubot 现状会把回复硬拆到 ≤ 20 字 / 段……体感反而比一条整句更不自然"——立项动机直接是 MaiBot 体感对照
- [omubot-humanization-part5-segmentation.md:23-30](./omubot-humanization-part5-segmentation.md#L23-L30) §1.1 "MaiBot 体感 / Omubot 现状 / 差异点" 6 行对照表——典型的现象差异表 framing
- [omubot-humanization-part5-segmentation.md:136](./omubot-humanization-part5-segmentation.md#L136) §2.3 "参考 MaiBot calculate_typing_time，改成 `delay = max(0.5, min(3.0, prev_seg_chars × 0.15))`"——直接 lift 公式形式
- [omubot-humanization-part5-segmentation.md:146-162](./omubot-humanization-part5-segmentation.md#L146-L162) §3.2 标题"算法（融合 MaiBot + Omubot 双方优点）" + 概率档 0.2/0.6/0.7 直接 lift 自 MaiBot utils.py
- [omubot-humanization-part5-segmentation.md:107-113](./omubot-humanization-part5-segmentation.md#L107-L113) §2.1 PACLIC 2008 + Houghton 2018 是仅有的 baseline-floor 学术锚点——但这两篇是 17 / 7 年前的工作，不是跨代前沿

**跨代证据**：几乎无。整篇没有 2024-2026 段切分 / EOT / turn-taking 的前沿论文引用。

**真正缺位的跨代支撑**（5 个 cluster）：

| Cluster | Part 5 缺位现状 | 跨代前沿候选 |
|---|---|---|
| C11 — neural utterance boundary | 切分仍走 separator + 概率合并启发式；无神经边界检测 | SpeculativeETD（arXiv 2503.23439）/ STS streaming separator-transducer（arXiv 2205.05199） |
| C12 — EOT prediction | 段间延迟仅基于上一段字数；无 end-of-turn 概率预测 | TurnGPT（ACL 2020 + 后续 HRI work）/ FastTurn（arXiv 2604.01897）/ Phoenix-VAD（arXiv 2509.20410） |
| C13 — keystroke dynamics | char_delay 是固定字数 × 系数；无个体打字节律建模 | TypeNet keystroke（arXiv 2406.15335）/ DEFT distance-based keystroke（arXiv 2310.04059） |
| C14 — CA TCU/TRP framework | "自然不打断"完全是体感词，无 conversation analysis 形式化模型 | Sacks/Schegloff/Jefferson 1974 / GRASS turn-taking annotation corpus（arXiv 2504.09980） |
| C15 — multi-party emit scheduling | 多人群内 emit 时机仍是 RWS 单点决策；无显式群体节奏调度 | Speak or Stay Silent context-aware turn-taking（arXiv 2603.11409）/ HUMA Router with timeliness factor（arXiv 2511.17315） |

---

## 2. 跨代前沿研究索引（按 cluster 组织）

### 2.1 Part 1 cluster — 语言风格层

#### C1 decoding-time persona-consistency

**SBS — Speech-By-Sequence persona-consistent decoding（arXiv 2410.10937，EMNLP 2024 main）**

- 核心：把 persona 描述放成 contrastive sequence，decoding 时让 logits 朝 persona-aligned 方向偏移
- Omubot 适配：可挂在 [services/llm/client.py](../../services/llm/client.py) `call_api` SSE 流前的 logit warpers 层（Anthropic API 不开放 logit 干预——降级方案：sampling 后 rerank top-k by persona-classifier score）
- 真痛点：register=playful 时偶尔输出 sober tone，persona drift 到现在只能后置 filter

**APD — Anchored Persona Distillation（arXiv 2401.16723）**

- 核心：把 strong persona LLM 蒸馏到弱 LLM，contrastive loss 拉开 persona vs anti-persona 距离
- Omubot 适配：Anthropic API 不开权重——降级为「prompt 工程层用 anchor-anti-anchor 对比 prompt」，性价比远低于原方法
- 落地候选优先级：低（结构性失配）

**DeCoRe — Decoding by Contrasting Retrievals（NAACL 2025）**

- 核心：用 retrieved persona snippets vs anti-persona snippets 做 contrastive decoding
- Omubot 适配：可与 [services/persona/runtime_selector.py](../../services/persona/runtime_selector.py) bundle 联动——bundle.persona vs bundle.anti_persona 双 prompt → contrastive rerank
- 落地候选优先级：中（需新建 anti-persona 数据通道）

**NeurIPS 2025 persona consistency framework**

- 核心：定义 persona drift 4 个度量维度（lexical / semantic / pragmatic / behavioral）+ 提供 detection benchmark
- Omubot 适配：4 个维度可作为 [services/persona/shadow.py](../../services/persona/shadow.py) shadow log divergent_axes 的扩展锚点
- 落地候选优先级：高（直接增强已有 shadow 比对路径）

#### C2 register classifier embedding

**Steering LLMs with Register Analysis（arXiv 2505.00679）**

- 核心：用 Biber-style register feature 训 lightweight classifier，runtime 推理时给 LLM hidden state 做 steering vector
- Omubot 适配：Omubot register 5 档（playful / casual / calm / sober / cold）正好可作为 classifier 标签空间；但 steering vector 需要 hidden state 访问——Anthropic API 不开放，降级为「register classifier 输出作为 system prompt 的 register hint 字段」
- 落地候选优先级：高（即便只用降级方案，也比当前 LLM 自我指认更可靠）

**Biber-style stylistic variation 框架（学术综述族）**

- 核心：register 拆为 6 个维度（involved-informational / narrative-non-narrative / explicit-situation-dependent 等）
- Omubot 适配：可作为 register 5 档的「子维度网格」——playful 不是单一档，而是 (involved=high, narrative=low, explicit=low) 这种向量
- 落地候选优先级：中（需重新设计 register schema，破坏 Part 1 已落地的 5 档）

#### C3 sampler & mode collapse

**min-p sampling（ICLR 2025 oral）**

- 核心：用 min-p 替代 top-p，在低 entropy 处更收敛、高 entropy 处更发散
- Omubot 适配：Anthropic API 暴露 top_p / temperature，**未暴露 min_p**——本机制不可直接落地
- 落地候选优先级：失配（API 限制）

**Locally Typical Sampling（TACL 2023）**

- 核心：按 token 的 local typicality 做截断，比 top-p 更接近人类文本分布
- Omubot 适配：同上，API 不暴露——降级仅保留作参考
- 落地候选优先级：失配

**Local and Global Decoding（EMNLP 2024 findings）**

- 核心：global decoding 收敛 / local decoding 发散，混合策略减少 mode collapse
- Omubot 适配：可在 prompt 层通过「先生成 N 个 candidate，rerank by diversity score」模拟，但成本 N×
- 落地候选优先级：低（成本不匹配 QQ 闲聊场景）

**Annotations Mitigate Mode Collapse（ICLR 2025）**

- 核心：在 SFT 阶段加 mode-collapse-aware annotation，inference 阶段不需改
- Omubot 适配：Anthropic API 不开 SFT——结构性失配
- 落地候选优先级：失配

#### C4 persona drift detection

**PersonaGym（EMNLP 2025）**

- 核心：定义 persona-conditioned task suite + 自动 drift detection metric
- Omubot 适配：metric 可作为 [services/persona/shadow.py](../../services/persona/shadow.py) divergent_axes 扩展；suite 不可直接搬（任务定义与 QQ 闲聊不匹配）
- 落地候选优先级：中

**Amadeus persona-aware contrastive learning**

- 核心：把 persona 描述拆为多 view（angle / lexicon / behavior），各 view 独立对比损失
- Omubot 适配：与 DeCoRe 类似，需要训练通道，Omubot 走 API 不可直接训
- 落地候选优先级：失配

**CoSER（arXiv 2502.09082）**

- 核心：character role-play benchmark，提供 persona consistency 自动评测
- Omubot 适配：可作为 admin 后台 persona 健康度看板的 evaluator
- 落地候选优先级：中（admin 看板增强）

**MAUVE Scores（JMLR）**

- 核心：用 quantized embedding distribution 比对生成文本与人类文本
- Omubot 适配：可作为 shadow log 的「整体语料 drift 指标」（vs 现有的 axis-level）
- 落地候选优先级：中（与 NeurIPS 2025 persona consistency framework 互补）

### 2.2 Part 2-3 cluster — 调度 / 记忆 / 多人对话层

#### C5 Dialog Act schema

**ISO 24617-2 / DAMSL / SWBD-MIDAS（学术 schema 族）**

- 核心：把 utterance 标注为 dialog act tag（statement / question / acknowledgment / hedge / ...）
- Omubot 适配：可作为 [services/scheduler.py](../../services/scheduler.py) RWS 信号的「上一条用户消息 DA tag」输入维度——即「用户问句 → bot 倾向回答；用户陈述 → bot 倾向回应或沉默」
- 落地候选优先级：中（需新建 DA classifier 通道）

**MIDAS dialog act scheme（arXiv 1908.10023）**

- 核心：23 类 multi-domain DA schema，专为 open-domain dialogue 设计
- Omubot 适配：相比 SWBD-MIDAS 更贴近闲聊；可作为本 cluster 首选 schema
- 落地候选优先级：中

**Dual Process Masking DA recognition（EMNLP 2024 findings）**

- 核心：双过程（intuitive + deliberative）DA 分类，对长上下文鲁棒
- Omubot 适配：分类器需训练，Anthropic API 不开 fine-tune——降级为「LLM in-context few-shot 分类」
- 落地候选优先级：低（降级后性价比低）

#### C6 state-space mood backbone

**DA-Mamba selective SSM dialogue engagement（arXiv 2509.17711）**

- 核心：用 selective SSM（Mamba 派系）替代 attention 做对话 engagement 时序建模，长上下文 O(N) 复杂度
- Omubot 适配：mood RuntimeStateBus 当前是 enum + 衰减函数；改成 Mamba backbone 需要训练通道，且 mood 数据量不足以撑训练
- 落地候选优先级：失配（数据量 + 训练通道双重不足）

**S4 / Mamba（基础 SSM 文献族）**

- 同上结论
- 落地候选优先级：失配

#### C7 episodic + working memory two-tier

**MemGPT（arXiv 2310.08560）**

- 核心：双层记忆（main context working memory + recall storage episodic），LLM 主动调 paginate / search 工具
- Omubot 适配：Omubot 已有 EpisodeStore（episodic）+ in-memory deque（working）但**没有让 LLM 主动调用记忆工具**——可在 [services/llm/client.py](../../services/llm/client.py) tool loop 加 `search_episode` / `paginate_context` 工具
- 落地候选优先级：高（与 Part 4 v2-修订版 §3.3 CER episodic 注入正交互补）

**Letta two-tier memory（MemGPT 商业化派系）**

- 核心：与 MemGPT 同源，优化了 archival memory schema 与 vector recall
- Omubot 适配：同 MemGPT
- 落地候选优先级：与 MemGPT 二选一

**UniMS-RAG（arXiv 2401.13256）**

- 核心：unified multi-source RAG，把 persona / dialogue history / external knowledge 统一 retrieval
- Omubot 适配：persona bundle + EpisodeStore + slang/style 模块的统一检索路径——可作为 [services/persona/runtime_selector.py](../../services/persona/runtime_selector.py) bundle 加载流程的扩展
- 落地候选优先级：中（与现有 bundle 加载路径有重构成本）

#### C8 graph-attention addressee

**MADNet（EMNLP 2023 graph multi-party addressee）**

- 核心：把多人对话建成 graph，graph attention 推断 addressee
- Omubot 适配：QQ 群消息已有 (sender, mentions, reply_to) 三元组——可作为 graph node + edge；推断结果输入 RWS 信号
- 落地候选优先级：中（需新建 graph 模块；但 ROI 取决于多人 addressee 误判频率，admin 需提供数据）

**GIFT（ACL 2023 graph-induced fine-tuning）**

- 核心：用 graph induced features 做 fine-tuning
- Omubot 适配：fine-tuning 不可直接落地（API 限制）
- 落地候选优先级：失配

#### C9 hierarchical planner

**LDPP（AAAI 2025 hierarchical latent policy）**

- 核心：high-level policy（when to act）+ low-level action（how to act）双层 latent policy
- Omubot 适配：Part 3.5 v3 RWS 是单层加权——可拆为「high-level: 是否说话（current RWS）」+「low-level: 怎么说（DA tag + register + segmentation）」
- 落地候选优先级：高（与 Part 3.5 v3 RWS 挂载位天然对齐）

**Skeleton-of-Thought（ICLR 2024）**

- 核心：先生成 skeleton 大纲再并行填充内容
- Omubot 适配：与 IM 闲聊场景结构化程度不匹配；闲聊大多 1-3 段，没有 skeleton 价值
- 落地候选优先级：失配

#### C10 ToM-style audience model

**EnigmaToM（ACL 2025 findings）**

- 核心：在 dialogue agent 内显式建模对方的 belief / desire / intention，提升 multi-party 决策
- Omubot 适配：当前 persona bundle 只建模 bot 自身；对方推理仅靠 episodic recall——可加 user belief slot（基于最近 N 条 user message + 关系档案推断）
- 落地候选优先级：中（运维负担需评估——需要给 user 建模型，admin 看板需新增维度）

**Infusing ToM（arXiv 2509.22887）**

- 核心：把 ToM 信号 infuse 到 prompt context，不改架构
- Omubot 适配：prompt 工程层落地——成本低，但 ToM 信号生成本身需要额外 LLM call，token 成本上升
- 落地候选优先级：中

### 2.3 Part 5 cluster — 段切分 / 节奏层

#### C11 neural utterance boundary

**SpeculativeETD（arXiv 2503.23439）**

- 核心：speculative decoding 风格的 utterance boundary 预测，用 small model 预测候选边界、large model 校验
- Omubot 适配：Omubot 走 Anthropic API 不能 speculative decoding——降级为「small classifier 预测边界，作为 natural_split.py 的概率合并先验」
- 落地候选优先级：中（降级后即「轻量神经分类器替代纯启发式 separator」）

**STS streaming separator-transducer（arXiv 2205.05199）**

- 核心：流式 ASR 中的句子边界检测——专为「边写边切」设计
- Omubot 适配：Omubot 是 SSE 流式生成，与 STS 流式范式天然对齐——可在 [services/llm/client.py](../../services/llm/client.py) SSE chunk 累积时实时跑边界预测
- 落地候选优先级：高（与 SSE 流的形态最匹配）

#### C12 EOT prediction

**TurnGPT（ACL 2020 + 后续 HRI work）**

- 核心：用 LM perplexity / next-turn probability 预测 end-of-turn
- Omubot 适配：可作为段间延迟的 dynamic baseline——上一段结尾的 EOT 概率高 → 段间延迟拉长（让用户有插话窗口）；EOT 概率低 → 段间紧凑
- 落地候选优先级：高（直接替代 Part 5 §2.3 的 `delay = max(0.5, min(3.0, prev_seg_chars × 0.15))` 同代公式）

**FastTurn（arXiv 2604.01897）**

- 核心：lightweight EOT 预测，<10ms latency
- Omubot 适配：与 TurnGPT 同向，但更轻量——优先选 FastTurn
- 落地候选优先级：高

**Phoenix-VAD（arXiv 2509.20410）**

- 核心：voice activity detection 的文本类比——用 token-level signal 预测「说话还是停顿」
- Omubot 适配：与 EOT 互补——VAD 预测「下一 token 是否进入静默」；可作为段内 micro-delay 的输入
- 落地候选优先级：中

#### C13 keystroke dynamics

**TypeNet keystroke（arXiv 2406.15335）**

- 核心：用 keystroke 时序建模个体打字节律
- Omubot 适配：Omubot 没有真实 keystroke 数据——只能用「字数 × 字符类型 × 系数」拟合；但若 admin 录一段「真人 IM 打字录像」标注 char-level latency，可拟合 humanizer.delay 的 char_delay 系数
- 落地候选优先级：低（数据采集成本高于收益——QQ 闲聊场景对个体打字节律敏感度低）

**DEFT distance-based keystroke（arXiv 2310.04059）**

- 核心：基于按键距离（QWERTY 键盘几何）的打字时序建模
- Omubot 适配：bot 不需模拟真实键盘几何——结构性失配
- 落地候选优先级：失配

#### C14 CA TCU/TRP framework

**Sacks/Schegloff/Jefferson 1974 — Conversation Analysis turn-taking framework**

- 核心：把话轮拆为 TCU（turn-construction unit）+ TRP（transition-relevance place），TRP 是合法的换轮点
- Omubot 适配：Part 5 §1.1「自然不打断」的体感词正好对应 TCU 完整性——TCU 未完整 = 不应被打断；可作为 segmentation 算法的形式化锚点（替代 Part 5 §3.2 的 MaiBot 概率档形式）
- 落地候选优先级：高（直接给 Part 5 §3.2 提供跨代论证替代 MaiBot 借鉴）

**GRASS turn-taking annotation corpus（arXiv 2504.09980）**

- 核心：开源 turn-taking 标注语料，含 TCU / TRP 边界标注
- Omubot 适配：可作为 §C14 形式化模型的训练 / 评测数据
- 落地候选优先级：中（数据集，非算法）

#### C15 multi-party emit scheduling

**Speak or Stay Silent context-aware turn-taking（arXiv 2603.11409）**

- 核心：120K 决策点 + I1/I2/S1/S2 4 类 taxonomy + Reasoning-with-Decision +7.2pp ablation
- Omubot 适配：Part 2-3 §2.1.1 已引用——本文不重复立论，仅强调它**也是 Part 5 §C15 的核心论文**（多人 emit 时机决策与段间延迟在群聊场景互相耦合）
- 落地候选优先级：高（已被 Part 2-3 引用，需在 Part 5 联动消化）

**HUMA Router with timeliness factor（arXiv 2511.17315）**

- 核心：Router 的 20 个细分策略中含 timeliness `T_s = min(1, k/N)`——「距上次发言越久、越想说话」
- Omubot 适配：Omubot 群聊场景的「冷场补救」可挂这个 factor；Part 5 §C12 EOT + §C15 timeliness 联动→既不打断别人也不冷场
- 落地候选优先级：高（可与 RWS 现有信号无缝叠加）

---

## 3. 真代际差 vs 形式代际差判定

继承 Part 4 v2-修订版 §1.3 标尺：**有没有同代框架想做都做不到的结构性条件，而 Omubot 整体架构恰好支撑得起**。

### 3.1 真代际差候选（保留）

| Cluster | 论文 | 真代际差挂载位论证 |
|---|---|---|
| C7 | MemGPT / Letta | Omubot 已有 EpisodeStore + in-memory deque + tool loop——3 件挂载位齐备；MaiBot 没有 tool loop，无法让 LLM 主动调用记忆工具 |
| C9 | LDPP | Omubot 有 RWS 单层加权层 + Part 4 v2-修订版 §3.1 跨层联动；MaiBot 调度是 if-else 散落，没有 high-level / low-level 拆分挂载位 |
| C11 | STS streaming separator-transducer | Omubot SSE 流式生成天然对齐流式 ASR 范式；MaiBot 完整生成后才走 split_into_sentences_w_remove_punctuation，结构上不可能上 STS |
| C12 | TurnGPT / FastTurn | Omubot SSE 累积阶段可实时跑 EOT 预测；MaiBot 是「先 LLM 完整生成 → 后段切」结构，EOT 预测无挂载位 |
| C14 | CA TCU/TRP framework | Omubot Part 5 重写 segmentation 时可显式建模 TCU 单元；MaiBot 用概率档 + separator 是纯启发式，没有 TCU 概念 |
| C15 | Speak or Stay Silent + HUMA Router | Omubot RWS 是统一打分层，可直接加 timeliness factor；MaiBot 没有 RWS，timeliness 无挂载位 |

### 3.2 形式代际差候选（砍掉或降级）

| Cluster | 论文 | 形式代际差砍除理由 |
|---|---|---|
| C1 | SBS / APD | Anthropic API 不开 logit warp / fine-tune——「contrastive decoding」是论文 keyword 形式；落地后只能降级到 prompt 层，性价比低 |
| C3 | min-p / Locally Typical / Annotations Mitigate | API 不暴露 min_p，sampling 层不可控——结构性失配 |
| C6 | DA-Mamba / S4 / Mamba | mood 数据量不足训练 SSM backbone；mood enum + 衰减函数已经够用 |
| C8 | GIFT graph-induced fine-tuning | fine-tuning 不可直接落地——结构性失配 |
| C9 | Skeleton-of-Thought | QQ 闲聊大多 1-3 段，无 skeleton 价值——场景失配 |
| C13 | DEFT keystroke distance | bot 不需模拟键盘几何——结构性失配 |

### 3.3 中间层（条件性落地，由 ROI 决定）

| Cluster | 论文 | 中间层判定 |
|---|---|---|
| C1 | DeCoRe / NeurIPS 2025 persona consistency framework | 可挂在 Part 1 shadow 层；落地价值取决于实际 persona drift 频率（admin 提供观察） |
| C2 | Steering LLMs with Register Analysis（降级版） | 5 档 register classifier 即便降级到「prompt hint」也比 LLM 自我指认可靠 |
| C4 | PersonaGym / CoSER / MAUVE | admin 看板 evaluator 增强；与 shadow log 互补 |
| C5 | MIDAS dialog act schema | 可作为 RWS 信号扩展；需新建 DA classifier 通道，成本中等 |
| C7 | UniMS-RAG | 与现有 bundle 加载路径有重构成本，但与 Part 4 v2-修订版 §3.1 RWS 跨层联动正交互补 |
| C8 | MADNet graph attention addressee | ROI 取决于多人 addressee 误判频率，admin 需提供数据 |
| C10 | EnigmaToM / Infusing ToM | 运维负担需评估——给 user 建模型，admin 看板需新增维度 |
| C11 | SpeculativeETD（降级版） | 轻量神经分类器替代 separator 启发式，与 §C14 TCU 形式化互补 |
| C12 | Phoenix-VAD | 与 EOT 互补；段内 micro-delay 输入 |
| C14 | GRASS corpus | 数据集，非算法——配合 §C14 主论文一起落地 |

---

## 4. 落地建议候选（按 Part / 优先级）

> 本节仅列 candidate，不进 execution。任何回填动作（改写 Part 5 §0 触发段、Part 2-3 §3.4 sticker 借鉴段、Part 1 sampler 章节等）需用户单独点头。

### 4.1 Part 1 回填候选

| 优先级 | 改动 | 跨代论文锚点 | 触点 |
|---|---|---|---|
| 高 | shadow log divergent_axes 扩展 4 个 persona drift 维度 | NeurIPS 2025 persona consistency framework | [services/persona/shadow.py](../../services/persona/shadow.py) |
| 高 | register 5 档加 lightweight classifier 输出（降级版作 prompt hint） | Steering LLMs with Register Analysis (arXiv 2505.00679) | Part 1 register pipeline |
| 中 | admin 后台加 persona 健康度 evaluator | PersonaGym / CoSER / MAUVE | admin/frontend 新页 |
| 失配 | sampler 层不动 | min-p / Locally Typical / SBS / APD（API 限制） | 不落地 |

### 4.2 Part 2-3 回填候选

| 优先级 | 改动 | 跨代论文锚点 | 触点 |
|---|---|---|---|
| 高 | tool loop 加 `search_episode` / `paginate_context` 工具，让 LLM 主动调记忆 | MemGPT / Letta | [services/llm/client.py](../../services/llm/client.py) tool registry |
| 高 | RWS 拆为 high-level（speak）+ low-level（DA + register + segmentation）双层 | LDPP (AAAI 2025) | Part 3.5 v3 RWS scaffolding |
| 中 | RWS 信号加「上一条用户消息 DA tag」输入维度 | MIDAS (arXiv 1908.10023) | [services/scheduler.py](../../services/scheduler.py) + 新建 DA classifier |
| 中 | bundle 加载路径升级为 unified RAG | UniMS-RAG (arXiv 2401.13256) | [services/persona/runtime_selector.py](../../services/persona/runtime_selector.py) |
| 中 | 多人 addressee 推断加 graph attention | MADNet (EMNLP 2023) | scheduler addressee resolver（条件性，需 admin 提供误判频率数据） |
| 中 | persona bundle 加 user belief slot | EnigmaToM (ACL 2025 findings) | persona bundle schema |
| 失配 | mood backbone 不改 SSM | DA-Mamba / Mamba（数据量 + 训练通道双重不足） | 不落地 |

### 4.3 Part 5 回填候选

| 优先级 | 改动 | 跨代论文锚点 | 触点 |
|---|---|---|---|
| 高 | §0 触发段 + §3.2 framing 改写——把 MaiBot 体感对照降级为 baseline-floor，主立论改用 CA TCU/TRP 形式化模型 + STS / EOT 跨代论文 | Sacks 1974 + STS (arXiv 2205.05199) + TurnGPT / FastTurn (arXiv 2604.01897) | Part 5 §0 / §3.2 |
| 高 | 段间延迟从「prev_seg_chars × 0.15」公式改为 EOT 概率驱动 | TurnGPT / FastTurn / Phoenix-VAD | natural_split.py 调用方 |
| 高 | SSE chunk 累积时跑 streaming boundary 预测，替代纯 separator 启发式 | STS (arXiv 2205.05199) | [services/llm/client.py](../../services/llm/client.py) SSE 流处理 |
| 高 | 群聊场景加 timeliness factor 进 RWS（既不打断又不冷场） | Speak or Stay Silent + HUMA Router | RWS 信号扩展 |
| 中 | 配合 GRASS corpus 标注 / 评测段切分质量 | GRASS (arXiv 2504.09980) | tests/ 评测套件 |
| 失配 | keystroke 不建模 | TypeNet / DEFT（数据采集成本高于收益） | 不落地 |

### 4.4 跨 Part 联动候选

| 联动点 | 论证 | 触点 |
|---|---|---|
| C7（MemGPT 工具） + Part 4 v2-修订版 §3.3（CER episodic 注入） | MemGPT 让 LLM 主动检索；CER 是被动按 situation 注入——组合后形成「主动 + 被动」双通道 | [services/persona/willingness.py](../../services/persona/willingness.py) + [services/llm/client.py](../../services/llm/client.py) |
| C12（EOT） + C15（timeliness） + Part 3.5 v3 RWS | EOT 决定「该不该让用户插话」；timeliness 决定「是否该补救冷场」；RWS 是统一打分层——三者天然组合 | RWS scaffolding |
| C9（LDPP 双层 policy） + Part 4 v2-修订版 §3.1（RWS 跨层联动） | LDPP 高低层拆分 + 跨层联动信号——结构上 100% 对齐 | RWS scaffolding |

---

## 5. 与 Part 1 / 2-3 / 5 的差异声明

本版**不修改** Part 1 / 2-3 / 5 任何设计章节；只产出诊断 + 索引 + 候选。

三种后续路径，由用户选择：

1. **仅留档**：Part 6.5 作 framing 自审记录，不回填——保留同代立论原貌，承认 framing 风格选择
2. **针对性回填**：选其中 1-2 个高优先级候选（如 Part 5 §0 触发段 framing 改写、Part 1 shadow drift 维度扩展）单独进 execution
3. **全面回填**：把所有"高"优先级候选纳入新 Part 6.6 execution——但需评估总改动量是否破坏已落地章节稳定性

**自审纪律重申**（与 Part 4 v2-修订版 §0 一致）：

- 选 ②③ 时，每项改动必须满足：在 Omubot 实际部署场景下解决用户能感知的问题
- 论文 keyword 形式（contrastive decoding / SSM / graph attention）不构成入选理由
- 真代际差在机制（挂载位），不在 metric / 数据来源 / 论文形式
- 中间层 cluster 的落地决策需 admin 提供观察数据（drift 频率、addressee 误判频率等）

---

## 6. 参考资料

### 6.1 Part 1 cluster 前沿（C1-C4）

| ID | 标题 | 出处 | 落地候选优先级 |
|---|---|---|---|
| SBS-2024 | Speech-By-Sequence persona-consistent decoding | EMNLP 2024 main / arXiv 2410.10937 | 失配（API 限制） |
| APD-2024 | Anchored Persona Distillation | arXiv 2401.16723 | 失配（API 限制） |
| DeCoRe-2025 | Decoding by Contrasting Retrievals | NAACL 2025 | 中 |
| NeurIPS-2025-PC | Persona consistency framework | NeurIPS 2025 | 高 |
| Register-Steer-2025 | Steering LLMs with Register Analysis | arXiv 2505.00679 | 高（降级版） |
| min-p-2025 | min-p sampling | ICLR 2025 oral | 失配（API 限制） |
| LTS-2023 | Locally Typical Sampling | TACL 2023 | 失配 |
| LGD-2024 | Local and Global Decoding | EMNLP 2024 findings | 低 |
| AMC-2025 | Annotations Mitigate Mode Collapse | ICLR 2025 | 失配 |
| PersonaGym-2025 | PersonaGym | EMNLP 2025 | 中 |
| Amadeus | Persona-aware contrastive learning | (会议待确认) | 失配 |
| CoSER-2025 | character role-play benchmark | arXiv 2502.09082 | 中 |
| MAUVE-JMLR | MAUVE Scores | JMLR | 中 |

### 6.2 Part 2-3 cluster 前沿（C5-C10）

| ID | 标题 | 出处 | 落地候选优先级 |
|---|---|---|---|
| MIDAS-2019 | MIDAS dialog act scheme | arXiv 1908.10023 | 中 |
| DPM-DA-2024 | Dual Process Masking DA recognition | EMNLP 2024 findings | 低 |
| ISO-24617-2 | ISO Dialog Act schema | ISO 标准 | 中（与 MIDAS 二选一） |
| DA-Mamba-2025 | DA-Mamba selective SSM dialogue engagement | arXiv 2509.17711 | 失配 |
| MemGPT-2023 | Two-tier memory + tool loop | arXiv 2310.08560 | 高 |
| Letta | MemGPT 商业化派系 | (商业产品) | 与 MemGPT 二选一 |
| UniMS-RAG-2024 | unified multi-source RAG | arXiv 2401.13256 | 中 |
| MADNet-2023 | graph multi-party addressee | EMNLP 2023 | 中 |
| GIFT-2023 | graph-induced fine-tuning | ACL 2023 | 失配 |
| LDPP-2025 | hierarchical latent policy | AAAI 2025 | 高 |
| SoT-2024 | Skeleton-of-Thought | ICLR 2024 | 失配 |
| EnigmaToM-2025 | dialogue ToM modeling | ACL 2025 findings | 中 |
| Infusing-ToM-2025 | Infusing ToM | arXiv 2509.22887 | 中 |

### 6.3 Part 5 cluster 前沿（C11-C15）

| ID | 标题 | 出处 | 落地候选优先级 |
|---|---|---|---|
| SpeculativeETD-2025 | Speculative early termination decoding | arXiv 2503.23439 | 中（降级版） |
| STS-2022 | streaming separator-transducer | arXiv 2205.05199 | 高 |
| TurnGPT-2020 | LM-based EOT prediction | ACL 2020 + 后续 HRI | 高 |
| FastTurn-2026 | lightweight EOT prediction | arXiv 2604.01897 | 高 |
| Phoenix-VAD-2025 | text-VAD micro-delay | arXiv 2509.20410 | 中 |
| TypeNet-2024 | keystroke biometric timing | arXiv 2406.15335 | 低 |
| DEFT-2023 | distance-based keystroke | arXiv 2310.04059 | 失配 |
| CA-1974 | Sacks/Schegloff/Jefferson turn-taking | Language 50:696-735 | 高 |
| GRASS-2025 | turn-taking annotation corpus | arXiv 2504.09980 | 中（数据集） |
| SoSS-2026 | Speak or Stay Silent | arXiv 2603.11409 | 高（已被 Part 2-3 引用） |
| HUMA-2025 | Router with timeliness factor | arXiv 2511.17315 | 高 |

### 6.4 Omubot 内部锚点

| 文件 | 角色 |
|---|---|
| [docs/tracking/omubot-humanization-part1-language-feel.md](./omubot-humanization-part1-language-feel.md) | Part 1 framing 诊断目标 |
| [docs/tracking/omubot-humanization-part2-3-research.md](./omubot-humanization-part2-3-research.md) | Part 2-3 framing 诊断目标 |
| [docs/tracking/omubot-humanization-part5-segmentation.md](./omubot-humanization-part5-segmentation.md) | Part 5 framing 诊断目标 |
| [docs/tracking/omubot-humanization-part4-memory-relationship.md](./omubot-humanization-part4-memory-relationship.md) | Part 4 v2-修订版（自审纪律 + 真/形式代际差判定模板） |
| [docs/tracking/omubot-humanization-part3.5-prob-scheduler-revision.md](./omubot-humanization-part3.5-prob-scheduler-revision.md) | Part 3.5 v3（同代基线判定 2 条 + RWS 挂载位模板） |

---

## 7. 状态收口

- 本版 **research-only**，不进 execution
- 不列 P6.x 派单
- 不动 Part 1 / 2-3 / 5 任何章节
- 不更新 maintenance-log.md（除非用户选 §5 路径 ②③ 触发实际改动）
- 用户决定后续路径前，本文档作 framing 自审锚点静置

---

## 8. 自审：Part 6.5 自身的「不先进」之处（基于代码而非 README）

> 触发：用户回望本文 §1-§7 后追问——「Part 6.5 是否也只在表面把 MaiBot 当下限，本质上对 Omubot 现有代码的诊断是否准确、跨代论文挂载是否真的对得上代码挂载位」。
>
> 方法：本节所有依据 grep / Read [services/](../../services/) / [kernel/](../../kernel/) 的实际代码，**不引用 README、不引用文档自述、不依赖 Part 1/2-3/5 的现状描述**。
>
> 结论：Part 6.5 §1-§4 在 4 条主线上判断不准——把 Omubot 已有的代码挂载位错认为「未建」，把已经在生产路径上跑的 LLM 分类器错认为「无 classifier」，把 RWS 的 11 项扩展槽位错认为「单层加权」。

### 8.1 §3.1 真代际差表的 6 条挂载位论证审计

| Cluster | Part 6.5 §3.1 论证 | 实际代码状态 | 判定 |
|---|---|---|---|
| C7 (MemGPT) | "Omubot 已有 EpisodeStore + in-memory deque + tool loop——3 件挂载位齐备" | ✅ tool loop 在 [services/llm/client.py:1381-1418](../../services/llm/client.py#L1381-L1418) `_build_tool_defs`；ToolRegistry 在 [services/tools/registry.py](../../services/tools/registry.py) 41 行；插件可注册 [kernel/router.py:626](../../kernel/router.py#L626) `ctx.tool_registry.register(tool)`；EpisodeStore 在 [services/episodic/store.py:262](../../services/episodic/store.py#L262)；**但 grep `services/tools/*.py` 全部 10 个 tool 文件——没有任何 episode 检索工具**。已有的 [memo_tools.py:65-90](../../services/tools/memo_tools.py#L65-L90) `CardLookupTool` 是 CardStore（语义记忆卡片）的检索工具，不是 episode（情景记忆）的检索工具——两个 store 是分离的 | **挂载位准确，论证不完整**：表中只说"挂载位齐备"，实际是「CardStore 已挂 LLM 工具，EpisodeStore 未挂 LLM 工具」。Part 4 v2-修订版 §3.3 CER episodic 注入是被动注入，不是 LLM 主动检索——MemGPT 的真"主动 +被动"分工应该写「为 EpisodeStore 新建 search_episode tool 与 CardLookupTool 并列」 |
| C9 (LDPP) | "Omubot 有 RWS 单层加权层" | ⚠️ [services/scheduler_rws/rws.py:67-80](../../services/scheduler_rws/rws.py#L67-L80) `compute_rws` 已有 11 项加权（bias / old_threshold / at / directed_followup / video_always / addressee / eot / info_gain / hawkes / skip_pressure / mood_residual / schedule_residual），weights 在 [services/scheduler_rws/weights.py](../../services/scheduler_rws/weights.py) 全部默认 0.0（除 at=6.0 / eot=1.0 / hawkes=1.3）；[services/scheduler_rws/bandit.py](../../services/scheduler_rws/bandit.py) 51 行 epsilon-greedy 自调 theta；[services/scheduler.py:497-511](../../services/scheduler.py#L497-L511) 三档 flag（rws_shadow / rws_primary / rws_bandit / rws_bandit_freeze）；[services/scheduler.py:513-560](../../services/scheduler.py#L513-L560) `_maybe_compute_rws` 把 mode + addressee_self + skip + hawkes_rho + eot_probability 5 类信号汇入 features | **论证不准确**：RWS 不是"单层加权"——它已经是「11 项加权 + bandit 自调 theta + 三档 flag 控开关」的复合层。LDPP 的真挂载位论证应该是「`compute_rws` 现在所有项都进同一个 sigmoid，需要拆为 high-level（mode + addressee_self → speak）+ low-level（eot + hawkes + skip → how-to-speak）双 sigmoid」，而不是泛泛的"单层 → 双层" |
| C11 (STS) | "Omubot SSE 流式生成天然对齐流式 ASR 范式" | ✅ [services/llm/client.py](../../services/llm/client.py) 是 raw aiohttp SSE（CLAUDE.md 已声明），无 SDK；[services/llm/segmentation.py:432-466](../../services/llm/segmentation.py#L432-L466) `natural_split` 已实现 + [services/llm/segmentation.py:887-941](../../services/llm/segmentation.py#L887-L941) `reply_segments` / `_natural_split_path` / `reply_segment_plan` 三层调用；[services/llm/segmentation.py:94](../../services/llm/segmentation.py#L94) `natural_split_enabled: bool = False` 默认关 | **判断准确，但落地路径需修正**：STS 不应替代 segmentation——`natural_split` 已经是后置切分；STS 真挂载位是「在 SSE chunk 累积阶段（client.py 的 SSE 解析回路里）实时跑边界预测，比 segment.py 提前」。Part 6.5 §4.3 把它写成「替代 separator 启发式」是错的——是新增前置分类器 |
| C12 (TurnGPT/FastTurn) | "Omubot SSE 累积阶段可实时跑 EOT 预测；MaiBot 没有 EOT 挂载位" | ⚠️ [services/scheduler_eot/classifier.py](../../services/scheduler_eot/classifier.py) 已有 154 行完整 EOT 路径——LLM-based 分类器（system prompt 在 [classifier.py:15-19](../../services/scheduler_eot/classifier.py#L15-L19)，输出 JSON `{"probability":0.0-1.0,"reason":"20字以内"}`）；[classifier.py:30-63](../../services/scheduler_eot/classifier.py#L30-L63) `EOTCache` 30s TTL + 30s min_interval；[scheduler.py:97-100](../../services/scheduler.py#L97-L100) 启动时 wire 进 scheduler；[rws.py:74](../../services/scheduler_rws/rws.py#L74) `weights.eot * (eot_probability - 0.5) * 2.0` 已喂入 RWS | **论证完全错误**：Omubot 已有 EOT 通道，且**已挂在 RWS 里跑了**——只是用 LLM 调用（成本高、延迟 1.2s）实现，不是 TurnGPT/FastTurn 的轻量神经模型。真跨代命题应该是「把现有的 LLM-based EOTClassifier 替换为 lightweight neural EOT model（per-call 成本从 ¥0.001 + 1.2s 降到 ¥0 + <10ms）」，而不是「补一个 EOT 挂载位」 |
| C14 (CA TCU/TRP) | "Omubot Part 5 重写 segmentation 时可显式建模 TCU 单元" | ⚠️ [services/llm/segmentation.py:265-289](../../services/llm/segmentation.py#L265-L289) `_natural_split_strength` 已实现 MaiBot 三档（< 12 → 0.2 / < 32 → 0.6 / 默认 0.7）；[segmentation.py:290-307](../../services/llm/segmentation.py#L290-L307) `_natural_boundary_indices` 已实现 separator 边界检测；[segmentation.py:353-372](../../services/llm/segmentation.py#L353-L372) `_natural_merge_segments` 已实现概率合并（`_rng_random > split_strength`）；register × 6 / mood × 5 系数已落地 [segmentation.py:22-46](../../services/llm/segmentation.py#L22-L46) `_NATURAL_REGISTER_FACTORS` / `_NATURAL_DELAY_REGISTER_FACTORS` / `_NATURAL_DELAY_MOOD_FACTORS` | **论证准确性争议**：Part 5 §3.2 抄自 MaiBot 的概率档**已经在 segmentation.py 里跑了**（natural_split_enabled flag-gated）。CA TCU/TRP 的"真挂载位"不是"重写时建模"——而是「在已有的 `_natural_boundary_indices` 之上加 TCU completeness 投票，决定边界是 TRP 还是 mid-TCU」。Part 6.5 §4.3「§0 / §3.2 framing 改写」是诊断对的，但落地路径写错了 |
| C15 (Speak or Stay Silent + HUMA) | "Omubot RWS 是统一打分层，可直接加 timeliness factor" | ✅ [scheduler_rws/weights.py:11-21](../../services/scheduler_rws/weights.py#L11-L21) RWSWeights 已有 11 项；timeliness 是新槽位，需在 weights.py 加 `timeliness: float = 0.0` + `compute_rws` terms dict 加一项；[services/scheduler_hawkes/cache.py:74-85](../../services/scheduler_hawkes/cache.py#L74-L85) `estimate_rho_from_times` 已计算 mean_gap / burst rate——HUMA timeliness `T_s = min(1, k/N)` 中的 k=最近静默时长在 [scheduler.py:567-581](../../services/scheduler.py#L567-L581) 已有现成的 `times = [self._timeline.get_turn_time(group_id, idx)...]` | **论证准确**：这是 §3.1 唯一一条挂载位与代码完全对齐的论证——但本文 §3.1 没说「HUMA timeliness 与 Hawkes ρ 的方向相反」（HUMA：静默越久 → 越想说；Hawkes：群越热闹 → 越想说），落地时要检查相关性是否冗余 |

### 8.2 §1.2-§1.4 framing 诊断的代码盲点

| 章节 | 诊断结论 | 代码盲点 | 修订 |
|---|---|---|---|
| §1.2 Part 1 framing | "no-comparison-framing"（健康）+ "Part 1 register 5 档全靠 LLM prompt 自我指认，无独立 classifier head" | ⚠️ [services/humanization/classifier.py:54-93](../../services/humanization/classifier.py#L54-L93) `RegisterClassifier` 是**独立的** LLM 分类器（system prompt 在 [classifier.py:19-33](../../services/humanization/classifier.py#L19-L33) 显式列 6 档：neutral / quiet / playful / affectionate / serious / distant，**比 Part 6.5 §1.2 描述的"5 档"多 1 档**），结构化输出 JSON `{"label":...,"confidence":...,"reason":...,"evidence":...}`，不是"LLM 自我指认"；[classifier.py:78-93](../../services/humanization/classifier.py#L78-L93) `classify_and_write` 写入 `REGISTER_LABEL_SLOT` RuntimeStateBus | **C2 cluster 论证修正**：Steering LLMs with Register Analysis 的真挂载位不是"建立 register classifier"——是「替换现有 LLM-based classifier 为 lightweight steering classifier，把 LLM 调用降级为 fallback」。这与 §C12 的修订（替换 LLM EOT 为轻量神经 EOT）是同模式 |
| §1.2 Part 1 framing | "Part 1 没有'是否漂移了'的运行时指标" | ⚠️ [services/persona/shadow.py:97-265](../../services/persona/shadow.py#L97-L265) `ShadowCompareEngine` 277 行已实现——v1 vs v2 prompt block 比对，[shadow.py:48-78](../../services/persona/shadow.py#L48-L78) `ShadowDiffReport` 含 `divergent_axes` / `notes` / `errors` / `has_divergence`；[shadow.py:199-225](../../services/persona/shadow.py#L199-L225) `_collect_divergent_axes` 调用 [parity_audit](../../services/persona/parity_audit.py) `compare_v1_vs_v2_dry_run` 生成 6 axis findings；写盘到 `storage/persona_shadow_diff.log` | **C4 cluster 论证修正**：NeurIPS 2025 persona consistency framework 的"4 维度"（lexical / semantic / pragmatic / behavioral）不是"扩展 divergent_axes"——现有 6 axis 是 v1 vs v2 **结构对照**（identity_personality / bot_self_id / admins / instruction / proactive / group_override），而 NeurIPS 4 维度是 v2-vs-v2 **运行时对照**（这一刻输出的 register / token / 行为 vs 历史平均）。这是两个正交维度，不是扩展 |
| §1.2 Part 1 framing | （未提及） | [services/humanization/mood_classifier.py:49-81](../../services/humanization/mood_classifier.py#L49-L81) `MoodClassifier` 是**纯启发式 FSM**（不调 LLM，[mood_classifier.py:104-113](../../services/humanization/mood_classifier.py#L104-L113) `_transition` 5 档转移基于 short_reply_ratio / tone_particle_rate / sticker_density / reply_delay_s 4 信号 + 阈值规则）；[mood_classifier.py:62-81](../../services/humanization/mood_classifier.py#L62-L81) `classify_and_write` 写 `MOOD_CURRENT_SLOT`，TTL 300s | **新发现**：Part 6.5 §1.2 完全没提及 mood——但 Part 1 里有 mood classifier 而且**它是纯启发式**（与 §C6 的 DA-Mamba SSM 形成代际对照）。Part 6.5 §3.2 把 DA-Mamba 标"失配"——理由是"mood 数据量不足"是错的，真理由是「FSM 5 档 + 4 信号 + 300s TTL 已经够 QQ 闲聊场景，SSM 增量不足以抵消运行时成本」 |
| §1.3 Part 2-3 framing | "mixed"（v2 新章节实质跨代）+ 未提及 humanization 模块 | ✅ [services/humanization/](../../services/humanization/) 8 个文件 699 行（contract.py 46 / classifier.py 150 / mood_classifier.py 156 / coupling.py 79 / scorer.py 182 / state.py 19 / __init__.py 67）；[contract.py:29-46](../../services/humanization/contract.py#L29-L46) `HUMANIZATION_CONTRACT` 显式声明 9 个 state slot（包括 [contract.py:9-17](../../services/humanization/contract.py#L9-L17) `REGISTER_LABEL_SLOT` / `STICKER_RECENT_USED_SLOT` / `AFFECTION_FAMILIARITY_SLOT` / `AFFECTION_STAGE_SLOT` / `MOOD_CURRENT_SLOT` / `THINKER_LAST_DECISION_SLOT` / `CLOCK_CURRENT_SLOT` / `LAST_METRICS_SLOT`），全部走 `RuntimeStateBus.set` 通道 | **修订**：Part 2-3 跨代真挂载位的论证不只是 "RuntimeStateBus slot 设计"——是「**ModuleContract 体系**的存在」。`StateSlotDefinition` 显式声明 schema / ttl / privacy（admin_only / group / per_session / per_turn / per_user），是 ToM-like Audience model（§C10）落地的真挂载位——用 `_slot("state.audience.<uid>.belief", ttl="per_user", privacy="admin_only")` 一行就能挂，MaiBot 没有这种 contract 抽象 |
| §1.4 Part 5 framing | "predominantly same-gen"（最严重） + "整篇 0 篇 2024-2026 段切分前沿引用" | ⚠️ Part 5 §3.2 描述的算法**已落地** [services/llm/segmentation.py](../../services/llm/segmentation.py) 963 行（[segmentation.py:432-466](../../services/llm/segmentation.py#L432-L466) `natural_split` + [segmentation.py:466](../../services/llm/segmentation.py#L466) `inter_segment_delay`），且**已集成到 client.py 的 reply path**（`reply_segments` + `_natural_split_path`），由 [segmentation.py:94](../../services/llm/segmentation.py#L94) `natural_split_enabled: bool = False` flag 控制 | **修订**：Part 5 不是"设计未落地的同代借鉴"，而是"已落地的同代借鉴"。Part 6.5 §4.3「Part 5 §0 / §3.2 framing 改写」实际指向的是「修改文档的同代立项叙述 + 替换 segmentation.py 中的 MaiBot 概率档为 TCU-driven boundary scoring」——是动**生产代码**，不是文档级 framing |

### 8.3 跨代解（基于以上 8.1-8.2 修正）

> 本节给出 7 条「真跨代解」，每条都满足三个条件：① 论文/项目挂载位与 grep 出来的实际代码挂载位**完全对齐** ② 解决 §8.1-§8.2 揭示的具体盲点 ③ 不重复 Part 4 v2-修订版 / Part 3.5 v3 已落地的内容。

#### 8.3.1 EpisodeStore 主动检索 tool（解决 §8.1 C7 修订）

**现状**：CardStore 已挂 [memo_tools.py:22-90](../../services/tools/memo_tools.py#L22-L90) `CardLookupTool`；EpisodeStore（[services/episodic/store.py:262](../../services/episodic/store.py#L262)）未挂任何 LLM 工具。Part 4 v2-修订版 §3.3 CER episodic 注入是被动注入（willingness 5-stage 决策时按 situation 检索注入 top-3）。

**跨代论文**：MemGPT (arXiv 2310.08560) §4 Tool-augmented memory access。

**Omubot 实施**：

- 新建 `services/tools/episode_tools.py` `EpisodeLookupTool`，复用 `CardLookupTool` 的 Tool 抽象（[services/tools/base.py](../../services/tools/base.py)）
- 工具签名：`lookup_episodes(situation: str, limit: int = 5, include_decayed: bool = False) -> str`——`include_decayed` 直接对接 Part 4 v2-修订版 §3.5 P4.11.2 的 `search_episodes` 旗标
- 注册路径：[kernel/router.py:626](../../kernel/router.py#L626) `ctx.tool_registry.register(tool)`，与 `CardLookupTool` 并列
- 与 CER 互补：CER 在 willingness 阶段被动注入；`EpisodeLookupTool` 在 LLM tool loop 主动调用——LLM 自决是否需要长程记忆

**真代际差**：MaiBot 没有 tool loop，不能让 LLM 主动调记忆；Omubot 有 tool loop + EpisodeStore + CardStore + RuntimeStateBus 四件挂载位齐备，可以让 LLM 在主回复中按需取记忆。

#### 8.3.2 RWS sigmoid 拆分为 high-level / low-level 两层（解决 §8.1 C9 修订）

**现状**：[services/scheduler_rws/rws.py:67-90](../../services/scheduler_rws/rws.py#L67-L90) `compute_rws` 把 11 项加权全部进同一个 sigmoid——`mode + addressee_self`（"该不该说"）和 `eot + hawkes + skip + mood + schedule`（"什么时候说"）混在一起，无法独立调权重。

**跨代论文**：LDPP (AAAI 2025) high-level policy + low-level action 双层 latent policy。

**Omubot 实施**：

- 改 [scheduler_rws/rws.py:53-90](../../services/scheduler_rws/rws.py#L53-L90) `compute_rws`：拆为 `compute_speak_score`（high-level：bias / mode / addressee_self / old_threshold）+ `compute_timing_score`（low-level：eot / hawkes / skip_pressure / mood_residual / schedule_residual / timeliness）
- 决策规则：`decision = speak_score >= speak_theta AND timing_score >= timing_theta`——双门槛
- `RWSExplanation` 加 `speak_terms / timing_terms / speak_score / timing_score` 字段，shadow log 可独立追溯
- bandit ([scheduler_rws/bandit.py:33-43](../../services/scheduler_rws/bandit.py#L33-L43)) 拆为两个独立的 theta，分别调

**真代际差**：MaiBot 调度是 if-else 散落（"该不该说"与"什么时候说"混在同一个 if 链），无法做双门槛；Omubot RWS 已有统一打分层 + bandit + shadow log 三件挂载位，可以做正交拆分。

#### 8.3.3 EOT 分类器从 LLM-based 降级为 lightweight neural（解决 §8.1 C12 修订）

**现状**：[services/scheduler_eot/classifier.py:66-84](../../services/scheduler_eot/classifier.py#L66-L84) `EOTClassifier.classify` 是 LLM 调用（timeout 1200ms），每群每 30s 至多调一次 ([classifier.py:30-63](../../services/scheduler_eot/classifier.py#L30-L63) `EOTCache`)；[scheduler_rws/rws.py:74](../../services/scheduler_rws/rws.py#L74) `weights.eot * (eot_probability - 0.5) * 2.0` 已喂入 RWS。

**跨代论文**：FastTurn (arXiv 2604.01897) lightweight EOT < 10ms latency；TurnGPT (ACL 2020) LM perplexity-based EOT。

**Omubot 实施**：

- 新建 `services/scheduler_eot/lightweight.py` `LightweightEOTClassifier`，与现有 LLM-based classifier 实现同接口（同 `classify(messages, *, group_id, api_call) -> EOTDecision`）
- 算法选择：fastText / lightweight transformer 计算最近 5 条消息的 turn-end probability——离线训练数据可用 [services/scheduler_eot/classifier.py:87-97](../../services/scheduler_eot/classifier.py#L87-L97) `build_eot_request` 历史调用结果（用 LLM 标签蒸馏）
- 切换路径：[services/scheduler.py:97-100](../../services/scheduler.py#L97-L100) `eot_classifier` 注入点改为读 `humanization_config.eot_backend in {"llm", "lightweight"}` flag，灰度切换
- 命中收益：30s min_interval 限制可去除——lightweight 每条新消息都能跑；[rws.py:74](../../services/scheduler_rws/rws.py#L74) eot_probability 不再需要 30s TTL 缓存

**真代际差**：MaiBot 没有 EOT 概念，更没有 EOT 信号挂载到调度层；Omubot EOT 信号已挂入 RWS，可以做"算法替换"——同接口、同挂载位、不同实现。

#### 8.3.4 RegisterClassifier 用 register-feature 头降级 LLM call（解决 §8.2 §1.2 修订）

**现状**：[services/humanization/classifier.py:60-77](../../services/humanization/classifier.py#L60-L77) `RegisterClassifier.classify` 是 LLM 调用（每次窗口 5 条消息 → JSON 输出 6 档 label），单次 max_tokens=220、capabilities=("chat",)；写入 `REGISTER_LABEL_SLOT`。

**跨代论文**：Steering LLMs with Register Analysis (arXiv 2505.00679) Biber-style register feature classifier。

**Omubot 实施**：

- 新建 `services/humanization/register_features.py` 抽取 Biber 6 维度（involved-informational / narrative-non-narrative / explicit-situation-dependent 等）的 token-level 特征——纯 regex / 词表，无 LLM
- 新建 `services/humanization/lightweight_register.py` `FeatureBasedRegisterClassifier`，与现有 LLM classifier 同接口（同 `classify(messages) -> RegisterDecision`）
- 6 档映射：6 个 Biber 维度 → 6 档 label（neutral / quiet / playful / affectionate / serious / distant），用启发式规则 + confidence
- 切换路径：humanization_config 加 `register_backend in {"llm", "feature"}` flag，灰度
- LLM 降级为 fallback：feature confidence < 0.4 时回退到 LLM call

**真代际差**：MaiBot 没有 register 概念；Omubot 已有 RegisterClassifier + ModuleContract slot + RuntimeStateBus 三件挂载位，可以做"算法替换"。

#### 8.3.5 ToM Audience model 通过 ModuleContract 挂载（解决 §8.2 §1.3 修订）

**现状**：[services/humanization/contract.py:29-46](../../services/humanization/contract.py#L29-L46) `HUMANIZATION_CONTRACT` 已声明 9 个 state slot（含 `AFFECTION_FAMILIARITY_SLOT` / `AFFECTION_STAGE_SLOT`）；`StateSlotDefinition` 显式声明 schema / ttl / privacy。

**跨代论文**：EnigmaToM (ACL 2025 findings) belief / desire / intention 三元组建模。

**Omubot 实施**：

- 在 [services/humanization/contract.py](../../services/humanization/contract.py) `HUMANIZATION_CONTRACT.state_owns` 加 3 个 slot：
  - `_slot("state.audience.<uid>.belief", "omubot.state.audience_belief.v1", ttl="per_user", privacy="admin_only")`
  - `_slot("state.audience.<uid>.desire", "omubot.state.audience_desire.v1", ttl="per_user", privacy="admin_only")`
  - `_slot("state.audience.<uid>.intention", "omubot.state.audience_intention.v1", ttl="per_session", privacy="admin_only")`
- 新建 `services/humanization/audience_classifier.py`，复用 RegisterClassifier 的 LLM 调用模式——窗口 N 条消息 → JSON 输出 belief/desire/intention
- 写入路径：`classify_and_write` 同模式，写 3 个 slot
- 消费路径：scheduler RWS 加 `audience_compatibility` 信号——bot persona 与 audience belief 的对齐度（与 [scheduler_rws/rws.py](../../services/scheduler_rws/rws.py) `compute_rws` 加权层对接）

**真代际差**：MaiBot 没有 ModuleContract 抽象，没有 StateSlotDefinition 的 schema/ttl/privacy 声明——audience model 在 MaiBot 里只能 hardcode；Omubot 一行 `_slot(...)` 就能挂，且自动获得 admin_only privacy 保护、per_user TTL 隔离。

#### 8.3.6 SSE chunk 累积阶段加流式边界预测（解决 §8.1 C11 修订）

**现状**：[services/llm/client.py](../../services/llm/client.py) 是 raw aiohttp SSE 累积 → 完整 reply → segmentation.py 切分；[services/llm/segmentation.py:432-466](../../services/llm/segmentation.py#L432-L466) `natural_split` 是后置切分。

**跨代论文**：STS streaming separator-transducer (arXiv 2205.05199) 流式 ASR 边界检测。

**Omubot 实施**：

- 在 client.py SSE 解析回路（grep `delta` / `text_delta` 找入口）加边界检测 hook：每累积 N 个字符跑一次 lightweight boundary classifier
- classifier 实现：复用 §8.3.4 的 register feature 框架——纯 regex + 词表，无 LLM；输出 `(boundary_score, confidence)`
- 命中规则：`boundary_score > 0.7` 时**预切**——把累积的字符提前 emit 到 humanizer.delay 路径，不等完整 reply
- 与现有 segmentation 互补：SSE 边界预测产生 hard cut；segmentation.py 仍跑（处理未被预切的片段）+ 概率合并跨段
- 命中收益：第一段消息可在 LLM 还在生成时发出，体感"边写边发"

**真代际差**：MaiBot 是「先 LLM 完整生成 → 后段切」，结构上不可能上 streaming boundary；Omubot SSE 流式 + 现有 segmentation flag-gated 双层结构，可以分前置/后置两路并行。

#### 8.3.7 TCU completeness 投票替代 MaiBot 概率档（解决 §8.1 C14 修订）

**现状**：[services/llm/segmentation.py:265-289](../../services/llm/segmentation.py#L265-L289) `_natural_split_strength` 三档 split_strength（0.2/0.6/0.7）+ [segmentation.py:353-372](../../services/llm/segmentation.py#L353-L372) `_natural_merge_segments` 概率合并——这是 Part 5 §3.2 抄自 MaiBot 的同代算法，已跑在生产代码里。

**跨代论文**：CA TCU/TRP framework (Sacks/Schegloff/Jefferson 1974)；GRASS turn-taking annotation corpus (arXiv 2504.09980)。

**Omubot 实施**：

- 新建 `services/llm/tcu_completeness.py` `TCUCompletenessScorer`：
  - 输入：候选 segment 文本
  - 输出：`(completeness_score, is_TRP)`——完整性 0-1 + 是否为合法 transition-relevance place
  - 算法：基于句法完整性（grammatical closure）+ pragmatic closure（已表达完整意图）+ prosodic proxy（标点 + 语气词收尾）三维度
- 替换路径：[segmentation.py:353-372](../../services/llm/segmentation.py#L353-L372) `_natural_merge_segments` 改为 TCU-driven——`_rng_random > split_strength` 替换为 `tcu.is_TRP and tcu.completeness > 0.7`
- 落地策略：保留 `natural_split_enabled` flag；新增 `tcu_driven_segmentation` flag——三档（off / shadow / primary）灰度
- shadow 阶段对比：natural_split（MaiBot 概率档）vs TCU-driven 的段切结果，admin 看板可视化分歧率

**真代际差**：MaiBot 的概率档是纯启发式、无形式化锚点；Omubot 已有 segmentation 双层架构 + flag 灰度机制 + shadow 对比基础设施（可类比 [services/persona/shadow.py](../../services/persona/shadow.py) 模式）——可以做形式化模型替换并保留同代算法作 fallback。

### 8.4 §8 自审小结

| 视角 | 修正前 Part 6.5 § | 修正后真挂载位 | 跨代解 §8.3 编号 |
|---|---|---|---|
| 主动记忆检索 | §3.1 C7 "tool loop 挂载位齐备" | tool loop 已有，但 EpisodeStore 未挂任何 tool；CardStore 已挂 | §8.3.1 |
| 调度双层 | §3.1 C9 "RWS 单层加权" | RWS 已是 11 项 + bandit + flag 复合层；缺的是双 sigmoid | §8.3.2 |
| EOT 通道 | §3.1 C12 "MaiBot 没 EOT 挂载位"（暗示 Omubot 也没有） | Omubot 已有 LLM-based EOT 全链路在 RWS 跑；缺的是轻量化 | §8.3.3 |
| Register 通道 | §1.2 "无独立 classifier head" | RegisterClassifier 是独立 LLM classifier；缺的是 feature-based 降级 | §8.3.4 |
| Audience model | §3.3 C10 "运维负担需评估" | ModuleContract slot 一行就能挂；评估的是分类器精度，不是基建 | §8.3.5 |
| SSE 流式边界 | §3.1 C11 "替代 separator 启发式" | natural_split 已落地后置切；STS 应做 SSE 阶段前置预切 | §8.3.6 |
| TCU 形式化 | §1.4 "整篇 0 篇前沿引用"（暗示 Part 5 未落地） | natural_split 已落地（同代算法）；TCU 需替换概率档为完整性投票 | §8.3.7 |

### 8.5 自审纪律的递归应用

Part 4 v2-修订版 §0 自审纪律 4 条，本版 §8 自查表：

1. ✅ 每项改动必须明确"在 Omubot 实际部署场景下解决用户能感知的问题"——§8.3 7 项均给出具体收益（LLM 调用降为本地 / 段切提前 emit / belief 写入 admin_only slot）
2. ✅ 威胁模型 / 需求频率 / 运维负担与 Omubot 部署语境匹配——§8.3.5 ToM audience 用 admin_only privacy 槽位（与 Part 4 v2-修订版砍掉 MEXTRA 红队的"熟人群无攻击者"逻辑一致），不是企业 agent 防御
3. ✅ 论文 keyword 形式不构成入选理由——§8.3.3 不立"用 FastTurn"，立"把 LLM EOT 替换为 lightweight"；§8.3.7 不立"用 CA TCU/TRP"，立"概率档替换为 completeness 投票"——形态都是「同接口同挂载位的算法替换」
4. ✅ 真代际差在机制（挂载位），不在数据来源/论文形式——§8.3 全部 7 条的"代际差"都是「Omubot 已有的代码挂载位 + 跨代算法 = 同代框架做不到的组合」

### 8.6 状态收口（§8 部分）

- §8 仅是 §1-§7 的**自审追加**，不替换原本立场——§5 三种路径仍由用户选择
- §8.3 7 条跨代解作 candidate 列出，不进 execution；任何落地需用户单独点头
- §8.4 修正表可作 Part 6.5 v1.1 的种子（如要重写 §3.1 真代际差表）
- 与 maintenance-log.md 的关系：§8 是文档自审，未触动代码——不更新维护日志（与 §7 状态收口一致）
