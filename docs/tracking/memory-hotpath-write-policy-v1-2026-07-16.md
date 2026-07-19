# 记忆热写入冲突感知策略 v1

> 状态：独立复审与 correction TDD 完成，代码验收，未部署 · 2026-07-16
> 前序：`docs/tracking/memory-system-frontier-audit-refactor-2026-07-15.md`
> 迁移：`docs/migrations/memory-hotpath-write-policy-v1-2026-07-16.md`

## 目标

把 `MemoExtractor` 从“每条抽取结果直接新增 active card”升级为受约束的
`add / reinforce / supersede / skip` 写入策略，减少重复与双重真相，同时保留
scope、证据、软失效和回滚能力。该切片是后续 temporal trace / premise awareness
与图检索增强的输入质量前提。

## 前沿依据

- Mem0（arXiv:2504.19413）：长期记忆需要动态 extract、consolidate、retrieve，而非只追加。
- A-MEM（arXiv:2502.12110）：新记忆写入时建立链接并推动历史记忆演化。
- MemGPT / Letta（arXiv:2310.08560）：事实变化通过受控 memory replace/edit，而非无界堆叠。
- Graphiti / Zep（arXiv:2501.13956）：保留历史关系和时间演化，不物理抹除证据。
- MemTrace（arXiv:2606.17328）：按 knowledge point 检查 current / earlier / trajectory；后续时间轨迹能力依赖干净 supersede 链。

## 本地证据

- 前：`MemoExtractor.extract_after_turn` 只调用 `CardStore.add_card`；extractor supersedes=0。
- 后：JSONL 策略 + 确定性 duplicate + 显式 supersede 信号 + `memory_card_observations` + 原子 supersede/reinforce。

## 冻结合同（已实现）

1. 作用域仍为 `user/<user_id>`；v1 不自动写群卡，不跨用户比较。
2. 抽取器单次 LLM 请求接收当前用户 top-24 active cards 与本轮对话，输出 JSONL；旧 `[category] content` 兼容为 `add`。
3. action：`add | reinforce | supersede | skip`。显式 target 只允许本次 prompt 实际展示的 card，且同 scope、同 category；全量 user active 只用于确定性 duplicate 防重。
4. 仅 exact / 标点归一化或高阈值 ngram 近重复可确定性改写为 `reinforce`；包含扩展（如“喜欢猫”→“喜欢猫和狗”）不强制合并。`supersede` 必须由当前 user message 的更新线索与新内容词汇证据共同授权，LLM content 不能自证。
5. `add` / `supersede` 透传 `source_message_id` 与 `captured_by=memo_extractor`。
6. `reinforce` 持久记录 evidence observation。
7. `CardStore` 同实例写操作串行化；`supersede` 以 `BEGIN IMMEDIATE` 在同一事务完成旧卡条件失效、新卡与 evidence 写入；取消/异常/并发 loser 不得留下双 active 或孤儿 observation。
8. RetrievalGate 继续只注入 active；历史证据保留。
9. `add/reinforce/supersede/skip/invalid` 计数经 `MemoExtractor.stats` / `get_write_stats()`。
10. `write_policy_enabled` 默认 true；false 恢复 legacy add-only。

## 实现文件

- `plugins/memo/plugin.py`（version 1.1.6）
- `services/memory/card_store.py`
- `services/memory/write_policy.py`（NEW）
- `bootstrap/chat_runtime.py`
- `plugins/memo/config.default.json` / `config.schema.json` / `plugin.json`
- `tests/test_memo_extractor_write_policy.py`（NEW）
- `tests/test_card_store.py`（扩展）

## Test Ledger

| 时间 | 命令/实验 | 实际结果 | 结论 |
| --- | --- | --- | --- |
| 2026-07-16 | 只读 `memory_cards.db` 聚合审计 | 114 total；extractor 43 = 20 expired / 11 superseded / 12 active；extractor supersedes=0 | 热写入缺少冲突/演化策略 |
| 2026-07-16 | Grok 前沿/代码只读审计 | 推荐 hot-path conflict-aware write policy | 合同冻结 |
| 2026-07-16 | RED：`pytest tests/test_memo_extractor_write_policy.py tests/test_card_store.py -q` | **31 failed, 48 passed**（`/tmp/memo_write_policy_red.txt`） | TDD 分离：RED 仅测，无生产编辑 |
| 2026-07-16 | GREEN focused 同命令 | **79 passed**（`/tmp/memo_write_policy_green.txt`） | 合同 A–F 落地 |
| 2026-07-16 | related memo/tools/manifest/app/context | **103 passed** | 兼容既有调用方 |
| 2026-07-16 | scoped Ruff + Pyright | clean / 0 errors | 静态通过 |
| 2026-07-16 | full `pytest -q` | **4020 passed, 17 skipped, 190 warnings** | 全仓绿 |
| 2026-07-16 | JSON parse config/schema/plugin | ok；version 1.1.6；restart field 含 write_policy_enabled | 配置契约一致 |
| 2026-07-16 | 独立 Grok review + Codex 复核 | 0 Critical；6 Important / 多项 permissive test gap | 24-cap 去重域、signal/content self-auth、containment、TOCTOU、dirty bootstrap 边界进入 correction |
| 2026-07-16 | correction RED → GREEN | **11 failed, 85 passed → 96 passed** | 全量去重、signal lexical support、非法 target、write lock、并发 supersede、scope owner 守卫落地 |
| 2026-07-16 | prompt target allowlist correction | **1 failed, 1 passed → focused 97 passed** | 显式 target 恢复为 prompt-window only；全量 active 仅供 add duplicate |
| 2026-07-16 | 首轮 full 复现 Dream 分类纠正回归 | **1 failed, 4037 passed**；direct + Dream 精确 **2 failed** | store 同 category 下沉过度；Extractor 同 category 合同与 trusted store category correction 分层 |
| 2026-07-16 | 最终 focused / related / static | write-policy+CardStore **97 passed**；Dream+related **123 passed**；Ruff clean；Pyright **0 errors** | prompt/parser/transaction/scope/legacy 兼容独立验收通过 |
| 2026-07-16 | 最终 full `pytest -q` | **4038 passed, 17 skipped, 186 warnings** | correction 净增 18 tests；全仓无回归 |

## 同模式扫描（D1）

| 位点 | 结果 |
| --- | --- |
| `plugins/memo/plugin.py` extractor 写入 | 已切 write-policy / legacy 双路径 |
| `CardStore.supersede_card` 调用方（Dream/tools） | 保留无 kwargs 与同 owner 分类纠正语义；跨 scope/scope_id 拒绝 |
| `CardStore.reinforce` 无 evidence | legacy conf+last_seen 保留 |
| 其他 hot-path memo 写入 | 无第二 extractor；group/global 自动写未引入 |
| `find_similar` | 仍供旧 API；write policy **不**依赖其语义 |

## 风险与回滚

- LLM action/target 仍可能不稳：prompt-window allowlist + 同 scope/category + user-message lexical signal；失败保守 skip/add。
- Supersede 与 duplicate 规则刻意偏保守：隐晦更新或非近似释义可能漏合并，优先避免误覆盖。
- `_write_lock` 为进程内同实例保护；多进程仍依赖 SQLite `BEGIN IMMEDIATE` 与条件状态更新。
- 新 observation 表 additive；配置 `write_policy_enabled=false` 即可 legacy 回退。
- 工作树含大量既有脏改；禁止 `git add -A`。

## 下一步

1. 启动 temporal trace / false-premise awareness 切片：先审计当前 query→current/earlier/trajectory 证据使用缺口，再冻结最小合同。
2. 可选后续：Admin 只读 observation 列表；metrics 接线。
3. 本切片未部署；未来部署仅 recreate bot，永不 recreate NapCat。
