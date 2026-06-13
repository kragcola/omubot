# Living Persona — Part C：Social Narrative（社会化叙事）

> 系列：[Living Persona / 角色生命化](living-persona-part0-overview.md)（Part 0 总纲）
> 状态：**需独立调研 · 2026-06-08**（形态已定，调研聚焦机制与红线）
> 关联：[Part A：Dialogue Climate](living-persona-partA-dialogue-climate.md)（关系温度复用）、[Part B：Generative Life](living-persona-partB-generative-life.md)（**本 part 依赖 B 先跑通**）
> 在系列中的角色：把伙伴与相关真人作为"活的存在"纳入 bot 的世界——单 bot 持有一个"活的社会世界模型"。
> 原则：调研先行；形态已明确（单 bot 内建模，非多 bot）；伦理红线（真人不可虚构）从 Part B-L1 即埋下；2026-06-08 [故事弧补充调研](living-persona-research-story-arc-2026-06-08.md) 后，新增 **C-MVP 伙伴状态卡** 先支撑 Part B-L1.5 的长线剧情弧。

---

## 0. 一句话

让 bot 的故事不只关乎自己：**伙伴是有自己生活、会遇意外、会心情不好的动态角色（非工具人），群内真人也能进入 bot 的故事线**。这不是多 bot society，而是**单 bot 持有一份"活的社会世界模型"**——伙伴和真人都是其中有独立状态、会变化的存在。

---

## 1. 用户原话（2026-06-08 二次澄清，纠正了形态）

> **不是多 bot。** 是单 bot 在故事创造中，把伙伴角色当成**有自己生活的存在**——伙伴也会遇到意外、心情不好，是**非固定的动态设定**，而不是召之即来的工具人。同时，**群内真人（不限于 bot）也纳入 bot 的故事线**。

这把命题从"多智能体 society"纠正为**更轻、更可行**的"单 bot 活社会世界模型"。

---

## 2. 现实诊断：伙伴是"静态工具人"，真人尚未入叙事

| 事实 | 证据 | 含义 |
|---|---|---|
| 凤笑梦人设写明团队 + 四伙伴 | [source.md:69](../../config/persona/fengxiaomeng-v2/source.md#L69)（天马司/草薙宁宁/神代类/朝比奈真冬）；[SKILL.md:69-81](../../config/soul/SKILL.md#L69) 对每人有专属语气 | 伙伴设定**已存在**，但是**静态文本**——永远在线、永远那个样子 |
| 情绪层已有零星团队意识 | [mood.py:496](../../plugins/schedule/mood.py#L496) "作为 W×S 的好伙伴，生日祝福" | 团队关系已渗入行为，但是 hardcode 片段，无状态 |
| 伙伴在角色识别包里 | `project_sekai.charpack` 含 `tenma_saki` 等 | bot 能**认出**伙伴的图，但伙伴没有"今天过得怎样" |
| 真人画像散落各处 | affection familiarity（[engine.py](../../plugins/affection/engine.py)）、memo 卡（per-user scope） | 真人有"好感/记忆"，但**不是 bot 故事里的角色** |
| 当前运行 | 单角色 bot（qq-bot），无第二角色实例 | 排除多 bot 路线，确认是**单 bot 内建模** |

**核心缺口**：伙伴是"永远站在原地等被提及"的纸板，真人是"被记住但不出现在故事里"的旁观者。要让他们"活"，需要给每个被纳入叙事的存在一份**轻量的、会随时间/事件变化的状态画像**——这正是单 bot 世界模型要解决的。

---

## 3. 前沿支撑

三个直接可借的锚点：

- **他者状态建模 / 心智理论**：让 bot 维护"伙伴今天的状态"本质是 belief-state tracking。前沿警示：**LLM 在稳健的信念状态追踪上仍弱于人类**（FANToM 基准，[LessWrong 复测](https://www.lesswrong.com/posts/L7pBM9RCnsoJJenkJ/frontier-models-still-lag-behind-humans-at-robust-belief)）——所以伙伴状态**不能纯靠 LLM 即兴脑补**，要有外置的轻量状态存储兜底。
- **bounded agency（有界自主）世界模型**：NPC 通过"pinned profiles + 状态账本 + 可重放历史"行动，实现**可控涌现**而非放飞（[Real-Scale World Simulation](https://huggingface.co/posts/kanaria007/141831597390747)）。伙伴状态应是这种"钉住的档案 + 可演化的状态"，不是每次重新发明。
- **角色注入框架**：persona 特征注入模拟角色（[SimsChat, arxiv 2406.17962](https://arxiv.org/html/2406.17962v4)）——伙伴的"专属语气"omubot 已有（SKILL.md），缺的是给它叠加动态状态。

---

## 4. 需调研的关键问题

1. **伙伴状态的最小表示**：一个伙伴的"活"需要几个维度？（近况一句话 + mood + 最近事件 + 与 bot 的当前关系温度）——参考 Part A 的轻量状态，避免给每个伙伴造一套完整 mood 引擎。
2. **伙伴状态从哪来**：纯 LLM 生成（风险高，见 §3）vs Dream 夜间为伙伴"推演近况"并存档 vs 事件触发（bot 提到某伙伴时才惰性生成其当前状态）。倾向**惰性生成 + 存档复用**。
3. **真人入叙事的边界（伦理+真实感双重约束）**：把群友编入 bot 故事，有 **fabrication/隐私风险**——bot 不能编造真人"做了什么"当成事实，也不能把私聊内容泄进群叙事。前沿明确把"虚构真人行为""数据最小化"列为 agentic AI 核心风险（[IAPP](https://iapp.org/news/a/managing-agents-in-the-age-of-agentic-ai-the-critical-role-of-purpose-and-data-minimization)、[CMU Tepper](https://tepperspectives.cmu.edu/all-articles/the-ethical-challenges-of-ai-agents/)）。
4. **想象 vs 事实的标注**：伙伴的"今天遇到意外"是 bot 的**想象世界**（伙伴是虚构角色，可自由演绎）；真人的状态是**有限事实 + 主观印象**（不可虚构）。两类存在必须在世界模型里**分层、打不同标签**，避免 bot 把脑补的伙伴剧情和真人事实混为一谈。
5. **与 Part A 的复用**：伙伴/关系的"温度"可直接复用 [Part A](living-persona-partA-dialogue-climate.md) 的 valence/familiarity 维度，不另造。

---

## 5. 伦理红线（不可妥协）

> **真人只能以"bot 对其的主观印象 / 共同经历回忆"形式入叙事，不能虚构其线下行为。** 私聊内容不进群叙事。

数据层落实：

- 伙伴卡标 `fiction`——虚构角色，可自由演绎其遭遇与心情。
- 真人卡标 `factual`——仅承载主观印象与共同经历，不可生成"该真人线下做了什么"。
- 两类**分层不混**，bot 不得把脑补的伙伴剧情当成真人事实，反之亦然。
- 这是 [Part B-L1](living-persona-partB-generative-life.md) 就要埋下的数据基础，不能等到本 part 才补。

---

## 6. 定位与前置

- **依赖 Part B 先跑通**：单角色叙事自洽是 C **主体**（真人入叙事 + 完整社会世界模型）的前提——bot 自己的故事都长不连贯，谈不上把伙伴/真人编进来。**唯一例外**是 §6.5 的 C-MVP 伙伴状态卡（仅虚构伙伴），它随 B-L1.5 并行、为剧情弧供数，不等 B 全部跑通。
- **形态已定**：单 bot 世界模型，非多 bot。调研聚焦"伙伴/真人状态的最小表示 + 想象/事实分层 + 真人入叙事的伦理红线"，不再讨论多实例形态。
- **复用现有基建**：记忆卡 scope 已支持多实体（per-user/per-entity），伙伴与真人直接落为"实体卡"，状态画像复用这套存储、不新建表；关系温度复用 Part A。

---

## 6.5 C-MVP：伙伴状态卡（先支撑 Part B-L1.5）

> 来源：[故事弧补充调研](living-persona-research-story-arc-2026-06-08.md) §6。结论是 Part C 不必等完整社会化叙事才动；为让 Part B-L1.5 的“舞台剧比赛 + 伙伴事故”样例成立，先做**虚构伙伴**的最小状态卡，真人入叙事仍后置。

最小表示（只覆盖虚构伙伴，`kind=fiction`）：

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

设计要点：

- `pinned_profile` 钉住人设（bounded agency），`current_state/mood/availability/recent_events` 是会随事件演化的动态部分。
- 伙伴状态**惰性生成 + 存档复用**（§4 第 2 点）：bot 提到某伙伴或剧情弧需要时才生成，写回后复用，不每次重编。
- 直接供 Part B-L1.5 的 `StoryArc.partner_states` 读取；伙伴“轻微受伤/临时缺席”改写 `availability` 与 `recent_events`，触发舞台动作重规划。
- 真人**不进** C-MVP：真人卡仍是 `factual`，等 Part B/C 主体稳定后再按红线接入。

---

## 7. 风险登记（Part C 范围）

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| C1 | 伙伴状态纯靠 LLM 即兴脑补 → 前后矛盾、失真 | **高** | 外置轻量状态画像兜底（FANToM 警示 LLM 信念追踪弱）；伙伴状态惰性生成后**存档复用**，不每次重编 |
| C2 | 把真人编入叙事 → fabrication / 隐私越界 | **高** | §5 红线：真人仅主观印象/共同经历，不虚构线下行为；`factual`/`fiction` 分层；私聊不进群叙事 |
| C3 | 想象与事实混淆（脑补伙伴剧情当真人事实） | 高 | 世界模型分层打标签，prompt 层显式区分两类存在 |
| C4 | 可控涌现失守（伙伴/世界剧情放飞跑偏） | 中 | bounded agency：钉住档案 + 可演化状态 + 可重放，不放任自由生成 |

---

## 8. 决策模板（Part C）

```text
Part C（社会化叙事，形态已定：单 bot 世界模型）：
[ ] 同意独立调研先行、依赖 Part B 跑通
[ ] 认可红线：真人仅以"主观印象/共同经历"入叙事，不虚构其线下行为
[ ] 伙伴状态来源倾向（供调研聚焦）：惰性生成+存档 / Dream 夜间推演 / 待调研定
[ ] 认可关系温度复用 Part A 的 valence/familiarity
[ ] 其他：___
```

---

## 附：核验命令留痕（D4）

```text
docker ps                              → qq-bot + pmubot + napcat（仅 1 角色 bot，确认单 bot 形态）
grep 伙伴名 source.md/SKILL.md          → 静态人设设定（非运行 agent）
grep tenma_saki project_sekai.charpack → 伙伴在识别包（能认出≠能共生活）
grep known_other_bots bot_pair_guard   → 负向抑制（非协作通道，与本 part 单 bot 形态无关）
affection familiarity + memo per-user  → 真人画像已有零件（好感/记忆），缺"入叙事"接线
```
