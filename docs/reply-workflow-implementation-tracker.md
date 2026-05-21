# Omubot 回复工作流改造跟踪

本文跟踪 Omubot 回复决策、等待、主动回复意图的后续改造。方案依据本机已拉取的论文、成熟项目和 MaiBot 代码，不把临时想法直接写成实现任务。

## 当前状态

- 状态：Phase 1 shadow 观测已落地；`rules` 正则消费路线已废弃；Phase 2 私聊 actor 与纯语义 `wait` 已验收。
- 当前阶段：Phase 2 私聊 `wait/complete` 收口完成，准备进入 Phase 3 主动意图持久化。
- 改造目标：让 Omubot 的“是否回复、何时回复、是否等待、是否主动后续”从概率分支升级为可观测、可扩展的会话工作流。
- 优先范围：先做私聊 `wait / complete / proactive intent`，再处理群聊 reply gate。
- 暂不改动：NapCat、发送队列、reply batch、分段器、群聊首段释放策略。

## 本地参考资料

本轮研究材料位于：

- `/private/tmp/omubot-research/private-reply-workflow/`
- `/private/tmp/omubot-research/llm-reply-gating/`

已查看的主要项目和论文：

| 类型 | 名称 | 本地路径 | 对本方案的启发 |
| --- | --- | --- | --- |
| 成熟项目 | Rasa | `/private/tmp/omubot-research/private-reply-workflow/projects/rasa` | event tracker、`action_listen`、reminder、pause/resume |
| 成熟项目 | BotBuilder Samples | `/private/tmp/omubot-research/private-reply-workflow/projects/BotBuilder-Samples` | proactive message 必须基于已保存 conversation reference |
| 成熟项目 | Chirpy Cardinal | `/private/tmp/omubot-research/private-reply-workflow/projects/chirpycardinal` | 多 response generator + priority ranking |
| 成熟项目 | TurnGPT | `/private/tmp/omubot-research/private-reply-workflow/projects/TurnGPT` | wait/listen 属于 turn-taking/timing 问题 |
| 本机项目 | MaiBot | `/Users/kragcola/MaiM-with-u/MaiBot` | 私聊 planner 显式决策 `reply / wait / complete_talk` |
| 论文 | TimelyChat | `/private/tmp/omubot-research/private-reply-workflow/papers/timelychat-2506.14285.pdf` | 开放域对话需要同时判断 what 与 when |
| 论文 | TurnGPT | `/private/tmp/omubot-research/private-reply-workflow/papers/turngpt-2010.10874.pdf` | 轮次完成点可被建模，不应只靠固定冷却 |
| 论文 | Response Selection SIGDIAL 2022 | `/private/tmp/omubot-research/private-reply-workflow/papers/response-selection-sigdial-2022.pdf` | 候选回复应通过 selector/ranker 选择 |

## 现状约束

当前 Omubot 群聊路径主要在 `kernel/router.py` 与 `services/scheduler.py`：

- `@bot`、`directed_followup`、`video_always` 会绕过概率直接触发。
- 普通消息在 scheduler 中动态计算 `threshold` 后用 `random.random() < threshold` 决定是否回复。
- `threshold` 是每条消息即时计算值，不是可事后查询的持久状态。
- `directed_followup` 不能继续扩词表；“继续说嘛/继续说呢”说明正则召回不能作为真实行为判断。
- 高歧义短词如果直接加入强触发，群聊中误触率会高。
- 同群并发改造已有独立跟踪文档：`docs/group-concurrency-implementation-tracker.md`。

## 核心设计决策

| 日期 | 决策 | 原因 |
| --- | --- | --- |
| 2026-05-10 | 私聊和群聊回复工作流分开设计 | 私聊需要拟人连续对话；群聊需要低打扰和防误触 |
| 2026-05-10 | 先实现私聊 actor，再推进群聊 gate | 私聊没有多人误触问题，更适合引入 `wait` 和主动后续 |
| 2026-05-10 | `wait` 做成显式动作，不等同于冷却 | MaiBot、TurnGPT、TimelyChat 都说明等待是会话时机问题 |
| 2026-05-10 | 主动回复必须有来源事件 | 参考 Bot Framework / Rasa，避免 bot 凭空打扰用户 |
| 2026-05-10 | 群聊高歧义承接词不直接强触发 | “继续/然后呢/还有呢”在群聊不一定指向 bot |
| 2026-05-10 | tiny LLM gate 放到 actor 队列之后 | 避免和 `running_task`、动态概率、并发状态产生竞态 |
| 2026-05-10 | 初期所有 gate 先 shadow log | 先拿真实日志校准误触/漏触，再改变线上行为 |
| 2026-05-10 | 废弃 `rules` 正则消费路线 | “继续说嘛”触发但“继续说呢”漏触，证明表层规则不应直接决定回复 |
| 2026-05-10 | semantic gate 高置信才消费 | 规则只做候选召回；是否回复由小模型基于短上下文输出 JSON 决策 |
| 2026-05-11 | 私聊 `wait` 只由 thinker 语义判断 | 本地短语硬识别不优雅且容易漂移；MaiBot 参考点是 planner 做 turn-taking 决策 |
| 2026-05-11 | 私聊 `thinker_wait` 必须进入 actor 日志 | 线上排查需要区分“语义决定等待”和“LLM 空回复” |

## 目标架构

### 1. ConversationActor

每个会话一个 actor，actor 内串行处理事件：

```text
IncomingMessage
  -> normalize event
  -> update conversation state
  -> local decision / planner decision
  -> action: reply | wait | listen | complete | schedule_followup | pass
  -> persist event + metrics
```

群聊 actor 与私聊 actor 使用同一事件模型，但策略不同。

### 2. PrivateConversationActor

私聊不再依赖“概率命中才回”。收到用户消息后进入会话循环：

- `reply`：立即回复用户。
- `wait`：等待指定秒数，可被新消息打断。
- `listen`：短等待，用于判断对方是否还没说完。
- `complete`：本轮会话结束，直到收到新消息再启动。
- `schedule_followup`：登记明确来源的主动后续。
- `proactive_reply`：执行已到期且仍有效的主动意图。

私聊第一版只落 `reply / wait / complete`，不急着引入小模型 gate。

### 3. GroupConversationActor

群聊保持低打扰：

- 强触发：`@bot`、reply bot、命令、插件明确触发、低歧义 directed followup。
- 约束强触发：`继续说嘛/接着讲/展开说` 等需要满足“最近 bot 回复同一用户、无其他 @、时间窗口内、最好引用/回复 bot”。
- 高歧义短词：`继续/然后呢/还有呢` 默认只 shadow 或 boost，不直接强制回复。
- 普通消息：保留当前概率系统，但将 gate 决策和动态阈值写入观测日志。

### 4. ProactiveIntentStore

主动后续必须持久化，且带来源和取消规则：

```text
ProactiveIntent(
  intent_id,
  conversation_id,
  user_id,
  source_event_id,
  source_type,
  due_time,
  cancel_on_user_message,
  max_attempts,
  status,
  reason,
)
```

允许的来源：

- 用户明确请求提醒或稍后继续。
- bot 明确承诺“查完/想完/到点告诉你”。
- 插件明确产生事件，例如日程、订阅、外部通知。

不允许的来源：

- 仅凭心情随机找人说话。
- 没有事件 id 的隐式主动回复。

### 5. ReplyGateDecision

gate 输出不要用含混的 `candidate`，建议固定为：

```text
force_reply
boost(prob_delta)
wait(seconds)
pass
suppress(reason)
```

群聊消费 `force_reply / boost / pass / suppress`。  
私聊额外消费 `wait`。

## 事件模型草案

第一版只需要轻量 JSON/SQLite 事件，不引入外部 runtime：

| 事件 | 用途 |
| --- | --- |
| `UserMessageReceived` | 记录进入 actor 的用户消息 |
| `DecisionMade` | 记录 gate/planner 的动作、理由、耗时 |
| `AssistantReplyStarted` | 回复开始生成 |
| `AssistantReplySent` | 回复已可见发送 |
| `WaitStarted` | 进入等待 |
| `WaitInterrupted` | 用户新消息打断等待 |
| `WaitExpired` | 等待超时 |
| `ConversationCompleted` | 会话完成，等待下一条用户消息 |
| `ProactiveIntentScheduled` | 主动意图已登记 |
| `ProactiveIntentCancelled` | 主动意图已取消 |
| `ProactiveIntentFired` | 主动意图已触发 |
| `ProactiveIntentSuppressed` | 到期但被冷却、静默时间、用户消息等规则压制 |

## 实施清单

| 阶段 | 项目 | 状态 | 验收 |
| --- | --- | --- | --- |
| Phase 0 | 写入方案与证据追踪 | 已完成 | 本文档存在，能说明来源、边界、阶段 |
| Phase 1 | 新增 reply workflow shadow log | 已实现 | router 记录 group/private shadow；scheduler 记录真实 fire/skip/suppress 路径 |
| Phase 1 | 定义 `ReplyGateDecision` 数据结构 | 已实现 | 单测覆盖 `force_reply/boost/pass/suppress` 及私聊当前路径 |
| Phase 1 | directed followup 风险词分级 | 已实现 | `继续说嘛` 识别为明确承接；`然后呢` 等高歧义词不强触发 |
| Phase 1.5 | `rules` 模式消费低风险明确承接 | 已废弃 | 旧配置值兼容读取为 `shadow`，不再影响真实行为 |
| Phase 1.6 | semantic gate 高置信消费 | 已实现 | `semantic` 模式下，小模型 `force_reply` 且置信度达阈值才转成 `directed_followup` |
| Phase 2 | 私聊 `PrivateConversationActor` 最小骨架 | 已实现 | 私聊消息串行处理，不影响群聊 scheduler |
| Phase 2 | 私聊 `wait` 可被新消息打断 | 已实现 | 测试覆盖 wait interrupted |
| Phase 2 | 私聊 `complete` 后等待新消息恢复 | 已实现 | complete 不会导致私聊永久沉默 |
| Phase 2 | 私聊 thinker 语义 `wait` | 已实现并验收 | 不做本地关键词硬判；`那个`、`我想啊`、`就是` 线上进入 `wait` |
| Phase 2 | 私聊 wait 原因回传 | 已实现并验收 | `reply_workflow` 记录 `reason=thinker_wait` 与 `thinker_thought` |
| Phase 3 | `ProactiveIntentStore` | 待做 | 意图可持久化、取消、到期触发、幂等执行 |
| Phase 3 | 主动后续仅允许明确来源 | 待做 | 无 source_event_id 的主动意图被拒绝 |
| Phase 4 | 群聊 reply gate boost | 待做 | `继续说嘛` 等明确承接可提权，高歧义短词只 boost/shadow |
| Phase 4 | gate latency 与决策日志 | 待做 | 日志含 `gate/action/confidence/latency_ms/reason` |
| Phase 5 | 候选生成器与 ranker | 暂缓 | Food/视频/闲聊/记忆/主动意图可作为候选源统一排序 |
| Phase 6 | tiny LLM gate 灰区试验 | 暂缓 | 只在 actor 队列内、灰区概率、无忙碌任务时调用 |

## 风险跟踪

| 风险 | 等级 | 状态 | 缓解 |
| --- | --- | --- | --- |
| “继续/然后呢”在群聊误触 | 高 | 设计中 | 高歧义词必须满足引用 bot、同用户、无其他 @、短时间窗口等约束 |
| tiny LLM gate 增加热路径延迟 | 中 | 暂缓 | Phase 6 才做；先 shadow；超时后降级 |
| gate 与 scheduler `running_task` 状态竞态 | 中 | 暂缓 | gate 放入 actor 队列后再启用行为影响 |
| 主动回复显得打扰 | 高 | 设计中 | 主动意图必须有来源、取消规则、静默时间、最大次数 |
| 私聊 actor 与现有 router/plugin 路径冲突 | 中 | 已缓解 | 线上已验证私聊 `wait/complete` 状态流正常，不影响群聊 scheduler |
| 纯语义 `wait` 依赖模型稳定性 | 中 | 观察中 | 保留 thinker 日志与 actor reason；只通过 prompt 微调，不回退到短语硬规则 |
| 事件日志膨胀 | 低 | 待评估 | 设置保留期和按会话压缩策略 |
| 与群聊并发 actor 化重复造状态 | 中 | 设计中 | 共用事件模型；群聊具体迁移等待并发 tracker 收口 |

## 观测日志要求

所有新决策至少记录：

```text
reply_workflow |
conversation=private_123
event_id=...
mode=private_actor|group_gate_shadow|semantic_gate|group_gate
action=reply|wait|complete|boost|pass|suppress
source=rule|planner|llm_gate|proactive_intent
confidence=...
latency_ms=...
reason=...
```

主动意图触发还要记录：

```text
proactive_intent |
intent_id=...
source_event_id=...
status=scheduled|cancelled|fired|suppressed|expired
cancel_on_user_message=true|false
attempt=...
```

## 验证计划

第一轮实现后最小验证：

```bash
source ./scripts/dev/env.sh
uv run pytest tests/test_config_loader.py tests/test_scheduler.py -q
uv run ruff check services kernel plugins tests
```

后续按落地文件增加：

- `tests/test_reply_gate.py`
- `tests/test_private_conversation_actor.py`
- `tests/test_proactive_intent_store.py`

## 和现有工作的边界

- 分段和 reply batch 已在 `docs/group-concurrency-implementation-tracker.md` 跟踪，本方案不改发送层。
- reply gate 研究材料见 `docs/audits/reply-gating-research-2026-05-10.md`。
- 本方案可以复用群聊并发方案的 actor 思路，但不会在 Phase 1-3 重写群聊 scheduler。
- NapCat 不属于本方案范围。

## 更新日志

- 2026-05-10：创建追踪文档；归档 Rasa、BotBuilder、Chirpy、TurnGPT、TimelyChat、MaiBot 对 Omubot 回复工作流的设计启发；确定先私聊 wait/proactive，后群聊 gate。
- 2026-05-10：Phase 1 shadow 观测落地：新增 `services/reply_workflow.py`，配置 `reply_workflow.mode=shadow|off`，router 记录群聊/私聊 shadow 决策，scheduler 记录真实调度 fire/skip/suppress 路径；不改变现有回复行为。下一步需要真人复查线上 `reply_workflow` 日志的误触/漏触样本。
- 2026-05-10：根据线上日志 `05:51:24 继续说嘛` 修正漏触：`group_gate_shadow` 已判 `action=force_reply`、`followup_kind=explicit_continuation`，但 scheduler 同一消息走 `probability_skip threshold=0.348`。新增 `reply_workflow.mode=rules`，只消费低风险明确承接；`shadow` 仍保持只观测。新增约束：最新 assistant 回复必须在窗口内回答当前用户，多人上一轮或其他 @ 不强触发。
- 2026-05-10：废弃 `rules` 方向：用户复测指出“继续说嘛”触发但“继续说呢”不触发，根因是正则词表泄漏成行为逻辑。`rules` 兼容读取为 `shadow`；新增 `semantic` 模式，规则只做候选过滤，语义 gate 使用 `reply_gate` LLM task 输出 `force_reply|pass|suppress` JSON，高置信才消费为 `directed_followup`，超时/解析失败/低置信全部 fail closed。
- 2026-05-10：semantic 线上复测发现 `然后呢然后呢`、`那之后呢`、`继续说呢` 均进入 `short_contextual_candidate`，但 `semantic_timeout_ms=600` 导致 DeepSeek `reply_gate` 三次约 604ms 被截断，随后 scheduler 走原概率并 skip。容器内短 gate 基准显示首字节约 946ms、总耗时约 1598ms，且返回 `force_reply confidence=0.95`；因此默认与本机配置调整为 `semantic_timeout_ms=2200`，并补充 `error_type=TimeoutError` 诊断日志。
- 2026-05-11：私聊 actor 最小骨架已落地。新增 `services/private_conversation.py` 维护 in-process 会话状态，router 私聊路径开始记录 `waiting` / `complete` / `interrupted_wait` / `resumed_from_complete`，并在新消息到来时串行恢复状态。补充 `tests/test_private_conversation.py`，验证 wait 进入、wait 被打断、complete 后恢复和未终态回退到 idle。下一步进入主动后续意图持久化。
- 2026-05-11：私聊 `wait` 收口为纯语义路线：删除本地短语硬识别，只通过 thinker prompt 判断“请求回应”还是“用户还在组织语言”；`LLMClient.chat()` 传入 `conversation_mode=private`，router 将 thinker 的 `wait` 回传为 `reply_workflow reason=thinker_wait`。聚焦验证通过：`uv run pytest tests/test_thinker.py tests/test_client.py -q`（69 passed），`uv run ruff check services/llm/thinker.py services/llm/client.py kernel/router.py tests/test_thinker.py tests/test_client.py` 通过。
- 2026-05-11：Docker 重建并只重启 `bot` 容器后线上验收通过：`那个`、`我想啊`、`就是`、`就是啊` 均记录 `thinker | action=wait` 与 `reply_workflow reason=thinker_wait`；`我想问你个事` 正常 `action=reply`。确认 `wait` 状态可被后续消息打断并恢复为 `complete`。
