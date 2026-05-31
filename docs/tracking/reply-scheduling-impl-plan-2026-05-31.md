# 回复调度同类方案 — 实施方案（待审查）

> 2026-05-31。承接 [peer-projects-codeaudit-reply-scheduling-2026-05-31.md](peer-projects-codeaudit-reply-scheduling-2026-05-31.md)（三项目源码审读）。**S2（弱回复 prompt 逃生口）按用户意见剔除——弱回复多档状态机是有意设计，保留不动；S3（触发分层）按用户意见迁出至 [rws-activation-plan-2026-05-31.md](rws-activation-plan-2026-05-31.md) P7（属 RWS 作用域界定）。** 本文仅列 **S1（防 bot↔bot 循环）+ S4（攒批合并）** 两条可审查实施方案，接缝均已核实行号。**待用户审查，未编码。**

## 现状关键事实（核实，决定方案形态）

- **OneBot/QQ 无平台级 bot 标志**：群消息 `sub_type` 只有 `normal/anonymous/notice`，`sender.role` 是群角色(owner/admin/member)，**都不标识"发送方是不是机器人"**。不像 Discord 有 `author.bot`。→ S1 在 QQ 上只能靠 **已知 bot 身份 + 行为熔断**，无法照搬 LangBot 的 `author.bot` 入口过滤。
- **pair_guard 已存在但有结构缺陷**：`kernel/bot_pair_guard.py` `BotPairLoopGuard` 完整实现（per-pair 60s 滑窗 + max_per_minute 超限进 cooldown），已接在 router 入口（`router.py:1028`）。但：
  - 线上 `enabled=False`、`known_other_bots={}`（实测）。
  - **核心缺陷**：`_pair_key`（bot_pair_guard.py:70-77）只在 `is_known_peer` 为真时返回 key → **熔断只对"已登记 QQ 清单里的 bot"生效**；未登记的新 bot（如 `2708815230`）`is_known_peer=False` → record/is_suppressed 全短路 → **完全不设防**。这就是"靠维护清单太脆"在代码里的确证。
- **coalesce 已存在但关**：`MessageCoalescer`（services/coalesce.py）完整，router 入口已接（`_should_bypass_coalescer`），线上 `enabled=False`、idle 5s/max 12s。

## 复用基线（2026-05-31 二次复审，确认净新建极小）

全仓复审本方案所需能力，**结论：S4 纯开关；S1 改一个文件的一处门槛（其余全复用）。** 逐项确权（纠正本文初稿中少数"需确认/新建"的措辞；S3 已迁出本表）：

| 改动 | 能力 | 仓库现状（实读） | 结论 |
| --- | --- | --- | --- |
| **S1** | per-pair 60s 滑窗计数 + cooldown | `BotPairLoopGuard._events` deque + `_prune`(60s,bot_pair_guard.py:26/79) + `_cooldowns` | **现成，扩展即可** |
| S1 | **outbound 已接线** | `scheduler.py:1520` `_send_to_group` 后已调 `record_outbound`（target=last_user_id）——**初稿说"需确认"，实为已接，只是被 known 门槛 no-op** | **已有，放开即生效** |
| S1 | inbound 已接线 | `router.py:362` `record_inbound`（在 :1028 入口，早于 is_addressed） | **已有** |
| S1 | 对任意 peer（非仅名单） | `_pair_key`/`is_known_peer`(bot_pair_guard.py:70/32) 硬门限名单 | **S1 唯一实质改动点**：放开此门槛 |
| S1 | 互回/交替轮数 | 无专用计数器，但 `_events` deque 可承载；`slot.last_user_id`(scheduler.py:142) 辅助交替判据 | **用 _events 承载，不新建** |
| S1 | is_bot 判定 | 仅名单式（`known_other_bots` 3 处：bot_pair_guard:32 / name_registry:87 / upstream_filter:14） | 放开后**不需要**；名单降级为加权输入 |
| **S4** | Coalescer 构造/接线/flush→notify | plugin.py:1257 构造、router.py:393 接入、`_flush`→`scheduler.notify`(:460) | **全就绪** |
| S4 | @bot 不被延迟 | `_should_bypass_coalescer`(router.py:385)=`is_addressed or trigger`，bypass 时还 discard 已 pending | **已保证** |
| S4 | 与 S1 顺序 | pair_guard(:1028) 早于 coalescer(:1183/1457) | **顺序正确（先熔断后聚合）** |

**净新建工作量**：S4 = 改 `coalesce.enabled` flag；S1 = 改 `bot_pair_guard.py` 的 `_pair_key`/`is_known_peer` 门槛（+ 可选交替加权）+ config 加阈值字段，**outbound/inbound/滑窗/cooldown/metric/bind_self_id 全复用**。（S3 触发分层已迁出至 RWS 激活方案 P7。）

---

## S1 — 防 bot↔bot 循环：从"靠清单"改成"行为熔断为主、清单为辅"（治 P0 复发，最优先）

**问题**：QQ 拿不到 bot 标志；现 pair_guard 只对已登记 bot 生效，新 bot 漏防；且默认关。P0 循环（emu↔🍟薯条 03:46–48 互@ 15+ 轮）就是漏在"对方未登记 + enabled=False"。

**设计（行为优先，不依赖清单完整性）**：核心改 `BotPairLoopGuard._pair_key`，让熔断**不再要求 `is_known_peer`**——对**任意 peer** 都按 per-pair 滑窗计数；`known_other_bots` 清单从"准入条件"降级为"加权信号"（已知 bot 用更严阈值）。

- **判定信号(按强度)**：
  1. **行为熔断(主)**：同一 (group, self↔peer) pair 在 60s 滑窗内**互相回复 ≥ N 轮**(N 比现 max_per_minute 更语义化——区分"用户连发"和"一来一回对刷")→ 进 cooldown 静默。这是唯一不依赖"知道对方是 bot"的信号，正是 P0 场景(emu↔薯条每 8-10s 一轮)的特征。
  2. **已知 bot 清单(辅，加严)**：`is_known_peer` 命中 → 阈值更低(更快熔断)。仍保留 known_other_bots 配置,但**不再是熔断的前提**。
  3. **(可选)对方消息特征**：reply+@bot 且高频规律间隔(机械感)可作辅助信号,但不作硬判(避免误伤真人快速对话)。
- **outbound 也记**：bot 自己 @ 对方时 `record_outbound`(已有此方法,line 55)同样计入 pair 滑窗——双向计数才能准确识别"对刷"。需确认 outbound 在 bot 发送处接线(现仅 inbound 接了)。
- **默认开**：S1 落地后 `bot_pair_guard.enabled` 应默认 true(P0 防护不该默认关)。

**接缝**：
- `kernel/bot_pair_guard.py`：`_pair_key` 去掉 `is_known_peer` 前提(对任意 peer 建 key)；新增"互回轮数"语义(区分单向连发 vs 双向对刷);known_peer 走更严阈值。
- `kernel/router.py:1028` `_maybe_drop_pair_guard`：已在 is_addressed/trigger 之前(正确),无需移位。
- **outbound 已接线（复审确认）**：`scheduler.py:1520` `_send_to_group` 后已调 `record_outbound`(target=last_user_id)——**无需新增接线**,放开 known 门槛后它对任意 peer 即开始计数(双向对刷识别所需)。
- `kernel/config.py:1873` `BotPairGuardConfig`：enabled 默认改 true;加"互回轮数阈值 N"字段;known_peer 严阈值字段。

**配置**：`bot_pair_guard.enabled=true`(默认)、`loop_turns_threshold`(任意 peer 互回轮数)、`known_peer_turns_threshold`(已知 bot 更严)、保留 `cooldown_seconds`。

**验证(测试设计)**：
- 行为熔断：构造 (self↔未登记peerX) 60s 内互回 N+1 轮 → 第 N+1 轮 `is_suppressed=True`(**关键回归:不在 known 清单也熔断**,直接复现 P0)。
- 单向不误伤：同一用户连发 N+1 条(非 bot 对刷,无 outbound 交替)→ 不熔断(区分"用户刷"和"bot 对刷")。
- known_peer 更快熔断:登记的 bot 用更低阈值 → 更早 suppress。
- cooldown 后恢复:cooldown_seconds 过后 `is_suppressed=False`。
- D2 cancel-path:guard 是纯内存计数,无 async 副作用,无污染。

**风险与防误伤**：行为熔断的核心风险是**把"真人之间快速一来一回"误判成 bot 对刷**。缓解:① pair 是 (self, peer),只计**涉及 bot 自己**的对刷(self↔peer),不管两个真人之间;② 阈值 N 设保守(如 60s 内 ≥6 轮才算,真人极少和 bot 在 1 分钟内对答 6 个回合);③ 误伤代价低(只是静默 cooldown 60s,不是永久)。

**回滚**：`bot_pair_guard.enabled=false` 回现状;`_pair_key` 改动加 flag 控制"是否要求 known_peer",回退即恢复旧行为。

---

## S3 — 已迁出（→ RWS 激活方案 P7）

触发分层（规则在前、RWS 退灰区、收敛分裂点 A）本质是 RWS 激活的作用域界定，已迁至 [rws-activation-plan-2026-05-31.md](rws-activation-plan-2026-05-31.md) **P7**，与 P1–P6 一同推进。本方案仅保留 **S1（防 bot↔bot 循环）+ S4（攒批合并）**。

---

## S4 — 攒批合并：启用并对齐 coalesce 参数（防刷屏，复用既有）

**问题**：omubot 已有 `MessageCoalescer` 但线上 `enabled=False`;三项目都靠攒批合并天然抑制刷屏 + 省 token + 防答非所问(连发被折叠成一次)。

**设计(纯启用 + 调参,几乎零新代码)**：
- 开 `coalesce.enabled=true`。
- 参数对齐审读基准:LangBot debounce 默认 1.5s/10 条上限;omubot 现 idle 5s/max 12s(更保守)。**评估 idle_window 是否过长**——5s 静默才 flush,在快聊群里会让 bot 反应慢半拍。建议 idle 收到 ~2-3s、max 保留。但这是体感调参,需灰度。
- **与 S1/S3 的边界**:coalesce 只作用于**非寻址**消息(`_should_bypass_coalescer`:is_addressed/trigger 直接 bypass 不攒批)——@bot 仍即时响应,不被 debounce 拖延。确认这个 bypass 在 S1 熔断之后(熔断的 bot 消息不该进 coalesce)。

**接缝**：`config.json` `coalesce.enabled=true` + idle_window 调参;`services/coalesce.py`/`kernel/router.py` 现成,无需改码(除非调 bypass 顺序)。

**验证**：非寻址连发 N 条 → 一次 flush 合并;@bot 不被 coalesce 延迟(立即);idle/max 窗口边界。
**风险**：低-中(idle 调短影响反应节奏,灰度观察);纯启用可一键回退 `enabled=false`。

---

## 落地顺序与依赖

| 阶段 | 治 | 依赖 | 改动量 | 风险 |
| --- | --- | --- | --- | --- |
| **S1 防循环行为熔断** | P0 循环复发 | 无（改 bot_pair_guard，outbound 已接） | 中（改 _pair_key 语义 + config） | 中（误伤真人快聊，靠保守阈值缓解） |
| **S4 coalesce 启用** | 刷屏/答非所问 | 无（启用+调参） | 极小（配置为主） | 低-中（idle 调参体感） |

**建议顺序**：S1 最优先(P0、独立、治反复复发的根)；S4 可随时(纯配置启用,观察体感)。（S3 已迁出至 RWS 激活方案 P7，与 RWS 一同推进。）

## 与既有设施关系（不重复造轮子）

- S1 = **改造** `BotPairLoopGuard`(已有),非新建——去掉"只防已登记 bot"的缺陷；inbound+outbound 接线全已就绪。
- S4 = **启用** `MessageCoalescer`(已有),非新建。

**待用户审查**：S1+S4 两条是否照此实施?S1 的"互回轮数阈值 N"建议值(60s 内 ≥6 轮算对刷)是否合理?S4 的 idle_window 是否要从 5s 收到 2-3s?确认后每条单独出 D3 四列迁移清单 + 测试再编码。
