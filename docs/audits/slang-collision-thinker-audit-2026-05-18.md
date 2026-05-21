# 黑话别名碰撞与 Thinker 决策上下文 — 第二轮审计

日期：2026-05-18（首版）/ 2026-05-18 复审修订 / 2026-05-18 多层记忆框架对接修订 / 2026-05-19 基石补丁同步
范围：续 `slang-module-audit-2026-05-10.md` / `multilayer-memory-learning-report-2026-05-17.md`
评审依据：2026-05-18 用户审计要点（"修了 2 条半"）+ 用户复审 5 条修正意见 + 验证复现

> **本文与 multilayer 报告的对接关系**：本文是 [`multilayer-memory-learning-report-2026-05-17.md`](multilayer-memory-learning-report-2026-05-17.md) **§ 8.1 lexical 层 prompt 注入承诺一致性** + **§ 8.2 子项 4b / 4c / 4d** 的实施细则，不是平行分支。多层记忆报告作为顶层路线图（Phase A→E + § 8 修复方案），本文是其中 lexical 层 + 决策可见性两块的可执行分解。
>
> **修订记录**：
>
> - 2026-05-18 首版：识别 2.1–2.4 + (误判) 2.5。
> - 2026-05-18 复审：用户提交 5 条修正意见全部属实。§2.5 翻转为 P0；§3 新增 2.6 / 2.7（merge_terms 跨群闸 / collision report 口径）；§4 新增 A0；§6 删除 `--status approved`；§7 时间预估更新。
> - 2026-05-18 多层记忆框架对接：把本文条目逐项映射到 multilayer report § 8.1 / § 8.2.4，多层报告同步扩展为 4a/4b/4c/4d 子项；本文成为顶层路线图的承重支柱而非独立战线。
> - 2026-05-19 基石补丁同步：multilayer Patch P2 已选择路线 A，`max_indirect_inject_terms` 默认值改回 `0`，本文 §2.5 / §8 状态同步关闭"决议待定"。

## 1. 上下文

2026-05-17 用户提交首轮审计，指出黑话工作流 3 条风险：

1. 别名碰撞（`find_existing` 不反查新候选自带的 aliases）
2. Prompt 注入不是"只注入直接命中"（`get_injectable_terms` 取前 N 排序）
3. 黑话不进 thinker 决策（thinker → on_pre_prompt 顺序）

2026-05-18 已落地修复：
- `find_existing` 改为对称查找（term + aliases 折成统一 key 集，3 步匹配）。8 个调用点中 4 个传 aliases。
- `get_injectable_terms` 改为 direct/indirect 分桶；2026-05-19 基石补丁 P2 已把 `SlangSettings.max_indirect_inject_terms` 默认值改回 `0`，恢复 direct-only 默认行为。
- 新增 `scripts/dev/slang_alias_collision_report.py`。
- 新增 4 个 store 单元测试。

测试结果：`tests/test_slang_store.py + test_slang_plugin.py + test_slang_backlog_reviewer.py` **33 passed**（原 29 + 新增 4）。

2026-05-18 用户复审结论："修了 2 条半"。本文档把**已经核实属实**的剩余缺陷固化为可执行的阶段计划。

## 2. 第二轮审计核实结果

每条结论都附验证证据（grep 行号、live DB 快照、可复现脚本）。

### 2.1 ✅ 属实：`update_term()` 缺少碰撞校验

**审计原文**：

> `update_term()` 仍可把 B 的 alias 改成 A 的 term，没有碰撞校验。见 store.py (line 2123)。我用临时库验证：甲、乙 两条存在时，`update_term(乙, aliases=['甲'])` 成功写入。

**核实**：
- [`services/slang/store.py:2123-2208`](../../services/slang/store.py#L2123) `update_term`：
  - line 2151-2155 `aliases` 字段处理只做 `_dedupe + json.dumps`，无 `find_existing` 反查
  - line 2142-2148 `term` 字段重写也未做 collision check
- 临时复现（`tmp_path` 临时库）：
  ```
  jia=slang_…32af  yi=slang_…6e89  (different)
  update_term(yi, aliases=['甲']) → ok=True
  YI now: term='乙' aliases=['甲']
  JIA still: term='甲' aliases=[]
  COLLISION CONFIRMED
  ```

**影响**：管理员"编辑词条"动作 + drift "alias_candidate" auto-merge + 任何用 `update_term(aliases=...)` 的旁路都能产生新的镜像碰撞，**绕过本次首轮修复**。

### 2.2 ✅ 属实：drift / merge 路径的 alias 写入也走 update_term

**审计原文**：

> 第 1 条需要补 `update_term()` / merge / drift alias 写入的碰撞校验和一次数据清理。

**核实**：
- [`services/slang/store.py:702-712`](../../services/slang/store.py#L702) `_maybe_create_drift_review` 的 `verdict='alias_candidate'` 分支——把 `existing.aliases + new.aliases` 合并后直接 `update_term(aliases=merged_aliases)`，没有 collision check。
- [`services/slang/store.py:2287-2291`](../../services/slang/store.py#L2287) `merge_terms` 把多 source 的 term 当 alias 合并到 target，再写 `aliases_json`——同库自身合并通常无冲突，但若 target 在另一群已被人手工编过含相同 alias 的另一 term，则可在跨范围操作时撞上。
- 修复 `update_term` 后这两路自动走新检查，无需各自再写一份。

### 2.3 ✅ 属实：Live DB 现有碰撞未清理

**审计原文**：

> 本地 live DB 现有碰撞还没清理：term↔alias 碰撞 63 组，alias↔alias 碰撞 27 组，其中 term↔alias 至少 22 组涉及 approved。

**核实**：
- 2026-05-18 跑 `scripts/dev/slang_alias_collision_report.py`：**72 对总碰撞，24 对涉及 approved**
- 量级与审计快照（63+27=90 / 22）一致；具体数字偏离是 bot 持续运行带来的自然波动
- DB schema 仅有 `idx_slang_term_scope ON slang_terms(term_key, scope, group_id)` 唯一索引，**aliases 是 `aliases_json TEXT`**，无独立索引或 DB 级唯一约束（[`services/slang/store.py:148-160`](../../services/slang/store.py#L148)）

**影响**：即便首轮修复完美阻止新碰撞，历史 72 对碰撞依然在影响 lookup 排序与 prompt 注入选择。需要一次性清理。

### 2.4 ✅ 属实：Thinker 决策完全不感知黑话

**审计原文**：

> 当前流程还是先跑 thinker，再 `bus.fire_on_pre_prompt()` 收集黑话块。`think()` 只接收 recent messages、mood、affection、identity，没有黑话 block 或 lookup 工具。

**核实**：
- [`services/llm/thinker.py:247-269`](../../services/llm/thinker.py#L247) `think()` 签名：
  ```python
  async def think(
      api_call, recent_messages, max_tokens=256,
      mood_text="", affection_text="", identity_name="Bot",
  ) -> ThinkDecision
  ```
  无任何 slang 入参。
- [`services/llm/client.py:1709`](../../services/llm/client.py#L1709) thinker 决策点
- [`services/llm/client.py:1805`](../../services/llm/client.py#L1805) `fire_on_pre_prompt`（黑话 block 在此装载）
- thinker 在前，黑话在后，无路径让 thinker 看到。

**影响**：bot 看见一条只有黑话能解释的消息时（例如群里说"伊冯今天又被礼了"），thinker 无法理解 → 可能错误地决定"不回复"或"用通用方式回复"，丢掉拟人化时机。

### 2.5 ✅ 属实（修订）：路线图承诺"只注入直命中"，曾与 indirect=2 默认值不一致

**审计原文**：

> 文档里还写"只注入直命中"，和当前默认行为不一致。

**核实**（2026-05-18 复审修订）：首版本节误判。grep 只扫了 `docs/audits/`，没扫 `docs/slang-module-implementation-tracker.md`。该 tracker 至少 3 处明确承诺过 direct-only：

- [`docs/slang-module-implementation-tracker.md:38`](../slang-module-implementation-tracker.md#L38)：`prompt block 只注入当前上下文直接命中的已批准黑话` — 设为 Phase 6 已实现条目
- [`docs/slang-module-implementation-tracker.md:94`](../slang-module-implementation-tracker.md#L94)：`Phase 6 prompt block 仅注入直命中已批准黑话 — 已实现`
- [`docs/slang-module-implementation-tracker.md:170`](../slang-module-implementation-tracker.md#L170)：`prompt block 误注入非直命中词条 — 已缓解 — build_prompt_block() 只取当前上下文 direct hits`

2026-05-18 我把 `get_injectable_terms()` 改成 direct-priority + indirect-cap，**默认 indirect=2 是 behavior regression**，不是无主中性默认值。

**决策**：本次审计应处理为 **行为回归** 修复，不是设计选择题。2026-05-19 基石补丁 P2 已选择路线 A：

- **路线 A（已落地，恢复承诺）**：默认值 `max_indirect_inject_terms` 从 2 改为 0；admin 可手动放开到 1-2 用于实验。这与三处 tracker 承诺一致。
- **路线 B（修订承诺）**：保留默认 2，但同步更新 tracker 三处条目，明确改为"direct-priority + 最多 N 条 indirect 作为群背景"，并在变更日志说明这是有意调整。

阶段 D 不再是"看跑 1-2 周再决定"，而是阶段 A 之前先二选一。

## 3. 缺陷分级与决策

| 缺陷 | 严重度 | 决策 |
|---|---|---|
| 2.1 `update_term` 无碰撞校验 | **P0** | 本轮立刻修 |
| 2.2 drift / merge 走 update_term | **P0** | 修 update_term 自动覆盖 |
| 2.3 历史 72 对碰撞未清理 | **P1** | 修完写入路径后一次性人工合并 |
| 2.4 thinker 不感知黑话 | **P1** | 用直接命中摘要 ≤200 字喂给 thinker |
| 2.5 默认 indirect=2 与 tracker 承诺不一致 | **P0** | 阶段 A 之前先二选一：恢复默认 0 / 同步改 tracker |
| 2.6（新增）`merge_terms` 缺同 scope/group 校验 | **P1** | 阶段 A 中合并修复 |
| 2.7（新增）collision report `--status` 口径误导 | **P1** | 阶段 B 之前修脚本：删 `--status` 或加 `--active-only` 口径 |

## 4. 阶段计划

每阶段独立可回滚，按顺序串行落地。

### 阶段 A0 — 默认 indirect 路线决议（P0，~5 分钟）

**前置门槛**，不动代码：选定 §2.5 二选一：

- **路线 A0-1（推荐）**：`SlangSettings.max_indirect_inject_terms` 默认 0；admin UI 保留 0–30 可调。与 tracker 三处承诺对齐。
- **路线 A0-2**：保留默认 2，同时改 [`docs/slang-module-implementation-tracker.md`](../slang-module-implementation-tracker.md) 第 38 / 94 / 170 三处条目，改为"direct-priority + 最多 N 条 indirect"，并在 `maintenance-log.md` 记录这是有意调整。

**A0 与其它阶段的依赖关系**（修订）：A0 只决定 **indirect 默认值** 与 **tracker 文案**，跟阶段 A / B / C 的代码改动 **无技术依赖**：

- 阶段 A（update_term + merge_terms 碰撞校验）：写入路径安全闸，可独立先行。
- 阶段 B（修脚本 + 清理历史碰撞）：跟 indirect 无关。
- 阶段 C（thinker 直接命中摘要）：跟 indirect 无关。

A0 只阻塞自身的"是否改默认/改 tracker"动作。

**当前状态**（2026-05-19）：基石补丁 P2 已拍板并执行 **路线 A0-1**。

- 默认值已改回 0，恢复 direct-only。
- tracker.md 的 3 条 direct-only 承诺保持原文。
- 阶段 A / B / C 是否启动由其它优先级决定，与 A0 解耦。

后续若 BlockTraceBus 采到证据证明 indirect 词条稳定有帮助，可通过 admin 设置显式调回 1 或 2；不再作为默认行为。

### 阶段 A — `update_term` + `merge_terms` 碰撞校验（P0，~1.5 小时）

**改动文件**：`services/slang/store.py` + `tests/test_slang_store.py`

**做什么**：

1. **`find_existing` 增加 `exclude_term_ids: set[str] | None = None`** kwarg（注意是 set 而不是 single id，因为 merge 要排除多个 source）。三步查询里 `AND term_id NOT IN (?,?)` 或 Python 端跳过。向后兼容：默认 None 时行为不变。
2. **`update_term` 在 `aliases` 或 `term` 字段被改写时**构造候选 key 集（新 term + 新 aliases），调 `find_existing(scope=term.scope, group_id=term.group_id, exclude_term_ids={term.term_id})`。**取消首版的 muted/expired 复用豁免**——即便对方是 muted/expired 也不允许复用 alias，否则 collision report 永远清不到 0（与阶段 B 验收冲突，详见 §2.5 复审修订）。如果业务上确实需要复用 muted 词的 alias，应先把那条 muted 词的 alias 显式清空再写新词。
   - 命中任何状态（含 muted/expired）：raise `ValueError("alias collision: would collide with term {existing.term_id} ({existing.term}, status={existing.status})")`
3. **`_maybe_create_drift_review` 的 `alias_candidate` 自动合并路径**包一层 try/except，碰撞时降级为创建 drift_review（让人工审）而不是 raise 给上游。
4. **`merge_terms` 加两道闸**（首版漏点修补）：
   - 4a. **同 scope/group 校验**：target 与每个 source 必须 `(scope, group_id)` 完全一致（global ↔ global，或同 group_id）。否则 raise `ValueError("merge_terms: target and source must share scope+group_id")`。这阻止 admin API 误把 A 群词条合到 B 群。
   - 4b. **写 aliases_json 前调 `find_existing(...exclude_term_ids={target_id, *source_ids})`**：若命中第三方碰撞，raise；merge 必须先解决这条隔板。

**API 设计取舍**：

- 校验失败 raise ValueError 而不是 silent drop → 让 admin UI 能显示明确报错，用户知道是数据冲突而不是奇怪的 5xx
- exclude 用 set → merge 可以"忽略所有 source ids"
- drift 自动合并降级到 drift review → 不阻塞自动管线，但确保人工有最终决定权
- 取消 muted/expired 复用豁免 → 阶段 B 验收能真正达到 pairs=0；如果业务上确有此需求，未来可加 admin 显式 force flag

**验收**：

- 新增 4 个测试：
  1. `test_update_term_blocks_alias_collision`：先建甲、乙，`update_term(乙, aliases=['甲'])` 必须 raise，且数据库中乙的 aliases 仍为空。
  2. `test_update_term_blocks_collision_against_muted`：muted 词的 alias 不允许被新词复用（与首版相反）。
  3. `test_drift_alias_candidate_falls_back_to_review_on_collision`：drift 自动合并撞上 collision 时不抛异常，而是创建 drift_review 记录。
  4. `test_merge_terms_rejects_cross_group`：target=群 A 词、source=群 B 词时 raise ValueError，DB 不变。
- `tests/test_slang_store.py` + `test_slang_plugin.py` + `test_slang_backlog_reviewer.py` 全绿。
- `scripts/dev/slang_alias_collision_report.py` 跑完后 **`new_pairs - existing_pairs = 0`**（运行 1 天对比前后；存量靠阶段 B 清）。

**回滚**：单 commit；`git revert` 即可恢复。

### 阶段 B — 历史碰撞清理（P1，离线人工 + 修脚本，~1 小时）

**改动文件**：`scripts/dev/slang_alias_collision_report.py`（修脚本）+ admin 操作（人工）

**做什么**：

1. **修脚本口径**（首版漏点修补）：当前 `--status approved` 是先 SQL 过滤 status 再做 pairwise，导致只能看到 approved↔approved，漏掉 approved↔candidate / approved↔muted。修法二选一：
   - **方案 B-1（推荐）**：删除 `--status` flag。报告默认全量扫描，`involve approved` / `involve candidate` 两个计数已经在头部输出（"72 pairs (24 involve approved)"），足够日常审计。
   - **方案 B-2**：保留 `--status` 但改语义为"只输出**至少有一端**符合 status 的对"，需要在 SQL 加载阶段不过滤，然后在碰撞检测后过滤。同时新增 `--active-only` flag（status ∈ {approved, candidate} 才算）。
2. 跑 `uv run python scripts/dev/slang_alias_collision_report.py --json > /tmp/coll.json` 拿出全量 ~72 对。
3. 在 admin `/admin/slang` 用现有 `merge_terms` API 逐对合并：脚本输出已带 `suggested_target / suggested_source` 决策（approved > candidate > expired > muted，再按 confidence + usage_count）。**注意 merge_terms 阶段 A 之后必须同 scope/group**——若脚本输出建议跨群，应当作误报跳过或拆成两条本群合并。
4. 合并后 muted 的源词条可以保留（防止再次抽取重新候选）。

**优化**（可选，~2 小时）：admin 前端加一个"碰撞审查"页面，把 collision_report 输出渲染成可逐对点击合并的列表。这步可以挪到下一阶段。

**验收**：

- 跑完后 `slang_alias_collision_report.py` 输出 `pairs=0`（或仅剩管理员明确不愿合并的边缘对，需手动登记白名单）
- live DB 中 approved 词条数稳定（合并源词条转 expired）

**回滚**：merge_terms 已记录 revisions，可逐条 revert。

### 阶段 C — Thinker 注入直接命中摘要（P1，~2.5 小时）

**改动文件**：`services/llm/client.py` + `services/llm/thinker.py` + `tests/test_thinker.py`（如不存在则新建）

**做什么**：

1. **拿到 slang store 的入口**（首版漏点修补）：当前 `LLMClient.__init__` 没有 `slang_store` 参数（仅 `bus`），见 [`services/llm/client.py:960-985`](../../services/llm/client.py#L960)。两条路二选一：
   - **方案 C-1（推荐）**：构造时显式注入 `slang_store: SlangStore | None = None`，在 `bot.py` 装配链路里把 `ctx.slang_store` 传进来。这样 LLMClient 不耦合 bus 的插件系统。
   - **方案 C-2**：运行时通过 `self._bus.get_plugin("slang").store` 拿，加 None 防御。这样不改 ctor 签名但跨服务依赖更隐式。
2. **不**改 thinker 的 prompt 模板基本结构，只在 `system_text` 末尾追加可选 `slang_context` 段。
3. 在 `LLMClient` thinker 调用前先做以下守护检查：
   - 若 `slang_store is None` 或群 `slang_enabled=False`（沿用 [`client.py:1277`](../../services/llm/client.py#L1277) 同样的判定），跳过摘要生成。
   - 否则异步算一次 `direct_hits = await slang_store.find_matching_terms(group_id, conversation_text, include_candidates=False)`（已有方法，O(n) scan，群内 approved 通常 < 300 条）。
4. 把 direct_hits 折成最多 3 条 + 总 ≤ 200 字符的轻量摘要：
   ```
   【群黑话直接命中】
   - ptt：节奏游戏中的实力分（同义词：pjsk）
   - 伊冯：群成员代号
   ```
5. 摘要为空时不追加，签名向后兼容：`think(slang_hint: str = "", ...)`。
6. **不传 slang_lookup 工具给 thinker**：thinker 只是决策"是否说话"，工具调用属于主回复模型的职责。

**为什么这个方案**：

- 不动 thinker 上下文窗口大小（增加 ≤200 字 vs 整段黑话 block）
- 不动 PromptBudgetManager（那是 8.3 节的远期工作）
- 不引入跨服务异步竞态（`find_matching_terms` 已经是 store 同步方法）
- 直接命中 ≠ 全部 approved，符合"不污染 prompt"的拟人化原则
- **遵守 group_profile.slang_enabled = False**：避免在用户已经禁用黑话的群里悄悄再注入

**验收**：

- 单元测试 `test_thinker_receives_direct_hit_summary`：mock store + 一段含已批准 term 的对话，断言 thinker system prompt 包含黑话摘要
- 单元测试 `test_thinker_skips_summary_when_slang_disabled`：群 profile `slang_enabled=False` 时跳过摘要
- 集成测试 `test_thinker_skips_summary_when_no_match`：对话没有任何黑话命中时，system prompt 与基线一致
- 本地手测：在群里说一句只有黑话能解释的话（"伊冯今天又被礼了"），看 thinker trace 是否能正确决定回复

**风险**：

- 摘要生成增加 thinker 之前的 ~5-50ms（一次 store query）；可接受。
- 极端场景：群有 300+ approved，每条消息都做 O(n) 扫描；现状已经发生（`SlangPlugin.on_message` 也在做）。如果成为瓶颈，可以加 LRU 缓存 `(group_id, normalized_text) → hits`。

**回滚**：单 commit；如果 thinker 行为变差，`git revert` 立即恢复。

### 阶段 D — 默认 indirect 行为观察（已被阶段 A0 接管）

阶段 A0 拍板"路线 A0-1（默认 0）"或"路线 A0-2（默认 2 + 改 tracker）"后，本阶段不再单独存在。

如果选了 A0-1：等下一轮看 admin trace 决定是否改回 1 或 2 作为可选实验。
如果选了 A0-2：下一轮看 prompt 表现，若 indirect 词条经常让 bot 强行造梗就降为 0。

## 5. 不在本轮范围

- **PromptBudgetManager**：见 `multilayer-memory-learning-report-2026-05-17.md` 第 8.3 节。属于远期架构改动，需要先做 MemoryConsolidator dry-run。
- **图谱化别名 / 跨群黑话联想**：见同上 8.3.4。需要先做隐私边界。
- **Slang lookup 工具开放给 thinker**：弃用。thinker 不应该有工具调用能力，那是回复模型的责任。
- **黑话相似度查询升级到 embedding**：与本审计无关。

## 6. 验证门径速查

每阶段过门径：

```bash
# A0 阶段（决议）
# 在 maintenance-log.md / changelog 写一行决议；不需要跑命令。

# A 阶段
cd /Users/kragcola/OmubotWorkspace/omubot
uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_slang_backlog_reviewer.py -q
# 预期：33 + 4 = 37 passed（含 4 条新增 collision/merge 测试）

uv run python scripts/dev/slang_alias_collision_report.py | head -1
# 预期：总碰撞数稳定（短期内只下降，不上升）
# 注意：不要再用 `--status approved`；脚本已先 SQL 过滤再做 pairwise，
# 看到的"1 pair"只是 approved↔approved，会漏掉 approved↔candidate/muted。
# 头部输出 "72 pairs (24 involve approved)" 已经覆盖这个口径。

# B 阶段（人工）
# 先把 collision report 的 --status flag 修掉（删除或改语义）。
uv run python scripts/dev/slang_alias_collision_report.py --json > /tmp/before.json
# 在 admin 合并 ...
uv run python scripts/dev/slang_alias_collision_report.py --json > /tmp/after.json
# 预期：after.json 中 pairs 应趋近 0；merge 期间不应出现跨群合并。

# C 阶段
uv run pytest tests/test_thinker.py tests/test_client.py -q
# 预期：原通过的测试保持通过 + 新增 thinker 测试（含 slang_enabled=False 守护）通过

# 全量回归
uv run python scripts/dev/slang_acceptance_check.py
# 已存在的验收脚本应全绿
```

## 7. 时间预估

| 阶段 | 工作量 | 类型 |
|---|---|---|
| A0 — indirect 默认路线决议 | ~5 分钟 | 拍板 + 一行 changelog |
| A — update_term + merge_terms 碰撞校验 + 跨群闸 | ~1.5 小时 | 代码 + 4 个测试 |
| B — 修脚本口径 + 历史碰撞清理 | ~1 小时 | 脚本修复 + 人工合并 |
| C — Thinker 黑话摘要（含入口注入 + slang_enabled 守护） | ~2.5 小时 | 代码 + 3 个测试 + 手测 |
| **合计** | **~5 小时（小一天）** | — |

## 8. 与既有路线图的关系

本文档**已正式合并**到 [`multilayer-memory-learning-report-2026-05-17.md`](multilayer-memory-learning-report-2026-05-17.md) 的 P0 修复方案。逐项对应关系：

| 本文条目 | Multilayer 报告坐标 | 状态 |
|---|---|---|
| § 2.5 / 阶段 A0（indirect 默认值与 tracker 承诺） | § 8.1 P0 文档可信度 — 新增条目 | 已落地（2026-05-19 默认改回 0） |
| § 2.1 / 2.2 / 2.6 阶段 A（update_term + merge_terms 碰撞闸） | § 8.2.4b lexical 写入碰撞防御 | 待落地 |
| § 2.3 / 2.7 阶段 B（历史碰撞清理 + 工具口径） | § 8.2.4c 历史数据治理 | 待落地（前置依赖 A） |
| § 2.4 阶段 C（thinker 直接命中摘要） | § 8.2.4d 决策可见性 | 待落地（独立可启动） |

multilayer 报告原 § 8.2 第 4 项"复核 learning normalizer 的 slang 写入路径"现已扩为 4a/4b/4c/4d 四个子项，4a 仍是原条款（normalizer attach 路径），4b–4d 由本文承重。

**与远期路线的关系**：

- multilayer § 4.3 "回复前检索流程"中 `QueryAnalyzer → UnifiedRetrievalPlanner` 链路是阶段 C 的远期形态。本文阶段 C 选择最小侵入方案（thinker system_text 末尾追加 ≤200 字摘要），不抢先实现 PromptBudgetManager。
- multilayer § 5 Phase B (`PromptBudgetManager`) → Phase C (`MemoryConsolidator dry-run`) → Phase D (`Episodic Reflection`) 的大方向不变，本轮 4 阶段是 Phase A 的承重支柱。

## 9. 参考

- 首轮审计：[`docs/audits/slang-module-audit-2026-05-10.md`](slang-module-audit-2026-05-10.md)
- 首轮修复方案：[`docs/audits/slang-module-remediation-plan-2026-05-10.md`](slang-module-remediation-plan-2026-05-10.md)
- 多层记忆研讨（顶层路线图）：[`docs/audits/multilayer-memory-learning-report-2026-05-17.md`](multilayer-memory-learning-report-2026-05-17.md) — 见 § 8.1 / § 8.2.4
- 2026-05-18 修复 commit：`165a53f` — fix(slang): alias collision detection + indirect-inject cap + multi-time daily review
