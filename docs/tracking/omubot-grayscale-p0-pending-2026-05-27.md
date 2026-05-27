# 灰度十七问题 — P0 候选方案审议清单（待用户定夺）

> 抽取自：[omubot-grayscale-issues-2026-05-26-solutions.md](omubot-grayscale-issues-2026-05-26-solutions.md)
>
> 配套诊断：[omubot-grayscale-issues-2026-05-26.md](omubot-grayscale-issues-2026-05-26.md)
>
> 范围：仅 5 个 **P0** issue（#7 / #1 / #3 / #10 / #13），P1/P2/P3 不在此列。
>
> 本文不下结论、不动代码。每个 issue 列推荐方案 + 备选 + **必须由用户拍板的决策点**。
>
> 落地原则：A 簇 5 件出口 guardrail 共一次落地 / B 簇入口 layer 共一次落地。下方"共骨架矩阵"列了 5 件之间的复用关系。

## 一览表

| # | 标题 | 紧迫性 | 推荐方案 | 备选 | 落地簇 | 行数估 |
| --- | --- | --- | --- | --- | --- | --- |
| 7  | 多 bot 互引死循环 | **高**（下次成本不对称） | 7A BotPairLoopGuard sliding-window + cooldown | 7B 手动 blocked_users（应急）；7C prompt-only（劣） | B 簇入口 | 250–350 |
| 1  | sentinel token 泄漏 | 中（持续小漏） | 1A post-LLM sentinel registry + pipeline guardrail | 1B 嵌 humanization sanitizer；1C prompt 自审（劣） | A 簇出口 | 200–300 |
| 3  | message coalescing | 中（节奏伤害） | 3A 独立 coalescer 服务 + config-driven 窗口 | 3B Redis backed（暂不做）；3C scheduler 内 debounce（stop-gap） | B 簇入口 | 400–500 |
| 10 | 近重复回应 / 自我相似度盲区 | 中（issue 7+8 放大器） | 10A n-gram dedup gate + per-group `asyncio.Lock` 双层 | 10B 仅 dedup gate；10C prompt-only（劣） | A 出口 + B 入口 | 250–350 |
| 13 | thinker 内部状态文本泄漏 | 中（破沉浸最严重） | 13A ThinkDecision 字段重构 + phrase detector + schedule enum 化 三层 | 13B 仅 phrase strip（可作 phase 1）；13C 关掉 thinker（劣） | A 出口 + C source | 350–450 |

## 共骨架矩阵（决定能否合并 PR）

| 件 | 与谁共骨架 | 共什么 |
| --- | --- | --- |
| F1 sentinel | F8 第二刀 / F9 ✨ watcher / F10 dedup / F13 phrase | A 簇出口 hook（client.py reply 出口、scheduler `_send_to_group` 前） |
| F3 coalescer | F7 pair guard / F17 burst window | B 簇入口 hook（router group_listener）+ TTLCache + `(gid, sender_id)` keyspace |
| F7 pair guard | F3 coalescer / F12 known_other_bots / F10 lock | router 入口序列化 + `(gid, peer_id)` keyspace |
| F10 dedup + lock | F1（dedup）/ F7（lock 同模式）/ F8 第一刀（重复素材源头） | dedup 在 A 簇出口；lock 在 B 簇入口；两侧分别合并 |
| F13 phrase + 字段重构 | F1（phrase 同位点）/ F4 / F8 第一刀（共"自由叙事 → enum"治本骨架） | A 簇 phrase + C 簇 ThinkDecision/schedule enum |

> 直接含义：F1+F10 dedup+F13 phrase（A 簇出口三件）值得合并 1 个 PR；F7+F3+F10 lock（B 簇入口三件）值得合并 1 个 PR；F13 字段重构是独立的 C 簇 breaking change，单独 PR。

## 推荐落地顺序（按"骨架共用度"非"紧迫性"排）

1. **A 簇出口三件合并 PR**：F1 + F10 dedup gate + F13 phrase detector（共 sentinel registry / pattern / dedup pipeline；估 550–800 行）
2. **B 簇入口三件合并 PR**：F7 + F3 + F10 group lock（共 router 入口 + TTLCache + `(gid, *)` keyspace；估 900–1200 行）
3. **C 簇独立 PR**：F13 ThinkDecision 字段重构（breaking change，需 thinker prompt 同步重写 + retry-on-parse-fail 兜底；估 200–250 行）

> 备选顺序：若用户希望先快速止血，可先落 13B 仅 phrase strip（100–150 行，A 簇骨架内最小可发布单元），下一轮再做 13A 字段重构。

## 派发分单索引（2026-05-27 用户拍板：方案全按推荐 / PR 自主分阶段 / 单内必须全做完才闭环）

| 派单 | 范围 | 串行顺序 | 执行追踪文档 | 收口标准摘要 |
| --- | --- | --- | --- | --- |
| **派单 1** | A 簇出口三件 1 PR：F1 sentinel registry + F10 dedup gate + F13 phrase detector | 1 | [omubot-grayscale-p0-dispatch-1-cluster-a-execution.md](omubot-grayscale-p0-dispatch-1-cluster-a-execution.md) | 7 个 metric 写入 + 灰度 24h 观察 + e2e + maintenance-log |
| **派单 2** | B 簇入口三件 1 PR：F7 BotPairLoopGuard + F3 MessageCoalescer + F10 per-group chat_lock | 2（依赖派单 1） | [omubot-grayscale-p0-dispatch-2-cluster-b-execution.md](omubot-grayscale-p0-dispatch-2-cluster-b-execution.md) | 6 个 metric 写入 + 灰度 24h 观察 + 用户体感无明显延迟 + e2e |
| **派单 3** | C 簇 13A 独立 PR：thinker ThinkDecision 字段重构 + schedule activity enum 化（breaking change） | 3（依赖派单 1） | [omubot-grayscale-p0-dispatch-3-cluster-c-execution.md](omubot-grayscale-p0-dispatch-3-cluster-c-execution.md) | thinker 内部状态文本泄漏率近 0 + phrase detector 命中率较前下降 ≥ 80% + 灰度 24h |

> 串行原因：派单 2 的 chat_lock 与派单 1 的 dedup gate 必须共测一次端到端；派单 3 的字段重构会让派单 1 的 phrase detector 命中率自然下降到近 0（thought 不再进 system_blocks），需要在派单 1 已上线后才能观测此降幅。
>
> 每个派单内 Wave 严格串行；Wave 0 是零代码前置验证，必须先完成才能领 Wave 1 单。
>
> 单内任一 Wave 不闭环即整单不闭环——不接受单件分发布或 phase-1 降级。
