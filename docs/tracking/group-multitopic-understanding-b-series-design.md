# B 系列设计：群聊多话题并行理解（话题块归属 → 角色 → 开口）

> 状态：2026-05-30 **已立项**（设计草案完成，待实施；B1 先行）。承接 [fix-prob-fire-stale-topic-sticker-2026-05-30.md](fix-prob-fire-stale-topic-sticker-2026-05-30.md) §17 的治本立项建议。
> 定位：P 系列（P0′/P1′）是**治标**——在"概率插话 + 单点锚定"框架内修补 R1/R2/R3。B 系列是**治本**——给 bot 建立调研（§9–§12）指出的**三段式群聊理解**：①消息属哪个并行话题块 → ②我是什么接收角色 → ③该不该开口。
> 原则：**复用现有骨架，不推倒重来**；轻量信号优先、不训练专门模型；强信号走规则、弱信号走打分（放弃预测下一说话人，Inner Thoughts CHI25 实证 self-selection 下 ≈ 随机）。

## 0. 为什么需要 B 系列（治本 vs 治标）

§1 的"鱼鱼烧"bug 只是"③开口了但①②错了"的一个可见切片。P0′ 把锚点指向"最新一条"，仍假设 **最新消息 = 回应对象**——这在简单的"旧话题残留"下能止血，但群聊真实形态是 Elsner & Charniak 实测的"**平均 2.75 个会话并行**、36% 消息带 @提及"。一旦两三个话题块**同时活跃**（不是一个停了一个起），"指向最新"照样接错块。

根因是架构层的：**bot 没有"话题块"这个概念**。它有 timeline（线性消息流）、有 RWS（要不要开口的概率）、有 AddresseeHint（对谁说），但没有"现在群里有几条并行话题线、每条消息属于哪条、我在哪条里"的表征。B 系列就是补这个表征，并让角色判断和开口决策建立在它之上。

## 1. 现有可挂载骨架（审查确认，B 系列不新建平行系统）

| 现有设施 | 文件:行号 | B 系列怎么用 |
| --- | --- | --- |
| `GroupTimeline` turn 带 `speaker`/`message_id` | timeline.py:210-218 | B1 的块归属输入：每条消息的说话人、消息 id |
| `[QUOTED_MSG sender_id=…]` reply 结构 | router.py:672-676 | B1 的**最强 parent 边**：reply-to = 显式 tying，QQ 白给的 ground-truth |
| `@<qq号>` / `@我` 渲染 | router.py:683-685 | B1 的 addressee 边 + B2 的寻址信号 |
| `TopicDriftDetector`（相邻相似度标量） | topic_drift.py:37-55 | B1 的**弱信号兜底**：无 reply/@ 时用相似度判同块 |
| `evaluate_group_gate_shadow`（shadow 模式，已算 is_addressed/has_other_at/reply_to_bot/followup_kind） | reply_workflow.py:588 | **B2 直接转正**：这就是 Goffman 角色判断的信号层，只差接管决策 |
| RWS `info_gain` 空壳字段 + 集中填充点 | rws.py:32, scheduler.py:1063 | B3 动机分的接入点之一 |
| thinker `light_reply`/`light_kind` 三态 | thinker.py（closing P0 已建） | B3 动机分的输出落点（已有弱回复通道） |
| `add_pending_trigger`（把回应锚点喂 LLM） | timeline.py:256 | B1 块代表消息 → 锚点（P0′ 同款 API，对象从"最新"升级为"块代表"） |

**关键判断**：B1（话题块）是唯一全新的能力，但它的**输入数据全部已存在**（reply-to 边、@、speaker、时间、相似度）。B2/B3 基本是把已有 shadow gate 与 thinker 三态"接线 + 增维"。

## 2. B1 — 话题块归属（治①，第一优先）

理论依据：Elsner & Charniak / Jiang 的**轻量实时解缠**——用 reply-to 链 + 时间窗 + 词重叠把消息聚成线程；Sacks 的 **skip-connecting**——消息靠 tying（reply 引用/@/重复指称）接回它真正所属的话题线，而非紧邻前句。

### 2.1 数据结构

新增 `services/group/topic_block.py`，`TopicBlockTracker`（per-group，in-memory，挂在 scheduler 旁，类比 `_GroupSlot`）：

```text
TopicBlock:
  block_id: str            # 自增/uuid
  message_ids: list[int]   # 块内消息 id（时序）
  participants: set[str]   # 块内说话人 QQ
  last_active: float       # 块内最后一条的时间戳
  last_text: str           # 块内最后一条文本（供相似度比较）
  bot_involved: bool       # 块里出现过 @bot / reply-bot / bot 自己发言
```

Tracker 维护**最近 K 条窗口**（如 30 条 / 10 分钟，取小）内的 2–4 个活跃块，超窗或长时间不活跃（如 >5min）的块归档。

### 2.2 归属算法（增量，每条新消息一次，纯 CPU）

对每条新消息 `m`（speaker、message_id、text、reply_to_id、at_targets），按**信号强度降序**找归属块：

1. **reply-to 边（最强，ground-truth）**：若 `m` 引用了某条消息 `p`（来自 `[QUOTED_MSG]`/QQ reply 字段），`m` 归入 `p` 所在块。skip-connecting 的直接实现——哪怕 `p` 在很多轮之前。
2. **@ 边**：若 `m` @了某人 `u`，且某活跃块的 `participants` 含 `u` 且该块近 T 秒活跃，归入该块。
3. **同说话人延续**：`m` 的 speaker 在某块近 T 秒内刚发过言 → 倾向同块（Ma et al. speaker property）。
4. **语义相似度兜底**：用 `TopicDriftDetector`/`NgramSimilarityProvider` 算 `m` 与各活跃块 `last_text` 的相似度，取最高且 ≥ 阈值的块。
5. **都不命中** → `m` 开**新块**（Egbert schisming：topic disjunction + 无 uptake）。

每步可加时间衰减（块越久不活跃，归入门槛越高）。归属是**软判定**：记录 top-1 块 + 置信度，供下游用。

### 2.3 与 prob-fire 锚点的衔接（替换 P0′ 的"指向最新"）

prob-fire 命中、需要锚点时，不再指向"最新一条"，而是问 Tracker：**bot 该参与哪个块？**

- 有块含 `bot_involved=True`（@bot/reply-bot/上次 bot 在该块发言）→ 选该块；
- 否则选**最活跃块**（last_active 最新 + participants 最多）；
- 锚点 reason 用该块的**代表消息**（块内最后一条或被 @ 的那条），而非全 timeline 最新。

这样 §1 场景下：两个表情若自成一个新块（与"鱼鱼烧"旧块分离），bot 要么锚到表情块（回应表情/短一句），要么因该块无 bot_involved 且语义稀薄而走 B3 压低开口——**不会再跨块捞起已归档的"鱼鱼烧"块**。

### 2.4 落点与复用

- 新增：`services/group/topic_block.py`（Tracker + TopicBlock）。
- 接线：`scheduler.notify` 入口喂 `m` 给 Tracker（已有 message_text/user_id，需补 reply_to_id/at_targets——router 已解析，加进 notify 入参或 trigger.extra）；prob-fire 锚点处查 Tracker。
- 复用：相似度用现成 `NgramSimilarityProvider`；reply/@ 用 router 已解析的结构；不引入向量库、不训练。

### 2.5 验证

- 单测：构造"旧话题 A 停 + 新表情块 B"序列，断言 B 自成块、bot 锚点选 B 不选 A（直接复现 §1 并验证修复）；reply-to 跨 5 轮断言归回原块（skip-connecting）；@他人消息断言归入对方块而非 bot 块。
- D1 同模式：confirm reply_to 解析点（router QUOTED_MSG）与 @ 解析点（router:683）是 Tracker 输入的唯一来源，无第二处遗漏。
- 离线评测（可选）：扒历史日志跑 Tracker，人工抽样看块划分合理性（对齐 §10 的 Local-k 思想，不追求 exact-match）。
- 回滚：Tracker 是旁路只读组件；prob-fire 锚点加 `if tracker_enabled` 开关，关掉即回 P0′ 的"指向最新"。

## 3. B2 — 接收角色判断接管（治②，复用 shadow gate）

理论依据：Goffman 参与框架——bot 每条消息可能是 addressed（被寻址，有回应义务）/ unaddressed-ratified / overhearer（旁听，无义务，沉默是常态）。

**现状**：`evaluate_group_gate_shadow`（reply_workflow.py:588）**已在 shadow 模式**计算 `is_addressed / has_other_at / reply_to_bot / last_assistant_to_user / followup_kind`——这正是角色判断的信号层，只是 `action` 不被消费（日志 `mode=group_gate_shadow action=pass`）。

**B2 做的事**：把 shadow gate 的角色输出**逐步转正**，与 B1 的块归属结合，产出每条消息 bot 的角色：

- `reply_to_bot` 或 @bot 或 B1 判定"bot 所在块且被指向" → **addressed**（强信号，进既有 bypass 必答）；
- `has_other_at`（@了别人）或 B1 判定"消息属于 bot 未参与的块" → **overhearer**（默认沉默，只更新理解/记忆，Goffman/SSJ）；
- 其余 → **unaddressed-ratified**（可选自选发言，交 B3 动机分决定）。

**转正路径（灰度，不一刀切）**：

1. 先 shadow 跑 B1+B2 联合，日志记录"若按角色决策会怎样" vs 实际行为，对比误伤率；
2. 再让 overhearer 态**仅影响 RWS 阈值**（overhearer → 阈值上调，更难开口），不直接否决；
3. 数据稳后才让 overhearer 在无 B3 高动机时直接默认沉默。

**复用/落点**：reply_workflow.py 已有全部信号字段，B2 = 加一个"角色聚合"函数 + 在 scheduler 消费它（先调阈值，后接管）。不新建。
**验证**：shadow 对比误伤率；@他人消息断言判 overhearer；D2 cancel-path 不涉（纯判定无副作用）。回滚：保持 shadow / 阈值影响关掉即回。

## 4. B3 — 动机打分替换裸概率（治③，对齐 Inner Thoughts）

理论依据：Inner Thoughts（CHI25）——不预测"谁该说"，而是对"要不要接这条"按**内在动机**打分，动机过阈才开口。8 维启发式裁剪为最小子集。

**现状**：开口决策是 RWS 概率回归（要不要开口）+ thinker `light_reply`/`light_kind` 三态（怎么回）。缺"该不该回这条"的语义动机维度。

**B3 做的事**：在 thinker 决策处增一次轻量打分（可并入现有 thinker LLM 调用，不新增往返），维度裁剪为三项：

- **相关性**：这条/这个块与 bot 人设、近期参与的话题相关度（Inner Thoughts 最重维度）；
- **信息缺口**：bot 是否有该块缺的、值得补的信息（避免无意义附和）；
- **会否刷屏**：结合 B1 块活跃度 + 冷却状态，高频块里抑制。

配 `im_threshold`（开口动机阈值）。**分工不变**：RWS 仍管"时机/频率"层（时机交规则的精神保留），B3 管"该不该回这条内容"。"沉默越久越想说"用已有 `consecutive_skip` 接 λ 衰减（Inner Thoughts d_p）。

**复用/落点**：`info_gain` 空壳承接"信息缺口"维度；thinker 三态承接输出；`consecutive_skip` 承接沉默衰减。新增主要是 thinker prompt 的打分段 + 三阈值 config（对齐 closing P0 已建的 thinker 扩展模式）。
**风险最高**：动机分调高 = bot 更话痨，与人性化主线"回得恰当非更多"相悖。**必须最后做、必须烤群数据支撑、必须与节制（P2′ 的 consecutive_unanswered + 已有冷却）同批**。
**验证**：动机分单测（相关高/信息缺口大→分高；纯附和→分低）；D2 cancel-path（打分在 thinker 内，取消不污染）；灰度 shadow 对比话痨度。回滚：`im_threshold` 设极高即等效关闭，回 RWS 裸概率。

## 5. 落地顺序与边界

| 阶段 | 治 | 依赖 | 新增量 | 风险 |
| --- | --- | --- | --- | --- |
| **P0′+P1(a)′（治标，先行止血）** | R1/R2 | 无 | ~5 行 + 正则 + 激活 info_gain | 低 |
| **B1 话题块归属** | ① | 无（输入数据已存在） | 新 `topic_block.py` + notify 接线 | 中（核心地基） |
| **B2 角色接管** | ② | B1（块归属增强角色） | shadow gate 转正 + 聚合函数 | 中（灰度可控） |
| **B3 动机分** | ③ | B1+B2 | thinker 打分段 + 三阈值 | **高（话痨回潮）** |

**演进关系**：B1 不是推翻 P0′，而是把 P0′ 锚点对象从"最新一条"**平滑升级**为"块代表"——P0′ 先上止血，B1 上线后无缝替换锚点来源。B2 复用 shadow gate 转正。B3 最重最后。

**明确不做（除非实测痛，§14 L3）**：训练型解缠/addressee 模型（MPC-BERT/W2W）、F1/F2 显式分类器、LLM 全量话题分簇每条都跑。轻量信号 + 现有骨架已能拿大部分收益。

## 6. 风险与开放问题

- **B1 块划分错误**会传导到 B2/B3——需 shadow 期对照人工抽样，确认 reply-to/@ 强信号下准确率足够高再转正。
- **B3 话痨回潮**是全系列最大风险；动机分上线必须同批节制 + 灰度 + 可一键回退（高 `im_threshold`）。
- **冷启动/无 reply 元数据**：若某些消息无 reply-to（QQ 偶发丢字段），B1 退化到相似度兜底，准确率下降——需评估退化是否可接受。
- **成本**：B1/B2 纯 CPU 近零成本；B3 若并入现有 thinker 调用则零新往返，若独立调用需评估延迟。
- **调参**：所有阈值（块窗口、相似度阈值、im_threshold、λ）均需本仓真实群活跃度调参，文献数值仅作起点。

**待用户决策**：是否立项 B 系列；若立项，B1 是否先行（建议先行——它是三段式地基，且 §1 的复杂版只有 B1 能治）。实施时每阶段单独出 D3 四列迁移清单 + 测试 + 回滚路径。

## 7. 缓存命中性能评估（B 系列上线前置约束）

> 目标：确保 B1/B2/B3 不降低 prompt 缓存命中。**结论先行：只要遵守"per-turn 内容只进 dynamic 段或消息尾、绝不前置于缓存前缀"这一条铁律，B 系列对缓存命中近零影响。** 下面给出依据、逐阶判定、铁律、基线度量。

### 7.1 现有缓存契约（已读代码确认，这是不可违反的边界）

仓库的缓存断点是**单一真相源**，由 `apply_cache_breakpoints`（llm_request.py:318）统一盖戳，调用方预设的 `cache_control` 一律剥离（llm_request.py:91,110,350）。关键事实：

- **system 三段固定序**：`static → stable → dynamic`（llm_request.py:183-204），断点打在**每段段尾**，外层优先；超过 Anthropic ≤4 上限时**dynamic 段断点最先被牺牲**（llm_request.py:367-369）。
- **main 任务预算**：`system_breakpoints=3 + message_breakpoint=True + tools` = 5 → capped 到 4，丢最外层（llm_request.py:267 注释）。意味着 **dynamic 段实际上常常拿不到独立断点**——它本就被设计成"前缀之后、每轮可变"的尾部。
- **消息侧断点在倒数第二条**：`_build_group_messages` 把断点设在 `len(messages)-2`（client.py:3625），**最后一条（pending 合并）永远在缓存前缀之外**（client.py:3611-3621）。
- **per-turn 注入的既有落点**：`addressee_hint` / `instruction_hint` 已经进 `plugin_dynamic`（client.py:4188-4196）；DeepSeek native 路径下 state_board + plugin_dynamic 走 `_append_tail_metadata` 塞到**消息尾**（client.py:4198-4205）。这就是 per-turn 内容的**安全落点先例**。

边界图（缓存前缀 = 可复用部分）：

```text
[tools] [system: static | stable | dynamic] [msg ...... msg(len-2)⊛] [msg(last)=pending]
└─────────────── 缓存前缀（命中区）────────────────────┘            └─ 前缀外（每轮新）─┘
   static/stable 必须字节稳定        dynamic 段+最后一条 = per-turn 安全区
```

### 7.2 逐阶缓存影响判定

| 阶段 | 往 prompt 加什么 | 落点 | 缓存影响 |
| --- | --- | --- | --- |
| **B1 话题块归属** | prob-fire 锚点（块代表消息） | 经 `add_pending_trigger` → pending → **最后一条消息**（前缀外） | **零**。锚点本就落在缓存前缀之外，与现有 trigger reason 同位（closing P0 已验证） |
| **B1 Tracker 本身** | 不进 prompt（纯 CPU 旁路组件） | 内存，不参与 LLM 调用 | **零**（无 prompt 改动） |
| **B2 角色判断** | 角色标签影响 RWS 阈值/是否 fire | **不进 prompt**（决策层，调度侧） | **零**。仅改"要不要调 LLM"，不改 prompt 内容 |
| **B2 角色提示（若需喂 LLM）** | "你现在是旁听者"之类提示 | 必须进 `plugin_dynamic`（同 addressee_hint） | 零~极小。dynamic 段本就每轮变，不动 static/stable 前缀 |
| **B3 动机打分** | 三维打分的 prompt 段 | 并入**现有 thinker 调用**的 dynamic 段 | 见 7.3，需专门约束 |

### 7.3 B3 的专门风险与约束（唯一需要小心的）

B3 把"相关性/信息缺口/会否刷屏"打分塞进 thinker。thinker 的 profile 是 `system_breakpoints=2`（llm_request.py:268），两个断点在 static + stable 段尾。**风险**：若把 per-turn 的打分维度（依赖当前块、当前消息）误放进 thinker 的 **static 段**，会让 thinker 的 static 前缀每轮变化 → thinker 缓存命中崩塌（thinker 当前 `hit=35-50%`，见 §1 日志）。

**约束（B3 实施红线）**：

1. B3 打分的 per-turn 输入（块摘要、当前消息、相关性上下文）**只能进 thinker 的 dynamic 段**，绝不进 static/stable。
2. B3 打分的**指令/schema/维度定义**（每轮字节相同）放 static 段——这部分可缓存，且应该缓存。
3. 优先**复用 thinker 现有的 conversation 输入位**喂块上下文，不新增独立 system 块（新块会改变段内 hash）。
4. 若 B3 需独立 LLM 调用（非并入 thinker），给它**自己的 TaskCacheProfile** + static 指令段，per-turn 输入走 messages，别污染 system。

### 7.4 上线前后基线度量（D4 证据要求）

每阶上线必须 before/after 对照，用现有度量入口（无需新建）：

- **逐块 hash 对照**：`cache_debug | session=… system=[hash,hash,…]`（client.py:663）——上线前后录同群同场景的 system 块 hash 序列，**static/stable 块的 hash 必须逐字节不变**；只允许 dynamic 段尾 hash 变化。任何 static/stable hash 抖动 = 缓存前缀被污染，回退。
- **per-task 命中率**：usage 表的 `cache_r / cache_w / hit%`（日志 `record | type=… cache_r=… hit=…%`）——按 task（main / thinker）取上线前 N 次调用的 hit% 均值作基线，上线后同口径对比，**main 与 thinker 的 hit% 不得下降**（容差 ±2%，超出即查）。
- **断点数不超限**：`apply_cache_breakpoints` 已硬性 cap 到 4，但新增 dynamic 块若改变段结构，跑现有缓存测试（test 套件里 apply_cache_breakpoints / `_build_group_messages` 断点相关用例）确认 ≤4 不变 + 段尾位置不变。

### 7.5 性能小结

- **B1/B2 对缓存命中零影响**：B1 锚点落在前缀外（最后一条消息），Tracker 是 CPU 旁路；B2 是调度层决策，默认不进 prompt。两者可放心上线。
- **B3 是唯一需要缓存纪律的阶段**：打分内容必须严格落在 dynamic 段 / messages，static/stable 前缀字节不动。遵守 7.3 红线则命中不降。
- **额外正收益**：B2 让 overhearer 态默认沉默 = **减少不必要的 LLM 调用次数**，整体 token 成本下降（少调用 = 少 miss 机会）；B1 把"接错块"的无效回复消除 = 减少返工式调用。
- **度量已就绪**：cache_debug 逐块 hash + usage hit% 双轨，足够做 before/after 把关，无需新建度量设施。

**实施前置条件**：B3 落地的 D3 清单必须包含 7.3 四条红线的逐条核对 + 7.4 的 before/after hit% 对照证据，否则不予上线。

## 8. 评估：B 系列能否杜绝「插进非己对话块 + 强行展现自己」

> 触发：烤群日志观察到 bot 插进本不属于它的对话块、做不必要的自我展现。诚实评估 B1（已实现）/B2/B3（设计中）能否杜绝——**结论先行：B1 单独不能杜绝；这个现象需要 B2（角色判断）为主、B3（动机抑制）为辅，B1 只是必要前提。**

### 8.1 现象拆成两个独立故障

用户描述的是**两个叠加的故障**，必须分开评估：

- **F-α「插进非己对话块」**：群里 A、B 在聊某话题，bot 既没被 @、也没参与该话题，却凑进去发言。
- **F-β「强行展现自己」**：即便话题与 bot 相关，它的发言动机是"刷存在感/接梗炫技"，而非真有内容。日志里 thinker 的 thought 直接暴露这点：`"夸他破纪录厉害，再轻快回应我是谁的梗"`、`"接梗逗趣，假装是自己在烤鱼鱼烧"`、`"有人分享视频，想轻松地夸一下，活跃气氛"`——这些是**表演性动机**，不是被需要。

### 8.2 逐故障对照 B1/B2/B3

| 故障 | B1 话题块归属 | B2 角色判断 | B3 动机打分 |
| --- | --- | --- | --- |
| **F-α 插错块** | **部分**：B1 让锚点指向"bot 该参与的块"——但 `pick_anchor_block` 在没有 bot-involved 块时**回退到"最活跃块"**（topic_block.py:pick_anchor_block），即仍会选一个 bot 没参与的块去接。B1 解决"接哪个块更连贯"，**没解决"我到底该不该参与这个块"** | **能治**：B2 判 bot 在该块是 addressed / overhearer。overhearer（没 @、没 reply-bot、bot 不在块参与者里）→ 默认沉默。这才是"不插进别人块"的正解（Goffman 旁听者无回应义务） | 间接 |
| **F-β 展现自己** | **不治**：B1 只管"回应锚点"，不管"该不该回、回得是否必要" | 部分：overhearer 沉默顺带压掉一部分表演 | **能治**：B3 的"信息缺口/相关性"维度——纯接梗炫技无信息增量 → 动机分低于 `im_threshold` → 不发。这是 F-β 的正解 |

### 8.3 诚实结论

1. **B1 单独上线不能杜绝 F-α，反而可能"更自信地插错块"**。这是必须正视的风险：B1 把"乱接旧话题"改成"连贯地接某个块"，但若那个块 bot 本不该参与，B1 只是让插入**显得更自然**，没拦住插入本身。`pick_anchor_block` 回退到"最活跃块"正是隐患点——它假设"既然要 fire，就找个最像样的块接"，而没问"是否该 fire 进这个块"。

2. **真正杜绝 F-α 的是 B2**。B2 的 overhearer 判定（没被寻址 + 不在块参与者中 → 沉默）直接对应"不插进别人的对话"。B1 是 B2 的**前提**（要判断"我在不在这个块的参与者里"，先得有块），但 B1 不是充分条件。

3. **F-β 需要 B3**。日志里的表演性 thought 是 thinker 在"既然要回，就找个有趣的角度"——根因是**裸概率决定"要回"、thinker 只决定"怎么回"**，没有一层问"这条回复有无必要"。B3 的动机分（信息缺口/相关性/会否刷屏）正是补这层。

4. **当前 B1 实现的一个可立即收紧的点**：`pick_anchor_block` 的"回退到最活跃块"应改为——**没有 bot-involved 块时返回 None（不注入锚点），而非硬选一个块**。返回 None 时 prob-fire 仍按原概率走（不恶化），但不会主动把 bot"安排"进一个它没参与的块。这是 B1 内部就能做的防 F-α 收紧，不必等 B2。

### 8.4 建议

- **不要指望 B1 单独解决这个现象**。B1 已上线（默认关）解决的是"回旧话题"（接错**时间**上的旧块），不是"插进别人**当前**的块"。
- **F-α 的正解是 B2**，应将 B2 提前、作为这个现象的主修复；B1 的 `pick_anchor_block` 同时按 8.3.4 收紧（no-bot-involved → None）。
- **F-β 的正解是 B3**，但 B3 风险最高（话痨回潮的反面是"过度沉默"），需灰度。
- **优先级调整建议**：原计划 B1→B2→B3，鉴于该现象，B2 的 overhearer 沉默判定价值前移。**待用户确认是否调整 B 系列内部优先级（B2 提前）+ 是否先做 8.3.4 的 B1 收紧。**
