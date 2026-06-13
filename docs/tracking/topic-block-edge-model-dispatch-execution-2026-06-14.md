# 话题块边模型重构 派单 — L0-L3 + 三护栏（执行追踪）

> 状态：2026-06-14 立。**接单人：reasonix。** 本文是 [话题块边模型 D3 实施清单](../migrations/topic-block-edge-model-2026-06-11.md) 的**执行派发版**，交付 reasonix 按 Wave 顺序落地。
>
> 范围：把 `TopicBlockTracker` 的"消息→规则瀑布→块归属"重构为**边模型**——维护"消息→前驱边 + `message_id→block` 反查、块=连通分量"，接入活跃度衰减生命周期、两层池复活、可选句向量表征。一举消解审计 [topic-block-multitopic-defects-audit-2026-06-11.md](_archive/topic-block-multitopic-defects-audit-2026-06-11.md)（已归档）的缺陷 1/2/3/4/5/6/7。覆盖 **L0 边模型 + L1 线性打分 + L2 活跃度生命周期 + L3 句向量（可选末位）+ 三护栏**。
>
> 配套（冲突时以**本文 §0 决策冻结表**为准，论证回溯实施清单与审计）：[D3 实施清单](../migrations/topic-block-edge-model-2026-06-11.md) §0–§8、审计 §7.5（L0-L3）/§8.5（三护栏）/§9（四靶对标公式）、[B 系列总设计](group-multitopic-understanding-b-series-design.md)。
>
> **执行原则（覆盖任何冲突项，逐条照做）：**
> 1. **Wave 严格串行**——下一 Wave 必须等上一 Wave「收口」全绿。L0→L1→L2→L3 有真实数据依赖（L0 的反查索引是 L1 候选池基础，L2 的 reservoir 依赖 L0 反查保命，L3 复用 L1 的候选池），**不允许跳、不允许抢跑**。
> 2. **每 Wave 自带：D1 grep 证据 + 验证命令 + 回滚命令**——三者缺一不算闭环。动手前先跑 D1 grep，把实际行号/输出贴回本文对应回执位置。
> 3. **遇到与本文不符的现实（行号变了/文件不在/命令报错）→ 停，记录到 §5 偏差表，等验收人确认，不要自行猜着改。**
> 4. **原地改造、行为锚兜底**：本批**不加 config 双轨开关**（实施清单 §0 用户拍板），靠 `topic_block.enabled` 总闸 + 现有 4 个核心场景测试作为行为锚。每层落地后这 4 个锚测试必须保持绿，否则即回归。
> 5. **D5：跑全量 pytest 前先 `pkill -9 -f pytest`**，避免与 IDE 抢 sqlite 文件锁死锁。
> 6. **缓存红线**：锚点写入路径不变（pending → 最后一条消息、缓存前缀外），对 hit% 零影响（沿用 B1 设计 §7.2）。任何改动不得把话题块状态写进缓存前缀。
> 7. **NapCat 红线**：本重构纯 bot 侧内存逻辑，不触 NapCat、不需重启容器；本地验证用既有 pytest，**禁止**任何需重启/重配 NapCat 的手段。
> 8. **回滚底线**：原地改 `topic_block.py`，回滚 = `git revert` 该 PR；`topic_block.enabled` 关闭则整模块旁路、行为回到无话题块。每 Wave 落地前确认这两条之一可用。

---

## 0. 决策冻结表（执行者不得改；与实施清单/审计冲突时以本表为准）

| # | 冻结决策 | 来源 | 执行含义 |
| --- | --- | --- | --- |
| F1 | **原地改造** `topic_block.py`，保留公共方法签名（`observe`/`mark_bot_involved`/`_active`），新逻辑内部替换 | 实施清单 §0（用户 2026-06-11 拍板） | 不新建并行模块、不加 config 双轨开关；调用方（scheduler）签名向后兼容 |
| F2 | **边模型**：维护 `message_id→block` 反查 + 引用 attribute 单条消息 + 边带说话人；块=连通分量 | 审计 §7.5 / Kummerfeld 2019 | reply 命中反查 O(1) 归属，弃 `representative_speaker` 的无序 set |
| F3 | 同说话人**硬规则降为软特征**（文献实测仅 52.2% 成立） | 审计 §7.3② | L1 规则瀑布→线性打分，同说话人为低权特征，不再先于相似度短路 |
| F4 | **活跃度衰减**用 EDMStream `activity=a^(λΔt)·activity+1`（惰性求值，无定时器）替 deque 插入序淘汰 | 审计 §9.1① | `a=0.998,λ=1`；按 activity 衰减值淘汰，不按插入序 |
| F5 | **三护栏不可妥协**：① 候选池不裁剪低活跃块（保 PC 召回）；② 引用边强先验非硬真值（轻校验防 broadcast/reframe 误并）；③ 衰减≠物理删除（reservoir 保 message_id 反查） | 审计 §8.5 | 任一护栏被破即回归；省算力靠打分排序而非裁剪候选 |
| F6 | **缓存断点不动**：锚点仍落 pending → 最后一条消息（缓存前缀外） | B1 设计 §7.2 | 重构纯内存逻辑，hit% 零影响 |
| F7 | L3 句向量**可选、末位**，`similarity_backend` 默认 `"ngram"`（现行为基线），渐进切 `"embedding"` | 实施清单 §0/§5 | L3 未上线时 `centroid=None`，回退 `last_text`；不引训练模型 |
| F8 | **不做（本期）**：不改 RWS 概率；不改 closing/at/followup bypass；不改 B2 Goffman 角色门阈值（缺陷5靠 L1 根因消除）；"纳入记忆"跨层接口后置 | 实施清单 §0 | 出本批范围；reservoir 溢出才落 memory，非复活必经路径 |

> **派单规则**：reasonix 拿到本文先跑 Wave 0 锚点复验。任何一项与下表预期不符 → 记 §5 偏差表，停，问验收人。

---

## 1. 主线锚点与证据订正（执行前必读）

下表是对实施清单 §2 接缝表的 grep 实证锚点，**2026-06-14 派单人已复核**（立项 06-11 后 router/scheduler 行号有 ±10 行漂移，已订正为当前值）。派单按本表执行；reasonix Wave 0 第一步就是重新 grep 确认仍成立。

| 锚点 | 实施清单表述 | 验证命令 | 预期（2026-06-14 复核） |
| --- | --- | --- | --- |
| reply 信号提取函数 | `_extract_topic_block_signals` 提 sender/self/at，缺 reply_to_message_id | `grep -n 'def _extract_topic_block_signals' kernel/router.py` | `:293`（reply_sender_id 在 `:304`，**需加 reply_to_message_id**） |
| 被引用消息 message_id | router 已取用于图片重取，提取函数未用 | `grep -n 'getattr(reply, "message_id"' kernel/router.py` | `:1059`（清单写 1046，**已漂移到 1059**；仅供透传参考，不改此点） |
| tracker.observe 调用 | 已传 message_id/reply_to_sender_id/at_targets | `grep -n '_topic_tracker.observe' services/scheduler.py` | `:577`（**需加 reply_to_message_id** 入参） |
| observe 签名 | tracker 侧入参 | `grep -n 'def observe' services/group/topic_block.py` | `:97`（加可选 `reply_to_message_id=None`，additive） |
| 块归属核心 | 规则瀑布，L0/L1 重写此处 | `grep -n 'def _attribute' services/group/topic_block.py` | `:121`（规则瀑布在 140–158） |
| TopicBlock 结构 | dataclass，加 activity/last_access/predecessor/反查 | `grep -n 'class TopicBlock' services/group/topic_block.py` | `:34`（participants `:39` set→dict；representative `:45-53` 弃 set 序） |
| tracker __init__ / 反查索引位置 | 加 `_msg_to_block` | `grep -n 'def __init__' services/group/topic_block.py` | `:59`（`self._blocks` deque 在 `:61`） |
| 容量淘汰 | `deque(maxlen)` 插入序淘汰 | `grep -n 'deque(maxlen' services/group/topic_block.py` | `:112`（`setdefault(..., deque(maxlen=self._max_blocks))`，L2 改 dict） |
| stale 硬窗 / 候选 | `_active` 隐式只取非 stale | `grep -n 'def _active' services/group/topic_block.py` | `:90`（L2-3 候选池须含 reservoir，护栏一） |
| mark_bot_involved 竞态 | 取"当前最活跃"而非 bot 实际应答块 | `grep -n 'def mark_bot_involved' services/group/topic_block.py` | `:183`（取 `max(last_active, len(participants))` 在 `:224`，**改按 firing_block_id**） |
| mark_bot_involved 调用点 | scheduler 调用 | `grep -n 'mark_bot_involved' services/scheduler.py` | `:2230`（清单写 2228，**已漂移到 2230**；改签名加 block_id） |
| firing_block_id 抓手 | 已存在，缺陷6 复用 | `grep -n 'firing_block_id' services/scheduler.py` | `:134/196/668/1754`（slot 字段已存在，直接复用） |
| 相似度 provider | `NgramSimilarityProvider`，embedding 已留口 | `grep -nE 'class .*SimilarityProvider\|backend' services/similarity.py` | L3 复用抽象，加 embedding 实现 |
| config 字段 | `TopicBlockConfig` 加 5 个 tunable | `grep -n 'class TopicBlockConfig' kernel/config.py` | 加 `decay_a/decay_lambda/reservoir_max/activity_floor/similarity_backend` |
| 现有行为锚测试 | 4 核心场景须保持绿 | `grep -nE 'def test_' tests/test_topic_block.py` | observe + now= 注入 + 断言 message_ids/anchor（全部保持绿） |


---

## 2. Wave 0 — 前置零代码锚点复验（必做，零代码）

后续所有 Wave 依赖本步。**Wave 0 不写代码、不改配置，只 grep + 抓信息 + 回填 §5 回执。**

| 步骤 | 命令 / 操作 | 预期 / 产出 |
| --- | --- | --- |
| 0.1 | 跑 §1 表全部 15 条验证命令，逐条把实际行号/输出贴到 §5「Wave 0 回执」 | 全部对上 → 继续；任一不符 → 停，问 |
| 0.2 | 确认 4 个行为锚测试当前全绿：`pkill -9 -f pytest; PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_topic_block.py -q` | 产出：基线 passed 数（每层落地后须 ≥ 此数且无 fail） |
| 0.3 | 确认 `SimilarityProvider` 抽象与 `backend="embedding"` 留口现状：`grep -nE 'class .*Similarity\|backend\|def similarity' services/similarity.py` | 产出：L3 复用点签名（L0-L2 期不动，仅登记） |
| 0.4 | 把 0.1–0.3 结论写 1 段到 §5「Wave 0 回执」 | 给验收人拍板是否发 Wave L0 单 |

**Wave 0 不是 commit；是派单前置验证。验收人看回执再发 Wave L0。**

---

## 3. 串行执行 Wave 表

依赖：Wave 0（复验）→ **L0（边模型+反查）** → **L1（线性打分）** → **L2（活跃度生命周期+reservoir）** → **L3（句向量，可选末位）**。**每 Wave 收口全绿才进下一个。** 4 个行为锚测试每层后保持绿。

```text
Wave 0 ── L0 (边模型: reply_to_message_id 透传 + _msg_to_block 反查 + 锚人=边source + 缺陷6 firing_block_id)
            │  解缺陷 2/3/6，支撑单用户多块
            ▼
          L1 (归属瀑布→线性打分: 同说话人降软特征 + participants set→dict 带时间戳)
            │  解缺陷 1/5（5 为 1 的下游，根因消除自动缓解）
            ▼
          L2 (活跃度衰减 a^(λΔt)·activity+1 替 deque + 两层池 reservoir 复活 + 候选含 reservoir)
            │  解缺陷 4，立护栏一/三
            ▼
          L3 (块质心 c-TF-IDF/句向量替 last_text 单条 + similarity_backend 可切 embedding)
               解缺陷 7（可选，默认 ngram 不变行为）
```

### 3.1 Wave L0 — 边模型：reply 反查 + 锚人=边 source + 缺陷6（改动可独立验证，先行）

目标：建 `message_id→block` 反查索引，reply 命中 O(1) 精确归属（护栏二轻校验），锚人由边 source 推出（弃无序 set），mark_bot_involved 改按 `firing_block_id` 精确标记。**纯结构 + 透传，不动 L1 打分逻辑。**

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | 验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **L0-1** | `_extract_topic_block_signals` 加提取 `reply_to_message_id`（`getattr(reply,"message_id",None)`） | [kernel/router.py:293](../../kernel/router.py#L293) | `grep -nE 'reply_to_message_id\|reply_to_sender_id' kernel/router.py` 命中提取点 | router 单测断言信号 dict 含 `reply_to_message_id` 且透传 | `git checkout kernel/router.py` |
| **L0-2** | `observe` 加可选 `reply_to_message_id=None`（additive，旧调用不传仍工作）；scheduler.py:577 透传 | [services/scheduler.py:577](../../services/scheduler.py#L577)、[services/group/topic_block.py:97](../../services/group/topic_block.py#L97) | `grep -nE 'reply_to_message_id' services/scheduler.py services/group/topic_block.py` | 单测：不传该参的旧 observe 调用行为不变 | `git checkout` 两文件 |
| **L0-3** | tracker 维护 `_msg_to_block: dict[str, dict[int,str]]`，`_apply` 时登记每条 msgid→block_id | [services/group/topic_block.py:59](../../services/group/topic_block.py#L59) | `grep -nE '_msg_to_block' services/group/topic_block.py` 新增结构 + 登记点 | 单测：observe 三条消息后反查表三键指向正确块 | `git checkout` |
| **L0-4** | `_attribute` 规则1/2 重写：有 reply_to_message_id 命中 `_msg_to_block` → 该块（O(1)，绕活跃度门）；轻校验背离则不盲并（护栏二） | [services/group/topic_block.py:121](../../services/group/topic_block.py#L121) | `grep -nE '_msg_to_block.get\|reply_to_message_id' services/group/topic_block.py` 命中归属分支 | 单测：bot 在 2 摊，reply 旧消息落其所在摊不落最新摊 | `git checkout` |
| **L0-5** | `representative_speaker`/`representative_message_id` 由反查命中消息的说话人（边 source）推出，弃 `set` 末位序 | [services/group/topic_block.py:45](../../services/group/topic_block.py#L45) | `grep -nE 'representative_speaker\|representative_message_id\|reversed\(list' services/group/topic_block.py` | 单测：reply X 的消息锚到 X，不锚 set 里碰巧最后插入的 Y | `git checkout` |
| **L0-6** | `mark_bot_involved` 加 `block_id` 入参，按 `slot.firing_block_id` 精确标记 bot 应答块（替"取最活跃"） | [services/scheduler.py:2230](../../services/scheduler.py#L2230)、[services/group/topic_block.py:183](../../services/group/topic_block.py#L183) | `grep -nE 'mark_bot_involved\|firing_block_id' services/scheduler.py services/group/topic_block.py` | **D2 测试**：模拟"bot 应答 b1 期间 b2 变最活跃"，断言 `mark_bot_involved` 仍标 b1（b1.bot_involved=True 且 b2 不变） | 改回无 block_id 签名 + `git checkout` |

**Wave L0 收口**：① 4 个行为锚测试（stale 不捞旧块 / reply skip-connecting / @ join / bot-involved 优先）保持绿；② 新增反查精确归属 + 锚人=边 source + 缺陷6 D2 不污染测试全绿；③ `observe` 旧签名向后兼容（不传 reply_to_message_id 行为不变，单测证明）；④ `uv run ruff check` + `uv run pyright` 改动文件 0 新增错误；⑤ D1 §6 同模式扫描结果贴回（router 多处 reply_sender_id 提取点说明为何只改喂 tracker 链路）。


### 3.2 Wave L1 — 归属瀑布→线性打分：同说话人降软特征 + participants 带时间戳

目标：把规则瀑布（reply>@>同说话人>相似度）改为线性加权打分，同说话人硬规则降为软特征（文献实测仅 52.2% 成立），消除 mega-block 坍缩。**依赖 L0 的反查索引已就位（reply 短路仍走 L0）。**

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | 验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **L1-1** | `participants: set[str]` → `dict[str, float]`（QQ→last-speak ts），可表达"离开"；打分用最近性 | [services/group/topic_block.py:39](../../services/group/topic_block.py#L39) | `grep -nE 'participants: \|participants\[' services/group/topic_block.py` 改为 dict | 单测：participants 带时间戳，旧成员随时间权重衰减 | `git checkout` |
| **L1-2** | `_attribute` 规则瀑布 → 线性打分：reply/@ 保留为高权或短路（L0 已短路 reply），同说话人降为低权软特征 + 时间衰减 + 相似度 | [services/group/topic_block.py:121](../../services/group/topic_block.py#L121) | `grep -nE 'score\|w_at\|w_spk\|w_t\|w_sim' services/group/topic_block.py` 命中打分式 | 单测：A 同人 120s 内转话题，不被"同说话人"硬粘回旧块 | `git checkout` |
| **L1-3** | `max(score) ≥ floor → 该块；else 开新块`；floor 与权重为模块常量（本批不进 config，F1） | [services/group/topic_block.py:_attribute](../../services/group/topic_block.py#L121) | `grep -nE 'floor\|>= .*score\|open.*new' services/group/topic_block.py` | 单测：多话题并发不坍缩成 mega-block（块数 > 1） | `git checkout` |

**Wave L1 收口**：① 4 个行为锚测试保持绿（特别是 reply skip-connecting / @ join 在打分式下仍成立）；② 新增"同人转话题不粘回""不坍缩 mega-block"测试全绿；③ 缺陷5（silent 保护被坍缩架空）随坍缩根因消除而自动缓解，加断言验证；④ ruff + pyright 0 新增错误。**注**：L1 重写 `_attribute` 核心、回归面最广，4 个行为锚是硬底线，任一变红即停、记 §5。

### 3.3 Wave L2 — 活跃度生命周期：EDMStream 衰减替 deque + 两层池复活（护栏一/三）

目标：用惰性求值的活跃度衰减替容量插入序淘汰；低活跃块移入 reservoir 冷却池（不删反查），候选池含 reservoir 保 PC 召回。**依赖 L0 反查（reservoir 块的 msgid 反查是复活命脉，护栏三）。**

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | 验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **L2-1** | `_blocks` 由 `deque(maxlen)` → `dict[block_id, TopicBlock]`；块加 `activity`/`last_access` 字段，`activity=a^(λΔt)·activity+1` 惰性求值（无定时器） | [services/group/topic_block.py:61](../../services/group/topic_block.py#L61)、[:112](../../services/group/topic_block.py#L112) | `grep -nE 'activity\|last_access\|decay\|a\*\*\|math.pow' services/group/topic_block.py` | 单测：7 并发话题不挤掉活跃 bot 块（按 activity 淘汰而非插入序） | `git checkout` |
| **L2-2** | `_active`/stale 硬窗 → 活跃度衰减到 `activity_floor` 移入 `_reservoir: dict[group_id, dict[block_id, TopicBlock]]`（冷却池，**不删 msgid 反查**，护栏三） | [services/group/topic_block.py:90](../../services/group/topic_block.py#L90) | `grep -nE '_reservoir\|activity_floor' services/group/topic_block.py` | 单测：低活跃块被引用（reply 命中反查）可从 reservoir 复活回活跃池 | `git checkout` |
| **L2-3** | 候选池 = 活跃池 ∪ reservoir（护栏一：不裁剪低活跃块，保 PC 召回）；省算力靠打分排序非裁剪 | [services/group/topic_block.py:_attribute](../../services/group/topic_block.py#L121) | `grep -nE 'reservoir\|candidate\|_active' services/group/topic_block.py` 命中候选生成 | 单测：安静摊（低活跃）接话能命中其块、不被误判新块 | `git checkout` |

**Wave L2 收口**：① 4 个行为锚测试保持绿（stale 不捞旧块语义改为"衰减后排序靠后"，断言等价行为）；② 新增衰减淘汰 + reservoir 复活 + 候选含 reservoir 三测试全绿；③ **护栏三硬证**：grep + 单测断言衰减块的 `_msg_to_block` 反查项**未被删除**（否则 reply 复活失效）；④ **护栏一硬证**：单测断言低活跃块仍在候选池内；⑤ 参数初值 `decay_a=0.998 / decay_lambda=1.0 / activity_floor`（标定对应旧 stale≈300s 等效衰减点，可放宽到 ~10min）为模块常量；⑥ ruff + pyright 0 新增错误。

### 3.4 Wave L3 — 句向量表征（可选，末位）：块质心替 last_text 单条

目标：块相似度从比 `last_text` 单条噪声升级为块质心（c-TF-IDF 或句向量增量平均），`similarity_backend` 可切 embedding。**默认 `"ngram"` 时 `centroid=None`、回退 last_text，零行为变更（F7）。**

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | 验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **L3-1** | 块加 `centroid` 字段（c-TF-IDF/vec 增量平均）；`backend="embedding"` 时维护质心，`"ngram"` 时为 None | [services/group/topic_block.py:155](../../services/group/topic_block.py#L155) | `grep -nE 'centroid\|c.tf.idf\|similarity_backend' services/group/topic_block.py` | 单测：块末尾噪声消息不再误开新块（质心稳定） | `git checkout` |
| **L3-2** | `SimilarityProvider` 加 embedding 实现，复用现有抽象与 `backend` 留口 | [services/similarity.py](../../services/similarity.py) | `grep -nE 'class .*SimilarityProvider\|backend\|embedding' services/similarity.py` | 单测：backend=ngram 行为与 L2 收口完全一致（基线）；embedding 切换可用 | `git checkout` |
| **CFG** | `TopicBlockConfig` 加 `decay_a/decay_lambda/reservoir_max/activity_floor/similarity_backend`，带 admin json_schema_extra | [kernel/config.py](../../kernel/config.py)（`class TopicBlockConfig`） | `grep -nE 'decay_a\|reservoir_max\|activity_floor\|similarity_backend' kernel/config.py` | 默认值 == 现行为基线；admin 配置页可见（前端零改动） | `git checkout` |

**Wave L3 收口**：① backend=ngram 时全测试与 L2 收口逐字节等价（零行为变更，F7）；② backend=embedding 切换后块质心稳定、末尾噪声不误开新块；③ config 5 字段默认值复现 L2 基线行为，admin 页可见；④ 4 个行为锚测试仍绿；⑤ ruff + pyright 0 新增错误。**注**：L3 是可选末位，若 embedding 实现成本超预期，可只交付 CFG（暴露 tunable）+ c-TF-IDF 质心、embedding 留待后续，但须在 §5 标注降级决策并经验收人确认。


---

## 4. 总收口判据（全 Wave 完成后）

- **L0**：reply 命中 `message_id→block` 反查 O(1) 精确归属；锚人 = 边 source（弃无序 set）；mark_bot_involved 按 firing_block_id 标记，D2 不污染。解缺陷 2/3/6。
- **L1**：归属为线性打分，同说话人为软特征，多话题并发不坍缩 mega-block。解缺陷 1/5。
- **L2**：活跃度衰减替插入序淘汰；两层池 reservoir 复活；候选池含 reservoir。解缺陷 4，立护栏一/三。
- **L3**：块质心替 last_text 单条；backend 可切 embedding，默认 ngram 零行为变更。解缺陷 7。
- **三护栏全程守住**（grep + 单测双证据）：① 候选池含低活跃块（PC 召回）；② 引用边轻校验非硬真值；③ 衰减块 msgid 反查未删（复活命脉）。
- **4 个行为锚测试**（stale / reply skip-connecting / @ join / bot-involved 优先）全 Wave 后仍绿。
- **缓存红线守住**：锚点写入路径不变，hit% 零影响（grep 确认无话题块状态进缓存前缀）。
- `uv run ruff check` + `uv run pyright` 对改动文件 0 新增错误；`uv run pytest` 全绿（D5：先 `pkill -9 -f pytest`）。

---

## 5. 偏差表 + 回执（执行者回填）

**Wave 0 回执**（reasonix 填）：

| 项 | 实测结果 | 结论 |
| --- | --- | --- |
| §1 十五条锚点复验 | _（reasonix 回填实际行号/输出）_ | _（全对上→继续；任一不符→停，问）_ |
| 0.2 行为锚基线 | _（`tests/test_topic_block.py` passed 数）_ | _（每层后须 ≥ 此数且无 fail）_ |
| 0.3 similarity 留口现状 | _（SimilarityProvider 签名 + backend 留口）_ | _（L3 复用点确认）_ |

**Wave L0 / L1 / L2 / L3 执行回执**（reasonix 填，每 Wave 一张）：

| 项 | 实测结果 | 结论 |
| --- | --- | --- |
| Wave L0 | _（D1 grep 行号 + 验证命令输出 + 新增测试 + 回滚演练）_ | _（实现侧收口到验收点，等 D4 复核）_ |
| Wave L1 | _（同上）_ | _（同上）_ |
| Wave L2 | _（同上 + 护栏一/三硬证）_ | _（同上）_ |
| Wave L3 | _（同上 + backend=ngram 基线等价证据）_ | _（同上）_ |

**偏差表**（执行中遇到与本文不符时填，停下等验收）：

| Wave | 本文表述 | 实际 | 处理 |
| --- | --- | --- | --- |
| _（待填）_ | _（如行号漂移/命令报错）_ | _（实测）_ | _（记录→停→问验收人，不自行猜改）_ |

---

## 6. 搁置区（保留不砍，明确不在本批）

| 项 | 归属 | 为何搁置 | 解冻前置 |
| --- | --- | --- | --- |
| "纳入记忆"跨层接口（reservoir 溢出落 memory） | 实施清单 §0 / 审计 §9.1② | reservoir 溢出才落，非复活必经路径，避免过早耦合 memo | reservoir 容量压力达瓶颈再议 |
| L3 embedding 完整实现（若 Wave L3 降级为仅 c-TF-IDF） | 实施清单 §0/§5 | embedding 成本可能超本批预算 | c-TF-IDF 质心稳定后增量上线 |
| B2 Goffman 角色门阈值调整 | 实施清单 §0 / 审计缺陷5 | 缺陷5 靠 L1 根因消除，不动阈值 | L1 稳定后若仍有 silent 误判再议 |
| RWS 概率 / closing/at/followup bypass | 实施清单 §0 | 出本批范围，与话题块归属正交 | 另立项 |

---

## 7. 自审表（每 Wave 收口后勾）

| Wave | 代码改动 | D1 grep 贴回 | 单测/验证全绿 | 4 行为锚保持绿 | 三护栏守住（grep+单测） | ruff+pyright 0 新增 | 验收人确认 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Wave 0 | N/A | ☐ | ☐ | N/A | N/A | N/A | ☐ |
| L0（边模型+反查+缺陷6） | ☐ | ☐ | ☐ | ☐ | 护栏二（reply 轻校验） | ☐ | ☐ |
| L1（线性打分） | ☐ | ☐ | ☐ | ☐ | N/A | ☐ | ☐ |
| L2（活跃度+reservoir） | ☐ | ☐ | ☐ | ☐ | 护栏一/三（候选含低活跃+反查不删） | ☐ | ☐ |
| L3（句向量+CFG） | ☐ | ☐ | ☐ | ☐ | N/A | ☐ | ☐ |

> 说明：「三护栏守住」列——L0 验护栏二（reply 强先验非硬真值，背离不盲并）；L2 验护栏一（候选池含 reservoir 低活跃块）+ 护栏三（衰减块 `_msg_to_block` 反查项不删）；L1/L3 标 N/A。每层落地后 4 个行为锚测试（stale / reply skip-connecting / @ join / bot-involved 优先）必须保持绿，是回归底线。

---

## 8. 验收人复核要点（落地后按 D4 逐条用外部证据复核，非照单全收回执）

- **行为锚不破**：每层后亲自跑 `tests/test_topic_block.py`，确认 4 个核心场景测试绿，不照搬执行者"已绿"声明。
- **缺陷修复外部证据**：L0 后构造"bot 在 2 摊、reply 旧消息"场景，断言归属落正确摊（缺陷2/3）；L1 后构造"同人 120s 转话题"，断言不坍缩（缺陷1）；L2 后构造"低活跃摊被引用复活"（缺陷4）；缺陷6 D2 测试断言生成期不污染。
- **三护栏外部证据**：① grep + 单测断言候选池含 reservoir 块；② 单测断言 reply 轻校验背离时不盲并；③ grep + 单测断言衰减块 msgid 反查未删（reply 复活成立）。这是本重构的不可妥协约束，逐条验。
- **零行为变更（L3）**：backend=ngram 时与 L2 收口产物 diff 为空（F7），自己跑一遍 diff。
- **D2 cancel-path**：确认 L0-6 有"bot 应答 b1 期间 b2 变最活跃"的回归测试，断言 mark_bot_involved 仍标 b1。
- **缓存红线**：grep 确认话题块状态未进缓存前缀（锚点仍落 pending → 最后一条消息）。
- **同模式扫描（D1 §6）**：确认 router 多处 reply_sender_id 提取点（304/361/1563/1599/1957/1979 等）中，**只改了喂 tracker 的链路**（293→scheduler 577），其余图片重取/echo 用途未动，维护日志列出已扫描点。
