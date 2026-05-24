# Persona Runtime Cutover B2 — Shadow Compare 双算 + diff 日志

> 状态：2026-05-24 立项；本文是 dry-run → runtime 切流序列的第二步（B2），仅落地**v1 vs v2 双算 + 本地 diff 日志**，不真正切流。
>
> 上游：[persona-runtime-cutover-B1-execution.md](./persona-runtime-cutover-B1-execution.md)、[persona-source-importer-go-live-readiness.md](./persona-source-importer-go-live-readiness.md)。
>
> 旁支证据：
> - [persona-s12-parity-audit-execution.md](./persona-s12-parity-audit-execution.md)（v1 vs v2 比对算法基础）
> - [persona-v2-importer.md §12](../migrations/persona-v2-importer.md#12-runtime-cutover-b1-——-协议层--配置层--runtime-入口骨架)（B1 协议层）

---

## 0. 范围与护栏

**In scope（B2 本期落地）**

1. `services/persona/shadow.py` 新增 `ShadowCompareEngine` + `ShadowDiffReport` dataclass
2. `_on_connect` hook（`kernel/router.py`）增加 `cfg.persona_v2.shadow_compare` flag-gated 调用，仅当 flag=on 时双算
3. v1 static block（`PromptBuilder._static_block`）与 v2 `compile_persona_runtime()` 输出双算 + 计算 diff
4. diff JSONL 日志落 `storage/persona_shadow_diff.log`（append-only），rotate 由现有 loguru 配置接管
5. counter 埋点：`persona_v2_shadow_total{result=ok|error|divergent}`（in-memory dict + admin API readonly 暴露）
6. 5 条单测覆盖 happy / divergent / flag off / bundle missing / cancel-path（D2）
7. 文档收口：本文 §7 状态表 + maintenance-log + migration doc §12 旧→新行追加

**Out of scope（B2 不动）**

- `PromptBuilder.build_static()` / `_build_group_profile_block()` 任何拼接逻辑（B3 起）
- 群级 / 用户级 prompt block 双算（B2 仅 static block 一块；group_profile 双算挪到 B3）
- LLM 真实调用：shadow compare 不发任何 LLM 请求，仅本地拼字符串计算 diff
- 单群 / 多群灰度切流：`runtime_consume` flag 仍默认 off（B3 才启用）
- Admin SPA Shadow 视图：仅 readonly counter API；前端可视化交给 B6
- v2 bundle 在 runtime 路径任何下游消费：除 shadow.py 自身，runtime 路径继续零命中

**护栏**

- D1：所有新增 site 必须 grep 同模式确认无第二处实现；`grep -rn 'ShadowCompareEngine\|shadow_compare' --include='*.py'` 仅命中 `services/persona/`、`kernel/router.py`（一处 hook 调用）、`tests/`
- D2：`ShadowCompareEngine.run_once()` 必须有 `pytest.raises(asyncio.CancelledError)` / `wait_for(..., timeout=0)` 回归——确保 cancel 不污染下次运行（diff log 不写半行、counter 不增、bundle 不重载）
- D3：本文是 B2 的 D3 迁移清单
- D5：targeted 跑 `tests/test_persona_shadow.py` / `tests/test_persona_runtime_*.py` / `tests/test_persona_compiler.py`；全量 pytest 前 `pkill -9 -f pytest`
- D6：B2 不触前端；不需要 `npm run build`，不需要 docker rebuild
- D7：commit 前 `git stash list && git status -uno`；只 stage `services/persona/shadow.py` / `kernel/router.py` / `tests/test_persona_shadow.py` / 本文 / `maintenance-log.md` / `docs/migrations/persona-v2-importer.md`

---

## 1. 子任务编号与依赖

| 编号 | 任务 | 依赖 | 关键产物 |
|---|---|---|---|
| B2.1 | `ShadowCompareEngine` + `ShadowDiffReport` dataclass | B1.4 (`load_pending_freeze`) | `services/persona/shadow.py`；`tests/test_persona_shadow.py` 5 条 |
| B2.2 | diff JSONL 日志格式 + counter 容器 | B2.1 | `storage/persona_shadow_diff.log` 写入路径；`shadow.py::ShadowCounter` |
| B2.3 | `_on_connect` flag-gated hook | B2.1 + B2.2 | `kernel/router.py` 仅新增 ≤ 8 行；保持 `shadow_compare=false` 时零代码路径变化 |
| B2.4 | counter readonly API（可选） | B2.2 | `admin/routes/api/persona_runtime.py` 单 GET endpoint；本期可以不暴露前端 |
| B2.5 | 文档收口（本文 §7 + maintenance-log + migration） | B2.1 ~ B2.4 | 三处文档同步 |

B2.1 / B2.2 同 commit；B2.3 单独 commit（router.py 改动需独立 git diff 易回滚）；B2.4 可选，先落 §7 标 ⏳；B2.5 收尾 commit。

---

## 2. B2.1 — `ShadowCompareEngine` + `ShadowDiffReport`

### 2.1 设计

**dataclass（services/persona/shadow.py）**

```python
@dataclass(frozen=True)
class ShadowDiffReport:
    timestamp: str            # ISO-8601 UTC
    persona_id: str
    source_sha256: str
    compile_signature: str    # v2 prompt blocks 拼接 sha256
    v1_signature: str         # v1 static_block.text sha256
    has_divergence: bool
    divergent_axes: tuple[str, ...]   # 与 parity_audit 6 axes 对齐
    v1_text_len: int
    v2_text_len: int
    notes: tuple[str, ...]    # 关键差异摘要（每条 < 200 char），仅给运维看
```

**counter（in-memory）**

```python
@dataclass
class ShadowCounter:
    ok: int = 0
    divergent: int = 0
    error: int = 0
    last_error: str = ""
    last_run_at: str = ""
```

**engine 接口**

```python
class ShadowCompareEngine:
    def __init__(
        self,
        *,
        cfg: PersonaV2Config,
        prompt_builder: PromptBuilder,
        log_path: Path = Path("storage/persona_shadow_diff.log"),
        persona_root: Path = Path("config/persona"),
    ) -> None: ...

    async def run_once(self) -> ShadowDiffReport | None:
        """Compute v1 vs v2 diff and append to JSONL log.

        Returns None if shadow_compare is off, bundle missing, or compile error
        (counter still increments for error). Never raises.
        """

    @property
    def counter(self) -> ShadowCounter: ...
```

**diff 计算策略**

1. v1 文本来源：`prompt_builder._static_block["text"]`（`build_static` 完成后非空）
2. v2 文本来源：`compile_persona_runtime(persona_id)` 返回的 `prompt_blocks`，按固定顺序（`core.identity` / `runtime.adapter` / `core.guard` / `core.voice` / `core.knowledge` / `core.examples` / `runtime.group_profile`）取 `position == "static"` 的拼接
3. 计算两侧 sha256（不直接比文本——文本可能差异巨大，diff 是上层信号）
4. 复用 `services/persona/parity_audit.compare_v1_vs_v2_dry_run` 的 `divergent_axes` 列表（B2 不重写比对逻辑，仅复用）
5. 把 `ParityReport.findings` 中 status 为 `divergent` / `v1_only` 的 axis name 收集成 `divergent_axes`
6. `notes` 字段写每条 divergent finding 的 `axis: notes`（截断到 200 字符）

### 2.2 风险

- **副作用控制**：shadow compare 必须只读，不能修改 `_static_block` / `_pending_freeze/` / 任何 BotConfig 字段。`run_once` 内部不能持锁、不能 await 长时间任务（compile 是同步 dataclass 操作，不应阻塞 event loop > 100ms）
- **日志膨胀**：每次 connect 写一行；正常每天 1-3 行（启动 + reconnect）。loguru 接管 rotate，文件大小不会失控
- **bundle 重复加载**：每次 `run_once` 都重新调 `load_pending_freeze`——这是预期行为（让运维改 `_pending_freeze/` 后重启 bot 立即生效），不做 LRU
- **v2 bundle ok=False**：counter `error += 1`，写一行带 `errors` 字段的日志，**不**自动 fallback（fallback 是 B3 runtime_consume 的语义，B2 仅记录）

### 2.3 验收

- 新增 `tests/test_persona_shadow.py` 5 条：
  - `test_run_once_flag_off_returns_none_no_log`：`shadow_compare=False` 直接返回 None，不写日志、counter 全 0
  - `test_run_once_happy_path_writes_jsonl`：fengxiaomeng-v2 fixture，写一行合法 JSON，`has_divergence=False` 或 True 取决于真实 source（断言 JSONL 可解析 + 字段齐）
  - `test_run_once_bundle_missing_increments_error`：persona_id 不存在 → `counter.error=1`，写一行带 `error="pending_freeze_dir_missing"` 的日志
  - `test_run_once_divergent_lists_axes`：用 mock prompt_builder._static_block.text=""，断言 `divergent_axes` 至少包含 `identity_personality`
  - `test_run_once_cancel_does_not_corrupt`（D2）：`asyncio.wait_for(engine.run_once(), timeout=0)` 抛 TimeoutError 后，counter 与 log 文件状态不变（外部可观察证据：日志文件 size 不变、counter dict 字段不变）
- `uv run pytest tests/test_persona_shadow.py -q` 全绿
- `uv run ruff check services/persona/shadow.py tests/test_persona_shadow.py` clean
- `uv run pyright services/persona/shadow.py` 0 errors

### 2.4 回滚

revert B2.1 commit；新文件，无 caller（B2.3 才接），回滚零风险。

---

## 3. B2.2 — JSONL 日志格式 + counter 容器

### 3.1 schema

`storage/persona_shadow_diff.log` 每行一条 JSON：

```json
{
  "timestamp": "2026-05-24T13:00:00Z",
  "persona_id": "fengxiaomeng-v2",
  "source_sha256": "c0d3d4c6...",
  "compile_signature": "abc123...",
  "v1_signature": "def456...",
  "has_divergence": true,
  "divergent_axes": ["identity_personality", "behavior_instruction"],
  "v1_text_len": 5234,
  "v2_text_len": 5198,
  "notes": [
    "identity_personality: v2 core.identity 没有出现 v1 personality 首行锚点",
    "behavior_instruction: v2 core.guard 行为指令未覆盖 v1 首行锚点"
  ],
  "errors": []
}
```

错误条目（compile 失败或 bundle 缺失）：

```json
{
  "timestamp": "...",
  "persona_id": "fengxiaomeng-v2",
  "ok": false,
  "errors": ["pending_freeze_dir_missing"],
  "v1_signature": "def456..."
}
```

### 3.2 写入策略

- `with open(log_path, 'a', encoding='utf-8') as f: f.write(json.dumps(...) + '\n')`
- 父目录不存在时 `mkdir(parents=True, exist_ok=True)`
- 写失败（disk full / 权限）：`logger.bind(channel='persona_shadow').warning(...)` + counter.error += 1，**不**抛
- log_path 默认 `storage/persona_shadow_diff.log`，与现有 `storage/logs/` 区分（前者由 shadow 持有，后者由 loguru bot.log 持有，互不干扰）

### 3.3 counter 持久化

B2 不做持久化（in-memory）。bot 重启后 counter 清零，是预期行为——shadow log 文件本身就是事实索引。

### 3.4 风险

- **JSONL parse 友好**：每行必须独立可解析，禁止跨行 JSON
- **timestamp 时区**：固定 UTC `Z` 后缀，与 `_persona_runtime.json.frozen_at` 对齐
- **v1_signature 可能为空**：`build_static` 在 connect 后才填 `_static_block`；shadow 在 connect 后调用，所以 `_static_block.text` 必非空。若意外空，写 `v1_signature=""` + 入 errors

---

## 4. B2.3 — `_on_connect` flag-gated hook

### 4.1 设计

`kernel/router.py::_on_connect` 在 `ctx.prompt_builder.build_static(...)` 之后加：

```python
ctx.prompt_builder.build_static(ctx.identity_mgr.resolve(), bot_self_id=bot.self_id)

# B2 shadow compare (flag-gated; defaults off)
if ctx.config.persona_v2.shadow_compare:
    from services.persona.shadow import ShadowCompareEngine
    engine = ShadowCompareEngine(
        cfg=ctx.config.persona_v2,
        prompt_builder=ctx.prompt_builder,
    )
    try:
        await engine.run_once()
    except Exception as exc:
        logger.bind(channel="persona_shadow").warning(
            "shadow compare unexpected error: {}", exc
        )
```

**为什么不做成 ctx 字段**：B2 不需要跨 turn 复用 engine 实例；每次 connect 创建新 engine 是为了让运维改 `_pending_freeze/` + 重启 bot 立即生效。B3 runtime_consume 启用时会改成 ctx 字段（per-turn 调用）。

### 4.2 风险

- **import 时机**：`from services.persona.shadow import ShadowCompareEngine` 放在函数体内（lazy import），避免 flag=off 时引入额外 startup 开销
- **except Exception**：双层兜底——`run_once` 自身不 raise（per §2.1），外层再兜一次防御性。外层 except 只 log warn，不 raise
- **router.py 是首个 v2 hook 落点**：B2 后 `kernel/` 不再算 forbidden zone（仅 router.py 一处 hook）；B3 才会改 PromptBuilder/LLMClient

### 4.3 验收

- D1 同模式扫描：`grep -rn 'shadow_compare\|ShadowCompareEngine\|run_once' --include='*.py'`：
  - `services/persona/shadow.py` 主体
  - `kernel/router.py` 单处调用（≤ 8 行）
  - `tests/test_persona_shadow.py` fixture 与断言
  - 其他路径（`services/llm/`、`bot.py`、`admin/routes/`）零命中
- 跑 `tests/test_persona_runtime_loader.py` / `tests/test_persona_compiler.py` 确认 B1 无回归
- 手动验证：在 fengxiaomeng-v2 已 freeze 的状态下，把 `config.toml` 加 `[persona_v2] shadow_compare = true`，重启 bot，看 `storage/persona_shadow_diff.log` 出现一行；再把 flag 改回 false，重启，确认无新行写入

### 4.4 回滚

revert B2.3 commit；router.py 仅删 8 行 if 块即可还原；shadow.py 模块保留（无 caller，等价于死代码，不影响 runtime）。

---

## 5. B2.4 — counter readonly API（可选）

### 5.1 设计

`admin/routes/api/persona_runtime.py` 新增 `GET /api/admin/persona-runtime/shadow-stats`：

```python
@router.get("/shadow-stats")
async def shadow_stats(ctx: BotContext = Depends(get_ctx)) -> dict:
    engine = getattr(ctx, "shadow_engine", None)
    if engine is None:
        return {"enabled": False, "counter": None}
    c = engine.counter
    return {
        "enabled": True,
        "counter": {"ok": c.ok, "divergent": c.divergent, "error": c.error,
                    "last_error": c.last_error, "last_run_at": c.last_run_at},
    }
```

**为什么标可选**：B2 单 connect 一次的 counter 信息有限（一行 JSONL 就够审计）；admin SPA 真实有用是 B3 per-turn 之后才有持续读数。本期暴露 counter API 是为了让 B6 Admin SPA Runtime 切换面板有现成接入点；不暴露也能跑。

### 5.2 决策

**默认不落 B2.4**——B2 commit 不写 admin API；B6 Admin SPA 上线时再补。本节保留是为了文档完整。

---

## 6. 提交节奏

按 B2.1+B2.2 → B2.3 → B2.5 顺序产出 3 个独立 commit。**每个 commit 落地后等用户显式说"commit"才执行 `git commit`**（CLAUDE.md "Only commit when user explicitly asks"）。

| commit | 目标 | 文件 |
|---|---|---|
| B2.1+B2.2 | `ShadowCompareEngine` + `ShadowDiffReport` + counter + JSONL writer + 5 tests | `services/persona/shadow.py` + `services/persona/__init__.py` + `tests/test_persona_shadow.py` |
| B2.3 | `_on_connect` flag-gated hook | `kernel/router.py`（≤ 8 行新增） |
| B2.5 | 文档收口 | 本文 §7 状态表 + `maintenance-log.md` + `docs/migrations/persona-v2-importer.md §12` 第 5 行从 ⏳ 改 ✅ |

### Commit message 模板

```
feat(persona/runtime): B2.{n} — {short title}

- {bullet 1: behavior}
- {bullet 2: tests}
- {bullet 3: docs / migrations / maintenance-log 链接}

flag-gated; shadow_compare defaults off; PromptBuilder/LLMClient unchanged.
```

---

## 7. 当前状态

| 编号 | 状态 | 落地证据 |
|---|---|---|
| B2.1 | ✅ 已落地 | commit `08761e9` — `services/persona/shadow.py::ShadowCompareEngine` + `ShadowDiffReport` + `ShadowCounter`；`__init__.py` 导出 |
| B2.2 | ✅ 已落地 | 同 B2.1 commit；JSONL append 写入 `storage/persona_shadow_diff.log`；schema 与 §3 对齐 |
| B2.3 | ✅ 已落地 | commit `da52391` — `kernel/router.py::_on_connect` 24 行 hook + `kernel/types.py::PluginContext.shadow_engine`；shadow_compare flag off 时零代码路径变化 |
| B2.4 | 🟡 跳过本期 | B6 Admin SPA 接入点；本期不落 |
| B2.5 | ✅ 已落地 | 当前 commit — 本文 §7 回填 + maintenance-log 当日条目 + migration §12 第 5 行 ⏳→✅ |

---

## 8. 出口标准（B2 完成的判定）

全部满足才能转入 B3：

- [ ] `BotConfig().persona_v2.shadow_compare` 默认 False（test 锁定，复用 B1.1 6 条 fixture）
- [ ] `ShadowCompareEngine.run_once()` 永不 raise（test 锁定）
- [ ] `shadow_compare=false` 时 router.py hook 零开销（lazy import + early return；test 锁定）
- [ ] `grep -rn 'ShadowCompareEngine\|shadow_compare' --include='*.py'` 仅命中 `services/persona/shadow.py` / `kernel/router.py` / `tests/test_persona_shadow.py`，PromptBuilder / LLMClient / bot.py 零命中（D1 同模式扫描）
- [ ] `uv run ruff check services/persona/ kernel/router.py tests/test_persona_*` clean
- [ ] `uv run pytest tests/test_persona_*` 全绿
- [ ] 维护日志当日条目五段齐（变更类型 / 内容 / 影响 / 验证 / 回滚）
- [ ] migration doc §12 第 5 行 "Shadow compare 双算" 从 ⏳ 改为 ✅
