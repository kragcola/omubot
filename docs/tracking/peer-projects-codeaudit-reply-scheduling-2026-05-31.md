# 同类项目代码审读 → omubot 回复调度同类解决方案

> 2026-05-31 调研。**全部结论来自三个成熟同类开源项目的 .py 源码审读，未读其 README/文档**（拉取至 /tmp/rws_refs/）。目的：用真实代码佐证「群聊 AI 何时回 / 防 bot 互刷 / 弱回复 / 防刷屏」该怎么做，给 omubot 定同类方案，否决此前"开 pair_guard 配置 + 弱回复打补丁"的临时修法。
> 审读对象：① `nonebot-plugin-moellmchats`（MoE 调度群聊 bot）② `nonebot-plugin-llmchat`（单文件群聊 bot）③ `LangBot`（多平台 pipeline 架构，最系统化）。

## 0. 三句话结论

1. **三个成熟项目"何时回"全是规则/概率，没有一个用 logistic 打分**——omubot 的 RWS 是这一类里最复杂的，而复杂度没换来对应收益（见 [rws-necessity-reaudit](rws-necessity-reaudit-2026-05-31.md)）。
2. **三个项目里两个完全没有 bot↔bot 防循环，第三个（LangBot）也只在个别平台 adapter 入口各写一份、覆盖不全**——说明这是行业普遍盲区，且正解是**消息入口的集中式 self/bot 硬过滤**，不是 omubot 现在那种"pair_guard 配置项默认关、靠登记 QQ 清单"的脆弱机制。
3. **"弱回复/要不要回"的成熟做法是 prompt 层逃生口（让 LLM 输出 `<botbr>` 表示不回）+ 攒批合并**，而非 omubot 这种"closing/companion 多档 + thinker 多路径"的重型设计——后者恰恰因为和 @ 路径不相交而失效。

## 1. 三项目"何时回"机制（代码实证）

| 项目 | 触发判定 | 概率插话 | 防刷屏 | 攒批合并 |
| --- | --- | --- | --- | --- |
| **moellmchats** | `to_me() & chat_rule`（@/昵称命中即回，群恒回） | 有但**已注释禁用**（`__init__.py:86` `random.randint(1,100)==1`） | **per-user CD 时间戳 dict + asyncio.sleep 排队**（`chat_runtime.py:40-53`，默认 120s），冷却内最多排 1 个、多余劝退 | 无（CD 内第二条直接丢弃） |
| **llmchat** | 集中在一个 `is_triggered` Rule（`__init__.py:188-244`）：黑名单→前缀忽略→`is_tome()`→`random.random()<prob` | **有且启用**：per-message 掷骰，默认 0.05，**per-group 可运行时调** | **无任何冷却/限频**；靠 per-context 串行队列 + `asyncio.sleep(2)` 段间隔 | **past_events deque(maxlen=10)**：所有群消息进缓冲，触发时回放最近 N 条合并成一次 LLM 调用 |
| **LangBot** | **可插拔响应规则**（resprule）：atbot/random/prefix/regexp，**任一命中即回(OR)**，全不中则 INTERRUPT 丢弃 | random 规则 = `random.random()<rate`（`rules/random.py:19`，per-message 无状态掷骰） | **固定窗口限速**（`fixedwin.py`，per-session，drop/wait 两策略），且只夹住 LLM 调用段 | **debounce 聚合器**（`aggregator.py`）：安静 delay(默认1.5s,钳[1,10]) 后一次性发，10 条上限兜底 |

**共性铁律（三项目一致）**：
- **触发判定与"打分"彻底解耦**。moellmchats 的分类器只用于"选哪个模型/工具"，不参与"要不要回"；LangBot 的规则是布尔 OR；llmchat 是掷骰。没有一个把"该不该回"做成多特征加权。
- **概率插话就是 `random.random() < p` 一行**，per-message 无状态。要么禁用(moellmchats)，要么 per-group 可调(llmchat/LangBot)。
- **便宜判断在前、贵资源在后**：LangBot 把 resprule 放 pipeline 第 1 阶（不中直接 INTERRUPT），限速放第 6 阶只夹 LLM。这套分层 omubot 的 scheduler bypass 也有雏形，但被 RWS/B2/necessity 多层判断搅乱了（见 [reply-pipeline-coherence-audit](reply-pipeline-coherence-audit.md)）。

## 2. 防 bot↔bot 循环（代码实证，行业盲区）

| 项目 | 防循环机制 | 实现 |
| --- | --- | --- |
| moellmchats | **完全没有** | 只有 `to_me()` 隐式门槛；两 bot 互@会无限对话，仅受 120s per-user CD 限速 |
| llmchat | **完全没有** | 不识别发送方是否 bot，无 ignore_self；`random_trigger_prob>0` 时两 bot 同群可 ping-pong；只能手动拉黑对方 QQ |
| **LangBot** | **部分，且覆盖不全** | pipeline 层无；只在 **Telegram adapter**（`telegram.py:209` `if from_user.is_bot: return`）和 **Discord adapter**（`discord.py:852` `if author.id==self.user.id or author.bot: return`）各写一份入口硬过滤；**aiocqhttp(QQ)/slack/wecom 等其余平台没有** |

**关键洞察**：
- LangBot 唯一做对的两处，都是**消息流入口的 hard filter**：① `author.id == self.user.id`（忽略自己）② `author.bot`（忽略任何 bot 账号）。**这是正解的形态**——在最上游、基于发送方身份、无条件丢弃，不依赖配置清单。
- **三项目都印证：靠"@ 触发"做隐式门槛挡不住 bot 互@**（对方 bot 一旦 @ 你/带你昵称就触发）。omubot 的 pair_guard 即便开启、登记了对方 QQ，本质还是"靠维护一份已知 bot 清单"——和 Discord 的 `author.bot`（平台直接告诉你对方是不是 bot）比，是脆弱的：换一个新 bot、对方改 QQ 就漏。
- omubot 现状更差:pair_guard **默认 `enabled=False`**,等于三项目里"完全没有"那一档,叠加 @ 走 force_reply 必答 → 这就是 P0 循环反复复发的结构原因。

## 3. 弱回复 / "要不要回"的成熟做法（代码实证）

- **llmchat 的 `<botbr>` 逃生口（最值得学）**：prompt 里直接告诉 LLM"不想回就只输出一个 `<botbr>`"（`__init__.py:381`），输出 `<botbr>` 即分段为空/不发（`send_split_messages` `__init__.py:307-319`）。**把"要不要回/回几条"下放给 LLM 在 prompt 层决定，零硬编码判断分支**。
- **moellmchats 的输入弱→卖萌兜底**：@ 了但无文字 → 随机一句卖萌文案不走 LLM（`__init__.py:250-255`）。轻量、确定性。
- **分段+递增延迟拟人**：moellmchats `2 + len/3` 秒延迟、`MAX_SEGMENTS=5` 截断（`llm_api.py:235-280`）；llmchat 段间 `sleep(2)`；都是流式回调里顺手做，不需要独立调度队列。

**对照 omubot**：omubot 的弱回复是 closing/companion 多档 + thinker 多路径 + scheduler bypass，**重得多**,且审计已证它与 @ 路径结构性不相交（force_reply 跳过 thinker → closing 永不触发）。三项目的做法说明:**"要不要实质回"用一个 prompt 逃生口（`<botbr>`）比一套多档状态机更简洁、且不会有路径不相交的盲区**。

## 4. omubot 同类解决方案（基于代码审读定，非打补丁）

按"三项目都验证有效 + 修 omubot 已暴露的结构问题"排序：

### S1 — 防 bot↔bot 循环：改成消息入口集中式硬过滤（治 P0 复发根）
- **不再依赖 pair_guard 的"登记已知 QQ 清单 + 默认关"**。学 LangBot/Discord 的入口 hard filter，在 `kernel/router.py` 群消息入口加**集中式**判定：① OneBot 事件能拿到的 bot 标志/sub_type；② sender_id == self_id（忽略自己）；③ **行为兜底**——"同会话短时间内对方与我已互@ ≥N 轮"则熔断(这是三项目都没有、但 omubot 因 bot 多发被反复咬，值得加的一层)。
- 入口过滤先于 is_addressed/trigger（omubot 的 `_maybe_drop_pair_guard` 已在 router.py:1028 正确位置，问题只是**判定太弱+默认关**）。
- 这比"开 pair_guard enabled + 填 QQ 清单"根本:不靠人工维护清单。

### S2 — "要不要实质回"：引入 `<botbr>` 式 prompt 逃生口，替代弱回复多档状态机
- 学 llmchat:在主 prompt 给 LLM 明确逃生口"无实质可说就只回 `<botbr>`/空",输出空即不发。**force_reply 路径也走这个**——这样即便被 @ 必答,LLM 也能"礼貌不接车轱辘话",直接解决"缓一下/接住你"敷衍套话(那正是 force_reply 逼回 + 无逃生口的产物)。
- 这一步让 omubot 的 closing/companion 弱回复可以**大幅简化**:大部分"弱回复"诉求由 prompt 逃生口承担,不需要 thinker 多路径判 light_kind。

### S3 — 触发分层回归"便宜在前、贵在后"，RWS 退为灰区打分器（呼应通道审计分裂点 A）
- 学 LangBot 的 resprule 分层 + omubot 已有的 scheduler bypass:**强规则在前**(@必回/黑名单/bot过滤/限频兜底)→ **灰区才用打分**(非寻址、要不要插话)。
- RWS **不取代规则,只管灰区**——这正是 [rws-activation-plan](rws-activation-plan-2026-05-31.md) 与本调研一致的结论:三项目证明"该不该回"主体是规则,打分只在"像群友一样自然插话"的灰区有价值。

### S4 — 攒批合并:复用 omubot 已有 MessageCoalescer,对齐 debounce 范式
- omubot 已有 `services/coalesce.py` `MessageCoalescer`(对应 LangBot aggregator)。审读确认三项目都靠"攒批合并成一次 LLM 调用"天然抑制刷屏 + 省 token + 防答非所问。确认 omubot coalesce 是否启用、delay/上限是否对齐(LangBot:1.5s/10条)。

## 5. 与既有 omubot 设施对照（避免重复造轮子）

| 能力 | 三项目做法 | omubot 现状 | 建议 |
| --- | --- | --- | --- |
| 攒批合并 | LangBot aggregator(debounce)、llmchat past_events | **已有** `MessageCoalescer`(services/coalesce.py) | 复用,核对 delay/上限 |
| 防 bot 循环 | LangBot 入口 `author.bot`/`==self` | **有 pair_guard 但默认关、靠QQ清单** | 改造成入口硬过滤+行为熔断(S1) |
| 弱回复 | llmchat `<botbr>` prompt 逃生口 | closing/companion 多档状态机(与@路径不相交、失效) | 引入逃生口,简化状态机(S2) |
| 触发打分 | 三项目都无(规则/掷骰) | RWS logistic(空壳恒等) | 退为灰区打分,规则在前(S3) |
| 限速 | LangBot 固定窗口 per-session | LLM API 级限速有,**群触发级限速无** | 评估是否要 per-group 触发限频兜底 |
| 分段/拟人延迟 | 三项目流式回调里做 | omubot 已有 humanizer/segmentation | 已对齐,无需动 |

## 6. 诚实定性

- **omubot 在这一类项目里是"过度工程"的一端**:别人用一行 `random.random()<p` + 一个 `to_me()` + 一个 prompt 逃生口解决的事,omubot 用了 RWS logistic + B2 角色门 + necessity_gate + closing/companion 多档 + wait 兜底。复杂度带来的是**多层互相否决、路径不相交、P0 反复复发**(见三份审计)。
- **但 omubot 的方向(拟人、按气氛插话、记忆/关系驱动)比三个参照项目都更高级**——参照项目都是"指令/触发型",不追求"像群友"。所以**不是要把 omubot 削成 llmchat**,而是:**用三项目验证过的简洁底座(入口硬过滤防循环 + prompt 逃生口 + 规则在前)兜住"必须对"的部分,把打分/拟人只用在真正的灰区**。
- **最该立刻做的是 S1(防循环入口硬过滤)**:它治 P0 复发,且三项目代码一致指向"基于发送方身份在入口无条件过滤"才是正解,而非 omubot 现在的脆弱配置项。

**待用户决策**:是否按 S1→S2→S3→S4 推进?S1 是 P0、独立、可立即做(且比"开 pair_guard 配置"更根本)。每项实施前出 D3 清单。
