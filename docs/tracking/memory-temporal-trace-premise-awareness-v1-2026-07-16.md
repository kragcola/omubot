# 记忆时间轨迹与错误前提感知 v1

> 状态：v1 代码验收完成，三轮独立 review/correction 收口，未部署 · 2026-07-16
> 前序：`docs/tracking/memory-hotpath-write-policy-v1-2026-07-16.md`

## 目标

让长期记忆在回答前能区分同一 knowledge point 的 `current / earlier / trajectory`，
并在用户问题显式依赖已过时、被纠正或互相冲突的前提时提供受控纠正证据；正常查询仍只
注入 active truth，不把 superseded/expired 事实重新混入普通上下文。

## 前沿依据

- MemTrace（arXiv:2606.17328）：主要瓶颈常是检索到证据后没有正确使用；按 knowledge point 检查 current / earlier / trajectory。
- LongMemEval-V2（arXiv:2605.12493）：长期记忆评测应覆盖前提错误、时间更新与证据使用，而不只看检索命中。
- Graphiti / Zep（arXiv:2501.13956）：保留双时间/历史关系，当前状态与历史演化分离。
- LoCoMo（arXiv:2402.17753）：跨会话时间推理、事件演化与多跳证据需要独立评测。
- 热写入 v1 已提供 supersedes、source_message_id、observations 与原子状态演化，可作为 trace 输入。

## 调查结论

- 默认调用链为 `LLMClient → ContextPlugin → ContextService → MemoryContextSource → RetrievalGate → CardStore`；full/keyword/semantic/tool 全部 active-only。
- `ContextProvenance.supersedes_id` 仅携带 active card 的一跳 parent id；packing 不加载 parent content/observations，`valid_to` 对 card 为空。
- CardStore 可按 id 读取任意状态与 observations，但无 bounded chain walk / active chain-head API。
- tmp 反例：杭州→上海→上海浦东后，错误前提与“我以前住在哪里”均只注入上海浦东；普通“我现在住在哪里”只注入当前事实是正确基线。
- Option A 胜出：CardStore 只拥有 durable chain primitives；新 `TemporalTraceAssembler` 拥有检测/校验/DTO/render；ContextPlugin 注入独立 sidecar。RetrievalGate 与 ProviderBus 不改普通语义。

## 冻结合同

1. **普通检索不变**：RetrievalGate / MemoryContextSource / RRF / pack 继续 active-only；trace 不进入 `ContextHit` 融合与 full cache。
2. **当前消息真值**：`PromptContext` 新增 `current_message`，由 LLMClient 传本轮最新人类消息；意图/前提检测只以此为权威。`rewritten_query` 仅作候选匹配辅助，不能自造历史意图。
3. **触发门**：仅闭集历史意图（以前/之前/曾经/原来/当时/过去等）或前提候选（还记得/不是…吗/明明/我记得/仍然等）进入 trace 调查；普通“现在/目前”不触发。
4. **候选来源**：先用普通 pack 中 active memory heads；若有历史/前提触发，可额外扫描同一 resolved scope 最多 24 个 `active AND supersedes IS NOT NULL` heads。private 只用 `user/<user_id>`；group 只用现有 group/pool scopes；不读取 user/private 卡补群聊。
5. **链完整性**：head 必须 active；parent 必须逐 hop 存在且为 superseded；scope/scope_id/category 每 hop 相同；card_id 不循环。任何断链、跨 owner、跨 category、expired、分叉/歧义均 fail-closed，不用相似文本拼接无关卡。
6. **语义门**：历史询问必须与链中至少一个 node 有实质 lexical overlap；错误前提必须命中 `earlier-only` 词汇而非 current 同词。无明确 knowledge point 不暴露历史。
7. **结构输出**：最多 2 个 knowledge points、每链 head+3 earlier（max_depth=4）、最多 600 chars；current/earlier/trajectory/evidence_refs/reason/confidence 均为确定性 DTO，禁止第二次 LLM 生成叙事。
8. **证据**：每 node 暴露 card id、status、confidence、source_message_id、valid_from；earlier.valid_to 由直接 successor 的 captured/created time 推导；有 observation 才写 `obs:` refs，禁止伪造。
9. **注入**：ContextPlugin 在 ordinary pack 后添加独立 `记忆时间轨迹` dynamic block，priority 45；仅 `fact|hybrid`、takeover enabled、trace non-empty 时添加。block current-first，并声明日常以 current 为准。
10. **异常/取消**：普通 Exception / broken data 返回空 trace；`CancelledError` 传播且不得留下 partial block。
11. **配置**：`context.temporal_trace` restart-required，默认 enabled=true；`max_heads=24/max_kps=2/max_depth=4/max_chars=600` 有 schema 上限。false 可零行为回滚。context plugin bump patch version。
12. **兼容**：Dream trusted cross-category supersede 保留，但 v1 trace 对 category-change chain 保守 no-op；稀疏旧数据无 chain 时 no-op，不 backfill。

## 初始边界

- 默认 RetrievalGate / ContextService active-only 语义不变。
- earlier/superseded 仅在 query 有明确历史意图或 premise 与 current 冲突时进入单独的 trace block。
- trace 必须有 scope、category、supersedes 连续性与 source/observation evidence；不能仅按相似文本拼接。
- 输出有界：候选 knowledge point、链长、字符预算都必须硬封顶；取消/异常局部降级为无 trace。
- 不修改历史卡状态，不自动删除/回写生产数据，不做 big-bang backfill。
- 不做 PPR、GraphRAG community、全量 benchmark 下载、Admin UI、部署、QZone 或 NapCat。

## 实施结果

- 新增 `TemporalTraceAssembler`，普通 RetrievalGate / ContextHit / RRF 继续 active-only；trace 仅作为 ContextPlugin sidecar。
- group scope 复用 `GroupMemoryConfig.resolve_group_pools()`，支持 pool / `__global__`；private 不读取 group/user 外 scope。
- premise conflict 只接受 `current_message` 内 earlier-only 证据；rewrite 不可补授权；普通“我现在还住上海吗”不暴露杭州。
- CardStore 新增 active chain-head 与 max-depth=4 walk；broken/cycle/expired/cross-owner/category fail-closed。
- typed evidence refs 为 `card:` / `message:` / `obs:`；DB observation 查询与 DTO refs 双重有界。
- render 仅在 100..600 chars 合同内输出；current-first、完整说明、整行/整 ref，错误小预算 no-op。
- ContextPlugin 真实 startup 注入 CardStore + GroupMemoryConfig；empty/punctuation ordinary query 不阻断已授权 trace。
- LLMClient 单次解析最新人类消息，移除 `locals().get("current_msg")` 耦合。
- migration：`docs/migrations/memory-temporal-trace-premise-awareness-v1-2026-07-16.md`。

## 实施文件

- `services/memory/card_store.py`
- `services/memory/temporal_trace.py`（NEW）
- `kernel/types.py`、`services/llm/client.py`
- `plugins/context/plugin.py`
- `plugins/context/config.default.json`、`config.schema.json`、`plugin.json`
- `tests/test_temporal_trace.py`（NEW）、`tests/test_card_store.py`、`tests/test_context_plugin.py` / runtime wiring tests
- migration / maintenance / ACTIVE

## RED 矩阵

| ID | 场景 | 断言 |
| --- | --- | --- |
| R1 | 普通“我现在住在哪里” + 杭州→上海链 | ordinary 只含上海；无 trace/杭州 |
| R2 | 错误前提“我不是还住杭州吗” | trace reason=premise_conflict；current=上海；earlier=杭州 |
| R3 | 历史询问“我以前住在哪里” | trace reason=historical_intent；earlier=杭州；ordinary 仍 active-only |
| R4 | current_message 无意图、rewritten_query 含“以前” | 不触发，防 rewrite 自授权 |
| R5 | current_message 含历史意图、rewritten_query 丢词 | 仍触发并正确选链 |
| R6 | single active / 无 supersedes | 无 trace |
| R7 | broken/missing/cycle/expired/cross-category chain | 无 partial trace；明确 omitted reason |
| R8 | cross-user、group→user/private、跨 pool poison | 目标内容不进入 DTO/block |
| R9 | 两条弱匹配歧义 | fail-closed 或仅明确最高单链；不混链 |
| R10 | depth>4 / heads>24 / KPs>2 / chars>600 | 硬截断且不越界 |
| R11 | observations / source ids | refs 真实、有界、去重；valid_to 来自 successor |
| R12 | store 异常 / CancelledError | Exception 空；cancel 传播；PromptContext blocks 不污染 |
| R13 | flag=false / mode=skip|doc / takeover=false | 零新增 block，旧 fixture 不变 |
| R14 | Dream fact→status trusted correction | Dream 继续通过；trace 对跨 category chain no-op |

## Test Ledger

| 时间 | 命令/实验 | 实际结果 | 结论 |
| --- | --- | --- | --- |
| 2026-07-16 | hot-path v1 最终全仓 | **4038 passed, 17 skipped, 186 warnings** | trace 输入质量前提已代码验收，未部署 |
| 2026-07-16 | Grok 双 worker + Codex 只读审计 | active-only 丢失 earlier/observations；tmp 杭州→上海→浦东反例复现；Option A 推荐 | 所有权冻结为 CardStore primitives + assembler + ContextPlugin sidecar |
| 2026-07-16 | 初始 TDD RED -> GREEN | **40 failed, 88 passed -> 128 passed** | R1-R14 实现落地；未部署 |
| 2026-07-16 | 第一轮独立 review | 2 deploy blockers：group pool miss、`还住上海` 错开杭州；另有 rewrite/refs/budget/integration 缺口 | GREEN 判定为 contract-incomplete，禁止部署 |
| 2026-07-16 | 第一轮 correction | 新回归 **13 failed, 1 passed -> 145 passed**；related **234 passed** | pool/premise/typed refs/coherent budget/current-message 修正 |
| 2026-07-16 | post-fix review + 第二轮 correction | review **0 Critical**；小预算/真实 startup/false-green 修正后 focused **149 passed** | runtime Pydantic 与 schema 对齐；Admin 式 shared config mutation 已验证 |
| 2026-07-16 | Codex 最终静态 + 全仓 | Ruff clean；Pyright **0 errors**；**4116 passed, 17 skipped, 186 warnings** | 代码验收完成；未 commit/push/deploy |

## 下一步

本切片完成，返回 `memory-system-frontier-audit-refactor-2026-07-15.md`。下一步只做下一个前沿候选的只读价值/复杂度审计；不得自动部署、不得直接上 PPR/GraphRAG/Memory Tools。若未来部署，先显式授权并仅 recreate bot，永不 touch NapCat。
