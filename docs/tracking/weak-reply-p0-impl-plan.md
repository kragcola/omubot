# P0 实施计划 — 弱回复机制：closing 收尾型弱回复

> 范围：weak-reply-mechanism-design.md §第1层(P0) + §第3层 closing 节制(同批)
> 决策：token 生成走 **SpeculativeExecutor 并行 LLM**(复用点 D)；超时 fallback 静态池
> 不做：companion(P1)、STICKER_ONLY(依赖表情包语义检索阶段3)、prob-skip 救济(P1)

## 目标

线上痛点：用户「好吧晚安」→ scheduler `prob skip → SILENCE`，bot 毫无反应 = 拒绝完成 terminal exchange(§1.1)。
P0 让 closing 消息绕过概率门进 chat，thinker 判 `light_kind=closing`，在 instruction_gate 同款 hook 点用并行预生成的对称 terminal token 直发(`on_segment` + `[CQ:reply]` + `return None` 跳过主 LLM)，并加去重/冷却防刷屏。

## 全部已核实的接缝(代码与设计文档一致)

| 接缝 | 位置 | 复用方式 |
|---|---|---|
| TriggerContext.mode | [types.py:270](kernel/types.py#L270) 自由 str | 加 `"closing"`(additive) |
| closing 检测 | [reply_workflow.py](services/reply_workflow.py) 仿 `classify_followup_text` | 新增 `classify_closing_intent` 规则函数 |
| router 注入 | [router.py:1360-1377](kernel/router.py#L1360) directed_followup 注入旁 | 加并列 closing 注入分支 |
| scheduler bypass | [scheduler.py:417-420](services/scheduler.py#L417) 派生 + [468](services/scheduler.py#L468) fire | 加 `is_closing` 派生 + 并列 `_fire` 分支 |
| slot 状态 | [scheduler.py:98-126](services/scheduler.py#L98) `_GroupSlot.__slots__` | 加 `last_light_time` / `closing_done` |
| trigger 流转 | notify→slot.trigger→`_fire`[1111](services/scheduler.py#L1111)→`_do_chat`[1268](services/scheduler.py#L1268)→`chat(trigger=)`[3731](services/llm/client.py#L3731) | 已贯通，无需改 |
| thinker 穿透 | [thinker.py](services/llm/thinker.py) `instruction_signal` 9 处模式 | 照抄加 `light_kind` |
| speculative | [client.py:3862](services/llm/client.py#L3862) `async with SpeculativeExecutor` | token 在块内 submit+await 进变量 |
| 直发短路 | [client.py:3977-3986](services/llm/client.py#L3977) instruction_gate DENY hook | closing 短路在同 hook 点，复刻 DENY 结构 |
| 短 LLM 生成 | [client.py:1704-1721](services/llm/client.py#L1704) LLMRequest+`_call`+`_clean_reply` | terminal token 生成同款 |
| fallback 静态 | [client.py:111](services/llm/client.py#L111) `_PASS_TURN_LIGHT_ACK` | 超时兜底复用 |

## 改动清单(按依赖序)

### 1. kernel/types.py — TriggerContext.mode 注释加 "closing"
仅更新 mode 注释列举(枚举是自由 str，无需改逻辑)。additive。

### 2. services/reply_workflow.py — classify_closing_intent(规则层)
```python
_CLOSING_TOKENS = ("晚安","睡了","睡觉","我去睡","先这样","下了","撤了","溜了","明天见","拜拜","88","碎觉","去睡了")
def classify_closing_intent(text: str) -> bool:
    # 短消息 + terminal token 在句尾/独立成句；带疑问或长后续不算
```
负例(设计 §5)：「晚安是什么意思」False(带疑问)、「先这样吧我觉得X更好」False(长句后续)、「今天好累」False。
仿现有 `classify_followup_text`(218)/`normalize_followup_text`(208) 风格：先 `normalize`(去标点空白)，限长(≤~16 字)，token 须在尾部，含 `什么/吗/嘛/为/?` 等疑问标记则否决。

### 3. kernel/router.py — closing 注入分支
- 加 `_CLOSING_*` 不需要(检测在 reply_workflow)。
- 在 directed_followup 注入块([1360](kernel/router.py#L1360))**之前**加 closing 分支(closing 优先级高于 followup，因「晚安」也可能误判为 followup)：
  ```python
  if (trigger is None and not is_addressed and has_recent_assistant
      and last_assistant_to_user and classify_closing_intent(text)):
      trigger = TriggerContext(reason="用户在收尾告别", mode="closing",
          target_message_id=event.message_id, target_user_id=str(event.user_id))
  ```
- 门槛：`last_assistant_to_user`(对方在对 bot 说) + `has_recent_assistant`(近窗内 bot 发过言)——只在"正在进行的双人对话收尾"时触发，不对陌生人冷启动晚安。

### 4. services/scheduler.py — bypass + slot 状态
- `notify` 派生 `is_closing = trigger.mode == "closing"`([420](services/scheduler.py#L420)旁)，并入 proactive=None 豁免集([421-427](services/scheduler.py#L421))。
- 加与 directed_followup 并列的 bypass 分支([468](services/scheduler.py#L468)旁)：`is_closing → _fire(group_id)`(busy 时入 pending_during_generation 同款)。
- `_GroupSlot.__slots__`([105](services/scheduler.py#L105)) 加 `last_light_time: float = 0.0`、`closing_done: bool = False`，ctor 初始化([126](services/scheduler.py#L126)旁)。
- **节制(第3层，同批)**：closing 进 bypass 前查冷却/去重——
  - `closing_done` 已 True 且未超时/无新话题 → 不重复 fire(连发两条晚安只回一次，§1.1 terminal exchange 已完成)。
  - `now - last_light_time < 冷却窗` → 压成不 fire。
  - 决策放 scheduler(进 chat 前)，与 directed bypass 同层，避免进 chat 才发现要节制。

### 5. services/llm/thinker.py — light_kind 穿透(照抄 instruction_signal 9 处)
- `_ALLOWED_ACTIONS` 加 `"light_reply"`([30](services/llm/thinker.py#L30))。
- prompt JSON schema([155](services/llm/thinker.py#L155)) 加 `light_kind`，加一段说明：trigger 为 closing 收尾语境时输出 `action=light_reply, light_kind=closing`。
- `ThinkDecision.__slots__`+ctor+`_normalize_light_kind`+`_decision_from_data`+repr+`_fallback`——照抄 instruction_signal 全套(fallback `""`)。
- thinker 需感知 closing 语境：`think()` 加可选入参 `trigger_mode: str = ""`，prompt 里提示。chat() 调 think 时传 `trigger_mode=getattr(trigger,"mode","")`。

### 6. services/llm/client.py — closing 短路(核心)
- **speculative 块内**([3862-3892](services/llm/client.py#L3862))：若 `trigger.mode=="closing"`，`closing_token_task = speculative.submit(self._gen_closing_token, ...)` 与 thinker 并行；块内 await 进 `closing_token`(超时→None)。
- 新增 `_gen_closing_token(...)`：仿 [1704-1721](services/llm/client.py#L1704) 构 `LLMRequest`(task 用轻量 profile，max_tokens~24，prompt="对方在道别/收尾，回一句对称的、口语化的告别 token，仿对方语气，越短越好，仅一句不解释")，`_call` → `_clean_reply`+`_strip_control_tokens`。语气由 mood_text 注入(复用 mood→how)。
- **hook 点**([3977](services/llm/client.py#L3977) instruction_gate 之前或之后并列)：若 thinker `light_kind=="closing"`(或 `trigger.mode=="closing"` 兜底)：
  ```python
  token = closing_token or _PASS_TURN_LIGHT_ACK
  quote_id = getattr(trigger,"target_message_id",None)
  seg = f"[CQ:reply,id={quote_id}]{token}" if quote_id else token
  await on_segment(seg)
  # 记 usage(thinker+token gen) + timeline 写入(复刻 DENY 后续)
  return None   # 跳过主 LLM
  ```
- 复刻 DENY 分支([2424-2432](services/llm/client.py#L2424))的 on_segment/CQ:reply/return None 结构；usage 记账走现有 `_record_usage`。
- closing fire 成功后回写 `slot.closing_done=True` / `slot.last_light_time`（经 scheduler 回调或 chat 返回标记——优先在 scheduler `_do_chat` 完成后置位，避免 client 直接摸 slot）。

## 测试清单(D2/D4，对应设计 §5)

| 文件 | 用例 |
|---|---|
| test_reply_workflow.py | `classify_closing_intent` 正例(好吧晚安/睡了/明天见) + 负例(晚安是什么意思/先这样吧我觉得X更好/今天好累) |
| test_router_*.py | 晚安+last_assistant_to_user → 注入 `mode="closing"`；无 recent_assistant → 不注入 |
| test_scheduler.py | closing trigger → bypass `_fire` 进 chat；`closing_done` 去重(连发两条只 fire 一次)；`last_light_time` 冷却压制 |
| test_thinker_*.py | closing trigger → 解析 `action=light_reply, light_kind=closing`；light_kind fallback `""` 不影响旧 JSON |
| test_client/streaming | closing 短路：`on_segment` 直发 token + `return None`(主 LLM 不被调用，mock `_call` 断言次数)；speculative 超时 → fallback `_PASS_TURN_LIGHT_ACK` 不静默 |
| **D2 cancel-path** | closing 短路被 shutdown 取消 → `pytest.raises(CancelledError)`，断言 `slot.closing_done`/timeline 未污染(参 test_sticker_tools 的 cancel 测试) |
| mood 染色 | 低落 mood 下 closing token 走安静语气(快照/prompt 断言) |

## D 条款遵循

- **D1 同模式扫描**：检查所有 `trigger.mode ==` 派生点(scheduler 91/417-420/527/548、client 1313/1320、router)是否需同步认 closing；检查 directed_followup/correction 的 pending_during_generation 处理是否 closing 也要(是，复刻)。
- **D2 cancel-path**：closing 短路含 await(on_segment/speculative)，必须有 shutdown 取消的回归测试，断言 slot 旗标/timeline 不污染。
- **D3 迁移清单**：本次跨 5 文件 + 新行为，产出 `docs/migrations/` 旧→新对照(closing 信号 → 注入点 → bypass → 短路 → 节制)。
- **D4 完成证据**：全量 pytest + ruff + pyright；rebuild 后真实"晚安"场景日志(`mode=closing` + 直发 token + 主 LLM 未调) + 回滚路径。
- **D5**：跑全量前 `pkill -9 -f pytest`。

## 落地顺序

1. reply_workflow.classify_closing_intent + 单测(纯函数，先验证检测准确)。
2. types.mode 注释 + thinker light_kind 穿透 + 单测。
3. router 注入 + scheduler bypass + slot 状态 + 节制 + 单测。
4. client closing 短路 + `_gen_closing_token` + speculative 接线 + 单测(含 D2 cancel)。
5. 全量 pytest + ruff + pyright → D3 迁移清单 → maintenance-log → rebuild。
6. 灰度观察"晚安"是否被对称回应、是否刷屏。

## 风险与回滚

- closing 词表误伤 → 检测要求短+句尾+无疑问(负例已覆盖)。
- 刷屏 → 第3层去重+冷却**同批**上线(不可拆)。
- token 延迟 → speculative 并行(零额外串行)+超时 fallback 静态池。
- 回滚：router closing 注入 + scheduler bypass 分支注释即回二元决策；thinker light_kind / mode="closing" / slot 新字段均 additive，不影响旧路径。改 .py 需 rebuild bot。
