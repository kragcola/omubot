# 话题块边模型重构 — D3 实施清单（L0-L3 + 三护栏）

> 状态：2026-06-11 **立项中，未编码**。对应审计 [topic-block-multitopic-defects-audit-2026-06-11.md](../tracking/topic-block-multitopic-defects-audit-2026-06-11.md) §7.5（L0-L3）+ §8.5（三护栏）+ §9（四靶对标公式）。
> 范围决策（用户 2026-06-11 拍板）：**全量 L0-L3 + 三护栏**，**原地改造** `topic_block.py`（保留公共方法签名，新逻辑内部替换），靠现有 + 新增单测护栏，不加 config 双轨开关。
> 纪律：D3 四列迁移清单 + D1 同模式扫描 + D2 cancel-path + D4 完成证据。**接缝已逐处核实行号**（非猜测，见 §2）。
> 缓存红线：锚点仍落 pending → 最后一条消息（缓存前缀外），对 hit% 零影响（沿用 B1 设计 §7.2）。

## 0. 范围与不做

**做**：把 `TopicBlockTracker` 的"消息→规则瀑布→块归属"重构成**边模型**——维护"消息→前驱边 + `message_id→block` 反查、块=连通分量（union-find）"，并接入活跃度衰减生命周期、两层池复活、候选池两阶段、可选句向量表征。一举消解审计缺陷 1/2/3/4/5/6/7。

**分层落地（同一 PR 内按 L 顺序提交，每层独立全绿）**：
- **L0 边模型**：`message_id→block` 反查 + 引用 attribute 单消息 + 边带说话人。解缺陷 2/3，支撑单用户多块。
- **L1 归属瀑布→线性打分**：同说话人硬规则降为软特征（文献实测仅 52.2% 成立）。解缺陷 1。
- **L2 活跃度生命周期**：EDMStream 指数衰减 `activity=a^(λΔt)·activity+1` 替 deque 插入序淘汰 + stale 硬窗；两层池（活跃池 + 冷却 reservoir）复活。解缺陷 4，护栏一/三。
- **L3 句向量表征（可选，末位）**：`NgramSimilarityProvider`→embedding provider，块质心用 c-TF-IDF/增量平均。解缺陷 7。

**三护栏（贯穿 L1-L2）**：① 候选池不裁剪低活跃块（保 PC 召回）；② 引用边强先验非硬真值（broadcast/reframe 轻校验）；③ 衰减≠物理删除（reservoir 保 message_id 反查）。

**不做（本期）**：不改 RWS 概率本身；不改 closing/at/followup 既有 bypass；不改 B2 Goffman 角色门阈值（只修被块坍缩架空的缺陷5，靠 L1 根因消除）；不引训练模型；不改缓存断点；"纳入记忆"跨层接口后置（reservoir 溢出才落，非复活必经路径，审计 §9.1②）。

**前置依赖**：无。L0 所需 `reply.message_id` 已在 router 可得（router.py:1046），缺的只是透传。

## 1. 缺陷→层映射（审计对账）

| 缺陷 | 根因 | 修复层 | 机制（审计 §9 公式） |
| --- | --- | --- | --- |
| 2 reply message_id 真值丢弃 | router 不传被引用 msgid + 无反查索引 | L0 | `_extract_topic_block_signals` 加 `reply_to_message_id`；tracker 建 `_msg_to_block` 反查 |
| 3 representative_speaker set 序 | 锚人靠无序 set | L0 | 边天然带目标消息+说话人；锚人 = 边的 source，弃 set 顺序 |
| 1 participants set 只增→坍缩 | 同说话人硬规则先于相似度且不看文本 | L1 | 规则瀑布→线性打分，同说话人降为权重特征 |
| 4 deque 插入序淘汰 | 容量淘汰与 last_active 无关 | L2 | 按 `activity` 衰减值淘汰（EDMStream `a=0.998,λ=1`） |
| 5 silent 保护被坍缩架空 | 缺陷1 下游 | L1 | 根因（坍缩）消除后自动缓解 |
| 6 mark_bot_involved 竞态 | 取"当前最活跃"而非 bot 实际应答块 | L0 | 改按 `slot.firing_block_id` 精确标记（已存在，scheduler.py:196） |
| 7 相似度比 last_text 单条 | 质心是单条噪声 | L3 | c-TF-IDF/句向量块质心（BERTopic decay 机制） |

## 2. 接缝核实（已读代码，行号为证）

| 需要的数据/接口 | 来源（文件:行号） | 现状 |
| --- | --- | --- |
| 被引用消息的 message_id | `getattr(reply, "message_id", None)` router.py:1046 | router 已取用于图片重取，**`_extract_topic_block_signals` 未提取**，需补 |
| reply 信号提取函数 | `_extract_topic_block_signals` router.py:293-325 | 现提 sender/self/at，**缺 reply_to_message_id** |
| tracker.observe 入参 | scheduler.py:577-586 | 已传 message_id/reply_to_sender_id/reply_to_self/at_targets，**需加 reply_to_message_id** |
| 块归属核心 | `TopicBlockTracker._attribute` topic_block.py:121-158 | 规则瀑布，L0/L1 重写此处 |
| 块数据结构 | `TopicBlock` dataclass topic_block.py:33-53 | 加 `activity`/`last_access`/`predecessor`/反查；`participants` 改带时间戳 |
| 反查索引位置 | `TopicBlockTracker.__init__` topic_block.py:59-66 | 加 `self._msg_to_block: dict[int,str]` |
| 容量淘汰 | `deque(maxlen=self._max_blocks)` topic_block.py:112 | L2 改 dict + 按 activity 淘汰 + reservoir |
| 锚点代表消息 | `representative_message_id/speaker` topic_block.py:45-53 | L0 后由边推出，弃 set 顺序 |
| mark_bot_involved 竞态点 | scheduler.py:2228-2232 调用、topic_block.py:183-188 实现 | 改按 `firing_block_id` 标记 |
| firing_block_id 抓手 | `slot.firing_block_id` scheduler.py:196/1754 | 已存在，缺陷6 修复直接复用 |
| 相似度 provider | `NgramSimilarityProvider` services/similarity.py、`backend="embedding"` 已留口 | L3 复用抽象，加 embedding 实现 |
| config 字段 | `TopicBlockConfig` config.py:1970-2001 | 加 `decay_a`/`decay_lambda`/`reservoir_max`/`activity_floor`/`similarity_backend` |
| 现有单测 | tests/test_topic_block.py（observe + now= 注入 + 断言 message_ids/anchor） | 全部须保持绿；新增衰减/复活/cancel 测试 |

## 3. 改动清单（四列：旧 → 新 / 文件 / 类型 / 回归）

| # | 旧行为 | 新行为 | 文件 | 类型 | 回归验证 |
| --- | --- | --- | --- | --- | --- |
| **L0-1** | reply 只提取 sender_id | 加提取 `reply_to_message_id`（`getattr(reply,"message_id",None)`） | `kernel/router.py:293-325` | 改 helper | router 单测断言透传 msgid |
| **L0-2** | observe 收 reply_to_sender_id | observe 加可选 `reply_to_message_id`（默认 None，additive） | `services/scheduler.py:577`、`services/group/topic_block.py:97` | 改签名（向后兼容） | 旧调用不传仍工作 |
| **L0-3** | 无反查索引 | tracker 维护 `_msg_to_block: dict[int,str]`，`_apply` 时登记每条 msgid→block | `services/group/topic_block.py` | 新增结构 | 单测：reply 旧消息精确归该块 |
| **L0-4** | reply_to_self 返回第一个 bot 块 | 有 reply_to_message_id → 反查命中块（O(1)，绕活跃度门）；轻校验背离则标记不盲并（护栏二） | `services/group/topic_block.py:_attribute` | 重写规则1/2 | 单测：bot 在 2 摊，reply 落正确摊 |
| **L0-5** | representative_speaker 用 set 序 | 锚人 = 反查命中消息的说话人（边 source） | `services/group/topic_block.py:51-53` | 重写 | 单测：reply X 的消息锚到 X 不锚 Y |
| **L0-6** | mark_bot_involved 取最活跃块 | 按 `slot.firing_block_id` 精确标记 bot 应答块 | `services/scheduler.py:2228`、`topic_block.py:183` | 改签名（加 block_id 入参） | D2 测试：生成期别摊更活跃不污染 |
| **L1-1** | 规则瀑布 reply>@>同说话人>相似度 | 线性加权打分：reply/@（强先验保留为高权或短路）+ 同说话人（软特征）+ 时间衰减 + 相似度 | `services/group/topic_block.py:_attribute` | 重写核心 | 单测：A 同人 120s 内转话题不被粘回旧块 |
| **L1-2** | participants: set 只增 | participants: dict[str,float]（带 last-speak 时间戳），打分用最近性 | `services/group/topic_block.py:39` | 改结构 | 单测：块不再坍缩成 mega-block |
| **L2-1** | deque(maxlen) 插入序淘汰 | dict + 活跃度 `activity=a^(λΔt)·activity+1`（惰性求值，无定时器） | `services/group/topic_block.py:112` | 改结构+算法 | 单测：7 并发话题不挤掉活跃 bot 块 |
| **L2-2** | stale_seconds 硬窗归档 | 活跃度衰减到 `activity_floor` → 移入 reservoir（冷却池，不删 msgid 反查） | `services/group/topic_block.py:_active` | 改生命周期 | 单测：低活跃块被引用可复活 |
| **L2-3** | 候选 = active（已隐式只取非 stale） | 候选池含 reservoir 块（保 PC 召回，护栏一）；省算力靠打分排序非裁剪 | `services/group/topic_block.py:_attribute` | 改候选生成 | 单测：安静摊接话能命中其块 |
| **L3-1** | 相似度比 last_text 单条 | 块质心：c-TF-IDF/句向量增量平均；similarity_backend 可切 embedding | `services/similarity.py`、`topic_block.py:155` | 新增 provider | 单测：块末尾噪声不误开新块 |
| **CFG** | 4 个 tunable | 加 `decay_a`/`decay_lambda`/`reservoir_max`/`activity_floor`/`similarity_backend` + admin json_schema_extra | `kernel/config.py:1970` | 新增 config | 默认值 == 现行为基线；admin 配置页可见 |

## 4. 数据结构终态（原地改造后的 TopicBlock / Tracker）

```text
TopicBlock (dataclass):
    block_id: str
    message_ids: list[int]                  # 保留（反查值 + 锚点）
    participants: dict[str, float]          # L1：QQ→last-speak ts（替 set，带时间戳，可表达"离开"）
    predecessor_msg: dict[int, int]         # L0：本块内 msgid→被引用的前驱 msgid（边）
    activity: float                         # L2：EDMStream timely-density
    last_access: float                      # L2/靶四：last-ACCESS（被引用复活刷新，非 last-create）
    last_text: str                          # 保留（L3 上线前的回退）
    centroid: <c-TF-IDF / vec>              # L3：块质心（可选，backend=ngram 时为 None）
    bot_involved: bool
    at_message_id: int | None

TopicBlockTracker:
    _blocks: dict[group_id, dict[block_id, TopicBlock]]   # 活跃池（deque→dict）
    _reservoir: dict[group_id, dict[block_id, TopicBlock]] # L2：冷却池（护栏三，保反查）
    _msg_to_block: dict[group_id, dict[int, str]]          # L0：反查索引（活跃+冷却都登记）
    # 归属打分（L0 短路 → L1 线性）：
    #   1. reply_to_message_id 命中 _msg_to_block → 该块（轻校验，护栏二）
    #   2. else 线性打分 over (活跃池 ∪ reservoir)：     ← 护栏一：候选含低活跃
    #        score = w_at·@命中 + w_spk·同说话人最近性 + w_t·时间衰减 + w_sim·质心相似度
    #      max(score) ≥ floor → 该块；else 开新块
```

## 5. 参数初值（审计 §9.5，实现期可调）

| 参数 | 初值 | 依据 |
| --- | --- | --- |
| `decay_a` / `decay_lambda` | 0.998 / 1.0 | EDMStream（审计 §9.1①），标定使 2min freshness 显著、1h 趋零 |
| `activity_floor`（移入 reservoir 阈值） | 待标定 | 对应旧 stale_seconds≈300s 的等效衰减点；放宽到 ~10min（§7.4） |
| `reservoir_max` | ~3×max_blocks | reservoir 溢出才落 memory |
| 打分权重 `w_at/w_spk/w_t/w_sim` | reply/@ 短路或高权；同说话人低权（实测 52.2%） | 审计 §7.3② |
| `similarity_backend` | "ngram"（默认），L3 切 "embedding" | 渐进上线 |
| `max_blocks` | 6（小群）/ 10（大群） | 并发会话 ≤10 占 97.3%（§7.4） |

## 6. D2 cancel-path 与 D1 同模式扫描（立项即登记，编码时补全）

**D2（cancel-path 测试）**：`tracker.observe` 是纯 CPU 同步、无 await，本身不被 cancel。风险点在 **`mark_bot_involved` 与生成期的时序**（缺陷6）：生成跨秒期间别摊更活跃，旧实现标错块。L0-6 改为按 `firing_block_id` 标记后，须加测试：模拟"bot 应答 b1 期间 b2 变最活跃"，断言 `mark_bot_involved` 仍标 b1（外部可观察：b1.bot_involved=True 且 b2 不变）。

**D1（同模式扫描）**：reply_sender_id 提取在 router 有**多处**（router.py:304、361、1563、1599、1957、1979）。L0 只需在喂 tracker 的链路（293-325 → scheduler.py:577）加 message_id；其余点是别的用途（图片重取/echo），**不改**，但维护日志须列出已扫描这些点并说明为何不动。

## 7. 风险与回滚

- **回滚路径**：本重构原地改 `topic_block.py`，回滚 = `git revert` 该 PR。`topic_block.enabled` 仍是总闸（关闭则整模块旁路，行为回到无话题块）。
- **最大风险**：L1 重写 `_attribute` 核心，回归面广。缓解：现有 4 个核心场景测试（stale 不捞旧块 / reply skip-connecting / @ join / bot-involved 优先）必须保持绿，作为行为锚。
- **缓存红线**：锚点写入路径不变（`add_pending_trigger` → pending → 缓存前缀外），hit% 零影响。
- **NapCat 红线**：本重构纯 bot 侧内存逻辑，不触 NapCat、不需重启容器。

## 8. 验收（D4 完成证据）

声明完成时给出：① `uv run pytest`（D5 先 `pkill -9 -f pytest`）全绿数；② ruff + pyright clean；③ 新增测试覆盖每层缺陷（L0 反查精确归属、L1 不坍缩、L2 复活、D2 不污染）；④ 同模式扫描结果（§6 D1）；⑤ 回滚路径（§7）。
