# 迁移清单 — 多寻址路由 × 弱回复降级（D3）

> 日期：2026-06-11 ｜ 类型：缺陷修复 + 行为新增（跨 8 模块）
> 上游：[multi-addressee-topic-routing-impl-plan-2026-06-11.md](../tracking/multi-addressee-topic-routing-impl-plan-2026-06-11.md)
> 调研：[multi-addressee-topic-routing-research-2026-06-11.md](../tracking/multi-addressee-topic-routing-research-2026-06-11.md)

修两个缺陷 + 补两类弱回复：
- **缺陷②（标量覆写）**：burst 内多人@时 `slot.trigger` 被最后到达的@覆盖，回复 `[CQ:reply]` 指向错人。改为按话题块分组，每块各自 fire，锚到自己块的@消息。
- **缺陷①（宝宝沉默）**：ratified 续话遇概率 miss → SILENCE。改为降级 companion 弱回复（冷却限频）。
- **greeting 弱回复**：早安/早上好等招呼，mirror closing，短路出对称 token。
- **STICKER_ONLY 防 SILENCE 地板**：companion 弱回复主 LLM 空文本 → 复用 `_fallback_ack`（表情优先→文字兜底），不再剥成沉默。

## 旧 → 新 对照（数据结构 + 信号流）

| 环节 | 旧 | 新 | 文件/位置 |
|---|---|---|---|
| PendingMessage | content/user_id/timestamp 三字段 | +`target_message_id`/`block_id`/`evidence`/`obligation_level`（全默认值，向后兼容） | [arbiter.py:53](../../services/llm/arbiter.py#L53) |
| arbiter LLM payload | `asdict(message)` 全序列化 | 只序列化 content/user_id/timestamp（路由字段不进 prompt） | [arbiter.py](../../services/llm/arbiter.py) judge_completeness |
| slot 状态 | `trigger: TriggerContext\|None` 标量 | +`block_fire_queue: list[TriggerContext]`（异块串行队列）；标量保留作非@/单trigger垫片 | [scheduler.py](../../services/scheduler.py) `_GroupSlot` |
| @ burst 归属 | observe 返回值丢弃 | 捕获 `observed_block`，填进 PendingMessage.block_id | scheduler.py notify 顶部 |
| burst fire 路由 | `self._fire(group_id)` 单发，读 `slot.trigger`（被覆写） | `_build_block_triggers` 按 block_id 分组 → 同块合并/异块分发；首块 fire + 余块入 `block_fire_queue` | scheduler.py `_arbiter_completeness_loop` |
| `_fire` 签名 | `_fire(group_id)` | `_fire(group_id, *, block_trigger=None)`；块trigger 优先于标量 | scheduler.py `_fire` |
| 异块连续 fire | 无 | `_do_chat` finally 排空 `block_fire_queue`（无间隔），优先于 pending/msg_count re-fire | scheduler.py `_do_chat` finally |
| 块锚点 | — | `TopicBlockTracker.pick_block_by_id` 新增；`representative_message_id()`（优先 at_message_id） | [topic_block.py](../../services/group/topic_block.py) |
| 多职 hint | 单 target_user_id | `extra["block_addressees"]` 携带同块多@成员；`_build_multi_addressee_hint` 列名 cue | [client.py](../../services/llm/client.py) `_build_addressee_hint` |
| ratified skip | role=ratified + 概率 miss → SILENCE | 降级 companion 弱回复 trigger（`_LIGHT_COOLDOWN_S` 限频）；纯 overhearer 仍 SILENCE | scheduler.py prob skip 分支 |
| greeting 检测 | 无 | `classify_greeting_intent`（封闭 token 集，句首、限长、排定义疑问），mirror closing | [reply_workflow.py](../../services/reply_workflow.py) |
| greeting 注入 | 无 | router `mode="greeting"`（gated 同 closing：recent assistant + last_to_user） | [router.py](../../kernel/router.py) closing 注入块后 |
| scheduler 派生 | `is_closing` | +`is_greeting`；并入 proactive=None 豁免；新增 bypass 分支（仅冷却门，不设 closing_done） | scheduler.py notify |
| thinker light_kind | `{"",companion,closing}` | +`greeting`；prompt 解封 companion（旧文案压制 companion 走普通 reply）+ 新增 greeting/companion 动态提示 | [thinker.py](../../services/llm/thinker.py) |
| token 生成 | `_gen_closing_token` | 抽出 `_gen_light_token(kind=...)`，closing 为薄包装；greeting 用对称问候 system prompt | client.py |
| light_reply 分发 | closing 短路 / companion 注入 hint | +greeting 短路分支（mirror closing）；短路判定扩为 `in ("closing","greeting")` | client.py `_handle_light_reply` + dispatch |
| 空文本兜底 | `force_reply or not is_group` → `_fallback_ack`，否则 SILENCE | companion 弱回复 turn 也进 `_fallback_ack`（`force_reply or companion_hint is not None`），两处 finalize 调用 | client.py 两处 `_finalize_visible_reply` 调用 |

## D1 同模式扫描结果

吃 `slot.trigger` 标量的全部点（grep `slot.trigger`）：
- notify 写入（615）：保留标量垫片，仅非@/单trigger 路径读；@ burst 真相源是 `burst_pending`（每条带自身 block_id），不再依赖标量。
- closing/greeting bypass skip 清理（695/699/724）、prob skip 清理（756/774/971）：行为不变。
- `_fire`（1662/1664/1665）：块trigger 优先，否则消费标量一次。
- `_maybe_anchor_topic_block`（1104）：仅 `slot.trigger is None`（概率非显式触发）才锚定，与块路由不冲突。
- `_deferred_addressed_fire` 超脱判定（1715）：读标量判是否被新 trigger 取代，行为不变。
- `clear_pending`（1141-1143）：新增清 `block_fire_queue`，与 trigger/pending 一致清理。
- **router flush 合并（676）**：`_should_bypass_coalescer = is_addressed or trigger is not None` —— **任何@都绕过 coalescer**，合并 flush 只处理非寻址 proactive 消息（无 trigger、无 target 可丢）。故计划「3c flush 合并丢 trigger」**经核实为非问题**，未加防御代码。

## 测试映射

| 模块 | 文件 | 关键断言 |
|---|---|---|
| 块路由单测 | test_arbiter_scheduler.py | 同块合并（block_addressees 两人）、异块分发（各锚自己 msg、单成员无 cue） |
| 块路由集成 | test_arbiter_scheduler.py | 异块 burst → fire 两次，`[CQ:reply]` target={201,202} 各自正确 |
| D2 cancel | test_arbiter_scheduler.py | 多块 fire 中途 cancel → `block_fire_queue` 清空不污染下轮 |
| 宝宝降级 | test_scheduler.py（TestOverhearerRole） | ratified + floor=0 → companion 救济 fire（mode=companion），非 SILENCE；冷却内压制 |
| greeting 检测 | test_reply_workflow.py | 正例(早安/早上好/在吗/hi/morning…)、负例(早安是什么意思/超长/空) |
| greeting 短路 | test_closing_light_reply_client.py | greeting token 生成+直发、失败 fallback「早~」 |

## D 条款落实

- **D2 cancel-path**：`_do_chat` `except CancelledError` 清空 `block_fire_queue`（不再起下一块）；`clear_pending` 同步清。回归 `test_block_fire_queue_cleared_on_cancel`。closing/greeting 短路沿用既有 `_gen_light_token` 的 `except Exception`（不捕 CancelledError）。
- **D4 证据**：全量 `pytest` **2628 passed, 17 skipped**；`ruff`（services/kernel/新增测试）All checks passed；`pyright`（8 文件）0 errors。外部可观察：日志 `arbiter_a_fire | burst spans N topic blocks -> serial fire`、`prob skip -> companion rescue (ratified)`、`greeting -> fire`。
- **D5**：全量前 `pkill -9 -f pytest`。

## 回滚

- git revert 上述 8 文件 + 测试 + rebuild bot。
- 无 DB 迁移：timeline 存储未变；PendingMessage 扩字段全默认值，向后兼容。
- `block_fire_queue` 为运行态字段，进程重启即空。
