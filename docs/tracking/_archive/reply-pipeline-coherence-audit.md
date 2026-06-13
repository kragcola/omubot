# 回复通道一致性考察（reply-pipeline coherence audit）

> 2026-05-31 全代码考察（读 `services/scheduler.py` notify/arbiter、`services/llm/client.py` chat、`kernel/router.py` 入口；不靠记忆）。**只考察、不改代码。**
> 缘起：连续多次修 ratified floor / wait 兜底 / necessity_gate 误杀，都是在给同一处架构分裂打补丁。停下来盘一次：现在"要不要说话"到底被判了几次、判据是否一致、会不会互相否决。

## 0. 结论先行

**不是"多个互不相关的通道"——所有路径串在一条链上。但链上"要不要说话"这件事被三个机制各判一遍（RWS / B2 角色门 / necessity_gate），判据重叠、"被寻址"定义不一致、且能互相否决。** 你看到的"reply 后无反应"（necessity_gate 覆盖 thinker 的 reply）就是这个分裂的直接症状。每个补丁都在增加新的交互面，不可持续。本文给出现状全图 + 三个分裂点 + 收敛方向。

## 1. 入口分叉（合理，非问题）

| 入口 | matcher | 去向 | 决策性质 |
| --- | --- | --- | --- |
| 私聊 | `private_chat` priority=10, rule=to_me, block | 直接 `chat(force_reply=...)`，**绕过 scheduler** | 私聊本就必答，无需 fire 决策 |
| 群聊 | `group_listener` priority=1, non-block | `_collect_group_context` → `scheduler.notify` | 走下面的两层决策 |

私聊/群聊分叉是干净的（私聊无 fire 问题）。**所有分裂都在群聊路径内。** 下面只谈群聊。

## 2. 群聊回复通道全图（读代码还原）

```text
入站群消息
 │
 ▼ [router._collect_group_context]
 │   upstream_filter（丢命令噪声）
 │   _check_reply（get_msg→若 reply 的是 bot 消息则 to_me=True）+ _check_at_me
 │   is_addressed = event.is_tome()  ← @bot 或 reply-bot 或昵称命中
 │   建 trigger: at_mention / closing / directed_followup / correction / bilibili / qq_interaction
 │   reply_workflow shadow gate（评 is_addressed/reply_to_bot/followup… **基本只 log，不决策**）
 │   timeline.add（消息入库）
 │
 ▼ scheduler.notify ══════════ 决策层 ① "要不要 fire" ══════════
 │   topic_tracker.observe（喂话题块，旁路）
 │   proactive=None 且非强信号 → skip
 │   ┌─ 强信号 bypass（各自独立 if 分支，直接 _fire）:
 │   │    is_at      → arbiter completeness wait → fire   （第③层等待）
 │   │    directed_followup → fire
 │   │    correction → fire
 │   │    closing    → dedup(closing_done)+cooldown(last_light_time) → fire
 │   │    video_always → fire
 │   └─ 概率路径（非寻址默认）:
 │        at_only/busy/interval → skip
 │        threshold = talk_value × skip_boost × mood_mult × time_mult
 │        B2 role = _receiver_role(addressed/ratified/overhearer)
 │           overhearer + silent → skip（B2）
 │           ratified → threshold = max(threshold, ratified_floor)（B2）
 │        roll = random(); old_decision = roll < threshold
 │        RWS → 若 rws_primary: decision = rws.decision（**盖掉 old_decision**）
 │        ratified_floor 再 OR 一次（RWS 后补，因为 RWS 无视 threshold）
 │        decision? → _fire : skip(consecutive_skip++)
 │
 ▼ _fire → _do_chat → llm.chat() ═══ 决策层 ② "要不要回 / 回什么" ═══
 │   force_reply（at/followup/correction/video/closing/wait兜底）→ 跳过 thinker，直奔生成
 │   非 force_reply:
 │     text_preflight → 纯标点/单字/单emoji → return None（第④个"别说话"判据）
 │     thinker → action(reply/wait/light_reply) + reply_necessity + light_kind
 │     necessity_gate → reply + necessity=low + trigger is None → **覆盖成 wait**（第⑤个）
 │     thinker_action==wait → return None（+ @ wait 兜底重排）
 │     closing light_reply → 终止 token 短路（on_segment + return None）
 │     instruction_gate → DENY 短路 / ALLOW 注入 hint
 │   main LLM 生成 → 分段 → on_segment 发出
 │     （生成期间 arbiter_b_monitor 轮询，可中断未发段）
 │
 ▼ 发送 + mark_bot_involved（B2）+ wait_deferrals 复位
```

## 3. 三个分裂点（问题所在）

### 分裂点 A：「要不要说话」被判三次，判据重叠

同一个"该不该开口"的问题，在链上被三个机制各自 judge：

| 机制 | 位置 | 判据 | 输出 |
| --- | --- | --- | --- |
| **RWS** | 决策层① 概率路径 | addressee/eot/mood/time/skip/hawkes/familiarity 加权 logistic | fire / skip |
| **B2 角色门** | 决策层① 概率路径 | 话题块参与（addressed/ratified/overhearer） | silent / floor / pass |
| **necessity_gate** | 决策层② thinker 后 | reply_necessity(high/med/low) + trigger 有无 | reply / 覆盖成 wait |

三者判据**重叠**（都在回答"这条值不值得回"），却**分散在两层、用不同信号、互不知情**。RWS 在层①说 fire，necessity_gate 在层②可以推翻成 wait——**层②否决层①**，没有任何协调。

### 分裂点 B：「被寻址」有两套互不一致的定义

| 定义方 | 判"被寻址/有回应义务"的依据 |
| --- | --- |
| 决策层① B2 `_receiver_role` | addressed = is_addressed/reply_to_self/at_self/有 trigger；**ratified** = bot 在该话题块参与过；overhearer = 都不是 |
| 决策层② necessity_gate | 仅 `trigger is not None`（addressed_exempt）——**不认 ratified、不认 `last_assistant_to_user`** |

后果（01:20 实测）：用户"眼皮打架你就打回去"直接回 bot 上一句（`last_assistant_to_user=True`）——
- 层① 视为 **ratified**（该回，给了 floor）→ fire ✅
- 层② necessity_gate 视为 **"无 trigger=没被寻址"**（可压）→ 覆盖成 wait → **没回** ❌

**同一个"延续对话"，两层判断相反。** 这就是"reply 后无反应"的根因。先前修的 ratified floor、wait 兜底也都是这个不一致的不同切面。

### 分裂点 C：概率路径内部的半死双通道

`old_decision = roll<threshold` 与 `rws.decision` 并存，`rws_primary` 时 RWS 直接盖掉前者。于是：
- 前面那一大段 `threshold = talk×skip×mood×time_mult` 计算在 RWS 主导时**半失效**（threshold 不再决定结果）。
- B2 的 ratified_floor 不得不**写两遍**（一遍改 threshold 给 old_decision 用、一遍在 RWS 后 OR）——正是因为这个双通道，单写一处覆盖不全。
- time_mult/mood_mult 这些"节奏控制"信号有的进了 RWS 特征、有的只作用于 old_decision，**职责未理清**。

## 4. 还有几处"别说话"判据散落（非全部相关，列全以免遗漏）

除上面三个，链上还有独立的"短路不回"点，它们各有合理职责、但与上面三者无统一协调：

- `text_preflight`（层②最前）：纯标点/单字/单 emoji → return None。低信号过滤，合理但又是一处独立 gate。
- `at_only / busy / interval` skip（层①）：硬规则，合理。
- `closing dedup+cooldown`（层①）：closing 专用节制，合理。
- arbiter completeness（第③层，@ 专用）：fire 前等"说完"。
- arbiter_b_monitor（生成期）：中断未发段。

这些大多职责单一、不与 A/B/C 直接冲突，但合起来意味着**"要不要/能不能说话"的判据散布在 ≥6 处**，没有单一权威。

## 5. 收敛方向：单一「回应义务」决策

理论锚（前述调研一致）：Goffman 参与框架——回应义务是**一个**由"我在这条消息里的接收角色"决定的量，不该被切成三处各判一次。

**目标架构**：把"要不要说话"收敛成**决策层①唯一一次裁定**，产出一个统一的「回应义务强度 / ResponseClass」；决策层②（chat）**只负责"说什么"，不再 litigate"要不要"**。

```text
现状（分裂）:
  层① RWS + B2        →fire→   层② thinker + necessity_gate（可再否决）→ 可能不回

目标（收敛）:
  层① 统一裁定 obligation：
       角色(addressed/ratified/overhearer) × 信号(RWS 特征) × 节制(冷却/skip)
       → 单一输出: MUST_REPLY / MAY_REPLY(prob) / LIGHT / SILENT
  层② 按 obligation 执行：
       MUST/MAY 命中 → thinker 只决定 reply 的"内容/retrieve/light_kind"，**不得翻成 wait**
       （wait 仅保留"等用户说完"的延迟语义，由层①的 arbiter 等待承担，不再是 thinker 的否决权）
```

**关键改动点**：
1. **necessity_gate 并入层① 的 obligation 计算**，不再在 chat 里二次否决。它的"刷存在感→压低"诉求，等价于"overhearer/低相关 → MAY_REPLY 概率更低"，本就该和 B2 角色、RWS 同处一层。
2. **统一"被寻址"定义**：层① 的 `_receiver_role` 是唯一真相源（含 ratified、`last_assistant_to_user`）。necessity_gate 若暂时保留，其豁免必须读同一个 role，而非自己的 `trigger is not None`。
3. **消除概率双通道**（分裂点 C）：rws_primary 下让 RWS 成为唯一概率裁决，threshold 系数（mood/time/floor）要么全进 RWS 特征、要么明确只作 RWS 的输入，不再有 `old_decision` 影子路径。

## 6. 落地路径（分阶段，每阶可独立验证）

| 阶段 | 动作 | 风险 | 验证 |
| --- | --- | --- | --- |
| **C0（止血，最小）** ✅完成 | necessity_gate 豁免条件加 `last_assistant_to_user` / ratified——即"用户在回 bot 上一句"不被降级。**只补豁免，不改架构。** | 低 | 复现 01:20"眼皮打架你就打回去"应回；全量 pytest |
| **C1（统一被寻址）** ✅完成 | necessity_gate 改读层① 的 `_receiver_role` 结果（经 `ctx.extra["receiver_role"]` 传入），删掉它自己的 `trigger is None` 判据 | 中 | role=ratified/addressed 时绝不降级的回归 |
| **C2（义务收敛）** ⛔重定性，不做 | ~~把 necessity 信号上移进层①~~——**架构上不可行**：`reply_necessity` 是 thinker LLM 的输出，thinker 只在层②（chat）运行，层① 拿不到它的输出。强行上移等于在层① 再跑一次 thinker（双 LLM 调用）。**C1 已实现 C2 的目标**：必要性判据仍在层②产生，但"要不要因此沉默"由层① 的统一 role 决定豁免——分裂点 B 已消除，无需 C2。 | — | — |
| **C3（概率单通道）** ✅完成 | 消除 `old_decision` 影子路径对 floor 的重复作用；ratified_floor 只写一处（决策后单 OR，channel-independent，数学等价于原双写） | 中 | RWS primary/shadow 两态回归 |

**顺序建议**：C0 立即（直接修你看到的 bug）；C1 紧随（消除"被寻址"不一致，这是反复打补丁的根）；C2/C3 是真正的架构收敛，需设计评审 + 灰度，不急于一次做完。

## 7. 诚实定性

- **不是"多个无关通道"**——是**一条链上"要不要说话"被判三次 + "被寻址"两套定义 + 概率双通道**。
- 这些分裂是**逐层叠加的历史产物**（RWS 来自 humanization P3.5，B1/B2/B3 是本轮治本，necessity_gate 是 B3 的一部分），每个单独看都合理，合起来缺一个统一裁决。
- 我前几次的修复（ratified floor / wait 兜底 / 本次待修的 necessity 误杀）**全是分裂点 B 的不同切面**——证明该收敛，否则会一直打补丁。
- **C0 治标止血、C1–C3 才是收敛**。建议先 C0（你看到的 bug），再按数据决定 C1+。**待用户定走到哪一阶。**

## 8. 实施结果（2026-05-31，一次完成 C0+C1+C3）

- **C0+C1（合并落地）**：scheduler `notify` 把统一的 `_receiver_role` 存 `slot.last_role`（强信号 bypass 默认 addressed，概率路径写计算值），`_do_chat` 经 `ctx.extra["receiver_role"]` 传给 `chat()`；necessity_gate 改用该 role——`addressed`/`ratified` 一律豁免，删掉自己的 `trigger is None` 私有判据。**分裂点 B 消除**：现在全链路用同一个"被寻址"定义。直接修了"用户回 bot 上一句被 necessity 误杀"（reply 后无反应）。
- **C3**：移除 ratified_floor 对 `threshold` 的预抬升（rws_primary 下那只喂影子 `old_decision`），只保留决策后的单次 floor OR；数学等价于原双写（`(roll<threshold) OR (roll<floor)` == `roll<max(threshold,floor)`），但只写一处、channel-independent。**分裂点 C 的重复写消除。**
- **C2**：重定性为**不做**——`reply_necessity` 是 thinker LLM 输出、只在层②产生，无法上移到层①（否则要双 LLM）。C1 已达成 C2 目标（必要性仍在层②判，但"是否因此沉默"由层① 统一 role 裁定）。
- **验证**：新增 2 测试（ratified 豁免不降级 / overhearer 仍抑制），全量 **2291 passed**，ruff clean。5 个 pyright `slot` Optional 告警是既有 narrowing 假阳性（`_do_chat` 顶部 `if slot is None: return` 已护，pyright 跨 async-with/for 深嵌套丢窄化），非本次引入、不影响正确性。
- **仍待（按需，非本次）**：分裂点 A 的更彻底收敛（RWS / B2 / necessity 三者完全统一为单一 obligation 打分）需动 RWS 主决策，风险高，留待 RWS 存废评估时一并处理。

