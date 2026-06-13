# 多寻址路由 × 弱回复降级 实现计划

> 状态：待审批 | 创建：2026-06-11 | 调研依据：[multi-addressee-topic-routing-research-2026-06-11.md](multi-addressee-topic-routing-research-2026-06-11.md)
> 落地范围：**一次全做**（缺陷①+②同批）。greeting **像 closing**（短路出 token）。异块 **无间隔连续 fire**。
> 目标定级：复杂并发寻址场景从容应对的「未来级」修复，非补丁。

## 决策快照（§7 已拍板）

- Q1 缺陷①「宝宝」→ 降级弱回复 companion（补 design §2.5-2d）+ 新增 greeting light_kind（像 closing）
- Q2 异块多@ → 路径 Y（块队列串行 fire，不拆并发防御），异块无间隔
- Q3 多职 addressee_hint → 仅强信号成员（reply-to/真@/@块参与者），词法兜底成员不进 cue
- Q4 真@优先级 → PendingTrigger 建 evidence/obligation 字段，fire 策略当前不区分

---

## 五个工作块（一次全做，按依赖排序）

### 块 1 — 数据模型升级（根，其余块依赖此）

**1a. `PendingMessage` 扩字段**（services/llm/arbiter.py:53-57）
- 增 `target_message_id: int | None = None`、`block_id: str = ""`、`evidence: str = ""`、`obligation_level: str = ""`。
- frozen+slots 照旧；新字段全默认值，不破坏现有构造点。

**1b. `_GroupSlot.trigger` 标量 → 队列**（services/scheduler.py:168, 598）
- 新增 `pending_triggers: list[TriggerContext]`（按 block_id 可分组）。
- 保留 `slot.trigger` 标量作兼容垫片（非@路径仍单 trigger），但@/burst 路径写 `pending_triggers`。
- L598 `slot.trigger = trigger  # latest wins` 的@分支改为 **append 到 pending_triggers**；非@路径行为不变。

**1c. timeline pending 按 block 过滤取用**（services/memory/timeline.py:290 `get_pending`）
- 不重构存储（仍 per-group 单 list）。
- `add_pending_trigger` 增 `block_id` 入参写进 marker；新增 `get_pending(group_id, block_id=None)` 可选过滤——fire 单块时只取该块 marker，避免异块串话。

### 块 2 — @ 事件进话题块归属（缺陷②前置）

**2a. @ 发火前调用 `topic_tracker.observe`**（services/scheduler.py:617 `is_at` 分支前）
- 当前纯@走 rule layer 直接 fire，**不经 observe**（§4 风险1）。在 `is_at`/`is_closing` 等 rule 分支进入前，补一次 `observe(... at_self/at_targets ...)`，使@消息有 block 归属。
- observe 已是纯 CPU 无副作用，仅补调用点；信号从 notify 已有的 `tb`（topic-block signals，router.py:624）透传。

**2b. burst_pending 携带 block 归属**（services/scheduler.py:627）
- append PendingMessage 时填入 observe 返回的 `block.block_id` + trigger 的 target_message_id/evidence/obligation_level。

### 块 3 — fire 按块路由（路径 Y 核心）

**3a. `_arbiter_completeness_loop` fire 前按 block 分组**（services/scheduler.py:1240）
- burst_pending 按 `block_id` 分组 → 得到 N 个块。
- **同块多@**：合并，引用该块 `representative_message_id()`（优先 at_message_id），一次 fire。
- **异块**：建块队列，**逐块连续 fire（无间隔）**——复用现有「回合接力」：第一块 fire→running_task；finally 里若块队列非空，立即起下一块（不倒回 pending，直接消费队列）。

**3b. `_fire` / `_do_chat` 接收单块 trigger**（services/scheduler.py:1489, 1794）
- `_fire(group_id, block_trigger=...)` 传入该块合并后的 trigger（target_message_id=block.representative）。
- `_do_chat` 的 `[CQ:reply]`（L1852）用块的 representative，不再读全局 slot.trigger。
- 块队列状态存 slot（新增 `block_fire_queue: list`），finally 消费；空则回落现有 msg_count re-fire。

**3c. D1 同模式：flush 合并丢 trigger**（kernel/router.py:676）
- 合并 flush 的 `notify(message_text=merged, 无trigger)` 纳入：merged 路径若含@消息，trigger 不能整个丢——至少保留首个@的 target。

### 块 4 — 缺陷①「宝宝」降级弱回复（companion 救济 + greeting）

**4a. ratified 续话 prob-skip 救济**（services/scheduler.py:879 skip 分支 / 855 floor 处）
- 补 design §2.5-2d：role=ratified 或 last_assistant_to_user 的「该被看见」消息，skip 时**注入 companion 弱回复通道**而非 SILENCE。
- 复用现有 trigger 注入模式（类比 closing bypass），注入一个 companion-oriented trigger 进 chat，让 thinker 判 light_kind。
- 解除 thinker prompt 对 companion 的压制（thinker.py:159「本期 companion 仍走普通 reply」→ 改为「ratified 续话/phatic 短消息可用 companion」）。

**4b. 新增 greeting light_kind（像 closing，短路出 token）**——7 核心站点：
1. thinker.py:33 `_ALLOWED_LIGHT_KINDS` 加 `"greeting"`
2. thinker.py:156-160 弱回复 prompt 段加 greeting（招呼型）bullet
3. thinker.py:171 JSON schema enum `companion|closing|` → `companion|closing|greeting|`
4. thinker.py:704 加 `elif trigger_mode == "greeting":` 动态提示块
5. client.py:152 加 `_LIGHT_REPLY_GREETING_FALLBACK = "早~"`（或场景化）
6. client.py:2760 加 `_gen_greeting_token`（或参数化复用 `_gen_closing_token`，换 system prompt 为「对称问候」）
7. client.py:2832-2872 `_handle_light_reply` 加 greeting 分支（mirror closing）；client.py:4610 短路检查扩为 `in ("closing","greeting")`

**4c. greeting 上游规则检测**（services/reply_workflow.py:292 旁 + kernel/router.py:1883 旁）
- 新增 `classify_greeting_intent`（早安/早上好/早/morning 封闭集，mirror closing 检测：短句、句首/独立、无疑问）。
- router 命中→注入 `TriggerContext(mode="greeting")`；scheduler.py:571 加 `is_greeting` 进 bypass 分支（mirror closing，复用 cooldown/dedup）。

### 块 5 — STICKER_ONLY 防 SILENCE 地板 + 弱回复表情载体（§3.5.5）

**5a. companion 表情优先偏置**（client.py:2874 companion 分支 / 复用 `_fallback_ack` 2951）
- companion 弱回复优先 `search_by_intent(语境)` 发表情（复用 by-intent，sticker_tools.py:220 已落地）；命中则发图，否则注入 hint 走主 LLM 出短 ack。
- 载体三档：语义必须型（closing/greeting）→ 文字 token；「在哦」→ 文字低频；其余 phatic → 表情优先。

**5b. 统一频控降级载体不降级回复**（services/sticker/decision_provider.py 消费侧）
- STICKER_ONLY 上下文：`StickerDecisionProvider.decide` 的布尔重解读为「发表情 vs 发文字 ack」，**两分支都有回复**。
- 频控 skip（sampled_skip/thinker_veto）→ 退化为 `_pick_empty_visible_reply_fallback`（固定池，必非空），而非 SILENCE。
- 表情构造失败（`_build_sticker_cq` None）→ 同样落文字地板。
- 地板仅 `force_reply or not is_group` 生效（保持现有边界，纯旁观仍可 SILENCE）。
- 频控压制时文字 ack 形态 = **(c)**：语义必须型走主 LLM；普通 phatic 用固定池。

---

## 纪律约束

**D2 cancel-path（必做回归）**：
- 块队列 fire 中途被 shutdown 取消：`block_fire_queue` / `pending_triggers` 不得污染下一轮（`pytest.raises(CancelledError)` 模拟，断言 slot 状态清空）。
- companion 救济 trigger 注入后被取消：不留半截 pending marker。
- greeting/closing 短路被取消：不污染 `closing_done` / timeline（沿用现有 closing D2 测试模式 tests/test_closing_light_reply_client.py）。

**D1 同模式扫描**：
- 吃 `slot.trigger` 标量的所有点：notify covering write（598）、_fire（1498）、deferred_addressed_fire（1549/1557）、flush 合并（router.py:676）、pending_during_generation re-fire（2018）——逐一确认是否需随队列化调整。
- 维护日志列出扫了哪些点。

**D3 迁移清单**（docs/migrations/）：本次有数据结构变更（PendingMessage 扩字段、slot 队列化），出「旧标量→新队列」对照表。

**D4 完成证据**：同模式扫描结果 + 外部可观察（多@场景日志：异块各自 [CQ:reply] 正确、宝宝走 companion、ratified 不再 prob-skip）+ 回滚路径。

---

## 回归测试清单（D4）

| 用例 | 断言 |
|------|------|
| 真@A 后伪@B（同 burst） | 各自块；A 的回复 [CQ:reply] 指向 A 的 msg，不被 B 顶 |
| 同块两人@ | 合并一次回复，引用 representative，多职 hint cue 两人 |
| 异块两人@ | 块队列连续两次 fire，各自 [CQ:reply] 正确，无串话 |
| 词法误并块成员 | 不进多职 cue 名单（仅强信号成员） |
| 宝宝（ratified 续话，无@） | 走 companion 弱回复（表情优先/文字兜底），不再 SILENCE |
| 早安 | classify_greeting_intent True → greeting token（非 companion） |
| 「早安是什么意思」 | greeting False（带疑问） |
| STICKER_ONLY 频控 skip | 降级文字 ack（固定池），不 SILENCE |
| 表情构造失败 | 落 _pick_empty_visible_reply_fallback |
| 纯旁观 prob-skip | 仍 SILENCE（边界不变） |
| D2：块队列 fire 取消 | block_fire_queue/pending_triggers 清空，不污染下轮 |
| flush 合并含@ | trigger 不整个丢，保留首@ target |

全量 `pytest` 通过 + ruff + pyright clean（D5：跑前 pkill pytest）。

---

## 部署（D7）

- 改的全是 .py（scheduler/router/timeline/thinker/client/arbiter/reply_workflow/decision_provider）→ 需 **rebuild bot**：`dot_clean . && docker compose up bot -d --build`。
- thinker prompt 改动随 .py rebuild 生效（非 persona freeze）。
- 部署前 `git stash list && git status -uno`；NapCat 全程不动。
- 验证：多@测试群（984198159）实发真@+伪@、同块、异块、宝宝、早安，看日志各路由正确。

## 回滚

- 全 git revert 上述文件 + rebuild bot。
- 无 DB 迁移（timeline 存储未变，仅 marker 增 block_id 字段，旧 marker 无此字段时 get_pending 过滤回落「全取」）。
- 数据模型扩字段全默认值，向后兼容。
