# Living Persona — 故事弧补充调研：成熟项目与前沿研究

> 状态：**研究补充 · 2026-06-08**
> 关联：[Part 0：系列总纲](living-persona-part0-overview.md)、[Part B：Generative Life](living-persona-partB-generative-life.md)、[Part C：Social Narrative](living-persona-partC-social-narrative.md)
> 目标：回答“当前/未来是否能产生连续而波折的故事结构”，并为“连续一周舞台剧比赛 + 期末考试冲突 + 伙伴事故 + 舞台动作重规划”这类验收样例补充可落地机制。

---

## 0. 结论摘要

成熟项目与论文给出同一个方向：**长故事不能只靠 LLM 每天现编**。要产生连贯而波折的结构，需要把“剧情弧”拆成显式状态：

1. **静态锚点**：角色卡 / 人设 / scenario / 示例对话，决定“谁在故事里、基调是什么”。
2. **触发式背景**：World Info / Lorebook / Story Cards，按关键词或上下文激活“相关设定”。
3. **强引导层**：Author's Note / Plot Essentials / Memory，在靠近当前输出的位置注入“此刻故事方向”。
4. **状态变量与分支结构**：Twine / Ink / Yarn / ChoiceScript 共同证明，互动叙事依赖变量、条件、分支、storylets，而不是纯文本续写。
5. **剧情管理器**：drama management / experience management 论文把“故事目标、玩家/用户行为、重规划”作为独立控制层；这正对应 omubot 的 L3。
6. **一致性约束**：长故事生成研究反复指出 LLM 会在事实、时间线、角色性格、世界规则上自相矛盾；必须有外部账本/检查器兜底。

因此 Part B 需要新增 **L1.5：Story Arc（剧情弧账本）**，位于 L1（昨日影响今天）和 L2（每日反思）之间；Part C 需要新增 **C-MVP：伙伴状态卡**，先只覆盖虚构伙伴，不急着纳入真人。

---

## 1. 成熟 AI 角色扮演工程实践

### 1.1 SillyTavern / 酒馆：角色卡 + 世界书 + 作者注入

SillyTavern 官方文档体系把长期角色扮演拆成多个 prompt 层：

- [Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/) / [Characters](https://docs.sillytavern.app/usage/characters/)：角色 description、personality、scenario、first message、alternate greetings / swipes、example messages、Character's Note 等字段。
- [World Info / Lorebooks](https://docs.sillytavern.app/usage/core-concepts/worldinfo/)：以 lorebook / memory book 形式按触发条件把世界设定插入上下文。
- [Author's Note](https://docs.sillytavern.app/usage/core-concepts/authors-note/)：靠近当前生成位置插入短而强的作者意图，可用于临时方向、风格、情绪、剧情偏置。
- [Group Chats](https://docs.sillytavern.app/usage/core-concepts/groupchats/)：群聊里存在多角色卡如何合并/切换、角色发言顺序、scenario override 等机制。

**World Info 机制细节（anysearch `extract` 全文核实，2026-06-08）**——这几条直接可借给 omubot Story Arc：

- **Insertion Order + Insertion Position**：条目带数值 order，order 越大越靠近 context 末尾、对输出影响越大；位置可选 Before/After Char Defs、Before/After Example Messages、Top/Bottom of Author's Note、`@ D`（指定深度，可作 system/user/assistant 角色）。→ 印证“当前剧情方向”应作为**近端高优先级块**注入，而非埋进早段 system。
- **Timed Effects（sticky / cooldown / delay）**：条目可“激活后保持 N 条消息（sticky）”“激活后 N 条内不可再触发（cooldown）”“至少 N 条消息后才可触发（delay）”。→ 这是 omubot **事件预算护栏（B6：每个 arc 最多 1 个重大 setback、同类事件不刷屏）** 的现成范式：用 cooldown 防止“伙伴天天出事”。
- **Recursive activation / Inclusion Group / Group Scoring**：条目能用关键词互相触发；同组多个命中时按权重/打分只选一个。→ 对应 storylet 触发模型与“同一情境只选一个剧情片段”。
- **Probability（Trigger %）**：命中后仍按百分比决定是否插入，用于随机事件。→ 对应“伙伴意外”的概率注入，可控且非每次必发。
- **Vector Storage / 关键词双模匹配**：除关键词外可用向量相似度激活。→ omubot 的 memo 检索已有三因子，可直接承接这套“触发式背景”。

**对 omubot 的启示**：

| 酒馆层 | Omubot 对应 | 补齐点 |
| --- | --- | --- |
| Character Card | Persona Runtime `source.md` | 已有，但 schedule 只拿 name，未注入身份/关系要点 |
| Scenario | Story Arc 当前剧情弧 | 当前缺；应新增本周主线/冲突/目标 |
| Example Messages | persona freeze / style examples | 已在表达层使用，但未用于日程叙事 |
| World Info / Lorebook | memo/entity cards | 有 memo 卡，但日程生成未检索、未按剧情触发 |
| Author's Note | prompt 近端强引导 block | 当前只有“当前时间/心情”；应新增“当前剧情方向” |
| Group Chat cards | Part C 伙伴状态卡 | 伙伴是静态人设，缺动态状态 |

也就是说，酒馆成熟实践不是“让模型自由发挥”，而是**多层提示 + 触发式背景 + 近端作者注**。这支持 Part B 增加 Story Arc block，而不是只把昨日全文塞进 prompt。

### 1.2 AI Dungeon / NovelAI：Memory、Author's Note、Story Cards / Lorebook

AI Dungeon 官方说明把上下文拆为 story text、AI Instructions、Plot Essentials（原 Memory）、Author's Note、相关 [Story Cards](https://help.aidungeon.com/faq/story-cards)；其 [Memory System](https://help.aidungeon.com/faq/the-memory-system) 文档明确这些组件共同构造模型输入。[Author's Note](https://help.aidungeon.com/faq/what-is-the-authors-note) 是靠近末端的语气/题材/即时方向提示。

NovelAI 官方 [Lorebook](https://docs.novelai.net/en/text/lorebook) 与 [Story Settings](https://docs.novelai.net/en/text/editor/storysettings) 同样提供 Memory、Author's Note、Lorebook/activation keys 等概念。

**对 omubot 的启示**：

- 长叙事系统会区分“长期事实”（Memory / Plot Essentials）与“当前方向”（Author's Note）。
- 设定不应全量常驻；应通过 Story Cards / Lorebook 按触发条件进入上下文。
- Omubot 的 `memo` 可以承接长期事实，但需要新增 `story_arc` 作为“当前方向 + 待解决冲突”的近端块。

---

## 2. 成熟故事树 / 互动小说工程实践

### 2.1 Twine：passages、links、story formats、variables

Twine 官方 [Basic Concepts](https://twinery.org/reference/en/getting-started/basic-concepts.html) 把 story 拆为 passages，story formats 负责显示、交互、变量、条件等功能；[Linking Passages](https://twinery.org/reference/en/editing-stories/linking-passages.html) 给出 passage link 基础；Twine Cookbook 的 [Variables](https://twinery.org/cookbook/terms/terms_variables.html) 说明不同 story format 下变量承担状态管理。

**启示**：故事不是一串自由文本，而是“节点 + 边 + 变量”。Omubot 不需要做完整 Twine，但需要一个轻量剧情弧状态：当前节点/阶段、可选后续、变量（考试压力、排练进度、伙伴状态）。

### 2.2 Ink：knots / stitches / choices / variables / diverts

Ink 官方 [Writing with ink](https://github.com/inkle/ink/blob/master/Documentation/WritingWithInk.md)（anysearch `extract` 全文核实）覆盖 choices、knots、stitches、diverts、变量与逻辑、tunnels/threads、lists 等叙事脚本结构；[Running your ink](https://github.com/inkle/ink/blob/master/Documentation/RunningYourInk.md) 说明运行时可跳转 knot/stitch、设置和观察变量。全文里两个机制对 omubot 直接有用：

- **knot/stitch 的 read count 即状态**：Ink 里“某段内容被看过几次”本身就是一个整数变量（`{seen_clue > 3}`），条件选项基于它开关。→ omubot 的 StoryArc 阶段不必另造状态机，可直接用“某剧情节点被推进过几次”当变量。
- **once-only / sticky / fallback 选项 + 条件门**：`*` 一次性、`+` 可重复、`->` fallback 缺省，配合 `{ 条件 }` 决定某分支是否出现。→ 对应“伙伴事故只触发一次（once-only）”“日常排练可反复（sticky）”“无特殊事件时走缺省日常（fallback）”。

**启示**：可落地为 omubot 的“剧情弧阶段机”：

```text
arc.stage = announced | preparation | conflict | setback | replanning | climax | aftermath
arc.variables = exam_pressure, rehearsal_progress, partner_availability, team_morale
```

这比“每天随机主题”更接近可控长故事。

### 2.3 Yarn Spinner：storylets + saliency

Yarn Spinner 官方 [Storylets and Saliency Primer](https://docs.yarnspinner.dev/3.1/write-yarn-scripts/advanced-scripting/storylets-and-saliency-a-primer)（anysearch `extract` 全文核实）和 [Saliency](https://docs.yarnspinner.dev/write-yarn-scripts/advanced-scripting/saliency) 把可触发叙事片段（storylets）与 `when:` 条件、saliency strategy 结合；Unity sample [Basic Storylets and Saliency](https://docs.yarnspinner.dev/yarn-spinner-for-unity/samples/storylets-and-saliency/basics-storylets-and-saliency) 展示 `$day`、`$time` 等变量与多条件触发。全文核实补充三点：

- **叙事演化谱系**：线性 → 分支 → storylet（自由流动），后者用 saliency 把碎片动态串起来。→ omubot 当前是“每日独立线性短篇”，目标是升级到 storylet + saliency。
- **directed saliency = Drama Management**：官方明确把“系统朝叙事目标选片段”（以 Façade 最大化戏剧张力为例）列为一种 saliency。→ **这把工程实践（storylets）和前沿论文（drama management，见 §3.2）在同一框架里接上了**——omubot 的“每日选哪个剧情片段”本质就是一个轻量 drama manager。
- **Best Least Recently Viewed 策略**：官方推荐默认策略，在“最相关”和“防重复”之间平衡。→ 直接对应护栏 B6（防止同类事件刷屏），是“事件预算”的现成算法。

**启示**：omubot 的随机日程应升级为“候选 storylet 选择”：

```text
when arc=stage_play_week and exam_pressure=high and partner_injured=true
→ 选择“改舞台动作 + 复习压缩 + 团队安慰”日程片段
```

这能解释为什么单纯 prompt 不能稳定产生“同伴受伤后更换动作”：缺少条件触发器与状态变量。

### 2.4 ChoiceScript：stats / variables

ChoiceScript 官方 [Introduction](https://www.choiceofgames.com/make-your-own-games/choicescript-intro/) 明确把变量称为 stats，并用 `*create`、`*set`、条件检查驱动叙事；[Stats Screen](https://www.choiceofgames.com/make-your-own-games/customizing-the-choicescript-stats-screen/) 展示如何把变量显式呈现。

**启示**：对 bot 而言，story arc 的变量不是给玩家看的 UI，而是给生成器和 Dream 看的内部“统计面板”：

- `exam_pressure`
- `rehearsal_progress`
- `team_morale`
- `partner_availability`
- `risk_level`
- `deadline_days_left`

---

## 3. 前沿与经典研究

### 3.1 Generative Agents：memory + reflection + planning

Stanford [Generative Agents](https://arxiv.org/abs/2304.03442) 提出 believable agents 的三件套：memory、reflection、planning。该论文的关键不是“更会写”，而是让 agent 有记忆流、能反思、能规划；这已经写入 [Part B](living-persona-partB-generative-life.md)。

**补充启示**：对 omubot 来说，L1 只做到“昨天影响今天”还不够；“一周舞台剧比赛”需要一个可被 reflection 更新、可被 planning 消费的 **arc memory**。

### 3.2 Drama Management / Experience Management：故事目标与重规划控制层

Roberts & Isbell 的 [A Survey and Qualitative Analysis of Recent Advances in Drama Management](https://www.cs.uky.edu/~sgware/reading/papers/roberts2008survey.pdf) 把 drama manager 定位为跟踪叙事进度、协调对象/agent 以达成叙事或训练目标的控制层。Thue 等的 [Interactive Storytelling: A Player Modelling Approach](https://cdn.aaai.org/ojs/18780/18780-52-22477-1-10-20210929.pdf) / PaSSAGE 用玩家模型动态选择故事内容。

**补充启示**：omubot 也需要一个极轻量 drama manager：不是游戏 AI，而是每天生成日程前评估：

```text
当前故事目标是什么？
用户/群聊最近是否改变了方向？
今天应该推进、加压、缓和、还是收束？
是否需要重规划？
```

这就是 Part B-L3 的“事件重规划”，但应在 L1.5 开始就保存目标与冲突。

### 3.3 Narrative Planning：平衡 plot coherence 与 character believability

Riedl & Young 的 [Narrative Planning: Balancing Plot and Character](https://arxiv.org/abs/1401.3841) 将故事生成视为 planning 问题，强调 plot coherence 与角色意图/可信度的平衡；2024 综述 [The Story So Far on Narrative Planning](https://ojs.aaai.org/index.php/ICAPS/article/view/31509) 继续把 narrative planning 定义为用自动规划构造、传达、理解故事。

**补充启示**：舞台剧例子里，“同伴事故 → 换动作”是 plot coherence；“受伤同伴仍有自己的心情、其他伙伴的反应不同”是 character believability。Part B 管前者，Part C 管后者，二者必须分工但联动。

### 3.4 Mimesis / Narrative Mediation：用户行为与异常事件的处理

Riedl, Saretto & Young 的 [Managing Interaction Between Users and Agents in a Multi-agent Storytelling Environment](https://sites.cc.gatech.edu/fac/riedl/pubs/riedl-young-aamas03.pdf) 提出 narrative mediation，用于检测并响应用户未预期行为；后续 [From Linear Story Generation to Branching Story Graphs](https://ojs.aaai.org/index.php/AIIDE/article/view/18725) 把 narrative mediation 与 branching story graphs 联系起来。

**补充启示**：群聊用户的随机发言、poke、reaction 不该直接改写全部剧情，但可以作为“外部扰动”进入 drama manager：

- 轻扰动：只影响 Dialogue Climate。
- 中扰动：进入当天 reflection。
- 重扰动：改变 story arc 的 conflict / plan。

**工程印证**：酒馆 Author's Note 官方用例里恰好有 `[{{user}} has a fresh wound to his leg, so won't be able to run away.]`（anysearch `extract` 全文核实）——即“受伤→后续行为受约束”的临时剧情偏置，注入在近末端、影响接下来的生成。这正是 narrative mediation 思想的轻量工程版：omubot 的“伙伴受伤→舞台动作受限”可落为一条近端 Author's Note 式注入，约束后续 2–3 天日程，而不必重写整段历史。

### 3.5 长故事一致性：LLM 自身不可靠

Microsoft Research 的 [Lost in Stories: Consistency Bugs in Long Story Generation by LLMs](https://www.microsoft.com/en-us/research/publication/lost-in-stories-consistency-bugs-in-long-story-generation-by-llms/) 与 [arXiv 2603.05890](https://arxiv.org/abs/2603.05890) 指出，LLM 可生成长文本但会在事实细节、时间逻辑、角色性格、世界规则等方面出现一致性 bug；其 ConStory-Bench 项目页还将长故事一致性错误细分为多个类别。

**补充启示**：omubot 不能把“舞台剧比赛周”只写进 prompt，然后期待模型记住。必须把事实、时间线、角色状态、世界规则拆成账本字段，并在每日生成后做一致性检查。

---

## 4. 对“舞台剧比赛 + 期末考试 + 伙伴事故”样例的机制拆解

该样例不是普通日程，而是一个完整 story arc：

```text
Arc: 舞台剧比赛准备周
长期目标：一周后参加舞台剧比赛
并行约束：期末考试临近，复习时间压缩排练时间
伙伴状态：伙伴不是工具人，各自有压力/心情/可用性
突发事件：某伙伴轻微交通事故/受伤，原动作不可行
重规划：更换舞台动作，调整站位与排练节奏
余波：团队士气、bot 心情、后续比赛表现受影响
```

当前日程系统只有单日 `theme` / `day_narrative` / `slots`，无法表达这些跨天变量。建议新增：

```json
{
  "arc_id": "stage_play_competition_2026_w23",
  "title": "舞台剧比赛准备周",
  "scope": "fiction",
  "starts_on": "2026-06-08",
  "ends_on": "2026-06-14",
  "stage": "preparation",
  "goals": ["完成舞台剧比赛准备", "兼顾期末复习"],
  "active_conflicts": ["排练时间不足", "考试压力升高"],
  "variables": {
    "deadline_days_left": 6,
    "exam_pressure": 0.7,
    "rehearsal_progress": 0.35,
    "team_morale": 0.6,
    "risk_level": 0.4
  },
  "partner_states": {
    "天马司": {"mood": "亢奋但压力大", "availability": "normal"},
    "草薙宁宁": {"mood": "担心动作完成度", "availability": "normal"},
    "神代类": {"mood": "想改机关但时间不够", "availability": "normal"},
    "朝比奈真冬": {"mood": "低能量", "availability": "limited"}
  },
  "open_threads": ["是否降低动作难度", "是否周六追加排练"],
  "last_events": [],
  "next_day_seed": "在复习和排练之间做取舍"
}
```

---

## 5. 对 Part B 的补充：新增 L1.5 Story Arc

原 Part B 的 L1–L3 方向保留，但应补一层：

| 层级 | 原定位 | 补充后定位 |
| --- | --- | --- |
| L1 | 角色驱动 + 昨日影响今天 | 保留，解决“今天不是随机” |
| **L1.5** | —— | **Story Arc 剧情弧账本**：解决“一周主线是什么” |
| L2 | 每日反思 | 从 daily reflection 升级为“更新 arc 变量/开放线程” |
| L3 | 反思反哺 + 事件重规划 | 以 arc 为对象做 replan，不只是改当天 slot |

### L1.5 最小实现建议

- 存储：先用 `storage/living_persona/story_arcs/*.json`，不急着建 SQLite。
- 字段：`arc_id/title/scope/stage/goals/conflicts/variables/partner_states/open_threads/last_events/next_day_seed`。
- 生成时注入：只注入当前 active arc 的摘要，不注入全文历史。
- 生成后更新：把当日日程的 `theme/day_narrative` 和关键 slot 摘成 `last_events`，更新 `next_day_seed`。
- 验证：连续 7 天生成中能看到同一 arc 的阶段推进、冲突变化、伙伴状态变化和余波。

---

## 6. 对 Part C 的补充：C-MVP 伙伴状态卡

Part C 不应等完整社会化叙事才开始；为了支持 Part B-L1.5，先做虚构伙伴状态卡：

```json
{
  "entity_id": "tenma_tsukasa",
  "kind": "fiction",
  "pinned_profile": "W×S 成员，外向、自信、舞台中心感强",
  "current_state": "为比赛兴奋但压力大",
  "mood": {"valence": 0.3, "tension": 0.6},
  "availability": "normal",
  "recent_events": ["主动提出加练"],
  "constraints": ["不能每天都成为事故源", "保持角色可信"]
}
```

真人实体仍维持 Part C 红线：只承载共同经历/主观印象，不生成线下遭遇。

---

## 7. 风险与护栏

| 风险 | 来源 | 护栏 |
| --- | --- | --- |
| LLM 长故事自相矛盾 | Lost in Stories / ConStory-Bench | 外部 arc 账本 + 每日一致性检查 |
| 过度戏剧化，天天事故 | drama manager 若只追求波折 | 事件预算：每 arc 最多 1 个重大 setback，优先“轻伤/扭伤/临时缺席”等低烈度事件 |
| 伙伴变工具人 | Part C 当前缺口 | 伙伴状态卡必须有 mood/availability/recent_events，不只服务 bot |
| 真人 fabrication | Part C 红线 | 真人 `kind=factual`，禁止线下行为脑补，私聊不进群叙事 |
| prompt 膨胀 | 酒馆/NovelAI 都靠触发式插入 | active arc 摘要 + 触发式 memo，不全量灌入 |
| 随机主题冲掉主线 | 当前 generator 规则 | active arc 存在时，“随机主题”降级为“主线内每日变奏” |

---

## 8. 建议验收样例

以用户提出的样例作为 Part B-L1.5/L3 + Part C-MVP 的验收：

1. 创建 `stage_play_competition_week` arc。
2. 连续生成 7 天日程。
3. 验收点：
   - 每天都承接“舞台剧比赛准备周”，但不是重复同一句。
   - 期末考试压力与排练进度形成真实取舍。
   - 至少 2 名伙伴状态发生变化，且不是工具人。
   - 若触发“伙伴轻微受伤/事故”，后续 2–3 天能看到舞台动作重规划与团队情绪余波。
   - bot 被问“这周怎么了”时能自然概括，而不是只说当前 slot。
4. 失败判定：
   - 每天随机新主题；
   - 伙伴事故次日消失；
   - 伙伴只作为推动剧情的道具；
   - 生成真人线下行为；
   - 因事故过度苦情化、频繁化。

---

## 9. 本调研使用的主要来源

成熟工程实践：

- SillyTavern：[Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)、[Characters](https://docs.sillytavern.app/usage/characters/)、[World Info](https://docs.sillytavern.app/usage/core-concepts/worldinfo/)、[Author's Note](https://docs.sillytavern.app/usage/core-concepts/authors-note/)、[Group Chats](https://docs.sillytavern.app/usage/core-concepts/groupchats/)
- AI Dungeon：[Memory System](https://help.aidungeon.com/faq/the-memory-system)、[Story Cards](https://help.aidungeon.com/faq/story-cards)、[Author's Note](https://help.aidungeon.com/faq/what-is-the-authors-note)、[Plot Components](https://help.aidungeon.com/faq/plot-components)
- NovelAI：[Lorebook](https://docs.novelai.net/en/text/lorebook)、[Story Settings](https://docs.novelai.net/en/text/editor/storysettings)
- Twine：[Basic Concepts](https://twinery.org/reference/en/getting-started/basic-concepts.html)、[Linking Passages](https://twinery.org/reference/en/editing-stories/linking-passages.html)、[Variables](https://twinery.org/cookbook/terms/terms_variables.html)
- Ink：[Writing with ink](https://github.com/inkle/ink/blob/master/Documentation/WritingWithInk.md)、[Running your ink](https://github.com/inkle/ink/blob/master/Documentation/RunningYourInk.md)
- Yarn Spinner：[Storylets and Saliency Primer](https://docs.yarnspinner.dev/3.1/write-yarn-scripts/advanced-scripting/storylets-and-saliency-a-primer)、[Saliency](https://docs.yarnspinner.dev/write-yarn-scripts/advanced-scripting/saliency)、[Basic Storylets sample](https://docs.yarnspinner.dev/yarn-spinner-for-unity/samples/storylets-and-saliency/basics-storylets-and-saliency)
- ChoiceScript：[Introduction](https://www.choiceofgames.com/make-your-own-games/choicescript-intro/)、[Stats Screen](https://www.choiceofgames.com/make-your-own-games/customizing-the-choicescript-stats-screen/)

研究与论文：

- Park et al., [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- Roberts & Isbell, [A Survey and Qualitative Analysis of Recent Advances in Drama Management](https://www.cs.uky.edu/~sgware/reading/papers/roberts2008survey.pdf)
- Thue et al., [Interactive Storytelling: A Player Modelling Approach](https://cdn.aaai.org/ojs/18780/18780-52-22477-1-10-20210929.pdf)
- Riedl & Young, [Narrative Planning: Balancing Plot and Character](https://arxiv.org/abs/1401.3841)
- Cardona-Rivera et al., [The Story So Far on Narrative Planning](https://ojs.aaai.org/index.php/ICAPS/article/view/31509)
- Riedl, Saretto & Young, [Managing Interaction Between Users and Agents in a Multi-agent Storytelling Environment](https://sites.cc.gatech.edu/fac/riedl/pubs/riedl-young-aamas03.pdf)
- Riedl & Young, [From Linear Story Generation to Branching Story Graphs](https://ojs.aaai.org/index.php/AIIDE/article/view/18725)
- Microsoft Research, [Lost in Stories: Consistency Bugs in Long Story Generation by LLMs](https://www.microsoft.com/en-us/research/publication/lost-in-stories-consistency-bugs-in-long-story-generation-by-llms/) / [arXiv 2603.05890](https://arxiv.org/abs/2603.05890)

> 注：本轮自动 deep-research workflow 抓到 23 个 URL 但未能抽取可靠 claim，因此本文最终采用人工核查的官方文档/论文入口。SillyTavern（World Info / Character Design / Author's Note / Group Chats 四页）、Ink（Writing with ink）、Yarn Spinner（Storylets and Saliency Primer）的功能细节**均已用 anysearch `extract` 抓取官方全文核实**（§1.1 的 Insertion Order / Timed Effects / Recursion / Probability、§2.2 的 read-count/once-only/sticky/fallback、§2.3 的 directed-saliency=drama-management / Best-Least-Recently-Viewed 等均出自全文）；Generative Agents 等论文经 anysearch `academic.search` 与 WebSearch 交叉核对。Twine / AI Dungeon / NovelAI / ChoiceScript 仍为 WebSearch 官方条目级核实，实现前如需精确字段建议同样用 `extract` 抓全文。

---

## 10. L1.5 落地清单（D3 旧→新；落地前置）

> 落地前于 2026-06-08 复核 Part B 代码证据仍准确：`identity_name` 只传名字（[generator.py:134](../../plugins/schedule/generator.py#L134)、[chat/plugin.py:1101](../../plugins/chat/plugin.py#L1101)）、日程在 dream/memo 中零引用（孤岛成立）、Dream tool loop 在 [dream/plugin.py:331](../../plugins/dream/plugin.py#L331)。L1.5 是在此基础上的纯增量。

| # | 旧（现状） | 新（L1.5） | 改动文件 | 风险 |
| --- | --- | --- | --- | --- |
| 1 | 无 StoryArc 存储 | `StoryArc` dataclass + JSON 读写 `storage/living_persona/story_arcs/*.json` | 新增 `plugins/schedule/story_arc.py`（或 `services/living_persona/`） | 低（新文件） |
| 2 | `_generate` 只喂 name+日期+日历 | 注入 active arc 摘要（近端高优先 block）+ 人设要点（L1 已做则复用） | [generator.py:131-166](../../plugins/schedule/generator.py#L131) | 中（改 prompt，灰度对比） |
| 3 | 生成后只 `store.save(schedule)` | 生成后把 `theme/day_narrative`+关键 slot 摘成 `last_events`、更新 `next_day_seed`/`variables` 回写 arc | generator.py 尾部 | 低（附加写） |
| 4 | 无事件预算 | setback `once-only`+`cooldown`、日常 `sticky`、缺省 `fallback`；候选用 Best-LRV 选 | story_arc.py | 中（调参） |
| 5 | 无开关 | `schedule.story_arc_enabled` flag，默认关，灰度 | [plugin.py:19-27](../../plugins/schedule/plugin.py#L19) ScheduleConfig | 低 |
| 6 | 无 arc 测试 | 单测：arc 读写/摘要注入/once-only 触发/cooldown 不重复触发/无 arc 时回退原行为 | `tests/test_story_arc.py` | 低 |

**落地顺序**：先 #1+#6（存储+测试，不接生成）→ #5（开关）→ #2+#3（接生成，灰度默认关）→ #4（事件预算调参）。每步 `uv run pytest` + `ruff` + `pyright` 通过再进下一步。

**回滚**：开关默认关 = 零行为变更；按文件 `git checkout` 即可全退；JSON 存储独立目录，删除不影响既有 schedule。

**不在 L1.5 范围**：Dream 反思更新 arc（L2）、运行时聊天内事件重规划（L3）、真人状态卡（C 主体）、SQLite 迁移。
