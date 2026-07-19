# Memory Episode v2 — Decay Eligibility + Query Rerank (2026-07-16)

> 状态：implemented in code, **not deployed**.
> 范围：`EpisodeStore` 严格 `decay_at` 合同 + 默认 recall 读时过期过滤；审计 `set_decay_at`；Admin `POST .../decay`；`EpisodeProvider` 有界候选池 + 确定性 ngram 重排。
> 非目标：schema migration、sweeper 重设计、LLM 重排、PPR/QZone、部署/canary、`BUILTIN_WIRE_PROFILE.validated`。

## Contract

### Decay eligibility (`EpisodeStore.list_for_recall`)

| 模式 | 状态集合 | `decay_at` 过滤 |
|------|----------|-----------------|
| **Default** (`include_decayed=False`) | 仅 `enabled_for_prompt` | `decay_at = ''` **或** `julianday(decay_at) > julianday(now)`（绝对时间、offset-safe）；过期 enabled 行在 sweeper 未跑时也**不可**进 prompt |
| **Wide reader** (`include_decayed=True`) | `enabled_for_prompt` + `disabled` | **无**默认过期排除（审计/历史检索，非「只看已过期」） |
| **Sweeper** (`expire_decayed`) | `enabled_for_prompt` | `decay_at != ''` **且** `julianday(decay_at) <= julianday(now)` → transition `disabled` |

排序不变：`confidence DESC, updated_at DESC`。`last_used_at` 仅为 stamp，不参与 eligibility。

**Offset-safe comparison (review remediation 2026-07-16)**：默认 recall 与 sweeper **不得**对 ISO 字符串做字典序比较。本地探针：`'2026-07-16T12:32:15+00:00' > '2026-07-16T19:32:15+08:00'` 为 0（假），但前者绝对时间晚一小时；`julianday` 比较正确。非法非空 legacy 值 → `julianday` 为 NULL → 比较失败 → **fail-closed** 排除默认 recall（不 backfill、无 schema migration）。

### Strict `decay_at` normalize (`_normalize_decay_at` / `create_episode` / `set_decay_at`)

- 空串 / 仅空白 → `""`（永不过期）。
- 非空必须是 **timezone-aware** ISO-8601；规范化为 Asia/Shanghai、秒精度。
- naive / 畸形 / bool / 数值 / 容器 → `TypeError` 或 `ValueError`（从不静默 coerce）。
- `set_decay_at`：缺失 episode → `False`；成功写库 + `EpisodeRevision(action=set_decay_at)` before/after。

### Admin route

- `POST /api/admin/episodes/{episode_id}/decay`
- Body 必须含 `decay_at`（可用 `""` 清除）；可选 `reason`。
- 缺字段 / 非法 → **400**；未知 id → **404**；成功返回规范化 `decay_at`。

### Provider recall (`EpisodeProvider`)

1. `fetch_limit = min(_CANDIDATE_POOL_CAP, max(top_k, top_k * 3))`，`_CANDIDATE_POOL_CAP = 24`
   - `top_k=1` → 3（不为 24）
   - 大 `top_k` 仍硬封顶 24
2. 候选池上 **register filter**（先于 final `top_k`）
3. 确定性 ngram **relevance DESC only**；tie 保 store 序；空 query / normalize-empty → 不重排；**无** relevance 阈值
4. 渲染 composite 评分文档：`_RERANK_FIELDS` 固定顺序，`\\n` 拼接，总长硬截断 **`_COMPOSITE_CHAR_CAP = 1200`**
5. 仅 **selected** episode：`evidence_refs` / typed linked evidence / `last_used_at` stamp

## Old → New Mapping

| 面 | 旧 | 新 |
|----|----|----|
| Default recall | 仅 `enabled_for_prompt`；过期行依赖 sweeper 变 `disabled` 后才消失 | 读时 `(decay_at='' OR julianday(decay_at) > julianday(now))`，不依赖 sweeper；offset-safe |
| Sweeper | 字符串 `decay_at <= now` | `julianday(decay_at) <= julianday(now)`（与默认 eligibility 同一绝对时间合同） |
| `include_decayed` | 宽读 enabled+disabled | 合同明确为历史 wide reader（仍无默认过期排除） |
| `decay_at` 写入 | 松散/可绕过 | 统一 `_normalize_decay_at`；`set_decay_at` 审计修订 |
| Admin | 无 decay 端点 | `POST .../decay` |
| Provider fetch | `max(top_k, top_k*3)` 无硬顶；实现一度误为恒 CAP | `min(CAP, max(top_k, top_k*3))` |
| Provider rank | store 序 + 边 filter | register → ngram rerank → top_k |
| Composite score text | 无总长 cap | 1200 字符确定性截断 |

## Rollout（offline only）

- 本 packet **仅代码 + focused tests + 文档**；**不部署**、不 recreate bot/NapCat、不写 live SQLite、不触 QZone HTTP。
- 无 DB schema 变更（沿用既有 `decay_at` 列）；无数据 backfill。
- 上线（未来、需另开部署窗）：仅 recreate **bot**；永不 recreate NapCat。

## Observability

- Admin 成功响应含规范化 `decay_at`；revision 可 `GET .../revisions` 审计。
- Provider 失败：`episode recall failed` warning；stamp 失败 debug + `return_exceptions`。
- 默认 recall 行数下降可能因读时过期过滤（预期）；wide reader 仍可对照。

## Rollback

1. 回退 `services/episodic/store.py` 中 normalize / list_for_recall / set_decay_at。
2. 回退 `services/block_trace/episode_provider.py` 候选池/重排/composite cap。
3. 回退 `admin/routes/api/episodes.py` 的 `/decay` 路由。
4. 回退对应 tests + 本文档。
5. 无 schema 反向迁移；已写入的规范化 `decay_at` 字符串仍合法 ISO。

## D1 Same-pattern scan

| 位点 | 结果 |
|------|------|
| `EpisodeStore.list_for_recall` / `expire_decayed` | 本 slice 合同；二者均用 `julianday` 绝对时间；sweeper 仍可 disable 过期 enabled |
| `EpisodeProvider.provide` 唯一 prompt 召回路径 | 已接 fetch_limit + rerank |
| Admin episodes enable/disable/reopen | 并列 state 机；decay 不改 state |
| 其他 ContextProvider（slang/style/climate） | **未改**；无共享 decay 合同 |
| QZone / graph / RRF / RetrievalGate | **未触** |

## D2 Cancellation preservation

- `list_for_recall` / `set_decay_at`：单次 SQL + commit；无 `wait_for` 包装的 in-flight 旗标。
- Provider `asyncio.gather(..., return_exceptions=True)` 仅 stamp `last_used_at`；取消不污染下一轮 eligibility（stamp 失败 best-effort）。
- 既有 cancel-path 合同未改写；本 slice 不新增跨请求可变全局。

## Deployment

**None.** 未 commit 要求以调用方为准；本 worker **不** commit / push / deploy。

## Key files

| 路径 | 变更 |
|------|------|
| `services/episodic/store.py` | `_normalize_decay_at`, create decay, `list_for_recall`, `set_decay_at` |
| `services/block_trace/episode_provider.py` | fetch_limit, composite cap, rerank, selected evidence/stamps |
| `admin/routes/api/episodes.py` | `POST /{id}/decay`（及既有 enable 并列） |
| `tests/test_episode.py` | decay 合同 |
| `tests/test_episode_context_provider.py` | rerank / bound / composite |
| `tests/test_admin_episodes.py` | admin decay HTTP |

## Verification (focused)

```bash
source ./scripts/dev/env.sh
export PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-}
uv run pytest -q tests/test_episode.py tests/test_episode_context_provider.py tests/test_admin_episodes.py
uv run ruff check services/episodic/store.py services/block_trace/episode_provider.py admin/routes/api/episodes.py \
  tests/test_episode.py tests/test_episode_context_provider.py tests/test_admin_episodes.py
uv run pyright services/episodic/store.py services/block_trace/episode_provider.py admin/routes/api/episodes.py
git diff --check
```
