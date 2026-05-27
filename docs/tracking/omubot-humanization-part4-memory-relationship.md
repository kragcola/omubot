# Omubot 拟人 Part 4 v2 — 记忆与关系系统的修订版

> 状态：2026-05-24 v1（已废）→ 2026-05-26 v2（已废，过度工程化）→ 2026-05-26 v2-修订版（本文）。
> 触发：v2 把 MaiBot 当作同代基线后，为了"超越同代"反而堆砌了 4 件 surpass-style 中间层（HEG 超图 / 5 信号 EBR + generative replay / ε-budget + 红队 / Chronos SVO + BDI 校准子表）——逐项审查后发现 8/14 项在 Omubot 实际部署语境（个人运行的 QQ 熟人群闲聊 bot、admin = 用户本人）下 **ROI 负向**：威胁模型不匹配、需求频率极低、为套用论文形式而引入。
> 本版只保留 ROI 正向的 6 项改动，并明确区分「机制层面的代际差」（RWS 跨层联动）与「形式层面的代际差」（论文 keyword 形式）：前者是真正的超越，后者是创新泡沫。

---

## 0. v2-修订版对前两版的差异

| 维度 | v1（已废） | v2（已废） | v2-修订版（本文） |
|---|---|---|---|
| MaiBot 角色 | 借鉴对象 | 同代基线 | 同代基线（不变） |
| 立足点 | "扩展 v2.1，搬 MaiBot 死代码" | "引入 4 件 surpass-class 中间层" | "保留 ROI 正向的 6 项；砍掉 8 项过度工程；区分机制代际差 vs 形式代际差" |
| 改动数 | 14 个 P4.x 子任务 | 12 个（4 W1 + 4 W2 + 4 W3） | **6 项**（5 项 v2.1 工程改进 + 1 项 RWS 跨层联动机制） |
| 论文支撑 | 26 篇 baseline | 11 篇 frontier | 真正落地的只挂 2 篇（CER ACL 2025 + THEANINE NAACL 2025）；其余前沿降为参考资料 |
| 原创合成 | 无 | 4 件（HEG / EBR / DP / Chronos×ToM） | **1 件**：RWS 跨层联动（用 v2.1 现成信号喂，不新建子表） |

**自审纪律**（避免再陷入"为创新而创新"）：

1. 每项改动必须明确"在 Omubot 实际部署场景下解决什么用户实际感知到的问题"。
2. 威胁模型 / 需求频率 / 运维负担与 Omubot 部署语境（QQ 熟人群、个人运行）匹配，才入选。
3. 论文 keyword 形式（超图 / 校准 / 红队）不构成入选理由。
4. RWS 跨层联动的真代际差在于**机制**（调度层 ↔ 记忆层可微分耦合的挂载位），不在于喂它的数据来自新子表还是现成 SQL。

---

## 1. 重新定性：当前记忆与关系系统是什么

### 1.1 一句话定性

Omubot v2.1 当前记忆栈（[services/memory/](../../services/memory/) + [services/episodic/](../../services/episodic/) + [services/memory_consolidator/](../../services/memory_consolidator/) + [services/slang/](../../services/slang/) + [services/style/](../../services/style/) + [services/persona/willingness.py](../../services/persona/willingness.py)）是一个**多模块协作的扁平结构化表 + 单层向量召回 + 周期触发 consolidator**——工程精修度上已**显著超过** MaiBot（双表 + 5-state 机 + observations 反馈环 + scope 双维 + slang/style 独立模块），但**范式仍与 MaiBot 同代**：所有事实存进 SQLite 行 / 列；跨实体关系靠 `linked_memory_ids` JSON 数组手工维护；跨 group 共享靠 `cross_group_visible` 单 bool 旗标；DreamAgent consolidate 按时间间隔触发；记忆层信号没有回流到调度层。

### 1.2 同代基线对照（仅作下限参考）

| 维度 | MaiBot | Omubot v2.1 | Omubot v2.1 + 本版 6 项 |
|---|---|---|---|
| 核心存储 | 扁平 KV / mood JSON | SQLite 行（CardStore 双表 + EpisodeStore 三表） | 不变（不引入超图） |
| 跨 scope 共享 | global pickle | `cross_group_visible: bool` | `cross_group_visibility: enum{none, opt_in, opt_out}` |
| 跨实体关系 | 印象表字段 | `linked_memory_ids: list[str]` JSON | 不变 |
| provenance | 无 | 无 | (source_msg_id, captured_at, captured_by) 三列 |
| decay 后召回 | 硬删除 | 软删除 + 不可召回 | 软删除 + `include_decayed` 旗标可召回 |
| consolidate 触发 | 时钟 | 时钟 | 时钟 + 2 信号边界（silence + mood reversal） |
| willingness 决策 | 无 | 5-stage 启发式 | 5-stage + CER episodic 注入（top-3 outcome 明确的相似 trajectory） |
| **调度 ↔ 记忆耦合** | **无** | **无** | **RWS 跨层联动 4 信号**（mood / outcome / familiarity / willingness phase） |

### 1.3 真代际差 vs 形式代际差

判定一项改动是否是"真代际差"，看：**有没有同代框架（MaiBot 等）想做都做不到的结构性条件，而 Omubot 整体架构恰好支撑得起**。

- **真代际差（保留）**：RWS 跨层联动。MaiBot 没有 RWS 这种统一打分层，调度逻辑就是 if-else 散落；记忆层就是 mood 字符串，没有结构化反馈数据。Omubot 有 RWS（Part 3.5 v3）+ services/memory 多模块结构化数据 + observations 反馈环 + willingness 5-stage——这套挂载位本身就是同代框架不具备的。
- **形式代际差（砍掉）**：HEG 超图 / SVO 子表 / BDI 校准 / DP-ε 配额 / MEXTRA 红队。这些是论文 keyword 形式，不是机制差。同代框架引入它们也能做，只是没人在 QQ 闲聊场景下做（因为收益小）。

---

## 2. 跨学科前沿研究（仅参考，不必每篇都落地）

v2 列出了 11 篇 2024-2026 前沿，逐项评估后**只有 2 篇真正驱动本版改动**，其余降为参考。这不是论文质量问题，是部署场景匹配度问题。

### 2.1 真正落地的 2 篇

**CER — Contextual Experience Replay（ACL 2025 long）**

- 核心：把 agent 过去 trajectory 蒸馏成可检索经验，新 task 时检索匹配经验加 context。training-free。
- 落地：[services/persona/willingness.py](../../services/persona/willingness.py) 5-stage 决策时，按 `situation` 做 episodic 检索注入 top-3。
- Omubot 适配：v2.1 EpisodeStore 已经存了 `(situation, action_taken, outcome_signal)` 三元组——CER trajectory 形态天然存在，只缺主动检索路径。
- 真痛点：bot 连续被冷处理后还硬聊，因为 willingness 5-stage 没用上历史 outcome。

**THEANINE — Lifelong Timeline-Based Memory（NAACL 2025 long）**

- 核心：拒绝"删旧记忆"思路，所有记忆按 timeline 永存，检索靠时间窗 + topic 双索引。
- 落地：[services/episodic/store.py](../../services/episodic/store.py) `search_episodes()` 加 `include_decayed: bool = False`。
- Omubot 适配：v2.1 已有 `decay_at` 软删除列——THEANINE 启发把 decay 后的检索路径补上即可，不需要新概念。

### 2.2 评估后不落地的 9 篇（与 Omubot 部署语境不匹配）

| 论文 | 不落地原因 |
|---|---|
| HyperMem (2604.08256) / Hyper-KGGen (2602.19543) / SAGE (2605.12061) / MemoriesDB (2511.06179) / MemORAI (2605.01386) | 超图记忆 + DWP 检索：跨实体推理在 QQ 闲聊场景频率极低；500-800 行实施成本对应的实际 bot 行为改善路径不清 |
| Chronos (2603.16862) | SVO 时序子表 + 相对时间消歧：QQ 闲聊场景下"上周 X 在哪个群说过什么"查询频率极低；消歧失败率高 |
| Spens et al. (Nature 2024 / CCN 2025) | 海马生成式回放：神经科学机制套到 LLM dream cycle 是大跳跃；generative counterfactual replay 不回流任何决策环节，只产生 admin 看板反思日志 |
| ToM-Agent / Agentic-ToM / ToMA | BDI 显式建模 + isotonic 校准：场景是 SocialIQA / FANToM 复杂多人推理；QQ 闲聊不需要；每周 30 条人工标注强加运维负担 |
| MEXTRA (ACL 2025) | 内存提取攻击：威胁模型 = 恶意用户对企业 agent；Omubot 部署在熟人群无对应威胁；红队 panel 是 LLM 配额纯支出 |

**自审结论**：v2 把这 9 篇全部塞进 §3 原创合成是论文 keyword 堆叠。修订版把它们放参考资料即可。

---

## 3. 真正保留的改动（按 ROI 降序）

### 3.1 RWS 跨层联动（机制代际差，本版唯一原创合成）

**问题**：v2.1 的调度层（[services/scheduler.py](../../services/scheduler.py) + Part 3.5 v3 的 RWS）和记忆层（[services/memory/](../../services/memory/)）目前没有耦合——bot 在被冷处理后还会硬聊，因为 RWS 不读记忆层信号；对熟悉用户也不会更主动，因为没有 familiarity 输入。MaiBot 同代框架做不到这条不是因为 11 个调度位点的精修度不够，是因为它们没有 RWS 这种**统一打分层挂载位**。

**方案**：Part 3.5 v3 §3.1 RWS 公式扩展 4 项，全部读 v2.1 现成数据，**不新建任何子表 / 不引入 LLM 提取 / 不引入人工标注**。

**4 项加权信号（全部读现成 SQL）**：

```text
RWS_v2-修订版(ctx) = sigmoid(
    ... [Part 3.5 v3 已有 8 项保持不变] ...
  + w_mood        * mood_trend(target, last_30min)              # state_board.py 已有
  + w_outcome     * recent_outcome_ratio(group, last_24h)       # episodic/store.py outcome_signal 已有
  + w_familiarity * min(card_count(target) / cap, 1.0)          # card_store.py 已有
  + w_willingness * stage_to_score(current_willingness_stage)   # persona/willingness.py 已有
)
```

**关键设计选择**：

- **默认权重保守**：w_mood / w_outcome / w_familiarity / w_willingness 默认 0.05-0.1，不主导决策；让 admin 后续按观察调整。
- **全部读现成数据**：4 个 reader helper 函数总计 < 100 行；不需要 ALTER TABLE、不需要 consolidator 周期任务、不需要 LLM 调用。
- **可解释性**：与 Part 3.5 v3 §3.1 的可解释决策日志一致——每次决策把 12 项加权值写日志。
- **回滚**：env flag `RWS_MEMORY_COUPLING=false` 让 4 项权重 = 0，瞬时回退。

**MaiBot 同代为什么做不到**：MaiBot 没有 RWS 这种统一打分层（散落 if-else），更没有 v2.1 这套结构化记忆数据。这是 Omubot 整体架构（kernel / services / RWS）才支撑得起的设计。

---

### 3.2 CardStore provenance 三列

**问题**：v2.1 的 memo card 没记录"从哪条消息抽出、由哪个 LLM call 抽、几时抽"——debug 时无法追溯"为什么 bot 知道我喜欢 X"，admin 看板也无法做基础审计。

**方案**：[services/memory/card_store.py](../../services/memory/card_store.py) schema 加三列。

- `source_msg_id TEXT`（NULL allowed for legacy）
- `captured_at TIMESTAMP`（NULL allowed for legacy）
- `captured_by TEXT`（默认 'unknown'，新写入必填）

写入路径：[services/memory_consolidator/promoter.py](../../services/memory_consolidator/promoter.py) 统一注入。旧数据全部标记 `source='legacy'`。

**收益**：debug + 审计场景刚需；后续任何 memo 异常排查都依赖此。

**风险**：极低，旧数据 legacy 标记不破坏现有调用。

---

### 3.3 CER willingness episodic 注入

**问题**：v2.1 willingness 5-stage 决策（[services/persona/willingness.py](../../services/persona/willingness.py)）没用上历史 outcome——bot 连续被冷处理后还会硬聊。EpisodeStore 已经存了 `(situation, action_taken, outcome_signal)` 三元组，是 CER trajectory 的天然形态，只缺主动检索路径。

**方案**：

- 新建 helper 函数 `episodic_situation_lookup(group_id, situation_embedding) → list[Episode]`，读 EpisodeStore。
- 注入策略：top-3 相似 episode（相似度 > 0.65 且 outcome_signal 明确）拼进 LLM context。
- 每次 willingness 调用最多注入 3 条，避免 prompt 暴涨。

**收益**：bot 实际行为可改善——见过类似冷处理的 trajectory 后，5-stage 决策能判断当前 situation 该退让。

**风险**：检索噪声污染（相似度阈值 + outcome 必须明确控制）；prompt 长度（cap 3 控制）。

---

### 3.4 EBR 2 信号触发 dream cycle（silence + mood reversal）

**问题**：v2.1 [services/dream_agent.py](../../services/dream_agent.py) 按时间间隔触发 consolidate——重要事件后要等下个时钟 tick 才处理。

**方案**：补充 2 类边界信号触发（不替换时钟，时钟仍是兜底）：

- 持续静默：> 30min 无新消息且 episodic 队列非空 → 触发。
- 用户情绪反转：[services/memory/state_board.py](../../services/memory/state_board.py) mood signal 方差 spike → 触发。

**为什么是 2 信号而非 5 信号**：v2 列的 5 信号互相重叠（mood variance ⊃ persona conflict；topic embedding 余弦 ⊃ Hawkes spike）。2 信号是 5 信号版收益的 70-80%，复杂度只有 1/3。

**砍掉**：generative counterfactual replay（v2 §3.2 后半段）。让 LLM 想象"如果当时换个回应路径会怎样"不回流到任何决策环节，只产生 admin 看板反思日志——投入产出不匹配。

**收益**：重要事件后及时 consolidate，不再傻等下个时钟 tick。

**风险**：信号质量取决于 state_board 上游数据；若 state_board mood 抖动剧烈会导致 boundary 队列噪声——加 cooldown 30min 控制。

---

### 3.5 EpisodeStore include_decayed 旗标

**问题**：v2.1 EpisodeStore 已有 `decay_at` 列做软删除——但 decay 后的 episode 在用户问"上个月那个事"时无法召回，等于硬删。

**方案**：[services/episodic/store.py](../../services/episodic/store.py) `search_episodes()` 加 `include_decayed: bool = False`。

- 当前所有调用点传默认值 → 行为不变。
- 长程召回路径调用方传 `True` → 召回包含 decay 的 episode。

**收益**：把现有软删除列做完整；THEANINE 风格 timeline 召回的最小落地。

**风险**：极低（一个参数 + WHERE 条件）。

---

### 3.6 cross_group_visibility enum 三值化

**问题**：v2.1 `cross_group_visible: bool` 二值——开了全暴露，关了全屏蔽，没有中间地带。

**方案**：改为 `cross_group_visibility: enum{none, opt_in, opt_out}`。

- `none`：默认；不跨群引用。
- `opt_in`：仅在 admin 显式标记的群间引用。
- `opt_out`：默认跨群引用，admin 可标记群间黑名单。

**砍掉**：v2 §3.3 的 ε-budget + scope_transfer_ledger + MEXTRA 红队 panel——威胁模型（恶意用户对企业 agent 攻击）与 Omubot 部署（熟人群、admin = 用户本人）不匹配。

**收益**：比 bool 二值灵活，三个场景明确划分；admin 控制力强。

**风险**：极低（ALTER TABLE 改字段类型 + 调用点适配）。

---

## 4. 砍掉的 8 项（明示与理由）

| 项 | v2 §位 | 砍掉理由 |
|---|---|---|
| HEG 超图 + entity_vertices + DWP 检索 | v2 §3.1 | 跨实体推理在 QQ 闲聊频率极低；500-800 行成本对应的 bot 行为改善路径不清；为套用 HyperMem / MemORAI 形式而引入 |
| entity resolution 跨群同人识别 | v2 §4.2 P4.12.2 | QQ 用户已有 user_id 唯一标识；admin 人工裁决队列是新运维负担；误合并 → memo bleed 风险 |
| EBR 5 信号 → 砍 3 留 2 | v2 §3.2 | 5 信号互相重叠；2 信号是 70-80% 收益、1/3 成本 |
| EBR generative counterfactual replay | v2 §3.2 + P4.12.4 | 不回流任何决策环节；只产生 admin 反思日志；LLM token 真金白银 |
| ε-budget + scope_transfer_ledger | v2 §3.3 + P4.13.1 | MEXTRA 威胁模型 = 恶意用户对企业 agent；Omubot 熟人群无对应威胁；admin daily review 是新运维负担；误冻结合法引用 → UX 恶化 |
| MEXTRA 红队 panel | v2 §3.3 + P4.13.2 | 熟人群无攻击者；30 条 weekly attack prompt 是 LLM 配额纯支出；误报噪声 |
| Chronos SVO 子表 | v2 §3.4 + P4.13.3 | "上周 X 在哪个群说过什么"查询频率极低；relative time 消歧失败率高；t_resolution_confidence < 0.3 丢弃后剩余样本不足以支撑 RWS 时序信号 |
| BDI 子表 + isotonic 校准 + 标注队列 | v2 §3.4 + P4.13.3 | ToM-Agent 场景是 SocialIQA / FANToM 复杂推理；QQ 闲聊不需要 BDI 拆解；每周 30 条人工标注强加运维负担；标注 stuck → 校准失败 |

**保留 vs 砍除原则**：
- 保留：解决 Omubot 实际场景下用户能感知的问题，且成本与收益匹配。
- 砍除：威胁模型不匹配 / 需求频率极低 / 论文 keyword 形式套用 / 强加运维负担。

---

## 5. 三波路线图（瘦身版）

### 5.1 Wave 1 — P4.11 系列：低风险止血（3-5 天）

**P4.11.1 CardStore provenance 三列**（§3.2）

- 触点：[services/memory/card_store.py](../../services/memory/card_store.py) schema + [services/memory_consolidator/promoter.py](../../services/memory_consolidator/promoter.py)。
- 测试：`tests/test_card_provenance.py` 断言新 card 三列非空、legacy 标记正确。

**P4.11.2 EpisodeStore include_decayed**（§3.5）

- 触点：[services/episodic/store.py](../../services/episodic/store.py) `search_episodes()` 加参数。
- 测试：构造 1 条 decay_at < now 的 episode，断言 False/True 各自召回 0/1 条。

**P4.11.3 cross_group_visibility enum 三值化**（§3.6）

- 触点：[services/memory/card_store.py](../../services/memory/card_store.py) schema + 所有调用 `card.cross_group_visible` 的位点（同模式扫描必跑）。
- 旧数据：`true → opt_out`；`false → none`。
- 测试：三值各 1 case 断言 admin 看板渲染正确。

**P4.11.4 CER willingness episodic 注入**（§3.3）

- 触点：[services/persona/willingness.py](../../services/persona/willingness.py) 5-stage + 新 helper `episodic_situation_lookup`。
- 测试：mock outcome=positive / outcome=NULL 各 1，断言注入策略正确。

**Wave 1 完成判据（D4）**：

- pytest + ruff + pyright 全绿。
- 维护日志一条："Wave 1 上线：provenance + include_decayed + visibility enum + CER 注入"。
- 同模式扫描（D1）：grep `cross_group_visible` 所有位点确认全部迁移到 enum。
- 回滚：4 条 commit 各自独立 revert，30 秒。

---

### 5.2 Wave 2 — P4.12 系列：RWS 跨层联动（5-7 天）

**P4.12.1 4 个 reader helper 落盘**（§3.1）

- 新建 [services/scheduler/memory_signals.py](../../services/scheduler/memory_signals.py) （或并入 Part 3.5 v3 P3.12 RWS scaffolding 同级目录）。
- 4 个函数：`mood_trend / recent_outcome_ratio / familiarity / willingness_phase`。
- 全部读现成 SQL，不新建表。
- 测试：每函数 mock 数据 2-3 case，断言返回值在 [0, 1] 区间。

**P4.12.2 RWS 公式扩展 4 项**

- 触点：依赖 Part 3.5 v3 P3.12 RWS scaffolding 已落盘（compute_rws + RWSExplanation dataclass）。
- 改动：compute_rws 内累加 4 项加权值；RWSExplanation 字段加 4 项可解释维度。
- 默认权重：每项 0.05-0.1。
- env flag：`RWS_MEMORY_COUPLING=false` 让 4 项权重 = 0，瞬时回退。

**P4.12.3 EBR 2 信号触发 dream cycle**（§3.4）

- 新建 [services/memory_consolidator/event_boundary.py](../../services/memory_consolidator/event_boundary.py)（精简版，只 2 信号 + cooldown）。
- 信号：silence > 30min + 队列非空 / state_board mood variance spike。
- 触点：[services/dream_agent.py](../../services/dream_agent.py) 优先消费 boundary 队列。
- cooldown：30min 内不重复触发。
- 测试：2 类信号各 1 case 触发 + 1 case 平稳序列不触发 + cooldown 1 case。

**Wave 2 完成判据（D4）**：

- RWS 决策日志可见 4 项加权值（shadow log SELECT）。
- EBR 触发命中：手工标注 20 条（10 silence + 10 mood reversal），自动检测召回 ≥ 80%。
- dream cycle 总耗时增长 ≤ 10%（usage.db 聚合）。
- 维护日志记录 Wave 2 上线 + 同模式扫描 + 回滚路径（D4）。
- 回滚：env flag `RWS_MEMORY_COUPLING=false` / `EBR_ENABLED=false` 各自独立瞬时回退。

---

### 5.3 没有 Wave 3

v2 的 Wave 3（P4.13 闭环治理）3 项全部砍掉（DP-budget / 红队 / Chronos-ToM 双通道）。本版到 Wave 2 即收口。后续如出现真实运营痛点（例如某条 memo 误暴露被用户投诉），再针对性引入对应组件即可，不在此版本预先建设。

---

## 6. 验收 / 风险 / 回滚

### 6.1 Wave 1 验收

| 项 | 通过条件 | 证据 |
|---|---|---|
| pytest | 全绿 | `uv run pytest` |
| lint + type | ruff + pyright 全绿 | 本地 |
| provenance 三列 | 新 card 三列非空、legacy 标记 | SQLite SELECT |
| include_decayed | 单元测试 True/False 各 1 case | tests/ |
| visibility enum | 三值 admin 看板渲染 + 调用点全迁移 | grep + 浏览器 |
| CER 注入 | mock 测试 positive/NULL 各 1 case | tests/ |

**风险**：CER 注入噪声污染 willingness 决策。**控制**：相似度阈值 0.65 + outcome_signal 必须明确。

**回滚**：4 条 commit 独立 revert。

### 6.2 Wave 2 验收

| 项 | 通过条件 | 证据 |
|---|---|---|
| 4 reader helper | 每函数返回值 ∈ [0, 1] | 单元测试 |
| RWS 12 项可解释 | shadow log 含 4 项新加权 | shadow log SELECT |
| RWS 决策与无耦合 baseline 对比 | 7 天日志可观察行为差异（被冷处理后退让 / 对熟悉用户更主动） | 决策日志人工抽样 |
| EBR 触发命中 | 手工标注 20 条召回 ≥ 80% | tests/ + 手工 |
| dream cycle 耗时增长 | ≤ 10% | usage.db |

**风险**：RWS 4 项权重默认值过保守 → 无可观察行为变化。**控制**：admin 按 7 天观察日志按需上调权重。

**回滚**：env flag 各自独立瞬时回退；4 项权重设 0 即等价于无耦合 baseline。

---

## 7. 参考资料

### 7.1 真正驱动本版的 2 篇

| ID | 标题 | 出处 | 落地位 |
|---|---|---|---|
| CER-2025 | Contextual Experience Replay for Self-Improvement of Language Agents | ACL 2025 long | §3.3 + Wave 1 P4.11.4 |
| THEANINE-2025 | Lifelong Dialogue Agents via Timeline-based Memory Management | NAACL 2025 long | §3.5 + Wave 1 P4.11.2 |

### 7.2 评估后不落地的前沿（仅作素材库）

HyperMem / Hyper-KGGen / SAGE / MemoriesDB / MemORAI / Chronos / Spens et al. (Nature 2024 + CCN 2025) / ToM-Agent / Agentic-ToM / ToMA / MEXTRA — 详见 §2.2 不落地理由表。

### 7.3 同代基线（仅作下限对照）

MaiBot 32 文件 / 22 dead-code | Mem0 ECAI 2025 | LangMem / Letta / Zep。

### 7.4 Omubot 内部触点

| 文件 | 角色 |
|---|---|
| [services/memory/card_store.py](../../services/memory/card_store.py) | provenance 三列 + visibility enum 宿主 |
| [services/episodic/store.py](../../services/episodic/store.py) | include_decayed 旗标宿主 |
| [services/memory_consolidator/promoter.py](../../services/memory_consolidator/promoter.py) | provenance 写入路径 |
| [services/persona/willingness.py](../../services/persona/willingness.py) | CER episodic 注入点 |
| [services/dream_agent.py](../../services/dream_agent.py) | EBR 2 信号触发点 |
| [services/memory_consolidator/event_boundary.py](../../services/memory_consolidator/event_boundary.py)（新建） | EBR 信号检测器 |
| [services/memory/state_board.py](../../services/memory/state_board.py) | mood 信号源 + RWS w_mood reader |
| [services/scheduler/memory_signals.py](../../services/scheduler/memory_signals.py)（新建） | RWS 4 项 reader helper |
| Part 3.5 v3 P3.12 RWS scaffolding（依赖项） | RWS 公式扩展宿主 |

---

## 8. 与前两版的最终差异声明

v1 → v2 → v2-修订版的演进路径：

- **v1 错误**：把 MaiBot 当借鉴对象，14 个 P4.x 子任务都是"为现有列加新字段"。
- **v2 错误**：把 MaiBot 降为同代基线后，**为了证明"超越"而堆砌 4 件 surpass-class 中间层**——其中 8 项在 Omubot 部署语境下 ROI 负向（威胁模型不匹配 / 需求频率极低 / 论文 keyword 形式套用 / 强加运维负担）。
- **v2-修订版立场**：区分"机制层面的代际差"和"形式层面的代际差"。前者是 RWS 跨层联动（v2.1 已有结构化数据 + Part 3.5 v3 RWS 挂载位的组合，同代框架做不到）；后者是 HEG / SVO / BDI / DP-ε / 红队等论文 keyword 形式（同代框架想做也能做，只是没人在 QQ 闲聊场景下做，因为收益小）。本版只保留前者（1 件原创合成）+ 5 项 v2.1 工程改进，砍掉所有形式代际差。

**6 项保留 vs 8 项砍除的判定标尺**：
1. 在 Omubot 实际部署场景下解决用户能感知的问题。
2. 威胁模型 / 需求频率 / 运维负担与 Omubot（QQ 熟人群、个人运行）匹配。
3. 论文 keyword 形式不构成入选理由。
4. 真代际差在机制（挂载位），不在数据来自新表还是现成 SQL。

落实节奏：Wave 1（P4.11.1-4，3-5 天）+ Wave 2（P4.12.1-3，5-7 天，依赖 Part 3.5 v3 P3.12 RWS scaffolding 已落盘）。本版**不**列派单文档；如需 Part 4 v2-修订版 execution.md 同步落盘，单独告知。
