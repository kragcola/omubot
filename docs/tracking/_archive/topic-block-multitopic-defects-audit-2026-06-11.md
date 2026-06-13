# 话题块系统缺陷审计 — 复杂多话题多人物情境

> 状态：审计 + 评审 + 搜索调研完成，待立项修复 | 日期：2026-06-11 | 类型：架构缺陷审计 → 重构评审 → 文献对标
> 触发：用户要求对话题块（TopicBlock / B 系列并行话题理解）系统做全量审计，并极端模拟"复杂多话题多人物"情境下的缺陷表现。
> 关联：[group-multitopic-understanding-b-series-design.md](group-multitopic-understanding-b-series-design.md)、[../migrations/b1-topic-block-attribution-2026-05-30.md](../migrations/b1-topic-block-attribution-2026-05-30.md)、[../migrations/b2-overhearer-role-2026-05-30.md](../migrations/b2-overhearer-role-2026-05-30.md)、[../migrations/multi-addressee-topic-routing-2026-06-11.md](../migrations/multi-addressee-topic-routing-2026-06-11.md)
> 方法：沿真实代码路径推演（非设计文档臆测），每条缺陷标注 `文件:行号` 证据。
> 文档演进：§1-5 缺陷审计（只记缺陷不含方案）→ §6 用户重构提案评审 → §7 学术/工程搜索审计（转向 conversation disentanglement 边模型）→ §8 风险B 再审（引用复活论点 + 三护栏）。后续章节会修正前面章节的早期结论，**以靠后章节为准**；早期已被推翻的判断（如原"§6.3 风险A=merge 两块"）就地标注撤销，不删除留痕。

## 0. 审计对象与线上现状

审计 B 系列并行话题理解系统的核心数据结构与归属逻辑。

| 层 | 文件 | 角色 |
|---|---|---|
| B1 归属 | [services/group/topic_block.py](../../services/group/topic_block.py) | `TopicBlockTracker` 把每条群消息聚到并发话题块 |
| B2 角色门 | [services/scheduler.py](../../services/scheduler.py) `_receiver_role` (~1138) | Goffman 三分类 addressed/ratified/overhearer |
| 锚点注入 | [services/scheduler.py](../../services/scheduler.py) `_maybe_anchor_topic_block` (~1166) | 概率开口锚到 bot 该参与块的代表消息 |
| 多寻址路由 | [services/scheduler.py](../../services/scheduler.py) `_build_block_triggers` (~1017) | burst 多人@按块分组、每块各自 fire |
| 信号提取 | [kernel/router.py](../../kernel/router.py) `_extract_topic_block_signals` (~293) | 从 event 抽 reply/@ 结构喂给 tracker |

**线上现状（`config/config.json` topic_block 段）**：

```json
{ "enabled": true, "overhearer_mode": "silent",
  "overhearer_threshold_boost": 0.0, "ratified_continuation_floor": 0.55 }
```

其余 `stale_seconds=300 / attrib_recent_seconds=120 / sim_threshold=0.4 / max_blocks=6` 取默认。系统已全量上线，且 overhearer 从灰度期的 `shadow` 升到 `silent`（纯旁听直接沉默）。

## 1. 极端情境模拟

设定：30 人活跃群，3 摊并发对话，bot 参与其中一摊。按 monotonic 秒走 `topic_block.py` 真实归属逻辑。

```text
摊1(烤鱼): A、B          bot 在这摊插过话(bot_involved=True)
摊2(原神): C、D
摊3(求助): E 技术问题
```

| t(s) | 事件 | 真实归属结果 | 块状态 |
|---|---|---|---|
| 0 | A:"今晚烤鱼?" | 新块 b1 | b1={A} |
| 3 | B:"好啊" reply→A | 规则2命中A的块 → b1 | b1={A,B} |
| 8 | bot 插话"我也要" | b1 标记 bot_involved | b1={A,B}, bot_involved=T |
| 20 | C:"原神更新了" | 与 b1.last_text 不相似 → 新块 b2 | b2={C} |
| 24 | D:"抽到了" @C | 规则3命中C → b2 | b2={C,D} |
| 40 | **A** 转聊原神:"我也抽了" | **规则4(同说话人)先于相似度命中→误并入 b1** ❌ | b2 没拿到 |
| 45 | E:"求助docker报错" @bot | 规则3:@bot,b1含bot→**误并入 b1** ❌ | b1 越滚越大 |

## 2. 缺陷清单（逐条对代码验证）

### 🔴 缺陷1 — participants 是 `set` 且只增不减 → 块坍缩成一团

[topic_block.py:148-150](../../services/group/topic_block.py#L148) 规则4（同说话人续话）**先于**规则5（词法相似）触发，且**完全不看文本**：

```python
for b in active:
    if (now - b.last_active) <= self._attrib_recent_s and speaker and speaker in b.participants:
        return b   # A 说什么都进 A 的旧块,哪怕换了话题
```

t=40 的 A 明明在聊原神，却因"A 在 b1 且 b1 在 120s 内活跃"被粘回烤鱼块。**同一个人 120s 内转话题，系统完全瞎**。participants 从不删除（[_apply](../../services/group/topic_block.py#L160) 只 `add`），块只增不减，多人重叠下**所有块趋向坍缩成一个 mega-block**——恰在它本该发挥作用的密集多话题场景失效。

### 🔴 缺陷2 — reply-to 的 message_id 真值被丢弃

QQ 最强信号是"这条 reply 了**那条具体消息**"。但 [router.py:293-325](../../kernel/router.py#L293) 只提取 `reply_to_self`(bool) 和 `reply_to_sender_id`，**不传被回复的 message_id**。归属逻辑 [topic_block.py:132-141](../../services/group/topic_block.py#L132)：

```python
if reply_to_self:
    for b in active:
        if b.bot_involved:
            return b   # 返回第一个(=最旧)bot 块,不管 reply 的是哪条
```

`active` 是 deque 左→右迭代（**最旧在前**）。bot 在 b1、b3 两摊都说过话时，用户 reply bot 在 **b3 的最新消息**，却被归到 **b1（最旧）**。块里存了 `message_ids` 却无 message_id→block 反查索引，真值白白浪费。

> 方向矛盾：`_attribute` 的 reply-to 取**最旧**块，而 [pick_anchor_block](../../services/group/topic_block.py#L200) 用 `max(last_active)` 取**最新**块——同一系统两处方向相反。

### 🔴 缺陷3 — `representative_speaker()` 用 set 顺序 → 锚到错的人

[topic_block.py:51-53](../../services/group/topic_block.py#L51)：

```python
def representative_speaker(self) -> str:
    return next(reversed(list(self.participants)), "")  # set 无插入序,"latest" 是假的
```

`_maybe_anchor_topic_block` 用它当 `target_user_id`（[scheduler.py:1192](../../services/scheduler.py#L1192)），而 message_id 来自 `representative_message_id()`（=最后@的那条，**可能另一个人**）。**reply 指向 X 的消息却 @ 了 Y**——正是 2026-06-11 多寻址修复要杀的"回错人"缺陷，从 speaker 字段又溜回来了。

### 🟠 缺陷4 — `deque(maxlen=6)` 按插入序淘汰，不按活跃度 → 活跃 bot 块被挤掉

[topic_block.py:112](../../services/group/topic_block.py#L112) `deque(maxlen=self._max_blocks)`。满了从**左侧(最早插入)**弹出，**与 last_active 无关**。极端：群里同时冒 7 个新话题，bot 正聊的 b1（最早插入但 1 秒前还活跃）被第 7 个新块挤出 deque → `bot_involved` 丢失 → 下一条 reply-bot 找不到块 → 角色降级 overhearer → **silent 模式下 bot 被话题洪水挤到沉默**，聊到一半哑火。

### 🟠 缺陷5 — overhearer=silent 保护随块坍缩而失效

线上 `overhearer_mode=silent`。但缺陷1导致 mega-block 吞掉无关话题后，该块是 bot_involved 的，`pick_anchor_block(require_bot_involved=True)` 对**任何**消息都命中它 → role 恒为 `ratified` → `ratified_continuation_floor=0.55` 触发开口。**本该防"插非己对话"的 F-α 保护，恰在密集多话题里被自己的块合并机制架空**，bot 反而对一团乱炖频繁插话。

### 🟡 缺陷6 — `mark_bot_involved` 生成期竞态 → bot_involved 贴错块

[topic_block.py:183-188](../../services/group/topic_block.py#L183) bot 说完话 `pick_anchor_block(require_bot_involved=False)` 取**当前最活跃**块标记。但 LLM 生成要几秒，期间别摊可能更活跃 → bot 答的是 b1，标记却落到现在最热的 b2。污染后续 reply-to-self / ratified 判定。

### 🟡 缺陷7 — 词法相似只比 `last_text` 单条，非块质心

[topic_block.py:155](../../services/group/topic_block.py#L155) `self._similarity.similarity(text, b.last_text)`。烤鱼块最后一条恰是"哈哈哈"（低信息）时，"烤鱼好吃吗"与"哈哈哈"相似度≈0 → 误开新块。话题连贯性由一条噪声消息裁定。

## 3. 严重度汇总

| # | 缺陷 | 触发条件 | 后果 | 严重度 |
|---|---|---|---|---|
| 1 | participants set 只增→块坍缩 | 多人话题重叠（常态） | 并发话题退化成一块，系统失效 | 🔴 高 |
| 2 | reply message_id 真值丢弃 | bot 参与≥2摊+被 reply | 归到最旧块，回错话题 | 🔴 高 |
| 3 | representative_speaker set 序 | 多人块概率锚点 | reply 指向人与@的人错位 | 🔴 高 |
| 4 | deque 按插入序淘汰 | ≥7 并发话题 | 活跃 bot 块被挤掉→沉默 | 🟠 中 |
| 5 | silent 保护被块坍缩架空 | 缺陷1下游 | F-α 防护失效，乱插话 | 🟠 中 |
| 6 | mark_bot_involved 竞态 | 生成期别摊更活跃 | bot_involved 贴错块 | 🟡 低 |
| 7 | 相似度比单条非质心 | 块末尾是噪声消息 | 误开新块 | 🟡 低 |

## 4. 根因归纳

缺陷在"复杂多话题多人物"这个**目标场景**里最严重——系统设计本为解决它，却因三个基础数据结构选择反向失效：

1. **participants 用无序 set 且只增**（缺陷 1/3/5 共同根因）——块没有"成员带时间戳/可衰减"概念，无法表达"A 离开了烤鱼话题"。
2. **无 message_id→block 反查**（缺陷2）——QQ 最精确的 reply 真值被降级成"reply 到某个 bot 块/某人块"。
3. **deque 容量淘汰用插入序而非 LRU-活跃度**（缺陷4）——话题洪水冲掉正在进行的对话。

**根因一句话**：系统把"并发话题"建模成了"参与者集合的并集"，而真实话题线程是"消息的回复树 / 时间窗口聚类"。2-3 摊、参与者不重叠时工作良好（设计文档测试场景）；一旦参与者跨摊重叠 + reply 链 + 话题转移，模型坍缩。

## 5. 修复优先级（仅排序，方案待立项讨论）

> ⚠️ 本节为 §6/§7/§8 之前的早期排序，**已被 §7.5 的 L0-L3 分层取代**。当时的"缺陷2 单点修"思路（router 多传 message_id + 加反查）后来升级为 §7.5 L0 边模型（系统性接回 reply 真值，同解缺陷2/3 并支撑单用户多块）。下面保留原排序留痕，立项以 §7.5 + §8.5 护栏为准。

- **缺陷2（message_id→block 反查）** — 最高性价比：数据已存在（`block.message_ids`），router 多传一个 message_id + 加反查即可，直接接回最强信号，顺带缓解 1/3。→ 升级为 §7.5 **L0 边模型**。
- **缺陷4（deque→按 last_active 淘汰）** — 改动最小（几行），立刻堵住"话题洪水挤掉 bot 块"。→ §7.5 **L2 活跃度生命周期**（守 §8.5 护栏一：不可只审高活跃块）。
- **缺陷1（participants 带时间衰减 / 同人转话题用文本 split）** — 根因，但改动最大，需重构块成员模型。→ §7.5 **L1 线性打分**（同说话人降为软特征，文献实测该硬规则仅 52.2% 成立）。
- 缺陷 3/5/6/7 多为 1/2 的下游或独立小修，待主结构定调后一并处理。缺陷7→§7.5 **L3 嵌入**（可选）；缺陷6 文献不覆盖（§7.6），单独处理。

---

# 6. 设计评审 — 注册/合并/降级体系（用户提案）

> 日期：2026-06-11 | 类型：方案评审（修复立项输入）
> 评审对象：用户提出的"块注册 + 临近合并 + 活跃度降级清理"重构思路。
> 立场（含后续修正）：模型骨架对路（直击 §4 根因）。本节最初提了"1 个必须翻转的设计 + 1 个乐观假设 + 3 个承重组件"，其中——「必须翻转 merge」经 §6.3 澄清A 撤销（用户本意是 attribute 单消息，正确）；「简述更新命门」经 §7.3③ 化解（可绕过/用 CPU 嵌入）；「乐观假设（活跃度门）」经 §8 收窄为"无引用接话"子集 + 三护栏。**§6.5 的 4 个问题已大半有答，最终方向见 §7.5（L0-L3）+ §8.5（护栏）。**

## 6.1 提案摘要

- 收消息时注册话题块，含：话题简述（实时更新）、创建时间戳、参与用户、各用户发送信息量等。
- 单用户可参与多个话题块。
- 新消息过一遍判是否临近话题；**若引用原话题消息则直接合并**（注：此处"合并"经 §6.3 澄清A 确认为 attribute 单条消息进被引用块，非 merge 两个块）。
- 块有活跃等级：对话中最高，不活跃逐渐降到 0；判归属时**只审视高活跃块**；不活跃块逐渐清理、取消注册、纳入记忆（待考虑）。

## 6.2 对 7 缺陷的应对（诚实打分）

| 缺陷 | 提案对策 | 判定 |
|---|---|---|
| 1 块坍缩(set 只增) | 单用户多块 + 丢弃"同说话人→同块"硬规则 | ✓ 模型层修好（根因消除） |
| 4 deque 插入序淘汰 | 活跃度衰减 + 不活跃清理 | ✓ 修好（淘汰改按活跃度） |
| 7 相似度比单条 last_text | 话题简述（质心）替代 | ~ 方向对，取决于简述更新机制（§6.4①） |
| 2 reply message_id 丢弃 | 引用消息归入被引用块 | ✓ 正确（§6.3 澄清A：attribute 单消息非 merge；前提=message_id 反查，见 §7.5 L0） |
| 3 representative_speaker set 序 | 结构化参与者 + 发送信息量 | ~ 提案用"信息量"半修；§7.6③ 边模型下前驱边天然带说话人，自然解决（信息量≠最近性这点仍对） |
| 5 silent 保护被架空 | 块不再坍缩→下游缓解 | ✓ 缺陷1 根因若由 L1 修掉则下游缓解；§8 风险B 收窄后残余可控 |
| 6 mark_bot_involved 竞态 | 未涉及 | ✗ 未解决（仍是生成期时序问题） |

## 6.3 结构性风险（风险A 已撤销，仅余风险B 且被 §8 收窄）

**澄清A（2026-06-11 用户更正）— 提案是 attribute 单消息，非 merge 块，无 blast radius**

> 早先版本此处误读了提案，记为"风险A：reply→直接合并两块"。用户澄清：提案的"合并"指**把引用消息这一条归并进被引用消息所在的话题块**（attribute 单条消息），**不是**把两个话题块 merge 成一个。块数不变，单条消息落位。这恰是"单用户同时处于多话题块"的设计支撑（他在块1说话、又引用块2某条→两块各落一条他的消息）。**此设计正确，与文献 attribute 范式一致，blast radius=1 条、下条自愈、有界——原"风险A"不成立，撤销。**

但它有一个**实现前提**和一个**开支边界**必须钉死，否则退化成审计缺陷2/3：

- **前提：按 message_id 反查，不能按 sender 反查。** "m 引用 x"必须落到"**x 这条具体消息**属于哪个块"，而非"x 的作者参与了哪些块"——后者在作者身处多块时选错块（正是缺陷2：现 `reply_to_self` 只返回第一个 bot 块，丢了 message_id 真值，[topic_block.py:132](../../services/group/topic_block.py#L132)）。承重件：`block.message_ids`（[topic_block.py:38](../../services/group/topic_block.py#L38)）已存数据，**缺 `message_id→block` 反查索引**。建好它，缺陷2/3 一并消解（边天然带目标消息+说话人）。
- **开支边界：这捷径只覆盖带引用的消息，省的是局部开支不是大头。** reply 命中→O(1) 直接归属、零相似度计算✅。但【文献实证】此类频道仅 ~48% 消息带 directed cue（@/reply 合计），真正带 reply 引用的更少；**占多数的无引用消息仍需相似度/时间判定**，这部分开支一分没省，且正是风险B（相似度门质量）与缺陷1（块坍缩）主战场。结论：引用即归属是 L0 里最干净、最该先做的一刀（ground-truth、零歧义、省局部开支），但它解决的是 minority 精确信号；majority 无引用消息仍需 L1 打分 + L2 活跃度兜底。

**风险 B — 活跃度门把块坍缩换马甲重现（rich-get-richer）**

"只审视高活跃块"有自我强化偏置：高活跃→吸引更多归属（因为只拿它去比）→保持高活跃→吸引更多……最活跃块变成**汇(sink)**，吞掉本该开新话题/归到安静块的消息。**这是缺陷1坍缩从"参与者重叠"换成"活跃度汇"重现**，块照样膨胀成 mega-block。

且"为省算力忽略低活跃块"与"正确归属本属于低活跃块的消息"直接打架：安静摊有人接话，归属器没拿那个块去比，只能误塞高活跃块或误开新块。

**核心诚实点**：在**无 reply 边的普通消息**（QQ 占绝大多数）上，提案仍 100% 押在相似度门质量上。多块成员只删了坏规则，没给"A 这条该进 A 的哪个块"的正面强信号。

> **§8 更新**：用户随后论证"低活跃块的复活消息基本带引用→走 L0 反查、绕开相似度门"，把风险B 从系统级收窄到"无引用接话"子集（保守 ~半数）。但残余非零，且**"只审视高活跃块"省算力的设计恰恰放大该残余**——见 §8.3，最终护栏见 §8.5 护栏一（不可裁剪低活跃块出候选池）。

## 6.4 三个承重却未定义的组件

**① 话题简述"实时更新" — 原列为命门，§7.3③ 已化解（可整个绕过或用 CPU 嵌入，非 LLM）**

- LLM 生成：每条群消息一次 LLM 调用更新简述。现 B1 核心美德是 *Pure-CPU, no I/O, no await*（[topic_block.py:13](../../services/group/topic_block.py#L13)）。30 msg/min 群=每分钟 30 次额外调用，成本/延迟灾难 + D2 cancel-path 风险。否，除非重度 debounce/批处理。
- 词法累加：关键词袋，比 last_text 强一点，但"判临近"仍是弱 n-gram——缺陷7 根没拔，从单条换成袋。
- 顺序陷阱：必须**先判归属再更新简述**；判成"新话题"时绝不能动旧块简述，否则自我污染。

**② 衰减 + 复活 + 纳入记忆（提案标的"待考虑"恰是最难）**

衰减曲线形状、"高活跃"阈值都是新调参面。硬骨头是**复活**：烤鱼块凉了被清理，10 分钟后有人"对了刚说的烤鱼"——块没了只能开新块，continuity 丢失。不做复活=从另一角度重造 staleness；做复活=必须落地"纳入记忆"跨层接口（scheduler 层临时结构耦合进 memory 子系统）。这不是"待考虑"，是承重件。

**③ 锚人需要 per-participant last-speak 时间戳，非"发送信息量"**

信息量大的人可能 5 分钟没说话，锚到他照样回错人（缺陷3 未真修）。锚人字段要的是最近开口时间戳。

## 6.5 落地前必须回答的 4 个问题（决定可行性）

> 状态更新：4 个问题经 §7/§8 大半有答，逐条标注。

1. **话题简述靠什么更新？** ✅ 已解（§7.3③）：可整个绕过（退回 antecedent-ranking + pairwise 特征，omubot 信号全已有），或用 CPU 级句向量增量平均。**不上 LLM**，命门解除。
2. **merge 与 attribute 分开吗？** ✅ 已解（§6.3 澄清A + §7.3①）：提案本就是 attribute 单消息，不存在 merge 块。reply→attribute 一条边，块=连通分量副产物。
3. **活跃度门相似度阈值多严？如何防高活跃块变吞噬汇（风险B）？** ⚠️ 部分解（§8.5 护栏一）：候选池**不得裁剪**低活跃块；省算力靠打分排序而非踢出。具体阈值待 L1/L2 实现时定。
4. **复活做不做？"纳入记忆"是真接口还是占位？** ⚠️ 待定（§8.5 护栏三）：低活跃块衰减≠物理删除，必须是可被引用唤回的软状态，否则 message_id 反查失败。是否落"纳入记忆"跨层接口仍需立项决策。

## 6.6 评审结论

**值得做，模型骨架对**（多块成员 + 活跃度生命周期，直击 §4 根因，模型层修 1/4/7、缓解 3/5）。落地要点：①以单消息 attribute 为基本操作（§6.3 澄清A：用户提案正确，引用归入被引用消息所在块=文献 attribute 一条边），实现走"消息→前驱边 + message_id 反查、块=连通分量"，比维护块对象+块归并更稳；②证伪活跃度门不会变新坍缩汇（取决于相似度门，而它正是弱环）；③§6.4 三承重件中"简述更新"已被 §7.3③ 化解（可绕过或用 CPU 嵌入），剩活跃度衰减曲线/复活策略待定。缺陷6（生成期竞态）本提案不覆盖，需单独处理。

下一步：§6.5 四问已大半有答（命门解除），最终落地路径见 §7.7（L0→L1→L2→L3）+ §8.5（三护栏）。

---

# 7. 搜索审计 — 学术/工程对标（解决思路提取）

> 日期：2026-06-11 | 类型：文献对标（修复立项输入）
> 方法：检索成熟/前沿项目与论文，把 omubot 话题块问题对接到已成熟的学术领域，提取可落地思路，回头校准 §6 提案与 §1-5 缺陷。
> 标注约定：**【文献实证】**=论文中的测量值/结论；**【映射推断】**=把文献结论应用到 omubot 的推理（我的判断，非论文原话）。

## 7.1 学科定位：这是 "Conversation Disentanglement"

omubot 话题块要解的"群里多摊并发对话、判这条属于哪摊"，在 NLP 里是一个有 ~20 年历史的成熟问题：**会话解纠缠（conversation disentanglement）**。奠基性资源是 Kummerfeld et al. 2019（ACL，IRC #Ubuntu 语料 77,563 条人工标注消息）。这意味着 omubot 不必从零摸索——主范式、参数经验值、失败教训都有实证。

**任务的学术定义**【文献实证】：把消息建模成**节点**，"这条回复了那条"建模成**有向边**，得到一张 reply-structure graph；**每个连通分量就是一场会话**。即——**会话是回复图的副产物，不是被直接维护的对象**。

## 7.2 主范式 vs omubot 现状（结构性差异）

| 维度 | 文献主范式（Kummerfeld 2019 / Elsner 2008 / Pointer-Net EMNLP2020） | omubot 现状 | 差异 |
|---|---|---|---|
| 核心操作 | **antecedent ranking**：新消息 m 到达 → 在时间窗内候选前驱里打分选最高者为"前驱"，或 self-link=开新会话 | message→cluster 单步规则瀑布归属（[topic_block.py:121](../../services/group/topic_block.py#L121)） | omubot **跳过了 reply-graph 中间层**，直接做块归属 |
| 会话/块 | reply-graph 的**连通分量**（union-find 自然涌现） | 显式维护的 `TopicBlock` 对象 + 块间合并 | omubot 把"块"当一等对象，引入了 merge 操作 |
| 信号用法 | time / directed(@/named) / 同说话人 / 词向量重叠 / 是否问句 → **线性或 FF 打分特征** | 同样信号，但用作**硬规则瀑布**（reply>@>同说话人>相似度） | omubot 把概率特征**硬化成 if-else**，丢了打分的可调性 |
| 在线性 | Pointer Networks（EMNLP 2020）证明逐条在线决策可行，不看未来消息 | 已是在线（observe 逐条） | ✓ 一致 |
| 简述/质心 | **不维护**话题简述；FF 模型最多用 GloVe 句向量平均（CPU 级嵌入） | 现用 last_text 单条；§6 提案要"实时更新简述" | 文献不需要简述这个组件 |

## 7.3 文献直接佐证/修正本审计的结论

**① 文献佐证"边模型"是正解（注：原写作"佐证风险A"，§6.3 已澄清提案本是 attribute 单消息、非 merge 块；本条改为正面论证边模型优于显式块对象）**

Kummerfeld 2019 §4.3 评测会话级一致性低于图级一致性，原因明确写道：

> *"a single link can merge two conversations, meaning a single disagreement in links can cause a major difference in conversations."*

【文献实证】文献维护的是**消息间的边**、会话是边的连通分量副产物——正因如此，"合并两场会话"从来不是一个被主动调用的操作，它只在"某条消息连到了另一分量的节点"时自然发生，代价受限于一条边。这印证了用户提案的方向：**以单条消息的归属（attribute）为基本操作，块/会话作为派生结果**，而不该把"话题块"当成一个需要主动 merge/split 的一等可变对象（omubot 现状把 `TopicBlock` 当一等对象维护，是与文献范式的主要结构差异）。

→ **对落地的指引【映射推断】**：用户的"引用消息归入被引用消息所在块"正是文献的 attribute 一条边。实现上**维护"消息→前驱边" + message_id 反查，块=连通分量**，比"维护块对象 + 在块上做归并"更稳——后者才有审计 §1 缺陷1/3 的坍缩与锚人错位。这一范式同时让缺陷3 自然消解（边天然带目标消息+说话人，不靠 set 顺序）。

**② 外部实证缺陷1根因（同说话人→同块的硬规则是错的）**

Lowe 2015/2017 的启发式只有 **10.8% precision**【文献实证】，Kummerfeld 2019 §5.3 归因于它做了脆弱假设，其中第一条：

> *"if all directed messages from a user are in one conversation, all undirected messages from the user are in the same conversation"* —— 实测**只有 52.2% 成立**【文献实证】。

omubot 的归属规则4（[topic_block.py:148](../../services/group/topic_block.py#L148) 同说话人→同块，且**先于**相似度、**不看文本**）正是这个假设的硬规则版本。**文献实测它只对一半【文献实证】**——这从外部独立证实了审计缺陷1（块坍缩）的根因。

→ **修正【映射推断】**：同说话人**不能**作为硬归属规则，只能作为**打分特征之一**（与时间、相似度共同加权）。这与 §5/§6 "丢弃同说话人硬规则"的方向一致，文献给了量化依据。

**③ 命门（§6.4① 简述更新）其实有第三条路——或可整个绕过**

§6.4 把"简述靠 LLM（成本灾难）还是词袋（质量弱）"列为命门。文献给出第三种现实：**主范式根本不维护话题简述**【文献实证】。Elsner 2008 / Kummerfeld linear 模型用的是 message-pair 特征（Δtime、directed、词重叠）；最强的 FF 模型也只是加了 **GloVe 句向量平均**（CPU 级嵌入，非 LLM，无 per-message 生成调用），graph-F 从 63.5→72.3【文献实证】。

→ **修正【映射推断】**：omubot **不一定需要"实时更新的话题简述"这个承重组件**。退回 antecedent-ranking + pairwise 特征（time/@/reply/word-overlap，omubot 全已有），可跳过简述。若要质心，用句向量（升级现有 `NgramSimilarityProvider`→embedding provider）做增量平均，CPU 级、无 LLM。**命门从"简述怎么生成"降级为"用不用嵌入相似度"，难度大幅下降。**

## 7.4 文献实测值校准 omubot 参数

Kummerfeld 2019 §5.4 用 77k 标注语料实测的分布【文献实证】，逐条对 omubot 默认参数：

| omubot 参数 | 现值 | 文献实测 | 校准建议【映射推断】 |
|---|---|---|---|
| `attrib_recent_seconds`（同说话人/@续话窗） | 120s | 连续消息 94.9% 在 2min 内、88.3% 在 8 条内，但 *"lower limits in prior work are too low"*（E&C 129s 偏低）；几乎全在 1h 内 | 120s 抓住了主峰，但 long-tail 续话会漏判→误开新块。考虑放宽到 ~10min 或改时间软衰减而非硬窗 |
| `stale_seconds`（块归档） | 300s | 会话常跨远超 5min；模型输出的链接很少 >2h | 300s 偏紧，活跃话题易被过早归档（呼应缺陷4）。建议 ≥1h 或活跃度衰减替代硬归档 |
| `max_blocks`（每群块数） | 6 | 并发会话 ≤3 占 **46.4%**、≤10 占 **97.3%**【文献实证】 | 6 对 QQ 小群够用（覆盖约 8-9 成场景），但大群高峰会溢出→deque 淘汰（缺陷4）。大群可调到 10 |
| 同说话人归属（规则4） | 硬规则 | 假设仅 **52.2%** 成立 | 降为软特征（见 §7.3②） |
| directed-cue 覆盖率 | @/reply 信号 | 只有 **48%** 消息带 directed cue【文献实证】 | 一半消息得靠文本/时间判定→相似度门是真承重，必须够强（呼应 §6.3 风险B"押在相似度质量上"） |

## 7.5 提取的解决思路（分层，按改动量递增）

把文献范式拆成可独立落地的层，对接 §5 修复优先级：

- **L0 边模型（最高价值）**：维护"消息→前驱边" + `message_id→block` 反查，块=连通分量（union-find）。reply 引用→把这条消息 attribute 到被引用消息所在块（用户提案，正确）。一举解决缺陷2（边带 message_id）+缺陷3（锚人=边的目标）+支撑"单用户多块"。注意：仅覆盖带引用的 minority 消息，无引用的 majority 仍走 L1/L2。
- **L1 归属瀑布→线性打分**：把现 if-else 规则瀑布（reply>@>同说话人>相似度）改成加权打分（同 RWS 的 logit 范式，本仓已有 `compute_rws` 可借鉴），同说话人从硬规则降为一个特征。解决缺陷1。
- **L2 活跃度生命周期**：用时间衰减活跃度替代硬 stale 窗 + deque 插入序淘汰，淘汰按活跃度。解决缺陷4。**注意 §6.3 风险B**：归属候选集不能只取高活跃块，否则高活跃块变吞噬汇——文献的"时间窗内全部候选"才是正解。
- **L3 嵌入相似度（可选）**：`NgramSimilarityProvider`→句向量 provider，质心用增量平均。解决缺陷7，且让 L1 的相似度特征更强。CPU 级、无 per-message LLM。

## 7.6 对 §6 提案的最终评审更新

§6.6 结论"值得做、模型骨架对"维持，文献进一步补强了三处：

1. **以单消息 attribute 为基本操作（印证用户提案）**：文献维护边、会话是连通分量副产物，"合并会话"非主动操作。落地走"消息→前驱边 + message_id 反查、块=连通分量"，优于把 `TopicBlock` 当一等可变对象去归并。用户"引用归入被引用块"正确，且只需 O(1)（带引用的消息）。
2. **§6.4① 命门降级**：简述更新不再是悬空命门——可整个绕过（pairwise 特征）或用 CPU 级嵌入，不必上 LLM。
3. **§6.4③ 锚人字段自然解决**：边模型下，前驱边天然指向具体消息+说话人，无需"per-participant last-speak 时间戳"这个补丁。

**未被文献覆盖的 omubot 特有项**：缺陷6（生成期 mark_bot_involved 竞态）是 omubot 异步架构特有，文献（离线/逐条同步）不涉及，仍需单独处理；`bot_involved` / overhearer 角色门（B2 Goffman）是 omubot 在 disentanglement 之上加的应用层，文献只解到"分块"为止，不解"bot 该不该在这块说话"。

## 7.7 下一步

修复立项时的推荐路径：**先做 L0（边模型）**——价值最高、文献背书最强，一并消解缺陷2/3 并支撑用户的"单用户多块 + 引用归属"设计。L0 落定后 L1（打分）→L2（活跃度，注意风险B）→L3（嵌入，可选）。§6.5 问题①（简述命门）已被 §7.3③ 化解，不再是立项前置阻塞。

## 7.8 来源

- [A Large-Scale Corpus for Conversation Disentanglement (Kummerfeld et al., ACL 2019)](https://ar5iv.labs.arxiv.org/html/1810.11118) — 任务定义、参数实测、merge blast-radius、52.2%/48%/并发数据
- [irc-disentanglement 数据集与模型 (GitHub)](https://github.com/jkkummerfeld/irc-disentanglement)
- [Online Conversation Disentanglement with Pointer Networks (EMNLP 2020)](https://aclanthology.org/2020.emnlp-main.512) — 在线逐条决策范式
- [Disentangling Chat (Elsner & Charniak, Computational Linguistics 2010)](https://direct.mit.edu/coli/article/36/3/389/2062/Disentangling-Chat) — pairwise 线性打分 + 局部/全局推断奠基
- [A Hierarchical Pre-Trained Model for Conversation Disentanglement (2020)](https://ar5iv.labs.arxiv.org/html/2004.03760)
- [Disentangling Online Chats with DAG-Structured LSTMs (2021)](https://ar5iv.labs.arxiv.org/html/2106.09024) — 用 turn/mention/timestamp 结构线索
- [Findings on Conversation Disentanglement (2021)](https://arxiv.org/abs/2112.05346)
- [Topic Detection and Tracking with Time-Aware Document Embeddings (2021)](http://arxiv.org/abs/2112.06166v2) — 时间感知嵌入（L3 参考）
- [Event-Driven News Stream Clustering using Entity-Aware Contextual Embeddings (2021)](https://ar5iv.labs.arxiv.org/html/2101.11059) — 流式 k-means 变体、micro-cluster 衰减（L2 参考）

---

# 8. 风险B 再审 — "引用回复复活低活跃块"论点（用户提出）

> 日期：2026-06-11 | 类型：论点验证（调研 + 严苛审计）
> 用户论点：风险B 担心"低活跃块没进候选池→消息被误塞高活跃块"。但低活跃话题在群里**已被顶到消息列表很上面**，群友若要接它**基本会用引用回复**；引用走 message_id 反查（L0）激发该块活跃度，**根本不经过相似度候选池**。故风险B 被大幅缩小。
> 审计立场：论点**结构正确且重要**，把风险B 从"全局"压缩成"无引用的话题转移"这一窄缝；但其经验前提（"基本会用引用"）只**部分**被实证支持，剩余缺口真实存在。下面分解。

## 8.1 论点的结构性内核（先讲对在哪）

把用户论点形式化，它其实是一条**信号分流论证**：

> 复活一个低活跃块的消息，要么 (a) 带引用 → 走 L0 message_id 反查，O(1) 精确命中该块，**绕开活跃度门**；要么 (b) 不带引用 → 才落到相似度候选池，才有"低活跃块没进池"的风险。

这是对的，且很关键：**风险B 只在 (b) 子集上成立**。L0 边模型一旦落地，凡 ground-truth 引用的复活全部精确归位，与块的活跃度高低无关——活跃度门只服务于"无引用、需推断"的消息。**用户实际上指出了：L0 不只是省开支，它还把风险B 的作用域砍掉了一大块。** 这一点 §7 没说透，是真实补强。

## 8.2 调研证据：引用回复确实承担"跨时复活"功能（定性强、定量弱）

**【文献实证·定性】** Yang 2025（WeChat 会话分析，*Studies in Applied Linguistics and TESOL*）把 Quote-and-Reply 的首要功能命名为 **"sequence-jumping：enables responses to non-adjacent turns across temporal gaps"**——精确对应"复活被顶上去的旧话题"这一行为。即：**当用户要接一个非相邻（已被新消息盖过）的话题时，引用回复正是为此而生的平台机制**。这从交互语言学侧支持了用户论点的方向。WeChat 是与 QQ 最同构的平台（同区、同为腾讯多人异步群聊、Q&R 交互一致），外部效度高。

**【文献实证·定量】** Kummerfeld 2019：directed cue（@/named/reply 合计）覆盖 **48%** 消息；且"会话内连续消息 94.9% 在 2min 内、几乎全在 1h 内"——**话题转移（gap 大）的接话，正是 directed cue 更可能出现的场合**（相邻闲聊往往裸接，跨 gap 接话才需要显式指认对象）。这与 Yang 的定性结论方向一致：**gap 越大→越需要引用→越可能带 ground-truth 信号**。对用户论点是顺向证据。

**缺口（必须诚实）**：
- 没有任何来源给出 QQ/微信"**复活旧话题时用引用的比例**"这一精确数字。Yang 是 7 样本定性研究，无频率统计；Kummerfeld 的 48% 是 IRC（**无原生 quote-reply UI**，directed=靠 @名字），不能直接当成 QQ 引用率。
- Twitter 研究（Garimella 等，*To Reply or to Quote*）反而指出 **quote 更多用于"broadcast/reframe"而非接续对话**——提示"引用"的动机并不单一，存在"引用了但不是为接旧话题"的噪声方向（对 L0 是误归属风险，见 §8.4）。

**结论**：用户论点的**方向**有文献支持（引用=跨时复活的机制），但"**基本会**用引用"的**强度**无定量背书。合理保守估计：复活旧话题时用引用的概率显著高于普通接话，但**远非 100%**——裸文本复活旧话题（"诶刚说的烤鱼几点？"不带引用）在熟人群是常见行为。

## 8.3 剩余缺口的真实大小（风险B 残余作用域）

风险B 在 L0 之后只剩这一窄缝，但缝不为零：

**残余 = 无引用 × 话题转移/复活 × 落到相似度门。** 量级估算【映射推断】：
- 设 directed/引用率乐观取 50%（借 Kummerfeld 48% 上调，因 QQ 有原生 quote UI 优于 IRC）。
- 则 ~50% 复活消息无引用，全压在相似度门。
- 这部分里，低活跃块若被排除出候选池（用户提案"只审视高活跃块"省算力），就**必然**误塞高活跃块或误开新块——风险B 在此子集内 100% 兑现。

所以用户论点把风险B 从"系统级"缩到"无引用接话"子集，是真实收窄；但该子集**绝对量仍可观**（按上述 ~50%），不能当成已消除。尤其是：**用户提案的"只审视高活跃块以省算力"恰恰是放大这个残余缺口的设计**——它和 L0 的精确归属是两件事，L0 救的是带引用的，省算力的裁剪伤的是不带引用的。

## 8.4 反向风险：引用回复≠话题归属正确（L0 自身的误差源）

用户论点依赖"引用 = 该消息属于被引用块"。但调研给出两个反例方向，L0 实现时必须知道：

1. **【文献实证】Twitter 研究**：quote 常用于 broadcast/reframe——把旧消息拎出来**另起话题评论**，而非续入原话题。QQ 同样有"引用某条→转而开新话题吐槽"的用法。此时 L0 把这条 attribute 进被引用块是**错的**（它其实是新块的起点）。
2. **spotlighting（Yang 2025 第三类）**：引用是为"强调/玩梗"，可能既不属于旧块也不开新块。

→ **对 L0 的修正【映射推断】**：reply 边是**强先验，不是硬真值**。L0 仍应以 message_id 反查为主信号，但保留一个轻校验——被引用消息与当前消息的相似度/时间若极端背离，标记为"引用但可能另起"，不盲目并入。这与 §7.3① "reply 边是强先验非硬真值"一致（Kummerfeld directed-cue 也只 48% 且非全可靠）。**好在这是单消息 attribute（澄清A），误了 blast radius=1 条，可接受。**

## 8.5 审计结论

用户论点**成立且重要**，应纳入设计，但需配三条护栏：

1. **采纳**：L0 边模型让"带引用的复活"精确绕开活跃度门——这是风险B 的主要解药，用户指出的分流正确，§7 据此补强。
2. **护栏一（不可省的候选池）**：**反对"只审视高活跃块"这条省算力裁剪。** 无引用的话题复活（保守 ~半数）必须仍能命中低活跃块，否则风险B 在该子集 100% 兑现。省算力应另寻途径（如候选池按"活跃度+近期性"打分排序但**不裁剪**低活跃块，或对低活跃块用更便宜的相似度），而非直接踢出候选。
3. **护栏二（引用非硬真值）**：L0 对引用做轻校验，防 broadcast/reframe/spotlight 类引用被误并入旧块（§8.4）。误差有界（单条），可接受但要记日志。
4. **护栏三（活跃度衰减≠删除）**：呼应 §6.4②——低活跃块在被引用复活前不能真删（取消注册），否则 message_id 反查失败、L0 救不回。"纳入记忆"必须是可被引用唤回的软状态，不是物理销毁。这是 §8 论点能成立的隐含前提，必须钉死。

## 8.6 来源

- [Quote-and-Reply in WeChat: A Conversation Analytic Study (Yang, 2025)](https://journals.library.columbia.edu/index.php/SALT/article/view/14084) — Q&R 的 sequence-jumping（跨时复活）/response-facilitation/spotlighting 三功能；与 QQ 最同构平台
- [To Reply or to Quote: Comparing Conversational Framing Strategies on Twitter (ACM TSC 2023)](https://dl.acm.org/doi/10.1145/3625680) — quote 更多用于 broadcast/reframe，引用动机非单一（§8.4 反向风险）
- [A Large-Scale Corpus for Conversation Disentanglement (Kummerfeld 2019)](https://ar5iv.labs.arxiv.org/html/1810.11118) — directed cue 48%、连续消息时间分布（§8.2 定量侧）
- [Who Is Answering to Whom? Finding "Reply-To" Relations in Group Chats with LSTMs (Guo 2017)](https://link.springer.com/chapter/10.1007/978-981-10-6520-0_17) — 群聊 reply-to 关系自动识别（无引用消息仍需推断的佐证）

---

# 9. 单模块对标 — 四个悬空不确定项的成熟方案拆解

> 日期：2026-06-11 | 类型：针对性文献对标（修复立项输入）
> 触发：用户要求"对文档仍存在的问题和不确定性追加调研，搜索成熟/前沿项目与论文，不追求与 omubot 相似，而是单模块有参考价值"。
> 方法：先盘出文档 §6-§8 仍 hand-wavy 的 4 个悬空点，每个点各找一个**有成熟实现/实证数字**的单模块方案，拆出可直接落地的机制，映射回 omubot。
> 标注约定沿用 §7：**【文献实证】**=论文/实现里的具体机制或数值；**【映射推断】**=应用到 omubot 的判断（非原文）。

## 9.0 四个悬空点（本节要打的靶）

§6-§8 留下四处只给了方向、没给落地机制的承重件：

| # | 悬空点 | 文档出处 | 现状缺口 | 对标的单模块 |
|---|---|---|---|---|
| 靶一 | 活跃度**衰减曲线 + 复活 + "纳入记忆"软状态** | §6.4②、§8.5 护栏三 | 只引一句 micro-cluster decay，没给衰减公式、没给"复活如何不丢块" | **EDMStream**（密度衰减 + outlier reservoir 复活） |
| 靶二 | **省算力但不裁剪低活跃块**的候选池 | §8.5 护栏一 | "对低活跃块用更便宜的相似度"无任何外部背书，等于口号 | **Entity Resolution Blocking/Filtering**（两阶段：高召回廉价候选 → 精确验证） |
| 靶三 | **不上 LLM 的话题表征/简述**增量更新 | §7.3③、§6.4① | 只说"句向量平均"，没给成熟增量方案与遗忘机制 | **BERTopic OnlineCountVectorizer + c-TF-IDF**（partial_fit + decay） |
| 靶四 | **衰减状态下"被引用即复活"的检索打分** | §8.5 护栏三、§6.4② | 复活靠 message_id 反查是结构，但"软状态怎么算分/怎么被唤回"没机制 | **Generative Agents memory stream**（recency×importance×relevance，last-access 衰减） |

四个靶彼此正交：靶一管"块的生命周期数据结构"，靶二管"归属时的候选池策略"，靶三管"块的内容表征"，靶四管"复活时的打分/检索"。下面逐个拆。

## 9.1 靶一 → EDMStream：密度衰减 + outlier reservoir 复活（直接给衰减公式与复活机制）

[EDMStream（Gong et al. 2017）](https://ar5iv.labs.arxiv.org/html/1710.00867) 是 density-based 流式聚类，专门解"簇随新数据涌现、随旧数据 fade out 而演化、且要实时更新"——与 omubot"话题块随消息涌现/凉掉"同构。它把 §6.4② 那句含糊的"活跃度衰减 + 复活 + 纳入记忆"**全部给成了带公式的机制**：

**① 衰减曲线（解 §6.4② "衰减曲线形状"悬空）**【文献实证】

freshness（新鲜度）用指数时间衰减：`f_i^t = a^(λ(t − t_i))`，论文取 `a=0.998, λ=1`，值域 `(0,1]`；`|λ|` 越大遗忘越快。簇的"timely density"= 簇内所有点 freshness 之和 `ρ_c^t = Σ f_i^t`。关键是**增量更新公式**：簇吸收一个新点时 `ρ_c^{t+Δ} = a^(λΔ)·ρ_c^t + 1`——即"旧密度按时间衰减一次，再 +1"。

→ **映射【映射推断】**：omubot 的"活跃等级"就该是这个 `ρ_c`，不必自创曲线。每块存 `(activity, last_update_ts)`，下次 touch 时 `activity = a^(λ·Δt)·activity + 1`，O(1)、纯 CPU、无定时器（惰性求值，被访问时才算）。这同时替掉缺陷4 的 `deque` 插入序淘汰（按 `activity` 淘汰）和缺陷1 的 `stale_seconds` 硬窗（衰减代替硬归档）。`a/λ` 可由 §7.4 实测的时间分布标定（连续消息 94.9% 在 2min 内 → 选 `a,λ` 使 2min 后 freshness 仍显著、1h 后趋近 0）。

**② 复活机制 = outlier reservoir（解 §8.5 护栏三 "衰减≠删除"，这是全文最关键的对标命中）**【文献实证】

EDMStream 用**两个**存储结构，而非一个：

- **DP-Tree**：当前活跃簇（高 timely-density）。
- **Outlier Reservoir**：缓存**低 timely-density 的 cluster-cell**——"temporally not considered for clustering"（暂不参与聚类，但**不删除**）。

簇降级与复活是**双向**的：① cluster-cell 因"点太少（低局部密度）**或**点过时（低 timely-density）"被移入 reservoir;② **reservoir 里的 cluster-cell 仍能吸收新点，密度涨够就重新插回 DP-Tree 参与聚类**（原文："The cluster-cells in the outlier reservoir are possible to absorb new points and be inserted to DP-Tree for clustering again"）。

→ **映射【映射推断】**：这正是 §8.5 护栏三要的"低活跃块衰减成可被唤回的软状态、不是物理删除"的成熟实现。omubot 应有**两层块池**：活跃池（参与归属候选）+ 冷却池（reservoir，退出主候选但保留 `message_ids` 反查索引）。块活跃度衰减到阈值 → 移入冷却池（**不取消注册、不删 message_id**）；冷却池块被引用（L0 反查命中）或吸收到新点 → 活跃度回升、移回活跃池 = 复活。"纳入记忆"在此框架下是 reservoir 的**更下一级**（reservoir 也满了才落 memory），不是复活的必经路径——复活只需 reservoir 命中即可，澄清了 §6.4② "纳入记忆是不是承重件"的疑问：**reservoir 才是承重件，"纳入记忆"是 reservoir 的溢出归宿（可后置）**。

**③ 附带印证 L0 边模型**【映射推断】

EDMStream 的簇定义是 **DP-Tree**（每点依赖唯一的"最近的更高密度点"，**有向**依赖树），与 DBSCAN 的"无向连通分量"对比明确。这与 §7.5 L0 的"消息→前驱边、块=连通分量"是同一范式家族（antecedent ranking 的密度版）——独立来源再次指向"维护有向边、块是派生结果"优于"维护可变块对象"。

## 9.2 靶二 → Entity Resolution Blocking/Filtering：高召回廉价候选池 → 精确验证（给 §8.5 护栏一一个成熟范式）

§8.5 护栏一只喊了句"省算力应另寻途径（如打分排序但不裁剪低活跃块），而非踢出候选"，没给范式。[Entity Resolution 的 Blocking/Filtering 综述（Papadakis et al. 2019）](https://ar5iv.labs.arxiv.org/html/1905.06167) 正是把"如何在不漏真匹配的前提下削减 O(n²) 候选"做了 20 年的成熟领域，直接给了护栏一要的两件武器：

**① 两阶段范式：Filtering（高召回廉价过滤）→ Verification（精确验证）**【文献实证】

ER 的核心结构是"candidate selection（廉价、只剪真负、**允许假正**）→ candidate matching（精确、贵）"。关键性质：**Filtering 是"exact procedure that produces no false negatives"**——即廉价过滤层的设计铁律是**绝不漏真匹配**，只允许放进假正让后面精确层去除。三个度量【文献实证】：
- **Pair Completeness (PC) = recall**：候选池里真匹配占全部真匹配的比例——**这是不可牺牲的那个**。
- **Pairs Quality (PQ) = precision**、**Reduction Ratio (RR) = 削减比**：这两个是"省算力"指标，可以牺牲。

→ **映射【映射推断】**：这从一个独立领域给了 §8.5 护栏一的理论靠山——**省算力 = 提高 RR/PQ，但 PC（召回）是硬约束不能动**。omubot 归属应分两层：**廉价候选层**用 O(1)/O(块数) 的便宜信号（时间窗 + 活跃度 + 是否带 @/reply）框出候选块**且必须含低活跃块**（保 PC），**精确层**只对候选块算贵的相似度/嵌入（省的是这一层的次数）。"只审视高活跃块"之所以错，用 ER 语言说就是：**它为了 RR 牺牲了 PC**——而 PC 是唯一不能牺牲的。

**② "省算力不靠裁剪、靠廉价签名"的具体手法**【文献实证】

ER 给了多个"既不漏、又便宜"的 Block Building 手法，全部是 omubot 可借的廉价签名思路：
- **Redundancy-positive blocks（重叠候选块）**：每个实体进**多个**块，"两实体共享的块越多→越可能匹配"——天然支持 §6 "单用户多块"，且把"匹配可能性"编码成**共享块数**这个 O(1) 整数，免算相似度。
- **Sorted Neighborhood + 动态窗口（DCS）**：按 blocking key 排序后滑窗，`d/c ≥ φ`（已发现重复/已比较数）才扩窗——一个**自适应候选池大小**的成熟范式，比 omubot 固定 `max_blocks=6` 更稳。
- **Canopy Clustering**（综述归为 Block Processing）：用**一个廉价距离**先框 canopy（高召回、可重叠），再在 canopy 内用贵距离精算——正是护栏一"对低活跃块用更便宜的相似度"的标准名字与实现。

→ **映射【映射推断】**：omubot 不必发明候选池策略。直接用"廉价签名（@target / reply-target / 近期活跃 token 重叠）建重叠候选块 → 候选块内才算句向量相似度"两阶段。低活跃块只要**廉价签名命中**就进候选（保 PC），省的是"精确相似度只在候选块内算"（提 RR）。这条把 §8.5 护栏一从口号变成了有名有实现的范式。

## 9.3 靶三 → BERTopic：OnlineCountVectorizer + c-TF-IDF 的无 LLM 增量表征（给 §7.3③ 一个成熟增量实现）

§7.3③ 说"简述可绕过或用 CPU 级句向量平均"，但没给"增量更新 + 遗忘旧词"的成熟实现。[BERTopic 的 Online Topic Modeling](https://maartengr.github.io/BERTopic/getting_started/online/online.html) 给了一个**完全不调 LLM、纯 sklearn `partial_fit`** 的增量话题表征流水线，正好填这个洞：

**① 增量管线 = partial_fit，无 LLM**【文献实证】

BERTopic 把话题建模拆 6 步，在线场景只需 2-4 步有增量变体：降维 `IncrementalPCA`、聚类 `MiniBatchKMeans`（或 river 的 `DBSTREAM` 用于**动态新增簇**）、分词 `OnlineCountVectorizer`，全部 sklearn `.partial_fit` 风格，**预训练句向量模型（Sentence-Transformers）一次性加载、不需持续更新**。即整条链零 per-message LLM 调用。

**② 话题表征 = c-TF-IDF，且自带遗忘（解 §7.3③ "质心怎么增量+怎么忘旧"）**【文献实证】

话题词不靠 LLM 生成，靠 **c-TF-IDF**（class-based TF-IDF）：把一个簇内所有文档当一个"类"做 TF-IDF，自然得到代表词。关键是 `OnlineCountVectorizer` 的两个增量参数：
- **`decay`（0~1）**：每次迭代把旧 bag-of-words 词频**按比例衰减**，例如 `decay=.01` 每轮降 1%——"makes sure that recent data has more weight than previous iterations"。**这正是话题表征版的活跃度衰减**：旧词逐渐淡出簇的代表词。
- **`delete_min_df`**：词频衰减到阈值下的词**从词表删除**，防稀疏矩阵无限膨胀。

→ **映射【映射推断】**：这给了 §6.4① "话题简述实时更新"的**第三条非 LLM 实现**（比 §7.3③ 的"句向量平均"更细）：块的"简述"= 块内消息的 c-TF-IDF top-k 词，增量维护，`decay` 让旧消息的词自动淡出（与靶一的活跃度衰减同构、可共用一个 `Δt`）。判归属时"新消息 vs 块"= 新消息 token 与块 c-TF-IDF 向量的余弦——比缺陷7 的"比 last_text 单条"强（是块质心非单条噪声），又不碰 LLM。`decay`/`delete_min_df` 的存在直接回答了 §6.4① "顺序陷阱/简述污染"的担忧：**衰减是按时间被动发生的，不需要"先判归属再更新"的人工排序**——新消息归属后才计入该块的 c-TF-IDF，判错的消息不会污染（它进的是别的块）。

**③ 诚实边界**【映射推断】

BERTopic 整套（UMAP+HDBSCAN+embedding）对 omubot 是**重依赖**（sentence-transformers 模型、sklearn），未必值得整体引入。可借的是**机制而非库**：c-TF-IDF 公式 + `decay` 词频衰减思路，用本仓已有的 `NgramSimilarityProvider` 升级实现，不必装 BERTopic。这与 §7.5 L3"`NgramSimilarityProvider`→句向量 provider"一致，且补上了"表征如何随时间遗忘"的机制。

## 9.4 靶四 → Generative Agents：recency×importance×relevance 检索打分（给 §8.5 护栏三"软状态怎么算分/被唤回"一个成熟公式）

§8.5 护栏三说低活跃块要"可被引用唤回的软状态"，但"软状态怎么打分、怎么决定唤回哪个"没机制。[Generative Agents（Park et al. 2023）](https://ar5iv.labs.arxiv.org/html/2304.03442) 的 **memory stream** 检索函数给了一个被广泛复刻的成熟打分式，正好是"衰减记忆如何被检索唤回"的范式：

**① 检索打分 = recency × importance × relevance**【文献实证】

每条 memory 存 `(自然语言描述, 创建时间戳, 最近访问时间戳)`。检索时三项加权：
- **Recency**：对"自**上次被检索/访问**以来的小时数"做指数衰减，**decay factor = 0.995**。注意是 **last-access** 衰减，不是 last-create——被唤回一次，recency 就刷新。
- **Importance**：区分平凡/核心记忆（让 LLM 打 1~10 分，或其他实现）。
- **Relevance**：当前 query 与 memory 的（嵌入）相似度。

→ **映射【映射推断】**：这给了 omubot 块"软状态"的**完整打分模型**，且和前三靶严丝合缝：
- **Recency** = 靶一 EDMStream 的活跃度 `ρ_c`（同是指数时间衰减，GA 的 0.995/小时 与 EDMStream 的 `a=0.998` 同一族）。关键启发：用 **last-access 而非 last-create** ——**块被引用复活一次，recency 就刷新**，这正是 §8.5 护栏三"被引用唤回"的打分实现（L0 反查命中 = 一次 access = recency 回升 = 自动复活，无需显式"移回活跃池"的特判，与靶一 reservoir 复活殊途同归）。
- **Relevance** = 靶三 c-TF-IDF/句向量相似度。
- **Importance** = omubot 可加的块级先验（如 bot_involved 块权重更高，呼应缺陷5 的 F-α 保护）。

**② 对"只审视高活跃块"的再否证**【映射推断】

GA 的检索**对整个 memory stream 打分排序取 top-k，从不预先裁掉低 recency 的记忆**——低 recency 记忆若 relevance 极高仍会被检索到。这与靶二 ER 的"PC 不可牺牲"、§8.5 护栏一完全同向：**三个独立领域（流式聚类的 reservoir、ER 的 PC、GA 的全量打分）一致反对"为省算力预先踢掉低活跃候选"**。省算力只能体现在"打分/排序/取 top-k"，不能体现在"提前删候选"。这是本轮调研对 §8.5 护栏一最强的交叉印证。

## 9.5 四靶汇总 → 对 L0-L3 与三护栏的增量结论

本轮把 §6-§8 的四个悬空点各钉到一个成熟方案，**不改变 §7.5/§8.5 的结论，只把"待定"补成"有公式/有实现/有度量"**：

| 悬空点 | 此前状态 | 本轮对标后 | 落地接口 |
|---|---|---|---|
| 活跃度衰减曲线 | "形状待调参" | `activity = a^(λΔt)·activity + 1`（EDMStream，惰性求值） | 块加 `(activity, last_access_ts)` 两字段 |
| 复活 / "纳入记忆" | "纳入记忆待考虑" | **两层池**：活跃池 + 冷却池(reservoir，不删 message_id)；reservoir 溢出才落 memory | reservoir 是承重件，memory 可后置 |
| 候选池省算力 | "另寻途径"口号 | **两阶段**：高召回廉价签名候选(含低活跃块,保 PC) → 候选内精确相似度(提 RR) | L1 打分的候选生成层 |
| 无 LLM 表征 | "句向量平均" | **c-TF-IDF + decay 词频衰减**（BERTopic 机制，非整库） | L3 升级 `NgramSimilarityProvider` |
| 软状态打分/唤回 | "可被唤回的软状态" | **recency(last-access)×importance×relevance**；引用命中=access=recency 刷新=复活 | L0 反查后刷新 last_access |

**一句话**：四个此前 hand-wavy 的承重件，分别在流式聚类（EDMStream）、实体解析（ER Blocking）、在线话题建模（BERTopic）、生成式智能体记忆（Generative Agents）四个**互不相关**的成熟领域里各有标准解，且四解彼此自洽（衰减同族、复活=last-access 刷新、候选池保召回三方共识）。立项时 L0-L3 + 三护栏可直接引用本节的公式/字段/接口，不再有"待定参数"挡路。唯一仍需 omubot 侧决策的是各阈值（`a/λ`、候选池大小、`decay`、top-k）的具体取值——这属于实现期调参，非设计期阻塞。

## 9.6 来源

- [Clustering Stream Data by Exploring the Evolution of Density Mountain — EDMStream (Gong et al. 2017)](https://ar5iv.labs.arxiv.org/html/1710.00867) — 指数衰减公式 `a^(λΔt)`、timely-density 增量更新、**outlier reservoir 复活机制**（靶一）
- [A Survey of Blocking and Filtering Techniques for Entity Resolution (Papadakis et al. 2019)](https://ar5iv.labs.arxiv.org/html/1905.06167) — Filtering 无假负铁律、PC/PQ/RR 度量、Canopy/Sorted-Neighborhood/redundancy-positive 候选范式（靶二）
- [Online Topic Modeling — BERTopic 文档](https://maartengr.github.io/BERTopic/getting_started/online/online.html) — `partial_fit` 增量管线、`OnlineCountVectorizer` 的 `decay`/`delete_min_df`、c-TF-IDF 无 LLM 表征（靶三）
- [Neural topic modeling with a class-based TF-IDF procedure (Grootendorst 2022)](https://arxiv.org/abs/2203.05794) — c-TF-IDF 原始论文（靶三理论侧）
- [Generative Agents: Interactive Simulacra of Human Behavior (Park et al. 2023)](https://ar5iv.labs.arxiv.org/html/2304.03442) — memory stream 的 recency(decay=0.995, last-access)×importance×relevance 检索打分（靶四）
