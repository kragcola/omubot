# Living Persona / 角色生命化 — Part 0：系列总纲

> 状态：**系列总纲 · 2026-06-08**
> 系列定位：让 omubot 从"无状态的当下生物"成长为有情绪、有生活、有关系的**生命化角色**。
> 本 part 是系列入口；各分 part 独立成档，见下方索引。

---

## 0. 这个系列要解决什么

用户的两个原始判断催生了本系列：

1. **情绪层**：mood / 日程 / 好感度等模块"各管各的，无统筹"——bot 的当下状态散落、互不通信。
2. **叙事层**：日程生成"随便而无组织"——bot 不会根据角色过出自己的生活、不会推演、长不出故事；而 bot 的人格是**团队偶像**，伙伴不该是工具人，群内真人也应能进入 bot 的故事线。

本系列把这两层统一为 **Living Persona（角色生命化）**：bot 不只是"知道什么"（Learning）和"怎么发消息"（Humanization），而是**有当下心情、过着连贯人生、活在一个有伙伴和真人的社会世界里**的角色。

---

## 1. 在 omubot 子系统全景中的位置

```text
Persona Runtime（bot 是谁）         —— 身份锚点：性格、说话方式基线
Learning Pipeline（知道什么）       —— memo / slang / style / context
Living Persona（过着谁的人生）★本系列 —— 当下心情 + 连贯生活 + 社会关系
Humanization Runtime（怎么发消息）  —— humanizer / register / coupling / sticker
```

Living Persona 居于 Learning（知识）与 Humanization（表达）之间：知识与身份是输入，最终行为是输出，而"此刻是什么心情、过着怎样的一天、和谁有什么关系"是中间的**生命状态**。

---

## 2. 系列分 part 索引

| Part | 主题 | 一句话 | 文档 | 状态 |
| --- | --- | --- | --- | --- |
| **Part 0** | 系列总纲 | 全局架构 + part 关系 + 落地顺序 | 本文 | —— |
| **Part A** | Dialogue Climate（情绪状态） | bot 当下"什么心情"——统一状态 + on-read 动力学 | [living-persona-partA-dialogue-climate.md](living-persona-partA-dialogue-climate.md) | 立项待批 |
| **Part B** | Generative Life（单角色生活叙事） | bot 过着"谁的人生"——经历→反思→规划闭环 | [living-persona-partB-generative-life.md](living-persona-partB-generative-life.md) | 立项待批 |
| **Part C** | Social Narrative（社会化叙事） | 伙伴与真人作为"活的存在"进入 bot 的世界 | [living-persona-partC-social-narrative.md](living-persona-partC-social-narrative.md) | 需独立调研 |
| **Research** | 故事弧补充调研 | 酒馆/故事树/Drama Management/长故事一致性对 omubot 的补充 | [living-persona-research-story-arc-2026-06-08.md](living-persona-research-story-arc-2026-06-08.md) | 研究补充 |

---

## 3. 各 part 的关系与边界

```text
                    ┌──────────────────────────────────────────┐
                    │            Living Persona                 │
                    │                                          │
  Persona ──┐       │  Part A: Dialogue Climate                │
            ├──────→│    当下状态（energy/valence/tension…）    │
  Learning ─┘       │    on-read 连续时间闭式动力学             │
                    │            │ tension/mood                 │
                    │            ▼                              │
                    │  Part B: Generative Life                 │
                    │    经历→反思→规划 闭环（复用 memo/Dream）  │
                    │    L1 角色+昨日 / L2 反思 / L3 反哺+重规划 │
                    │            │ 叙事需要"他者"               │
                    │            ▼                              │
                    │  Part C: Social Narrative                │
                    │    伙伴=可演绎虚构 / 真人=有限事实+印象    │
                    │    单 bot 持有"活的社会世界模型"          │
                    └──────────────────────────────────────────┘
                                 │ 统一行为指导
                                 ▼
                         Humanization Runtime
```

- **A 与 B 互补不重叠**：A 管"状态怎么随时间波动"，B 管"生活怎么连贯展开"。Stanford Generative Agents 里二者合一（reflection 同驱情绪与规划），omubot 选择**分线落地、在 B-L3 交汇**（事件重规划消费 A 的 tension/mood）。
- **C 依赖 B**：单角色叙事自洽是社会化叙事的前提——bot 自己的故事都长不连贯，谈不上把伙伴/真人编进来。
- **C 复用 A**：伙伴/真人的"关系温度"直接复用 A 的 valence/familiarity 维度，不另造。

---

## 4. 落地顺序（全局）

1. **A-M1（tension 单维）与 B-L1（角色+昨日）可并行**——互不依赖，都是低风险小批。
2. A 的全维 / B 的 L2-L3 在各自 MVP 稳定后推进；B 新增 **L1.5 Story Arc（剧情弧账本）** 承接“连续一周舞台剧比赛 + 期末考试冲突 + 伙伴事故 + 舞台动作重规划”这类长线样例，详见 [故事弧补充调研](living-persona-research-story-arc-2026-06-08.md)。B-L3 与 A 在事件重规划处交汇。
3. **C 在 B 跑通后启动独立调研**（伙伴/真人状态画像 + 想象/事实分层 + 伦理红线），产出报告再立项。**例外**：C 的轻量前置 **C-MVP 伙伴状态卡**（仅虚构伙伴 `fiction`）随 B-L1.5 一起做，为剧情弧的 `partner_states` 供数；真人入叙事仍后置到 C 主体。
4. 全系列**低优先级**，不抢当前主线（角色包补齐 + pmubot）。各 part、各层均可独立停在稳定态。

---

## 5. 共识与红线（全系列适用）

- **架构对称**：复用已验证基建（`add_block` / `has_provider` 让位 / `on_post_reply` / RuntimeStateBus / memo 卡 / Dream），不重造轮子。
- **on-read 优先**：状态尽量表述为"last_ts→now 的解析函数"，避免常驻 tick（详见 Part A §2 的 R5 决策）。
- **想象/事实分层**：虚构角色（伙伴）可自由演绎，真人仅以主观印象/共同经历入叙事、不可虚构其线下行为（详见 Part C）。
- **灰度兜底**：每个落地层带开关、默认关、灰度对比，新路径未验证不夺旧路径。

---

## 6. 来源沿革

本系列由 2026-06-08 的两份独立 charter 合并重组而来（原 charter 已并入下列分 part，不再单独保留）：

- `dialogue-climate-charter`（Dialogue Climate 立项书）→ [Part A](living-persona-partA-dialogue-climate.md)
- `generative-life-charter`（Generative Life 调研+立项书）→ [Part B](living-persona-partB-generative-life.md) + [Part C](living-persona-partC-social-narrative.md)

更上游的调研仍有效、可回溯：[issue17 dialogue-climate 调研](omubot-grayscale-issue17-research-dialogue-climate.md)、[issue17 mood-feedback 调研](omubot-grayscale-issue17-research-mood-feedback.md)。后续以本系列四份文档为准。
