# 黑话别名碰撞与 Thinker 决策上下文 — 第二轮审计

日期：2026-05-18
范围：续 `slang-module-audit-2026-05-10.md` / `multilayer-memory-learning-report-2026-05-17.md`
评审依据：2026-05-18 用户审计要点（"修了 2 条半"）+ 验证复现

## 1. 上下文

2026-05-17 用户提交首轮审计，指出黑话工作流 3 条风险：

1. 别名碰撞（`find_existing` 不反查新候选自带的 aliases）
2. Prompt 注入不是"只注入直接命中"（`get_injectable_terms` 取前 N 排序）
3. 黑话不进 thinker 决策（thinker → on_pre_prompt 顺序）

2026-05-18 已落地修复：
- `find_existing` 改为对称查找（term + aliases 折成统一 key 集，3 步匹配）。8 个调用点中 4 个传 aliases。
- `SlangSettings.max_indirect_inject_terms: int = 2`；`get_injectable_terms` 改为 direct/indirect 分桶。
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

### 2.5 ❌ 不属实：UI tooltip"只注入直命中"与默认行为不一致

**审计原文**：

> 文档里还写"只注入直命中"，和当前默认行为不一致。

**核实**：唯一命中的串在 [`SlangSettingsForm.vue:134`](../../admin/frontend/src/views/slang/components/SlangSettingsForm.vue#L134)：
```html
<span title="未在本轮对话直接命中的高分 approved 黑话，最多带几条作为群背景。0 表示只注入直接命中。">非直接命中上限</span>
```

这是**条件式描述**（"0 表示只注入直接命中"），不是"当前默认就是 0"的断言。无 docs / md 文件做出该承诺。**审计在此处轻微误读**。

但审计的潜台词成立：默认 `max_indirect_inject_terms=2` 是设计选择，不是 bug；如果团队最终目标是更激进的拟人化，**默认值改 0** 是合理调整。本轮先固定为 2，下一轮根据实际 prompt 表现再决定。

## 3. 缺陷分级与决策

| 缺陷 | 严重度 | 决策 |
|---|---|---|
| 2.1 `update_term` 无碰撞校验 | **P0** | 本轮立刻修 |
| 2.2 drift / merge 走 update_term | **P0** | 修 update_term 自动覆盖 |
| 2.3 历史 72 对碰撞未清理 | **P1** | 修完写入路径后一次性人工合并 |
| 2.4 thinker 不感知黑话 | **P1** | 用直接命中摘要 ≤200 字喂给 thinker |
| 2.5 默认 indirect=2 是否要降到 0 | **P3** | 不动；运行 1-2 周后看 prompt trace 决定 |

## 4. 阶段计划

每阶段独立可回滚，按顺序串行落地。

### 阶段 A — `update_term` 碰撞校验（P0，~1 小时）

**改动文件**：`services/slang/store.py` + `tests/test_slang_store.py`

**做什么**：

1. `find_existing` 增加 `exclude_term_id: str | None = None` kwarg，在三步查询里 `AND term_id != ?`（步骤 ③ 也要在 Python 端跳过）。向后兼容：默认 None 时行为不变。
2. `update_term` 在 `aliases` 或 `term` 字段被改写时构造候选 key 集（新 term + 新 aliases），调 `find_existing(scope=term.scope, group_id=term.group_id, exclude_term_id=term.term_id)`：
   - 命中且 status ∈ {approved, candidate}：raise `ValueError("alias collision: would collide with term {existing.term_id} ({existing.term})")`
   - 命中且 status ∈ {muted, expired}：允许（旧词复用）但记一条 `revision_meta={"collision_overridden": existing.term_id}`
3. 把 `_maybe_create_drift_review` 的 `alias_candidate` 自动合并路径包一层 try/except，碰撞时降级为创建 drift review（让人工审）而不是 raise 给上游。
4. `merge_terms` 写 aliases 前同样调用一次（合并目标自身的 source-set 不算碰撞，需要 `exclude_term_id={target_id, *source_ids}`，这里 find_existing 的 exclude 改为 set 兼容）。

**API 设计取舍**：
- 校验失败 raise ValueError 而不是 silent drop → 让 admin UI 能显示明确报错。
- exclude 用 set 而不是 single id → 让 merge 可以"忽略所有 source ids"。
- drift 自动合并降级到 drift review → 不阻塞自动管线，但确保人工有最终决定权。

**验收**：

- 新增 3 个测试：
  1. `test_update_term_blocks_alias_collision`：先建甲、乙，`update_term(乙, aliases=['甲'])` 必须 raise，且数据库中乙的 aliases 仍为空。
  2. `test_update_term_collision_with_muted_allowed`：muted 词的 alias 可以被新词复用，写入成功。
  3. `test_drift_alias_candidate_falls_back_to_review_on_collision`：drift 自动合并撞上 collision 时不抛异常，而是创建 drift_review 记录。
- `tests/test_slang_store.py` + `test_slang_plugin.py` + `test_slang_backlog_reviewer.py` 全绿。
- `scripts/dev/slang_alias_collision_report.py` 跑完后**新增碰撞数 = 0**（运行 1 天对比前后）。

**回滚**：单 commit；`git revert` 即可恢复。

### 阶段 B — 历史碰撞清理（P1，离线人工，~30 分钟）

**改动文件**：纯数据动作，不改代码

**做什么**：

1. 跑 `uv run python scripts/dev/slang_alias_collision_report.py --json > /tmp/coll.json` 拿出当前 72 对（含 24 对 approved-涉及）
2. 在 admin `/admin/slang` 用现有 `merge_terms` API（已实现）逐对合并：脚本输出已带 `suggested_target / suggested_source` 决策（approved > candidate > expired > muted，再按 confidence + usage_count）
3. 合并后 muted 的源词条可以保留（防止再次抽取重新候选）

**优化**（可选，~2 小时）：admin 前端加一个"碰撞审查"页面，把 collision_report 输出渲染成可逐对点击合并的列表，比手动复制 term_id 快得多。这步可以挪到下一阶段。

**验收**：
- 跑完后 `slang_alias_collision_report.py` 输出 `pairs=0`（或仅剩管理员明确不愿合并的边缘对）
- live DB 中 approved 词条数稳定（合并源词条转 expired）

**回滚**：merge_terms 已记录 revisions，可逐条 revert。

### 阶段 C — Thinker 注入直接命中摘要（P1，~2-3 小时）

**改动文件**：`services/llm/client.py` + `services/llm/thinker.py` + `tests/test_thinker.py`（如不存在则新建）

**做什么**：

1. **不**改 thinker 的 prompt 模板基本结构，只在 `system_text` 末尾追加可选 `slang_context` 段
2. 在 `LLMClient` thinker 调用前异步算一次 `direct_hits = await store.find_matching_terms(group_id, conversation_text, include_candidates=False)`（已有方法，O(n) scan，群内 approved 通常 < 300 条）
3. 把 direct_hits 折成最多 3 条 + 总 ≤ 200 字符的轻量摘要：
   ```
   【群黑话直接命中】
   - ptt：节奏游戏中的实力分（同义词：pjsk）
   - 伊冯：群成员代号
   ```
4. 摘要为空时不追加，签名向后兼容：`think(slang_hint: str = "", ...)`
5. **不传 slang_lookup 工具给 thinker**：thinker 只是决策"是否说话"，工具调用属于主回复模型的职责；让 thinker 看一眼上下文足以解决"看不懂的消息错过回复时机"

**为什么这个方案**：
- 不动 thinker 上下文窗口大小（增加 ≤200 字 vs 整段黑话 block）
- 不动 PromptBudgetManager（那是 8.3 节的远期工作）
- 不引入跨服务异步竞态（`find_matching_terms` 已经是 store 同步方法）
- 直接命中 ≠ 全部 approved，符合"不污染 prompt"的拟人化原则

**验收**：
- 单元测试 `test_thinker_receives_direct_hit_summary`：mock store + 一段含已批准 term 的对话，断言 thinker system prompt 包含黑话摘要
- 集成测试 `test_thinker_skips_summary_when_no_match`：对话没有任何黑话命中时，system prompt 与基线一致
- 本地手测：在群里说一句只有黑话能解释的话（"伊冯今天又被礼了"），看 thinker trace 是否能正确决定回复

**风险**：
- 摘要生成增加 thinker 之前的 ~5-50ms（一次 store query）；可接受。
- 极端场景：群有 300+ approved，每条消息都做 O(n) 扫描；现状已经发生（`SlangPlugin.on_message` 也在做）。如果成为瓶颈，可以加 LRU 缓存 `(group_id, normalized_text)→hits`。

**回滚**：单 commit；如果 thinker 行为变差，`git revert` 立即恢复。

### 阶段 D — 默认 indirect=2 是否降到 0（P3，下一轮决定）

**做什么**：什么都不做。

**何时回看**：阶段 A/B/C 落地并跑 1-2 周后，管理员从 admin trace 看 prompt 实际命中情况，决定：
- 如果发现 indirect 词条经常帮助理解 → 保持 2 或调到 3
- 如果发现 indirect 词条经常让 bot 强行造梗 → 降到 0 或 1

不在本轮做无证据的默认值变更。

## 5. 不在本轮范围

- **PromptBudgetManager**：见 `multilayer-memory-learning-report-2026-05-17.md` 第 8.3 节。属于远期架构改动，需要先做 MemoryConsolidator dry-run。
- **图谱化别名 / 跨群黑话联想**：见同上 8.3.4。需要先做隐私边界。
- **Slang lookup 工具开放给 thinker**：弃用。thinker 不应该有工具调用能力，那是回复模型的责任。
- **黑话相似度查询升级到 embedding**：与本审计无关。

## 6. 验证门径速查

每阶段过门径：

```bash
# A 阶段
cd /Users/kragcola/OmubotWorkspace/omubot
uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_slang_backlog_reviewer.py -q
# 预期：33 + 3 = 36 passed

uv run python scripts/dev/slang_alias_collision_report.py --status approved | head -3
# 预期：approved-涉及对数不再增长

# B 阶段（人工）
uv run python scripts/dev/slang_alias_collision_report.py --json > /tmp/before.json
# 在 admin 合并 ...
uv run python scripts/dev/slang_alias_collision_report.py --json > /tmp/after.json
# 预期：after.json 中 pairs 应趋近 0

# C 阶段
uv run pytest tests/test_thinker.py tests/test_client.py -q
# 预期：原通过的测试保持通过 + 新增 thinker 测试通过

# 全量回归
uv run python scripts/dev/slang_acceptance_check.py
# 已存在的验收脚本应全绿
```

## 7. 时间预估

| 阶段 | 工作量 | 类型 |
|---|---|---|
| A — update_term 碰撞校验 | ~1 小时 | 代码 + 测试 |
| B — 历史碰撞清理 | ~30 分钟 | 人工合并 |
| C — Thinker 黑话摘要 | ~2-3 小时 | 代码 + 测试 + 手测 |
| **合计** | **半个工作日** | — |

## 8. 与既有路线图的关系

本审计是 `multilayer-memory-learning-report-2026-05-17.md` § 8.2 "P0 运行时闭环修复" 的具体细化：报告里"slang daily AI review 契约"已在 2026-05-15 SlangView U-1..U-14 完成；"learning normalizer 的 slang 写入路径"已经走 `attach_candidate(domain='slang')`；本文档补足 "lexical layer 的数据完整性 + thinker 决策可见性" 这两块。

后续大方向仍是 `PromptBudgetManager → MemoryConsolidator dry-run → Episodic Reflection`，本轮三阶段是这条路上的承重支柱，而不是平行分叉。

## 9. 参考

- 首轮审计：[`docs/audits/slang-module-audit-2026-05-10.md`](slang-module-audit-2026-05-10.md)
- 首轮修复方案：[`docs/audits/slang-module-remediation-plan-2026-05-10.md`](slang-module-remediation-plan-2026-05-10.md)
- 多层记忆研讨：[`docs/audits/multilayer-memory-learning-report-2026-05-17.md`](multilayer-memory-learning-report-2026-05-17.md)
- 2026-05-18 修复 commit：本轮三个修复完成后追加 commit hash 到此处
