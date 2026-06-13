# Reply Gating Research - 2026-05-10

## Question

用户问：是否存在“先用简短 LLM/轻量判别器判断这一轮是否应该回复，再决定是否进入完整回复生成”的成熟做法；如果存在，怎样保证快捷、迅速、轻量。

## Local Pulls

本轮资料已拉取到本机临时目录，未纳入仓库依赖：

- `/private/tmp/omubot-research/llm-reply-gating/RouteLLM`
- `/private/tmp/omubot-research/llm-reply-gating/semantic-router`
- `/private/tmp/omubot-research/llm-reply-gating/papers/timelychat-2506.14285.pdf`
- `/private/tmp/omubot-research/llm-reply-gating/papers/routellm-2406.18665.pdf`
- `/private/tmp/omubot-research/llm-reply-gating/papers/response-selection-sigdial-2022.pdf`

## External Evidence

### TimelyChat / TIMER

Paper: https://arxiv.org/abs/2506.14285

要点：开放域对话不只要决定“说什么”，还要决定“什么时候说”。论文把 timing prediction 作为任务的一部分，让模型预测时间间隔并生成对应回复。它证明了 proactive dialogue agent 需要对“立即回复、延迟回复、不合适立即回复”做判断。

对 Omubot 的启发：群聊里的 `继续说嘛`、`然后呢`、`多说点` 属于明确 continuation intent，应直接转成强触发；普通闲聊则可以经过轻量 gate 判断是否值得插话。

### RouteLLM

Paper: https://arxiv.org/abs/2406.18665
Project: https://github.com/lm-sys/RouteLLM

要点：RouteLLM 用一个 router 在调用重模型前做二元决策，把请求路由到强模型或弱模型。核心是“先轻量预测收益，再用阈值决定是否支付更高成本”。它强调理想 router 应尽量只调用一个后端模型，避免多模型级联带来的延迟。

对 Omubot 的启发：`should_reply` 可以建模成同类二元路由：`reply` vs `pass`，阈值由群配置、心情、时间段和连续跳过次数调节。不要每条消息都先打一遍完整 LLM。

### Semantic Router

Project: https://github.com/aurelio-labs/semantic-router

要点：semantic-router 是一个快速语义决策层，用 route utterances + encoder + score threshold 做路由；未命中时返回空决策。它也支持每个 route 独立阈值和本地执行。

对 Omubot 的启发：明确短意图先用规则/短语/embedding route 兜住，例如 continuation、help request、bot mentioned by nickname、topic interest。只有模糊场景才进入轻量 LLM gate。

### Response Selection for Open Domain Dialogue

Paper: https://aclanthology.org/2022.sigdial-1.30/

要点：成熟开放域对话系统常有多个 response generator，再用 response selector/ranker 判断候选是否合适。论文强调真实 hard negatives 比随机负例更有价值。

对 Omubot 的启发：如果后续做训练型 gate，日志样本应来自真实群聊中的“应该回/不该回/回了很怪/没回很怪”，而不是只靠合成数据。

## Recommended Omubot Shape

不建议第一步就“每条普通消息调用一个小 LLM”。推荐四层 gate：

1. Hard triggers: `@bot`、reply bot、continuation intent、插件强触发、视频 always。
2. Cheap local intent: 正则/短语/昵称/最近 assistant 完成状态/用户是否在追问。
3. Cheap score gate: 当前概率系统继续保留，叠加 mood/time/skip/busy/interval。
4. Ambiguous LLM gate: 只在本地规则不确定、概率在灰区、且群不忙时调用小模型，输出严格 JSON：`{"action":"reply|pass","confidence":0-1,"reason":"..."}`。

## Latency Budget

建议默认不让 LLM gate 进入热路径。只有满足以下条件才调用：

- 非强触发；
- 当前没有同群 running task；
- 不在 `planner_smooth` 硬冷却内；
- 概率分数处于灰区，例如 `0.25 <= threshold <= 0.65`；
- 最近 N 条里存在问句、情绪高、点名昵称、开放话题、或 bot 上轮刚说完后的承接词。

超时预算建议：

- local rule: 1 ms 级；
- embedding/semantic route: 10-50 ms，最好本地缓存；
- tiny LLM gate: 300-800 ms 超时，超时直接走原概率策略；
- full LLM reply: 只在 gate 决定 reply 后进入。

## Safety Rules

- 强触发永远绕过 gate：`@bot`、directed followup、明确 continuation request。
- gate 只能把普通消息从 probability 升级为 reply 或降级为 pass，不能吞掉强触发。
- gate 结果写入日志：`gate=rule|prob|llm action=reply|pass confidence=... reason=... latency_ms=...`。
- 初期 shadow mode：先只记录 gate 决策，不影响真实回复，积累 1-2 天日志后再打开。

## Conclusion

这种做法存在，而且很常见；成熟系统不会用“完整大模型生成”来判断每条消息是否回复，而是用规则、语义路由、轻量分类器、小模型、阈值和超时组成分层 gate。

Omubot 当前最该先做的是：

1. 把 `继续说/接着说/然后呢/多说点/展开说` 接入 `directed_followup`，这是确定性强触发，不该走概率。
2. 为普通非强触发新增 `reply_gate` shadow log，先观测。
3. 再决定是否加 tiny LLM gate，默认只处理灰区，不进所有消息热路径。
