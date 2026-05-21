# TASK-20260521-03 Phase C — MemoryConsolidator dry-run（typed candidates 不入生产库）

## 状态

- [x] 起草（2026-05-21）
- [ ] 执行中 @ 分支 task-20260521-03
- [ ] 已合并 @ commit

## 背景

`docs/audits/multilayer-memory-learning-report-2026-05-17.md` § 5 Phase C 与 § 8.3 第 2 项要求：以
`ConversationArchive` 为唯一扫描源，一次扫描派生 typed candidates `{fact, slang, style,
episode, graph_relation}`；先 **dry-run**，把候选展示在 admin，**不**自动写生产库；每条候选
携带 scope / privacy / source refs / confidence / 回滚提示。

前置依赖盘点（grep + 阅读已确认）：

- ✅ `services/conversation_archive/store.py:222` `ConversationArchive` + `read_scan_batch` /
  `finish_scan_batch`（commit `3477163` 已落地）
- ✅ `services/episodic/store.py:246` `EpisodeStore` 5 态状态机（A3 已 green）
- ✅ `services/learning_normalizer/store.py:248` `attach_candidate(domain="general")`（A1.3 已 green）
- ✅ `services/llm/llm_request.py:56-57,284-285` LLMTask `reflection_consolidator` /
  `episode_summarizer` 已注册 + cache profile 已配（**目前 0 caller**——本 spec 是首位 caller）
- ✅ `kernel/config.py:182-183` 同上注册
- ✅ Phase B BlockTraceBus + PromptBudgetManager + provider_bus active 已落地
  ([plugins/chat/plugin.py:802-890](plugins/chat/plugin.py#L802-L890))

剩下的就是组装：扫描 archive → 喂 LLM → 解析 typed candidates → 写到独立 dry-run db → 暴露 admin API。

## 目标

1. 新建 `services/memory_consolidator/` 模块：独立 dry-run candidates store + consolidator
   orchestrator + scanner runner，扫描 `ConversationArchive` 输出 5 类 typed candidates，**只**写
   `storage/consolidator_candidates.db`，**完全不动**生产 slang/style/episode/graph 表
2. 新建 `admin/routes/api/memory_consolidator.py`：`GET runs` / `GET candidates` / `POST runs`
   触发一次 dry-run / `POST candidates/<id>/decide` 记录 admin 决定（**不**走 promotion 到
   生产库——promotion 是下一个 spec 的事）
3. 把 `MemoryConsolidator` 与候选 store 接入 `BotContext`（lazy init，仅 admin 触发时才跑），
   注册新 admin router
4. 测试覆盖：candidates store schema/CRUD、Consolidator dry-run 不污染生产库、admin API 走通

## 约束

- **dry-run only**：本 spec 0 行代码动 `services/slang/store.py` / `services/style/store.py` /
  `services/episodic/store.py` / `services/knowledge_graph/store.py` 的写入路径。promotion 到生产
  库**完全不在范围**——admin decide 只更新候选 state 字段
- **复用 LLMTask spine**：调 LLM 必须走 `LLMRequest(task="reflection_consolidator", ...)` /
  `LLMRequest(task="episode_summarizer", ...)`，**不**直接 `httpx.post(...)`，**不**新增 task
  名。fallback 用 stub 模型也通过 `LLMClient.run_request(...)` 路径，spec 不接受绕过 spine
- **scanner 协议**：调 `services.conversation_archive.scanner.read_scan_batch(...,
  scanner_name="memory_consolidator", scanner_version="v1", ...)` + `finish_scan_batch(...)`，
  不旁路 archive 直查 `messages.db`
- **normalizer attach**：每条候选必须 `attach_candidate(domain="general", profile="general",
  source_table="consolidator_candidates", source_id=candidate_id, ...)`——这是 § 7.5 决议 5
  的"normalizer 纯 domain"语义，**不**用 `domain="slang"` / `"style"`，避免与生产 normalizer 数据
  混淆
- **scope / privacy 字段**：候选 schema 必须含 `scope`（`group` / `user` / `global`）、
  `group_id`、`source_message_pks` (JSON list of archive `message_pk`，作为 source refs)、
  `confidence` (0.0-1.0)、`payload_json`（typed fields per domain，参见「实施步骤」第 2 步）
- **不引入新依赖**：复用现有 `aiosqlite` + `loguru` + `pydantic`（如需）；**禁止** `langchain`
  / `llama-index` 之类的 framework
- **frontend 不动**：本 spec 仅产出 backend dry-run + admin JSON API；候选审计 UI 在下一个 spec
  （TASK-2026MMDD-`memory-consolidator-admin-ui`）做
- **journal_mode=DELETE**：候选 db 沿用 Phase 2 的 slang.db 模式（`PRAGMA journal_mode=DELETE` +
  `synchronous=FULL`），避免 macOS Docker bind mount fsync 风险（铁律）

## 动的文件

精确到路径：

**新建**

- `services/memory_consolidator/__init__.py` — 公开 `MemoryConsolidator`、
  `ConsolidatorCandidatesStore`、`CandidateDomain`、`CandidateState`
- `services/memory_consolidator/store.py` — `ConsolidatorCandidatesStore`：schema + CRUD（独立
  db `storage/consolidator_candidates.db`）
- `services/memory_consolidator/consolidator.py` — `MemoryConsolidator`：单次 dry-run pass 的
  orchestrator（archive 扫描 → LLM call → typed candidates 解析 → store 写入 + normalizer
  attach）
- `services/memory_consolidator/types.py` — typed dataclass：`Candidate`、`CandidateDomain` /
  `CandidateState` Literal、`ScanRun`、5 个 domain 的 payload schema（dataclass 或 TypedDict
  二选一，spec 不强制；目标是 pyright 0 errors）
- `admin/routes/api/memory_consolidator.py` — admin JSON API
- `tests/test_memory_consolidator_store.py` — schema / CRUD / scope filter（~120 行）
- `tests/test_memory_consolidator.py` — 用 stub LLM 跑一次 dry-run，断言：
  - 5 类 typed candidates 都能解析
  - 生产 slang/style/episode/graph store 0 写入（注入 mock，断言 `attach_candidate(...,
    domain="general", source_table="consolidator_candidates")` 被调用且 mock 生产 store 全程
    没有 `create_term` / `create_episode` 等写方法被触发）
  - normalizer attach 用 `domain="general"`
  - `read_scan_batch` / `finish_scan_batch` 都被正确调用，advance_cursor 行为符合预期
  - cancel-path（D2）：`asyncio.wait_for(consolidator.run_once(...), timeout=0.05)`，断言外部
    可观察状态干净——未 finish 的 batch 不会留下"已 advanced cursor 但 0 candidate"的污染状态
- `tests/test_admin_memory_consolidator.py` — 4 个端点 happy path + 1 个 4xx case（~120 行）

**修改**

- `admin/routes/api/__init__.py` — 注册 `create_memory_consolidator_router(...)`
- `bot.py`（或 `plugins/chat/plugin.py` 的 ctx init 段，看现有同类 store 接入位置）— lazy
  init `ctx.memory_consolidator_store` 与 `ctx.memory_consolidator`，**不**启动周期任务
- `plugins/chat/plugin.py:on_shutdown` — 如果在 chat plugin 接的 ctx，补
  `await close_with_checkpoint(ctx.memory_consolidator_store._db, name="memory_consolidator")`
  / `await ctx.memory_consolidator_store.close()`（Phase 1 同模式，D1 同模式扫描结果之一）

不需要修改的现有文件（**不准动**列表会再列一次）：`kernel/`、`services/llm/llm_request.py`
（task 已注册）、`services/conversation_archive/`、`services/learning_normalizer/`、
`services/episodic/`、`services/slang/`、`services/style/`、`services/knowledge_graph/`、
`admin/frontend/`。

## 不准动

- `services/slang/**` / `services/style/**` / `services/episodic/**` /
  `services/knowledge_graph/**` / `services/conversation_archive/**` /
  `services/learning_normalizer/**`——本 spec 是 dry-run，所有生产 store 0 diff
- `services/llm/llm_request.py` / `services/llm/llm_pipelines.py`（task 注册已就绪）
- `kernel/config.py`（task 注册已就绪；如确需新 BotConfig 字段，**先停手报告**，不在本 spec
  范围）
- `services/storage/sqlite.py`（Phase 1 已落地的 `connect_sqlite` / `close_with_checkpoint`
  直接复用）
- `services/block_trace/**` / `services/context/**`（Phase B 已落地）
- `admin/frontend/**`——前端 0 diff
- `docker-compose.yml` / `napcat/**` / `scripts/**`
- `docs/**`（除非 spec 在「备注」段显式要求）
- `pyproject.toml` / `uv.lock`（无新依赖）

## 实施步骤

### 1. 候选 store schema（`services/memory_consolidator/store.py`）

参考 `services/episodic/store.py` 的写法（同 dataclass + CRUD + 5 态状态机风格）。

```sql
CREATE TABLE IF NOT EXISTS consolidator_runs (
    run_id TEXT PRIMARY KEY,
    triggered_by TEXT NOT NULL,            -- "admin:user_id" / "scheduled" / "test"
    group_id TEXT NOT NULL DEFAULT '',     -- '' = all groups
    scope TEXT NOT NULL DEFAULT 'group',   -- 'group' | 'user' | 'global'
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running', -- running | done | failed
    scanned_count INTEGER NOT NULL DEFAULT 0,
    candidates_count INTEGER NOT NULL DEFAULT 0,
    error_text TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS consolidator_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    domain TEXT NOT NULL,                  -- fact | slang | style | episode | graph_relation
    scope TEXT NOT NULL DEFAULT 'group',
    group_id TEXT NOT NULL DEFAULT '',
    source_message_pks TEXT NOT NULL DEFAULT '[]',  -- JSON list of archive message_pk
    payload_json TEXT NOT NULL,            -- typed fields per domain
    confidence REAL NOT NULL DEFAULT 0.0,  -- 0.0 - 1.0
    state TEXT NOT NULL DEFAULT 'dry_run', -- dry_run | queued | approved | rejected
    decision_reason TEXT NOT NULL DEFAULT '',
    decided_by TEXT NOT NULL DEFAULT '',
    decided_at REAL NOT NULL DEFAULT 0,
    normalizer_cluster_id TEXT NOT NULL DEFAULT '',  -- attach_candidate 返回的 cluster_id
    created_at REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES consolidator_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_cand_run    ON consolidator_candidates(run_id, state);
CREATE INDEX IF NOT EXISTS idx_cand_domain ON consolidator_candidates(domain, scope, group_id);
CREATE INDEX IF NOT EXISTS idx_cand_state  ON consolidator_candidates(state, created_at);
```

`payload_json` 5 个 domain 的字段（写入 store 前已 schema 校验，建议 dataclass）：

| domain          | payload 字段 |
| --------------- | ------------ |
| `fact`          | `subject`, `predicate`, `object`, `evidence_quotes` (list[str]) |
| `slang`         | `term`, `meaning`, `aliases` (list[str]), `repeat_policy` |
| `style`         | `expression`, `situation`, `outcome_signal` |
| `episode`       | `situation`, `observed_context`, `action_taken`, `outcome_signal`, `reflection`（与 `EpisodeStore.create_episode` 字段对齐，便于将来 promotion 一一映射） |
| `graph_relation`| `subject_node`, `predicate`, `object_node`, `edge_type`（参见 `services/knowledge_graph/store.py` 现有 edge type 名单） |

CRUD 必须含：

- `start_run(triggered_by, group_id, scope, meta) -> run_id`
- `finish_run(run_id, status, scanned_count, candidates_count, error_text="")`
- `record_candidate(run_id, domain, scope, group_id, source_message_pks, payload, confidence,
  normalizer_cluster_id="") -> candidate_id`
- `list_runs(limit, offset)` / `list_candidates(*, run_id=None, domain=None, state=None,
  scope=None, group_id=None, limit, offset)`
- `decide_candidate(candidate_id, *, state, decided_by, reason)` — 仅 `dry_run → queued |
  rejected | approved`，**不**触发任何生产写入

### 2. Consolidator orchestrator（`services/memory_consolidator/consolidator.py`）

```python
class MemoryConsolidator:
    def __init__(
        self,
        *,
        store: ConsolidatorCandidatesStore,
        archive: ConversationArchive,           # ConversationArchive 实例
        normalizer: LearningNormalizerStore,    # 同实例，独立 db 路径
        llm_client: LLMClient,                  # 走 spine
    ) -> None: ...

    async def run_once(
        self,
        *,
        group_id: str,
        triggered_by: str,
        scope: str = "group",
        max_batches: int = 1,
        batch_size: int = 50,
    ) -> RunReport: ...
```

`run_once` 流程（每个 batch 都要 cancel-safe）：

1. `run_id = await store.start_run(triggered_by, group_id, scope, meta={"max_batches":...})`
2. for batch_idx in range(max_batches):
   - `batch = await read_scan_batch(archive, scanner_name="memory_consolidator", group_id,
     limit=batch_size, scanner_version="v1", params_hash="...", required=True)`
   - if `not batch["rows"]`: break
   - 拼 prompt，调 `await llm_client.run_request(LLMRequest(task="reflection_consolidator",
     ..., system=[...], messages=[...]))`，期待返回结构化 JSON（含 5 类候选 list）
   - 解析失败 → log `_L.warning("consolidator parse failed run={} batch={} error={}", ...)`
     + `finish_scan_batch(..., status="failed", advance_cursor=False, error=...)` + 跳到下一
     batch（不增 candidates_count）
   - 解析成功 → 逐条 `record_candidate(...)` + `attach_candidate(domain="general",
     profile="general", source_table="consolidator_candidates", source_id=candidate_id, ...)`
   - `finish_scan_batch(batch, status="done", scanned_count=len(rows),
     extracted_count=len(candidates), saved_count=len(candidates), advance_cursor=True)`
3. `await store.finish_run(run_id, status="done", scanned_count, candidates_count)`
4. 返回 `RunReport(run_id=..., scanned=..., candidates=...)`

**异常 / cancel 路径**（D2 必须覆盖）：

- 任意 `await` 抛 `CancelledError` / `asyncio.TimeoutError` → 在 `try / finally` 里
  `await store.finish_run(run_id, status="failed", error_text=...)`，并对当前 batch 调
  `finish_scan_batch(..., status="cancelled", advance_cursor=False)`
- `BaseException` 子类不被 `except Exception` 吞掉

### 3. admin API（`admin/routes/api/memory_consolidator.py`）

参考 `admin/routes/api/episodes.py` 与 `admin/routes/api/slang.py` 的形态。

| Method | Path | 行为 |
| ------ | ---- | ---- |
| `GET`  | `/api/admin/memory_consolidator/runs?limit=&offset=` | 列最近 run |
| `GET`  | `/api/admin/memory_consolidator/runs/<run_id>/candidates?domain=&state=&limit=&offset=` | 列 run 下候选 |
| `GET`  | `/api/admin/memory_consolidator/candidates?domain=&state=&group_id=&limit=&offset=` | 全局列候选 |
| `POST` | `/api/admin/memory_consolidator/runs` body `{group_id, scope?, max_batches?, batch_size?}` | 触发一次 dry-run；同步等待返回 RunReport |
| `POST` | `/api/admin/memory_consolidator/candidates/<id>/decide` body `{state, reason?}` | 仅更新候选 state，**不**写生产 |

返回 dict 用 `{"ok": bool, "data": ..., "error": "..."}` 包裹（同 admin/routes/api 现有形态）。

### 4. ctx 接入（`bot.py` 或 `plugins/chat/plugin.py`，找到现有同类 store init 的位置）

```python
from services.memory_consolidator import (
    ConsolidatorCandidatesStore, MemoryConsolidator,
)

ctx.memory_consolidator_store = ConsolidatorCandidatesStore(
    "storage/consolidator_candidates.db"
)
await ctx.memory_consolidator_store.init()

ctx.memory_consolidator = MemoryConsolidator(
    store=ctx.memory_consolidator_store,
    archive=message_log,                           # 已是 ConversationArchive 实例
    normalizer=LearningNormalizerStore(
        "storage/consolidator_normalizer.db"       # 独立 db，与 slang/style 各自 normalizer
                                                   # 物理隔离，避免污染生产 cluster
    ),
    llm_client=llm,
)
```

`on_shutdown` 接 `await ctx.memory_consolidator_store.close()`（其内部走
`close_with_checkpoint` — 即便 journal_mode=DELETE 也无害，调用一致即可）。

### 5. 测试

- 用 stub LLM：`class _StubLLM: async def run_request(self, req): return LLMResponse(text="
  {...}", ...)`，把 5 类候选 hard-code 在返回 JSON 里
- 用 in-memory `ConversationArchive`（`tmp_path / "messages.db"`）+ 手工 record 几条
- 断言：
  - `consolidator_candidates.db` 含期望条数 + 5 类 domain 全覆盖
  - 生产 store mock 全程未写（`Mock(spec=SlangStore).create_term.assert_not_called()` 等）
  - normalizer attach 调用了，参数 `domain="general"` / `profile="general"` /
    `source_table="consolidator_candidates"`
  - `archive` 的 cursor 在 `done` 时 advance、`cancelled` / `failed` 时不 advance
  - cancel-path：`asyncio.wait_for(c.run_once(...), 0.05)` 抛 `TimeoutError`，
    `consolidator_runs.status == "failed"`，candidates count 为 0 或部分（取决于 stub LLM
    速度）但**没有**遗孤 normalizer attach（即如果某 candidate 写了 normalizer，对应
    `consolidator_candidates` 行也必须存在）

D5：跑全量 pytest 前 `pkill -9 -f pytest`。

## 验收

每条命令行可跑、能 0/非 0 判断：

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 1. 新建文件存在
test -f services/memory_consolidator/__init__.py && echo "OK-pkg-init"
test -f services/memory_consolidator/store.py && echo "OK-pkg-store"
test -f services/memory_consolidator/consolidator.py && echo "OK-pkg-consolidator"
test -f services/memory_consolidator/types.py && echo "OK-pkg-types"
test -f admin/routes/api/memory_consolidator.py && echo "OK-admin-api"
test -f tests/test_memory_consolidator_store.py && echo "OK-tests-store"
test -f tests/test_memory_consolidator.py && echo "OK-tests-orchestrator"
test -f tests/test_admin_memory_consolidator.py && echo "OK-tests-api"

# 2. 生产 store 路径 0 diff
git diff HEAD -- services/slang services/style services/episodic services/knowledge_graph \
       services/conversation_archive services/learning_normalizer | wc -l \
       | xargs -I{} test {} -eq 0 && echo "OK-prod-stores-untouched"

# 3. LLMTask spine 未动
git diff HEAD -- services/llm/llm_request.py services/llm/llm_pipelines.py kernel/config.py \
       | wc -l | xargs -I{} test {} -eq 0 && echo "OK-llm-spine-untouched"

# 4. frontend / docker-compose / napcat / scripts 0 diff
git diff --name-only HEAD | grep -qE '^(admin/frontend|docker-compose|napcat/|scripts/|pyproject\.toml|uv\.lock)' \
       && echo "FAIL-out-of-scope" || echo "OK-scope-clean"

# 5. admin router 已注册
grep -q 'create_memory_consolidator_router' admin/routes/api/__init__.py && echo "OK-router-registered"

# 6. ctx 接入
grep -q 'MemoryConsolidator\|memory_consolidator_store' bot.py plugins/chat/plugin.py \
       && echo "OK-ctx-wired"

# 7. lint / type / test 不退步
uv run ruff check 2>&1 | tail -1 | grep -qE 'Found 26 errors|All checks passed' && echo "OK-ruff"
uv run pyright 2>&1 | tail -1 | grep -qE '0 errors' && echo "OK-pyright"
pkill -9 -f pytest 2>/dev/null; sleep 1
uv run pytest -q 2>&1 | tail -3 | grep -qE 'passed' && echo "OK-pytest"

# 8. 新增 3 个测试文件全绿
uv run pytest tests/test_memory_consolidator_store.py tests/test_memory_consolidator.py \
              tests/test_admin_memory_consolidator.py -q 2>&1 | tail -3 \
              | grep -qE 'passed' && echo "OK-new-tests"

# 9. 候选 db 不在 git tree 里（应被 .gitignore 物理拦截）
git check-ignore -v storage/consolidator_candidates.db 2>&1 | grep -q 'storage/\*\.db' \
       && echo "OK-db-gitignored" || echo "FAIL-db-tracked"

# 10. 启动容器后端口可达 + admin runs 端点 200
# （部署后人手验证；CI 阶段不必跑此条）
# curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
#   http://localhost:8081/api/admin/memory_consolidator/runs?limit=5
```

至少 9 行 `OK-*` 输出（第 10 条人手）。

## 用户复制命令段

### 1. 建分支（含 dirty-worktree 保护）

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

git stash list                    # D7：先看 stash，避免重蹈 5-day-loss
git status -uno
git stash push -u -m "pre-task-20260521-03" 2>&1
git checkout -b task-20260521-03

echo "branch ready; HEAD=$(git rev-parse --short HEAD)"
```

### 2. 交给 codex 执行

```bash
codex 'cd /Users/kragcola/OmubotWorkspace/omubot && 严格按照 .claude/handoff/TASK-20260521-03-memory-consolidator-dryrun.md 执行。Phase C MemoryConsolidator dry-run：新建 services/memory_consolidator/ 模块（store + consolidator + types）+ admin/routes/api/memory_consolidator.py + 3 个测试文件，注册 admin router、ctx lazy init。绝不动 services/slang/style/episodic/knowledge_graph/conversation_archive/learning_normalizer/llm/llm_request 等生产路径，不动 frontend / docker-compose / scripts。完成后跑 spec 验收 9 条命令，全部 OK-*。'
```

### 3. 本地验证

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
# 验收命令见 ## 验收
```

### 4. 把 diff 给 Claude 审查

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
git diff HEAD | pbcopy
# 贴给 Claude 说 "审 TASK-20260521-03"
```

### 5. Claude 通过后提交

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# D7：staged 列表先过一眼，禁止 git add -A
git status -uno
git add services/memory_consolidator/ \
        admin/routes/api/memory_consolidator.py \
        admin/routes/api/__init__.py \
        bot.py plugins/chat/plugin.py \
        tests/test_memory_consolidator_store.py \
        tests/test_memory_consolidator.py \
        tests/test_admin_memory_consolidator.py
git diff --cached --stat

git commit -m "$(cat <<'EOF'
feat(memory): Phase C — MemoryConsolidator dry-run + admin queue

新建 services/memory_consolidator/：扫描 ConversationArchive 单批 LLM 调用
（reflection_consolidator task），输出 5 类 typed candidates（fact/slang/style/
episode/graph_relation）写到独立 storage/consolidator_candidates.db，绝不动生产
slang/style/episodic/graph store。每条候选走 LearningNormalizer
attach_candidate(domain="general") 去重，admin 通过新 API 查看 + decide（仅状态字段，
不入生产库）。

详见 .claude/handoff/TASK-20260521-03-memory-consolidator-dryrun.md
EOF
)"

git stash pop
```

### 6. 失败回滚

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
git checkout -
git branch -D task-20260521-03
git stash pop
```

## 审查要点（给 Claude 看 diff 时过一遍）

- [ ] 「不准动」列表的所有路径 0 diff（特别是生产 store 与 LLM spine）
- [ ] `services/memory_consolidator/store.py` schema 含 `consolidator_runs` +
      `consolidator_candidates` 两表，所有约束字段都在
- [ ] `MemoryConsolidator.run_once` 在 `try / finally` 内 `finish_run`，捕获 `BaseException`
      子类（CancelledError / TimeoutError）—— D2 cancel-path 测试覆盖此分支
- [ ] LLM 调用路径：必走 `LLMRequest(task="reflection_consolidator", ...)` /
      `LLMRequest(task="episode_summarizer", ...)`，**不**直接 `httpx` / `aiohttp`
- [ ] `attach_candidate(domain="general", profile="general",
      source_table="consolidator_candidates", ...)`——不是 `domain="slang"` / `"style"`
- [ ] candidates db 路径 `storage/consolidator_candidates.db`（被 `.gitignore` 现有规则
      `storage/*.db` 物理拦截）
- [ ] admin router 用 `/api/admin/memory_consolidator/...` 前缀，已注册到
      `admin/routes/api/__init__.py`
- [ ] 4 个 admin 端点 happy path 都有 4xx 防护（缺参数 / 不存在的 candidate_id 等）
- [ ] 测试断言"生产 store 0 写入"（mock 生产 store + `assert_not_called` 链）
- [ ] D2 cancel-path 测试用 `pytest.raises(asyncio.TimeoutError)` 包 `wait_for(...,
      timeout=0.05)`，断言外部可观察状态：`consolidator_runs.status == "failed"`、
      candidates 与 normalizer attach 一致（不留遗孤）
- [ ] D1 同模式扫描（在维护日志里写）：grep `await\s+self\.store\.set_meta` 一类，确认
      `start_run` / `finish_run` 没在长跑 await 之前写状态导致 cancel 后留下"已 advanced
      但 0 candidate"的污染
- [ ] `pyproject.toml` / `uv.lock` 0 diff
- [ ] frontend 0 diff
- [ ] 没残留 `TODO` / `FIXME` / `print(` / `console.log`

## 备注

### 为什么 Phase C 范围只到 dry-run，不做 promotion

报告 § 8.3 第 2 项明确：「先 dry-run，把写入建议展示在 Admin，不自动落库」。promotion 到生产
slang / style / episodic / graph 涉及：

- 跨 store 一致性事务（candidate → 多个生产 store 的写入要么全成要么全失败）
- admin 操作日志 / 撤销链（每个 promotion 必须可回滚）
- 与现有 slang/style 抽取流水线的优先级仲裁（同一条候选 slang 与抽取器结果撞了怎么办）

这些都不该塞进首版 dry-run。本 spec 落地后跑 1-2 周观察候选质量，再单独开
`promotion` spec。

### 为什么用独立 normalizer db `consolidator_normalizer.db`

`services/slang/store.py` 与 `services/style/store.py` 各自把 normalizer 写在
`learning_normalizer.db`（同一个文件，靠 `domain` 字段区分）。dry-run 候选**不应**进入这个生产
cluster——否则 admin 看 cluster 时会被未审核候选污染统计。独立 db 是物理隔离，promotion 时再决
定是否合入主 normalizer。

### Phase C 与已注册 LLMTask 的关系

`reflection_consolidator` / `episode_summarizer` 在 [services/llm/llm_request.py:56-57](services/llm/llm_request.py#L56-L57)
+ [services/llm/llm_pipelines.py:77](services/llm/llm_pipelines.py#L77)
+ [services/llm/llm_request.py:284-285](services/llm/llm_request.py#L284-L285)
+ [kernel/config.py:182-183](kernel/config.py#L182-L183) 已注册但全无 caller。本 spec 是首位 caller。

将来若 `episode_summarizer` 与 `reflection_consolidator` 各自承担不同 prompt（前者总结对话段
为单条 episode 草稿、后者生成跨条反思），由 consolidator 内部分流；spec 不强制顺序，但**两
个 task 至少都要有一处调用**——否则保留它们就是死代码。

### 与 D1 / D2 / D7 的关联

- **D1**（同模式扫描）：扫 `await self._db.execute("UPDATE ... SET ... WHERE run_id = ?")` 是否
  写在长跑 LLM `await` 之前——本 spec 的 `start_run` 只设 `running` + `started_at`，不预占
  `candidates_count`，不会复刻 slang `last_daily_ai_review_date` 的"提前标完跑过"事故
- **D2**（cancel-path 测试）：`run_once` 是被 admin POST 端点的 `await` 包裹的协程，端点超时
  会触发 cancel——必须有专门的 cancel 测试断言「runs 表标 failed + 当前 batch 不 advance
  cursor」
- **D7**（git hygiene）：spec 命令段已在建分支前要求 `git stash list && git status -uno`，
  在提交前要求 `git diff --cached --stat`，避免 `git add -A` 把候选 db 误进 commit
  （即便 .gitignore 已拦也按程序走）
