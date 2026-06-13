# Living Persona — Part B：Generative Life（单角色生活叙事）

> 系列：[Living Persona / 角色生命化](living-persona-part0-overview.md)（Part 0 总纲）
> 状态：**立项待批 · 2026-06-08**
> 关联：[Part A：Dialogue Climate](living-persona-partA-dialogue-climate.md)（情绪线，L3 交汇）、[Part C：Social Narrative](living-persona-partC-social-narrative.md)（社会化叙事，依赖本 part）
> 对标：Stanford Generative Agents（[arxiv 2304.03442](https://huggingface.co/papers/2304.03442)，2023）。
> 在系列中的角色：bot 过着"谁的人生"——经历→反思→规划闭环，让日程从随机现编变成角色驱动、跨天连贯的生活叙事。
> 原则：调研先行，分层 MVP（L1–L3），每层独立可用、可停在稳定态；2026-06-08 补充 [故事弧调研](living-persona-research-story-arc-2026-06-08.md) 后，新增 **L1.5 Story Arc（剧情弧账本）** 作为长线叙事中间层。

---

## 0. 一句话

当前日程是"无状态、与人设解耦、孤立于记忆之外的每日随机现编"；本 part 对标 Generative Agents，给出单角色生活叙事的 L1–L3 落地路径，**复用已有 memo/Dream 基建**，让 bot 根据角色过出自己连贯的生活。多角色/真人入叙事见 [Part C](living-persona-partC-social-narrative.md)。

---

## 1. 现状诊断：日程的"组织度"（代码实证）

| 维度 | 现状 | 证据 |
| --- | --- | --- |
| 角色驱动 | ❌ 几乎为零，生成器只收到 `identity_name`（一个名字） | [chat/plugin.py:1101](../../plugins/chat/plugin.py#L1101) `identity_name=ctx.identity.name`；人设 source.md 的性格/背景/关系从不流向日程 |
| 自我推演 | ❌ 无，每天 02:00 独立现编，不读昨日、不读记忆 | [generator.py:121-166](../../plugins/schedule/generator.py#L121) prompt 只含当天日期+日历 |
| 跨天因果 | ❌ 无，prompt 只要求"当天内前事影响后事" | [generator.py:54](../../plugins/schedule/generator.py#L54) |
| 与记忆系统连接 | ❌ 孤岛，日程在 dream/memo/consolidator 中零引用 | grep 全仓 0 命中 |
| 故事涌现 | ⚠️ 仅单日 `theme`+`day_narrative`，天与天不连成弧 | 6-04~6-08 五份日程主题各自独立 |
| 单日局部质量 | ✅ 较好，有画面、有情绪起伏 | 今日：闹钟响三遍才起→数学课被点名答对小得意→食堂红烧牛肉面 |

**诊断结论**：它是"每天交一篇命题日记"的生成器，命题只有"今天星期几 + 什么节日"。用户的"随便而无组织"判断成立。

---

## 2. 前沿对标：你预想的那套叫 Generative Agents

用户描述的"角色有自己的生活、自动推演、产生自己的故事"，精确对应 Stanford **Generative Agents**（[arxiv 2304.03442](https://huggingface.co/papers/2304.03442)，[HAI 概述](https://hai.stanford.edu/news/computational-agents-exhibit-believable-humanlike-behavior)）。其三件套：

1. **Memory Stream（记忆流）+ 检索**：经历存为自然语言条目，检索按 **recency × importance × relevance** 三因子打分。
2. **Reflection（反思）**：周期性把零散观察综合为更高层洞察（"我最近总熬夜→可能压力大"），洞察反过来影响行为。
3. **Recursive Planning（递归规划）**：自顶向下分解（粗线条一天→小时→分钟）；**关键：用昨日经历 + 角色身份 seed 今日计划**，计划遇事件动态重规划。

效果即用户预想：agents 醒来做早餐、上班、记得并反思过去的日子，自发组织情人节派对——**故事从连续状态中涌现，而非被编排**。后续谱系：斯坦福 2024 扩到 [1000 真人 agent](https://hai.stanford.edu/policy/simulating-human-behavior-with-ai-agents)；2025–2026 前沿（[Emergence World](https://www.emergence.ai/blog/emergence-world-a-laboratory-for-evaluating-long-horizon-agent-autonomy)）研究 agent 连续运行数周后的行为漂移与长时程自洽。

---

## 3. 关键发现：omubot 的零件已齐，日程只是没接线

把 GA 三件套与 omubot 现状对照：

| GA 组件 | omubot 是否已有 | 现状证据 |
| --- | --- | --- |
| Memory Stream + 三因子检索 | ✅ 有 | `MemoExtractor` + retrieval，带 recency/relevance（[memo/plugin.py:58](../../plugins/memo/plugin.py#L58)）；179 张记忆卡（memory_cards.db） |
| Reflection（综合洞察） | ✅ 有雏形 | **Dream agent** 夜间 consolidation：合并/修正/交叉验证记忆卡、事件边界检测（[dream/plugin.py:330](../../plugins/dream/plugin.py#L330)） |
| Recursive Planning（角色+昨日→今日） | ❌ 缺 | 日程只有名字、不读昨日、不递归 |
| 三者互联 | ❌ 断 | 日程 ↔ 记忆 ↔ 反思 零连接 |

**结论**：离用户预想缺的不是"从头造 generative agent"，而是三件接线：①日程吃到角色；②日程吃到昨天 + 最近经历；③反思反哺日程。Dream agent 现在已经在夜里"做梦整理记忆"——它本就该是 reflection 层，只是现在只反思"记忆卡"，没反思"我这一天 / 我这一生"。

---

## 4. 落地路径：L1–L3（复用已有基建）

GA 的递归规划落到 omubot，三个递进 MVP。每层独立可用、可停在稳定态。

### L1 — 角色驱动 + 跨天连续（改动最小，立竿见影）

- 日程生成 prompt 注入**人设要素**：性格、身份定位（WxS 成员、宫益坂二年级）、背景、关系——这些 [source.md](../../config/persona/fengxiaomeng-v2/source.md) 已写好，现在却只传了个名字。
- 生成时**回读昨日日程**（`schedule_store.load(yesterday)`）+ 最近 N 条记忆卡，做有连续性的推演（"昨天社团比赛输了→今天开局有点低落"）。
- prompt 规则扩展：跨天因果 + 不与昨日重复主题。
- **为 Part C 预留**：注入人设时一并注入"团队 + 伙伴名单 + 每个伙伴一句话近况"，使日程天然带团队底色；伙伴/真人入记忆卡时即埋 `fiction`/`factual` 分层标签（见 [Part C §5.3](living-persona-partC-social-narrative.md)）。
- **改动面**：仅 [generator.py](../../plugins/schedule/generator.py) 的 prompt 构造 + 注入 persona/昨日；无新存储、无新基建。
- **验证**：连续 3 天日程主题不重复且有可读的因果链；人设要素可见地影响活动选择。

### L1.5 — Story Arc（剧情弧账本；承接长线波折）

> 2026-06-08 补充调研结论：酒馆 / AI Dungeon / NovelAI 的 Memory + Lorebook + Author's Note，以及 Twine / Ink / Yarn / ChoiceScript 的变量与分支实践，都说明长故事不能只靠“每天现编”。详见 [故事弧补充调研](living-persona-research-story-arc-2026-06-08.md)。

- 新增轻量 `StoryArc` 状态：`arc_id/title/scope/stage/goals/active_conflicts/variables/partner_states/open_threads/last_events/next_day_seed`。
- 生成时注入 active arc 摘要，替代“每天随机主题”为“主线内每日变奏”。例如“舞台剧比赛准备周”下，每天围绕排练、期末复习、伙伴状态、突发 setback 推进。
- **注入位置（借酒馆 WI Insertion Order）**：arc 摘要作为**近端高优先级块**注入（`ctx.add_block` 高 priority），不埋进早段 system——越靠 prompt 末端对生成影响越大（[研究 §1.1](living-persona-research-story-arc-2026-06-08.md)）。
- **变量即状态（借 Ink read-count / ChoiceScript stats）**：`variables`（`exam_pressure/rehearsal_progress/team_morale/risk_level/deadline_days_left`）是给生成器和 Dream 读的内部“统计面板”，不必另造状态机；阶段推进 = 变量随天数演化（[研究 §2.2/§2.4](living-persona-research-story-arc-2026-06-08.md)）。
- 生成后摘要写回：把当日日程的 `theme/day_narrative` 与关键 slot 摘成 `last_events`，更新 `next_day_seed`，为次日生成提供短上下文。
- 与 Part C 的衔接：`partner_states` 先只承载虚构伙伴（`fiction`），真人仍只允许共同经历/主观印象，不能虚构线下行为。
- **改动面**：先用 JSON 文件存 `storage/living_persona/story_arcs/*.json`；不急着建 SQLite；不改 Dream tool loop。
- **验证样例**：连续 7 天“舞台剧比赛 + 期末考试冲突 + 伙伴轻微受伤/临时缺席 + 舞台动作重规划”能形成可读剧情弧，且后续 2–3 天承接余波。

### L2 — 每日反思（Dream agent 升级为 reflection 层）

- Dream agent 夜间 consolidation 增加一类任务：**对"今天过得怎样"做反思**——读当天日程 slots + active `StoryArc` + 当天群聊经历（group_messages）+ 交互信号，综合出 1–3 条**经历洞察**存入记忆卡（如"今天被群友夸了新发型，心情不错"），并更新 arc 的 `last_events/open_threads/next_day_seed`。
- 复用现有 Dream tool loop（[dream/plugin.py:330](../../plugins/dream/plugin.py#L330)）与 memo card 写入，**不新建反思引擎**。
- **验证**：每日产出经历洞察卡；洞察可被 memo 检索召回。

### L3 — 反思反哺 + 事件重规划（闭环，故事开始涌现）

- L2 的经历洞察**喂回 L1 的次日日程生成**——形成 GA 式的 经历→反思→规划 闭环。
- 重大事件（被连续骚扰、收到特别消息、节日、伙伴轻微受伤/临时缺席、比赛/考试 deadline 临近）触发**日程内与剧情弧重规划**：当前/后续 slot 的 description/mood_hint 可被运行时事件覆盖，`StoryArc.stage/variables/open_threads` 同步改写（对接 [Part A](living-persona-partA-dialogue-climate.md) 的 tension）。
- **重规划用近端约束注入（借酒馆 Author's Note + narrative mediation）**：突发事件落为一条近端注入（如「伙伴受伤→原舞台动作不可用，后续约束站位/降难度」），约束接下来 2–3 天生成，**不重写整段历史**（[研究 §3.4](living-persona-research-story-arc-2026-06-08.md) 的 `[fresh wound→won't run away]` 用例同款思路）。
- **事件触发器用条件门 + once-only/cooldown（借 Ink / 酒馆 Timed Effects）**：重大 setback 为 `once-only`（每 arc 至多 1 次），日常推进为 `sticky`（可反复），无事件时走 `fallback` 缺省日常；触发后进入 cooldown，防同类事件刷屏（落实护栏 B6，见 [研究 §1.1/§2.2](living-persona-research-story-arc-2026-06-08.md)）。
- **验证**：可观测到跨天叙事弧（某天的事在后续日程里留下痕迹）。

> 与 [Part A：Dialogue Climate](living-persona-partA-dialogue-climate.md) 的关系：**互补两条线**。Part A = bot 当下"什么心情"（状态动力学）；本 part = bot 过着"谁的人生、记不记得、连不连贯"（叙事/规划）。L3 的事件重规划正好消费 Part A 的 tension/mood——两线在此交汇。

---

## 5. 风险登记（Part B 范围）

| # | 风险 | 等级 | 缓解 |
| --- | --- | --- | --- |
| B1 | L1 把人设全量塞进日程 prompt → token 膨胀 / 偏离 | 中 | 只注入人设要点（团队/身份/关系/性格关键词），非全文；灰度对比生成质量 |
| B2 | 跨天连续性导致错误累积（昨日幻觉被次日继承放大） | 中 | 回读昨日**摘要**而非全文；反思层做一致性校验（Dream 已有交叉验证能力） |
| B3 | 日程叙事与真实群聊语境冲突（bot 说"在上课"但正深夜聊天） | 中 | 日程是"底色"非"硬约束"；运行时以实际对话为准，L3 事件重规划兜底 |
| B4 | 抢占主线人力（当前焦点是角色包补齐 + pmubot） | 中 | 本 part 低优先级，L1 可作小批独立 PR |
| B5 | 长线故事只靠 LLM 续写 → 时间线/角色/世界规则自相矛盾 | **高** | L1.5 用外部 StoryArc 账本固定目标、阶段、变量和 open_threads；L2 反思时做一致性校验（Dream 已有交叉验证能力），参考长故事一致性研究（[研究 §3.5](living-persona-research-story-arc-2026-06-08.md) Lost in Stories / ConStory-Bench 的错误类别） |
| B6 | 为追求“波折”过度戏剧化，天天事故/苦情化 | 中 | 事件预算 = `once-only` + `cooldown`（借酒馆 Timed Effects / Ink once-only）：每个 arc 至多 1 个重大 setback，触发后进冷却；候选事件用 **Best Least Recently Viewed** 选择（借 Yarn saliency 默认策略，[研究 §2.3](living-persona-research-story-arc-2026-06-08.md)），优先轻微受伤/临时缺席/排练失误等低烈度事件，禁止连续灾难化 |

---

## 6. 排期建议（Part B 范围）

1. **L1 先行**：角色驱动 + 跨天连续，单 PR，灰度对比生成质量。改动小、收益直观。L1 即埋下伙伴/真人卡的 `fiction`/`factual` 分层（为 Part C 铺路）。
2. **L1.5 接上**：StoryArc 剧情弧账本，单 PR，先用 JSON 存 active arc；以“舞台剧比赛准备周 + 期末考试冲突 + 伙伴轻微受伤/临时缺席 + 舞台动作重规划”作为验收样例。
3. L1.5 稳定后 → **L2**（Dream 反思升级，同时更新 arc）→ **L3**（反哺闭环 + 事件重规划，对接 Part A）。
4. **C-MVP 伙伴状态卡（仅虚构伙伴 `fiction`）随 L1.5 并行落地**——L1.5 的 `partner_states` 依赖它供数，二者**同批交付**（见 [Part 0 §4](living-persona-part0-overview.md)、[Part C §6.5](living-persona-partC-social-narrative.md)）；真人入叙事（Part C 主体）仍后置，不在此批。
5. L1/L1.5 与 Part A 的 M1（tension）可并行，互不依赖。全程低优先级，不抢角色包/pmubot 主线。

---

## 7. 决策模板（Part B）

```text
Part B（单角色生活叙事 L1–L3）：
[ ] 批准 L1–L3 路径
[ ] 批准新增 L1.5 StoryArc（剧情弧账本），作为长线波折故事的中间层
[ ] L1 先行做小批 PR（角色驱动 + 跨天连续 + 伙伴/真人卡分层）
[ ] L1.5 验收样例采用：舞台剧比赛准备周 + 期末考试冲突 + 伙伴轻微受伤/临时缺席 + 舞台动作重规划
[ ] 调整 MVP 起点为：___
[ ] 认可 L1/L1.5 与 Part A M1 可并行、L3 与 Part A 交汇
[ ] 其他：___
```

---

## 附：核验命令留痕（D4）

```text
grep schedule in dream/memo/consolidator   → 0（日程孤岛，确认）
grep identity_name= generator               → 只传 name（角色解耦，确认）
ls storage/schedule/                         → 6-04~6-08 五份日程各自独立
grep memo recency/relevance                  → memo 三因子检索已有（GA memory stream 就绪）
grep Dream consolidation tool loop           → 反思层雏形已有（dream/plugin.py:330）
```
