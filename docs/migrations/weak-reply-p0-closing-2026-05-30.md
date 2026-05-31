# 迁移清单 — 弱回复 P0：closing 收尾型弱回复（D3）

> 日期：2026-05-30 ｜ 类型：行为新增（跨 5 模块）｜ 上游计划：[weak-reply-p0-impl-plan.md](../tracking/weak-reply-p0-impl-plan.md)
> 设计：[weak-reply-mechanism-design.md](../tracking/weak-reply-mechanism-design.md) §第1层(P0) + §第3层 closing 节制

P0 让「晚安/睡了/先这样」这类收尾消息绕过概率门进 chat，thinker 判 `light_kind=closing`，在 instruction_gate 同款 hook 点用并行预生成的对称 terminal token 直发（`on_segment` + `[CQ:reply]` + `return None` 跳过主 LLM），并加去重/冷却防刷屏。全程复用既有管道，无新建平行管线。

## 旧 → 新 对照（信号流四列）

| 环节 | 旧（二元决策） | 新（closing 弱回复） | 文件/位置 |
|---|---|---|---|
| 触发信号 | 无 closing 概念；「晚安」走概率门 → 多半 skip → SILENCE | `classify_closing_intent(text)` 规则检测（封闭 token 集，句尾、限长、排疑问） | [reply_workflow.py](../../services/reply_workflow.py) 新增函数 + `_CLOSING_TOKENS`/`_CLOSING_QUESTION_RE` |
| trigger 注入 | directed_followup / correction 两种 bypass mode | 新增并列 `mode="closing"` 注入（优先于 followup） | [router.py](../../kernel/router.py) correction 注入块之前 |
| TriggerContext.mode | 注释列举不含 closing | 注释补 `"closing"`（自由 str，additive） | [types.py](../../kernel/types.py#L270) |
| scheduler 派生 | `is_at/is_video_always/is_directed_followup/is_correction` | + `is_closing`；并入 proactive=None 豁免集 | [scheduler.py](../../services/scheduler.py) notify 派生块 |
| scheduler 分发 | directed/correction → `_fire` bypass | + closing → `_fire` bypass，**前置 dedup(`closing_done`) + 冷却(`last_light_time`)** | scheduler notify，correction 分支后 |
| slot 状态 | 无 light/closing 字段 | `_GroupSlot` 加 `closing_done: bool`、`last_light_time: float`（additive __slots__） | [scheduler.py](../../services/scheduler.py) `_GroupSlot` |
| 节制常量 | — | `_LIGHT_COOLDOWN_S=30`、`_CLOSING_RESET_S=1800`（长静默后允许新 closing） | scheduler 模块常量 |
| thinker action | `_ALLOWED_ACTIONS={reply,wait}` | + `light_reply`；新增 `light_kind ∈ {"",companion,closing}` 字段（照抄 instruction_signal 9 处穿透：`__slots__`/ctor/repr/`_normalize_light_kind`/`_decision_from_data`/prompt schema/guidance） | [thinker.py](../../services/llm/thinker.py) |
| thinker 入参 | 无 trigger 感知 | `think(trigger_mode=...)`；closing 时注入收尾提示 dynamic block | thinker.py `think()` + [client.py](../../services/llm/client.py) think 调用 |
| token 生成 | 主 LLM 完整生成 | `_gen_closing_token`（≤24 token 短生成）经 SpeculativeExecutor 与 thinker **并行**预取；超时/失败 → 静态 `_PASS_TURN_LIGHT_ACK` | client.py 新方法 + speculative 块 submit |
| 直发短路 | 仅 instruction_gate DENY 用 on_segment+return None | closing 短路在 DENY hook 之后同款结构：on_segment 直发 + 写 timeline assistant turn + 记 usage + return None（不调主 LLM） | client.py 4056 起 |
| 回复形态 | FULL_REPLY / SILENCE | closing → 对称 terminal token（弱回复），跳过主生成 | — |

## 测试映射（共 +34）

| 模块 | 文件 | 数量 | 关键断言 |
|---|---|---|---|
| 检测 | test_reply_workflow.py | 15 | 正例(好吧晚安/明天见/88…)、负例(晚安是什么意思/睡了吗/长句后续/超长) |
| thinker 穿透 | test_thinker.py | 6 | closing 解析、light_reply 默认 companion、非 light 清空 light_kind、无效值 fallback、旧 JSON 兼容、prompt 含 closing |
| scheduler bypass | test_scheduler.py（TestClosingBypass） | 4 | 低 talk_value/proactive=None 仍 fire、`closing_done` 去重、冷却压制 |
| client 短路 | test_closing_light_reply_client.py | 4 | token 清洗/截断、失败返回""、**D2 cancel-path：CancelledError 传播不被吞** |

## D 条款落实

- **D1 同模式扫描**：核 `thinker_action`/`decision.action` 全消费点——主流程唯一 action 分支是 `== "wait"`（4023），closing 短路在其后、instruction_gate 之后；`light_reply` 非 wait 故正常流过，companion 型 light_reply 落到主生成（P0 文档行为）。directed/correction 的 `pending_during_generation` busy 处理 closing 已复刻。
- **D2 cancel-path**：`_gen_closing_token` 的 `except Exception` 不捕 `CancelledError`（BaseException），cancel 传播；speculative 块 await 的 `except Exception` 同理。回归测试 `test_gen_closing_token_cancellation_propagates`。`closing_done`/`last_light_time` 在 scheduler `_fire` 前置位——shutdown 取消 chat 不会让同一 closing 在重启后重复触发（dedup 语义正确）。
- **D4 证据**：全量 `pytest` **2242 passed, 8 skipped**（+34）；`ruff` All checks passed；`pyright`（6 文件）0 errors。
- **D5**：全量前 `pkill -9 -f pytest`。

## 回滚

- router closing 注入块 + scheduler closing bypass 分支注释 → 即回二元决策。
- thinker `light_kind` / `light_reply` / `mode="closing"` / slot 新字段均 additive，fallback 安全，不影响旧路径。
- 改 .py → **需 rebuild bot 上线**。

## 已知边界（P0 划界）

- companion 型弱回复、prob-skip 救济、STICKER_ONLY 均不在 P0（见设计 §P1 / §2.6）。
- closing 去重粒度是「每群每次 terminal exchange」；`_CLOSING_RESET_S`(30min) 静默后允许新一轮 closing。
- token 生成失败回退静态 `_PASS_TURN_LIGHT_ACK="嗯，我在。"`——非理想告别语，但 degraded 不静默（优于 skip，依据设计 §1.4）。
